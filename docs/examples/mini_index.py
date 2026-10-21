#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mini_index.py —— 双链路最小可运行实现（约 160 行，纯标准库）

它把「链路 A 的 Markdown 文件」映射成「链路 B 的 SQLite 索引」，
并演示三件在生产里最关键的事：

  1. 增量更新：按 content_hash 判断，只重写变化的文档；
  2. 两级检索：整篇（docs_fts）⊕ 分块（chunks_fts），RRF 融合；
  3. 结果必带路径：所有命中统一落回 doc.path，AI 才能引用到原文。

用法
    python mini_index.py build  --kb-root /path/to/kb --db kb.db
    python mini_index.py search "混合检索" --db kb.db --topk 5
    python mini_index.py stats  --db kb.db

生产版（jieba 分词、TF-IDF、LSA/BGE 向量、评测集、周自检）见仓库文档
docs/双链路体系.md 的「从最小实现到生产」一节。
"""
import argparse
import hashlib
import os
import re
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".obsidian", "outputs"}
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

DDL = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT,
  mtime REAL,
  size INTEGER,
  content_hash TEXT,
  body TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
  path UNINDEXED, title, body, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  doc_path TEXT NOT NULL,
  heading TEXT,
  ord INTEGER,
  body TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  doc_path UNINDEXED, heading, body, tokenize='unicode61'
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_path);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


# ------------------------------------------------------------------ 扫描
def iter_md(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if fn.lower().endswith(".md"):
                yield os.path.join(dp, fn)


def sha1(s):
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


def doc_title(path, body):
    m = HEAD_RE.search(body)
    if m:
        return m.group(2).strip()
    return os.path.splitext(os.path.basename(path))[0]


def split_chunks(body):
    """按 Markdown 标题层级切块，保留「父标题 > 子标题」路径。"""
    lines = body.splitlines()
    chunks, stack, buf, ordn = [], [], [], 0

    def flush():
        nonlocal buf, ordn
        text = "\n".join(buf).strip()
        if text:
            chunks.append({"heading": " > ".join(t for _, t in stack) or "(无标题)",
                           "ord": ordn, "body": text})
            ordn += 1
        buf = []

    for ln in lines:
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(ln)
    flush()
    return chunks


# ------------------------------------------------------------------ 建库
def build(kb_root, db_path, full=False):
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    if full:
        for t in ("docs_fts", "chunks_fts", "docs", "chunks"):
            conn.execute("DELETE FROM %s" % t)
        conn.commit()

    old = {p: h for p, h in conn.execute("SELECT path, content_hash FROM docs")}
    seen, added, updated, skipped = set(), 0, 0, 0

    for fp in iter_md(kb_root):
        rel = os.path.relpath(fp, kb_root).replace("\\", "/")
        seen.add(rel)
        try:
            body = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print("  [skip] %s (%s)" % (rel, e))
            continue
        h = sha1(body)
        if old.get(rel) == h:
            skipped += 1
            continue

        st = os.stat(fp)
        cur = conn.execute(
            "INSERT INTO docs(path,title,mtime,size,content_hash,body) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET title=excluded.title, mtime=excluded.mtime, "
            "size=excluded.size, content_hash=excluded.content_hash, body=excluded.body",
            (rel, doc_title(rel, body), st.st_mtime, st.st_size, h, body),
        )
        # 同步 FTS 与分块：先删旧，再插新（FTS5 无 UPSERT）
        conn.execute("DELETE FROM docs_fts WHERE path = ?", (rel,))
        conn.execute("INSERT INTO docs_fts(path,title,body) VALUES(?,?,?)",
                     (rel, doc_title(rel, body), body))
        conn.execute("DELETE FROM chunks WHERE doc_path = ?", (rel,))
        conn.execute("DELETE FROM chunks_fts WHERE doc_path = ?", (rel,))
        for c in split_chunks(body):
            conn.execute("INSERT INTO chunks(doc_path,heading,ord,body) VALUES(?,?,?,?)",
                         (rel, c["heading"], c["ord"], c["body"]))
            conn.execute("INSERT INTO chunks_fts(doc_path,heading,body) VALUES(?,?,?)",
                         (rel, c["heading"], c["body"]))
        if rel in old:
            updated += 1
        else:
            added += 1

    # 删除已不存在的文件
    gone = [p for p in old if p not in seen]
    for p in gone:
        conn.execute("DELETE FROM docs WHERE path=?", (p,))
        conn.execute("DELETE FROM docs_fts WHERE path=?", (p,))
        conn.execute("DELETE FROM chunks WHERE doc_path=?", (p,))
        conn.execute("DELETE FROM chunks_fts WHERE doc_path=?", (p,))

    conn.execute("INSERT INTO meta(k,v) VALUES('built_at',datetime('now','localtime')) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v")
    conn.commit()
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
    conn.commit()
    conn.close()
    print("[build] new=%d upd=%d same=%d del=%d -> %s"
          % (added, updated, skipped, len(gone), db_path))


# ------------------------------------------------------------------ 检索
def fts_query(q):
    """把用户输入转成安全的 FTS5 查询：每个词加引号，词间 AND。"""
    words = [w for w in re.split(r"[^\w\u4e00-\u9fff]+", q) if w]
    if not words:
        return None
    return " AND ".join('"%s"' % w.replace('"', '""') for w in words)


def search(db_path, q, topk=5, mode="fused"):
    conn = sqlite3.connect(db_path)
    fq = fts_query(q)
    if not fq:
        print("empty query")
        return []

    if mode in ("fused", "doc"):
        rows = conn.execute(
            "SELECT path, snippet(docs_fts,2,'[',']','…',12) AS sn, bm25(docs_fts) AS s "
            "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY s LIMIT 50", (fq,)).fetchall()
        doc_hits = [(r[0], r[1], -r[2]) for r in rows]
    else:
        doc_hits = []

    if mode in ("fused", "chunk"):
        rows = conn.execute(
            "SELECT doc_path, heading, snippet(chunks_fts,2,'[',']','…',12) AS sn, "
            "bm25(chunks_fts) AS s FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY s LIMIT 50", (fq,)).fetchall()
        chunk_hits = [(r[0], "[%s] %s" % (r[1], r[2]), -r[3]) for r in rows]
    else:
        chunk_hits = []

    # RRF 融合：只吃排名，不吃原始分（两路分值量纲不同）
    K = 60
    score, best = {}, {}
    for lst, w in ((doc_hits, 1.0), (chunk_hits, 1.0)):
        for rank, (path, sn, _s) in enumerate(lst, 1):
            score[path] = score.get(path, 0.0) + w / (K + rank)
            if path not in best or (mode == "fused" and w == 1.0 and "[" in sn):
                best.setdefault(path, sn)
    ranked = sorted(score.items(), key=lambda x: -x[1])[:topk]
    conn.close()
    return [(p, best.get(p, ""), s) for p, s in ranked]


def stats(db_path):
    conn = sqlite3.connect(db_path)
    q = lambda sql: conn.execute(sql).fetchone()[0]
    print("docs=%d chunks=%d fts_rows=%d"
          % (q("SELECT COUNT(*) FROM docs"), q("SELECT COUNT(*) FROM chunks"),
             q("SELECT COUNT(*) FROM docs_fts")))
    print("db_size=%.1f KB" % (os.path.getsize(db_path) / 1024.0))
    print("built_at=%s" % (q("SELECT v FROM meta WHERE k='built_at'") or "—"))
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="双链路最小索引（SQLite FTS5）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--kb-root", default=".")
    b.add_argument("--db", default="mini_kb.db")
    b.add_argument("--full", action="store_true", help="全量重建")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--db", default="mini_kb.db")
    s.add_argument("--topk", type=int, default=5)
    s.add_argument("--mode", default="fused", choices=["fused", "doc", "chunk"])
    st = sub.add_parser("stats")
    st.add_argument("--db", default="mini_kb.db")
    args = ap.parse_args()

    if args.cmd == "build":
        build(args.kb_root, args.db, args.full)
    elif args.cmd == "search":
        hits = search(args.db, args.query, args.topk, args.mode)
        if not hits:
            print("(no hits)")
        for i, (p, sn, sc) in enumerate(hits, 1):
            print("%d. %s   [%.4f]" % (i, p, sc))
            if sn:
                print("   %s" % sn)
    else:
        stats(args.db)


if __name__ == "__main__":
    main()
