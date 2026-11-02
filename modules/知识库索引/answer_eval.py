#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
answer_eval — 答案层评测（P0-1：检索好 ≠ 回答对）
==================================================

检索评测答的是「有没有捞到正确文档」，答案层评测答的是「捞到之后有没有说错」。
后者才决定用户实际体验，但也是最容易空着的一层——本脚本把它变成可算的数。

五个指标（全部零外部依赖，可回归）：

| 指标 | 算法 | 抓什么 |
|------|------|--------|
| 引用准确率 | 答案里引用的路径 ⊆ 检索上下文 | 引用是不是真的来自上下文 |
| 幽灵路径率 | 答案里的路径在磁盘上存不存在 | **幻觉的直接指标** |
| 无引用率 | 答案一条路径都没给的比例 | 说了等于没说的答案 |
| 上下文支撑率 | 答案实词被上下文覆盖的比例 | faithfulness 的弱代理（jieba 分词重叠） |
| 上下文利用率 | 答案引用了 top-k 中的几篇 | 是不是只用了第一篇文章 |

⚠ 诚实声明：上下文支撑率是**弱代理**，它能抓「答案凭空造词」，抓不住「答案把 A 的事安到 B 头上」。
真正的 faithfulness 要 LLM-as-judge（P1）。这个指标的价值是**零成本可回归**，先把趋势盯起来。

用法：
  python answer_eval.py harvest --query "问法" --answer "答案"   # 日常问答落库（自动取上下文+去重）
  python answer_eval.py harvest --file a.json                   # 批量落库
  python answer_eval.py prepare --n 12      # 打印待作答题目 + 检索到的上下文（给作答用）
  python answer_eval.py add --file a.json   # 批量录入答案样本（需手工准备 contexts）
  python answer_eval.py report              # 出评测报告 state/answer_eval_report.md
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

SAMPLES = os.path.join(HERE, "state", "answer_samples.json")
REPORT = os.path.join(HERE, "state", "answer_eval_report.md")
# 注意带上根级文件（SKILL.md / index.md 等），否则引用它们会被误判成「没给出处」
PATH_RE = re.compile(
    r"((?:wiki|raw|modules|outputs|references)/[\w一-鿿\-/\.]+\.(?:md|png|jpg|pdf|docx|csv|json|html|py)"
    r"|SKILL\.md|index\.md)")
STOP = set("的了是我在有和就不人都一个上也很到说要去你会着没有看好自己这那么什么怎么如何可以为与及 neboj".split())


def _kb():
    from kb_index import connect, search_fts_fused, search_fts_chunks
    return connect(), search_fts_fused, search_fts_chunks


def ctx_from_chunks(conn, search_chunks, query, k=3, limit=1500):
    """真实链路的上下文 = **分块内容**，不是整篇文档的前 N 字。

    为什么必须这样：整篇检索返回的是「文档」，取前 1800 字拿到的大概率是文档开头，
    跟问题毫无关系；而答案层评测要模拟的是「LLM 实际读到什么」——
    真实链路是检索 → 取块 → 读块 → 作答。用整篇开头当上下文，会系统性低估 faithfulness。
    """
    out = []
    for h in search_chunks(conn, query, k):
        row = conn.execute(
            "SELECT content FROM chunks WHERE doc_id=? AND heading=? LIMIT 1",
            (h["doc_id"], h.get("heading", ""))).fetchone()
        out.append({"path": h["path"], "title": h["title"],
                    "heading": h.get("heading", ""),
                    "excerpt": (row[0] if row else h.get("summary", ""))[:limit]})
    return out


def tokenize_words(text):
    try:
        import jieba
        jieba.setLogLevel(60)
        words = [w for w in jieba.lcut(text) if len(w) >= 2 and w not in STOP]
    except ImportError:
        words = [text[i:i + 2] for i in range(len(text) - 1)]
    return [w for w in words if not re.match(r"^[\W\d_]+$", w)]


def read_ctx(rel, limit=2500):
    full = os.path.join(KB_ROOT, rel)
    if not os.path.exists(full):
        return ""
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read()[:limit]


def cmd_prepare(args):
    conn, _search_doc, search_chunks = _kb()
    items = json.load(open(os.path.join(HERE, "state", "kb_eval_100.json"), encoding="utf-8"))
    picked = items[:args.n]
    out = []
    for it in picked:
        out.append({
            "query": it["query"],
            "gt": it["gt"],
            "contexts": ctx_from_chunks(conn, search_chunks, it["query"],
                                        args.k, args.ctx_len),
            "answer": "",
        })
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已生成 %d 道待作答题 → %s" % (len(out), args.out))
    print("填 answer 字段后运行：python answer_eval.py add --file %s" % args.out)
    conn.close()


