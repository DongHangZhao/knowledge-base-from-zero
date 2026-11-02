#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_kg — #41 图谱实验脚手架的单测（内存 sqlite，零依赖、不碰 GPU）

守什么（这个实验能"花 1pt 买到结论"的前提）：
  ① 实体抽取真的抽得到三类（《标题》 / `代码` / #tag），且不整段乱抽；
  ② 1-hop 邻居只走**一跳**：与查询实体直接共享 chunk 的才算，2 跳的**不许**混进来；
  ③ **不含自身**：调用方传入 `exclude_docs`（已命中文档）后，那些文档必须消失
     —— 否则这一路只是给已有结果加分，实验等于没做；
  ④ 决策门 4 个边界值判定正确（0.03/150 含等号）：
        (0.03, 150) → PASS ｜ (0.029, 150) → FAIL
        (0.05, 151) → FAIL ｜ (0.05, 150) → PASS
  ⑤ **`KB_KG` 未设时 `build_sidecar` 返回 disabled 且连 `chunk_entities` 都不建**
     （"零建表"是这个实验能整块删掉的前提）。

跑法：python _test_kg.py      （纯 stdlib + sqlite3）
退出码：0 = 全 PASS；1 = 有 FAIL
"""
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 🔴 先确保 KB_KG 未设，再 import（kg 模块的行为与它有关）
os.environ.pop("KB_KG", None)

import kg_extract as X  # noqa: E402
import kg_gate as G     # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    """打一条 [PASS]/[FAIL] —— run_tests.py:70 的正则依赖这个格式，不许改。"""
    global PASS, FAIL
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  ｜ " + str(detail)) if detail != "" else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def make_conn():
    """5 chunk / 6 实体：
    c1(甲) · c2(甲,乙) · c3(乙,丙) · c4(丁) · c5(戊)
    查询「甲」→ 1-hop = {c1, c2}；c3 是 2 跳（要经 乙 才到），必须不出现。
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, title TEXT)")
    conn.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, content TEXT)")
    for i in (1, 2, 3, 4, 5):
        conn.execute("INSERT INTO docs(id,path,title) VALUES(?,?,?)",
                     (i, "wiki/文档%d.md" % i, "文档%d" % i))
        # c5 的正文故意带一个 #标签：用来验「删掉侧表行后 build_sidecar 能补回实体」
        body = ("第5篇正文 #戊标签 与实体相关的描述。" if i == 5
                else "第%d篇正文，与实体无关的通用描述。" % i)
        conn.execute("INSERT INTO chunks(id,doc_id,content) VALUES(?,?,?)", (i, i, body))
    X.ensure_sidecar(conn)
    ents = {1: [("甲", "title")],
            2: [("甲", "title"), ("乙", "tag")],
            3: [("乙", "tag"), ("丙", "code")],
            4: [("丁", "quote")],
            5: [("戊", "capword")]}
    for cid, lst in ents.items():
        conn.executemany("INSERT OR IGNORE INTO chunk_entities(chunk_id,entity,ent_type) "
                         "VALUES(?,?,?)", [(cid, e, t) for e, t in lst])
    conn.commit()
    return conn


