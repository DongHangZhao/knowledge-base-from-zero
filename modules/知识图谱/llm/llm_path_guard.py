#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · A：在线入口「零 LLM 引用」守卫。

判据（W12 卡 §A）：在线入口不得出现 `import llm_kit`，脚本 rc=0；
故意插一行 import 时必须判 BLOCK。

🔴 范围纪律（先判规则前提）：**只扫在线入口，不扫整个索引目录**。
离线工具 `hyde_cmd.py` / `w9_eval/eval_ragas_adapter.py` 允许复用 `llm_kit`
—— 那恰是「能力可复用」的证据，把它们算进来会让 C7 变成永久假红。
真要扩范围，用 `--extra <相对库根路径>` 显式登记。

退出码：0 PASS / 3 BLOCK / 2 参数或入口缺失。
`--selftest` 只在临时目录造样本，不碰任何真实文件。
纯标准库；不联网、不调 LLM、不 import llm_kit。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KB_ROOT = HERE.parent.parent.parent  # modules/知识图谱/llm → 库根

# 📌 新增在线入口时只改这张表（路径均来自真实读过的文件，不编路径）。
ONLINE_ENTRYPOINTS = [
    "modules/知识库索引/kb_rag.py",
    "modules/知识库索引/kb_index.py",
    "modules/知识库索引/mcp_server.py",
    "modules/知识库索引/query_rewrite.py",
]

# 只认真正的 import 语句；注释/文档里提到 llm_kit 不算引用（本文件注释就会提到）。
FORBIDDEN = re.compile(
    r"^[ \t]*(?:"
    r"import[ \t]+llm_kit\b"
    r"|from[ \t]+llm_kit[ \t]+import\b"
    r"|from[ \t]+llm[ \t]+import[ \t]+[^\n]*\bllm_kit\b"
    r"|import[ \t]+[^\n]*\bllm_kit\b"
    r")",
    re.M,
)


def scan_paths(paths) -> dict:
    """对任意路径列表扫描，返回 {scanned, violations, ok}。供 selftest 复用。"""
    scanned, violations = [], []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            violations.append({"path": str(p), "line": 0,
                               "text": "MISSING", "why": "入口文件不存在"})
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            violations.append({"path": str(p), "line": 0,
                               "text": str(exc), "why": "入口文件不可读"})
            continue
        scanned.append({"path": str(p), "bytes": p.stat().st_size})
        for m in FORBIDDEN.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            violations.append({"path": str(p), "line": line,
                               "text": m.group(0).strip()[:120],
                               "why": "在线入口出现 llm_kit 引用"})
    return {"scanned": scanned, "violations": violations,
            "ok": not violations}


def check(entrypoints=None) -> dict:
    rels = list(entrypoints if entrypoints is not None else ONLINE_ENTRYPOINTS)
    rep = scan_paths([KB_ROOT / r for r in rels])
    # 输出用相对路径，便于进版本库与留痕，不暴露本机盘符
    for item in rep["scanned"]:
        try:
            item["path"] = str(Path(item["path"]).relative_to(KB_ROOT)).replace("\\", "/")
        except ValueError:
            pass
    for v in rep["violations"]:
        try:
            v["path"] = str(Path(v["path"]).relative_to(KB_ROOT)).replace("\\", "/")
        except ValueError:
            pass
    rep["entrypoints"] = rels
    rep["note"] = ("只扫在线入口；离线批处理/评测工具复用 llm_kit 属正常，"
                   "不在扫描范围（否则 C7 永久假红）")
    return rep


def selftest() -> dict:
    """判据取证：干净样本 PASS；故意插 import / from-import 两种都必须 BLOCK。"""
    cases = []
    with tempfile.TemporaryDirectory(prefix="llm_guard_") as td:
        clean = Path(td) / "clean.py"
        clean.write_text("import json\n# import llm_kit 只是注释，不算引用\nx = 1\n",
                         encoding="utf-8")
        bad = Path(td) / "bad.py"
        bad.write_text("import json\nimport llm_kit\n", encoding="utf-8")
        bad2 = Path(td) / "bad2.py"
        bad2.write_text("from llm_kit import chat\n", encoding="utf-8")
        bad3 = Path(td) / "bad3.py"
        bad3.write_text("from llm import llm_kit\n", encoding="utf-8")

        r_clean = scan_paths([clean])
        r_bad = scan_paths([bad])
        r_bad2 = scan_paths([bad2])
        r_bad3 = scan_paths([bad3])
        cases += [
            {"case": "clean_pass", "ok": r_clean["ok"]},
            {"case": "import_blocked",
             "ok": (not r_bad["ok"] and len(r_bad["violations"]) == 1
                    and r_bad["violations"][0]["line"] == 2),
             "line": r_bad["violations"][0]["line"] if r_bad["violations"] else None},
            {"case": "from_import_blocked", "ok": not r_bad2["ok"]},
            {"case": "from_llm_import_blocked", "ok": not r_bad3["ok"]},
        ]
    real = check()
    cases.append({"case": "real_entrypoints_pass", "ok": real["ok"],
                  "violations": real["violations"]})
    return {"total": len(cases),
            "passed": sum(1 for c in cases if c["ok"]),
            "failed": [c for c in cases if not c["ok"]],
            "cases": cases,
            "real": {"scanned": real["scanned"], "note": real["note"]}}


def main() -> int:
    ap = argparse.ArgumentParser(description="在线入口零 LLM 引用守卫（W12 · A）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--selftest", action="store_true", help="临时目录造样本验证 BLOCK 能力")
    ap.add_argument("--extra", action="append", default=[],
                    help="追加在线入口（相对库根路径），可重复")
    a = ap.parse_args()

    if a.selftest:
        rep = selftest()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["passed"] == rep["total"] else 3

    rep = check(ONLINE_ENTRYPOINTS + a.extra)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("在线入口零引用守卫：%s（扫描 %d 个入口）" %
              ("PASS" if rep["ok"] else "BLOCK", len(rep["scanned"])))
        for s in rep["scanned"]:
            print("  OK   %s (%d B)" % (s["path"], s["bytes"]))
        for v in rep["violations"]:
            print("  BLOCK %s:%d  %s" % (v["path"], v["line"], v["text"]))
        print("范围：" + rep["note"])
    return 0 if rep["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
