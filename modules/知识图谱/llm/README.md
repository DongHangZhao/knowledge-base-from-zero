# LLM 能力层（默认 off · 🔴 只服务离线 / 批处理）

> 口径：`AGENTS.md` §5.11（能力必建 · 开关可控，含第 9 条「离线边界」）+ 准则 §39 / §39.4b（版本看准则文件首行，别写死）+ W12 卡。
> 建是义务，开是选择：**代码必须存在且验过能跑**，交付状态默认关闭。
>
> 🔴 **边界（2026-11-10 立，写进 §5.11 执行判据）**：LLM **只服务离线 / 批处理，不进在线检索链路**。
> `kb_rag.py` / `kb_index.py` / 检索服务零 `import llm_kit`，由 `llm_path_guard.py` 守着并接入 W6 交付闸门 C7。
> 在线调用（若将来立项）必须异步 + 缓存；实测单次约 3.3s，**不许进同步链路**；
> 要接主链路需增量 p50 ≤50ms 并单独立项。常开之所以安全，是因为在线链路压根不 import 它（0 在线延时）。

## 是什么

W6 图谱子系统的 LLM 增强层，五件套齐全，可 `cp -r` 到其它模块复用；W12 又补齐「边界 / 审计 / A/B / 成本 / 批处理」五类工程件。

| 文件 | 职责 | 关键约束 |
|---|---|---|
| `llm_kit.py` | client / redact / cache / trace / 降级 | 纯标准库；OpenAI 兼容；限速 + `Retry-After`；可选 `prompt_version` 缓存键 |
| `enhance.py` | 任务层：关系类型判定、查询改写 | 先规则基线，LLM 只做「在基线上加值」 |
| `b3_review.py` | W6 B3 typed 边质检 | 只出 JSON 供人审，不自动删边 |
| `cli.py` | 状态 / 离线自测 / 单条调用 / A/B | `--selftest` 不联网也能全过（9/9） |
| `judge_review.py` | judge 复核（历史能力） | C4 人标仍归库主，模型不自证 |
| **`llm_path_guard.py`** | **W12 · A** 在线入口零引用守卫 | 只扫在线入口；离线工具复用 `llm_kit` 不算违规 |
| **`b3_audit.py`** | **W12 · B** 41/41 结论写回 + 对照表 | 只加 `llm_valid/conf/reason` 属性，**不改拓扑** |
| **`rewrite_ab.py`** | **W12 · C** 查询改写 A/B | 冻结人标题集、McNemar 精确检验、延迟增量 |
| **`rate_probe.py`** | **W12 · D** 429 / 超时连发压测 | 默认零网络，真跑加 `--run`（消耗配额） |
| **`batch_runner.py`** | **W12 · E** 断点续跑 / 失败队列 / 退避 | 已处理 ID 落盘，kill 后不重复计费 |
| **`report.py`** | **W12 · D/E** 命中率 + 成本延迟报表 | 按 task 出 p50/p90 / 降级率 / tokens，供 W9 接 OTel |

父目录 `../sync_to_kb_from_zero.py` 是 **W12 · F** 的可复跑镜像脚本（替代手工复制）。

## 开关与凭据

```bash
# 凭据只走库外环境变量，本目录任何文件都不存 key
setx AGNES_API_KEY "***"          # 或在当前 shell export
setx KG_LLM_ENABLED 1             # 默认 0 = 关闭；常开仅影响离线批处理
# 可选：KG_LLM_BASE_URL / KG_LLM_MODEL / KG_LLM_TIMEOUT / KG_LLM_RETRIES
#      KG_LLM_MIN_INTERVAL（默认 2.5s 主动限速）
#      KG_LLM_CACHE_TTL（默认 0 = 永不过期，可复现优先；>0 按缓存文件 mtime 判过期）
```

`KG_LLM_ENABLED != 1` 或没有 key → **不发任何网络请求**，直接返回 `degraded=True` 的本地结果。
即使开关关闭，已缓存过的输入仍可重放（先查缓存再判开关），保证「关掉也能跑、结果可复现」。

### 缓存键与提示词版本（W12 · D）

