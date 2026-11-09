# W6 · 独立知识图谱子系统（GraphRAG）

> **独立 / 脱敏 / LLM 能力必建·默认 off / 可展示**。对应 `窗口交接工程/14-就业导向RAG工程化补齐/W6-知识图谱子系统-必做.md` §三 A→F。  
> 交付物口径：**单文件 `index.html` 双击即用**。  
> 进度：A→F 全段执行完毕；2026-11-07 补交付闸门，终态 **`PASS_WITH_WAIVERS`**
> （机读 `release_manifest.json` / 人读 `RELEASE.md`，由 `release_verify.py` 真读证据生成，不是手写结论）。
> 逐段取证原文在 W6 卡 §七，本节只记稳定结论。

---

## 🔴 红线（A 段要求，先写死再建图）

1. **本子系统不连接生产 `kb.db`**。只读写本目录 `state/kg_w6.db`（独立 sqlite，schema 由 `build_corpus.py` 自建）。
2. **`KB_KG` 保持默认关闭**，`kb_rag.py` 一个字不改，**不接主检索链路**。W5 判定（30 条多跳 `p=1.000000`、184 条主集 `p=0.814529` → 不上主链路）保持有效，不推翻、不重跑。
3. **`state/_kg_sandbox.db` 已于 2026-11-09 按 L3 删除**（库主授权：过期索引快照 docs 8363/chunks 111441，可视化只读 `state/kg_w6.pkl`、`kb_rag` 零引用）。删前备份在库外；取证见 `release_manifest.json` 的 `sandbox.deleted=true`。⚠️ 空间要等库主清备份才真正回收。
4. **不用真实业务 / 患者 / 个人信息建图**。语料只取 `<KB_ROOT>/wiki/技术/` 方法学页与 `<OSS_ROOT>/docs/` 公开示例，逐篇过脱敏；不把图数据上传任何云端服务。
5. **LLM 能力必建、开关默认 off**（2026-11-09 口径，升级 11-07「按需可用」）。**隐私不上云仍是红线**：只允许把 `privacy_scan` 已判 0 BLOCK 的脱敏块发到库主自配的 OpenAI 兼容端点；凭据走库外环境变量、不入库不入 git；结果缓存落盘保证离线可复现；无 key / 超时 / 429 一律降级到本地规则。详见文末「LLM 能力层」。
6. **不改 `kg_*.py` 原版**：本目录全部是 `cp` 副本，只改副本的参数区/扩展层。
7. **不为「图谱看起来有用」挑评测题**：题目先冻结再跑，判定规则提前写死。

---

## 目录

| 路径                                                           | 作用                                                            |
| ------------------------------------------------------------ | ------------------------------------------------------------- |
| `build_corpus.py`                                            | 脱敏语料 → 独立 sqlite（含 B3 零 LLM 关系抽取、段落节点表）                       |
| `kg_extract.py` / `kg_gate.py` / `kg_ppr.py` / `kg_terms.py` | cp 副本：实体抽取 / 门控 / PPR 建图与检索                                   |
| `build_kg_viz.py` / `export_kg_graph.py`                     | cp 副本：出图与单文件 HTML 渲染                                          |
| `kg_build.py`                                                | B1–B5：同义词边 / 段落节点 / 有类型边 / 覆盖统计 / 增量更新                        |
| `kg_search.py`                                               | C：独立 CLI，三模式 `1hop` / `ppr` / `hybrid`，`--explain` / `--json` |
| `eval_multihop.py`                                           | E：多跳评测 + 配对 McNemar 精确检验                                      |
| `corpus/manifest.json`                                       | 语料清单（相对路径 + 块数 + 剔除原因分类，不含敏感词）                                |
| `eval_sets/`                                                 | 版本化多跳评测集 + manifest                                           |
| `state/`                                                     | 运行时独立库与扫描原始返回（`.gitignore` 忽略，不提交）                            |

---

## 复现（解释器必须用 venv 版）

```bash
PY="E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
cd <本目录>

"$PY" build_corpus.py --fresh                 # ① 脱敏语料 → 独立库
"$PY" kg_build.py --stats                    # ② 建图，落盘规模三数 + 耗时
"$PY" kg_search.py --mode ppr --query "..." --explain --json
"$PY" eval_multihop.py --set eval_sets/kg_multihop_v2026.11.json
"$PY" build_kg_viz.py --in <graph.json> --out <index.html> --sub "全局图"
```

隐私门禁（A 段必过，返回原文见 W6 卡 §七）：

```bash
git -C <本目录> add -A
"$PY" <OSS_ROOT>/tools/privacy_scan.py --root <本目录> --git-only --json
# 判据：rc=0 且 files_scanned>0 且 block=0
```

