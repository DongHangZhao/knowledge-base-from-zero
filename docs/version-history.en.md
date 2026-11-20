# Version History (v1.0.0 → latest)

[简体中文](版本发展史.md) ｜ **English**

> This document answers one question: **how did this project grow into what it is today?**
> It is not a release checklist but a development record — the paths that worked, the approaches
> that were rejected, and the experiments that only proved themselves useless by being run.
>
> Every fact here is traceable: `git tag`, `gh release list`, the root `CHANGELOG.md`, and
> [`modules/知识库索引/CHANGELOG.md`](../modules/知识库索引/CHANGELOG.md) (49 engine entries with
> measured numbers). The split is deliberate: **the root log records repository milestones**,
> **the engine log records every measurement and every failed conclusion**.

---

## 1. Six phases at a glance

| Phase | Period | Versions | What this phase solved |
|---|---|---|---|
| 0 Engine prototype | 2026-10-14 ~ 10-22 | engine v1.0.0 ~ v1.10.0 | Can retrieval work at all: chunking, vectors, three paths, concurrent writes |
| 1 Hub goes open source | 2026-10-21 ~ 10-27 | repo v1.0.0 ~ v1.4.0 | Publishing the method: hub, bilingual docs, license, search tab, rerank, methodology |
| 2 Engine matures | 2026-10-23 ~ 11-03 | engine v1.11.0 ~ v1.14.2 | Can it swallow a real library: multimodal, legacy formats, rechunking, junk control |
| 3 Making it verifiable | 2026-11-04 ~ 11-11 | v1.15.0 ~ v1.18.0 | Can the numbers be trusted: CI, eval harness, decision gates, contamination cleanup |
| 4 Release engineering | 2026-11-17 ~ 11-19 | v1.19.0 ~ v1.22.1 | Can others actually get it: two-repo convergence, multi-platform assets, containers |
| 5 Recall ceiling & mechanization | 2026-11-20 | v1.23.0 ~ latest | Where is the ceiling: question channel, assets baked into the pipeline |

> Version numbering: the repository `VERSION` is the single source of truth.
> `v1.5.0 ~ v1.13.39` belongs to the **engine's internal iteration line** and was never published
> as a repository version — the repo then held only the hub, templates and docs, while the engine
> lived elsewhere. That is why repository tags jump (`v1.4.0` → `v1.14.2`); it is not a gap.

---

## 2. Phase 0 · Engine prototype (2026-10-14 ~ 10-22)

The repository did not exist yet; the retrieval engine was being built from scratch locally,
fourteen versions, all inside `modules/知识库索引/`.

| Version | Date | What this step did |
|---|---|---|
| v1.0.0 ~ v1.7.1 | 10-14 ~ 10-15 | Skeleton: chunk indexing, vectors, FTS, CLI, incremental build, rerank and result assembly |
| **v1.8.0** | 10-21 | **Three-path retrieval takes shape** (FTS5 ⊕ BGE ⊕ character-level substring) — the ceiling of single-path recall is confirmed |
| **v1.9.0 / v1.10.0** | 10-22 | **Cross-session build mutex**: the same concurrency bug recurred the same day and needed a fourth layer before it was actually fixed |

The lesson carried into all later code: **one retrieval path always leaks; fusion is what makes it
stable. A concurrency bug that is not fixed at the root will come back.**

## 3. Phase 1 · Hub goes open source (2026-10-21 ~ 10-27)

The repository was born here. The goal was never "publish code" — it was **to explain how a
knowledge base should be built**.

| Version | Date | What this step did |
|---|---|---|
| v1.0.0 | 10-21 | First release: 9-tab hub + templates + docs |
| v1.1.0 | 10-21 | License MIT → **KBL-1.0** (no commercial use + mandatory attribution + share-alike); full de-identification; privacy scanner added |
| v1.2.0 | 10-21 | Full bilingual documentation (README / RELEASE / 9 docs / 6 templates / hub copy) |
| **v1.3.0** | 10-22 | **🔍 KB Search** tab (three-path RAG + `/api/rag` + cited context pack); one-click Chinese/English UI switch |
| **v1.3.1** | 10-22 | **rerank stage**: Hybrid → RRF → rerank → top-k; short queries skip rerank; zero-hit banner |
| **v1.4.0** | 10-27 | *Designing a Retrieval Layer, and Proving It Works* + *Usage Guide*, both languages — from "here is code" to "here is how to design and evaluate it" |

