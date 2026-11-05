#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""raw/ 正文摄取器 —— 补上「只有元数据、没有正文」的缺口。

背景：kb.db 的 raw_metadata 存了 1.6 万个原件的路径/大小/时间，但正文没进索引，
导致有道云笔记导出、docx/pdf、本地笔记「搜不到」。本脚本把可提取正文的文件
摄成 docs/chunks（category='raw'），FTS5 与 BGE 向量路立刻覆盖它们。

设计（为什么这样干）：
- 增量：index_state['ingest_raw_state'] 记 {路径:[大小,mtime,hash]}，只处理变化的，
  重复跑零开销；键同时用于清理「已消失文件」的索引
- 断点续传：每 50 篇 commit + 落 state，长跑被kill也不丢进度
- 单文件失败不中断：失败的进 failed 列表，末尾汇总——一个坏 PDF 不该堵死四千篇
- PDF 单文件超时隔离：read_pdf_guarded 起 spawn 子进程跑解析，KB_PDF_TIMEOUT
  （默认 90s）到点 terminate——病态单文件磨 10 分钟也只烧它自己的进程，
  主进程与整体摄取不受影响（Windows 无信号可中断 CPU-bound 解析，详见函数注释）
- PDF 并发 worker 池（T5）：全量重建时上千个 PDF 串行解析要 1.5-2 小时，改为
  _PdfPool 常驻子进程并发（KB_PDF_WORKERS，默认半核/上限 8）+ 每篇独立 deadline。
  池**不牺牲**上面两条：并发只省掉重复 spawn 的开销，超时隔离照样按篇生效
- 隐私目录（raw/隐私/、raw/个人信息/）纳入本地索引：先例是 raw_metadata 已含
  这两个目录的元数据；索引库是本机派生层，严禁外传/上传/入 git（doctor 会提示）
"""
import os
import sys
import json
import time
import hashlib
import datetime
import multiprocessing
import queue
import subprocess
import threading

sys.stdout.reconfigure(encoding="utf-8")

import kb_index  # noqa: E402  （同目录模块：KB_ROOT / connect / upsert_doc / drop_doc）

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_KEY = "ingest_raw_state"
GAP_KEY = "ingest_raw_gaps"
MAX_TEXT = 200_000        # 单文件正文截断：防 PDF 乱码/超长文把库撑爆
MIN_TEXT = 20             # 提取后不足此长度视为无正文，不入库
BATCH = 50                # 每 BATCH 篇 commit + 落 state
# PDF 并发预提取的分段大小：一批里所有 PDF 的正文（每篇最多 MAX_TEXT）会同时
# 待在内存里，1265 篇一次性预提取峰值可达 250MB；分段让缓存随段释放，
# 峰值封顶 PDF_SEG×MAX_TEXT（256×200KB≈50MB）。顺序仍按 todo 原顺序走。
PDF_SEG = 256
# 每 N 篇重启一次 Word 实例（2026-10-28）：Word COM 长时间连着会退化/失效，
# 一次失效会让后面**所有** .doc 都抽不出来。分批重启把损失限制在 N 篇以内，
# 配合 read_doc 的单次自愈，基本不会再出现「几百篇一起失败」。
WORD_RESTART_EVERY = 50
PRIVACY_NOTE = "🔴 索引库含 raw/隐私/ 与 raw/个人信息/ 正文：本地派生层，严禁外传/上传/入 git"

# 扩展名 → 提取器；不在表里的（jpg/mp4/zip…）记 gap 跳过
TEXT_EXTS = {".md", ".txt", ".json", ".py", ".csv", ".log", ".yaml", ".yml", ".ini", ".xml"}
HTML_EXTS = {".html", ".htm"}
OFFICE_EXTS = {".docx", ".doc", ".xlsx", ".pptx", ".xls"}   # .doc 走 Word COM（2026-10-28 加）
PDF_EXTS = {".pdf"}


def sha1_of(text):
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


# ---------- 提取器 ----------
def read_plain(fp):
    """文本类直读：utf-8 优先，gbk 回退（老 Windows 导出的笔记大量 gbk）。"""
    try:
        with open(fp, "rb") as f:
            raw = f.read(MAX_TEXT + 1024)
    except Exception:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc, "ignore")[:MAX_TEXT]
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")[:MAX_TEXT]


def read_html(fp):
    try:
        from bs4 import BeautifulSoup
        with open(fp, "rb") as f:
            raw = f.read(MAX_TEXT + 1024)
        for enc in ("utf-8", "gbk"):
            try:
                soup = BeautifulSoup(raw.decode(enc, "ignore"), "lxml")
                break
            except Exception:
                continue
        else:
            return ""
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n")[:MAX_TEXT]
    except Exception:
        return ""


def read_docx(fp):
    try:
        import docx
        d = docx.Document(fp)
        parts = [p.text for p in d.paragraphs]
        for tb in d.tables:                      # 表格内容也是正文
            for row in tb.rows:
                parts.append("\t".join(c.text for c in row.cells))
        return "\n".join(parts)[:MAX_TEXT]
    except Exception:
        return ""


# 老式 .doc（OLE 二进制）：python-docx 读不了，本机没装 LibreOffice，
# 但装了 Office 2016 → 走 Word COM 只读打开、取正文、不保存不改原件。
# 🔴 Word 实例复用：每篇 Dispatch 一次要多花约 1s，626 篇就是 10 分钟白等。
#    但也不能常驻不退——留着会占一个 Word 进程，脚本结束必须 Quit。
_WORD_APP = {"app": None}
_WORD_PID = {"pid": None}
# 本脚本 Dispatch 出来的所有 Word PID（用于收尸，见 _reap_owned_word）
_WORD_OWNED = set()
# 单篇 .doc 的抽取超时（秒）。Word COM 的 Documents.Open 是**阻塞且无超时**的：
# 遇到带外部链接/域/损坏结构的文档，Word 会弹个看不见的对话框然后一直挂着。
# 2026-10-28 实测：一次 532 篇的批量跑到 20 分钟时一篇没落库，Word 进程
# 挂 134MB 不动——就是卡在某一篇上。没有看门狗就永远走不到下一行。
DOC_TIMEOUT = float(os.environ.get("KB_DOC_TIMEOUT") or 30)


def word_pids():
    """列出当前所有 WINWORD 进程 PID（只读，绝不 kill 任何东西）。

    🔴 为什么必须有这个函数（2026-10-29 实测踩出来的）：
    原先用 `app.Hwnd` 取 PID，但**隐形 Word 实例没有主窗口**，该属性访问直接抛
    AttributeError("Word.Application.Hwnd") → PID 记为 None → `_kill_word_hard()`
    拿到空 PID 直接 return，看门狗**一次都没生效过**。这就是「532 篇跑 20 分钟
    一篇没落库、Word 挂 134MB 不动」的真正根因：不是没有看门狗，是看门狗的
    枪里没有子弹。
    改用「Dispatch 前后取差集」的快照法：不依赖 Word 任何属性，拿到的一定是
    本脚本刚起的那个实例。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV", "/FI", "IMAGENAME eq WINWORD.EXE"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace").stdout
    except Exception:
        return set()
    pids = set()
    for line in (out or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].upper() == "WINWORD.EXE":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def _word_app():
    if _WORD_APP["app"] is None:
        import win32com.client as w
        before = word_pids()
        # 🔴 必须用 DispatchEx（新实例）而不是 Dispatch（会连到 ROT 里已有的实例）：
        #    卡死的旧实例仍能响应 Count 这类简单调用，却永远打不开新文档，
        #    连上去就是「全都失败且查不出原因」。
        app = w.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        # 🔴 防挂起三件套：Word 打开老 .doc 时会弹「文件转换 / 更新链接 / 宏安全」
        #    等看不见的对话框然后永久挂起。逐项都在 try 里——Office 版本不支持就跳过，
        #    不能因为一个属性不存在把整个摄取打死。
        for name, val in (("AutomationSecurity", 3),               # 禁用宏与安全提示
                          ("FeatureInstall", 0),                   # 缺功能不要弹安装
                          ("Options.ConfirmConversions", False),   # 不弹「文件转换」
                          ("Options.UpdateLinksAtOpen", False)):   # 不弹「更新链接」
            try:
                if "." in name:
                    a, b = name.split(".")
                    setattr(getattr(app, a), b, val)
                else:
                    setattr(app, name, val)
            except Exception:
                pass
        # 进程登记到 tasklist 需要一点时间，轮询最多 3s 取差集
        pid = None
        for _ in range(6):
            new = word_pids() - before
            if new:
                pid = sorted(new)[0]
                break
            time.sleep(0.5)
        if pid is None:
            # 兜底：快照法失败时再试 Hwnd 法（有些环境反而只有它能用）
            try:
                import win32process
                pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
            except Exception:
                pid = None
        _WORD_PID["pid"] = pid
        if pid:
            _WORD_OWNED.add(pid)
        _WORD_APP["app"] = app
    return _WORD_APP["app"]


