# GI Hub ERP — Handoff

---

> # 🛑 READ THIS FIRST — SME ↔ ERP INTEGRATION CANON (locked 2026-06, round 20.5.1)
>
> The standalone **Smart Material Estimator (SME)** project has been fully merged into this ERP. The integration is **feature-complete and LOCKED**. A fresh AI session MUST internalize the rules below before touching anything under `pages_internal/material_estimator_portal.py`, `scripts/sme_bootstrap.py`, or any `sme_*` table / helper in `database.py`. These rules were each written *because the mistake was actually made and cost a debugging round.*
>
> ### The one-paragraph mental model
> The SME UI is a **literal drop-in** of the original standalone app (one 7,200-LOC file, `pages_internal/material_estimator_portal.py`). It reads the ERP database through a set of compatibility **SQLite VIEWs** (`equipment`, `recipe`, `sqm_progress`, `locations`, `types`, `consumption_log`, `sme_materials_view`) that alias the real `sme_*` tables into the lowercase/dotted column names the SME's legacy SQL expects. **SME inventory data is strictly isolated** from the ERP `inventory` ledger: the SME baseline lives in `sme_inventory_seed`, and live quantities are *derived* (never stored) as `Available_Qty = Initial_Qty + Received − Consumed` via the `sme_materials_view` SQL view.
>
> ### ⚠️ STRICT AI DIRECTIVES — violating any of these will break production or corrupt the ledger
>
> **RULE 1 — NEVER `ORDER BY rowid` on SME data.**
> Every SME table the UI touches in Master Data is accessed through a SQLite **VIEW**, and **views have no `rowid`**. `SELECT … ORDER BY rowid` raises `no such column: rowid`; if it's inside a `try/except`, it silently returns an empty result and the UI shows *"No records found"* even though the data exists. Always order by an **explicit primary key**: `id` for `equipment`/`recipe`, `material_code` for `sme_materials_view`. (This was the R20.5.1 "empty grids" bug.)
>
> **RULE 2 — NEVER mingle `sme_inventory_seed` with the ERP `inventory` table.**
> They are deliberately, permanently separate. `sme_inventory_seed` is the **SME-owned** baseline (loaded from `Materials_DetailsAvailable_Qty.xlsx`). ERP `inventory` is the **ERP-owned** ledger-backed stock. SME Master Data CRUD writes go to `sme_inventory_seed` ONLY. The live Available_Qty shown anywhere in the SME portal is computed by `sme_materials_view` (seed + ERP receipts − ERP consumption, joined via `SAP_Code → inventory.Material_Code`). Do **not** "simplify" by pointing SME reads at ERP `inventory`, and do **not** write SME quantities into ERP `inventory`. Doing either re-introduces the qty=0 / clutter bugs and risks corrupting the ERP ledger. (This isolation was an explicit, repeated user directive.)
>
> **RULE 3 — ALWAYS enforce `Site_ID` scoping on new queries/reports.**
> The ERP is multi-site (`HQ`, `CNCEC`, …). `sme_equipment` and `sme_sqm_progress` are **site-scoped** (carry `Site_ID`); `sme_recipe` and `sme_inventory_seed` are **global** by design (recipes and the materials master are not per-site). When you write a new query, report, or helper that reads site-scoped data, thread `Site_ID` through it — mirror the existing `get_sme_equipment(site_id=…)` / `get_sme_sqm_progress(site_id=…)` signatures. Never assume a single site.
>
> **RULE 4 — NEVER hallucinate the original standalone SME project's internals.**
> The original SME `app.py` / `allocation_engine.py` source is **not** in this repo beyond the vendored copies (`material_estimator_portal.py`, `material_estimator_engine.py`). If a task requires deep analysis of the *original* standalone SME architecture (its un-merged behavior, original tabs, original SQL, original Excel schemas), **STOP and explicitly ask the user to provide the original SME project files.** Do not invent column names, sheet names, or behaviors from memory.
>
> ### 🔒 Project direction (locked)
> **The SME integration is DONE and FROZEN.** Future development focus shifts to **the ERP project's own features** (see §3 "Remaining Features — Prioritized"). Touch the SME code only to fix a regression that is *proven* against the 522-green test baseline — and add a regression test for any SME fix. Do not refactor the SME drop-in for style; it is intentionally a verbatim port.
>
> **Full architecture details: §2W (R20.5 + R20.5.1 + R20.5.2). Test baseline (2026-06-30): `.venv/bin/python bug_check.py` → 560 passing / 0 failed in the full venv · `test_ui_crawler.py` → 21/21. (A bare env without optional deps skips some checks.)**
>
> ### ⚠️ NOTE FOR A FRESH SESSION — recent SME edits are INTENTIONAL, not regressions
> The 2026-06-29/30 session made several **surgical, user-requested, regression-tested** edits to the frozen drop-in (`material_estimator_portal.py`) — admin site picker, hidden SME sidebar, KPI-modal drill-downs, filter cross-filtering, and the cold-start import fix. They are guarded by `bug_check.py` checks under the **"Material Estimator"** group. Do NOT revert them as "frozen-file violations" — they extend, not refactor, and all 560 checks stay green. The freeze still holds for *new* work: touch SME only for a proven regression + add a test.
>
> ### 🧹 Two footguns that bit us twice (do not repeat)
> - **Deleting/renaming an SME module?** `grep -rn "pages_internal.material_estimator" --include=*.py` first. A single missed inline import inside a function body stays invisible until that code path renders (bare-mode import tests skip Streamlit-gated branches). This crashed the SK page in R20.5.2.
> - **Stashing selections/orderings in `st.session_state`?** Reconcile that state against freshly-loaded data EVERY run. Persisted drag-order held tags removed by a re-bootstrap → `IndexError` in the Location Report (R20.5.2).

---

**Last update:** 2026-07-04 — **Operational ERP + estimator core is feature-complete on the new FastAPI+PostgreSQL+React stack; procurement runs end-to-end, an admin console manages users + audits, and a notification bell lights up the workflow.** Built + verified: auth/roles · staged data-entry · HOD approvals/burn-rate · **full procurement loop** (**HOD creates PR** → submit → Logistics PO → assign → Warehouse receive → DN → ship → site receive → HOD approve → ledger) · Supervisor requests → SK · SME estimator (read-only) · **Admin console** (user management + audit-log viewer) · **in-app notifications** (sidebar bell, role/site/warehouse/user-scoped, wired to 5 procurement events). This session added the **PR-creation UI**, the **Admin console** (users · audit · inventory Master-DB editor), the **notification bell**, **2FA self-enrollment**, **Reports** (Excel/PDF/CSV export), a **hardening pass** (service+guard tests gated in CI · master-data write role-gate), and **cutover readiness** (JWT_SECRET fail-fast in production · code-split bundle 1.3 MB→288 kB) — the new stack is now feature-rich, self-sufficient, and ship-ready (only the deploy + make-React-primary decision remains). Streamlit/SQLite untouched. Tests: **599 bug_check / 0 · UI crawler 21/21 · derived-view parity PASS · dual_ci 64/64 · service+guard tests 44/0 · frontend `npm run build` green**. Per-item detail in commit messages (`git log --oneline`).

> ### 📍 WHERE WE ARE — read this first
>
> **🆕 CONTINUING THE NEW REACT/FASTAPI/POSTGRES BUILD? → START WITH [`docs/NEW_STACK_HANDOFF.md`](docs/NEW_STACK_HANDOFF.md).** It is the self-contained fresh-chat entry point: how to run it, login creds, golden rules (don't touch `database.py`; SME frozen; keep local PG == SQLite), the full DONE list, and the explicit PENDING/NOT-yet-ported backlog (in-app notifications, WhatsApp, email/mailer, local-LLM/OCR, CV, user-registration + user-management + 2FA-enrollment, reservations, QR, reports, man-hours, admin console, PR-creation UI, DN-approval chain, peripheral logistics/warehouse tabs). Per-slice detail is in `docs/POSTGRES_MIGRATION.md` §8.
>
> **The 3-tier stack now exists (all local, all verified):** Streamlit+SQLite (unchanged, production) ‖ **NEW** FastAPI+PostgreSQL API (`backend/api/`, `./run_api.sh` → :8000/docs) ‖ **NEW** React/AntD SPA (`frontend/`, `npm run dev` → :5173). The React app talks to the API via a Vite `/api`→:8000 proxy. Both new tiers are the future direction; the live app is still Streamlit+SQLite.
>
> ### 🧭 DID / DOING / WANT (the plan the user set 2026-07-02)
> - **DID (all committed + pushed, verified on real PG, SQLite/Streamlit untouched):**
>   1. ✅ **Ledger services layer** — receipt / consumption (FEFO, allow-and-log) / returns / adjustments, each ported + verified.
>   2. ✅ **Staging → HOD approval** — data-entry stages to `pending_*`; HOD Approvals commits (reuses the ledger services).
>   3. ✅ **Auth** — login + JWT + bcrypt/TOTP + role-gated nav.
>   4. ✅ **Portals** — HOD (approvals/burn-rate/PR-submit) · Logistics (PR→PO→assign) · Warehouse (receive→DN→ship) · Site receiving (DN→pending_receipts, **loop closed**) · Supervisor (requests→SK→pending_issues).
> - **DOING (current):** the **SME Material Estimator** — the last major portal (reads the `sme_*` compat views: equipment / recipe/BOM / sqm_progress / sme_materials_view). Scope core read/dashboard screens first, then outward. **⚠️ SME is FROZEN in the Streamlit app (SME Canon in this handoff) — the new build only READS the sme_* views; do NOT touch the drop-in.**
> - **WANT (after SME):** deferred peripheral tabs (Logistics reschedules/force-close/vendor-returns/history/manual-PO; Warehouse returns-from-site/history; Admin console; Reports; Man-Hours) · service-level parity tests in CI · 2FA-enrollment UI · per-endpoint (not just nav) role checks · then the React app can become the primary UI (cutover decision) with `JWT_SECRET`/deploy.
>   - **Deploy (Hetzner) stays parked** until the user asks.
>
> **DONE this session (all committed + pushed, newest first):**
> - **🚀 Cutover prep (non-deploy) — JWT_SECRET hardening + frontend code-split.** No deploy (parked); React-primary stays the user's call. **JWT:** `config.jwt_secret()`/`is_production()` — production (`GI_ENV=production`) **fails fast at import** on a missing/short/dev-default key; dev uses a long placeholder (no PyJWT warning). `auth.py` resolves `JWT_SECRET=jwt_secret()`. **Deploy MUST set a strong `JWT_SECRET` + `GI_ENV=production`.** **Bundle:** `App.tsx` `React.lazy` per-route + `AppLayout` `<Suspense>` around `<Outlet>` → **initial 1,354 kB → 288 kB** (each page/heavy-widget its own chunk; >500 kB warning gone). **Verified:** jwt guard (dev-ok / prod-weak-raises / prod-strong-ok); in-browser nav across 5 lazy pages loads + renders, sidebar persists, console clean. `service_tests` +4 → **44/0**. `bug_check` 599/0, dual_ci/parity PASS, build green. **Cutover now needs only: deploy + the decision to point users at React.** `backend/api/{config,auth,service_tests}.py`, `frontend/src/App.tsx`, `frontend/src/components/AppLayout.tsx`.
> - **📊 Reports — downloadable Excel / PDF / CSV exports.** The biggest capability the old app still had over the new stack. `reports.py` (level ≥2): `GET /reports` (+ per-report filters) and `GET /reports/{key}?format=xlsx|pdf|csv`. Six reports (stock/expiring/consumption/receipts/purchase-orders/inventory), each one query → any format via `to_xlsx` (openpyxl, styled), `to_pdf` (fpdf landscape, branded), `to_csv` (UTF-8 BOM); `StreamingResponse` with a `Content-Disposition` filename. Read-only. **+2 endpoints (85 paths).** **Verified live (PG):** valid xlsx (stock=51 rows = the view)/pdf(`%PDF`)/csv, site+days filters, guards 403/404/400/401. **In-browser (admin):** Reports page (6 cards + filters + Excel/PDF/CSV) → Excel download round-trips `GET /reports/stock?format=xlsx` 200 + toast; console clean. `service_tests` +5 → **40/0**. Frontend: `ReportsPage` + **Reports** nav (≥2) + `useReports`/`downloadReport` (authenticated blob). `bug_check` 599/0. **Next: peripheral tabs · more notif events · user registration · report scheduler · cutover prep.** `backend/api/{reports,main,service_tests}.py`, `frontend/src/pages/ReportsPage.tsx`, `frontend/src/{App,components/AppLayout,api/hooks}.tsx`.
> - **🗂️ Finish admin surface — inventory Master-DB editor + 2FA self-enrollment.** Closes the new stack's last self-sufficiency gaps. **Inventory editor** (`admin.py`, level 4): `POST /admin/inventory` (SAP unique→409), `PATCH /admin/inventory/{sap}` (Opening_Stock change → explicit `OPENING_STOCK_EDIT` audit), `DELETE /admin/inventory/{sap}` (**refuses if the SAP has any receipts/consumption/returns/lots/pending_*/pr_master rows** — no orphaning); reads still via open `/inventory`. **2FA enrollment** (`auth.py`, current user): `/auth/2fa/{status,enroll,verify,disable}` — enroll returns a pyotp secret + otpauth URI + **QR PNG data-URI**, secret stored but 2FA only turns on after a verified code (no lockout). **+7 endpoints (83 paths).** **Verified live (PG):** inventory guards (worker-403, dup-409, delete-with-movements-409, opening-stock audit); 2FA full cycle incl. login returning `mfa_required` after enable. **In-browser (admin):** Inventory Master create-via-modal round-trip; Security page **enroll→scan QR→verify→ON→disable→OFF** with real TOTP codes; console clean (fixed antd `Alert message`→`title`). `service_tests` +6 guards → **35/0**. Frontend: **Admin→Inventory** + **Account→Security** nav/pages, hooks. Test rows PG-only → `dual_ci` reset (inventory 306, admin 2FA off, parity PASS). `bug_check` 599/0. **Next: Reports · peripheral tabs · more notif events · user registration · cutover prep.** `backend/api/{admin,auth,service_tests}.py`, `frontend/src/pages/{InventoryAdmin,Security}Page.tsx`, `frontend/src/{App,components/AppLayout,api/hooks}.tsx`.
> - **🛡️ Hardening — service-level tests in CI + per-endpoint role audit.** (1) New `backend/api/service_tests.py`: **Suite A** exercises the write services in a txn and asserts effects via count-deltas then **rolls back** (create_pr+audit, submit_pr+notif, create_smr+notif, approve_smr+pending+notif, post_receipt+lot+audit, notification isolation+mark-read guard); **Suite B** drives the ASGI app with httpx for the auth/role matrix. **29/29**, PG byte-unchanged. Gated in `postgres-dual-ci.yml` (new step, real `JWT_SECRET`). (2) Route audit found the **master-data writes unguarded** — `make_read_router` POST/PUT/DELETE (vendors/warehouses/employees) were auth-only; fixed with `write_dep=require_level(3)` (reads stay open; entry/receiving stay get_current_user by design). **Verified:** service_tests 29/0, worker→`POST /vendors` now **403**, admin still passes; `bug_check` 599/0, dual_ci/parity PASS, build green. Backend-only (frontend untouched). **Next: Reports · peripheral tabs · finish admin (Master-DB editor, 2FA enroll) · more notif events · cutover prep.** `backend/api/{crud,main,service_tests}.py`, `.github/workflows/postgres-dual-ci.yml`.
> - **🔔 In-app notifications — sidebar bell + procurement-event wiring.** The new services fired nothing; now they light up a per-user bell over `app_notifications`. `services/notifications.py::notify()` ports `queue_app_notification`; `list_for`/`unread_count`/`mark_read`/`mark_all_read` port the bell visibility rule **verbatim** (`recipient_user=me OR (recipient_role=role AND site-any/match AND warehouse-any/match)`) with a mark-read guard. `notifications.py` router: `GET /notifications` (+unread-count) · `POST /{id}/read` · `POST /read-all` (`_ctx` reads live site/warehouse from `users`). **+4 endpoints.** Wired 5 events: `submit_pr`→logistics · `assign_po`→warehouse+wh · `ship_dn`→site store-keeper · `create_smr`→SK · `approve_smr`→the supervisor (feedback). **Verified live (PG):** correct targeting, **isolation** (store keeper never sees the logistics PR), mark-read/all persistence, cross-user mark-read→**404**, and it surfaces the 75 migrated notifications. Frontend: `NotificationBell` (Badge + Popover feed, click→mark-read+navigate, mark-all, optimistic count). Bell/list render correct across counts; console clean. **CAVEAT:** headless preview reports `document.hidden` → React render throttled, so the sub-second live badge decrement wasn't cleanly demo-able there (persistence + mount render are correct; optimistic path is standard). Test rows PG-only → `dual_ci` reset (parity PASS, notifications back to 75). `bug_check` **599/0**. **Next: Reports · peripheral tabs · hardening · finish admin (Master-DB editor, 2FA enroll) · more notif events.** `backend/api/{notifications,main}.py`, `backend/api/services/{notifications,procurement,warehouse,supervisor}.py`, `frontend/src/components/NotificationBell.tsx`, `frontend/src/{components/AppLayout,api/hooks}.tsx`.
> - **🛠️ Admin console — user management + audit-log viewer (admin only).** The new app could only *log in* existing users; now `admin.py` (`require_level(4)`) manages them and surfaces the `system_audit_log`. Ports auth.py's `add_user`/`reset_password`/`delete_user`: `GET/POST /admin/users` (bcrypt; list **never** returns `password_hash`/`totp_secret`), `PATCH /admin/users/{u}` (role/site/warehouse/phone), `POST .../reset-password`, `POST .../reset-2fa`, `DELETE /admin/users/{u}`, `GET /admin/roles`, `GET /admin/audit` (+`/meta`, filterable). **Guards:** last-admin demote/delete + self-delete blocked; every mutation audited. NOTE: `users` has no `status` col → "disable" = delete. **Verified live (PG):** guards 403/401, no-secret-leak, create→login (bcrypt round-trip), dup 409, 422s, reset-pw (new works/old 401), reset-2fa, self-delete 409, audit filters (47 actions/30 tables). **In-browser (admin):** Users create→delete round-trip; Audit 671 events with live capture + filter; console clean (`forceRender` modal). Frontend: **Admin** nav group (level 4) → `UsersPage` + `AuditLogPage`. Test users are PG-only → `dual_ci` reset (parity PASS, 8 users). `bug_check` **599/0** (database.py untouched). **+9 endpoints. Next: notifications · Reports · peripheral tabs · hardening · finish admin (inventory Master-DB editor · 2FA enrollment).** `backend/api/{admin,main}.py`, `frontend/src/pages/{Users,AuditLog}Page.tsx`, `frontend/src/{App,components/AppLayout,api/hooks}.tsx`.
> - **🧾 PR-creation UI — procurement now runs end-to-end on the new stack.** The new app could *submit* a PR and build a PO from it, but had no way to **create** one (PRs came from migrated data only). `services/procurement.py::create_pr()` ports `insert_manual_pr()` — validates + enriches each line against the inventory master (SAP must exist; Material_Code/Material_Name/UOM backfilled; TRIM-matched), inserts one row/line (`status='open'`, `workflow_state='draft'`, `logistics_status='site_draft'`), audits `CREATE_PR`; `_next_pr_number()` auto-assigns **`PR-YYYYMMDD-NNNN`** (daily, mirrors SMR). `POST /hod/prs` (`require_level(2)`, `CreatePRIn`/`PRLineIn`) → **67 endpoints**. **Verified live (PG):** create `PR-20260704-0001`, enrichment OK, guards worker-403 / bad-SAP-409 / qty≤0-409 / empty-422, submit → Logistics queue; **in-browser (admin):** Create-PR form → `POST /api/hod/prs` **201** → `PR-20260704-0002` → shows in Submit-to-Logistics. Frontend: `HodPrsPage` now tabbed (**Create PR** + **Submit to Logistics**), `useCreatePr()`, no new nav. Test PRs are PG-only → reset via `dual_ci` (parity **PASS**, 64/64). `bug_check` **599/0** (database.py untouched). **Next: notifications · Admin console · Reports · peripheral tabs · hardening.** `backend/api/{hod,services/procurement}.py`, `frontend/src/pages/HodPrsPage.tsx`, `frontend/src/api/hooks.ts`.
> - **🧪 SME Material Estimator (READ-ONLY) — last major portal.** SME stays **frozen** (Canon) — the new build only reads `sme_*`. `sme.py` (`require_level(2)`): `/sme/{summary,equipment,recipes,sqm-progress,materials}`. **`SQL_SME_MATERIALS`** ports the SQLite `sme_materials_view` (derived Available_Qty = seed+received−consumed) → parity-gated (`DERIVED_SME`), **PASS 22/22** (5 derived views now gated). **Verified live:** worker 403; summary 65 equipment / 3526 SQM / 86 recipes / 22 materials; all reads real. Frontend: **SME Estimator** nav (hod/admin) → `SmePage` (Dashboard KPIs + Equipment/Recipes/SQM/Materials tabs). `bug_check` **599/0** (SME drop-in untouched). **🎉 All major portals now on the new stack.** `backend/api/sme.py`, `frontend/src/pages/SmePage.tsx`, `backend/api/parity_check.py`.
> - **🛡️ Supervisor portal — material requests → SK approve → pending_issues.** `services/supervisor.py` + `requests.py` port `create_supervisor_request` (worker active+site-bound; per-line stock snapshot + Available_Flag; `SMR-YYYYMMDD-NNNN`), `approve_supervisor_request` (→ pending_issues `pending_hod`, `Work_Type=SUPERVISOR_REQUEST`, `Source_Ref=SMR:…` → **HOD Approvals → Issues**), reject. Endpoints `/requests` (create supervisor/admin; approve/reject store_keeper/admin). **Verified live:** create (Available_Flag short-flag ✓), guards (worker-create 403, supervisor-approve 403), SK approve → staged issue in HOD Approvals; test rows removed → **parity PASS**. Frontend: **Supervisor** nav (Material Requests: multi-item form + My Requests) + **Data Entry → Supervisor Requests** (SK approve/reject). `bug_check` **599/0**. **Next: SME estimator (last major portal).** `backend/api/{requests}.py`, `services/supervisor.py`, `frontend/src/pages/{Supervisor,SkRequests}Page.tsx`.
> - **🔁 Closed the loop — DN → site receipt → staging → ledger.** A delivered warehouse DN now feeds the SK/HOD receipt staging already built, closing the full circle: PR→PO→assign→WH receive→DN→ship→**site receive→pending_receipts→HOD approve→commit_receipt→ledger**. `services/warehouse.py`: `incoming_dns` + `stage_dn_receipt` (ports `sk_mark_dn_received`'s Material_Code→SAP_Code mapping but stages into pending_receipts, so HOD still approves). `receiving.py` router (`/site/incoming-dns`, `/site/dns/{dn}/receive`, auth + site-scoped). **Trace fix:** `commit_receipt` now carries DN/PO/Warehouse trace cols from the staged row onto the committed receipt. **Verified live (admin): whole loop → ledger receipt with `DN=… PO_src=… WH=…`;** then reset PG pristine via `dual_ci` (64/64 parity + derived-view parity PASS). Frontend: **Data Entry → Incoming Deliveries** (list in-transit DNs → Receive). `bug_check` **599/0**. **Next: Supervisor / SME portals.** `backend/api/{receiving,warehouse}.py`, `services/{warehouse,ledger}.py`, `frontend/src/pages/IncomingDeliveriesPage.tsx`.
> - **🏭 Warehouse portal — assignment → receive → DN → outbound.** `services/warehouse.py` + `warehouse.py` (`require_roles("warehouse_user","logistics")` — new exact-role guard) port `list_assignments_for_warehouse` (prices never joined), `acknowledge_assignment`, `record_warehouse_receipt` (bump Delivered_Qty + over-deliver guard + roll status), `create_delivery_note` (**RL/BL separation** + available-qty guard, `DN-<WH>-<date>-<seq>`), ship_dn (→in_transit). Endpoints `/warehouse/{assignments,assignments/{id}/items,assignments/{id}/acknowledge,assignments/{id}/receive,dns,dns/{dn}/items,dns/{dn}/ship}`. **Verified live (admin):** fresh chain → ack → receive (over-receive 409) → prepare DN (over-ship 409, RL/BL) → ship → in_transit; worker 403; test rows removed → **parity PASS**. Frontend: **Warehouse** nav (exact roles; `buildMenu` now role-aware) → WarehousePage (Assignments: Receive + Prepare-DN modals; Delivery Notes: expandable + Ship). `bug_check` **599/0** (database.py untouched). **Deferred:** DN→Logistics/HOD approve→**site pending_receipts** (loop back into SK staging), returns-from-site. **Next: Supervisor / SME, then DN→site-receipt bridge.** `backend/api/{warehouse,auth}.py`, `services/warehouse.py`, `frontend/src/pages/WarehousePage.tsx`.
> - **🚚 Logistics portal — PR → PO → assign (procurement chain).** `services/procurement.py` + `logistics.py` (`require_level(3)`) port `submit_pr_to_logistics`/`list_prs_for_logistics`/`create_po_manual` (header + po_items with **RL/BL family tagging**, flips PR→in_po)/`assign_po_to_warehouse`. Endpoints `/logistics/{prs,prs/{pr}/lines,pos,pos/{po}/items,pos/{po}/assign}` + HOD feeder `/hod/prs` + `/hod/prs/{pr}/submit`. **Verified live (as admin):** HOD lists PR 456789→submit→logistics queue→create PO (13 po_items, rl_bl tagged, PR→in_po)→assign WH-01; worker→/logistics 403; bad-warehouse 409. Test rows removed + PR reverted → **parity PASS** (also swept a stray consumption row from earlier UI testing). Frontend: **Logistics** nav (≥3) → Procurement (Incoming PRs + Create-PO modal; Purchase Orders + expandable items + Assign modal); **HOD** += Purchase Requests. `bug_check` **599/0** (database.py untouched). **Deferred:** reschedules/force-close/vendor-returns/history/manual-PO. **Next: Warehouse portal.** `backend/api/{logistics,hod}.py`, `services/procurement.py`, `frontend/src/pages/{Logistics,HodPrs}Page.tsx`.
> - **🏛️ Staging → HOD approval workflow + HOD portal (approvals + burn-rate).** User chose the old app's **stage → approve → commit** control over direct posting. `services/ledger.py`: `stage_*` write to `pending_*`/`stock_adjustments` (pending_hod); the existing `post_*` became the **commit** step (`commit_*` load pending → post to ledger → delete/mark); `reject_pending`. `entry.py` now stages. New `hod.py` router (`require_level(2)`): `/hod/pending`(+lists), `/hod/pending/{kind}/{id}/approve|reject`, `/hod/burn-rate`; `auth.py` += `require_level()`. **Verified live:** stage→pending (stock unchanged)→approve→committed (1001 3.01→8.01); worker→/hod 403; reject leaves stock untouched; burn-rate works; test rows cleaned → parity PASS. Frontend: entry pages say "submitted for approval"; **HOD** nav group (level ≥2) with Approvals (tabbed, count badges, Approve/Reject, site filter) + Burn Rate; verified in-browser as hod (HOD group shows, Master Data hidden, 26 pending returns listed). `bug_check` **599/0** (database.py untouched). **Next: Logistics/Warehouse/Supervisor/SME portals.** `backend/api/{hod,entry}.py`, `services/ledger.py`, `frontend/src/pages/{Approvals,BurnRate}Page.tsx`.
> - **🔐 Auth — login + JWT + role-gated SPA (ports bcrypt/TOTP/roles).** `backend/api/auth.py`: bcrypt verify + opt-in TOTP (pyotp, ±30s) + roles from config.py; `POST /auth/login` → JWT (PyJWT HS256 8h) or `{mfa_required, mfa_token}`; `POST /auth/login/2fa`; `GET /auth/me`; audits LOGIN/LOGIN_FAILED/2FA_FAILED. `get_current_user` guards reads/stock/meta; entry routes now record the **authenticated user** as actor (dropped `X-Actor`). Frontend: `auth/AuthContext` (localStorage token, 401→logout), axios interceptors, `LoginPage` (+2FA step), App gate, `AppLayout` user chip + Sign out + **role-gated nav** (Master Data ≥ level 3). **Verified live** on migrated users: unauth→401, admin/admin2026→JWT(level 4) sees Master Data, worker/floor2026→store_keeper(level 0) does not; gated reads 200 with token; test audit rows cleaned. `requirements.txt` += PyJWT. `bug_check` **599/0** (database.py untouched). **Next: per-portal screens.** Later: 2FA enrollment UI, per-endpoint role checks, `JWT_SECRET` in deploy. `backend/api/auth.py`, `frontend/src/{auth,pages/LoginPage}`.
> - **🧾 Ledger write core COMPLETE — Consumption (FEFO) + Returns + Adjustments (service → API → React).** All four ledger ops now live on the new stack. `post_consumption` (FEFO auto-tag earliest-expiry open lot; **allow-and-log** over-issue per locked [[fefo-enforcement-decision]] — warn, don't block), `post_return` (→ `returns`, reduces stock), `post_adjustment` (variance>0→synthetic receipt, <0→synthetic consumption STOCK_ADJUSTMENT + optional lot disposal). `POST /entry/{consumption,returns,adjustments}` + `GET /entry/adjustment-reasons`. **Verified live:** issue −1 + over-issue warning, return −0.5, adjustment surplus +2 (receipt R:72), 422/400 guards; test rows removed → PG==SQLite, parity re-run PASS. Frontend: Issue/Return/Adjust forms under Data Entry. `bug_check` **599/0** (database.py untouched). **Next: auth → per-portal screens.** `backend/api/{services/ledger,entry}.py`, `frontend/src/pages/{Issue,Return,Adjust}Page.tsx`.
> - **🧾 Ledger services layer — Receipts slice (service → API → React) with business-rule parity.** First real transactional write on the new stack. `backend/api/services/ledger.py::post_receipt()` ports `process_receipt_delivery()` exactly: insert receipt (+validated extra logistics cols), **auto lot** on expiry (`LOT-<YYYYMMDD>-<SAP>`), **mirror to `lots`** (Status='open'), **PR-fulfilment auto-close**, **audit** (`POST_RECEIPT`) — all atomic. `POST /entry/receipts` (`backend/api/entry.py`, pydantic + `X-Actor`, 404 unknown-SAP, 400 integrity). **Verified live:** 1001@CNCEC 3.01→8.01 (+5, identity math ✓), lot auto-created (5/5/open ✓), audit written ✓, test rows cleaned. Frontend: `ReceivePage.tsx` antd form + new **Data Entry** nav, wired to `useReceiptEntry()`. `bug_check` **599/0** (database.py untouched). **Next slices:** consumption (FEFO), returns, adjustments → then auth → then per-portal screens. `backend/api/{services/ledger,entry}.py`, `frontend/src/pages/ReceivePage.tsx`, `docs/POSTGRES_MIGRATION.md` §8.
> - **⚛️ React frontend (Vite + TypeScript + Ant Design) — runs & views locally.** New `frontend/` SPA on the FastAPI+PG stack (TanStack Query + React Router + axios). Screens: **Dashboard** (KPIs: 306 items / 2 sites / 2 expired + inventory-by-category + expiring-stock, live from the API), **Stock** (tabs = the derived views live/by-site/lots/expiring, with site + within-days filters), **Records** (config-driven read browsers for inventory/receipts/consumption/returns/lots/POs/equipment — pagination + Site_ID filter), **Master Data** (full add/edit/delete modals for vendors/warehouses/employees → the API's writable entities). Config-driven off `src/config/entities.ts`. Verified end-to-end in a browser against real PG data (dashboard, vendor CRUD modal, stock tabs all render); `npm run build` (tsc) green; console clean. Run: `./run_api.sh` (:8000) + `npm run dev` in `frontend/` (:5173). `frontend/**`, `.claude/launch.json`. **This is the first UI on the new stack — Streamlit is still the live app.**
> - **🧱 FastAPI backend v2 — derived stock (parity-tested) + master-data writes.** `/stock/live`·`/by-site`·`/lots`·`/expiring` are **Postgres-native ports** of the SQLite reporting views (`v_live_stock`/`v_site_stock`/`v_lot_balance`/`v_expiring_stock`), computed at request time (views NOT created on PG). Ports fix the real SQLite→PG gaps: quoted identifiers, all non-agg cols in GROUP BY, `julianday`/`date('now')` → PG date math (+ regex/`substring` guard for junk expiry text). **Accuracy proven by `backend/api/parity_check.py`** — order-independent multiset compare of each ported query vs its SQLite view on real data → **PASS all 4** (live 306/by-site 51/lots 10/expiring 2); now a **CI step**. Writes: **POST/PUT/DELETE for vendors/warehouses/employees only** (generic Core insert/update/delete + `.returning`, auto `created_at`/`updated_at`, 422 unknown-col, 400 integrity); **ledger tables stay read-only** (POST→405) pending a services layer (identity-math/FEFO/audit). Verified live (vendor CRUD cycle; 27 OpenAPI paths). SQLite untouched — **599/0 · 21/21**. `backend/api/{stock,crud,parity_check}.py`, `docs/POSTGRES_MIGRATION.md` §8.
> - **🚀 FastAPI REST backend (v1, PostgreSQL-only, async) — RUNNABLE & VIEWABLE locally.** New `backend/api/` (`main.py` / `crud.py` / `db.py` / `config.py`) serves the ERP over **async SQLAlchemy** (AsyncSession + asyncpg pool, rule #5) against local PG (`gihub` DB on 5433). **Read-only v1:** `/health`, `/meta/sites`, `/meta/inventory-summary` (exact GROUP BY counts) + list/detail for **10 core business entities** (inventory/receipts/consumption/returns/lots/purchase-orders/equipment/employees/vendors/warehouses) with `?site_id=` scoping (rule #4) + explicit-PK ordering (rule #2). A generic **Core-Table** router → clean JSON incl. space-named cols (`"Approved By"`); **blobs + secret-named cols scrubbed**, credential tables (users/pending_users/*_tokens/qr_approval_requests) **not exposed** (rule #3 isolation respected). Verified live on real PG data: site scoping HQ→13, int/str-PK detail, 404/422, `/docs` 200, 23 OpenAPI paths. Launch: **`./run_api.sh` → http://localhost:8000/docs**. Deps: **+asyncpg +greenlet** (in `requirements.txt`; separate process from Streamlit — psycopg2 still drives the sync migration/dual-CI). **Streamlit/SQLite untouched — 599/0 · 21/21.** Deferred to **v2** (kept out for accuracy): writes (POST/PUT/DELETE) + derived "live stock" (port SQLite views to PG *with parity tests*, don't hand-roll). Note: **React frontend still not started** (that's the remaining `FRONTEND_GO` item); the user explicitly green-lit the *backend*. `backend/api/README.md`, `docs/POSTGRES_MIGRATION.md` §8.
> - **🧭 STRATEGIC PIVOT (user-approved): Streamlit stays on SQLite; PG = FastAPI foundation.** Confirmed on a local Postgres that the app's raw SQL (unquoted mixed-case identifiers: `SAP_Code`, `Site_ID`, …; ~1,320 lines / 170 df-keys / 74 aliases) can't run on PG without a large/risky retrofit. So: keep Streamlit on SQLite; the PG schema (`backend/models.py`) + copy script are the base for the future FastAPI/ORM backend (which quotes identifiers → no case problem). **Data-layer migration is now PROVEN on real local Postgres 16:** `backend/dual_ci.py` → 64/64 table parity, semantic aggregates, `get_connection()` facade + `read_sql` + `init_db` all ✅. Views (SQLite/Streamlit legacy) are NOT migrated to PG (FastAPI computes via ORM); `pg_smoke.py` retained but out of CI. Local PG installed for verification (`brew postgresql@16`). SQLite **599/0 · 21/21**. `docs/POSTGRES_MIGRATION.md` §8.
> - **Behavioural dual-CI + runtime dialect fixes wave 1 (step 2, incr 3).** `backend/pg_smoke.py` runs 16 real `database.py` code paths on PG (CI step in `postgres-dual-ci.yml`); surfaces runtime dialect-isms in actual app code. Fixed (SQLite no-ops via helpers): 5 receipts `rowid` reads (`rowid_ref()`), `datetime('now')` in overdue-returnables (`now_sql()`), 3 `INSERT OR IGNORE` (`sql_insert_or_ignore()` → `ON CONFLICT DO NOTHING`). **Wave 2 TODO (in `docs/POSTGRES_MIGRATION.md` §8):** 5 `date('now', ?)` param-modifier sites + 2 `INSERT OR REPLACE` upserts. 599/0 · 21/21; pg_smoke 16/16 on real data (SQLite).
> - **Postgres runtime seam — app can now target PG (step 2, incr 1+2).** `get_connection()` returns a `sqlite3`-compatible facade over the SQLAlchemy engine when `DATABASE_URL` is Postgres (`?`→`%s` translation, `lastrowid` via `lastval()`); **SQLite default 100% unchanged**. `pd.read_sql` works through the facade → the 265 read_sql sites need **no changes**. `init_db` PG-guard builds schema from `models.py` (skips SQLite self-heal DDL). `backend/` is now a package. CI facade smoke runs `init_db`+`?`-params+`read_sql` on real PG. Verified on a **copy of the real DB** (SQLite path intact). 598/0 · 21/21. `database.py`, `docs/POSTGRES_MIGRATION.md` §8. **Not yet:** full `bug_check` against PG in CI (behavioural dual-CI) — the remaining confidence gap before cutover.
> - **Phase-4 dual-backend CI (data layer) + totp fix.** `backend/dual_ci.py` migrates SQLite→target then asserts per-table + per-view + semantic (identity-math) parity; `--dry-run` needs no PG. `.github/workflows/postgres-dual-ci.yml` spins up `postgres:16` on GitHub runners and runs `bug_check` (SQLite) + `dual_ci` (PG) on push — **dual-CI with no local Docker** (neither machine has it). PG-native `v_expiring_stock` override; `totp_*` init_db bug fixed (relocated after users rebuilds). **Harness caught + fixed a model-generator bug** (flattened view SQL swallowed `v_lot_balance`'s `--` comment; now stores raw view SQL). 596/0 · 21/21. **Run the real PG check in the Actions tab** ("Postgres dual-CI"). `docs/POSTGRES_MIGRATION.md` §8.
> - **Phase-5 migration tooling (SQLite→PostgreSQL).** `backend/migrate_sqlite_to_postgres.py` — creates the target schema from `models.py`, copies all 64 tables (ledger `id:=rowid`), coerces SQLite loose-typed values, fixes PG sequences, recreates the 14 views, per-table row-count parity; `--dry-run` validates with no live PG. **Real `gi_database.db` → PARITY OK.** `postgres` service + `pg-data` volume added to `docker-compose.yml` (target only; app stays on SQLite). +2 `bug_check` checks (594/0 · 21/21). **✅ Latent bug FIXED:** `init_db`'s `users` rebuilds dropped `totp_*` on a fresh DB's 1st run — relocated the totp self-heal to after both rebuilds (`column_exists`), +regression test. Detail in `docs/POSTGRES_MIGRATION.md` §8.
> - **Backend prep for FastAPI+PostgreSQL (no endpoints/React — `FRONTEND_GO: NO` holds).** New `backend/models.py` = SQLAlchemy 2.0 Declarative for all 64 tables (+14 views kept as views per SME Canon), auto-generated from live `init_db()`; the 4 rowid-dependent ledger tables carry a SERIAL `id`. **Rowid audit** done (8 real SQL sites). **`system_settings` migrated to an explicit `id` PK** (guarded rowid→id rebuild) + its 4 SQL sites fixed (SME `locations`/`types` views → `MIN(id)` with `DROP VIEW IF EXISTS`; HOD dropdown editor). **`receipts`/`consumption`/`returns` `id` PK DEFERRED** to the Phase-5 cutover copy-script (frozen ledger — reviewed step, not a sweep). +2 guardrail checks (id-PK integrity, models↔live parity). **Autonomous routine PAUSED — Postgres now built interactively.** Full `.venv`: **593/0 bug_check · 21/21 crawler**. `docs/POSTGRES_MIGRATION.md` §7/§8.
> - **⚙️ Verify with `.venv/bin/python`** (not system `python3`) — the full env gives **593/0 bug_check + 21/21 crawler**; system python misses `bcrypt`/`fpdf`/`dotenv`/`sqlalchemy` and falsely shows ~20 failures.
> - **DN line auto-FEFO (backlog #30)** — `suggest_fefo_lot_for_material()` (Material_Code→SAP_Code 1:1 → earliest-expiry open lot at destination site) + opt-in "🔎 Auto-suggest FEFO lots" checkbox on Warehouse Prepare-DN. +1 regression. `database.py`, `pages_internal/warehouse_portal.py`.
> - **Email-path deprecation tracking (backlog #29)** — `get_procurement_adoption()` + `procurement_email_deprecated(80%)`; HOD PR tab shows adoption %, escalating to a deprecation warning on the legacy email/PDF buttons at ≥80%. +1 regression. `database.py`, `pages_internal/hod_portal.py`.
> - **Report scheduler daemon (backlog #13)** — `report_schedule_due()`/`due_report_schedules()` + worker `_maybe_run_report_schedules()` (once/day, generate→archive→mark_schedule_run, fully guarded). +1 regression. `database.py`, `whatsapp_worker.py`.
> - **Vendor master admin tab (backlog #24)** — Admin "🏭 Vendors" tab (list/add/edit/activate/bulk-import-Excel with dup detection) + `update_vendor`/`set_vendor_status`/`bulk_import_vendors`. +1 regression. `database.py`, `pages_internal/admin_portal.py`.
> - **Crash-safe Master DB Editor save (backlog #10)** — `crash_safe_replace_table()` (stage→swap→rollback-on-failure) replaces the bare DELETE+to_sql; original rows always preserved on error. +1 regression proves no data loss. `database.py`, `pages_internal/admin_portal.py`.
> - **Force-close undo window (backlog #28)** — `force_close_target()` snapshots prior state → JSON; `undo_force_close()` restores it verbatim within 24h (no double-undo, no past-window); `get_undoable_force_closures()` + Logistics "↩️ Undo" panel. +1 regression. `database.py`, `pages_internal/logistics_portal.py`.
> - **uploads/ disk-mirror cleanup (backlog #19)** — `cleanup_upload_disk_mirror()` (dry-run + injectable root) + Admin Danger-Zone "Cleanup old upload files" button (live qualifying-count, CLEAN-confirm, audit). BLOBs authoritative → non-destructive. +1 regression. `database.py`, `pages_internal/admin_portal.py`.
> - **Configurable delivery-reminder cadence (backlog #25)** — `get/set_reminder_offsets()` + `app_settings.reminder_offsets` (JSON, default [2,1,0]); `sweep_delivery_reminders()` data-driven; Admin → Settings cadence input. +1 regression. `database.py`, `pages_internal/admin_portal.py`.
> - **Estimator KPI cards — no more wrapped numbers.** `[data-testid="stMetricValue"]` in `material_estimator_portal.py` now `white-space:nowrap` + `font-size:clamp(1rem,1.6vw,1.9rem)` so hero values (e.g. `29,280.3`, `13,046.25`) auto-shrink to one line instead of breaking mid-digit. CSS-only; mobile rule inherits nowrap. No logic touched.
> - **CNCEC equipment re-baseline (data-only re-seed).** User reworked `Equipment.xlsx` (root + `scripts/sme_seed_data/` copies, byte-identical) — blanked the `To_Be_Confirmed_*` placeholder cells AND reworked Surface-Area SQM across 51 of 65 (tag,code) combos (**total 31,343.53 → 29,280.29 SQM, −6.6%**; user-confirmed intentional). Re-loaded via `python scripts/sme_bootstrap.py --site-id CNCEC --equipment-only --force` (DB backed up first to scratchpad). Same 26 tags / 65 rows; `sme_recipe`/`sme_inventory_seed` untouched; 30 pre-existing zero-Done orphan progress rows unchanged. **562/20, all SME checks green.** Blank-cell handling needed no code change (parser already drops blank/placeholder LSC + nulls blank text). ⚠️ Commit of `gi_database.db` changes the public demo numbers.
> - **Opening_Stock audit trail (backlog #23)** — `audit_opening_stock_changes()` logs one `OPENING_STOCK_EDIT` per changed existing inventory item on Master DB Editor save (new items excluded). +1 regression. `database.py`, `pages_internal/admin_portal.py`.
> - **Uncategorised-item banner (backlog #22)** — Admin → Master DB Editor → `inventory` flags rows still on `Category='Others'`/NULL with a count banner + expander (SAP/Material) for backfill. Render-only, additive. `pages_internal/admin_portal.py`.
> - **Rejected-returns cleanup (backlog #20)** — new `returns_history` archive table + `archive_rejected_returns()` (copy-then-delete, `days_ago_sql()`-portable) + Admin Danger-Zone **"Cleanup rejected returns"** button (CLEANUP-confirm + audit log). Archives only `status='rejected'` rows >30 days; `returns` ledger + awaiting-HOD rows untouched. **561/20** (+1 new regression check). `database.py`, `pages_internal/admin_portal.py`.
> - **HOD reject-reason input (backlog #21)** — the HOD **Returns** per-card reject and the **QR** bulk-reject both now open a popover with a **required** reason textbox instead of the hardcoded `"Rejected by HOD"`. Reason persists to `rejection_reason` via the existing helpers; 2 `bug_check` assertions strengthened to prove it. `pages_internal/hod_portal.py`. **560/20 baseline parity (the 20 = pre-existing missing-deps env failures).**
> - **2FA (TOTP), opt-in + admin reset** — sidebar self-enrollment (QR via `qrcode` + `pyotp`); login challenge holds 2FA-enabled users for a 6-digit code; **Admin → User Management → Reset 2FA** is the lost-device safety net. `totp_enabled` defaults 0 so existing users keep password-only login — **no lock-outs.** Helpers in `auth.py`; `users.totp_secret/totp_enabled` self-heal. (`b329d5c`)
> - **PostgreSQL migration — Phases 0–2 (no cutover).** Plan + risk register in `docs/POSTGRES_MIGRATION.md`. Phase 1 = SQLAlchemy engine seam (`get_database_url()`/`get_engine()`, lazy import) with **`get_connection()` untouched → zero behavior change**. Phase 2 = portability helpers (`db_dialect`, `column_exists`, `now_sql`, `days_ago_sql`, `date_diff_days_sql`). **SQLite stays the default; nothing past Phase 2 is implemented.** Phase 3+ (param-style → dual-CI → cutover) awaits green-light. (`8908ec5`, `4ef91d6`, `cf0a3c3`)
> - **🤖 Migration status (2026-07-01, interactive · ROUTINE PAUSED):** Now built interactively (autonomous routine paused per user). Backend prep done: `backend/models.py` (SQLAlchemy schema, 64 tables), rowid audit (8 SQL sites), `system_settings` id-PK migrated + 4 sites fixed; `receipts`/`consumption`/`returns` id-PK deferred to Phase-5 cutover. Phase 3 sub-phase A still at 10/~55 `column_exists()` sites. SQLite still default & in prod. Full `.venv`: **593/0 · 21/21**. Detail: `docs/POSTGRES_MIGRATION.md` §7/§8.
> - **Lot Management UI** (Admin cross-site + HOD site-scoped) — quarantine/release, mark-expired, **dispose** (write-off via the existing HOD stock-adjustment approval; lot flips to `disposed` on approval, back to `open` on reject), plus **split/merge** via a new `lot_transfers` table that `v_lot_balance` nets in/out (within-SAP reclassification — movement ledger untouched, Current_Stock unchanged). `pages_internal/lot_management.py`. (`e16b615`, `ef1fbdb`)
> - **Global error boundary** — users see a friendly one-liner + 8-char reference ID; full traceback → `logs/app_errors.log` (gitignored); `GI_DEBUG=1` for inline. `config.toml [client] showErrorDetails="none"`. Decided: **stay on Streamlit**, no FastAPI rewrite. (`a0b8281`, `error_handling.py`)
> - **SME data + tab work** (intentional, regression-tested edits to the frozen drop-in):
>   - **Substrate load bug FIXED** — the bootstrap had loaded *Lining_Type* values into `Substrate` (and left `Lining_Type` empty). Now `Substrate` = the xlsx Substrate (TANK/VESSEL/CONCRETE); **area-split rows are SUMMED per (tag,code)** (fixes a pre-existing SQM undercount); CNCEC re-baselined from the **new `Equipment.xlsx`** (75→65 rows). (`4e22ebf`)
>   - **System Code Report tab** (9th estimator tab) — per system code: # equipments + total SQM + per-code drill-down + Excel. (`8244abe`)
>   - **`Sub_Location`** captured + surfaced in `get_sme_equipment`, the equipment detail card, and the `equipment` compat VIEW (Master Data grid). New `--equipment-only` bootstrap flag. (`0aeeb50`, `faf8254`)
> - **Stock reservations** — approved cross-site transfers earmark stock at the target site; `Available = Current − Reserved` shown on the stock badge + a non-blocking warning when an issue dips into reserved. Current_Stock identity untouched. (`4de7d61`)
> - **UoM pack→base conversion** — `uom_conversions` table + a per-item pack manager on the SK receipt form; receiving in a pack stores BASE units. Entry aid only; ledger stays single-UoM. (`aab0dfb`)
> - **Bins** — lightweight `Bin_Location` put-away tag on receipts (+ pending_receipts), threaded staging→commit; `get_item_bin_locations()` lookup. (`5c8a068`)
> - **Auto-PR drafting** — HOD button drafts one batch PR (qty = shortage, configurable factor) for all below-min items; idempotent; drafts are editable/renamable before submit. Also **fixed an auth reject crash** (`log_audit_action` UnboundLocalError). (`6db970b`)
> - **WhatsApp auto-retry** — failed sends requeue up to 3 attempts before terminal-failed. (`349c459`)
> - **Maintenance Mode** — the admin toggle now actually blocks non-admins at login (was a no-op). (`85e0b01`)
>
> **Locked decisions (saved to AI memory — do not re-litigate):**
> - FEFO stays **allow-and-log** (not hard-block). UoM = base-UoM + entry conversion. Bins = lightweight tag. Reservations = available + warn. Stay on **Streamlit** (error UX via the boundary). Postgres **planned, not cut over** until Phase 1/3 green-lit by the user.
> - **To update SME equipment data:** edit `scripts/sme_seed_data/Equipment.xlsx` (sheet `Data Input`) → `python scripts/sme_bootstrap.py --site-id CNCEC --equipment-only --force` (back up `gi_database.db` first; `--dry-run` to preview).
>
> **PENDING / NEXT:**
> 1. **PostgreSQL — NOW BUILT INTERACTIVELY (autonomous routine PAUSED, 2026-07-01).** Per user direction we stopped relying on the scheduled `GI-Hub autonomous` routine and are building Postgres directly on `main`. Backend groundwork landed: `backend/models.py`, rowid audit, `system_settings` id-PK. If the routine is ever resumed, honour the §7 coordination box; for now there is a single worker. (Routine history: increment 1 merged `c8dd848`.)
>    - **🤝 Coordination (read this before doing ANY Postgres work here):** the routine and this interactive session share ONE source of truth — **`docs/POSTGRES_MIGRATION.md` §7 "Progress Ledger" + §8 "Run Log"**, plus the one-line pointer below. Protocol: *read ledger + `git log` → re-grep to verify remaining-counts (trust code over the table) → do ≤10 sites → update §7/§8 + this pointer in the SAME change → explain the diff → push.* So neither worker redoes the other's work. Full protocol in the ledger's coordination box.
>    - **🚧 `FRONTEND_GO: NO`** — the **FastAPI + React** rewrite (API-first, incremental, chosen for *after* Postgres) is **gated**; no worker starts it until cutover (Phase 5) is done and a human flips the flag in the ledger. It gets its own plan + routine then.
>    - **🤖 Migration status:** ROUTINE PAUSED — interactive. Backend prep done (`backend/models.py`, rowid audit, `system_settings` id-PK; ledger id-PK deferred to Phase-5). Phase 3 sub-phase A at 10/~55. Full `.venv`: 593/0 · 21/21. Next action: ledger §7.
> 2. **Workstream C ops** (unchanged, external): provision Hetzner **CPX42**, wire Meta WhatsApp secrets (never in chat), redeploy the Streamlit-Cloud demo. ⚠️ `gi_database.db` is tracked + carries real data; a `git add -A` pushes DB changes (we stage it deliberately).
> 3. **After an app RESTART** the new surfaces appear: estimator **🔢 System Code Report**, Admin + HOD **🧪 Lot Management**, sidebar **🔐 Two-Factor Auth**, Master Data **Sub_Location** column. (Streamlit hot-reload does NOT add new tabs from imported modules — a full restart is required.)
>
> **Frozen contracts still intact:** Man-Hour writes only `mh_*`; EOD path, identity math (`receipts − consumption − returns`), RBAC, RL/BL separation, price masking — all untouched. The SME edits above are **additive + regression-tested** (System Code Report tab, Sub_Location, the Substrate/area-SQM data fix) — not refactors; the freeze still holds for *new* work.

**Prior update:** 2026-06-30 (earlier) — **WORKSTREAM C INFRA + STREAMLIT-CLOUD DEMO + ESTIMATOR/LOGIN POLISH.** Estimator filter cross-filtering (System Code ↔ Substrate); cold-start `ImportError` fixed (lazy `pages_internal/__init__.py`); login-focus fix (`st.form`); KPI drill-down `st.dialog` modal; admin estimator site picker + hidden SME sidebar; **Meta WhatsApp Cloud API sender** (`WHATSAPP_PROVIDER` router, default = existing chain) + `worker` compose service; Streamlit-Cloud readiness (`pdfplumber`, `streamlit>=1.58` pin, `watchdog`, sanitized `demo_seed.db` + DB fallback); **Certbot + Nginx TLS** (`docker-compose.yml` + `scripts/init-letsencrypt.sh`). Tests at that point: 560/0.

**Prior update:** 2026-06-28 — **MAN-HOUR FEATURE COMPLETE; WORKSTREAM C UNPAUSED.** Man-Hour & Labor Tracking shipped + documented (USER_MANUAL §19, SOP §3.3a) — see §2Z. Certbot + Nginx TLS wired (§2Y).

**Prior update:** 2026-06 round 20.5.2 — **TWO LIVE CRASHES FIXED.** (1) SK Consumption page threw `ModuleNotFoundError: No module named 'pages_internal.material_estimator'` — `daily_issue_log.py` still imported `days_of_continuation_block` from the R19 package R20 deleted; vendored the function in-file and removed the dead import (it was the only live reference). (2) Admin Material Estimator → Location Report threw `IndexError: single positional indexer is out-of-bounds` — `st.session_state.loc_order` held equipment tags removed by a re-bootstrap; now reconciled against the current `eq_master` each run. +3 regression checks. See §2W.2. **Tests: 525 / 542 (57 SME-related green).**

**Prior round:** 20.5 — **TAB 8 MASTER DATA WIRED + SME INVENTORY ISOLATED.** Four user-reported issues on Tab 8 fixed: (1) "Missing Submit Button" warning on Equipment radio (silent IndexError severed the form mid-build); (2) `KeyError: 'Lining_System'` from `_get_autofill` (sme_equipment was missing 15 legacy Excel columns); (3) "cannot modify view" errors on every Add/Edit/Delete (Tab 8 wrote raw SQL against the compat VIEWs); (4) Materials Details flooded with 1,200+ rows of ERP inventory clutter and showed Available_Qty=0 (TABLE_MAP pointed at the wrong table; no SME-specific seed existed). Phase A extends `sme_equipment` +15 cols + `sme_recipe` +8 cols, creates `sme_inventory_seed` (SME-owned baseline), creates `sme_materials_view` (joins seed against ERP `receipts`/`consumption` so `Available_Qty = Initial + Received − Consumed`), rewrites the `equipment`/`recipe` compat VIEWs to expose every aliased column, and adds 9 CRUD helpers that translate UI form keys (lowercase, dotted, slashed) onto PascalCase table columns. Phase B extends the bootstrap to load every Excel column, adds the inventory-seed loader, and switches default semantics to `INSERT OR IGNORE` so manual edits survive (with a `--force` flag for explicit re-baseline). Phase C surgically rewires 4 raw-SQL write sites in Tab 8 to dispatch on `db_table` and call the helpers; `TABLE_MAP` now points `"Materials_DetailsAvailable_Qty" → "sme_materials_view"`. Phase D adds 11 regression tests. The SME inventory store is now fully isolated from ERP `inventory` writes; live Available_Qty still rolls up automatically from R18-tagged consumption + Logistics receipts via SQL view math. See §2W. **Tests: +11 Round-20.5 checks (11/11 green).** **R20.5.1 follow-up:** fixed two bugs the live render exposed — Master Data's `ORDER BY rowid` returned empty grids for all 3 radios (VIEWs have no rowid), and `get_sme_inventory_view()` was still reading ERP live stock so every analytical tab showed qty=0; rewired it to the seed-based `sme_materials_view` model so the whole portal reflects the SME inventory file. **Total 522 / 539 (54 SME-related green: R17 13/13 + R18 13/13 + R20 11/11 + R20.1 4/4 + R20.5 11/11 + R20.5.1 2/2).**

**Prior round:** 20.1 — **R20 LIVE-RENDER BUGFIXES.** First-render audit of the literal SME drop-in surfaced four issues that bare-mode tests couldn't catch: (a) the R20 Phase-2 wrap leaked 8-space indent INTO multi-line string contents, so `st.markdown(... unsafe_allow_html=True)` treated the sticky header / gauge / h-bars / section dividers as escaped code text instead of styled HTML; (b) `_cached_cascade_allocate` raised `KeyError: 'Equipment_Tag_No.'` on the Total Overview tab when the row list was empty (e.g., stale cache from a prior session); (c) the Stock-Only Materials section showed generic warehouse items (bolts/gloves) because our `get_sme_inventory_view()` returns all inventory rows, not just SME-tracked ones; (d) `load_all()` could return shape-less empty frames when no data was loaded yet. All four fixed via a tokenize-walking dedent script (158 + 6 lines patched), an explicit `_EXPECTED_COLS` argument to `pd.DataFrame(rows, columns=...)`, an `isin(_all_sme_codes)` filter on Stock-Only Materials, and three `if X.empty: pd.DataFrame(columns=[...])` guards. Plus 4 R20.1 regression tests so this class of issue gets caught in CI next time. See §2V.1. **Tests: +4 Round-20.1 checks (4/4 green) — total 510 / 526 (41 SME-related checks all green: R17 13/13 + R18 13/13 + R20 11/11 + R20.1 4/4).**

**Prior round:** 20 — **LITERAL SME DROP-IN SHIPPED (revert-and-replace).** The R19 piecemeal port broke the SME's intermediate-DataFrame architecture (KeyError: `'Lining_System_Code'`) and lost the dark/light theme via CSS scope leakage. Round 20 is a clean pivot: delete the entire R19 package, drop the original 8,505-LOC SME `app.py` in as a single file at `pages_internal/material_estimator_portal.py`, and perform a tight set of surgical edits to bridge it to the ERP data layer. Every chart, KPI card, Plotly table, drag sortable, the entire `<style>` CSS block, and `_apply_theme_attr()` are preserved verbatim — apple-to-apple parity guaranteed because we're running the SME's own code. Surgical edits (search `# R20 EDIT`): `st.set_page_config` commented out; `_show_login` + auth gate deleted; `load_all()` rewritten to call ERP helpers (`D.get_sme_inventory_view`, `get_sme_equipment`, `get_sme_recipe`, `get_sme_sqm_progress`) producing the exact column-cased intermediate frames (`inv`, `recipe`, `equip_sc`, `dm`, `eq_master`, `sqm_ref`) the SME engine expects; `get_db()` redirected to the ERP DB; the entire `with tab_consume:` block (1,402 LOC, 6 sub-views) deleted — R18 already wired the SME consumption flow into the ERP's SK Consumption tab; tab declaration trimmed 9 → 8; Master Data Locations/Types CRUD routed through `D.add_sme_setting`/`D.delete_sme_setting` (R17 Correction #1 preserved); `st.download_button` monkey-patch scoped inside the wrapper via try/finally (R17 Correction #2 preserved for other portals); all imperative rendering (3,913 lines from `with st.sidebar:` to EOF) wrapped inside `page_material_estimator(user)`. Six new compatibility VIEWS in `init_db` (`locations`, `types`, `consumption_log`, `equipment`, `recipe`, `sqm_progress`) let the SME's legacy SQL resolve transparently against the ERP tables. See §2V. **Tests: +11 Round-20 checks (11/11 green) — total 505/522 in `bug_check.py` (same 17 pre-existing failures unchanged). RBAC, EOD commit path, routing rule all preserved. The R19 KeyError is gone: `equip_sc` and `dm` both carry `Lining_System_Code` because `load_all()` now builds them per the original SME architecture.**

**Prior round:** 19 — Apple-to-apple SME UI parity port (piecemeal rewrite, reverted).

**Round 19 prior:** **APPLE-TO-APPLE SME UI PARITY SHIPPED.** Every tab of the Material Estimator portal now mirrors the original standalone SME app's surface 1:1 — same sub-views, KPI cards, filter strips, Plotly tables, SVG gauges, drag-priority sortables, per-location color schemes, suggestion-engine panels, multi-sheet Excel layouts with logo + title bars + scheme-colored headers + GRAND TOTAL rows, Master Data CRUD across all 5 sub-views. Original SME COLOR_SCHEMES (7 schemes: `dashboard / brown_field / train_j / train_k / session / execution / overview`) ported verbatim; per-location mapping preserved. New modules: `colors.py` (palette), `charts.py` (SVG gauge + h-bars + Plotly stacked bars), `suggestion_panel.py` (port of `_run_suggestion_engine` + 2-column UI). Tab modules rewritten end-to-end: `ui_dashboard.py` (7-card KPI strip + gauge + Plotly stacked bars + per-location strip + Full Material Balance + Stock-Only expander; 4-card procurement view with per-location/per-code expanders), `ui_priority.py` (left/right column layout with drag sortable + tag detail card), `ui_session_order.py` (4-card KPI + reorder sortable + per-equipment expanders with show_sqm tables + combined procurement + 5-cell grand total + smart suggestions), `ui_location_report.py` (Location Based + All Equipment sub-views with per-location color schemes), `ui_equipment_report.py` (SME column order + 3-section per-equipment + multi-sheet workbook), `ui_execution_plan.py` (3 sub-views with critical card + procurement priority + production-detail blocks), `ui_total_overview.py` (6-card KPI + master table + per-system-code drilldowns), `ui_master_data.py` (5 sub-views with full CRUD against SME tables + system_settings — never the ERP ledger). The ERP ledger (`pending_issues` / `consumption` / `receipts` / `returns`) is unchanged; the Round-18 routing rule is preserved and explicitly regression-tested. See §2U. **Tests: +16 Round-19 checks (16/16 green) — total 514/531 in `bug_check.py` (same 17 pre-existing failures in unrelated areas). RBAC, EOD commit path, and routing rule all preserved.**

**Prior round:** 18 — SME consumption form + raw `.xlsx` + state-machine wrappers around `commit_eod` / `hod_reject_pending_issue`.

**Round 18 prior:** **SME CONSUMPTION FORM + UI PARITY + RAW XLSX SHIPPED.** The estimator now writes through the ERP's EOD commit pipeline via a state-machine wrapper layer. New `🧪 SME Multi-Material Entry` expander on the SK Consumption tab renders the legacy SME multi-row grid (equipment tag → system codes → SQM per system → auto-computed materials → actual override → batch staging). On Submit Batch, `stage_sme_consumption_batch()` aggregates per `Material_Code`, resolves the unique `SAP_Code` (1:1 per user contract), writes one `pending_issues` row per material AND writes per-detail rows to the new `sme_consumption_log` table. `commit_eod_with_sme_sync()` wraps `commit_eod` (which itself is **unchanged**) and shifts SQM from `Done_SQM_staged` → `Done_SQM` on commit; `hod_reject_pending_issue_with_sme_sync()` mirrors the path for the reject route. UI parity port: SME's sticky-header chain (title bar + tabs + sub-view radio all pinned while scrolling) recolored to ERP yellow/amber, SME logo bundled in the package, 8-card KPI strip via `dbl_click_metric` (click → drilldown popovers), color-coded `plotly_mat_table` for per-tag breakdowns, post-Submit-Batch Days-of-Continuation runway report inline. Downloads: `pyzipper` ripped out entirely — Excel files are raw `.xlsx` with the pattern `SME_<Report>_<Site>_<YYYY-MM-DD>.xlsx`; PDF popover gate retained for email-attached reports. New view `v_inventory_with_sme` exposes a computed `is_sme` flag via LEFT JOIN on `sme_recipe` (zero new columns on `inventory`; zero maintenance burden). See §2T. **Tests: +16 Round-18 checks (16/16 green) — total 498/515 in `bug_check.py` (the 17 pre-existing failures in manual_qa / PR PDF / PO PDF / locate_anything sidecar are unchanged and unrelated). The EOD commit logic and all RBAC contracts are untouched.**

**Prior round:** 17 — Smart Material Estimator (SME) merged as a read-only portal.

**Round 17 prior:** **SMART MATERIAL ESTIMATOR (SME) MERGED.** The standalone Streamlit project for Rubber Lining / Brick Lining material planning is now a first-class portal inside the ERP at `pages_internal/material_estimator/` (package, 14 files), exact-locked to `{hod, admin}` via `_EXACT_ROLE_PAGES`. The estimator was originally a read-only projection over the ERP ledger — `Available_Qty` from `load_live_inventory()` (computed, not stored); `Ordered_Qty` from the new `get_on_order_by_material()` helper (open-PO outstanding). Three new SME tables (`sme_equipment`, `sme_recipe`, `sme_sqm_progress`) added to `init_db()` self-heal. Locations + Equipment Types live in `system_settings` under new categories `sme_location` and `sme_equipment_type` (per Correction #1 — no separate dropdown tables). Downloads originally used standalone `sme_secure_xlsx_download` / `sme_secure_pdf_download` helpers (per Correction #2 — no monkey-patching of `st.download_button`, so other portals' downloads can't be affected); Round 18 then stripped the pyzipper layer entirely. SME's Inventory data-entry tabs (Consumption Log, Receipt Log, New Order) deleted entirely so the EOD commit ledger remains the only write path. Bootstrap script `scripts/sme_bootstrap.py --site-id <SITE>` loads `equipment` + `recipes` from `scripts/sme_seed_data/*.xlsx`. See §2S.

**Prior round:** 16 — DN Routing Simplification + PR PDF Polish.

**Round 16 prior:** **DN ROUTING SIMPLIFICATION + PR PDF POLISH SHIPPED.** Logistics removed from the DN approval chain — Warehouse-prepared DNs now flow `draft → pending_hod → pending_sk → received` (was `… → pending_logistics → pending_hod → …`). `submit_dn_for_logistics` writes `pending_hod` directly and dual-notifies (HOD actionable + Logistics info-only awareness). Idempotent `init_db` migration sweeps any in-flight `pending_logistics`/`logistics_approved` DN forward to `pending_hod`. PR PDF gains two columns — `PO #` (comma-joined for multi-PO PRs, via new `get_pr_with_po_numbers` helper) and `UoM`. Filename renamed from `…_Record.pdf` to `…_Status.pdf`. See §2R. **Tests: 485/485 in `bug_check.py` · 17/17 in `test_ui_crawler.py`.**

**Prior round:** 15 — Multi-Portal Polish + Material Master + PO Parser Fix.

**Round 15 prior:** **MULTI-PORTAL POLISH + MATERIAL MASTER + PO PARSER FIX SHIPPED.** New Logistics `📦 Material Details` tab (manual entry + Excel upload + Temp-GI auto-codes + SAP auto-increment + duplicate rejection). PO PDF parser rewritten — three line-item layouts supported (code-on-own-line + 7-column row + legacy single-line). Per-site `Minimum_Qty` override via new `inventory_site_overrides` table (additive, no impact on identity math). HOD DN Approvals now uses a 3-way OR-join so legacy mismatched-Site_ID DNs surface to the right HOD. HOD reschedule routes directly to `warehouse_user` when the DN is post-receive (`pending_logistics → pending_sk`); PO-level reschedules still go to Logistics. Warehouse Prepare-DN destination site locked to the PO's originating site. Admin Live Dashboard KPI cards now click through to inline drill-downs; a single wide "Search across all columns" input replaces the cramped per-column filter strip on mobile/landscape. New `ui_components.render_confirm` helper wired to SK Submit Grid + HOD Approve All Pending + HOD In-Transit Reschedule. PR report `_ALWAYS_KEEP` now includes UOM so the column survives partial-row legacy data. See §2Q. **Tests: 480/480 in `bug_check.py` · 17/17 in `test_ui_crawler.py`.**

**Prior round:** 14 — Vision OCR Image-Pipeline Hardening.

**Round 14 prior:** **VISION OCR IMAGE-PIPELINE HARDENING SHIPPED.** New `ai/image_utils.py` interposes EXIF auto-orient + RGB convert + 1600 px long-edge cap + JPEG quality-85 re-encode between every uploaded photo and the Ollama vision call. `pillow-heif>=0.16` added so iPhone HEIC files (often delivered with a `.JPG` extension) decode cleanly. `ollama_vision_generate` default timeout bumped 120 → 240 s + new `keep_alive='30m'` so cold-start no longer trips the request. Both SK OCR uploaders now accept `.heic` / `.heif`. See §2P. **Tests: 465/465 in `bug_check.py` · 17/17 in `test_ui_crawler.py`.**

**Prior round:** 13 — EOD State Unification + Schema Cleanup. **Prior round 12:** SMR-via-SK-Grid + Auto-Attribution. **Round 11 prior:** Phase 8 · Workstream B (Smart Scan AI) COMPLETE.

**Original round 13 last-update marker preserved below for context:**

**Round 13 update:** **EOD STATE UNIFICATION + SCHEMA CLEANUP SHIPPED.** `commit_eod` filter widened from `pending_hod` only → `(pending_hod, approved, flagged)` via the new `_EOD_COMMIT_STATUSES` constant, so per-row ✓ approvals no longer strand rows. `↩️ Unapprove` button added to the HOD EOD per-row panel. Rejected rows now route to a new `rejected_issues_archive` table (copy-then-delete) so `pending_issues` stays lean while audit trail is preserved. `line_status` gains a 4th value `'rejected_at_hod'` for SMR-sourced rejections. Bogus `Approved` column (legacy parsing artifact, always NULL) dropped from `consumption`; the proper `"Approved By"` column stays as the single attribution slot. Admin DB Editor PDF export for consumption now uses the canonical `config.CONSUMPTION_EXPORT_COLS` list — no more legacy junk in operations PDFs. See §2O. **Tests: 460/460 in `bug_check.py` · 17/17 in `test_ui_crawler.py`.**

**Prior round:** 12 — SMR-via-SK-Grid + Auto-Attribution. **Round 11 prior:** Phase 8 · Workstream B (Smart Scan AI) COMPLETE. Smart Scan Tier-3 fallback via a separately-deployed FastAPI sidecar wrapping NVIDIA LocateAnything-3B (MPS, fp16 on Apple Silicon). Phases 8A–8E:
- **8A**: `ai/locate_anything/` package — stdlib-HTTP client (gate check, circuit breaker, 30s timeout), `model_loader.py` (MPS + lazy single-load + ModelNotReadyError), `server.py` (FastAPI POST /detect, GET /health), sidecar-only `requirements.txt`. Admin gate `app_settings.locate_anything_enabled` default OFF. `scripts/download_model.sh` (manual).
- **8B**: `scripts/bundle_locate_anything_weights.sh` + `scripts/install_locate_anything_weights.sh` (overwrite-always, SHA-256 verified) for air-gapped sites. `host_setup/launchd/com.gi.locate-anything.plist.tmpl` + `host_setup/scripts/run_locate_anything.sh`. `host_setup/scripts/install.sh --with-locate-anything` opt-in flag.
- **8C**: `ai/cv/smart_scan.py:should_invoke_tier3 / tier3_to_candidates`. SK Smart Scan gains amber-bordered "🤖 AI fallback" panel — "Use this tool" is the only accept path. Fires ONLY when YOLO is in "manual" mode (top conf < 0.30 OR empty). Audit events: TIER3_SHOWN / ACCEPTED / REJECTED.
- **8D**: Admin Portal Settings → "🤖 Smart Scan AI (LocateAnything)" expander — toggle, sidecar URL, /health probe (reachable / device / model on disk), 7-day telemetry rollup, recent-calls table.
- **8E**: `locate_anything_calls` telemetry table (auto-self-heal). `client.detect()` now returns `(detections, call_id)` and writes one row per HTTP attempt. SK panel calls `mark_locate_anything_outcome` on accept/reject to close the loop. `get_locate_anything_summary` powers the Admin metric strip.

**Workstream B — Smart Scan AI module: COMPLETE.** All five phases shipped; the gate stays OFF in production until a pilot site explicitly opts in.
**Prior rounds:** 7F Role-Based PDFs · 7E Network Resilience · 7D PO Masking · 7C Cross-Site Notifications · 7B Supervisor Material Request · 7A Employee Site Binding · 6A–F Workstream A.
**Test status:** **485/485 in `bug_check.py` · 17/17 in `test_ui_crawler.py`** (run `python bug_check.py && python test_ui_crawler.py`). +5 across Round 16 (DN routing flip, dual notification fan-out, legacy DN migration idempotent, PO# comma-join helper, PR PDF column expansion). **Zero network access · zero torch import in test path · zero weight files required on disk.**
**Production hosting:** Self-host on `giinventory.com` via Cloudflare Tunnel + Access (email allow-list `@generalindustries.net`). Turnkey installer at `host_setup/scripts/install.sh`. See §4 "Run / Develop" and the "Production hosting" chapter.
**Purpose:** Get the next session productive in <5 minutes — architecture, what changed, what's next.
**Companion docs:** `USER_MANUAL.md` (every page/tab/button), `SOP.md` (Logistics + Warehouse operating procedure with cadences, decision trees, escalation matrix), `docs/cv_training_guide.md` (Phase 6C — capture → label → train → promote walkthrough).

---

## 1. Architecture Map (touch-tested files)

```
CNCEC PROJECT/
├── main.py                       Page routing, RBAC gate, sidebar.
│                                 + 🔔 Notifications bell with unread badge +
│                                 inbox modal (dialog), routes to Logistics +
│                                 Warehouse portals via exact-role lock.
│                                 + Hub Assistant pre-flights ollama health()
│                                 and shows a clean st.warning on offline.
├── config.py                     Constants, ROLES (now 6), ROLE_HIERARCHY (parallel
│                                 procurement ladder), PAGE_ACCESS (7 entries), brand
│                                 colours, MATERIAL_CATEGORIES, MTC_REQUIRED_CATEGORY,
│                                 RL_BL_FAMILY_TOKENS + classify_rl_bl_family(),
│                                 WHATSAPP_ENABLED master switch + WHATSAPP_TRIGGERS
│                                 per-event toggle dict (16 keys).
├── database.py     (~5,200 LOC)  ALL SQL + schema + helpers + reports (no Streamlit).
│                                 Phase C additions: 12 new procurement tables,
│                                 60+ new helpers, 3 new reports, T-2/T-1/T-0 sweep,
│                                 in-app notifications inbox query layer.
├── cache_layer.py                @st.cache_data wrappers, bust_inventory_cache()
├── auth.py                       bcrypt login, render_user_management_tab
├── ui_components.py              Custom CSS, AgGrid wrapper, charts, brand headers,
│                                 STATUS_BADGE_JS, LOAN_STATUS_BADGE_JS, status_pill_html
├── mailer.py                     Outlook (Win COM) / Mail.app (AppleScript) / mailto:
│                                 + draft_rubber_mtc_email, draft_return_logistics_email
├── reports.py                    PDF/Excel/CSV generators with narrow-column smart widths
├── whatsapp_worker.py            Twilio API (cloud) → pywhatkit (local fallback, lazy-imported).
│                                 Started as @st.cache_resource thread from main.py:78.
│                                 Phase C: 60-sec poll loop now also calls
│                                 _maybe_run_delivery_reminders() once per local day
│                                 (idempotent via app_settings.delivery_reminders_last_run
│                                 day marker + delivery_reminders_sent UNIQUE constraint).
│
├── pages_internal/
│   ├── live_dashboard.py         Hero strip (4 cards inc. Stock Value SAR).
│   │                             Grid column order: SAP_Code → Material_Code → Desc → UOM →
│   │                             Opening_Stock → Receipt → Consumption → Return →
│   │                             Closing_Stock → Min → Unit_Cost → Stock_Value → Category.
│   ├── daily_issue_log.py        6 SK tabs (unchanged) + ONE new expander at top of
│   │                             Receipt Staging: 🚚 Incoming Delivery Notes from
│   │                             Warehouse. Click "Mark as Received" → row lands in
│   │                             `receipts` ledger; DN flips to 'received'.
│   ├── hod_portal.py             15 tabs (13 unchanged + 2 NEW appended):
│   │                               original 13: EOD · Cross-Site · Burn Rate ·
│   │                               Pending Receipts · Returns · Adjustments · PRs ·
│   │                               Shelf-Life · Notifications · My Requests ·
│   │                               Site Config · DOC · QR Approval.
│   │                               NEW:
│   │                               🚚 DN Approvals — HOD approves warehouse-prepared
│   │                                 DNs → stages pending_receipts for SK confirm.
│   │                               🚚 In-Transit — 3 read-only sub-tabs: Active DNs
│   │                                 (with 🔁 reschedule popover per row), My
│   │                                 reschedule requests, Force-closures affecting me.
│   │                             Plus 1 additive expander in the existing PR tab:
│   │                             🚚 Submit PR(s) to Logistics Portal.
│   ├── admin_portal.py           11 tabs (10 unchanged + 1 NEW appended):
│   │                               🚚 Logistics Oversight — cross-site read-only
│   │                               view of every PR/PO/DN/return/force-closure with
│   │                               site + warehouse filters.
│   │                             Pending Requests tab: SQL now LEFT-JOINs inventory
│   │                             to surface Material_Code + Material_Name + UOM
│   │                             alongside SAP_Code (Bug 2 fix).
│   │                             WhatsApp Console: redundant +3h timedelta removed
│   │                             (Bug 1 fix — _localize already converts).
│   ├── logistics_portal.py  (NEW) 8 tabs role-locked to {logistics, admin}:
│   │                               📥 Incoming PRs · 🧾 Create PO (manual + PDF) ·
│   │                               📋 Open POs · 🏭 Assign to Warehouse ·
│   │                               🔁 Reschedules · 🛑 Force-Close ·
│   │                               ↩️ Vendor Returns · 📂 History.
│   │                             PO PDF upload uses pdfplumber to extract header,
│   │                             line items, and the PO Annexure delivery schedule.
│   │                             Vendor master with inline-add.
│   ├── warehouse_portal.py (NEW) 6 tabs role-locked to {warehouse_user, admin}:
│   │                               🔔 Incoming Assignments · 📦 Receive Goods ·
│   │                               📝 Prepare DN · ✈️ Outbound DNs ·
│   │                               ↩️ Returns from Site · 📂 History.
│   │                             Three independent layers guarantee Unit_Price +
│   │                             Total_Price + monetary header fields NEVER show
│   │                             in any warehouse view. Admin shadow uses a
│   │                             sidebar warehouse picker.
│   └── reports_page.py           14 reports total (11 unchanged + 3 NEW):
│                                 🧾 PO Status · 🏭 Warehouse Throughput ·
│                                 🛑 Force-Closures. Each PDF/Excel/CSV via the
│                                 existing toolchain. Site + date filters honoured.
│
├── ai/                           Unchanged (NL search, OCR, fuzzy, insights)
├── pwa/                          Unchanged
│
├── bug_check.py                  Standalone smoke harness. Run `python bug_check.py`.
│                                 Writes BUG_REPORT.md. Throwaway DB, never touches live.
├── BUG_REPORT.md    (generated)  Latest bug_check output — pass/fail per check, by area.
├── build_manual_pdf.py           Markdown → branded fpdf2 PDF (cover + TOC + headers).
│                                 Used by Admin → Settings → "Download User Manual".
├── host_setup/      (NEW)        Turnkey Path-A deployment for the host Mac.
│   ├── README.md                 45-min step-by-step install playbook
│   ├── cloudflared_config.yml.example
│   ├── launchd/                  4 plist templates with __PROJECT_DIR__ placeholders
│   │   ├── com.gi.streamlit.plist.tmpl
│   │   ├── com.gi.whatsapp-worker.plist.tmpl
│   │   ├── com.gi.cloudflared.plist.tmpl
│   │   └── com.gi.backup.plist.tmpl
│   └── scripts/
│       ├── install.sh            render + launchctl load all four
│       ├── uninstall.sh          remove without touching data
│       ├── restart_app.sh        zero-downtime restart after git pull
│       ├── run_streamlit.sh      wrapper exec'd by streamlit plist (avoids exit-126)
│       └── backup_db.sh          SQLite online backup + iCloud + 14-day prune
├── ai/manual_qa.py  (NEW)        Role-aware Q&A over USER_MANUAL.md. Sidebar widget.
├── USER_MANUAL.md                §1–13 user catalogue. §14 = host operations chapter.
└── handoff.md                    THIS FILE
```

### Critical contracts (do not break)

- **`config.utc_to_local(value, fmt)`, `config.localize_timestamps_df(df, cols)`, and `config.auto_localize_timestamps(df)`** — display-time UTC → GMT+3 conversion. DB stays UTC; helpers add `TZ_OFFSET_HOURS` (defaults to 3 / Asia/Riyadh) at render.
  - **`auto_localize_timestamps(df)`** is the one you want 99% of the time — it scans for any column matching the canonical set (`_DEFAULT_TS_COLS` in `config.py`) and converts only those. Idempotent on already-localized strings.
  - **Every display-bound `get_*` helper in `database.py` already wraps its result through `_localize()`** — `get_pending_returns`, `list_qr_requests`, `get_pending_requests`, `get_returnable_items`, `get_receipt_history`, `get_whatsapp_log`, `get_pending_stock_adjustments`, `get_stock_adjustment_history`, `get_missing_mtc_for_site`, `get_wbs_for_site`. Callers get GMT+3 strings for free.
  - For ad-hoc `pd.read_sql` calls at the page level (no helper), call `auto_localize_timestamps(df)` right after the read. Already done in Admin Pending Cross-Site, Admin Audit Log, Admin Live Activity Feed, Admin WhatsApp Console, HOD Cross-Site Incoming, HOD Pending Receipts, HOD PR tab, HOD My Requests, SK QR Requests, SK Returnable Items.
  - If you add a new timestamp column to the schema, ALSO add its name to `_DEFAULT_TS_COLS` so auto-detection picks it up everywhere.
- **`GI_SUPPRESS_EMBEDDED_WORKER=1`** — when set, `main.py` skips spawning the embedded WhatsApp worker thread. The Streamlit plist sets this so the standalone `com.gi.whatsapp-worker` process is the only consumer. Without this, both workers race and the embedded one (daemon thread, fails macOS Cocoa main-thread guard) flips every message to `failed`.
- **macOS WhatsApp sender uses ONLY `System Events` for input** (`whatsapp_worker._send_via_chrome_macos`). No `tell application <browser>` anywhere → no Automation permission prompt. Needs Accessibility permission once on the Python binary. If you ever add new AppleScript snippets here, keep them `System Events`-only or the prompt comes back.
- **`commit_eod(conn) → int`** — signature unchanged. Moves `pending_issues` → `consumption`, deletes staged.
- **`process_receipt_delivery(conn, date, sap, qty, supplier, remarks, site, pr_number, expiry_date, extra_fields)`** — auto-creates lot row when Lot_Number provided or Expiry_Date set. Idempotent via UNIQUE on lots. **`extra_fields` MUST be a column on `receipts`** — the function swallows OperationalErrors, so missing columns silently drop SK input. The receipts self-heal block now covers the full 15-column logistics set (see `database.py:553`).
- **Identity math:** `Closing_Stock = Opening_Stock + Σ receipts − Σ consumption − Σ returns`. **Opening_Stock is a column on `inventory`** (default 0) — admins can set it via DB Editor. Never stored as a computed total. Same pattern for `v_lot_balance` (Remaining = Received − Consumed per Lot_Number).
- **Self-healing schema** — `init_db()` adds missing cols + missing tables on every startup. No manual migrations.
- **Returns flow:** SK `submit_return_request()` → row in `pending_returns` (status `pending_hod`). HOD `approve_return_request()` is the ONLY path that writes to the `returns` ledger — never let any other code path insert into `returns` directly. Approval is idempotent (refuses to re-approve an `approved` row).
- **Attachments:** `save_entry_attachment(file_obj, …)` reads bytes once, persists BLOB to `entry_attachments.file_blob`, AND mirrors to `uploads/<Site>/<doc_type>/<doc_number>/<name>`. The disk mirror is best-effort (read-only FS → `disk_path` is empty); the BLOB is authoritative.
- **MTC docs:** `save_mtc_document(uploaded_file=None, …)` writes `status='missing'` so HOD sees the rubber item in the Pending Receipts banner. Don't filter `uploaded_file is None` upstream — let it through with status='missing'.
- **RBAC override:** `_EXACT_ROLE_PAGES` in `main.py` bypasses hierarchy for pages that need exact-role lock. As of v3.0 it carries FOUR locks: `📝 Entry Log = {store_keeper}`, `📋 HOD Portal = {hod, admin}`, `🚚 Logistics Portal = {logistics, admin}`, `🏭 Warehouse Portal = {warehouse_user, admin}`. The HOD lock was added so the procurement roles (which sit higher in the numeric hierarchy) do NOT inherit access via `ROLE_HIERARCHY` comparison.
- **Warehouse view masks prices.** `get_assignment_detail()` and every warehouse-visible PO drill-down call `get_po_detail(hide_prices=True)`, which blanks `Unit_Price` + `Total_Price` AND strips `Total_Amount`, `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Amount_In_Words` from the header dict. The blanking is then re-applied defensively at the assignment layer in case a future caller bypasses `hide_prices`. Three layers. Don't remove any of them.
- **RL/BL strict separation.** Rubber Lining and Brick Lining items must NEVER aggregate with each other on any PO line, DN, or Warehouse split. Three enforcement points: (a) `po_items.rl_bl_family` is tagged at insert time via `config.classify_rl_bl_family(material_code, description)`; (b) `create_delivery_note()` rejects any payload that spans more than one family with the message `"Strict separation violated: this DN spans multiple RL/BL families. Prepare one DN per family."`; (c) `delivery_notes.rl_bl_family` carries the family forward so reports can group by it. Allowed values are `'RL'`, `'BL'`, or `NULL` — never a combo string.
- **DN state machine.** `draft → pending_logistics → logistics_approved → pending_hod → hod_approved → pending_sk → received`, with `rejected` as terminal from any pending state. The transient `logistics_approved` / `hod_approved` rows are observable in queries but the workflow flips straight through to the next pending state in the same UPDATE; they exist for log clarity.
- **PO over-ship guard (DN side).** `create_delivery_note()` computes available stock as `available = Delivered_Qty − Returned_Qty − Σ(qty on live DNs for this po_item)`, where "live" = DN status NOT IN (`'rejected'`, `'cancelled'`). Conflating `Delivered_Qty` (received from vendor) with "already shipped" was the bug found mid-Phase-3. Don't replace this calc with a naive `Qty − Delivered_Qty` check.
- **DN → SK → receipts handoff.** On HOD approval, `hod_decide_dn(approve=True)` mirrors the DN lines into `pending_receipts` with `status='pending_sk'` (a new status value). HOD's existing Pending Receipts tab filters by `status='pending_hod'` so the mirror rows don't bleed into that tab. The SK confirms via the new expander in Receipt Staging → `sk_mark_dn_received` writes one `receipts` row per line (with `DN_Number`, `Warehouse_ID`, `PO_Number_Source` populated), flips the DN to `'received'`, deletes the mirror rows, and busts the inventory cache.
- **Reminder dedup.** `sweep_delivery_reminders()` is idempotent across runs via TWO independent guards: (1) `delivery_reminders_sent` table has UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) so INSERT-OR-IGNORE blocks per-target double-fires; (2) `app_settings.delivery_reminders_last_run` stores today's ISO date so the worker's 60-sec poll loop skips the whole sweep on subsequent ticks. Restarting the worker mid-day is safe.
- **WhatsApp triggers gate every event.** Every Phase C notification fires `queue_app_notification(...)` (always — in-app inbox) AND `fire_whatsapp_event(event_key, phone, msg)` (gated by `config.WHATSAPP_ENABLED` master + `config.WHATSAPP_TRIGGERS[event_key]` per-event). Flip a key to `False` to silence WhatsApp for that event without touching in-app behaviour. Default toggles: `warehouse_acknowledged=False` (low value), `dn_received_by_sk=False` (closure ping), everything else `True`.

---

## 2. Tuning Changes — What We Did (in order)

### #1 — Stock Adjustment Module (Tier 1 P1)

**Problem:** Master DB Editor was the only way to correct system-vs-shelf discrepancies, which bypassed the audit ledger.

- New `stock_adjustments` table with 9 reason codes (`ADJUSTMENT_REASONS` in database.py)
- Helpers: `insert_stock_adjustment` / `approve_stock_adjustment` / `reject_stock_adjustment` / `get_pending_stock_adjustments` / `get_stock_adjustment_history`
- **`approve_stock_adjustment` atomically posts a synthetic ledger row** — `consumption` for shortfall, `receipts` for surplus, with `Work_Type='STOCK_ADJUSTMENT'` or `Supplier='STOCK_ADJUSTMENT'` + `posted_txn_ref` ('C:rowid' or 'R:rowid')
- UI: Entry Log → new **"🧮 Stock Count"** tab (Store Keeper). HOD Portal → new **"🧮 Adjustments"** tab.

### #2 — Negative-Stock Guard at EOD Commit

**Problem:** Entry Log over-issue guard was the only check; HOD editing in EOD review, OCR bulk staging, and PWA queue all bypassed it.

- New `validate_eod_no_negative_stock(conn, site_id, edited_df) → list[violations]` in database.py
- Wired as **first step** in `_eod_commit_dialog`, before the type-COMMIT box
- If any violation: dialog shows red banner + violation table (SAP, Material, Current, ToConsume, Deficit) + Close button. Commit path locked.

### #3 — Standard-Cost Inventory Valuation

**Problem:** No money. Couldn't answer "what's the stock at Site A worth?"

- Self-heal: `inventory.Unit_Cost` (default 0) + `receipts.Unit_Cost` (nullable, captured at receive time for future weighted-average)
- 5 helpers: `get_inventory_valuation` / `get_total_inventory_value` / `get_value_by_site` / `get_consumption_value_window` / `format_sar` ("SAR 1.2M" / "SAR 125K" / "SAR 875")
- 4 cache wrappers in `cache_layer.py`
- KPI cards: Live Dashboard hero (Total stock value), HOD Portal hero (Site stock value + 30d consumption), Admin Portal Overview (Total + Biggest-value site + 30d consumption + Pending receipts value)
- Live Dashboard grid: `Unit_Cost` and `Stock_Value` columns merged in
- New report: **💰 Inventory Valuation**. Monthly Summary now carries Issued_Value_SAR / Received_Value_SAR / Closing_Value_SAR columns.

### #4 — Lot Master Table

**Problem:** Lots were inferred by date from receipts. No traceability for recalls / expiry audits.

- New `lots` table (Lot_Number, SAP, Site, Received_Date, Expiry_Date, Supplier, PR_Number, Status, UNIQUE constraint)
- Self-heal: `Lot_Number` column on `receipts` / `consumption` / `pending_issues` / `pending_receipts`
- New view `v_lot_balance` — Received_Qty / Consumed_Qty / Remaining_Qty per lot, identity math
- One-time backfill in `init_db`: legacy receipts with Expiry_Date get synthetic `LOT-YYYYMMDD-SAP` numbers
- Helpers: `create_or_get_lot` / `get_lots_for_item` / `get_all_lots` / `mark_lot_status` / `auto_generate_lot_number` / `suggest_fefo_lot_for_consumption`
- `process_receipt_delivery` + `commit_pending_receipts` now auto-create lots
- `get_fefo_lots` now prefers hard lots (from `v_lot_balance`); falls back to date-allocation for un-lotted legacy data
- Entry Log: auto-attaches FEFO Lot_Number to staged consumption

### #4.5 — Hard FEFO Override (Audit Trail)

**Problem:** Auto-FEFO is silent; if the suggested bin is physically blocked, the store keeper has no audited way to override.

- Self-heal: `FEFO_Override` column on `pending_issues` + `consumption`
- Entry Log → new **"🔄 Pull from a different lot"** expander — only renders when 2+ open lots exist
- Requires reason ≥ 5 chars to activate; sets `Lot_Number` + `FEFO_Override`, fires `log_audit_action("FEFO_OVERRIDE", ...)` + WhatsApp to site HOD in real time

### Bug fixes during tuning

- `report_monthly_summary` closed-conn crash → moved costs lookup inside the original `try` block
- `_eod_commit_dialog` `to_sql` crash → filter `edited_admin_df` to columns that exist in `pending_issues` (the EOD tab merges inventory metadata for display)
- `get_receipt_history` SQL referencing non-existent `r.id` / `r.Timestamp` → use `r.rowid DESC` (receipts table has neither)
- Mac PR email body — was pipe-text, now AppleScript-driven Mail.app HTML table with fixed-width plain-text fallback
- PR PDF — added Received Qty + Pending Qty columns
- Burn Rate compact chart — color-coded by intensity (red/amber/green) + monospace tabular numerics + legend
- Reports `_site_filter` — supervisor + HOD locked to own site; admin only gets "All Sites" dropdown
- Reports duplicate `_rep_site` key — site filter takes `key_suffix` per tab
- Cross-Site tab — reverted matrix removal; >5-item escalation queues separate WhatsApp to target HOD + audit
- HOD Notifications tab — removed the Notification Log; it moved to Admin → WhatsApp Console

### Recently-added reports

- **📥 Daily Receipts** — mirror of Daily Consumption with Receipt_Value_SAR
- **💰 Inventory Valuation** — standard-cost rollup with top-10 share

### Recently-added column hygiene

- **Material_Code in every report** (right after SAP_Code) — joined into `load_live_inventory`, `report_daily_consumption`, `report_daily_receipts`, `report_monthly_summary`, `report_fefo_compliance`, `report_pr_status`, `get_short_dated_stock`, `get_inventory_valuation`
- **`_strip_empty_columns` helper in `pages_internal/reports_page.py`** — auto-drops columns where every row is null/blank/NaN. Always keeps SAP_Code + Material_Code + Date. Never drops numeric columns (0 is data).

### Recently-added PDF formatter

- `reports.py:generate_report_pdf` — narrow fixed widths for short/code/numeric columns (Date 18mm, UOM 12mm, Qty 16mm, SAP 22mm, Lot_Number 30mm, etc.). Remaining width spreads across descriptive columns. Numeric columns right-aligned. Per-cell char budget scales with column width. Header redraws on every page break.

---

## 2C. Tuning Round 2 (2026-06) — Operational Polish & Cloud Hosting

### Streamlit Cloud hardening (no more startup crashes)

- `get_connection()` now uses `sqlite3.connect(timeout=30)`, applies WAL/synchronous/busy_timeout via per-PRAGMA try/except (Streamlit Cloud's FUSE filesystem rejects WAL), and runs a corruption probe (`SELECT name FROM sqlite_master LIMIT 1`) that wipes-and-recreates the file on `DatabaseError`. Caught: `malformed database schema (MASTER EQUIPMENTS)` from prior crashed deploys.
- `auth.seed_default_users()` switched to `INSERT OR IGNORE` (race-safe across the cloud's multiple uvicorn workers) while keeping the count-gate so `test_seed_does_not_run_if_users_exist` still passes.
- `requirements.txt`: `twilio` added; `pywhatkit` now `; sys_platform == 'win32' or sys_platform == 'darwin'` (it pulls in display libs that fail on cloud Linux).

### WhatsApp worker — Twilio + embedded thread

- `whatsapp_worker._send_whatsapp()` tries Twilio first (reads `st.secrets['twilio']` or `TWILIO_*` env vars), falls back to `pywhatkit` on local desktop. **`pywhatkit` is lazy-imported** — module-level import was stalling Streamlit startup by ~30 seconds because of pywhatkit's GUI deps.
- `main.py:78` starts the worker via `@st.cache_resource` so it spawns exactly once per server lifecycle.
- Sandbox: every Twilio recipient must `join <code>` to the sandbox number once before they can receive (out-of-band onboarding step — document in your rollout playbook).

### Material categories + rubber-MTC workflow

- New `inventory.Category` column; values from `config.MATERIAL_CATEGORIES` (`Consumable`, `Equipments`, `Utilities`, `Maintenance`, `Others`, `Rubber materials`, `Tools`, `QC items`).
- SK Receipt Staging: when selected SAP is in `Rubber materials`, show MTC Number + MTC file uploader. `save_mtc_document()` always writes a row — `status='attached'` or `status='missing'` — so HOD can act on missing ones.
- HOD Pending Receipts tab: red banner lists items with `status='missing'`, with **✉️ Draft Logistics Email** button (`mailer.draft_rubber_mtc_email`). Successful send flips status to `sent_to_logistics`.
- Reports page → Generate tab: **Filter by Category** dropdown next to the SAR toggle.

### Attachments + HOD DOC tab

- Schema: `entry_attachments` (BLOB + `disk_path` mirror; `doc_type IN ('consumption','receipt','return')`).
- SK forms: attachment expander on Consumption Log, Receipt Staging, and (via new) Return Items. Per-batch OR per-date scope. Allowed: PDF / JPEG / JPG / XLSX (`config.ATTACHMENT_ALLOWED`).
- Doc number = DDMMYY for consumption; DN_No (or manual override) for receipts; Return DN No. for returns.
- HOD Portal **📎 DOC** tab — sub-tabs Consumption / Receipt / Return, period filter, doc-number text filter, per-file ⬇️ download button.

### Real returns workflow (NEW — distinct from Returnable Items)

- Schema: `pending_returns`. Approval writes to the existing `returns` ledger so dashboard math + reports auto-update.
- SK **↩️ Return Items** tab in Entry Log: multi-row staging grid pattern. Material picker is restricted to last-30-day receipts at the SK's site. If multiple receipts of the same SAP exist, the SK picks the exact one (Date / DN / Received Qty). Qty hard-capped to that receipt's `received_qty`. Mandatory: Return DN No. + reason (work-types) + at least one attachment. Override checkbox widens picker to 12 months and requires written justification — flagged red in the HOD's queue.
- **Add to Grid** captures file bytes immediately (Streamlit UploadedFile is single-use) into `st.session_state["_ret_queue"]`. Submit Batch fires one consolidated WhatsApp ping.
- HOD **↩️ Returns** tab: one card per pending return, override rows in red. **✓ Approve** → `approve_return_request()` (idempotent) → `returns` row + `mailer.draft_return_logistics_email()` opens an Outlook/Mail.app draft. **✗ Reject** removes from pending list.
- Returnable Items remains **tool-loan only** — no DN, no attachment. Don't conflate the two.

### QR label approval flow

- Schema: `qr_approval_requests` (status `pending|approved|rejected`).
- SK **🏷️ QR Label Request** tab: `st.multiselect` materials, per-item label quantity, one Submit Batch button.
- HOD **🏷️ QR Approval** tab: checkbox-column data editor → Approve Selected / Reject Selected. Approved sub-tab has **📥 Download QR Labels PDF for ALL approved** which generates a consolidated PDF via `reports.generate_qr_labels_pdf`.

### Field-level changes

- `config.OPTIONAL_ISSUE_COLS = set()` — every field on every Entry Log / Admin / HOD form is mandatory now. **Exception:** `Expiry_Date` on receipt staging — explicitly optional.
- Live Dashboard column order (see Architecture map above). Identity formula now includes `Opening_Stock`.
- HOD Pending Receipts: `Material_Code` + `Equipment_Description` shown.
- SK Receipt Staging: `rejection_reason` filtered out (HOD-side state was leaking into the SK form).
- HOD Receive Material form rebuilt into a half/half column split so both columns are the same height regardless of how many dynamic fields exist.
- Toast emojis: Streamlit rejects "✓" / "✗" — use "✅" / "🚫" / specific glyphs.

### Per-site dropdowns (Work Type, Tank No.)

- `system_settings.Site_ID` column (NULL = global default, non-NULL = site override). `get_work_types(conn, site_id)` and `get_tank_nos(conn, site_id)` fall back to global if no site-specific values exist.
- HOD **⚙️ Site Config** tab → add/delete per-site values. `bust_settings_cache()` clears the relevant `@st.cache_data` wrappers.

### Bug found by the harness

- `receipts` table was missing the full logistics column set (`DN_No`, `Serial_No`, `Vehicle_No`, `Driver_Name`, `Pallet_No`, `Mob_From`, `Prepared_by`, `Mob_To`, `Received_by`, `DN_Copy`, `Location`, `PR`). `process_receipt_delivery` builds INSERTs from `extra_fields`, but its outer try/except swallowed the `OperationalError`. **In production, SK-staged receipts with any logistics field were silently dropped on HOD commit.** Fixed at `database.py:553` — full self-heal block.

---

## 2D. Automated bug harness — `bug_check.py`

**Run:** `python bug_check.py` (add `--verbose` to stream each check). Finishes in ~5 seconds.

Covers (114 checks across 13 areas):

- **Schema:** every table + every critical column. `init_db()` idempotency.
- **RBAC matrix:** 14 role × page combinations including the SK-only Entry Log lock.
- **Module import smoke:** every `pages_internal/*` and top-level module loads without raising.
- **Math identity:** seed → receipts/consumption/returns → `Current_Stock == 113` for SAP-001 (Opening 100 + 30 − 12 − 5).
- **Workflows:** consumption stage→commit, receipt stage→commit, return submit→approve→ledger, return reject, returnable tool loan, QR approve/reject, MTC attached + missing, attachment BLOB round-trip.
- **Reports:** every `report_*` runs; Daily Receipts has `Material_Code`.
- **Mailer:** rubber-MTC + return-logistics drafts (subprocess patched — no actual emails).
- **Audit + WhatsApp queue + Sites.**

Safety: a throwaway DB under `tempfile.mkdtemp()` is patched into `database.DB_FILE` and `database.UPLOADS_ROOT` **before** the project modules import. `subprocess.Popen` is monkey-patched to a no-op so mailer helpers never actually launch. The tmp dir is wiped on exit. `gi_database.db` is never touched.

Output: `BUG_REPORT.md` at repo root. Exit code `0` on full pass, `1` on any failure (CI-ready).

When you add a new database function or schema column, add a check at the bottom of `bug_check.py:main()` and re-run.

---

## 2E. Tuning Round 3 (2026-06) — v3.0 Procurement Chain Architecture

The largest single feature batch since launch. Five phases, fully additive, no edits to the existing SK / HOD / Admin tabs, EOD commit path, identity math, cache layer, mailer, WhatsApp worker, or Ollama integration. Phase 1 schema + roles, Phase 2 Logistics Portal, Phase 3 Warehouse Portal, Phase 4 HOD In-Transit, Phase 5 Admin Oversight + bell + reminders + reports.

### Two new role-locked portals

- **🚚 Logistics Portal** (`pages_internal/logistics_portal.py`) — 8 tabs. Role-locked to `{logistics, admin}` via `_EXACT_ROLE_PAGES`.
- **🏭 Warehouse Portal** (`pages_internal/warehouse_portal.py`) — 6 tabs. Role-locked to `{warehouse_user, admin}` via the same lock dict.
- Admin shadow: an admin who picks either page sees a sidebar warehouse picker (warehouse portal) or just lands as logistics-equivalent (logistics portal). For the warehouse portal, the shadow warehouse picker writes to `st.sidebar.selectbox(..., key="_wh_admin_shadow_wh")` so context survives reruns.

### End-to-end procurement chain

```
Site HOD → submits PR    → Logistics: 📥 Incoming PRs
Logistics → creates PO   → Site HOD: 📋 Purchase Requests + Admin Portal
Logistics → assigns to WH → Warehouse: 🔔 Incoming Assignments
Warehouse → acks + receives from vendor
Warehouse → drafts DN (RL/BL safe) → Logistics: ✈️ approval queue
Logistics → approves date → HOD: 🚚 DN Approvals
HOD → approves content → SK: 🚚 Incoming DNs expander (in Receipt Staging)
SK → marks received → receipts row + DN closed + inventory cache busted
```

The legacy email-based PR follow-up (HOD Portal → PR tab → 📧 Draft Outlook Email + 📥 Download PR PDF) is PRESERVED and continues to work for direct-with-vendor flows. The new in-app chain is opt-in: HODs activate it per-PR via the new "🚚 Submit PR(s) to Logistics Portal" expander on the PR tab. **Both paths coexist for now; email path is marked for future deprecation once procurement chain adoption is complete.**

### Site HOD additions (additive only)

- New action in PR tab: "🚚 Submit PR(s) to Logistics Portal" expander — multi-select open PRs → submit. Calls `submit_pr_to_logistics()` which flips matching `pr_master` rows to `logistics_status='submitted'` and fires `pr_submitted_to_logistics` event to the `logistics` role.
- New 14th tab: **🚚 DN Approvals** — per-DN card with View Lines + ✅ Approve / ❌ Reject popover (mandatory rejection reason). Approval calls `hod_decide_dn(approve=True)` which mirrors DN lines into `pending_receipts.status='pending_sk'` so SK sees them on the next tab.
- New 15th tab: **🚚 In-Transit** — 3 read-only sub-tabs:
  - Active in-transit DNs with per-DN status pill, RL/BL family chip, expand-to-preview lines, and **🔁 Request reschedule popover** (date defaults to current ETA + 3 days; `min_value=today`; mandatory reason). Submits to existing `request_reschedule(po, dn, current, requested, reason, role='hod', user=username)` helper.
  - My reschedule requests — custom HTML table with status pill + decided-by + notes.
  - Force-closures affecting me — 3-way join fallback (Site_ID direct → `purchase_orders.Site_ID` → `pr_master.Site_ID`) so closures with NULL Site_ID still surface on the originating site.

### Site SK addition (additive only)

- New expander at top of Receipt Staging: **🚚 Incoming Delivery Notes from Warehouse** — per-DN container with View Lines + ✅ Mark as Received button. Calls `sk_mark_dn_received()` which:
  1. Writes one `receipts` row per DN line (with `DN_Number`, `Warehouse_ID`, `PO_Number_Source` populated for full traceback)
  2. Flips the DN to `'received'`
  3. Deletes the mirror rows from `pending_receipts`
  4. Calls `bust_inventory_cache()` so Live Dashboard reflects new stock the same minute

### Admin additions

- New 11th tab in Admin Portal: **🚚 Logistics Oversight** — KPI strip (Open PRs / Open POs / Active DNs / Vendor Returns / Reschedules / Force-Closures) + site + warehouse filters + 6 sub-tabs (PRs / POs / DNs / Vendor Returns / Force-Closures / Reschedules). 100% read-only — admin still acts in role-specific portals for any mutation.

### Sidebar: 🔔 Notifications bell

- Rendered in `main.py:_render_notifications_bell()` between role card and navigation radio. Reads `count_unread_notifications()`.
- Unread count → red pill badge (caps at "99+"). Button text changes to `"Open inbox (N unread)"` with `type='primary'` when N>0.
- `@st.dialog("🔔 Notifications")` opens modal with Only-unread toggle, per-card severity colour (info/warning/critical/success), per-row 👁 Mark read, bulk ✅ Mark all as read.
- Backed by `app_notifications` table. Visibility = `recipient_user=me OR (recipient_role=my_role AND (site/warehouse scoping matches))` — the OR clause is **parenthesised** so the optional `AND read_at IS NULL` binds to both branches (this was a bug found mid-Phase 1).

### Reminder sweep

- `database.sweep_delivery_reminders(today=None)` fires T-2 / T-1 / T-0 events for upcoming `purchase_orders.Expected_Delivery` AND `delivery_notes.DN_Date`.
- Severity: T-2 / T-1 = `warning`, T-0 = `critical`.
- PO fires: Logistics + originating Site HOD.
- DN fires: Logistics + Site HOD + warehouse_user at the receiving warehouse.
- Idempotent via UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) on `delivery_reminders_sent` table.
- Triggered from `whatsapp_worker._maybe_run_delivery_reminders()` which day-marker-guards via `app_settings.delivery_reminders_last_run` — the 60-second poll loop runs the sweep at most once per local day.

### Three new reports (in the existing reports module)

- **🧾 PO Status** — per-PO ordered / delivered / returned qty totals + line count + status. Summary: Open / Delivered / Closed-or-Force / Total Value SAR.
- **🏭 Warehouse Throughput** — DN counts by warehouse, split by every pipeline state + RL/BL family. Summary: Warehouses / DNs / Received / Open pipeline / RL DNs / BL DNs.
- **🛑 Force-Closures** — every PR/PO/line closure with reason, closed-by, timestamp.

All three honour the existing site + date filters and export as PDF/Excel/CSV through the existing toolchain. Access: Supervisor + HOD + Admin (same as existing reports).

### Bug-fix patches that shipped with v3.0

- **Double TZ removed** in Admin Pending Requests, HOD My Requests, and Admin WhatsApp Console: `get_pending_requests()` and `get_whatsapp_log()` already pass results through the `_localize()` boundary helper. Page-level `localize_timestamps_df` / `auto_localize_timestamps` / `timedelta(+3)` calls were stacking. Removed.
- **Pending Requests now joins inventory.** `get_pending_requests()` LEFT JOINs `inventory` so each row carries `Material_Code` + `Material_Name` + `UOM`. Admin Pending Requests editor reorders columns + uses `column_config` to relabel headers.
- **Ollama unreachable now graceful.** `main.py` Hub Assistant pre-flights `manual_qa.health()` before streaming. Unreachable → clean `st.warning("🤖 Local AI is offline. Please run 'ollama serve' in your terminal to enable the AI assistant.")`. Mid-stream connection drops caught by `(ConnectionError, OSError, TimeoutError)`. No more raw `http://localhost:11434` strings leaking into chat.

---

## 2F. Tuning Round 4 (2026-06) — Workstream A.5 step 1: Phase 7A Employee Site Binding

First slice of the operational-polish arc that precedes Workstream B (Docker/Deployment). Six features in this arc; this round delivers feature #5 (Employee Site Binding), which is the prerequisite for Phase 7B (Supervisor Material Request workflow).

### Schema (self-heal in `init_db()`)

- `employees.Site_ID TEXT` — nullable; legacy rows backfill to NULL until Admin assigns them.
- `CREATE INDEX IF NOT EXISTS ix_employees_site ON employees(Site_ID)` — supports per-site lookups in Phase 7B's supervisor form.

### Helper changes — `database.py`

- `add_employee(... , *, site_id=None, conn=None)` — kwarg added. NULL = unbound.
- `update_employee(... , *, site_id=None, ...)` — three-valued sentinel: `None` = leave untouched, `""` = clear to NULL, `"<site>"` = set binding.
- `list_employees(... , *, site_id_filter=None, ...)` — `"__UNASSIGNED__"` sentinel returns NULL-binding rows (powers the red banner).
- `list_employees_for_site(site_id, *, status_filter="active", conn=None)` — convenience wrapper (used by Phase 7B's supervisor form).
- `bulk_assign_employees_to_site(id_numbers, site_id, *, updated_by, conn)` — N-row UPDATE behind the Admin red-banner widget. Audited as `EMPLOYEE_BULK_SITE_ASSIGN`.
- `import_employees_csv` — optional `Site_ID` CSV column. **Absent column never overwrites an existing binding** (back-compat).

### UI — `pages_internal/admin_portal.py`

- **➕ Add form** gains a Site picker (`— Unassigned —` + every site).
- **✏️ Edit form** gains a Site picker; this is the **only** path that can move an employee between sites.
- **📥 CSV caption** updated to mention the optional `Site_ID` column.
- **👥 Roster sub-tab**:
  - Red banner above the filter row listing all NULL-binding employees, with a bulk-assign expander (multi-select + Site dropdown + Apply).
  - New Site filter dropdown with "All Sites" / individual sites / `— Unassigned —`. Default "All Sites".
  - New `Site` column in the displayed dataframe.

### UI — `pages_internal/hod_portal.py`

- New `_render_employees_tab(user, site_id)` function (inserted before `page_hod_portal`).
- Tab labels list gains `"👷 Employees"` at **index 11** (right after `⚙️ Site Config`). Indices 12–15 shifted +1: `📎 DOC`=12, `🏷️ QR Approval`=13, `🚚 DN Approvals`=14, `🚚 In-Transit`=15.
- HOD-side roster is strictly site-scoped via `list_employees_for_site(site_id)`.
- HOD can: add (auto-bound), edit name/phone/department, change status (active/inactive/suspended).
- HOD CANNOT: move an employee to a different site (no Site picker on the HOD edit form) or CSV-import (Admin-only).

### Tests

- `bug_check.py` +16 checks (1 schema column + 15 Phase 7A behaviour checks). **Total now 284/284.**
- UI crawler unchanged (16/16) — it indexes pages not tabs, so the new HOD tab is exercised on every HOD Portal render automatically.

### Contracts (don't break these in Phase 7B)

- `Site_ID = NULL` semantics: legacy / unassigned. The Phase 7B supervisor form will use `list_employees_for_site(site)` and therefore never see NULL-binding workers. Phase 7B depends on Admins clearing the unassigned banner before going live.
- `update_employee(site_id=None)` MUST remain "leave untouched" — `_render_employees_tab` in HOD Portal relies on this to enforce the no-cross-site-transfer rule by simply not passing `site_id`.
- `ID_Number` remains globally UNIQUE. Cross-site transfer is a single-row Site_ID update by Admin — never delete-and-recreate (would break QR badges + Smart Scan history).

---

## 2G. Tuning Round 5 (2026-06) — Workstream A.5 step 2: Phase 7B Supervisor Material Request

The user-spec "Point #4" of Workstream A.5: Supervisor specifies what a worker needs → SK approves → entries flow into the existing pending_issues / EOD commit pipeline. The original request stays around forever as the **intent** ledger, so management can compare against **actual** consumption via the new report card.

### Schema (self-heal in `init_db()`)

- `supervisor_material_requests` — header. UNIQUE `request_no` (format `SMR-YYYYMMDD-NNNN`, global per-day counter). Status FSM: `pending_sk → {approved|rejected|cancelled}`. CHECK constraint enforces it. `posted_pending_ids` is a JSON list of rowids in `pending_issues` written at approval — single-source-of-truth back-trace.
- `supervisor_material_request_items` — line rows. Captures `Stock_At_Request` snapshot + `Available_Flag` (0/1) at insert. `SK_Adjusted_Qty` is filled by the SK at approval time; `0` semantically means "drop this line" per user-approved Spec Q6.
- Two indices: `ix_smr_site_status(Site_ID, status)`, `ix_smr_requested_at(requested_at)`.
- Self-heal `Source_Ref TEXT` on both `pending_issues` and `consumption`. Format: `SMR:{request_no}:{line_id}`. NULL on all SK-typed manual rows (back-compat).

### Helpers — `database.py` (new "Phase 7B" section, ~600 LOC)

- `generate_smr_request_no()` — `SELECT MAX → +1` per-day, returns formatted string.
- `create_supervisor_request(*, site_id, worker_id, job_tank_place, old_ppe_returned, no_return_reason, items, supervisor_username)` — single-transaction insert. Validations (each returns `(False, msg)`, never raises): worker must exist + be active + bound to `site_id`; `Job_Tank_Place` required; PPE flag in `{0,1}`; reason required when `PPE=0`; ≥1 item; every SAP_Code must exist in inventory. Snapshots stock per line via `_smr_snapshot_stock()`. Fires `queue_app_notification(event_key="smr_submitted")` + `fire_whatsapp_event("smr_submitted", …)` to every SK at the site.
- `list_supervisor_requests(site_id=None, status=None, requested_by=None, days=None)` — header DataFrame, auto-localized timestamps.
- `get_supervisor_request(request_id)` → `(header_dict, items_df)`.
- `update_supervisor_request_item(item_id, *, requested_qty, sk_adjusted_qty, notes)` — only while parent status = `pending_sk`.
- `delete_supervisor_request_item(item_id)` — same lock.
- `cancel_supervisor_request(request_id, by_username)` — supervisor self-cancel only while `pending_sk`.
- `approve_supervisor_request(request_id, sk_username)` — **the critical transaction**. Mirror payload per line:
  - `Quantity = COALESCE(SK_Adjusted_Qty, Requested_Qty)` (zero auto-drops the line)
  - `Work_Type = "SUPERVISOR_REQUEST"` (new sentinel)
  - `Issued_To = Worker_Name`, `Tank_No = Job_Tank_Place`
  - `Source_Ref = "SMR:{request_no}:{item_id}"`
  - `status = "pending_hod"` → lands in HOD's EOD Commit, gated by the existing `validate_eod_no_negative_stock` safety net
  - Allow-list pattern (same shape as `pwa_stage_pending_issues`) → forward-compat with any future `pending_issues` column addition.
- `reject_supervisor_request(request_id, sk_username, reason)` — mandatory reason. No pending_issues writes.
- `report_supervisor_intent_vs_actual(site_id=None, days=30)` — joins approved lines to `consumption.Source_Ref` via correlated subquery; computes `Variance_Pct`. Blank `Actual_Qty` = approved but HOD hasn't committed yet.
- `get_open_returnables_for_employee(employee_id)` — drives the SK side-panel. Matches both the CV-loan path (`cv_employee_id`) AND the legacy manual-loan path (`borrower_name`) so older loans still surface.

### config.py

- `PAGE_ACCESS` gains `"🛡️ Supervisor Portal": "supervisor"`.
- `WHATSAPP_TRIGGERS` gains 4 keys: `smr_submitted`, `smr_approved`, `smr_rejected`, `smr_cancelled`. All default `True`.

### main.py

- New import `page_supervisor_portal`.
- `_EXACT_ROLE_PAGES` gains `"🛡️ Supervisor Portal": {"supervisor", "admin"}` — same lock pattern as the procurement portals.
- `_PAGE_BLOCKED_ROLES["📊 Reports"]` now includes `"supervisor"` — Reports nav is hard-hidden for the supervisor per Spec Q1.
- New route branch `elif page == "🛡️ Supervisor Portal": page_supervisor_portal(user)`.

### UI — `pages_internal/supervisor_portal.py` (NEW, ~310 LOC)

One top-level tab `📦 Request Material` with three sub-tabs:

1. **🆕 New Request:** worker picker (name-first format `Ahmed Ali (EMP-1042)`, active employees bound to site only), Job/Tank/Place text input, Old PPE radio + reason text area (reason only appears when "No"). Item picker uses the same search-and-add cart pattern as HOD Cross-Site (Spec Q3). Each cart row shows live stock with red/amber/green colouring and a ⚠️ short warning when stock < qty. Submit calls `create_supervisor_request`.
2. **📋 My Requests:** status + window filter, per-card status pill, view-items expander, Cancel button while pending. SK rejection reason is surfaced inline.
3. **📊 Intent vs Actual:** calls `report_supervisor_intent_vs_actual` and renders with formatted Variance %.

If the site has no active employees, a red empty-state banner directs the supervisor to ask the HOD to add workers via the Phase 7A `👷 Employees` tab.

### UI — `pages_internal/daily_issue_log.py` (SK Portal)

- New 7th tab `🛒 Supervisor Requests` (preserves all 6 existing tab indices).
- Per-request card: header info, PPE flag with reason, **open-loan side-panel** showing the worker's active returnable items (Spec Q5).
- `st.data_editor` over the line items: SK can adjust `Requested_Qty` / `Approved_Qty` (the `SK_Adjusted_Qty` column) / `Notes`. Live banner if any effective qty exceeds Stock@Req.
- Three action buttons:
  - **💾 Save edits** — persists row-by-row via `update_supervisor_request_item`.
  - **✅ Approve** — auto-saves edits first, then calls `approve_supervisor_request`.
  - **❌ Reject** — popover with mandatory reason text area → `reject_supervisor_request`.

### UI — `pages_internal/reports_page.py`

- New report type `("smr_intent_actual", "🛡️", "Supervisor Intent vs Actual", …)` appended to `_REPORT_TYPES`.
- Dispatch in `_run_report_raw`: converts the date picker into a `days` window for `report_supervisor_intent_vs_actual`, then clamps via `df_to`. Summary card: Lines, Avg_Variance_Pct, Lines_Over_10pct, Lines_Not_Yet_Committed.
- Inherits the existing site filter — HOD locked to their site, Admin can pick All Sites.

### Tests — `bug_check.py`

+21 Phase 7B checks. Existing 268 + Phase 7A 16 + Phase 7B 21 + schema column hits = **326/326**. Coverage:
- Schema (tables + every column).
- request_no generation (day-empty + increment).
- Happy-path insert.
- Five validation rejection paths (wrong-site worker, empty items, PPE-no-without-reason, unknown SAP, etc).
- Stock snapshot + Available_Flag mechanics.
- Approval mirror — column mapping, posted_pending_ids JSON, idempotency, zero-adjusted drop semantics.
- Reject path — reason required, no pending_issues writes.
- End-to-end: approve → `commit_eod()` → consumption row with Source_Ref preserved.
- Lock-out: update_item / cancel refused after decision.
- delete_item works while pending.
- Report joins on Source_Ref.
- Open-returnables side-panel finds CV-path matches.
- WHATSAPP_TRIGGERS has the 4 new keys defaulted True.

UI crawler picks up the new Supervisor Portal page automatically → **17/17**.

### Contracts (don't break in Phase 7C+)

- `Work_Type = 'SUPERVISOR_REQUEST'` is a sentinel — never overwrite it with manual SK edits; reports filter on it for SMR-sourced consumption.
- `Source_Ref` format is `SMR:{request_no}:{line_id}` — Phase 7C's variance / drift reports already join on this exact string. Don't trim, don't change separators.
- `posted_pending_ids` is JSON-encoded — if you ever back-out an SMR approval, those rowids are the rollback target.
- `pending_issues` mirror writes `status='pending_hod'` so the HOD's EOD Commit + negative-stock guard catches them — never bypass to `pending_hod` skipping the validator.
- `approve_supervisor_request` is idempotent by design — second call refused with "already approved". Don't relax this; it's the only thing preventing a click-spammer double-debiting stock.

---

## 2H. Tuning Round 6 (2026-06) — Workstream A.5 step 3: Phase 7C HOD Cross-Site View Notification

Spec Point #1 of Workstream A.5: when a HOD opens another site's stock view, the target site's HOD must know. Without spamming. With a visible indicator that compliance trail was created.

### Schema (self-heal in `init_db()`)

```sql
CREATE TABLE IF NOT EXISTS cross_site_views (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_username TEXT NOT NULL,
    viewer_site_id  TEXT,
    target_site_id  TEXT NOT NULL,
    view_date       TEXT NOT NULL,
    first_seen_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(viewer_username, target_site_id, view_date)
);
CREATE INDEX ix_csv_target_date ON cross_site_views(target_site_id, view_date);
CREATE INDEX ix_csv_viewer_date ON cross_site_views(viewer_username, view_date);
```

The UNIQUE constraint **is the entire debounce mechanism**. `INSERT OR IGNORE` returns `rowcount=0` on duplicate same-day attempts. No Streamlit-session coupling, no timer code, no race conditions across browser tabs or simultaneous AppTest reruns.

### Helpers — `database.py` (new "Phase 7C" section, ~120 LOC)

- `record_cross_site_view(viewer_username, viewer_site_id, target_site_id, *, conn=None) → bool` — pure debounce. Returns True on first-of-day insert, False on duplicate / blank inputs / self-view. **Never raises.**
- `notify_cross_site_view(viewer_user: dict, target_site_id, viewed_item=None, *, conn=None) → bool` — the orchestrator. Skips silently when:
  - `viewer_user['role'] == 'admin'` (admin shadowing → silent per Spec Q2(b))
  - `record_cross_site_view` returned False (dedupe / invalid)
  
  On first-of-day fire:
  - `queue_app_notification(event_key='cross_site_viewed', recipient_role='hod', recipient_site=target_site_id, severity='info', ...)`
  - `log_audit_action(viewer, 'CROSS_SITE_VIEW', 'cross_site_views', 'viewer={X} target={Y} date={Z}')`
  - `fire_whatsapp_event('cross_site_viewed', phone, msg)` per HOD at target site — gated by `WHATSAPP_TRIGGERS['cross_site_viewed']` (default False)
  
  Returns True iff a notification actually fired. Drives the UI banner's "has been notified" vs "already notified earlier today" wording.

### Message tone (spec Q3(b) — context-rich)

- **Title:** `"HOD of {viewer_site} is viewing your stock"`
- **Body:** `"{viewer_username} from {viewer_site} (looking at {item}) is checking your stock — they may submit a transfer request shortly."`
- `viewed_item` is whatever the supervisor selected in the SAP picker at fire time. Subsequent item changes same day do NOT re-fire (dedupe per spec Q1) and do NOT update the message body.

### config.py

`WHATSAPP_TRIGGERS` gains `"cross_site_viewed": False`. **Default off** per spec Q6(b) — in-app bell badge is enough; flip later if HODs explicitly want phone pings.

### UI — `pages_internal/hod_portal.py`

Modify `_render_crosssite_tab` only. Hook lives inside the existing `if target_site and item_selection:` block (right-column live-stock render) — that's the **moment of actual data view**, not bare tab-open. Spec Q1.

Two indicators rendered side-by-side (spec Q4(c) — both):

1. **Top-of-tab persistent banner** — gold-bordered card with two lines:
   - "👁️ You are viewing `{target_site}` inventory."
   - "The HOD of **{target_site}** has been notified of your view today." OR "...was already notified earlier today." (dedupe wording per spec Q7)
   No dismiss button — sticky while a target is picked (spec Q5).

2. **Fixed-position corner pill** — `position:fixed; top:72px; right:22px; z-index:999;` gold background, dark text. Survives scroll. Compact label: "👁️ Viewing {target_site}".

Both indicators are **suppressed** when the user role is admin (admin shadowing → silent, mirrors the notification suppression).

### Tests

- `bug_check.py` +14 Phase 7C checks (+6 schema column existence). **Total now 346/346.**
- UI crawler unchanged at 17/17 — same HOD Portal page count, no new top-level routes.

Coverage:
- Schema (table + every column + both indices).
- UNIQUE constraint enforced at the DB layer (raw INSERT raises).
- `record_cross_site_view` returns True on first call, False on dedupe.
- Different target same day → True (new (viewer,target) tuple).
- Different viewer same target → True.
- Self-view never records (skipped).
- Blank username/target → False, never raises.
- Admin role → notify silent, no row written.
- First fire → app_notifications row queued with correct recipient_role/site, item context in body.
- First fire → audit row written with correct columns (`username`/`action_type`/`target_table`).
- Dedupe → exactly 1 app_notifications row (no double-send).
- `WHATSAPP_TRIGGERS['cross_site_viewed']` defaults False.

### Contracts (don't break in Phase 7D+)

- The UNIQUE tuple is `(viewer_username, target_site_id, view_date)` where `view_date` is **local ISO date** (`datetime.date.today().isoformat()`). Don't switch to UTC — the spec says "calendar day" and HOD-perspective dedupe must match wall-clock day at site.
- `notify_cross_site_view` is the ONLY entry point for cross-site view side-effects — never call `queue_app_notification(event_key='cross_site_viewed')` directly from a page.
- Admin role check is `(role or "").lower() == "admin"`. Don't tighten this to `== "admin"` without lowercase — users can theoretically write `"Admin"` casing in legacy seed data.
- The fixed-pill `z-index:999` sits BELOW the notification bell modal (`@st.dialog` ≥ 1050) but ABOVE page chrome. Don't raise it past 1000 or it'll cover the bell.

---

## 2I. Tuning Round 7 (2026-06) — Workstream A.5 step 4: Phase 7D PO Notifications with Strict Masking

Spec Point #6 of Workstream A.5: when Logistics issues a PO, the destination site's HOD and SK must learn about it **without ever seeing vendor or financial data**. Operational tracking only.

### The leak we fixed (regression guard now in bug_check.py)

Pre-7D, `create_po_manual` queued one notification with the literal body `f"Vendor: {Vendor_Name}"` — every site-level user could see who Logistics was buying from. Closed by Phase 7D's mandatory masker.

### Helper extensions — `database.py`

**`PO_VENDOR_MASK_FIELDS`** (module-level tuple, 17 entries) — the canonical list of header fields blanked when `hide_vendor=True`:

- **Vendor identity** (6): `Vendor_Code`, `Vendor_Name`, `Contact_Person`, `Contact_Email`, `Mobile`, `Our_Email`
- **Commercial terms** (6): `Inco_Terms`, `Payment_Terms`, `Quotation_No`, `Quotation_Date`, `Your_Reference`, `Our_Reference`
- **Financial totals** (5): `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Total_Amount`, `Amount_In_Words`

Intentionally **kept visible** (operational tracking, not commercial): `PO_Type`, `PO_Date`, `Expected_Delivery`, `Site_ID`, `PR_Number`, plus everything in `po_items` except `Unit_Price` + `Total_Price` (those gate behind `hide_prices=True`).

**`get_po_detail(po_number, hide_prices=False, hide_vendor=False, conn=None)`** — added the `hide_vendor` axis orthogonal to `hide_prices`. Back-compat: all existing callers behave identically. The warehouse-side three-layer price defence is untouched.

**`build_po_site_notification(po_number, *, conn=None)`** — single entry point for every site-bound PO notification payload. Internally calls `get_po_detail(hide_prices=True, hide_vendor=True)` so future callers cannot accidentally bypass the masker. Returns:

```python
{
    "site_id":           str | None,
    "title":             "PO {n} issued for delivery to {site}",
    "app_body":          "...",          # multi-line, in-app body
    "whatsapp_body":     "...",          # mirrors app_body line-for-line
    "pr_numbers":        "PR-100, PR-200",  # distinct PRs across items
    "expected_delivery": "2026-06-25" or "—",
    "item_count":        int,
    "total_qty":         float,
}
```

Body shape: header lines (PO Number, PR Number(s), Expected Delivery, Items/Total Qty) + top-5 line items as `• {Material_Code} — {Description} — {Qty} {UOM}` + `… and N more line(s)` overflow caption when `> 5`. WhatsApp version adds a `🧾` emoji header and `*bold*` markers around the title line; everything else is identical.

### `create_po_manual` notification refactor

Old leaky block (15 LOC, queued one notification, leaked Vendor_Name in body) → replaced with:

```python
if site_for_notif:
    summary = build_po_site_notification(po_number, conn=conn)
    for _role, _link in (("hod", "📋 HOD Portal"),
                         ("store_keeper", "📝 Entry Log")):
        queue_app_notification(event_key="po_issued", ..., body=summary["app_body"], ...)
        for _ph in get_site_role_phones(_role, site_for_notif, conn=conn):
            fire_whatsapp_event("po_issued", _ph, summary["whatsapp_body"], conn=conn)
```

Defensive: `if site_for_notif` guards against `Site_ID=NULL` POs — no recipient, no notification queued.

### config.py — unchanged

Existing `WHATSAPP_TRIGGERS["po_issued"]: True` covers the upgraded behaviour. The trigger key is reused for both HOD and SK fan-outs. No new key added — keeps the trigger table clean and avoids per-channel toggles for the same event.

### Tests

- `bug_check.py` +16 Phase 7D checks. **Total now 362/362.**
- Categories: mask field count + signature; default no-mask back-compat; `hide_vendor=True` strips all 17 fields; PO_Type + PO_Date preserved; combined masks; summary title/site/PR-list dedup/Expected_Delivery/line truncation; vendor + financial leak regression guards; WhatsApp mirrors in-app; HOD fan-out; SK fan-out; site-less PO → no notification.

UI crawler unchanged (no UI changes in 7D).

### Contracts (don't break in Phase 7E+)

- `PO_VENDOR_MASK_FIELDS` is the single source of truth for what counts as "commercial". If you add a new commercial column to `purchase_orders`, append it to this tuple — otherwise the masker silently misses it.
- `build_po_site_notification` is the ONLY entry point for site-bound PO bodies. Don't construct PO notification bodies inline anywhere else — the masker would be bypassed.
- The existing warehouse-side call `get_po_detail(po_number, hide_prices=True, conn=conn)` continues to NOT pass `hide_vendor=True`. Warehouses NEED the vendor info to receive goods correctly — the three-layer price defence is what protects them from seeing prices, and `hide_vendor` is opt-in per call.
- `create_po_manual` notification block lives inside a `try/except: pass` so a build error never blocks PO creation. Don't move the build call outside that guard — the PO write must always succeed even if notification fan-out fails.
- The `po_issued` WhatsApp trigger fan-out hits HOD phones AND SK phones. If a site needs different on-off control per role, split the key into `po_issued_to_hod` / `po_issued_to_sk` — don't condition inline.

---

## 2J. Tuning Round 8 (2026-06) — Workstream A.5 step 5: Phase 7E Network Resilience / Form Recovery

Spec Point #3 of Workstream A.5. Streamlit is server-rendered HTTP/WebSocket — true offline operation isn't possible without a separate FastAPI queue (out of scope). What we can do is shield in-flight form data from network drops so users don't lose typed entries when the WebSocket reconnects.

### Two-tier safety net + passive indicator

| Tier | Layer | Purpose |
|---|---|---|
| 1 | Browser localStorage via `streamlit-local-storage` | Per-browser, per-device. Survives WS drops, page reloads, tab crashes. Auto-save every Streamlit rerun. |
| 2 | Server-side `form_drafts` table | Cross-device recovery (phone ↔ laptop). Explicit "💾 Save Form Draft" button writes here immediately. Auto-save throttled to 1/min. |
| Passive | Top-left red pill, `navigator.onLine`-driven | Sets user expectation when the browser detects an offline state. No button disabling — pure information. |

### Schema (self-heal in `init_db()`)

```sql
CREATE TABLE IF NOT EXISTS form_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL,
    form_id       TEXT NOT NULL,         -- 'supervisor_request' | 'sk_consumption' | 'sk_receipt_staging'
    site_id       TEXT,
    payload_json  TEXT NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at    DATETIME,              -- now + 7d default
    UNIQUE(username, form_id)
);
CREATE INDEX ix_form_drafts_expires ON form_drafts(expires_at);
CREATE INDEX ix_form_drafts_user    ON form_drafts(username);
```

UNIQUE(username, form_id) → one draft per (user, form). UPSERT via `ON CONFLICT … DO UPDATE` overwrites in place. No race conditions across browser tabs.

### Helpers — `database.py` (Phase 7E block, ~150 LOC)

| Helper | Purpose |
|---|---|
| `upsert_form_draft(username, form_id, payload, *, site_id, ttl_days=7, conn=None)` | UPSERT. JSON-encodes via `default=str` so widgets carrying Decimal / datetime persist as strings (drafts MUST succeed). Truly unserialisable inputs (circular refs) raise ValueError. |
| `get_form_draft(username, form_id, *, conn=None)` | Returns `{payload, updated_at, expires_at}` or None. Hides expired rows even before prune runs. |
| `delete_form_draft(username, form_id, *, conn=None)` | Called after successful submit. Idempotent on missing entries. |
| `list_user_drafts(username, *, conn=None)` | Multi-form listing. Feeds a future Admin "Active Drafts" view (deferred to 7E.1). |
| `prune_expired_form_drafts(*, conn=None) → int` | Daily prune. Wired into `whatsapp_worker._maybe_run_form_drafts_prune()` with `app_settings.form_drafts_last_prune` day-marker. |

`DRAFT_DEFAULT_TTL_DAYS = 7` — covers the Fri/Sat weekend cycle.

### draft_bus — `ui_components.py` (~200 LOC)

```python
render_form_recovery_banner(form_id, username, site_id, state_keys)
auto_save_form_draft(form_id, username, site_id, state_keys)
render_manual_save_draft_button(form_id, username, site_id, state_keys, *, label, key_suffix)
clear_form_draft(form_id, username)
```

**Conflict resolution dialog (spec Q3(a)):** when BOTH local and server drafts exist, three buttons — Restore Local · Restore Cloud · Discard both — each with its updated_at timestamp visible. Single-side restore drops a simpler banner.

**Auto-save cadence (spec Q4(a)):** localStorage write on every Streamlit rerun (cheap), server-side UPSERT throttled to once per 60s via `st.session_state["_draft_last_server_save::<form_id>"]`. Manual button bypasses throttle.

**Silent fallback (spec Q6(a)):** `streamlit-local-storage` import is try/except wrapped at module load. If unavailable, all four helpers run server-side only — no banner, no error to field workers.

**Sensitive fields (spec Q8):** drafts persist Worker_ID / Tank_No etc. in localStorage (OS disk encryption protects) and in `gi_database.db` (inside production hosting perimeter). No additional encryption layer added.

### Form wiring pattern (4 lines per form)

```python
state_keys = ["smr_worker_pick", "smr_job_tank", "smr_ppe_radio", "smr_ppe_reason", "_smr_cart"]
render_form_recovery_banner(form_id, user["username"], site_id, state_keys)
# … existing form body unchanged …
render_manual_save_draft_button(form_id, user["username"], site_id, state_keys, key_suffix="…")
# inside the submit-success block:
clear_form_draft(form_id, user["username"])
# at the tail of the tab:
auto_save_form_draft(form_id, user["username"], site_id, state_keys)
```

**File-uploader keys are intentionally excluded from `state_keys`** — `UploadedFile` objects can't be JSON-serialised. The staging queue rows themselves (which contain the bulk of "in-flight" data) live in `pending_issues` / `pending_receipts` with `status='draft'` and are already durable; the form draft preserves the currently-being-built row's selections so users can pick up exactly where they left off after a WS drop.

### Forms wired in 7E (3)

| Form | Module | form_id | state_keys |
|---|---|---|---|
| Supervisor → 🆕 New Request | `supervisor_portal.py` | `supervisor_request` | `smr_worker_pick`, `smr_job_tank`, `smr_ppe_radio`, `smr_ppe_reason`, `_smr_cart` |
| SK → 📋 Consumption Log | `daily_issue_log.py` | `sk_consumption` | `item_selectbox`, `tank_no_select`, `wbs_consumption_select`, `override_expiry_ck`, `cons_attach_scope` |
| SK → 📦 Receipt Staging | `daily_issue_log.py` | `sk_receipt_staging` | `rcpt_pr_link`, `rcpt_item_selectbox`, `rcpt_mtc_number`, `rcpt_attach_scope`, `rcpt_attach_dn` |

**Deferred to a Phase 7E.1 follow-up** (not in scope today): SK Return Items, HOD Cross-Site cart, SK Stock Count, HOD/Admin PR forms. The draft_bus is form-agnostic — adding more forms later is the 4-line wrapper above.

### Offline indicator — `main.py`

Pure browser-native HTML/JS rendered via `st.markdown(unsafe_allow_html=True)` right after the existing `inject_keyboard_shortcuts()` call. Top-left at `top:72px; left:22px` (mirrors the 7C corner pill in the top-right, no collision). Hidden by default; flips on `window.addEventListener('offline')`, flips back on `'online'`. No Python coupling.

### Worker prune — `whatsapp_worker.py`

New `_maybe_run_form_drafts_prune()` follows the same idiom as `_maybe_run_delivery_reminders()`. Marker key: `app_settings.form_drafts_last_prune`. Called once per 60-sec poll tick → 1 actual prune per local day. Idempotent across worker restarts.

### Tests

`bug_check.py` +16 Phase 7E checks (+8 schema column-existence). **Total now 386/386.** Coverage: schema, indices, UNIQUE enforcement, upsert insert vs update, default + custom TTL, circular-ref rejection, payload roundtrip, missing/expired hiding, delete, prune-only-expired, list_user_drafts, `requirements.txt` declaration.

The browser-side localStorage layer and the offline-pill JS can't be exercised in `bug_check.py` (no DOM) or `AppTest` (no `navigator.onLine` simulation). Manual verification via `streamlit run main.py` + browser DevTools throttling.

### Contracts (don't break in Phase 7F or later)

- `default=str` on the JSON encoder is INTENTIONAL — drafts MUST succeed for widgets carrying Decimal / datetime / similar. Tighten this and SK forms will start losing data silently.
- The `clear_form_draft` call lives inside the submit-success branch ONLY. Don't call it from the tab body unconditionally — that would wipe the user's draft on every rerun.
- `state_keys` lists must NEVER include file-uploader widget keys (`UploadedFile` is opaque). The wired forms already follow this rule; new wrappings must too.
- The 1/min server-side throttle is enforced inside `auto_save_form_draft` via `st.session_state` — bypassing it means hitting SQLite ~60× per rerun in active typing.
- `streamlit-local-storage` is an OPTIONAL dependency. The try/except at module load is the contract — don't lift the import to the top of `ui_components.py` or air-gapped deployments will crash.

---

## 2K. Tuning Round 9 (2026-06) — Workstream A.5 step 6 (FINAL): Phase 7F Role-Based User Manual PDFs

Spec Point #2 of Workstream A.5 — the last piece. Each team prints its own focused booklet from the same source markdown, no maintenance of parallel docs. Screenshots embed inline.

### Architecture — one engine, role-aware slicer + image renderer on top

`build_manual_pdf.py` keeps its existing public API and rendering for the master PDF. Three additions:

1. **`ROLE_MANUAL_RECIPES`** — dict mapping `role_key → {title, icon, audience, chapters[]}`. `chapters` is matched literally against trimmed `# N. Title` lines. `"ALL"` means full master.
2. **`slice_markdown_for_role(role_key, md_text) → str`** — pure-Python line walker. Enables/disables an `include` flag at each `# ` line based on the recipe. Deterministic; no regex magic.
3. **`build_role_manual_pdf(role_key, md_text=None) → bytes`** — slices the markdown, calls a personalised cover (`render_cover_for_role`), then runs the same two-pass TOC build the master uses. Falls through to `build_manual_pdf()` for `role_key in {"admin", unknown}`.

The existing `build_manual_pdf(md_text)` signature is unchanged — the existing Admin Settings call site still works.

### Markdown image syntax — extension to the IR

`parse_markdown` now recognises standalone lines matching `^!\[(.*?)\]\((.+?)\)\s*$` and emits `Block(kind="img", text=path, items=[alt_text])`. Inline-mid-paragraph images are intentionally ignored — only own-line images render as captioned screenshots.

**`ManualPDF.render_image(path, caption)`** — scales image to 80% of body width, max 90mm tall (keeps two images per page comfortable), uses PIL to read the intrinsic aspect ratio for distortion-free placement. Page-break guard: if the image won't fit in the remaining page, `add_page()` first. Missing file → renders a neutral grey placeholder card with `[Screenshot pending: <path>]` so the PDF never crashes on incomplete asset sets.

### Personalised covers

`ManualPDF.render_cover_for_role(recipe)` — mirrors the master `render_cover` layout (navy panel + gold accent strip + white app title block) but swaps `DOC_TITLE` for the role-specific title ("Store Keeper Manual") and surfaces the `audience` line in italic body text. The footer band shows the role title instead of the generic GI-Hub tagline so a printed booklet is identifiable at a glance.

### Recipe chapter assignments (spec Q3)

| Role | Chapters included |
|---|---|
| Store Keeper | 1, 2, 3, 4, 10, 11, 12 |
| Supervisor | 1, 3, 5, 11, 12 |
| HOD | 1, 2, 3, 6, 8, 10, 11, 12 |
| Logistics | 1, 3, 14, 16, 11 |
| Warehouse | 1, 3, 15, 16, 11 |
| Admin | ALL (full master) |

Excluded from every site-level booklet (admin-only): §13 "What Changed" release notes, §17 "Operations & Hosting" chapter.

### Screenshot library

`docs/screenshots/` (new directory). 18 placeholder PNGs generated by `scripts/generate_screenshot_placeholders.py` — 1280×720 brand-themed cards labeled with the target filename + audience hint. Replace any file with a real capture from the running app at any time; PDF builder picks it up on next render.

Seed file list (stable contract — names are referenced from USER_MANUAL.md):
- SK chapter (3): `sk_consumption_log.png`, `sk_receipt_staging.png`, `sk_supervisor_requests.png`
- Supervisor chapter (3): `supervisor_new_request.png`, `supervisor_my_requests.png`, `supervisor_intent_vs_actual.png`
- HOD chapter (3): `hod_eod_commit.png`, `hod_cross_site_inquiry.png`, `hod_employees_tab.png`
- Logistics chapter (3): `logistics_create_po.png`, `logistics_assign_warehouse.png`, `logistics_open_pos.png`
- Warehouse chapter (3): `warehouse_receive_goods.png`, `warehouse_prepare_dn.png`, `warehouse_outbound_dns.png`
- Shared / universal preamble (3): `notification_bell.png`, `live_dashboard_hero.png`, `offline_pill.png`

### Admin Portal download UX

Renamed expander from "📄 Download User Manual" → **"📥 Download Role Manuals"**. Layout:

```
📕 Master Manual — full reference, every chapter:
    [🛠️ Build Master PDF]    [⬇️ Download GI_Hub_User_Manual_v2.0_<date>.pdf]
─────────────────────────────────────────────────────────────────
📘 Role Booklets — print one per team, personalised cover + that role's chapters:
    ┌────────────────────────────────┐  ┌────────────────────────────────┐
    │ 🗝️ Store Keeper Manual         │  │ 🛡️ Supervisor Manual           │
    │ <audience>                     │  │ <audience>                     │
    │ [🛠️ Build]  [⬇️ GI_SK_…]       │  │ [🛠️ Build]  [⬇️ GI_Supervisor…]│
    └────────────────────────────────┘  └────────────────────────────────┘
    ┌────────────────────────────────┐  ┌────────────────────────────────┐
    │ 🏛️ HOD Manual                  │  │ 🚚 Logistics Portal Manual     │
    ...
```

Generation on-demand (no caching). Each Build audits via `log_audit_action(BUILD_MANUAL_PDF, role=<key>, size=<bytes>)`. File naming: `GI_<Role>_Manual_<YYYY-MM-DD>.pdf` per spec Q7. Admin-only per spec Q9.

### CLI

```bash
python build_manual_pdf.py                              # master only (unchanged)
python build_manual_pdf.py --role store_keeper          # SK booklet
python build_manual_pdf.py --role all                   # master + every role
```

### Tests

`bug_check.py` +12 Phase 7F checks. **Total now 398/398.** Coverage: recipe completeness, per-role slicer keeps own/drops others, admin recipe == master passthrough, image syntax parsed correctly, missing-file placeholder doesn't crash, role PDFs start with `%PDF-` magic, admin equals master within ±5% byte tolerance, unknown role falls back gracefully, seed placeholders exist on disk.

UI crawler unchanged (no UI page-count changes — additions live inside an existing expander).

### Contracts (don't break in any future round)

- Adding a new top-level chapter to USER_MANUAL.md → update every relevant role's `chapters` list, otherwise the chapter silently drops from that booklet. Test #2-5 catch role-specific regressions on the SK/Supervisor/HOD slices.
- Renaming an existing chapter heading → update the matching recipe entry; the slicer matches on the trimmed exact string.
- Screenshot filenames are a contract surface — referenced from USER_MANUAL.md. Renaming a placeholder PNG breaks the manual's image block silently (placeholder renders instead of the picture).
- `render_image` must continue to render the placeholder card on PIL/disk failure — site rollouts ship with incomplete screenshot sets, and an exception here would brick PDF generation entirely.
- The existing `build_manual_pdf(md_text)` signature is the master entry point — never break its signature.

---

## 2L. Workstream A.5 — COMPLETE

Six enhancement points → six phases → all shipped:

| # | Phase | Title | Test delta |
|---|---|---|---|
| 1 | 7C | HOD Cross-Site View Notifications + Indicator | +14 |
| 2 | 7F | Role-Based User Manual PDFs with Screenshots | +12 |
| 3 | 7E | Network Resilience / Form Recovery | +16 |
| 4 | 7B | Supervisor Material Request Workflow | +21 |
| 5 | 7A | Employee Site Binding | +16 |
| 6 | 7D | Automated PO Notifications with Strict Data Masking | +16 |

Net test growth across A.5: **268 → 398** (`+95 helper checks + 35 schema/column self-heals`). UI crawler grew from 16 → 17 (new Supervisor Portal page in 7B). Zero regressions. Zero edits to identity math, EOD commit, RL/BL separator, RBAC hierarchy, mailer, or any pre-existing form-submit logic.

~~**Ready for Workstream B: Docker / Deployment.**~~ — **Workstream B was redirected** to the Smart Scan AI module. See §2M below.

---

## 2M. Workstream B — Smart Scan AI (LocateAnything-3B) — COMPLETE

**Important context:** the original "Workstream B = Docker/Deployment" placeholder from the post-A.5 close-out was redirected. Workstream B is now **the Smart Scan AI sidecar** (Phases 8A–8E). Docker/Deployment, if undertaken later, will be Workstream C.

### Architecture at a glance

```
                                                    OPT-IN per site
                                                    ────────────────
[Streamlit · SK Smart Scan]                         [launchd]
        │                                              │
        │  YOLO (Tier 1+2 — unchanged)                 │ com.gi.locate-anything.plist
        │  ──────────────────────────────              │
        │  top conf ≥ 0.75 → auto-accept               │
        │  top conf in [0.30, 0.75) → top-3 picker     │
        │  else (manual mode) ───┐                     │
        │                        ▼                     ▼
        │              ai/locate_anything/client      [uvicorn 127.0.0.1:8503]
        │              ──────────────────────         │ ai/locate_anything/server
        │              gate check                     │ ───────────────────────
        │              circuit breaker (3 fails/60s)  │ POST /detect → bboxes
        │              POST /detect                   │ GET /health → status
        │              writes telemetry row ─────┐    │
        │              returns (dets, call_id)   │    │ MPS, fp16, lazy-load
        │                                        │    │ ModelNotReadyError
        │ amber "🤖 AI fallback" panel           │    │ → HTTP 503
        │   "Use this tool" / "None of these"    │    │
        │   marks accept/reject ─────────────────┘    │
        │                                             │
        ▼                                             ▼
[SQLite: locate_anything_calls]              [~/Library/Caches/gi_locate/
   id, called_at, site_id, sk_user,           LocateAnything-3B/]
   yolo_top_conf, detection_count,                  ↑
   accepted, latency_ms, error                bundled at HQ via
                                              bundle_locate_anything_weights.sh
                                              installed at site via
                                              install_locate_anything_weights.sh
```

### Files of record

| Layer | File | Purpose |
|---|---|---|
| Schema | `database.py:init_db` | Seeds `app_settings.locate_anything_enabled='0'` + `locate_anything_sidecar_url`. Self-heals `locate_anything_calls` table + 2 indices. |
| Telemetry helpers | `database.py` (Phase 8E block) | `log_locate_anything_call`, `mark_locate_anything_outcome`, `get_locate_anything_summary`, `list_recent_locate_anything_calls`. |
| Client | `ai/locate_anything/client.py` | Stdlib HTTP, gate check, circuit breaker, telemetry write. Returns `(detections, call_id)`. |
| Server | `ai/locate_anything/server.py` | FastAPI `POST /detect` + `GET /health`. Torch imports deferred to endpoint handlers — module-import-safe for tests. |
| Model | `ai/locate_anything/model_loader.py` | MPS device, fp16 fallback (bitsandbytes int8 path for future CUDA hosts), `@lru_cache(maxsize=1)`, raises `ModelNotReadyError` when bundle missing. |
| Deps | `ai/locate_anything/requirements.txt` | torch, transformers, fastapi, uvicorn, pillow, accelerate. **NOT** in project root `requirements.txt`. |
| Tier-3 logic | `ai/cv/smart_scan.py` | `should_invoke_tier3` (manual-mode-only), `tier3_to_candidates` (shape + noise filter + cap 3). |
| SK UI | `pages_internal/daily_issue_log.py` | `_get_catalogue_class_names`, `_maybe_render_tier3_branch`, `_render_tier3_panel` (amber, no auto-accept). |
| Admin UI | `pages_internal/admin_portal.py` | `_render_locate_anything_panel` — toggle + URL + health probe + 7-day rollup + recent calls. |
| Bundle scripts | `scripts/download_model.sh`, `bundle_locate_anything_weights.sh`, `install_locate_anything_weights.sh` | One-time HF download, HQ packaging, site install with SHA-256. |
| Service | `host_setup/launchd/com.gi.locate-anything.plist.tmpl`, `host_setup/scripts/run_locate_anything.sh` | launchd template + uvicorn launcher. Activated via `install.sh --with-locate-anything`. |

### Contracts (do not break)

- **Admin gate default OFF.** `app_settings.locate_anything_enabled='0'` ships in init_db. Sites get the YOLO 2-tier flow unchanged until an admin flips the toggle.
- **`client.detect()` returns `(detections, call_id)`.** Phase 8E API contract. Old single-list return was retired. Every caller must unpack the tuple. The call_id is the rowid of the telemetry write — passing 0 to `mark_locate_anything_outcome` is a no-op (gate-off / breaker-open / DB write failed).
- **Gate-off path writes NO telemetry row.** Verified by `check_8e_client_gate_off_no_telemetry`. Don't add a row here — it would spam the table with no-op events that distort the 7-day rollup.
- **Tier-3 NEVER auto-accepts.** The amber panel exists precisely because LocateAnything is a less-trusted suggestion. The "Use this tool" button is the only accept path; there is no code branch that calls `_accept_tool_pick` directly off Tier-3 candidates.
- **No torch import on the Streamlit side.** `ai/locate_anything/__init__.py` re-exports `client` symbols only. `bug_check.check_8a_client_import_does_not_pull_torch` enforces this.
- **Test isolation honoured.** `GI_SUPPRESS_LOCATE_ANYTHING=1` env var short-circuits `client.is_enabled()` regardless of DB state — used by `test_ui_crawler.py`. `bug_check` monkey-patches `client._perform_http_post` for offline mocking; the harness's `_orig_popen` is required for `bash -n` script-syntax checks.

### Operational quick-reference

```bash
# Pilot a site:
./scripts/download_model.sh                            # at HQ
./scripts/bundle_locate_anything_weights.sh            # at HQ
# transport ~/Downloads/gi_locate_bundle_*.{tar.gz,sha256} to site
./scripts/install_locate_anything_weights.sh gi_locate_bundle_*.tar.gz   # at site
./host_setup/scripts/install.sh --with-locate-anything                   # at site
# Admin Portal → Settings → "🤖 Smart Scan AI (LocateAnything)" → toggle ON, save.
```

```bash
# Take a site OUT of the pilot:
# Admin Portal → Settings → toggle OFF + save. Sidecar can keep running
# (idle, ~50 MB RAM) or stop the service:
launchctl unload ~/Library/LaunchAgents/com.gi.locate-anything.plist
```

---

## 2N. Tuning Round 12 (2026-06) — Workstream C paused: SMR-via-SK-Grid + Auto-Attribution

Workstream C (Docker / Deployment) paused for a high-value workflow tweak the operations team called for after the Smart Scan AI pilot opened. The supervisor-request approval path was bypassing the SK's own staging grid — Supervisor → SK → straight to HOD EOD queue — so SKs had no place to enrich batch numbers / lot info / final qty before the row hit the permanent ledger. Round 12 reroutes the flow through the SK Consumption staging grid and adds auto-attribution of all three roles (Supervisor / SK / HOD) directly into the ledger, retiring four manual textboxes.

### The shift, in one paragraph

**Before:** SMR Approve → `pending_issues.status='pending_hod'` → HOD EOD Commit → consumption (Issued_By = SK only, no record of supervisor or HOD on the row).

**After:** SMR Approve → `pending_issues.status='draft'` with `Requested_By=<supervisor>` + `Issued_By=<sk>` → SK enriches batch / qty in the Consumption Log grid → SK Submit Batch (negative-stock validator runs here too) → `status='pending_hod'` → HOD EOD Commit (validator runs again) → `commit_eod(hod_username=…)` → consumption row carries `Issued_By` + `Requested_By` + `"Approved By"` simultaneously, and the matching `supervisor_material_request_items.line_status` flips `active` → `committed`.

### Schema self-heal (`init_db`)

```sql
ALTER TABLE pending_issues  ADD COLUMN Requested_By TEXT;
ALTER TABLE consumption     ADD COLUMN Requested_By TEXT;
ALTER TABLE supervisor_material_request_items
            ADD COLUMN line_status TEXT DEFAULT 'active';
-- Defensive: backfill consumption."Approved By" if a legacy DB lacks it.
ALTER TABLE consumption     ADD COLUMN "Approved By" TEXT;  -- if missing
```

The legacy `Technician` column on `pending_issues` + `consumption` is **NOT dropped** — existing rows preserve historical data. It is simply removed from the SK form via `HIDDEN_FORM_COLS` and never populated again.

The legacy `"Approved By"` column (space-named, in place since launch but always NULL) is **reused** — `commit_eod` now writes the HOD username into it. No rename, no migration, zero data churn.

### Database helper changes

| Function | Change |
|---|---|
| `commit_eod(conn, *, hod_username=None)` | New kwarg. Populates legacy `"Approved By"` column on every committed row. Flips matching SMR items' `line_status='committed'` via `Source_Ref → line_id` decode. |
| `approve_supervisor_request` | Mirror writes `status='draft'` (was `'pending_hod'`) + `Requested_By=<supervisor>`. Idempotency contract preserved. |
| `withdraw_smr_line_at_staging(pending_issue_id, sk_username)` **NEW** | When SK deletes an SMR-draft row from the Consumption grid, decodes its `Source_Ref` and flips the SMR line to `line_status='withdrawn_at_staging'`. Audited as `SMR_LINE_WITHDRAWN`. Idempotent, silent on non-SMR rows. |
| `list_smr_history(site_id, *, status_in, date_from, date_to, supervisor, tank, days=None)` **NEW** | Powers the SK Supervisor Requests history expander. Filters compose AND-wise. Decided-only default (`approved` + `rejected` + `cancelled`) over last 7 days. |
| `get_pending_issues_for_site` | Surfaces `Requested_By` for the HOD EOD grid's triple-layer visibility. |

### config.py

`HIDDEN_FORM_COLS` (new): single source of truth for columns the SK forms must NOT render — `Technician`, `Issued_By`, `Approved By`, `Approved_By`, `Requested_By`, `Source_Ref`, `FEFO_Override`, `Lot_Number`. `EXTENDED_ISSUE_COLS` shrinks to drop `Issued_By` (auto-filled now).

### UI — `pages_internal/daily_issue_log.py`

- **Form column generator** for both Consumption Log + Receipt Staging now filters against `HIDDEN_FORM_COLS`. Removes the Technician + Issued_By textboxes in one move.
- **Add to Grid** explicitly sets `Issued_By = user.username` server-side at INSERT.
- **Staging queue banner** counts and surfaces "🛡️ N supervisor-requested line(s)" so the SK knows what to enrich.
- **Save Draft Edits** detects SMR rows removed from the editor and calls `withdraw_smr_line_at_staging` for each before the wipe-and-reinsert.
- **Submit Grid to HOD** runs `validate_eod_no_negative_stock` BEFORE flipping `status='draft' → 'pending_hod'` (belt + suspenders alongside HOD-side validation).
- **🛒 Supervisor Requests tab** — new `📜 Supervisor Request History` expander at the bottom with Date / Supervisor / Tank filters + Include-pending toggle. Per-row drill-down shows lines + `line_status` chips.

### UI — `pages_internal/hod_portal.py` (EOD Commit tab)

Triple-layer SMR visibility:

1. **Banner above the grid** — counts and lists distinct supervisors of origin.
2. **New "Requested By" column** in the EOD HTML table (blank for SK-direct rows).
3. **🛡️ glyph badge** prepended to the Material cell for SMR-sourced rows.

The Confirm Commit button passes `user["username"]` into `commit_eod(hod_username=…)` so `"Approved By"` lands on every row.

### Tests

`bug_check.py` — Phase 7B's three SMR contract tests updated to assert the new `status='draft'` + `Requested_By` contract. **+10 Round 12 checks added** covering: Requested_By column on both ledger tables, line_status column + default, withdraw_smr_line_at_staging, commit_eod writes `"Approved By"`, commit_eod flips `line_status='committed'`, commit_eod carries Requested_By to consumption, `HIDDEN_FORM_COLS` content, `list_smr_history` filters + decided-only default, full three-role attribution end-to-end. **Total now 450/450.** UI crawler unchanged at **17/17** (no new pages).

### Contracts (don't break in Round 13+)

- **Phase 7B contract relaxed:** "approve mirrors to `pending_issues` with `status='pending_hod'`" → now `status='draft'`. The SK Submit Batch step is the new gate to `pending_hod`. Negative-stock validator runs at BOTH gates.
- `approve_supervisor_request` remains idempotent — second-call refusal is the click-spam debit guard.
- `commit_eod` is back-compat: callers passing no `hod_username` skip the `"Approved By"` write (the column stays NULL for those rows). All app-side callers pass it.
- `Source_Ref` shape `SMR:{request_no}:{line_id}` unchanged — the line_id decoder in `withdraw_smr_line_at_staging` + `commit_eod` depends on it. Don't trim, don't reformat.
- `HIDDEN_FORM_COLS` is the contract for "auto-filled / retired" columns. Adding a new auto-attributed column → put its name here so it stops appearing in forms.
- `Technician` column lives on in the schema. Don't drop it — legacy rows carry data; SQLite ALTER DROP is risky on hosted instances.
- `line_status` in `('active', 'withdrawn_at_staging', 'committed')`. Adding a 4th value → update `list_smr_history` callers + the test fixtures.

---

## 2O. Tuning Round 13 (2026-06) — EOD State Unification + Schema Cleanup

Two production bugs surfaced after Round 12 deployment: (a) per-row ✓ approvals on the HOD EOD tab were stranding rows — they vanished from the UI yet never reached the consumption ledger, and (b) the Admin Master DB Editor "Export as PDF" on the consumption table was leaking junk columns into operations PDFs (a bogus parsing-artifact column named `Approved` with type `By TEXT`, the retired `Technician` column, internal-only `Source_Ref` / `FEFO_Override` / `status`, etc.).

Both fixed in this round, with two additional polish wins folded in: a `↩️ Unapprove` action on per-row approvals so HODs can change their mind, and an archive table for rejected rows so `pending_issues` stays lean while the audit trail is preserved.

### Root causes

**Bug 1 — three filters disagreed on what `commit_eod` should commit.** `hod_approve_pending_issue` (per-row ✓) and `hod_approve_all_pending_issues` (bulk Approve All) both wrote `status='approved'`, but `commit_eod` and `get_pending_issues_for_site` only matched `status='pending_hod'`. So a `✓`-clicked row dropped out of the HOD UI (filter) and never reached `consumption` (commit filter).

**Bug 2 — two legacy column landmines on the consumption table.** Old code at some point ran `ALTER TABLE consumption ADD COLUMN Approved By TEXT` *without quotes*; SQLite parsed `Approved` as the column name and `By TEXT` as the type. The column has always been NULL — but it was still in `SELECT *` dumps. The Admin export ran `pd.read_sql(f"SELECT * FROM {selected_table}", conn)` straight into `generate_universal_pdf`, so the PDF carried both `Approved` (bogus) and `Approved By` (Round 12's proper column), plus the retired `Technician` field and several internal-only columns.

### Schema self-heal (`init_db`)

1. **DROP the bogus `Approved` column** on `consumption`. Pre-flight `SELECT COUNT(*) … WHERE "Approved" IS NOT NULL` confirms it's NULL-only (it always has been); then `ALTER TABLE consumption DROP COLUMN "Approved"`. Wrapped in a `try/except sqlite3.OperationalError` so older SQLite runtimes (< 3.35) silently skip the DROP — the canonical export list hides it from PDFs regardless.
2. **`rejected_issues_archive`** new table — `original_id` + mirror of `pending_issues` business columns + `rejected_by` / `rejected_at` / `reject_reason`. Two indexes: `(Site_ID, rejected_at)` for per-site audits, `(Source_Ref)` for SMR back-resolution. The init script also forward-mirrors any new `pending_issues` column onto the archive automatically.
3. **`supervisor_material_request_items.line_status`** gains a 4th value `'rejected_at_hod'` (semantics only — no enum constraint to migrate). Set by `hod_reject_pending_issue` when the source row was SMR-sourced. Distinct from `'withdrawn_at_staging'` (SK-side drop) so the supervisor's intent ledger reflects the actual lifecycle without forcing a join through the archive.

### Database helper changes (`database.py`)

| Change | Notes |
|---|---|
| New module constant `_EOD_COMMIT_STATUSES = ('pending_hod', 'approved', 'flagged')` + a SQL predicate alias `_EOD_PI_STATUS_PRED` | Single source of truth — every filter that asks "is this row eligible to commit?" reuses it. |
| `commit_eod` SELECT + DELETE filters reuse the new predicate | Per-row ✓ rows (`approved`) and flagged rows now reach consumption. `rejected` is excluded — those rows live only in the archive. |
| `get_pending_issues_for_site` filter widened to the same set | Approved rows stay visible in the HOD EOD grid until commit, so `↩️ Unapprove` is reachable. |
| `hod_reject_pending_issue(issue_id, *, rejected_by=None, reason=None)` refactored | Copy-then-delete to `rejected_issues_archive` with metadata. Detects SMR `Source_Ref` and flips the matching SMR line to `line_status='rejected_at_hod'`. Audited as `REJECT_PENDING_ISSUE`. Idempotent on already-archived rows. |
| **NEW `hod_unapprove_pending_issue(issue_id)`** | Flips `status='approved' → 'pending_hod'`. No-op on rows that aren't currently approved. |
| `hod_approve_all_pending_issues` unchanged | Still bulk-sets `'approved'`; the widened `commit_eod` filter is what makes it actually reach the ledger now. |

### config.py

`CONSUMPTION_EXPORT_COLS` (new) — canonical `(db_col, display_label)` list for the Admin DB Editor's PDF export on the consumption table. 17 entries: Date, SAP Code, Material Code (joined), Material (joined), UOM (joined), Quantity, Work Type, PR Number, Tank No, Serial No, Lot Number, Issued By, Issued To, Requested By, Approved By, Remarks, Site. **Excludes** Technician, `Approved` (bogus), `status`, `Source_Ref`, `FEFO_Override`.

### UI — `pages_internal/admin_portal.py`

`_render_master_db_editor_tab` — when `selected_table == "consumption"`, the Export-as-PDF button now joins inventory for Material_Code / Equipment_Description / UOM, projects against `CONSUMPTION_EXPORT_COLS`, renames to the display labels in the canonical order, and feeds the cleaned dataframe to `generate_universal_pdf`. **All other tables continue to export `SELECT *`** — only consumption was buggy. The editable in-page `st.data_editor` grid still loads `SELECT *` because admins legitimately need the raw view to fix bad rows.

### UI — `pages_internal/hod_portal.py`

- **Actionable filter widened** from `["pending", "flagged"]` to `["pending", "flagged", "approved"]` so approved rows surface in the per-row action list.
- **Per-row buttons branch on state:** approved rows show **↩️ Unapprove**; pending / flagged rows show the existing **✓ Approve**. Both states share the **✗ Reject** button.
- **Reject is now reason-mandatory** via a popover (`text_input` + Confirm reject button, disabled until a reason is typed). The reason and HOD username pass through to `hod_reject_pending_issue(rejected_by=…, reason=…)`.
- **New green banner** above the grid: `"✅ N row(s) already approved — they will commit to the master ledger on the next 📤 Commit EOD click."` Renders only when N > 0.
- **Bulk Commit dialog DELETE widened** to match `_EOD_COMMIT_STATUSES` so the re-insert → `commit_eod` chain clears every commit-eligible status from `pending_issues`.

### Tests (`bug_check.py`)

**+10 Round 13 checks, target 460/460 — achieved.** Coverage:

1. `commit_eod` commits status='approved' rows (and writes `"Approved By"`).
2. `commit_eod` commits status='flagged' rows.
3. `commit_eod` skips status='rejected' rows (and doesn't delete them either).
4. `get_pending_issues_for_site` returns rows in all three commit-eligible statuses.
5. `hod_reject_pending_issue` copies to `rejected_issues_archive` with metadata + deletes the source.
6. `hod_unapprove_pending_issue` flips `approved → pending_hod`; idempotent no-op otherwise.
7. Bogus `Approved` column gone from consumption post-`init_db`; proper `"Approved By"` retained.
8. `rejected_issues_archive` table has the required column set + indexes.
9. `CONSUMPTION_EXPORT_COLS` content: includes the canonical business columns, excludes the legacy/internal ones.
10. SMR-sourced row rejected at HOD review → `line_status='rejected_at_hod'`, and the archive carries the original `Source_Ref` so intent-vs-actual reports can still resolve the line.

UI crawler **17/17** still green.

### Contracts (don't break in Round 14+)

- `_EOD_COMMIT_STATUSES` is the canonical "commit-eligible" set. New status values must NOT be added to `pending_issues` without deciding whether they belong here. **`'rejected'` is intentionally absent**.
- `hod_reject_pending_issue` is the **only** path that should remove a row from `pending_issues` to a terminal state. Direct UPDATE-to-rejected is treated as an inert legacy pattern (commit_eod ignores it, but no archive row exists for audit).
- `line_status ∈ ('active', 'withdrawn_at_staging', 'committed', 'rejected_at_hod')`. SK-side withdraw and HOD-side reject are **distinct** terminal states — never collapse them into one value.
- `CONSUMPTION_EXPORT_COLS` is the single source of truth for the canonical consumption export. When you add a new business-meaningful column to `consumption`, also add it here, otherwise the PDF will silently drop it.
- The DROP of the bogus `Approved` column is gated by a NULL-only safety probe. If a non-NULL value ever appears in a fresh DB upgrade (it shouldn't — the column was always unused), the DROP is skipped and a manual review is required.

### Operational note for live deployments

Before the first init_db that includes this round runs against a long-lived production DB, take a one-line snapshot:

```bash
cp gi_database.db gi_database.preR13_$(date +%Y%m%d).db
```

The DROP COLUMN is irreversible. The pre-flight check makes data loss impossible, but the snapshot makes any "wait, what happened" investigation trivial.

---

## 2P. Tuning Round 14 (2026-06) — Vision OCR Image-Pipeline Hardening

Store Keeper OCR was failing in the field on every iPhone smartphone upload. Two failure modes were intertwined:

- `Ollama vision request failed: timed out` — the 120-second urllib timeout couldn't cover **cold start of `qwen2.5vl:7b` (30–90 s) + multi-megabyte JPEG upload + inference + JSON output**.
- `Ollama vision request failed: HTTP Error 500: Internal Server Error` — Ollama's vision preprocessor blew up on 12-megapixel inputs (a 4032×3024 JPEG ≈ 3–6 MB).

Underneath both, a third silent killer: **iPhone "Share as JPG" shares often deliver the raw HEIC file with a `.JPG` extension**. The browser hands Streamlit a HEIC byte stream; PIL throws `UnidentifiedImageError`; the OCR call path surfaces a generic "unsupported format" with no hint about what went wrong.

Round 14 fixes all three at one boundary.

### New module — `ai/image_utils.py`

`prep_image_for_vision(raw_bytes, *, max_dim=1600, quality=85) -> bytes`

Five operations, in order:

1. **Decode** via PIL with pillow-heif's opener pre-registered (idempotent registration on first call). Magic-byte sniff (`ftyp{heic|heix|heif|mif1|msf1|hevc}`) backs a targeted error message when HEIC bytes arrive at a server without pillow-heif installed.
2. **EXIF auto-orient** via `ImageOps.exif_transpose` — the iPhone portrait that arrives as `800×600 + orientation=6` comes out `600×800`, displayed the way the user shot it.
3. **RGB convert** — strips alpha / palette modes; JPEG re-encode is then safe.
4. **Long-edge cap** at 1600 px via `Image.thumbnail` (no-op if the input is already smaller). `qwen2.5vl`'s internal tile preprocessor caps in this range — larger uploads were wasted bandwidth.
5. **JPEG re-encode** at `quality=85, optimize=True` — sharp enough for handwritten digits, ~5–10× smaller than the raw camera roll.

Failures map to one typed exception, `ImagePrepError`, so OCR callers (`ai/ocr.py`) never have to catch a PIL internal.

### `ai/client.py` — `ollama_vision_generate`

| Param | Before | After |
|---|---|---|
| `timeout_s` | `120` | **`240`** (cold-start headroom) |
| `keep_alive` | not sent | **`"30m"`** (subsequent uploads skip cold-load) |

`keep_alive` is forwarded into Ollama's `/api/generate` payload alongside the existing `stream=False`, `options`, and `system` fields.

### `ai/ocr.py`

Both `extract_consumption_from_image` and `extract_delivery_note_from_image` now pipe `image_bytes` through `prep_image_for_vision` **before** base64 encoding. `ImagePrepError` is caught and surfaced as `ok=False` with the user-facing message — same contract the existing failure paths already use.

### `pages_internal/daily_issue_log.py`

Both file_uploader widgets (`cons_ocr_img`, `rcpt_ocr_img`) now accept `.heic` and `.heif` extensions alongside the existing `png / jpg / jpeg / webp`. Without these extensions Streamlit's browser-side filter rejects HEIC uploads before the prep step can even run.

### `requirements.txt`

Added `pillow-heif>=0.16`. Wheels ship for macOS (arm64 + x86_64) and manylinux; `libheif` bundled. On air-gapped sites without the wheel the module degrades gracefully — JPEG/PNG uploads keep working; HEIC inputs surface the targeted "share as JPEG instead" error.

### Tests — `bug_check.py`

**+5 Round 14 checks, target 465/465 — achieved.** All synthesised in-memory via PIL so they never touch a real photo on disk:

1. 4032×3024 input comes back with long edge ≤ 1600 px, aspect ratio preserved within 1 px.
2. Grayscale source returns RGB JPEG.
3. High-quality 12-MP synthetic shrinks by at least 2× through prep.
4. EXIF orientation 6 → `width/height` swap (display-correct).
5. Corrupt bytes raise `ImagePrepError`, not a raw PIL exception.

UI crawler stays at **17/17** (no new pages, just uploader-filter widening).

### Contracts (don't break in Round 15+)

- `prep_image_for_vision` is the **only** boundary that converts user-uploaded bytes to "ready-for-vision" bytes. New OCR callers must route through it; don't base64-encode raw `file_uploader().getvalue()` directly.
- The `max_dim=1600` default is calibrated to qwen2.5vl's internal preprocessor. Bumping it past ~2048 reintroduces the HTTP 500 risk; below ~1000 hurts OCR accuracy on dense handwritten sheets.
- `ImagePrepError` is the only exception `prep_image_for_vision` raises. Catching it gives callers a user-facing message; catching a broader `Exception` would mask bugs.
- `keep_alive='30m'` keeps the model resident across uploads. Sites tight on RAM can set this to `"0"` per call, but the default stays generous because cold-start was the primary failure mode in the field.
- The HEIC opener registers itself on first prep call (module-level state). Do not call `pillow_heif.register_heif_opener()` at module import — air-gapped sites without the wheel must still be able to import `ai.image_utils`.

### Operational note

After deploying this round, the first iPhone upload to a freshly-restarted host will still take ~30–60 s (model cold-load). The second upload within 30 minutes is the one that feels fast (~5–15 s). Communicate this expectation to SK rollout users.

---

## 2Q. Tuning Round 15 (2026-06) — Multi-Portal Polish + Material Master + PO Parser Fix

Six surfaces touched in one coordinated round: Admin Live Dashboard polish, a new Material Master entry tab in Logistics, the PO PDF parser regression, the Warehouse Prepare-DN site lock, the HOD DN-visibility fallback + reschedule routing, and a confirm helper for destructive paths.

### Findings recap (root causes worth keeping)

| Issue | Root cause |
|---|---|
| Per-row HOD ✓ approval stranding rows | (Already fixed in Round 13) — `commit_eod` filter widened to `_EOD_COMMIT_STATUSES`. |
| PO PDF "no line items" | The GI sample PDF (e.g. `PO#4710003114.pdf`) lays each item on **two** lines: `GI-NNNNNNN` on its own, then `<srno> <desc> <qty> <uom> <unit> <vat> <total>` on the next. The old regex assumed code+srno were paired. |
| HOD DN Approvals empty after Logistics approves | DN's `Site_ID` historically came from the Warehouse Prepare-DN dropdown, which let the operator pick any site. Mismatch → HOD never saw the row. |
| HOD reschedule always notified Logistics | `request_reschedule` had one notification target. Even for post-receive-at-warehouse reschedules, where Warehouse alone could swap the date. |
| PR PDF report missing UoM | `_strip_empty_columns` dropped UOM whenever a legacy PR batch had it blank. |

### Schema self-heal (`init_db`)

```sql
CREATE TABLE IF NOT EXISTS inventory_site_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    SAP_Code     TEXT NOT NULL,
    Site_ID      TEXT NOT NULL,
    Minimum_Qty  REAL NOT NULL,
    updated_by   TEXT,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(SAP_Code, Site_ID)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_material_code
  ON inventory(Material_Code)
  WHERE Material_Code IS NOT NULL AND Material_Code <> '';
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('temp_material_seq', '0');
```

The partial UNIQUE index permits legacy NULL-Material_Code rows but blocks any future duplicate. The `temp_material_seq` counter persists the `Temp-GI-NNNNNNN` sequence across restarts.

### `database.py` — new helpers

| Helper | Purpose |
|---|---|
| `next_sap_code()` | `MAX(numeric tail) + 1` across `inventory.SAP_Code`, formatted `GI-NNNNNNN`. |
| `next_temp_material_code()` | Atomic increment of `app_settings.temp_material_seq`, formatted `Temp-GI-NNNNNNN`. |
| `bulk_upsert_materials(rows, *, created_by, overwrite_duplicates=False)` | Single-transaction upsert. Detects duplicate Material_Codes (within the batch AND against existing rows), auto-assigns SAP + Temp codes, returns `{inserted, updated, rejected}`. Audited as `MATERIAL_BULK_UPSERT`. |
| `set_site_min_qty(sap, site, qty, *, updated_by)` | Upsert into the override table; negative qty deletes the override (falls back to global default). |
| `get_min_qty_for(sap, site)` | `COALESCE(override, inventory.Minimum_Qty, 0)` lookup. |

### `database.py` — behavioural changes

- **`process_po_pdf` rewrite.** Items extractor walks the text line-by-line and recognises **three layouts**: code-on-own-line + 7-column row (the failing GI sample); single-line 6-column row (original sample); legacy `<srno> <code>` + desc-on-next-line. Header `PR_Number` / `PO_Number` regexes were tightened to allow the `Purch. Order.` variant.
- **`list_pending_hod_dns` 3-way OR-join.** A DN surfaces for an HOD when ANY of (`delivery_notes.Site_ID`, `purchase_orders.Site_ID`, `pr_master.Site_ID`) matches the HOD's site. Legacy DNs created with `Site_ID='HQ'` now reach the right HOD.
- **`request_reschedule` routing.** A new module constant `_RESCHEDULE_WAREHOUSE_DIRECT_STATUSES = ('pending_logistics', 'logistics_approved', 'pending_hod', 'hod_approved', 'pending_sk')` defines when the goods are physically with the Warehouse. DN-attached reschedules in those states notify `warehouse_user` at the receiving warehouse; everything else (PO-level, or DN already `received`) still notifies Logistics. Audit + success message reflect the route taken.

### `config.py`

Nothing changed in Round 15 — the existing `HIDDEN_FORM_COLS` already retired Technician. The Round 13 `CONSUMPTION_EXPORT_COLS` contract is preserved (a Round 15 test pins it).

### Logistics Portal

- **NEW 9-tab layout** — `📦 Material Details` inserted at position 8 (right before History).
  - **Manual entry sub-tab**: 5 fields (Material_Code, Material_Description, UoM, Category, Minimum_Qty). Blank Material_Code triggers Temp-GI auto-assignment; SAP code always auto-generates.
  - **Excel upload sub-tab**: case-insensitive header map onto canonical names, parsed preview, `Overwrite existing` toggle, inserted/updated/rejected metrics + rejection table.
  - **Current register sub-tab**: read-only inventory grid in the spec column order (Material Code → Material Description → UoM → Category → Min Qty → SAP Code), Temp-GI codes sorted to the bottom.
- **Create PO → PR dropdown.** Replaced the free-text input with a `selectbox` over open PRs (`pr_master.status='open'`). Site_ID auto-fills from the picked PR. A `➕ Add unlisted PR` expander remains for out-of-band entries.

### Warehouse Portal

- **Prepare DN destination site locked** to the PO's originating `Site_ID` (resolved via `get_po_detail`). Admin shadow gets a small "Override destination" expander for legitimate cross-site shipments. Falls back to the full dropdown only when the PO has no Site_ID at all.

### HOD Portal

- **DN Approvals visibility** — the 3-way join above lets legacy DNs land in the right HOD's queue (no code change to the tab itself).
- **In-Transit `🔁 Request reschedule`** — two-step confirmation. First click → confirmation banner showing the route ("Warehouse" vs "Logistics") + new date + reason summary. Second click → fires `request_reschedule`. Cancel keeps the popover open.
- **`✅ Approve All Pending`** — now uses the shared `render_confirm` helper. The disabled state still applies when nothing is pending.

### SK Portal (daily_issue_log.py)

- **`📨 Submit Grid to HOD`** — now requires explicit confirmation after the negative-stock validator passes. Attachments + WhatsApp + form-draft clear all happen inside the Yes branch, so a Cancel keeps the grid intact.

### Admin Portal — Live Dashboard

- **KPI click-through.** A row of 4 secondary buttons under the hero strip toggles inline drill-down panels for: all catalogue items, top-value items by Stock_Value (with concentration share), below-minimum items, expiring/expired lots. Session-state-driven so the panel survives reruns; click the same button again to collapse.
- **Filter polish.** A single prominent **"🔎 Search across SAP, Material Code, Description, Category"** input replaces the old narrow per-column strip on landscape/mobile. The per-column strip is preserved inside an "Advanced — filter per column" expander for power users.

### `ui_components.render_confirm`

New helper. Two-step destructive-action gate driven by `st.session_state` and a stable key prefix. Returns `True` only when the user clicks Confirm.

```python
if render_confirm(
    "_hod_eod_approve_all",
    action_label=f"✅ Approve All Pending ({pend_n})",
    body="This will move N pending row(s) to approved …",
    confirm_label="✅ Yes — approve all pending",
):
    hod_approve_all_pending_issues(site_id=site_id)
```

Future destructive paths can adopt this incrementally (Logistics Force-Close, HOD Reject DN, etc.) — out of scope this round to keep blast radius small.

### Reports

- `_ALWAYS_KEEP` (in `reports_page._strip_empty_columns`) now includes `UOM`. The PR Status report — and every other UOM-bearing report — keeps the column even when partial rows have it blank.

### Tests (`bug_check.py`)

**+15 Round 15 checks · 480/480.** Coverage:

1. `inventory_site_overrides` schema + UNIQUE(SAP, Site) enforcement.
2. `next_sap_code` increments from max numeric tail.
3. `next_temp_material_code` persists + increments.
4. `bulk_upsert_materials` inserts with auto SAP + Temp-GI for blank Material_Code.
5. `bulk_upsert_materials` rejects duplicate Material_Code.
6. `bulk_upsert_materials` overwrite path updates in place.
7. `set_site_min_qty` + `get_min_qty_for` round-trip + COALESCE override.
8. `inventory.Material_Code` UNIQUE partial index rejects duplicate raw INSERTs.
9. `process_po_pdf` extracts 3 items from `PO#4710003114.pdf` (regression guard).
10. `process_po_pdf` two-line synthetic fixture (parses even on machines without the real PDF on disk).
11. `list_pending_hod_dns` 3-way OR-join surfaces DNs with mismatched Site_ID.
12. `request_reschedule` routes to warehouse_user when DN status ∈ post-receive set.
13. `request_reschedule` routes to logistics for PO-level (no DN) — back-compat.
14. Round 13 `CONSUMPTION_EXPORT_COLS` contract unchanged.
15. `_ALWAYS_KEEP` includes UOM for PR report.

UI crawler stays at **17/17** — the new Material Details tab lives inside the existing Logistics Portal page, no new top-level routes.

### Contracts (don't break in Round 16+)

- `inventory.Material_Code` is now UNIQUE (when non-NULL). Any new ingestion path **must** go through `bulk_upsert_materials` or honour the constraint manually.
- `next_sap_code` derives the next SAP from the MAX numeric tail across the whole `inventory` table. Don't introduce non-numeric tails (e.g. `GI-7003A18`) without updating the helper's `int(...)` parse, or one bad row will reset the counter.
- `Temp-GI-NNNNNNN` and `GI-NNNNNNN` share format width (7-digit tail). The Temp prefix is the only thing that distinguishes the two; downstream code that wants to count "real" SAP codes should filter on `NOT LIKE 'Temp-%'`.
- `_RESCHEDULE_WAREHOUSE_DIRECT_STATUSES` is the contract for "Warehouse owns this DN now". Adding a new DN status without deciding which side it lives on will silently misroute reschedule notifications.
- `list_pending_hod_dns` 3-way OR-join intentionally surfaces DNs where ANY Site_ID matches. The DN visibility bug came from an inflexible AND-style filter; don't tighten this back without re-introducing the legacy-row blind spot.
- `render_confirm` keys are stable session-state strings. Don't generate them with `uuid` or timestamps — the helper toggles state across reruns and a fresh key every render would break the two-step flow.
- The Logistics Material Details tab UI does NOT touch site overrides — that's the HOD's job from the HOD Portal. Round 15 ships the helpers; the HOD-side UI is left for a follow-up sprint (`set_site_min_qty` is callable from a small admin grid whenever you want to ship it).

### Operational note

- Existing inventory rows are NOT migrated. New uploads through the Material Details tab populate Material_Code + SAP_Code; legacy rows with blank Material_Code remain (the partial UNIQUE index ignores them).
- The `Temp-GI-NNNNNNN` counter starts at 1. To inspect or reset it manually: `SELECT value FROM app_settings WHERE key='temp_material_seq'`. Never decrement after live use — temp codes are referenced from POs and audit trails.

---

## 2R. Tuning Round 16 (2026-06) — DN Routing Simplification + PR PDF Polish

Two field-driven changes shipped together:

1. **HOD DN Approvals tab was empty.** Cause: legacy DNs sitting at `pending_logistics` after the Round 15 schema fix, and the entire Logistics approval step adding friction without business value.
2. **PR PDF needed PO traceability + UoM.** The Notify-Logistics download was named `…_Record.pdf` and missing two columns the recipients (vendors, finance) needed to act on it.

### DN state machine — before vs. after

```
BEFORE (Rounds 1–15):
  draft → pending_logistics → pending_hod → pending_sk → received
                      ▲              ▲
                Warehouse         Logistics
                submits           approves

AFTER (Round 16):
  draft → pending_hod → pending_sk → received
                ▲
        Warehouse submits (Logistics gets an info notification only)
```

`pending_logistics` and `logistics_approved` remain in the CHECK constraint (back-compat for restore-from-backup), but no code path writes them. `logistics_decide_dn` is retained as a safety net — marked deprecated in its docstring — to drain any leftover legacy row.

### Schema migration (idempotent, in `init_db`)

```sql
UPDATE delivery_notes
SET status = 'pending_hod',
    logistics_decided_at = COALESCE(logistics_decided_at, CURRENT_TIMESTAMP),
    logistics_decided_by = COALESCE(logistics_decided_by, 'system_r16_migration'),
    logistics_decision   = COALESCE(logistics_decision, 'auto')
WHERE status IN ('pending_logistics', 'logistics_approved');
```

Wrapped in try/except for `OperationalError` so a brand-new DB (where `delivery_notes` may not yet exist at the migration line) is safe. Logs a `DN_LEGACY_MIGRATION_R16` row to `system_audit_log` when N > 0.

### `database.py` — behaviour changes

| Function | Change |
|---|---|
| `submit_dn_for_logistics(dn, wh_user)` | Now writes `status='pending_hod'` (was `pending_logistics`). Fans out TWO notifications: **HOD** (actionable, scoped to destination Site_ID) + **Logistics** ("info only, no action required"). Function name kept so the Warehouse Portal caller works unchanged. |
| `logistics_decide_dn(...)` | Docstring tagged DEPRECATED. Body unchanged — used only as a safety net for legacy rows the migration didn't catch (impossible in normal operation post-Round-16). |
| `get_pr_with_po_numbers(pr_number)` **NEW** | Returns `{pr_line_id: 'PO-001, PO-002, …'}` map for the PR PDF. Joins `pr_master ↔ purchase_orders ↔ po_items` by Material_Code (per the schema's documented "no SAP_Code on po_items" contract). |

### `reports.py` — `generate_pr_pdf` extension

Two new columns added:
- **PO #** (25 mm) — fed from the `po_map` kwarg; cells truncate to 14 chars + `…` so multi-PO PRs never overflow the column width.
- **UoM** (12 mm) — read directly from `pr_master.UOM` (already projected by the HOD PR query).

Column widths rebalanced to 25+55+25+12+18+18+18+19 = 190 mm (A4 portrait inside the existing margins). Row font tightened from 9 → 8 pt to fit the new layout.

**Signature**: `generate_pr_pdf(..., *, po_map: dict[int, str] | None = None)` — keyword-only, default `None` keeps existing callers working with the PO column rendering blank.

### `pages_internal/hod_portal.py`

- Filename: `PR_{n}_{site}_Record.pdf` → **`PR_{n}_{site}_Status.pdf`**.
- Before the `generate_pr_pdf` call, `get_pr_with_po_numbers(pr_to_email)` resolves the per-line PO map and passes it through.

### Tests (`bug_check.py`)

**+5 Round 16 checks · 485/485.** Coverage:

1. `submit_dn_for_logistics` writes `status='pending_hod'`.
2. `submit_dn_for_logistics` queues both an HOD-targeted (with site scope) and a Logistics-info notification.
3. Legacy `pending_logistics` + `logistics_approved` DNs migrate to `pending_hod` on `init_db`; second call is a no-op.
4. `get_pr_with_po_numbers` comma-joins POs per PR line (sorted, deduped).
5. `generate_pr_pdf` produces a valid PDF byte stream with the new kwarg; back-compat path (no `po_map`) also works.

**Two pre-existing tests updated for the new contract** (NOT new tests):
- `check_full_dn_flow_to_sk_receipt` — removed the `logistics_decide_dn` call from the happy-path. Warehouse now submits straight to HOD.
- `check_in_transit_dns_for_site_isolation_and_order` — was asserting `pending_sk` sorts before `pending_logistics`; now asserts it sorts before `pending_hod` (the new resting state for newly-submitted DNs).

UI crawler stays at **17/17**.

### Contracts (don't break in Round 17+)

- The DN state machine still permits `pending_logistics` and `logistics_approved` in the CHECK constraint — keep them there. Removing those values from the constraint would break restore-from-backup of any DB snapshotted before Round 16.
- `submit_dn_for_logistics` is now a misnomer. **Do not rename it** without updating the Warehouse Portal caller in lockstep; pick this up only if the cost of staring at the name daily exceeds the rename risk. The docstring documents the new behaviour clearly.
- `get_pr_with_po_numbers` joins on `Material_Code` (not `SAP_Code`) because `po_items` deliberately lacks `SAP_Code`. New code introducing a SAP-Code-on-PO column should update this helper to prefer SAP when available.
- The PR PDF filename is now `…_Status.pdf`. Any external tooling that grepped for `…_Record.pdf` needs the new pattern.
- The PR PDF `po_map` kwarg is keyword-only. Don't promote it to a positional parameter — back-compat callers rely on signature stability.

### Operational note

After deploying Round 16, the first `init_db` call on a live DB will silently flip any in-flight `pending_logistics` DNs into the HOD's queue. Run the migration during a low-traffic window and notify the HODs that their DN Approvals tab will fill on the next refresh.

---

## 2S. Tuning Round 17 (2026-06) — Smart Material Estimator (SME) merge

The standalone Smart Material Estimator project (8.5k LOC Streamlit app for Rubber Lining / Brick Lining planning) is now a first-class portal inside the ERP. The merge follows two binding architectural directives: (a) ERP is the single source of truth — SME no longer holds its own stock/consumption/receipts ledgers; (b) SME is a read-only projection — the EOD commit pipeline remains the only path that writes to `consumption` / `receipts` / `returns`.

### Schema additions — `database.py:init_db()`

Three new tables, all additive, all under the existing self-heal idiom:

- **`sme_equipment`** — equipment master with `Site_ID`, `Equipment_Tag_No`, `Lining_System_Code`, `Surface_Area_SQM`, plus display fields (Name, Location, Type, Substrate). `UNIQUE(Site_ID, Equipment_Tag_No, Lining_System_Code)` — a tag can appear once per lining system per site.
- **`sme_recipe`** — lining-system recipe master (global, not site-scoped). `UNIQUE(Lining_System_Code, Material_Code)`. Carries `For_1_SQM` and `Nature`.
- **`sme_sqm_progress`** — per-(site × tag × system) build progress. PK is the composite triple; `Original_SQM` is loaded from the bootstrap, `Done_SQM` is preserved across re-loads.

Plus seed values for two new `system_settings` categories — `sme_location` (Brown Field / TRAIN J / TRAIN K) and `sme_equipment_type` (Vessel / Tank / Column / Pipe / Reactor). **Per Correction #1, no new `sme_locations` / `sme_types` tables** — these dropdowns ride on the existing per-site `system_settings` infrastructure that already powers `Work_Type` and `Tank_No`. Helpers `get_sme_locations()` / `get_sme_equipment_types()` mirror the `get_work_types()` / `get_tank_nos()` pattern, with the same site-then-global fallback.

### Ledger-bridging helpers — `database.py`

- **`get_on_order_by_material(site_id, conn)`** — per-`Material_Code` open-PO outstanding quantity. Sum is `Qty − Delivered_Qty − Returned_Qty` clamped at 0, across POs whose `status` is `open` or `partially_delivered` AND whose `line_status` is not `closed` / `force_closed`. Site-scoped or global.
- **`get_sme_inventory_view(site_id, conn)`** — bridges ERP ledger to SME engine schema. Calls `load_live_inventory()` for `Available_Qty` (computed: `Opening + Σ Receipts − Σ Consumption − Σ Returns`), then left-joins `get_on_order_by_material()` for `Ordered_Qty`. **`Available_Qty` is NEVER stored on `inventory`** — this was the critical impedance mismatch with the legacy SME schema and is the central contract of the merge. Groups by `Material_Code` so the engine sees one row per material regardless of SAP_Code splits.
- `get_sme_equipment` / `get_sme_recipe` / `get_sme_sqm_progress` — site-scoped read accessors shaped for the engine's column contract (note the literal `Equipment_Tag_No.` column name with the trailing dot — `allocation_engine.py:57` joins on that exact label).
- `add_sme_setting` / `delete_sme_setting` / `upsert_sme_sqm_progress` — narrow write helpers used by the Master Data tab and the bootstrap. All refuse cross-category writes (`Work_Type` / `Tank_No` are guarded).

### Bootstrap — `scripts/sme_bootstrap.py`

One-shot loader for SME master data:

```bash
python3 scripts/sme_bootstrap.py --site-id HQ           # wet run
python3 scripts/sme_bootstrap.py --site-id HQ --dry-run # parse only
python3 scripts/sme_bootstrap.py --site-id HQ --db /tmp/other.db
```

Reads `Equipment.xlsx` + `For_1_SQM.xlsx` from `scripts/sme_seed_data/`. Cleaners are inlined (port of SME's `validate_data.clean_recipe` / `clean_equipment`) so the ERP doesn't depend on the legacy SME project directory. Idempotent: equipment rows for the target `Site_ID` are deleted then re-inserted; recipes are global and fully reloaded; progress uses `upsert_sme_sqm_progress` which preserves `Done_SQM` across re-loads.

### Portal — `pages_internal/material_estimator/` (14-file package)

The `_EXACT_ROLE_PAGES` lock in `main.py` adds `"🧪 Material Estimator": {"hod", "admin"}`. HOD lands on the page scoped to their own bound site; admin gets a sidebar shadow site picker that mirrors the warehouse portal pattern.

Layout (one module per concern):

```
pages_internal/material_estimator/
├── __init__.py            # page_material_estimator(user) — entry point
├── allocation_engine.py   # vendored from SME unchanged (pure pandas)
├── data_layer.py          # @st.cache_data wrappers; build_estimator_inputs()
│                          # subtracts Done_SQM so demand reflects remaining work
├── engine_runner.py       # cached allocation + procurement_list (with
│                          # Ordered_Qty join for Net_Shortfall)
├── downloads.py           # standalone secure-download helpers — sme_secure_
│                          # xlsx_download / sme_secure_pdf_download. NOT a
│                          # monkey-patch on st.download_button. Excel still
│                          # AES-zipped via pyzipper; PDF still ReportLab
│                          # encrypt=. Password constants live at module top.
├── theming.py             # CSS — status pills, location chips, header strip
├── ui_dashboard.py        # Tab 0 — KPIs + feasibility + procurement view
├── ui_priority.py         # Tab 1 — streamlit-sortables drag (rank-input fallback)
├── ui_session_order.py    # Tab 2 — plan summary + suggestion engine
├── ui_location_report.py  # Tab 3 — Location-Based + All-Equipment views
├── ui_equipment_report.py # Tab 4 — Per-tag 3-section report
├── ui_execution_plan.py   # Tab 5 — Execution Plan + Progress + Consumption
│                          # Comparison. READ-ONLY. The comparison view joins
│                          # the ERP `consumption` ledger on Material_Code.
├── ui_total_overview.py   # Tab 7 — Project-wide demand × stock × on-order
└── ui_master_data.py      # Tab 8 — Equipment edit + Recipe read + Locations
                           # + Equipment Types CRUD (system_settings backed)
```

The SME's `📦 Inventory` top-level tab is **gone entirely** (per directive #4). Its six sub-views (Inventory Dashboard, Consumption, Order Status, New Order, Receipt Log, Consumption Log) all relied on the legacy `consumption_log` / `receipt_log` / `orders_log` tables — the merger writes nothing to any of these, so the data-entry surface ceases to exist. HODs use the existing ERP Live Dashboard for current stock and the Logistics Portal for open POs.

### Critical contracts (don't break these)

- **`Available_Qty` is computed, never stored.** Any future caller that wants per-`Material_Code` stock must go through `get_sme_inventory_view()` (or `load_live_inventory()` directly and group by `Material_Code` themselves). Adding an `Available_Qty` column to `inventory` would silently fork truth from the ledger.
- **Locations + Equipment Types belong in `system_settings`.** The legacy plan proposed `sme_locations` / `sme_types` tables; the final decision (Correction #1) was to reuse `system_settings`. Adding a separate table would split the dropdown infrastructure and silently break the per-site override fallback.
- **No monkey-patching `st.download_button`.** SME originally patched the global namespace. In the multi-page ERP that would silently break every other portal's downloads. The merge uses `sme_secure_xlsx_download(...)` / `sme_secure_pdf_download(...)` — same UX (popover + password gate + auto-download), zero global side-effects (Correction #2).
- **Estimator is read-only on the ledger.** Master Data tab edits SME-prefixed tables only. Any path that needs to write `consumption` / `receipts` / `returns` MUST go through the existing EOD pipeline.
- **HOD exact-role lock matters.** Procurement / Logistics / Warehouse roles do NOT inherit access — even though they sit higher in `ROLE_HIERARCHY` numerically. The `_EXACT_ROLE_PAGES` entry is the only thing keeping the estimator from leaking to roles that have no reason to see planning data.
- **Allocation engine's column contract is `Equipment_Tag_No.`** (trailing dot). The accessor `get_sme_equipment()` renames `Equipment_Tag_No` → `Equipment_Tag_No.` at the boundary. Never join across the boundary without the dot.
- **Recipe master is global; equipment master is site-scoped.** Two different idempotency contracts in the bootstrap — `sme_recipe` is fully DELETE-then-INSERT on every run; `sme_equipment` only deletes rows matching the target `Site_ID`.

### Tests — `bug_check.py` +14 checks, all green (14/14)

Round-17 area in `BUG_REPORT.md` covers schema + idempotency + helper arithmetic + site filter + ledger bridging + setting CRUD + progress preservation + RBAC matrix + import smoke + PAGE_ACCESS membership. The two non-trivial defensive details: the RBAC check reads `main.py` as text and parses the dict literal (avoids transitive imports of `bcrypt` / `fpdf` that may be missing locally); the import smoke loads the ME package via `importlib.util.spec_from_file_location` to bypass `pages_internal/__init__.py` for the same reason. Both still validate the production contract.

Pre-existing baseline before this merge was 468/485 — the 17 failures in `manual_qa` / PR PDF / PO PDF parser / locate_anything sidecar are unchanged and unrelated. Post-merge total: 482/499. UI crawler auto-discovers the new page (it iterates `PAGE_ACCESS`, no hard-coded count to update).

### Requirements

Added four packages — `xlsxwriter`, `pyzipper`, `reportlab`, `streamlit-sortables`. All pure-Python or pre-built wheels, no display deps (the cloud-Linux exclusion that affects `pywhatkit` does NOT apply here — `pyzipper` is just ZIP + AES, no GUI).

### Gotchas to watch

- `ui_priority.streamlit_sortables` is optional. The page falls back to a rank-input grid if missing — works, but the drag UX is the better one.
- The Master Data → Equipment editor is rebuild-from-scratch on save (`DELETE WHERE Site_ID = ?` → re-INSERT). For large equipment masters this is fine (we tested with 75 rows) but a future page-size threshold may need a per-row UPSERT. Don't over-engineer that until it's needed.
- The Consumption Comparison view joins `consumption.SAP_Code → inventory.SAP_Code → Material_Code`. If a SAP_Code has no Material_Code on `inventory` it silently drops from the comparison. This matches the rest of the SME engine, which is `Material_Code`-keyed end to end.
- `get_sme_inventory_view()` groups by `Material_Code` and takes `MIN(Equipment_Description)` for display. If two SAPs share a Material_Code with different descriptions, the picked label is arbitrary. Full SAP detail is always available on the ERP Live Dashboard.
- The seed Excel files in `scripts/sme_seed_data/` are a one-shot artifact. After bootstrap, edits go through the Master Data tab (for equipment) or by re-running the bootstrap (for recipes — they're global and uneditable in-UI by design in this round).

### Operational note

Deploying Round 17:
1. Pull the changes.
2. Run `python3 bug_check.py` and confirm 482/499 (the same 17 pre-existing failures remain).
3. Start Streamlit. `init_db()` self-heals the three new SME tables + seeds the new `system_settings` categories on first request.
4. Run `python3 scripts/sme_bootstrap.py --site-id <SITE>` once per site that needs the estimator. Idempotent — safe to re-run.
5. The portal appears in the sidebar for `hod` + `admin` accounts immediately.

---

## 2T. Tuning Round 18 (2026-06) — SME Consumption Form + UI Parity + Raw .xlsx

Round 17 made the estimator read-only. Round 18 promotes it to a first-class **data-entry surface** that funnels through the ERP's EOD commit gate without ever leaking SME-specific columns into the ledger tables. Per the user's binding data routing rule: aggregate per `Material_Code` at the SK's Submit-Batch boundary, write 1 clean `pending_issues` row per material; keep `Equipment_Tag` / `System_Code` / `Location` exclusively in the new SME-side ledger.

### Schema delta — `init_db()` self-heal, all additive

| Object | Purpose |
|---|---|
| `sme_sqm_progress.Done_SQM_staged REAL DEFAULT 0` | Two-column model. Increments on SK Submit Batch; shifts to `Done_SQM` on HOD EOD commit; decrements on reject. Estimator sums both for "remaining work" math. |
| `sme_consumption_log` | Rich detail ledger — one row per `(batch_id × equipment_tag × system_code × material_code × sqm × expected × actual)`. Status FSM: `staged → committed → rejected`. Links back to the aggregated `pending_issues` row via `staged_pi_id` for full bidirectional audit. Never touched by `commit_eod`. |
| `v_inventory_with_sme` VIEW | LEFT JOIN exposing computed `is_sme = EXISTS(SELECT 1 FROM sme_recipe r WHERE r.Material_Code = inventory.Material_Code)`. Zero new columns on `inventory`; zero maintenance — recipe edits flow through automatically. |

### Dispatch + staging helpers — `database.py`

- **`is_sme_material(material_code)` / `is_sme_sap(sap_code)`** — runtime dispatch fork. Returns True iff the material participates in any `sme_recipe` row.
- **`get_sap_for_material(material_code) → SAP_Code | None`** — 1:1 resolver. Per the user contract, every SME-flagged Material_Code has exactly one SAP_Code in `inventory`; SAPs may exist without a Material_Code but those can never be SME-flagged (no recipe possible).
- **`stage_sme_consumption_batch(*, site_id, entry_date, entered_by, rows, extras)`** — the central bridge. Aggregates `actual_qty` per `Material_Code` across the SK's grid, resolves SAP_Code per material, writes one `pending_issues` row per material (with `Source_Ref="SME:<batch_id>"`), writes per-detail `sme_consumption_log` rows linked via `staged_pi_id`, bumps `Done_SQM_staged` per `(Site_ID, Equipment_Tag, Lining_System_Code)`. Required `extras` keys: `Issued_To / Tank_No / Serial_No / PR_Number` (the ERP-mandatory pending_issues fields, captured at the batch level). Returns `{batch_id, pending_issue_ids, materials_staged}`.
- **`mark_sme_log_committed(pending_issue_ids, conn)`** — flips linked `sme_consumption_log` rows to `committed` and shifts SQM from staged → committed. Idempotent (clamps staged at 0).
- **`mark_sme_log_rejected(pending_issue_ids, *, rejected_by, reason, conn)`** — same for the reject path; decrements `Done_SQM_staged`.

### EOD listener wrappers — the architectural keystone

- **`commit_eod_with_sme_sync(conn, hod_username)`** — captures `pending_issues.id` values about to be committed (BEFORE the existing `commit_eod` deletes them), runs `commit_eod` **unchanged**, then calls `mark_sme_log_committed` with the captured ids. SME sync failure NEVER undoes the commit — it logs `SME_SYNC_FAILED_ON_COMMIT` to `system_audit_log` and continues. The legacy `commit_eod` signature and behavior are byte-identical to Round 17.
- **`hod_reject_pending_issue_with_sme_sync(issue_id, ...)`** — wraps `hod_reject_pending_issue` for the SME reject path.

Wired into `pages_internal/hod_portal.py` via two import aliases (the import block renames the wrappers back to the legacy names so all 2,000+ existing call sites continue to work unchanged):

```python
from database import (
    commit_eod_with_sme_sync as commit_eod,
    hod_reject_pending_issue_with_sme_sync as hod_reject_pending_issue,
)
```

### SME consumption form — `pages_internal/daily_issue_log.py`

New top-level expander **🧪 SME Multi-Material Entry (Lining systems)** at the top of the SK Consumption tab. Renders only when `sme_recipe` has rows (non-SME sites never see it). The legacy single-item form remains unchanged for ad-hoc issues; when the SK picks an SME-flagged SAP through that form an inline banner steers them to the SME expander.

Form flow (`_render_sme_consumption_form`):

1. **Step 1 — Batch details:** Date, Issued_To, Tank_No, Serial_No, PR_Number, optional Notes. These propagate to every aggregated row at submit.
2. **Step 2 — Equipment + systems:** pick `Equipment_Tag_No.`, multi-select Lining System Codes (with `✨ Select All` sentinel matching legacy SME).
3. **Step 3 — Per-system SQM + material grid:** SQM input capped at `min(remaining_sqm, stock_coverage_sqm)` where stock-coverage = `min(Available / For_1_SQM)` across system materials. Auto-computed `Required_Qty = For_1_SQM × SQM`. `Actual_Qty` editable with live shortfall warnings.
4. **Add to Batch** stages local session state.
5. **Submit Batch** calls `stage_sme_consumption_batch`, busts the inventory cache, fires the **inline Days-of-Continuation block** (runway report showing days remaining per material at this batch's burn rate, color-coded red <3 / amber <7 / green ≥7).

### UI parity port — `pages_internal/material_estimator/`

- **`theming.py` rewritten** with the SME sticky-header chain (title bar + tab strip + sub-view radio, all pinned while scrolling via `position: sticky` + `:has()` CSS), recolored to ERP yellow/amber (`#FBBF24` → `#D97706` gradient). Logo embedded as base64 data URI from `sme_logo.png` bundled in the package.
- **`widgets.py` (new)** — `dbl_click_metric` (KPI card with click-to-expand drilldown popovers), `plotly_mat_table` (color-coded per-row allocation table — green ≥100% / orange ≥90% / yellow ≥80% / red < 80% on the Fulfil % column), `status_dot` / `fulfil_pill` / `loc_badge` inline HTML pills, `days_of_continuation_block` (the post-Submit-Batch runway report).
- **`ui_dashboard.py`** — 8-card KPI strip (Equipment / Total SQM / Coverage SQM / SQM Deficit / Overall Coverage / Fully ready / Partial / Critical) every tile click-through to a relevant drilldown.
- **`ui_session_order.py`** — 4-card KPI strip + per-tag breakdown using `plotly_mat_table` with On Order column when available.
- **`ui_total_overview.py`** — 3-card click-through KPIs + plotly-styled coverage matrix.

### Downloads — raw .xlsx, no AES wrapper

- **`pyzipper` ripped out entirely.** Removed from `requirements.txt`. `_encrypt_xlsx_bytes` deleted. The `.protected.zip` wrapper is gone.
- **Excel downloads use `st.download_button` directly** — one click, raw `.xlsx`, no popover. Filename pattern: `SME_<ReportName>_<Site>_<YYYY-MM-DD>.xlsx`. The SME_ prefix groups outputs in a user's Downloads folder; the Site disambiguates multi-site admins.
- **Excel builders gain ERP-branded title block** (amber `#FBBF24` header strip, gray subtitle line with site + date, freeze panes at row 4).
- **PDF popover gate retained.** PDFs travel via email so the password adds real value at rest. Still uses ReportLab `encrypt=` with the existing `SME_PDF_PASSWORD` constant (default `pdf2026`; rotate by editing).
- **New `build_equipment_report_excel`** — faithful port of legacy SME's 3-section format (Equipment Summary → System Summary → Detailed Table) with merged section banners.
- **New `build_location_report_excel`** — per-location feasibility + per-material alloc + optional extra summary blocks.

### Critical contracts (don't break)

- **ERP `pending_issues` / `consumption` schemas are unchanged.** No `Equipment_Tag`, no `System_Code`, no `Location` columns. The aggregation at `stage_sme_consumption_batch` is what protects this. Any future caller that thinks it needs to add such a column to either ledger should re-read this section and route through the SME log instead.
- **`commit_eod()` signature + behavior unchanged.** The wrapper layer captures ids before, runs commit_eod, marks log after. Sync failure logs but never undoes the commit. Any future "let me just add one tiny side-effect to commit_eod" change should go through a wrapper, not modify `commit_eod` itself.
- **State machine direction is one-way per call.** `stage_sme_consumption_batch` only goes `_ → staged`. `commit_eod_with_sme_sync` only goes `staged → committed`. `hod_reject_pending_issue_with_sme_sync` only goes `staged → rejected`. No path flips `committed` or `rejected` back to `staged` — if the HOD per-row unapprove button (Round 13) is hit, it flips PI status from approved → pending_hod, which stays "staged" from the SME ledger's POV. No SME-side action needed.
- **SAP↔Material is 1:1 for SME materials, by user contract.** Don't add SME-flagged SAPs that share a Material_Code with another inventory row. `get_sap_for_material` uses `LIMIT 1` and will silently pick one if the contract breaks — a future caller could add a uniqueness CHECK to enforce.
- **No monkey-patch on `st.download_button`.** All Round-18 download helpers are namespaced (`sme_*`) and standalone. Reverting this would break HOD/Admin/Logistics/Warehouse portal downloads in ways the test suite cannot easily catch.

### Tests — `bug_check.py` +16 R18 checks, all green (16/16)

Schema (3): `Done_SQM_staged` column; `sme_consumption_log` table + status FSM; `v_inventory_with_sme` view + `is_sme` flag math.
Dispatch + resolution (3): `is_sme_sap` / `is_sme_material` (incl. empty/None safety); `get_sap_for_material` 1:1 mapping.
Stage (3): per-material aggregation (2 detail rows → 1 PI row, qty summed); missing-extras `ValueError`; `Done_SQM_staged` increment with per-tag-system MAX dedupe.
State machine (3): `commit_eod_with_sme_sync` shifts SQM staged→committed; **`commit_eod` itself unchanged regression** (non-SME row still commits identically); reject path decrements + archives.
Downloads (2): `pyzipper` not imported (source-level grep); filename pattern matches `SME_<Report>_<Site>_<YYYY-MM-DD>.xlsx`.
Integration (3): `widgets` module loads with all six helpers; `_render_sme_consumption_form` present in `daily_issue_log.py`; `hod_portal.py` aliases the two EOD wrappers.

Pre-merge baseline: 482/499. Post-merge: 498/515. The 17 pre-existing failures (manual_qa / PR PDF / PO PDF / locate_anything sidecar) are unchanged.

### Gotchas

- The SME form gates `Submit Batch` on the four required extras (Issued_To / Tank_No / Serial_No / PR_Number). If a deployment ever flips `OPTIONAL_ISSUE_COLS` to allow blanks, the SME form would still require these — keep the form's validation in sync.
- `Done_SQM_staged` clamps at 0 in the increment helpers. A second commit_eod_with_sme_sync call after a successful commit is a no-op (the SME log status is already `committed`, so `mark_sme_log_committed` finds no matching `staged` rows). Safe but not silent if you're debugging — look at `committed_at` timestamps in `sme_consumption_log`.
- The post-Submit Days-of-Continuation block calls `Material_Estimator.widgets.days_of_continuation_block` — if the estimator package is ever uninstalled the SME consumption form will fail at submit time. Import is lazy (inside the helper) so it doesn't break module load.
- Filename uses `_safe()` which collapses non-alphanumerics to `_`. Site IDs with spaces become underscored. If a customer ever creates a Site_ID like `Site A`, the file lands as `SME_Project_Overview_Site_A_2026-06-25.xlsx`. Acceptable but not pretty.
- The HOD per-row approve / unapprove buttons (Round 13) flip PI status WITHIN `pending_issues`. From the SME ledger's POV nothing changes — the rows are still "staged" until commit_eod flips them. Don't add a sync hook to those buttons; you'd cause double-counting if the user toggled approve/unapprove repeatedly.

### Operational note

Deploying Round 18:
1. Pull the changes.
2. `pip install -r requirements.txt` (no new packages — pyzipper removed; xlsxwriter / reportlab / streamlit-sortables already there from R17).
3. `python3 bug_check.py` and confirm **498/515** (the same 17 pre-existing failures remain unrelated).
4. Start Streamlit. `init_db()` self-heals the new `Done_SQM_staged` column + `sme_consumption_log` table + `v_inventory_with_sme` view on first request.
5. The new `🧪 SME Multi-Material Entry` expander appears at the top of the SK Consumption tab once `sme_recipe` has rows (it should already after the R17 bootstrap).
6. HOD's EOD commit + per-row reject now automatically sync the SME ledger via the wrapper layer — no UI changes for HODs.

---

## 2U. Tuning Round 19 (2026-06) — Apple-to-Apple SME UI Parity

Round 18 wired the data and put a working consumption form on the SK side. Round 19 brings the **estimator portal's visual + interaction surface to 1:1 parity with the original standalone Smart Material Estimator app** — same sub-views, same KPI cards, same Plotly tables, same SVG gauges, same drag-priority placements, same per-location color schemes, same Master Data CRUD across all 5 sub-views. Per user directive: *"apple to apple originality is required."*

### Three new shared modules

- **`colors.py`** — verbatim port of SME's `COLOR_SCHEMES` dict (7 schemes — `dashboard`, `brown_field`, `train_j`, `train_k`, `session`, `execution`, `overview`) + `LOC_COLOR_MAP` (Brown Field → brown_field; TRAIN J → train_j; TRAIN K → train_k) + `TABLE_COLOR_MAP` + `scheme_for_location()` / `scheme_for_table()` resolvers. These are the **authoritative SME palette**; theme drift here would break the parity.
- **`charts.py`** — `render_design_gauge()` (SVG semi-circular coverage gauge — exact SME look), `render_design_hbar()` (SVG horizontal bar chart with right-aligned labels), `render_plotly_stacked_hbar()` (Available/Shortage), `render_plotly_grouped_bar_by_location()`.
- **`suggestion_panel.py`** — port of SME's `_run_suggestion_engine` (via `allocation_engine.run_suggestion_engine`) + the 2-column "By Equipment" / "By System Code" UI cards.

### Downloads — full SME parity sheet layout

`downloads.py` rewritten with `_write_sheet()` that produces SME's exact professional sheet:
- **Row 0–3:** Logo (top-left, embedded from `sme_logo.png`) + spacer.
- **Row 4:** Title bar — merged across df columns, scheme `title_bg`.
- **Row 5:** Header row — scheme `header_bg`, white bold text, bordered.
- **Row 6+:** Data rows with banding (white + `#F9FAFB`), numeric columns formatted `#,##0.###`.
- **Last row:** Optional **GRAND TOTAL** with scheme `total_bg` / `total_fg`, sums all numeric columns.
- **AutoFilter** on the header row.
- **Freeze panes at row 6.**

Multi-sheet variant (`generate_multi_sheet_excel`) honors per-sheet `color_scheme`. Specialized helpers `equipment_report_excel` (location_sheets + all_eq_sheet + optional all_codes_sheet) and `location_report_excel` preserve per-location schemes. Back-compat aliases (`build_equipment_report_excel`, `build_location_report_excel`) maintained for any caller not yet migrated.

### Tab-by-tab parity port

| Tab | Sub-views | What's now in the file |
|---|---|---|
| 0 Dashboard | 📈 Project Overview · 🛒 Material Requirement & Procurement | 4-column filter strip (Location · Type · System Code · Substrate). Overview: 7-card click-through KPI strip (Equipment, Total SQM, Coverable SQM, SQM Deficit, Overall Coverage, Shortfall SQM, Critical <50%); SVG gauge; mini Plotly stacked bar; per-location grouped Plotly bar + location strip; system-code coverage h-bar; per-material coverage h-bar (lowest 10); Full Material Balance table with row coloring; Stock-Only Materials expander; Excel + PDF download pair (dashboard scheme). Procurement: 4-card strip; per-location header strip; per-code expanders with 5 KPI metrics + per-code material table; grand procurement table; Net Order List (if shortages). |
| 1 Selective Equipment Entry | (single) | Left column (1/2.65): Location · Type · System Code filters + tag search + **"＋ Add to Session"** + drag-sortable session list + per-tag fulfillment rows (status dot + pill + ✕ remove) + Clear All. Right column (1.65/2.65): amber info card (tag, location badge, name, type/substrate/codes grid) + per-system-code expanders with 4 KPI metrics + `plotly_mat_table(show_sqm=True)` + equipment grand total box. |
| 2 Session Order Report | (single) | 4-card click-through KPI strip (Equipment, Materials, Need to Order, Overall Coverage); in-tab priority reorder sortable; per-equipment expanders with metadata strip + per-system-code header block (status dot + code chip + SQM + fulfil pill) + conditional 4 KPI metrics + `plotly_mat_table(show_sqm=True)` + equipment grand-total box; **Combined Procurement Section** with Plotly stacked bar (top 10 shortages) + master material table; 5-cell grand total; multi-sheet Excel + PDF (session scheme) + Order List Excel + PDF; **🔮 Smart Reorder Suggestions** panel. |
| 3 Location Report | 📍 Location Based · 🌐 All Equipment | All Equipment: single drag-sortable across all locations + 5-card KPI strip + per-equipment expanders (3-button download row + readiness chip + metadata + per-code blocks + add-to-session button) + Smart Suggestions expander + multi-sheet Excel button. Location Based: per-location sections each with its own drag-sortable + status header + per-equipment expanders (downloads honor location color scheme) + per-location Excel + PDF + per-location Smart Suggestions; final multi-location Excel + PDF buttons. |
| 4 Equipment Report | (single) | 4-cell KPI strip (Equipment Tags / Locations / System Codes / Total SQM); per-location expandable list with per-equipment expanders that each have a 3-button row (Excel + PDF + Print) using the location's color scheme; per-system-code row with code chip + system name + SQM (right-aligned amber bold). Per-location quick download row + multi-sheet workbook (per-location sheets + "All Equipment" sheet + optional "All System Codes" sheet). |
| 5 Execution Plan | ⚙️ Execution Plan · 📋 Progress List · 📊 Consumption Comparison | Execution Plan: equipment + system code selectors → amber critical card → all-materials table → 1️⃣ critical code shortages (red) / 2️⃣ other code shortages (orange) / 3️⃣ fully covered codes (green). Progress List: 4-card KPI strip + Location/Status filters + progress table with row coloring + numbered production-detail blocks read from `sme_consumption_log`. Consumption Comparison: From/To/Location/Equipment Tag/System Code filters + 4-card KPI + variance table (green on-target / orange over / blue under) joining ERP `consumption` ledger on `Material_Code` — **READ-ONLY**. |
| 7 Total Overview | (single) | 4-cell filter strip (Location/Type/Code/Status) + 6-card click-through KPI strip (Items / Total SQM / Already Done / Remaining / Shortfall SQM / Avg Coverage) + master table (S.No, Equipment Tag, Name, Location, Type, Substrate, System Code, System Name, Total SQM, Already Done, Remaining, Total Demand, Allocated, Shortfall, Fulfil %) with row coloring + sub-metrics strip + per-system-code expanders with 5 click-through metrics + material detail table; downloads in overview scheme; SME Consumption Log export expander. |
| 8 Master Data | Equipment · LINING SYSTEM MATERIAL CONSM · Materials_DetailsAvailable_Qty · ➕ Add Location · ➕ Add Type | Equipment: Add expander with multi-select codes + per-code SQM inputs + search filter + editable grid with checkbox column + Save edits + Delete selected (also deletes from `sme_sqm_progress`). Recipe: Add row form + editable grid + Save/Delete. Materials_DetailsAvailable_Qty: read-only view of `v_inventory_with_sme` with Search / SME-only checkbox / Category filter. Locations + Types: list + Add + Remove against `system_settings` via `add_sme_setting` / `delete_sme_setting`. |

### Critical contracts (preserved across R19)

- **ERP ledger schemas unchanged.** A regression check (`check_r19_ledger_schemas_unchanged`) asserts that `pending_issues`, `consumption`, `receipts`, and `returns` do NOT carry `Equipment_Tag`, `Equipment_Tag_No`, `Lining_System_Code`, `System_Code`, or `SQM_Completed` columns. This is the Round-18 routing rule, locked at the schema level.
- **Master Data writes restricted.** A static-grep check (`check_r19_master_data_safety`) asserts that `ui_master_data.py` issues **no** `INSERT INTO` / `UPDATE` / `DELETE FROM` against `pending_issues` / `consumption` / `receipts` / `returns`. Allowed targets: `sme_equipment`, `sme_recipe`, `sme_sqm_progress`, `system_settings`. The CRUD UI gets the rich edit experience without touching the ERP ledger.
- **`commit_eod` byte-identical.** Round 18's wrapper layer (`commit_eod_with_sme_sync`) still owns the SME sync; `commit_eod` itself is never edited. R19 added no new callers.
- **RBAC unchanged.** Material Estimator still exact-locked to `{hod, admin}` via `_EXACT_ROLE_PAGES`.
- **Color schemes are authoritative.** When a download is invoked from a per-location button (e.g., Location Report → Brown Field), the helper resolves the location → `brown_field` scheme and that scheme's `title_bg` / `header_bg` / `total_bg` colors land in the .xlsx. Don't fork these constants — edit `colors.py` if a palette change is ever needed.

### Tests — `bug_check.py` +16 R19 checks, all green (16/16)

SHARED INFRA: COLOR_SCHEMES has all 7 schemes with exact SME hex values; `scheme_for_location` maps the three named locations; `charts` exports gauge + h-bar + Plotly helpers; `suggestion_panel` exports `render_suggestion_panel` + `_run_suggestion_engine`; `downloads` exports the specialized report builders + back-compat aliases; all 8 ui_*.py modules + the 5 shared modules import cleanly.

PER-TAB: Dashboard has ≥7 `dbl_click_metric` cards + both sub-views + 4-field filter strip; Priority has Location/Type/Code filters + tag search + add/remove/clear; Location Report has both sub-views + `scheme_for_location`; Equipment Report has SME column order + `equipment_report_excel`; Execution Plan has 3 sub-views + zero consumption INSERTs; Total Overview has ≥6 `dbl_click_metric` cards + 4-field filter strip including status; Master Data has all 5 sub-views; SME logo bundled.

REGRESSION: ERP ledger schemas unchanged; Master Data routing rule (no writes against `pending_issues` / `consumption` / `receipts` / `returns`).

Final test posture: **514 / 531** (Round 17 14/14 + Round 18 16/16 + Round 19 16/16; same 17 pre-existing failures in manual_qa / PR PDF / PO PDF / locate_anything sidecar — all unrelated).

### Gotchas

- The drag sortables share session-state keys with the legacy SME: `session_tags` (global priority), `all_eq_order` (Location Report → All Equipment), `loc_order[loc]` (Location Report → Location Based). Keep these key names stable — they're the bridge that ties Dashboard / Selective Equipment / Session Order / Location Report together so the priority flows seamlessly between tabs.
- Plotly is required for `render_plotly_stacked_hbar` + `render_plotly_grouped_bar_by_location`. Both helpers degrade gracefully (silent skip) if it isn't installed, but the visual parity drops noticeably.
- The Dashboard Procurement sub-view iterates per-location → per-code. For projects with many locations × many codes the nested expanders can scroll long. SME accepted this; do not collapse into a single table without UX review.
- `Materials_DetailsAvailable_Qty` is **read-only** in our merged ERP. The SME version had Add/Edit/Delete — those got dropped because ERP `inventory` is authored on the ERP's Material Details tab (not from the estimator). The dropdown for SME-only filtering is the value-add this view brings over the ERP-native view.
- The Equipment Report's per-equipment Print button shows a Streamlit info banner pointing the user to the browser print dialog rather than injecting `@media print` CSS — the SME chain CSS already hides the sidebar + tab strip in print mode globally.
- Excel exports are pure SME styling but the **filename** still uses Round 18's pattern `SME_<Report>_<Site>_<YYYY-MM-DD>.xlsx`. If a customer ever wants the SME's `<title>_<username>_<date>` convention back, swap `sme_filename()` in `downloads.py`.

### Deployment

1. `git pull` — picks up the new `colors.py`, `charts.py`, `suggestion_panel.py` modules and the rewritten 8 tab files.
2. `python3 bug_check.py` should report **514 / 531** (17 pre-existing failures remain in unrelated areas).
3. Restart Streamlit. No DB migrations needed — Round 18's schema is enough.
4. The portal looks substantially richer immediately — every tab now has SME's KPI cards, plotly tables, sortables, expanders, and downloads with logo + scheme-colored sheets.

> **Round 20 reverted this approach.** The piecemeal R19 rewrite reproduced the SME's appearance imperfectly: my Phase-B `_build_dashboard_frames()` did `alloc.groupby(["Lining_System_Code", "Material_Code"])` on the engine output, but the engine deliberately drops `Lining_System_Code` during aggregation — KeyError on production. The fix-the-rewrite path is fragile because the SME app threads a dozen intermediate frames through each tab (`equip_sc`, `eq_master`, `sqm_ref`, `dm`, `INV_POOL_INIT`, …) and a partial rewrite can't catch every one. Round 20's literal drop-in inherits the SME's intermediate-frame architecture verbatim and exposes only one seam — the data layer — for surgical edits.

---

## 2V. Tuning Round 20 (2026-06) — Literal SME Drop-In (revert and replace)

The R19 piecemeal port broke in two ways: (a) the SME's intermediate-DataFrame architecture got partially reproduced, surfacing as `KeyError: 'Lining_System_Code'` when Dashboard groupby's ran against the cascade-allocate output; (b) the dark/light theme toggle stopped working because the SME's CSS variable cascade was scoped into the wrong DOM subtree. Round 20 is a clean pivot: **delete the entire R19 package, drop the original 8,505-LOC SME `app.py` in as a single file, perform only the surgical edits the merger absolutely requires.** This guarantees 100% parity because we are running the SME's own code.

### File layout change

| Round 19 (deleted) | Round 20 (literal drop-in) |
|---|---|
| `pages_internal/material_estimator/` (18-file package: 8 tab modules + colors/charts/widgets/theming/downloads/suggestion_panel/engine_runner/data_layer/allocation_engine + sme_logo.png + __init__.py) | `pages_internal/material_estimator_portal.py` (single file, 7,103 lines, copy of SME `app.py` with `# R20 EDIT` markers at every surgical seam) |
|   | `pages_internal/material_estimator_engine.py` (vendored SME `allocation_engine.py`) |
|   | `pages_internal/sme_logo.png` |

### Surgical edits (search for `# R20 EDIT` in the file to find them all)

| Edit | What |
|---|---|
| Imports | Removed `sys.path.insert` + `from validate_data import …` + `from allocation_engine import build_demand_matrix`. Added `import database as D` + `from pages_internal.material_estimator_engine import build_demand_matrix`. |
| `st.set_page_config(...)` | Commented out — ERP `main.py` already called it. |
| `st.download_button` monkey-patch | Module-level reassignment removed; helper saved as `_SME_SECURE_DOWNLOAD_BUTTON` and patched inside `page_material_estimator(user)` via try/finally so other ERP portals' downloads are unaffected (R17 Correction #2 preserved). |
| `_show_login`, `_ADMIN_USER`, `_ADMIN_PASS`, auth gate | Deleted. ERP `main.py` + `auth.py` own login; the user dict is passed in. |
| `load_all()` | Rewritten end-to-end. Calls ERP helpers (`D.get_sme_inventory_view`, `get_sme_equipment`, `get_sme_recipe`, `get_sme_sqm_progress`) and builds the SME's exact intermediate frames: `inv` (Material_Code-keyed), `recipe` (with synthesized `Lining_System_Short_Name` / `Lining_Type` / `Material_Description`), `equip_sc` ((tag × code) aggregation carrying `Lining_System_Code`, `Total_SQM_Original`, `done_sqm`, `remaining_sqm`, `Total_SQM`), `dm` (demand matrix), `eq_master` (one row per tag), `sqm_ref`. The R19 KeyError is gone: `equip_sc` and `dm` both carry `Lining_System_Code`. |
| Module-level `inv, recipe, … = load_all()` | Replaced with empty placeholders. Real assignment happens inside `page_material_estimator(user)` via `global` so SME helpers (`cascade_allocate`, `tag_fulfillment`, `syscode_fulfillment`, `sqm_can_do`) which read these via closure see site-scoped data. |
| `get_db()` / `db_available()` | Redirected to the ERP DB connection. Always available. |
| `_ensure_locations_table()` / `_ensure_types_table()` | Stubbed to no-op. The legacy `locations` / `types` tables are now VIEWS (see below); seeding is owned by `init_db`. |
| `with tab_consume:` block (Inventory tab) | Deleted entirely — 1,402 lines across 6 sub-views. R18 wired the SME consumption flow into the ERP's `daily_issue_log.py` as the `🧪 SME Multi-Material Entry` expander, routing through `stage_sme_consumption_batch` + `commit_eod_with_sme_sync` for the proper EOD ledger commit. |
| Tab declaration | 9 tabs → 8 tabs. `tab_consume` removed from unpacking and `📦 Inventory` removed from labels. |
| Master Data Locations CRUD | `INSERT INTO locations (...)` → `D.add_sme_setting("sme_location", name, site_id)`. `DELETE FROM locations WHERE name=?` → `D.delete_sme_setting("sme_location", name, site_id)`. R17 Correction #1 preserved. |
| Master Data Types CRUD | Same pattern with `sme_equipment_type` category. |
| Page body wrap | Everything from `with st.sidebar:` to EOF (3,913 lines) wrapped inside `def page_material_estimator(user)` with try/finally for the scoped monkey-patch + `global` for the data globals. |

### Compatibility VIEWS in `database.py:init_db`

The SME's legacy SQL queries reference tables that don't exist in the ERP schema. Round 20 adds 6 read-only VIEWS so those queries Just Work:

| View | Resolves to | Purpose |
|---|---|---|
| `locations` | `system_settings WHERE category='sme_location'` | name + synthetic badge_color + rowid as sort_order |
| `types` | `system_settings WHERE category='sme_equipment_type'` | name + rowid as sort_order |
| `consumption_log` | `sme_consumption_log WHERE status='committed'` | with column aliases (entry_date → Date, Equipment_Tag_No → equipment_tag, etc.) so the SME's "Consumption Comparison" sub-view shows only HOD-approved consumption (matches SME semantics) |
| `equipment` | `sme_equipment` | lowercase snake_case aliases the SME uses |
| `recipe` | `sme_recipe` | same |
| `sqm_progress` | `sme_sqm_progress` | done_sqm exposed as `Done_SQM + Done_SQM_staged` so the SME's progress views reflect in-flight work too |

The views are read-only by design. All WRITES against `locations` / `types` are surgically rerouted to `add_sme_setting` / `delete_sme_setting` helpers (see edits table above).

### Critical contracts preserved

- **The CSS block and `_apply_theme_attr()` are untouched** — dark/light mode toggle works exactly as in the standalone SME. The R20 test `check_r20_theme_toggle_present` regression-checks both.
- **`commit_eod()` byte-identical** — R18 wrapper layer still owns the SME ledger sync.
- **ERP ledger schemas unchanged** — `pending_issues` / `consumption` / `receipts` / `returns` carry no SME-specific columns. Regression-tested by `check_r20_ledger_schemas_unchanged`.
- **RBAC unchanged** — Material Estimator still exact-locked to `{hod, admin}`.
- **Master Data routing rule preserved** — all dropdown writes flow through `system_settings` via R17 helpers (Correction #1).
- **No global `st.download_button` patch** — scoped inside the wrapper via try/finally (Correction #2).

### Tests — `bug_check.py` +11 R20 checks, all green (11/11)

R19's 16 checks were all deleted (the package they tested is gone). R17 + R18 tests that referenced the deleted package were also pruned (3 stale checks dropped). Final test posture: R17 13/13 + R18 13/13 + R20 11/11 = **37 SME-related checks all green; total 505 / 522**. The 17 pre-existing failures in unrelated areas (manual_qa / PR PDF / PO PDF / locate_anything sidecar) are unchanged.

R20 checks (all static text/SQL):
1. `material_estimator_portal.py` exists + exports `page_material_estimator(user)`.
2. Portal module loads cleanly (no module-level `st.set_page_config`).
3. SME `<style>` CSS block preserved (greps for `.loc-badge`, `.pill-g`, `.sticky-header-wrap`, etc.).
4. `_apply_theme_attr` preserved and invoked — dark/light mode toggle works.
5. Inventory tab body deleted (`with tab_consume:` absent + label absent from tabs list).
6. Tab declaration unpacks exactly 8 tabs.
7. `_show_login` + auth gate deleted.
8. Monkey-patch scoped inside `page_material_estimator` (try/finally + `_orig_dl_button` save/restore).
9. Locations/Types CRUD routes through `add_sme_setting`/`delete_sme_setting` (no raw `INSERT INTO locations|types`).
10. All 6 compatibility VIEWS present and functional in `init_db`.
11. ERP ledger schemas unchanged regression.

### Gotchas

- The literal SME `app.py` references many display-only columns (`Material Spec.`, `Lining_System+`, etc.) that aren't in our ERP tables. `load_all()` synthesizes them as empty strings so SME's display code finds the key but renders blank. If you ever need real values, populate them via the bootstrap or add columns to `sme_equipment` / `sme_recipe`.
- The SME's `_cached_cascade_allocate` is decorated with `@st.cache_data` and reads `dm` + `INV_POOL_INIT` from module globals via closure. When the user's site changes, those globals get reassigned but the cache key (just `tag_order_tuple`) doesn't see it — `page_material_estimator` explicitly clears the cache on site change to prevent staleness.
- `load_all()` is also `@st.cache_data`-decorated; we pass `_site_id` as its only arg so the cache key naturally invalidates per site.
- Tab 6 Inventory is gone — if a user complains they used the SME's Order Status sub-view, point them at the ERP's Logistics Portal which surfaces open POs natively.
- The R20 compatibility views are read-only. Any future SME-side feature that writes to `locations` / `types` / `consumption_log` directly will fail; route through the helpers instead.
- The SME's sidebar block (theme toggle + project overview + session list) renders alongside ERP's main sidebar nav. They stack vertically — visually busier than the standalone SME but apple-to-apple for the SME's portion. If you ever want to collapse, move the theme toggle into the SME portal's title bar.
- The `_login_site_id` session-state key is set by `page_material_estimator(user)` and read by the Master Data CRUD shims. If a future page also writes this key for a different purpose, the SME portal would scope its CRUD to the wrong site.

### Deployment

1. `git pull` — picks up the deleted R19 package + the new `material_estimator_portal.py` / `material_estimator_engine.py` / `sme_logo.png` siblings.
2. `python3 bug_check.py` should report **510 / 526** (16 pre-existing failures remain in unrelated areas).
3. Restart Streamlit. `init_db()` self-heals the 6 new compatibility VIEWS on first request.
4. Open the HOD or Admin account → Material Estimator: every original SME tab renders verbatim, dark/light theme toggle works, KeyError is gone.

---

## 2V.1. Tuning Round 20.1 (2026-06) — Bug fixes after first live render

After R20 landed, the first live render surfaced four issues that the bare-mode `bug_check.py` couldn't catch:

| Symptom | Root cause | Fix |
|---|---|---|
| **Sticky header + multiple sections render as escaped HTML code text** instead of styled elements | The R20 Phase-2 wrap added a uniform 8-space indent to all 3,913 lines from `with st.sidebar:` to EOF — **including content inside multi-line `st.markdown(f"""...""", unsafe_allow_html=True)` strings**. Markdown sees `        <div>...` (8-space leading) and treats it as an indented code block, escaping the HTML. Affected the sticky header, dashboard gauge, h-bars, section dividers, and per-equipment grand-total boxes. | Tokenize-walk the file via `tokenize.generate_tokens()` to find every `STRING` literal + `FSTRING_START..FSTRING_END` range inside the wrap region. For each, strip 8 spaces from the first non-empty interior line. Two-pass dedent (first-pass missed strings buried under deeper code-block indent levels). |
| **`KeyError: 'Equipment_Tag_No.'` at `_alloc_ov.groupby(...)` in Total Overview** | The SME's `_cached_cascade_allocate` does `result = pd.DataFrame(rows)`. When `rows=[]` (e.g., first render before `dm` is populated, or Streamlit serves a stale empty cache from a prior session), `pd.DataFrame([])` has **zero columns** — every downstream `.groupby(["Equipment_Tag_No.", ...])` raises `KeyError`. | One-line patch: `pd.DataFrame(rows, columns=_EXPECTED_COLS)` where `_EXPECTED_COLS` is the explicit 12-column list the SME always populates. Empty result still has the shape, no KeyError. |
| **Stock-Only Materials shows generic warehouse items** (bolts, gloves, goggles) | The SME's filter was `inv[~inv["Material_Code"].isin(recipe_codes)]` — returned everything in `inv` that wasn't in the current plan's demand. In the standalone SME that was OK because `inv` only had SME materials. In the merged ERP, our `get_sme_inventory_view()` returns every inventory row with a non-blank `Material_Code` — including bolts, gloves, etc. | Add a second filter: `& (inv["Material_Code"].isin(_all_sme_codes))` where `_all_sme_codes = set(recipe["Material_Code"].unique())`. Restricts to SME-tracked materials only (those in any `sme_recipe` row but not in the current plan's demand). |
| **`load_all()` could return DataFrames with zero columns on empty data**, causing KeyErrors in any column-list slice downstream | When `equip_sc.merge(recipe, on="Lining_System_Code")` returns empty (no equipment loaded), the subsequent `dm = dm[[col_list]]` slice raises `KeyError` because the empty DF has no columns. Same risk for `eq_master` and `sqm_ref`. | Wrap each construction in `if X.empty: pd.DataFrame(columns=[...])` guards so empty frames always carry the expected shape. |

### R20.1 regression tests (4/4 green)

1. `check_r20_1_no_string_indent_bug` — tokenize-walk every multi-line string + f-string in the wrap region. Flag any whose **first non-empty interior line** starts with 8+ spaces AND begins with `<` (HTML content). Catches wrap-induced indent that breaks markdown's HTML-block recognition. SQL strings and Python docstrings are not flagged (they don't render through markdown).
2. `check_r20_1_cascade_allocate_empty_safe` — assert `pd.DataFrame(rows, columns=...)` pattern present + `_EXPECTED_COLS` list contains the 6 critical column names (`Equipment_Tag_No.`, `Lining_System_Code`, `Material_Code`, `Demand_Qty`, `Allocated_Qty`, `Shortfall_Qty`).
3. `check_r20_1_stock_only_sme_filter` — static grep for `_all_sme_codes` variable + `isin(_all_sme_codes)` filter expression.
4. `check_r20_1_load_all_empty_safe` — static grep for `if dm.empty:` / `if equip_raw_local.empty:` / `if equip_sc.empty:` guards.

### Why Phase D (download repositioning) was a no-op

After auditing all download buttons in the file, the literal SME drop-in already follows the "downloads after data" rule consistently. The Material Balance download sits after the table + Stock-Only block; the Equipment Report multi-sheet sits after all per-location data; etc. The screenshots showing UI confusion were entirely downstream of Bug #2 (HTML-as-text rendering) — once the markdown rendered HTML correctly, the visual flow followed the SME's original layout. No code changes needed.

### Test posture

Round 17 13/13 + Round 18 13/13 + Round 20 11/11 + Round 20.1 4/4 = **41 SME-related checks all green; total 510 / 526** (the original 17 pre-existing failures reduced to 16 — one manual-qa test recovered incidentally during the merge work).

### Deployment

1. `git pull` — picks up the R20.1 patches in `material_estimator_portal.py` and the +4 R20.1 checks in `bug_check.py`.
2. `python3 bug_check.py` → **510 / 526**.
3. Restart Streamlit. The dashboard sticky header, gauge, h-bars, and per-equipment grand-total boxes all render as styled HTML; the Total Overview tab loads without KeyError; the Stock-Only Materials section shows only SME-tracked materials.

---

## 2W. Tuning Round 20.5 / 20.5.1 (2026-06) — Tab 8 Master Data CRUD wiring + SME inventory isolation

> ### 📐 §2W.0 — SME ARCHITECTURE CANON (the authoritative reference)
> Read this before editing anything SME. The three earlier rounds (R17 merge, R18 consumption form, R20 literal drop-in) are summarized in §2S–§2V; the *current, locked* shape is here.
>
> **What the SME portal IS:** a verbatim drop-in of the original standalone Streamlit app, living as ONE file — `pages_internal/material_estimator_portal.py` (~7,200 LOC). All rendering is wrapped inside `page_material_estimator(user)`. The allocation engine is vendored at `pages_internal/material_estimator_engine.py`. The logo is `pages_internal/sme_logo.png`. It is intentionally NOT refactored into a package — do not "clean it up."
>
> **Why VIEWs:** the SME's legacy SQL references tables named `equipment`, `recipe`, `sqm_progress`, `locations`, `types`, `consumption_log`, and an inventory source. None exist in the ERP schema under those names. `init_db()` creates **compatibility SQLite VIEWs** that alias the real `sme_*` tables (and `system_settings`) into the exact lowercase/dotted/slashed column names the SME expects. The SME reads through the views; **writes are rerouted to helper functions** (views are read-only).
>
> **Canonical table / view map:**
>
> | SME legacy name | Backed by (real object) | Kind | Site-scoped? | Writes go through |
> |---|---|---|---|---|
> | `equipment` | `sme_equipment` | VIEW | ✅ `Site_ID` | `D.insert/update/delete_sme_equipment` |
> | `recipe` | `sme_recipe` | VIEW | ❌ global | `D.insert/update/delete_sme_recipe` |
> | `sqm_progress` | `sme_sqm_progress` | VIEW | ✅ `Site_ID` | `D.upsert_sme_sqm_progress` |
> | Materials radio → `sme_materials_view` | `sme_inventory_seed` + `receipts`/`consumption` join | VIEW | ❌ global | `D.insert/update/delete_sme_inventory_seed` |
> | `locations` | `system_settings` (category `sme_location`) | VIEW | ✅ via Site_ID rows | `D.add/delete_sme_setting` |
> | `types` | `system_settings` (category `sme_equipment_type`) | VIEW | ✅ via Site_ID rows | `D.add/delete_sme_setting` |
> | `consumption_log` | `sme_consumption_log` (R18) | VIEW | ✅ `Site_ID` | R18 staging helpers |
>
> **The inventory isolation model (THE most important part):**
> ```
>   Materials_DetailsAvailable_Qty.xlsx ──bootstrap──▶ sme_inventory_seed  (SME-owned baseline; NEVER ERP inventory)
>                                                              │
>   ERP receipts ──┐                                          │  joined at READ time only
>   ERP consumption ┼─ via SAP_Code → inventory.Material_Code ─┤  (nothing is stored)
>                  ┘                                           ▼
>                                              sme_materials_view.available_qty
>                                       = Initial_Available_Qty + Σreceipts − Σconsumption
> ```
> `get_sme_inventory_view()` (in `database.py`) reads `sme_materials_view` and feeds `load_all()`, which feeds EVERY analytical tab AND the SK consumption form in `daily_issue_log.py`. So fixing the math in the view fixes the whole portal at once. ERP `inventory` is never written by SME code; SME quantities are never written into ERP `inventory`. (See RULE 2 at the top of this file.)
>
> **`pyzipper` is GONE.** R17 originally shipped encrypted-zip download helpers using `pyzipper`. R18 ripped that out entirely — SME report downloads are now raw `.xlsx`/PDF via the standard ERP download path (the `st.download_button` monkey-patch is scoped inside `page_material_estimator` via try/finally so other portals are unaffected). Do not re-introduce `pyzipper` or a module-level download-button patch.
>
> **Bootstrap:** `python3 scripts/sme_bootstrap.py --site-id <SITE>` loads all three Excel files (`scripts/sme_seed_data/*.xlsx`) into `sme_recipe` (global), `sme_equipment` (site), `sme_inventory_seed` (global), and seeds `sme_sqm_progress`. Default `INSERT OR IGNORE` preserves manual Master-Data edits; `--force` re-baselines from Excel. Run once per `Site_ID`.

Round 20 landed the literal SME drop-in with the analytical tabs (Dashboard, Selective Entry, Session Order, Location Report, Equipment Report, Execution Plan, Total Overview) working end-to-end. Tab 8 (Master Data) was the last block that still needed an architectural treatment because it does CRUD — and the R17 compat VIEWs are read-only.

### Four issues fixed

| Issue | Root cause | Fix |
|---|---|---|
| **Equipment radio: "Missing Submit Button" warning** | `_get_autofill()` raised `IndexError` mid-form-build (Issue 2 below) which severed the form before its `st.form_submit_button` line rendered — Streamlit then warned that the form had inputs but no submit. | Resolved as a side-effect of Issue 2's fix (autofill no longer crashes). |
| **`IndexError: No item with that key` at `eq_row["Lining_System"]`** | The R17 `sme_equipment` table was minimal (6 lining columns). The SME's `_get_autofill` queries `"Lining_System", "Material Spec.", "Lining_Area/location"` as quoted identifiers; the `equipment` compat VIEW didn't expose them. | Phase A ALTERs `sme_equipment` with all 15 legacy Excel columns. The `equipment` VIEW now aliases them with the dotted/slashed identifier names the SME hard-codes. |
| **`Database error: cannot modify recipe because it is a view`** (and same for `equipment`, `inventory`, `sqm_progress`) | The Master Data tab was doing raw `INSERT INTO equipment` / `INSERT INTO recipe` / `INSERT INTO inventory` / `UPDATE … SET` / `DELETE FROM …` against the compat VIEWs. SQLite rejects writes to views. | Phase A adds 9 helpers (`insert/update/delete_sme_equipment`/`_recipe`/`_inventory_seed`). Phase C rewires the 4 raw-SQL write sites in Tab 8 to dispatch on `db_table` and call those helpers. |
| **Materials_DetailsAvailable_Qty radio floods with ERP inventory clutter (1,200+ rows of bolts/gloves/etc.) and Available/Ordered qty = 0** | `TABLE_MAP` pointed at the ERP `inventory` table. Returns every SAP_Code, not just SME materials. CNCEC qty=0 was a separate symptom of the same root: no SME-specific seed for CNCEC. | New table `sme_inventory_seed` (SME-owned baseline). New `sme_materials_view` joins it against ERP `receipts`/`consumption` so `Available_Qty = Initial + Received − Consumed`. Bootstrap loads `Materials_DetailsAvailable_Qty.xlsx` into the seed. Tab 8 now reads from this view. |

### Why the inventory isolation model is load-bearing

The user explicitly directed: *"don't mingle with the Inventory of the ERP, we will leave those two separate."* The catch surfaced before drafting: R18's SK SME-consumption flow already debits ERP `inventory.Available_Qty` (after Material_Code aggregation, no SME columns leak into the ERP ledger). Receipts likewise flow through ERP `receipts`. If the SME Master Data view read only a frozen seed table, the displayed Available_Qty would never reflect actual consumption or new receipts.

The solution: a 3-column derivation at view time, executed entirely in SQL. The seed is SME-owned and immutable from the ERP side; the live qty is derived from ERP ledger movements that are already tagged via the `is_sme` flag (R18). This means:

* **Master Data CRUD writes** target `sme_inventory_seed` only — they never touch ERP `inventory`.
* **Master Data reads** show seed columns + derived live columns side-by-side.
* **R18 consumption / Logistics receipts** continue to flow through ERP unchanged; their effect appears automatically in `sme_materials_view.available_qty`.
* **Future site rollups** are just additional `sme_inventory_seed` rows + the same view math — no refactor required.

### Schema deltas (Phase A)

* `sme_equipment` +15 columns: `Sl_No`, `Project`, `WBS_No`, `IO_No`, `Drawing_No`, `Design`, `Dia_L`, `Ht_W`, `Equipment_Total_SQM`, `Remaraks`, `Lining_System_Short_Name`, `Lining_Type`, `Lining_System`, `Material_Spec`, `Lining_Area_Location`. (Note: the SME's Excel typo "Remaraks" is preserved verbatim because the upstream UI references it that way.)
* `sme_recipe` +8 columns: `Sl_No`, `Substrate`, `System_Keys`, `Lining_Thickness`, `Lining_System`, `Lining_Type`, `Material_Description`, `Package_Size`.
* New table `sme_inventory_seed`: `Material_Code` PK, `Material_Name`, `Item`, `Vendor`, `Purchasing_Document`, `Document_Date`, `Nature`, `UOM`, `Initial_Available_Qty`, `Initial_Ordered_Qty`, plus timestamps.
* New view `sme_materials_view`: aliases the seed columns to lowercase snake_case + computes `received_qty`, `consumed_qty`, `available_qty` from `receipts`/`consumption` via `SAP_Code → inventory.Material_Code` joins.
* `equipment` and `recipe` compat VIEWs rewritten to expose every aliased column the SME UI references (PascalCase / dotted / slashed identifiers preserved).

### Helper API (Phase A)

```python
D.insert_sme_equipment(row: dict, site_id: str) -> int      # returns new id
D.update_sme_equipment(eq_id, changes, site_id=None) -> int # rowcount
D.delete_sme_equipment(eq_id, site_id=None) -> int          # cascades sme_sqm_progress

D.insert_sme_recipe(row) -> int
D.update_sme_recipe(rec_id, changes) -> int
D.delete_sme_recipe(rec_id) -> int

D.insert_sme_inventory_seed(row) -> int   # INSERT OR REPLACE on Material_Code
D.update_sme_inventory_seed(material_code, changes) -> int
D.delete_sme_inventory_seed(material_code) -> int
```

All accept UI-shaped form keys (lowercase, dotted, slashed) and translate them via per-table `_SME_*_COL_MAP` dicts to the PascalCase table columns. Unknown keys are dropped silently so PRAGMA-driven dynamic forms don't error on derived view columns.

### Bootstrap changes (Phase B)

* Three Excel files (`For_1_SQM.xlsx`, `Equipment.xlsx`, `Materials_DetailsAvailable_Qty.xlsx`) now load **every column** the SME UI cares about, not just the 6/3 the engine needed.
* New `_clean_inventory_seed()` aggregates the materials Excel by `Material_Code` (multiple PO lines → one row per code; sums qty, picks first non-null vendor/PO/etc.).
* **Default mode is now `INSERT OR IGNORE`** so manual edits made via the Master Data tab survive a re-bootstrap. New `--force` flag restores the prior wipe-and-reload behavior for explicit re-baseline from Excel.
* For CNCEC qty=0: run `python3 scripts/sme_bootstrap.py --site-id CNCEC` once. This populates `sme_inventory_seed` (global) and `sme_equipment` (site-scoped) for CNCEC. Available_Qty then reflects HQ seed + any CNCEC-specific receipts/consumption that have flowed through ERP.

### Portal rewiring (Phase C)

The four raw-SQL write sites in Tab 8 of `material_estimator_portal.py`:

* **Equipment Smart Entry save (~line 7000)** — was `INSERT INTO equipment … + INSERT INTO sqm_progress … ON CONFLICT`. Now: `D.insert_sme_equipment(_all_vals, site_id=_login_site)` + `D.upsert_sme_sqm_progress(...)`. Site_ID auto-derived from `st.session_state["_login_site_id"]`.
* **Dynamic add form (~line 7080)** — dispatches on `db_table`: `recipe → D.insert_sme_recipe`, `sme_materials_view → D.insert_sme_inventory_seed`. Skips derived view columns (`received_qty`, `consumed_qty`, `available_qty`, `ordered_qty`) from the form.
* **Cell-edit save (~line 7212)** — dispatches on `db_table`: `equipment → D.update_sme_equipment`, `recipe → D.update_sme_recipe`, `sme_materials_view → D.update_sme_inventory_seed`.
* **Bulk delete (~line 7244)** — same dispatch pattern. `D.delete_sme_equipment` internally cascades `sme_sqm_progress`.

`TABLE_MAP` updated: `"Materials_DetailsAvailable_Qty" → "sme_materials_view"` (was `"inventory"`). `PK_MAP` recognizes the view's `material_code` PK.

### R20.5 regression tests (11/11 green)

1. `check_r20_5_sme_equipment_columns` — all 15 new columns present
2. `check_r20_5_sme_recipe_columns` — all 8 new columns present
3. `check_r20_5_sme_inventory_seed_table` — table exists with correct schema + Material_Code PK
4. `check_r20_5_sme_materials_view_math` — end-to-end: seed 100 + receipt 25 − consumption 30 = `available_qty` 95
5. `check_r20_5_equipment_view_aliases` — VIEW exposes `Lining_System`, `Material Spec.`, `Lining_Area/location` (dotted/slashed identifiers preserved)
6. `check_r20_5_recipe_view_lining_type` — `recipe.lining_type` round-trips real Lining_Type values
7. `check_r20_5_crud_helpers_exist` — all 9 helpers callable
8. `check_r20_5_col_translation` — helpers translate lowercase/dotted/slashed UI keys → PascalCase table columns
9. `check_r20_5_no_raw_view_writes` — slices Tab 8 region, regex-scans for residual `INSERT INTO equipment|recipe|inventory|sqm_progress|sme_materials_view`
10. `check_r20_5_table_map_materials_view` — TABLE_MAP points to `sme_materials_view`; stale `"inventory"` mapping absent
11. `check_r20_5_equipment_smart_entry_helpers` — confirms `D.insert_sme_equipment`, `D.upsert_sme_sqm_progress`, `D.insert_sme_recipe`, `D.insert_sme_inventory_seed` all called in the portal

### Test posture (R20.5 — superseded by the R20.5.1 LOCKED baseline in §2W.1)

Round 17 13/13 + Round 18 13/13 + Round 20 11/11 + Round 20.1 4/4 + Round 20.5 11/11 = 52 SME-related checks green; total 520 / 537 at the end of R20.5. **R20.5.1 then added +2 checks → final 522 / 539 (54 SME-related green). See §2W.1 for the authoritative current baseline.**

### Deployment

1. `git pull` — picks up Phase A (database.py), Phase B (scripts/sme_bootstrap.py + sme_seed_data refresh), Phase C (material_estimator_portal.py), Phase D (bug_check.py + handoff.md).
2. `python3 bug_check.py` → **520 / 537**.
3. **One-time** per site needing CNCEC fix: `python3 scripts/sme_bootstrap.py --site-id CNCEC` (or `--force` to re-baseline an existing site from refreshed Excel).
4. Restart Streamlit. All 4 Tab 8 issues resolved: form renders with Save button, autofill panels populate, Add/Edit/Delete work for Equipment / Recipe / Materials Details, and Materials Details shows the ~30 SME materials with live Available_Qty derived from ERP movements.

## 2W.1. R20.5.1 — two bugs surfaced after the user ran the bootstrap (live render)

These two are the source of **RULE 1** and the reinforcement of **RULE 2** at the top of this file. Each one cost a debugging round; do not regress them.

| Symptom | Root cause | Fix |
|---|---|---|
| **Master Data → "No records found" for ALL three radios** (Equipment / Recipe / Materials) despite 75 / 86 / 22 rows in the DB | Tab 8's View/Edit/Delete read did `SELECT * FROM {db_table} ORDER BY rowid`. After R20.5, `db_table` resolves to a **VIEW** — and VIEWs have no implicit `rowid`. SQLite raised `no such column: rowid`, swallowed by the surrounding `except Exception: view_df = pd.DataFrame()` into an empty grid. | Order by a real per-table column via `_ORDER_COL` map (`equipment`/`recipe` → `id`, `sme_materials_view` → `material_code`) with a no-ORDER fallback. **→ RULE 1.** |
| **Every SME analytical tab (Dashboard Full Material Balance, Equipment Report, etc.) showed Available/Ordered = 0** | `get_sme_inventory_view()` still sourced Available from ERP **live stock** and Ordered from ERP **open POs** — both 0 for SME materials, which live in `sme_inventory_seed`, not ERP `inventory`. R20.5 only rewired Tab 8's Materials radio to the new model; the rest of the portal (everything fed by `load_all()`) was missed. | Rewrote `get_sme_inventory_view()` to read `sme_materials_view` (the approved `Initial + Received − Consumed` math). Now the **whole** SME surface — Dashboard, Equipment Report, Session Order, Execution Plan, Total Overview, AND the SK consumption form in `daily_issue_log.py` (shared caller) — reflects the SME inventory file. Recipe master still fallback-enriches name/UOM/Nature. **→ RULE 2.** |

`check_r17_sme_inventory_view` updated to seed the new source (same 130/15 expected outputs). +2 R20.5.1 regression checks (`check_r20_5_1_master_data_no_order_by_rowid`, `check_r20_5_1_inventory_view_seed_sourced`).

## 2W.2. R20.5.2 — two live crashes after the chat-history clear (SK page + Location Report)

Found on first render after pulling the R20.5.1 fixes. Both are "deleted-package / stale-state" footguns; each gets a regression guard.

| Symptom | Root cause | Fix |
|---|---|---|
| **SK Consumption page crashes: `ModuleNotFoundError: No module named 'pages_internal.material_estimator'`** at `daily_issue_log.py` → `_render_sme_consumption_form` | The line `from pages_internal.material_estimator.widgets import days_of_continuation_block` referenced the **R19 package that R20 deleted** (commit `1da612b`). The import sat at the top of the form function, so the whole SK Consumption page crashed on entry. R20's deletion sweep missed this one call site. | **Vendored** `days_of_continuation_block` verbatim (recovered via `git show 1da612b~1:pages_internal/material_estimator/widgets.py`) as a module-level function in `daily_issue_log.py`; removed the dead import. It was the *only* live reference to the deleted package. |
| **Admin → Material Estimator → Location Report crashes: `IndexError: single positional indexer is out-of-bounds`** at `eq_master[eq_master["Equipment_Tag_No."]==tag].iloc[0]` (portal ~line 5134) | `st.session_state.loc_order[loc]` persists the drag-to-reorder ordering across reruns. It's seeded **once** from `eq_master` and never reconciled. After a re-bootstrap (or any Master Data edit) changed the equipment master, the persisted order still held tags that no longer exist in `eq_master` → empty frame → `.iloc[0]` raised. A *fresh* session wouldn't hit it; a long-running session that survived a data change does. | **Reconcile** `loc_order[loc]` against the current `eq_master` location tags before use: keep valid tags in their saved order, drop stale ones, append any new ones. Generalizes RULE 3-adjacent hygiene: persisted UI state must be reconciled against reloaded data. |

> **⚠️ Lesson for future sessions (added to the canon):** When you delete or rename an SME module, **grep the entire repo for imports of it** (`grep -rn "pages_internal.material_estimator" --include=*.py`) — a single missed lazy/inline import inside a function body won't surface until that code path renders at runtime, and bare-mode import tests skip Streamlit-gated branches. And any feature that stashes selections/orderings in `st.session_state` must **reconcile that state against freshly-loaded data** every run, never trust it blindly.

+3 R20.5.2 regression checks: `check_r20_5_2_no_deleted_pkg_import` (repo-wide scan for live imports of the deleted package), `check_r20_5_2_doc_block_vendored` (loads `daily_issue_log`, asserts `days_of_continuation_block` is defined + no-ops on empty input), `check_r20_5_2_loc_order_reconciled` (asserts the reconciliation code is present in the Location Report).

### Final SME test posture (LOCKED baseline)

**`python3 bug_check.py` → 525 passing / 17 pre-existing env failures (542 total).** The 57 SME-related checks are all green: **R17 13/13 + R18 13/13 + R20 11/11 + R20.1 4/4 + R20.5 11/11 + R20.5.1 2/2 + R20.5.2 3/3.** The 17 failures are environment-only (missing `dotenv`, `bcrypt`, `fpdf`, `pdfplumber` on this machine) and are unrelated to application logic — they predate all SME work. **Any future change must keep the 525 green.**

### 🔒 SME integration status: FEATURE-COMPLETE & FROZEN

The SME ↔ ERP merge is done. Future development pivots to **the ERP project's own roadmap (§3 below)**. Touch SME code only to fix a *proven* regression against the 525 baseline, and always add a regression test for the fix. If a task requires understanding the *original standalone* SME app's internals beyond what's vendored here, **ask the user for the original SME project files — do not hallucinate** (RULE 4).

---

## 2Y. Workstream C (Docker / Deployment) — 🟢 ACTIVE (unpaused 2026-06-28)

Resumed after the Man-Hour feature shipped (§2Z). **All infra is server-only / additive** — the Mac dev path (`streamlit run main.py`) is byte-for-byte unchanged, verified each round.

**Latest step — Certbot + Nginx TLS wiring (single-command first boot):**
- `docker-compose.yml` now has a **`certbot`** service (auto-renew loop, no host ports) + **`certbot-etc`** / **`certbot-www`** volumes; **nginx** publishes **:80 + :443**, mounts the cert volumes, uses `docker/nginx/gihub.ssl.conf`, and runs a **6h reload loop** so renewed certs apply without a restart.
- **`scripts/init-letsencrypt.sh`** solves the cold-boot chicken-and-egg: seeds a self-signed **dummy cert** so nginx can start → brings nginx up → deletes dummy → issues the **real** Let's Encrypt cert via webroot → reloads. Run **once** on the live box (`staging=1` default; flip to `0` for trusted certs). Thereafter ordinary `docker compose up -d` works and certbot auto-renews.
- **Pre-reqs flagged in the script:** DNS A-record → server IP; Cloudflare set to **DNS-only (grey)** or **Full (strict)** so HTTP-01 isn't blocked; firewall opens 80/443.
- Domain/email are EDIT-THESE vars (default `giinventory.com`).

**Done & committed (earlier steps):**

**Done & committed:**
- **Docker foundation** — `Dockerfile.streamlit`, `Dockerfile.fastapi`, `docker-compose.yml` (5 services: nginx/streamlit/fastapi/ollama/backup on the `gi-net` bridge; only nginx binds host ports), `requirements-server.txt`, `.dockerignore`, `docker/entrypoint-streamlit.sh` (symlinks DB+uploads into the `gi-data` volume — zero app-code changes).
- **16 GB tuning** — server changed to **Hetzner CPX42 (8 vCPU / 16 GB / 320 GB)**; Ollama pinned via `OLLAMA_MAX_LOADED_MODELS=1` + `OLLAMA_NUM_PARALLEL=1` so only one ~5 GB model is resident at a time.
- **CI/CD** — `.github/workflows/deploy.yml` (push-to-main → SSH → `git reset --hard origin/main` → build → `compose up -d`).
- **Nginx + TLS** — `docker/nginx/gihub.conf` (HTTP) + `docker/nginx/gihub.ssl.conf` (Let's Encrypt for `giinventory.com`, with the certbot bootstrap documented inline).
- **Meta WhatsApp webhook** — `services/rag_api.py` (FastAPI RAG sidecar wrapping `ai/manual_qa`) + `services/whatsapp_webhook.py` (pure parser/router: extracts sender phone, body, name, `phone_number_id`; handshake + `X-Hub-Signature-256` verification; stub response router). **+5 bug_check.py "Workstream C" checks, all green.**

**Frozen decisions** live in the Phase 6 register's "🔁 SUPERSEDED by Workstream C" block (public Hetzner VPS + Nginx + Meta Cloud API; A3 bcrypt-RBAC and A4 SQLite still in force; CV gate OFF for v1).

**When resumed:** provision the box → `git clone` → smoke test → certbot first-issue → register the Meta webhook (`https://giinventory.com/api/whatsapp/webhook`) → wire the Meta *sender* provider + a dedicated worker service. Offsite-backup gap (Hetzner Storage Box) and the server-side git deploy key are the two go-live to-dos.

---

## 2Z. Man-Hour & Labor Tracking Integration — 🟢 ACTIVE (planning, 2026-06-28)

New workstream: track **labor** the way the SME tracks **material**. Source-of-truth schema came from the user's `to john_Attendance.xlsx` (2 sheets: `ADD EMPLOYEE` roster template + `SAR` daily attendance, 209 rows / 22 employees / 2026-05-16→06-20).

**Goal:** Employee master (per site; OWN/Supply + Company) → daily timesheets (in/out → Total/Normal/OT hours + SQM done, individual or team-distributed) → a **Man-Hour Estimator** (required MH per Location/Equipment-Tag/System-Code) → an **Estimate-vs-Actual variance dashboard** with reason capture and an employee-wise "where did each person work, date-wise" view.

**Key architectural finding (drives the design):** the work dimensions already exist in the **FROZEN SME tables** — `sme_equipment` carries `Equipment_Tag_No`, `Location`, `Lining_System_Code`; `sme_recipe` is the system-code catalogue; `sme_sqm_progress` holds `Done_SQM`. The man-hour feature therefore **reads those SME tables read-only** (dropdowns + SQM context) and **writes only to NEW `mh_*` tables** — never modifying the frozen SME data (RULE 2 spirit). Proposed prefix `mh_` (man-hour), kept distinct from `sme_` to avoid the SME freeze tripwires.

**Decisions locked (2026-06-28):**
1. **Employee master** = new **`mh_employees`** roster (Code, Name, Designation, Worker_Type[OWN|Supply], Company, Site_ID), with an *optional* `linked_id_number` → `employees.ID_Number` for OWN/GI workers. Handles Supply/DMC subcontractors that aren't in the ERP `employees` master.
2. **SQM** = team entry per Date/Equipment/System in new **`mh_production`**, auto-distributed to workers (**even** *or* **by-hours**, with per-person override). Per-worker share lands in `mh_timesheets.Allocated_SQM`.
3. **Hours math** = **8h normal + 1h unpaid break.** `Total = (Out−In) − 60min` ; `Normal = min(Total, 8)` ; `OT = max(0, Total−8)`. (Your file's 07:30–16:30 → 9h gross − 1h = 8h net = Normal 8 / OT 0.)
4. **UI** = new standalone portal **"🕒 Man-Hours"**, RBAC-locked to **{hod, admin}** via `_EXACT_ROLE_PAGES` (mirrors the SME estimator lock). Tabs: Employees · Daily Timesheet · Man-Hour Estimator · Estimate-vs-Actual · Employee-wise.

**Status (2026-06-28): SHIPPED.** Tables `mh_employees`, `mh_timesheets`, `mh_production`, `mh_manhour_estimates`, `mh_variance_notes` + view `v_mh_estimate_vs_actual` (all `mh_` prefixed, self-heal in `init_db`). `database.py` Man-Hour helper section: `compute_mh_hours`, employee/timesheet CRUD, team-SQM distribution (even/by_hours), estimator + variance, `parse_attendance_workbook` + `import_mh_attendance` (shared by UI upload + CLI). New **`pages_internal/manhour_portal.py`** — standalone "🕒 Man-Hours" page exact-locked to {hod, admin} in `main.py` + `config.PAGE_ACCESS`; 5 tabs (Employees · Daily Timesheet [Excel upload w/ **replace-by-date** + manual per-day batch grid] · Estimator · Estimate-vs-Actual w/ reason capture · Employee-wise). Reads `sme_equipment`/`sme_recipe` read-only for Equipment-Tag/Location/System-Code dropdowns (note the column is `Equipment_Tag_No.` *with a dot*). `scripts/manhour_bootstrap.py` now delegates to the shared helpers. **Proven:** bootstrap loaded 22 employees + 209 rows for CNCEC; **bug_check 554/0** (10 Man-Hour checks) · **UI crawler 21/0** (page renders for HOD+admin, hidden from others). The live DB (`gi_database.db`) was **untracked from git** this session (commit `801f859`) so production data stops being committed. **Nothing in the SME drop-in, EOD path, or material ledger was touched.**

---

## 3. Remaining Features — Prioritized

### P0 — Operational gaps that could surprise users

1. **Maintenance Mode actually blocks non-admins at login**
   - `app_settings.maintenance_mode` is written by the toggle but `auth.py` doesn't read it
   - Need: `if get_app_setting("maintenance_mode") == "1" and user.role != "admin": show downtime page`

2. ~~**Returnable overdue WhatsApp — verify scheduler**~~ ✅ Confirmed wired. `whatsapp_worker.run_worker_loop()` calls `check_overdue_returnables()` every 60 s alongside `process_queue()`. Started from `main.py:78` via `@st.cache_resource`.

3. **WhatsApp queue retries**
   - Failed messages stay `status='failed'` with no automatic retry
   - Suggest: admin button "Retry all failed" + max-3-attempts auto-retry in worker

### P1 — Inventory discipline (what's left from original audit)

4. **Stock reservations on approved cross-site requests**
   - Today: an approved transfer doesn't earmark stock; a store keeper can consume it before it ships
   - Fix: `reservations` table + reduce available stock by sum of pending reservations in `v_site_stock`

5. **Hard FEFO enforcement (currently advisory)**
   - The override flow exists but the system never *blocks* a non-FEFO consumption — it just records the reason
   - Decision needed: do you want silent allow-and-log (current) OR hard-block-without-override?

6. **UoM conversion** (buy in Box of 100, issue in Pcs)
   - Today: 1 UoM per item, no conversion
   - Adds: `uom_conversions(SAP, from_uom, to_uom, factor)` table + form in Receive Material + DB editor

7. **Bin/location within a site**
   - Today: stock is "at Site A". No shelf/bay/bin
   - Becomes painful past ~1,000 SKUs per warehouse. Adds: `bins` table + `location` column on receipts/consumption

8. **Auto-PR drafting from below-minimum**
   - Today: shows low-stock alert; HOD types PR manually
   - Adds: button "Auto-draft PRs for all below-minimum items" → pre-fills `pr_master` rows ready to send

### P2 — Platform / scale

9. **PostgreSQL migration path**
   - SQLite + WAL handles ~10-25 concurrent comfortably; past that, contention bites
   - Already structured for it: all SQL lives in `database.py`. The PWA FastAPI layer in `pwa/api.py` is the bridge

10. ~~**Master DB Editor save = DELETE then INSERT-all**~~ ✅ **DONE (crash-safety)** — `crash_safe_replace_table()` stages `df` into a temp table (validates every row) then swaps within one transaction; any failure `rollback`s so the ORIGINAL rows are always preserved (never partial/empty). Editor save now calls it. +1 `bug_check` regression proves no data loss on a failed write. `database.py`, `pages_internal/admin_portal.py`. (The deeper immutable-ledger/reversal redesign remains a separate, larger effort — this closes the acute data-loss risk.)

11. **Lot splitting / merging / quarantine UI**
    - Schema supports `Status` transitions but there's no UI for them — admin/HOD has to use Master DB Editor
    - Lot disposal workflow with HOD approval is sketched in the manual but unimplemented

12. **2FA**
    - Manual says "Access Control" tab has 2FA — actually placeholder only. Need TOTP library + `users.totp_secret` column + login challenge

### P3 — Polish / nice-to-have

13. ~~**Scheduled report cron**~~ ✅ **DONE** — `report_schedule_due()` (daily≥1/weekly≥7/monthly≥28 days-elapsed) + `due_report_schedules()`; worker `_maybe_run_report_schedules()` fires due active schedules once/day (generate → archive via the streamlit-free `_run_report`/`_encode_report`/`_save_to_archive`, then `mark_schedule_run`), each per-schedule + whole-sweep guarded so it never crashes the worker. +1 `bug_check` regression. `database.py`, `whatsapp_worker.py`.
14. **Dashboard tile editor** — admin can pick which KPI cards appear in the hero strips
15. ✅ **Per-site Unit_Cost — DONE (additive).** New `inventory_site_costs` table + `get_effective_unit_cost()` (COALESCE site→global→0) + `set_site_unit_cost()`/`get_site_unit_costs()` + Admin → Settings "💲 Per-Site Unit Cost" editor. **All valuation sites now site-aware:** `get_value_by_site()`, `get_total_inventory_value()` (company total sums per-site), `get_inventory_valuation(site_id=…)`, `report_wbs_consumption()` (per-movement site), `report_daily_receipts()`, `report_monthly_summary()` (site-scoped runs). **Zero behaviour change until an override is set.** 2 `bug_check` regressions (resolution + report-level). **Only remaining on global cost by design:** the per-SAP *all-sites* company rollup branch of `get_inventory_valuation(None)` (one cost per SAP across sites — no single site context). `database.py`, `pages_internal/admin_portal.py`.
16. **AI Insights regen scheduling** — currently on-demand only
17. ~~**Export the USER_MANUAL.md to PDF programmatically**~~ ✅ **ALREADY DONE** (verified 2026-07-01) — `build_manual_pdf()` / `build_role_manual_pdf()` in `build_manual_pdf.py` (fpdf2, no pandoc) + Admin → Settings "⬇️ Download GI_Hub_User_Manual.pdf" / "📥 Download Role Manuals" buttons already wired.

### New (from 2026-06 round)

18. **Twilio paid number** vs sandbox — sandbox is fine for the rollout but requires every recipient to opt-in. A WhatsApp Business Account + paid number removes that step and unlocks template messages, ~$5–10/month + Meta approval.
19. ~~**`uploads/` disk-mirror rotation**~~ ✅ **DONE** — `cleanup_upload_disk_mirror(older_than_days=180, dry_run, root)` deletes stale disk copies + prunes empty dirs (DB BLOBs are authoritative — no document lost). Admin → Danger Zone "Cleanup old upload files" button (shows a live dry-run count, CLEAN-confirm + audit log). +1 `bug_check` regression. `database.py`, `pages_internal/admin_portal.py`.
20. ~~**`pending_returns` cleanup of rejected rows**~~ ✅ **DONE** — new `returns_history` archive table + `archive_rejected_returns(older_than_days=30, by_user)` helper (copy-then-delete, mirroring `rejected_issues_archive`; uses the Phase-2 `days_ago_sql()` for portability). Admin Portal → Danger Zone gains a **"Cleanup rejected returns"** button (type-CLEANUP confirm + `log_audit_action`). Only `status='rejected'` rows older than 30 days move; the `returns` ledger and awaiting-HOD rows are untouched (zero stock-math impact). +1 `bug_check` regression (`check_returns_archive`). `database.py`, `pages_internal/admin_portal.py`.
21. ~~**HOD QR / Return reject reason input**~~ ✅ **DONE** — both the HOD Returns per-card reject and the QR bulk-reject now open a popover with a required reason textbox (mirrors the DN "Decide" required-notes pattern). Reason flows through the existing `reject_return_request` / `reject_qr_request` helpers into `rejection_reason` (columns already existed). Two bug_check assertions strengthened to prove persistence. `pages_internal/hod_portal.py`.
22. ~~**Categories on legacy items**~~ ✅ **DONE** — Admin → Master DB Editor → `inventory` view now shows a warning banner counting items still on `Category='Others'` (or NULL), with an expander listing SAP/Material so the team can backfill. Render-only, additive. `pages_internal/admin_portal.py`.
23. ~~**`Opening_Stock` audit trail**~~ ✅ **DONE** — new `audit_opening_stock_changes(old_df, new_df, by_user)` diffs pre-edit vs saved inventory by SAP_Code and writes one `OPENING_STOCK_EDIT` audit row per changed existing item (new items excluded — that's creation). Wired into the Master DB Editor save (fires before the DELETE/re-insert). +1 `bug_check` regression. `database.py`, `pages_internal/admin_portal.py`.

### New (from v3.0 — procurement chain)

24. ~~**Vendor master maintenance UI**~~ ✅ **DONE** — new Admin → "🏭 Vendors" tab: list (active/inactive toggle), add, edit, activate/deactivate, and Excel bulk import with duplicate detection. Helpers `update_vendor()`, `set_vendor_status()`, `bulk_import_vendors()` (added/skipped/errors). +1 `bug_check` regression. `database.py`, `pages_internal/admin_portal.py`.
25. ~~**Reminder cadence is hardcoded T-2 / T-1 / T-0.**~~ ✅ **DONE** — `get_reminder_offsets()`/`set_reminder_offsets()` read/write `app_settings.reminder_offsets` (JSON, normalized non-neg-int desc, default [2,1,0]); `sweep_delivery_reminders()` now drives its cadence from it (canonical T-2/1/0 event keys preserved, custom offsets get generic key+label). Admin → Settings "🔔 Delivery Reminder Cadence" input. +1 `bug_check` regression. `database.py`, `pages_internal/admin_portal.py`.
26. **Per-warehouse SLA dashboards.** Throughput report exists but no live "warehouse health" page. Add: average ack time, average receive time, partial-delivery rate, RL/BL split ratio per WH.
27. **Mobile-optimised warehouse PWA.** Warehouse floor users would benefit from a barcode-scanner-first PWA for receive + DN preparation. PWA framework already lives in `pwa/`.
28. ~~**Force-close UNDO window.**~~ ✅ **DONE** — `force_close_target()` now snapshots exact prior state (PO/PR/line statuses) as JSON into `po_force_closures.prior_state`; `undo_force_close(closure_id, by_user, within_hours=24)` restores it verbatim, refuses double-undo + past-window, sets `reverted_at/by`. `get_undoable_force_closures()` + Logistics Force-Close tab "↩️ Undo" panel. Self-heal adds `prior_state/reverted_at/reverted_by`. +1 `bug_check` regression. `database.py`, `pages_internal/logistics_portal.py`.
29. ~~**Email path deprecation timer.**~~ ✅ **DONE** — `get_procurement_adoption()` (PRs past `site_draft` vs all) + `procurement_email_deprecated(threshold=80%)`; HOD PR tab shows a live adoption caption, escalating to a "🚫 slated for removal" warning on the legacy email/PDF buttons once adoption ≥80%. +1 `bug_check` regression. `database.py`, `pages_internal/hod_portal.py`.
30. ~~**DN line auto-FEFO.**~~ ✅ **DONE** — `suggest_fefo_lot_for_material(material_code, site_id)` maps Material_Code→SAP_Code (verified clean 1:1 in inventory) then returns the earliest-expiry OPEN lot at the destination site. Warehouse "Prepare DN" gains an opt-in "🔎 Auto-suggest FEFO lots" checkbox that pre-fills Lot_Number+Expiry_Date per line (fully editable; editor key includes the flag so toggling rebuilds). +1 `bug_check` regression. `database.py`, `pages_internal/warehouse_portal.py`.
31. **PO PDF extractor — multi-vendor template support.** Current `process_po_pdf()` is calibrated against the GI sample layout. Different vendors send different layouts. Build a template registry where Logistics can train new layouts in-app.
32. **Procurement chain analytics in AI Insights.** Add an LLM probe for "stuck POs", "vendor delay patterns", "RL/BL throughput imbalance".

---

## 4. How to Run / Develop

```bash
# Run app (worker auto-starts as a background thread via @st.cache_resource)
streamlit run main.py

# Standalone worker (only needed if you want to run the worker without
# the Streamlit UI, e.g. for offline desktop usage)
python whatsapp_worker.py

# Pytest — 315 expected, ~22 s
.venv/bin/python -m pytest -x --tb=short -q

# Automated bug harness — 114 checks, ~5 s, writes BUG_REPORT.md
python bug_check.py               # quiet
python bug_check.py --verbose     # streams each check

# Smoke a specific helper without Streamlit overhead
.venv/bin/python -c "
import database as d
conn = d.get_connection(':memory:')
d.init_db(conn)
# … do stuff …
"
```

### Twilio (cloud) — one-time setup

Add to Streamlit Cloud → App Settings → Secrets:

```toml
[twilio]
account_sid  = "AC..."
auth_token   = "..."
from_number  = "whatsapp:+14155238886"
```

Every recipient must join the Twilio sandbox once by sending `join <code>` to `+1 415 523 8886` on WhatsApp. Sandbox is free; a paid Twilio number removes the join step but needs an approved WhatsApp Business Account.

### Production hosting — full comparison + giinventory.com playbook

You own `giinventory.com` on Cloudflare. That's the front door — pick a back door (where the app actually runs) from this table.

| Option | Always-on without Mac? | DB persists? | WhatsApp | AI / Ollama | Free? | Best for |
|---|---|---|---|---|---|---|
| **A. Self-host on Mac + Cloudflare Tunnel** | ❌ Mac must stay on | ✅ on Mac disk | pywhatkit OR Twilio | ✅ local (when Mac on) | Yes (~$8 domain only) | True warehouse rollout with Mac as the server |
| **B. Fly.io free tier + Cloudflare DNS** | ✅ Always-on cloud VM | ✅ Fly volume + nightly B2 backup | Twilio (cloud) OR tunneled pywhatkit | ❌ unless Mac+Tailscale | Yes | Always-on, no Mac dependency |
| **C. Streamlit Cloud + Litestream → B2** | ✅ but sleeps weekly | ✅ via Litestream replication | Twilio | ❌ unless Mac+Tailscale | Yes | Minimum-effort cloud, accept brief restarts |
| **D. Render free tier** | ⚠ sleeps after 15 min, wakes ~30 s | ✅ persistent disk add-on | Twilio | ❌ unless Mac+Tailscale | Yes (1 sleeping service) | Low-traffic, idle-tolerant |
| **E. Cheap VPS** ($5/mo Hetzner/Contabo) | ✅ Always-on, no sleep | ✅ permanent | Twilio OR own Ollama+Mac for AI | $5/month + $8 domain | Tightest control, mid-cost | Mid-scale rollout where Mac is awkward |

The honest verdict: **A** is best when the Mac CAN stay on (it can; it's a laptop in a server room with FileVault). **B (Fly.io)** is the best free always-on alternative. **C (Streamlit Cloud + Litestream)** is the easiest if you accept ~5 sec cold-start after weekly auto-restarts. Don't use D for a real warehouse — the sleep wake is painful for SK who scan and walk away.

#### What "safe for our data" actually means (the brief for management)

Same on every option above:

| Risk | Defence | Where it lives |
|---|---|---|
| External attacker | Inbound port = 0 (Tunnel/proxy). No public IP, no SSH port, no Redis/Postgres exposed. | Network layer |
| Eavesdropping in transit | TLS 1.3 end-to-end. Cloudflare provides the cert. | Wire |
| Eavesdropping at the edge | Cloudflare cannot decrypt the *body* — they terminate TLS at their POP and re-encrypt to your origin via the Tunnel's mutual-TLS connection. Body is opaque to them. (Documented at https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) | Cloudflare ↔ origin |
| Unauthorised user | Cloudflare Access email allow-list at the edge (FREE ≤50 users) + the app's own bcrypt login. Two locks, not one. | Identity layer |
| Data leak via screenshot | Audit log records every consequential action with username + timestamp. Reviewable in Admin Portal → Audit Logs. | App layer |
| Data leak via stolen Mac (option A) | macOS FileVault (AES-256). Without the disk password the SQLite file is unreadable. Backups (next row) are independent. | Disk layer |
| Disk failure / accidental delete | Nightly backups to **two** independent destinations (e.g. iCloud Drive + Backblaze B2). 14-day retention. Restore = `sqlite3 db ".restore <file>"`. | Backup layer |
| Compliance / data residency | Data lives on YOUR machine (Mac, Fly Frankfurt, Streamlit US — you pick) — not on a vendor-owned DB. Full deletion at will. | Architecture |

If management asks "where does the data go when we type into the app?" the chain is: **browser → TLS → Cloudflare POP → encrypted Tunnel → your origin's local SQLite file**. The data never sits on anyone else's database service.

#### Daily backup options (free, pick any two)

| Destination | Free quota | Setup time | Restore complexity |
|---|---|---|---|
| **Backblaze B2** | 10 GB free, 1 GB/day egress | 15 min (rclone or `b2 sync`) | One `b2 download-file-by-name` |
| **Cloudflare R2** | 10 GB free, no egress charges | 20 min (rclone with S3 endpoint) | One `aws s3 cp` (R2 is S3-compat) |
| **Private GitHub repo** | Unlimited <100 MB files | 5 min (git commit cron) | `git pull` and copy in |
| **Google Drive** via rclone | 15 GB on personal account | 10 min (rclone config; one-time OAuth) | `rclone copyto` |
| **iCloud Drive** (Mac only) | 5 GB free | 0 min (it's already mounted) | Drag and drop |

Recommended pair: **iCloud Drive (always there) + Backblaze B2 (off-Apple, off-Mac, off-Cloudflare)**. If any one party (you, Apple, Cloudflare) is compromised, the other two snapshots survive.

#### Setting up giinventory.com — pick A, B, or C and follow

##### Path A — point giinventory.com at your Mac (self-host)

You already finished the tunnel install. The DNS step inside Cloudflare's dashboard:

1. **Dash → giinventory.com → DNS → Records** (you're already here per your screenshot).
2. **Add record** → Type `CNAME` → Name `gi` (so URL is `gi.giinventory.com`) or `@` (apex `giinventory.com`) → Target: leave for the tunnel command.
3. Actually skip step 2 and let `cloudflared` do it for you:
   ```bash
   cloudflared tunnel route dns gi-hub gi.giinventory.com
   ```
   Refresh the Cloudflare DNS page — a CNAME for `gi` appears automatically, pointing at `<tunnel-uuid>.cfargotunnel.com`. **Proxied** (orange cloud) should be ON.
4. Visit `https://gi.giinventory.com` — app loads.

##### Path B — point giinventory.com at Fly.io (always-on, no Mac)

1. **Sign up at fly.io** (free, no credit card for the smallest tier).
2. **Install flyctl locally:** `curl -L https://fly.io/install.sh | sh`
3. In the repo root:
   ```bash
   fly launch --no-deploy           # asks: app name → gi-hub, region → fra (Frankfurt closest to KSA)
   fly volumes create gi_data --size 1 --region fra
   ```
4. Edit the generated `fly.toml`:
   ```toml
   [[mounts]]
     source = "gi_data"
     destination = "/data"
   [env]
     STREAMLIT_SERVER_PORT = "8080"
     # gi_database.db will live at /data/gi_database.db
   ```
5. Add a tiny `Dockerfile`:
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   ENV STREAMLIT_SERVER_HEADLESS=true
   CMD ["streamlit", "run", "main.py", "--server.address=0.0.0.0", "--server.port=8080"]
   ```
6. `fly deploy`. After ~3 min, Fly prints `https://gi-hub.fly.dev`.
7. **Point giinventory.com at Fly:** Cloudflare dash → DNS → Add record → Type `CNAME` → Name `gi` → Target `gi-hub.fly.dev` → **Proxied** ON.
8. In Fly: `fly certs create gi.giinventory.com` — Fly auto-issues a TLS cert via Cloudflare's CDN.

Cost: $0 until you exceed 3 small VMs or 160 GB egress/month — you won't.

##### Path C — point giinventory.com at Streamlit Cloud + Litestream

1. Add **Litestream** sidecar to the repo. Create `litestream.yml`:
   ```yaml
   dbs:
     - path: /mount/src/cncec-project/gi_database.db
       replicas:
         - type: s3
           bucket: gi-hub-db-backup
           path: gi_database.db
           endpoint: https://s3.eu-central-003.backblazeb2.com
           region: us-east-1
   ```
2. In Streamlit Cloud → Secrets, add B2 credentials:
   ```toml
   [s3]
   access_key_id     = "..."
   secret_access_key = "..."
   ```
3. Modify `main.py` to call `litestream restore -if-replica-exists` before `init_db()` on startup, then `litestream replicate &` to mirror writes.
4. Point Cloudflare DNS: dashboard → DNS → Add `CNAME` → `gi` → `your-app.streamlit.app` → Proxied ON. Streamlit ignores the host header so it works.

This path is the most fragile (Streamlit Cloud restart kills `litestream replicate` and you risk seconds of writes). Only pick C if you absolutely cannot run a VM.

#### Cloudflare Access — email-only login (FREE ≤50 users)

Works for any path above where giinventory.com goes through Cloudflare DNS.

1. Cloudflare dash → **Zero Trust** (left sidebar bottom) → on first visit it asks for a Team name → choose `giinventory` → Free plan (no card).
2. **Access → Applications → Add application** → Self-hosted.
3. Application name: `GI Hub Warehouse`. Application domain: `gi.giinventory.com`. Session duration: 24 hours.
4. **Add a policy** → Name: "Staff allow-list" → Action: Allow → Selector: **Emails** → paste comma-separated emails OR Selector: **Emails ending in @yourcompany.com**.
5. Save. Now anyone hitting `gi.giinventory.com` sees a Cloudflare-branded login page first. They enter their email → receive a one-time PIN → only then reach your app's login. Non-allow-listed emails see "You don't have permission" with no further info leaked.

#### The 8 things to do before sharing the URL with users

1. Change every default password (`admin/admin2026` etc.). They're public knowledge from the repo.
2. Enable FileVault on the host Mac (path A only): System Settings → Privacy & Security → FileVault → On.
3. Set up Cloudflare Access with the staff email list.
4. Add Twilio credentials to App Secrets (cloud paths) so WhatsApp doesn't depend on the Mac for path B/C/D.
5. Test the nightly backup script restores cleanly to a fresh DB before you start collecting real data.
6. Run `python bug_check.py` — make sure it shows 114/114.
7. Run `python build_manual_pdf.py` and email the resulting PDF to management for review.
8. Take a "day 0" backup before letting anyone log in: `cp gi_database.db gi_database_day0_$(date +%Y%m%d).db`.

#### Quick tunnel for testing TODAY without buying anything

While you decide between A/B/C, you can already share a URL with one teammate to validate the app:

```bash
# Terminal 1 — Streamlit
.venv/bin/streamlit run main.py --server.headless true

# Terminal 2 — Cloudflare quick tunnel
cloudflared tunnel --url http://localhost:8501
```

Terminal 2 prints `https://random-words.trycloudflare.com`. Share that URL. When you Ctrl-C, the URL dies. Perfect for "does it work end-to-end" before committing to a permanent setup.

---

### Production hosting — self-host on a spare Mac/laptop + Cloudflare Tunnel

**Turnkey installer:** `host_setup/` ships everything as runnable scripts. After installing `cloudflared` and creating the tunnel, you just run:

```bash
./host_setup/scripts/install.sh
```

That renders four `launchd` plists into `~/Library/LaunchAgents/`, loads them, and shows a coloured status table. See `host_setup/README.md` for the full step-by-step (45 minutes from zero to live, including Cloudflare Access setup for the `@generalindustries.net` email allow-list).

The narrative description below is kept for reference but the scripts are the authoritative path.

---

This is the recommended path for a real warehouse rollout. Free, permanent, no API costs, full control. The trade-off is that the host machine must stay on.

**Why not Streamlit Community Cloud for production**
- Disk is ephemeral. `gi_database.db` is wiped on every redeploy / weekly auto-restart / sleep wake. All consumption, receipts, returns, audit log, attachments — gone.
- Streamlit Cloud is fine as a **demo** environment. Not as the system of record.

**Components**

| Component | What it does | How to run |
|---|---|---|
| `streamlit run main.py` | The web app + embedded WhatsApp worker thread | `caffeinate -dis streamlit run main.py` (keeps machine awake without locking the display) |
| `python whatsapp_worker.py` | Standalone WhatsApp sender (needed on macOS — pywhatkit can't run from Streamlit's thread; Twilio works either way) | Second terminal |
| `cloudflared tunnel` | Exposes localhost:8501 as a public HTTPS URL | `cloudflared tunnel run gi-hub` |
| `backup_db.sh` (cron) | Nightly copy of `gi_database.db` + `uploads/` to an external disk + iCloud Drive | macOS launchd plist below |

**One-time setup (Mac)**

```bash
# 1. Install
brew install cloudflared
# 2. Authenticate (opens browser)
cloudflared tunnel login
# 3. Create a named tunnel
cloudflared tunnel create gi-hub
# 4. Route a subdomain on a Cloudflare-managed domain (you need a domain on Cloudflare for this)
cloudflared tunnel route dns gi-hub gi.yourdomain.com
# 5. Config file
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml <<'EOF'
tunnel: gi-hub
credentials-file: /Users/johnsonandrew/.cloudflared/<tunnel-uuid>.json
ingress:
  - hostname: gi.yourdomain.com
    service: http://localhost:8501
  - service: http_status:404
EOF
# 6. Test it manually
cloudflared tunnel run gi-hub
# Then visit https://gi.yourdomain.com — Streamlit should answer.
```

**Auto-start everything on login (macOS launchd)**

Create three `.plist` files under `~/Library/LaunchAgents/`:

```xml
<!-- com.gi.streamlit.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.gi.streamlit</string>
  <key>WorkingDirectory</key><string>/Users/johnsonandrew/Downloads/CNCEC PROJECT</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>caffeinate -dis ./.venv/bin/streamlit run main.py --server.headless true</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/johnsonandrew/Library/Logs/gi-streamlit.log</string>
  <key>StandardErrorPath</key><string>/Users/johnsonandrew/Library/Logs/gi-streamlit.err</string>
</dict></plist>
```

(Equivalent `.plist` files for `whatsapp_worker.py` and `cloudflared tunnel run gi-hub` — same pattern, different ProgramArguments.)

Load them once:
```bash
launchctl load -w ~/Library/LaunchAgents/com.gi.streamlit.plist
launchctl load -w ~/Library/LaunchAgents/com.gi.whatsapp-worker.plist
launchctl load -w ~/Library/LaunchAgents/com.gi.cloudflared.plist
```

After reboot they auto-restart. Check with `launchctl list | grep gi`.

**Nightly backup script**

Save as `~/bin/gi_backup.sh`, `chmod +x`, then add a launchd plist that fires it daily at 02:00.

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT="/Users/johnsonandrew/Downloads/CNCEC PROJECT"
DEST="$HOME/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups"
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M%S)
# SQLite online backup (safe even while Streamlit holds the file)
sqlite3 "$PROJECT/gi_database.db" ".backup '$DEST/gi_database_$STAMP.db'"
# Attachments (mirror the BLOBs to disk too, in case the DB ever corrupts)
rsync -a --delete "$PROJECT/uploads/" "$DEST/uploads_latest/"
# Prune old backups — keep last 14 days of DB snapshots
find "$DEST" -name 'gi_database_*.db' -mtime +14 -delete
```

Restore is `sqlite3 gi_database.db ".restore '<backup>.db'"` while the app is stopped.

**Security checklist**

- Change every default password (`/admin admin2026`, etc.) on first login.
- Set `[ollama]` and `[twilio]` secrets via Streamlit's `secrets.toml` (in `~/.streamlit/secrets.toml`) — never commit them.
- Cloudflare Tunnel terminates HTTPS at the edge, so traffic from the user's browser to your Mac is encrypted end-to-end. No port forwarding needed.
- Optional: enable Cloudflare Access policies (free for 50 users) so only emails on your team's domain can reach the URL.
- Run `git pull` periodically; the self-healing `init_db()` handles schema migrations automatically.

**Updates without downtime**

```bash
cd "Downloads/CNCEC PROJECT"
git pull
launchctl kickstart -k gui/$UID/com.gi.streamlit    # restart Streamlit only
```

`launchd` brings it back within 2 seconds. Tunnel + worker stay up.

---

### Why we do NOT recommend a PyInstaller `.exe` for production

The packaging is technically possible (PyInstaller can bundle Streamlit) but:

- Bundle size: ~500 MB. Slow first launch.
- Single user, single DB. Defeats the multi-site / multi-role design.
- Streamlit's dynamic template loading fights PyInstaller; you'll spend hours on `--add-data` flags and copy-fix loops.
- No live updates. Every code change → re-bundle → reinstall.
- WhatsApp: only Twilio works reliably inside a frozen binary (pywhatkit's browser automation is fragile inside `.exe`).

**Where `.exe` IS reasonable:** a single-laptop offline demo for management who can't access the network app. For that, run a one-off bundle:

```bash
.venv/bin/pip install pyinstaller
pyinstaller --onefile --add-data "USER_MANUAL.md:." \
    --hidden-import="streamlit.runtime.scriptrunner.magic_funcs" \
    -n GIHub main.py
```

…and accept that updates require re-bundling and DB is local to that machine. The hosted path is strictly better for real use.

---

### Branded User Manual PDF — `build_manual_pdf.py`

The repo ships a standalone PDF generator that converts `USER_MANUAL.md` into a designed, management-presentable PDF using fpdf2 (already in `requirements.txt` — no new deps).

```bash
python build_manual_pdf.py                       # writes GI_Hub_User_Manual.pdf
python build_manual_pdf.py --out report.pdf      # custom output
```

Or from inside the app: **Admin → Settings → 📄 Download User Manual (Branded PDF)** → click *Build PDF now* → download.

What's in the PDF:
- Cover page with navy + gold brand panel, version, date
- Auto-generated table of contents with dotted leaders and page numbers
- Per-page header (chapter title) + footer (page X of N + brand)
- Hierarchical headings, paragraphs, bullet lists, **GFM tables**, fenced code blocks
- All non-Latin-1 characters (em-dash, smart quotes, emoji) sanitised to ASCII so fpdf's core fonts render without crashing

Current output: 65 pages, ~130 KB.

---

### Ollama — local + Streamlit-Cloud tunnel

| Where | What runs | How |
|---|---|---|
| Local Mac | Ollama on `localhost:11434` | `ollama serve` (or it's already running) |
| Streamlit Cloud | No Ollama. Tunnels to your Mac. | Tailscale Funnel **or** ngrok |

**Models used in this project** (see Admin Portal → Settings → 🤖 AI Connection for a live status panel):

| Purpose | Model id | Source |
|---|---|---|
| NL search → SQL | `qwen2.5-coder:7b` | `ai/nl_search.py` |
| Summaries / AI Insights / chat | `llama3.1:8b` | `ai/summarize.py`, `ai/insights.py` |
| **OCR (handwritten consumption + delivery notes)** | `qwen2.5vl:7b` ⚠ vision model, pull separately | `ai/ocr.py` |
| RAG / embeddings (reserved) | `nomic-embed-text:latest` | unused so far |

Pull once locally: `ollama pull qwen2.5vl:7b` (otherwise the SK Entry Log "📷 Upload Handwritten Consumption List" expander shows a `Vision model not installed` error with the exact pull command).

**Streamlit Cloud → local Ollama via Tailscale Funnel** (recommended — encrypted, free, no opening firewall ports):

1. On your Mac: `brew install tailscale` (or download from tailscale.com), sign in.
2. Enable Funnel on port 11434:
   ```bash
   tailscale funnel --bg 11434
   ```
   Tailscale prints a public HTTPS URL like `https://johnsons-air.tail1234.ts.net`.
3. In Streamlit Cloud → App Settings → Secrets, add:
   ```toml
   [ollama]
   host         = "https://johnsons-air.tail1234.ts.net"
   vision_model = "qwen2.5vl:7b"
   ```
4. Reload the app. Admin Portal → Settings → **🤖 AI Connection** should show "✅ Reachable" plus a per-model INSTALLED / MISSING table.

**Alternative — ngrok** (faster setup, free tier rotates URLs daily):

```bash
brew install ngrok && ngrok config add-authtoken <your_token>
ngrok http 11434
```

Copy the `https://...ngrok-free.app` URL into Streamlit Secrets as `[ollama] host`. Note: free ngrok URLs change on every restart.

Caveats: AI features only work while your Mac is on, awake, and connected. Put `caffeinate -d` in front of `ollama serve` if you want the display to stay off but the server to stay up.

### Default credentials (change immediately)

- admin / admin2026 · hod / hod2026 · supervisor / super2026 · worker / floor2026

---

## 5. Where to Look When You're Stuck

| Symptom | First place to check |
|---|---|
| "Cannot operate on closed database" | A helper closes its own conn then keeps using it after `finally` (see the `report_monthly_summary` fix pattern) |
| `to_sql` crash on append | DataFrame has columns the table doesn't (see EOD dialog filter pattern: `PRAGMA table_info(...)` then column-filter) |
| Streamlit duplicate key error | Two tabs/widgets share a `key=` — pass a `key_suffix` per call site |
| Live stock wrong / stuck | `bust_inventory_cache()` not called after the write that mutated receipts / consumption / returns |
| Report column wrong / missing | Source query in `database.py` — `report_*` functions or fetcher (`get_*`). Material_Code is now in all the JOINs. |
| FEFO panel shows nothing | `lots` table empty for that SAP+Site AND no receipts with Expiry_Date → check `process_receipt_delivery` was called with expiry; otherwise legacy fallback in `get_fefo_lots` should trigger |
| WhatsApp not delivered | `whatsapp_worker.py` not running; check Admin → WhatsApp Console → status pills |
| Audit log filter shows nothing | `system_audit_log` populated by `log_audit_action(username, action_type, target_table, details)` — check the function was actually called from the code path you expect |
| Streamlit startup hangs on `_start_whatsapp_worker()` | Something at module-level inside `whatsapp_worker.py` pulled in a heavy dep. Lazy-import the offender like we did for `pywhatkit` (`whatsapp_worker.py:32-50`) |
| `OperationalError: no such column: X` on receipt commit | The column is missing from the `receipts` self-heal block. See `database.py:553`. **The error gets swallowed** by `process_receipt_delivery`'s try/except, so it shows up as a silent drop, not a crash. Always extend the self-heal when `commit_pending_receipts` starts propagating a new column. |
| Streamlit `StreamlitAPIException: not a valid emoji` | `st.toast(icon=...)` accepts a real emoji, NOT a glyph like "✓" or "✗". Use "✅" / "🚫" / a specific emoji. |
| Return approved but stock didn't move | Cache. `approve_return_request` writes to `returns`; the dashboard reads via `cached_live_inventory`. The approve flow calls `bust_inventory_cache()`. If a future caller forgets that, the dashboard stays stale until the next bust. |
| Attachment file missing from `uploads/` but downloads from HOD DOC tab still work | Expected. The DB BLOB is authoritative; the disk mirror is best-effort (read-only FS / Streamlit Cloud restart). |
| Tests pass but UI crashes on render | pytest doesn't render the page. Run `python bug_check.py` for the data-layer coverage, then click through the affected page manually. Streamlit AppTest harness (Tier B) is an unbuilt option in this repo. |
| `FPDFUnicodeEncodingException: Character X outside the range` | fpdf2 core fonts are Latin-1 only. Add the offender's glyph → ASCII mapping in `build_manual_pdf.py:_REPLACE`. Don't switch to a Unicode TTF font unless you're OK with the 1-3 MB binary footprint that adds. |
| Self-host: app unreachable from outside | `launchctl list \| grep gi` — all three (streamlit, whatsapp-worker, cloudflared) should show non-zero PIDs. Check `~/Library/Logs/gi-*.err` for crashes. Test `curl http://localhost:8501` first to isolate Streamlit vs tunnel. |
| Self-host: backup script fails | Run it manually under your shell to see the error. Common: `sqlite3` not on $PATH (use `/opt/homebrew/bin/sqlite3`); iCloud Drive path not present (system migration?). |
| `com.gi.streamlit exit 126` | launchd can't exec the venv binary. The wrapper at `host_setup/scripts/run_streamlit.sh` works around it. If you bypassed the wrapper, look for missing exec bit (`chmod +x .venv/bin/streamlit`) or Gatekeeper quarantine (`xattr -d com.apple.quarantine .venv/bin/streamlit`). |
| WhatsApp messages all `failed` even though worker is running | Embedded thread in main.py is racing the standalone process. Confirm `GI_SUPPRESS_EMBEDDED_WORKER=1` appears in the rendered `~/Library/LaunchAgents/com.gi.streamlit.plist`. Reinstall via `./host_setup/scripts/install.sh` if missing. |
| "Python wants to control Google Chrome" popup repeats | macOS Automation prompt. Means a `tell application <X>` slipped back into the AppleScript path. The current sender uses `System Events` only; check `whatsapp_worker._send_via_chrome_macos` hasn't been edited. |
| Timestamps still UTC on a specific page | `localize_timestamps_df(df, [...])` not yet applied there. Add the import + one-liner call right after the `pd.read_sql(...)`. The helper is idempotent and safe to apply on already-converted DataFrames (it returns the input on the second pass — strings don't re-parse as timestamps). |
| Ask Hub Assistant returns "That isn't covered in your section of the manual" | Either (a) the user IS asking about a section above their role — that's the security feature working, or (b) USER_MANUAL.md has drifted away from the role-section allow-list in `ai/manual_qa._ROLE_ALLOWED`. Re-check the section numbering matches `# N. ` headings. |
| Logistics user can't see HOD Portal in the sidebar | Working as intended. v3.0 added `📋 HOD Portal` to `_EXACT_ROLE_PAGES = {hod, admin}` so the procurement roles (numerically higher than HOD in the hierarchy) don't inherit access. |
| Warehouse_user opens a PO and sees prices | Bug. Check `get_assignment_detail()` is the helper being called (not `get_po_detail(hide_prices=False)` directly). All three layers must blank: (a) items, (b) defensive re-blank, (c) header dict pops. Run `python -c "from database import get_assignment_detail; ..."` to inspect. |
| Mixed-family RL+BL DN was accepted | Bug. `create_delivery_note()` must reject. Check: (a) `po_items.rl_bl_family` is populated for the offending rows — if not, `classify_rl_bl_family()` failed to detect (token mismatch); (b) the splitter's `if len(families - {None}) > 1` guard is intact at top of `create_delivery_note`. |
| DN approved by HOD but SK doesn't see "Incoming DNs" | `pending_receipts` mirror row didn't insert OR SK is on a different Site_ID. Inspect `SELECT * FROM pending_receipts WHERE DN_Number = ?` — if no row, `hod_decide_dn(approve=True)` failed silently (look at audit log for `DN_HOD_APPROVE`). If row exists but with wrong Site_ID, the DN's `Site_ID` was wrong at WH-prepare time. |
| Reminder fired twice in one day | Either `delivery_reminders_sent` UNIQUE was dropped/violated OR `app_settings.delivery_reminders_last_run` was manually wiped. Check both. The double-fire is recoverable but a sign of guard regression. |
| Notification bell shows N unread but inbox is empty | Race condition: bell count cached before a mark-all-read elsewhere in the same session. Click the bell once to refresh; it re-reads. If persistent, check `app_notifications.read_at` was actually written for the rows. |
| Force-closure doesn't appear in HOD's In-Transit "affecting me" tab | Closure was on a PR/PO with NULL Site_ID. Check: `SELECT * FROM po_force_closures WHERE id=?` — if `Site_ID` is NULL, the 3-way fallback join needs `pr_master.Site_ID` OR `purchase_orders.Site_ID` to be populated. Backfill Site_ID on the parent row if missing. |
| `whatsapp_worker` log shows "delivery_reminders crashed: no such table delivery_reminders_sent" | `init_db()` didn't run before worker started. On a fresh DB, ensure `main.py` (which calls `init_db()`) runs at least once before the standalone worker, OR have the worker call `init_db()` defensively at startup. |

---

## 6. Inventory of Schema Additions

**Pre-2026-06 round (Tier-1 tuning):**
- `app_settings(key, value)` — thresholds, maintenance_mode, last_backup_at
- `bug_reports`, `report_schedules`, `report_archive`
- `stock_adjustments` + 9 reason codes
- `lots` + `v_lot_balance` view
- `inventory.Unit_Cost`, `receipts.Unit_Cost`
- `pr_master.workflow_state, UOM, Supplier, Est_Cost_SAR, Notes`
- `pending_receipts.rejection_reason`
- `receipts.Lot_Number`, `consumption.Lot_Number`, `pending_issues.Lot_Number`, `pending_receipts.Lot_Number`
- `pending_issues.FEFO_Override`, `consumption.FEFO_Override`

**2026-06 round:**
- New tables:
  - `qr_approval_requests` — SK label requests → HOD approval → consolidated PDF download
  - `entry_attachments` — BLOB + disk-mirror path; `doc_type IN ('consumption','receipt','return')`
  - `mtc_documents` — Surface Shields MTC; `status IN ('attached','missing','sent_to_logistics')`
  - `pending_returns` — SK return staging → HOD approval; `override_required` flag for >30-day returns
- Inventory: `Category` (default `'Others'`) + `Opening_Stock` (default 0)
- System settings: `system_settings.Site_ID` (NULL = global, non-NULL = site-specific)
- Receipts: `DN_No`, `Serial_No`, `PR`, `Location`, `Vehicle_No`, `Driver_Name`, `Pallet_No`, `Mob_From`, `Prepared_by`, `Mob_To`, `Received_by`, `DN_Copy` — closes the silent-drop bug
- `whatsapp_queue`: `error_message`, `attempts` columns for failure visibility + retry

**2026-06 round 4 — Phase 6A–F (Workstream A):**
- New tables:
  - `employees` — physical-labour master (`ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status IN ('active','inactive','suspended')`, `created_by`, `created_at`, `updated_at`). NOT a login — separate from `users`.
  - `cv_model_versions` — YOLO model registry (`version` UNIQUE, `model_path`, `classes_json`, `mAP`, `trained_at`, `is_active`). Partial unique index `ix_cv_active` guarantees ≤1 active row.
  - `tool_catalogue` — class registry (`class_name` UNIQUE, `display_name`, `category`, `model_version_id` FK, `min_confidence` REAL default 0.75, `created_by`).
- `returnable_items` self-heal — `cv_detected` (INTEGER 0/1), `cv_confidence` (REAL), `cv_employee_id` (TEXT), `cv_tool_class` (TEXT). Manual entries leave them NULL so adoption telemetry stays honest.
- `delivery_reminders_sent` — `CHECK(ref_type IN ('po','dn'))` constraint **dropped** via self-heal table rebuild so Phase 6E can reuse the dedup table with `ref_type='returnable_loan'` + signed-hour `offset_days` ∈ {−2, 0, 2, 24}. UNIQUE(ref_type, ref_number, target_date, offset_days) preserved. Existing rows carried over verbatim.
- `app_settings` keys: `returnable_reminders_last_run_hour` (worker hourly-gate marker, format `YYYY-MM-DDTHH`).
- `users` (legacy, no migration): admin / logistics / warehouse_user rows now allowed to carry empty `Site_ID = ""` (global roles). The existing seeded `admin` row was migrated from `"CNCEC"` → `""`.
- Inventory data cleanup (one-off, via `scripts/clean_inventory_sites.py`): all `Site_ID = "HQ"` rows across `inventory` / `receipts` / `pending_receipts` / `users` flipped to `"CNCEC"` (357 rows); `system_settings` `("Site","HQ")` row deleted.

**2026-06 round 2:**
- New tables:
  - `wbs_master` — per-site allowed WBS numbers; UNIQUE(WBS_Number, Site_ID); `active`/`closed` status
- Self-heal: `wbs` column on `consumption`, `receipts`, `pending_issues`, `pending_receipts`
- Config rename: `RUBBER_CATEGORY` → `MTC_REQUIRED_CATEGORY = "Surface Shields"` (old name kept as alias)

All added via self-healing `ALTER TABLE` in `init_db()`. None require manual migration. The bug harness asserts every column listed here.

**Phase C round (2026-06 round 3) — Procurement chain:**

New tables (12) — column-by-column summary:

| Table | Key columns | Purpose |
|---|---|---|
| `warehouses` | `Warehouse_ID` UNIQUE, `Name`, `Location`, `Contact_*`, `status` | Physical receiving locations master |
| `vendors` | `Vendor_Code` UNIQUE, `Vendor_Name`, `Address`, `Contact_*`, `Default_Inco_Terms`, `Default_Payment_Terms`, `status` | Vendor master, auto-fills PO creation form |
| `purchase_orders` | `PO_Number` UNIQUE, `PR_Number`, `Site_ID`, `Vendor_Code`, `Vendor_Name`, `Inco_Terms`, `Payment_Terms`, `PO_Date`, `PO_Type`, `Quotation_No/Date`, `Your_Reference`, `Our_Reference`, `Contact_Person`, `Contact_Email`, `Mobile`, `Our_Email`, `Expected_Delivery`, `Freight_Charges`, `Handling_Charges`, `Discount_Amount`, `Total_Amount`, `Amount_In_Words`, `source` (`manual`/`pdf_upload`), `attachment_blob`/`_name`/`_mime`, `status` (`open`/`partially_delivered`/`delivered`/`closed`/`force_closed`/`cancelled`), `created_by`, `closed_by`, `close_reason` | PO header; one row per PO |
| `po_items` | `PO_Number`, `line_no`, `Material_Code`, `Description`, `Qty`, `UOM`, `Unit_Price`, `Total_Price`, `PR_Number`, `WBS_Number`, `Network`, `Plant`, **`rl_bl_family`** (`RL`/`BL`/NULL), `Delivered_Qty`, `Returned_Qty`, `line_status` (`open`/`partially_delivered`/`delivered`/`returned`/`closed`/`force_closed`), `close_reason` | PO line items; one row per line. `SAP_Code` intentionally absent here — Logistics works with Material_Code, SAP joins at SK receipt |
| `po_shipment_schedule` | `PO_Number`, `shipment_no`, `material_group`, `target_date`, `actual_date`, `status`, `notes` | Parsed from PO Annexure delivery schedule (PDF page 3 of the sample) |
| `po_assignments` | `PO_Number`, `Warehouse_ID`, `items_subset_json` (NULL = all items), `Expected_Delivery`, `assigned_by`/`_at`, `acknowledged_by`/`_at`, `status` (`assigned`/`acknowledged`/`received`/`partial`/`closed`/`cancelled`), `notes` | Logistics → Warehouse routing |
| `delivery_notes` | `DN_Number` UNIQUE, `PO_Number`, `Warehouse_ID`, `Site_ID`, **`rl_bl_family`**, `DN_Date`, `Vehicle_No`, `Driver_Name`, `Driver_Phone`, `Prepared_By`, `Remarks`, `status` (DN state machine — see Critical Contracts), `logistics_decided_by`/`_at`/`_decision`, `hod_decided_by`/`_at`, `sk_received_by`/`_at`, `rejection_reason`, `created_by` | DN header. One PO can produce many DNs |
| `dn_items` | `DN_Number`, `po_item_id`, `Material_Code`, `Description`, `Qty`, `UOM`, `Lot_Number`, `Expiry_Date`, `Remarks`, `rl_bl_family`, `sk_received_qty`, `status` (`pending`/`received`/`partial`/`returned`/`cancelled`) | DN line items |
| `po_returns` | `PO_Number`, `po_item_id`, `DN_Number`, `Material_Code`, `Qty`, `Reason`, `raised_by_role`, `raised_by`, `raised_at`, `Expected_Resupply`, `status` (`open`/`vendor_acknowledged`/`resupplied`/`cancelled`), `closed_by`/`_at`, `notes` | Vendor returns + site→warehouse returns (both flow through here) |
| `po_reschedule_requests` | `PO_Number`, `DN_Number`, `current_date`, `requested_date`, `reason`, `requested_by_role` (`warehouse_user`/`hod`/`admin`), `requested_by`/`_at`, `status` (`pending`/`approved`/`rejected`), `decided_by`/`_at`, `decision_notes` | Warehouse / Site HOD → Logistics reschedule asks |
| `po_force_closures` | `target_type` (`pr`/`po`/`po_item`), `target_ref`, `Site_ID`, `PR_Number`, `PO_Number`, `reason`, `closed_by`/`_at`, `notes` | Audit log of every force-closure with reason |
| `app_notifications` | `recipient_user` OR (`recipient_role` + optional `recipient_site`/`recipient_warehouse`), `event_key`, `severity` (`info`/`warning`/`critical`/`success`), `title`, `body`, `link_page`, `link_anchor`, `related_table`, `related_ref`, `read_at`, `created_at` | In-app bell inbox. Always fires alongside any WhatsApp event |
| `delivery_reminders_sent` | UNIQUE(`ref_type` (`po`/`dn`), `ref_number`, `target_date`, `offset_days`), `fired_at` | Idempotency log for the T-2/T-1/T-0 sweep |

Extended existing tables (column additions):

| Table | New columns | Purpose |
|---|---|---|
| `pr_master` | `WBS_Number`, `Network`, `Plant`, `Delivery_Date`, `submitted_to_logistics_at`, `submitted_to_logistics_by`, `logistics_status` (`site_draft`/`submitted`/`in_po`/`closed`/`force_closed`) | Procurement chain handoff state on PR rows |
| `receipts` | `DN_Number`, `Warehouse_ID`, `PO_Number_Source` | Traceback so a `receipts` row can be mapped to its originating DN/PO/warehouse |
| `pending_receipts` | `DN_Number`, `Warehouse_ID`, `PO_Number_Source` (mirrored from receipts schema) | DN-driven mirror rows arrive here with `status='pending_sk'` |
| `users` | `Warehouse_ID` (nullable) | Scopes a `warehouse_user` to a warehouse |
| `pending_users` | `Warehouse_ID` | Same, for self-registration queue |
| `users` (CHECK) | Role CHECK rebuilt to include `'logistics'` + `'warehouse_user'` via the worker→store_keeper migration pattern | New roles accepted by INSERT |

New roles (2): `logistics` (icon 🚚, hierarchy=3), `warehouse_user` (icon 🏭, hierarchy=1). `ROLE_HIERARCHY` revised to `{store_keeper:0, warehouse_user:1, supervisor:1, hod:2, logistics:3, admin:4}`. New `PAGE_ACCESS` entries: `🚚 Logistics Portal → logistics`, `🏭 Warehouse Portal → warehouse_user`. Both exact-locked in `_EXACT_ROLE_PAGES`.

---

## 7. Hidden surprises a future session needs to know

1. **`process_receipt_delivery` swallows OperationalError.** Any new column it propagates via `extra_fields` MUST exist on `receipts` — otherwise SK input silently disappears at HOD commit time. Add to the self-heal at `database.py:553` and to the `bug_check.py` schema list.
2. **Streamlit `UploadedFile` is single-use.** If you stash files in `st.session_state` (e.g. multi-row staging), read `.read()` immediately at Add-to-Grid time and store the bytes. The `_BytesBlob` wrapper in the Return Items submit handler exists for exactly this reason.
3. **`@st.cache_resource` is the only safe place to start daemon threads.** Module-level threads spawn duplicates because Streamlit re-execs `main.py` on every interaction.
4. **`pywhatkit` import is heavy.** Keep it lazy. If you import any other GUI lib (`tkinter`, `pyautogui`, …) at module level, expect the same hang.
5. **The `returns` ledger has only one writer in approved flows:** `approve_return_request`. Anything else that needs to reduce stock should go through Stock Adjustments, not direct `INSERT INTO returns`.
6. **Toast icon emojis are validated by Streamlit.** Stick to actual single emojis (✅ 🚫 📨 📦 ⚠️ …), not shortcodes or geometric glyphs.
7. **Streamlit Cloud filesystem rejects WAL mode.** `get_connection()` applies PRAGMAs in a per-pragma try/except. Don't refactor that block back into a single statement.
8. **HOD Portal hides itself from Admin in the sidebar** but `_can_access('admin', '📋 HOD Portal')` returns `True` — admin can navigate there if the URL is set. That's deliberate (Admin can shadow for support). Don't "fix" it.
9. **launchd direct exec of `caffeinate <abs-path>` is unreliable on Apple Silicon / newer macOS** — silent `exit 126`. Use the `host_setup/scripts/run_streamlit.sh` wrapper. Don't "simplify" the plist back to direct exec.
10. **`tell application <browser>` in AppleScript triggers Automation permission, NOT Accessibility**. They're separate macOS prompts. The Chrome WhatsApp sender uses only `System Events` keystrokes to stay under Accessibility (one-time grant per binary). If you ever add `tell application` you'll be back to per-message popups.
11. **`Ask Hub Assistant` role-filters at the RAG layer, not just system prompt.** A Store Keeper's request never sees the Admin chapter in the prompt context — that's why the model can't leak privileged info even under prompt-injection attack. Preserve this when extending. The role allow-list lives in `ai/manual_qa._ROLE_ALLOWED`.
12. **SQLite `CURRENT_TIMESTAMP` is UTC by spec.** New rows we write through Python use Riyadh time (because `TZ=Asia/Riyadh` in launchd plists). Mixed timestamps in the DB are normal; display layer normalises via `config.utc_to_local()`. Don't try to "fix" the DB to all-local — portability across timezones depends on UTC at rest.
13. **`streamlit-keyup` is a Streamlit component**, not a pip dep we can install on Streamlit Cloud's container if there's no network. It's in `requirements.txt` and works on Streamlit Cloud. The dashboard gracefully degrades to plain `st.text_input` if the import fails.
14. **`pending_receipts.status='pending_sk'` is a NEW status value (v3.0).** HOD's existing Pending Receipts tab filters by `status='pending_hod'`, so the DN-driven mirror rows (which use `'pending_sk'`) don't bleed into that tab. There is NO CHECK constraint on `pending_receipts.status`, so the new value inserts cleanly. Do NOT add one without also updating `hod_decide_dn()`.
15. **DN over-ship guard counts LIVE DNs only.** `create_delivery_note()` excludes DNs with status IN (`'rejected'`, `'cancelled'`) from the "already shipped" calculation. A rejected DN frees its qty back to be used by the next DN. Don't change this — it's how reschedules + rejections recover stock allocation cleanly.
16. **Force-closure site visibility uses a 3-way join fallback.** `list_force_closures_for_site()` matches on (a) `po_force_closures.Site_ID` direct OR (b) via `purchase_orders.Site_ID` OR (c) via `pr_master.Site_ID`. Closures landed before Site_ID resolution still surface on the correct site. Same pattern in `report_force_closures()`.
17. **`users` role CHECK constraint rebuild reuses the worker→store_keeper pattern.** Adding the procurement roles required a CHECK rebuild, which means `ALTER TABLE users RENAME TO _users_old2; CREATE TABLE users (...); INSERT FROM _users_old2;`. The rebuild block in `init_db()` detects via `"'logistics'" not in sql` so it's idempotent. Don't add a new role without extending this CHECK and the rebuild check, OR you'll get `IntegrityError: CHECK constraint failed`.
18. **`delivery_reminders_sent` has both a UNIQUE constraint AND a separate day-marker.** UNIQUE blocks per-(ref, date, offset) double-fires inside one sweep. The `app_settings.delivery_reminders_last_run` day-marker skips the SQL queries entirely on the second-and-later worker ticks of the same day. Removing either guard means more work (UNIQUE alone) OR risk of double-fires across worker restarts (day-marker alone).
19. **Sidebar Notifications bell tolerates DB errors silently.** `_render_notifications_bell()` wraps `count_unread_notifications` in a try/except that defaults to 0. A notification helper failure must NEVER stop the rest of the sidebar (or the page) from rendering. If you add bell features, preserve this contract.
20. **`app_notifications` visibility OR-group MUST be parenthesised.** The query is `SELECT * FROM app_notifications WHERE (recipient_user = ? OR (recipient_role = ? AND ...)) [AND read_at IS NULL]`. Without the outer parens, the optional `AND read_at IS NULL` binds only to the second OR branch, leaking read user-targeted notifications back as "unread". This was a bug found in Phase 1 testing — see `get_app_notifications()`.
21. **Warehouse view price hiding has THREE layers, not one.** (a) `get_po_detail(hide_prices=True)` blanks the item columns. (b) `get_assignment_detail()` re-blanks them defensively. (c) `get_assignment_detail()` also pops monetary keys from the header dict. Never trust just one. The bug_check test `check_warehouse_view_strict_price_hiding` asserts all three.
22. **RL/BL classifier is substring-based, NOT exact-match.** `config.classify_rl_bl_family()` matches `RUBBER LINING` / `BRICK LINING` / `BRICK MATERIAL` / `RL-` / `BL-` against the concatenated `Material_Code + Description` (case-insensitive). Adding new family tokens? Add them to `RL_BL_FAMILY_TOKENS` in `config.py`. RL takes precedence by dict insertion order if both tokens are present — never combo.

---

---

## Phase 6 — Enterprise Deployment & Computer Vision (forward roadmap)

> **Status as of this handoff:** **Workstream A (Phase 6A–F) SHIPPED 2026-06.** Phases 1–5 (procurement chain) and Phase 6A–F (CV + Smart Scan + reminders) are stable at **268/268 bug_check + 16/16 UI crawler**. Workstream B (Phase 6G–K — Docker, dynamic WhatsApp provider, Ollama containerization, NAS backups, deployment playbook) remains PLANNED.
>
> **What shipped in Workstream A (one-line each):**
> - **6A** — `employees`, `tool_catalogue`, `cv_model_versions` tables + CRUD helpers + `returnable_items.cv_*` self-heal columns.
> - **6B** — `ai/cv/qr.py` (encode + decode with macOS Homebrew libzbar patch) + 👷 Employees admin tab (Add/Edit · CSV import · Roster + per-badge PNG).
> - **6C** — `ai/cv/train.py` CLI (auto-versioning, mAP harvest, DB registration) + `ai/cv/inference.py` (lazy YOLO, per-class min_confidence, cache invalidation) + 🛠️ Tool Catalogue admin tab. Companion: `docs/cv_training_guide.md`.
> - **6D** — Smart Scan in SK Returnable Items tab (badge → tool → write-through to manual form, session-state hash dedup, auto/candidates/manual buckets) + return-by-scan grid filter.
> - **6E** — `sweep_returnable_reminders` (T−2h / T−0 / T+2h / T+24h with Borrower → SK → Supervisor escalation; signed-hour `offset_days`; hourly worker gate via `app_settings.returnable_reminders_last_run_hour`).
> - **6F** — `reports.generate_employee_qr_badges_pdf` (multi-page A4 grid with HR header band) + Admin Portal bulk download + documentation rollup (this section, `USER_MANUAL.md` §4.5.0, `SOP.md` §7.4 SK card).
>
> **Read first if you are picking this up cold:** This entire chapter is self-contained. You do not need any prior chat context — every decision is recorded here, every file path is named, every env var is documented.
>
> **Start here for Workstream B (Phase 6G+):** Read §Phase 6.0 (preflight) — Twilio creds, NAS path, Docker layer cache. Then proceed serially through Phase 6G→K.

### Phase 6 — Why this exists

Two milestones requested in v3.1+:
1. **Enterprise Docker deployment** so the system can move from local Mac + Cloudflare to a corporate intranet server (specs unknown — built for the lowest-common-denominator Linux CPU VM).
2. **Computer vision for Returnable Items** — QR-scan an employee badge + object-detect the borrowed tool so the Store Keeper can check out tools without typing.

Two workstreams, decoupled by design. Workstream A (CV) pilots on the current Mac. Workstream B (Docker) is the deployment chassis. They can be tackled in parallel or in series, but the recommended order is **A first, then B**, because A introduces new heavy deps (`ultralytics`, `opencv-python-headless`, `pyzbar`) that B's `requirements-server.txt` will need to know about.

### Critical operating constraint — DO NOT BREAK THE MAC

Until management approves the company server, the user demonstrates Phases 1–5 to leadership from the current **Mac + Cloudflare Tunnel** setup. **Therefore:**

- **`pywhatkit` MUST remain installed and functional** on the local Mac throughout Phase 6.
- **The macOS Chrome + AppleScript WhatsApp path MUST continue to work** when the user runs `streamlit run main.py` locally.
- **No code path may hard-delete or hard-block `pywhatkit`** — Phase 6.H uses a runtime-evaluated env var (`WHATSAPP_PROVIDER`) to switch behavior, NOT a delete.
- The Docker build excludes `pywhatkit` from its requirements layer because it has heavy GUI deps that fail on a slim Linux image — but that exclusion is per-image, NOT per-codebase.

If you find yourself about to write code that removes `pywhatkit` or breaks the AppleScript path, **stop**. Reread this section. The toggle is the only acceptable design.

---

## Phase 6 decisions register (set in v3.0 brainstorm)

These are FROZEN — proceed without re-litigating:

| ID | Decision | Rationale |
|---|---|---|
| A1 | Linux Docker target (Windows out of scope) | Standard Docker base, easy CI |
| A2 | Intranet only — no Cloudflare on the server | Corporate security |
| A3 | Keep bcrypt + app-level RBAC | SSO/AD = separate future phase |
| A4 | Keep SQLite | WAL handles 10–25 concurrent fine |
| A5 | CPU-only assumed; GPU is bonus | Lowest common denominator |
| A6 | Corporate NAS for backups | IT-controlled, no cloud dep |
| A7 | Twilio for server WhatsApp; pywhatkit stays for Mac via env-var toggle | Mac demo must keep working |
| B1 | Mixed CV: small fixed YOLO catalogue + manual fallback for everything else | Realistic accuracy expectations |
| B2 | Custom YOLOv8 trained locally | Internal AI, no cloud calls |
| B3 | Server-side Python inference | Centralised model updates |
| B4 | `st.camera_input` (snapshot, not WebRTC) | Corporate Wi-Fi friendly |
| B5 | New `employees` master (NOT `users`) | Separates physical workers from app users |
| B6 | QR contains only the `ID_Number` | Privacy — no PII in scannable code |
| B7 | NO images stored on disk; process in-memory, log only metadata | GDPR / IT compliance |
| B8 | Hourly returnable-reminder sweep: T-2h, T-0, T+2h, T+24h | Tool-loan cadence ≠ delivery cadence |
| B9 | Confidence ≥ 0.75 auto-fills; < 0.75 shows top-3; manual fallback always | Robustness |
| X1 | CV and Docker decoupled; CV pilots on Mac first | De-risk one at a time |
| X2 | Pilot CV at one storeroom, then expand | Real-world model accuracy before scale |

> ### 🔁 SUPERSEDED by Workstream C (locked 2026-06-27) — read this before applying A2/A6/A7
> The user has formally re-canonized the deployment target. The following frozen rows are **overridden** for production:
> - **A2 (intranet-only) → PUBLIC.** Production runs on a **public Hetzner VPS fronted by Nginx** (TLS via Let's Encrypt). The "AI stays safe internal" guarantee is preserved *not* by intranet isolation but by giving **Ollama and all FastAPI sidecars NO host port mapping** — Nginx is the sole ingress on `gi-net`. Public exposure adds: firewall (only 22/80/443), fail2ban/brute-force protection, TLS, and Streamlit's bcrypt RBAC as the auth gate.
> - **A6 (corporate NAS) → Hetzner Storage Box.** The `backup` service rsyncs to a Hetzner Storage Box bind-mount instead of a corporate NAS.
> - **A7 (Twilio for server WhatsApp) → Meta WhatsApp Business Cloud API.** Twilio is skipped. The `whatsapp_worker._send_whatsapp()` router gains a **third** provider, `WHATSAPP_PROVIDER=meta` → `_send_via_meta()`. **`pywhatkit` and the Mac AppleScript path stay intact** (DO-NOT-BREAK-THE-MAC still governs); `meta` is set only in the server's compose env.
>
> **Still in force / unchanged:** A1 (Linux Docker), A3 (bcrypt + app RBAC — NOT .NET), A4 (SQLite kept), A5 (CPU-only; **CV/LocateAnything gate stays OFF for v1**, so size the box for Ollama only). New: **material photos stored as files at `/app/data/material_photos/` in the `gi-data` volume; SQLite `inventory.Image_Filename` holds only the filename.** New AI topology: a **FastAPI RAG sidecar** wraps `ai/manual_qa.py` over Ollama (v1 = wrapper, no vector store) so Streamlit stays responsive. CI/CD: **GitHub Actions → GHCR → SSH `docker compose up -d`** on the Hetzner box.

---

## Phase 6.0 — Preflight (do BEFORE any code)

Must be resolved by the user, not the engineer, before Workstream A or B begin:

| Item | Owner | Output |
|---|---|---|
| Acquire Twilio production credentials (Account SID + Auth Token + paid WhatsApp Business number) | User + Twilio account admin | Three secrets to drop into `/Users/.../.streamlit/secrets.toml` for local Docker testing, then into Docker secrets / env vars for the server |
| HR employee export format | User + HR | CSV with these column names AT MINIMUM: `ID_Number`, `Name`, `Phone_Number`, `Department`. Sample file with 5+ real rows |
| Decide on initial tool catalogue (20 items) | User + Pilot SK | Tab-separated list of tool class names + display names + categories |
| Confirm badge QR convention | User + IT/HR | Either (a) existing badges encode the `ID_Number` directly — share an example PNG/decoded value, OR (b) we will print new badges using our QR generator |
| Pilot storeroom + SK volunteer | User | Site_ID + SK username for the pilot |

If any of these are blocked, **delay Workstream A code**. The pilot site decision in particular drives where training data is captured.

---

# WORKSTREAM A — Computer Vision Pilot (Mac first, ~2 weeks effort)

Build, train, deploy locally on the user's current Mac+Cloudflare setup. Pilot at one storeroom with ~20 tools. Tune on real data. Roll out to other sites once accuracy is proven.

## Phase 6A — CV data model + employees master (~2 days)

### Goal
DB foundation so the rest of Workstream A has something to read/write. No UI yet.

### New tables (added via self-healing `init_db()` per existing pattern)

| Table | Key columns | Purpose |
|---|---|---|
| `employees` | `ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status` (`active`/`inactive`/`suspended`), `created_by`, `created_at`, `updated_at` | Physical-labour employee master. NOT a system login (no `password_hash`). |
| `tool_catalogue` | `class_name` UNIQUE (YOLO class id — e.g. `torque_wrench_12`), `display_name`, `category`, `model_version_id` (FK to `cv_model_versions.id`), `min_confidence` (per-tool override of the 0.75 default), `created_by`, `created_at` | Catalogue of tools the YOLO model can recognise. |
| `cv_model_versions` | `version` (e.g. `v1`, `v2`), `model_path`, `classes_json` (list of class names in this model), `mAP` (mean Average Precision from training), `trained_at`, `is_active` (only one row at a time) | Versioning so an admin can swap to a new trained model without restarting. |

### Self-heal extensions on `returnable_items`

| New column | Type | Purpose |
|---|---|---|
| `cv_detected` | INTEGER (0/1) | Was this loan started via the Smart Scan flow? |
| `cv_confidence` | REAL | YOLO confidence at issue time |
| `cv_employee_id` | TEXT | Employee ID Number from the QR scan |
| `cv_tool_class` | TEXT | YOLO class name that was detected |

Audit trail of which loans were CV-assisted vs manual.

### New helpers in `database.py`
- `add_employee()`, `update_employee()`, `list_employees()`, `get_employee_by_id_number()`, `import_employees_csv()`
- `add_tool_class()`, `list_tool_catalogue()`, `set_tool_class_min_confidence()`
- `register_cv_model_version()`, `promote_cv_model_version()`, `get_active_cv_model()`

### `bug_check.py` additions (~8 new checks)
- Schema verification: all 3 new tables + 4 extended columns
- `add_employee` + duplicate `ID_Number` rejection
- `import_employees_csv` round-trip (5 rows, then idempotent re-import with one UPDATE)
- `register_cv_model_version` + `promote_cv_model_version` (only one active at a time)

### Files touched
- `database.py` (add `CREATE TABLE IF NOT EXISTS` blocks + helpers)
- `bug_check.py` (new checks)
- `requirements.txt` (no new deps yet — schema only)

---

## Phase 6B — Employee QR scanning (~1 day)

### Goal
QR encode + decode helpers + Admin Portal CRUD UI for employees. Still no detection — just QR.

### New module `ai/cv/qr.py`
```
encode_id_to_png(id_number: str) -> bytes
decode_png_to_id(image_bytes: bytes) -> str | None
```
- Encode via `qrcode[pil]`. Content = just the `ID_Number` literal string (per B6).
- Decode via `pyzbar`. Returns `None` if no QR or unreadable.
- Both functions tolerate noise / rotation up to 30° and accept the common phone-camera image sizes.

### New Admin Portal tab: 👷 Employees (12th admin tab, appended)
- Search box + paginated grid (similar pattern to existing 👥 Users tab)
- Add Employee form (ID_Number, Name, Phone, Department)
- Edit / Suspend / Reactivate per-row
- 📁 CSV Import expander (drop a CSV that matches HR's export columns → preview → confirm)
- 📥 Download QR PNG button per row (calls `encode_id_to_png` + `st.download_button`)

### `bug_check.py` additions (~3 checks)
- Encode→decode round-trip with a deterministic `ID_Number`
- CSV import with 5 rows + idempotent re-import
- QR PNG bytes are a valid PNG (header check, no crash on Pillow.open)

### Dependencies added to `requirements.txt`
```
qrcode[pil]>=7.4
pyzbar>=0.1.9
Pillow>=10.0
```

### System dependency (will land in Docker, NOT installed on the Mac since macOS already has libzbar via brew if user has it; user can `brew install zbar` if missing)
- Linux: `libzbar0` — added to Dockerfile in Workstream B

### Files touched
- `ai/__init__.py` (extend if needed)
- `ai/cv/__init__.py` (new dir)
- `ai/cv/qr.py` (new)
- `pages_internal/admin_portal.py` (new tab + renderer)
- `database.py` (the CRUD helpers from 6A wire up here)
- `bug_check.py` (new checks)

---

## Phase 6C — YOLO training pipeline (~3–4 days)

### Goal
A reproducible pipeline that takes labelled images and produces a versioned model in the DB. Admin can promote a model version to active.

### Dataset layout convention
```
data/cv_training/
  ├── torque_wrench_12/
  │     ├── img_001.jpg
  │     ├── img_001.txt   (YOLO label: class_id x y w h)
  │     ├── img_002.jpg
  │     └── ...
  ├── multimeter_fluke/
  └── ...
data.yaml   (auto-generated by the training CLI)
```

Documented in a new file `docs/cv_training_guide.md`:
- How to capture ~50 images per class under realistic storeroom lighting (overhead fluorescent, partial shadow, varied angles)
- How to label with LabelImg (or Roboflow if user prefers a hosted tool) — the CLI accepts both YOLO and COCO label formats
- How to run the training CLI

### New module `ai/cv/train.py` (CLI)
```
python ai/cv/train.py --dataset data/cv_training --epochs 50 --device cpu --out models/cv_returnable/v1/
```
- Internally uses `ultralytics` `YOLO('yolov8n.pt')` as the base model
- Trains on the dataset, validates on a 20% holdout
- Writes `models/cv_returnable/v{n}/best.pt` + `models/cv_returnable/v{n}/training_log.json`
- Registers a row in `cv_model_versions` via `register_cv_model_version()` (NOT active yet)
- Reports mAP@0.5 to stdout and saves it on the version row

### New module `ai/cv/inference.py`
```
load_active_model() -> ultralytics.YOLO     (cached via @lru_cache; invalidated by promote)
detect_tool(image_bytes: bytes) -> list[(class_name, confidence, bbox)]
```
- Lazy-loads the active model from `cv_model_versions WHERE is_active=1`
- Returns top-K detections sorted by confidence
- Honors per-tool `min_confidence` override
- Gracefully returns `[]` if no active model (e.g. before first training)

### New Admin Portal tab: 🛠️ Tool Catalogue (13th admin tab, appended)
- Add Tool Class form (class_name, display_name, category, min_confidence)
- List of model versions with mAP, trained_at, "is active" pill, **✅ Promote** button per row
- Promoting clears active on all other versions, sets the chosen one active, invalidates the inference cache

### Confidence threshold doc
- Default `min_confidence = 0.75` (per B9)
- Per-tool override available in `tool_catalogue.min_confidence`
- Documented: "Raise to 0.85+ for safety-critical tools (e.g. respirators). Lower to 0.65 for high-volume low-value items."

### `bug_check.py` additions (~5 checks — model is MOCKED to avoid GPU/dataset deps in tests)
- `register_cv_model_version` + `promote_cv_model_version` exclusivity
- `get_active_cv_model` returns expected row when promoted
- `detect_tool` returns `[]` cleanly when no active model
- `detect_tool` honors `min_confidence` filter (with mock inference)
- `min_confidence` per-tool override beats the default

### Dependencies added to `requirements.txt`
```
ultralytics>=8.1
opencv-python-headless>=4.9
```

### Files touched
- `ai/cv/train.py` (new — CLI)
- `ai/cv/inference.py` (new)
- `ai/cv/__init__.py` (exports)
- `pages_internal/admin_portal.py` (new Tool Catalogue tab)
- `database.py` (cv_model helpers)
- `docs/cv_training_guide.md` (new)
- `requirements.txt`
- `bug_check.py`

### Storage
- `models/cv_returnable/v{n}/best.pt` — `models/` dir is gitignored; backed up by Workstream B's backup service
- `data/cv_training/` — also gitignored; admin uploads images via shell/scp, not through the app

---

## Phase 6D — Camera UI integration (~3 days)

### Goal
The SK Returnable Items tab gets a new **📷 Smart Scan** expander at the top. Manual entry form below is unchanged.

### Flow design
The expander is collapsed by default. Opening it reveals a two-step camera workflow:

**Step 1: Scan Employee ID**
- `st.camera_input("Scan employee QR badge", key="_sk_cv_emp_cam")` returns a snapshot
- Pass to `ai/cv/qr.decode_png_to_id` → get `ID_Number`
- Look up via `get_employee_by_id_number(id_number)` → display Name + Phone + Department in a green card
- If no match: red card "Employee not found. Ask Admin to add them, or use manual entry below."

**Step 2: Scan Tool**
- Only shown after Step 1 succeeded
- `st.camera_input("Scan tool", key="_sk_cv_tool_cam")` → bytes
- Pass to `ai/cv/inference.detect_tool` → list of detections
- Render based on confidence tier:
  - **≥ 0.75** (high) — auto-fills the existing material picker below the expander, shows green "✓ Recognised: <display_name> (conf 0.82)" caption. SK confirms qty + expected return + clicks the existing Submit button.
  - **0.30 to 0.75** (medium) — shows top-3 candidates with confidence bars; SK picks one. Auto-fills the picker.
  - **< 0.30 or no detection** — falls through to existing manual material selectbox. Caption: "Couldn't auto-identify. Pick the tool manually below."

### Return flow (same expander, different sub-tab)
- Scan QR → look up employee → fetch their open loans from `returnable_items WHERE status='borrowed' AND borrower_match_via_employees=id_number`
- Scan tool → match against open loans for this employee (the YOLO class is in `cv_tool_class` on the loan row)
- If match → ✅ Mark Returned button
- If no match (e.g. wrong tool) → show all open loans for this employee, SK picks

### Image lifecycle (per B7)
- The image bytes from `st.camera_input` live in Streamlit's per-session memory
- After `detect_tool` returns, the bytes go out of scope
- Nothing written to disk. No image columns added to `returnable_items`. Only the detection METADATA (`cv_confidence`, `cv_tool_class`, `cv_employee_id`) persists.

### Preservation Rule
- The existing **Returnable Items tab structure** is unchanged
- The existing **manual material selectbox + borrower name + qty + expected return** form below the expander is unchanged
- The new expander is purely additive
- The existing **Mark as Returned** dropdown below remains as the manual fallback

### `bug_check.py` additions (~4 checks — mocking the inference + QR layers)
- End-to-end issue flow: mock QR → mock detection at 0.85 → assert `returnable_items` row written with `cv_detected=1`
- Low-confidence flow: mock detection at 0.55 → assert top-3 picker shown, manual confirm wires correctly
- No-detection flow: mock empty detection → assert fallback to manual picker
- Return flow: mock QR + matching tool → assert `status='returned'`

### Files touched
- `pages_internal/daily_issue_log.py` (new expander INSIDE the existing Returnable Items tab; existing code below unchanged)
- `ai/cv/inference.py` (already in 6C)
- `ai/cv/qr.py` (already in 6B)
- `database.py` (helper: `find_open_loans_for_employee(id_number)`)
- `bug_check.py`

---

## Phase 6E — Hourly returnable reminder sweep (~1 day)

### Goal
Automatic WhatsApp reminders to the borrower as the expected return time approaches and passes.

### New helper `database.sweep_returnable_reminders(now: datetime | None = None)`

Iterates open `returnable_items` rows and fires events at four offsets relative to `expected_return_time`:

| Offset | Severity | Recipient |
|---|---|---|
| T−2h (2 hours before) | `info` | Borrower (via `employees.Phone_Number`) |
| T−0 (at the expected return time) | `warning` | Borrower |
| T+2h (overdue by 2 hours) | `warning` | Borrower + Site SK |
| T+24h (escalation) | `critical` | Borrower + Site SK + Site HOD |

### Dedup
Reuses the existing `delivery_reminders_sent` table with a new `ref_type='returnable_loan'`. UNIQUE(`ref_type`, `ref_number`, `target_date`, `offset_days`) blocks per-(loan, offset) double-fires.

Note: `offset_days` is overloaded here — for returnable loans we use HOURS not days, encoded as negative integers for clarity (e.g. `offset_days=-2` means T−2h). Documented in the helper docstring.

### Wired into `whatsapp_worker.run_worker_loop()`
- Runs every hour (NOT every day like the delivery sweep)
- New helper `_maybe_run_returnable_reminders()` mirrors `_maybe_run_delivery_reminders()` but with an hour-bucket marker in `app_settings.returnable_reminders_last_run_hour` to skip 60-sec poll repeats inside the same hour
- Independent of the daily delivery sweep — failures in one don't block the other

### WhatsApp recipient resolution
- Borrower phone comes from `employees.Phone_Number` (looked up via `cv_employee_id` on the loan row)
- If the loan was created MANUALLY (no CV scan), fall back to the existing `borrower_phone` column on `returnable_items`
- If neither resolves: skip the WhatsApp ping, still queue the in-app notification

### `bug_check.py` additions (~4 checks)
- Sweep fires expected count at each offset
- Idempotent: re-running same sweep with same `now` fires zero new events
- Borrower phone resolution: CV-loan path uses `employees`; manual-loan path uses `returnable_items.borrower_phone`
- T+24h escalation pings SK + HOD (not just borrower)

### `config.py` additions
```python
WHATSAPP_TRIGGERS = {
    ...existing keys...
    # Phase 6E — returnable loan reminders
    "returnable_reminder_t_minus_2h": True,
    "returnable_reminder_t_zero":     True,
    "returnable_reminder_t_plus_2h":  True,
    "returnable_reminder_t_plus_24h": True,
}
```

### Files touched
- `database.py` (sweep helper)
- `whatsapp_worker.py` (hourly-bucket marker + call site)
- `config.py` (4 new trigger keys)
- `bug_check.py`

---

## Phase 6F — Tests + pilot (~1–2 days)

### Goal
End-to-end validation + first real-world model train at the pilot storeroom.

### Acceptance criteria for v3.1 release
- 315/315 pytest still green
- 270+/270+ bug_check (Phase 6A–E adds ~24 checks)
- ≥ 85% of high-confidence (≥0.75) detections are correct on real footage at the pilot site
- Zero crashes on `requirements.txt` install for both Mac and Linux Docker (with `requirements-server.txt`)
- Manual fallback works when CV is disabled via env var `GI_CV_ENABLED=0` — the Returnable Items tab still functions exactly as v3.0

### Pilot procedure
1. SK volunteer captures ~50 images per chosen tool under storeroom conditions (varied lighting, angles, partial occlusion)
2. Engineer labels via LabelImg (~1 hour per class)
3. `python ai/cv/train.py --dataset data/cv_training/ --epochs 50` (3–4 hours CPU on M-series Mac)
4. Admin Portal → 🛠️ Tool Catalogue → promote v1 to active
5. SK runs the Smart Scan flow for 1 week, with manual fallback always available
6. Review at end of week: confusion matrix + per-tool accuracy → adjust `min_confidence` per tool

### Documentation updates
- `USER_MANUAL.md` §4.5 (Returnable Items): append §4.5.0 "📷 Smart Scan workflow" subsection (additive — existing §4.5.1+ unchanged)
- `handoff.md` §6 Schema Additions: append Phase 6A schema block
- `SOP.md`: SK quick reference card adds a "When CV detection is wrong" bullet

---

# WORKSTREAM B — Enterprise Docker Deployment (~1.5 weeks effort)

Independent of Workstream A. Can be tackled in parallel or after. Strictly Linux Docker per A1. **Does NOT break the Mac dev path.**

## Phase 6G — Docker foundation (~2 days)

### Goal
Single `docker compose up -d` brings the entire stack live on a fresh Linux box.

### New files
- `Dockerfile` (multi-stage):
  - Stage 1 `builder`: `python:3.12-slim` + build deps + `pip install -r requirements-server.txt --target /install`
  - Stage 2 `runtime`: `python:3.12-slim` + runtime deps (`libzbar0`, `libgomp1`, `tini`) + copy `/install` from builder + copy app code. Final image ~1.5GB. Drops to non-root user `gihub`.
- `docker-compose.yml`: three services on a shared internal bridge network `gi-net`:
  - `app` — the Streamlit app, port 8501 exposed to host (intranet routes here)
  - `ollama` — official `ollama/ollama:latest` image, **NO port mapping** (internal-only per A2 + "AI stays safe")
  - `backup` — alpine + busybox + sqlite3 + rsync, no port, runs cron
- `requirements-server.txt` — derived from `requirements.txt` MINUS `pywhatkit` and any macOS-specific deps (e.g. `pyobjc`). Mac developers still use `requirements.txt`.
- `.dockerignore` — excludes `.venv`, `.git`, `gi_database.db` (volume), `reports_archive/`, `data/cv_training/`, `models/cv_returnable/` (mounted), `BUG_REPORT.md`, `*.pdf`
- `docker-compose.override.yml.example` — template for admin to add their NAS path, custom env, etc.
- `docs/DEPLOY.md` — 6.K landing page

### Volumes
```yaml
volumes:
  gi-data:     # SQLite DB + uploads + entry attachments
  gi-models:   # CV model artifacts (Workstream A output)
  gi-ollama:   # Ollama model cache (qwen2.5-coder, llama3.1, etc.)
  gi-backups:  # backup destination (admin's override binds this to NAS)
```

### Volume binds vs named volumes
Use **named volumes** for `gi-data`, `gi-models`, `gi-ollama` (Docker manages location). Use **bind mount** for `gi-backups` so admin can point it at a NAS share via `docker-compose.override.yml`.

### `bug_check.py` not affected by 6G (Dockerfile is infra, not code).

### Smoke test gate
`docker compose build && docker compose up -d && docker compose ps` → all three services `running`. App reachable at `http://localhost:8501`.

---

## Phase 6H — Dynamic WhatsApp provider toggle (~1 day) **[REVISED per user constraint]**

### Goal
Runtime-evaluated env var `WHATSAPP_PROVIDER` selects the sender backend. **`pywhatkit` is NOT removed from the codebase.** The current Mac+Cloudflare demo path keeps working exactly as today. Docker sets the env var to `twilio` so the server uses the API path.

### Decision matrix
| Env value | Sender backend used | Where typically set |
|---|---|---|
| unset (default) | `pywhatkit` (current Mac AppleScript+Chrome flow) | local Mac (`streamlit run main.py` with no env var) |
| `pywhatkit` (explicit) | `pywhatkit` | local Mac, optional explicit form |
| `twilio` | Twilio API | Docker `docker-compose.yml` env block |
| `auto` (reserved) | Try Twilio if creds present, else `pywhatkit` | not used in Phase 6 — kept for future flexibility |

### Code change in `whatsapp_worker.py`
The worker already lazy-imports `pywhatkit` (see existing Hidden Surprise #4). Phase 6H formalises the switch:

```python
# whatsapp_worker.py — provider toggle at module top, evaluated once
import os

WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "pywhatkit").lower()
if WHATSAPP_PROVIDER not in ("pywhatkit", "twilio", "auto"):
    print(f"⚠️  Unknown WHATSAPP_PROVIDER={WHATSAPP_PROVIDER!r}; defaulting to 'pywhatkit'")
    WHATSAPP_PROVIDER = "pywhatkit"
```

`_send_whatsapp()` becomes a router:
```python
def _send_whatsapp(phone, message):
    if WHATSAPP_PROVIDER == "twilio":
        return _send_via_twilio(phone, message)
    if WHATSAPP_PROVIDER == "pywhatkit":
        return _send_via_pywhatkit_macos(phone, message)  # current AppleScript path
    if WHATSAPP_PROVIDER == "auto":
        sid, _, _ = _twilio_config()
        if sid:
            return _send_via_twilio(phone, message)
        return _send_via_pywhatkit_macos(phone, message)
    return False  # unreachable thanks to the module-top normalisation
```

### Critical: imports stay guarded
The `pywhatkit` import remains lazy and inside `_send_via_pywhatkit_macos`. If the worker is running in `WHATSAPP_PROVIDER=twilio` mode, `pywhatkit` is never imported even if it's installed. This is what lets the Docker image safely OMIT `pywhatkit` from its requirements while the Mac dev install keeps it.

### docker-compose.yml env block (in 6G)
```yaml
services:
  app:
    environment:
      - WHATSAPP_PROVIDER=twilio
      - GI_DEPLOYMENT_MODE=server
      - OLLAMA_HOST=http://ollama:11434
    secrets:
      - twilio_account_sid
      - twilio_auth_token
      - twilio_from_number
```

### Mac dev — no change required
The user keeps running `streamlit run main.py` exactly as today. `WHATSAPP_PROVIDER` is unset → defaults to `pywhatkit` → current Mac AppleScript flow runs unchanged. **Management demo is preserved.**

### `bug_check.py` additions (~3 checks)
- With `WHATSAPP_PROVIDER` unset → router selects pywhatkit branch (mock the sender call, assert function name)
- With `WHATSAPP_PROVIDER=twilio` → router selects twilio branch (mock Twilio client, assert function name)
- With `WHATSAPP_PROVIDER=bogus` → falls back to pywhatkit with warning log

### Files touched
- `whatsapp_worker.py` (provider toggle + router; existing `_send_via_chrome_macos` and Twilio helpers stay)
- `config.py` (document the env var alongside `WHATSAPP_ENABLED` / `WHATSAPP_TRIGGERS`)
- `bug_check.py`

### What 6H deliberately does NOT do
- ❌ Delete `pywhatkit` from `requirements.txt`
- ❌ Remove `_send_via_chrome_macos` or its AppleScript helpers
- ❌ Break the launchd plist install path
- ❌ Force users to pick a provider — the unset-default = current behavior

---

## Phase 6I — Ollama containerization (~2 days)

### Goal
Ollama runs as its own service, INTERNAL-ONLY (no host port mapping), accessed by the app via container DNS. Models persist across container restarts.

### docker-compose.yml `ollama` service
```yaml
ollama:
  image: ollama/ollama:latest
  volumes:
    - gi-ollama:/root/.ollama
  networks: [gi-net]
  # NO ports: — internal only per A2 + "AI stays safe"
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "ollama", "list"]
    interval: 30s
    start_period: 30s
```

### Init script `ollama/init.sh` (run once on first start via compose `command:` or a one-shot init container)
- `ollama pull qwen2.5-coder:7b` (NL search)
- `ollama pull llama3.1:8b` (chat + insights)
- Optional: `ollama pull qwen2.5vl:7b` if `GI_ENABLE_VISION_MODEL=1` (gated because the vision model is heavy and many sites won't use OCR)
- Idempotent (Ollama skips already-pulled models)

### App-side config
- `ai/client.py` already reads `OLLAMA_HOST` from env — verified to work with container hostname `http://ollama:11434`
- For local Mac dev, `OLLAMA_HOST` defaults to `http://localhost:11434` — unchanged

### Security guarantee (per "AI stays safe internal")
- Ollama service has NO `ports:` block → not bound to host network
- Only reachable via internal `gi-net` bridge → only `app` container can talk to it
- Reverse proxy (corporate Nginx / Traefik fronting the app, if any) does NOT route to Ollama
- Cloudflare Tunnel (if still used) only proxies the app container, never Ollama

### `bug_check.py` additions (~2 checks)
- `ai/client.py` reads `OLLAMA_HOST` from env (test by setting + asserting the parsed URL)
- Falls back to localhost when env unset

### Files touched
- `docker-compose.yml` (in 6G — Ollama service block)
- `ollama/init.sh` (new)
- `ai/client.py` (verify env var read; likely no code change needed)
- `bug_check.py`

---

## Phase 6J — Backup automation + NAS persistence (~1 day)

### Goal
Nightly backups land on the corporate NAS via a bind-mounted volume. 14-day retention. Surface failures in the Admin Overview.

### `backup` service
- Image: `alpine:3.19` + apk install `sqlite tini rsync` + a small `crond` config
- Cron entry: `0 2 * * * /backup.sh`
- `backup.sh`:
  1. `sqlite3 /data/gi_database.db ".backup /backups/sqlite_$(date +%Y%m%d_%H%M%S).db"`
  2. `rsync -a /data/uploads/ /backups/uploads_latest/`
  3. `rsync -a /models/ /backups/models_latest/`
  4. `find /backups -name 'sqlite_*.db' -mtime +14 -delete`
  5. Write timestamp to a marker file `/backups/.last_success`
  6. On any failure: write `/backups/.last_failure` with error log

### Admin Overview enhancement
- Admin Portal → 🖥️ Overview → 🔧 Service Health card adds a row:
  - "Backup" — green if `.last_success` is within 48h, amber if 48–72h, red if >72h or `.last_failure` newer than `.last_success`
- Source: a new helper `get_backup_health()` that reads the two marker files

### NAS mount
- `docker-compose.override.yml.example` shows: `volumes: gi-backups: { driver: local, driver_opts: { type: nfs, o: addr=corp-nas.internal,rw, device: ":/exports/gi_hub" } }`
- Admin replaces with their NAS path. If no NAS, falls back to a local bind-mount.

### Restore procedure (documented in `DEPLOY.md`)
1. `docker compose down`
2. `cp /backups/sqlite_<latest>.db /var/lib/docker/volumes/gi-data/_data/gi_database.db` (or use a `docker run --rm -v gi-data:/data alpine cp` invocation)
3. `rsync -a /backups/uploads_latest/ /var/lib/docker/volumes/gi-data/_data/uploads/`
4. `docker compose up -d`
5. Run smoke test (§6K)

### `bug_check.py` not affected (backup is infra cron, tested manually).

### Files touched
- `backup/Dockerfile` (or inline image override in compose)
- `backup/backup.sh` (new)
- `backup/crontab` (new)
- `docker-compose.yml` (backup service)
- `docker-compose.override.yml.example` (NAS template)
- `database.py` (`get_backup_health()` helper)
- `pages_internal/admin_portal.py` (extend Service Health card)
- `docs/DEPLOY.md` (restore procedure)

---

## Phase 6K — Deployment playbook + smoke test (~1 day)

### Goal
A single document that a corporate sysadmin can follow from a blank Linux VM to a running GI Hub.

### `docs/DEPLOY.md` structure
1. **System requirements** — Linux (Ubuntu 22.04+ recommended; RHEL 9+ also fine), Docker 24+, Docker Compose v2, 4 vCPU / 8GB RAM / 50GB disk minimum (16GB+ recommended if vision model enabled)
2. **Network requirements** — intranet DNS entry (e.g. `gi.corp.local` → server IP), port 8501 reachable from intranet users, NAS share path
3. **Twilio setup** — sign-up steps, paid number provisioning, credentials acquisition
4. **First start** — clone repo, edit `docker-compose.override.yml` for NAS + secrets, `docker compose up -d`, wait for Ollama init (~15 min on first run for model pulls), smoke test
5. **User onboarding** — admin login, change default password, create logistics + warehouse user accounts, import HR employee CSV (Phase 6A), train first YOLO model (Phase 6C)
6. **Backups + monitoring** — backup verification, log file locations, common failure modes
7. **Updates** — `git pull && docker compose build && docker compose up -d` (config-only updates skip the build step)
8. **Rollback** — restore-from-backup procedure (mirror of §6J restore)

### Smoke test checklist (also in `DEPLOY.md`)
- [ ] `docker compose ps` — all 3 services `running`
- [ ] Login as admin succeeds; sidebar bell renders without error
- [ ] Create a test PR → submit to logistics → in-app notification fires
- [ ] Sidebar Hub Assistant returns an answer (validates Ollama reachability)
- [ ] Admin Portal → 📱 WhatsApp Console → send manual test → `whatsapp_queue.status='sent'` (validates Twilio)
- [ ] After 24h wait OR `docker compose exec backup /backup.sh` → check `/backups/` has fresh `sqlite_*.db` (validates backup pipeline)
- [ ] If CV pilot deployed: scan a test QR + tool → loan recorded with `cv_detected=1`
- [ ] `docker logs gi-app | grep "delivery_reminders"` shows daily sweep (validates worker)

### Rollback procedure
1. `docker compose down`
2. Restore SQLite via the §6J procedure
3. `docker compose up -d`
4. Re-run smoke test

### Acceptance gate for v3.2 release
- All smoke test items pass on a fresh Linux VM with no prior state
- `docker compose down && up -d` cycle preserves all data
- Mac `streamlit run main.py` still works identically (regression check)
- bug_check + pytest still green

### Files touched
- `docs/DEPLOY.md` (new, comprehensive)

---

## Phase 6 — schema additions consolidated (column-by-column per `handoff.md` §6 format)

New tables (3) from Workstream A:

| Table | Key columns | Purpose |
|---|---|---|
| `employees` | `ID_Number` UNIQUE, `Name`, `Phone_Number`, `Department`, `status`, `created_by`, `created_at`, `updated_at` | Physical-labour master, separate from `users` |
| `tool_catalogue` | `class_name` UNIQUE, `display_name`, `category`, `model_version_id`, `min_confidence` (REAL, NULL = use global 0.75) | What the YOLO model can recognise |
| `cv_model_versions` | `version`, `model_path`, `classes_json`, `mAP`, `trained_at`, `is_active` (only one TRUE at a time) | Model versioning |

Extended existing tables:

| Table | New columns | Purpose |
|---|---|---|
| `returnable_items` | `cv_detected` (INTEGER), `cv_confidence` (REAL), `cv_employee_id` (TEXT), `cv_tool_class` (TEXT) | Audit which loans went through Smart Scan |

Reuses existing tables (no schema change):

| Table | What's added | Purpose |
|---|---|---|
| `delivery_reminders_sent` | New `ref_type='returnable_loan'` value | Dedup for the hourly returnable reminder sweep |
| `app_settings` | New keys: `returnable_reminders_last_run_hour` | Hour-bucket marker so the 60-sec poll loop skips repeats |

---

## Phase 6 — new dependencies consolidated

### Python (added to `requirements.txt`)
```
qrcode[pil]>=7.4
pyzbar>=0.1.9
Pillow>=10.0
ultralytics>=8.1
opencv-python-headless>=4.9
```

### Python (server-only, kept OUT of `requirements-server.txt`)
- None new — all Phase 6 Python deps are cross-platform

### Python (server-only, REMOVED from `requirements-server.txt` — but stays in `requirements.txt` for Mac)
- `pywhatkit` (Mac WhatsApp via AppleScript+Chrome)
- `pyobjc-framework-*` (if any are pulled in by pywhatkit transitively)

### Linux system packages (Dockerfile `apt-get install`)
```
libzbar0      # pyzbar runtime
libgomp1      # OpenCV runtime
libglib2.0-0  # OpenCV
tini          # PID 1 reaper for clean container shutdown
```

### macOS system packages (documented in `docs/cv_training_guide.md`)
```bash
brew install zbar     # only if pyzbar import fails
```

---

## Phase 6 — new env vars consolidated

| Variable | Default | Allowed values | Purpose |
|---|---|---|---|
| `WHATSAPP_PROVIDER` | `pywhatkit` (unset) | `pywhatkit` / `twilio` / `auto` | Phase 6H — sender backend |
| `GI_DEPLOYMENT_MODE` | unset | `server` / unset | Phase 6G — tells code it's in Docker so it can disable Mac-only paths |
| `GI_CV_ENABLED` | `1` | `0` / `1` | Phase 6D — hard kill switch for the Smart Scan expander |
| `GI_ENABLE_VISION_MODEL` | `0` | `0` / `1` | Phase 6I — whether to pull qwen2.5vl:7b for OCR |
| `OLLAMA_HOST` | `http://localhost:11434` | URL | Phase 6I — set to `http://ollama:11434` in Docker |
| `TWILIO_ACCOUNT_SID` | unset | Twilio SID | Phase 6H — Twilio creds (also can come from Docker secrets or secrets.toml) |
| `TWILIO_AUTH_TOKEN` | unset | Twilio token | ↑ |
| `TWILIO_FROM_NUMBER` | unset | `whatsapp:+...` | ↑ |

---

## Phase 6 — risk register

| Risk | Mitigation | Owner |
|---|---|---|
| **MUST NOT happen:** Mac WhatsApp path broken before management demo | Phase 6H is **explicit env-var toggle**, default = pywhatkit, NO code path deletes pywhatkit | Engineer |
| Tool images in real storeroom lighting are noisy | Capture training set under actual conditions; aggressive YOLO augmentation | Pilot SK + Engineer |
| Ollama container OOM on 8GB box if vision model loads | `GI_ENABLE_VISION_MODEL=0` by default; document 16GB min if enabled | Sysadmin |
| Twilio sandbox requires per-recipient opt-in | Use paid number before go-live; sandbox only for dev | User (procurement) |
| NAS unmounted silently → backup fails for days | Admin Overview Service Health amber/red on stale `.last_success` marker | Admin |
| QR codes on existing badges have a different format | Phase 6.0 preflight resolves this; system has manual-entry fallback regardless | User + HR |
| Employee table drifts when HR onboards new staff | CSV re-import is idempotent (UPDATE on existing `ID_Number`, INSERT on new); document monthly sync | Admin |
| YOLO model accuracy too low at pilot site | Confidence threshold raised; manual fallback always available; iterate dataset | Engineer + Pilot SK |
| Streamlit `st.camera_input` blocked by corporate browser policy | Per B4 decision — fallback to manual entry always present; no broken UX | — |
| pywhatkit Linux pip install fails inside Docker | `requirements-server.txt` omits it entirely; combined with Phase 6H router never imports it in server mode | Engineer |

---

## Phase 6 — execution order summary for a fresh session

When you (the next session) read this cold, this is your starting checklist:

1. **Confirm Phase 6.0 preflight items are resolved.** If any block is still red, surface it to the user before writing code.
2. **Decide workstream order with the user.** Recommended: Workstream A first (CV pilot on Mac) → then Workstream B (Docker). Both can also be parallel if the user has bandwidth.
3. **Workstream A starting point:** Open `database.py`, locate the existing `init_db()` body, append the Phase 6A schema block (mirror the existing Phase C round structure). Then `add_employee()` helpers. Then `bug_check.py` schema verification.
4. **Workstream B starting point:** Create `Dockerfile` + `requirements-server.txt` (`requirements.txt` minus `pywhatkit`). Then `docker-compose.yml`. Verify `docker compose build` succeeds before continuing to 6H.
5. **Run `bug_check.py` + `pytest` after EVERY phase.** Phase 6 must not regress 241/241 + 315/315. After Workstream A is done, expect ~270/270 + 315/315.
6. **Update `USER_MANUAL.md` + `SOP.md` after each user-visible change.** Maintain the additive Preservation Rule pattern from Phases 1–5.
7. **Touch `pywhatkit` only via the Phase 6H router.** If you find yourself about to delete the import or the AppleScript helper, you've misunderstood the constraint — re-read the "Critical operating constraint" subsection above.

---

## Phase 6 — out of scope (intentionally deferred)

The following were considered and **explicitly deferred** to Phase 7+ to keep Phase 6 shippable:

- SSO / Active Directory integration (A3 decision — bcrypt stays for now)
- Postgres migration (A4 decision — SQLite stays for now)
- Multi-warehouse CV rollout (X2 decision — pilot one site first)
- GPU acceleration for YOLO (A5 decision — CPU-first design)
- Live video WebRTC for camera capture (B4 decision — `st.camera_input` snapshot only)
- Cloudflare Tunnel on the company server (A2 decision — intranet only)
- Storing image bytes for audit (B7 decision — in-memory processing only)
- Force-close UNDO (deferred from Phase 5 §3)
- Vendor master maintenance UI (deferred from Phase 5 §3)

---

**End of handoff. Read this file first, then `USER_MANUAL.md` §13 + §15 + §16 for the latest UI reference (including the new Logistics + Warehouse portals). For day-to-day operating procedure across all 5 roles, read `SOP.md`. For the next development milestones, read `Phase 6 — Enterprise Deployment & Computer Vision (forward roadmap)` above. Run `python bug_check.py` before and after any database/mailer/page change.**
