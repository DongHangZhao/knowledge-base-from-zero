# Skill workbench: turning prompts and skills into cards

[简体中文](技能工作台.md) ｜ **English**

> Companion to section 5 of the README. Core question: "are my prompts and the AI's reasoning prompts turned into cards?"
>
> Directory names in this English edition use English slugs (`modules/skill-workbench/`, `wiki/prompt-assets/`) for readability. The Chinese edition uses `modules/技能工作台/` and `wiki/提示词资产/`. Use whatever names you like — what matters is that the scanner and the templates agree.

## 1. The mechanism already exists

The skill workbench (`modules/skill-workbench/`) unifies skills, modules and prompts scattered across the filesystem into **cards**. It currently manages 100+ cards in six source categories:

| Source category | What it is |
|---|---|
| Prompt assets | **Author-defined rules + AI working-mode cards** (copy instead of modifying originals, ask before uploading, version triple, watermark removal, explain decisions, check for wheels first ...) |
| User-level / project-level | Skills you installed, project-specific skills |
| Knowledge base modules | One card per module under `modules/` (name + purpose + reference command + location) |
| Built-in / plugins | Skills bundled with the AI tool and from plugin marketplaces |

Every card carries four things: **ready-to-use phrasing (with `{dynamic}` placeholders) + a worked example + a bilingual term note + trigger words**. Search, favorite, pin, one-click copy (placeholders auto-filled), edit phrasing, hide/restore, add, export/import backup.

## 2. How a prompt becomes a card

It is **not** automatic on every conversation. It is the **standard move** for "sealing a conversation prompt into a card":

1. A conversation produces a rule or phrasing worth reusing;
2. Write it to `wiki/prompt-assets/<topic>.md`;
3. Rerun `scan_skills.py`; the card appears in the workbench automatically.

So the rules you establish and the AI's working modes **are already cards**. Ad-hoc prompts from a one-off conversation need the three steps above before they join the card library. This is what "add, don't subtract; attach consolidation to the work itself" looks like in practice.

## 3. Relationship with KB Hub

The hub's "Skill cards" tab reuses the workbench's scan output (`skills_data.json`); "Open skill workbench" jumps there to copy phrasing. The hub's "count" action adds heat to a card, pushing high-frequency cards to the top of "Daily".

## 4. How to add your own card

1. Create `wiki/prompt-assets/<topic>.md` covering: when to use it, the phrasing (with `{placeholders}`), and an example;
2. Run `modules/skill-workbench/scan_skills.py`;
3. Open `modules/skill-workbench/index.html` — if you can search it, it worked.
