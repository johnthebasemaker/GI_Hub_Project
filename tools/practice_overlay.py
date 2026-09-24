#!/usr/bin/env python3
"""
tools/practice_overlay.py — what a Practice SANDBOX needs that a tutorial VIDEO
does not, written on top of the Phase 12 synthetic dataset (rule 17, slice S2).

    GI_INSTANCE=training GI_DOTENV=0 \\
    DATABASE_URL=postgresql+asyncpg://gi_training@127.0.0.1:5433/gihub_seed_training \\
    .venv/bin/python tools/practice_overlay.py

Normally run by `tools/practice_db.py build`, never by hand.

⚠️ WHY THIS IS NOT AN EDIT TO `make_tutorial_db.py`. That file's output is
pinned by ruling P12-5: its `DATASET_VERSION` is written into every rendered
tutorial's manifest, so adding trainee accounts or seeded queues THERE would
change the dataset sixty videos were recorded against. This overlay runs after
`cutover_migrate` has loaded the fixture, touches only the Postgres copy, and
leaves the tutorial dataset byte-identical. The side benefit is deliberate: a
trainee who watches "Return Stock" and then opens Practice finds *Tutorial Demo
Gasket Set / 899001* exactly where the video showed it.

⚠️ IT RUNS AS A PRACTICE PROCESS. `GI_INSTANCE=training` against the SEED
database (`gihub_seed_training`, which ends `_training` for exactly this
reason), so rule 17's boot check applies to it like any other Practice process:
pointed at Live, it refuses before it has an engine.

WHAT IT ADDS
  · one SHARED account per role in `auth.ROLE_META` (`practice.<role>`), with
    the scope each role's registration rule demands — ruling Q4. A role added to
    ROLE_META without an account here fails suite TR-10 (rule 13's pattern).
  · the five tutorial harness logins are REMOVED: `admin / admin2026` is a
    password published in the repo, and in a sandbox anybody can reach it would
    be an admin account nobody chose to hand out.
  · work waiting in every approval queue, staged through the REAL services, so
    an HOD trainee opens Approvals and finds something to approve.
  · 2FA mandate off (Q4), phones replaced by a non-routable number (V7's second
    wall), and the dataset stamps `GET /instance` reports.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import pathlib
import secrets
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("GI_DOTENV", "0")

# Bumped when the OVERLAY's content changes. Reported with the tutorial
# fixture's own DATASET_VERSION as "<fixture>.<overlay>".
OVERLAY_VERSION = 1

SITE = "CNCEC"
WAREHOUSE = "WH-01"
WBS = "WBS-9001"
# The tutorial harness accounts (make_tutorial_db.HARNESS_USERS). Removed here.
HARNESS_USERNAMES = ("admin", "hod", "supervisor", "worker", "Logistics")
# A number no carrier routes. Outbound is already impossible in Practice; this
# is so a copied-out row can never become a real text to a real person.
NON_ROUTABLE_PHONE = "+000000000000"
DEFAULT_PASSWORD = "Practice@2026"

# (username, role, Site_ID, Warehouse_ID). Scopes follow auth.py's
# _SCOPED_ / _UNSCOPED_ / _DUAL_SCOPE_REG_ROLES exactly — TR-10 asserts it.
ACCOUNTS: tuple[tuple[str, str, str, str | None], ...] = (
    ("practice.admin",       "admin",          "",   None),
    ("practice.logistics",   "logistics",      "",   None),
    ("practice.auditor",     "auditor",        "",   None),
    ("practice.hod",         "hod",            SITE, None),
    ("practice.qchod",       "qc_hod",         "",   None),
    ("practice.warehouse",   "warehouse_user", "",   WAREHOUSE),
    ("practice.supervisor",  "supervisor",     SITE, None),
    ("practice.qc",          "qc",             SITE, None),
    ("practice.storekeeper", "store_keeper",   SITE, None),
)


def practice_password() -> str:
    return os.environ.get("PRACTICE_PASSWORD", "").strip() or DEFAULT_PASSWORD


def admin_password() -> tuple[str, bool]:
    """The Practice ADMIN is the one account that can wipe everybody's work,
    so it does not get the published password unless the operator says so.
    Returns (password, generated?)."""
    v = os.environ.get("PRACTICE_ADMIN_PASSWORD", "").strip()
    if v:
        return v, False
    return "Pa-" + secrets.token_urlsafe(12), True


async def seed_accounts(session) -> dict:
    """Idempotent: re-running converges on the same nine rows. Does NOT commit."""
    import bcrypt
    from sqlalchemy import delete, select, update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend import models
    users = models.Base.metadata.tables["users"]
    await session.execute(delete(users).where(users.c["username"].in_(HARNESS_USERNAMES)))
    pw = practice_password()
    admin_pw, generated = admin_password()
    for uname, role, site, wh in ACCOUNTS:
        raw = admin_pw if role == "admin" else pw
        h = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=10)).decode()
        vals = {"password_hash": h, "role": role, "Site_ID": site,
                "Warehouse_ID": wh, "Phone_Number": None, "email": None,
                "totp_secret": None, "totp_enabled": 0}
        await session.execute(pg_insert(users).values(username=uname, **vals)
                              .on_conflict_do_update(index_elements=["username"],
                                                     set_=vals))
    n = (await session.execute(select(users.c["username"]).where(
        users.c["username"].like("practice.%")))).all()
    return {"accounts": len(n), "admin_password": admin_pw if generated else None}


async def seed_settings(session, *, fixture_version: int) -> None:
    """Idempotent. Does NOT commit."""
    from sqlalchemy import update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend import models
    t = models.Base.metadata.tables["app_settings"]
    emp = models.Base.metadata.tables["employees"]
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for k, v in (("mfa_required_roles", ""),              # ruling Q4
                 ("maintenance_mode", "0"),
                 ("practice_dataset_version", f"{fixture_version}.{OVERLAY_VERSION}"),
                 ("practice_seeded_at", now)):
        await session.execute(pg_insert(t).values(key=k, value=v)
                              .on_conflict_do_update(index_elements=["key"],
                                                     set_={"value": v}))
    await session.execute(update(emp).values(Phone_Number=NON_ROUTABLE_PHONE))


async def _set(session, key: str, value: str | None) -> None:
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend import models
    t = models.Base.metadata.tables["app_settings"]
    if value is None:
        await session.execute(delete(t).where(t.c["key"] == key))
    else:
        await session.execute(pg_insert(t).values(key=key, value=value)
                              .on_conflict_do_update(index_elements=["key"],
                                                     set_={"value": value}))


async def seed_queues() -> dict:
    """Stage real work through the REAL app, so every side effect a trainee
    will see — the HOD's bell, the notification text, the pending row's shape —
    is the one Live produces. Returns counts per queue."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import func, select

    from backend import models
    from backend.api.db import SessionLocal
    from backend.api.main import app
    from backend.api.services import ledger, procurement

    md = models.Base.metadata
    today = _dt.date.today().isoformat()
    pw = practice_password()
    out: dict[str, int] = {}

    # The supporting-document gate is ON in Live and stays ON in Practice
    # (parity). It is lifted only for the seconds it takes to stage the seed —
    # the trainee's own entries meet it exactly as in Live.
    async with SessionLocal() as s:
        t = md.tables["app_settings"]
        prev = (await s.execute(select(t.c["value"]).where(
            t.c["key"] == "require_entry_documents"))).scalar_one_or_none()
        await _set(s, "require_entry_documents", "0")
        await s.commit()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://practice-overlay") as ac:
            r = await ac.post("/auth/login", json={"username": "practice.storekeeper",
                                                   "password": pw})
            r.raise_for_status()
            H = {"Authorization": f"Bearer {r.json()['access_token']}",
                 "X-GI-Instance": "training"}
            async with SessionLocal() as s:
                inv = md.tables["inventory"]
                saps = [x[0] for x in (await s.execute(
                    select(inv.c["SAP_Code"]).where(inv.c["Site_ID"] == SITE)
                    .order_by(inv.c["SAP_Code"]).limit(6))).all()]
            receipts = [("899001", 24.0), (saps[0], 40.0), (saps[1], 12.0)]
            for sap, qty in receipts:
                rr = await ac.post("/entry/receipts", headers=H, json={
                    "Date": today, "SAP_Code": sap, "Quantity": qty, "Site_ID": SITE,
                    "Supplier": "Halcyon Industrial Supply", "wbs": WBS,
                    "Remarks": "Practice seed — approve or reject me"})
                if rr.status_code != 201:
                    print(f"  ⚠️  receipt {sap}: {rr.status_code} {rr.text[:160]}")
            issues = [("899001", 6.0, "Aria Bellweather"),
                      (saps[2], 3.0, "Tomas Halversen"),
                      (saps[3], 2.0, "Nadia Okonjo")]
            for sap, qty, who in issues:
                ri = await ac.post("/entry/consumption", headers=H, json={
                    "Date": today, "SAP_Code": sap, "Quantity": qty, "Site_ID": SITE,
                    "Work_Type": "Maintenance", "Tank_No": "Tank 1", "Issued_To": who,
                    "wbs": WBS, "Remarks": "Practice seed — approve or reject me"})
                if ri.status_code != 201:
                    print(f"  ⚠️  issue {sap}: {ri.status_code} {ri.text[:160]}")

        async with SessionLocal() as s:
            await ledger.stage_return(s, username="practice.storekeeper", data={
                "Site_ID": SITE, "SAP_Code": "899001", "Quantity": 2.0,
                "Reason": "Surplus to job", "Lot_Number": "LOT-TUT-001",
                "Remarks": "Practice seed"})
            await s.commit()
            draft = await procurement.create_pr(
                s, username="practice.hod", site_id=SITE,
                lines=[{"SAP_Code": saps[4], "Requested_Qty": 50},
                       {"SAP_Code": saps[5], "Requested_Qty": 20}],
                notes="Practice seed — a draft for the HOD to submit")
            sent = await procurement.create_pr(
                s, username="practice.hod", site_id=SITE,
                lines=[{"SAP_Code": "899001", "Requested_Qty": 100}],
                notes="Practice seed — already with Logistics")
            await s.commit()
            if sent.get("pr_number"):
                await procurement.submit_pr(s, username="practice.hod",
                                            pr_number=sent["pr_number"], site_id=SITE)
                await s.commit()
            for name in ("pending_receipts", "pending_issues", "pending_returns"):
                tt = md.tables[name]
                out[name] = (await s.execute(select(func.count()).select_from(tt).where(
                    tt.c["status"] == ledger.PENDING))).scalar_one()
            pm = md.tables["pr_master"]
            out["pr_draft"] = (await s.execute(select(func.count(func.distinct(
                pm.c["PR_Number"]))).where(pm.c["logistics_status"] == "site_draft"))).scalar_one()
            if draft.get("error") or sent.get("error"):
                print(f"  ⚠️  PR seed: {draft.get('error') or sent.get('error')}")
    finally:
        async with SessionLocal() as s:
            await _set(s, "require_entry_documents", prev)
            await s.commit()
    return out


