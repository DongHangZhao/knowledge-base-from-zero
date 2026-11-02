#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vec_tables — 向量表的「缺失 id 原语」与四表一致性校验（零依赖）

为什么单独抽一个模块（2026-11-03，#37 增量闭环）：
    本库有四张与 chunks 一一对应的表：``chunk_bge``（bge-small-zh 512d）、
    ``vec_m3``（bge-m3 1024d）、``chunks_trigram``（FTS5 字级索引）。它们由**不同
    函数**维护（backfill_bge / build_vec_m3 / ensure_trigram），谁掉了没人知道：

    build 只补 bge，`vec_m3` **建表但不填** —— 于是四表静默漂移，检索不报错、
    doctor 覆盖率还是 100%（因为它只查 bge）。这是**最难发现的一类退化**：
    不告警、不丢数据，只是「有些内容怎么都搜不出来」。

    所以这里的第一原则是：**补写与校验必须共用同一个 SQL 口径**。
    `missing_ids()` 是唯一的那把尺子 —— 补写取它的前 N 个 id，校验比它的长度。
    任何「补一套、验一套」的写法都会在下一次改动里悄悄分叉。

设计约束（硬）：
    · **零依赖**：模块顶层不 import numpy / torch / kb_index / bge_encoder。
      `ensure()` 里才懒 import bge_encoder（它内部对 torch 也是懒加载），
      所以本模块在任何装了 Python 的机器上都能 import、能单测（含 CI）。
    · **只读优先**：`missing_ids` / `counts` / `diff_report` / `m3_lag` 全是只读；
      只有 `backfill_m3` / `write_m3_model` 写库。
    · **只写缺失**：`backfill_m3` 的待写 id 集合 ≡ `missing_ids()` 的前 N 个，
      绝不 DELETE、绝不重写已有向量；二次跑必然 `encoded == 0`（幂等红线）。
    · **表名白名单**：`missing_ids(conn, table)` 只认 `TABLES` 里的三个名字，
      其余一律 `ValueError` —— 表名是要拼进 SQL 的，不校验就是注入面。

