#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 · C 独立检索问答 CLI（🔴 不 import kb_rag.search_unified，保持解耦）

三模式：
  1hop    种子实体 → 1 跳邻居扩展 → chunk 打分
  ppr     副本 PPR（numpy 幂迭代，α=0.85）→ chunk 打分
  hybrid  图召回 ∪ 字符 bigram TF-IDF 向量兜底（零依赖、零 API 成本）

--explain 打印命中路径 A→B→C；--json 输出结构化结果（便于 W10 接 REST）。
兜底口径说明：这里用**字符 bigram TF-IDF 稀疏向量**而非 BGE dense，
为的是让子系统不依赖生产模型与 4.5GB 库；README 里如实标注，不冒充 dense。
"""
import argparse
import json
import math
import os
import pickle
import re
import sqlite3
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kg_ppr as K  # noqa: E402  (cp 副本)

DB = os.path.join(HERE, "state", "kg_w6.db")
GRAPH = os.path.join(HERE, "state", "kg_w6.pkl")


def load_graph():
    if not os.path.isfile(GRAPH):
        raise SystemExit("[ERR] 先跑 kg_build.py --stats 建图")
    with open(GRAPH, "rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------- 向量兜底
def bigrams(s):
    s = re.sub(r"\s+", "", s or "")
    return [s[i:i + 2] for i in range(len(s) - 1)] or [s]


def vector_fallback(conn, query, topk, exclude):
    rows = conn.execute("SELECT id, path, content FROM chunks").fetchall()
    if not rows:
        return []
    qc = Counter(bigrams(query))
    if not qc:
        return []
    df = defaultdict(int)
    for _cid, _p, content in rows:
        for g in set(bigrams(content)):
            df[g] += 1
    n = len(rows)
    idf = {g: math.log((n + 1) / (d + 1)) + 1.0 for g, d in df.items()}

    def vec(text):
        c = Counter(bigrams(text))
        v = {g: (1 + math.log(k)) * idf.get(g, 1.0) for g, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    qv = vec(query)
    scored = []
    for cid, path, content in rows:
        if path in exclude:
            continue
        v = vec(content)
        dot = sum(x * v.get(g, 0.0) for g, x in qv.items())
        if dot > 0:
            scored.append((path, dot, cid))
    scored.sort(key=lambda x: -x[1])
    out, seen = [], set()
    for path, sc, _cid in scored:
        if path in seen:
            continue
        seen.add(path)
        out.append((None, path, round(sc, 6)))
        if len(out) >= topk:
            break
    return out


# ---------------------------------------------------------------- 三模式
def run_1hop(conn, G, query, topk):
    seeds = K.link_entities(query, G)
    if not seeds:
        return [], {"error": "no_seed", "query": query}
    expanded = {}
    for s, w in seeds.items():
        expanded[s] = expanded.get(s, 0) + w
        if G.has_node(s):
            for nb in G.neighbors(s):
                expanded[nb] = expanded.get(nb, 0) + w * 0.5 * float(
                    G[s][nb].get("w", 1.0))
    res = K.score_chunks(conn, expanded, topk=topk)
    chain = [query, "→".join(list(seeds)[:3])]
    if seeds:
        s0 = list(seeds)[0]
        nbs = [n for n in G.neighbors(s0) if not str(n).startswith("P::")][:2] if G.has_node(s0) else []
        chain.append("→".join(nbs) if nbs else "(无 1 跳邻居)")
    chain.append("→".join(r[1].split("/")[-1] for r in res[:2]))
    return res, {"seeds": list(seeds)[:8], "expanded": len(expanded),
                 "path": " → ".join(chain)}


def run_ppr(conn, G, query, topk):
    res, seeds, top_ent = K.search_ppr(conn, query, topk=topk, graph=G,
                                       return_debug=True)
    hits = list((top_ent or {}).items())[:3]
    chain = [query, "→".join(list(seeds)[:3]) or "(无种子)",
             "→".join(e for e, _s in hits) or "(无扩散)",
             "→".join(r[1].split("/")[-1] for r in res[:2])]
    return res, {"seeds": list(seeds)[:8],
                 "top_entities": {e: round(s, 6) for e, s in hits},
                 "path": " → ".join(chain)}


def run_hybrid(conn, G, query, topk):
    res, dbg = run_ppr(conn, G, query, topk)
    exclude = {r[1] for r in res}
    fb = vector_fallback(conn, query, max(1, topk - len(res)), exclude)
    merged = list(res) + fb
    dbg["path"] += " ｜ 向量兜底补 %d 条" % len(fb)
    dbg["fallback"] = {"backend": "char-bigram tfidf (sparse vector)",
                       "paths": [r[1] for r in fb],
                       "note": "非 BGE dense；为保持解耦与零 API 成本"}
    return merged[:topk], dbg


RUNNERS = {"1hop": run_1hop, "ppr": run_ppr, "hybrid": run_hybrid}


def main(argv=None):
    ap = argparse.ArgumentParser(description="W6 独立图谱检索 CLI")
    ap.add_argument("--query", required=True)
    ap.add_argument("--mode", choices=list(RUNNERS), default="ppr")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--explain", action="store_true", help="打印命中路径 A→B→C")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(a.db)
    try:
        G = load_graph()
        res, dbg = RUNNERS[a.mode](conn, G, a.query, a.topk)
        out = {
            "query": a.query, "mode": a.mode, "topk": a.topk,
            "graph": {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
            "results": [{"rank": i + 1, "path": r[1], "score": round(float(r[2]), 6)}
                        for i, r in enumerate(res)],
            "n_results": len(res),
        }
        if a.explain or a.json:
            out["explain"] = dbg
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print("[%s] %s → %d 条" % (a.mode, a.query, len(res)))
            for i, r in enumerate(res):
                print("  %d. %-6.4f  %s" % (i + 1, float(r[2]), r[1]))
            if a.explain:
                print("  path: %s" % dbg.get("path", ""))
                if "fallback" in dbg:
                    print("  fallback: %s" % dbg["fallback"]["backend"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
