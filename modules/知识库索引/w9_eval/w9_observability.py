#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
w9_observability.py — W9-C · OTel 语义 spans + 三张门禁指标（纯本地）
====================================================================

一句话：读 W8 `query_trace.py` 的 JSONL 结构，真跑若干条查询产出
        OTel 语义 span（embed → retrieve → rerank → generate），
        并算零结果率 / index freshness / 每层耗时 p95 三张门禁指标。

🔴 边界：
   · **不装 opentelemetry-sdk**：包未安装，装它需联网拉依赖（且 C 盘零增长纪律）。
     本模块输出 **OTLP/JSON 兼容的 span 结构**（trace_id / span_id / parent /
     start_time_unix_nano / end_time_unix_nano / attributes / status），
     纯标准库、默认 off、一键可开（KB_W9_TRACE=1）。若将来装了官方 SDK，
     `--otlp-endpoint` 可接本地 collector；🔴 **绝不接云端 Langfuse/Phoenix**。
   · 本库是**检索系统，无 generate 段** ⇒ 第四段如实标 `kb.no_generate_stage=true`，
     不伪造耗时、不编造答案。
   · 只读连接 + INSERT 自证（query_log 不被污染）；不上传任何生产数据。

用法（venv 解释器）：
  "$PY" w9_eval/w9_observability.py --self-test
  KB_W9_TRACE=1 "$PY" w9_eval/w9_observability.py --rounds 5
  "$PY" w9_eval/w9_observability.py --rounds 5 --json observability.json

退出码：0 正常 / 1 自测失败 / 2 输入错误 / 3 readonly 自证失败
作者：落花飞雪· ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import statistics
import sys
import time
import uuid
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.dirname(HERE)
sys.path.insert(0, IDX)
KB_ROOT = os.path.dirname(os.path.dirname(IDX))
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "W9-评测对齐")

TRACE_JSONL = os.path.join(KB_ROOT, "outputs", "知识库运维", "W8-query-trace.jsonl")
MAIN_SET = os.path.join(IDX, "eval_sets", "v2026.11.json")
SPANS_OUT = os.path.join(OUT_DIR, "otel-spans.jsonl")
METRICS_OUT = os.path.join(OUT_DIR, "observability.json")

ZERO_RESULT_ALERT_PCT = 5.0       # W9 卡：>5% 告警（起点 33/1003=3%）
FRESHNESS_SAMPLE_N = 20           # W9 卡：随机抽 20 篇
SEED = 20261110
SEGMENTS = ("embed", "retrieve", "rerank", "generate")


def _load(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _p95(vals):
    """小样本 p95 用线性插值；样本 < 2 时如实返回 None，不拿单条冒充 p95。"""
    if not vals:
        return None
    if len(vals) < 2:
        return None
    s = sorted(vals)
    idx = 0.95 * (len(s) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 3)


# ------------------------------------------------------------------ OTel span 结构
def span(trace_id, name, parent_id, start_ns, dur_ms, attributes, status="OK"):
    sid = uuid.uuid4().hex[:16]
    return {
        "traceId": trace_id,
        "spanId": sid,
        "parentSpanId": parent_id,
        "name": name,
        "kind": "SPAN_KIND_INTERNAL",
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + int(dur_ms * 1_000_000)),
        "duration_ms": round(dur_ms, 3),
        "attributes": attributes,
        "status": {"code": status},
    }


