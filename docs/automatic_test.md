# GI Hub — E2E Test Matrix (permanent checklist)

> **Purpose.** The single source of truth for full-application testing: every portal,
> tab, button and multi-user workflow, with the role locks each one must enforce.
> Re-run the relevant sections after every feature chunk; run everything before a
> release/cutover. Sections marked 🔁 are the cross-role workflows — they are the
> highest-value tests and must always pass.
>
> **Last full sweep:** 2026-07-12 night shift (visual, isolated `gihub_e2e` DB).

---

## 0. Test environment recipe (isolated — never the live DBs)

```bash
# 1. throwaway Postgres DB, cloned from the CI mirror (users + realistic data included)
psql -h 127.0.0.1 -p 5433 -U postgres -c "DROP DATABASE IF EXISTS gihub_e2e"
psql -h 127.0.0.1 -p 5433 -U postgres -c "CREATE DATABASE gihub_e2e TEMPLATE gihub"

# 2. hermetic backend on :8000 — no dotenv (⇒ NO live WhatsApp token), no schedulers
GI_DOTENV=0 GI_SCHEDULER=0 \
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub_e2e \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/uvicorn backend.api.main:app --host 127.0.0.1 --port 8000

# 3. Vite dev server (proxies /api → :8000)
npm run dev --prefix frontend
```

Safety invariants: `gi_database.db` (SQLite system of record) is never touched;
`deploy/.env` is never loaded (`GI_DOTENV=0` ⇒ `wa.enabled()` False ⇒ zero real
WhatsApp/Meta traffic; OTP endpoints return the friendly 503). Rate limits key on
client IP — browser traffic shares one bucket; login is 10/min, register 5/min.

**Seed logins** (from the mirror clone): `admin/admin2026` (admin, all sites) ·
`hod/hod2026` (hod) · `supervisor/super2026` (supervisor) · `worker/floor2026`
(store_keeper). No seeded logistics/warehouse user — create via §A1 or
register+approve (§W10).

**Role levels:** store_keeper 0 · warehouse_user/supervisor 1 · hod 2 ·
logistics 3 · admin 4. `require_roles(...)` always implicitly admits admin.
Reads for level <3 are pinned to the user's own site (fail-closed when siteless).

---

### Scripted suite (Playwright — the headless twin of this matrix)

```bash
cd tests/e2e && npm test          # 39 tests ≈15 s; builds+drops its own gihub_e2e_pw
npm run report                    # HTML report
```
`global-setup` loads the clone via the real cutover script and sets
`require_entry_documents='0'` so functional specs submit freely; the `gated`
project (`specs/entry-docs.spec.ts`) runs AFTER the parallel pack, flips the
setting ON, and tests the document gate itself. service_tests does the same
(`_relax_entry_gates()` + suite AH).

## 1. Cross-cutting chrome (every role)

| # | Check | Expect |
|---|-------|--------|
| C1 | Login (each seed user) | lands on role home: SK→/entry/issue, hod→/hod/approvals, logistics→/logistics, warehouse→/warehouse, supervisor→/supervisor, admin→/admin/console |
| C2 | Wrong password ×3 | clean 401 toast, no lockout surprises; 11th rapid attempt → 429 |
| C3 | Header: health dot | green + `postgresql · <db name>` correct |
| C4 | Theme toggle | dark⇄light, persists across reload |
| C5 | ⌘K command palette | opens, fuzzy-search only shows pages the role can access, Enter navigates |
| C6 | Notification bell | unread badge count = `GET /notifications/unread-count`; click row marks read + navigates to `link_page`; "Mark all read" zeroes badge |
| C7 | Profile modal | shows username/label; phone on file; dual-OTP flow wording (§W9) |
| C8 | Sign out | back to login; protected URL then redirects to login |
| C9 | Silent refresh | leave tab 15+ min (or expire token) → next call transparently refreshes, no logout |
| C10 | Session expiry | revoke session from admin console → next action shows session-expired warning, redirect to login |
| C11 | Sidebar badges | work-queue counts (approvals, incoming DNs, SK requests, returnables overdue, warehouse) match the queue pages |
| C12 | Admin "All areas" toggle | admin sidebar shows curated set by default; toggle reveals operational portals; persists |
| C13 | Direct-URL access to a forbidden page | UI redirects to role home; API returns 403 (never 500) |
| C14 | Hub Assistant FAB | opens; if Ollama offline → graceful "AI offline" state, no crash |

