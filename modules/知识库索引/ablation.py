#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ablation — RAG 检索消融实验（同一个知识库，一次只改一个变量）
==============================================================

原则：**同一份评测集、同一套检索算法、同一份索引范围，只变一个配置项。**
这样跑出来的差值才配叫「这个变量的收益」，而不是「运气」。

内置配置（对应 kb_index.py 的环境变量）：
  bigram        中文两字切分（无分词器兜底方案）
  jieba         jieba 精确模式
  jieba_search  jieba 搜索引擎模式（长词递归再切，提升召回）
  jieba_w10     jieba + FTS5 标题字段加权 10 倍（bm25 列权）
  jieba_sw      jieba_search + 标题加权（如果前两项都有效，这是组合拳）

副产物（顺手白拿）：索引体积、词项数、posting 数、本机实测查询延迟

用法：
  python ablation.py                       # 跑默认四配置，30+100 双集
  python ablation.py --configs bigram jieba
  python ablation.py --no-build            # 索引已存在，跳过重建只评测
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")

_CANDIDATE = r"E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
PY = os.environ.get("ELECTED_PY") or (_CANDIDATE if os.path.exists(_CANDIDATE) else sys.executable)

MODES = [("fts", "FTS5 关键词"),
         ("tfidf", "TF-IDF 向量"),
         ("hybrid", "混合等权 RRF"),
         ("hybrid_w2", "混合加权 RRF 2:1")]

CONFIGS = {
    "bigram": {"KB_TOKENIZER": "bigram"},
    "jieba": {"KB_TOKENIZER": "jieba"},
    "jieba_search": {"KB_TOKENIZER": "jieba_search"},
    "jieba_w10": {"KB_TOKENIZER": "jieba", "KB_FTS_W": "10"},
    "jieba_sw": {"KB_TOKENIZER": "jieba_search", "KB_FTS_W": "10"},
    "jieba_w5": {"KB_TOKENIZER": "jieba_search", "KB_FTS_W": "5"},
}


