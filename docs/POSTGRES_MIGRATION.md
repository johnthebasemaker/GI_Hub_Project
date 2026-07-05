# PostgreSQL Migration — Phase 0 (Plan Only, No Code)

**Status:** PLANNING. Nothing in this document is implemented. The app runs on
SQLite today and stays on SQLite until we deliberately execute the phases below.

**Goal:** Make GI Hub run on **PostgreSQL** (localhost now, server later) for real
multi-user concurrency, **without breaking a single feature** and with an
**instant rollback** at every step.

---

## 1. Why move (and when you actually need to)

SQLite + WAL comfortably handles ~**10–25 concurrent writers**. Past that, writes
serialize and users see lag/locks. PostgreSQL has no such ceiling and gives real
connection pooling, row-level locking, and concurrent writes.

**Honest take:** if you're below ~10–15 simultaneous active users today, this is
*future-proofing*, not an emergency. The plan below lets us do it **gradually and
safely** rather than as a risky big-bang rewrite.

> Note: moving to PostgreSQL does **not** by itself improve the *error display*
> you asked about — that's already handled by the new error boundary
> (`error_handling.py` + `logs/app_errors.log`). The two are independent.

---

## 2. The big advantage we already have

**Every database call funnels through one factory:** `database.get_connection()`
(470 call sites) and `DB_FILE` is already env-driven (`GI_DB_FILE`). That single
chokepoint is what makes a safe migration realistic — we change the plumbing in
*one* place and adapt the dialect-isms behind helpers, instead of touching 470
sites by hand.

Recommended bridge (your stack, confirmed sound):
- **PostgreSQL** server (local now, Hetzner later — a `postgres` service in the
  existing `docker-compose.yml`).
- **SQLAlchemy** Engine + **`psycopg2-binary`** driver, with a **QueuePool** so
  the Streamlit threads + the WhatsApp worker thread share pooled connections.
- Selection via a new `DATABASE_URL` env var:
  - `sqlite:///gi_database.db` (default — unchanged behavior, the demo, tests)
  - `postgresql+psycopg2://user:pass@host/gihub` (server)

---

## 3. Inventory of SQLite-isms (the real work)

Counts from the current tree (2026-06-30):

| Pattern | Count | Why Postgres cares | How we neutralize it |
|---|---:|---|---|
| `get_connection()` callers | 470 | — | ✅ single factory; swap internals once |
| `?` placeholders (`execute`, `read_sql`) | ~470 / 261 read_sql | psycopg2 uses `%s`; SQLAlchemy uses `:name` | adopt SQLAlchemy `text()` + named params, OR a paramstyle shim; migrate file-by-file with tests green |
| `PRAGMA table_info(...)` self-heal | 88 (was 94) | no `PRAGMA` in PG | one `column_exists(table, col)` helper over `information_schema.columns` — 7 `init_db()` sites now routed (1 from Phase 2 + 6 this run) |
| `CURRENT_TIMESTAMP` defaults | 113 | standard SQL | ✅ works as-is |
| `AUTOINCREMENT` PKs | 57 | PG syntax differs | `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL/BIGSERIAL` (or `GENERATED … IDENTITY`) via a DDL dialect branch |
| `rowid` references | 52 | PG has no `rowid` | order by an explicit PK (SME views already do this — R20.5.1); audit the rest (many are comments) |
| `INSERT OR IGNORE / REPLACE` | 41 | not PG syntax | `ON CONFLICT (cols) DO NOTHING / DO UPDATE` via an `upsert()` helper |
| `ON CONFLICT …` | 18 | PG needs an explicit conflict target | add the target columns/constraint name |
| `date('now')` / `DATE('now')` / `datetime('now')` | 30 | SQLite funcs | `CURRENT_DATE` / `NOW()` |
| `julianday(...)` date math | 4 | SQLite func | PG date subtraction / `EXTRACT(EPOCH …)` |
| direct `sqlite3.connect(...)` | 21 | bypass the factory | route through the engine (scripts + `bug_check` fixtures) |
| `to_sql(...)` | 2 | works via engine | pass the SQLAlchemy engine |

Clean bill on a few things that often bite: **no `GROUP_CONCAT`, no `GLOB`, no
`BOOLEAN` columns** found.

### Type-affinity caveat (the subtle one)
SQLite is loosely typed; PostgreSQL is strict. Some columns are stored as TEXT but
compared/sorted as numbers (e.g. `Lining_System_Code`). On PG these need explicit
`CAST(... AS INTEGER)`. These won't show up in a grep — they surface as runtime
errors, which is exactly what the **dual-backend CI** phase is designed to catch.

---

## 4. Catches you asked about — and how to "rid them off" with zero feature impact

1. **`?` placeholders everywhere (biggest).** Don't sed-replace blindly.
   *Fix:* introduce SQLAlchemy and migrate module-by-module to `text()` + named
   params, keeping the 572-check suite green after each module. Reversible per file.
2. **PRAGMA-based self-healing schema.** The app auto-adds missing columns via
   `PRAGMA table_info`. *Fix:* one portable `column_exists()` helper; the 111 call
   sites become helper calls. Behavior identical on SQLite.
3. **`rowid` ordering** returns empty/garbage on PG. *Fix:* order by real PKs
   (pattern already established for SME views). Audit the 52 hits (many are prose).
4. **`AUTOINCREMENT` / identity.** *Fix:* a small DDL dialect branch in `init_db`
   (or SQLAlchemy `Table` metadata) emits `SERIAL` on PG, `AUTOINCREMENT` on SQLite.
5. **SQLite date funcs (`date('now')`, `julianday`).** *Fix:* `now_sql()` /
   `date_diff_days()` helpers that emit the right dialect.
6. **Strict typing.** *Fix:* add `CAST`s where TEXT columns are used numerically;
   caught by dual-CI before any user sees it.
7. **Streamlit-Cloud demo trick breaks.** The public demo currently works by
   *committing the `.db` file*. That's impossible with a server DB. *Fix:* keep
   the demo on **SQLite** (`DATABASE_URL=sqlite:///…`) and use Postgres only for
   the real server. Dual-support means **both keep working** — no either/or.
8. **Concurrency is the real driver.** If you're not hitting the SQLite ceiling,
   we can stage this slowly with no urgency and no risk.

**Guiding principle:** at every step the app must still run on SQLite with all
**572 bug_check / 21 crawler** checks green. Postgres support is *added alongside*,
never *swapped in*, until the dual-CI phase proves parity.

---

## 5. Phased plan (each phase independently shippable + reversible)

- **Phase 0 — this document.** Inventory + decision. No code. ✅
- **Phase 1 — Engine seam. ✅ DONE.** Added SQLAlchemy + `psycopg2-binary` to
  requirements; new `get_database_url()` (DATABASE_URL wins, else derives a
  SQLite URL from DB_FILE) and `get_engine()` (lazy SQLAlchemy import, pooled).
  `get_connection()` is **untouched** and remains the runtime path — **zero
  behavior change**, verified by a regression check (`get_connection()` still
  returns `sqlite3.Connection`). 577 bug_check / 21 crawler green on SQLite.
- **Phase 2 — Portability helpers. ✅ HELPERS DONE.** Added `db_dialect()`,
  `column_exists()`, `now_sql()`, `days_ago_sql()`, `date_diff_days_sql()` —
  each emits *identical* SQLite behavior and the correct Postgres form, with a
  regression check. First self-heal site (`stock_adjustments.Lot_Number`) routed
  through `column_exists()` to prove the pattern. **Remaining ~185 legacy sites
  are migrated incrementally and validated against real Postgres under Phase 4
  dual-CI** (the safe way — never a blind sed). Still SQLite in prod; tests green.
- **Phase 3 — Param style. 🔶 IN PROGRESS.** Route the ~185 legacy
  `PRAGMA table_info` self-heal sites through `column_exists()` (one `init_db()`
  call site at a time), then migrate raw `?` SQL to SQLAlchemy `text()` + named
  params, module by module, suite green after each. The largest mechanical phase.
  **Increment 1 done (this run):** 6 `PRAGMA table_info` self-heal call sites in
  `init_db()` → `column_exists()` — see Run Log below.
- **Phase 4 — Dual-backend CI.** Spin a throwaway Postgres (docker) and run the
  **same 572 checks** against it until green. This is where type-affinity and any
  missed dialect-ism get caught — *before* production.
- **Phase 5 — Data migration + cutover.** `pgloader` copies `gi_database.db` →
  Postgres. Flip `DATABASE_URL`. **Rollback = flip it back** (SQLite file is
  untouched).
- **Phase 6 — Server.** Add a `postgres` service to `docker-compose.yml`
  (volume-backed, backed up); point the app's `DATABASE_URL` at it.

**Rollback at any time:** because SQLite stays the default and the `.db` file is
never destroyed, reverting is a single env-var flip until Phase 5 cutover — and
even then the pre-cutover `.db` is a full snapshot.

---

## 6. What Phase 0 delivers / what's next

- ✅ This inventory + risk register + reversible plan.
- ✅ **Phase 1 delivered** (engine seam, zero behavior change — see above).
- ⏭️ **Next decision point:** **Phase 2** (portability helpers — `column_exists`,
  `upsert`, `now_sql`, `date_diff_days`) — still SQLite in prod, tests green
  throughout. Green-light when ready.

---

## 7. Progress Ledger (single source of truth)

> ### 🤖 Coordination protocol — READ BEFORE ANY MIGRATION WORK
> **Two workers touch this migration:** the *interactive Claude Code session* (a
> human + Claude in this repo) and the *scheduled routine* (`GI-Hub autonomous`,
> runs Mon/Wed/Fri on the cloud → PR on a `claude/*` branch, laptop-off). They
> stay in sync through **this ledger + the §8 Run Log + a one-line `🤖 Migration
> status` pointer in `handoff.md`** — nothing else.
>
> **Both workers, every time, in order:**
> 1. **Read** this §7 ledger, the last §8 Run Log entry, and `git log --oneline -30`.
> 2. **Verify against reality** — re-grep the remaining-counts below; if they
>    disagree with the code, trust the code and fix the table. Never redo a
>    site that's already converted.
> 3. Do **one bounded increment** (≤~10 sites) per the "Next action".
> 4. **Update** this ledger + append a §8 Run Log entry + refresh the `handoff.md`
>    pointer — *in the same change* as the code.
> 5. **Analyse & explain the diff**, then push (routine → PR, never merge;
>    interactive → commit after showing the human).
>
> **Files that carry the shared state (keep all three in sync):**
> `docs/POSTGRES_MIGRATION.md` (§7 ledger + §8 log) · `handoff.md` (the `🤖
> Migration status` line) · `MEMORY.md`/AI-memory holds only the *decisions*, not
> progress. The routine PR only ever touches `claude/*` so it can't collide with
> direct-to-main commits — worst case is a rebase.
>
> **`FRONTEND_GO: NO`** — the FastAPI + React (API-first, incremental) work is
> **gated**. No worker starts it until Postgres cutover (Phase 5) is done *and* a
> human flips this to `FRONTEND_GO: YES (approved by <name>)`. It gets its own
> phased plan + its own routine when that happens.

| Phase | Status | Notes |
|---|---|---|
| 0 — Inventory/plan | ✅ Done | No code. |
| 1 — Engine seam | ✅ Done | `get_database_url()` / `get_engine()`; `get_connection()` untouched. |
| 2 — Portability helpers | ✅ Helpers done | `db_dialect`, `column_exists`, `now_sql`, `days_ago_sql`, `date_diff_days_sql` added; 1 proof-of-pattern site migrated. |
| 3 — Portable SQL (route ~185 legacy sites through Phase-2 helpers + named params) | 🔶 In progress | Sub-phase A (`PRAGMA table_info` → `column_exists()` in `init_db()`) started. 10/~55 `init_db()` self-heal call sites done (1 Phase-2 + 6 routine increment 1 + 3 interactive increment 2). Param-style (`?` → named params) not yet started. |
| 4 — Dual-backend CI | 🔶 Data-layer harness done | `backend/dual_ci.py` (migrate + per-view + semantic parity) + `.github/workflows/postgres-dual-ci.yml` (postgres:16 service → runs on push, no local Docker). Validates schema/types/data/views on real PG. Full *behavioural* CI (bug_check on PG) still needs `get_connection()` wired to the engine. |
| 5 — Cutover | 🔶 Copy script written + dry-run-validated | `backend/migrate_sqlite_to_postgres.py` (schema from models.py, ledger `id:=rowid`, typed coercion, per-table parity, view recreation). Validated SQLite→SQLite (real `gi_database.db` → PARITY OK). Awaits a live Postgres run + Phase-4 dual-CI. |
| 6 — Server | 🔶 Compose service added | `postgres` service + `pg-data` volume in `docker-compose.yml` (migration target; app still on SQLite). |

