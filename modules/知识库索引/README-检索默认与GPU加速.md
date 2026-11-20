# 检索默认：CPU small-zh vs GPU m3 强语义塔

> 2026-11-20 实测结论的开源版说明（不含任何本机路径/个人信息）。
> 适用：`kb_rag.py search` 的向量塔选择（关键词链路/字级链路不受影响）。

## 背景

检索栈有两套向量塔：

| 塔 | 维度 | 特点 |
|---|---|---|
| `small-zh`（默认） | 512 | 中文单语，CPU 即可，历史默认 |
| `bge-m3`（可选） | 1024 | 多语强语义塔，支持 GPU fp16 加速 |

代码里的两个开关（W7 系列，默认均为关闭/unset）：

- `KB_VEC_ROUTE=cjk\|all`：让中文查询也切到 m3（默认只外文查询切）
- `KB_VEC_M3_ENGINE=gpu_fp16`：m3 模型 tensors 上 CUDA（缺 CUDA 自动回落 CPU，不崩）

## 实测 A/B（2026-11-20，同尺子）

同一解释器、同一评测集，唯一变量为上述两个开关：

| 样本 | 档位 | recall@5 | recall@1 | p95 延迟 |
|---|---|---|---|---|
| 冻结主集 n=168 | small-zh | 0.476 | 0.256 | 852ms |
| 冻结主集 n=168 | m3 gpu_fp16 | 0.542 | 0.280 | 852ms |
| 独立新样本 n=30 | small-zh | 0.667 | 0.467 | 2168ms |
| 独立新样本 n=30 | m3 gpu_fp16 | 0.667 | 0.333 | 653ms |

**结论**：30 条独立新样本命中数两路完全相同（各 20/30）——m3 **不是"明显更准"**；但在支持 CUDA 的环境里 p95 延迟快 3.3×（2168→653ms）。在无 CUDA 的 CPU 环境强开 `gpu_fp16` 会回落 CPU 且反慢约 1.6×，**不要在没有 GPU 的环境强开**。

## 推荐用法

```bash
# 有 CUDA 的环境：临时启用 m3 GPU 强塔（不改默认值）
KB_VEC_ROUTE=cjk KB_VEC_M3_ENGINE=gpu_fp16 \
  python modules/知识库索引/kb_rag.py search --query "你的问题" --topk 5

# 默认 / 无 GPU：直接跑，行为与历史逐位一致
python modules/知识库索引/kb_rag.py search --query "你的问题" --topk 5
```

两个开关都不设 = 回到默认行为（零代码改动的回滚开关）。

## 依赖

- 向量塔模型需自行准备（`bge-small-zh` / `bge-m3`），模型目录可由 `KB_BGE_MODEL_DIR` / `KB_M3_MODEL_DIR` 覆盖。
- GPU 路径需要 CUDA 版 PyTorch；CPU 路径只需 CPU 版。
- 查询分词依赖 `jieba`——**必须用装了 jieba 的解释器**，否则静默降级 bigram、检索质量崩且不报错。
