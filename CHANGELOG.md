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

### 多平台发布产物补齐（W15 续 · 2026-11-17）

- **10 个历史 Release 全部补齐 assets**：v1.3.0/v1.3.1/v1.4.0/v1.15.0~v1.20.0 每个挂载
  `kb-from-zero.zip`（按各 tag `git archive` 单独打，内容对应版本）、`kb-hub.exe`、`kb-hub-linux`、
  `kb-hub-macos`。此前这些 Release 只有 GitHub 自动生成的 Source code (zip/tar.gz)。
  - **决策理由**：库主明确要求历史版本也补齐多平台产物。源码按 tag 单独打以保证版本内容对应；
    桌面二进制是运行时门户工具、读用户自己的 inventory，与知识库版本弱耦合，用当前 master 的
    `.github/workflows/build-assets.yml` 构建一次上传所有 Release，避免为每个老 tag 重复构建
    （老源码没有 Android 壳，无法打 apk）。
  - **取证**：所有源码 zip 解压后 `privacy_scan --no-assets` 0 BLOCK/0 WARN；二进制从干净
    CI runner 构建（非本机），ELF/Mach-O/PE 头校验通过；`gh release view` 逐版本核对 assets。
- **CI 三平台构建修复**：`kb-hub.spec` 原先被 `.gitignore` 的 `*.spec` 规则排除、从未入库，
  CI checkout 后报 `Spec file not found` 三平台全挂。加 `!kb-hub/kb-hub.spec` 例外使其入库，
  并去掉 spec 注释里的本机绝对路径（避免隐私门禁 BLOCK）。重跑 CI 三平台 success。
- **Android APK 构建链修复完成**（详见 HANDOFF §11）：p4a 交叉编译 CPython 经 13 轮取证，
  收敛到 **p4a 2024.1.21 + Python 3.11.9 + NDK 25c** 稳定组合（新版 2026.5.9 默认 Python 3.14
  的 PEP517 隔离 wheel 构建必缺 Cython；老版 webview bootstrap 自带 JNI、无需列 webviewjni；
  orientation 只接受 portrait/landscape）。第十三轮 CI `android-apk` job success，产出
  `kb-hub-android.apk`（11 MB，完整自带后端：内置 CPython + 本地 HTTP server + WebView），
  已挂载全部 Release。构建链代码/配置全部入库，可一键复现。

### 修复（W15 · 2026-11-17）

- **署名统一为 `落花飞雪·`**：全仓 38 个文本文件 / 53 处 `FallenPetal` → `落花飞雪·`
  （含代码头、文档、评测集示例实体、隐私词表白名单）。README（中/英）与 NOTICE（中/英）
  门面补「项目名 + 作者 + KBL-1.0 + 原始仓库链接」四要素署名行；法律文本 LICENSE 的 Copyright 行
  保持法律惯例不动。
  - **决策理由**：库主 2026-11-17 裁决——`落花飞雪·` / `FallenPetal` / `DongHangZhao` / 库主真名
    四个均认可作署名，不是非删某个；门面四要素需填一个字面值，本次取 `落花飞雪·`（不碰真名与账号
    关联的笔名，最贴「真名不公开」长期红线），非否定其余三个。仓库 URL 里的 `DongHangZhao`
    是地址、不是署名，保留不动。历史署名备份在库外，可随时换回任一。
  - **边界**：二进制缓存 `.db`/`.pyc` 仍含旧字符串，但全被 gitignore 挡住、不入库；幂等脚本两次验证。
- **同步生产侧 W14 修复**：`modules/知识图谱/llm/llm_kit.py` 的 `chat()` 加 `use_cache` 参数
  （默认 True 不改现有语义），`cli.py` 的 degrade 用例显式 `use_cache=False`——避免历史缓存把
  「开关 off 应降级」短路成 `reason=cache`，导致 C7 门禁在发布版假红（同步前 8/1，同步后 off 前提 9/9）。
  - **决策理由**：按「生产/发布双库同步」规则（准则 §44），修复属每次必同步项；效果好不好都要搬，
    不是可选项。只合并功能点、不整文件覆盖（两库另有差异）。
