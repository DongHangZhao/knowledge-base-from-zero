# 公开评测表 · oss-demo-v1

> 🔴 **这不是质量结论**。gold 为「query 词取自文档标题/正文」的自动弱标注，
> 非人标；仅演示指标口径与回归冒烟，**不得用于检索质量结论、模型选型或简历数字**。
> 生产 184 条人标主集含隐私，不公开；judge 未校准；本评测不含任何 LLM 判分，
> 也不做显著性检验（样本量与标注强度都不够）。

| 项 | 值 |
|---|---|
| 生成时间 | 2026-11-11 00:22:57 |
| 语料 | `examples/state/demo.db` · 41 docs / 560 chunks（仓库公开 Markdown，**不注水**） |
| 检索路 | `kw`（FTS5 关键词，零模型依赖）｜适配器 `LlamaIndex BaseRetriever` |
| 样本数 | 18（中 14 / 英 4） |
| 可追溯校验通过 | 18 / 18 |
| **R@5** | 1.000 |
| **MRR** | 1.000 |
| 延迟 p50 / p95 | 20.16 ms / 86.18 ms（单次 top5，含冷启动） |
| 平均 top1 覆盖率 | 0.917 |

## 逐条明细

| # | query | gold 路径 | top1 路径 | 命中 rank | top1 覆盖率 | 延迟 ms | 可追溯 |
|---|---|---|---|---|---|---|---|
| 1 | RRF 倒数排名融合 | `docs/检索层设计与评测.md` | `docs/检索层设计与评测.md` | 1 | 0.0 | 86.18 | ✓ |
| 2 | 双链路体系 文件链路 索引链路 | `docs/双链路体系.md` | `docs/双链路体系.md` | 1 | 1.0 | 20.81 | ✓ |
| 3 | 架构总览 四区 真相源 | `docs/架构总览.md` | `docs/架构总览.md` | 1 | 1.0 | 20.16 | ✓ |
| 4 | 技能工作台 提示词 卡片 | `docs/技能工作台.md` | `docs/技能工作台.md` | 1 | 1.0 | 19.83 | ✓ |
| 5 | 开源守则 KBL-1.0 许可 | `docs/开源守则.md` | `docs/开源守则.md` | 1 | 1.0 | 19.93 | ✓ |
| 6 | 任务清单 并列任务依次执行 | `docs/任务清单.md` | `docs/任务清单.md` | 1 | 1.0 | 27.91 | ✓ |
| 7 | 团队协作规范 交付流程 | `docs/团队协作规范与交付流程.md` | `docs/团队协作规范与交付流程.md` | 1 | 1.0 | 20.01 | ✓ |
| 8 | 使用指南 别人怎么用这个项目 | `docs/使用指南.md` | `docs/使用指南.md` | 1 | 1.0 | 21.38 | ✓ |
| 9 | 从零到一搭建步骤 详细版 | `docs/从零到一搭建步骤.md` | `docs/从零到一搭建步骤.md` | 1 | 1.0 | 22.18 | ✓ |
| 10 | 模块总览 可复用能力区 | `docs/模块总览-示例.md` | `docs/模块总览-示例.md` | 1 | 1.0 | 21.73 | ✓ |
| 11 | 复用副本不改原版 提示词资产 | `templates/wiki/提示词资产/复用副本不改原版.example.md` | `templates/wiki/提示词资产/复用副本不改原版.example.md` | 1 | 1.0 | 21.25 | ✓ |
| 12 | 知识库检索 示例模块说明 | `templates/wiki/可复用模块/检索.example.md` | `templates/wiki/可复用模块/检索.example.md` | 1 | 1.0 | 22.76 | ✓ |
| 13 | GraphRAG 独立知识图谱子系统 | `modules/知识图谱/README.md` | `modules/知识图谱/README.md` | 1 | 1.0 | 18.24 | ✓ |
| 14 | RAG Index Engine 混合检索 | `modules/知识库索引/README.md` | `modules/知识库索引/README.md` | 1 | 1.0 | 20.69 | ✓ |
| 15 | Retrieval Layer RRF fusion | `docs/retrieval-engine.en.md` | `docs/retrieval-engine.en.md` | 1 | 0.5 | 17.69 | ✓ |
| 16 | dual pipeline content index | `docs/dual-pipeline.en.md` | `docs/dual-pipeline.en.md` | 1 | 1.0 | 17.80 | ✓ |
| 17 | Skill workbench prompts skills cards | `docs/skill-workbench.en.md` | `docs/skill-workbench.en.md` | 1 | 1.0 | 13.28 | ✓ |
| 18 | Architecture overview four zones | `docs/architecture.en.md` | `docs/architecture.en.md` | 1 | 1.0 | 12.62 | ✓ |

## 为什么这张表容易满分，以及它不能证明什么

- 弱标注 query 的词**直接取自 gold 文档的标题/正文**，`kw` 路又是 OR 语义，
  命中几乎是必然的。所以 R@5 / MRR 拿满分只说明「clone → 建库 → 检索 → 引用」
  **链路是通的**，不说明排序质量好、更不说明比别人强。
- 这正是生产侧另设 184 条**去饱和人标主集** + McNemar 显著性检验 + `eval_gate` 的原因：
  避开自证。本表**不得**用于简历数字、模型选型、框架对比或任何「涨点/更强」结论。
- 本表仅两个用途：① **回归冒烟**（改坏了会掉分）② **指标口径演示**
  （R@5 / MRR / p95 / 覆盖率怎么算、覆盖率警示长什么样）。
- 未做、且归人验的部分：gold 人标校准（归库主）、`judge_calibration.py` 校准、
  同义改写 / 跨语言 / 明确无答案（拒答率）难例集、多跑道显著性检验。
  想让评测变难，前提是有人标 gold——不是把 query 改难就能自证。


## 复跑与边界

```
python examples/build_demo_index.py
python examples/eval_public.py     # 重新生成本表
```
- `rank=未命中` 照样列出，不剔除、不放宽、不补标注（库主铁律：库里有的不许说没有）。
- 未做长时多进程并发压测；REST 侧并发基线见 `HANDOFF.md`，短并发 ≠ soak。
- 零 push / 零上传 / 零发布：本表只是本地产物，完成 ≠ 发布。
- 许可 KBL-1.0 · 署名 FallenPetal。
