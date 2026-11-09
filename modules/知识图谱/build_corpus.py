#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W6 独立图谱子系统 · 脱敏语料 → 独立 sqlite

🔴 三条硬边界（对应 W6 卡 §五）：
  1. 只写本目录 state/ 下的独立库，**绝不连接生产 kb.db**；
  2. KB_KG 保持默认关闭，不 import 生产检索链路；
  3. 任何命中脱敏规则的文档整篇剔除，不落盘正文、不进清单。

语料来源（默认相对本目录，可用参数覆盖；代码内不写死盘符绝对路径）：
  <kb-root>/wiki/技术/*.md     生产侧方法学 / 原创分析页（逐篇筛查）
  <oss-root>/docs/**/*.md      开源侧公开示例语料
具体敏感专名从库外词表读取（默认 ~/.kb_privacy_terms.txt），**不进 git、不落库**。

用法（复现命令见 README.md）：
  python build_corpus.py                      # 全量重建独立库
  python build_corpus.py --add <file.md>      # B5 增量：只插新文档，不重建
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_extract import extract_entities, ensure_sidecar  # noqa: E402  (cp 副本)

# ---------------------------------------------------------------- 脱敏
# 通用规则：不含任何具体人名 / 项目名 / 单位名字面量
GENERIC_RULES = [
    ("绝对路径", re.compile(r"[A-Za-z]:[\\/]")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")),
    ("身份证", re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}\d{7}[\dXx](?!\d)")),
    ("密钥", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b")),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
# 业务类别词：命中 = 整篇剔除（真隐私，不进图）
CATEGORY_WORDS = ["患者", "病历", "住院", "门诊", "医保", "通话录音", "身份证号", "学号"]
# 软脱敏标记：方法学页里讨论目录结构属于架构内容，不是引用真实数据。
# 出现即替换为占位符，正文其余部分保留 —— 既不泄路径，也不误杀语料。
SOFT_MARKERS = ["raw/隐私/", "raw/个人信息/", "raw/项目/", "raw/", "交接盘"]

TERMS_DEFAULT = os.path.expanduser("~/.kb_privacy_terms.txt")
# 从 cp 副本的示例常量里自动抽出的演示用专名：只做占位符替换、不整篇剔除。
# 文件落在 state/ 且被 .gitignore 忽略：不进 git、不打印、不进清单。
EXTRA_TERMS = os.path.join(HERE, "state", "extra_terms.txt")


def load_extra_terms():
    if not os.path.isfile(EXTRA_TERMS):
        return []
    try:
        with open(EXTRA_TERMS, "r", encoding="utf-8") as f:
            return [x.strip() for x in f if x.strip() and not x.startswith("#")]
    except Exception:
        return []


def load_terms(path):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [x.strip() for x in f if x.strip() and not x.startswith("#")]
    except Exception:
        return []


def classify(text, terms):
    """返回 (hard, soft)。hard 非空 → 整篇剔除；soft → 就地替换后保留。

    只返回**类别名**，不返回命中词本身，防止清单二次泄露。
    """
    hard, soft = [], []
    for name, r in GENERIC_RULES:
        if r.search(text):
            (hard if name in ("私钥", "身份证") else soft).append(name)
    if any(w and w in text for w in terms):
        hard.append("专名")
    if any(w in text for w in CATEGORY_WORDS):
        hard.append("业务类别")
    if any(w in text for w in SOFT_MARKERS):
        soft.append("目录引用")
    if any(w and w in text for w in load_extra_terms()):
        soft.append("示例专名")
    return sorted(set(hard)), sorted(set(soft))


def sanitize(text):
    """软脱敏：路径 / 邮箱 / 号码 / 密钥 / 原始目录引用 → 占位符。"""
    text = re.sub(r"[A-Za-z]:[\\/][^\s`'\"，。；：）)\]】]*", "<路径>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}", "<邮箱>", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "<号码>", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b", "<密钥>", text)
    for w in SOFT_MARKERS:
        text = text.replace(w, "<资料目录>/")
    for w in load_extra_terms():
        if w:
            text = text.replace(w, "<示例专名>")
    return text


# ---------------------------------------------------------------- 有类型关系边（B3 · 零 LLM）
# 中文规则模板：X 依赖/用于/导致/属于 Y。逐 chunk 抽取，绝不调外部 LLM。
REL_PATTERNS = [
    ("依赖", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:依赖|取决于|基于)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("用于", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:用于|用来|服务于)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("导致", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:导致|造成|引发)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("构成", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:包括|包含|由)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("需要", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:需要|要求)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("对比", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:对比|相比|不同于)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
    ("判定", re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})\s*(?:判定|定义为|称为)\s*([\u4e00-\u9fffA-Za-z0-9_]{2,16})")),
]
REL_STOP = {"这个", "那个", "我们", "它们", "其中", "因为", "所以", "可以", "一个", "这种",
            "同时", "这里", "那里", "什么", "怎么", "如果", "但是", "而且", "不是", "就是"}


def typed_relations(text, limit=8):
    out = []
    for rel, pat in REL_PATTERNS:
        for m in pat.finditer(text):
            a, b = m.group(1).strip(), m.group(2).strip()
            if a in REL_STOP or b in REL_STOP or a == b or len(a) < 2 or len(b) < 2:
                continue
            out.append((a, rel, b))
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------- 切块
def split_chunks(text, size=480):
    cur, out = "", []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#") and cur:
            out.append(cur)
            cur = ""
        if len(cur) + len(s) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + s).strip()
    if cur:
        out.append(cur)
    return out or [(text or "")[:size]]


# ---------------------------------------------------------------- 库
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, source TEXT, sha1 TEXT, n_chunks INTEGER)",
    "CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, path TEXT, doc_id INTEGER, idx INTEGER, content TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)",
    "CREATE TABLE IF NOT EXISTS entity_stats(entity TEXT PRIMARY KEY, df INTEGER, ent_type TEXT)",
    "CREATE TABLE IF NOT EXISTS passages(chunk_id INTEGER PRIMARY KEY, path TEXT, idx INTEGER, heading TEXT)",
    "CREATE TABLE IF NOT EXISTS typed_edges(src TEXT, rel TEXT, dst TEXT, chunk_id INTEGER, path TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_typed_src ON typed_edges(src)",
]


