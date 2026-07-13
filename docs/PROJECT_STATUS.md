# PROJECT STATUS — resume here (updated 2026-07-13 · CUTOVER DAY: SME S6 + PHASE B RESTRUCTURE SHIPPED — only the Hetzner deployment + optional polish remain)

**This is the single source of truth for "where we left off."** A fresh chat
should read this file, then [`ARCHITECTURE.md`](ARCHITECTURE.md) (**the full
system map: backend modules, DB traps, entry gates, rate limiting, AI routing,
PWA/offline mechanics, test commands — read it before touching code**), then
[`REPO_MAP.md`](../REPO_MAP.md) (segregation contract),
[`NEW_STACK_HANDOFF.md`](NEW_STACK_HANDOFF.md) (how-to-work rules), and
[`POSTGRES_MIGRATION.md`](POSTGRES_MIGRATION.md) §8 (per-slice run log).
Legacy/SME rules: [`handoff.md`](../handoff.md) (SME Canon).

---

## 0. Current state in one paragraph

**The 2026-07-06 code freeze was TEMPORARILY LIFTED (2026-07-08)** to close the
legacy→new feature-parity gap and prepare for multi-user testing. That whole
program is now **COMPLETE** (P0–P6 + deploy infra I-A/I-B), shipped in order:
deploy/CI infra (PG backup service, manual-trigger v2 Hetzner pipeline, S3
backups) · standalone extract at `~/gi_hub_v2` · P0 role-access manifest + AI
visibility · P1 SK bulk entry + snapshot · P2 HOD reject-reason + auto-draft PR ·
P3 sidebar ⌘K + collapsible nav · Phase 4 procurement depth (reschedule,
force-close + 24h undo, manual PO + vendors) · Phase I-B Cloudflare Tunnel
(gi-hub hijack) + CF-Connecting-IP rate-limit · Phase 5 PR-status report +
Dashboard valuation/charts + Admin system-overview · Phase 6 DN two-stage
approval + supervisor parity (intent-vs-actual UI, cancel-while-pending, live
cart stock) + receipt entry guards (MTC gate + UoM conversion) · **Deferred-MED
backlog** (logistics vendor-returns, HOD draft-PR line-edit/rename, admin lot
lifecycle). **2026-07-10: the five-phase UAT master plan is DONE** — Phase 1
critical fixes (global **+E.164 phone format**, profile-modal bugs, the 3-hour
returnables **timezone offset**, loan/return/overdue **borrower WhatsApp** +
in-app), Phase 2 (global search `q`/`category` on all read entities + stock
views, **/purchase-requests browse page**), Phase 3 QoL (**barcode/QR
picking**, smart last-entry defaults, **Open-POs KPI hero**), Phase 4
(**notification QA suite AA** — 22 pathways → whatsapp_outbox with the target
number; caught + fixed the warehouse-only dispatch in-app gap), Phase 5
(**production cutover script `scripts/migration/cutover_migrate.py`** +
runbook; dry-run **VERIFIED --strict** on a scratch PG; fixed a real data-loss
bug — `inventory.Sl_No` 293 values + `consumption.WBS/status` case-drop).
**2026-07-11: Phase 6 (inbound WhatsApp) is DONE** — Meta webhook
(`GET/POST /whatsapp/webhook` + `/api/v1/…` alias; verify-token handshake,
X-Hub-Signature-256 HMAC), sender→user resolution (+E.164), interactive
**STOCK <SAP>** (site-scoped) / **RESET PASSWORD** (temp credential, sessions
revoked) commands with session-text replies, **OTP-to-OLD-number** possession
proof on phone changes, and the **dynamic delivery engine**:
`X-Delivery-Preference: evening` stages events in
`pending_summary_notifications`; a 16:00 aggregator (lifespan task, manual
`POST /admin/digests/run`) compiles ONE `gi_evening_summary` template per
recipient (critical alerts always immediate). Operator TODO: approve
`gi_evening_summary` (2 body vars, lang `en`) + set
`WHATSAPP_WEBHOOK_VERIFY_TOKEN`/`WHATSAPP_APP_SECRET` + subscribe the webhook
URL in Meta.
Gates green:
`service_tests` **649/0** (suites A…AH), `parity_check` **5/5**, `bug_check`
**599/0**, `parity:sme` **509**, frontend build ✅, `tsc --noEmit` ✅, alembic
single head **e7c31a9f24d5**, Playwright E2E **39/39** (`cd tests/e2e && npm
test`; the `gated` project runs entry-docs last because it flips a global
setting). Exact commands: ARCHITECTURE.md §8.
**Parity sprint (2026-07-13, approved plan):** the legacy **entry-document
system is back** — SK uploads (file or 📷 camera) hard-gate Issue/Receipt/
Return submission via the `require_entry_documents` setting (**default ON**;
tests relax it), HOD **Document Library** at /hod/documents (35 migrated
legacy files included) + inline 📎 preview in Approvals; **returns** are made
against a source receipt (30-day window, override+justification → red >30d
badge, qty capped, Return DN No. required, approval auto-emails logistics);
**MTC** detection fixed to legacy `Category == "Surface Shields"` (setting
`mtc_required_category`) with an MTC-number field, hard-block kept; **WBS**
master per site (HOD config) required on entries once configured; FEFO
manual-lot override captures a reason → HOD alert; Bin/Shelf on Receive;
localStorage form-draft recovery; Records→Documents links. Known deliberate
divergences from legacy: allow-and-log over-issue/FEFO (locked ruling),
per-row+bulk approvals instead of the legacy single EOD commit, MTC block
(stricter than legacy warn-only). Skipped by choice: B2 SME batch entry lane,
B3 QR request queue, B4 PO-PDF blob, B7 admin extras, C3 OCR doc assist.
Phase 8 (2026-07-13): **/analytics/lining-coverage** — the SME engine run
against LIVE ledger stock (RL/BL coverage + burn-based depletion dates,
HOD/Logistics page at /hod/lining-coverage) · **abuse hardening** — OTP 3/h
per IP AND per phone + webhook HMAC penalty box (5 bad sigs → 15-min IP ban),
relaxed under GI_DOTENV=0, `GI_FORCE_STRICT_LIMITS=1` for the limits suite,
global 429 countdown toast · **weekly exec PDF automation** — Friday 17:00
lifespan daemon → `generated_reports` + 72-h tokenized download link via
WhatsApp/bell to every admin+HOD (`POST /admin/reports/weekly-exec/run` for
on-demand; ⚠️ set `PUBLIC_BASE_URL` in deploy/.env).
Polish sprint (2026-07-13): cutover migration EXECUTED against the :5433
mirror (`cutover_migrate.py --strict --wipe`, sync driver) → CUTOVER ✅
VERIFIED + migrated data visually QA'd in the live UI; the §F2 Playwright
plan is now BUILT (`tests/e2e/` — throwaway-DB globalSetup, per-role
storageState, auth/smoke/workflows/negative-access/exec-PDF/offline/ask-data
specs); the SPA is an installable PWA (manifest + autoUpdate SW + NetworkFirst
read cache) with an IndexedDB offline mutation queue on the entry forms
(header sync badge, auto-replay on reconnect); and `POST /ai/query` powers an
"Ask your data" dashboard card for level ≥2 — deterministic site-pinned SQL
templates for HODs, NL→SQL fallback (unscoped only, AI-5 ruling intact).
QA night shift (2026-07-12): full multi-role E2E on an isolated `gihub_e2e`
clone — 21/21 workflow checks (W1 entry-approval, W2 SMR, W3 PR→PO) + 63/63
page-render probes (0×500) + negative-access lattice, all green; **no
functional bugs found**. Cleaned up antd-6 console deprecations (Alert
message→title ×7, Modal destroyOnClose→destroyOnHidden, Space
direction→orientation, rowKey index-param ×3) → fresh-tab console is silent.
Permanent matrix at `docs/automatic_test.md`; Playwright automation plan in §F2.
UAT round 2 (2026-07-12): the Executive Summary PDF is now SERVER-rendered
(`exec_pdf.py`, fpdf2 — measured tables, nothing cut at page edges; the
print-the-webpage approach is gone); the Urgent/Evening delivery toggle moved
from ProfileModal to the Issue/Receive/Return forms (only transaction posts
send `X-Delivery-Preference`; profile/OTP calls are always immediate); phone
changes use a DUAL-OTP flow (`phone_otp.stage`: code 1 → OLD number
authorizes, code 2 → NEW number proves deliverability before commit;
first-time setup skips step 1).
**Phase 7 (WhatsApp), 7b (email) AND
7c (ubiquitous notifications) are DONE** — native v2 `whatsapp_outbox` +
`email_outbox` + **a reusable-template layer (`gi_action_required` /
`gi_status_update` / `gi_critical_alert` / `gi_otp_code`) and a unified
`dispatch()` so every significant action fires BOTH an in-app notification
(bell, all portals) AND a best-effort WhatsApp** to the concerned party, plus
**self-service phone changes via a WhatsApp OTP** (`phone_otp`, admins override
without OTP). Triggers now span PR/PO, DN multi-stage, entries/approvals, MTC
upload, lot quarantine/dispose, vendor returns, reschedules, force-close, SMR,
cross-site, SLA, FEFO, report delivery.
**CUTOVER DAY (2026-07-13): SME Phase S6 (Master Data CRUD) SHIPPED** — the
last parked phase. `backend/api/sme_master.py` (`/sme/master/*`, exact-lock
{hod, admin}, HOD site-pinned, all writes audited; legacy Tab 8 semantics:
equipment create seeds `sme_sqm_progress` and delete cascades it, materials
write `sme_inventory_seed` only, settings guarded by equipment usage) + the
🗄️ Master Data tab in SmePage (5 sub-tabs, mutations invalidate the whole
/sme query family). Suite **AI** (+32) → `service_tests` **681/0**; engines
+ goldens untouched (`parity:sme` 509 PASS). Run-log:
POSTGRES_MIGRATION.md §8.
The user is handling **Cloudflare-Tunnel local hosting** (`gi.giinventory.com`) +
UI smoke testing himself. Remaining feature backlog: §4 below + the
`feature-gap-program` memory (only optional LOW polish left).