## 2. Dashboard `/` (level ≥1; SK redirected)

| # | Check |
|---|-------|
| D1 | KPI cards: inventory items / stock value / sites / expiring counts consistent with Records + Stock pages |
| D2 | Charts render (stock-vs-min, burn forecast, top-consumed); empty windows show empty states, not crashes |
| D3 | NL→SQL search card (level ≥3 only): visible for logistics/admin, absent for hod/SK; graceful when AI offline |
| D4 | Expiring table = `/stock/expiring` rows |

## 3. Stock `/stock` (level ≥1)

| # | Check |
|---|-------|
| S1 | Tabs: Live (global — level ≥3 only), By site, Lot balances, Expiring |
| S2 | Search `q` filters on SAP/name; category select filters; combination works |
| S3 | Expiring: `within days` input changes the horizon |
| S4 | HOD/scoped user sees only own-site rows in By site |

## 4. Records `/records/:key` + Master Data `/master/:key`

| # | Check |
|---|-------|
| R1 | Every entity opens & paginates: inventory (all roles), receipts/consumption/returns/lots/purchase-requests (hod+), purchase-orders (logistics+), equipment (hod) |
| R2 | Search + category + site filters (site select hidden below level 3) |
| R3 | Master Data (level ≥3): vendors/warehouses/employees — Add, Edit, Delete (popconfirm), Export xlsx |
| R4 | Negative: SK direct-URL to /records/receipts → redirected; API 403 |

## 5. SK Data-Entry portal (store_keeper; admin shadow)

| # | Check |
|---|-------|
| E1 | Receive: material picker + barcode scan; ItemSnapshot renders; rubber SAP requires MTC upload; pack-UoM conversion offered; batch add/edit/remove; **Submit batch to HOD**; DeliveryPrefRadio present |
| E2 | Issue: FEFO lot hint, bins hint, batch flow, over-issue allowed (logged, not blocked), DeliveryPrefRadio |
| E3 | Return: single form + reason codes, DeliveryPrefRadio |
| E4 | Adjust: reasons load, system vs counted diff staged |
| E5 | Count sheet: counted qty + variance; stages N adjustments |
| E6 | Returnables: loan a tool (due time), overdue badge, mark returned |
| E7 | OCR import: photo→job→poll or paste lane; review grid; stage rows (AI offline ⇒ paste lane still works) |
| E8 | Incoming deliveries: in-transit DNs listed; Receive → staged pending receipt (§W5 step 7) |
| E9 | SK requests: SMR queue; review modal (per-line qty, 0=withdraw); approve mirrors to HOD queue; reject |

### 5b. Entry-document gate (parity A1/A2 — `require_entry_documents` ON, the production default)

| # | Check |
|---|---|
| D1 | Issue/Receive batch submit WITHOUT a supporting document → blocked (client toast + server 422 "supporting document…") |
| D2 | "Attach file" and "📷 Photograph note" (camera capture on mobile PWA) both upload; chip appears; unlinked chip is removable |
| D3 | After upload, the same batch submits 201; the attachment shows "submitted/linked" in the Document Library |
| D4 | Return form: source-receipt picker only lists receipts ≤30 days (365 with the override checkbox); return qty is CAPPED to the source receipt qty; Return DN No. + document required; >30-day source demands a justification |
| D5 | HOD Approvals: returns with override show the red ">30d" tag (hover = justification); every row's 📎 button opens the entry-date-matched documents with inline image/PDF preview |
| D6 | /hod/documents Document Library: type tabs, doc-number + date filters, download + preview; SK gets 403 on the full library but can list own uploads (mine=1) |
| D7 | MTC: a `Surface Shields`-category receipt cannot be added without an MTC upload (422 + logistics email); MTC number field persists with the upload |
| D8 | WBS: once the HOD adds an active WBS for the site, Issue/Receive without one → 422; the form shows a required WBS select; closing the WBS lifts the gate |
| D9 | Draft recovery: type into an entry form, reload → "unsaved form draft" banner; Restore refills (incl. dates), Discard clears |