- **outputs 展示 JSON 处置**：`w9-privacy-git.json` / `w9-ci-report.json` 把真实业务词、管理员名、
  本机绝对路径换成中性示例，保留结构与计数后入库；`w9-privacy-full.json`（全库扫描快照）脱敏后仍
  保留 `block:49` 计数与 hits 结构，属元信息暴露、展示价值低，库主 2026-11-17 裁决**直接排除**
  （移出库外备份 + `.gitignore` 精确条目），不入库。
  - **决策理由**：扫描快照会把命中的真实上下文一并存下，属「结果本身是泄露源」；此前 `--git-only`
    0 BLOCK 只因文件未跟踪，`git add` 会转为 BLOCK。脱敏是入库前置；full 快照无展示价值则排除更干净。
  - **边界**：本地生产库本就没有这三个展示文件，本地检索不受影响。full.json 原件已移出库外
    临时备份区，未删。

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

## [v1.23.0] - 2026-11-20

> 主题：**召回侧补一路「文档侧问句」通道**，并把评估方法学的一条硬教训写进决策记录。

### 新增

- **文档侧问句库通道**（doc2query）：
  - `modules/知识库索引/qch_build.py` —— 离线给每篇文档生成「用户可能会怎么问」的问句，
    写入**独立** sqlite（默认 `state/kb_questions.db`），不写主库、不重跑 build、不抢 build.lock。
    两种方式：`--method llm`（效果好）/ `--method rule`（零成本兜底，仅跑通流程）。
  - `modules/知识库索引/qch_channel.py` —— 检索侧通道：FTS5（jieba 分词）+ bge 向量双路，
    经 RRF 与既有三链路融合。`KB_QCH=off` 一键关停（关时零加载、与接入前逐位一致）；
    库缺失 / 模型不可用 / FTS 报错一律返回空，**绝不阻塞主链路**。
  - 接入点在 `kb_rag.search_unified` 的 **HyDE 之后、元数据过滤之前**：此时融合池完整，
    新增文档同样经过 `--since/--path-prefix/--category` 过滤，不绕过契约。
  - **决策理由**：41%~47% 的失败题是「答案就在库里、但压根没进候选池」，精排够不着。
    改写/分流这类「检索前判别」已试完三条路都不显著，所以换方向做**召回侧扩充**。
    选择「文档侧生成问句」而不是「查询侧扩词」，是因为后者依赖以 query 文本为键的缓存，
    新 query 必然未命中（详见下条）。
  - **实测**（私有库 1.1 万篇 / 98 条评测题）：gt 进池 51→70（+19.39pp，McNemar b=0，
    p=3.8e-6）；延迟 +19ms；独立集 top1 40→42、MRR 0.675→0.687（p=0.82，方向正但未达显著，
    零误伤）。因此**默认开但保留一键关**，未声称「显著有效」。

### 变更

- **检索核心模块与参数注册表同步**：`kb_rag.py` / `query_rewrite.py` / `kb_index.py` /
  `bge_encoder.py` / `bge_reranker.py` / `traceability.py` 更新至与生产侧一致；
  新增 `rag_config.py` + `rag_params.yaml`（参数默认值登记表，带「改默认需要什么证据」列）。
  - **决策理由**：仓库侧此前落后约两周，缺 `recall_mult/pool_cap`、元数据过滤、
    在线编排适配器、`scope` 出海口等通用能力；这些不是环境定制，属应当收敛的部分。
  - **取证**：同步后 `run_tests.py --ci` **15/15 套件、287/287 断言通过**，与同步前基线逐项一致。
- **本机路径中性化**：GPU venv 路径改为留空即跳过；bge-m3 模型目录改为仓库内
  `models/bge-m3`（可用 `KB_M3_MODEL_DIR` 覆盖），三处口径注释互指避免静默跳过。

### 文档 / 方法学

- 新增 `docs/decisions/` 一篇：**「以 query 文本为键的离线缓存，其 Δ 不得进交付口径」**。
  记录一次实测——某扩词机制在回放集上 +26.53pp，但回放集 96/98 条命中缓存
  （缓存就是照着这批题生成的），换成未见过的新样本命中率为 0，机制实际未生效。
  判据：任何机制先跑一遍**没见过的新样本**，命中率不成立就把 Δ 标红。

### 已知边界（不粉饰）

- 问句库只覆盖生成过问句的文档；未覆盖的文档此通道不贡献分数，不是「全库都变好了」。
- 标题类规则问句（每条标题一条问句）**不进**本方案：实测独立集 top1 40→35、
  MRR −0.20，原因是「目录/总结/概述」这类标题对任何查询都命中，
  等权 RRF 下一条 1/(60+1) 就能盖过主链路第 60 名。**并表 ≠ 加权。**

## [v1.22.1] - 2026-11-19

### 新增
- W32 宿主访问收尾：demo 页面 API 改同源地址 + no-store，容器映射端口下检索可用

