#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
traceability — 引用可追溯检查器
================================

回答一个朴素问题：**这句话从哪来，能不能查回去？**

两件事分开查，因为出错方式完全不同：

  A. 网上来源 —— 必须有「来源标注」（Sources/来源 + 日期），否则外来知识落地后就成了无根之木
  B. 库内来源 —— 引用的路径必须真实存在（不存在的路径 = 引用了不存在的证据）

指标（全部可算、可回归）：
  · 外链溯源覆盖率 = 含外部链接的文章中，已标注来源的比例
  · 来源字段含日期率 = 有来源标注的文章中，带明确日期的比例（日期决定「该结论是哪天的事实」）
  · 库内引用可解析率 = 相对链接能落到真实文件的比例
  · Raw 原料存在率  = Raw / Raw files 字段指向的文件真实存在的比例

用法：
  python traceability.py                     # 扫描 wiki/，出报告 state/traceability_report.md
  python traceability.py --json              # 只出 JSON，给脚本用
  python traceability.py --fix-hint          # 额外打印每条缺口的补法
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.dirname(os.path.dirname(HERE))
WIKI_ROOT = os.path.join(KB_ROOT, "wiki")
REPORT = os.path.join(HERE, "state", "traceability_report.md")
JSON_OUT = os.path.join(HERE, "state", "traceability_latest.json")

