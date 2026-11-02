#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_index — 知识库 SQLite 索引与混合检索模块
============================================

🔴 已废用为日常命令（BUG-20261022-19：无品类所有权边界，会冲掉 raw 索引）。
日常构建/检索请用 kb_rag.py；本文件保留为关键词路原版（评测基线）与
kb_rag 的进程内依赖。下方用法仅面向评测基线与进程内调用方。

背景：参考《微信为什么用 SQLite 存聊天记录》的架构思路，给知识库补一个
「程序化检索层」。之前知识库检索全靠 LLM 读 index.md 再 Glob/Grep 找文件，
没有倒排索引、没有向量、没有混合检索——本模块补上这三块。

三种检索模式（对应《AI-Agent小白实战详解》第三、四章）：
  fts     — FTS5 关键词检索（类 BM25 打分，SQLite 内置，倒排索引）
  tfidf   — TF-IDF 向量检索（语义近似，纯 Python 实现，零外部依赖）
  hybrid  — 混合检索：FTS5 + TF-IDF 双路召回，RRF（倒数排名融合）合并

中文分词策略：jieba 优先；未安装则降级字符 bigram（相邻两字切分），
bigram 是中文 IR 在无分词器下的经典兜底，同时保留英文/数字整词。

数据文件：state/kb.db（单文件，可直接拷贝备份，ACID 事务）

用法：
  python kb_index.py build [--full]          构建/增量更新索引（默认增量）
  python kb_index.py search --query "混合检索" --mode hybrid --topk 5
  python kb_index.py stats                   索引统计
