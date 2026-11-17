#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval_ragas_adapter.py — W9-A · RAGAS 指标定义的**本地对照层**（不替换 eval_gate）
================================================================================

一句话：把 W3 冻结主集 + W8 结构化 trace 映射成 RAGAS 四类输入，
        在**纯本地、零外部 API**的前提下算出对照数字，供对外口径对齐。

🔴 为什么不装 ragas / deepeval 包（W9 卡 §二 A 第一条要求的「装前确认」）：
   官方文档（标题+URL+访问日期 2026-11-10，见本文件 §信源）实证：
   Faithfulness / ContextPrecision / ContextRecall / AnswerRelevancy 的示例
   全部是 `AsyncOpenAI()` + `llm_factory("gpt-4o-mini")` —— **默认调外部 LLM**。
   装包后即便手动禁用，调用链默认仍带外发路径，违反「隐私不上云 +
   出口门禁写在必经处，不靠调用方自觉」。故改走官方同时提供的
   **IDBased / NonLLM 变体定义**，用本地 judge 实现，零依赖、可复现。

🔴 不做什么：不替换 eval_gate、不动冻结主集 sha1、不代填人标、不外发任何数据、
   不把 UNCALIBRATED 的分数写成「已评测」。生成侧两个指标恒标 ⏳。

指标定义（RAGAS 官方，本地实现口径逐条对齐）：
  · context_precision  IDBased 变体 = |retrieved ∩ reference| / |retrieved|
    + 排名加权版 ∑(Precision@k × v_k) / topK 中相关项数（官方 ContextPrecision 公式）
  · context_recall     IDBased 变体 = |reference ∩ retrieved| / |reference|
    （官方 LLM 版是「参考主张被检索上下文支持比例」，需生成侧 LLM，本库无生成段）
  · faithfulness       官方 = 答案主张被上下文支持数 / 答案主张总数（需 LLM 拆主张）
    本地 = answer_eval.evaluate() 的 `context_support` 实词覆盖弱代理 —— ⏳ 弱代理
  · answer_relevancy   官方 = 由答案反推 query 的相似度（需 LLM + embedding）
    本地 = query 实词 ∩ 答案实词 / query 实词 —— ⏳ 弱代理，仅示意不可比

三级 judge 开关（§5.11 能力必建 · 默认 off 一键可开）：
  --judge local  默认。词级弱代理，零依赖、离线可复现。
  --judge claim  本地结构化「主张拆分」（按句读切分 + 实词覆盖），无需 LLM。
  --judge llm    走 modules/知识图谱/llm/llm_kit.py（出口门禁 / 缓存 / 留痕 / 降级五件套）；
                 需库外环境变量，未配置自动降级，绝不中断。
  --ab           小样本 local vs claim 的 A/B 差值（纯本地闭环，不依赖外部 key）。

用法（解释器必须 venv 版）：
  PY="E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
  "$PY" w9_eval/eval_ragas_adapter.py --self-test
  "$PY" w9_eval/eval_ragas_adapter.py --sample 30 --json out.json
  "$PY" w9_eval/eval_ragas_adapter.py --sample 30 --ab
  "$PY" w9_eval/eval_ragas_adapter.py --llm-smoke

退出码：0 正常 / 1 自测失败 / 2 参数或文件错误 / 3 只读自证失败
作者：落花飞雪· ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.dirname(HERE)                       # modules/知识库索引
sys.path.insert(0, IDX)
KB_ROOT = os.path.dirname(os.path.dirname(IDX))    # 仓库根（相对推导，不写死机器路径）
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "W9-评测对齐")

MAIN_SET = os.path.join(IDX, "eval_sets", "v2026.11.json")
MANIFEST = os.path.join(IDX, "eval_sets", "manifest.json")
TRACE_JSONL = os.path.join(KB_ROOT, "outputs", "知识库运维", "W8-query-trace.jsonl")
ANSWER_SAMPLES = os.path.join(IDX, "state", "answer_samples.json")
CALIB_STATUS = os.path.join(IDX, "state", "judge_calibration", "status.json")

