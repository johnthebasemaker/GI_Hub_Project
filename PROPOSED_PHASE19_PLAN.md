# PROPOSED PHASE 19 — closing the reorder loop and partial returns

*Drafted 2026-10-04 from the operator's rulings on `MORNING_REPORT.md` §3
(Q18-9, Q18-10, Q18-11). Nothing here is built yet. Each slice lists the
decisions it needs before code starts.*

**Why these three were not built into Phase 18's close-out.** The operator had
just migrated Live and tested the branch. The close-out added only what needs
**no schema change and no new business rule**: the slip, the site pace, the
holdout secrecy and the CI/branch-protection fix. Each item below changes what
a number *means* (the minimum, the on-order, the open quantity of a loan), so
each gets its own slice, tests and manual pages.

---

## 1. Q18-10 — the HOD accepts recommended minimums (per site)

**The ruling, as asked:** in the minimum-quantity column, suggest a number for
every material. The HOD ticks rows, edits a number and submits. Items that
already have a minimum keep being re-suggested from the latest 30-day
consumption, and the HOD approves before the minimum changes.

**Design.**
- **Store:** `inventory_site_overrides` (SAP_Code, Site_ID, Minimum_Qty,
  updated_by, updated_at). It already exists, is unused and needs **no
  migration**. Every accept writes a `system_audit_log` row (`MIN_ACCEPT`)
  carrying the recommendation it came from, so "why is the minimum 90?" always
  has an answer.
- **Precedence:** site override → `inventory.Minimum_Qty` (global manual) →
  smart recommendation. `SQL_SITE_STOCK` and the reorder signals both read the
  override, so the Stock pages and the Dashboard agree.
- **Re-suggestion:** a row is flagged **"changed"** when the fresh
  recommendation differs from the accepted minimum by more than a band (±20 %?
  ⚖️). The HOD sees *accepted 90 → now suggests 120 (use up 33 % in 30 days)*,
  and nothing changes until they approve.
- **UI:** a **Review minimums** mode on Reorder signals (HOD own site; admin
  any site). It has a checkbox column, an editable number prefilled with the
  recommendation, a "changed since accepted" filter, and one **Submit** that
  applies the ticked rows in a single transaction.
- **Tests:** a suite for precedence, the site wall, the audit row, the "changed"
  band, and an untouched `inventory.Minimum_Qty` (P18-advice still holds for
  the global column). E2E: tick, edit, submit, and the row turns from smart to
  **accepted**.

⚖️ **Needs:** the "changed" band (±20 %?). Should Logistics also accept, or
only the HOD? And should an accepted minimum ever expire?

## 2. Q18-9 — on-order per site

**The ruling:** POs can be raised globally, but most come from a PR, which
carries the site, and one PR can have several POs.

**Design.** `po_items.PR_Number` → `pr_master.Site_ID` attributes an open PO
line to a site, and **needs no migration**. A line with no PR (a global PO)
goes to a separate **global on order** figure, which is shown on every site's
row but *not* subtracted from any site's suggested order. Subtracting it
everywhere would count one PO many times.

⚖️ **Needs:** confirm that a global PO should not reduce any site's suggested
order. The alternative is to allocate it to the site with the largest
shortfall.

## 3. Q18-11 — partial returns, with reminders until the rest is back

**The ruling:** partial returns are needed, and the borrower is reminded until
the remaining quantity comes back.

**Design.**
- **Migration** (one, additive): `returnable_items.qty_returned` (Float, default
  0). Each partial return is a row in a new `returnable_returns` (loan_id, qty,
  condition, note, returned_by, returned_time). A loan is `returned` when
  `qty_returned >= qty`; until then it is **partly returned** (a new status
  tag), and it stays in Open / Overdue.
- **Desk:** a quantity stepper per loan, defaulting to the open quantity. The
  condition and note apply to that part only (3 good + 1 damaged = two
  returns).
- **Reminders:** the overdue chase (`returnable_overdue`) fires for the
  *remaining* quantity, once when due and then **daily** until it is back. The
  daily cadence needs a ruling ⚖️, and it uses the existing `daily_job_runs`
  claim so only one worker sends.
- **Slip:** the slip shows "returned 3 of 5" on a re-print.

⚖️ **Needs:** the reminder cadence (daily? every shift?). Should the HOD also be
told after N days? And may a partial return be undone?

---

## 4. Also queued (from `MORNING_REPORT.md` §4)

- **4.1 semantic safety signal** (`nomic-embed-text` kNN). It is the most
  promising route to the 0.95 target, and the operator's blind holdout v2 is
  now the honest scoreboard for it.
- **4.3 one scan service + `GI1|…` sticker payloads.** The loan slip already
  uses the one loan syntax (`#id`) the desk resolves.