**Three decisions from this phase still hold today:**
1. **KBL-1.0 rather than MIT** — free to use and modify, no commercial use, attribution required.
2. **Bilingual is a delivery standard, not a bonus** — every document is cross-linked in both
   languages and checked by a dedicated script.
3. **Code without method is not a delivery** — since v1.4.0, methodology documents rank equal to code.

## 4. Phase 2 · Engine matures (2026-10-23 ~ 11-03)

This is the engine's internal line, answering "can it still find things when a real library has
everything in it".

**Eating a real library (v1.11.0 ~ v1.13.39):**

- `v1.11.0` metadata filters (`--since` / `--path-prefix`)
- `v1.13.21` ~ `v1.13.23` **legacy `.doc` support** — 622 old Word documents had been invisible
- `v1.13.32` ~ `v1.13.36` **multimodal landing**: image OCR (2,448 docs), audio/video transcription (272), scanned PDFs (580 files / 5,101 pages, extraction 5.74× faster)
- `v1.13.38` fixed a defect that **silently lost work**: failed scan processing reported no error, and the ledger looked complete
- `v1.13.39` **privacy egress gate**: searchable locally, gated at the exit — no restrictions inside retrieval, one gate on the way out

**What retrieval quality actually depends on (measured through v1.13.x):**

| Change | Outcome |
|---|---|
| `v1.13.7` RRF constant **k 60 → 2** | R@5 **+7pp** on the old index; after a rebuild only **+3pp** remained, corrected explicitly in `v1.13.9` |
| `v1.13.8` rerank input from "document summary" to "hit chunk text + sliding-window max" | degradations 32 → 1 |
| `v1.13.10` alias / synonym table (query-time expansion, no re-encoding) | Chinese/English term mismatches covered |
| `v1.13.18` chunk overlap proposal | **rejected in sandbox** — no positive gain |
| `v1.13.20` switching embedding model (S4) | **rejected after measurement** |
| `v1.13.20` smaller chunks (S4c) | **rejected in sandbox** — both roads dead |
| `v1.13.28` vector-path block oversampling | **zero gain**; made configurable, default keeps historical behaviour |

**Restructuring and engineering (v1.14.x):**

- `v1.14.0` **chunk structure rebuilt**: `CHUNK_MAX 1200 → 480` with sentence-boundary snapping,
  fragment completeness **5.6% → 76.4%**; language routing (m3 multilingual tower, off by default)
- `v1.14.1` rerank on GPU (`device=auto` + fp16, `KB_RERANK_DEVICE=cpu` to fall back)
- **`v1.14.2` (2026-11-03) the engine is merged into this repository** — the key repository milestone:
  published after de-identifying every machine path, with duplicate-block cleanup in the same
  version (junk blocks 3.43% → 1.67%, full rechunk 116,150 → 111,228) and the first offline CI

**What matters most from this phase is not what was built but what was rejected.** Six rejected
approaches each carry measured numbers, recorded so nobody runs them again.

## 5. Phase 3 · Making it verifiable (2026-11-04 ~ 11-11)

The theme shifts from "make it better" to **"on what basis do you call it better"**.

**v1.15.0 (11-04) turned "it runs" into "it can be verified":**

- T01 tests triaged + first offline CI; T02 incremental closure (m3 filled during build + four-table consistency assertion)
- T03 three-layer query rewriting; T04 productization trio (persona config / dual-license note / tag-driven release); T05 graph 1pt experiment scaffolding
- Added MCP server exposure (zero-dependency stdio) + evaluation harness extraction (`metrics.py` + `eval_rag.py`)
- 🔴 **`#41` graph 1pt experiment failed; archived and dropped** — the criteria were fixed before starting, and it did not meet them
- 🔴 **Query rewriting L0 rejected; default rolled back to off** — latency up, per-query diff rescued 0 and broke 1
- 🔴 **Evaluation contamination found**: `eval_rag` was not read-only against the production DB — it wrote into `query_log`, 93% of which were evaluation queries