---

## 1. THE TWO PROJECTS — bulletproof separation 🛡

Two applications coexist in this repo **on purpose** until cutover day. The
full ownership table is in [`REPO_MAP.md`](../REPO_MAP.md); these are the
invariants that make the separation bulletproof — no feature work on one side
can affect the other:

| # | Guarantee | Enforced by |
|---|---|---|
| 1 | **LEGACY (production)** = Streamlit + SQLite, feature-frozen. Lives at repo root (`main.py`, `database.py`, `pages_internal/`, `ai/`, …) | `REPO_MAP.md` ownership table; golden rule: never edit `database.py`/`pages_internal/` for new-stack work |
| 2 | **NEW STACK (ship-ready)** = React + FastAPI + PostgreSQL. Lives ONLY in `backend/`, `frontend/`, `deploy/` | Same contract; every new-stack commit touches only those dirs (+`docs/`) |
| 3 | Different **databases**: legacy writes SQLite (`gi_database.db` = system of record); new stack reads/writes its own PostgreSQL (`:5433/gihub`), loaded as a mirror by `backend/dual_ci.py` | Physical process separation; PG is disposable/reloadable, SQLite is truth |
| 4 | Different **ports/processes**: Streamlit `:8501` · FastAPI `:8000` · Vite `:5173` · RAG sidecar `:8503` (retired) | No shared runtime |
| 5 | Different **deploy surfaces**: legacy = root `docker-compose.yml`, new stack = `deploy/docker-compose.prod.yml` | Never mixed |
| 6 | **Legacy gates must stay green after every commit**: `bug_check.py` **599/0**, `test_ui_crawler.py` 21/21 | Shared CI (`.github/workflows/postgres-dual-ci.yml`) runs BOTH apps' gates on every push |
| 7 | **Mirror-integrity gate**: `backend/api/parity_check.py` proves PG-derived views == SQLite views (5/5) — catches ANY contamination between stacks | CI + per-phase verification |
| 8 | **SME Canon**: `sme_*` tables are read-only for the new stack (writes deferred to S6/cutover); explicit-PK ordering, never rowid | Canon in `handoff.md` + zero write endpoints exist (test-proven) |
| 9 | **SME engine parity**: the client TS engine (`frontend/src/sme/engine.ts`) and Python oracle (`backend/api/sme_engine.py`) are proven equal against a shared golden fixture — 509 comparisons, both sides re-verified in CI (`service_tests` suite G + `npm run parity:sme`). **If numeric behavior changes, change BOTH in one commit and regenerate the golden** | Golden files `backend/api/sme_parity_fixture.json` / `sme_parity_golden.json` |
| 10 | **Man-Hours boundary**: `mh_*` tables belong to the MH portal (both stacks write them); it reads `sme_*` strictly read-only | Phase 10/11 tests |
| 11 | Bridge tools (`backend/dual_ci.py`, `migrate_sqlite_to_postgres.py`, `api/parity_check.py`) import legacy `database.py` **by design** and retire at cutover | REPO_MAP "bridge" markers |
| 12 | **Physical restructure (Phase B)** — legacy → `legacy/`, data → `data-archive/` — happens in ONE commit on cutover day; until then nothing moves | Pre-approved plan in REPO_MAP |