交付闸门（一条命令复核全部证据，收尾必跑）：

```bash
"$PY" release_verify.py                # 含写隔离探针（真跑 kg_build.py --stats）
"$PY" release_verify.py --no-probe     # 快速复跑，复用已有探针证据
# 判据：overall=PASS_WITH_WAIVERS；出现 BLOCKED 则 rc=3，不许放行
```

---

## A 段取证摘要

- 语料：`docs_kept=33` / `chunks=565` / `entities=1075` / `typed_edges=33` / `passages=565`，其中 22 篇做了软脱敏，55 篇整篇剔除（专名 54、业务类别 6，有重叠）。清单 `corpus/manifest.json`。
- 脱敏两级：**硬剔除**（库外词表专名 + 业务类别词）＋**软替换**（绝对路径 / 邮箱 / 号码 / 密钥 / 原始目录引用 → 占位符）。具体专名从库外 `~/.kb_privacy_terms.txt` 读取，**不落库、不进 git**。
- `privacy_scan.py --git-only`：`rc=0`、`files_scanned=10`、`rules=16`、`terms_loaded=12`、`block=0`、`warn=0`、`ok=True`。
- cp 副本里原版带专名的 3 处（`kg_ppr.py` ×1、`alias_table.json` ×2）已在**副本**内替换为占位符，`py_compile rc=0`；原版 sha1 未变。

---

## 与主流方案的差异（F 段补表）

| 维度    | MS GraphRAG | LightRAG | HippoRAG 2 | 本子系统                       |
| ----- | ----------- | -------- | ---------- | -------------------------- |
| 关系来源  | LLM 抽取      | LLM 抽取   | LLM + 同义边  | **零 LLM 中文规则模板 + NPMI 共现** |
| 增量更新  | 重建          | 增量       | 增量         | 增量                         |
| 成本    | 高           | 中        | 中          | **当前零调用**（非红线，按需可开） |
| 段落节点  | 有           | 有        | 有          | 有（B2）                      |
| 全库覆盖  | 全量          | 全量       | 全量         | 34 篇（脱敏语料池上限，见 B4 说明）      |
| 对外可展示 | 需自部署        | 需自部署     | 需自部署       | **单文件 HTML 双击即用**          |



---

## B–E 段实测（返回原文在 `state/` 与 `eval_sets/`，均可复跑）

| 段       | 结果                                                                              | 取证                         |
| ------- | ------------------------------------------------------------------------------- | -------------------------- |
| B1 同义词边 | 新增 0 条，但对 1 条已有共现边**提权 + 打 `synonym` 标记**（副本的 `has_edge` 分支不进 `added`，只数新增边会漏报） | `state/graph_stats.json`   |
| B2 段落节点 | 加 565 个 `P::<chunk_id>` 节点 + 短语→段落边；**4/4** 真实查询的 PPR 实体分布发生变化                  | `state/ablate_b1b2b3.json` |
| B3 有类型边 | 零 LLM 中文规则模板 33 条（`依赖/用于/导致/构成/需要/对比/判定`）；**1/4** 查询实体分布变化                      | 同上                         |
| B4 规模   | `1650 节点 / 5783 边 / 33 篇 / 565 块 / 0.539s`；B5 增量后 `1666 / 5794 / 34 篇`          | `state/graph_stats.json`   |
| B5 增量   | `rebuild=false`，只 merge 16 节点 / 11 边，`0.53s`，未重建全图                              | `state/incremental.out`    |
| C CLI   | `1hop` / `ppr` / `hybrid` 各真跑一条 `--explain --json`，`rc=0`；hybrid 向量兜底补 1 条真实路径  | `state/cli_*.json`         |
| D 可视化   | 全局图 150 节点/734 边、局部 2 跳图 150/537；4 个 HTML 结构化目检全过                               | `outputs/知识图谱/W6/`         |

**B4 为什么没到 2,000 篇**：语料池只有「开源侧公开示例 + 生产方法学页」33–34 篇。生产库 8,933 篇里含 `raw/隐私/`、`raw/个人信息/` 正文（W6 卡 §二 红线），不能拿来建对外可展示的图。这是**红线的代价，不是没跑完**，不靠切块充数。

**hybrid 的兜底口径（不冒充 dense）**：字符 bigram TF-IDF **稀疏向量** + cosine，零模型依赖、零 API 调用；不 import `kb_rag.search_unified`，也不碰生产 BGE 模型与 4.5GB 库。要接 BGE dense 需另加开关。

---

## 评测（100 条 · 三路对照 · 配对 McNemar 精确检验）

