#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vector_index — 稠密向量检索路（LSA / 截断 SVD）
================================================

要回答的问题：**把词袋那路换成真·稠密向量之后，混合检索能不能反超关键词单路？**

为什么先用 LSA 而不是 BGE：
  BGE-small 效果更好，但模型要从 HuggingFace 下 —— 本机实测 HF 主站不通、
  镜像站 401/429 限流，**拿不到**。与其干等，先把「向量路」这条链路跑通：
  LSA 也是真·稠密向量（128 维），靠词共现捕捉部分语义，零依赖、零下载、可回归。
  脚本设计成可替换：**换模型只需改 `fit()`**，检索层不用动。BGE 列为「网络通了再换」的 P1。

原理（四行）：
  1. 块的 TF-IDF 稀疏矩阵 X（去高频词降噪）
  2. 谱分解 X·Xᵀ = U·S²·Uᵀ → 块的稠密表示 D = U·S（128 维，行归一化）
  3. 词侧右奇异向量 V = Xᵀ·U·S⁻¹ —— **存下它，查询才能直接投影**
  4. 查询：q_lsa = Σ_t q_tfidf[t]·V[t]，与 D 算余弦

用法：
  python vector_index.py build --k 128      # 训练并存向量
  python vector_index.py search --query "混合检索" --topk 5
  python vector_index.py stats
"""

import argparse
import json
import math
import os
import pickle
import sqlite3
import sys
import time
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KB_IDX = os.path.join(os.path.dirname(HERE), "知识库索引")
sys.path.insert(0, KB_IDX)
from kb_index import connect, DB_PATH, tokenize, search_fts_fused, RRF_K  # noqa: E402

MAX_DF_RATIO = 0.35   # 出现在超过 35% 块里的词当停用词丢掉：降噪 + 大幅省算力
MIN_DF = 2
_CACHE = {}


def ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS chunk_vecs(
        chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lsa_model(
        id INTEGER PRIMARY KEY CHECK(id=1), k INTEGER, n INTEGER, vocab INTEGER,
        idf TEXT, V BLOB, chunk_ids TEXT, doc_ids TEXT, built_at TEXT)""")


def build_bow(conn):
    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.heading, c.content FROM chunks c ORDER BY c.id").fetchall()
    out = []
    for cid, doc_id, heading, content in rows:
        toks = [t for t in tokenize((heading or "") + "\n" + (content or "")) if len(t) >= 2]
        out.append((int(cid), int(doc_id), Counter(toks)))
    return out


