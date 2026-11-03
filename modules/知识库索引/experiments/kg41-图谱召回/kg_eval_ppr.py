#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg_eval_ppr.py — PPR 图扩展 vs baseline vs 1-hop 的三方对照

为什么单独一个脚本而不是改 kb_rag：
    实验要在**不改生产代码路径**的前提下比较三条路。改 kb_rag 就意味着
    「实验污染实现」，跑出来好不好都说不清是哪一部分起作用。
    这里把 PPR 当作外挂通道，用 RRF 融合，权重可调 —— 与真实上线时的
    形态一致（`KB_W_KG` 已经是这个形态），但不碰 kb_rag 一行代码。

三组：
    baseline   search_unified(mode=fused, rerank=False)  ← 现有生产行为
    +1hop      走 kb_rag 的 KG 钩子（expand_1hop，对比用）
    +ppr       本脚本自己跑 PPR，再与 baseline 做 RRF 融合

判据（沿用 #41 开工前写死的口径）：
    主指标 all@5（所有 gt 都进 Top5），要求 ≥ +3pp，且 p95 ≤ +150ms

用法：
    python kg_eval_ppr.py --db <沙盘> --set <多跳集> --k 5 \
        [--alpha 0.85] [--w-ppr 1.0] [--spec-power 1.0]
