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
   `frontend/`, `deploy/`, `tests/e2e/`, `tests/ai_eval/` at the repo root.

## Ownership

| Path | Owner | Notes |
|---|---|---|
| `legacy/` | Legacy | The complete frozen Streamlit app: `main.py`, `database.py`, `pages_internal/`, `ai/`, `services/`, `pwa/`, `scripts/` (bootstrap/ops), `tests/` (pytest), `.streamlit/`, gates (`bug_check.py`, `test_ui_crawler.py`), legacy deploy surface (`docker-compose.yml`, `Dockerfile.streamlit`, `Dockerfile.fastapi` = RAG sidecar, `docker/`, `host_setup/`), runtime dirs (`uploads/`, `logs/`, `backups/`). Run it with `GI_DB_FILE=../gi_database.db` (the DB stays at root) |
| `backend/` | New stack | FastAPI API (`backend/api/`), SQLAlchemy `models.py` (the schema contract — also verified by legacy bug_check's parity check), Alembic |
| `frontend/` | New stack | React + Vite + AntD SPA; SME TS engine twin in `src/sme/engine.ts`. **Native shells (2026-07-24):** `capacitor.config.ts` (the `android/`/`ios/` projects are GITIGNORED — regenerated per build by `npx cap add`) and `src-tauri/` (**COMMITTED** Tauri v2 scaffold incl. the explicit CSP in `tauri.conf.json` — its `connect-src` must track the API domain) |
| `deploy/` | New stack deploy | `docker-compose.prod.yml`, `Dockerfile.api`/`Dockerfile.web`, nginx, certbot, backup + v2 pipeline scripts — see `docs/DEPLOY.md` |
| `tests/e2e/` | New stack | Playwright suite (**125**) — global-setup loads its throwaway DB via `tools/migration/cutover_migrate.py` |
| `tests/ai_eval/` | New stack | The adversarial RAG audit (Phase 10 Track 4). **Tier 1 is a hard gate** and also runs inside service-test suite CQ; **Tier 2 is a scored artefact, never a gate** — it needs a live model and is stochastic. `cases/*.yaml` are the adversarial prompts; `cases/policy.yaml` pins each role's chapter allowlist as data, so widening one is a signed diff rather than a change nobody saw |
| `frontend/scripts/nav_access_dump.mjs` | New stack | Dumps which roles may open which route, read from `config/nav.tsx` through the TypeScript parser (the approach its sibling `nav_routes_check.mjs` already uses). Feeds the Phase 12 rule-14 script lint. ⚠️ It is a MODEL of `canAccessPath`, used only as a fast pre-flight — the recorder logs every path the browser actually landed on, and that is the oracle it is checked against |
| `tools/make_tutorial_db.py` | New stack ops | **The SYNTHETIC dataset every tutorial is recorded against (ruling P12-0).** Real structure — categories, lining systems, roles, UOMs — with every employee name, material description, SAP code, quantity and date invented. Schema built by `legacy.database.init_db()`, never restated. Deterministic (pinned `ANCHOR`), because ruling Q4 keys a compliance version bump on the narration script's hash and a drifting dataset would change the video without changing that hash. `--collision-check` proves the invented names absent from a real register, read-only and opt-in |
| `tests/video_gen/` | New stack | **Phase 12 tutorial recorder — NOT A GATE.** Its own Playwright config, deliberately outside `tests/e2e/playwright.config.ts` so `npm test` cannot pick it up. `tutorial.spec.ts` is the ONE recorder for the whole catalogue — it performs whatever `steps:` the tracked YAML declares (`harness/steps.ts`), because ~60 bespoke spec files would be 60 places for the harness to drift. It reuses the E2E suite's `global-setup`/`global-teardown`, `setup/auth.setup.ts`, `harness/env.ts` and (by symlink) its `node_modules` — but runs on **its own database `gihub_tutorial_pw` at :8011/:5184** (P12-4), so it cannot collide with the gate. `stack.ts` REFUSES to build on an occupied port: a stale uvicorn answers a health poll instantly, and three tutorials were once recorded through servers nobody knew were running |
| `tools/generate_tutorial.py` · `tools/tutorials/` | New stack ops | **The Phase 12 tutorial pipeline.** The orchestrator (egress guard → HeyGen payload → narration → screencast → ffmpeg composite → WebVTT → manifest) and the tracked narration scripts, which are **the entire HeyGen payload** (ruling P12-1). ⚠️ It was briefly a new top-level `scripts/` directory; question Q11 folded it into `tools/`, where ops tooling belongs, so rule 1 below is unchanged. Renders go to gitignored `docs/tutorials/out/` and are **never committed** — see `tools/make_tutorial_db.py` for why the dataset, not the redaction, is the boundary |
| `tools/` | Bridge + ops | `dual_ci.py` (mirror reload; imports `legacy/database.py` by design), `migrate_sqlite_to_postgres.py` (core copier), `parity_check.py` (SQLite-views ↔ PG-SQL oracle — ⚠️ fails vs the LIVE mirror by design since the Excel injection), `pg_smoke.py`, `migration/cutover_migrate.py` + `migration/README.md` (**the production cutover runbook**), **`pg_excel_sync.py`** (⭐ the preferred sync since 2026-07-27: ONE atomic transaction across all five kinds, Postgres-native `ON CONFLICT` upserts, dry-run by default, PG-only guards — imports `bulk_import.py`'s planners so mapping is never duplicated, and **must never import Pandas**), **`excel_sync.py`** (the older per-kind chain; header-name-driven, `--kinds`, `--sme-reseed`) + **`excel_sync_reconcile.py`** (post-sync ledger reconciliation), **`export_docs_pdf.py`** (manual/SOP → `docs/export/` PDFs). The bridge pieces retire once the legacy app is switched off; the Excel-sync + PDF tools are permanent ops |
| `data-archive/` | Archive | Root-level artifacts moved at Phase B: seed xlsx files, sample PO pdf, `IMG_2397.JPG`, `gi_database.*.bak`, `PyWhatKit_DB.txt`, `demo_seed.db` |
| `gi_database.db` | **Shared bridge — root by design** | The legacy SQLite system of record AND the source for `tools/dual_ci.py` / `parity_check.py` / the final production `cutover_migrate.py` load. Deliberately NOT moved (and never staged — it is live, constantly-modified data) |
| `reports_archive/` | Shared runtime | Deliberately the same directory both stacks' report archives use |
| `GI_Hub_SOP.pdf` · `GI_Hub_User_Manual.pdf` · `SOP.md` · `USER_MANUAL.md` · `build_*_pdf.py` | New stack docs | Served by `backend/api/documents.py` (repo root) and read by `ai/manual_qa.py` — must stay at root |
| `deletion.html` · `privacy_policy_whatsapp.html` · `terms.html` | Shared | Meta/WhatsApp app compliance pages (registered by URL) — do not move |
| `requirements.txt` | Shared | The one venv both Python stacks use; pulls in `backend/requirements.txt` |
| `run_api.sh` | New stack | Local backend launcher (`:8000`) — also what `bin/dev.sh` execs, so backend startup stays single-sourced |
| `bin/dev.sh` | Dev tooling | The unified local stack launcher: `localhost` \| `tunnel` \| `gi` \| `stop` \| `status` \| `logs`. Owns process-group teardown; never touches Postgres or the ROOT cloudflared daemon. Scratch state in gitignored `.dev/` |
| `.github/workflows/` | Shared | `postgres-dual-ci.yml` gates BOTH apps: `legacy/bug_check.py` + `tools/dual_ci.py` + `tools/parity_check.py` + **gi_ai_ro role provisioning** + `backend.api.service_tests` (777) + frontend build — ⚠️ its dual-ci job has NEVER passed on the GitHub runner (bug_check step; failures now surface as public annotations + artifacts). `deploy.yml` (v1, **manual-only** — server not provisioned) · `deploy-v2.yml` (manual cutover pipeline, gated) · **release pipeline (2026-07-25, `v*` tags/dispatch only): `release-desktop.yml` (Tauri dmg/exe/msi — green) + `release-android.yml` (Capacitor APK, JDK 21 required)** — both inject `VITE_API_URL` and attach assets to the tag's GitHub Release |
| `CNCEC_Inventory.xlsx` etc. (root `*.xlsx`) | Operator data | The four live tracking workbooks `tools/excel_sync.py` reads — **gitignored, never committed** (archived snapshots live in `data-archive/`) |
| `PROJECT_HANDOVER.md` | Shared docs | **The fresh-session entry point** — locked architecture rules (PAST), current baselines (PRESENT), next phase (FUTURE) |
| `docs/` · `handoff.md` | Shared docs | New-stack brain (`ARCHITECTURE.md`) + status/migration log · ops PDFs (`docs/export/`; the manual itself lives at the repo root — **one** manual, the 2026-07-26 `docs/USER_MANUAL.md` duplicate was deleted in Phase 8) · **native build/install/release guide (`docs/NATIVE_APPS.md`) + sync-doctor debugging guide (`docs/DEBUGGING.md`)** · handwritten-OCR spec (`docs/features/handwritten-ocr/`) · SME Canon + legacy handoff |

## Rules of engagement (the short version)

1. New-stack work touches **only** `backend/`, `frontend/`, `deploy/`,
   `tests/e2e/`, `tests/ai_eval/`, `tests/video_gen/`, `docs/`. ⚠️
   `tests/video_gen/` was added by the Phase 12 prototype (2026-09-06). The
   prototype also briefly created a top-level `scripts/`, which WIDENED this
   contract rather than extending it; question Q11 moved it into `tools/` and
   the contract is unchanged again.
2. Never edit `legacy/**` for new-stack work; the legacy gate
   (`legacy/bug_check.py` 599/0) must stay green after every change until the
   Streamlit instance is switched off.
3. ~~SME `sme_*` read-only freeze~~ **lifted at cutover (Phase S6, 2026-07-13)**
   — Master Data CRUD lives in `backend/api/sme_master.py` (exact-lock
   {hod, admin}, audited). The rest of the Canon holds: explicit-PK ordering,
   `sme_inventory_seed` never mingles with ERP `inventory`.
4. **PostgreSQL is AHEAD of the frozen SQLite** (Excel injection). After any
   `dual_ci`/cutover reload: re-run `backend/scripts/create_ai_readonly_role.sql`
   AND the Excel sync chain (`tools/excel_sync.py --commit` →
   `excel_sync_reconcile.py --commit` → SME `--sme-reseed` — exact recipe in
   ARCHITECTURE §1). `tools/parity_check.py` is only meaningful on CI or a
   freshly-reloaded mirror.
5. Two deployment surfaces until the legacy switch-off — legacy =
   `legacy/docker-compose.yml`, new stack = `deploy/`. Don't mix them.
6. **SME engine parity contract:** `frontend/src/sme/engine.ts` and
   `backend/api/sme_engine.py` are proven equal against
   `backend/api/sme_parity_fixture.json`/`sme_parity_golden.json` (**1,276
   comparisons**; `service_tests` suite G + `npm run parity:sme`). Any numeric
   change = change BOTH engines + regenerate the golden in ONE commit.
7. **SME material identity is `(Material_Code, SAP_Code)`** (locked
   2026-07-30) — recipes AND stock. Never pool by `Material_Code` alone; a
   multi-part system is four physical drums sharing one code. Details:
   [`PROJECT_HANDOVER.md`](PROJECT_HANDOVER.md).
8. **Frontend tables import `Table` from `src/lib/smartTable.tsx`**, not from
   `'antd'` — that wrapper is what gives every grid sorting + filtering.
9. Audit rows are never deleted (`system_audit_log`); tests use delta counts.

---

## Phase 11 additions (2026-09-05)

| Path | What it is | Tracked? |
|---|---|---|
| `CLAUDE.md` · `.claude/RULES.md` | The four-line project card and the full locked-rule contract an agent reads | ✅ |
| `bin/ci_preflight.sh` · `tools/harness_hygiene.py` | Harness hygiene gate — audits the TEST SUITES, not the app. Runs first in CI. | ✅ |
| `bin/ai_eval_tier2.sh` | The scored half of the AI eval. Needs Ollama; never a gate (P10-7). | ✅ |
| `tools/gen_eval_grid.py` | Regenerates `tests/ai_eval/cases/grid.yaml`. ⚠️ The grid is GENERATED — never hand-edit it. | ✅ |
| `backend/api/ai/trace.py` | Request spans → `ai_traces` (P11-1: Postgres, not a hosted tracer) | ✅ |
| `backend/api/ai/guard.py` · `guard_patterns.yaml` | Input/output boundaries. ⚠️ NOT the security boundary — rule 9 is (P11-4). | ✅ |
| `backend/api/ai/route.py` | Lane policy table, retry classes, fallback policy | ✅ |
| `backend/api/ai/answer_cache.py` | Exact-match answer cache. ⚠️ The key includes the ROLE (P11-7). | ✅ |
| `frontend/src/pages/AiTracesPage.tsx` | The AI Traces panel, mounted as a Console tab AND its own route | ✅ |
| `tests/ai_eval/cases/{grid,fence,jailbreak,nearmiss}.yaml` | The 147-case dataset | ✅ |
| `tests/ai_eval/baseline.json` | Tier 2's recorded baseline. ⚠️ Only moved by `--record`. | ✅ |
| **`fixtures/ocr_ground_truth.yaml`** | Expected values for the OCR fixtures — **tracked, because a score's yardstick must be reviewable in a diff** | ✅ |
| **`fixtures/ocr/`** | The operator's three real documents (a delivery note naming a driver, a register with thirty employees' names) | ❌ **gitignored** |
| **`Trail Files/`** | The operator's drop folder for the same documents | ❌ **gitignored** |