def fixture_version() -> int:
    sys.path.insert(0, str(_ROOT / "tools"))
    import make_tutorial_db
    return make_tutorial_db.DATASET_VERSION


async def run() -> int:
    from backend.api.config import database_name, is_practice
    if not is_practice():
        print("❌ practice_overlay runs as a Practice process: set GI_INSTANCE=training",
              file=sys.stderr)
        return 2
    from backend.api.db import SessionLocal, engine
    print(f"▶ overlay v{OVERLAY_VERSION} → {database_name()}")
    async with SessionLocal() as s:
        acc = await seed_accounts(s)
        await seed_settings(s, fixture_version=fixture_version())
        await s.commit()
    counts = await seed_queues()
    await engine.dispose()
    print(f"  accounts={acc['accounts']} · " + " · ".join(f"{k}={v}" for k, v in counts.items()))
    if acc["admin_password"]:
        print(f"  🔑 practice.admin password (generated — shown ONCE): {acc['admin_password']}")
    # ⚠️ THE OVERLAY CHECKS ITSELF, as the tutorial fixture does: an empty
    # approval queue does not fail anything downstream — it produces a Practice
    # environment where an HOD trainee has nothing to practise on, which looks
    # finished and is not.
    problems = []
    if acc["accounts"] != len(ACCOUNTS):
        problems.append(f"expected {len(ACCOUNTS)} practice accounts, found {acc['accounts']}")
    for q in ("pending_receipts", "pending_issues", "pending_returns", "pr_draft"):
        if not counts.get(q):
            problems.append(f"{q} is EMPTY — that role's queue would have nothing to practise on")
    if problems:
        print("❌ overlay self-check failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
