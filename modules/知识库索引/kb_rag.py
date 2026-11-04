#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三链路统一检索入口（kb_rag）——把割裂的三个脚本收成一个入口。

体系（对应知识库「双链路 → 三链路」演进）：
  链路 A 文件链路  raw/ + wiki/           真相源，本脚本只读不写文件
  链路 B FTS5 关键词 kb_index.search_fts_fused（jieba 分词，doc+chunk 粒度 RRF）
  链路 C BGE 语义向量 bge_encoder.search_bge（bge-small-zh，CLS 池化）
  保底   字级 substring  trigram（≥3 字）/ LIKE（1-2 字，带超时熔断）
        ↑ 用户原话「一个字一句话都要搜得到」——分词器漏的，字级路兜底

CLI：
  build   摄取 raw 正文 + 补 trigram + 补 BGE 向量（全增量，可 --limit 分批）
  search  检索（--mode fused|kw|vec|substr），--json 给门户 /api/rag 用；
          T3 起支持 --since YYYY-MM-DD / --path-prefix PREFIX 元数据过滤
  ask     检索 + 组装带引用的 RAG 上下文包（可直接喂给 AI）
  backfill-meta  回填 chunks 的 path/title/mtime（元数据过滤的数据基础，幂等）
  stats / doctor  覆盖率与健康检查