---

## 2. WHAT WE DID (all shipped, all pushed to `origin/main`)

### A. New-stack feature-parity build (10 slices) — COMPLETE
Role locks & site scoping · HOD ops pack · warehouse returns · store-keeper
toolbox · reports + scheduler · documents/PDF generators · SME read-parity ·
admin console · auth/refresh hardening · navy/gold UI overhaul.
(Only Phase 7 — email/WhatsApp outbox — remains, on Meta-token hold.)

### B. Man-Hours & Labor Tracking (Phases 10, 11A, 11B, 11C) — COMPLETE
Employee roster + timesheets + xlsx import (exact `{hod, admin}` lock) ·
'nan' hygiene + bulk-assign · SME link layer (productivity norms + Equipment
Scorecard, `sme_*` read-only) · auto-draft estimates + manpower forecast.
Commits `2fdb641`, `556d25d`, `f252493`, `0e1b4a2`.

### C. Intelligence Layer (Phases AI-0 … AI-5) — COMPLETE
All local Ollama (llama3.1:8b · qwen2.5-coder:7b · qwen2.5vl:7b), no external
AI APIs. Foundation + SSE Hub Assistant (`e8e6c8e`) · PR/PO PDF extraction
with preview-confirm (`762d80f`) · handwriting-OCR job queue, `ai_jobs` table
(`d8f4bb7`) · client-side QR Smart Scan + tool vision (`1c22cb9`) · NL→SQL on
a TRUE read-only PG login `gi_ai_ro` + insights + EOD summary (`0c6297a`).
LocateAnything sidecar RETIRED by ruling. Per-feature admin flags.

