# Release & Installation Guide (Releases)

[简体中文](RELEASE.md) ｜ **English**

Current version: **v1.3.1** (2026-10-22)
License: **KBL-1.0** — free to use, free to modify, **no commercial use**; attribution and source link required when redistributing.
See [`LICENSE`](LICENSE) / [`LICENSE.zh-CN.md`](LICENSE.zh-CN.md) / [`NOTICE.en.md`](NOTICE.en.md).

This repository offers two download options under **Releases**. Pick one.

## Assets

| Asset | File name | Who it's for | Notes |
|---|---|---|---|
| Source bundle | `kb-from-zero.zip` | People who want all source and templates | Snapshot of the whole repo, usable right after unzipping |
| Hub executable | `kb-hub.exe` | People who just want the hub without installing Python | Zero-dependency single file; double-click opens `http://127.0.0.1:8799/` |

> Both assets are accompanied by this guide (`RELEASE.en.md`) and link to the repository home page `README.md`.

---

## Option 1: use the exe (simplest)

1. Download `kb-hub.exe` from Releases;
2. Put it at the **root of your knowledge base** (alongside `raw/ wiki/ modules/`);
3. Double-click `kb-hub.exe` → the browser opens the hub;
4. On first launch you will see **example data** (a yellow banner at the top). That is normal — it has not scanned your knowledge base yet.

### Point the exe at your own knowledge base

By default the exe treats **its own directory** as the knowledge base root. To generate a real inventory:

```bat
:: requires Python (3.10+) once, only to run the scanner
python inventory_builder.py --kb-root "path\to\your\knowledge-base"
```

Place the generated `inventory.json` **next to the exe** and restart it. The inventory looks like:

```
{ "summary": {...}, "systems": [...], "skills": [...], "entries": [...], ... }
```

> No Python? Use `kb-hub/inventory_builder.py` from the source zip, or read the example inventory's field documentation and write one by hand.

> On first run Windows may show SmartScreen — click "Run anyway". Open-source projects have no code-signing certificate; it is not a virus.

---

## Option 2: use the source zip

1. Download and unzip `kb-from-zero.zip`;
2. Requires Python 3.10+ (the hub uses only the standard library, no third-party dependencies);
3. Enter `kb-hub/` and double-click `start-kbhub.bat` (or run `python kb_hub_server.py`);
4. Open `http://127.0.0.1:8799/` in your browser.

`start-kbhub.bat` locates Python automatically and falls back through ports (8799 → 8808). No path editing required.

---

## First three steps

1. **Read section 2 of the README** and create `raw/ wiki/ modules/ outputs/`;
2. **Copy a template**: `templates/AGENTS.example.en.md` → `AGENTS.md` at your knowledge base root, replacing placeholders like `<your name>`;
3. **Open the hub**, click through "🚪 Entries / Systems / Files", then run the scanner so it recognizes your knowledge base.

---

## Advanced: rebuild the exe yourself

```bash
pip install pyinstaller
cd kb-hub
pyinstaller --onefile --name kb-hub --noconfirm ^
  --add-data "%CD%/web/index.html;web" ^
  --add-data "%CD%/inventory.example.json;." ^
  --distpath .. ^
  kb_hub_server.py
# output: kb-hub.exe at the repository root
```

Two things that matter:

- `--add-data` **must use absolute paths**: relative paths resolve against the spec file's directory and can silently bundle empty folders;
- Never bundle `inventory.json` (the real inventory) into the exe: it contains absolute local paths and real module names.
  The repository bundles only `inventory.example.json`; users place their real inventory next to the exe.

---

## Pre-release self-check (for maintainers)

```bash
python tools/make_example_inventory.py      # regenerate the purely fictional example inventory
python tools/privacy_scan.py                # final privacy gate: BLOCK > 0 means do not publish
```

Rules used by `privacy_scan.py`:

- **Generic rules**: absolute paths, user names, phone numbers, emails, national IDs, keys, private IP ranges, media files;
- **Custom term list**: real names / companies / clients / project names, kept in `~/.kb_privacy_terms.txt`, **never in the repository**;
- **Binaries**: the exe is decoded as UTF-8/GBK and its embedded strings are scanned (PyInstaller compresses resources, so plain-text scanning can miss content → **always rebuild from a clean source**, never rely on scanning alone).

The one-click publish script `publish_to_github.bat` runs this gate first and aborts if it fails.

---

## Privacy statement

Neither the repository nor the Releases contain any personal data:

- ❌ real name / student ID / contact details / credentials / chat logs;
- ❌ real project, client, company names or employment history;
- ❌ images / audio / video / PDF / Office documents (`tools/privacy_scan.py` blocks them);
- ❌ a real knowledge-base inventory (`inventory.json` is git-ignored).

The `kb-hub/inventory.example.json` in this repository is **purely fictional** (6 example modules / 8 example cards / 3 example projects),
with all paths written as `~/my-knowledge-base` and every `exists` set to `false` — so nothing looks clickable when it isn't.

---

## Version history

| Version | Date | Changes |
|---|---|---|
| v1.3.1 | 2026-10-22 | The search tab gains a **rerank stage** (bge-reranker-v2-m3, a local cross-encoder; Hybrid → RRF → rerank → top-k, the 2026 production gold standard): result cards show the rerank score, short queries (≤4 characters) skip rerank to preserve literal-lookup sensitivity, and an all-low-score pool reports "nothing found in the library" instead of returning edge-case documents; zero-hit / edge-case queries show a ⚠️ banner (rerank threshold + query-term coverage); "Ask KB" shows warnings and the citation table; hub exe rebuilt |
| v1.3.0 | 2026-10-21 | New **🔍 KB Search** tab (three-pipeline RAG: FTS5 keywords + BGE semantic vectors + character-level substring; "Ask KB" assembles a cited context pack), new `/api/rag` endpoint (config-driven, degrades gracefully when unconfigured); **one-click Chinese/English UI switch** (all UI copy in an I18N dictionary, persisted in localStorage, selftest extended with language assertions); tabs 9 → 10 |
| v1.2.0 | 2026-10-21 | **Full bilingual documentation**: every document now ships in Chinese and English with cross-links (README, RELEASE, NOTICE, nine docs, six templates, hub README); added a language switcher to each file; privacy term list extended to catch name fragments, which surfaced and removed 4 remaining leaks (2 code comments, 1 in the skill-workbench doc, 1 real scale figure in the task list) |
| v1.1.0 | 2026-10-21 | License changed from MIT to **KBL-1.0** (non-commercial + mandatory attribution + share-alike); full de-identification (example inventory rewritten, real inventory removed from the repo); added `docs/dual-pipeline.en.md` plus a minimal runnable implementation; added `tools/privacy_scan.py` and a pre-release gate; hub overview now shows the four-zone + dual-pipeline diagram and example-mode banner |
| v1.0.0 | 2026-10-21 | First release: 9-tab hub + templates + documentation |