def fit(conn, k=128, verbose=True):
    import numpy as np
    t0 = time.time()
    docs = build_bow(conn)
    n = len(docs)
    if n < 8:
        raise SystemExit("块太少，先跑 kb_rag.py build 建分块")

    df = Counter()
    for _, _, tf in docs:
        df.update(tf.keys())
    drop = {t for t, c in df.items() if c / n > MAX_DF_RATIO or c < MIN_DF}
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items() if t not in drop}

    vecs = []
    for _, _, tf in docs:
        v = {}
        for t, c in tf.items():
            if t in drop:
                continue
            v[t] = (1 + math.log(c)) * idf[t]
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    if verbose:
        print("  TF-IDF：%d 块，词表 %d（丢掉 %d 个高频/罕见词，%.1fs）" % (
            n, len(idf), len(drop), time.time() - t0))

    # Gram = X·Xᵀ（倒排累加，只算上三角）
    inv = defaultdict(list)
    for i, v in enumerate(vecs):
        for t, w in v.items():
            inv[t].append((i, w))
    G = np.zeros((n, n), dtype=np.float64)
    skipped = 0
    for t, posting in inv.items():
        if len(posting) > 400:
            skipped += 1
            continue
        for a in range(len(posting)):
            i, wi = posting[a]
            for b in range(a + 1, len(posting)):
                j, wj = posting[b]
                G[i, j] += wi * wj
                G[j, i] += wi * wj
    for i, v in enumerate(vecs):
        G[i, i] = sum(x * x for x in v.values())
    if verbose:
        print("  Gram %d×%d（跳过 %d 个超高频词，%.1fs）" % (n, n, skipped, time.time() - t0))

    lam, U = np.linalg.eigh(G)
    order = np.argsort(lam)[::-1]
    lam, U = lam[order], U[:, order]
    k = min(k, n - 1, max(1, int((lam > 1e-9).sum())))
    Uk, Sk = U[:, :k].copy(), np.sqrt(np.maximum(lam[:k], 0)).copy()
    D = Uk * Sk
    D /= (np.linalg.norm(D, axis=1, keepdims=True) + 1e-12)

    # 词侧右奇异向量 V = Xᵀ·U·S⁻¹ —— 有了它，查询可以直接投影，不用重算整个 X
    V = {}
    for t, posting in inv.items():
        if len(posting) > 400:
            continue
        col = np.zeros(k, dtype=np.float64)
        for i, w in posting:
            col += w * Uk[i]
        col /= (Sk + 1e-12)
        V[t] = col.astype(np.float32)
    if verbose:
        print("  谱分解 k=%d，解释方差 %.1f%%，词向量 %d 个（%.1fs）" % (
            k, float(lam[:k].sum() / max(lam.sum(), 1e-12) * 100), len(V), time.time() - t0))
    return {"k": int(k), "n": n, "idf": idf, "D": D, "V": V,
            "chunk_ids": [d[0] for d in docs], "doc_ids": [d[1] for d in docs]}