`cache_key(model, system, prompt, prompt_version="")`：**不传版本时拼串与旧版逐位一致**（3 段），
既有缓存与其它窗口（`hyde_cmd.py` / `w9_eval`）的回放不受影响；显式传版本才追加第 4 段。
`enhance.py` / `b3_review.py` 用 `prompt_version_of(模板...)` 把**模板内容本身**做成版本号——
改任何一句提示词，hash 变、键变、旧结果自动失效，**不用手工 drop 缓存**。

## 命令

```bash
PY="<venv 解释器，绝对路径>"          # 检索/构建统一用 venv 版
cd <库根>/modules/知识图谱/llm

"$PY" cli.py --status                       # 开关 / 端点 / key 是否就绪（打码）
"$PY" cli.py --selftest                     # 离线自测 9/9：门禁 / 规则 / 缓存键 / 版本键 / 降级
"$PY" cli.py --task relation --head A --phrase 用于 --tail B
"$PY" cli.py --task rewrite --query "五大系统怎么部署"
KG_LLM_ENABLED=1 "$PY" cli.py --ab          # 关系判定小样本对照
KG_LLM_ENABLED=1 "$PY" b3_review.py --limit 12

# W12 新增
"$PY" llm_path_guard.py                     # 在线入口零引用（rc=0 PASS / 3 BLOCK）
"$PY" llm_path_guard.py --selftest          # 故意插 import 验证能判 BLOCK（临时目录，不碰真文件）
"$PY" b3_audit.py                           # 41/41 审计 + 边属性写回 + 关开关复跑一致
"$PY" rewrite_ab.py --prepare --n 50        # 冻结题集生成改写候选（逐条落盘，可断点）
"$PY" rewrite_ab.py --eval                  # paired R@5 + McNemar p + 延迟增量（默认关精排，两臂同配置）
"$PY" rewrite_ab.py --eval --rerank         # 开精排：索引 13.9 万块时单条 13–31s，且实测会崩（见已知限制 7）
"$PY" report.py                             # 命中率 / 成本报表 → state/llm_cost_report.json
"$PY" rate_probe.py                         # 历史 429 证据（零网络）
"$PY" rate_probe.py --run --burst 6         # 真压测（消耗配额）
"$PY" batch_runner.py --selftest            # 中断→重跑，tokens 不重复增长
"$PY" ../sync_to_kb_from_zero.py --dest "<开源仓>/modules/知识图谱" --check
```

## 出口门禁（硬拦 / 软替换）

- **硬拦（命中即拒发，一个字节都不出去）**：`raw/个人信息|隐私` 路径、身份证、手机号、`sk-` 形态密钥、32 位以上十六进制、`住院号/病历号/患者姓名` 等业务主键字段，外加**结构级实名明细表**（多行 Tab 分隔 + 表头含实名/病案主键）。
- **软替换（脱定位信息保语义）**：本地/网络路径、邮箱、内网 IP。
- 宁可误拦不可漏发（准则 §37.16 判据）。被拦的调用在 trace 里记 `export_blocked`，不写正文。

## 实测（🔴 数字是快照，会失效，别当常量）

| 实验 | 结果 | 证据 |
|---|---|---|
| 离线自测 | **9/9** 通过（3 类硬拦 + 2 类软替换 + 规则基线 + 缓存键 + 版本键 + 默认 off 降级） | `cli.py --selftest` rc=0 |
| 在线入口零引用 | 4 个入口 PASS；故意插 `import llm_kit` / `from llm_kit import` 两种都判 BLOCK | `llm_path_guard.py --selftest` 5/5 |
| 关系判定 A/B（n=10，人标金标，历史） | 规则 **0.3** → LLM **0.7**（+40pp），0 降级 | `state/llm_ab_20261109_133003.json` |
| **B3 边质检（41/41 全量）** | 有效判定 39：**真知识 7 / 碎片 32 / 429 降级 2**（降级率 **4.88%** ≤5%） | `state/b3_llm_review_20261109_170902.json` → `state/b3_llm_audit.json` |
| B3 写回 | 只加 `llm_valid/llm_conf/llm_reason`，前后 `(u,v,kind)` 拓扑签名一致；关开关复跑 rows sha1 一致 | `state/b3_audit_gate.json` 4/4 |
| **429 / 超时压测（2026-11-11 00:32 真跑）** | 6 连发：成功 4 / **超时 2（30s TimeoutError）** / **429 共 0 次**；耗时 73s | `state/llm_rate_probe.json` |
| 缓存与成本报表 | 按 task 出命中率 / p50·p90 / 降级率 / tokens；TTL=0 | `state/llm_cost_report.json` |
| **改写 A/B（2026-11-11 01:34，n=50 冻结人标题面）** | 规则 **0.580** → LLM **0.580**（+0.00pp）；配对 24/16/5/5；McNemar **p=1.0 ⇒ 记平**；LLM 改写 p50 **3.52s** / p90 **23.0s**；快照 `stable=true` | `state/rewrite_ab_20261111_013403.json` |
| 关掉开关重放 | 同一输入不联网，结论逐条一致（先查缓存再判开关） | `b3_audit.py --verify` 子进程 sha1 一致 |

