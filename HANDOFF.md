# kb-from-zero · 交接文档

> 换 AI / 换电脑能接手的关键。八段结构沿用《版本管理与备份规范》§四。
> 「已踩过的坑」**只追加，不删旧条目**。

## 1. 一句话说清这是什么

一个**去隐私化的开源知识库搭建体系**（方法论 + 门户 + 检索引擎 + 知识图谱 + 中英双语文档），
许可 KBL-1.0，署名 FallenPetal；用 Git 管版本，用 CI 管验收。

## 2. 当前状态（2026-11-10）

- 最新 tag `v1.17.1`（`4636fea`，图谱闸门口径订正），根 `VERSION` = `1.17.1`，工作区干净。
- 本轮新增「版本管理上云」能力：**已本地落地、未 push、未发 Release、未建远程仓库**。
- `release.yml` 与 `version-check.yml` 尚未在真实 GitHub Actions 上跑过（需 push 才触发）。
- 人验签字归库主，本轮只交人验包，不收口。

## 3. 技术栈与关键版本

| 项 | 值 |
|---|---|
| 语言 | Python 3.10+（门户只用标准库）、少量 bat、YAML |
| CI | GitHub Actions：`ci.yml`（离线回归 + 隐私终审）、`release.yml`（tag 发版）、`version-check.yml`（版本一致性） |
| 许可 | KBL-1.0（不可商用 / 强制署名 / 相同方式共享） |
| 仓库版本 | 根 `VERSION`（唯一真相源） |
| 子系统版本 | 检索引擎 `modules/知识库索引/VERSION` = `1.16.0`；图谱子系统 v1.0.1；门户 v1.4.0 |
| 外部依赖 | 检索引擎最小依赖 jieba + numpy；torch/transformers 函数内懒加载，CI 不装 |

## 4. 目录结构速览

```
VERSION  CHANGELOG.md  CONTRIBUTING.md  HANDOFF.md   ← 版本管理四件套（根级）
README.md / README.en.md / RELEASE.md / RELEASE.en.md
LICENSE / NOTICE / COMMERCIAL.md                    ← 许可与署名
docs/                                               ← 中英双语文档 + decisions/(ADR)
modules/知识库索引/                                  ← 三链路检索引擎（自带 CHANGELOG/VERSION）
modules/知识图谱/                                    ← 图谱子系统
kb-hub/                                             ← 本地门户（标准库 HTTP 服务）
tools/                                              ← privacy_scan / make_release_notes / release_gate 等
.github/workflows/                                  ← ci / release / version-check
templates/                                          ← 给用户复制的模板
```

## 5. 怎么跑起来

```
python tools/release_gate.py --check          # 版本一致性（rc=0 才继续）
python tools/release_gate.py --preflight      # 上云预检（隐私 + 待推体积，只打印）
python tools/privacy_scan.py --git-only       # 发布前隐私终审，0 BLOCK
python tools/make_release_notes.py --version 1.17.1 --changelog CHANGELOG.md --check
```

检索引擎离线回归：`cd modules/知识库索引 && python run_tests.py --ci`（不写看板、不污染仓库）。
门户：`cd kb-hub && python kb_hub_server.py`，浏览器开 `http://127.0.0.1:8799/`。

## 6. 已踩过的坑（🔴 最重要，只追加）

1. **CI 装 torch 会把套件拖到 10 分钟以上**，大家开始习惯 `--no-verify` → 重依赖全部函数内懒
   import，CI 只装 jieba + numpy。
2. **CI 绿 ≠ 可以发布**：隐私词表按红线不得入库，CI 只能跑 L1+L3；发布前必须本机带词表再跑一次。
3. **RRF 的 k=60 是网上照抄的错值**（小语料实测 k=2，Recall@5 82.0% → 89.0%）；
   精排层「加了反而更差」的完整归因见 `docs/检索层设计与评测.md`。
4. **Release 正文空着比不发版更伤** → `release.yml` 故意设计成「CHANGELOG 找不到版本段就 fail」。
5. **🔴 打了 tag 却没写版本日志 = 发版必失败**（2026-11-10 实证）：tag 已到 `v1.17.1`，
   但 `modules/知识库索引/CHANGELOG.md` 最高只到 `v1.16.0`，推 tag 会触发门禁主动 fail。
   修法是把 Release 正文来源切到**根 CHANGELOG.md**，并让根 CHANGELOG 顶段版本与 tag 强制一致；
   **不要**在子系统日志里伪造仓库级版本段。
6. **版本号写死在发布脚本里会烂掉**：`publish_to_github.bat` 曾写死 `TAG=v1.3.1`，
   比实际版本落后三代。现在从根 `VERSION` 读取，并由 `--check` 把五处版本绑在一起校验。
7. **文件名差一个字母吃掉几十 GB**：`envs` 写成 `env` 会让快照误收 22 GB 虚拟环境；
   排除表要写全（见生产库 `snapshot.py` 的 `EXCLUDE_DIRS` 注释）。
8. **CI 的 `--category` 过滤行为曾在并行窗口被改动**，导致基线 2 条 FAIL；
   合并前应跑备份对照，别把在途改动误判成自己的回归。
9. **W9 · 双库副本会静默缺依赖**：`w9_human_label_pack.py` 在生产侧 `import judge_calibration`
   正常，复制到本仓库后自测直接 `ModuleNotFoundError`（本仓库没有该模块）。
   修法不是删 import，而是「优先 import 原版常量 + 镜像常量降级 + 自测做逐位一致性校验」，
   保证两侧阈值不漂移。🔴 每次 `cp -r` 副本后，务必在**目标仓库**重跑一次全套 `--self-test`。
