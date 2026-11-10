#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release_gate — kb-from-zero 版本管理上云闸门（默认全离线 · 永不 push）

设计总稿：docs/decisions/ADR-2026-11-10-版本管理上云.md
规范全文：CONTRIBUTING.md

三个动作：
  --check       版本一致性体检：VERSION == 根 CHANGELOG 顶段 == 最新 tag ==
                RELEASE.md == RELEASE.en.md == publish_to_github.bat 的 TAG
  --bump        生成新版本（默认 dry-run，显式 --apply 才写盘；--tag 才打本地 tag）
  --preflight   上云预检：隐私 0 BLOCK + 待推文件数/体积/提交数 + 打印待执行命令

🔴 本脚本永不执行 push / 永不建远程 tag / 永不发 Release / 永不联网。
   它只做门禁与打印；对外动作由库主手工运行 publish_to_github.bat 或显式 git push 授权。

退出码：0 = 全过；1 = 有 FAIL；2 = 参数/文件缺失。
"""
import argparse
import datetime as _dt
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

VERSION_F = os.path.join(ROOT, "VERSION")
CHANGELOG_F = os.path.join(ROOT, "CHANGELOG.md")
RELEASE_ZH = os.path.join(ROOT, "RELEASE.md")
RELEASE_EN = os.path.join(ROOT, "RELEASE.en.md")
BAT_F = os.path.join(ROOT, "publish_to_github.bat")
PRIVACY_F = os.path.join(ROOT, "tools", "privacy_scan.py")

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEAD_RE = re.compile(r"^##\s*\[v?(\d+\.\d+\.\d+)\]", re.M)
TAG_LINE_RE = re.compile(r'(set\s+"TAG=)v?\d+\.\d+\.\d+(")')
REL_ZH_RE = re.compile(r"(当前版本：\*\*v?)\d+\.\d+\.\d+(\*\*)")
REL_EN_RE = re.compile(r"(Current version: \*\*v?)\d+\.\d+\.\d+(\*\*)")


def log(msg=""):
    print(msg)


def _git(*args):
    """git 命令；返回 (rc, stdout)。失败不静默，由调用方决定怎么处理。"""
    try:
        r = subprocess.run(["git", "-C", ROOT] + list(args),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, "[ERR] git 调用失败：%s" % e


def _read(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _norm(v):
    v = (v or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    return v.strip()


def _rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def bump_ver(ver, part):
    a, b, c = [int(x) for x in ver.split(".")]
    if part == "major":
        return "%d.0.0" % (a + 1)
    if part == "minor":
        return "%d.%d.0" % (a, b + 1)
    return "%d.%d.%d" % (a, b, c + 1)


def changelog_section(text, version):
    """抽出某版本段正文（与 make_release_notes.py 同口径，忽略 Unreleased）。"""
    v = _norm(version)
    m = re.search(r"^##\s*\[v?%s\]" % re.escape(v), text, re.M)
    if not m:
        return ""
    body = []
    for line in text[m.end():].splitlines():
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


# ────────────────────────── --check ──────────────────────────
def collect_sources(args):
    """六处版本来源，逐项带取证路径。"""
    out = []
    raw = _read(VERSION_F)
    out.append(("VERSION", _norm(raw) if raw else None, _rel(VERSION_F), "缺文件" if raw is None else ""))

    cl = _read(CHANGELOG_F)
    m = HEAD_RE.search(cl or "")
    out.append(("CHANGELOG", m.group(1) if m else None, _rel(CHANGELOG_F), "找不到带版本号的 ## 段" if not m else ""))

    tag = args.tag
    if tag:
        out.append(("TAG", _norm(tag), "--tag 传入", ""))
    elif args.skip_tag:
        out.append(("TAG", None, "--skip-tag", "跳过（CI/PR 用）"))
    else:
        rc, s = _git("tag", "-l", "--sort=-v:refname")
        first = s.splitlines()[0] if s else ""
        out.append(("TAG", _norm(first) if first else None, "git tag -l --sort=-v:refname",
                    "git rc=%d" % rc if rc else ("无 tag" if not first else "")))

    zh = _read(RELEASE_ZH)
    mz = REL_ZH_RE.search(zh or "")
    zh_ver = _norm(re.search(r"v\d+\.\d+\.\d+", mz.group(0)).group(0)) if mz else None
    out.append(("RELEASE.md", zh_ver, _rel(RELEASE_ZH),
                "头部无「当前版本：**vX.Y.Z**」" if not mz else ""))

    en = _read(RELEASE_EN)
    me = REL_EN_RE.search(en or "")
    en_ver = _norm(re.search(r"v\d+\.\d+\.\d+", me.group(0)).group(0)) if me else None
    out.append(("RELEASE.en.md", en_ver, _rel(RELEASE_EN),
                "头部无「Current version: **vX.Y.Z**」" if not me else ""))

    # bat 的 TAG 有两种合法形态：① 动态从 VERSION 读（推荐，不会烂掉）；
    # ② 旧版写死 set "TAG=vX.Y.Z"（仍兼容，但会提示改造）。
    bat = _read(BAT_F) or ""
    dyn = re.search(r"for\s+/f[^\n]*VERSION", bat, re.I)
    mb = re.search(r'set\s+"TAG=(v?\d+\.\d+\.\d+)"', bat)
    if dyn:
        out.append(("publish_to_github.bat", _norm(_read(VERSION_F)),
                    "动态读 VERSION（无写死值）", ""))
    elif mb:
        out.append(("publish_to_github.bat", _norm(mb.group(1)), _rel(BAT_F),
                    "写死值，建议改为动态读"))
    else:
        out.append(("publish_to_github.bat", None, _rel(BAT_F),
                    "既无动态读取也无 TAG 行"))
    return out


def cmd_check(args):
    log("=== release_gate --check（版本一致性）===")
    sources = collect_sources(args)
    for key, val, src, note in sources:
        log("  %-22s = %-10s  <- %s%s" % (key, val if val else "(跳过/缺失)", src,
                                          ("  [%s]" % note) if note else ""))
    base = sources[0][1]
    fails = []
    if not base or not SEMVER_RE.match(base):
        fails.append("VERSION 不是合法 SemVer：%r" % base)
    for key, val, src, note in sources[1:]:
        if val is None:
            if key != "TAG" or not args.skip_tag:
                fails.append("%s 取不到版本（%s）" % (key, src))
            continue
        if base and val != base:
            fails.append("%s=%s 与 VERSION=%s 不一致" % (key, val, base))

    cl = _read(CHANGELOG_F) or ""
    ver_for_notes = _norm(args.tag) if args.tag else base
    if ver_for_notes:
        body = changelog_section(cl, ver_for_notes)
        if not body:
            fails.append("根 CHANGELOG 里找不到 v%s 的段落（或段落为空）——release.yml 会拒发" % ver_for_notes)
        else:
            log("  %-22s = %d 行正文  <- %s（Release 正文可抽取）"
                % ("CHANGELOG[v%s]" % ver_for_notes, len(body.splitlines()), _rel(CHANGELOG_F)))

    log("  %-22s = 0  <- 本脚本不执行任何 push" % "PUSH_EXECUTED")
    if fails:
        for f in fails:
            log("  [FAIL] " + f)
        log("CHECK=FAIL  rc=1")
        return 1
    log("CHECK=PASS  rc=0")
    return 0


# ────────────────────────── --bump ──────────────────────────
ENTRY_TMPL = """## [v{ver}] - {today}

