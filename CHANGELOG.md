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

- 版本管理上云：根 `VERSION` 单一真相源、根 `CHANGELOG.md`、`CONTRIBUTING.md`、`HANDOFF.md`、
  `tools/release_gate.py`（`--check` / `--bump` / `--preflight`）、CI `version-check.yml`。
- `docs/decisions/ADR-2026-11-10-版本管理上云.md`：业界调研 + 五方案对比 + 选型理由（本地设计总稿）。

### 修复

- `release.yml` 推 `v1.17.0` / `v1.17.1` 必失败：原从 `modules/知识库索引/CHANGELOG.md` 抽 Release 正文，
  而该文件最高只到 `v1.16.0`。改为读根级 `CHANGELOG.md`，并在抽正文前先过版本一致性门禁。
- `publish_to_github.bat` 的 `TAG` 写死在 `v1.3.1`（三代之前）。改为从根 `VERSION` 读取，
  并在隐私扫描前加一道 `release_gate.py --check --preflight`。

### 决策理由

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
