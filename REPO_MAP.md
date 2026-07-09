# REPO MAP — who owns what in this repository

Two applications deliberately coexist in this repo until **cutover day**:

1. **LEGACY (production):** Python + Streamlit + SQLite. Feature-frozen for
   new-stack work; must stay green (`bug_check.py` 599/0, `test_ui_crawler.py`
   21/21) after every change.
2. **NEW STACK (ship-ready, pre-cutover):** React + FastAPI + PostgreSQL.
   Lives only in `backend/`, `frontend/`, `deploy/`.

This file is the boundary contract. The physical restructure (**Phase B** —
moving the legacy app into `legacy/`, stray data into `data-archive/`) is
scheduled for cutover day and happens in a single commit; until then **nothing
moves**. Read [`docs/NEW_STACK_HANDOFF.md`](docs/NEW_STACK_HANDOFF.md) before
touching the new stack, and `handoff.md` (SME Canon) before touching legacy.

> **STATUS 2026-07-08 — 🔓 FREEZE TEMPORARILY LIFTED (feature-gap program).**
> Both apps green (`service_tests` **418/0**, `bug_check` **599/0**, parity 5/5,
> `parity:sme` 509). On top of the frozen build (parity + Man-Hours + AI-0…AI-5 +
> SME S1…S5) we shipped, working within this same segregation: deploy/CI infra
> (v2 backup service + manual-trigger Hetzner pipeline + S3 backups), a
> standalone new-stack copy-out at `~/gi_hub_v2`, and the feature-gap program
> (P0 role-access · P1 SK bulk entry · P2 HOD correctness · P3 sidebar ⌘K ·
> Phase 4 procurement depth). All new-stack work still touches ONLY
> `backend/` · `frontend/` · `deploy/` · `docs/`. Remaining: feature-gap backlog,
> Phase 7 (Meta-token hold), cutover, in-repo Phase B, SME S6 (post-cutover).
> Resume snapshot: [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — including
> the **bulletproof separation invariants** (12 guarantees).

## Ownership

| Path | Owner | Notes |
|---|---|---|
| `main.py` | Legacy | Streamlit entrypoint — **also the Streamlit Cloud demo entry; path-sensitive, do not move before Phase B** |
| `database.py` | Legacy ⚠ bridge | Legacy data layer. **Imported by `backend/dual_ci.py`** as the SQLite→PG migration reader — never edit for new-stack work |
| `auth.py` · `ui_components.py` · `cache_layer.py` · `error_handling.py` · `mailer.py` · `reports.py` · `recover.py` · `migrate_role.py` | Legacy | Streamlit app modules |
| `config.py` | Legacy ⚠ reference | Brand colors here are **mirrored (not imported)** by `frontend/src/theme/tokens.ts` — keep the two in sync when brand colors change |
| `whatsapp_worker.py` | Legacy | Outbound WhatsApp queue worker — provider chain `WHATSAPP_PROVIDER=meta\|twilio\|pywhatkit` (Meta Cloud API already implemented) |
| `pages_internal/` · `services/` · `ai/` · `pwa/` · `scripts/` · `tests/` | Legacy | Portal pages · RAG sidecar + WhatsApp-webhook helpers · Ollama/CV · Phase-4 PWA · ops/seed scripts · pytest |
| `uploads/` · `logs/` · `backups/` | Legacy | Runtime data dirs |
| `bug_check.py` · `test_ui_crawler.py` | Legacy gate | The old app's regression gates; run in the **shared** CI |
| `packages.txt` · `requirements-server.txt` | Legacy | Streamlit Cloud apt deps · Linux-server dependency subset (workstream C) |
| `docker-compose.yml` · `Dockerfile.streamlit` · `Dockerfile.fastapi` · `docker/` · `host_setup/` | Legacy deploy | **Workstream-C surface** (nginx/certbot/streamlit/ollama). NB: `Dockerfile.fastapi` is the *RAG sidecar*, **not** the new-stack API |
| `backend/` | New stack | FastAPI API (`backend/api/`), SQLAlchemy models, Alembic, **plus the bridge tools** `dual_ci.py` / `migrate_sqlite_to_postgres.py` / `api/parity_check.py` (these import legacy `database.py` by design; they retire at cutover) |
| `frontend/` | New stack | React + Vite + AntD SPA; theme source of truth in `src/theme/` |
| `deploy/` | New stack deploy | **The new-stack surface**: `docker-compose.prod.yml` (incl. a `backup` pg_dump service), `Dockerfile.api`, `Dockerfile.web`, nginx, certbot, `backup/backup-pg.sh` (+S3), and the manual-trigger v2 pipeline scripts `deploy-v2.sh` / `health-check.sh` / `rollback.sh` — see [`docs/DEPLOY.md`](docs/DEPLOY.md) §9 |
| `run_api.sh` | New stack | Local backend launcher (`:8000`) |
| `backend/requirements.txt` | New stack | The new stack's Python deps; **included by root `requirements.txt` via `-r`** (one shared venv pre-cutover; standalone install post-cutover) |
| `gi_database.db` | **Shared bridge** | SQLite system of record (legacy runtime) **and** the source for `dual_ci` / parity. Do not move before Phase B |
| `requirements.txt` | **Shared** | The one venv both Python stacks use; ends up pulling `backend/requirements.txt` in |
| `.github/workflows/postgres-dual-ci.yml` | **Shared** | One workflow gating BOTH apps: bug_check + dual_ci + parity + service_tests + frontend build. Needs both codebases in one checkout — do not split before cutover |
| `docs/` · `handoff.md` | Shared docs | New-stack handoff/migration log/deploy runbook · SME Canon + legacy handoff |
| `*.xlsx` · `*.pdf` · `IMG_2397.JPG` · `gi_database.*.bak` · `PyWhatKit_DB.txt` · `demo_seed.db` | Data/archive | Root-level artifacts; Phase B destination `data-archive/` |
| `BUG_REPORT.md` · `UI_CRAWLER_REPORT.md` | Generated | Test-run output (checked in); regenerate, don't hand-edit |

## Rules of engagement (the short version)

1. New-stack work touches **only** `backend/`, `frontend/`, `deploy/`, `docs/`.
2. Never edit `database.py` or the Streamlit app for new-stack work; legacy
   gates (599/0 · 21/21) must stay green after every change.
3. SME (`sme_*` tables) is frozen — read-only, explicit-PK ordering. The new
   stack has ZERO sme_* write endpoints (test-proven); Master Data CRUD is
   deferred to cutover (Phase S6) to prevent dual-write drift.
4. Keep local PG == SQLite (reset with `backend/dual_ci.py`); verify with the
   5-check list in `docs/NEW_STACK_HANDOFF.md` §1b and
   `backend/api/parity_check.py` (5/5).
5. Two deployment surfaces exist until cutover — legacy = root compose,
   new stack = `deploy/`. Don't mix them.
6. **SME engine parity contract:** `frontend/src/sme/engine.ts` and
   `backend/api/sme_engine.py` are byte-equivalent ports proven against
   `backend/api/sme_parity_fixture.json`/`sme_parity_golden.json` (509
   comparisons; `service_tests` suite G + `npm run parity:sme`). Any numeric
   change = change BOTH engines + regenerate the golden in ONE commit.
7. Audit rows are never deleted (`system_audit_log`); tests use delta counts.
8. 🔓 **Freeze temporarily lifted (2026-07-08)** for the feature-gap program —
   still additive, still only `backend/`/`frontend/`/`deploy/`/`docs/`, gates
   green per commit. WhatsApp/email stay parked (Meta token); SME/Man-Hours are
   not touched. Reverts to 🧊 freeze between phases unless the user says otherwise.

## Phase B (cutover day, pre-approved plan)

One commit: legacy app → `legacy/`, root data artifacts → `data-archive/`,
bridge tools → `tools/`, CI paths updated, root becomes the new stack's home.
Until then, this map is the segregation.
