# PostgreSQL Migration & New-Stack Build Log

**Status (2026-07-06): BUILD COMPLETE — 🧊 CODE FREEZE, awaiting cutover.**
The plan below (written at Phase 0) has been executed far beyond its original
scope: the new **React + FastAPI + PostgreSQL** stack is feature-complete
(parity build, Man-Hours portal, Intelligence Layer AI-0…AI-5, SME React
rebuild S1…S5), all gates green (`service_tests` 352/352 · `bug_check` 599/0 ·
`parity_check` 5/5 · `parity:sme` 509). The **live production app still runs
Streamlit + SQLite, unchanged** — PostgreSQL is its verified mirror until
cutover day. Only Phase 7 (WhatsApp/email — Meta-token hold) and SME S6
(Master Data CRUD — deferred to cutover) remain.

➡ **For "where we left off", read [`PROJECT_STATUS.md`](PROJECT_STATUS.md).**
The authoritative per-slice history is **§8 Run Log** below (newest first).
Sections 1–7 are the original planning document, kept for rationale/history.

**Goal (original):** Make GI Hub run on **PostgreSQL** (localhost now, server later) for real
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

### 2026-07-13 (Phase 8-3) · actor=interactive · branch=`main` · 📬 Automated weekly Executive Summary — Friday 17:00 → PDF → WhatsApp link to Admin/HOD
- **`backend/api/weekly_report.py` + `weekly_report_loop()`** started from the FastAPI lifespan next to the digest loop (same `GI_SCHEDULER=0` escape hatch; server clock is Asia/Riyadh UTC+3): every **Friday 17:00** it renders the last-7-days Executive Summary via the existing `exec_pdf.py` — one ALL-SITES PDF for admins + one site PDF per distinct HOD site.
- **Delivery = secure expiring link, not a Meta attachment** (document messages only deliver inside a 24-h service window, which a Friday broadcast is not in): each PDF is stored in the new **`generated_reports`** table (alembic **`e7c31a9f24d5`**, new single head; model in models.py; mirror upgraded) with a 256-bit urlsafe token stored **only as sha256**; the link `{PUBLIC_BASE_URL}/reports/weekly-exec/{token}` dies after **72 h** (expired → 410, unknown → 404; endpoint is deliberately session-less — recipients open it from WhatsApp). Expired artifacts are purged on each run. Fan-out goes through `dispatch()` (status_update template, `delivery="urgent"` so it never sits in the digest queue) ⇒ every recipient also gets the in-app bell row; WhatsApp failures can never break the run.
- **Ops**: `POST /admin/reports/weekly-exec/run` (admin) triggers it on demand. ⚠️ Operator TODO: set **`PUBLIC_BASE_URL`** in `deploy/.env` to the externally reachable API origin (e.g. the tunnel URL incl. any `/api` prefix nginx adds) — default is `http://localhost:8000`.
- **Tests**: suite **AG** (+9 → **626/0**): `next_friday_1700` math (Wed→Fri, Fri-noon→same-day, Fri-18:00→next week) · run-now admin-only · ≥1 PDF + ≥2 recipients · tokenized link streams `%PDF-` with no auth · unknown 404 · force-expired 410 · one bell row per admin/HOD carrying the link · full cleanup.
- **Gates:** service_tests **626/0** · parity **5/5** · Playwright **38/38** · build+tsc ✅ · alembic single head **`e7c31a9f24d5`**.

