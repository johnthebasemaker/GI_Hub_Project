# NEW-STACK BUILD — Handoff (START HERE for a fresh chat)

Single entry point for continuing the **new React + FastAPI + PostgreSQL** build of
GI Hub. The **live Streamlit app is unchanged and stays on SQLite** — the new stack
is a *separate* set of processes. Per-slice history is in
[`docs/POSTGRES_MIGRATION.md` §8](POSTGRES_MIGRATION.md). SME rules live in
[`handoff.md`](../handoff.md) (SME Canon). Last updated 2026-07-06.

---

## 🎯 CURRENT STATUS (2026-07-06) — read this, then [`PROJECT_STATUS.md`](PROJECT_STATUS.md)

**🧊 CODE FREEZE.** The new stack is **feature-complete far beyond the original
ten slices** — since 2026-07-05 four more full programs landed on `main`:

- **Man-Hours & Labor Tracking** (Phases 10 + 11A/B/C): roster, timesheets +
  xlsx import, MH estimator, variance, SME link layer (Equipment Scorecard),
  auto-draft estimates + manpower forecast — exact `{hod, admin}` lock.
- **Intelligence Layer** (AI-0…AI-5): SSE Hub Assistant, PR/PO PDF extraction,
  handwriting-OCR job queue (`ai_jobs` table), client-side QR Smart Scan +
  tool vision, NL→SQL on the true read-only `gi_ai_ro` PG login, AI insights
  + streaming EOD summary. All local Ollama; per-feature admin-console flags.
- **SME React rebuild** (S1…S5): the full read-facing estimator — dual
  TS/Python cascade engine with golden-fixture parity (509 comparisons,
  both sides in CI), Dashboard (both sub-views), drag-priority Session
  Builder + client-side suggestion simulations + `?scenario=` sharing,
  Location/matrix reports, Execution Plan (3 sub-views), virtualized Total
  Overview. **S6 (Master Data CRUD) deferred to cutover by locked ruling.**

Gates at freeze: **service_tests 352/352 · bug_check 599/0 · parity_check 5/5 ·
parity:sme 509 · frontend build ✅ · alembic clean.** The deploy kit
(`deploy/` + [`docs/DEPLOY.md`](DEPLOY.md)) still has **NOT been run against
any server.**

