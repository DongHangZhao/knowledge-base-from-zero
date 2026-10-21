# The dual pipeline: content pipeline + index pipeline

[简体中文](双链路体系.md) ｜ **English**

> Look only at `raw + wiki` and you see **a set of directories**.
> Add `md → SQLite` and you get **a knowledge base that AI can retrieve from reliably**.
> This document explains what each pipeline is, how they align, and what breaks if you only build the first one.

---

## 1. The whole picture

```
┌──────────────────── Pipeline A · Content (source of truth) ────────────────────┐
│                                                                                │
│   raw/                      wiki/                       outputs/               │
│   immutable originals ──distill──▶ synthesized ──produce──▶ deliverables       │
│   PDF/image/video                  markdown                 reports/charts      │
│   (read-only, never deleted)       (freely rewritten)       (overwritten)      │
│                                                                                │
└────────────────────────────────┬───────────────────────────────────────────────┘
                                 │  build (incremental, by content_hash)
                                 ▼
┌──────────────────── Pipeline B · Index (derived layer) ────────────────────────┐
│                                                                                │
│   kb.db  (SQLite, single file)                                                 │
│   ├── docs           whole-document metadata: path / title / hash / mtime      │
│   ├── docs_fts       FTS5 whole-document inverted index (bm25 ranking)         │
│   ├── doc_terms      TF-IDF term vectors (cosine similarity)                   │
│   ├── chunks         split by heading level (with "parent > child" path)       │
│   ├── chunks_fts     FTS5 chunk-level inverted index                           │
│   └── raw_metadata   original-file metadata (metadata only, no body text)      │
│                                                                                │
│   search ──▶ whole-doc ⊕ chunk RRF fusion ──▶ always resolves to doc.path      │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                      AI does not receive "a block of text";
                      it receives "a block of text + which file and which section"
```

**Division of labour in one line**:
Pipeline A guarantees **knowledge is never lost, stays diffable, stays portable**; Pipeline B guarantees **AI can find it, cite it, and does not make things up**.

---

## 2. What happens if you only build Pipeline A

Many people (including the author at first) assume `raw + wiki` plus an `index.md` table of contents is enough. It holds up to about 500 documents, then you hit four walls in order:

| Symptom | Root cause | Consequence |
|---|---|---|
| AI says "according to `wiki/tech/xxx.md`" but the file does not exist | It assembles paths from memory instead of looking them up | **Ghost citations** — clicking the path gives a 404 |
| Answers are vague and miss the point | It only read the headings in `index.md`, not the body | Correct but useless |
| Entire 5,000-word documents get stuffed into context | No chunking, so whole files are fed in | 10× token cost, frequent context overflow |
| AI says "not in the knowledge base" when it is | The keyword is in the body, not the title | Knowledge that should be reused is missed |

**An index is not a convenience feature — it is the precondition for AI to cite originals.**
Without Pipeline B, AI has two options: guess paths (hallucinate) or read whole files (burn money).

---

## 3. Pipeline A: content

### 3.1 Three zones, three lifecycles

| Zone | Content | Write permission | Lifecycle |
|---|---|---|---|
| `raw/` | Downloaded originals: PDFs, images, videos, docx, web archives | **append only, never modify** | Kept forever, never deleted |
| `wiki/` | Readable knowledge written after reading `raw/` (markdown) | AI + human may rewrite | Continuously iterated, rewrites allowed |
| `outputs/` | Reports, charts, exports | Overwritten each run | Safe to wipe and regenerate |

**Why they must be separate**:
- Originals mixed with syntheses → one bad edit destroys the source;
- Capabilities mixed with artifacts → scripts drown under reports;
- All three in one directory → you never dare to "clear the artifacts" because you cannot tell which file is an original.

### 3.2 Syntheses must be traceable

Every `wiki/` page carries a source line:

```markdown
> Raw: raw/tech/2026-10-01-some-paper.pdf
```

This is not ceremony — it is **a third safety net beyond Pipeline A and B**: when you doubt a conclusion months later, you can click all the way back to the original. Without that line, you will not trust your own writing half a year on.

---

## 4. Pipeline B: index

### 4.1 Six tables, one job each

```sql
-- whole-document granularity
CREATE TABLE docs(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,   -- 🔴 the only join key; identical to the file path in Pipeline A
  title TEXT,
  mtime REAL,
  size INTEGER,
  content_hash TEXT,           -- incremental decision (sha1 of body)
  body TEXT NOT NULL
);

CREATE VIRTUAL TABLE docs_fts USING fts5(
  path UNINDEXED, title, body, tokenize='unicode61'
);
-- lower bm25(docs_fts) is more relevant; quote phrases in queries

CREATE TABLE doc_terms(          -- production version only: TF-IDF sparse vectors
  term TEXT, doc_path TEXT, tf INTEGER, weight REAL
);

-- chunk granularity
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY,
  doc_path TEXT NOT NULL,
  heading TEXT,                 -- "H1 > H2", so a hit can tell AI which section it came from
  ord INTEGER,                  -- order within the document, for context stitching
  body TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  doc_path UNINDEXED, heading, body, tokenize='unicode61'
);

-- original-file metadata (key design: no body text)
CREATE TABLE raw_metadata(
  path TEXT PRIMARY KEY, ext TEXT, size INTEGER,
  mtime REAL, sha1 TEXT, title TEXT
);
```

