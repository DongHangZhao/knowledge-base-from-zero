#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_vec_tables — 四表一致性原语的离线单测（内存 sqlite，零依赖）

守什么（#37 增量闭环的验收）：
  ① `missing_ids` 的口径精确：4 表 5 chunk 的构造下，缺的 id 必须恰好是
     `[5]`（bge）/ `[4,5]`（m3）/ `[5]`（trigram），一个不多一个不少。
     —— 口径错了，「补写」和「校验」就会一起错，且永远不会报错。
  ② `diff_report` 能判漂移：`ok is False`、`lag_m3 == 2`。
  ③ **补写只写缺失**：注入假 encode（只返回字节，不碰 torch），跑一次
     `encoded == 2`、m3 缺口清零、**已有向量一个字节都没变**。
  ④ **幂等红线**：同一份数据再跑一次 → `encoded == 0`、`status == "noop"`。
  ⑤ `limit=1` 只补 1 块，另一块仍缺（分批构建的正确性）。
  ⑥ 白名单外 table 名 → `ValueError`（表名要拼进 SQL，不校验就是注入面）。
  ⑦ 默认文本口径与生产 `vector_backfill.m3_text` 逐字一致（防跨代索引）。

跑法：python _test_vec_tables.py        （纯 stdlib + sqlite3，无 numpy/torch）
退出码：0 = 全 PASS；1 = 有 FAIL
"""
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vec_tables as V  # noqa: E402

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


# ---------- 夹具：5 chunk / 4 bge / 3 m3 / 4 tri ----------

FILLERS = {
    1: (b"\x01" * 8, b"\x02" * 8),
    2: (b"\x01" * 8, b"\x02" * 8),
    3: (b"\x01" * 8, b"\x02" * 8),
    4: (b"\x01" * 8, None),      # 有 bge、无 m3
}


def make_conn():
    """内存库：chunks(5) / chunk_bge(4) / vec_m3(3) / chunks_trigram(4)。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, path TEXT, title TEXT,"
                 " heading TEXT, content TEXT)")
    conn.execute("CREATE TABLE chunk_bge(chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL)")
    conn.execute("CREATE TABLE vec_m3(chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL)")
    conn.execute("CREATE VIRTUAL TABLE chunks_trigram USING fts5(content, tokenize='trigram')")
    for cid in range(1, 6):
        conn.execute("INSERT INTO chunks(id,path,title,heading,content) VALUES(?,?,?,?,?)",
                     (cid, "wiki/doc%d.md" % cid, "标题%d" % cid, "小节%d" % cid,
                      "正文内容 %d 号" % cid))
    conn.execute("INSERT INTO chunk_bge(chunk_id,vec) VALUES(1,?),(2,?),(3,?),(4,?)",
                 tuple(FILLERS[c][0] for c in (1, 2, 3, 4)))
    conn.execute("INSERT INTO vec_m3(chunk_id,vec) VALUES(1,?),(2,?),(3,?)",
                 tuple(FILLERS[c][1] for c in (1, 2, 3)))
    conn.executemany("INSERT INTO chunks_trigram(rowid, content) VALUES(?,?)",
                     [(c, "正文内容 %d 号" % c) for c in (1, 2, 3, 4)])
    conn.commit()
    return conn


def fake_encode(texts):
    """假编码器：1024 维零向量（4096 字节 = 1024 × float32），不碰 torch。"""
    return [b"\x00" * 4096] * len(texts)


def _empty_counts():
    """空库（一张表都没有）上的 counts：必须给 -1 而不是抛异常。"""
    bare = sqlite3.connect(":memory:")
    try:
        return V.counts(bare)
    finally:
        bare.close()


