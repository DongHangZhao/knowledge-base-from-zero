# 变更日志

所有**仓库级**重要变更都记录在这里。格式遵循 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)，
提交信息遵循 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/)。

> **层级口径（2026-11-10 立）**：本文件只记**仓库整体**的里程碑与跨模块决策；
> 各子系统自己的细粒度变更仍写在各自的 CHANGELOG（例如 `modules/知识库索引/CHANGELOG.md`），
> **不要**把两份混成一份，也不要在子系统日志里伪造仓库级版本段。
> `VERSION`（仓库根）是**唯一版本真相源**；`release.yml` 的 Release 正文从本文件抽取。
>
> 🔴 每个版本段**必填「决策理由」**：写不出为什么这么做，就说明还没想清楚（AGENTS §3）。
> 🔴 本文件不含真实姓名、学号、公司名、真实项目名、本机绝对路径；发布前过 `privacy_scan.py --git-only`。

---

## [Unreleased]

> 待发内容先写在这里；正式发版时由 `tools/release_gate.py --bump ... --apply` 生成带版本号的段落。
> `make_release_notes.py` 明确忽略本段，未发内容不会混进 Release 正文。

### 新增

- **就业可展示层（W10）**：`modules/adapters/llama_index_retriever.py`（LlamaIndex `BaseRetriever` 适配器）、
  `modules/知识库索引/api_server.py`（由 `mcp_server.py` **cp 副本**改造的 FastAPI REST：
  `/health` `/search` `/stats` `/ask`）、`examples/` 全链路 demo（`build_demo_index.py` /
  `demo_llamaindex.py` / `index.html` / `quickstart.md` / `eval_public.json` + `eval_public.py` +
  `EVAL.md` / `mock_llm_server.py` / `make_architecture.py`）、`requirements-demo.txt`；
  README 增加「跑得起来的 RAG demo」一节。
  - **决策理由**：v3 判「引框架重写主链路是反模式」是对的，但 JD 硬要求
    「LangChain / LlamaIndex / Dify 至少一种」+「有自己搭的知识库 demo」——缺的是
    **别人能 clone、能看见关键词、能复现的那一层**，不是算法能力。只做适配层与脱敏 demo，
    生产检索一行不改：既拿互操作关键词，又保住自研可控性。
  - **决策理由（LLM 能力必建）**：`/ask` 不是 503 占位，而是「默认 off、一键可开」的完整链路
    （开关 / 库外凭据 / 出口门禁 / sha1 缓存 / 留痕 / 降级）。本地 mock 端点让没有 Key 的人
    也能验「开起来真能跑」与缓存命中，无需联网。
  - **决策理由（架构图双轨）**：`privacy_scan.py` L3 把 PNG / SVG 一律判 BLOCK，
    所以高清 PNG 只在本机生成、由 `.gitignore` 排除，对外用同结构 Mermaid 文本图；
    **不为了让图过门禁去改安全护栏**。
  - 边界：不改 `mcp_server.py` 原版、不推生产 `kb.db` / 真实项目名、服务只绑 `127.0.0.1`、
    **非 release 不 bump `VERSION`**、零 push / 零上传 / 零发布；公开评测为弱标注，不作质量结论。
- 版本管理上云：根 `VERSION` 单一真相源、根 `CHANGELOG.md`、`CONTRIBUTING.md`、`HANDOFF.md`、
  `tools/release_gate.py`（`--check` / `--bump` / `--preflight`）、CI `version-check.yml`。