**v1.16.0 (11-04)** removed 93% of the contamination, archived the graph experiment, extracted a
domain lexicon. Decision rationale in one line: **when the evaluation set is contaminated, no score
carries decision weight — clean the data before talking about metrics.**

**v1.17.0 / v1.17.1 (11-09)** standalone knowledge-graph subsystem v1.0.0 → v1.0.1: de-identified,
recomputable, gated; plus a fix for a gate that swallowed the whole evaluation section when the
sandbox was cleaned — a gate should fail loud, never silently print less.

**v1.18.0 (11-11)** LLM capability layer engineered: online-entry zero-reference guard, edge quality
audit, paired rewrite A/B on a frozen human title set (McNemar exact p + latency delta + snapshot
guard), reporting, 429 probing, resumable batches, and an idempotent production → repo mirror
script replacing manual copying of 28 files.

## 6. Phase 4 · Release engineering (2026-11-17 ~ 11-19)

Theme: **can others actually get it and run it.**

| Version | Date | What this step did |
|---|---|---|
| v1.19.0 | 11-17 | Two-repo convergence (production fixes merged back) + unified attribution + `outputs/` excluded; fixed a false-red C7 gate (unsynced `use_cache`, selftest 8/1) |
| **v1.20.0** | 11-17 | **Multi-platform assets**: source zip + Windows exe + Linux + macOS + Android apk; new `kb-hub/android/` subproject (p4a webview bootstrap). Every Release from v1.3 to v1.19 had **only GitHub's auto-generated source archive and no binaries at all** |
| v1.21.0 | 11-19 | rerank switched to ONNX (CPU cold start 23.8s → 20.4s, steady state 1.31×, ranking identical 3/3) + query-level cache (off by default) + ONNX export script committed |
| v1.22.0 | 11-19 | **Containerized deployment**: `Dockerfile` (non-root / `WITH_VECTOR` toggle) + `docker-compose.yml` (loopback binding, read-only model mount, read-only root filesystem) + build-context privacy gate |
| v1.22.1 | 11-19 | Fixed `Failed to fetch` under container port mapping: same-origin API + `no-store` |

**The most instructive pitfall of this phase: every Release before v1.20.0 was empty** —
`gh release create` returned 0, the page showed a Source code archive, CI was green, and there was
not a single downloadable binary. It took until phase 5 to fix this at the mechanism level.

## 7. Phase 5 · Recall ceiling and mechanization (2026-11-20)

| Version | What this step did |
|---|---|
| **v1.23.0** | **Document-side question channel** (doc2query): each document gets an offline index of "how a user might ask this", in a separate DB, without rebuilding or taking `build.lock`; retrieval fuses FTS5 + vectors via RRF, inserted after HyDE and before metadata filtering. Correct-document pool entry **51 → 70 (+19.39pp)**, zero regressions, +19ms. Same release rejects title-derived rule questions (independent set top1 40 → 35, MRR −0.20): **merging banks is not weighting** |
| **v1.23.1** | **Release mechanism fix**: `release.yml` rewritten as build → upload → self-check; a failed build means no Release; new assets gate `tools/release_assets_gate.py`; `publish_to_github.bat` reduced to push-only |
| v1.23.2 | Assets gate source-zip floor 200 KB → 100 KB (older source zips were genuinely small) |
| v1.23.3 | Removed the apk wrongly attached to nine Releases (`v1.3.0`–`v1.19.0`): those tags contain no `kb-hub/android/`, so no Android build ever existed for them |

**The root cause behind v1.23.1 deserves its own line:** missing assets were not "forgetting" but
four failures at once — `release.yml` had no asset argument at all, a comment framed manual upload
as intentional design, manual upload had no carrier (no gate, no checklist, no post-check), and the
11-17 backfill was a one-off action. **Anything that must happen every time belongs in the
pipeline, not in memory.**

---

## Appendix A · Repository releases

