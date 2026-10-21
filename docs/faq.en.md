# FAQ

[简体中文](FAQ.md) ｜ **English**

**Q1: Do I have to use all these tools?**
No. The four-zone architecture plus `AGENTS.md` is the core. The retrieval layer, the hub and the skill workbench are optional upgrades. Start with the fewest entities that work.

**Q2: How long should AGENTS.md be?**
As long as it needs to be, no longer. Put "must know every time" content in the root file and move details into a quick-reference that is loaded on demand. Too long and AI ignores the middle (lost in the middle).

**Q3: How should private data be handled?**
`raw/personal/`, `raw/private/`, credentials, chat logs: **never to the cloud, never into git, never into public posts, never into AI image generation**. Templates use `<your name>` placeholders.

**Q4: Can the hub be used by several people?**
The hub binds `127.0.0.1` only and performs open actions locally — **not exposed to the LAN**. For teamwork, follow the branch/directory-ownership approach in [`team-collaboration.en.md`](team-collaboration.en.md).

**Q5: Where does the exe come from?**
`kb-hub/` is packaged into a single-file exe with PyInstaller (zero dependencies, no Python required). Build steps are in `RELEASE.en.md`.

**Q6: Do I have to retrain a new AI tool?**
No. Hand `AGENTS.md` (and its forwarding files) to the new tool and it immediately knows the rules, context, red lines and where the knowledge base lives.

**Q7: Do skill cards automatically capture every conversation?**
No. Turning a prompt into a card is the deliberate "seal into a card" action (write to `wiki/prompt-assets/` → rerun the scanner). Ad-hoc prompts need manual consolidation.

**Q8: `raw + wiki` already has structure — why add SQLite?**
Directory structure answers "where things live", not "how AI finds them". The index pipeline handles three things: chunk-level location (down to the exact section), multi-route recall (keywords + vectors), and path backfill (every result carries `doc.path`, so citations are clickable).
Details: [`dual-pipeline.en.md`](dual-pipeline.en.md).

**Q9: What if the index breaks or is deleted?**
Rerun `build`. SQLite is a derived layer — **deleting it loses no knowledge**; the source of truth is always the files in `raw/` and `wiki/`.
Conversely: **never hand-edit the db**; your edits are overwritten on the next build.

**Q10: What happens if I edit an md and forget to rebuild the index?**
AI cites stale content — no error, just confident outdated answers. That is why "rebuild after every md edit" belongs in the red lines of `AGENTS.md`.

**Q11: Can I use it commercially?**
No. The license is KBL-1.0 (non-commercial + mandatory attribution). See [`../LICENSE.zh-CN.md`](../LICENSE.zh-CN.md) (Chinese) / [`../LICENSE`](../LICENSE) (English) and [`open-source-policy.en.md`](open-source-policy.en.md).
