#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bge_encoder — 真·语义向量路（BGE-small-zh）
=============================================

为什么最后还是做成了：`vector_index.py` 的 LSA 证明了「稠密化 ≠ 语义化」——
它把「大模型瞎编怎么防止」匹配到了完全无关的文档，因为 LSA 只看词共现。

BGE 是**预训练语义模型**：能理解「瞎编 = 幻觉」这种同义/近义关系。
模型从 **ModelScope** 下的（BAAI/bge-small-zh 官方上传），绕开了 HuggingFace 不通的问题。

关键做法（BGE 官方用法，不是自创）：
  · query 前加指令前缀 `为这个句子生成表示以用于检索相关文章：`（文档侧不加）
  · 取 **CLS** token 的 hidden state 作为句向量（不是 mean pooling）
  · L2 归一化后点积即余弦

用法：
  python bge_encoder.py build --batch 32     # 编码全部块（约 30-60 秒，CPU）
  python bge_encoder.py search --query "大模型瞎编怎么防止"
  python bge_encoder.py stats
"""

import argparse
import json
import os
import pickle
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kb_index import connect, DB_PATH, search_fts_fused, RRF_K, CHUNKER_FP  # noqa: E402

# 🔴 为什么模型目录要能用环境变量覆盖：换模型 / 回退都应该只是**切环境变量**，
# 不该改代码。2026-10-28 跑 bge-m3 GPU 换模型沙盘时暴露的就是这个问题 —— 当时
# MODEL_DIR 是硬编码的，想试 bge-m3 就得改生产代码，试完还得改回来，改的过程
# 本身就是风险。默认值与历史行为**完全一致**（不设环境变量时行为不变）。
MODEL_DIR = (os.environ.get("KB_BGE_MODEL_DIR")
             or os.path.join(HERE, "models", "bge-small-zh"))
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
VEC_TABLE = "chunk_bge"
_MODEL_CACHE = {}

# ============ bge-m3 多语塔（外文查询路由，默认关闭）============
# 🔴 为什么独立一套常量：m3 是 1024 维多语模型，与 small-zh（512 维中文单语）
# 完全隔离，不能复用 QUERY_PREFIX（BGE-M3 官方不加中文指令前缀）、不能复用
# chunk_bge 表（维度不同）。只有 KB_VEC_ROUTE 开启且查询判为外文时才启用。
M3_MODEL_DIR = (os.environ.get("KB_M3_MODEL_DIR")
                or os.path.join(HERE, "models", "bge-m3"))
M3_VEC_TABLE = "vec_m3"
M3_MAX_LEN = 512   # 与全库缓存的生成口径 _m3_tower_eval.py 保持一致（max_len=512）

# ============ 编码文本口径（B4 一致性 + C7 标题利用）============
# 🔴 变更前（2026-10-27 取证）：两条编码路径各拼各的 ——
#   ① 全量 cmd_build（本文件）：heading + "\n" + content，且截 [:1000]
#   ② 增量 backfill_bge（kb_rag.py）：只编码 content，且**不截断**
#   ⇒ 同一个 chunk 经两条路径得到两个不同的向量；backfill 补的块和 build
#     建的块不在同一个语义空间里，检索时等于半个库是噪声。
#   现在两条路径都必须走 build_vector_text()，口径由 mode 决定，指纹写进
#   bge_model.tie_breaker，进程内缓存按「built_at + tie_breaker」失效。
#
# 三档口径（沙盘结论出来前默认 v1 = 与现状全量路径等价，保证不劣化）：
#   v1 = heading + "\n" + content                （现状）
#   v2 = title + "\n" + heading + "\n" + content
#   v3 = title + "\n" + title + "\n" + heading + "\n" + content（短标题重复加权）
VECTOR_TEXT_MODE = (os.environ.get("KB_BGE_VTEXT_MODE") or "v1").strip().lower()
VECTOR_MAX_CHARS = int(os.environ.get("KB_BGE_VTEXT_MAX_CHARS") or 1000)
# v3 标题重复的长度上限：见 build_vector_text 里的解释（512 token 预算）
TITLE_REPEAT_MAX_LEN = 30
_MODES = ("v1", "v2", "v3")


def build_vector_text(title, heading, content, mode=None, max_chars=None):
    """唯一的编码文本构造函数 —— 全量 build / 增量 backfill **必须**都走这里。

    Args:
        title: 文档标题（docs.title；chunks 上有冗余列，取不到时 LEFT JOIN docs）
        heading: 块标题（chunks.heading，可能是「父标题 > 子标题」链路）
        content: 块正文
        mode: 口径档位 v1/v2/v3，None 时取模块级 VECTOR_TEXT_MODE（环境变量可配）
        max_chars: 截断长度，None 时取 VECTOR_MAX_CHARS

    Returns:
        str: 送进 encode() 的文本。

    🔴 为什么 v3 的标题重复有长度上限：BGE-small-zh 的 max_length=512 token，
    而中文 1 字≈1 token。正文平均 848 字，加上 heading 平均 31 字已经接近
    预算；一个 40+ 字的标题重复两次会把正文挤掉 80 字的尾巴，反而丢正文信息。
    所以只在 title 非空且 ≤30 字时才重复（短标题通常是精准主题词，加权划算），
    长标题只拼一次（退化为 v2）。这是「加权」而不是「无脑复读」。

    🔴 为什么截断口径要统一：原 cmd_build 截 1000、backfill 不截，同一个块
    尾部不同 ⇒ 向量不同。现在三档都截 max_chars，截断发生在拼接之后，
    保证「同一 (title, heading, content) + 同一 mode ⇒ 同一字符串」。
    """
    mode = (mode or VECTOR_TEXT_MODE or "v1").strip().lower()
    if mode not in _MODES:
        mode = "v1"
    max_chars = VECTOR_MAX_CHARS if max_chars is None else int(max_chars)
    title = (title or "").strip()
    heading = (heading or "").strip()
    content = content or ""

    parts = []
    if mode == "v3":
        # 短标题重复 2 次做加权；长标题只拼 1 次（理由见 docstring）
        if title and len(title) <= TITLE_REPEAT_MAX_LEN:
            parts.append(title)
        if title:
            parts.append(title)
    elif mode == "v2":
        if title:
            parts.append(title)
    # v1：不含 title（与现状全量路径等价）
    if heading:
        parts.append(heading)
    parts.append(content)

    text = "\n".join(parts)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def vector_fingerprint(mode=None, max_chars=None):
    """编码口径指纹，写进 bge_model.tie_breaker，用于判断「要不要重编码」。

    例："v1|max_chars=1000|chunker=v2|max=480|snap=0.85|ovl=0"。指纹变了 ⇒ 库里
    的向量是用别的口径编的 ⇒ 必须重跑。

    🔴 T6 分块改造：分块器版本（kb_index.CHUNKER_FP）也必须拼进指纹。分块变了，
    向量「文本输入」事实上也变了（同一段文字被切进不同的块），tie_breaker 必须能
    承载这个变化，否则 load_bge 不会重载进程内常驻矩阵，swap 换表后在线进程继续
    用旧 chunk_id 给新 chunks 打分 → 结果张冠李戴但分数合理（最难排查的故障）。
    """
    mode = (mode or VECTOR_TEXT_MODE or "v1").strip().lower()
    if mode not in _MODES:
        mode = "v1"
    max_chars = VECTOR_MAX_CHARS if max_chars is None else int(max_chars)
    return "%s|max_chars=%d|chunker=%s" % (mode, max_chars, CHUNKER_FP)


def _ensure_tie_breaker_column(conn):
    """老库兼容：bge_model 没有 tie_breaker 列时才 ADD COLUMN。

    🔴 不能直接改 CREATE TABLE —— CREATE TABLE IF NOT EXISTS 对已存在的库
    是 no-op，改 CREATE 只会让**新库**有列、**老库**永远没有，然后所有
    SELECT tie_breaker 老库全炸。必须 PRAGMA 探测 + ALTER。
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bge_model)").fetchall()}
    except sqlite3.Error:
        return False
    if "tie_breaker" in cols:
        return False
    try:
        conn.execute("ALTER TABLE bge_model ADD COLUMN tie_breaker TEXT")
        try:
            conn.commit()
        except sqlite3.Error:
            pass
        return True
    except sqlite3.Error:
        return False


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS chunk_bge(
        chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS bge_model(
        id INTEGER PRIMARY KEY CHECK(id=1), dim INTEGER, n INTEGER,
        model TEXT, built_at TEXT)""")
    # 🔴 bge-m3 多语塔的表（T1 语言路由）。CREATE IF NOT EXISTS，老库兼容：
    # 不碰 chunk_bge 的既有 CREATE，只在旁边新增两张 m3 专用表。
    conn.execute("""CREATE TABLE IF NOT EXISTS vec_m3(
        chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS m3_model(
        id INTEGER PRIMARY KEY CHECK(id=1), dim INTEGER, n INTEGER,
        model TEXT, built_at TEXT)""")
    _ensure_tie_breaker_column(conn)


def _read_tie_breaker(conn):
    """读库里的 tie_breaker；列不存在（老库/只读快照）时返回 ''。"""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bge_model)").fetchall()}
    except sqlite3.Error:
        return ""
    if "tie_breaker" not in cols:
        return ""
    try:
        row = conn.execute("SELECT tie_breaker FROM bge_model WHERE id=1").fetchone()
    except sqlite3.Error:
        return ""
    return (row[0] if row and row[0] else "") or ""


def load_model():
    if "m" in _MODEL_CACHE:
        return _MODEL_CACHE["m"]
    import torch
    from transformers import AutoTokenizer, AutoModel
    if not os.path.exists(os.path.join(MODEL_DIR, "pytorch_model.bin")):
        raise SystemExit("模型不存在，先把 bge-small-zh 放到 %s" % MODEL_DIR)
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    mdl = AutoModel.from_pretrained(MODEL_DIR)
    mdl.eval()
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
    _MODEL_CACHE["m"] = (torch, tok, mdl)
    return _MODEL_CACHE["m"]


def encode(texts, batch=32, is_query=False):
    """BGE 官方用法：query 加指令前缀、取 CLS、L2 归一化。"""
    import numpy as np
    torch, tok, mdl = load_model()
    outs = []
    if is_query:
        texts = [QUERY_PREFIX + t for t in texts]
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        enc = tok(chunk, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            out = mdl(**enc)
        cls = out.last_hidden_state[:, 0]          # CLS pooling
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)
        outs.append(cls.numpy().astype("float32"))
        if (i // batch) % 5 == 0:
            # 进度必须走 stderr：stdout 是 --json 机器通道（QA 实测门户 /api/rag 的
            # json.loads 会被这里的 \r 进度打挂）
            print("    %d/%d" % (i + len(chunk), len(texts)), end="\r", file=sys.stderr)
    return np.concatenate(outs, axis=0)


def cmd_build(args):
    import numpy as np
    # 口径优先级：CLI --mode/--max-chars > 环境变量 KB_BGE_VTEXT_* > v1（现状等价）
    mode = getattr(args, "mode", None) or VECTOR_TEXT_MODE
    max_chars = getattr(args, "max_chars", None) or VECTOR_MAX_CHARS
    conn = connect()
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT id, title, heading, content FROM chunks ORDER BY id").fetchall()
    print("待编码 %d 块（口径 %s）" % (len(rows), vector_fingerprint(mode, max_chars)))
    t0 = time.time()
    # 🔴 必须走 build_vector_text()，与增量 backfill 同口径（B4）
    texts = [build_vector_text(t, h, c, mode, max_chars) for _, t, h, c in rows]
    vecs = encode(texts, args.batch)
    dt = time.time() - t0
    conn.execute("DELETE FROM chunk_bge")
    conn.executemany(
        "INSERT OR REPLACE INTO chunk_bge(chunk_id, vec) VALUES(?,?)",
        [(int(cid), v.astype("float32").tobytes()) for (cid, _, _, _), v in zip(rows, vecs)])
    conn.execute(
        "INSERT OR REPLACE INTO bge_model(id,dim,n,model,built_at,tie_breaker) "
        "VALUES(1,?,?,?,?,?)",
        (int(vecs.shape[1]), len(rows), "BAAI/bge-small-zh (ModelScope)",
         time.strftime("%Y-%m-%d %H:%M:%S"), vector_fingerprint(mode, max_chars)))
    conn.commit()
    print("编码完成：%d 块 × %d 维，用时 %.1fs（%.0f 块/秒）" % (
        len(rows), vecs.shape[1], dt, len(rows) / max(dt, 1e-6)))
    conn.close()


def load_bge(conn):
    import numpy as np
    # 🔴 为什么不能无条件返回缓存：重编码跑完后库里的向量已经换了，但进程内
    # 单例缓存还是旧矩阵 —— 表现为「重编码跑完了、指标纹丝不动」，会被误判成
    # 「改法无效」。所以缓存必须带上 built_at + tie_breaker 两个指纹一起比。
    fp_db = _read_tie_breaker(conn)
    cached = _MODEL_CACHE.get("v")
    if cached is not None:
        row0 = conn.execute(
            "SELECT built_at FROM bge_model WHERE id=1").fetchone()
        if cached.get("built_at") == (row0[0] if row0 else None) \
                and cached.get("tie_breaker", "") == fp_db:
            return cached
        print("[bge] 向量已更新（built_at/tie_breaker 变化），重载常驻矩阵",
              file=sys.stderr)
    row = conn.execute("SELECT dim,n,built_at FROM bge_model WHERE id=1").fetchone()
    if not row:
        return None
    rows = conn.execute("SELECT chunk_id, vec FROM chunk_bge ORDER BY chunk_id").fetchall()
    if not rows:
        # 重编码中间窗口（DELETE 后未 INSERT）会走到这里；np.stack([]) 会抛
        # ValueError，宁可返回 None 让调用方降级到关键词路，也不要 500。
        return None
    D = np.stack([np.frombuffer(v, dtype="float32") for _, v in rows])
    ids = [int(c) for c, _ in rows]
    cid2doc = {int(c): int(d) for c, d in conn.execute("SELECT id, doc_id FROM chunks")}
    # 与 ids 平行的 doc 数组（-1 = 孤儿块）。放在加载期算一次，检索期零成本；
    # 原实现每命中一块都要过一遍 dict，全扫时那是 4.8 万次 Python 查找。
    doc_arr = np.array([cid2doc.get(c, -1) for c in ids], dtype="int64")
    _MODEL_CACHE["v"] = {"D": D, "ids": ids, "cid2doc": cid2doc, "doc_arr": doc_arr,
                         "dim": row[0], "n": row[1], "built_at": row[2],
                         "tie_breaker": fp_db}
    return _MODEL_CACHE["v"]


def _oversample():
    """块级候选倍数：先取 topk*N 个**块**，再去重成 doc 级 topk。

    🔴 为什么做成可配（2026-10-29）：这是「向量路只看前 80 块」的根因——
    `kb_rag` 融合时传 `topk*2`（topk=5 ⇒ 10），乘 8 ⇒ 只扫 80 块。
    而实测（QA 留证）目标块的**余弦相似度并不低**（0.708~0.715），
    只是全局块排名在 563~2622 名，被这个截断挡在门外。
    设 0 = 全扫（精确 doc 级 topk）；缺省 8 = 保持历史行为，零风险。
    """
    raw = os.environ.get("KB_BGE_OVERSAMPLE")
    try:
        v = int(raw) if raw not in (None, "") else 8
    except ValueError:
        v = 8
    return v if v >= 0 else 0


def search_bge(conn, query, topk, oversample=None):
    import numpy as np
    m = load_bge(conn)
    if not m:
        return []
    qv = encode([query], is_query=True)[0]
    sims = m["D"] @ qv
    doc_arr = m.get("doc_arr")
    if doc_arr is None:  # 老缓存对象（不该出现，纯防御）
        doc_arr = np.array([m["cid2doc"].get(c, -1) for c in m["ids"]], dtype="int64")
    order = np.argsort(-sims)
    n_os = _oversample() if oversample is None else int(oversample)
    if n_os > 0 and topk * n_os < len(order):
        order = order[:topk * n_os]
    # np.unique 给的是**首次出现**下标；order 已按分数降序 ⇒ 首次出现即该 doc 的最高分块。
    # 这样 doc 级最高分是精确值，不再依赖「截断窗口里恰好凑够」。
    _, first = np.unique(doc_arr[order], return_index=True)
    best = {}
    for pos in first:
        idx = order[pos]
        doc_id = int(doc_arr[idx])
        if doc_id < 0:
            continue
        best[doc_id] = (int(m["ids"][idx]), float(sims[idx]))
    out = []
    for doc_id, (cid, s) in sorted(best.items(), key=lambda x: -x[1][1])[:topk]:
        r = conn.execute("SELECT path,title,summary FROM docs WHERE id=?", (doc_id,)).fetchone()
        if r:
            out.append({"doc_id": doc_id, "path": r[0], "title": r[1],
                        "summary": r[2], "score": s})
    return out


def load_m3_model():
    """懒加载 bge-m3 模型（CPU）。与 small-zh 的 load_model 完全隔离、互不污染。"""
    if "m3" in _MODEL_CACHE:
        return _MODEL_CACHE["m3"]
    import torch
    from transformers import AutoTokenizer, AutoModel
    if not os.path.exists(os.path.join(M3_MODEL_DIR, "pytorch_model.bin")):
        raise SystemExit("m3 模型不存在，先把 bge-m3 放到 %s" % M3_MODEL_DIR)
    tok = AutoTokenizer.from_pretrained(M3_MODEL_DIR)
    mdl = AutoModel.from_pretrained(M3_MODEL_DIR)
    mdl.eval()
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
    _MODEL_CACHE["m3"] = (torch, tok, mdl)
    return _MODEL_CACHE["m3"]


def encode_m3(texts, batch=8):
    """BGE-M3 多语塔编码：CLS 池化 + L2 归一化，1024 维。

    🔴 不加 query 指令前缀（BGE-M3 官方用法不加中文 prefix，与 small-zh 不同）。
    max_length 与全库缓存生成口径一致（M3_MAX_LEN=512）。
    """
    import numpy as np
    torch, tok, mdl = load_m3_model()
    outs = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        enc = tok(chunk, padding=True, truncation=True,
                  max_length=M3_MAX_LEN, return_tensors="pt")
        with torch.no_grad():
            out = mdl(**enc)
        cls = out.last_hidden_state[:, 0]          # CLS pooling（与缓存口径一致）
        cls = torch.nn.functional.normalize(cls, p=2, dim=1)
        outs.append(cls.numpy().astype("float32"))
        if (i // batch) % 5 == 0:
            print("    %d/%d" % (i + len(chunk), len(texts)), end="\r",
                  file=sys.stderr)
    return np.concatenate(outs, axis=0)


def _read_m3_built_at(conn):
    """读 m3_model 的 built_at；表空 / 没建过时返回 None。"""
    try:
        row = conn.execute("SELECT built_at FROM m3_model WHERE id=1").fetchone()
    except sqlite3.Error:
        return None
    return (row[0] if row else None) or None


def load_m3(conn):
    """懒加载 vec_m3 → 矩阵 + ids + doc 数组，带 built_at 指纹缓存（键名 vm3）。

    与 load_bge 同一套缓存失效思路：vec_m3 重导后 built_at 变化 ⇒ 进程内旧矩阵
    立即失效重载，不会「导完指标纹丝不动」。
    """
    import numpy as np
    fp_db = _read_m3_built_at(conn)
    cached = _MODEL_CACHE.get("vm3")
    if cached is not None:
        if cached.get("built_at") == fp_db:
            return cached
        print("[m3] 向量已更新（built_at 变化），重载常驻矩阵", file=sys.stderr)
    row = conn.execute("SELECT dim,n,built_at FROM m3_model WHERE id=1").fetchone()
    if not row:
        return None
    rows = conn.execute(
        "SELECT chunk_id, vec FROM vec_m3 ORDER BY chunk_id").fetchall()
    if not rows:
        return None
    D = np.stack([np.frombuffer(v, dtype="float32") for _, v in rows])
    ids = [int(c) for c, _ in rows]
    cid2doc = {int(c): int(d) for c, d in conn.execute("SELECT id, doc_id FROM chunks")}
    doc_arr = np.array([cid2doc.get(c, -1) for c in ids], dtype="int64")
    _MODEL_CACHE["vm3"] = {"D": D, "ids": ids, "cid2doc": cid2doc,
                           "doc_arr": doc_arr, "dim": row[0], "n": row[1],
                           "built_at": row[2]}
    return _MODEL_CACHE["vm3"]


def search_bge_m3(conn, query, topk, oversample=None):
    """bge-m3 多语塔检索：doc 级去重逻辑与 search_bge 完全一致，只是换 m3 塔。"""
    import numpy as np
    m = load_m3(conn)
    if not m:
        return []
    qv = encode_m3([query])[0]
    sims = m["D"] @ qv
    doc_arr = m.get("doc_arr")
    if doc_arr is None:  # 老缓存对象（不该出现，纯防御）
        doc_arr = np.array([m["cid2doc"].get(c, -1) for c in m["ids"]],
                           dtype="int64")
    order = np.argsort(-sims)
    n_os = _oversample() if oversample is None else int(oversample)
    if n_os > 0 and topk * n_os < len(order):
        order = order[:topk * n_os]
    _, first = np.unique(doc_arr[order], return_index=True)
    best = {}
    for pos in first:
        idx = order[pos]
        doc_id = int(doc_arr[idx])
        if doc_id < 0:
            continue
        best[doc_id] = (int(m["ids"][idx]), float(sims[idx]))
    out = []
    for doc_id, (cid, s) in sorted(best.items(), key=lambda x: -x[1][1])[:topk]:
        r = conn.execute("SELECT path,title,summary FROM docs WHERE id=?",
                         (doc_id,)).fetchone()
        if r:
            out.append({"doc_id": doc_id, "path": r[0], "title": r[1],
                        "summary": r[2], "score": s})
    return out


def search_fused_bge(conn, query, topk, w_kw=1.0, w_bge=1.0):
    """关键词路 ⊕ BGE 语义路 —— 这是本库真正的「混合检索」该有的样子。"""
    a = search_fts_fused(conn, query, topk * 2)
    b = search_bge(conn, query, topk * 2)
    rrf, meta = {}, {}
    for rank, it in enumerate(a, 1):
        rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + w_kw / (RRF_K + rank)
        meta[it["doc_id"]] = it
    for rank, it in enumerate(b, 1):
        rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + w_bge / (RRF_K + rank)
        meta.setdefault(it["doc_id"], it)
    out = []
    for doc_id, sc in sorted(rrf.items(), key=lambda x: -x[1])[:topk]:
        it = dict(meta[doc_id])
        it["score"] = sc
        out.append(it)
    return out


def cmd_search(args):
    conn = connect()
    hits = (search_fused_bge if args.fused else search_bge)(conn, args.query, args.topk)
    if not hits:
        print("(无结果，或还没 build)")
        return 1
    for i, h in enumerate(hits, 1):
        print("  %d. [%.4f] %s" % (i, h["score"], h["path"]))
        print("     %s" % h["title"])
    conn.close()


def cmd_stats(args):
    conn = connect(readonly=True)
    row = conn.execute("SELECT dim,n,model,built_at FROM bge_model WHERE id=1").fetchone()
    if not row:
        print("还没有 BGE 向量，先跑 build")
        return 1
    print("块数 %d ｜ 维度 %d ｜ 模型 %s ｜ 构建于 %s" % (row[1], row[0], row[2], row[3]))
    # tie_breaker = 编码口径指纹：判断「库里的向量是用哪套口径编的」。
    # 与当前进程口径不一致 ⇒ 库是旧的，必须重编码才能生效。
    fp_db = _read_tie_breaker(conn) or "(无/老库未加列)"
    print("编码口径 tie_breaker：库内=%s ｜ 当前进程=%s ｜ %s" % (
        fp_db, vector_fingerprint(),
        "一致" if fp_db == vector_fingerprint() else "⚠️ 不一致，需重编码"))
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="BGE 语义向量路")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--mode", choices=list(_MODES), default=None,
                   help="编码文本口径：v1=heading+content / v2=+title / "
                        "v3=+title×2（短标题加权）；缺省取 KB_BGE_VTEXT_MODE，再缺省 v1")
    p.add_argument("--max-chars", type=int, default=None,
                   help="编码文本截断长度，缺省取 KB_BGE_VTEXT_MAX_CHARS（1000）")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("search")
    p.add_argument("--query", required=True)
    p.add_argument("--topk", type=int, default=5)
    p.add_argument("--fused", action="store_true", help="关键词⊕BGE 融合")
    p.set_defaults(func=cmd_search)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())