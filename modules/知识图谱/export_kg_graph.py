#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_kg_graph.py — 从实体侧表导出可视化用的图数据（nodes / edges）

做什么：
    chunk_entities 是「块 → 实体」的二分表，人看不懂。
    这里把它投影成「实体 → 实体」的共现图：
      节点 = 实体（大小按文档频次 df，颜色按类型）
      边   = 两个实体在同一个 chunk 里同时出现（权重 = 共现块数）
    再裁到可读规模，输出 JSON 给 HTML 画。

为什么要裁剪：
    v3 侧表有 4821 个实体、可能上万条边。全画出来是一团毛线，
    讲不出任何东西。裁剪策略按「信息量」而不是「前 N 条」：
      ① 只保留 df >= min_df 的实体（df=1 的多半是噪声）
      ② 边权 >= min_w（共现 1 次的边绝大多数是巧合）
      ③ 取最大连通分量（孤立的小团看不出结构）
      ④ 超上限时按「节点度数」截断，保枢纽丢边缘

用法：
    python export_kg_graph.py --db <库> --out <json> \
        --min-df 3 --min-w 2 --max-nodes 120

输出 JSON 结构：
    {
      "meta": {...参数与统计...},
      "nodes": [{"id","df","type","deg"}...],
      "links": [{"source","target","w"}...]
    }
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")