def table_exists(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def main():
    print("== 图谱实验脚手架（#41）==")

    # ---------- ① 实体抽取 ----------
    ents = X.extract_entities("见《架构总览》与 `build()` 的 #tag")
    types = {t for _e, t in ents}
    names = {e for e, _t in ents}
    check("① 抽到《标题》/`代码`/#tag 三类", {"title", "code", "tag"} <= types, sorted(types))
    check("①b 标题实体文本正确", "架构总览" in names, sorted(names))
    check("①c 代码实体含 build", any("build" in n for n in names), sorted(names))
    check("①d tag 实体不含 # 号", "tag" in names and "#tag" not in names, sorted(names))
    check("①e 单字符不误抽（《a》长度不足）",
          all(e != "a" for e, _t in X.extract_entities("《a》")))
    check("①f 空文本不抛且返回空", X.extract_entities("") == [])
    check("①g 纯数字/纯符号的代码实体被过滤（防假 1-hop 边）",
          all(e not in ("400", "1/(k+rank)") for e, _t in
              X.extract_entities("参数 `400` 与公式 `1/(k+rank)` 与 `RRF`")),
          [e for e, _t in X.extract_entities("参数 `400` 与公式 `1/(k+rank)` 与 `RRF`")])

    # ---------- ② 1-hop 只走一跳 ----------
    conn = make_conn()
    os.environ["KB_KG"] = "1"          # expand_1hop 的总闸
    try:
        got = G.expand_1hop(conn, "甲", topk=10)
        ids = sorted(r["chunk_id"] for r in got)
        check("② 1-hop 命中 c1/c2（共享实体「甲」）", ids == [1, 2], ids)
        check("②b 2 跳的 c3 不许混进来", 3 not in ids, ids)
        check("②c 返回项含 kb_rag.add 需要的字段",
              all(k in got[0] for k in ("doc_id", "path", "title", "chunk_id")), list(got[0]))

        # ---------- ③ 不含自身（排除已命中文档） ----------
        got2 = G.expand_1hop(conn, "甲", topk=10, exclude_docs={1})
        ids2 = sorted(r["chunk_id"] for r in got2)
        check("③ exclude_docs 排掉自身后只剩邻居 c2", ids2 == [2], ids2)
        check("③b 与查询无关的 c4/c5 从不出现",
              not ({4, 5} & set(ids2)), ids2)
        check("③c 无实体命中 → 空列表（不抛）", G.expand_1hop(conn, "完全不存在的词", 10) == [])
        check("③d 空查询 → 空列表", G.expand_1hop(conn, "", 10) == [])

        # 侧表不存在时也不抛
        bare = sqlite3.connect(":memory:")
        bare.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, content TEXT)")
        bare.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, title TEXT)")
        check("③e 侧表不存在 → 空列表（不抛）", G.expand_1hop(bare, "甲", 10) == [])
        bare.close()
    finally:
        os.environ.pop("KB_KG", None)

    # KB_KG 未设时 expand_1hop 直接空（零加载语义）
    check("③f KB_KG 未设 → expand_1hop 返回空", G.expand_1hop(conn, "甲", 10) == [])

    # ---------- ④ 决策门 4 个边界 ----------
    cases = [
        ({"r5": 0.85, "p95": 300}, {"r5": 0.88, "p95": 450}, True, "0.03 / +150"),
        ({"r5": 0.85, "p95": 300}, {"r5": 0.879, "p95": 450}, False, "0.029 / +150"),
        ({"r5": 0.85, "p95": 300}, {"r5": 0.90, "p95": 451}, False, "+0.05 / +151"),
        ({"r5": 0.85, "p95": 300}, {"r5": 0.90, "p95": 450}, True, "+0.05 / +150"),
    ]
    for b, t, want, label in cases:
        r = G.decide(b, t)
        check("④ 决策门 %s → pass=%s" % (label, want),
              r["pass"] is want, "r5_delta=%.3f p95_delta=%s"
              % (r["r5_delta"], r["p95_delta_ms"]))
    check("④b 阈值是写死常量（3% / 150ms）",
          (G.MIN_R5_DELTA, G.MAX_P95_DELTA_MS) == (0.03, 150.0),
          (G.MIN_R5_DELTA, G.MAX_P95_DELTA_MS))
    src = open(os.path.join(HERE, "kg_gate.py"), encoding="utf-8").read()
    check("④c 阈值没有被环境变量接管（模块里没有 KB_MIN_R5 / KB_MAX_P95 的读取）",
          "KB_MIN_R5" not in src and "KB_MAX_P95" not in src and "KB_KG_R5" not in src)
    r_missing = G.decide({"r5": 0.85, "p95": 300}, {"r5": 0.9})
    check("④d 缺指标 → 不通过且说明原因",
          r_missing["pass"] is False and any("缺 p95" in x for x in r_missing["reasons"]),
          r_missing["reasons"])
    check("④e 百分数口径也认（85 → 0.85）",
          G.decide({"r5": 85, "p95": 300}, {"r5": 88, "p95": 450})["pass"] is True)

    # ---------- ⑤ KB_KG 未设：disabled 且不建表 ----------
    c2 = sqlite3.connect(":memory:")
    c2.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, content TEXT)")
    c2.execute("INSERT INTO chunks(id,doc_id,content) VALUES(1,1,'《架构总览》与 #tag')")
    st = X.build_sidecar(c2)
    check("⑤ KB_KG 未设 → build_sidecar 返回 disabled",
          st["status"] == "disabled", st)
    check("⑤b KB_KG 未设 → 连 chunk_entities 表都不建",
          not table_exists(c2, "chunk_entities"))
    c2.close()

    # ---------- ⑥ KB_KG=1：只处理未建的（幂等） ----------
    c3 = make_conn()          # 已含 6 条实体
    os.environ["KB_KG"] = "1"
    try:
        n_before = c3.execute("SELECT COUNT(*) FROM chunk_entities").fetchone()[0]
        st = X.build_sidecar(c3)      # 5 chunk 都已建 → 待处理 0
        check("⑥ 侧表已建时不再重复处理（chunks=0）", st["chunks"] == 0, st)
        n_after = c3.execute("SELECT COUNT(*) FROM chunk_entities").fetchone()[0]
        check("⑥b 实体行数不变（只 INSERT OR IGNORE）", n_before == n_after,
              (n_before, n_after))
        # 清一篇再建一次：只补那一篇
        c3.execute("DELETE FROM chunk_entities WHERE chunk_id=5")
        c3.commit()
        st2 = X.build_sidecar(c3, dry_run=True)
        check("⑥c dry-run 只报数不写库", st2["dry_run"] is True and st2["chunks"] == 1, st2)
        st3 = X.build_sidecar(c3)
        check("⑥d 增量补齐缺的那篇", st3["chunks"] == 1 and st3["inserted"] >= 1, st3)
    finally:
        os.environ.pop("KB_KG", None)
    c3.close()
    conn.close()

    print("-" * 56)
    print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
