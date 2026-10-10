# GI-Hub — the case for company-wide implementation

*A 10-minute presentation for the Finance Manager, with a live demo on the
Practice sandbox. Prepared 2026-10-04 (Phase 20); Phases 22–23 added
2026-10-10 (§3A, measured numbers only — ruling Q23-16).*

**The slide deck** (13 slides, GI branding, speaker notes on every slide;
downloads as PowerPoint or PDF from its page):
<https://claude.ai/artifact/FZRtKrYqn6e5zoD8Mx3TVn>. It is private until you
share it from its Share menu.

> **How to read the numbers in this document.** Every figure is one of three
> kinds, and is labelled:
> - **[measured]** comes from GI-Hub itself or its test suites;
> - **[public]** is a published external figure, with its source in §9;
> - **[operator]** is a placeholder **you fill in** before presenting.
>
> Nothing here is an estimate dressed as a fact. A finance audience will test
> one number; make sure that one is solid.
>
> **Confidentiality:** no supplier prices and no real site names. Sites are
> "Site A", "Site B". The Practice sandbox's site code appears on screen during
> the demo; call it "Site A".

---

## 1. The one-page summary (the slide to leave on screen)

**The problem.** Stock, consumption and lining progress were kept across Excel
workbooks and memory. The same drum could be deducted twice. A *can* was
sometimes read as a *kilogram*. Nobody could say, on a given day, what was
used for which m² and whether a manager had approved it.

**What GI-Hub is.** One system, on our own server, used by:
- **store keepers**: receive, issue, return, count;
- **supervisors**: report the work and the m² done;
- **HODs**: approve, plan, order;
- **Logistics**: purchase requests, POs, deliveries;
- **auditors**: read everything.

Every movement of material is recorded once, approved by the right person, and
traceable.

**Three headline numbers** (fill before presenting):

1. **[operator]** of stock value now tracked by lot, location and expiry.
2. **[operator]** hours a week no longer spent consolidating Excel.
3. **[operator]** of material value protected from double counting and unit
   errors, from your own write-off history.

**The ask:** approve company-wide rollout, starting with a pilot at one site,
with a hosting budget of about **€69.49 a month** for one server
[public, Hetzner CPX42].

---

## 2. Presentation outline (10 minutes)

| # | Slide | Minutes | Purpose |
|---|---|---|---|
| 1 | Title — GI-Hub | 0:30 | Logo, one line: *one source of truth for material and progress* |
| 2 | The problem, as one story | 1:00 | The drum counted twice / the can booked as a kilogram — with its cost [operator] |
| 3 | What GI-Hub is | 1:00 | One system, five roles, one ledger, on our own server |
| 4 | Where the money is (1): stock you can trust | 1:30 | Unit Size, one deduction per drum, FEFO lots |
| 5 | Where the money is (2): buy the right amount | 1:30 | Smart minimums, HOD-accepted minimums, on-order per site |
| 6 | Where the money is (3): progress you can audit | 1:00 | The Surface Shield daily log: m² per job, approved by a named HOD |
| 7 | Control & audit | 1:00 | Segregation of duties, approvals, full audit trail |
| 8 | Risk, security & quality | 1:00 | Own server, 2FA, site walls, backups; thousands of automated checks |
| 9 | Cost of ownership | 0:30 | €69.49/month server, no per-user licence |
| 10 | Rollout & the ask | 1:00 | Pilot → sites; KPIs; what we need from Finance |
| — | (Live demo, separate) | 8–10 | §5 — on the Practice sandbox |

---

## 3. Talking points: what each capability is worth

