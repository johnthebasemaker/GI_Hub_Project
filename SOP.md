# General Industries Hub — Standard Operating Procedure

**Version 2.0** · 3 October 2026 · The whole system, by role
**Owners:** Site HOD Council · Logistics Manager · Warehouse Lead · GI Hub Administrator
**Applies to:** every site and warehouse on GI Hub (the React / FastAPI application)
**Companion documents:** `USER_MANUAL.md` — every page, field and button (the §
numbers below point into it) · `MANUAL_TESTING_GUIDE.md` — how each feature is
checked · `docs/ARCHITECTURE.md` — how the system is built
**Review:** quarterly, and whenever a role, a workbook or an approval step changes.

> **What changed from v1.0.** Version 1.0 (July 2026) covered the procurement
> chain only, and was written for the old Streamlit screens. Version 2.0 covers
> the whole system — stock entries, consumption forms, the Surface Shield queue,
> lots and FEFO (Phase 16), Practice, the Excel workbook sync and the
> administrator's routine — and is organised by role. The procurement decision
> trees, escalation matrix and recovery procedures from v1.0 are kept in §6–§8,
> unchanged in substance.

---

## §1. Purpose & scope

This SOP answers one question for every role: **"what do I do today, and in
what order?"** The User Manual says what each screen does; this document says
when to open it and what "done" looks like.

**How to use it.**
- Find your role in **§4** and follow its *Start of day → During the day → End
  of day* list. The weekly and monthly lists follow.
- **§2** shows how the roles' days fit together, so you can see who is waiting
  on you.
- **§5** is the administrator's data routine: the Excel sync, the lot workbook,
  backups, updates and Practice.
- **§6–§8** are for judgment calls, delays and things that went wrong.
- **§9** is one card per role to print and pin up.

**Three rules that apply to everyone.**
1. **Live is real; Practice is for learning.** Anything typed in Live moves real
   stock. To try something, switch to **Practice** on the sign-in page (violet
   screens, a pulsing *PRACTICE* badge — §26 of the manual).
2. **Nothing is final until the HOD approves it.** A store keeper stages, a
   supervisor files, the HOD approves. Stock figures update on approval.
3. **Fix the source, not the symptom.** If a figure is wrong because the Excel
   workbook is wrong, correct the workbook and sync again; do not "balance" it
   with an adjustment.

**Out of scope:** screen-by-screen instructions (User Manual), code and SQL
(Architecture), server hosting (manual §17).

---

## §2. The system's day — how the roles fit together

Times are the site's normal working day (Asia/Riyadh, GMT+3). Automatic jobs
are marked ⚙️.

| When | Who | What happens | Waiting on it |
|---|---|---|---|
| 02:00 | ⚙️ System | Nightly database backup (`bin/backup_db.sh --install`). | Admin checks it weekly (§5.3) |
| 07:00 | ⚙️ System | **Morning Briefing** to admins and HODs — health probes, uncertified Surface Shields, day-shift MTC chase. | HOD, Logistics, QC |
| 07:00–08:00 | Store Keeper | Opens the store: incoming deliveries, supervisor requests, overdue returnables, **Lots & Expiry** red banner. | Supervisors (material), HOD (DNs) |
| 07:00–08:00 | Supervisor | Prints the consumption forms for today's jobs; raises material requests. | Store Keeper |
| Morning | HOD | Clears yesterday's approvals and Delivery Notes. | Store Keeper, Warehouse, Logistics |
| Morning | Logistics | Incoming PRs → POs; DN date approvals; overdue deliveries. | Warehouse, HOD |
| Morning | Warehouse | Incoming assignments, goods received, DNs prepared. | Logistics, HOD |
| During jobs | Store Keeper | Issues material (FEFO lot), receives deliveries, records returns. | HOD |
| During jobs | QC | Inspects controlled material; passes, fails or holds it. | Store Keeper (issue is blocked until QC passes) |
| After each job | Supervisor | Photographs and files the consumption form. | Store Keeper |
| After each job | Store Keeper | Verifies the supervisor's form; works the Surface Shield queue. | HOD |
| Afternoon | HOD | Approves receipts, issues, returns, adjustments, execution entries and Surface Shield jobs. | Everyone who staged something |
| 16:00 | ⚙️ System | **Evening digest** (WhatsApp + bell) and the **lot expiry notice** — one per site, lots expiring within 30 days or expired. | Store Keeper, HOD |
| After the workbooks change | Admin | **Excel sync** (§5.1), including the Lot Register workbook. | Everyone (stock and lots) |
| Friday 17:00 | ⚙️ System | Weekly executive PDF to admins and HODs. | Management |

---

## §3. Role responsibility matrix (RACI)

R = Responsible (does it) · A = Accountable (signs off) · C = Consulted · I = Informed.
SK = Store Keeper · Sup = Supervisor · WH = Warehouse User · QC-H = Head of Qualities.