**Remaining-counts snapshot** (repo-wide, `grep -rn <pattern> --include=*.py . \| wc -l`, run at the start of each session and trusted over this table if they disagree):

| Pattern | Count |
|---|---:|
| `PRAGMA table_info(...)` | 85 |
| `execute(...?...)` in `database.py` (single-line regex, undercounts) | 9 |
| `date('now'` | 17 |
| `julianday` | 8 |
| `rowid` in SQL (ORDER BY / SELECT col) — breaks in PG | 8 remaining (was 9; `system_settings` group fixed) |

**⏸️ ROUTINE PAUSED (2026-07-01).** Per user direction, the autonomous `GI-Hub autonomous` routine is **paused**; Postgres is now **built interactively in this repo on `main`**. The coordination box below still applies if the routine is ever resumed, but for now there is a single worker. `FRONTEND_GO` stays **NO** (backend schema prep is allowed; FastAPI/React code is not).

**🔎 Rowid audit (Postgres has no `rowid`).** 4 tables had no explicit PK and relied on SQLite's implicit rowid: `consumption`, `receipts`, `returns`, `system_settings`. **`system_settings` migrated** — given an explicit `id INTEGER PRIMARY KEY` (rowid→id copy) and its 4 SQL sites fixed (`locations`/`types` compat views → `MIN(id)`; HOD dropdown editor `SELECT id` + delete key). **Remaining rowid SQL sites (all on `receipts`), deferred to the Phase-5 cutover copy-script** (adding a PK to the frozen identity-math ledger tables is a reviewed step, not a bundled sweep):
- `database.py:3342` `SELECT r.rowid AS receipt_id`
- `database.py:5970` `SELECT r.rowid AS rid` · `:5980` `ORDER BY r.rowid DESC`
- `database.py:6011` `ORDER BY rowid DESC` (Bin_Location lookup)
- `database.py:6737` `ORDER BY r.Date DESC, r.rowid DESC`
- (`consumption`/`returns` have no rowid SQL usage; they only need a SERIAL `id` created at cutover.)
- `cur.lastrowid` (~25 sites) are the DBAPI cursor attribute, NOT SQL — they become `RETURNING id` in the SQLAlchemy path, tracked separately.

**📐 `backend/models.py`** — SQLAlchemy 2.0 Declarative schema for the future FastAPI+PostgreSQL backend, auto-generated from the authoritative live schema (64 tables + 14 documented views). SME compat views kept as views (Canon rule 1); the 4 ledger tables carry a SERIAL `id`. Not wired to runtime. Guarded by a `bug_check` schema-parity test (`models.py` ⊇ live schema; only the ledger `id`s may be model-only).

**Next action:** (interactive) Continue Phase 3 sub-phase A — pick the next ~10 `PRAGMA table_info` self-heal call sites in `database.py::init_db()` (grep `PRAGMA table_info` in database.py, skip any already converted) and route them through `column_exists()`, following the exact pattern used for `stock_adjustments.Lot_Number` (Phase 2), the 6 sites converted in routine increment 1 (`returnable_items`, `pending_users`, `whatsapp_queue`, `employees`, `supervisor_material_request_items`), and the 3 blocks converted in interactive increment 2 (`pending_receipts.rejection_reason`, plus the `receipts`/`pending_receipts` DN/PO/Warehouse trace-ref loops).

> ⚠️ **The easy, unambiguously-safe single-column sites in `init_db()` are now largely exhausted.** What remains splits into two harder buckets, each needing a *closer read, not a batch swap*: (a) **sensitive** blocks — `users`/`pending_users` RBAC table-rebuilds, cost fields (`inventory.Unit_Cost`, `receipts.Unit_Cost`), and EOD/approval columns (`consumption."Approved By"`, the `Approved`-drop probe); and (b) **multi-column-reuse** blocks where a single `PRAGMA` read feeds a large column loop (`pr_master` 520/1375, `receipts` 787, `pending_receipts` 803, the `EXTENDED_ISSUE_COLS` loop 753/756, the `rejected_issues_archive` set-difference at 1709). The (b) blocks are mechanically convertible to a per-column `column_exists()` loop (the `returnable_items` precedent) but trade 1 PRAGMA for N calls — fine at init but review the diff. Triage (a) individually. **Continue avoiding**: `users`/`pending_users` login-adjacent RBAC columns beyond what's already done, and any site inside `receipts`/`consumption`/`returns`/`pending_issues`/`pending_receipts`/`pr_master` self-heal blocks that sit directly in the identity-math or EOD-commit code paths — those need a closer read (not a mechanical swap) because of the Section-2 guardrails, so triage them individually rather than batch-converting. Once all `PRAGMA table_info` self-heal sites in `init_db()` are converted, move to sub-phase B (`date('now')`/`julianday` → `now_sql()`/`date_diff_days_sql()`), then sub-phase C (`?` → named params).

---

## 8. Run Log

### 2026-07-05 · actor=interactive · branch=`main` · 🔒 Security + UX hardening — site scoping · token refresh · nav badges (+ segregation Phase A)
Four user-approved slices (commits `b85a00d` · `16b799c` · `a28d9a1` · `9cf48b4`):
- **Segregation Phase A (repo).** `REPO_MAP.md` = the monorepo boundary contract (legacy /
  new-stack / shared-bridge / archive ownership per top-level path, both deploy surfaces,
  golden rules). New-stack Python deps split to `backend/requirements.txt`, included by the
  root file via `-r` (same venv/CI; `deploy/Dockerfile.api` copies both). **Physical Phase B
  (moves into `legacy/` etc.) is scheduled for CUTOVER DAY — nothing moved.**
- **Site-scoped reads (THE Tier-2 multi-tenancy gap — closed).** Below logistics (level 3),
  every read is pinned to the user's own `Site_ID`: forced filters on CRUD list/get, stock
  views, meta aggregates, HOD queues/burn-rate/PRs, receiving, all six reports (expiring +
  POs gained site filters), SME site views. Explicit foreign `?site_id=` → 403; cross-site
  get-one → 404 (no id-existence leak); site-less scoped users fail CLOSED; `/stock/live`
  (cross-site aggregate) → 403 below level 3. Cross-site approve/reject/PR-create/submit
  also guarded. FE hides the site picker + global stock tab for scoped users.
- **Access/refresh token split + silent session refresh.** 15-min access JWT + rotating
  7-day refresh token in an httpOnly SameSite=Lax cookie, hashed server-side in the new
  `auth_sessions` table (alembic `fd225ce87708`; new-stack-only — dual_ci leaves it empty,
  documented in the model). Rotation reuse-detection revokes the whole session family;
  logout / admin password-reset / user-delete revoke server-side. Axios client does
  single-flight silent refresh + replay on 401 → a shift never loses form state; only a
  failed refresh logs out (with a "session expired" toast).
- **Sidebar work-queue badges.** `GET /meta/work-queues` (role- + site-aware, one round
  trip): approvals (≥hod), in-transit DNs, pending SMRs, open warehouse assignments →
  gold count badges on the nav items, focus-refetch + 60s visible poll.
- **Verified:** service_tests **52 → 78/78** (new suites: C site-scoping, D token refresh);
  dual_ci PASS (65-table metadata handled); parity 5/5; `bug_check` **599/0**; crawler
  **21/21**; FE build green. Live-verified in the browser: worker(CNCEC) isolation,
  httpOnly cookie invisible to JS, corrupted-token reload silently recovers, hod badge=26.
- **WhatsApp (no code, by instruction):** user is running Meta Business Verification; the
  legacy worker already supports `WHATSAPP_PROVIDER=meta`. New-stack port waits for the
  permanent token.