题库 `eval_sets/kg_multihop_v2026.11.json`：`n=100`，gt 追到 `path + chunk_id + chunk_idx`，sha1 `8f6b89260364`；**`human_judged=0` / `UNCALIBRATED`**。题目先 `--freeze` 再 `--run`，run 校验 sha1，不许事后挑题。统计轮子复用 `outputs/知识库运维/eval_stats.py::mcnemar_exact`。

| 系统                | any@5  | all@5  |
| ----------------- | ------ | ------ |
| baseline（词法/向量兜底） | 0.6300 | 0.2300 |
| +1hop             | 0.7800 | 0.3000 |
| +ppr              | 0.7800 | 0.2400 |

| 配对               | 口径    | Δ        | McNemar p | 不一致对 | 按写死规则的结论   |
| ---------------- | ----- | -------- | --------- | ---- | ---------- |
| baseline → +1hop | any@5 | +15.00pp | 0.035698  | 45   | 值得接        |
| baseline → +ppr  | any@5 | +15.00pp | 0.035698  | 45   | 值得接        |
| baseline → +1hop | all@5 | +7.00pp  | 0.229523  | 25   | 能力齐备、增益不显著 |
| baseline → +ppr  | all@5 | +1.00pp  | 1.000000  | 23   | 能力齐备、增益不显著 |

判定规则写死在 `eval_multihop.py::RULES`：`Δ ≥ +3pp 且 p < 0.05 → 值得接`，否则「能力齐备、增益不显著、不接主链路」。**两种都算完成，没有为凑好看改判据。**

**不确定性（2026-11-07 补，业界要求「给区间不给单点」）**：Wilson 95% —— baseline any@5 `0.63 [0.5322, 0.7182]`、`+1hop/+ppr` `0.78 [0.6893, 0.8500]`；配对逐题差 Δ=+15pp 的 95%CI `[2.18, 27.82]`（下界 > 0，与 `p=0.035698` 一致），all@5 两对 CI 分别为 `[-2.70, 16.70]` / `[-8.40, 10.40]`（均含 0，与 p 不显著一致）。计算与导出在 `release_verify.py::calibration_package`，表见 `RELEASE.md`。

### 🔴 口径裁决：漂亮的 p 值不等于可以接主链路

上面「值得接」只在**这个独立脱敏子系统内部**成立。题库是半自动标注（人标 0 条、UNCALIBRATED、题面模板化）、语料是公开示例 + 方法学页，与 W5 的 184 条主集**不同分布**。W5 实测（30 条 `p=1.000000`、184 条 `p=0.814529` → 不上主链路）**继续有效、不推翻、不重跑**：`KB_KG` 保持默认关闭、`kb_rag.py` 一个字不改、不接主检索链路。本子系统的存在是为了「能力齐备 + 可展示 + 简历写得上」，不是给主链路翻案。

---

## 交付闸门（2026-11-07 补 · 机器判定，不是手写结论）

库主追问「做完了吗 / 做好了吗」→ 判定：**动作做完了，交付凭据没做好**。上一轮证据散在 `state/`、时点性、结论靠手写；本轮按业界做法补 `release_verify.py`，只取三家**判据**不取实现：SLSA-L1 provenance（build 与 index_governance 两类独立事件）／golden-set 校准与区间／Gitleaks 式可复跑机读 gate。纯标准库、闸门自身零调用、不联网、不写生产库（这是闸门实现的事实，不是全局红线）。终态 `verify_rc=0 / overall=PASS_WITH_WAIVERS`。

| 项 | 状态 | 结论 |
| --- | --- | --- |
| C1 写隔离 / 判据 8 | **PASS** | 探针真跑 `kg_build.py --stats rc=0`，生产 `kb.db` 前后 `(size, mtime_ns)` 完全一致，变更仅 `state/` 2 文件、库外 0；**本轮变更清单里没有任何生产脚本**。mtime 时序争议改由 `provenance.events` 事件分离裁决；跨窗口对原版的改动单独登记，不拿历史 sha1 误判本轮 |
| C2 文本盲区 5 件 | **PASS** | 按业界 registry/storage 分离登记为 `non_indexable_artifact`。🔴 **口径更正：这不是索引欠账，不改 `INDEX_TARGETS` 才是正确做法**，此项不再待拍板 |
| C3 沉淀历史欠账 | **PASS** | 9 条 FAIL 全为 11-02～11-04 历史页，本轮 0 新增 |
| C4 评测校准 | **LIMITED** | Wilson 95% + 配对 Δ 95%CI 已量化；`human_judged=0` **不许**升级为 PASS，数字仍不可当公开评测用 |
| C5 语料规模 | **PASS** | 34 篇定位 demo-scale、pin 到 corpus snapshot（**事后补记**已在 manifest 标注），不切块充数 |
| C6 隐私门禁 | **PASS** | `rc=0 / 23 文件 / block=0 / ok=True`；词表版本化 + 90 条疑似专名建议交人审（不自动写词表） |

