"""
backend/api/service_tests.py — service-level + auth/role guard tests (CI gate).

Two suites, both run against a **populated** Postgres (the same one dual_ci.py
loads from gi_database.db):

  A. Service invariants — call the write services inside a transaction, assert
     their effects (rows, audit, notifications) via count-deltas, then ROLL BACK.
     Nothing persists, so there is no cleanup and no divergence from SQLite.

  B. Auth/role guards — drive the real ASGI app with httpx and assert the
     endpoint guards: 401 without a token, 403 for the wrong role (including the
     master-data write gate), 200 on an open read.

Run:  DATABASE_URL=postgresql+psycopg2://…  python backend/api/service_tests.py
Exit code is non-zero if any check fails (so CI fails the build).
"""
from __future__ import annotations

import asyncio
import sys

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from .db import SessionLocal, engine
from .main import app
from .services import ledger, notifications, procurement, supervisor

_MD = ledger._MD
pr_master_t = _MD.tables["pr_master"]
audit_t = _MD.tables["system_audit_log"]
notif_t = _MD.tables["app_notifications"]
smr_t = _MD.tables["supervisor_material_requests"]
smr_items_t = _MD.tables["supervisor_material_request_items"]
pending_issues_t = _MD.tables["pending_issues"]
receipts_t = _MD.tables["receipts"]
lots_t = _MD.tables["lots"]

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    mark = "✅" if cond else "❌"
    line = f"  {mark} {name}"
    if not cond and detail:
        line += f"  — {detail}"
    print(line)


async def _count(session, table, *where) -> int:
    stmt = select(func.count()).select_from(table)
    for w in where:
        stmt = stmt.where(w)
    return (await session.execute(stmt)).scalar_one()


# --- Suite A: service invariants (rolled back) -------------------------------
async def test_create_and_submit_pr():
    async with SessionLocal() as s:
        res = await procurement.create_pr(
            s, username="svc_hod", site_id="CNCEC",
            lines=[{"SAP_Code": "1001", "Requested_Qty": 3},
                   {"SAP_Code": "1002", "Requested_Qty": 2}])
        pr = res.get("pr_number")
        check("create_pr returns created", res.get("created") is True, str(res))
        n_lines = await _count(s, pr_master_t, pr_master_t.c["PR_Number"] == pr)
        check("create_pr writes one row per line", n_lines == 2, f"got {n_lines}")
        n_audit = await _count(s, audit_t, audit_t.c["action_type"] == "CREATE_PR",
                               audit_t.c["details"].like(f"%{pr}%"))
        check("create_pr writes a CREATE_PR audit", n_audit == 1, f"got {n_audit}")

        sub = await procurement.submit_pr(s, username="svc_hod", pr_number=pr, site_id="CNCEC")
        check("submit_pr succeeds", sub.get("submitted") is True, str(sub))
        n_notif = await _count(
            s, notif_t, notif_t.c["event_key"] == "pr_submitted_to_logistics",
            notif_t.c["related_ref"] == pr, notif_t.c["recipient_role"] == "logistics")
        check("submit_pr notifies logistics", n_notif == 1, f"got {n_notif}")
        await s.rollback()


async def test_smr_create_and_approve():
    async with SessionLocal() as s:
        created = await supervisor.create_smr(
            s, supervisor="svc_sup", site_id="CNCEC", worker_id="30001",
            job_tank_place="svc test", old_ppe_returned=1, no_return_reason=None,
            items=[{"SAP_Code": "1001", "Requested_Qty": 2}])
        rid = created.get("request_id")
        no = created.get("request_no")
        check("create_smr succeeds", created.get("created") is True, str(created))
        n_items = await _count(s, smr_items_t, smr_items_t.c["request_id"] == rid)
        check("create_smr writes items", n_items == 1, f"got {n_items}")
        n_notif = await _count(
            s, notif_t, notif_t.c["event_key"] == "smr_created",
            notif_t.c["related_ref"] == no, notif_t.c["recipient_role"] == "store_keeper",
            notif_t.c["recipient_site"] == "CNCEC")
        check("create_smr notifies store-keeper@site", n_notif == 1, f"got {n_notif}")

        item_id = (await supervisor.smr_items(s, rid))[0]["id"]
        appr = await supervisor.approve_smr(s, sk_username="svc_sk", request_id=rid,
                                            qty_overrides={item_id: 1.5})
        check("approve_smr succeeds", appr.get("approved") is True, str(appr))
        n_pending = await _count(
            s, pending_issues_t, pending_issues_t.c["Source_Ref"].like(f"SMR:{no}:%"),
            pending_issues_t.c["status"] == "pending_hod")
        check("approve_smr stages pending_issues", n_pending == 1, f"got {n_pending}")
        staged_qty = (await s.execute(select(pending_issues_t.c["Quantity"]).where(
            pending_issues_t.c["Source_Ref"].like(f"SMR:{no}:%")))).scalar_one()
        check("SK qty-override lands on the staged issue (2 → 1.5)",
              abs(float(staged_qty) - 1.5) < 1e-9, f"got {staged_qty}")
        n_fb = await _count(
            s, notif_t, notif_t.c["event_key"] == "smr_approved",
            notif_t.c["related_ref"] == no, notif_t.c["recipient_user"] == "svc_sup")
        check("approve_smr notifies the requester", n_fb == 1, f"got {n_fb}")
        await s.rollback()


async def test_receipt_ledger():
    async with SessionLocal() as s:
        r_before = await _count(s, receipts_t, receipts_t.c["SAP_Code"] == "1001",
                                receipts_t.c["Site_ID"] == "CNCEC")
        a_before = await _count(s, audit_t, audit_t.c["action_type"] == "POST_RECEIPT")
        res = await ledger.post_receipt(s, username="svc", data={
            "Date": "2026-07-04", "SAP_Code": "1001", "Quantity": 5, "Site_ID": "CNCEC",
            "Supplier": "svctest", "Remarks": "svctest", "Expiry_Date": "2027-06-01"})
        check("post_receipt returns a receipt_id", bool(res.get("receipt_id")), str(res))
        r_after = await _count(s, receipts_t, receipts_t.c["SAP_Code"] == "1001",
                               receipts_t.c["Site_ID"] == "CNCEC")
        check("post_receipt inserts one receipt", r_after == r_before + 1)
        lot = res.get("lot_number")
        n_lot = await _count(s, lots_t, lots_t.c["Lot_Number"] == lot)
        check("post_receipt auto-creates the lot", bool(lot) and n_lot >= 1, f"lot={lot}")
        a_after = await _count(s, audit_t, audit_t.c["action_type"] == "POST_RECEIPT")
        check("post_receipt writes an audit row", a_after == a_before + 1)
        await s.rollback()


async def test_submitter_resolution():
    """hod._submitter maps each pending kind to its submitter column (receipts=None)."""
    from . import hod
    async with SessionLocal() as s:
        r = await ledger.stage_return(s, username="svc_sub", data={
            "Date": "2026-07-04", "SAP_Code": "1001", "Quantity": 1, "Site_ID": "CNCEC"})
        sub = await hod._submitter(s, "returns", r["pending_id"])
        check("_submitter resolves the return's submitter", sub == "svc_sub", f"got {sub}")
        rr = await ledger.stage_receipt(s, username="svc_sub", data={
            "Date": "2026-07-04", "SAP_Code": "1001", "Quantity": 1, "Site_ID": "CNCEC"})
        none_sub = await hod._submitter(s, "receipts", rr["pending_id"])
        check("_submitter is None for receipts (no submitter column)", none_sub is None, f"got {none_sub}")
        await s.rollback()


async def test_notification_visibility():
    async with SessionLocal() as s:
        await notifications.notify(s, event_key="svc_role_ev", title="t", recipient_role="logistics")
        await notifications.notify(s, event_key="svc_user_ev", title="t", recipient_user="svc_alice")

        sk = await notifications.list_for(s, username="svc_bob", role="store_keeper",
                                          site_id="CNCEC", warehouse_id=None, limit=200)
        check("isolation: store-keeper can't see a logistics broadcast",
              not any(n["event_key"] == "svc_role_ev" for n in sk))
        lg = await notifications.list_for(s, username="svc_carol", role="logistics",
                                          site_id=None, warehouse_id=None, limit=200)
        check("logistics sees the role broadcast",
              any(n["event_key"] == "svc_role_ev" for n in lg))
        al = await notifications.list_for(s, username="svc_alice", role="store_keeper",
                                          site_id="ZZ", warehouse_id=None, limit=200)
        check("user-targeted notification is visible to its recipient",
              any(n["event_key"] == "svc_user_ev" for n in al))

        nid = (await s.execute(select(notif_t.c["id"])
               .where(notif_t.c["event_key"] == "svc_user_ev"))).scalars().first()
        ok = await notifications.mark_read(s, notif_id=nid, username="svc_bob",
                                           role="store_keeper", site_id="CNCEC", warehouse_id=None)
        check("mark_read guard blocks a non-recipient", ok is False)
        ok2 = await notifications.mark_read(s, notif_id=nid, username="svc_alice",
                                            role="store_keeper", site_id="ZZ", warehouse_id=None)
        check("mark_read succeeds for the recipient", ok2 is True)
        await s.rollback()