🔴 **别把「8 连发即 429」当常量**：那是 2026-11-09 的经验值；本轮 6 连发先出现的是**超时**而不是 429，
历史 trace 里确有 10 条 429 记录。**不因单次没触发 429 就上调并发**——生产仍保持主动限速 2.5s + 并发 1。

## 已知限制（如实记）

1. **n=10 不作统计断言**：只证明链路与方向，显著性要 150+ 条人标题库（ARES/PPI 量级）。
2. **共享端点会波动**：429 与超时都出现过，批量任务调大 `KG_LLM_MIN_INTERVAL`，别并发。
3. **B3 全量已做完**（旧版「只抽样 12/41、全量未做」已作废）：41/41 有结论，但**去留归人审**——
   本轮只加属性不改拓扑，不自动删边；规则版精度约 17%，提质应走「LLM 抽取 + 规则兜底」而非继续加正则。
4. **不自动改图 / 不自当金标**：LLM 判定只落 JSON 供人审；C4 / 题面 / gt 三处不建（方法论禁止模型自证）。
5. **缓存目录在 `state/`**：已 gitignore，不进版本库、不上传。
6. **改写 A/B 的 p 值**：题集冻结、gt 人工，LLM 改写不得自当裁判；未达 p<0.05 就如实记平，
   **不许拿「收益不明显」砍能力**（§5.11）。本轮结果即 **p=1.0 记平**。
7. **跑评测前先确认索引没在重建**：2026-11-11 01:18 实测——并行窗口把索引从 4.4 万块重建到
   13.9 万块，`load_bge()` 走热重载路径直接段错误（**rc=139**）；开精排重跑又在第 4 条崩（**rc=127**）。
   所以 `rewrite_ab.py` 内置**快照守卫**（跑前跑后比对 `built_at` / `chunks` / `docs`）：
   指纹不一致会写 `rewrite_ab_INVALID_*.json` 并在结论里标「数字不可用」。看到 `INVALID_` 就重跑，别抄里面的数。
   逐条进度落 `state/rewrite_ab_partial.json`（键含指纹 + 检索配置），中断后重跑从断点续，不重复烧钱。

## 复用

```bash
cp -r modules/知识图谱/llm <目标模块>/llm
# 只改 enhance.py 的任务提示词与规则基线，llm_kit.py 不动
```

`llm_kit.py` 不含任何 W6 专有逻辑，任何「需要调 LLM 但必须可关可复现」的模块都能直接拿去用。
🔴 复用 = 复制副本，绝不改原版；本目录 W12 的改动是**卡片授权的能力层自身升级**（改前原版备份在 `state/*.bak-20261110-W12`），不是拿去给别的项目改参数。

## W12 判据 → 产物对照

| 段 | 判据 | 可复跑命令 | 取证 |
|---|---|---|---|
| A | 在线入口零引用、故意插 import 能 BLOCK | `llm_path_guard.py --selftest` | `state/w12_guard_selftest.out` |
| B | 41/41 有结论 / 降级率≤5% / 关开关逐条一致 | `b3_audit.py` | `state/b3_audit_gate.json` |
| C | 成对数据 + p 值 + 延迟增量 | `rewrite_ab.py --prepare` → `--eval` | `state/rewrite_ab_*.json` |
| D | 改提示词旧缓存不命中 / 报表能出 / 429 结论 | `cli.py --selftest`、`report.py`、`rate_probe.py` | `state/llm_cost_report.json` |
| E | 杀进程重跑 tokens 不重复增长 | `batch_runner.py --selftest` | `state/batch_selftest.json` |
| F | 镜像可复跑（替代手工 28 文件） | `../sync_to_kb_from_zero.py --check` | `state/sync_report.json` |