| Capability | In plain words | Financial or control win | Evidence |
|---|---|---|---|
| **Packs ⇄ KG from Unit Size** | A *can* is never mistaken for a *kilogram*. Stock stays in the unit it is bought in, and weights are worked out from each item's Unit Size. | No write-offs or phantom shortages from unit confusion. | Every Surface Shield comparison is made in one unit; a missing Unit Size never defaults to 1 [measured: Phase 14, rule P14-units]. |
| **One deduction per drum** | The Excel log and the paper form can no longer both deduct the same material. | Stock value is not understated; jobs are not charged twice. | Four double-count paths were found and closed [measured: Phase 14]. |
| **FEFO lots and expiry** | The oldest stock is issued first, and expiring stock is flagged in advance. | Less scrap of expired material. Carrying cost is typically **20–30 % of inventory value a year** [public], so every month of avoidable stock costs money. | Lots & Expiry page; FEFO-ordered issue. |
| **Intelligent minimum stock** | The system works out a minimum for every item from its use (general items) or from the remaining m² plan (Surface Shields), and shows red/amber/green with a suggested order. | Less over-stock (working capital) and fewer stock-outs (idle crews). | Reorder signals; manual minimums still win [measured: Phase 18]. |
| **HOD-accepted minimums per site** | The HOD accepts the recommendation, or edits it, for their site. The system flags it when use changes by more than 20 %. | Planning decisions are recorded, with who and why. | Audit trail of every acceptance [measured: Phase 19]. |
| **On order per site** | A PO counts for the site that raised it. A global PO is shown but subtracted from nobody. | One PO is never counted against several sites, so there is no false "covered". | [measured: Phase 19] |
| **Surface Shield daily log** | Per day and job: what was drawn, the store keeper's own remark, the m² done, and the HOD's decision. | Material per m² is visible and explained, and goes into the weekly executive email. | Approved / pending / rejected per job [measured: Phase 20]. |
| **Every Surface Shield draw approved by an HOD** | Variances over 10 % are flagged and named before approval. | Over-use is caught while it can still be corrected. | Bulk approval still lists every high variance [measured: Phase 20]. |
| **Return desk, loan slips, daily chaser** | Tools are scanned back in, with a slip and QR code. Borrowers are reminded every day, and the HOD is told after 3 days. | Fewer lost tools; damaged returns reach the HOD. | [measured: Phases 18–19] |
| **OCR of paper forms + bulk approvals** | Photograph the form; approve many jobs in one click. | Supervisor and HOD hours saved. | [operator: hours/week] |
| **An assistant on our own server** | Answers "how do I…" from the manual and routes questions, guarded against misuse. | No per-seat AI fees. By default, data stays in-house. | A cloud reader for scanned forms exists but is **off and unconfigured**; live-stock questions have **no cloud route** by policy. The assistant is tested on every change [measured]. |
| **Practice sandbox** | A full copy with fake data, for training. | Train people with no risk to real stock. | A separate database and process [measured: rule 17]. |
| **Engineering quality** | A change cannot ship if a check fails. | Lower running cost, fewer incidents. | **2,890** automated service checks, **190** browser tests, **599** legacy checks, required checks on every change. Tests never touch the live database (rule 15) [measured]. |

**Turning one capability into money (worked example — fill the [operator] values):**

> Average Surface Shield stock: **[operator] SAR**. Carrying cost at 25 %
> [public, mid-range] = **[operator × 0.25] SAR a year**. If smart minimums let
> us hold **[operator] %** less on average, the saving is **inventory ×
> reduction × 25 %** a year. Add any write-off from unit or double-count
> errors you have on record: **[operator] SAR**.

---

## 3A. Phases 22–23, measured (October 2026)

Everything in this section is **[measured]** on the office Mac, on 7–10
October 2026, from GI-Hub itself. Where a figure needs one of your own numbers
it says **[operator]**; nothing here is projected.

**The paper trail, from Google Drive (Phase 22).**

| What | Measured |
|---|---|
| Delivery-note photos in Drive that open from their receipt | **152 of 160** (95 %) [measured, Live, 10 Oct] |
| Deliveries that came with no note | each has a **WD number**, so it can still be found |
| Certificates (MTC) filed on their lot | **11 of 24** files; the rest wait for a person (no batch on the page) [measured] |
| Request lines read from the request workbooks | 313 lines, 265 matched to a SAP code on day one [measured, 7 Oct] |
| Request lines still needing a SAP code, after the catalogue | **82 → 15**; 33 more carry a catalogue code not stocked yet [measured, 9 Oct] |

**Reading the handwritten consumption papers (Phases 21–23).** The same
photographs, scored against what the store keepers typed into the workbook
(`tools/ocr_eval.py`):

