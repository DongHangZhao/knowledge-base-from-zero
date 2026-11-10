#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · E：离线批处理服务化 —— 断点续跑 / 失败队列 / 指数退避 / trace 汇总。

判据（W12 卡 §E）：**杀进程后重跑，tokens 不重复增长**。
机制：
  · 每条任务稳定 ID = sha1(task + 规范化后的 item)[:16]；
  · `state/batch_state.json` 的 done 表逐条原子落盘（tmp + replace），kill 不丢；
  · 重跑先查 done，命中即跳过（不重复计费）；
  · 失败入 `state/batch_failed.json`，指数退避 2**attempts 秒（上限 16），重试上限 3；
  · `--limit N` 只处理前 N 条未完成任务，用来真模拟「中途被杀」。

两种模式：
  · `--dry-run`：本地模拟计费单元（每条固定 10 tokens），不联网、不调 LLM，
    用来对「去重」判据做可复跑的形式化验证；
  · 真实模式：走 enhance / b3_review 的任务层（默认 off 时自动降级，不联网）。
    🔴 真实计费以 `state/llm_trace.jsonl` + `report.py` 为准，本脚本不虚报 tokens。

`--selftest` 真跑一次「跑 20 条 → 中断 → 重跑全部」，把前后 tokens 写进
`state/batch_selftest.json` 留痕，rc=0 才算判据成立。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import llm_kit  # noqa: E402

STATE = Path(__file__).resolve().parent.parent / "state"
DEFAULT_STATE = STATE / "batch_state.json"
FAILED = STATE / "batch_failed.json"
SELFTEST_ITEMS = STATE / "batch_selftest_items.json"
SELFTEST_STATE = STATE / "batch_selftest_state.json"
SELFTEST_REPORT = STATE / "batch_selftest.json"
MAX_ATTEMPTS = 3
BACKOFF_CAP = 16.0
DRY_TOKENS = 10


