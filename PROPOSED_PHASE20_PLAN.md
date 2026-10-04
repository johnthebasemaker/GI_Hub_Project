# PROPOSED PHASE 20 — Pre-presentation polish & bulk workflows

*Drafted 2026-10-04 from the operator's brief. **Planning only — no application
code until the operator approves this plan and rules on §5.** Each track lists
what exists today (read from the code, not assumed), the design, the tests and
the docs, and the decisions it needs.*

The brief, in one line each:

1. **Track 1:** make Surface Shield consumption and SQM done easy for management
   to read: grouped by date, the HOD's decision visible, and the Excel
   **Remarks** (where the SQM comes from) shown as written.
2. **Track 2:** bulk **Submit selected to HOD** for the field, and bulk **Approve
   selected** for the HOD.
3. **Track 3:** a `FINANCE_PITCH.md` for the Finance Manager: an outline, the
   features translated into money and control, and a live demo script on the
   Practice sandbox.

---

## 0. What exists today (the ground truth this plan builds on)

| Piece | Where | What it does now |
|---|---|---|
| Consumption ledger | `consumption` (Excel log synced by `tools/pg_excel_sync.py`) | One row per draw. **`Remarks`** holds the field's words, e.g. *"Floor - 13.37 SQM Done"* or *"LS LSC5 …"* |
| Remark → SQM | `services/sme_groups.parse_note` / `sqm_hint` | Reads *"Floor - 13.37 SQM Done"* as area **Floor**, **13.37 m²**. ⚠️ A **pre-fill**, never the record (Phase 15e) |
| Attribution of a draw | `sme_consumption_log` | One row per material: system code, tag, SQM, variance, **status `staged` / `committed` / `rejected`**, `notes` (the remark as submitted) |
| A job | `sme_attribution_group` (Phase 14c) | One day on one equipment: one system code, **one SQM**, `status`, `submitted_by`, `hod_username`, `hod_decided_at`, `rejected_reason`, `Work_Area`, `notes` |
| Field queue (SK / supervisor) | Execution → *Surface Shield consumption* → **Needs an area** (`SmeJobs.tsx` `JobQueue`) | One **card per (date, equipment)**. Suggested system code, SQM pre-filled from the remark, materials ticked. **Submit is per card** |
| HOD queue | the same card → **Awaiting the HOD** (`HodJobs`) | One card per job, **Review → Approve/Reject the whole job**, one at a time. Approval credits the job's SQM **once** (defect D3) |
| Execution entries (paper form/OCR) | Execution page table | `DRAFT_SUPERVISOR → PENDING_SK → PENDING_HOD → APPROVED/REJECTED`. **HOD approval posts the stock AND the area.** One modal per entry |
| Bulk-approve precedent | `POST /hod/pending/{kind}/bulk-approve` + `ApprovalsPage` | Ticks + *Approve selected*. **Each id in its own transaction**: one bad row fails alone, the rest land. Cap 200. Per-id results |
| History views | SME → SQM Progress / Equipment Report / Actual Consumption; Records → Consumption | ⚠️ **No single view** shows, per day: what was drawn, the remark, the SQM done, and whether the HOD approved it. Those four facts live in three tables, which is why management cannot read them |

---

## 1. Track 1 — the Surface Shield daily log (UX)

### 1.1 The view

A new **"Surface Shield daily log"**, read-only, built for someone who has never
opened the SME portal:

```
┌ KPI strip (the chosen period) ─────────────────────────────────────────────┐
│ SQM approved 412.5 · pending HOD 38.0 · rejected 6.2 · not yet filed 9 jobs │
│ Surface Shield drawn 1,840 KG (92 packs) · High-priority variances 3        │
└─────────────────────────────────────────────────────────────────────────────┘
▼ Wed 01 Oct 2026   3 jobs · 51.4 m² approved · 13.4 m² pending · 220 KG drawn
   ● APPROVED   TK-091 · LSC5 · Floor     "Floor - 13.37 SQM Done"   13.37 m²   by hod, 02 Oct 09:14
   ◐ PENDING    TK-104 · LSC2 · Shell     "Shell 38 m2 done"         38.00 m²   with the HOD since 01 Oct
   ✕ REJECTED   TK-077 · LSC5             "Dyke wall patch work"     —          "No area stated" — by hod
      └ (expand) materials: SAP · description · 4 cans = 80 KG · lot · issued by
▶ Tue 30 Sep 2026   …
```