### 修改
- `examples/index.html`：API 常量由写死 `127.0.0.1:8788` 改为同源优先（http(s) 取 `location.origin`；
  `file://` 双击回退 8788）；错误提示改动态显示当前 API 地址；页脚补容器入口 `127.0.0.1:18788`。
- `modules/知识库索引/api_server.py`：`/` 返回 HTML 加 `Cache-Control: no-store`，重建/修复后
  浏览器刷新不吃旧缓存。
- `docker-compose.yml`：订正宿主访问注释——`0.0.0.0` 是监听地址不是访问地址；此前
  「Windows 转发层 Empty reply」的误判作废，真因见 v1.22.0。

### 修复
- 容器映射端口 18788 下页面显示「服务未启动（127.0.0.1:8788）/ Failed to fetch」：根因是页面 JS
  写死 8788，宿主并无该服务（该段被 WSL/Hyper-V 保留）。改同源后浏览器实测服务在线、检索命中。

### 决策理由
- 同源地址让同一份页面在「本地直跑 8788」和「容器映射 18788」两种入口下都不用改代码；
  只在 `file://` 场景回退固定端口，保住单文件双击即用的交付口径。
- 加 no-store 是因为这类 UI 修复最容易被浏览器缓存掩盖，刷新看到旧页面会被误判成「没修好」——
  宁可多一次网络请求。
- 不改宿主映射端口：18788 已验证可用，8788 在 Windows 保留段会 bind forbidden，保留现状风险最低。

### 已知问题
- 已做浏览器实测（服务在线 + 检索命中），未做长时并发压测；生产库未发布、未改动。

### 对应提交
- `2505852`（页面同源 / no-store / compose 注释）

---

## [v1.22.0] - 2026-11-19

### 新增
- **W32 容器化部署**：`Dockerfile`（python:3.11-slim / 非 root 10001 / `WITH_VECTOR` 开关）、
  `docker-compose.yml`（宿主回环绑定 + db/模型只读挂载 + 只读根文件系统）、
  `.dockerignore`（构建上下文挡掉 raw/隐私库/模型/密钥）、
  `tools/verify_docker_assets.py`（8 项离线门禁：上下文隐私扫描 / 代码在上下文内 / 启动命令文档化）。
- `api_server.py` 新增 `KB_API_HOST` 变量：容器内可绑 `0.0.0.0`，本地直跑默认仍 `127.0.0.1`。
- `.gitignore` 排除 `snapshot.py` 生成的 `_versions/` 本地快照目录。

### 修改
- `README.md` 新增「可选：容器化部署」章节：build/up/search/stop 命令、三条前提、端口映射说明。
- `api_server.py` 的 HOST 由硬编码改为读环境变量（默认值不变，安全默认不破）。

### 修复
- **宿主访问 `Empty reply`（连接建立后 0 字节即断）**：真因是服务在容器内只绑 `127.0.0.1`，
  Docker 端口转发从容器外部进入的连接被秒断。compose 设 `KB_API_HOST=0.0.0.0` 解决；
  宿主侧仍锁 `127.0.0.1:18788`，不对外发布。nginx 最小化对照（映射 18081 宿主 curl 200）
  先证明转发通道健康，避免误判为 WSL/Docker 环境问题。

### 决策理由
- **为什么容器化**：JD 把 Docker/生产部署列为硬项或优先项；本地 demo 跑通不等于换台机器能复现，
  容器化把「一条命令起服务」做成可交付能力。
- **为什么 db/模型走挂载而非烤进镜像**：库天天长、模型 2.3G，烤进去每次重建都是浪费；
  且生产库含隐私，进镜像层就删不干净。`.dockerignore` 在上下文阶段就挡，不靠事后扫描。
- **为什么用 `KB_API_HOST` 而不是直接改成 `0.0.0.0`**：本地直接跑 REST 是高频用法，
  保持默认 127.0.0.1 的安全边界（不上云、不对外发布）；只在容器场景显式放开容器网卡，
  宿主暴露面仍由 compose 的 127.0.0.1 映射锁死。一个小变量同时满足两种场景。
- **为什么先做 nginx 对照再改代码**：此前把 Empty reply 误判为 WSL/bridge 网络冲突，
  升级 WSL、重装 Docker、换端口全部无效。对照实验 10 秒定位到应用层绑定问题——
  先分清「通道 vs 应用」再修，避免继续盲试。

