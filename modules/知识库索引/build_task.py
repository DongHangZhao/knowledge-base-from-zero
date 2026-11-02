#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_task — 多会话构建队列（T12，ROADMAP E5）
=================================================

解决什么问题：门户、定时任务（daily_run）、手动终端**多个会话**都可能触发
build。T3 之前的防线只有 BuildLock（OS 文件锁）——拿不到锁的会话直接跳过，
**没有队列、没有可见性**：谁在跑、前面排了几个人、上次成功没有，都答不了。

设计（作者口径：单写者 + 队列，不上多写、不引 Redis——SQLite 够用）：
  - `build_tasks`：每次 build 调用一行。状态机 queued → running → done/failed
  - `build_sessions`：会话级台账（first_seen / last_seen / build_count），
    回答「这个会话历史上跑过几次 build」
  - claim 用 `BEGIN IMMEDIATE` 事务原子领取：两个会话同时 claim 只有一条
    能成功（SQLite 单写者模型下的正确姿势），另一个看到的是「已被领走」
  - 为什么不上 Redis：单用户本地知识库，build 频率每天 1-2 次，SQLite 队列
    零新增运维面；为一个每天用两次的功能引一个常驻服务，依赖面/故障面全部
    上升——**不过度设计**

接线方式（kb_rag.cmd_build，追加式）：登记失败自动降级为不登记——
队列是可观测性增强，绝不能成为 build 的新故障点。
"""
import datetime
import sqlite3

# 状态机（只在这四个态之间迁移；写入侧强制小写，查询侧统一比较）
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def ensure_build_tasks(conn):
    """建 build_tasks / build_sessions 两表（幂等，IF NOT EXISTS）。

    由 kb_index.ensure_schema 末尾调用（照 _ensure_meta_columns 同款模式），
    也可被单独调用（测试/演示）。
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT NOT NULL,
            argv TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            queued_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            result TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_sessions(
            session_id TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            build_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_build_tasks_status ON build_tasks(status, id)")
    conn.commit()


def touch_session(conn, session):
    """登记/刷新会话台账（UPSERT：首见记 first_seen，每次 build_count+1）。"""
    now = _now()
    conn.execute("""
        INSERT INTO build_sessions(session_id, first_seen, last_seen, build_count)
        VALUES(?,?,?,1)
        ON CONFLICT(session_id) DO UPDATE SET last_seen=?, build_count=build_count+1
    """, (session, now, now, now))


def enqueue_build(conn, session, argv):
    """提交构建任务 → task_id。入队即持久化（commit），进程死了任务仍在。"""
    argv = str(argv or "")[:500]
    touch_session(conn, session)
    cur = conn.execute(
        "INSERT INTO build_tasks(session, argv, status, queued_at) VALUES(?,?,?,?)",
        (session, argv, STATUS_QUEUED, _now()))
    conn.commit()
    return cur.lastrowid


def claim_next_build(conn):
    """原子领取最早的 queued 任务 → task dict 或 None。

    并发安全的核心：`BEGIN IMMEDIATE` 在 SQLite 层拿写锁，SELECT+UPDATE
    在同一事务里——两个会话同时 claim，只有一个能改到行，另一个 SELECT
    到的已是 'running'（或被写锁挡住直到对方 commit 后看到空）。
    ⚠️ 调用方若有未提交事务，会先被 commit（claim 是队列边界操作，独立成事务）。
    """
    conn.commit()  # 收掉 legacy 模式隐式事务：否则 BEGIN 报 "transaction within a transaction"
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None  # 写锁被别的 claimer 握着：本次不抢，返 None 让调用方自行决定
    try:
        row = conn.execute(
            "SELECT id, session, argv, queued_at FROM build_tasks "
            "WHERE status=? ORDER BY id LIMIT 1", (STATUS_QUEUED,)).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        tid = row[0]
        cur = conn.execute(
            "UPDATE build_tasks SET status=?, started_at=? WHERE id=? AND status=?",
            (STATUS_RUNNING, _now(), tid, STATUS_QUEUED))
        if cur.rowcount != 1:               # 理论上 IMMEDIATE 下不会发生，双保险
            conn.execute("COMMIT")
            return None
        conn.execute("COMMIT")
        return {"id": tid, "session": row[1], "argv": row[2], "queued_at": row[3],
                "started_at": _now()}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def finish_build(conn, task_id, ok, result=""):
    """结束任务：done / failed。result 截断 500 字（rc/错误摘要）。"""
    conn.execute(
        "UPDATE build_tasks SET status=?, finished_at=?, result=? WHERE id=?",
        (STATUS_DONE if ok else STATUS_FAILED, _now(), str(result)[:500], task_id))
    conn.commit()


def queue_depth(conn):
    """当前还有多少个任务排着队（queued，未开始跑）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM build_tasks WHERE status=?", (STATUS_QUEUED,)).fetchone()[0]


def list_tasks(conn, limit=20):
    """最近的任务（新→旧），给 CLI/门户展示用。"""
    rows = conn.execute(
        "SELECT id, session, argv, status, queued_at, started_at, finished_at, result "
        "FROM build_tasks ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [{"id": r[0], "session": r[1], "argv": r[2], "status": r[3],
             "queued_at": r[4], "started_at": r[5], "finished_at": r[6],
             "result": r[7]} for r in rows]


def session_stats(conn, limit=10):
    """会话台账（build_count 多→少）。"""
    rows = conn.execute(
        "SELECT session_id, first_seen, last_seen, build_count FROM build_sessions "
        "ORDER BY build_count DESC, session_id LIMIT ?", (int(limit),)).fetchall()
    return [{"session_id": r[0], "first_seen": r[1], "last_seen": r[2],
             "build_count": r[3]} for r in rows]