### D. SME React rebuild (Phases S1 … S5) — COMPLETE, sprint closed
The read-facing Smart Material Estimator, rebuilt to exceed the 7,600-line
Streamlit portal — all EIGHT legacy tabs:
- **S1** `1888139` — model snapshot API + `POST /sme/plan/cascade` oracle +
  dual TS/Python engine with golden-fixture parity (509 checks both sides).
- **S2** `6ef1746` — Dashboard: 4-way cascading cross-filters, 7 KPI
  drill-down modals, legacy SVG gauge/hbars as React components, Recharts,
  material balance w/ 4-tier tinting. Zero server round-trips on interaction.
- **S3** `f823276` — Session Builder + Session Report: dnd-kit drag priority
  with INSTANT client re-cascade (browser ≡ oracle to the displayed digit on
  the real J021/J022 contention pair), client-side suggestion simulations,
  localStorage + `?scenario=` URL sharing, oracle-rendered exports.
- **S4** `6272eb3` — 🛒 procurement sub-view, dual-mode Location Report
  (independent per-location drag orders + stale-tag reconciliation),
  Equipment/System-Code matrix reports, per-scope export titles.
- **S5** `4ca97ef` — Execution Plan (critical-code plan w/ worst-code smart
  default, Progress List + production detail blocks, ±1% variance
  comparison) + virtualized Total Overview master grid.
  Backend adds (read-only): `GET /sme/production-log`, export keys
  `progress-list`/`production-log`, plan-export key `overview`.

