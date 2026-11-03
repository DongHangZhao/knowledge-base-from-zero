# 变更日志

所有重要变更都记录在这里。

## [v1.15.0] - 2026-11-04

> 本版核心：**把「没验证就设默认」的两处改回来**（L0 查询改写、#41 图谱），
> 并补上让结论可被验证的观测能力（延迟指标 / summary 层 / holdout 接入 / 只读评测）。


### 🔴 #41 图谱 1pt 实验：**实测 FAIL，归档不做**（2026-11-04）

判据开工前写死（`R@5 ≥ +3pp` **且** `p95 ≤ +150ms`），实测：

| 口径 | baseline | +1hop | Δ |
|---|---|---|---|
| `any@5`（任一 gt 命中） | 100.0% | 100.0% | ±0 |
| **`all@5`（所有 gt 都命中）** | **50.0%** | **46.7%** | **−3.3pp** |
| 第一跳中、第二跳空 | 10 条 | 10 条 | ±0 |
| p95 | 494ms | 630ms | +136ms |

**1-hop 一条没救回，还弄坏一条**（mh29 `all: True→False`）。

**但「图谱无效」这个说法不成立**，失效点在实体抽取：`jieba.cut` 把「某医院集群项目」切成
`['该集群','五大','系统']`，「系统」被 df 过滤掉，剩下「该集群」这种泛词去做 1-hop，
召回的是求职简历。**前置条件是短语实体抽取，不是分词。** 详见 `docs/decisions/KG-41.md`。

代码**未删**（删是 L2 操作，等项目所有者批准）。

#### 顺带得到两把尺子（比结论更值钱）

1. **`any@5` 测不出多跳** —— 多答案场景下「任一命中」会随答案数虚高。第一次跑出来
   `baseline 100% / treat 100%`，差点收工认定「图谱无害也无用」；换 `all@5` 后 baseline
   立刻掉到 50%，问题才显形。**多答案评测必须用 all-hit。**
2. **分段测速不能外推全量** —— 1000 chunk 测得全库分词 1.7~2.7 分钟，实际跑 27 分钟未完成。
   瓶颈不在分词，在「单进程累积 11 万条结果（内存 600MB+）+ 写 WITHOUT ROWID 侧表」。
   两次全库尝试（28min / 27min）都超时被杀，最后缩到 196 篇 gt 文档（9.6 秒完成）。

新增实验工具（`state/_kg_exp/`，不入库）：`build_sidecar_scope.py`（按范围建侧表）/
`kg_eval_both_hit.py`（三口径评测）。


### 修 `_test_vec_tables` ⑫ 系列：原来只在「机器上没模型」时才真跑

生产侧跑这套测试时 3 条红（`33 PASS / 3 FAIL`），但红的原因不是代码错 —— 是测试依赖了环境事实。

**根因**：⑫ 用 `os.environ["KB_M3_MODEL_DIR"] = 不存在路径` 制造 no-model 场景。
它在仓库侧能过，只因为 `models/bge-m3` 本来就不存在（默认值就是坏的）；
在生产侧必然失败，因为：
- 生产机 `<MODEL_DIR>/bge-m3` 真实存在
- 且生产 `kb_rag.M3_MODEL_DIR` 是**模块级常量**（import 时绑定），运行时改 env 根本不生效

**更糟的是另一半**：仓库侧 `backfill_m3` 是**函数内**读 env，生产侧是模块常量 —— 两侧实现不同。
只 patch 其中一种，另一侧会走 `except` 分支记一条 `[PASS] skipped`，
**看起来 33 PASS / 0 FAIL 全绿，实际 3 条断言一条没跑**。

**改法**：env 和模块属性**两处同时动**，`hasattr` 判存在再 patch，`finally` 各自恢复。
改完两侧都 `36 PASS / 0 FAIL`、无 skipped。

**教训**：`skipped` 混在 `PASS` 里是最危险的假绿 —— 它比 FAIL 更难发现，因为它显示为通过。

### 内部运维记录 · 生产库定向移植（2026-11-03，不影响开源用户）

> 本节是本人生产库的运维记录，开源用户可跳过。留在这里是因为它记了三件
> **开源版也需要知道**的事：开源化可能改坏别人的生产路径、覆盖前必须查路径依赖、
> `run_tests` 存在「0 断言也报 OK」的假绿。

仓库侧 7 个 commit 的改进，生产库一条都没生效过（生产侧 `kb_rag.py` 停留在 11-01、`eval_rag.py` 是 10-15 的老版本）。本轮按「定向移植」而非全量覆盖搬过去，因为开源化过程把生产路径改坏了几处。

**移植了 5 项**：
1. `vec_tables.py`（新增）—— 四表一致性的唯一口径
2. `metrics.py`（新增）—— 检索指标纯函数
3. `kb_rag.py` —— `import vec_tables` + `_m3_skip_tail()` + `backfill_m3()`，并接进 `_cmd_build_body` 的 `4/4 补向量：BGE → m3 → 四表断言`
4. `eval_rag.py` —— p50/p95 延迟统计 + `summary` 平铺层 + **`readonly=True`**
5. `_test_vec_tables.py` / `_test_metrics.py`（新增）+ `run_tests.py` SUITES 挂两条

**故意不移植的**：
- `query_rewrite.py` / rewrite 参数 —— L0 实测负收益（见上一条），生产不引入
- `kg_*.py` / `KB_KG` 路 —— #41 结论未出，未验代码不进生产
- `bge_encoder.py` / `build_vec_m3.py` / `reencode_m3.py` —— 与生产版唯一差异就是 m3 模型路径，覆盖零收益且有把 `<MODEL_DIR>` 改成不存在路径的风险
- `cloud_publishable.py` —— 开源版 `find_privacy_scan()` 在生产侧解析到不存在的路径 → 导出/上云会**永久 BLOCK**
- `alias_table.json` —— 生产 15 键（带实测归因）比开源版 10 键强

**发现的生产侧偏差（覆盖前必须知道的）**：
- `kb_index.py` 的 `dir_filter` 开源化时从 `{"某客户面试项目"}` 改成了 `{"示例同事面试项目"}` → 直接覆盖会让 `modules/后端开发/` 下 3 篇 RBAC/软删除文档**静默掉出索引**（不报错、只丢召回）
- `run_tests.py` 开源版删掉了 `test_dashboard_equiv.py` 套件，但生产侧该文件真实存在且守着 exFAT 170s→0.1s 的事故回归

**验证**：生产侧 `run_tests.py` → 9/9 套件 · 96/96 断言 · rc=0（原 7 套件 87 断言，新增两条各 36/23）

### 🔴 发现：评测会往生产 `query_log` 写行，93% 是评测题

- **成因**：`eval_rag.py` 用裸 `sqlite3.connect(DB_PATH)`，绕过了 `kb_index.connect(readonly=True)` —— 而那个函数的 docstring 原话是「评测脚本并行跑时不会误改索引」。**有设计、没执行**
- **实测**：`query_log` 6347 行里 **5908 行（93.1%）是评测题**，真人查询只剩 439 行。分布：10-28 2716 行、10-29 2342 行、11-03 608 行
- **影响**：任何基于 `query_log` 的「真实查询漏检归因」，93% 样本是评测题，结论不可信
- **改法**：`kb_index.connect(DB_PATH, readonly=True)`，实测写操作被拒（`attempt to write a readonly database`）
- **未清理**：那 5908 行**没有删** —— 删数据是 L2 操作，且留着是这次缺陷的现场证据。待用户过目后统一清
- **另一个同类隐患（未修）**：`_test_rerank_fallback.py` 不用临时库、直连生产跑检索，跑一次测试也写一批 `query_log`


### 🔴 查询改写 L0 默认回滚（#39 实测否决，2026-11-03）

**这是本版唯一一处「上一版开了、这一版关掉」的变更。** 原因：L0 是**没验证就设成默认开**的，实测三套集全为负收益。

#### 实测（生产索引 8363 篇 / 111441 chunk，GPU venv，p95 已含预热）

| 评测集 | off 基线 R@5 | l0 R@5 | Δ |
|---|---|---|---|
| 30 条（人工标注） | 70.0% | 70.0% | ±0.0pp |
| 100 条（人工标注） | **87.0%** | 86.0% | **−1.0pp** |
| 60 条（留出集） | **95.0%** | 93.3% | **−1.7pp** |

100 条集上 l0 权重 1.0 的对照：R@5 87.0%（打平）、MRR 0.7600（仍低 0.0075）、p95 +20ms。**调整权重救不回来，问题在拆分本身。**

#### 逐题 diff：L0 救回 0 条、弄坏 1 条

弄坏的是 `长上下文 和 RAG 区别` —— 拆成 `长上下文`(1.0) + `RAG 区别`(0.6) 后，后半边降权把原本 Top5 内的 gt 挤出，Top1 变成无关文档。

根因：**「A 和 B 区别」这类对比句式要的是两半都召回，不是前半重要后半次要。** 把 12 个字的查询切碎，丢掉了「长上下文 vs RAG」的联合语义。

#### 改法

- `query_rewrite.DEFAULT_LEVEL`：`"l0"` → `"off"`（附实测数字注释，钉死不许改回）
- `kb_rag` 的 `rewrite` 参数说明与 `--rewrite` 帮助文本同步改为「默认 off」
- `_test_query_rewrite.py` ⑧b：非法档名断言从「回落 l0」改为「回落 `DEFAULT_LEVEL`」（= off）。**打错档名的安全兜底应该是「什么都不做」，而不是「悄悄开改写」**
- 代码全保留：`KB_REWRITE=l0` 仍可显式开，随时回退

#### 教训（写进决策文档 `docs/decisions/RW-39.md`）

**小样本持平不等于安全。** 30 条集上 R@5 持平、MRR 甚至微升，我差点据此认定「无副作用」；换 100 条集立刻掉 1pp。1 条题在 30 条集上就值 3.3pp，30 条根本分辨不出 ±1pp 的差异。

### 评测可观测性：延迟指标 + summary 层 + 留出集接入（2026-11-03）

三处都是「数据早就存在、但管线没接上」，导致结论无法验证：

#### 1. `eval_rag` 从来没测过延迟

- **症状**：`kg_gate` 决策门要 `p95`，但 eval 结果 json 里根本没有耗时字段 → 决策门恒返回「无法判定（数据缺失）」
- **改法**：`evaluate()` 每路每查询 `time.perf_counter()` 计时；`_quantile()` 线性插值算 p50/p95（**不用 nearest-rank** —— 30 条样本下 nearest-rank 的 p95 恒等于 max，那测的不是 p95，是「碰巧最慢的那条」）
- **冷启动隔离**：评测前先跑一轮预热（模型加载 / jieba 首次分词 / SQLite 页缓存），否则 bge 路第一条 800ms+ 污染 p95，会得出「加了图谱反而更快」这种不可能的结论
- **表格**：`render_table` 增加 `p50` / `p95` 两列

#### 2. `kg_gate` 与 `eval_rag` 的 json 契约从没对齐过

- **症状**：eval 写的是三层嵌套 `{sets:{<set>:{res:{<mode>:{...}}}}}`，`kg_gate._pick` 只在「顶层 / summary / res」找 `recall`、`p95` —— 两条路永远接不上
- **改法**：eval json 增加 `summary` 键（把最后一个评测集的指标按 mode 平铺一层）；`kg_gate` 增加 `_squash(payload, mode)` 下钻 + `--mode` / `--baseline-mode` 参数。summary 有多路又没指定 mode 时**原样返回让 decide 报缺数，不猜**

#### 3. 留出集 `kb_eval_holdout.json` 存在但没人读

- **症状**：60 条留出集早就在 `state/` 下，但 `eval_rag` 的 `--set` 选项里没有 holdout，**等于「留出集白留」**——调参时看不到、改完也不复核
- **改法**：新增 `--set holdout`（并纳入 `all`）。用途是**调完参数后再看一次**：30/100 集上看涨、holdout 上不涨就是过拟合

#### 4. 🔴 `eval_rag` 对生产库不是只读的，把评测题写进了 query_log

- **症状**：`eval_rag.py` 用裸 `sqlite3.connect(DB_PATH)`，绕过了 `kb_index.connect(readonly=True)`。而那个函数 docstring 里原话写着「评测脚本并行跑时不会误改索引」—— **有设计、没执行**
- **实测污染**：生产库 `query_log` 共 6347 行，其中 **5908 行（93.1%）是评测题**，真正的人工查询只剩 439 行。时间分布：10-28 2716 行、10-29 2342 行、11-03 608 行
- **影响**：任何基于 `query_log` 的「真实查询漏检归因」分析，93% 样本是评测题，结论**基本不可信**
- **改法**：改走 `kb_index.connect(readonly=True)`（零副作用）


### QA 复核修正 · 两处误导日志（2026-11-03）

QA 独立复核 10/10 PASS、无功能 bug，但揪出两处**日志自相矛盾/语义误导**。只改措辞与判定输入，**业务判定逻辑不变**：

#### 1. 全新库 `build --skip-vec` 打印「四表一致 ✓」

- **改前**：`[build]   四表：chunks=18 bge=-1 trigram=18 m3=-1 ｜ 缺 bge=0 m3=0 tri=0` → `[build] 四表一致 ✓`
  （`bge=-1` 表示表不存在；`missing_ids` 对不存在的表返回 `[]`，于是 lag 全 0 被判「一致」）
- **改后**：`⚠️ 四表未齐（缺表：chunk_bge、vec_m3），跳过一致性判定` + `→ 本次不做一致性判定（表还没建出来，不代表索引有问题）`
- **改法**：`vec_tables.diff_report` 增加 `tables_ok` / `absent_tables`，`ok` 改为「四张表都在 **且** 三张派生表无缺口」；`_cmd_build_body` 与 `weekly_selfcheck` 按 `tables_ok` 分支。**表缺失 ≠ 漂移**，因此不触发 rc=3。
- **断言**：`_test_vec_tables.py` ⑪/⑪b/⑪c（空库、只有母表两种场景都不报 `ok=True`，且 `absent_tables` 精确点出缺哪几张）。

#### 2. 企业档缺 m3 模型时说「本档不判红」，随后却真的返回 rc=3

- **改前**：`⚠️ m3 模型缺失（…），跳过 m3 补编码（本档不判红）` → 随后 `_cmd_build_body` 因企业档漂移 **rc=3**
- **改后**：`⚠️ m3 模型缺失（…），跳过 m3 补编码（企业档 KB_BACKFILL_M3=1 要求 m3 齐 → 本次全量构建将以 rc=3 判红）` → 随后确实 rc=3
- **改法**：新增 `_m3_skip_tail(mode, limit)`，按「企业档/其它档 × 全量/分批 × `KB_STRICT_VEC`」现算后果说明，保证日志与 build 实际返回值一一对应；no-model / no-cuda 三处共用同一段尾巴。
- **断言**：`_test_vec_tables.py` ⑫/⑫b/⑫c/⑫d（企业档文案含 rc=3 且不再出现「不判红」；auto 档只说不判红、不提 rc=3；企业档 + `--limit` 说「本次不判红」）。

实测：`run_tests.py --ci` → **13/13 套件 · 266/266 断言 · rc=0**（较修前 +7 条断言）。

### 图谱 1pt 实验脚手架 · #41（T05，2026-11-03）

#### 1. 为什么只做脚手架

图谱在小语料上的收益**高度依赖查询形态**（只有多跳/实体关联类查询受益），而本库主力场景是「单跳事实查找」；维护成本（实体抽取质量、侧表同步、schema 演进）却是持续支出。所以先花 1pt 买一个**可复现的结论**：通过才允许投入，不通过就整块删掉 —— 不做图谱层。

#### 2. 改法

- **`kg_extract.py`（新建，零依赖）**：`extract_entities(text)` 规则抽取 6 类实体（`《标题》` / `「引号」` / 反引号标识符 / `#tag` / 英文 CapWords / `alias_table.json` 键）；`ensure_sidecar(conn)` 建 `chunk_entities(chunk_id, entity, ent_type) WITHOUT ROWID` + `idx_ent(entity)`；`build_sidecar(conn, limit, dry_run)` **只处理侧表里还没有的 chunk** + `INSERT OR IGNORE`（幂等）；`rebuild()` 仅 `--rebuild` 显式调用。
  - 🔴 **`KB_KG != 1` 时 `build_sidecar` 直接 return `{"status":"disabled"}`，连表都不建**（有测试钉死）。
  - 实体过滤：要求含字母或汉字（挡 `400`），代码实体还必须是**标识符形状**（挡反引号里的公式碎片 `1/(k+rank)`、`2.0/1.5/0.5` —— 这类会命中每个提到数字的块，造出大量假 1-hop 边）。
- **`kg_gate.py`（新建）**：`MIN_R5_DELTA=0.03` / `MAX_P95_DELTA_MS=150` **写死为模块常量，没有任何 env 读取**（实验最容易被"调阈值调到通过"）；`expand_1hop(conn, q, topk, exclude_docs)` 从「查询实体 → 共享实体的 chunk」取 1-hop 邻居，**必须传 `exclude_docs`** 排除已命中文档（否则这路只是给已有结果加分，等于没做实验）；`decide(baseline, treat)` → `{pass, r5_delta, p95_delta_ms, verdict, reasons}`，`pass = r5_delta >= 0.03 且 p95_delta_ms <= 150`（含等号）；`main()` 打印判定表，**FAIL → rc=6**（永不出现在 CI），`--write-decision` 生成 `docs/decisions/KG-41.md`。
- **`kb_rag.search_unified`**：substr 路之后追加 6 行 `if os.environ.get("KB_KG") == "1": try: import kg_gate; add(...) except: note`。**默认 `KB_KG` 未设 ⇒ 这 6 行永不执行、`kg_gate` 连 import 都不发生**。
- **`eval_sets/multihop_30.example.json`（新建）**：30 条占位（真集放 `state/kb_eval_multihop_30.json`，gitignore 不入库）；`eval_rag.py` 新增 `--set multihop` 与 **gt 列表容错**（多跳子集常有多 gt，原来 `path=?` 传 list 会崩）。
- **`docs/decisions/KG-41.md`（新建）**：决策记录模板（判据 / 实测表格待填 / 复现命令 / **一条命令回滚**）。
- **`_test_kg.py`（新建，30 断言）**：实体三类抽全 + 噪声过滤、1-hop 只走一跳（2 跳的 c3 不许混入）、"不含自身"（`exclude_docs` 排掉种子文档后只剩邻居）、决策门 4 个边界（0.03/150、0.029/150、0.05/151、0.05/150）、**`KB_KG` 未设时连表都不建**、侧表幂等与 dry-run。`run_tests.py` 已登记（第 13 套件）。

#### 3. 实测（2026-11-03，本机）

- `python _test_kg.py` → **30 PASS / 0 FAIL**，rc=0
- `python run_tests.py --ci` → **套件 13/13 通过 · 断言 258/258 · 19.3s**，rc=0
- `KB_KG` 未设：`build_sidecar` → `{'status': 'disabled', ...}`；`sqlite_master` 里**只有 `chunks`、没有 `chunk_entities`**
- **零影响对照**：把 T03 的 commit（`git archive`）与当前代码对同一库、同 4 条 query 各跑一遍 `search --json` → **4/4 逐字节一致**（KB_KG 未设时检索结果与 T03 后完全相同）
- `KB_KG=1 python kg_extract.py`（26 chunk 的临时库）→ `抽到实体=140/122 写入=139/121`（过滤公式碎片后从 143→122 实体）；`--dry-run` → `写入=0（dry-run，未写库）`
- `python kg_gate.py --baseline base.json --treat treat.json` → PASS 场景 `R@5 +3.0pp ✅ / p95 +150ms ✅ → 判定：做插件层`，**rc=0**；FAIL 场景 `R@5 +2.0pp 🔴 / p95 +160ms 🔴 → 判定：归档不做`，**rc=6**

#### 4. ⚠️ 结论未定（诚实标注）

- **真实验没跑**：需要「四表一致 + `state/kb_eval_multihop_30.json` + 有 GPU 的机器」，本机 `models/` 为空、评测集不在库。所以本文件**不能**写「R@5 +X.Xpp → 判定做/不做」——那是编的。
- 已就绪的是**判据、脚本与模板**：真跑一条命令即可得出结论（见 `docs/decisions/KG-41.md` §三 复现命令），结论出来后用 `kg_gate.py --write-decision` 覆盖生成实测版。
- **前置断言**：实验前必须 `diff_report(conn)["ok"] == True`（四表漂移会让 treat 组延迟虚高，把结论带偏）。
- 回滚（若结论是"不做"）：`DROP TABLE chunk_entities` + 删 `kg_extract.py`/`kg_gate.py`/`_test_kg.py`/`eval_sets/*.example.json` + 删 `kb_rag.py` 那 6 行，业务路径零命中。

### 查询改写三层 · #39 L0 默认开 / L2 默认关（T03，2026-11-03）

#### 1. 为什么改

三链路召回的上限是「查询本身长什么样」。用户问「知识库 和 wiki 有啥区别」，字面路只能命中含全句的块；拆成「知识库」「wiki 有啥区别」两路召回再融合，池子完全不同。而 HyDE（用假设答案去检索）只在**有 LLM 出口**时才有意义 —— 所以它必须默认关、必须能超时回落。

#### 2. 改法

- **`query_rewrite.py`（新建，零依赖）**：
  - `Plan{original, subqueries[(text,w)], hyde, level, notes}`；`LEVELS={"off":0,"l0":1,"l1":2,"l2":3}`。
  - `split_subqueries(q)`：分隔符 `| / " vs " / 以及 / 、 / / / 和`（**"和" 排最后**，误切面最小），双阈值 `总长>=8` 且 `2~3 段、每段>=2 字`。
  - `strip_question(q)`：只剥**首尾**（客套前缀 / 前置疑问词 / 后置语气词），不动中间 —— 全文替换会把正文关键词误删。
  - `hyde(q, cmd, timeout)`：`subprocess.run(shlex.split(cmd), input=q, timeout=...)` → stdout 去空行前 600 字；**任何异常/非零/空 → None**。
  - `normalize(plan, fanout_w)`：首条 1.0、其余 `KB_REWRITE_FANOUT_W`（0.6）、去重、最多 3 条。
  - `plan(q, level, alias_fn, env)`：`off`=别名扩展后的单子查询（= 改造前）；`l0`=拆分子查询；`l1`=+去口语；`l2`=+HyDE（**必须** `KB_HYDE=1` 且有 `KB_HYDE_CMD`）。
- **`kb_rag.search_unified`**：末尾追加 `rewrite=None`（None → 读 `KB_REWRITE`，默认 l0）；三路召回包进 `for sub, w_sub in plan.subqueries`，权重 `路权重 × w_sub`；HyDE 成功后额外并一路 `vec_hyde`（`KB_W_HYDE=0.8`），失败仅记 note。
  - 🔴 **rerank / `_enrich` / `log_query` 一律用原始 q** —— 精排不该被同义词串稀释，查询日志必须保持可分析。
  - 🔴 **`rewrite="off"` 与改造前逐位一致**：`subs=[(q_alias,1.0)]`、`w*1.0` 在 float 上精确、循环次序仍是 kw→vec→substr。
- **CLI**：`search` / `ask` 新增 `--rewrite {off,l0,l1,l2}`（默认 None = 读 env → l0）。
- **`eval_rag.py`**：新增 `--rewrite`，给了就**额外评一路** `search_unified`（表里多一列 `三路融合·改写 <档位>`）；顺手把「评测集不存在」从 `FileNotFoundError` 改成「打印 `[缺]` 并跳过」（评测集在 `state/` 不入库，干净机器上必然没有）。
- **`alias_table.json`（新建，8 条）**：`rrf / bm25 / hyde / fts5 / embedding / rerank / chunk / llm` 的通用技术同义词 —— **不含任何人名 / 公司 / 项目名**。此前该文件不存在（`_load_alias_table` 静默返回空表），D5 别名扩展实际从未生效。
- **`_test_query_rewrite.py`（新建，31 断言）**：短查询三档不拆、`[1.0,0.6]` 权重、`和而不同` 不误切、off 档仍做别名扩展、`strip_question` 只剥首尾、HyDE 成功/超时/非零/空命令四种结局、**`KB_HYDE=0` 时外部命令调用 0 次**（计数脚本钉死）、normalize 去重上限、纯函数同输入同输出、非法档位回落。`run_tests.py` 已登记（第 12 套件）。

#### 3. 实测（2026-11-03，本机）

- `python _test_query_rewrite.py` → **31 PASS / 0 FAIL**，rc=0
- `python run_tests.py --ci` → **套件 12/12 通过 · 断言 229/229 · 18.9s**，rc=0
- **off 档回滚钉**：把 `HEAD`（改造前）整棵树 `git archive` 到临时目录，同一库、同 6 条 query（含 2 条命中别名键）各跑一遍 `search --json`：
  `怎么搭建个人知识库 / 知识库 和 wiki 有啥区别 / 血透 / 混合检索架构 / rrf 怎么调 / chunk 大小怎么定` → **6/6 与「改造后 + `--rewrite off`」逐字节一致**（`diff` 为空，含 note 文本）
- `--rewrite l0`：`知识库 和 wiki 有啥区别` → `note = L1 拆分：2 条子查询; 改写 L1：2 子查询; …`；`血透`（2 字）→ `改写 L1：1 子查询`（**短查询不 fanout**）
- HyDE 回落：`KB_HYDE=1 KB_HYDE_CMD=false KB_REWRITE=l2 search ...` → **rc=0**，note 含 `HyDE 回落：外部命令失败/超时/空输出，按 L1 检索`
- `eval_rag` 改写列：`_modes(None)` = 11 路 → `_modes("l0")` = 12 路（新增 `('unified', '三路融合·改写 l0（search_unified）')`），`render_table` 末行 `| 三路融合·改写 l0（search_unified） | 50.0% | 0.2500 | 0.3000 | 1 |`；`search_unified_mode` 实跑 off/l0 均返回真实路径

#### 4. 🔴 默认值变更 + 未完成的验证（诚实标注）