| Activity | SK | Sup | HOD | QC | QC-H | Logistics | WH | Admin |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Raise a material request for a worker | C | R | I | — | — | — | — | — |
| Issue stock (with its lot) | R | I | A | — | — | — | — | — |
| Receive stock at site (batch, MFD, expiry) | R | — | A | C | — | I | — | — |
| Return stock | R | C | A | — | — | I | — | — |
| Stock count / adjustment | R | — | A | — | — | — | — | I |
| Print and file a consumption form | C | R | A | — | — | — | — | — |
| Verify a filed consumption form | R | I | A | — | — | — | — | — |
| Surface Shield job (area, Old/New surface) | R | C | A | — | — | — | — | — |
| Inspect controlled material | I | — | I | R/A | I | I | I | — |
| Dispose of / quarantine an expired lot | C | — | A | C | I | — | — | R |
| Create and submit a PR | — | — | R/A | — | — | I | — | I |
| Issue a PO, assign a warehouse | — | — | I | — | I | R/A | I | I |
| Receive from vendor, prepare a DN | — | — | I | C | — | I | R/A | — |
| Approve a DN (date / content) | — | — | R/A content | — | — | R/A date | I | I |
| Mark a DN received at site | R/A | — | I | — | — | I | I | — |
| Add / edit an inventory item | — | — | R (own site) | — | — | — | — | R/A (and delete) |
| WBS numbers and work types | — | — | R/A | — | — | — | — | I |
| Excel workbook sync (incl. lot workbook) | I | — | C | — | — | — | — | R/A |
| Keep the workbooks correct | C | — | A | — | — | C | — | R (reports problems) |
| Users, roles, access requests | — | — | C | — | — | — | — | R/A |
| Backups, updates, Practice reset | — | — | I | — | — | — | — | R/A |
| Surface Shield oversight, escalations | — | — | I | C | R/A | I | — | — |

---

## §4. SOPs by role

Each role has the same shape: **start of day**, **during the day**, **end of
day**, then **weekly** and **monthly**, and a short **never** list. References to the User Manual say *manual §*;
plain § numbers are sections of this SOP.

### §4.1 Store Keeper

The store keeper is the only role that moves stock. Everything you stage goes
to the HOD.

**Start of day (15 minutes)**
1. **Incoming Deliveries** — any DN on its way? Note what to expect today.
2. **Supervisor Requests** (badge) — approve and issue, or reject with a reason.
3. **Lots & Expiry** — if the red banner says an expired lot still has stock,
   check the shelf. If it is there, set it aside and tell the HOD (§6.6). Note
   the lots in *≤ 30 days*: those are what you issue first.
4. **Returnable Items** — anything overdue? Chase the holder.
5. Read the bell and any **What's new** notice.

**During the day**
- **Issue Stock** — for each issue, pick the material, the tank / work type and
  (for a Surface Shield) the system code. In the **Lot** field take the lot
  marked **FEFO** unless there is a reason not to; any other lot asks for the
  reason and the HOD sees it. Never issue the expired lot without the HOD's
  say-so. For CHEMOLINE, pick the **roll**. (manual §3.10.3, §4)
- **Receive Stock** — for a lot-tracked material, type the **batch exactly as
  printed**, the **MFD** and the **expiry**. If the label has no expiry, leave it
  blank; GI Hub derives it from the MFD. Attach the DN and, for Surface
  Shields, the MTC. (manual §3.10.3, §4.4)
- **Return Stock** — name the lot the material came from. A return gives back to
  that lot. (manual §3.10.1)
- **Mark a DN received** when the truck is unloaded and checked (Incoming
  Deliveries).
- **Verify filed consumption forms** — Execution Entries → *With store keeper*.
  Compare the supervisor's quantities with what left the shelf. Correct only
  with a reason; your corrections show to the HOD in red. (manual §4.10.3)
- **Surface Shield queue** — Execution Entries → the queue. One card is one job
  (one tank, one day, one note). Check the area the note filled in, tick the
  materials that belong to the job, submit to the HOD. (manual §4.9a)

**End of day**
1. Everything issued today is entered (no "I'll do it tomorrow").
2. Supervisor Requests and *With store keeper* forms are empty or explained.
3. Anything saved while offline has been sent (the queue indicator is clear).
4. Read the **evening expiry notice** and plan tomorrow's FEFO issues.

**Weekly** — Stock Count of the fast movers and every Surface Shield; walk the
lot shelves against Lots & Expiry; confirm overdue returnables are chased.

**Monthly** — full Stock Count; list the lots used but never received (Lots &
Expiry, bottom card) and give them to whoever keeps the workbook.

**Never** — post an adjustment to hide a workbook error; issue from an expired
lot without the HOD; re-type a lot number differently from the label (`A 4525`,
not `A4525` or `4525`); practise in Live.

### §4.2 Supervisor

The supervisor runs the field work: asks for the material, records what the job
used.

**Start of day**
1. Execution Entries → **Print a consumption form** for each job today. Print
   one per job; never photocopy a form (its QR code is unique). (manual §4.9)
2. Supervisor → **Material Requests** → **New Request** for each worker who
   needs material: worker, job / tank / place, the lines. (manual §5.3)
3. Check **My Requests** for anything rejected overnight.

**During the day**
- Fill the paper form on site as the work happens: quantities (packs, or tick
  KG), area, crew, hours, and the lot on each line.