### E. Gates (all green, current)
`service_tests` **591/0** (360 at freeze → +231 across freeze-lift suites
H–AC: SLA tracker, submission intel, bulk entry, reschedule, force-close, manual
PO, rate-limiter IP, reporting/dashboard, DN approval, supervisor parity, entry
guards, vendor-returns, PR line-edit/rename, lot lifecycle, WhatsApp outbox,
email outbox, phone OTP **+ Meta sandbox #131030 graceful-degradation +
OTP-to-old-number**, **loan notifications + timezone (Y)**, **search/PR
browse (Z)**, **22-pathway notification QA (AA)**, **inbound webhook + dynamic
delivery/evening digest (AB)**, **HOD executive summary + server PDF (AC)**,
**dual-OTP phone change (X)**) · `bug_check` **599/0** ·
`parity_check` **5/5** · `parity:sme`
**509** · frontend build ✅ · `alembic check` clean (single head **d6b0e72f51a8**) ·
dual_ci mirror consistent. Schema additions since day one: `auth_sessions`,
`ai_jobs`, `sla_dismissals`, `users.Location`/`pending_users.Location`,
`whatsapp_outbox` (Phase 7), `email_outbox` (Phase 7b), `phone_otp`
(Phase 7c) and the **legacy-column preservation set** (`inventory.Sl_No`,
`consumption."WBS"/status/Technician`, `pending_issues.Technician`,
`rejected_issues_archive.Technician` — UAT Phase 5 cutover audit) — all
user-authorized; Phase-4/5/6 feature work otherwise reused existing tables
(`app_notifications` powers the in-app bell). **Cutover data migration:**
`scripts/migration/cutover_migrate.py` + runbook, dry-run VERIFIED `--strict`.

### F. Post-freeze work (2026-07-08 freeze-lift) — SHIPPED, pushed to `origin/main`
- **Deploy / CI infra:** v2 Postgres backup service (`deploy/backup/backup-pg.sh`,
  nightly `pg_dump -Fc` + optional S3 push) · manual-trigger v2 Hetzner pipeline
  (`.github/workflows/deploy-v2.yml` + `deploy/deploy-v2.sh` / `health-check.sh` /
  `rollback.sh`, with the v1↔v2 `:80/:443` **port-handover guard**). Commits
  `35603f5`, `af5a3e9`.
- **Standalone extract:** `~/gi_hub_v2` — the new stack copied out (bridge tools
  excluded), `service_tests` 386/0 proven in place. This is a *copy-out*, NOT the
  in-repo Phase B restructure (still reserved for cutover day).
- **Feature-gap program:** P0 role-access single-source-of-truth
  `frontend/src/config/nav.tsx` (ports legacy `_can_access`) + client route
  guards + lean-admin default / "All areas" toggle + AI insight open-by-default
  (`f221a95`); P1 SK bulk `POST /entry/bulk` + `GET /entry/snapshot` + batch UI
  (`a119fa9`); P2 HOD reject-reason modal + auto-draft PR button (`b99935b`); P3
  ⌘K command palette + collapsible role-primary nav (`a176b88`); **Phase 4**
  reschedule (`34a8b62`), force-close + 24h undo (`cc3040a`), manual PO + vendor
  picker (`1f0d811`); **Phase I-B** CF-Connecting-IP rate-limit + gi-hub tunnel
  hijack config (`b94ecaa`); **Phase 5** PR-status report + Dashboard valuation
  KPI/charts + Admin system-overview (`685b614`); **Phase 6** DN two-stage
  approval (`2db5b2f`), supervisor parity — intent-vs-actual UI + cancel + live
  cart stock (`41712dc`), receipt entry guards — MTC gate + UoM conversion
  (`7a72ff4`); **deferred-MED** vendor-returns (`92daaa8`) + HOD draft-PR
  line-edit/rename (`e9cea70`) + admin lot lifecycle (`45175bc`); **Phase 7**
  native WhatsApp Cloud API outbox + triggers + admin console (`whatsapp_outbox`
  table, Meta-hold lifted; `99faf6b`), alerts hardened to approved TEMPLATE
  messages for 24h-window deliverability (`257cacb`); **Phase 7b** native SMTP
  `email_outbox` + MTC-missing / vendor-return logistics emails + admin Email
  Console. **The entire feature-gap backlog (P0–P6 + I-A/I-B + deferred-MED)
  AND Phase 7/7b are DONE.** See `feature-gap-program` memory — only optional
  LOW polish remains.

---

### F2. Automated visual E2E — the Playwright plan (2026-07-12 QA night shift)

> ✅ **BUILT 2026-07-13 (polish sprint Phase A)** — the suite lives at
> `tests/e2e/` (not `frontend/e2e/`): DB-clone globalSetup via the real
> cutover script, hermetic uvicorn :8010 + Vite :5183, per-role storageState
> (admin/hod/sk/supervisor/logistics), specs for auth, per-role smoke,
> W1/W1b/W1c/W2/W3 workflows, negative access, exec-summary PDF, the offline
> queue and the ask-data card — **38/38 in ~14 s** via `npm test`. The plan
> below is kept for the CI wiring + remaining matrix rows (see
> `tests/e2e/README.md`).