"""

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from _garbage_filter import filter_chunk_pairs  # 重复块治理（A，2026-11-02）：split_chunks 内部调用
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

# ============ 参数区（换项目只改这里） ============
KB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HERE = os.path.dirname(os.path.abspath(__file__))
# 环境变量 KB_DB_PATH 可覆盖（消融实验用：同一个方程不同索引文件互不污染）
DB_PATH = os.environ.get("KB_DB_PATH") or os.path.join(_HERE, "state", "kb.db")
# 环境变量 KB_TOKENIZER=auto|jieba|jieba_search|bigram，默认 auto（有 jieba 就用）
TOKENIZER = (os.environ.get("KB_TOKENIZER") or "auto").lower()
# FTS5 字段加权：标题命中比正文命中有信权值高，bm25(docs_fts, W, 1.0)
FTS_TITLE_W = float(os.environ.get("KB_FTS_W") or 1.0)
# 索引目标：(分类, 目录, glob 模式)；recursive=True 时递归
INDEX_TARGETS = [
    ("wiki", os.path.join(KB_ROOT, "wiki"), "*.md", True),
    # modules 分两条：顶层总览 + 各模块自己的 README（一层）。
    # 🔴 **绝不能把 modules 改成 recursive=True**：递归会扫进 node_modules 里 497 个
    # 第三方包 README（2026-10-23 实测：modules 下共 560 个 README，排掉 node_modules/
    # _versions 后 63 个，其中一层 26 个才是模块说明本体）。用 `*/README.md` 精确锁一层。
    # 为什么要收这 26 篇：它们是「能力怎么用」的**权威说明**，落在索引外会导致
    # 「wiki 合成页过时、README 早更新了，检索却只命中过时 wiki」——2026-10-23 视频下载
    # 实例：wiki 说「视频流被风控压住」，README v1.4.0 说「已打通」，检索只命中前者。
    ("modules", os.path.join(KB_ROOT, "modules"), "README.md", False),
    ("modules", os.path.join(KB_ROOT, "modules"), "*/README.md", False),
    # modules 一层内的「说明书类」文档（2026-10-23 扩围）。为什么只收这几种：
    #   README.md  = 模块怎么用（权威说明，落索引外会导致「wiki 合成页过时、
    #                README 早更新了，检索却只命中过时 wiki」）
    #   AGENT.md   = 该模块的 AI Agent 入口约定
    #   INDEX.md   = 全库 Bug 台账总表（排障第一入口，「有没有复返」先查它）
    #   ROADMAP.md = 路线图（阶段规划与优先级真相源）
    #   AUTOMATION_PROMPT.md = 定时任务 prompt 原文
    # **刻意排除**：CHANGELOG.md / HANDOFF.md（时间快照与流水，改一次就变一次，
    # 进索引等于制造旧版重复 + 稀释排序）、bug记录/ 下 21 篇单 bug 台账（由
    # INDEX.md 总表代表）、rea/ 第三方开源项目自带治理文件。
    ("modules", os.path.join(KB_ROOT, "modules"), "*/*.md", False,
     {"README.md", "AGENT.md", "INDEX.md", "ROADMAP.md", "AUTOMATION_PROMPT.md"}),
    # modules 二层子项目（2026-10-23 作者拍板纳入）。为什么用「子目录白名单」
    # 而不是通配：二层 glob `*/*/*.md` 全开会命中 157 篇，其中 rea/docs/ 58 篇
    # 是第三方开源项目自带文档、知识沉淀/reports/ 50+ 篇是 sediment 流水快照、
    # 资产巡检/reports/ 8 篇是时间戳快照——全收等于往检索库灌垃圾。
    # 只收「后端开发/示例同事面试项目/」3 篇：RBAC 权限体系与软删除方案是
    # **与业务无关的通用可复用方案**（作者 2026-10-23 拍板纳入索引），
    # README 含公开 GitHub 仓库地址与本地路径，属求职作品集说明。
    ("modules", os.path.join(KB_ROOT, "modules"), "*/*/*.md", False,
     {"README.md", "RBAC权限体系-可复用模板.md", "软删除与唯一索引冲突-通用方案.md"},
     {"示例同事面试项目"}),
    ("root", KB_ROOT, "SKILL.md", False),
    ("root", KB_ROOT, "index.md", False),
    # 技能区：SKILL.md 是「这个能力存在、怎么用」的唯一说明。不索引会导致
    # **能力封装成技能后反而检索不到**——2026-10-23 实证：video-channels-capture
    # 明明在 .workbuddy/skills/ 里，kb_rag 搜「视频号 下载」却搜不出它，
    # 结果先去 WebFetch 硬抓、撞两次 401 才想起查库（准则 §16.3.3 结论三）。
    # 三条 pattern 分工（2026-10-23 实测校准）：
    #   ① "SKILL.md" 递归 → 各技能入口说明（13 篇）
    #   ② "*/" 直属 → 技能目录直属的参考文档（tdd/tests.md、triage/AGENT-BRIEF.md、
    #      setup-matt-pocock-skills/*.md 等，权威细节常在这里，不收会「只命中入口摘要」）
    #   ③ "/*/*" 二层 → 技能自带的子目录文档（ai-coding-collab/prompts/、examples/、
    #      onetake/references/）。**深度恰好卡在 2 是刻意的**：onetake/cases/ 下
    #      12 篇同质英文案例 README 位于深度 3，自动排除——它们是「某个片子怎么做」
    #      的同构记录，全量收进来只会稀释中文查询排序。
    ("skills", os.path.join(KB_ROOT, ".workbuddy", "skills"), "SKILL.md", True),
    ("skills", os.path.join(KB_ROOT, ".workbuddy", "skills"), "*/*.md", False),
    ("skills", os.path.join(KB_ROOT, ".workbuddy", "skills"), "*/*/*.md", False),
]
# 🔴 所有权边界（BUG-20261022-19，raw docs 3885→0 事故）：本模块只负责
# INDEX_TARGETS 扫到的品类（modules/root/wiki）；raw 正文索引归 ingest_raw.py
# 所有——它的摄取、清理、存活判断都由那边做，这里 build/清理一律不碰。
OWNED_CATEGORIES = sorted({c for c, _, _, _, *_ in INDEX_TARGETS})
# RRF 融合常数 k：score(d) = Σ w_i / (k + rank_i)。
# k=60 来自 RRF 原论文的百万级网页语料；小语料 + 单答案场景 k 应小一个数量级
# （Qdrant 官方调优实测：每查询相关文档≈1 的数据集最佳 k=2~5，见
#  https://qdrant.tech/articles/how-to-tune-hybrid-search ——注意它 0-based，
#  其 k=61 才等于本处口径的 k=60）。
# 现按环境变量 KB_RRF_K 可配。
# 🔴 2026-10-26 默认值从 **60 改为 2**（作者拍板）。
# 依据：本库 100 条 judged set（全部 rerank=False），**在 docs 4044 / chunks 43760 那份索引上测得**：
#     k=1  R@5 90.0% · R@20 96.0% · MRR 0.7215
#     k=2  R@5 89.0% · R@20 97.0% · MRR 0.7380   ← 取它
#     k=20 R@5 85.0% · R@20 96.0% · MRR 0.7069
#     k=60 R@5 82.0% · R@20 96.0% · MRR 0.7023   ← 旧默认
# ⇒ 当时 60→2 拿到 R@5 **+7pp**、MRR +0.036。
#
# 🔴 **2026-10-26 索引重建后（docs 4152 / chunks 44479）同条件复测，基线自己动了**：
#     k=2  88.0%   vs   k=60  85.0%   ⇒ 差 **+3.0pp**（MRR +0.0179）
#     ⇒ **优势收窄到 3pp，方向不变，k=2 仍然更好。对外引用请用「+3pp @当前索引」，不要再用 +7pp。**
#     原因不是代码退化：judged set 的 100 条 gt 没变，但索引新增了 108 篇 / 719 块，
#     k=60 这一档自己也从 82.0% 涨到了 85.0%（B5 标题分词生效 + 新文档都在起作用）。
# 🔴 **方法论**：任何「以某个基线为门槛」或「与某个基线逐位一致」的判据，
#    在基线被改变（换索引 / 换默认值 / 改分词）时必须**同批复核**，否则判据会静默失真。
#    这个错我在同一天里犯了两次。
# 🔴 为什么不取 k=1（R@5 更高的那个）：k=1 只多 1pp，但 MRR(0.7215<0.7380) 与
#    R@20(96%<97%) 都更低；且 1/(1+rank) 的权重过于极端（第 1 名 0.5 vs 第 2 名 0.333），
#    在 100 条集上有过拟合风险。k=2 也正是 Qdrant 实测给出的先验值，外部与本地一致。
# 环境变量仍可覆盖（临时对比/回退不改代码）；本常数只决定「不设变量时的默认档」。
RRF_K_DEFAULT = 2


def rrf_k() -> int:
    """运行时解析 RRF 融合常数 k：读环境变量 KB_RRF_K，未设/非法时回落 RRF_K_DEFAULT。

    为什么是函数而不是常量：模块级 `RRF_K = 60` 在**导入时**绑定，任何下游的
    `RRF_K = kb_index.RRF_K` 复制式引用都会拿到旧值——扫 k 时五档会跑出完全相同
    的数，等于白测。改成调用时解析，进程内改 os.environ 立即生效。
    """
    raw = os.environ.get("KB_RRF_K", "").strip()
    if not raw:
        return RRF_K_DEFAULT
    try:
        k = int(raw)
    except ValueError:
        return RRF_K_DEFAULT
    return k if k > 0 else RRF_K_DEFAULT


RRF_K = rrf_k()  # 模块级兼容值（导入时按当前环境求值，老引用方行为不变）
SUMMARY_LEN = 120  # 摘要截断长度
# 分块参数：整篇入库会让长文语义被全文高频词拉平，按标题切块是 P0 修复项
CHUNK_MIN = 80       # 太短的块并入上一块，避免碎片
CHUNK_MAX = 480      # 超长块按长度再切（1200→480：让整块进 512 token 编码窗口，修 D1 截断）
SNAP = "。！？!?；;\n"  # 句末标点集：断句吸附时允许的切点
SNAP_RATIO = 0.85    # 窗口末尾 15% 内找切点（从后往前，切点尽量靠右）
CHUNKER_FP = "v2.1|max=480|snap=0.85|ovl=0|gf=1"   # 🔴 分块器指纹：写进 index_state['chunker_fp']，护栏防混代；gf=1=启用分块阶段垃圾过滤+去重（2026-11-02 重复块治理）
# ============ 参数区结束 ============


# ============ 跨会话构建互斥锁（BUG-20261022-19 第 4 层根治） ============
# 背景：SQLite WAL 是**单写者**模型。两个 WorkBuddy 会话（或「定时任务 + 手动」
# 同时开工）一起 build，轻则 "database is locked" 挂掉，重则旧版索引器把 raw
# 冲光（10-22 事故：raw docs 3885 → 0，恢复花了 1h42m）。
# busy_timeout 只能延长等待，**阻止不了并发语义冲突**，所以必须有互斥。
#
# 为什么用 OS 文件锁而不是「锁文件 + PID 探测」：进程被强杀时 OS 会自动释放
# 文件锁，不会留下永久残留。PID 方案在这种场景会失效——10-22 我就是用
# 「杀所有 python.exe」的焦土命令把自己的 build 一起杀了，PID 文件会烂在那。


def default_lock_path(db_path=None):
    """推导 build 锁文件路径（D2：锁必须**随库**派生，不能全进程共用一把）。

    优先级：
      ① 环境变量 `KB_BUILD_LOCK`（显式指定，压过一切；也是本修复的一键回滚开关）
      ② `<db_path 所在目录>/build.lock`（默认库 → `state/build.lock`，与旧行为逐位一致）

    🔴 为什么必须 per-DB（2026-11-03 修，多租户 bug）：
      旧实现把锁路径写成**模块级常量**，只依赖 `_HERE`，完全不看 `KB_DB_PATH`。
      于是两个租户（`tenantA/kb.db` 与 `tenantB/kb.db`）并发 build 会抢同一把锁：
      一方被 Busy 直接跳过（看似"保护"实为串行化错杀），更糟的是 `_read_meta()`
      会报出**另一个租户**的 PID 与命令行 —— 排查时严重误导（以为是自己的进程卡住）。

    🔴 为什么「调用时解析」而不是导入时绑定：`DB_PATH` 是导入时从 `KB_DB_PATH`
      快照下来的，同一进程内切库（消融实验、单测直连临时库）时，导入时绑定的
      路径永远是旧值。函数化后每把锁都按当下的库现算。

    注意：本实现按**目录**派生，因此同一个目录下的两个不同 .db 仍共用一把锁。
    隔离粒度是「一个库目录 = 一个租户」，多租户请一租户一目录（或显式设
    `KB_BUILD_LOCK`）；这是刻意的取舍 —— 同目录并发写同一个 SQLite 文件本就该串行。
    """
    env = os.environ.get("KB_BUILD_LOCK")
    if env:
        return env
    base = db_path or DB_PATH
    return os.path.join(os.path.dirname(os.path.abspath(base)), "build.lock")


# 兼容别名：历史上外部代码/文档直接引用 `kb_index.LOCK_PATH`（导入时快照值）。
# 新代码请用 `default_lock_path()` —— 它随 KB_DB_PATH / KB_BUILD_LOCK 在调用时解析。
LOCK_PATH = default_lock_path()


if os.name == "nt":
    import msvcrt

    def _lk_lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _lk_unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lk_lock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _lk_unlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class BuildLock:
    """build 互斥锁：同一时刻只允许一个 build 进程。

    用法：
        try:
            with BuildLock().acquire_with(wait=600) as lk:
                ...
        except BuildLock.Busy as e:
            print("跳过：%s" % e)   # 不抢、不冲库

    拿不到锁抛 Busy（调用方决定：报错退出 or 排队等待），**绝不静默并发**。
    """

    class Busy(Exception):
        def __init__(self, info):
            super().__init__("另一个 build 正在运行：%s" % info)
            self.info = info

    def __init__(self, path=None):
        # 🔴 调用时解析（不能写 `path or LOCK_PATH`）：LOCK_PATH 是导入时快照，
        # 同一进程内换库（KB_DB_PATH 变化 / 单测直连临时库）会拿到别人的锁文件。
        self.path = path or default_lock_path()
        self.fh = None

    def _read_meta(self):
        """读持有者信息（PID / 启动时间 / 命令），报错时好定位是谁在跑。

        🔴 为什么单独放 .info 文件：Windows 的 msvcrt 锁是**强制锁**，持有期间
        连锁文件本身都读不了（实测报错会退化成「持有者信息不可读」）。信息写
        到旁路文件才能被别人读到——冲突时要能立刻回答「是谁在跑、跑了多久」。
        """
        try:
            with open(self.path + ".info", "r", encoding="utf-8") as f:
                txt = f.read().strip()
            if txt:
                return txt
        except Exception:
            pass
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return f.read().strip() or "(空)"
        except Exception:
            return "(持有者信息不可读：锁被强制持有中)"

    def acquire(self, wait=0, poll=5.0, verbose=True):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.fh = open(self.path, "a+b")  # a+b：不截断，别人读得到持有者信息
        deadline = time.time() + wait
        while True:
            try:
                _lk_lock(self.fh)
                meta = "%d | %s | %s" % (
                    os.getpid(),
                    datetime.now().isoformat(timespec="seconds"),
                    " ".join(sys.argv[:4]),
                )
                self.fh.seek(0)
                self.fh.truncate()
                self.fh.write(meta.encode("utf-8"))
                self.fh.flush()
                # 旁路副本：锁文件本体在 Windows 上被强制锁住时别人读不到
                try:
                    with open(self.path + ".info", "w", encoding="utf-8") as g:
                        g.write(meta)
                except Exception:
                    pass
                return self
            except OSError:
                if time.time() >= deadline:
                    raise BuildLock.Busy(self._read_meta())
                if verbose:
                    print("[lock] build 已被占用（%s），等待 %.0fs 后重试…"
                          % (self._read_meta(), poll), flush=True)
                time.sleep(poll)

    def release(self):
        if self.fh:
            try:
                _lk_unlock(self.fh)
            except Exception:
                pass
            try:
                self.fh.close()
            except Exception:
                pass
            self.fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# 🔴 语义扩展（2026-10-26，作者「经常并发开任务」）：
#    BuildLock 原本只服务 build，现在**所有写 kb.db 的命令共用这一把锁**
#    （build / raw-scan / ingest_raw run）。留个别名，免得后来者以为它只管 build，
#    又给新命令写一套自己的锁——那正是「build↔raw-scan 撞 database is locked」的成因。
WriteLock = BuildLock


@contextlib.contextmanager
def write_lock(wait=120):
    """写库互斥上下文：拿不到锁抛 BuildLock.Busy，由调用方决定「跳过」还是「排队」。

    为什么默认等 120s 而不是 0：写库命令多是后台任务（巡检/自动化），
    排队两分钟比直接失败重跑划算；交互场景传 wait=0 立刻跳过即可。
    """
    lk = BuildLock().acquire(wait=wait)
    try:
        yield lk
    finally:
        lk.release()


# jieba 可选加载
jieba = None
HAS_JIEBA = False
if TOKENIZER not in ("bigram",):  # auto/jieba/jieba_search 才尝试加载
    try:
        import jieba as _jieba
        jieba = _jieba
        HAS_JIEBA = True
        try:
            jieba.setLogLevel(60)  # 静默 "Building prefix dict..." 日志
        except Exception:
            pass
    except ImportError:
        HAS_JIEBA = False
# 最终生效的分词器：auto → 有 jieba 用 jieba，否则 bigram
USE_JIEBA = TOKENIZER in ("jieba", "jieba_search") or (TOKENIZER == "auto" and HAS_JIEBA)
# jieba_search：索引用 cut_for_search 细粒度（递归再切长词），IR 里提升召回的经典手法
JIEBA_MODE = "search" if TOKENIZER == "jieba_search" else "precise"

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[A-Za-z0-9_\-\.]+")


# ============ 数据库连接（统一 PRAGMA 调优） ============
PRAGMAS = [
    "PRAGMA journal_mode=WAL",      # 读写并发 + 少 fsync（微信同款思路）
    "PRAGMA synchronous=NORMAL",    # WAL 下 NORMAL 是安全/性能甜点
    "PRAGMA temp_store=MEMORY",     # 临时数据结构走内存
    "PRAGMA cache_size=-16000",     # 约 16MB 页缓存
    # 🔴 15s 不是拍脑袋：写库进程（build 的一次全量切片 / raw-scan 的 1.6 万文件）
    #    最坏持锁时长按秒级算，5s 会把「等一会儿就好」变成直接报错（2026-10-26 实测撞了 2 次）。
    #    配合 write_lock 的进程级互斥，这个超时只是最后一道兜底。
    "PRAGMA busy_timeout=15000",
]


def connect(db_path=None, readonly=False, check_same_thread=True):
    """统一入口：所有读写的 PRAGMA 在这里一次设好，避免各命令各设一套。
    readonly=True 走 file:...?mode=ro URI，评测脚本并行跑时不会误改索引。

    check_same_thread=False 仅供**长驻进程**（门户）跨请求复用同一个连接用：
    SQLite 的查询计划缓存与页缓存是**连接级**的，每请求新建连接会让每个查询都付
    冷启动成本（实测同一 query：新连接 1.7s vs 复用连接 0.1s）。
    🔴 调用方必须自己保证**串行使用**（门户靠 `_RAG_INFER_LOCK`），否则 sqlite3
    不是线程安全的。默认 True，其他调用方行为完全不变。
    """
    p = db_path or DB_PATH
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True,
                               check_same_thread=check_same_thread)
    else:
        conn = sqlite3.connect(p, check_same_thread=check_same_thread)
    if not readonly:
        for pr in PRAGMAS:
            try:
                conn.execute(pr)
            except sqlite3.OperationalError:
                pass
    return conn


def tokenize(text):
    """中英文混合分词：英文/数字整词（小写）+ 中文 jieba 或 bigram。"""
    text = text.lower()
    tokens = []
    # 英文与数字整词
    for m in WORD_RE.finditer(text):
        w = m.group(0).strip("-._")
        if len(w) >= 2:
            tokens.append(w)
    # 中文
    for m in CJK_RE.finditer(text):
        run = m.group(0)
        if USE_JIEBA:
            cut = jieba.lcut_for_search(run) if JIEBA_MODE == "search" else jieba.lcut(run)
            tokens.extend(t for t in cut if t.strip())
        else:
            # bigram 兜底：单字 run 保留单字，多字 run 全部相邻二字
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def tokens_to_fts_text(text):
    """把原文转成空格分隔的分词结果，供 FTS5 unicode61 tokenizer 使用。"""
    return " ".join(tokenize(text))


def fts_escape(query):
    """转义 FTS5 MATCH 特殊字符，把用户 query 变成安全的 OR 查询。"""
    raw = tokenize(query)
    terms = [t.replace('"', "") for t in raw if t]
    if not terms:
        return None
    return " OR ".join('"%s"' % t for t in terms)


HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def split_chunks(title, content):
    """按 Markdown 标题层级切块，每块带上它的标题路径（如 `三、评测 > 4.1 指标`）。

    为什么切：整篇入库时，一篇 2 万字的文档在倒排里是一整袋词，bm25 打分被全文高频词
    拉平，具体某段讲什么都区分不出来；也没法做「这句话来自哪一段」的溯源。
    切完之后：小 Fritz 检索粒度到段落，命中代码块/表格/某节 grosso。
    """
    blocks, cur_head, buf = [], [], []
    for line in content.splitlines():
        m = HEAD_RE.match(line.strip())
        if m:
            if buf:
                blocks.append((" > ".join(cur_head) or title, "\n".join(buf).strip()))
            buf = [line]
            lvl = len(m.group(1))
            cur_head = cur_head[:lvl - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    if buf:
        blocks.append((" > ".join(cur_head) or title, "\n".join(buf).strip()))

    # 🔴 重复块治理（A，2026-11-02）：分块阶段丢垃圾块 + 同文档去重。
    # 在「标题切块」之后、「短块合并」之前做——避免合并把真实短块连坐进相邻的
    # 垃圾长块一起被误杀（实测：纯 csv 长块占 ~80% 行时，合并后的整块被判垃圾会
    # 丢掉其中夹杂的真实短块）。先过滤去重，剩下的真实短块再安心合并进相邻真实块。
    blocks = filter_chunk_pairs(blocks)

    merged = []
    for h, t in blocks:
        if not t:
            continue
        if len(t) < CHUNK_MIN and merged:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n" + t)
        else:
            merged.append((h, t))
    out = []
    for h, t in merged:
        if len(t) <= CHUNK_MAX:
            out.append((h, t))
            continue
        # 🔴 断句吸附（§3.4 规格，照抄）：CHUNK_MAX=480 后尾部硬劈会把句子劈成
        # 两半。改为在窗口末尾 15% 内从后往前找句末标点（SNAP）当切点，
        # overlap=0（i=end），切点尽量靠右。吸附只在需要切（end < n）时做，
        # 块尾就是文档尾时不向前吸，否则最后一块被无谓截短。
        i, n = 0, len(t)
        while i < n:
            end = min(i + CHUNK_MAX, n)
            if end < n:
                lo = i + int(CHUNK_MAX * SNAP_RATIO)
                for j in range(end - 1, lo - 1, -1):
                    if t[j] in SNAP:
                        end = j + 1
                        break
            if end <= i:                        # 🔴 死循环兜底，绝不可删
                end = min(i + CHUNK_MAX, n)
            piece = t[i:end]
            if piece.strip():
                out.append((h, piece))
            elif out:
                # 🔴 纯空白块守卫（T01 实测 A+ 会切出 1 个纯空白块）：不产出空块，
                # 把纯空白并入前一块（内容不丢，也不新增无意义的空 chunk）。
                # 首块不可能是纯空白：merged 里的块在构造时已 .strip()，t[0] 非空白。
                out[-1] = (out[-1][0], out[-1][1] + piece)
            if end >= n:
                break
            i = end                              # overlap = 0
    return [(i, h, t) for i, (h, t) in enumerate(out, 1)]


def sha1_of(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _cn_ratio(s):
    """中文字符占比 —— 判断一行是不是「人话」而不是代码/路径。"""
    s = s.strip()
    if not s:
        return 0.0
    cn = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cn / len(s)


def _quote_lead(lines):
    """从文档开头的引用块里找「导语句」当摘要。

    为什么需要：纯表格/纯清单类文档（如 bug记录/INDEX.md 是整页表格、
    agent资产模板/README.md 是五件套表格）没有普通正文段落，摘要会一路
    落到表格后面的命令行（`PY="C:/..."`）或空。它们的导语写在 `>` 引用块里。

    排除 `Sources:` / `Raw:` / `来源:` 这类**元数据标注**（它们是出处不是摘要）
    —— 实测 411/559 篇的引用块首行是 Sources，全取会把摘要全变成来源行。
    """
    for line in lines[:12]:
        s = line.strip()
        if not s.startswith(">"):
            continue
        t = s.lstrip(">").strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith(("sources:", "source:", "raw:", "来源:", "出处:", "注:", "说明:")):
            continue
        if _cn_ratio(t) >= 0.5 and len(t) >= 12:
            return t[:SUMMARY_LEN]
    return None


def read_md_meta(path):
    """提取标题/摘要/正文。标题取第一个 # 标题，否则文件名。

    摘要提取的坑（2026-10-23 修，作者授权）：
    原来的「跳过 # | > - * ` { 开头行」规则**漏掉了代码块围栏内部**，
    于是摘要会抓到代码首行 —— 实证：`RBAC权限体系-可复用模板.md` 摘要是
    `<dependency>`、`软删除与唯一索引冲突-通用方案.md` 是 `// 校验：只查未删除记录`、
    `bug记录/INDEX.md` 是 `PY="<某绝对路径>python.exe"`。这类摘要在检索结果里
    完全不可读（只影响展示，不影响排序与命中）。

    修法四条（干跑验证：559 篇中 243 篇摘要变好，**无新增空摘要**）：
      ① 识别 ``` / ~~~ 围栏，**整块跳过**（配对翻转 in_fence）
      ② 跳过图片行 `![alt](path)` —— 否则摘要变成图片路径
      ③ 正文无候选时，回退到**引用块导语句**（纯表格类文档的摘要在这）
      ④ 仍无候选才回退旧逻辑，保证摘要**绝不为空**
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    title = os.path.splitext(os.path.basename(path))[0]
    summary = ""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            title = s.lstrip("#").strip() or title
            break
    # 摘要：第一个非标题、非元数据、非表格、非代码块、非图片的正文行
    in_fence = False
    fence_marker = ""
    for line in lines:
        s = line.strip()
        # 代码围栏：``` 或 ~~~，进入后整块跳过，到配对围栏才退出
        if s.startswith("```") or s.startswith("~~~"):
            mk = s[:3]
            if not in_fence:
                in_fence, fence_marker = True, mk
            elif mk == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        if not s or s.startswith(("#", "|", ">", "-", "*", "`", "{")) or s.startswith("---"):
            continue
        if s.startswith("!["):  # 图片语法 ![alt](path)
            continue
        summary = s[:SUMMARY_LEN]
        break
    if not summary:
        # 纯表格/纯清单类文档：摘要取引用块里的导语句
        summary = _quote_lead(lines) or ""
    if not summary:
        # 兜底：整篇都是代码/表格时，宁可给旧逻辑的首行也不要空摘要
        for line in lines:
            s = line.strip()
            if not s or s.startswith(("#", "|", ">", "-", "*", "`", "{")) or s.startswith("---"):
                continue
            summary = s[:SUMMARY_LEN]
            break
    return title, summary, content


