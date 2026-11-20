#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""egress_policy — 默认检索出口的敏感路径策略（default-deny + 可审计 allowlist）
================================================================================

回答一个问题：**这条命中，在「默认 egress」出口能不能带正文出去？**

本模块**只管默认检索出口**（`kb_rag._enrich` / `build_context` 的 egress 分支）。
它刻意**不改** `kb_index.PRIVACY_PATH_PREFIXES` —— 那个常量是三用共享真相源
（① 元数据回填「连 mtime 都不 stat」② `ingest_raw.XLS_DENY_PATH_PREFIXES` 摄取拒绝
③ 检索出口脱敏），把 `wiki/个人信息/` 塞进去会连带改掉 ①② 两层语义，属于误伤。

设计依据（2026-11-13 联网调研，URL 见 wiki/技术/2026-11-13-出口敏感策略-业界做法落地.md）：
  * Presidio 官方 Warning：「there is no guarantee that Presidio will find all
    sensitive information. Consequently, additional systems and protections
    should be employed.」→ **不能只靠内容级检测当放行开关**，路径级 default-deny
    才是主判据；内容检测只用来出 trace / 分类理由。
  * gitleaks 官方 README：`[extend] useDefault = true`（扩展而非另起炉灶，否则
    "default rules do not apply"）、`[[rules.allowlists]]` 带 `description` 理由、
    `condition = "AND"` 组合多个条件、`--redact` 日志里不打印真值
    → 本模块是**追加**一层，既有四道门禁一条不动；allowlist 必须带理由与日期。
  * On-prem RAG 边界共识（AWS / OWASP 转述）：授权与脱敏画在「上下文组装之前」，
    身份或元数据缺失时 default-deny；检索层召回不过滤，砍召回等于砍功能。
  * 2026-11-12 库主裁定：隐私「只是对外、对云」，本地自用走显式 `--scope local`。

🔴 三条硬边界：
  1. 本模块只影响 **默认 egress**；`scope=local` / `local_query.py` 一律放行正文，
     用户「本地问自己的事」体感不许退化。
  2. `is_egress_sensitive()` 是**纯函数**，不读文件、不连库、不打日志、不返回值。
  3. `scan_hints()` 只返回「命中类别」字符串，🔴 绝不返回/打印任何原值，
     供离线审计报告用；**不作为放行条件**（Presidio 不保证 recall）。