- `docs/decisions/ADR-2026-11-10-版本管理上云.md`：业界调研 + 五方案对比 + 选型理由（本地设计总稿）。
- **评测与可观测性对齐（W9）**：新增 `modules/知识库索引/w9_eval/` 六件套
  （RAGAS 指标定义本地适配 / CI 对照门禁 / 生成侧钩子与 50 行空白人标表 /
  OTel 语义 spans + 三张门禁指标 / gt 可追溯性钩子 / 单文件本地看板 + CI 编排器）、
  公开 CI `.github/workflows/eval-ci.yml`、对外口径页 `docs/评测与可观测性-W9.md`、README `eval-ci` 徽章。
  - **决策理由**：业界/JD 认 RAGAS / DeepEval / OTel 这套名词，自建六桶 + McNemar 分辨力其实更强，
    差的是「对外能被认得的口径」；但 RAGAS 官方四指标默认走 `AsyncOpenAI()`（装前核实），
    与隐私不上云 + 出口门禁写在必经处冲突 ⇒ 改走官方同时提供的 `IDBased*` / `NonLLM*` 本地变体定义，
    零装包、零外部 API、可复现；LLM 通道另建为三级开关（默认 off 一键可开），**能力必建但不默认开**。
  - **决策理由（CI 三态）**：仓库侧只有脱敏示例集、没有冻结主集与生产基线，
    `eval_gate` 本体与相对掉分判定在 CI 里必须**明确 SKIP**；生产 184 条含真实业务 query，
    绝不推上公开仓库。编排器用 `subprocess` 显式取每条 returncode + `sys.exit(main())`，
    杜绝「多行 run 一条挂了后面照跑、看上去全绿」与「把 FAIL 洗成 SKIP 冒充通过」。
  - 边界：不动既有 `ci.yml`（只加新文件）、不动 `VERSION` / tag、零 push / 零上传 / 零发布；
    生成侧分数恒标 ⏳ UNCALIBRATED（人标 gold 0 条），不代填、不写「已评测」。

### 修复

- `release.yml` 推 `v1.17.0` / `v1.17.1` 必失败：原从 `modules/知识库索引/CHANGELOG.md` 抽 Release 正文，
  而该文件最高只到 `v1.16.0`。改为读根级 `CHANGELOG.md`，并在抽正文前先过版本一致性门禁。
- `publish_to_github.bat` 的 `TAG` 写死在 `v1.3.1`（三代之前）。改为从根 `VERSION` 读取，
  并在隐私扫描前加一道 `release_gate.py --check --preflight`。

### 决策理由

- 🔴 **W10 只补「可展示层」，不动主链路**：技术深度不落后，缺的是可复现的那一层。
  备选有三：引框架重写（实体最多、不可逆）、把生产库 4.5GB 脱敏公开（含隐私正文，红线禁止）、
  适配层 + 脱敏最小闭环（实体最少、全可逆）——选第三条。真跑取证见 `HANDOFF.md` §6 与
  `examples/EVAL.md`；后者明确标注弱标注 R@5 满分**不得当质量结论**。
- 🔴 **半自动而不是全自动**：库主红线要求「上传云端先问」「人验签字」「完成 ≠ 发布」，
  `semantic-release` 那种 push 即发版会绕过人审；`release-please` 的 Release PR 仍属自动对外动作。
  因此只吸收它们的**规范**（Conventional Commits / Keep a Changelog / 发版前 PR 复核），
  把「自动」降级成本地脚本 + 人工确认，等明确要自动化时加一个 workflow 即可升级。
- 🔴 **不移植 `snapshot.py` 的全量磁盘快照**：Git 本身就是完整历史且更省空间；
  云端仓库再复制一份 `_versions/` 是冗余，还会把向量库/模型/媒体带进来。
  生产库继续用 `snapshot.py` 做本地快照与 L1/L2/L3 分级，两边各管各的，不混。
- 🔴 **不启用 Git LFS**：库主明确「没有云端大容量的需要」，GitHub LFS 免费额度仅 1 GB/1 GB 月流量，
  且已入库大文件迁 LFS 需重写历史、打断所有 clone。重资产留在本地，LFS 记为未来可选项。
- 🔴 **只增不删、不重构**：现有 `ci.yml`（离线回归 + 隐私终审）与 `release.yml` 的「找不到版本段就 fail」
  是好设计，保留；本轮只补一致性校验，不推翻工作流。

### 已知问题

- ⏳ `version-check.yml` 与改后的 `release.yml` 尚未在真实 GitHub Actions 上跑过（需 push 才触发，push 待授权）。
- ⏳ 本次零 push / 零上传 / 零发布；远程 tag 与 Release 均未创建。

### 对应提交

- 本轮「版本管理上云」改动为**本地 commit、未 push**；具体 hash 见 `git log --oneline`。
  （`release_gate.py --check` 校验版本口径，不校验 commit 号。）

---

## [v1.18.0] - 2026-11-11

### 新增

