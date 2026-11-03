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
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kb_index import DB_PATH, search_fts, search_tfidf, search_hybrid, search_fts_chunks, search_fts_fused
import metrics as _metrics  # 检索评测纯函数（hit-rate@k / MRR@k / NDCG@k / precision@k）

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


def _modes(rewrite=None):
    """评测模式列表；给了 `--rewrite` 时**追加**一路 search_unified（#39 A/B 用）。

    为什么不默认加：`search_unified` 会带 3 路召回 + 融合，比其他模式慢一个量级；
    不做改写对照时没必要付这个成本。
    """
    if not rewrite:
        return MODES
    return MODES + [("unified", "三路融合·改写 %s（search_unified）" % rewrite)]


def search_unified_mode(conn, query, topk, rewrite=None):
    """eval 专用包装：只取结果、丢掉 note；**关精排**（各模式口径一致，且不需模型）。"""
    import kb_rag
    res, _note = kb_rag.search_unified(conn, query, topk=topk, mode="fused",
                                       rerank=False, rewrite=rewrite)
    return res


def _mode_fn(mode, rewrite=None):
    """mode 名 → 检索函数。

    拆成独立函数是为了让「预热」和「正式评测」走**同一个**调用口径 ——
    两处各写一份 lambda 的话，改一处忘另一处，预热就变成了测别的东西。
    """
    if mode == "unified":
        return lambda c, q, k: search_unified_mode(c, q, k, rewrite)
    return {"fts": search_fts, "fts_chunk": search_fts_chunks,
            "fts_fused": search_fts_fused,
            "tfidf": search_tfidf, "hybrid": search_hybrid,
            "vec": search_vec, "fused_vec": search_fused_vec,
            "bge": search_bge, "fused_bge": search_fused_bge,
            "fused_tri": search_fused_tri,
            "hybrid_w2": lambda c, q, k: search_hybrid(c, q, k, w_fts=2.0, w_tfidf=1.0)}[mode]


def _quantile(vals, p):
    """线性插值分位数（ms）。

    为什么不用 nearest-rank：30 条样本下 nearest-rank 的 p95 恒等于 max，
    一条抖动就决定结论 —— 那测的不是 p95，是「碰巧最慢的那条」。
    """
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def evaluate(conn, items, topk=TOPK, rewrite=None):
    """返回 {mode: {"recall","mrr","ndcg10","misses","p50","p95","pmax"}}

    🔴 **p95 是 #41 决策门的左右腿之一**（另一条是 R@5）。没有延迟就没法判
    「值不值」——收益 +3pp 但慢 800ms 的方案在交互场景是负收益。
    """
    modes = _modes(rewrite)
    result = {m: {"hit": 0, "hit1": 0, "rr_sum": 0.0, "ndcg_sum": 0.0,
                  "misses": [], "elapsed": []}
              for m, _ in modes}
    # 🔴 预热一轮：模型加载 / jieba 首次分词 / SQLite 页缓存全落在第一条查询上。
    # 不预热的话 bge 路第一条 800ms+，p95 被冷启动污染，
    # 会出现「加了图谱反而更快」这种不可能的结论。
    if items:
        warm_q = items[0]["query"]
        for mode, _ in modes:
            try:
                _mode_fn(mode, rewrite)(conn, warm_q, topk)
            except Exception:
                pass
    for item in items:
        q, gt = item["query"], item["gt"]
        for mode, _ in modes:
            fn = _mode_fn(mode, rewrite)
            _t0 = time.perf_counter()
            hits = fn(conn, q, topk)
            result[mode]["elapsed"].append((time.perf_counter() - _t0) * 1000.0)
            # 用 metrics 统一口径（与 metrics.py 单测一致；单条 gt 时 NDCG=1/log2(rank+1)）
            ranked = [h["path"] for h in hits]
            mm = _metrics.ranking_metrics(ranked, gt, ks=(1, topk))
            if mm[topk]["hit"]:
                result[mode]["hit"] += 1
            if mm[1]["hit"]:
                result[mode]["hit1"] += 1
            result[mode]["rr_sum"] += mm[topk]["mrr"]
            result[mode]["ndcg_sum"] += mm[topk]["ndcg"]
            if not mm[topk]["hit"]:
                result[mode]["misses"].append({"query": q, "gt": gt,
                                               "top1": hits[0]["path"] if hits else None})
    n = len(items)
    for mode, _ in modes:
        r = result[mode]
        r["recall"] = r["hit"] / n if n else 0
        r["recall1"] = r["hit1"] / n if n else 0
        r["ndcg10"] = r["ndcg_sum"] / n if n else 0
        r["mrr"] = r["rr_sum"] / n if n else 0
        elapsed = r.pop("elapsed", [])
        r["p50"] = _quantile(elapsed, 0.50)
        r["p95"] = _quantile(elapsed, 0.95)
        r["pmax"] = max(elapsed) if elapsed else None
    return result