def cmd_add(args):
    data = json.load(open(args.file, encoding="utf-8"))
    exist = []
    if os.path.exists(SAMPLES):
        exist = json.load(open(SAMPLES, encoding="utf-8"))
    known = {s["query"] for s in exist}
    added = 0
    for s in data:
        if not s.get("answer", "").strip():
            continue
        s["added_at"] = datetime.now().isoformat(timespec="seconds")
        if s["query"] in known:
            exist = [s if x["query"] == s["query"] else x for x in exist]
        else:
            exist.append(s)
            added += 1
        known.add(s["query"])
    with open(SAMPLES, "w", encoding="utf-8") as f:
        json.dump(exist, f, ensure_ascii=False, indent=2)
    print("样本库：新增 %d 条，共 %d 条 → %s" % (added, len(exist), SAMPLES))


def evaluate(samples):
    rows, agg = [], {"n": 0, "cite_ok": 0, "ghost": 0,
                     "no_cite": 0, "support": 0.0, "use": 0.0}
    for s in samples:
        ans = s.get("answer", "")
        ctx_paths = [c["path"] for c in s.get("contexts", [])]
        cited = PATH_RE.findall(ans)
        ghost = [p for p in cited if not os.path.exists(os.path.join(KB_ROOT, p))]
        not_in_ctx = [p for p in cited if p not in ctx_paths]
        ctx_text = " ".join(read_ctx(p) for p in ctx_paths)
        awords = set(tokenize_words(ans))
        cwords = set(tokenize_words(ctx_text))
        support = (len(awords & cwords) / len(awords)) if awords else 1.0
        used = len({p for p in cited if p in ctx_paths})
        is_synthetic = bool(s.get("synthetic_bad"))
        rows.append({"query": s["query"], "cited": cited, "ghost": ghost,
                     "not_in_ctx": not_in_ctx, "support": round(support, 3),
                     "used": used, "ctx_n": len(ctx_paths),
                     "synthetic": is_synthetic})
        # 聚合只统计真实样本：synthetic_bad 是刻意造的反例（验证幽灵/张冠李戴检测用），
        # 不能拉低生产环境的答案质量率；但仍保留在明细里证明「确实被抓到」。
        if is_synthetic:
            continue
        agg["n"] += 1
        agg["cite_ok"] += 1 if (cited and not not_in_ctx) else 0
        agg["ghost"] += 1 if ghost else 0
        agg["no_cite"] += 1 if not cited else 0
        agg["support"] += support
        agg["use"] += used
    n = max(1, agg["n"])
    return {
        "n": agg["n"],
        "citation_accuracy": agg["cite_ok"] / n * 100,
        "ghost_path_rate": agg["ghost"] / n * 100,
        "no_citation_rate": agg["no_cite"] / n * 100,
        "context_support": agg["support"] / n,
        "avg_ctx_used": agg["use"] / n,
    }, rows


def cmd_report(args):
    if not os.path.exists(SAMPLES):
        print("还没有答案样本，先跑 prepare + add")
        return 1
    samples = json.load(open(SAMPLES, encoding="utf-8"))
    samples = [s for s in samples if s.get("answer", "").strip()]
    m, rows = evaluate(samples)
    print("=== 答案层评测（%d 条样本）===" % m["n"])
    print("  引用准确率      %.1f%%   引用的路径是否都来自检索上下文" % m["citation_accuracy"])
    print("  幽灵路径率      %.1f%%   🔴 引用了不存在的路径 = 幻觉" % m["ghost_path_rate"])
    print("  无引用率        %.1f%%   答案没给任何出处" % m["no_citation_rate"])
    print("  上下文支撑率    %.3f    答案实词被上下文覆盖的比例（faithfulness 弱代理）" % m["context_support"])
    print("  平均用到上下文  %.2f 篇" % m["avg_ctx_used"])
    gate = []
    if m["ghost_path_rate"] > 0:
        gate.append("存在幽灵路径引用，必须清零")
    if m["no_citation_rate"] > 20:
        gate.append("无引用率 %.0f%% 超过 20%%" % m["no_citation_rate"])
    print("\n结论：%s%s" % ("PASS" if not gate else "FAIL", ("｜" + "；".join(gate)) if gate else ""))

    lines = ["# 答案层评测报告", "",
             "> 生成时间：%s ｜ 样本 %d 条 ｜ 结论：**%s**" % (
                 datetime.now().isoformat(timespec="seconds"), m["n"],
                 "PASS" if not gate else "FAIL"), "",
             "| 指标 | 数值 | 说明 |", "|---|---|---|",
             "| 引用准确率 | %.1f%% | 答案引用的路径是否都来自检索上下文 |" % m["citation_accuracy"],
             "| 幽灵路径率 | %.1f%% | 🔴 引用不存在的路径 = 幻觉的直接指标 |" % m["ghost_path_rate"],
             "| 无引用率 | %.1f%% | 答案没给任何出处 |" % m["no_citation_rate"],
             "| 上下文支撑率 | %.3f | 答案实词被上下文覆盖的比例 |" % m["context_support"],
             "| 平均用到上下文 | %.2f 篇 | 是不是只看了第一篇 |" % m["avg_ctx_used"], "",
             "## 逐条明细", "", "| 问法 | 引用路径 | 幽灵 | 不在上下文 | 支撑率 |", "|---|---|---|---|---|"]
    for r in rows:
        qlabel = (r["query"][:24] + "（反例）") if r.get("synthetic") else r["query"][:30]
        lines.append("| %s | %s | %s | %s | %.2f |" % (
            qlabel, ", ".join("`%s`" % p for p in r["cited"]) or "—",
            "⚠" if r["ghost"] else "—", "⚠" if r["not_in_ctx"] else "—", r["support"]))
    lines += ["", "> 上下文支撑率是**弱代理**：抓得住「凭空造词」，抓不住「张冠李戴」。",
              "> 真正的 faithfulness 要 LLM-as-judge（P1 事项）。这里先保证趋势可回归。"]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n报告：%s" % REPORT)
    return 0


