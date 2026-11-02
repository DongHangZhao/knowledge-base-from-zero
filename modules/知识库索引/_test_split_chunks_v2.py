#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_test_split_chunks_v2 —— T6 分块器 slice 测试（§7 T03 验收 ①）。

覆盖：空串 / 纯标题 / 恰好 480 / 481 / 无标点长文 / 全标点 / 单字符 /
      断句吸附 / 纯空白块守卫 / 481 字无句末标点死循环回归（子进程 5s 超时）。

用法：
  python -u _test_split_chunks_v2.py
退出码 0 = 全绿；非 0 = 有失败（打印差异）。
"""
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from kb_index import split_chunks, CHUNK_MAX, SNAP, CHUNKER_FP  # noqa: E402


def _texts(chunks):
    return [t for _, _, t in chunks]


def check(name, got, want):
    if got == want:
        print("  ✓ %s" % name)
        return True
    print("  ✗ %s\n    实际 %r\n    期望 %r" % (name, got, want))
    return False


ok = True
print("CHUNK_MAX=%d SNAP=%r CHUNKER_FP=%r" % (CHUNK_MAX, SNAP, CHUNKER_FP))

# 1) 空串
ok &= check("空串", split_chunks("t", ""), [])
# 2) 纯标题（无正文）
ok &= check("纯标题", split_chunks("仅标题", ""), [])
# 3) 单字符
ok &= check("单字符", _texts(split_chunks("t", "甲")), ["甲"])
# 4) 恰好 480
s480 = "甲" * 480
r = split_chunks("t", s480)
ok &= check("恰好480=1块", len(r), 1)
ok &= check("恰好480内容不变", _texts(r), [s480])
# 5) 481（无标点 → 硬劈 480+1）
s481 = "甲" * 481
r = split_chunks("t", s481)
ok &= check("481=2块", len(r), 2)
ok &= check("481内容拼接还原", "".join(_texts(r)), s481)
# 6) 无标点长文（1000 个甲，硬劈 480+480+40，不死循环）
s1000 = "甲" * 1000
r = split_chunks("t", s1000)
ok &= check("无标点1000=3块", len(r), 3)
ok &= check("无标点1000块长", [len(t) for t in _texts(r)], [480, 480, 40])
ok &= check("无标点1000还原", "".join(_texts(r)), s1000)
# 7) 全标点（每字符都是句末标点 → 每块都不为空，吸附到标点）
sp = "。" * 1000
r = split_chunks("t", sp)
ok &= check("全标点1000块数>0", len(r) > 0, True)
ok &= check("全标点1000还原", "".join(_texts(r)), sp)
ok &= check("全标点无空白块", all(t.strip() for t in _texts(r)), True)
# 8) 断句吸附：480 附近有句号 → 切点吸到句号后
s = "甲" * 450 + "。" + "乙" * 200   # 451 处是句号
r = split_chunks("t", s)
first = _texts(r)[0]
ok &= check("吸附首块终点=句号后", len(first), 451)
ok &= check("吸附首块以句号结尾", first.endswith("。"), True)
ok &= check("吸附拼接还原", "".join(_texts(r)), s)
# 9) 纯空白块守卫：句号后接大段换行 → 不产出纯空白块
s = "甲" * 470 + "。" + "\n" * 500 + "丙"
r = split_chunks("t", s)
ok &= check("空白守卫无空白块", all(t.strip() for t in _texts(r)), True)
ok &= check("空白守卫内容还原", "".join(_texts(r)), s)
# 10) 短块合并阶段（CHUNK_MIN=80 不动）：短块并入前块
s = "甲" * 200 + "\n" + "乙" * 30   # 乙块 30 字 < 80，应并入前块
r = split_chunks("t", s)
ok &= check("短块并入前块=1块", len(r), 1)

# 11) 死循环回归：481 字无句末标点，子进程 5s 超时判失败
#     吸附逻辑写错会 i 不前进 → while 死循环 → 子进程卡死 → TimeoutExpired
code = (
    "import sys; sys.path.insert(0, %r); "
    "from kb_index import split_chunks; "
    "split_chunks('t', '甲'*481); print('DONE')" % __file__.rsplit("\\", 1)[0]
)
t0 = time.time()
try:
    out = subprocess.run([sys.executable, "-u", "-c", code],
                         capture_output=True, text=True, timeout=5)
    done_in = time.time() - t0
    ok &= check("481无标点死循环回归（%.2fs）" % done_in, out.stdout.strip(), "DONE")
except subprocess.TimeoutExpired:
    ok &= check("481无标点死循环回归", "TIMEOUT", "DONE")

print("\n结论：%s" % ("✅ 全绿" if ok else "✗ 有失败"))
sys.exit(0 if ok else 1)