"""

import argparse
import json
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# 本脚本在 experiments/kg41-图谱召回/ 下，模块目录要往上两级
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

RRF_K = 60


def rrf(paths_list, weights=None, k=RRF_K):
    """倒数排名融合 → [(path, score)]，按分数降序。"""
    weights = weights or [1.0] * len(paths_list)
    score = {}
    for paths, w in zip(paths_list, weights):
        seen = set()
        for r, p in enumerate(paths, 1):
            if not p or p in seen:
                continue
            seen.add(p)
            score[p] = score.get(p, 0.0) + w / (k + r)
    return sorted(score.items(), key=lambda kv: -kv[1])


def paths_of(res):
    out, seen = [], set()
    for r in res:
        p = r.get("path") if isinstance(r, dict) else r[1]
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def metric(rows, gts_list, k):
    """rows: [(paths, latency_ms)]；返回 any@k / all@k / p50 / p95。"""
    any_hit = all_hit = partial = 0
    lat = []
    for (paths, ms), gts in zip(rows, gts_list):
        top = set(paths[:k])
        hit = [g for g in gts if g in top]
        if hit:
            any_hit += 1
        if len(hit) == len(gts):
            all_hit += 1
        elif hit:
            partial += 1
        lat.append(ms)
    n = len(rows)
    lat.sort()

    def q(p):
        if not lat:
            return 0.0
        i = max(0, min(len(lat) - 1, int(round((len(lat) - 1) * p))))
        return lat[i]

    return {
        "n": n,
        "any": any_hit / n if n else 0,
        "all": all_hit / n if n else 0,
        "partial": partial,
        "p50": q(0.50),
        "p95": q(0.95),
        "mean": statistics.fmean(lat) if lat else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--set", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--alpha", type=float, default=0.85)
    ap.add_argument("--w-ppr", type=float, default=1.0)
    ap.add_argument("--spec-power", type=float, default=1.0)
    ap.add_argument("--min-df", type=int, default=2)
    ap.add_argument("--min-w", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试）")
    args = ap.parse_args()

    os.environ["KB_DB_PATH"] = os.path.abspath(args.db)
    import sqlite3
    import kb_index
    import kb_rag
    import kg_ppr

    conn = kb_index.connect(args.db, readonly=True)
    items = json.load(open(args.set, encoding="utf-8"))
    if args.limit:
        items = items[:args.limit]

    # gt 统一成 list
    gts_list = []
    for it in items:
        g = it.get("gt")
        gts_list.append(g if isinstance(g, list) else [g])
    print("[ppr] 评测集 %d 条 ｜ k=%d ｜ alpha=%.2f ｜ w_ppr=%.2f ｜ spec=%.1f"
          % (len(items), args.k, args.alpha, args.w_ppr, args.spec_power))

    # ---- 建图（一次，复用）----
    t0 = time.time()
    G, st = kg_ppr.build_graph(conn, min_df=args.min_df, min_w=args.min_w)
    print("[ppr] 建图 %.1fs ｜ 节点 %d ｜ 边 %d ｜ 密度 %.5f"
          % (time.time() - t0, st["nodes"], st["edges"], st["density"]))
    if st["edges"] == 0:
        print("[ERR] 图为空，先跑 build_sidecar_v3.py", file=sys.stderr)
        return 2

    # 预热（隔离模型加载与首次分词）
    try:
        kb_rag.search_unified(conn, items[0]["query"], topk=args.k,
                              mode="fused", rerank=False)
    except Exception:
        pass

    base_rows, ppr_rows, onehop_rows = [], [], []
    hit_flip = []
    for i, it in enumerate(items, 1):
        q = it["query"]

        t = time.perf_counter()
        res, _n = kb_rag.search_unified(conn, q, topk=args.k, mode="fused",
                                        rerank=False)
        base_rows.append((paths_of(res), (time.perf_counter() - t) * 1000))

        # PPR 通道
        t = time.perf_counter()
        pr = kg_ppr.search_ppr(conn, q, topk=args.k * 4, graph=G,
                               alpha=args.alpha, spec_power=args.spec_power)
        ppr_paths = paths_of(pr)
        pms = (time.perf_counter() - t) * 1000

        fused = rrf([base_rows[-1][0], ppr_paths], [1.0, args.w_ppr])
        ppr_rows.append(([p for p, _ in fused][:args.k * 3], pms))

        # 1-hop 对照（走 kb_rag 的 kg 钩子）
        os.environ["KB_KG"] = "1"
        t = time.perf_counter()
        try:
            res2, _n2 = kb_rag.search_unified(conn, q, topk=args.k, mode="fused",
                                              rerank=False)
            onehop_rows.append((paths_of(res2), (time.perf_counter() - t) * 1000))
        except Exception:
            onehop_rows.append(([], 0.0))
        os.environ.pop("KB_KG", None)

        if i % 10 == 0:
            print("   %d/%d" % (i, len(items)))

    B = metric(base_rows, gts_list, args.k)
    P = metric(ppr_rows, gts_list, args.k)
    H = metric(onehop_rows, gts_list, args.k)

    def line(tag, m):
        return ("  %-10s any@%d=%5.1f%%  all@%d=%5.1f%%  部分命中=%-2d  "
                "p50=%5.0fms p95=%5.0fms"
                % (tag, args.k, m["any"] * 100, args.k, m["all"] * 100,
                   m["partial"], m["p50"], m["p95"]))

    print("\n=== 三方对照（%d 条）===" % len(items))
    print(line("baseline", B))
    print(line("+1hop", H))
    print(line("+ppr", P))
    print("\n  Δall@%d  vs baseline：1hop %+.1fpp ｜ ppr %+.1fpp"
          % (args.k, (H["all"] - B["all"]) * 100, (P["all"] - B["all"]) * 100))
    print("  Δp95    vs baseline：1hop %+.0fms ｜ ppr %+.0fms"
          % (H["p95"] - B["p95"], P["p95"] - B["p95"]))

    verdict = []
    if (P["all"] - B["all"]) * 100 >= 3.0:
        verdict.append("PASS（all@k ≥ +3pp）")
    else:
        verdict.append("FAIL（all@k 未达 +3pp）")
    if P["p95"] - B["p95"] > 150:
        verdict.append("延迟超预算（>+150ms）")
    print("\n  判定：%s" % " ／ ".join(verdict))

    conn.close()
    return 0 if verdict and verdict[0].startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
