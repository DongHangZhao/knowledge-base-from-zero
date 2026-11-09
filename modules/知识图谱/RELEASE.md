# W6 · 交付验收清单（机器生成，勿手改）

> 由 `release_verify.py` 真读证据生成；结论不是手写。复跑：`"E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe" release_verify.py`
> 机读原件：`release_manifest.json`。访问日期 2026-11-07。

## 总体判定

**`PASS_WITH_WAIVERS`** — 技术闸门全过（含 C7 LLM 能力层已建成并默认 off）；剩 3 项红线需库主书面许可 + 1 项运行开关待定，见下表。

## 技术闸门（脚本判定）

| ID | 状态 | 结论 | 判据 |
|---|---|---|---|
| C1-写隔离/判据8 | **PASS** | 图谱构建只写本目录；生产 kb.db 未被子系统触碰；索引治理 build 是另一类独立事件。 | 探针前后生产库 (size, mtime_ns) 一致 + 本轮变更清单里没有生产脚本 + 库外 0；跨窗口对原版的改动单独登记为 external_changes，不拿历史 sha1 误判本轮 |
| C2-文本盲区5件 | **PASS** | 5 个 JSON/示例文件按业界 registry/storage 分离登记为「非检索资产」，不是索引欠账 | 每类文件写明为何不进全文索引；核心资产（README/py/结论页）已进索引且可检索 |
| C3-沉淀历史欠账 | **PASS** | sediment_guard 的 9 条 FAIL 逐条核对为 11-02～11-04 历史欠账，本轮 0 新增 | 报告时间戳早于本轮新建文件；本轮新页已挂 > Raw: 并进 index.md/log.md |
| C4-评测校准 | **LIMITED** | 统计不确定性已量化（Wilson 区间 + 配对 delta 95%CI）；人标 0 条，数字仍不可当公开评测用 | human_judged=0 → 只允许 LIMITED；校准包已生成且责任人=库主；30 条不足以做 PPI |
| C5-语料规模 | **PASS** | 34 篇定位为 demo-scale 方法验证，pin 到 corpus snapshot digest；不切块充数 | 题库 sha1 与语料清单 sha1 一并登记；规模不足的原因写明为红线代价 |
| C6-隐私门禁 | **PASS** | 词表版本化 + 机读 gate（files_scanned>0 且 block=0）+ 疑似专名建议交人审 | rc=0 且 files_scanned>0，防 0 文件假通过；对外发布另列需许可项 |
| C7-LLM能力层 | **PASS** | LLM 环节已建成可运行代码，默认 off；关掉开关自测全过（不联网），开启后实跑过并有对照证据。 | 五件套齐 + cli.py --selftest rc=0（KG_LLM_ENABLED 不设）+ 默认 enabled=false + A/B 与 B3 质检落盘；「不建」只允许用于方法论禁止模型自证的环节（C4/题面/gt） |

## 待拍板（AI 不动手）

| ID | 档位 | 需要什么 | 一键命令 |
|---|---|---|---|
| D1-4.38GB沙盒 | L3 减法 · 已执行 | 已无需拍板：2026-11-09 库主书面授权「不影响可视化、属冗余」并已执行 | `python release_verify.py --no-probe   # 复跑核对 manifest.sandbox.deleted=true` |
| D2-INDEX_TARGETS硬登记 | 改索引机制 | 库主拍板 | `改 INDEX_TARGETS 后跑 orphan_scan.py --strict 复核` |
| D3-对外发布 | L2 云端/公开 · 已授权常态执行 | 已授权：库主要求「每次更新加模块都要发版」，避免 GitHub 与生产分叉；每轮发布前仍报备一次范围 | `见 W12 §F：同步 → 版本三件套 → privacy_scan --git-only → git push/tag` |
| D4-LLM启用范围 | 运行开关（能力已建） | ① 常驻开启已执行（用户级 KG_LLM_ENABLED=1，2026-11-09）；② B3 全量 41 条质检待开窗口跑（W12 §B）；③ 主检索链路仍建议不接 | `setx KG_LLM_ENABLED 1   # 已执行（User 作用域，新进程生效）` |