def export_npmi(args):
    """NPMI 图 + Louvain 社区 → 可视化数据。

    与 raw 版的最大区别：**主题岛不再是「连通分量」，而是 Louvain 社区**。
    共现图在 NPMI 加权后仍然是连通的（70 万条边），连通分量只剩 1 个，
    画出来是一整坨；Louvain 会按「内部边密度」切开，才能看出主题结构。

    选点也要按社区分配名额，否则取 top-N 度数会被最大的那个社区占满，
    图上还是只有一片 —— 那等于没做社区检测。
    """
    import kg_ppr

    db = os.path.abspath(args.db)
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    G, st = kg_ppr.build_graph(conn, min_df=args.min_df, min_w=args.min_w,
                               weighting="npmi")
    print("[graph] NPMI 图：%d 节点 / %d 边 / 覆盖文档 %d / 平均度 %.0f"
          % (st["nodes"], st["edges"], st.get("graph_docs", 0), st["avg_degree"]))

    comm, sizes = kg_ppr.communities(G, resolution=args.resolution)
    print("[graph] Louvain 社区 %d 个 ｜ 前 8 大 %s" % (len(sizes), sizes[:8]))

    # 按社区分配名额：大社区多给，但每个社区至少 3 个
    deg = dict(G.degree(weight="w"))
    by_comm = defaultdict(list)
    for n, c in comm.items():
        if n in G and G.degree(n) > 0:
            by_comm[c].append(n)
    # 只留最大的若干社区：25 个岛在侧栏排成一长条、图上还挤成一团，
    # 信息量反而不如「8 个大岛 + 其余合并」。
    order = sorted(by_comm, key=lambda c: -len(by_comm[c]))[:args.comps]
    # 名额按「社区规模占全部选中社区的比例」分。
    # 早期写成 /len(order)（社区个数），第一个社区一次吃掉全部预算，
    # 结果「覆盖 1 个社区」——图上还是单色。
    total_pool = sum(len(by_comm[c]) for c in order) or 1
    budget = args.max_nodes
    quota = {}
    for c in order:
        if budget <= 0:
            break
        n_alloc = max(3, int(round(args.max_nodes * len(by_comm[c]) / total_pool)))
        quota[c] = min(n_alloc, len(by_comm[c]), budget)
        budget -= quota[c]

    keep = set()
    for c, k in quota.items():
        top = sorted(by_comm[c], key=lambda n: (-deg.get(n, 0), -G.nodes[n].get("df", 1)))
        keep.update(top[:k])
    print("[graph] 选点 %d 个（覆盖 %d 个社区）" % (len(keep), len(quota)))

    # 边只留两端都在 keep 里的；权重按 NPMI 归一化到 0-100 便于可视化。
    # 再按权重取前 max_links 条：130 个点配 3200 条边平均度 49，
    # 画出来是毛线球，看不出结构。只留最强的那些边，结构反而清楚。
    cand = []
    wmax = max([d.get("w", 0) for _u, _v, d in G.edges(data=True)] or [1])
    for u, v, d in G.edges(data=True):
        if u in keep and v in keep:
            cand.append((d.get("w", 0), u, v))
    cand.sort(reverse=True)
    if args.max_links and len(cand) > args.max_links:
        cand = cand[:args.max_links]
    edges = [{"source": u, "target": v, "w": round(w / wmax * 100, 2)}
             for w, u, v in cand]

    nodes = [{"id": n,
              "df": G.nodes[n].get("df", 1),
              "type": G.nodes[n].get("type", "noun"),
              "deg": G.degree(n),
              "comp": comm.get(n, -1)} for n in keep]
    nodes.sort(key=lambda x: (-x["deg"], -x["df"]))

    out = {
        "meta": {
            "db": os.path.basename(db),
            "min_df": args.min_df, "min_w": args.min_w,
            "max_nodes": args.max_nodes, "seed": None,
            "nodes": len(nodes), "links": len(edges),
            "docs": st.get("graph_docs", 0),
            "chunks": st.get("total_chunks", 0),
            "dict_size": 0, "mentions": st.get("pairs_raw", 0),
            "weighting": "npmi",
            "communities": len(sizes),
            "built_at": __import__("datetime").datetime.now().isoformat(
                timespec="seconds"),
        },
        "nodes": nodes,
        "links": edges,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[graph] 已写出 %d 节点 / %d 边 → %s（%s 字节）"
          % (len(nodes), len(edges), args.out,
             "{:,}".format(os.path.getsize(args.out))))
    print("[graph] 度数最高 8 个：%s" % [(n["id"], n["deg"]) for n in nodes[:8]])
    conn.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-df", type=int, default=3, help="实体最小文档频次")
    ap.add_argument("--min-w", type=int, default=2, help="边最小共现块数")
    ap.add_argument("--max-nodes", type=int, default=120)
    ap.add_argument("--comps", type=int, default=8,
                    help="保留几个连通分量（主题岛）；默认 8")
    ap.add_argument("--max-links", type=int, default=900,
                    help="最多保留多少条边（按权重取强边）")
    ap.add_argument("--resolution", type=float, default=1.0,
                    help="Louvain 分辨率（越大社区越多越碎）")
    ap.add_argument("--weighting", choices=["raw", "npmi"], default="raw",
                    help="边权算法：raw=共现次数；npmi=归一化点互信息（与 kg_ppr 一致）")
    ap.add_argument("--seed", default=None,
                    help="只导出与该实体 2 跳内相连的子图（主题视图）")
    args = ap.parse_args(argv)

    # NPMI + 社区模式：直接复用 kg_ppr 的建图，
    # 保证「可视化里看到的图」和「PPR 检索真正用的图」是同一张 ——
    # 否则图上漂亮的簇在检索里根本不存在，分析半天对不上。
    if getattr(args, "weighting", "raw") == "npmi":
        return export_npmi(args)

    db = os.path.abspath(args.db)
    conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)

    # ---- 1. 取实体及其 df ----
    rows = conn.execute(
        "SELECT entity, df, ent_type FROM entity_stats WHERE df >= ?",
        (args.min_df,)).fetchall()
    allow = {r[0]: (r[1], r[2]) for r in rows}
    print("[graph] df>=%d 的实体：%d 个" % (args.min_df, len(allow)))
    if not allow:
        print("[ERR] 没有实体，先跑 build_sidecar_v3.py", file=sys.stderr)
        return 2

    # ---- 2. 共现统计 ----
    # 按 chunk 分组，块内实体两两配对。块内实体数通常 <15，配对成本可接受。
    ph = ",".join("?" * min(len(allow), 900))
    # 大表用分批 IN 查询
    allow_list = list(allow)
    pair = defaultdict(int)
    B = 900
    for i in range(0, len(allow_list), B):
        chunk_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT chunk_id FROM chunk_entities "
            "WHERE entity IN (%s)" % ",".join("?" * len(allow_list[i:i + B])),
            tuple(allow_list[i:i + B]))]
        if not chunk_ids:
            continue
        for j in range(0, len(chunk_ids), 500):
            cc = chunk_ids[j:j + 500]
            bychunk = defaultdict(list)
            for cid, ent in conn.execute(
                    "SELECT chunk_id, entity FROM chunk_entities "
                    "WHERE chunk_id IN (%s)" % ",".join("?" * len(cc)),
                    tuple(cc)):
                if ent in allow:
                    bychunk[cid].append(ent)
            for cid, ents in bychunk.items():
                ents = sorted(set(ents))
                for a in range(len(ents)):
                    for b in range(a + 1, len(ents)):
                        pair[(ents[a], ents[b])] += 1

    print("[graph] 共现对：%d 个（含权重 1）" % len(pair))
    edges = {k: v for k, v in pair.items() if v >= args.min_w}
    print("[graph] 边权>=%d：%d 条" % (args.min_w, len(edges)))

    # ---- 3. 主题子图（可选）----
    if args.seed:
        adj = defaultdict(set)
        for a, b in edges:
            adj[a].add(b)
            adj[b].add(a)
        keep, frontier = {args.seed}, {args.seed}
        for _ in range(2):
            nxt = set()
            for n in frontier:
                nxt |= adj.get(n, set())
            keep |= nxt
            frontier = nxt
        edges = {k: v for k, v in edges.items() if k[0] in keep and k[1] in keep}
        print("[graph] 主题子图（%s 的 2 跳）：%d 实体 / %d 边"
              % (args.seed, len(keep), len(edges)))

    # ---- 4. 度数 + 裁剪 ----
    deg = defaultdict(int)
    for a, b in edges:
        deg[a] += 1
        deg[b] += 1

    nodes_in_edges = {n for k in edges for n in k}
    if len(nodes_in_edges) > args.max_nodes:
        # 按度数排序，保枢纽
        ranked = sorted(nodes_in_edges, key=lambda n: (-deg[n], -allow[n][0]))
        keep = set(ranked[:args.max_nodes])
        edges = {k: v for k, v in edges.items() if k[0] in keep and k[1] in keep}
        nodes_in_edges = keep
        print("[graph] 截断到 %d 个节点（按度数优先）" % args.max_nodes)

    # ---- 5. 连通分量（保留若干主题岛，不只留最大的）----
    # 早期版本只取最大连通分量，结果把「集群项目簇」「公众号日更簇」全丢了，
    # 图上只剩「Agent/RAG/MCP」这一片 AI 技术簇 —— 看起来像「知识库只有 AI 内容」，
    # 与事实不符。知识库的价值恰恰在于它是**多个主题岛**，
    # 所以保留前 N 个岛、每岛内单独布局，视觉上也更像星图。
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        stack, comp = [n], []
        seen.add(n)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(comp)
    comps.sort(key=len, reverse=True)

    comp_id = {}
    kept_comps = [c for c in comps if len(c) >= 3][:args.comps]
    for ci, comp in enumerate(kept_comps):
        for n in comp:
            comp_id[n] = ci
    if kept_comps:
        keep = set(comp_id)
        before_e, before_n = len(edges), len(nodes_in_edges)
        edges = {k: v for k, v in edges.items()
                 if k[0] in keep and k[1] in keep}
        nodes_in_edges = keep
        print("[graph] 保留 %d 个主题岛共 %d 节点（原 %d，丢 %d 个 <3 节点小团）/ 边 %d→%d"
              % (len(kept_comps), len(keep), before_n,
                 sum(1 for c in comps if len(c) < 3), before_e, len(edges)))

    # ---- 6. 输出 ----
    nodes = [{"id": n, "df": allow[n][0], "type": allow[n][1], "deg": deg[n],
              "comp": comp_id.get(n, -1)} for n in nodes_in_edges]
    links = [{"source": a, "target": b, "w": w} for (a, b), w in edges.items()]
    nodes.sort(key=lambda x: (-x["deg"], -x["df"]))
    # 文档数与词典规模从 kg_meta 取（v3 侧表建的时候写进去了）
    kmeta = {}
    try:
        for k, v in conn.execute("SELECT key, value FROM kg_meta"):
            kmeta[k] = v
    except Exception:
        pass
    out = {
        "meta": {
            "db": os.path.basename(db),
            "db_path": db,
            "min_df": args.min_df, "min_w": args.min_w,
            "max_nodes": args.max_nodes, "seed": args.seed,
            "nodes": len(nodes), "links": len(links),
            "docs": int(kmeta.get("docs", 0) or 0),
            "chunks": int(kmeta.get("chunks", 0) or 0),
            "dict_size": int(kmeta.get("dict_size", 0) or 0),
            "mentions": int(kmeta.get("mentions", 0) or 0),
            "entities_all": int(kmeta.get("entities", 0) or 0),
            "kg_version": kmeta.get("version", ""),
            "built_at": __import__("datetime").datetime.now().isoformat(
                timespec="seconds"),
        },
        "nodes": nodes,
        "links": links,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[graph] 已写出 %d 节点 / %d 边 → %s（%s 字节）"
          % (len(nodes), len(links), args.out,
             "{:,}".format(os.path.getsize(args.out))))
    if nodes:
        print("[graph] 度数最高 8 个：%s"
              % [(n["id"], n["deg"]) for n in nodes[:8]])
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