"""
import re

# ---------------------------------------------------------------- 主判据：路径级 default-deny
# 2026-11-13 库主授权「按你的建议来做」后纳入。取证：wiki/个人信息/ 共 9 个文本文件，
# 受控扫描（剔除 URL 后 5 类形状，只报类别不报值）发现 2 页含 REAL_SHAPED 命中：
#   基础档案.md → 手机号 1 + 邮箱 1（非示例域 / 非占位本地部 / 非示例号段）
#   密钥与令牌管理.md → 邮箱 1（非示例域；上下文含示例标记，仍按疑似真值保守处理）
# 前缀匹配、含子目录；新增文件默认受保护，不依赖「检测有没有查到」。
SENSITIVE_PREFIXES = (
    "wiki/个人信息/",
)

# ---------------------------------------------------------------- 例外：最窄 allowlist，初始为空
# gitleaks `.gitleaksignore` 思路：只对**已取证的、逐条的** finding 加例外，
# 每条必须写 path / reason / added / by 四字段，且需库主书面许可（L2 过目）。
# 🔴 初始为空 = default-deny 全目录；「0 命中」不构成安全证明（Presidio 不保证查全），
# 所以不预先给 7 个无命中页开例外。日后确需对外可读某页，逐页取证后在此追加。
ALLOWLIST = (
    # 示例（保持注释状态，勿直接取消注释）：
    # {"path": "wiki/个人信息/助手-操作指南.md",
    #  "reason": "2026-11-13 受控扫描 5 类 PII 形状 0 命中，且全文为操作说明",
    #  "added": "2026-11-13", "by": "<库主签字后填写>"},
)

# ---------------------------------------------------------------- trace：内容级「只报类别」
# ⚠️ 只用于离线审计与分类理由，🔴 不参与 is_egress_sensitive() 的放行判定。
# 与 modules/安全护栏/privacy_tiers.py 的 PII 形状保持同族（身份证/手机/邮箱/长数字/凭据），
# 但刻意独立定义：本模块不能因跨目录 import 失败而变成「默认放行」。
HINT_PATTERNS = (
    ("身份证18位", re.compile(r"\b\d{17}[\dXx]\b")),
    ("手机号11位", re.compile(r"\b1[3-9]\d{9}\b")),
    ("邮箱",       re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("长数字>=15", re.compile(r"\b\d{15,}\b")),
    ("凭据形状",   re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_|\bAKIA[0-9A-Z]{16}\b"
                             r"|\bsk-[A-Za-z0-9]{20,}\b")),
)
URL_STRIP = re.compile(r"https?://\S+|www\.\S+")   # 防 URL 里长数字误判证件号


def _norm(path):
    """归一化成 KB 相对路径：反斜杠→正斜杠、去 ./ 前缀、去首尾空白。"""
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def find_allowlist_entry(path):
    """命中则返回该条 allowlist（dict），否则 None。精确路径匹配，不做前缀通配。"""
    p = _norm(path)
    for e in ALLOWLIST:
        if _norm(e.get("path")) == p:
            return e
    return None


def is_egress_sensitive(path):
    """默认检索出口是否需要脱敏 → bool。纯函数、零 IO、零副作用。

    🔴 这只是「新增的一层」：调用方仍需 `or` 上 `kb_rag.is_privacy_path()`
    （gitleaks `[extend] useDefault`：扩展默认规则，不覆盖、不另起炉灶）。
    """
    p = _norm(path)
    if not p:
        return False
    if find_allowlist_entry(p) is not None:
        return False
    return any(p.startswith(x) for x in SENSITIVE_PREFIXES)


def scan_hints(text):
    """离线审计用：返回 [(类别, 命中次数), ...]，🔴 不返回任何原值。

    调用方打印/落盘时应只写类别与数量，不得把命中串写进日志（gitleaks `--redact`）。
    """
    clean = URL_STRIP.sub(" ", text or "")
    return [(name, len(rx.findall(clean))) for name, rx in HINT_PATTERNS
            if rx.search(clean)]


def classify(path, text=None):
    """给审计报告用的结构化判据（不含任何 PII 值）。

    → {"sensitive", "by", "reason", "pii_hint_types", "tier"}
      by: "prefix" | "allowlist" | "none"
      tier: 沿用四级分级的 P1_SELF（本人敏感：本地开放、永不上云）
    """
    p = _norm(path)
    e = find_allowlist_entry(p)
    if e is not None:
        return {"sensitive": False, "by": "allowlist",
                "reason": "逐条取证例外：%s（%s）" % (e.get("reason", ""), e.get("added", "")),
                "pii_hint_types": [], "tier": "P1_SELF"}
    hit = next((x for x in SENSITIVE_PREFIXES if p.startswith(x)), None)
    hints = scan_hints(text)[:] if text else []
    if hit:
        return {"sensitive": True, "by": "prefix",
                "reason": "路径级 default-deny：%s（不依赖内容检测 recall）" % hit,
                "pii_hint_types": [k for k, _ in hints], "tier": "P1_SELF"}
    return {"sensitive": False, "by": "none", "reason": "不在敏感前缀内",
            "pii_hint_types": [k for k, _ in hints], "tier": "P3_GENERAL"}


def verify():
    """自检：配置结构合法 + 关键路径判定符合预期。返回 (ok, [问题...])。"""
    bad = []
    for e in ALLOWLIST:
        if not all(k in e for k in ("path", "reason", "added", "by")):
            bad.append("allowlist 条目缺四字段：%s" % e.get("path", "<无 path>"))
        if not e.get("reason") or not e.get("added"):
            bad.append("allowlist 条目缺理由/日期：%s" % e.get("path"))
    cases = [
        ("wiki/个人信息/基础档案.md", True),
        ("wiki/个人信息/简历/README.md", True),
        ("wiki/个人信息/面相手相/2026-10-15-面相手相解读.md", True),
        ("wiki/技术/随便一篇.md", False),
        ("wiki/资料目录/2026-11-12-E盘存量资料目录-第二批次.md", False),
        ("raw/个人信息/xxx.md", False),      # 由既有 is_privacy_path 负责，本层不重复
        ("", False),
    ]
    for p, want in cases:
        got = is_egress_sensitive(p)
        if got != want:
            bad.append("判定不符 %s want=%s got=%s" % (p, want, got))
    return (not bad), bad


if __name__ == "__main__":
    import sys
    ok, problems = verify()
    print("egress_policy 自检：%s" % ("PASS" if ok else "FAIL"))
    for x in problems:
        print("  ❌", x)
    print("敏感前缀：%s｜allowlist 条目：%d" % (", ".join(SENSITIVE_PREFIXES), len(ALLOWLIST)))
    sys.exit(0 if ok else 1)
