# 生产同步清单（T1 调查产出）

> 生成时间：2026-11-03 ｜ 作者：寇豆码（工程师）
> 仓库侧：`F:\ZDH\kb-from-zero\modules\知识库索引\`（git，remote `DongHangZhao/knowledge-base-from-zero`）
> 生产侧：`F:\ZDH\知识库\modules\知识库索引\`（非 git，跑着 4.7GB 索引库 `state/kb.db`）
>
> **本文件只做调查，未修改任何一侧的任何文件。** T2（补 vec_m3 缺失向量）待主理人确认本清单后执行。

---

## 0. 结论摘要（先看这 5 条）

| # | 结论 | 关键证据 |
|---|---|---|
| 1 | 仓库侧 **16 个文件/2 个目录** 生产侧完全没有，可**直接复制**，零风险（生产侧无任何文件 import 它们） | 反查结果 `(无)`，见 §5.3 |
| 2 | 共有的 31 个文件里 **15 个有差异**（14 个 `.py` + 1 个 `.json`）；本次建议**覆盖 8 个**、**必须打补丁后覆盖 2 个**、**保留生产原版 5 个**（8+2+5=15） | §2 / §3 / §4 |
| 3 | **没有任何一个共有文件，仓库版删掉了生产版存在的函数/类**（AST 全量比对：`FUNC prod_only` 全为 `(none)`） | §2 每行 `删除` 列 |
| 4 | 🔴 **最大风险不是"删函数"，而是"开源化改造把生产可用路径改坏了"**：4 个文件把 `E:/kb_models/bge-m3` 或 `F:/ZDH/kb-from-zero/tools` 改成了仓库相对路径，覆盖即坏 | §3 四个 🔴 |
| 5 | 🔴 `kb_rag.py` 覆盖后**检索默认行为会变**（`search_unified` 默认走 `l0` 查询改写），生产侧有 **16 个文件** 调它 | §2 风险列 + §6 |

---

## 1. 文件集合差异总览（可复现）

测量命令（只读）：

```python
# E:/_venvs/gpu/Scripts/python.exe -X utf8
# 仅比较 .py / .json / .txt，排除 __pycache__ .git _versions state models _models eval_sets
REPO_ONLY(16)   BOTH(31)   PROD_ONLY(13448 → 其中根目录仅 54 个，其余 13393 在 envs/ 目录)
```

`BOTH(31)` 中有差异的 **15 个**（14 个 `.py` + 1 个 `.json`）：

| 文件 | 仓库行 | 生产行 | 行变化(+/-) | 结论分类 |
|---|---|---|---|---|
| kb_rag.py | 1497 | 1245 | +303 / -51 | §2 覆盖 |
| eval_rag.py | 348 | 242 | +147 / -41 | §2 覆盖 |
| run_tests.py | 461 | 433 | +43 / -15 | **§3 打补丁后覆盖 🔴** |
| kb_index.py | 1551 | 1516 | +47 / -12 | **§3 打补丁后覆盖 🔴** |
| weekly_selfcheck.py | 655 | 618 | +44 / -7 | §2 覆盖 |
| vector_backfill.py | 170 | 164 | +40 / -34 | **§3 打补丁后覆盖 🔴** |
| _test_rerank_fallback.py | 160 | 106 | +54 / -0 | §2 覆盖 |
| _test_rotate.py | 116 | 105 | +11 / -0 | §2 覆盖 |
| bge_encoder.py | 535 | 535 | +1 / -1 | **§4 保留生产 🔴** |
| cloud_publishable.py | 197 | 198 | +4 / -5 | **§4 保留生产 🔴** |
| _test_privacy_gate.py | 128 | 128 | +5 / -5 | §2 覆盖 |
| faithfulness_judge.py | 273 | 273 | +4 / -4 | §2 覆盖 |
| build_vec_m3.py | 189 | 188 | +2 / -1 | **§4 保留生产 🔴** |
| reencode_m3.py | 117 | 116 | +2 / -1 | **§4 保留生产 🔴** |
| alias_table.json | 10 键 | 15 键 | — | **§4 保留生产（生产更强）** |

其余 16 个共有文件**字节完全相同**（SAME）：`_garbage_filter.py` `_test_build_task.py` `_test_buildlock.py` `_test_garbage_filter.py` `_test_meta_filter.py` `_test_split_chunks_v2.py` `ablation.py` `answer_eval.py` `bge_reranker.py` `build_task.py` `expand_eval.py` `ingest_raw.py` `rechunk_all.py` `reencode_gpu.py` `traceability.py` `vector_index.py`

---

## 2. 覆盖生产：8 个文件（可直接复制）

> 全部经 AST 顶层符号比对：**`FUNC prod_only` 均为 `(none)`，`CLASS prod_only` 均为空** —— 即仓库版没有删掉生产版的任何函数。
> `删除` 列写的都是**行级**删除（文案/死代码/同义替换），不是能力删除。

### 2.1 `kb_rag.py` → 覆盖 ✅（+303/-51，1245→1497 行）

| 项 | 内容 |
|---|---|
| **新增函数** | `_m3_skip_tail(mode, limit)`、`backfill_m3(conn, limit, batch)` |
| **新增 import** | `import vec_tables`、`import query_rewrite`（**模块顶层**，必须先放这两个文件，否则 import 直接崩） |
| **新增参数** | `search_unified(..., rewrite=None)`（**追加在参数表末尾**，向后兼容）；`--rewrite {off,l0,l1,l2}` 加到 `search` / `ask` 两个子命令 |
| **新增建表兜底** | `backfill_bge` 里提前 `bge_encoder.ensure_tables(conn)`（首次 build 不再 `no such table` 崩） |
| **新增 stats** | `vec_m3` 行数 + `m3_model.built_at` 并排显示 |
| **重写 doctor** | `checks` 改由 `vec_tables.diff_report()` 驱动；m3 覆盖率按 `KB_BACKFILL_M3` 档位判红 |
| **build 4/4** | `BGE 补写 → m3 补写 → 四表断言`；写 `index_state.m3_lag` / `vec_consistency`；全量+企业档漂移返回 `rc=3` |
| **删除（行级）** | ① `backfill_bge` 内 `import numpy as np`（**死代码**，本函数从未用 np）② `search_unified` 里三路单查询写法（改为按 `plan.subqueries` 循环，`off` 档逐位一致）③ `notes.append(note)` 加 `not in notes` 去重 |
| **删除（能力级）** | **无** |
| **依赖** | 🔴 必须先复制 `vec_tables.py` + `query_rewrite.py`（同目录），否则 `import kb_rag` 失败 |
| **风险** | 🔴 **默认行为变化**：`search_unified(rewrite=None)` → 读 `KB_REWRITE`，未设则 `l0`（确定性拆分）。生产侧 **16 个文件** 调 `search_unified`（`_bench_like.py` `_diagnose_misses.py` `_probe_boundary_share.py` `_probe_refuse_t1.py` `_probe_rerank_window.py` `_probe_vecpath.py` `_sandbox_model_ab_fused.py` `_sweep_media_penalty.py` `_test_meta_filter.py` `_test_rerank_fallback.py` `_verify_refuse_t1.py` `_verify_t1.py` `bge_reranker.py` `eval_layered.py` `kb_index.py` `kb_rag.py`）。回滚开关：`KB_REWRITE=off`（零代码，逐位回到改造前） |

### 2.2 `eval_rag.py` → 覆盖 ✅（+147/-41，242→348 行）

| 项 | 内容 |
|---|---|
| **新增函数** | `_modes(rewrite)`、`search_unified_mode(conn,q,topk,rewrite)`、`_mode_fn(mode,rewrite)`、`_quantile(vals,p)` |
| **新增 import** | `import metrics as _metrics`（**依赖 metrics.py**）、`import time` |
| **新增参数** | `--k`（cutoff，默认 5）、`--rewrite {off,l0,l1,l2}`、`--set` 增加 `multihop` |
| **新增能力** | ① 每个 mode 出 **p50/p95/pmax 延迟** ② 正式测前**预热一轮**（防冷启动污染 p95）③ 缺评测集**跳过并说清**（不再 FileNotFoundError 崩）④ `gt` 支持**多路径列表** ⑤ JSON 加 `summary` 顶层平铺（供 `kg_gate` 取数） |
| **删除（行级）** | `import math`（改用 `metrics` 后不再用）；`render_table` 旧 5 列表头换 7 列 |
| **删除（能力级）** | **无**。生产的 `--set` 5 个选项（30/100/both/auto/all）全部保留，只是多一个 multihop |
| **依赖** | 🔴 必须先复制 `metrics.py`；`--set multihop` 需要 `state/kb_eval_multihop_30.json`（生产侧没有 → 会走"缺评测集，跳过"分支，不崩） |

### 2.3 `weekly_selfcheck.py` → 覆盖 ✅（+44/-7，618→655 行）

| 项 | 内容 |
|---|---|
| **新增** | `import vec_tables`；`check_index()` 返回 dict **追加** `vec_tables` 键（不动既有键，看板兼容）；`[1/6]` 段与 markdown 报告各加一行四表一致性 |
| **删除（行级）** | 4 处 `栋行` → `作者`（开源化去标识） |
| **删除（能力级）** | **无**。四表一致性**只告警不 FAIL**（个人档无 GPU 时 m3 不全属允许状态） |
| **依赖** | 必须先复制 `vec_tables.py` |

### 2.4 `_test_rerank_fallback.py` → 覆盖 ✅（+54/-0，纯新增）

新增 `env_gate()`：无 torch / 无精排模型 / 无索引库时打印 `[PASS] skipped-rerank-env：...` 并 `return 0`。
**删除 0 行**。生产侧跑它：本机有 torch+模型+索引 → 6 条断言照旧全跑，行为不变。

### 2.5 `_test_rotate.py` → 覆盖 ✅（+11/-0，纯新增）

新增 `if os.name != "nt": 打印 [PASS] skipped-on-posix; sys.exit(0)`。
**删除 0 行**。生产是 Windows → 该分支不触发，6 条断言照旧全跑。

### 2.6 `_test_privacy_gate.py` → 覆盖 ✅（+5/-5，纯去标识）

`万州五大系统`→`示例市五大系统`、`赵栋行-基础档案`→`示例用户-基础档案`、删掉电话号 `13800001111`、`F:/ZDH/kb-from-zero/tools/privacy_scan.py` 改成 `tools/privacy_scan.py（仓库根）` 描述。
断言对象是**路径前缀** `raw/隐私/` `raw/个人信息/` `raw/项目/` —— 两边一致，**判定结果不变**。

### 2.7 `faithfulness_judge.py` → 覆盖 ✅（+4/-4，纯去标识）

`小落` → `AI 助手`（4 处），含 `--judge` 默认值。**逻辑零变化**。

### 2.8 `vector_backfill.py` → **打补丁后覆盖** 🔴（+40/-34，164→170 行）

> 归到 §2 是因为**逻辑价值高**（T2 就靠它），但**必须先改 1 行**，所以打了红标。补丁见 §3.4。

| 项 | 内容 |
|---|---|
| **新增** | `import vec_tables`；缺口统计 + 终态校验**全部改用** `vec_tables.counts/missing_ids/diff_report`（原来自己写了一份 `NOT IN`）；打印加 `缺 trigram` |
| **重写** | m3 补写改调 `vec_tables.backfill_m3(conn, encode, ..., text_fn=m3_text, ...)`，编码口径仍是本文件的 `m3_text`（未变） |
| **删除（行级）** | 旧的手写 `total/miss_bge/miss_m3` 三条 SQL；旧的 `encode → INSERT OR REPLACE` 内联块；旧的 4 行数相等判据 `ch == bg == tg == m3` |
| **删除（能力级）** | **无**。`chunks==chunks_trigram==chunk_bge==vec_m3` 的行数判据被换成"**缺失 id 集合为空**"，后者更严（孤儿行再多也不假绿） |
| **🔴 必须打补丁** | `M3_DIR = os.environ.get("KB_M3_MODEL_DIR") or <仓库>/models/bge-m3` → 生产侧 `models/bge-m3` **不存在**（§3.4 有证据），会直接 `找不到模型` 退出 |

---

## 3. 打补丁后才可覆盖：2 个（不打补丁就是功能回退）

### 3.1 `kb_index.py` 🔴（+47/-12，1516→1551 行）

**新增（全部是好东西，无删除）**

| 项 | 内容 |
|---|---|
| **新增函数** | `default_lock_path(db_path=None)` —— 锁路径**随库派生**（修多租户 bug：两租户抢同一把 build 锁，且 `_read_meta()` 会报出**别人的 PID**） |
| **改绑定** | `LOCK_PATH = default_lock_path()`（保留兼容别名）；`BuildLock.__init__` 里 `self.path = path or default_lock_path()`（调用时解析，不在导入时快照） |
| **改默认值** | `raw-scan --raw-root`：`r"F:\ZDH\知识库\raw"` → `os.path.join(KB_ROOT, "raw")` |
| **删除（能力级）** | **无**（`FUNC prod_only: (none)`） |

**✅ 已验证 `--raw-root` 改动在**生产侧**等价（不需要补丁）**

```
# 两侧共有的定义（kb_index.py L47，逐字相同）
KB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

