#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_test_metrics — metrics.py 纯函数单测（离线、零依赖）

跑法：python _test_metrics.py
全部用「人工可手算」的样例，确保指标口径与论文定义一致，不靠魔法数字。
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics as M

PASS = 0
FAIL = 0


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


def check(name, got, exp, tol=1e-9):
    global PASS, FAIL
    ok = approx(got, exp, tol)
    print(("✓" if ok else "✗") + " %-40s got=%.6f exp=%.6f" % (name, got, exp))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main():
    # 样例排序：a b c d e
    ranked = ["a", "b", "c", "d", "e"]

    # ---- 单条 gt = "c"（第 3 位）----
    check("hit@1 (c not in top1)", M.hit_rate_at_k(ranked, "c", 1), 0.0)
    check("hit@3 (c in top3)", M.hit_rate_at_k(ranked, "c", 3), 1.0)
    check("hit@5", M.hit_rate_at_k(ranked, "c", 5), 1.0)
    check("precision@3", M.precision_at_k(ranked, "c", 3), 1 / 3)
    check("mrr@3 (rank 3)", M.mrr_at_k(ranked, "c", 3), 1 / 3)
    check("mrr@5", M.mrr_at_k(ranked, "c", 5), 1 / 3)
    # NDCG@3 单条 gt：DCG = 1/log2(4)=0.5；IDCG = 单条相关放 rank1 = 1/log2(2)=1.0
    # → NDCG = 0.5（与 eval_rag.py 现有口径 1/log2(rank+1) 一致）
    check("ndcg@3 (single gt rank3)", M.ndcg_at_k(ranked, {"c": 1}, 3), 0.5)
    # NDCG@5 但 k=2（c 不在 top2）→ DCG=0 → 0.0
    check("ndcg@2 (c outside)", M.ndcg_at_k(ranked, {"c": 1}, 2), 0.0)

    # ---- 多条 gt = {b, e} ----
    check("hit@2 (b at rank2)", M.hit_rate_at_k(ranked, {"b", "e"}, 2), 1.0)
    check("hit@1 (none in top1)", M.hit_rate_at_k(ranked, {"b", "e"}, 1), 0.0)
    check("mrr@5 (first hit b rank2)", M.mrr_at_k(ranked, {"b", "e"}, 5), 1 / 2)
    check("precision@5 (2 of 5)", M.precision_at_k(ranked, {"b", "e"}, 5), 2 / 5)

    # ---- 分级相关性 NDCG ----
    # rel: a=3, c=1, 其余 0；ranked=a b c d e
    # DCG@3 = 3/log2(2) + 0 + 1/log2(4) = 3 + 0.5 = 3.5
    # IDCG@3 = 3/log2(2) + 1/log2(3) = 3 + 0.6309 = 3.6309
    rel = {"a": 3, "c": 1}
    dcg3 = 3 / math.log2(2) + 1 / math.log2(4)
    idcg3 = 3 / math.log2(2) + 1 / math.log2(3)
    check("ndcg@3 graded", M.ndcg_at_k(ranked, rel, 3), dcg3 / idcg3)

    # ---- 边界 ----
    check("hit empty ranked", M.hit_rate_at_k([], "x", 5), 0.0)
    check("mrr empty gt", M.mrr_at_k(ranked, set(), 5), 0.0)
    check("ndcg empty rel", M.ndcg_at_k(ranked, {}, 5), 0.0)
    check("precision k=0", M.precision_at_k(ranked, "a", 0), 0.0)
    check("as_set scalar", len(M.as_set("a")), 1)
    check("as_set list", len(M.as_set(["a", "b"])), 2)

    # ---- ranking_metrics 聚合 ----
    m = M.ranking_metrics(ranked, "c", ks=(1, 3, 5))
    check("agg hit@3", m[3]["hit"], 1.0)
    check("agg mrr@3", m[3]["mrr"], 1 / 3)
    check("agg ndcg@3", m[3]["ndcg"], 0.5)
    check("agg precision@3", m[3]["precision"], 1 / 3)

    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
