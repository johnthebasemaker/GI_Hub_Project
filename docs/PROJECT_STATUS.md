# PROJECT STATUS — resume here (updated 2026-07-06, 🧊 CODE FREEZE)

**This is the single source of truth for "where we left off."** A fresh chat
should read this file, then [`REPO_MAP.md`](../REPO_MAP.md) (segregation
contract), then [`NEW_STACK_HANDOFF.md`](NEW_STACK_HANDOFF.md) (how-to-work
rules), then [`POSTGRES_MIGRATION.md`](POSTGRES_MIGRATION.md) §8 (per-slice
run log). Legacy/SME rules: [`handoff.md`](../handoff.md) (SME Canon).

---

## 0. Current state in one paragraph

**CODE FREEZE is in effect (declared 2026-07-06 after commit `4ca97ef`).**
Every planned build program is complete except two deliberately-parked items:
**Phase 7 (WhatsApp/email outbox)** — waiting on the user's Meta Business
verification and permanent API token — and **SME Phase S6 (Master Data CRUD)**
— deferred to Cutover Day by locked ruling (dual-write drift protection).
The user is offline handling Meta document verification. The next activation
is EITHER "here are the Meta keys → build Phase 7" OR "start the deployment
cutover." Do not write code, refactor, or sweep until one of those arrives.

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

### E. Gates at freeze (all green)
`service_tests` **352/352** · `bug_check` **599/0** · `parity_check` **5/5** ·
`parity:sme` **509** · frontend build ✅ · `alembic check` clean ·
dual_ci mirror consistent. Schema additions since day one: `auth_sessions`,
`ai_jobs` (both user-authorized, new-stack-only).

---

## 3. WHAT WE'RE DOING NOW

**🧊 Standing by under code freeze.** No code, no refactors, no sweeps.
User is running Meta WhatsApp Business verification (started 2026-07-05).

---

## 4. WHAT WE WANT TO DO NEXT (in the user's order, on his signal)

1. **Phase 7 — WhatsApp/email outbox (new stack)** when the permanent Meta
   token arrives. Legacy already has the Meta Cloud API sender
   (`whatsapp_worker.py`, provider chain meta|twilio|pywhatkit) — port the
   outbox to `backend/api` using env-var credentials (never in chat/repo).
2. **Deployment cutover** (user's go-ahead + target details required):
   provision Hetzner CPX42 → run `deploy/` kit (`docs/DEPLOY.md`) → TLS via
   certbot → `ollama pull` the three models (one-warm-model config) → run
   `backend/scripts/create_ai_readonly_role.sql` with a production password +
   set `GI_AI_RO_URL` → final `dual_ci` data load → point users at React.
3. **Phase B restructure** (same day as cutover, one commit): legacy →
   `legacy/`, artifacts → `data-archive/`, bridge tools → `tools/`, CI paths.
4. **SME Phase S6 — Master Data CRUD + polish** (AFTER cutover only, when
   SQLite stops being the writer): equipment/recipe/materials CRUD,
   locations/types registration, scenario A/B diff, PDF polish.
5. **User-side ops (no code)**: bulk-assign the 1,672 imported man-hour rows
   to tanks; populate `tool_catalogue` to tighten AI tool identification;
   optionally stop the retired LocateAnything sidecar still running on :8503.

---

## 5. Verification commands (run from repo root)

```bash
# new-stack service + guard tests (352 checks)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub .venv/bin/python -u -m backend.api.service_tests
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
