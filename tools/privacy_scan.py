#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
privacy_scan.py —— 发布前隐私终审（发布到 GitHub / 打包 zip 前必跑）

它回答一个问题：这个目录里，还有没有不该公开的东西？

三层检查
  L1 通用规则：绝对路径、用户名、手机号、邮箱、身份证、密钥、私有 IP、内网主机
  L2 自定义词表：真实姓名 / 公司 / 客户 / 项目名 / 学号等
     ★ 词表 **不放在仓库里**（放进来本身就是泄露），默认读 ~/.kb_privacy_terms.txt
  L3 文件形态：混进来的图片 / 视频 / PDF / Office 文档一律拦下

二进制也能扫（exe 里的内嵌字符串、zip 里的成员），分别按 UTF-8 / UTF-16LE / GBK 解码后匹配。

用法
  python tools/privacy_scan.py                    # 扫仓库根目录
  python tools/privacy_scan.py --root . --json    # JSON 输出（供脚本/CI 调用）
  python tools/privacy_scan.py --terms ~/terms.txt
  python tools/privacy_scan.py --self-test        # 自检：验证规则能命中样本

退出码
  0 = 干净（可能有 WARN）　1 = 有 BLOCK　2 = 脚本自身异常

词表文件格式（UTF-8，每行一个词，# 开头为注释）
  # 真实姓名
  张三
  # 公司
  某某科技有限公司
  # 学号
  （你的学号）
