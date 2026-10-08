# GI Hub — System Architecture (the "brain" document)

> **Purpose:** a fresh AI instance (or engineer) reading ONLY this file plus
> [`PROJECT_STATUS.md`](PROJECT_STATUS.md) must understand the exact state,
> tech stack and rules of the project with no chat history. Written 2026-07-13;
> finalized 2026-07-18 (pre-deploy batch); updated 2026-07-26 (native-app
> program); **updated 2026-07-30 (SME allocation overhaul: two-tier
> Available-vs-Ordered + reverse SQM + COMPONENT IDENTITY; global table
> tools)**; **updated 2026-08-13 (workflow polish + test isolation)** at gates
> **updated 2026-09-03 (Phase 9 paper-first OCR + Phase 10 security, analytics
> and training — see §7a–§7d)**; **updated 2026-09-05 (Phase 11 tracing,
> guards, gateway, eval gates — §7e–§7i)**; **updated 2026-09-07 (Phase 12
> automated role tutorials and the assistant's deep links — see §7j and
> §7k)** at gates
> `ci_preflight 10 controls · service_tests 2,328/0 (suites A…CX, own throwaway
> DB) · Playwright 128/128 · AI evals Tier 1 147/147 (0 leaks), recall 1.000 /
> precision 0.994 · parity:sme 1,313 · ui-math 33/0 · bug_check 599/0/0 ·
> nav 51 routes · build+tsc ✅ · alembic single head a1c9e64b3d70`.
> **Updated 2026-10-03 (Phases 13–16 — §2a module map, §4f Garnet, §4g lots &
> FEFO, §4h the Phase 14 ledger rules, §5a the login and the critical path,
> §6a Practice)** at gates `ci_preflight ✅ · service_tests 2,762/0 · Playwright
> 178/178 · AI evals Tier 1 147/147 (0 leaks), recall 1.000 / precision 0.994 ·
> grid 72 cases · parity:sme 1,334 · ui-math 33/0 · nav 52 routes · bug_check
> 599/0/0 · build ✅ + critical path +0 B · CI derived-view parity 5/5 · alembic
> single head e5b2c7a9d4f1`. The gate line above is the older 2026-09-07 one,
> kept for the trend.
> **The Hetzner deployment is PAUSED by decision.** Locked rules + baselines in
> one page: [`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md).

---

## 1. The two applications (segregation contract)

| | LEGACY (production, frozen) | NEW STACK (ship-ready) |
|---|---|---|
| UI | Streamlit (`main.py`, `pages_internal/*.py`) | React 19 + antd 6 + Vite (`frontend/`) |
| API | — (monolith) | FastAPI (`backend/api/`), uvicorn `:8000` |
| DB | SQLite `gi_database.db` (**system of record until cutover**) | PostgreSQL 16 — CI mirror `postgresql://postgres@127.0.0.1:5433/gihub` |
| Deploy | on-prem | Hetzner CPX42 plan + Cloudflare Tunnel (`gi.giinventory.com`), nginx, deploy/ |

Rules ([REPO_MAP.md](../REPO_MAP.md) is the contract): never edit
`legacy/database.py` / `legacy/pages_internal/` for new-stack work; new-stack
commits touch only `backend/`, `frontend/`, `deploy/`, `tests/`, `docs/`.
**Phase B executed 2026-07-13**: the legacy app lives under `legacy/`, root
data artifacts under `data-archive/`, and the bridge tools under `tools/`
(`dual_ci.py`, `migrate_sqlite_to_postgres.py`, `parity_check.py`,
`migration/cutover_migrate.py` + runbook); `gi_database.db` deliberately stays
at the repo root (bridge tools + the final production load read it there, and
it must never be staged). `tools/dual_ci.py` reloads the mirror from SQLite
and verifies 5 semantic aggregates; the production cutover script is
`tools/migration/cutover_migrate.py` (sync psycopg2 URL, `--strict --wipe`;
asyncpg URLs fail with MissingGreenlet by design). After every mirror reload,
re-run `backend/scripts/create_ai_readonly_role.sql` (grants get wiped).
**⚠️ Excel injection: PostgreSQL is permanently AHEAD of the frozen SQLite**
(2026-07-13 CNCEC workbook sync + 2026-07-18 re-sync: inventory 306→442,
full ledger backfill, stock verified **429/429** vs the workbook). A
`dual_ci`/cutover reload from `gi_database.db` WIPES that data — after ANY
reload re-run the sync (same on the production box after the final load;
the runbook says so):

**Preferred since 2026-07-27: `tools/pg_excel_sync.py`** — the single-entry,
ATOMIC replacement (one transaction across all five kinds; a failure anywhere
rolls the whole sync back). Dry-run by default:

```bash
DATABASE_URL=postgresql+psycopg2://…/gihub \
  .venv/bin/python tools/pg_excel_sync.py --site CNCEC            # dry-run
DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC --commit
DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC \
    --sme-reseed --commit                            # SME wholesale replace
```

It imports `bulk_import.py`'s planners and replaces ONLY the write path, so
column mapping is never duplicated; every master write is `ON CONFLICT … DO
UPDATE` with `COALESCE(excluded.col, table.col)`. **It must never import
Pandas** (absent from `backend/requirements.txt` — it arrives only
transitively via streamlit for the legacy app). It refuses non-Postgres URLs
and any URL mentioning `gi_database`. Exit code 1 after a successful commit
just means the stock-verification line found mismatches — that is a signal,
not a failure.

The older per-kind chain still works and is what the historical runbook used:

```bash
tools/excel_sync.py --site CNCEC --commit            # header-NAME-driven; all 4 workbooks at repo root
tools/excel_sync_reconcile.py --commit               # zeroes superseded rows; date-less lines
tools/excel_sync.py --site CNCEC \
    --kinds sme-equipment,sme-recipes,sme-materials \
    --sme-reseed --commit                            # SME trio: wholesale replace (see below)
```

Sync mechanics (2026-07-18 final): every sheet's columns resolve **by header
name** (reorders/additions in the workbooks are safe; unknown columns warn,
never silently drop). `--kinds` restricts a run; **`--sme-reseed`** drops
recipes (global) + equipment/progress (per-site) + seeds before the SME loads
— REQUIRED whenever the workbook renumbers `Lining_System_Code`s (an upsert
would leave stale old-code rows double-counting SQM); it aborts if any
`Done_SQM > 0` would be lost unless `--force-drop-progress`. Recipe line
identity is **(code, material, SAP_Code)** — PU systems carry Comp-A/B/C/D
lines sharing one Material_Code, distinguished only by variant SAPs
(1041/-1/-2/-3); SAP-aware files merge repeated identities as coat lines
(For_1_SQM sums), legacy no-SAP files keep first-occurrence-wins.
**STOCK identity matches it since 2026-07-30**: `sme_inventory_seed`'s PK is
`(Material_Code, SAP_Code)` (alembic `a4e9b1c73f28`), so each component drum
holds its own quantities. SAP codes are whitespace-normalized on both sides
of every join (the ERP writes `"1043 - 2"` for `"1043-2"`). ⚠️ The frozen
legacy SQLite has NO `SAP_Code` column on either SME table, so a cutover
lands 86 blank-SAP recipe rows + one blank-SAP seed row per material; the
86 are REAL data (disjoint from the workbook's coded pairs — measured), and
a blank-SAP seed row is retired only when no blank-SAP recipe line still
references it. `--sme-reseed` is the remedy for a mixed state.
`tools/parity_check.py` fails against the live mirror BY DESIGN (only
meaningful on CI or a freshly-reloaded mirror). Executed 2026-07-18:
inventory+ledger 429/429, SME reseed run by the operator (recipes 41, codes
1–10, all SAP-mapped).

## 2. Backend map (`backend/api/`)

FastAPI app in `main.py`. The lifespan starts **five** daemons — report
scheduler, 16:00 evening digest, Friday 17:00 weekly exec PDF, 07:00 morning
briefing, and the AI-job orphan sweep — all disabled by `GI_SCHEDULER=0`, plus
the Bloom-filter refresh (§7b). It also runs three one-shot boot steps: the
orphan sweep, the manual-index warm and the read-only-wall probe.

⚠️ **THREE OF THOSE FIVE ARE DAILY, AND EACH RUNS IN EVERY WORKER.**
Production is `uvicorn --workers 4`, so a daily loop without a claim dispatches
four copies of everything it sends. `services/dailyjob.py` is that claim and all
three daily loops now take it — see §7d for the bug it closed.

`models.py` (repo `backend/models.py`) is the single schema contract; alembic
migrations in `backend/alembic/versions` (single head **`e5b2c7a9d4f1`** since
2026-10-01 — the Phase 13–16 chain after `a1c9e64b3d70` is listed in §2a; the
history before it, from 2026-09-07: single head `a1c9e64b3d70` = 11e
`ai_answer_cache`; before it `f8a3c05d1b27` = `ai_traces`, `e7f2a4c916b8` =
slice 10b's execution `Shift` + `daily_job_runs` + the training registry,
`d5b8c3f92a41` = `rate_buckets`, `c4a7e2b81f36` = ai_jobs worker heartbeat,
`b8d3f1a72c94` = ai_jobs indexes + payload purge). ⚠️ **Phase 12 added no
migration at all** — it fills tables slice 10b created. Modules:

| Module | Owns |
|---|---|
| `auth.py` | bcrypt login, 15-min JWT access + **RTR refresh families in `refresh_sessions`** (signed refresh JWT; `client_type` web 7d / native 90d; replay ⇒ THAT family revoked, other devices survive — full model in §6), TOTP 2FA, role levels (SK 0 · warehouse/supervisor 1 · hod 2 · logistics 3 · admin 4; `require_roles` always admits admin; `site_scope` pins level <3), registration + admin approval, **dual-OTP phone change** (`phone_otp.stage` 'old'→'new'; commit only after the NEW number verifies) |
| `entry.py` | SK staging: receipts/consumption/returns/adjustments + `/entry/bulk`. Guards: **the MTC gate for `Category == "Surface Shields"` binds at ISSUE, not at receipt** (setting `mtc_required_category`; moved 2026-08-12 — a receipt is never refused, and an uncertified goods-in notifies Logistics instead), pack→base UoM conversion (`uom_conversions`), **WBS required when the site has active `wbs_master` rows**, **supporting-document gate** (below), return source-receipt gates, FEFO auto-pick w/ allow-and-log override alerts. **`GET /entry/lining-systems`** (2026-07-18): recipe SAP lists per system code + site Done/Pending SQM — powers the Surface-Shields system-first Issue workflow (UI enforces: shield SAP without a selected system is refused; the code travels as an `LS <code>` Remarks suffix) |
| `bulk_import.py` | **Bulk Excel Import** (`POST /import/{kind}`; kinds `inventory`/`ledger` admin-only, `sme-*` {hod,admin}): dry-run→commit, upsert-only, header-name-driven, category canonicalisation, 3-tier ledger reconcile (exact-match / qty-correction / insert), Material_Code uniqueness resolution — the same plan/apply code `tools/excel_sync.py` drives |
| `entry_docs.py` | **Entry document system (parity A1/A4)**: `entry_attachments` upload/list/download/delete, `require_entry_documents` gate, WBS config endpoints. Doc types `consumption`/`receipt`/`return`/`safety_approval`/**`delivery_note`** (2026-08-13, the scan a warehouse must attach before a DN may ship) — the last two are per-item and per-shipment respectively, so neither goes through the per-BATCH `assert_entry_docs` gate |
| `hod.py` | pending queues, per-row approve/reject(+reason)/edit (`{"fields":{...}}`), `bulk-approve` (≤200), submitter bell dispatch (receipts have NO submitter column by design — returns/issues/adjustments do), return-approval → logistics email |
| `exec_summary.py` + `exec_pdf.py` | Executive Summary JSON/xlsx/**server-rendered fpdf2 PDF** (content-measured tables; nothing clips). **Slice 10b:** `/hod/valuation` + `/hod/valuation/export.pdf` — the board brief, rendered by `_ValuationPDF` (subclasses `_ExecPDF`, so there is ONE branding implementation). hod · auditor · admin via `_EXEC_READERS`. See §7d |
| `weekly_report.py` | Friday 17:00 auto exec-PDF → `generated_reports` + sha256-tokenized 72-h link `/reports/weekly-exec/{token}` → WhatsApp+bell to every admin/HOD; `POST /admin/reports/weekly-exec/run`; needs `PUBLIC_BASE_URL` in deploy/.env |
| `lining_analytics.py` | `GET /analytics/lining-coverage` — read-only SME engine with **live-ledger availability pool**; RL/BL family coverage + 90-day-burn depletion dates (hod/logistics; scoped site-pinned, default CNCEC) |
| `logistics.py` / `warehouse.py` / `receiving.py` | PR→PO→assignment→DN two-stage approval state machine (`draft→pending_logistics→…→received`), RL/BL family separation, reschedules, force-close + 24 h undo, vendor returns |
| `requests.py` | supervisor SMRs (worker must be an active employee at the site) → SK approve → HOD issue queue |
| `sme.py` + `sme_engine.py` | SME read layer + planning engine — **dual TS/Python engines with golden parity; change BOTH or neither** (frontend twin: `frontend/src/sme/engine.ts`). **2026-07-30 COMPONENT IDENTITY:** every pool/total/shortfall/report row keys on `Material_Key` = `mat_key(Material_Code, SAP_Code)`; `sap_norm()` strips whitespace. **2026-08-05 SUBSET RULE:** the workbook's `Ordered_Qty` is the TOTAL procured and `Available_Qty` the arrived part OF it, so tier 2 = `max(ordered − available, 0)` (`Alloc_Pending` / `pool_pending_init`) and `Allocated_Qty` = `max(available, ordered)` — the additive reading understated the buy list by 22,951 units. Suite BC. **2026-08-04 SCOPE-WIDE BOTTLENECK:** every scope-level coverage KPI is `Σ buildable m² ÷ Σ remaining m²` (`session.ts scopeBottleneckCoverage`, shared by the Session and Location reports), never a quantity average — gated by `npm run test:ui-math`. **2026-08-03 STRICT TIER SEGREGATION:** readiness reads `Alloc_Available` ONLY (`Status`, `Completion_Pct`, `SQM_Achievable_Now`, `Coverage_Now_Pct`, `Fulfillment_Pct`); `Alloc_Ordered` feeds only the `*_With_Ordered_*` twins + the net buy list; `Allocated_Qty` is a conservation field and never a coverage numerator. **2026-08-02 STRICT DECOUPLING:** the estimator is a separate pool from the ERP warehouse — `available_qty` **is** `Initial_Available_Qty`, `ordered_qty` **is** `Initial_Ordered_Qty`, both from `sme_inventory_seed`; `receipts`/`consumption`/`returns`/`inventory` are never read, and the 2026-07-28 on-order netting `max(Initial_Ordered_Qty − Σreceipts, 0)` is GONE with them (it only ever undid receipts flowing into availability). Suite BA. **2026-07-28 two-tier allocation:** `Allocated_Qty = Alloc_Available + Alloc_Ordered`, `Shortfall_Available_Qty` (physical → feasibility) vs `Shortfall_Qty` (net → buy list), plus reverse-SQM `build_sqm_rollup`/`build_sqm_by_code` where a unit's achievable area is its SCARCEST component's rate. **`GET /sme/calculator?code&sqm`** (2026-07-18, level ≥2): recipe demand math `For_1_SQM × SQM` per component line, pack counts from Package_Size, **SME seed stock per COMPONENT `(Material_Code, SAP_Code)`** — 2026-08-04 overturned the last Material_Code pooling here too (was the ERP ledger until the 2026-08-02 decoupling); `sap_norm()` matches the SQL's `REPLACE(TRIM(...))` on both sides + per-line shortfall + human explanation strings — the 🧮 Smart Calculator tab's backend |
| `sme_master.py` | **Phase S6 (cutover day): Master Data CRUD** — `/sme/master/*` equipment/recipes/materials-seed/progress/settings, exact-lock {hod, admin}, HOD site-pinned, every write audited; equipment create seeds `sme_sqm_progress`, delete cascades it; materials write `sme_inventory_seed` ONLY (Canon Rule 2) |
| `ai/` | Hub Assistant SSE, OCR lanes, PDF extract, `/ai/nl-search` (unscoped, Ollama→safety gate→`gi_ai_ro` read-only PG login), **`/ai/query` two-lane chat-with-your-data** (below), **`ai/handwritten.py`** — the handwritten-consumption-form spec implementation (below §7), and **`ai/tutorials.py`** — the Phase 12f deep-link matcher (§7k): one BM25 chunk per recorded BEAT, the role fence built from each script's `audience` and applied BEFORE scoring (P12-6). Read-only, deterministic, and allowed to be absent |
| `notifications.py` + `services/notifications.py` | in-app bell (`app_notifications`) + unified `dispatch()` (bell ALWAYS + best-effort WhatsApp; `X-Delivery-Preference: evening` stages into `pending_summary_notifications` for the 16:00 digest; critical always immediate) |
| `services/whatsapp.py` | Meta Cloud API v2 outbox (`whatsapp_outbox`), approved templates `gi_action_required/gi_status_update/gi_critical_alert/gi_otp_code/gi_evening_summary` (lang **`en`**), friendly #131030 sandbox handling |
| `webhook.py` | inbound Meta webhook (`/whatsapp/webhook` + `/api/v1/…`): verify-token handshake, **X-Hub-Signature-256 HMAC**, STOCK/RESET PASSWORD commands, session-text replies |
| `ratelimit.py` | see §6. **Slice 10a:** the four per-process limiters gained a cross-worker half in `rate_buckets` — Postgres, not Redis (§7c). ⚠️ `read_bucket_shared` ≠ `check_bucket_shared`: the counting one INCREMENTS, so asking it "is this IP banned?" creates the ban |
| `console.py` | admin settings (whitelist incl. `maintenance_mode`, `require_entry_documents`, `mtc_required_category`), pg_dump backup, sessions revoke, outbox retries, lot lifecycle. **Bug Tracking Engine (2026-07-18)**: `bug_reports` + `title/severity/rollback_notes/safety_constraints/triage_notes`; admin triage drawer; **`GET /admin/feedback/{id}/prompt`** renders a self-contained coding-agent implementation prompt (report + triage + rollback plan + the project's non-negotiable gates) and `GET /admin/feedback-export.md` a batch digest — the portal never mutates code itself |
| `documents.py` | SOP/manual downloads, QR label sheets, employee badges, **`GET /documents/material-stickers`** (2026-07-24, {hod,admin}): 2×6 full-bleed A4 rack stickers replicating the operator's CNCEC sheet — Material Name (auto-shrink 17→11pt), QR from `SAP_Code`, SAP/MAT lines, category; `sap_codes` repeats = copies, category filter, site-scoped |
| `stock.py` | stock views + **`GET /stock/material-card?sap=&days=`** (2026-07-24; **Material Intelligence 2026-08-01**): the 📷 scan payload. `sap` resolves a SAP code, a **Material_Code**, OR a raw label payload (`"1163\|Cable Tie Wire ( Nylon)"` — the operator's stickers are `SAP\|Description`, and passing the whole string was the reported 404); `site_scope` pinning (''→403) is applied AFTER resolution. Returns stock + a gap-free 7–365-day receipt/consumption series with a **backwards-walked closing-balance line**, burn rate + **days of cover**, open **lots** (FEFO order, balance derived like SQL_LOT_BALANCE), last 12 **movements**, and a per-site split for unscoped roles only |
| `qc.py` | the `qc` role, dual scoping (`qc_scope`), accounts + admin-decided transfers, the `qc_inspections` ledger and the one decide endpoint. **2026-08-13:** the list/fetch are decorated with the material NAME (from `inventory."Equipment_Description"`) and the certificate's number/filename, and `GET /qc/inspections/{id}/certificate` streams the MTC **through the inspection**, inheriting its scoping rather than re-deriving it. A rejection mints `return_no` = `QCR-YYYYMMDD-<id>` |
| `health_monitor.py` | the 07:00 Morning Briefing: **ten** probes, each individually guarded, silent on a clean run but always audited, body forced to ONE line (Meta rejects a newline in a template parameter). **2026-08-13:** `probe_missing_mtc` + `dispatch_missing_mtc` — uncertified Surface Shields, grouped by PLACE and routed by location (warehouse → logistics/warehouse_user/qc; site → store_keeper/hod/qc/logistics) because the briefing's own admin+HOD audience cannot fix it. **Slice 10b:** `probe_day_shift_mtc` + `dispatch_day_shift_mtc` — uncertified material staged for TODAY'S day shift, which unlike the standing sweep has a deadline measured in hours. Channels chosen by WHO is asked: in-app+WhatsApp to colleagues, EMAIL to Logistics only, and a WhatsApp **DRAFT** for anyone outside the company. Rides the same 07:00 `dailyjob` claim |
| `testdb.py` | **the throwaway database the service tests run against (2026-08-13, rule 15).** Provisions `gihub_svctest` from `gi_database.db` via the production cutover script and rewrites `DATABASE_URL` before `db.py` is imported; refuses to run if source and target are the same name. `_apply_fixtures` carries the state a cutover-built database lacks (the AI read-only role, the `employees` site backfill, the nine PPE SAPs, the entry-doc switch) |
| `service_tests.py` | the **2,328-check** gate (suites A…CX), see §8 |
| `training.py` | **Track 5 (slice 10b):** the training hub and the SOFT gate. `/training/modules`, `/training/gate/{feature}` (⚠️ `allowed` is unconditionally true — it reports, it never refuses), `/training/progress` (monotonic), `/training/acknowledge` (refused below 90% watched), `/training/defer` (the "Watch later" record), `/training/compliance` (HOD; driven from `users`, not from the compliance table), and the admin asset/version endpoints. See §7d. ⚠️ Phase 12 is what FILLS it: `storage_uri` and the long-empty `captions_uri` are written by `POST /training/assets` from the artefacts `tools/generate_tutorial.py` produces (§7j), and the page honours `?module&lang&t` so the assistant can deep-link a step (§7k) |

## 2a. Phases 13–16 — the modules they added (2026-09-10 → 2026-10-01)

**Migrations after `a1c9e64b3d70`, in order:** `b2d47f6c91ae` form print
batches · `c3e58a7d24bf` form sheets seen · `d4f61b8e35ca` Phase 12 training
modules · `e5a72c94f16d` SME consumption-log component identity ·
`f6b83d1a27c9` ledger workbook row labels (the `XLSX:` sync identity) ·
`c41d7e9a2b58` 14a Unit Size · `d7a3f05c1e92` 14b reconciliation ·
`e8b4c16d2f03` 14c attribution groups · `f2c9a7d41b36` 14d announcements ·
`a3d5e7f91c24` stock-vs-Excel checks · `b7e2c4a19d53` 15d Garnet prep baseline
· `c4f1a8d2e6b7` 15e job `Work_Area` · `d8a3f6c1b2e9` 16 lots (lot columns,
`returns.Lot_Number/Serial_No`, `inventory.Lot_Tracked/Shelf_Life_Months`,
`lot_units`) · **`e5b2c7a9d4f1`** 16c `pending_receipts.MFD_Date`.

| Module | Phase | Owns |
|---|---|---|
| `services/units.py` | 14a | The ONE home of the pack ⇄ base-unit factor (`inventory.Unit_Size`). Convert at READ; the ledger stays in packs. A Surface Shield with no Unit Size shows "— (no unit size)", never 1 kg per can. |
| `services/reconcile.py` | 14b | QR ⇄ Excel: the ledger holds **max(QR, Excel)** per (site, day, resolved tag, SAP) — one quantity per bucket, never the sum. |
| `services/sme_groups.py` | 14c, 15e | The grouped Surface Shield queue: one job = one tank on one day; SQM credited once. 15e: `parse_note`/`notes_of` read the store keeper's remark (`Floor - 13.37 SQM Done` → area, part, remark); two notes = two jobs; `Work_Area` + notes kept on the group and log rows and shown to the HOD, the Production Log and the Executive Summary. |
| `announcements.py` + `services/announcements.py` + `services/tutorial_staleness.py` | 14d | "What's new": YAML in `docs/announcements/`; the audience is read from the nav snapshot `backend/api/data/nav_access.json` (refresh with `tools/announcements.py nav`; `npm run test:nav` fails if stale). Tutorial staleness warns admins only. |
| `stock_excel.py` + `services/stock_excel.py` | 14 follow-up | Stock vs the workbook's Current Stock, with signed causes that sum to the difference; stored in `stock_excel_checks`; `tools/stock_excel_check.py --marked` writes a marked COPY (never the original — openpyxl drops DV extensions). |
| `schema_head.py` | 15a | Boot guard: a process whose database is behind the alembic head refuses to start (Practice was six migrations behind and 500ed). `tools/practice_db.py migrate` brings both Practice DBs up. |
| `services/prep.py` | 15d | Garnet = surface preparation (see §4f). |
| `admin.py` `item_router` (`/inventory-items`) | 15e | HOD + admin add/edit inventory items; the HOD is pinned to their own site; delete stays admin-only. Site / Category snapped to the spelling in use (422 `unknown_*` unless `confirm_new`). |
| `services/lots.py`, `services/lot_file.py`, `lot_register.py` | 16 | Lots & FEFO (see §4g). |
| `practice.py` + `tools/practice_overlay.py` (OVERLAY_VERSION 3) | 17g | Every Live feature ships a Practice example: job notes (`seed_job_notes`), lots (`seed_lots`: PR-OLD / PR-SOON / PR-LATE, CHEMOLINE 899973 with rolls, PR-TYPO). |

**`stock.DERIVED` and the CI parity check.** Each derived view has a SQLite
original (`legacy/`, frozen) and a Postgres port. `tools/parity_check.py`
(CI step *Derived-view parity*) compares them on the CI fixture. A port whose
shape has legitimately moved past the frozen view supplies `parity_sql` (and,
if needed, `parity_source_sql`) so parity is asserted on the invariant both must
still share: `sme_materials` since 2026-07-30, and **`lots` since 2026-10-03** —
`SQL_LOT_BALANCE_PARITY` projects the legacy columns and adds the returned
quantity back, because legacy `returns` carry no lot. ⚠️ This step is not in the
local gate list; reproduce it with the recipe in MANUAL_TESTING_GUIDE §16e
after touching `stock.py`.

## 2b. Phase 18 — Night Shift (2026-10-03)

Plan, measurements and deviations: `PROPOSED_PHASE18_PLAN.md`.

- **Router (Track 1).** `ai/guard_patterns.yaml` **v3**: twelve weight-2
  combination signals plus `encoded.disguised` (`guard.DISGUISE_HIT`), so the
  guard alone refuses 36/39 dev attacks. `system_one.is_howto` gives a stage-0
  `MANUAL_QA` with no model call. `system_one.CACHE` holds the deterministic
  router's ANSWER (never the decision: the guard and Q17-2 run every request).
  `warm()` primes the real system prompt, because Ollama keeps ONE prompt cache.
- **CI (Track 2).** `ai-router-eval` is a hard gate (no `continue-on-error`);
  its L3 dev-block floor is 0.90.
- **Return desk (Track 3).** `returnable_items` gains `SAP_Code`, `Item_Ref`,
  `returned_time`, `returned_by`, `return_condition`, `return_note` (alembic
  `a7d3e1f5c829`, head). `entry.py`: `GET /entry/returnables/resolve` (a scan
  resolves to `loan` / `item` / `employee` / `material` / `none`),
  `POST /entry/returnables/return-batch` (per-id skips), and an optional
  `LoanReturnIn` body on `…/{rid}/return`. ⚠️ Not `ReturnIn`: that name is the
  stock-return body, and shadowing it 500'd `/entry/returns`. Overdue is
  computed on **local** naive time (`entry._local_now`, also in `main.py`'s
  work-queue badge). Before this it used UTC wall-clock and ran 3 h late.
  Frontend: `components/ScanBox.tsx` (autofocused, wedge-scanner field),
  `lib/scanFeedback.ts` (WebAudio tones + vibration, per-viewer mute),
  `api/returnablesHooks.ts`. That last module is kept out of `api/hooks.ts`
  because the login page imports `hooks.ts`. `AppLayout` now lazy-loads
  `QrScanner`, which took jsQR off the sign-in critical path (−131 KB raw,
  baseline re-recorded down).
- **Reorder signals (Track 4).** `services/smart_min.py` → `GET
  /stock/smart-min` (site-scoped like `/stock/by-site`; read-only). General
  items: consumption (max of 30-day and window-day averages) × cover days.
  Surface Shields: remaining SQM × `For_1_SQM` per (Material_Code, SAP_Code),
  plus Garnet via `services/prep`. A recipe line with no SAP is resolved
  through `inventory.Material_Code`. Packs via `services/units.factor`. The
  result is scaled by the approved-SQM pace (or `ss_planned_sqm_per_day`), and
  the suggested order is capped at the remaining plan. NEITHER SME engine is
  touched. Frontend: `components/ReorderSignals.tsx` (lazy from both
  StockPage and Dashboard, which keeps the entry preload map flat) and
  `api/smartMinHooks.ts`. Settings: `min_stock_cover_days`,
  `min_stock_window_days`, `ss_pace_window_days`, `ss_planned_sqm_per_day`.

## 2c. Phase 21 — lots, reorder, Drive (2026-10-06)

Rulings Q21-1..24 (`PROJECT_HANDOVER.md` → *Phase 21*). Migrations **`d1f7a3c9e2b4`**
(`consumption` / `returns` `.Source_Sheet`, `.Source_Row`) and **`e2a8c4f6b1d9`** (`ocr_aliases`).

| Module | What |
|---|---|
| `services/lots.plan_lot_problems` / `lot_problems` | One walk per lot in date order → `unknown_lot` / `lot_used_up` with the workbook sheet + Excel row and a hint. Dry run (from `bulk_import.plan_ledger`) and after the sync (Lots page). Warn, never block (Q21-18). |
| `bulk_import._sheet_rows_xl` | Rows numbered by **Excel row** (`iter_rows(min_row=1)`); header in the first five NON-EMPTY rows. `_write_positions` stamps sheet/row after the upsert — a moved row is not an update. |
| `dashboard.py` Top 5 Expiring | `SQL_LOT_BALANCE`, stock left only (Q21-19: never auto-closed). |
| `services/smart_min.sqm_pace` | Approved SQM/day = paper-form entries **+ committed jobs** (`sme_attribution_group`) — D0. No pace → shown, not ordered (D2), no days of cover (D1); exact pack rate (D3); `Trail` per Surface Shield row. |
| `practice_demo.py` · `frontend/src/demo/` · `tools/demo_voice.py` | 21f. The self-driving demo. The server half is mounted **only in the Practice process** (Live: 404). It has the catalogue, a 30-minute `scope=demo` ticket, the role switch (practice.* → practice.<role>, never admin, a real session, audited `PRACTICE_DEMO_SWITCH`), and the reset (`DEMO-` rows plus the `DEMO-` tank's jobs, with their credited m² taken back). The runner is a lazy, Practice-only chunk that drives the real DOM (`element.click()`, the native value setter + `input` event). Scripts are data, and every one is also a Practice-leg E2E. Voice: Mac `say` clips keyed by djb2 of the sentence, then on-device `speechSynthesis` (localService only), then silence. The assistant gives a `demo` SSE frame only in Practice (explicit "show me / for me" asks; an unsupported one goes to Feedback once a day). `AuthContext` adopts a switched session via the `gi-session-adopt` event (Practice only). **21g:**
- flows 3–6;
- `tours.ts`, one narrated read-only tour per sidebar page; `scripts/tour_check.mjs` (in `test:nav`) fails a sidebar page without one;
- `snapshot_demo_state` / `restore_demo_state` record and restore the settings demos change: the pace keys, the demo SAP's accepted minimum, the demo's learned OCR name. It is held in `app_settings.practice_demo_snapshot` until reset;
- reset also covers `lots`, `lot_units`, `qc_*` by lot, `returnable_items` by borrower/code, `pending_issues`/`consumption` by `Issued_To`, and the DEMO slips in `entry_attachments`. |
| `ai/consumption_match.py` · `ocr_names.py` · `tools/ocr_eval.py` | 21d (alembic `e2a8c4f6b1d9`, `ocr_aliases`). A written product name → exact / learned (green) · fuzzy (GOLD, never auto — Q21-5) · nothing (red); stock-aware; learned per site on Accept/pick, HOD deletes (Q21-3). `consumption_semantic.py` (nomic-embed-text) is MEASURED only (Q21-4) and was NOT wired into the app: on the 11 real pages it changed nothing (156/160 resolvable rows right with or without it). `ai/paper_fields.py` (21d follow-up): `check_paper_date` — a date outside [today − 14, tomorrow] is never taken; it offers up to three in-window dates scored per day/month/year part (confusable digits 9↔4, 1↔7 … cost ½), the store keeper confirms (`POST /ai/ocr/paper-check`); `norm_work_type` maps PV/RIL/BLL/Blaster onto the workbook's PU/R/L/B/L/Blast. The photo lane (`jobs._resolve`) and the paste lane (`Date:` line) now carry `date_text` to the page. The harness compares OCR with the workbook per DAY TOTAL (the Consumption Log aggregates per date, item, work type, tank; `PV` = `PU`); local vision only, photos git-ignored, no names printed. |
| `services/drive_sync.py` · `drive_admin.py` · `tools/gdrive_sync.py` | Read-only Drive (httpx; folder by ID) → the six sync workbooks; **structural** `.xlsm → .xlsx` verified cell by cell (an openpyxl re-save drops cached values); atomic swap + `.backups/workbooks/`; SME commit / ERP dry run, the admin commits; 07:30 behind the daily claim; Live only. Secrets in git-ignored `deploy/gdrive_*.json`. |

## 2d. Phase 22 — Drive as a service, OCR lines, polish (2026-10-07)

Rulings Q22-1..24 (`PROPOSED_PHASE22_PLAN.md` §8, approved 2026-10-07).

| Module | What |
|---|---|
| `services/drive_sync.py` (22a) | `DriveClient._call` retries every call 3× with backoff (a timeout on the first real crawl); `TokenError` for a refused refresh token (Testing apps expire after 7 days). `fetch()` checks Drive's md5, records per-sheet row counts and refuses a sheet that shrank > 2 % unless `accept_shrink`. `crawl()` reads the DN / MTC / Pending subfolders (Waste Disposal ignored); `cache_files()` keeps read-only copies by Drive id in `CACHE_DIR` (`.cache/drive/`, md5-checked, 10-minute budget per run). `additions_only()` reads `pg_excel_sync --report-json`. Schedule helpers: `clean_times`, `due_slots` (3 h catch-up), `next_slot`; `freshness_status` is the top-bar word. |
| `drive_admin.py` (22a) | `run_once` = fetch → SME commit → ERP dry run (with `--report-json`) → **ERP commit only if additions-only** (Q22-1) → crawl + cache → `drive_links.relink_all` → `drive_sync_runs` row + `drive_sync_last_ok`. `schedule_loop` ticks every minute; each `drive_sync@HH:MM` slot is claimed once via `daily_job_runs`. `/drive/freshness` (all roles), `/drive/pull` (admin, HOD), `/drive/files/{id}` (a cached file, path confined to the cache), `PUT /admin/drive-sync/schedule` (audited). The CLI calls the same `run_once`. |
| `services/lots.record_problem_snapshot` (22a) | After every committed ledger sync (`pg_excel_sync`): problem keys by WHAT they are (never the row), `fixed` for 24 h, the per-site count for the top bar. `GET /lot-register/problems.xlsx`. |
| `services/drive_links.py` | The name parsers (22b DN, 22c MTC, 22d requests) register in `LINKERS`; idempotent over `drive_files`. **22b** (alembic `a4c7e1d9b3f2`): `dn_key` / `dn_file_key` / `rdn_key` reduce a DN number and a file name to one key (`dn:13021`, `cp:8`, `dn:GIRLPSAR348`, `rdn:24`); the Receipt Log's `DN_Copy` path links its exact file; `assign_wd_numbers` gives each without-DN delivery (site · day · DN text · vehicle) `WD-<site>-NNNN` once, never renumbered (`receipt_wd`); `ledger_docs` → `GET /drive/ledger-docs` (the Records ledger readers); `dn_coverage` + `return_dn_check` (an RDN workbook's line count/total vs the Return Log) on the Drive card. `returns.DN_No` is mapped from the Return Log (not part of the row label); `tools/backfill_return_dn.py` fills existing rows by `Source_Row`. |
| `services/mtc_links.py` · `mtc_drive.py` (22c) | Drive certificates → lots (alembic `b5d8f2a6c3e7`). Candidates from the name (`(BNO-…)`, `Batch - …`, `CUMI FN n`, TIP TOP `wwyy-L#-3.1-product`) and the PDF text layer per page (CUMI `Product Name / Batch No / D.O.M / D.O.E`, TIP TOP `Artikel / Chargen Nr. / Herstellungsdatum`). A link needs `batch_matches` (exact, or a letter+digits supplier batch at the end of the workbook's long batch — never a bare digit tail) AND `family_ok` (product overlap ≥ 6 after aliases Härter→HARDNER, Ch.→CHEMOLINE; a thickness must be the item's own). Exact → `mtc_documents` (`drive_file_id`, `disk_path` = cache copy) — this clears `quality.assert_mtc_for_issue` like a site upload — and the D.O.E onto `lots.Expiry_Date` with `Expiry_Source='mtc'` (never over `'app'`; `lot_file` no longer overwrites `'mtc'`). Less than exact (no product, or a batch spanning two thicknesses) → `mtc_assignments` proposed; Admin/HOD/QC assign by hand (Q22-10), only QC/QC-HOD confirm. `PUT /lot-register/expiry` (QC, QC-HOD, HOD; audited) sets a retest date as `'app'` (Q22-11). |
| `services/requests_sync.py` · `material_requests.py` (22d) | The *Pending Material Follow-up* workbooks (alembic `c6e9a3b7d4f8`): `read_requests` finds the header row (SAP Code / Material Code / description / UOM / Qty / Received on … / Pending / PR# / Type / Date) — four layouts; a "Received and Pending" status table has no request date. `link_requests` (a Drive linker) re-writes a file's lines; Surface Shields skipped; the "Over all" roll-up stored `is_summary` (a CHECK, Q22-12). `overview` allocates receipts FIFO (oldest request first, never a receipt older than the request) → received / pending; `needs_sap`; `summary_check`. `pending_no_pr` → `smart_min.compute` `Requested_No_PR`, subtracted from Suggested_Order (Q22-13). `GET /material-requests` (SK, HOD, Logistics, Admin, Auditor) — distinct from `/requests` (inter-site transfers). Page `/requests-pending`. |
| `ai/paper_fields` · `ai/paper_compare` · `services/preparers` (22e) | alembic `d7fa4c8e2b19`: `Prepared_By` on `pending_issues` / `consumption` (sync maps *Prepared by* to it and `Issued_By`; `Issued_By` stays the submitter on app rows). `preparers`: per-site `{from, day, night}` history in `app_settings` (`consumption_preparers:<site>`), `shift_of` (no mark = Day, Q22-14), `pick` by the paper's date. `match_tank` (official `sme_equipment` tags + mapped `sme_tank_alias`; train rule `K-`/`J`, area rule `89D0-TNK-001`, confusable-stroke fuzzy → gold, bare `TNK-091` never picked), `tank_key`, `fill_dittos` (with `group`), `fill_work_types` (vision misreads; a lone `1` = ditto). `paper_compare.align`: one-to-one pairing in four passes (SAP+qty+tank+worker → … → SAP), `workbook_rows` by date + Prepared_By (Surface Shields out). Endpoints `/ai/ocr/paper-check` (shift, preparer, tanks), `/ai/ocr/tank-alias`, `/ai/ocr/compare`, `/ai/ocr/preparers` (GET SK/HOD/Admin, PUT HOD/Admin, audited). `tools/ocr_eval.py --lines` scores the same pairing. |
| 22f | Raw hex 0 (`design-baseline.json`); tokens `status.neutral`, `media.letterbox`. `tools/practice_overlay.py` v11: `trim_seed_duplicates` makes the queue seed idempotent; `seed_drive_examples` draws a DN (PIL), two certificates (fpdf2) and a request workbook into `CACHE_DIR/practice/`, indexes them in `drive_files` and runs `drive_links.relink_all` — `/drive/files/{id}` now serves Practice too (still confined to the cache). `tools/ocr_site_setup.py` loads approved OCR names and a site's preparers (audited). Header: below 1440 px `.gi-user-role` and `.gi-health-label` hide. |
| `components/DriveFreshness.tsx` | The lazy top-bar chip + Pull button (in the ☰ drawer on phones; critical path re-baselined +682 B raw for its lazy entry). ⚠️ Its buttons use `disabled`, not `loading`: an antd v6 Button whose `loading` flips back within a frame (a fast 4xx) kept ignoring clicks — found by the 22a E2E. |

## 3. Database facts that bite

- Mixed-case column names are real (`"SAP_Code"`, `"Site_ID"`) — always quote.
- ⚠️ **Ledger ROW identity (2026-09-16) is a provenance label, never the
  movement.** The Excel sync writes `Source_Ref = XLSX:<site>:<kind>:<day>:<sap>:
  <ref-hash>:<n>` and upserts on a PARTIAL unique index over that label only.
  (Date, SAP, Tank No.) is shared by 2,785 of 4,394 real consumption rows, so it
  can never be unique. `DO UPDATE` keeps the row id, which keeps Phase 13
  attributions linked; an edit that changes Date/SAP/Quantity/Tank sends the
  attribution back to the queue (`sme_link.SOURCE_FP_SQL`). App rows
  (`SME_EXEC:`, `SMR:`, adjustments) are never relabelled. See
  PROJECT_HANDOVER §3a.
- Ledger identity: **stock = Σreceipts − Σconsumption − Σreturns** per SAP/site
  (`v_live_stock`, `v_site_stock` views). Dates are ISO **text**.
- The 3 rowid-ledger tables keep `id := sqlite rowid` through migration so
  `posted_txn_ref` (`C:{rowid}`/`R:{rowid}`) stays valid.
- pending vs ledger naming traps: pending_returns `Return_Reason` → ledger
  returns `Reason`; pending `wbs` (lowercase) → ledger `WBS`; pending_returns
  has `override_required/override_reason/received_*` provenance columns.
- `entry_attachments` (BLOB-authoritative), `mtc_documents`, `wbs_master`,
  `form_drafts` were migrated from legacy; `generated_reports`,
  `phone_otp`, `auth_sessions`, `app_notifications`, `whatsapp_outbox`,
  `email_outbox`, `pending_summary_notifications` are new-stack-only.
- Locked rulings: **FEFO + over-issue/negative stock are allow-and-log, never
  hard-block** (2026-06-30); legacy hard-blocked — deliberate divergence.
- ⚠️ **`receipts."Date"` is the DELIVERY date typed off the vendor's paperwork,
  not when the row entered the ledger.** `posted_at` (2026-08-13,
  `c7a93e5d2b18`) is the latter, and the return form's 30-day window needs
  BOTH — it was measuring `Date` alone, so goods received this morning against
  a six-week-old document vanished from the source-receipt dropdown.
  `posted_at` is **NULL on every pre-migration row** (no backfill: a
  `DEFAULT CURRENT_TIMESTAMP` on `ADD COLUMN` would have declared all 632
  historical receipts posted on migration day), so readers must fall back to
  `Date` rather than assuming it is set.
- ⚠️ **A constraint that lives only in an Alembic revision does not exist on a
  database built by `metadata.create_all`** — which is how
  `tools/migration/cutover_migrate.py` builds production. Declare it in
  `models.py` too, in the same commit. `ux_asset_transfer_open` was missing
  from every created-from-models database until 2026-08-13.

## 4. Entry gates (parity sprint, 2026-07-13)

Master switch **`require_entry_documents`** (app_settings, **default ON** when
the row is absent; admin-editable):
- ON ⇒ Issue / Receipt / Return submission (single + bulk) requires ≥1
  uploaded supporting document (`attachment_ids`); returns additionally
  require **Return DN No.** + a **source receipt** (`GET /entry/return-sources`,
  30-day window; 365-day override needs a justification → `override_required=1`
  red-flagged in HOD approvals); qty capped to the source receipt.
- OFF ⇒ legacy-optional behaviour (tests run this way).
Independent of the switch: the **MTC gate for `Surface Shields`, which binds at
ISSUE and nowhere else** (see §4b), WBS requirement once a site has active WBS
rows, UoM conversion.

### 4b. The MTC rule, stated once and precisely

> **Material with no MTC CAN be received, and CAN be sent to site. It CANNOT be
> issued or consumed at the site.**

This line is the whole rule. It is repeated here because the sentence it
replaced — "MTC hard-block for `Surface Shields` receipts" — described the
pre-2026-08-12 behaviour and was left behind when the gate moved, so a reader
of this document would have concluded the opposite of what the code does.

| Step | Certificate required? | Enforced by |
|---|---|---|
| Warehouse goods-in (`warehouse.receive`) | **No** — recorded, and Logistics is notified to chase it | `quality.note_mtc` / `quality.warn_mtc_missing` |
| Site receipt staging (`POST /entry/receipts`, `/entry/bulk`) | **No** — same: recorded and chased | `quality.warn_mtc_missing` |
| Delivery Note creation and shipping (`warehouse.create_dn`, `ship_dn`) | **No** — travel is never gated | *(deliberately no call — suite BM asserts its absence)* |
| **Issue to a worker** (`ledger.stage_consumption`, `supervisor.approve_smr`) | **YES — hard block** | `quality.assert_mtc_for_issue` |
| **Consumption posted from an execution entry** (`execution.post_stock`) | **YES — hard block** | `quality.assert_mtc_for_issue` via the QSEP pre-check |

Refusing to RECORD something that has physically happened is the one thing an
inventory system must never do: the truck is in the yard, the certificate is in
somebody's inbox, and a receipt block makes real stock invisible to the shelf
report, to planning and to everyone. What the system controls is what happens
NEXT — and the moment before material reaches a worker is the moment the
certificate actually protects anyone.

The same reasoning is why the block is absent from DN creation specifically:
leaving it there would have reproduced the identical stall one hop later, with
the warehouse able to receive material and unable to send it anywhere.

Certificates are **inherited down the chain**, not re-uploaded — see
`quality.visible_mtc` for the precedence (PO line → DN → shipping warehouse →
site) and for why a certificate for site A does not clear site B.

### 4a. WBS + work types (Phase 9a, `services/wbs.py`)

Two CONDITIONAL gates — both no-ops until an HOD curates a list for the site,
which is what lets the feature ship without a flag day. Managed at `/hod/wbs`
(HOD/Admin); the endpoints predate the screen by a whole phase, which is exactly
why every one of the 1,674 live consumption rows carries a blank WBS.

- `assert_work_type` — the strict dropdown. Skipped entirely for the reserved
  markers `SUPERVISOR_REQUEST` / `STOCK_ADJUSTMENT` (written by
  `supervisor.approve_smr` and `ledger.stage_adjustment`; `rep_intent_vs_actual`
  joins on the first). A gate that refused those would break stock adjustments.
- `resolve_wbs` — precedence **explicit → work-type map → `sme_equipment.WBS_No`
  → none**, returning the `source` as well as the number. The explicit value is
  never overridden: the map is a default, not a correction.
- `assert_wbs` — moved here from `entry_docs` so the gate and the resolver read
  one list.

⚠️ **ORDER.** For an ISSUE the sequence is resolve → assert, inside
`stage_consumption` (the same place the QSEP gates sit, and for the same reason:
`supervisor.approve_smr` is the other mouth of the path). Asserting the form's
raw `wbs` first — which the router used to do — rejects every blank-box entry
for want of the number the map is about to supply, making the map unreachable.
RECEIPTS still assert in the router: they have no work type to resolve from.

`wbs_work_type_map` is keyed `(Site_ID, Work_Type_Norm)` where the norm is
lower + trim + collapse-whitespace. That merges the four case-collisions the
live ledger actually holds and nothing else; near-duplicates are the HOD's
judgement, made in the UI. Nothing is seeded by migration —
`/hod/site-config/work-types/suggestions` offers the ledger's own spellings
merged and counted instead.

### 4c. The printed consumption form (Phase 9c, `services/consumption_form.py`)

`GET /execution/forms/{code}[?esc=]` — SK, supervisor, HOD. Deliberately NOT
under `/mh` (exact-locked {hod, admin}; the supervisor is the person who carries
the paper).

- **Pre-printed names + a QR** is the whole design. The vision model in 9d never
  reads a material name (its weakest task) and never reads header context; it
  reads digits in boxes. QR payload is `GIF1|site|system|sub-activity|uuid` —
  five fields always, version-tagged so a future decoder can refuse rather than
  guess.
- ⚠️ **Every download REGISTERS a new `Form_UUID`** in `sme_consumption_form`.
  A GET that writes, because two prints are two sheets and 9d must tell a
  re-print from a re-photograph.
- ⚠️ **`Recipe_Fingerprint` pins ROW ORDER**, which is the mapping contract —
  9d matches handwriting positionally. Covers (index, esc, material, SAP, uom);
  EXCLUDES `For_1_SQM`, which cannot mis-map anything.
- ⚠️ **`_row_label` appends `Material_Description` only where a material code
  appears more than once in that form.** LSC8 prints one code four times
  (Comp-A/B/C/D); seven (system, material) pairs live are in this shape.
- **No write-in rows** (ruling Q9), **blank work date** (ruling Q6 — printed in
  batches, used same- or next-day; the generation date is in the footer).
- `_txt()` transliterates before `reports._latin`, which drops what latin-1
  cannot encode — an em-dash separator vanishing is how four rows become
  indistinguishable.

### 4b. The manpower shift model (Phase 9b, ruling Q10)

`services/planner.py` and `services/session_plan.py` share ONE split helper,
`planner.shift_split`. Two planners reading the same roster and disagreeing
about how it divides would be worse than either being wrong, because only one
of them would ever be checked.

- **Total headcount is invariant to the shift count**:
  `manhours / (target_days × 11)`. A person works one shift a day, so nobody
  works both — "two shifts, so half the people" under-hires by half.
- **What nights buy is calendar time.** `Days_Day_Shift_Only`,
  `Days_Both_Shifts`, `Days_Saved_By_Nights`, from
  `manhours / (crew × 11)` on the day-only and the whole in-scope roster.
- **The per-shift split comes from the ROSTER**, never `/ shifts_per_day`.
  This operator runs ~20 day against ~80 night; an even split understates the
  night crew fourfold. `Shift_Split_Basis` names the basis: `roster` · `site` ·
  `assumed_even` · `day_only`. ⚠️ A role with day workers and **no night
  workers** is NOT a valid basis — that says there is no night crew yet, and
  read as a proportion it puts 100% of a forced two-shift plan on days.
- **Capacity counts the roles the job needs**, matching `days_with_roster`,
  which always did. An idle blaster used to inflate `normal_capacity`,
  understating the overtime and the hire-to-clear advice. The payroll totals
  stay published beside them as `roster.Capacity_GI` / `Capacity_NON_GI`.

### 4d. The paper-first consumption workflow (Phase 9d)

    DRAFT_SUPERVISOR → PENDING_SK → PENDING_HOD → APPROVED
                                                └→ REJECTED (terminal, no bounce-back)

Labour-only activities skip PENDING_SK — a store keeper has nothing to verify.
`DRAFT_SK` / `PENDING_SUPERVISOR` are RETIRED, not deleted: the 9d migration
rejects any live row in them and `assert_transition` explains rather than
returning a lookup miss.

**Modules.** `ai/ocr_form.py` (QR decode → rectify → vision → parse) ·
`ai/form_jobs.py` (the async worker; the entry is created IN the worker, so the
model's reading never round-trips through a client and the `Form_UUID` is
consumed in the same transaction) · `services/form_intake.py` (the four
refusals: unknown sheet, wrong site, already filed, stale fingerprint) ·
`services/execution.py` (`sk_verify`, `qsep_status`, `post_stock`).

- ⚠️ **Four layers per material line**, each rendering only when it differs:
  `OCR_Qty` → `Supervisor_Qty` → `SK_Qty` → `Actual_Qty`.
- ⚠️ **`post_stock` is the ONLY writer for lining consumption** (Q1-b), calling
  `ledger.post_consumption` so FEFO, the over-issue warning and the audit line
  stay in one place. Idempotent on `Consumption_ID`; `Source_Ref` is
  `SME_EXEC:<entry id>:<line id>` — the **id**, because `next_entry_no` reuses
  a deleted entry's number.
- ⚠️ **QSEP blocks by default, HOD may override** (Q2-D) with a mandatory reason
  and a `qc_hod` dispatch. Checked AFTER the HOD's edits.
- **Rectification**: `PX_PER_MM = 8`, homography from four corner fiducials,
  falling back to the QR quad, then to "no crop" — `X-Crop` says which.
- **Cloud seam**: `client.vision_json()` + `GI_AI_VISION_PROVIDER`.

### 4e. Efficiency over time (Phase 9e, `services/mh_analytics.py`)

`GET /mh/analytics/daily` + `/scope` — HOD/Admin, inheriting the Man-Hours lock.
Reads `mh_timesheets` (hours, remarks) and `mh_production` (SQM). No migration:
both tables already carry `Work_Date`, `Equipment_Tag`, `System_Code`.

- ⚠️ **Two divisions by zero.** `sqm == 0` → `daily_mh_per_sqm = null`;
  `cum_sqm == 0` → `cum_mh_per_sqm = null` as well. A day can fail the first
  and pass the second.
- ⚠️ **`gap` vs `idle`.** Hours with no area is a gap (ruling Q12, carries a
  reason); neither hours nor area is idle. They look identical on a chart and
  mean opposite things.
- ⚠️ **The reason is `mh_timesheets.Remarks`, verbatim.** No taxonomy is
  inferred.
- Every calendar day between the first and last observation is emitted; the
  window is the OBSERVED range, not the requested one. `_date_span` caps at 730
  days so one typo'd year cannot ask a browser for 700,000 bars.
- Mixed lining systems WARN rather than refuse — the HOD may have wanted that
  view.

### 4f. Surface prep (Garnet) — Phase 15d, `services/prep.py`

ESC1/ESC2 are the **blasting** codes: the sub-activity codes of the blasting
man-hour norms. The planner calls a code "surface prep" when **no recipe line
names it**. The 2026-09-27 workbooks added Garnet recipe lines under exactly
those codes, which by that rule would have turned blasting into a lining system.

- **A code is surface prep when it has a row in `sme_prep_baseline`.** The four
  rows (ESC1/ESC2 × OLD/NEW) are seeded by the migration's `data_upgrade`.
  `prep_codes()` is subtracted from every lining list: `planner._lining_codes`,
  `/execution/activities` `manpower_only`, the norms grid, `/mh/meta`,
  `consumption_form.available_systems`. `/entry/lining-systems` keeps them but
  flags `prep: true`, because the Issue form needs them and the Smart Calculator
  hides them.
- **The benchmark is the baseline, not the recipe line.** `recipe_rate(…,
  surface_state)` returns `sme_prep_baseline.KG_Per_SQM` for (code, OLD|NEW). NEW
  falls back to the workbook's `For_1_SQM` until set (Q15-9); OLD has none until
  set. It is snapshotted on the row like every benchmark.
- **The pair is the substrate, not the equipment's code.** `pair_ok`: CV/CONCRETE
  → ESC1, ME/TANK/VESSEL/STEEL → ESC2 (Q15-5), because no equipment *carries* a
  prep code. `equipment_for_system` lists tags the same way.
- **No area is credited.** `execution.credit_done_sqm`, the one writer, returns
  early for a prep code. Blasted area is recorded by the blasting execution
  entries; a Garnet job's area only feeds its variance (Q15-6).
- **Garnet is its own card.** `sme_groups.queue` splits Garnet rows (recipe SAPs
  under prep codes, plus controlled-category descriptions containing GARNET)
  into `kind: "prep"` cards, with `prep_code` from the substrate and
  `surface_hint` from the tag's last answer (Q15-7). The two sides refuse each
  other's material (422).
- **Units:** a mass pack with Unit Size ≠ 1 has base **KG** (`units._measure_base`),
  so a TON of 1000 is 1000 KG. A prep row whose base is not KG is refused.
- **Estimator untouched:** it is driven by the codes the *equipment* carries, and
  none carries a prep code. Both engines are unchanged and the goldens identical
  (rule 1c).

### 4g. Lots & FEFO — Phase 16, `services/lots.py`, `services/lot_file.py`, `lot_register.py`

The workbooks' `Serial No.` holds a Surface Shield's **batch** and every other
item's **asset tag**. Before Phase 16 both landed in `Serial_No`, so Live had 0
lots and FEFO had nothing to choose from.

- **The item decides what `Serial No.` means.** `lots.tracking()` gives
  `{SAP: 'lot'|'roll'}`. A controlled-category item is lot-tracked unless
  `inventory.Lot_Tracked` says otherwise (the bricks are FALSE — data step
  d8a3f6c1b2e9), and UOM `ROL` makes it a roll item. `lots.interpret()` maps a
  cell to `(Serial_No, Lot_Number)`. `plan_ledger` uses it for all three logs; the
  Return Log's serial is read for the first time.
- **One lot spelling.** `norm_lot`: `3504.0` → `3504`, `A4525` → `A 4525`,
  `N/A` → none. `norm_roll`: `10…` → `1O…` (Q16-12b); a roll's batch is its
  first 10 characters unless the roll register says otherwise. A cell with
  several lots gets none and is reported (Q16-2).
- **The `XLSX:` label excludes Serial and Lot**, so filling a lot updates rows in
  place (`~`), never `+`. `apply_ledger` (the one ledger writer) then creates a
  `lots` row for every lot a receipt names (`sync_lots_from_ledger`, idempotent on
  the existing unique key).
- **Balance = received − consumed − returned ± transfers** (`stock.SQL_LOT_BALANCE`).
  For a roll batch, received is `GREATEST(receipts naming it, rolls in lot_units)`,
  because a "36 ROL" receipt line carries no roll numbers.
- **The Lot Register workbook only describes.** `lot_file.plan/apply` resolves
  each row to a SAP (the `SAP` column, else name + component + pack size,
  ambiguity refused) and matches it to a receipt on SAP + DN + date (rolls: SAP +
  date). It writes lot MFD / expiry / batch ref / DN, the roll register, a lot on
  an existing lot-less receipt, and `Shelf_Life_Months` where unset (learned from
  the file's MFD → expiry pairs). It never writes a quantity, and every
  disagreement is reported. An expiry with `Expiry_Source = 'app'` is never
  overwritten.
- **FEFO puts expired lots LAST.** `ledger._FEFO_PICK` and
  `lot_register.options` share one order: valid lots by expiry, then lots with no
  expiry, then expired lots. It stays allow-and-log: a non-FEFO pick asks for a
  reason, never blocks.
- **The expiry notice** runs inside the evening digest's one-worker claim: one
  notice per site to the store keeper and the HOD (`lots.expiry_notices`).
- **The estimator is untouched:** the SME engines never read lots (rule 1c).

### 4h. The Phase 14 ledger rules (2026-09-26)

- **Units convert at READ (14a).** The ledger, minimums and the workbook stay in
  packs; `services/units.py` multiplies by `inventory.Unit_Size` for display
  (`189 KG · 21 Can`). A consumption form ticked KG is converted to packs
  before it posts, and refused if the material has no Unit Size.
- **One quantity per bucket (14b).** When a QR form and the Excel workbook both
  record the same (site, day, tag, SAP), the ledger holds the larger of the two,
  never the sum. The HOD sees the comparison under SME → QR ⇄ Excel.
- **One job, one credit (14c).** A Surface Shield job is one tank on one day;
  its area is asked once and credited once, however many materials it drew.
  15e split that further by the store keeper's note (two notes = two jobs).
- **The Excel sync is an upsert on a per-row label** (`XLSX:<site>:<kind>:<day>
  :<sap>:<hash ref>:<n>`, migration f6b83d1a27c9). Values are COALESCEd, never
  erased; `--prune-vanished` removes rows the workbook dropped; rows entered in
  GI Hub are never touched. Serial and Lot are excluded from the label (16a).

## 5. Frontend map (`frontend/src/`)

React Router routes in `App.tsx`; **`config/nav.tsx` is the single
source of truth for nav + route guards** (exact-lock `anyRole` / `minLevel`;
duplicate menu keys across groups are forbidden — use route aliases like
`/logistics/lining-coverage`). API via axios `api` (`api/client.ts`):
**`API_BASE` = `VITE_API_URL` (native builds; injected by release-*.yml as
`https://gi.giinventory.com/api`) or relative `/api`** (web: Vite proxies →
`:8000`; `VITE_API_PROXY` overrides for E2E); axios runs `withCredentials`
(cross-origin refresh cookie for the native shells); token in localStorage
`gi_token`, silent refresh on 401 (the rotated refresh token returns as an
httpOnly Set-Cookie — no JS storage), `detectClientType()` sends
`client_type` web|native at login (drives the RTR TTL), **429 →
`gi-rate-limited` event → RateLimitToast deadline countdown**, backend
unreachable → throttled console hint + `gi-api-unreachable` toast
(RateLimitToast is mounted at the app ROOT so the login page shows it),
`logApiFailure()` prints message/code/status/headers on network-err/403/5xx
incl. a Cloudflare-Access-block note. TanStack Query hooks in `api/hooks.ts`.

**Phase 10 additions (§7c, §7d):**

| File | Owns |
|---|---|
| `pages/TrainingPage.tsx` | 🎓 Training — my modules (per-language player, 90% bar, acknowledge) and, for level ≥ 2, the **Team compliance** tab |
| `components/TrainingGate.tsx` | the SOFT gate. ⚠️ **It gates the ACTION, not the page** — a render prop supplying `guard(run)`. The first version rendered on mount and blocked the whole ExecutionPage including "Print a consumption form", so somebody wanting a BLANK sheet was stopped by a video about filling one in. Playwright caught it as a modal intercepting an unrelated click |
| `components/MandatoryEnrollPanel.tsx` | the 2FA enrolment shown INSTEAD of a session to a mandated, unenrolled account. ⚠️ It drives `/auth/2fa/*` with an `enroll`-scoped token that is deliberately never passed to `setAuthToken` — storing it as a session would make "you must set up 2FA" the way to skip it |
| `auth/AuthContext.tsx` | `LoginOutcome` now has **three** shapes, not two: straight through · `mfa` challenge · `enroll` required. Plus `mfaDue`, the grace-period deadline that drives the warning banner |

**PWA/offline:** vite-plugin-pwa autoUpdate SW (build-only; dev unaffected),
NetworkFirst cache for read APIs; **strict OTA** — `main.tsx` polls
`reg.update()` every 15 min AND on tab refocus, so deployments reach every
open client without a manual refresh. **Offline mutation queue**
(`offline/queue.ts`, IndexedDB `gi-offline`): only entry-form POSTs opt in via
`postWithOfflineFallback()` → `{queued:true}` + amber toast + header
`OfflineSyncBadge`; replay on reconnect with `X-Offline-Replay: 1`; rejected
rows are dropped+surfaced. **Send/Receive (Outlook-style):**
`SyncControls.tsx` header button = flushQueue + invalidate ALL queries;
gear popover sets the auto-sync cap (localStorage `gi_sync_interval_min`,
1–120 min, default 1; re-armed live via the `gi-sync-interval` event).
**Native shells:** Capacitor (`capacitor.config.ts`; `android/`/`ios/`
GITIGNORED — regenerated by `npx cap add`) + Tauri v2 (`src-tauri/`
COMMITTED; explicit CSP — update its `connect-src` if the API domain ever
changes). **QR scan-to-dashboard:** header 📷 → `QrScanner` →
`MaterialCardModal` (`GET /stock/material-card`: whitespace-normalized SAP,
site-scope-pinned, 30-day gap-free 2-series Recharts trend). **Entry documents:** `EntryDocsUpload` (file +
`capture="environment"` camera), `DocumentLibraryPage` (/hod/documents) with
inline image/PDF preview reused by the ApprovalsPage 📎 drawer. **Draft
recovery:** `lib/formDraft.ts` (localStorage, debounced) + DraftBanner on the
three entry forms. SME engine twin lives in `sme/engine.ts` (golden parity).

**2026-07-30 global table tools:** every grid renders through
**`lib/smartTable.tsx`** — a drop-in replacement for antd's `Table` (import
`Table` from there, not from `'antd'`; 99 instances across 45 files). It
derives a sorter for every `dataIndex`-backed column and a checkbox filter
for every categorical one, with no call-site change and no added chrome.
Rules: no `dataIndex` ⇒ no sorter (action columns); numeric and boolean
columns get a sorter but no filter; **server-paginated grids are left alone**
(auto-detected from controlled pagination — `total` AND `current` set — because
sorting one page of N silently lies). Filters cap at 30 distinct values,
search box above 8; explicit `sorter`/`filters` always win; `smart={bool}`
overrides. Known limit: filter labels come from the RAW field value, so a
column whose `render` maps codes to labels needs an explicit `filters` array
(as `UsersPage` does). Companion **`sme/materialCols.tsx`** renders material
components — variant SAP under the code, names WRAP rather than ellipse.

**2026-07-18 UI polish:** every antd Table carries `sticky={{ offsetHeader:
64 }}` (SmePage grids use the live-measured pinned-band offset instead);
**smart decimals** via `lib/format.ts` (`fmtQty`/`fmtCell` — `5.00`→`5`,
fractions keep ≤4 dp) wired into the generic `lib/columns.tsx` renderer (SME
coverage percentages keep their fixed `.1f` style; `sme/engine.ts` strings
are golden-parity-pinned). New pages/components: `BulkImportPage` (dry-run →
commit), `sme/SmartCalculator.tsx`, IssuePage Surface-Shields system-first
flow, OcrImportPage "Validate (handwritten spec)" + TSV export, Admin Console
feedback triage drawer + 📋 Prompt copy.

### 5a. Phases 14–16 on the client

- **The login (14e, 15c, 15e).** Tier 0 is CSS only. Tier 1 WebGL lives in two
  extra Vite entries — `src/three/boot.ts` (an async entry injected by the
  `gi-login-fx` plugin) and `src/three/loginScene.ts` (a module `<script>`) —
  and is never precached. ⚠️ **Never `import()` the scene:** Vite's shared
  preload helper re-splits the app chunks. The mark rises in once and then holds
  still with a slow light sweep, centred in geometry space (Box3 of the intro
  pose put it half a band too high); the canvas follows `.gi-login-crest`.
- **The critical-path check.** `npm run build` ends with
  `scripts/critical_path_check.mjs`: zero growth of raw JS against
  `perf/critical-path.json` (hashes normalised), the scene not precached.
  Re-baseline deliberately with `node scripts/critical_path_check.mjs --update`
  and say why in the commit (16c added +229 B for the lot picker; the lot hooks
  live in `api/lotHooks.ts` so they are not in the shared chunk).
- **Duplicate `@keyframes` fail the build** (15c — a `gi-rise` clash once broke
  the Live dashboard).
- **Practice theme (15c).** `practiceTheme` (violet) + `PracticeBadge` (amber
  pulse) when the API reports the training instance.
- **Site and WBS (15e).** `components/SiteField.tsx` (`lockedSite`,
  `SiteField`, `SiteFilter`) hides the site picker from a one-site user and
  shows their site instead. `components/WbsField.tsx` on Receive and Issue shows
  "None set up" when the site has no WBS numbers. `InventoryItemModal.tsx` is the
  shared item editor (admin + HOD; Lot tracking and Shelf life fields since 16c).
- **Lots (16c).** `api/lotHooks.ts`, `components/LotPicker.tsx` (FEFO order,
  expired last, reason asked for a non-FEFO pick, rolls for CHEMOLINE),
  `pages/LotRegisterPage.tsx` at `/lots` ("Lots & Expiry": SK, QC, HOD,
  `qc_hod`), the Receive MFD field.

## 6. Security & rate limiting

`ratelimit.py` (in-house, no slowapi — resolves client IP
**CF-Connecting-IP → X-Real-IP → peer**, per-process store):
- per-endpoint dependencies: login 10/60, register 30/60, OTP burst 5/60 …
- `check_bucket(key,…)` arbitrary-key windows: **OTP 3/hour per source IP AND
  3/hour per target phone** (checked before anything else; failed sends burn
  quota; 429 + Retry-After).
- `PenaltyBox`: **5 invalid webhook HMAC signatures / 10 min ⇒ 15-min IP ban**
  (refused pre-parse, even for later valid signatures).
- `strict_limits_enabled()`: strict rules ON in production, **relaxed when
  `GI_DOTENV=0`** (hermetic tests), force-enabled by `GI_FORCE_STRICT_LIMITS=1`
  (suite AF).
**Auth = RTR (Refresh Token Rotation, 2026-07-25, alembic `f1a7c9e83b52`):**
15-min access JWTs + a **signed refresh JWT** (scope `refresh`, jti/fam/
client claims) in the `gi_refresh` httpOnly cookie, tracked in
**`refresh_sessions`** (UUID id, users.id FK, family_id, unique jti,
client_type, is_revoked + forensic columns). One login = one **family**;
`client_type` from the login body sets the TTL — **web 7 days, native
(Tauri/Capacitor) 90 days** (on MFA logins it rides inside the signed
mfa_token). Every `/auth/refresh` rotates in-family (old row → revoked/
'rotated'/replaced_by). **Replaying a revoked token = breach: the WHOLE
family is revoked (successor included) + SESSION_REUSE audit + 401 — the
user's OTHER families/devices survive.** Logout and the admin console's
single-session revoke are family-wide; `revoke_all_sessions()` (admin
reset / user delete / WhatsApp RESET PASSWORD) spans all families + the
legacy `auth_sessions` table (revoke-only now). Production sets the cookie
`SameSite=None; Secure` so cross-origin native refresh works; CORS defaults
include the fixed shell origins (`tauri://localhost`,
`capacitor://localhost`, …). ⚠️ `refresh_sessions` must STAY in the
`gi_ai_ro` REVOKE set (`create_ai_readonly_role.sql`) AND
`ai/safety.py` FORBIDDEN_TABLES. Secrets live ONLY in gitignored `deploy/.env`
(`config.py` dotenv-loads it on bare metal unless `GI_DOTENV=0` — that pin in
service_tests must NEVER be removed). Secret-scan every push range for the Meta token prefix (`EAA…`) and the
WhatsApp phone-number ID before pushing (the exact grep lives in the project
memory — deliberately not reproduced here).

## 6a. Practice (Live | Practice) — a second process, not a second session (rule 17)

*Shipped 2026-09-24 (branch `feat/practice-sandbox`). Design record:
`PROPOSED_SANDBOX_PLAN.md`. Locked as rule 17 in `PROJECT_HANDOVER.md`.*

```
                    ┌──────────── nginx / Vite proxy ────────────┐
 browser / native → │  /                → SPA (ONE build)        │
                    │  /api/            → api            (Live)  │──► gihub                (superuser locally / app role)
                    │  /training-api/   → api-training (Practice)│──► gihub_training       (role gi_training)
                    └────────────────────────────────────────────┘          ▲  CREATE DATABASE … TEMPLATE
                                                                   gihub_seed_training
```

**The one decision.** Nothing in the API selects a database per request. A
process learns what it is from `GI_INSTANCE` (`config.instance()`), holds
exactly one `DATABASE_URL`, and `config.assert_instance_safe()` — called by
`db.py` **before** the engine is built — refuses to boot when the two disagree
(Practice must open a `*_training` database, Live must not open one, and
Practice may hold no WhatsApp/SMTP/cloud-vision credential). That is rule 15's
own mechanism applied to a long-running process. A per-request switch was
rejected because **30 `SessionLocal()` sites in 12 files** (AI jobs, trace
drain, answer cache, Bloom filters, limiter, digest, weekly report, scheduler,
briefing) never see a request, and in-process caches (Bloom snapshots, the
login throttle) cannot serve two registers.

**The walls, outermost first:**

| Vector | Wall | Where |
|---|---|---|
| V1 wrong database | `gi_training` has **no CONNECT** on `gihub` (Postgres privilege; holds under trust auth) | `tools/practice_db.py wall` / `verify` |
| V2 misconfigured process | boot refusal, both directions | `config.assert_instance_safe` ← `db.py` |
| V3 token replay | different `JWT_SECRET` **and** an `env` claim `_decode` checks (claim-less = Live, so no mass logout) | `auth._make_token` / `token_env_ok` |
| V4 offline replay | one IndexedDB per environment, entries stamped, stamp sent as `X-GI-Instance`; server 409s a mismatch before routing | `frontend/src/offline/queue.ts`, `backend/api/instance.py` |
| V5 PWA cache | distinct URL prefix; the `gi-api-read` regex cannot match `/training-api/` | `vite.config.ts` |
| V6 cookie clobber | `gi_refresh` (Live, unchanged) vs `gi_refresh_training` | `config.refresh_cookie_name` |
| V7 outbound | boot refusal + `whatsapp/emailer.enabled()` honour `outbound_enabled()`; overlay phones are non-routable | `config.py`, `services/*` |
| V8 backup confusion | dump names carry the database; Practice has no backup volume | `console.backup_filename` |
| V10 wrong belief | banner/tag/title/print watermark from `GET /instance` (server), never the toggle; red mismatch bar | `components/PracticeBanner.tsx` |
| V11 reset | router mounted only in Practice (404 on Live); target from config, must end `_training`; role owns only the Practice DBs | `backend/api/practice.py` |

**Deliberate differences in Practice** (operator rulings): OCR refused at every
vision entry point plus `ai/client.vision_json` as the last wall (Q5);
`training_compliance` never written, acknowledge refused (Q2); 2FA neither
mandated nor enrollable (Q4); outbound impossible.

**Data.** `tools/practice_db.py build` = `make_tutorial_db.py --today <today>`
(untouched, so every tutorial's `DATASET_VERSION` stays valid — P12-5) →
`cutover_migrate.py` (P12-4, the same loader as tutorials/E2E; builds at head)
→ AI read-only grants → `tools/practice_overlay.py` (one `practice.<role>`
account per `ROLE_META` role, tutorial logins removed, real queues staged
through the real app, MFA mandate off) → reset. ~3 s. Reset = `DROP DATABASE …
WITH (FORCE)` + `CREATE DATABASE … TEMPLATE gihub_seed_training`, ~0.6 s; the
Practice API runs **one worker** so the FORCE can terminate every session it
holds (both engines are disposed first).

**Client.** `frontend/src/api/environment.ts`: `CURRENT_ENV` is fixed for the
page's life; `switchEnvironment()` logs out of the current API, persists, and
reloads — no cached query/stream/queue handle survives. Live keeps `gi_token`;
Practice uses `gi_token@training`. The Practice base is the Live base's
`/training-api` twin, so native builds get the toggle unchanged.

## 7. AI routing layers — and the cross-cutting patterns filed beside them

> §7a–§7d grew into this section rather than being designed into it, and
> two of them (§7b Bloom filters, §7c's rate limiting) are not AI at all.
> They are kept here because every reference in `PROJECT_HANDOVER.md` names
> them by these numbers; renumbering would silently break those pointers,
> which is a worse cost than a slightly wide section title.

1. **Hub Assistant** (`/ai/assistant`, SSE) + insights/EOD — same-box Ollama,
   one warm model. Retrieval is `ai/manual_index.py` (BM25 over
   `USER_MANUAL.md`, no vector store, no embeddings) + `ai/manual_qa.py`
   (role gating, prompt). **Measured 2026-08-24 on the live 229 KB manual:
   2 ms to chunk, 15 ms to build the BM25 tables, 0.3 ms per search.** The
   index is warmed in the FastAPI lifespan (`manual_qa.warm()`), which is
   hygiene rather than a speed fix — perceived latency is Ollama token
   generation, not this. Four properties worth knowing:
   * **the role filter runs BEFORE scoring** (`Index.search(allowed=…)`), so a
     role's prompt cannot physically contain a chapter it may not see. That is
     the security boundary, not the prompt;
   * **an alias map expands both documents and queries** (`_ALIASES`,
     `expand_aliases`) — the corpus writes "purchase requisition" and users
     type "PR". It expands, never substitutes, and cannot widen what a role
     reaches (the chapter filter is upstream of it);
   * **a table adheres to the paragraph above it** when a long sub-section is
     wrapped, so a caption and its table are never retrieved separately;
   * **the fallback path** (used only when nothing scores) keeps whole `##`
     sub-sections up to `_PER_SECTION_CHAR_CAP = 3000` and never truncates §2
     at all — at the old 800-character head-truncation the access matrix,
     which starts ~1,900 characters in, was in NO non-admin prompt.

   ⚠️ **`USER_MANUAL.md` at the REPO ROOT is the only manual.** It is the AI
   corpus, the source of the in-app PDF and the ops PDF (both built by
   `tools/export_docs_pdf.py`, one command, two destinations). A second
   role-based `docs/USER_MANUAL.md` existed from 2026-07-26 to 2026-08-24 and
   fell four phases behind; suite CJ now fails if it comes back, and it
   compares §19's tab count against `ManHoursPage.tsx` rather than a number
   typed into the test.
2. **`POST /ai/query` (chat-with-your-data, level ≥2)** — two lanes:
   **template lane** = deterministic intent router (`ai/query_router.py`:
   returns/receipts/issues/stock/low-stock/expiring/top-suppliers/PRs/POs +
   time windows + site mention **+ 2026-07-18 deep filters: category
   aliases → bound `ILIKE :cat`, and material-family keywords ("furan",
   "remafix"…) → ILIKE over description/material-code PLUS the SME tables
   via the `sme_recipe.SAP_Code` join**), fully bound-param SQL, **scoped
   users' site enforced from the JWT** (safe for HODs, works with Ollama
   down; count questions return a `metric`); **NL lane** = unmatched
   questions from UNSCOPED roles only → `/ai/nl-search` machinery (Ollama
   coder → SCHEMA_HINT incl. SME schema + deep-ILIKE rule →
   `is_safe_select` gate → `gi_ai_ro` read-only login). The AI-5 ruling
   stands: generated SQL never runs for a scoped user.
3. Doc-intel: PR/PO PDF extract (preview-only), vision-OCR job queue, badge
   verify. LocateAnything is RETIRED.
4. **Handwritten consumption forms** (spec: `docs/features/handwritten-ocr`,
   v1.0 — vendored; "preserve exactly" list inside): the vision model
   (`ocr.CONSUMPTION_PROMPT`) TRANSCRIBES faithfully (ditto glyphs verbatim,
   struck-through flagged, raw `qty_text`, top-right `date_text`); every
   rule is deterministic in `ai/handwritten.py` — 3-format date parsing w/
   digit fixes, the 18-entry corrections list, ditto resolution, qty rules
   (additive `2+3` sums, blank→1, zero rejects), the spec fuzzy scorer
   (auto-accept conf ≥40 + lead ≥8, top-5 candidates), the CLOSED 8-rule
   substitution table (R1–R4), whole-batch stock simulation
   ((date,form,row) order, low-stock 5, negative → blocked), the
   [?]/⚠️/🚨 flag taxonomy, and the **17-column legacy TSV export**
   (blocked rows never exported). Endpoint: SK-locked
   `POST /ai/ocr/handwritten-process` — READ-ONLY (posting stays in the
   Issue flow). Changing a preserved rule: edit the owning spec file first,
   then the module, then the suite-AM pins.

### 7e. Request tracing — the scores that were computed and thrown away

*Phase 11 slice 11c (alembic `f8a3c05d1b27`), `ai/trace.py` + `ai_traces`.*

⚠️ **`Index.score()` produced a BM25 score for every candidate chunk on every
question, `search()` sorted by it, and all of them were discarded.** So a good
answer and a bad answer left identical evidence — none — and "did retrieval
fetch the wrong passage, or did the model ignore the right one?" had no answer.
Those have completely different fixes. The 800-character head-truncation that
kept §2's access matrix out of every non-admin prompt (§7, bullet 1) was a
RETRIEVAL failure that presented as a model failure and lived a whole phase.

Six spans, one row each, grouped by `trace_id`:

| Span | Carries |
|---|---|
| `ai.request` | lane, role, the QUESTION, queue-wait, total ms, outcome |
| `ai.guard.input` | pattern hits, score, decision *(slice 11d)* |
| `ai.retrieve` | **allowed chapter set, candidate count, per-chunk `{chapter, heading, score, rank, chars, used}`, whether the fallback fired** |
| `ai.cache` | key hash, hit/miss, similarity *(slice 11e)* |
| `ai.generate` | model, num_ctx/num_predict, ms, **answer LENGTH** |
| `ai.guard.output` | redactions, canary hits, defusals *(slice 11d)* |

**Three rulings inside it:**

* ⚠️ **Postgres, not a hosted tracer (P11-1).** A hosted span carries the
  PROMPT, and ours contain manual chapters, the results of `/ai/insights`'
  five live SQL probes, generated SQL, and OCR'd delivery notes with employee
  names. Same argument as P10-1 chose Postgres over Redis.
* ⚠️ **The question is stored; the answer and the chunk TEXT are not.** The
  question is retained by operator ruling Q11 (what people actually ask is the
  best eval material in the system and nothing was keeping it). Storing
  passages would copy the manual into a table with a laxer read path than the
  manual's own — undoing rule 9 by accident. Chapter numbers, headings and
  scores answer every diagnostic question the text would.
* ⚠️ **It must never cost the thing it measures.** Nothing in `trace.py` raises
  and nothing blocks: spans go on a bounded per-worker queue drained in
  batches, and a full queue DROPS and counts rather than waiting. A dropped
  span is a missing diagnostic; a blocked request is a missing answer.
  `stats().dropped` surfaces it, and the console shows it — the same reasoning
  that stopped a skipped check counting as a pass.

`search()` delegates to `search_scored()` so there is exactly one ranking
implementation; two would let the telemetry and the prompt drift apart, and a
trace that reports a ranking the prompt did not use is worse than no trace.
Retention is a 30-day DELETE carried by the orphan-sweep loop **under the
`daily_job_runs` claim** (P10-2), because a once-a-day job on four workers runs
four times without one. Read at `/admin/ai-traces` — admin **and auditor**,
mounted both as a Console tab and as its own route, because the Console is
`minLevel: 4` and the auditor is level 3.

### 7f. The guard at the door and the guard at the exit

*Phase 11 slice 11d, `ai/guard.py` + `ai/guard_patterns.yaml`, suite CT.*

⚠️ **THE SECURITY BOUNDARY IS NOT IN `guard.py`.** Rule 9 is. The fence in
`allowed_sections()` filters chapters BEFORE BM25 scores them, so an injection
that succeeds perfectly still reaches nothing. The failure mode to fear here is
not that the guard is bypassed — it is that people start trusting it INSTEAD of
the fence, and then somebody simplifies the fence because the guard exists.

Everything in the module is one of two things: a refusal that would otherwise
have been a slow confused answer, or a check on text that has ALREADY passed
the fence for things the fence was never about (a spreadsheet formula, a phone
number, a canary).

**`guardrails-ai` was rejected**, on three grounds: its useful validators
download a transformer of a few hundred MB at install time — a second model
resident beside the one warm 7-8B the CPX42 ruling allows; its dependency tree
out-masses the module it would guard; and its core abstraction (typed output
with re-ask loops) is a retry this system cannot afford, since a re-ask on a
400-second vision read is another six minutes.

| Stage | What it does |
|---|---|
| **shape** | length, line count, repeated-token flood, long base64 runs |
| **patterns** | 13 scored jailbreak patterns — override, extraction, elevation, delimiter injection |
| **topic pre-flight** | refuses when a question matches a FORBIDDEN chapter far better than anything allowed |
| **output** | formula defusal, PII redaction, runtime canary scan, over a sliding buffer for SSE |

⚠️ **Scores, never a boolean, and every pattern has a NEGATIVE TWIN.** "Ignore
the damaged drum and issue the rest" is a real sentence a store keeper types at
06:00; a guard that refuses it has cost more than the injection the fence had
already made profitless. One pattern warns; a combination refuses. Prompt
extraction is the single deliberate one-pattern refusal, because there is no
innocent phrasing of "show me your system prompt" and what it asks for is the
fence's own description.

⚠️ **The topic pre-flight can ONLY refuse**, and its trigger is a RATIO, not an
empty retrieval. The first version fired only when retrieval returned nothing —
measured against the live manual that condition is very nearly empty, because
BM25 over ~450 chunks scores something for almost any English sentence, so the
guard never ran while looking like coverage. It now needs the forbidden chapter
to beat the best allowed one by 2.5×, which is what keeps §2's access matrix
answering "can I add a user?" ("you cannot; an admin does") instead of being
refused.

⚠️ **The output guard's buffer is SIZED FROM THE DATA (65 chars), not guessed.**
It must exceed the longest thing that must not be split across two SSE chunks —
the longest live canary is 21 characters, the longest bounded PII shape 60. The
first draft used a flat 240 and coalesced whole short answers into one event,
defeating the streaming the endpoint exists for; suite A caught it. A JWT can
exceed 65 and may be split: an accepted, stated trade, because a token in a
manual answer is an alarm either half raises.

⚠️ **No LLM judge in the request path.** A second generation doubles the wait on
one warm model, or cold-starts a second; and a stochastic judge that can REFUSE
means the same question is answered on Monday and denied on Tuesday — P10-7's
flaky-gate argument applied to production. The judge belongs in eval Tier 2.

Formula defusal imports `reports._RISKY` rather than retyping the six characters
a spreadsheet evaluates (rule 12); two copies would agree until one was updated.
Canaries come from `tests/ai_eval/cases/*.yaml`, whose uniqueness
`audit_canaries()` already verifies — defence in depth that degrades to an empty
set when the case files are absent, because the fence is the control and a smoke
alarm should not take the building down when its battery is flat.

### 7g. The gateway, and a cache key that is a security control

*Phase 11 slice 11e, `ai/route.py` + `ai/answer_cache.py` (alembic
`a1c9e64b3d70`), suite CV.*

**`route.POLICIES` is one table, keyed by LANE**, holding model, `num_predict`,
timeout, retry budget, cloud permission and cacheability. `jobs.NUM_PREDICT` is
now DERIVED from it — two literal copies of "3072" is how a budget and the lane
it belongs to drift apart, which is the bug §7a already describes.

⚠️ **`num_ctx` is deliberately NOT in the policy table.** It is not a policy, it
is a computation over the image *this* request carries, and it stays in
`client.vision_num_ctx()` beside the measurements that justify it. Getting it
wrong does not truncate — it aborts the runner and takes every queued job with
it. This is also **the reason LiteLLM was rejected**: its Ollama adapter passes
its own options dict, which would have handed that decision to a library default
and re-opened `ggml_abort` silently. Portkey was rejected earlier and harder — a
Node daemon *in the request path* is P10-1's objection to Redis, enlarged.

⚠️ **Error CLASS drives behaviour, and the timeout case is the whole argument
for hand-writing this.** `route.classify()` separates `RETRYABLE` (connect, 429,
5xx — retry with **full-jitter** backoff, because four workers whose Ollama
restarts together would otherwise herd) from `TIMEOUT` (the model was healthy
and still generating) from `UNAVAILABLE` (the engine is gone). A timeout is
**never retried and never falls back**: retrying starts a second multi-minute
generation on a one-model box, and falling back would ship a page off the
network because our own stopwatch expired.

⚠️ **Slice 11e found that 11b's cloud fallback covered ONE of five vision
lanes.** `ocr_consumption`, `ocr_delivery_note`, `ocr_purchase_doc` and
`tool_identify` called `aic.generate(images=…)` directly and so never reached
`client.vision_json`, where the cloud seam lives — only the Phase 9d execution
form had ever used it. All five now route through `route.call_vision`.

#### ⚠️ The answer cache key is a security control, not a performance detail

`ai_answer_cache.key_for()` hashes **normalised question · role · manual content
hash · prompt template hash**, and every factor is load-bearing:

* **role** — rule 9's guarantee is that a role's CONTEXT differs. Two people can
  type a byte-identical question and be entitled to different answers. A cache
  keyed on the question alone serves an Admin's answer to a Store Keeper and
  undoes the retrieval fence *from the side*, invisibly, because the wrong
  answer is fluent and cites chapters that reader has never been shown;
* **manual hash** — the manual gains a chapter almost every phase (§24, §25). An
  entry cached against the previous edition describes a screen that has changed,
  with no sign of being stale. Hashing the corpus is the only invalidation rule
  nobody has to remember;
* **prompt hash** — change the instructions and old answers stop matching rather
  than lingering under new rules.

**Only `assistant` is cacheable.** `/ai/query`, `/ai/nl-search`, `/ai/insights`
and `/ai/eod-summary` answer from LIVE STOCK; a cached "you have 40 drums" is a
wrong number wearing a timestamp. The lookup runs **after both guards**, so the
cache can never be a way past a refusal, and **what is cached is the GUARDED
text** — caching raw model output would replay a redaction-worthy answer
unredacted one turn later.

**Exact match, not semantic, and that order is deliberate** (rule 11: benchmark
before you add). `nomic-embed-text` is already pulled, so stage 2 needs no new
service — what is missing is evidence it earns its correctness risk. "What can a
supervisor approve?" and "what can a supervisor NOT approve?" sit ~0.95 apart in
embedding space and have opposite answers: a similarity threshold is a
correctness knob dressed as a performance one. `answer_cache.stats()` publishes
the hit rate, which is the number that decides whether to build it.

### 7h. The eval gates — split by determinism, not by name

*Phase 11 slice 11f, `tests/ai_eval/` + `tools/gen_eval_grid.py` +
`bin/ai_eval_tier2.sh`, suite CW. Full detail: `tests/ai_eval/README.md`.*

The brief asked for a CI gate failing below **0.85**; ruling **P10-7** says a
Tier 2 eval never gates a merge. Both cannot hold — today's Tier 2 security
score is 64%, so an 0.85 gate on it fails every run until somebody disables it,
which is P10-7's own prediction. **The split is by DETERMINISM:**

* **Gates** — Tier 1 (what the model was shown), the policy pin, canary
  integrity, and **Contextual Recall / Precision ≥ 0.85**. All are pure
  functions of BM25 over a fixed corpus: no model, no temperature, identical
  every run.
* **Scored, never gated** — Faithfulness, Answer Relevance, answer-level
  Safety. They need a judge, so they are trended against a recorded baseline and
  a >10-point drop **opens a bug row** rather than failing a build.

⚠️ **This gates the failure the system is actually prone to.** The
800-character truncation that kept §2's access matrix out of every non-admin
prompt was a retrieval regression that lived a whole phase because nothing
measured retrieval. At these thresholds it fails the commit that caused it.

**Dataset: 147 cases** — 72 grid (chapter × role, GENERATED and verified), 24
fence probes with live canaries, 15 jailbreak cases (7 of them negative twins),
12 near-miss pairs, plus the original rbac/exfiltration/groundedness sets.

⚠️ **The grid is generated, never hand-edited** (`tools/gen_eval_grid.py`, with
`--check` in CI). A case is kept only if the chapter it claims is retrieved AND
ranks first. Hand-written expectations drift into fiction: slice 11d asserted §2
would answer "how do I add a user" because its title sounds like it would — §2 is
about page ACCESS and contains none of that vocabulary. And BM25's `idf` is
corpus-wide, so adding a chapter perturbs every role's ranking (~0.3%, measured
in 11c) — a hand-maintained grid would rot one manual edit at a time.

⚠️ **The live scores are ~1.0 by construction, which is why suite CW exists.**
The grid starts perfect because it was built that way; the metric's job is to
catch a regression away from it. A metric scored on data built to satisfy it is
theatre until somebody proves it can fail, so CW feeds it synthetic telemetry
for a broken retrieval and asserts the score falls under the floor. That control
found a latent crash in the runner on its first run.

**RAGAS and DeepEval were rejected as dependencies, their definitions vendored.**
Both compute these metrics with an LLM judge defaulting to OpenAI, which cannot
see proprietary data and would have to be replaced anyway — leaving a prompt
template and a scoring convention. A metric whose definition changes under a
`pip upgrade` invalidates every historical score it produced.

Tier 2 runs from `bin/ai_eval_tier2.sh` on the operator's box or the Hetzner
host — never on GitHub's runners, which have no GPU and no Ollama.

### 7i. ⚠️ KNOWN MODEL CONSTRAINT — the Tier 2 ceiling is `llama3.1:8b`

*Measured 2026-09-03/05. Not a defect, not a backlog item: a hardware/model
limit that no amount of prompt engineering will move.*

**Tier 2 security sits at 64% against a 95% target, and the remaining gap is
the model, not the pipeline.** Three numbers make that falsifiable rather than
an opinion:

* **Tier 1 is 147/147 with ZERO leaks**, so nothing forbidden ever reached the
  model. The fence is intact.
* **Contextual recall 1.000, precision 0.994.** The right passage is being
  retrieved and ranked first.
* **False refusal is 0%.** The assistant is not buying compliance by refusing
  everything.

So the failures are neither retrieval nor policy. They are the model
**confabulating a UI that no chapter describes** — asked "what is on the Service
Health card?", an 8B model describes one, because that phrase exists only in
chapter 7 and Tier 1 proves it was never in the prompt. It echoes the question's
own wording and invents the rest.

⚠️ **Prompt engineering has already been spent here, and the ledger is
public.** Slice 10b added an explicit anti-confabulation rule to
`_SYSTEM_PROMPT_TMPL` ("if the CONTEXT does not name the thing being asked
about, you do not know about it"). That bought **+21 points** — 43% → 64% — with
false refusal staying at 0%. The next increment of prompt text buys
progressively less and risks the other direction, and false refusal at 0% is
the thing most worth protecting: a store keeper refused at 06:00 costs more than
an imperfect refusal rate.

**The lever is a larger chat model, and that is a SERVER decision.** The
standing ruling is one warm 7-8B model on the box (2026-07-06); a 30B-class
model needs GPU memory the CPX42 does not have. So this is deferred to a future
phase that upgrades the host, and until then:

* the threshold stays at **95%, deliberately unmet**. A threshold tuned down to
  whatever today's model scores measures nothing — it is left where it is so it
  reads as "here is the gap";
* **Tier 2 never gates a build** (P10-7). It is trended against a recorded
  baseline and opens a bug row on a >10-point drop;
* **the deterministic gates carry CI instead** (§7h). They cover the half of the
  problem that is ours to control, and they are at 1.000 / 0.994.

⚠️ **Do not "fix" this by lowering the target, adding more prompt text, or
wiring Tier 2 into CI.** All three have been considered; the first hides the
gap, the second is past its point of diminishing return, and the third produces
exactly the flaky gate P10-7 exists to prevent.

### 7a. ⚠️ The vision envelope — three numbers that are ONE decision

Fixed 2026-09-01 after two production reports ("the Consumption Log fails
silently", "the new PDF hangs with a ReadTimeout"). Both were diagnosed as the
model being bad at tables. It is not. Reproduced on the operator's own files,
the model read both correctly and was cut off by the limits around it.

| Knob | Where | Value | Why that number |
|---|---|---|---|
| output budget | `ai/jobs.py: NUM_PREDICT`, `ai/ocr_form.py: FORM_NUM_PREDICT` | per lane (3072 / 1536 / 2560 / 384 / 2600) | a DN is 4 items × 3 fields (~350 tok); a consumption log is 30 rows × 9 fields (~2,400 tok). One number for both clipped the sheet at row 14 and never clipped the note |
| context window | `ai/client.py: vision_num_ctx()` | computed per call; floor 8192, ceiling 16384 | **Ollama runs this model at `n_ctx=4096` whatever the 128k model card says.** An 1800 px page is 3,120 prompt tokens of that. Since 2026-09-02 the window is sized from `ocr.estimate_image_tokens()` — calibrated to err HIGH, because under-counting aborts the runner while over-counting only costs KV cache |
| HTTP timeout | `ai/client.py: VISION_TIMEOUT_S` | 900 s | measured: the form lane takes 269–444 s and the consumption lane 361 s. The old shared 240 s ceiling could not physically be met |

⚠️ **Raising the output budget without raising `num_ctx` is WORSE than leaving
it alone.** Asking for 4,096 predicted tokens over a 1,400-token image aborted
the Ollama runner outright (`ggml_abort`, SIGABRT, "llama runner terminated"),
taking every other queued job with it and answering with an empty body and no
error field. Suite CP-05 pins the relationship.

Behind the budget sits a second guard: `ocr.salvage_truncated_json` rebuilds the
complete prefix of a reply that simply stops, **cutting only at a closing
bracket** and dropping the unfinished element. A clipped reply used to be
discarded whole — thirteen correctly-read rows thrown away to punish one clipped
row, reported to the store keeper as "unparseable, try the Paste tab".

Three more rules the lanes now share:
- **nothing read is a FAILURE, not a result.** An empty row list raises instead
  of finishing at `status='done'` with a blank grid, which was indistinguishable
  from a photo of a blank form.
- **an unreadable quantity stays `null`.** The prompt has always said "never
  invent 0 or 1"; `clean_consumption_row` invented it anyway, because
  `_to_float(None)` is `0.0`. The paste lane keeps its `0.0` default — a cell
  typed empty by a human is a different fact from a box a camera could not read.
- **images are capped twice**: `MAX_VISION_BYTES` for what reaches the model,
  `SOURCE_MAX_DIM`/`SOURCE_MAX_BYTES` for the copy kept for the QR decode and the
  row-crop rectifier. `/execution/ocr/upload` normalises at the door, so a 20 MB
  HEIC no longer arrives base64'd at ~27 MB in `ai_jobs.payload_json` — and the
  workers now NULL that column on both terminal transitions.

### 7a-ii. ⚠️ The orphan sweep, and why it is heartbeat-based

Fixed 2026-09-02 (alembic `c4a7e2b81f36`). `ai/jobs.py:fail_orphans()` used to
run at startup and execute, with no filter for who owned the row:

```sql
UPDATE ai_jobs SET status='error' WHERE status IN ('queued','running')
```

Correct for one process — the worker is an in-process `asyncio.create_task`, so
its jobs die with it. **Wrong for `uvicorn --workers 4`, which is what
`deploy/Dockerfile.api` runs.** When one worker crashed and uvicorn respawned
it, the new process's lifespan failed the in-flight OCR jobs of the three
workers still running them. Invisible at deploy (all four boot before any job
exists); it only bit on a respawn.

`sweep_orphans()` now reaps on **liveness, not existence**:

| Column | Meaning |
|---|---|
| `ai_jobs.worker_id` | which process claimed the row (`pid-uuid8`) — answers "which worker was this on" after the fact |
| `ai_jobs.heartbeat_at` | touched every 30 s (`GI_AI_HEARTBEAT_S`) for as long as the owner is working |

The predicate is `COALESCE(heartbeat_at, started_at, created_at) < now −
ORPHAN_STALE_SECONDS` (180 s, five missed beats), covering all three ages a row
can have: beating, claimed-but-not-yet-beaten, and never-claimed (a `queued`
row whose process died between the commit and `spawn`).

Three rules a later edit must not break:

- **The beat starts BEFORE `GEN_SEMAPHORE`.** A third concurrent job waits
  minutes on the 2-permit semaphore with status already `running`; a beat
  started after the wait would let the sweep reap a job that is patiently queued.
- **The stale window is NOT the job timeout.** A vision read legitimately runs
  up to `VISION_TIMEOUT_S` (900 s). Sizing the sweep off job *duration* would
  mean waiting 15 minutes to reap a corpse; sizing it off the *beat* is
  independent of how long the job takes.
- **The sweep also runs on a timer** (`orphan_sweep_loop`, 300 s). A worker that
  dies while its siblings stay up leaves an orphan no startup ever sees —
  uvicorn respawns it in seconds, long before the row goes stale.

### 7b. Uniqueness Bloom filters (`services/bloom.py`)

Three sets — `usernames` (both registries), `sap_codes`, `asset_serials` —
built at boot and refreshed every 300 s. ~2.4 KB and ~5 µs per lookup each.

⚠️ **The filter is an accelerator; the database is the authority.** A Bloom
filter answers "definitely NOT present" exactly and "maybe" probabilistically,
so it can retire the round trip for a FREE name and never for a taken one. The
rule that makes this safe under multiple uvicorn workers: **a "definitely not
present" may skip a READ and may never authorise a WRITE** — this process holds
its own copy of the bits, so a username another worker registered is absent from
it until the next refresh. Every `UNIQUE` index and `IntegrityError` handler is
untouched. Suite CP-20..CP-29.

`GET /auth/username-available` is the live case: a free name is answered from
memory (`checked: "bloom"`), a name that looks taken is CONFIRMED against both
`users` and `pending_users` before the endpoint will say so (`checked: "db"`).

### 7c. Phase 10 slice 10a — mandatory 2FA, shared limiters, AI guardrail audit

### The 2FA mandate (`auth.mfa_gate`)

⚠️ **The capability was already shipped; slice 10a added the ENFORCEMENT.**
`pyotp` enrol/verify/disable, the login challenge, the step-up password check
(audit A03-F8) and `SecurityPage.tsx` have existed since Phase 2, and
`users.totp_secret` / `totp_enabled` were already columns. What did not exist
was any reason for a privileged account to turn it on.

`POST /auth/login` now has a **third** outcome:

| password | role mandated | enrolled | deadline | outcome |
|---|---|---|---|---|
| ok | – | yes | – | `mfa_required` + `mfa_token` (unchanged) |
| ok | no | no | – | `access_token` (unchanged) |
| ok | yes | no | future | `access_token` + `mfa_enrollment_due` (banner) |
| ok | yes | no | **passed** | **`enrollment_required` + `enroll_token`** |

⚠️ **The enrolment token is scope-limited, and that is the whole control.**
`_decode` matches `scope` exactly and `get_current_user` asks for `"access"`, so
an `enroll` token opens only the three `/auth/2fa/*` routes that use
`enroll_or_current_user` — **not** `/2fa/disable`, which keeps the ordinary
dependency. Minting a normal access token there (the obvious shortcut) would
turn "you must set up 2FA" into the way to skip 2FA, and the account would be
both exempt and believed protected. Suite CR-05 is that assertion.

Both knobs are `app_settings` rows, matching `mtc_required_category`:
`mfa_required_roles` (default `admin,logistics,hod,qc_hod,auditor`) and
`mfa_enforced_from` (ISO date). ⚠️ **Every uncertain branch resolves towards
ACCESS** — no row, an unparseable date, an empty role list all mean warn-only.
A bug in the rollout of a control must not lock a company out of its own
inventory system.

### `rate_buckets` — the cross-worker half of four limiters

`deploy/Dockerfile.api` runs `uvicorn --workers 4`, and four limiters kept state
in process memory, so each enforced **4× its configured limit**:

| limiter | documented | actual on 4 workers |
|---|---|---|
| `rate_limit(n, w)` per-IP dependency | n | 4n |
| `check_bucket` OTP budgets (per IP, per phone) | 3/hr | 12/hr |
| `PenaltyBox` webhook HMAC bans | banned | banned on 1 worker of 4 |
| `_totp_failures` second-factor attempts | 5 | **20** |

The last is the worst: it is the ceiling on brute-forcing the second factor
against a `_verify_totp` that accepts three codes at any instant
(`valid_window=1`).

**Postgres, not Redis** — operator ruling re-confirmed 2026-09-02, generalising
the `login_attempts` mechanism rather than introducing a second idea of a shared
counter. Two layers, not a replacement: the in-memory check runs first and costs
nothing; the row makes the ceiling true across workers. Gated by
`strict_limits_enabled()` so hermetic runs write no rows.

⚠️ **It fails OPEN** (ruling Q1.2) — a throttle that takes sign-in down when its
own storage hiccups is worse than the attack it prevents. Deliberately the
opposite of the access matrix, which fails **closed**.

⚠️ **`read_bucket_shared` vs `check_bucket_shared` is not a style choice.** The
counting function INCREMENTS, so using it to ask "is this IP banned?" creates
the ban it was asking about — the first invalid webhook signature answered 429
instead of 403 until suite `limits` caught it. A test and a tally are different
verbs.

### `tests/ai_eval/` — the adversarial RAG audit

Two tiers; **only Tier 1 gates a merge** (suite CQ). Tier 1 audits the finished
*system prompt* — deterministic, no model — and therefore covers the retrieval
path AND the fallback path at once. Tier 2 audits the *answer*, needs a live
model, and is stochastic; it is a scored artefact on a schedule, because a
flaky gate is one people re-run rather than read.

Three ways it fails: a **leak** (no threshold — one is too many), a **broken
canary** (a canary that drifted into an allowed chapter would pass forever), and
a **policy change**.

⚠️ **The policy pin closes a blind spot the structural check cannot see.** Tier 1
compares the prompt's chapters against `allowed_sections(role)` — the same
allowlist that built it — so a policy *widening* is self-consistent and
invisible. Proved by negative control: granting a Store Keeper chapters 7 and 17
failed **zero** structural checks. `cases/policy.yaml` pins the allowlists as
data; superset test, so gaining a chapter fails and losing one does not.

### 7d. Phase 10 slice 10b — daily claim, valuation, training gate

### ⚠️ The daily loops were firing four times a day

`report_center.scheduler_loop` claims its work with an atomic `last_run`
UPDATE. The three DAILY loops did **not**: the 07:00 morning briefing, the
16:00 evening digest and the Friday 17:00 executive report each slept until
their hour and dispatched, **in every worker**. `deploy/Dockerfile.api` runs
`uvicorn --workers 4`, so every one of those messages reached every recipient
four times. Invisible in development (one worker) and invisible in the tests
(the loops are off under `GI_SCHEDULER=0`), which is how it survived three
phases.

`services/dailyjob.py` is the claim they were missing — one row per job key,
one atomic `INSERT … ON CONFLICT DO UPDATE … WHERE last_run < :due RETURNING`.

- **It must stay ONE statement.** Select-then-compare-then-update is a
  check-then-write, and four workers waking on the same clock tick is exactly
  the case that window is open for.
- **`due` is the SCHEDULED time, not `now`** — comparing against `now` lets a
  worker that started a second later re-claim the same run.
- ⚠️ **It fails CLOSED**, the opposite of the rate limiter. A missed briefing is
  silence somebody notices; a double-claimed one is four messages everybody
  learns to ignore.

### Track 2 — the day-shift chase

`health_monitor.probe_day_shift_mtc` asks a sharper question than
`probe_missing_mtc`: not "is there uncertified stock?" (a standing condition,
true for weeks) but "is uncertified material staged for the crew starting in an
hour?" — which has a deadline, and the deadline is today.

`sme_execution_entry.Shift` (`'Day'`/`'Night'`/NULL) makes that answerable.
⚠️ **NULL is skipped, not assumed.** Every pre-slice-10b entry has one and there
is no honest backfill; reading NULL as `'Day'` would make the probe scream about
a year of history on its first morning.

Channels are chosen by **who** is being asked, not by urgency:

| Recipient | Channel | Why |
|---|---|---|
| Logistics, site SK/HOD/QC | in-app + WhatsApp | colleagues; numbers are in `users` |
| Logistics **only** | **email** (`emailer.py`) | the role that can obtain the document, and they live in a mailbox. Emailing everybody makes three copies of one message |
| anybody outside the company | **WhatsApp DRAFT** | `whatsapp.draft_text` writes `status='draft'` and never posts. Released by a human via `POST /admin/whatsapp/{id}/approve` |

⚠️ A chase that leaves the company is read as the company's position; an
automated one naming the wrong purchase order is a commercial mistake nobody
reviewed. `_po_for` returns `None` rather than a plausible wrong PO, and the
message then says "PO unknown".

The chase rides the **same 07:00 claim** rather than a second 07:30 timer — two
alerts half an hour apart trains people to ignore both.

### Track 3 — valuation & 30-day burn (`services/valuation.py`)

⚠️ **Un-costed lines are counted, never summed as zero.** `inventory.Unit_Cost`
defaults to 0; on the live database **every** line is un-costed, so a naive
valuation reports `SAR 0.00` for a site holding 731 units — arithmetically
correct and a lie a board would act on. They are reported as
**"Not Valued (N items)"** with a footnote saying the value is a **floor**, and
a coverage percentage sits beside the total.

⚠️ **ERP and SME numbers are printed side by side and never added** (rule 1a).
`inventory`/`consumption` are the live ledger; `sme_inventory_seed` is a frozen
project-wide estimate. Summing them double-counts every material in both.

⚠️ **`months_cover` is `None` when the burn is zero**, and the burn rate divides
by the **full** window rather than by the days that had activity — dividing by
active days flatters a site that worked eight days in thirty, and a board reads
it as a run rate.

`fpdf2` only (`_ValuationPDF` subclasses `_ExecPDF`, so there is one branding
implementation). Reachable by admin, HOD and auditor via `_EXEC_READERS`.

### Track 5 — the training hub and the SOFT gate

⚠️ **The gate never refuses**, and that is operator ruling Q5.1. Phase 9 made
photographing a form the primary way consumption is filed; a hard gate means a
supervisor holding a filled sheet at 06:00 cannot file it because of a video.
FEFO (allow-and-log) and the MTC gate (moved out of receipt) were ruled the same
way. `GET /training/gate/{feature}` returns `allowed: true` unconditionally;
only `show_interstitial` varies. **If a future slice makes it hard, the refusal
must move server-side into `POST /execution/ocr/upload` — a UI-only gate is not
a control.**

"Watch Later" POSTs to `/training/defer`, which increments a counter the HOD
dashboard shows with a name against it. The control is visibility.

⚠️ **`module_version` is in the compliance unique key.** Bumping
`training_modules.version` invalidates every prior acknowledgement by
construction — old rows are kept (auditable) but stop matching. Keyed on
`(user, module)` alone, re-recording a tutorial would leave everybody certified
against a video they have never seen: worse than no record, because it would be
produced as evidence.

⚠️ **Videos are URIs, not blobs** (Q5.3). A 200–600 MB tutorial set in a
`LargeBinary` lands in every nightly `pg_dump`, cannot serve HTTP Range requests
(so the viewer cannot seek), and competes for OLTP buffers. Languages: `en`,
`ta`, `ta-Latn` (Tanglish), `ar`.

⚠️ **The HOD dashboard is driven from `users`, not from `training_compliance`.**
Listing the compliance table shows only people who have engaged — so somebody
who has never opened the module, the one most worth knowing about, would be
invisible. The absence is the finding.

### 7j. Phase 12 — the tutorial pipeline (two passes, then one filtergraph)

A role tutorial is a **screencast of the real UI** with a **narrating avatar**
composited over it. Nothing here runs in the request path; it is an offline
producer whose output lands in the training hub slice 10b already built
(§7d). Owned by `tools/generate_tutorial.py` + `tests/video_gen/`.

```
tools/tutorials/<id>.yaml          ← the ONLY thing that may leave the machine
   │
   ├─▶ [egress guard]  assert_text_only()      P12-1 provenance whitelist
   │        └─▶ HeyGen payload ─▶ avatar audio ─┐
   │                        (durations MEASURED) │      ◀── PASS A
   ▼                                             ▼
 shot list + per-beat HOLDS ─▶ Playwright ─▶ screencast.webm + beats.json
        (tests/video_gen/tutorial.spec.ts, gihub_tutorial_pw :8011/:5184)
                                                 │      ◀── PASS B
                                                 ▼
                       ffmpeg: freeze-pad → scale → avatar → captions
                                                 │
                                                 ▼
                    <id>.mp4 + <id>.vtt + <id>.manifest.json
```

⚠️ **PASS A COMES FIRST, AND THAT ORDERING IS THE DESIGN.** A Playwright-driven
UI is far faster than a person explaining it. Built the other way round — record
first, narrate second — the first run overran **all six** of its beats: 40.2 s
of speech over a 19.6 s recording, the worst by 5.15 s. So the audio is rendered
and measured first and each UI step is held for the length of its own line.
With a real HeyGen key the ordering is unchanged: the avatar is requested
**before the browser opens**, because its timing is what the screencast is cut
to. This is the single thing most likely to be undone by someone "simplifying"
the script into a linear flow.

**Pass B** records against the SYNTHETIC dataset (P12-0), built by
`tools/make_tutorial_db.py` — the real classification structure with every
name, description, SAP code, quantity and date invented, on a pinned `ANCHOR`
so the numbers on screen cannot drift between renders (P12-5). It reuses the
E2E suite's own lifecycle on its own database and ports (P12-4), and
`stack.ts` **refuses to build on an occupied port** — a stale uvicorn answers a
health poll instantly, and three tutorials were once recorded through servers
nobody knew were running.

**The composite** is one filtergraph: `split → trim → tpad=stop_mode=clone →
concat` (the freeze-padding), then a lanczos scale to 1920×1080, the avatar
through `alphamerge`, the watermark, and one caption overlay per beat gated on
`enable=between(t,…)`.

⚠️ **Freeze-padding is what makes one screencast serve four languages.** Every
beat already ends on a static hold, so extending it duplicates identical frames
— invisible. `beats.json` supplies the segment boundaries, so a language cut is
an ffmpeg pass rather than a second recording: the browser runs **once per
tutorial, not once per language**. Measured: 8.86 s padded into one beat,
47.72 s → 56.58 s, mean per-channel frame difference **0.12/255** inside the pad
against **1.5/255** across the boundary.

⚠️ **Three measured facts about this machine that shaped the code**, two of
which fail silently:

* Homebrew's ffmpeg 8.1.2 has **no `drawtext`** (built without libfreetype), so
  every caption is a Pillow RGBA PNG composited with `overlay`.
* It **silently discards video alpha** — `-pix_fmt yuva420p` with `libvpx-vp9`
  exits 0, warns about nothing and writes `yuv420p`. Rule 16 in a new domain:
  an exit code is not a result, so the `pix_fmt` is read back and an
  `alphamerge` mask path takes over (which is also the branch a green-screen
  delivery would use).
* Playwright's `recordVideo.size` **does not scale the page** — a 1536×864
  viewport in a 1920×1080 canvas is drawn 1:1 at the top-left, leaving black
  over exactly 20% of each axis. The two are pinned equal; the upscale happens
  once, in ffmpeg.

**The scripts are data, and they are linted before a browser starts:** the
rule-14 route lint (every role in `audience:` must be able to open every
declared route, checked against a model of `canAccessPath` **and** against the
paths the browser actually landed on), the rule-9 manual fence (`manual_sections:`
against `manual_qa.allowed_sections()` itself), and the redaction lint (a
replacement that contains the thing it replaces cannot terminate — `hod` →
`demo.hod` produced `demo.demo.hod` until the renderer stalled).

### 7k. Phase 12f — the assistant answers, and points at the frame

`backend/api/ai/tutorials.py`. When a Hub Assistant answer matches a recorded
step, the SSE stream emits one extra frame after the tokens:

```json
{"tutorial": {"module_key": "sk_stage_return_v1", "language": "en",
              "beat": "source", "t": 61.2,
              "url": "/training?module=sk_stage_return_v1&lang=en&t=61.2"}}
```

The UI renders a **Watch it** button; `/training` reads `?module&lang&t` and
seeks that card's player once, clamped a second inside the duration (seeking to
the end fires `ended` on some browsers, which would beacon a completion the
viewer never earned into a compliance record).

⚠️ **P12-6 — the fence runs before the score, and it is THE SAME FENCE.** The
matcher reuses `manual_index.Index.search(allowed=…)` — rule 9's machinery —
with one `Chunk` per BEAT (`chapter` = the beat's index, `heading` = its note,
`text` = its narration line) and `allowed` built from each script's declared
`audience`. A tutorial a role may not watch is never a candidate.

**Two floors, and the second one is the real guard.** BM25 always ranks
something first, and "the closest of four videos" is not "a video about this".
A candidate must clear `MIN_SCORE = 4.0` **and** share at least
`MIN_TOKEN_OVERLAP = 2` distinct tokens with the beat it won on.

> ⚠️ The overlap rule exists because a score cannot tell a coincidence from a
> weak match. `manual_index._tokens` expands SYNONYMS — "valuation" becomes
> "stock value board brief not valued" — so a Store Keeper asking about the
> executive summary's valuation floor shared the single accidental token
> "stock" with *"This is Return Stock"*, and the 2.4× heading boost scored it
> 4.95 over a floor of 4.0. Raising the floor to 6.0 killed the coincidence AND
> a real hit (an HOD asking *"what does not valued mean"* scores 4.59 against
> the beat that exists to answer it). One word in common is a coincidence; two
> is a topic. Suite CX-04 is the trap, CX-15 the control.

⚠️ **What counts as a shared token (Phase 17d, measured).** The overlap rule
first counted every token `manual_index._tokens` emits — including JOINED
BIGRAMS of adjacent raw words. Phase 17's tutorial-retrieval eval
(`tests/ai_eval/router/tutorial_retrieval.yaml`) found HOD questions getting a
confident wrong link on `{many, howmany}` and `{into, intothe}`: one function
word, counted twice through its bigram. `tutorials._evidence()` now counts only
content words (not `_STOP`, not `_FUNCTION_WORDS`) and bigrams with at least
one content word — for the overlap rule only, so BM25 scoring, the manual's
retrieval and the generated grid do not move. CX-15's `{valued, notvalued}`
still counts (a stopword joined to a real term), which is the control. One
miss remains on purpose — "how do I book man-hours" → the KPI beat that reads
"man-hours booked", one real term in common, CX-15's own shape — held by the
eval's ratchet (≤ 1) rather than hidden by a narrower rule. Suite CX-16.

**It is allowed to be absent.** Until the renders reach object storage, a
production box has no manifests: the matcher returns `None`, raises nothing, and
`/ai/health` reports `tutorials: {present, tutorials, beats, indexed}` so an
empty index is a visible state rather than a mystery. Deterministic throughout —
no model, no clock — so the same question returns the same second every time.

### 7l. Phase 17 — System One: a fast lane decision before the slow one

*`backend/api/ai/system_one.py` + `system_one_prompt.md`, wired in
`ai/router.py` `/ai/assistant`; guard patterns v2; suites 17A–17C, 17E;
`tests/ai_eval/router_eval.py`; CI job `ai-router-eval`.*

```
/ai/assistant → flags · greeting
   └─ System One (span ai.route) — BEFORE the semaphore, never behind the 8B
        stage 0  guard.scan_input (refusal → today's path refuses it)
                 "open the lots page" → UI_COMMAND, resolved against the menu
                 labels in nav_access.json for routes THIS role may open
                 video vocabulary     → TUTORIAL_SEARCH
        stage 1  qwen2.5:1.5b via route.call_structured("router"): JSON Schema
                 `format`, temperature 0 / seed 0 / top_k 1, keep_alive -1,
                 3 s, no retry, no cloud, NOT behind GEN_SEMAPHORE
        guard.with_router_signal — is_safe:false refuses with a guard pattern
                 or on the SQL lane; otherwise traced (`flagged`) and answered
   └─ lane_for(role) — an intent NEVER widens a lane
        BLOCKED          refusal sentence, no generation
        UI_COMMAND       {"navigate": {path, label}}, no generation
        TUTORIAL_SEARCH  tutorials.match(video_topic(q)) → {"tutorial"}; miss → manual
        SQL_QUERY        level ≥ 2, not oversight: the /ai/query TEMPLATE lane →
                         {"table"} (≤ 50 rows), audited `lane=assistant/template`;
                         no template / other roles → manual
        MANUAL_QA        today's path, byte for byte (fence, guard, cache)
```

⚠️ **It narrows; it never widens (P11-4).** Rule 9's fence, `is_safe_select` and
`gi_ai_ro` are untouched, and suite 17c-03 fails if `system_one.py` ever imports
`manual_qa`, `safety`, `analytics` or `db`. Every failure — switched off
(`ai_router_enabled`), Ollama down, timeout, malformed reply, no template, no
tutorial — falls through to the unchanged manual path.

⚠️ **The model speaks words, the contract speaks codes.** It answers `{"intent":
"data"|"video"|"open_page"|"question", "is_safe": bool}`; `WIRE_TO_INTENT` maps
those onto `SQL_QUERY · TUTORIAL_SEARCH · UI_COMMAND · MANUAL_QA`. Measured: the
codes scored 0.915 routing / 38 % detection, the words 0.95 / 49 %
(`PROPOSED_PHASE17_PLAN.md` §9).

⚠️ **The router is LOADED off the request path (`system_one.warm`).** A cold load
(~3 s here, longer on CPU) exceeds the 3 s request budget, and Ollama CANCELS a
load whose caller gave up (HTTP 499), so without this the router never warms
after an Ollama restart and every question silently falls back — 133 of 176
eval calls did exactly that. It warms at startup (lifespan; skipped when the
router is off or `GI_SCHEDULER=0`) and in the background on any timeout/
unavailable fallback (suite 17b-10).

⚠️ **Video requests are matched on their topic.** `video_topic()` strips the
media words before `tutorials.match()` — in the TUTORIAL lane AND the Phase 12f
post-answer link. Sent whole, "video"/"tutorial"/"watch" matched the OCR
tutorial's beat ABOUT the tutorial gate for 8 of 12 requests.

**Rulings** (`PROPOSED_PHASE17_PLAN.md`, LOCKED 2026-10-03): Q17-1 one warm
generation model + one pinned router ≤ 1.5 GB (raised from 1 GB after the spike
measured 1.35 GB — deviation D1, ruled 2026-10-03) · Q17-2 a signal, a veto only on SQL · Q17-3 P10-7
amended: the router eval gates after a 10-run probation · Q17-5 `MANUAL_QA` ·
Q17-7 navigation only · Q17-8 tables in the chat.

**Measured** (qwen2.5:1.5b, Metal): routing 0.95, 0 flips, twins 0 refused,
dev block 0.667, holdout block 0.316, detection 41 %, p50/p95 360/443 ms, 1.35 GB
resident beside `llama3.1:8b` (5.46 GB). The 0.95 block target is NOT met
(deviation D3): with the model a signal, blocking rests on guard v2, which sees
every dev attack but generalises to under a third of the holdout. It is printed
as the gap on every run, not tuned away.

## 8. Testing — the gates

> 🔄 **2026-08-13 — the service tests run against their OWN database.**
> `backend/api/testdb.py` provisions `gihub_svctest` from `gi_database.db`
> (via the production cutover script) and rewrites `DATABASE_URL` **before
> `backend.api.db` is imported** — the engine is built at import time, so that
> ordering is the entire mechanism. `DATABASE_URL` below now names only the
> CLUSTER; its database is never opened, and provisioning exits non-zero if
> the two resolve to the same name. Suites B…BX commit through the real ASGI
> app and cannot be rolled back, which is why isolation and not cleanup is the
> answer. See `PROJECT_HANDOVER.md` rule 15.

```bash
# 0. harness hygiene — ~1 s, no services, RUNS FIRST IN CI (rule 16).
#    Audits the TEST SUITES, not the app: process-wide stdlib monkeypatches,
#    one-exception dependency guards, silent no-ops, unrestored sender mocks.
bash bin/ci_preflight.sh
```
```bash
# 1. service tests (2,762 checks on 2026-10-01; 2,328 at suite CX) — CI mirror, own throwaway DB
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
JWT_SECRET=ci-only-service-test-secret-key-32bytes-min \
.venv/bin/python -u -m backend.api.service_tests

# 2. SQLite↔PG parity oracle (5 aggregates) — same env vars (Phase B: tools/)
#    ⚠️ meaningful ONLY on CI or a freshly-cutover DB (PG is permanently ahead).
#    `sme_materials` is asserted as a CONSERVATION invariant since 2026-07-30:
#    the PG port is per-COMPONENT while the frozen SQLite view pools by
#    Material_Code, so both sides are rolled up and every quantity must match.
.venv/bin/python tools/parity_check.py

# 3. frontend
npm run build --prefix frontend && cd frontend && npx tsc --noEmit

# 4. headless E2E (Playwright — builds/destroys its own gihub_e2e_pw stack)
cd tests/e2e && npm test        # 128 tests, ~55 s

# 5. AI eval — the DETERMINISTIC half gates: Tier 1 (147 cases, what the model
#    was SHOWN) plus contextual RECALL and PRECISION at >= 0.85. No model
#    needed; identical every run. Also runs inside suite CQ.
#    ⚠️ The grid is GENERATED from the manual — --check fails if it has moved.
.venv/bin/python tools/gen_eval_grid.py --check
.venv/bin/python -m tests.ai_eval.runner
#    ⚠️ Tier 2 (what the model SAID) is STOCHASTIC and NEVER gates (P10-7). It
#    is trended against a recorded baseline and opens a BUG ROW on a >10-point
#    drop. Needs Ollama, so it never runs on GitHub's runners.
bash bin/ai_eval_tier2.sh          # score + ratchet + bug row
bash bin/ai_eval_tier2.sh --record # move the baseline, deliberately

# 6. nav manifest — every routable page must declare who may open it (51 routes)
npm run test:nav --prefix frontend

# 7. 🎬 tutorial scripts — NOT A GATE (Phase 12). `--dry-run` lints every
#    script without opening a browser: the rule-14 route lint (every role in
#    `audience:` may open every declared route), the rule-9 manual fence
#    (`manual_sections:` vs manual_qa.allowed_sections()) and the redaction
#    lint. A tutorial that renders badly is one to re-render, not a red build.
.venv/bin/python tools/generate_tutorial.py --all --dry-run

# 8. alembic single head
.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config('backend/alembic.ini'); c.set_main_option('script_location','backend/alembic'); print(ScriptDirectory.from_config(c).get_heads())"
```
Test-compat switches: service_tests sets `require_entry_documents='0'` first
(suite AH tests it ON); Playwright global-setup does the same in its clone —
the `gated` project (entry-docs.spec) runs AFTER the parallel pack because it
flips the global setting. Legacy `legacy/bug_check.py` (599, self-rooted —
run `.venv/bin/python legacy/bug_check.py`) guards the frozen Streamlit app;
its models-parity check carries an allowlist for new-stack-only columns
(SME `SAP_Code`s, bug_reports triage fields). New suites 2026-07-18:
**AJ** bulk import · **AK** OCR doc assist + prompt pins · **AL** QR/
returnables · **AM** handwritten-OCR stages + ask-data filters · **AN**
Surface-Shields workflow + Smart Calculator + report scoping · **AO** Bug
Tracking Engine. 2026-07-24…26: **AP** material-card scan dashboard
(site-scope matrix) · **AQ** RTR (per-client TTLs, in-family rotation,
replay → family revocation w/ other-family isolation, logout, audit).
2026-07-27…30: **AW** pg_excel_sync (PG-only guards, atomicity, idempotency,
COALESCE preservation) · **AX** session-report aggregation · **AY** two-tier
allocation + reverse SQM · **AZ** component identity (pools, dirty-SAP
normalization, naming, per-component shortfall, the bottleneck as a specific
drum, 4 `az-revert` checks against the old grain, 3 `az-sql` checks on the
seed-driven availability SQL, 5 `az-cutover` convergence checks, 2
`az-header` workbook-column checks).
2026-08-02: **BA** SME strict decoupling — 11 checks that post real ERP
receipts/consumption/returns against an SME material's own SAP and require
every SME read back byte-identical, plus a word-boundary source guard that
neither SME quantity query names an ERP table.
2026-08-05: **BC** SME subset rule — 16 checks that tier 2 is the PENDING
delivery and the ceiling is `max(available, ordered)`, including the live
GI-8005763 shape (143,000 of 143,000 → empty pipeline, 9,685 to buy) and two
checks that READ the workbook to confirm `available <= ordered` still holds.
2026-08-03: **BB** SME tier segregation — 18 checks on the PHENACIN ACP POWDER
shape (0 physical / 500 on order): 0 immediate achievable SQM, BLOCKED status,
bottleneck named despite a zero NET shortfall, nothing on the buy list, the
forecast parked in its own `*_With_Ordered_*` fields, plus a control (move the
same units to `available` → fully ready) and a revert-check on the outlawed
`Allocated_Qty / Demand_Qty` formula.
⚠️ **The derived-view parity gate is partly vacuous for SME materials** —
every SME material in the legacy data has zero ERP movement, so both sides
agree trivially on exactly the columns the decoupling touches. Suite BA
covers it directly instead, on live-shaped data.
⚠️ Test-authoring trap suite AQ exposed: httpx
`cookies.set(..., domain="host")` SILENTLY drops the cookie (host-only
domain mismatch) — suite E's replay checks passed vacuously for months;
never pass `domain=`. The NL round-trip check needs the `gi_ai_ro` role —
CI provisions it in the workflow; locally re-run
`backend/scripts/create_ai_readonly_role.sql` after any reload/DDL drift.
Manual matrix: [automatic_test.md](automatic_test.md).

**CI/CD:** `postgres-dual-ci.yml` = bug_check + dual_ci + parity +
**gi_ai_ro provisioning step** + service_tests (with `GI_AI_RO_URL`) +
frontend build. **History:** the dual-ci job failed on the runner from
2026-07-07 (always at bug_check) until the July–August fixes; since then
bug_check re-emits failing ❌ checks as `::error::` annotations and uploads
`bugcheck_ci.log`/`BUG_REPORT.md`. It was **green on every run from Phase 13
through Phase 15e** (PRs #83–#98).
**Phase 17 (2026-10-03):** the workflow now fires on path-filtered pushes to
ANY branch plus PRs (one run per branch via a `concurrency` group), and
`USER_MANUAL.md`, `tests/ai_eval/**`, `docs/tutorials/**` joined the filters (a
manual-only commit used to trigger nothing although the AI grid is generated
from it). A third parallel job, **`ai-router-eval`**, installs Ollama 0.20.4,
verifies `qwen2.5:1.5b` BY DIGEST and runs `runner --router --require-model`
— on **probation** (`continue-on-error`) until 10 consecutive green runs with 0
flips (ruling Q17-3). **Phase 18 (2026-10-03): promoted to a hard gate by
operator order** after 4/4 green runs, 0 flips; its dev-block floor rose from
0.60 to 0.90 with guard patterns v3. ⚠️ `main` has no branch protection, so
no check *blocks* a merge until the operator marks them required. Router L2 (deterministic)
gates in the existing AI eval step from day one. It went red on all eight Phase 16 runs
(2026-10-01) at *Derived-view parity* — the lot port gained columns and the
returns subtraction the frozen SQLite view cannot have — and was fixed on
2026-10-03 with `SQL_LOT_BALANCE_PARITY` (§2a). ⚠️ **Lesson: wait for dual-ci
on the PR before merging.** The Phase 16 PRs were merged on green LOCAL gates
while their own CI runs had already failed, and the parity step is not a local
gate. `service_tests` on the runner is a documented SKIP (no master-data
snapshot; rule 16 — a SKIP is not a PASS). `deploy.yml` (v1 Streamlit) is **manual-only**; `deploy-v2.yml`
(manual, gated) is the cutover pipeline (post-restructure `tools/` paths +
the RO-role step). **Release pipeline (tags `v*` or workflow_dispatch
ONLY):** `release-desktop.yml` (macos-14 + windows-latest, `npx tauri
build` → dmg/nsis-exe/msi — ✅ green on v0.1.0–v1.0.1) and
`release-android.yml` (ubuntu + **JDK 21 — Capacitor 8 hardcodes Java 21;
JDK 17 was the v0.1.0–v1.0.1 failure**; `cap add android` regenerates the
gitignored project → debug APK); both inject
`VITE_API_URL=https://gi.giinventory.com/api` and attach assets to the same
tag Release via softprops/action-gh-release.

## 9. Operational notes

- Local dev: **`./bin/dev.sh localhost`** raises Postgres + API + Vite in one
  command (`tunnel` adds the cloudflared connector for local.giinventory.com;
  `gi` serves the gi.giinventory.com mirror without one; `stop` guarantees a
  clean slate — see PROJECT_HANDOVER §"Running it locally"). The pieces
  individually are still `./run_api.sh` (:8000, asyncpg → :5433/gihub) +
  `npm run dev --prefix frontend` (:5173). Hermetic: prefix
  `GI_DOTENV=0 GI_SCHEDULER=0`.
- **Local Cloudflare tunnel:** exactly ONE connector should run — the managed
  root LaunchDaemon (`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`).
  Several simultaneous connectors on different tunnel IDs is what produces the
  recurring **Error 1033**; diagnosis + recovery commands live in
  `deploy/cloudflared/README.md`. ⚠️ The tunnel token is passed as a
  command-line argument and is therefore visible in `ps aux`.
- Mirror Postgres runs on brew postgresql@16 :5433 (autostart).
- Meta/WhatsApp is LIVE (templates approved, lang `en`); operator TODOs that
  remain: approve `gi_evening_summary`, set webhook env + subscribe URL,
  set `PUBLIC_BASE_URL`.
- **Native apps + Cloudflare Access:** the domain sits behind Access; the
  installed apps have NO Access SSO session, so `/api` calls die as
  CORS-killed 302s ("Server unreachable" while the web portal works).
  One-time Zero Trust dashboard fix: Access application for
  `gi.giinventory.com/api/*` with a **Bypass (Everyone)** policy (the API
  self-guards). Full walkthrough + Apple-Silicon install fix (`sudo xattr
  -cr` **+ `codesign --force --deep --sign -`** — M-series refuses fully
  unsigned code) + local JDK-21 path: `docs/NATIVE_APPS.md`;
  troubleshooting: `docs/DEBUGGING.md` + `tools/diagnose_sync.py`.
- Remaining program work: **the Hetzner production deployment is PAUSED by
  decision (2026-07-30)** — the next phase is Feature Fine-Tuning and UI
  Polish. When it resumes: runbook `tools/migration/README.md` (includes the
  post-load Excel re-sync + SME reseed). Everything else through the 2026-07-18 pre-deploy
  batch AND the 2026-07-24…26 native program (Capacitor/Tauri + release
  pipeline + RTR + Send/Receive + QR stickers/scan) is SHIPPED
  (B2/B3/B4/B7 remain documented-optional). Ops handoff PDFs live in
  `docs/export/`
  (regenerate: `python tools/export_docs_pdf.py`).
