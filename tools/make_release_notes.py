#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_release_notes — 从 CHANGELOG.md 抽出某个版本的段落，作为 GitHub Release 正文

为什么需要（#42b）：发版最容易漏的一步不是打 tag，而是**Release 正文空着**。
tag 一推、页面一刷，用户看到的是「No release notes」—— 而 CHANGELOG 里其实写得
清清楚楚。所以把它做成机械动作，并且**找不到版本段就失败**（宁可不出 Release，
也不出一个空版）。

口径：
  · 版本段 = `## [v<version>]` 到下一个行首 `^## ` 之间的全部内容（不含标题行）
  · `version` 的 `v` 前缀可选：`1.14.2` 与 `v1.14.2` 等价
  · 不看 `## [Unreleased]`（未发版内容不该出现在任何 Release 正文里）

用法：
  python tools/make_release_notes.py --version 1.14.2            # 打到 stdout
  python tools/make_release_notes.py --version 1.14.2 --check    # 只校验存在性
  python tools/make_release_notes.py --version 1.14.2 --out notes.md

退出码：0 = 成功；1 = 找不到该版本段 / 段落为空；2 = CHANGELOG 不存在
"""

import argparse
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_CHANGELOG = os.path.join(REPO_ROOT, "modules", "知识库索引", "CHANGELOG.md")
DEFAULT_VERSION_FILE = os.path.join(REPO_ROOT, "modules", "知识库索引", "VERSION")


def _norm_version(version):
    """`v1.14.2` / `1.14.2` → `1.14.2`（后接空白或行尾，避免 1.14 命中 1.14.2）。"""
    v = (version or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    return v


def extract(changelog_text, version):
    """抽出版本段正文。

    Args:
        changelog_text: CHANGELOG 全文。
        version: 版本号（`v` 前缀可选）。

    Returns:
        str: 版本段正文（已去掉标题行与尾随空行）；找不到返回 ""。
    """
    v = _norm_version(version)
    if not v:
        return ""
    # 标题形如：## [v1.14.2] - 2026-11-02   （也容忍 ## [1.14.2] 与无日期）
    head_re = re.compile(r"^##\s*\[v?%s\]" % re.escape(v))
    lines = changelog_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if head_re.match(line.strip()):
            start = i
            break
    if start is None:
        return ""
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    text = "\n".join(body).strip("\n").strip()
    return text


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 CHANGELOG 抽版本段，供 Release 正文使用")
    ap.add_argument("--version", default=None,
                    help="版本号（v 前缀可选）；默认读 modules/知识库索引/VERSION")
    ap.add_argument("--changelog", default=DEFAULT_CHANGELOG, help="CHANGELOG 路径")
    ap.add_argument("--check", action="store_true",
                    help="只校验该版本段存在且非空（不存在 → rc=1，防发空版）")
    ap.add_argument("--out", default=None, help="写入文件（默认打印到 stdout）")
    args = ap.parse_args(argv)

    version = args.version
    if not version:
        raw = _read(DEFAULT_VERSION_FILE)
        if raw is None:
            print("[ERR] 找不到 VERSION 文件：%s" % DEFAULT_VERSION_FILE, file=sys.stderr)
            return 2
        version = raw.strip()
    version = _norm_version(version)

    text = _read(args.changelog)
    if text is None:
        print("[ERR] 找不到 CHANGELOG：%s" % args.changelog, file=sys.stderr)
        return 2

    body = extract(text, version)
    if not body:
        print("[FAIL] CHANGELOG 里找不到 v%s 的段落（或段落为空）—— 拒绝发一个空版" % version,
              file=sys.stderr)
        return 1

    if args.check:
        first = body.splitlines()[0] if body.splitlines() else ""
        print("[PASS] v%s 段落存在：%d 行，首行「%s」" % (version, len(body.splitlines()), first[:60]))
        return 0

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        print("[ok] 已写入 %s（%d 字节）" % (args.out, len(body) + 1), file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