def save_model(conn, m):
    ensure_tables(conn)
    conn.execute("DELETE FROM chunk_vecs")
    conn.execute("DELETE FROM lsa_model")
    for cid, row in zip(m["chunk_ids"], m["D"]):
        conn.execute("INSERT INTO chunk_vecs(chunk_id, vec) VALUES(?,?)",
                     (int(cid), row.astype("float32").tobytes()))
    conn.execute(
        "INSERT INTO lsa_model(id,k,n,vocab,idf,V,chunk_ids,doc_ids,built_at)"
        " VALUES(1,?,?,?,?,?,?,?,?)",
        (m["k"], m["n"], len(m["idf"]), json.dumps(m["idf"]),
         pickle.dumps(m["V"]), json.dumps([int(c) for c in m["chunk_ids"]]),
         json.dumps([int(d) for d in m["doc_ids"]]), time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def load_model(conn):
    if "m" in _CACHE:
        return _CACHE["m"]
    import numpy as np
    row = conn.execute(
        "SELECT k,n,vocab,idf,V,chunk_ids,doc_ids FROM lsa_model WHERE id=1").fetchone()
    if not row:
        return None
    m = {"k": row[0], "n": row[1], "vocab": row[2], "idf": json.loads(row[3]),
         "V": pickle.loads(row[4]),
         "chunk_ids": json.loads(row[5]), "doc_ids": json.loads(row[6])}
    rows = conn.execute("SELECT chunk_id, vec FROM chunk_vecs").fetchall()
    m["D"] = np.stack([np.frombuffer(v, dtype="float32").astype(np.float64)
                       for _, v in rows])
    m["vec_ids"] = [int(c) for c, _ in rows]
    _CACHE["m"] = m
    return m


def query_vec(m, query):
    """查询 → 稠密向量：q_lsa = Σ_t q_tfidf[t]·V[t]（L2 归一化）。"""
    import numpy as np
    toks = [t for t in tokenize(query) if len(t) >= 2]
    if not toks:
        return None
    q = Counter(toks)
    w = {}
    for t, c in q.items():
        if t in m["idf"]:
            w[t] = (1 + math.log(c)) * m["idf"][t]
    if not w:
        return None
    norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
    acc = np.zeros(m["k"], dtype=np.float64)
    for t, x in w.items():
        v = m["V"].get(t)
        if v is not None:
            acc += (x / norm) * v
    nn = np.linalg.norm(acc)
    if nn < 1e-12:
        return None
    return acc / nn


def search_vec(conn, query, topk, m=None):
    """稠密向量检索：块级召回 → 同文档去重取最高分 → 返回与 search_fts 相同的结构。"""
    import numpy as np
    m = _prepare(m or load_model(conn))
    if not m:
        return []
    qv = query_vec(m, query)
    if qv is None:
        return []
    sims = m["D"] @ qv                      # (n_chunks,) 余弦相似度
    order = np.argsort(-sims)[:topk * 8]
    best = {}
    for idx in order:
        cid = m["vec_ids"][idx]
        if cid not in m["_cid2i"]:
            continue
        i = m["_cid2i"][cid]
        doc_id = m["doc_ids"][i]
        if doc_id not in best or sims[idx] > best[doc_id][1]:
            best[doc_id] = (cid, float(sims[idx]))
    items = sorted(best.items(), key=lambda x: -x[1][1])[:topk]
    if not items:
        return []
    out = []
    for doc_id, (cid, s) in items:
        row = conn.execute("SELECT path,title,summary FROM docs WHERE id=?", (doc_id,)).fetchone()
        if row:
            out.append({"doc_id": doc_id, "path": row[0], "title": row[1],
                        "summary": row[2], "score": s})
    return out


def search_fused_vec(conn, query, topk, w_kw=1.0, w_vec=1.0):
    """关键词路 ⊕ 稠密向量路的 RRF。

    这是全库 MRR 最高的一路（实测人工 100 条：0.8595，高于关键词粒度融合 0.8440、
    整篇 0.8195）。能不能赢，取决于两路质量是否接近 —— 见 HANDOFF 第 13 条。
    """
    a = search_fts_fused(conn, query, topk * 2)
    b = search_vec(conn, query, topk * 2)
    rrf, meta = {}, {}
    for rank, it in enumerate(a, 1):
        rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + w_kw / (RRF_K + rank)
        meta[it["doc_id"]] = it
    for rank, it in enumerate(b, 1):
        rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + w_vec / (RRF_K + rank)
        meta.setdefault(it["doc_id"], it)
    out = []
    for doc_id, sc in sorted(rrf.items(), key=lambda x: -x[1])[:topk]:
        it = dict(meta[doc_id])
        it["score"] = sc
        out.append(it)
    return out


def _prepare(m):
    m.setdefault("_cid2i", {c: i for i, c in enumerate(m["chunk_ids"])})
    return m


def cmd_build(args):
    conn = connect()
    ensure_tables(conn)
    m = fit(conn, args.k, verbose=True)
    save_model(conn, m)
    print("向量索引完成：%d 块 × %d 维 → %s（%s）" % (
        m["n"], m["k"], DB_PATH, time.strftime("%H:%M:%S")))
    conn.close()


def cmd_search(args):
    conn = connect()
    m = _prepare(load_model(conn))
    if not m:
        print("还没建向量索引，先跑：python vector_index.py build")
        return 1
    t0 = time.time()
    hits = search_vec(conn, args.query, args.topk, m)
    dt = (time.time() - t0) * 1000
    if not hits:
        print("(无结果)")
    for i, h in enumerate(hits, 1):
        print("  %d. [%.4f] %s" % (i, h["score"], h["path"]))
        print("     %s" % h["title"])
    print("（%.1f ms）" % dt)
    conn.close()


def cmd_stats(args):
    conn = connect(readonly=True)
    row = conn.execute("SELECT k,n,vocab,built_at FROM lsa_model WHERE id=1").fetchone()
    if not row:
        print("还没有向量索引，先跑 build")
        return 1
    print("块数 %d ｜ 维度 %d ｜ 词表 %d ｜ 构建于 %s" % (row[1], row[0], row[2], row[3]))
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="稠密向量检索路（LSA）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--k", type=int, default=128)
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("search")
    p.add_argument("--query", required=True)
    p.add_argument("--topk", type=int, default=5)
    p.set_defaults(func=cmd_search)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