def run(cmd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE, env=e, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr, time.time() - t0


def build_index(name, env_map):
    db = os.path.join(STATE, "kb_%s.db" % name)
    env = dict(env_map, KB_DB_PATH=db)
    rc, out, dt = run([PY, "kb_index.py", "build", "--full"], env)
    if rc != 0:
        raise SystemExit("✗ [%s] 构建失败：\n%s" % (name, out[-1200:]))
    # 收缩 WAL，让体积读数准确（否则看到的是尚未 checkpoint 的大小）
    try:
        import sqlite3
        c = sqlite3.connect(db)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")
        c.close()
    except Exception:
        pass
    print("  [%s] 构建 %.1fs ｜ 索引 %.1f KB" % (name, dt, os.path.getsize(db) / 1024))
    return db


def eval_index(name, db, env_map, sets):
    env = dict(env_map, KB_DB_PATH=db)
    out_json = os.path.join(STATE, "ablation_%s.json" % name)
    which = "both" if len(sets) == 2 else sets[0]
    rc, out, _ = run([PY, "eval_rag.py", "--set", which, "--json", out_json, "--quiet"], env)
    if rc != 0 or not os.path.exists(out_json):
        raise SystemExit("✗ [%s] 评测失败：\n%s" % (name, out[-1200:]))
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def measure_latency(db, env_map, rounds=20):
    """本机实测平均查询延迟（ms）：8 条代表性 query × rounds 轮，同一连接。"""
    qs = ["混合检索", "每日运势", "SQLite 为什么", "简历求职", "傅里叶", "公众号日更",
          "塔罗三张牌", "Tai Chi 太极"]
    code = (
        "import sys,time,os;os.environ.update(%r);sys.path.insert(0,%r);"
        "import kb_index as k;c=k.connect(readonly=True);qs=%r;\n"
        "t0=time.time();\n"
        "[k.search_fts(c,q,5) for _ in range(%d) for q in qs];\n"
        "print((time.time()-t0)/(%d*len(qs))*1000)"
    ) % (env_map, HERE, qs, rounds, rounds)
    _, out, _ = run([PY, "-c", code])
    try:
        return float(out.strip().splitlines()[-1])
    except Exception:
        return -1.0


def pct(x):
    return "%.1f%%" % (x * 100)


def pick_set(data, fname):
    sets_ = data.get("sets", {})
    return next((v for k, v in sets_.items() if os.path.basename(k) == fname),
                next(iter(sets_.values()), None))


def main():
    ap = argparse.ArgumentParser(description="RAG 检索消融实验")
    ap.add_argument("--configs", nargs="+", default=["bigram", "jieba", "jieba_search", "jieba_w10"])
    ap.add_argument("--sets", nargs="+", default=["30", "100"])
    ap.add_argument("--no-build", action="store_true", help="跳过重建，直接用已有 db")
    ap.add_argument("--out", default=os.path.join(STATE, "ablation_report.md"))
    args = ap.parse_args()

    bad = [c for c in args.configs if c not in CONFIGS]
    if bad:
        raise SystemExit("未知配置：%s ｜ 可选：%s" % (bad, list(CONFIGS)))

    print("=== RAG 消融实验 ===\n解释器：%s\n配置：%s" % (PY, " / ".join(args.configs)))
    R = {}
    for name in args.configs:
        print("\n--- %s ---" % name)
        db = os.path.join(STATE, "kb_%s.db" % name)
        if not (args.no_build and os.path.exists(db)):
            db = build_index(name, CONFIGS[name])
        data = eval_index(name, db, CONFIGS[name], args.sets)
        lat = measure_latency(db, CONFIGS[name])
        try:
            import sqlite3
            c = sqlite3.connect(db)
            terms = c.execute("SELECT COUNT(DISTINCT term) FROM doc_terms").fetchone()[0]
            postings = c.execute("SELECT COUNT(*) FROM doc_terms").fetchone()[0]
            c.close()
        except Exception:
            terms = postings = 0
        R[name] = {"db": db, "size_kb": os.path.getsize(db) / 1024, "data": data,
                   "latency_ms": lat, "terms": terms, "postings": postings}
        print("  评测完成｜%.1f KB｜词项 %d｜延迟 %.2f ms" % (R[name]["size_kb"], terms, lat))

    # ---------- 汇总 ----------
    lines = ["# RAG 检索消融实验报告", "",
             "> 生成时间：%s ｜ **唯一变量：配置** ｜ 评测集/topK/索引范围全部固定" %
             time.strftime("%Y-%m-%d %H:%M"), ""]
    print("\n\n=== 对照结果（Recall@5 / MRR@5）===")
    for s in args.sets:
        fname = "kb_eval_%s.json" % s
        print("\n## 评测集 %s 条" % s)
        lines.append("## 评测集 %s 条" % s)
        lines.append("")
        table = ["| 检索方式 | " + " | ".join(
            "%s Recall/MRR" % c for c in args.configs) + " |"]
        table.append("|---|" + "---|" * len(args.configs))
        for mode, label in MODES:
            cells = []
            for c in args.configs:
                tgt = pick_set(R[c]["data"], fname)
                m = tgt["res"][mode] if tgt else {"recall": 0, "mrr": 0}
                cells.append("%s / %.3f" % (pct(m["recall"]), m["mrr"]))
            table.append("| %s | %s |" % (label, " | ".join(cells)))
        lines.append("\n".join(table))
        lines.append("")
        print("\n".join(table))

        r1 = ["| 检索方式 | " + " | ".join(c for c in args.configs) + " |"]
        r1.append("|---|" + "---|" * len(args.configs))
        for mode, label in MODES:
            cells = []
            for c in args.configs:
                tgt = pick_set(R[c]["data"], fname)
                m = tgt["res"][mode] if tgt else {}
                cells.append(pct(m.get("recall1", 0)))
            r1.append("| %s | %s |" % (label, " | ".join(cells)))
        lines.append("### Recall@1（区分度更高，Recall@5 撞顶时才看得出差距）")
        lines.append("")
        lines.append("\n".join(r1))
        lines.append("")

        # 顽固难题：所有配置、所有检索方式都打不中的 query
        items_path = os.path.join(STATE, fname)
        if os.path.exists(items_path):
            with open(items_path, encoding="utf-8") as f:
                items = json.load(f)
            missed = {}
            for c in args.configs:
                tgt = pick_set(R[c]["data"], fname) or {"res": {}}
                for mode, _ in MODES:
                    for m in tgt["res"].get(mode, {}).get("misses", []):
                        missed.setdefault(m["query"], {"gt": m["gt"], "top1": m["top1"]})
            hard = [it for it in items if it["query"] in missed and all(
                any(m["query"] == it["query"] for m in
                    (pick_set(R[c]["data"], fname) or {"res": {}})["res"].get(md, {}).get("misses", []))
                for c in args.configs for md, _ in MODES)]
            if hard:
                lines.append("### 顽固难题（%d 条：换分词器、加权重都救不回来）" % len(hard))
                lines.append("")
                lines.append("| 问法 | 标准答案 | 最强路实际给的 Top1 |")
                lines.append("|---|---|---|")
                for it in hard:
                    g = missed[it["query"]]
                    lines.append("| %s | `%s` | `%s` |" % (it["query"], g["gt"], g["top1"]))
                lines.append("")
                print("\n顽固难题 %d 条（占该集 %.0f%%）" % (len(hard), len(hard) / len(items) * 100))

    meta = ["## 副产物指标", "", "| 配置 | 索引体积 | 文档数 | 词项数 | posting 数 | 检索延迟 |",
            "|---|---|---|---|---|---|"]
    for c in args.configs:
        meta.append("| %s | %.1f KB | %d | %d | %d | %.2f ms |" % (
            c, R[c]["size_kb"], R[c]["data"].get("doc_count", 0),
            R[c]["terms"], R[c]["postings"], R[c]["latency_ms"]))
    lines.append("\n".join(meta))
    lines.append("")

    # 自动结论：哪个配置在哪个模式上最优
    concl = ["## 逐项最优（脚本自动生成｜Recall@5 并列时以 Recall@1 决出）", ""]
    for s in args.sets:
        fname = "kb_eval_%s.json" % s
        for mode, label in MODES:
            def score(c, mode=mode):
                t = pick_set(R[c]["data"], fname) or {"res": {}}
                return (t["res"].get(mode, {}).get("recall", 0),
                        t["res"].get(mode, {}).get("recall1", 0))
            best, worst = max(args.configs, key=score), min(args.configs, key=score)
            b, w = score(best), score(worst)
            concl.append("- **%s 条 / %s**：最优 `%s`（%.1f%% / R@1 %.1f%%），最差 `%s`（%.1f%% / R@1 %.1f%%）" % (
                s, label, best, b[0] * 100, b[1] * 100, worst, w[0] * 100, w[1] * 100))
    lines.append("\n".join(concl))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n报告：%s" % args.out)


if __name__ == "__main__":
    main()
