# Task list: run parallel tasks in order and check each one

[简体中文](任务清单.md) ｜ **English**

> Companion to the execution discipline in "team collaboration and delivery flow".
> Never take a big task in one gulp — break it into independently verifiable items: **do one, tick one, escalate when blocked**.

---

## 1. Why a list is necessary

- Multi-step work relying on memory skips and drops steps;
- When collaborating, the list is the shared view of "done / blocked / awaiting review";
- With multiple AI agents, the list is the shared progress source that prevents duplication and conflicts.

## 2. How to write one

Each item must have: **a concrete action + a verification criterion + a trackable status**. Recommended shape:

```
- [ ] Task name ｜ verify: xxx
```

Statuses: `pending / in_progress / done / blocked (state the blocker)`.

## 3. Execution discipline (do and check in order)

1. **Plan before acting**: the full list exists before any work starts;
2. **One item at a time**: mark `in_progress`, finish it, mark `done`, then start the next;
3. **Self-check each step**: compare against the verification criterion; do not advance otherwise;
4. **Escalate when blocked**: do not grind on `blocked` — list the blocker, hand it to a human or change approach;
5. **Final sweep**: after everything is `done`, look back for missed red lines or missed consolidation.

## 4. This project's actual list (worked example)

Below is the task list actually used when building this repository (already completed), kept as a format example:

| # | Task | Status | Verification |
|---|---|---|---|
| 1 | Scan and generate the real knowledge base inventory + overview report | ✅ | `inventory.json` + report produced; systems/cards/projects/entries all captured |
| 2 | Build the KB hub (frontend + backend + daily heat launcher) | ✅ | API verified live, heat calculation correct |
| 3 | Prepare the open-source repository (de-identified, from zero) | ✅ | README/examples/docs/hub code present, no private data |
| 4 | Package the local zip + build the exe with PyInstaller + write release notes | ⏳ | zip + exe + RELEASE.md in place |
| 5 | Consolidate and write the usage/publishing guide | ⏳ | Work log + publishing steps (`gh` must be authenticated first) |

> Note: status is tracked live with a task tool; this table is a snapshot for the record.

## 5. Reusable blank template

```
## Task list
- [ ] 1. Research ｜ verify: ≥3 options with a recommendation
- [ ] 2. Design    ｜ verify: five questions answered, minimum entities
- [ ] 3. Implement ｜ verify: idempotent, resumable, one failure does not abort
- [ ] 4. Self-test ｜ verify: failing case fixed + return codes checked
- [ ] 5. Consolidate ｜ verify: raw + wiki + index + log
- [ ] 6. Release    ｜ verify: version triple + de-identification + release notes
```
