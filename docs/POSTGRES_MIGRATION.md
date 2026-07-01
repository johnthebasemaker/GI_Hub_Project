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
| 3 — Portable SQL (route ~185 legacy sites through Phase-2 helpers + named params) | 🔶 In progress | Sub-phase A (`PRAGMA table_info` → `column_exists()` in `init_db()`) started. 7/~55 `init_db()` self-heal call sites done (1 Phase-2 + 6 this run). Param-style (`?` → named params) not yet started. |
| 4 — Dual-backend CI | ⏳ Not started | Gated on Phase 3 completing. |
| 5 — Cutover | ⏳ Not started | Gated on Phase 4 green. |
| 6 — Server | ⏳ Not started | Gated on Phase 5. |

**Remaining-counts snapshot** (repo-wide, `grep -rn <pattern> --include=*.py . \| wc -l`, run at the start of each session and trusted over this table if they disagree):

| Pattern | Count |
|---|---:|
| `PRAGMA table_info(...)` | 88 (was 94 before this run) |
| `execute(...?...)` in `database.py` (single-line regex, undercounts true total — most `?` sites span multiple lines) | 9 |
| `date('now'` | 17 |
| `julianday` | 8 |

**Next action:** Continue Phase 3 sub-phase A — pick the next ~10 `PRAGMA table_info` self-heal call sites in `database.py::init_db()` (grep `PRAGMA table_info` in database.py, skip any already converted) and route them through `column_exists()`, following the exact pattern used for `stock_adjustments.Lot_Number` (Phase 2) and the 6 sites converted this run (`returnable_items`, `pending_users`, `whatsapp_queue`, `employees`, `supervisor_material_request_items`). **Continue avoiding**: `users`/`pending_users` login-adjacent RBAC columns beyond what's already done, and any site inside `receipts`/`consumption`/`returns`/`pending_issues`/`pending_receipts`/`pr_master` self-heal blocks that sit directly in the identity-math or EOD-commit code paths — those need a closer read (not a mechanical swap) because of the Section-2 guardrails, so triage them individually rather than batch-converting. Once all `PRAGMA table_info` self-heal sites in `init_db()` are converted, move to sub-phase B (`date('now')`/`julianday` → `now_sql()`/`date_diff_days_sql()`), then sub-phase C (`?` → named params).

---

## 8. Run Log

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