`docs/automatic_test.md` is the permanent, human-run test matrix. To turn it
into a **scripted, headless CI suite**, here is exactly what it takes:

1. **Runner:** add Playwright (`@playwright/test`) to `frontend/` — `npm i -D @playwright/test && npx playwright install chromium`. Tests live in `frontend/e2e/*.spec.ts`. It bundles browser control, network assertions, auto-waiting, trace/video capture, and parallel workers — no Selenium glue.
2. **Isolated backend fixture (the key piece, already proven tonight):** a `globalSetup` that clones the DB (`CREATE DATABASE gihub_e2e_ci TEMPLATE gihub`), boots uvicorn with `GI_DOTENV=0 GI_SCHEDULER=0 DATABASE_URL=…gihub_e2e_ci JWT_SECRET=…` on a test port, waits for `/health`, and a `globalTeardown` that drops the DB. This is the recipe in `automatic_test.md §0` — it makes runs hermetic and repeatable (WhatsApp/Meta stay dark). Each `test.describe` can `TRUNCATE` its own scratch rows or re-clone for full isolation.
3. **Role sessions:** one `storageState` JSON per role (admin/hod/sk/supervisor/logistics/warehouse), created once in setup by POSTing `/auth/login` and saving the token to `localStorage` — tests then start already-authenticated (no login-rate-limit flakiness). Playwright `test.use({ storageState })` per project.
4. **Matrix → specs, 1:1:** every table row in `automatic_test.md` becomes one `test()`. The cross-role workflows (§11 W1–W10) become sequential specs that hand off between role contexts in a single file — precisely the flows validated tonight via the `e2e_workflows.py` harness (that script is the executable spec; port its 21 assertions verbatim). Negative-access (§12) becomes a data-driven `for (const [role, path] of matrix)` loop asserting 403.
5. **Assertions:** prefer Playwright's `page.waitForResponse(/\/api\/…/)` + `expect(status).toBe(200)` and DOM `expect(locator).toHaveText(...)` over pixel checks; add `toHaveScreenshot()` snapshots only for the 3–4 layouts that matter (dashboard, exec summary, SME grid) to catch visual regressions without brittleness.
6. **CI wiring:** a GitHub Actions job (or the existing runner) with a Postgres service container, `alembic upgrade head`, seed, then `npx playwright test`. Publish the HTML report + traces as artifacts; fail the build on any red. Gate merges on it alongside `service_tests`.
7. **Effort estimate:** ~1 day to stand up the harness (fixture + role sessions + 5 smoke specs), then ~2–3 days to port the full matrix (~120 checks). The backend is already E2E-friendly (in-process ASGI tests, deterministic seed, IP-keyed rate limits you can bypass with a header) so most of the cost is writing selectors, not fighting infrastructure.

Bottom line: the two hard parts of visual E2E — a disposable real-data backend and multi-role session handoff — are already solved and documented; Playwright is the thin, mergeable layer on top.

---

## 3. WHAT WE'RE DOING NOW

**🔓 Feature-gap program + Phase 7/7b COMPLETE, still under the temporary
freeze-lift.** Every parity gap AND the WhatsApp/email outboxes are closed and
pushed. The user is running the app for multi-user testing via the **Cloudflare
Tunnel** (`gi.giinventory.com`, config in `deploy/cloudflared/`) and doing
browser smoke tests. Nothing is in flight — awaiting the next directive (a new
feature ask, optional LOW polish, or the **cutover go-ahead**).

---

## 4. WHAT WE WANT TO DO NEXT

**Feature-gap backlog — approved scope AND deferred-MED are DONE.** Only optional
LOW polish remains (details in the `feature-gap-program` memory):
- **LOW polish:** barcode/QR pick · smart last-entry defaults · recently-used
  pills · form draft-recovery · report category filter / SAR toggle / preview ·
  open-POs filters + KPI hero · FEFO auto-suggest on DN prep.

