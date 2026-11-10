#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开评测：跑弱标注示例集，算 R@5 / MRR / p95 / 覆盖率，生成 ``EVAL.md``。

🔴 边界（先读，别把这张表当质量结论）：
- gold 是「query 词取自该文档标题/正文」的**自动弱标注**，不是人标；仅用于演示指标口径
  与回归冒烟，**不得用于检索质量结论、模型选型或简历数字**。
- 生产 184 条人标主集含隐私，不公开；judge 未校准，本脚本不含任何 LLM 判分
  （LLM 不自当评测金标），也不跑 McNemar 显著性检验（样本量与标注强度都不够）。
- 每条 query 会做**可追溯校验**：gold 文档必须在库里，且 query 的分词至少一个
  出现在该文档正文。校验不过照样如实列出 WARN，不偷偷剔除。

用法：python examples/eval_public.py   （需先跑 build_demo_index.py）
"""
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
sys.path[:0] = [_REPO, os.path.join(_REPO, "modules", "adapters"),
                os.path.join(_REPO, "modules", "知识库索引")]

from llama_index.core.schema import QueryBundle    # noqa: E402
from llama_index_retriever import KBRetriever      # noqa: E402
import kb_index                                    # noqa: E402

DB = os.environ.get("KB_DEMO_DB") or os.path.join(_HERE, "state", "demo.db")
SET = os.path.join(_HERE, "eval_public.json")
OUT = os.path.join(_HERE, "EVAL.md")


def main():
    data = json.load(open(SET, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    retriever = KBRetriever(db_path=DB, mode="kw", top_k=5)
    rows = []
    for case in data["queries"]:
        q, exp = case["q"], case["expected"]
        r = conn.execute("SELECT content FROM docs WHERE path=?", (exp,)).fetchone()
        toks = [t for t in kb_index.tokenize(q) if len(t) >= 2]
        trace = bool(r) and any(t in (r[0] or "").lower() for t in toks)
        t0 = time.perf_counter()
        nodes = retriever.retrieve(QueryBundle(q))
        ms = round((time.perf_counter() - t0) * 1000, 2)
        paths = [n.node.metadata.get("path", "") for n in nodes]
        rank = paths.index(exp) + 1 if exp in paths else 0
        cov = nodes[0].node.metadata.get("coverage") if nodes else None
        rows.append({"q": q, "lang": case.get("lang", ""), "expected": exp,
                     "top1": paths[0] if paths else "—", "rank": rank,
                     "coverage": cov, "ms": ms, "trace": trace})
    conn.close()

    n = len(rows)
    r5 = sum(1 for r in rows if 0 < r["rank"] <= 5) / n
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / n
    lats = sorted(r["ms"] for r in rows)
    p50 = lats[math.ceil(0.50 * n) - 1]
    p95 = lats[math.ceil(0.95 * n) - 1]
    covs = [r["coverage"] for r in rows if r["coverage"] is not None]
    avg_cov = round(sum(covs) / len(covs), 3) if covs else None
    traced = sum(1 for r in rows if r["trace"])

    L = []
    L.append("# 公开评测表 · oss-demo-v1\n")
    L.append("> 🔴 **这不是质量结论**。gold 为「query 词取自文档标题/正文」的自动弱标注，")
    L.append("> 非人标；仅演示指标口径与回归冒烟，**不得用于检索质量结论、模型选型或简历数字**。")
    L.append("> 生产 184 条人标主集含隐私，不公开；judge 未校准；本评测不含任何 LLM 判分，")
    L.append("> 也不做显著性检验（样本量与标注强度都不够）。\n")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 生成时间 | %s |" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("| 语料 | `examples/state/demo.db` · %d docs / %d chunks（仓库公开 Markdown，**不注水**） |" % (docs, chunks))
    L.append("| 检索路 | `kw`（FTS5 关键词，零模型依赖）｜适配器 `LlamaIndex BaseRetriever` |")
    L.append("| 样本数 | %d（中 %d / 英 %d） |" % (n, sum(1 for r in rows if r["lang"] == "zh"),
                                              sum(1 for r in rows if r["lang"] == "en")))
    L.append("| 可追溯校验通过 | %d / %d |" % (traced, n))
    L.append("| **R@5** | %.3f |" % r5)
    L.append("| **MRR** | %.3f |" % mrr)
    L.append("| 延迟 p50 / p95 | %.2f ms / %.2f ms（单次 top5，含冷启动） |" % (p50, p95))
    L.append("| 平均 top1 覆盖率 | %s |" % (avg_cov if avg_cov is not None else "n/a"))
    L.append("\n## 逐条明细\n")
    L.append("| # | query | gold 路径 | top1 路径 | 命中 rank | top1 覆盖率 | 延迟 ms | 可追溯 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        L.append("| %d | %s | `%s` | `%s` | %s | %s | %.2f | %s |" %
                 (i, r["q"].replace("|", "\\|"), r["expected"], r["top1"],
                  r["rank"] or "未命中", r["coverage"] if r["coverage"] is not None else "—",
                  r["ms"], "✓" if r["trace"] else "⚠️ WARN"))
    L.append("\n## 为什么这张表容易满分，以及它不能证明什么\n")
    L.append("- 弱标注 query 的词**直接取自 gold 文档的标题/正文**，`kw` 路又是 OR 语义，")
    L.append("  命中几乎是必然的。所以 R@5 / MRR 拿满分只说明「clone → 建库 → 检索 → 引用」")
    L.append("  **链路是通的**，不说明排序质量好、更不说明比别人强。")
    L.append("- 这正是生产侧另设 184 条**去饱和人标主集** + McNemar 显著性检验 + `eval_gate` 的原因：")
    L.append("  避开自证。本表**不得**用于简历数字、模型选型、框架对比或任何「涨点/更强」结论。")
    L.append("- 本表仅两个用途：① **回归冒烟**（改坏了会掉分）② **指标口径演示**")
    L.append("  （R@5 / MRR / p95 / 覆盖率怎么算、覆盖率警示长什么样）。")
    L.append("- 未做、且归人验的部分：gold 人标校准（归库主）、`judge_calibration.py` 校准、")
    L.append("  同义改写 / 跨语言 / 明确无答案（拒答率）难例集、多跑道显著性检验。")
    L.append("  想让评测变难，前提是有人标 gold——不是把 query 改难就能自证。\n")
    L.append("\n## 复跑与边界\n")
    L.append("```")
    L.append("python examples/build_demo_index.py")
    L.append("python examples/eval_public.py     # 重新生成本表")
    L.append("```")
    L.append("- `rank=未命中` 照样列出，不剔除、不放宽、不补标注（库主铁律：库里有的不许说没有）。")
    L.append("- 未做长时多进程并发压测；REST 侧并发基线见 `HANDOFF.md`，短并发 ≠ soak。")
    L.append("- 零 push / 零上传 / 零发布：本表只是本地产物，完成 ≠ 发布。")
    L.append("- 许可 KBL-1.0 · 署名 FallenPetal。\n")
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("EVAL.md written: %s" % OUT)
    print("docs=%d chunks=%d n=%d traced=%d R@5=%.3f MRR=%.3f p50=%.2fms p95=%.2fms avg_cov=%s" %
          (docs, chunks, n, traced, r5, mrr, p50, p95, avg_cov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