- **LLM 能力层工程化（W12）**：`modules/知识图谱/llm/` 新增 6 个可运行脚本 ——
  `llm_path_guard.py`（在线入口零引用守卫，含 5 类自测）、`b3_audit.py`（边质检全量 + 只加属性回写）、
  `rewrite_ab.py`（冻结人标题集配对改写 A/B：McNemar 精确 p + 延迟增量 + 快照守卫）、
  `report.py`（按 task 的命中率 / p50 / p90 / tokens 报表）、`rate_probe.py`（429 与超时连发压测）、
  `batch_runner.py`（断点续跑 + 失败队列 + 指数退避）；另加 `sync_to_kb_from_zero.py`
  （生产模块 → 本仓的**可复跑镜像**，替代手工复制 28 文件，`--check` 幂等）。
  `release_verify.py` 的 C7 闸门扩判「在线入口零 `import llm_kit`」，接进交付门禁。

### 修改

- `llm/llm_kit.py`：`cache_key` 支持可选 `prompt_version`（**不传时拼串逐位不变**，不动存量缓存），
  `cache_get` 支持 TTL（默认 0＝永不过期），trace 记录版本键。
- `llm/enhance.py` / `llm/b3_review.py`：提示词模板内容 sha1 即版本号，改模板自动失效旧缓存。
- `llm/cli.py --selftest`：增加版本键断言；`llm/README.md` 重写为「离线边界 + 六段命令 + 判据→产物」对照表。

### 修复

- 无代码缺陷修复；本轮修的是**能力缺口**（全量结论 / 成本可测 / 断点可续 / 发版可复跑四件事此前只到手工程度）。

### 决策理由

- **能力必建、开关可控、默认 off**：LLM 环节缺的是「有没有」，不是「省不省」；但**绝不接在线同步链路**——
  单次改写 p50 已是秒级，接进主链路会把检索延迟推到不可接受。所有能力只在离线 / 批处理侧，
  在线入口由静态守卫机器判死（`llm_path_guard.py`），不靠人记。
- **缓存键向后兼容而不是直接换新键**：换键会让存量缓存与他人调用一起失效，属于「动原版」事故；
  改成可选参数后，不传版本的调用行为逐位不变，只有显式传版本的调用才换键。
- **B3 只加边属性、不改图拓扑**：结论归人审，AI 不代删；写回前先备份 `kg_w6.pkl`。
- **A/B 加快照守卫**：实跑时撞上并行窗口全量重建索引（4.4 万 → 13.9 万块），进程热重载处段错误；
  即便跑完，两臂语料也不同源。因此报告写入前比对索引指纹（`bge_model.built_at` / `chunks` / `docs`），
  **前后不一致直接标 `INVALID`**，不允许把漂移快照上的数字当结论。
- **模型不自证**：C4 评测校准 / 题面 / 真值三处仍不建 LLM，人标未完成前不许模型当裁判。

### 已知问题

- 改写 A/B 的题集固定为冻结的 50 条人标题面，负样本桶（gt 为空）天然排除，覆盖率有限；
  结论只用于「要不要离线预生成改写候选」，不用于宣称检索质量提升。
- 429 阈值不是常量（历史 trace 有 10 条 429，本轮 6 连发 0 条），生产仍保持限速与单并发；
  不做「单次没触发就上调并发」的推断。
- 边质检的「碎片 / 真知识」判定是描述性统计，无人工 gold，不作显著性断言。

### 对应提交

- 本轮 commit（推 `master`）+ tag `v1.18.0` 后由 CI 生成 Release 正文。

---

## [v1.17.1] - 2026-11-09

### 修复

- 知识图谱子系统 v1.0.1：闸门口径订正 —— 修「沙盒一删，评测段整段不打印」。

### 决策理由

- 评测段属于交付证据，沙盒清理不应把它一起吞掉；门禁宁可 fail loud，也不静默少打印一段。

### 对应提交

- `4636fea`

---

## [v1.17.0] - 2026-11-09

### 新增

- 独立知识图谱子系统 v1.0.0：脱敏 / 可复算 / 带交付闸门。

### 决策理由

- 图谱作为检索之外的第二层派生视图，必须与主索引解耦，才能单独复算与回滚。

### 对应提交

- `77ebae5`

---

## [v1.16.0] - 2026-11-04

### 修改

- 清理 93% 的评测污染 + 图谱实验归档 + 领域词典抽取（检索引擎侧细粒度记录见
  `modules/知识库索引/CHANGELOG.md`）。

### 决策理由

- 评测集被污染时，任何分数都不具备决策效力，先清数据再谈指标。

### 对应提交

- `b75ea65`

---

> 更早的 `v1.3.x ~ v1.15.0` 属于检索引擎版本线，逐段明细见
> `modules/知识库索引/CHANGELOG.md`；根级日志自 2026-11-10 起只记仓库级里程碑，不追溯补写。
