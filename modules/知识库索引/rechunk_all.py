#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rechunk_all.py —— T6 全库分块重建的唯一正确入口（§4.2 / §4.6）。

为什么必须存在这个脚本：
  CHUNK_MAX 1200→480 + 断句吸附后，`kb_index.py build --full` 只重切自有品类
  （wiki/modules/root/skills），`ingest_raw.run_ingest(full=True)` 对 hash 未变的
  raw 文档直接 skipped —— 直接跑增量 build 会得到「一半新分块一半旧分块」的
  混代索引，且库看起来完全健康。本脚本直接遍历 docs 全表（不分类别、不看
  content_hash），把结果写进影子表 `*_new`，再单事务原子 swap。

用法（每一步都是幂等或带护栏的）：
  python rechunk_all.py --dry-run    只统计（新块数/劈开率/受影响文档），不写表
  python rechunk_all.py --apply      全表重切 → 写 chunks_new/chunks_fts_new/
                                     chunks_trigram_new/chunk_bge_new(空表)
  （apply 后跑 reencode_gpu.py --apply 填 chunk_bge_new）
  python rechunk_all.py --swap       单事务原子换表 + 写 bge_model + 写 chunker_fp
  python rechunk_all.py --rollback   R1 回滚：把 *_old 换回正式名（秒级）

🔴 红线：
  - --swap 前必须等 team-lead 确认（先 --dry-run + 影子表行数核对 + 代码 diff 回报）。
  - 重建范围 = 全库（含 raw/隐私），隐私前缀是常量、不接受命令行覆盖；报告只出聚合数字。
  - --swap 持 BuildLock 挡并发写；读仍打老表，检索不受影响。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kb_index  # noqa: E402

# 旧分块器指纹（R1 回滚后写入，让 guard_chunker_fp 能识别「库是旧分块、代码是新分块」
# 并拒绝后续增量 build，避免混代）。旧版没有显式指纹，用描述性常量补记。
OLD_CHUNKER_FP = "v1|max=1200|hard|ovl=0"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _cjk_ratio(s):
    """中文字符占比 —— 用于把「英文/代码硬劈」从「中文答案句劈开」里剔出去。"""
    if not s:
        return 0.0
    return len(_CJK_RE.findall(s)) / len(s)

# 影子表 → 正式表映射（swap 顺序：先正式表改名 *_old，再 *_new 改名正式表）
PAIRS = [
    ("chunks", "chunks_new"),
    ("chunks_fts", "chunks_fts_new"),
    ("chunks_trigram", "chunks_trigram_new"),
    ("chunk_bge", "chunk_bge_new"),
    ("vec_m3", "vec_m3_new"),
]

LOG_PATH = os.path.join(HERE, "state", "rechunk_migration.log")


