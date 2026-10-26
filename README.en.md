# Build Your Knowledge Base From Zero · A Complete Guide

[简体中文](README.md) ｜ **English**

![license](https://img.shields.io/badge/license-KBL--1.0%20(noncommercial)-blue)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![deps](https://img.shields.io/badge/dependencies-zero-brightgreen)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

> A reusable methodology and toolset for building a **personal or team knowledge base that doubles as an AI external brain**.
> Distilled from the author's own long-running setup, then **fully de-identified** before publication.
>
> License: **KBL-1.0** — free to use, free to modify, **no commercial use**, attribution and source link required when redistributing.
> See [`LICENSE`](LICENSE) / [`LICENSE.zh-CN.md`](LICENSE.zh-CN.md) / [`NOTICE.en.md`](NOTICE.en.md) / [`docs/open-source-policy.en.md`](docs/open-source-policy.en.md).

---

## What this is

A system for **keeping scattered material under control and making AI actually reuse your knowledge**. It solves three problems:

1. **Material stops piling up into chaos** — a fixed directory architecture (raw originals / synthesized wiki / capabilities / outputs) replaces "download → Desktop → forget".
2. **AI gets a single source of truth** — one `AGENTS.md` file tells any AI tool (Codex / Claude Code / Qoder / Trae / Cursor) the rules, the context and the red lines the moment it takes over.
3. **Scattered tools get one entry point, and daily habits get ranked** — the bundled KB Hub pulls dozens of scripts, skill cards and projects into a single dashboard, sorted by usage heat.

What's in this repository:

| Item | Path | Notes |
|---|---|---|
| Step-by-step guide | this file `README.en.md` | You are reading it |
| KB Hub (frontend + backend) | `kb-hub/` | Zero-dependency local web dashboard aggregating systems / skills / projects |
| Example templates | `templates/` | Blank starting points for AGENTS.md / wiki / modules / prompt assets |
| Advanced docs | `docs/` | Architecture, **dual-pipeline**, **retrieval design and evaluation**, module overview, skill workbench, collaboration, licensing, FAQ |
| Release & install guide | `RELEASE.en.md` | How to use the zip / exe from Releases |
| Policy enforcement tools | `tools/` | Example inventory generator, privacy scanner, bilingual checker |

> 🌐 **Every document ships in both Chinese and English**: each file has a "简体中文 ｜ English" switcher at the top, and the two versions correspond one to one.
> English naming: `<original-name>.en.md` at the repository root and in `kb-hub/`; semantic English names under `docs/` (e.g. `architecture.en.md`); `<original-name>.en.md` under `templates/`.
> The license is bilingual too: [`LICENSE`](LICENSE) (English) / [`LICENSE.zh-CN.md`](LICENSE.zh-CN.md) (Chinese).

---

## 1. Core idea: Karpathy's LLM Wiki + an AI external brain

Treat the knowledge base as "a wiki that the LLM writes and maintains":

- **Humans** produce raw material and make final judgments;
- **AI** archives, synthesizes, retrieves and reuses;
- **The system** keeps all of the above from depending on memory or supervision.

Key insight: **rules do not live inside each AI tool**. They live in one cross-tool `AGENTS.md`. Switching tools means giving the new AI a file it can read — not teaching it everything again.

---

## 2. Architecture: four zones + one source of truth + two pipelines

```
my-knowledge-base/
├── AGENTS.md            # 🔴 Single source of truth: who you are / how to talk / red lines / where knowledge lives / how to query
├── raw/                 # Immutable originals (downloaded PDFs/images/videos/docs), read-only, never deleted
├── wiki/                # Synthesized articles (readable knowledge written after reading raw/), relative links
├── modules/             # Reusable capabilities: runnable scripts (retrieval / sync / generation ...)
├── outputs/             # Deliverables: reports / lists / exports (grows forever, overwritten often)
├── references/          # Templates
└── .workbuddy/          # Project-level AI config and skills (optional)
```

**Why split this way** (details in [`docs/architecture.en.md`](docs/architecture.en.md)):

- Separating `raw/` from `wiki/` → originals stay traceable; synthesized drafts can be rewritten freely without losing the source;
- Separating `modules/` from `outputs/` → capabilities (long-lived) are not buried by artifacts (overwritten);
- `AGENTS.md` is the source of truth; other tools' convention files are mere **forwarding layers** pointing back to it.

### 🔴 Two pipelines: content pipeline + index pipeline

Most people only see the `raw + wiki` pipeline. **What actually makes AI retrieval accurate is the second one** —
the same Markdown files are also written into a SQLite database, and the two pipelines are aligned by file path:

```
[Pipeline A · content] Files are the source of truth: always readable, diffable, portable
  raw/ (immutable originals) ──distill──▶ wiki/ (synthesized) ──produce──▶ outputs/ (deliverables)
        │                        │
        └──── metadata only ─────┘
                    ▼
[Pipeline B · index] Derived layer, deletable and rebuildable, never hand-edited
  md files ──build──▶  kb.db (SQLite)
                       ├─ docs          whole-document metadata (path/title/hash/mtime)
                       ├─ docs_fts      FTS5 whole-document inverted index (bm25)
                       ├─ doc_terms     TF-IDF term vectors (cosine)
                       ├─ chunks        split by heading level (with heading path)
                       ├─ chunks_fts    FTS5 chunk-level inverted index
                       └─ raw_metadata  original-file metadata (no body text)
                    ▼
  search ──▶ whole-doc ⊕ chunk RRF fusion ──▶ always resolved back to doc.path
```

**Three hard rules:**

1. **SQLite is a derived layer, not the source of truth** — deleting `kb.db` loses no knowledge; just rerun `build`.
2. **The index is bound to its tokenizer** — changing the tokenizer requires a full rebuild, otherwise query tokens and index tokens diverge and retrieval quality collapses.
3. **Every hit must resolve to a file path** — results always carry `doc.path`, so AI citations can be opened. Without that, you are breeding hallucinations.

Full design, schema and a minimal runnable implementation (~160 lines of stdlib SQLite FTS5) are in
[`docs/dual-pipeline.en.md`](docs/dual-pipeline.en.md).

---

## 3. Build from zero (7 steps)

> Full version: [`docs/setup-from-zero.en.md`](docs/setup-from-zero.en.md). Below is the short form.

1. **Create the skeleton** — the five zones from section 2 plus `AGENTS.md`.
2. **Write AGENTS.md** — your identity, communication preferences, red lines, where the knowledge base is, how to query it (start from [`templates/AGENTS.example.en.md`](templates/AGENTS.example.en.md)).
3. **Define the intake rule** — every incoming item passes three questions (will I reuse it? is it already there? is it traceable?). Only keep what passes.
4. **Install the index pipeline** — index `wiki/` so AI retrieves instead of inventing paths (see [`docs/dual-pipeline.en.md`](docs/dual-pipeline.en.md)).
5. **Build your first capability module** — put a reusable script in `modules/` with a README covering usage / call chain / pitfalls.
6. **Open the skill workbench** — turn recurring prompts into cards (see [`docs/skill-workbench.en.md`](docs/skill-workbench.en.md)).
7. **Install KB Hub** — drop [`kb-hub/`](kb-hub/) into your knowledge base, double-click `start-kbhub.bat`, and every system / skill / project is one screen away.

---

## 4. KB Hub

Aggregates scattered systems into one local dashboard, with a **daily heat-ranked launcher**.

- **Start**: put `kb-hub/` at the root of your knowledge base, double-click `start-kbhub.bat` → opens `http://127.0.0.1:8799/`.
- **Ten tabs** (the UI supports **one-click Chinese/English switching**, remembered in localStorage):
  | Tab | Purpose |
  |---|---|
  | Overview | Metrics board + dual-pipeline diagram + frequent tools + unified entries + recent sessions + file-type distribution |
  | 🚪 Entries | Collapses destinations scattered across `modules/`, `.workbuddy/`, `outputs/` into one screen, each marked "reachable / path missing" |
  | Systems | One card per capability module: open folder / read README / browse / count a use |
  | Skill cards | Each card carries its source path; "Open card" shows the actual SKILL.md |
  | Projects | Project notes and code directories |
  | Resources | `wiki/` sections plus `outputs/` and `raw/` top-level folders (all clickable) |
  | 🧰 Tools | **Three frequent actions in place**: download a video by URL, search local chat history, generate a QR code |
  | Files | **Built-in file browser**: breadcrumbs, up/refresh, click into folders, open files with the OS handler |
  | 🔍 KB Search | **Three-pipeline RAG**: FTS5 keywords + BGE semantic vectors + character-level substring, fused; "Ask KB" assembles a cited context pack (configuration in `kb-hub/README.en.md`) |
  | 🔥 Daily | Sorted by "frequency × 30-day decay"; this is the one screen you open every day |
- **Usage is recorded on click**: every "open" or "count" writes to `usage.json`; heat = *frequency × 30-day time decay*.
- **Zero dependencies**: Python standard library only, no environment pollution, easy to move — and it can be packaged into a single-file exe (see `RELEASE.en.md`).
- **Path robustness**: the frontend passes arguments via `data-*` attributes (never string-concatenated `onclick`); the backend normalizes slashes, resolves relative paths and self-heals plugin version drift — so Windows backslashes are never eaten by JS escaping and turned into "path not found".
- **Automatic port fallback**: if 8799 is taken it tries 8800–8808 instead of failing silently.
- **Example mode is visible**: with no real inventory yet, the page says "showing example data" instead of looking broken.

> The hub binds `127.0.0.1` only (localhost) and performs open actions locally — **never exposed to the LAN**, in line with the privacy principle.

---

## 5. Skill cards and prompt management

The skill workbench unifies skills / modules / prompts into cards: search, favorite, pin, one-click copy, prompt editing, export/import backups.
**Prompt-asset cards** capture the rules you have established (copy instead of modifying the original, ask before uploading, version triple, watermark removal, explain the decision ...) and the AI's working modes as reusable phrasing — this is the standard move for "sealing a conversation prompt into a card".

See [`docs/skill-workbench.en.md`](docs/skill-workbench.en.md) and [`templates/wiki/提示词资产/`](templates/wiki/提示词资产/).

---

## 6. Team collaboration and delivery

For small teams (several people maintaining one knowledge base, or delivering software together), see [`docs/team-collaboration.en.md`](docs/team-collaboration.en.md):

- Collaboration rules: branches / directory ownership / code review / red-line tiers;
- Delivery flow: requirement → design → implementation → self-test → consolidation → release, six gates with checklists;
- Companion [`docs/task-list.en.md`](docs/task-list.en.md): break big work into verifiable items, tick one at a time, escalate when blocked.

---

## 7. License (read this before use)

This project is **not MIT and not GPL**. It uses a custom license: source-visible, non-commercial, attribution required.

| You **may** | You **may not** |
|---|---|
| Download, read, learn from it, build your own knowledge base | Sell paid courses / consulting / template packs |
| Modify and extend it for internal use | Bundle it into a product or SaaS you sell |
| Share it with friends or write about it (with attribution) | Strip the author's name and claim it as your own |
| Use it in a non-commercial open-source project (same license) | Re-license it as MIT / Apache and republish |
| Evaluate it inside your organization for 30 days | Run it in company production long-term (commercial license required) |

**Redistribution must state**: project name + author `FallenPetal` + license `KBL-1.0` + original repository link (derivatives must also say "modified").
Copy-paste attribution templates for four scenarios → [`NOTICE.en.md`](NOTICE.en.md) section 1.

Why not MIT / GPL / CC BY-NC / PolyForm → [`docs/open-source-policy.en.md`](docs/open-source-policy.en.md).
For commercial licensing, open an issue describing your organization, intended use, scale and whether you modified it.

> ⚠️ GitHub may show `Unknown license` in the sidebar: custom licenses are not in GitHub's detection database. That is expected; the full `LICENSE` text governs.

---

## 8. Zero-privacy commitment

Every file was scanned before publication ([`tools/privacy_scan.py`](tools/privacy_scan.py), whose binary-detection logic is guarded by the regression test [`tools/test_privacy_binary.py`](tools/test_privacy_binary.py)). This repository deliberately contains none of:

- ❌ the author's real name, student ID, contact details, accounts, keys, tokens or cookies;
- ❌ real project names, client names, company names or employment details;
- ❌ chat logs, address books, emails, photos, audio, video or any media original;
- ❌ a real knowledge-base inventory (`inventory.json` is git-ignored; only a fictional `inventory.example.json` ships);
- ❌ absolute local paths (examples use the `~/my-knowledge-base` placeholder).

When you build your own, keep private data local: **never in git, never in the cloud**.

---

## 9. Download and install

- **Source**: `git clone` this repository, or grab the zip from Releases;
- **No-Python exe**: Releases ships `kb-hub.exe`; double-click to open the hub;
- Detailed steps: [`RELEASE.en.md`](RELEASE.en.md).

---

## 10. FAQ and further reading

- Why the architecture is split this way → [`docs/architecture.en.md`](docs/architecture.en.md)
- **How to implement index + SQLite** → [`docs/dual-pipeline.en.md`](docs/dual-pipeline.en.md)
- **Retrieval layer design and evaluation** (three retrieval paths + RRF k tuning + a failed rerank post-mortem + the evaluation method + engineering, with real A/B numbers) → [`docs/retrieval-engine.en.md`](docs/retrieval-engine.en.md)
- What reusable modules look like → [`docs/modules-overview.en.md`](docs/modules-overview.en.md)
- How to choose and declare a license → [`docs/open-source-policy.en.md`](docs/open-source-policy.en.md)
- Common questions → [`docs/faq.en.md`](docs/faq.en.md)

---

## License

[KBL-1.0](LICENSE) — free to use and modify, **no commercial use**; redistribution must credit `FallenPetal` and link the source.