## 6. HOD portal (hod; admin shadow)

| # | Check |
|---|-------|
| H1 | Approvals: tab counts = `GET /hod/pending`; per-row Edit (whitelisted fields), Approve, Reject (mandatory reason), bulk approve; negative-stock preflight banner; DN tab = two-stage queue (§W5 step 5) |
| H2 | Executive Summary: date presets + from–to; KPI hero + all 13 sections; **Download PDF** (server-rendered, paginated, nothing clipped); **Download Excel** (14 sheets); admin extra site selector |
| H3 | Burn rate: site/days filters change data |
| H4 | Low stock: list + **Auto-draft PR** → lands in /hod/prs |
| H5 | PRs: create with lines; import from PDF (graceful if AI offline); line edit/rename while draft; **Submit to Logistics**; PDF download |
| H6 | Cross-site: raise request; admin decides; owner cancels while pending |
| H7 | SME: all read tabs render for the site; exports download; numbers match legacy goldens |
| H8 | Man-Hours: employees CRUD, timesheet import (dry-run first), distribute SQM, estimates + auto-draft, variance + reason, scorecard exports |

## 7. Logistics portal `/logistics` (level ≥3)

| # | Check |
|---|-------|
| L1 | Incoming PRs: queue shows submitted PRs; Create PO (PR → in_po); Force-close with reason |
| L2 | Create PO manual + vendor inline-add |
| L3 | Import PO PDF (graceful if AI offline) |
| L4 | Purchase Orders: KPI hero cards click-to-filter; expandable items; per-line force-close; **Assign to warehouse** |
| L5 | DN Approvals: stage-1 decide (approve → pending_hod; reject → back to warehouse) |
| L6 | Vendor returns: raise (reopens PO line) and close |
| L7 | Reschedules: approve/reject warehouse requests |
| L8 | Force-closures log: undo within 24h |

## 8. Warehouse portal `/warehouse` (warehouse_user, logistics; admin)

| # | Check |
|---|-------|
| WH1 | Assignments: acknowledge → receive (partial supported) |
| WH2 | Prepare DN (qty+lot per line) → submit for approval |
| WH3 | Ship blocked until hod_approved; works after |
| WH4 | Returns from site: record + disposition (restock/scrap/return_to_vendor) |
| WH5 | History tab shows completed DNs |
| WH6 | warehouse_user pinned to own warehouse; logistics/admin pick |

## 9. Supervisor portal `/supervisor`

| # | Check |
|---|-------|
| SU1 | New request: worker picker, old-PPE switch + reason, line stock hints, submit |
| SU2 | My requests: cancel while pending_sk; item expand |
| SU3 | Intent vs actual variance table |

## 10. Admin portal (level 4)

| # | Check |
|---|-------|
| A1 | Users: create (each role; scoped roles need site), edit, reset password, reset 2FA, delete (self-delete + last-admin blocked) |
| A2 | Access requests: approve (role+warehouse choice) creates login; reject |
| A3 | Overdue actions: list >24h items; Notify nudges assignee; Clear |
| A4 | Inventory admin: create/edit; delete blocked when movements exist |
| A5 | Audit log: filters (user/action/table/q) work |
| A6 | Console tabs: Overview KPIs · WhatsApp outbox + retry · Email outbox + retry · Lots (quarantine⇄release→dispose; dispose terminal 409) · Sites add/delete (delete blocked when users bound) · Settings (maintenance switch blocks non-admin login; thresholds persist) · Backup (pg_dump file lands) · Sessions (revoke one/all → target user logged out) · Oversight · Feedback respond |
| A7 | Reports page (shared, level ≥2): every report card downloads xlsx/pdf/csv; archive + delete; schedules create/toggle/run-now; AI tab streams (or degrades) |
| A8 | Documents: SOP/manual download; QR labels + badges (level ≥2); master exports |

## 🔁 11. Multi-user workflows (the night-shift core)

