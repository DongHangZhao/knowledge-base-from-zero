# User Guide: how others use this project

[简体中文](使用指南.md) ｜ **English**

> You have downloaded or cloned this repository. This document is specifically about "what to do once you have it" — it does not repeat the philosophy or the from-zero setup steps from the README.
> If you have not read the README yet, please read [README.en.md](README.en.md) first (it explains "what this is and why it is designed this way").

## 1. What this repository gives you

| What you get | What it is for | Where |
|---|---|---|
| **Knowledge Base Hub (KB Hub)** | A local web dashboard that gathers your systems / skills / projects / resources / tools onto one screen, ranked by daily usage heat | `kb-hub/` |
| **Blank templates** | A ready-to-copy knowledge-base skeleton (AGENTS.md / wiki / modules / prompts) | `templates/` |
| **Methodology docs** | Architecture, dual-pipeline, retrieval-layer design & evaluation, open-source policy, FAQ | `docs/` |
| **Policy-enforcement tools** | Example-inventory builder, privacy scanner, bilingual checker | `tools/` |
| **Install guide** | How to install the exe / zip from Releases | [RELEASE.en.md](RELEASE.en.md) |

> ⚠️ **This open-source repository does NOT ship the RAG retrieval engine itself (`kb_rag.py`)**. It only publishes the methodology of "how to design and evaluate a retrieval layer" ([`docs/retrieval-engine.en.md`](retrieval-engine.en.md)).
> The "🔍 Full-library search" tab in the hub needs an **external** `kb_rag.py` to run (see section 3). It works without it — that tab will just say "retrieval not configured".

## 2. Two ways to get started (pick one)

### Route A: use the exe (zero install, just want the hub)
For: people who just want to gather scattered systems onto one dashboard and do not want to install Python.

1. Download `kb-hub.exe` from [Releases](../../releases);
2. Put it at the **root of your own knowledge base** (alongside `raw/ wiki/ modules/`);
3. Double-click `kb-hub.exe` → it opens the browser to `http://127.0.0.1:8799/`;
4. On first launch you will see **example data** (a yellow banner at the top) — that is normal, it has not scanned your base yet;
5. To see real data, run a "scan" (see section 4).

### Route B: use the source (want to modify, see everything, run the tools)
For: people who want to build their own base on the templates, or study the implementation.

```bash
# 1) Get the source
git clone https://github.com/DongHangZhao/knowledge-base-from-zero.git
#   or download kb-from-zero.zip from Releases and unzip

# 2) Check Python (the hub uses only the standard library, no pip install needed)
python --version        # needs 3.10+

# 3) Start the hub
cd kb-from-zero
python kb-hub/kb_hub_server.py --kb-root "your knowledge base path" --port 8799
#   On Windows you can also just double-click kb-hub/start-kbhub.bat
```

Open `http://127.0.0.1:8799/` in your browser.

## 3. What is inside the hub (the tabs)

| Tab | What it does |
|---|---|
| Overview | Number dashboard + four-zone / dual-pipeline architecture diagram + common tools + recent sessions + file-type distribution |
| 🚪 Entries | Gathers destinations scattered in `modules/`, `.workbuddy/`, `outputs/` onto one screen, each marked "reachable / missing" |
| Systems | One card per `modules/` module: open folder / read README / record a use |
| Skill cards | Each card carries its source-file path; "open card" opens the SKILL.md directly |
| Projects | Project notes and code directories |
| Resources | `wiki/` · `outputs/` · `raw/` zone entries |
| 🧰 Tools | Paste a link to download a video / search local chat history / generate a QR code, all in-page |
| Files | Built-in file browser: breadcrumb, up / refresh, click a directory to enter, click a file to open with the system app |
| 🔍 Full-library search | Three-path fused retrieval (see below) |
| 🔥 Daily favorites | Ranked by usage heat (Top), one click to use |

### How to use "🔍 Full-library search" (optional)
This tab calls the knowledge-base side `kb_rag.py`. The open repo does not have it; you supply your own:

- **Option 1 (recommended, local)**: put a `rag.json` in the `kb-hub/` directory:
  ```json
  {"python": "<interpreter path with jieba/numpy/torch>", "script": "<path to your kb_rag.py>"}
  ```