**What's left (user's call, in his order):** Phase 7 WhatsApp/email outbox
(waiting on the user's Meta permanent token) · deployment cutover + Phase B
restructure · SME S6 after cutover. **No feature work is in progress — do NOT
write code until the user brings Meta keys or the cutover go-ahead.** Full
resume snapshot: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

---

## 0. GOLDEN RULES — do not break old or new

1. **Never edit `database.py` or the Streamlit app / `pages_internal/`** for new-stack
   work. The new stack lives only in `backend/api/**` and `frontend/**`.
2. **Old app must stay green after every change:** `.venv/bin/python bug_check.py`
   → **599 / 0**, `.venv/bin/python test_ui_crawler.py` → **21 / 21**.
3. **SME is FROZEN** — read the `sme_*` tables only, never write; order by an explicit
   PK, never rowid (SME Canon Rule 1).
4. **Keep local PG == SQLite.** When you test writes against local Postgres, delete
   your test rows afterward (target them *exactly* — a `LIKE` once nuked a real DN).
   If unsure, **reset** with `dual_ci` (below): it wipes + re-copies all 64 tables
   from `gi_database.db`. Then `parity_check.py` must pass.
5. **Use SQLAlchemy Core/ORM with quoted identifiers** (that's *why* PG works where the
   raw-SQL Streamlit app can't — PG folds unquoted mixed-case). Never hand-roll
   unquoted mixed-case SQL.
6. Verify writes on real PG, then clean up. Every ledger write is audited to
   `system_audit_log`.

## 0b. Architecture rules the user locked
1. SME feature-frozen (read views). 2. No rowid on PG (explicit PK order).
3. `sme_inventory_seed` strictly separate from ERP `inventory` (derive live qty).
4. Enforce `Site_ID` scoping. 5. Async SQLAlchemy (`AsyncSession` + asyncpg pool).
6. React state decoupled — clean REST JSON.

---

## 1. Run it locally (exact steps)

- **Local Postgres:** `postgres@127.0.0.1:5433/gihub` (Homebrew `postgresql@16`,
  trust auth, throwaway cluster under the session scratchpad). Superuser role is
  `postgres` (NOT the OS user).
- **Populate / reset PG from SQLite** (also verifies 64/64 table parity):
  ```
  DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
    .venv/bin/python backend/dual_ci.py --source gi_database.db
  ```
- **Backend (FastAPI, :8000):** `./run_api.sh`  → http://localhost:8000/docs
- **Frontend (Vite/React, :5173):** `cd frontend && npm install && npm run dev`
  → http://localhost:5173 (Vite proxies `/api` → `:8000`).
- **Logins (migrated + seeded users):** `admin`/`admin2026` (sees everything),
  `hod`/`hod2026`, `supervisor`/`super2026`, `worker`/`floor2026` (store_keeper).
  The migrated `Warehouse`/`Logistics` users exist but their passwords are unknown —
  test those roles as **admin** (level 4).
- **Deps** (in `requirements.txt`): fastapi, uvicorn, SQLAlchemy, **asyncpg**,
  **greenlet**, **PyJWT**, bcrypt, pyotp, psycopg2-binary.

## 1b. Verify-nothing-broke checklist (run before AND after any change)
```
.venv/bin/python bug_check.py                                   # 599/0  (old app)
.venv/bin/python test_ui_crawler.py                             # 21/21  (old app)
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python backend/dual_ci.py --source gi_database.db   # 64/64 table parity
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python backend/api/parity_check.py --source gi_database.db  # 5 derived views PASS
DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
  .venv/bin/python -m backend.api.service_tests                 # 78/78 (rolled-back services + auth/role guards + JWT + registration + site scoping + token refresh)
npm run build --prefix frontend                                 # tsc + vite green
```

---

## 2. Code map

**Backend `backend/api/`:** `main.py` (wiring, /health, /meta/*) · `auth.py`
(login/JWT/bcrypt/TOTP, `get_current_user`, `require_level`, `require_roles`) ·
`db.py`+`config.py` (async engine, `DATABASE_URL`→asyncpg) · `crud.py` (generic
read + master-data write router factory) · `stock.py` (derived stock views) ·
`entry.py` (data-entry → stages to `pending_*`) · `hod.py` (approvals/commit +
burn-rate + PR submit) · `logistics.py` · `warehouse.py` · `receiving.py`
(site DN → pending_receipts) · `requests.py` (supervisor) · `sme.py` (read-only) ·
`services/` (`ledger.py`, `procurement.py`, `warehouse.py`, `supervisor.py`) ·
`parity_check.py` (5 derived-view ports vs SQLite; CI gate).

**Frontend `frontend/src/`:** `auth/AuthContext`+`pages/LoginPage` · `components/
AppLayout` (role-gated nav via `buildMenu(level, role)`) · `api/hooks.ts` (all
TanStack Query hooks) · `config/entities.ts` · `pages/` (Dashboard, Stock, Records,
MasterData, Receive/Issue/Return/Adjust, IncomingDeliveries, Approvals, BurnRate,
HodPrs, Logistics, Warehouse, Supervisor, SkRequests, Sme) · `theme/` (`tokens.ts`
= brand palette source of truth, `themes.ts` = dark/light/sider AntD configs,
`ThemeContext` = dark-first toggle) + `index.css` (gradients, sider rail, keyframes).

**CI:** `.github/workflows/postgres-dual-ci.yml` — bug_check (SQLite) + dual_ci +
parity_check against a `postgres:16` service.

---

## 3. ✅ DONE — implemented + verified on the new stack

- **Auth & RBAC:** login + JWT (PyJWT), bcrypt verify, TOTP *verify*, role-gated nav
  (`require_level`/`require_roles`). 401 without token; per-role menus.
- **Read layer:** Dashboard (KPIs, inventory-by-category, expiring) · Stock (derived
  live/by-site/lots/expiring, parity-tested) · Records browsers (inventory, receipts,
  consumption, returns, lots, POs, equipment) · `/meta/*`.
- **Master-data CRUD:** vendors, warehouses, employees (create/edit/delete).
- **Ledger services + staging→approval:** Receive / Issue (FEFO, allow-and-log over-
  issue) / Return / Adjustment all **stage** to `pending_*`; **HOD Approvals** commit
  them to the ledger (FEFO, auto-lot, PR-close, audit) or reject.
- **HOD portal:** Approvals (receipts/issues/returns/adjustments) · Burn-rate ·
  submit a PR to Logistics.
- **Logistics portal:** incoming PR queue → create PO (RL/BL split, po_items) → assign
  to warehouse.
- **Warehouse portal:** incoming assignments → acknowledge → receive (over-deliver
  guard) → prepare DN (RL/BL separation + available guard) → ship (in_transit).
- **Site receiving (loop closed):** in-transit DN → receive → `pending_receipts`
  (Material→SAP mapped) → HOD Approvals → ledger receipt *with DN/PO/WH trace*.
- **Supervisor portal:** create material request (worker validation, stock snapshot +
  availability flag) → SK approve → `pending_issues` (`SUPERVISOR_REQUEST`, `Source_Ref`)
  → HOD Approvals.
- **SME estimator (read-only):** summary + equipment + recipes/BOM + SQM progress +
  materials (derived `Available_Qty`, parity-tested).
- **PR creation (HOD):** raise a site PR from scratch — multi-line form (material
  picker off the inventory master, auto-assigned `PR-YYYYMMDD-NNNN`), then submit to
  Logistics. Procurement now runs end-to-end on the new stack (no more migrated-only PRs).
- **Admin console (admin only, level 4):** **user management** — list (no secrets) /
  create (bcrypt) / edit role+site+warehouse+phone / reset password / reset 2FA / delete,
  with last-admin & self-delete guards — plus a filterable **audit-log viewer** over
  `system_audit_log`. Every mutation is itself audited.
- **In-app notifications (sidebar bell):** per-user feed over `app_notifications` — new
  services fire `notify()` on `submit_pr`→logistics, `assign_po`→warehouse, `ship_dn`→site
  store-keeper, `create_smr`→SK, `approve_smr`→the supervisor. Bell = badge + popover with
  mark-read / mark-all; visibility is role/site/warehouse/user scoped (isolation + a
  mark-read guard). Surfaces the migrated notifications too.
- **Inventory Master-DB editor (admin):** add / edit / delete inventory master items
  (`/admin/inventory`) — opening-stock edits audited, delete blocked if the item has ledger
  movements. Closes the last read-only master-data gap.
- **2FA self-enrollment:** `Account → Security` — enroll (QR + manual key) → verify → on;
  disable with a code. Login already challenged enabled users; now they can turn it on.
- **Reports (≥hod):** `GET /reports/{key}?format=xlsx|pdf|csv` — stock / expiring /
  consumption / receipts / purchase-orders / inventory, each filterable, rendered by
  openpyxl / fpdf / csv. A Reports page downloads them (authenticated blob).

Full role → workflow loop runs on Postgres. **~89 API endpoints.**

---

## 4. 🚧 PENDING — NOT yet on the new stack (the real backlog)

### 4a. Cross-cutting systems NOT ported (the user asked to confirm these)
- ~~**In-app notifications**~~ ✅ **DONE 2026-07-04** — `services/notifications.py` +
  `/notifications` router + `NotificationBell`; wired to 5 procurement events **+ staging→HOD
  + HOD approve/reject→submitter**. NOT yet wired: DN reschedules, cross-site views.
- **WhatsApp** — `whatsapp_queue` + `whatsapp_worker.py` + Twilio/Meta sender. Fires on
  PR/PO/DN/reschedule events. **NOT ported.** STATUS 2026-07-05: the legacy worker already
  speaks the official **Meta Cloud API** (`WHATSAPP_PROVIDER=meta`); the user is running
  Meta Business Verification now. New-stack port (wa_outbox + webhook) is ON HOLD until the
  permanent access token exists — do not build it before the user says go.
- **Email / mailer** — `mailer.py` (SMTP/Outlook), scheduled-report dispatch, delivery
  reminders. **NOT ported.**
- **Local LLM (Ollama)** — Hub Assistant Q&A, Reports "AI Insights", and the **OCR
  vision pipeline** (handwritten consumption lists, DN photo extraction, HEIC).
  **NOT ported.**
- **Computer Vision / Smart Scan (YOLOv8)** — returnable-tool auto-detection. **NOT
  ported** (gated off in prod anyway).
- ~~**User registration + approval**~~ ✅ **DONE 2026-07-04** — public `POST /auth/register`
  → `pending_users` → admin **Access Requests** page approve/reject (`/admin/pending-users`).
  Self-registrants can't request admin; approver overrides role/warehouse.
- ~~**User management UI**~~ ✅ **DONE 2026-07-04** (Admin console) — add / reset password /
  reset 2FA / delete / warehouse-bind. NOTE: users have no `status` column, so "disable" =
  **delete** (last-admin & self guards).
- ~~**2FA enrollment**~~ ✅ **DONE 2026-07-04** — `Account → Security` (`/auth/2fa/{status,
  enroll,verify,disable}`, QR + manual key). Still NOT done: **user registration + approval**
  (`pending_users` → admin approves).
- **Reservations** (`stock_reservations`, Available = Current − Reserved). **NOT
  ported.**
- **QR codes** — bin labels, employee badges, QR-approval flow. **NOT ported.**
- **Reports** — ✅ **PDF/Excel/CSV export DONE 2026-07-04** (`/reports`, 6 reports). Still
  NOT ported: the **scheduler** (daily auto-generate), **archive**, and **AI insights**.
- **PWA scan-and-stage** (the separate Phase-4 FastAPI PWA) — out of scope of this build.

### 4b. Portal tabs / features not yet built
- ~~**PR creation**~~ ✅ **DONE 2026-07-04** — HOD → Purchase Requests → **Create PR**
  tab (`POST /hod/prs`, `create_pr` service, auto `PR-YYYYMMDD-NNNN`). Procurement now
  runs end-to-end from the new stack.
- **DN approval chain** — SIMPLIFIED: warehouse DN → site receive → HOD approves the
  *receipt* (via staging). The old **Logistics-approve + HOD-approve DN** steps are not
  ported (approval moved to receipt level). Decide if the DN-level approvals are wanted.
- **Admin console** — ✅ user management + **audit-log viewer** + **inventory Master-DB
  editor** all DONE (2026-07-04). Still missing: global sites, settings/maintenance
  mode, backup, tool catalogue, logistics oversight.
- **HOD:** Cross-Site requests, My Requests, Site Config, DOC, QR Approval, In-Transit
  visibility, Lot Management UI (quarantine/dispose — backend lot-disposal exists via
  adjustments).
- **Logistics:** reschedules, force-close, vendor returns, material details, history,
  **manual (non-PR) PO**.
- **Warehouse:** returns-from-site, history/throughput.
- **Supervisor:** Intent-vs-Actual report; SK qty-adjust/withdraw at approval.
- **SME estimator:** only 5 read views; the old app's reporting tabs (Selective
  Equipment Entry, Session/Location/Equipment/System-Code reports, Execution Plan,
  Total Overview) and all **writes** (frozen) are not built.
- **Entry Log:** Returnable Items (tool tracking, CV), QR Label Request.
- **Man-Hours portal** — not started.
- **Ledger tables** (receipts/consumption/…): read-only via the generic CRUD **by
  design** — all writes go through the services/staging.

### 4c. Hardening / infra
- ~~**Service-level tests in CI**~~ ✅ **DONE 2026-07-04** — `backend/api/service_tests.py`
  (rolled-back service invariants + httpx auth/role guards), gated in `postgres-dual-ci.yml`.
  **29 checks.** Extend it whenever you add a write service.
- ~~**Per-endpoint role checks**~~ ✅ **DONE 2026-07-04** — audited every route; the one gap
  (master-data **writes** were auth-only) is fixed → `write_dep=require_level(3)`. Reads
  stay open by design; entry/receiving stay `get_current_user` (store-keeper stages → HOD
  approves). ~~If you want site-scoped reads … not done~~ ✅ **DONE 2026-07-05** —
  reads below level 3 are pinned to the user's own `Site_ID` (see §5 Tier 2).
- ~~**Real `JWT_SECRET`**~~ ✅ **DONE 2026-07-04** — `config.jwt_secret()`: production
  (`GI_ENV=production`) **fails fast** on a missing/weak/dev-default key; dev uses a safe
  long placeholder. **Deploy MUST set a strong `JWT_SECRET`** (≥32 chars) + `GI_ENV=production`.
- ~~**Frontend bundle** ~1.3 MB~~ ✅ **DONE 2026-07-04** — route-based `React.lazy` code-split;
  initial bundle **1,354 → 288 kB**.
- ✅ **Deploy kit DONE 2026-07-04** — turnkey `deploy/` (Docker compose: Postgres + FastAPI +
  nginx SPA/`/api`-proxy/TLS + certbot) + runbook [`docs/DEPLOY.md`](DEPLOY.md). **Not run
  against a server** — build/run it on the box when ready.
- **Still pending for cutover (user's call):** **provision + run** the Hetzner box (parked) +
  the **one-time SQLite→PG data migration** (runbook §4, `dual_ci`) + the **decision to make
  React primary** (point users at it). That's the whole remaining gap.

---

## 5. Improvement backlog (OPTIONAL — nothing here is started)
The build is done; these are ideas from a 2026-07-04 architecture review, ranked.
**None are in progress.** Pursue any only if the user asks. Current data volume is
tiny (receipts ~70, consumption 1 row, audit ~657), so perf items are "before scale,"
not urgent.

**Tier 1 — ✅ ALL DONE 2026-07-04 (the four small real gaps):**
- ~~**Alembic migrations (BE/DB)**~~ ✅ `backend/alembic/` + autogenerated baseline (64 tables).
  Cutover flow: `dual_ci` load → `alembic stamp head` → future changes via
  `revision --autogenerate` + `upgrade head`. `alembic check` confirms dual_ci's schema ==
  the baseline. See `backend/alembic/README.md`.
- ~~**CI frontend build/typecheck**~~ ✅ `frontend-build` job added to `postgres-dual-ci.yml`.
- ~~**React error boundary**~~ ✅ `ErrorBoundary` wraps `<App/>` → a page crash shows a
  recoverable antd Result instead of a white screen.
- ~~**Rate-limit public auth**~~ ✅ `ratelimit.py` — login/2fa 10/min, register 5/min per IP
  (keyed by nginx `X-Real-IP`). Per-process store; a hard cross-worker cap (Redis) is still open.

**Tier 2 — for a real multi-site / at-scale rollout:**
- ~~**Site-scoped reads (BE/security).**~~ ✅ **DONE 2026-07-05** — reads below logistics
  (level 3) are pinned to the user's own `Site_ID` (403 asking for another site, 404 on
  cross-site get-one, fail-closed for site-less users; `/stock/live` restricted to
  logistics/admin). Policy in `auth.site_scope()`/`resolve_site_param()`; enforced across
  CRUD reads, stock views, meta, HOD (incl. approve/reject guards), receiving, reports, SME.
- ✅ **ALSO DONE 2026-07-05 (were Tier-3/UX):** **access/refresh token split** (15-min JWT +
  rotating httpOnly refresh cookie, `auth_sessions` table, reuse detection, revoke on
  logout/password-reset/user-delete; SPA silently refreshes → "session expired" toast only
  when truly over) · **sidebar work-queue badges** (`/meta/work-queues`).
- **DB indexes on hot columns (DB/perf).** No indexes beyond PKs. Before real volume, add
  indexes on `SAP_Code` / `Site_ID` / `Date` for receipts/consumption/returns/lots (the
  derived-stock queries `TRIM(SAP_Code)`-join + GROUP BY these). Cheap insurance.
- **Normalize `SAP_Code` whitespace (DB).** Source data has stray spaces (`" 1002 "`), which
  is why queries `TRIM()` everywhere; a one-time trim (on the SQLite source) would let
  indexes work and simplify SQL. Touches the frozen data layer — do carefully.

**Tier 3 — nice-to-have / later:**
- Frontend E2E smoke tests (Playwright) — no UI tests exist; service_tests cover the API only.
- ~~Friendlier session-expiry UX~~ ✅ **DONE 2026-07-05** — silent refresh + expiry toast
  (part of the access/refresh token split above).
- Structured logging + request IDs for prod observability. · ~~Dark mode / a11y polish~~
  ✅ **DONE 2026-07-05** — navy/gold theme + dark/light toggle + `prefers-reduced-motion`
  guards (UI overhaul, `POSTGRES_MIGRATION.md` §8). Deeper a11y (full WCAG audit) still open.
- DB foreign keys for referential integrity (stricter; risky on migrated data).
- Feature parity leftovers: peripheral Logistics/Warehouse tabs (reschedules, force-close,
  vendor-returns, history, manual PO); report scheduler/archive; DN-reschedule/cross-site
  notification events; SME reporting tabs (frozen — read-only); Man-Hours portal.
- **Not ported (Streamlit-only, intentional):** WhatsApp, email/mailer, local-LLM (Ollama)
  Q&A + OCR, computer-vision. Larger integrations — only if the business needs them post-cutover.

## 6. Where the detail lives
- Per-slice build log + verification: `docs/POSTGRES_MIGRATION.md` §8 (newest first).
- SME Canon + overall project handoff: `handoff.md`.
- Commit history: `git log --oneline` (each slice is one `feat(...)` commit).