# --- Suite B: auth/role guards (live ASGI app) -------------------------------
async def test_auth_guards():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://svc") as ac:
        async def token(u, p):
            r = await ac.post("/auth/login", json={"username": u, "password": p})
            return r.json().get("access_token")

        def H(t):
            return {"Authorization": f"Bearer {t}"}

        admin_t = await token("admin", "admin2026")
        worker_t = await token("worker", "floor2026")
        check("admin + worker can log in", bool(admin_t) and bool(worker_t))

        r = await ac.get("/inventory")
        check("no token → 401 on a protected read", r.status_code == 401, f"got {r.status_code}")
        r = await ac.get("/inventory", headers=H(worker_t))
        check("worker → 200 on an open read", r.status_code == 200, f"got {r.status_code}")

        for path in ("/admin/users", "/hod/pending", "/logistics/prs"):
            r = await ac.get(path, headers=H(worker_t))
            check(f"worker (lvl 0) → 403 on {path}", r.status_code == 403, f"got {r.status_code}")

        # The hardening fix: master-data writes are role-gated (level ≥ 3).
        r = await ac.post("/vendors", headers=H(worker_t), json={"Vendor_Name": "svc_x"})
        check("worker → 403 on POST /vendors (master-data write gate)",
              r.status_code == 403, f"got {r.status_code}")
        # Admin passes the role gate (bad column → 422, proving the guard let it through
        # without persisting anything).
        r = await ac.post("/vendors", headers=H(admin_t), json={"__not_a_column__": 1})
        check("admin passes the write gate (422 on bad body, not 403)",
              r.status_code == 422, f"got {r.status_code}")
        check("admin → 200 on /admin/users",
              (await ac.get("/admin/users", headers=H(admin_t))).status_code == 200)

        # Inventory editor guards (non-persisting: duplicate SAP + delete-with-movements).
        r = await ac.post("/admin/inventory", headers=H(worker_t), json={"SAP_Code": "svc_x"})
        check("worker → 403 on POST /admin/inventory", r.status_code == 403, f"got {r.status_code}")
        r = await ac.post("/admin/inventory", headers=H(admin_t), json={"SAP_Code": "1001"})
        check("admin → 409 creating a duplicate SAP (no persist)", r.status_code == 409, f"got {r.status_code}")
        r = await ac.request("DELETE", "/admin/inventory/1001", headers=H(admin_t))
        check("admin → 409 deleting an item with movements", r.status_code == 409, f"got {r.status_code}")

        # 2FA self-enrollment guards (non-persisting).
        r = await ac.get("/auth/2fa/status", headers=H(worker_t))
        check("2fa status → 200 for any authed user",
              r.status_code == 200 and r.json().get("enabled") is False, f"got {r.status_code}")
        r = await ac.post("/auth/2fa/verify", headers=H(worker_t), json={"code": "000000"})
        check("2fa verify without enrollment → 409", r.status_code == 409, f"got {r.status_code}")
        r = await ac.post("/auth/2fa/disable", headers=H(worker_t), json={"code": "000000"})
        check("2fa disable when not enabled → 409", r.status_code == 409, f"got {r.status_code}")

        # Reports (read-only, level ≥ 2): role gate + format validation.
        r = await ac.get("/reports/stock", params={"format": "xlsx"}, headers=H(worker_t))
        check("worker (lvl 0) → 403 on a report", r.status_code == 403, f"got {r.status_code}")
        r = await ac.get("/reports", headers=H(admin_t))
        check("admin → 200 on /reports list",
              r.status_code == 200 and len(r.json().get("reports", [])) >= 1, f"got {r.status_code}")
        r = await ac.get("/reports/stock", params={"format": "xlsx"}, headers=H(admin_t))
        check("stock report xlsx → 200 + spreadsheet content-type",
              r.status_code == 200 and "spreadsheetml" in r.headers.get("content-type", ""),
              f"got {r.status_code} / {r.headers.get('content-type')}")
        r = await ac.get("/reports/nope", params={"format": "xlsx"}, headers=H(admin_t))
        check("unknown report → 404", r.status_code == 404, f"got {r.status_code}")
        r = await ac.get("/reports/stock", params={"format": "docx"}, headers=H(admin_t))
        check("bad report format → 400", r.status_code == 400, f"got {r.status_code}")

        # Registration + access requests (non-persisting: only failing paths + reads).
        r = await ac.post("/auth/register", json={"username": "svc_admin_wannabe",
                          "password": "secret123", "role": "admin"})
        check("register requesting admin role → 422 (no self-elevation)",
              r.status_code == 422, f"got {r.status_code}")
        r = await ac.post("/auth/register", json={"username": "admin",
                          "password": "secret123", "role": "store_keeper"})
        check("register an existing username → 409", r.status_code == 409, f"got {r.status_code}")
        r = await ac.post("/auth/register", json={"username": "svc_x", "password": "no",
                          "role": "store_keeper"})
        check("register short password → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.get("/admin/pending-users", headers=H(worker_t))
        check("worker → 403 on /admin/pending-users", r.status_code == 403, f"got {r.status_code}")
        r = await ac.get("/admin/pending-users", headers=H(admin_t))
        check("admin → 200 on /admin/pending-users", r.status_code == 200, f"got {r.status_code}")
        r = await ac.post("/admin/pending-users/999999/approve", headers=H(admin_t), json={})
        check("approve a non-existent request → 404", r.status_code == 404, f"got {r.status_code}")

        # Rate limiting on public auth (isolated by a unique X-Real-IP so it does
        # not affect the other logins in this suite; login cap is 10/min).
        rl = {"X-Real-IP": "203.0.113.7"}
        codes = [(await ac.post("/auth/login", json={"username": "nobody", "password": "x"}, headers=rl)).status_code
                 for _ in range(12)]
        check("public /auth/login is rate-limited (429 past the cap)", 429 in codes, f"codes={codes}")
        check("attempts under the cap are 401, not 429", codes[0] == 401, f"first={codes[0]}")


async def test_token_refresh():
    """Access/refresh split: cookie issuance, rotation, reuse detection
    (family revocation), and logout revocation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://svc") as ac:
        r = await ac.post("/auth/login", json={"username": "worker", "password": "floor2026"})
        old_refresh = r.cookies.get("gi_refresh")
        check("login sets the httpOnly refresh cookie", bool(old_refresh),
              f"status={r.status_code}")

        r2 = await ac.post("/auth/refresh")
        check("refresh → 200 + a new access token",
              r2.status_code == 200 and bool(r2.json().get("access_token")),
              f"got {r2.status_code}")
        new_refresh = r2.cookies.get("gi_refresh")
        check("refresh rotates the cookie", bool(new_refresh) and new_refresh != old_refresh)

        # NB: manual cookie sets use a different jar key (domain) than
        # response-set cookies — clear the jar first or requests carry BOTH
        # gi_refresh cookies and the server reads the stale one.
        def use_cookie(tok):
            ac.cookies.clear()
            ac.cookies.set("gi_refresh", tok, domain="svc")

        # Replaying the OLD (rotated) token must trip reuse detection…
        use_cookie(old_refresh)
        r3 = await ac.post("/auth/refresh")
        check("replaying a rotated token → 401 (reuse detection)",
              r3.status_code == 401, f"got {r3.status_code}")
        # …which revokes the whole family, including the successor.
        use_cookie(new_refresh)
        r4 = await ac.post("/auth/refresh")
        check("reuse detection also revoked the successor token",
              r4.status_code == 401, f"got {r4.status_code}")

        # Fresh session → logout revokes it server-side.
        ac.cookies.clear()
        r5 = await ac.post("/auth/login", json={"username": "worker", "password": "floor2026"})
        tok5 = r5.cookies.get("gi_refresh")
        r6 = await ac.post("/auth/logout")
        check("logout → 200", r6.status_code == 200, f"got {r6.status_code}")
        use_cookie(tok5)
        r7 = await ac.post("/auth/refresh")
        check("refresh after logout → 401 (session revoked)",
              r7.status_code == 401, f"got {r7.status_code}")


async def test_site_scoping():
    """Multi-site isolation: below logistics (level 3), every read is pinned to
    the caller's own Site_ID; admin/logistics stay global."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://svc") as ac:
        async def token(u, p):
            r = await ac.post("/auth/login", json={"username": u, "password": p})
            return r.json().get("access_token")

        def H(t):
            return {"Authorization": f"Bearer {t}"}

        worker_t = await token("worker", "floor2026")   # store_keeper @ CNCEC
        hod_t = await token("hod", "hod2026")           # hod @ CNCEC (level 2 → scoped)
        admin_t = await token("admin", "admin2026")     # level 4 → global

        r = await ac.get("/receipts", params={"limit": 500}, headers=H(worker_t))
        items = r.json().get("items", [])
        check("scoped list returns only own-site rows",
              r.status_code == 200 and len(items) > 0
              and all((i.get("Site_ID") or "").strip() == "CNCEC" for i in items),
              f"status={r.status_code} n={len(items)}")

        r = await ac.get("/receipts", params={"site_id": "HQ"}, headers=H(worker_t))
        check("scoped user asking for another site → 403", r.status_code == 403,
              f"got {r.status_code}")

        ra = (await ac.get("/receipts", params={"limit": 1}, headers=H(admin_t))).json()
        rw = (await ac.get("/receipts", params={"limit": 1}, headers=H(worker_t))).json()
        check("admin sees at least as many rows as a scoped user",
              ra.get("total", 0) >= rw.get("total", 0), f"{ra.get('total')} vs {rw.get('total')}")

        # Cross-site get-one must 404 (not leak existence). Only checkable when a
        # foreign-site row exists in the data.
        foreign = (await ac.get("/receipts", params={"limit": 1, "site_id": "HQ"},
                                headers=H(admin_t))).json().get("items", [])
        if foreign:
            r = await ac.get(f"/receipts/{foreign[0]['id']}", headers=H(worker_t))
            check("scoped get-one of another site's row → 404", r.status_code == 404,
                  f"got {r.status_code}")
        else:
            check("scoped get-one of another site's row → 404 (skipped: no HQ receipts)", True)

        check("scoped user → 403 on /stock/live (cross-site aggregate)",
              (await ac.get("/stock/live", headers=H(worker_t))).status_code == 403)
        check("admin → 200 on /stock/live",
              (await ac.get("/stock/live", headers=H(admin_t))).status_code == 200)

        r = await ac.get("/stock/by-site", params={"limit": 500}, headers=H(worker_t))
        rows = r.json().get("items", [])
        check("stock/by-site forced to the user's own site",
              r.status_code == 200 and all(i.get("Site_ID") == "CNCEC" for i in rows),
              f"status={r.status_code}")

        r = await ac.get("/meta/sites", headers=H(worker_t))
        check("meta/sites returns only the user's site", r.json().get("sites") == ["CNCEC"],
              str(r.json()))
        r = await ac.get("/meta/sites", headers=H(admin_t))
        check("meta/sites unrestricted for admin", len(r.json().get("sites", [])) >= 1)

        r = await ac.get("/meta/inventory-summary", headers=H(worker_t))
        bs = r.json().get("by_site", [])
        check("inventory-summary by_site scoped to one site",
              len(bs) <= 1 and all(x.get("Site_ID") == "CNCEC" for x in bs), str(bs))

        r = await ac.get("/hod/pending", params={"site_id": "HQ"}, headers=H(hod_t))
        check("scoped hod asking for a foreign approvals queue → 403",
              r.status_code == 403, f"got {r.status_code}")
        r = await ac.get("/hod/pending", headers=H(hod_t))
        check("scoped hod pending counts → 200 (own site)", r.status_code == 200)

        r = await ac.get("/reports/stock", params={"format": "csv"}, headers=H(hod_t))
        check("scoped hod report → 200 (forced to own site)", r.status_code == 200,
              f"got {r.status_code}")
        r = await ac.get("/reports/stock", params={"format": "csv", "site_id": "HQ"},
                         headers=H(hod_t))
        check("scoped hod report for a foreign site → 403", r.status_code == 403,
              f"got {r.status_code}")

        # Work-queue badge counts are role- and site-aware.
        j = (await ac.get("/meta/work-queues", headers=H(worker_t))).json()
        check("work-queues: store keeper gets site queues but no approvals",
              "approvals" not in j and "incoming_dns" in j and "sk_requests" in j, str(j))
        j = (await ac.get("/meta/work-queues", headers=H(hod_t))).json()
        check("work-queues: hod gets the approvals count",
              isinstance(j.get("approvals"), int), str(j))
        j = (await ac.get("/meta/work-queues", headers=H(admin_t))).json()
        check("work-queues: admin gets the warehouse workload too",
              isinstance(j.get("warehouse"), int), str(j))

        # R2 lock: entry staging is exact-locked to store_keeper (+admin).
        r = await ac.post("/entry/receipts", headers=H(hod_t), json={})
        check("hod → 403 staging an entry (R2 exact lock)",
              r.status_code == 403, f"got {r.status_code}")
        r = await ac.post("/entry/receipts", headers=H(worker_t), json={})
        check("store keeper passes the entry gate (422 on empty body, not 403)",
              r.status_code == 422, f"got {r.status_code}")

        # Warehouse binding: policy unit-checks on the resolver.
        from .auth import resolve_warehouse_param, warehouse_scope
        wu = {"role": "warehouse_user", "warehouse_id": "WH-01"}
        check("warehouse_user pinned to own warehouse",
              resolve_warehouse_param(wu, None) == "WH-01")
        try:
            resolve_warehouse_param(wu, "WH-02")
            blocked = False
        except Exception:  # noqa: BLE001 — HTTPException(403)
            blocked = True
        check("warehouse_user asking for another warehouse → 403", blocked)
        check("logistics passes warehouse params through",
              resolve_warehouse_param({"role": "logistics", "warehouse_id": ""}, "WH-02") == "WH-02")
        check("unbound warehouse_user fails closed (scope='')",
              warehouse_scope({"role": "warehouse_user", "warehouse_id": ""}) == "")

        # HOD operations pack (non-persisting guard checks; commit machinery is
        # covered by the rolled-back suite-A service tests).
        r = await ac.patch("/hod/pending/returns/999999", headers=H(hod_t),
                           json={"fields": {"Quantity": 5}})
        check("edit of a non-existent staged row → 404", r.status_code == 404,
              f"got {r.status_code}")
        r = await ac.patch("/hod/pending/returns/1", headers=H(hod_t),
                           json={"fields": {"status": "approved"}})
        check("editing a non-whitelisted field → 422", r.status_code == 422,
              f"got {r.status_code}")
        r = await ac.get("/hod/preflight", headers=H(hod_t))
        check("negative-stock pre-flight → 200 + items list",
              r.status_code == 200 and isinstance(r.json().get("items"), list),
              f"got {r.status_code}")
        r = await ac.post("/hod/pending/issues/bulk-approve", headers=H(hod_t),
                          json={"ids": []})
        check("bulk-approve with no ids → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.get("/hod/low-stock", headers=H(hod_t))
        check("low-stock view → 200 for a scoped hod", r.status_code == 200,
              f"got {r.status_code}")
        r = await ac.post("/hod/prs/auto-draft", headers=H(hod_t), json={"site_id": "HQ"})
        check("scoped hod auto-drafting a foreign-site PR → 403", r.status_code == 403,
              f"got {r.status_code}")
        r = await ac.get("/hod/prs/PR-NOPE-0000/pdf", headers=H(hod_t))
        check("PDF of a non-existent PR → 404", r.status_code == 404, f"got {r.status_code}")
        r = await ac.get("/hod/preflight", headers=H(worker_t))
        check("worker (lvl 0) → 403 on the HOD ops pack", r.status_code == 403,
              f"got {r.status_code}")

        # Warehouse completion pack (non-persisting guard checks).
        r = await ac.get("/warehouse/returns", headers=H(admin_t))
        check("returns-from-site queue → 200 for admin",
              r.status_code == 200 and isinstance(r.json().get("items"), list),
              f"got {r.status_code}")
        r = await ac.get("/warehouse/returns", headers=H(worker_t))
        check("worker → 403 on the warehouse returns queue", r.status_code == 403,
              f"got {r.status_code}")
        r = await ac.post("/warehouse/returns", headers=H(admin_t), json={})
        check("recording a return with an empty body → 422", r.status_code == 422,
              f"got {r.status_code}")
        r = await ac.post("/warehouse/returns/999999/disposition", headers=H(admin_t),
                          json={"status": "hold"})
        check("disposition of a non-existent return → 404", r.status_code == 404,
              f"got {r.status_code}")
        r = await ac.post("/warehouse/returns/1/disposition", headers=H(admin_t),
                          json={"status": "yeet"})
        check("invalid disposition value → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.get("/warehouse/history", headers=H(admin_t))
        j = r.json() if r.status_code == 200 else {}
        check("warehouse history → 200 with dns/assignments/throughput",
              r.status_code == 200 and {"dns", "assignments", "throughput"} <= set(j),
              f"got {r.status_code}")

        # Store-keeper toolbox (non-persisting guard checks).
        r = await ac.get("/entry/count-sheet", headers=H(worker_t))
        check("count sheet → 200 for a store keeper (own site)",
              r.status_code == 200 and isinstance(r.json().get("items"), list),
              f"got {r.status_code}")
        r = await ac.post("/entry/count-sheet", headers=H(worker_t),
                          json={"site_id": "CNCEC", "rows": []})
        check("count submit with no rows → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.post("/entry/count-sheet", headers=H(worker_t),
                          json={"site_id": "CNCEC", "reason_code": "yeet",
                                "rows": [{"SAP_Code": "1001", "counted_qty": 1}]})
        check("count submit with a bad reason → 422", r.status_code == 422,
              f"got {r.status_code}")
        r = await ac.get("/entry/bins/1001", headers=H(worker_t))
        check("bin locations → 200 + bins list",
              r.status_code == 200 and isinstance(r.json().get("bins"), list),
              f"got {r.status_code}")
        r = await ac.get("/entry/returnables", headers=H(worker_t))
        check("returnables list → 200 for a store keeper", r.status_code == 200,
              f"got {r.status_code}")
        r = await ac.get("/entry/returnables", headers=H(hod_t))
        check("hod → 403 on returnables (SK exact lock)", r.status_code == 403,
              f"got {r.status_code}")
        r = await ac.post("/entry/returnables", headers=H(worker_t),
                          json={"material_name": "svc wrench", "borrower_name": "svc",
                                "expected_return_time": "not-a-date"})
        check("loan with a bad datetime → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.post("/entry/returnables/999999/return", headers=H(worker_t))
        check("returning a non-existent loan → 404", r.status_code == 404,
              f"got {r.status_code}")
        j = (await ac.get("/meta/work-queues", headers=H(worker_t))).json()
        check("work-queues: store keeper gets the returnables_overdue count",
              isinstance(j.get("returnables_overdue"), int), str(j))

        # Phase-5 reports: every new key renders (csv), scoped gates hold.
        new_keys = ("daily-consumption", "monthly-summary", "wbs", "low-stock",
                    "burn-rate", "valuation", "fefo", "audit",
                    "warehouse-throughput", "force-closures", "intent-vs-actual")
        bad = []
        for k in new_keys:
            rr = await ac.get(f"/reports/{k}", params={"format": "csv"}, headers=H(admin_t))
            if rr.status_code != 200:
                bad.append(f"{k}={rr.status_code}")
        check("all 11 Phase-5 reports render as CSV for admin", not bad, ", ".join(bad))
        r = await ac.get("/reports/audit", params={"format": "csv"}, headers=H(hod_t))
        check("scoped hod → 403 on the global-only audit report",
              r.status_code == 403, f"got {r.status_code}")

        # Archive lifecycle (created → listed → downloaded → deleted).
        r = await ac.post("/reports/archive", headers=H(admin_t),
                          json={"key": "stock", "format": "csv"})
        aid = r.json().get("id") if r.status_code == 201 else None
        check("archive a report → 201 + id", bool(aid), f"got {r.status_code}")
        r = await ac.get("/reports/archive", headers=H(admin_t))
        check("archive list contains the new entry",
              any(x["id"] == aid for x in r.json().get("items", [])))
        r = await ac.get(f"/reports/archive/{aid}/download", headers=H(admin_t))
        check("archived file re-downloads", r.status_code == 200, f"got {r.status_code}")
        r = await ac.request("DELETE", f"/reports/archive/{aid}", headers=H(admin_t))
        check("archive delete → 200 (cleanup)", r.status_code == 200, f"got {r.status_code}")

        # Scheduler: validation + run-now + the daemon's atomic claim.
        r = await ac.post("/reports/schedules", headers=H(admin_t),
                          json={"label": "svc bad", "report_type": "stock",
                                "frequency": "whenever"})
        check("bad schedule frequency → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.post("/reports/schedules", headers=H(admin_t),
                          json={"label": "svc daily", "report_type": "stock",
                                "frequency": "daily 00:00", "format": "csv"})
        sid = r.json().get("id")
        check("create schedule → 201 + id", bool(sid), f"got {r.status_code}")
        r = await ac.post(f"/reports/schedules/{sid}/run", headers=H(admin_t))
        ran_aid = (r.json().get("archive") or {}).get("id")
        check("run-now → archives + returns the archive id",
              r.status_code == 200 and bool(ran_aid), f"got {r.status_code}")

        from .report_center import run_due_schedules
        from .db import SessionLocal as _SL
        from sqlalchemy import update as _upd
        from .services.ledger import _MD as _md
        async with _SL() as s2:
            await s2.execute(_upd(_md.tables["report_schedules"])
                             .where(_md.tables["report_schedules"].c["id"] == sid)
                             .values(last_run=None))
            await s2.commit()
        n1 = await run_due_schedules()
        check("daemon tick runs the due schedule", n1 >= 1, f"ran {n1}")
        n2 = await run_due_schedules()
        check("second tick does NOT rerun (atomic claim holds)", n2 == 0, f"ran {n2}")

        # Cleanup: schedule + every archive row this test created.
        r = await ac.request("DELETE", f"/reports/schedules/{sid}", headers=H(admin_t))
        check("schedule delete → 200 (cleanup)", r.status_code == 200, f"got {r.status_code}")
        arch = (await ac.get("/reports/archive", headers=H(admin_t))).json().get("items", [])
        for x in arch:
            if x["id"] == ran_aid or str(x.get("generated_by", "")).startswith("scheduler:"):
                await ac.request("DELETE", f"/reports/archive/{x['id']}", headers=H(admin_t))

        # Phase-6 documents: label/badge PDFs, reference docs, master exports.
        r = await ac.get("/documents/qr-labels", headers=H(admin_t))
        check("QR bin labels → 200 + PDF",
              r.status_code == 200 and "application/pdf" in r.headers.get("content-type", ""),
              f"got {r.status_code}/{r.headers.get('content-type')}")
        r = await ac.get("/documents/employee-badges", headers=H(admin_t))
        check("employee badges → 200 + PDF",
              r.status_code == 200 and "application/pdf" in r.headers.get("content-type", "")
              and len(r.content) > 800, f"got {r.status_code} len={len(r.content)}")
        r = await ac.get("/documents/qr-labels", headers=H(worker_t))
        check("store keeper (lvl 0) → 403 on QR labels", r.status_code == 403, f"got {r.status_code}")
        r = await ac.get("/documents/reference/manual", headers=H(worker_t))
        check("any authed user can download the manual → 200 PDF",
              r.status_code == 200 and "application/pdf" in r.headers.get("content-type", ""),
              f"got {r.status_code}")
        r = await ac.get("/documents/reference/nope", headers=H(worker_t))
        check("unknown reference doc → 404", r.status_code == 404, f"got {r.status_code}")
        r = await ac.get("/documents/master/vendors", params={"format": "xlsx"}, headers=H(admin_t))
        check("vendor master export → 200 + spreadsheet",
              r.status_code == 200 and "spreadsheet" in r.headers.get("content-type", ""),
              f"got {r.status_code}")
        r = await ac.get("/documents/master/nope", params={"format": "xlsx"}, headers=H(admin_t))
        check("unknown master entity → 404", r.status_code == 404, f"got {r.status_code}")
        r = await ac.get("/documents/master/vendors", params={"format": "docx"}, headers=H(admin_t))
        check("bad export format → 400", r.status_code == 400, f"got {r.status_code}")
        r = await ac.get("/documents/master/employees", params={"format": "csv"}, headers=H(hod_t))
        check("scoped hod employee export → 200 (forced to own site)",
              r.status_code == 200, f"got {r.status_code}")

        # Phase-8 SME read-parity (pure reads; SME Canon — no write endpoints exist).
        r = await ac.get("/sme/equipment-report", headers=H(hod_t))
        check("SME equipment report → 200 + items",
              r.status_code == 200 and isinstance(r.json().get("items"), list),
              f"got {r.status_code}")
        r = await ac.get("/sme/consumption-comparison", headers=H(hod_t))
        check("SME consumption comparison → 200 + items",
              r.status_code == 200 and isinstance(r.json().get("items"), list),
              f"got {r.status_code}")
        r = await ac.get("/sme/demand-matrix", headers=H(hod_t))
        dm = r.json() if r.status_code == 200 else {}
        check("SME demand matrix → 200 with lines + totals",
              r.status_code == 200 and {"lines", "totals"} <= set(dm),
              f"got {r.status_code}")
        check("demand lines hold allocated + shortfall == demand",
              all(abs(l["Allocated_Qty"] + l["Shortfall_Qty"] - l["Demand_Qty"]) < 1e-6
                  for l in dm.get("lines", [])))
        line_sum = sum(l["Demand_Qty"] for l in dm.get("lines", []))
        tot_sum = sum(t["Demand_Qty"] for t in dm.get("totals", []))
        check("demand totals reconcile with the lines",
              abs(line_sum - tot_sum) < 1e-3, f"{line_sum} vs {tot_sum}")
        r = await ac.get("/sme/export/demand-totals", params={"format": "xlsx"}, headers=H(hod_t))
        check("SME export → 200 + spreadsheet",
              r.status_code == 200 and "spreadsheet" in r.headers.get("content-type", ""),
              f"got {r.status_code}")
        r = await ac.get("/sme/export/nope", headers=H(hod_t))
        check("unknown SME export → 404", r.status_code == 404, f"got {r.status_code}")
        r = await ac.get("/sme/demand-matrix", headers=H(worker_t))
        check("worker (lvl 0) → 403 on SME views", r.status_code == 403, f"got {r.status_code}")

        # ---- Phase-9 admin console -------------------------------------------
        import os as _os

        # Global sites CRUD lifecycle.
        r = await ac.post("/admin/sites", headers=H(admin_t), json={"name": "SVC-SITE"})
        new_site = r.json().get("id")
        check("add site → 201", r.status_code == 201 and bool(new_site), f"got {r.status_code}")
        r = await ac.post("/admin/sites", headers=H(admin_t), json={"name": "SVC-SITE"})
        check("duplicate site → 409", r.status_code == 409, f"got {r.status_code}")
        r = await ac.get("/admin/sites", headers=H(admin_t))
        check("sites list contains the new site",
              any(s["name"] == "SVC-SITE" for s in r.json().get("items", [])))
        r = await ac.request("DELETE", f"/admin/sites/{new_site}", headers=H(admin_t))
        check("delete site → 200 (cleanup)", r.status_code == 200, f"got {r.status_code}")
        sites_all = (await ac.get("/admin/sites", headers=H(admin_t))).json()["items"]
        cncec = next((s for s in sites_all if s["name"] == "CNCEC"), None)
        if cncec:
            r = await ac.request("DELETE", f"/admin/sites/{cncec['id']}", headers=H(admin_t))
            check("deleting a site with bound users → 409", r.status_code == 409,
                  f"got {r.status_code}")
        else:
            check("deleting a site with bound users → 409 (skipped: no CNCEC row)", True)

        # Settings + the maintenance-mode login gate (isolated rate-limit bucket;
        # ALWAYS restored in the finally so later suites can log in).
        r = await ac.put("/admin/settings", headers=H(admin_t),
                         json={"key": "nope", "value": "1"})
        check("non-whitelisted setting key → 422", r.status_code == 422, f"got {r.status_code}")
        mip = {"X-Real-IP": "203.0.113.9"}
        r = await ac.put("/admin/settings", headers=H(admin_t),
                         json={"key": "maintenance_mode", "value": "1"})
        check("maintenance mode ON → 200", r.status_code == 200, f"got {r.status_code}")
        try:
            r = await ac.post("/auth/login", headers=mip,
                              json={"username": "supervisor", "password": "super2026"})
            check("non-admin login during maintenance → 503", r.status_code == 503,
                  f"got {r.status_code}")
            r = await ac.post("/auth/login", headers=mip,
                              json={"username": "admin", "password": "admin2026"})
            check("admin login during maintenance → 200", r.status_code == 200,
                  f"got {r.status_code}")
        finally:
            rr = await ac.put("/admin/settings", headers=H(admin_t),
                              json={"key": "maintenance_mode", "value": "0"})
            check("maintenance mode OFF restored", rr.status_code == 200,
                  f"got {rr.status_code}")

        # Manual backup trigger (200 where pg_dump exists, else a clear 501).
        r = await ac.post("/admin/backup", headers=H(admin_t))
        if r.status_code == 200:
            p = r.json().get("file", "")
            ok = _os.path.exists(p) and r.json().get("size_bytes", 0) > 0
            if ok:
                _os.remove(p)
            check("manual backup → dump file written (cleaned up)", ok, p)
        else:
            check("manual backup → 501 when pg_dump unavailable",
                  r.status_code == 501, f"got {r.status_code}")

        # Sessions viewer + admin revocation ends a live session.
        r = await ac.get("/admin/sessions", headers=H(admin_t), params={"username": "worker"})
        check("sessions list → 200, no token material",
              r.status_code == 200 and all("refresh_hash" not in s
                                           for s in r.json().get("items", [])),
              f"got {r.status_code}")
        async with AsyncClient(transport=transport, base_url="http://svc") as ac2:
            lr = await ac2.post("/auth/login", headers=mip,
                                json={"username": "worker", "password": "floor2026"})
            check("victim login for revocation test → 200", lr.status_code == 200)
            r = await ac.post("/admin/sessions/revoke-user/worker", headers=H(admin_t))
            check("revoke-user → 200 + revoked ≥ 1",
                  r.status_code == 200 and r.json().get("revoked", 0) >= 1, str(r.json()))
            r = await ac2.post("/auth/refresh")
            check("revoked session's refresh → 401", r.status_code == 401,
                  f"got {r.status_code}")

        # Oversight KPIs: admin 200 with the expected blocks; hod 403.
        r = await ac.get("/admin/oversight", headers=H(admin_t))
        j = r.json() if r.status_code == 200 else {}
        check("logistics oversight → 200 with KPI blocks",
              r.status_code == 200 and {"prs_by_state", "pos_by_status", "dns_by_status",
                                        "warehouse_load"} <= set(j), f"got {r.status_code}")
        r = await ac.get("/admin/oversight", headers=H(hod_t))
        check("hod (lvl 2) → 403 on oversight", r.status_code == 403, f"got {r.status_code}")

        # Cross-site requests: hod raises → admin decides → cleanup.
        r = await ac.post("/xsite", headers=H(worker_t),
                          json={"target_site": "HQ", "SAP_Code": "1001", "requested_qty": 1})
        check("worker (lvl 0) → 403 raising a cross-site request",
              r.status_code == 403, f"got {r.status_code}")
        r = await ac.post("/xsite", headers=H(hod_t),
                          json={"target_site": "HQ", "SAP_Code": "1001", "requested_qty": 2})
        xid = r.json().get("id")
        check("hod raises a cross-site request → 201 + availability snapshot",
              r.status_code == 201 and "available_at_target" in r.json(),
              f"got {r.status_code}")
        r = await ac.get("/xsite", headers=H(hod_t), params={"mine": "true"})
        check("hod 'my requests' lists it",
              any(x["id"] == xid for x in r.json().get("items", [])))
        r = await ac.post(f"/xsite/{xid}/decide", headers=H(hod_t), json={"action": "approve"})
        check("hod cannot decide (admin only) → 403", r.status_code == 403,
              f"got {r.status_code}")
        r = await ac.post(f"/xsite/{xid}/decide", headers=H(admin_t),
                          json={"action": "approve", "suggested_qty": 1.5})
        check("admin decides → approved", r.status_code == 200
              and r.json().get("status") == "approved", f"got {r.status_code}")
        r = await ac.post(f"/xsite/{xid}/decide", headers=H(admin_t), json={"action": "reject"})
        check("double-decide → 409", r.status_code == 409, f"got {r.status_code}")
        r = await ac.request("DELETE", f"/xsite/{xid}", headers=H(admin_t))
        check("admin deletes the test request (cleanup)", r.status_code == 200,
              f"got {r.status_code}")

        # Feedback: worker submits → admin responds → cleanup.
        r = await ac.post("/feedback", headers=H(worker_t),
                          json={"type": "nope", "description": "x"})
        check("bad feedback type → 422", r.status_code == 422, f"got {r.status_code}")
        r = await ac.post("/feedback", headers=H(worker_t),
                          json={"type": "bug", "description": "svc test report", "page": "/stock"})
        fid = r.json().get("id")
        check("submit feedback → 201", r.status_code == 201 and bool(fid), f"got {r.status_code}")
        r = await ac.get("/feedback/mine", headers=H(worker_t))
        check("'my feedback' lists it", any(x["id"] == fid for x in r.json().get("items", [])))
        r = await ac.patch(f"/admin/feedback/{fid}", headers=H(admin_t),
                           json={"status": "resolved", "admin_response": "done"})
        check("admin resolves feedback → 200", r.status_code == 200, f"got {r.status_code}")
        r = await ac.request("DELETE", f"/admin/feedback/{fid}", headers=H(admin_t))
        check("admin deletes the test report (cleanup)", r.status_code == 200,
              f"got {r.status_code}")


async def test_manhours():
    """Phase-10 Man-Hours portal: exact {hod, admin} lock, roster upserts, the
    ported hour math (8h normal + OT, overnight wrap), SQM distribution,
    estimate-vs-actual variance, attendance-xlsx import, exports. Uses a unique
    future work-date + SVC- codes, and cleans every mh_* row up afterwards."""
    transport = ASGITransport(app=app)
    ip = {"X-Real-IP": "203.0.113.10"}  # own rate-limit bucket
    D = "2031-01-15"                    # far-future date: never collides with real data
    async with AsyncClient(transport=transport, base_url="http://svc") as ac:
        async def token(u, p):
            r = await ac.post("/auth/login", json={"username": u, "password": p}, headers=ip)
            return r.json().get("access_token")

        def H(t):
            return {"Authorization": f"Bearer {t}"}

        admin_t = await token("admin", "admin2026")
        hod_t = await token("hod", "hod2026")        # hod @ CNCEC
        worker_t = await token("worker", "floor2026")

        try:
            # Exact role lock: {hod, admin} only.
            r = await ac.get("/mh/employees", headers=H(worker_t))
            check("worker (lvl 0) → 403 on the MH portal", r.status_code == 403,
                  f"got {r.status_code}")
            r = await ac.get("/mh/meta", headers=H(hod_t))
            check("hod → 200 on /mh/meta with SME dropdowns",
                  r.status_code == 200 and len(r.json().get("equipment_tags", [])) > 0
                  and len(r.json().get("system_codes", [])) > 0, f"got {r.status_code}")

            # Roster: upsert + re-upsert updates in place (no duplicate).
            r = await ac.post("/mh/employees", headers=H(hod_t), json={
                "employee_code": "SVC-EMP-1", "name": "Svc One", "worker_type": "nope"})
            check("bad worker_type → 422", r.status_code == 422, f"got {r.status_code}")
            for code, name in (("SVC-EMP-1", "Svc One"), ("SVC-EMP-2", "Svc Two"),
                               ("SVC-EMP-3", "Svc Three")):
                await ac.post("/mh/employees", headers=H(hod_t), json={
                    "employee_code": code, "name": name, "worker_type": "OWN"})
            r = await ac.post("/mh/employees", headers=H(hod_t), json={
                "employee_code": "SVC-EMP-1", "name": "Svc One Renamed",
                "worker_type": "Supply", "company": "ACME"})
            check("roster upsert → 200", r.status_code == 200, f"got {r.status_code}")
            emps = (await ac.get("/mh/employees", headers=H(hod_t))).json()["items"]
            mine = [e for e in emps if e["Employee_Code"] == "SVC-EMP-1"]
            check("re-upsert updates in place (1 row, new name/type)",
                  len(mine) == 1 and mine[0]["Name"] == "Svc One Renamed"
                  and mine[0]["Worker_Type"] == "Supply", str(mine))
            r = await ac.patch(f"/mh/employees/{mine[0]['id']}/status",
                               headers=H(hod_t), params={"status": "inactive"})
            check("status flip → inactive", r.status_code == 200, f"got {r.status_code}")
            r = await ac.patch(f"/mh/employees/{mine[0]['id']}/status",
                               headers=H(hod_t), params={"status": "active"})
            check("status flip back → active", r.status_code == 200, f"got {r.status_code}")

            # Site scoping: the hod (CNCEC) may not read/write another site.
            r = await ac.get("/mh/employees", headers=H(hod_t), params={"site_id": "HQ"})
            check("hod requesting another site → 403", r.status_code == 403,
                  f"got {r.status_code}")
            r = await ac.post("/mh/employees", headers=H(admin_t), json={
                "employee_code": "SVC-X", "name": "x"})
            check("admin write without site_id → 422", r.status_code == 422,
                  f"got {r.status_code}")

            # Timesheet batch: ported hour math (8h normal + unpaid break + OT,
            # overnight wraps +24h). 07:30–16:30→8.0 · 07:00–18:30→10.5 (2.5 OT)
            # · 22:00–06:00→7.0.
            r = await ac.post("/mh/timesheets", headers=H(hod_t), json={
                "work_date": D, "equipment_tag": "SVC-TAG", "system_code": "99",
                "location": "SVC-LOC", "break_mins": 60, "rows": [
                    {"employee_code": "SVC-EMP-1", "in_time": "07:30", "out_time": "16:30"},
                    {"employee_code": "SVC-EMP-2", "in_time": "07:00", "out_time": "18:30"},
                    {"employee_code": "SVC-EMP-3", "in_time": "22:00", "out_time": "06:00"},
                ]})
            check("timesheet batch → 3 saved", r.status_code == 200
                  and r.json().get("saved") == 3, f"got {r.status_code} {r.text[:120]}")
            ts = (await ac.get("/mh/timesheets", headers=H(hod_t),
                               params={"work_date": D})).json()["items"]
            hours = {t["Employee_Code"]: (t["Total_Hours"], t["Normal_Hours"], t["OT_Hours"])
                     for t in ts}
            check("hour math: 07:30–16:30 − 60min → 8.0 / 8.0 / 0",
                  hours.get("SVC-EMP-1") == (8.0, 8.0, 0.0), str(hours.get("SVC-EMP-1")))
            check("hour math: 07:00–18:30 → 10.5 total with 2.5 OT",
                  hours.get("SVC-EMP-2") == (10.5, 8.0, 2.5), str(hours.get("SVC-EMP-2")))
            check("hour math: overnight 22:00–06:00 wraps → 7.0",
                  hours.get("SVC-EMP-3") == (7.0, 7.0, 0.0), str(hours.get("SVC-EMP-3")))
            # Re-posting the same day/tag/system upserts (no duplicate rows).
            await ac.post("/mh/timesheets", headers=H(hod_t), json={
                "work_date": D, "equipment_tag": "SVC-TAG", "system_code": "99",
                "break_mins": 60, "rows": [
                    {"employee_code": "SVC-EMP-1", "in_time": "07:30", "out_time": "16:30"}]})
            ts2 = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                params={"work_date": D})).json()["items"]
            check("batch re-post upserts in place (still 3 rows)", len(ts2) == 3,
                  f"got {len(ts2)}")

            # Team SQM distribution: even, then pro-rata by hours.
            r = await ac.post("/mh/production", headers=H(hod_t), json={
                "work_date": D, "equipment_tag": "SVC-TAG", "system_code": "99",
                "sqm_done": 30, "distribution_method": "even"})
            check("production even-distribute hits 3 rows", r.status_code == 200
                  and r.json().get("distributed_rows") == 3, r.text[:120])
            ts3 = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                params={"work_date": D})).json()["items"]
            check("even split → 10 SQM each",
                  all(abs(float(t["Allocated_SQM"]) - 10.0) < 1e-6 for t in ts3),
                  str([t["Allocated_SQM"] for t in ts3]))
            await ac.post("/mh/production", headers=H(hod_t), json={
                "work_date": D, "equipment_tag": "SVC-TAG", "system_code": "99",
                "sqm_done": 30, "distribution_method": "by_hours"})
            ts4 = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                params={"work_date": D})).json()["items"]
            sqm_by = {t["Employee_Code"]: float(t["Allocated_SQM"]) for t in ts4}
            # total hours 25.5 → EMP-2's pro-rata share = 30 × 10.5 / 25.5
            check("by-hours split is pro-rata on Total_Hours",
                  abs(sqm_by.get("SVC-EMP-2", 0) - round(30 * 10.5 / 25.5, 3)) < 1e-6,
                  str(sqm_by))

            # Estimator + variance (the inlined v_mh_estimate_vs_actual port):
            # estimate 20 vs actual 25.5 → +5.5 / +27.5%.
            r = await ac.post("/mh/estimates", headers=H(hod_t), json={
                "equipment_tag": "SVC-TAG", "system_code": "99",
                "estimated_manhours": 20, "estimated_sqm": 60, "basis": "svc test"})
            check("estimate upsert → 200", r.status_code == 200, f"got {r.status_code}")
            v = (await ac.get("/mh/variance", headers=H(hod_t))).json()
            row = next((x for x in v["items"] if x["Equipment_Tag"] == "SVC-TAG"), None)
            check("variance row: actual 25.5 vs estimated 20 → +5.5",
                  row is not None and abs(float(row["Actual_Manhours"]) - 25.5) < 1e-6
                  and abs(float(row["Variance_Manhours"]) - 5.5) < 1e-6, str(row))
            check("variance pct 27.5 + SQM rollup 30",
                  row is not None and abs(float(row["Variance_Pct"]) - 27.5) < 1e-6
                  and abs(float(row["SQM_Done"]) - 30.0) < 1e-6, str(row))
            check("variance KPIs count the over-consumer",
                  v["kpis"]["scopes"] >= 1 and v["kpis"]["over_consuming"] >= 1,
                  str(v["kpis"]))
            r = await ac.post("/mh/variance/reason", headers=H(hod_t), json={
                "equipment_tag": "SVC-TAG", "system_code": "99",
                "reason": "svc: rework after hydrotest"})
            check("variance reason saved", r.status_code == 200, f"got {r.status_code}")
            v2 = (await ac.get("/mh/variance", headers=H(hod_t))).json()["items"]
            row2 = next((x for x in v2 if x["Equipment_Tag"] == "SVC-TAG"), None)
            check("reason lands on the variance row",
                  row2 is not None and row2["Variance_Reason"] == "svc: rework after hydrotest",
                  str(row2 and row2["Variance_Reason"]))

            # Employee-wise timeline (roster-name join + windowing).
            tl = (await ac.get("/mh/employee-timeline", headers=H(hod_t), params={
                "employee_code": "SVC-EMP-2", "date_from": D, "date_to": D})).json()
            check("employee timeline: 1 row, joined name, 10.5h total",
                  len(tl["items"]) == 1 and tl["items"][0]["Name"] == "Svc Two"
                  and abs(tl["total_hours"] - 10.5) < 1e-6, str(tl)[:160])

            # Attendance-xlsx import: dry-run preview, replace import, idempotent
            # re-import (replace deletes the file's dates first).
            import io as _io

            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "ADD EMPLOYEE"
            ws.append(["Code", "Name", "Designation", "Type", "Company"])
            ws.append(["SVC-IMP-1", "Svc Import One", "Blaster", "Supply", "ACME"])
            sar = wb.create_sheet("SAR")
            sar.append(["Location", "Equipment Tag #", "Code", "Name", "Work Date",
                        "In Time", "Out Time", "Status", "Remarks"])
            sar.append(["YARD", "SVC-TAG", "SVC-IMP-1", "Svc Import One", "2031-02-01",
                        "07:30", "16:30", "PR", ""])
            sar.append(["YARD", "SVC-TAG", "SVC-IMP-2", "Svc Import Two", "2031-02-01",
                        "07:00", "18:30", "PR", ""])
            buf = _io.BytesIO()
            wb.save(buf)
            xlsx = ("att.xlsx", buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx},
                              params={"dry_run": "true"})
            check("import dry-run parses 2 employees / 2 rows / 1 date",
                  r.status_code == 200 and r.json().get("employees") == 2
                  and r.json().get("timesheets") == 2 and len(r.json().get("dates", [])) == 1,
                  r.text[:160])
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx},
                              params={"replace": "true"})
            check("import (replace) → 2 employees + 2 timesheets", r.status_code == 200
                  and r.json().get("timesheets") == 2, r.text[:160])
            imp = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                params={"work_date": "2031-02-01"})).json()["items"]
            check("imported rows carry recomputed hours (SAR worker merged to roster)",
                  len(imp) == 2 and {float(t["Total_Hours"]) for t in imp} == {8.0, 10.5},
                  str([(t['Employee_Code'], t['Total_Hours']) for t in imp]))
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx},
                              params={"replace": "true"})
            imp2 = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                 params={"work_date": "2031-02-01"})).json()["items"]
            check("replace re-import is idempotent (still 2 rows)", len(imp2) == 2,
                  f"got {len(imp2)}")
            emp_row = next((e for e in (await ac.get("/mh/employees", headers=H(hod_t)))
                            .json()["items"] if e["Employee_Code"] == "SVC-IMP-1"), None)
            check("ADD EMPLOYEE attributes land on the roster",
                  emp_row is not None and emp_row["Worker_Type"] == "Supply"
                  and emp_row["Company"] == "ACME", str(emp_row))
            r = await ac.post("/mh/import", headers=H(hod_t),
                              files={"file": ("junk.xlsx", b"not an xlsx", "application/octet-stream")})
            check("unparseable workbook → 422", r.status_code == 422, f"got {r.status_code}")

            # ---- Phase-11A: import fit + bulk-assign -----------------------
            # Legend defaults: a SAR-only worker gets OWN→GI.
            emp2 = next((e for e in (await ac.get("/mh/employees", headers=H(hod_t)))
                         .json()["items"] if e["Employee_Code"] == "SVC-IMP-2"), None)
            check("legend default: SAR-only worker → OWN/GI",
                  emp2 is not None and emp2["Worker_Type"] == "OWN"
                  and emp2["Company"] == "GI", str(emp2))

            # Workbook 2: literal 'nan' junk cells, a duplicated (code,date)
            # row, and a Supply employee with a blank Company cell.
            wb2 = Workbook()
            ws2 = wb2.active
            ws2.title = "ADD EMPLOYEE"
            ws2.append(["Code", "Name", "Designation", "Type", "Company"])
            ws2.append(["SVC-IMP-3", "Svc Import Three", "nan", "Supply", None])
            sar2 = wb2.create_sheet("SAR")
            sar2.append(["Location", "Equipment Tag #", "Code", "Name", "Work Date",
                         "In Time", "Out Time", "Status", "Remarks"])
            sar2.append(["nan", "nan", "SVC-IMP-1", "Svc Import One", "2031-02-02",
                         "07:30", "16:30", "PR", "nan"])
            sar2.append(["nan", "nan", "SVC-IMP-1", "Svc Import One", "2031-02-02",
                         "08:00", "17:00", "PR", ""])   # dup (code,date,tag) — last wins
            sar2.append([None, None, "SVC-IMP-3", "Svc Import Three", "2031-02-02",
                         "07:30", "16:30", "PR", ""])
            buf2 = _io.BytesIO()
            wb2.save(buf2)
            xlsx2 = ("att2.xlsx", buf2.getvalue(),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx2},
                              params={"replace": "true"})
            check("in-file dedupe: 3 SAR rows → 2 imported (last dup wins)",
                  r.status_code == 200 and r.json().get("timesheets") == 2, r.text[:160])
            d2 = (await ac.get("/mh/timesheets", headers=H(hod_t),
                               params={"work_date": "2031-02-02"})).json()["items"]
            one = next((x for x in d2 if x["Employee_Code"] == "SVC-IMP-1"), None)
            check("'nan' cells → NULL (import guard) + dup row's later times won",
                  one is not None and one["Equipment_Tag"] is None
                  and one["Location"] is None and one["In_Time"] == "08:00",
                  str(one))
            emp3 = next((e for e in (await ac.get("/mh/employees", headers=H(hod_t)))
                         .json()["items"] if e["Employee_Code"] == "SVC-IMP-3"), None)
            check("legend default: Supply + blank company → DMC ('nan' designation cleaned)",
                  emp3 is not None and emp3["Company"] == "DMC"
                  and emp3["Designation"] == "", str(emp3))

            # Unassigned filter: only wb2's NULL-tag rows qualify (the Phase-10
            # workbook filled Equipment Tag # = SVC-TAG, so its rows are assigned).
            r = await ac.get("/mh/timesheets", headers=H(hod_t), params={
                "unassigned": "true", "date_from": "2031-02-01", "date_to": "2031-02-02"})
            un = r.json()
            check("unassigned filter: wb2's 2 NULL-tag rows with a total_hours rollup",
                  len(un["items"]) == 2 and un["total_hours"] == 16.0,
                  f"{len(un.get('items', []))} rows, {un.get('total_hours')}")

            # Bulk-assign: unassigned rows → a real SME scope; Location auto-fills.
            meta_j = (await ac.get("/mh/meta", headers=H(hod_t))).json()
            atag = meta_j["equipment_tags"][0]
            aloc = meta_j["tag_locations"].get(atag)
            ids1 = [x["id"] for x in un["items"]]
            r = await ac.patch("/mh/timesheets/assign", headers=H(hod_t), json={
                "ids": ids1, "equipment_tag": atag, "system_code": "99"})
            check("bulk-assign → all rows assigned, SME location auto-filled",
                  r.status_code == 200 and r.json().get("assigned") == len(ids1)
                  and r.json().get("location") == aloc, r.text[:200])
            left = (await ac.get("/mh/timesheets", headers=H(hod_t), params={
                "unassigned": "true", "work_date": "2031-02-02"})).json()["items"]
            check("assigned rows leave the unassigned queue", len(left) == 0,
                  f"got {len(left)}")

            # Append-overlap warning: dry-run flags dates that already have rows.
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx2},
                              params={"dry_run": "true"})
            check("dry-run reports overlap_dates for append preview",
                  r.json().get("overlap_dates") == ["2031-02-02"], r.text[:160])
            r = await ac.post("/mh/import", headers=H(hod_t), files={"file": xlsx2},
                              params={"replace": "false"})
            check("append into an existing date → overlap_dates in the response",
                  r.status_code == 200 and r.json().get("overlap_dates") == ["2031-02-02"],
                  r.text[:160])
            d2b = (await ac.get("/mh/timesheets", headers=H(hod_t),
                                params={"work_date": "2031-02-02"})).json()["items"]
            check("append duplicates NULL-tag rows (the documented reason for the warning)",
                  len(d2b) == 4, f"got {len(d2b)}")

            # Conflict skip: the appended twins target a scope where each
            # worker/date already has an assigned row — all skipped, none merged.
            ids2 = [x["id"] for x in d2b if x["Equipment_Tag"] is None]
            r = await ac.patch("/mh/timesheets/assign", headers=H(hod_t), json={
                "ids": ids2, "equipment_tag": atag, "system_code": "99"})
            check("assign skips unique-key twins and reports them (0 assigned + 2 conflicts)",
                  r.status_code == 200 and r.json().get("assigned") == 0
                  and len(r.json().get("conflicts", [])) == 2, r.text[:200])
            r = await ac.patch("/mh/timesheets/assign", headers=H(hod_t), json={
                "ids": [], "equipment_tag": atag, "system_code": "99"})
            check("assign with no ids → 422", r.status_code == 422, f"got {r.status_code}")
            r = await ac.patch("/mh/timesheets/assign", headers=H(hod_t), json={
                "ids": ids2, "equipment_tag": "nan", "system_code": "99"})
            check("assign with a blank-ish tag → 422", r.status_code == 422,
                  f"got {r.status_code}")

            # ---- Phase-11B: SME link layer (read-only joins) ----------------
            # Pick a real SME scope with planned SQM and no existing estimate,
            # book SVC labor + production + an estimate on it, and assert every
            # joined column on the scorecard.
            sc0 = (await ac.get("/mh/scorecard", headers=H(hod_t))).json()
            check("scorecard unions SME scopes with MH-only scopes",
                  any(x["In_SME"] for x in sc0["items"])
                  and any(not x["In_SME"] and x["Equipment_Tag"] == "SVC-TAG"
                          for x in sc0["items"]),
                  f"{sc0['kpis']}")
            real = next(x for x in sc0["items"]
                        if x["In_SME"] and (x["Planned_SQM"] or 0) > 0
                        and x["Estimated_Manhours"] is None
                        and x["Actual_Manhours"] == 0)
            rtag, rsys = real["Equipment_Tag"], real["System_Code"]
            await ac.post("/mh/timesheets", headers=H(hod_t), json={
                "work_date": "2031-03-01", "equipment_tag": rtag, "system_code": rsys,
                "break_mins": 60, "rows": [
                    {"employee_code": "SVC-EMP-1", "in_time": "07:30", "out_time": "16:30"}]})
            await ac.post("/mh/production", headers=H(hod_t), json={
                "work_date": "2031-03-01", "equipment_tag": rtag, "system_code": rsys,
                "sqm_done": 40, "distribution_method": "even"})
            await ac.post("/mh/estimates", headers=H(hod_t), json={
                "equipment_tag": rtag, "system_code": rsys,
                "estimated_manhours": 10, "estimated_sqm": 50, "basis": "svc scorecard"})
            sc = (await ac.get("/mh/scorecard", headers=H(hod_t))).json()
            row_sc = next(x for x in sc["items"]
                          if x["Equipment_Tag"] == rtag and x["System_Code"] == rsys)
            check("scorecard row: 8h labor + est 10 → labor variance −20%",
                  row_sc["Actual_Manhours"] == 8.0 and row_sc["Estimated_Manhours"] == 10.0
                  and row_sc["Labor_Variance_Pct"] == -20.0, str(row_sc))
            check("scorecard row: labor-reported 40 SQM → MH/SQM 0.2",
                  row_sc["Done_SQM_Labor"] == 40.0 and row_sc["MH_per_SQM"] == 0.2,
                  str(row_sc))
            check("reconciliation flags drift (labor says 40, SME says 0)",
                  row_sc["Reconciliation"] == "drift" and row_sc["Done_SQM_SME"] == 0.0,
                  str(row_sc))
            check("scorecard KPIs count labor + drift",
                  sc["kpis"]["with_labor"] >= 1 and sc["kpis"]["drift"] >= 1,
                  str(sc["kpis"]))

            prod = (await ac.get("/mh/productivity", headers=H(hod_t))).json()
            row_p = next(x for x in prod["items"]
                         if x["Equipment_Tag"] == rtag and x["System_Code"] == rsys)
            check("productivity norms: 0.2 MH/SQM · 5 SQM/MH · est-norm 0.2",
                  row_p["MH_per_SQM"] == 0.2 and row_p["SQM_per_MH"] == 5.0
                  and row_p["Est_MH_per_SQM"] == 0.2, str(row_p))
            check("site norm aggregates scopes with both hours and SQM",
                  (prod["site_norm"]["mh_per_sqm"] or 0) > 0, str(prod["site_norm"]))
            r = await ac.get("/mh/scorecard", headers=H(worker_t))
            check("worker (lvl 0) → 403 on the scorecard", r.status_code == 403,
                  f"got {r.status_code}")
            r = await ac.get("/mh/export/scorecard", headers=H(hod_t),
                             params={"format": "pdf"})
            check("scorecard export → 200 + pdf",
                  r.status_code == 200 and "pdf" in r.headers.get("content-type", ""),
                  f"got {r.status_code}")
            r = await ac.get("/mh/export/productivity", headers=H(hod_t),
                             params={"format": "xlsx"})
            check("productivity export → 200 + spreadsheet",
                  r.status_code == 200 and "spreadsheet" in r.headers.get("content-type", ""),
                  f"got {r.status_code}")

            # ---- Phase-11C: planning automation ------------------------------
            # Auto-draft preview: only unestimated SME scopes with remaining SQM;
            # the seeded history (33.5 h over 70 SQM) yields a real site norm.
            ad = (await ac.get("/mh/estimates/auto-draft", headers=H(hod_t))).json()
            check("auto-draft preview: unestimated scopes only, site norm learned",
                  len(ad["items"]) > 0 and ad["site_norm"] is not None
                  and not any(x["Equipment_Tag"] == rtag and x["System_Code"] == rsys
                              for x in ad["items"]), f"{len(ad['items'])} rows, norm={ad['site_norm']}")
            check("draft math: Draft_MH == Remaining_SQM × Norm_Used on every row",
                  all(x["Draft_Manhours"] is not None
                      and abs(x["Draft_Manhours"] - round(x["Remaining_SQM"] * x["Norm_Used"], 1)) < 0.11
                      for x in ad["items"]), str(ad["items"][:2]))
            ad5 = (await ac.get("/mh/estimates/auto-draft", headers=H(hod_t),
                                params={"norm": 0.5})).json()
            check("norm override: every draft = remaining × 0.5, source 'override'",
                  all(x["Norm_Source"] == "override"
                      and abs(x["Draft_Manhours"] - round(x["Remaining_SQM"] * 0.5, 1)) < 0.11
                      for x in ad5["items"]), str(ad5["items"][:1]))

            # Save two reviewed drafts (edited MH) → they appear as estimates and
            # leave the draftable pool.
            pick = ad["items"][:2]
            r = await ac.post("/mh/estimates/auto-draft", headers=H(hod_t), json={
                "rows": [{"equipment_tag": x["Equipment_Tag"],
                          "system_code": x["System_Code"],
                          "estimated_manhours": 123.0,
                          "estimated_sqm": x["Remaining_SQM"],
                          "location": x["Location"], "basis": "svc auto-draft"}
                         for x in pick]})
            check("auto-draft save → 2 estimates", r.status_code == 200
                  and r.json().get("saved") == 2, r.text[:120])
            ests = (await ac.get("/mh/estimates", headers=H(hod_t))).json()["items"]
            check("saved drafts land in mh_manhour_estimates with the reviewed MH",
                  sum(1 for e in ests if e["Basis"] == "svc auto-draft"
                      and float(e["Estimated_Manhours"]) == 123.0) == 2, "")
            ad2 = (await ac.get("/mh/estimates/auto-draft", headers=H(hod_t))).json()
            check("saved scopes leave the draftable pool",
                  len(ad2["items"]) == len(ad["items"]) - 2,
                  f"{len(ad['items'])} → {len(ad2['items'])}")
            r = await ac.post("/mh/estimates/auto-draft", headers=H(hod_t), json={"rows": []})
            check("auto-draft save with no rows → 422", r.status_code == 422,
                  f"got {r.status_code}")

            # Manpower forecast: estimate-based remaining (10 est − 8 actual = 2)
            # + norm-based scopes; fully-consumed SVC-TAG/99 drops out.
            fc = (await ac.get("/mh/forecast", headers=H(hod_t),
                               params={"crew_size": 10, "hours_per_day": 8})).json()
            fr = next((x for x in fc["items"] if x["Equipment_Tag"] == rtag
                       and x["System_Code"] == rsys), None)
            check("forecast: estimate-based scope has 2 MH remaining",
                  fr is not None and fr["Basis"] == "estimate"
                  and fr["Remaining_Manhours"] == 2.0 and fr["Days_To_Complete"] > 0,
                  str(fr))
            check("forecast: fully-consumed scope drops out (SVC-TAG 25.5h > 20 est)",
                  not any(x["Equipment_Tag"] == "SVC-TAG" for x in fc["items"]), "")
            check("forecast: norm-based scopes included + rollup sums",
                  any(x["Basis"] == "norm" for x in fc["items"])
                  and fc["rollup"]["total_remaining_manhours"] > 0
                  and fc["rollup"]["days_to_complete"] > 0, str(fc["rollup"]))
            r = await ac.get("/mh/forecast", headers=H(hod_t), params={"crew_size": 0})
            check("forecast crew_size=0 → 422", r.status_code == 422, f"got {r.status_code}")
            r = await ac.get("/mh/forecast", headers=H(worker_t))
            check("worker (lvl 0) → 403 on the forecast", r.status_code == 403,
                  f"got {r.status_code}")

            # Exports reuse the shared report renderers.
            r = await ac.get("/mh/export/variance", headers=H(hod_t),
                             params={"format": "xlsx"})
            check("MH export → 200 + spreadsheet", r.status_code == 200
                  and "spreadsheet" in r.headers.get("content-type", ""),
                  f"got {r.status_code}")
            r = await ac.get("/mh/export/nope", headers=H(hod_t))
            check("unknown MH export → 404", r.status_code == 404, f"got {r.status_code}")
        finally:
            # Cleanup: remove every SVC- artifact this suite created.
            from sqlalchemy import text as _text
            async with SessionLocal() as s:
                await s.execute(_text(
                    "DELETE FROM mh_timesheets WHERE \"Employee_Code\" LIKE 'SVC-%'"))
                await s.execute(_text(
                    "DELETE FROM mh_employees WHERE \"Employee_Code\" LIKE 'SVC-%'"))
                await s.execute(_text(
                    "DELETE FROM mh_production WHERE \"Equipment_Tag\" = 'SVC-TAG' "
                    "OR \"Work_Date\" LIKE '2031-%'"))
                await s.execute(_text(
                    "DELETE FROM mh_manhour_estimates WHERE \"Equipment_Tag\" = 'SVC-TAG' "
                    "OR \"Basis\" LIKE 'svc%'"))
                await s.execute(_text(
                    "DELETE FROM mh_variance_notes WHERE \"Equipment_Tag\" = 'SVC-TAG'"))
                await s.commit()


