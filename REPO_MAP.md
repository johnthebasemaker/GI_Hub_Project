# REPO MAP — who owns what in this repository

> **PHASE B EXECUTED 2026-07-13 (cutover day).** The physical restructure this
> file used to *schedule* has happened: the legacy app moved to `legacy/`,
> root data artifacts to `data-archive/`, and the SQLite→PG bridge tools to
> `tools/`. The repo root is now the NEW STACK's home. This file remains the
> boundary contract.

Two applications still coexist until the legacy Streamlit instance is switched
off (users are being pointed at the React app):

1. **LEGACY (being retired):** Python + Streamlit + SQLite, feature-frozen,
   now entirely under `legacy/`. Its regression gate must stay green
   (`.venv/bin/python legacy/bug_check.py` → 599/0).
2. **NEW STACK (production):** React + FastAPI + PostgreSQL — `backend/`,
   `frontend/`, `deploy/`, `tests/e2e/` at the repo root.

## Ownership

| Path | Owner | Notes |
|---|---|---|
| `legacy/` | Legacy | The complete frozen Streamlit app: `main.py`, `database.py`, `pages_internal/`, `ai/`, `services/`, `pwa/`, `scripts/` (bootstrap/ops), `tests/` (pytest), `.streamlit/`, gates (`bug_check.py`, `test_ui_crawler.py`), legacy deploy surface (`docker-compose.yml`, `Dockerfile.streamlit`, `Dockerfile.fastapi` = RAG sidecar, `docker/`, `host_setup/`), runtime dirs (`uploads/`, `logs/`, `backups/`). Run it with `GI_DB_FILE=../gi_database.db` (the DB stays at root) |
| `backend/` | New stack | FastAPI API (`backend/api/`), SQLAlchemy `models.py` (the schema contract — also verified by legacy bug_check's parity check), Alembic |
| `frontend/` | New stack | React + Vite + AntD SPA; SME TS engine twin in `src/sme/engine.ts` |
| `deploy/` | New stack deploy | `docker-compose.prod.yml`, `Dockerfile.api`/`Dockerfile.web`, nginx, certbot, backup + v2 pipeline scripts — see `docs/DEPLOY.md` |
| `tests/e2e/` | New stack | Playwright suite (39) — global-setup loads its throwaway DB via `tools/migration/cutover_migrate.py` |
| `tools/` | Bridge (retiring) | `dual_ci.py` (mirror reload; imports `legacy/database.py` by design), `migrate_sqlite_to_postgres.py` (core copier), `parity_check.py` (SQLite-views ↔ PG-SQL oracle, 5/5), `pg_smoke.py`, `migration/cutover_migrate.py` + `migration/README.md` (**the production cutover runbook**). These retire once the legacy app is switched off |
| `data-archive/` | Archive | Root-level artifacts moved at Phase B: seed xlsx files, sample PO pdf, `IMG_2397.JPG`, `gi_database.*.bak`, `PyWhatKit_DB.txt`, `demo_seed.db` |
| `gi_database.db` | **Shared bridge — root by design** | The legacy SQLite system of record AND the source for `tools/dual_ci.py` / `parity_check.py` / the final production `cutover_migrate.py` load. Deliberately NOT moved (and never staged — it is live, constantly-modified data) |
| `reports_archive/` | Shared runtime | Deliberately the same directory both stacks' report archives use |
| `GI_Hub_SOP.pdf` · `GI_Hub_User_Manual.pdf` · `SOP.md` · `USER_MANUAL.md` · `build_*_pdf.py` | New stack docs | Served by `backend/api/documents.py` (repo root) and read by `ai/manual_qa.py` — must stay at root |
| `deletion.html` · `privacy_policy_whatsapp.html` · `terms.html` | Shared | Meta/WhatsApp app compliance pages (registered by URL) — do not move |
| `requirements.txt` | Shared | The one venv both Python stacks use; pulls in `backend/requirements.txt` |
| `run_api.sh` | New stack | Local backend launcher (`:8000`) |
| `.github/workflows/postgres-dual-ci.yml` | Shared | One workflow gating BOTH apps: `legacy/bug_check.py` + `tools/dual_ci.py` + `tools/parity_check.py` + `backend.api.service_tests` + frontend build |
| `docs/` · `handoff.md` | Shared docs | New-stack brain (`ARCHITECTURE.md`) + status/migration log · SME Canon + legacy handoff |

## Rules of engagement (the short version)

1. New-stack work touches **only** `backend/`, `frontend/`, `deploy/`,
   `tests/e2e/`, `docs/`.
2. Never edit `legacy/**` for new-stack work; the legacy gate
   (`legacy/bug_check.py` 599/0) must stay green after every change until the
   Streamlit instance is switched off.
3. ~~SME `sme_*` read-only freeze~~ **lifted at cutover (Phase S6, 2026-07-13)**
   — Master Data CRUD lives in `backend/api/sme_master.py` (exact-lock
   {hod, admin}, audited). The rest of the Canon holds: explicit-PK ordering,
   `sme_inventory_seed` never mingles with ERP `inventory`.
4. Keep local PG == SQLite while legacy still runs (reset with
   `tools/dual_ci.py`); verify with `tools/parity_check.py` (5/5). Re-run
   `backend/scripts/create_ai_readonly_role.sql` after every reload.
5. Two deployment surfaces until the legacy switch-off — legacy =
   `legacy/docker-compose.yml`, new stack = `deploy/`. Don't mix them.
6. **SME engine parity contract:** `frontend/src/sme/engine.ts` and
   `backend/api/sme_engine.py` are proven equal against
   `backend/api/sme_parity_fixture.json`/`sme_parity_golden.json` (509
   comparisons; `service_tests` suite G + `npm run parity:sme`). Any numeric
   change = change BOTH engines + regenerate the golden in ONE commit.
7. Audit rows are never deleted (`system_audit_log`); tests use delta counts.