def fmt_pct(x):
    return "%.1f%%" % (x * 100)


def render_table(res, n, modes=None):
    lines = []
    lines.append("| 检索方式 | Recall@%d | MRR@%d | NDCG@10 | 未命中 | p50 | p95 |" % (TOPK, TOPK))
    lines.append("|---------|---------|--------|---------|--------|-----|-----|")
    for mode, label in (modes or MODES):
        r = res[mode]
        p50, p95 = r.get("p50"), r.get("p95")
        lines.append("| %s | %s | %.4f | %.4f | %d | %s | %s |" % (
            label, fmt_pct(r["recall"]), r["mrr"], r.get("ndcg10", 0), n - r["hit"],
            "%.0fms" % p50 if p50 is not None else "-",
            "%.0fms" % p95 if p95 is not None else "-"))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["30", "100", "both", "auto", "all", "multihop", "holdout"],
                    default="both",
                    help="multihop = 多跳子集（#41 图谱实验用，真集放 state/ 不入库）；"
                         "holdout = 60 条留出集（调参时可看、调完后才算数）")
    ap.add_argument("--k", type=int, default=None, help="Recall/MRR/NDCG 的 cutoff k（默认 5）")
    ap.add_argument("--json", help="结果另存 JSON（供 ablation.py 做对照）")
    ap.add_argument("--quiet", action="store_true", help="少打印，供脚本调用")
    # #39：给了这个档位就额外评一路 search_unified（同一份评测集，A/B 对照用）
    ap.add_argument("--rewrite", choices=["off", "l0", "l1", "l2"], default=None,
                    help="额外评一路三路融合（search_unified）的改写档位；不给则不评")
    args = ap.parse_args()
    global TOPK
    TOPK = args.k if args.k else TOPK
    if not os.path.exists(DB_PATH):
        print("索引不存在，先跑 build")
        sys.exit(1)
    # 🔴 必须走只读连接。这一行是本模块最容易被「优化掉」的地方，说清楚为什么不能改：
    #
    # 原为 sqlite3.connect(DB_PATH)。评测每查一次，kb_index 的埋点就往 query_log
    # 插一行 —— 于是「评测」这件纯读取的事，悄悄改写了生产库的业务表。
    # 实测后果（2026-11-04 凌晨）：生产 query_log 6363 行里 5924 行是评测题，
    # 占 93%，真人查询只剩 439 行。任何基于 query_log 的漏检归因分析，
    # 都是建在 93% 噪声上的 —— 而这个污染**不报错、不影响检索**，只是让结论全错。
    #
    # kb_index.connect(readonly=True) 早就实现了这个能力，docstring 原话就是
    # 「评测脚本并行跑时不会误改索引」——有设计，一直没被执行。
    # kb_index 对只读连接的 query_log INSERT 有 except OperationalError 兜底，
    # 所以改成只读不影响检索本身，只是不再埋点。
    #
    # 防回退：`_test_eval_readonly.py` 会验证「跑一次 eval，query_log 行数不变」，
    # 这行被改掉测试就红。
    import kb_index as _kb_conn
    conn = _kb_conn.connect(DB_PATH, readonly=True)

    sets = []
    json_out = {}
    S = os.path.dirname(DB_PATH)
    if args.set in ("30", "both", "all"):
        sets.append(("人工标注 30 条", os.path.join(S, "kb_eval_30.json")))
    if args.set in ("100", "both", "all"):
        sets.append(("人工标注 100 条", os.path.join(S, "kb_eval_100.json")))
    if args.set == "multihop":
        # #41 实验集：真集放 state/（gitignore），仓库里只有 eval_sets/*.example.json 占位
        sets.append(("多跳子集 30 条（#41 实验）", os.path.join(S, "kb_eval_multihop_30.json")))
    if args.set in ("holdout", "all"):
        # 🔴 留出集（2026-11-03 接入）：文件早就在 state/ 下，但 eval_rag 从来没读过它，
        # 等于「留出集白留」—— 调参时看不到、改完也不复核。它的用途是
        # **调完参数后再看一次**：30/100 集上看涨、holdout 上不涨，就说明是过拟合。
        sets.append(("留出集 60 条（调参后复核用）", os.path.join(S, "kb_eval_holdout.json")))
    if args.set in ("auto", "all"):
        p = os.path.join(S, "kb_eval_auto.json")
        if os.path.exists(p):
            sets.append(("自动弱标注 70 条（章节标题生成，与人工集分开看）", p))

    # 🔴 缺评测集时**跳过并说清**，不是抛 FileNotFoundError（2026-11-03，T03 顺手修）：
    # 评测集放 state/（gitignore）不入库，干净机器/CI 上必然没有；原来的
    # `open(path)` 直接崩，于是「跑一遍 eval 看看」变成必须先猜文件名。
    for _n, _p in list(sets):
        if not os.path.exists(_p):
            print("[缺] 评测集不存在，跳过：%s" % _p)
    sets = [(n, p) for n, p in sets if os.path.exists(p)]
    if not sets:
        print("没有可用评测集（state/kb_eval_30.json 等不入库），先准备一份再跑评测")
        conn.close()
        return

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
            # gt 允许单路径字符串或多路径列表（多跳子集常见多 gt）；列表时逐个校验
            gts = it["gt"] if isinstance(it.get("gt"), list) else [it.get("gt")]
            if not any(conn.execute("SELECT id FROM docs WHERE path=?", (g,)).fetchone()
                       for g in gts if g):
                bad.append(it.get("gt"))
        if bad:
            print("[警告] %d 条 ground truth 不在索引中：" % len(bad))
            for b in sorted(set(bad)):
                print("   -", b)
        res = evaluate(conn, items, rewrite=args.rewrite)
        json_out[path] = {"name": name, "n": len(items),
                          "res": {m: {"recall": res[m]["recall"],
                                      "recall1": res[m]["recall1"],
                                      "ndcg10": res[m].get("ndcg10", 0),
                                      "mrr": res[m]["mrr"],
                                      "p50": res[m].get("p50"),
                                      "p95": res[m].get("p95"),
                                      "misses": res[m]["misses"]}
                                  for m, _ in _modes(args.rewrite)}}
        if not args.quiet:
            print("\n===== %s（%d 条）=====" % (name, len(items)))
            print(render_table(res, len(items), _modes(args.rewrite)))
        report.append("## %s（%d 条）" % (name, len(items)))
        report.append("")
        report.append(render_table(res, len(items), _modes(args.rewrite)))
        report.append("")
        # 打印各模式未命中样例（前 8 条）
        if not args.quiet:
            for mode, _ in _modes(args.rewrite):
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
        # 🔴 summary：把「唯一/最后一个评测集」的指标平铺一层（按 mode 分层），
        # 专供 kg_gate 决策门取数。原来只写 sets（三层嵌套），kg_gate 的 _pick
        # 在顶层找不到 recall / p95，决策门永远返回「无法判定（数据缺失）」。
        # 🔴 summary 的 scope 必须显式写出来。
        # 原实现取 list(json_out.values())[-1] —— 也就是「最后一个评测集」冒充全集。
        # `--set all` 时顺序是 30条 / 100条 / multihop / holdout / auto，
        # 于是 summary 里躺的是 auto（70 条弱标注集）的指标，而调用方（kg_gate、
        # 各种报告脚本）看到键名叫 summary 就当成全集结论。
        # 这是「数字看起来正常、结论全错」的典型 —— 比缺数危险得多。
        #
        # 改法：
        #   · 单集 → summary 照旧平铺（向后兼容，不需要改任何既有调用方）
        #   · 多集 → summary 留空 + summary_scope="multi" + 逐集写 summary_by_set
        #     「显式失败优于静默误导」：拿到的 summary 是空字典，调用方会立刻发现，
        #     而不是拿着 auto 的数字去汇报「全量评测结果」。
        summary, summary_by_set, scope = {}, {}, "empty"
        if json_out:
            def _flat(d):
                return {k: v for k, v in d.items() if k != "misses"}
            if len(json_out) == 1:
                scope = "single"
                one = next(iter(json_out.values()))
                summary = {m: _flat(d) for m, d in one["res"].items()}
            else:
                scope = "multi"
                summary_by_set = {
                    v["name"]: {m: _flat(d) for m, d in v["res"].items()}
                    for v in json_out.values()}
        payload = {"db": DB_PATH, "tokenizer": "jieba" if _kb.USE_JIEBA else "bigram",
                   "doc_count": conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
                   "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "summary_scope": scope,
                   "summary": summary,
                   "summary_by_set": summary_by_set,
                   "sets": json_out}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("JSON 已写入: %s（summary_scope=%s）" % (args.json, scope))
        if scope == "multi":
            print("   ⚠ 本次是多集评测，summary 故意留空（防止「最后一个集」冒充全集）；"
                  "逐集指标见 summary_by_set")
    conn.close()


if __name__ == "__main__":
    main()