🔴 **仍需库主点头（红线，不动手）**：D1 4.38GB 沙盒（L3 减法，完整清单含 `LinkType/Target` / inode / nlink 判定）、D2 若坚持把数据文件硬登记 `INDEX_TARGETS`、D3 对外发布（上传云端前必问）、**D4 是否开 LLM 增强（2026-11-07 新增）**。四项各附一键命令，见 `RELEASE.md`。

WARN 1 条：`calibration/judge_calibration_30.csv / 未知扩展名（需人工确认）`——扫描器对 `.csv` 的提示、不阻断；这份表本来就是给库主人标的人审队列，如实保留、不擅自消除。

🔴 **本轮闸门抓到的外部变动（2026-11-07 07:10:16）**：`modules/知识库索引/kb_rag.py` 从 `2a5dc28c362f` 变为 `bb7197876b61`（目录内有 `.bak-20261107-W8-pre` 备份）→ 归因**并行窗口 W8**，非本子系统所为。本轮不回滚、不擅自改生产脚本，已在 `release_manifest.json::external_changes` 与 `RELEASE.md` 登记交库主核实。跨窗口改动静态快照发现不了，这正是交付闸门存在的理由。

## 交付物

- 双击即用：`outputs/知识图谱/W6/index.html`（全局）、`ego-2hop.html`（局部）；`-nowm` 双份按 L3 规则另存。模板本身无水印/logo，故两份 sha1 一致。
- 中文标签：HTML 走 CSS 字体族 `"Microsoft YaHei"`（系统即 `msyh.ttc` 对应的字族），不是位图出图，**没有**本地 `msyh.ttc` 传参这一步；此处如实标注，不冒充执行了出图铁律。


---

## LLM 能力层（2026-11-09 口径：能力必建 · 开关可控）

库主升级口径：「不只是能用和该用的问题，是 AI 时代这个功能**有没有**的问题。可以暂时不用，但**不能没有**。」据此 W6 不再把 LLM 当作「可选加分项」，而是**必须交付的能力件**：`llm/` 已建成并验过，交付状态默认 `KG_LLM_ENABLED=0`。

**落地**：`modules/知识图谱/llm/`（client / redact / cache / trace / 降级 五件套，纯标准库，可 `cp -r` 复用）。用法与实测见 [`llm/README.md`](llm/README.md)。

| 环节 | 代码 | 默认开关 | 依据 |
|---|---|---|---|
| B3 有类型边质检 | `llm/b3_review.py` + `llm/enhance.py` | off | 抽样 12 条中 11 条规则边被判为碎片（`也不该 --构成-> AI`、`32 --构成-> 低置信降级查询`）→ 规则版只解决了「有类型」这个形状，没解决「是不是知识」 |
| 关系类型判定（供 B3 扩展） | `llm/enhance.py::llm_relation` | off | 难题集 n=10：规则 0.3 → LLM 0.7；提示词补否定/因果规则后由 0.6 升 0.7 且 0 降级 |
| 查询改写 | `llm/enhance.py::llm_rewrite` | off | 能力已建，尚未对照评测（W4 的活）⏳ |
| C4 评测校准（人标 0） | —— | **不建** | ARES/PPI 的前提是人标校准判分器；**LLM 自当金标＝循环论证**，造出来的是「更权威的假数字」。可让 LLM 出候选标注交库主确认，人仍是终裁 |
| 题面生成 / gt 判定 | —— | **不建** | 会动题库 sha1 与评测口径，属新实验，须单独立项；不是省成本，是换口径必须重做全链路 |

**「不建」与「暂不启用」的区别（判据 3）**：C4/题面/gt 三项是**方法论上不能由模型自证**，不是「不值得花钱」；其余环节一律按「必须有代码」交付。这条区分写死了，避免下次又拿「省成本」当理由砍能力。

**凭据纪律**：密钥只在库外环境变量 `AGNES_API_KEY` / `OPENAI_API_KEY`，本仓库任何文件、git、日志、缓存里都没有 key；`state/` 已 gitignore。历史遗留的明文密钥曾在会话中出现，**建议轮换**。

**全库口径订正**：`AGENTS.md` §5.11、准则 v3.65 §39、AGENTS 家族三件套、项目/用户级记忆、topics、`00 号:56` / `窗口分工 README:70` / `W4:78` / `W8:89` / `W10:11` / `W6` 卡 / `rag-param-tuning` 技能卡均已按新口径订正；历史日志按 append-only 追加订正注记。