def _kill_word_hard():
    """强杀**我们自己那个** Word 进程（按 PID）。

    🔴 绝不用 `taskkill /IM WINWORD.EXE`：那会把用户正开着的 Word 文档一起杀掉。
    这里只杀 `_WORD_PID` 记下的那一个进程——它是本脚本 Dispatch 出来的。
    """
    pid = _WORD_PID.get("pid")
    _WORD_APP["app"] = None
    _WORD_PID["pid"] = None
    if not pid:
        # 🔴 PID 为空 = 看门狗空枪。宁可把话说出来，也不要静默「保护了个寂寞」
        print("[doc] ⚠ 看门狗无 PID 可杀（本次超时未生效）", file=sys.stderr)
        return
    _WORD_OWNED.discard(pid)
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def _reap_owned_word():
    """收尸：Quit 没退干净的本脚本 Word 实例，按 PID 精确杀掉。

    为什么需要：带对话框/损坏文档的 Word 实例 `Quit()` 会**假成功**——调用返回了，
    进程照样活着（2026-10-29 实测：Quit 打印 ok，PID 仍在，一个 164MB）。
    长跑每 50 篇重启一次，攒下来就是一堆僵尸占内存、还会干扰后续 Dispatch。
    只杀 `_WORD_OWNED` 里记过的 PID，🔴 绝不按 /IM 进程名杀。
    """
    if not _WORD_OWNED:
        return
    alive = word_pids()
    for pid in list(_WORD_OWNED):
        if pid in alive:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=15)
            except Exception:
                pass
        _WORD_OWNED.discard(pid)