### D1 · 4.38GB 沙盒已按 L3 删除（2026-11-09）

- 路径：`modules/知识库索引/state/_kg_sandbox.db` → **已删除**（含 `-shm` / `-wal` 伴生文件）。
- 依据：库主书面授权「不影响可视化知识图谱、属冗余可删」；删前已判非 junction、非硬链接、`nlink=1`、与生产库不同 inode（铁律 11）。
- 实测冗余：过期索引快照 docs 8363 / chunks 111441（生产 9057 / 128505）；可视化读 `state/kg_w6.pkl`，`kb_rag` 零引用。
- 备份：删前已复制到库外（路径库主自持，不入库、不入 git）。
- ⚠️ 空间口径：备份 + 回收站仍占约 4.4GB，确认无误后清除备份才真正回收。

## 评测：带不确定性，不给单点

| 系统 | any@5 | Wilson 95% | all@5 | Wilson 95% |
|---|---|---|---|---|
| baseline | 0.63 | [0.5322, 0.7182] | 0.23 | [0.1584, 0.3215] |
| 1hop | 0.78 | [0.6893, 0.85] | 0.30 | [0.2189, 0.3959] |
| ppr | 0.78 | [0.6893, 0.85] | 0.24 | [0.1669, 0.3323] |

配对逐题差（与 McNemar 精确检验互补）：

| 配对 | 口径 | Δ | 95% CI | McNemar p |
|---|---|---|---|---|
| baseline → +1hop | any@5 | +15.00pp | [2.18, 27.82] | 0.035698 |
| baseline → +ppr | any@5 | +15.00pp | [2.18, 27.82] | 0.035698 |
| baseline → +1hop | all@5 | +7.00pp | [-2.7, 16.7] | 0.229523 |
| baseline → +ppr | all@5 | +1.00pp | [-8.4, 10.4] | 1.000000 |

🔴 `human_judged=0 / UNCALIBRATED`。校准包：`calibration/judge_calibration_30.csv` + `calibration/标注规范.md`。30 条不足以做 ARES/PPI（业界 150–200 条），**数字不可当公开评测、不可据此接主链路**。

## LLM 能力层（C7 证据，默认 `KG_LLM_ENABLED=0`）

- 关系判定对照（n=10，gold 人工判定，仅描述性不作显著性断言）：规则 **0.30** → LLM **0.70**（+40.0pp），降级调用 0 次。`state/llm_ab_20261109_133003.json`
- B3 边质检（抽样 12/41）：LLM 有效判定 12 条，其中判为无效碎片边 **11** 条。`state/b3_llm_review_20261109_133332.json`
- 关掉开关后同一批输入重放：命中缓存、结论逐条一致（`state/llm_trace.jsonl` 可核）。「不建」仅限 C4/题面/gt 三处（方法论禁止模型自证），其余环节一律必须有代码。

## Provenance（可复算）

- 语料快照：`corpus/manifest.json` sha1 `a6a58abeaede`，33 篇 / 565 块。
- 题库：`eval_sets/kg_multihop_v2026.11.json` sha1 `8f6b89260364`。
- 原版脚本 7 个 sha1（历史基线 2026-11-06）：外部变动 1 个（非本轮，见下段）：kb_rag.py 2a5dc28c362f→bb7197876b61。
- 产物 sha1（4 HTML + 2 JSON）：见 `release_manifest.json::provenance.subject`。
- 写隔离探针：`kg_build.py --stats` 真跑 rc=0，生产 `kb.db` 前后一致=True，变更文件 2 个且全在本目录。

🔴 **判据 8 时序冲突的正解**：不是「哪个 mtime 才算数」，而是`graph_build` 与 `index_governance_build` 是**两类独立事件**，各自有 actor / writes / touches_prod_db 字段。前者 False，后者 True 且注明「收尾索引治理，非图谱子系统写入」。见 `provenance.events`。

### 本轮发现的外部变动（如实记账，不回滚）