def scan_files():
    """按 INDEX_TARGETS 扫描待索引文件，返回 [{category, path, title, summary, content, hash}]

    INDEX_TARGETS 每项 = (分类, 目录, glob 模式, recursive[, 文件名白名单[, 子目录白名单]])。
    第 5 列可选：只有文件名在该白名单里的文件才收录 —— 用来在「同目录一堆 md」
    的场景里精准挑说明书，排除 CHANGELOG/HANDOFF 这类时间快照与流水
    （改了就读旧版、且会稀释排序）。
    第 6 列可选：只有**直接子目录名**在该白名单里的文件才收录（配合
    pattern="*/*.md" 这类二层 glob 使用）—— 用来在「一个模块下有很多子项目」
    的场景里只收指定的子项目，避免把第三方项目自带文档（rea/docs/ 58 篇）、
    自动生成的流水快照（知识沉淀/reports/ 50+ 篇 sediment）一并灌进索引。
    """
    import glob as globmod
    docs = []
    seen = set()
    for target in INDEX_TARGETS:
        category, base, pattern, recursive = target[:4]
        name_filter = target[4] if len(target) > 4 else None
        dir_filter = target[5] if len(target) > 5 else None
        pat = os.path.join(base, "**", pattern) if recursive else os.path.join(base, pattern)
        for fp in globmod.glob(pat, recursive=recursive):
            if not os.path.isfile(fp):
                continue
            if name_filter and os.path.basename(fp) not in name_filter:
                continue
            if dir_filter:
                # dir_filter 匹配的是「base 下的**直接子目录**」，
                # 即 rel 路径的第 2 段（parts[1]）。parts[0] 是模块目录本身
                # （如「后端开发」），parts[1] 才是模块下的子项目
                # （如「示例同事面试项目」）。配合 pattern="*/*/*.md" 使用。
                rel = os.path.relpath(fp, base).replace("\\", "/")
                parts = rel.split("/")
                if len(parts) < 3 or parts[1] not in dir_filter:
                    continue
            rp = os.path.relpath(fp, KB_ROOT).replace("\\", "/")
            if rp in seen:
                continue
            # 版本快照是旧副本：索引进来只会与当前版重复并稀释排序
            # （2026-10-23 实证：搜「AI 写代码 难度分档」第二名是 wiki/_versions 里的旧准则；
            #   skills 加版本管理后同样出现 _versions/code/SKILL.md 占位）
            if "/_versions/" in rp:
                continue
            seen.add(rp)
            try:
                title, summary, content = read_md_meta(fp)
            except Exception as e:
                print("  [warn] 读取失败 %s: %s" % (rp, e))
                continue
            docs.append({
                "category": category,
                "path": rp,
                "title": title,
                "summary": summary,
                "content": content,
                "hash": sha1_of(content),
            })
    return docs


