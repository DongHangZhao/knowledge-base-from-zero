#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 · B1–B5 建图升级层

不改 cp 副本里的任何算法，只做三件事：
  · B1 传 synonym=True 打开副本里已写好的 add_synonym_edges()；
  · B2 在图上叠加显式段落节点 + 短语→段落上下文边；
  · B3 叠加零 LLM 中文规则模板抽出的**有类型关系边**（rel 属性）；
  · B4 落盘规模三数（节点/边/覆盖篇数）+ 建图耗时；
  · B5 --add 只把新文档的节点/边 merge 进已有图，不重建。

用法：
  python kg_build.py --stats          # 建全图 + 落盘 state/kg_w6.pkl
  python kg_build.py --ablate         # B1/B2/B3 改前 vs 改后真实查询对照
  python kg_build.py --add <file.md>  # B5 增量
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
import kg_ppr as K                      # noqa: E402  (cp 副本)
from build_corpus import connect, ingest_one, load_terms, TERMS_DEFAULT  # noqa: E402

DB = os.path.join(HERE, "state", "kg_w6.db")
GRAPH = os.path.join(HERE, "state", "kg_w6.pkl")
STATS = os.path.join(HERE, "state", "graph_stats.json")

# B1 只启用**通用技术别名**：alias_table.json 里混有生产侧业务别名组，
# 白名单以外的键一律不进图（红线：不用真实业务专名建图）。
GENERIC_KEYS = ("智能体", "工作流", "提示词", "提示词注入", "rerank", "rag", "flask")


def load_alias_map():
    p = os.path.join(HERE, "alias_table.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items()
                if k in GENERIC_KEYS and isinstance(v, list)}
    except Exception:
        return {}


# ---------------------------------------------------------------- B2 / B3 叠加层
def enrich(G, conn, with_passage, with_typed, only_chunk_ids=None):
    """返回 (新增节点数, 新增边数)。only_chunk_ids 给定时只处理这些块（B5 增量用）。"""
    added_n = added_e = 0
    if with_passage:
        rows = conn.execute("SELECT chunk_id,path,idx,heading FROM passages ORDER BY chunk_id")
        for cid, path, _idx, _heading in rows:
            if only_chunk_ids is not None and cid not in only_chunk_ids:
                continue
            node = "P::%d" % cid
            if not G.has_node(node):
                G.add_node(node, df=1, type="passage", path=path)
                added_n += 1
            for (e,) in conn.execute(
                    "SELECT entity FROM chunk_entities WHERE chunk_id=?", (cid,)):
                if G.has_node(e) and not G.has_edge(node, e):
                    G.add_edge(node, e, w=0.35, kind="phrase_passage")
                    added_e += 1
    if with_typed:
        sql = "SELECT src,rel,dst,chunk_id FROM typed_edges"
        rows = list(conn.execute(sql))
        for src, rel, dst, cid in rows:
            if only_chunk_ids is not None and cid not in only_chunk_ids:
                continue
            for x in (src, dst):
                if not G.has_node(x):
                    G.add_node(x, df=1, type="typed_entity")
                    added_n += 1
            if not G.has_edge(src, dst):
                G.add_edge(src, dst, w=0.60, rel=rel, kind="typed")
                added_e += 1
            else:
                G[src][dst]["rel"] = rel
                G[src][dst]["kind"] = "typed"
    return added_n, added_e


def base_graph(conn, args, synonym):
    return K.build_graph(conn, min_df=args.min_df, min_w=args.min_w,
                         idf_weight=True, weighting="npmi", synonym=synonym,
                         alias_map=load_alias_map())


