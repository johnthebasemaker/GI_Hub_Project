# PROJECT STATUS — resume here (updated 2026-07-08, 🔓 FREEZE TEMPORARILY LIFTED — feature-gap program active)

**This is the single source of truth for "where we left off."** A fresh chat
should read this file, then [`REPO_MAP.md`](../REPO_MAP.md) (segregation
contract), then [`NEW_STACK_HANDOFF.md`](NEW_STACK_HANDOFF.md) (how-to-work
rules), then [`POSTGRES_MIGRATION.md`](POSTGRES_MIGRATION.md) §8 (per-slice
run log). Legacy/SME rules: [`handoff.md`](../handoff.md) (SME Canon).

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
lifecycle). Gates green:
`service_tests` **473/0**, `parity_check` **5/5**, `bug_check` **599/0**,
`parity:sme` **509**, frontend build ✅. STILL PARKED: **Phase 7 (WhatsApp/email
outbox)** on the Meta token, and
**SME Phase S6 (Master Data CRUD)** to Cutover Day (dual-write drift protection).
The user is handling **Cloudflare-Tunnel local hosting** (`gi.giinventory.com`) +
UI smoke testing himself. Remaining feature backlog: §4 below + the
`feature-gap-program` memory. All WhatsApp/email work stays parked.

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
`service_tests` **473/0** (360 at freeze → +113 across freeze-lift suites
H–U: SLA tracker, submission intel, bulk entry, reschedule, force-close, manual
PO, rate-limiter IP, reporting/dashboard, DN approval, supervisor parity, entry
guards, vendor-returns, PR line-edit/rename, lot lifecycle) · `bug_check`
**599/0** · `parity_check` **5/5** · `parity:sme`
**509** · frontend build ✅ · `alembic check` clean · dual_ci mirror consistent.
Schema additions since day one: `auth_sessions`, `ai_jobs`, `sla_dismissals`,
`users.Location`/`pending_users.Location` (all user-authorized, new-stack-only;
every Phase-4/5/6 feature reused existing tables — **no** further migration).

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
  (`7a72ff4`). **The entire approved feature-gap backlog (P0–P6 + I-A/I-B) is
  DONE.** See `feature-gap-program` memory for the audit + the only-LOW-polish +
  parked-Meta remainder.

---

## 3. WHAT WE'RE DOING NOW

**🔓 Feature-gap program COMPLETE (P0–P6 + I-A/I-B), still under the temporary
freeze-lift.** All approved-scope parity gaps are closed and pushed. The user is
running the app for multi-user testing via the **Cloudflare Tunnel**
(`gi.giinventory.com`, config in `deploy/cloudflared/`) and doing browser smoke
tests. Nothing is in flight — awaiting the next directive (a new feature ask, the
Meta token for Phase 7, or the cutover go-ahead).

---

## 4. WHAT WE WANT TO DO NEXT

**Feature-gap backlog — approved scope AND deferred-MED are DONE.** Only optional
LOW polish remains (details in the `feature-gap-program` memory):
- **LOW polish:** barcode/QR pick · smart last-entry defaults · recently-used
  pills · form draft-recovery · report category filter / SAR toggle / preview ·
  open-POs filters + KPI hero · FEFO auto-suggest on DN prep.

**Still parked / cutover (unchanged):**
5. **Phase 7 — WhatsApp/email outbox** when the Meta permanent token arrives.
   Legacy has the Meta Cloud API sender (`whatsapp_worker.py`) — port to
   `backend/api` with env-var creds. ALL WhatsApp/email work stays parked until then.
6. **Local hosting** — Cloudflare Tunnel → `gi.giinventory.com` (user-driven;
   plan in the `feature-gap-program` memory). **Gotcha:** the rate-limiter keys
   on `X-Real-IP`; behind the tunnel the real client IP is `CF-Connecting-IP` —
   map it or all testers share one bucket.
7. **Deployment cutover** (Hetzner CPX42 → `deploy/` kit (`docs/DEPLOY.md`) → TLS
   → `ollama pull` the 3 models → `create_ai_readonly_role.sql` + `GI_AI_RO_URL`
   → final `dual_ci` load → point users at React). The v2 deploy pipeline is
   built (§2.F) and manual-trigger only.
8. **Phase B restructure** (in-repo, one commit at cutover): legacy → `legacy/`,
   artifacts → `data-archive/`, bridge tools → `tools/`. (A verified copy-out
   already exists at `~/gi_hub_v2`.)
9. **SME Phase S6 — Master Data CRUD + polish** (AFTER cutover only).

---

## 5. Verification commands (run from repo root)

```bash
# new-stack service + guard tests (418 checks; needs JWT_SECRET set)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub JWT_SECRET=ci-only-service-test-secret-key-32bytes-min .venv/bin/python -u -m backend.api.service_tests
# SQLite↔PG derived-view parity (5 views)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python -u -m backend.api.parity_check
# SME TS↔Python engine parity (509 comparisons)
node frontend/scripts/sme_parity.mjs          # or: npm run parity:sme --prefix frontend
# legacy gates
.venv/bin/python bug_check.py                 # 599/0
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