**Cutover / parked:**
5. ~~**Phase 7/7b/7c — WhatsApp + email + ubiquitous notifications**~~ ✅ **DONE 2026-07-09** — native
   v2 `whatsapp_outbox` + `email_outbox` (stdlib-SMTP; `SMTP_*` +
   `EMAIL_LOGISTICS_TO` in `deploy/.env`) + a **reusable-template layer + unified
   `dispatch()`** so every significant action writes BOTH an in-app row (bell)
   AND a best-effort WhatsApp, plus **self-service phone OTP** (`phone_otp`;
   admins override without OTP). **⚠️ Operator action:** create + approve **four
   Meta templates** in WhatsApp Manager — `gi_action_required`, `gi_status_update`,
   `gi_critical_alert` (each Utility, TWO body vars `{{1}} {{2}}`) and
   `gi_otp_code` (Authentication, ONE body var `{{1}}`); names are env-overridable
   (`WHATSAPP_TPL_*`). Exact body text is in the Phase 7c run-log + the delivery
   report. Legacy `whatsapp_worker.py`/mailer NOT reused.
6. **Local hosting** — Cloudflare Tunnel → `gi.giinventory.com` (user-driven;
   plan in the `feature-gap-program` memory). **Gotcha:** the rate-limiter keys
   on `X-Real-IP`; behind the tunnel the real client IP is `CF-Connecting-IP` —
   map it or all testers share one bucket.
7. **Deployment cutover** (Hetzner CPX42 → `deploy/` kit (`docs/DEPLOY.md`) → TLS
   → `ollama pull` the 3 models → `create_ai_readonly_role.sql` + `GI_AI_RO_URL`
   → final `dual_ci` load → point users at React). The v2 deploy pipeline is
   built (§2.F) and manual-trigger only.
8. ~~**Phase B restructure**~~ ✅ **EXECUTED 2026-07-13 (cutover day)** —
   legacy → `legacy/`, artifacts → `data-archive/`, bridge tools → `tools/`;
   CI/Playwright/runbook paths updated; ALL gates re-verified green
   (bug_check 599/0 · crawler 21/21 · service_tests 681/0 · parity 5/5 ·
   Playwright 39/39). `gi_database.db` deliberately stays at root (final
   production load reads it; never staged). Run-log: POSTGRES_MIGRATION.md §8.
9. ~~**SME Phase S6 — Master Data CRUD**~~ ✅ **SHIPPED 2026-07-13 (cutover
   day)** — `backend/api/sme_master.py` + SmePage 🗄️ Master Data tab; suite
   AI → service_tests 681/0. Optional polish only.

---

## 5. Verification commands (run from repo root)

```bash
# new-stack service + guard tests (418 checks; needs JWT_SECRET set)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub JWT_SECRET=ci-only-service-test-secret-key-32bytes-min .venv/bin/python -u -m backend.api.service_tests
# SQLite↔PG derived-view parity (5 views) — Phase B: now in tools/
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python tools/parity_check.py
# SME TS↔Python engine parity (509 comparisons)
node frontend/scripts/sme_parity.mjs          # or: npm run parity:sme --prefix frontend
# legacy gates — Phase B: the legacy app lives in legacy/
.venv/bin/python legacy/bug_check.py          # 599/0
# frontend build
npm run build --prefix frontend
```

Local services: FastAPI `uvicorn backend.api.main:app --port 8000` (needs
`DATABASE_URL`), Vite dev via `.claude/launch.json` ("frontend"), PostgreSQL
on `:5433/gihub`, Ollama local with all three models installed.

---

## 6. Hard-won gotchas a fresh session must know

- **Never delete `system_audit_log` rows** — audit assertions are
  DELTA-counted (PR numbers restart per day).
- **SME engine changes** = change BOTH engines + regenerate the golden in ONE
  commit (see §1 row 9). Rounding is shared half-up `floor(x·10ⁿ+0.5)` — do
  not "fix" it to Python `round()`/pandas (half-even would break parity).
- `sme_consumption_log` is EMPTY in the mirror → Execution-Plan comparison/
  production details legitimately show empty states until real entries commit.
- antd v6: Select internals are `.ant-select-content` (no `.ant-select-selector`);
  virtual Table rows are `[data-row-key]`, not `.ant-table-tbody tr`.
- Watch for literal NUL bytes sneaking into written TS files (unitKey code)
  — git flags the file binary; replace with the `\u0000` escape.
- The Claude preview browser: restart the preview server for a truly clean
  console (HMR windows leave stale errors); re-read DOM in a second eval
  after clicks.
- `gi_database.db` stays modified-but-uncommitted; never stage it.
