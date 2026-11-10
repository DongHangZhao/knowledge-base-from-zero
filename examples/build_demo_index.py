#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从仓库内【已公开】的 markdown 建一个脱敏 demo 索引库。

🔴 边界：不读生产库、不读 raw/、不写任何生产文件；产物 ``examples/state/demo.db``
   被根 ``.gitignore`` 的 ``*.db`` 忽略，永不入库。
幂等：``upsert_doc`` 按 path 覆盖；**不删库、不删文件**（本脚本减法动作为 0）。
语料口径：只收白名单里仓库自身的公开文档（本来就在 GitHub 上），条数如实打印，不注水。

用法（venv 解释器，UTF-8，查 returncode）：
    python examples/build_demo_index.py
"""
import hashlib
import os
import sqlite3
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_REPO, "modules", "知识库索引"))

import kb_index  # noqa: E402

DB = os.environ.get("KB_DEMO_DB") or os.path.join(_HERE, "state", "demo.db")

# 白名单：公开文档目录 + 根级说明页。decisions/ 含内部决策细节，明确排除。
ROOTS = [
    "README.md", "RELEASE.md", "COMMERCIAL.md", "CONTRIBUTING.md", "FAQ.md",
    "docs", "templates",
    "modules/知识库索引/README.md", "modules/知识图谱/README.md",
]
SKIP_DIRS = {"decisions", "state", "experiments", "eval_sets", "__pycache__", "build"}


def collect():
    files = []
    for rel in ROOTS:
        p = os.path.join(_REPO, rel)
        if os.path.isfile(p):
            files.append(rel)
        elif os.path.isdir(p):
            for base, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for n in sorted(names):
                    if n.endswith(".md"):
                        files.append(os.path.relpath(os.path.join(base, n), _REPO).replace("\\", "/"))
    return sorted(set(files))


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    kb_index.ensure_schema(conn)
    t0, ok = time.time(), 0
    for rel in collect():
        try:
            with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
                body = f.read()
        except (OSError, UnicodeDecodeError) as e:  # 单条失败不中断
            print("SKIP %s: %s" % (rel, e))
            continue
        if not body.strip():
            continue
        title = next((l.lstrip("# ").strip() for l in body.splitlines()
                      if l.startswith("# ")), rel)
        summary = " ".join(body.split())[:120]
        doc = {"path": rel, "category": "oss-demo", "title": title,
               "summary": summary, "content": body,
               "hash": hashlib.sha1(body.encode("utf-8")).hexdigest()}
        try:
            kb_index.upsert_doc(conn, doc)
            ok += 1
        except Exception as e:  # 单条失败不中断
            print("FAIL %s: %s" % (rel, e))
    conn.commit()
    docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    print("db=%s" % DB)
    print("docs=%d chunks=%d ingested=%d elapsed=%.2fs" %
          (docs, chunks, ok, time.time() - t0))
    return 0 if docs else 1


if __name__ == "__main__":
    sys.exit(main())