def cmd_harvest(args):
    """日常问答落库：自动取块级上下文 + 按问法去重 + 记录来源。

    为什么要它：评测样本全靠人攒，永远攒不起来。**让真实问答自己长成评测集**，
    样本的分布才是真实的（query_log 也印证了：真实问法和人工出题不一样）。
    """
    conn, _doc, search_chunks = _kb()
    if args.file:
        rows = json.load(open(args.file, encoding="utf-8"))
    else:
        if not args.query or not args.answer:
            print("需要 --query 与 --answer，或 --file")
            return 1
        rows = [{"query": args.query, "answer": args.answer}]
    exist = json.load(open(SAMPLES, encoding="utf-8")) if os.path.exists(SAMPLES) else []
    by_q = {s["query"]: s for s in exist}
    added = updated = no_cite = 0
    for r in rows:
        q = (r.get("query") or "").strip()
        ans = (r.get("answer") or "").strip()
        if not q or not ans:
            continue
        # 根因治理：强制答案带 path 出处，杜绝「无引用样本」入库。
        # 为什么：答案层无引用率全部来自「答案把结论内化、没标出处」的作答习惯，
        # 在入口拦一次比事后补样本治本；确属常识/库里没有的答案可显式 --allow-no-cite。
        if not PATH_RE.findall(ans) and not getattr(args, "allow_no_cite", False):
            no_cite += 1
            print("  ⚠ 拒绝入库（答案无 path 引用，请补 wiki/... 出处）：%s" % q[:40])
            continue
        item = {"query": q, "answer": ans,
                "contexts": ctx_from_chunks(conn, search_chunks, q, args.k, args.ctx_len),
                "harvested_at": datetime.now().isoformat(timespec="seconds"),
                "harvest_from": r.get("from") or args.from_tag}
        if q in by_q:
            old = by_q[q]
            if old.get("answer") != ans:
                old.update(item)
                updated += 1
            else:
                continue
        else:
            exist.append(item)
            by_q[q] = item
            added += 1
    with open(SAMPLES, "w", encoding="utf-8") as f:
        json.dump(exist, f, ensure_ascii=False, indent=2)
    print("样本库：新增 %d、更新 %d、拒绝无出处 %d，共 %d 条 → %s"
          % (added, updated, no_cite, len(exist), SAMPLES))
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="答案层评测")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="生成待作答题（含检索上下文）")
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--k", type=int, default=3, help="取几个块当上下文")
    p.add_argument("--ctx-len", type=int, default=1500)
    p.add_argument("--out", default=os.path.join(HERE, "state", "answer_pending.json"))
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("add", help="录入答案样本")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("harvest", help="日常问答落库（自动取上下文+去重）")
    p.add_argument("--query")
    p.add_argument("--answer")
    p.add_argument("--file")
    p.add_argument("--from", dest="from_tag", default="日常问答", help="来源标记")
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--ctx-len", type=int, default=1500)
    p.add_argument("--allow-no-cite", action="store_true",
                   help="允许无 path 引用的答案入库（默认拒绝，治缺溯源）")
    p.set_defaults(func=cmd_harvest)
    sub.add_parser("report").set_defaults(func=cmd_report)
    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
