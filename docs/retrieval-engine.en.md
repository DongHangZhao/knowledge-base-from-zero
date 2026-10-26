# Designing a Retrieval Layer, and Proving It Works

**English** ｜ [简体中文](检索层设计与评测.md)

> How do you build a retrieval layer for a personal knowledge base that people actually use — and, more importantly, **how do you prove it is right**?
> This is not API documentation. It is **design trade-offs, an evaluation method, and a record of things that broke**.
> Every number below comes from a real run, not an estimate, and every important claim is followed by how it was verified.

---

## 0. When you should not build this yourself

A retrieval layer is optional. Ask three questions first:

1. **How big is your library?** Up to a few hundred documents, `grep` plus a sane directory structure is enough. Do not add a search engine.
2. **Is outbound network traffic acceptable?** If you can send data off-machine, a hosted service is the least work.
3. **Will anyone maintain it?** A self-hosted retrieval layer needs maintenance: tokenizers, index rebuilds, version upgrades.

**This document is for you if**: you have a few thousand documents, you need retrieval to work offline, and you are willing to tune for "if a single character or a single sentence exists, it should be findable".

---

## 1. Three retrieval paths, because one is never enough

Any single retrieval method has queries it cannot serve. The design here runs **three paths in parallel and fuses the results**:

| Path | What it solves | Its built-in weakness |
|---|---|---|
| **FTS5 keywords** (SQLite built-in, pre-tokenized with jieba) | Exact term hits, proper nouns, code identifiers | Paraphrases miss entirely (synonyms, colloquial phrasing) |
| **Dense vectors** (BGE-small-zh, 512 dimensions) | Semantic similarity; a different wording still lands | Weak on exact matches; short queries drift |
| **Character-level trigram fallback** | Terms the tokenizer cannot split: rare characters, abbreviations, typos | Noisy; precision is poor on its own |

**Why a character-level fallback is mandatory**: Chinese tokenization always leaks. Professional abbreviations, rare characters, OCR errors — if jieba cannot split it, the document is never retrieved. A character-level index guarantees that **if the character appears anywhere, it has a chance to match**. That is the direct implementation of the requirement that "one character, one sentence — both must be searchable".

**Why no vector database**: at a few thousand documents, a single SQLite file with FTS5 answers in milliseconds and stays **explainable** — you can open the database and look at every table. Vector databases exist to solve distribution, multi-tenancy and horizontal scaling. That is a different order of problem.

---

## 2. Fusion: RRF and the parameter everyone copies wrong

The three paths produce scores on incompatible scales: BM25 is negative, cosine similarity runs 0–1, trigram is a raw count. **Weighted summation is meaningless.**

So the design uses **RRF (Reciprocal Rank Fusion)**, which fuses **ranks only** and never touches scores:

```
score(d) = Σ_i  weight_i / (k + rank_i)
```

### The finding: k=60 is wrong for a small corpus

Nearly every tutorial online says `k=60`. That value comes from the original RRF paper (Cormack et al., 2009), tuned on **TREC news and web corpora at million-document scale**.

`k` **flattens the gap between ranks**. The larger k is, the flatter it gets:

| k | Score at rank 8 | Score at rank 80 | Ratio |
|---|---|---|---|
| 60 | 1/(60+8) = 0.0147 | 1/(60+80) = 0.0071 | 2.1× |
| 2 | 1/(2+8) = 0.100 | 1/(2+80) = 0.0122 | 8.2× |

The consequence is concrete: **equal-weight fusion punishes a strong hit on a single path.**

- A document ranked 8th on **one** path: `1/(60+8) = 0.0147`
- Noise ranked 80th on **all three** paths: `3 × 1/(60+80) = 0.0214`

**The noise wins.** It has a rank on three paths; the strong hit has one.

### Sweeping k on real data

k swept across 100 hand-labelled queries, rerank off, same index:

| k | Recall@5 | Recall@20 | MRR@10 |
|---|---|---|---|
| 1 | **90.0%** | 96.0% | 0.7215 |
| **2** | 89.0% | **97.0%** | **0.7380** |
| 20 | 85.0% | 96.0% | 0.7069 |
| 60 (the old default) | 82.0% | 96.0% | 0.7023 |

