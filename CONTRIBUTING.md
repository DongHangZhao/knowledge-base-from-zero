# 贡献与发版规范

**简体中文** ｜ 许可 KBL-1.0（不可商用 · 强制署名 · 相同方式共享，署名 FallenPetal）

本仓库是 **Docs-as-Code**：Markdown 与源码都在 Git 里，靠 commit / tag / CI 管版本，
不依赖任何 wiki 服务、不需要 Node 工具链、不启用 Git LFS。

---

## 一、提交信息（Conventional Commits 1.0.0）

格式：`<type>(<scope>): <一句话说明>`，`scope` 可省略。

| type | 含义 | SemVer |
|---|---|---|
| `feat` | 新功能 | minor |
| `fix` | 修 bug | patch |
| `feat!` / `BREAKING CHANGE:` | 破坏性变更 | major |
| `docs` | 文档 | 通常不 bump |
| `refactor` / `test` / `chore` / `ci` / `build` | 内部改动 | 通常不 bump |

例：
```
feat(图谱): 增加 PPR 图检索
fix(release): 修正 Release 正文来源为根 CHANGELOG
docs: 补版本管理上云 ADR
```

🔴 **不要复制 `git log` 当 CHANGELOG**：正文要写「对用户有什么影响」+「为什么这么做」，
不是把一堆 commit 标题粘上去（业界反模式 CL-01 / CL-07）。

---

## 二、版本真相源（只此一处）

| 项 | 位置 | 规则 |
|---|---|---|
| 版本号 | 根 `VERSION` | **唯一真相源**，纯 `x.y.z` 一行 |
| 变更日志 | 根 `CHANGELOG.md` | Keep a Changelog；每段**必填「决策理由」** |
| 交接与坑 | 根 `HANDOFF.md` | 「已踩过的坑」只追加，不删旧条目 |
| 发布说明 | `RELEASE.md` / `RELEASE.en.md` | 头部版本必须与 `VERSION` 一致 |
| 一键发布器 | `publish_to_github.bat` | TAG 从 `VERSION` 读，**不许写死** |
| 子系统版本 | `modules/知识库索引/VERSION` 等 | 只管子模块自身，不冒充仓库版本 |

**层级不要混**：根 CHANGELOG 记仓库级里程碑，子系统 CHANGELOG 记细粒度改动。
`release.yml` 的 Release 正文从**根 CHANGELOG**抽取（`make_release_notes.py --changelog CHANGELOG.md`）。

---

## 三、发版 SOP（六步，每步都要有取证）

```
# 1) 一致性体检（VERSION == 根 CHANGELOG 顶段 == 最新 tag == RELEASE.md == bat 的 TAG）
python tools/release_gate.py --check

# 2) 上云预检（隐私 0 BLOCK + 待推文件数/体积/提交数；只打印，不执行 push）
python tools/release_gate.py --preflight

# 3) 生成新版本（默认 dry-run，只打印会改什么）
python tools/release_gate.py --bump patch --note "一句话说明"

# 4) 确认无误后才真写盘
python tools/release_gate.py --bump patch --note "一句话说明" --apply

# 5) 手工补全根 CHANGELOG 新段的「修改 / 修复 / 决策理由 / 已知问题」，
#    更新 HANDOFF.md 的第 2、6、7 节，再跑一次 --check
python tools/release_gate.py --check

# 6) 人验签字后，由库主单独授权才执行 push / 打 tag / 发 Release：
#    git push origin master
#    git push origin v<版本>        # 推 tag 会触发 release.yml 自动建 Release
```

🔴 **脚本本身永不 push、永不打远程 tag、永不建 Release**。`publish_to_github.bat` 是库主
**手工双击**的一键发布器，运行它才代表授权；`release_gate.py` 只做门禁与打印。

🔴 **人验三态不许冒充**：`⏳ 待人验`（有包无签字）／`✅ 确认收口`（回 ok）／
`✅ 人已验签字`（逐行复核 + 自跑 rc + 署名落盘）。AI 不代签，一句 `ok` 不算人已验。

---

## 四、三条红线

1. **隐私不上云**：`raw/个人信息/`、`raw/隐私/`、密钥、真实业务原始数据绝不进本仓库；
   真实姓名 / 学号 / 公司名 / 真实项目名 / 本机绝对路径一律不写。
   发布前必须在本机跑 `python tools/privacy_scan.py --git-only`（词表放仓库外），**0 BLOCK 才放行**。
   CI 里的隐私扫描只有 L1+L3（词表按红线不得入库），**CI 绿 ≠ 可以发布**。
2. **减法先过目**：删除/合并/下线先列完整清单等许可；「AI 生成的 / 看着过期」不构成豁免。
3. **复用 = 复制副本，绝不改原版**：`modules/` 下脚本可能挂着定时任务，改之前先复制副本改参数。

---

## 五、双库边界（别混）

| | 生产库（本地） | 本仓库（GitHub） |
|---|---|---|
| 版本管理 | `snapshot.py`（VERSION + CHANGELOG + HANDOFF + `_versions/` 磁盘快照，L1/L2/L3） | `release_gate.py`（Git 即历史，**不复制全量快照**） |
| 远程 | **无 remote，只本地提交，不推** | `origin` 已配置，push 需单独授权 |
| 重资产 | 19 GB 级向量库 / 模型 / 媒体留在本地 | **一律不入库**，也不需要 LFS |
| 内容 | 真实业务资料（含隐私） | 去隐私化的方法论 / 示例 / 通用代码 |

两套体系**各自独立**，不互相替代、不双向同步版本号。

---

## 六、还没做 / 以后再说（诚实标注）

- `release-please` Action：只吸收规范，暂不引入自动开 Release PR 的对外动作。
- Git LFS / DVC：无云端大容量需求，暂不启用。
- 文档站点化（Docusaurus / MkDocs）：现有 `docs/` 已是 Git-first，换平台＝重构，暂不做。
