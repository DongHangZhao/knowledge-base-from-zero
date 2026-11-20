# -*- coding: utf-8 -*-
"""中英成对完整性检查：文件都存在、双向切换行都在、字节数对照。"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

PAIRS = [
    ("README.md", "README.en.md"),
    ("RELEASE.md", "RELEASE.en.md"),
    ("NOTICE", "NOTICE.en.md"),
    ("kb-hub/README.md", "kb-hub/README.en.md"),
    ("docs/架构总览.md", "docs/architecture.en.md"),
    ("docs/双链路体系.md", "docs/dual-pipeline.en.md"),
    ("docs/从零到一搭建步骤.md", "docs/setup-from-zero.en.md"),
    ("docs/模块总览-示例.md", "docs/modules-overview.en.md"),
    ("docs/技能工作台.md", "docs/skill-workbench.en.md"),
    ("docs/团队协作规范与交付流程.md", "docs/team-collaboration.en.md"),
    ("docs/任务清单.md", "docs/task-list.en.md"),
    ("docs/FAQ.md", "docs/faq.en.md"),
    ("docs/开源守则.md", "docs/open-source-policy.en.md"),
    # v1.4.0 新增。加这一行之前它是「假绿」：文件确实生成并存在，
    # 但 PAIRS 里没有它 → check_bilingual 报到 19/19 全过，其实根本没校验这一对。
    # 凡是新增双语文档，必须同步加进本表，否则发布闸门覆盖不到。
    ("docs/检索层设计与评测.md", "docs/retrieval-engine.en.md"),
    ("docs/版本发展史.md", "docs/version-history.en.md"),
    ("docs/使用指南.md", "docs/usage.en.md"),
    ("templates/AGENTS.example.md", "templates/AGENTS.example.en.md"),
    ("templates/modules/README.example.md", "templates/modules/README.example.en.md"),
    ("templates/wiki/可复用模块/总览.example.md", "templates/wiki/可复用模块/总览.example.en.md"),
    ("templates/wiki/可复用模块/检索.example.md", "templates/wiki/可复用模块/检索.example.en.md"),
    ("templates/wiki/提示词资产/复用副本不改原版.example.md",
     "templates/wiki/提示词资产/复用副本不改原版.example.en.md"),
    ("templates/wiki/项目/示例项目.md", "templates/wiki/项目/示例项目.en.md"),
]


def head(p, n=800):
    with open(p, encoding="utf-8") as f:
        return f.read(n)


ok = 0
print("%-4s %-44s %10s" % ("", "中文文件", "中/英字节"))
print("-" * 72)
for zh, en in PAIRS:
    ez, ee = os.path.exists(zh), os.path.exists(en)
    hz = head(zh) if ez else ""
    he = head(en) if ee else ""
    zt = "English" in hz
    et = "简体中文" in he
    good = ez and ee and zt and et
    ok += good
    print("%-4s %-44s %7d/%-7d 切换%s/%s"
          % ("OK" if good else "BAD", zh,
             os.path.getsize(zh) if ez else 0,
             os.path.getsize(en) if ee else 0,
             "Y" if zt else "N", "Y" if et else "N"))

print("-" * 72)
print("成对且双向切换: %d / %d" % (ok, len(PAIRS)))

# 🔴 自动发现：本脚本靠 PAIRS 硬编码，漏加一行就会「全绿但没校验」（v1.4.0 与 v1.23.4
#    两次踩同一个坑）。所以反向扫一遍：凡是 docs/ 根层与 templates/ 下、**自己头部声明了
#    English 链接**的中文文档，就必须出现在 PAIRS 里；出现了却没进表 = 假绿，判 FAIL。
#    单语工程页（头部没有 English 链接）只提示，不算失败。
covered = {zh for zh, _ in PAIRS}
uncovered, unmanaged = [], []
for root in ("docs", "templates"):
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith(".md") or fn.endswith(".en.md"):
                continue
            p = os.path.join(dp, fn).replace("\\", "/")
            if p in covered:
                continue
            try:
                # 中文文档里声明双语的写法是 `[English](xxx.en.md)`
                claims_bilingual = "[English]" in head(p)
            except OSError:
                claims_bilingual = False
            (unmanaged if claims_bilingual else uncovered).append(p)

bad_pairs = len(PAIRS) - ok
if unmanaged:
    print("\n[FAIL] 自称双语但没写进 PAIRS（校验不到的假绿）：")
    for p in unmanaged:
        print("   " + p)
if uncovered:
    print("\n[提示] 未纳入校验的单语文档（如确认单语可忽略）：")
    for p in uncovered:
        print("   " + p)

ok_all = bad_pairs == 0 and not unmanaged
print("\n结论: %s（成对 %d/%d，未纳管 %d）"
      % ("PASS" if ok_all else "FAIL", ok, len(PAIRS), len(unmanaged)))

allmd = []
for dp, dns, fns in os.walk("."):
    dns[:] = [d for d in dns if d not in {".git", "__pycache__"}]
    for fn in fns:
        if fn.endswith(".md"):
            allmd.append(os.path.join(dp, fn))

en_md = [m for m in allmd if m.endswith(".en.md")]
zh_md = [m for m in allmd if not m.endswith(".en.md")]
print("仓库 md 总数: %d（中文 %d + 英文 %d）；另有 LICENSE / NOTICE 无扩展名，不计入"
      % (len(allmd), len(zh_md), len(en_md)))

import sys
sys.exit(0 if ok_all else 1)