**Why k=2 and not k=1**, even though k=1 scores 1pp higher on Recall@5:

1. k=1 loses on MRR (0.7215 < 0.7380) and on Recall@20 (96% < 97%);
2. `1/(1+rank)` is an extreme weighting curve (rank 1 → 0.5, rank 2 → 0.333), which risks overfitting a 100-query set;
3. k=2 matches the prior published by Qdrant's tuning write-up (best k for single-answer corpora is 2–5), so **external and local evidence point the same way**.

> ⚠️ **Convention trap**: Qdrant's article is 0-based, so its `k=61` equals `k=60` in this document's convention. Align the convention before comparing across sources.

### How this was verified

```bash
# switch k inside one process so the model is not reloaded each run
for k in 1 2 5 20 60; do
  KB_RRF_K=$k python judged-eval-runner.py --tag "rrfk$k" --no-rerank
done
```

🔴 **One thing has to be checked first**: if `k` is a constant bound at module import time, changing the environment variable has no effect on an already-imported process — **all five runs return identical numbers** and you have wasted a full evaluation cycle. So the implementation must resolve it **at call time**:

```python
def rrf_k() -> int:
    """Read the environment variable at call time, falling back to the default.

    Do not write RRF_K = 60 and let downstream code import it — that binds at import.
    """
    raw = os.environ.get("KB_RRF_K", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else RRF_K_DEFAULT
```

---

## 3. Reranking: the optimization that made things worse

This is the most useful section in the document, because it is a **failed optimization** — and the cause was not the model, it was the input.

### 3.1 What happened

A Cross-Encoder rerank stage (BGE-reranker-v2-m3) was added on top of the fused results, expecting better ordering. Measured:

| Configuration | Recall@5 | MRR@10 | Latency (100 queries) |
|---|---|---|---|
| Rerank off | **88.0%** | **0.7235** | fast |
| Rerank on | 76.0% | 0.5552 | 22% slower |

**Turning reranking on dropped 12 points and made everything slower.**

### 3.2 Root cause: the model was fine, it was fed the wrong text

Reading the actual cases: in 6 failing queries, the **gold answer scored 0.0000 / 0.0002 / 0.0001 / 0.0011**, while the wrong answers ranked above it scored **0.9936 / 0.7787**.

That is not "unsure". That is **confidently identifying the wrong document**.

Tracing the input revealed why:

```
Cross-Encoder input fallback chain: chunk_text → summary → title
None of the three retrieval paths returns the chunk body → it falls back to summary (first 120 characters)
```

**The summary does not contain the answer, so of course the model scores it low.** Worse, every one of the twelve mis-ranked top-1 results was a "sibling document" of the gold answer (same project, same series) — the model simply cannot separate them when the only difference it sees is a summary.

### 3.3 The fix: feed the matched chunk's own text, plus a sliding window

Two open-source implementations do exactly this — both **feed the full chunk text, never the summary**:

- **RAGFlow** (92k★) says it plainly in a comment:
  > Feed the reranker the natural chunk text, **not the tokenized content_ltks**.
  > Neural rerankers score stemmed / accent-split tokens far lower, which **collapses relevance scores**.

- **QAnything** (14k★) uses parent/child chunks: long chunks are **not hard-truncated**; instead it **slides a window (overlap=80 tokens) and takes the max score per chunk**. Its CPU-only profile also runs `max_length=512`.

🔴 **Here is the counter-intuitive part**: the first instinct was "the window is too small" and the plan was to widen it to 8192. **That was wrong.** After `sigmoid`, the absolute scores are **globally calibrated** — feed real body text and **every** score shifts up together; it is not a zero-sum trade. So the assumption "if the gold answer goes up, the wrong answers must come down" does not hold. The correct move is to **keep 512 and take the max over a sliding window**, not to widen the window (8192 makes CPU inference 16× slower).

### 3.4 After the fix: it works, and it still does not pay off

| Metric | Before (summary input) | After (body text + sliding window) |
|---|---|---|
| Recall@5 | 68.0% | **76.0%** |
| **Low-confidence degradations** | **32 queries** | **1 query** |