### 2026-07-13 (Phase 8-2) · actor=interactive · branch=`main` · 🛡️ OTP toll-fraud + webhook HMAC-probing hardening (+ 429 countdown UX)
- **No new dependency** — extended the existing `ratelimit.py` (it already resolves CF-Connecting-IP → X-Real-IP → peer, which slowapi would not out of the box): new `check_bucket(key, …)` sliding-window on ARBITRARY keys, `PenaltyBox` (strikes→temp ban), `client_ip()` alias, and **`strict_limits_enabled()`** — the strict rules are ON in production, RELAXED when `GI_DOTENV=0` (service_tests / Playwright / CI keep working untouched), and force-enabled by `GI_FORCE_STRICT_LIMITS=1` for the suite that tests the limits themselves.
- **OTP (`POST /auth/phone/request-otp`)**: on top of the 5/min burst dependency, **3 requests/HOUR per source IP AND 3/hour per target phone number** (rotating IPs still exhausts the number's budget and vice versa), checked before anything else so failed/misconfigured sends still burn quota. 429s carry Retry-After.
- **Webhook (`POST /whatsapp/webhook`)**: `PenaltyBox(5 strikes / 10 min → 15-min ban)` on invalid `X-Hub-Signature-256` — a banned IP is refused with 429+Retry-After BEFORE body parsing, even if it later presents a valid signature; other IPs unaffected.
- **Frontend**: axios interceptor turns any 429 into a `gi-rate-limited` event; new `RateLimitToast` (mounted in AppLayout) shows ONE sticky countdown toast ("try again in Ns", deadline-based so background-tab timer throttling can't freeze it) ending in a "you can try again now" success note. Callers still receive the rejection for their own handling.
- **Tests**: suite **AF** (+6 → **617/0**): per-IP 4th request 429 · Retry-After present · per-phone cap across rotating IPs · 5×403 then ban · banned IP 429 even with a VALID signature · unrelated IP unaffected. All prior suites (X OTP flows, AB webhook) untouched thanks to the relax gate.

### 2026-07-13 (Parity sprint · approved plan) · actor=interactive · branch=`main` · 📎 Entry documents + return gates + MTC/WBS parity + UX polish
- **A1 — entry document system** (`backend/api/entry_docs.py`): the migrated-but-orphaned `entry_attachments` table (35 legacy files) finally has endpoints — SK upload (`POST /entry/attachments`, images/PDF/XLSX ≤15 MB, doc_number defaults DDMMYY, DN override), Library list (level ≥2, site-scoped; SK `mine=1`), inline/attachment download, unlinked-only delete. **`require_entry_documents` app setting (whitelisted in Console, DEFAULT ON)** hard-gates Issue/Receipt/Return submits (single + `/entry/bulk` with batch-level `attachment_ids`); attachments link to the staged batch (`entry_table` + `entry_date`). Frontend: `EntryDocsUpload` on all three entry forms (required marker driven by the setting) + **HOD Document Library page `/hod/documents`** (type tabs, date/doc-number filters, download + inline preview) + nav.
- **A2 — return workflow gates**: `/entry/returns` (strict mode = same master switch) requires Return DN No. + a source receipt via new `GET /entry/return-sources` (30-day window, 365 with override); qty capped to the source receipt; >30-day returns need a justification → `override_required=1` + provenance (`received_date/dn/qty`) on the pending row; **HOD approvals show a red >30d badge**; return approval auto-emails logistics (best-effort outbox). `stage_return` no longer stuffs Remarks into `override_reason`.
- **A3 — MTC correctness**: rubber detection now matches legacy `config.py` — **inventory `Category == "Surface Shields"`** (app-setting `mtc_required_category`), not a description token; MTC Number field added to the upload; hard-block + logistics email kept (stricter than legacy warn-only, per approved plan).
- **A4 — WBS**: `wbs_master` (migrated) gets HOD site-config endpoints (`/hod/site-config/wbs` GET/POST/PATCH open↔closed) + `GET /entry/wbs` options; entries at a site with ACTIVE WBS numbers must carry one (legacy semantics — sites without WBS unaffected; `wbs_master` is currently empty so nothing breaks until an HOD configures it). Staged `wbs` carries to the ledger `WBS` column on commit. WBS selects appear on Issue/Receive when the site has options.
- **B1** manual-lot FEFO override now captures a reason on IssuePage (min 5 chars, sent as `FEFO_Override` → existing critical HOD dispatch). **B5** Bin/Shelf field on Receive (staged + carried to ledger). **B6** verified already present (zero-variance rows can't submit; button disabled).
- **C1** inline attachment preview: 📎 button per approvals row (entry-date-matched) → drawer → image/PDF inline preview. **C2** `capture="environment"` camera button ("Photograph note") on the uploader. **C4** localStorage form-draft recovery (`lib/formDraft.ts` + DraftBanner) on Issue/Receive/Return. **C5** ledger Records tabs link to the Document Library.
- **Test compat (critical requirement honored):** service_tests `main()` sets `require_entry_documents='0'` up front and Playwright `global-setup` does the same in the clone, so ALL prior suites run unchanged; **suite AH** (+23 → **649/0**) and the sequenced `gated` Playwright project (`entry-docs.spec.ts`, runs AFTER the parallel pack → **39/39**) test the gates ON. Suites R/W rubber seeds updated to `Surface Shields`.
- **Gates:** service_tests **649/0** · Playwright **39/39** · parity 5/5 · build ✅ · tsc ✅ · alembic head **`e7c31a9f24d5`** (no schema change — every table already existed).

### 2026-07-13 (Phase 8-1) · actor=interactive · branch=`main` · 🧪 Predictive lining analytics — GET /analytics/lining-coverage + HOD/Logistics page
- **`backend/api/lining_analytics.py`** (router `/analytics`, `require_roles("hod","logistics")`, scoped users site-pinned, unscoped default **CNCEC**): runs the READ-ONLY SME planning engine (`sme_engine` — untouched, golden-parity intact) but swaps its availability pool from the frozen `sme_inventory_seed` to **LIVE ledger stock** (Σreceipts−Σconsumption−Σreturns per GI `Material_Code`, joined through `inventory.SAP_Code`; SME materials missing from the live master fall back to seed with a `stock_source:"seed"` flag + counts). Rolls up: per-FAMILY (RL/BL/OTHER — system-name tokens, composites like "RL+CBL 30THK" count in both; material names checked first) remaining vs achievable SQM + coverage% + top-5 bottleneck shortfalls · per-SYSTEM coverage sorted worst-first · per-MATERIAL demand/allocated/shortfall + **90-day burn → days-of-cover → depletion date** (the predictive half), sorted biggest-shortfall-first.
- **Frontend `LiningCoveragePage`**: routes `/hod/lining-coverage` + alias `/logistics/lining-coverage` (distinct nav keys so admin "All areas" never duplicates a menu key); nav entries in the HOD group (hod) and Logistics group (level ≥3). Family KPI cards with coverage Progress + bottleneck lines, worst-first system table with RL/BL tags, materials table with live-stock/seed tag, shortfall colouring, days-of-cover heat (⚠<90, 🔴<30) and depletion date. Verified live on the migrated CNCEC data (RL 0/11801 SQM with PR 304/BC 3004/E40 bottlenecks — real engineering truth: ledger stock is tiny vs demand).
- **Tests**: suite **AE** (+9 → **611/0**): role 403s (SK, supervisor), shape, HOD site pinning, RL+BL present + sane math (achievable ≤ remaining, 0≤cov≤100), worst-first ordering, `stock_source:"live"` rows present, **`live_stock` == ledger truth** (exact SQL cross-check), shortfall-desc ordering.

### 2026-07-13 (Polish sprint · Phase C) · actor=interactive · branch=`main` · 💬 Chat-with-your-data — /ai/query two-lane router + dashboard card
- **Backend `backend/api/ai/query_router.py` + `POST /ai/query` (+ `GET /ai/query/examples`), level ≥2**: two lanes. **Template lane** — deterministic intent router (returns/receipts/issues/stock/low-stock/expiring/top-suppliers/PRs/POs + time windows "today/yesterday/last week/last N days/this month/…" + optional site mention) → parameterized SQL, every value a bound param; a scoped user's Site_ID is **enforced from the JWT** (naming another site cannot widen it; '' scope matches nothing), which is what makes the lane safe for HODs. Works with Ollama down and the AI flags off. Count/total questions return a `metric` for a stat card. **NL lane** — unmatched questions from UNSCOPED roles (level ≥3, `ai_enabled`+`ai_nl_search_enabled`) fall through to the existing `analytics.run_nl_query()` (Ollama → safety gate → gi_ai_ro); the AI-5 ruling stands — generated SQL never runs for a scoped user; scoped users get a friendly refusal + example questions instead.
- **Frontend**: new `components/AskDataCard.tsx` replaces the Dashboard's inline NlSearchCard and is now shown to **level ≥2** (HODs included). Example-question tags, ⚡instant/🤖AI mode tag, metric stat card or dynamic table, collapsible executed SQL. Verified live against the migrated DB: "How many issues in the last 30 days?" → metric 2.1 · 1 entry (matches ledger truth).
- **Tests**: service_tests suite **AD** (+11 checks → **602/0**): role gates, template lane ok + site pinning (rows all HOD's site; naming another site stays pinned), metric == DB truth, scoped unmatched → refusal + examples (never NL), admin unmatched → NL lane (stubbed), admin site-mention narrowing, blank 422. Playwright `ask-data.spec.ts` (HOD card → instant answer → SQL visible) → suite **38/38** (run twice).
- **Gates:** service_tests **602/0** · parity **5/5** · build ✅ · `tsc` ✅ · alembic single head **`d6b0e72f51a8`** (no schema change) · Playwright 38/38.

### 2026-07-13 (Polish sprint · Phase B) · actor=interactive · branch=`main` · 📱 PWA — installable app, offline read cache, offline mutation queue
- **vite-plugin-pwa (`generateSW`)**: manifest (name/short_name, standalone, navy `#0a192f` theme, 192/512 + maskable icons generated into `frontend/public/`, apple-touch-icon), `registerType:'autoUpdate'`, SW registered in `main.tsx` via `virtual:pwa-register` (no-op in dev). Workbox precaches the build (77 entries) and runtime-caches core READ APIs (`/api/stock/* · inventory · receipts · consumption · returns · notifications · meta/*`) **NetworkFirst** (4 s timeout → last good copy, 24 h/300 entries) with `navigateFallbackDenylist` on `/api/` so the SPA shell never swallows API calls.
- **Offline mutation queue** `frontend/src/offline/queue.ts` (IndexedDB `gi-offline`): only the material-transaction POSTs opt in via `postWithOfflineFallback()` — `useLedgerPost` (receive/issue/return/adjust) + `useBulkEntry`. Network failure ⇒ payload saved + `{queued:true}` resolved (forms clear, amber "saved offline" toast instead of the success one — Issue/Receive/Return/Adjust pages branch on `res.queued`). Replay: `online` event, app boot, 60 s interval, or the new header `OfflineSyncBadge` (cloud-sync icon + count, click = sync now; owns all queue toasts). Replay is serialized with `X-Offline-Replay: 1`, stops on the first network error, and DROPS+surfaces server-rejected (4xx/5xx) entries so one bad row can't dam the queue. Approvals/auth/admin stay strictly online by design.
- **E2E proof** `tests/e2e/specs/offline-queue.spec.ts`: SK context goes `setOffline(true)` → post via the exposed `window.__giOffline` (same code path as the hooks) → `{queued:true}` + badge visible → back online → flush → badge gone → **HOD pending queue contains the replayed receipt**. Suite now **37/37**.
- **Gates:** `npm run build` ✅ (SW + manifest emitted) · `tsc --noEmit` ✅ · Playwright 37/37 · backend untouched (service_tests unaffected).

### 2026-07-13 (Polish sprint · Phases 0+A) · actor=interactive · branch=`main` · 🚀 Cutover migration executed + Playwright E2E suite (36/36)
- **Phase 0 — cutover migration EXECUTED**: `cutover_migrate.py --strict --wipe --target postgresql://postgres@127.0.0.1:5433/gihub` (sync psycopg2 driver — the `+asyncpg` URL the operator tried first fails with `MissingGreenlet`; the script is sync by design) → **CUTOVER ✅ VERIFIED**: 64 tables exact row parity, 5 semantic aggregates equal, 0 UOM issues, 0 soft-FK orphans under `--strict`. Follow-ups run: `backend/scripts/create_ai_readonly_role.sql` re-applied (grants are wiped on every reload) + `VACUUM ANALYZE`. alembic stamped `d6b0e72f51a8` by the script.
- **Phase 0 — human-style visual QA on the migrated DB** (hermetic backend `GI_DOTENV=0 GI_SCHEDULER=0` on :8000 + Vite :5173): admin login ✅ · Dashboard KPIs (306 items, 2 sites, `postgresql·gihub` badge) ✅ · Stock with historical aggregates (Concrete Blocks 36 received/2.1 consumed) ✅ · Records→Receipts renders legacy rows with preserved rowids + original remarks ("Send from SAR to Abu Hadriyah… DN. No. 14055") ✅ · Admin→Users shows migrated users with +E.164 phones ✅ · Issue entry form ✅.
- **Phase A — headless Playwright suite `tests/e2e/` (36/36 passed, ~14 s)**: `global-setup.ts` builds the whole stack per run (throwaway `gihub_e2e_pw` loaded via the REAL cutover script; role passwords reset to a known value inside the clone only; hermetic uvicorn :8010; Vite :5183 via new `VITE_API_PROXY` knob in `frontend/vite.config.ts`) and `global-teardown.ts` kills both process groups + `DROP DATABASE … WITH (FORCE)`. A `setup` project mints per-role **storageState** (admin/hod/sk/supervisor/logistics) by API login + planting `gi_token` — specs never log in through the UI. Specs: `auth` (login form good/bad/sign-out) · `smoke` (22 per-role routes: marker text + non-blank + zero pageerrors) · `workflows` (W1/W1b/W1c/W2/W3 — the 21-check night harness ported) · `negative-access` (403 lattice + UI affordance hiding) · `exec-summary` (Download PDF → real `%PDF-` bytes).
- **Suite findings encoded as assertions (all designed behavior, not bugs):** receipt approvals do NOT bell the submitter (`_SUBMITTER_COL` receipts→None — pending_receipts has no submitter column; returns/issues do, so the submitter-bell contract is asserted on returns). Pending returns store form `Reason`→`Return_Reason` (+`Remarks`→`override_reason`); the committed ledger row has plain `Reason`. SK at `/stock` is landed on Issue by the role manifest. The 2026-07-12 night-harness "SK notified of approval" check had passed against pre-existing clone rows — a false positive now corrected in `docs/automatic_test.md` §11.
- **Gates:** Playwright **36/36** · run `cd tests/e2e && npm test` (see tests/e2e/README.md; CI shape documented there + PROJECT_STATUS §F2).

### 2026-07-12 (QA night shift) · actor=interactive · branch=`main` · 🌙 Full E2E sweep + test matrix + antd-6 deprecation cleanup
- **Permanent test matrix** `docs/automatic_test.md` — every portal/tab/button + the 10 cross-role workflows (W1 entry-approval … W10 lot-lifecycle) + negative-access grid + the isolated-env recipe. Re-run per feature; run all before cutover.
- **Isolated test env** (never the live DBs): `gihub_e2e` Postgres cloned `TEMPLATE gihub`; hermetic backend `GI_DOTENV=0 GI_SCHEDULER=0` on :8000 (⇒ `wa.enabled()` False, zero Meta traffic); Vite → :8000. `gi_database.db` and the CI mirror untouched.
- **Multi-user workflow E2E (21/21):** drove W1 (SK stages receipt → HOD **edits** qty → approves → ledger row at edited qty → SK bell notification; stage return → HOD **rejects** w/ reason → NOT in ledger), W2 (supervisor SMR → SK approves → mirrors into HOD issue queue), W3 (HOD creates PR → submits to logistics), plus the negative-access lattice (SK→/hod 403, SK→/logistics 403, HOD→/admin 403, supervisor→/entry POST 403, scoped-HOD cross-site 403). All green — the backend state machines are sound.
- **Page-render probe (63/63, 0×500):** every page-backing GET across admin/hod/worker/supervisor returns <400. No dead endpoints.
- **Visual confirmation:** Stock (search `q` + category + expiring-days all fire the right query), Users (Reset-PW modal renders + 200), Executive Summary (KPI hero + all sections render live test data; **Download PDF** button → `GET …/export.pdf` 200; Download Excel wired), theme toggle, command palette.
- **Fixes / improvements applied (dev authority):** antd-6 deprecations that were flooding the console — `Alert message=`→`title=` (7 components: BrowseTable, SubmissionInsight, AppLayout, ProfileModal, AdminConsolePage, ApprovalsPage, ExecutiveSummaryPage), `Modal destroyOnClose`→`destroyOnHidden` + `Space direction`→`orientation` (ProfileModal), and the `rowKey={(_, i)=>…}` index-param deprecation (AdminConsolePage, SupervisorPage, ExecutiveSummaryPage — now inject a stable `__k`). Fresh-tab console = **zero errors/warnings**. No behavioural change; purely API-currency + console hygiene.
- **No functional bugs found.** Two initial "failures" in the workflow harness were the harness's own wrong payloads (receipt returns 201 not 200; PR-submit takes `site_id` as a query param — the real `useSubmitPr` passes it correctly; SMR 409 was *correct* validation rejecting an unknown worker).
- **Gates:** service_tests **591/0** · parity **5/5** · frontend build ✅ · `tsc --noEmit` ✅ · alembic single head **`d6b0e72f51a8`** (unchanged — no schema touched).
- **Automation next step (Playwright):** see PROJECT_STATUS §F. `docs/automatic_test.md` is written to be script-ready — each row maps 1:1 to a Playwright `test()`; the isolated-env recipe is the CI fixture.

### 2026-07-12 (UAT round 2) · actor=interactive · branch=`main` · 🖨️ Server-rendered exec PDF · delivery toggle → transaction forms · dual-OTP phone change
- **Executive Summary PDF is now server-rendered** (the print-the-webpage approach cut content at the page edges and was rejected in UAT). NEW `backend/api/exec_pdf.py` (fpdf2, A4 landscape): cover band + 6 KPI boxes with green/red prev-window deltas, "Actions & Pipeline at a Glance" 4-column grid, then every section as a measured table — **column widths are computed from content and normalized to the printable width (nothing can overflow the edges)**, cells truncate with an ellipsis, rows never split across pages, table headers repeat after every page break, letterhead strip on continuation pages + "Page X of Y" footer. `GET /hod/executive-summary/export.pdf` (detail_limit 500) streams it; the page's **Download PDF** button now calls it (with a loading spinner) — `PRINT_CSS`, the print letterhead and `window.print()` are gone. Live smoke vs the mirror: CNCEC 2026 full-year = 9 clean pages; empty single-day render also verified.
- **Delivery-preference toggle relocated (UAT):** REMOVED from ProfileModal — profile changes and OTP requests always send immediately (the axios interceptor no longer stamps `X-Delivery-Preference` globally). NEW shared `DeliveryPrefRadio` (Urgent ⚡ / Evening digest 🕓, localStorage-sticky) sits on the three material-transaction forms — **IssuePage + ReceivePage** (batch-card header next to "Submit batch to HOD") and **ReturnPage** (next to "Post return"). Only `useLedgerPost` (receipts/consumption/returns/adjustments singles) and `useBulkEntry` send the header, via the new `client.deliveryHeaders()` (header omitted entirely when "urgent" = backend default).
- **Dual-OTP phone change (UAT: the old flow never verified the NEW number — a typo could lock the user out of WhatsApp alerts):** `phone_otp` gains **`stage`** ('old'|'new', alembic **`d6b0e72f51a8`** = new single head; mirror stamped `c3a9d51e42b0` first — its schema was already there in-place — then upgraded). Flow: number on file → code 1 to the **OLD** number (`stage='old'`, authorizes); verifying it saves NOTHING — it issues code 2 to the **NEW** number (`stage='new'`, proves deliverability); only THAT verify commits `users.Phone_Number`. First-time setup skips stage 'old'. Single-active-code rule spans both stages (`_issue_phone_code` helper); `verify-otp` returns `{updated:false, stage:'new', sent, …}` mid-flow. ProfileModal shows a 2-step Steps header (Authorize → Verify new) and words each stage; admin direct edit (no OTP) unchanged.
- **Tests:** suite X dual-OTP rework (**+3**: stage='old' + old-number-only dispatch, authorize → not-saved + code 2 to the new number, DB unchanged until stage='new' verifies, then commit) · suite AC **+1**: PDF export = `%PDF-` magic, ≥1 page, application/pdf, attachment filename.
- **Gates:** service_tests **591/0** (×2 consecutive — idempotent) · parity_check **5/5** · frontend build ✅ · alembic single head **`d6b0e72f51a8`**.

### 2026-07-11 (HOD portal) · actor=interactive · branch=`main` · 📊 Executive Summary tab (PDF + Excel)
- **Backend `backend/api/exec_summary.py`** (`require_roles("hod")` = hod+admin, read-only): `GET /hod/executive-summary?date_from&date_to[&site_id]` assembles the whole period picture in one payload — ledger KPIs+detail (receipts/consumption/returns; count, qty, **prev-window delta %, 7-day daily average**), **SQM done** (mh_production) + **manpower present/absent/man-hours** (mh_timesheets vs active mh_employees), PR pipeline (raised-in-period + open-by-state), PO status (+open/overdue à la KPI hero), **delivery plan** (DNs in flight + po_assignments due within 7 days), **actions taken vs pending** (posted/rejected/DN decisions vs pending_issues, DN queue, SMR, draft PRs), **achievable-SQM capacity per equipment AND per system code** — a READ-ONLY run of the frozen SME engine (`_snapshot_rows` → `build_model` → `cascade_allocate`, strict-bottleneck rate × remaining SQM, bottleneck material named), and cross-site enquiries. HODs pinned to their site (fail-closed on siteless), admins choose a site or all. `…/export.xlsx` renders 14 styled sheets via the existing `reports.to_xlsx_sheets`.
- **Frontend `ExecutiveSummaryPage.tsx`** (HOD nav → "Executive Summary", route `/hod/executive-summary`): KPI hero with trend tags, section cards (movements, manpower, procurement, delivery plan, actions, capacity with Coverage progress bars, cross-site), RangePicker default **today** with Today/Yesterday/7-day/month presets + free from–to, admin site selector, **Download PDF** = dedicated A4 print stylesheet (letterhead, nav hidden, cards break-inside avoid) via the browser engine, **Download Excel** = server workbook stream.
- **Gotchas fixed while building:** asyncpg types `x::date BETWEEN :p` binds as dates — those three queries now receive real `date` params; `to_xlsx_sheets` width calc chokes on ragged rows (`[]` spacer → `["",""]`).
- **Tests:** NEW suite **AC (8 checks)** — all sections present · **exact** site-scoped receipts KPI vs SQL truth · manpower present+absent==active invariant · 422 on malformed/inverted dates · store-keeper 403 · admin all-sites/one-site · xlsx magic bytes + attachment filename · capacity math strict-bottleneck unit test (50%-covered material caps 100 SQM at 50).
- **Gates:** service_tests **587/0** · parity 5/5 · frontend build ✅ · alembic head unchanged `c3a9d51e42b0`.

### 2026-07-11 (Phase 6) · actor=interactive · branch=`main` · 📥 Inbound webhooks, dynamic notification scheduling, WhatsApp self-service
- **Inbound webhook router (`backend/api/webhook.py`, mounted at BOTH `/whatsapp/webhook` and `/api/v1/whatsapp/webhook`):** GET = Meta's verification handshake (echoes `hub.challenge` only on a constant-time `WHATSAPP_WEBHOOK_VERIFY_TOKEN` match); POST enforces the **X-Hub-Signature-256 HMAC** whenever `WHATSAPP_APP_SECRET` is set. Senders are canonicalised to +E.164 and matched against `users.Phone_Number` — unregistered numbers are **logged and silently dropped** (no reply → no cost, no probe signal); verified users get role/site loaded and can run **`STOCK <SAP>`** (site-scoped via ROLE_META level: ≥3 = global `SQL_LIVE_STOCK`, scoped roles = their `Site_ID` over `SQL_SITE_STOCK`, fail-closed on siteless accounts) and **`RESET PASSWORD`** (10-char temp credential bcrypt'd into `password_hash`, ALL refresh sessions revoked, audited). Replies are free-form **session texts** (new `wa.send_session_text`, `type:"text"` — always inside the 24h window because they answer an inbound message); handler ACKs 200 even for dropped messages so Meta never redelivers.
- **OTP handshake hardening:** `POST /auth/phone/request-otp` now dispatches the 6-digit code to the **OLD (currently registered) number** — possession proof, so a stolen web session can't silently redirect WhatsApp alerts; first-time setup (no number on file) bootstraps to the new number. Response gains `sent_to: "current"|"new"`; ProfileModal words both cases. Admin direct edit (PATCH /admin/users, no OTP) unchanged + still covered by suite X.
- **Dynamic notification engine:** new table **`pending_summary_notifications`** (alembic **`c3a9d51e42b0`**, new single head; NEW-STACK-ONLY contract like phone_otp; mirror created in place). `dispatch()` gains `delivery=` + a request-scoped **`X-Delivery-Preference`** contextvar set by middleware in main.py — "urgent" (default) sends immediately, "evening" stages one row per resolved recipient username; **critical events always send immediately**. The **batch aggregator** (`send_evening_digests`) claims rows FOR UPDATE SKIP LOCKED, groups by recipient, compiles a •-bulleted digest (`_compile_digest`, newline-free for Meta #132000, capped ≤1024 with an explicit "(+N more)" tail), sends ONE **`gi_evening_summary`** template ({{1}} date+count header, {{2}} digest) per person, and marks rows `processed_at` + `digest_outbox_id` **only after a successful send** (failures stay pending → retry next run; phoneless recipients retired with a warning). Daily **16:00 local** lifespan task (`digest_loop`, `GI_DIGEST_HOUR/MINUTE` overridable, `GI_SCHEDULER=0` escape hatch) + admin console `GET /admin/digests/pending` / `POST /admin/digests/run`. Frontend: axios interceptor sends the header from localStorage; ProfileModal gains the Urgent/Evening-digest Segmented toggle.
- **Tests:** suite X reworked for the old-number flow (bootstrap → new number; change → code to OLD number, verify commits; +2 checks) · NEW suite **AB `test_webhook_and_digest` (17 checks)**: handshake echo + wrong-token 403 (on the /api/v1 alias), bad-HMAC 403, unregistered-sender drop (no outbox row), STOCK session-text reply (site-scoped, recorded sent/text), help fallback, RESET PASSWORD (temp password **actually logs in**, sessions revoked, hash restored), evening staging (2 staged, 0 sent), critical bypass, compiler formatting + (+N more) cap, aggregator (ONE gi_evening_summary with both bullets, rows processed + linked, second run no-op), and the X-Delivery-Preference header end-to-end through a real `POST /xsite`.
- **Gates:** service_tests **579/0** (×2 consecutive — idempotent) · parity_check **5/5** · frontend build ✅ · alembic single head **`c3a9d51e42b0`**. Operator TODO: approve `gi_evening_summary` (Utility, TWO body vars, lang `en`), set `WHATSAPP_WEBHOOK_VERIFY_TOKEN` + `WHATSAPP_APP_SECRET` in deploy/.env, subscribe the webhook URL in Meta App Dashboard.

### 2026-07-11 (UAT hotfix 2) · actor=interactive · branch=`main` · 🧯 Graceful handling of Meta sandbox error #131030 (recipient not whitelisted)
- **Context:** in Meta Development/Sandbox mode, sends to numbers not on the app's allowed-recipient list fail with Graph error `#131030`. Previously the raw Graph JSON blob (`400: {"error":{...}}`) flowed verbatim into the outbox row and the request-otp response.
- **`services/whatsapp.py`:** new `_graph_error(status, body)` normaliser used by both live-HTTP boundaries (`_post_message`, `_upload_media`) — parses the Graph error JSON into `{ok, code, error}`, logs a clear `gi.whatsapp` warning, and maps **#131030 → "WhatsApp delivery failed: Destination number is not whitelisted in the Meta Developer Sandbox console…"** (raw Meta detail kept bracketed with the code, e.g. `[400 (#131030): …]`, so the outbox row stays fully diagnostic). Unknown codes keep the `status (#code): detail` form.
- **Endpoints/lifecycle:** no HTTP-contract change needed — verified the failure already cannot break business flows: `_post_message` never raises (errors return as dicts), `dispatch()` is best-effort/swallowed, and `POST /auth/phone/request-otp` returns **200 `{"sent": false, "error": <friendly>}`** with the code row still created; the outbox row is marked **failed** with the sandbox note. ProfileModal already renders `res.error`, so the descriptive message reaches the user with zero frontend changes.
- **Tests:** suite X (+4 checks): `_graph_error` #131030 mapping (friendly text + `400 (#131030)` detail) · request-otp under a mocked sandbox rejection → 200/sent=false (no generic 4xx/5xx) · descriptive message in the response · outbox row FAILED containing "Sandbox" + "131030".
- **Gates:** service_tests **560/0** (was 556) · alembic head unchanged `b8d2f4a61c07`.

### 2026-07-11 (UAT hotfix) · actor=interactive · branch=`main` · 🔌 Live WhatsApp was dead on bare-metal runs — env-file loading + template language fix
- **Symptom:** after the backend restart, the OTP modal showed *"WhatsApp is not configured on the server"* (request-otp → 503) and no workflow notification pushed, despite a fully populated `deploy/.env`.
- **🐛 Root cause 1 — env file never loaded:** nothing in the backend ever read `deploy/.env`; that file is only injected by docker-compose in production. A bare `uvicorn backend.api.main:app` saw no `WHATSAPP_*` at all → `wa.enabled()` False → `dispatch()` silently skipped WhatsApp everywhere. **Fix:** `backend/api/config.py` now loads repo-root `.env` then `deploy/.env` at import (python-dotenv, `override=False` so real process env — compose/systemd/CLI — always wins). `GI_DOTENV=0` skips the loader; **service_tests pin it** before importing the app so CI can never pick up a developer's real token (un-mocked suites would otherwise hit Meta live).
- **🐛 Root cause 2 — template language mismatch:** with credentials finally loaded, Meta returned `404 #132001 "template name does not exist in en_US"` for every template. A live probe of the name×language matrix showed **all four `gi_*` templates are approved in language `en`**, while `deploy/.env` carried the legacy `WHATSAPP_TEMPLATE_LANG=en_US`. Also confirmed: `gi_otp_code` was never created (the operator's rename to **`gi_profile_update`** is the real OTP template — env override handles it) and legacy `alert_notification` no longer exists (harmless: nothing calls `send_text`). **Fix:** `deploy/.env` → `WHATSAPP_TEMPLATE_LANG=en` (`.env.example` already said `en`).
- **Live end-to-end proof (temporary script, real Meta calls, target `+966569233053`):** OTP via `gi_profile_update` → **HTTP 200**, wamid returned, outbox #338 SENT · `gi_action_required` dispatch-style alert → **HTTP 200**, outbox #339 SENT · live server flow `POST /auth/phone/request-otp` on :8000 → **HTTP 200 {"sent":true}**, outbox #340 SENT (test `phone_otp` row deleted after; before the lang fix the same sends were HTTP 404 #132001, recorded as FAILED #336–#337 — nothing swallowed).
- **Heads-up:** the loader now also supplies `JWT_SECRET` from `deploy/.env` to bare-metal runs — existing dev-signed sessions get a one-time logout.
- **Gates:** service_tests **556/0** re-run WITH `deploy/.env` present (determinism proven) · alembic head unchanged `b8d2f4a61c07`.

### 2026-07-10 (UAT · Phase 5) · actor=interactive · branch=`main` · 🚚 Production cutover migration scripts (+ legacy data-loss fix)
- **`scripts/migration/cutover_migrate.py`** — the one-shot production migration, layered on the proven core copier (`backend/migrate_sqlite_to_postgres.py`, the dual_ci workhorse): pre-flight (SQLite `integrity_check`, refuses a non-empty target without `--wipe`) → load (models.py contract, rowid→id preservation for the 3 ledger tables, type coercion, chunked, PG sequence resets) → post-load (**alembic_version stamped to head**; users/pending_users/employees phones normalised to **+E.164**, unparseable reported-not-destroyed) → verification battery (per-table row parity · dual_ci semantic aggregates · **UOM-conversion integrity** (factor>0, no dupes, orphan SAPs) · soft-FK orphan scan, advisory unless `--strict`). `--verify-only` re-checks without reloading. **`scripts/migration/README.md`** = the cutover-day runbook (freeze → backup → load → role SQL → VACUUM → point API → smoke gates).
- **🐛 DATA-LOSS BUG found by the dry-run:** models.py silently dropped live legacy columns — `inventory.Sl_No` (**293 real values**), `consumption.status` + `consumption.WBS`, `pending_issues.Technician` — plus a **case bug**: consumption/receipts declare `"WBS"` (uppercase) in SQLite while models used lowercase `wbs` → the copier saw two different columns. Fixed in models (`wbs = Column("WBS", Text)` + 5 preserved legacy columns), Alembic `b8d2f4a61c07` (rename + adds; **new single head**), mirror ALTERed in place, and `rep_wbs` report SQL updated to `"WBS"`.
- **End-to-end validated:** ran the script against a scratch PG database (`gihub_cutover_test`, dropped after) — **CUTOVER ✅ VERIFIED under `--strict`**: 70 tables copied, 64 row-count parities, 5/5 semantic aggregates, 0 UOM issues, 0 soft-FK orphans, **0 dropped columns** (was 5 tables' worth), Sl_No 293/293 landed, alembic stamped.
- **Gates:** service_tests **556/0** · parity 5/5 · frontend build ✅ · alembic single head `b8d2f4a61c07`.

### 2026-07-10 (UAT · Phase 4) · actor=interactive · branch=`main` · 🧪 Automated notification QA — every pathway → whatsapp_outbox (+2 real bugs fixed)
- **NEW suite AA `test_notification_qa` (26 checks):** forces `+966569233053` onto every test role (restored after), sets `WHATSAPP_ESCALATION_TO` to the same target as the role-broadcast catch-all, mocks Meta HTTP, then fires **22 pathways end-to-end**: entry staged / FEFO override / HOD approval feedback · MTC upload · PR submit · PO assign · reschedule raise+decide · vendor return · force-close · DN submit→logistics→HOD→ship (4 events) · SMR create+approve · cross-site request + >5 escalation + decision · lot quarantine · feedback update · report-document delivery. Asserts per-event SENT rows, **zero failed/unresolved rows across the sweep**, digits-only `to` = target, and the **dual-write invariant** (every dispatch pathway also wrote its in-app twin). Loans/overdue, OTP and email pathways stay covered by suites Y/X/W. All fixtures + the sweep's own in-app rows cleaned in `finally` (deleting PR/SMR fixtures frees their sequence numbers, so leftover notifications would collide with suite A's exact counts — verified idempotent across two consecutive runs).
- **🐛 Bug 1 (found by the sweep):** DN `dn_hod_approved` / `dn_rejected` (×2) dispatched with `recipient_warehouse` ONLY — `notify()` no-ops without a user/role, so warehouse-targeted DN events sent WhatsApp but silently skipped the in-app bell row. Fixed: `recipient_role="warehouse_user"` + warehouse narrow at all 3 sites (matches `assign_po`'s pattern and the legacy visibility rule).
- **🐛 Bug 2 (test-env):** `assign_po` requires an ACTIVE warehouse — the mirror had none matching, so the pathway silently early-returned. Suite now provisions a synthetic active warehouse (`WH-SVC-QA`).
- **Gates:** service_tests **556/0** (twice, idempotent) · parity 5/5.

### 2026-07-10 (UAT · Phase 3) · actor=interactive · branch=`main` · ✨ QoL polish — barcode/QR picking, smart defaults, Open-POs KPI hero
- **Barcode/QR material picking:** `QrScanner` generalised (`formats` prop — BarcodeDetector now scans code_128/EAN/UPC/ITF/… alongside QR; jsQR fallback stays QR-only; manual-entry placeholder configurable). New `lib/barcode.ts` (`matchScanToSap`: exact → case-insensitive → `SAP:<code>` payload → longest embedded code ≥4 chars). Issue + Receive material pickers gained a scan button (clears the category filter when the scanned item sits outside it; unknown scans warn instead of silently failing). Decode stays 100 % client-side.
- **Smart last-entry defaults:** new `lib/smartDefaults.ts` (localStorage per form, strings only, corrupt-safe). Issue remembers Site/Work Type/Issued By, Receive remembers Site/Supplier, Return remembers Site/Reason — applied via `initialValues`; volatile fields (material/qty/lot) deliberately never remembered.
- **Open-POs KPI hero:** LogisticsPage Purchase Orders tab opens with 4 stat cards — Open POs · Overdue delivery (Expected_Delivery < today) · Partially delivered · Delivered/closed — computed from the loaded list; each card is a click-to-filter toggle on the table.
- **Gates:** frontend build ✅ (backend untouched this phase).

### 2026-07-10 (UAT · Phase 2) · actor=interactive · branch=`main` · 🔍 Global search & filtering + Purchase Request browse page
- **Generic entity search:** `crud.make_read_router` list endpoint gains `q` (case-insensitive ILIKE across every emitted TEXT column — SAP codes, names, categories, lot/PR/PO numbers, remarks) → live on ALL read entities (/inventory /receipts /consumption /returns /lots /purchase-orders /purchase-requests /equipment). Site pinning still applies under `q`.
- **Derived stock search:** `/stock/live|by-site|lots|expiring` gain `q` (SAP + Lot_Number on the view, description/category via an inventory-join subquery) and `category` (exact match via inventory). New **`GET /meta/categories`** (distinct trimmed inventory categories) feeds the dropdowns.
- **PR page (PO-page standard):** `pr_master` registered as read entity **`/purchase-requests`** (site-scoped, read-only) + `entities.ts` entry (minLevel 2) → appears under Records in the nav manifest and renders via the same RecordsPage/BrowseTable as Purchase Orders.
- **Frontend:** `BrowseTable` gains `searchable` (Input.Search → `q`) + `hasCategory` (Select ← `useCategories`) — enabled on all 4 Stock tabs and every Records page (category on Inventory). Entry forms (Issue/Receive/Return) gain a **Category** select that narrows the material picker client-side (text search stays inside the picker).
- **Tests:** NEW suite Z `test_search_filters` (9 checks: categories meta, q narrows + nonsense→0, SAP-code match, stock-view join search, category filter, scope-pinning under q, PR browse shape + q). **service_tests 531/0 · frontend build ✅.**

### 2026-07-10 (UAT · Phase 1) · actor=interactive · branch=`main` · 🚑 Critical UAT fixes — phone format, timezone, loan notifications, snapshot render
- **Global phone format (+E.164):** canonical storage is now `+<country><digits>` project-wide. `auth.normalize_phone` (was `_normalize_phone`, digits-only) validates 8–15 digits / no leading 0 and PREPENDS '+'; applied at every write path — OTP request/verify, `POST/PATCH /admin/users` (admin create/update now 422 on malformed), `POST /auth/register`. The Meta Graph boundary strips the '+' via `whatsapp._meta_to()` in all payload builders (outbox `to_number` keeps the canonical form). Frontend `+`-format validation on ProfileModal, UsersPage, LoginPage register, ReturnablesPage borrower phone.
- **Profile modal bugs:** displayed number no longer double-prefixes '+' (showed `++966…` for admin-set numbers); query error now surfaces an explicit "API may be running an older build" alert instead of a silent **"none set"** (the UAT "none set"/"Not found" pair is exactly what a stale pre-7c backend produces — `/auth/phone` 404s); number refetches on modal open; `request-otp` checks WhatsApp is configured BEFORE creating a code row (was: commit-then-503) and returns the send error detail.
- **Timezone (3 h offset):** two-sided fix. Frontend `ReturnablesPage` sent the due-back picker value via `.toISOString()` (→ UTC wall-clock, `21:59`→`18:59`); now sends the LOCAL `YYYY-MM-DDTHH:mm:ss`. Backend `entry._parse_dt` stripped tzinfo from aware inputs WITHOUT converting (kept UTC wall-clock); now `astimezone().replace(tzinfo=None)` → any old client still lands local. Given/Due render via dayjs local format.
- **Loan/return notifications (new):** loan create → in-app `loan_created` to the site SKs + WhatsApp **direct to the borrower** (status_update template); mark-returned → `loan_returned` in-app + borrower confirmation; overdue sweep now ALSO chases the borrower (critical_alert) alongside the existing SK dispatch, still deduped via `whatsapp_alert_sent`.
- **Snapshot render:** `ReturnPage` was missing `ItemSnapshot` entirely (only Issue+Receive had it) — added with the same `Form.useWatch` wiring; `ItemSnapshot` now shows an explicit warning on query error instead of rendering nothing.
- **SK "Not found" audit:** wrote a route auditor (scratchpad) cross-checking all **164** unique frontend `api.*()` calls against the **243**-route OpenAPI table — zero mismatches; consumption single+bulk verified 201 in-process with exact frontend payloads and real mirror data (`sap_exists` trims both sides; 0 untrimmed SAP codes). Conclusion: the UAT 404s match a stale backend behind the new frontend; every reachable defect found in the sweep is fixed above.
- **Tests:** suite X updated for canonical +E.164 (+3 checks incl. digits-only Meta `to` + admin 422); NEW suite Y `test_returnables_notify` (10 checks: verbatim local storage, Z-input → local conversion, borrower/SK notifications on loan/return/overdue, dedupe flag). **service_tests 522/0 · frontend build ✅.**

### 2026-07-09 (Phase 7c · chunk 3) · actor=interactive · branch=`main` · 📲 Phone-number self-service (WhatsApp OTP) + admin override
- **New table `phone_otp`** (model + Alembic `a1e8c4d20f9b`, single head; created in the mirror via checkfirst) — NEW-STACK ONLY, holds short-lived **bcrypt-hashed** 6-digit codes; 10-min TTL, single-use, max 5 attempts.
- **`auth.py` endpoints:** `GET /auth/phone` (my number), `POST /auth/phone/request-otp` (normalize E.164-ish → generate via mockable `_gen_otp()` → hash + store, supersede any prior active code → send to the NEW number via `wa.send_otp` using the `gi_otp_code` template) and `POST /auth/phone/verify-otp` (checks expiry/attempts/hash → on success writes `users.Phone_Number` + audits `PHONE_UPDATED`). The number changes ONLY after a code verifies. `wa` imported lazily inside the handler to avoid the auth↔whatsapp↔ledger↔stock import cycle.
- **`whatsapp.send_otp`:** otp_code template send with a **redacted outbox preview** (the code lives only in `payload_json`, the message actually sent).
- **Admin override (already present):** `PATCH /admin/users/{username}` sets any user's `Phone_Number` directly, no OTP (UsersPage already exposes the field).
- **Frontend:** `ProfileModal` (two-step: enter number → 6-digit code) opened from a new profile button in the shared `AppLayout` header (every portal); hooks `useMyPhone` / `useRequestPhoneOtp` / `useVerifyPhoneOtp`.
- **Tests:** suite X (`test_phone_otp`, 12 checks) — Meta HTTP mocked + `_gen_otp` monkeypatched: request → redacted-preview send, single-active-code rule, wrong code → 400, correct code → number saved, reused code → 404, admin override with no OTP. **service_tests 510/0 · parity 5/5 · frontend build ✅ · alembic single head a1e8c4d20f9b.**

### 2026-07-09 (Phase 7c · chunk 2) · actor=interactive · branch=`main` · 🔔 In-app notification center parity (bell)
- **No new table:** the requested `in_app_notifications` already exists as **`app_notifications`** (cols `recipient_user`/`recipient_role`+site/warehouse, `title`/`body`, `link_page`, `read_at`, `created_at`) with its own `services/notifications.py` + `/notifications` router (list / unread-count / mark-read / read-all) and a **`NotificationBell`** mounted in the single shared `AppLayout` header → present in **every portal** (all authenticated routes render through one layout). Reused it rather than duplicating.
- **Parity invariant:** chunk 1's `dispatch()` writes an identical in-app row for every WhatsApp send, so the bell now mirrors the WhatsApp stream for the receiving user/role.
- **Live badge:** `useUnreadCount` gains a gentle 60s visible-tab `refetchInterval` (matches `useWorkQueues`) so an alert raised by someone else surfaces without a manual reload.

### 2026-07-09 (Phase 7c · chunk 1) · actor=interactive · branch=`main` · 🔔 Reusable WhatsApp templates + unified dispatch() + comprehensive trigger wiring
- **Goal:** make notifications ubiquitous — every significant action fires BOTH an in-app row and (best-effort) a WhatsApp message, via a small set of reusable Meta templates instead of ad-hoc text.
- **Templates (`services/whatsapp.py`):** new `send_template(template_key, variables)` + `_TEMPLATES` map of 4 logical keys → env-overridable Meta names: `action_required`→`gi_action_required`, `status_update`→`gi_status_update`, `critical_alert`→`gi_critical_alert`, `otp_code`→`gi_otp_code` (first three take 2 body vars, otp takes 1). `_template_payload` flattens params (Meta #132000) + caps 1024. `resolve_numbers(recipient_user|role|site|warehouse)` maps the in-app recipient descriptor → phone numbers, de-duped/capped, with an opt-in `WHATSAPP_ESCALATION_TO` catch-all for role/warehouse broadcasts (mirrors `hod_numbers`). Legacy `send_text`/`alert_notification` kept for back-compat but no longer used by triggers.
- **Dispatcher (`services/notifications.py`):** new `dispatch()` = `notify()` (always) + best-effort WhatsApp to the same recipient(s). Skipped entirely when WhatsApp isn't configured (`enabled()` False) → no failed-row spam; any send failure swallowed so messaging never breaks the business action. `related_ref` coerced to str once (app_notifications + outbox are Text).
- **Wired (notify→dispatch, +new):** entry_staged, entry_approved/rejected (single+bulk), DN pending-logistics/pending-hod/approved/rejected/shipped, PR submitted, PO assigned, reschedule raised/decided, force-close, vendor-return raised (warehouse + disposition + procurement), SMR created/approved, cross-site requested/decided, feedback-updated, report-ready, SLA nudge, returnable-overdue, FEFO-override; **new triggers:** MTC uploaded (→ logistics) and lot quarantine/dispose/release (→ site store keepers). xsite>5 escalation migrated to the `critical_alert` template.
- **Tests:** suite V (`test_whatsapp_outbox`) now enables dummy WhatsApp creds (HTTP still mocked) so dispatch()-driven triggers actually send; asserts the dual-write invariant (in-app + outbox for one event), the `gi_critical_alert` template name, and robust body-param matching. **service_tests 498/0 · parity 5/5.**
- **Env:** `deploy/.env.example` gains `WHATSAPP_TPL_ACTION/STATUS/CRITICAL/OTP` (defaults `gi_*`).

### 2026-07-09 (Phase 7b · chunk 2) · actor=interactive · branch=`main` · 📧 Native SMTP email outbox + parked email triggers + Email Console
- **Files:** `backend/models.py` + `backend/alembic/versions/…f7d4a20b88c3…`
  (`email_outbox`, NEW-STACK-ONLY) · `backend/api/services/emailer.py` (new SMTP
  client + outbox; named *emailer* to stay clear of the stdlib `email` package) ·
  `backend/api/entry.py` (MTC-missing alert) · `backend/api/logistics.py`
  (vendor-return draft) · `backend/api/console.py` (Email Console endpoints) ·
  `backend/api/service_tests.py` (suite W, 10 checks, **SMTP mocked**) ·
  `frontend/src/pages/AdminConsolePage.tsx` (Email tab) · `deploy/.env.example` · this doc.
- **Architecture:** mirrors the WhatsApp outbox — NOT a port of the legacy SQLite
  mailer. stdlib `smtplib` via `asyncio.to_thread`; `_smtp_send` is the single
  network boundary (monkeypatched in tests). Env: `SMTP_HOST/PORT/USER/PASS/FROM/
  STARTTLS` + `EMAIL_LOGISTICS_TO` (users have no email column → fixed logistics
  inbox, same model as the legacy mailer). `enabled()` false ⇒ queue as failed.
- **Triggers (both parked items, best-effort, never mask the primary result):**
  MTC gate blocks a Rubber receipt → 422 still returned AND a "chase the MTC"
  email to the logistics inbox (single + bulk paths); vendor return raised →
  ready-to-forward Logistics email draft (post-commit).
- **Console:** `GET /admin/email` (+ status counts + configured flag, body incl.
  for preview), `POST /admin/email/{id}/retry`. UI: AdminConsole "Email" tab next
  to WhatsApp (status filter, expandable body preview, Retry).
- **Gates:** service_tests **495/0** (+10) · parity 5/5 · frontend build ✅ ·
  alembic single head f7d4a20b88c3.
- **ALL parked Meta-hold items (WhatsApp + email) are now COMPLETE.**

### 2026-07-09 (Phase 7b · chunk 1) · actor=interactive · branch=`main` · 📱 WhatsApp TEMPLATE messages (24h-window fix)
- **Files:** `backend/api/services/whatsapp.py` (`_text_payload` → template) ·
  `backend/api/service_tests.py` (suite V payload assertion) · `deploy/.env.example` · this doc.
- **Why:** free-form text is only deliverable inside Meta's 24-hour customer-service
  window. Alert sends now use `type: "template"` — an approved Utility template with
  ONE body variable `{{1}}` carrying the alert text (newlines flattened per Meta
  #132000; `hello_world` special-cased with no components for smoke tests).
  Configured via `WHATSAPP_TEMPLATE_NAME` (default `alert_notification`) +
  `WHATSAPP_TEMPLATE_LANG` (default `en`). Report-document sends unchanged
  (documents outside 24h need a document-header template — noted, not built).
- **Gates:** service_tests **485/0** (+1 template assertion) · frontend build n/a (backend-only).

### 2026-07-09 (Phase 7) · actor=interactive · branch=`main` · 📱 Native v2 WhatsApp outbox + Meta Cloud API (Meta-hold LIFTED)
- **Files:** `backend/models.py` + `backend/alembic/versions/…e5c3f19a77b2…` (new
  `whatsapp_outbox` table, NEW-STACK-ONLY) · `backend/api/services/whatsapp.py` (new
  Meta client + outbox) · `backend/api/console.py` (xsite>5 escalation + WhatsApp
  Console endpoints) · `backend/api/entry.py` (FEFO-override alert) · `backend/api/reports.py`
  (report delivery) · `backend/api/service_tests.py` (suite V, 11 checks, **Meta
  mocked**) · `frontend/src/pages/{AdminConsolePage,ReportsPage}.tsx` · `deploy/.env.example` · this doc.
- **Architecture:** from-scratch async impl — does NOT reuse the legacy SQLite
  `whatsapp_worker.py`. `whatsapp_outbox` logs every message + its exact Meta
  payload + status (pending→sent|failed). The two live-HTTP boundaries
  (`_post_message`, `_upload_media`) are the only network calls — monkeypatched in
  tests so CI never hits Meta. Creds from env (`WHATSAPP_PHONE_NUMBER_ID/TOKEN/
  API_VERSION/ESCALATION_TO`); `enabled()` false ⇒ messages queue as failed.
- **Triggers (best-effort, post-commit, never fail the primary action):**
  cross-site request > 5 units → target-site HOD; FEFO override on an issue → HOD;
  report delivery (`POST /reports/{key}/whatsapp`) uploads the rendered doc + sends.
- **Console:** `GET /admin/whatsapp` (+ status counts + configured flag),
  `POST /admin/whatsapp/{id}/retry`. UI: AdminConsole "WhatsApp" tab (list, status
  filter, retry) + a "WhatsApp" send button on every ReportsPage report card.
- **Schema:** `whatsapp_outbox` added to models + Alembic (down_revision
  d4f1a27c8e90; single head e5c3f19a77b2). dual_ci leaves it empty (new-stack-only).
- **Gates:** service_tests **484/0** (+11) · parity 5/5 · frontend build ✅ · alembic single head.
- **Phase 7 COMPLETE — every parked Meta-hold item is now shipped.**

### 2026-07-09 (MED · chunk 3) · actor=interactive · branch=`main` · 🔓 Admin lot lifecycle (quarantine / release / dispose)
- **Files:** `backend/api/console.py` (GET /admin/lots, POST /admin/lots/{id}/status) ·
  `backend/api/service_tests.py` (suite U, 7 checks) ·
  `frontend/src/pages/AdminConsolePage.tsx` (Lots tab) · this doc.
- **Backend:** `POST /admin/lots/{id}/status {status, reason}` transitions a lot
  between open ⇄ quarantined and → disposed (terminal); validated (422 bad status,
  409 on disposed/no-op), audited. `GET /admin/lots?status=&sap_code=` lists lots.
  Only status-flip (a disposed lot drops out of FEFO open-lot picking); physical
  write-off stays the stock-adjustment path. Existing `lots` table — no migration.
- **UI:** AdminConsolePage "Lots" tab — status filter + Quarantine / Release /
  Dispose actions per lot.
- **Gates:** service_tests **473/0** (+7) · parity 5/5 · frontend build ✅.
- **Deferred-MED backlog COMPLETE** (vendor-returns · PR line-edit/rename · lot lifecycle).

### 2026-07-09 (MED · chunk 2) · actor=interactive · branch=`main` · 🔓 HOD draft-PR management (line-edit + rename)
- **Files:** `backend/api/services/procurement.py` (update_pr_line, rename_pr) ·
  `backend/api/hod.py` (GET /prs/{pr}/lines, PATCH /prs/lines/{id}, POST /prs/{pr}/rename) ·
  `backend/api/service_tests.py` (suite T, 9 checks) · `frontend/src/api/hooks.ts` ·
  `frontend/src/pages/HodPrsPage.tsx` · this doc.
- **Draft-only guards:** line-edit + rename only act on `logistics_status='site_draft'`
  rows; rename dup-checks the target PR number; both site-scoped for a scoped HOD.
  Editable line fields: Requested_Qty(>0)/Supplier/Est_Cost_SAR/Material_Name/UOM/
  Notes/WBS_Number/Delivery_Date. No migration.
- **UI:** HodPrsPage "Submit to Logistics" table now expands a draft PR to an
  editable line grid (per-line Edit modal) and shows a Rename button on draft rows.
- **Gates:** service_tests **466/0** (+9) · frontend build ✅.

### 2026-07-09 (MED · chunk 1) · actor=interactive · branch=`main` · 🔓 Logistics vendor-returns (raise-to-vendor → reopen PO line)
- **Files:** `backend/api/services/procurement.py` (raise/list/close_vendor_return) ·
  `backend/api/logistics.py` (3 endpoints) · `backend/api/service_tests.py` (suite S,
  7 checks) · `frontend/src/api/hooks.ts` · `frontend/src/pages/LogisticsPage.tsx` · this doc.
- **Backend:** `POST /logistics/vendor-returns {po_number, po_item_id, qty, reason,
  expected_resupply}` — validates qty ≤ delivered-unreturned, inserts a `po_returns`
  row (raised_by_role='logistics'), and **reopens the PO line** (Returned_Qty += qty,
  line_status→open, PO delivered/closed→partially_delivered). `GET /logistics/vendor-returns`
  + `POST .../{id}/close` (resupplied; idempotent). Existing `po_returns` table — no migration.
- **UI:** LogisticsPage "Vendor Returns" tab — raise form (PO → line picker via
  usePoItems, qty/reason/resupply) + a returns table with a Close action.
- **Gates:** service_tests **457/0** (+7) · frontend build ✅.

### 2026-07-09 (Phase 6 · chunk 3) · actor=interactive · branch=`main` · 🔓 Receipt entry guards — MTC gate (Rubber) + pack→base UoM conversion
- **Files:** `backend/api/entry.py` (receipt-meta, MTC upload, guards on single +
  bulk receipts) · `backend/api/service_tests.py` (suite R, 9 checks) ·
  `frontend/src/api/hooks.ts` (useReceiptMeta) · `frontend/src/pages/ReceivePage.tsx` · this doc.
- **MTC gate:** `_apply_receipt_guards` blocks a receipt of a **Rubber** material
  (`inventory.Category` contains "rubber") unless an `mtc_document_id` is supplied.
  `POST /entry/mtc` (multipart) stores the file in `mtc_documents`; after staging,
  the MTC is linked via `pending_receipt_id`. Applies to single + bulk receipts (atomic).
- **UoM conversion:** an `entry_uom` (pack) is converted to the base UoM using
  `uom_conversions.Factor` (unknown pack → 422); the base qty is stored with a
  conversion note in Remarks. `GET /entry/receipt-meta/{sap}` feeds the UI
  (is_rubber, base_uom, conversions). Existing tables — no migration.
- **UI:** ReceivePage add-line form shows a "Receive in unit" select (when
  conversions exist) and an MTC upload (when rubber, blocks add without it); both
  ride each staged row into the bulk submit.
- **Gates:** service_tests **450/0** (+9) · parity 5/5 · frontend build ✅.
- **Phase 6 COMPLETE** (DN two-stage approval · supervisor parity · entry guards).

### 2026-07-09 (Phase 6 · chunk 2) · actor=interactive · branch=`main` · 🔓 Supervisor parity (Intent-vs-Actual UI · cancel-while-pending · live cart stock)
- **Files:** `backend/api/services/supervisor.py` (cancel_smr) · `backend/api/requests.py`
  (intent-vs-actual, stock-check, cancel endpoints) · `backend/api/service_tests.py`
  (suite Q, 7 checks) · `frontend/src/api/hooks.ts` · `frontend/src/pages/SupervisorPage.tsx` · this doc.
- **Intent vs Actual:** `GET /requests/intent-vs-actual` (supervisor/admin; site-pinned
  via site_scope) reuses the existing `rep_intent_vs_actual` and returns JSON rows —
  reports router is level≥2 so supervisors couldn't reach it there. New "Intent vs
  Actual" tab on SupervisorPage.
- **Cancel-while-pending:** `smr.cancel_smr` (owner-only, must be pending_sk) +
  `POST /requests/{id}/cancel`; Cancel button on pending rows in My Requests.
- **Live cart stock:** `GET /requests/stock/{sap}` (reuses `_stock_snapshot`, pinned to
  the supervisor's site); `LineStock` shows current stock + a ⚠ short flag per cart line.
- **Gates:** service_tests **441/0** (+7) · frontend build ✅.

### 2026-07-09 (Phase 6 · chunk 1) · actor=interactive · branch=`main` · 🔓 DN two-stage approval (Logistics date → HOD content → ship)
- **Files:** `backend/api/services/warehouse.py` (submit_dn/decide_dn_logistics/
  decide_dn_hod; ship_dn now gated to hod_approved) · `backend/api/warehouse.py`
  (submit endpoint) · `backend/api/logistics.py` + `backend/api/hod.py` (DN
  list/items/decide) · `backend/api/service_tests.py` (suite P, 7 checks) ·
  `frontend/src/api/hooks.ts` · `frontend/src/components/DnApprovalQueue.tsx` (new,
  shared) · `frontend/src/pages/{WarehousePage,LogisticsPage,ApprovalsPage}.tsx` · this doc.
- **State machine restored** (schema already had the columns — no migration):
  draft → (WH `POST /warehouse/dns/{dn}/submit`) pending_logistics → (Logistics
  `POST /logistics/dns/{dn}/decide`) pending_hod → (HOD `POST /hod/dns/{dn}/decide`)
  hod_approved → (WH ship, now **gated to hod_approved**) in_transit → SK receipt.
  Reject at either gate → 'rejected' (WH resubmits). In-app notify only.
- **UI:** WarehousePage DN actions = Submit / awaiting tags / Ship (only when
  hod_approved); shared `DnApprovalQueue` on LogisticsPage ("DN Approvals" tab)
  and ApprovalsPage ("Delivery Notes" tab) with expandable line items + reason-reject.
- **Gates:** service_tests **434/0** (+7) · frontend build ✅.

### 2026-07-08 (Phase 5) · actor=interactive · branch=`main` · 🔓 Reporting & Dashboard parity
- **Files:** `backend/api/reports.py` (rep_pr_status + registry) ·
  `backend/api/dashboard.py` (new, GET /dashboard/metrics) · `backend/api/console.py`
  (GET /admin/system-overview) · `backend/api/main.py` (register dashboard router) ·
  `backend/api/service_tests.py` (suite O, 6 checks) · `frontend/src/api/hooks.ts`
  (useDashboardMetrics, useSystemOverview) · `frontend/src/pages/{Dashboard,AdminConsolePage}.tsx` · this doc.
- **PR-Status report:** `rep_pr_status` — one row per PR (lines rolled up) with
  workflow/logistics status; registered as `pr-status` (distinct from the
  PO-status report). Appears automatically in ReportsPage (dynamic `useReports`).
- **Dashboard:** `GET /dashboard/metrics` (≥supervisor, site-scoped) → total
  valuation + 3 series (stock-vs-min, burn forecast days-of-cover, top-consumed
  30d). Dashboard.tsx gains a Stock-value KPI + 3 Recharts bar charts.
- **Admin system-overview:** `GET /admin/system-overview` (admin) → DB size,
  transaction counts (receipts/consumption/returns/adjustments/audit + total),
  users/sites, valuation total + by-site. New "Overview" tab in AdminConsolePage.
- **Gates:** service_tests **427/0** (+6) · parity 5/5 · frontend build ✅
  (Recharts deduped into a shared BarChart chunk). Intent-vs-Actual report was
  found ALREADY present (`rep_intent_vs_actual`) — remains a separate backlog item
  for its supervisor UI. SME/Man-Hours untouched.

### 2026-07-08 (Phase I-B) · actor=interactive · branch=`main` · ☁️ Cloudflare Tunnel hijack (existing gi-hub) + rate-limiter CF-Connecting-IP fix
- **Files:** `backend/api/ratelimit.py` (CF-Connecting-IP priority) ·
  `backend/api/service_tests.py` (suite N, 3 checks) · `frontend/vite.config.ts`
  (VITE_TUNNEL mode) · `deploy/cloudflared/config.yml` + `README.md` (new) · this doc.
- **Rate limiter:** `_client_ip` now resolves CF-Connecting-IP → X-Real-IP →
  TCP peer, so remote testers through the tunnel key on their real IP instead of
  the tunnel's single egress IP.
- **Tunnel:** reuse the pre-existing `gi-hub` tunnel
  (`8e2f8d9d-08f4-432e-9857-dee2ff4ebb63`) that served the legacy build — DNS
  already CNAMEs to it, so only the served config changes. Ingress → Vite
  (:5173), which serves the SPA + proxies `/api`→:8000 (single origin; no path
  split because FastAPI has no /api prefix). `VITE_TUNNEL=1` allows the host +
  fixes HMR over TLS. CLI: `cloudflared tunnel --config deploy/cloudflared/config.yml run gi-hub`.
- **Gates:** service_tests **421/0** (+3) · frontend build ✅.

### 2026-07-08 (Phase 4 · chunk 3) · actor=interactive · branch=`main` · 🔓 Manual PO creation + vendor picker (MED gaps)
- **Files:** `backend/api/services/procurement.py` (create_po_manual) ·
  `backend/api/logistics.py` (POST /pos/manual + ManualPO models) ·
  `backend/api/service_tests.py` (suite M, 7 checks) · `frontend/src/api/hooks.ts`
  (useCreateManualPo) · `frontend/src/pages/LogisticsPage.tsx` (Create PO tab +
  VendorPicker) · this doc.
- **Manual PO:** `POST /logistics/pos/manual` — unique PO number, ≥1 free-text
  line (material code OR description), custom Unit_Price (Total_Amount computed),
  optional/**unlisted** PR reference (links pr_master only if it exists +
  submitted), Inco/Payment terms + WBS/Network/Plant per line. Existing tables.
- **Vendors:** reused the generic `/vendors` CRUD (writable lvl≥3). Frontend
  VendorPicker = searchable Select + inline "＋ Add" modal; selecting a vendor
  auto-fills its Default_Inco_Terms / Default_Payment_Terms.
- **Gates:** service_tests **418/0** (+7) · frontend build ✅.
- **Phase 4 complete** (H7 reschedule · H8 force-close · manual PO + vendors).

### 2026-07-08 (Phase 4 · chunk 2) · actor=interactive · branch=`main` · 🔓 H8 force-close (PR/PO/line) + 24h undo
- **Files:** `backend/api/services/procurement.py` (force_close/undo_force_close/
  list_force_closures) · `backend/api/logistics.py` (3 endpoints) ·
  `backend/api/service_tests.py` (suite L, 9 checks) · `frontend/src/api/hooks.ts` ·
  `frontend/src/pages/LogisticsPage.tsx` · this doc.
- **Backend:** `POST /logistics/force-close {target_type: pr|po|line, target_ref,
  reason, notes}` — reason required; captures `prior_state` JSON on
  `po_force_closures` and sets the target to force_closed. `POST
  /logistics/force-close/{id}/undo` restores prior_state if within 24h (window
  computed in-DB via `EXTRACT(EPOCH …)` to dodge tz issues) and not already
  reverted. `GET /logistics/force-closures` returns each row + `age_hours`.
  Tables already existed — no migration. In-app notify only.
- **UI:** reusable `ForceCloseButton` (reason modal) on PO rows, PO line items,
  and PR queue rows; new "Force-Closures" tab with an Undo button that disables
  once the 24h window lapses.
- **Gates:** service_tests **411/0** (+9) · frontend build ✅.

### 2026-07-08 (Phase 4 · chunk 1) · actor=interactive · branch=`main` · 🔓 H7 reschedule workflow (WH/HOD raise → Logistics decide → push date)
- **Files:** `backend/api/services/procurement.py` (raise/list/decide_reschedule) ·
  `backend/api/logistics.py` (GET/POST decide) · `backend/api/warehouse.py` +
  `backend/api/hod.py` (raise endpoints) · `backend/api/service_tests.py` (suite
  K, 8 checks) · `frontend/src/api/hooks.ts` · `frontend/src/pages/{WarehousePage,LogisticsPage}.tsx` · this doc.
- **Flow:** WH raises via `POST /warehouse/reschedule`, HOD via `POST /hod/reschedule`
  (both → `procurement.raise_reschedule`, one pending per PO). Logistics reviews
  at `GET /logistics/reschedules` and decides at `POST /logistics/reschedules/{id}/decide`
  — approve pushes `requested_date` onto `purchase_orders.Expected_Delivery` + the
  PO's assignments. In-app notify only (WhatsApp/email parked). Tables already
  existed (`po_reschedule_requests`) — no migration.
- **UI:** WarehousePage assignment "Reschedule" button + modal; LogisticsPage new
  "Reschedules" tab (approve/reject w/ notes).
- **Gates:** service_tests **402/0** (+8) · frontend build ✅.

### 2026-07-08 (Phase I-A) · actor=interactive · branch=`main` · ☁️ S3 off-box PostgreSQL backups (Req 5 — infra layer)
- **Files:** `deploy/backup/backup-pg.sh` (S3 push) ·
  `deploy/docker-compose.prod.yml` (aws-cli + S3 env on `backup`) ·
  `deploy/.env.example` (AWS_* vars) · `docs/DEPLOY.md` · this doc.
- **Decision (Req 5):** infrastructure layer, not app layer — DR must survive
  the app being down and keeps AWS creds out of the API process (extends the
  existing backup service rather than the report_center daemon).
- **How:** after a successful `pg_dump -Fc`, `push_to_s3()` runs `aws s3 cp`
  (SSE-encrypted) when `AWS_S3_BUCKET` is set; local-only otherwise (clean
  skip). Never fails the backup — records `.last_s3_success`/`.last_s3_failure`.
  14-day LOCAL retention kept; S3 retention = bucket lifecycle policy (documented,
  not scripted). aws-cli installed in the `backup` container (falls back to
  tzdata-only if the package is unavailable).
- **Validation:** `sh -n` backup-pg.sh ✅ · compose YAML ✅ (docker not local —
  CI smoke-build covers image build). No app code touched.

### 2026-07-08 (Phase 3) · actor=interactive · branch=`main` · 🔓 P3: sidebar UX — ⌘K palette + collapsible role-primary groups (Req 3 UX)
- **Files:** `frontend/src/config/nav.tsx` (PRIMARY_GROUP, groupOfPath,
  accessibleNodes) · `frontend/src/components/CommandPalette.tsx` (new) ·
  `frontend/src/components/AppLayout.tsx` (collapsible SubMenus + openKeys +
  header launcher) · this doc.
- **⌘K command palette:** fuzzy jump-to-page over the manifest, access-aware
  (admin shadow included, ignores the curated-default filter so admin can reach
  anything in two keystrokes). Opens via ⌘K/Ctrl-K or the header search button;
  ↑/↓/Enter/Esc keyboard nav. This is what lets the sidebar stay lean.
- **Collapsible groups:** sidebar groups are now collapsible SubMenus (were
  static section headers). The role's PRIMARY_GROUP opens by default
  (progressive disclosure); choice persists (localStorage) and the active
  group is always kept open. Combined with the Phase-0 lean-admin default +
  "All areas" toggle, this completes the Req-3 UX proposal.
- **Verification:** typecheck + build ✅ (live browser smoke deferred — the
  user's own Vite dev server holds :5173; per standing practice we verify via
  build here). service_tests **394/0** (backend untouched this phase).

### 2026-07-08 (Phase 2) · actor=interactive · branch=`main` · 🔓 P2: HOD approval correctness — reject-reason (H5) + auto-draft PR button (H9)
- **Files (frontend only — backends pre-existed):**
  `frontend/src/pages/ApprovalsPage.tsx` (reject-reason modal) ·
  `frontend/src/pages/HodPrsPage.tsx` (auto-draft button) · this doc.
- **H5:** Reject now opens a modal requiring a reason (was hard-coded
  `'rejected by HOD'`). The reason flows through the existing
  `POST /hod/pending/{kind}/{pid}/reject` body → audit row + the submitter's
  rejection notification. Approve unchanged (approve==commit kept per ruling).
- **H9:** `/hod/prs/auto-draft` (backend already existed) now has a button on
  the PR create page too (LowStockPage already had one) — drafts a PR from every
  below-minimum item at the selected site. Reused the existing `useAutoDraftPr`.
- **Not done (per your answers):** two-phase EOD NOT restored (approve==commit
  kept); negative-stock/FEFO stay allow-and-log (no hard block).
- **Gates:** frontend build ✅ · service_tests **394/0** · parity 5/5
  (backend untouched this phase).

### 2026-07-08 (Phase 1) · actor=interactive · branch=`main` · 🔓 P1: SK bulk issue/receipt + item snapshot (Req 1)
- **Files:** `backend/api/entry.py` (POST /entry/bulk, GET /entry/snapshot/{sap}) ·
  `backend/api/service_tests.py` (suite J, 8 checks) ·
  `frontend/src/api/hooks.ts` (useBulkEntry, useItemSnapshot) ·
  `frontend/src/components/{Sparkline,ItemSnapshot}.tsx` (new) ·
  `frontend/src/pages/{IssuePage,ReceivePage}.tsx` (batch grid) · this doc.
- **Bulk:** `POST /entry/bulk {kind, rows[]}` stages a whole batch atomically
  (every row validated up-front; a bad/invalid row stages nothing), one HOD
  notification per site. SK-locked (require_roles admin-inclusive). Issue &
  Receive pages now: add-to-batch → editable grid (edit/delete) → submit-all.
- **Snapshot:** `GET /entry/snapshot/{sap}?site_id=` returns ledger-derived
  current stock + 30-day burn/daily-rate + days-of-cover + a 30-point trend
  (reuses ai/submission_stats.usage_stats). Shown as a compact panel with an
  inline-SVG sparkline (no Recharts → small chunk) on both entry pages.
  Advisory only — honors the allow-and-log ruling (a <14-day "low cover" tag,
  never a block).
- **Gates:** service_tests **394/0** (+8) · parity 5/5 · frontend build ✅.

### 2026-07-08 (Phase 0) · actor=interactive · branch=`main` · 🔓 FEATURE-GAP PROGRAM P0: role-access foundation (Req 3 logic) + AI visibility (Req 2)
- **Files:** `frontend/src/config/nav.tsx` (NEW — single source of truth) ·
  `frontend/src/config/entities.ts` (per-entity access) ·
  `frontend/src/components/AppLayout.tsx` (manifest-driven sidebar + route guard
  + admin "All areas" toggle) · `frontend/src/pages/ApprovalsPage.tsx` (AI
  insight open-by-default on Issues) · this doc.
- **Req 3 (logic):** ported legacy `_can_access` (config.py PAGE_ACCESS +
  main.py _EXACT_ROLE_PAGES/_PAGE_BLOCKED_ROLES) into `nav.tsx` — one manifest
  drives the sidebar AND client route guards. Fixes the leaks: admin no longer
  sees Data Entry by default (curated console + "All areas" reveal = legacy
  admin-shadow); logistics no longer inherits HOD/SME; Records ledger logs gated
  hod+ (inventory all, POs logistics+, equipment {hod,admin}); warehouse back to
  exact {warehouse_user, admin}. Route guard redirects denied paths → role-home.
  API gates already enforced server-side (require_roles admin-inclusive) — this
  makes the UI agree. UX layer (⌘K, role-home ordering) is Phase 3.
- **Req 2:** `SubmissionInsight` moved from click-to-expand to controlled
  `expandedRowKeys` = all issue rows (open by default) + a "review before
  approving" hint. HOD can still collapse.
- **Gates:** frontend build ✅ · service_tests **386/0** ✅ · parity 5/5 ✅
  (backend untouched this phase). Honors locked rulings: SME/Man-Hours nav
  gated but pages untouched; no app-logic behavior change server-side.

### 2026-07-08 (CI/CD) · actor=interactive · branch=`main` · 🚀 DEPLOY INFRA: v2 manual-deploy pipeline + Postgres backup service (NO app code touched)
- **Files touched (deploy/docs/CI only — zero application logic):**
  `deploy/docker-compose.prod.yml` · `deploy/backup/backup-pg.sh` (new) ·
  `deploy/{deploy-v2.sh, health-check.sh, rollback.sh}` (new) ·
  `.github/workflows/deploy-v2.yml` (new) · `docs/DEPLOY.md` · this doc.
- **Why:** the v2 `deploy/` stack had **no automated Postgres backup** despite
  `pg-data-prod` being the system of record (the v1 `backup` service only dumps
  SQLite). Also needed a repeatable, safe cutover/redeploy path.
- **PG backup service:** new `backup` (postgres:16-alpine) runs
  `backup-pg.sh` nightly 02:00 Asia/Riyadh — `pg_dump -Fc` → `pg-backups`
  volume, 14-day retention, `.last_success`/`.last_failure` markers (v1
  convention → Admin Service Health card). Same volume mounted into `api`
  (`GI_BACKUPS_DIR=/backups`) so the console's manual `POST /admin/backup`
  and the nightly dumps unify. Off-box bind stub documented on the volume.
- **v2 deploy pipeline** `deploy-v2.yml` — **`workflow_dispatch` ONLY** (type
  `deploy` to confirm), own concurrency group `hetzner-v2` (cannot collide with
  v1's `hetzner-production`); the existing `deploy.yml` is UNTOUCHED. Gate
  (dual_ci→parity→service_tests→frontend build; **Black advisory/non-blocking**)
  → docker smoke-build (api+web) → SSH `deploy-v2.sh`.
- **`deploy-v2.sh`:** pre-flight → `git reset --hard origin/main` (no rsync;
  server already mirrors the repo) → SHA-tagged image build → `db` up +
  `alembic upgrade head` → **PORT-HANDOVER** (stop v1 `nginx`, free :80/:443) →
  v2 `up -d` → `health-check.sh` (api `/health`<2s · web `/`<400 · alembic at
  head) → success: record SHA + Slack ✅; fail: `rollback.sh` + Slack 🔁.
- **`rollback.sh`:** reverts the port-handover (stop v2 `web`, restart v1
  `nginx` → users back on known-good Streamlit), retags prior-SHA images.
  **Never downgrades the DB schema** (containers/images only).
- **Constraints honored:** no v1 app code / `database.py` / legacy Streamlit /
  `deploy.yml` changes; application-logic freeze intact. Fixed two stale
  `docs/DEPLOY.md` lines while in-file (site-scoping shipped; service_tests
  52→386). **Validation:** `sh -n`/`bash -n` all 4 scripts ✅ · yaml.safe_load
  compose + all 3 workflows ✅ · docker `compose config` deferred to CI
  smoke-build (docker not installed locally). Gates NOT re-run — no importable
  code changed (deploy/docs/CI only); the shared dual-CI doesn't trigger on
  `deploy/**`.

### 2026-07-07 (T1 + bottleneck) · actor=interactive · branch=`main` · 🔓 FREEZE-LIFT FEATURE: strict bottleneck coverage (engine ruling) + Submission Intelligence (final step of the user-approved T4→T3→T2→T1 plan)
- **Files touched:** `backend/api/{sme_engine.py, sme.py}` ·
  `frontend/src/sme/{engine.ts, session.ts, insights.ts}` ·
  `backend/api/sme_parity_golden.json` (REGENERATED) ·
  `backend/api/ai/{submission_stats.py (new), router.py}` ·
  `frontend/src/components/SubmissionInsight.tsx` (new) ·
  `frontend/src/pages/{ApprovalsPage,CrossSitePage}.tsx` ·
  `backend/api/service_tests.py` · this doc.
- **⚖️ STRICT BOTTLENECK ruling (both engines, ONE commit, golden
  regenerated per Canon row 9):** coverage for a (tag, code) — and every
  rollup built from it — is the LEAST-available material's rate, never the
  Σalloc/Σdemand average (3×100% + 45% + 25% ⇒ **25%**, proven by a synthetic
  oracle test). Changed: `compute_feasibility` Completion_Pct (py + ts),
  `_overview_rows` pct, session.ts codeStats/tagStats (tag = worst unit;
  canSqm = Σ unit SQM × bottleneck rate), insights.ts
  pairCoverage/scopeCoverage/materialBalance totals (scope Coverage Area =
  Σ(remaining × bottleneck rate)/Σremaining — area-weighted, never above any
  unit's worst component). Status ✅/🟡/🔴 mechanics unchanged; suite G's
  hardcoded fixture assertions all survive; golden regenerated →
  `parity:sme` **509 ✅** both sides.
- **T1 Submission Intelligence:** `ai/submission_stats.py` computes ALL
  numbers from the ledger (30/60-day per-issue mean/σ, mean daily rate,
  deviation %, z-score, first-time flag, off-pattern weekday; xsite:
  target-site Current_Stock via SQL_SITE_STOCK + days-of-cover now/after).
  `GET /ai/submission-summary?kind=&ref_id=` (staged-issue · xsite, level ≥2)
  phrases the facts with llama3.1:8b (temp 0.2, numbers-locked system
  prompt) when `ai_enabled`+`ai_submission_intel_enabled` and Ollama is
  healthy — EVERY failure path returns the deterministic template ("Usual
  consumption." / "issued N% more than its 30-day average" / "drops from X
  to Y days of cover — short within two weeks"). 15-min cache in `ai_jobs`
  (kind=submission_summary, no schema change). UI: expandable insight rows
  on HOD Approvals→Issues (pilot) and Cross-Site Requests (granting side).
- **Gates:** `service_tests` **386/0** (suite I: guards, 404s, contract,
  stats block, xsite depletion facts — synthetic rows cleaned up) ·
  `parity:sme` 509 · `parity_check` 5/5 · `bug_check` 599/0 · frontend
  build ✅ · `alembic check` ✅ · :8000 restarted.
- **Next:** T1 rollout to the remaining reviewer surfaces (SK request
  review, Logistics PR/PO) after user feedback on the pilot.

### 2026-07-07 (T2) · actor=interactive · branch=`main` · 🔓 FREEZE-LIFT FEATURE: Admin SLA oversight & nudge system (user-approved plan, step 3 of T4→T3→T2→T1)
- **Files touched:** `backend/api/sla.py` (new) ·
  `backend/alembic/versions/…_d4f1a27c8e90_sla_dismissals_table.py` (new) ·
  `backend/models.py` · `backend/api/main.py` · `backend/api/service_tests.py` ·
  `frontend/src/{api/hooks.ts, components/AppLayout.tsx, App.tsx,
  pages/OverdueActionsPage.tsx (new), pages/SmePage.tsx}` · this doc.
- **Schema (user-authorized):** `sla_dismissals` (kind, ref_id, cleared_by,
  cleared_at; UNIQUE(kind, ref_id)) — NEW-STACK-ONLY like auth_sessions/
  ai_jobs; Alembic `d4f1a27c8e90`; `alembic check` clean.
- **Aggregation:** `GET /admin/overdue-actions` (admin-only) UNIONs all 8
  pending queues — the same definitions /meta/work-queues counts: 4× HOD
  staging (`pending_receipts/issues/returns` + `stock_adjustments`
  status=pending_hod), SK requests (`supervisor_material_requests`
  pending_sk), warehouse PO assignments (assigned/acknowledged/partial),
  logistics reschedules (pending) and PR submissions
  (`logistics_status='submitted'`, grouped per PR_Number). Items older than
  the window (default 24h) and not in `sla_dismissals` return with resolved
  responsible users (role × Site_ID / Warehouse_ID, WH falls back to the
  whole role), age-sorted desc.
- **Actions:** `POST …/{kind}/{ref_id}/clear` → dismissal row (409 on
  double-clear) + `SLA_CLEAR` audit. `POST …/{kind}/{ref_id}/notify` →
  re-resolves the item live (404 if no longer pending), one **critical**
  in-app notification per responsible user via the shared service with the
  EXACT template "URGENT — Dear {User Name}, From: Admin. Subject: Action
  required on pending submission {ID/Details}." + `SLA_NOTIFY` audit.
  Gotcha fixed: `session.begin()` after the collector reads →
  "transaction already begun"; switched to execute+commit.
- **UI:** new `/admin/overdue` "Overdue Actions" page (age-sorted table,
  escalating age colors, Notify/Clear per row, responsible-user tags,
  unassigned-scope warning) + **red badge** on the Admin nav (60s poll,
  admin-only fetch).
- **Tabs-boundary fix (user report):** both themes set
  `colorBgLayout: 'transparent'` (body gradient) — the T3 sticky band was
  see-through. Switched sticky header + tab bar to solid
  `token.colorBgContainer` + hairline border.
- **Gates:** `service_tests` **380/0** (12 new suite-H checks: role guard,
  422/404s, 30h synthetic item surfacing + responsible resolution + sort,
  notify recipients + EXACT template + severity, clear/double-clear/hidden;
  synthetic row fully cleaned up; suite-H logins isolated on their own
  X-Real-IP — the shared IP exhausts the 10/min login cap by suite H) ·
  `bug_check` **599/0** · frontend build ✅ · `alembic check` ✅ · :8000
  restarted onto this code.
- **Next action:** T1 (Submission Intelligence / AI summaries) on the user's
  go signal.

### 2026-07-07 (T3) · actor=interactive · branch=`main` · 🔓 FREEZE-LIFT FEATURE: SME sticky header + legacy Excel export layouts (user-approved plan, step 2 of T4→T3→T2→T1)
- **Files touched:** `backend/api/sme_export_layouts.py` (new) ·
  `backend/api/sme.py` · `frontend/src/pages/SmePage.tsx` ·
  `frontend/src/sme/LocationReport.tsx` · `frontend/src/api/hooks.ts` · this doc.
- **Sticky header:** SME page title + site picker pin at viewport top
  (`position: sticky`, z-30) and the Tabs nav pins right below at a
  live-measured offset (ResizeObserver → `top`), theme-aware background via
  `theme.useToken().colorBgLayout` — tabs stay visible over the virtualized
  grids in light AND dark mode.
- **Legacy export layouts:** new `sme_export_layouts.py` is a 1:1 port of the
  legacy writers (`material_estimator_portal.py` ~2040-2640) — deliberately
  **xlsxwriter** (already a dependency; the blueprint's format dicts +
  merge/insert_image geometry translate verbatim, zero re-implementation
  drift; the plan said openpyxl — deviation flagged and justified).
  Every sheet: rows 0-3 logo+meta band, row 4 merged title bar, then:
  - **Equipment Report** — "1. Summary by Equipment" (+-joined system names)
    · "2. Summary by System Code" · "3. Detailed Table" (autofilter), each
    with GRAND TOTAL in the scheme's gold/tinted colors; multi-sheet
    per-location (legacy `_LOC_COLOR_MAP` schemes) + "All Equipment" +
    "All System Codes"; `?tag=` / `?location=` single-sheet variants.
  - **Location Report** — NEW plan-export key `location-report`
    (body.location; oracle-cascaded lines): main alloc table + GRAND TOTAL,
    then the 3 legacy summary blocks (System Code · Equipment · Material).
    `LocationReport.tsx` per-location + All-Equipment buttons now use it.
  - **System Code Report** — Summary sheet + one styled sheet per code
    (overview scheme).
- **Filenames:** every SME export now uses the legacy
  `{stem}_{username}_{date}.{ext}` convention (`legacy_filename()`);
  `postDownloadDocument` now honors Content-Disposition so the browser
  receives the server's name.
- **Verified:** structural verifier (scratchpad `verify_t3.py`) — **27/27**
  against live mirror data: sheet lists, 5-row header, merged title, section
  order/rows (29 equipment · 77 detail rows · ΣSQM 33,323.29), 4 GRAND
  TOTALs in the location sheet, scheme fills (#2D4A6A / #1E5799), autofilter.
  Gates: `service_tests` **368/0** · `parity:sme` **509** · frontend build ✅.
  :8000 restarted onto this code. (Attached old/new files unreadable —
  macOS TCC blocks ~/Downloads; blueprint taken from the legacy generator.)
- **Next action:** T2 (Admin SLA oversight + nudge) on the user's go signal.

### 2026-07-07 (T4) · actor=interactive · branch=`main` · 🔓 FREEZE-LIFT FEATURE: role-based site validation on Request Access (user-approved plan, T4→T3→T2→T1)
- **Files touched:** `backend/alembic/versions/…_c7a2e91f3b55_user_location_columns.py`
  (new) · `backend/models.py` · `database.py` (self-heal only) ·
  `backend/api/{auth,admin,service_tests}.py` ·
  `frontend/src/{api/hooks.ts, pages/LoginPage.tsx, pages/PendingUsersPage.tsx}` · this doc.
- **What:** `/auth/register` now enforces role-conditional site rules —
  scoped roles (`store_keeper`/`supervisor`/`hod`) MUST pick an
  **admin-created** site (`system_settings category='Site'`, same source as
  the console CRUD); unscoped/global roles (`warehouse_user`/`logistics`)
  must NOT carry a site and may give a free-text **Location** instead.
  New public (rate-limited 30/min) `GET /auth/register/sites` feeds the
  pre-login form; the React form swaps Site-select ↔ Location-input on role
  change; the admin Access Requests screen shows the new Location column and
  approval carries it onto the user row.
- **Schema (user-authorized):** `users.Location` + `pending_users.Location`
  (nullable TEXT) — Alembic `c7a2e91f3b55` on PG, POST-rebuild `column_exists`
  self-heal in `database.py` (same placement rule as `totp_*`: the users
  role-CHECK rebuilds recreate from a fixed column list). ⚠️ The rebuilt
  mirror had **no `alembic_version`** (dual_ci creates tables from models) —
  stamped `b3e91d40aa17` then upgraded; `alembic check` clean.
- **Ordering fix found by the suite:** the new site rules initially ran
  before the duplicate-username check, turning the historical
  "existing username → 409" into a 422 — reordered so 409 wins.
- **Gates:** `service_tests` **368/0** (8 new T4 checks: public sites list,
  3 fail-closed 422 paths, 2 happy-path 201s + surfaced `Site_ID`/`Location`,
  register→reject cleanup, X-Real-IP isolation from the 5/min cap) ·
  frontend build ✅ · `alembic check` ✅ · live curl matrix on :8000 verified
  all four responses + row content (probe rows cleaned).
- **Next action:** T3 (SME sticky header + legacy export layout from
  `material_estimator_portal.py:2040-2420`) on the user's go signal.

### 2026-07-07 (later) · actor=interactive · branch=`main` · 🚑 FREEZE-LIFT HOTFIX 2: SME 26→29 fixed at the ROOT (ingestion) + legacy export-parity pack (per-code/per-tag downloads, multi-sheet formats)
- **Files touched:** `scripts/sme_bootstrap.py` · `backend/api/sme.py` ·
  `backend/api/reports.py` · `backend/api/service_tests.py` ·
  `frontend/src/sme/{MatrixReports,ExecutionPlan,TotalOverview,SmeDashboard,ProcurementView,rowsExport}.tsx` ·
  `frontend/src/pages/SmePage.tsx` · this doc.
- **Bug 2 root cause (26 vs 29 equipments + SQM variance):** the morning
  entry below patched the *Progress List* to show the 3 work-area scopes, but
  the root was upstream — `sme_bootstrap.py::_clean_equipment` did
  `dropna(subset=["Equipment_Tag_No.", "Lining_System_Code"])`, silently
  discarding the 23 Excel rows of the 3 tag-less civil areas (**Existing MGA
  Pump Area · Train Unloading MGA Vessel PIT · PPA Storage Tank Area** — the
  original SME DB stored the Name as the tag) and the 5 CBL30 rows carrying
  only a short name. **Fix:** backfill tag←Name and code←short-name (recipe
  map) before the dropna. Re-ran bootstrap in ignore-mode (manual edits +
  `Done_SQM` preserved): `sme_equipment` 65→**77** rows, 26→**29** tags;
  `sme_sqm_progress` 95→102; **SQM totals now reconcile exactly with
  Equipment.xlsx: 33,323.293** across Excel = SQLite = live progress rows.
  Mirror reloaded via `dual_ci` (66/66 ✅) — **all sme_\* tables verified
  cell-identical SQLite↔PG** — and `create_ai_readonly_role.sql` re-applied
  (dual_ci table recreation re-grants SELECT on `users` via default
  privileges; the REVOKE must be re-run after every reload — now re-tested).
- **Bug 3 (report-download parity):** the rebuild was missing most legacy
  download affordances. Added, all read-only (SME Canon intact — zero write
  endpoints):
  - `reports.py`: `to_xlsx_sheets` + `to_pdf_sheets` (multi-sheet/sectioned
    renderers matching the legacy workbook layouts).
  - `GET /sme/export/{key}` now takes `tag` / `location` / `code` scopes and
    emits **legacy filename stems** (`equipment_{tag}_{date}`,
    `equipment_report_{loc}_{date}`, `equipment_report_all_{date}` multi-sheet
    incl. "All System Codes", `system_code_{code}_{date}`,
    `system_code_report_{date}` multi-sheet per code, `progress_list_{date}`
    multi-sheet w/ per-scope production-detail sheets,
    `consumption_comparison_{date}`, `consumption_log_full_{date}`).
  - `POST /sme/plan/export` key `execution-plan` (+`equipment_tag`) — the
    legacy per-tag Execution Order List, oracle-rendered.
  - `POST /sme/export/rows` — renders the CLIENT-filtered frame (legacy
    exported the displayed dataframe verbatim); capped, no DB access.
  - Frontend buttons wired for every legacy download site: Equipment Report
    (all/per-location/per-equipment, xlsx+pdf) · System Code Report
    (all multi-sheet + per-code, xlsx like legacy) · Execution Plan (order
    list per tag, progress list, consumption comparison) · Dashboard
    (material balance xlsx/pdf + procurement grand total + net order list) ·
    Total Overview (full consumption log). NOT ported: the legacy
    `.protected.zip` AES wrapper — new stack serves authenticated downloads;
    flag if password-wrapping is still wanted.
- **Test results:** `service_tests` **360/360** (352 + 8 new export checks) ·
  `parity_check` **5/5** · `parity:sme` **509 ✅** · `bug_check` **599/0** ·
  frontend build ✅. Login/refresh re-verified healthy (200s in
  `logs/uvicorn_dev.log`; bad-credential probe → clean 401).
- **Guardrails:** `sme_*` writes only via the sanctioned legacy-side
  bootstrap (ignore-mode); engines untouched (no golden regen needed);
  `gi_database.db` not staged.
- **Next:** back to 🧊 freeze — Phase 7 (Meta token) or cutover.

### 2026-07-07 · actor=interactive · branch=`main` · 🚑 FREEZE-LIFT HOTFIX: login 500 (PG cluster loss) + SME Progress List legacy parity (26→29 equipments, SQM totals)
- **Bug 1 — login 500 "worked last night, broke this morning": NOT a code bug.**
  The local PG mirror lived in a throwaway cluster under a Claude session
  scratchpad (`/private/tmp/...`); macOS temp cleanup wiped it overnight →
  asyncpg `ConnectionRefusedError` on every request (traceback in
  `logs/uvicorn_dev.log`). **Recovery (data-loss-free — SQLite is truth):**
  Homebrew `postgresql@16` now hosts the mirror durably — `port = 5433` set
  in `/opt/homebrew/var/postgresql@16/postgresql.conf`, started via
  `brew services` (survives reboots), `postgres` superuser role created,
  `gihub` reloaded by `dual_ci` (64/64 + aggregates ✅), `gi_ai_ro` role
  re-applied from `backend/scripts/create_ai_readonly_role.sql`.
  Login + cookie refresh verified 200/200.
- **Bug 2 — SME shows 26 equipments (user knows 29) + SQM totals differ from
  legacy: a read-pipeline gap, zero data loss.** Legacy Progress List reads
  `FROM sqm_progress LEFT JOIN equipment` (progress-driven), so 7 scopes that
  exist ONLY in the SQM progress table (entered via the legacy SQM editor,
  no equipment-master row) still rendered: the 3 real work areas
  **Existing MGA Pump Area · Fan Duct Support J · Fan Duct Support k**
  (26+3 = the user's 29) plus 4 suspected typo tags (`0050`, `0091`, `7112`,
  `7113`). The S5 Progress List iterated engine units (equipment-driven) →
  dropped all 7 → Total SQM 29,280.29 vs legacy 41,642.64 (Δ12,362.35 =
  exactly the orphan rows). **Fix (Canon-safe, read-only):**
  `_progress_list_rows` (backend/api/sme.py) + `ProgressList`
  (frontend/src/sme/ExecutionPlan.tsx) are now PROGRESS-driven with
  equipment meta LEFT-joined and legacy ordering (location · tag · numeric
  code). The parity-locked engine (`build_model`/cascade) is deliberately
  UNTOUCHED — legacy `load_all()` also excludes orphan scopes from the
  cascade, so allocation numbers stay identical. Live-verified: export =
  95 rows · 33 tags · 41,642.64 total — byte-matches the legacy query.
- **Data-quality flag for the user (no action taken):** `0050`/`0091`/`7112`/
  `7113` look like manual-entry typos in `sqm_progress` (e.g. vs `J050`/
  `J091`, values differ) — they render in BOTH stacks; cleaning them is a
  master-data decision (S6/cutover), not a hotfix.
- Gates: service_tests **352/352** · parity_check **5/5** · parity:sme
  **509** ✅ · bug_check **599/0** · frontend build ✅. Freeze re-armed.

### 2026-07-06 · actor=interactive · branch=`main` · 🏗 Phase S5 — Execution Plan (3 sub-views) + Total Overview master grid — LEGACY TAB PARITY COMPLETE (8/8 read tabs)
- **⚙️ Execution Plan** (NEW ExecutionPlan.tsx), 3 sub-views as legacy Tab 4:
  · main plan — session-scoped critical-code analysis (equipment + code
    selectors; the critical code DEFAULTS to the equipment's worst-fulfillment
    code — a smart-default upgrade over legacy), RED 1️⃣ critical shortage
    section then AMBER 2️⃣–N️⃣ per-code sections with coverage pills, and a
    numeric procurement narrative (live-verified on the shared J022/J021
    session: "order the 4 critical-code materials (Code 7) first — 6,388.2
    units — then 6 shortage lines across 3 other codes");
  · 📋 Progress List — plan-vs-done per (tag, code) from the snapshot
    (65 scopes, status ✅/🔄/⏳, completion coloring, Location + Status
    filters) + date-wise production detail blocks from the new log endpoint
    (first-class empty state — the mirror has zero committed entries);
  · 📊 Consumption Comparison — client aggregation with the legacy SQM
    dedup per (date, tag, code), expected-vs-actual variance, cascading
    Location→Equipment→Code filters, 4 KPI drill-downs, and the legacy ±1%
    tinting (over amber · under blue · on-target green).
- **📈 Total Overview** (NEW TotalOverview.tsx): the master (Equipment ×
  System Code) grid — 65 pairs cascaded in default order — with cascading
  Location/Type/Code filters + readiness-status filter (All / Fully Ready /
  Partial 50–99 / Blocked <50; live-verified 65→41 under Blocked), 6 KPI
  drill-downs (incl. fulfillment-weighted Shortfall SQM 22,263.8), **antd
  `virtual` scrolling** (15 windowed row elements over the full set), 4-tier
  row tinting, per-code material expanders (cascade-based coverage +
  availability), and oracle-rendered Excel/PDF.
- **Backend (authorized read-only additions, Canon-safe)**:
  GET /sme/production-log (committed sme_consumption_log rows, site-scoped,
  optional tag/code filters — matches the legacy committed-only view);
  export keys progress-list + production-log on GET /sme/export; plan-export
  key `overview` (per-(tag, code) rollup of oracle cascade lines — a
  presentation aggregation in sme.py, deliberately OUTSIDE the parity-locked
  engine).
- Gates: service_tests **352/352** (+4: production-log shape + worker 403,
  progress-list columns, overview rows ≥ cascade pairs) · frontend build ✅ ·
  parity:sme 509 ✅ · parity_check 5/5 ✅ · bug_check 599/0 ✅ · alembic
  clean (zero schema changes) · clean console.
- **Milestone: all 8 read-facing legacy SME tabs are now rebuilt and exceeded
  in React.** Remaining: S6 = Master Data CRUD (deferred to cutover by
  ruling) + polish.

### 2026-07-06 · actor=interactive · branch=`main` · 🗺 Phase S4 — Location & matrix reports + Dashboard procurement sub-view
- **Dashboard completed** (SmeDashboard + NEW ProcurementView.tsx): Segmented
  toggle restores the legacy dash_view radio; 🛒 Material Requirement &
  Procurement = 4-KPI strip + per-location sections (dot · location-colored
  badge · equipment count · SQM · coverage pill) → per-system-code expanders
  (5 metric chips + the (location, code)-scoped material balance with 4-tier
  tinting) → grand-total strip incl. On-Order-aware net shortfall. Same
  no-cascade dashboard semantics (insights.materialBalance on unit subsets).
- **📍 Location Report** (NEW LocationReport.tsx), dual mode as legacy Tab 3:
  · Location Based — INDEPENDENT drag order per location (localStorage
    gi.sme.locorder.v1 per site key) with the legacy stale-tag reconciliation
    (drop gone, append new, preserve user order); per-location color badges,
    Excel/PDF exports, per-location suggestion panels, per-equipment
    expanders with Add-to-Session;
  · All Equipment — one global order (gi.sme.alleqorder.v1) with a 5-KPI
    strip and the same cascade detail. NOTE: this mode is cascade-based
    (shared pool ⇒ Available SQM 7,020.6) vs the Dashboard's no-cascade
    per-scope coverage (13,046) — both faithful to their legacy tabs.
  Live-proven: Brown Field reorder persisted across a full server restart
  while TRAIN J and the session scenario stores stayed untouched (three
  isolated stores, no crosstalk).
- **📋 Equipment Report rebuilt + 🔢 System Code Report** (NEW
  MatrixReports.tsx): KPI strip + Location→Equipment→code expander hierarchy
  (original SQM); inverse view = summary grid + per-code equipment tables —
  client matrix verified ≡ the server export row-for-row (Code 2/CBL63/12/
  747.3 …). The old flat Equipment Report tab is superseded.
- **Oracle export authority extended**: PlanExportBody gained an optional
  `title` (per-scope document titles, e.g. "SME Location Report — TRAIN J");
  GET /sme/export gained `system-code-report`. All new exports render
  server-side; legacy multi-sheet workbooks are intentionally flattened to
  single-sheet documents (scope in the title / a Location column) — the
  renderer stack is single-sheet by design.
- Gates: service_tests **348/348** (+2: title override, system-code-report
  columns) · frontend build ✅ · parity:sme 509 ✅ · parity_check 5/5 ✅ ·
  bug_check 599/0 ✅ · clean console on a fresh preview server (mid-edit HMR
  windows produced transient ReferenceErrors during dev — gone on restart).

### 2026-07-06 · actor=interactive · branch=`main` · 🧲 Phase S3 — Session Builder & Suggestion Engine (dnd-kit drag priority · live client cascade · scenario sharing · oracle exports)
- **Two new SME tabs** (frontend/src/sme/): 🔍 Session Builder
  (SessionBuilder.tsx — cascading find-equipment filters, add-to-session
  picker, and the **dnd-kit sortable priority list**: every drag/arrow-move
  re-runs the parity-locked TS cascade in the browser; right panel shows the
  selected equipment's live per-code detail, with an *added-last what-if
  preview* for tags not yet in the session) and 📦 Session Report
  (SessionReport.tsx — 4 KPI drill-downs, the same shared priority list,
  per-equipment expanders, shortage-only Recharts stacked bar, **SQM-weighted
  combined procurement** (legacy per-cell fulfillment × SQM port), amber
  grand-total, suggestion panel).
- **Suggestion engine client-side** (SuggestionPanel.tsx): the pause-one
  simulation loop (engine.ts runSuggestionEngine) runs in the browser on
  every order change; recommended scenario narrated + all-candidates table +
  reversible one-click "Apply" (removes the tag). Live-verified numerically:
  panel said pausing J022 gains +5.3% — the oracle's completion delta is
  exactly 72.77 − 67.46 = +5.31.
- **Scenario persistence & sharing** (ScenarioContext): priority order in
  localStorage per site key AND mirrored to `?scenario=` (URL wins on first
  load → share-links open the sender's exact scenario); Share button copies
  the link. Proven: scenario survived a full preview-server restart.
- **Parity exports**: `POST /sme/plan/export` (the ONLY backend change —
  read-only compute; keys session-full/order-list/feasibility × xlsx/csv/pdf
  via the reports renderers) — official documents are rendered by the PYTHON
  oracle from the client's posted priority order, never by the browser.
  4 export buttons wired in the report tab (postDownloadDocument helper).
- **Live proof of instant re-cascade ≡ oracle**: staged the real contention
  pair J021/J022 (COROFLAKE EP PRIMER + CARBON FILLER); browser pills
  [72.8/70.9] → flip → [70.9/67.5], matching POST /sme/plan/cascade to the
  displayed digit ([72.77/70.87] → [70.93/67.46]).
- Supporting: session.ts (tag/code stats — tag_fulfillment/syscode_
  fulfillment/sqm_can_do ports), PriorityList.tsx (dots/pills/code badges/SQM
  ratios + keyboard-accessible arrow moves), TagDetail.tsx (shared per-code
  breakdown), @dnd-kit/utilities dep.
- Gates: service_tests **346/346** (5 new export checks: xlsx magic, csv
  carries oracle shortages, worker 403, bad key 404, bad format 400) ·
  frontend build ✅ · parity:sme 509 ✅ · parity_check 5/5 ✅ · bug_check
  599/0 ✅ · clean console on a fresh preview server.

### 2026-07-06 · actor=interactive · branch=`main` · 📊 Phase S2 — SME Dashboard rebuild (client-side cross-filters · KPI drill-downs · SVG gauge/hbars · Recharts)
- User-authorized cleanups executed first: `DELETE FROM consumption WHERE id=2`
  on the PG mirror (the gibberish preview-form test row) → **parity_check back
  to 5/5**; stray empty backend/gi_database.db removed.
- **frontend/src/sme/insights.ts** (NEW): faithful port of the legacy Tab 0
  "Project Overview" math (portal lines ~3633–4046) — demand = For_1_SQM ×
  remaining SQM per filtered (tag, code); scope coverage = Σ min(demand_m,
  avail_m) / Σ demand_m (per-material cap, NO cascade — dashboard semantics);
  coverable SQM = scope SQM × min(1, cov); material balance with On-Order-aware
  Net_Shortfall; stock-only = recipe-member ∧ no current demand (R20.1 rule).
  Deliberately OUTSIDE the parity-locked engine (engine.ts/sme_engine.py
  untouched numerically; engine only gained an exported `unitKey` alias).
- **frontend/src/sme/SmeDashboard.tsx** (NEW, replaces the basic Dashboard tab):
  4-way cascading cross-filters (Location→Type→System Code→Substrate; each
  option list scoped by the other three, empty = all) · 7-KPI strip
  (Equipment, Total SQM, Available Coverage SQM, SQM Deficit, Overall
  Coverage + delta, Shortfall SQM, Critical <50%) where **every KPI is a
  single click → real AntD drill-down modal** (legacy: double-click hack) ·
  Recharts stacked bars (demand-vs-available mini; per-location Can-Do in
  legacy location colors + red Deficit, with location stat cards
  dot/%/SQM-ratio/equipment) · Full Material Balance grid with the legacy
  4-tier row tinting (≥100 green · ≥90 orange · ≥80 yellow · <80 red) +
  client-side CSV export · stock-only collapse.
- **frontend/src/sme/CoverageGauge.tsx + CoverageHBar.tsx** (NEW): the legacy
  custom SVGs (render_design_gauge:3169 / render_design_hbar:3215) ported 1:1
  as native React components — same geometry, tier band arcs, JetBrains Mono
  readouts; zero chart-library dependency.
- Everything computes CLIENT-SIDE from one GET /sme/model-snapshot:
  live-verified that the whole filter + drill session issued **zero further
  /sme requests** (network log), with instant recompute — Brown Field filter:
  Equipment 26→10, coverage 44.6%→78.9%, code options narrowed to Codes 2/7.
  Balance math spot-verified in-browser (BC 3004: avail 0, on-order 7,281,
  demand 9,324 → shortfall 9,324, net 2,043, 0.0% red ✓).
- Zero backend changes. Gates: service_tests 341/341 · frontend build ✅ ·
  parity:sme 509 ✅ · parity_check 5/5 ✅ · bug_check 599/0 ✅. Clean console
  after a preview-server restart (one transient stale-HMR signature error
  during dev, gone on restart — known preview behavior).

### 2026-07-06 · actor=interactive · branch=`main` · 🧮 Phase S1 — SME rebuild foundation (model snapshot + client TS engine + parity oracle)
- Kickoff of the SME React rebuild program (S1…S6, approved plan): lift the
  read-only-basic SME UI to full legacy-portal richness. **Backend stays
  READ-ONLY per the SME Canon** — S6 (Master Data CRUD) deferred to cutover day
  by explicit ruling (dual-write drift).
- **backend/api/sme_engine.py** (NEW, pure — no DB/framework imports): faithful
  port of the legacy portal's `cascade_allocate` (global per-material pool,
  priority order, codes numeric-first, 4dp/2dp rounding, remaining SQM =
  original − done − staged clipped at 0) + `compute_feasibility` (exact legacy
  ✅/🟡/🔴 labels, bottleneck material), `run_suggestion_engine` (pause-one
  simulation, stable tie ordering), `build_procurement_list`, `build_totals`.
  Shared half-up `round_n` (floor(x·10ⁿ+0.5)) used VERBATIM in both languages
  so rounding ties can never diverge between runtimes.
- **Two new read-only endpoints** (backend/api/sme.py, level ≥ 2 as before):
  `GET /sme/model-snapshot` (equipment + recipes + derived materials + progress
  + default_order in one payload, site-scoped, staged-progress column guarded)
  and `POST /sme/plan/cascade` (server-side plan for a given priority order +
  optional suggestions — the parity oracle and the future export backend).
  POST = compute, not mutation; nothing writes.
- **frontend/src/sme/engine.ts** (NEW): line-for-line TS mirror of the Python
  engine (typed snapshot/result interfaces; dependency-free by design).
  **frontend/src/sme/ScenarioContext.tsx** (NEW): persistent planning-scenario
  store (priority order per site key, localStorage `gi.sme.scenario.v1`),
  mounted around SmePage. Deps added: @dnd-kit/core, @dnd-kit/sortable,
  recharts (used from S2/S3 on).
- **Golden-fixture parity proof**: backend/api/sme_parity_fixture.json (edge
  cases: dup rows summed, staged fold, remaining clip, zero-demand tag,
  missing/negative pools, non-numeric code, priority inversion, suggestion
  ties) + sme_parity_golden.json generated by the Python engine and verified
  BY BOTH SIDES — service_tests suite G re-runs Python vs golden;
  `npm run parity:sme` (frontend/scripts/sme_parity.mjs, Node type-stripping,
  no bundler) asserts TS vs golden: **✅ 509 comparisons**. Golden equality on
  both sides ⇒ TS engine ≡ Python oracle.
- Suite G also proves endpoint behavior live: worker 403 on both endpoints,
  hod foreign-site 403 + own-site pinning, cascade endpoint ≡ pure engine on
  the live snapshot, reversed-priority demand invariance.
- Gates: service_tests **341/341** · frontend build ✅ · parity:sme ✅ 509 ·
  bug_check 599/0 · alembic check clean (zero schema changes). Live verify:
  real-data cascade (26 tags → 247 lines, bottleneck GI-6000013, suggestion
  ranking), /sme page clean console.
- ⚠️ Pre-existing gate drift (NOT Phase S1): parity_check live/by-site now
  fail on ONE mirror-only junk consumption row in PG (id=2, SAP 1001, qty 1,
  gibberish fields, dated 2026-07-06 — manual preview-form test data; absent
  from SQLite truth). Phase S1 adds zero write paths; cleanup needs an
  explicit user-authorized `DELETE FROM consumption WHERE id=2 …` or a dual_ci
  mirror reload. sme_materials parity itself passes 22=22.

### 2026-07-06 · actor=interactive · branch=`main` · 📈 Phase AI-5 — analytics AI (NL→SQL · insights · EOD summary) — INTELLIGENCE LAYER COMPLETE
- **NL→SQL** (backend/api/ai/analytics.py + POST /ai/nl-search): plain English →
  qwen2.5-coder → PG-spelling schema hint (quoted identifiers, ISO-text date
  patterns, live-stock worked example; users/auth tables absent) → the
  PG-hardened safety gate → execution on **`gi_ai_ro`, a TRUE read-only PG
  login** (backend/scripts/create_ai_readonly_role.sql — idempotent:
  default_transaction_read_only, ROLE-level statement_timeout=5s, REVOKEd
  SELECT on users/pending_users/auth_sessions/ai_jobs). Two independent walls,
  both test-proven: the gate rejects model-emitted UPDATE/users-reads, AND the
  role physically blocks INSERT + users even when the gate is bypassed
  on purpose. **Gated to UNSCOPED roles (level ≥ 3) for V1** — generated SQL
  can't be site-pinned, so scoped roles are excluded by design. Flag
  ai_nl_search_enabled. UI: "Ask in plain English" card on the Dashboard
  (level ≥ 3 only) with a result grid + Show-SQL transparency collapse.
- **AI Insights** (POST /ai/insights, SSE): the 5 legacy probes ported to PG
  (SQLite date fns → CURRENT_DATE − INTERVAL casts over ISO-text dates;
  HAVING-on-alias rewritten) — consumption spike, projected stockouts,
  expired lots, supplier consolidation, health score. **Probe events stream
  FIRST (deterministic numbers, instant), commentary events follow** as
  llama3.1 narrates each (strict-JSON title/body/3-recs, deterministic
  fallback when Ollama is down — the stream never dies). hod+, site-scoped
  via resolve_site_param. Flag ai_insights_enabled.
- **EOD summary** (POST /ai/eod-summary, SSE): legacy context builder ported
  (day totals + per-site consumption + top-10 low stock, ≤1.5 KB) with a
  site-filter addition for scoped hods; streams llama3.1 prose. Foreign-site
  request by a hod → 403 (scoping held on the AI surface too).
- **FE:** shared src/api/sse.ts (the HubAssistant fetch+ReadableStream pattern
  extracted) · Reports "🤖 AI" tab (EOD card w/ date picker + streaming
  paragraph; Insights card w/ progressive severity-tagged cards that upgrade
  in place when commentary lands) · Dashboard NL-search card.
- **Verified:** service_tests **305 → 324/324** (role gates incl. hod-403-by-
  design, fenced-SQL extraction + LIMIT injection, gate rejections, BOTH RO-
  role walls, probe-before-commentary ordering, fallback commentary, EOD
  context assertion + tokens + foreign-site 403, flag 503). Build green.
  **Live with REAL models** (all three installed locally): qwen2.5-coder
  answered "top 5 suppliers" in ~2 s → real rows + SQL; llama3.1 streamed a
  genuine EOD summary naming actual 0-stock items; both insight cards
  rendered real narration (health 96/100, 13 low-stock). Clean console.
- **Deploy note:** run create_ai_readonly_role.sql once per DB; set a password
  + GI_AI_RO_URL in production.

### 2026-07-06 · actor=interactive · branch=`main` · 📸 Phase AI-4 — Smart Scan (client-side QR + tool vision)
The warehouse-floor CV port, per the locked rulings (LocateAnything retired;
qwen2.5vl covers identification; legacy YOLO tier optional-later).
- **Tier 1 — badge QR, decoded ENTIRELY client-side** (components/QrScanner.tsx):
  getUserMedia → native BarcodeDetector when available, jsQR fallback (≤480px
  canvas frames) — the video stream NEVER leaves the browser; only the decoded
  ID string hits **GET /ai/badge/{id}** (exact {store_keeper,admin} lock), which
  verifies the ACTIVE employee and returns name/phone/department (legacy Tier-1
  semantics). Camera-denied/absent → graceful manual-ID fallback in the same
  modal (also covers damaged badges + desktops). `jsqr` added to frontend deps.
- **Tier 2 — tool identification**: new `tool_identify` job kind on the AI-3
  queue (photo → prepped → qwen2.5vl). **Catalogue-optional**: when
  tool_catalogue has rows the prompt constrains to those classes and replies
  map class→display name; when empty (the current PG state — legacy never
  promoted a model here) the model names the tool freeform. Alternatives
  surface as a picker.
- **Integration — Phase-4 Returnables**: the "Loan a tool" modal gains Scan
  badge (→ borrower + phone prefilled + verified Tag, inactive employees
  warned) and Identify tool (→ material_name prefilled, alternatives Select).
  Staging still flows through the existing exact-locked /entry/returnables.
- **Verified:** service_tests **297 → 305/305** (badge found/unknown/role-gate,
  tool job with seeded catalogue: classes in the PROMPT, class→display mapping,
  mixed catalogue/freeform alternatives, empty-catalogue freeform path; seeds
  cleaned). Build green. Live: scan modal → camera-denied fallback → typed
  badge 30816 → borrower "Johnson Andrew" + phone prefilled + green verified
  tag; clean console.

### 2026-07-06 · actor=interactive · branch=`main` · 📷 Phase AI-3 — handwriting OCR + async job queue
The heaviest AI port: photographed paper logs → reviewed rows → the normal
staging chain. **One user-authorized schema addition:** `ai_jobs` (alembic
`b3e91d40aa17`, models.AiJob) — NEW-STACK-ONLY like auth_sessions (dual_ci
leaves it empty); `alembic check` clean.
- **Job pattern** (vision OCR runs 5–120 s — past proxy timeouts and mobile
  patience): POST /ai/jobs (multipart, prep-validated at upload → corrupt
  images 422 immediately, never a dead job) inserts a row + spawns an
  in-process asyncio task; React polls GET /ai/jobs/{id} every 2 s. The
  queued→running transition is an **atomic claim UPDATE** (report-scheduler
  discipline); a **startup orphan sweep** fails stranded jobs with "resubmit
  the photo". Owner-only polling (admin may inspect any). Exact-locked
  {store_keeper, admin} — the legacy Daily Issue Log gate.
- **Pipeline** (backend/api/ai/ocr.py + jobs.py): image prep port (HEIC via
  pillow-heif → EXIF auto-orient → RGB → 1600px cap → JPEG q85), byte-identical
  qwen2.5vl JSON-schema prompts (consumption + delivery-note), fence-tolerant
  JSON parse, row cleaning, **fuzzy resolve** to auto/pick/unknown with SAP
  candidates — all under the generation semaphore. Ollama-offline and
  unparseable-reply paths land as clean job errors naming the Paste fallback.
- **Paste lane** POST /ai/paste/{kind}: pure-Python twin (delimiter-sniffing,
  DN header synonyms) + the same fuzzy resolution — works with Ollama down.
- **FE:** OcrImportPage (/entry/ocr, Data Entry nav): kind toggle, photo
  dragger (HEIC hint, warm-up notice, offline alert) + offline paste card, DN
  header Descriptions, review grid (match Tag, candidate-first SAP select w/
  ★-scores, editable qty/issued-to, per-row delete) → **stages through the
  EXISTING exact-locked services** POST /entry/consumption / /entry/receipts
  (drafts → HOD approval; DN header feeds Supplier + Remarks).
- **Verified:** service_tests **277 → 297/297, run twice** (full mocked-vision
  lifecycle incl. atomic claim, model/image/prompt assertions, auto+unknown
  resolution, DN header round-trip, garbage-reply + offline error paths, paste
  lanes, orphan sweep, exact-lock 403s, flag 503s; ai_jobs cleaned). Fixed en
  route: the AI-2 confirm test leaked CREATE_PR audit rows that collided with
  per-day PR-number reuse — both audit assertions are now **delta-counted**
  (suite is rerun-stable). pillow-heif added to backend/requirements. Live:
  paste lane round-trips to auto-matched SAP rows in the review grid, clean
  console. NB deploy: `ollama pull qwen2.5vl:7b` (~5 GB) enables the photo lane.

### 2026-07-06 · actor=interactive · branch=`main` · 📄 Phase AI-2 — document intelligence (PR/PO PDF extraction)
`backend/api/ai/pdf_extract.py`: framework-free, byte-compatible ports of BOTH legacy
pdfplumber parsers — the PR word-stream extractor (GI-\d{7} + 6-word qty look-ahead,
dedupe, strict Material_Code matching) and the PO Round-15 scanner (header regexes,
ALL THREE line-item layouts: code-line+7-col-w/-VAT, inline 6-col, split-line pair;
RL/BL family tagging via the ported classifier; SHIPMENT annexure → ISO dates).
- **Endpoints:** POST /ai/extract/pr (≥hod) + /ai/extract/po (≥logistics) — UploadFile →
  **asyncio.to_thread** (pdfplumber is sync/CPU-bound; the event loop stays free), 15 MB
  cap, 422 on unparseable, flag `ai_doc_intel_enabled` (console-editable) → 503 when off.
- **Preview-confirm workflow (fixes the legacy silent-insert flaw):** extraction writes
  NOTHING — proven by a row-count invariant in the tests. Confirm goes through the
  EXISTING audited services: PR → POST /hod/prs (test asserts the CREATE_PR audit row
  that legacy never wrote); PO → POST /logistics/pos (create_po_from_pr — PO lines
  derive from the submitted PR per the locked simplified-chain ruling; extracted items
  render for reconciliation only).
- **FE:** HodPrsPage "📄 Import from PDF" tab (dragger → matched table w/ editable qtys +
  unmatched-codes alert w/ legacy context windows → site picker → Create PR) and
  LogisticsPage "📄 Import PO PDF" tab (header Descriptions + items + delivery schedule +
  prefilled Create-PO modal). `pdfplumber` added to backend/requirements.txt.
- **Verified:** service_tests **262 → 277/277** (synthetic fpdf2 PDFs: PR strict-match
  2-matched/1-unmatched, preview-only invariant, audited confirm, all 3 PO layouts w/
  exact prices + VAT-column skip + ISO schedule, role gates 403, junk 422, flag 503 w/
  finally-restore); build green; live UI: real file dispatched through the Upload
  component → preview rendered (PDF PR 3001234567, editable qtys, unmatched alert), PO
  header/items/schedule rendered; clean console (one antd v6 Alert prop fixed en route).

### 2026-07-06 · actor=interactive · branch=`main` · 🧠 Phases AI-0 + AI-1 — Intelligence-layer foundation + Hub Assistant
First slice of the AI program (from the 2026-07-06 legacy AI audit; user rulings: LocateAnything
sidecar RETIRED in favor of qwen2.5vl vision fallback later; Ollama on the SAME BOX, one warm
model, load-on-demand swap). New `backend/api/ai/` package:
- **client.py** — async Ollama client (httpx), env-config (OLLAMA_HOST, GI_AI_*_MODEL,
  GI_AI_CONCURRENCY), health/list/generate/stream, keep_alive=30m, and a **generation
  semaphore** (default 2) so concurrent users queue instead of thrashing the model host.
  Late-bound module calls = the monkeypatch seam for Ollama-free tests.
- **safety.py** — PG-hardened port of the legacy safe-SQL gate (comment/string-literal
  sanitizing scanner; + COPY/DO/CALL/EXECUTE/… keywords, `auth_sessions` + pg_catalog/
  information_schema blocked). Guards the future AI-5 NL→SQL feature; tests ported.
- **fuzzy.py** — pandas-free port of the hybrid matcher (SequenceMatcher × token-Dice,
  auto ≥0.85 / pick ≥0.45 / unknown) for the AI-3 OCR review grid; tests ported.
- **manual_qa.py** — role-gated section retrieval over USER_MANUAL.md ported intact
  (allowlists, admin-full/800-char-head truncation, greeting fast-path, role-aware refusal);
  allowlists UPDATED for the v3.0 manual's new §18 SME + §19 Man-Hours (hod/admin).
- **router.py** — GET /ai/health (flags + Ollama + model + manual) and **POST /ai/assistant:
  SSE token stream** (data: {token} / {status:queued} / {done}); flags `ai_enabled` +
  `ai_assistant_enabled` in app_settings (added to the console whitelist, default ON).
- **FE** — floating HubAssistant panel (all roles, gold FAB bottom-right): fetch+
  ReadableStream SSE reader (axios buffers; EventSource can't send the bearer header),
  AbortController cancel, health preflight → graceful "Local AI is offline" alert with
  input disabled, queued-state hint. `getAuthToken()` exported from api/client.
- **Deploy kit** — `ollama` service added to docker-compose.prod.yml (internal-only,
  OLLAMA_MAX_LOADED_MODELS=1, model volume, pull instructions, CPX42 RAM notes);
  api gets OLLAMA_HOST=http://ollama:11434.
- **Verified:** service_tests **233 → 262/262** (suite F, Ollama MOCKED: 12 safety checks,
  fuzzy states, role-context isolation — the store keeper's PROMPT physically lacks §7,
  greeting fast-path, SSE order, flag-off → error event w/ restore); build green; live:
  offline alert with Ollama down, then real llama3.1:8b streaming — cold-start Q&A in
  21.9s ("stage a return" → Entry Log steps), warm FEFO answer in-panel; clean console.

### 2026-07-06 · actor=interactive · branch=`main` · 🤖 Phase 11C — planning automation (auto-draft estimates + manpower forecast)
Closes the Man-Hours integration. Zero schema changes; sme_* still read-only (writes land
exclusively in mh_manhour_estimates); exact {hod, admin} lock inherited from the router.
- **GET /mh/estimates/auto-draft** (preview): a draft for every SME scope with remaining
  SQM and NO estimate yet — remaining × MH/SQM norm, preferring the scope's own learned
  norm over the site norm, with an explicit `?norm=` override (and a hint when no
  productivity history exists yet). Estimated scopes are never overwritten silently.
- **POST /mh/estimates/auto-draft** (save): bulk upsert of the REVIEWED rows (≤200) —
  mirrors the HOD auto-draft-PR pattern: preview → edit → approve, nothing saves unseen.
- **GET /mh/forecast**: days-to-complete per scope for a crew — estimate-based remaining
  (max(est − actual, 0)) plus norm-based scopes; fully-consumed scopes drop out; site
  rollup (total remaining MH ÷ crew × hours/day). crew_size 1–1000, hours/day 1–24.
- **FE**: 🤖 Auto-draft card on the Estimator tab (norm override, editable Draft-MH
  column, row selection, "Save N estimates") + 📅 Manpower-forecast card on the Scorecard
  tab (crew/hours inputs, per-scope days, rollup tag).
- **Verified:** service_tests **221 → 233/233** (draft math on every row, norm override,
  save→pool-shrink round-trip, estimate-based 2-MH remaining, fully-consumed drop-out,
  norm-based rows + rollup, 422 guards, worker 403; zero residue); build green; live: 65
  real scopes draft at a 0.5 override ("Save 65 estimate(s)"), forecast shows the real
  0050/1 estimate (15 MH ≈ 0.2 crew-days), clean console.

### 2026-07-06 · actor=interactive · branch=`main` · 🔗 Phase 11B — SME link layer + Equipment Scorecard
The SME↔MH interconnect (user-approved Architecture A: **read-only join layer**, no link
tables, sme_* NEVER written — verified by a code audit for insert/update/delete on sme_*).
Both domains join on the natural key (Site_ID, Equipment_Tag≡Equipment_Tag_No,
System_Code≡Lining_System_Code); merges happen in Python over grouped SELECTs (sme.py style).
- **GET /mh/productivity**: per-scope labor norms (MH/SQM · SQM/MH from mh_timesheets ÷
  mh_production) + the estimate norm (Estimated_MH/Estimated_SQM) + the **site norm**
  (aggregated over scopes with both hours and SQM) — the calibration constant for 11C.
- **GET /mh/scorecard**: one row per Tank/System — union of SME scopes and MH-only scopes
  (flagged `In_SME:false`): Planned SQM (sme_sqm_progress.Original, fallback summed
  Surface_Area_SQM — area rows repeat per scope, so SUM + first location), Done (SME) vs
  Done (Labor), % complete, Estimated vs Actual MH + labor variance %, MH/SQM, **material
  expected/actual/variance % from sme_consumption_log** (rejected excluded), and a
  **Reconciliation flag**: the two independent "SQM done" sources (labor-reported vs
  SME-reported) → 'drift' when they disagree by > max(1 SQM, 5%).
- **Exports**: scorecard + productivity join the /mh/export/{key} family (xlsx/csv/pdf via
  the shared renderers).
- **FE**: new 🔗 Scorecard tab (6th) — KPI cards (scopes / with-labor / site norm / drift),
  variance Tags, drift rows tinted red, "only scopes with labor" filter, XLSX+PDF buttons.
- **Verified:** service_tests **211 → 221/221** (union incl. MH-only, −20% labor variance on
  a seeded real-SME scope, MH/SQM 0.2 + SQM/MH 5.0 + est-norm, drift flag 40-vs-0, KPI
  counts, site norm, worker 403, pdf+xlsx exports; cleanup extended — zero residue); build
  green; live: 66 scopes render (65 SME + 1 MH-only), Brown Field/TRAIN J planned SQM
  visible, clean console. SME Canon intact.

### 2026-07-05 · actor=interactive · branch=`main` · 🔗 Phase 11A — attendance-import fit + bulk-assign workflow
Drove by the user's REAL `to john_Attendance.xlsx` (22 employees · 209 rows · 27 dates —
the Phase-10 parser handles it verbatim, verified). What the real file exposed:
- **'nan' hygiene**: the legacy pandas bootstrap wrote literal `'nan'` strings into
  mh_timesheets.Equipment_Tag/Location (209 rows in BOTH DBs). One-time normalization run
  against PG **and** SQLite (both → NULL; SQLite fixed too so dual_ci reloads stay clean;
  bug_check 599/0 after). Permanent guard: `_clean()` treats ''/nan/none/null as NULL on
  every mh write path, and the unassigned-filter matches them defensively (the FROZEN
  legacy uploader in database.py can still produce 'nan' — code untouched per golden rule).
- **Legend defaults** (ADD EMPLOYEE sheet): OWN→GI, Supply→DMC applied when Company is
  blank, on import and on POST /mh/employees.
- **In-file dedupe** on (code, date, tag) — last occurrence wins — protecting NULL-tag rows
  the unique key can't (PG treats NULLs as distinct).
- **Append-overlap warning**: /mh/import (incl. dry_run) returns `overlap_dates` = file
  dates that already hold rows; the SPA dry-runs first and Modal-confirms before an append
  that would duplicate ("Append anyway" / switch to Replace).
- **Bulk-assign workflow** (the critical gap — the file ships Equipment Tag # 100% blank):
  `PATCH /mh/timesheets/assign` (ids ≤ 500 + target Tag/System; Location auto-fills from
  sme_equipment READ-ONLY; unique-key twins are skipped + reported, never merged silently)
  + `?unassigned=` filter with a total_hours rollup + the "🔗 Assign hours to a scope" card
  on the Daily Timesheet tab (range filter, row-select, live unassigned badge).
- **Verified:** service_tests **198 → 211/211** (legend defaults, 'nan' guard via a junk
  workbook, dedupe, overlap dry-run/append, assign + location autofill + conflict skip +
  422s); bug_check 599/0; build green; live: the real 209 rows / 1,672 h surface in the
  assign card as unassigned, clean console. SME Canon intact (reads only).

### 2026-07-05 · actor=interactive · branch=`main` · 🕒 Parity build Phase 10 — Man-Hours & Labor Tracking portal
New `backend/api/manhours.py` (/mh, ZERO new tables — the mh_* tables already exist in both
stacks; alembic baseline carries them). **Exact-locked {hod, admin}** via `require_roles("hod")`,
mirroring the legacy page + SME estimator lock (a level check would wrongly admit logistics).
HOD pinned to own Site_ID; admin passes ?site_id= (required on writes → 422 without).
- **Employees**: roster CRUD over `mh_employees` (upsert on Site+Code, OWN/Supply,
  active/inactive flips) — logically separate from the system `users` table.
- **Daily timesheets**: per-day batch grid + **attendance-xlsx import** (openpyxl port of
  `parse_attendance_workbook` — ADD EMPLOYEE + SAR sheets, replace-by-date or append, dry-run
  preview; SAR workers auto-merge into the roster). Hour math ported verbatim:
  Total=(Out−In)−break w/ overnight +24h wrap, Normal=min(8), OT=rest — the file's own hour
  columns are ignored. NB: FastAPI UploadFile ⇒ `python-multipart` added to backend/requirements.
- **Team SQM production**: upsert + auto-distribute into Allocated_SQM (even | by_hours pro-rata).
- **Estimator**: required MH per Tag/System (+optional SQM → MH/SQM norm) over
  `mh_manhour_estimates`.
- **Estimate-vs-Actual**: the legacy `v_mh_estimate_vs_actual` view inlined as plain PG SQL
  (no view/migration needed) + KPIs + over-consumption reason capture (`mh_variance_notes`).
- **Employee-wise report**: roster-joined timeline w/ date window + total hours.
- **Exports** reuse the shared /reports renderers (employees | timesheets | variance |
  employee-timeline · xlsx/csv/pdf).
- **FE:** ManHoursPage (5 tabs: Employees · Daily Timesheet · Estimator · Estimate vs Actual ·
  Employee-wise) at /manhours; nav group gated to exact `['hod','admin']`; admin site picker.
- **Verified:** service_tests **166 → 198/198** (new suite E: exact lock, hour math incl.
  overnight, upsert-in-place, even+by-hours distribution, variance 20 est vs 25.5 act → +27.5%,
  reason capture, timeline join, xlsx import dry-run/replace/idempotent re-import/bad-file-422,
  exports; full SVC- cleanup in a finally); parity 5/5; build green; live: 22 CNCEC workers in
  the roster grid, variance dashboard renders the real estimate row, clean console.

### 2026-07-05 · actor=interactive · branch=`main` · 🛡️ Parity build Phase 9 — admin console completion
New `backend/api/console.py` (NO new tables — sites live in `system_settings` category='Site';
requests/bug_reports/app_settings are legacy tables):
- **Global sites CRUD** (admin): add/list/delete; delete blocks when users are bound (409).
- **Settings + MAINTENANCE MODE**: whitelisted app_settings keys; when `maintenance_mode='1'`,
  non-admin **login/2fa/refresh → 503** (enforced in auth.py; running access tokens die within
  their 15-min lifetime — documented tradeoff); flag rides `/health` → gold banner in the SPA.
- **Manual backup**: `POST /admin/backup` shells pg_dump -Fc into GI_BACKUPS_DIR (GI_PG_DUMP /
  PATH / Homebrew fallback; 501 with runbook pointer where absent — the slim API container).
- **Access control**: `/admin/sessions` viewer (never exposes refresh_hash) + per-session and
  per-user revoke → a revoked user's refresh 401s (proven with a live victim session).
- **Logistics oversight** (≥logistics): 7 KPI blocks (PRs by state, POs by status, top vendors,
  DNs, warehouse load, force-closures, vendor returns).
- **Cross-site requests** (`/xsite` — /requests was taken by SMR): HOD raises (availability
  snapshot at target captured), admin decides w/ suggested qty, creator cancels while pending;
  notifications both ways. = legacy HOD "My Requests" + admin "Pending Requests" (one page).
- **Feedback**: `POST /feedback` (any authed) + `/feedback/mine`; admin list/respond/delete over
  legacy `bug_reports`; submitter notified on status change.
- **FE:** AdminConsolePage (Sites·Settings·Sessions·Oversight·Feedback tabs), CrossSitePage
  (/hod/requests), FeedbackPage (/feedback, Account group), maintenance banner in AppLayout.
- **Verified:** service_tests **137 → 166/166** (sites lifecycle, maintenance ON→worker-503→OFF
  in a finally, real pg_dump written+cleaned, victim-session revocation, xsite+feedback
  lifecycles w/ cleanup); build green; live: 3 legacy sites, 77 active sessions listed,
  7 KPI blocks, maintenance banner round-trip.

### 2026-07-05 · actor=interactive · branch=`main` · 🧪 Parity build Phase 8 — SME read-parity (Phase 7 SKIPPED — Meta hold)
**SME Canon held: 9 routes on /sme, ALL GET, zero insert/update/delete (audited).** New in
`backend/api/sme.py` (all ≥ hod, site-scoped via resolve_site_param):
- **/sme/equipment-report** — per-tag rollup (systems, planned/done/remaining SQM, % complete);
  Python group-over-SELECT, explicit tag ordering (Canon Rule 1).
- **/sme/consumption-comparison** — expected vs actual per material over `sme_consumption_log`
  (+ committed/pending/rejected row counts; recipe-name join deduped via GROUP BY subselect).
- **/sme/demand-matrix** — read-only port of the legacy allocation engine: demand = remaining
  SQM × For_1_SQM per recipe line, then cascade allocation against the derived available pool
  (SQL_SME_MATERIALS). The legacy drag-priority order is interactive UI state → the port uses a
  FIXED deterministic order (tag asc, system numeric asc), stated in the response.
- **/sme/export/{key}** — xlsx/csv/pdf of equipment-report · consumption-comparison ·
  demand-matrix · demand-totals (≈ legacy Net Order List) · materials, reusing reports renderers.
- FE: SmePage grows Equipment Report / Consumption Comparison / Demand Matrix tabs + XLSX
  export buttons (Dashboard tab already existed).
- **Verified:** service_tests **129 → 137/137** incl. invariants (allocated+shortfall==demand;
  totals reconcile with lines); parity 5/5 (sme_materials 22=22); build green; live: 247 demand
  lines → 19 totals, 26 equipment rollups, comparison 0 rows (no staged SME consumption yet —
  correct empty state). Gotcha: sme.py never imported HTTPException before (no error paths).

### 2026-07-05 · actor=interactive · branch=`main` · 📄 Parity build Phase 6 — documents & PDF/label generators
- **New `backend/api/documents.py`** (DRY — reuses `reports.py` renderers + one FPDF grid backbone):
  - **QR bin labels** `GET /documents/qr-labels` (inventory SAP QR grid, 3×4, site-scoped) and
    **employee badges** `GET /documents/employee-badges` (ID_Number QR + name + dept, active only,
    site-scoped) share `_grid_pdf(cells, draw_cell)` — each supplies only a per-cell callback;
    ports the legacy `generate_qr_labels_pdf` / `generate_employee_qr_badges_pdf` layouts. `qrcode`
    (already a 2FA dep) renders each QR PNG.
  - **SOP / Manual** `GET /documents/reference/{sop|manual}` streams the pre-built root PDFs
    (`GI_Hub_SOP.pdf`, `GI_Hub_User_Manual.pdf`) — reference material, any authenticated user.
  - **Master exports** `GET /documents/master/{vendors|warehouses|employees|inventory}?format=` reuse
    `reports.py` `to_xlsx/to_csv/to_pdf` (blob/sensitive columns dropped); employees/inventory
    site-scoped. Management outputs (labels/badges/exports) gated `require_level(2)`.
- **FE:** new DocumentsPage (`/documents`, nav for all; Reference card for everyone, Label-sheets +
  Master-export cards ≥ hod) + an Export button on MasterDataPage. Generic `downloadDocument()` blob helper.
- **Verified:** service_tests **120 → 129/129**; build green; live: QR PDF 251 KB w/ `%PDF-` magic,
  vendor xlsx w/ `PK` magic, worker sees only the Reference card (UI + backend 403 both hold).

### 2026-07-05 · actor=interactive · branch=`main` · 📊 Parity build Phase 5 — reports parity + archive + scheduler
- **11 new reports** in the `/reports/{key}` framework (6 → **17**): daily-consumption,
  monthly-summary (opening/received/issued/returned/closing per SAP), wbs, low-stock,
  burn-rate, valuation, fefo, audit (**global_only** — hidden from + 403 for scoped users),
  warehouse-throughput, force-closures (over `po_force_closures`), intent-vs-actual
  (SMR items ⋈ consumption via `Source_Ref`). `download_report` refactored into a shared
  `render_report()` (+ `date_from/date_to/month` params). asyncpg gotcha ×2: params cast
  to timestamp/date must be Python datetime/date objects, not ISO strings; and SMR headers
  use `requested_at` (no created_at).
- **Archive** (`backend/api/report_center.py`): disk files under `GI_REPORTS_ARCHIVE_DIR`
  (default `reports_archive/`, shared with legacy, gitignored) indexed in the legacy
  `report_archive` table — generate/list/re-download/delete (admin-or-generator), site-scoped,
  audited. Router registered BEFORE `/reports/{key}` so literal paths win.
- **Scheduler**: dependency-free asyncio daemon in the FastAPI lifespan (`GI_SCHEDULER=0`
  disables; NOT APScheduler — no new dep, and multi-worker duplicate runs are solved by an
  **atomic last_run claim** UPDATE that only one worker wins). Frequencies over the legacy
  `report_schedules` table: `daily HH:MM` · `weekly mon..sun HH:MM` · `monthly DD HH:MM`
  (server time). On run: render → archive → `report_ready` notification to recipients (or
  the creator). Full CRUD + toggle + run-now endpoints; ReportsPage → Generate | Archive |
  Schedules tabs.
- **Verified:** service_tests **108 → 120/120** (all 11 keys render; global-only gate;
  archive lifecycle incl. re-download + cleanup; bad frequency 422; run-now; **daemon tick
  runs once and the second tick proves the claim**); build green; 17 cards live, hod sees 16.

### 2026-07-05 · actor=interactive · branch=`main` · 🗝️ Parity build Phase 4 — store-keeper toolbox
- **Stock count workflow:** `GET/POST /entry/count-sheet` — site stock list (derived
  site-stock SQL) → SK enters counted qtys → variances stage adjustments via
  `ledger.stage_adjustment` (reason validated; server recomputes system qty) + one HOD
  notification. New StockCountPage (variance highlighting, reason picker).
- **Returnable items (tool loans):** built on the existing `returnable_items` table (no
  migration). Loan / list / mark-returned endpoints (SK exact-locked, site-scoped);
  **one-time overdue notifications** deduped via the legacy `whatsapp_alert_sent` flag,
  fired on list access; `returnables_overdue` count added to /meta/work-queues → gold nav
  badge. New ReturnablesPage (overdue rows tinted red).
- **SK qty-adjust at SMR approval:** `approve_smr` gains `qty_overrides` ({item_id: qty};
  0 withdraws a line; adjustment noted in the staged issue's remarks). SkRequestsPage now
  opens a review modal with per-line editable quantities.
- **Bin locations:** `GET /entry/bins/{sap}` (port of legacy `get_item_bin_locations` —
  distinct recent `receipts.Bin_Location` per site); IssuePage shows "Pull from bin" tags
  under the material picker.
- **Verified:** service_tests **98 → 108/108** · build green · live: overdue loan →
  notification fired once (dedup proven) → badge 1 → returned → badge 0; count sheet
  renders 50 rows; SK override 2→1.5 asserted in the rolled-back suite.

### 2026-07-05 · actor=interactive · branch=`main` · 🏭 Parity build Step 3 — warehouse completion (returns-from-site + history)
- **Returns-from-site (disposition workflow):** built on the existing (previously unused)
  `po_returns` table — NO new table/migration. `GET/POST /warehouse/returns` +
  `POST /warehouse/returns/{id}/disposition` (open → hold | return_to_vendor | scrap |
  rework → closed; closed is terminal → 409). Warehouse-bound users guarded via the PO's
  assignment (403 cross-warehouse; unbound fail-closed); every action audited; logistics
  notified on create + return_to_vendor. NB the legacy audit's "returns_from_site /
  return_disposition" table names were paraphrases — `po_returns` is the real mechanism.
- **History & throughput:** `GET /warehouse/history` — completed DNs (status ∉ prepared/
  in_transit), fulfilled assignments, DN counts by status + RL/BL family; warehouse-scoped.
- **FE:** WarehousePage gains "Returns from Site" (record modal + per-row disposition
  select) and "History" (throughput tags + two tables) tabs.
- **Verified:** service_tests **92 → 98/98** · build green · live: create→disposition→close
  →409 lifecycle + all 4 tabs render.

### 2026-07-05 · actor=interactive · branch=`main` · 🏗️ Parity build Steps 1–2 — role locks + HOD operations pack
From the user-approved feature-parity audit (see the audit in-session; plan phases 1–10).
- **Step 1 (security):** `/entry/*` staging writes exact-locked to store_keeper(+admin) —
  legacy Entry Log parity; nav group hidden for other roles. **Warehouse_ID server binding:**
  JWT/user carry `warehouse_id`; warehouse_user pinned on assignments/DNs (param + row-level
  guards, DN-create 403 cross-warehouse; unbound → fail closed); FE pins the picker.
- **Step 2 (HOD ops pack):** PATCH edit of staged rows pre-approval (per-kind whitelist —
  NB returns use `Return_Reason`; adjustments recompute `variance`), `GET /hod/preflight`
  (negative-stock deficit table over pending issues), bulk-approve (per-id transactions),
  `GET /hod/low-stock` (+30d burn, days-of-supply, suggested reorder) + LowStockPage +
  nav, `POST /hod/prs/auto-draft` (below-minimum → draft PR via create_pr), PR PDF download
  (shared fpdf renderer). ApprovalsPage: edit modal + row-selection bulk commit + pre-flight
  banner.
- **Verified:** service_tests **84 → 92/92** · build green · live: hod nav lost Data Entry,
  PATCH edit persisted + audited, low-stock (0 rows = matches SQLite v_site_stock), route
  order gotcha fixed (`/hod/preflight`, not `/pending/preflight`).

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