async def test_ai_layer():
    """Phase AI-0/AI-1: safety-gate + fuzzy ports (pure functions), role-gated
    manual retrieval, and the SSE assistant endpoint with a MOCKED Ollama
    client (tests never require a live model server)."""
    import backend.api.ai.client as aic
    from backend.api.ai import fuzzy, manual_qa
    from backend.api.ai.safety import is_safe_select, scrub_sql

    # --- safety gate (PG-hardened port) ----------------------------------
    ok, _ = is_safe_select("SELECT * FROM receipts -- Replace with real Site_ID")
    check("safety: forbidden keyword in a comment does NOT trip", ok)
    ok, _ = is_safe_select("SELECT * FROM receipts WHERE note = 'please DELETE later'")
    check("safety: forbidden keyword in a string literal does NOT trip", ok)
    check("safety: multi-statement blocked",
          not is_safe_select("SELECT 1; DROP TABLE receipts")[0])
    check("safety: UPDATE blocked", not is_safe_select("UPDATE receipts SET x=1")[0])
    check("safety: users table blocked",
          not is_safe_select("SELECT * FROM users")[0])
    check("safety: auth_sessions blocked (new-stack addition)",
          not is_safe_select("SELECT * FROM auth_sessions")[0])
    check("safety: pg_catalog blocked (PG addition)",
          not is_safe_select("SELECT * FROM pg_catalog.pg_tables")[0])
    check("safety: COPY blocked (PG addition)",
          not is_safe_select("SELECT 1 UNION COPY x TO '/tmp/f'")[0])
    check("safety: WITH...SELECT CTE allowed",
          is_safe_select("WITH t AS (SELECT 1 AS n) SELECT n FROM t")[0])
    check("safety: LIMIT injected on a new line",
          scrub_sql("SELECT * FROM receipts").endswith("\nLIMIT 500"))
    check("safety: existing LIMIT kept",
          scrub_sql("SELECT 1 LIMIT 7") == "SELECT 1 LIMIT 7")
    check("safety: 'limit' inside a comment still gets a real LIMIT",
          "LIMIT 500" in scrub_sql("SELECT 1 -- no limit here"))

    # --- fuzzy matcher (pandas-free port) ---------------------------------
    inv = [{"SAP_Code": "1001", "Equipment_Description": "Pipe 6m DN50", "UOM": "PCS"},
           {"SAP_Code": "1002", "Equipment_Description": "Double Clamp 2in", "UOM": "PCS"},
           {"SAP_Code": "1003", "Equipment_Description": "Axial Fan 500mm", "UOM": "EA"}]
    check("fuzzy: normalise drops punctuation + UOM noise",
          fuzzy.normalise("Pipe, 6m (PCS)") == "pipe 6m")
    bm = fuzzy.best_match("pipe 6m dn50", inv)
    check("fuzzy: exact-ish query auto-fills", bm is not None and bm["SAP_Code"] == "1001",
          str(bm))
    rows = fuzzy.resolve_rows([{"material_text": "6m pipe"},
                               {"material_text": "totally unknown thing"}], inv)
    check("fuzzy: reordered tokens → auto/pick (never unknown), junk → unknown",
          rows[0]["match_state"] in ("auto", "pick") and rows[1]["match_state"] == "unknown",
          str([(r['match_state'], r['score']) for r in rows]))

    # --- role-gated manual retrieval --------------------------------------
    sections = manual_qa._load_sections()
    check("manual: sections parsed (v3.0 has 19)", len(sections) >= 17, f"got {len(sections)}")
    sk_ctx = manual_qa._context_for_role("store_keeper")
    adm_ctx = manual_qa._context_for_role("admin")
    hod_ctx = manual_qa._context_for_role("hod")
    check("manual: store keeper sees §4, physically NOT §7 (admin chapter)",
          "=== Section 4:" in sk_ctx and "=== Section 7:" not in sk_ctx)
    check("manual: admin sees §7 untruncated (longer context than SK)",
          "=== Section 7:" in adm_ctx and len(adm_ctx) > len(sk_ctx))
    check("manual: hod allowlist grew §18 SME + §19 Man-Hours",
          "=== Section 18:" in hod_ctx and "=== Section 19:" in hod_ctx)
    check("manual: greeting fast-path (no LLM)",
          manual_qa.greeting_reply("hi") is not None
          and manual_qa.greeting_reply("how do I stage a return?") is None)

    # --- /ai endpoints over the live ASGI app (mocked Ollama) --------------
    transport = ASGITransport(app=app)
    ip = {"X-Real-IP": "203.0.113.15"}
    async with AsyncClient(transport=transport, base_url="http://svc") as ac:
        async def token(u, p):
            r = await ac.post("/auth/login", json={"username": u, "password": p}, headers=ip)
            return r.json().get("access_token")

        def H(t):
            return {"Authorization": f"Bearer {t}"}

        admin_t = await token("admin", "admin2026")
        worker_t = await token("worker", "floor2026")

        r = await ac.get("/ai/health")
        check("ai health without a token → 401", r.status_code == 401, f"got {r.status_code}")
        r = await ac.get("/ai/health", headers=H(worker_t))
        check("ai health → 200 with ok/enabled/message",
              r.status_code == 200 and {"ok", "enabled", "message"} <= set(r.json()),
              r.text[:120])

        # Greeting streams without any model (works even with Ollama down).
        r = await ac.post("/ai/assistant", headers=H(worker_t), json={"question": "hi"})
        check("assistant greeting → SSE tokens + done (no LLM involved)",
              r.status_code == 200 and '"token"' in r.text and '"done": true' in r.text,
              r.text[:160])

        # Mock the Ollama client and prove the stream + the role gate.
        saved = (aic.health, aic.list_models, aic.stream)
        captured: dict = {}

        async def fake_health():
            return True

        async def fake_models():
            return [aic.MODEL_CHAT]

        async def fake_stream(model, prompt, *, system=None, **kw):
            captured["system"] = system or ""
            for t in ("Go to ", "Entry Log."):
                yield t
        try:
            aic.health, aic.list_models, aic.stream = fake_health, fake_models, fake_stream
            r = await ac.post("/ai/assistant", headers=H(worker_t),
                              json={"question": "how do I stage a return?"})
            check("assistant streams model chunks as SSE events in order",
                  r.text.index('"Go to "') < r.text.index('"Entry Log."')
                  and '"done": true' in r.text, r.text[:200])
            check("role gate: the store keeper's PROMPT physically lacks the admin chapter",
                  "=== Section 4:" in captured["system"]
                  and "=== Section 7:" not in captured["system"], "")

            # Feature flag: switch the assistant off → error event; restore.
            r = await ac.put("/admin/settings", headers=H(admin_t),
                             json={"key": "ai_assistant_enabled", "value": "0"})
            check("ai flag accepted by the settings whitelist", r.status_code == 200,
                  f"got {r.status_code}")
            try:
                r = await ac.post("/ai/assistant", headers=H(worker_t),
                                  json={"question": "how do I stage a return?"})
                check("assistant while flagged off → SSE error event",
                      '"error"' in r.text and '"done": true' in r.text, r.text[:160])
                r = await ac.get("/ai/health", headers=H(worker_t))
                check("health reports enabled:false while flagged off",
                      r.json().get("enabled") is False, r.text[:120])
            finally:
                rr = await ac.put("/admin/settings", headers=H(admin_t),
                                  json={"key": "ai_assistant_enabled", "value": "1"})
                check("ai flag restored", rr.status_code == 200, f"got {rr.status_code}")
        finally:
            aic.health, aic.list_models, aic.stream = saved