**W1 — Entry approval (SK→HOD):** *(with the doc gate ON, every stage step below first needs a supporting document attached — see §5b)* SK stages receipt+issue+return (E1–E3) → HOD sees exact rows in Approvals, edits one, approves receipt+issue, rejects return with reason → SK sees bell notifications (approved + rejected w/ reason); ledger rows exist in Records; stock moved; rejected row in archive, NOT in ledger.
> ⚠️ Scripted-suite findings (2026-07-13): (a) **receipt** approvals do NOT bell the submitter — `pending_receipts` has no submitter column (`_SUBMITTER_COL` maps receipts→None in `backend/api/hod.py`); assert the submitter bell on **returns/issues/adjustments** instead. (b) On pending returns the entry form's `Reason` lands in `Return_Reason` (and `Remarks` in `override_reason`); the committed ledger row stores it as plain `Reason`. (c) SK visiting `/stock` is landed on the Issue page by the role manifest — assert that lock, not a Stock render.
**W2 — SMR (Supervisor→SK→HOD):** supervisor raises 2-line SMR → SK adjusts one qty, withdraws other (0) → approve ⇒ pending_issue appears in HOD queue tagged SMR → HOD approves → consumption ledger row; supervisor intent-vs-actual updates.
**W3 — PR→PO (HOD→Logistics):** HOD auto-drafts from low stock or creates PR → edits line, renames, submits → logistics sees it in Incoming PRs → creates PO ⇒ PR in_po; PR PDF downloads at each stage.
**W4 — PO assignment (Logistics→Warehouse):** assign PO → warehouse acknowledges → receives (try partial) ⇒ assignment delivered/partial, PO status updates.
**W5 — DN two-stage (Warehouse→Logistics→HOD→Warehouse→SK→HOD):** prepare DN → submit (pending_logistics) → logistics approve (pending_hod) → HOD approve (hod_approved) → ship (in_transit) → site SK receives ⇒ DN received + staged pending receipt → HOD approves ⇒ ledger. Also: logistics reject → warehouse resubmit; HOD reject.
**W6 — Cross-site:** HOD site A raises vs site B → admin approves → requester notified.
**W7 — Registration:** register scoped role (site dropdown from admin-created sites) → admin approves → login works; reject path; duplicate username re-register.
**W8 — Notifications end-to-end:** every W1–W7 action lands an in-app bell row for the right recipient (WhatsApp disabled in test env ⇒ outbox untouched); evening preference on an entry form stages instead (visible `GET /admin/digests/pending`); admin "run digest now" drains it.
**W9 — Dual-OTP phone change:** user with number on file: code1→old, code2→new, DB unchanged until code2; first-time user: single code to new number. In hermetic env expect the friendly "WhatsApp is not configured" 503.
**W10 — Lot lifecycle:** admin quarantines lot (SKs get critical notice) → release → dispose (terminal; further changes 409).

## 12. Negative-access spot matrix

| Actor ↓ / Target → | /entry/* | /hod/* | /logistics | /warehouse | /admin/* | /mh | /sme |
|---|---|---|---|---|---|---|---|
| store_keeper | ✅ | 403 | 403 | 403 | 403 | 403 | 403 |
| supervisor | 403 | 403 | 403 | 403 | 403 | 403 | 403 |
| warehouse_user | 403 | 403 | 403 | ✅ (own WH) | 403 | 403 | 403 |
| hod | 403 | ✅ (own site) | 403 | 403 | 403 | ✅ | ✅ |
| logistics | 403 | 403 | ✅ | ✅ | 403 | 403 | 403 |
| admin | ✅ | ✅ (any site) | ✅ | ✅ | ✅ | ✅ | ✅ |

Also verify: scoped HOD asking `?site_id=<other>` → 403; single foreign row → 404 (no existence leak); maintenance mode blocks non-admin login with 503.

## 13. Visual/UX checks

- No layout overflow at 1280×720 and mobile (375×812): tables scroll inside cards, nav collapses.
- Dark AND light theme: no unreadable text, charts recolor.
- Every destructive action has a confirm; every failure shows a toast with a human message (never raw JSON/500).
- Empty states everywhere (fresh site with no data).
- Downloads: correct filename + extension + non-zero size (xlsx `PK`, pdf `%PDF-`).

---

*Maintained by the QA night-shift. When adding a feature, add its rows here in the same commit.*