硬约束（业主铁律）：
- 库里没有的明确说没有：零命中输出「库内未检索到相关内容」，不许编
- 命中必须落回 doc.path，否则 AI 引用就是猜的
- 索引库是本地派生层（含 raw/隐私/ 正文），严禁外传/上传/入 git
"""
import os
import sys
import json
import time
import sqlite3
import datetime
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

import kb_index        # noqa: E402
import bge_encoder     # noqa: E402
import bge_reranker    # noqa: E402
import ingest_raw      # noqa: E402
import build_task      # noqa: E402  T12：多会话构建队列
import vec_tables      # noqa: E402  #37：四表一致性的唯一口径（缺失 id / 补 m3 / 断言）
import query_rewrite   # noqa: E402  #39：查询改写三层（纯函数，零依赖）

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA_PREFIX = "wiki/媒体/"


def _media_penalty():
    """媒体合成页（图片 OCR / 音视频转写）在 RRF 融合里的权重系数。

    默认 0.5，依据是一次扫四档的实测（holdout 60 + raw 50，`_sweep_media_penalty.py`）：

      pen=1.0（不降权）holdout R@5 96.7% / MRR 0.8778 ｜ raw 98.0% / 0.8340
      pen=0.5           holdout R@5 **98.3%** / MRR **0.9006** ｜ raw 98.0% / 0.8440
      pen=0.35 与 0.0   **与 0.5 完全相同** ← 说明 0.5 已把媒体页压到位，再压无收益

    ⇒ 选 0.5：三个指标全部优于不降权，且「只在图片里存在的字」仍能搜到
      （实证：「抢救工作站 办理入院证 按钮」媒体页排第 2）。
      设 1.0 = 降级前行为（不降权），0.0 = 完全不参与融合。
    """
    raw = os.environ.get("KB_MEDIA_PENALTY")
    try:
        v = float(raw) if raw not in (None, "") else 0.5
    except ValueError:
        v = 0.5
    return max(0.0, min(1.0, v))


def rrf_k() -> int:
    """RRF 融合常数：与关键词路同一常数（跨路融合才公平），**默认 2**（2026-10-26 起）。

    🔴 曾经是 60。改为 2 的依据与完整对照表见 `kb_index.py` 的 `RRF_K_DEFAULT` 注释
    （100 条 judged set 实测；注意那个注释里有两套口径：旧索引 +7pp / 新索引 +3pp）。

    🔴 不能写成 `RRF_K = kb_index.RRF_K`：那是**模块导入时绑定**，环境变量在导入
    之后才设置（或同一进程内切档扫描）时这里永远是旧值——扫 k 会五档完全相同。
    改成调用时向 kb_index.rrf_k() 取值，读环境变量 KB_RRF_K，缺省取 RRF_K_DEFAULT。
    """
    return kb_index.rrf_k()


# 三路融合权重默认值（2026-10-28 由 `_sandbox_weight_ab.py` 扫出，154 条 judged set）
# 🔴 依据（必须留档，否则后人无从判断能不能改）：
#   kw=2.0 / vec=1.5 / substr=0.5  →  合并 R@5 87.0%→89.6%（+2.6pp）、MRR 0.730→0.733、
#   R@20 95.5% 持平、零命中 0% 持平。
#   三集合趋势一致（kw 1.0→1.5→2.0：30 条 76.7→80.0→83.3；100 条 88.0→90.0→91.0；
#   raw 持平台 95.8%）⇒ **不是单集合噪声**。
# 🔴 为什么 substr 取 0.5 而不是数据上略优的 0.3：substr 是字级 trigram 保底路，
#   生僻字/单字查询（如「屮艸芔茻」）只有它能命中。0.3 与 0.5 的实测差只有 1 条
#   （噪声量级），但 0.5 保留了更强的保底能力——不为 1 条差异去削保底路。
W_KW = 2.0
W_VEC = 1.5
W_SUBSTR = 0.5


def _w(env, default=1.0):
    """三路融合权重（kw / vec / substr），环境变量可覆盖，默认取上面实测最优值。

    🔴 为什么要可配：权重是靠 154 条 judged set 扫出来的，样本量有限；将来
    judged set 扩充或分块策略变更后，最优值可能变。留环境变量口子（KB_W_KW /
    KB_W_VEC / KB_W_SUBSTR），不用改代码就能重扫、回退。
    """
    try:
        v = float(os.environ.get(env, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


RRF_K = rrf_k()                 # 模块级兼容值（老引用方行为不变）
LIKE_TIMEOUT = 2.0              # 1-2 字查询走全表 LIKE 的熔断秒数
PRIVACY_NOTE = ingest_raw.PRIVACY_NOTE


def say(msg):   # 进度一律走 stderr：stdout 只留给 --json 结果，门户才解析得动
    print(msg, file=sys.stderr)


# ---------- build 自动补 m3（W1-C-4 机制根治，2026-11-04）----------
# 为什么要有这段：本进程 import 的 torch 是 `+cpu` 版，`backfill_m3()` 按 auto 档
#   「无 CUDA 只跳过、不失败」，于是**每次增量 build 都留缺口**；而 `doctor` 只看
#   chunk_bge 仍报 100%，退化是静默的。靠「人记得手工跑 GPU venv」挡不住复发
#   （2026-11-04 19:17 实证：上一轮收尾全绿后，别的窗口跑一次 build 又缺 33 块）。
# 为何用子进程：同一进程无法把 CPU 版 torch 换成 CUDA 版，只能外部解释器跑；
#   且必须在主连接 conn.close() 之后调用，否则与本次 build 的写锁冲突。
# 边界：① 仅当本机存在 GPU venv 时触发，不存在则维持原行为（个人档不判红）；
#   ② `--skip-vec` / `--limit` 分批构建不触发（那是操作者显式选择）；
#   ③ 补失败只告警、不影响 build 的 rc（补向量不该把文本构建搞红）。
GPU_VENV_PY = os.environ.get("KB_GPU_PY") or r"E:\_venvs\gpu\Scripts\python.exe"


def _m3_missing_now():
    """只读查当前 m3 缺块数；查不到返回 -1（不阻断）。"""
    try:
        conn = kb_index.connect(readonly=True)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM chunks c WHERE NOT EXISTS"
                "(SELECT 1 FROM vec_m3 v WHERE v.chunk_id = c.id)").fetchone()[0]
        finally:
            conn.close()
        return n
    except Exception:
        return -1


def auto_backfill_m3(reason=""):
    """用 GPU venv 子进程补 m3 缺块。返回 (status, detail)。

    status：done=补齐且复验缺口 0｜nogpu=本机无 GPU venv（保持原行为）｜
            fail=补失败（缺口保留，需手工）｜skip=本来就没缺口
    幂等：vector_backfill.py 只写缺失 id，不碰已有向量；最多重试 2 轮，
          因为补的过程中若别的窗口在 build，chunk 会继续增长（2026-11-04 实证）。
    """
    if not os.path.exists(GPU_VENV_PY):
        return "nogpu", GPU_VENV_PY
    script = os.path.join(HERE, "vector_backfill.py")
    if not os.path.exists(script):
        return "fail", "vector_backfill.py 不存在"
    for attempt in (1, 2):
        lag = _m3_missing_now()
        if lag == 0:
            return "skip" if attempt == 1 else "done", "缺口 0"
        if lag < 0:
            return "fail", "缺块数查询失败"
        say("[build] 🔧 自动补 m3：缺口 %d（第 %d 轮，GPU venv 子进程）…" % (lag, attempt))
        try:
            p = subprocess.run(
                [GPU_VENV_PY, "-u", script, "--skip-bge", "--batch", "16"],
                cwd=HERE, capture_output=True, text=True, timeout=1800)
            # vector_backfill.py 的 rc=5 只表示「四表行数不等」（历史孤儿行让它长期不等），
            # 不是补块失败 —— 判据看复验的 missing，不看它的 rc（速查表 §4 有同样口径）。
            if p.returncode not in (0, 5):
                tail = (p.stderr or p.stdout or "").strip().replace("\n", " ")[-300:]
                say("   ⚠️ GPU 补齐异常 rc=%s：%s" % (p.returncode, tail))
            elif lag > 0:
                say("   ✓ 已编码，用时见上（rc=%s）" % p.returncode)
        except subprocess.TimeoutExpired:
            return "fail", "GPU 补齐超时（>30min）"
        except Exception as e:
            return "fail", "%s: %s" % (type(e).__name__, str(e)[:120])
    lag = _m3_missing_now()
    if lag == 0:
        return "done", "缺口 0"
    return "fail" if lag > 0 else "done", "剩余缺口 %s" % lag



# ---------- 字级 substring 路 ----------
def ensure_trigram(conn):
    """chunks_trigram：FTS5 trigram 分词，专治「一个字/一句话都要搜得到」。
    与 chunks 同步维护（NOT IN 差量补），删篇时连带清。"""
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram USING fts5(content, tokenize='trigram')")
    miss = conn.execute(
        "SELECT id, content FROM chunks WHERE id NOT IN (SELECT rowid FROM chunks_trigram)").fetchall()
    if miss:
        conn.executemany("INSERT INTO chunks_trigram(rowid, content) VALUES(?,?)", miss)
    # 已删 chunk 的残行（drop_doc 不会知道 trigram 表）
    stale = conn.execute(
        "SELECT rowid FROM chunks_trigram WHERE rowid NOT IN (SELECT id FROM chunks)").fetchall()
    for (rid,) in stale:
        conn.execute("DELETE FROM chunks_trigram WHERE rowid=?", (rid,))
    return len(miss), len(stale)


def _fts_quote(s):
    return '"' + s.replace('"', '""') + '"'


def _like_with_timeout(conn, pattern, limit, seconds=LIKE_TIMEOUT):
    """1-2 字查询只能全表 LIKE。SQLite progress handler 做硬超时——
    超时不是失败，是降级：返回已找到的部分 + note，融合路照常工作。"""
    state = {"t0": time.time(), "n": 0}

    def handler():
        state["n"] += 1
        return 1 if (time.time() - state["t0"]) > seconds else 0

    conn.set_progress_handler(handler, 2000)
    try:
        rows = conn.execute(
            "SELECT id, doc_id, content FROM chunks WHERE instr(content, ?) > 0 LIMIT ?",
            (pattern, limit)).fetchall()
        return rows, ""
    except sqlite3.OperationalError as e:
        return [], "substr 超时(%.0fs)已降级: %s" % (seconds, str(e)[:60])
    finally:
        conn.set_progress_handler(None, 1)


def search_substr(conn, query, topk):
    """字级路：≥3 字走 trigram MATCH（快）；1-2 字走 LIKE（慢，带熔断）。
    返回 ([{doc_id, path, title, summary, score}], note)。"""
    q = (query or "").strip()
    if not q:
        return [], "空查询"
    if len(q) >= 3:
        try:
            rows = conn.execute(
                "SELECT c.id, c.doc_id, c.content FROM chunks_trigram t JOIN chunks c ON c.id = t.rowid "
                "WHERE chunks_trigram MATCH ? ORDER BY bm25(chunks_trigram) LIMIT ?",
                (_fts_quote(q), topk)).fetchall()
        except sqlite3.OperationalError as e:
            return [], "trigram 检索失败: %s" % str(e)[:80]
    else:
        rows, note = _like_with_timeout(conn, q, topk)
        if note:
            return [], note
    out, seen = [], set()
    for cid, doc_id, content in rows:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        r = conn.execute("SELECT path,title,summary FROM docs WHERE id=?", (doc_id,)).fetchone()
        if r:
            out.append({"doc_id": doc_id, "path": r[0], "title": r[1], "summary": r[2],
                        "score": 0.0, "chunk_id": cid, "chunk_text": content})
        if len(out) >= topk:
            break
    return out, ""


def _sliding_windows(text, toks, win=400, overlap=80, maxw=2):
    """把块正文切成 ≤win 的重叠窗口（滑窗 + 取 max 的前置步骤）。

    为什么滑窗而不是放宽 max_length：`bge_reranker` 的 `max_length=512` 是
    CPU 推理成本红线（放宽到 8192 会让单次推理慢一个数量级，而精排已是延迟大头）。
    长块切成重叠窗口、各段独立打分取 max，既不丢信息也不改模型开销上限
    （QAnything 父子 chunk 的 `overlap_tokens=80 + max` 同款做法）。

    窗口位置优先围绕 **token 实际命中处**（不是从块首硬切）：相关性信号就在
    那一段，切到别处等于给模型喂噪声。无字面命中（纯语义召回）时退化为从块首
    均匀滑窗。窗口数 ≤maxw，防止长块把精排对数放大到不可接受。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= win:
        return [text]
    stride = max(1, win - overlap)
    centers = []
    for t in toks or []:
        if not t:
            continue
        p = text.find(t)
        # 只收彼此拉开 ≥stride/2 的命中点，避免几个相邻 token 堆出重复窗口
        if p >= 0 and all(abs(p - c) >= max(1, stride // 2) for c in centers):
            centers.append(p)
        if len(centers) >= maxw:
            break
    if not centers:
        centers = list(range(0, len(text), stride))[:maxw]
    wins, seen = [], set()
    for c in centers:
        a = max(0, c - win // 3)          # 命中点前留 1/3 给上下文
        b = min(len(text), a + win)
        a = max(0, b - win)
        sig = a // stride
        if sig in seen:
            continue
        seen.add(sig)
        wins.append(text[a:b])
    return wins or [text[:win]]


def _rerank_texts_for(conn, q, toks, doc_ids):
    """B2（2026-10-23）：给每个候选文档准备「**真正被命中的块正文**」作精排输入。

    背景（实测，不是推断）：kw/vec/字级三路返回体都不带正文，
    `bge_reranker._text_of()` 的取值链 `chunk_text → summary → title` 只能
    回落到 summary（文档前 120 字），于是 Cross-Encoder 给**正确答案打 0.0000 分**、
    给兄弟文档打 0.9936 分——不是"没把握"，是**自信地认错人**。
    4/4 主流开源实现（RAGFlow 92k★ / QAnything 14k★ / …）喂的都是
    **完整 chunk 原文**，无一家用文档摘要。

    选块规则：**token 重叠数最多的块**，不是「首个含任意 token 的块」。
    实测 #6 的 gt 文档有 28 块，chunk3 命中 3 词、chunk16 命中 4 词——
    取首个只排第 6（进不了 top5），取重叠最多排第 1。

    返回 {doc_id: [窗口文本, ...]}；**取不到正文的 doc 直接不出现在返回里**，
    调用方于是走原 summary 兜底链——优雅降级，绝不因为拿不到正文废掉整次检索。

    可调（环境变量，不必改代码）：KB_RERANK_WIN / KB_RERANK_OVERLAP / KB_RERANK_MAXW
    """
    win = int(os.environ.get("KB_RERANK_WIN") or "400")
    overlap = int(os.environ.get("KB_RERANK_OVERLAP") or "80")
    maxw = int(os.environ.get("KB_RERANK_MAXW") or "2")
    toks = [t for t in (toks or []) if t]
    doc_ids = [d for d in (doc_ids or []) if d is not None]
    if not doc_ids:
        return {}
    try:
        ph = ",".join("?" * len(doc_ids))
        rows = conn.execute(
            "SELECT id, doc_id, chunk_no, content FROM chunks WHERE doc_id IN (%s) "
            "ORDER BY doc_id, chunk_no" % ph, tuple(doc_ids)).fetchall()
    except Exception as e:
        print("[rerank-in] 命中块读取失败，本次回落文档摘要：%s" % str(e)[:120],
              file=sys.stderr)
        return {}
    by_doc = {}
    for _cid, did, _cno, content in rows:
        by_doc.setdefault(did, []).append(content or "")
    out = {}
    for did in doc_ids:
        chunks = by_doc.get(did) or []
        if not chunks:
            continue
        best, best_hit = chunks[0], -1
        for content in chunks:
            hit = sum(1 for t in toks if t in content)
            if hit > best_hit:
                best, best_hit = content, hit
        out[did] = _sliding_windows(best, toks, win, overlap, maxw)
    return out


# ---------- 三链路融合 ----------
def _since_to_epoch(s):
    """'YYYY-MM-DD' → 当天 00:00（本地时区）的 epoch 秒。

    本地时区而非 UTC：chunks.mtime 来自文件 mtime/ingest state（都是本地时间
    语义），两边必须同一时钟，否则「今天改的文件」会因 8 小时时差被漏掉。
    格式错抛 ValueError（消息自解释，argparse 与 cmd_search 都能直接展示）。
    """
    try:
        return int(datetime.datetime.strptime(str(s).strip(), "%Y-%m-%d").timestamp())
    except ValueError:
        raise ValueError("日期需要 YYYY-MM-DD 格式，收到：%r" % (s,))


def _since_arg(s):
    """argparse type：--since 'YYYY-MM-DD' → 当天 00:00 本地 epoch 秒。

    为什么在解析期就转换：日期格式错是用法错误，应该在敲回车时就报出来，
    而不是等检索跑完。search_unified 同时兼容直接传字符串（程序内调用/测试）。
    """
    import argparse
    try:
        return _since_to_epoch(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _doc_meta_map(conn, doc_ids):
    """批量取 doc 级 (path, mtime)，供元数据过滤用（一次查询，不逐篇回表）。

    老库没有 docs.mtime 列时降级：mtime 全 None——--since 会安全地返回空
    （时间未知不满足「>= 日期」），--path-prefix 仍可用。绝不静默给错数据。
    """
    out = {}
    ids = list(doc_ids)
    for i in range(0, len(ids), 400):  # SQLite 变量数上限 999，分批防爆
        part = ids[i:i + 400]
        ph = ",".join("?" * len(part))
        try:
            rows = conn.execute(
                "SELECT id, path, mtime FROM docs WHERE id IN (%s)" % ph, part).fetchall()
        except sqlite3.OperationalError:
            rows = [(r[0], r[1], None) for r in conn.execute(
                "SELECT id, path FROM docs WHERE id IN (%s)" % ph, part).fetchall()]
        for did, p, mt in rows:
            out[did] = (p, mt)
    return out


def _enrich(conn, items, query):
    """给命中补 category / snippet / matched——前端与 ask 都吃这套字段。"""
    toks = [t for t in kb_index.tokenize(query) if len(t.strip()) > 1][:6]
    for it in items:
        r = conn.execute("SELECT category, summary FROM docs WHERE id=?", (it["doc_id"],)).fetchone()
        it["category"] = r[0] if r else ""
        snippet, matched = "", []
        row = None
        if it.get("chunk_id"):
            row = conn.execute("SELECT content FROM chunks WHERE id=?",
                               (it["chunk_id"],)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT content FROM chunks WHERE doc_id=? AND instr(content, ?) > 0 LIMIT 1",
                (it["doc_id"], query)).fetchone()
        if row:
            snippet = row[0]
        if not snippet:
            snippet = (r[1] if r else "") or ""
        pos = snippet.find(query) if query else -1
        if 0 <= pos < len(snippet):
            a, b = max(0, pos - 60), min(len(snippet), pos + len(query) + 90)
            snippet = ("…" if a else "") + snippet[a:b] + ("…" if b < len(snippet) else "")
        else:
            snippet = snippet[:160]
        it["snippet"] = snippet.replace("\n", " ").strip()
        low = (it["snippet"] + " " + (it.get("title") or "")).lower()
        it["matched"] = [t for t in toks if t.lower() in low]
        # 词覆盖率：查询词有几个出现在命中里。vec 路同义命中可能 matched 为空但结果相关，
        # 所以覆盖率只用于「透明化警示」不用于过滤（防误杀同义改写命中）。
        it["coverage"] = (round(len(it["matched"]) / len(toks), 2) if toks else None)
        it.pop("chunk_text", None)
        it.pop("summary", None)
        # ---- 出口门禁（2026-11-04，随 .xls 开摄一起上；作者批 A 方案的第二道门）----
        # 🔴 为什么必须下沉到 _enrich（而不是只在 cmd_ask 输出层挡）：
        #   `search` / `--json` / 门户 / 任何直接调 `search_unified` 的调用方
        #   拿到的都是同一批 items，只有 cmd_ask 挡 = 其他出口裸奔。
        #   门禁要在**出口统一**做，不能指望每个调用方自己记得挡。
        # 🔴 为什么不是「不返回这条结果」：返回路径仍有用（知道库里有、可本地打开），
        #   断掉的是**正文与命中词**——那才是会被拼进模型上下文、被复制外传的部分。
        if is_privacy_path(it.get("path")):
            it["snippet"] = PRIVACY_EXPORT_MARK
            it["matched"] = []
            it["coverage"] = None
            it["redacted"] = True
    return items


# ---- D5 别名 / 同义词表（2026-10-27，零重编码，查询时扩展）----
def _load_alias_table(path=None):
    """加载别名表（JSON）。缺省与本文件同目录的 alias_table.json。加载失败返回空表。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alias_table.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tbl = {}
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if isinstance(v, str):
                v = [v]
            if not isinstance(v, list):
                continue
            tbl[k.strip().lower()] = [str(x).strip() for x in v if str(x).strip()]
        return tbl
    except Exception:
        return {}

_ALIAS_TABLE = _load_alias_table()

def expand_alias(query):
    """把查询里命中的别名词的同义词追加到查询串（查询时扩展，不改索引）。

    为什么这样做：#4 智能体vs工作流 / #23 flask / #41 提示词注入 这类「整篇级 /
    项目级」口语查询，gt 是总览类文档，纯靠字面召回进不了池；追加同义词把相关
    文档拉进融合池，再由 rerank（用原始 query）按本意重排。零重编码。"""
    if not _ALIAS_TABLE or not query:
        return query
    q = query.strip()
    ql = q.lower()
    seen, extra = set(), []
    for key, syns in _ALIAS_TABLE.items():
        if not key or key.lower() not in ql:
            continue
        for s in syns:
            sl = (s or "").strip().lower()
            if sl and sl not in ql and sl not in seen:
                seen.add(sl)
                extra.append(s.strip())
    if not extra:
        return q
    return (q + " " + " ".join(extra)).strip()


def _is_cjk_query(q):
    """查询是否以 CJK 字符为主 —— 用于外文/中文分流（T1 语言路由）。

    阈值 0.5：中文查询 CJK 占比极高（标点/空格很少），英文/代码/数字查询 CJK
    占比近 0，两者界限清晰。范围覆盖 CJK 统一表意文字 + 日文假名 + 韩文。
    """
    q = (q or "").strip()
    if not q:
        return False
    cjk = sum(1 for ch in q
              if ("\u4e00" <= ch <= "\u9fff")       # 中日韩统一表意文字
              or ("\u3040" <= ch <= "\u30ff")       # 日文假名
              or ("\uac00" <= ch <= "\ud7af"))      # 韩文
    return cjk / len(q) >= 0.5


def _vec_route_m3(q):
    """外文查询是否切到 bge-m3 多语塔。

    🔴 默认关闭（红线）：不设 KB_VEC_ROUTE 时一律 False ⇒ 走现状 small-zh，
    中文/外文行为逐位不变。只有 KB_VEC_ROUTE 为真值 **且** 查询判为外文（非 CJK）
    时才切 m3；中文查询即使开关开着也仍走 small-zh。
    """
    if not os.environ.get("KB_VEC_ROUTE"):
        return False
    return not _is_cjk_query(q)


def search_unified(conn, query, topk=5, mode="fused", rerank=True,
                   since=None, path_prefix=None, rewrite=None):
    """三链路 RRF 融合。mode: fused(全开) / kw / vec / substr。
    rerank: RRF 融合后过 Cross-Encoder 精排（仅 fused 模式生效，默认开）。
    since: 'YYYY-MM-DD' 或 epoch 秒——只返 mtime >= 该日期的文档（T3 元数据过滤）。
    path_prefix: 只返 path 以该前缀开头的文档（如 'wiki/技术'）。
    两个过滤都在**融合后、rerank 前**执行（理由见下方注释）。
    rewrite: 改写档位 off|l0|l1|l2（#39）。None = 读 `KB_REWRITE`（默认 off，见 query_rewrite.DEFAULT_LEVEL）；
             **`rewrite="off"` 时行为与改造前逐位一致**（回滚开关，零代码）。

    🔴 改写只作用于**召回**：精排（rerank）、摘要补齐（_enrich）、查询日志（log_query）
       一律用**原始 q**。否则精排分被同义词串稀释、查询日志也失去可分析性。
    """
    t0 = time.time()
    q = (query or "").strip()
    if not q:
        return [], "查询为空"
    # #39 改写计划（纯函数，不碰 DB）。off 档 = 原文 + 别名扩展（D5 老行为）。
    # 🔴 别名扩展先单独算一次：它是 D5 老能力，note 只该在**它真的改了查询**时出现。
    #    若直接用 subs[0][0] 与 q 比较，l0 拆分会把「知识库 和 wiki…」的
    #    subs[0]（"知识库"）误报成"别名扩展"—— 一条会误导排障的假 note。
    q_alias = expand_alias(q)
    plan = query_rewrite.plan(q, level=rewrite, alias_fn=lambda _q: q_alias)
    subs = plan.subqueries or [(q, 1.0)]
    q_exp = subs[0][0]
    # --since 归一化成 epoch 秒（CLI 层已校验过格式，这里双保险 + 兼容传 epoch）
    since_ts, since_txt = None, ""
    if since is not None:
        if isinstance(since, str):
            since_txt = since.strip()
            since_ts = _since_to_epoch(since_txt)
        else:
            since_ts = int(since)
            # epoch 是当天 00:00 本地时间，round-trip 回日期字符串是精确的
            since_txt = datetime.datetime.fromtimestamp(since_ts).strftime("%Y-%m-%d")
    rrf, meta, sources = {}, {}, {}
    notes = list(plan.notes)
    if plan.level == 0:
        # off 档：note 文本保持改造前的原样（便于 off/on 两份输出逐位对比）
        if q_alias != q:
            notes.append("D5 别名扩展: %s" % q_alias)
    else:
        notes.append("改写 L%d：%d 子查询" % (plan.level, len(subs)))
        if q_alias != q:
            notes.append("D5 别名扩展: %s" % q_alias)

    k = rrf_k()   # 运行时解析（KB_RRF_K 可配，默认 2），避免导入时绑定吃不到环境变量

    # 🔴 媒体合成页降权（2026-10-29）：图片 OCR / 音视频转写产出的是**机器识别文本**，
    # 字面碎、断行多、重复度高，当成普通文档参与 RRF 会把真文档挤出前几名。
    # 实证：2,448 篇 OCR 页进库后（语料 48,177→54,776 块，+13.7%），
    # holdout MRR@5 0.9256→0.8778（**−0.0478**）、R@5 98.3%→96.7%；
    # raw 侧 MRR 0.8623→0.8340。对照：S4 换 bge-m3 才 −0.0098 就被否决了。
    # 0.0 = 完全不参与（等于旧行为），1.0 = 不降权。可用 KB_MEDIA_PENALTY 覆盖。
    media_pen = _media_penalty()

    def add(items, path_name, weight):
        for rank, it in enumerate(items, 1):
            pen = media_pen if str(it.get("path") or "").startswith(MEDIA_PREFIX) else 1.0
            if pen <= 0.0:
                continue          # 不参与融合，但也不阻断其它路命中该文档
            rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + weight * pen / (k + rank)
            prev = meta.get(it["doc_id"])
            if prev is None or (it.get("chunk_id") and not prev.get("chunk_id")):
                meta[it["doc_id"]] = it
            sources.setdefault(it["doc_id"], set()).add(path_name)

    # 🔴 #39：三路召回按 plan.subqueries 循环，权重 = 路权重 × 子查询权重。
    #    off 档 subs=[(q_exp,1.0)]，`w * 1.0 == w`（float 乘 1.0 精确），且循环次序
    #    仍是 kw→vec→substr，与改造前**逐位一致**。
    use_m3_route = _vec_route_m3(q)
    route_noted = [False]

    for sub, w_sub in subs:
        if mode in ("fused", "kw"):
            try:
                add(kb_index.search_fts_fused(conn, sub, topk * 2), "kw",
                    _w("KB_W_KW", W_KW) * w_sub)
            except Exception as e:
                notes.append("kw 路失败: %s" % str(e)[:60])
        if mode in ("fused", "vec"):
            try:
                # T1 语言路由（默认关闭）：KB_VEC_ROUTE 开启且查询为外文时切 bge-m3 多语塔；
                # 否则走现状 small-zh。开关关闭时本分支与历史行为逐位相同。
                if use_m3_route:
                    add(bge_encoder.search_bge_m3(conn, sub, topk * 2), "vec",
                        _w("KB_W_VEC", W_VEC) * w_sub)
                else:
                    add(bge_encoder.search_bge(conn, sub, topk * 2), "vec",
                        _w("KB_W_VEC", W_VEC) * w_sub)
                if not route_noted[0]:      # 只记一次（3 条子查询不该刷 3 行）
                    notes.append("vec 路由: m3" if use_m3_route else "vec 路由: small-zh")
                    route_noted[0] = True
            except Exception as e:
                notes.append("vec 路失败: %s" % str(e)[:60])
        if mode in ("fused", "substr"):
            items, note = search_substr(conn, sub, topk * 2)
            if note and note not in notes:
                notes.append(note)
            add(items, "substr", _w("KB_W_SUBSTR", W_SUBSTR) * w_sub)
        # #41 图谱实验路：默认**永不执行**（KB_KG 未设），且函数内 import ⇒ 零加载。
        # 排除已命中文档：否则这路只是给已有结果加分，等于没做实验（见 kg_gate 注释）。
        if os.environ.get("KB_KG") == "1":
            try:
                import kg_gate
                add(kg_gate.expand_1hop(conn, sub, topk * 2, set(rrf.keys())),
                    "kg", _w("KB_W_KG", 0.5))
            except Exception as e:
                notes.append("kg 路失败: %s" % str(e)[:60])

    # #39 L2：HyDE 假设答案单独走一路 vec（权重 KB_W_HYDE=0.8，失败仅记 note）。
    # 只并入 vec 路、不改关键词路：假设答案是"假文档"，让它参与精排/关键词匹配
    # 只会引入噪声。失败绝不阻断——检索照常返回 L1 的结果。
    if plan.hyde and mode in ("fused", "vec"):
        try:
            add(bge_encoder.search_bge(conn, plan.hyde, topk * 2), "vec_hyde",
                _w("KB_W_HYDE", 0.8))
        except Exception as e:
            notes.append("HyDE 召回失败，已忽略: %s" % str(e)[:60])

    # ---- 元数据过滤（T3）：融合之后、rerank 之前 ----
    # 为什么加在这个位置（三个候选位逐一推演）：
    #   ① 各路召回内部过滤（kw/vec/substr 各加 WHERE）：同一逻辑三处实现，
    #      且各路召回深度只有 topk*2，过滤后可能凑不够 rerank 的 pool_n；
    #      BGE 向量路在 numpy 侧打分，SQL 过滤根本掺不进去——不可行。
    #   ② 融合后、rerank 前（本次选择）：RRF 分此时已跨路可比，在**完整融合池**
    #      上一刀切，单点实现；rerank 仍然只在过滤后的集合上跑（业主要求），
    #      精排分不会被「与查询相关但被过滤」的文档稀释；融合池按 RRF 序截
    #      pool_n，过滤放在截断**之前**，保证池子里装的是排名最靠前的合格文档。
    #   ③ rerank 后、截 topk 前：rerank 会对即将被过滤掉的文档白算 cross-encoder
    #      （0.23s/对），且「全池精排分低于阈值判零命中」会被受限文档的低分
    #      污染——库里明明有很多合格命中，却可能误报「未检索到」。
    # 代价：融合池照常全量计算（过滤不省召回成本）——换取正确性与单点实现，值得。
    if since_ts is not None or path_prefix:
        dmeta = _doc_meta_map(conn, list(rrf.keys()))
        kept = {}
        for did, sc in rrf.items():
            p, mt = dmeta.get(did, (None, None))
            if path_prefix and not (p or "").startswith(path_prefix):
                continue
            # mtime 未知（NULL，如未跑 backfill-meta 的新文档）不满足 --since：
            # 「无法证明够新」与「不够新」在过滤语义下同权，宁缺毋滥
            if since_ts is not None and not (isinstance(mt, int) and mt >= since_ts):
                continue
            kept[did] = sc
        notes.append("元数据过滤：%s%s融合池 %d→%d（rerank 在过滤后集合上运行）" % (
            "--path-prefix=%s " % path_prefix if path_prefix else "",
            "--since=%s " % since_txt if since_ts is not None else "",
            len(rrf), len(kept)))
        rrf = kept
    # ---- Cross-Encoder 精排（2026 RAG 金标准：Hybrid → RRF → rerank → top-k）----
    # 为什么加：RRF 只按名次融合，看不出「关键词都对但答非所问」的擦边文档；
    # bge-reranker-v2-m3 联合编码 query+文档出相关性分，只对融合池 top-N 打分，
    # 0.23s/对延迟可控，且本地推理不出网。必须在 _enrich 之前做——
    # _enrich 会 pop 掉 chunk_text/summary，精排的文本取值链就没了。
    pool_n = min(max(topk * 3, 20), int(os.environ.get("KB_RERANK_POOL") or "30"))
    ranked = sorted(rrf.items(), key=lambda x: -x[1])[:pool_n]
    rerank_on = rerank and mode == "fused"
    rerank_scores = {}          # doc_id → 精排分（精排生效的才在，_enrich 前先攒着）
    if rerank_on and len(q) <= 4:
        # 灵敏度保护：短查询 = 字面查找意图（如「茻」「血透」），用户核心指标
        # 「一个字一句话都要搜得到」优先——精排可能把唯一的字面命中压出 top-k
        notes.append("rerank 跳过：短查询(≤4字)按字面查找处理，保持 RRF 原序")
        rerank_on = False
    if rerank_on and ranked:
        # B2（2026-10-23）：精排输入从「文档摘要」换成「命中块正文（滑窗）」。
        # 取块失败（SQL 异常 / 该 doc 无 chunk）时返回空字典 → 下面的 pool_items
        # 不带 rerank_texts → bge_reranker 走原 chunk_text → summary → title 兜底链，
        # 行为与修改前逐位一致。绝不因为修输入而让检索失败。
        try:
            rerank_texts = _rerank_texts_for(conn, q, kb_index.tokenize(q),
                                             [d for d, _ in ranked])
        except Exception as e:                      # noqa: BLE001 - 兜底不可崩
            rerank_texts = {}
            notes.append("精排输入取块失败，回落文档摘要：%s" % str(e)[:60])
        pool_items = []
        for d, _ in ranked:
            it = dict(meta[d])
            texts = rerank_texts.get(d) or []
            if texts:
                it["chunk_text"] = texts[0]     # 兜底链（_text_of）继续有真值可取
                it["rerank_texts"] = texts      # 滑窗多段，bge_reranker 取 max
            pool_items.append(it)
        rr, why = None, ""
        try:
            rr = bge_reranker.rerank(q, pool_items, topk=pool_n)
        except Exception as e:          # rerank 内部已兜底返 None，这里双保险
            why = str(e)[:60]
        if rr:
            # 精排生效：按相关性分重排融合池，再截 topk
            rerank_scores = dict(rr)
            rrf_backup = list(ranked)      # 降级用：RRF 融合原序备份
            ranked.sort(key=lambda x: -rerank_scores.get(x[0], -1.0))
            # 低分降级（2026-10-23 judged set 实证，替代原「零命中硬判定」）：
            # 100 条基线显示 rerank 对本库口语化中文查询系统性打分偏低——34 条零命中里
            # 33 条是「低分硬清空」造成的（库里明明有，无 rerank 时 gt 多排第 1-2，最高分 mean 仅 0.0175）。
            #
            # 🔴 2026-10-23 晚间二次定性（推翻上面旧注释里「阈值误杀」这个说法）：
            #    这 33 条**不是**「阈值定得太高误杀」，根因是两层，调阈值完全救不了——
            #    ① **精排输入退化**：精排打分的对象是「文档前 120 字 summary」，不是命中块。
            #       `search_bge` / `search_fts_chunks` 的返回体都不含正文，
            #       `bge_reranker._text_of()` 的取值链 `chunk_text → summary → title` 只能回落到 summary。
            #    ② **低分硬清空**：全池最高分 < `KB_RERANK_MIN`（默认 0.05）时直接清空结果。
            #    实测证据：6 条问题 query 的 gt 精排分 0.0000/0.0002/0.0001/0.0011/0.0248/0.0426
            #    （6/6 全低于阈值），而同期 top1 分是 0.9936/0.7787/0.4770/0.1697
            #    —— 精排不是「没把握」，是**自信地认错人**，且 12/12 的 top1 都是 gt 的兄弟文档。
            #    更强的对照：关掉精排后 R@5 68.0%→**82.0%**、MRR@10 0.5588→**0.7023**、
            #    冷查询 8.6–14.5s→**0.13–0.32s** ⇒ 精排在本库**同时是最慢的和最不准的**。
            #    结论：**这条路径的出路是修输入（B2）、不是调阈值**。已实测否决「调 KB_RERANK_MIN」。
            # 「库里有的不许说没有」铁约束 > 防擦边：低于阈值不再清空，
            # 降级返回 RRF 融合原序并在 note 标注低置信，由用户自行核对；
            # 真·库里没有时，下面的覆盖率警示（max coverage==0）仍会触发，不会放行擦边结果。
            best = rr[0][1]
            min_score = float(os.environ.get("KB_RERANK_MIN") or "0.05")
            if best < min_score:
                ranked = rrf_backup
                rerank_scores = {}          # 最终序非精排序，不标 rerank_score
                notes.append("rerank 最高分 %.4f 低于阈值 %.2f：精排层对本查询区分度不足，"
                             "已按未精排的融合序返回（低置信，请自行核对）；"
                             "库里确无内容时会另报覆盖率警示" % (best, min_score))
        elif rr is None:
            notes.append("rerank 跳过：%s，保持 RRF 原序（降级不崩）"
                         % (why or "模型不可用或推理异常"))
    ranked = ranked[:topk]
    out = []
    for doc_id, sc in ranked:
        it = dict(meta[doc_id])
        it["score"] = round(sc, 4)
        it["source"] = "+".join(sorted(sources[doc_id]))
        if doc_id in rerank_scores:
            it["rerank_score"] = round(rerank_scores[doc_id], 4)
            it["reranked"] = True
        out.append(it)
    _enrich(conn, out, q)
    # L1 修复（QA 验收 2026-10-22 提出）：kw 路 OR 语义 + vec 路无阈值会让库里不存在的
    # 查询返回擦边文档而非「没有」。只警示不过滤——同义改写命中（如「搭建」vs「建设」）
    # 的 matched 也可能为空，过滤会误杀「一个字都要搜得到」的灵敏度。
    covs = [it.get("coverage") for it in out if it.get("coverage") is not None]
    if out and covs and max(covs) == 0.0:
        notes.append("⚠️ 全部命中均不含查询关键词，疑似语义擦边结果；"
                     "如内容确应存在，请确认是否已收录进 raw/ + wiki/")
    kb_index.log_query(conn, q, mode, out, (time.time() - t0) * 1000)
    conn.commit()
    return out, "; ".join(notes)


# ---------- 回答层拒答（T1：覆盖度不足 → 明确说没有，不编造） ----------
# 🔴 阈值选择（2026-06 实测，见 _probe_refuse_t1.py 与
#   reports/2026-10-30-评测集任务清单-102配额与池化表-定稿.md §④）：
#   拒答主信号用「查询词在全部命中里的覆盖率 max(coverage)」，**不是**融合分。
#   原因——RRF/trigram 分在擦边（子串巧合）命中时仍偏高：
#     实测 SLA赔付 top1 融合分 0.6667，但 coverage 全 0（库内任何文档都不含「SLA/赔付」词）。
#     若用分阈值会漏判；只有 coverage=0 能稳定区分「库里真没有」与「真有命中」。
#   coverage 来自 _enrich：命中块/标题/摘要是否含查询 token（同义改写命中可能为空，
#   所以 coverage 仅作「透明化警示 + 拒答主信号」，不作过滤——防误杀同义改写）。
#   判定：所有命中 coverage 均 ≤ REFUSE_MAX_COVERAGE（默认 0.0）→ 视为无自信命中，拒答。
#   库里真有命中（coverage>0）时一律不拒答——恪守「库里有的不许说没有」铁约束。
REFUSAL_NO_HIT = "库内未检索到相关结论，不敢编造"
REFUSE_MAX_COVERAGE = 0.0

# ---------- 隐私出口门禁（2026-10-31 作者拍板：本机可检索，但不得外传）----------
# 口径：**检索层不过滤**（"本机搜得到"是需求，砍召回等于砍功能）；
# 只在「把内容交给模型 / 拼成可外传文本」的出口，把隐私正文换成占位符，
# 保留路径与标题让用户知道它存在。
# 🔴 只认 raw/隐私/ 与 raw/个人信息/ 两个前缀——**不套用 cloud_publishable 全量白名单**：
# 后者会把 raw/项目/（示例市原始数据）、raw/微信文件/、raw/生活/ 等非隐私品类
# 也判为"不可发布"，那会误伤本机正常检索。
PRIVACY_EXPORT_MARK = "🔒【隐私内容·本机可检索，禁止外传/引用原文】"


def is_privacy_path(path):
    """是否落在隐私黑名单目录（raw/隐私/ · raw/个人信息/）。

    单一真相源取 kb_index.PRIVACY_PATH_PREFIXES（与索引层同一常量），
    取不到时才回落硬编码，避免两处定义漂移。
    """
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    prefixes = getattr(kb_index, "PRIVACY_PATH_PREFIXES",
                       ("raw/隐私/", "raw/个人信息/"))
    return any(p.startswith(x) for x in prefixes)


def is_no_confident_hit(results, note=None):
    """回答层拒答判定：是否『库内没有自信命中』，应当拒答而非把擦边结果当答案。

    返回 True 的两种情形（任一即拒答）：
      ① results 为空——检索零命中；
      ② 所有结果的 coverage 均为 0（库内任何文档都不含查询词）——纯语义擦边/子串巧合。
    note 仅透传可观察性，不参与判定（融合分在擦边命中时仍偏高，不可靠）。
    库里真有命中（coverage>0）一律返回 False，避免误伤「一个字都要搜得到」的灵敏度。
    """
    if not results:
        return True
    covs = [r.get("coverage") for r in results if r.get("coverage") is not None]
    if not covs or max(covs) <= REFUSE_MAX_COVERAGE:
        return True
    return False


# ---------- RAG 上下文包 ----------
def build_context(query, results):
    """组装带编号引用的上下文包。无自信命中必须明说——库里没有的明确说没有，不编造。

    T1 拒答分支：检索到结果但无一为自信命中（coverage 全 0，纯语义擦边）时，
    直接输出拒答串而非把擦边结果拼成上下文包喂给模型（那会诱导模型编造）。"""
    if not results:
        return (REFUSAL_NO_HIT + "（query 已记入 query_log）。"
                "可换泛词重试，或确认该内容是否已在 raw/ + wiki/ 内。"), []
    if is_no_confident_hit(results):
        return (REFUSAL_NO_HIT + "（检索到的若干结果均不含查询关键词，疑似语义擦边；"
                "库内可能没有相关结论，不敢编造。）可换泛词重试，"
                "或确认该内容是否已在 raw/ + wiki/ 内。"), []
    lines = ["## 上下文包（问题：%s）" % query, ""]
    cites = []
    n_privacy = 0
    for i, it in enumerate(results, 1):
        path = it["path"]
        priv = is_privacy_path(path)
        lines.append("[%d] 《%s》" % (i, it.get("title") or path))
        lines.append("路径：%s" % path)
        if priv:
            # 出口门禁：隐私正文不进上下文包，只留占位（路径仍给，便于本机追溯）
            lines.append("摘录：%s" % PRIVACY_EXPORT_MARK)
            n_privacy += 1
        else:
            lines.append("摘录：%s" % (it.get("snippet") or "")[:400])
        lines.append("")
        cites.append({"n": i, "path": path,
                      "title": it.get("title") or "", "privacy": priv})
    lines.append("## 引用清单")
    for c in cites:
        lines.append("[%d] %s%s" % (c["n"], c["path"],
                                    "  🔒隐私" if c.get("privacy") else ""))
    lines.append("")
    if n_privacy:
        lines.append("⚠️ 本次 %d 条命中来自隐私目录，原文已替换为占位符"
                     "（本机可检索、禁止外传：不得引用原文、不得写入对外发布物）。"
                     % n_privacy)
    lines.append("（以上为检索结果原文摘录；作答时每句标注引用编号，库内没有的内容不要补充。）")
    return "\n".join(lines), cites


# ---------- build ----------
def backfill_bge(conn, limit=0, batch=32):
    """给没有向量的 chunk 补 BGE。增量：NOT IN 差量；每批 commit，可随时中断续跑。"""
    # 🔴 2026-11-03（T02）删掉一行**死代码** `import numpy as np`：本函数体内
    # 从未用到 np（真正需要 numpy 的是 bge_encoder.encode()，它自己会 import）。
    # 保留它的后果是：一台没装 numpy 的机器上，**哪怕一块都不用编码**（增量 build
    # 常态），build 也会在补写阶段直接 ModuleNotFoundError 崩掉 —— 这与
    # 「无 GPU/无重依赖的机器上增量 build 只跳过、不失败」的承诺直接矛盾。
    # 本库实测：并发会话跑 kb_index.py build 时 5s 默认超时必挂（database is locked），
    # WAL 单写者模型下全量 build 的写事务窗口以分钟计，30s 起步
    conn.execute("PRAGMA busy_timeout=30000")
    # 🔴 首次构建时 chunk_bge/vec_m3 可能还不存在，先确保建表（幂等），
    # 否则下面的孤儿清理查询会 no such table 直接崩（开放版首次 build 必现）。
    bge_encoder.ensure_tables(conn)
    # 清残：旧版 build 冲库后 chunk_bge 会留下指向不存在 chunk 的孤儿行，
    # 不清理则 stats/doctor 覆盖率 >100% 失真（实测 20154 孤儿 vs 4007 chunk = 503%）
    n_orphan = conn.execute(
        "SELECT COUNT(*) FROM chunk_bge WHERE chunk_id NOT IN (SELECT id FROM chunks)"
    ).fetchone()[0]
    if n_orphan:
        conn.execute("DELETE FROM chunk_bge WHERE chunk_id NOT IN (SELECT id FROM chunks)")
        conn.commit()
        say("[bge] 清理孤儿向量 %d 行" % n_orphan)
    rows = conn.execute(
        "SELECT id, title, heading, content FROM chunks "
        "WHERE id NOT IN (SELECT chunk_id FROM chunk_bge) "
        "ORDER BY id").fetchall()
    if limit:
        rows = rows[:limit]
    if not rows:
        return 0
    done, t0 = 0, time.time()
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        # 🔴 B4：必须与全量 cmd_build 同口径，否则增量补的块和全量建的块
        # 落在两个语义空间，检索时等于半个库是噪声。唯一入口是 build_vector_text。
        vecs = bge_encoder.encode(
            [bge_encoder.build_vector_text(t, h, c) for _, t, h, c in part], batch=batch)
        conn.executemany("INSERT OR REPLACE INTO chunk_bge(chunk_id, vec) VALUES(?,?)",
                         [(cid, v.astype("float32").tobytes())
                          for (cid, _, _, _), v in zip(part, vecs)])
        conn.commit()
        done += len(part)
        if (i // batch) % 5 == 0:
            say("[bge] %d/%d 已用%.0fs" % (done, len(rows), time.time() - t0))
    n = conn.execute("SELECT COUNT(*) FROM chunk_bge").fetchone()[0]
    dim = conn.execute("SELECT dim FROM bge_model WHERE id=1").fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO bge_model(id,dim,n,model,built_at,tie_breaker) "
        "VALUES(1,?,?,?,?,?)",
        (512, n, "BAAI/bge-small-zh",
         datetime.datetime.now().isoformat(timespec="seconds"),
         bge_encoder.vector_fingerprint()))
    conn.commit()
    return done


def _m3_skip_tail(mode, limit):
    """m3 跳过时的「后果说明」，**按档位现算**，保证日志与 build 实际返回值一致。

    🔴 为什么要有这个函数（2026-11-03 修，QA 揪出的前后矛盾）：
       原来固定写「本档不判红」，但企业档（`KB_BACKFILL_M3=1`）缺模型时
       `_cmd_build_body` 随后真的会返回 rc=3 —— 日志说"不判红"、退出码却红，
       是典型的"漂亮报错但自相矛盾"。现在三种结局分别说清：
         企业档 + 全量 + 未降级 → 明确"将返回 rc=3"
         企业档 + 全量 + KB_STRICT_VEC=0 → 明确"降级为告警，rc=0"
         企业档 + 分批（--limit）→ 明确"本次不判红，全量构建才会红"
         auto/其它档 → "本档不判红"
    """
    ent = str(mode).strip().lower() == "1"
    strict = (os.environ.get("KB_STRICT_VEC") or "1").strip().lower() \
        not in ("0", "false", "no", "off")
    if not ent:
        return "（本档 KB_BACKFILL_M3=%s 不判红）" % mode
    if limit:
        return "（企业档 KB_BACKFILL_M3=1 要求 m3 齐；本次是分批构建 --limit，不判红）"
    if strict:
        return "（企业档 KB_BACKFILL_M3=1 要求 m3 齐 → 本次全量构建将以 rc=3 判红）"
    return "（企业档要求 m3 齐，但 KB_STRICT_VEC=0 → 本次降级为告警，rc=0）"


def backfill_m3(conn, limit=0, batch=32):
    """给缺 `vec_m3` 的 chunk 补 bge-m3 向量 —— 增量、只写缺失、无 GPU 只跳过不失败。

    🔴 为什么要进 build（#37，2026-11-03）：
       build 原来只调 `backfill_bge`，而 `bge_encoder.ensure_tables()` 顺手把
       `vec_m3` 也建了出来 —— 于是「表在、行不在」。检索不报错、doctor 覆盖率还是
       100%（它只查 chunk_bge），只是 m3 语义路长期缺一块。这是最难发现的一类退化。

    档位（`KB_BACKFILL_M3`）：
      · `auto`（默认）：有 CUDA **且** 有模型才补；缺任一只打 ⚠️ 跳过，**不失败**。
        无 GPU 的个人机本来就该没有 m3 —— 那是允许状态，不是故障。
      · `1`（企业档）：要求 m3 齐。这里同样只跳过不抛；「全量构建后仍漂移」由
        `_cmd_build_body` 按 `limit==0 且 !ok 且 本值==1` 判 `rc=3`（可见化，不 SystemExit，
        这样 `finally` 仍能释放构建锁）。
      · `0` / `off`：明确不补，`status=skipped`。

    Returns:
        dict：`{status: encoded|noop|skipped|no-model|no-cuda, encoded, lag_before,
        lag_after, reason?, dim?}`。status 永远返回而**从不抛异常**（构建不许因为没 GPU 挂）。
    """
    mode = (os.environ.get("KB_BACKFILL_M3") or "auto").strip().lower()
    lag_before = vec_tables.m3_lag(conn)
    if mode in ("0", "off", "false", "no"):
        return {"status": "skipped", "encoded": 0, "lag_before": lag_before,
                "lag_after": lag_before, "reason": "KB_BACKFILL_M3=%s" % mode}
    if not lag_before:
        return {"status": "noop", "encoded": 0, "lag_before": 0, "lag_after": 0}

    model_dir = os.environ.get("KB_M3_MODEL_DIR") or os.path.join(HERE, "models", "bge-m3")
    tail = _m3_skip_tail(mode, limit)
    if not os.path.isdir(model_dir):
        say("⚠️ m3 模型缺失（%s），跳过 m3 补编码%s" % (model_dir, tail))
        return {"status": "no-model", "encoded": 0, "lag_before": lag_before,
                "lag_after": lag_before, "reason": "model dir not found"}
    try:
        import torch
    except Exception as e:
        say("⚠️ no-cuda 跳过 m3：torch 未安装（%s）%s" % (str(e)[:60], tail))
        return {"status": "no-cuda", "encoded": 0, "lag_before": lag_before,
                "lag_after": lag_before, "reason": "torch missing"}
    if not torch.cuda.is_available():
        say("⚠️ no-cuda 跳过 m3：CUDA 不可用（缺 %d 块）%s" % (lag_before, tail))
        return {"status": "no-cuda", "encoded": 0, "lag_before": lag_before,
                "lag_after": lag_before, "reason": "cuda unavailable"}

    # 🔴 编码口径唯一来源：vector_backfill 的 m3_text / load_m3_gpu / encode_m3_gpu。
    #    这里只做「组装」，一个字都不许另起一套 —— 否则同一段文字落进两个语义空间。
    from vector_backfill import m3_text, load_m3_gpu, encode_m3_gpu
    torch_, tok, mdl = load_m3_gpu(model_dir)
    warmed = {"done": False}

    def encode(texts):
        if not warmed["done"]:      # CUDA 首次分配很慢，先热一次小批
            encode_m3_gpu(torch_, tok, mdl, texts[:min(8, len(texts))])
            warmed["done"] = True
        return encode_m3_gpu(torch_, tok, mdl, texts, batch=batch)

    st = vec_tables.backfill_m3(
        conn, encode, limit=limit, batch=batch, text_fn=m3_text, log=say,
        model="BAAI/bge-m3 (dense 1024, CLS+L2, fp16 cuda)")
    say("[m3] %s：本次 %d 块，缺口 %d → %d"
        % (st["status"], st["encoded"], st["lag_before"], st["lag_after"]))
    return st


def _build_session_id():
    """T12 会话标识：KB_SESSION_ID 环境变量优先（门户/定时任务可固定会话名），
    默认「主机名-pidN」（每次调用一个会话，build_sessions 记录每次调用来历）。"""
    env = os.environ.get("KB_SESSION_ID")
    if env:
        return env[:80]
    import socket
    return "%s-pid%d" % (socket.gethostname(), os.getpid())


def _register_build_task(args):
    """T12：把本次 build 登记进 build_tasks（入队 → claim → 报告排队深度）。

    🔴 降级保护（接线第一原则）：任何异常都只在内部吞掉并返 None——
    队列是可观测性增强，绝不能成为 build 的新故障点。登记失败 = 不登记，
    build 照跑。
    """
    try:
        conn = kb_index.connect()
        try:
            build_task.ensure_build_tasks(conn)
            depth = build_task.queue_depth(conn)
            if depth:
                say("[queue] 前面还有 %d 个排队中的构建任务" % depth)
            tid = build_task.enqueue_build(conn, _build_session_id(), " ".join(sys.argv[1:]))
            build_task.claim_next_build(conn)
            say("[queue] 本次构建已登记为任务 #%s（running；历史/排队深度查 build_tasks 表）" % tid)
            return tid
        finally:
            conn.close()
    except Exception as e:
        say("⚠️ [queue] 队列登记失败，降级为不登记（不影响本次 build）：%s" % str(e)[:80])
        return None


def _finish_build_task(task_id, ok, result):
    """T12：结束任务登记（done/failed）。同样吞异常降级——记账失败不许把
    build 的结果拖成未知。"""
    if task_id is None:
        return
    try:
        conn = kb_index.connect()
        try:
            build_task.finish_build(conn, task_id, ok, result)
        finally:
            conn.close()
    except Exception as e:
        say("⚠️ [queue] 任务结束登记失败（不影响 build 结果）：%s" % str(e)[:80])


def cmd_build(args):
    # 🔴 跨会话互斥（BUG-20261022-19 第 4 层根治）：拿不到锁就明确退出，
    # 绝不静默并发写库。进程被强杀时 OS 自动放锁，不会永久堵死后续任务。
    wait = int(getattr(args, "wait", 0) or 0)
    # T12 追加：队列登记（入队→claim；失败自动降级为不登记，build 照跑）
    task_id = _register_build_task(args)
    try:
        lk = kb_index.BuildLock().acquire(wait=wait)
    except kb_index.BuildLock.Busy as e:
        say("❌ 跳过本次 build：%s" % e)
        say("   （同一时刻只允许一个 build；等它结束再跑，或用 --wait N 排队等待）")
        _finish_build_task(task_id, False, "skipped: build lock busy")
        return 2
    try:
        rc = _cmd_build_body(args)
        _finish_build_task(task_id, rc in (None, 0), "rc=%s" % rc)
        return rc
    except BaseException as e:   # 含 KeyboardInterrupt：Ctrl-C 也要把任务记账成 failed
        _finish_build_task(task_id, False, "%s: %s" % (type(e).__name__, str(e)[:160]))
        raise
    finally:
        lk.release()


def safe_backup(db_path, bak_path):
    """用 SQLite 在线备份 API 生成一致性快照（替代 shutil.copy2）。

    为什么不能用 copy2（2026-10-23 修，行业实践核实）：
    WAL 模式下「一个库」其实是 db + -wal + -shm 三个文件，copy2 只拷主文件，
    且三个文件是不同时刻抓取的 —— 可能拿到「主文件停在旧页、WAL 里还有已提交
    新页」的撕裂快照。恢复时轻则丢最近写入，重则库打不开。也就是说
    **以为有退路，其实没有**，这比不备份更危险。

    Connection.backup() 在 SQLite 引擎内部按页拷贝，走独立读事务，拿到的是
    单一时点的一致视图；不需要停写，也不需要 WAL 三件套对齐。
    紧凑重排（去空闲页/改 page_size）仍交给 db_admin.py backup 的 VACUUM INTO，
    两者分工：这里是「构建前快速回滚点」，那里是「定期离线快照」。
    """
    src = sqlite3.connect(db_path, timeout=30)
    try:
        dst = sqlite3.connect(bak_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def rotate_backups(days=None, same_day=1, keep=None, vdir=None):
    """备份轮转：同一天最多留 same_day 份，跨天最多留 days 天。

    🔴 2026-10-23 作者拍板改语义（原为「按份数保留最近 N 份」）。
    为什么必须改：实测「保留 3 份」根本不等于「保留 3 天」—— 10-23 那天一天内
    跑了 3 次构建，三份备份时间戳只差 11 分钟（14:17 / 14:19 / 14:28），前两份
    docs/chunks 完全相同，结果把 10-22 及更早的历史全滚光了，回滚窗口从「几天」
    缩到「几十分钟」。按份数轮转在频繁构建下等于没有历史。
    现在的规则：
      ① 同一天只留最后 1 份（同日多份对回滚没有增量价值）
      ② 跨天保留最近 7 天
    最坏情况回滚窗口 = 7 天，磁盘峰值 ≈ 7 × 1.3GB。
    环境变量：KB_BACKUP_DAYS（天数，默认 7）；老的 KB_BACKUP_KEEP 仍认，但
    语义已改为「天数」——天数 >= 份数，只会更宽松，不会比以前更激进地删。
    days<=0 = 显式关闭（一份都不删）。
    """
    if keep is not None and days is None:
        days = keep  # 旧调用传的位置参数，当作天数用
    if days is None:
        env = os.environ.get("KB_BACKUP_DAYS")
        if env is None:
            env = os.environ.get("KB_BACKUP_KEEP")  # 兼容旧变量名
        days = 7 if env is None else int(env)
    if days <= 0:
        return 0  # 0 / 负数 = 显式关闭，一个都不删
    if same_day <= 0:
        same_day = 1  # 当天那份（本次构建前的回滚点）永远不能删
    vdir = vdir or os.path.join(HERE, "_versions")
    if not os.path.isdir(vdir):
        return 0
    # 🔴 按「份」分组不是按文件（2026-10-22 修）：一份库 = 主文件 + -shm + -wal
    # 三个文件。按文件计数会让 keep=3 实际只留 1 份库，当场误删了事故前健康态备份。
    groups = {}
    for f in os.listdir(vdir):
        if not f.startswith("kb.db.bak"):
            continue
        base = f
        for sfx in ("-shm", "-wal"):
            if f.endswith(sfx):
                base = f[: -len(sfx)]
                break
        groups.setdefault(base, []).append(f)

    def _day(base):
        """kb.db.bak-YYYYmmdd-HHMMSS → YYYYmmdd（取不到就归到空串，一并当最旧处理）"""
        try:
            return base.rsplit("-", 1)[0].rsplit("-", 1)[-1]
        except Exception:
            return ""

    order = sorted(groups.keys(), reverse=True)  # 文件名含时间戳，字典序 = 时间序（新→旧）
    by_day = {}
    for base in order:
        by_day.setdefault(_day(base), []).append(base)
    day_order = sorted(by_day.keys(), reverse=True)
    stale = []
    # ① 同日超量：每天只留最新的 same_day 份
    for d in day_order:
        stale.extend(by_day[d][same_day:])
    # ② 跨天超期：只保留最近 days 天，更早的整组淘汰
    for d in day_order[days:]:
        stale.extend(by_day[d][:same_day])
    seen = set()
    stale = [b for b in stale if not (b in seen or seen.add(b))]
    n = 0
    for base in stale:
        for f in groups[base]:
            try:
                os.remove(os.path.join(vdir, f))
                n += 1
            except Exception as e:
                say("⚠️ 旧备份清理失败 %s：%s" % (f, str(e)[:60]))
    if stale:
        say("[build] 备份轮转：清理 %d 份（每天最多 %d 份 / 最多留 %d 天，"
            "KB_BACKUP_DAYS 可调）" % (len(stale), same_day, days))
    return len(stale)


def _cmd_build_body(args):
    db = kb_index.DB_PATH
    os.makedirs(os.path.dirname(db), exist_ok=True)
    # 🔴 备份策略（2026-10-23 作者拍板 A2）：默认**不**备份。
    # 为什么改：增量 build 通常只动几十个文档，却要为 1.3GB 全库拷一份；
    # _versions/ 一天涨 3.9GB、累计 4.2GB，占的全是磁盘，换不来任何信息。
    # 现在只在 --full（全量重扫，风险最高那次）或显式 --backup 时备份；
    # 真正的定期快照交给 db_admin.py backup（VACUUM INTO，周一自测已接）。
    want_backup = (getattr(args, "backup", False) or getattr(args, "full", False)) \
        and not getattr(args, "no_backup", False)
    if os.path.exists(db) and want_backup:
        bak = os.path.join(HERE, "_versions", "kb.db.bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        try:
            safe_backup(db, bak)
            say("[build] 已备份（SQLite 在线备份 API）→ %s" % bak)
            rotate_backups()
        except Exception as e:
            # 备份是旁路，失败不该拖垮索引构建；但绝不能静默——显式喊出来。
            say("⚠️ 备份失败（不影响本次构建）：%s: %s" % (type(e).__name__, str(e)[:120]))
    conn = kb_index.connect()
    kb_index.ensure_schema(conn)
    kb_index.guard_chunker_fp(conn)   # T6：分块器指纹不一致 → SystemExit（防混代索引）
    # 🔴 state 说谎自愈（BUG-20261022-19）：ingest_raw_state 篇数与库内 raw
    # 篇数对不上说明 raw 索引被外部冲掉过（如旧版 kb_index build 全表清 docs）。
    # 此时增量会全部 skipped——state 认为都在，库里其实没了。检测到就强制 full。
    # 🔴 阈值必须是 0.5 不是 0.9（2026-10-22 实测教训）：state 记的是「全部尝试过
    # 的提取文件」= 成功入库 + 提取为空的 gap + 失败的，本库健康比例本来就只有
    # 83%（4676 state vs 3887 docs）。用 0.9 会把每次健康构建都误判成说谎、
    # 强制全量——每日运势自动化的增量 build 会变成 40 分钟全量 walk。
    # 冲库后的比例是 <20%（docs 归零或只剩 wiki），与 83% 界限清晰，0.5 安全。
    try:
        row = conn.execute(
            "SELECT value FROM index_state WHERE key=?", (ingest_raw.STATE_KEY,)).fetchone()
        state = json.loads(row[0]) if row else {}
        n_state = len(state) if isinstance(state, dict) else 0
        n_raw = conn.execute(
            "SELECT COUNT(*) FROM docs WHERE category='raw'").fetchone()[0]
        if n_state > 0 and n_raw < n_state * 0.5:
            say("⚠️ 检测到 state 说谎（state %d 篇 vs 库内 %d 篇），强制 full 重扫 raw"
                % (n_state, n_raw))
            args.full = True
    except Exception as e:
        say("⚠️ state 自检失败（按原参数继续）: %s" % str(e)[:80])
    say("[build] 1/4 摄取 raw 正文…")
    st = ingest_raw.run_ingest(limit=args.limit, full=args.full, quiet=False)
    say("[build]   raw 新增%d 更新%d 跳过%d 空%d 失败%d" %
        (st["added"], st["updated"], st["skipped"], st["empty"], st["failed"]))
    # 🔴 wiki/modules/root 同步（BUG-20261022-19）：以前 kb_rag build 只跑
    # ingest_raw，旧版 kb_index build 仍在被自动化调用、会冲掉 raw；现在两边
    # 都走 sync_owned（所有权边界在函数内强制：只清自有品类，raw 不动），
    # kb_rag.py build 成为真正的单一安全入口（raw + wiki 都覆盖）。
    say("[build] 2/4 同步 wiki/modules/root 索引…")
    kb_index.sync_owned(conn, full=False)
    say("[build] wiki/modules/root 同步完成")
    say("[build] 3/4 同步 trigram 字级索引…")
    n_add, n_del = ensure_trigram(conn)
    conn.commit()
    say("[build]   trigram 补 %d 行、清残 %d 行" % (n_add, n_del))
    # T3 元数据自愈（v1.13.5）：write_chunks 已改为写入时带齐 path/title/mtime，
    # 但历史存量（2962 块 / 324 篇）和「docs.mtime 尚未由 stat 补齐」两类仍需回填。
    # 只处理 NULL 行，重跑代价≈0；失败不阻断构建——元数据缺了检索仍能用，只是
    # --since/--path-prefix 会漏，不该因为补元数据把整次构建搞挂。
    try:
        mt = kb_index.backfill_chunk_meta(conn)
        say("[build]   元数据回填：docs.mtime 补 %d 篇，chunks 待补 %d → 剩余 %d"
            % (mt["docs_mtime_filled"], mt["chunks_null_before"], mt["chunks_null_left"]))
    except Exception as e:
        say("⚠️ 元数据回填失败（不阻断构建）：%s" % str(e)[:110])
    m3_st = {"status": "skipped", "encoded": 0, "lag_before": 0, "lag_after": 0}
    if not args.skip_vec:
        say("[build] 4/4 补向量：BGE → m3 → 四表断言…")
        n = backfill_bge(conn, limit=args.limit)
        say("[build]   BGE 新编码 %d 块" % n)
        # 🔴 #37：m3 与 bge 同批补。无 GPU 时 status=no-cuda 只打 ⚠️、**不失败**
        #    （个人档的 m3 缺失是允许状态，不该把日常构建搞红）。
        m3_st = backfill_m3(conn, limit=args.limit)
        say("[build]   m3 %s（缺口 %d → %d）"
            % (m3_st["status"], m3_st["lag_before"], m3_st["lag_after"]))
    else:
        say("[build] 4/4 --skip-vec，跳过向量")
    # 🔴 四表断言（与补写同一口径）：缺 id 集合为空**且四张表都存在**才算一致
    rep = vec_tables.diff_report(conn)
    say("[build]   四表：chunks=%d bge=%d trigram=%d m3=%d ｜ 缺 bge=%d m3=%d tri=%d"
        % (rep["counts"]["chunks"], rep["counts"]["chunk_bge"],
           rep["counts"]["chunks_trigram"], rep["counts"]["vec_m3"],
           rep["lag_bge"], rep["lag_m3"], rep["lag_tri"]))
    if not rep.get("tables_ok", True):
        # 表缺失 ≠ 一致：全新库 + --skip-vec 时一张都没建，lag 全 0 但根本没数据可比。
        # 说「未齐 / 跳过判定」，不说「一致 ✓」（QA 2026-11-03 揪出的误导绿灯）。
        say("⚠️ 四表未齐（缺表：%s），跳过一致性判定"
            % "、".join(rep.get("absent_tables") or []))
    consistency = {
        "ts": rep["ts"],
        "ok": rep["ok"],
        "counts": rep["counts"],
        "missing": rep["missing"],
        "lag": {"bge": rep["lag_bge"], "m3": rep["lag_m3"], "tri": rep["lag_tri"]},
        "m3_status": m3_st["status"],
    }
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('m3_lag',?)",
                 (str(rep["lag_m3"]),))
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('vec_consistency',?)",
                 (json.dumps(consistency, ensure_ascii=False),))
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('rag_last_build',?)",
                 (datetime.datetime.now().isoformat(timespec="seconds"),))
    conn.commit()

    # ---- 漂移判红（按档位断言，见 backfill_m3 注释）----
    # 只有「全量构建（limit==0）+ 四表仍漂 + 本档要求 m3 齐（KB_BACKFILL_M3=1）」
    # 才返回 3；--limit 分批构建与 auto/0 档只记录 m3_lag/vec_consistency，不判红 ——
    # 否则个人档（无 GPU，m3 天然不全）每次 build 都会红，红灯就失去意义。
    # 🔴 `--skip-vec` 永不判红（2026-11-03，T04 修）：显式跳过了补写，四表必然漂，
    #    这是**操作者自己的选择**，报 3 只会让「只同步文本、稍后再补向量」的正常用法
    #    变红。跳过时只告警。
    m3_mode = (os.environ.get("KB_BACKFILL_M3") or "auto").strip().lower()
    strict_vec = (os.environ.get("KB_STRICT_VEC") or "1").strip().lower() \
        not in ("0", "false", "no", "off")
    if not rep.get("tables_ok", True):
        # 与上面那条「四表未齐」呼应：表都没建，谈不上漂移，也谈不上一致。
        say("   → 本次不做一致性判定（表还没建出来，不代表索引有问题）")
    elif rep["ok"]:
        say("[build] 四表一致 ✓")
    elif getattr(args, "skip_vec", False):
        say("⚠️ 四表有缺口（缺 bge=%d 缺 m3=%d 缺 tri=%d）——本次 --skip-vec 显式跳过补写，"
            "按设计不判红" % (rep["lag_bge"], rep["lag_m3"], rep["lag_tri"]))
    elif not args.limit and m3_mode == "1":
        say("🔴 四表漂移：缺 bge=%d 缺 m3=%d 缺 trigram=%d"
            % (rep["lag_bge"], rep["lag_m3"], rep["lag_tri"]))
        if strict_vec:
            say("   → 返回 rc=3（KB_BACKFILL_M3=1 要求 m3 齐；"
                "KB_STRICT_VEC=0 可把这个 rc 降级为告警）")
            conn.close()
            return 3
        say("   ⚠️ KB_STRICT_VEC=0：降级为告警，本次仍视为成功（rc=0）")
    else:
        say("⚠️ 四表仍有缺口（缺 bge=%d 缺 m3=%d 缺 tri=%d）：本次 %s，本档不判红"
            % (rep["lag_bge"], rep["lag_m3"], rep["lag_tri"],
               "分批构建（--limit）" if args.limit else "KB_BACKFILL_M3=%s" % m3_mode))
    say("[build] 完成。%s" % PRIVACY_NOTE)
    conn.close()
    # 🔴 W1-C-4 机制根治（2026-11-04，生产侧实证后回流）：主连接关闭后再补 m3，避免写锁冲突。
    #    触发条件刻意收紧：全量构建 + 未 --skip-vec + 当时确有缺口。
    #    无 GPU venv 的机器维持原行为（打一行提示，不改 rc）。
    if (not getattr(args, "skip_vec", False)) and not args.limit and rep.get("lag_m3", 0) > 0:
        _st, _detail = auto_backfill_m3()
        if _st == "done":
            say("[build] ✓ 自动补 m3 完成（%s）" % _detail)
        elif _st == "skip":
            say("[build] m3 无缺口（%s）" % _detail)
        elif _st == "nogpu":
            say("[build] ⓘ 未发现 GPU venv（%s），m3 缺口 %d 保留 —— 个人档允许状态，"
                "需要时手工跑 vector_backfill.py" % (_detail, rep["lag_m3"]))
        else:
            say("⚠️ 自动补 m3 失败（%s），缺口 %d 保留；可手工跑 vector_backfill.py"
                % (_detail, rep["lag_m3"]))
    return 0


# ---------- 报表 ----------
def cmd_stats(args):
    conn = kb_index.connect(readonly=True)

    def g(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return -1  # 表还没建（没跑过 build）
    print("docs %d（wiki %d · raw 正文 %d · 其他 %d）" % (
        g("SELECT COUNT(*) FROM docs"),
        g("SELECT COUNT(*) FROM docs WHERE category='wiki'"),
        g("SELECT COUNT(*) FROM docs WHERE category='raw'"),
        g("SELECT COUNT(*) FROM docs WHERE category NOT IN ('wiki','raw')")))
    print("chunks %d ｜ trigram %d ｜ BGE 向量 %d" % (
        g("SELECT COUNT(*) FROM chunks"),
        g("SELECT COUNT(*) FROM chunks_trigram"),
        g("SELECT COUNT(*) FROM chunk_bge")))
    row = conn.execute("SELECT built_at FROM bge_model WHERE id=1").fetchone()
    print("BGE 构建于：%s" % (row[0] if row else "未构建"))
    # 🔴 #37：m3 覆盖率必须和 BGE 并排可见。以前只看 BGE，导致「m3 建表但从不填」
    #    这种漂移在 stats 上完全看不出来。
    try:
        n_m3 = g("SELECT COUNT(*) FROM vec_m3")
        m3row = conn.execute("SELECT built_at FROM m3_model WHERE id=1").fetchone()
        print("vec_m3 %d ｜ m3 构建于 %s" % (n_m3, m3row[0] if m3row else "未构建"))
    except sqlite3.OperationalError:
        print("vec_m3 未建表（尚未跑过 build）")
    conn.close()


def cmd_doctor(args):
    conn = kb_index.connect(readonly=True)

    def g(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            return 0  # 表不存在 = 覆盖率为 0，doctor 会判 🔴
    # 🔴 #37（2026-11-03）：checks 改由 `vec_tables.diff_report` 驱动 —— 与 build
    #    补写**同一口径**（同一个 missing_ids SQL）。以前两处各写一套行数比较，
    #    结果是「孤儿行（chunk 已删、向量还在）让行数看着齐，实际仍有 chunk 没向量」。
    #    现在报的是「真实覆盖 = chunks - 缺 id 数」。
    rep = vec_tables.diff_report(conn)
    cnt = rep["counts"]
    chunks = max(0, cnt["chunks"])
    cover = {
        "bge": max(0, chunks - rep["lag_bge"]),
        "tri": max(0, chunks - rep["lag_tri"]),
        "m3": max(0, chunks - rep["lag_m3"]),
    }
    m3_mode = (os.environ.get("KB_BACKFILL_M3") or "auto").strip().lower()

    print("== doctor ==")
    ok = True

    def line(mark, name, have, want, note=""):
        print("%s %s：%d / %d（%.0f%%）%s"
              % (mark, name, have, want, (have / want * 100) if want else 0, note))

    good_bge = cover["bge"] >= chunks * 0.95
    line("✅" if good_bge else "🔴", "BGE 向量覆盖率", cover["bge"], chunks,
         "" if good_bge else " → kb_rag.py build 补向量（可 --limit 分批）")
    ok = ok and good_bge

    good_tri = cover["tri"] >= chunks * 0.95
    line("✅" if good_tri else "🔴", "字级索引覆盖率", cover["tri"], chunks,
         "" if good_tri else " → kb_rag.py build 会同步 trigram")
    ok = ok and good_tri

    # 🔴 按档位断言：企业档（KB_BACKFILL_M3=1）要求 m3 齐，其余档位只标 ⚠️。
    #    理由：个人档机器本来可能没有 GPU，m3 不全属**允许状态**，判红会让
    #    「红灯」失去信息量。
    if m3_mode == "1":
        good_m3 = chunks > 0 and cover["m3"] >= chunks * 0.95
        line("✅" if good_m3 else "🔴", "m3 覆盖率", cover["m3"], chunks,
             "" if good_m3 else " → 企业档要求 m3 齐：用 GPU venv 跑 build 或 vector_backfill.py")
        ok = ok and good_m3
    else:
        line("⚠️", "m3 覆盖率（本档不要求）", cover["m3"], chunks,
             " → KB_BACKFILL_M3=%s；设 1 才判红" % m3_mode)
    print("m3_lag：%d（缺 m3 的 chunk 数；本档要求 m3 时 >0 即故障）" % rep["lag_m3"])

    row = conn.execute("SELECT value FROM index_state WHERE key='ingest_raw_gaps'").fetchone()
    gaps = json.loads(row[0]) if row else {}
    top_gap = sorted(gaps.items(), key=lambda x: -x[1]["files"])[:6]
    if top_gap:
        print("⚠️ 无正文可提取（已知缺口）：" +
              ", ".join("%s×%d" % (k, v["files"]) for k, v in top_gap))
    qlog = g("SELECT COUNT(*) FROM query_log")
    empty = g("SELECT COUNT(*) FROM query_log WHERE n_results=0")
    if qlog:
        print("查询日志 %d 条，零命中 %d（%.0f%%）——零命中高说明该补料了" %
              (qlog, empty, empty / qlog * 100))
    print(PRIVACY_NOTE)
    conn.close()
    sys.exit(0 if ok else 3)


def cmd_backfill_meta(args):
    """backfill-meta：回填 chunks 的 path/title/mtime（T3 元数据过滤的数据基础）。

    幂等可重跑：只补 NULL 行。build 新增的 chunk 元数据为 NULL，重跑一次即补齐
    （过滤语义里 NULL mtime 不满足 --since、NULL path 不满足 --path-prefix，
    所以「回填」与「build」的先后只影响新文档能否被过滤筛中，不影响正确性）。
    """
    conn = kb_index.connect()
    kb_index.ensure_schema(conn)      # 幂等加列（docs.mtime / chunks.path/title/mtime）
    st = kb_index.backfill_chunk_meta(conn)
    conn.close()
    print("[backfill-meta] docs.mtime 补齐 %d 篇；chunks 原有待补 %d 行" %
          (st["docs_mtime_filled"], st["chunks_null_before"]))
    print("[backfill-meta] 回填后 chunks.path/title/mtime 为 NULL 的剩余 %d 行"
          "（其中孤儿 chunk %d 行——doc 已不在库内，正常应为 0）" %
          (st["chunks_null_left"], st["orphan_chunks"]))
    print("[backfill-meta] 全库 chunks 共 %d 行。%s" % (st["total_chunks"], PRIVACY_NOTE))
    return 0 if st["chunks_null_left"] == 0 else 3


# ---------- CLI ----------
def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


def cmd_search(args):
    conn = kb_index.connect()
    try:
        res, note = search_unified(conn, args.query, topk=args.topk, mode=args.mode,
                                   rerank=not args.no_rerank,
                                   since=args.since, path_prefix=args.path_prefix,
                                   rewrite=getattr(args, "rewrite", None))
    except ValueError as e:  # --since 格式错（argparse type 之外的双保险）
        conn.close()
        print("参数错误：%s" % e)
        return 2
    conn.close()
    if args.json:
        # --since 在 argparse 层已转 epoch 秒，回显时 round-trip 回日期字符串
        # （epoch 是当天 00:00 本地时间，转换精确），门户/报告读起来才是人话
        since_txt = (datetime.datetime.fromtimestamp(args.since).strftime("%Y-%m-%d")
                     if isinstance(args.since, int) else args.since)
        emit_json({"query": args.query, "mode": args.mode,
                   "filters": {"since": since_txt, "path_prefix": args.path_prefix},
                   "latency_ms": 0, "n_results": len(res),
                   "note": note,
                   "results": [{"rank": i, "score": r["score"], "path": r["path"],
                                "title": r.get("title", ""), "category": r.get("category", ""),
                                "snippet": r.get("snippet", ""), "matched": r.get("matched", []),
                                "coverage": r.get("coverage"),
                                "source": r.get("source", ""),
                                "rerank_score": r.get("rerank_score"),
                                "reranked": bool(r.get("reranked"))} for i, r in enumerate(res, 1)]})
        return
    if not res:
        print("库内未检索到相关内容" + ("（%s）" % note if note else ""))
        return
    for i, r in enumerate(res, 1):
        print("%d. [%.4f] %s%s" % (i, r["score"], r["path"],
                                   " [rerank %.2f]" % r["rerank_score"]
                                   if r.get("rerank_score") is not None else ""))
        print("   %s ｜ %s" % (r.get("title", ""), r.get("source", "")))
        print("   %s" % (r.get("snippet", "") or "")[:110])
    if note:
        print("（%s）" % note)


def cmd_ask(args):
    conn = kb_index.connect()
    res, note = search_unified(conn, args.query, topk=args.topk, mode=args.mode,
                               rerank=not args.no_rerank,
                               rewrite=getattr(args, "rewrite", None))
    ctx, cites = build_context(args.query, res)
    conn.close()
    if args.json:
        emit_json({"question": args.query, "mode": args.mode, "note": note,
                   "n_results": len(res), "context": ctx, "citations": cites})
        return
    print(ctx)
    if note:
        print("（%s）" % note)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="三链路统一检索（FTS5 ⊕ BGE ⊕ 字级 substring）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--limit", type=int, default=0, help="本次只处理前 N 个待处理文件/块")
    p.add_argument("--full", action="store_true", help="全量重扫（hash 相同仍跳过）")
    p.add_argument("--skip-vec", action="store_true", help="跳过 BGE 向量补齐")
    p.add_argument("--backup", action="store_true",
                   help="构建前用 SQLite 在线备份 API 生成回滚点（默认不备份；--full 自动开）")
    p.add_argument("--no-backup", action="store_true",
                   help="强制不备份（即使 --full/--backup；保留兼容）")
    p.add_argument("--wait", type=int, default=0,
                   help="拿不到构建锁时排队等待的秒数；0=立即跳过（默认）。"
                        "定时任务建议 --wait 600，避免与手动 build 撞车")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("search")
    p.add_argument("--query", required=True)
    p.add_argument("--mode", default="fused", choices=["fused", "kw", "vec", "substr"])
    p.add_argument("--topk", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-rerank", action="store_true",
                   help="关闭 Cross-Encoder 精排（默认开；仅 fused 模式生效，短查询自动跳过）")
    # #39 改写档位（默认 None = 读 KB_REWRITE，再回落 DEFAULT_LEVEL=off；off 与改造前逐位一致）
    p.add_argument("--rewrite", default=None, choices=["off", "l0", "l1", "l2"],
                   help="查询改写档位：off=不改写（=改造前行为）/ l0=确定性拆分（默认）/ "
                        "l1=+去口语 / l2=+HyDE（需 KB_HYDE=1 且有 KB_HYDE_CMD）")
    # T3 元数据过滤：--since 用 type 在解析期校验日期格式（用法错误立刻可见）；
    # 过滤在 search_unified 内融合后、rerank 前执行（见该函数注释）
    p.add_argument("--since", type=_since_arg, default=None, metavar="YYYY-MM-DD",
                   help="只返 mtime ≥ 该日期 00:00 的文档（mtime 来自 backfill-meta 回填）")
    p.add_argument("--path-prefix", dest="path_prefix", default=None, metavar="PREFIX",
                   help="只返 path 以该前缀开头的文档（如 wiki/技术）")
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("ask")
    p.add_argument("--query", required=True)
    p.add_argument("--mode", default="fused", choices=["fused", "kw", "vec", "substr"])
    p.add_argument("--topk", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-rerank", action="store_true",
                   help="关闭 Cross-Encoder 精排（默认开；仅 fused 模式生效，短查询自动跳过）")
    p.add_argument("--rewrite", default=None, choices=["off", "l0", "l1", "l2"],
                   help="查询改写档位（同 search；默认读 KB_REWRITE，再回落 off）")
    p.set_defaults(func=cmd_ask)
    p = sub.add_parser("stats")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)
    # T3 元数据过滤：回填 chunks.path/title/mtime（追加区，只新增不改动既有子命令）
    p = sub.add_parser("backfill-meta",
                       help="回填 chunks 的 path/title/mtime（元数据过滤的数据基础，幂等可重跑）")
    p.set_defaults(func=cmd_backfill_meta)
    args = ap.parse_args()
    rc = args.func(args)
    sys.exit(rc or 0)  # build 拿不到锁返回 2：让定时任务/脚本看得见"这次没跑成"


if __name__ == "__main__":
    main()
