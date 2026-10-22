#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_privacy_binary.py —— privacy_scan 二进制通道回归测试

守护的逻辑
----------
privacy_scan 扫 exe/zip 等二进制时，先按 utf-8 / gbk 解码再匹配。但解码时
`errors="ignore"` 会丢弃无效字节，把原本**不相邻**的 ASCII 字节拼成敏感词
—— 实测一个 9MB 的 PyInstaller 产物用 GBK 解码后凭空凑出了词表里的
ASCII 词，而原始字节流里零命中（上下文是 GBK 解码出来的 CJK 乱码）。
因此词表分支改成：解码流命中只报 INFO，**原始字节命中才 BLOCK**。

这个改动是「降低告警级别」，属于削弱安全，所以必须有回归测试反向证明：
真泄漏一个都没被降级。

用例
    A. 二进制含 UTF-8 中文敏感词 → BLOCK
    B. 二进制含 GBK   中文敏感词 → BLOCK
    C. 二进制含 ASCII 敏感词     → BLOCK
    D. 纯随机字节（解码噪声）    → 不 BLOCK

⚠️ 本文件**故意不硬编码任何真实敏感词**：
   优先从外部词表（默认 ~/.kb_privacy_terms.txt，绝不入库）取词；
   取不到就退回中性占位词，只验证通道逻辑本身。

用法
    python tools/test_privacy_binary.py
    python tools/test_privacy_binary.py --terms C:/path/to/terms.txt
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import privacy_scan as ps  # noqa: E402

# 仓库内的中性占位词：没有任何真实含义，只用来验证「原始字节通道能否抓到」
PLACEHOLDER = "KBHUBPROBEMARK"


def pick_terms(terms_path):
    """取一个中文词 + 一个 ASCII 词用于测试；拿不到真实词表就用占位词。"""
    words = ps.load_terms(terms_path) if terms_path else []
    cn = next((w for w in words if any(ord(c) > 127 for c in w)), None)
    asc = next((w for w in words if w.isascii() and w.isalpha()), None)
    src = "外部词表" if (cn or asc) else "内置占位词"
    return cn or PLACEHOLDER, asc or PLACEHOLDER, src


def run(name, blob, terms, want_block):
    hits = []
    # 与隐私扫描器对 exe 的处理保持一致：utf-8 / gbk 两路解码都过一遍
    text = blob.decode("utf-8", "ignore") + blob.decode("gbk", "ignore")
    ps.scan_blob(text, "fake.bin", ps.RULES, terms, hits, binary=True, raw=blob)

    blocks = [h for h in hits if h["level"] == "BLOCK"]
    infos = [h for h in hits if h["level"] == "INFO"]
    ok = (len(blocks) > 0) == want_block

    print("%s：BLOCK %d / INFO %d" % (name, len(blocks), len(infos)))
    for h in blocks + infos:
        print("      %-5s %-28s | %s" % (h["level"], h["match"][:28], h["rule"]))
    print("      [%s] 期望%s BLOCK" % ("PASS" if ok else "FAIL",
                                       "有" if want_block else "无"))
    return ok


def main():
    ap = argparse.ArgumentParser(description="隐私扫描器二进制通道回归测试")
    ap.add_argument("--terms", default=os.path.expanduser("~/.kb_privacy_terms.txt"),
                    help="外部敏感词表（默认 ~/.kb_privacy_terms.txt，仓库外）")
    args = ap.parse_args()

    cn, asc, src = pick_terms(args.terms)
    print("测试用词来源：%s（中文 %d 字 / ASCII %d 字）" % (src, len(cn), len(asc)))

    results = []
    print("\n=== A. 二进制含 UTF-8 中文敏感词（真泄漏）===")
    results.append(run("A", os.urandom(2048) + cn.encode("utf-8") + os.urandom(2048),
                       [cn, asc], want_block=True))

    print("\n=== B. 二进制含 GBK 中文敏感词（真泄漏）===")
    try:
        blob_b = os.urandom(2048) + cn.encode("gbk") + os.urandom(2048)
    except UnicodeEncodeError:
        blob_b = os.urandom(2048) + cn.encode("utf-8") + os.urandom(2048)
    results.append(run("B", blob_b, [cn, asc], want_block=True))

    print("\n=== C. 二进制含 ASCII 敏感词（真泄漏）===")
    results.append(run("C", os.urandom(2048) + asc.encode("utf-8") + os.urandom(2048),
                       [cn, asc], want_block=True))

    print("\n=== D. 纯随机字节（解码噪声，必须不 BLOCK）===")
    results.append(run("D", os.urandom(65536), [cn, asc], want_block=False))

    print("\n" + "=" * 56)
    if all(results):
        print("✅ 全部通过：真泄漏仍 BLOCK，随机解码噪声已降级 INFO")
        return 0
    print("❌ 有 %d 个用例失败 —— 扫描器的二进制通道不可信，禁止发布"
          % results.count(False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