### 2026-07-05 · actor=interactive · branch=`main` · 🎨 UI/UX overhaul — "Navy vault, gold key" brand theme + animation layer (FE-only)
User-approved visual overhaul of the React SPA (5 commits, `4d98b05`…`a659b12`). **Pure presentation layer — no API / hook / backend / endpoint touched;** the functional data layer is unchanged and was verified live.
- **Theme foundation (`frontend/src/theme/`).** The legacy GI palette (root `config.py`: navy `#003366` / gold `#D4AF37` + dark surfaces + status colors) becomes the single source of truth in `tokens.ts`; `themes.ts` = three AntD `ThemeConfig`s (`darkTheme` flagship · `lightTheme` amber-accent `#B45309` for contrast on white · `siderTheme` always-navy rail) on `theme.darkAlgorithm`/`defaultAlgorithm`; `ThemeContext` = **dark-first** default (ignores OS pref), localStorage-persisted, header sun/moon toggle. Restyles all 24 pages at the `ConfigProvider` token level — almost no per-page edits.
- **Branded shell.** Always-navy sider rail in both modes (gold wordmark + gold active-item bar), sticky `backdrop-blur` header (pulsing API-health dot replaces the green Tag; theme toggle), gold-primary buttons with navy text. Login → full glassmorphic navy screen with a staged entrance (stays dark regardless of the in-app toggle — flagship first impression).
- **Animation layer (subtle-premium: 120–200 ms ease-out, no bounce — user's explicit rule).** Keyed route fade+rise, branded `Skeleton` first-loads (BrowseTable + lazy route chunks), sticky data-grid headers, row-landing fade, pill-shaped tags, one gentle bell-ring on unread↑, and a rAF `useCountUp` hook — all behind a global `prefers-reduced-motion` kill switch.
- **Page polish.** New `KpiCard` (tinted icon chip + count-up value + gold hairline + hover lift) on the Dashboard with a stagger cascade and status-aware tinting (expiring-lots number goes red only when > 0); last hardcoded colors → theme tokens; the 2FA QR keeps a white quiet-zone frame so it stays scannable on the dark theme.
- **Sidebar fix (`040dc5e`).** A Phase-2 regression: `overflow-y:auto` on the antd `Sider` forced `overflow-x` visible→auto (CSS spec) → clipped the zero-width reopen trigger that hangs outside the collapsed rail (proven via hit-testing); and `breakpoint=lg` collapsed the rail on desktop. Fix: scroll on an inner `.gi-sider-scroll` wrapper (the `<aside>` stays `overflow:visible`), collapse only below `md`. Verified at 900 px (full rail) and 375 px (visible, hit-testable trigger).
- **Verified:** `npm run build` green (tsc + vite); console clean on a fresh dev server; walked login → dashboard → records in **dark + light** at desktop + mobile widths; KPIs/tables load live from the API. Zero backend delta → old-app gates (`bug_check` 599/0, crawler 21/21) unaffected; `database.py`/Streamlit untouched.

### 2026-07-04 · actor=interactive · branch=`main` · 🛠️ Tier-1 quick wins (from the architecture review) — 4 small real gaps
Four low-risk hardening items the user green-lit after the review (3 commits).
- **React error boundary (FE).** `ErrorBoundary` class wraps `<App/>` in `main.tsx` → a render-time crash shows a recoverable antd `Result` (Reload / Go-to-dashboard) instead of a white screen. **Verified:** temporarily threw in LoginPage → the boundary caught it + rendered the fallback; reverted → normal.
- **CI frontend build (infra).** `postgres-dual-ci.yml` gained a `frontend-build` job (Node 20, `npm ci` + `tsc -b && vite build`) + `frontend/**` in the path triggers — TS/build regressions now caught in CI (were local-only).
- **Rate-limit public auth (BE/security).** New dependency-free `ratelimit.py` (in-memory FastAPI dependency, keyed by nginx `X-Real-IP` → TCP-peer fallback): `/auth/login` + `/auth/login/2fa` = 10/min, `/auth/register` = 5/min → 429 + Retry-After past the cap. Per-process store (N workers → N× the cap; Redis for a hard cross-worker limit — noted). **Verified:** service_tests +2 (12 rapid logins from a test `X-Real-IP` → 401 under cap, 429 past it, isolated from the suite's real logins).
- **Alembic migrations (BE/DB — the biggest gap).** Post-cutover schema-evolution path for the Postgres system-of-record. `backend/alembic.ini` + `alembic/env.py` (`target_metadata = models.Base.metadata` = the 64 tables; views excluded — they're `dual_ci`'s job; `$DATABASE_URL` normalised to psycopg2) + autogenerated **baseline** migration (all 64 `create_table`). **Verified:** `upgrade head` on an empty DB → 64 tables + stamped, idempotent; **`dual_ci` schema matches the baseline exactly** (`alembic check` on a dual_ci'd DB → "No new upgrade operations detected"). Cutover flow: `dual_ci` load → `alembic stamp head` → future changes via `revision --autogenerate` + `upgrade head` (`backend/alembic/README.md`; runbook §4 updated). `alembic>=1.13` added to requirements.
- **Verified overall:** service_tests **54/0**; dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**, build green. Alembic tested on throwaway DBs only — local `gihub` untouched. `database.py`/Streamlit untouched.

### 2026-07-04 · actor=interactive · branch=`main` · 🚢 Deploy kit (turnkey, no deploy) — new-stack Docker/Nginx/Certbot
- User chose "build the deploy kit" (cutover option #1 — prepare everything, zero prod contact). **Nothing run against a server.**
- **`deploy/`** — a self-contained production kit for the **new** React/FastAPI/Postgres stack (separate from the repo-root Streamlit `docker-compose.yml`): `docker-compose.prod.yml` (db postgres:16 · api FastAPI internal · web nginx `:80/:443` · certbot auto-renew — only `web` binds host ports), `Dockerfile.api` (multi-stage venv, uvicorn 4 workers, `GI_ENV=production` → fail-fast on weak `JWT_SECRET`), `Dockerfile.web` (node build → nginx serving the Vite bundle), `nginx.conf` (SPA fallback + `/api/`→`api:8000/` **prefix-stripping** proxy [matches the axios `/api` baseURL + Vite dev rewrite] + TLS + ACME webroot, envsubst `${DOMAIN}`), `init-letsencrypt.sh` (dummy-cert→real-cert bootstrap), `.env.example` (gitignored `.env`).
- **`docs/DEPLOY.md`** — full runbook: provision → secrets → `init-letsencrypt.sh` → `up -d` → **one-time SQLite→PG migration via `dual_ci` (WIPES the target — pre-go-live only)** → verify (`/api/health`, in-browser, `service_tests` 52/52) → cutover (freeze Streamlit → final re-migrate → point users) → ops (logs/renew/pg_dump backups) → rollback (Streamlit + `gi_database.db` untouched). Also lists what's NOT ported (WhatsApp/email/LLM/CV) + the non-site-scoped-reads caveat.
- **Small code change:** `config.py` — `CORS_ORIGINS` now reads a comma-separated `CORS_ORIGINS` env (single-origin nginx needs none; dev defaults unchanged). `.dockerignore` += `**/node_modules/` + `frontend/dist/`.
- **Verified (locally, no Docker on either machine):** compose YAML valid, `init-letsencrypt.sh` `bash -n` clean, CORS env-override + default both import, `service_tests` **52/0**, `bug_check` **599/0**, crawler 21/21, build green. Docker image builds happen on the server (fresh clone). `database.py`/Streamlit untouched.

### 2026-07-04 · actor=interactive · branch=`main` · 🔔 More notification events (staging → HOD · approve/reject → submitter)
- Extends the notification bell to close the data-entry feedback loop. **Backend-only** — the bell already renders any notification.
- **Wired at the router layer** (NOT in `ledger.py` — that would be circular, since `notifications` imports `_MD` from `ledger`): `entry.py` fires `entry_staged` (recipient_role=hod + site) after each of the 4 stages (receipt/issue/return/adjustment) so the site HOD sees waiting work; `hod.py` fires `entry_approved` (success) / `entry_rejected` (warning) to the **original submitter** (`recipient_user`) on approve/reject. The submitter is resolved per kind via `_SUBMITTER_COL` — issues=`Issued_By`, returns/adjustments=`submitted_by`, **receipts=None** (`stage_receipt` doesn't store a submitter on the row → the submitter notification is gracefully skipped for receipts).
- **Verified live (PG):** worker stages receipt → HOD@CNCEC sees "Receipt awaiting approval"; worker stages issue → HOD approves → worker sees "Your issue was approved" (success); worker stages return → HOD rejects → worker sees "Your return was rejected: …" (warning). `service_tests` +2 rolled-back checks for `_submitter` (return-submitter resolved; receipts→None) → **52/52 PASS**.
- **Verified:** service_tests **52/0**; dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**, build green. `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🙋 User registration + approval (self-service onboarding)
- **Gap closed:** the new app only *logged in* existing users; admins had to hand-create every account. Now there's a self-service Request-Access → admin-approval onboarding flow (`pending_users`).
- **Backend:** `POST /auth/register` (**public**, `auth.py`) bcrypt-hashes the password into `pending_users` (status `pending`). Guards: username not already in `users` (409), not already pending (409, revives a rejected row via upsert), password ≥6 (422), and the requested role **cannot be admin** (422 — no self-elevation). Admin side (`admin.py`, level 4): `GET /admin/pending-users` (no `password_hash`), `POST /admin/pending-users/{id}/approve` (copies the row into `users` — role/warehouse overridable — carrying the bcrypt hash, marks pending `approved`, audits `APPROVE_USER`), `POST .../reject` (marks `rejected`, audits `REJECT_USER`). **+4 endpoints (89 paths).**
- **Verified live (PG):** register→201; guards existing-username-409, admin-role-422, short-pw-422, dup-pending-409; admin list (no secret leak), worker→403; approve with role override → user created → **logs in** (bcrypt carried over); re-approve→409, re-register-existing→409. **In-browser:** LoginPage **Request access** form (role defaulted — fixed a React form-reuse bug with distinct `key`s) → submit → "await approval"; admin **Access Requests** page → Approve modal (role/warehouse) → user created (`ui_reg` logs in as store_keeper, pending row `approved`). Console clean.
- **Frontend:** LoginPage register mode, `PendingUsersPage` (+ **Admin → Access Requests** nav, lazy route), `useRegister`/`usePendingUsers`/`useApprovePending`/`useRejectPending`.
- **Tests:** `service_tests.py` +6 (admin-role-422, existing-409, short-pw-422, worker-403, admin-list-200, approve-404) → **50/50 PASS**.
- **Verified:** service_tests **50/0**; dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**, build green. Test users PG-only → reset (8 users). `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🚀 Cutover prep (non-deploy) — JWT_SECRET hardening + frontend code-split
- Two ship-readiness items; **no deploy** (Hetzner stays parked) and React-primary/cutover stays the user's call.
- **JWT_SECRET hardening** (`config.py::jwt_secret()` + `is_production()`): the dev default was insecure (22 bytes → PyJWT `InsecureKeyLengthWarning`). Now — in **production** (`GI_ENV=production`) a missing / too-short (<32) / dev-default key **raises at import (fail-fast)**, so the app refuses to boot with a weak signing key; in **dev** it falls back to a long (56-char) obvious placeholder → no warning, no setup. `auth.py` resolves `JWT_SECRET = jwt_secret()` at import. **Deploy note:** production MUST set a strong `JWT_SECRET`.
- **Frontend code-split:** `App.tsx` now `React.lazy()`-loads every route page (LoginPage stays eager); `AppLayout` wraps `<Outlet>` in `<Suspense>` (a `Spin` fallback) so the sidebar stays put while a page chunk streams. **Initial bundle 1,354 kB → 288 kB (gzip 422 → 91 kB)** — each page + heavy antd widget (table/date-picker/select) is its own on-demand chunk; the >500 kB warning is gone.
- **Verified:** `jwt_secret()` — dev ≥32 chars/no warning; production without/short/dev-default → RuntimeError; production + strong → OK. **In-browser (admin):** navigated Stock/Reports/Users/Security/Dashboard — each lazy chunk loads + renders (Reports 6 cards, Dashboard 4 KPIs), sidebar persists, console clean. `service_tests` +4 JWT-hardening checks → **44/44 PASS**. dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**. `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 📊 Reports — downloadable Excel / PDF / CSV exports
- **Gap closed:** exportable reports were the biggest capability the old app still had over the new stack. Now the live data downloads in three formats.
- **Backend** (`reports.py`, gated level ≥ 2 = hod/logistics/admin): `GET /reports` (list + each report's filters) and `GET /reports/{key}?format=xlsx|pdf|csv`. Six reports — **stock** (per site), **expiring** (≤N days), **consumption** (last N days), **receipts** (last N days), **purchase-orders** (by status), **inventory** (master). Each is one query (reuses `SQL_SITE_STOCK`/`SQL_EXPIRING`); one row-set → any format via `to_xlsx` (openpyxl, navy header, freeze panes, autosized cols), `to_pdf` (fpdf landscape, branded header, latin-1 safe), `to_csv` (UTF-8 BOM). Served as `StreamingResponse` with a `Content-Disposition` filename. **+2 endpoints (85 paths).** Read-only — no writes.
- **Verified live (PG):** `/reports` lists 6; stock.xlsx = valid zip (PK), sheet "Current Stock by Site", 51 rows (matches the by-site view); stock.pdf = `%PDF`; consumption.csv has a BOM header; inventory.xlsx honours `site_id=CNCEC` (291 rows, all CNCEC); guards worker→**403**, unknown→**404**, bad-format→**400**, no-token→**401**. **In-browser (admin):** Reports page (6 cards + per-report filters + Excel/PDF/CSV buttons) → clicking Excel round-trips `GET /reports/stock?format=xlsx` **200** + a "downloaded" toast; console clean.
- **Frontend:** `ReportsPage` (card per report, site/days/status filters, authenticated blob download via axios `responseType:'blob'` → object-URL save) + a **Reports** nav group (level ≥ 2) + `useReports`/`downloadReport` hooks.
- **Tests:** `service_tests.py` +5 report checks (worker-403, list, xlsx content-type, 404, 400) → **40/40 PASS**. Gated in CI.
- **Verified:** service_tests **40/0**; dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**, build green. `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🗂️ Finish admin surface — inventory Master-DB editor + 2FA enrollment
- **Two gaps closed** toward new-stack self-sufficiency: inventory was **read-only** (you still needed Streamlit to add/edit a master item — a cutover blocker), and 2FA could be *verified*/*reset* but never *enrolled*.
- **Inventory editor** (`admin.py`, admin-only level 4): `POST /admin/inventory` (SAP unique → 409), `PATCH /admin/inventory/{sap}` (**Opening_Stock changes get an explicit `OPENING_STOCK_EDIT` audit** since they feed the identity math), `DELETE /admin/inventory/{sap}` (**guarded — refuses if the SAP has any rows in receipts/consumption/returns/lots/pending_*/pr_master**, so it can't orphan history). Every write audited (CREATE/UPDATE/DELETE_INVENTORY). Reads still via the open `/inventory`. **+3 endpoints.**
- **2FA self-enrollment** (`auth.py`, current user): `GET /auth/2fa/status`, `POST /auth/2fa/enroll` (pyotp secret + otpauth URI + **QR PNG data-URI**; secret stored but 2FA stays OFF), `POST /auth/2fa/verify` (valid code → enable), `POST /auth/2fa/disable` (valid code → clear). A half-finished enroll never locks anyone out (login only challenges when `totp_enabled=1`). **+4 endpoints.** **83 total API paths.**
- **Verified live (PG):** inventory — worker create→403, admin create→201, dup→409, PATCH Opening_Stock 100→250 (OPENING_STOCK_EDIT audit ✓), delete 1001 (3 movements)→409, delete unused item→200. 2FA — status→false, enroll→secret+uri+QR, wrong code→400, correct code→enabled, **login then returns `mfa_required`**, enroll-while-enabled→409, disable→restored. **In-browser (admin):** Inventory Master page (create item via modal → persisted → cleaned up); Security page — full **enroll → scan QR → verify → ON → disable → OFF** cycle with real TOTP codes. Console clean (fixed antd `Alert message`→`title`).
- **Tests:** `service_tests.py` +6 non-persisting guard checks (worker→403 on POST /admin/inventory; admin dup-SAP→409; delete-with-movements→409; 2fa status/verify/disable guards) → **35/35 PASS**. Gated in CI.
- **Cleanup / parity:** test item + admin enroll are PG-only → `dual_ci` reset → inventory back to 306, admin 2FA OFF, **64/64 parity PASS**, derived-view parity **PASS 5/5**. `bug_check` **599/0**, crawler **21/21**, build green. `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🛡️ Hardening — service-level tests in CI + per-endpoint role audit
- **Two gaps closed:** (1) write services were only verified manually then cleaned — no automated regression net; (2) an audit found the **master-data write endpoints unguarded** — `make_read_router`'s POST/PUT/DELETE for vendors/warehouses/employees were behind `get_current_user` only, so any authenticated user (incl. a level-0 store keeper) could mutate master data via the API even though the nav hides it.
- **Fix:** `crud.py` `make_read_router` takes `write_dep`; `main.py` passes `write_dep=require_level(3)` so master-data **writes** now require level ≥ 3 (logistics/admin, matching the Master-Data nav gate). **Reads stay open** to any authenticated user (the app needs them). Full route audit otherwise clean: entry/receiving are `get_current_user` by design (store keeper stages → HOD approves); hod/logistics/warehouse/requests/sme/admin self-guard; only `/` and `/health` are open.
- **Tests:** new `backend/api/service_tests.py` — **Suite A** calls the write services inside a txn and asserts effects via count-deltas, then **rolls back** (no persistence, no cleanup): create_pr (+audit), submit_pr (+logistics notif), create_smr (+SK notif), approve_smr (+pending_issues +requester notif), post_receipt (+auto-lot +audit), notification visibility/isolation + mark_read guard. **Suite B** drives the real ASGI app with httpx: 401 no-token, 200 open read, **403 for worker on /admin/users · /hod/pending · /logistics/prs · POST /vendors** (the fix), admin passes the write gate (422 on bad body). **29/29 PASS**, and PG is byte-unchanged afterwards (0 svc rows persisted).
- **CI:** added a `Service + guard tests` step to `postgres-dual-ci.yml` (after parity), with a real `JWT_SECRET`. Now gated: bug_check · dual_ci (64/64) · derived-view parity (5) · **service+guard (29)**.
- **Verified:** service_tests **29/0**; dual_ci **PASS**, parity **PASS**; `bug_check` **599/0**, crawler **21/21**, frontend `npm run build` green (frontend untouched — backend-only slice). `database.py`/Streamlit untouched. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🔔 In-app notifications — sidebar bell + event wiring
- **Gap closed:** the new services fired no notifications; the old app's `app_notifications` inbox had no new-stack producer or reader. Now the procurement loop lights up a per-user bell.
- **Service** (`services/notifications.py`): `notify()` ports `queue_app_notification` (recipient_user OR recipient_role, narrowed by site/warehouse; silent no-op without a recipient). `list_for` / `unread_count` / `mark_read` / `mark_all_read` port the bell-inbox visibility rule **verbatim** — `recipient_user = me OR (recipient_role = role AND (recipient_site IS NULL OR =site) AND (recipient_warehouse IS NULL OR =warehouse))`, fully parenthesised so `read_at IS NULL` binds to both branches. `mark_read` carries the same visibility guard so nobody marks another user's row.
- **Router** (`notifications.py`): `GET /notifications` (+`unread_only`), `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`. A `_ctx` dep reads live `Site_ID`/`Warehouse_ID` from `users` (the JWT carries neither warehouse binding reliably). **+4 endpoints.**
- **Event wiring** (each a one-line `await notify(...)` inside the existing txn): `submit_pr` → role=logistics; `assign_po` → role=warehouse_user + warehouse; `ship_dn` → role=store_keeper + destination site; `create_smr` → role=store_keeper + site; `approve_smr` → recipient_user = the requesting supervisor (success feedback). Notifications are additive to the ledger — no integrity impact.
- **Verified live (Postgres):** supervisor→SMR fires `smr_created` (store_keeper@CNCEC); worker (store_keeper@CNCEC) sees it; admin→submit PR fires `pr_submitted_to_logistics` (role=logistics) and the store keeper does **NOT** see it (isolation ✓); admin→approve fires `smr_approved` (recipient_user=supervisor); supervisor sees it, marks it read (unread 4→3); **store keeper marking the supervisor's row → 404** (visibility guard); read-all clears; the bell also correctly surfaces the **75 pre-existing migrated notifications** to the right roles/sites.
- **Frontend:** `NotificationBell` in the header — antd `Badge` (unread count) + `Popover` feed (severity dot, body, timestamp, `open →` on linked rows). Click a row → mark read + navigate to `link_page`; **Mark all read**. Optimistic count updates (instant, rolls back on error) + invalidation. Bell + list render correctly from server state (verified across counts 3/1/2/0). **NOTE:** the headless preview reports `document.hidden=true`, which throttles React rendering — the sub-second live badge decrement couldn't be cleanly shown there, but persistence + mount render are correct and the optimistic path is standard. Console clean (dropped deprecated antd `List`/`Tag bordered`). `npm run build` green.
- **Cleanup / parity:** all test rows PG-only → `dual_ci` reset → **0 test SMRs, app_notifications back to 75, 64/64 parity PASS**, derived-view parity **PASS 5/5**. `bug_check` **599/0**, crawler **21/21**. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🛠️ Admin console — user management + audit-log viewer
- **Gap closed:** the new stack could only *log in* existing users — no way to manage them, and the rich `system_audit_log` (every service writes to it) had no viewer. Both now exist, admin-only.
- **Router** (`admin.py`, `require_level(4)` — admin only): `GET /admin/users` (never returns `password_hash`/`totp_secret`), `POST /admin/users` (bcrypt hash, role-validated, dup guard), `PATCH /admin/users/{u}` (role/site/warehouse/phone; **last-admin demote guard**), `POST .../reset-password`, `POST .../reset-2fa` (`totp_secret=NULL, totp_enabled=0`), `DELETE /admin/users/{u}` (**last-admin + self-delete guards**), `GET /admin/roles`; `GET /admin/audit` (filter by username/action/table + `ilike` details search, paginated, newest-first) + `GET /admin/audit/meta` (distinct actions/tables for dropdowns). Ports auth.py's `add_user`/`reset_password`/`delete_user`. Every mutation audits (CREATE_USER / UPDATE_USER / RESET_PASSWORD / RESET_2FA / DELETE_USER). **+9 endpoints.** The credential table `users` stays out of the generic CRUD — this is the one narrow admin-gated seam.
- **Verified live (Postgres):** guards worker/hod (level<4)→**403**, no-token→**401**; list leaks **no secrets**; create→**201** then the new user **logs in** (bcrypt round-trip ✓); dup→**409**, bad-role/short-pw→**422**; PATCH role+warehouse ✓; reset-password → new pw logs in / old pw **401**; reset-2fa ✓; self-delete→**409**, unknown→**404**, delete→**200**; audit filter (`target_table=users`) returns the full trail; `audit/meta` = 47 actions / 30 tables. **In-browser (admin):** Users page (8 users, create→delete round-trip through the modal), Audit page (671 events, live capture of the UI's own DELETE_USER at the top, username filter → 1 event). Console clean (`forceRender` on the modal avoids the useForm-not-connected + deprecated-`destroyOnClose` warnings).
- **Frontend:** new **Admin** nav group (level 4 only) → `UsersPage` (table + Create/Edit/Reset-PW modals, Reset-2FA/Delete popconfirms, self-row guards) + `AuditLogPage` (filters + pagination). Hooks in `api/hooks.ts`. `npm run build` green.
- **Cleanup / parity:** test users/audit rows live only in PG; reset via `dual_ci` → **0 test users remain, 64/64 parity PASS**, derived-view parity **PASS 5/5**. `database.py`/Streamlit untouched → `bug_check` **599/0**, crawler **21/21**. Local PG == SQLite.

### 2026-07-04 · actor=interactive · branch=`main` · 🧾 PR-creation UI — procurement now operable end-to-end
- **Gap closed:** the new stack could *submit* a PR to Logistics and build a PO from it, but had no way to **create** a PR — PRs only came from migrated data. Now a HOD/admin can raise one from scratch, so the whole chain (create PR → submit → PO → assign → WH receive → DN → ship → site receipt → HOD approve → ledger) runs from the new UI.
- **Service** (`services/procurement.py`): `create_pr()` ports `insert_manual_pr()` — validates & enriches each line against the ERP inventory master (SAP_Code must exist; `Material_Code`/`Material_Name`/`UOM` backfilled when blank, TRIM-matched), inserts one row per line (`status='open'`, `workflow_state='draft'`, `logistics_status='site_draft'`), writes a `CREATE_PR` audit row. `_next_pr_number()` auto-assigns **`PR-YYYYMMDD-NNNN`** (daily sequence; mirrors the SMR scheme in `services/supervisor.py`).
- **Endpoint** (`hod.py`): `POST /hod/prs` (`require_level(2)`, `CreatePRIn`/`PRLineIn`) — alongside the existing `GET /hod/prs` + `.../submit`. **67 API endpoints.**
- **Verified live (Postgres):** create → `PR-20260704-0001` (2 lines); enrichment confirmed (Material_Name/UoM backfilled, `" 1002 "` → `1002`, est-cost default 0, blank line-note falls back to the PR-level note); guards worker→**403**, unknown-SAP→**409**, qty≤0→**409**, empty-lines→**422**; submit → row appears in `/logistics/prs`. **In-browser (admin):** Create-PR form → real submit → `POST /api/hod/prs` **201** → `PR-20260704-0002` → shows in the Submit-to-Logistics tab. No console errors.
- **Frontend:** `HodPrsPage` reworked into tabs — **Create PR** (multi-line form, material picker off `/inventory`, auto-assigned number) + **Submit to Logistics** (the existing queue). No new nav (reuses HOD → Purchase Requests). `useCreatePr()` hook. `npm run build` green.
- **Cleanup / parity:** test PRs live only in PG; reset via `dual_ci` → **0 test rows remain, 64/64 parity PASS**, derived-view parity **PASS 5/5**. `database.py`/Streamlit untouched → `bug_check` **599/0**, crawler **21/21**. Local PG == SQLite.

### 2026-07-03 · actor=interactive · branch=`main` · 📓 New-stack handoff doc + expiring-view timezone fix
- **Handoff:** added `docs/NEW_STACK_HANDOFF.md` — the self-contained fresh-chat entry point (run steps, logins, golden rules, DONE list, and the explicit NOT-yet-ported backlog: in-app notifications, WhatsApp, email/mailer, local-LLM/OCR, CV, user-registration/-management/2FA-enrollment, reservations, QR, reports, man-hours, admin console, PR-creation UI, DN-approval chain, peripheral tabs). `handoff.md` points to it.
- **Fix:** `v_expiring_stock` port used PG `CURRENT_DATE` (local tz) vs SQLite `date('now')` (UTC) — when the calendar rolled over mid-session, `Days_Until_Expiry` read −7 vs −6 (rows otherwise identical), failing parity. Pinned the PG port to **UTC** (`(now() AT TIME ZONE 'UTC')::date`) so it matches SQLite regardless of tz/rollover. **Parity PASS 5/5 again.** `bug_check` 599/0, crawler 21/21, dual_ci 64/64. Local PG reset pristine.

### 2026-07-02 · actor=interactive · branch=`main` · 🧪 SME Material Estimator (READ-ONLY) — last major portal
- **Constraint honoured:** SME is **frozen** in Streamlit (SME Canon). The new build only **reads** the `sme_*` tables — never writes them. Ordering uses explicit keys, never rowid (Rule 1).
- **Backend** (`sme.py`, `require_level(2)` = hod/admin): `GET /sme/summary` (equipment/recipes/materials counts, total + planned + done SQM, equipment-by-lining-system), `/sme/equipment`, `/sme/recipes`, `/sme/sqm-progress`, `/sme/materials`. **`SQL_SME_MATERIALS`** is a Postgres-native port of the SQLite `sme_materials_view` (derived `Available_Qty = seed + received − consumed`, joined SAP_Code→inventory.Material_Code); added to the parity harness (`DERIVED_SME`) → **parity PASS 22/22** (now 5 derived views gated: live/by-site/lots/expiring/**sme_materials**).
- **Verified live (admin):** worker→`/sme/summary` **403**; summary = 65 equipment / 3526.39 total SQM / 86 recipes / 22 materials / planned SQM 41642.6; equipment (65) / recipes (86) / materials (22, derived avail) all return real data. Read-only → no cleanup, no divergence.
- **Frontend:** new **SME Estimator** nav group (hod/admin) → `SmePage`: tabs **Dashboard** (KPI cards + SQM-progress + equipment-by-lining-system) · **Equipment** · **Recipes/BOM** · **SQM Progress** · **Materials** (derived available). Site filter. Verified in-browser. `npm run build` green, console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified; the frozen SME drop-in is not touched.
- **Milestone:** **all major operational + estimator portals now run on the new stack.** Remaining = peripheral tabs (see the handoff WANT list), service-level CI tests, and the eventual cutover/deploy decision.

### 2026-07-02 · actor=interactive · branch=`main` · 🛡️ Supervisor portal — material requests → SK approve → pending_issues
- **Backend** (`services/supervisor.py` + `requests.py` router): ports `create_supervisor_request` (worker must be **active + site-bound**; per-line **stock snapshot + Available_Flag**; `SMR-YYYYMMDD-NNNN`), `approve_supervisor_request` (mirror lines → `pending_issues` `status=pending_hod`, `Work_Type=SUPERVISOR_REQUEST`, `Source_Ref=SMR:<no>:<item>`, `Requested_By`=supervisor — flowing into the **HOD Approvals → Issues** queue already built), `reject_supervisor_request`. Endpoints: `POST /requests` (supervisor/admin), `GET /requests`(+`/{id}/items`), `POST /requests/{id}/{approve,reject}` (store_keeper/admin). New `auth.require_roles` reused; create uses the supervisor's own site.
- **Verified live:** create SMR (CNCEC, worker 30001, item 1084 qty3 → `Stock_At_Request` 2.1, `Available_Flag`=0 (short) ✓); role guards worker-create **403** + supervisor-approve **403**; SK lists pending → approves → **1 pending_issue staged** (`SUPERVISOR_REQUEST`, `Source_Ref=SMR:…`) → shows in HOD Approvals → Issues. Test rows removed → PG == SQLite, **parity PASS**.
- **Frontend:** new **Supervisor** nav group (supervisor/admin) → `SupervisorPage` (New Request: site/worker/PPE/job + multi-item `Form.List`; My Requests, expandable items w/ availability tags); **Data Entry** += **Supervisor Requests** (`SkRequestsPage`: SK approve/reject pending SMRs, expandable). Hooks `useSmrList/useSmrItems/useCreateSmr/useSmrDecision`. Verified in-browser as admin. `npm run build` green, console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Next portal:** SME Material Estimator (the last major one). Deferred SMR bits: SK qty-adjust/withdraw, cancel, intent-vs-actual report, reservations.

### 2026-07-02 · actor=interactive · branch=`main` · 🔁 Closed the loop — DN → site receipt → staging → ledger
- **What:** a delivered warehouse DN now feeds the **site receipt staging** (the SK/HOD flow already built), closing the full circle: PR → PO → assign → warehouse receive → DN → ship → **site receive → pending_receipts (pending_hod) → HOD Approvals → commit_receipt → ledger**. `services/warehouse.py`: `incoming_dns` (in-transit DNs for a site) + `stage_dn_receipt` (ports `sk_mark_dn_received`'s **Material_Code → SAP_Code** inventory mapping, but stages into `pending_receipts` instead of writing the ledger directly — so the HOD still approves). New `receiving.py` router (`/site/incoming-dns`, `/site/incoming-dns/{dn}/items`, `POST /site/dns/{dn}/receive`), auth + site-scoped (a user only receives DNs for their own site; admin any).
- **Trace fix:** `commit_receipt` now carries the DN/PO/warehouse **trace columns** (`DN_Number`, `PO_Number_Source`, `Warehouse_ID`, etc. = receipts ∩ pending_receipts − base − blob) from the staged row onto the committed receipt (was dropping them). Verified the final ledger receipt shows `DN=… PO_src=… WH=…`.
- **Verified live (as admin) — the whole loop:** PR 456789 → PO → assign WH-01 → warehouse receive 10 → DN (ship 6, lot) → ship (in_transit) → site incoming-DNs lists it → **site receive → staged 1 pending_receipt** → appears in HOD Approvals → **HOD approve → receipt in ledger with full DN/PO/WH trace**. Then **reset PG to pristine via `dual_ci` (wipe+re-migrate) → 64/64 table parity + derived-view parity PASS** (a cleanup `LIKE` had over-matched a pre-existing DN, so the migration reset is the safe restore).
- **Frontend:** new **Data Entry → Incoming Deliveries** (`IncomingDeliveriesPage`): lists in-transit DNs for the user's site + expandable dn_items + **Receive** (→ stages receipts for HOD approval). Hooks `useIncomingDns/useSiteDnItems/useReceiveDn`. `npm run build` green, console clean, renders (empty on pristine PG — no in-transit DNs).
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.

### 2026-07-02 · actor=interactive · branch=`main` · 🏭 Warehouse portal — assignment → receive → DN → outbound
- **Backend** (`services/warehouse.py` + `warehouse.py` router, `require_roles("warehouse_user","logistics")` — new exact-role guard in `auth.py`): ports `list_assignments_for_warehouse` (PRICES never joined), `acknowledge_assignment`, `record_warehouse_receipt` (bumps `po_items.Delivered_Qty`, over-deliver guard, rolls assignment/PO status), `_generate_dn_number` (`DN-<WH>-<YYYYMMDD>-<seq>`), `create_delivery_note` (**RL/BL strict separation** — reject multi-family DN — + available-qty guard: delivered−returned−already-on-live-DNs), and a `ship_dn` (draft→in_transit). Endpoints: `GET /warehouse/assignments`(+`/{id}/items`), `POST /assignments/{id}/{acknowledge,receive}`, `POST /dns`, `GET /dns`(+`/{dn}/items`), `POST /dns/{dn}/ship`.
- **Verified live on real PG** (as admin): read WH-01 assignments (prices hidden); worker → **403**; built a fresh chain (PR→PO-WHTEST→assign) → acknowledge → receive 10/line (over-receive 9999 → **409**) → prepare DN `DN-WH-01-20260702-001` (over-ship 9999 → **409**, RL/BL enforced) → ship → in_transit → DN list. **All test rows removed + PR reverted → PG == SQLite, parity PASS** (delivery_notes/po_assignments back to baseline).
- **Frontend:** new **Warehouse** nav group (exact roles warehouse_user/logistics/admin — `buildMenu` now takes role) → `WarehousePage`: warehouse picker + tabs *Incoming Assignments* (Acknowledge / **Receive** modal (qty-per-line) / **Prepare DN** modal (ship-qty + lot per line)) and *Delivery Notes* (list + expandable dn_items + **Ship**). Hooks `useWhAssignments/useWhAssignmentItems/useWhAck/useWhReceive/useCreateDn/useWhDns/useDnItems/useShipDn`. Verified in-browser as admin. `npm run build` green, console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Deferred:** DN → Logistics-approve → HOD-approve → **site pending_receipts** (closes the loop into the SK staging we already built); returns-from-site; warehouse history/throughput. **Next portals:** Supervisor requests, SME estimator; then the DN→site-receipt bridge.

### 2026-07-02 · actor=interactive · branch=`main` · 🚚 Logistics portal — PR → PO → assign (procurement chain)
- **Backend** (`services/procurement.py` + `logistics.py` router `require_level(3)`): ports the Logistics chain from database.py — `submit_pr`(`submit_pr_to_logistics`:8619), `pr_queue`/`hod_prs`(`list_prs_for_logistics`:8675), `create_po_from_pr`(`create_po_manual`:8769 — header + po_items with **RL/BL family tagging** via the ported `classify_rl_bl_family`, then flips PR lines to `logistics_status='in_po'`), `assign_po`(`assign_po_to_warehouse`:9486 — validates active warehouse + open PO → `po_assignments`). Endpoints: `GET /logistics/prs`, `/prs/{pr}/lines`, `POST /pos`, `GET /pos`, `/pos/{po}/items`, `POST /pos/{po}/assign`; HOD feeder `GET /hod/prs` + `POST /hod/prs/{pr}/submit`.
- **Verified live on real PG** (as `admin`, level 4 ≥ 3 — the migrated `Logistics` user's password isn't a known default): HOD PR list → PR 456789 (13 lines, site_draft); submit → 13 submitted; logistics queue → 1 PR (qty 195); worker → `/logistics/prs` **403**; create PO → PO-TEST-1 with 13 po_items (rl_bl tagged), PR flips to in_po (queue empties); assign → WH-01 ✅; bad warehouse → 409. **All test rows removed + PR reverted → PG == SQLite, derived-view parity PASS** (306/51/10/2). *(Also swept a stray `consumption` test row that had been left from earlier UI testing — parity now clean.)*
- **Frontend:** new **Logistics** nav group (level ≥ 3) → **Procurement** (`LogisticsPage`: tab *Incoming PRs* → Create-PO modal; tab *Purchase Orders* → list + expandable po_items + Assign-to-warehouse modal). **HOD** group += **Purchase Requests** (`HodPrsPage`: submit site PRs to logistics). Hooks `useHodPrs/useSubmitPr/useLogisticsPrs/useLogisticsPos/usePoItems/useCreatePo/useAssignPo`. Verified in-browser as admin: Logistics portal renders the 3 existing POs with Assign + expandable items. `npm run build` green, console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Deferred (Logistics peripheral tabs):** reschedules, force-close, vendor-returns, material-details, history, shipment schedules, PO attachments/quotations, manual PO (non-PR). **Next portals:** Warehouse (receive→DN→outbound), Supervisor requests, SME estimator.

### 2026-07-02 · actor=interactive · branch=`main` · 🏛️ Staging→approval workflow + HOD portal (approvals + burn-rate)
- **User decision:** reintroduce the old app's **stage → HOD approve → commit** control (not direct posting). Entry now stages; the existing `post_*` services became the **commit** step, reused at approval.
- **Backend:** `services/ledger.py` — `stage_receipt/consumption/return/adjustment` write to `pending_receipts`/`pending_issues`/`pending_returns`/`stock_adjustments` (status=`pending_hod`); `commit_receipt/consumption/return/adjustment` load the pending row → post to the ledger via `post_*` (FEFO/lot/PR-close/audit) → delete (receipts/issues) or mark approved (returns/adjustments); `reject_pending` marks rejected. `entry.py` endpoints now call `stage_*`. New `hod.py` router (guarded by `require_level(2)` — hod/admin): `GET /hod/pending`(+per-kind lists), `POST /hod/pending/{kind}/{id}/approve|reject`, `GET /hod/burn-rate`. `auth.py` += `require_level()`.
- **Verified live on real PG:** worker stages a receipt → `pending_hod`, **stock unchanged** (1001 stays 3.01); worker→`/hod/pending` **403**; hod sees counts `{receipts:1, returns:26(pre-existing), …}` + the pending row; hod **approve** → commits (1001 → 8.01, lot auto-created); worker stages an issue → hod **reject** → marked rejected, **stock unchanged** (1002 stays 33.9); burn-rate returns per-material consumed + daily avg. All test rows removed → PG == SQLite, **derived-view parity PASS** (306/51/10/2).
- **Frontend:** entry pages now show "submitted for HOD approval"; new **HOD** nav group (level ≥ 2) → **Approvals** (tabs receipts/issues/returns/adjustments with pending-count badges + Approve/Reject per row + site filter) and **Burn Rate** (site + days → consumed/daily-avg). Hooks `useHodCounts/useHodPending/useHodDecision/useBurnRate`. Verified in-browser as `hod`: HOD group shows, **Master Data hidden** (level 2<3), Returns tab lists 26 pending with actions. `npm run build` green, console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Next portals:** Logistics (PR→PO), Warehouse (receive→DN), Supervisor requests, SME estimator. Later: EOD "commit all", per-endpoint role checks beyond nav, carry receipt logistics-extras through staging→commit.

### 2026-07-02 · actor=interactive · branch=`main` · 🔐 Auth — login + JWT + role-gated SPA (ports bcrypt/TOTP/roles)
- **Backend** (`backend/api/auth.py`): ports `auth.py` — **bcrypt** password verify, opt-in **TOTP** 2FA (pyotp, `valid_window=1`), roles from `config.py`. `POST /auth/login` → JWT (PyJWT, HS256, 8h) or `{mfa_required, mfa_token}`; `POST /auth/login/2fa` → JWT; `GET /auth/me`. Writes `LOGIN` / `LOGIN_FAILED` / `2FA_FAILED` audit rows. `get_current_user` guards the read entities + `/stock` + `/meta` (via `include_router(dependencies=…)`); entry routes self-guard and record the **authenticated user** as the ledger actor + audit username (replaced the `X-Actor` header). `JWT_SECRET` from env (dev default). `requirements.txt` += PyJWT.
- **Frontend:** `auth/AuthContext` (token in localStorage, `/auth/me` on boot, `gi-unauthorized` on 401), axios request/response interceptors (`client.ts`) attach the bearer + drop the token on 401, `LoginPage` (username/password → optional TOTP step), App gates on `user`, `AppLayout` shows the user + **Sign out** and **role-gates the nav** (Master Data only for level ≥ 3 = admin/logistics).
- **Verified live on real PG** (migrated users): unauth read → **401**; wrong password → **401**; `admin`/`admin2026` → JWT + `{role:admin, level:4}`; `/auth/me` + gated reads **200** with token; `worker`/`floor2026` → `store_keeper` level 0. In-browser: login renders the app with all data (JWT on every request); **admin sees Master Data, store_keeper does not** (role-gated nav confirmed via a11y snapshot). Test audit rows removed → PG == SQLite. `npm run build` green.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Notes / next:** login form driven by real typing (onChange) — the harness's programmatic fill doesn't update antd controlled inputs, so the UI login was verified via the app's own fetch path (token → AuthProvider → app). 2FA path coded but not exercised live (no TOTP-enabled user). **Next: per-portal screens** (warehouse / HOD / logistics / admin / supervisor / SME). Later: 2FA enrollment UI, per-endpoint role checks (not just nav), `JWT_SECRET` in deployment.

### 2026-07-02 · actor=interactive · branch=`main` · 🧾 Ledger services — Consumption (FEFO) + Returns + Stock Adjustments
- **Completed the ledger write core** (all four ops now: receipt/issue/return/adjust), ported from `database.py`:
  - **Consumption** (`post_consumption`, ports the staging→consumption write + `suggest_fefo_lot_for_consumption`:8165): FEFO auto-tags the earliest-expiry open lot when no lot is given (reuses the parity-tested lot-balance SQL); **ALLOW-AND-LOG** — over-issue is permitted and recorded with a `warning`, never blocked (honours the locked [[fefo-enforcement-decision]]); audit `POST_CONSUMPTION`.
  - **Returns** (`post_return`, ports `approve_return_request`:3666): inserts into `returns` (reduces stock via identity); audit `POST_RETURN`.
  - **Adjustments** (`post_adjustment`, ports `insert_stock_adjustment`:7241 + `approve_stock_adjustment`:7301 as one direct action): variance>0 → synthetic **receipt**, variance<0 → synthetic **consumption** (STOCK_ADJUSTMENT tag), optional **lot disposal** (`lots.Status='disposed'`); writes the `stock_adjustments` row (approved) + audit `POST_ADJUSTMENT`. Reason codes = `ADJUSTMENT_REASONS` (verbatim).
- **Endpoints** (`backend/api/entry.py`): `POST /entry/{consumption,returns,adjustments}` + `GET /entry/adjustment-reasons`. Validation: 404 unknown-SAP, 422 bad reason_code, 400 zero-variance / integrity.
- **Verified live on real PG:** issue 1002 33.9→32.9 (−1) ✓; over-issue qty 9999 → `warning` returned, still posted (allow-and-log) ✓; return 1001 3.01→2.51 (−0.5) ✓; adjustment surplus system3/counted5 → synthetic receipt R:72, 1003 3.1→5.1 (+2) ✓; 422/400 guards ✓. **All test rows deleted → local PG == SQLite** (derived-view parity re-run **PASS** 306/51/10/2).
- **Frontend:** `IssuePage` / `ReturnPage` / `AdjustPage` (antd forms, searchable material + site, reason dropdown from API) under the **Data Entry** nav; new mutation hooks (`useConsumptionEntry`/`useReturnEntry`/`useAdjustmentEntry`, invalidate stock+ledger reads). `npm run build` green; console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**, `database.py` not modified.
- **Next:** auth (login + JWT, bcrypt/TOTP/roles) → per-portal screens. Hardening TODO: automated service-parity test (rolled-back-txn) in CI.

### 2026-07-02 · actor=interactive · branch=`main` · 🧾 Ledger services layer — Receipts slice (service → API → React), business-rule parity
- **Goal (user directive):** bring the new build to full parity with the Streamlit app — every tab + real data-entry — improving where the old app was capped. Started the **ledger services layer** (real transactional writes), Receipts first as an end-to-end vertical slice.
- **Old-app map:** ran a full sweep of `pages_internal/` + `database.py` (15 roles, 80+ tabs, 50+ write ops). Ported the exact receipt rules from `process_receipt_delivery()` (database.py:5062), `auto_generate_lot_number()` (:7818), `create_or_get_lot()` (:7824), `log_audit_action()` (:5375).
- **Backend:** `backend/api/services/ledger.py` — `post_receipt()` (async, Core over PG): insert receipt (base + validated extra logistics cols), **auto lot** when expiry given (`LOT-<YYYYMMDD>-<SAP>`), **mirror into `lots` master** (idempotent, Status='open'), **PR-fulfilment auto-close** (Σreceived ≥ Σrequested → pr_master.status='closed'), **audit** row (`POST_RECEIPT`). `backend/api/entry.py` — `POST /entry/receipts` (pydantic `ReceiptIn`, extra-col allow-list, `X-Actor` header until auth; owns the `async with session.begin()` txn, 404 unknown-SAP, 400 integrity).
- **Verified live on real PG:** SAP 1001 @ CNCEC 3.01 → **8.01** after a qty-5 receipt (identity math ✓); lot `LOT-20260702-1001` auto-created (Received 5 / Remaining 5 / open) ✓; audit row `tester|POST_RECEIPT|receipts|id=71 …` ✓; test rows then deleted to keep local PG == SQLite.
- **Frontend:** `frontend/src/pages/ReceivePage.tsx` — antd Form (Site, searchable Material, Qty, dates, Supplier, PR, Lot, Remarks) wired to `useReceiptEntry()` (invalidates stock/receipts). New **Data Entry** nav group. `npm run build` green; console clean.
- **Untouched:** Streamlit/SQLite — `bug_check` **599/0**. `database.py` not modified (services are a separate async layer).
- **Next slices:** consumption/issue (FEFO via `get_fefo_lots()` :4668), returns, stock adjustments; then auth; then per-portal screens. Hardening TODO: an automated service-parity test (post-in-rolled-back-txn) alongside `parity_check.py`.

### 2026-07-02 · actor=interactive · branch=`main` · ⚛️ React frontend (Vite + TS + Ant Design) on the FastAPI+PG stack
- **What:** new `frontend/` SPA — the first UI on the Postgres/API stack (Streamlit+SQLite stays the live app). Vite + React + TypeScript, **Ant Design**, **TanStack Query**, React Router, axios. Vite dev-proxy maps `/api` → uvicorn `:8000` (no CORS in dev).
- **Screens (config-driven off `src/config/entities.ts`):** Dashboard (KPI cards + inventory-by-category + expiring stock), Stock (tabs = derived views live/by-site/lots/expiring, with Site_ID + within-days filters), Records (generic read browsers for inventory/receipts/consumption/returns/lots/POs/equipment — server pagination + site filter), Master Data (add/edit/delete modals for vendors/warehouses/employees → the API's writable entities).
- **Verified:** ran both processes locally (`./run_api.sh` :8000 + `npm run dev` :5173), opened in a browser against **real PG data** — dashboard (306/2/2), vendor CRUD modal, and all four stock tabs render correctly; header shows live `postgresql · gihub`. `npm run build` (tsc typecheck) green; runtime console clean after fixing two AntD deprecations (`valueStyle`→`styles.content`, index-based `rowKey`→synthetic key).
- **Untouched:** Streamlit/SQLite (`bug_check` 599/0 · crawler 21/21 still hold; frontend is a separate process). `node_modules`/`dist` gitignored.
- **Next:** frontend polish/features as needed (auth screen, more entities, charts) + backend ledger services layer for transactional writes; deploy (Hetzner) is parked per the user.

### 2026-07-02 · actor=interactive · branch=`main` · 🧱 FastAPI backend v2 — derived stock (parity-tested) + master-data writes
- **Derived stock endpoints** (`backend/api/stock.py`, `/stock/*`): PG-native ports of the SQLite reporting views, computed at request time (views are NOT created on PG — the API computes them). `live`→`v_live_stock`, `by-site`→`v_site_stock`, `lots`→`v_lot_balance`, `expiring`→`v_expiring_stock`. Ports handle the real SQLite→PG gaps: quoted mixed-case identifiers, **all non-agg cols added to GROUP BY** (PG strict), and `julianday`/`date('now'[,'+30 days'])` → PG date arithmetic (`date - date` → int days, `CURRENT_DATE(+30)`), with a regex guard + `substring(...,1,10)` cast so junk expiry text can't 500 (SQLite `date()` is lenient; PG cast raises).
- **Accuracy proven — `backend/api/parity_check.py`:** compares each ported PG query against its SQLite `v_*` view as an **order-independent, value-normalised multiset** on the real data → **PARITY PASS** for all four (live 306, by-site 51, lots 10, expiring 2). Wired as a **CI step** in `postgres-dual-ci.yml` (runs after dual_ci populates PG). Note: `/stock/by-site?site_id=HQ` = 0 rows is *correct* (v_site_stock is activity-based; all movement is CNCEC) — parity confirms it.
- **Master-data writes** (`crud.py` `writable=True`): POST/PUT/DELETE for **vendors / warehouses / employees** only. Generic Core insert/update/delete with `.returning(*)`; `created_at`/`updated_at` auto-set via `func.now()`; unknown/secret/blob cols → 422; `IntegrityError`/`DataError` → 400. **Ledger tables stay read-only** (receipts/consumption/returns/inventory/lots/purchase-orders → POST 405) — their writes need the identity-math/FEFO/audit **services layer** (a later milestone), not naive INSERTs.
- **Verified live** on real PG: vendor create→read→update→delete cycle (auto `created_at`, 404 after delete, count restored to 2); `/receipts` POST→405; bad col→422; empty POST→400; 27 OpenAPI paths.
- **Tests:** Streamlit/SQLite untouched — `bug_check` **599/0**, crawler **21/21**. Derived-view parity **PASS** (SQLite vs PG). `backend/api/README.md` updated.
- **Next:** the React frontend (the remaining `FRONTEND_GO` item). Backend follow-ups when needed: ledger services layer (transactional writes) + optional JWT auth.

### 2026-07-02 · actor=interactive · branch=`main` · 🚀 FastAPI REST backend v1 (async, PostgreSQL) — runnable & viewable locally
- **What:** built the decoupled REST API foundation the pivot pointed to. New package `backend/api/`:
  - `db.py` — async engine (`create_async_engine` + asyncpg, `pool_pre_ping`) + `async_sessionmaker`/`AsyncSession` dependency (architecture **rule #5**).
  - `config.py` — reads `DATABASE_URL`, normalises psycopg2/plain-postgres URLs onto the **asyncpg** driver; default `postgresql+asyncpg://postgres@127.0.0.1:5433/gihub`; CORS origins for the future React dev server.
  - `crud.py` — generic **read-only router factory** over a SQLAlchemy Core `Table` (from `models.Base.metadata`). Uses `result.mappings()` so columns with awkward names (`"Approved By"` with a space, `Dia_L`) serialise by their true DB name. Orders by explicit PK (**rule #2**); `?site_id=` filter for site-scoped tables (**rule #4**); drops `LargeBinary` blobs + scrubs secret-named columns.
  - `main.py` — app wiring: `/health`, `/meta/sites`, `/meta/inventory-summary` (exact GROUP BY counts) + list/detail for **10 core entities** (inventory[PK SAP_Code]/receipts/consumption/returns/lots/purchase-orders/equipment/employees/vendors/warehouses). Credential tables (users/pending_users/*_tokens/qr_approval_requests) **not exposed** (rule #3 isolation).
  - `run_api.sh` + `backend/api/README.md`; `requirements.txt` += `asyncpg`, `greenlet`.
- **Scope (accuracy-first):** **read-only** v1. Writes (POST/PUT/DELETE) and any **derived** figure (e.g. "live stock", currently a SQLite view) are **deferred to v2** — those views get ported to PG *with parity tests*, not hand-rolled, so results stay exact. v1 serves raw rows + exact counts only.
- **Verified live on the real PG data** (`gihub` on 5433): `/health` ok (dialect=postgresql); `/meta/sites` → [CNCEC, HQ]; `/meta/inventory-summary` total 306; site scoping `?site_id=HQ` → total 13; detail by string PK (`/inventory/1001`) + int PK (`/receipts/1`); blob excluded from `purchase-orders`; missing→404, bad-int→422; `/docs` 200; 23 OpenAPI paths.
- **Tests:** Streamlit/SQLite **completely untouched** — `bug_check.py` **599/0**, `test_ui_crawler.py` **21/21**. The API is a **separate process**; psycopg2 still drives the sync migration/dual-CI.
- **Run it:** `./run_api.sh` → open **http://localhost:8000/docs**. Prereq: local PG populated (via `backend/dual_ci.py` / `migrate_sqlite_to_postgres.py`).
- **Next:** v2 = write endpoints + ported derived views (parity-tested) + optional JWT auth; then the React frontend (still the open `FRONTEND_GO` item — user green-lit the backend only).

### 2026-07-02 · actor=interactive · branch=`main` · 🧭 STRATEGIC PIVOT — Streamlit-on-PG parked; PG = FastAPI foundation; data-layer proven on real PG
- **Decision (user-approved):** the existing Streamlit app **stays on SQLite**. Reason, confirmed against a **local Postgres** installed this session: the whole raw-SQL surface uses unquoted mixed-case identifiers (`SAP_Code`, `Site_ID`, …) — PG folds them to lowercase and can't match the case-preserved columns. Scope is ~1,320 lines / 170 `df["Mixed_Case"]` keys / 74 SQL aliases — a full retrofit (lowercase schema + result-remap) is large/risky with no clean shortcut. **The Postgres schema (`models.py`) + copy script are the foundation for the future FastAPI backend** (ORM-based → quotes identifiers → no case problem). This matches the original `FRONTEND_GO: NO` plan.
- **What now works, verified on REAL local Postgres 16** (`brew install postgresql@16`, port 5433): `backend/dual_ci.py` → **table parity 64/64 ✅, semantic aggregates ✅, `get_connection()` facade + `?`-params + `read_sql` + `init_db` (create_all) all ✅.** The DATA-LAYER migration is proven end-to-end on Postgres.
- **Scoped out of the PG path (intentional):** the 14 SQL views (SQLite/Streamlit legacy — FastAPI computes those via ORM). `run_migration(create_views=…)` defaults to skip-on-PG; `dual_ci` skips view checks on PG; `_init_db_postgres()` creates tables only. `backend/pg_smoke.py` (behavioural Streamlit-on-PG) is retained but **removed from CI** (its premise is parked).
- **Local PG for ongoing verification:** installed + a throwaway cluster in scratchpad, so PG work is now verified locally (no CI paste loops).
- **Tests:** SQLite `.venv` **599/0 · 21/21**; dual_ci dry-run (SQLite) PASS with views; dual_ci vs real local PG **PASS**. CI (GitHub Actions) should now be green on the data-layer job.

### 2026-07-02 · actor=interactive · branch=`main` · CI fixes (first real PG run surfaced two)
- **First live Actions run went red at the `bug_check` step (exit 2)** — two real bugs the CI caught:
  1. The workflow set `DATABASE_URL` at **job level**, so it bled into the SQLite `bug_check` step → `db_dialect()`→postgresql → `init_db` took the PG path mid-suite → crash. **Fix:** `DATABASE_URL` is now scoped to only the `dual_ci` + `pg_smoke` steps; `bug_check.py` also defensively `os.environ.pop("DATABASE_URL")` at startup (it's the SQLite suite).
  2. **`models.py` CHECK constraints used unquoted identifiers** (`CHECK (Worker_Type IN …)`) — Postgres folds `Worker_Type`→`worker_type`, which doesn't match the quoted `"Worker_Type"` column → `create_all` fails on PG (would also break dual_ci/pg_smoke). **Fix:** regenerated `models.py` WITHOUT CHECK constraints (enum rules stay enforced in app code + the SQLite schema; PG can get validated CHECKs later). All 64 tables now compile cleanly to the PG dialect.
- **Verified locally:** bug_check 599/0 (SQLite-forced), all 64 tables PG-DDL-compile, dual_ci dry-run PASS. Re-pushed for the next Actions run.

### 2026-07-02 · actor=interactive · branch=`main` · Step 2 increment 3 — behavioural dual-CI + runtime dialect fixes (wave 1)
- **Behavioural harness:** `backend/pg_smoke.py` migrates the DB then runs 16 real `database.py` code paths through `get_connection()` and reports per-path pass/fail (isolated, so one run lists everything). Wired as a CI step in `postgres-dual-ci.yml` (runs on real PG). `--dry-run` validates structurally on SQLite (16/16 on the real DB).
- **Runtime dialect fixes (verifiable on SQLite, no-ops there):**
  - `rowid` → `rowid_ref()` helper (`rowid` on SQLite, `id` on PG) at the 5 receipts read sites (`get_receipt_history`, activity feed, `get_item_bin_locations`, `report_daily_receipts`).
  - `datetime('now')` → `now_sql()` in `get_overdue_unreported_items`.
  - `INSERT OR IGNORE` → `sql_insert_or_ignore()` helper (`ON CONFLICT DO NOTHING` on PG) at 3 sites (`process_receipt_delivery`, `create_or_get_lot`, `record_cross_site_view`).
  - Unit test `check_pg_sql_helpers` covers both dialects (no PG needed).
- **⏭️ WAVE 2 (remaining runtime dialect-isms — need per-caller work / CI verification):**
  - `date('now', ?)` / `datetime('now', ?)` param-modifier sites (5): `get_consumption_value_window`, `list_supervisor_requests`, `list_smr_history`, `report_supervisor_intent_vs_actual`, `get_locate_anything_summary` — the `?` carries a SQLite modifier string ('-30 days'); PG needs `INTERVAL`. Convert to pass an int + `days_ago_sql()`.
  - `INSERT OR REPLACE` → upsert (2): `next_temp_material_code` (app_settings), `insert_sme_inventory_seed` — need `ON CONFLICT (target) DO UPDATE`.
  - Add these functions to `pg_smoke` as they're fixed (CI turns them green).
- **Tests:** `.venv` **599/0 · 21/21**; pg_smoke dry-run 16/16 on real data. SQLite path unchanged (all fixes are no-ops on SQLite via the helpers).

### 2026-07-02 · actor=interactive · branch=`main` · Step 2 increment 2 — init_db PG-guard + read_sql
- **read_sql (265 sites) — ZERO changes needed.** Verified `pd.read_sql(sql, conn, params)` works THROUGH the `_EngineConnection` facade (pandas 3.0 DBAPI path uses `cursor.execute` + `description`, which the facade provides with `?`→`%s` translation). So all 265 sites work on Postgres unchanged.
- **`init_db` PG-guard.** On Postgres, `init_db` now early-returns via `_init_db_postgres()` — `models.Base.metadata.create_all()` (tables, idempotent) + recreate the 14 views (PG-native override for `v_expiring_stock`). The SQLite self-heal DDL (PRAGMA/AUTOINCREMENT/rebuilds/`date()`) is skipped entirely. Data is loaded by the migration, not seeded here. `backend/` is now a package (`__init__.py`) so `database.py` can import `models`.
- **SQLite unchanged:** the guard is `if db_dialect(conn)=='postgresql'`; on SQLite it's skipped. Verified on a copy of the **real DB** (init_db + get_connection + inventory/v_site_stock/users) — OK.
- **CI:** the dual_ci facade smoke now also calls `init_db()` on Postgres (asserts the app can *start* on PG) and runs `read_sql` through the facade on PG.
- **Tests:** `check_pg_compat_seam` extended (read_sql-through-facade). **598/0 · 21/21.**
- **Where this leaves us:** with the migration + this seam, the app should now be able to run on Postgres (get_connection facade + read_sql + init_db-guard). Remaining before a confident cutover: run the full `bug_check` against Postgres in CI (behavioural dual-CI) to shake out any last type-affinity / SQL-dialect edge cases, and finish the `PRAGMA table_info`→`column_exists` sweep (only relevant to SQLite self-heal, which PG skips, but keeps the code portable).

### 2026-07-02 · actor=interactive · branch=`main` · Step 2 increment 1 — runtime connection seam
- **What:** wired `get_connection()` to the engine behind the `DATABASE_URL` dialect switch. New `_qmark_to_pyformat()` (translates `?`→`%s`, escapes `%`, skips string/identifier/comment contexts) + `_EngineConnection`/`_EngineCursor` — a `sqlite3.Connection`-compatible facade over the SQLAlchemy raw DBAPI connection (`execute`, `cursor`, `commit/rollback/close`, `fetchone/all/many`, `rowcount`, `description`, `lastrowid` via `SELECT lastval()` on PG, context manager). **SQLite path 100% unchanged** — the facade activates ONLY when `DATABASE_URL` is Postgres and no explicit `db_file` is passed.
- **Audit that scoped it:** 155 `PRAGMA`, 265 `read_sql`, 51 `.lastrowid`, 63 `.cursor()`, 0 `executemany`/`executescript`, 1 `row_factory`, 1 context-manager. So `read_sql`-on-PG (pandas needs an engine/params) and `init_db`-on-PG (PRAGMA/DDL) are explicitly **later increments** — increment 1 is the execute-path seam only.
- **Tests:** `check_pg_compat_seam` (translator units incl. `?`/`%` inside strings/identifiers/comments; facade-over-sqlite equivalence for execute/lastrowid/rowcount/cursor). Full startup smoke on a **copy of the real DB** (init_db + get_connection + inventory/v_site_stock/locations queries) — OK. CI dual_ci gains a **facade smoke on real Postgres** (`?` params, `?/%/'` value passed as a parameter, lastrowid, rowcount). **598/0 · 21/21** (SQLite).
- **Next increment:** `init_db` PG-guard (schema on PG comes from models.py, not the SQLite self-heal DDL) + migrate the `read_sql` sites (pass the engine) so the app actually runs on Postgres and the full `bug_check` can run against it.

### 2026-07-02 · actor=interactive · branch=`main` · 🚑 HOTFIX: system_settings rebuild crashed existing DBs
- **Symptom:** the app's global error boundary fired on localhost. Root cause: the `system_settings` `id`-PK rebuild (added earlier) crashed `init_db` on any **existing** DB — the `locations`/`types` compat views already reference `system_settings`, so SQLite's view-integrity check blocked `RENAME system_settings_new → system_settings` ("error in view locations: no such table"). It left an **orphan `system_settings_new`**, so every subsequent startup then failed at `CREATE ... already exists`. Fresh-DB tests (all of them) never hit this because the views don't exist yet when the rebuild runs.
- **Fix (`database.py`):** before the rebuild, `DROP VIEW IF EXISTS locations/types` (recreated later in the same `init_db`) and `DROP TABLE IF EXISTS system_settings_new` (clears the orphan). Idempotent; auto-repairs a stuck DB on next startup. Verified against a copy of the real broken DB → recovers cleanly, 30 rows preserved, orphan gone, views queryable.
- **Regression test:** `check_system_settings_migration_on_existing_db` builds the exact broken state (views + orphan) and asserts recovery — **fails on the pre-fix code, passes after.** This closes the fresh-DB-only blind spot.
- **Tests:** full `.venv` **597/0 · 21/21**. Committed the code fix only (the working-tree `gi_database.db` was locked by the running app; it self-heals on restart).

### 2026-07-01 (night) · actor=interactive · branch=`main` · Phase-4 dual-CI harness + totp fix
- **Files:** `backend/dual_ci.py` (new), `.github/workflows/postgres-dual-ci.yml` (new), `backend/migrate_sqlite_to_postgres.py` (PG view overrides), `backend/models.py` (regenerated: raw view SQL), `database.py` (totp fix), `bug_check.py` (+3 checks), docs, handoff.
- **totp fix:** relocated the `users.totp_*` self-heal to AFTER both role-CHECK rebuilds (via `column_exists`) so a fresh DB's 1st `init_db` keeps 2FA columns. Regression test added.
- **Dual-CI harness** (`backend/dual_ci.py`): migrates SQLite→target then checks per-table + per-**view** row-count parity and **semantic aggregates** (identity-math totals, lot balances, expiry counts). `--dry-run` = SQLite→SQLite (local, no PG). **GitHub Actions workflow** stands up a `postgres:16` service and runs `bug_check` (SQLite) + `dual_ci` (PG) on push — dual-backend CI with **no local Docker** (neither the sandbox nor the user's Mac has Docker/PG).
- **PG view override:** `v_expiring_stock` rewritten for Postgres (`julianday`/`date('now')` → `::date` arithmetic + `CURRENT_DATE`, with a `~ '^[0-9]{4}-...'` guard so the cast never errors). Other 13 views are portable.
- **⚠ Two bugs the harness caught (both fixed):** (1) the model generator **flattened view SQL whitespace**, which swallowed `v_lot_balance`'s `--` line comment (rest of the query became a comment → broken view). Now stores **raw** view SQL (newlines preserved). (2) confirmed `system_settings`/SME views survive.
- **Tests:** full `.venv` **596/0 · 21/21**. Dry-run dual-CI PASS on the real `gi_database.db` (all 64 tables, all 14 views queryable, semantic aggregates match).
- **Next:** the real Postgres run happens in **GitHub Actions** on push (watch the "Postgres dual-CI" workflow). Once green there, the remaining gap to cutover is wiring `get_connection()` to the SQLAlchemy engine (so the *app* + full `bug_check` run on PG) — Phase 3 completion + a behavioural dual-CI.

### 2026-07-01 (late) · actor=interactive · branch=`main` · Phase-5 copy script + PG service
- **Files:** `backend/migrate_sqlite_to_postgres.py` (new), `docker-compose.yml` (postgres service + pg-data volume), `backend/models.py` (regenerated: steady-state), `bug_check.py` (+2 checks: migration dry-run, plus parity now steady-state), `docs/`, `handoff.md`.
- **Copy script** — `run_migration(source_sqlite, target_url, wipe, chunk)`: creates the target schema from `models.py`, copies every table in dependency order, populates **`id := sqlite rowid`** for the 3 deferred ledger tables (preserves `posted_txn_ref`), **coerces** SQLite loose-typed values (empty/junk in numeric/date/bool cols → NULL, counted), fixes PG sequences (`setval`), recreates the 14 views, and does per-table **row-count parity**. `--dry-run` targets a throwaway SQLite so it validates with no live Postgres.
- **Validated:** real `gi_database.db` → dry-run **OVERALL PARITY OK** (all 64 tables, all 14 views). Regression-covered by `check_sqlite_to_pg_migration_dryrun`. Full `.venv`: **594/0 · 21/21**.
- **✅ Latent bug found by the dry-run — NOW FIXED:** `init_db()`'s two `users` role-CHECK rebuilds (recreate-and-copy) dropped the `totp_secret`/`totp_enabled` columns because they were self-healed *before* the rebuilds and aren't in the rebuild's column list — so on a brand-new DB they vanished on the 1st `init_db` and only reappeared on the 2nd startup. **Fix:** relocated the totp self-heal to *after* both `users` rebuilds (via `column_exists()`); regression test `check_users_totp_survives_fresh_init` asserts a single fresh `init_db` keeps them. `models.py`/parity retain the steady-state (2×`init_db`) approach as belt-and-suspenders.
- **Vestigial dropped columns (safe, legacy — confirm none are load-bearing):** `consumption.{Technician,status,WBS}`, `receipts.WBS`, `inventory.Sl_No`, `pending_issues.Technician`, `rejected_issues_archive.Technician`. A canonical `init_db` doesn't create these; the copy reports them rather than silently dropping.
- **Next:** stand up the `postgres` service locally → run the copy for real → Phase-4 dual-CI (`bug_check`/crawler against Postgres) → decide the totp fix.

### 2026-07-01 (evening) · actor=interactive · branch=`main` · ROUTINE PAUSED
- **Files touched:** `backend/models.py` (new), `database.py`, `pages_internal/hod_portal.py`, `bug_check.py`, this doc, `handoff.md`.
- **What:** Backend-prep pivot (FastAPI+PostgreSQL groundwork; no endpoints/React). (1) Generated `backend/models.py` — SQLAlchemy 2.0 Declarative for all 64 tables (+ 14 views documented, kept as views per SME Canon), introspected from the live `init_db()` schema; the 4 PK-less ledger tables get a SERIAL `id`. (2) Rowid audit across `database.py` + `pages_internal/` — 8 real SQL rowid sites found (rest are `cur.lastrowid` cursor attrs / comments). (3) Migrated `system_settings` to an explicit `id INTEGER PRIMARY KEY` via a guarded, idempotent rowid→id rebuild (runs before the `locations`/`types` views); fixed its 4 SQL sites (both SME compat views → `MIN(id)`, added `DROP VIEW IF EXISTS` so existing DBs pick up the change; HOD dropdown editor `SELECT id` + delete key). (4) Added 2 guardrail checks: `system_settings` id-PK + SME-views integrity, and `models.py` ↔ live-schema parity (isolated fresh `init_db`).
- **Deferred (by design):** `receipts`/`consumption`/`returns` `id` PK — these are the frozen identity-math ledger tables; adding a PK is a reviewed Phase-5 cutover-copy step, not a bundled sweep. Their 4 `receipts` rowid SQL sites stay on `rowid` (valid on SQLite) until then.
- **Test results (full `.venv`):** `bug_check.py` **593 passed / 0 failed** · UI crawler `test_ui_crawler.py` **21/21**. (Prior sessions' "20 failures" were an artifact of running system `python3` without optional deps — resolved by using `.venv/bin/python`.) `system_settings` rebuild verified idempotent (id survives repeated `init_db`); SME `locations`/`types` views confirmed to still return data via `MIN(id)`.
- **Guardrails:** SQLite stays default + fully working; SME business logic untouched (only the two compat views' sort-key expression `rowid→id`, behaviour-identical); identity math / EOD / RBAC / price masking untouched; `FRONTEND_GO` still NO.
- **Next:** await user confirmation on the deferred ledger-table PK approach; then either continue Phase 3 sub-phase A or begin the Phase-5 copy-script (SQLite→PG) design.

### 2026-07-01 · actor=interactive · branch=`main`
- **Files touched:** `database.py` + this doc (`docs/POSTGRES_MIGRATION.md` §7/§8) + `handoff.md` pointer.
- **What:** Phase 3 sub-phase A, increment 2. Converted **3 self-heal blocks (7 column-checks)** inside `init_db()` from raw `PRAGMA table_info` + set-membership to the `column_exists()` helper, following the routine's increment-1 pattern and the `returnable_items` per-column loop precedent:
  - `pending_receipts.rejection_reason` (single check; HOD-UI rejection metadata).
  - `receipts` DN/PO/Warehouse trace-ref loop → `DN_Number`, `Warehouse_ID`, `PO_Number_Source` (3 checks).
  - `pending_receipts` DN/PO/Warehouse trace-ref loop → `DN_Number`, `Warehouse_ID`, `PO_Number_Source` (3 checks).
  - **Why these:** all three blocks are pure upstream-traceability / HOD-UI metadata — they do **not** sit in the quantity-identity (`receipts − consumption − returns`) or EOD-commit code paths, so they pass the "closer read" bar the ledger requires for `receipts`/`pending_receipts` sites. Cost/RBAC/EOD/approval and multi-column-reuse blocks were deliberately left for individual triage (see §7 Next-action warning).
- **Before → after counts:** `PRAGMA table_info` (repo-wide) 88 → 85. `init_db()` self-heal call sites routed through `column_exists()`: 7 → 10.
- **Test results:** `bug_check.py` on this machine's system Python 3.12 — **560 passed / 20 failed, identical to the clean baseline** (verified by `git stash` of `database.py` → re-run → 560/20 → `stash pop`). All 20 failures are pre-existing environmental import errors (`dotenv`, `bcrypt`, `fpdf` not installed in this interpreter) that cascade through the module-import check and the mailer/auth/reports/PDF checks — **none touched by this diff, passing count unchanged from baseline (zero regressions).** Additionally exercised the edited path directly: fresh `init_db()` + idempotent re-run on a temp DB both succeed and create all 7 converted columns via `column_exists()`.
- **Guardrail confirmation:**
  - SQLite stays the default and fully working — ✅ `column_exists()` runs the identical `PRAGMA table_info` query on `sqlite3.Connection`; no SQL text changed for SQLite; idempotency preserved (re-run is a no-op).
  - Frozen code untouched — ✅ only `database.py::init_db()` self-heal blocks for traceability metadata; identity math, RBAC, EOD commit, cost fields, price masking, `sme_*`/`mh_*`, `material_estimator_portal.py` — none referenced by this diff.
  - Branch — ✅ interactive session committed to `main` after showing the human; routine PRs stay on `claude/*`, so no collision.
  - No `.db`/`.env`/`secrets.toml` committed — ✅ only `database.py`, this doc, and `handoff.md`.
  - FastAPI/React — ✅ not touched; `FRONTEND_GO` still `NO`.
- **Next action for the next run:** see "Next action" in §7 above — the unambiguously-safe single-column sites are largely exhausted; remaining work is sensitive-block triage or multi-column-reuse-loop conversion. Pick per the §7 warning.

### 2026-07-01 · actor=routine · branch=`claude/wizardly-pasteur-9t0hpz`
- **Files touched:** `database.py` (only).
- **What:** Phase 3 sub-phase A, increment 1. Converted 6 self-contained `PRAGMA table_info` self-heal call sites inside `init_db()` to use the existing `column_exists()` helper (established in Phase 2 for `stock_adjustments.Lot_Number`):
  - `returnable_items.whatsapp_alert_sent`
  - `pending_users.Phone_Number`
  - `whatsapp_queue.error_message`, `whatsapp_queue.attempts`
  - `returnable_items` 4 CV-audit columns (`cv_detected`, `cv_confidence`, `cv_employee_id`, `cv_tool_class`)
  - `employees.Site_ID`
  - `supervisor_material_request_items.line_status`
  - Deliberately skipped sites inside `users`, `receipts`, `consumption`, `returns`, `pending_issues`, `pending_receipts`, `pr_master` self-heal blocks (RBAC / identity-math / EOD-commit / cost-field adjacency — need individual triage, not a batch swap).
- **Before → after counts:** `PRAGMA table_info` (repo-wide) 94 → 88. `column_exists()` call sites in `database.py`: 1 → 7.
- **Test results:** `bug_check.py` — 576/580 passed on this sandbox's default Python 3.11 venv (4 pre-existing failures: missing `libzbar` system lib, a Python-3.11 `tokenize.FSTRING_START` gap, and a pre-existing f-string `SyntaxError` at `pages_internal/material_estimator_portal.py:2755` — all present identically on a clean checkout, none touched by this change). Re-verified on a Python 3.12 venv (matches the `tokenize`/f-string requirements): 579/580 passed, 21/21 `test_ui_crawler.py`, with the sole remaining failure (QR decode roundtrip, pyzbar/libzbar) confirmed identical on a clean pre-change checkout — i.e. **zero regressions, 0 sites caused by this increment's edits**. Passing count did not drop from baseline in either interpreter.
- **Guardrail confirmation:**
  - SQLite stays the default and fully working — ✅ `column_exists()` executes the identical `PRAGMA table_info` query on `sqlite3.Connection`; no SQL text changed for SQLite.
  - Frozen code untouched — ✅ `pages_internal/material_estimator_portal.py`, `scripts/sme_bootstrap.py`, `sme_*`/`mh_*` tables, identity math, RBAC, EOD commit path, price masking — none referenced by this diff (only `database.py::init_db()` self-heal blocks for non-frozen tables).
  - Branch — ✅ worked on `claude/wizardly-pasteur-9t0hpz` only (this session's designated branch), never `main`.
  - No `.db`/`.env`/`secrets.toml` committed — ✅ nothing outside `database.py` and this doc changed.
  - FastAPI/React — ✅ not touched; no `FRONTEND_GO: YES` line exists in this ledger.
- **Next action for the next run:** see "Next action" in §7 above — continue Phase 3 sub-phase A with the next ~10 `PRAGMA table_info` sites in `init_db()`.