### 已知问题
- `fused` 模式在无 BGE 模型时静默降级为关键词链路（能力降级、不报错，README 已写明前提）。
- 8 并发×24 短测不是长时浸泡压测（soak 未做，卡片已显式声明，不把 demo 包装成生产 SLA）。
- 发布库与生产库模块结构不同：REST 服务只在发布库，生产库无同源 `api_server.py`，
  本次改动不涉及跨库回流（W32 卡范围从一开始就限定在 kb-from-zero）。

### 对应提交
- `800d7ce`（W32 容器化 + KB_API_HOST 修复主提交）

---

## [v1.21.0] - 2026-11-19

### 新增
- `bge_reranker.py` 接入 ONNX 推理分支（`KB_RERANK_ONNX`，默认 on）：`load_onnx_session()` 懒加载 + `_rerank_onnx()` 走 onnxruntime CPU，torch 原路径逐字保留作兜底。
- 查询级缓存（`KB_RERANK_CACHE`，默认 off）：键含 query + 全部参数指纹（`_param_fingerprint`），带命中率统计 `cache_stats()` / `reset_cache()`。
- `export_rerank_onnx.py` 落库：把 bge-reranker-v2-m3 导出成 ONNX 的可复跑脚本，内建「ASCII 中转 → 中文目录」两步走 + skip 幂等守卫 —— 补上此前 docstring 引用但全库不存在的导出能力。

### 修改
- rerank 默认引擎 torch → ONNX（仅换推理引擎，不动 rerank 开/关本身、不动 W8 T1/T2/T3 分流裁决）。

### 修复
- （无）

### 决策理由
- **为何切 ONNX 默认 on**：实测 CPU 冷启动 23.8s→20.4s（省 3.4s）、稳态 3.001s→2.285s（1.31x），排序 3/3 一致、分数差 <1e-5，且缺模型/缺 onnxruntime 自动回退 torch 不崩 —— 纯收益，建了不用等于 0。ONNX Runtime 是 CPU 推理的 production-grade 标准（onnxruntime.ai 官方），业界同款 bge-reranker-v2-m3 的 CPU ONNX 是常规做法。
- **为何缓存默认 off**：生产检索是 CLI 一次性进程（每次 search 新进程、单次只调一次 rerank），进程内缓存命中率≈0，切 on 只增 hash 开销；业界「cache the head / warm requests 2–3×」前提是常驻服务 + p95 口径，不在 CLI 场景。代码保留，将来上常驻服务再 `KB_RERANK_CACHE=1`。
- **为何补导出脚本**：docstring 引用了不存在的 `export_rerank_onnx.py`，用户拿到发布版想复现 ONNX 能力会找不到工具 —— 「文件存在≠能力生效」的反面，能力必须可复现闭环。
- **逐位判据订正**：ONNX 与 torch 跨框架算子数值实现不同，bitwise 逐位相等字面不可达（实测同一二进制输入 logits 差 2.861e-6）；判据订正为「排序一致 + sigmoid 分数差 <1e-5」，留一个数量级余量，既不误判浮点噪声也能抓住真精度退化。

### 已知问题
- ONNX 模型（2.2G / 173 文件）不入库（GitHub 无 LFS），发布版用户默认回退 torch 路径；要用 ONNX 需自行跑 `export_rerank_onnx.py` 生成模型放 `models/bge-reranker-v2-m3-onnx/`。这是预期降级，不是故障。
- 缓存代码在库但默认 off（理由见上），CLI 场景无收益。
- 导出依赖 torch + onnx + onnxscript（`requirements-demo.txt` 未列，属可选能力，缺则不影响核心检索）。

### 对应提交
- 见本地 tag `v1.21.0` 与 `git log`（commit message 标注 W26 ONNX 切默认）。

---

## [v1.20.0] - 2026-11-17

### 新增
- **多平台发布产物**：补齐 Release 长期缺失的 assets——源码 zip、`kb-hub.exe`（Windows）、
  `kb-hub-linux`、`kb-hub-macos`、Android `kb-hub-android.apk`。此前 v1.3–v1.19 全部 Release
  只有 GitHub 自动生成的 Source code 归档，无任何可下载二进制。
- **Android 子项目** `kb-hub/android/`：p4a `webview` bootstrap 把 Python 解释器 + 精简 HTTP 服务
  + 前端打进 apk，离线可浏览示例门户。精简后端 `android_server.py` 只保留展示 API，
  砍掉手机端无意义的 subprocess/启动外部工具调用。`main.py`/`.p4a`/`build_apk.sh` 一应俱全。