生产: F:\ZDH\知识库\modules\知识库索引\kb_index.py → dirname×3 → F:\ZDH\知识库
      os.path.join(KB_ROOT,"raw")  ==  F:\ZDH\知识库\raw   ← 与原硬编码默认值完全相同 ✅
```

**🔴 红：`dir_filter` 被开源化改坏了 —— 覆盖后生产会漏索引 3 篇**

```diff
 # 只收「后端开发/欣铁面试项目/」3 篇：RBAC 权限体系与软删除方案是
     ("modules", os.path.join(KB_ROOT, "modules"), "*/*/*.md", False,
      {"README.md", "RBAC权限体系-可复用模板.md", "软删除与唯一索引冲突-通用方案.md"},
-     {"欣铁面试项目"}),
+     {"示例同事面试项目"}),
```

该 `dir_filter` 的语义（L644 附近的原注释）：`parts[1] not in dir_filter` → **跳过**。
生产库 `modules/后端开发/` 下的真实子目录名是 `欣铁面试项目`；换成 `示例同事面试项目` 后
→ `欣铁面试项目` 不在白名单 → **那 3 篇 RBAC / 软删除方案文档下次 rebuild 起不再进索引**（静默丢召回，不报错）。

**补丁（覆盖后立刻改回 1 处）**

```python
# 覆盖后执行：把开源占位名改回生产真实目录名
-     {"示例同事面试项目"}),
+     {"欣铁面试项目"}),
```

> 更稳做法（可选）：写成 `{"欣铁面试项目", "示例同事面试项目"}` —— 同时兼容开源读者自建同名目录，也让后续再同步不再踩这个坑。

### 3.2 `run_tests.py` 🔴（+43/-15，433→461 行）

**新增（全部是好东西）**

| 项 | 内容 |
|---|---|
| **新增参数** | `--ci`（照跑全部套件，**不写** `data.json`/`index.html`，只认退出码） |
| **改签名** | `gather(skip_tests=False, write=True)`（`write=False` 供 CI） |
| **改 DB_PATH** | `os.environ.get("KB_DB_PATH") or <HERE>/state/kb.db`（CI/多租户能读到临时库） |
| **新增套件 7 条** | `_test_metrics.py` `_test_mcp_server.py` `_test_lock_perdb.py` `_test_vec_tables.py` `_test_kb_profile.py` `_test_query_rewrite.py` `_test_kg.py` |
| **删除（能力级）** | **无函数删除**，但删了 **1 条测试套件注册** —— 见下 |

**🔴 红：仓库版删掉了 `test_dashboard_equiv.py` 套件，而生产侧这个文件**确实存在**

```
=== REPO SUITES (13) ===   ← 无 test_dashboard_equiv.py
=== PROD SUITES (7)  ===
  test_dashboard_equiv.py      EXISTS      ← 生产侧文件真实存在（16000 字节左右，看板采集等价性回归）