* **Grouped by DATE → JOB (equipment + system) → MATERIALS** (expand). This
  is the grouping the field already works in (Phase 14c), so a number here
  always matches a card there.
* **One status badge per job, in plain words**:
  * ✅ **Approved**: committed, with who and when.
  * 🟡 **Pending HOD**: staged, with since when.
  * 🔴 **Rejected**: with the HOD's reason.
  * ⚪ **Not yet filed**: in the field queue, no SQM yet.
  * 🔵 **Edited in Excel after approval**: a staged revision; the approved
    figures still count.
* **The Remark is shown exactly as the store keeper typed it** in the Excel
  log, in quotes, never paraphrased. Beside it are the SQM read from it and the
  SQM approved. When they differ, an amber marker says *"remark says 13.37,
  approved 12.0"* with the HOD's justification on hover. **That difference is
  what management will ask about.**
* **Units**: packs **and** KG (`services/units`, P14-units: converted on read,
  a missing Unit_Size never defaults to 1).
* **Filters**: period presets (this week · this month · custom), equipment,
  system code, status, site (unscoped roles only).
* **Export**: Excel and a printable PDF of exactly what is on screen, for the
  Finance Manager's pack (`reports.py`'s `to_xlsx` / `exec_pdf`-style table).
* **Garnet (surface prep)** in its own section or filter (it credits no lining
  SQM: Phase 15d).

### 1.2 Backend