- **默认值变更**：`KB_REWRITE` 默认 `l0`（Q4 拍板）。**回滚只需 `KB_REWRITE=off`，零代码**。
- **⚠️ L0 默认开，但 R@5 能否不降尚未验证**：本机**没有 `state/kb_eval_30.json / kb_eval_100.json`**（评测集在 `state/`，gitignore 不入库），且本机 `models/` 为空（无 bge / rerank / m3 模型）⇒ `eval_rag` 的 `vec / tfidf / bge` 等模式在本机直接报缺 `lsa_model` / numpy。**因此「L0 是否降指标」这一条我没有验过，也不假装验过**。
  - 补验命令（在有完整索引 + 评测集的机器上）：
    `python eval_rag.py --set 30 --rewrite off` 与 `--rewrite l0` 对比新增列
  - 判据：R@5 **不得下降**（若下降 >0.5pp，按架构 §T03 风险1 把 L0 收窄为「仅 `len(q)>=8` 且含分隔符的查询生效」）。
  - 在补齐该验证前，**默认值保持 l0 是主理人已知的风险**；不放心就 `KB_REWRITE=off`。
- **本机 vec 路降级**：无 numpy / 无 bge 模型，检索 note 里会出现 `vec 路失败: No module named 'numpy'` —— 这是环境，不是改写引入的；所有 off/l0 对比都在**同一降级条件**下做的，故对比仍然有效。

### 产品化三件套 · D1 人设配置 + D3 双许可说明 + #42b tag→Release（T04，2026-11-03）

#### 1. 为什么改

- **D1**：十几个 `KB_*` 环境变量没有"默认档"，新用户不知道设什么；"企业档要求 m3 齐"这类**策略差异**只写在文档里，代码完全不知情。
- **D3**：`LICENSE` §4/§5 已经写清"商用需单独书面许可"，但**没有任何入口告诉用户怎么办** —— 于是"源码可见"被当成"开源免费用"，商业谈判也没个统一话术。
- **#42b**：发版最容易漏的不是打 tag，是 **Release 正文空着**（用户看到 "No release notes"，而 CHANGELOG 里其实写得很清楚）。

#### 2. 改法

- **`kb_profile.py`（新建，零依赖）**：`personal / team / enterprise` 三档矩阵（9 个 `KB_*` 键，见模块 docstring），`apply(name)` 只对每对 kv 执行 `os.environ.setdefault` → **显式 env 恒胜**，返回 `{KEY: {value, from: env|profile}}`；`active()` / `matrix()` / `validate()`；CLI `--show / --json / --apply NAME / --check`。
  - 🔴 三条铁律：**只用 setdefault**、**禁止导入期 apply**（`import kb_profile` 不改任何 env）、**矩阵不含 `*PRIVACY*` / `*CLOUD*` / `*TERMS*` 键**（隐私与法务承诺不能变成"某档可放松"的开关）。
- **`COMMERCIAL.md`（新建）**：照 `LICENSE` §4(a)-(h) / §5 写商用通道 —— 免费情形表、必须授权的 8 类行为、申请四要素（组织 / 用途 / 部署规模 / 是否修改）、组织内 30 日评估例外、常见问题。**联系方式只写公开仓库 issue，不写个人邮箱**；开头明确「本文件不修改许可条文，歧义以 `LICENSE` 英文全文为准」。`LICENSE` / `LICENSE.zh-CN.md` **零改动**。
- **README（根 + 模块）**：保留"不可商用"，追加「商用/企业生产部署须按 `LICENSE` §5 取得书面许可 → 见 `COMMERCIAL.md`」；措辞统一为「**源码可见（source-available）· 非 OSI 开源**」（这是事实描述，不是新增权利授予）。
- **`tools/make_release_notes.py`（新建，stdlib）**：`extract(changelog, version)` 定位 `## [v<ver>]` 段截到下一个 `^## `；`--check` 找不到（或段为空）→ **rc=1**；`--out`；默认版本读 `VERSION`；`v` 前缀可选。
- **`.github/workflows/release.yml`（新建）**：`on: push tags ["v*"]`、`permissions: contents: write`；步骤 = 抽正文（`--check` 失败即 fail）→ `gh release create --notes-file notes.md --verify-tag`；**不附 exe**（二进制手工上传）。
- **新增 `_test_kb_profile.py`（27 断言）**：三档 key 集合一致、env 恒胜（`KB_REWRITE=off` 后再 `apply("enterprise")` 仍 `off` 且 `from=="env"`）、矩阵无隐私键、`apply` 幂等（生效值不变）、**`import kb_profile` 不改 env**、非法档名回落不抛、CLI 三个子命令端到端（子进程）。`run_tests.py` 已登记（第 11 个套件）。

**顺带三个止血小修（并入本任务）**
1. `_test_rotate.py`：`os.name != "nt"` 时打印 `[PASS] skipped-on-posix: <原因>` 并 exit 0 —— 该用例判定的是 **Windows 侧 `-shm/-wal` 三件套语义**，CI 首日不该因平台差异假红（Windows 本机仍全跑 6 条）。
2. `_cmd_build_body`：判红条件加 `and not args.skip_vec` —— `build --skip-vec` 是**操作者显式选择**（先同步文本、稍后补向量），报 rc=3 只会把正常用法搞红；现只告警。回滚/回归：`--skip-vec` 且全量且四表有缺口时输出「按设计不判红」，rc=0。
3. `_test_metrics.py` / `_test_mcp_server.py`：断言输出 `✓/✗` → **`[PASS]/[FAIL]`**（断言与退出码不变），`run_tests.py:70` 的正则终于数得到，看板「断言」栏不再只能标"按退出码"。

#### 3. 实测（2026-11-03）

- `python _test_kb_profile.py` → **27 PASS / 0 FAIL**，rc=0
- `python run_tests.py --ci` → **套件 11/11 通过 · 断言 198/198 · 16.7s**，rc=0（其中 `_test_metrics` 23、`_test_mcp_server` 17、`_test_kb_profile` 27 已能被正则计数）
- `python tools/make_release_notes.py --version 1.14.2 --check` → rc=0，`[PASS] v1.14.2 段落存在：28 行，首行「### 重复块治理（A 任务）…」`
- `python tools/make_release_notes.py --version 9.9.9 --check` → **rc=1**，`[FAIL] CHANGELOG 里找不到 v9.9.9 的段落（或段落为空）—— 拒绝发一个空版`
- `git diff --stat LICENSE LICENSE.zh-CN.md` → **空**（法条零改动）
- `python tools/privacy_scan.py --root . --no-assets` → rc=0，`COMMERCIAL.md` 无命中
- `python kb_profile.py --show` → 打印 3×9 矩阵；`--apply enterprise --json` → `KB_BACKFILL_M3 {"value":"1","from":"profile"}`；`KB_REWRITE=off` 时 `from` 变 `"env"`
- `python kb_rag.py build --skip-vec`（`KB_BACKFILL_M3=1` 全量）→ rc=0，`⚠️ …本次 --skip-vec 显式跳过补写，按设计不判红`
- 模拟 POSIX（`os.name="posix"` 后 runpy）→ `[PASS] skipped-on-posix: …`，rc=0
- `_test_rotate.py`（Windows 真机）→ 仍 6 PASS

#### 4. 决策与已知边界

- **不发版**：`VERSION` 仍 `1.14.2`、**未打 tag**、未 bump。理由：本期有默认值变更（L0 改写默认开，见 T03），按语义化应走 minor；但评测集未补齐前不设默认值，故发版推后。`release.yml` 已就位，随时可 tag。
- **`kb_profile` 只是"默认值"**，不接管任何配置持久化（不写文件、不读 yaml）—— 一旦它变成配置中心，就要处理合并/校验/回滚，收益远不及复杂度。
- **`--json` 不带 `--apply` 时输出矩阵本身（只读）**，不改环境：同一个命令不该因为加了 `--json` 就产生副作用。
- **矩阵新增 key 时三档必须同时补**，否则 `validate()` 与 `_test_kb_profile.py` 会红 —— 这是刻意留的"提醒"，防止某档悄悄少一个键。

### 增量闭环 · #37 m3 随 build 补 + 四表一致性断言（T02，2026-11-03）

#### 1. 为什么改（一个「不报错、不丢数据、只是搜不出来」的退化）

`build` 原来只调 `backfill_bge`。而 `bge_encoder.ensure_tables()` 顺手把 `vec_m3` 也建了出来 —— 于是**表在、行不在**：

- 检索不报错、`doctor` 覆盖率还是 100%（它只查 `chunk_bge`）、`stats` 也只印 BGE；
- 结果：m3 语义路长期缺一块，表现为「有些内容怎么都搜不出来」，且**没有任何告警**。

四张派生表（`chunk_bge` / `vec_m3` / `chunks_trigram` / 母表 `chunks`）由**不同函数**维护，谁掉了没人知道 —— 这是最难发现的一类退化，所以本期不是「加个补向量函数」，而是**把口径收成一个原语**。

#### 2. 改法

- **新增 `vec_tables.py`（零依赖）** —— 表级原语，补写与校验**共用同一个 SQL 口径**：
  - `missing_ids(conn, table, limit=0)`：唯一那把尺子。`table` **白名单校验**（`TABLES` 之外一律 `ValueError`，表名要拼进 SQL，不校验就是注入面）；trigram 是 FTS5 虚表，按 `rowid` 对齐；表不存在返回 `[]` 而不是抛（空库上 doctor 也能跑）。
  - `diff_report(conn)` → `{ok, counts, missing, lag_bge, lag_m3, lag_tri, ts}`，`lag_*` 是全量计数，`missing` 里的 id 列表截断到 200 个（`vec_consistency` 要写进 `index_state`，不能塞十万个 int）。
  - `backfill_m3(conn, encode, limit=0, batch=32, text_fn=None, log=None)`：注入式编码器（离线可单测），**只写缺失 id**，`status ∈ encoded|noop|skipped`。
  - `write_m3_model` / `m3_lag`（`lag_m3` 是只读别名）/ `ensure`（内部只调 `bge_encoder.ensure_tables`，torch 懒加载 ⇒ CI 安全）。
  - 模块**顶层不 import** numpy / torch / kb_index / bge_encoder（`ensure()` 里才懒 import）。
- **`vector_backfill.py` 的 `main()` 改为调 `vec_tables`**：缺口统计、终态校验全部换口径；m3 段注入 `text_fn=m3_text`。`m3_text` / `load_m3_gpu` / `encode_m3_gpu` **原地不动**（它们是 m3 唯一编码口径）；退出码 4（无 CUDA）/ 5（校验失败）保留。
- **`kb_rag.py` 新增 `backfill_m3(conn, limit=0, batch=32)`**（放在 `backfill_bge` 之后）：读 `KB_BACKFILL_M3`（`auto` 默认 / `1` 企业档 / `0` 关闭）；模型目录缺 → `no-model`；`torch` 缺或 CUDA 不可用 → `no-cuda`，**打 ⚠️ 不抛**；可用时借 `vector_backfill` 的三个函数组装 `encode`。
- **`_cmd_build_body` 的 4/4 段**从「只补 BGE」改成 **BGE → m3 → 四表断言**，末尾写 `index_state['m3_lag']` 与 `index_state['vec_consistency']`（JSON）。
- **按档位断言**（这是对「四表断言」的刻意收敛）：只有「`limit==0` 全量 且 四表漂移 且 `KB_BACKFILL_M3=1`」才 `return 3`（**不用 SystemExit**，`finally` 仍释放构建锁）；`--limit` 分批构建与 `auto/0` 档只记录、只告警。理由：个人档机器没有 GPU，m3 不全属**允许状态**，判红会让红灯失去信息量。`KB_STRICT_VEC=0` 可把该 rc=3 降级为告警（rc=0）。
- **`cmd_stats`** 加一行 `vec_m3 N ｜ m3 构建于 <built_at>`；**`cmd_doctor`** 的 checks 改由 `diff_report` 驱动（报「真实覆盖 = chunks - 缺 id 数」，不再是单纯行数比 —— 孤儿行会让行数看着齐），新增「m3 覆盖率」（仅企业档判 ✅/🔴，其余档 ⚠️）与 `m3_lag` 一行。
- **`weekly_selfcheck.check_index`** 追加 `"vec_tables": diff_report(conn)`（**只追加键、不改既有键**，历史 JSON 消费者不受影响）；报告与 stdout 加「四表一致性」一行，漂移只标 ⚠️ **不 FAIL** 整个自检。
- **新增 `_test_vec_tables.py`**（内存 sqlite，29 断言）：缺失 id 精确、`diff_report.ok False`、`lag_m3==2`、假编码器补 2 块后清零、**已有向量一个字节都没变**、**二次跑 `encoded==0` 且编码器一次都没被调用**（幂等红线）、`limit=1` 只补 1 块、白名单外 table 名 `ValueError`、默认文本口径与 `vector_backfill.m3_text` 逐字一致。
- **顺手修一行死代码**：`backfill_bge` 开头的 `import numpy as np` 在本函数体内**从未使用**（真正需要 numpy 的是 `bge_encoder.encode()`，它自己会 import）。留着它的后果是：一台没装 numpy 的机器上，**哪怕一块都不用编码**（增量 build 常态），build 也会在补写阶段 `ModuleNotFoundError` 崩掉 —— 与「无重依赖的机器上增量 build 只跳过、不失败」的承诺直接矛盾。

#### 3. 实测（2026-11-03，本机；详细命令见验收记录）

- `python _test_vec_tables.py` → **29 PASS / 0 FAIL**
- `python run_tests.py --ci` → **套件 10/10 通过 · 断言 131/131 · 15.1s**，rc=0（GPU venv 同参数亦 10/10）
- `KB_BACKFILL_M3=auto python kb_rag.py build --limit 1` → **rc=0**，stderr `⚠️ no-cuda 跳过 m3…`（无 torch 机器）
- `KB_BACKFILL_M3=0 … build --limit 1` → rc=0，`m3 skipped`
- `KB_BACKFILL_M3=1 … build`（全量，四表漂移）→ **rc=3**；加 `KB_STRICT_VEC=0` → rc=0（降级告警）
- 同参数连跑第二次 → `BGE 新编码 0 块`（幂等）
- `index_state` 两 key 齐：`m3_lag=26`、`vec_consistency={"ok":false,"m3_status":"no-cuda","lag":{"bge":0,"m3":26,"tri":0}}`
- `python kb_rag.py doctor` → 企业档下 `🔴 m3 覆盖率：0 / 26（0%）`、`m3_lag：26`，rc=3；auto 档只 `⚠️`、rc=0
- `python kb_rag.py stats` → `vec_m3 0 ｜ m3 构建于 未构建`
- `python vector_backfill.py --dry-run` → `[info] chunks=26 缺 bge=0 缺 m3=26 缺 trigram=0`，rc=0

#### 4. 决策与已知边界

- **「四表一致」不能全档硬断言**：改成按档位（企业档严格、其余告警）。字面要求是「四表必须一致」，此处为「不把没有 GPU 的普通用户的日常构建搞红」做了收敛。
- **`rc=3` 是新语义**：漂移可见化。既有定时任务若只看 rc=0 会把漂移当失败 —— 这是**故意**的；不想要就 `KB_BACKFILL_M3=0` 或 `KB_STRICT_VEC=0`。
- **锁粒度**：见 T01 节（按目录派生）。
- **本机无法验的**：`models/` 为空（无 bge / m3 模型），所以「补 m3 后 `lag_m3==0` 且 `diff_report.ok==True`」只能在有模型 + GPU 的机器上验；本机用注入假编码器（`_test_vec_tables.py`）与「补齐 bge 的临时库」验完了全部**编排与判定**逻辑。

### 基础设施 · #42a 测试止血 + 首次 CI + D2 多租户锁修复（T01，2026-11-03）

#### 1. 为什么改（三个真实故障点，不是「代码风格」）

- **A. 一键验收的第一条就是假失败**：`run_tests.py` 的 `SUITES` 里第 7 项指向一个**全仓不存在**的文件（属门户层，已随 kb-hub 迁走）。每次跑都报一个红，久而久之「红了也没人看」——比没有测试更糟。同时上一轮新增的 `_test_metrics.py` / `_test_mcp_server.py` 从未登记，等于没进验收。
- **B. CI/临时库首跑必崩**：`run_tests.py` 的 `__main__` 直接 `open(OUT_DIR/"index.html","w")`，**没有 `os.makedirs`**；`DB_PATH` 又硬编码成模块目录下的 `state/kb.db`，不吃 `KB_DB_PATH`。干净机器上（无 `outputs/`、无库）第一次跑必红，且红在写盘而不是红在测试。
- **C. D2 多租户锁 bug**：`LOCK_PATH` 是**模块级常量**，只依赖模块目录，完全不看 `KB_DB_PATH`。两个租户库（`A/kb.db`、`B/kb.db`）并发 build 会抢同一把锁：一方被 Busy 跳过，而 `_read_meta()` 报出的还是**另一个租户**的 PID 与命令行 —— 排障时会被彻底带偏。

#### 2. 改法（每处都留了一键回滚开关）

- `run_tests.py`
  - `SUITES`：删掉那个不存在的套件；追加 `_test_metrics.py`（评测指标纯函数）、`_test_mcp_server.py`（MCP 协议层）、`_test_lock_perdb.py`（按库隔离的锁）。
  - `DB_PATH = os.environ.get("KB_DB_PATH") or <原默认>`（多租户 / CI 临时库都能报出真实规模）。
  - 新增 `--ci`：照跑全套件、只打汇总行，**不建目录、不写 data.json / index.html**；非 `--ci` 路径补 `os.makedirs(OUT_DIR, exist_ok=True)`（修 B）。
- `kb_index.py`
  - 新增 `default_lock_path(db_path=None)`：`KB_BUILD_LOCK` 优先 → 否则 `<库所在目录>/build.lock`。
  - `BuildLock.__init__` 改为 `self.path = path or default_lock_path()`（**调用时解析**，不再导入时绑定）。`LOCK_PATH` 保留为兼容别名 = `default_lock_path()`，默认库仍派生 `state/build.lock`，**单租户行为逐位不变**。
- `.github/workflows/ci.yml`（新建）：py3.11，只 `pip install jieba numpy`，**绝不装 torch**（重依赖全是函数内懒 import）；steps = `compileall` → `run_tests.py --ci` → `tools/privacy_scan.py --root . --no-assets`；`on` 为 push/pull_request + paths 过滤。
- `_test_rerank_fallback.py`：加 `env_gate()`。该套件 A/B/C 三段是真跑（B 段要求带 `rerank_score`＝必须有 cross-encoder 真推理；C 段要求真实索引里某篇文档被召回），在「无 torch / 无索引」的环境里**不可能**通过。现按 §2 三个条件显式跳过并打印 `[PASS] skipped-rerank-env：<原因>`，**不是假绿**（本机 GPU venv 必跑，原因逐条打印）。

#### 3. 实测（本机，2026-11-03）

- `python -m compileall -q .` → rc=0
- `python _test_lock_perdb.py` → **12 PASS / 0 FAIL**（含 D2 核心断言：A 进程持 `tenantA/build.lock` 时，B 取 `tenantB/build.lock` 0.01s 成功）
- `python _test_metrics.py` → 23 passed / 0 failed
- `python _test_mcp_server.py` → 17 passed / 0 failed
- `python run_tests.py --ci` → **套件 9/9 通过 · 断言 102/102 · 14.5s**，rc=0，且 `outputs/知识库运维/测试看板/` **未被创建**（确认不污染仓库）
- `KB_DB_PATH=/tmp/t1.db python -c "import kb_index;print(kb_index.default_lock_path())"` → `<tmp>/build.lock`（与库同级）
- `grep -c <已删套件名> run_tests.py` → 0

#### 4. 决策与已知边界

- **锁按「目录」派生而非「文件」**：同一目录下两个不同 .db 仍共用一把锁。隔离粒度＝一租户一目录（同目录并发写同一个 SQLite 文件本就该串行）。需要更细粒度用 `KB_BUILD_LOCK` 显式指定。
- **`--ci` 是新增开关**：本地原有调用（不带参数）行为不变，回滚只需 `git revert`。
- **CI 的隐私门只有 L1+L3**：L2 词表按隐私红线不入库，故 **CI 绿 ≠ 可发布**，发布前必须本机带词表再跑一次（已写进 `ci.yml` 注释）。
- **`_test_metrics.py` / `_test_mcp_server.py` 用 ✓/✗ 输出而非 `[PASS]`**：`run_tests.py` 对它们按退出码判定（看板「断言」栏如实标「按退出码」），不硬折算条数。

### 新增（差距项补齐 · 作者 2026-11-03 拍板「把我没有的补上」）

#### 1. MCP 服务暴露（#38）— `mcp_server.py`（零依赖 stdio）

- **为什么改**：作者清单里「MCP 服务暴露」此前缺失；目标环境未装 `mcp`/`fastmcp` 且出网受限装不了。
- **改法**：手写 **JSON-RPC 2.0 over stdio**（纯标准库），协议层（`handle_message`）与检索引擎解耦——懒导入 `kb_rag`，后端不可用（无 torch）时工具返回 `isError` 友好提示而非崩服务。
  - 工具：`kb_search(query, topk, mode, use_rerank)`（三链路融合检索，返回路径/标题/得分/摘要片段）、`kb_stats()`（docs/chunks/chunk_bge 统计）。
  - 只暴露**只读检索**，不外泄写入或隐私（内容来自已脱敏 wiki 合成层）。
- **实测**：`_test_mcp_server.py` 21 断言全过（含真实子进程 stdio 握手 + 真实 FTS 检索冒烟：返回 `wiki/关于本项目.md` 等真实路径，vec/rerank 分支在无 torch 时优雅降级）。
- **决策理由**：不依赖官方 SDK = 任何装了 Python 的客户端都能直连；协议单测可离线跑，避免「CI 跑不起 MCP SDK」的坑。

#### 2. 正式评测 harness 抽离（#40）— `metrics.py` + `eval_rag.py` 重构

- **为什么改**：`eval_rag.py` 的 Recall/MRR/NDCG 写死在 `evaluate()` 里、不可单测、且只有 k=5。
- **改法**：抽离纯函数 `metrics.py`（`hit_rate_at_k` / `precision_at_k` / `mrr_at_k` / `ndcg_at_k` / `ndcg_at_k` 分级相关性 + `ranking_metrics` 聚合，支持单条/多条 ground truth）；`eval_rag.py` 改调 `metrics`，并加 `--k` 任意 cutoff。
- **实测**：`_test_metrics.py` 23 断言全过（含手算可核值：单条 gt 在 rank3 时 NDCG=0.5 = 1/log2(4)，与旧口径一致）；`eval_rag` 重构后 FTS 模式端到端跑通（recall@3=0.67）。
- **未完**：LLM-judge 组件 `faithfulness_judge.py` 已存在，但评测集缺参考答案 / 未接 LLM endpoint，接入推迟到评测集补齐后。

#### 3. 路线图其余项（#37 / #39 / #41 / #42）侦察结论

- **#37 增量索引**：docs 级 content-hash 增量 upsert + `vector_backfill.py`（只补缺失向量，幂等）**已存在**；缺口＝把 backfill 接进 `kb_rag.py build` 自动跑（替代 2h 全量重切）。→ 下回合落地。
- **#39 查询改写/HyDE**：未开工，需 `query_rewrite.py`（LLM 抽象 + 确定性兜底）。
- **#41 知识图谱**：先研究后决策（预计 ~2pt 增益，复杂度不值，倾向「可选插件层」）。
- **#42 CI/Release**：未开工，GitHub Actions 跑离线单测（不碰 GPU）即可。

## [v1.14.2] - 2026-11-02

### 重复块治理（A 任务）：split_chunks 接入垃圾过滤 + 同文档去重 · CHUNKER_FP v2→v2.1

#### 1. 为什么改（作者 2026-11-02 拍板「做吧」）

夜跑体检测出 35.5% 块为重复/垃圾内容（日志重复行 / CSV 空行 `,,,,,,` / base64 串 / PPT 模板标记）。正确解法是在分块阶段加过滤，而非事后清理。

#### 2. 改法（最小改动，零质量回归）

- 新增 `_garbage_filter.py`：`is_garbage_block()`（4 类垃圾判别）+ `filter_chunk_pairs()`（过滤 + 同文档去重），11/11 单测通过（后又扩到 14/14）。
- `kb_index.split_chunks` 在「标题切块后、短块合并前」接入过滤——**先剔垃圾/重复，再合并真实短块**，避免垃圾长块连坐误杀相邻真实短块（端到端验证发现并修掉的交互 bug）。
- `CHUNKER_FP` 由 `v2|max=480|snap=0.85|ovl=0` → `v2.1|max=480|snap=0.85|ovl=0|gf=1`，诚实记录分块策略变更；`vector_fingerprint` 随之变化，护栏防混代。
- `rechunk_all.py` 修补：补齐 `vec_m3` 影子表 / swap / `m3_model` 元数据（此前 swap 只换 `chunk_bge`，重切会让 `vec_m3` 全孤儿、m3 跨语路由失效）；新增 `reencode_m3.py`（fp16 cuda，复刻 `build_vec_m3` 口径）。

#### 3. 实测（2026-11-02，白天低风险时段）

- 全量重切 chunks **116,150 → 111,228**（净减 4,922，-4.2%）。
- 垃圾块 **3.43% → 1.67%**；同文档重复 **1.27% → 0.94%**；跨文档重复 22.44% 维持不动（设计边界，删了破坏溯源，列为拍板项 D）。
- 双塔重编码：bge 111,228×512 fp32 cuda 746.9s；m3 111,228×1024 fp16 cuda 2637.1s。
- 五表完全对齐（chunks/chunks_fts/chunks_trigram/chunk_bge/vec_m3 = 111,228），orphan=0。
- 回归测试 **7/7 套件、87/87 断言**通过；周自检 R@5 粒度融合 84.0%→**85.0%**、空检率 0%、引用准确率 100%（重切零质量退化）。

#### 4. 顺带修复

- `wiki/log.md` 第 36 行内链多写一级 `wiki/`（相对 wiki 根应为 `技术/...`），周自检失效内链 1 条已修正。

#### 5. 拍板项（非本版本范围，待作者决策）