### 新增
- {note}

### 修改
- （填写改动了什么）

### 修复
- （无）

### 决策理由
- 🔴 （必填：为什么这么做，而不是别的方案；写不出来就别发版）

### 已知问题
- （选填；未做项诚实标注，藏起来才叫风险）

### 对应提交
- （本轮 commit 后回填）

---

"""


def cmd_bump(args):
    raw = _read(VERSION_F)
    cur = _norm(raw) if raw else None
    if not cur or not SEMVER_RE.match(cur):
        log("[ERR] VERSION 缺失或非法：%r" % cur)
        return 2
    new = bump_ver(cur, args.bump)
    today = _dt.date.today().isoformat()
    entry = ENTRY_TMPL.format(ver=new, today=today,
                              note=args.note or "（一句话说明本次更新）")
    log("=== release_gate --bump %s ===" % args.bump)
    log("  v%s -> v%s  note=%s" % (cur, new, args.note))
    log("  MODE = %s" % ("APPLY（真写盘）" if args.apply else "DRY-RUN（只打印，不写盘）"))
    log("  将更新：VERSION / CHANGELOG.md / RELEASE.md / RELEASE.en.md / publish_to_github.bat")
    log("  将插入 CHANGELOG 的新段首行：%s" % entry.splitlines()[0])
    if args.tag:
        log("  将打【本地】tag v%s（不推远程）" % new)
    if not args.apply:
        log("  [dry-run] 未写入任何文件；确认后加 --apply")
        log("  PUSH_EXECUTED=0")
        return 0

    # 1) VERSION
    with open(VERSION_F, "w", encoding="utf-8") as f:
        f.write(new + "\n")
    # 2) CHANGELOG：插在第一个带版本号的 ## 段之前（Unreleased 保留，不自动删内容）
    cl = _read(CHANGELOG_F) or ""
    m = HEAD_RE.search(cl)
    cl_new = (cl[:m.start()] + entry + cl[m.start():]) if m else (cl.rstrip() + "\n\n" + entry)
    with open(CHANGELOG_F, "w", encoding="utf-8") as f:
        f.write(cl_new)
    # 3) 中英 RELEASE 头部
    for path, rx in ((RELEASE_ZH, REL_ZH_RE), (RELEASE_EN, REL_EN_RE)):
        txt = _read(path)
        if txt and rx.search(txt):
            with open(path, "w", encoding="utf-8") as f:
                f.write(rx.sub(lambda mm: mm.group(1) + new + mm.group(2), txt, count=1))
        else:
            log("  [WARN] %s 头部版本未匹配，未改动（请手工同步）" % _rel(path))
    # 4) bat 的 TAG：动态读 VERSION 的写法无需改动（TAG 自动跟随）
    bat = _read(BAT_F) or ""
    if re.search(r"for\s+/f[^\n]*VERSION", bat, re.I):
        log("  %s 为动态读 VERSION，TAG 自动跟随，无需改动" % _rel(BAT_F))
    elif TAG_LINE_RE.search(bat):
        with open(BAT_F, "w", encoding="utf-8") as f:
            f.write(TAG_LINE_RE.sub(lambda mm: mm.group(1) + "v" + new + mm.group(2), bat, count=1))
    else:
        log("  [WARN] %s 未找到 TAG 行，未改动" % _rel(BAT_F))
    # 5) 本地 tag（可选，默认不打）
    if args.tag:
        rc, s = _git("tag", "-a", "v" + new, "-m", args.note or ("release v" + new))
        log("  git tag rc=%d %s" % (rc, s))
    log("  ✅ 已写入；下一步：补全 CHANGELOG 新段的「修改/修复/决策理由」，再跑 --check")
    log("  PUSH_EXECUTED=0（push 需库主单独授权）")
    return 0


# ────────────────────────── --preflight ──────────────────────────
def cmd_preflight(args):
    log("=== release_gate --preflight（上云预检 · 只打印不执行）===")
    fails = []

    # 1) 版本一致性
    rc = cmd_check(argparse.Namespace(tag=None, skip_tag=args.skip_tag))
    if rc != 0:
        fails.append("--check 未过（版本一致性）")

    # 2) 隐私出口门禁（词表在库外，CI 里可能没有 → 只提示不 fail）
    terms = os.path.expanduser("~/.kb_privacy_terms.txt")
    log("  %-22s = %s" % ("PRIVACY_TERMS", "就位" if os.path.isfile(terms) else "未找到（只跑通用规则）"))
    if os.path.isfile(PRIVACY_F):
        r = subprocess.run([sys.executable, PRIVACY_F, "--root", ROOT, "--git-only", "--no-assets"],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        log("  %-22s = rc=%d" % ("PRIVACY_SCAN", r.returncode))
        tail = (r.stdout or "").strip().splitlines()[-4:]
        for ln in tail:
            log("      " + ln)
        if r.returncode != 0:
            fails.append("privacy_scan --git-only 非 0 BLOCK，禁止发布")
    else:
        fails.append("找不到 tools/privacy_scan.py")

    # 3) 待推体积（文件数也是成本，不能只算字节）
    rc, s = _git("-c", "core.quotepath=false", "ls-files")
    files = s.splitlines() if s else []
    total = 0
    for rel in files:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            total += os.path.getsize(p)
    log("  %-22s = %d 个文件 / %.2f MB（git 已跟踪）" % ("TRACKED", len(files), total / 1048576.0))

    # 4) 未提交变更 / 待推提交
    rc, st = _git("status", "--short")
    log("  %-22s = %d 处未提交变更%s" % ("UNCOMMITTED", len(st.splitlines()) if st else 0,
                                     "" if rc == 0 else "  [git rc=%d]" % rc))
    rc, pending = _git("log", "--oneline", "origin/master..HEAD")
    if rc == 0:
        log("  %-22s = %d 个待推提交" % ("UNPUSHED_COMMITS", len(pending.splitlines()) if pending else 0))
    else:
        log("  %-22s = 无法比对（远程未拉取或不存在）" % "UNPUSHED_COMMITS")

    # 5) 只打印待执行命令
    ver = _norm(_read(VERSION_F) or "")
    log("  --- 待执行命令（本脚本不执行）---")
    log("      git push origin master")
    log("      git push origin v%s   # 推 tag 会触发 release.yml 建 Release" % ver)
    log("  %-22s = 0" % "PUSH_EXECUTED")
    if fails:
        for f in fails:
            log("  [FAIL] " + f)
        log("PREFLIGHT=FAIL  rc=1")
        return 1
    log("PREFLIGHT=PASS  rc=0（仍不代表已授权发布）")
    return 0


# ────────────────────────── --self-test ──────────────────────────
def cmd_selftest():
    """临时沙盒里真跑一次 --bump --apply，证明写盘逻辑可用；目录自动清理，不碰仓库本体。"""
    import shutil
    import tempfile
    global ROOT, VERSION_F, CHANGELOG_F, RELEASE_ZH, RELEASE_EN, BAT_F
    saved = (ROOT, VERSION_F, CHANGELOG_F, RELEASE_ZH, RELEASE_EN, BAT_F)
    log("=== release_gate --self-test（临时沙盒，自动清理）===")
    try:
        with tempfile.TemporaryDirectory(prefix="release_gate_selftest_") as td:
            for p in (VERSION_F, CHANGELOG_F, RELEASE_ZH, RELEASE_EN, BAT_F):
                shutil.copy2(p, os.path.join(td, os.path.basename(p)))
            ROOT = td
            VERSION_F = os.path.join(td, "VERSION")
            CHANGELOG_F = os.path.join(td, "CHANGELOG.md")
            RELEASE_ZH = os.path.join(td, "RELEASE.md")
            RELEASE_EN = os.path.join(td, "RELEASE.en.md")
            BAT_F = os.path.join(td, "publish_to_github.bat")

            ns = argparse.Namespace(tag=None, skip_tag=True)
            before = cmd_check(ns)
            log("  before --check rc=%d（预期 0）" % before)
            rc_bump = cmd_bump(argparse.Namespace(bump="patch", note="selftest",
                                                  apply=True, tag=False))
            log("  bump --apply rc=%d（预期 0）" % rc_bump)
            after = cmd_check(ns)
            log("  after  --check rc=%d（预期 0）" % after)

            ver = (_read(VERSION_F) or "").strip()
            cl = _read(CHANGELOG_F) or ""
            zh = _read(RELEASE_ZH) or ""
            en = _read(RELEASE_EN) or ""
            checks = [
                ("VERSION 递增为 1.17.2", ver == "1.17.2"),
                ("CHANGELOG 有新版本段", "## [v1.17.2]" in cl),
                ("RELEASE.md 头部同步", bool(re.search(r"当前版本：\*\*v1\.17\.2\*\*", zh))),
                ("RELEASE.en.md 头部同步", bool(re.search(r"Current version: \*\*v1\.17\.2\*\*", en))),
                ("bat 仍为动态读 VERSION", bool(re.search(r"for\s+/f[^\n]*VERSION", _read(BAT_F) or "", re.I))),
            ]
            bad = [n for n, okv in checks if not okv]
            for n, okv in checks:
                log("  %s %s" % ("[ok] " if okv else "[FAIL]", n))
            ok = (before == 0 and rc_bump == 0 and after == 0 and not bad)
            log("  SELFTEST=%s  rc=%d" % ("PASS" if ok else "FAIL", 0 if ok else 1))
            return 0 if ok else 1
    finally:
        ROOT, VERSION_F, CHANGELOG_F, RELEASE_ZH, RELEASE_EN, BAT_F = saved


def main():
    ap = argparse.ArgumentParser(description="kb-from-zero 版本管理上云闸门（永不 push）")
    ap.add_argument("--check", action="store_true", help="版本一致性体检")
    ap.add_argument("--bump", choices=["major", "minor", "patch"], help="生成新版本")
    ap.add_argument("--preflight", action="store_true", help="上云预检（隐私 + 体积 + 待推清单）")
    ap.add_argument("--note", default="", help="--bump 用的一句话说明")
    ap.add_argument("--apply", action="store_true", help="--bump 真写盘（默认 dry-run）")
    ap.add_argument("--tag", metavar="V", default=None,
                    help="--check 时指定要比对的 tag（如 v1.17.1），不给则取最新 tag")
    ap.add_argument("--local-tag", action="store_true", help="--bump 时打本地 tag（不推远程）")
    ap.add_argument("--skip-tag", action="store_true", help="--check/--preflight 跳过 tag 比对（CI/PR 用）")
    ap.add_argument("--self-test", action="store_true", help="临时沙盒里真跑一次 --bump --apply 自测")
    a = ap.parse_args()

    if a.self_test:
        return cmd_selftest()
    if a.bump:
        return cmd_bump(argparse.Namespace(bump=a.bump, note=a.note,
                                           apply=a.apply, tag=a.local_tag))
    if a.preflight:
        return cmd_preflight(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
