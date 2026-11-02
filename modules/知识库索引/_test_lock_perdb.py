#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_lock_perdb — BuildLock 锁路径必须「随库派生」（D2 多租户回归钉）

为什么要这条测试（2026-11-03）：
  旧实现里锁路径是**模块级常量**，只依赖模块目录，完全不看 `KB_DB_PATH`。
  两个租户库（A/kb.db、B/kb.db）并发 build 会抢同一把锁 —— 一方被 Busy 跳过，
  而 `_read_meta()` 报出的还是**另一个租户**的 PID 和命令行。这类 bug 不报错、
  不丢数据，只让人在排障时被彻底带偏，所以必须有回归钉。

钉四件事：
  ① 两个不同库（不同目录）→ 锁路径不同
  ② 同一个库连算两次 → 锁路径恒等（确定，不能带随机名）
  ③ `KB_BUILD_LOCK` 显式设置 → 压过一切派生规则（这也是一键回滚开关）
  ④ **D2 核心**：A 进程持 a.db 的锁时，B 取 b.db 的锁**必须成功**
     （旧实现会 Busy —— 因为两者本来指向同一个 state/build.lock）

跑法：python _test_lock_perdb.py      （纯 stdlib/sqlite，无 numpy/torch 依赖）
退出码：0 = 全 PASS；1 = 有 FAIL
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    """打一条 [PASS]/[FAIL] —— run_tests.py 的正则依赖这个格式，不许改。"""
    global PASS, FAIL
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", name,
                         ("  ｜ " + detail) if detail else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


TMP = tempfile.mkdtemp(prefix="kb_lock_perdb_")
A_DIR = os.path.join(TMP, "tenantA")
B_DIR = os.path.join(TMP, "tenantB")
os.makedirs(A_DIR, exist_ok=True)
os.makedirs(B_DIR, exist_ok=True)
A_DB = os.path.join(A_DIR, "kb.db")
B_DB = os.path.join(B_DIR, "kb.db")

# 🔴 必须在 import kb_index **之前**设好 KB_DB_PATH：kb_index.DB_PATH 是导入时快照。
# 这正好也复现了真实场景（每个租户进程/容器各带自己的 KB_DB_PATH 启动）。
os.environ["KB_DB_PATH"] = B_DB
os.environ.pop("KB_BUILD_LOCK", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_index  # noqa: E402  （上面的 env 必须先就位）

# 子进程夹具：按自己的 KB_DB_PATH 拿锁 → 打印 HELD → 睡 N 秒 → 释放
HOLDER = r'''
import sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"%(here)s")
from kb_index import BuildLock, default_lock_path
lk = BuildLock()
print("PATH " + lk.path, flush=True)
lk.acquire(wait=0)
print("HELD", flush=True)
time.sleep(%(sleep)d)
lk.release()
print("RELEASED", flush=True)
'''


def main():
    print("== 锁路径 per-DB 回归（D2）==")
    print("  临时根目录：%s" % TMP)

    # ① 不同库 → 不同锁路径
    pa = kb_index.default_lock_path(A_DB)
    pb = kb_index.default_lock_path(B_DB)
    check("① 两库锁路径不同", pa != pb, "A=%s B=%s" % (pa, pb))

    # 期望形态：<库所在目录>/build.lock
    check("①a A 的锁落在 A 库同级",
          os.path.abspath(pa) == os.path.abspath(os.path.join(A_DIR, "build.lock")), pa)
    check("①b B 的锁落在 B 库同级",
          os.path.abspath(pb) == os.path.abspath(os.path.join(B_DIR, "build.lock")), pb)

    # ② 同库两次 → 恒等（确定性，供多进程互认同一把锁）
    check("② 同一库两次调用恒等", kb_index.default_lock_path(B_DB) == pb, pb)

    # ②b 默认库（不传参 = 用 KB_DB_PATH 上的 DB_PATH）也走同一规则
    check("②b 默认库派生一致", kb_index.default_lock_path() == pb,
          "default=%s" % kb_index.default_lock_path())

    # ③ KB_BUILD_LOCK 压过一切
    forced = os.path.join(TMP, "forced", "custom.lock")
    os.environ["KB_BUILD_LOCK"] = forced
    try:
        check("③ KB_BUILD_LOCK 压过派生（A 库）",
              kb_index.default_lock_path(A_DB) == forced, kb_index.default_lock_path(A_DB))
        check("③b KB_BUILD_LOCK 压过派生（B 库）",
              kb_index.default_lock_path(B_DB) == forced)
        check("③c BuildLock 也认 env", kb_index.BuildLock().path == forced,
              kb_index.BuildLock().path)
    finally:
        os.environ.pop("KB_BUILD_LOCK", None)

    # ④ D2 回归钉：A 进程持 a.db 锁时，本进程取 b.db 锁必须成功
    script = os.path.join(TMP, "_holder.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(HOLDER % {"here": os.path.dirname(os.path.abspath(__file__)).replace("\\", "\\\\"),
                          "sleep": 20})
    env_a = dict(os.environ)
    env_a["KB_DB_PATH"] = A_DB
    env_a.pop("KB_BUILD_LOCK", None)
    proc = subprocess.Popen([sys.executable, script], env=env_a, cwd=TMP,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    held_path = ""
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith("PATH "):
                held_path = line[5:]
            if line == "HELD":
                break
        got_held = bool(held_path)
        check("④ 子进程已在 A 库持锁", got_held, held_path or "未收到 HELD")
        check("④a 子进程的锁 = A 库同级 build.lock",
              os.path.abspath(held_path) == os.path.abspath(os.path.join(A_DIR, "build.lock")),
              held_path)

        # 关键断言：不同库的锁**必须**互不阻塞
        t0 = time.time()
        ok = False
        detail = ""
        try:
            lk_b = kb_index.BuildLock().acquire(wait=0, verbose=False)
            ok = os.path.abspath(lk_b.path) == os.path.abspath(os.path.join(B_DIR, "build.lock"))
            detail = "path=%s 用时 %.2fs" % (lk_b.path, time.time() - t0)
            lk_b.release()
        except kb_index.BuildLock.Busy as e:
            detail = "被 A 的锁挡住：%s" % e
        check("④b A 持锁时 B 取自己的锁成功（D2 核心）", ok, detail)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ⑤ 补充：显式传 path 仍然生效（既有调用方兼容）
    explicit = os.path.join(TMP, "explicit.lock")
    check("⑤ BuildLock(path=...) 仍被尊重",
          kb_index.BuildLock(explicit).path == explicit)

    print("-" * 56)
    print("结果：%d PASS / %d FAIL" % (PASS, FAIL))
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