def ensure_schema(conn):
    # page_size 只对新库（尚无数据页）生效；老库要改请用 db_admin.py optimize-vacuum
    try:
        if conn.execute("PRAGMA page_size").fetchone()[0] < 8192:
            conn.execute("PRAGMA page_size=8192")
    except sqlite3.OperationalError:
        pass
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS docs(
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        category TEXT,
        title TEXT,
        summary TEXT,
        content TEXT,
        content_hash TEXT,
        word_count INTEGER,
        mtime INTEGER,                      -- T3 元数据过滤：源文件修改时间 epoch 秒
        indexed_at TEXT
    );
    -- 普通 FTS5 表（内容自存，非 external content）：DELETE 直接可用，
    -- 代价是索引文件略大，129 篇规模下完全可接受。
    CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
        title, tokens, tokenize='unicode61'
    );
    CREATE TABLE IF NOT EXISTS doc_terms(
        doc_id INTEGER NOT NULL,
        term TEXT NOT NULL,
        tf INTEGER NOT NULL
    );
    -- ⚠ 旧索引 idx_doc_terms_term 已在 v1.2.0 删除：它是 idx_doc_terms_tdt 的
    --   最左前缀重复。EXPLAIN QUERY PLAN 实测规划器只用 tdt（覆盖索引），
    --   旧索引白白占 7% 空间。DROP 后体积 -0.87MB、同查询反而快 21%。
    --   「加了索引不一定被用」——要用 EXPLAIN 验证，不能靠感觉堆索引。
    DROP INDEX IF EXISTS idx_doc_terms_term;
    -- 覆盖索引：TF-IDF 查询只读索引即可拿到 (term,doc_id,tf)，不必回表
    CREATE INDEX IF NOT EXISTS idx_doc_terms_tdt ON doc_terms(term, doc_id, tf);
    -- 分块表（P0）：整篇入库 + 标题切块并存，两种检索粒度做同一次评测里的对照。
    -- 命中判定统一落回 doc.path，所以 Recall 口径跟整篇模式完全一致，可直接比。
    CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY,
        doc_id INTEGER NOT NULL,
        chunk_no INTEGER NOT NULL,
        heading TEXT,
        content TEXT,
        path TEXT,                          -- T3 元数据过滤：所属文档路径（docs.path 冗余，过滤免 join）
        title TEXT,                         -- T3 元数据过滤：所属文档标题（docs.title 冗余）
        mtime INTEGER,                      -- T3 元数据过滤：源文件修改时间 epoch 秒（--since 用）
        UNIQUE(doc_id, chunk_no)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        title, tokens, tokenize='unicode61'
    );
    CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
    -- 删除/更新单篇时按 doc_id 清理 posting，避免全表扫
    CREATE INDEX IF NOT EXISTS idx_doc_terms_doc ON doc_terms(doc_id);
    CREATE INDEX IF NOT EXISTS idx_docs_hash ON docs(content_hash);
    CREATE INDEX IF NOT EXISTS idx_docs_cat ON docs(category);
    CREATE TABLE IF NOT EXISTS index_state(
        key TEXT PRIMARY KEY, value TEXT
    );
    -- 查询日志：评测集是「我猜用户会怎么问」，日志是「用户真这么问」。
    -- 没日志就没法发现高频问法与零结果的坑，评测集只能靠拍脑袋补。
    CREATE TABLE IF NOT EXISTS query_log(
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        query TEXT NOT NULL,
        mode TEXT,
        topk INTEGER,
        n_results INTEGER,
        latency_ms REAL,
        top1_path TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_qlog_ts ON query_log(ts);
    CREATE INDEX IF NOT EXISTS idx_qlog_empty ON query_log(n_results);
    -- raw/ 元数据表（不存正文）：T05 端到端验证新增。
    -- 设计原则：raw/ 下 1.3 万原件以二进制为主（图/视频/PDF/Office），
    -- 入正文成本高且无意义，但元数据（文件名/类型/大小/修改时间）
    -- 是查找「这个文件什么时候来的」「这一类有多少」的关键。
    CREATE TABLE IF NOT EXISTS raw_metadata(
        id INTEGER PRIMARY KEY,
        rel_path TEXT UNIQUE NOT NULL,           -- 相对 raw/ 的路径
        filename TEXT NOT NULL,
        ext TEXT NOT NULL,                       -- 小写扩展名，无扩展名用 '(无)'
        category TEXT,                           -- 一级目录（如 微信文件/项目/学习笔记）
        size_bytes INTEGER NOT NULL,
        mtime TEXT NOT NULL,                     -- ISO 8601 修改时间
        indexed_at TEXT NOT NULL,
        sha1 TEXT                                -- 可选，懒算
    );
    CREATE INDEX IF NOT EXISTS idx_raw_ext ON raw_metadata(ext);
    CREATE INDEX IF NOT EXISTS idx_raw_cat ON raw_metadata(category);
    CREATE INDEX IF NOT EXISTS idx_raw_mtime ON raw_metadata(mtime);
    CREATE INDEX IF NOT EXISTS idx_raw_fn ON raw_metadata(filename);
    """)
    _ensure_meta_columns(conn)
    _ensure_build_task_tables(conn)


def _ensure_build_task_tables(conn):
    """T12：建 build_tasks / build_sessions 队列表（幂等，照 _ensure_meta_columns
    同款显式模式）。表结构定义在 build_task.py（单一事实源，避免两处维护改散），
    这里只负责在 ensure_schema 里挂上调用；lazy import 避免模块级循环依赖
    （build_task 不 import kb_index，但 kb_index 是被 import 的大户，从严）。"""
    import build_task
    build_task.ensure_build_tasks(conn)


# T3 元数据过滤：新列清单（表, 列名, 类型）。docs.mtime + chunks.path/title/mtime。
# 上面的 CREATE TABLE IF NOT EXISTS 只对**新库**生效；老库（本模块 1.3GB 生产库）
# 必须走 ALTER 补列，且必须幂等——ensure_schema 每次 build/backfill 都会跑一遍。
META_COLUMNS = (
    ("docs", "mtime", "INTEGER"),
    ("chunks", "path", "TEXT"),
    ("chunks", "title", "TEXT"),
    ("chunks", "mtime", "INTEGER"),
)
# 🔴 隐私红线：这两个目录下的文件连 mtime 都不 stat，回填只用库内已有信息
# （ingest state / indexed_at）。索引库本就含其正文，但「不碰」从元数据层做起。
PRIVACY_PATH_PREFIXES = ("raw/隐私/", "raw/个人信息/")


def _ensure_meta_columns(conn):
    """幂等补 T3 元数据列（docs.mtime / chunks.path/title/mtime）。

    为什么用 PRAGMA table_info 显式判断而不是 try/except ALTER：
    ALTER 报错信息无法区分「列已存在」与真错误，且依赖异常流控制可读性差。
    显式查列名，重复跑 ensure_schema 零副作用、零报错。
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, col, ctype in META_COLUMNS:
        if table not in tables:
            continue  # 表尚未创建（全新库由 ensure_schema 的 executescript 建）
        have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ctype))