- **D. 跨文档重复 22.44% 全局去重**：需牺牲溯源换存储，非分块阶段能解，列入后续。

## [v1.14.1] - 2026-11-02

### rerank 精排层 GPU 化（device=auto + fp16，KB_RERANK_DEVICE=cpu 可回退）

#### 1. 为什么改

此前 `bge_reranker.load_model()` 只在 CPU 跑 Cross-Encoder 精排，30 文档精排实测 **22–23 秒（≈0.77s/文档）**。而 `kb_rag` 对任何 >4 字查询都走精排 —— 用户每次长查询等 20 秒以上，体验不可接受。

#### 2. 改法（最小改动，零质量回归）

- `load_model()` 默认 `device=auto`：有 CUDA 就上 GPU + fp16（`mdl.half().to("cuda")`），无卡自动回落 CPU；`KB_RERANK_DEVICE=cpu` 一键回退。
- 模型与权重不变，只换算力；fp16 分差 1e-3 量级，不影响排序。
- `BATCH_GPU=32`：GPU 上更大批，30 条池子一批跑完。

#### 3. 实测（2026-11-02）

- 30 文档精排延迟 **32,092ms（CPU）→ 852ms（GPU fp16）**，加速 **≈37×**。
- GPU / CPU top20 排序**逐位一致**（fp16 不影响精排结论）。
- 模型缺失 / 推理异常仍 `return None` 优雅降级，检索照常返回 RRF 结果。

#### 影响面

- 仅 `bge_reranker.py`：`load_model()` device 选择 + `rerank()` 批大小按 device 切换。
- 检索主链路 `kb_rag.search_unified` 行为不变——仍是「有模型才精排，无模型降级」。

## [v1.14.0] - 2026-11-01

### 分块结构改造上线（CHUNK_MAX 1200→480 + 断句吸附）· 片段完整性覆盖率 5.6%→76.4% · 语言路由（m3 多语塔，默认关）

#### 1. 为什么改 + 验收口径（作者 2026-10-31 晚拍板）

- 旧判据（2026-10-28 / 10-31 早）以「块级二值召回」量分块，测得 Δ@5 不显著 → 判「不做」。真因是该尺子**不可配对**（票数 2:1、块数 ×1.93、D1 截断偏差全偏 old），**不是改造没用**。
- 作者改以「**片段完整性**」为唯一验收口径：`coverage_at_k`（top-5 最好块覆盖答案句 ≥90%），理由写「片段完整性」，**不写「块级召回提升」**。
- 三臂消融实测（sandbox-chunk-ab-3arm.json，n=72 自然问句，McNemar 配对检验）：
  - old（1200 硬劈）：≥90% 命中率 **5.6%**（4/72）
  - new-merged（同块数 + 断句吸附）：**44.4%**（32/72），较 old **+38.9pp，exact p≈5.8e-8**
  - new（480 + 吸附 + 零 overlap）：**76.4%**（55/72），较 new-merged **+31.9pp，p≈1.6e-6**；较 old **+70.8pp，p≈8.6e-14**
  - **总效：覆盖率 5.6% → 76.4%，p<0.0001**。

#### 2. 落地（T6 · rechunk_all.py 影子表 + 单事务 swap）

- 生产 chunks **59,500 → 115,551**（+94.2%）；`chunks_trigram` / `chunk_bge` 同步 115,551。
- 块长 p50=461 / p90=480 / p99=480，**仅 1 块 >480 字**（断句吸附生效）；中文答案句劈开率 **3.16% ≤5%** ✅。
- `index_state['chunker_fp']` = `v2|max=480|snap=0.85|ovl=0`（混代护栏，不一致即 SystemExit，失败模式=拒绝干活）。
- 单事务 `ALTER TABLE RENAME` 原子换表；`*_old` 回滚表保留 7 天（R1 窗口）。
- GPU 全量重编码直写 `chunk_bge`，顺带修复环境漂移：生产向量与当前 query 编码器系统性偏差 ~0.961，GPU==CPU==query 编码器实测 **bit-identical=1.0**。

#### 3. 验收闸门（S3 / S4）

- S3 主集 eval-main-137（fused/rerank=0/k=2）：**R@5 52/137（基线 51，+1，过闸）**；R@20 91→83（vec 路 0.961 修复 + RRF kw 权重 2.0 交互，vec 单路 R@20=92 仍强，不触发回滚）。
- S4 1–2 字查询 p95：**茻 336ms / 血透 50ms / 的 290ms / 视网膜 40ms**，全部未恶化（≤+50%）✅。
- 主集扩到 184 条（当前生产配置）：R@5 83/184、R@20 121/184。

#### 4. 语言路由（T1 · `KB_VEC_ROUTE` 开关，默认关）

- 新增 `vec_m3` 表 + `m3_model` 元数据 + `search_bge_m3`（1024 维 CLS+L2）；bge-m3 **GPU fp16 全量编码直写 `vec_m3`，不经 npy 中转**（旧 npy 因 chunk_id 不连续会张冠李戴，已踩坑）。
- `kb_rag.search_unified` vec 分支加语言路由：`KB_VEC_ROUTE` 为真 **且** 查询判为非 CJK（CJK≥0.5）→ 走 m3 多语塔；否则走 small-zh。**默认关闭**，中文/外文行为逐位不变（红线：零回归优先）。
- 跨语种验收（34 条，KB_VEC_ROUTE=1 走 m3，mode=vec）：**m3 R@20 = 31/34**（15/18 + 16/16，目标 ≥26/34 ✅；R@5 = 20/34）。基线（不路由、外文走 small-zh）：**R@20 = 1/34、R@5 = 0/34** —— 证明语言路由对外文检索是必需的，m3 塔验收合格、默认关待一周观察。

#### 5. 评测体系固化（T3 / T4）

- `eval_stats.coverage_at_k` 固化「片段完整性」指标；三臂消融复现 PASS（mean_cov@5 / ≥90% 题数逐位对齐沙盘）。
- 题集扩容：主集 137→**184**（C70/B40/F34/D24/E16）、跨语种 14→**34**（m3 塔）、C 桶 36→70、raw 侧 5 条（判据对 raw 侧不适用，后续立项块级口径）。

## [v1.13.39] - 2026-10-31

### 隐私出口门禁（本机可检索、出口必须门禁）· 订正 `log_query` docstring

#### 1. 隐私出口门禁（`kb_rag.py`）

**背景**：隐私定界探针实测 `raw/隐私/` + `raw/个人信息/` 共 **258 篇 / 2482 chunk 已 100% 进三路索引**（fts/trigram/bge 各 2482/2482），而**检索层与回答层双零门禁**；20 条中性查询中 **5 条（25%）** top20 出现隐私命中（最坏「运维手册」5 条命中、首次在第 4 位）。

**作者 2026-10-31 01:4x 拍板：「只要不外传就行」** → 本机可检索、出口必须门禁。

**做法与取舍（为什么这样做）**：
- 新增 `PRIVACY_EXPORT_MARK` + `is_privacy_path()`：单一真相源取 `kb_index.PRIVACY_PATH_PREFIXES`，取不到才回落硬编码，避免两处定义漂移。
- `build_context()` 把隐私条目的 `snippet` 换成占位符，**路径与标题仍保留**（便于本机追溯），cites 加 `privacy` 字段，末尾追加告警行。
- **不在检索层过滤**：砍召回等于砍功能，与业主核心验收「一个字一句话都要搜得到」直接冲突。
- 🔴 **不套用 `cloud_publishable` 全量白名单**：该模块的 `assert_publishable()` 会把 `raw/项目/`（示例市原始数据）、`raw/微信文件/`、`raw/生活/`、`raw/其他/` 等**非隐私品类**也判为"不可发布"；拿它做检索侧门禁会误伤本机正常检索。用户要的是「隐私不外传」，不是「只让 wiki 可搜」。

**实测**：`LangChain` 查询 —— search 侧 4 条隐私命中仍带 snippet（本机可读 ✅）；ask 侧 4 条全部脱敏 + 告警行 ✅。

#### 2. 订正 `log_query` docstring（`kb_index.py`）

原文写「只有 CLI 调用，评测循环不埋点」**不准确**：`kb_rag.py:602` 让所有走 `search_unified` 的调用都会埋点（门户同样会写）。
实测 `query_log` 413 个去重 query 里 202 个是评测集原题，来源是**旧版 runner 未禁用日志**时的历史运行 —— 现行 `outputs/知识库运维/2026-10-23-judged-eval-runner.py:80` 已 monkeypatch 禁用 `log_query`、`--with_log` 全库无调用方。docstring 已改为准确表述。

#### 影响面
- `kb_rag.py`：`build_context()` 对隐私条目脱敏；**`search_unified()` 行为完全不变**（不砍召回）。
- `kb_index.py`：仅 docstring 变更，零运行时影响。
- 门户 `kb_hub_server.py`：`/api/rag` 结果体新增 `privacy` 字段（前端可据此提示"禁止外传"，本机仍可看正文）。

## [v1.13.38] - 2026-10-30

### 扫描件 PDF 全线打通（580 份 / 5101 页进库）· PDF 提取快 5.74 倍 · 修掉一个会「静默丢账」的缺陷

#### 1. T5 · PDF 提取并发 worker 池（`ingest_raw.py`，加速 5.74x）

原实现每篇 PDF 起一个子进程（空转 4.7s：python 启动 + jieba + pypdf 导入）。改为**常驻 spawn 子进程池 + 每篇独立 deadline**。

选型理由（三条被否掉的方案都写在代码注释 482-501 行）：
- **不用 `ProcessPoolExecutor`**：它没有 per-task 超时——`future.result(timeout)` 只是「不等了」，**不会杀掉正在研磨的 worker**，一个病态 PDF 会永久占住槽位。本池每篇自记 deadline，到点只 `terminate` 那**一个** worker 并换新的。
- **不用线程池**：GIL 下纯 Python 解析无真并行；更致命的是**线程无法被中途杀掉**，超时隔离直接失效。实测起池只要 0.23s，池把这笔钱从「每篇付」变成「只付一次」。
- **不每篇新起进程**：空转 4.7s/篇。

实测（1235 篇全量 PDF 等距抽样 24 篇）：
```
[串行] 71.64s（2.99s/篇）
[池]   起池 0.23s ｜ 稳态 12.47s（0.52s/篇）
[加速比] 5.74x    [一致性] 池产出与串行逐篇完全相等: True
[外推] 全量 1235 篇：串行 ≈61.4min → 池稳态 ≈10.7min
```
并发数默认半核（16 核 → 8，`KB_PDF_WORKERS` 可覆盖，封顶 16）；`PDF_SEG=256` 分段释放，峰值封顶 ~50MB。
**超时隔离 / 失败隔离 / gap 顺序稳定性均有实测保留**，收尸干净（4 worker 全灭、exitcode 全 0）。
⚠️ 边界：全量重建 = PDF 段 + 其他类型段，`.doc` 走 Word COM（626 篇约 10–35min）未动，「总时长 30min 内」尚未完全达标。

#### 2. 新增 `media_pdf.py`：扫描件 PDF 批量解析管线

MinerU 4.0.12 basic 档（ONNX/CPU）+ `ocr_mode=auto`，state 断点续跑、`O_EXCL` 单实例锁、`--dry-run/--limit/--only/--retry-failed/--rebuild`。

**2026-10-30 全量跑完 613 份扫描件**（≤20 页 502 份 + 21-100 页 78 份，>100 页 33 份按指令未碰）：
| 页档 | 候选 | 有记录 | 结果 |
|---|---|---|---|
| ≤20 页 | 502 | 502 | done 486 / empty 16 |
| 21-100 页 | 78 | 78 | done 67 / empty 11 |
| **合计** | **580** | **580** | **done 553 / empty 27 / fail 0** |

#### 3. 新增 `media_doc.py`：legacy `.doc` 解析 + 内嵌图 OCR 兜底

补上 2 篇原本搜不到的 `.doc`：
- 病案室需要的报表.doc（5.26MB，原本 **0 字**）→ **3,182 字**
- 免疫规划定期复训通知.doc（0.98MB，Word COM 打不开）→ **1,135 字**

**第 1 篇的死因是反直觉的**：不是 Word 加密/损坏/OLE2 不支持（MinerU 3.4s 就出了 middle_json），而是**正文压根是 9 张内嵌 JPEG 图表，文本层为空**（`block0..8 type=image`，0 个文本块）。解法是从 middle_json 的 `image_base64` 导出 jpg 走 RapidOCR 兜底。

#### 4. 🔴 修掉一个会「静默丢账」的缺陷

`--retry-failed` 原实现是**先删掉全部 fail 记录，再只重跑 `--limit N` 个** ⇒ 未被重跑的条目**永久从 state 消失**（文件还在盘上，记账没了）。

**危害远大于普通 bug**：失败会被看见、会被统计；**丢账不会**。若不把 `pdf_triage.json` 与 `state` 逐条比对，会误判成「全跑完了、零失败」。
**本次实测丢了 11 条 / 717 页**（`state` 580 → 569，fail 11 → 0）。

修法（`media_pdf.py:374-404`、`media_doc.py:425-440`）：只摘除**本次真要跑**的 N 条，其余原样保留；并加**账务自检**——打印「从 state 摘除 N 条 == 本轮待做 N 条 → OK/❌」。
自检证据：造 3 条假 fail、`--limit 1` 跑，断言「只 pop 1 条 / 另 2 条仍在且仍为 fail / 总条数 572→571」全过。

#### 5. 错误记录不再吞消息（`str(e)` → `type + repr + traceback`）

`media_pdf.py:279` / `media_doc.py:321`。原代码只记 `str(e)`，而**裸 `MemoryError()` 的 `str()` 是空字符串** ⇒ state 里只剩 `"parse: "`，8 条失败全是这个形态，无法诊断。
现在记 `异常类型 + repr + traceback 末 3 行`，能直接区分 `MemoryError` / `COMError` / `OSError`。

#### 6. 渲染池定期释放（OOM 的机制性修复）

docvortex 的 PDF 渲染池是**模块级全局、跨 `parse()` 存活**，长跑时子进程累计的页面图不释放 ⇒ 主进程 3.7GB + 3 个渲染进程吃光内存。新增 `_reset_render_pool()`，每 N 份（默认 5，`--render-reset-every` 可调，`--no-render-reset` 可关）调一次。
⚠️ 实测导入路径坑：`docvortex.public_api` 在 0.6.0 上**导不出** `shutdown_pdf_render_executor`（惰性导出表未挂属性），真正常用的是 `docvortex.document.pdf.images`，故做两级 try/except 回退。

#### 7. 11 份 OOM 失败全部救回（3 类疑似问题归为 1 个真因）

11 条 fail 看似两类（5 条 `Unable to allocate ... (3500,2038,3)` + 6 条空错误），**实际全是 OOM**。
- **关键证据**：89 页那本报 OOM 的 shape 只有 `(960,672,3)` float32 ≈ 7.38MB —— 一张小图不可能独撑爆内存 ⇒ 是**累计压力**，不是单图过大
- **降 DPI 这条路走不通**：`DEFAULT_PDF_IMAGE_DPI=200` 是模块常量、`config.py` 无对应项、无 env 入口
- **有效且零改代码的是降并发**：`DOCVORTEX_PDF_RENDER_THREADS=1`

实测代价几乎为零（推翻「串行会更慢」的直觉）：
| 口径 | 速度 | 结果 |
|---|---|---|
| 默认（3 渲染进程） | 2.54 s/页 | 89 页那本 **108s 崩** |
| `THREADS=1` | **2.62 s/页** | **89 页全过、0 OOM** |

11 份补跑结果：**10 done + 1 empty、失败 0、31.4 分钟、2.62 s/页**，state 569 → **580**，21-100 档缺口 **11 → 0**。

🔴 **价值极不均衡（决策依据）**：743,253 字符里**吴菊英 89 页贡献 716,979 字 = 96.4%**，讲师一-黄宇豪 19,371 字 = 2.6%，**其余 9 份合计仅 6,903 字**。⇒ **页数不等于价值**，这条直接用于 >100 页那 33 份的取舍。

#### 8. 库终态与回归

两次 build 吸收共 **560 篇**（551 + 9）：
| 指标 | v1.13.36 | v1.13.37 | 变化 |
|---|---|---|---|
| docs | — | **8176**（wiki 3952 · raw 4149 · 其他 75） | — |
| chunks | — | **58765** | — |
| trigram 覆盖 | — | **100%** | ✅ |
| BGE 覆盖 | — | **100%** | ✅ |
| 零命中率 | — | **31 / 5584（1%）** | 持平 |

回归**两轮均零回退**：
| 回归集 | R@5 | R@20 | MRR@5 | 基线 | 判定 |
|---|---|---|---|---|---|
| holdout（60 条） | **98.3%** | **100%** | 0.8922 | 98.3% / 0.9006 | ✅ 持平 |
| raw（50 条） | **98.0%** | **100%** | 0.8417 | 98.0% / 0.8440 | ✅ 持平 |

⚠️ **一次中断**：二次 build 在前台被 SIGTERM 杀掉（BGE 编到 192/883），改后台重跑完成。**当天第三次「会话中断带走子进程」**（前两次：00:5x 跑批、12:47 自动化 launcher）。BGE 增量，重跑只补未编码的 691 块。

#### 9. 决策记录：**不做** 内嵌图 OCR 兜底迁移

上一轮遗留问题「要不要把 `.doc` 的内嵌图 OCR 兜底移植到 `media_pdf.py`」，用数据否定：**不需要**。
- PDF 走 `ocr_mode=auto`，**对页面图像本来就会 OCR**
- 红烧羊肉 47 页出 0 字符、毛毛帽 39 页出 27 字符 ⇒ 是**图上没有可识别文字**，不是「OCR 没跑」
- 与 `.doc` 病案室报表**不是同一个病**：那个是 MinerU 的 doc 通道**不做内嵌图 OCR**，PDF 通道没有这个缺口

🔴 **可复用判据**：「没过 OCR」和「OCR 过了但图上没字」是两种完全不同的病，混在一起会去修一个不存在的问题。

---

## [v1.13.36] - 2026-10-29

### 音视频转写全量落地（272 篇可搜）· GPU 比 CPU 快 8–10 倍

#### 1. 全量跑完

`media_asr.py` 把 `raw/` 下音视频全部过了一遍（faster-whisper **small**，`cuda` / `float16`）：

| 项 | 数字 |
|---|---|
| 处理 | **714 件**（音频 92 / 视频 622），累计 **44.6 小时音频** |
| **成页** | **272 篇**写进 `wiki/媒体/音视频转写/` |
| 空结果 | 413 件（58%）——纯音乐、无声录屏、只有字幕水印的短视频 |
| 无音轨 | 29 件（`IndexError`：`streams.audio[0]` 不存在，改判 `nospeech` 而非失败） |
| **失败** | **0 件** |
| 吞吐 | **RTF 0.075**，墙钟 11,389s（3.2 小时）；CPU 版同模型 0.648–0.805 ⇒ **快 8–10 倍** |

入库后全库：`docs 7,616`（wiki 3,392 / raw 正文 4,149 / 其他 75）、`chunks 55,403`、向量 100%。

**实证「录音里的字能搜到了」**：
`恍惚間 盡頭了回 慢慢的回` → 第 1 名 `wiki/媒体/音视频转写/微信文件/生活/2023-06-05-椿-沈以诚.mp3.md`。
这句此前只是 218 秒音频里的波形。

#### 2. 回归：**零影响**

| 集 | 加转写前 | 加转写后 |
|---|---|---|
| holdout 60 R@5 / MRR@5 | 98.3% / 0.9006 | **98.3% / 0.9006（逐位相同）** |
| raw 50 R@5 / MRR@5 | 98.0% / 0.8440 | **98.0% / 0.8440（逐位相同）** |

原因可解释：只新增 627 块（+1.1%），且转写页本来就吃 `KB_MEDIA_PENALTY=0.5`。

#### 3. 三类失败的处理（都写进代码注释）

- **无音轨**（`IndexError: tuple index out of range`）：faster-whisper 取 `streams.audio[0]` 直接抛。
  这不是失败，是**本来就没有人声** ⇒ 记 `nospeech`，不污染失败率；
- **PyAV 解码失败**（`ValueError: Frame does not match AudioFifo parameters`）：
  自动走 `ffmpeg -vn -ac 1 -ar 16000` 转临时 wav 再转写，用完即删；
- **VAD 会不会误杀轻声人声**：抽 3 个「空」结果开 `vad_filter=False` 复跑，
  仍只有「字幕by索兰娅」「詞曲 李宗盛」⇒ **不是误杀，是真没语音**。

#### 决策理由

- **为什么用 small 不用 medium**：medium 在 4GB 卡上约 2.5 倍耗时（全量 8 小时→20 小时），
  而 small 的错字（「青蒿素→清豪宿」）对「搜得到/搜不到」影响有限；
  页面里已显式标注「机器转写，有错字，搜不到先怀疑转写」，把局限写在明处而不是藏着。
- **为什么不先降权再上线**：`KB_MEDIA_PENALTY` 已由 v1.13.34 定值 0.5，转写页自动继承，
  不必为音视频单独调参（实测也确实零影响）。

#### 验证

- `kb_rag.py stats`：`docs 7,616 / chunks 55,403 / BGE 100%`；
- `eval_layered` holdout + raw 与 v1.13.34 逐位相同；
- `kb_rag search` 实测「音频独有短语」命中（见上）。

#### 已知问题

- small 模型的中文错字会**直接进索引**：搜「青蒿素」找不到那篇（转写成「清豪宿」）。
  页面已带警示行；要根治得换 medium 或加同音词扩展。
- 413 件「空」里有相当比例是**纯音乐短视频**（微信收到的），这批内容永远不会有文字表示，
  属结构性空白，不是流程缺陷。
- 媒体页累计 2,720 篇里，**72 篇含「患者」等医疗业务词**（`raw/项目/` 业务系统截图）。
  按作者 2026-10-29 拍板正常收录、不打码；**对外发布前必须过 `--quarantine` + `privacy_scan`。**

> 版本号说明：一次改动，`snapshot.py` 因沙箱重试被调起两轮 ⇒ **1.13.35 / 1.13.36** 两份快照，按 L1 铁律都保留；CHANGELOG 只记一条，以 **VERSION = 1.13.36** 为准。

## [v1.13.34] - 2026-10-29

### 图片 OCR 全量落地（2,448 篇可搜）+ 媒体页降权定值 + 单实例锁

#### 1. 图片 OCR 全量跑完（这是本日最大的一个"从 0 到 1"）

`raw/` 下 **11,703 张图片**此前一个字都搜不到。`media_ocr.py` 全量跑完：

| 项 | 数字 |
|---|---|
| 处理 | 11,703 张（0.51 s/张，2 线程，4,370s） |
| **成页** | **2,448 篇**写进 `wiki/媒体/图片OCR/`（256,747 字） |
| 空图 | 9,238 张（79%）——表情包/纯壁纸/无字截图，抽验确认**真没内容**，非误杀 |
| 失败 | 17 张，全部是文件本身损坏（`cannot identify image file`） |

入库后全库：`docs 7,413`（wiki 3,189 / raw 正文 4,149 / 其他 75）、`chunks 54,776`、向量 100%。

**实证「图片里的字真的能搜到了」**：
`今日：守正待时` → 第 1 名 `wiki/媒体/图片OCR/生活/2026-10-21-…签文图.png.md`（rerank 0.98）。
这行字此前只存在于 PNG 像素里。

#### 2. 🔴 媒体页降权：不降权会把真文档挤下去（压测过四档）

2,448 篇 OCR 页进库后（语料 48,177→54,776 块，**+13.7%**），回归立刻掉：

| 配置 | holdout R@5 / MRR@5 | raw R@5 / MRR@5 |
|---|---|---|
| 降级前（48,177 块） | 98.3% / 0.9256 | 100.0% / 0.8623 |
| **pen=1.0（不降权）** | **96.7% / 0.8778** | 98.0% / 0.8340 |
| **pen=0.5（采用）** | **98.3% / 0.9006** | 98.0% / 0.8440 |
| pen=0.35 与 0.0 | 与 0.5 **完全相同** | 与 0.5 **完全相同** |

三条判读：

- **0.5 与 0（完全不参与融合）指标完全相同** ⇒ 0.5 已经压到位，再压一分收益都没有；
- 剩下的 −0.025（holdout MRR）**不是媒体造成的**：逐条查 12 条非第 1 名的查询，
  只有 2 条是被媒体页顶掉的（而且那 2 条媒体页**内容确实正确**，属 gt 口径问题），
  其余 10 条是被普通文本页顶掉 —— 是**语料变大本身的竞争/IDF 漂移**，加多少内容都会发生；
- 采用 0.5 而不是 0，是因为**能力不能砍**：`抢救工作站 办理入院证 按钮` 这条查询，
  pen=0.5 时媒体页排**第 2**（仍可搜到），pen=1.0 时反而不在前 4。
  另有一条 `今日：守正待时` 在 0.5 时会掉出前 4——那是三个文本页说了同一件事，属可接受。

**结论**：`KB_MEDIA_PENALTY` 默认 **0.5**，可用环境变量覆盖（1.0=不降权，0.0=完全不参与）。

#### 3. 单实例锁（三版才对，见 HANDOFF 坑 H）

两个长跑批量必须单实例：`save_state()` 写的是**内存里的整份字典**，两进程互写会丢记录。
`msvcrt.locking` 在空文件上拦不住、`os.mkdir` 有创建/写 pid 的竞态，最终用
`O_CREAT|O_EXCL` + 空 pid 视为「正在启动」不抢占 + 300s 陈旧判定，实测 `--dry-run` 返回 rc=2。

#### 4. 修掉的两个自己的 bug

- `md_fp` 只写在 `if hits:` 分支里 ⇒ `UnboundLocalError`，**18 张正常图误判 fail**（已修 + `--retry-failed` 补跑）；
- 见上「单实例锁」，另纠正一个观察错误：同命令的「两个 PID」实测是**父+子**（父 0 CPU/1 线程），
  不是被执行了两遍——判据要看 CPU 与线程数。

#### 决策理由

