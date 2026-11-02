# 知识库检索引擎（RAG Index Engine）

本地优先、隐私安全、混合检索的个人/团队知识库检索引擎。一句话：把 `wiki/`、`raw/`、`modules/` 下的 Markdown 与文档切成分块、编成向量、建好倒排，然后用 **FTS5 关键词 ⊕ BGE 语义 ⊕ 字级子串** 三链路融合召回，再用 **bge-reranker 交叉编码器精排**，得到带引用的答案。

> 本引擎是 [`knowledge-base-from-zero`](https://github.com/DongHangZhao/knowledge-base-from-zero) 的检索内核。方法论与门户见仓库根 README。

---

## 架构

```
原始文档(wiki/raw/modules)
   │  split_chunks()  480 字断句吸附 + 垃圾过滤 + 同文档去重
   ▼
chunks 分块表 ──┬─► chunk_bge      BGE-small-zh 512 维稠密向量（中文单语）
                ├─► vec_m3        BGE-M3 1024 维多语向量（跨语言路由，默认关）
                ├─► chunks_fts    FTS5 倒排（关键词 + 标题加权）
                └─► chunks_trigram 字级 n-gram（防「一句话也搜不到」）
                          │
                    search_unified()
        ┌─────────────────┼─────────────────┐
     FTS5 召回        BGE 语义召回      字级子串召回
        └─────────────────┼─────────────────┘
                  RRF 倒数排名融合（W_KW/W_VEC/W_SUBSTR 权重可调）
                          │
                  bge-reranker 交叉编码器精排（GPU fp16，32s→0.85s）
                          ▼
                    带引用的最终结果
```

- **三链路融合**：关键词、语义、字级子串各查各的，RRF 融合，覆盖面远超单路。
- **跨语言路由**：查询判为外文时切到 BGE-M3 多语塔（`KB_VEC_ROUTE=1`），中英文混合库也能搜。
- **重排序**：bge-reranker-v2-m3 交叉编码器，GPU fp16，30 文档精排约 0.85s（CPU 约 32s）。
- **垃圾过滤**：分块阶段丢 CSV 空行 / base64 串 / PPT 模板标记 / 日志重复行，并同文档去重。
- **可自验**：周自检（R@5、空检率、引用准确率）+ 回归测试 7 套件 87 断言。

---

## 目录结构

```
modules/知识库索引/
├── kb_index.py        分块 + 入库 + 构建 CLI（build / search / stats / raw-scan）
├── bge_encoder.py     BGE-small-zh 与 BGE-M3 向量编码
├── bge_reranker.py    bge-reranker 精排（GPU fp16，可降级 CPU）
├── kb_rag.py          统一检索入口（search_unified：三链路融合 + 精排）
├── rechunk_all.py     全量重切（影子表原子换表，支持回滚）
├── reencode_gpu.py    重切后 BGE 塔重编码（GPU）
├── reencode_m3.py     重切后 M3 塔重编码（GPU）
├── vector_backfill.py 缺失向量补齐（幂等）
├── build_vec_m3.py    构建 BGE-M3 多语向量
├── ingest_raw.py      原始文档（docx/pdf/xlsx/html）元数据入库
├── _garbage_filter.py 分块垃圾过滤 + 同文档去重
├── eval_rag.py        检索评测（调用 metrics，支持 --k 任意 cutoff）
├── metrics.py         检索评测纯函数（hit-rate@k / MRR@k / NDCG@k / precision@k，可单测）
├── mcp_server.py      零依赖 stdio MCP 服务（暴露 kb_search / kb_stats 工具）
├── weekly_selfcheck.py 周自检六件套
├── run_tests.py       回归测试
├── VERSION / CHANGELOG.md
├── state/             kb.db（运行时生成，gitignore）
└── models/            bge 模型目录（运行时放置，gitignore）
```

---

## 快速开始

```bash
# 1) 准备 venv
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2) 放置模型（二选一）
#    - 把 bge-small-zh 放到 modules/知识库索引/models/bge-small-zh/
#    - 或设环境变量：KB_BGE_MODEL_DIR / KB_M3_MODEL_DIR / KB_RERANK_MODEL_DIR
# 重排模型放 modules/知识库索引/models/bge-reranker-v2-m3/

# 3) 放语料：把你的 Markdown 放到仓库根的 wiki/ 、raw/ 、modules/ 下
#    （仓库自带 wiki/ 示例，可直接体验）

# 4) 构建索引（统一入口：分块 + FTS + trigram + BGE 向量一次完成）
python modules/知识库索引/kb_rag.py build

# 5) 检索
python modules/知识库索引/kb_rag.py search --query "混合检索怎么做" --topk 5
```

---

## 配置（环境变量，全部可选，均有合理默认）

| 变量 | 默认 | 说明 |
|---|---|---|
| `KB_DB_PATH` | `state/kb.db` | 索引库路径 |
| `KB_BGE_MODEL_DIR` | `models/bge-small-zh` | 中文语义向量模型 |
| `KB_M3_MODEL_DIR` | `models/bge-m3` | 跨语言向量模型 |
| `KB_RERANK_DEVICE` | `auto` | 精排设备；`cpu` 可强制回落 |
| `KB_VEC_ROUTE` | `0` | 跨语言路由开关（1=开） |
| `KB_TOKENIZER` | `auto` | 分词器：auto/jieba/bigram |
| `KB_W_KW` / `KB_W_VEC` / `KB_W_SUBSTR` | `2.0/1.5/0.5` | RRF 三路权重 |
| `KB_RRF_K` | `60` | RRF 常数 |
| `KB_RERANK_WIN` | `400` | 进入精排的候选数 |

---

## 质量保障

```bash
python modules/知识库索引/run_tests.py          # 回归 7 套件 / 87 断言
python modules/知识库索引/weekly_selfcheck.py   # 周自检：R@5 / 空检率 / 引用准确率
python modules/知识库索引/eval_rag.py           # 题集评测 hit-rate@k / MRR / Recall@k
```

---

## MCP 服务（让智能体直接检索知识库）

把检索引擎暴露成标准 MCP（Model Context Protocol）工具，Claude Code / 任意 MCP 客户端可直连检索。**零额外依赖**——手写 JSON-RPC 2.0 over stdio（不用官方 SDK，因目标环境未装 `mcp`/`fastmcp` 且出网受限）。只暴露**只读检索**，不外泄写入或隐私（返回内容来自已脱敏的 wiki 合成层）。

```bash
# 起服务（stdio，客户端自动拉起）
python modules/知识库索引/mcp_server.py
# 指定索引库：KB_DB_PATH=/path/to/kb.db python modules/知识库索引/mcp_server.py
```

Claude Code 配置示例（`项目根/.mcp.json` 或 `~/.claude.json` 的 mcpServers）：

```json
{
  "mcpServers": {
    "kb": {
      "command": "python",
      "args": ["modules/知识库索引/mcp_server.py"],
      "env": { "KB_DB_PATH": "modules/知识库索引/state/kb.db" }
    }
  }
}
```

工具：
- `kb_search(query, topk=5, mode="fused", use_rerank=True)`：三链路融合检索，返回相关文档路径 / 标题 / 得分 / 摘要片段。
- `kb_stats()`：返回 docs / chunks / chunk_bge 统计。

> 语义路（vec）与精排（rerank）需 torch + bge 模型（GPU venv）；缺失时自动降级为关键词 / 字级融合，检索照常可用。
> 协议层有单测覆盖：`python modules/知识库索引/_test_mcp_server.py --stdio`

---

## 路线图（进行中）

- [ ] **增量索引**：把 `vector_backfill.py`（只补缺失向量，已存在且幂等）接进 `kb_rag.py build` 自动跑，替代 2h 全量重切
- [x] **MCP 服务**：零依赖 stdio MCP server（mcp_server.py）已上线，暴露 kb_search / kb_stats
- [ ] **查询改写 / HyDE**：轻量查询改写 + 可选假设文档嵌入
- [x] **正式评测 harness**：抽离可单测的 metrics.py（hit-rate@k / MRR@k / NDCG@k）；LLM-judge（faithfulness_judge.py 已存在）待接入评测集
- [ ] **CI / Release**：GitHub Actions 跑离线单测 + 周自检，自动发版

---

## 协议

KBL-1.0（知识库从零到一开源许可 v1.0）：不可商用、强制署名、相同方式共享。版权人署名 **FallenPetal**。详见仓库根 `LICENSE` / `LICENSE.zh-CN.md`。
