#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · C：查询改写 A/B —— 规则改写 vs LLM 改写（接 W4 冻结题集）。

题集：`modules/知识库索引/eval_sets/v2026.11.json`（184 条，gt 人工冻结）。
🔴 选题规则写死：**按原序取前 N 条 `gt` 非空者**（默认 50），不挑题、不合成、
   不让 LLM 出题、不让 LLM 判命中（C4）。E 桶 gt 为空是负样本设计，天然排除。

两阶段分开跑（= 天然断点，LLM 调用不重复计费）：
  `--prepare`  规则改写（本地）+ LLM 改写（真调 API，可命中缓存），逐条落盘
               → `state/rewrite_candidates.json`
  `--eval`     读候选，在**一个进程**里复用 `kb_rag.search_unified` 跑 paired R@5，
               出 McNemar 精确 p 值 + 延迟 p50/p90 + 缓存命中率
               → `state/rewrite_ab_<时间>.json`

判据（W12 §四 #3）：**成对数据 + p 值 + 延迟增量，三者齐**。
p<0.05 且 LLM 净胜才算赢，否则如实记「平 / 不显著」。
🔴 无论输赢都**不接在线同步链路**；赢也只建议「离线预生成改写候选」。
🔴 两路检索均传 `rewrite="off"` 关掉 W4 内置改写，保证差异只来自本 A/B 的改写候选。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import enhance  # noqa: E402

HERE = Path(__file__).resolve().parent
KB_ROOT = HERE.parent.parent.parent
IDX = KB_ROOT / "modules" / "知识库索引"
EVALSET = IDX / "eval_sets" / "v2026.11.json"
STATE = HERE.parent / "state"
CAND = STATE / "rewrite_candidates.json"
PARTIAL = STATE / "rewrite_ab_partial.json"


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(float(s[0]), 2)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(float(s[lo] + (s[hi] - s[lo]) * (pos - lo)), 2)


def mcnemar_exact(llm_only: int, rule_only: int) -> float:
    """配对四格表的精确双尾检验（纯标准库，不依赖 scipy）。"""
    n = llm_only + rule_only
    if n == 0:
        return 1.0
    k = min(llm_only, rule_only)
    return min(1.0, 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n))


def snapshot_probe() -> dict:
    """读索引库指纹（只读）。🔴 为什么要它：2026-11-11 01:18 实跑时撞上别窗口
    全量重建（44,479 → 139,171 块），进程在 `load_bge()` 热重载处段错误（rc=139），
    且即便跑完，两臂读到的语料也不是同一份 —— 数字作废。所以「跑完」之外还要证
    「跑的过程中库没变」。
    """
    sys.path.insert(0, str(IDX))
    import kb_index  # noqa: E402

    conn = kb_index.connect()
    try:
        row = conn.execute("SELECT built_at, n FROM bge_model WHERE id=1").fetchone()
        ch = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        dc = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    finally:
        conn.close()
    return {"bge_built_at": row[0] if row else None,
            "bge_n": row[1] if row else None,
            "chunks": int(ch), "docs": int(dc)}


def pick_queries(n: int) -> list[dict]:
    if not EVALSET.is_file():
        raise FileNotFoundError("缺冻结题集：%s" % EVALSET)
    items = json.loads(EVALSET.read_text(encoding="utf-8"))
    picked = [it for it in items if it.get("gt")][:n]
    return [{"qid": "q%03d" % i, "query": it["query"],
             "gt": [str(g).replace("\\", "/") for g in it["gt"]],
             "bucket": it.get("bucket", "")}
            for i, it in enumerate(picked, 1)]


