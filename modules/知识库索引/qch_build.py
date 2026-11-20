#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文档侧问句库构建器（doc2query）· 开源版。

思路一句话：给每篇文档补一份「用户可能会怎么问」的索引，检索时多一路召回。
用户问「缓存过期了怎么办」，文档里写的是「TTL 失效策略」——字面对不上，
但文档侧生成的问句里会有类似说法，于是能把它召回上来。

为什么落独立库而不写 kb.db：
  问句是**派生数据**，写进主库就得重跑 build、抢 build.lock、与并发写入打架。
  独立 sqlite（默认 `state/kb_questions.db`）→ 随时可删可重建，零侵入。

两种生成方式：
  --method llm    文档正文交给大模型，生成 3 条用户口吻问句（效果好，需自配端点）
  --method rule   抽标题 + 首段关键句拼问句（零成本、零外发，效果明显差，只作跑通兜底）

实测参考（私有库，1.1 万篇文档 / 98 条评测题）：llm 法让「原本进不了前 100 的 gt」
救回 19 条（+19.39pp，p=3.8e-6，零误伤）；rule 法（抽标题这种）反而伤排序。
所以默认 llm；没有端点时才用 rule 跑通流程。

用法：
  python qch_build.py --limit 200                 # 先小样本试
  python qch_build.py --all --workers 4           # 全量
  python qch_build.py --all --method rule         # 无端点兜底
  python qch_channel.py "你的问题"                 # 验证检索通道

隐私：默认跳过 `raw/隐私/`、`raw/个人信息/`、`wiki/个人信息/`；LLM 法经 `llm_kit`
      出口门禁，被拦即跳过（**不脱敏重试**——换名不等于脱敏）。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "知识图谱", "llm"))

PRIVATE_PREFIX = ("raw/隐私/", "raw/个人信息/", "wiki/个人信息/", "raw/隐私", "wiki/个人信息")
# 媒体 OCR / 转写派生页：内容碎、问句会退化成「图里写了啥」，实测无区分度，默认跳过
MEDIA_RE = re.compile(r"(\.(jpg|jpeg|png|gif|webp|mp4|mp3|wav|m4a|pdf)\.md$)|(^wiki/媒体/)", re.I)

SYS = "你是知识库检索增强助手。只输出 JSON。"
TMPL = """下面是知识库中的一篇文档。请用**普通用户的口吻**提 3 个这篇文档能回答的问题。

要求：
1. 口语化，像真人随口问（可以用「怎么」「为啥」「多久」「靠什么」这类词）
2. 不要照抄标题，不要用「本文」「该文档」指代
3. 每条不超过 25 个字，覆盖不同角度（是什么 / 为什么 / 怎么做 / 参数或数量）
4. 只输出 JSON：{{"questions":["...","...","..."]}}

标题：{title}
正文片段：
{body}
"""

DDL = """
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY, doc_path TEXT, doc_title TEXT, question TEXT, src TEXT, ts TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_q ON questions(doc_path, question);
CREATE INDEX IF NOT EXISTS ix_qp ON questions(doc_path);
CREATE VIRTUAL TABLE IF NOT EXISTS qfts USING fts5(seg, question UNINDEXED, doc_path UNINDEXED);
CREATE TABLE IF NOT EXISTS qvec(qid INTEGER PRIMARY KEY, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS qmeta(k TEXT PRIMARY KEY, v TEXT);
"""


def seg(text):
    try:
        import jieba
        return " ".join(t for t in jieba.lcut(text or "") if t.strip())
    except Exception:
        return " ".join(list(text or ""))


def is_private(path):
    p = (path or "").replace("\\", "/")
    return any(p.startswith(x) for x in PRIVATE_PREFIX)


def gen_rule(title, body):
    """零成本兜底：标题 + 首段里的关键句，拼成「…是什么 / 怎么…」形式的问句。"""
    out = []
    t = (title or "").strip()
    if t:
        out.append("%s是什么" % t[:20])
        out.append("%s怎么用" % t[:20])
    m = re.search(r"[^\n。！？]{6,40}[。！？]", body or "")
    if m:
        s = m.group(0).strip("。！？")
        out.append("%s是怎么说的" % s[:18])
    return [x for x in out if 4 <= len(x) <= 30][:3]


