# -*- coding: utf-8 -*-
"""BuildLock 回归测试（BUG-20261022-19 第 4 层根治的验收）。

验证三件事：
  1. 互斥：A 持锁时 B 拿不到 → 抛 Busy（而不是静默并发写库）
  2. 排队：--wait 语义下 B 能等到 A 释放后拿到锁
  3. 🔴 强杀不残留：A 被 taskkill /F 杀掉后，B 仍能拿到锁
     （这是选 OS 文件锁而非「锁文件+PID」的唯一理由，必须实测）

改 kb_index.BuildLock 后必跑。
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 子进程：拿锁 → 打印 HELD → 睡 N 秒 → 释放
HOLDER = r'''
import sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"%s")
from kb_index import BuildLock
lk = BuildLock()
lk.acquire(wait=0)
print("HELD", flush=True)
time.sleep(%d)
lk.release()
print("RELEASED", flush=True)
''' % (HERE.replace("\\", "\\\\"), 20)

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def try_lock_child():
    """子进程视角：尝试立刻拿锁，成功打印 GOT，Busy 打印 BUSY。"""
    code = r'''
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"%s")
from kb_index import BuildLock
try:
    lk = BuildLock()
    lk.acquire(wait=0)
    print("GOT", flush=True)
    lk.release()
except BuildLock.Busy as e:
    print("BUSY:" + str(e)[:60], flush=True)
''' % HERE.replace("\\", "\\\\")
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return (r.stdout or "").strip()


print("=== 1. 互斥：持锁期间别人拿不到 ===")
# stderr 走 DEVNULL：taskkill 等系统命令输出是 GBK，会让 subprocess 的
# 读线程抛 UnicodeDecodeError（不影响结果，但每次都打一串 traceback）
p = subprocess.Popen([PY, "-c", HOLDER], stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                     errors="replace")
# 等子进程打印 HELD
t0 = time.time()
held = False
while time.time() - t0 < 15:
    line = p.stdout.readline()
    if line and "HELD" in line:
        held = True
        break
    if p.poll() is not None:
        break
check("子进程拿到锁", held)
out = try_lock_child()
check("第二个进程被拒", out.startswith("BUSY"), "→ " + out[:70])

print("\n=== 2. 🔴 强杀后锁自动释放（OS 层保证）===")
# 🔴 跨平台：`taskkill` 是 Windows 专用命令，Linux/macOS 上直接抛 FileNotFoundError，
# 整条测试异常退出（CI 里表现为「2 PASS / 0 FAIL 但 rc≠0」——看着像断言全过，其实没跑完）。
# POSIX 用 SIGKILL，语义等价：进程被强杀、不留清理机会，靠 OS 释放锁。
if os.name == "nt":
    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                   capture_output=True, text=True)
else:
    p.kill()                      # SIGKILL
time.sleep(1.5)
out2 = try_lock_child()
check("强杀后能重新拿到锁", out2.startswith("GOT"), "→ " + out2[:70])

print("\n=== 3. 排队等待：本进程持锁，子进程 wait 应等到释放后拿到 ===")
sys.path.insert(0, HERE)
from kb_index import BuildLock  # noqa: E402

CODE_WAIT = (
    "import sys, time\n"
    "sys.stdout.reconfigure(encoding='utf-8')\n"
    "sys.path.insert(0, r'" + HERE + "')\n"
    "from kb_index import BuildLock\n"
    "t = time.time()\n"
    "try:\n"
    "    lk = BuildLock()\n"
    "    lk.acquire(wait=30, poll=2, verbose=False)\n"
    "    print('GOT after %.1fs' % (time.time() - t), flush=True)\n"
    "    lk.release()\n"
    "except BuildLock.Busy as e:\n"
    "    print('BUSY:' + str(e)[:60], flush=True)\n"
)

mine = BuildLock()
mine.acquire(wait=0)
print("  主进程已持锁，启动排队子进程…")
r3 = subprocess.Popen([PY, "-c", CODE_WAIT], stdout=subprocess.PIPE,
                      stderr=subprocess.DEVNULL, text=True,
                      encoding="utf-8", errors="replace")
time.sleep(4)          # 让子进程先撞一次锁，进入等待
mine.release()         # 释放后子进程应当拿到
try:
    out3, _ = r3.communicate(timeout=60)
except subprocess.TimeoutExpired:
    r3.kill()
    out3 = ""
out3 = (out3 or "").strip()
check("排队后拿到锁", out3.startswith("GOT after"), "→ " + out3[:70])

print("\n" + "=" * 52)
if fails:
    print("❌ 失败：%s" % ", ".join(fails))
    sys.exit(1)
print("✅ BuildLock 三项全过：互斥 / 强杀不残留 / 可排队")
sys.exit(0)