def prepare(n: int, limit: int) -> dict:
    qs = pick_queries(n)
    doc = load_json(CAND, {"meta": {}, "items": {}})
    doc.setdefault("items", {})
    doc["meta"] = {"evalset": str(EVALSET.relative_to(KB_ROOT)).replace("\\", "/"),
                   "n_requested": n,
                   "selection": "按冻结集原序取前 N 条 gt 非空者（不挑题、不 LLM 出题）",
                   "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    done = 0
    for q in qs:
        if q["qid"] in doc["items"]:
            continue
        if limit and done >= limit:
            break
        rule = enhance.rule_rewrite(q["query"])
        llm = enhance.llm_rewrite(q["query"])
        doc["items"][q["qid"]] = {
            **q,
            "rule_queries": rule["queries"],
            "llm_queries": llm["queries"],
            "llm_source": llm.get("source"),
            "llm_degraded": bool(llm.get("degraded")),
            "llm_cached": bool(llm.get("cached")),
            "llm_latency_ms": llm.get("latency_ms"),
            "llm_reason": llm.get("reason", ""),
            "prompt_version": llm.get("prompt_version", ""),
        }
        done += 1
        atomic_json(CAND, doc)  # 逐条落盘：中断后不重复计费
    return {"phase": "prepare", "n_total": len(qs), "new_this_run": done,
            "ready": len(doc["items"]),
            "note": "逐条落盘，重跑只补未完成的 qid（断点续跑）"}


def path_hit(paths, gt) -> bool:
    for p in paths:
        p = str(p).replace("\\", "/")
        for g in gt:
            if p == g or p.endswith("/" + g) or g.endswith("/" + p):
                return True
    return False


def evaluate(rerank: bool = False) -> dict:
    """rerank：默认 False。🔴 为什么默认关（2026-11-11 实测）：索引 4.4 万 → 13.9 万块后，
    开精排单条检索 13–31s，50 条两臂要 30+ 分钟，且连续两次在同一位置崩（rc=139 / rc=127）。
    关精排后两臂配置仍然**完全一致**，配对设计不受影响；口径写进报告 `retrieval_config`，
    不靠读者猜。
    """
    doc = load_json(CAND, None)
    if not doc or not doc.get("items"):
        raise RuntimeError("缺候选文件，请先跑 --prepare")
    snap0 = snapshot_probe()
    key = {**snap0, "rerank": bool(rerank)}
    sys.path.insert(0, str(IDX))
    import kb_index  # noqa: E402
    import kb_rag  # noqa: E402

    # 逐条落盘：断点续跑 + 同快照同口径才复用（换快照或换口径就整段作废重跑）
    part = load_json(PARTIAL, {"key": key, "rows": {}})
    if part.get("key") != key:
        print("[ab] 索引指纹或检索口径变了，旧进度作废重跑（%s → %s）"
              % (part.get("key"), key), file=sys.stderr)
        part = {"key": key, "rows": {}}

    conn = kb_index.connect()
    rows = []
    try:
        for qid, c in sorted(doc["items"].items()):
            if qid in part["rows"]:
                rows.append(part["rows"][qid])
                continue

            def one(qs):
                # 口径：各取「首个改写式」（下标 1）；规则版第 0 个是原式、LLM 版第 0 个也是原式。
                # 两边都不含原式，比较的才是「改写带来的增益」。
                q = qs[1] if len(qs) > 1 else qs[0]
                t0 = time.perf_counter()
                res, _note = kb_rag.search_unified(conn, q, topk=5, mode="fused",
                                                   rerank=rerank, rewrite="off")
                ms = round((time.perf_counter() - t0) * 1000, 2)
                return q, path_hit([r.get("path", "") for r in res], c["gt"]), ms

            rq, rhit, rms = one(c["rule_queries"])
            lq, lhit, lms = one(c["llm_queries"])
            row = {"qid": qid, "query": c["query"], "gt": c["gt"],
                   "bucket": c["bucket"],
                   "rule_query": rq, "rule_hit": rhit, "rule_retrieval_ms": rms,
                   "llm_query": lq, "llm_hit": lhit, "llm_retrieval_ms": lms,
                   "llm_degraded": c.get("llm_degraded"),
                   "llm_cached": c.get("llm_cached"),
                   "llm_latency_ms": c.get("llm_latency_ms")}
            rows.append(row)
            part["rows"][qid] = row
            atomic_json(PARTIAL, part)  # 逐条落盘：段错误/断电都不丢已跑的
    finally:
        conn.close()

    snap1 = snapshot_probe()
    stable = snap1 == snap0

    n = len(rows)
    llm_only = sum(1 for r in rows if r["llm_hit"] and not r["rule_hit"])
    rule_only = sum(1 for r in rows if r["rule_hit"] and not r["llm_hit"])
    both = sum(1 for r in rows if r["rule_hit"] and r["llm_hit"])
    neither = n - llm_only - rule_only - both
    p = mcnemar_exact(llm_only, rule_only)
    rule_acc = round(sum(1 for r in rows if r["rule_hit"]) / n, 4) if n else None
    llm_acc = round(sum(1 for r in rows if r["llm_hit"]) / n, 4) if n else None
    llm_lat = [float(r["llm_latency_ms"]) for r in rows
               if isinstance(r["llm_latency_ms"], (int, float))]
    ret_delta = [r["llm_retrieval_ms"] - r["rule_retrieval_ms"] for r in rows]
    cached = sum(1 for r in rows if r["llm_cached"])
    degraded = sum(1 for r in rows if r["llm_degraded"])
    win = (p < 0.05) and (llm_only > rule_only) and stable
    if not stable:
        verdict = ("【无效】评测期间索引被重建（快照指纹前后不一致：%s → %s），"
                   "数字不可用，须在静默窗口重跑" % (snap0, snap1))
    else:
        verdict = ("LLM 改写显著胜出（p<0.05）" if win else
                   "未达显著 / 记平（p≥0.05 或净胜为负），不做增益断言")

    rep = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evalset": str(EVALSET.relative_to(KB_ROOT)).replace("\\", "/"),
        "selection": "按冻结集原序取 gt 非空者；题面与 gt 均非 LLM 生成",
        "snapshot": {"before": snap0, "after": snap1, "stable": stable,
                     "why": "库在跑的过程中被重建 ⇒ 两臂语料不同源，数字作废（不信「跑完就算数」）"},
        "retrieval_config": {"mode": "fused", "rerank": bool(rerank), "rewrite": "off",
                             "why": "两臂检索配置必须逐位一致；关精排是为可跑完（索引 13.9 万块时"
                                    "开精排单条 13–31s 且连续崩），不影响配对比较"},
        "n": n,
        "paired": {"both_hit": both, "neither": neither,
                   "llm_only": llm_only, "rule_only": rule_only,
                   "n_discordant": llm_only + rule_only},
        "rule_r_at_5": rule_acc, "llm_r_at_5": llm_acc,
        "delta_pp": round((llm_acc - rule_acc) * 100, 2) if n else None,
        "mcnemar_exact_two_sided_p": round(p, 6),
        "significant": p < 0.05, "verdict": verdict,
        "latency": {"llm_rewrite_p50_ms": pct(llm_lat, 0.5),
                    "llm_rewrite_p90_ms": pct(llm_lat, 0.9),
                    "retrieval_delta_p50_ms": pct(ret_delta, 0.5),
                    "retrieval_delta_p90_ms": pct(ret_delta, 0.9),
                    "rule_retrieval_p50_ms": pct([r["rule_retrieval_ms"] for r in rows], 0.5),
                    "llm_retrieval_p50_ms": pct([r["llm_retrieval_ms"] for r in rows], 0.5),
                    "sync_increment_note": ("LLM 改写单次 p50 已是秒级，"
                                            "接同步链路会把在线延迟推到不可接受 —— 这是量化的「不接」依据")},
        "cache": {"cached": cached, "hit_rate": round(cached / n, 4) if n else None,
                  "degraded": degraded,
                  "degrade_rate": round(degraded / n, 4) if n else None},
        "recommendation": ("赢也只做「离线预生成改写候选」并落缓存，不接在线主链路；"
                           "未赢则如实记平，保留能力、改进题集或提示词后再测，"
                           "**不许用「收益不明显」砍能力**（AGENTS.md §5.11）"),
        "criteria": {"paired_rows": n > 0, "p_value": True,
                     "latency_increment": pct(llm_lat, 0.5) is not None or
                     pct(ret_delta, 0.5) is not None},
        "rows": rows,
    }
    tag = "" if stable else "INVALID_"
    out = STATE / ("rewrite_ab_%s%s.json" % (tag, datetime.now().strftime("%Y%m%d_%H%M%S")))
    atomic_json(out, rep)
    rep["artifact"] = out.name
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="查询改写 A/B（W12 · C）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--eval", action="store_true")
    ap.add_argument("--n", type=int, default=50, help="题集条数（50–100），默认 50")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多新生成 N 条（断点续跑）")
    ap.add_argument("--rerank", action="store_true",
                    help="开精排（慢且易崩，默认关；两臂始终同配置）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.n < 50 or a.n > 184:
        print("--n 需在 50..184", file=sys.stderr)
        return 2

    try:
        rep = prepare(a.n, a.limit) if a.prepare else evaluate(a.rerank)
    except (FileNotFoundError, RuntimeError, ImportError) as exc:
        print("A/B 失败（如实报，不降级成假结论）：%s" % exc, file=sys.stderr)
        return 3

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    elif a.prepare:
        print("候选生成：本轮新增 %d · 已就绪 %d/%d · 逐条落盘可断点续跑"
              % (rep["new_this_run"], rep["ready"], rep["n_total"]))
    else:
        print("改写 A/B：n=%d · 规则 R@5 %s → LLM %s (%+.2fpp) · McNemar p=%s · 净胜 %d/%d"
              % (rep["n"], rep["rule_r_at_5"], rep["llm_r_at_5"], rep["delta_pp"],
                 rep["mcnemar_exact_two_sided_p"], rep["paired"]["llm_only"],
                 rep["paired"]["rule_only"]))
        print("结论：%s" % rep["verdict"])
        print("快照：stable=%s · before=%s · after=%s"
              % (rep["snapshot"]["stable"], rep["snapshot"]["before"], rep["snapshot"]["after"]))
        print("检索口径：mode=%s rerank=%s rewrite=%s（两臂一致）"
              % (rep["retrieval_config"]["mode"], rep["retrieval_config"]["rerank"],
                 rep["retrieval_config"]["rewrite"]))
        print("延迟：LLM 改写 p50/p90 %s/%s ms · 检索增量 p50 %s ms · 缓存命中率 %s"
              % (rep["latency"]["llm_rewrite_p50_ms"], rep["latency"]["llm_rewrite_p90_ms"],
                 rep["latency"]["retrieval_delta_p50_ms"], rep["cache"]["hit_rate"]))
        print("逐条取证：%s" % rep["artifact"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