def gen_llm(title, body, task="qch_doc_questions"):
    import llm_kit
    prompt = TMPL.format(title=(title or "")[:120], body=(body or "")[:900])
    try:
        r = llm_kit.chat(prompt, system=SYS, task=task, max_tokens=220, temperature=0.0)
    except llm_kit.ExportBlocked:
        return None                      # 门禁拦下：跳过，不脱敏重试
    except Exception:
        return None
    try:
        v = llm_kit.extract_json(r.get("text") or "")
        qs = v.get("questions") if isinstance(v, dict) else None
        return [q.strip() for q in (qs or []) if isinstance(q, str) and 4 <= len(q.strip()) <= 40][:3]
    except Exception:
        return None


def load_docs(db, limit=0, skip_media=True):
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    sql = "SELECT path, coalesce(title,''), substr(coalesce(content,''),1,900) FROM docs ORDER BY path"
    out = []
    for path, title, body in conn.execute(sql):
        p = (path or "").replace("\\", "/")
        if is_private(p):
            continue
        if skip_media and MEDIA_RE.search(p):
            continue
        if not (body or "").strip():
            continue
        out.append((p, title, body))
        if limit and len(out) >= limit:
            break
    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "state", "kb.db"))
    ap.add_argument("--out", default=os.path.join(HERE, "state", "kb_questions.db"))
    ap.add_argument("--method", choices=["llm", "rule"], default="llm")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-media", action="store_true")
    a = ap.parse_args()

    if not a.all and not a.limit:
        ap.error("要么 --limit N 试跑，要么 --all 全量")

    docs = load_docs(a.db, 0 if a.all else a.limit, skip_media=not a.keep_media)
    print("待处理文档 %d 篇（method=%s）" % (len(docs), a.method))

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    task = "qch_doc_questions"

    def work(item):
        path, title, body = item
        qs = gen_rule(title, body) if a.method == "rule" else gen_llm(title, body, task)
        return path, title, qs or []

    rows, t0, ok, blocked = [], time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, (path, title, qs) in enumerate(ex.map(work, docs), 1):
            if qs:
                ok += 1
                rows += [(path, title, q, a.method, ts) for q in qs]
            else:
                blocked += 1
            if i % 100 == 0:
                print("  %d/%d  %.0fs  成功 %d 跳过 %d" % (i, len(docs), time.time() - t0, ok, blocked),
                      flush=True)

    print("生成 %d 条问句 / %d 篇（跳过 %d 篇：门禁或无输出）" % (len(rows), ok, blocked))

    # ---- 建库 ----
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.executescript(DDL)
    conn.executemany(
        "INSERT OR IGNORE INTO questions(doc_path,doc_title,question,src,ts) VALUES(?,?,?,?,?)", rows)
    conn.commit()
    for qid, q, p in conn.execute("SELECT id, question, doc_path FROM questions").fetchall():
        conn.execute("INSERT INTO qfts(rowid,seg,question,doc_path) VALUES(?,?,?,?)",
                     (qid, seg(q), q, p))
    conn.commit()

    # 向量：bge-small-zh（与 kb_rag 同一塔，查询侧 is_query=True）
    try:
        import bge_encoder
        allrows = conn.execute("SELECT id, question FROM questions").fetchall()
        for i in range(0, len(allrows), 32):
            batch = allrows[i:i + 32]
            vecs = bge_encoder.encode([q for _, q in batch], batch=16, is_query=False)
            conn.executemany("INSERT OR REPLACE INTO qvec(qid,dim,vec) VALUES(?,?,?)",
                             [(qid, len(v), v.astype("float32").tobytes()) for (qid, _), v in zip(batch, vecs)])
            conn.commit()
        print("向量 %d 条" % len(allrows))
    except Exception as e:
        print("⚠️ 向量不可用（FTS 仍可检索）：%s: %s" % (type(e).__name__, str(e)[:60]))

    conn.execute("INSERT OR REPLACE INTO qmeta(k,v) VALUES(?,?)",
                 ("built", json.dumps({"method": a.method, "docs": ok, "questions": len(rows),
                                       "ts": ts}, ensure_ascii=False)))
    conn.commit()
    conn.close()
    os.replace(tmp, a.out)          # 原子替换：中途被杀也不会留半截库
    print("✅ 已写入 %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
