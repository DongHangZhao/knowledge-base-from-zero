# -*- coding: utf-8 -*-
"""T12 构建队列回归测试（build_task：状态机 + 并发安全 + 隐私回归不破）。

三条断言（全部真跑，临时 DB 不碰生产库）：
  1. 队列表状态机全流转：queued → running → done / queued → running → failed，
     时间戳三态齐全、queue_depth 递减、会话台账计数正确
  2. 🔴 并发安全：两个「会话」同时 enqueue + claim 各领到不同任务；
     4 个线程同时 claim 同一个任务，恰好一个成功（BEGIN IMMEDIATE 原子性）
  3. 隐私回归：_test_privacy_gate.py 子进程复跑 rc=0（T11 门禁没被 T12 改动破坏）

改 build_task.py / kb_index.ensure_schema 的队列表接线后必跑。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

TMP = tempfile.mkdtemp(prefix="kbbtask_")
os.environ["KB_DB_PATH"] = os.path.join(TMP, "kb.db")   # 必须在 import kb_index 之前
sys.path.insert(0, HERE)

import kb_index      # noqa: E402
import build_task    # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


try:
    conn = kb_index.connect()
    build_task.ensure_build_tasks(conn)

    print("=== 1. 状态机：queued → running → done / failed ===")
    check("初始队列深度 0", build_task.queue_depth(conn) == 0)
    t1 = build_task.enqueue_build(conn, "sess-A", "build --limit 5")
    t2 = build_task.enqueue_build(conn, "sess-B", "build --full")
    check("入队后深度 2", build_task.queue_depth(conn) == 2)
    task = build_task.claim_next_build(conn)
    check("claim 拿到最早的 t1（FIFO）", task and task["id"] == t1
          and task["session"] == "sess-A", "got=%s" % (task or {}).get("id"))
    r = conn.execute("SELECT status, started_at FROM build_tasks WHERE id=?",
                     (t1,)).fetchone()
    check("t1 → running 且 started_at 非空", r[0] == "running" and bool(r[1]))
    check("claim 后深度降为 1", build_task.queue_depth(conn) == 1)
    task2 = build_task.claim_next_build(conn)
    check("再 claim 拿到 t2", task2 and task2["id"] == t2)
    check("队列空后 claim 返 None", build_task.claim_next_build(conn) is None)
    build_task.finish_build(conn, t1, True, "rc=0")
    build_task.finish_build(conn, t2, False, "rc=2 skipped: build lock busy")
    r1 = conn.execute("SELECT status, queued_at, started_at, finished_at, result "
                      "FROM build_tasks WHERE id=?", (t1,)).fetchone()
    check("t1 → done（三时间戳齐全 + result 落账）",
          r1[0] == "done" and all(r1[1:4]) and r1[4] == "rc=0", str(r1))
    r2 = conn.execute("SELECT status, result FROM build_tasks WHERE id=?", (t2,)).fetchone()
    check("t2 → failed 且原因落账", r2[0] == "failed" and "lock busy" in r2[1], str(r2))
    t3 = build_task.enqueue_build(conn, "sess-A", "build")
    s = conn.execute("SELECT first_seen, last_seen, build_count FROM build_sessions "
                     "WHERE session_id='sess-A'").fetchone()
    check("会话台账：sess-A build_count=2 且 first_seen 不变",
          s[2] == 2 and s[0] <= s[1], str(s))
    tasks = build_task.list_tasks(conn, limit=3)
    check("list_tasks 新→旧且字段齐全",
          [x["id"] for x in tasks] == [t3, t2, t1] and "session" in tasks[0])
    # 收尾：把 t3 领走并结束，保证后续并发场景从一个**干净的空队列**起跑
    # （否则残留的 queued 任务会被并发线程顺路 claim 走，污染断言）
    t3c = build_task.claim_next_build(conn)
    build_task.finish_build(conn, t3c["id"], True, "cleanup")
    check("状态机测完队列清零", build_task.queue_depth(conn) == 0)

    print("\n=== 2. 并发：两个会话同时入队+claim 各领不同；4 线程抢 1 个只 1 个成功 ===")
    check("并发场景起跑前队列为空", build_task.queue_depth(conn) == 0)
    results = [None, None]
    barrier = threading.Barrier(2)

    def worker(idx, sess):
        c = kb_index.connect()          # 每线程独立连接（SQLite 连接非线程共享）
        try:
            build_task.ensure_build_tasks(c)
            barrier.wait()              # 对齐起跑线，制造真实争用
            tid = build_task.enqueue_build(c, sess, "build")
            task = build_task.claim_next_build(c)
            results[idx] = (tid, task["id"] if task else None)
        finally:
            c.close()

    ths = [threading.Thread(target=worker, args=(i, "sess-C%d" % i)) for i in range(2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=30)
    mine = [r[0] for r in results if r]
    claimed = [r[1] for r in results if r]
    check("两会话各入队成功", len(mine) == 2 and len(set(mine)) == 2, str(results))
    check("两会话 claim 到两个不同任务（无重复领取）",
          len(claimed) == 2 and len(set(claimed)) == 2 and set(claimed) == set(mine),
          str(results))

    # 单任务 4 线程抢：恰好一个 claim 成功（BEGIN IMMEDIATE 的原子领取）
    check("solo 场景起跑前队列已空", build_task.queue_depth(conn) == 0)
    solo = build_task.enqueue_build(conn, "sess-solo", "build")
    check("solo 入队后队列深度为 1", build_task.queue_depth(conn) == 1)
    got = [None] * 4
    barrier4 = threading.Barrier(4)

    def claimer(idx):
        c = kb_index.connect()
        try:
            barrier4.wait()
            task = build_task.claim_next_build(c)
            got[idx] = task["id"] if task else None
        finally:
            c.close()

    ths4 = [threading.Thread(target=claimer, args=(i,)) for i in range(4)]
    for th in ths4:
        th.start()
    for th in ths4:
        th.join(timeout=30)
    winners = [g for g in got if g is not None]
    check("4 线程抢 1 个任务：恰好 1 个成功且就是 solo", winners == [solo], str(got))
    st = conn.execute("SELECT status FROM build_tasks WHERE id=?", (solo,)).fetchone()[0]
    check("solo 任务状态为 running（被成功领取）", st == "running")

    print("\n=== 3. 隐私回归：_test_privacy_gate.py 复跑 rc=0 ===")
    r = subprocess.run([PY, os.path.join(HERE, "_test_privacy_gate.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    check("隐私门禁回归 rc=0", r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[-200:])

    conn.close()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 60)
if fails:
    print("❌ 失败：%s" % ", ".join(fails))
    sys.exit(1)
print("✅ 构建队列三项全过：状态机全流转 / 并发恰好一次领取 / 隐私回归不破")
sys.exit(0)
