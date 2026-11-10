#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W12 · F：生产 `modules/知识图谱/` → 开源仓镜像（可复跑脚本，替代手工 28 文件复制）。

W12 卡 §F 的剩余工作：把「2026-11-09 已手工跑通」固化成一条命令。

    python sync_to_kb_from_zero.py --dest "<开源仓>/modules/知识图谱" --check
    python sync_to_kb_from_zero.py --dest "<开源仓>/modules/知识图谱"

纪律：
  · 🔴 目标路径只走命令行参数，**不写死本机绝对路径**（避免本机路径进公开仓库）。
  · 排除 `state/` `__pycache__/` `.git/` `*.pyc` `*.bak*` `*.tmp` `*.log`
    （日志与 state 含本机痕迹，卡里已记：全量隐私扫描的 BLOCK 全在 state/）。
  · 幂等：按 sha1 比对，只复制变化文件；重复跑第二次应全是 unchanged。
  · 🔴 **绝不删除目标端多余文件**（那是 L2/L3 减法，需单独过目拿许可）；
    目标端独有文件只列进 `target_only` 报告，交人审。
  · 报告落生产侧 `state/sync_report.json`，本身不进镜像、不进 git。

退出码：0 成功；2 参数/目标不存在；3 复制过程中有失败（逐条记，不静默吞）。
纯标准库；不联网、不调 LLM、不 push（push 由发版流程另做）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
REPORT = STATE / "sync_report.json"
EXCLUDE_DIRS = {"state", "__pycache__", ".git", ".pytest_cache", ".venv"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".tmp", ".log")


def excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    name = rel.name
    if name.endswith(EXCLUDE_SUFFIX) or ".bak" in name:
        return True
    return False


def sha1(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="知识图谱模块镜像到开源仓（W12 · F）")
    ap.add_argument("--dest", required=True, help="开源仓 modules/知识图谱 目录（必填）")
    ap.add_argument("--check", action="store_true", help="只比对不写")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    dest = Path(a.dest)
    if not dest.is_dir():
        print("目标目录不存在（不自动跨区创建）：%s" % dest, file=sys.stderr)
        return 2

    src_files = sorted(p for p in HERE.rglob("*")
                       if p.is_file() and not excluded(p.relative_to(HERE)))
    added, updated, unchanged, failures = [], [], [], []
    for p in src_files:
        rel = p.relative_to(HERE)
        target = dest / rel
        try:
            if not target.is_file():
                if not a.check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
                added.append(str(rel).replace("\\", "/"))
            elif sha1(p) != sha1(target):
                if not a.check:
                    tmp = target.with_name(target.name + ".sync-tmp")
                    shutil.copy2(p, tmp)
                    tmp.replace(target)
                updated.append(str(rel).replace("\\", "/"))
            else:
                unchanged.append(str(rel).replace("\\", "/"))
        except OSError as exc:  # 单条失败不中断，但必须显式记下来
            failures.append({"path": str(rel).replace("\\", "/"), "error": str(exc)[:200]})

    dest_files = {str(p.relative_to(dest)).replace("\\", "/")
                  for p in dest.rglob("*")
                  if p.is_file() and not excluded(p.relative_to(dest))}
    src_names = {str(p.relative_to(HERE)).replace("\\", "/") for p in src_files}
    target_only = sorted(dest_files - src_names)

    rep = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(HERE).replace("\\", "/"),
        "dest": str(dest).replace("\\", "/"),
        "mode": "check" if a.check else "sync",
        "excluded": {"dirs": sorted(EXCLUDE_DIRS),
                     "suffix": list(EXCLUDE_SUFFIX), "name_contains": ".bak"},
        "source_files": len(src_files),
        "added": added, "updated": updated, "unchanged": unchanged,
        "counts": {"added": len(added), "updated": len(updated),
                   "unchanged": len(unchanged), "failures": len(failures)},
        "failures": failures,
        "target_only": target_only,
        "note": ("🔴 本脚本只新增/覆盖，绝不删除目标端文件；target_only 只列清单交人审。"
                 "重复跑应 added=updated=0（幂等）。"),
    }
    if not a.check:
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = REPORT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(REPORT)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("镜像 %s：源 %d 文件 · 新增 %d · 更新 %d · 未变 %d · 失败 %d"
              % ("[CHECK] " if a.check else "", rep["source_files"],
                 rep["counts"]["added"], rep["counts"]["updated"],
                 rep["counts"]["unchanged"], rep["counts"]["failures"]))
        if target_only:
            print("目标端独有 %d 个（只列不删）：%s"
                  % (len(target_only), ", ".join(target_only[:8])))
        if failures:
            for f in failures:
                print("  FAIL %s：%s" % (f["path"], f["error"]))
        print("报告：%s" % (REPORT.name if not a.check else "<--check 未落盘>"))
    return 3 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
