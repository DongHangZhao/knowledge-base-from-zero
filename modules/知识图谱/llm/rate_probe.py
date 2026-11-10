#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · D：429 连发上限压测（默认**不联网**）。

两种口径，别混：
  · `--history`（默认）：只读 `state/llm_trace.jsonl`，报历史调用量与 429 证据，零网络请求。
  · `--run --burst N`：真发 N 次内容安全的不同提示词，关掉主动限速与重试
    （`KG_LLM_MIN_INTERVAL=0` / `KG_LLM_RETRIES=0`），测「无限速连发上限」。
    🔴 会消耗真实配额，默认不做；结果写 `state/llm_rate_probe.json`。

口径说明（写进 README 时别改）：本压测测的是**单进程无主动限速时的连发上限**，
不是多进程并发上限；共享端点经验值为「8 连发即 429」，因此默认限速 2.5s、并发建议 1。
压测提示词固定为无敏感内容的短句，仍会走 `prepare()` 出口门禁。

退出码：0 正常；2 参数错；3 真跑时开关/凭据未就绪（不假装压过）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import llm_kit  # noqa: E402

STATE = Path(__file__).resolve().parent.parent / "state"
OUT = STATE / "llm_rate_probe.json"
TRACE = Path(llm_kit.TRACE_FILE)


def history() -> dict:
    rows = []
    if TRACE.is_file():
        for line in TRACE.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    r429 = [r for r in rows if "429" in str(r.get("reason", ""))]
    degraded = [r for r in rows if r.get("degraded")]
    real = [r for r in rows if not r.get("cached")]
    return {
        "mode": "history", "network_calls": 0,
        "trace_records": len(rows), "network_trace_records": len(real),
        "degraded_records": len(degraded), "http_429_records": len(r429),
        "first_429_reasons": [str(r.get("reason")) for r in r429[:5]],
        "known_from_readme": "共享端点 8 连发即 429；已加主动限速（默认 2.5s）+ 尊重 Retry-After",
        "conclusion": "默认限速 2.5s、并发 1；批量任务调大 KG_LLM_MIN_INTERVAL",
        "note": "本模式零网络请求；要真测连发上限用 --run（会消耗配额）",
    }


def run_burst(n: int) -> dict:
    cfg = llm_kit.settings()
    if not cfg["enabled"] or not cfg["has_key"]:
        return {"mode": "run", "ok": False,
                "why": "开关或凭据未就绪，未发起任何请求（不假装压过）",
                "enabled": cfg["enabled"], "has_key": cfg["has_key"]}
    # 只在本进程内改限速/重试，不动任何配置文件；不打印凭据
    os.environ["KG_LLM_MIN_INTERVAL"] = "0"
    os.environ["KG_LLM_RETRIES"] = "0"
    out = []
    t0 = time.perf_counter()
    for i in range(n):
        # 每次 prompt 不同，确保不命中缓存；内容安全、过出口门禁
        r = llm_kit.chat("连发压测第 %d 次：只回复 ok" % (i + 1),
                         system="你是连通性探针，只输出 ok。", task="rate_probe",
                         max_tokens=8, prompt_version="rate_probe_v1")
        out.append({"i": i + 1, "ok": bool(r.get("ok")),
                    "degraded": bool(r.get("degraded")),
                    "cached": bool(r.get("cached")),
                    "reason": str(r.get("reason", ""))[:120],
                    "latency_ms": r.get("latency_ms")})
    elapsed = int((time.perf_counter() - t0) * 1000)
    hit = [x["i"] for x in out if "429" in x["reason"]]
    ok = sum(1 for x in out if x["ok"] and not x["degraded"])
    return {
        "mode": "run", "ok": True,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "burst": n, "elapsed_ms": elapsed,
        "success": ok, "degraded": n - ok,
        "http_429_count": len(hit), "first_429_at": hit[0] if hit else None,
        "min_interval_set": "0（压测专用）", "retries_set": "0（压测专用）",
        "conclusion": ("无主动限速时第 %s 次开始 429 → 生产保持 2.5s 限速、并发 1"
                       % hit[0] if hit else
                       "本次 %d 连发未触发 429；不因此上调并发（共享端点会波动）" % n),
        "runs": out,
        "note": "真调用，凭据只走库外环境变量；结果为当次实测，不是常量",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="429 连发上限压测（W12 · D）")
    ap.add_argument("--run", action="store_true", help="真发请求（消耗配额）")
    ap.add_argument("--burst", type=int, default=8, help="连发次数，默认 8")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.burst < 1 or a.burst > 30:
        print("--burst 需在 1..30", file=sys.stderr)
        return 2

    rep = run_burst(a.burst) if a.run else history()
    if a.run:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(OUT)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        if a.run:
            print("429 连发压测：%d 次 · 成功 %d · 降级 %d · 429 %d · 首个 429 于第 %s 次 · %d ms"
                  % (rep["burst"], rep["success"], rep["degraded"],
                     rep["http_429_count"], rep["first_429_at"], rep["elapsed_ms"]))
            print(rep["conclusion"])
            print("留痕：%s" % OUT.name)
        else:
            print("历史留痕：%d 条（网络 %d）· 降级 %d · 429 记录 %d"
                  % (rep["trace_records"], rep["network_trace_records"],
                     rep["degraded_records"], rep["http_429_records"]))
            print(rep["known_from_readme"])
            print("零网络请求；真测加 --run。")
    return 3 if (a.run and not rep.get("ok")) else 0


if __name__ == "__main__":
    sys.exit(main())
