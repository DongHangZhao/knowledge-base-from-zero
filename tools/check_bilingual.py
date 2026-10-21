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