def _log(msg):
    line = "[%s] %s" % (datetime.now().isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except sqlite3.OperationalError:
        return -1


def create_shadow_tables(conn):
    """幂等重建四张影子表：先 DROP 再 CREATE，保证 --apply 可重跑（重跑后需重跑 reencode）。"""
    conn.executescript("""
    DROP INDEX IF EXISTS idx_chunks_new_doc;
    DROP TABLE IF EXISTS chunks_new;
    DROP TABLE IF EXISTS chunks_fts_new;
    DROP TABLE IF EXISTS chunks_trigram_new;
    DROP TABLE IF EXISTS chunk_bge_new;
    CREATE TABLE chunks_new(
        id INTEGER PRIMARY KEY,
        doc_id INTEGER NOT NULL,
        chunk_no INTEGER NOT NULL,
        heading TEXT,
        content TEXT,
        path TEXT,
        title TEXT,
        mtime INTEGER,
        UNIQUE(doc_id, chunk_no)
    );
    CREATE INDEX idx_chunks_new_doc ON chunks_new(doc_id);
    CREATE VIRTUAL TABLE chunks_fts_new USING fts5(title, tokens, tokenize='unicode61');
    CREATE VIRTUAL TABLE chunks_trigram_new USING fts5(content, tokenize='trigram');
    CREATE TABLE chunk_bge_new(chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
    CREATE TABLE vec_m3_new(chunk_id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
    """)


def _rechunk(conn, dry_run):
    """遍历 docs 全表重切。dry_run=True 只统计不写表；False 写影子表并返回统计。"""
    t0 = time.time()
    docs = conn.execute(
        "SELECT id, path, title, content, mtime FROM docs ORDER BY id").fetchall()
    old_total = _count(conn, "chunks")
    # 一次性取全库旧块（按 doc_id 分组），避免 8 千次逐篇查询
    old_by_doc = {}
    for doc_id, content in conn.execute(
            "SELECT doc_id, content FROM chunks ORDER BY doc_id, chunk_no"):
        old_by_doc.setdefault(doc_id, []).append(content)

    new_total = 0
    docs_affected = 0
    docs_identical = 0
    hard_split = 0        # 长度恰 == CHUNK_MAX 的块（硬劈的保守上界代理）
    mid_split = 0         # 相邻块边界：左尾非标点 且 右首非标点（句子横跨边界，含英文/代码）
    mid_split_cjk = 0     # 上述中，边界区域（左末8+右首8字）CJK 占比 ≥0.4（≈中文答案句劈开）
    total_len = 0
    max_len = 0
    if not dry_run:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for k, (doc_id, path, title, content, mtime) in enumerate(docs):
            pieces = kb_index.split_chunks(title or "", content or "")
            new_texts = [t for _, _, t in pieces]
            old_texts = old_by_doc.get(doc_id, [])
            if new_texts == old_texts:
                docs_identical += 1
            else:
                docs_affected += 1
            new_total += len(pieces)
            for _, _, text in pieces:
                L = len(text)
                total_len += L
                max_len = max(max_len, L)
                if L == kb_index.CHUNK_MAX:
                    hard_split += 1
            # 句子劈开率结构自检：相邻块边界若「左尾、右首都不是句末标点」，
            # 说明边界落在句子中间（英文句点 '.' 不在 SNAP 里，故英文/代码会多算，
            # 用边界区域 CJK 占比过滤出中文答案句）。
            for a, b in zip(new_texts, new_texts[1:]):
                if not a or not b:
                    continue
                if a[-1] not in kb_index.SNAP and b[0] not in kb_index.SNAP:
                    mid_split += 1
                    if _cjk_ratio(a[-8:] + b[:8]) >= 0.4:
                        mid_split_cjk += 1
            if not dry_run:
                title_fts = kb_index.tokens_to_fts_text(title or "")
                for no, heading, text in pieces:
                    cur = conn.execute(
                        "INSERT INTO chunks_new(doc_id,chunk_no,heading,content,path,title,mtime)"
                        " VALUES(?,?,?,?,?,?,?)",
                        (doc_id, no, heading, text, path, title, mtime))
                    cid = cur.lastrowid
                    conn.execute(
                        "INSERT INTO chunks_fts_new(rowid,title,tokens) VALUES(?,?,?)",
                        (cid, title_fts, kb_index.tokens_to_fts_text(heading + "\n" + text)))
                    conn.execute(
                        "INSERT INTO chunks_trigram_new(rowid,content) VALUES(?,?)",
                        (cid, text))
                if (k + 1) % 300 == 0:
                    conn.commit()          # 分批提交，避免单事务持锁过久
                    conn.execute("BEGIN IMMEDIATE")
                    _log("  apply 进度 %d/%d 篇，已产 %d 块" % (k + 1, len(docs), new_total))
        if not dry_run:
            conn.commit()
    except BaseException:
        if not dry_run:
            conn.execute("ROLLBACK")
        raise

    stats = {
        "docs_total": len(docs),
        "old_chunks": old_total,
        "new_chunks": new_total,
        "delta": new_total - old_total,
        "delta_pct": round((new_total - old_total) / old_total * 100, 1) if old_total else 0.0,
        "docs_affected": docs_affected,
        "docs_identical": docs_identical,
        "hard_split_chunks": hard_split,
        "hard_split_ratio": round(hard_split / new_total * 100, 2) if new_total else 0.0,
        "mid_split_pairs": mid_split,
        "mid_split_cjk_pairs": mid_split_cjk,
        "cjk_split_ratio": round(mid_split_cjk / new_total * 100, 2) if new_total else 0.0,
        "avg_len": round(total_len / new_total, 1) if new_total else 0,
        "max_len": max_len,
        "elapsed_s": round(time.time() - t0, 1),
    }
    return stats


def _print_stats(stats):
    print("docs 总数        : %(docs_total)d" % stats)
    print("旧块数 → 新块数  : %(old_chunks)d → %(new_chunks)d（%(delta)+d，%(delta_pct).1f%%）" % stats)
    print("受影响文档数    : %(docs_affected)d / %(docs_total)d" % stats)
    print("逐字节不变文档  : %(docs_identical)d（短文档，无需重编码）" % stats)
    print("硬劈块（len==480）: %(hard_split_chunks)d，占比 %(hard_split_ratio).2f%%（含英文/代码，保守上界）" % stats)
    print("句子横跨边界(全)  : %(mid_split_pairs)d 对" % stats)
    print("中文答案句劈开率 : %(cjk_split_ratio).2f%%（边界区域 CJK 占优，应 ≤5%%）" % stats)
    print("块长 avg/max     : %(avg_len).0f / %(max_len)d" % stats)
    print("耗时            : %(elapsed_s).1fs" % stats)


def do_swap(conn, checkpoint_first=True):
    """单事务原子 swap：正式表 → *_old，*_new → 正式表，写 bge_model + chunker_fp。

    返回 dict（swap 前后三数），供回报用。
    """
    for prod, sh in PAIRS:
        if not _table_exists(conn, sh):
            raise SystemExit("✗ 影子表 %s 不存在：先 --apply 再 reencode_gpu.py --apply" % sh)
    if _table_exists(conn, "chunks_old"):
        raise SystemExit("✗ 已存在 chunks_old（可能已 swap 过）。先 --rollback 或人工确认后再操作")

    n_new = _count(conn, "chunks_new")
    n_fts = _count(conn, "chunks_fts_new")
    n_tri = _count(conn, "chunks_trigram_new")
    n_bge = _count(conn, "chunk_bge_new")
    n_m3 = _count(conn, "vec_m3_new")
    if n_new <= 0:
        raise SystemExit("✗ chunks_new 空，不能 swap")
    if n_bge <= 0:
        raise SystemExit("✗ chunk_bge_new 空（reencode_gpu 未完成），不能 swap")
    if n_m3 <= 0:
        raise SystemExit("✗ vec_m3_new 空（reencode_m3 未完成），不能 swap")
    if n_fts != n_new:
        raise SystemExit("✗ chunks_fts_new(%d) != chunks_new(%d)" % (n_fts, n_new))
    if n_tri != n_new:
        raise SystemExit("✗ chunks_trigram_new(%d) != chunks_new(%d)" % (n_tri, n_new))
    if n_bge != n_new:
        raise SystemExit("✗ chunk_bge_new(%d) != chunks_new(%d)" % (n_bge, n_new))
    if n_m3 != n_new:
        raise SystemExit("✗ vec_m3_new(%d) != chunks_new(%d)" % (n_m3, n_new))

    from bge_encoder import vector_fingerprint  # 延迟 import：单一真相源，防 tie_breaker 漂移
    tie = vector_fingerprint()

    # 保存旧 bge_model 行与旧 chunker_fp，供 R1 回滚恢复（swap 会覆盖它们）
    old_bge = conn.execute(
        "SELECT dim,n,model,built_at,tie_breaker FROM bge_model WHERE id=1").fetchone()
    old_fp = conn.execute(
        "SELECT value FROM index_state WHERE key='chunker_fp'").fetchone()

    old_chunks = _count(conn, "chunks")
    old_bge_n = _count(conn, "chunk_bge")

    conn.isolation_level = None
    if checkpoint_first:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 顺序：先把正式表挪开，再把影子表顶上（同一事务，读者在 COMMIT 时刻原子切换）
        conn.execute("ALTER TABLE chunks RENAME TO chunks_old")
        conn.execute("ALTER TABLE chunks_new RENAME TO chunks")
        conn.execute("ALTER TABLE chunks_fts RENAME TO chunks_fts_old")
        conn.execute("ALTER TABLE chunks_fts_new RENAME TO chunks_fts")
        conn.execute("ALTER TABLE chunks_trigram RENAME TO chunks_trigram_old")
        conn.execute("ALTER TABLE chunks_trigram_new RENAME TO chunks_trigram")
        conn.execute("ALTER TABLE chunk_bge RENAME TO chunk_bge_old")
        conn.execute("ALTER TABLE chunk_bge_new RENAME TO chunk_bge")
        conn.execute("ALTER TABLE vec_m3 RENAME TO vec_m3_old")
        conn.execute("ALTER TABLE vec_m3_new RENAME TO vec_m3")
        # 新 bge_model 行：dim=512 / n=新块数 / built_at=now / tie_breaker=新口径指纹
        conn.execute(
            "INSERT OR REPLACE INTO bge_model(id,dim,n,model,built_at,tie_breaker)"
            " VALUES(1,?,?,?,?,?)",
            (512, n_new, "BAAI/bge-small-zh (ModelScope)",
             datetime.now().isoformat(timespec="seconds"), tie))
        # m3 跨语塔模型元数据（与 bge_model 对称）。CREATE IF NOT EXISTS 防旧库无表。
        conn.execute("CREATE TABLE IF NOT EXISTS m3_model("
                     "id INTEGER PRIMARY KEY CHECK(id=1), dim INTEGER, n INTEGER, "
                     "model TEXT, built_at TEXT)")
        conn.execute(
            "INSERT OR REPLACE INTO m3_model(id,dim,n,model,built_at) VALUES(1,?,?,?,?)",
            (1024, n_new, "BAAI/bge-m3 (dense 1024, CLS+L2, fp16 cuda)",
             datetime.now().isoformat(timespec="seconds")))
        conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('chunker_fp',?)",
                     (kb_index.CHUNKER_FP,))
        # 回滚所需的「旧 bge_model 行 + 旧 chunker_fp」存进 index_state
        conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('pre_t6_bge_model',?)",
                     (json.dumps(old_bge, ensure_ascii=False),))
        conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('pre_t6_chunker_fp',?)",
                     (json.dumps(old_fp, ensure_ascii=False),))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    new_chunks = _count(conn, "chunks")
    new_bge = _count(conn, "chunk_bge")
    result = {
        "old_chunks": old_chunks, "old_bge": old_bge_n,
        "new_chunks": new_chunks, "new_bge": new_bge,
        "new_m3": _count(conn, "vec_m3"),
        "tie_breaker": tie, "chunker_fp": kb_index.CHUNKER_FP,
    }
    _log("SWAP 完成：chunks %d→%d，chunk_bge %d→%d，tie_breaker=%s" %
         (old_chunks, new_chunks, old_bge_n, new_bge, tie))
    return result


