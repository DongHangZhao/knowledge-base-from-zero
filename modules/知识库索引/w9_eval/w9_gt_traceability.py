#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
w9_gt_traceability.py — W9-E · gt 可追溯性校验钩子（交 W11 人验，不代人验）
============================================================================

一句话：给 `eval_sets/candidates/gt_todo_16.json` 配一个只读钩子——
        检查某条 gt 路径在 chunks 表里是否真能追到 chunk（有 path + chunk_no + 正文）。

🔴 边界：
   · `gt_todo_16.json` 的 rule 写明「只是优先人验建议，不是已确认可入主集的 16 条」，
     gt 一律为空。本钩子**不代填 gt、不改主集、不改候选文件**。
   · `top1_path` 只是检索线索不是答案 ⇒ 只做「线索可否追溯」预检，
     结果标 🔍 预检，不写「gt 已确认」。**人验归 W11**。
   · 只读连接 + INSERT 自证，不污染 query_log。

用法（venv 解释器）：
  "$PY" w9_eval/w9_gt_traceability.py --self-test
  "$PY" w9_eval/w9_gt_traceability.py
  "$PY" w9_eval/w9_gt_traceability.py --gt "wiki/技术/xxx.md"   # 单条 gt 预检

退出码：0 正常 / 1 自测失败 / 2 输入缺失 / 3 readonly 自证失败
作者：落花飞雪· ｜ 许可：KBL-1.0
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.dirname(HERE)
sys.path.insert(0, IDX)
KB_ROOT = os.path.dirname(os.path.dirname(IDX))
OUT_DIR = os.path.join(KB_ROOT, "outputs", "知识库运维", "W9-评测对齐")

GT_TODO = os.path.join(IDX, "eval_sets", "candidates", "gt_todo_16.json")
MAIN_SET = os.path.join(IDX, "eval_sets", "v2026.11.json")
REPORT_OUT = os.path.join(OUT_DIR, "gt16_traceability.json")


def _load(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def trace_one(conn, path):
    """纯查询：gt 路径能否在 chunks 表追到实体块。"""
    if not path:
        return {"traceable": False, "reason": "空路径"}
    rows = conn.execute(
        "SELECT id, chunk_no, heading, LENGTH(content) FROM chunks "
        "WHERE path=? ORDER BY chunk_no LIMIT 200", (path,)).fetchall()
    full = os.path.join(KB_ROOT, path.replace("/", os.sep))
    return {
        "path": path,
        "n_chunks": len(rows),
        "chunk_nos": [r[1] for r in rows[:10]],
        "headings": [r[2] for r in rows[:3] if r[2]],
        "source_file_exists": os.path.isfile(full),
        "traceable": bool(rows) and os.path.isfile(full),
        "reason": ("可追溯到 %d 个 chunk 且源文件存在" % len(rows)) if rows else
                  "chunks 表无此 path（可能未索引 / 已改名 / 是备份目录）",
    }


def self_test():
    print("=== w9_gt_traceability --self-test（内存假 conn，不碰生产库）===")
    import sqlite3 as sq

    class FakeConn:
        def execute(self, sql, params=()):
            class Cur:
                def fetchall(self):
                    if params and params[0] == "wiki/ok.md":
                        return [(1, 0, "标题", 400), (2, 1, "二节", 380)]
                    return []
            return Cur()

    ok = trace_one(FakeConn(), "wiki/ok.md")["n_chunks"] == 2
    empty = trace_one(FakeConn(), "")["traceable"] is False
    miss = trace_one(FakeConn(), "wiki/missing.md")["n_chunks"] == 0
    for name, v in (("命中 2 块", ok), ("空路径不追溯", empty), ("缺失路径 0 块", miss)):
        print("  [%s] %s" % ("PASS" if v else "FAIL", name))
    return 0 if all((ok, empty, miss)) else 1


def main():
    ap = argparse.ArgumentParser(description="W9-E gt 可追溯性校验钩子")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--gt", action="append", default=[], help="单条/多条 gt 路径预检")
    args = ap.parse_args()
    if args.self_test:
        rc = self_test()
        print("结果：%s" % ("全 PASS" if rc == 0 else "有 FAIL"))
        return rc

    todo = _load(GT_TODO)
    if not todo:
        print("🔴 找不到 %s" % GT_TODO, file=sys.stderr)
        return 2

    import kb_index
    conn = kb_index.connect(kb_index.DB_PATH, readonly=True)
    ro = False
    try:
        conn.execute(
            "INSERT INTO query_log(ts,query,mode,topk,n_results,latency_ms,top1_path) "
            "VALUES('_w9gt_ro_probe_','_w9gt_ro_probe_','x',1,1,1,'x')")
        conn.commit()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        ro = True
    if not ro:
        print("🔴 readonly 自证失败，立刻停", file=sys.stderr)
        conn.close()
        return 3
    n0 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]

    items = todo.get("items", [])
    rows = []
    for it in items:
        clue = it.get("top1_path")
        tr = trace_one(conn, clue)
        rows.append({
            "rank": it.get("rank"), "query": it.get("query"),
            "gt": it.get("gt", []),
            "gt_filled": bool(it.get("gt")),
            "top1_path_clue": clue,
            "clue_traceability": tr,
            "verdict": "🔍 仅为线索预检，gt 待 W11 人验；不算已确认",
            "human_verdict": it.get("human_verdict"),
            "needs_human_label": it.get("needs_human_label", True),
        })
    extra = [trace_one(conn, g) for g in args.gt]
    n1 = conn.execute("SELECT COUNT(*) FROM query_log").fetchone()[0]
    conn.close()

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "source": GT_TODO,
        "rule": todo.get("rule", ""),
        "main_set_unchanged": todo.get("main_set_unchanged", ""),
        "n": len(rows),
        "gt_filled_by_this_script": sum(1 for r in rows if r["gt_filled"]),
        "clue_traceable": sum(1 for r in rows if r["clue_traceability"]["traceable"]),
        "clue_not_traceable": sum(1 for r in rows if not r["clue_traceability"]["traceable"]),
        "human_verdict_owner": "W11（人验归库主；本窗口不代验、不代填、不改候选文件）",
        "readonly": {"enforced": ro, "query_log_before": n0, "query_log_after": n1},
        "extra_gt_checks": extra,
        "items": rows,
    }
    _dump(REPORT_OUT, report)
    print("=== W9-E gt 可追溯性钩子 ===")
    print("候选 %d 条｜gt 已被填 %d 条（应为 0）｜线索可追溯 %d / 不可追溯 %d" % (
        report["n"], report["gt_filled_by_this_script"], report["clue_traceable"],
        report["clue_not_traceable"]))
    print("🔴 top1_path 只是检索线索不是答案；人验归 W11，本脚本不代填 gt、不改主集")
    print("readonly 生效：%s｜query_log %d → %d" % (ro, n0, n1))
    print("产物：%s" % REPORT_OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