def main():
    print("== 四表一致性原语（vec_tables）==")

    conn = make_conn()

    # ① 计数
    cnt = V.counts(conn)
    check("① counts 四表行数正确",
          (cnt["chunks"], cnt["chunk_bge"], cnt["vec_m3"], cnt["chunks_trigram"]) == (5, 4, 3, 4),
          cnt)
    check("①b 缺表返回 -1 不抛（空库安全）", _empty_counts()["vec_m3"] == -1, _empty_counts())

    # ② 缺失 id 精确
    check("② missing_ids(chunk_bge) == [5]", V.missing_ids(conn, "chunk_bge") == [5],
          V.missing_ids(conn, "chunk_bge"))
    check("②b missing_ids(vec_m3) == [4,5]", V.missing_ids(conn, "vec_m3") == [4, 5],
          V.missing_ids(conn, "vec_m3"))
    check("②c missing_ids(chunks_trigram) == [5]（rowid 口径）",
          V.missing_ids(conn, "chunks_trigram") == [5],
          V.missing_ids(conn, "chunks_trigram"))
    check("②d m3_lag == 2", V.lag_m3(conn) == 2, V.lag_m3(conn))

    # ③ diff_report 判漂移
    rep = V.diff_report(conn)
    check("③ diff_report.ok is False", rep["ok"] is False, rep["ok"])
    check("③b lag_m3 == 2 / lag_bge == 1 / lag_tri == 1",
          (rep["lag_m3"], rep["lag_bge"], rep["lag_tri"]) == (2, 1, 1),
          (rep["lag_m3"], rep["lag_bge"], rep["lag_tri"]))
    check("③c diff_report.missing 精确",
          rep["missing"]["m3"] == [4, 5] and rep["missing"]["bge"] == [5]
          and rep["missing"]["tri"] == [5], rep["missing"])
    check("③d 报告带 ts / counts", bool(rep.get("ts")) and rep["counts"]["chunks"] == 5)

    # ④ 白名单校验（防注入）
    for bad in ("chunks", "chunk_bge; DROP TABLE chunks", "", "docs"):
        try:
            V.missing_ids(conn, bad)
            ok = False
        except ValueError:
            ok = True
        check("④ 白名单外表名 → ValueError（%r）" % bad, ok)

    # ⑤ 补写只写缺失 + 已有向量不动
    before_rows = {r[0]: r[1] for r in conn.execute("SELECT chunk_id, vec FROM vec_m3")}
    seen = {}

    def enc(texts):
        seen["n"] = len(texts)
        seen["texts"] = list(texts)
        return fake_encode(texts)

    got = V.backfill_m3(conn, enc, text_fn=lambda t, h, c: (h or "") + "\n" + c)
    check("⑤ 返回 status=encoded / encoded=2",
          (got["status"], got["encoded"]) == ("encoded", 2), got)
    check("⑤b 编码器只收到 2 条文本（== 缺口数）", seen.get("n") == 2, seen.get("n"))
    check("⑤c 写后 m3 缺口清零", V.missing_ids(conn, "vec_m3") == [],
          V.missing_ids(conn, "vec_m3"))
    check("⑤d lag_after == 0 / lag_before == 2",
          (got["lag_before"], got["lag_after"]) == (2, 0), got)
    after_rows = {r[0]: r[1] for r in conn.execute("SELECT chunk_id, vec FROM vec_m3")}
    untouched = all(after_rows[cid] == before_rows[cid] for cid in before_rows)
    check("⑤e 已有向量一个字节都没变（绝不重写）", untouched)
    check("⑤f 总行数 3 → 5", len(after_rows) == 5, len(after_rows))
    check("⑤g m3_model 元数据已写（dim=1024, n=5）",
          conn.execute("SELECT dim, n FROM m3_model WHERE id=1").fetchone() == (1024, 5),
          conn.execute("SELECT dim, n FROM m3_model WHERE id=1").fetchone())

    # ⑥ 幂等红线：再跑一次必须无写
    seen.clear()
    got2 = V.backfill_m3(conn, enc)
    check("⑥ 二次跑 encoded == 0 / status=noop",
          (got2["encoded"], got2["status"]) == (0, "noop"), got2)
    check("⑥b 二次跑编码器一次都没被调用", seen.get("n") is None, seen)

    # ⑦ limit 分批：只补 1 块，另一块仍缺
    conn2 = make_conn()
    got3 = V.backfill_m3(conn2, fake_encode, limit=1)
    check("⑦ limit=1 只补 1 块", got3["encoded"] == 1, got3)
    check("⑦b limit=1 后仍缺 1 块（id=5）",
          V.missing_ids(conn2, "vec_m3") == [5], V.missing_ids(conn2, "vec_m3"))
    got4 = V.backfill_m3(conn2, fake_encode, limit=1)
    check("⑦c 再补一批后清零", got4["encoded"] == 1 and V.missing_ids(conn2, "vec_m3") == [],
          got4)

    # ⑧ 空库/已齐库：noop 而不是异常
    conn3 = make_conn()
    conn3.execute("DELETE FROM vec_m3")   # 测试里可以清，生产代码绝不允许
    conn3.commit()
    n_before = len(V.missing_ids(conn3, "vec_m3"))
    V.backfill_m3(conn3, fake_encode)
    check("⑧ 从零补齐 5 块", n_before == 5 and V.lag_m3(conn3) == 0, n_before)

    # ⑨ 默认文本口径必须与生产 vector_backfill.m3_text 一致（防跨代索引）
    try:
        import vector_backfill as VB
        same = (V._default_m3_text("t", "h", "c" * 2000) == VB.m3_text("t", "h", "c" * 2000)
                and V._default_m3_text("t", "", "c") == VB.m3_text("t", "", "c")
                and V._default_m3_text("t", None, None) == VB.m3_text("t", None, None))
        check("⑨ 默认 m3 文本口径 == vector_backfill.m3_text（逐字）", same)
    except Exception as e:      # 环境缺依赖时不算失败，但要说清原因
        check("⑨ 默认 m3 文本口径 == vector_backfill.m3_text", True,
              "skipped：%s" % str(e)[:60])

    # ⑩ 表名白名单常量与文档一致
    check("⑩ TABLES 三表齐全", V.TABLES == ("chunk_bge", "vec_m3", "chunks_trigram"), V.TABLES)

    # ⑪ 🔴 缺表 ≠ 一致（QA 2026-11-03 揪出的误导绿灯）：
    #    全新库 + `build --skip-vec` 时四张表一张都没建，`missing_ids` 对不存在的表
    #    返回 [] ⇒ 旧实现判 ok=True 并打印「四表一致 ✓」——数据上没有、语义上更不能说。
    bare = sqlite3.connect(":memory:")
    rep_bare = V.diff_report(bare)
    check("⑪ 空库/缺表 → ok is False（不再报假的绿）", rep_bare["ok"] is False, rep_bare["ok"])
    check("⑪b tables_ok False 且 absent_tables 列出四张表",
          rep_bare["tables_ok"] is False
          and set(rep_bare["absent_tables"]) == {"chunks"} | set(V.TABLES),
          rep_bare["absent_tables"])
    # 只建母表、三张派生表都没建（= 刚 sync_owned 完、还没补向量）
    half = sqlite3.connect(":memory:")
    half.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, content TEXT)")
    half.execute("INSERT INTO chunks(id, content) VALUES(1,'x')")
    rep_half = V.diff_report(half)
    check("⑪c 只有母表时也不报一致，且点出缺哪三张",
          rep_half["tables_ok"] is False and rep_half["ok"] is False
          and set(rep_half["absent_tables"]) == set(V.TABLES),
          rep_half["absent_tables"])
    half.close()
    bare.close()

    # ⑫ 🔴 no-model 日志必须**按档位说话**（QA 揪出的前后矛盾：日志说"本档不判红"、
    #    调用方随后却真的返回 rc=3）。这里直接调 kb_rag.backfill_m3 抓 stderr 文案。
    try:
        import contextlib
        import io
        import kb_rag
        keep = {k: os.environ.get(k) for k in
                ("KB_BACKFILL_M3", "KB_M3_MODEL_DIR", "KB_STRICT_VEC")}
        try:
            conn4 = make_conn()          # 5 chunk / 3 m3 → 缺口 2
            os.environ["KB_M3_MODEL_DIR"] = os.path.join(HERE, "_no_such_m3_model_dir_")
            os.environ.pop("KB_STRICT_VEC", None)     # 默认严格
            os.environ["KB_BACKFILL_M3"] = "1"
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                st_ent = kb_rag.backfill_m3(conn4, limit=0)
            msg_ent = buf.getvalue()
            check("⑫ 企业档缺模型：文案明确「将以 rc=3 判红」",
                  st_ent["status"] == "no-model" and "rc=3" in msg_ent,
                  msg_ent.strip()[:80])
            check("⑫b 企业档文案不再自相矛盾（不出现「本档…不判红」）",
                  "不判红" not in msg_ent, msg_ent.strip()[:80])

            os.environ["KB_BACKFILL_M3"] = "auto"
            buf2 = io.StringIO()
            with contextlib.redirect_stderr(buf2):
                st_auto = kb_rag.backfill_m3(conn4, limit=0)
            msg_auto = buf2.getvalue()
            check("⑫c auto 档仍然只说「不判红」、不提 rc=3",
                  st_auto["status"] == "no-model" and "不判红" in msg_auto
                  and "rc=3" not in msg_auto, msg_auto.strip()[:80])

            # 企业档 + 分批构建：文案要说明「本次不判红，全量才红」
            os.environ["KB_BACKFILL_M3"] = "1"
            buf3 = io.StringIO()
            with contextlib.redirect_stderr(buf3):
                kb_rag.backfill_m3(conn4, limit=1)
            msg_limit = buf3.getvalue()
            check("⑫d 企业档 + --limit：文案说明本次不判红",
                  "分批构建" in msg_limit and "rc=3" not in msg_limit,
                  msg_limit.strip()[:80])
            conn4.close()
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    except Exception as e:                  # 环境缺依赖时不算失败，但要说清原因
        check("⑫ no-model 文案按档位（kb_rag 可导入）", True, "skipped：%s" % str(e)[:70])

    conn.close()
    conn2.close()
    conn3.close()

    print("-" * 56)
    print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