def _iso_to_epoch(s):
    """ISO 8601（naive 本地时间，与库内 indexed_at/state mtime 同格式）→ epoch 秒。
    解析失败返 None（调用方有兜底链，不许因单篇坏数据整批回填失败）。"""
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(str(s).strip()).timestamp())
    except (ValueError, OSError, OverflowError):
        return None


def backfill_chunk_meta(conn):
    """回填 chunks 的 path/title/mtime（T3 元数据过滤的数据基础）。

    两级回填：
      1. docs.mtime（只补 NULL 行）——raw 品类取 ingest_raw_state 里登记的
         文件 mtime（摄取时 scandir 拿到，零磁盘 IO，且反映的正是「被索引进库
         那一版」的时间）；其余品类 stat 源文件（wiki/modules/root 约 470 篇）；
         文件已不存在/隐私目录 → docs.indexed_at 兜底；再不行 → 0
         （保证非 NULL：时间未知不满足 --since，但回填验收要求全非空）
      2. 传播到 chunks（join docs，只补 NULL 行）——同一篇的 chunk 共享
         path/title/mtime，与 doc 级过滤语义一致

    幂等：只处理 NULL 行，重跑代价≈0（build 新增的 chunk 重跑一次即补齐）。
    返回统计 dict（chunks_null_left 应为 0，孤儿 chunk 除外）。
    """
    conn.execute("PRAGMA busy_timeout=30000")  # 与 build 并发时等锁，而非 5s 秒挂
    _ensure_meta_columns(conn)
    # 1. 补 docs.mtime
    state = {}
    try:
        row = conn.execute("SELECT value FROM index_state WHERE key=?",
                           ("ingest_raw_state",)).fetchone()
        state = json.loads(row[0]) if row else {}
        if not isinstance(state, dict):
            state = {}
    except (sqlite3.OperationalError, ValueError):
        state = {}  # index_state 不存在/JSON 坏：不影响非 raw 品类
    pend = conn.execute(
        "SELECT id, path, category, indexed_at FROM docs WHERE mtime IS NULL").fetchall()
    upd = []
    for did, path, cat, idx_at in pend:
        ts = None
        if cat == "raw":
            # state 结构 {rel_path: [size, mtime_iso(, hash)]}，取下标 1
            ent = state.get(path)
            ts = _iso_to_epoch(ent[1] if isinstance(ent, (list, tuple)) and len(ent) > 1 else None)
        if ts is None and path and not path.startswith(PRIVACY_PATH_PREFIXES):
            try:
                ts = int(os.stat(os.path.join(KB_ROOT, path.replace("/", os.sep))).st_mtime)
            except OSError:
                ts = None  # 文件已不在磁盘（索引残留），走 indexed_at 兜底
        if ts is None:
            ts = _iso_to_epoch(idx_at) or 0
        upd.append((ts, did))
    if upd:
        conn.executemany("UPDATE docs SET mtime=? WHERE id=?", upd)
    # 2. 传播到 chunks（相关子查询版，兼容旧 SQLite；docs.id 是主键，查找 O(log n)）
    n_null_before = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE path IS NULL OR title IS NULL OR mtime IS NULL"
    ).fetchone()[0]
    conn.execute("""
        UPDATE chunks SET
            path =(SELECT d.path  FROM docs d WHERE d.id = chunks.doc_id),
            title=(SELECT d.title FROM docs d WHERE d.id = chunks.doc_id),
            mtime=(SELECT d.mtime FROM docs d WHERE d.id = chunks.doc_id)
        WHERE path IS NULL OR title IS NULL OR mtime IS NULL
    """)
    n_null_left = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE path IS NULL OR title IS NULL OR mtime IS NULL"
    ).fetchone()[0]
    n_orphan = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE doc_id NOT IN (SELECT id FROM docs)"
    ).fetchone()[0]
    conn.commit()
    return {"docs_mtime_filled": len(upd),
            "chunks_null_before": n_null_before,
            "chunks_null_left": n_null_left,
            "orphan_chunks": n_orphan,
            "total_chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]}


def upsert_doc(conn, doc):
    """插入或更新单篇文档（三个表同步：docs / docs_fts / doc_terms）。"""
    cur = conn.execute("SELECT id FROM docs WHERE path=?", (doc["path"],))
    row = cur.fetchone()
    fts_text = tokens_to_fts_text(doc["title"] + " " + doc["summary"] + " " + doc["content"])
    tf = {}
    for t in tokenize(doc["content"] + " " + doc["title"]):
        tf[t] = tf.get(t, 0) + 1
    if row:
        doc_id = row[0]
        conn.execute(
            "UPDATE docs SET category=?,title=?,summary=?,content=?,content_hash=?,word_count=?,indexed_at=? WHERE id=?",
            (doc["category"], doc["title"], doc["summary"], doc["content"], doc["hash"],
             len(tf), datetime.now().isoformat(timespec="seconds"), doc_id))
        conn.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
        conn.execute("DELETE FROM doc_terms WHERE doc_id=?", (doc_id,))
    else:
        cur = conn.execute(
            "INSERT INTO docs(path,category,title,summary,content,content_hash,word_count,indexed_at) VALUES(?,?,?,?,?,?,?,?)",
            (doc["path"], doc["category"], doc["title"], doc["summary"], doc["content"],
             doc["hash"], len(tf), datetime.now().isoformat(timespec="seconds")))
        doc_id = cur.lastrowid
    # 🔴 为什么 title 必须过一遍 tokens_to_fts_text：FTS5 用 tokenize='unicode61'，
    #    汉字被归为 Unicode L*，整串中文标题会被切成一个 token。实测 title:"知识"
    #    命中 0 篇，而 tokens:"知识" 命中 484 篇 —— 中文标题自建库以来从未进过索引，
    #    导致 kb_index.py:950 的 bm25(docs_fts, W, 1.0) 标题加权一直是 no-op。
    #    改法抄 AstrBot 42,970★：**不改 tokenizer，改写入侧**——用与正文同一个
    #    分词漏斗把标题预分词后写进去，保持 unicode61 默认。
    conn.execute("INSERT INTO docs_fts(rowid,title,tokens) VALUES(?,?,?)",
                 (doc_id, tokens_to_fts_text(doc["title"]), fts_text))
    conn.executemany("INSERT INTO doc_terms(doc_id,term,tf) VALUES(?,?,?)",
                     [(doc_id, t, c) for t, c in tf.items()])
    write_chunks(conn, doc_id, doc)
    return doc_id


def write_chunks(conn, doc_id, doc):
    """把文档切成标题级块，写 chunks + chunks_fts。幂等：先清旧块再写。"""
    try:
        conn.execute("SELECT 1 FROM chunks LIMIT 1")
    except sqlite3.OperationalError:
        return 0  # 老库没有分块表，跳过（build --full 会建）
    old = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    for cid in old:
        conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
    pieces = split_chunks(doc["title"], doc["content"])
    # 标题分词提到循环外：同一文档的每块 title 完全相同，放循环里会对
    # 单个 28 块的文档重复分词 28 次（全库 43,760 块，重建时这是纯浪费）。
    title_fts = tokens_to_fts_text(doc["title"])
    # 🔴 T3 元数据必须在插入时就带上（v1.13.5）：旧版只写 4 列，path/title/mtime
    # 全靠事后 `backfill-meta` 补——但那个子命令**没有任何自动化在跑**，于是每次
    # 新建文档都留一批 NULL 块。实测已累积 2962 块 / 324 篇，而 kb_rag.py:384 明写
    # 「mtime 为 NULL 不满足 --since」→ 这些文档用时间过滤检索时被静默排除。
    # 补偿机制不能当主路径：写对才是根因修复，回填只该处理历史存量。
    meta = conn.execute("SELECT path, title, mtime FROM docs WHERE id=?", (doc_id,)).fetchone()
    dpath, dtitle, dmtime = meta if meta else (doc.get("path"), doc.get("title"), None)
    for no, heading, text in pieces:
        cur = conn.execute(
            "INSERT INTO chunks(doc_id,chunk_no,heading,content,path,title,mtime)"
            " VALUES(?,?,?,?,?,?,?)",
            (doc_id, no, heading, text, dpath, dtitle, dmtime))
        cid = cur.lastrowid
        conn.execute("INSERT INTO chunks_fts(rowid,title,tokens) VALUES(?,?,?)",
                     (cid, title_fts, tokens_to_fts_text(heading + "\n" + text)))
    return len(pieces)


def guard_chunker_fp(conn):
    """分块器指纹护栏（T6）：库内 chunker_fp 与代码 CHUNKER_FP 不一致时拒绝增量 build。

    🔴 失败模式 = 拒绝干活，不是干错活：CHUNK_MAX 等分块参数变了之后，
    sync_owned 只重切自有品类（wiki/modules/root），ingest_raw 对 hash 未变的
    raw 文档直接 skipped —— 直接跑增量 build 会得到「一半新分块一半旧分块」的
    混代索引，而且库看起来完全健康（docs/bge 覆盖率 100% 全对）。所以不一致就
    SystemExit，逼你走 rechunk_all.py（先 --dry-run 再 --apply）。
    """
    try:
        fp_db = conn.execute(
            "SELECT value FROM index_state WHERE key='chunker_fp'").fetchone()
    except sqlite3.OperationalError:
        return  # index_state 不存在（全新库）—— 无护栏可守，放行
    if fp_db and fp_db[0] != CHUNKER_FP:
        raise SystemExit(
            "✗ 分块器指纹不一致：库内 %s / 代码 %s\n"
            "  现在做增量 build 会产生【混代索引】（一半新分块一半旧分块，raw 侧永久不更新）。\n"
            "  请先跑：python rechunk_all.py --dry-run  （再 --apply）"
            % (fp_db[0], CHUNKER_FP))


def sync_owned(conn, full=False, verbose=True):
    """同步自有品类（wiki/modules/root，见 INDEX_TARGETS）的索引：
    扫描 → 增量 upsert → 自有品类清理 → 写 last_build。

    🔴 所有权边界（BUG-20261022-19，raw docs 3885→0 事故复发）：本函数**绝不碰**
    category='raw' 的文档——raw 正文的摄取/清理/存活判断全归 ingest_raw.py。
    旧版 cmd_build 有两处无类别边界的删除：--full 的全表清空，
    以及增量清理按「不在本次扫描 live 集」drop_doc（raw 路径永远不在 wiki 的
    live 集里 → 被当失效索引删光）。现在 kb_index.py build 与 kb_rag.py build
    都走本函数，统一入口 + 所有权边界在函数内强制，旧入口再被调用也冲不掉 raw。
    """
    guard_chunker_fp(conn)   # T6：分块器指纹不一致 → SystemExit（防混代索引）
    ph = ",".join("?" * len(OWNED_CATEGORIES))

    def _say(msg):
        if verbose:
            print(msg)

    docs = scan_files()
    _say("扫描到 %d 个候选文件" % len(docs))
    if full:
        # --full 只清自有品类：先按 id 清四张外围表，最后删 docs 本体；
        # raw 品类一行不碰（它的全量重扫由 ingest_raw.run_ingest(full=True) 负责）
        owned_ids = [r[0] for r in conn.execute(
            "SELECT id FROM docs WHERE category IN (%s)" % ph, OWNED_CATEGORIES)]
        for doc_id in owned_ids:
            conn.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
            conn.execute("DELETE FROM doc_terms WHERE doc_id=?", (doc_id,))
            try:
                for cid in [r[0] for r in conn.execute(
                        "SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]:
                    conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
                conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            except sqlite3.OperationalError:
                pass  # 老库没有分块表，跳过（后续 build --full 会建）
        conn.execute("DELETE FROM docs WHERE category IN (%s)" % ph, OWNED_CATEGORIES)
        _say("已清空旧索引（--full，仅限自有品类 %s，raw 不动）" % "/".join(OWNED_CATEGORIES))
    # 增量：只处理 hash 变化的
    # 🔴 摘要/标题变了也要感知（2026-10-23 修）：content_hash 只覆盖**正文**，
    # 摘要提取逻辑改进后正文没变、摘要变了 → hash 相同 → 被判「跳过」，
    # 结果库里留着旧摘要（实证：修 read_md_meta 后 build 报「更新 0 跳过 559」）。
    # 为什么不把摘要塞进 hash：hash 一改，559 篇全部重切 chunk → 4 万+ chunk_id
    # 变 → 4 万+ BGE 向量成孤儿 + 2 小时重编码，代价远大于收益。
    # 所以走轻量路径：**只 UPDATE docs 的 title/summary，不动 chunks**——
    # chunk 内容来自 content，content 没变就不该重切。
    existing = {p: h for p, h in conn.execute("SELECT path, content_hash FROM docs")}
    cur_meta = {p: (t, s) for p, t, s in
                conn.execute("SELECT path, title, summary FROM docs WHERE category IN (%s)" % ph,
                             OWNED_CATEGORIES)}
    added = updated = skipped = meta_only = 0
    for doc in docs:
        old = existing.get(doc["path"])
        old_meta = cur_meta.get(doc["path"])
        if old == doc["hash"]:
            # 正文没变：只有标题/摘要变了就轻量更新，chunk 不动
            if old_meta and old_meta != (doc["title"], doc["summary"]):
                conn.execute("UPDATE docs SET title=?,summary=?,indexed_at=? WHERE path=?",
                             (doc["title"], doc["summary"],
                              datetime.now().isoformat(timespec="seconds"), doc["path"]))
                # FTS 索引里含 title+summary，同步重写这一行
                row = conn.execute("SELECT id FROM docs WHERE path=?", (doc["path"],)).fetchone()
                if row:
                    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (row[0],))
                    conn.execute("INSERT INTO docs_fts(rowid,title,tokens) VALUES(?,?,?)",
                                 (row[0], tokens_to_fts_text(doc["title"]),
                                  tokens_to_fts_text(doc["title"] + " " + doc["summary"] + " " + doc["content"])))
                meta_only += 1
            skipped += 1
            continue
        upsert_doc(conn, doc)
        if old is None:
            added += 1
        else:
            updated += 1
    # 清理已消失文件的自有品类索引：只有 category 在 OWNED_CATEGORIES 且 path
    # 不在本次 live 集才 drop；raw 的存活判断归 ingest_raw.py，这里一律不碰
    live = {d["path"] for d in docs}
    for p in existing:
        if p not in live:
            row = conn.execute(
                "SELECT id FROM docs WHERE path=? AND category IN (%s)" % ph,
                (p,) + tuple(OWNED_CATEGORIES)).fetchone()
            if row:
                drop_doc(conn, row[0])
                _say("  移除失效索引: %s" % p)
    conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('last_build',?)",
                 (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    _say("完成：新增 %d，更新 %d，仅元数据 %d，跳过 %d，库内共 %d 篇 → %s" %
         (added, updated, meta_only, skipped, total, DB_PATH))
    return {"added": added, "updated": updated, "meta_only": meta_only,
            "skipped": skipped, "total": total}


def cmd_build(args):
    """kb_index.py build 薄包装：建目录/连接/建表后调 sync_owned（主体逻辑见那里）。

    🔴 即使本命令已废用（入口统一到 kb_rag.py build），仍要上锁：它仍可能被
    旧脚本/旧习惯调用，锁能防止它与另一个 build 并发写库（BUG-19 事故形态）。
    """
    wait = int(getattr(args, "wait", 0) or 0)
    try:
        lk = BuildLock().acquire(wait=wait)
    except BuildLock.Busy as e:
        _say("❌ 跳过本次 build：%s" % e)
        _say("   （同一时刻只允许一个 build；等它结束再跑，或用 --wait N 排队）")
        return 2
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = connect()
        ensure_schema(conn)
        sync_owned(conn, full=args.full, verbose=True)
        conn.close()
    finally:
        lk.release()
    return 0


# ============ 检索 ============
def search_fts(conn, query, topk):
    m = fts_escape(query)
    if not m:
        return []
    rows = conn.execute(
        "SELECT d.id, d.path, d.title, d.summary, bm25(docs_fts, %s, 1.0) AS s "
        "FROM docs_fts f JOIN docs d ON d.id = f.rowid "
        "WHERE docs_fts MATCH ? ORDER BY s LIMIT ?" % FTS_TITLE_W, (m, topk * 2)).fetchall()
    # bm25 越小越相关，转成越大越好
    if not rows:
        return []
    scores = [-r[4] for r in rows]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    out = []
    for r, s in zip(rows, scores):
        out.append({"doc_id": r[0], "path": r[1], "title": r[2], "summary": r[3],
                    "score": (s - lo) / span})
    return out[:topk]


def search_tfidf(conn, query, topk):
    qterms = {}
    for t in tokenize(query):
        qterms[t] = qterms.get(t, 0) + 1
    if not qterms:
        return []
    n_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    if n_docs == 0:
        return []
    # 查每个 query 词的 df 和 posting
    placeholders = ",".join("?" * len(qterms))
    df_rows = conn.execute(
        "SELECT term, COUNT(DISTINCT doc_id) FROM doc_terms WHERE term IN (%s) GROUP BY term" % placeholders,
        tuple(qterms.keys())).fetchall()
    df_map = dict(df_rows)
    postings = conn.execute(
        "SELECT doc_id, term, tf FROM doc_terms WHERE term IN (%s)" % placeholders,
        tuple(qterms.keys())).fetchall()
    doc_vecs = {}
    for doc_id, term, tf in postings:
        idf = math.log((n_docs + 1) / (df_map.get(term, 0) + 1)) + 1
        doc_vecs.setdefault(doc_id, {})[term] = tf * idf
    if not doc_vecs:
        return []
    qvec = {t: c * (math.log((n_docs + 1) / (df_map.get(t, 0) + 1)) + 1) for t, c in qterms.items()}
    qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
    scored = []
    for doc_id, dvec in doc_vecs.items():
        dot = sum(qvec.get(t, 0.0) * v for t, v in dvec.items())
        dnorm = math.sqrt(sum(v * v for v in dvec.values())) or 1.0
        scored.append((doc_id, dot / (qnorm * dnorm)))
    scored.sort(key=lambda x: -x[1])
    out = []
    for doc_id, s in scored[:topk]:
        row = conn.execute("SELECT path,title,summary FROM docs WHERE id=?", (doc_id,)).fetchone()
        if row:
            out.append({"doc_id": doc_id, "path": row[0], "title": row[1],
                        "summary": row[2], "score": s})
    return out


def search_fts_chunks(conn, query, topk):
    """分块检索：在 chunks_fts 上 MATCH，同文档的多个块去重取最高分。

    返回结构与 search_fts 完全一致（path 仍是文档路径），
    所以 Recall@5 的口径跟整篇模式相同 —— 这是能直接做对照的前提。
    """
    m = fts_escape(query)
    if not m:
        return []
    try:
        rows = conn.execute(
            "SELECT c.doc_id, c.heading, bm25(chunks_fts, %s, 1.0) AS s "
            "FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?" % FTS_TITLE_W,
            (m, topk * 8)).fetchall()
    except sqlite3.OperationalError:
        return []  # 没有分块表就退化为空，调用方会自己兜底
    best = {}
    for doc_id, heading, s in rows:
        if doc_id not in best:
            best[doc_id] = (heading, -s)
    items = sorted(best.items(), key=lambda x: -x[1][1])[:topk]
    if not items:
        return []
    scores = [v[1] for _, v in items]
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    out = []
    for doc_id, (heading, s) in items:
        row = conn.execute("SELECT path,title,summary FROM docs WHERE id=?", (doc_id,)).fetchone()
        if row:
            out.append({"doc_id": doc_id, "path": row[0], "title": row[1],
                        "summary": row[2], "heading": heading,
                        "score": (s - lo) / span})
    return out


def search_fts_fused(conn, query, topk, w_doc=1.0, w_chunk=1.0):
    """粒度融合：整篇路 + 分块路做 RRF。

    两路不是重复劳动——整篇路掌握全文词频（长文整体相关性），
    分块路掌握局部命中密度（某一段正好讲这件事）。RRF 只吃排名，天然适合跨粒度合并。
    """
    a = search_fts(conn, query, topk * 2)
    b = search_fts_chunks(conn, query, topk * 2)
    k = rrf_k()   # 运行时解析（KB_RRF_K 可配），不用模块级常量副本
    rrf, meta = {}, {}
    for rank, item in enumerate(a, 1):
        rrf[item["doc_id"]] = rrf.get(item["doc_id"], 0) + w_doc / (k + rank)
        meta[item["doc_id"]] = item
    for rank, item in enumerate(b, 1):
        rrf[item["doc_id"]] = rrf.get(item["doc_id"], 0) + w_chunk / (k + rank)
        meta.setdefault(item["doc_id"], item)
    out = []
    for doc_id, s in sorted(rrf.items(), key=lambda x: -x[1])[:topk]:
        item = dict(meta[doc_id])
        item["score"] = s
        out.append(item)
    return out


def search_hybrid(conn, query, topk, w_fts=1.0, w_tfidf=1.0):
    """RRF 融合：score(d) = Σ w_i/(k + rank(d))，只看排名不看原始分。
    权重可调：评测发现两路质量差距大时，等权 RRF 会稀释优质路，
    加权 RRF（w_fts=2, w_tfidf=1）是常见补偿手段。"""
    a = search_fts(conn, query, topk * 2)
    b = search_tfidf(conn, query, topk * 2)
    k = rrf_k()   # 运行时解析（KB_RRF_K 可配），不用模块级常量副本
    rrf = {}
    meta = {}
    for rank, item in enumerate(a, 1):
        rrf[item["doc_id"]] = rrf.get(item["doc_id"], 0) + w_fts / (k + rank)
        meta[item["doc_id"]] = item
    for rank, item in enumerate(b, 1):
        rrf[item["doc_id"]] = rrf.get(item["doc_id"], 0) + w_tfidf / (k + rank)
        meta.setdefault(item["doc_id"], item)
    ranked = sorted(rrf.items(), key=lambda x: -x[1])
    out = []
    for doc_id, s in ranked[:topk]:
        item = dict(meta[doc_id])
        item["score"] = s
        out.append(item)
    return out


def drop_doc(conn, doc_id):
    """彻底删掉一篇文档在四张表里的痕迹（docs / docs_fts / doc_terms / chunks）。"""
    conn.execute("DELETE FROM docs WHERE id=?", (doc_id,))
    conn.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
    conn.execute("DELETE FROM doc_terms WHERE doc_id=?", (doc_id,))
    try:
        for cid in [r[0] for r in conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]:
            conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    except sqlite3.OperationalError:
        pass


def log_query(conn, query, mode, results, latency_ms):
    """把一次真实检索落库。**CLI 与门户的真实检索会埋点**（只要连接可写）；
    🔴 评测脚本必须自行 monkeypatch 掉本函数（见
    `outputs/知识库运维/2026-10-23-judged-eval-runner.py:80` 的默认 no-op），
    否则几百上千条评测 query 会把「真实使用」这个信号淹没掉。

    （订正说明：原文写「只有 CLI 调用」不准确——`kb_rag.py:602` 让所有走
    search_unified 的调用都会埋点，门户同样会写；实测 query_log 413 个去重
    query 里 202 个是评测集原题，来源是旧版 runner 未禁用日志时的历史运行。）"""
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,top1_path)"
            " VALUES(?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), query, mode, len(results),
             len(results), round(latency_ms, 3),
             results[0]["path"] if results else None))
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 库只读时（readonly 连接）不记日志，不影响检索本身