**"Degradations 32 → 1" says more than the +8pp**: before the fix, 32 queries had a pool-wide top score below the threshold — the model could not produce a usable score, so the system fell back to the unfused order. After the fix, only 1 does. **The scores discriminate again.** The diagnosis was correct.

But the fixed pipeline is still **76.0% against 88.0% with rerank off** — a 12pp gap. So the outcome was:

> **Keep all the code, ship it disabled by default.** One config line turns it on for A/B comparison.

### 3.5 What this episode actually teaches

1. **A reranker is not a magic wand.** When input quality is poor, adding a rerank stage is a net negative.
2. **Aggregate metrics hide a fix that was correct.** Looking only at Recall@5, 76% is still below 88%, which reads as "the fix did nothing". "Degradations 32 → 1" shows the fix itself succeeded — it just **never crossed the break-even line**.
3. **Thresholds must be tied to a baseline.** A real trap here: after changing the default k, a leftover rule still read "rerank stays only if Recall@5 ≥ 82%" — but with the new k, rerank-off was already at 88%, so the bar had silently dropped 7pp. **Change the baseline and every threshold derived from it must be recomputed.**

---

## 4. Evaluation: how to prove it works

Optimization without an evaluation set is guesswork. This method is not sophisticated, but it is not optional.

### 4.1 A minimal usable evaluation set

- **100 queries**, each hand-labelled with its gold answer (document-path granularity is enough).
- The queries must cover real usage: colloquial short queries, proper nouns, paraphrases, cross-document synthesis.
- **You have to label them yourself** — you are the one who needs to know whether a hit counts as correct.

### 4.2 Three metrics

| Metric | Meaning | Why use it |
|---|---|---|
| **Recall@5** | Share of queries whose gold answer is in the top 5 | Closest to real experience (people read the first few hits) |
| **Recall@20** | Widened to the top 20 | Separates "never retrieved" from "retrieved but mis-ranked" |
| **MRR@10** | Mean reciprocal rank of the gold answer | Measures how high it ranks; sensitive to the top position |

**The Recall@20 − Recall@5 gap is the useful signal**: a large gap means a **ranking** problem (found it, ranked it badly); a small gap means a **recall** problem (never found it). The two have completely different fixes.

### 4.3 When does a change count as an improvement

**Always A/B: same index, same evaluation set, one variable changed.**

```
run before → run after → compare
```

🔴 **And run a null test.** When a number moves, resist the urge to invent a story for it — **rerun the same configuration and check that the delta is 0**. This trap was hit for real: a metric shift was attributed to change A, and a rerun showed nothing had moved at all; the earlier number had been misread.

### 4.4 Keep the artifacts

Every evaluation run must persist: the metrics, the per-query results, and **a fingerprint of the code and the index** (file mtimes, document and chunk counts from the database, vector build timestamp).

> Why fingerprints are non-negotiable: two evaluation runs once disagreed, and it turned out the **index had been rebuilt in between** — document count had gone from 4,044 to 4,152. Without a fingerprint you can never answer "did the code change or did the data change?"

**Measurement baseline**: unless stated otherwise, every number in this document was measured against one index — **4,152 documents, 44,479 chunks**.

---

## 5. Engineering: making it more than a script

A retrieval layer usually dies from one of three things: **concurrent writes corrupting the database, backups that do not restore, and no way to notice a regression.**

### 5.1 A write lock (paid for with a real incident)

Several automation jobs were building the index at the same time, and one of them went through an old entry point with no ownership boundary. **It wiped half the index and then failed to rebuild it** — a few thousand documents were reduced to a few hundred. And it happened **silently**, with no error.

The fix has four layers:

1. **Ownership boundaries** — each indexer may only write the categories it owns;
2. **Self-healing state** — if the row counts in the database disagree with the state file, force a rebuild;
3. **Caller consolidation** — keep exactly one safe entry point and migrate every other path to it;
4. **A mutex** — if the lock is unavailable, queue or skip. Silent concurrency is the failure mode; never allow it.

