# 发布与安装说明（Releases）

**简体中文** ｜ [English](RELEASE.en.md)

当前版本：**v1.4.0**（2026-10-26）
许可：**KBL-1.0** —— 可免费用、随便改，**不可商用**，转载须署名并标注出处。
详见 [`LICENSE`](LICENSE) / [`LICENSE.zh-CN.md`](LICENSE.zh-CN.md) / [`NOTICE`](NOTICE)。

本仓库在 **Releases** 里提供两种下载方式，二选一即可。
**全部文档中英双语**：每篇正文顶部有「简体中文 ｜ English」切换，两版内容一一对应。

## 本次更新（v1.4.0）

新增文档 **《检索层设计与评测方法》** —— [`docs/检索层设计与评测.md`](docs/检索层设计与评测.md) / [`docs/retrieval-engine.en.md`](docs/retrieval-engine.en.md)，中英双语同步发布。这是本仓库第一篇专门回答「检索层怎么设计、又怎么证明它对」的文档：

- **三链路检索**：FTS5 关键词 + 稠密向量 + 字级 trigram 保底，讲清为什么一路不够；
- **RRF k 参数实测调优**：网上普遍照抄的 `k=60` 在小语料上是错的，实测从 60 调到 2（Recall@5 82.0% → 89.0%）；
- **精排「加了反而更差」的完整归因**：加一层 Cross-Encoder 后 Recall@5 从 88.0% 掉到 76.0%，根因不是模型而是喂了摘要；修复有效（降级条数 32 → 1）但仍不划算，最终代码保留、默认关闭；
- **100 条人工标注集的评测方法**：三个指标怎么读、A/B 怎么做、为什么必须先做零假设检验、为什么要给每次评测留指纹；
- **工程化实践**：并发写锁（真事故换来的）、备份健康闸门、一条命令跑完的回归测试套件；
- **精选踩坑**：中文标题从未进过索引、trigram 对 2 字查询失效、注释与代码不一致、文件名差一个字母吃掉几十 GB。
- **使用指南**：同步新增《使用指南》（[`docs/使用指南.md`](docs/使用指南.md) / [`docs/usage.en.md`](docs/usage.en.md)），专讲拿到仓库后怎么真正用起来——门户怎么开门户、怎么扫出你自己的库、怎么让 AI 接手你的知识。

> 所有数字都来自真实运行（4,152 篇文档 / 44,479 个切片），每个关键结论后都标注了「怎么验证的」。

## 资产说明

| 资产 | 文件名 | 适合谁 | 说明 |
|---|---|---|---|
| 源码压缩包 | `kb-from-zero.zip` | 想看全部源码 / 模板的人 | 整个仓库的快照（含中英双语文档），解压即用 |
| 门户可执行文件 | `kb-hub.exe` | 只想用门户、不想装 Python 的人 | 零依赖单文件，双击即开 `http://127.0.0.1:8799/` |

> 两份资产都附本说明文件（`RELEASE.md` / `RELEASE.en.md`）与仓库首页 `README.md` / `README.en.md` 的链接。

> **拿到之后怎么用？** 看 [`docs/使用指南.md`](docs/使用指南.md)（中文）/ [`docs/usage.en.md`](docs/usage.en.md)（英文）：门户怎么开门户、怎么扫出你自己的库、怎么让 AI 接手你的知识。

---

## 方式一：用 exe（最简单）

1. 在 Releases 下载 `kb-hub.exe`；
2. 把它放进你的知识库**根目录**（和 `raw/ wiki/ modules/` 同级）；
3. 双击 `kb-hub.exe` → 自动开浏览器门户；
4. 首次打开会看到**示例清单**（页面顶部有黄色提示条），这是正常的——它还没扫描过你的库。

### 让 exe 认你自己的库

exe 默认把**自己所在的目录**当成知识库根。生成真实清单：

```bat
:: 需要一次 Python（3.10+），仅用于跑扫描器
python inventory_builder.py --kb-root "你的知识库路径"
```

把生成的 `inventory.json` 放在 **exe 旁边**，重启 exe 即生效。清单长这样：

```
{ "summary": {...}, "systems": [...], "skills": [...], "entries": [...], ... }
```

> 不想装 Python？也可以用源码 zip 里的 `kb-hub/inventory_builder.py`，或者直接看示例清单的字段说明自己手写一份。

> 首次运行 Windows 可能弹 SmartScreen，点「仍要运行」即可（开源项目没有代码签名证书，不是病毒）。

---

## 方式二：用源码 zip

1. 下载 `kb-from-zero.zip` 并解压；
2. 需要 Python 3.10+（门户只用标准库，无第三方依赖）；
3. 进入 `kb-hub/`，双击 `start-kbhub.bat`（或 `python kb_hub_server.py`）；
4. 浏览器开 `http://127.0.0.1:8799/`。

`start-kbhub.bat` 会自动找 Python、自动顺延端口（8799 → 8808），不需要改脚本里的路径。

---

## 首次使用的三步

