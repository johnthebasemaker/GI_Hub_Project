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

        appr = await supervisor.approve_smr(s, sk_username="svc_sk", request_id=rid)
        check("approve_smr succeeds", appr.get("approved") is True, str(appr))
        n_pending = await _count(
            s, pending_issues_t, pending_issues_t.c["Source_Ref"].like(f"SMR:{no}:%"),
            pending_issues_t.c["status"] == "pending_hod")
        check("approve_smr stages pending_issues", n_pending == 1, f"got {n_pending}")
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
    await engine.dispose()

    print(f"\n== SERVICE TESTS: {'✅ PASS' if not FAILED else '❌ FAIL'} "
          f"({len(PASSED)} passed, {len(FAILED)} failed) ==")
    if FAILED:
        print("   failed:", ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
