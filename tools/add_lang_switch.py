# -*- coding: utf-8 -*-
"""给所有中文文档插入语言切换行（幂等：已有 English 链接则跳过）。

用法：
    python tools/add_lang_switch.py            # 执行
    python tools/add_lang_switch.py --dry-run  # 只看会改哪些
"""
import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 中文文件 -> 英文文件（相对仓库根）
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


def rel_link(src, dst):
    return os.path.relpath(os.path.join(REPO, dst),
                           os.path.dirname(os.path.join(REPO, src))).replace("\\", "/")


def insert(path, en_rel):
    full = os.path.join(REPO, path)
    with open(full, encoding="utf-8") as f:
        lines = f.read().split("\n")

    # 幂等：任何一行同时含 English 与 .en.md 就认为已加
    for ln in lines[:8]:
        if ".en.md" in ln and "English" in ln:
            return "skip", 0

    bar = "**简体中文** ｜ [English](%s)" % en_rel
    # 有 H1 就插在 H1 之后；否则（如 NOTICE）插在首行标题之后
    idx = 0
    for i, ln in enumerate(lines[:10]):
        if ln.startswith("# "):
            idx = i
            break
    block = [bar, ""]
    # 保证 H1 与切换行之间恰好一个空行
    if idx + 1 < len(lines) and lines[idx + 1].strip() == "":
        lines[idx + 2:idx + 2] = block
    else:
        lines[idx + 1:idx + 1] = [""] + block
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return "done", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    done = skipped = missing = 0
    for zh, en in PAIRS:
        if not os.path.isfile(os.path.join(REPO, en)):
            print("✗ 英文版缺失: %s" % en)
            missing += 1
            continue
        if a.dry_run:
            print("· would edit %s -> %s" % (zh, rel_link(zh, en)))
            done += 1
            continue
        st, _ = insert(zh, rel_link(zh, en))
        print(("✓ %s" if st == "done" else "· %s 已存在") % zh)
        done += st == "done"
        skipped += st == "skip"

    print("\n插入 %d · 跳过 %d · 英文缺失 %d" % (done, skipped, missing))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