- **`build-assets.yml`**：`workflow_dispatch` 触发，matrix 构建 Win/Linux/macOS 二进制 + p4a 打 apk，
  产物上传为 Actions artifact（不直接推 Release）。

### 修改
- `kb-hub.spec`：datas 从写死的仓库绝对路径改为基于 spec 目录的相对路径，跨平台可构建。
- `make_release_zip.py`：`SKIP_DIRS` 增加 `state` 且排除 `build*`/`dist*` 前缀目录——原脚本把
  `modules/*/state/*.json`（真实评测数据，含敏感命中上下文）和构建中间物收进 zip，
  重打后 privacy_scan 报 50 BLOCK，修复后复扫 0 BLOCK。

### 修复
- Release 产物缺失（本版核心）；跨平台构建路径硬编码；发布包夹带 state 敏感数据。

### 决策理由
- **二进制用当前 master 构建一次、上传所有历史 Release**：kb-hub 是运行时门户工具，读用户自己的
  inventory，与知识库版本弱耦合；源码 zip 则按各 tag 单独 `git archive`，保证版本内容对应。
  避免为每个老 tag 重复构建二进制（且老源码无 Android 壳，无法打 apk）。
- **apk 不直接进 CI Release**：隐私词表按红线不得入库，CI 隐私门只有 L1+L3，CI 绿 ≠ 可发布；
  产物须库主下载到本地带词表跑 privacy_scan 0 BLOCK 后再 `gh release upload`。
- **完整自带后端而非离线展示壳**：用户明确要求功能最全；p4a webview 方案官方支持，
  代价是首次构建约 20 分钟、体积较大，已如实列为已知问题。

### 已知问题
- APK 首次 CI 构建需下载 Android SDK/NDK（约 20 分钟），且 debug 签名；数据为内置示例，
  换自己的库需重新构建。移动端不支持 RAG/视频下载等本机脚本功能（返回友好提示）。
- Mac 二进制未做签名公证（source-available 项目，用户需自行允许）。

### 对应提交
- （本轮 commit 后见 v1.20.0 tag）

---

## [v1.19.0] - 2026-11-17

### 修复
- **C7 门禁假红**：同步生产侧 W14 修复——`llm/llm_kit.py` 的 `chat()` 加 `use_cache` 参数
  （默认 True 不改语义），`cli.py` degrade 用例显式 `use_cache=False`。同步前发布仓库 selftest
  8/1（`reason=cache`），同步后 off 前提 9/9、RC=0。

### 修改
- **署名统一为 `落花飞雪·`**：全仓 38 个文本文件 / 53 处旧笔名替换（含代码头、文档、评测示例实体、
  隐私词表白名单）；README（中/英）与 NOTICE（中/英）门面补「项目名 + 作者 + KBL-1.0 +
  原始仓库链接」四要素署名行；LICENSE 版权行保持法律惯例。四个候选署名（落花飞雪· / 旧笔名 /
  GitHub 账号名 / 库主真名）均获认可，门面取落花飞雪·填字面值，非否定其余，历史备份在库外可换。
- **outputs 展示 JSON 处置**：`w9-privacy-git.json` / `w9-ci-report.json` 脱敏后入库；全库扫描快照
  `w9-privacy-full.json` 因保留 `block` 计数与 hits 结构（元信息暴露、展示价值低）**排除**
  （移库外备份 + `.gitignore` 条目）。

### 决策理由
- **双库同步**（准则 §44）：生产/发布是同一项目，修复属每次必同步项，效果好不好都要搬；只合并
  功能点、不整文件覆盖（两库另有差异）。
- **署名取笔名**：四候选均可署名，门面需填一个字面值，取不碰真名与账号关联的笔名最贴「真名不公开」
  长期红线；仓库 URL 里的账号名是地址不是署名，不动。
- **扫描快照排除**：结果快照把命中上下文嵌进文件、`--git-only` 的 0 BLOCK 只因未跟踪，属泄露源；
  展示价值低的直接排除比脱敏入库更干净。

### 已知问题
- 1 条 WARN：`judge_calibration_30.csv` 未知扩展名需人工确认——已抽查内容为纯评测数据、无 PII。
- 二进制缓存 `.db`/`.pyc` 仍含旧署名字符串，但全被 gitignore 挡住、不入库。
- 本仓库 push 需库主单独授权；`release_gate.py` 自身永不 push。

### 对应提交
- 见本地 tag `v1.19.0` 与 `git log`（commit message 标注 W15）。

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