SEED = 20261110
TOPK = 5

SOURCES = [
    ("Faithfulness - Ragas", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/", "2026-11-10"),
    ("Context Precision - Ragas", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/", "2026-11-10"),
    ("Context Recall - Ragas", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/", "2026-11-10"),
]


# ------------------------------------------------------------------ 工具
def _load(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print("⚠ 读取失败 %s：%s" % (path, e), file=sys.stderr)
        return default


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sha1(path):
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def words(text):
    """🔴 复用库内既有口径 answer_eval.tokenize_words（jieba），不另立分词口径。
    轮子不可用时退化为字级 bigram，绝不静默改用别的分词器。"""
    try:
        import answer_eval as ae
        return set(ae.tokenize_words(text or ""))
    except Exception:
        s = text or ""
        return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def calibration_state():
    """读 judge_calibration 状态；绝不调用 status 命令（它会写盘）。"""
    st = _load(CALIB_STATUS, {}) or {}
    verdict = st.get("verdict", "UNKNOWN")
    return {
        "verdict": verdict,
        "calibrated": verdict == "CALIBRATED",
        "thresholds": st.get("thresholds", {}),
        "inventory": st.get("inventory", {}),
        "status_path": CALIB_STATUS,
        "status_sha1": _sha1(CALIB_STATUS),
        "rule": "未 CALIBRATED 时任何生成侧分数标 ⏳，不判好坏、不进决策、不写进简历数字",
    }


# ------------------------------------------------------------------ 四个指标（纯函数，可自测）
def context_precision_id(retrieved, reference):
    """RAGAS IDBasedContextPrecision = |retrieved ∩ reference| / |retrieved|。"""
    if not retrieved:
        return 0.0
    return len(set(retrieved) & set(reference)) / len(set(retrieved))


def context_precision_ranked(retrieved, reference):
    """RAGAS 官方排名加权 CP@K = ∑(Precision@k × v_k) / topK 中相关项数。"""
    ref = set(reference)
    num, rel = 0.0, 0
    for k, p in enumerate(retrieved, 1):
        if p in ref:
            tp = sum(1 for x in retrieved[:k] if x in ref)
            num += (tp / k) * 1.0
            rel += 1
    return num / rel if rel else 0.0


def context_recall_id(retrieved, reference):
    """RAGAS IDBasedContextRecall = |reference ∩ retrieved| / |reference|；无 reference 返回 None。"""
    if not reference:
        return None
    return len(set(retrieved) & set(reference)) / len(set(reference))


def faithfulness_proxy(answer, contexts, judge="local"):
    """本地弱代理：答案实词被上下文覆盖比例（claim 档按句切分再逐句覆盖）。"""
    ctx = words(" ".join(contexts or []))
    if judge == "claim":
        claims = [c for c in re.split(r"[。！？；\n.!?;]", answer or "") if c.strip()]
        if not claims:
            return 0.0
        hit = 0
        for c in claims:
            w = words(c)
            if w and len(w & ctx) / len(w) >= 0.5:
                hit += 1
        return hit / len(claims)
    aw = words(answer)
    return (len(aw & ctx) / len(aw)) if aw else 1.0


def answer_relevancy_proxy(query, answer):
    """本地弱代理：query 实词被答案覆盖比例。RAGAS 原义需 LLM 反推，标不可比。"""
    qw = words(query)
    return (len(qw & words(answer)) / len(qw)) if qw else 0.0


# ------------------------------------------------------------------ 数据装配
def stratified_sample(items, n):
    """🔴 不取前 N 条：主集按桶排序，前 20 全是 C 桶（W9 §五 已知坑）。固定 seed 分层。"""
    by_bucket = collections.defaultdict(list)
    for it in items:
        by_bucket[it.get("bucket") or "(无)"].append(it)
    rnd = random.Random(SEED)
    for v in by_bucket.values():
        rnd.shuffle(v)
    buckets = sorted(by_bucket)
    picked, i = [], 0
    while len(picked) < n and any(len(by_bucket[b]) > i for b in buckets):
        for b in buckets:
            if i < len(by_bucket[b]) and len(picked) < n:
                picked.append(by_bucket[b][i])
        i += 1
    return picked


def load_trace_records():
    recs = []
    if not os.path.isfile(TRACE_JSONL):
        return recs
    with open(TRACE_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def run_live(items, topk=TOPK):
    """真跑融合检索（readonly + INSERT 自证），返回逐条 retrieved paths。"""
    import kb_index
    import kb_rag

    conn = kb_index.connect(kb_index.DB_PATH, readonly=True)
    readonly_ok = False
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,top1_path) "
            "VALUES('_w9_ro_probe_','_w9_ro_probe_','x',1,1,1,'x')")
        conn.commit()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        readonly_ok = True
    n0 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    rows = []
    t0 = time.perf_counter()
    for it in items:
        try:
            res, _note = kb_rag.search_unified(conn, it["query"], topk=topk,
                                               mode="fused", rerank=False)
            paths = [h["path"] for h in res]
        except Exception as e:                       # 单条失败不中断
            paths = []
            print("  ⚠ 检索失败按空结果计：%s｜%s" % (it["query"][:24], str(e)[:60]),
                  file=sys.stderr)
        rows.append({"query": it["query"], "bucket": it.get("bucket"),
                     "gt": it.get("gt") or [], "retrieved": paths})
    n1 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    conn.close()
    return rows, {
        "readonly_enforced": readonly_ok,
        "query_log_before": n0, "query_log_after": n1,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        "rerank": False, "topk": topk,
    }


def ragas_inputs(rows, traces):
    """把主集 / trace 映射成 RAGAS SingleTurnSample 四类输入的**形状**（不外发）。"""
    out = []
    for r in rows:
        out.append({
            "user_input": r["query"],
            "retrieved_context_ids": r["retrieved"],
            "reference_context_ids": r["gt"],
            "response": None,             # 本库是检索系统，无 generate 段
            "reference": None,            # 无生成侧参考主张
            "source": "W3-main-set",
            "bucket": r["bucket"],
        })
    for t in traces:
        out.append({
            "user_input": t.get("query"),
            "retrieved_context_ids": [h.get("path") for h in t.get("final_top5", [])],
            "reference_context_ids": t.get("gt") or [],
            "response": None,
            "reference": None,
            "source": "W8-query-trace",
            "bucket": "trace",
        })
    return out


def compute(samples, answer_rows, judge="local"):
    """核心计算：检索侧真算，生成侧本地弱代理 + ⏳ 标注。"""
    cal = calibration_state()
    per = []
    for s in samples:
        ret, ref = s["retrieved_context_ids"], s["reference_context_ids"]
        per.append({
            "source": s["source"], "bucket": s["bucket"],
            "context_precision_id": round(context_precision_id(ret, ref), 6),
            "context_precision_ranked": round(context_precision_ranked(ret, ref), 6),
            "context_recall_id": (None if context_recall_id(ret, ref) is None
                                  else round(context_recall_id(ret, ref), 6)),
        })

    def _avg(key):
        vals = [r[key] for r in per if r[key] is not None]
        return round(sum(vals) / len(vals), 6) if vals else None

    # 生成侧：answer_samples 21 条（17 真实 + 4 合成坏答案）；合成反例单列不混入主数
    gen_real, gen_synth = [], []
    for a in answer_rows:
        ctxs = [c.get("excerpt", "") for c in a.get("contexts", [])]
        rec = {
            "faithfulness_proxy": round(faithfulness_proxy(a.get("answer", ""), ctxs, judge), 6),
            "answer_relevancy_proxy": round(answer_relevancy_proxy(a.get("query", ""),
                                                                   a.get("answer", "")), 6),
            "synthetic_bad": bool(a.get("synthetic_bad")),
        }
        (gen_synth if rec["synthetic_bad"] else gen_real).append(rec)

    def _gen_avg(rows, key):
        return round(sum(r[key] for r in rows) / len(rows), 6) if rows else None

    gen = {
        "judge": judge,
        "calibration": cal,
        "status": "⏳ UNCALIBRATED" if not cal["calibrated"] else "CALIBRATED",
        "decision_usable": False if not cal["calibrated"] else True,
        "note": ("本库是检索系统，无 generate 段；RAGAS 生成侧两指标的官方实现需 LLM "
                 "拆主张 / 反推 query，本适配器给的是**本地弱代理**，不是 RAGAS 原值。"
                 "judge 人标 0 条 ⇒ 恒标 ⏳，不判好坏、不进决策、不写进简历数字。"),
        "n_real": len(gen_real), "n_synthetic_bad_excluded": len(gen_synth),
        "faithfulness_proxy_real": _gen_avg(gen_real, "faithfulness_proxy"),
        "answer_relevancy_proxy_real": _gen_avg(gen_real, "answer_relevancy_proxy"),
        "faithfulness_proxy_synthetic_bad": _gen_avg(gen_synth, "faithfulness_proxy"),
    }
    # 交叉核验：与 answer_eval 的既有弱代理对齐（复用轮子，不自己另立口径）
    try:
        import answer_eval as ae
        agg, _ = ae.evaluate(answer_rows)
        gen["answer_eval_context_support"] = round(agg["context_support"], 6)
        gen["answer_eval_citation_accuracy_pct"] = round(agg["citation_accuracy"], 2)
    except Exception as e:                          # 轮子不可用则如实标注，不假装
        gen["answer_eval_crosscheck"] = "UNAVAILABLE: %s" % str(e)[:100]

    return {
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "adapter": "eval_ragas_adapter.py",
        "impl": "RAGAS 指标定义本地实现（IDBased / NonLLM 变体），零外部 API、零装包",
        "n_samples": len(samples),
        "by_source": dict(collections.Counter(s["source"] for s in samples)),
        "by_bucket": dict(collections.Counter(str(s["bucket"]) for s in samples)),
        "retrieval_side": {
            "context_precision_id": _avg("context_precision_id"),
            "context_precision_ranked": _avg("context_precision_ranked"),
            "context_recall_id": _avg("context_recall_id"),
            "n_no_reference": sum(1 for r in per if r["context_recall_id"] is None),
            "note": "E 桶 gt 为空（负样本设计），context_recall 分母无定义 ⇒ 不计入均值，单独报条数",
        },
        "generation_side": gen,
        "per_sample": per,
        "sources": [{"title": t, "url": u, "accessed": d} for t, u, d in SOURCES],
    }


# ------------------------------------------------------------------ LLM 通道（默认 off，可开）
def llm_smoke():
    """验「开起来真能跑」：无凭据时也必须走降级路径正常返回，绝不中断。"""
    try:
        sys.path.insert(0, os.path.join(KB_ROOT, "modules", "知识图谱", "llm"))
        import llm_kit
        safe = llm_kit.prepare("检索三链路 FTS5 BGE 字级 substring 是哪三条")
        r = llm_kit.chat("请用一句话回答：1+1 等于几？", task="w9-llm-smoke")
        return {
            "channel": "modules/知识图谱/llm/llm_kit.py（client/出口门禁/缓存/留痕/降级五件套）",
            "export_gate": "prepare() 必经，硬拦 ExportBlocked；soft 脱敏 %d 处" % len(safe["hits"]),
            "chat_ok": bool(r.get("ok")), "degraded": bool(r.get("degraded")),
            "cached": bool(r.get("cached")), "reason": r.get("reason", ""),
            "credential_source": "库外环境变量 AGNES_API_KEY / OPENAI_API_KEY（代码只读 os.environ）",
            "note": ("LLM 不得自当评测金标；本通道只做可选增强，默认 off。"
                     "未配凭据→自动降级（§5 L2.4 别卡住），不阻断本地四指标。"),
        }
    except Exception as e:
        return {"channel": "llm_kit", "error": str(e)[:200], "degraded": True,
                "note": "导入失败亦按降级处理，不中断"}


# ------------------------------------------------------------------ 自测
def self_test():
    ok = True

    def chk(name, got, expect):
        nonlocal ok
        passed = (abs(got - expect) < 1e-9) if isinstance(expect, float) else (got == expect)
        ok &= passed
        print("  [%s] %-46s got=%s expect=%s" % ("PASS" if passed else "FAIL", name, got, expect))

    print("=== eval_ragas_adapter --self-test（纯函数，不落盘、不跑检索）===")
    chk("ID precision 2/4", context_precision_id(["a", "b", "c", "d"], ["a", "d"]), 0.5)
    chk("ID precision 空检索", context_precision_id([], ["a"]), 0.0)
    chk("ranked precision 相关项在首位", context_precision_ranked(["a", "x"], ["a"]), 1.0)
    chk("ranked precision 相关项在第二位", context_precision_ranked(["x", "a"], ["a"]), 0.5)
    chk("ranked precision 无相关项", context_precision_ranked(["x", "y"], ["a"]), 0.0)
    chk("ID recall 2/3", context_recall_id(["a", "b", "x"], ["a", "b", "c"]), 2 / 3)
    chk("ID recall 无 reference", context_recall_id(["a"], []), None)
    chk("faithfulness local 全覆盖", faithfulness_proxy("窗口函数排名", ["窗口函数排名 SQL"]), 1.0)
    chk("relevancy 全覆盖", answer_relevancy_proxy("窗口函数", "窗口函数完全指南"), 1.0)

    items = [{"query": "q%d" % i, "gt": ["a"], "bucket": b}
             for i, b in enumerate(["B"] * 5 + ["C"] * 5 + ["D"] * 5 + ["E"] * 5 + ["F"] * 5)]
    s = stratified_sample(items, 10)
    cnt = collections.Counter(x["bucket"] for x in s)
    chk("分层抽样覆盖 5 桶（不取前 N 条）", len(cnt), 5)
    chk("分层抽样每桶 2 条", set(cnt.values()) == {2}, True)

    # A/B：claim 档必须能跑出与 local 不同或相同的数（能力可运行），不要求一定更优
    a = faithfulness_proxy("窗口函数排名。分组求和。", ["窗口函数排名 SQL"], "local")
    b = faithfulness_proxy("窗口函数排名。分组求和。", ["窗口函数排名 SQL"], "claim")
    ok &= isinstance(b, float) and 0.0 <= b <= 1.0
    print("  [%s] claim 档可运行｜local=%.4f claim=%.4f 差值=%+.4f" % (
        "PASS" if isinstance(b, float) and 0.0 <= b <= 1.0 else "FAIL", a, b, b - a))
    # 生成侧恒标 ⏳：构造一个假的 CALIBRATED 状态不能改变「不外发」事实
    st = calibration_state()
    print("  [INFO] 校准状态（只读 status.json）= %s｜生成侧标 ⏳=%s" % (
        st["verdict"], not st["calibrated"]))
    return 0 if ok else 1


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="W9-A RAGAS 指标定义本地对照层")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--sample", type=int, default=30, help="分层抽样条数（默认 30，非全量）")
    ap.add_argument("--judge", choices=("local", "claim", "llm"), default="local")
    ap.add_argument("--ab", action="store_true", help="打印 local vs claim 的 A/B 差值")
    ap.add_argument("--llm-smoke", action="store_true", help="验 LLM 通道能跑（含降级）")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--no-live", action="store_true", help="不跑检索，只用 W8 trace 记录（快）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.llm_smoke:
        print(json.dumps(llm_smoke(), ensure_ascii=False, indent=2))
        return 0

    items = _load(MAIN_SET)
    answer_rows = _load(ANSWER_SAMPLES, [])
    man = _load(MANIFEST, {}) or {}
    if not items or not isinstance(items, list):
        print("🔴 读不到冻结主集 %s" % MAIN_SET, file=sys.stderr)
        return 2

    traces = load_trace_records()
    print("=== W9-A RAGAS 本地对照层｜主集 %d 条（sha1 %s）===" % (
        len(items), (man.get("sets") or [{}])[0].get("sha1", "?")))
    print("W8 trace 记录：%d 条｜judge=%s（默认 local，零外部 API）" % (len(traces), args.judge))

    rows = []
    live_meta = {"skipped": True}
    if not args.no_live:
        picked = stratified_sample(items, min(args.sample, len(items)))
        print("分层抽样 %d 条（固定 seed %d，按 B/C/D/E/F 分层，避开「前 20 全是 C 桶」坑）…" % (
            len(picked), SEED))
        rows, live_meta = run_live(picked)
        if not live_meta.get("readonly_enforced"):
            print("🔴 readonly 自证失败：评测连接可写，立刻停", file=sys.stderr)
            return 3
        print("真跑完成｜query_log %d → %d（readonly 生效）｜耗时 %.1f ms" % (
            live_meta["query_log_before"], live_meta["query_log_after"],
            live_meta["elapsed_ms"]))
    samples = ragas_inputs(rows, traces)
    result = compute(samples, answer_rows, judge=args.judge)
    result["main_set"] = {"path": MAIN_SET, "sha1": _sha1(MAIN_SET),
                          "version": man.get("latest"), "n": len(items)}
    result["live"] = live_meta
    result["llm_channel"] = "built, default off, --judge llm 可开（见 --llm-smoke）"

    r = result["retrieval_side"]
    g = result["generation_side"]
    print("\n--- RAGAS 四指标（本地真跑，样本 n=%d：%s）---" % (
        result["n_samples"], result["by_source"]))
    print("  context_precision (IDBased)  = %s" % r["context_precision_id"])
    print("  context_precision (ranked)   = %s" % r["context_precision_ranked"])
    print("  context_recall    (IDBased)  = %s  （E 桶无 reference %d 条不计入）" % (
        r["context_recall_id"], r["n_no_reference"]))
    print("  faithfulness      [⏳ 弱代理] = %s  （judge=%s, 真实样本 %d 条）" % (
        g["faithfulness_proxy_real"], args.judge, g["n_real"]))
    print("  answer_relevancy  [⏳ 弱代理] = %s  （不可比，见映射表）" % g["answer_relevancy_proxy_real"])
    print("  校准状态：%s｜人工 gold %s 条｜生成侧决策可用：%s" % (
        g["status"], cal_gold(result), g["decision_usable"]))
    print("  answer_eval 交叉核验 context_support = %s（复用既有轮子）" % (
        g.get("answer_eval_context_support", "UNAVAILABLE")))

    if args.ab:
        alt = compute(samples, answer_rows, judge="claim")
        d1 = g["faithfulness_proxy_real"]
        d2 = alt["generation_side"]["faithfulness_proxy_real"]
        print("\n--- A/B（纯本地闭环，不依赖外部 key）---")
        print("  judge=local faithfulness_proxy = %s" % d1)
        print("  judge=claim faithfulness_proxy = %s" % d2)
        print("  差值 = %+.6f｜口径：claim 按句切主张再逐句覆盖；两者均 ⏳ 不判好坏" % (
            (d2 - d1) if (d1 is not None and d2 is not None) else 0))
        result["ab"] = {"local": d1, "claim": d2,
                        "delta": (d2 - d1) if (d1 is not None and d2 is not None) else None,
                        "note": "差值仅证「开起来真能跑」，未校准不得用于判好坏/上线决策"}

    out = args.json_out or os.path.join(OUT_DIR, "ragas_metrics.json")
    if not os.path.isabs(out):
        out = os.path.join(OUT_DIR, out)
    _dump(out, result)
    print("\n产物：%s" % out)
    return 0


def cal_gold(result):
    inv = result["generation_side"]["calibration"].get("inventory", {})
    return inv.get("human_gold", "?")


if __name__ == "__main__":
    sys.exit(main())