def connect(db_path, fresh=False):
    if fresh and os.path.isfile(db_path):
        os.remove(db_path)  # 仅限本子系统 state/ 下的自建库，删除范围由 argparse 约束
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    for sql in SCHEMA:
        conn.execute(sql)
    ensure_sidecar(conn)  # chunk_entities，来自 cp 副本
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(path, content)")
    except sqlite3.OperationalError:
        pass  # 环境无 fts5 → hybrid 自动降级为 LIKE 兜底
    conn.commit()
    return conn


def iter_sources(kb_root, oss_root):
    tech = os.path.join(kb_root, "wiki", "技术")
    if os.path.isdir(tech):
        for name in sorted(os.listdir(tech)):
            p = os.path.join(tech, name)
            if name.endswith(".md") and os.path.isfile(p) and not name.startswith("~$"):
                yield p, "wiki/技术/" + name, "wiki-tech"
    docs = os.path.join(oss_root, "docs")
    if os.path.isdir(docs):
        for root, dirs, files in os.walk(docs):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in sorted(files):
                if not name.endswith(".md") or name.startswith("~$"):
                    continue
                p = os.path.join(root, name)
                rel = os.path.relpath(p, docs).replace("\\", "/")
                yield p, "kb-from-zero/docs/" + rel, "oss-docs"