def do_rollback(conn):
    """R1 回滚：把 *_old 换回正式名，恢复旧 bge_model 行与旧 chunker_fp（秒级）。"""
    if not _table_exists(conn, "chunks_old"):
        raise SystemExit("✗ 无 chunks_old，无可回滚对象（说明从未 swap 过）")

    row = conn.execute(
        "SELECT value FROM index_state WHERE key='pre_t6_bge_model'").fetchone()
    old_bge = json.loads(row[0]) if row else None
    fp_row = conn.execute(
        "SELECT value FROM index_state WHERE key='pre_t6_chunker_fp'").fetchone()
    old_fp = json.loads(fp_row[0]) if fp_row else None

    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 当前正式表（新分块）先挪回 *_new 名（保留现场），再 *_old → 正式名
        conn.execute("ALTER TABLE chunks RENAME TO chunks_new")
        conn.execute("ALTER TABLE chunks_old RENAME TO chunks")
        conn.execute("ALTER TABLE chunks_fts RENAME TO chunks_fts_new")
        conn.execute("ALTER TABLE chunks_fts_old RENAME TO chunks_fts")
        conn.execute("ALTER TABLE chunks_trigram RENAME TO chunks_trigram_new")
        conn.execute("ALTER TABLE chunks_trigram_old RENAME TO chunks_trigram")
        conn.execute("ALTER TABLE chunk_bge RENAME TO chunk_bge_new")
        conn.execute("ALTER TABLE chunk_bge_old RENAME TO chunk_bge")
        conn.execute("ALTER TABLE vec_m3 RENAME TO vec_m3_new")
        conn.execute("ALTER TABLE vec_m3_old RENAME TO vec_m3")
        # m3_model 元数据（仅记录 n）同步回退到当前 vec_m3 行数（swap 覆盖过新值，rollback 不恢复旧值）
        try:
            n_m3_now = conn.execute("SELECT COUNT(*) FROM vec_m3").fetchone()[0]
            conn.execute("UPDATE m3_model SET n=? WHERE id=1", (n_m3_now,))
        except sqlite3.Error:
            pass
        if old_bge:
            conn.execute(
                "INSERT OR REPLACE INTO bge_model(id,dim,n,model,built_at,tie_breaker)"
                " VALUES(1,?,?,?,?,?)", tuple(old_bge))
        # chunker_fp：旧库从未有过这个键 → 删除；否则写回旧值
        if old_fp is None:
            conn.execute("DELETE FROM index_state WHERE key='chunker_fp'")
            conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('chunker_fp',?)",
                         (OLD_CHUNKER_FP,))
        else:
            conn.execute("INSERT OR REPLACE INTO index_state(key,value) VALUES('chunker_fp',?)",
                         (old_fp[0],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    _log("ROLLBACK 完成：已把 *_old 换回正式表，恢复旧 bge_model 与旧 chunker_fp")
    return {"chunks": _count(conn, "chunks"), "chunk_bge": _count(conn, "chunk_bge")}


def main():
    ap = argparse.ArgumentParser(description="T6 全库分块重建（影子表 + 原子 swap + R1 回滚）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="只统计不写表（只读连接）")
    g.add_argument("--apply", action="store_true", help="全表重切写影子表 *_new")
    g.add_argument("--swap", action="store_true", help="原子换表（须 apply+reencode 已完成）")
    g.add_argument("--rollback", action="store_true", help="R1 回滚：*_old 换回正式名")
    args = ap.parse_args()

    if args.dry_run:
        conn = kb_index.connect(readonly=True)
        _log("DRY-RUN 开始")
        stats = _rechunk(conn, dry_run=True)
        conn.close()
        _print_stats(stats)
        _log("DRY-RUN 结果：%s" % json.dumps(stats, ensure_ascii=False))
        return 0

    # --apply / --swap / --rollback 都是写库，持 BuildLock 挡并发写
    lk = kb_index.BuildLock().acquire(wait=0)
    try:
        conn = kb_index.connect()
        kb_index.ensure_schema(conn)
        if args.apply:
            _log("APPLY 开始")
            create_shadow_tables(conn)
            stats = _rechunk(conn, dry_run=False)
            conn.close()
            _print_stats(stats)
            _log("APPLY 完成：%s" % json.dumps(stats, ensure_ascii=False))
        elif args.swap:
            res = do_swap(conn)
            conn.close()
            print("SWAP 完成：")
            print("  chunks     %(old_chunks)d → %(new_chunks)d" % res)
            print("  chunk_bge  %(old_bge)d → %(new_bge)d" % res)
            print("  vec_m3     → %(new_m3)d" % res)
            print("  tie_breaker: %s" % res["tie_breaker"])
            print("  chunker_fp : %s" % res["chunker_fp"])
        elif args.rollback:
            res = do_rollback(conn)
            conn.close()
            print("ROLLBACK 完成：chunks=%(chunks)d chunk_bge=%(chunk_bge)d" % res)
        return 0
    finally:
        lk.release()


if __name__ == "__main__":
    sys.exit(main())