- Keep the form clean and the QR code uncovered.

**End of each job**
1. Execution Entries → **Upload a filled form** — photograph the whole page,
   QR code included. Reading takes minutes; you can leave the page. (manual §4.10.1)
2. Check every figure the camera read against the paper, then **File**.
3. Watch the status: *With store keeper* → *With HOD* → *Approved*. A rejection
   comes back with the reason; correct and re-file.

**Weekly** — Intent vs Actual: what you asked for against what was issued. Talk
to the store keeper about any gap.

**Never** — photocopy a printed form; file a form that is not yours; ask the
store keeper to issue without a request.

### §4.3 Head of Department (HOD)

The HOD approves everything the site stages and plans the site's material.

**Start of day (30 minutes)**
1. **Approvals** — Receipts, Issues, Returns, Adjustments, Delivery Notes.
   Clear what was staged yesterday. Read any non-FEFO lot reason and any red
   store-keeper correction before approving. Reject with a reason, never
   silently. (manual §6)
2. **Execution Entries** — forms *With HOD*: approve, or correct with a written
   justification (the supervisor is notified).
3. **Surface Shield queue → Awaiting the HOD** — High Priority jobs first (more
   than ±10% from the benchmark, or no benchmark). Review each job, answer
   **Old or New surface** for Garnet, approve or reject the whole job. (manual §4.9a.4,
   §4.9a.7)
4. Read the Morning Briefing.

**During the day**
- **Lots & Expiry** — expired lots with stock: decide dispose / quarantine and
  ask the Admin to record it (§6.6). Lots expiring within 30 days: make sure the
  store keeper is issuing them first.
- **Purchase Requests** — create and submit PRs to Logistics for what Low Stock
  and the Material Estimator say is short.
- **Cross-Site Requests** — request material from another site rather than
  buying it.