19 tags, all with a Release (`v1.14.2` had a tag only until its Release was backfilled on 2026-11-20).

| Tag | Date | One line | Assets |
|---|---|---|---|
| v1.3.0 | 2026-10-22 | KB search tab + bilingual UI switch | 4 |
| v1.3.1 | 2026-10-22 | rerank stage + zero-hit banner | 4 |
| v1.4.0 | 2026-10-27 | Retrieval methodology + usage guide (bilingual) | 4 |
| v1.14.2 | 2026-11-03 | Engine merged into the repo + duplicate-block cleanup | 4 (backfilled 2026-11-20) |
| v1.15.0 | 2026-11-04 | Systematic engineering T01~T05 + two defaults rolled back | 4 |
| v1.16.0 | 2026-11-04 | 93% evaluation contamination removed | 4 |
| v1.17.0 | 2026-11-09 | Standalone knowledge-graph subsystem v1.0.0 | 4 |
| v1.17.1 | 2026-11-09 | Graph gate reporting fixed (v1.0.1) | 4 |
| v1.18.0 | 2026-11-11 | LLM capability layer engineered (W12) | 4 |
| v1.19.0 | 2026-11-17 | Two-repo convergence + unified attribution | 4 |
| v1.20.0 | 2026-11-17 | Multi-platform assets + Android shell | 5 |
| v1.21.0 | 2026-11-19 | rerank on ONNX + query-level cache | 5 |
| v1.22.0 | 2026-11-19 | Containerized deployment | 5 |
| v1.22.1 | 2026-11-19 | Same-origin API fix for containers | 5 |
| v1.23.0 | 2026-11-20 | Document-side question channel | 5 |
| v1.23.1 | 2026-11-20 | Release must ship 5 assets (mechanized) | 5 |
| v1.23.2 | 2026-11-20 | Assets gate threshold fix | 5 |
| v1.23.3 | 2026-11-20 | Removed apks that never belonged to old versions | 5 |
| v1.24.0 | 2026-11-20 | **History restored and completed**: CHANGELOG backfill + this document + completed version table + a Release for v1.14.2 | 5 |

> "4" = source zip + Windows + Linux + macOS; "5" adds the Android apk (`kb-hub/android/` exists
> only from v1.20.0). Releases before v1.20.0 originally carried only GitHub's auto-generated
> source archive; the binaries were attached during the 2026-11-17 backfill.

## Appendix B · Rejected approaches

Each was either measured or judged on explicit grounds — kept here **so nobody runs it again**.

| Approach | Verdict | Basis |
|---|---|---|
| Chunk overlap | ❌ rejected | sandbox measurement showed no positive gain |
| Switching embedding model (S4) | ❌ rejected | did not beat the existing combination |
| Smaller chunks (S4c) | ❌ rejected | rejected in sandbox |
| Vector-path block oversampling | ❌ zero gain | kept configurable, default unchanged |
| RRF k = 60 | ⚠️ changed to 2 | but only +3pp after a rebuild; the original +7pp was old-index arithmetic |
| Query rewriting L0 | ❌ off by default | latency up; per-query diff rescued 0, broke 1 |
| Graph 1pt experiment (`#41`) | ❌ failed, archived | criteria fixed before starting, not met |
| Title-derived rule questions in the recall pool | ❌ rejected | independent set top1 40 → 35, MRR −0.20 |
| Global cross-document dedup | ⏳ not done | trades provenance for storage; not solvable at chunking stage |

## Appendix C · Three lessons that keep recurring

1. **"It ran" is not "it was verified".** `gh release create` returning 0, CI green, and a Source
   code link on the page do not mean assets were attached — the only valid check is counting files.
2. **Defaults must not be set on intuition.** Query rewriting L0 and the `#41` graph experiment were
   both defaulted first and beaten back by measurement. The rule now: **fix the criteria before
   starting; not meeting them means not meeting them.**
3. **A one-off manual backfill is not a mechanism.** The 11-17 backfill of ten historical Releases
   was thorough, and the next version still shipped empty — because that action lived nowhere in
   the pipeline. A release is now build + upload + self-check, in one.