def build_spans(rec):
    """把一条 query_trace 记录映射成四段 OTel span。🔴 不伪造 generate 段耗时。"""
    tid = uuid.uuid4().hex[:32]
    root = span(tid, "kb.query", None, time.time_ns(), 0.0,
                {"kb.query": rec.get("query", ""), "kb.topk": 5,
                 "kb.param_snapshot_hash": (rec.get("param_snapshot") or {}).get("hash"),
                 "kb.hit_gt": rec.get("hit_gt")})
    root_id = root["spanId"]
    t = rec.get("timing_ms") or {}
    embed = t.get("vec_ms", 0.0)                       # BGE 编码 + 向量检索
    retrieve = sum(t.get(k, 0.0) for k in ("kw_ms", "substr_ms", "rrf_ms"))
    rerank = t.get("search_unified_ms", 0.0)           # 含 CE 精排的整段（见 attributes 口径）
    total = t.get("total_ms") or (embed + retrieve + rerank)

    out = [root]
    prev = root_id
    base_ns = int(root["startTimeUnixNano"])
    for name, dur, attrs, status in (
        ("kb.embed", embed,
         {"kb.stage": "embed", "kb.source_field": "timing_ms.vec_ms",
          "kb.lane": "bge-small-zh", "kb.note": "含 query 编码与向量检索"}, "OK"),
        ("kb.retrieve", retrieve,
         {"kb.stage": "retrieve", "kb.source_field": "kw_ms+substr_ms+rrf_ms",
          "kb.lanes": "FTS5(jieba) + 字级 substring → RRF(k=2)",
          "kb.candidate_counts": {k: len(v) for k, v in
                                  (rec.get("three_lane_candidates") or {}).items()}}, "OK"),
        ("kb.rerank", rerank,
         {"kb.stage": "rerank", "kb.source_field": "timing_ms.search_unified_ms",
          "kb.caveat": ("该字段是「完整融合检索 + CE 精排」整段耗时，trace 未拆分独立 rerank 段；"
                        "不可与 embed/retrieve 相加当作总耗时"),
          "kb.rerank_scored": len(rec.get("rerank_scores") or [])}, "OK"),
        ("kb.generate", 0.0,
         {"kb.stage": "generate", "kb.no_generate_stage": True,
          "kb.note": "🔴 检索系统无生成段：本库只做检索，不产出答案；此段存在仅为 OTel 分层对齐",
          "kb.fake_duration": False}, "UNSET"),
    ):
        s = span(tid, name, root_id, base_ns, dur, attrs, status)
        out.append(s)
        prev = s["spanId"]
    root["duration_ms"] = round(total, 3)
    root["endTimeUnixNano"] = str(base_ns + int(total * 1_000_000))
    root["attributes"]["kb.span_count"] = len(out)
    root["attributes"]["kb.segment_sum_ms"] = round(embed + retrieve + rerank, 3)
    return out


# ------------------------------------------------------------------ 三张门禁指标
def zero_result_rate(conn):
    n = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    z = conn.execute("SELECT COUNT(*) FROM query_log WHERE n_results=0").fetchone()[0]
    pct = round(z * 100 / n, 4) if n else None
    return {"total_queries": n, "zero_hit": z, "zero_result_pct": pct,
            "threshold_pct": ZERO_RESULT_ALERT_PCT,
            "alert": (pct is not None and pct > ZERO_RESULT_ALERT_PCT),
            "note": "query_log 是生产表，本脚本 readonly 读取，不写不删"}


def index_freshness(conn, kb_root):
    """随机抽 20 篇已索引文档，对照源文件 mtime，算中位年龄与索引滞后。"""
    rows = conn.execute(
        "SELECT path, MAX(mtime) AS m FROM chunks WHERE path IS NOT NULL GROUP BY path"
    ).fetchall()
    paths = [r[0] for r in rows if r[0]]
    rnd = random.Random(SEED)
    picked = rnd.sample(paths, min(FRESHNESS_SAMPLE_N, len(paths)))
    now = time.time()
    ages, lags, missing = [], [], []
    for p in picked:
        full = os.path.join(kb_root, p.replace("/", os.sep))
        if not os.path.isfile(full):
            missing.append(p)
            continue
        src = os.path.getmtime(full)
        ages.append((now - src) / 86400.0)
        idx_m = next((r[1] for r in rows if r[0] == p), None)
        if idx_m:
            # mtime 可能是字符串或时间戳，容错处理
            try:
                im = float(idx_m)
                lags.append(abs(im - src) / 86400.0)
            except (TypeError, ValueError):
                pass
    return {
        "sample_n": len(picked), "found": len(ages), "missing": len(missing),
        "source_median_age_days": round(statistics.median(ages), 2) if ages else None,
        "index_source_median_lag_days": round(statistics.median(lags), 2) if lags else None,
        "note": ("随机抽 20 篇（固定 seed %d）对照源文件 mtime；"
                 "中位年龄是『源文件多久没更新』，滞后是『索引 mtime 与源文件 mtime 差』" % SEED),
        "missing_paths": missing[:5],
    }


