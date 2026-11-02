#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cloud_publishable — 数据分级与隐私门禁（T11，ROADMAP E5）
============================================================

回答一个问题：**这个路径，能不能上云？**

作者 2026-10-22 21:24 拍板口径：
  - 隐私层绝不上云（准则 L1 不变，硬红线）
  - 非隐私层可上云 / 服务端化
  - 21:35 追加「本地先行」：上云前全链路先在本地等价环境跑通
    （证据见 `_demo_local_sync.py`：白名单过滤 → 导出 → 门禁 → 对端检索）

两道独立的门，缺一不可（职责不同，互不替代）：
  1. **本模块 = 路径门禁（品类级）**：隐私目录一律拒；白名单外一律拒
     （默认不上云、显式枚举才放行）。它在数据离开本机**之前**生效。
  2. **privacy_scan.py = 内容门禁（字符级）**：kb-from-zero 的发布终审，
     扫绝对路径/手机号/邮箱/密钥/媒体文件。它拦「路径合法但内容泄密」。
  ⚠️ 实测认知：privacy_scan 只扫内容、不认路径——`raw/隐私/` 里若有一篇
  内容干净的 md，内容门禁不会因路径拦它。所以路径门禁必须在前面。

路径语义：统一用 KB 相对路径（正斜杠，如 `wiki/技术/a.md`、`raw/隐私/x.md`），
兼容 `./` 前缀与 Windows 反斜杠输入。
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 黑名单（硬红线）
# 🔴 隐私黑名单（准则 L1 原文：raw/个人信息/、raw/隐私/ 不上云端、不进自动化）。
# 前缀匹配、含子目录；与白名单是否误枚举无关——先判黑名单再判白名单。
BLACKLIST_DIRS = (
    "raw/隐私/",
    "raw/个人信息/",
)
# 本地派生/备份/缓存段：出现在路径任何层级都不上云。
# 为什么需要它：modules/ 在白名单里（代码区），但 modules/知识库索引/_versions/
# 是 2.5GB 的 kb.db 备份（含 raw/隐私 正文）、state/ 是 1.3GB 活索引库——
# 目录级同步消费方若只靠前缀白名单，会把它们一起打包出去。
BLACKLIST_SEGMENTS = (
    "_versions",   # kb.db 备份（含隐私正文）
    "state",       # 本地索引库 kb.db
    "__pycache__",
    "node_modules",
    ".git",
    "envs",        # 本地 venv
    "models",      # 模型权重（百 MB 级二进制）
)

# ---------------------------------------------------------------- 白名单（显式枚举）
# 设计原则：**未枚举 = 不上云**（宁缺毋滥）。要放开新品类，改这里 + 跑测试，
# 不给「默认放行」留任何口子。
CLOUD_PUBLISHABLE = (
    # 合成层：知识库自己的文章——可上云的主体
    "wiki/",
    # 代码区：modules/ 各模块（段黑名单兜底排除 _versions/state/envs/models）
    "modules/",
    # 模板区
    "references/",
    # raw/ 非隐私品类（按 AGENTS.md 目录结构逐个显式枚举）：
    "raw/AI资讯/",     # 外部资讯采集
    "raw/Ai大模型/",   # 外部资讯采集
    "raw/学习笔记/",   # 学习笔记
    "raw/技术/",       # 技术资料
    "raw/工具软件/",   # 工具资料
    "raw/想法与灵感/", # 个人想法（非隐私类）
    "raw/采集/",       # 主动采集
)

# 明确**不**纳入白名单的 raw 品类及理由（写下来防止后来者误加）：
NOT_PUBLISHABLE_RAW = {
    "raw/隐私/": "隐私黑名单（准则 L1）",
    "raw/个人信息/": "隐私黑名单（准则 L1）",
    "raw/项目/": "含示例市项目原始数据（AGENTS.md L1 点名不上云），整类不纳",
    "raw/微信文件/": "个人往来文件，内容不可控",
    "raw/生活/": "个人生活记录",
    "raw/其他/": "未分类杂项，内容不可控",
    "raw/图库/": "媒体文件（内容门禁 L3 亦会拦）",
    "raw/视频号/": "媒体文件",
    "raw/视频号素材/": "媒体文件",
}


