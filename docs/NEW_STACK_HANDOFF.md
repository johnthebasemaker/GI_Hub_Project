# NEW-STACK BUILD — Handoff (START HERE for a fresh chat)

Single entry point for continuing the **new React + FastAPI + PostgreSQL** build of
GI Hub. The **live Streamlit app is unchanged and stays on SQLite** — the new stack
is a *separate* set of processes. Per-slice history is in
[`docs/POSTGRES_MIGRATION.md` §8](POSTGRES_MIGRATION.md). SME rules live in
[`handoff.md`](../handoff.md) (SME Canon). Last updated 2026-07-04.

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
  .venv/bin/python -m backend.api.service_tests                 # 52/52 (rolled-back services + auth/role guards + JWT + registration + submitter-resolution)
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
HodPrs, Logistics, Warehouse, Supervisor, SkRequests, Sme).

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
  PR/PO/DN/reschedule events. **NOT ported.**
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
  approves). If you want site-scoped reads (a store keeper only seeing their own site's
  records), that's a further, larger change — not done.
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

## 5. Suggested next steps (ask the user which)
The operational + estimator core is complete; **procurement end-to-end** (PR creation),
the **Admin console** (users · audit viewer · **inventory Master-DB editor**), the
**in-app notification bell**, **2FA self-enrollment**, **Reports** (Excel/PDF/CSV), a
**hardening pass** (service+guard tests in CI · master-data write gate), and **cutover
readiness** (JWT_SECRET fail-fast · code-split bundle) have all landed (2026-07-04). The new
stack is feature-rich, self-sufficient, and ship-ready — only the **deploy + make-React-primary
decision** (yours) remains for actual cutover. Highest-value next options: **(a)** the
peripheral Logistics/Warehouse tabs, **(b)** report scheduler/archive, or **(c)** pull the
trigger on **cutover** (deploy). (Notifications now cover procurement + staging→HOD +
approve/reject→submitter; only DN-reschedule/cross-site events remain unwired.)
WhatsApp/mail/LLM are larger — scope first.

## 6. Where the detail lives
- Per-slice build log + verification: `docs/POSTGRES_MIGRATION.md` §8 (newest first).
- SME Canon + overall project handoff: `handoff.md`.
- Commit history: `git log --oneline` (each slice is one `feat(...)` commit).