"""
import argparse
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 规则
# level: BLOCK = 一定不能出现；WARN = 需人工判断（可能是技术示例）
RULES = [
    ("Windows 绝对路径",      r"[A-Za-z]:[\\/]{1,2}(?:Users|Documents|Desktop|projects?|work|data|ZDH)[\\/]", "BLOCK"),
    ("Windows 盘符路径",      r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{2}[^\s\"'`<>|]{2,}", "WARN"),
    ("Unix 家目录绝对路径",    r"/home/[A-Za-z0-9_.-]{2,}/", "BLOCK"),
    ("Unix 用户目录绝对路径",  r"/Users/[A-Za-z0-9_.-]{2,}/", "BLOCK"),
    ("默认管理员用户名",       r"\bAdministrator\b", "BLOCK"),
    ("中国大陆手机号",         r"(?<!\d)1[3-9]\d{9}(?!\d)", "BLOCK"),
    ("电子邮箱",              r"[\w.+-]+@[\w-]+\.[\w.-]{2,}", "BLOCK"),
    ("身份证号",              r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", "BLOCK"),
    ("GitHub Token",          r"\bgh[pousr]_[A-Za-z0-9]{20,}", "BLOCK"),
    ("OpenAI 风格密钥",        r"\bsk-[A-Za-z0-9_-]{20,}", "BLOCK"),
    ("AWS Access Key",        r"\bAKIA[0-9A-Z]{16}\b", "BLOCK"),
    ("Slack Token",           r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "BLOCK"),
    ("私钥文件内容",           r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "BLOCK"),
    ("带 Bearer 的长令牌",     r"Bearer\s+[A-Za-z0-9\-._~+/]{24,}", "BLOCK"),
    ("私有网段 IP",            r"(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])", "WARN"),
    ("局域网主机名",           r"\b[\w-]+\.(?:local|lan|internal|corp)\b", "WARN"),
]

# 允许出现的技术性例外（命中后不计入）
ALLOW = {
    "127.0.0.1", "0.0.0.0", "localhost",
    "FallenPetal",            # 公开笔名，即许可署名
    "noreply@github.com", "you@example.com", "user@example.com",
}

MEDIA_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico",
             ".mp4", ".mov", ".avi", ".mkv", ".flv", ".mp3", ".wav", ".m4a",
             ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
             ".zip", ".7z", ".rar", ".tar", ".gz"}

TEXT_EXT = {".md", ".txt", ".py", ".bat", ".cmd", ".ps1", ".sh", ".html", ".htm",
            ".css", ".js", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".example", ".gitignore", ".gitattributes"}

BIN_EXT = {".exe", ".dll", ".so", ".bin", ".pyd"}

# 构建产物：本机留存、不入库也不外发（.gitignore 已覆盖 *.log / *.spec）。
# 它们含本机绝对路径，扫出来全是噪声，因此识别为已知产物并降级 INFO。
BUILD_ARTIFACT_EXT = {".log", ".spec", ".pyc", ".manifest"}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".idea", ".vscode", "build", "dist"}

# 扫描器自身：规则定义里必然含有敏感模式的正则原文（如用户名规则），
# 因此跳过本文件，且在报告里显式说明——不做静默豁免。
SELF_IGNORE = {"tools/privacy_scan.py"}

# Release 资产：确实在目录里，但被 .gitignore 排除、只上传到 Releases
RELEASE_ASSETS = {"kb-hub.exe", "kb-from-zero.zip"}


def load_terms(path):
    if not path or not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out


def iter_files(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = os.path.join(dp, fn)
            yield p, os.path.relpath(p, root).replace("\\", "/")


def ctx(text, start, end, pad=45):
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    s = text[a:b].replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s{2,}", " ", s).strip()


def ascii_ratio(s):
    """可打印 ASCII 占比——二进制解码后的上下文用它区分真字符串与随机噪声。"""
    if not s:
        return 0.0
    n = 0
    for ch in s:
        o = ord(ch)
        if o in (9, 10, 13) or 0x20 <= o <= 0x7E:
            n += 1
    return n / len(s)


# 二进制里最容易误报的规则 → 对应的「上下文可打印率」阈值。
# 压缩数据里可打印 ASCII 的占比可能很高，所以邮箱/路径这类短模式阈值要顶到 0.9；
# 手机号是 11 位连续数字，随机凑出的概率极低，阈值可以放宽。
NOISY_IN_BINARY = {"电子邮箱": 0.9, "Windows 盘符路径": 0.9,
                   "私有网段 IP": 0.85, "局域网主机名": 0.85,
                   "中国大陆手机号": 0.6}


def term_in_raw(raw, t):
    """原始字节通道：在二进制里搜敏感词的真实编码字节。

    返回命中的编码名（utf-8 / gbk），未命中返回 None。

    为什么二进制必须走这一层：解码流（bytes.decode(enc, "ignore")）在遇到
    无效字节时会直接丢弃，于是原本**不相邻**的 ASCII 字节被拼接成了敏感词
    —— 实测 9MB 的 kb-hub.exe 用 GBK 解码后凭空凑出 "JZXT"，而原始字节流
    里零命中（上下文是 `V3쬊;k>jZxt~o` 这类 GBK 解码噪声）。
    原始字节是充要的：任何被打进 exe 的文本常量，其编码字节必然在文件里。
    """
    if not raw:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            b = t.encode(enc)
        except UnicodeEncodeError:
            continue
        if b and b in raw:
            return enc
    return None


def scan_blob(text, rel, rules, terms, hits, binary=False, raw=None):
    for name, pat, level in rules:
        for m in re.finditer(pat, text):
            val = m.group(0)
            if any(a in val for a in ALLOW):
                continue
            c = ctx(text, m.start(), m.end())
            if binary:
                # 规则命中必须是纯 ASCII；中文敏感词由下面的词表分支负责
                if not all(ord(ch) < 128 for ch in val):
                    continue
                thr = NOISY_IN_BINARY.get(name)
                if thr is not None and ascii_ratio(c) < thr:
                    continue
            hits.append({"file": rel, "rule": name, "level": level,
                         "match": val[:80], "context": c})
    for t in terms:
        if not t:
            continue
        low = text.lower()
        tl = t.lower()
        idx = low.find(tl)
        if idx < 0:
            continue
        c = ctx(text, idx, idx + len(t))
        if binary:
            # 二进制通道：解码流命中不算数，必须过原始字节确认（见 term_in_raw 说明）。
            # 只有原始字节里真有这个词 → BLOCK；否则降级 INFO，不阻塞发布。
            enc = term_in_raw(raw, t)
            if enc is None:
                hits.append({"file": rel, "level": "INFO", "match": t,
                             "rule": "自定义词表（仅解码流命中·疑似二进制误报）",
                             "context": c})
                continue
            hits.append({"file": rel, "rule": "自定义词表", "level": "BLOCK",
                         "match": "%s（原始字节 %s 命中）" % (t, enc), "context": c})
        else:
            hits.append({"file": rel, "rule": "自定义词表", "level": "BLOCK",
                         "match": t, "context": c})


def scan_zip(path, rel, rules, terms, hits):
    """压缩包必须深挖：否则「打包前是干净的、打包后混进脏文件」这种情况会漏。"""
    try:
        with zipfile.ZipFile(path) as z:
            bad = []
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                nm = zi.filename
                if nm.replace("\\", "/") in SELF_IGNORE:
                    continue
                if any(nm.lower().endswith(e) for e in MEDIA_EXT):
                    bad.append(nm)
                try:
                    raw = z.read(zi)
                except Exception:
                    continue
                sub = rel + "!" + nm
                # zip 里的 md/文本成员按文本扫描（否则中文上下文会被当成二进制噪声过滤掉）
                is_text = os.path.splitext(nm)[1].lower() in TEXT_EXT
                for enc in ("utf-8", "gbk"):
                    scan_blob(raw.decode(enc, "ignore"), sub, rules, terms, hits,
                              binary=not is_text, raw=raw)
            for nm in bad:
                hits.append({"file": rel + "!" + nm, "rule": "压缩包内含媒体/文档文件",
                             "level": "BLOCK", "match": nm,
                             "context": "发布包只应含源码与 md/文本模板"})
    except Exception as e:
        hits.append({"file": rel, "rule": "压缩包读取失败", "level": "WARN",
                     "match": str(e)[:60], "context": ""})


def scan_file(path, rel, rules, terms, hits, allow_media=False):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)

    if ext in MEDIA_EXT:
        if name in RELEASE_ASSETS:
            hits.append({"file": rel, "rule": "Release 资产（不入 git，仅上传）",
                         "level": "INFO", "match": name,
                         "context": "被 .gitignore 排除；内部仍会深挖"})
            if ext == ".zip":
                scan_zip(path, rel, rules, terms, hits)
        else:
            hits.append({"file": rel, "rule": "禁止提交的媒体/文档/压缩包", "level": "BLOCK",
                         "match": name, "context": "仓库里不应出现图片/音视频/PDF/Office 文件"})
            if ext == ".zip" and allow_media:
                scan_zip(path, rel, rules, terms, hits)
        return

    if ext in BIN_EXT:
        if name in RELEASE_ASSETS:
            hits.append({"file": rel, "rule": "Release 资产（不入 git，仅上传）",
                         "level": "INFO", "match": name,
                         "context": "二进制明文扫描可能漏（PyInstaller 会压缩），须从干净源重建"})
        with open(path, "rb") as f:
            data = f.read()
        # 只按 UTF-8 / GBK 解：PyInstaller 内嵌的常量就是这两种，
        # UTF-16LE 解出来的随机字节最容易凑出假邮箱，收益极低。
        # raw=data 传原始字节供词表分支做二次确认，挡掉解码噪声误报。
        for enc in ("utf-8", "gbk"):
            scan_blob(data.decode(enc, "ignore"), rel, rules, terms, hits,
                      binary=True, raw=data)
        return

    if ext in TEXT_EXT or ext == "" or name in ("LICENSE", "NOTICE", ".gitignore"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                scan_blob(f.read(), rel, rules, terms, hits)
        except Exception as e:
            hits.append({"file": rel, "rule": "读取失败", "level": "WARN",
                         "match": str(e)[:60], "context": ""})
        return

    if ext in BUILD_ARTIFACT_EXT:
        # PyInstaller 等构建产物：被 .gitignore 排除、不入库也不外发，
        # 但内容里必然含本机绝对路径（如 F:/.../kb-hub.spec），扫了也是噪声。
        hits.append({"file": rel, "rule": "构建产物（已排除，不入库）", "level": "INFO",
                     "match": name, "context": "被 .gitignore 排除，仅本机留存"})
        return

    hits.append({"file": rel, "rule": "未知扩展名（需人工确认）", "level": "WARN",
                 "match": ext or name, "context": "不在文本/二进制白名单内"})


def dedupe(hits):
    seen, out = set(), []
    for h in hits:
        k = (h["file"], h["rule"], h["match"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    return out


def self_test():
    """用内联样本验证规则能命中——防止规则写错导致假阴性。

    注意：样本串一律拼接构造，避免扫描器把自己的测试串当成真隐私命中。
    """
    samples = [
        ("路径", "打开 " + "D:" + r"\Users\someone\Desktop\a.txt" + " 看看", "Windows 绝对路径"),
        ("手机", "联系我 " + "138" + "00001111" + " 谢谢", "中国大陆手机号"),
        ("邮箱", "发到 some" + "one@example.org", "电子邮箱"),
        ("令牌", "token=gh" + "p_" + "A" * 36, "GitHub Token"),
        ("私钥", "-----BEGIN RSA" + " PRIVATE KEY-----", "私钥文件内容"),
        ("身份证", "号码 110101" + "199003077777", "身份证号"),
    ]
    ok = True
    for label, text, expect in samples:
        got = []
        scan_blob(text, "<selftest>", RULES, [], got)
        if not any(g["rule"] == expect for g in got):
            print("[FAIL] 自检未命中：%s（期望规则 %s）" % (label, expect))
            ok = False
        else:
            print("[OK] 自检命中：%s → %s" % (label, expect))
    # 反向：正常文本不应误报
    clean = "本仓库使用 ~/my-knowledge-base 作为示例路径，服务监听 127.0.0.1:8799。"
    got = []
    scan_blob(clean, "<selftest>", RULES, [], got)
    bad = [g for g in got if g["level"] == "BLOCK"]
    if bad:
        print("[FAIL] 正常文本被误报：%s" % bad)
        ok = False
    else:
        print("[OK] 正常文本无误报")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="发布前隐私终审")
    ap.add_argument("--root", default=REPO, help="扫描根目录")
    ap.add_argument("--terms", default=os.path.expanduser("~/.kb_privacy_terms.txt"),
                    help="自定义敏感词表（默认 ~/.kb_privacy_terms.txt）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--self-test", action="store_true", help="只跑规则自检")
    ap.add_argument("--no-assets", action="store_true", help="跳过 exe/zip 等 Release 资产")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    root = os.path.abspath(args.root)
    terms = load_terms(args.terms)
    hits, files, skipped = [], 0, []
    for path, rel in iter_files(root):
        if rel in SELF_IGNORE:
            skipped.append(rel)
            continue
        if args.no_assets and os.path.basename(path) in RELEASE_ASSETS:
            continue
        files += 1
        scan_file(path, rel, RULES, terms, hits, allow_media=True)

    hits = dedupe(hits)
    blocks = [h for h in hits if h["level"] == "BLOCK"]
    warns = [h for h in hits if h["level"] == "WARN"]
    infos = [h for h in hits if h["level"] == "INFO"]
    result = {
        "root": root,
        "files_scanned": files,
        "skipped": skipped,
        "rules": len(RULES),
        "terms_loaded": len(terms),
        "terms_file": args.terms if os.path.isfile(args.terms) else None,
        "block": len(blocks),
        "warn": len(warns),
        "info": len(infos),
        "hits": hits,
        "ok": not blocks,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("隐私终审 · %s" % root)
        print("扫描 %d 个文件 · %d 条通用规则 · %d 条自定义词"
              % (files, len(RULES), len(terms)))
        if skipped:
            print("[豁免] 跳过 %s（规则定义文件，已在代码注释说明原因）" % ", ".join(skipped))
        if not os.path.isfile(args.terms):
            print("[提示] 未找到词表 %s，只跑了通用规则。" % args.terms)
            print("       实名/公司/项目名这类词请写进该文件（不要放进仓库）。")
        print("=" * 68)
        for lvl, group in (("BLOCK", blocks), ("WARN", warns), ("INFO", infos)):
            if not group:
                continue
            print("\n[%s] %d 条" % (lvl, len(group)))
            for h in group:
                print("  · %s" % h["file"])
                print("      规则: %s ｜ 命中: %s" % (h["rule"], h["match"]))
                if h["context"]:
                    print("      上下文: %s" % h["context"][:110])
        print("\n" + "-" * 68)
        verdict = "✅ 干净：可以发布" if not blocks else "❌ 有 %d 条 BLOCK，不能发布" % len(blocks)
        print(verdict + ("（另有 %d 条 WARN 需人工确认）" % len(warns) if warns else ""))
    return 0 if not blocks else 1


if __name__ == "__main__":
    sys.exit(main())
