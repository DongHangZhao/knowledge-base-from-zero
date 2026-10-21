# Architecture overview: why four zones and one source of truth

[简体中文](架构总览.md) ｜ **English**

> A knowledge base is not "yet another folder" — it is an operating system that lets AI reuse your knowledge. Every cut in the design serves three goals: **fewer entities, lower coupling, full traceability**.

## 1. Responsibilities of the four zones

| Zone | Nature | Who writes | Deletable? | Typical content |
|---|---|---|---|---|
| `raw/` | Immutable originals | You / AI downloads | **Never** (time snapshot) | PDFs, images, videos, Word files, original article pages |
| `wiki/` | Synthesized articles | AI (after reading raw) | Editable | Project notes, study notes, technical docs, prompt assets |
| `modules/` | Reusable capabilities | You / AI | Edit with care (may back scheduled jobs) | Retrieval, sync, generation, crawler scripts |
| `outputs/` | Deliverables | AI / tools | Overwrite freely | Reports, lists, exports, images |

**Core principle**: originals separate from syntheses; capabilities separate from artifacts. Mixed together, artifacts bury originals, rewrites lose the source, and capability scripts get polluted by temporary files.

## 2. Why `raw/` is read-only and never deleted

- Traceability: every synthesized conclusion can point back to an original;
- Loss prevention: AI synthesis can be wrong; the original is the correction baseline;
- Time snapshot: multiple outputs on the same day are independent batches that coexist — **cleaning by date is simply wrong**.

## 3. Why `AGENTS.md` is the source of truth

- Rules are written in one place; other tools' convention files (`CLAUDE.md`, `.cursor/rules`, ...) are **forwarding layers** pointing back to it;
- Changing a rule means changing `AGENTS.md` only, which prevents the source of truth from splitting;
- Cross-tool: Codex / Claude Code / Qoder / Cursor read it natively; the Trae desktop app needs two switches enabled; web-based AIs get a cold-start bundle.

## 4. Two pipelines: content + index

The four zones are **Pipeline A (content)**: files are the source of truth — readable, diffable, portable.
But past a few hundred documents, AI will inevitably invent paths from memory. Hence **Pipeline B (index)**:
the same Markdown is `build`-ed into a SQLite database, retrieval hits both whole documents and chunks, and every hit **resolves back to `doc.path`**.

```
raw/ ──▶ wiki/ ──▶ outputs/                 Pipeline A: source of truth
          │
          └──build (incremental)──▶ kb.db (SQLite)   Pipeline B: derived layer, deletable, rebuildable
                                        └──▶ search ──▶ top-k + doc.path
```

Three hard rules: **SQLite is a derived layer, not the source of truth / the index is bound to its tokenizer (changing it requires a full rebuild) / every hit must resolve to a file path**.
Full schema, fusion formula, minimal implementation and seven pitfalls → [`dual-pipeline.en.md`](dual-pipeline.en.md).

> ⚠️ The most common mistake: editing an md file and forgetting to `build`. **A stale index is more dangerous than no index** — it makes AI confidently cite outdated content.

## 5. Minimum-entity principle

Before adding a directory, script or convention, ask "what happens if I remove it?" — **if nothing happens, remove it**. For conflicts, there is one tiebreaker: **whichever option introduces fewer entities wins.**