def test_config_jwt():
    """JWT_SECRET hardening: dev is lenient, production fails fast on a weak key."""
    import os
    from .config import _DEV_JWT_SECRET, jwt_secret
    saved = {k: os.environ.get(k) for k in ("GI_ENV", "JWT_SECRET")}
    try:
        os.environ.pop("GI_ENV", None)
        os.environ.pop("JWT_SECRET", None)
        check("dev jwt_secret ≥ 32 chars (no HMAC warning)", len(jwt_secret()) >= 32)
        os.environ["GI_ENV"] = "production"
        try:
            jwt_secret()
            raised = False
        except RuntimeError:
            raised = True
        check("production without JWT_SECRET fails fast", raised)
        os.environ["JWT_SECRET"] = "x" * 40
        check("production accepts a strong secret", jwt_secret() == "x" * 40)
        os.environ["JWT_SECRET"] = _DEV_JWT_SECRET
        try:
            jwt_secret()
            rejected = False
        except RuntimeError:
            rejected = True
        check("production rejects the dev-default secret", rejected)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def main() -> int:
    print("Service-level invariants (rolled back) + auth/role guards:\n")
    print(" A. service invariants")
    await test_create_and_submit_pr()
    await test_smr_create_and_approve()
    await test_receipt_ledger()
    await test_submitter_resolution()
    await test_notification_visibility()
    test_config_jwt()
    print("\n B. auth/role guards")
    await test_auth_guards()
    print("\n C. site scoping (multi-site isolation)")
    await test_site_scoping()
    print("\n D. token refresh (rotation + revocation)")
    await test_token_refresh()
    print("\n E. man-hours portal (Phase 10)")
    await test_manhours()
    print("\n F. intelligence layer (AI-0/AI-1, Ollama mocked)")
    await test_ai_layer()
    await engine.dispose()

    print(f"\n== SERVICE TESTS: {'✅ PASS' if not FAILED else '❌ FAIL'} "
          f"({len(PASSED)} passed, {len(FAILED)} failed) ==")
    if FAILED:
        print("   failed:", ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
