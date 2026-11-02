# -*- coding: utf-8 -*-
"""T11 隐私门禁回归测试（cloud_publishable + privacy_scan 真调用）。

三条断言（全部真跑，临时目录 + 合成内容，不碰任何真实隐私文件）：
  1. 隐私路径过同步器被拒：raw/隐私/、raw/个人信息/（含子目录）→ blocked
  2. 白名单外品类被拒：微信文件/项目（示例市原始数据）/生活/其他/outputs → blocked
  3. 含隐私导出包报 BLOCK：白名单文件 + 1 个隐私文件打成导出包，
     **真调用** tools/privacy_scan.py（仓库根 tools/），断言 rc=1/BLOCK≥1
     且命中文件就是隐私文件；对照组（只有白名单文件）必须 0 BLOCK——
     证明 BLOCK 来自隐私文件而非测试脚手架自身。

改 cloud_publishable.py 的名单/优先级，或改 privacy_scan 集成方式后必跑。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, HERE)

import cloud_publishable as cp    # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        fails.append(name)


TMP = tempfile.mkdtemp(prefix="kbgate_")
try:
    print("=== 1. 隐私路径过同步器被拒（黑名单优先，含子目录）===")
    for p in ("raw/隐私/测试笔记.md",
              "raw/隐私/子目录/更深的/照片说明.txt",
              "raw/个人信息/示例用户-基础档案.md",
              "raw/个人信息/a/b/c.jpg"):
        ok, why = cp.assert_publishable(p)
        check("被拒：%s" % p, not ok, why)
    # 反向：同层级的非隐私路径必须放行（证明不是「raw/ 全拒」的假通过）
    ok, why = cp.assert_publishable("raw/技术/测试资料.md")
    check("放行对照：raw/技术/测试资料.md", ok, why)

    print("\n=== 2. 白名单外品类被拒（未枚举 = 不上云）===")
    for p, note in (
        ("raw/微信文件/某某/聊天记录.md", "个人往来"),
        ("raw/项目/示例市五大系统/原始数据.xlsx", "AGENTS L1 点名：示例市原始数据"),
        ("raw/生活/日记.md", "个人生活"),
        ("raw/其他/杂项.txt", "未分类杂项"),
        ("outputs/每日运势/图.png", "产物区不在白名单"),
        ("SKILL.md", "根级文件不在白名单"),
        ("", "空路径"),
    ):
        ok, why = cp.assert_publishable(p)
        check("被拒：%s（%s）" % (p or "<空>", note), not ok, why)
    # 白名单正面样本 + 段黑名单（备份/索引库不上云）
    for p in ("wiki/技术/测试.md", "wiki/index.md", "modules/README.md",
              "raw/AI资讯/2026-10-23-新闻.md", "raw/采集/网页存档.md"):
        ok, why = cp.assert_publishable(p)
        check("放行：%s" % p, ok, why)
    for p in ("modules/知识库索引/_versions/kb.db.bak-20261022-000001",
              "modules/知识库索引/state/kb.db",
              "modules/知识库索引/envs/Lib/site-packages/x.py"):
        ok, why = cp.assert_publishable(p)
        check("🔴 本地派生段被拒：%s" % p, not ok, why)
    # 批量版：两份清单 + 顺序保留
    allowed, blocked = cp.filter_publishable(
        ["wiki/a.md", "raw/隐私/b.md", "raw/技术/c.md", "raw/生活/d.md"])
    check("filter_publishable 分流正确",
          allowed == ["wiki/a.md", "raw/技术/c.md"]
          and [b[0] for b in blocked] == ["raw/隐私/b.md", "raw/生活/d.md"]
          and all(len(b) == 2 and b[1] for b in blocked),
          "allowed=%s blocked=%s" % (allowed, blocked))

    print("\n=== 3. 含隐私导出包报 BLOCK（真调用 privacy_scan.py）===")
    scanner = cp.find_privacy_scan()
    check("找到 privacy_scan.py", bool(scanner), scanner or "")
    exp = os.path.join(TMP, "export")
    os.makedirs(os.path.join(exp, "wiki", "技术"), exist_ok=True)
    os.makedirs(os.path.join(exp, "raw", "隐私"), exist_ok=True)
    # 白名单文件：内容必须干净（无绝对路径/手机号/邮箱/IP/密钥）
    clean = os.path.join(exp, "wiki", "技术", "导出测试文档.md")
    with open(clean, "w", encoding="utf-8") as f:
        f.write("# 导出测试文档\n\n这是本地先行闭环的导出样本，讨论知识库检索系统的"
                "倒排索引与向量召回两部分设计。路径引用一律用相对路径，"
                "如 wiki/技术/示例.md。\n")
    # 隐私文件：合成敏感内容（假手机号 + 自定义词表词），显式标注测试假数据
    priv = os.path.join(exp, "raw", "隐私", "测试私有笔记.md")
    with open(priv, "w", encoding="utf-8") as f:
        f.write("# 测试私有笔记（合成假数据，非真实隐私）\n\n"
                "备注 测试敏感词ZZZ（合成隐私标记，非真实数据）。\n")
    terms = os.path.join(TMP, "terms.txt")
    with open(terms, "w", encoding="utf-8") as f:
        f.write("# 测试词表（合成）\n测试敏感词ZZZ\n")
    ok, detail = cp.run_privacy_scan(exp, terms=terms)
    check("含隐私导出包：privacy_scan 判 BLOCK", not ok and detail.get("block", 0) >= 1,
          "rc=%s block=%s" % (detail.get("rc"), detail.get("block")))
    hit_files = [h.get("file", "") for h in detail.get("hits", []) if h.get("level") == "BLOCK"]
    check("BLOCK 命中隐私文件本身",
          any("raw/隐私/测试私有笔记.md" in h for h in hit_files), "hits=%s" % hit_files[:4])
    check("白名单文件未被误拦", not any("导出测试文档" in h for h in hit_files))
    # 对照组：只有白名单文件的导出包必须 0 BLOCK（证明 BLOCK 来源唯一）
    os.remove(priv)
    ok2, detail2 = cp.run_privacy_scan(exp, terms=terms)
    check("对照组（纯白名单包）0 BLOCK", ok2 and detail2.get("block") == 0,
          "rc=%s block=%s" % (detail2.get("rc"), detail2.get("block")))
    # 门禁故障=不放行：指向不存在的扫描器必须 ok=False
    os.environ["KB_PRIVACY_SCAN"] = os.path.join(TMP, "no_such_scanner.py")
    try:
        ok3, detail3 = cp.run_privacy_scan(exp, terms=terms)
        check("扫描器缺失时门禁不放行", not ok3, detail3.get("error", ""))
    finally:
        os.environ.pop("KB_PRIVACY_SCAN", None)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 60)
if fails:
    print("❌ 失败：%s" % ", ".join(fails))
    sys.exit(1)
print("✅ 隐私门禁三项全过：黑名单优先 / 白名单外拒 / 含隐私包 BLOCK（对照组干净）")
sys.exit(0)