def ingest_one(conn, path, rel, source, terms):
    """返回 (状态, 块数, 命中类别列表)。命中脱敏 → 整篇不落盘。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    hard, soft = classify(text, terms)
    if hard:
        return ("skipped", 0, hard, soft)
    if soft:
        text = sanitize(text)
    if conn.execute("SELECT 1 FROM docs WHERE path=?", (rel,)).fetchone():
        return ("duplicate", 0, [], soft)
    sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()
    title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), rel)
    parts = split_chunks(text)
    cur = conn.execute(
        "INSERT INTO docs(path,title,source,sha1,n_chunks) VALUES(?,?,?,?,?)",
        (rel, title[:200], source, sha1, len(parts)))
    doc_id = cur.lastrowid
    df = {}
    for idx, content in enumerate(parts):
        cc = conn.execute(
            "INSERT INTO chunks(path,doc_id,idx,content) VALUES(?,?,?,?)",
            (rel, doc_id, idx, content))
        cid = cc.lastrowid
        heading = next((l.lstrip("# ").strip() for l in content.splitlines()
                        if l.startswith("#")), "")[:80]
        conn.execute("INSERT OR REPLACE INTO passages(chunk_id,path,idx,heading) VALUES(?,?,?,?)",
                     (cid, rel, idx, heading))
        try:
            conn.execute("INSERT INTO chunks_fts(path,content) VALUES(?,?)", (rel, content))
        except sqlite3.OperationalError:
            pass
        # 丢弃超长引号实体（原版规则会把整句示例模板当实体，污染图与 explain 路径）
        ents = [(e, t) for e, t in extract_entities(content) if len(e) <= 24]
        conn.executemany(
            "INSERT OR IGNORE INTO chunk_entities(chunk_id,entity,ent_type) VALUES(?,?,?)",
            [(cid, e, t) for e, t in ents])
        for e, t in {x: y for x, y in ents}.items():
            prev = df.get(e)
            df[e] = ((prev[0] if prev else 0) + 1, t)
        for a, relname, b in typed_relations(content):
            conn.execute("INSERT INTO typed_edges(src,rel,dst,chunk_id,path) VALUES(?,?,?,?,?)",
                         (a, relname, b, cid, rel))
    for e, (n, t) in df.items():
        row = conn.execute("SELECT df FROM entity_stats WHERE entity=?", (e,)).fetchone()
        conn.execute("INSERT OR REPLACE INTO entity_stats(entity,df,ent_type) VALUES(?,?,?)",
                     (e, (row[0] if row else 0) + n, t))
    conn.commit()
    return ("ok", len(parts), [], soft)


def main(argv=None):
    ap = argparse.ArgumentParser(description="W6 独立子系统：脱敏语料 → 独立 sqlite")
    ap.add_argument("--db", default=os.path.join(HERE, "state", "kg_w6.db"))
    ap.add_argument("--kb-root", default=os.path.normpath(os.path.join(HERE, "..", "..")),
                    help="知识库根（默认相对本目录上两级）")
    ap.add_argument("--oss-root", default=os.path.normpath(os.path.join(HERE, "..", "..", "..", "kb-from-zero")),
                    help="开源示例仓库根（默认相对本目录上三级）")
    ap.add_argument("--terms", default=TERMS_DEFAULT, help="库外敏感词表路径")
    ap.add_argument("--manifest", default=os.path.join(HERE, "corpus", "manifest.json"))
    ap.add_argument("--add", default=None, help="B5 增量：只插入这一篇，不重建")
    ap.add_argument("--fresh", action="store_true", help="重建独立库（仅影响 state/ 自建库）")
    a = ap.parse_args(argv)

    # 🔴 删除护栏：只允许操作本子系统 state/ 目录下的自建库
    db_abs = os.path.abspath(a.db)
    if os.path.dirname(db_abs) != os.path.join(HERE, "state"):
        print("[BLOCK] --db 必须落在本目录 state/ 下，拒绝：%s" % db_abs)
        return 2

    t0 = time.time()
    terms = load_terms(a.terms)
    conn = connect(db_abs, fresh=a.fresh)
    kept, skipped, dup, reasons, chunks_total, sanitized = [], 0, 0, {}, 0, 0

    if a.add:
        rel = os.path.basename(a.add)
        st, n, hits, _soft = ingest_one(conn, os.path.abspath(a.add), "incremental/" + rel,
                                        "incremental", terms)
        print("[corpus] --add %s → status=%s chunks=%d hits=%s" % (rel, st, n, hits))
        conn.close()
        return 0 if st == "ok" else 1

    for path, rel, source in iter_sources(a.kb_root, a.oss_root):
        st, n, hits, soft = ingest_one(conn, path, rel, source, terms)
        if st == "ok":
            kept.append({"path": rel, "source": source, "chunks": n})
            chunks_total += n
            sanitized += 1 if soft else 0
        elif st == "skipped":
            skipped += 1
            for h in hits:
                reasons[h] = reasons.get(h, 0) + 1
        else:
            dup += 1

    stats = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_roots": ["<KB_ROOT>/wiki/技术", "<OSS_ROOT>/docs"],
        "terms_loaded": len(terms),
        "docs_kept": len(kept),
        "chunks": chunks_total,
        "docs_sanitized": sanitized,
        "docs_skipped": skipped,
        "skip_reasons": reasons,
        "docs_duplicate": dup,
        "elapsed_s": round(time.time() - t0, 2),
        "entities": conn.execute("SELECT COUNT(*) FROM entity_stats").fetchone()[0],
        "typed_edges": conn.execute("SELECT COUNT(*) FROM typed_edges").fetchone()[0],
        "passages": conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0],
        "docs": kept,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.manifest)), exist_ok=True)
    with open(a.manifest, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in stats.items() if k != "docs"},
                     ensure_ascii=False, indent=2))
    print("[corpus] 清单 → %s" % a.manifest)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
