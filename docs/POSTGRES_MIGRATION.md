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