1. **读 README 第二节**，建好 `raw/ wiki/ modules/ outputs/` 四个目录；
2. **复制模板**：`templates/AGENTS.example.md` → 你库根目录的 `AGENTS.md`，把 `<你的名字>` 之类占位符改成你自己的；
3. **打开门户**，点「🚪 入口 / 系统模块 / 文件」熟悉一遍，再跑一次扫描器让它认你的库。

---

## 高级：自己重新打包 exe

```bash
pip install pyinstaller
cd kb-hub
pyinstaller --onefile --name kb-hub --noconfirm ^
  --add-data "%CD%/web/index.html;web" ^
  --add-data "%CD%/inventory.example.json;." ^
  --distpath .. ^
  kb_hub_server.py
# 产物：仓库根目录的 kb-hub.exe
```

两个必须注意的点：

- `--add-data` **要用绝对路径**：相对路径会按 spec 文件所在目录解析，容易打进去空目录；
- 不要把 `inventory.json`（真实清单）打进 exe：它含本机绝对路径与真实模块名。
  仓库只打包 `inventory.example.json`，真实清单由用户在 exe 同级放置。

---

## 发布前自检（作者用）

```bash
python tools/make_example_inventory.py      # 重新生成纯虚构示例清单
python tools/privacy_scan.py                # 隐私终审：BLOCK > 0 就不能发
```

`privacy_scan.py` 的规则：
- **通用规则**：绝对路径、用户名、手机号、邮箱、身份证、各类密钥、私有网段、媒体文件；
- **自定义词表**：真实姓名 / 公司 / 客户 / 项目名，词表放 `~/.kb_privacy_terms.txt`，**不进仓库**；
- **二进制**：exe 按 UTF-8/GBK 解码后扫内嵌字符串（PyInstaller 会压缩资源，明文扫可能漏 → 因此**必须从干净源重建**，不能只靠扫描）。

一键发布脚本 `publish_to_github.bat` 已内置这道关卡：扫描不通过会直接中止。

---

## 隐私声明

Releases 与仓库**均不含**任何个人隐私数据：

- ❌ 真实姓名 / 学号 / 联系方式 / 账号密钥 / 聊天记录；
- ❌ 真实项目名 / 客户名 / 公司名 / 工作经历；
- ❌ 图片 / 音视频 / PDF / Office 文档（`tools/privacy_scan.py` 会拦）；
- ❌ 真实知识库文件清单（`inventory.json` 已列入 `.gitignore`）。

仓库里的 `kb-hub/inventory.example.json` 是**纯虚构**数据（6 个示例模块 / 8 张示例卡片 / 3 个示例项目），
路径统一写成 `~/my-knowledge-base`，且 `exists` 全为 `false`——不会让你误以为能点开。

---

## 版本记录

| 版本 | 日期 | 变化 |
|---|---|---|
| v1.4.0 | 2026-10-26 | 新增 **《检索层设计与评测方法》** 文档（中英双语）：三链路召回设计、RRF k 实测调优（60 → 2）、精排「加了反而更差」的完整归因与修复（降级 32 → 1，代码保留、默认关闭）、100 条标注集的评测方法与 A/B 纪律、并发写锁 / 备份闸门 / 回归测试等工程化实践、精选踩坑；README 文档索引同步 |
| v1.3.1 | 2026-10-22 | 检索页签新增 **rerank 精排层**（bge-reranker-v2-m3 本地 cross-encoder，Hybrid → RRF → rerank → top-k 2026 生产级金标准架构）：结果卡片显示精排分，短查询（≤4 字）自动跳过保字面灵敏度，全池低分直接判「库内未检索到相关内容」；零命中/擦边查询在页面显示 ⚠️ 警示条（精排阈值 + 查询词覆盖率双触发）；「问知识库」展示警示与引用表；门户 exe 重建 |
| v1.3.0 | 2026-10-21 | 门户新增 **🔍 全库检索**页签（三链路 RAG：FTS5 关键词 + BGE 语义向量 + 字级 substring；「问知识库」组装带引用的上下文包），后端新增 `/api/rag`（配置驱动，未配置时优雅降级）；**界面中英一键切换**（全部 UI 文案进 I18N 字典，localStorage 持久化，selftest 增加语言断言）；页签 9 → 10 |
| v1.2.0 | 2026-10-21 | **全量中英双语**：README、RELEASE、NOTICE、9 篇 docs、6 份 templates、门户 README 全部补齐英文对照版，每篇顶部加「简体中文 ｜ English」互链；新增 `tools/add_lang_switch.py`（批量插切换行）与 `tools/check_bilingual.py`（成对与双向切换校验）；隐私词表补「名字片段」规则，据此抓掉 4 处残留（服务端 2 处注释、技能工作台 1 处、任务清单真实规模 1 处） |
| v1.1.0 | 2026-10-21 | 许可由 MIT 改为 **KBL-1.0**（禁商用 + 强制署名 + 相同方式共享）；全量脱敏（示例清单重写、真实清单移出仓库）；新增 `docs/双链路体系.md` + 最小可运行实现；新增 `tools/privacy_scan.py` 与发布前门禁；门户总览加「四区 + 双链路」架构图与示例模式提示 |
| v1.0.0 | 2026-10-21 | 首个版本：门户 9 页签 + 模板 + 文档 |