# ---------------------------------------------------------------- B4
def do_stats(conn, args):
    t0 = time.time()
    G, st = base_graph(conn, args, synonym=True)   # B1 开
    n, e = enrich(G, conn, with_passage=True, with_typed=True)
    elapsed = round(time.time() - t0, 3)
    with open(GRAPH, "wb") as f:
        pickle.dump(G, f, protocol=4)
    docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    typed = conn.execute("SELECT COUNT(*) FROM typed_edges").fetchone()[0]
    passages = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    data = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "covered_docs": docs,
        "covered_chunks": chunks,
        "build_elapsed_s": elapsed,
        "b1_synonym_edges": st.get("synonym_edges", 0),
        # 副本对「已存在的共现边」是提权 + 打 synonym 标记，不进 added 列表；
        # 只数新增边会漏报，必须连标记边一起统计。
        "b1_synonym_tagged_edges": sum(
            1 for _u, _v, d in G.edges(data=True) if d.get("synonym")),
        "b2_passage_nodes": passages,
        "b3_typed_edges": typed,
        "enriched_nodes": n,
        "enriched_edges": e,
        "base_graph": {k: st[k] for k in ("nodes", "edges", "avg_degree", "density")
                       if k in st},
        "coverage_note": ("脱敏语料池上限：生产库含真实业务/隐私正文，红线禁止用于对外可展示"
                          "子系统；故覆盖篇数止于公开示例 + 方法学页，未达 2000 篇量级。"),
    }
    with open(STATS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("[kg_build] graph → %s" % GRAPH)
    return 0


# ---------------------------------------------------------------- B1/B2/B3 改前 vs 改后
def real_queries(conn, args, k=3):
    """从**建好的图节点**里挑真实实体做查询，保证 link_entities 有种子。

    第一版直接取 entity_stats 的 df 冠军（The / Recall / RRF），结果它们全被
    build_graph 的 max_df_ratio 当泛词剔掉 → 空种子 → 空对空，对照无效。
    改为从图里真实存在的节点里挑中文优先、df 适中的实体。
    """
    G, _ = base_graph(conn, args, synonym=False)
    cands = []
    for n in G.nodes:
        if not isinstance(n, str) or n.startswith("P::"):
            continue
        df = G.nodes[n].get("df", 1)
        if df < 2:
            continue
        zh = any("\u4e00" <= ch <= "\u9fff" for ch in n)
        cands.append((0 if zh else 1, -df, n))
    cands.sort()
    return [c[2] for c in cands[:k]]


def top_paths(conn, G, query, topk=5):
    res = K.search_ppr(conn, query, topk=topk, graph=G)
    return [r[1] for r in res]


def entity_profile(conn, G, query, k=8):
    """PPR 实体分布：比 top5 文档更敏感，能取证「图结构真的变了」。"""
    seeds = K.link_entities(query, G)
    es = K.ppr(G, seeds) if seeds else {}
    return {e: round(float(s), 6) for e, s in
            sorted(es.items(), key=lambda kv: -kv[1])[:k]}


def do_ablate(conn, args):
    queries = real_queries(conn, args)
    # 补一条真实落在「有类型关系边」上的查询，避免 B3 对照全是无关种子
    G0, _ = base_graph(conn, args, synonym=False)
    for (src,) in conn.execute("SELECT DISTINCT src FROM typed_edges ORDER BY chunk_id"):
        if isinstance(src, str) and len(src) >= 3 and G0.has_node(src):
            queries.append(src)
            break
    cases = [
        ("B1_同义词边", dict(synonym=False, passage=False, typed=False),
         dict(synonym=True, passage=False, typed=False)),
        ("B2_段落节点", dict(synonym=True, passage=False, typed=False),
         dict(synonym=True, passage=True, typed=False)),
        ("B3_有类型边", dict(synonym=True, passage=True, typed=False),
         dict(synonym=True, passage=True, typed=True)),
    ]
    cache, out = {}, {"queries": queries, "cases": []}
    for name, before, after in cases:
        row = {"case": name, "before": [], "after": []}
        for q in queries:
            for tag, cfg in (("before", before), ("after", after)):
                key = (cfg["synonym"], cfg["passage"], cfg["typed"])
                if key not in cache:
                    G, _ = base_graph(conn, args, synonym=cfg["synonym"])
                    enrich(G, conn, cfg["passage"], cfg["typed"])
                    cache[key] = G
                G = cache[key]
                paths = top_paths(conn, G, q)
                row[tag].append({"query": q, "nodes": G.number_of_nodes(),
                                 "edges": G.number_of_edges(), "top5": paths,
                                 "ppr_top_entities": entity_profile(conn, G, q)})
        row["changed_queries"] = sum(
            1 for b, a in zip(row["before"], row["after"]) if b["top5"] != a["top5"])
        row["changed_entity_profiles"] = sum(
            1 for b, a in zip(row["before"], row["after"])
            if b["ppr_top_entities"] != a["ppr_top_entities"])
        out["cases"].append(row)
    p = os.path.join(HERE, "state", "ablate_b1b2b3.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("[kg_build] 对照 → %s" % p)
    return 0


# ---------------------------------------------------------------- B5 增量
def do_add(conn, args):
    if not os.path.isfile(GRAPH):
        print("[BLOCK] 先跑 --stats 建图，B5 增量不重建")
        return 2
    terms = load_terms(TERMS_DEFAULT)
    rel = "incremental/" + os.path.basename(args.add)
    t0 = time.time()
    st, n, hits, _soft = ingest_one(conn, os.path.abspath(args.add), rel,
                                    "incremental", terms)
    if st != "ok":
        print("[BLOCK] 语料未通过脱敏/重复：status=%s hits=%s" % (st, hits))
        return 1
    with open(GRAPH, "rb") as f:
        G = pickle.load(f)
    before = (G.number_of_nodes(), G.number_of_edges())
    cids = {r[0] for r in conn.execute(
        "SELECT c.id FROM chunks c JOIN docs d ON c.doc_id=d.id WHERE d.path=?", (rel,))}
    an, ae = enrich(G, conn, with_passage=True, with_typed=True, only_chunk_ids=cids)
    with open(GRAPH, "wb") as f:
        pickle.dump(G, f, protocol=4)
    out = {
        "added_doc": rel, "added_chunks": n, "rebuild": False,
        "graph_before": list(before),
        "graph_after": [G.number_of_nodes(), G.number_of_edges()],
        "merged_nodes": an, "merged_edges": ae,
        "elapsed_s": round(time.time() - t0, 3),
        "docs_now": conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="W6 B1–B5 建图升级层")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--stats", action="store_true", help="B4 建全图 + 规模三数")
    ap.add_argument("--ablate", action="store_true", help="B1/B2/B3 改前改后对照")
    ap.add_argument("--add", default=None, help="B5 增量插入一篇 md")
    ap.add_argument("--min-df", type=int, default=1)
    ap.add_argument("--min-w", type=int, default=1)
    a = ap.parse_args(argv)
    conn = connect(a.db)
    try:
        if a.add:
            return do_add(conn, a)
        if a.ablate:
            return do_ablate(conn, a)
        return do_stats(conn, a)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