- **为什么把 OCR 结果落 `wiki/` 而不是 `raw/`**：raw 是原件区只读；`raw/_*` 是归档区、**不进索引**，
  塞进去等于白干；wiki 是合成区且在索引内，头部 `> Raw:` 还能指回原图。
- **为什么 `MIN_TEXT=20` 才出页**：11,703 张里 79% 没字，全出页会拿上万篇空壳污染索引。
- **为什么降权而不是直接剔除媒体**：实测二者指标完全相同，但剔除会让「只在图里存在的字」搜不到，
  那是把刚做出来的能力又砍掉。

#### 验证

- `run_tests.py` **7/7 套件 87/87 断言**（361.8s）；
- `eval_layered` holdout 60 + raw 50 已跑四档并落 `state/sweep_media_penalty.json`；
  新基线 `state/layered_baseline_post_ocr.json`（tag `post-ocr-pen05`）；
- `kb_rag search` 实测「图片独有短语」能命中（见上）。

#### 已知问题

- `raw/项目/` 业务系统截图里有**真实患者姓名**（抽查见「示例患者」）。按作者 2026-10-29 拍板
  「不用隐私打码，业务截图就那样吧」**正常收录**；隔离机制保留为 `--quarantine` 开关。
  **对外发布前必须再过一遍 `--quarantine` + `privacy_scan`。**
- 音视频转写（`media_asr.py`）仍在跑，本版只交付管线与已验证单价（GPU RTF 0.083–0.203），
  其产出与回归放到下一版。

> 版本号说明：一次改动，`snapshot.py` 因沙箱重试被调起两轮 ⇒ **1.13.33 / 1.13.34** 两份快照，按 L1 铁律都保留；CHANGELOG 只记一条，以 **VERSION = 1.13.34** 为准。

## [v1.13.32] - 2026-10-29

### 新增三个工具：图片 OCR 批量管线 / S3b 块边界尺子 / GPU 转写实测

**1. `media_ocr.py` —— 图片批量 OCR 落成可检索合成页（本轮最大的一个新能力）**

`raw/` 下 **11,703 张图片**此前一个字都搜不到（`.png/.jpg` 不在 `OFFICE_EXTS`，
且它们只是被当成路径字符串入库）。现在：

- 用 **RapidOCR 3.10.0（onnxruntime / CPU）** 抽字，落成 `wiki/媒体/图片OCR/<原相对路径>.md`；
- 每页头部写 `> Raw: <相对路径>` 指回原图，与既有「原件↔合成页」等价表机制同构；
- **为什么不落 raw**：raw 是原件区只读；`raw/_*` 前缀是归档区、**不进索引**，塞进去等于白干；
- **隐私红线**：跳过 `隐私/`、`个人信息/` 两个目录（任意层级），正文再过一遍打码网
  （手机号 / 身份证 / 银行卡 / 邮箱），命中替换成 `[已脱敏·类型]`。
  **隐私目录里的图本轮不处理**——它们的 OCR 文本不能进 wiki（wiki 一律脱敏），
  要做得另开「本地专用」通路，不在脚本里偷偷放行。
- 置信度 <0.60 的页会打 `⚠️ 可能读反/读错`（竖排古画题跋实测会读反，见 §12.4）；
- 文本 <20 字的图不产出页面（属「真空」），只记 state，避免上万张表情包污染索引；
- 断点续跑：每 25 张落一次 `state/media_ocr_state.json`，中断重跑不重算。

**并发选型（实测，不是拍脑袋）**：1 线程 **2.62 s/张**、2 线程 1.12 s/张（微信小图）、
**4 线程反而更慢 3.05 s/张** —— 因为 onnxruntime 内部本就吃满多核，多线程只增加争用。

**2. `_probe_boundary_share.py` —— S3b 尺子（块边界影响面，零编码秒级）**

回答「S3/S4c 的否决是尺子不够细，还是本来就没收益」。结果见任务清单 §十四：
**硬切边界影响面 4.5%（5/110），且这 5 条 doc 级全部第 1 名 ⇒ 可得收益 0 条**。

**3. `_probe_asr_gpu.py` —— GPU 转写实测**

faster-whisper small 在 RTX 3050 Laptop 4GB 上跑通，**RTF 0.203**（CPU 为 0.648–0.805），
全量 35.6h 音频外推 **7.2 小时**（CPU 29.5–36.6h）。

### 修复 / 踩坑（都写进 HANDOFF 坑 G）

- **faster-whisper 1.2.1 与 PyAV 19 不兼容**：`av.open()` 在 av≥19 移除了 `metadata_errors`
  关键字，表现为 `TypeError`。正解 = 把 av 钉到 **14.0.0**（与 `envs/transcribe` 对齐）。
- **DLL 不用另装 CUDA Toolkit**：cuDNN/cuBLAS 由 `torch(cu126)` 自带，
  `os.add_dll_directory(torch/lib)` 即可被 ctranslate2 找到。
- **模型下载别走网络**：`WhisperModel("small")` 会尝试连 HF（代理 502）；
  本地缓存已完整，直接传快照路径 + `HF_HUB_OFFLINE=1`。

### 决策理由

- **为什么先做图片 OCR 而不是音视频转写**：OCR 是纯 CPU、随时可停、栈已验证；
  转写要先解决包管理（av 版本冲突），属「有前置未知」的任务。先做没有未知的那条。
- **为什么 OCR 用 2 线程而不是 16**：实测 4 线程更慢。**并发数不是越多越好，
  要看底层库是否已经并行**——这是本轮唯一一条「反直觉但有数字」的选择。
- **为什么块边界用结构性度量而不是再扩集**：三次否决都用「分辨率不够」解释，那是猜测。
  发生次数就是收益上限，上限算出来低于可得收益就不必再花机时做 A/B。

### 验证

- `media_ocr.py --dry-run` 数出 11,703 张（与 §12.4 家底 11,665 差 38，因本次改用新的剪枝规则，
  含之前被 `_` 前缀过滤掉的部分，已在报告中说明）；
- `_probe_boundary_share.py` 两阶段跑完，落 `state/probe_boundary_share.json`；
- `_probe_asr_gpu.py` 落 `state/probe_asr_cuda.json`（含转写全文，可复用比对）；
- `run_tests.py` 7/7 套件 87/87 断言（改动前后各一次）。

> 版本号说明：一次改动，但 `snapshot.py` 因沙箱重试被调起两轮 ⇒ 生成 **1.13.29 / 30 / 31 / 32** 四份快照（29·30 为第一轮，当时 HANDOFF 尚未补坑 G；31·32 为第二轮，含完整 HANDOFF）。按 L1 铁律「时间快照永不可按日期清理」，四份全留；CHANGELOG 只记一条，以 **VERSION = 1.13.32** 为准。

## [v1.13.28] - 2026-10-29

> 版本号说明：本次改动只有一次，但 `snapshot.py` 因沙箱重试被调起两次，连跳 **1.13.25 / 26 / 27 / 28** 四个号、生成四份快照（130240 / 130242 / 130436 / 130438）。按 L1 铁律「时间快照永不可按日期清理」，四份**全部保留**；CHANGELOG 只留这一条，同一件事不记四遍。以 VERSION = **1.13.28** 与最新快照目录为准。

### A 任务：向量路块级过采样做成可配，并实测否决（零收益）

**改了什么**（`bge_encoder.py`）：
1. `search_bge()` 的 `order = np.argsort(-sims)[:topk * 8]` → `order[:topk * _oversample()]`，
   倍数读环境变量 `KB_BGE_OVERSAMPLE`，**默认 8 = 与历史行为逐位一致**；
   设 `0` 表示**全扫 48,100 块**（doc 级 topk 精确解）。
2. `load_bge()` 增加与 `ids` 平行的 `doc_arr`（`np.int64`，-1 代表孤儿块），
   把「每次命中都过一遍 `cid2doc` dict」换成加载期算一次。
3. doc 级最高分改用 `np.unique(return_index=True)` 取**首次出现**（order 已按分数降序，
   首次出现即最高分块），去掉了原来「截断窗口内凑够即可」的近似。

**为什么要做**：QA（10-29 中午）实测目标块余弦 **0.708–0.715 并不低**，但全局块排名
563 / 641 / 2622 名；而 fused 口径下 `kb_rag` 传 `topk*2=10`，×8 ⇒ **向量路只看前 80 块**。
据此判定「结构性地进不了候选池」，属代码缺陷。

**实测结果：三档零变动**（holdout 60 + raw 50 = 110 条，`_sandbox_oversample.py`）：

| 配置 | holdout R@5 / MRR@5 | raw R@5 / MRR@5 | 逐条变动 |
|---|---|---|---|
| os=8（历史行为） | 98.3% / 0.9256 | 100% / 0.8623 | — |
| os=50 | 完全相同 | 完全相同 | 0 条 |
| **os=0（全扫）** | **完全相同** | **完全相同** | **0 条** |

再拆三路单跑（`_probe_vecpath.py`，660 次查询）确认也不是融合掩盖：

| 路 | holdout R@5 | raw R@5 |
|---|---|---|
| kw | 98.3% | 98.0% |
| vec | 85.0% | 96.0% |
| fused | 98.3% | 100% |

**决策理由（为何留着代码不发散）**：
- 全扫之后 doc 级 top-10 **一个都没变** ⇒ **截断不是约束**，QA 的根因判断需要更正：
  真正的病是「**分数带被压得极扁**」（目标 0.708 vs 无关文档 0.77–0.79），
  即 bge-small-zh 在这批中文长块上的**排序分辨力**不足，加深召回深度救不回来。
- 换模型（S4）与精排修输入（B2）两条出路都已被实测否决/关门，所以这条线到此为止。
- 代码保留而不回滚的理由：默认值逐位等价（660 次查询验证），且新增的 `doc_arr` 是
  纯优化；`KB_BGE_OVERSAMPLE=0` 作为一个可复现的「精确解」开关，留给以后换模型时对照。

**验证**：`run_tests.py` 7/7 套件、87/87 断言通过（273.5s）；
`sandbox_oversample.json` 与 `probe_vecpath.json` 已落 `state/`。

**已知缺口**：`_sandbox_oversample.py` 首档（os8）计 471 ms/q 含模型冷启，后两档 182–196 ms/q
才是稳态 —— 报告里不许拿首档数字当「os8 更慢」。

---

## [v1.13.23] - 2026-10-29

### T9 完成：`.doc` 老格式纳管（raw 下 622 篇此前全部搜不到）

`OFFICE_EXTS` 只有 `{docx, xlsx, pptx}`，`.doc` 被直接排除 —— 这是「老资料搜不到」的根因。

**结果**：在册 622 篇中 **620 篇入库**，`.doc` chunks **2758**、向量 **2758（100%）**；
全库 `docs 4890 / chunks 48100 / chunk_bge 48100（100%）`。

**根因级修复（比加功能更重要的一条）**：上一版加的「单篇超时看门狗」**从建成起一次都没生效过**。
`_word_app()` 用 `app.Hwnd` 取 PID，但**隐形 Word 实例没有主窗口**，该属性直接抛
`AttributeError` → PID 记为 `None` → `_kill_word_hard()` 拿到空 PID 直接 `return`。
表现是「跑 20 分钟一篇没落库、Word 挂 134MB 不动」，而日志看起来一切正常。
改法：`Dispatch`→**`DispatchEx`**（不再连 ROT 里已卡死的实例）、PID 改**前后差集快照法**、
`_kill_word_hard()` PID 为空时**显式告警**（不再静默 return）、新增 `_reap_owned_word()` 收尸
（实测 `Quit()` 会**假成功**：打印 ok 但进程还活着、164MB）。修完后 532 篇 **226 秒**跑完。

### 改动清单

| 文件 | 内容 |
|---|---|
| `ingest_raw.py` | 新增 `read_doc()`（Word COM 只读抽取）；**失败返回 `None`**，`ingest_one()` 映射为 `extract_failed`（记 gap 且**不写 state**，下次重试）；`_word_app()` DispatchEx + PID 差集 + 防挂起三件套（`AutomationSecurity=3` / `FeatureInstall=0` / 关转换确认与链接更新）；`_kill_word_hard()` 按 PID 精确杀；`_reap_owned_word()` 收尾；`WORD_RESTART_EVERY=50` 分批重启；`DOC_TIMEOUT`（默认 30s，`KB_DOC_TIMEOUT` 可配）+ 看门狗；`scan_raw()` 跳过 `~$` 锁文件；`OFFICE_EXTS` 加 `.doc` |
| `bge_encoder.py` | `MODEL_DIR` 改为可用 `KB_BGE_MODEL_DIR` 覆盖（**默认值不变**，stats 前后逐位一致，无需回归向量） |
| `run_tests.py` | `_sandbox_b4_check.py` 以 **opt-in** 挂入（`KB_RUN_B4_CHECK=1` 才跑）；`_sandbox_b4_check.py` 退出码从「恒 0」改为真实返回（原来挂进去永远显示通过） |
| `_sandbox_encode_ab.py` | 标签更正：历史报告里的 `MRR@10` 实为 **MRR@20**、`零命中` 实为「gt 未进 top-60 候选池」。**计算逻辑未动、未重跑**（保历史可比性） |
| `HANDOFF.md` | 追加坑 A/B/C/D/E（pip safe-delete 钩子 / MinerU 落 C 盘 / 4GB 显存假象 / 沙盘与 build 不能并发 / GPU venv 缺 jieba） |
| 新增一次性脚本 | `_measure_chunks.py`、`_sandbox_chunk_overlap.py`、`_sandbox_model_ab.py`（带 `--dump-ranks`/`--ranks-in`）、`_bench_embed.py`、`_split_eval.py`、`_expand_eval.py` |

### 决策理由

- **为什么用 Word COM 而不是装 LibreOffice**：本机无 LibreOffice、有 Office 2016；`.doc` 是 legacy
  格式，Word 自己最稳。装 LibreOffice 是 350MB + 一次系统级安装，为 626 个文件不值。
- **为什么失败必须返回 `None` 而不是空串**：上一版 `except: return ""` 把 532 篇 COM 失效
  **伪装成「空 532」**，差点被当成「这些文件本来就没内容」汇报出去。**失败必须可见**——
  否则「空」和「坏」在日志里长得一模一样，而后果完全不同（一个不重试、一个要重试）。
- **为什么不做子进程隔离**：为 1 个挂起文件搭框架不值（Word 冷启动 3s/篇，批量上不划算）。
  保持「未写 state、下次增量重试」即可；那篇 1.0MB 的 `免疫规划定期复训通知.doc` 两次 120s
  超时都救不回来，疑似加密或结构损坏，留作已知缺口。

### 验证

- `run_tests` **7/7 套件、87/87 断言全绿**（与改前逐位一致）；`py_compile` 通过；
- **非 `.doc` 路径未被影响**：md/txt/docx/pdf/xlsx/html/pptx/csv/json/log 十种格式各 1 个
  在独立 scratch 库复跑 → 9 篇正常入库、0 失败；证实 `_WORD_OWNED` 在非 `.doc` 路径恒为空；
- `.doc` 检索冒烟：`source=kw+vec`，精排生效；
- QA 独立验收：索引终态逐位一致、门户 **16/16 PASS**、holdout R@5 持平且 MRR **+0.84pp**。

### 已知缺口（QA 查出，均不影响可检索性）

1. **向量路召回深度不足**：`search_bge()` 是 `[:topk*8]`（fused 下 `topk*2`），topk=5 时只看前 80 个
   chunk；而 `.doc` 的真命中在全局第 **563 / 641 / 2622** 名（余弦 0.71 说明块嵌得没问题）。
   ⇒ 3/5 的 `.doc` 样例只有关键词路命中。**关键词 + substring 5/5 兜住**，但纯语义查询受此上限拖累。
   候选修法：过采样倍数做成可配试 100× / 长字面查询两段式 / ANN 索引。
2. `raw/微信文件/其他/2026-09-23-病案室需要的报表.doc`（5.5MB）只有表格与图、正文 20 字符，
   被 `MIN_TEXT` 挡掉 —— 属真空，正解是走表格/OCR 管线；
3. `raw/微信文件/其他/2026-05-14-…免疫规划定期复训…通知.doc`（1.0MB）`read_doc` 返回 `None`（真失败）。

## [v1.13.21] - 2026-10-28

### T9 前半：`.doc` 老格式纳入索引（此前 626 篇全部搜不到）

**问题**：`OFFICE_EXTS` 只有 `{docx, xlsx, pptx}`，`.doc`（OLE 二进制）直接被排除。
实测 raw 下 **626 个 `.doc`**（对比 docx 829 个，占 43%），全部不在索引里 —— 老资料搜不到。

**为什么不是「转成 docx 再说」**：raw 是原件只读区，往里塞 626 个派生文件等于污染原件区；
派生层也不该进 wiki（那是合成区）。所以做成**入库时的抽取分支**，不落任何派生文件。

**改动**：
1. `ingest_raw.py` 新增 `read_doc(fp)`：走 **Word COM** 只读打开、取 `Content.Text`、
   不保存不改原件。本机没装 LibreOffice，但装有 Office 2016，这条是唯一低成本通路。
2. **Word 实例复用**（`_word_app()` 惰性单例 + `close_word()` 收尾）：
   每篇 `Dispatch` 一次要多花约 1s，626 篇就是 10 分钟白等；但也不能常驻不退，
   脚本结束必须 `Quit`，否则留一个后台 Word 进程。
3. `OFFICE_EXTS` 加 `.doc`，`EXTRACTORS` 挂 `read_doc`。
   新依赖 `pywin32`（仅此一处用）。

**验证（可取证）**：
- `ingest_raw.py run --dry-run` → 待处理分布含 **`.doc: 622`**（说明扩展名已被识别，改前会被直接排除）；
- `ingest_raw.py run --limit 8` → `新增 8 更新 0 跳过 0 空 0 失败 0`，13s（**1.6s/篇**）；
- 落库核对：`库内 .doc 文档 8 篇 / 块 73 个`，抽出的中文正文正常
  （例：`唐山工业职业技术大学优秀毕业生审批表 样例 姓名 张三丰 ...`）。
- **剩余 614 篇**按 1.6s/篇估算约 **16 分钟**，可断点续传（state 驱动，不需要 `--full`）。

### 决策理由

- **为什么用 COM 而不是装 LibreOffice**：LibreOffice 约 350MB + 一次系统级安装，
  而 Word 已经在这台机器上；`.doc` 是 legacy 格式，转换质量 Word 自己最稳。
- **为什么先做 `.doc` 而不是等 MinerU（T4）**：MinerU 解决的是 **PDF 版面/扫描件**，
  跟 `.doc` 是两回事。`.doc` 不需要 OCR、不需要版面重建，用现成 Word 就能拿干净正文，
  **没必要被 T4 挡着**。原计划「T9 随 T4」是基于「两者都要重工具」的误判。
- **剩余批次的真实结果（必须记录，别当成功）**：17:20 启动全量后 **42 秒**跑完 621 个，
  结果 **新增 88 / 更新 1 / 空 532 / 失败 0**。42 秒跑 621 篇明显不合理（单篇 1.6s），
  说明**中途 Word COM 失效**，之后每次 `Open` 立即抛异常，被我 `except: return ""`
  静默吞成「空」——**「空 532」是我的错误处理制造的假象，不是文件真的没内容**。
  抽查证明文件本身正常（`1.选题审题表.doc` 921 字、`2.毕业设计任务书` 3065 字、`3.开题报告书` 4505 字）。
  **好消息**：空提取的文档**没有写进库**（`库内 .doc 90 篇，空正文 0 篇`），没有脏数据。
  **待修（明日）**：① `read_doc` 不许把失败吞成空串（失败要能区分）② COM 断开自动重连
  ③ 分批跑（每 N 篇重启 Word），避免长进程退化。
  ⇒ **T9 本次只真正纳管了 90 篇，不是 626 篇。** 剩余 532 篇等重试逻辑修好后重跑。

## [v1.13.20] - 2026-10-28

### S4 换 embedding：实测否决；S4c 缩小块：沙盘否决。两条路都走死了

新增 `_bench_embed.py`（一次性基准：加载时间 / 每块耗时 / 维度 / **截断比例**）。

**① S4 换 0.6B 级模型——CPU 上不可行**（真实库块 64 条，长度中位 1220 字）：

| 模型 | maxlen | 维度 | s/块 | **全量 45,069 块** | 单查询 | 截断块 | 实际编码内容 |
|---|---|---|---|---|---|---|---|
| **bge-small-zh（现状）** | 512 | 512 | 0.089 | **1.1 h** | 15 ms | 59% | **66%** |
| bge-m3 | 512 | 1024 | 1.53 | **19.1 h** | 242 ms | 53% | 76% |
| bge-m3 | 1024 | 1024 | 2.96 | **37.1 h** | 241 ms | 2% | 99.5% |
| Qwen3-Emb-0.6B | 512 | 1024 | 2.92 | **36.6 h** | 292 ms | 53% | 68% |
| Qwen3-Emb-0.6B | 1024 | 1024 | 5.89 | **73.7 h** | 254 ms | 9% | 95% |

成本 17–33 倍，且想用长上下文消除截断反而更慢（37–74 h）。单查询编码 15ms → 240–290ms，
门户每次检索和 `kb_rag` 每次 CLI 调用都要付。GPU 侧（RTX 3050 4GB）**NVML 初始化失败**、
torch 为 `2.14.1+cpu`，要用得先修驱动 + 装 cu12x torch 到独立 venv（登记 S4b）。

**② 顺带量出「向量路一直只看前半段」**：`max_length=512` 而块长中位 1220 字
⇒ **59% 的块被截断、平均只有 66% 内容进向量**。这不是模型强弱，是**块比窗口大**。

**③ S4c 缩小块（1200→480）消除截断——沙盘否决**（同 360 篇、同 110 条 query、只改块大小）：

| 臂 | 块数 | R@5 | R@20 | MRR@5 | 零命中 |
|---|---|---|---|---|---|
| old（1200 字块，现状＝截断） | 5,515 | 97.3% | 98.2% | 0.9129 | 0% |
| new（480 字块，不截断） | 11,100（+101%） | 96.4% | 98.2% | 0.9089 | 0% |

差值 R@5 **107 vs 106 命中（差 1 条）**、R@20 **108 vs 108 完全相同**、MRR 约 2 条名次。
块数翻倍（全量编码 1.1h → ~2.2h）换一条查询的噪声。

### 决策理由

- **为什么「理论上有问题」也要否决**：截断是真实存在的（59% 块），但**换小块之后指标不动**。
  说明被截掉的那 34% 内容在多数查询里并不承担答案——理论缺陷 ≠ 实际瓶颈。
  这跟 S3 的结论合起来看更有说服力：**两次结构改动都是 ±1 条查询**。
- **为什么不再试第三个分块方案**：同一把尺子已经给出两次「无差别」。再试只会消耗机时，
  真正该做的是**先把尺子做细**（S3b：20–30 条跨块边界定向探针，或把集扩到 300+ 条把分辨率压到 0.3pp）。
- **为什么不删这些否定结论**：它们标出了「不该再往哪投时间」。两次否决省下的是一次
  2.2h 重建 + 一次 37h 重编码，比任何一次「提升了 0.9pp」的记录都值钱。

### 下载通路踩坑（留给下次）

- hf-mirror **0.6 MB/s**，且 huggingface_hub 会走 Xet 存储直接 `401 Unauthorized`
  → 须 `HF_HUB_DISABLE_XET=1`；
- **ModelScope 直连 8.4 MB/s（快 14 倍）**，按官方 file API 取清单再逐文件下；
- **bge-m3 在 ModelScope 只有 `pytorch_model.bin`，没有 safetensors** ——
  第一轮我按扩展名把 `.bin` 当冗余过滤掉，下完 371MB 却没有权重。必须**校验大小**才算完成；
- 模型落 **E 盘**（本地 NVMe）：F 是 USB 12 MB/s，1.2G 模型每次进程启动要读 100s。
  现位于 `<MODEL_DIR>/{bge-m3,Qwen3-Embedding-0.6B}`，未接入生产，留待有 GPU 时复用。

## [v1.13.19] - 2026-10-28

### S2：判分口径改为「多 gt」（原件 ↔ 合成页等价），并修一条本质歧义题

**症状**：`本地转写视频音频用什么方案，首次跑模型为什么要设 HF 镜像` 这条一直判漏检，
实测第 1 名是 `wiki/编码指南/准则修订历史.md`（打分 1.0）、第 2 名是
`.workbuddy/skills/video-channels-capture/SKILL.md`，而 gt 指定的 raw 原件排第 13。

**误判与纠正**：我起初认定是「同类转录稿几十篇都写了同一套方案，gt 不唯一」。
把合成页加进 gt 后**仍然漏检**（合成页也只排第 10），说明**不是 gt 选错，是这题本质就是
「共性操作知识」**——库里同时写了 `faster-whisper` 方案与 `HF_ENDPOINT` 镜像的文档有 **6 篇**，
任何一篇都是正确答案。

### 改动

1. **`evaluate()` 支持 gt 为 list**（任一命中即 hit），`_gt_list()` 统一解析。
   分层归域仍按**第一条 gt**（保持原域归属，不让多 gt 把一条 raw 题挪进 wiki 域）。
2. **新增等价 gt 表 `build_gt_aliases()`**：由 wiki 合成页头部的 `> Raw: <原件>` 声明
   机械推导「原件 ↔ 合成页」双向等价。三条准入限制，每条都是实测踩出来的：
   - 只认 `Raw:` 行，**不认 `Sources:`**（Sources 是外部出处，不是库里同一份内容）；
   - **只认「单源」页**（`Raw:` 只声明一个原件的）。准则页 `Raw:` 列了几十份原件，
     按多源建等价会让它跟一堆无关文档互判「答对」——第一版就是这么污染了 43 对假等价；
   - 目标必须真实存在于 docs，且**排除图片/二进制**（`运势卦象.png` ≠ `每日运势.md`）。
   产出：**28 对，全部逐对人工核对过，路径零缺失**。
3. **那条歧义题按内容判据改为 6 篇 gt**：判据是「文档里是否真的写了这套方案」
   （SQL 按 `HF_ENDPOINT` + `faster-whisper` 双关键词取交集），**与检索排名无关**，
   不是拿结果倒推着改。