def _norm(path):
    """归一化成 KB 相对路径：反斜杠→正斜杠、去 ./ 前缀、去首尾空白。"""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def assert_publishable(path):
    """单路径判定 → (ok, reason)。

    优先级：**隐私黑名单 > 段黑名单 > 白名单**。
    先判黑名单是硬性设计：即使将来有人把隐私目录误加进白名单（枚举失误），
    隐私路径仍然被拒——红线不依赖白名单的正确性。
    """
    p = _norm(path)
    if not p:
        return False, "空路径"
    segs = p.split("/")
    # 1. 隐私黑名单（优先级最高）
    for d in BLACKLIST_DIRS:
        if p == d.rstrip("/") or p.startswith(d):
            return False, "🔴 隐私黑名单目录 %s：准则 L1，绝不上云" % d
    # 2. 段黑名单（本地派生/备份/缓存，任何层级）
    for seg in BLACKLIST_SEGMENTS:
        if seg in segs:
            return False, "🔴 本地派生段 %s：备份/索引库/缓存不上云" % seg
    # 3. 白名单（未枚举 = 不上云）
    for pre in CLOUD_PUBLISHABLE:
        if p.startswith(pre):
            return True, "白名单品类 %s" % pre
    return False, "不在可发布白名单：未枚举的品类默认不上云（宁缺毋滥）"


def filter_publishable(paths):
    """批量版 → (allowed, blocked)。

    allowed: [path, ...]  （原顺序保留）
    blocked: [(path, reason), ...]  （带原因，报告/日志要用）
    """
    allowed, blocked = [], []
    for p in paths:
        ok, reason = assert_publishable(p)
        (allowed if ok else blocked).append(p if ok else (p, reason))
    return allowed, blocked


# ---------------------------------------------------------------- 内容门禁（真调用 privacy_scan）
def find_privacy_scan():
    """定位 kb-from-zero 的 privacy_scan.py（真调用，不复制规则）。

    查找顺序：环境变量 KB_PRIVACY_SCAN（**显式设置则严格使用**，指向不存在的
    文件时返 None 而不是静默回退——显式配置被悄悄换掉是最难排查的一类故障，
    且回退方向是放行，违背「门禁故障=不放行」）→ 相对本模块的 kb-from-zero/tools/
    → 本机已知绝对路径。都找不到返 None（调用方按「门禁故障=不放行」处理）。
    """
    env = os.environ.get("KB_PRIVACY_SCAN")
    if env:
        return env if os.path.isfile(env) else None
    # 本模块位于仓库 modules/知识库索引/，privacy_scan 位于仓库根 tools/
    cands = [os.path.normpath(os.path.join(
        HERE, "..", "..", "tools", "privacy_scan.py"))]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def run_privacy_scan(root, terms=None, timeout=300):
    """对导出目录跑**真实的** privacy_scan.py（子进程，--json）。

    → (ok, detail)。ok=True 当且仅当 退出码 0（无 BLOCK）。
    🔴 门禁故障（扫描器找不到/超时/自身异常）一律 ok=False：
    放行必须证明干净，不能证明就不放行。
    """
    scanner = find_privacy_scan()
    if not scanner:
        return False, {"error": "privacy_scan.py 未找到（KB_PRIVACY_SCAN 可指定）",
                       "block": -1}
    cmd = [sys.executable, scanner, "--root", os.path.abspath(root), "--json"]
    if terms:
        cmd += ["--terms", terms]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": "privacy_scan 超时(%ds)" % timeout, "block": -1}
    except Exception as e:
        return False, {"error": "privacy_scan 调用异常: %s" % str(e)[:120], "block": -1}
    detail = {"rc": r.returncode}
    try:
        obj = json.loads(r.stdout)
        detail.update({"files_scanned": obj.get("files_scanned"),
                       "block": obj.get("block", 0),
                       "warn": obj.get("warn", 0),
                       "hits": obj.get("hits", [])})
    except ValueError:
        detail.update({"block": -1, "error": "privacy_scan 输出不是 JSON",
                       "stdout_tail": (r.stdout or "")[-200:]})
    if r.returncode not in (0, 1):     # 2 = 扫描器自身异常
        detail["stderr_tail"] = (r.stderr or "")[-300:]
    return r.returncode == 0 and detail.get("block") == 0, detail


if __name__ == "__main__":  # 手动抽查：python cloud_publishable.py <路径>...
    for arg in sys.argv[1:]:
        ok, why = assert_publishable(arg)
        print("%s %s — %s" % ("✅" if ok else "❌", arg, why))
    sys.exit(0)
