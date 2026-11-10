#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地绘制 RAG demo 架构图（matplotlib + 中文 msyh.ttc）。

🔴 图里的数字全部来自本轮真跑取证（40 docs / 538 chunks、24/24 并发 0 错误、
   p95 157.7ms），不让模型凭印象「画一张带数字的图」。
🔴 产物 PNG 按 ``tools/privacy_scan.py`` L3 规则**禁止入 git**（媒体文件一律 BLOCK），
   已在根 ``.gitignore`` 排除；README 中公开可渲染的是同一结构的 Mermaid 文本图。

用法：python examples/make_architecture.py
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.font_manager import FontProperties   # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

FONT = FontProperties(fname="C:/Windows/Fonts/msyh.ttc")
_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "rag-demo-architecture.png")

BLUE, GREEN, GRAY, ORANGE = "#2563eb", "#0a7c42", "#64748b", "#b45309"
fig, ax = plt.subplots(figsize=(12, 6.4), dpi=160)
ax.set_xlim(0, 12)
ax.set_ylim(0, 6.4)
ax.axis("off")
fig.patch.set_facecolor("white")


def box(x, y, w, h, title, lines, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.10",
                                linewidth=1.6, edgecolor=color, facecolor="#f8fafc"))
    ax.text(x + w / 2, y + h - 0.28, title, ha="center", va="center",
            fontsize=11, fontweight="bold", color=color, fontproperties=FONT)
    for i, line in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.66 - i * 0.29, line, ha="center", va="center",
                fontsize=8.6, color="#334155", fontproperties=FONT)


def arrow(x1, y1, x2, y2, color=GRAY):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=13, linewidth=1.4, color=color))


ax.text(6, 6.05, "KB RAG Engine · 可复现 Demo 架构", ha="center", va="center",
        fontsize=15, fontweight="bold", color="#1f2937", fontproperties=FONT)
ax.text(6, 5.72, "自研三链路引擎 ｜ 适配器仅互操作 · 生产检索不经过框架", ha="center",
        fontsize=9, color=GRAY, fontproperties=FONT)

box(0.35, 2.45, 2.55, 1.35, "① 脱敏语料",
    ["仓库白名单公开 Markdown", "条数以实跑输出为准", "不连生产库 · 不注水"], GREEN)
box(3.45, 2.45, 2.55, 1.35, "② 建索引",
    ["build_demo_index.py", "→ demo.db (SQLite FTS5)", "幂等 upsert · 零模型依赖"], BLUE)
box(7.0, 4.15, 4.6, 1.18, "③A LlamaIndex 适配器",
    ["KBRetriever(BaseRetriever) → NodeWithScore", "demo_llamaindex.py：带引用路径作答"], BLUE)
box(7.0, 2.55, 4.6, 1.18, "③B 本地 REST (FastAPI)",
    ["/health · /search · /stats · /ask", "只绑 127.0.0.1:8788 · CORS 供 file:// 双击"], ORANGE)
box(7.0, 0.95, 4.6, 1.18, "③C 单文件 Web UI",
    ["index.html：结果 / 引用 / 分桶 / 延迟 / 覆盖率", "curl 预览地址字段比对一致"], GREEN)

arrow(2.92, 3.12, 3.42, 3.12)
for yy in (4.74, 3.14, 1.54):
    arrow(6.02, 3.12, 6.97, yy)

ax.text(0.35, 1.95, "④ /ask：LLM 能力必建 · 默认 off", ha="left", fontsize=9.4,
        fontweight="bold", color=ORANGE, fontproperties=FONT)
ax.text(0.35, 1.60, "一键可开 → 出口门禁(451) → 缓存(sha1) → 留痕 → 失败降级", ha="left",
        fontsize=8.6, color="#334155", fontproperties=FONT)
ax.text(0.35, 1.28, "凭据只走库外环境变量；零 push / 零上传 / 零对外发布", ha="left",
        fontsize=8.6, color="#334155", fontproperties=FONT)

ax.text(0.35, 0.55, "实测取证：REST 三接口 rc=0 ｜ 8 并发 24 请求 0 错误 ｜ 热态 p95 157.7ms ｜"
        " 公开弱标注集 R@5 1.000（不作质量结论）", ha="left", fontsize=8.2,
        color=GRAY, fontproperties=FONT)
ax.text(0.35, 0.25, "KBL-1.0 · FallenPetal ｜ 本图由 make_architecture.py 本地绘制，"
        "按隐私门禁不入 git", ha="left", fontsize=7.8, color=GRAY, fontproperties=FONT)

plt.tight_layout(pad=0.4)
fig.savefig(OUT, facecolor="white")
print("saved=%s bytes=%d" % (OUT, os.path.getsize(OUT)))
