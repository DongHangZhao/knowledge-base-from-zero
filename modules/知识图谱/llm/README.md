# LLM 能力层（默认 off）

> 口径：`AGENTS.md` §5.11 + 准则 §39 **v3.65「能力必建 · 开关可控」**。
> 建是义务，开是选择：**代码必须存在且验过能跑**，交付状态默认关闭。

## 是什么

W6 图谱子系统的 LLM 增强层，五件套齐全，可 `cp -r` 到其它模块复用：

| 文件 | 职责 | 关键约束 |
|---|---|---|
| `llm_kit.py` | client / redact / cache / trace / 降级 | 纯标准库；OpenAI 兼容；主动限速 + 尊重 `Retry-After` |
| `enhance.py` | 任务层：关系类型判定、查询改写 | 先规则基线，LLM 只做「在基线上加值」 |
| `b3_review.py` | 接到 W6 B3 边：对规则抽出的 typed 边做质检 | 抽样跑，输出供人审，不自动删边 |
| `cli.py` | 状态 / 离线自测 / 单条调用 / A/B 对照 | `--selftest` 不联网也能全过 |

## 开关与凭据

```bash
# 凭据只走库外环境变量，本目录任何文件都不存 key
setx AGNES_API_KEY "***"          # 或在当前 shell export
setx KG_LLM_ENABLED 1             # 默认 0 = 关闭
# 可选：KG_LLM_BASE_URL / KG_LLM_MODEL / KG_LLM_TIMEOUT / KG_LLM_RETRIES / KG_LLM_MIN_INTERVAL
```

`KG_LLM_ENABLED != 1` 或没有 key → **不发任何网络请求**，直接返回 `degraded=True` 的本地结果。
即使开关关闭，已缓存过的输入仍可重放（先查缓存再判开关），保证「关掉也能跑、结果可复现」。

## 命令

```bash
PY="E:/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
cd modules/知识图谱/llm

"$PY" cli.py --status                       # 开关 / 端点 / key 是否就绪（打码）
"$PY" cli.py --selftest                     # 离线自测：门禁 3 拦 + 软替换 + 规则基线 + 缓存键 + 降级
"$PY" cli.py --task relation --head A --phrase 用于 --tail B
"$PY" cli.py --task rewrite --query "五大系统怎么部署"
KG_LLM_ENABLED=1 "$PY" cli.py --ab          # 规则 vs LLM 对照，写 state/llm_ab_*.json
KG_LLM_ENABLED=1 "$PY" b3_review.py --limit 12   # B3 边质检，写 state/b3_llm_review_*.json
```

## 出口门禁（硬拦 / 软替换）

- **硬拦（命中即拒发，一个字节都不出去）**：`raw/个人信息|隐私` 路径、身份证、手机号、`sk-` 形态密钥、32 位以上十六进制、`住院号/病历号/患者姓名` 等业务主键字段。
- **软替换（脱定位信息保语义）**：本地/网络路径、邮箱、内网 IP。
- 宁可误拦不可漏发（准则 §37.16 判据）。被拦的调用在 trace 里记 `export_blocked`，不写正文。

## 实测（2026-11-09，`agnes-3.0-flash`）

| 实验 | 结果 | 证据 |
|---|---|---|
| 离线自测 | 8/8 通过（含 3 类硬拦、2 类软替换、默认 off 降级） | `cli.py --selftest` rc=0 |
| 关系判定 A/B（n=10，难题集，gold 人工判定） | 规则 **0.3** → LLM **0.7**（+40pp），0 降级 | `state/llm_ab_20261109_133003.json` |
| B3 边质检（抽样 12/41） | 12/12 调用成功，**11 条判为无效碎片边**（如 `也不该 --构成-> AI`、`32 --构成-> 低置信降级查询`） | `state/b3_llm_review_20261109_133309.json` |
| 关掉开关重放 | 同一输入 5s 跑完，结论与开启时**逐条一致**（全命中缓存） | 同目录两次输出 diff 为空 |
| 提示词迭代 | 首版 0.6 / 2 降级 → 补否定与因果规则后 0.7 / 0 降级 | 两版 `llm_ab_*.json` |

## 已知限制（如实记）

1. **n=10 不作统计断言**：只证明链路与方向，显著性要 150+ 条人标题库（业界 ARES/PPI 量级）。
2. **共享端点有 429**：8 连发即触发；已加主动限速（默认 2.5s）+ `Retry-After` 退避。批量任务请调大 `KG_LLM_MIN_INTERVAL`。
3. **B3 质检只抽样 12/41**：全量跑要 ~3 分钟，尚未做；结论「规则版 typed 边质量差」已有足够样本支撑，但要全量清单得单独跑。
4. **不自动改图**：LLM 判定只落 JSON 供人审，`kg_w6.pkl` 不动（判据 7：评测/标注不由模型自证）。
5. **缓存目录在 `state/`**：已 gitignore，不进版本库、不上传。

## 复用

```bash
cp -r modules/知识图谱/llm <目标模块>/llm
# 只改 enhance.py 的任务提示词与规则基线，llm_kit.py 不动
```

`llm_kit.py` 不含任何 W6 专有逻辑，任何「需要调 LLM 但必须可关可复现」的模块都能直接拿去用。
