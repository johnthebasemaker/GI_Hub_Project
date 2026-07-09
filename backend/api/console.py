"""
backend/api/console.py — admin-console completion (Phase-9 parity).

Global sites CRUD (over the legacy `system_settings` category='Site' rows) ·
app settings incl. MAINTENANCE MODE · manual Postgres backup trigger · live
session viewer/revoker (over `auth_sessions`) · logistics-oversight KPIs ·
cross-site requests (HOD raises → admin decides; legacy `requests` table) ·
feedback / bug reports (legacy `bug_reports`). NO new tables.

Maintenance-mode semantics: when `app_settings.maintenance_mode = '1'`,
non-admin LOGIN and REFRESH are refused (503). Existing access tokens keep
working for at most their 15-minute lifetime — acceptable and documented; the
flag also rides /health so the SPA can show a banner.

Backup: shells out to pg_dump (custom format). Looks at GI_PG_DUMP, then
PATH, then the Homebrew postgresql@16 path. Inside the slim API container
pg_dump is absent → 501 with instructions (server backups run via the db
container per docs/DEPLOY.md).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import shutil
import subprocess
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_level, revoke_all_sessions,
                   site_scope)
from .config import async_database_url
from .db import get_session
from .services import whatsapp as wa
from .services.ledger import _MD, write_audit
from .services.notifications import notify
from .stock import SQL_SITE_STOCK

# Cross-site requests above this many units escalate to the target-site HOD via
# WhatsApp (Phase 7 — legacy ">5 item" cross-site escalation).
XSITE_ESCALATION_QTY = 5

settings_t = _MD.tables["app_settings"]
sysset_t = _MD.tables["system_settings"]
sessions_t = _MD.tables["auth_sessions"]
requests_t = _MD.tables["requests"]
bugs_t = _MD.tables["bug_reports"]
lots_t = _MD.tables["lots"]

admin = APIRouter(prefix="/admin", tags=["admin console"],
                  dependencies=[Depends(require_level(4))])
public = APIRouter(tags=["feedback"])


# --- Global sites CRUD ----------------------------------------------------------
class SiteIn(BaseModel):
    name: str


@admin.get("/sites", summary="Global sites (system_settings category='Site')")
async def list_sites(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(sysset_t.c["id"], sysset_t.c["value"])
        .where(sysset_t.c["category"] == "Site")
        .order_by(sysset_t.c["id"]))).all()
    return {"items": [{"id": r.id, "name": r.value} for r in rows]}


@admin.post("/sites", status_code=201, summary="Add a site")
async def add_site(body: SiteIn = Body(...),
                   user: dict = Depends(require_level(4)),
                   session: AsyncSession = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "site name is required")
    dup = (await session.execute(select(func.count()).select_from(sysset_t)
           .where(sysset_t.c["category"] == "Site",
                  sysset_t.c["value"] == name))).scalar_one()
    if dup:
        raise HTTPException(409, f"site {name!r} already exists")
    sid = (await session.execute(insert(sysset_t).values(
        category="Site", value=name).returning(sysset_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "SITE_CREATE", "system_settings",
                      f"id={sid} {name}")
    await session.commit()
    return {"created": True, "id": sid, "name": name}


@admin.delete("/sites/{sid}", summary="Remove a site (blocks if users are bound to it)")
async def delete_site(sid: int, user: dict = Depends(require_level(4)),
                      session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(sysset_t.c["value"]).where(
        sysset_t.c["id"] == sid, sysset_t.c["category"] == "Site"))).first()
    if row is None:
        raise HTTPException(404, f"site id {sid} not found")
    users_t = _MD.tables["users"]
    bound = (await session.execute(select(func.count()).select_from(users_t)
             .where(users_t.c["Site_ID"] == row.value))).scalar_one()
    if bound:
        raise HTTPException(409, f"{bound} user(s) are bound to {row.value!r} — reassign them first")
    await session.execute(delete(sysset_t).where(sysset_t.c["id"] == sid))
    await write_audit(session, user["username"], "SITE_DELETE", "system_settings",
                      f"id={sid} {row.value}")
    await session.commit()
    return {"deleted": sid, "name": row.value}


# --- Settings (incl. maintenance mode) ------------------------------------------
_EDITABLE_SETTINGS = {"maintenance_mode", "low_stock_days", "burn_alert_days",
                      "expiry_warn_days", "ai_enabled", "ai_assistant_enabled",
                      "ai_doc_intel_enabled", "ai_ocr_enabled",
                      "ai_nl_search_enabled", "ai_insights_enabled"}


class SettingIn(BaseModel):
    key: str
    value: str


@admin.get("/settings", summary="App settings (key/value)")
async def get_settings(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(settings_t))).all()
    return {"settings": {r.key: r.value for r in rows},
            "editable": sorted(_EDITABLE_SETTINGS)}


@admin.put("/settings", summary="Update one app setting (whitelisted keys)")
async def put_setting(body: SettingIn = Body(...),
                      user: dict = Depends(require_level(4)),
                      session: AsyncSession = Depends(get_session)):
    if body.key not in _EDITABLE_SETTINGS:
        raise HTTPException(422, f"key must be one of {sorted(_EDITABLE_SETTINGS)}")
    if body.key == "maintenance_mode" and body.value not in ("0", "1"):
        raise HTTPException(422, "maintenance_mode must be '0' or '1'")
    res = await session.execute(update(settings_t)
                                .where(settings_t.c["key"] == body.key)
                                .values(value=body.value))
    if res.rowcount == 0:
        await session.execute(insert(settings_t).values(key=body.key, value=body.value))
    await write_audit(session, user["username"], "SETTING_UPDATE", "app_settings",
                      f"{body.key}={body.value}")
    await session.commit()
    return {"updated": True, body.key: body.value}


# --- Manual backup trigger -------------------------------------------------------
def _find_pg_dump() -> Optional[str]:
    return (os.environ.get("GI_PG_DUMP")
            or shutil.which("pg_dump")
            or (p if os.path.exists(p := "/opt/homebrew/opt/postgresql@16/bin/pg_dump") else None))


@admin.post("/backup", summary="Run pg_dump now (custom format) into GI_BACKUPS_DIR")
async def run_backup(user: dict = Depends(require_level(4)),
                     session: AsyncSession = Depends(get_session)):
    pg_dump = _find_pg_dump()
    if not pg_dump:
        raise HTTPException(501, "pg_dump is not available on this host — on the "
                                 "server, back up via the db container (docs/DEPLOY.md §ops)")
    u = urlparse(async_database_url().replace("+asyncpg", ""))
    out_dir = os.environ.get("GI_BACKUPS_DIR", os.path.join("backups", "new_stack"))
    os.makedirs(out_dir, exist_ok=True)
    fname = f"gihub-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
    path = os.path.join(out_dir, fname)
    cmd = [pg_dump, "-Fc", "-h", u.hostname or "127.0.0.1",
           "-p", str(u.port or 5432), "-U", u.username or "postgres",
           "-d", (u.path or "/gihub").lstrip("/"), "-f", path]
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True,
                                   text=True, env=env, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(500, f"pg_dump failed: {proc.stderr.strip()[:400]}")
    size = os.path.getsize(path)
    await write_audit(session, user["username"], "DB_BACKUP", "app_settings",
                      f"{path} ({size} bytes)")
    await session.commit()
    return {"backed_up": True, "file": path, "size_bytes": size}


# --- Access control: live sessions ----------------------------------------------
@admin.get("/sessions", summary="Auth sessions (no token material)")
async def list_sessions(username: Optional[str] = None, active: bool = True,
                        session: AsyncSession = Depends(get_session)):
    stmt = select(sessions_t.c["id"], sessions_t.c["username"],
                  sessions_t.c["created_at"], sessions_t.c["expires_at"],
                  sessions_t.c["revoked_at"], sessions_t.c["revoke_reason"])
    if username:
        stmt = stmt.where(sessions_t.c["username"] == username)
    if active:
        stmt = stmt.where(sessions_t.c["revoked_at"].is_(None),
                          sessions_t.c["expires_at"] > _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None))
    rows = (await session.execute(stmt.order_by(sessions_t.c["id"].desc()).limit(500))
            ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@admin.post("/sessions/{sid}/revoke", summary="Revoke one session")
async def revoke_session(sid: int, user: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    res = await session.execute(update(sessions_t)
                                .where(sessions_t.c["id"] == sid,
                                       sessions_t.c["revoked_at"].is_(None))
                                .values(revoked_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
                                        revoke_reason="admin-revoked"))
    if res.rowcount == 0:
        raise HTTPException(404, f"no active session {sid}")
    await write_audit(session, user["username"], "SESSION_REVOKE", "auth_sessions",
                      f"id={sid}")
    await session.commit()
    return {"revoked": sid}


@admin.post("/sessions/revoke-user/{username}", summary="Revoke every session for a user")
async def revoke_user_sessions(username: str, user: dict = Depends(require_level(4)),
                               session: AsyncSession = Depends(get_session)):
    n = await revoke_all_sessions(session, username, "admin-revoked")
    await write_audit(session, user["username"], "SESSION_REVOKE_ALL", "auth_sessions",
                      f"username={username} n={n}")
    await session.commit()
    return {"revoked": n, "username": username}


# --- WhatsApp Console (admin): outbox viewer + manual retry ---------------------
outbox_t = _MD.tables["whatsapp_outbox"]


@admin.get("/whatsapp", summary="WhatsApp outbox (queue + delivery status)")
async def whatsapp_outbox(status: Optional[str] = None, limit: int = 200,
                          session: AsyncSession = Depends(get_session)):
    cols = [outbox_t.c["id"], outbox_t.c["to_number"], outbox_t.c["message_type"],
            outbox_t.c["body"], outbox_t.c["status"], outbox_t.c["meta_message_id"],
            outbox_t.c["error"], outbox_t.c["event_key"], outbox_t.c["related_table"],
            outbox_t.c["related_ref"], outbox_t.c["attempts"], outbox_t.c["created_by"],
            outbox_t.c["created_at"], outbox_t.c["sent_at"]]
    stmt = select(*cols).order_by(outbox_t.c["id"].desc()).limit(max(1, min(int(limit), 1000)))
    if status:
        stmt = stmt.where(outbox_t.c["status"] == status)
    rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
    counts = {r["status"]: r["n"] for r in (await session.execute(text(
        "SELECT status, COUNT(*) AS n FROM whatsapp_outbox GROUP BY status"))).mappings().all()}
    return {"items": rows, "counts": counts, "configured": wa.enabled()}


@admin.post("/whatsapp/{outbox_id}/retry", summary="Retry a failed/pending WhatsApp message")
async def whatsapp_retry(outbox_id: int, user: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    res = await wa.retry(session, outbox_id=outbox_id)
    if res.get("error"):
        raise HTTPException(409, res["error"])
    await write_audit(session, user["username"], "WHATSAPP_RETRY", "whatsapp_outbox",
                      f"id={outbox_id} → {res.get('status')}")
    await session.commit()
    return res


# --- Lot lifecycle (admin): quarantine / dispose / release ----------------------
_LOT_STATUSES = {"open", "quarantined", "disposed"}


class LotStatusIn(BaseModel):
    status: str
    reason: Optional[str] = None


@admin.get("/lots", summary="Lots with lifecycle status")
async def admin_lots(status: Optional[str] = None, sap_code: Optional[str] = None,
                     session: AsyncSession = Depends(get_session)):
    stmt = select(lots_t).order_by(lots_t.c["id"].desc()).limit(1000)
    if status:
        stmt = stmt.where(lots_t.c["Status"] == status)
    if sap_code:
        stmt = stmt.where(func.trim(lots_t.c["SAP_Code"]) == sap_code.strip())
    return {"items": [dict(m) for m in (await session.execute(stmt)).mappings().all()]}


@admin.post("/lots/{lot_id}/status", summary="Quarantine / dispose / release a lot")
async def set_lot_status(lot_id: int, body: LotStatusIn = Body(...),
                         user: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    if body.status not in _LOT_STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(_LOT_STATUSES)}")
    async with session.begin():
        row = (await session.execute(select(lots_t.c["Status"], lots_t.c["Lot_Number"])
               .where(lots_t.c["id"] == lot_id))).first()
        if row is None:
            raise HTTPException(404, f"lot {lot_id} not found")
        if row[0] == "disposed":
            raise HTTPException(409, "a disposed lot cannot change status")
        if row[0] == body.status:
            raise HTTPException(409, f"lot is already {body.status}")
        await session.execute(update(lots_t).where(lots_t.c["id"] == lot_id).values(Status=body.status))
        await write_audit(session, user["username"], f"LOT_{body.status.upper()}", "lots",
                          f"id={lot_id} lot={row[1]} {row[0]}→{body.status}"
                          + (f": {body.reason}" if body.reason else ""))
    return {"updated": True, "id": lot_id, "status": body.status, "prior": row[0]}


# --- System-overview KPIs (admin) -----------------------------------------------
@admin.get("/system-overview", summary="System overview: DB size, transaction counts, valuation by site")
async def system_overview(session: AsyncSession = Depends(get_session)):
    async def _scalar(sql: str):
        return (await session.execute(text(sql))).scalar_one()

    try:
        db_size = await _scalar("SELECT pg_size_pretty(pg_database_size(current_database()))")
        db_bytes = int(await _scalar("SELECT pg_database_size(current_database())"))
    except Exception:
        db_size, db_bytes = "n/a", None

    receipts = int(await _scalar('SELECT COUNT(*) FROM receipts'))
    consumption = int(await _scalar('SELECT COUNT(*) FROM consumption'))
    returns = int(await _scalar('SELECT COUNT(*) FROM returns'))
    adjustments = int(await _scalar('SELECT COUNT(*) FROM stock_adjustments'))
    audit = int(await _scalar('SELECT COUNT(*) FROM system_audit_log'))
    users = int(await _scalar('SELECT COUNT(*) FROM users'))
    sites = int(await _scalar("SELECT COUNT(*) FROM system_settings WHERE category='Site'"))

    by_site = [dict(m) for m in (await session.execute(text(f'''
        SELECT COALESCE(s."Site_ID",'—') AS "Site_ID",
               COALESCE(ROUND(CAST(SUM(s."Current_Stock"*COALESCE(i."Unit_Cost",0)) AS NUMERIC),2),0) AS "value"
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        GROUP BY COALESCE(s."Site_ID",'—') ORDER BY "value" DESC'''))).mappings().all()]
    valuation_total = round(sum(float(r["value"] or 0) for r in by_site), 2)

    return {
        "db_size": db_size, "db_bytes": db_bytes,
        "transactions": {"receipts": receipts, "consumption": consumption,
                         "returns": returns, "adjustments": adjustments,
                         "audit_log": audit,
                         "total": receipts + consumption + returns + adjustments},
        "users": users, "sites": sites,
        "valuation_total": valuation_total, "valuation_by_site": by_site,
    }


# --- Logistics oversight KPIs (admin + logistics) --------------------------------
oversight = APIRouter(prefix="/admin", tags=["admin console"])


@oversight.get("/oversight", summary="Cross-site procurement KPIs",
               dependencies=[Depends(require_level(3))])
async def logistics_oversight(session: AsyncSession = Depends(get_session)):
    async def _q(sql: str):
        return [dict(m) for m in (await session.execute(text(sql))).mappings().all()]

    prs = await _q('''SELECT workflow_state AS state, COUNT(DISTINCT "PR_Number") AS n
                      FROM pr_master GROUP BY workflow_state ORDER BY workflow_state''')
    pos = await _q('''SELECT status, COUNT(*) AS n FROM purchase_orders
                      GROUP BY status ORDER BY status''')
    top_vendors = await _q('''SELECT COALESCE("Vendor_Name",'—') AS vendor, COUNT(*) AS pos
                              FROM purchase_orders GROUP BY COALESCE("Vendor_Name",'—')
                              ORDER BY pos DESC LIMIT 10''')
    dns = await _q('''SELECT status, COUNT(*) AS n FROM delivery_notes
                      GROUP BY status ORDER BY status''')
    wh_load = await _q('''SELECT COALESCE("Warehouse_ID",'—') AS warehouse, status, COUNT(*) AS n
                          FROM po_assignments GROUP BY COALESCE("Warehouse_ID",'—'), status
                          ORDER BY warehouse, status''')
    closures = await _q('''SELECT COALESCE(reason,'—') AS reason, COUNT(*) AS n
                           FROM po_force_closures GROUP BY COALESCE(reason,'—')
                           ORDER BY n DESC LIMIT 10''')
    returns = await _q('''SELECT status, COUNT(*) AS n FROM po_returns
                          GROUP BY status ORDER BY status''')
    return {"prs_by_state": prs, "pos_by_status": pos, "top_vendors": top_vendors,
            "dns_by_status": dns, "warehouse_load": wh_load,
            "force_closures_by_reason": closures, "vendor_returns_by_status": returns}


# --- Cross-site requests (HOD raises → admin decides) -----------------------------
xsite = APIRouter(prefix="/xsite", tags=["cross-site requests"])


class XSiteIn(BaseModel):
    target_site: str
    SAP_Code: str
    requested_qty: float
    notes: Optional[str] = None
    requesting_site: Optional[str] = None  # admins may set; scoped users pinned


class DecideIn(BaseModel):
    action: str  # approve | reject
    suggested_qty: Optional[float] = None
    notes: Optional[str] = None


@xsite.post("", status_code=201, summary="Raise a cross-site material request")
async def create_xsite(body: XSiteIn = Body(...),
                       user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    scope = site_scope(user)
    req_site = scope if scope is not None else (body.requesting_site or "").strip()
    if not req_site:
        raise HTTPException(422, "requesting_site is required")
    if body.requested_qty <= 0:
        raise HTTPException(422, "requested_qty must be > 0")
    if body.target_site.strip() == req_site:
        raise HTTPException(422, "target site must differ from the requesting site")
    # Availability snapshot at the target site (advisory, like the legacy cart).
    avail = (await session.execute(text('''
        SELECT COALESCE(SUM(CASE WHEN t = 'r' THEN q ELSE -q END), 0) FROM (
            SELECT 'r' AS t, "Quantity" AS q FROM receipts
             WHERE TRIM("SAP_Code") = :sap AND COALESCE("Site_ID",'HQ') = :site
            UNION ALL SELECT 'c', "Quantity" FROM consumption
             WHERE TRIM("SAP_Code") = :sap AND COALESCE("Site_ID",'HQ') = :site
            UNION ALL SELECT 'c', "Quantity" FROM returns
             WHERE TRIM("SAP_Code") = :sap AND COALESCE("Site_ID",'HQ') = :site) x'''),
        {"sap": body.SAP_Code.strip(), "site": body.target_site.strip()})).scalar_one()
    rid = (await session.execute(insert(requests_t).values(
        requesting_site=req_site, target_site=body.target_site.strip(),
        SAP_Code=body.SAP_Code.strip(), requested_qty=body.requested_qty,
        available_qty=float(avail or 0), status="pending", notes=body.notes,
        requested_by=user["username"]).returning(requests_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "XSITE_REQUEST", "requests",
                      f"id={rid} {body.SAP_Code} {req_site}→{body.target_site} qty={body.requested_qty:g}")
    await notify(session, event_key="cross_site_requested", recipient_role="admin",
                 severity="info", title="Cross-site request raised",
                 body=f"{req_site} asks {body.target_site} for {body.SAP_Code} × {body.requested_qty:g}",
                 link_page="/admin/console", related_table="requests", related_ref=str(rid))
    await session.commit()

    # Phase 7 — escalate large cross-site requests to the target-site HOD via
    # WhatsApp. Best-effort: a messaging failure never fails the request.
    if body.requested_qty > XSITE_ESCALATION_QTY:
        try:
            nums = await wa.hod_numbers(session, body.target_site.strip())
            msg = (f"⚠️ Large cross-site request: {req_site} is asking {body.target_site} "
                   f"for {body.SAP_Code} × {body.requested_qty:g} (available {float(avail or 0):g}). "
                   f"Raised by {user['username']}. Please review.")
            for n in nums:
                await wa.send_text(session, to=n, body=msg, event_key="xsite_escalation",
                                   related_table="requests", related_ref=rid, created_by=user["username"])
            await session.commit()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            await session.rollback()
    return {"created": True, "id": rid, "available_at_target": float(avail or 0)}


@xsite.get("", summary="Cross-site requests (own site below admin; all for admin)")
async def list_xsite(status: Optional[str] = None, mine: bool = False,
                     user: dict = Depends(require_level(2)),
                     session: AsyncSession = Depends(get_session)):
    stmt = select(requests_t)
    scope = site_scope(user)
    if scope is not None:
        if scope == "":
            return {"items": []}
        stmt = stmt.where(requests_t.c["requesting_site"] == scope)
    if mine:
        stmt = stmt.where(requests_t.c["requested_by"] == user["username"])
    if status:
        stmt = stmt.where(requests_t.c["status"] == status)
    rows = (await session.execute(stmt.order_by(requests_t.c["id"].desc()).limit(500))
            ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@xsite.post("/{rid}/decide", summary="Approve/reject a cross-site request (admin)")
async def decide_xsite(rid: int, body: DecideIn = Body(...),
                       user: dict = Depends(require_level(4)),
                       session: AsyncSession = Depends(get_session)):
    if body.action not in ("approve", "reject"):
        raise HTTPException(422, "action must be approve | reject")
    row = (await session.execute(select(requests_t).where(requests_t.c["id"] == rid))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, f"request {rid} not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"request already {row['status']}")
    new_status = "approved" if body.action == "approve" else "rejected"
    values: dict = {"status": new_status, "reviewed_by": user["username"],
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)}
    if body.suggested_qty is not None:
        values["suggested_qty"] = body.suggested_qty
    if body.notes:
        values["notes"] = body.notes
    await session.execute(update(requests_t).where(requests_t.c["id"] == rid).values(**values))
    await write_audit(session, user["username"], "XSITE_DECIDE", "requests",
                      f"id={rid} → {new_status}")
    await notify(session, event_key="cross_site_decided",
                 recipient_user=row["requested_by"],
                 severity="success" if new_status == "approved" else "warning",
                 title=f"Cross-site request {new_status}",
                 body=f"{row['SAP_Code']} {row['requesting_site']}→{row['target_site']}"
                      + (f" · suggested qty {body.suggested_qty:g}" if body.suggested_qty else ""),
                 link_page="/hod/requests", related_table="requests", related_ref=str(rid))
    await session.commit()
    return {"id": rid, "status": new_status}


@xsite.delete("/{rid}", summary="Cancel a pending request (creator) / delete (admin)")
async def delete_xsite(rid: int, user: dict = Depends(require_level(2)),
                       session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(requests_t.c["status"], requests_t.c["requested_by"])
           .where(requests_t.c["id"] == rid))).first()
    if row is None:
        raise HTTPException(404, f"request {rid} not found")
    if user["level"] < 4:
        if row.requested_by != user["username"]:
            raise HTTPException(403, "you may only cancel your own request")
        if row.status != "pending":
            raise HTTPException(409, f"request already {row.status} — cannot cancel")
    await session.execute(delete(requests_t).where(requests_t.c["id"] == rid))
    await write_audit(session, user["username"], "XSITE_DELETE", "requests", f"id={rid}")
    await session.commit()
    return {"deleted": rid}


# --- Feedback / bug reports -------------------------------------------------------
class FeedbackIn(BaseModel):
    type: str  # bug | feature | other
    description: str
    page: Optional[str] = None


class FeedbackDecideIn(BaseModel):
    status: str  # open | in_progress | resolved | closed
    admin_response: Optional[str] = None


@public.post("/feedback", status_code=201, summary="Submit a bug report / feature request")
async def submit_feedback(body: FeedbackIn = Body(...),
                          user: dict = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    if body.type not in ("bug", "feature", "other"):
        raise HTTPException(422, "type must be bug | feature | other")
    if not body.description.strip():
        raise HTTPException(422, "description is required")
    fid = (await session.execute(insert(bugs_t).values(
        username=user["username"], type=body.type, page=body.page,
        description=body.description.strip(), status="open")
        .returning(bugs_t.c["id"]))).scalar_one()
    await notify(session, event_key="feedback_submitted", recipient_role="admin",
                 severity="info", title=f"New {body.type} report from {user['username']}",
                 body=body.description.strip()[:140], link_page="/admin/console",
                 related_table="bug_reports", related_ref=str(fid))
    await session.commit()
    return {"created": True, "id": fid}


@public.get("/feedback/mine", summary="My submitted feedback")
async def my_feedback(user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(bugs_t)
            .where(bugs_t.c["username"] == user["username"])
            .order_by(bugs_t.c["id"].desc()).limit(100))).mappings().all()
    return {"items": [dict(r) for r in rows]}


@admin.get("/feedback", summary="All feedback (admin)")
async def all_feedback(status: Optional[str] = None,
                       session: AsyncSession = Depends(get_session)):
    stmt = select(bugs_t)
    if status:
        stmt = stmt.where(bugs_t.c["status"] == status)
    rows = (await session.execute(stmt.order_by(bugs_t.c["id"].desc()).limit(500))
            ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@admin.patch("/feedback/{fid}", summary="Update a report's status / respond")
async def decide_feedback(fid: int, body: FeedbackDecideIn = Body(...),
                          user: dict = Depends(require_level(4)),
                          session: AsyncSession = Depends(get_session)):
    if body.status not in ("open", "in_progress", "resolved", "closed"):
        raise HTTPException(422, "bad status")
    row = (await session.execute(select(bugs_t.c["username"]).where(bugs_t.c["id"] == fid))).first()
    if row is None:
        raise HTTPException(404, f"report {fid} not found")
    values: dict = {"status": body.status,
                    "updated_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)}
    if body.admin_response is not None:
        values["admin_response"] = body.admin_response
    await session.execute(update(bugs_t).where(bugs_t.c["id"] == fid).values(**values))
    await notify(session, event_key="feedback_updated", recipient_user=row.username,
                 severity="info", title=f"Your report #{fid} is now {body.status}",
                 body=(body.admin_response or "")[:140] or f"Status changed to {body.status}.",
                 link_page="/feedback", related_table="bug_reports", related_ref=str(fid))
    await write_audit(session, user["username"], "FEEDBACK_UPDATE", "bug_reports",
                      f"id={fid} → {body.status}")
    await session.commit()
    return {"id": fid, "status": body.status}


@admin.delete("/feedback/{fid}", summary="Delete a report (admin)")
async def delete_feedback(fid: int, user: dict = Depends(require_level(4)),
                          session: AsyncSession = Depends(get_session)):
    res = await session.execute(delete(bugs_t).where(bugs_t.c["id"] == fid))
    if res.rowcount == 0:
        raise HTTPException(404, f"report {fid} not found")
    await write_audit(session, user["username"], "FEEDBACK_DELETE", "bug_reports", f"id={fid}")
    await session.commit()
    return {"deleted": fid}
