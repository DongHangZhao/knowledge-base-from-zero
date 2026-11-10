#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · D/E：缓存命中率 + 成本延迟报表（按 task 维度）。

读 `state/llm_trace.jsonl` 与 `state/llm_cache/*.json`，纯本地、不联网、不调 LLM。
输出 `state/llm_cost_report.json`（机读原件）+ 控制台人读表；重复跑幂等覆盖。

按 task 出四项：命中率 / 延迟 p50·p90 / 降级率 / tokens；
另给缓存容量、TTL 设置、提示词版本分布（供 W9 接 OTel span 时按 task 对齐）。

退出码：0 正常（trace 不存在也报 0 文件，不假装跑过）；2 参数错。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import llm_kit

STATE = Path(__file__).resolve().parent.parent / "state"
TRACE = llm_kit.TRACE_FILE
CACHE = llm_kit.CACHE_DIR
OUT = STATE / "llm_cost_report.json"


def pct(vals, q):
    """线性插值百分位；空列表返回 None（不编 0）。"""
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return round(float(s[0]), 2)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(float(s[lo] + (s[hi] - s[lo]) * (pos - lo)), 2)


def read_trace():
    rows, bad = [], 0
    if not Path(TRACE).is_file():
        return rows, bad
    with Path(TRACE).open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
                continue  # 单条坏行不中断（脚本四条）
    return rows, bad


def cache_stats():
    files = sorted(CACHE.glob("*.json")) if CACHE.is_dir() else []
    if not files:
        return {"files": 0, "bytes": 0, "oldest": None, "newest": None}
    stamps = [f.stat().st_mtime for f in files]
    fmt = lambda t: datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files),
            "oldest": fmt(min(stamps)), "newest": fmt(max(stamps))}


def build() -> dict:
    rows, _ = read_trace()
    by_task = defaultdict(list)
    for r in rows:
        by_task[str(r.get("task") or "<未标 task>")].append(r)

    tasks, totals = {}, Counter()
    lat_all = []
    for task, rs in sorted(by_task.items()):
        n = len(rs)
        cached = sum(1 for r in rs if r.get("cached") is True)
        degraded = sum(1 for r in rs if r.get("degraded") is True)
        # 延迟只统计真实网络调用（cached 记录的 0ms 会拉低 p50）
        lat = [float(r["latency_ms"]) for r in rs
               if not r.get("cached") and isinstance(r.get("latency_ms"), (int, float))]
        lat_all.extend(lat)
        tok = 0
        for r in rs:
            u = r.get("usage") or {}
            v = u.get("total_tokens") if isinstance(u, dict) else None
            if isinstance(v, (int, float)):
                tok += int(v)
        versions = Counter(str(r.get("prompt_version") or "<旧键>") for r in rs)
        tasks[task] = {
            "calls": n,
            "cached": cached,
            "hit_rate": round(cached / n, 4) if n else None,
            "degraded": degraded,
            "degrade_rate": round(degraded / n, 4) if n else None,
            "network_calls": len(lat),
            "latency_p50_ms": pct(lat, 0.5),
            "latency_p90_ms": pct(lat, 0.9),
            "tokens": tok,
            "prompt_versions": dict(versions),
        }
        totals["calls"] += n
        totals["cached"] += cached
        totals["degraded"] += degraded
        totals["tokens"] += tok

    cfg = llm_kit.settings()
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"trace": str(Path(TRACE).name), "records": len(rows)},
        "cache_ttl_seconds": cfg["cache_ttl"],
        "cache": cache_stats(),
        "totals": {"calls": totals["calls"], "cached": totals["cached"],
                   "hit_rate": round(totals["cached"] / totals["calls"], 4)
                   if totals["calls"] else None,
                   "degraded": totals["degraded"], "tokens": totals["tokens"],
                   "network_latency_p50_ms": pct(lat_all, 0.5),
                   "network_latency_p90_ms": pct(lat_all, 0.9)},
        "by_task": tasks,
        "note": ("tokens 只统计 trace 里带 usage 的网络调用；缓存命中记录不含 usage，"
                 "故「tokens」不等于全部调用成本。TTL=0 表示永不过期（可复现优先）。"
                 "本报表由纯本地元信息生成，不含正文与凭据，也不由 LLM 自评。"),
        "w9_hook": "by_task 每项即一个可映射为 OTel span 的聚合单元，键名已固定",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 缓存/成本报表（W12 · D/E）")
    ap.add_argument("--json", action="store_true", help="stdout 只输出机读 JSON")
    ap.add_argument("--no-write", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()

    rep = build()
    if not a.no_write:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(OUT)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        t = rep["totals"]
        print("LLM 成本/命中率报表（来源 %s，%d 条留痕）" %
              (rep["source"]["trace"], rep["source"]["records"]))
        print("总计：calls %d · 缓存命中 %d (%s) · 降级 %d · tokens %d · 网络 p50/p90 %s/%s ms"
              % (t["calls"], t["cached"], t["hit_rate"], t["degraded"], t["tokens"],
                 t["network_latency_p50_ms"], t["network_latency_p90_ms"]))
        print("缓存：%d 文件 / %d B · TTL %s 秒" %
              (rep["cache"]["files"], rep["cache"]["bytes"], rep["cache_ttl_seconds"]))
        print("%-16s %6s %7s %8s %8s %9s %9s %8s" %
              ("task", "calls", "hit%", "degr%", "tok", "p50ms", "p90ms", "ver"))
        for name, v in rep["by_task"].items():
            print("%-16s %6d %7s %8s %8d %9s %9s %8s" %
                  (name[:16], v["calls"],
                   ("%.1f" % (100 * v["hit_rate"])) if v["hit_rate"] is not None else "-",
                   ("%.1f" % (100 * v["degrade_rate"])) if v["degrade_rate"] is not None else "-",
                   v["tokens"], v["latency_p50_ms"], v["latency_p90_ms"],
                   ",".join(v["prompt_versions"].keys())[:8]))
        print("机读原件：%s" % (OUT if not a.no_write else "<--no-write 未落盘>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