作者：FallenPetal ｜ 许可：见仓库 LICENSE（KBL-1.0，非商用）
"""

import datetime
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

#: 与 chunks 一一对应的三张派生表（chunks 本身是母表，不在此列）
TABLES = ("chunk_bge", "vec_m3", "chunks_trigram")

#: 表名 → 该表里指向 chunks.id 的列（trigram 是 FTS5 虚表，用 rowid 对齐）
_ID_COLUMN = {
    "chunk_bge": "chunk_id",
    "vec_m3": "chunk_id",
    "chunks_trigram": "rowid",
}

#: SQLite 单条语句的变量上限默认 999，批量查询留安全余量
_SQL_VARS = 900

#: 报表里候选 id 最多带多少个（lag_* 计数永远是全量，不受此限）
MAX_IDS_IN_REPORT = 200


# ============ 建表 ============

def ensure(conn):
    """确保四张衍生表的 DDL 就位（幂等，重复调用无副作用）。

    🔴 只建「向量侧」的四张表（chunk_bge / bge_model / vec_m3 / m3_model），
       trigram 虚表由 `kb_rag.ensure_trigram()` 负责（那里还带着与 chunks 的同步
       逻辑，不该在这里重复实现）。为什么走 bge_encoder：建表语句只能有**一份**，
       否则老库缺列（如 bge_model.tie_breaker）的补列逻辑会漏。
    """
    import bge_encoder  # 懒 import：模块顶层保持零依赖（CI 只装 jieba+numpy 也能 import 本文件）
    bge_encoder.ensure_tables(conn)
    try:
        conn.commit()
    except sqlite3.Error:
        pass


# ============ 只读：计数 / 缺失 id / 一致性 ============

def _table_exists(conn, table):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (table,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row)


def counts(conn):
    """四表 + 母表的行数。表不存在 → -1（不抛，doctor 要能在空库上跑）。"""
    out = {}
    for name in ("chunks",) + TABLES:
        if not _table_exists(conn, name):
            out[name] = -1
            continue
        try:
            out[name] = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
        except sqlite3.Error:
            out[name] = -1
    return out


def missing_ids(conn, table, limit=0):
    """返回 `chunks` 里在该表中**没有对应行**的 id，升序。

    Args:
        conn: sqlite3 连接。
        table: 只允许 `TABLES` 里的三个名字（白名单，防 SQL 注入）。
        limit: >0 时只取前 limit 个（分批补写用，构建大库时避免一次拉几十万 int）。

    Returns:
        list[int]，升序。

    Raises:
        ValueError: table 不在白名单内。
    """
    if table not in TABLES:
        raise ValueError("未知向量表 %r（只允许 %s）" % (table, " / ".join(TABLES)))
    if not _table_exists(conn, "chunks") or not _table_exists(conn, table):
        return []
    col = _ID_COLUMN[table]
    sql = ("SELECT id FROM chunks WHERE id NOT IN "
           "(SELECT %s FROM %s) ORDER BY id" % (col, table))
    if limit and limit > 0:
        sql += " LIMIT %d" % int(limit)
    try:
        return [r[0] for r in conn.execute(sql)]
    except sqlite3.Error:
        # 表在但结构异常（老库/只读快照）：按「无法判定」处理，不制造假缺口。
        return []


def m3_lag(conn):
    """缺 m3 向量的 chunk 数（幂等红线：这个数只能降，涨了必然是 bug）。

    契约名是 `m3_lag`（架构接口表 / index_state 的 key 都叫 m3_lag）；
    `lag_m3` 保留为只读别名，方便写 `lag_*` 系列可读代码。
    """
    return len(missing_ids(conn, "vec_m3"))


#: 只读别名（同一函数对象，不是第二套实现）
lag_m3 = m3_lag


def diff_report(conn, max_ids=MAX_IDS_IN_REPORT):
    """四表一致性报告（**只读**；build / doctor / 周自检共用这一份口径）。

    Returns:
        {
          "ts": ISO 时间,
          "ok": bool,                    # 四表都在 **且** 三张派生表都无缺口
          "tables_ok": bool,             # 四张表是否都存在
          "absent_tables": [str],        # 不存在的表名（ok=False 的原因之一）
          "counts": {chunks, chunk_bge, vec_m3, chunks_trigram},
          "missing": {"bge": [ids], "m3": [ids], "tri": [ids]},   # 各自最多 max_ids 个
          "lag_m3": int, "lag_bge": int, "lag_tri": int,          # 全量计数
        }

    🔴 表不存在 ≠ 一致（2026-11-03 修，QA 揪出的误导日志）：
       `missing_ids` 对不存在的表返回 `[]`（空库上不能抛），于是**全新库 + `--skip-vec`**
       时四张表一张都没建、lag 全是 0，旧代码会判 `ok=True` 并打印「四表一致 ✓」——
       一个数据上没有、语义上更不能说的"绿灯"。现在把「表缺失」单独记成
       `tables_ok=False` / `absent_tables`，调用方据此说「四表未齐」而不是「一致」。
    """
    cnt = counts(conn)
    absent = [t for t in ("chunks",) + TABLES if cnt.get(t, -1) < 0]
    mb = missing_ids(conn, "chunk_bge")
    mm = missing_ids(conn, "vec_m3")
    mt = missing_ids(conn, "chunks_trigram")
    keep = max(0, int(max_ids or 0))
    return {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "ok": (not absent) and (not mb) and (not mm) and (not mt),
        "tables_ok": not absent,
        "absent_tables": absent,
        "counts": cnt,
        "missing": {
            "bge": mb[:keep] if keep else [],
            "m3": mm[:keep] if keep else [],
            "tri": mt[:keep] if keep else [],
        },
        "lag_bge": len(mb),
        "lag_m3": len(mm),
        "lag_tri": len(mt),
    }


# ============ 写：m3 补写 ============

def _default_m3_text(title, heading, content):
    """v1 口径兜底实现（heading + content，前 1000 字）。

    🔴 **生产调用方必须显式传 `text_fn=vector_backfill.m3_text`**，不要依赖这个兜底：
       它只是为了让本模块能独立单测而存在。两处一旦有任何一个字符不同，同一段文字
       就会落进两个语义空间 —— 检索不报错，只是「有些内容突然搜不出来」。
       （口径唯一入口见 `vector_backfill.m3_text` 的注释。）
    """
    heading = (heading or "").strip()
    parts = [heading] if heading else []
    parts.append(content or "")
    return ("\n".join(parts))[:1000]


def _to_blob(v):
    """把一个向量元素转成 float32 字节串（兼容 ndarray / bytes / 序列）。"""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    try:
        return v.astype("float32").tobytes()
    except AttributeError:
        import array
        return array.array("f", [float(x) for x in v]).tobytes()


def _blob_dim(blob):
    return len(blob) // 4 if blob else 0


def _fetch_texts(conn, ids, text_fn):
    """按 id 升序取 (id, text)；分批 IN 查询避开 SQLite 变量数上限。"""
    out = []
    for i in range(0, len(ids), _SQL_VARS):
        part = ids[i:i + _SQL_VARS]
        rows = conn.execute(
            "SELECT id, title, heading, content FROM chunks WHERE id IN (%s) ORDER BY id"
            % ",".join("?" * len(part)), part).fetchall()
        for cid, title, heading, content in rows:
            out.append((cid, text_fn(title, heading, content)))
    return out


def write_m3_model(conn, dim, n, model=None):
    """写 m3_model 元数据（口径与 `vector_backfill.py` 逐字一致）。"""
    conn.execute(
        "INSERT OR REPLACE INTO m3_model(id,dim,n,model,built_at) VALUES(1,?,?,?,?)",
        (int(dim), int(n),
         model or "BAAI/bge-m3 (dense, CLS+L2)",
         datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def backfill_m3(conn, encode, limit=0, batch=32, text_fn=None, log=None, model=None):
    """给缺 `vec_m3` 的 chunk 补 m3 向量（**只写缺失 id**，幂等）。

    Args:
        conn: sqlite3 连接（调用方负责锁与事务边界）。
        encode: `encode(list[str]) -> 序列[向量]`；注入式，便于离线单测。
            生产注入的是 `vector_backfill.encode_m3_gpu` 的偏函数（fp16 cuda，CLS+L2）。
        limit: >0 时本次只补前 limit 个缺口（分批构建、首次灌 10 万块时分次跑）。
        batch: 每批编码条数。
        text_fn: `(title, heading, content) -> str`；生产必须传
            `vector_backfill.m3_text`（唯一编码口径）。
        log: 日志回调，默认静默。
        model: 写进 `m3_model.model` 的模型描述。

    Returns:
        {
          "status": "encoded" | "noop" | "skipped",
          "encoded": int,            # 本次真正写入的行数
          "lag_before": int,         # 补写前缺口数（全量）
          "lag_after": int,          # 补写后缺口数（全量；成功后应为 0 或缩小 limit 值）
          "dim": int,
        }
        （"no-model" / "no-cuda" 由上层 `kb_rag.backfill_m3` 判定后直接返回，
          因为「模型在不在、有没有 CUDA」不是表级原语该关心的事。）

    幂等保证：
        · 待写 id 集合 ≡ `missing_ids(conn, "vec_m3")`（有 limit 时取前 limit 个）；
        · 绝不 DELETE / 绝不重写已有向量（缺失 id 在 vec_m3 里本来就没有行）；
        · 连跑两次，第二次 `encoded == 0`、`status == "noop"`。
    """
    say = log or (lambda _m: None)
    text_fn = text_fn or _default_m3_text
    ensure(conn)

    before = missing_ids(conn, "vec_m3")
    lag_before = len(before)
    if not before:
        return {"status": "noop", "encoded": 0,
                "lag_before": 0, "lag_after": 0, "dim": 0}

    todo = before[:limit] if limit and limit > 0 else before
    if not todo:
        return {"status": "skipped", "encoded": 0,
                "lag_before": lag_before, "lag_after": lag_before, "dim": 0}

    say("[m3] 缺口 %d 块，本次补 %d 块（batch=%d）…" % (lag_before, len(todo), batch))
    done = 0
    dim = 0
    step = max(1, int(batch or 32))
    for i in range(0, len(todo), step):
        part = todo[i:i + step]
        pairs = _fetch_texts(conn, part, text_fn)
        if not pairs:           # 取到的 chunk 被并发删了：跳过而不是造空向量
            continue
        ids = [p[0] for p in pairs]
        vecs = encode([p[1] for p in pairs])
        blobs = [_to_blob(v) for v in vecs]
        if len(blobs) != len(ids):
            raise ValueError("编码器返回行数不符：%d vs %d" % (len(blobs), len(ids)))
        if not dim and blobs:
            dim = _blob_dim(blobs[0])
        # 🔴 INSERT OR REPLACE 作用在「缺失 id」上 = 纯新增；已存在的行永远不会被这里碰到
        conn.executemany("INSERT OR REPLACE INTO vec_m3(chunk_id, vec) VALUES(?,?)",
                         list(zip(ids, blobs)))
        conn.commit()
        done += len(ids)

    n_now = conn.execute("SELECT COUNT(*) FROM vec_m3").fetchone()[0]
    write_m3_model(conn, dim, n_now, model)
    conn.commit()
    lag_after = len(missing_ids(conn, "vec_m3"))
    say("[m3] 补写完成：%d 块，缺口 %d → %d" % (done, lag_before, lag_after))
    return {"status": "encoded", "encoded": done,
            "lag_before": lag_before, "lag_after": lag_after, "dim": dim}


if __name__ == "__main__":
    # 只读自检：把当前库的四表一致性打出来（不写库）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import json as _json
    import kb_index  # noqa: E402

    _conn = kb_index.connect(readonly=True)
    try:
        print(_json.dumps(diff_report(_conn), ensure_ascii=False, indent=2))
    finally:
        _conn.close()