def latency_p95(records):
    """每层耗时 p95，样本量如实报；<2 条不冒充 p95。"""
    out = {}
    for key, label in (("vec_ms", "embed"), ("kw_ms", "kw_lane"),
                       ("substr_ms", "substr_lane"), ("rrf_ms", "rrf_fusion"),
                       ("search_unified_ms", "rerank_inclusive"),
                       ("total_ms", "total")):
        vals = [(r.get("timing_ms") or {}).get(key) for r in records]
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        out[label] = {"n": len(vals), "p95_ms": _p95(vals),
                      "median_ms": round(statistics.median(vals), 3) if vals else None}
    out["caveat"] = ("W8 trace 的 vec_ms / search_unified_ms 首轮含模型加载；"
                     "样本量 n 逐项如实报，n<2 时 p95=None 不伪造")
    return out


# ------------------------------------------------------------------ 真跑
def live_traces(conn, n):
    """复用 W8 轮子 query_trace.trace_query（只读、不落盘：KB_TRACE 不设为 1）。"""
    import query_trace
    items = _load(MAIN_SET, []) or []
    rnd = random.Random(SEED)
    # 固定分层：每桶抽一条，避免全 C 桶
    by = {}
    for it in items:
        by.setdefault(it.get("bucket") or "(无)", []).append(it)
    picked = []
    for b in sorted(by):
        rnd.shuffle(by[b])
        if by[b] and len(picked) < n:
            picked.append(by[b][0])
    recs = []
    for it in picked[:n]:
        try:
            rec = query_trace.trace_query(conn, it["query"], topk=5,
                                          gt=it.get("gt") or [])
            recs.append(rec)
            print("  · 真跑 [%s] %s｜total %.1f ms｜hit_gt=%s" % (
                it.get("bucket"), it["query"][:28],
                (rec.get("timing_ms") or {}).get("total_ms", 0), rec.get("hit_gt")))
        except Exception as e:                        # 单条失败不中断
            print("  ⚠ 真跑失败（跳过）：%s｜%s" % (it["query"][:24], str(e)[:80]),
                  file=sys.stderr)
    return recs