* `GET /execution/sme-link/history?from&to&site_id&status&tag&code`, one
  read-only, site-scoped endpoint. It joins `consumption` (Surface Shield
  category, `Remarks` verbatim), `sme_consumption_log` and
  `sme_attribution_group` (status, SQM, system, work area, decided by/at,
  reason), plus the queue's unfiled groups, and returns `days → jobs →
  materials` plus the KPI totals. **No schema change.**
* The default window is 30 days, with a hard cap on rows. The indexes on
  `consumption("Date")` and the group `Work_Date` will be checked with EXPLAIN
  on Live-sized data first.
* ⚠️ The remark is rendered as **text, never HTML** (it is free text from a
  workbook).

### 1.3 Polish on the existing cards (small)

* The HOD's **Awaiting** cards and the field's **Needs an area** cards show the
  remark verbatim in a quoted line at the top (today it sits inside the note
  dropdown), plus the same status vocabulary.

### 1.4 Tests and docs

* Suite **20A**:
  * grouping (`date → job → materials`);
  * each of the five statuses;
  * the remark returned byte-for-byte;
  * remark SQM vs approved SQM flagged;
  * KG = packs × Unit_Size;
  * the site wall;
  * no writes.
* **E2E**: the log shows an approved, a pending and a rejected job with their
  remarks; export downloads.
* **Practice (rule 17g)**: guarantee one job in each status (today's overlay
  has staged and rejected jobs; add approved and "edited in Excel" examples).
* USER_MANUAL (a new section for management, written for non-technical
  readers) and MANUAL_TESTING_GUIDE §20a.

---

## 2. Track 2 — bulk submit and bulk approve

### 2.1 Field side (SK / supervisor): "Submit selected to HOD"

On **Needs an area**:
* each job card gets a **checkbox**, and the toolbar gets **Select all ready**
  and **Submit selected to HOD (N)**;
* a card is **ready** when it has a system code, an SQM, at least one ticked
  material and, for Garnet, Old/New;
* a card that is not ready shows *why* ("no SQM in the remark — type one") and
  its checkbox is disabled. ⚖️ Q20-8.

Each selected card submits **with what it currently shows**: the suggested (or
edited) code, the SQM from the remark (or typed), the ticked materials, the
work area and the remark. **A one-click review modal** lists them first: N jobs,
total SQM, any card whose suggestion was not touched by a human. **This keeps
"a pre-fill, never the record" true:** the person still confirms, once for the
batch instead of once per card.

* **Backend**: `POST /execution/sme-link/groups/bulk-submit` `{jobs:[…]}` (≤ 50)
  → for each job, `sme_groups.submit()` inside its **own savepoint**. A job
  that fails (rows already taken by another user, a code not in the recipe, a
  site mismatch) is **skipped with its reason**; the rest go through. **The HOD
  is notified once per batch**, not once per job.

### 2.2 HOD side: "Approve selected"

On **Awaiting the HOD**:
* checkboxes, **Select all**, filters by **date** and **system code** (as
  asked), and **Approve selected (N)**;
* a confirmation modal shows N jobs, the **total SQM that will be credited**,
  and how many are **High Priority** (variance outside ±10 %).

* **Backend**: `POST /execution/sme-link/groups/bulk-decide` `{ids, approve,
  reject_reason?}` → for each group, `sme_groups.decide_group()` in its **own
  transaction**, the same shape as `/hod/pending/{kind}/bulk-approve`.
* **Mixed statuses and errors**: each id is re-read and locked first. Already
  decided, another site's, or gone → **skipped with the reason** ("already
  approved by hod at 10:02"). The response lists approved, skipped and failed,
  and the screen shows the same list. **Idempotent**: approving the same ids
  twice credits SQM once.
* **Audit**: one `GROUP_DECIDE` row per job (as today) plus one batch id
  linking them. **Notifications**: one summary per submitter.
* **Excluded from bulk** (they are different decisions): Excel-edit
  **revisions** (before/after review) and the old **one-material** rows.
  ⚖️ Q20-10, Q20-11.

### 2.3 Execution entries (the paper-form pipeline)

* **HOD bulk approve** of `PENDING_HOD` entries is possible, but **approval
  posts stock** (FEFO lots, the negative-stock check), so each entry commits
  alone and a stock conflict fails alone. ⚖️ Q20-12.
* **SK "verify as filed"** in bulk is possible; supervisor drafts are **not**
  (they need the area and hours typed).

### 2.4 Tests and docs

* Suite **20B**:
  * partial success;
  * already-decided skip;
  * cross-site skip;
  * SQM credited once on a double bulk;
  * one notification per batch;
  * the 50 cap;
  * the HOD-only lock;
  * Garnet without Old/New is not ready.
* **E2E**:
  * the SK selects 3 ready cards and submits;
  * the HOD selects them by date and approves;
  * a stale card is reported as skipped.
* MANUAL_TESTING_GUIDE §20b; USER_MANUAL (store keeper, supervisor and HOD
  chapters).

---

## 3. Track 3 — `FINANCE_PITCH.md` (and how to present it)

### 3.1 Structure of the document

1. **One-page executive summary**: the problem (a stock ledger kept in Excel,
   double counts, packs vs KG, no audit trail), what GI-Hub is, three headline
   numbers, the ask.
2. **The money story**: each capability as *what it does → what it saves or
   protects → the evidence in the system*:

| Capability | In plain words | Financial / control win | Evidence |
|---|---|---|---|
| Packs ⇄ KG at read (Unit Size, P14-units) | a "can" is never mistaken for a kilogram | prevents write-offs and phantom shortages from unit confusion | Phase 14 invariant L4; suite BA's guard |
| One deduction per drum (Phase 14 L1–L3) | the Excel log and the QR form can't both deduct | stock value is not understated; no double charge to a job | 4 double-count paths closed |
| FEFO lots + expiry (Phase 16) | oldest stock issued first, expiring stock flagged | less expired-material scrap | Lots & Expiry page, the FEFO order |
| Intelligent minimums + HOD-accepted per-site minimums (18/19) | the system says what to order, from consumption and the SQM plan | less over-stock (working capital) and fewer stock-outs (idle crews) | red/amber/green signals, on-order per site |
| SQM credited once per job (14c) + HOD approval of every Surface Shield draw | progress and material are reconciled job by job | material-per-m² variance is visible and explained | ±10 % priority band, audit trail |
| Segregation of duties | the SK counts, the supervisor reports, the HOD approves | the control environment auditors want | role matrix (rule 14), site walls |
| Full audit trail | who changed what, when | audit readiness | `system_audit_log`, every approval |
| Return desk + loan slips + chaser (18/19) | tools come back, or the HOD knows within 3 days | fewer lost tools | daily chaser, damaged-return alerts |
| OCR of paper forms (Phase 9) + bulk approvals (20) | less typing, fewer clicks | supervisor/HOD hours saved | jobs, not rows |
| On-premise AI (System One router, guarded) | an assistant that answers from the manual and live data, on our own server | no per-seat AI fees; **data stays in-house by default** (the cloud vision fallback exists but is off and unconfigured; live-stock questions have no cloud path by ruling) | guard v3, router eval hard gate in CI |
| Practice sandbox (rule 17) | train people on a full copy with fake data | training without risk to real stock | a separate database, a separate process |
| Engineering quality | changes cannot ship red | lower run cost, fewer incidents | **2,870** service checks · **187** browser tests · 599 legacy checks · required CI on every change · **Rule 15: tests never open the live database** |
| Cost of ownership | open-source stack, self-hosted | no licence per user | [hosting figure: operator] |

3. **Risk and control**: security (2FA, refresh-token rotation, site walls,
   audit, maintenance mode, backups), what happens if a server fails (backups,
   runbook), offline capture (the offline queue with idempotent replay: a
   dropped connection never double-posts).
4. **Rollout proposal**: pilot site, then sites; training via Practice and the
   video tutorials; KPIs to measure (stock accuracy %, write-offs, days of
   cover, PR → PO time, approval lead time).
5. **The ask**: what is needed from Finance (hosting budget, sign-off, a pilot
   owner).
6. **Appendix**: glossary, architecture in one diagram, FAQ for finance
   questions.

⚠️ **Accuracy rule for the whole document**: every number is either *measured
in the system* (cited) or *supplied by the operator* (marked `[operator]`). No
invented savings. A finance audience will test one number, and if that one is
soft, every other number loses its value.

### 3.2 The live demo script (Practice sandbox, ~12 minutes)

| # | Account | Show | The line to say |
|---|---|---|---|
| 1 | practice.storekeeper | Issue a Surface Shield item: packs + KG shown; FEFO picks the oldest lot | "A can is never a kilogram, and the oldest stock goes first." |
| 2 | practice.storekeeper | **Needs an area**: the remark *"Floor - 13.37 SQM Done"* pre-fills the SQM. **Select ready → Submit selected** (Track 2) | "Ten jobs, one click, and the field's own words are the record." |
| 3 | practice.hod | **Awaiting the HOD**: filter by date → **Approve selected**; one high-priority variance is shown before approving | "Every m² is approved by a person who can see the variance." |
| 4 | practice.hod | **Surface Shield daily log** (Track 1): approved / pending / rejected by day, remarks verbatim, export the PDF | "This is what Finance would read every month." |
| 5 | practice.logistics | **Reorder signals**: red/amber/green, the suggested order, on order per site | "The system tells us what to buy, and what is already coming." |
| 6 | practice.storekeeper | **Return desk**: scan, a partial return, print a slip | "Tools come back, or we know who has them." |
| 7 | any | Ask the **Hub Assistant** a how-to; try a trick question and see it refused | "AI on our own server, guarded and tested on every change." |
| 8 | practice.admin | Audit log of the last 10 minutes | "Every click you just saw is on record." |

Plus a **fallback plan**:
* a pre-recorded walkthrough (the Phase 12 tutorial pipeline renders against
  synthetic data, P12-0);
* screenshots in the appendix;
* run the demo on **localhost Practice**, so it never depends on venue Wi-Fi.

### 3.3 Ideas for presenting it (beyond the document)

* **Open with one story, not a feature list.** Tell the drum that was counted
  twice, or the can booked as a kilogram, with its value in money. Then show
  that it cannot happen any more.
* **Three numbers on the first slide**, for example stock accuracy, hours
  saved per week, and value protected. Use the operator's real figures.
* **A one-page leave-behind PDF.** The Executive Summary PDF generator
  (`exec_pdf.py`) already exists, so a monthly management PDF can be shown as
  "you would get this every month".
* **Show the control environment, not the code**: who approves what, and the
  audit trail. That is the language of finance.
* **Show a phone**: a WhatsApp notification arriving live (overdue tool,
  approval) makes it tangible.
* **Answer the four questions finance always asks** before they are asked:
  what it costs, what happens if it breaks, who maintains it, and how we know
  the numbers are right. The last one is the test suites and CI gates.
* **Propose a pilot with success criteria**, not a big-bang rollout.
* **Rehearse** with the practice.* accounts the day before, after `practice_db
  build`, so every figure in the script is fresh.
* Optionally, a **slide deck** version (a claude.ai Slides artifact, exportable
  to PowerPoint/PDF) generated from `FINANCE_PITCH.md`.

---

## 4. Order of work, gates, shipping

1. **20a**: daily log endpoint + view + card polish → gates → PR (auto-merge).
2. **20b**: bulk submit + bulk decide → gates → PR.
3. **20c**: `FINANCE_PITCH.md` (and the optional deck) once 20a/20b exist, so
   the demo script names real buttons and real Practice data.

Every slice follows the usual rules:
* both manuals (rule 13);
* Practice examples (17g);
* the nav matrix if a new page appears (rule 14);
* every gate;
* branch → PR → auto-merge → local pull (CLAUDE.md §5).

**No migration is expected** in any slice.

---

## 5. Questions for the operator (⚖️ please rule)

**Track 1**
* **Q20-1 Audience.** Who must open the daily log? The SK, supervisor and HOD
  already open `/execution`. Should **Logistics, Auditor and Admin** read it
  too? Does the **Finance Manager need their own login**? A new read-only
  "management" role is possible, but it is a role-matrix change (rule 14).
* **Q20-2 Placement.** Choose one:
  * a tab on the Execution page (field roles);
  * a tab on SME (HOD/auditor);
  * a section of the HOD **Executive Summary**;
  * all of these, linking to one view (my recommendation).
* **Q20-3 Which remark is "the" remark?** The store keeper's Excel remark is
  shown verbatim. When the supervisor or HOD edits the job's remark on
  submit/approve, show **both** (Excel original + job note)? I recommend both,
  with the Excel original first.
* **Q20-4 Grouping and period.** Date → job (recommended), or equipment →
  date? Default period: the last 30 days?
* **Q20-5 Units.** Show packs and KG together (recommended), or KG only for
  management?
* **Q20-6 Garnet** in the same log (as a section) or a separate view?
* **Q20-7 Export.** Is Excel + PDF enough, or should the HOD's weekly Executive
  Summary email include this?

**Track 2**
* **Q20-8 A card that is not ready** (no SQM in its remark, no system code,
  Garnet without Old/New): disable its checkbox with the reason (recommended),
  or let it be selected and skipped?
* **Q20-9 Who may bulk-submit**: store keeper and supervisor both, as today for
  one card?
* **Q20-10 High Priority in bulk.** May the HOD bulk-approve jobs whose
  variance is outside ±10 %? I recommend **yes, but listed in the
  confirmation**. Or should they be excluded unless "include high priority" is
  ticked?
* **Q20-11 Bulk reject** with one shared reason: wanted, or approve-only?
* **Q20-12 Execution entries** (the paper-form pipeline): include them?
  * **HOD bulk-approve of `PENDING_HOD`**: it posts stock, and each entry fails
    alone on a stock conflict.
  * **SK bulk "verify as filed"**.
  * Or limit Phase 20 to the Surface Shield jobs?
* **Q20-13 Batch size.** 50 per click?

**Track 3**
* **Q20-14 Your numbers** (or the pitch uses `[operator]` placeholders):
  * annual Surface Shield / consumables spend;
  * known write-offs or losses from unit or double-count errors;
  * hours per week spent consolidating Excel today;
  * sites and users planned;
  * today's tool cost (Excel, SAP modules);
  * the hosting budget (Hetzner CPX42).
* **Q20-15 Format.** A Markdown document only, or also a slide deck
  (PowerPoint/PDF)? Length (10, 20 or 30 minutes)? Company branding or logo?
  English only?
* **Q20-16 Demo venue.** Localhost Practice on your laptop (recommended: no
  Wi-Fi dependency), or Practice on `gi.giinventory.com`?
* **Q20-17 What not to say.** Anything confidential (site names, supplier
  prices) that must not appear in a document leaving your team?
