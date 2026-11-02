#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_rag — 知识库 RAG 三方式对比评测
=====================================

对应《AI-Agent小白实战详解》难题 7：
  - 评测集：state/kb_eval_30.json（核心）/ kb_eval_100.json（全集），人工标注 Ground Truth
  - 检索方式：FTS5 关键词 / TF-IDF 向量 / 混合（RRF）
  - 指标：Recall@5（Top5 是否命中标准答案）、MRR@5（命中的倒数排名均值）
  - 输出：对比表 + Markdown 报告 state/rag_eval_report.md

用法：python eval_rag.py [--set 30|100|both]
"""

import argparse
import json
import math
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb_index import DB_PATH, search_fts, search_tfidf, search_hybrid, search_fts_chunks, search_fts_fused

try:
    import vector_index as _vi
    HAS_VEC = True
except ImportError:
    HAS_VEC = False


def search_vec(conn, query, topk):
    if not HAS_VEC:
        return []
    return _vi.search_vec(conn, query, topk)


def search_fused_vec(conn, query, topk, w_kw=1.0, w_vec=1.0):
    if not HAS_VEC:
        return search_fts_fused(conn, query, topk)
    return _vi.search_fused_vec(conn, query, topk, w_kw, w_vec)


try:
    import bge_encoder as _bg
    HAS_BGE = True
except ImportError:
    HAS_BGE = False


def search_fused_tri(conn, query, topk, w_kw=1.0, w_vec=1.0, w_bge=1.0):
    """三路融合：关键词粒度 ⊕ LSA 稠密 ⊕ BGE 语义。三路质量接近（95/91/87），适合等权。"""
    from kb_index import RRF_K
    a = search_fts_fused(conn, query, topk * 2)
    b = search_vec(conn, query, topk * 2)
    c = search_bge(conn, query, topk * 2)
    rrf, meta = {}, {}
    for lst, w in ((a, w_kw), (b, w_vec), (c, w_bge)):
        for rank, it in enumerate(lst, 1):
            rrf[it["doc_id"]] = rrf.get(it["doc_id"], 0) + w / (RRF_K + rank)
            meta.setdefault(it["doc_id"], it)
    out = []
    for doc_id, sc in sorted(rrf.items(), key=lambda x: -x[1])[:topk]:
        it = dict(meta[doc_id]); it["score"] = sc; out.append(it)
    return out


def search_bge(conn, query, topk):
    if not HAS_BGE:
        return []
    try:
        return _bg.search_bge(conn, query, topk)
    except Exception:
        return []


def search_fused_bge(conn, query, topk, w_kw=1.0, w_bge=1.0):
    if not HAS_BGE:
        return search_fts_fused(conn, query, topk)
    try:
        return _bg.search_fused_bge(conn, query, topk, w_kw, w_bge)
    except Exception:
        return search_fts_fused(conn, query, topk)


TOPK = 5
MODES = [("fts", "FTS5 关键词（整篇）"),
         ("fts_chunk", "FTS5 关键词（分块）"),
         ("fts_fused", "FTS5 粒度融合（整篇⊕分块 RRF）"),
         ("tfidf", "TF-IDF 向量（语义近似）"),
         ("hybrid", "混合检索（等权 RRF）"),
         ("hybrid_w2", "混合检索（加权 RRF，fts:tfidf=2:1）"),
         ("vec", "LSA 稠密向量（128 维）"),
         ("fused_vec", "关键词 ⊕ LSA 向量融合"),
         ("bge", "BGE-small 语义向量（512 维）"),
         ("fused_bge", "关键词 ⊕ BGE 语义融合"),
         ("fused_tri", "三路融合 ⊕LSA ⊕BGE")]


def evaluate(conn, items, topk=TOPK):
    """返回 {mode: {"recall": x, "mrr": y, "misses": [...]}}"""
    result = {m: {"hit": 0, "hit1": 0, "rr_sum": 0.0, "ndcg_sum": 0.0, "misses": []}
              for m, _ in MODES}
    for item in items:
        q, gt = item["query"], item["gt"]
        for mode, _ in MODES:
            fn = {"fts": search_fts, "fts_chunk": search_fts_chunks,
                  "fts_fused": search_fts_fused,
                  "tfidf": search_tfidf, "hybrid": search_hybrid,
                  "vec": search_vec, "fused_vec": search_fused_vec,
                  "bge": search_bge, "fused_bge": search_fused_bge,
                  "fused_tri": search_fused_tri,
                  "hybrid_w2": lambda c, q, k: search_hybrid(c, q, k, w_fts=2.0, w_tfidf=1.0)}[mode]
            hits = fn(conn, q, topk)
            rank = 0
            for i, h in enumerate(hits, 1):
                if h["path"] == gt:
                    rank = i
                    break
            if rank:
                result[mode]["hit"] += 1
                result[mode]["rr_sum"] += 1.0 / rank
                # NDCG@10（二值相关）：DCG = 1/log2(rank+1)，IDCG = 1（仅一个 GT 且理想排第 1）
                result[mode]["ndcg_sum"] += 1.0 / math.log2(rank + 1)
                if rank == 1:
                    result[mode]["hit1"] += 1
            else:
                result[mode]["misses"].append({"query": q, "gt": gt,
                                               "top1": hits[0]["path"] if hits else None})
    n = len(items)
    for mode, _ in MODES:
        r = result[mode]
        r["recall"] = r["hit"] / n if n else 0
        r["recall1"] = r["hit1"] / n if n else 0
        r["ndcg10"] = r["ndcg_sum"] / n if n else 0
        r["mrr"] = r["rr_sum"] / n if n else 0
    return result


def fmt_pct(x):
    return "%.1f%%" % (x * 100)


def render_table(res, n):
    lines = []
    lines.append("| 检索方式 | Recall@%d | MRR@%d | NDCG@10 | 未命中 |" % (TOPK, TOPK))
    lines.append("|---------|---------|--------|---------|--------|")
    for mode, label in MODES:
        r = res[mode]
        lines.append("| %s | %s | %.4f | %.4f | %d |" % (
            label, fmt_pct(r["recall"]), r["mrr"], r.get("ndcg10", 0), n - r["hit"]))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["30", "100", "both", "auto", "all"], default="both")
    ap.add_argument("--json", help="结果另存 JSON（供 ablation.py 做对照）")
    ap.add_argument("--quiet", action="store_true", help="少打印，供脚本调用")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        print("索引不存在，先跑 build")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)

    sets = []
    json_out = {}
    S = os.path.dirname(DB_PATH)
    if args.set in ("30", "both", "all"):
        sets.append(("人工标注 30 条", os.path.join(S, "kb_eval_30.json")))
    if args.set in ("100", "both", "all"):
        sets.append(("人工标注 100 条", os.path.join(S, "kb_eval_100.json")))
    if args.set in ("auto", "all"):
        p = os.path.join(S, "kb_eval_auto.json")
        if os.path.exists(p):
            sets.append(("自动弱标注 70 条（章节标题生成，与人工集分开看）", p))

    report = ["# 知识库 RAG 检索评测报告", "",
              "> 生成时间：%s ｜ 评测集：人工标注 Ground Truth ｜ 索引：%d 篇" % (
                  __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"), 
                  conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]), ""]
    for name, path in sets:
        with open(path, encoding="utf-8") as f:
            items = json.load(f)
        # 校验 gt 是否在索引中
        bad = []
        for it in items:
            row = conn.execute("SELECT id FROM docs WHERE path=?", (it["gt"],)).fetchone()
            if not row:
                bad.append(it["gt"])
        if bad:
            print("[警告] %d 条 ground truth 不在索引中：" % len(bad))
            for b in sorted(set(bad)):
                print("   -", b)
        res = evaluate(conn, items)
        json_out[path] = {"name": name, "n": len(items),
                          "res": {m: {"recall": res[m]["recall"],
                                      "recall1": res[m]["recall1"],
                                      "ndcg10": res[m].get("ndcg10", 0),
                                      "mrr": res[m]["mrr"],
                                      "misses": res[m]["misses"]}
                                  for m, _ in MODES}}
        if not args.quiet:
            print("\n===== %s（%d 条）=====" % (name, len(items)))
            print(render_table(res, len(items)))
        report.append("## %s（%d 条）" % (name, len(items)))
        report.append("")
        report.append(render_table(res, len(items)))
        report.append("")
        # 打印各模式未命中样例（前 8 条）
        if not args.quiet:
            for mode, _ in MODES:
                misses = res[mode]["misses"][:8]
                if misses:
                    print("\n[%s] 未命中样例（前 %d 条）：" % (mode, len(misses)))
                    for m in misses:
                        print("  Q: %s\n    GT: %s\n    Top1: %s" % (m["query"], m["gt"], m["top1"]))
        report.append("### 混合检索未命中样例")
        report.append("")
        for m in res["hybrid"]["misses"][:12]:
            report.append("- Q：%s ｜ GT：`%s` ｜ 实际 Top1：`%s`" % (m["query"], m["gt"], m["top1"]))
        report.append("")
    out = os.path.join(os.path.dirname(DB_PATH), "rag_eval_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\n报告已写入: %s" % out)
    if args.json:
        import kb_index as _kb
        payload = {"db": DB_PATH, "tokenizer": "jieba" if _kb.USE_JIEBA else "bigram",
                   "doc_count": conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
                   "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "sets": json_out}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("JSON 已写入: %s" % args.json)
    conn.close()


if __name__ == "__main__":
    main()