URL_RE = re.compile(r"(https?://[^\s\)\]\"'>）】]+)")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
SOURCE_RE = re.compile(r"^\s*>\s*(Sources|来源|出处)\s*[:：]\s*(.+)$", re.M | re.I)
RAW_RE = re.compile(r"^\s*[-*>]?\s*(Raw\s*files?|Raw|原料)\s*[:：]\s*(.+)$", re.M | re.I)
DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def parse_frontmatter(text):
    """取出 YAML frontmatter 原文。库里有一批文章用 `sources:` + `collected:` 记来源，
    这也是合法标注，不能因为不是 `> Sources:` 行就判它没来源 —— 那是工具的问题，不是文档的问题。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*$", text, re.S | re.M)
    return m.group(1) if m else ""


def scan():
    """返回每篇文章的溯源事实。不做判断，只采集。"""
    rows, skipped = [], []
    for dirpath, dirs, files in os.walk(WIKI_ROOT):
        # 跳过 _ 前缀目录（版本快照 _versions 等归档区）：它们的相对链接在活文件
        # 原位有效，被拷进深层快照目录后必然断链，扫它们会产生假阳性死链。
        # 与 raw/ 下 _ 前缀归档区不进索引同一原则。
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        for fn in files:
            if not fn.endswith(".md"):
                continue
            # log.md / log_*.md 是只追加的操作流水，天然用简写文件名记录，
            # 把它的简写当失效引用属于误报 → 显式豁免，并在报告里写明豁免了谁
            if fn == "log.md" or fn.startswith("log_"):
                skipped.append(os.path.relpath(os.path.join(dirpath, fn), KB_ROOT)
                               .replace("\\", "/"))
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, KB_ROOT).replace("\\", "/")
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()
            except OSError:
                continue
            text = INLINE_CODE_RE.sub("", CODE_FENCE_RE.sub("", raw_text))
            urls = URL_RE.findall(text)
            srcs = SOURCE_RE.findall(text)
            # 来源行附近常带「作者/日期」，可能在其后一行，也可能在其**上一行**
            # （如 `> 2026-10-15 建｜...` 再 `> 来源：...`）。只取匹配行本身会漏日期
            # → 取匹配行前后各两行一起判定。
            lines_all = text.splitlines()
            ctx = []
            for m in SOURCE_RE.finditer(text):
                i = text[:m.start()].count("\n")
                ctx.append("\n".join(lines_all[max(0, i - 2):i + 3]))
            fm_text = parse_frontmatter(raw_text)
            src_text = " ".join(s[1] for s in srcs) + " " + " ".join(ctx) + " " + fm_text
            fm_has_src = bool(re.search(r"^\s*sources\s*[:：]", fm_text, re.M | re.I))
            raws = RAW_RE.findall(text)
            raw_paths = []
            for _, v in raws:
                for tok in re.split(r"[、,，;；\s]+", v):
                    tok = tok.strip().strip("`")
                    if re.search(r"\.(md|pdf|docx|csv|json|png|jpg|py|html)$", tok, re.I):
                        raw_paths.append(tok)
            links = [m.group(1).strip() for m in LINK_RE.finditer(text)]
            rows.append({
                "path": rel, "urls": urls, "has_url": bool(urls),
                "source_text": src_text, "has_source": bool(srcs) or fm_has_src,
                "source_dated": bool(DATE_RE.search(src_text)),
                "raw_paths": raw_paths, "links": links,
            })
    return rows, skipped


def check_targets(rows):
    """校验每条 库内链接 / Raw 引用 能不能落到真实文件，返回缺口列表。"""
    dead_links, dead_raw = [], []
    for r in rows:
        base = os.path.dirname(os.path.join(KB_ROOT, r["path"]))
        for tgt in r["links"]:
            t = tgt.strip().split("#")[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, t))) and \
               not os.path.exists(os.path.normpath(os.path.join(KB_ROOT, t))):
                dead_links.append({"from": r["path"], "target": t})
        for p in r["raw_paths"]:
            if not os.path.exists(os.path.normpath(os.path.join(base, p))) and \
               not os.path.exists(os.path.normpath(os.path.join(KB_ROOT, p))):
                dead_raw.append({"from": r["path"], "target": p})
    return dead_links, dead_raw


def compute(rows, dead_links, dead_raw):
    url_docs = [r for r in rows if r["has_url"]]
    src_docs = [r for r in rows if r["has_source"]]
    total_links = sum(len([t for t in r["links"]
                           if not t.startswith(("http://", "https://", "mailto:"))]) for r in rows)
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "doc_count": len(rows),
        "url_docs": len(url_docs),
        "url_docs_with_source": len([r for r in url_docs if r["has_source"]]),
        "source_docs": len(src_docs),
        "source_dated": len([r for r in src_docs if r["source_dated"]]),
        "internal_links": total_links,
        "dead_links": len(dead_links),
        "raw_refs": sum(len(r["raw_paths"]) for r in rows),
        "dead_raw": len(dead_raw),
        "url_trace_coverage": (len([r for r in url_docs if r["has_source"]]) / len(url_docs) * 100)
        if url_docs else 100.0,
        "source_date_rate": (len([r for r in src_docs if r["source_dated"]]) / len(src_docs) * 100)
        if src_docs else 0.0,
        "internal_link_resolve_rate": ((total_links - len(dead_links)) / total_links * 100)
        if total_links else 100.0,
    }


def main():
    global WIKI_ROOT
    ap = argparse.ArgumentParser(description="引用可追溯检查")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--fix-hint", action="store_true", help="打印每条缺口的补法")
    ap.add_argument("--wiki-root", default=WIKI_ROOT)
    args = ap.parse_args()

    WIKI_ROOT = args.wiki_root
    rows, _skipped = scan()
    dead_links, dead_raw = check_targets(rows)
    m = compute(rows, dead_links, dead_raw)
    m["dead_link_list"] = dead_links
    m["dead_raw_list"] = dead_raw
    missing_src = [r["path"] for r in rows if r["has_url"] and not r["has_source"]]
    m["missing_source_docs"] = missing_src
    undated = [r["path"] for r in rows if r["has_source"] and not r["source_dated"]]
    m["undated_source_docs"] = undated

    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
        with open(JSON_OUT, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        return

    print("=== 引用可追溯检查 %s ===" % m["checked_at"][:19])
    print("文档 %d 篇｜含外部链接 %d 篇｜其中已标来源 %d 篇" % (
        m["doc_count"], m["url_docs"], m["url_docs_with_source"]))
    print("")
    print("  A 网上来源溯源覆盖率 ：%.1f%%（缺 %d 篇）" % (
        m["url_trace_coverage"], len(missing_src)))
    print("  A 来源字段带日期率   ：%.1f%%（缺 %d 篇）" % (
        m["source_date_rate"], len(undated)))
    print("  B 库内引用可解析率   ：%.1f%%（%d 条链接，失效 %d）" % (
        m["internal_link_resolve_rate"], m["internal_links"], m["dead_links"]))
    print("  B Raw 原料存在率     ：%d 条引用，失效 %d" % (m["raw_refs"], m["dead_raw"]))

    gate = []
    if m["url_trace_coverage"] < 95:
        gate.append("网上来源溯源覆盖率 %.1f%% 未达 95%%" % m["url_trace_coverage"])
    if m["dead_links"]:
        gate.append("%d 条库内引用指向不存在的文件" % m["dead_links"])
    if m["source_date_rate"] < 90:
        gate.append("来源带日期率 %.1f%% 未达 90%%" % m["source_date_rate"])
    print("\n结论：%s%s" % ("PASS" if not gate else "FAIL",
                          ("｜" + "；".join(gate)) if gate else ""))

    if args.fix_hint:
        if missing_src:
            print("\n【缺来源标注，补 `> Sources: <标题>, <YYYY-MM-DD>`】")
            for p in missing_src[:15]:
                print("  · %s" % p)
        if undated:
            print("\n【有来源但缺日期】")
            for p in undated[:15]:
                print("  · %s" % p)
        if dead_links:
            print("\n【引用了不存在的文件】")
            for d in dead_links[:15]:
                print("  · %s → %s" % (d["from"], d["target"]))

    lines = ["# 引用可追溯检查报告", "",
             "> 生成时间：%s ｜ 扫描 %d 篇 wiki ｜ 结论：**%s**" % (
                 m["checked_at"], m["doc_count"], "PASS" if not gate else "FAIL"), "",
             "> 豁免 %d 个流水文件（log.md / log_*.md）——操作流水天然用简写文件名记录，"
             "把它的简写当成失效引用属于误报；豁免范围在此公开写明，不藏着。" % len(_skipped), "",
             "## 一、指标总览", "",
             "| 指标 | 数值 | 说明 |", "|---|---|---|",
             "| 网上来源溯源覆盖率 | %.1f%% | 含外部链接的文章中已标注来源的比例 |" % m["url_trace_coverage"],
             "| 来源字段带日期率 | %.1f%% | 有来源标注的文章中带明确日期的比例 |" % m["source_date_rate"],
             "| 库内引用可解析率 | %.1f%% | 相对链接能落到真实文件的比例 |" % m["internal_link_resolve_rate"],
             "| Raw 原料引用 | %d 条 / 失效 %d | Raw 字段指向的文件是否存在 |" % (
                 m["raw_refs"], m["dead_raw"]), ""]
    lines += ["## 二、为什么这两条要分开算", "",
              "**网上来源**缺标注 = 外来知识进了库却查不到出处，时间一久就跟自己想的分不清；",
              "**库内来源**路径失效 = 引用了不存在的证据，这是幻觉最常见的落地形式。",
              "两者失败模式不同，所以指标分开、修复方式也不同。", ""]
    if missing_src:
        lines += ["## 三、缺来源标注的文章（%d 篇）" % len(missing_src), "",
                  "补法：在文章头部加一行 `> Sources: <资料标题>, <YYYY-MM-DD>`", ""]
        for p in missing_src:
            lines.append("- `%s`" % p)
        lines.append("")
    if dead_links:
        lines += ["## 四、库内失效引用（%d 条）" % len(dead_links), ""]
        for d in dead_links:
            lines.append("- `%s` → `%s`" % (d["from"], d["target"]))
        lines.append("")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print("\n报告：%s" % REPORT)


if __name__ == "__main__":
    main()