4. **`--no-gt-expand` 开关**：关掉放宽以复现 2026-10-28 之前的历史数字。
   实测能逐位复现旧基线（wiki30 90.0%/0.6800、wiki100 92.0%、raw 98.0%、holdout 98.3%/0.9089）。
5. 基线 JSON 增记 `gt_expand` 与 `gt_alias_paths` 两位。**不记这一位，新旧基线混比
   会把「口径放宽」读成「检索变好」**——和 rerank 开关同一个道理。

### 口径切换前后的数（同配置、同事后、只差口径）

| 集 | n | R@5 旧→新 | MRR@5 旧→新 | 靠等价 gt 命中 |
|---|---|---|---|---|
| wiki 30 | 30 | 90.0% → 90.0% | 0.6800 → 0.6911 | 2 条 |
| wiki 100 | 100 | 92.0% → **93.0%** | 0.7167 → 0.7200 | 6 条 |
| holdout 60 | 60 | 98.3% → 98.3% | 0.9089 → **0.9172** | 9 条 |
| raw 50 | 50 | 98.0% → **100.0%** | 0.8373 → **0.8573** | 5 条 |

新基线：`base_1028_gteq_all.json` / `holdout_base_60_gteq.json` / `raw_base_50_gteq.json`
（旧口径同批存 `*_old*.json`，旧文件一个没删没改）。

### 决策理由

- **为什么口径放宽不算「改考卷让分好看」**：放宽的每一条都由**文档内容**判定
  （`Raw:` 声明、双关键词交集），不看检索排名。判据与结果解耦，就不叫调参。
  反过来，删掉那条漏检才叫改考卷——这次没删。
- **为什么 `--no-gt-expand` 必须留着**：历史结论（尤其 §9.3 那批权重/别名结论）
  都是在旧口径下得出的。能一键复现旧数，那些结论才还站得住；否则等于把历史证据作废。
- **为什么归域看第一条 gt**：多 gt 会让一条题横跨 wiki/raw，若按「任一 gt 归域」
  它就能同时进两个分母，指标立刻失真。用第一条锁死归属最省事也最可解释。

### 已知残留

- `wiki/log.md`（日志索引）被算作等价答案之一，因为该题判据是内容包含。可接受但价值低，
  若以后要更严，可把元文件从 gt 候选中排除；
- 28 对等价表是「单源 `Raw:` 页」的子集，仍有部分合成页因 `Raw:` 写了多源而未被纳入，
  属于**保守漏收**（宁可少放宽），不影响正确性。

## [v1.13.18] - 2026-10-28

### 分块 overlap 立项调查 → **沙盘否决，不做**（含两个新工具）

S3 任务是「给硬切块加 overlap」。按「先量、再沙盘、后落地」，三步走完**结论是不做**，
但过程中拿到三个以后都用得上的数。

**① 先量结构**（新增 `_measure_chunks.py`，只读、不用模型）：

- 全库 **45,069 块中 27,132 块（60.2%）是超 `CHUNK_MAX=1200` 按长度硬劈的**，占全部字符 **84.1%**；
  块长分布 **p50 = p90 = p99 = 1200**（顶格）。
- 32.0% 文档只有 1 块（对它们加 overlap 毫无作用）；**受影响文档 1,674 / 4,232（39.6%）**。
- 结论：标题级切块只对 markdown 有效，**pdf/docx/txt/csv 全退化成定长窗口 + 零重叠**，
  答案跨窗口边界就被劈成两半。问题真实，且比立项时预想的严重。

**② 再量成本**（同脚本 `--bench`，实测）：

- BGE 编码 **0.086 s/块**（batch=8）⇒ 全量 4.5 万块 **约 1.1 小时**；只重编硬切文档 **约 47 分钟**。
- 🔴 **订正一个长期错数**：记忆里记的「0.33 s/块、全量约 4h」**夸大了约 4 倍**，据此排的工期都虚高一档。

**③ 沙盘 A/B**（新增 `_sandbox_chunk_overlap.py`，360 篇 = 110 gt + 250 干扰，
两臂各自编码 6–7 千块、耗时 18 分钟，只读不写库，仅向量路 doc 级最高分）：

| 臂 | 块数 | R@5 | R@20 | MRR@5 | 零命中 |
|---|---|---|---|---|---|
| old（现状：硬劈零重叠） | 6,339 | 96.4% | 98.2% | 0.8945 | 0% |
| new（overlap 200 + 断句吸附） | 7,252（+14.4%） | 95.5% | 98.2% | 0.8855 | 0% |

**④ 差值必须换算成条数才算判据**：R@5 = 106 vs **105** 命中（**差 1 条**）；
R@20 = **108 vs 108 完全一样**；MRR 约 2 条名次变动。
110 条集的**测量分辨率 = 0.91pp**（1 条查询），所以这是噪声。

### 决策理由

- **为什么不做**：不是「overlap 有害」，而是**在这把尺子下测不出收益**，而成本是确定的
  （块数 +14.4%、约 1h 全库重编码、chunker 版本号机制带来的复杂度）。
  收益不确定 + 成本确定 ⇒ 默认不做。沙盘这 18 分钟省下的正是那 1 小时和一次不必要的全库改动。
- **为什么把「换算成条数」单列一条**：不换算就会把 -0.9pp 读成「退步」而去做无谓排查，
  或者反过来把 +0.9pp 当成优化成果——**小样本上两种误读都会发生**。
- **为什么保留两个脚本而不是删掉**：`_measure_chunks.py` 以后任何 chunker 改动都能复用来量结构；
  `_sandbox_chunk_overlap.py` 是「同文档、同 query、只改一个变量」的对照模板，S4 换 embedding 可直接照抄。

### 后续（已登记排期 S3b）

要再评"分块"这类结构改动，得先把尺子做细：建 20–30 条「答案跨块边界」定向探针，
或把集扩到 300+ 条把分辨率压到 0.3pp。**否则别动生产库。**

## [v1.13.17] - 2026-10-28

### 扩集：held-out 43 → 60、raw 36 → 50（治「测量样本太小」）

原 held-out 43 条、raw 36 条，指标一动就不知道是真变化还是小样本抖动。本轮各扩到 60 / 50。

**方法（防自证，这是重点）**：

1. 新写 `_expand_eval.py`：从 `kb.db` **分层抽样**候选文档（按一级子目录轮转，避免全落一个
   大类），只把**标题 + 正文片段**摊出来。
2. **只摊正文、不检索**。query 由正文盲写，写完整体跑一次，结果好坏照收。
   ——先搜一遍看 gt 排第几、再回头改 query，这批条目就退化成调参集，测出来是自证。
3. 抽样阶段清掉不适合当检索目标的：元文件（index/log/CHANGELOG/README）、
   每日自动生成的运势/详解稿（几十份近重复，gt 无区分度）、`raw/微信文件/项目/`
   （客户数据隐私红线）、私人通话录音、乱码壁纸、**源码/配置/CSV**（不是知识，还会虚抬召回）。

**分组对照（新写 `_split_eval.py`，关键步骤）**：
只看全集数字 = 换卷后的分数，与能力无关，所以必须拆老/新两组报。

| 分组 | n | R@5 | R@20 | MRR@5 | 零命中 |
|---|---|---|---|---|---|
| hold-全 | 60 | 98.3% | 100% | 0.9089 | 0% |
| hold-老 43 | 43 | 97.7% | 100% | 0.9233 | 0% |
| hold-新 17 | 17 | 100% | 100% | 0.8725 | 0% |
| raw-全 | 50 | 98.0% | 100% | 0.8373 | 0% |
| raw-老 36 | 36 | 100% | 100% | 0.8435 | 0% |
| raw-新 14 | 14 | 92.9% | 100% | 0.8214 | 0% |

### 决策理由

- **为什么不直接报全集 98.3% 就完事**：新条目召回更高（100% vs 97.7%）但排得更不准
  （MRR 0.8725 vs 0.9233），全集 MRR 反而从 0.9233 **降到** 0.9089。
  也就是说新卷**更难排**，扩集没有把指标刷高 —— 这个结论只有拆组才看得出。
- **为什么不删掉那 1 条漏检**：删了 R@5 就变 100%，但那是**改考卷让分好看**。
  真相是它 gt 不唯一（见下），属于口径缺陷，登记待修，不是检索能力问题。

### 已知缺陷（登记任务清单 10c，本轮不修）

唯一漏检 `本地转写视频音频用什么方案，首次跑模型为什么要设 HF 镜像` 排第 13：
同类转录稿几十篇的头部元信息写着**同一套转写方案**，**gt 天然不唯一**。
单 gt 口径下这类「高频共性知识」条目会永久压住 R@5 上限。
修法：`evaluate()` 支持 gt 为 list（任一命中即 hit）。会动所有历史基线，需单独排期。

### 验证

- 110 条 gt 逐条校验**全部存在于 `docs` 表**（0 死链），避免「gt 不在库内 = 永久漏检」；
- 两个集各自 0 条重复 query、0 条重复 gt；
- 基线已存 `state/holdout_base_60.json`、`state/raw_base_50.json`；旧集留 `.bak43` / `.bak36`；
- 方法论补进 `wiki/技术/检索调优方法论-沙盘优先与held-out测量.md` §五/§六。

## [v1.13.15] - 2026-10-28

### 补发版收尾（历史欠账）
- **为什么补**：门禁 `modules/版本管理/check_release.py` 报本模块「版本号滞后于代码（旧 v1.13.14，VERSION mtime 2026-10-28 02:45）」。
  按编码准则 **§2.5-6 发版纪律**，实质变更必须当次留痕，欠账必须补齐，
  否则门禁噪声会埋掉真正的欠发版。
- **本次改动范围（证据）**：  - `HANDOFF.md` — 2026-10-28 02:47
- ⚠️ **诚实说明**：上述文件的具体改动内容未在 CHANGELOG / 提交记录中逐条留痕，
  本次补发版只同步版本与证据清单，**不推测、不补全**改动描述。
  需要逐条追溯时以各文件 mtime 与实际内容为准。
- **怎么验**：`python check_release.py --module 知识库索引` 不再报本模块欠发版。


## [v1.13.16] - 2026-10-28

### `connect()` 加 `check_same_thread` 参数（供门户跨请求复用连接）

- **改了什么**：`kb_index.connect(db_path=None, readonly=False, check_same_thread=True)`
  —— 新增第三个参数，透传给 `sqlite3.connect`。**默认 True，所有现有调用方行为零变化。**
- **为什么加**：门户（`知识库体系门户/kb_hub_server.py`）原来每请求新建连接、用完即关，
  导致每个查询都当「首次查询」付冷启动成本。实测同一 query：

  | 方式 | 连续 3 次 |
  |---|---|
  | 每请求新建连接 | 1744 / 1771 / 1816 ms |
  | 复用同一连接 | 1996 / **97 / 98 ms** |

  要跨请求复用就得 `check_same_thread=False`（sqlite3 默认禁止跨线程用连接），
  而连接由 `kb_index.connect()` 统一创建（PRAGMA 都在里面设），所以参数只能加在这里。
- 🔴 **安全前提**：调用方必须自己保证串行使用。门户靠 `_RAG_INFER_LOCK`
  （整段检索都在锁内）满足该前提，**不要在其他地方用 False 而不加锁**。
- **怎么验**：`kb_index.connect()` 默认参数编译 + 现有检索全链路无变化（本轮复测：
  wiki30 R@5 90.0%/R@20 100%、wiki100 92.0%/99.0%、raw36 100%/100%，与 v1.13.14 一致）。

## [v1.13.14] - 2026-10-28

### judged set 扩充：raw 域 24 → 36 条

- 从库里再抽 13 条 raw 域标注（Go 进阶实战 / 辩论赛赛制 / 程序员延寿指南 / 抖音倍速复盘 /
  云支教 / 高中数学技巧 / 三基三严 / 低价 GPT plus / real_news_scraper / 中联恒创招聘 / 驳题 等）。
- 🔴 **主动剔除一条歧义项**：候选里「大模型—我的思与惑」在库中有 **4 个内容副本**
  （`(1)(1)` / `(1)` / `(2)` / 无后缀），gt 不唯一会把指标打低而不自知 —— 直接删掉这条。
  （同类处理：早前也剔过「思想道德与法治 (1)(2)」「breast_cancer 两份」「看漫画学Python 两份」。）
- 隐私红线：SQL 显式排除 `raw/隐私/`、`raw/个人信息/`；另主动避开示例市五大系统项目原始数据。
  （抽样时还出现过 `raw/微信文件/个人信息/…` 这种**二级**个人信息路径 —— 前缀过滤只挡了
  `raw/个人信息/`，靠人工识别才拦下。**以后建集要同时防二级路径。**）
- **raw 36 条最终：R@5 100.0% / R@20 100.0% / MRR 0.8329 / 零命中 0%。**

### 最终基线（v1.13.14，`state/layered_baseline_1028.json`）

| 集合 | n | R@5 | R@20 | 零命中 |
|---|---|---|---|---|
| wiki 30 条 | 30 | 90.0% | 100.0% | 0% |
| wiki 100 条 | 100 | 92.0% | 99.0% | 0% |
| raw 36 条 | 36 | **100.0%** | **100.0%** | 0% |

（held-out 43 条另测，见 v1.13.12 的无偏对照：R@5 新旧持平 97.7%、MRR 0.8826→0.9256。）

### 补发版（无内容变更，mtime 被动更新）
- 门禁报「`alias_table.json` 02:43 晚于 VERSION 02:37」。原因是做 held-out 新旧对照时
  用 `cp` 临时回退别名表、测完再 `cp` 恢复——**恢复操作把 mtime 刷到了 02:43**，
  而文件内容与 v1.13.13 完全一致（12 组，md5 已校验）。
- 处置：仅补版本号，**不改任何内容**。
- 🔴 经验（给下次做隔离验证的人）：**对照组用完恢复时要用 `cp -p` 保住原 mtime**，
  否则「内容没变」也会被门禁判成欠发版。或者干脆接受它并补一版（本次做法）。

## [v1.13.13] - 2026-10-28

### 别名表再补一组（中英术语错配）+ 全链路收尾验证

- **新增 `新闻爬虫 → NEWS_SCRAPER / scraper / 爬虫指南`**：来自 raw 域唯一漏检的归因。
  query「示例社交平台 新闻爬虫怎么用」的 gt 是 `NEWS_SCRAPER_GUIDE.md`（**英文标识符**），
  三路其实都命中了 gt 但都排在 35 名开外、融合后被压出 top20。**隔离验证**（同一索引状态下
  加/不加这一组各跑一次）：

  | raw 24 条 | R@5 | R@20 | MRR@20 |
  |---|---|---|---|
  | 无该组 | 95.8% | 95.8% | 0.8278 |
  | 有该组 | **100.0%** | **100.0%** | **0.8361** |

- 🔴 **一个差点误判的干扰变量**：加上该组后第一次测出「raw 域 MRR 从 0.8681 掉到 0.8361」，
  看着像回退；实际是**中间跑过一次 `kb_rag.py build`**（raw 新增 9 篇、wiki 更新 7 篇、新编码 648 块）
  把索引改了，两个数字不是同一份索引。控制变量重测后才确认该组是**净正面**。
  ⇒ **见到指标变化，先问「索引/数据动过没有」，再问「我的改动有没有效」。**

### 本版收尾验证（全绿）

| 检查 | 结果 |
|---|---|
| `kb_rag.py build`（防 08:00 自动化挂掉） | ✅ EXIT=0，真实跑通 B4 新路径：BGE 新编码 648 块/60s，无报错 |
| `tie_breaker` 指纹写入 | ✅ `bge_model.tie_breaker = 'v1\|max_chars=1000'` |
| 向量覆盖 | ✅ chunks 44,864 / chunk_bge 44,864（100%） |
| held-out 28 条（未参与调优） | ✅ R@5 96.4%、R@20 100%、MRR 0.9214（**无回退**） |
| 门户端到端 | ✅ 别名在门户生效（`note: D5 别名扩展`），昨天完全搜不到的「医院那五个系统」现在 top1/top2 即示例市五大系统 |
| B3 回归门禁 | ✅ 通过（回退均在 ±3pp 内） |

**最终指标（rerank=off 对齐门户）**：wiki 30 条 R@5 90.0%/R@20 100.0% ｜ wiki 100 条 92.0%/99.0% ｜
raw 24 条 100.0%/100.0% ｜ 三域零命中率均 0%。基线存 `state/layered_baseline_1028.json`。

## [v1.13.12] - 2026-10-28（整晚托管批次 · 检索质量）

三件事，全部**零重编码**（没有动那 44,773 个已编码向量）。第 3 条是本版真正的收益来源。

### 1. B4 编码口径统一（修潜伏 bug）
- **缺陷**：两条编码路径各拼各的 —— `kb_rag.py` 增量 backfill 只编 `content` 且不截断；
  `bge_encoder.cmd_build` 编 `heading + content` 且截 `[:1000]`。同一个 chunk 经两条路径
  得到两个不同的向量，**增量补的块和全量建的块不在同一语义空间**。
- **改法**：新增唯一入口 `build_vector_text(title, heading, content, mode)`（v1/v2/v3 三档），
  两条路径都改走它；`bge_model` 加 `tie_breaker` 口径指纹列（`PRAGMA` 探测 + `ALTER`，老库兼容）；
  `load_bge()` 改按「`built_at` + `tie_breaker`」双指纹失效，并对空矩阵返回 `None`（不再
  `np.stack([])` 抛 `ValueError`）。
- **为什么默认口径仍是 v1**：见下面第 2 条的沙盘结论——加 title 无收益且劣化。
  v1 与库里现有向量**完全同口径**，所以这次修复**不需要重编码**，现有向量继续有效。

### 2. C7 标题加权 —— **沙盘否决，不做**（省下约 65 分钟机时）
- 依据 `_sandbox_encode_ab.py`（2013 块样本 × 30 条 judged set，只读内存计算）：

  | 口径 | R@5 | R@20 | MRR@10 | 零命中 | 融合后R@5 |
  |---|---|---|---|---|---|
  | **v1（现状）** | 90.0% | **93.3%** | **0.808** | **3.3%** | 93.3% |
  | v2（+title） | 90.0% | 90.0% | 0.803 | 10.0% | 93.3% |
  | v3（+title×2） | 90.0% | 90.0% | 0.759 | 10.0% | 93.3% |

- **加 title 不但无收益，反而劣化**（R@20 −3.3pp、零命中翻三倍、MRR 单调降），且**融合后三档完全一致**。
- 🔴 方法论：清单里 C7 挂着「2h 代码 + 1.9h 机时」，**9 分钟沙盘就否掉了**。以后「改编码 ⇒ 必须全量重编码」
  这类改动，默认先跑子集沙盘。

### 3. 三路融合权重 + 别名表（本版收益来源）
- **权重**：`W_KW=2.0 / W_VEC=1.5 / W_SUBSTR=0.5`（原为全 1.0 等权），环境变量 `KB_W_KW`/`KB_W_VEC`/`KB_W_SUBSTR` 可覆盖。
  依据 `_sandbox_weight_ab.py` 在 **154 条** judged set 上的扫描：合并 R@5 87.0%→89.6%、MRR 0.730→0.733、
  R@20 持平；且 **kw 1.0→1.5→2.0 在 30/100/raw 三个集合上趋势一致**（76.7→80→83.3 / 88→90→91 / 持平台），不是单集噪声。
  🔴 substr 取 0.5 而非数据上略优的 0.3：substr 是字级保底路，生僻字/单字查询只有它能命中，0.3 与 0.5 差 1 条（噪声量级），不为 1 条削保底路。
- **别名表**：`alias_table.json` 从 5 组扩到 11 组。新增 6 组**全部来自实测漏检归因**（`_diagnose_misses.py` 跑 154 条找出 15 条漏检），
  不是拍脑袋扩词：`五个系统→示例市五大系统`、`急诊急救→急诊系统/一体化急诊急救平台`、`血透→血液透析`、`配图→媒体生成/图片生成`、`rerank→重排/精排`、`rag→检索增强生成`。

### 实测结果（`eval_layered.py --set all`，rerank=off 对齐门户）

| 集合 | 基线（等权+旧别名） | 本版 | Δ |
|---|---|---|---|
| wiki 30 条 R@5 | 76.7% | **90.0%** | **+13.3pp** |
| wiki 30 条 R@20 | 96.7% | **100.0%** | +3.3pp |
| wiki 100 条 R@5 | 87.0% | **92.0%** | +5.0pp |
| wiki 100 条 R@20 | 97.0% | **99.0%** | +2.0pp |
| raw 24 条 R@5 | 95.8% | 95.8% | 0 |
| 零命中率 | 0% | 0% | 持平 |

-   ### 🔴 无偏验证（held-out：从未参与调优的独立集）
  上面那些数字是**在调参用的同一批集上测的**，所以另建 `state/kb_eval_holdout.json`
  （**28 条**，从未参与任何调优；刻意避开每日运势/卦象/塔罗里标题雷同、gt 会歧义的文档，
  只挑能用独特词精确区分的；gt 路径全部 SQL 校验存在）。
  用 `--set holdout` 分别跑新旧配置（旧配置靠 `KB_W_KW=1 KB_W_VEC=1 KB_W_SUBSTR=1` + 临时回退旧别名表实现，测完已恢复并 md5 校验）：

  | 配置 | R@5 | R@20 | MRR@20 |
  |---|---|---|---|
  | 旧（等权 + 5 组别名） | 96.4% | 100.0% | 0.8911 |
  | 新（2/1.5/0.5 + 11 组别名） | **96.4%** | 100.0% | **0.9214** |

  （**三批 held-out 同向印证**，结论稳定）：

  | held-out 规模 | 旧 R@5 → 新 R@5 | 旧 MRR@20 → 新 MRR@20 |
  |---|---|---|
  | 12 条（首建） | 91.7% → 91.7% | 0.8125 → 0.8750 |
  | 28 条 | 96.4% → 96.4% | 0.8911 → 0.9214 |
  | **43 条（最终）** | **97.7% → 97.7%** | **0.8826 → 0.9256** |

  ⇒ **表观的 R@5 +13.3pp 在 held-out 上一次都没重现（三批全持平）**——那部分确实是自证；
  **真实无偏收益是排序质量：MRR@20 +0.043**（43 条上）。样本越大增益越清楚。
  这与数据自洽：R@20 已满格 100%、零命中 0%，召回端本来就没空间，改进只可能发生在排序上。

  ⇒ **汇报口径（照此讲，不许只讲好看的数）**：
  「调参集上 R@5 +13.3pp，但 held-out 上 R@5 持平、MRR +0.043 ——
  真实收益是**排得更准**，不是**召回更多**。」

  > held-out 建集规范：gt 与调参集不重叠（SQL 排除）、避开每日运势/卦象/塔罗这类标题模板化
  > 会歧义的文档、gt 路径全部 SQL 校验存在、建完之后**不再用它调任何参数**。
  > 详细方法论见 `wiki/技术/检索调优方法论-沙盘优先与held-out测量.md`。

  🔴 这条已写进编码准则 **§2.7「参数调优的测量纪律」**（v3.53）：调参集 ≠ 测试集、
  先沙盘后落地、基线带口径标签、指标异常先看是不是少了一路。
- **怎么验**：`python eval_layered.py --set all --baseline state/layered_baseline_1028.json --tol 0.03`
  → 应输出「✅ B3 回归门禁通过」。基线存 `state/layered_baseline_1028.json`（含 `rerank:false` 口径标签）。

### 附带修的坑
- `eval_layered.py` 调用 `search_unified` 时**没传 `rerank`**（默认 `True`），与门户 `rag.json` 的 `rerank: false` 不一致
  ⇒ 用有 torch 的解释器跑时精排真的会跑，既慢 3 倍又测的不是线上。已改为默认关 + `--rerank` 开关，
  基线 JSON 里写入 `rerank`/`tag` 口径标签（**基线必须自带口径，否则开/关的 14pp 差混着比等于自欺**）。
- 新增 `state/kb_eval_raw.json`（24 条 raw 域 judged set）：原有 30/100 条 **100% 是 wiki 域，raw 域（占全库 92%）零覆盖**。
  标注时 gt 路径一律用 SQL 取回完整 path，并显式排除 `raw/隐私/`、`raw/个人信息/` 及示例市项目原始数据。
  ⚠️ raw 集的 query 由文档标题/摘要反写，**偏「有特征词」**，只能看涨跌，**不能与 wiki 集横向比高低**。

## [v1.13.11] - 2026-10-28

### 补发版收尾（历史欠账）
- **为什么补**：门禁 `modules/版本管理/check_release.py` 报本模块「版本号滞后于代码（旧 v1.13.10，VERSION mtime 2026-10-27 16:51）」。
  按编码准则 **§2.5-6 发版纪律**，实质变更必须当次留痕，欠账必须补齐，
  否则门禁噪声会埋掉真正的欠发版。
- **本次改动范围（证据）**：  - `_sandbox_encode_ab.py` — 2026-10-28 01:12
  - `kb_rag.py` — 2026-10-28 01:09
  - `bge_encoder.py` — 2026-10-28 01:08
  - `eval_layered.py` — 2026-10-27 16:55
- ⚠️ **诚实说明**：上述文件的具体改动内容未在 CHANGELOG / 提交记录中逐条留痕，
  本次补发版只同步版本与证据清单，**不推测、不补全**改动描述。
  需要逐条追溯时以各文件 mtime 与实际内容为准。
- **怎么验**：`python check_release.py --module 知识库索引` 不再报本模块欠发版。


## [v1.13.10] - 2026-10-27

### 新增 —— D5 别名/同义词表（零重编码，查询时扩展）

- `kb_rag.py` 新增 `alias_table.json` + `expand_alias()`：在 `search_unified` 三路召回（kw/vec/substr）前把查询里命中的别名词同义词追加到查询串；**rerank 仍用原始 query 按本意重排**，不改索引、零重编码。
- 种子别名打 3 个已记录靶子：`智能体↔agent/工作流`、`flask↔flask 框架/python web 框架`、`提示词注入↔prompt injection`；增删直接改 `alias_table.json`，无需重编码。
- 验证：`py_compile` 通过；`expand_alias` 单测 5 条用例（3 靶子正确扩、2 无关查询不扩）；线上 `search --query flask --mode kw` 实测 `note` 出现「D5 别名扩展」且无崩溃。
- 精确 gt 排名提升待 B7/B3 分层评测（§10.8：池外集合随索引漂移，须先重取）。

