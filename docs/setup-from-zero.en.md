# Build from zero, step by step (detailed)

[简体中文](从零到一搭建步骤.md) ｜ **English**

> An expanded version of section 3 of the README. Every step states **what to do + why + how to verify**.

## Step 1 · Create the skeleton
```
my-knowledge-base/
├── AGENTS.md
├── raw/  wiki/  modules/  outputs/  references/
```
**Why**: with the skeleton in place, every later item has somewhere to land.
**Verify**: five directories plus one `AGENTS.md` exist.

## Step 2 · Write AGENTS.md (the most important step)
Start from [`templates/AGENTS.example.en.md`](../templates/AGENTS.example.en.md) and fill in:
- who you are and how you want to be talked to;
- red lines (privacy blacklist, copy instead of modifying originals, ask before subtracting, never clean time snapshots, only state what is in context);
- where the knowledge base lives and how to query it (the retrieval command).
**Why**: this is the only context an AI has when it takes over. If it is vague, the AI guesses.
**Verify**: give it to a different AI tool — it can correctly state your red lines and the knowledge base location.

## Step 3 · Define the intake rule (three-question filter)
Every incoming item passes: **will I reuse it? is it already in the base? is it traceable?** If all three are "no", do not keep it.
**Three landing places**: `raw/` + `wiki/` + the rulebook (if it is a rule, bump the version number).
**Verify**: a week later, spot-check that everything you kept has a home and nothing was "promised but never filed".

## Step 4 · Install the index pipeline (advanced, but worth doing early)

Index `wiki/` so AI retrieves instead of relying on memory. This is "the second pipeline". Full design in [`dual-pipeline.en.md`](dual-pipeline.en.md);
minimal runnable implementation in [`examples/mini_index.py`](examples/mini_index.py) (~160 lines, standard library only).

**Verify**: ask an in-base question and the answer cites a source path; ask something not in the base and it clearly says so.
After editing an md file run `build` once; run it twice and the second run should report everything "unchanged".

## Step 5 · Build your first capability module
Create a directory + script + README under `modules/` (usage / call chain / pitfalls).
**Verify**: someone else can get it running by following the README alone.

## Step 6 · Open the skill workbench
Turn recurring prompts into cards. See [`skill-workbench.en.md`](skill-workbench.en.md).
**Verify**: frequently used phrasing is one click away instead of scrolling through chat history.

## Step 7 · Install KB Hub
Put `kb-hub/` at the root and double-click `start-kbhub.bat`.
**Verify**: port 8799 shows every system/skill/project, and after clicking "count" an item appears under "Daily".

## Wrap-up
- At the end of every task, run the closing checklist in [`../README.en.md`](../README.en.md);
- Keep bumping the version of your "rules" and keep writing "knowledge" into the wiki.
