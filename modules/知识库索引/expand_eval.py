#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
expand_eval — 把评测集从 130 条扩到 200 条（自动弱标注）
=========================================================

人工标注的 130 条很宝贵，但两个问题：
  ① 数量不够，Recall@5 撞到 94% 天花板后就没区分度了
  ② 全靠人想问法，覆盖不到冷门文档

所以补一批**自动弱标注**：query 取自文档里真实存在的**章节标题**（去掉编号前缀），
GT = 这个章节所属的文档。理由：章节标题是作者自己写的小标题，天然是「这段讲什么」，
拿来当问法比编的更贴近真实检索需求。

🔴 诚实声明：这是**弱标注**，不是人工标注。
   · query 来自文档自身措辞，与真实用户的口语化问法有差距（真实用户不会照着标题问）
   · 因此**指标必须和人工集分开报**，不能混在一起算总分
   · 它的价值是：① 拉开数量、② 覆盖冷门文档、③ 提高区分度（标题级别比文档级别更难）

用法：
  python expand_eval.py --n 70            # 生成 70 条，写入 state/kb_eval_auto.json
  python expand_eval.py --n 70 --apply    # 生成后立刻跑一次评测对比
"""

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "state")
OUT = os.path.join(STATE, "kb_eval_auto.json")

# 只吃「1.2 / 三、」这类真编号（后面跟空格或顿号），
# 不能用 [\d\.\s]+ 否则会把「2026-10-14 改进详记」吃成「-10-14 改进详记」
NUM_PREFIX = re.compile(r"^\d+(\.\d+)*[\s、]+")
JUNK = ("概述", "基本信息", "目录", "前言", "小结", "总结", "附录", "参考", "备注")


def clean_heading(h):
    """取末级标题，去掉编号前缀。"""
    if not h:
        return ""
    last = h.split(" > ")[-1]
    last = last.lstrip("# ").strip()
    last = NUM_PREFIX.sub("", last).strip()
    return last


def pick(conn, limit=70, min_len=6):
    """从 chunks 里挑有意义的小标题当 query。"""
    rows = conn.execute(
        "SELECT c.heading, d.path, d.title FROM chunks c JOIN docs d ON d.id=c.doc_id"
    ).fetchall()
    seen, out, per_doc = set(), [], {}
    for heading, path, title in rows:
        # 每篇最多取 2 条：不加限制的话，小标题多的文档会把 70 条全占满，
        # 评测集就只覆盖十来篇，反而测不出冷门文档的检索能力
        if per_doc.get(path, 0) >= 2:
            continue
        # 流水文件（log.md / log_*.md）条目不是有效评测题：它们是操作记录，
        # 标题形如「[2026-08-10] ingest | xxx」，拿它当问法既不真实也把 GT 全压在同一篇上
        base = os.path.basename(path)
        if base == "log.md" or base.startswith("log_"):
            continue
        q = clean_heading(heading)
        if len(q) < min_len or len(q) > 40:
            continue
        if any(j in q for j in JUNK):
            continue
        # 排除时间戳/编号型残留（"[2026-08-10] ingest | xxx" 这类）
        if q.startswith("[") or q.startswith("20") or "|" in q or "]" in q:
            continue
        # 「（最多 60 条）」这类是表格说明，不是可检索的问法
        if re.search(r"（最多\s*\d+\s*条）", q) or q.startswith("本次") or q.startswith("最近"):
            continue
        # 模板标题（Overview 之类）不是有效问法
        if not re.search(r"[\u4e00-\u9fff]", q):
            continue
        if q == title or q in seen:
            continue
        seen.add(q)
        per_doc[path] = per_doc.get(path, 0) + 1
        out.append({"query": q, "gt": path, "title": title,
                    "source": "自动弱标注（取自章节标题）"})
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description="扩充评测集（自动弱标注）")
    ap.add_argument("--n", type=int, default=70)
    ap.add_argument("--min-len", type=int, default=6)
    args = ap.parse_args()

    from kb_index import connect
    conn = connect(readonly=True)
    items = pick(conn, args.n, args.min_len)
    # 与人工集去重（避免同一文档同一问法重复计入）
    human = []
    for fn in ("kb_eval_30.json", "kb_eval_100.json"):
        p = os.path.join(STATE, fn)
        if os.path.exists(p):
            human.extend(json.load(open(p, encoding="utf-8")))
    hq = {h["query"] for h in human}
    items = [i for i in items if i["query"] not in hq]
    conn.close()

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print("生成 %d 条自动弱标注 → %s" % (len(items), OUT))
    print("人工标注 %d 条 + 自动弱标注 %d 条 = %d 条" % (len(human), len(items), len(human) + len(items)))
    print("\n样例：")
    for i in items[:5]:
        print("  Q: %s\n     GT: %s" % (i["query"], i["gt"]))


if __name__ == "__main__":
    main()
