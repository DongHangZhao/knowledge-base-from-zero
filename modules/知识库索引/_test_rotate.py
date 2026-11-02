# -*- coding: utf-8 -*-
"""rotate_backups 回归测试：keep 数的是「份」不是「文件」。

起因（2026-10-22 真事故）：一份库 = 主文件 + -shm + -wal 三个文件。按文件
计数时 keep=3 实际只留 1 份库，当场误删了本该保留的事故前健康态备份。
这是**删除操作**，出错不可逆，必须有测试兜底。

做法：在临时目录造 5 份假备份（每份 3 个文件），monkeypatch kb_rag.HERE 后
调用真实函数，断言保留的是最新的 N 份、且每份的三个文件要么全留要么全删。
"""
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_rag  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


def make_fake(vdir, n):
    """造 n 份假备份，命名 kb.db.bak-2026102X-00000N（+ -shm/-wal）。"""
    for i in range(1, n + 1):
        base = "kb.db.bak-2026102%d-00000%d" % (i, i)
        for suffix in ("", "-shm", "-wal"):
            with open(os.path.join(vdir, base + suffix), "wb") as f:
                f.write(b"x" * (1024 if suffix == "" else 32))
        # 无关文件：不该被动
    with open(os.path.join(vdir, "build.lock.info"), "w", encoding="utf-8") as f:
        f.write("keep me")


def bases(vdir):
    out = set()
    for f in os.listdir(vdir):
        if not f.startswith("kb.db.bak"):
            continue
        for sfx in ("-shm", "-wal"):
            if f.endswith(sfx):
                f = f[: -len(sfx)]
                break
        out.add(f)
    return out


tmp = tempfile.mkdtemp(prefix="kbrot_")
vdir = os.path.join(tmp, "_versions")
os.makedirs(vdir)
orig_here = kb_rag.HERE
try:
    make_fake(vdir, 5)
    print("初始：5 份 / %d 个文件" % len(os.listdir(vdir)))

    kb_rag.HERE = tmp
    kb_rag.rotate_backups(keep=3)

    left = bases(vdir)
    print("轮转后剩余：%d 份 → %s" % (len(left), sorted(left)))
    check("保留 3 份（不是 3 个文件）", len(left) == 3, "实际 %d 份" % len(left))

    # 时间戳字典序：000005 最新，应保留 000003/000004/000005
    expect = {"kb.db.bak-20261023-000003",
              "kb.db.bak-20261024-000004",
              "kb.db.bak-20261025-000005"}
    check("保留的是最新的 3 份", left == expect, "缺 %s" % (expect - left))

    # 每份的三个文件必须同进同退
    intact = True
    for b in sorted(left):
        for suffix in ("", "-shm", "-wal"):
            if not os.path.exists(os.path.join(vdir, b + suffix)):
                intact = False
    check("保留的每份三个文件都完整", intact)

    stale_gone = not any(
        os.path.exists(os.path.join(vdir, "kb.db.bak-2026102%d-00000%d" % (i, i) + s))
        for i in (1, 2) for s in ("", "-shm", "-wal"))
    check("被淘汰的份连附属文件一起删净", stale_gone)

    check("非备份文件未被误删",
          os.path.exists(os.path.join(vdir, "build.lock.info")))

    # keep=0 必须什么都不删
    make_fake(vdir, 2)
    before = len(bases(vdir))
    kb_rag.rotate_backups(keep=0)
    check("keep=0 不删任何东西", len(bases(vdir)) == before)
finally:
    kb_rag.HERE = orig_here
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 50)
if fails:
    print("❌ 失败：%s" % ", ".join(fails))
    sys.exit(1)
print("✅ rotate_backups 回归测试全过（keep 按份计、附属同进同退、keep=0 安全）")
sys.exit(0)
