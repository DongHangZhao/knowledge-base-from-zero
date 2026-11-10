# Quickstart · 5 条命令跑通一个 RAG demo

> 从 `git clone` 到第一条**带引用路径**的检索结果。全程只用仓库内的**已公开脱敏语料**：
> 不连生产库、不下载任何模型、不需要 API Key、不联网（装包除外）。

## 五条命令

Windows PowerShell（macOS / Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`）：

```bat
git clone https://github.com/DongHangZhao/knowledge-base-from-zero.git && cd knowledge-base-from-zero
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
.venv\Scripts\python.exe examples/build_demo_index.py
.venv\Scripts\python.exe examples/demo_llamaindex.py "RRF 融合怎么做"
```

第 4 条打印 `docs=<条数> chunks=<块数> ingested=<条数>`，第 5 条打印 3 条命中及其 `引用：<仓库相对路径>`。
**条数以实跑输出为准**：白名单扫的是仓库当前公开 Markdown，仓库加减文档就会变
（本轮 2026-11-10 夜里两次快照分别是 40/538 与 41/560，差异来自并行窗口新落地的一篇文档）——
别把快照当常量，也别为了凑数去注水。
每条命令跑完看退出码（PowerShell `echo $LASTEXITCODE` / bash `echo $?`），**非 0 就停**，别往下跑。

### 脚本四条怎么落地的

| 规矩 | 本仓库做法 |
|---|---|
| 解释器绝对路径 | 文档命令用 `.venv` 内的解释器（可移植）；脚本运行时由 `sys.executable` 自解析，等价于绝对路径。**本机绝对路径属隐私扫描 BLOCK 项，公开文档一律不写** |
| UTF-8 + 查 returncode | 每个 `.py` 头部 `# -*- coding: utf-8 -*-`，服务层 `sys.stdout.reconfigure(encoding="utf-8")`；`build_demo_index.py` 用 `sys.exit(main())` 返回 0/1 |
| 幂等 + 断点续传 | `upsert_doc` 按 `path` 覆盖，重复跑不会重复插入；语料库是派生产物，可反复重建 |
| 单条失败不中断 | 读文件 / 写库各自 try-except，打印 `SKIP`/`FAIL` 后继续，最后报成功条数 |

## 可选：REST + 单文件 Web UI（不计入 5 条）

```bat
.venv\Scripts\python.exe modules\知识库索引\api_server.py
```

浏览器打开 `http://127.0.0.1:8788/`；也可以**直接双击** `examples/index.html`（纯静态，CORS 已开）。
服务只绑 `127.0.0.1`，不上云、不对外发布。三个接口：`/health` `/search` `/stats`，另有 `/ask`（默认 off）。

## 可选：LLM 开关 A/B（零凭据、零联网）

```bat
.venv\Scripts\python.exe examples\mock_llm_server.py
```

另开一个终端，带开关启动 REST：

```bat
set KB_LLM_ENABLED=1 && set KB_LLM_BASE_URL=http://127.0.0.1:8789/v1 && .venv\Scripts\python.exe modules\知识库索引\api_server.py
```

访问 `/ask?q=RRF` 两次：首次 `cache_hit=false`（真发请求），第二次 `cache_hit=true`（零重复调用）。
**关掉开关后系统照常跑**；真实云端只接受库外环境变量里的 Key，隐私路径在出口门禁被 451 拦下。
mock 输出**不是**评测结论，只能用来验链路。

## 可选：公开评测

```bat
.venv\Scripts\python.exe examples/eval_public.py
```

生成 `examples/EVAL.md`（R@5 / MRR / p95 / 覆盖率）。口径与边界见该文件头部。

## 语料口径（不注水）

- demo 语料 = 本仓库**自身已公开**的 Markdown（根说明页 + `docs/` + `templates/` + 两个模块 README），
  由 `build_demo_index.py` 白名单扫描后切成块，落 `examples/state/demo.db`（运行期产物，已忽略，不入库）。
  条数 / 块数**以第 4 条命令的输出为准**；`examples/EVAL.md` 头部会记录生成时的实测值。
- **不是 10,000 docs，也不假装是**。招聘方常看「万级文档 + 公开评测集」；这里给的是**可复现的最小闭环**。
  生产侧索引库含隐私正文，按红线**绝不外传、不进 git**，其规模数字属会失效的内部快照，不写进公开文档。
- 默认走 `kw`（FTS5 关键词）路：零模型依赖，clone 下来就能复现。
  UI 里的 `fused` / `vec` 需要额外配 BGE 模型；`/stats` 里 `chunk_bge=0` 是这条取舍的如实值，不是覆盖率事故。

## 边界

- 🔴 **适配器仅为互操作/演示，生产检索不走框架**（为什么自研见根 README「RAG Engine」一节）。
- 🔴 不替换生产、不改 `mcp_server.py` 原版、不推生产 `kb.db` / 真实项目名 / 患者与个人数据。
- 🔴 开源/对外一律 **KBL-1.0**，署名 **FallenPetal**；推送前 `python tools/privacy_scan.py --git-only` 必须 0 BLOCK。
- 许可：KBL-1.0（源码可见 · 非商用 · 强制署名 · 相同方式共享），见根 `LICENSE`。