```

仓库侧删它的理由（原注释）：「那个文件**全仓不存在**（它属门户层，已随 kb-hub 迁走）」——
**这个理由对生产侧不成立**：`F:\ZDH\知识库\modules\知识库索引\test_dashboard_equiv.py` 确实在，
且它依赖生产专有的 `dashboard_gen.py`（`collect_raw_stats()` 性能回归，exFAT 上 170.2s → 0.1s 的真事故）。
直接覆盖 = **一键验收少一条真实守护，且少的那条正是性能事故回归钉**。

**补丁（覆盖后把这一行登记回去）**

```python
# 覆盖后：在 SUITES 末尾补回生产专有套件
    ("_test_kg.py",             "图谱实验门",   "实体抽取 / 1-hop 邻居 / 决策门边界"),
+   ("test_dashboard_equiv.py", "看板等价性",   "遍历优化前后逐文件比对，口径不漂（生产专有）"),
]
```

### 3.3 `vector_backfill.py` 补丁 🔴

见 §2.8 与 §3.4。

### 3.4 🔴 全部 4 处「开源化路径改坏」证据（这是本轮最大坑）

仓库为了开源，把本机绝对路径改成**仓库相对路径**。生产侧目录层级与仓库不同 →
复制过去后路径解析全部指向**不存在的目录**。

| 文件 | 仓库版写法 | 生产侧解析结果 | 存在吗 |
|---|---|---|---|
| `bge_encoder.py` L52-53 | `os.path.join(HERE, "models", "bge-m3")` | `F:\ZDH\知识库\modules\知识库索引\models\bge-m3` | ❌ **不存在** |
| `build_vec_m3.py` L34 | `os.path.join(dirname(__file__), "models", "bge-m3")` | 同上 | ❌ **不存在** |
| `reencode_m3.py` L35 | 同上 | 同上 | ❌ **不存在** |
| `vector_backfill.py` L36 | 同上 | 同上 | ❌ **不存在** |
| `cloud_publishable.py` L149 | `os.path.join(HERE, "..", "..", "tools", "privacy_scan.py")` | `F:\ZDH\知识库\tools\privacy_scan.py` | ❌ **不存在** |

**实测证据（ls 原文）**

```
$ ls -la F:/ZDH/kb-from-zero/modules/知识库索引/models/
total 0
drwxr-xr-x  ... Nov  3 00:59 .        ← 空目录（models 被 gitignore，仓库里本来就没有模型）
drwxr-xr-x  ... Nov  3 00:55 ..