10. **W9 · `privacy_scan.py --git-only` 扫不到未追踪的新文件**：CI 口径 0 BLOCK 只能证明
    「已入库文件干净」，新加的 workflow / 脚本 / 文档根本没被扫到。本轮补跑全量
    `--root . --no-assets` 才抓出新脚本里 4 处真实姓名（自定义词表 BLOCK）。
    ⇒ 发版前必须**两份都跑**：全量看新增文件、`--git-only` 看真正会推的内容。
11. **W9 · 全量扫描的既有 BLOCK 不等于本轮引入**：全量 49 条 BLOCK 全部落在
    `modules/知识库索引/state/*.json`（本机运行时产物，含绝对路径），
    `git ls-files` 显示该目录追踪文件数 = 0 ⇒ 不会外发。别为了「把数字压到 0」
    去动别人的运行时产物（减法先过目），如实分口径记账即可。
12. **W9 · Python `%` 格式化模板里的 CSS 裸 `%` 会炸**：`width:100%` 被当成格式符，
    报 `ValueError: unsupported format character ';'`。HTML 模板用 `%` 插值时，
    CSS 里的 `%` 必须写成 `%%`。
13. **W9 · 编排器别为了绿放宽判据**：CI 侧没有生产 `ragas_metrics.json` 时
    `eval_deepeval_gate.py` 正确返回 rc=4（输入缺失就该拦）。修法是编排器在**发起调用前**
    判定「不具备条件 ⇒ SKIP」，而不是把脚本的 rc=4 改成 0 或吞掉 FAIL。

9. **LlamaIndex 0.14 的 `BaseRetriever` 不是 pydantic 模型**：它自定义 `__init__`
   （callback_manager / object_map / objects / verbose），用 `Field` 声明 `db_path` 会
   `TypeError: unexpected keyword argument`。照 0.13 的印象写必翻车，真跑一次才暴露。
10. **sqlite3 跨线程：只加串行锁不够**。`Ctx._conn` 是全局单连接，FastAPI 同步端点跑在
   anyio 线程池 → `sqlite3.ProgrammingError: objects created in a thread can only be used in
   that same thread`（实测 8 并发 24 请求 12~13 个 500；约一半请求恰好落在创建线程上才成功，
   所以「有时能跑」最骗人）。正解两条同时满足：`connect(check_same_thread=False)`
   **且** `_LOCK` 串行访问；用子类覆盖 `_ensure`，原版 `mcp_server.py` 一字不动。修复后 24/24 / 0 错误。
11. **`file://` 双击的单文件 UI 必须开 CORS**（Origin 是 `null`）；服务只绑 `127.0.0.1` 时
   `allow_origins=["*"]` 的暴露面仍限本机。不开＝「双击即用」直接废掉，curl 却测不出来。
12. **隐私标记常量别手抄**：出口门禁要比对的是 `kb_rag.PRIVACY_EXPORT_MARK`
   （`🔒【隐私内容·本机可检索，禁止外传/引用原文】`）。手写另一个字符串＝门禁静默失效，
   典型的「目检幻觉」；一律从单一真相源读。
13. **`privacy_scan.py` L3 把 `.png` / `.svg` 判 BLOCK**：架构图既要出图又不能让门禁变红，
   走双轨——本地 matplotlib 生成 PNG（`.gitignore` 排除）+ README 用同结构 Mermaid 文本图对外。
   **别为了让图过门禁去改安全护栏。**
14. **`kw` 路是 OR 语义**：查不存在的词不会返回 0 条，而是 `coverage=0` 的擦边结果 + note 警示
   （库主「库里有的不许说没有」）。别为了「0 条好看」去过滤；真 0 条（`substr` + 生僻串）才给 hint。
15. **`import kb_rag` 只拉 jieba**：torch / transformers 都是函数内延迟加载。干净 venv 复现只需
   `jieba + llama-index-core + fastapi + uvicorn`；漏 jieba 会**静默降级 bigram**，检索质量崩且不报错。
16. **Git Bash 的 `/tmp` 与 Windows 版 Python 不互通**：`tee /tmp/x.json` 之后 Windows Python
   读 `/tmp/x.json` 会 `FileNotFoundError`；取证文件统一落 F/E 盘的显式路径。
17. **公开评测集弱标注满分 ≠ 引擎强**：query 取自 gold 文档标题时 R@5 / MRR 必然 1.000，
   只证明链路通。已在 `examples/EVAL.md` 写死「不得用于简历数字 / 选型」，并解释原因——
   诚实比一张漂亮的表重要。

## 7. 当前进行到哪 / 下一步做什么

- ✅ 已完成：根版本四件套、`release_gate.py`、CI 版本门禁、`release.yml` 正文来源修复、
  `publish_to_github.bat` 的 TAG 修复、ADR 设计总稿。
- ⏳ 待库主确认：是否授权 `git push` / 推 tag / 发 Release（本轮**零对外动作**）。
- ⏳ 待人验：按 `CONTRIBUTING.md` 六步跑一遍并复核 diff。
- 下一步（可选，别提前做）：确认要自动化后，加 `release-please` 的 `simple` 型 workflow；
  真有云端大容量需求时再评估 Git LFS / DVC。

## 8. 关键决策记录

- `docs/decisions/ADR-2026-11-10-版本管理上云.md`：五方案对比 + 五何 + 边界 + 回退（**本轮总稿**）。
- `docs/decisions/KG-41.md`、`docs/decisions/RW-39.md`：图谱与检索层既有决策。
- 决策解释义务：每个非平凡动作都要说清「为何做 / 为何这样 / 边界 / 怎么验 / 何时停」；
  写不出理由的决策大概率没想过。
