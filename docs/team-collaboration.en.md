# Team collaboration and delivery flow

[简体中文](团队协作规范与交付流程.md) ｜ **English**

> For when the knowledge base grows from "a personal external brain" into "a small-team collaboration platform".
> Applies to: several people maintaining one knowledge base, or delivering software together. Goals — **lower collaboration entropy, guarantee handover, never cross a red line**.

---

## 1. Collaboration rules

### 1.1 Roles and responsibilities (simplified RACI)
| Item | Responsible (R) | Accountable (A) | Informed (I) | Consulted (C) |
|---|---|---|---|---|
| Knowledge base structure changes | Architecture owner | Everyone reviews | Everyone | Implementer |
| Red line / rulebook changes | Architecture owner | Everyone reviews | Everyone | — |
| Module script release | Module owner | Architecture owner | Stakeholders | Developer |
| External publishing / cloud upload | Release owner | Knowledge base owner | Everyone | Publisher |

> Key point: **the knowledge base owner is the ultimately accountable person**; AI is an execution instance and never substitutes for judgment.

### 1.2 Branches and directory ownership
- Code: protect the main branch; all changes go through a branch + PR;
- Knowledge base: assign **directory ownership** first (last writer wins otherwise) so two people never edit the same file blindly;
- Sub-agents / collaborators: read-only investigation first, the main agent applies all changes; every sub-agent conclusion must be human-verified.

### 1.3 Code review (four-eyes principle)
- Every merge gets at least one reviewer;
- Review checks three things: **did it touch what it must not (red lines / original modules), does it cite sources, does it include tests**;
- AI-generated code is unverified by default. Even the best models score only around 35% accuracy on in-context reasoning — human verification is mandatory.

### 1.4 Red-line tiers (everyone must memorize)
| Tier | Content | Handling |
|---|---|---|
| **L1 absolutely forbidden** | Private data to cloud/git/public posts; modifying original modules; cleaning time snapshots; inventing paths | Crossing it is an incident: stop first, then fix |
| **L2 ask first** | Before cloud upload; destructive deletion; writing outside your zone | Produce a list and wait for approval |
| **L3 advisory** | Dual watermark/no-watermark copies; full Chinese-English pairing; tinkering with tools ≠ progress | May ignore, but explain why |

### 1.5 Four AI collaboration mechanisms
1. **Continuous investigation**: don't answer once and stop — follow it to the end;
2. **Stable background**: a task brief has three elements (what to investigate / what you may do / what to hand back);
3. **Messages are asynchronous**: sent ≠ read ≠ acted upon;
4. **Turn over ≠ session over**: context must travel with the person.

### 1.6 Conflict resolution
**Whichever option introduces fewer entities wins.** If still unclear, pick the one you can write into a CHANGELOG "decision rationale" and still understand three months later.

---

## 2. Delivery flow (six gates + checklists)

Tick every item before leaving a gate. Nothing passes unverified.

### Gate 1 · Requirement confirmation
- [ ] Requirement written down (roles / rules / task / success criteria)
- [ ] Success criteria are verifiable
- [ ] Boundaries and red lines identified
- [ ] Reuse check: is there already a wheel? (do not rebuild)

### Gate 2 · Design
- [ ] At least two options with reasons for rejection
- [ ] Five questions answered (why do it / why this way / boundaries / how to verify / when to stop)
- [ ] Minimum-entity review
- [ ] High-risk items marked with approval points

### Gate 3 · Implementation
- [ ] Touch only what must be touched (surgical edits)
- [ ] Incremental, idempotent, resumable
- [ ] A single failure does not abort the batch; it is recorded in the summary
- [ ] Comments explain "why", not "what"

### Gate 4 · Self-test
- [ ] A failing case proves the fix
- [ ] Return codes checked (silent failures must be surfaced)
- [ ] Retrieval/aggregation results compared against expectations
- [ ] Visual check before delivery (ran successfully ≠ correct content)

### Gate 5 · Consolidation
- [ ] Material passes the three-question filter; lands in `raw/` + `wiki/`
- [ ] If it is a rule → bump the rulebook version; if it is knowledge → write the wiki page
- [ ] Rebuild the index (`kb_index build` or equivalent)
- [ ] Write the log and the memory note

### Gate 6 · Release
- [ ] Version triple (VERSION + CHANGELOG with rationale + backup snapshot)
- [ ] Final human verification of external content
- [ ] Private data stripped (de-identified)
- [ ] Release notes (md) shipped alongside the assets

---

## 3. How this connects to the knowledge base

When a gate closes, write **that gate's decisions and pitfalls** into `wiki/` (project notes) or the "pitfalls" section of `modules/<module>/README.md`. The compounding value of a knowledge base comes from this: **insight produced by the process immediately becomes searchable knowledge**.

## 4. Metrics and retrospectives

- Weekly: consolidation-gate self-check (claimed vs actually filed);
- Monthly: review red-line touches, reuse rate, rework rate;
- Anyone may propose a "subtraction proposal" (approved by L1/L2/L3 tier), but the default posture is "add, don't subtract".