def cmd_search(args):
    if not os.path.exists(DB_PATH):
        print("索引不存在，请先运行: python kb_rag.py build")
        sys.exit(1)
    conn = connect()
    ensure_schema(conn)
    mode = args.mode
    fn = {"fts": search_fts, "tfidf": search_tfidf, "hybrid": search_hybrid,
          "chunk": search_fts_chunks, "fused": search_fts_fused}.get(mode)
    if mode == "auto":
        # 默认走「关键词⊕LSA」：纯 numpy、0.7ms、人工 100 条 MRR 0.8525 最高。
        # BGE 语义路虽然能在关键词路完全失效的难例上救回 40%，但整体更差（单路 87% vs 95%），
        # 且每次检索多 20ms + 首次加载模型 5 秒 —— 边际收益不足，不进默认路径。
        has_vec = False
        try:
            has_vec = conn.execute(
                "SELECT count(*) FROM lsa_model WHERE id=1").fetchone()[0] > 0
        except sqlite3.OperationalError:
            has_vec = False
        if has_vec:
            import vector_index as _vi
            fn = _vi.search_fused_vec
            mode = "fused_vec（关键词⊕LSA 向量）"
        else:
            fn, mode = search_fts_fused, "fused（关键词粒度融合）"
    elif mode in ("fused_bge", "bge", "tri"):
        import bge_encoder as _bg
        fn = {"bge": _bg.search_bge, "fused_bge": _bg.search_fused_bge}[
            "fused_bge" if mode in ("fused_bge", "tri") else "bge"]
    elif mode == "fused_vec":
        import vector_index as _vi
        fn = _vi.search_fused_vec
    elif mode == "vec":
        import vector_index as _vi
        fn = _vi.search_vec
    args.mode = mode
    t0 = time.perf_counter()
    results = fn(conn, args.query, args.topk)
    latency_ms = (time.perf_counter() - t0) * 1000
    log_query(conn, args.query, args.mode, results, latency_ms)
    conn.close()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print("查询: %s ｜ 模式: %s ｜ 分词器: %s" % (args.query, args.mode, "jieba" if USE_JIEBA else "bigram"))
    if not results:
        print("  (无结果)")
    for i, r in enumerate(results, 1):
        print("  %d. [%.4f] %s" % (i, r["score"], r["path"]))
        print("     %s" % r["title"])