| Papers | Lines found (recall), before → after the second read | Correct lines (precision) |
|---|---|---|
| 1–4 Oct (11 photos) | 0.742 → **0.761** | 0.79 → 0.773 |
| 5–6 Oct | 0.735 → **0.767** | 0.764 → 0.767 |
| 7–8 Oct | 0.855 → **0.867** | 0.876 → 0.856 |

- **The tank on each line:** right **0.42 → 0.81** once one tank is chosen for
  the whole page; lines entirely right **32 → 52** (5–6 Oct).
- **The second read** costs the store keeper **0 seconds**: it runs after the
  first read, in the background, about 14 seconds a page.
- **Time per page to read:** 92 to 398 seconds on this Mac's local AI
  [measured, 2 Sep]. The paper never leaves the building.
- **Minutes a store keeper spends typing one paper by hand:** **[operator]**.
  Multiply by papers a day for the hours this saves; we have not measured it,
  so we do not quote it.

**Buying the right thing (Phase 23).** The company's **5,976** material codes
are in GI-Hub's catalogue, with the 37 plant and tool lines of the site list
[measured, Live]. A purchase request now carries the item's **picture**, and a
HOD can request an item the site does not stock yet without anyone inventing a
SAP number. The pictures are being added (the operator's Drive folder); the
benefit is fewer wrong sizes and wrong grades delivered — **[operator]**: one
wrong delivery you remember, and what it cost.

**Voice.** Speech is turned into text on the office Mac in **15 ms** a phrase,
with a word-error rate of **0.14** on twenty site phrases, using 88 MB of
memory and no internet [measured, `tools/stt_eval.py`].

---

## 4. Risk, security and control (the questions Finance always asks)

| They ask | The answer |
|---|---|
| **What does it cost?** | One server, about **€69.49/month** [public]; open-source software, no per-user licence; plus **[operator]** of support time. |
| **What if it breaks?** | Database snapshots on demand or scheduled daily (`bin/backup_db.sh`, `--install` for 02:00 every day — **[operator: confirm it is scheduled on the server]**), a written restore command for each snapshot, and an API that refuses to start on a mismatched database. **[operator: target recovery time]** |
| **Who maintains it?** | **[operator]**. Every change goes through a pull request with three required automated checks, and changes can be reverted. |
| **How do we know the numbers are right?** | The same calculations are checked by thousands of automated tests on every change. The SME estimator is computed by two independent engines that must agree (1,334 comparisons) [measured]. |
| **Who can do what?** | Each role sees only its own pages and its own site, and approvals are separated from data entry. Every approval is in the audit log. |
| **Security?** | Two-factor sign-in, rotating sessions, site walls, rate limits, maintenance mode, and an audit log of every change. The live database is never used by tests. |
| **What if the internet drops at a site?** | Entries made offline are queued and sent once, never twice [measured: Phase 14]. |

---

## 5. Live demo script (Practice sandbox, ~8 minutes)

**Set-up:**
- The night before, rebuild Practice so every figure is fresh:
  `.venv/bin/python tools/practice_db.py build`. It resets trainee data.
- Open GI-Hub on **localhost**, in Practice mode, for the introduction.
- Accounts are `practice.*`: the Practice sign-in screen lists them with one-click
  sign-in (Phase 23). Or let the app present itself: **▶ Auto demo → Management
  tour** walks the whole system with captions and voice.
- For the multi-user part on the hosted site, sign in with the **Practice**
  accounts too, never Live accounts, so production data is untouched.

| # | Who | What you do | What you say |
|---|---|---|---|
| 1 | practice.storekeeper | **Issue** a Surface Shield item: packs and KG side by side, and the oldest lot suggested first. | "A can is never a kilogram, and the oldest stock goes first." |
| 2 | practice.supervisor | Execution → **Needs an area**: the store keeper's remark (*"Coving - 4 SQM Done"*) has filled in the m². **Select all ready → Submit selected to HOD.** | "The field's own words become the record, and ten jobs go in one click." |
| 3 | practice.hod | **Awaiting the HOD** → filter a date → **Approve selected**. The confirmation names the job with a high variance. | "Every square metre is approved by a named person who can see the variance." |
| 4 | practice.hod | **Surface Shield → Daily Log**: approved, pending and rejected by day, with remarks as typed; one job shows *"⚠ remark says 8"* with the HOD's reason. Export PDF. | "This is what Finance would read, and it arrives every Friday by email." |
| 5 | practice.logistics | **Stock → Reorder signals**: red/amber/green, suggested order, on order per site, "+ N global". | "The system says what to buy, and what is already coming." |
| 6 | practice.hod | **Review minimums**: tick, edit, accept; *SAFETY GLASSES* shows **changed**. | "Planning decisions are recorded, and use changes are flagged." |
| 7 | practice.storekeeper | **Return desk**: scan *PR-SC-0005*, take 1 back (a partial return), print a slip. | "Tools come back, or we know who has them." |
| 8 | any | Hub Assistant: a how-to, then a trick question that is refused. | "An assistant on our own server, guarded and tested." |
| 9 | practice.admin | Audit log of the last ten minutes. | "Every click you just saw is on record." |
| 10 | practice.hod | **Catalogue** → a rubber sheet → its family picture; then a PR for an item not stocked yet, with its picture. | "Logistics buys what the site means — the picture travels with the request." |
| 11 | practice.hod | **Requests & Pending → Needs a SAP code**: link a line, then Undo. | "A request line counts against stock the moment it is linked, and the workbook is never touched." |

**Fallback, if anything misbehaves:**
- Switch to the pre-recorded walkthrough videos (`docs/exec_video/`, and the
  management tour in `docs/tutorials/out/`);
- or show the screenshots in the appendix.
- Never improvise on Live.

---

## 6. Rollout proposal

1. **Pilot (4–6 weeks), one site.**
   - Success criteria (baseline first, [operator]):
     - stock-count accuracy ≥ **[operator] %**;
     - zero double deductions;
     - approval lead time ≤ **[operator] days**;
     - Excel consolidation hours down **[operator] %**.
2. **Training** on the Practice sandbox, plus the built-in video tutorials, per
   role.
3. **Site-by-site rollout**, each with the same KPIs on the weekly executive
   email.
4. **Review** at 3 months: the KPIs against baseline, decided by Finance.

**KPIs to track:**
- stock accuracy %;
- write-offs (SAR);
- days of cover;
- stock-outs that stopped work;
- PR → PO time;
- approval lead time;
- m² per KG of Surface Shield (variance).

---

## 7. The ask

- **Approval** for company-wide implementation after the pilot.
- **Hosting budget:** about **€69.49/month** [public] (one CPX42 server)
  **+ [operator]** for backups and the domain.
- **A pilot owner** at one site, and a Finance contact for the KPI review.

---

## 8. Ideas for presenting it

- **Open with the story, not the software.** Tell what one double-counted drum
  or one can-for-KG mix-up cost. Then show it can no longer happen.
- **Three numbers, no more**, on the first slide, all from your own records.
- **Speak the language of control:** who approves what, what is logged, what
  the weekly email shows. That is what a Finance Manager signs off.
- **Show a phone.** A WhatsApp notice arriving live (an overdue tool, an
  approval) makes it real.
- **Leave behind the weekly Executive Summary PDF.** It shows "this is what
  you would get every Friday".
- **Close with a pilot and its success criteria**, not a big-bang rollout.
- **Rehearse once, end to end**, after the Practice rebuild, with the accounts
  logged in in separate browser windows.

---

## 9. Sources (public figures)

- Hetzner, *Price adjustment, 15 June 2026*: CPX42 at **€69.49/month**
  excl. VAT (US-region table; the Germany/Finland figure is confirmed by
  third-party trackers). Re-check before quoting.
  <https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/>
  · <https://costgoat.com/pricing/hetzner>
- Inventory carrying cost **≈ 20–30 % of inventory value per year** (industry
  benchmark; varies with product and supply chain):
  <https://www.fishbowlinventory.com/blog/what-is-carrying-cost> ·
  <https://www.vndly.io/blog/inventory-carrying-cost-statistics-2026>
- Everything marked **[measured]** comes from this repository: the test gate
  results recorded in `SESSION_HANDOVER.md`, and the decisions in
  `PROJECT_HANDOVER.md`.