def read_doc(fp):
    """只读抽取 .doc 正文。实测单篇 1–3.4 s（Word COM）。

    🔴 返回值必须区分三种情况（2026-10-28 踩出来的）：
       - `str`  → 抽到了正文（可能是空串，说明文件确实没字）
       - `None` → **抽取失败**（COM 断开/文件损坏/被占用），调用方要记 gap 并**留待重试**
    改前失败也 `return ""`，于是「532 篇 COM 失效」在日志里显示成「空 532」，
    差点被当成「这些文件本来就没内容」汇报出去。**失败必须可见。**

    另外自带一次自愈：Word 实例中途会失效（RPC 断开），此时重建实例重试一次，
    仍失败才认输。

    🔴 2026-11-02 新增 **olefile 纯 Python 兜底**：
       实测 `raw/微信文件/其他/2026-05-14-...通知.doc` 让 Word **整个进程崩溃**
       （Documents.Open 上 RPC 断开，连 Quit 都调不到，留僵尸 WINWORD）。
       该文件 OLE 结构完全正常，只是 WPS 生成的 → 是「Word 处理不了」不是「文件坏」。
       所以 Word 两次都失败后，降级到 `doc_ole.py`（零 Office 依赖、毫秒级、绝不崩）。
       兜底成功照样算「抽到正文」，只有两条路都失败才返回 None 记 gap。
    """
    for attempt in (1, 2):
        # 看门狗：超时就把我们自己的 Word 杀掉，让阻塞的 Open 立即抛错
        killer = threading.Timer(DOC_TIMEOUT, _kill_word_hard)
        killer.daemon = True
        killer.start()
        try:
            app = _word_app()
            d = app.Documents.Open(os.path.abspath(fp), ReadOnly=True,
                                   AddToRecentFiles=False)
            try:
                return (d.Content.Text or "")[:MAX_TEXT]
            finally:
                d.Close(False)
        except Exception as e:
            close_word()          # 先把坏实例丢掉，下一轮 _word_app() 会重建
            if attempt == 2:
                _DOC_ERR[fp] = "%s: %s" % (type(e).__name__, str(e)[:120])
                # 🔴 Word 救不出来的，交给纯 Python 解析器再试一次（2026-11-02）
                try:
                    import doc_ole
                    txt = doc_ole.read_doc_ole(fp, MAX_TEXT)
                    if txt is not None and txt.strip():
                        _DOC_ERR.pop(fp, None)
                        return txt
                except Exception as e2:
                    _DOC_ERR[fp] = _DOC_ERR.get(fp, "") + " | ole: %s" % str(e2)[:80]
                return None
        finally:
            killer.cancel()
    return None


def close_word():
    app = _WORD_APP.get("app")
    if app is not None:
        # 同样要防「Quit 本身挂住」：给 10s，超时就按 PID 强杀
        t = threading.Timer(10.0, _kill_word_hard)
        t.daemon = True
        t.start()
        try:
            app.Quit()
        except Exception:
            pass
        finally:
            t.cancel()
    _WORD_APP["app"] = None
    _WORD_PID["pid"] = None
    _reap_owned_word()          # Quit 可能假成功，按 PID 收尸防止僵尸进程堆积


# 失败原因留痕（只存最近若干条，供 doctor/日志定位，不落库）
_DOC_ERR = {}


def abs_path(rel):
    """item['rel']（相对 KB_ROOT、/ 分隔）→ 本机绝对路径。"""
    return os.path.join(kb_index.KB_ROOT, rel.replace("/", os.sep))


def pdf_timeout_default():
    """单篇 PDF 的解析超时（秒）：KB_PDF_TIMEOUT，默认 90。"""
    try:
        return float(os.environ.get("KB_PDF_TIMEOUT") or 90)
    except (TypeError, ValueError):
        return 90.0


