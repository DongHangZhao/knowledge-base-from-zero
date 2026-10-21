# Module overview (example): what a reusable capability zone looks like

[简体中文](模块总览-示例.md) ｜ **English**

> This is an **illustrative** module list, showing what belongs in `modules/`. Add or remove according to your own needs.

## Example module list

| Module | What it solves | Status |
|---|---|---|
| Knowledge retrieval | Index `wiki/` + retrieval; AI searches before answering | ✅ Core |
| File sync | Incremental sync of WeChat/cloud files into `raw/` | ✅ Example |
| Versioning | Version numbers + backup snapshots (run before publishing) | ✅ Recommended |
| Media generation | One entry point for free image/video/chart generation | ✅ Optional |
| Skill workbench | Prompt/skill card management | ✅ Recommended |
| KB hub | The bundled `kb-hub/` | ✅ Included |

## Retrieval layer (advanced, but worth building early)

**Problem**: past a few hundred wiki pages, AI invents paths and answers without sources.
**Solution**: build an index (SQLite FTS5 + tokenizer) so AI retrieves instead of remembering.

Minimal shape (pseudocode):
```python
# build: scan wiki/*.md -> split into chunks -> insert
# search: query -> tokenize -> FTS5 match -> top-k chunks (with path)
# answering: put top-k chunks in context, cite the source path for every claim
```

**Verify**: ask an in-base question and the answer cites a source path; ask about something absent and it says so plainly.

> This is the second half of the "dual pipeline": an index pipeline beside the file pipeline.
> Schema, RRF fusion, seven pitfalls, runnable code → [`dual-pipeline.en.md`](dual-pipeline.en.md).

## Module README template

One README per module, covering **purpose / usage / call chain / pitfalls**. Example: [`../templates/modules/README.example.en.md`](../templates/modules/README.example.en.md).

## Two red lines (for the modules zone)

1. **Reuse = copy the directory, never modify the original**: scripts under `modules/` may be wired to scheduled jobs — changing parameters means the original project starts running with your values. That is an incident, not a convenience.
2. **Ask before uploading to the cloud**: before bulk uploads or document creation, state how much, how much room is left, and what happens if it fails.