- **Records → Inventory → New item / Edit** — add a material for your site when
  a new one arrives. (Deleting is the Admin's.)

**End of day**
1. The approval queues are empty, or every item left has a reason.
2. The evening digest and expiry notice are read.

**Weekly** — Burn Rate and Low Stock; Lining Coverage; Material Estimator
(Session Report, Total Overview) for what can be built now versus when
deliveries arrive; Man-Hours variance and the HOD Approval Queue; WBS & Work
Types up to date.

**Monthly** — Executive Summary and Reports → Monthly Summary for management;
Valuation & 30-Day Burn; review the shelf-life and lot list with QC.

**Never** — approve a whole queue without reading the red and amber values;
approve an issue from an expired lot without checking it physically; ask
Logistics for a PO directly (raise a PR).

### §4.4 Administrator — maintenance and sync

The Admin keeps the data true and the system running. The detailed commands are
in §5.

**Start of day**
1. **Admin → Overdue Actions** (red badge) and **Access Requests** — approve new
   users into the right role and site.
2. **Admin → Console → Overview** — services, the WhatsApp and email outboxes
   (retry failures), feedback.
3. If the workbooks were updated yesterday, run the **Excel sync** (§5.1) and
   read its report.

**During the day**
- Users: create, change role or site, reset 2FA, revoke sessions.
- **Admin → Inventory** — fix item master data; delete an item only when it has
  no history.
- **Console → Lots** — record a disposal or quarantine the HOD decided (§6.6).
- Triage **Feedback** (bug reports); copy the prompt for a developer.

**End of day** — the outboxes are clear; nothing is stuck in Overdue Actions.

**Weekly**
1. **Backups:** `./bin/backup_db.sh --list` — last night's file is there (§5.3).
2. **Stock vs Excel** check — the Stock page's *≠ Excel* list; send the causes to
   whoever keeps the workbook (§5.2).
3. **Lots used but never received** (Lots & Expiry) — send the list to the
   workbook keeper.
4. **Announcements** — tutorial freshness warnings.

**Monthly** — review users and roles (leavers disabled); restore one backup
into a scratch database to prove it works; Practice reset if trainees have
cluttered it (§5.5).

**After every update** — §5.4.

**Never** — run a test or a tool against the Live database that is not the
documented sync; edit the original workbook with a script (work on a copy);
start a second tunnel connector; share a Practice password as a Live one.

### §4.5 Logistics

**Start of day** — Procurement → **Incoming PRs** (convert to POs or ask the
HOD); **DN Approvals** (date stage); **Purchase Orders** — overdue and partial
deliveries; Reschedules and Vendor Returns waiting.
**During the day** — Create PO (or **Import PO PDF**); assign each PO to a
warehouse; chase vendors on overdue lines; approve DN dates.
**End of day** — no PR older than its SLA without a note; DN date queue empty.
**Weekly** — open POs review; Lining Coverage for the next month's shortfalls.
**Monthly** — Force-Closures review; vendor performance from Reports.
**Never** — approve DN content (that is the HOD's stage); merge Rubber Lining
and Brick Lining material on one DN.

### §4.6 Warehouse User

**Per shift** — **Incoming Assignments** (acknowledge); receive goods against
the PO (batch, MFD and expiry for Surface Shields); **Delivery Notes** —
prepare RL and BL separately, attach the scanned DN before shipping; **Returns
from Site**.
**Weekly** — History review; anything received but not dispatched.
**Never** — ship a DN without its scan; mix RL and BL.

### §4.7 Quality Control (QC)

**Start of day** — Quality → **Inspections**: the queue of controlled material
waiting. Each item stays blocked from issue until you decide.
**During the day** — pass, fail or hold each one with the certificate checked.
A fail mints a return number the store keeper uses. **Lots & Expiry**: report an
expired lot with stock to the HOD.
**Never** — pass material without its MTC; decide outside your site or
warehouse.

### §4.8 Head of Qualities

**Daily** — **Quality Oversight**: Overview, Expired / Expiring / Stagnant, the
daily alert. Raise an escalation naming **one** place for anything that needs a
decision. Check **Lots & Expiry** across sites.
**Weekly** — Surface Shield POs and the MTC Register; Where It Is Used.
**Monthly** — review the 90/60-day thresholds in Settings.

### §4.9 Auditor (view-only)

Read Dashboard, Stock, Records, Reports, the HOD read pages and the Material
Estimator; download reports. Raise findings through **Feedback**; the Auditor
never changes data.

---

## §5. Data procedures (Administrator)

### §5.1 Syncing the Excel workbooks — including the Lot Register workbook

**When:** after the workbooks have been updated (normally daily), and always
after a correction the store or the HOD asked for.

**Before:** close the workbooks in Excel; put the latest copies in the sync
folder. The Lot Register workbook (`Rubber & Brick Materials - CNCEC.xlsx`, any
file named like `*Rubber*Brick*CNCEC*.xlsx`) goes in the same folder — the sync
finds it.

**Steps**
1. Wake the services if the computer was asleep: `./bin/power.sh wake`.
2. **Dry run** (shows what would change, changes nothing):
   ```bash
   cd ~/GI_Hub_Project && DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python tools/pg_excel_sync.py --site CNCEC --erp --prune-vanished
   ```
3. Read the report: `+` new rows, `~` updated rows, `-` rows removed because the
   workbook no longer has them, and the **▶ lots** section (lots filled, rolls,
   shelf lives learned, and the problems listed by sheet and row).
4. If it looks right, run the same command with **`--commit`** at the end.
5. Open **Lots & Expiry** and the Stock page and spot-check one material.

**What the report asks you to fix in the workbook** (never in GI Hub):
- a cell with **several lots** (`4525 = 55 Cans; 1823 = 2 Cans`) — split the row;
- a **lot used but never received** — usually a typing slip in the lot or the
  SAP code; the report names the material that *did* receive that lot;
- a Lot Register quantity that differs from the Receipt Log, a row with no
  matching receipt, an expiry before its MFD, a batch spelled differently
  (`0926` vs `0.926`);
- a roll typed `10…` — corrected to `1O…` automatically, but fix the source.

⚠️ The Lot Register workbook **never changes stock**. Stock comes only from the
Receipt, Consumption and Return Logs. Rows entered in GI Hub are never touched
by the sync.

### §5.2 Stock vs the Excel workbook

The Stock page marks a material **≠ Excel** when GI Hub's stock differs from the
workbook's Current Stock, and lists the causes (e.g. *Only in GI Hub*: an entry
made in the app that the workbook does not have). Weekly, send the list to the
workbook keeper. To give them a marked copy:
`.venv/bin/python tools/stock_excel_check.py --marked` — it writes a **copy**;
never overwrite the original workbook.

### §5.3 Backups and restore

- Nightly at 02:00 once installed: `./bin/backup_db.sh --install`.
- Check: `./bin/backup_db.sh --list` — expect last night's file in `.backups/`.
- Before any risky work (a migration, bulk edits): `./bin/backup_db.sh`.
- Restore: `./bin/backup_db.sh --restore FILE` prints the exact command; restore
  into a scratch database first and compare.

### §5.4 After an update

1. Take a backup (§5.3).
2. Pull the new version and restart (`bin/dev.sh` or the production service).
   Database changes apply automatically before anyone signs in.
3. **Practice:** `.venv/bin/python tools/practice_db.py migrate` brings both
   Practice databases to the same version (Practice refuses to start otherwise),
   then `.venv/bin/python tools/practice_db.py verify`.
4. If the Live database was reloaded from a dump: re-run
   `tools/practice_db.py wall` and `backend/scripts/create_ai_readonly_role.sql`
   (a reload wipes both protections).
5. Read the **What's new** notice yourself so you can answer questions.

### §5.5 Practice and training

- New staff learn in **Practice** with the shared `practice.<role>` accounts
  (manual §26.3). Each new Live feature has a Practice example (manual §26.4).
- Reset Practice from **Admin → Console → Practice** (Practice only) when it is
  cluttered; it returns to the seeded state.
- Training videos and acknowledgements: **Training**; HODs see compliance.

### §5.6 Power — sleep and wake (the office laptop)

`./bin/power.sh sleep` stops Postgres and the tunnel to save battery —
**gi.giinventory.com is offline while asleep.** `./bin/power.sh wake` brings
both back; `./bin/power.sh status` shows what is running.

---

## §6. Decision trees

§6.1–§6.4 are the procurement trees from v1.0; §6.5–§6.7 are new for lots.

### §6.1 DN approval — Approve vs Reject vs Reschedule

```
                     ┌─────────────────────────┐
                     │  DN in HOD approval     │
                     │  queue (or Logistics)   │
                     └────────────┬────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  │ Inspect: qty, family,         │
                  │ lot, expiry, vehicle, driver  │
                  └───────────────┬───────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       Everything OK    Date wrong but content    Content wrong
              │           is fine                     │
              │              │                        │
              ▼              ▼                        ▼
         ✅ Approve    🔁 Reschedule              ❌ Reject
                       (set new date)         (must include reason)
                              │                        │
                              ▼                        ▼
                       Logistics decides     Warehouse rebuilds DN;
                       within 2h business   loop continues
```

**Approve when:** Qty matches PR · Family correct · Lot has > 90d shelf life · Vehicle + driver populated · No HSE concern with this delivery date

**Reschedule (don't reject) when:** Content is fine but YOU can't receive on the scheduled date (site shutdown, manpower, weather). Reschedule is faster than reject + rebuild.

**Reject when:** Qty doesn't match PR · Wrong material · Lot expiry inadequate · Wrong destination site (misroute) · Driver/vehicle missing critical info

### §6.2 Vendor return vs Stock adjustment

```
                ┌─────────────────────────────┐
                │  Discrepancy discovered     │
                └──────────────┬──────────────┘
                               │
            ┌──────────────────┴──────────────────┐
            │  Was material RECEIVED already      │
            │  via a DN this site approved?       │
            └──────────────────┬──────────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      YES (in receipts)               NO (still at warehouse)
              │                                 │
              ▼                                 ▼
   ┌──────────────────────┐         ┌──────────────────────┐
   │ Is the issue with    │         │  Warehouse handles   │
   │ THIS DN (defective,  │         │  internally — Return │
   │ wrong qty), or       │         │  to vendor flow      │
   │ general stock        │         │  (Tab 8 Warehouse)   │
   │ count drift?         │         └──────────────────────┘
   └──────────┬───────────┘
              │
    ┌─────────┴─────────┐
    ▼                   ▼
This DN              General drift
    │                   │
    ▼                   ▼
↩️ Vendor          🧮 Stock Adjustment
   Return            (SK Entry Log →
   (any role)        Stock Count tab)
```

**Use Vendor Return when:** A specific DN brought defective / wrong qty / quality-issue material. Reopens the originating PO line so Logistics can chase vendor.

**Use Stock Adjustment when:** Cycle-count discovers shelf qty ≠ system qty for a general reason (damage in storage, miscount, expired-disposal, etc.). Doesn't touch the PO chain.

### §6.3 30-day return window — Override or not?

```
                ┌─────────────────────────────┐
                │  SK / HOD wants to return   │
                │  material to logistics      │
                └──────────────┬──────────────┘
                               │
                  ┌────────────┴────────────┐
                  │ Received in last 30d?   │
                  └────────────┬────────────┘
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
            YES                                 NO
              │                                 │
              ▼                                 ▼
   Standard return flow         Override required:
   (SK Entry Log → Return)      tick "Override 30-day window"
                                + write justification
                                       │
                                       ▼
                               HOD reviews — override
                               rows highlighted RED
                                       │
                               ┌───────┴───────┐
                               ▼               ▼
                       Justified            Not justified
                           │                    │
                           ▼                    ▼
                      ✓ Approve            ✗ Reject
                                     (use Stock Adjustment
                                      with reason `damaged`
                                      or `expired_disposal`)
```

### §6.4 Force-close decision

```
                ┌─────────────────────────────┐
                │  Logistics considering      │
                │  force-closing PR/PO/line   │
                └──────────────┬──────────────┘
                               │
                  ┌────────────┴────────────┐
                  │ Has stakeholder been    │
                  │ consulted (Site HOD)?   │
                  └────────────┬────────────┘
                               │
                  NO ─────────┐│┌──────── YES
                               │ │
                               ▼ ▼
               STOP — call HOD first.       Proceed to next gate.
               Force-close is permanent.
                                         │
                            ┌────────────┴────────────┐
                            │  Is the reason in       │
                            │  scope of force-close?  │
                            └────────────┬────────────┘
                                         │
                       In scope ───────┐│┌────── Out of scope
                                       │ │
                                       ▼ ▼
                 ✓ Force-close      Use alternative:
                 with full reason   - Vendor cancellation → close PR
                                    - Wrong qty on PO → vendor return + new PO
                                    - Wrong material → vendor return
                                    - Project cancelled → close PR
```

**In scope for force-close:** Project cancellation · Vendor permanently unable to fulfill · PR raised in error and beyond email-recall · Item discontinued · Compliance violation

**Out of scope (use other tool):** Quantity / quality issues (use vendor return) · Date slippage (use reschedule) · Wrong destination site (rebuild DN, no force-close needed)

### §6.5 Which lot do I issue?

```
  Lot field on Issue Stock (lot-tracked material)
        │
        ├─ Take the lot marked FEFO ─────────────────► no question asked
        │     (earliest valid expiry; lots with no expiry next;
        │      expired lots are listed LAST and never suggested)
        │
        ├─ A different, valid lot? ──► give the reason (e.g. "FEFO lot
        │                               buried behind pallets") → HOD sees it
        │
        └─ The EXPIRED lot? ──► only with the HOD's agreement, after a
                                physical check; give the reason.
                                FEFO warns, it never blocks.
```

A **quarantined** or **disposed** lot is not offered at all.

### §6.6 An expired lot still has stock

1. **Store Keeper:** check the shelf.
   - **It is there** → set it aside, label it, tell the HOD.
   - **It is not there** → an issue or return was never recorded. Enter it (or
     ask for the workbook to be corrected). Do **not** adjust it away.
2. **HOD:** decide with QC — re-test, dispose, or return to the vendor.
3. **Admin:** record the decision in Admin Console → **Lots** (*Quarantine* or
   *Dispose*). The lot leaves the Issue form at once.
4. If it was disposed, the store keeper posts the matching adjustment with the
   reason *🗑️ Expired — disposed* so stock agrees with the shelf.

### §6.7 A lot is "used but never received"

This is a **typing problem in a workbook**, not a stock problem; the stock
already counts the line.

1. Read the row on Lots & Expiry (bottom card): the log, the SAP, the lot as
   typed, and **Received under SAP** — the material that did receive a lot with
   that number.
2. If *Received under SAP* names a sister item (e.g. lot `3504` consumed under
   `1043` but received under `1041`), the consumption line has the **wrong SAP
   code or the wrong lot**. Check the paper form and correct the workbook line.
3. If nothing received that lot anywhere, the lot number itself is mistyped, or
   the receipt is missing. Check the delivery note.
4. The Admin syncs again (§5.1); the row disappears from the card.

---

## §7. Escalation Matrix

When a process step doesn't happen on time, here's who pings whom, when, and on which channel.

### §7.1 PR → PO escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| PR submitted to Logistics, no PO after 4 business hours | 4h | Site HOD → Logistics lead | WhatsApp |
| ↑ Still nothing after another 4h | 8h | Site HOD → Admin | App notification + email |
| ↑ Still nothing after 24h | 24h | Admin overrides — assigns alternate Logistics user | Admin Portal → 👥 Users |

### §7.2 PO → Assignment escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| PO issued, no warehouse assignment after 8 business hours | 8h | Site HOD → Logistics (with PO number) | WhatsApp |
| Assignment created, Warehouse hasn't acknowledged in 24h | 24h | Logistics → Warehouse Lead | WhatsApp + email |
| ↑ Still no ack after 48h | 48h | Warehouse Lead → Admin | Admin escalation |

### §7.3 DN approval escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| DN sitting at `pending_logistics` for > 4 business hours | 4h | Warehouse → Logistics | WhatsApp |
| DN sitting at `pending_hod` for > 8 business hours | 8h | Logistics → Site HOD | WhatsApp |
| DN sitting at `pending_sk` for > 24 hours after expected arrival | 24h | HOD → SK + Warehouse | WhatsApp |

### §7.4 Reschedule escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| Reschedule pending > 2 business hours during work day | 2h | Requester → Logistics | WhatsApp |
| ↑ Pending > 24h | 24h | Site HOD → Admin | App + email |

### §7.5 Vendor return escalation

| Trigger | Wait time | Action | Channel |
|---|---|---|---|
| Vendor return raised, no acknowledgement from vendor in 7 days | 7d | Logistics → Vendor directly (email) | Email |
| ↑ Still unresolved after 14d | 14d | Logistics → Site HOD + Admin | App + email |
| Resupply date passed without delivery | T+1d | Logistics → Admin | Critical |

### §7.6 Force-closure escalation

Force-closures fire `critical` notifications to Admin + originating Site HOD instantly. There's no escalation needed — the action is already at maximum severity. Admin reviews weekly per §3.13.

---

## §8. Recovery procedures

§8.1–§8.7 are the procurement procedures from v1.0; §8.8–§8.10 are new.

### §8.1 DN rejected at HOD — how Warehouse recovers

1. Bell shows rejection notification with HOD's reason
2. Warehouse user reads the reason from the notification card (full text in the related DN row in `delivery_notes.rejection_reason`)
3. Determine what changed:
   - If qty wrong: prepare a new DN with corrected qty (the rejected DN's qty frees back into the available calculation immediately)
   - If material wrong: file an internal Stock Adjustment if you over-counted at receive, OR raise a vendor return if the wrong material was delivered to you
   - If date wrong: prepare new DN with the corrected date; HOD will approve in same cycle
4. Submit new DN → Logistics → HOD; loop continues

### §8.2 Warehouse short-receives from vendor

1. Receive Goods tab — type the actual received qty (less than ordered)
2. System auto-flips `po_items.line_status` to `partially_delivered`
3. Add a note in the assignment notes describing the shortfall
4. Notify Logistics via WhatsApp with the PO number + shortfall qty
5. Logistics raises a Vendor Return with reason "Vendor under-shipped" (uses the same return flow, reopens the line for resupply)
6. New shipment from vendor → Warehouse receives the remainder → line flips to `delivered`

### §8.3 Vendor return + resupply cycle

1. Issue surfaces (SK at site OR Warehouse at receive)
2. Determine raiser:
   - Issue at site after SK confirmed → SK raises to HOD → HOD raises to Warehouse via internal return → Warehouse raises Vendor Return
   - Issue at warehouse before DN ship → Warehouse raises Vendor Return directly
3. Logistics tracks return in `↩️ Vendor Returns` tab
4. Expected_Resupply date set (default today + 14)
5. T-2 / T-1 / T-0 reminders fire on Expected_Resupply
6. When resupply arrives: Warehouse re-receives → line `Delivered_Qty` recalculates → status moves forward

### §8.4 HOD absent for > 24h with pending DN

1. Admin shadows HOD Portal (admin can access via the existing override)
2. Admin reviews the pending DN(s) in HOD Portal → 🚚 DN Approvals
3. Admin approves on behalf, leaves a note in the DN attesting to the cover
4. Audit log records `DN_HOD_APPROVE` with admin's username
5. HOD-on-return is notified to review post-fact

### §8.5 Logistics absent — Admin override

1. Admin enters Logistics Portal via shadow access
2. Admin acts as Logistics for the day (approve PRs, issue POs if pre-arranged, approve reschedules)
3. Audit log records each action with admin's username (not the absent Logistics user)
4. Admin notifies the absent Logistics user via WhatsApp before signing off

### §8.6 Force-closure made in error

There's no "undo force-close" button (yet — on the v3.0 backlog). Workaround:

1. Admin → Master DB Editor → relevant table (`purchase_orders` / `pr_master`)
2. Edit `status` from `force_closed` back to `open` (PO) or remove the `logistics_status='force_closed'` flag (PR)
3. Add a corresponding `po_force_closures` row with reason "Reversed in error — see audit"
4. The Site HOD's "Force-closures affecting me" tab will show both the original close + the reversal

Use sparingly — this is invasive. Better: communicate the reversal verbally and update via DB Editor with a paper trail.

### §8.7 RL/BL DN was prepared as combined (system rejected, user confused)

1. UI shows: *"Strict separation violated: this DN spans multiple RL/BL families. Prepare one DN per family."*
2. Warehouse user reduces Ship Qty to 0 on either the RL lines OR the BL lines (not both)
3. Save DN draft — succeeds with only one family
4. Prepare second DN with the other family — same source PO, different family
5. Submit both DNs separately; both flow through approval independently

### §8.8 The Excel sync reports problems

The sync never stops for a workbook problem; it reports it and carries on with
the rows it can trust. Work the report top to bottom:
1. **Rows it could not place** (unknown SAP, unreadable date or quantity) — fix
   the workbook cell; nothing was imported for that row.
2. **Multi-lot cells** — split the row, one lot per row.
3. **Lots used but never received** — §6.7.
4. **Lot Register differences** (quantity vs Receipt Log, no matching receipt,
   expiry before MFD) — correct whichever workbook is wrong. Stock is not
   affected either way.
5. Sync again and confirm the list is shorter.

If a sync was committed by mistake, restore the backup taken before it (§5.3)
— and from then on take one before every sync.

### §8.9 An entry was saved while the signal was down

The app keeps it on the device and sends it when the connection returns; the
queue indicator shows how many are waiting. It is sent **once**, however many
times it retries. Do not type it again. If the device is lost or reset before
it reconnects, the entry is lost — enter it again from the paper.

### §8.10 Something was entered in Practice instead of Live (or the other way)

- **Practice by mistake:** nothing reached Live. Enter it again in Live.
  Practice and Live are separate systems, and Practice entries never move to
  Live — not even entries saved offline.
- **Live by mistake (a training entry):** ask the HOD to **reject** it if it is
  still pending. If it was approved, the store keeper reverses it with an
  adjustment whose reason says what happened, and the HOD approves that.

---

## §9. Quick reference cards

### §9.1 Store Keeper
- **Morning:** Incoming Deliveries · Supervisor Requests · Lots & Expiry banner · Returnables.
- **Issue:** take the **FEFO** lot; any other lot needs a reason; never the expired lot without the HOD.
- **Receive:** batch as printed · MFD · expiry (blank = derived) · DN + MTC.
- **Verify** supervisor forms; **submit** Surface Shield jobs (one card = one job).
- **Evening:** nothing left unentered; read the expiry notice.

### §9.2 Supervisor
- Print one form per job (never photocopy).
- Material Requests → New Request for each worker.
- After the job: photograph the whole form (QR in view) → check → File.
- Watch: *With store keeper* → *With HOD* → *Approved*.

### §9.3 HOD
- **Morning:** Approvals (all five tabs) · forms *With HOD* · Surface Shield jobs (High Priority first; Old/New for Garnet).
- Read every red / amber value and every non-FEFO reason before approving.
- Expired lot with stock → decide with QC → Admin records it.
- PRs for what is short; Cross-Site before buying.
- Weekly: Burn Rate, Low Stock, Lining Coverage, Estimator, Man-Hours.

### §9.4 Administrator
- **Daily:** Overdue Actions · Access Requests · Console outboxes · Excel sync (dry run → read → `--commit`).
- **Weekly:** backup list · Stock vs Excel · lots used but never received · tutorial freshness.
- **After an update:** backup → restart → `practice_db.py migrate` + `verify`.
- Never run tools against Live except the documented sync; never edit the original workbook with a script.

### §9.5 Logistics
- Incoming PRs → POs → assign warehouse · DN date approvals · overdue lines · reschedules · vendor returns.

### §9.6 Warehouse User
- Acknowledge assignments · receive against PO · DNs RL and BL separate, scan attached · returns from site.

### §9.7 QC · Head of Qualities · Auditor
- **QC:** Inspections queue → pass / fail / hold with the MTC; report expired lots.
- **Head of Qualities:** Quality Oversight daily; escalate naming one place.
- **Auditor:** read and download only; findings through Feedback.

---

## §10. Glossary

For the full vocabulary see `USER_MANUAL.md` §11.5. Terms used in this SOP:

| Term | Meaning |
|---|---|
| **Live / Practice** | Live is the real system. Practice is a separate system with invented data for learning (violet screens, PRACTICE badge). Nothing crosses between them. |
| **Stage / approve** | A store keeper or supervisor *stages* an entry; it changes stock only when the HOD *approves* it. |
| **Lot (batch)** | One batch of a material, e.g. PU COMP A batch `3504`. Lot-tracked materials are the Surface Shields except bricks. |
| **FEFO** | First Expire, First Out — issue the lot that expires soonest. In GI Hub expired lots go last and FEFO warns, never blocks. |
| **MFD** | Manufacture date. A missing expiry is MFD + the item's shelf life (*derived*). |
| **Roll register** | CHEMOLINE rolls, each a unit inside its batch (`1O…`, letter O). |
| **Lot Register workbook** | `Rubber & Brick Materials - CNCEC.xlsx` — adds MFD, expiry and rolls to lots; never changes stock. |
| **Used but never received** | A lot named on a consumption or return line that no receipt of that material brought in — a workbook typing slip. |
| **Quarantined / Disposed** | A lot the Admin took out of use (Admin Console → Lots). Never offered on the Issue form. |
| **Excel sync** | `tools/pg_excel_sync.py` — makes GI Hub's workbook-sourced rows match the workbooks (adds, updates, removes); never touches rows entered in GI Hub. |
| **Job (Surface Shield)** | One tank on one day for one store-keeper note; its area is credited once. |
| **Old / New surface** | The HOD's answer for a Garnet (blasting) job; it decides which benchmark the draw is compared with. |
| **Procurement chain** | Site PR → Logistics PO → Warehouse DN → Site receipt, all in the app. |
| **DN state machine** | `draft` → `pending_logistics` → `logistics_approved` → `pending_hod` → `hod_approved` → `pending_sk` → `received`; `rejected` is terminal from any pending state. |
| **RL/BL strict separation** | Rubber Lining and Brick Lining never share a PO group, DN or warehouse aggregation. |
| **Warehouse-blind pricing** | A warehouse user never sees prices on a PO. |
| **Shadow (Logistics / Warehouse)** | The Admin's access to those portals for absence cover or audit. |
| **Force-closure** | Logistics' one-way close of a PR / PO / line with a mandatory reason; notifies Admin and the site HOD. |
| **T-2 / T-1 / T-0 reminders** | Daily notices 2 days before, 1 day before and on the expected delivery date. |
| **Material Estimator (SME)** | HOD planning portal for lining material. It has its **own** material pool (from the estimator workbook), separate from the store's stock; *available* is what has arrived, *ordered* is the total procured. |
| **Man-Hours** | HOD labour tracking: roster, timesheets, estimator, variance. Writes only `mh_*` tables. |
| **OWN vs Supply** | Own (GI) staff vs subcontractor labour. |

---

## §11. Change Log

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06 | Initial release | Procurement chain: Site HOD, Logistics, Warehouse User, SK, Admin. RACI, cadences, 4 decision trees, escalation matrix, 7 recovery procedures, 5 quick cards. |
| 1.1 | 2026-06-28 | Update | Man-Hours and Material Estimator HOD cadences; glossary entries. |
| **2.0** | **2026-10-03** | **Rewrite** | Whole system, by role, for the React / FastAPI application. New: the system's day (§2); RACI for all nine roles (§3); daily plans for Store Keeper, Supervisor, HOD, Admin, Logistics, Warehouse, QC, Head of Qualities, Auditor (§4); the administrator's data procedures — Excel and Lot Register sync, stock vs Excel, backups, updates, Practice, power (§5); decision trees for lots and FEFO (§6.5–6.7); recovery for sync problems, offline entries and Practice/Live mistakes (§8.8–8.10); new quick cards (§9); lot and Practice terms in the glossary. Procurement trees, escalation and recovery from v1.0 kept as §6.1–6.4, §7 and §8.1–8.7. |

---

**End of SOP. Review quarterly. Companion documents: `USER_MANUAL.md` (every screen) · `MANUAL_TESTING_GUIDE.md` (how each feature is checked) · `docs/ARCHITECTURE.md` (how it is built).**
