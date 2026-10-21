# -*- coding: utf-8 -*-
"""Markdown 内部链接死链检查（跳过 http/mailto 与代码块）。

用法：
    python tools/check_links.py
    python tools/check_links.py --root docs
退出码：0 无死链，1 有死链。
"""
import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "node_modules", "build", "dist"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=REPO)
    a = ap.parse_args()
    root = os.path.abspath(a.root)

    bad, total = [], 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".git")]
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dp, fn)
            with open(p, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            txt = re.sub(r"```.*?```", "", txt, flags=re.S)
            for m in re.finditer(r"\]\(([^)\s]+)\)", txt):
                t = m.group(1).split("#")[0]
                if not t or t.startswith(("http", "mailto:", "tel:")):
                    continue
                total += 1
                target = os.path.normpath(os.path.join(dp, t))
                if not os.path.exists(target):
                    bad.append((os.path.relpath(p, root), t))

    print("检查链接 %d 个 · 死链 %d" % (total, len(bad)))
    for p, t in bad:
        print("  X %s -> %s" % (p, t))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
