#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inventory_builder.py —— 知识库体系扫描器
================================================
扫描知识库的真实家底，产出两份产物：
  1. inventory.json   —— 机器可读清单（门户前端/后端直接消费）
  2. 知识库体系梳理报告.md —— 人类可读梳理报告

扫描范围：
  - 系统/模块   modules/ 下每个子目录（从 README 抽标题/用途/状态）
  - 技能卡片   modules/技能工作台/skills_data.json（153 张卡，按来源分类）
  - 项目       wiki/项目/ + 顶层 ai决策管理施工落地项目/
  - 资源       wiki/ 各分区 + outputs/ + raw/ 文件统计
  - 文件统计   raw/ outputs/ wiki/ 按扩展名计数

设计取舍：
  - 用「扫描 + 读 README 首段」而非硬编码，保证新模块自动进清单；
  - 技能卡片直接复用技能工作台的扫描产物（不重复造轮子）；
  - 纯标准库，零依赖，方便随知识库一起迁移。

用法：
  python inventory_builder.py [--kb-root <你的知识库路径>] [--out 目录]
"""
import os
import sys
import json
import re
import datetime
import argparse

sys.stdout.reconfigure(encoding="utf-8")


# ---- 已知系统模块的「用途/状态」精炼摘要（来自 modules/README.md 与 可复用模块总览.md）----
# 仅作首屏精炼；扫描器仍会读取每个模块 README 作为权威说明。
CURATED_SYSTEMS = {
    "微信同步": ("微信文件 → 知识库 → 云端资料库 全链路", "✅ 生产运行中"),
    "资料库对接": ("WorkBuddy 云端资料库 API 统一封装", "✅ 已验证"),
    "简历求职": ("简历/自我介绍 docx→pdf 生成 + 云端同步", "✅ 已验证"),
    "版本管理": ("版本记录 + 分级备份快照（每次更新/推送必跑）", "✅ 已验证"),
    "媒体生成": ("免费生图/生视频/图表统一入口（Agnes + matplotlib）", "✅ 生产运行中"),
    "抖音心动视频": ("照片+歌+主题 → 抖音竖屏情感MV", "✅ 已验证"),
    "ai协作流水线": ("多模型分工协作自动开发流水线", "✅ QA 16/16 通过"),
    "浏览器自动化": ("轻量无头浏览器 Obscura（Rust，30MB）", "✅ 已安装"),
    "技能工作台": ("107+ 技能/模块 → 技能卡片：搜索/收藏/置顶/复制话术", "✅ 已上线"),
    "上下文交接": ("AGENTS.md 唯一真相源 + 6 工具薄转发层 + verify_handoff", "✅ 37/37 PASS"),
    "程序化视频": ("代码生成视频三件套 HyperFrames/Remotion/hypit", "✅ 已安装 v1.0.0"),
    "磁盘清理": ("磁盘告警只读侦察：定位大头→mtime 判死数据→安全查重", "✅ 已验证 v1.0.0"),
    "微信聊天记录导出": ("解密微信 4.x 本地数据库 → 导出聊天记录", "✅ 已验证 v1.0.0"),
    "微信聊天记录库": ("解密 → FTS5 全文检索库 → CLI/Web 双端查询", "✅ 已投产 v1.1.0"),
    "二维码系统": ("万物皆可扫码：链接/文本/WiFi/名片/媒体文件 → 二维码", "✅ 已验证 v1.0.0"),
    "后端开发": ("Spring Boot 4 + Vue 3 用户/部门系统（求职作品集）", "✅ 已完成验收"),
    "公众号日更": ("每天三篇自动生成并推草稿箱（言序为主）", "✅ 运行中"),
    "知识库管理": ("本地 Web 后台（8787 端口）：总控台+文档管理+QR 分发", "✅ 运行中 v1.1.0"),
    "知识库索引": ("SQLite FTS5+TF-IDF+RRF 三模式检索 + RAG 评测", "✅ 运行中 v1.2.0"),
    "知识沉淀": ("只读扫描「说要沉淀」与「真沉淀了」的差额", "✅ 运行中 v1.0.5"),
    "视频下载": ("微信视频号 sph 分享链接 → 解析 MP4 直链下载", "✅ 鉴权打通 v1.2.0"),
    "采集爬虫": ("kb_crawl 网页/文章采集入库", "✅ 已验证 v1.1.0"),
    "资产巡检": ("知识库资产盘点与覆盖度巡检", "📖 运行中"),
    "rea": ("REA 逆向 MCP：编排开源工具做联网/逆向", "✅ 已装 v6.7.0"),
    "agent资产模板": ("Agent 资产/提示词模板集合", "📖 模板区"),
    "微信同步": ("微信文件同步体系", "✅ 运行中"),
}


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def first_paragraph(md):
    """从 markdown 抽第一段非空、非标题、非表格、非列表的正文。"""
    lines = [l.strip() for l in md.splitlines()]
    out = []
    started = False
    for l in lines:
        if not l:
            if started:
                break
            continue
        # 跳过标题 / 表格 / 引用 / 列表 / 纯分隔
        if l.startswith("#") or l.startswith("|") or l.startswith(">") \
           or l.startswith("-") or l.startswith("*") or l.startswith("```") \
           or set(l) <= set("|-*>+ "):
            if started:
                break
            continue
        out.append(l)
        started = True
        if len("".join(out)) > 120:
            break
    if not out:
        return "（见模块 README）"
    return "".join(out)[:200]


def detect_status(md):
    """从 README 顶部识别模块状态，避免把随处可见的「🔴红线提醒」误判为状态。"""
    head = md[:2500]
    if "✅ 生产运行中" in head or "✅ 运行中" in head:
        return "✅ 运行中"
    if "✅ 已上线" in head:
        return "✅ 已上线"
    if "✅ 已完成验收" in head:
        return "✅ 已完成验收"
    if "✅ 已验证" in head or "✅ 已安装" in head:
        return "✅ 已验证"
    if "🆕" in head[:800]:
        return "🆕 新增"
    if "📖" in head[:800]:
        return "📖 文档"
    return "—"


def scan_systems(kb_root):
    systems = []
    mod_dir = os.path.join(kb_root, "modules")
    if not os.path.isdir(mod_dir):
        return systems
    for name in sorted(os.listdir(mod_dir)):
        p = os.path.join(mod_dir, name)
        if not os.path.isdir(p):
            continue
        readme = os.path.join(p, "README.md")
        purpose, status = CURATED_SYSTEMS.get(name, ("（待补用途）", "—"))
        title = name
        if os.path.isfile(readme):
            md = read_text(readme)
            if name not in CURATED_SYSTEMS:
                # 抽首标题 + 首段
                m = re.search(r"^#\s+(.+)$", md, re.M)
                if m:
                    title = m.group(1).strip()
                purpose = first_paragraph(md) or purpose
            status = detect_status(md)
        # 计数模块内脚本/文件
        py_count = sum(1 for _, _, fs in os.walk(p) for f in fs if f.endswith(".py"))
        systems.append({
            "id": "sys:" + name,
            "name": name,
            "title": title,
            "purpose": purpose,
            "status": status,
            "rel_path": "modules/" + name,
            "abs_path": p,
            "py_count": py_count,
        })
    return systems


def scan_skills(kb_root):
    skills = []
    cats = {}
    data_path = os.path.join(kb_root, "modules", "技能工作台", "skills_data.json")
    if os.path.isfile(data_path):
        try:
            data = json.load(open(data_path, encoding="utf-8"))
            for s in data.get("skills", []):
                # 卡片源文件路径：skill 的 SKILL.md / 提示词资产 md / 模块 README
                p = (s.get("path") or "").strip()
                if p and not os.path.isabs(p):
                    p = os.path.normpath(os.path.join(kb_root, p))
                p = heal_plugin_path(p)
                skills.append({
                    "id": "skill:" + s.get("id", s.get("name", "")),
                    "name": s.get("name", ""),
                    "cn_name": s.get("cn_name", ""),
                    "category": s.get("category", s.get("source", "未知")),
                    "source": s.get("source", ""),
                    "description": (s.get("description", "") or "")[:240],
                    "triggers": (s.get("triggers") or [])[:6],
                    "path": p,
                    "exists": bool(p and os.path.exists(p)),
                })
                c = s.get("category", s.get("source", "未知"))
                cats[c] = cats.get(c, 0) + 1
        except Exception as e:
            print("[WARN] 读取技能数据失败:", e)
    cat_list = [{"category": k, "count": v} for k, v in sorted(cats.items(), key=lambda x: -x[1])]
    return skills, cat_list


def scan_projects(kb_root):
    projects = []
    wiki_proj = os.path.join(kb_root, "wiki", "项目")
    if os.path.isdir(wiki_proj):
        for name in sorted(os.listdir(wiki_proj)):
            p = os.path.join(wiki_proj, name)
            if os.path.isdir(p):
                projects.append({"name": name, "rel_path": "wiki/项目/" + name, "abs_path": p, "type": "wiki"})
            elif name.endswith(".md"):
                projects.append({"name": name[:-3], "rel_path": "wiki/项目/" + name, "abs_path": p, "type": "wiki-md"})
    top = os.path.join(kb_root, "ai决策管理施工落地项目")
    if os.path.isdir(top):
        projects.append({"name": "ai决策管理施工落地项目", "rel_path": "ai决策管理施工落地项目", "abs_path": top, "type": "project-root"})
    return projects


def scan_resources(kb_root):
    resources = []
    wiki_dir = os.path.join(kb_root, "wiki")
    skip = {"项目", "个人信息", "编码指南", "log.md", "index.md", "log_20260810_092214_246.md",
            "知识库改进日志.md", "微信文件库.md", "隐私文件索引.md"}
    if os.path.isdir(wiki_dir):
        for name in sorted(os.listdir(wiki_dir)):
            p = os.path.join(wiki_dir, name)
            if name in skip:
                continue
            if os.path.isdir(p):
                n = sum(len(fs) for _, _, fs in os.walk(p))
                resources.append({"name": name, "rel_path": "wiki/" + name, "abs_path": p, "type": "wiki-zone", "files": n})
    # outputs / raw 作为大资源区
    for zone in ("outputs", "raw"):
        zp = os.path.join(kb_root, zone)
        if os.path.isdir(zp):
            n = sum(len(fs) for _, _, fs in os.walk(zp))
            resources.append({"name": zone + "（产物/原件区）", "rel_path": zone, "abs_path": zp, "type": "zone", "files": n})
    # 根级分区（自动覆盖，避免「知识库创建指南 / references / 编码指南」这类去处被漏掉）
    root_skip = {"modules", "raw", "outputs", "wiki", "知识库体系门户", ".workbuddy",
                 "__pycache__", "_tmp", "ai决策管理施工落地项目", ".git"}
    for name in sorted(os.listdir(kb_root)):
        p = os.path.join(kb_root, name)
        if not os.path.isdir(p) or name in root_skip or name.startswith("."):
            continue  # 跳过隐藏目录（.cache/.cursor/.github/.qoder 等工具缓存）
        n = sum(len(fs) for _, _, fs in os.walk(p))
        resources.append({"name": name, "rel_path": name, "abs_path": p, "type": "root-zone", "files": n})
    return resources


def heal_plugin_path(p):
    """插件升级后路径漂移自愈。

    技能卡的 path 记录的是安装时的版本目录（如 5.6.2-wb.xxx），插件升级后
    旧目录被删 → 卡片「打不开」。此处把 <plugin>/<version>/ 段换成当前实际
    存在的版本目录，保留剩余相对路径。
    """
    if not p or os.path.exists(p):
        return p
    m = re.match(r"^(.*?[\\/]plugins[\\/]cache[\\/][^\\/]+[\\/][^\\/]+)[\\/]([^\\/]+)[\\/](.+)$", p)
    if not m:
        return p
    base, _ver, rest = m.groups()
    if not os.path.isdir(base):
        return p
    try:
        vers = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    except Exception:
        return p
    # 先精确试剩余路径，多个命中时取最新版本
    hits = []
    for v in vers:
        cand = os.path.join(base, v, rest)
        if os.path.exists(cand):
            hits.append((os.path.getmtime(cand), cand))
    if hits:
        hits.sort(reverse=True)
        return hits[0][1]
    return p


def resolve_session_dir(kb_root):
    """定位「本知识库」的会话记录目录（WorkBuddy 按项目路径生成目录名）。"""
    proj_root = os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")
    if not os.path.isdir(proj_root):
        return ""
    kb_root = os.path.abspath(kb_root)
    # 例：<盘符>:\<知识库目录> → <盘符小写>-<目录>（: 去掉，路径分隔符换 -）
    exact = kb_root[0].lower() + kb_root[1:].replace(":", "").replace("\\", "-").replace("/", "-")
    exact = re.sub(r"-+", "-", exact)
    cand = os.path.join(proj_root, exact)
    if os.path.isdir(cand):
        return cand
    # 兜底：名字以 -<知识库目录名> 结尾且不再带子路径
    kb_name = os.path.basename(kb_root)
    best = ""
    try:
        for d in os.listdir(proj_root):
            dp = os.path.join(proj_root, d)
            if os.path.isdir(dp) and d.endswith("-" + kb_name):
                if not best or os.path.getmtime(dp) > os.path.getmtime(best):
                    best = dp
    except Exception:
        pass
    return best


def scan_entries(kb_root):
    """统一入口：把不在 modules/wiki 扫描范围内的关键去处也接进来。

    用户反馈「系统没集成全，聊天记录等页面打不开」——根因是
    这些去处散落在 .workbuddy/、模块目录里，清单只扫了 modules/wiki，
    前端自然没有入口。此处固定收敛为入口卡，存在性逐个实测。
    """
    entries = []

    def add(eid, name, desc, path, kind="folder", action="open_folder", icon="📁", exists=None):
        entries.append({
            "id": eid, "name": name, "desc": desc,
            "path": path, "kind": kind, "action": action, "icon": icon,
            "exists": bool(exists) if exists is not None else bool(path and os.path.exists(path)),
        })

    mod = os.path.join(kb_root, "modules")
    # 1) 总控台（8787）
    add("entry:kb8787", "知识库后台 8787", "总控台 + 文档管理 + QR 分发",
        os.path.join(mod, "知识库管理", "start-kb8787.bat"), "bat", "run_bat", "🖥️")
    # 2) 技能工作台
    add("entry:workbench", "技能工作台", "153 张技能卡：搜索/收藏/置顶/复制话术",
        os.path.join(mod, "技能工作台", "index.html"), "html", "open_file", "🎴")
    # 3) 记忆日志
    add("entry:memory", "记忆日志（.workbuddy/memory）", "每日工作日志 + 项目长期记忆",
        os.path.join(kb_root, ".workbuddy", "memory"), "folder", "open_folder", "🧠")
    # 4) 项目级 skills
    add("entry:skills", "项目技能目录（.workbuddy/skills）", "项目内 12 个 skill 本体",
        os.path.join(kb_root, ".workbuddy", "skills"), "folder", "open_folder", "🧩")
    # 5) 会话记录（与 AI 的聊天记录）
    sess_dir = resolve_session_dir(kb_root)
    add("entry:sessions", "聊天记录 / 会话记录", "与本知识库相关的 AI 会话 jsonl（含本次对话）",
        sess_dir, "folder", "open_folder", "💬")
    # 6) 微信聊天记录（两个模块）
    add("entry:wxchat", "微信聊天记录库", "解密 → FTS5 全文检索 → CLI/Web 查询",
        os.path.join(mod, "微信聊天记录库"), "folder", "open_folder", "🗂️")
    add("entry:wxexport", "微信聊天记录导出", "解密微信 4.x 本地库 → 导出聊天记录",
        os.path.join(mod, "微信聊天记录导出"), "folder", "open_folder", "📤")
    # 6.1) 门户工具页（贴地址下载视频 / 微信记录搜索 / 二维码）
    add("entry:tools", "工具页（下载视频 / 搜微信 / 二维码）",
        "贴视频号链接直接下载、搜本机微信记录、生成二维码，都在这一页",
        "tools", "page", "open_tools", "🧰", exists=True)
    add("entry:qr", "二维码工具（8787 /qr）", "文本·URL·WiFi·名片·文件内嵌，扫码经 /files/ 取本体",
        "http://127.0.0.1:8787/qr", "url", "open_url", "🔳", exists=True)
    add("entry:videodl", "视频下载（视频号）", "模块本体：sph_download.py + downloads/（Cookie 过期需重抓）",
        os.path.join(mod, "视频下载"), "folder", "open_folder", "🎬")
    add("entry:douyin", "抖音下载", "抖音视频/图集下载",
        os.path.join(mod, "抖音下载"), "folder", "open_folder", "📱")
    # 7) 四大区
    add("entry:raw", "raw 原件区", "只读原件（文档/媒体/项目资料）",
        os.path.join(kb_root, "raw"), "folder", "open_folder", "📥")
    add("entry:outputs", "outputs 产物区", "生成的报告/图片/视频/应用",
        os.path.join(kb_root, "outputs"), "folder", "open_folder", "📦")
    add("entry:wiki", "wiki 合成区", "结构化笔记与准则",
        os.path.join(kb_root, "wiki"), "folder", "open_folder", "📚")
    # 8) 准则与总览
    add("entry:guide", "编码/写作/智能体准则", "唯一真相源（当前 v3.37）",
        os.path.join(kb_root, "wiki", "编码指南"), "folder", "open_folder", "📏")
    add("entry:agents", "AGENTS.md 唯一真相源", "12 章规则 + 操作手册",
        os.path.join(kb_root, "AGENTS.md"), "md", "open_file", "📜")
    add("entry:refs", "references 模板区", "文章/日记/归档/raw 模板",
        os.path.join(kb_root, "references"), "folder", "open_folder", "🧰")
    add("entry:guidebook", "知识库创建指南", "从零搭库的说明文档",
        os.path.join(kb_root, "知识库创建指南"), "folder", "open_folder", "🧭")
    return entries


def scan_zones(kb_root):
    """outputs/ 与 raw/ 的一级子目录（带文件数），让每个产物区都能点进去。"""
    zones = []
    for zone in ("outputs", "raw"):
        zp = os.path.join(kb_root, zone)
        if not os.path.isdir(zp):
            continue
        for name in sorted(os.listdir(zp)):
            p = os.path.join(zp, name)
            if not os.path.isdir(p):
                continue
            n = sum(len(fs) for _, _, fs in os.walk(p))
            zones.append({
                "id": "zone:" + zone + "/" + name,
                "name": name, "rel_path": zone + "/" + name,
                "abs_path": p, "type": zone + "-zone", "files": n,
            })
    return zones


def scan_sessions(kb_root, limit=12):
    """最近的会话记录（jsonl），供门户「最近会话」直接打开。"""
    out = []
    sess_dir = resolve_session_dir(kb_root)
    if not sess_dir or not os.path.isdir(sess_dir):
        return out
    files = []
    try:
        for f in os.listdir(sess_dir):
            if f.endswith(".jsonl"):
                fp = os.path.join(sess_dir, f)
                files.append((os.path.getmtime(fp), fp))
    except Exception:
        return out
    files.sort(reverse=True)
    for mt, fp in files[:limit]:
        out.append({
            "name": datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M"),
            "rel_path": fp,
            "abs_path": fp,
            "type": "session",
            "size_kb": round(os.path.getsize(fp) / 1024, 1),
        })
    return out


def file_stats(kb_root):
    stats = {}
    for zone in ("raw", "outputs", "wiki"):
        zp = os.path.join(kb_root, zone)
        ext = {}
        total = 0
        if os.path.isdir(zp):
            for _, _, fs in os.walk(zp):
                for f in fs:
                    total += 1
                    e = os.path.splitext(f)[1].lower() or "(无扩展名)"
                    ext[e] = ext.get(e, 0) + 1
        top = dict(sorted(ext.items(), key=lambda x: -x[1])[:12])
        stats[zone] = {"total": total, "top_ext": top}
    return stats


def build(kb_root):
    systems = scan_systems(kb_root)
    skills, cats = scan_skills(kb_root)
    projects = scan_projects(kb_root)
    resources = scan_resources(kb_root)
    entries = scan_entries(kb_root)
    zones = scan_zones(kb_root)
    sessions = scan_sessions(kb_root)
    stats = file_stats(kb_root)
    inv = {
        "kb_root": kb_root,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "systems": len(systems),
            "skills": len(skills),
            "skill_categories": len(cats),
            "projects": len(projects),
            "resources": len(resources),
            "entries": len(entries),
            "zones": len(zones),
            "sessions": len(sessions),
            "skills_openable": sum(1 for s in skills if s.get("exists")),
            "raw_files": stats["raw"]["total"],
            "outputs_files": stats["outputs"]["total"],
            "wiki_files": stats["wiki"]["total"],
        },
        "systems": systems,
        "skills": skills,
        "skill_categories": cats,
        "projects": projects,
        "resources": resources,
        "entries": entries,
        "zones": zones,
        "sessions": sessions,
        "file_stats": stats,
    }
    return inv


def render_report(inv, out_path):
    s = inv["summary"]
    L = []
    L.append("# 知识库体系梳理报告\n")
    L.append(f"> 生成时间：{inv['generated_at']} ｜ 知识库根：`{inv['kb_root']}`\n")
    L.append("## 一、总览数字\n")
    L.append(f"- 系统/模块：**{s['systems']}** 个")
    L.append(f"- 技能卡片：**{s['skills']}** 张（分 **{s['skill_categories']}** 类来源）")
    L.append(f"- 项目：**{s['projects']}** 个")
    L.append(f"- 资源分区：**{s['resources']}** 个")
    L.append(f"- 文件总量：raw {s['raw_files']} ｜ outputs {s['outputs_files']} ｜ wiki {s['wiki_files']}\n")

    L.append("## 二、系统 / 模块（modules/）\n")
    L.append("| 模块 | 用途 | 状态 | 脚本数 |")
    L.append("|---|---|---|---|")
    for x in inv["systems"]:
        L.append(f"| {x['name']} | {x['purpose'][:60]} | {x['status']} | {x['py_count']} |")

    L.append("\n## 三、技能卡片分类（技能工作台）\n")
    L.append("| 来源分类 | 卡片数 |")
    L.append("|---|---|")
    for c in inv["skill_categories"]:
        L.append(f"| {c['category']} | {c['count']} |")
    L.append("\n> 技能卡片由 `modules/技能工作台/` 统一管理（搜索/收藏/置顶/一键复制话术）。")
    L.append("> 「提示词资产」类即立规类提示词 + AI 工作模式卡片——对话提示词「封存成卡片」的标准动作。\n")

    L.append("## 四、项目（wiki/项目/）\n")
    for x in inv["projects"]:
        L.append(f"- **{x['name']}** —— `{x['rel_path']}`")

    L.append("\n## 五、资源分区（wiki/ / outputs/ / raw/）\n")
    for x in inv["resources"]:
        L.append(f"- **{x['name']}** —— `{x['rel_path']}`（{x.get('files', '?')} 个文件）")

    L.append("\n## 六、统一入口（门户「入口」页签，逐个实测存在性）\n")
    L.append("| 入口 | 说明 | 路径 | 状态 |")
    L.append("|---|---|---|---|")
    for e in inv.get("entries", []):
        L.append(f"| {e['icon']} {e['name']} | {e['desc']} | `{e['path']}` | {'✅' if e['exists'] else '❌ 缺失'} |")
    if inv.get("sessions"):
        L.append(f"\n最近会话记录：**{len(inv['sessions'])}** 条可直达（含本次对话）。\n")

    L.append("\n## 七、文件类型分布（Top 扩展名）\n")
    for zone, d in inv["file_stats"].items():
        L.append(f"### {zone}/（共 {d['total']} 文件）")
        L.append("| 扩展名 | 数量 |")
        L.append("|---|---|")
        for e, n in d["top_ext"].items():
            L.append(f"| {e} | {n} |")
        L.append("")

    L.append("\n## 八、体系结论\n")
    L.append("- 知识库已具备**成熟的能力区（modules/）**与**检索层（kb_index）**，零散系统可通过「知识库体系门户」统一链接管理。")
    L.append("- 技能卡片机制已存在（技能工作台），但**非每次对话自动封存**；门户补「一键存卡 + 每日热度启动台」解决「按需点用」。")
    L.append("- 去隐私化后可作为开源模板分享（见 GitHub 仓库）。")

    txt = "\n".join(L) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(txt)
    return txt


def main():
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.dirname(here)
    ap.add_argument("--kb-root", default=default_root)
    ap.add_argument("--out", default=here)
    args = ap.parse_args()

    kb_root = os.path.abspath(args.kb_root)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    print(f"[1/3] 扫描知识库：{kb_root}")
    inv = build(kb_root)
    json_path = os.path.join(out, "inventory.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f"      → systems={inv['summary']['systems']} skills={inv['summary']['skills']} projects={inv['summary']['projects']}")

    print(f"[2/3] 生成梳理报告")
    report_path = os.path.join(out, "知识库体系梳理报告.md")
    render_report(inv, report_path)

    print(f"[3/3] 完成")
    print(f"      inventory.json : {json_path}")
    print(f"      梳理报告      : {report_path}")


if __name__ == "__main__":
    main()