def cmd_stats(args):
    if not os.path.exists(DB_PATH):
        print("索引不存在")
        sys.exit(1)
    conn = connect(readonly=True)
    print("数据库: %s (%.1f KB)" % (DB_PATH, os.path.getsize(DB_PATH) / 1024))
    print("分词器: %s ｜ journal: %s ｜ page_size: %s" % (
        "jieba" if USE_JIEBA else "bigram",
        conn.execute("PRAGMA journal_mode").fetchone()[0],
        conn.execute("PRAGMA page_size").fetchone()[0]))
    print("文档数: %d" % conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0])
    print("词项数: %d" % conn.execute("SELECT COUNT(DISTINCT term) FROM doc_terms").fetchone()[0])
    print("posting 数: %d" % conn.execute("SELECT COUNT(*) FROM doc_terms").fetchone()[0])
    try:
        cn = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if cn:
            avg = conn.execute("SELECT AVG(LENGTH(content)) FROM chunks").fetchone()[0]
            print("分块数: %d（平均 %.0f 字，整篇入库时可比分子=%d）" % (cn, avg or 0, conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]))
    except sqlite3.OperationalError:
        pass
    print("分类分布:")
    for cat, n in conn.execute("SELECT category, COUNT(*) FROM docs GROUP BY category"):
        print("  %s: %d" % (cat, n))
    row = conn.execute("SELECT value FROM index_state WHERE key='last_build'").fetchone()
    if row:
        print("最近构建: %s" % row[0])
    try:
        n = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
        if n:
            zero = conn.execute("SELECT COUNT(*) FROM query_log WHERE n_results=0").fetchone()[0]
            print("查询日志: %d 条｜零结果率 %.1f%%｜最近 3 条：" % (n, zero / n * 100))
            for q, ts, nr in conn.execute(
                    "SELECT query,ts,n_results FROM query_log ORDER BY id DESC LIMIT 3"):
                print("   %s  (%d 条结果)  %s" % (ts, nr, q[:40]))
    except sqlite3.OperationalError:
        pass
    conn.close()


