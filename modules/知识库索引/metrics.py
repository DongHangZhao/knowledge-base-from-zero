#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metrics — 检索评测纯函数（无依赖、可单测）
==========================================

把 RAG 评测里反复手写的「Recall@k / MRR@k / NDCG@k / Precision@k」抽成
**纯函数**，让 eval_rag.py、ablation.py、CI 都能复用同一套口径，而不是各写各的。

设计原则：
  - 零依赖（只用到标准库），离线可单测；
  - 入参只有「有序结果列表」+「相关集」，不碰数据库、不碰模型；
  - gt 既支持单条（字符串 path），也支持多条（set/list）——单条时
    hit_rate@k 即 Recall@k，多条时退化为集合命中率；
  - 所有函数对空结果 / 空相关集都返回 0.0，不抛异常（评测循环容错）。

用法：
    from metrics import ranking_metrics
    m = ranking_metrics(ranked_paths, gt_path, ks=(1, 3, 5, 10))
    print(m[5]["hit"], m[5]["mrr"], m[5]["ndcg"], m[5]["precision"])
"""

from __future__ import annotations

__all__ = [
    "as_set",
    "hit_rate_at_k",
    "precision_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "ranking_metrics",
]


def as_set(gt):
    """把 ground truth 归一成 set。

    - 单条字符串 / int → {x}
    - list / set / tuple → set(x)
    - None / 空 → set()
    """
    if gt is None:
        return set()
    if isinstance(gt, (set, frozenset)):
        return set(gt)
    if isinstance(gt, (list, tuple)):
        return set(gt)
    return {gt}


def hit_rate_at_k(ranked, gt, k):
    """Top-k 命中率：相关集里**至少一条**出现在前 k 个结果为 1.0，否则 0.0。

    单条 gt 时等价于 Recall@k；多条 gt 时是「集合是否被前 k 覆盖到任意一条」。
    """
    if k <= 0:
        return 0.0
    g = as_set(gt)
    if not g:
        return 0.0
    topk = ranked[:k]
    for item in topk:
        if item in g:
            return 1.0
    return 0.0


def precision_at_k(ranked, gt, k):
    """Top-k 准确率：前 k 个结果里属于相关集的比例（多条 gt 按集合算）。"""
    if k <= 0:
        return 0.0
    g = as_set(gt)
    if not g:
        return 0.0
    topk = ranked[:k]
    if not topk:
        return 0.0
    hit = sum(1 for item in topk if item in g)
    return hit / len(topk)


def mrr_at_k(ranked, gt, k):
    """Top-k 倒数排名均值（单查询视角）：第一个相关结果的 1/rank；无则 0.0。

    多条 gt 时取「最早出现的相关结果」的排名。
    """
    if k <= 0:
        return 0.0
    g = as_set(gt)
    if not g:
        return 0.0
    for rank, item in enumerate(ranked[:k], 1):
        if item in g:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked, rel, k):
    """Top-k NDCG：支持分级相关性（rel 是 {doc_id: 相关度} 或 {doc_id: 0/1}）。

    - DCG = Σ rel_i / log2(rank+1)
    - IDCG = 把相关性降序排后的 DCG
    - NDCG = DCG / IDCG（相关集为空或全 0 时返 0.0）
    """
    if k <= 0:
        return 0.0
    if not rel:
        return 0.0
    topk = ranked[:k]
    dcg = 0.0
    for rank, item in enumerate(topk, 1):
        r = rel.get(item, 0) or 0
        if r:
            dcg += r / math_log2(rank + 1)
    # IDCG：理想排序 = 相关度从大到小
    gains = sorted((v for v in rel.values() if v), reverse=True)
    idcg = 0.0
    for rank, r in enumerate(gains[:k], 1):
        idcg += r / math_log2(rank + 1)
    return dcg / idcg if idcg else 0.0


def math_log2(x):
    import math
    return math.log2(x)


def ranking_metrics(ranked, gt, ks=(1, 3, 5, 10), rel=None):
    """一次性算多个 k 的全部指标，返回 {k: {hit, precision, mrr, ndcg}}。

    - ranked: 有序结果列表（doc_id / path 均可）
    - gt: 单条或多条 ground truth
    - ks: 要计算的 cutoff 序列
    - rel: 可选分级相关性 dict；不传则按 gt 二值（命中=1）
    """
    g = as_set(gt)
    rel_map = rel if rel is not None else {x: 1 for x in g}
    out = {}
    for k in ks:
        out[k] = {
            "hit": hit_rate_at_k(ranked, g, k),
            "precision": precision_at_k(ranked, g, k),
            "mrr": mrr_at_k(ranked, g, k),
            "ndcg": ndcg_at_k(ranked, rel_map, k),
        }
    return out


if __name__ == "__main__":
    # 自测：经典样例，人工可核的值
    ranked = ["a", "b", "c", "d", "e"]
    m = ranking_metrics(ranked, "c", ks=(1, 3, 5))
    assert m[1]["hit"] == 0.0
    assert m[3]["hit"] == 1.0
    assert abs(m[3]["mrr"] - 1 / 3) < 1e-9
    assert abs(m[3]["ndcg"] - (1 / math_log2(4))) < 1e-9
    # 多条 gt
    m2 = ranking_metrics(ranked, {"b", "e"}, ks=(2, 5))
    assert m2[2]["hit"] == 1.0          # b 在第 2 位
    assert m2[2]["mrr"] == 1 / 2
    assert m2[5]["hit"] == 1.0
    # 空结果 / 空 gt
    assert hit_rate_at_k([], "x", 5) == 0.0
    assert mrr_at_k(ranked, set(), 5) == 0.0
    assert ndcg_at_k(ranked, {}, 5) == 0.0
    print("metrics self-check OK")