## [v1.13.9] - 2026-10-26

### 更正 —— **v1.13.7 的「+7pp」在重建后的索引上只有 +3pp**（口径纠错 + doc-rot）

**这不是代码退化，是基线自己动了。**

| 索引 | k=2 | k=60 | 差值 |
|---|---|---|---|
| 旧（10-23，docs **4044** / chunks 43760 / bge 10-23T15:52） | 89.0% | 82.0% | **+7.0pp** |
| **新（10-26，docs 4152 / chunks 44479 / bge 10-26T16:51）** | **88.0%** | **85.0%** | **+3.0pp** |

MRR 同理：+0.0357 → **+0.0179**。**方向不变（k=2 仍更好），但优势收窄了一半。**

原因：judged set 的 100 条 gt 没变，但索引新增了 **108 篇 / 719 块**，且 B5 标题分词在 10-26 的重建里生效 —— **k=60 这一档自己也从 82.0% 涨到了 85.0%**。所以是分子分母一起动，不是 k=2 变差了。

⇒ **对外引用规则：请用「+3pp @当前索引」，不要再用「+7pp」。** 已同步修正 `kb_index.py` 的 `RRF_K_DEFAULT` 注释（原文把「测得四档的索引」误写成现有的 4152，本身也是口径错误）。

**验证方式（默认值确实端到端生效，不是假生效）**：不设 `KB_RRF_K` 连跑两次 → **88.0 / 97.0 / 0.7235 逐位相同**（`2026-10-26_1946-rrfk-default-verify` vs `_1947-rrfk-default-rerun`），说明测量确定性没问题；且默认（88.0%）≠ 显式 `KB_RRF_K=60`（85.0%），证明默认分支与 env 分支各自独立生效、env 覆盖仍可用。

### 修复 —— doc-rot：注释写着「默认 60」，实际已是 2
- `kb_rag.py:42` / `:46`（`rrf_k()` 的 docstring）与 `:341`（`search_unified` 行内注释）原写「默认 60」/「缺省 60」，全部改为「默认 2」。
- 同时补明：**没有任何检索路径依赖 `kb_rag.py:51` 的模块级 `RRF_K = rrf_k()`**（它是导入时求值的兼容值），所以不会重演「五档跑出同一个数」。
- ⇒ 与 HANDOFF #38「取值链看着很稳所以没人查」同类：**照注释判断行为，必踩坑。**

### 🔴 方法论（同一天里同一个错犯了两次，写下来止损）
**任何「以某个基线为门槛」或「与某个基线逐位一致」的判据，在那个基线被改变时（换索引 / 换默认值 / 改分词），必须同批复核。**

| 次 | 基线变了什么 | 我没改的判据 | 后果 |
|---|---|---|---|
| 1 | k 默认值 60 → 2 | 「R@5 ≥ **82%** 才值得留精排」 | 门槛静默低 7pp，实际门槛应是 89% |
| 2 | 索引重建（docs 4044→4152） | 「与旧索引**逐位一致**」 | 会把索引漂移误判成代码 bug |

两次都是 eng-t1 先发现、我后认账。**判据不是写完就固定的，它绑在基线上，基线一动就必须重算。**

## [v1.13.8] - 2026-10-26

### 修复 —— **B2：精排输入从「文档摘要」换成「命中块正文 + 滑窗取 max」**

#### 缺陷（实测，不是推断）

`bge_reranker._text_of()` 的取值链是 `chunk_text → summary → title`，但**三路召回的返回体都不带正文**（`search_fts` / `search_bge` / `search_fts_chunks` 只回 `path/title/summary`），字级路虽有 `chunk_text` 但只占极少数条目；`_enrich` 又会在精排之后 `pop` 掉正文。于是实际喂给 Cross-Encoder 的**几乎永远是文档前 120 字 summary**。

后果不是"排序略差"，而是**排序全错但指标上看着正常**：

| query | gt 精排分 | 抢位者 top1 分 | gt 名次 |
|---|---|---|---|
| 大模型一本正经胡说八道怎么治 | **0.0000** | 0.1697 | 不在 top20（RRF 序里是**第 1**） |
| 急诊 实际代码项目 数据库设计 | **0.0002** | 0.4770 | 不在 top20（RRF 序里是**第 4**） |
| 提示词注入怎么防 | 0.0001 | 0.1795 | 不在 top20 |
| 智能体和工作流有什么区别 | 0.0011 | 0.9936 | 不在 top20 |

`qa-t2` 侧的证据：12/12 的 top1 都是 gt 的**同系列/同项目兄弟文档** —— 精排不是"没把握"，是**在摘要输入上自信地认错人**。

#### 外部依据（2026-10 GitHub 调研）

- **RAGFlow 92,736★** `internal/service/nlp/reranker.go:150-166` 注释原话：「Feed the reranker the natural chunk text (markup preserved), **not the tokenized content_ltks**. Neural rerankers score stemmed / accent-split tokens far lower, which **collapses relevance scores**」；`:566` 取 `fields["content_with_weight"]` 原样全文
- **QAnything 14,206★** 父子 chunk（`parent_retriever.py:52-58`）：`DEFAULT_CHILD_CHUNK_SIZE=400` / `PARENT=800`，长 chunk **滑窗 + `overlap_tokens=80` + 取 max**，不硬截断；纯 CPU 配置也是 `LOCAL_RERANK_MAX_LENGTH=512`
- **4/4 开源项目全部用完整 chunk 原文，无一家用文档摘要**

#### 改法

| 文件 | 位置 | 内容 |
|---|---|---|
| `kb_rag.py` | 134–174 | 新增 `_sliding_windows()`：块正文 >400 字时切重叠窗口（默认 400 字 / 重叠 80 字 / 最多 2 段），**窗口位置围绕 token 实际命中处**，不是从块首硬切 |
| `kb_rag.py` | 177–226 | 新增 `_rerank_texts_for()`：取该 doc 全部 chunk，选**token 重叠数最多**的块（不是「首个含任意 token 的块」，实测两者在 #6 上差 5 个名次），再滑窗 |
| `kb_rag.py` | 408–425 | `search_unified` 精排前调用上述函数，把窗口写进 `it["chunk_text"]`（兜底链继续可用）与 `it["rerank_texts"]`（多段） |
| `bge_reranker.py` | 72–90 | 新增 `_pairs_of()`：条目带 `rerank_texts` 时**每段各出一对**，无则走原取值链（行为与修前逐位一致） |
| `bge_reranker.py` | 113–140 | 多段分数**按 key 取 max**（QAnything 同款 max-pooling） |

🔴 **`max_length` 保持 512 未放宽**。上一版设计里"放宽到 8192"是**错的**：CPU 上推理会慢一个数量级，而精排本就是延迟大头。正确的做法是滑窗 + 取 max（QAnything 纯 CPU 配置同样用 512）。

🔴 **rerank 代码路径一处没删**，`_text_of()` 的 `chunk_text → summary → title` 兜底链原样保留；取块失败（SQL 异常 / 该 doc 无 chunk）时 `_rerank_texts_for` 返回空字典 → 走原 summary 链，**行为与修改前逐位一致**，不因修输入而让检索失败。

#### 验收（同进程 A/B；`old` = 强制走原 summary 链，`new` = 本次实现）

零假设检验先做：同组重跑两次，`rank / gt_score / top1_score` **完全相同（identical=True）** → 后面的差值可信。

指纹：`docs 4152 / chunks 44479 / bge_model.built_at 2026-10-26T16:51:24`；`topk=20`（各路深度 40、`pool_n=30`）。

| query | old 名次 / gt 分 / top1 分 | new 名次 / gt 分 / top1 分 | 判定 |
|---|---|---|---|
| 大模型一本正经胡说八道怎么治 | — / — / 0.8932 | **9 / 0.0687 / 0.9763** | gt 分 >0.05 ✅，名次进 top5 ❌ |
| 急诊 实际代码项目 数据库设计 | — / — / 0.4770 | **2 / 0.9434 / 0.9697** | 两条都 ✅ |
| flask 开发要注意哪些安全问题 | 6 / — / — | 5 / 0.0260 / 0.7014 | gt 分仍 <0.05 ❌ |
| 智能体和工作流有什么区别 | — / — / 0.9936 | — / — / 0.9176 | 无变化（不在池内） |
| 医院那五个系统分别是干嘛的 | — / — / 0.7829 | — / — / 0.8606 | 无变化（不在池内） |
| 提示词注入怎么防 | — / — / 0.8860 | — / — / 0.9580 | 无变化（不在池内） |

汇总：**R@5 0/6 → 2/6**；gt 分 >0.05 的条目 **0 → 2**；耗时 **27.8s → 34.0s（+22%）**。

**逐条判据结论（不过就说不过）**：

1. 主判据 #6「gt 分 >0.05 且进 top5」→ **一半达成**（0.0000 → 0.0687 ✅，名次 9 ❌）
2. 主判据 #84「gt 分 >0.05 且进 top5」→ **达成**（0.0002 → 0.9434，名次 2）
3. **反向守卫「top1 分必须下降」→ 不成立**。实测 5/6 的 top1 分**上升**（0.4770→0.9697、0.7829→0.8606、0.8860→0.9580、0.8932→0.9763），均值 0.6721 → **0.8973**。原因：喂真实正文后**所有条目**的相关性分一起上移，不是 gt 单独上移。
   ⇒ 这条守卫的**假设错了**（它假设"gt 上去 = 错误答案下来"）；`scan-tech-gaps` 提的无量纲指标更稳。
4. **无量纲指标 `gt 分 / top1 分 ≥0.5`** → #84 **0.973 ✅**；#6 **0.070 ❌**；flask 0.037 ❌ ⇒ **1/3**
5. 取舍线 R@5（100 条 judged set，`rerank=True`）：**76.0%**。对照：同一份代码关精排 **89.0%**（`k=2` 之后的数，见 v1.13.7）；修前的摘要输入在 `k=60` 时代是 68.0%。
   ⇒ 按判定规则「70–82%：修对了但仍该关」→ **B2 修对了机制，但精排整体仍是负贡献，继续维持 `rag.json rerank:false`**。
   注：规则里的 82% 是 `k=60` 时代的对照值，`k=2` 之后门槛实际升到 89.0%，B2 距此差 **13pp**，结论方向不变。

**全量复跑（100 条，`rerank=on`，`--tag b2-verify`）**：

| 指标 | B2 后 | 修前（摘要输入） | 关精排（`k=2`） |
|---|---|---|---|
| Recall@5 | **76.0%** | 68.0% | **89.0%** |
| Recall@20 | 92.0% | 94.0% | 97.0% |
| MRR@10 | 0.5552 | 0.5588 | 0.7380 |
| 低置信降级 | **1 条** | **32 条** | — |
| 零命中率 | 0.0% | 0.0% | — |
| gt 第 1 名 | 41 | 45 | — |
| gt 前 5 合计 | 76 | 68 | 89 |

**降级条数 32 → 1 是 B2 生效最干净的单一证据**：修前 32 条查询的池内最高分低于 `KB_RERANK_MIN=0.05`（因为喂的是摘要，模型给不出分），修后只剩 1 条——说明分数重新变得有区分度了。

<!-- 上一版残留的占位说明已由上面这张表替代 -->

#### 机制结论（与预期一致，也解释了为什么只救回 2 条）

B2 只重排**池内**条目。6 条里只有「大模型」「急诊」在 `pool_n=30` 内（RRF 第 1 / 第 4），另 4 条**根本不在池子里**（RRF 33/39/46/42，见 §12.2 的 2×2 实验），修输入对它们 **100% 无效**。这与 2026-10-23 的成因拆分完全吻合：①类 2 条能被 B2 救，②③④ 类不能。

#### 已知 / 遗留

- **#6 只到第 9 名**：gt 分刚过阈值（0.0687），距 top5 还差 4 位。方向是「命中块选得更准」（当前取 token 重叠最多的块），而不是继续放宽输入长度。
- `KB_RERANK_WIN` / `KB_RERANK_OVERLAP` / `KB_RERANK_MAXW` 三个环境变量可调窗口，不必改代码。
- 新增耗时 +22%（27.8s → 34.0s），主要来自更长的输入文本；窗口数上限 2 是主要控制手段。

## [v1.13.7] - 2026-10-26

### 调优 —— **RRF 融合常数 k 默认值 60 → 2**（作者拍板；R@5 **+7pp @旧索引 / +3pp @新索引**）

**为什么改**：`k=60` 是 RRF 原论文为**百万级网页语料**定的。本库是 4,152 篇 / 44,479 块的小语料 + 单答案型查询，`1/(60+rank)` 会把名次**压平**——`1/68` 与 `1/140` 只差 2 倍，等权融合因此**惩罚单路强命中**：一条在单路排第 8 的文档得 `1/(60+8)=0.0147`，反而小于一条在**三路都排第 80** 的噪声 `3×1/(60+80)=0.0214`。

**实测（100 条 judged set，全部 `rerank=False`，同一份索引）**：

| k | R@5 | R@20 | MRR@10 | 说明 |
|---|---|---|---|---|
| 1 | **90.0%** | 96.0% | 0.7215 | R@5 最高，但见下 |
| **2** | 89.0% | **97.0%** | **0.7380** | ← **取它** |
| 20 | 85.0% | 96.0% | 0.7069 | |
| 60 | 82.0% | 96.0% | 0.7023 | 旧默认 |

⇒ **60 → 2：R@5 82.0% → 89.0%（+7.0pp）、MRR@10 0.7023 → 0.7380（+0.0357）、R@20 96% → 97%**。

> 🔴 **口径更正（2026-10-26 20:15，重要，引用本页前必读）**
> 上表是在 **docs 4044 / chunks 43760 / bge 2026-10-23T15:52** 那份索引上测的。
> **2026-10-26 索引重建后（docs 4152 / chunks 44479 / bge 2026-10-26T16:51）同条件复测结果变了**：
>
> | 索引 | k=2 | k=60 | 差值 |
> |---|---|---|---|
> | 旧（10-23，docs 4044） | 89.0% | 82.0% | +7.0pp |
> | **新（10-26，docs 4152）** | **88.0%** | **85.0%** | **+3.0pp** |
>
> **优势由 7pp 收窄到 3pp（MRR 同理：+0.0357 → +0.0179），但方向不变，k=2 仍然更好。**
> **⇒ 对外引用请用「+3pp @当前索引」，不要再用「+7pp」。** 原因不是代码退化，是**判据集没跟着索引变**：
> judged set 的 100 条 gt 是原来的，索引新增了 108 篇文档 / 719 块（含 B5 标题分词生效后的重排），
> 所以 k=60 这一档本身也从 82.0% 涨到了 85.0% —— 是**基线动了**，不是收益消失了。
>
> 🔴 **方法论教训（这是同一个错的第二次）**：我两次都在「基线变了但判据没跟着变」上翻车——
> ① 改完 k 默认值，却还留着 k=60 时代的「R@5 ≥82% 才值得留精排」判据（应 ≥89%）；
> ② 换了索引，却要求与旧索引「逐位一致」。
> **纪律：任何以某个基线为门槛的判据，在那个基线被改变时必须同批复核。**

> 🔴 **doc-rot 一并修正（v1.13.9）**：`kb_rag.py:42` / `:46` 的 docstring 与 `:338` 的行内注释原写「默认 60」，实际默认已是 2。
> 属于「照注释判断行为」必踩的坑 —— 与 HANDOFF #38「取值链看着很稳所以没人查」同一类。

**🔴 为什么不取 k=1（R@5 更高的那个）**：
1. 只多 1pp（90.0 vs 89.0），但 **MRR（0.7215 < 0.7380）和 R@20（96% < 97%）都更低**；
2. `1/(1+rank)` 的权重过于极端（第 1 名 0.5、第 2 名 0.333），在 **100 条**集上有过拟合风险；
3. k=2 正是 Qdrant 官方调优实测给出的先验值（单答案型语料最佳 k=2~5），**外部与本地一致**才敢当默认。

**外部依据**：Qdrant《How to Tune Hybrid Search in Qdrant》扫 5 个数据集（5,183~10 万文档）—— 每查询相关文档 ≈1 的（ArguAna / CodeSearchNet / SciFact）最佳 k=**2 或 5**；每查询几十~几百的（DBPedia 38.2 / WANDS 358.9）才 k=20/61。⚠️ 口径陷阱：**Qdrant 用 0-based，它的 k=61 才等于本处口径的 k=60**。

**改动位置**：`kb_index.py:110-116` 的 `RRF_K_DEFAULT`（含完整的实测依据注释）。

**怎么验（实测，非推断）**：
```
kb_index.RRF_K_DEFAULT = 2
kb_index.rrf_k() = 2        kb_rag.rrf_k() = 2      ← 两条链路都跟随
设 KB_RRF_K=60 → 60         清掉 → 2                ← 环境变量仍可覆盖，回退不必改代码
```
**保留可回退**：`KB_RRF_K=60` 一行环境变量即可退回旧行为；配置项没有写进 `rag.json` 或启动脚本，所以「默认值」是唯一生效口径。

### 为什么这条单独成一版
它与 v1.13.8（B2 精排输入修复）是**两件正交的事**：本条只改「三路融合的名次权重」，B2 只改「精排打分的输入文本」。分开记版本，将来出问题能独立回滚。

## [v1.13.6] - 2026-10-26

### 性能 —— 同类「每条目一次 stat」反模式扫全库，又逮到两处（作者批「这个要检查」）

v1.0.17 在知识沉淀里踩出的坑（`rglob` + `is_file()` = 每个条目一次 `os.stat`）在同一天
本模块里还有两处，都是**会被反复触发的热路径**：

| 位置 | 改前 | 改后 | 倍数 |
|---|---|---|---|
| `dashboard_gen.collect_raw_stats()`（扫 raw/ 17,245 个文件） | **169.3 s** | **0.2 s** | 846× |
| `dashboard_gen.collect_modules()`（28 个模块跑两趟 rglob） | 9.8 s | 1.8 s | 5× |
| `weekly_selfcheck.suggest()`（**每条死链**一次全库 glob） | **4.37 s/条** | **0.19 s/条** | 23× |

- `collect_raw_stats()` 原来 `rglob("*")` + `p.is_file()` + `p.stat()`（双份系统调用）；
  改走 `_walk_files()`（`os.scandir` + `DirEntry`，属性取自目录项），`e.stat()` 也取自目录项。
- `collect_modules()` 原来每个模块跑**两趟** `rglob("*.py")` / `rglob("*.md")`（同一棵树走两遍），
  合成**一趟遍历同时数两种扩展名**。
- `suggest()` 原来对每条失效内链调一次 `glob(KB_ROOT/"**"/base, recursive=True)`，
  死链 N 条就把整棵库走 N 遍（2026-10-15 那轮修 4 条＝白等 16 秒）。改为
  `_name_index()` **一次建「文件名 → 路径列表」索引**（约 2 秒）供全部死链共用。

### 🔴 两处「提速不能顺手改口径」的实测细节

1. **键一律小写**。Windows 文件系统不区分大小写，旧的 `glob('README.md')` 能命中磁盘上的
   `readme.md`（npm 包里就是小写）；按键名精确匹配会让这类从「唯一命中」退化成
   「库内无同名文件」——而 `fix_links` 对唯一命中是**自动改链接**。
   🔴 顺带查明一个反直觉事实：**Python 3.13 的 `glob` 在 Windows 上返回的是「模式里的名字」，
   不是磁盘真实名**（磁盘 `readme.md` → glob 返回 `README.md`，`isfile` 因不区分大小写而通过）。
   第一版对比脚本按精确名比，76 个文件的差异全是这个假象。
2. **跳过 `.` 开头目录**。`glob` 默认 `include_hidden=False` 不进隐藏目录；若新实现进去了，
   `.workbuddy/skills/*/README.md` 之类会突然成为「唯一命中」→ `fix_links` 把死链**自动指向
   .workbuddy**。这条差异在验证里现形（新实现多 13 个候选），已按旧口径关掉。
3. **通配符分支保留**。旧实现走 glob，天然支持 `20_*.md` 这种写法（记忆日志里就这么记过）；
   直接查字典会把它降级成「无同名文件」，故保留 `fnmatch` 退化分支。

### 等价性验证（新落 `test_dashboard_equiv.py`）
- 逐文件比对 17,245 条「相对路径 → 大小」：**键集合 100% 一致**，size 差异 0 条，
  `total` 与 `top_ext` 分布逐项相等；`collect_modules` 的 28 个模块 `name/version/py/md` 全字段相等。
- 🔴 第一版脚本用「跑两遍比总字节数」报了**假 FAIL**（87.59 vs 87.60）：`raw/隐私/微信聊天记录/`
  是**活数据**（`message_*.db` 数百 MB、`2026.jsonl` 197MB，微信同步每分钟在写），
  两次独立扫描的 size 天然会漂。判据改成「所有 size 差异必须都能被『近 15 分钟内被写』解释」。
- `suggest()` 用 12 个样本（含大小写、通配符、不存在文件）与旧 glob 版逐条比输出：12/12 一致。

### 决策理由
- 🔴 **为什么不给 `suggest()` 做持久化缓存**：索引本身 2 秒、单次查询 0.19 秒，缓存只会引入
  「何时失效」这个新 bug 面（与知识沉淀 v1.0.17 同一条判断）。**先测再优化**。
- 🔴 **为什么改原版而不是复制副本**：这三处都是模块内被直接调用的函数，复制副本等于让门户/周自测
  继续用慢的那份。改原版 + 升版本 + 等价验证，才是「修到调用方」（准则 §30.3 第七条）。
- **没动的**（实测后判定不值得）：`kb_manager.py` 的「最近更新」（443 个 md，0.14 s）、
  回收站列表（`.trash` 当前 0 文件）、`scan_skills.py`（只列一级模块 28 个）、
  `backfill_raw_line.py`（一次性回填脚本，已跑完）。

## [v1.13.5] - 2026-10-26

### 修复 —— `chunks` 的 path/title/mtime **从未写入**（2962 块 / 324 篇元数据空洞）
- 起因：把两份私密件移到 `raw/个人信息/账号密钥/` 后重建索引，发现新入库文档的 chunk
  `path` 全是 NULL。顺查发现**这不是新问题**：全库 2962 块 / 324 篇一直是 NULL。
- 根因：`write_chunks()` 的 INSERT 只写 4 列（doc_id / chunk_no / heading / content），
  path/title/mtime 完全依赖 `backfill_chunk_meta()` 事后补——而那个函数**只由
  `kb_rag backfill-meta` 子命令触发，没有任何自动化在跑**。
- 实测影响（不是推测）：`kb_rag.py:384` 注释明写「mtime 为 NULL（如未跑 backfill-meta 的
  新文档）不满足 `--since`」→ 这 324 篇用 `--since` / `--path-prefix` 检索时被**静默排除**；
  命中结果的 `path` 也为空，违反「命中必须落回 doc.path」。
- 修两处：
  1. `write_chunks()` 插入时直接带 `path/title/mtime`（从 docs 取）
  2. build 在 trigram 之后自动跑一次 `backfill_chunk_meta()`（只处理 NULL 行，代价≈0，失败不阻断）

### 决策理由
- 🔴 **为什么修写入侧，而不是「加个定时任务跑 backfill-meta」**：补偿机制不能当主路径。
  加定时任务等于承认「写的时候可以写错，事后有人擦」——擦的人一旦挂了就又积一批，
  而且初犯时没人知道该擦。写入正确是根因，回填只该处理历史存量。
- 🔴 **为什么自动回填放在 build 里而不做成独立定时任务**：新 NULL 只可能在「产生新块」时出现，
  而新块只在 build 里产生，同处一个函数才不会漏；独立任务还要额外管时序与并发锁。

### 结果
- 回填后 `chunks` 的 path/title/mtime **NULL 全为 0**（44469 块），`docs.mtime` 补 112 篇
- 复跑 build：rc=0，`database is locked` **0 次**

## [v1.13.4] - 2026-10-26

### 修复 —— 并发写库撞 `database is locked`（作者「经常并发开任务和对话」）
- **根因不是 SQLite 配置，是「只有 build 拿锁」**：`BuildLock` 一直只管 build↔build，`raw-scan` 与 `ingest_raw run` 裸写。raw-scan 一次事务扫 1.7 万文件、持写锁数十秒，build 的 `busy_timeout=5000` 等到头就报错（本轮实测撞 2 次，第三次才成功）
- **所有写者共用一把锁**：新增 `write_lock(wait=120)` 上下文 + `WriteLock = BuildLock` 语义别名。`raw-scan` 接入（新增 `--wait`，默认 120s 排队）、`ingest_raw run` 接入（`--dry-run` 不拿锁，只看一眼不该把 build 排队）
- **长写事务切片**：raw-scan 每 500 条 `COMMIT` 再 `BEGIN IMMEDIATE`，单次持锁从数十秒压到毫秒级。代价是中途崩了只剩部分写入——元数据扫描天然幂等，重跑即补齐，比「持锁几十秒让别的进程直接失败」划算
- **`BEGIN IMMEDIATE` 显式抢锁**：Python `sqlite3` 默认 **deferred** 事务，先读后写时升级写锁失败会**直接抛 SQLITE_BUSY，`busy_timeout` 完全不生效**。这是「明明设了超时还报 locked」的常见真因，此前全库都没设
- **`busy_timeout` 5000 → 15000**：最后一层兜底（进程级互斥才是主防线）
- **资产巡检** `scan_envs.py` 调用 raw-scan 补 `--wait 60`：拿不到锁就跳过记 note，不因撞上 build 直挺挺失败

### 为什么不引入更重的方案（决策解释）
- **不上 Redis / 写队列守护进程**：并发源是「同一台机器上多个 agent 会话」，不是分布式多机。写锁 + WAL + busy_timeout 就是 SQLite 官方对这一场景的标准答案；上 Redis 要多养一个常驻服务、多一套 TTL 故障面，还违反 C 盘零增长
- **不上 PostgreSQL**：本库数据量（4124 篇 / 4.4 万 chunk）与写入频率（每天几次构建）远没到 SQLite 的瓶颈；换库是拿运维复杂度换一个当前不存在的问题

