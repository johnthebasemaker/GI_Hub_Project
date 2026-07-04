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


async def main() -> int:
    print("Service-level invariants (rolled back) + auth/role guards:\n")
    print(" A. service invariants")
    await test_create_and_submit_pr()
    await test_smr_create_and_approve()
    await test_receipt_ledger()
    await test_notification_visibility()
    print("\n B. auth/role guards")
    await test_auth_guards()
    await engine.dispose()

    print(f"\n== SERVICE TESTS: {'✅ PASS' if not FAILED else '❌ FAIL'} "
          f"({len(PASSED)} passed, {len(FAILED)} failed) ==")
    if FAILED:
        print("   failed:", ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
