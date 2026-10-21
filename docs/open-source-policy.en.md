# Open-source policy: how I picked KBL-1.0 out of a pile of existing licenses

[简体中文](开源守则.md) ｜ **English**

> For independent authors who want to be "open but not free-for-commercial-exploitation".
> Bottom line first: **no existing license satisfies all four of "no commercial use + mandatory attribution + covers code and docs + authoritative Chinese text"**,
> so I assembled one from mature licenses: **KBL-1.0**.

---

## 1. Decide what you actually want people to do

Choosing a license is not choosing a stance, it is choosing **boundaries**. Answer four questions first:

| Question | My answer | What it decides |
|---|---|---|
| May others read, learn and use it for free? | Yes — the more people build on it the better | Must grant copy / modify / personal-use rights |
| May others sell it? | No | Must prohibit commercial use |
| Must others credit me when reposting or remixing? | Yes, with a source link | Must add an attribution clause |
| May others re-license it as MIT and republish? | No — that washes out attribution | Must add share-alike |

Once those four answers are fixed, the set of usable licenses is already very small.

---

## 2. Eliminating options: six families I examined

Mainstream GitHub projects fall into roughly six families. I tried each one:

### 2.1 MIT / Apache-2.0 / BSD — eliminated immediately

The most common choice on GitHub, and the default for most "tool libraries".

- ✅ Best ecosystem fit; companies dare to use it; lawyers do not need to review it
- ❌ **Explicitly permits commercial use, closed-source forks, and republishing after stripping attribution**
- ❌ Directly conflicts with my two hard requirements: "no commercial use" and "credit the source"

> A typical outcome: you release under MIT, and the next day someone sells your templates on a marketplace — and you have no standing to complain.

### 2.2 GPL-3.0 / AGPL-3.0 — eliminated

Many people believe GPL blocks commercial use. That is a misunderstanding.

- ✅ Forces derivative works to stay open (copyleft)
- ❌ **Does not prohibit commercial use**, only requires source disclosure. Others can still sell it as long as they ship the source
- ❌ AGPL only adds "network services must also publish source", and still does not block selling

> If your goal is blocking commercial use, the entire GPL family is ineffective.

### 2.3 CC BY-NC 4.0 / CC BY-NC-SA 4.0 — partly usable, unsuitable for code

Creative Commons' non-commercial variants, most common in documentation.

- ✅ A mature NonCommercial definition, internationally recognized, with an official Simplified Chinese translation
- ✅ BY (attribution) + SA (share-alike) match my requirements exactly
- ❌ **CC explicitly does not recommend its use for software**: no patent grant, no source-distribution clause, incompatible with GPL and others
- ❌ This repository is a code + documentation hybrid; CC alone leaves a gap on the code side

### 2.4 PolyForm Noncommercial 1.0.0 — best structure, but it protects notices, not attribution

A non-commercial license designed for "software + documentation + content". Structurally the most rigorous I have read.

- ✅ Clear definitions (Noncommercial Purposes / Personal Uses / Noncommercial Organizations, written in three layers)
- ✅ Explicitly allows personal research, experimentation, hobby projects, and charitable/educational/government use
- ✅ Fine-grained, enforceable clauses; SPDX identifier `PolyForm-Noncommercial-1.0.0`
- ✅ Has a `Notices` clause: distributions must carry the license text/URL plus the original `Required Notice:` line
- ❌ **But `Notices` is "retain the notice", not "publicly credit the author"**:
  it governs "do not delete the copyright information", not "when you write an article or make a video about this, do you name the author".
  What I wanted is exactly the latter — **reposts, lectures and remixes must credit the source**.
- ❌ No official Chinese text, so Chinese readers can misread the scope of "noncommercial"

> This is the direct reason I ended up writing my own license: **retaining a notice ≠ mandatory attribution**.
> The former prevents copyright info from being erased; only the latter tells people who made the thing.

### 2.5 Source-available commercial licenses — reference for drawing boundaries

Elastic License 2.0, Business Source License 1.1, the Sustainable Use License (n8n), Commons Clause — licenses that are "not open source but source-visible". I mainly studied **how they draw commercial boundaries**:

- Elastic License 2.0: prohibits "providing the software as a hosted service" — very precise wording
- BSL 1.1: time-lock (converts to open source after some years) — good idea, unnecessary for a personal project
- Sustainable Use License: prohibits "competing with the author's product" — too subjective to enforce

What I learned: **you cannot prohibit commercial use with a single sentence**. You must enumerate real scenarios —
SaaS, paid consulting, bundling into a paid product, use as training data — otherwise every dispute becomes an argument about interpretation.

### 2.6 Custom license + Chinese counterpart — the path taken

GitHub's documentation explicitly supports custom licenses (`LicenseRef-*` naming); it just will not show a green badge.

- ✅ All three hard requirements can be written at once
- ✅ Bilingual, with Chinese prevailing, so domestic readers have no ambiguity
- ❌ GitHub shows "Unknown license" in the sidebar, which the README must explain
- ❌ No "default compliance" trust from the OSI ecosystem

---

## 3. KBL-1.0 = the best of three

The final result is an assembly, not an invention:

| Source | What was borrowed |
|---|---|
| **PolyForm Noncommercial 1.0.0** | Overall skeleton: definitions → grant → conditions → prohibitions → termination → disclaimer, plus the definition style for Noncommercial / Commercial Purpose |
| **CC BY-NC-SA 4.0** | Attribution mechanics (BY) and share-alike (SA): what an attribution notice must contain and where it must appear to count |
| **Elastic License 2.0 / SUL** | Enumerating commercial prohibitions (8 typical scenarios + a 30-day evaluation window) |
| **GPL-3.0** | Termination and cure mechanics (automatic termination on breach + 30-day cure to reinstate) |

Where the three hard requirements landed:

| Requirement | Clause |
|---|---|
| **No commercial use** | Section 4 (8 prohibitions + 30-day evaluation window) + Section 5 (commercial licensing channel) |
| **Mandatory attribution** | Section 3.1 (retain notices) + Section 3.2 (five elements of an attribution notice + placement) |
| **Credit the source** | Section 3.2(d) original URL + Section 3.4 (no re-licensing to wash out attribution) |

---

## 4. One key trade-off: why not simply use CC BY-NC-SA

If this repository were **documentation only**, I would use CC BY-NC-SA 4.0 — well-argued, internationally recognized.

But it is a **code + documentation hybrid**:

- `kb-hub/` is a running program → needs software-grade clauses on source distribution, patents and warranty disclaimer;
- `templates/` and `docs/` are documentation → need attribution and share-alike;
- `kb-hub.exe` is a binary → needs a clear statement of the PyInstaller / Python third-party license relationship (see section 3 of `NOTICE`).

One license covering all three is far clearer than "CC0 for code + CC BY-NC-SA for docs + something else for the exe" —
**users should not have to read three licenses to legally use one repository**.

---

## 5. What this means in practice: three things you get automatically

While using the system yourself, **you do not need to do anything** (personal, non-commercial, no redistribution).

Once you redistribute, remember three lines:

1. **Name it**: FallenPetal · knowledge-base-from-zero
2. **Link it**: the original repository URL
3. **Say what changed**: one sentence (derivatives only)

Copy-paste templates → [`NOTICE.en.md`](../NOTICE.en.md) section 1 (repository / article / video / application).

---

## 6. Common questions

**Q1: Is using it to build an internal company knowledge base commercial use?**
It depends on whether you run it in production long-term. Section 4 grants a **30-day evaluation window** (not in production, no revenue).
Beyond that it is an "internal production deployment" and requires a commercial license. Personal learning is unaffected.

**Q2: I present this method internally at my company — do I need a license?**
No fee to the audience → non-commercial, allowed, attribution required.
If the company charges for it or sells it as paid training → commercial use, license required.

**Q3: Can I fork it, rename it and publish it?**
You may fork. You **may not remove the original attribution** (sections 3.2, 3.4). The correct approach is to keep the original credit and add "modified".