### 4.2 Why `raw/` only gets metadata

Originals are PDFs, images and videos — often hundreds of megabytes. Extracting full text, chunking and indexing them is extremely expensive with little payoff: about 90% of retrieval needs are "do I even have this file?", not "what does page 37 of this PDF say?".

So: **`raw/` stores metadata (can it be found), `wiki/` stores full text (what it says)**.
If you really need the body of a PDF, extract it on demand; do not pre-build.

### 4.3 Why chunk, and why chunks alone are not enough

- **Whole documents only**: a hit forces 5,000 words into context — low precision, high cost;
- **Chunks only**: loses whole-document term frequency; measured Recall@5 drops about 2 points;
- **Fusion**: whole documents capture "what this is about", chunks capture "which section is densest" — **RRF fusion lifts MRR by about 2.2%**.

Fusion formula (Reciprocal Rank Fusion):

```
score(doc) = Σ_over_lists  1 / (k + rank_in_list)      # k is usually 60
```

Key point: **RRF consumes ranks only, never raw scores** — bm25 and cosine live on different scales, and weighting them directly contaminates both.

### 4.4 Alignment: `doc.path` is the only join key

There is **no foreign key and no sync service** between the two pipelines, only one convention:

> `docs.path` = the file path relative to the knowledge base root, forward slashes, no `./` prefix.

Every retrieval result normalizes to this key, therefore:
- whole-document hits, chunk hits and vector hits all resolve to the same `path`;
- Recall is measured the same way, so retrieval modes are directly comparable;
- AI can turn any result into a clickable path.

---

## 5. Minimal runnable implementation (~160 lines, standard library only)

Code: [`examples/mini_index.py`](examples/mini_index.py). It depends only on Python's built-in `sqlite3`.
It implements three things: **incremental build / whole-doc ⊕ chunk fused search / results carry paths**.

### 5.1 Build

```bash
python docs/examples/mini_index.py build --kb-root . --db mini_kb.db
```