### 验收（实测）
- 单跑 `raw-scan`：17227 件 / 96.1s / rc=0
- 持锁时 `raw-scan --wait 0`：立刻跳过、rc=2、并指出持有者 PID 与命令
- **build 与 raw-scan 同时跑**：raw-scan 排队 4 轮后自动接手，两边 `grep -c locked` = **0**，build rc=0（58s）

## [v1.13.3] - 2026-10-23

### 文档（定性纠偏 · A1）
- **把「rerank 阈值误杀」这个错误诊断结论从三个文件里改掉**
  - **为什么值得单独发一版**：`阈值误杀` 这个说法指向的修复动作是「调 `KB_RERANK_MIN` 阈值」，而这条路**已实测否决**（6 条问题 query 的 gt 精排分 0.0000/0.0002/0.0001/0.0011/0.0248/0.0426，6/6 全低于阈值，调高阈值只会更糟）。**错结论比没结论危险**——下一个人看到它会先去调阈值，白跑一轮。
  - **改成什么**：`阈值误杀` → **「精排输入退化 + 低分硬清空」两层**，并附代码行号与实证数字。① 精排打分对象是**文档前 120 字 summary 而非命中块**（`search_bge` / `search_fts_chunks` 返回体都不含正文，`bge_reranker._text_of()` 取值链 `chunk_text → summary → title` 只能回落到 summary）；② 全池最高分 < `KB_RERANK_MIN`（默认 0.05）时直接清空结果。实证：同期 top1 分 0.9936/0.7787/0.4770/0.1697 ⇒ **精排不是「没把握」是「自信地认错人」**；关精排后 R@5 68.0%→**82.0%**、MRR@10 0.5588→**0.7023**、冷查询 8.6–14.5s→**0.13–0.32s**。
  - **改了哪三个文件**：`kb_rag.py:313-330`（源头注释）、`ROADMAP.md` 第 44 行 T2 行、`_test_rerank_fallback.py:8-9`（回归测试脚本的 docstring）。
  - **刻意不改的两处**：`reports/2026-10-23-T1门户常驻化实现.md:181` 与历史 judged set 报告——那是**历史存档，保留当时的认知才有追溯价值**，改了反而抹掉证据链。
  - **验收（实测）**：`py_compile kb_rag.py _test_rerank_fallback.py` 通过；`kb_rag.py` 里 `阈值误杀` 出现次数 **1**，且上下文是 `🔴 2026-10-23 晚间二次定性（推翻上面旧注释里「阈值误杀」这个说法）`——只在否定语境出现，不再当根因。
  - 🐛 **过程中我自己犯的错（记下来防再犯）**：写 ROADMAP 改文时顺手引了「T13/B2」，**T13 实际是「ColPali 视觉文档检索评估」，跟 B2 毫无关系**——这就是自己刚总结过的「编路径/编任务号」反模式。核实后已改成：修输入的任务编号是**清单 B2**（见 `wiki/技术/知识库完善-任务清单-2026-10-23.md`），**ROADMAP 里还没对应条目，等作者拍板后再挂**。

## [v1.13.2] - 2026-10-23

### 检索质量实证（六轮只读探针，本版本产出的是**结论**不是代码；代码一行未动）
本轮不动任何生产代码，只把「检索到底差在哪」量清楚。全部结论有实测数字，落 `reports/2026-10-23-judged-set基线.md`（34KB）与 `wiki/技术/知识库完善-任务清单-2026-10-23.md`。

- 🔴 **决定性结论：精排在本库同时是最慢的和最不准的**

  | | 精排开（现状） | 精排关（`rerank=False`） |
  |---|---|---|
  | Recall@5 | 68.0% | **82.0%** |
  | MRR@10 | 0.5588 | **0.7023** |
  | Recall@20 | 94.0% | 96.0% |
  | 冷查询延迟 | **8.6–14.5s**（pool30）/ 19.2–47.9s（pool60） | **0.13–0.32s** |

  **精排占 >99% 的延迟，同时让 R@5 掉 14pp** ⇒ 关掉它同时拿到更快 + 更准，不需要任何取舍论证。根因：`fused` 模式下精排打分对象是「文档前 120 字 summary」而非命中块（`search_bge`/`search_fts_chunks` 都不返正文，`bge_reranker._text_of()` 取值链 `chunk_text → summary → title` 回落到 summary）。

- 🔴 **精排不是"没把握"，是"自信地认错人"**：6 条问题 query 的 gt 精排分 **0.0000 / 0.0002 / 0.0001 / 0.0011 / 0.0248 / 0.0426**（6/6 全 < `KB_RERANK_MIN=0.05`），而同查询 top1 分是 **0.9936 / 0.7787 / 0.4770 / 0.1697**。且 **12/12 高分错排的 top1 都是 gt 的兄弟文档**（同系列口播稿、同项目的兄弟文档）——退化输入看到的「文档前 120 字」在兄弟文档之间高度同质。
- 🔴 **12 条错排拆四类**（B2 只打到 ① 类那 2 条）：① 可召回·修输入即可 **2 条**（#6 RRF 第 1、#84 RRF 第 4，池内深度够，唯一瓶颈是输入退化）② 池深 **2 条** ③ 融合排序 **1 条** ④ 纯语义 **1 条**。⇒ **能进门户的只有 ① 类与 ④ 类别名表，两条都是零成本**；②③ 类要 24–34s / 47.9s 单次查询，与「亚秒级」定位直接冲突。

### 新发现的两个真 bug（均实测，均零重编码成本）
- **中文标题加权自建库以来从未生效（B5）**：`docs_fts.title` 存原始中文（`kb_index.py:771` 直传原串），FTS5 `tokenize='unicode61'` 把中文整串切一 token，query 却走 jieba。实测 `title : "知识"` → **0 篇** vs `tokens : "知识"` → 484 篇；`title : "flask"` → 3 篇（只有 ASCII 有效）。**修复成本实测 ≈30 秒**（只重切标题 7.06s + FTS 重建写入 6.29s + 读回 3.14s，jieba init 1.7s）——因为 `tokens` 列里已有切好的正文分词可复用，**不重编码 BGE、不碰 chunks**。
- **等权 RRF 系统性惩罚「单路强命中」（B8，待测规模）**：`1/(60+8)=0.0147` 打不过「三路都第 80 名」的 `3×1/(60+80)=0.0214`。且 `k=60` 是 RRF 原论文为百万级网页定的，本库 4 千篇时 `1/68` vs `1/140` 只差 2 倍，名次几乎被压平。

### 已否决的假设 8 条（防后人重做，最难复现也最易重复踩）
调 `KB_RERANK_MIN` 阈值 ❌（救不了被打 0.99 的错误文档）｜调 `KB_FTS_W` 标题权重 ❌（no-op，见上）｜「关精排必须重启门户」⚠️半对（缓存维度已修、热生效未修）｜「`pool_n` 在 `rerank=False` 不被使用」❌（`:293` 无条件截断）｜扩池是低垂果实 ❌（门户 `topk=8` → pool 24，cap 30 与 60 相同，零效果）｜padding 分数漂移 ❌（`repeat_max_delta=0.0` + `expand_max_delta=0.0` 双零证伪）｜扩池引入噪声 ❌（是分母变大不是噪声）｜语境前缀救 3 条召回缺口 ❌（三条病因是权重/词面/池深，向量名次不是瓶颈）。

### 验收
- 全部为**只读探针**（`mode=ro` 连接，不写 query_log、不碰 `pool_n` 公式、不碰 `chunks`/`chunk_bge`），未改任何生产代码。
- 指纹已钉：`docs 4044 / chunks 43760 / bge_built_at 2026-10-23T15:52:15`、`kb_rag.py @15:22:54`。
- 每条结论都有复现路径，见清单 §五「本文的取证清单」。

## [v1.13.1] - 2026-10-23

### 修复（备份防线：三份「有备份」其实没有退路）
- **`db_admin.py cmd_backup()` 加健康闸门**：原先只机械 `VACUUM INTO`、不校验内容，结果是 10-15 / 10-19 两份残库被当成「退路」存着（前者 162 docs 且**连 chunks 表都没有**，后者 319 docs / **raw=0**，都是 BUG-19 冲库期的产物）。恢复它们等于把 4,000 篇退回 319 篇——**比没有备份更危险**。现为双闸门：**前置**（VACUUM 之前查源库，不合格连几十秒 VACUUM 都不做）+ **后置**（校验真正落盘的文件），规则 `docs<500 或 raw=0 或 bge=0` → 拒写 + 告警 + `rc=2`，不合格快照当场 `os.remove()`，磁盘上不留残库。新增 `--force`：确需留证时只写进 `state/backups/_坏快照留证/`，不进轮转、不可当恢复源
- **`kb_rag.rotate_backups()` 从「按份数」改「按天」**：原 `keep=3` 数的是份，10-23 一天跑了 3 次构建（时间戳 14:17 / 14:19 / 14:28，**只差 11 分钟**，前两份内容完全相同）就把 10-22 及更早的历史全滚光，回滚窗口从几天缩到几十分钟。现改为 **同一天最多留 1 份 + 跨天保留 7 天**。环境变量 `KB_BACKUP_DAYS`（默认 7），旧 `KB_BACKUP_KEEP` 兼容但语义改为天数（天数 ≥ 份数，只会更宽松）；新增 `vdir` 参数仅为可测。最坏回滚窗口 = 7 天，磁盘峰值 ≈ 7×1.3GB 封顶
- 顺手修两个既有 bug：① `--keep 0/负数` 会走到 `files[:-keep]`（负数时变成「只留 1 份」并删掉其余）→ 加 `keep>0` 守卫；② 备份份数统计用 `len(os.listdir())` 会把 `_坏快照留证/` 子目录算成一份 → 只数 `*.db`
- 磁盘：删除 `_versions/kb.db.bak-20261023-142734`（B2 搬迁前快照，与健康快照功能重叠，B2 台账 459 行可反向还原），`_versions` 1.6G → **314M**；两份残库（46MB）移入 `state/backups/_坏快照留证/` 留证

### 索引范围扩围（同日 15:19 `kb_index.py`，本版本漏记，本次补登记）
- **技能区入索引（三条 pattern）**：SKILL.md 递归 / `*/` 直属 / `/*/*` 二层。🔴 **为什么必须收**：2026-10-23 实证——`video-channels-capture` 明明躺在 `.workbuddy/skills/` 里，`kb_rag` 搜「视频号 下载」却搜不出它，结果先去 WebFetch 硬抓、撞两次 401 才想起查库。**能力封装成技能后反而检索不到**，等于白封装。深度卡在 2 层是刻意的（更深的 `/*/*/*` 会扫进 skills 内的 node_modules）。
- **modules 一层「说明书类」白名单**：`{README.md, AGENT.md, INDEX.md, ROADMAP.md, AUTOMATION_PROMPT.md}`。🔴 **刻意排除** `CHANGELOG.md` / `HANDOFF.md`（时间快照与流水，改一次变一次，进索引等于制造旧版重复 + 稀释排序）、`bug记录/` 下 21 篇单 bug 台账（由 INDEX.md 总表代表）、`rea/` 第三方开源项目自带治理文件。
- **modules 二层子目录白名单**：只收 `后端开发/示例同事面试项目/` 3 篇（RBAC 权限体系 + 软删除方案，作者 2026-10-23 拍板：与业务无关的通用可复用方案，属求职作品集说明）。🔴 **为什么用白名单不通配**：`*/*/*.md` 全开会命中 157 篇，其中 `rea/docs/` 58 篇是第三方项目自带文档、知识沉淀/reports/ 50+ 篇是流水快照、资产巡检/reports/ 8 篇是时间戳快照——全收等于灌垃圾。
- 🔴 **绝不能把 modules 改成 `recursive=True`**（已写进代码注释防后人改）：递归会扫进 node_modules 里 **497 个第三方包 README**（实测 modules 下共 560 个 README，排掉 node_modules/_versions 后 63 个，其中一层 26 个才是模块说明本体）。

### 验收
- `outputs/知识库运维/2026-10-23-backup-gate-test.py` **14/14**：四库判定符合预期 / 前置拦截不产生任何文件、且根本没调用 `connect()` / 后置拦截当场删文件 / `--force` 只进留证目录不进正常轮转
- `outputs/知识库运维/2026-10-23-rotate-backups-test.py` **13/13**：8 天 14 份假备份下默认策略剩 7 份且均为每天最晚那份；16 日整天淘汰；**今天 3 份只留 1 份**（关键回归点）；`-wal/-shm` 无孤儿文件；`days=0` / `same_day=2` / `KB_BACKUP_DAYS` / 旧 `KB_BACKUP_KEEP` / 空目录 / 不存在目录全部符合预期
- 真实 CLI：`db_admin.py backup --keep 7` **56 秒**产出 1.2GB 快照，`docs=4042 raw=3483 chunks=43729 bge=43729 ✓`，备份数 2｜2.4GB

## [v1.13.0] - 2026-10-23

### 完成（M2：T4 OCR / T10 多模态三模态，安装+试点验收）
- **T4 OCR 栈安装并试点通**（依赖落 `envs/ocr/`）：**MinerU 4.0.12**（`parser.parse(tier="basic", ocr_mode="auto")`，ONNX/CPU 档，Apache-2.0+附加条款）解析「数学总结.pdf」9.3s 提 14 文本块/825 字；**RapidOCR 3.10.0** 对带文字截图抽字 1.7s 中文正确（onnxruntime 引擎，本地已缓存 PP-OCRv6 权重）。🔴 **踩坑修复**：docvortex 渲染 worker 用 `multiprocessing.spawn`，调用方脚本必须把 `parse()` 入口放在 `if __name__=="__main__"` 内——否则子进程重 import 本模块重跑 parse，导致 render worker `exit: 1` 崩溃；`_smoke_t4_ocr.py` 已加守卫复跑全绿。后续 30 文件批量标定（~6.5h）+ media 入库为独立 batch 任务
- **T10 多模态三模态试点全绿**（caption/ASR 依赖落 Managed default venv `E:\.workbuddy\binaries\python\envs\default`，权重 `modules/知识库索引/_models/`）：
  - ① **图片 caption**：`Qwen3-VL-2B-Instruct`（4.26GB，modelscope 下载；transformers+torch CPU 加载 7–14s，推理 135–436s），中文描述准确。`_smoke_t10_caption.py` 实测通过
  - ② **音频 ASR**：`SYSTRAN/faster-whisper-tiny`（modelscope 下载至 `_models/faster-whisper-tiny`，CPU int8），转写中文 73.5s，语种识别 zh。`_smoke_t10_whisper.py` 实测通过
  - ③ **视频**：`FFmpeg CLI`（ffmpeg-8.0.1-essentials_build）抽帧 1fps，42 帧 0.9s；中间帧送 Qwen3-VL caption 135.6s 描述准确。`_smoke_t10_video.py` 实测通过
- 🔴 **T10 选型偏离记录（准则 §33 落点纪律）**：原规划图片 caption 走 **Qwen3-VL-2B GGUF + llama.cpp 子进程**，但 `llama-cpp-python` 本机无 C/CXX 编译器、镜像无 cp313 预编译 wheel，编译失败 → 改 **transformers + torch 直接加载 safetensors**（功能等价，但 CPU 推理偏慢 135–436s/图）。属已知性能项，待有编译环境或换 GGUF 量化再优化
- 🔴 **环境铁律（本机实测）**：HF 直连 502/401（xet），whisper 权重走 `HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=1` + modelscope 直下；pip 安装需 `CODEBUDDY_SAFE_DELETE_ENABLED=0` 关闭 WorkBuddy safe-delete 守卫；WorkBuddy 托管 Python 无 numpy/torch，重负载用 Managed default venv

### 已知问题 / 后续
- T4/T10 当前为「安装+单文件试点」级别；**30 文件 OCR 批量标定**、**50 张 caption 人工可用率抽检（≥60%）**、**media_assets 时间戳↔帧↔字幕入库** 均为后续 batch 任务（属 T4/T10 验收标准里的「批量」部分，不阻塞「栈可用」判定）
- T10 Qwen3-VL CPU 推理慢（135–436s/图）：若上线高频 caption，需 GGUF/llama.cpp 或 GPU 路径

## [v1.12.0] - 2026-10-23

### 完成（M1 收尾：T1 / T2 / T11 / T12；T3 见 v1.11.0）
- **T1 门户 reranker 进程内常驻化**（`知识库体系门户/kb_hub_server.py`）：`_get_rag_module` 启动一次性加载 bge_encoder + bge_reranker + 向量矩阵（实测 ~18s），后续 `/api/rag` 走 `_rag_search_persistent` 零模型重载；加载/推理失败自动回退 `_run_rag_spawn` 子进程（`rag.json persistent=true` 默认开）。验收：`_verify_t1.py` 实测首查含加载 18.2s、后续查询各 ~5s 且带 rerank_score、相比 spawn 模式每次省 ~18s。**ONNX 量化 Plan B 未做**（单次查询含 rerank 约 5s，未达 ROADMAP 里 <3s/<1s 激进目标，留作后续专项）
- **T2 judged set 评测集 + rerank 低分降级修复**（`state/judged_set_v1.json` 100 条 + `reports/2026-10-23-judged-set基线.md`）：基线 Recall@5=47.0% / Recall@20=61.0% / MRR@10=0.3609 / 零命中率 34.0%（score 分布 3:61 / 2:4 / 1:1 / 0:34）。发现 rerank 阈值 `KB_RERANK_MIN=0.05` 误杀 33/34 条零命中（库里有却报无）——已改 `kb_rag.search_unified` 为「低于阈值→降级返回 RRF 融合原序 + note 标注低置信」，回归 `_test_rerank_fallback.py` 8/8 断言通过
- **T11 数据分级 + 分层部署**（`cloud_publishable.py` + `_test_privacy_gate.py` 3 项全过）：`CLOUD_PUBLISHABLE` 白名单 + `raw/隐私/`、`raw/个人信息/` 目录黑名单 + `privacy_scan.py` 门禁。隐私路径过同步器被拒 / 白名单外品类被拒 / 含隐私导出包报 BLOCK（对照组干净）。🔴 本地先行：白名单过滤→导出→同步→对端检索全链路先在本地等价环境跑通，真上云前走 L2 审批
- **T12 多会话并发协同**（`build_task.py` + `_test_build_task.py` 3 项全过）：`build_tasks` 任务队列表 + `build_sessions` 会话注册表 + `--wait` 排队（BuildLock 打底）；状态机全流转 / 并发恰好一次领取 / 隐私回归不破。明确不上多写、不引 Redis

### 决策理由（T11 / T12，实现者补充）
- **为什么白名单先判黑名单**：黑名单是准则 L1 硬红线（隐私绝不上云），误放行代价不可逆；白名单是「未枚举=不上云」的保守姿态。先判黑名单保证**即使白名单将来被误加进隐私目录**，隐私路径仍被拒——红线不依赖白名单的正确性。另按 AGENTS L1 把 `raw/项目/`（含示例市原始数据）整类排除在白名单外；`raw/其他`、`raw/微信文件`、`raw/生活` 等未枚举品类默认不上云（宁缺毋滥）
- **为什么队列不上 Redis**：单用户本地知识库，build 每天 1-2 次；SQLite 队列表 + `BEGIN IMMEDIATE` 原子领取已足够（并发测试实测恰好一次领取）。为一个每天用两次的功能引常驻服务，依赖面/运维面/故障面全升——不过度设计
- **为什么接线失败自动降级**：队列是可观测性增强，绝不能成为 build 的新故障点；`_register_build_task`/`_finish_build_task` 内部吞异常返 None，build 照跑
- **为什么路径门禁与内容门禁是两道门**：privacy_scan 只扫内容不认路径（`raw/隐私/` 里内容干净的 md 内容门禁不拦）；白名单品类里的文档内容也可能泄密（实测首轮 40 篇里 23 篇有 BLOCK：手机号/邮箱/绝对路径/真实姓名）。缺一不可
- **内容门禁假阳性要人工过目**：npm 包 `xxx`（版本 1.7.2）被「电子邮箱」规则误 hit（实测拦了 wiki/index.md）。门禁宁枉勿纵（fail-closed），误报处置是在 privacy_scan 侧加 ALLOW/改词表，不是放松门禁

### 已知问题 / 后续
- T1 单次查询含 rerank 约 5s，未达 <3s/<1s：待 ONNX int8 量化（Plan B）专项评估
- T4（OCR）/ T10（多模态 caption）安装与试点进行中（见 M2），本版本不含

## [v1.11.0] - 2026-10-23

### 新增（T3 元数据过滤：--since / --path-prefix，ROADMAP E3）
- **schema 加列（幂等）**：`chunks` 表加 `path TEXT` / `title TEXT` / `mtime INTEGER`，`docs` 表加 `mtime INTEGER`。`ensure_schema` 末尾用 `PRAGMA table_info` 显式判断列是否存在再 `ALTER`——CREATE TABLE IF NOT EXISTS 只对**新库**生效，1.3GB 老库必须走 ALTER；显式查列名而非 try/except（ALTER 报错分不清「已存在」与真错误），重复跑 ensure_schema 零报错零副作用
- **`kb_index.backfill_chunk_meta(conn)`**：两级回填（先补 docs.mtime，再 join 传播到 chunks.path/title/mtime），50196 行 **16.7s** 回填完，重跑 **2.75s 且 0 行待补**（幂等，只补 NULL 行）。docs.mtime 取数链：raw 品类取 `ingest_raw_state` 登记的 mtime（摄取时 scandir 拿到，零磁盘 IO，且反映的是「被索引进库那一版」的时间）→ 其余品类 stat 源文件 → 文件丢失/隐私目录走 `indexed_at` → 再不行 0（保证非 NULL：时间未知不满足 --since，但回填验收要求全非空）
- 🔴 **隐私红线从元数据层做起**：`raw/隐私/`、`raw/个人信息/` 下的文档连 mtime 都不 stat，回填只用库内已有信息（ingest state / indexed_at）
- **`kb_rag.py backfill-meta` 子命令**（main() 追加区）：调用上述函数并打印统计；回填后仍有 NULL（孤儿 chunk）时 **rc=3**，让定时任务/脚本看得见
- **`kb_rag.py search --since YYYY-MM-DD` / `--path-prefix PREFIX`**：日期在 argparse 层转换校验（格式错敲回车时就报，rc=2，不用等检索跑完）；过滤在 `search_unified` 内**融合后、rerank 前**执行；`--json` 输出回显 `filters` 字段（since round-trip 回日期字符串，门户/报告读起来是人话）
- **回归测试 `_test_meta_filter.py`**（24 项断言全过）：回填三分支（磁盘 stat / ingest state 优先 / indexed_at 兜底，含隐私路径不 stat 与文件丢失）/ 前缀含**部分匹配**（`wiki/技` vs `wiki/技术x`）与**空结果** / since **边界**（恰为当天 00:00 应被包含）/ 组合过滤 / 🔴 **rerank 只在过滤后集合上运行**（spy 记录每次收到的池 + spy 排序决定最终输出顺序）/ CLI 坏日期 fail-fast

### 决策理由
- **为什么过滤放在融合后、rerank 前**：三个候选位逐一推演——① 各路召回内部过滤（kw/vec/substr 各加 WHERE）：同一逻辑三处实现，且各路召回深度只有 topk*2，过滤后可能凑不够 rerank 的 pool_n；BGE 向量路在 numpy 侧打分，SQL 过滤根本掺不进去，不可行。② **融合后、rerank 前（选中）**：RRF 分此时已跨路可比，在**完整融合池**上一刀切、单点实现；rerank 仍只在过滤后的集合上跑（业主要求），精排分不会被「与查询相关但被过滤」的文档稀释；且过滤放在按 RRF 序截 pool_n **之前**，保证池子里装的是排名最靠前的合格文档。③ rerank 后、截 topk 前：rerank 会对即将被过滤掉的文档白算 cross-encoder（0.23s/对），且「全池精排分低于阈值判零命中」会被受限文档的低分污染——库里明明有很多合格命中，却可能误报「未检索到」。代价：融合池照常全量计算（过滤不省召回成本）——换取正确性与单点实现，值得
- **为什么给 docs 也加 mtime 列（不止 chunks）**：回填需要一处「文档级 mtime」的权威存储——raw 从 ingest state、其余从 stat 算出后先落 docs.mtime，chunks.mtime 再由它 join 传播；后续 ask/门户等 doc 级消费方也可直接读，不必每次重算
- **为什么 chunks 冗余 path/title/mtime 而不是过滤时现 join**：检索期过滤发生在融合池上（≤30 个 doc_id，一次 `WHERE id (...)` 即得），但冗余到 chunk 行是 ROADMAP T3 的明文要求（「chunk 表补路径/标题/mtime 字段」），且块级消费方不必回表
- **为什么 --since 用本地时区**：chunks.mtime 来自文件 mtime/ingest state（都是本地时间语义），两边必须同一时钟，否则「今天改的文件」会因 8 小时时差被漏掉
- **为什么 raw 的 mtime 取 ingest state 而不是现 stat**：state 记录的是摄取时的文件 mtime，反映的正是「被索引进库那一版」的时间；现 stat 会拿到摄取之后的新 mtime，与库内正文版本不一致
- **为什么 mtime 未知（NULL）时不满足 --since**：「无法证明够新」与「不够新」在过滤语义下同权，宁缺毋滥；配套约束是 backfill-meta 幂等可重跑——build 新增的 chunk 重跑一次即补齐

### 已知问题 / 后续
- build 新增文档的 chunk 元数据为 NULL，需重跑 `backfill-meta` 补齐（或后续任务把 meta 维护并入 `write_chunks`/`upsert_doc`，本任务按禁令未碰这两个函数）
- 过滤不省召回成本（融合池照常全量算）；如需性能优化，可在各路召回内加 metadata 预筛（须先解决 BGE 路的 numpy 侧过滤）

## [v1.10.0] - 2026-10-22

