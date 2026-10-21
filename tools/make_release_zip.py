#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_release_zip.py —— 打源码发布包（Releases 用）

只收「源码 + 文档 + 模板 + 工具」，绝不收：
    · kb-hub.exe / kb-from-zero.zip 自身（Release 单独上传）
    · inventory.json（真实清单，含本机路径）
    · usage.json / *.db / __pycache__（运行期产物）
    · 任何图片 / 音视频 / PDF / Office 文件

用法
    python tools/make_release_zip.py                 # 输出到仓库根 kb-from-zero.zip
    python tools/make_release_zip.py --out D:/x.zip
    python tools/make_release_zip.py --list          # 只打印清单不打包
"""
import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

ALLOW_EXT = {".md", ".py", ".bat", ".json", ".html", ".css", ".js", ".txt",
             ".yml", ".yaml", ".example"}
ALLOW_NAME = {"LICENSE", "NOTICE", ".gitignore"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "build", "dist", ".idea", ".vscode"}
SKIP_FILE = {"kb-hub.exe", "kb-from-zero.zip", "inventory.json", "usage.json",
             "desktop.ini", "Thumbs.db", ".DS_Store"}
SKIP_EXT = {".db", ".db-wal", ".db-shm", ".log", ".pyc", ".spec"}


def collect():
    out = []
    for dp, dns, fns in os.walk(REPO):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(fns):
            if fn in SKIP_FILE:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT:
                continue
            if ext not in ALLOW_EXT and fn not in ALLOW_NAME:
                print("  [跳过] 不在白名单：%s" % os.path.join(dp, fn))
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            out.append((p, rel))
    return out


def main():
    ap = argparse.ArgumentParser(description="打源码发布包")
    ap.add_argument("--out", default=os.path.join(REPO, "kb-from-zero.zip"))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    files = collect()
    print("[收集] %d 个文件" % len(files))
    if args.list:
        for _, rel in files:
            print("  " + rel)
        return 0

    if os.path.exists(args.out):
        os.remove(args.out)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in files:
            z.write(p, rel)

    size = os.path.getsize(args.out)
    print("[完成] %s（%.1f KB / %d 文件）" % (args.out, size / 1024.0, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