**Q4: I read the code and rewrote it in my own words — am I still bound?**
Ideas and methods are not protected by copyright; **specific expression** is. If you only took the methodology and implemented it independently,
this license does not bind you — but crediting "method inspired by ..." is good practice and good citation hygiene.

**Q5: Why does GitHub show "Unknown license"?**
Custom licenses are not in GitHub's detection database. This is expected, and the README states it explicitly.
If the green badge matters to you, switch to PolyForm Noncommercial (which is detected) at the cost of losing the attribution clause.

**Q6: Is this license legally enforceable?**
It is a copyright license contract and is valid in most jurisdictions. But **the author is not a lawyer**, and this is not legal advice.
For commercial use or a dispute, both parties should consult their own counsel.

**Q7: May I train AI models on it?**
Training commercial models → prohibited (section 4, item g). Model training for personal research → allowed.

---

## 7. For anyone writing their own policy: a checklist

Go through it in order; half an hour is enough:

- [ ] 1. List five "allowed" and five "forbidden" scenarios you care about (concrete actions, not abstractions)
- [ ] 2. Classify the work: pure code / pure docs / hybrid / includes binaries
- [ ] 3. Check whether an existing license covers 80%: MIT (don't care) → Apache-2.0 (need patents) → GPL (need copyleft) → CC BY-NC-SA (docs, non-commercial) → PolyForm NC (software, non-commercial)
- [ ] 4. Only patch the remaining 20%; do not invent from scratch
- [ ] 5. Write "commercial use" as a **decidable list of actions**, not one sentence saying "no commercial use"
- [ ] 6. The attribution clause must specify **five elements + where to place them**, or it cannot be enforced
- [ ] 7. Add share-alike, otherwise downstream strips your attribution
- [ ] 8. Write termination and cure (30 days) so both sides have a way back
- [ ] 9. Bilingual, and state which version prevails on conflict
- [ ] 10. List third-party dependencies separately in NOTICE, never mixed into the main license
- [ ] 11. Put one sentence at the top of the README: what you may and may not do — do not make people read 150 lines of English
- [ ] 12. Run a privacy scan before publishing (`tools/privacy_scan.py`), or the policy becomes a joke

---

## 8. References (actually fetched and verified on 2026-10-21)

| Source | Link | Key point verified |
|---|---|---|
| Choose a License (GitHub's official chooser) | https://choosealicense.com/ | Official entry point for the mainstream license comparison table |
| PolyForm Noncommercial 1.0.0 text | https://polyformproject.org/licenses/noncommercial/1.0.0 | **Has a `Notices` clause, but it only requires passing along the license text/URL and the `Required Notice:` line** — no public attribution; breach has a 32-day cure period |
| PolyForm license sources (GitHub) | https://github.com/polyformproject/polyform-licenses | Markdown originals of every version, for clause-by-clause citation |
| CC BY-NC-SA 4.0 official Simplified Chinese | https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans | Official Chinese wording of the four conditions: attribution / non-commercial / share-alike / no additional restrictions |
| Elastic License 2.0 | https://www.elastic.co/licensing/elastic-license | Reference wording for "may not provide the software to others as a hosted service" |
| Business Source License 1.1 | https://mariadb.com/bsl11/ | Time-lock mechanism (automatic conversion to open source) |
| n8n Sustainable Use License | https://docs.n8n.io/ (the license page moves with doc revisions; search the site for "Sustainable Use License") | The "no competing with the author's product" wording; too subjective, not adopted |
| PyInstaller License | https://pyinstaller.org/en/stable/license.html | **Dual license: GPL 2.0 + bundling exception, with a few files under Apache-2.0**; officially states bundled output may use any license and needs no credit |

> Links and clauses may change between versions; always cite the latest official text.
> The table above was opened and verified on 2026-10-21 — not written from memory. Writing a license is the last place to rely on "I vaguely remember it being like this".

---

## 9. One-sentence summary

**MIT says "use it however you like". GPL says "use it and you must open-source". CC BY-NC says "no commercial use, but credit me".**
**KBL-1.0 says "no commercial use + mandatory attribution + no stripping attribution + one license for docs, code and binaries".**

I chose it not because it is more advanced, but because it lands exactly on the line I need to hold.