Measured output (using this repository's own 38 markdown files as the corpus):

```
[build] new=38 upd=0 same=0 del=0 -> mini_kb.db
```

### 5.2 Search

```bash
python docs/examples/mini_index.py search "dual pipeline" --db mini_kb.db --topk 2
```

```
1. docs/dual-pipeline.en.md   [0.1994]
   # The [dual] [pipeline]: content [pipeline] + index [pipeline]

[简体中文](双链路体系.md) ｜ **English**

> Look…
2. README.en.md   [0.0788]
   …in
[`docs/[dual]-[pipeline].en.md`](docs/[dual]-[pipeline].en.md).
```

Two details matter in the output: **matched terms are bracketed**, and **the first item on each result is the file path**.
The latter is a hard requirement: a citation must resolve to a file.

### 5.3 Stats and incrementality

```bash
python docs/examples/mini_index.py stats --db mini_kb.db
# docs=38 chunks=336 fts_rows=38
# db_size=1396.0 KB
# built_at=2026-10-21 17:58:12

python docs/examples/mini_index.py build --kb-root . --db mini_kb.db
# [build] new=0 upd=0 same=38 del=0    <- incremental works, finishes in under a second
```

---

## 6. From minimal implementation to production

The minimal version runs, but it is not enough. The production version (used by the author) hardens five areas:

| Dimension | Minimal | Production | Why |
|---|---|---|---|
| Tokenizer | SQLite `unicode61` (character-level for Chinese) | **jieba** (precise / search mode) | Character-level has high recall but poor precision; jieba search mode lifts vector-route Recall@1 from 32% to 43% |
| Sparse vectors | none | **TF-IDF + cosine** | Adds a bag-of-words signal beside the keyword route |
| Dense vectors | none | **LSA 128-dim** (default) / BGE 512-dim (deep) | Handles "different wording, same meaning" |
| Evaluation | none | **100 human + 70 automatic queries × 8 routes**, Recall@5 / MRR@5 | Retrieval tuning without evaluation is astrology |
| Self-check | none | **weekly self-check + exit-code gate** | A silently broken index is scarier than a loud error |

**Measured comparison (100-query human evaluation set)**:

| Retrieval route | Recall@5 | MRR@5 |
|---|---|---|
| Keyword ⊕ LSA dense (default) | 93–94% | **0.8525–0.8595** |
| Keyword granularity fusion (whole ⊕ chunk) | 94.0% | 0.8440 |
| FTS5 whole-document, single route | 94.0% | 0.8212 |
| Chunks, single route | 92.0% | 0.7828 |
| TF-IDF sparse, single route | 77.0% | 0.5152 |

Two conclusions that were earned, not derived:

1. **Do not fuse a weak route** — TF-IDF is 17 points behind FTS5; forcing a fusion drops Recall by 4 points.
   LSA is only 3 points behind, and fusing it is a clear win. **Fusion helps only when the two routes are of comparable quality.**
2. **BGE only helps on hard cases** — semantic hard cases go from 0/5 to 1/5 hits, but overall Recall drops slightly, at 19.8 ms latency (keyword route: 0.7 ms).
   **28× latency for 0.3% is a bad trade**, so it stays as a deep fallback only.

---

## 7. Three hard rules

### Rule 1: SQLite is a derived layer, not the source of truth

- `kb.db` can be **deleted at any time**; rerun `build` and it is back;
- **Never hand-edit** the database — your edits are overwritten on the next build and nobody knows they existed;
- The backup strategy you need is "rebuildable", not "back up the db file" (production still keeps rolling backups, because rebuilds take time).

The test is simple: **if the db and the markdown disagree, the markdown always wins.**

### Rule 2: The index is bound to its tokenizer

FTS5 tokenizers are frozen **at build time**. If you later switch to jieba, change an environment variable or upgrade SQLite,
**you must rebuild in full**, otherwise:

- the index was built per-character while queries are per-word → tokens never match → hit rate halves;
- and it **does not raise an error** — it just quietly gets worse.

### Rule 3: Every hit must resolve to a file path

If a result is only "a block of text", AI cannot cite it and you cannot verify it.
So every layer of hits (whole document / chunk / vector) must normalize to `doc.path`,
and carry `heading`, so AI can write "see section 4 of `wiki/tech/xxx.md`".

---

## 8. Seven pitfalls (all hit for real)

1. **FTS5 external-content tables cannot be DELETEd directly** — it raises `no such column: T.tokens`.
   Fix: use a normal FTS5 table (self-contained content) and `DELETE FROM fts WHERE path=?`.

2. **SQL comments can only use `--`** — `#` is a syntax error. This is the easiest mistake to make when writing scripts across languages.

3. **Equal-weight RRF gets diluted by weak routes** — see section 6. Either upgrade the weak route or leave it out.

4. **Adding an index does not mean it is used** — with two indexes sharing the same leftmost prefix, the planner picks one.
   Autopsy with `EXPLAIN QUERY PLAN`; removing the redundant index cut size 7.3% and made the same query 21% faster.

5. **You cannot just `cp kb.db` in WAL mode** — content still in the WAL is lost.
   Back up with `VACUUM INTO 'backup.db'`.

6. **Chunks alone are a negative optimization** — they lose whole-document term frequency. Fuse them with whole documents.

7. **`content_hash` must be computed from the body, never from mtime** —
   `touch` a file or let a sync client change the timestamp and mtime moves while content does not: a wasted rebuild.
   Conversely, a script that bulk-replaces content but preserves mtime (`copy2`) changes content while the build skips it — **and the index starts lying**.

---

## 9. Self-check list (run after every change to index code)

- [ ] After `build`, does `stats` document count equal `find . -name "*.md" | wc -l`?
- [ ] Delete one md, `build` again: did it disappear from `docs` / `docs_fts` / `chunks`?
- [ ] Edit a paragraph, `build` again: does retrieval return the **new** content?
- [ ] Run `build` twice: is the second run entirely "unchanged"?
- [ ] Pick three random results, paste each `path` into a file manager: **does the file exist**?
- [ ] After changing the tokenizer, did you run a full `--full` rebuild?
- [ ] Is the db-size to source-size ratio acceptable? (1.5–3× is normal in practice)

---

## 10. FAQ

**Q: Why not use a vector database (Chroma / FAISS / Qdrant)?**
You can, and semantic recall is indeed better. The cost is an extra service, extra dependencies, and a higher chance of the index and the files drifting apart.
This system chooses "a single SQLite file + optional vector routes" because **one file can move with the knowledge base** — new laptop, USB stick, git LFS, no environment rebuild. Switch when you reach 100k documents.

**Q: Should `outputs/` be indexed?**
By default **no**. Artifacts are intermediate states that get overwritten; indexing them pollutes results (ten versions of the same report).
If you really need to search artifacts, build a separate database.

**Q: How often should the index be rebuilt?**
Run an incremental `build` right after content changes (milliseconds). Full rebuilds only when changing the tokenizer, the schema, or after corruption.

**Q: Can I skip the code and use existing tools?**
Yes. Obsidian with a full-text plugin, Logseq, or even `rg` will do.
But as soon as you want **AI to cite "which section"**, you need chunking + heading paths + path backfill — and off-the-shelf note apps generally do not provide those three, which is why this builds its own database.

---

## 11. Further reading

- Why the directories are split this way → [`architecture.en.md`](architecture.en.md)
- The full build-from-zero steps → [`setup-from-zero.en.md`](setup-from-zero.en.md)
- What reusable modules look like → [`modules-overview.en.md`](modules-overview.en.md)
- Minimal implementation source → [`examples/mini_index.py`](examples/mini_index.py)