def self_test():
    ok = True

    def chk(name, got, expect):
        nonlocal ok
        p = got == expect
        ok &= p
        print("  [%s] %-50s got=%s expect=%s" % ("PASS" if p else "FAIL", name, got, expect))

    print("=== w9_observability --self-test（内存数据，不落盘）===")
    chk("p95 样本不足返回 None", _p95([1.0]), None)
    chk("p95 空样本返回 None", _p95([]), None)
    chk("p95 双样本线性插值", _p95([0.0, 100.0]), 95.0)
    rec = {"query": "q", "hit_gt": True, "param_snapshot": {"hash": "abc"},
           "timing_ms": {"vec_ms": 10, "kw_ms": 2, "substr_ms": 1, "rrf_ms": 0.5,
                         "search_unified_ms": 20, "total_ms": 33.5},
           "three_lane_candidates": {"kw": [{"path": "a"}], "vec": [], "substr": []},
           "rerank_scores": [{"path": "a"}]}
    spans = build_spans(rec)
    chk("四段 + root = 5 个 span", len(spans), 5)
    chk("span 名逐段对齐", [s["name"] for s in spans[1:]],
        ["kb.embed", "kb.retrieve", "kb.rerank", "kb.generate"])
    gen = spans[-1]
    chk("generate 段标无生成段", gen["attributes"]["kb.no_generate_stage"], True)
    chk("generate 段不伪造耗时", gen["duration_ms"], 0.0)
    chk("generate 段状态 UNSET", gen["status"]["code"], "UNSET")
    chk("traceId 全局唯一 32 hex", len(spans[0]["traceId"]), 32)
    chk("spanId 16 hex 且互异", len({s["spanId"] for s in spans}), 5)
    chk("同 trace 内 parent 一致", all(s["parentSpanId"] == spans[0]["spanId"]
                                     for s in spans[1:]), True)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="W9-C OTel spans + 三张门禁指标")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--rounds", type=int, default=5, help="真跑查询条数（分层抽，每桶一条）")
    ap.add_argument("--no-live", action="store_true", help="只用 W8 既有 JSONL，不真跑")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    if args.self_test:
        rc = self_test()
        print("结果：%s" % ("全 PASS" if rc == 0 else "有 FAIL"))
        return rc

    import kb_index
    conn = kb_index.connect(kb_index.DB_PATH, readonly=True)
    readonly_ok = False
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,top1_path) "
            "VALUES('_w9obs_ro_probe_','_w9obs_ro_probe_','x',1,1,1,'x')")
        conn.commit()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        readonly_ok = True
    if not readonly_ok:
        print("🔴 readonly 自证失败，立刻停", file=sys.stderr)
        conn.close()
        return 3
    n0 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]

    w8 = []
    if os.path.isfile(TRACE_JSONL):
        with open(TRACE_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        w8.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    print("=== W9-C 追踪与可观测｜W8 既有 trace %d 条 ===" % len(w8))
    live = []
    if not args.no_live:
        print("真跑 %d 条（分层，每桶一条；复用 W8 query_trace.trace_query，KB_TRACE 默认 off 不落盘）…"
              % args.rounds)
        live = live_traces(conn, args.rounds)

    print("算三张门禁指标…")
    zero = zero_result_rate(conn)
    fresh = index_freshness(conn, KB_ROOT)
    p95 = latency_p95(w8 + live)
    n1 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    conn.close()

    all_spans = []
    for rec in (w8 + live):
        all_spans.extend(build_spans(rec))
    if os.environ.get("KB_W9_TRACE") == "1":
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(SPANS_OUT, "w", encoding="utf-8") as f:
            for s in all_spans:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print("OTLP/JSON spans 落盘：%s（%d 条）" % (SPANS_OUT, len(all_spans)))
    else:
        print("KB_W9_TRACE 未开 → spans 只在内存生成（默认 off，一键可开）")

    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "otel": {
            "impl": "OTLP/JSON 兼容 span 结构（纯标准库，未装 opentelemetry-sdk）",
            "switch": "KB_W9_TRACE=1 一键可开，默认 off；零外部 exporter",
            "cloud_exporter": "NONE（🔴 不接 Langfuse / Arize Phoenix / 任何云端）",
            "segments": list(SEGMENTS),
            "generate_stage": "检索系统无生成段（如实标注，不伪造耗时）",
            "n_traces": len(w8) + len(live),
            "n_spans": len(all_spans),
            "spans_path": SPANS_OUT if os.environ.get("KB_W9_TRACE") == "1" else "(内存)",
        },
        "zero_result_rate": zero,
        "index_freshness": fresh,
        "latency_p95": p95,
        "readonly": {"enforced": readonly_ok, "query_log_before": n0,
                     "query_log_after": n1},
        "w8_traces": len(w8), "live_traces": len(live),
    }
    out = args.json_out or METRICS_OUT
    if not os.path.isabs(out):
        out = os.path.join(OUT_DIR, out)
    _dump(out, result)

    print("\n--- 三张门禁指标 ---")
    print("零结果率：%s%%（%d/%d，阈值 >%s%% 告警，当前 alert=%s）" % (
        zero["zero_result_pct"], zero["zero_hit"], zero["total_queries"],
        zero["threshold_pct"], zero["alert"]))
    print("index freshness：抽 %d 篇 / 命中 %d｜源文件中位年龄 %s 天｜索引-源中位差 %s 天" % (
        fresh["sample_n"], fresh["found"], fresh["source_median_age_days"],
        fresh["index_source_median_lag_days"]))
    print("每层耗时 p95（n 如实报）：")
    for k, v in p95.items():
        if isinstance(v, dict):
            print("  · %-18s n=%-2d p95=%s ms｜median=%s" % (
                k, v["n"], v["p95_ms"], v["median_ms"]))
    print("readonly 生效：%s｜query_log %d → %d" % (readonly_ok, n0, n1))
    print("产物：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
