# -*- coding: utf-8 -*-
"""_garbage_filter 单测：覆盖 4 类垃圾 + 真实内容零误杀 + 去重。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _garbage_filter as gf

fail = 0


def check(name, got, want):
    global fail
    ok = got == want
    if not ok:
        fail += 1
    print("  %s %-40s got=%s want=%s" % ("✓" if ok else "✗", name, got, want))


print("[垃圾应丢弃 = True]")
check("CSV 空行 ,,,,,,,", gf.is_garbage_block(",,,,,,\n;;;;;;\n, , ,"), True)
check("base64 长串", gf.is_garbage_block(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/abcdefghijklmnopqrstuvwxyz0123456789+/"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"), True)
check("PPT 模板标记(纯占位)", gf.is_garbage_block("点击此处添加标题"), True)
check("日志重复行", gf.is_garbage_block(
    "\n".join(["2026-11-02 10:00:00 INFO heartbeat ok"] * 8)), True)

print("[真实内容应保留 = False]")
check("RAG 真实段落", gf.is_garbage_block(
    "检索增强生成（RAG）让每句话都带引用，无据不答，是防止大模型幻觉的主流做法。"), False)
check("PPT 标记但含实义", gf.is_garbage_block(
    "点击此处添加 这一页讲大模型幻觉的三种缓解手段：RAG、对齐、人类反馈。"), False)
check("少量重复但非主导", gf.is_garbage_block(
    "报错信息如下：\nValueError: x\nValueError: x\n正常日志一行\n另一行正常日志"), False)

print("[去重]")
out = gf.dedup_blocks(["真实块A", "真实块A", "真实块B"])
check("去重后长度", len(out), 2)
check("去重内容", out, ["真实块A", "真实块B"])

print("[filter_blocks 端到端]")
mix = ["真实块A", ",,,,,,", "真实块A", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
       "abcdefghijklmnopqrstuvwxyz0123456789+/ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/", "真实块B"]
out2 = gf.filter_blocks(mix)
check("filter 后长度", len(out2), 2)
check("filter 内容", out2, ["真实块A", "真实块B"])

print("[filter_chunk_pairs 端到端 (heading,text)]")
pairs = [
    ("章一", "真实块A"),
    ("章一", ",,,,,,,"),                       # 垃圾 CSV 空行 → 丢
    ("章二", "点击此处添加标题"),              # 纯 PPT 占位 → 丢
    ("章三", "真实块B"),
    ("章四", "真实块B"),                        # 同文本不同 heading → 去重保留首个
]
out3 = gf.filter_chunk_pairs(pairs)
check("pairs 后长度", len(out3), 2)
check("pairs 内容", out3, [("章一", "真实块A"), ("章三", "真实块B")])
check("去重保留首个 heading", out3[1], ("章三", "真实块B"))

print("\n%s (fail=%d)" % ("✓ ALL PASS" if fail == 0 else "✗ FAIL", fail))
sys.exit(1 if fail else 0)
