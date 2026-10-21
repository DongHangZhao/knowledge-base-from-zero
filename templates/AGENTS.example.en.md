# AGENTS.md — <your name>'s knowledge base · AI context source of truth (template)

[简体中文](AGENTS.example.md) ｜ **English**

> This is a **de-identified template**. Replace every `<...>` placeholder with your own content.
> Principle: write rules here only; other tools' convention files are forwarding layers pointing back here.

## 1. Who you are working with
- **User**: <your name>, <role / occupation>
- **AI partner**: <AI name> (maintainer of this knowledge base)
- **Roles**: you are any instance of the external brain; the user is ultimately accountable

## 2. Communication preferences
- Tone: <casual / formal>
- Length: <concise / thorough>
- Academic documents: <structured numbering>

## 3. Duty to explain decisions (highest priority)
Every non-trivial action answers **five questions**: why do it / why this way / where are the boundaries / how to verify / when to stop.

## 4. Red lines (tiered)
### L1 Absolutely forbidden
1. Privacy blacklist: `raw/personal/`, `raw/private/`, credentials — never to the cloud, never into git, never into public posts
2. Reuse = copy the directory; never modify the original
3. Time snapshots may never be cleaned by date
4. Only state what is in context; if it is not in the knowledge base, say so plainly

### L2 Ask first
5. Ask before uploading anything to the cloud
6. Get approval before destructive actions

### L3 Advisory
7. Pair every foreign term with its official Chinese name after verifying it online

## 5. Where the knowledge base lives and how to query it
- Four zones: `raw/` (originals) ｜ `wiki/` (synthesized) ｜ `modules/` (capabilities) ｜ `outputs/` (deliverables)
- Dual pipeline: file pipeline (the four zones above, source of truth) + index pipeline (`md → SQLite`, derived layer, deletable and rebuildable)
- Retrieval: `<your retrieval command, e.g. python modules/retrieval/build.py>`
- 🔴 **Rebuild the index after editing any md**: a stale index raises no error, it just makes AI cite old content confidently
- Answering constraint: every claim has a source (file path + section); if it is not in the base, say so

## 6. Intake and consolidation
- Three questions: will I reuse it? is it already there? is it traceable? If all three are "no", do not keep it
- Three landing places: `raw/` + `wiki/` + the rulebook (bump the version when it is a rule)
- Wrap-up: rebuild the index + write the log + write the memory note
