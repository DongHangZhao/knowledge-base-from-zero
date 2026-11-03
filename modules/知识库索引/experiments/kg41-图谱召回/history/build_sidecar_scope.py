#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sidecar_scope — 只为「评测集涉及的文档」建实体侧表（#41 的 1pt 规模）

## 为什么不跑全库（两次实测，都超时）

    全库 111441 chunk 建侧表，两次尝试都失败：
      · 带词性标注 pseg：28 分钟未完成（被杀）
      · 纯分词 jieba.cut：27 分钟未完成（被杀），尽管分段测速只要 2.7 分钟
    分段测速（1000 chunk）与端到端实测差 10 倍以上 —— 说明瓶颈不在分词算法本身，
    而在**单进程累积 11 万条结果 + 35 万条写入**这一段（内存 600MB+ 持续增长）。

    这一点很关键：#41 是 **1pt 实验**，目的是回答「1-hop 邻居召回有没有用」，
    不是「把图谱建成基础设施」。花 30 分钟建一个可能马上要删的侧表，是错的花法。
    评测集涉及的文档只有两百来篇，在它们上面建侧表，几秒出结果。

## 这个侧表的边界（必须说清，否则结论会被误用）

    侧表**只覆盖评测集 gt 涉及的文档**，所以 1-hop 只能在这些文档之间扩展：
      ✅ 对「多跳题」有效 —— gt 本来就是 [第一跳文档, 第二跳文档]，1-hop 该在它们之间连线
      ❌ 不能当「通用图谱」用 —— 真实查询的实体大多不在这批文档里，扩不出去

    所以本实验的结论只能回答：**「当实体抽取覆盖到 gt 文档时，1-hop 机制有没有带来
    R@5 提升」**，不能回答「全库建图的收益」。后者需要先把全量侧表跑通（那是下一个 8pt 的活）。

跑法：
    python build_sidecar_scope.py --db state/_kg_sandbox.db --eval-dir <state 目录>
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_sidecar_v2 import extract_rules, extract_jieba, STOP_CN, STOP_EN  # noqa: E402

TABLE = "chunk_entities"
INDEX = "idx_ent"


def collect_paths(eval_dir):
    """把各评测集里出现过的所有 gt 路径收集起来（单条 str 或 list 都吃）。"""
    paths, sets_used = set(), []
    for f in sorted(glob.glob(os.path.join(eval_dir, "kb_eval*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, list):
            continue
        n0 = len(paths)
        for it in d:
            if not isinstance(it, dict):
                continue
            gts = it.get("gt")
            if isinstance(gts, list):
                paths.update(g for g in gts if g)
            elif gts:
                paths.add(gts)
        sets_used.append((os.path.basename(f), len(d), len(paths) - n0))
    return paths, sets_used


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="沙盘库（**不要指向生产库**）")
    ap.add_argument("--eval-dir", required=True, help="评测集所在目录（通常是 state/）")
    ap.add_argument("--min-df", type=int, default=2, help="实体至少出现在几篇文档里")
    ap.add_argument("--max-df-ratio", type=float, default=0.5,
                    help="df 上限按「本次纳入的文档数 × 比例」算（范围小，比例要放宽）")
    args = ap.parse_args(argv)

    db = os.path.abspath(args.db)
    if os.path.basename(db) == "kb.db":
        print("[ERR] 拒绝在生产库上建实验侧表：%s" % db, file=sys.stderr)
        return 2

    paths, sets_used = collect_paths(args.eval_dir)
    print("[scope] 评测集:", ", ".join("%s(%d条,+%d路径)" % x for x in sets_used))
    print("[scope] 去重 gt 路径 %d 条" % len(paths))

    conn = sqlite3.connect(db)
    ph = ",".join("?" * len(paths))
    rows = conn.execute(
        "SELECT c.id, c.content, c.doc_id FROM chunks c JOIN docs d ON d.id=c.doc_id "
        "WHERE d.path IN (%s)" % ph, tuple(paths)).fetchall()
    real_docs = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM docs WHERE path IN (%s)" % ph, tuple(paths)).fetchone()[0]
    print("[scope] 命中文档 %d 篇 / chunk %d 个（%d 条 gt 路径不在库里）"
          % (real_docs, len(rows), len(paths) - real_docs))
    if not rows:
        print("[ERR] 一个 chunk 都没命中，检查 --eval-dir 或 DB_PATH")
        return 3

    t0 = time.time()
    import jieba
    jieba.setLogLevel(20)

    pairs = []
    for i, (cid, content, _did) in enumerate(rows):
        text = content or ""
        ents = extract_rules(text) | extract_jieba(text, jieba)
        pairs.append((cid, list(ents)))
        if (i + 1) % 1000 == 0:
            print("[scope] %d/%d  %.1fs" % (i + 1, len(rows), time.time() - t0))
    print("[scope] 抽取完成 %.1fs，实体实例 %d" % (time.time() - t0, sum(len(p[1]) for p in pairs)))

    # df：实体 → 出现过几篇文档
    cid2doc = {r[0]: r[1] for r in conn.execute(
        "SELECT c.id, c.doc_id FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE d.path IN (%s)" % ph,
        tuple(paths))}
    df = {}
    for cid, ents in pairs:
        did = cid2doc.get(cid)
        for e, _t in ents:
            df.setdefault(e, set()).add(did)
    max_df = max(2, int(real_docs * args.max_df_ratio))
    keep = {e for e, s in df.items() if args.min_df <= len(s) <= max_df}
    print("[scope] 实体 %d → 保留 %d（剔除高频 %d / 低频 %d）"
          % (len(df), len(keep),
             sum(1 for s in df.values() if len(s) > max_df),
             sum(1 for s in df.values() if len(s) < args.min_df)))

    conn.execute("DROP TABLE IF EXISTS %s" % TABLE)
    conn.execute("""CREATE TABLE %s(
        chunk_id INTEGER NOT NULL,
        entity   TEXT    NOT NULL,
        ent_type TEXT    NOT NULL,
        PRIMARY KEY(chunk_id, entity)) WITHOUT ROWID""" % TABLE)
    conn.execute("CREATE INDEX IF NOT EXISTS %s ON %s(entity)" % (INDEX, TABLE))
    payload = [(cid, e, t) for cid, ents in pairs for e, t in ents if e in keep]
    conn.executemany("INSERT OR IGNORE INTO %s(chunk_id,entity,ent_type) VALUES(?,?,?)" % TABLE,
                     payload)
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM %s" % TABLE).fetchone()[0]
    allents = conn.execute("SELECT COUNT(DISTINCT entity) FROM %s" % TABLE).fetchone()[0]
    cn = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT entity FROM %s WHERE entity GLOB '*[一-龥]*')" % TABLE).fetchone()[0]
    covered = conn.execute("SELECT COUNT(DISTINCT chunk_id) FROM %s" % TABLE).fetchone()[0]
    print("[scope] 侧表 %d 条 ｜ 实体 %d（含中文 %d，%.1f%%）｜ 覆盖 chunk %d/%d"
          % (n, allents, cn, 100.0 * cn / max(1, allents), covered, len(rows)))
    print("[scope] 总耗时 %.1fs" % (time.time() - t0))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
