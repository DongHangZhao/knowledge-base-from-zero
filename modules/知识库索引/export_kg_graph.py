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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-df", type=int, default=3, help="实体最小文档频次")
    ap.add_argument("--min-w", type=int, default=2, help="边最小共现块数")
    ap.add_argument("--max-nodes", type=int, default=120)
    ap.add_argument("--comps", type=int, default=8,
                    help="保留几个连通分量（主题岛）；默认 8")
    ap.add_argument("--seed", default=None,
                    help="只导出与该实体 2 跳内相连的子图（主题视图）")
    args = ap.parse_args(argv)

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