$ ls -la F:/ZDH/知识库/modules/知识库索引/models/
drwxr-xr-x ... Oct 22 12:13 bge-reranker-v2-m3
drwxr-xr-x ... Oct 15 17:12 bge-small-zh
                                      ← 🔴 没有 bge-m3 子目录

$ ls -la E:/kb_models/
drwxr-xr-x ... Oct 28 15:07 bge-m3    ← m3 模型真实位置（2.5G）

$ ls -la F:/ZDH/知识库/tools/privacy_scan.py
ls: cannot access ...: No such file or directory      ← 🔴 生产侧没有 tools/
$ ls -la F:/ZDH/kb-from-zero/tools/privacy_scan.py
-rwxr-xr-x ... 17977 Oct 22 15:17                      ← 只在仓库侧
```

**补丁方案（三选一，推荐 A）**

- **A（推荐，逐文件硬编码回退）**：覆盖后把 4 个文件的 `M3_DIR` 打回生产可用值
  ```python
  M3_DIR = os.environ.get("KB_M3_MODEL_DIR") or r"E:/kb_models/bge-m3"
  ```
  `bge_encoder.py` 同理：`M3_MODEL_DIR = os.environ.get("KB_M3_MODEL_DIR") or "E:/kb_models/bge-m3"`
  注意：`bge_encoder.py` / `build_vec_m3.py` / `reencode_m3.py` 三个文件**唯一差异就是这一行**，
  覆盖收益为 0 → 按 §4 处理（**直接保留生产原版，不要覆盖**），只有 `vector_backfill.py` 需要打补丁。
- **B（靠环境变量）**：所有跑 m3 的进程都设 `KB_M3_MODEL_DIR=E:/kb_models/bge-m3`。仓库版三个文件都认这个 env。
  风险：`kb_rag.py build` 里 `backfill_m3` 的默认值是 `HERE/models/bge-m3`，**不设 env 时只是 `status=no-model` 跳过、不报错** → 极易被误认为"m3 已齐"。
- **C（建目录软链/拷贝）**：把 `E:/kb_models/bge-m3` 链到 `models/bge-m3`，2.5G 不建议拷贝。

---

## 4. 保留生产原版：5 个（仓库版反而弱 / 覆盖即坏）

| 文件 | 仓库 vs 生产 | 为什么**不能**覆盖 |
|---|---|---|
| `bge_encoder.py` | +1/-1，**唯一差异**是 `M3_MODEL_DIR` | 生产版 `"E:/kb_models/bge-m3"`（可用）；仓库版 `HERE/models/bge-m3`（**不存在**）。覆盖 = m3 语义路全断，且**换不来任何代码改进**（差异只有这一行） |
| `build_vec_m3.py` | +2/-1，**唯一差异**是 `M3_DIR` | 同上。生产版 `M3_DIR = "E:/kb_models/bge-m3"` 直连可用 |
| `reencode_m3.py` | +2/-1，**唯一差异**是 `M3_DIR` | 同上 |
| `cloud_publishable.py` | +4/-5 | 🔴 **覆盖后 `find_privacy_scan()` 返回 `None`** → 生产 `run_privacy_scan()` 按"门禁故障=不放行"处理 → **所有导出/上云永久 BLOCK**。差异本质只有：①`栋行`→`作者` ②`万州`→`示例市` ③路径 `HERE/../../../kb-from-zero/tools` → `HERE/../../tools`。前两项是去标识（无功能价值），第三项在生产侧**是坏的**。代码逻辑（`KB_PRIVACY_SCAN` env 优先、显式配置不静默回退）两边一致 → **零收益，纯风险** |
| `alias_table.json` | 仓库 **10 键** / 生产 **15 键** | 生产版更强，且带**实测归因注释**。逐键对比见下 |

### `alias_table.json` 逐键对比（覆盖 = 净丢 10 个生产实测词条）

| 键 | 仓库侧 | 生产侧 | 说明 |
|---|---|---|---|
| `_comment` | 有（通用） | 有（含建立/扩容日期） | 生产更详细 |
| `_why` | 有 | ❌ | 仓库独有（通用说明） |
| `_how_to_add` | ❌ | **有**（"只从实测失败案例加词，不拍脑袋扩表"） | 🔴 生产独有，方法论注释 |
| `_note_zh_en` | ❌ | **有**（中英术语错配类归因） | 🔴 生产独有 |
| `rerank` | `['精排','交叉编码器重排']` | `['重排','精排','cross-encoder']` | **生产多 1 个同义词** |
| `智能体` | ❌ | **有** | 🔴 生产独有（实测归因） |
| `工作流` | ❌ | **有** | 🔴 生产独有 |
| `flask` | ❌ | **有** | 🔴 生产独有 |
| `提示词注入` | ❌ | **有** | 🔴 生产独有 |
| `提示词` | ❌ | **有** | 🔴 生产独有 |
| `五个系统` | ❌ | **有**（→`万州五大系统`） | 🔴 生产独有，**本库专有业务词** |
| `急诊急救` | ❌ | **有** | 🔴 生产独有 |
| `血透` | ❌ | **有** | 🔴 生产独有 |
| `配图` | ❌ | **有** | 🔴 生产独有 |
| `rag` | ❌ | **有** | 🔴 生产独有 |
| `新闻爬虫` | ❌ | **有** | 🔴 生产独有（中英错配归因） |
| `rrf` `bm25` `hyde` `fts5` `embedding` `chunk` `llm` | **有**（7 个通用技术词） | ❌ | 仓库独有（**可取**，见下） |

**建议动作（不是覆盖，是"取并集"）**：保留生产 15 键不动，把仓库独有的 7 个通用技术键
（`rrf` `bm25` `hyde` `fts5` `embedding` `chunk` `llm`）+ `_why` **追加**进生产文件。
⚠️ 但这会改变检索行为（别名扩展会多命中），**必须重跑 `eval_rag.py --set all` 复测 R@5/R@20 不退化再留**
（生产版 `_how_to_add` 自己就写着这条纪律）。**默认建议：本次同步不动 `alias_table.json`。**

---

## 5. 绝对不碰：生产专有 54 个根目录文件（+`envs/` 13393 个）

**规则：以下文件仓库侧完全没有，一律不读、不改、不删、不移动。**

| 类别 | 文件 |
|---|---|
| 媒体处理（10） | `media_asr.py` `media_doc.py` `media_ocr.py` `media_pdf.py` `doc_ole.py` `check_imgs.py` `_mm_inventory.py` `_mm_inventory.json` `_gpu_caption_probe.py` `_gpu_caption_probe.json` |
| 看板/门户（4） | `dashboard_gen.py` `test_dashboard_equiv.py` `db_admin.py` `build_vs_output.py` |
| 探测/沙盒（19） | `_bench_embed.py` `_bench_like.py` `_diagnose_misses.py` `_probe_asr_gpu.py` `_probe_boundary_share.py` `_probe_refuse_t1.py` `_probe_rerank_window.py` `_probe_vecpath.py` `_verify_refuse_t1.py` `_verify_t1.py` `_sandbox_b4_check.py` `_sandbox_chunk_overlap.py` `_sandbox_encode_ab.py` `_sandbox_model_ab.py` `_sandbox_model_ab_fused.py` `_sandbox_oversample.py` `_sandbox_weight_ab.py` `_sweep_media_penalty.py` `_measure_chunks.py` |
| 备份/清理工具（6） | `_clean_orphans.py` `_clean_vec_orphans.py` `_count_backups.py` `_inspect_backups.py` `_purge_backups.py` `_night_run.py` |
| 评测扩展（4） | `eval_layered.py` `gen_eval.py` `_expand_eval.py` `_split_eval.py` |
| 其他（11） | `backfill_sources.py` `faithfulness_probe.py` `import_vec_m3.py` `rebuild_title_fts.py` `_demo_local_sync.py` `_dl_model.py` `_pdf_triage.py` `_smoke_t10_caption.py` `_smoke_t10_video.py` `_smoke_t10_whisper.py` `_smoke_t4_ocr.py` |
| 目录 | `envs/`（13393 个 .py/.txt/.json，独立 venv） `_versions/` `_models/` `outputs/` |
| 文档（非同步目标） | `HANDOFF.md` `ROADMAP.md` `README.md` `CHANGELOG.md` 及全部 `.bak` |

> 注意 `_clean_vec_orphans.py`（生产专有，3308 字节）—— 它就是处理**孤儿向量**的现有工具。
> T2 里「570 条孤儿先只报告不删」，**绝不动用这个脚本**（它会 DELETE），本清单把它列入禁区。

---

## 6. 三项附加必查

### 6.1 `eval_rag.py`：生产版是"老版本"，还是被单独改过？

**结论：是 10-15 时期的老版本，没有被单独改过。生产版没有任何仓库版缺失的功能。**

证据（AST 顶层符号 + argparse 选项）：

| 检查项 | 生产版（242 行） | 仓库版（348 行） | 判定 |
|---|---|---|---|
| 顶层函数 | `evaluate` `render_table` `fmt_pct` `main` | 同 4 个 **+** `_modes` `_mode_fn` `_quantile` `search_unified_mode` | 生产是**子集** |
| 生产独有函数 | — | — | **`(none)`** |
| `--set` 选项 | `["30","100","both","auto","all"]` | 同 5 个 **+ `"multihop"`** | 生产是**子集** |
| `--k` / `--rewrite` | 无 | 有 | 仓库新增 |
| 延迟指标 | 无 | `p50`/`p95`/`pmax` + 预热 | 仓库新增 |
| 四表/JSON `summary` | 无 | 有（供 `kg_gate` 取数） | 仓库新增 |
| `import math` | 有（自己算 NDCG） | 删掉（改调 `metrics.ranking_metrics`） | 口径统一，非能力丢失 |

→ 生产版就是"加 `--rewrite`/延迟/预热/metrics 之前"的同一份代码，**没有本地魔改痕迹**，可安全覆盖。

> 📌 口径更正：主理人给的「eval_rag.py 590 行差异」应是 `diff` 输出的**含上下文**行数。
> 本清单按"实际增删行"计量：**仓库 348 行 / 生产 242 行，+147 / -41**。

### 6.2 `run_tests.py`：两侧 SUITES 差在哪？

```
=== REPO SUITES (13) ===                    === PROD SUITES (7) ===
  _test_buildlock.py        EXISTS            _test_buildlock.py        EXISTS
  _test_build_task.py       EXISTS            _test_build_task.py       EXISTS
  _test_meta_filter.py      EXISTS            _test_meta_filter.py      EXISTS
  _test_privacy_gate.py     EXISTS            _test_privacy_gate.py     EXISTS
  _test_rerank_fallback.py  EXISTS            _test_rerank_fallback.py  EXISTS
  _test_rotate.py           EXISTS            _test_rotate.py           EXISTS
  _test_metrics.py          EXISTS            test_dashboard_equiv.py   EXISTS  ← 仓库侧没有此行/此文件
  _test_mcp_server.py       EXISTS
  _test_lock_perdb.py       EXISTS
  _test_vec_tables.py       EXISTS
  _test_kb_profile.py       EXISTS
  _test_query_rewrite.py    EXISTS
  _test_kg.py               EXISTS