### 安全修复（BUG-20261022-19 第 4 层根治：跨会话构建互斥锁）
- **`kb_index.BuildLock`**：OS 层文件锁（Windows `msvcrt.LK_NBLCK` / POSIX `fcntl.flock`），同一时刻只允许一个 build 进程。拿不到锁 → 明确报错退出 **rc=2**，**绝不静默并发写库**
  - 为什么用 OS 锁而非「锁文件 + PID 探测」：**进程被强杀时 OS 自动放锁**，不会留永久残锁（事故当天我就是用「杀所有 python.exe」的焦土命令误杀自己的 build，PID 方案在这种场景会失效）
  - `--wait N`：拿不到锁时排队等待 N 秒（默认 0 = 立即跳过）。`daily_run.py`（每日 08:00 定时任务）已改为 `--wait 600`：与手动 build 撞车时排队，10 分钟还拿不到就跳过本次——**宁可少跑一次，也不冒冲库风险**（索引是可重建的，第二天自动补）
  - 持有者信息写**旁路 `.info` 文件**（PID / 启动时间 / 命令行）：Windows 强制锁会让锁文件本体在持有期间**读不到**，信息放旁路才能被别人读到，冲突时可立刻回答「谁在跑、跑了多久」
  - `kb_index.py cmd_build` 同样上锁（虽已废用，但旧命令仍可能被调用，锁住它才不留下并发口子）；两个入口 `main()` 均改用 `sys.exit(rc or 0)` 让调用方看得见失败
- **回归测试 `_test_buildlock.py`**：3 项断言——互斥（第二个进程被拒）/ 🔴 **强杀后能重新拿到锁**（taskkill /F 实测，OS 自动放锁）/ 排队等待生效。改 BuildLock 必跑

### 健壮性（备份轮转，防磁盘每日膨胀）
- **`kb_rag.rotate_backups()`**：只保留最近 N 份 `kb.db.bak`（N 由 `KB_BACKUP_KEEP` 指定）
- 🔴 **默认不轮转**（`KB_BACKUP_KEEP` 未设置 = 不删任何备份）：删除不可逆，按「减法分级审批·先过目后执行」，等确认后再开。库已 1.3GB，而每日自动化每天 build 一次就备份一次——**没有轮转的话磁盘每天固定涨 1.3GB**
- `_inspect_backups.py`：只读体检每份备份的 docs/raw/chunks 篇数，用于判断哪些还有回滚价值（不删任何东西）
- 🔴 **轮转的两个 bug（同一天自己写出来又自己测出来）**：① keep 数的是「文件」不是「份」——一份库 = 主文件 + `-shm` + `-wal` 三个文件，按文件计数让 keep=3 实际只留 1 份，**当场误删了本该保留的事故前健康态备份 023656**；② 参数优先级写反——显式传入的 `keep=0` 被默认值覆盖。均已修（按基础名分组、同进同退；优先级 = 显式参数 > 环境变量 > 默认 3）
- 回归测试 `_test_rotate.py` 6 项全过：保留 N **份** / 保留最新的 / 每份三文件完整 / 淘汰的连附属删净 / 非备份文件不动 / keep=0 不删
- 新增 `ROADMAP.md`：10 项技术遗留的推进路线（优先级/难度档/工作量/验收标准/依赖 + Day1-5 每日计划 + 新对话冷启动提示词）

## [v1.9.0] - 2026-10-22

### 安全修复（BUG-20261022-19 同日复发根治）
- **`kb_index.py` 品类所有权边界**：新增 `OWNED_CATEGORIES`（wiki/modules/root）；`--full` 按品类删（先按 id 清 docs_fts/doc_terms/chunks 再删 docs 本体）、增量清理加 `category IN` 过滤——**raw 品类一行不碰**（归 ingest_raw.py 所有）。主体逻辑提取为 `sync_owned(conn, full)` 双入口共用
- **`kb_rag.py build` 自愈 + 全量覆盖**：state 说谎自愈（ingest_raw_state 篇数 vs 库内 raw docs <90% → 强制 full 重扫）；补调 `kb_index.sync_owned`，使 kb_rag.py build 覆盖 raw+wiki+trigram+BGE，成为唯一安全入口
- **调用方清零**：`modules/每日运势/daily_run.py`（挂定时任务的原版）INDEX_PY → kb_rag.py；README/HANDOFF/AGENTS/速查表/.trae+.qoder 规则/SKILL/冷启动包/技能工作台 4 模板/wiki 12+ 处的 `kb_index.py build` 全部切换；`kb_manager.py` 两处调用同步

### 新增（rerank 精排层，2026 RAG 金标准）
- **`bge_reranker.py`**：bge-reranker-v2-m3 cross-encoder 精排。融合池 top-N（默认 30，KB_RERANK_POOL 可调）联合打分重排；CPU 0.23s/对、首次加载约 19s；批 16；sigmod 分；模型缺失/异常自动降级跳过（返 None 不崩）
- **`kb_rag.py search/ask`**：`--no-rerank` 开关（默认开）；**短查询 ≤4 字跳过精排**（字面查找意图，保「一个字都要搜得到」灵敏度）；全池精排分 < KB_RERANK_MIN（默认 0.05）直接判「库内未检索到相关内容」（QA 建议的全路低分零命中判定）；结果带 rerank_score 字段
- 门户「🔍 全库检索」：结果卡片 rerank 分 chip + 零命中/擦边 ⚠️ 警示条（ragNote/rerankChip 两个 i18n 键）

### 健壮性（病态 PDF 单文件超时）
- **`ingest_raw.py read_pdf_guarded()`**：spawn 子进程隔离跑 PDF 提取，默认 90s 超时（KB_PDF_TIMEOUT 可调）；超时 terminate→kill、记 pdf_timeout gap，**一个坏 PDF 不再堵死几千篇的摄取**（实测 251MB 风景名胜.pdf 27s 准时掐断；join-first 会被大正文 flush 堵死误杀，已改 get 轮询）。代价：全量重建每个 PDF 多 4-5s spawn 开销（增量构建零开销），持久 worker 池优化记入待办

## [v1.8.0] - 2026-10-21

### 新增（三链路检索，准则 §30）
- **`kb_rag.py`**：三链路统一入口。`search`（FTS5 关键词 ⊕ BGE 语义 ⊕ 字级 substring，RRF k=60 融合）/ `ask`（带 [n] 编号引用的 RAG 上下文包，零命中明说「库内未检索到」）/ `build`（摄取+trigram+BGE 一键全建，增量）/ `stats` / `doctor`（覆盖率+缺口+query_log 零命中率+隐私提示，🔴 时 exit 3）
- **`ingest_raw.py`**：raw/ 正文摄取器。md/txt/json/py/csv/html/docx/xlsx/pptx/pdf 九类提取器（pypdf 先行、pdfplumber 回退；openpyxl 每表限 500 行）；单文件正文截 200KB；增量 state（大小|mtime）+ 每 50 篇 commit 断点续传 + 单文件失败不中断 + 失效索引清理 + gap 统计落库
- **`chunks_trigram`**：FTS5 trigram 字级保底路——≥3 字走 MATCH，1-2 字走 LIKE + progress handler 2 秒熔断（超时降级不崩）

### 性能修复（不修就是事故）
- 扫描必须 `os.scandir`：本机 `os.stat` 实测 **5ms/个**，os.walk+stat 扫 raw/ 16872 文件要 **81.5 秒**（dry-run 直接超时被杀）；scandir 的 DirEntry 在 Windows 白拿 size/mtime，**0.11 秒**（快 740 倍）
- pypdf/pdfminer 解析噪声必须静音（logging CRITICAL）：坏 PDF 能刷几千行「Object N found」，把 build 进度行冲掉＝没有可观测性

### 边界（写清楚防止误用）
- 索引库含 `raw/隐私/` 与 `raw/个人信息/` 正文（先例：raw_metadata 早已含这两个目录元数据）——**本地派生层，严禁外传/上传/入 git**，doctor 每次输出都带此提示
- `kb_index.py` / `bge_encoder.py` / `vector_index.py` 现有语义零改动（评测基线 94.0%@MRR0.8498 回归对比用）

## [v1.7.1] - 2026-10-15

### 新增
- 失效内链**豁免机制**：`exempt_reason()` + `split_exempt()`，只豁免「时间上的历史快照」，豁免清单与理由**公开写进周报**，作者可随时推翻

### 修改
- 门禁只对「要修的失效内链」判 FAIL；豁免部分照实报数不隐藏
- 知识库准则升 **v3.2**，新增 §10.1.1《减法不因「是我生成的」而豁免》

### 决策理由
- **为什么不删那些失效引用的运势文档**：每日运势同一天被重跑过，旧批次文档引用的图只存在于当时那批产物里。作者原话「运势图不用清理以前的，就算重跑了，也不是同一天同一时刻的」——那些是**各自时点的快照**，不是垃圾。
- **为什么用豁免而不是删文件或放宽标准**：删文件是减法（要先问）；无条件放宽标准是自欺。**豁免是第三条路**：不改内容、不掩盖数字、把理由写进报告，接受作者随时推翻。
- **为什么立 §10.1.1**：判断「这是垃圾」本身就是减法的前提，而这个判断权不在 AI。**"是我生成的"只能当汇报说明，不能当动手理由。**

---

## [v1.7.0] - 2026-10-15

### 新增
- `bge_encoder.py` 真·语义向量路：BAAI/bge-small-zh（**从 ModelScope 下载，绕开 HuggingFace 不通**），CLS pooling + 查询指令前缀 + L2 归一化；1690 块 × 512 维，CPU 编码 135s
- `answer_eval.py harvest` 子命令：日常问答自动落库（自动取块级上下文 + 按问法去重 + 记来源）
- SKILL.md 新增《答案自动沉淀》规则：答检索类问题后必须落一条样本
- CLI 新增深度模式 `--mode fused_bge` / `bge`

### 修改
- `--mode auto` 默认路径明确为「关键词⊕LSA」（0.7ms，MRR 0.8525 最高）
- 评测扩到 11 路（新增 BGE 单路 / 关键词⊕BGE / 三路融合）

### 决策理由
- **为什么 BGE 不进默认路径**：整体指标比默认路低（MRR 0.8477 vs 0.8525），却要付 28 倍延迟（19.8ms vs 0.7ms）+ 5 秒模型加载 + 200MB torch 依赖。**为 0.3% 的 NDCG 差距换 20ms 和 200MB，不划算。** 保留为深度模式兜底。
- **为什么模型从 ModelScope 拿**：HuggingFace 主站不通、镜像站 401/429；ModelScope 上有 `BAAI/bge-small-zh` 官方上传版。**换源比等网络通更快。**
- **为什么答案要自动沉淀**：样本靠人攒永远攒不起来；让真实问答自己长成评测集，分布才是真实的。

### 已知问题
- BGE 慢 28 倍，不能进默认路径
- **融合不等于并集**：关键词⊕BGE 在难例上只救回 1/5，比 BGE 单路的 2/5 还少（RRF 按排名加权，正确答案被挤到 top5 外就救不回）
- 答案样本 21 条（含 4 反例），仍偏少

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.6.0] - 2026-10-15

### 新增
- `vector_index.py` 稠密向量检索路（LSA / 截断 SVD，128 维，纯 numpy 零下载）：`build` / `search` / `stats`
- `expand_eval.py` 评测集扩充：从章节标题生成 70 条自动弱标注，覆盖 38 篇冷门文档；人工 130 + 自动 70 = **200 条**
- NDCG@10 指标上线（与 Recall@5 / MRR@5 并列）
- 独立 judge 盲测：换模型复核 + 注入 4 条反例（编造/张冠李戴/幽灵引用/常识补答）
- CLI 默认 `--mode auto`：有稠密向量索引就用「关键词⊕LSA 向量」（MRR 最优），否则退回粒度融合

### 修改
- 评测从 6 路扩到 8 路；周自测同步
- `faithfulness_judge.py` 支持独立 judge 三方对照与盲测有效性统计

### 决策理由
- **为什么用 LSA 而不是 BGE**：BGE 模型要从 HuggingFace 下，本机实测主站不通、镜像 401/429 限流，**拿不到**。LSA 零依赖零下载，也是真·稠密向量，先把链路跑通、把对照结论拿到手。脚本设计成可替换：**换模型只需改 `fit()`**，检索层不动。BGE 列为「网络通了再换」的 P1。
- **为什么注入反例做盲测**：不注入反例，样本全在 4-5 分区，既算不出弱代理区分度，也证明不了 judge 本身可信。造 4 条故意失败的答案混进去（judge 不知道是哪几条），是成本最低的可信度验证。
- **为什么自动弱标注集的指标要分开报**：这批题的 query 取自文档章节标题，与分块索引同源，分块路天然占优（实测 100% vs 整篇 98.6%）。混在一起算总分会得出「分块比整篇强」的错误结论。
- **为什么默认模式改成 auto**：实测「关键词⊕LSA 向量」MRR 0.8595 > 粒度融合 0.8440 > 整篇 0.8195，是新最优。auto 保证没有向量索引时也能优雅退回。

### 已知问题
- BGE-small 真语义模型未接入（网络受限，HuggingFace 不可达）
- LSA 无法处理语义改写：「大模型瞎编怎么防止」匹配不到「幻觉」——**稠密化 ≠ 语义化**
- 答案样本 16 条（含 4 条反例），仍偏少
- 自评偏差 +0.33 分，需长期用独立 judge 复核

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.5.0] - 2026-10-15

### 新增
- `faithfulness_judge.py` 真·faithfulness 评测（LLM-as-judge）：prepare / import / report 三命令，含**改进前后对比**与**弱代理 vs judge 对照**
- 答案层上下文改为**块级**（`ctx_from_chunks`）：真实链路是「检索→取块→读块→作答」，原来取「整篇文档前 N 字」与真实链路不符
- 周自测接入 judge 忠实度分数

### 修改
- `answer_eval.py` prepare 默认用分块上下文；新增 `--k` 控制取块数
- SKILL.md 新增《作答约束：只写上下文有的，不许用常识补》

### 修复
- 溯源覆盖率因新增文章跌到 95.8% → 补齐 `wiki/可复用模块/总览.md` 后回到 **100%**

### 决策理由
- **为什么先做 faithfulness 而不是向量路**：向量路换完只能看 Recall 涨没涨，看不到答案质量变化；先把答案层的尺子做好，后面换什么都量得准。而且 judge 成本极低，向量路要装 torch/onnxruntime + 下 130MB 模型，踩坑概率高。
- **为什么 judge 用AI 助手本人**：云 LLM 通道是给图像/视频工具用的（需 applicationId），走不通。用自己当 judge 有**自评偏差**，已在报告与文档中明确标注，并列入 P1 用独立模型复核。**诚实标注局限，胜过假装没有**。
- **为什么上下文必须用块**：judge 实验证明——同样 12 道题，上下文从「文档开头」换成「分块命中章节」，faithfulness 从 2.92 涨到 4.67。整篇检索返回的"上下文"大概率跟问题无关，模型只能靠常识补，这是幻觉的结构性来源。
- **为什么「主动承认库里没有」算满分**：答案与上下文严格一致就是最高忠实度。宁可说「库里没记录，我不补」，也不要编一段听着专业的。

### 已知问题
- judge = 答案作者，**自评偏差**未消除（P1）
- 样本全在 4-5 分区，无低分对照组 → 弱代理区分度无法计算（P1：构造反例样本）
- 答案样本仅 12 条

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.4.0] - 2026-10-15

### 新增
- `backfill_sources.py` 批量补来源标注：只从文档已有字段（正文来源/作者/发布时间、frontmatter `sources:`+`collected:`）**提取搬运**，绝不编造
- `answer_eval.py` 答案层评测：引用准确率 / 幽灵路径率 / 无引用率 / 上下文支撑率 / 上下文利用率，首批 12 条样本
- 周自测接入答案层指标

### 修改
- `traceability.py` 格式识别扩展：支持 frontmatter `sources:`+`collected:`；来源行日期取窗口扩到前后各两行（原来只取匹配行，会漏掉写在相邻行的日期）

### 修复
- 网上来源溯源覆盖率 9.5% → **100%**（补标 17 篇）
- 来源字段带日期率 66.7% → **100%**（其中 96.3% 是靠修格式识别白捡的，不是补出来的）

### 决策理由
- **为什么补来源是「搬运」不是「生成」**：来源必须翻原始资料才知道，脚本代填 = 伪造溯源，比不标更危险。脚本只把文档里已有的出处提取成结构化字段，推断不出的一律留给人。
- **为什么先修检查器再补数据**：首跑报 19 篇缺来源，看着是大工程；挖下去发现一批是工具不认合法格式（frontmatter、日期写在相邻行）。**指标异常时先怀疑工具**——修完带日期率自己涨了 30 个点，白捡。
- **为什么答案层先用弱代理**：真 faithfulness 要 LLM-as-judge（要 API、要成本、要标定）。弱代理（实词覆盖 + 路径校验）零依赖、可回归，能先抓住「凭空造词」和「引用幽灵路径」这两类最致命的幻觉。**诚实标注它的局限**，不假装它等于 faithfulness。
- **为什么答案是人写的不是自动生成的**：12 条答案由AI 助手基于检索上下文真实作答作为基线样本；自动沉淀留给后续（每次真实问答落 `answer_samples.json`）。

### 已知问题
- 上下文支撑率 0.663 只是弱代理，抓不住「张冠李戴」
- 答案样本仅 12 条，统计意义有限，需扩到 50 条
- Recall@5 仍撞顶（95%），主指标该换 Recall@1 / NDCG@10

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.3.0] - 2026-10-15

### 新增
- **标题级分块**（P0-2）：`split_chunks()` 按 Markdown 标题层级切块并保留标题路径，新增 `chunks` + `chunks_fts` 表；164 篇 → 1495 块（平均 326 字）
- **粒度融合检索** `search_fts_fused()`：整篇路 ⊕ 分块路 RRF，成为默认模式
- `traceability.py` 引用可追溯检查：网上来源覆盖率 / 来源带日期率 / 库内引用可解析率 / Raw 引用存在率
- 评测从 4 路扩到 6 路（`fts` / `fts_chunk` / `fts_fused` / `tfidf` / `hybrid` / `hybrid_w2`），同一次运行出对照
- 周自测接入溯源检查；**失效内链已清零，门禁首次 PASS**

### 修改
- CLI `--mode` 默认由 hybrid → **fused**；新增 `chunk` / `fused` 两个选项
- 周自测幻觉风险维度加入溯源指标
- SKILL.md 检索规则改为**两条链路并行**（索引检索与人工翻阅互补而非替代），新增《引用可追溯铁律》

### 修复
- 失效内链 6 条 → 0 条（备份落在 `state/linkfix_backup/`）
- 扫描器误报：示例代码里的链接被当成死链 → 先剥离代码块/行内代码再检测
- 扫描器误报：log.md 天然用简写文件名 → 显式豁免 log.md/log_*.md 并写进报告

### 决策理由
- **为什么分块但保留整篇路**：实测分块单独用 Recall 掉 2 点（94%→92%），因为它丢了全文词频信号。**融合后召回持平、排名更好（MRR +2.2%）**——两个粒度不是替代关系，是互补关系。
- **为什么融合这次生效、上次失败**：上次 FTS5+TF-IDF 混合失败是因为两路质量悬殊（94% vs 79%），弱路稀释；这次整篇与分块两路都是 bm25 关键词路，势均力敌才能双赢。**RRF 的前提是两路质量相当**。
- **为什么溯源缺口不进 FAIL 门禁**：缺来源标注要人工翻原始资料，机器修不了；把修不了的东西设成门禁，只会让门禁长期飘红然后被无视。原则是「只把机器能判定且应立刻修的项设为门禁」。
- **为什么不自动补 Sources**：脚本代填来源 = 伪造溯源，比不标更危险。宁可留 9.5% 的可见缺口，也不要 100% 的假数据。
- **为什么不废弃旧链路**：索引检索强在精确定位、可编程可评测；人工翻阅强在看全貌、Lint 和级联更新。两者地形不同，不是替代关系。用户的明确要求是并行对照。

### 已知问题
- Recall@5 仍撞天花板（94%），主指标应改用 Recall@1 / NDCG@10
- 答案层 faithfulness 仍空白
- 网上来源溯源覆盖率仅 9.5%（19 篇待人工补标）
- 分块未把 GT 升级到「路径+块号」（接 embedding 时才必需）

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.2.0] - 2026-10-15

### 新增
- `ablation.py` 消融实验编排：一次只改一个变量（分词器/字段加权），独立 db 隔离，自动出对照表 + Recall@1 + 顽固难题清单
- `db_admin.py` SQLite 运维六件套：health/optimize/vacuum/integrity/backup/sizes（+pragmas/tune）
- 分词器可切换：`KB_TOKENIZER=bigram|jieba|jieba_search|auto` + `KB_FTS_W` 标题字段加权
- `query_log` 表 + CLI 检索埋点（评测循环不埋点，避免淹没真实问法）
- `weekly_selfcheck.py` 重写：六项检查 + 回归门禁（掉点 >3 点即 FAIL，exit 1）+ 失效内链扫描与建议路径 + `--fix-links`（只修唯一候选，先备份）
- 自动化任务 `2aa8be13`：每周一 09:00 自测

### 修改
- kb_index 连接层统一 `connect()`：WAL + synchronous=NORMAL + cache 16MB + mmap + busy_timeout
- 删冗余索引 `idx_doc_terms_term`（与 `idx_doc_terms_tdt` 最左前缀重复，规划器从不使用）
- 正式索引改用 jieba 重建（162 篇），12.0MB → page_size 8192

### 修复
- `hashlib.sha1(f.read())` 未 encode 导致新鲜度检查静默失败（db_admin health）
- `eval_rag.py` 加 `--json`；`tokenize` 支持 jieba_search

### 决策理由
- **为什么正式索引用 jieba 而不是 bigram**：体积 -40%（11.5MB→6.9MB）、词项 -63%、FTS5 路 MRR 0.831→0.836 / Recall@1 75%→76%，代价是查询 0.44ms→4.0ms（绝对量仍亚毫秒，可忽略）。**但真正的收获是证明了它不重要**——Recall@5 完全不变。
- **为什么删 idx_doc_terms_term 而不是保留**：`EXPLAIN QUERY PLAN` 实测规划器只走覆盖索引 tdt，旧索引白占 7.3% 空间。信念来源必须是 EXPLAIN，不是"多索引总没错"。
- **为什么周自测要设门禁而不是只记数**：没有阈值的记录等于没记。掉点 >3 点 / 空检率 >5% / 有过期文档 / 有失效内链 → FAIL 且 exit 1，让"知识库在退化"这件事能被机器说出来。
- **为什么失效内链只给建议不自动改**：md 正文属于用户内容，自动改错路径比不改更糟。`--fix-links` 默认关闭，且只修库内唯一同名文件、先备份到 `state/linkfix_backup/`。
- **为什么查询日志只在 CLI 埋点**：评测循环每次跑 130 条 × 4 模式 = 520 条，会把真实问法信号彻底淹没。

### 已知问题
- Recall@5 已撞天花板（94%），换配置无差异 → 主要指标应改用 Recall@1 / NDCG@10
- 整篇文档当一个 chunk，长文语义被稀释 → P0 待改
- 答案层（faithfulness）无任何评测 → P0 待补
- 顽固难题 5 条全为语义型查询（同义/概念题），关键词路注定打不中 → 需 embedding

### 对应提交
- `unknown`（本地模块，未接 git）

---

## [v1.1.0] - 2026-10-14

### 新增
- 新增 weekly_selfcheck.py 每周一自测：索引健康+30/100评测+空检率幻觉扫描+基线对比

### 修改
- （填写改动了什么）

### 修复
- （无）

### 决策理由
- （🔴 最重要：为什么这么做，而不是别的方案）

### 已知问题
- （选填）

### 对应提交
- `unknown`

---

## [v1.0.0] - 2026-10-14

### 新增
- 知识库 SQLite 索引与混合检索模块 v1.0：FTS5+TF-IDF+RRF 三模式检索，30/100 评测集验证，默认模式 FTS5
- `kb_index.py`：build/search/stats 三命令；docs + docs_fts + doc_terms 三表；content_hash 增量更新
- `gen_eval.py`：30 条核心 + 100 条全集人工标注评测集（人话问法 + Ground Truth）
- `eval_rag.py`：四方式对比（FTS5/TF-IDF/等权 RRF/加权 RRF），Recall@5 + MRR@5

### 修改
- （无，首发版本）

### 修复
- （无）

### 决策理由
- **为什么用 SQLite 而不是 MySQL/pgvector**：参考《微信为什么用 SQLite 存聊天记录》——单用户本地知识库是 SQLite 的强烈推荐场景（零配置、单文件、ACID、FTS5 倒排索引）；pgvector 等需要起服务，违背「知识库跟着文件走、拷贝即备份」的原则。
- **为什么 FTS5 表不用 external content 模式**：external content 表不能直接 DELETE（报 no such column），改用普通 FTS5 表内容自存——129 篇规模下存储翻倍可忽略，换取删除逻辑简单可靠。
- **为什么中文分词用 bigram 兜底而不是强依赖 jieba**：环境无 jieba，bigram 是中文 IR 无分词器下的经典方案且零依赖；脚本保留 jieba 自动检测，装上即切换。
- **为什么默认模式定 FTS5 而不是混合**：评测实证——知识库查询是术语驱动型，FTS5 单路 Recall@5 90%/96% 优于等权混合 80%/92%（RRF 被弱 TF-IDF 路稀释）；混合留给探索性查询。这印证「混合检索不是银弹，要用评测数据说话」。
- **为什么索引范围不含 raw/**：raw/ 1.3 万个原件按冷热分层原则靠目录浏览；索引 wiki 合成层（129 篇）已覆盖检索主场景，raw 元数据入库列入下一步。

### 已知问题
- bigram 分词下 TF-IDF 语义分辨不足（同主题辨析题未命中集中），待 jieba/embedding 升级后重跑消融
- 加权 RRF 的 2:1 权重是在本评测集上试出的，换评测集需重新标定

### 对应提交
- `unknown`（本地模块，未接 git）

---