def pdf_workers_default():
    """PDF 并发 worker 数：KB_PDF_WORKERS，默认 CPU 核数一半（上限 8，下限 1）。

    为什么不直接拉满核数：池里的 worker 是常驻进程（每个都带一份 jieba 词典），
    全量重建要跑几十分钟，把 16 核全占满会让同机其他活（检索 / 别的 agent）
    一起饿死；「半核 + 上限 8」是够快又不独占的经验值。要更快显式设
    KB_PDF_WORKERS（封顶 16，防止手滑写 256 把机器打死）。
    """
    try:
        n = int(os.environ.get("KB_PDF_WORKERS") or 0)
    except ValueError:
        n = 0
    if n <= 0:
        n = max(1, min((os.cpu_count() or 4) // 2, 8))
    return max(1, min(n, 16))


def read_pdf_inline(fp):
    """pypdf 先行（快），抽不到字再 pdfplumber（慢但更全）。扫描件没文字层，记 gap。
    🔴 pypdf/pdfminer 默认把解析过程打到 stderr——坏 PDF 能刷几千行「Object N found」，
    把 build 日志淹了。必须静音，否则进度行被冲掉，等于没有可观测性。
    🔴 本函数是「无保护」版：只准跑在 read_pdf_guarded 起的子进程里。主进程
    直接调它，一个病态 PDF 就能把整个 build 磨住（今天全量实测卡了 ~10 分钟的
    就是单个文件）——为什么拦不住，见 read_pdf_guarded 的注释。"""
    import logging
    for name in ("pypdf", "pdfminer", "pdfplumber", "PIL"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        from pypdf import PdfReader
        r = PdfReader(fp)
        if r.is_encrypted:
            try:
                r.decrypt("")
            except Exception:
                return ""
        parts = [ (p.extract_text() or "") for p in r.pages[:200] ]
        txt = "\n".join(parts)
        if len(txt.strip()) >= MIN_TEXT:
            return txt[:MAX_TEXT]
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(fp) as pdf:
            parts = [(p.extract_text() or "") for p in pdf.pages[:200]]
        return "\n".join(parts)[:MAX_TEXT]
    except Exception:
        return ""


def _pdf_worker(fp, q):
    """read_pdf_guarded 的子进程入口。

    🔴 必须是模块级函数：spawn 靠 pickle 传 target，局部函数/lambda 传不过去。
    子进程里任何异常都要 put ("err", None)，不能任由进程默默死掉——父进程
    只看得见「队列空」，分不清是崩了还是还在磨，会白等满 timeout 才误判。
    """
    try:
        q.put(("ok", read_pdf_inline(fp)))
    except Exception:
        q.put(("err", None))


def read_pdf_guarded(fp, timeout=None):
    """带单文件超时隔离的 PDF 提取。返回 (text, status)：

      status=None          正常（text 可能为 ""：扫描件/坏件无文字层，交上层记 empty）
      status="pdf_timeout" 子进程研磨超过 timeout 秒，已 terminate/kill 掉
      status="pdf_crash"   子进程异常退出、或没把结果塞进队列

    为什么必须起子进程（线程/信号/页间 deadline 都不行）：
    1. Windows 没有 SIGALRM，pypdf/pdfminer 是 CPU-bound 的纯 Python 解析，
       主线程里没有任何「中断点」能中途掐掉它；
    2. 页间 deadline 只拦得住「每页都慢」，拦不住单页研磨——今天全量 build
       实测卡了 ~10 分钟的就是单个病态文件；
    3. 子进程隔离是唯一可靠解：坏文件磨多久都只是它自己的进程，父进程到点
       terminate，几千篇的摄取整体不被堵死（库规：单条失败不中断整体）。

    timeout 默认取环境变量 KB_PDF_TIMEOUT（秒，默认 90）。
    """
    if timeout is None:
        timeout = pdf_timeout_default()
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_pdf_worker, args=(fp, q), daemon=True)
    try:
        p.start()
    except Exception:
        return ("", "pdf_crash")
    try:
        # 轮询等结果，而不是一次 get(timeout=整段)：子进程秒崩（exitcode 非 None）
        # 时立刻走 pdf_crash，不必傻等满 timeout——坏文件一多，每次白等 90 秒
        # 照样把整体拖死。数据到达时 get 立即返回，0.5s 轮询不添正常路径延迟。
        deadline = time.monotonic() + timeout
        got = None
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                got = q.get(timeout=min(left, 0.5))
                break
            except queue.Empty:
                if p.exitcode is not None:
                    # 子进程已退出但队列看着空：put 与退出是并发的，数据可能还在
                    # 管道里——给 2s 宽限再取一次，仍空才判 crash
                    try:
                        got = q.get(timeout=2.0)
                    except queue.Empty:
                        return ("", "pdf_crash")
                    break
        if got is None:
            # 到点还在磨：先礼后兵。Windows 上 kill() 与 terminate() 同效
            # （都是 TerminateProcess），保留两级是 POSIX 上的正确写法。
            p.terminate()
            p.join(2.0)
            if p.is_alive():
                p.kill()
                p.join(1.0)
            return ("", "pdf_timeout")
        kind, val = got
        # 收到结果后收尸：正常情况子进程已退出或正在退出
        p.join(2.0)
        if p.is_alive():
            p.terminate()
            p.join(1.0)
            if p.is_alive():
                p.kill()
                p.join(1.0)
        if kind != "ok":
            return ("", "pdf_crash")
        return (val or "", None)
    finally:
        # 只关不 join_thread：本进程是纯消费方，从没 put 过
        try:
            q.close()
        except Exception:
            pass


def _pdf_pool_worker(task_q, res_q):
    """池 worker 主循环：收一个路径 → 解析 → 回一个结果；收到 None 哨兵退出。

    🔴 模块级函数（spawn 靠 pickle 传 target，局部函数传不过去）。
    解析仍用 read_pdf_inline（无保护版），超时由父进程兜：到点 terminate 本进程
    并换一个新的上来。子进程内异常一律回 ("err", None)，绝不让连接默默断掉——
    那样父进程只能干等到 deadline 才判超时，白白损失一轮并发。
    """
    try:
        while True:
            fp = task_q.get()
            if fp is None:
                return
            try:
                res_q.put(("ok", read_pdf_inline(fp)))
            except Exception:
                try:
                    res_q.put(("err", None))
                except Exception:
                    return
    except (EOFError, OSError, KeyboardInterrupt):
        return


class _PdfPool:
    """常驻 PDF worker 池：N 个 spawn 子进程并发 + 每篇独立 deadline。

    🔴 选型理由（三条都是量过/踩过才定的）：
    1. 不用 concurrent.futures.ProcessPoolExecutor：它**没有 per-task 超时**——
       future.result(timeout) 只是「不等了」，不会杀掉正在研磨的 worker，一个
       病态 PDF 会永久占住一个槽位（收工时还会卡在 join）。本池每篇自己记
       deadline，到点只 terminate 那一个 worker 并换新的，超时隔离照旧生效。
    2. 不用线程池：GIL 下纯 Python 解析没有真并行；更致命的是**线程无法被中途
       杀掉**——超时隔离会整个失效（库规：病态单文件不许堵死整体）。
    3. 子进程常驻而不是每篇新起：上一版每篇 spawn 一次，实测空转也要 4-5s
       （python 启动 + jieba + pypdf/pdfminer 导入），1265 篇光 spawn 就
       ~1.5 小时，并发收益被进程开销吃光。常驻池只 spawn N 次（+换新的次数）。

    用法：
        with _PdfPool() as pool:
            res = pool.map_batch([fp1, fp2, ...])   # 与入参同序
    """

    POLL = 0.05   # 收活轮询间隔（秒）；延迟上界 POLL×worker 数，对秒级解析可忽略

    def __init__(self, workers=None, timeout=None):
        self.workers = workers or pdf_workers_default()
        self.timeout = timeout or pdf_timeout_default()
        self.ctx = multiprocessing.get_context("spawn")
        self.slots = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------- 槽位管理 ----------
    def _spawn(self):
        """起一个 worker 槽；起不来返回 None（调用方据此降级）。"""
        try:
            task_q, res_q = self.ctx.Queue(), self.ctx.Queue()
            p = self.ctx.Process(target=_pdf_pool_worker, args=(task_q, res_q),
                                 daemon=True)
            p.start()
            return {"p": p, "task": task_q, "res": res_q,
                    "idx": None, "deadline": 0.0, "dead": False}
        except Exception:
            return None

    def _kill(self, s):
        """杀掉一个 worker 并关掉它的两条管道（terminate → 还活着就 kill）。"""
        p = s.get("p")
        try:
            if p is not None:
                p.join(0.2)
                if p.is_alive():
                    p.terminate()
                    p.join(2.0)
                    if p.is_alive():
                        p.kill()
                        p.join(1.0)
        except Exception:
            pass
        for key in ("task", "res"):
            try:
                if s.get(key) is not None:
                    s[key].close()
            except Exception:
                pass
        s["p"] = s["task"] = s["res"] = None

    def _restart(self, s):
        """槽位原地换新 worker（超时/崩溃之后）；换不出来就标 dead，由外层降级。"""
        self._kill(s)
        new = self._spawn()
        if new is None:
            s.update({"p": None, "task": None, "res": None, "idx": None, "dead": True})
        else:
            s.update(new)

    def ensure(self):
        """保证池里有活槽；一个都起不来返回 False（调用方退回逐文件单进程）。"""
        if self.slots:
            return True
        for _ in range(self.workers):
            s = self._spawn()
            if s is None:
                break
            self.slots.append(s)
        return bool(self.slots)

    def close(self):
        """收工：先给每个活 worker 发 None 哨兵让它自己退，再逐个收尸。"""
        if self.closed:
            return
        self.closed = True
        slots, self.slots = self.slots, []
        for s in slots:
            if not s["dead"] and s["task"] is not None:
                try:
                    s["task"].put(None)
                except Exception:
                    pass
        for s in slots:
            self._kill(s)

    # ---------- 批量提取 ----------
    def map_batch(self, fps):
        """批量提取：返回与 fps **同序**的 [(text, status), ...]。

        同序是断点续传的前提——先完成的不抢位，结果按下标写回，所以同样输入
        跑两次，state / gap 记录顺序完全一致。
        """
        if not fps:
            return []
        if self.closed:                       # 池已收工：别再偷偷起进程
            return [read_pdf_guarded(fp, self.timeout) for fp in fps]
        if self.workers <= 1:                 # 就一个 worker：池没意义
            return [read_pdf_guarded(fp, self.timeout) for fp in fps]
        if not self.slots and len(fps) <= 1:
            # 池还没起来、且就这一篇：为它拉 N 个常驻进程不划算
            return [read_pdf_guarded(fp, self.timeout) for fp in fps]
        if not self.ensure():                 # 进程起不来：整批退回单篇隔离
            return [read_pdf_guarded(fp, self.timeout) for fp in fps]

        out = [("", "pdf_crash")] * len(fps)
        handled = set()
        nxt, pending = 0, 0
        try:
            while nxt < len(fps) or pending:
                # 1) 派活：空闲槽领下一篇
                for s in self.slots:
                    if s["dead"] or s["idx"] is not None or nxt >= len(fps):
                        continue
                    i = nxt
                    nxt += 1
                    try:
                        s["task"].put(fps[i])
                    except Exception:
                        out[i] = ("", "pdf_crash")
                        handled.add(i)
                        self._restart(s)
                        continue
                    s["idx"] = i
                    s["deadline"] = time.monotonic() + self.timeout
                    pending += 1
                if pending == 0:
                    break
                # 2) 收活 / 判超时 / 判崩溃
                for s in self.slots:
                    if s["idx"] is None:
                        continue
                    left = s["deadline"] - time.monotonic()
                    try:
                        got = s["res"].get(timeout=max(0.0, min(self.POLL, left)))
                    except queue.Empty:
                        now = time.monotonic()
                        if now >= s["deadline"]:
                            # 这一篇磨超时：只杀这一个 worker 并换新的，整体照跑
                            out[s["idx"]] = ("", "pdf_timeout")
                            handled.add(s["idx"])
                            self._restart(s)
                            pending -= 1
                        elif s["p"] is not None and s["p"].exitcode is not None:
                            # worker 已死且没留结果 → 判崩溃，不必空等满 timeout
                            out[s["idx"]] = ("", "pdf_crash")
                            handled.add(s["idx"])
                            self._restart(s)
                            pending -= 1
                        continue
                    except Exception:
                        got = None            # 管道坏了，按崩溃处理并换人
                    i = s["idx"]
                    s["idx"] = None
                    pending -= 1
                    if got is None:
                        out[i] = ("", "pdf_crash")
                        self._restart(s)
                    else:
                        kind, val = got
                        out[i] = (val or "", None) if kind == "ok" else ("", "pdf_crash")
                    handled.add(i)
        except Exception:
            pass    # 驱动层异常绝不中断整体：没落定的篇目下面逐文件补
        # 🔴 兜底：池没给出结论的（worker 全挂 / 驱动异常）用单篇隔离补齐——
        #    任何一篇都不会因为池的问题被静默跳过（库规：单条失败不中断整体）
        for i in range(len(fps)):
            if i not in handled:
                out[i] = read_pdf_guarded(fps[i], self.timeout)
        return out


def read_pdfs_pool(fps, timeout=None, workers=None):
    """一次性批量提取 PDF（自起自收池）。返回与 fps 同序的 [(text, status), ...]。"""
    with _PdfPool(workers=workers, timeout=timeout) as pool:
        return pool.map_batch(fps)


def extract_pdfs(items, pool):
    """按 items 原顺序批量提取其中 PDF 的正文，返回 {rel: (text, status)}。

    非 PDF 项不进结果；同序由 _PdfPool.map_batch 的下标写回保证。
    """
    pdfs = [it for it in items if it["ext"] in PDF_EXTS]
    if not pdfs:
        return {}
    res = pool.map_batch([abs_path(it["rel"]) for it in pdfs])
    return {it["rel"]: r for it, r in zip(pdfs, res)}


def read_xlsx(fp):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append("## %s" % ws.title)
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 500:                     # 大表只取头部，防爆
                    break
                vals = [str(v) for v in row if v is not None]
                if vals:
                    parts.append("\t".join(vals))
        wb.close()
        return "\n".join(parts)[:MAX_TEXT]
    except Exception:
        return ""


def read_pptx(fp):
    try:
        from markitdown import MarkItDown
        r = MarkItDown().convert(fp)
        return (r.text_content or "")[:MAX_TEXT]
    except Exception:
        return ""


# ---------- .xls（Excel 97-2003）与隐私双黑名单（2026-11-04 生产回流，示例配置）----------
# 为什么开 .xls 要配门禁：老格式表格常是名单 / 成绩 / 工号一类业务原始数据，
# 直接加进 OFFICE_EXTS 等于把隐私数据灌进检索库（撞隐私红线）。
# 两道门：① 路径前缀（隐私目录整目录挡）② 文件名关键词。
# 再加检索侧出口门禁（命中隐私路径只返路径不返正文），共三道防线。
# 🔴 下面是**示例词表**，按自己库的隐私词表补齐（词表不入库，与 privacy_scan.py 同思路）。
#    漏一个 = 敏感名单进检索上下文。宁可误挡，不可漏挡。
XLS_DENY_PATH_PREFIXES = kb_index.PRIVACY_PATH_PREFIXES
XLS_DENY_NAME_KEYWORDS = (
    "名单", "名册", "通讯录", "工资表", "身份证", "成绩", "病历",
)


def xls_denied(rel_path):
    """.xls 是否被隐私门禁拒绝。返回拒绝理由（空字符串 = 允许摄取）。

    只对 .xls 生效：其它格式不进这个门，避免影响既有行为。
    """
    p = (rel_path or "").replace("\\", "/")
    for pre in XLS_DENY_PATH_PREFIXES:
        if p.startswith(pre):
            return "路径在隐私/业务目录（%s）" % pre
    name = p.rsplit("/", 1)[-1]
    for kw in XLS_DENY_NAME_KEYWORDS:
        if kw in name:
            return "文件名含「%s」" % kw
    return ""


def read_xls(fp):
    """读 .xls（Excel 97-2003）。xlrd>=2.0 只支持 .xls，正好对口。

    与 read_xlsx 同口径：每表最多 500 行、Tab 分隔、截断 MAX_TEXT。
    🔴 本函数只管「技术上读得出来」，能不能读由 `xls_denied()` 在扫描层决定。
      500 行截断是第二道保险：即便门禁漏网，也不会把超长表格整表灌进库。
    """
    try:
        import xlrd
        book = xlrd.open_workbook(fp, on_demand=True)
        parts = []
        for sh in book.sheets():
            parts.append("## %s" % sh.name)
            for i in range(min(sh.nrows, 500)):
                vals = [str(v) for v in sh.row_values(i) if v not in (None, "")]
                if vals:
                    parts.append("\t".join(vals))
        book.release_resources()
        return "\n".join(parts)[:MAX_TEXT]
    except Exception:
        return ""
EXTRACTORS = {}
for _e in TEXT_EXTS:
    EXTRACTORS[_e] = read_plain
for _e in HTML_EXTS:
    EXTRACTORS[_e] = read_html
for _e in OFFICE_EXTS:
    EXTRACTORS[_e] = {".docx": read_docx, ".doc": read_doc,
                        ".xlsx": read_xlsx, ".pptx": read_pptx, ".xls": read_xls}[_e]
for _e in PDF_EXTS:
    # scan_raw 用「ext in EXTRACTORS」判可提取，.pdf 必须留在表里；但 ingest_one
    # 对 .pdf 特调 read_pdf_guarded（子进程超时隔离），不经过本映射——这里挂
    # 无保护版只是兜底，禁止新增直接调 EXTRACTORS[".pdf"] 的调用方
    EXTRACTORS[_e] = read_pdf_inline


# ---------- 扫描 ----------
def scan_raw():
    """列 raw/ 下全部文件，按扩展名分两类：可提取 / 已知跳过。

    🔴 必须用 os.scandir 而不是 os.walk + os.stat：本机 F: 盘逐个 os.stat
    实测 5ms/个（16872 个文件 = 81 秒，dry-run 直接超时被杀）；scandir 的
    DirEntry 在 Windows 上枚举时已带回 size/mtime，零额外 syscall（0.11s）。
    """
    base = os.path.join(kb_index.KB_ROOT, "raw")
    items, gaps = [], {}
    if not os.path.isdir(base):
        return items, gaps
    stack = [base]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            # 🔴 "_" 前缀目录 = 冷存档区，不进索引（2026-10-23 立规）
                            # 起因：raw/其他/其他 里 451 个第三方包附带文件占 7,320 chunk，
                            # 归档方案是「移不删」搬到 raw/_归档/。但本函数原本遍历 raw 下
                            # **所有**子目录、无任何排除，搬进去照样被索引 → 等于白搬。
                            # 约定：raw/ 下以 "_" 开头的目录一律视为归档区（当前 raw 下无
                            # 此类目录，加这条零副作用）。要恢复索引把目录名去掉 "_" 即可。
                            if not e.name.startswith("_"):
                                stack.append(e.path)
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        # 🔴 Word 锁文件（~$ 前缀）不是文档：Word 打开文档时会生成一个
                        #    「~$同名」属主文件，进程被强杀/异常退出时就会残留在盘上。
                        #    它只有 162 字节（属主名），却能被抽出一百多字「正文」——
                        #    实测抽到 156 字、够过 MIN_TEXT 闸门，会以乱码标题混进索引。
                        #    本轮全量跑 .doc 时 Word 会被看门狗反复强杀，这类文件必然产生。
                        if e.name.startswith("~$"):
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    ext = os.path.splitext(e.name)[1].lower() or "(无)"
                    rel = os.path.relpath(e.path, kb_index.KB_ROOT).replace("\\", "/")
                    item = {"rel": rel, "ext": ext, "size": st.st_size,
                            "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
                    if ext == ".xls":
                        # 隐私门禁在**扫描层**拦，不是提取后拦：拦在这里等于这些文件
                        # 根本不进 todo 队列，不会有半截正文写进 chunks 的风险。
                        deny = xls_denied(rel)
                        if deny:
                            g = gaps.setdefault(
                                ".xls·隐私拒绝",
                                {"files": 0, "reason": "双黑名单拒绝（路径前缀/文件名关键词），不摄取正文"})
                            g["files"] += 1
                            continue
                    if ext in EXTRACTORS:
                        items.append(item)
                    else:
                        g = gaps.setdefault(ext, {"files": 0, "reason": "二进制/媒体，无正文可提取"})
                        g["files"] += 1
        except OSError:
            continue
    return items, gaps


def load_state(conn):
    row = conn.execute("SELECT value FROM index_state WHERE key=?", (STATE_KEY,)).fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except Exception:
        return {}


def save_state(conn, state):
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES(?,?)",
                 (STATE_KEY, json.dumps(state, ensure_ascii=False)))


def ingest_one(conn, item, pdf_result=None):
    """提取正文并入库；返回 'added'/'updated'/'skipped'/'empty'/'failed'/
    'extract_failed'/'pdf_timeout'/'pdf_crash'（后两者走 gap 记录，不中断整体）。

    pdf_result：run_ingest 用并发池预提取好的 (text, status)。不传就退回单篇
    子进程隔离提取（read_pdf_guarded）——**池只负责快，不改变任何一篇的结果判定**，
    两条机制（单文件超时隔离 / 单条失败不中断）在池内外完全一致。
    """
    fp = abs_path(item["rel"])
    if item["ext"] in PDF_EXTS:
        # PDF 一律走子进程隔离：病态单文件能磨 10 分钟+，主进程里没有任何手段
        # 能中途掐掉 pypdf/pdfminer 的 CPU-bound 解析。有池结果就用池的（池内部
        # 同样按篇记 deadline、到点只杀那一个 worker），没传就地起一个单篇进程。
        text, status = pdf_result if pdf_result is not None else read_pdf_guarded(fp)
        if status:                    # pdf_timeout / pdf_crash：交给上层记 gap
            return status
    else:
        text = EXTRACTORS[item["ext"]](fp)
        # 🔴 None = 抽取失败（不是"文件没字"）。绝不能和空串混为一谈：
        #    混了就会被当成「空」跳过并写进 state，以后永远不再重试。
        if text is None:
            return "extract_failed"
    text = (text or "").strip()
    if len(text) < MIN_TEXT:
        return "empty"
    h = sha1_of(text)
    old = conn.execute("SELECT content_hash FROM docs WHERE path=?", (item["rel"],)).fetchone()
    if old and old[0] == h:
        return "skipped"
    stem = os.path.splitext(os.path.basename(item["rel"]))[0]
    doc = {
        "category": "raw",
        "path": item["rel"],
        "title": stem[:80],
        "summary": text[:120].replace("\n", " "),
        "content": text,
        "hash": h,
    }
    kb_index.upsert_doc(conn, doc)
    return "added" if not old else "updated"


def run_ingest(limit=0, full=False, dry_run=False, quiet=False):
    """主流程。返回统计 dict。limit>0 时只处理前 limit 个待处理文件（分批跑用）。"""
    t0 = time.time()
    os.makedirs(os.path.dirname(kb_index.DB_PATH), exist_ok=True)
    conn = kb_index.connect()
    kb_index.ensure_schema(conn)
    items, gaps = scan_raw()
    state = load_state(conn)
    todo = [it for it in items
            if state.get(it["rel"]) != [it["size"], it["mtime"]]]
    # full：忽略 state 全量重摄（hash 相同的仍会走 skipped，不会重复劳动）
    if full:
        todo = items
    todo.sort(key=lambda x: x["rel"])
    if limit:
        todo = todo[:limit]

    def say(msg):
        if not quiet:
            print(msg, file=sys.stderr)

    say("[ingest] raw 可提取文件 %d，待处理 %d（已有 state %d）" % (len(items), len(todo), len(state)))
    if dry_run:
        by_ext = {}
        for it in todo:
            by_ext[it["ext"]] = by_ext.get(it["ext"], 0) + 1
        say("[ingest] --dry-run 待处理分布: %s" % json.dumps(by_ext, ensure_ascii=False))
        conn.close()
        return {"todo": len(todo), "total": len(items), "dry_run": True}

    cnt = {"added": 0, "updated": 0, "skipped": 0, "empty": 0, "failed": 0,
           "extract_failed": 0, "pdf_timeout": 0, "pdf_crash": 0}
    failed, done = [], 0
    # PDF 走并发池：按 todo 原顺序**分段**预提取、段内再顺序入库。
    # 为什么不整批一次性预提取：每篇正文最多 MAX_TEXT(200KB)，1265 篇同时攒在
    # 内存里峰值可达 250MB；分段让缓存随段释放，峰值封顶 PDF_SEG×MAX_TEXT。
    # 顺序仍严格按 todo 原顺序 → state / gap 记录可复现（断点续传的前提）。
    pool = None
    try:
        for start in range(0, len(todo), PDF_SEG):
            seg = todo[start:start + PDF_SEG]
            cache = {}
            if any(it["ext"] in PDF_EXTS for it in seg):
                if pool is None:
                    pool = _PdfPool()
                cache = extract_pdfs(seg, pool)
            for it in seg:
                try:
                    r = ingest_one(conn, it, pdf_result=cache.get(it["rel"]))
                except Exception as e:
                    r = "failed"
                    failed.append("%s: %s" % (it["rel"], str(e)[:80]))
                if r == "extract_failed":
                    # 🔴 抽取失败 ≠ 文件没字。单独计数、记 gap、**且不写 state**——
                    #    写了 state 就等于把它标成「处理过」，以后永远不再重试。
                    cnt[r] += 1
                    g = gaps.setdefault("extract_failed", {
                        "files": 0,
                        "reason": "抽取失败（Word COM 断开 / 文件损坏 / 被占用）：未入索引，下次增量会重试"})
                    g["files"] += 1
                    failed.append("%s: extract_failed %s"
                                  % (it["rel"], _DOC_ERR.get(abs_path(it["rel"]), "")))
                    done += 1
                    if WORD_RESTART_EVERY and done % WORD_RESTART_EVERY == 0:
                        close_word()
                    if done % BATCH == 0:
                        conn.commit()
                        save_state(conn, state)
                        say("[ingest] %d/%d 新增%d 更新%d 跳过%d 空%d 失败%d 抽取失败%d 已用时%.0fs"
                            % (done, len(todo), cnt["added"], cnt["updated"], cnt["skipped"],
                               cnt["empty"], cnt["failed"], cnt["extract_failed"],
                               time.time() - t0))
                    continue
                cnt[r] += 1
                if r in ("pdf_timeout", "pdf_crash"):
                    # 按现有 gap 机制记一种新类型（与 scan_raw 的 {ext:{files,reason}}
                    # 同构，doctor / cmd_stats 直接读）：超时/崩溃的 PDF 已隔离跳过，
                    # 未入索引；state 照记（下次增量不重试），下一篇继续——不中断整体
                    g = gaps.setdefault("pdf_timeout", {
                        "files": 0,
                        "reason": "PDF 子进程解析超时/崩溃，已隔离跳过未入索引（KB_PDF_TIMEOUT 可调，默认90s）"})
                    g["files"] += 1
                    failed.append("%s: %s" % (it["rel"], r))
                state[it["rel"]] = [it["size"], it["mtime"]]
                done += 1
                if WORD_RESTART_EVERY and done % WORD_RESTART_EVERY == 0:
                    close_word()      # 定期重启 Word，防止长连接退化连累后面所有 .doc
                if done % BATCH == 0:
                    conn.commit()
                    save_state(conn, state)
                    say("[ingest] %d/%d 新增%d 更新%d 跳过%d 空%d 失败%d 抽取失败%d 已用时%.0fs"
                        % (done, len(todo), cnt["added"], cnt["updated"], cnt["skipped"],
                           cnt["empty"], cnt["failed"], cnt["extract_failed"],
                           time.time() - t0))
    finally:
        # 池必须收：常驻 worker 是 daemon，不显式关会留到父进程退出才被回收
        if pool is not None:
            pool.close()
    conn.commit()
    save_state(conn, state)

    # 清理已消失文件的索引（state 里有、本次扫描没见到）。
    # 用扫描时拿到的 seen 集合比对，不再逐个 os.path.exists——那等于再踩一次
    # 5ms/stat 的坑（见 scan_raw 注释）。
    seen = {it["rel"] for it in items}
    gone = [p for p in state if p not in seen]
    for p in gone:
        row = conn.execute("SELECT id FROM docs WHERE path=?", (p,)).fetchone()
        if row:
            kb_index.drop_doc(conn, row[0])
        del state[p]
    if gone:
        save_state(conn, state)
        say("[ingest] 清理失效索引 %d 条" % len(gone))

    # gap 统计落库，供 doctor 读
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES(?,?)",
                 (GAP_KEY, json.dumps(gaps, ensure_ascii=False)))
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('ingest_raw_last',?)",
                 (datetime.datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM docs WHERE category='raw'").fetchone()[0]
    conn.close()
    close_word()          # 收尾必须退掉 Word 进程，否则留一个后台实例
    say("[ingest] 完成 新增%d 更新%d 跳过%d 空%d 失败%d 抽取失败%d PDF隔离%d → 库内 raw 正文 %d 篇，用时%.0fs"
        % (cnt["added"], cnt["updated"], cnt["skipped"], cnt["empty"], cnt["failed"],
           cnt["extract_failed"], cnt["pdf_timeout"] + cnt["pdf_crash"], total,
           time.time() - t0))
    if failed:
        say("[ingest] 失败明细（前 10）: " + " | ".join(failed[:10]))
    say("[ingest] " + PRIVACY_NOTE)
    out = dict(cnt)
    out.update({"total_raw_docs": total, "gone": len(gone), "failed_list": failed[:50],
                "gaps": gaps, "seconds": round(time.time() - t0, 1)})
    return out


def cmd_run(args):
    # 🔴 写库互斥（2026-10-26）：摄取是长流程（逐文件 commit），与 build / raw-scan
    #    并发时会在写锁上来回抢，两边都可能撞 database is locked。
    #    dry-run 不写库 → 不拿锁，免得「只看一眼」把正在跑的 build 排到后面去。
    if args.dry_run:
        run_ingest(limit=args.limit, full=args.full, dry_run=True)
        return
    try:
        with kb_index.write_lock(wait=getattr(args, "wait", 120)):
            run_ingest(limit=args.limit, full=args.full, dry_run=False)
    except kb_index.BuildLock.Busy as e:
        print("✗ 跳过：%s" % e)
        print("   （同一时刻只允许一个写库进程；等它结束再跑，或用 --wait N 排队）")
        sys.exit(2)


def cmd_stats(args):
    conn = kb_index.connect(readonly=True)
    state = load_state(conn)
    gaps = conn.execute("SELECT value FROM index_state WHERE key=?", (GAP_KEY,)).fetchone()
    total = conn.execute("SELECT COUNT(*) FROM docs WHERE category='raw'").fetchone()[0]
    print("raw 正文文档 %d 篇 · state %d 条" % (total, len(state)))
    if gaps:
        g = json.loads(gaps[0])
        print("跳过（无正文）: " + ", ".join("%s×%d" % (k, v["files"]) for k, v in
                                          sorted(g.items(), key=lambda x: -x[1]["files"])[:10]))
    conn.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="raw/ 正文摄取器（增量 · 断点续传）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个待处理文件（分批跑）")
    p.add_argument("--full", action="store_true", help="忽略 state 全量重扫（hash 相同仍跳过）")
    p.add_argument("--dry-run", action="store_true", help="只报告待处理分布，不写库")
    p.add_argument("--wait", type=int, default=120,
                   help="拿不到写锁时最多等多少秒（0=立刻跳过；默认 120 排队）")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
