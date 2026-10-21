# KB Hub

[简体中文](README.md) ｜ **English**

Aggregates scattered **systems / modules / skill cards / projects / resources / frequent tools** into one local dashboard,
with a **daily heat-ranked launcher**: usage is recorded on click and sorted by "frequency × time decay".

## One-line start

Double-click `start-kbhub.bat` → the browser opens `http://127.0.0.1:8799/`.

Or manually:

```bash
python kb_hub_server.py --kb-root "<path to your knowledge base>" --port 8799
# requires Python 3.10+, standard library only
```

If the port is taken it falls back automatically (8799 → 8808) instead of failing silently.

## Features

| Tab | Contents |
|---|---|
| Overview | Metrics board + **four-zone / dual-pipeline diagram** + frequent tools + unified entries + recent sessions + file-type distribution |
| 🚪 Entries | Collapses destinations across `modules/`, `.workbuddy/`, `outputs/` into one screen, each marked "reachable / path missing" |
| Systems | One card per `modules/` module: open folder / read README / browse / count a use |
| Skill cards | Each card carries its source path; "Open card" shows the SKILL.md itself |
| Projects | Project notes and code directories |
| Resources | Section entries for `wiki/` · `outputs/` · `raw/` |
| 🧰 Tools | Download a video by URL / search local chat logs / generate a QR code, without leaving the page |
| Files | Built-in file browser: breadcrumbs, up/refresh, click into folders, open files with the OS handler |
| 🔍 KB Search | Three-pipeline fused search: FTS5 keywords + BGE semantic vectors + character-level substring; "Ask KB" assembles a cited context pack |
| 🔥 Daily | Sorted by heat, click and go |

> The number of cards depends on **your own knowledge base** — the first run shows example data with a banner at the top.

## 🔍 Whole-library search (three-pipeline RAG)

The "🔍 KB Search" tab calls `/api/rag`, executed by `kb_rag.py` on the knowledge-base side:

- **Pipeline B · keywords**: SQLite FTS5 + jieba tokenization (whole-doc and chunk granularities fused with RRF) — exact at the word level
- **Pipeline C · semantic vectors**: BGE-small-zh embeddings over all chunks — finds it even when you phrase it differently
- **Fallback · character level**: FTS5 trigram for ≥3 characters, LIKE with a timeout breaker for 1-2 characters — **every character, every sentence is findable**
- **"Ask KB"**: assembles the top-k hits into a numbered, cited markdown context pack (ready to feed an AI); on zero hits it explicitly says "nothing found in the library" instead of inventing an answer

Configuration (either way; **local only, never committed**):

1. Put `rag.json` in the portal folder:
   ```json
   {"python": "<interpreter with jieba/numpy/torch>", "script": "<path to kb_rag.py>"}
   ```
2. Or set the environment variables `KB_RAG_PYTHON` / `KB_RAG_SCRIPT`.

Without configuration the endpoint returns a clear message and the rest of the page keeps working.

> The search database (`kb.db`) is a **local derived layer**: it contains the full text of `raw/` (including private directories), can be deleted and rebuilt, and **must never leave the machine** (no upload, no git).

## Where the data comes from

```
inventory_builder.py  ──scan──▶  inventory.json  ──read──▶  kb_hub_server.py  ──▶  web/index.html
   (your knowledge base)         (real inventory)          (local HTTP server)     (single-file frontend)

usage.json  ◀── every "open / count" writes a timestamp ──▶  heat = frequency × 30-day linear decay
```

- `inventory.json`: **absolute local paths and real module names — never committed** (excluded by `.gitignore`);
- `inventory.example.json`: a **purely fictional** example shipped with the repo, all paths `~/my-knowledge-base`, every `exists` set to `false`;
- when no real inventory exists it falls back to the example and shows "showing example data" at the top of the page.

Generate your own inventory:

```bash
python inventory_builder.py --kb-root "path\to\your\knowledge-base"
```

## Design trade-offs

- **Zero dependencies**: only the Python standard library (`http.server`), no venv pollution — easy to move with the knowledge base and easy to package as a single-file exe.
- **Open actions run locally**: the hub uses `os.startfile` to open folders/files and start child services, so it **binds `127.0.0.1` only** and is never exposed to the LAN.
- **Paths are passed via `data-*`, never string-concatenated into `onclick`**: Windows backslashes get eaten by JS escaping (`\Z`, `\知`).
  This was the biggest pitfall in this project — every button once failed 100% of the time while the page looked perfectly normal.
- **Heat = frequency × decay**: prevents "used once six months ago" from dominating the list forever.
- **Port fallback + detached processes**: the 8787 child service starts as a detached process so it is not reclaimed when the session ends.

## Files

| File | Purpose |
|---|---|
| `inventory_builder.py` | Scans the knowledge base → `inventory.json` + an overview report |
| `kb_hub_server.py` | Backend (stdlib HTTP; API documented in the file header) |
| `web/index.html` | Frontend dashboard (single file, vanilla JS, no external links) |
| `start-kbhub.bat` | Windows launcher (finds Python, falls back through ports) |
| `inventory.example.json` | Example inventory (fictional) |
| `inventory.json` | Your real inventory (generated at runtime, **not committed**) |
| `usage.json` | Heat data (generated at runtime, not committed) |
| `rag.json` | Local search config (interpreter + kb_rag.py path, **not committed**) |

## Self-test

Append `?selftest=1` when loading the page; it writes JSON into `<pre id="selftest">` reporting
`hasAct / active / shown` for every tab, confirming the navigation really works — added after a
full-site click failure that looked fine on screen.
