#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_example_inventory.py —— 生成「纯虚构」示例清单 inventory.example.json

为什么要这个脚本：
    真实清单（inventory.json）来自作者本机扫描，含真实模块名/项目名/文件量，
    绝对不能进仓库。手的办法是每次发布前重跑扫描再手动脱敏——很容易漏。
    这里把示例清单**代码化**：内容全为虚构，跑一次就得到干净的 example 文件。

用法：
    python tools/make_example_inventory.py              # 写到 kb-hub/inventory.example.json
    python tools/make_example_inventory.py --check      # 只校验现有文件是否为示例（供 CI/发布前用）

约定：
    · 所有路径以 ~/my-knowledge-base 为根，不出现任何真实绝对路径；
    · 所有 exists=false —— 示例数据不应让用户以为能点开（避免「点了没反应」）；
    · 项目名统一为「示例项目A/B/C」，模块名用通用能力名。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TARGET = os.path.join(REPO, "kb-hub", "inventory.example.json")

ROOT = "~/my-knowledge-base"


def p(rel: str) -> str:
    """相对路径 → 示例绝对路径（统一用反斜杠，贴近 Windows 真实形态）。"""
    return ROOT + "\\" + rel.replace("/", "\\")


def build():
    systems = [
        ("知识沉淀", "资料入库前的三问守卫与 Raw 溯源检查", "modules/知识沉淀", 2),
        ("检索索引", "SQLite FTS5 全文检索与向量检索的融合排序", "modules/检索索引", 4),
        ("二维码工具", "链接 / 文本 / 文件转二维码，本地生成不外发", "modules/二维码工具", 1),
        ("媒体下载", "贴链接下载视频音频，转写为文字稿后归档", "modules/媒体下载", 3),
        ("采集爬虫", "站点批量抓取、正文抽取、图片落盘、断点续传", "modules/采集爬虫", 5),
        ("备份巡检", "版本快照、滚动备份、资产完整性巡检", "modules/备份巡检", 2),
    ]

    skills = [
        ("doc-convert", "示例技能·文档转换", "内置", "把 PDF / DOCX / PPTX 转成 Markdown，保留标题层级与表格"),
        ("image-process", "示例技能·图片处理", "内置", "批量压缩、改尺寸、加水印或去水印，输出到指定目录"),
        ("sheet-analysis", "示例技能·表格分析", "内置", "读取 Excel/CSV，做透视、筛选、清洗，输出图表与结论"),
        ("slide-builder", "示例技能·演示文稿", "项目级", "按大纲生成幻灯片，统一版式与配色，导出 pptx"),
        ("rag-answer", "示例技能·检索问答", "项目级", "先检索本地库再作答，答案必须带原文路径，不得凭空补全"),
        ("code-review", "示例技能·代码审查", "用户级", "按团队规范检查命名、异常处理、边界条件与测试覆盖"),
        ("web-fetch", "示例技能·网页抓取", "用户级", "抓正文、去广告、转 Markdown，保留来源与抓取时间"),
        ("prompt-assets", "示例技能·提示词资产", "提示词资产", "把常用规矩固化成可复用话术卡片，支持搜索与一键复制"),
    ]

    resources = [
        ("学习笔记", "wiki/学习笔记", 12),
        ("技术", "wiki/技术", 18),
        ("想法与灵感", "wiki/想法与灵感", 6),
        ("生活", "wiki/生活", 9),
        ("项目", "wiki/项目", 3),
    ]

    entries = [
        ("hub", "知识库后台", "本地 Web 管理台：文件浏览 / 检索 / 工具", "modules/知识库管理", "folder", "open_folder", "🖥️"),
        ("skills", "技能卡片目录", "项目内 skill 本体（SKILL.md）", ".workbuddy/skills", "folder", "open_folder", "🧩"),
        ("memory", "记忆日志", ".workbuddy/memory 下的每日工作日志", ".workbuddy/memory", "folder", "open_folder", "🧠"),
        ("sessions", "聊天记录 / 会话记录", "AI 工具的会话存档（jsonl）", ".workbuddy/projects", "folder", "open_folder", "💬"),
        ("tools", "工具页（下载 / 搜索 / 二维码）", "三个高频动作就地做，不跳页", "modules/知识库管理/tools.html", "page", "open_tools", "🧰"),
        ("raw", "raw 原件区", "不可变原件：PDF / 图 / 视频 / 文档", "raw", "folder", "open_folder", "📥"),
    ]

    zones = [
        ("raw/学习笔记", "学习笔记", "raw-zone", 320),
        ("raw/技术", "技术", "raw-zone", 120),
        ("raw/想法与灵感", "想法与灵感", "raw-zone", 48),
        ("raw/生活", "生活", "raw-zone", 96),
        ("raw/项目", "项目", "raw-zone", 64),
        ("outputs/日报", "日报", "outputs-zone", 180),
        ("outputs/图表", "图表", "outputs-zone", 75),
        ("outputs/导出", "导出", "outputs-zone", 40),
    ]

    inv = {
        "kb_root": ROOT,
        "generated_at": "示例数据（运行 inventory_builder.py 后自动生成真实清单）",
        "summary": {
            "systems": len(systems),
            "skills": len(skills),
            "skill_categories": 4,
            "projects": 3,
            "resources": len(resources),
            "entries": len(entries),
            "zones": len(zones),
            "sessions": 2,
            "skills_openable": len(skills),
            "raw_files": 648,
            "outputs_files": 295,
            "wiki_files": 48,
        },
        "systems": [
            {
                "id": "sys:" + name,
                "name": name,
                "title": name,
                "purpose": purpose,
                "status": "—",
                "rel_path": rel,
                "abs_path": p(rel),
                "py_count": py,
            }
            for name, purpose, rel, py in systems
        ],
        "skills": [
            {
                "id": "skill:example:" + name,
                "name": name,
                "cn_name": cn,
                "category": cat,
                "source": cat,
                "description": desc,
                "triggers": [name, cn],
                "path": p(".workbuddy/skills/%s/SKILL.md" % name),
                "exists": False,
            }
            for name, cn, cat, desc in skills
        ],
        "skill_categories": [
            {"category": "内置", "count": 3},
            {"category": "项目级", "count": 2},
            {"category": "用户级", "count": 2},
            {"category": "提示词资产", "count": 1},
        ],
        "projects": [
            {"name": "示例项目A-业务系统实施", "rel_path": "wiki/项目/示例项目A.md", "type": "wiki-md"},
            {"name": "示例项目B-个人作品集", "rel_path": "wiki/项目/示例项目B.md", "type": "wiki-md"},
            {"name": "示例项目C-研究笔记", "rel_path": "wiki/项目/示例项目C.md", "type": "wiki-md"},
        ],
        "resources": [
            {"name": n, "rel_path": rel, "abs_path": p(rel), "type": "wiki-zone", "files": f}
            for n, rel, f in resources
        ],
        "entries": [
            {
                "id": "entry:" + eid,
                "name": name,
                "desc": desc,
                "path": p(rel),
                "kind": kind,
                "action": action,
                "icon": icon,
                "exists": False,
            }
            for eid, name, desc, rel, kind, action, icon in entries
        ],
        "zones": [
            {
                "id": "zone:" + rel,
                "name": name,
                "rel_path": rel,
                "abs_path": p(rel),
                "type": ztype,
                "files": files,
            }
            for rel, name, ztype, files in zones
        ],
        "sessions": [
            {
                "name": "2026-01-01 09:00",
                "rel_path": "~/.workbuddy/projects/<your-project>/<session>.jsonl",
                "abs_path": "~/.workbuddy/projects/<your-project>/<session>.jsonl",
                "type": "session",
                "size_kb": 128.0,
            },
            {
                "name": "2026-01-02 14:30",
                "rel_path": "~/.workbuddy/projects/<your-project>/<session-2>.jsonl",
                "abs_path": "~/.workbuddy/projects/<your-project>/<session-2>.jsonl",
                "type": "session",
                "size_kb": 256.0,
            },
        ],
        "file_stats": {
            "raw": {
                "total": 648,
                "top_ext": {".pdf": 120, ".md": 210, ".jpg": 180, ".docx": 60, ".png": 66, ".mp4": 12},
            },
            "outputs": {
                "total": 295,
                "top_ext": {".png": 75, ".md": 120, ".csv": 40, ".json": 30, ".html": 30},
            },
            "wiki": {"total": 48, "top_ext": {".md": 44, ".jpg": 4}},
        },
    }
    return inv