```

- 两侧**共有 6 条**，文件名与存在性完全一致。
- 仓库**多 7 条**（都随本轮新增文件一起进来，文件都在仓库里 `EXISTS`）。
- 生产**多 1 条** `test_dashboard_equiv.py` —— **文件真实存在（生产专有）**，仓库版把它删了。
- ⚠️ **生产侧没有任何 SUITES 条目指向不存在的文件**（7/7 全 EXISTS）；反而是**仓库版的删除动作**会制造"少测一条"。
  → 这就是 §3.2 必须打补丁的原因。

### 6.3 反查：生产侧有文件 import 了那 7 个新模块（或只在仓库存在的东西）吗？

```
### 生产侧引用这 7 个新模块的文件（反查）:
   (无)
```

扫描范围：`F:\ZDH\知识库\modules\知识库索引\*.py`（根目录全部 .py），正则
`^\s*(?:from|import)\s+(query_rewrite|vec_tables|kb_profile|metrics|mcp_server|kg_extract|kg_gate)\b`。

结论：**零引用** → 新增这 7 个模块 + 7 个测试 + 2 个 requirements 是**纯增量**，不影响任何现网进程。

同时反查了「本轮被改动的符号」在生产侧的使用面，确认签名/语义兼容：

| 符号 | 生产侧引用者 | 兼容性判定 |
|---|---|---|
| `search_unified` | 16 个文件（见 §2.1） | ✅ 签名**末尾追加** `rewrite=None`，关键字调用全部兼容；⚠️ 但**默认行为**变（改写 l0 默认开） |
| `backfill_m3` | **0 个** | ✅ 生产侧没人调（生产 kb_rag.py 里根本没这个函数，本清单是新增） |
| `M3_DIR` | `build_vec_m3.py` `reencode_m3.py` `vector_backfill.py`（3 个，全是共有文件） | ⚠️ 无生产专有文件依赖，所以只要这 3 个按 §3.4/§4 处理即可 |
| `M3_MODEL_DIR` | `bge_encoder.py`（1 个，共有文件） | ✅ 同上 |
| `LOCK_PATH` | `kb_index.py`（自身） | ✅ 仓库版保留 `LOCK_PATH = default_lock_path()` 兼容别名 |
| `default_lock_path` | 仓库新增，生产 0 引用 | ✅ 但覆盖 `kb_index.py` 后 `_test_lock_perdb.py` 才能跑 |

---

## 7. 建议执行顺序（T1 之后，T2 之前）

> 每步都可单独回滚（生产是普通目录，覆盖前 `cp` 一份 `.bak-20261103` 即可）。

| 步骤 | 动作 | 校验 |
|---|---|---|
| 1 | 复制 7 个新模块 + 7 个新测试 + 2 个 requirements（§1 / §2.1 依赖） | `python -c "import vec_tables, query_rewrite, metrics, kb_profile, mcp_server, kg_extract, kg_gate"` 全绿 |
| 2 | 覆盖 `kb_rag.py` `eval_rag.py` `weekly_selfcheck.py` `vector_backfill.py` + **打 §3.4-A 补丁**（`vector_backfill.M3_DIR`） | `kb_rag.py doctor` 能出四表段；`eval_rag.py --set 100` 能跑 |
| 3 | 覆盖 `kb_index.py` + **打 §3.1 补丁**（`dir_filter` 改回 `欣铁面试项目`） | `python kb_index.py stats`；`grep -c 欣铁 kb_index.py` 应为 1 |
| 4 | 覆盖 `run_tests.py` + **打 §3.2 补丁**（补回 `test_dashboard_equiv.py`） | `python run_tests.py --ci` 套件数 14/14 |
| 5 | 覆盖 4 个去标识/跳过类：`_test_privacy_gate.py` `_test_rerank_fallback.py` `_test_rotate.py` `faithfulness_judge.py` | 4 个文件单独跑均 rc=0 |
| 6 | **不动**：`bge_encoder.py` `build_vec_m3.py` `reencode_m3.py` `cloud_publishable.py` `alias_table.json` | `grep M3_DIR` 仍指向 `E:/kb_models/bge-m3` |
| 7 | 生产侧全域 import 冒烟 + 跑一遍查询 | 检索结果与同步前对比（`KB_REWRITE=off` 应逐位一致） |

**回滚开关备忘**：`KB_REWRITE=off`（关查询改写，回到改造前检索行为）、`KB_BACKFILL_M3=0`（关 m3 自动补写）、`KB_STRICT_VEC=0`（四表漂移不判红）、`KB_BUILD_LOCK`（显式指定锁路径）。

---

## 8. T2 预方案（等确认后执行）

> 硬约束复述：**只 INSERT OR IGNORE**；不 DELETE / UPDATE / DROP；孤儿 570 条只报告不删；跑前备份 kb.db 并核对字节数；第二遍必须 0 新增。

### 8.1 实现路径选择（重要技术发现）

`vec_tables.backfill_m3()`（§2.1 依赖的新模块）内部写库语句是 **`INSERT OR REPLACE INTO vec_m3`**（第 301 行），
**不是** `INSERT OR IGNORE`。虽然它**只对 `missing_ids()` 取出的 id 写入**（那些 id 在 `vec_m3` 里本来没有行，
REPLACE 等价于纯 INSERT，已有行永不被碰 —— 模块 docstring 第 264-267 行明确承诺），
但为**严格遵守主理人的硬约束字面要求**，T2 **不直接调用 `vec_tables.backfill_m3()`**，改为：

- 用 `vec_tables.missing_ids(conn, "vec_m3")`（只读）取缺口 id 集合 —— 复用它作为唯一口径（口径一致性收益保留）
- 用 `vector_backfill.m3_text` + `vector_backfill.load_m3_gpu/encode_m3_gpu` 编码（**编码口径唯一来源**，与全库 111441 条同源）
- 写库用**自写的** `INSERT OR IGNORE INTO vec_m3(chunk_id, vec) VALUES(?,?)`，每批 commit
- 不调用 `vec_tables.write_m3_model()`（它 `INSERT OR REPLACE INTO m3_model` 会动已有元数据行）→ 只读记录前后值，写入留给主理人批准
- 孤儿清理**不做**（`_clean_vec_orphans.py` 在生产侧存在但本轮禁用）

### 8.2 执行步骤（4 步，每步带证据）

| 步 | 命令要点 | 产出证据 |
|---|---|---|
| 1 | `--dry-run`：只读统计 `缺 m3`、`孤儿`、待编码 chunk 数、文本总字节、估算耗时 | 期望 `缺=783`、`孤儿=570` ⏳ 待实测确认 |
| 2 | 备份：`cp state/kb.db state/kb.db.bak-20261103-m3fix`，比对字节数（4.7GB 级，`cmp`/`Get-FileHash` 二者一致） | 前后 `stat` 字节数原文 |
| 3 | 分批补：`E:/_venvs/gpu/Scripts/python.exe -X utf8`，`KB_M3_MODEL_DIR=E:/kb_models/bge-m3`，`torch fp16 cuda`，每批 200 条，每批打印 `done/total` 与 `lag` | 每批进度行 |
| 4 | 独立 SQL 复核（**不信托补脚本自报**）：`缺` 必须 0；`孤儿` 仍报 570（不动）；再跑一遍脚本 → `encoded=0` | 补前/补后 `缺/孤儿` 数字 + 耗时 + 库文件字节数 |

### 8.3 已知风险 / ⏳ 待确认

| # | 事项 | 处置 |
|---|---|---|
| 1 | ⏳ `缺 783 / 孤儿 570` 未在本轮重新实测（按指令采信主理人取证） | T2 步骤 1 先只读复测，与主理人数字对齐；不一致则**停下报告** |
| 2 | ⏳ 生产 `vec_m3` 表结构（是否 `PRIMARY KEY(chunk_id)`、有无额外列）未查 | T2 步骤 1 一并 `PRAGMA table_info(vec_m3)`；若不支持 `OR IGNORE` 语义则停下报告 |
| 3 | ⏳ 生产 kb_rag.py 是 11-01 版、**没有** `backfill_m3` | 故 T2 脚本**不依赖** `kb_rag.backfill_m3`，只用 `vector_backfill` 的编码函数（生产版已有：`m3_text` / `load_m3_gpu` / `encode_m3_gpu`） |
| 4 | 4.7GB 库 + WAL：补写期间若并发 build 会抢锁 | 补写前确认无 build 进程；`PRAGMA busy_timeout=600000`；不用 `kb_index.BuildLock` 之外的锁 |
| 5 | 783 条中可能有源 chunk 已被并发删除 | `_fetch_texts` 取不到就跳过（不造空/零向量），实际新增数可能 < 783 → 如实报告差额 |

---

## 9. 本轮未做的事（明确声明）

- ❌ 未修改仓库侧任何文件（除本清单自身）
- ❌ 未修改生产侧任何文件
- ❌ 未读取/未写生产 `state/kb.db`（本轮零 SQL 连接）
- ❌ 未执行 T2 任何动作
- ⏳ 783 / 570 两个数字**未在本轮重新实测**（按指令采信已取证事实）