def cmd_raw_scan(args):
    """扫描 raw/ 目录，把所有文件的元数据入 raw_metadata 表（不存正文）。

    🔴 写库互斥（2026-10-26）：raw-scan 以前裸写，一次事务持写锁数十秒（1.6 万文件），
       而 build 的 busy_timeout 只有 5s，等到头就报 database is locked（实测撞 2 次）。
       现在与 build 共用 WriteLock，并且分批提交把单次持锁压到毫秒级。
    """
    RAW_ROOT = args.raw_root
    if not os.path.isdir(RAW_ROOT):
        print("✗ raw 目录不存在：%s" % RAW_ROOT); return 1
    wait = getattr(args, "wait", 120)
    try:
        with write_lock(wait=wait):
            return _raw_scan_body(RAW_ROOT)
    except BuildLock.Busy as e:
        print("✗ 跳过：%s" % e)
        print("   （同一时刻只允许一个写库进程；等它结束再跑，或用 --wait N 排队）")
        return 2


def _raw_scan_body(RAW_ROOT):
    import time
    from datetime import datetime
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    ensure_schema(conn)
    # 🔴 isolation_level=None：关掉 sqlite3 的隐式事务管理，才能显式 BEGIN IMMEDIATE。
    #    Python 默认是 **deferred** 事务——先读后写时升级写锁失败会直接抛 SQLITE_BUSY，
    #    busy_timeout 完全不生效。这是「明明设了超时还报 locked」的常见真因，
    #    在同时有别人在写的场景里几乎必踩。
    conn.isolation_level = None
    BATCH = 500          # 每 500 条提交一次：单次持锁从数十秒压到毫秒级
    t0 = time.time()
    seen = set()
    n_ins = n_upd = 0
    pending = 0
    conn.execute("BEGIN IMMEDIATE")
    for dirpath, _, files in os.walk(RAW_ROOT):
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, RAW_ROOT).replace("\\", "/")
            ext = os.path.splitext(fn)[1].lower() or "(无)"
            cat = rel.split("/", 1)[0] if "/" in rel else "(根)"
            mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            now_iso = datetime.now().isoformat(timespec="seconds")
            cur = conn.execute("SELECT id FROM raw_metadata WHERE rel_path=?", (rel,))
            if cur.fetchone():
                conn.execute("UPDATE raw_metadata SET filename=?, ext=?, category=?, size_bytes=?, mtime=?, indexed_at=? WHERE rel_path=?",
                             (fn, ext, cat, st.st_size, mtime, now_iso, rel))
                n_upd += 1
            else:
                conn.execute("INSERT INTO raw_metadata(rel_path, filename, ext, category, size_bytes, mtime, indexed_at) VALUES(?,?,?,?,?,?,?)",
                             (rel, fn, ext, cat, st.st_size, mtime, now_iso))
                n_ins += 1
            seen.add(rel)
            pending += 1
            if pending >= BATCH:
                # 分批提交的代价：中途崩了只剩部分写入（不再是 all-or-nothing）。
                # 这里可以接受——元数据扫描天然幂等，重跑一次就补齐，而「持锁数十秒」
                # 会让别的写库进程直接失败，代价更大。
                conn.execute("COMMIT")
                conn.execute("BEGIN IMMEDIATE")
                pending = 0
    conn.execute("COMMIT")

    # 软删：库里没在 seen 里的记录清掉（schema 暂不存 deleted 列，先物理清）
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.execute("SELECT rel_path FROM raw_metadata")
    orphan = [r[0] for r in cur if r[0] not in seen]
    if orphan:
        conn.executemany("DELETE FROM raw_metadata WHERE rel_path=?", [(r,) for r in orphan])
    conn.execute("COMMIT")
    total = conn.execute("SELECT COUNT(*) FROM raw_metadata").fetchone()[0]
    tot_sz = conn.execute("SELECT SUM(size_bytes) FROM raw_metadata").fetchone()[0] or 0
    print("[raw-scan] 耗时 %.1fs  新增 %d  更新 %d  软删 %d  库内共 %d 件  总 %.2f GB" % (
        time.time() - t0, n_ins, n_upd, len(orphan), total, tot_sz / 1073741824))
    conn.close()
    return 0


def cmd_raw_query(args):
    """按类型/时间/关键字检索 raw/ 元数据。"""
    conn = connect()
    ensure_schema(conn)
    sql = "SELECT rel_path, filename, ext, category, size_bytes, mtime FROM raw_metadata WHERE 1=1"
    params = []
    if args.ext:
        sql += " AND ext=?"; params.append(args.ext.lower())
    if args.cat:
        sql += " AND category=?"; params.append(args.cat)
    if args.kw:
        sql += " AND filename LIKE ?"; params.append("%%%s%%" % args.kw)
    if args.since:
        sql += " AND mtime >= ?"; params.append(args.since)
    if args.before:
        sql += " AND mtime <= ?"; params.append(args.before)
    sql += " ORDER BY mtime DESC LIMIT ?"; params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    print("找到 %d 条（限 %d）：" % (len(rows), args.limit))
    for r in rows:
        sz = r[4]
        sz_str = "%.2f MB" % (sz / 1048576) if sz > 1048576 else "%.1f KB" % (sz / 1024)
        print("  %s  [%s]  %-50s  %s  %s" % (r[5][:10], r[2], r[1][:50], sz_str, r[3]))
    conn.close()
    return 0


def cmd_raw_stats(args):
    """raw/ 元数据统计：按扩展名/一级目录分布。"""
    conn = connect()
    ensure_schema(conn)
    total = conn.execute("SELECT COUNT(*), SUM(size_bytes) FROM raw_metadata").fetchone()
    print("raw/ 总文件 %d ｜ 总大小 %.2f GB" % (total[0], (total[1] or 0) / 1073741824))
    print("\n按扩展名 Top %d：" % args.top)
    for ext, n, sz in conn.execute(
        "SELECT ext, COUNT(*), SUM(size_bytes) FROM raw_metadata GROUP BY ext ORDER BY COUNT(*) DESC LIMIT ?",
        (args.top,)):
        print("  %-8s %5d 件  %.2f GB" % (ext, n, sz / 1073741824))
    print("\n按一级目录 Top %d：" % args.top)
    for cat, n, sz in conn.execute(
        "SELECT category, COUNT(*), SUM(size_bytes) FROM raw_metadata GROUP BY category ORDER BY COUNT(*) DESC LIMIT ?",
        (args.top,)):
        print("  %-25s %5d 件  %.2f GB" % (cat, n, sz / 1073741824))
    conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="知识库 SQLite 索引与混合检索")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="构建/增量更新索引")
    p_build.add_argument("--full", action="store_true", help="全量重建")
    p_build.add_argument("--wait", type=int, default=0,
                         help="拿不到锁时排队等待的秒数；0=立即失败（默认）")
    p_search = sub.add_parser("search", help="检索")
    p_search.add_argument("--query", required=True)
    p_search.add_argument(
        "--mode", choices=["auto", "fused_vec", "fused_bge", "bge", "fused", "fts", "chunk",
                           "vec", "tfidf", "hybrid"],
        default="auto",
        help="默认 auto：关键词⊕LSA 向量（0.7ms，MRR 最优）。BGE 是深度可选，"
             "能救关键词路完全失效的难例，但慢 28 倍，不进默认路径")
    p_search.add_argument("--topk", type=int, default=5)
    p_search.add_argument("--json", action="store_true")
    sub.add_parser("stats", help="索引统计")
    # T05 端到端新增：raw/ 元数据三件套
    p_raw_scan = sub.add_parser("raw-scan", help="扫描 raw/ 入元数据表（不存正文）")
    p_raw_scan.add_argument("--raw-root", default=os.path.join(KB_ROOT, "raw"), help="raw/ 根目录")
    p_raw_scan.add_argument("--wait", type=int, default=120,
                            help="拿不到写锁时最多等多少秒（0=立刻跳过；默认 120 排队）")
    p_raw_query = sub.add_parser("raw-query", help="按类型/时间/关键字检索 raw/ 元数据")
    p_raw_query.add_argument("--ext", help="按扩展名筛选（如 .pdf）")
    p_raw_query.add_argument("--cat", help="按一级目录筛选（如 微信文件）")
    p_raw_query.add_argument("--kw", help="文件名关键字（模糊匹配）")
    p_raw_query.add_argument("--since", help="mtime 起始日期 YYYY-MM-DD")
    p_raw_query.add_argument("--before", help="mtime 截止日期 YYYY-MM-DD")
    p_raw_query.add_argument("--limit", type=int, default=20)
    p_raw_stats = sub.add_parser("raw-stats", help="raw/ 元数据分布统计")
    p_raw_stats.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    rc = {"build": cmd_build, "search": cmd_search, "stats": cmd_stats,
          "raw-scan": cmd_raw_scan, "raw-query": cmd_raw_query,
          "raw-stats": cmd_raw_stats}[args.cmd](args)
    sys.exit(rc or 0)  # build 拿不到锁时返回 2，让调用方（定时任务）看得见


if __name__ == "__main__":
    main()