def is_example(path: str) -> bool:
    """判断清单是否为示例（用于 check 模式）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        print("[FAIL] 读取失败：%s (%s)" % (path, e))
        return False
    problems = []
    # 拼串构造：避免扫描器把本文件里的“反面样本”当成真命中
    BAD = ("F:" + "\\", "F:" + "/", "C:" + "\\Users", "Admin" + "istrator")
    root = str(d.get("kb_root", ""))
    if not root.startswith("~"):
        problems.append("kb_root 不是 ~ 开头：%r" % root)
    if "示例" not in str(d.get("generated_at", "")):
        problems.append("generated_at 未标记为示例")
    for sec in ("systems", "skills", "entries", "zones", "resources"):
        for item in d.get(sec) or []:
            blob = json.dumps(item, ensure_ascii=False)
            for bad in BAD:
                if bad in blob:
                    problems.append("%s 含真实路径 %s" % (sec, bad))
            if sec in ("entries", "skills") and item.get("exists") is True:
                problems.append("%s 的 exists 应为 false：%s" % (sec, item.get("name")))
    if problems:
        for x in problems[:20]:
            print("[FAIL] " + x)
        return False
    print("[OK] 示例清单干净：%s" % path)
    return True


def main():
    ap = argparse.ArgumentParser(description="生成或校验脱敏示例清单")
    ap.add_argument("--check", action="store_true", help="只校验现有 example 文件")
    ap.add_argument("--out", default=TARGET, help="输出路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果（供脚本调用）")
    args = ap.parse_args()

    if args.check:
        ok = is_example(args.out)
        if args.json:
            print(json.dumps({"ok": ok, "path": args.out}, ensure_ascii=False))
        return 0 if ok else 1

    inv = build()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
        f.write("\n")
    size = os.path.getsize(args.out)
    print("[OK] 已写入 %s（%d 字节）" % (args.out, size))
    print("     systems=%d skills=%d entries=%d zones=%d"
          % (len(inv["systems"]), len(inv["skills"]), len(inv["entries"]), len(inv["zones"])))
    return 0 if is_example(args.out) else 1


if __name__ == "__main__":
    sys.exit(main())