def item_id(task: str, item: dict) -> str:
    raw = task + "\x00" + json.dumps(item, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def handler_for(task: str, dry_run: bool):
    if dry_run:
        return lambda item: {"ok": True, "degraded": False, "cached": False,
                             "reason": "dry_run", "usage": {"total_tokens": DRY_TOKENS}}
    if task == "rewrite":
        import enhance
        return lambda item: enhance.llm_rewrite(str(item.get("query", "")))
    if task == "relation":
        import enhance
        return lambda item: enhance.llm_relation(
            str(item.get("head", "")), str(item.get("phrase", "")), str(item.get("tail", "")))
    if task == "edge_valid":
        import b3_review
        return lambda item: b3_review.review(item)
    raise ValueError("未知 task：%s" % task)


def run(items, task: str, state_path: Path, *, dry_run: bool, limit: int,
        reset: bool = False, now: float = None) -> dict:
    now = time.time() if now is None else now
    st = {"done": {}, "runs": []} if reset else load_json(
        state_path, {"done": {}, "runs": []})
    st.setdefault("done", {})
    st.setdefault("runs", [])
    failed = load_json(FAILED, {})
    handler = handler_for(task, dry_run)

    processed, skipped, newly_failed = 0, 0, 0
    for item in items:
        if limit and processed >= limit:
            break
        iid = item_id(task, item)
        if iid in st["done"]:
            skipped += 1
            continue
        f = failed.get(iid)
        if f and f.get("attempts", 0) >= MAX_ATTEMPTS:
            skipped += 1
            continue
        if f and float(f.get("next_retry", 0)) > now:
            skipped += 1
            continue
        try:
            res = handler(item)
            if not isinstance(res, dict):
                raise ValueError("handler 返回不是 dict")
            usage = res.get("usage") or {}
            tok = int(usage.get("total_tokens", 0)) if isinstance(usage, dict) else 0
            st["done"][iid] = {"task": task, "tokens": tok,
                               "degraded": bool(res.get("degraded")),
                               "cached": bool(res.get("cached")),
                               "reason": str(res.get("reason", ""))[:120],
                               "at": datetime.now().astimezone().isoformat(timespec="seconds")}
            failed.pop(iid, None)
            processed += 1
            atomic_write(state_path, st)   # 逐条落盘：kill 后已完成的不丢
        except Exception as exc:  # 单条失败不中断（脚本四条）
            attempts = int((f or {}).get("attempts", 0)) + 1
            failed[iid] = {"task": task, "attempts": attempts,
                           "reason": str(exc)[:200],
                           "next_retry": now + min(2 ** attempts, BACKOFF_CAP)}
            newly_failed += 1
            atomic_write(FAILED, failed)

    total_tokens = sum(int(v.get("tokens", 0)) for v in st["done"].values())
    st["runs"].append({"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "task": task, "dry_run": dry_run, "limit": limit,
                       "processed": processed, "skipped": skipped,
                       "newly_failed": newly_failed, "total_tokens": total_tokens})
    atomic_write(state_path, st)
    atomic_write(FAILED, failed)
    return {"processed": processed, "skipped": skipped, "newly_failed": newly_failed,
            "done": len(st["done"]), "total_tokens": total_tokens,
            "failed_queue": len(failed), "state": str(state_path),
            "runs": st["runs"][-2:]}


def selftest() -> dict:
    """真跑「中断 → 重跑」，断言 tokens 只增长新处理部分。"""
    items = [{"n": i, "query": "断点续跑自测 %d" % i} for i in range(41)]
    atomic_write(SELFTEST_ITEMS, items)
    # 🔴 不做减法：自建的 state 测试产物用空内容覆盖（unlink 属删除，得先过目拿许可）
    atomic_write(SELFTEST_STATE, {"done": {}, "runs": []})
    atomic_write(FAILED, {})

    r1 = run(items, "selftest", SELFTEST_STATE, dry_run=True, limit=20, reset=True)
    # 第二次不 reset：模拟 kill 之后重新启动同一批任务
    r2 = run(items, "selftest", SELFTEST_STATE, dry_run=True, limit=1000)

    expected = (r1["processed"] + r2["processed"]) * DRY_TOKENS
    checks = [
        {"case": "first_batch_20", "ok": r1["processed"] == 20 and r1["total_tokens"] == 200},
        {"case": "resume_only_remaining", "ok": r2["processed"] == 21 and r2["skipped"] == 20},
        {"case": "tokens_not_double_charged",
         "ok": r2["total_tokens"] == expected == 410 and r1["total_tokens"] != r2["total_tokens"]},
        {"case": "all_done", "ok": r2["done"] == 41 and r2["failed_queue"] == 0},
        {"case": "third_run_is_noop",
         "ok": (lambda r3: r3["processed"] == 0 and r3["total_tokens"] == 410)(
             run(items, "selftest", SELFTEST_STATE, dry_run=True, limit=1000))},
    ]
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "n_items": len(items), "dry_run_tokens_each": DRY_TOKENS,
           "phase1": r1, "phase2": r2, "checks": checks,
           "passed": sum(1 for c in checks if c["ok"]), "total": len(checks),
           "note": ("dry-run 用固定计费单元验证「已处理 ID 不重复计费」的逻辑；"
                    "真实 tokens 以 state/llm_trace.jsonl + report.py 为准，不拿模拟值冒充真实调用成本。")}
    atomic_write(SELFTEST_REPORT, rep)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 离线批处理（W12 · E）")
    ap.add_argument("--items", help="任务列表 JSON 文件（list[dict]）")
    ap.add_argument("--task", default="selftest",
                    choices=["selftest", "rewrite", "relation", "edge_valid"])
    ap.add_argument("--state", help="断点状态文件路径（默认 state/batch_state.json）")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多处理 N 条未完成任务（模拟中断）")
    ap.add_argument("--reset", action="store_true", help="清空断点重新跑（仅对指定 state 文件）")
    ap.add_argument("--dry-run", action="store_true", help="本地模拟，不联网不调 LLM")
    ap.add_argument("--selftest", action="store_true", help="真跑中断→重跑判据，写留痕")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        rep = selftest()
        print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json else
              ("断点续跑自测：%d/%d 通过 · 首次 %d 条 %d tokens → 重跑 %d 条累计 %d tokens"
               % (rep["passed"], rep["total"], rep["phase1"]["processed"],
                  rep["phase1"]["total_tokens"], rep["phase2"]["processed"],
                  rep["phase2"]["total_tokens"])))
        return 0 if rep["passed"] == rep["total"] else 3

    if not a.items:
        print("需要 --items <json> 或 --selftest", file=sys.stderr)
        return 2
    items = load_json(Path(a.items), None)
    if not isinstance(items, list):
        print("--items 必须是 list[dict]", file=sys.stderr)
        return 2
    sp = Path(a.state) if a.state else DEFAULT_STATE
    rep = run(items, a.task, sp, dry_run=a.dry_run, limit=a.limit, reset=a.reset)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("批处理：新处理 %d · 跳过 %d · 累计 done %d · tokens %d · 失败队列 %d" %
              (rep["processed"], rep["skipped"], rep["done"],
               rep["total_tokens"], rep["failed_queue"]))
        print("断点：%s" % rep["state"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
