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

Full role → workflow loop runs on Postgres. **~76 API endpoints.**

---

## 4. 🚧 PENDING — NOT yet on the new stack (the real backlog)

### 4a. Cross-cutting systems NOT ported (the user asked to confirm these)
- **In-app notifications** — the old app fires `queue_app_notification` on every
  procurement event (`app_notifications` table + a sidebar bell). The new services
  deliberately **omit** notifications. → build a `/notifications` feed + bell.
- **WhatsApp** — `whatsapp_queue` + `whatsapp_worker.py` + Twilio/Meta sender. Fires on
  PR/PO/DN/reschedule events. **NOT ported.**
- **Email / mailer** — `mailer.py` (SMTP/Outlook), scheduled-report dispatch, delivery
  reminders. **NOT ported.**
- **Local LLM (Ollama)** — Hub Assistant Q&A, Reports "AI Insights", and the **OCR
  vision pipeline** (handwritten consumption lists, DN photo extraction, HEIC).
  **NOT ported.**
- **Computer Vision / Smart Scan (YOLOv8)** — returnable-tool auto-detection. **NOT
  ported** (gated off in prod anyway).
- **User registration + approval** ("Request Access" → `pending_users` → admin
  approves). **NOT ported** — the new app only *logs in* existing users.
- ~~**User management UI**~~ ✅ **DONE 2026-07-04** (Admin console) — add / reset password /
  reset 2FA / delete / warehouse-bind. NOTE: users have no `status` column, so "disable" =
  **delete** (last-admin & self guards). Still NOT done: **2FA enrollment** (login
  *verifies* TOTP but there's no enroll/QR screen) and **user registration + approval**.
- **Reservations** (`stock_reservations`, Available = Current − Reserved). **NOT
  ported.**
- **QR codes** — bin labels, employee badges, QR-approval flow. **NOT ported.**
- **Reports** — PDF/Excel generation, scheduler, archive, AI insights. **NOT ported.**
- **PWA scan-and-stage** (the separate Phase-4 FastAPI PWA) — out of scope of this build.

### 4b. Portal tabs / features not yet built
- ~~**PR creation**~~ ✅ **DONE 2026-07-04** — HOD → Purchase Requests → **Create PR**
  tab (`POST /hod/prs`, `create_pr` service, auto `PR-YYYYMMDD-NNNN`). Procurement now
  runs end-to-end from the new stack.
- **DN approval chain** — SIMPLIFIED: warehouse DN → site receive → HOD approves the
  *receipt* (via staging). The old **Logistics-approve + HOD-approve DN** steps are not
  ported (approval moved to receipt level). Decide if the DN-level approvals are wanted.
- **Admin console** — ✅ user management + **audit-log viewer** DONE (2026-07-04). Still
  missing: **Master DB Editor (inventory CRUD)**, global sites, settings/maintenance
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
- **Service-level parity tests in CI** (today only the 5 derived-view ports are gated;
  the write services are verified manually then cleaned). Add rolled-back-txn service
  tests.
- **Per-endpoint role checks** — nav is role-gated and portal routers self-guard, but
  some generic *reads* allow any authenticated user. Tighten if needed.
- **Real `JWT_SECRET`** (dev default today) + **deploy** (Hetzner is parked per the
  user) + a **cutover decision** (make React primary, re-sync data).
- **Frontend bundle** is one ~1.3 MB chunk — code-split later.

---

## 5. Suggested next steps (ask the user which)
The operational + estimator core is complete, **procurement runs end-to-end** (PR
creation, 2026-07-04), and the **Admin console** (user management + audit viewer,
2026-07-04) has landed. Highest-value next options:
**(a)** in-app notifications feed (bell), **(b)** Reports (generate/export), **(c)** the
peripheral Logistics/Warehouse tabs, **(d)** hardening (service CI tests, per-endpoint
roles) before cutover, or **(e)** finish the admin surface — inventory **Master-DB editor**
+ **2FA enrollment/QR** + user registration-approval.
Notifications/WhatsApp/mail/LLM are larger integrations — scope explicitly before starting.

## 6. Where the detail lives
- Per-slice build log + verification: `docs/POSTGRES_MIGRATION.md` §8 (newest first).
- SME Canon + overall project handoff: `handoff.md`.
- Commit history: `git log --oneline` (each slice is one `feat(...)` commit).