- **Option 2**: set the environment variables `KB_RAG_PYTHON` / `KB_RAG_SCRIPT`.

When unconfigured, this tab returns a clear message and the other tabs keep working. Hits can be assembled into a cited context package via "ask the knowledge base", ready to feed an AI.

## 4. Make the hub show YOUR base (the key step)

The hub learns what systems / skills / projects you have from `inventory.json`. Without it, it uses the fictional example shipped with the repo. Generate your own:

```bash
python kb-hub/inventory_builder.py --kb-root "your knowledge base path"
```

- Outputs: `inventory.json` (machine-readable) + `知识库体系梳理报告.md` (human-readable — a home inventory of your base);
- Put `inventory.json` next to `kb-hub/` (or next to the exe) and **restart the hub** to apply;
- These two files contain your local absolute paths and real module names; they are excluded by `.gitignore` — **do not commit and do not share them**.

> Every time you add a module / project to your base, re-run the command above and the hub updates.

## 5. Daily usage loop

```
Drop material into your base / add a module
        │
        ▼
python kb-hub/inventory_builder.py --kb-root <base>   ← refresh the dashboard data
        │
        ▼
Double-click kb-hub.exe (or python kb_hub_server.py)   ← open the hub
        │
        ▼
Click to use from "Daily favorites / Systems / Resources";
to find material use "🔍 Full-library search" (needs the engine configured)
```

Treat the hub as the "home screen" of your knowledge base: for today's task, enter from "🔥 Daily favorites"; to find something, enter from "🚪 Entries / Resources / Files".

## 6. Let AI truly take over your knowledge (the core selling point)

The essence of this system is **AGENTS.md = the single source of truth**. Hand it to any AI tool (Codex / Claude Code / Qoder / Trae / Cursor) and the AI immediately understands your:

- Directory architecture (`raw` originals / `wiki` synthesis / `modules` capabilities / `outputs` products, four zones never mixed);
- Hard rules (reuse = copy a副本, never modify the original; ask before uploading; tiered approval for deletions; watermark removal; duty to explain decisions…);
- Red lines (privacy stays off the cloud; open-source is noncommercial and must be attributed…).

How: in the AI tool's session, `@` or paste the full `AGENTS.md` (together with `wiki/编码指南/`). The AI understands on handoff — no need to re-explain the background every time.

## 7. From "my base" to "your base" (customize)

To build your own system:

1. Copy the whole `templates/` directory and rename it to your knowledge-base root;
2. Fill in your own content following the blank `AGENTS.md` / `wiki/README.md` / `modules/示例模块/README.md` inside;
3. Drop your material into `raw/` (originals, read-only) and `wiki/` (synthesis notes) by category;
4. Run `inventory_builder.py` → open the hub → start using it;
5. To add a capability, create a module directory + `README.md` under `modules/`; the hub auto-collects it into "Systems".

> Everything in the templates is placeholder / example and contains no author privacy.

## 8. Toolbox (tools/)

| Tool | Purpose |
|---|---|
| `kb-hub/inventory_builder.py` | Scan the knowledge base → real inventory + audit report |
| `tools/privacy_scan.py` | Scan before open-sourcing / sharing outward to confirm no leak of real name / student ID / company / project name |
| bilingual checker under `tools/` | Check that each doc's Chinese and English versions correspond one-to-one and none is missing |

## 9. FAQ

See [faq.en.md](faq.en.md). The two most frequent:

- **Hub won't open / shows example data**: you have not generated `inventory.json`; run the command in section 4.
- **"🔍 Full-library search" says not configured**: the open repo has no engine; configure `rag.json` per section 3 or use your own `kb_rag.py`.

## 10. License reminder

This repository is **KBL-1.0**: free to use, free to modify, **no commercial use**; when redistributing you must attribute `FallenPetal` and mark the source.
See [LICENSE](LICENSE) / [LICENSE.zh-CN.md](LICENSE.zh-CN.md) / [NOTICE.en.md](NOTICE.en.md) / [docs/open-source-policy.en.md](open-source-policy.en.md).
