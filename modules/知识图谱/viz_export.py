#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 · D 可视化数据导出（复用 build_kg_viz.py 副本渲染 HTML）

🔴 图数据直接取 `state/kg_w6.pkl` —— 就是 kg_search.py 真正用于检索的那张增强图，
   保证「图上看到的簇」和「PPR 用的图」是同一张，不另造一张好看的图。
输出 build_kg_viz.py 兼容的 JSON：全局图（实体层）+ 某实体 2 跳邻居局部图（含段落节点）。
"""
import argparse
import json
import os
import pickle
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(HERE, "state", "kg_w6.pkl")
OUT = os.path.normpath(os.path.join(HERE, "..", "..", "outputs", "知识图谱", "W6"))


def records(G, keep, max_links=900):
    keep = set(keep)
    sub = G.subgraph(keep)
    deg = dict(sub.degree())
    ws = [float(d.get("w", 1.0)) for _u, _v, d in sub.edges(data=True)] or [1.0]
    wmax = max(max(ws), 1e-6)
    links = [{"source": u, "target": v,
              "w": round(max(0.0, float(d.get("w", 1.0))) / wmax * 100, 2),
              "rel": d.get("rel", d.get("kind", "共现"))}
             for u, v, d in sub.edges(data=True)]
    links.sort(key=lambda x: -x["w"])
    links = links[:max_links]
    nodes = [{"id": n, "df": int(G.nodes[n].get("df", 1)),
              "type": str(G.nodes[n].get("type", "entity")),
              "deg": int(deg.get(n, 0))} for n in keep]
    return nodes, links


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出 W6 可视化 JSON")
    ap.add_argument("--graph", default=GRAPH)
    ap.add_argument("--out-dir", default=OUT)
    ap.add_argument("--seed", default="提示词", help="局部图中心实体")
    ap.add_argument("--max-nodes", type=int, default=150)
    ap.add_argument("--max-links", type=int, default=900)
    a = ap.parse_args(argv)

    with open(a.graph, "rb") as f:
        G = pickle.load(f)
    os.makedirs(a.out_dir, exist_ok=True)

    # 全局图：只看实体层（P:: 段落节点在局部图里展示，否则会淹没实体）
    ents = [n for n in G.nodes if not str(n).startswith("P::")]
    ents.sort(key=lambda n: -G.degree(n))
    g_nodes, g_links = records(G, ents[:a.max_nodes], a.max_links)
    import sqlite3
    db = os.path.join(HERE, "state", "kg_w6.db")
    docs = sqlite3.connect(db).execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    g_meta = {"docs": docs, "nodes": len(g_nodes), "links": len(g_links),
              "source": "kg_w6.pkl / entity layer", "seed": None}
    p1 = os.path.join(a.out_dir, "global_graph.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump({"meta": g_meta, "nodes": g_nodes, "links": g_links}, f,
                  ensure_ascii=False, indent=1)
    print("[viz] 全局图 → %s（%d 节点 / %d 边）" % (p1, len(g_nodes), len(g_links)))

    # 局部图：中心实体 2 跳邻居（保留段落节点，展示 B2 的段落层）
    if not G.has_node(a.seed):
        print("[WARN] 图中无实体 %r，局部图改用度数最高的实体" % a.seed)
        a.seed = ents[0]
    try:
        import networkx as nx
        ego = nx.ego_graph(G, a.seed, radius=2, undirected=True)
        keep = list(ego.nodes)[:a.max_nodes]
    except Exception as exc:
        print("[WARN] ego_graph 失败(%s)，退化为 1 跳" % exc)
        keep = [a.seed] + [n for n in G.neighbors(a.seed)][:a.max_nodes - 1]
    e_nodes, e_links = records(G, keep, a.max_links)
    e_meta = {"docs": docs, "nodes": len(e_nodes), "links": len(e_links),
              "source": "kg_w6.pkl / 2-hop ego (incl. passage nodes)", "seed": a.seed}
    p2 = os.path.join(a.out_dir, "ego_2hop_graph.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"meta": e_meta, "nodes": e_nodes, "links": e_links}, f,
                  ensure_ascii=False, indent=1)
    print("[viz] 局部图 → %s（中心=%s，%d 节点 / %d 边）"
          % (p2, a.seed, len(e_nodes), len(e_links)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