### 5.2 Backups need a health gate

"Having a backup" is more dangerous than "having no backup" — if the backup itself is truncated.

Validate before writing: if document count, chunk count or vector count is zero or obviously low, **refuse the write and raise an alert**; then validate the integrity of the file on disk afterwards. Rotation must count **generations**, not files — a single SQLite database is three files (`.db`, `-wal`, `-shm`), and counting files deletes exactly the ones you meant to keep. That, too, is a real incident.

### 5.3 Regression tests must run as one command

Tests scattered across directories with no single entry point are tests that do not exist. The shape is:

```
one command → run every suite → print pass/fail + assertion count + duration → render a dashboard
```

Each suite guards a mechanism that **has actually broken before**. None of them exist to pad a number:

| Suite guards | Why it exists |
|---|---|
| Concurrent write lock | See 5.1 |
| Backup rotation | See 5.2 |
| Build queue | State-machine transitions and concurrency safety |
| Metadata filters | Boundary conditions on time/path filters |
| Privacy gate | Private paths must be rejected |
| Rerank degradation | A low score must never empty the result set |
| Traversal equivalence | After optimizing traversal, results must match item by item |

---

## 6. Pitfalls (selected)

1. **Chinese titles were never in the index.**
   FTS5's `unicode61` tokenizer classifies Han characters as Unicode L*, so an entire Chinese title becomes **a single token**. Title weighting had therefore been a no-op for as long as it existed: `title:"知识"` matched **0 documents**, while the body field matched 484.
   **Fix**: leave the tokenizer alone and change the **write path** — pre-tokenize titles with the same tokenizer used for body text, then insert. The idea comes from a Chinese open-source project (AstrBot), which also keeps `unicode61` as the default and relies on pre-tokenized inserts.

2. **`tokenize='trigram'` looks like it solves Chinese. It does not.**
   It does support Chinese substrings, but **any query term shorter than 3 characters matches nothing** — and "知识" is exactly two characters. Chinese still needs tokenization.

3. **A comment that disagrees with the code is worse than no comment.**
   A file header said the tool deliberately ignores the `reports` directory, but `reports` was not in the exclude list at all — so logs and artifacts were reported as code changes every single day. **Trusting the comment over the behaviour is a guaranteed trap.**

4. **One missing letter in a filename can cost tens of gigabytes.**
   The snapshot exclude list said `env`; the actual directory was `envs` (one `s` short). Measured impact: a single module pulled **22GB across 37,767 files** into the snapshot. **No test catches this class of bug — only a dry run with volume reported does.**

5. **Verify the input before you diagnose.**
   Before investigating "cannot fetch / invalid", re-read the raw input character by character — URL, ID, path. A link ID was once transcribed with one wrong letter, and an entire debugging session was spent investigating an object that did not exist.

---

## 7. Scope and limitations

**Fits**: an individual or a small team, a few thousand documents, an offline requirement, and a willingness to tune for retrieval quality.

**Does not fit / known gaps**:

- **Data volume**: 4 thousand documents is personal scale. Enterprise scale in the millions needs a different backend (distribution, permissions, operations).
- **No multi-user or permission model**: it is a personal tool, with no tenant isolation.
- **Single-machine CPU**: latency is bought with trade-offs (disabling rerank is one of them).
- **No CI**: tests run locally as one command; GitHub Actions is not wired up.
- **No coverage figure**: the strategy is "critical paths plus regressions for real incidents", not full coverage.

**If the corpus grows 10×, the order is**: ① grow the evaluation set to 300–1,000 queries first (otherwise you cannot judge whether a change helps) ② then swap the retrieval backend (keeping the RRF layer — it is not tied to any backend) ③ only then re-evaluate reranking (the benefit curve moves with data volume).
**The order cannot be reversed: measurement first, architecture second.**

---

## 8. In one sentence

**The bottleneck in retrieval is usually whether the right document was found, not how well the answer is generated — and the only way to prove the right document was found is to own a labelled evaluation set.**

Every optimization should be able to answer one question: **after this change, did it go up or down, and by how much.**
If you cannot answer that, you are not yet at the optimization stage.