- `kb_rag.py`：历史基线 `2a5dc28c362f` → 现值 `bb7197876b61`，mtime `2026-11-07 07:10:16`；非 W6 子系统写入；并行窗口改动（目录内有同名 .bak-20261107-W8-pre 备份），本轮不回滚、不擅自改，仅登记待库主核实。
🔴 跨窗口改动**静态快照发现不了**，这正是本闸门存在的理由。本轮不回滚、不擅自改生产脚本（回滚需库主许可），只登记并交库主核实。

## 业界参考（标题 + URL + 访问日期）

- RAG Evaluation Metrics: Retrieval, Reranking, Generation — <https://slavadubrov.github.io/blog/2026/05/10/rag-evaluation-metrics/>（访问 2026-11-07）
- Building a Golden Dataset for RAG Evaluation: A Systematic Engineering Approach — <https://www.querystack.tech/post/building-a-golden-dataset-for-rag-evaluation-a-systematic-engineering-approach-78fc31>（访问 2026-11-07）
- RAG Accuracy and Hallucinations: How to Measure Them (OPORA) — <https://agentsunrise.pro/articles/rag-accuracy-hallucinations-measurement>（访问 2026-11-07）
- Ragas 官方文档 — <https://docs.ragas.io/>（访问 2026-11-07）
- ML Model Provenance: Signing and Verification Guide (SLSA / in-toto) — <https://aidefense.dev/posts/securing-the-ml-model-supply-chain>（访问 2026-11-07）
- 稳健可靠的 MLOps CI/CD 流水线（模型护照 / Model Passport） — <https://beefed.ai/zh/reliable-mlops-cicd-pipelines>（访问 2026-11-07）
- AI Supply Chain and Model Provenance (Ch.23) — <https://www.socratopia.app/library/security-engineering-ai-era-en/chapter-23>（访问 2026-11-07）
- Gitleaks: Pattern-driven secret scanner for git repositories and file content — <https://zairalabs.ai/guide/tools/gitleaks>（访问 2026-11-07）
- Best secrets scanning tools for Git repos and pre-commit — <https://safeguard.sh/resources/blog/best-secrets-scanning-tools-for-git-repos-and-pre-commit>（访问 2026-11-07）
- Microsoft Presidio（PII 检测与匿名化 SDK） — <https://aicompliance.ai/tools/presidio>（访问 2026-11-07）
- Model Registries（元数据与产物分离） — <https://layerwiseml.com/topics/engineering/model-registries.html>（访问 2026-11-07）
- Policy-as-Code Pipelines: Secret, Dependency, and SBOM Scanning in Enterprise CI/CD — <https://jamutech.com/policy-as-code-pipelines-secret-dependency-and-sbom-scanning-in-enterprise-ci-cd>（访问 2026-11-07）

## 术语对照（全中文对照 · 外文无官方中文名者给说明性译名，不音译猜）

| 原名 | 中文说明 | 全称 / 读法 |
|---|---|---|
| SLSA | 软件制品供应链安全分级框架 | Supply-chain Levels for Software Artifacts，读作「S-L-S-A」 |
| in-toto | 制品链路完整性证明格式 | 拉丁语「整体」，读作「因-图-图」 |
| provenance / attestation | 溯源记录 / 可验证声明 | 「普罗维南斯」/「阿特斯泰申」 |
| Ragas | RAG 自动评测框架 | Retrieval Augmented Generation Assessment |
| ARES / PPI | 自动评测框架 / 预测驱动推断 | Automated RAG Evaluation System / Prediction-Powered Inference |
| Wilson interval | 威尔逊二项比例置信区间 | 统计学家 Edwin B. Wilson |
| McNemar | 麦克尼马尔配对检验 | 统计学家 Quinn McNemar |
| Gitleaks | Git 仓库密钥扫描器 | git + leaks |
| Presidio | 微软开源 PII 检测与匿名化 SDK | 西班牙语「驻防地」 |
| artifact registry | 制品登记表 | 与全文检索文档库分离 |
