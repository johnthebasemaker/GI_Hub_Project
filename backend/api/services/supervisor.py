"""
backend/api/services/supervisor.py — supervisor material requests (SMR).

Ports the SMR flow from database.py:
  * create_smr   — create_supervisor_request() (worker must be active + site-bound;
                   snapshots per-line stock + an availability flag)
  * approve_smr  — approve_supervisor_request(): mirror lines into pending_issues
                   (Work_Type=SUPERVISOR_REQUEST, Source_Ref=SMR:<no>:<item>) — which
                   then flow into the HOD Approvals → Issues queue we already built.
  * reject_smr   — reject_supervisor_request()

Request no format SMR-YYYYMMDD-NNNN (resets per day).
"""
from __future__ import annotations

import datetime as _dt
import json as _json

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit

smr_t = _MD.tables["supervisor_material_requests"]
smr_items_t = _MD.tables["supervisor_material_request_items"]
employees_t = _MD.tables["employees"]
inventory_t = _MD.tables["inventory"]
pending_issues_t = _MD.tables["pending_issues"]


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


async def _request_no(session: AsyncSession) -> str:
    today = _dt.date.today().strftime("%Y%m%d")
    prefix = f"SMR-{today}-"
    last = (await session.execute(select(smr_t.c["request_no"]).where(
        smr_t.c["request_no"].like(prefix + "%")).order_by(smr_t.c["id"].desc()).limit(1))).scalar_one_or_none()
    nxt = 1
    if last:
        try:
            nxt = int(str(last).split("-")[-1]) + 1
        except (ValueError, IndexError):
            nxt = 1
    return f"{prefix}{nxt:04d}"


async def _stock_snapshot(session: AsyncSession, site_id: str, sap: str) -> float:
    sql = text('''
        SELECT COALESCE(i."Opening_Stock",0)
             + COALESCE((SELECT SUM("Quantity") FROM receipts    WHERE "SAP_Code"=i."SAP_Code" AND "Site_ID"=:site),0)
             - COALESCE((SELECT SUM("Quantity") FROM consumption WHERE "SAP_Code"=i."SAP_Code" AND "Site_ID"=:site),0)
             - COALESCE((SELECT SUM("Quantity") FROM returns     WHERE "SAP_Code"=i."SAP_Code" AND "Site_ID"=:site),0)
        FROM inventory i WHERE TRIM(i."SAP_Code")=TRIM(:sap) LIMIT 1''')
    val = (await session.execute(sql, {"site": site_id, "sap": sap})).scalar_one_or_none()
    return float(val) if val is not None else 0.0


# --- reads -------------------------------------------------------------------
async def list_smr(session: AsyncSession, *, site_id: str | None = None,
                   status: str | None = None, requested_by: str | None = None):
    stmt = select(
        smr_t.c["id"], smr_t.c["request_no"], smr_t.c["Site_ID"], smr_t.c["Worker_Name"],
        smr_t.c["Job_Tank_Place"], smr_t.c["requested_by"], smr_t.c["requested_at"],
        smr_t.c["status"], smr_t.c["sk_decided_by"],
    )
    if site_id:
        stmt = stmt.where(func.coalesce(smr_t.c["Site_ID"], "HQ") == site_id)
    if status:
        stmt = stmt.where(smr_t.c["status"] == status)
    if requested_by:
        stmt = stmt.where(smr_t.c["requested_by"] == requested_by)
    return _rows(await session.execute(stmt.order_by(smr_t.c["id"].desc())))


async def smr_items(session: AsyncSession, request_id: int):
    stmt = select(
        smr_items_t.c["id"], smr_items_t.c["SAP_Code"], smr_items_t.c["Equipment_Description"],
        smr_items_t.c["UOM"], smr_items_t.c["Requested_Qty"], smr_items_t.c["Stock_At_Request"],
        smr_items_t.c["Available_Flag"], smr_items_t.c["Notes"],
    ).where(smr_items_t.c["request_id"] == request_id).order_by(smr_items_t.c["id"])
    return _rows(await session.execute(stmt))


# --- mutations ---------------------------------------------------------------
async def create_smr(session: AsyncSession, *, supervisor: str, site_id: str, worker_id: str,
                     job_tank_place: str, old_ppe_returned: int, no_return_reason: str | None,
                     items: list[dict]) -> dict:
    if not (site_id and worker_id and (job_tank_place or "").strip()):
        return {"error": "site, worker and job/tank/place are required"}
    if old_ppe_returned == 0 and not (no_return_reason or "").strip():
        return {"error": "give a reason — old PPE not returned"}
    if not items:
        return {"error": "add at least one item"}

    w = (await session.execute(select(employees_t.c["Name"], employees_t.c["status"], employees_t.c["Site_ID"])
         .where(employees_t.c["ID_Number"] == worker_id))).first()
    if w is None:
        return {"error": f"worker {worker_id!r} not in employee master"}
    if w[1] != "active":
        return {"error": f"worker {worker_id!r} is {w[1]}, not active"}
    if (w[2] or "") != site_id:
        return {"error": f"worker {worker_id!r} is bound to site {w[2] or '—'}, not {site_id}"}
    worker_name = w[0]

    saps = [str(it.get("SAP_Code") or "").strip() for it in items]
    if any(not s for s in saps):
        return {"error": "every item needs a SAP_Code"}
    inv = {str(r[0]).strip(): r for r in (await session.execute(select(
        inventory_t.c["SAP_Code"], inventory_t.c["Material_Code"],
        inventory_t.c["Equipment_Description"], inventory_t.c["UOM"],
    ).where(inventory_t.c["SAP_Code"].in_(saps)))).all()}
    missing = [s for s in saps if s not in inv]
    if missing:
        return {"error": f"unknown SAP_Code(s): {', '.join(missing)}"}

    request_no = await _request_no(session)
    req_id = (await session.execute(insert(smr_t).values(
        request_no=request_no, Site_ID=site_id, Worker_ID=worker_id, Worker_Name=worker_name,
        Job_Tank_Place=job_tank_place.strip(), Old_PPE_Returned=int(old_ppe_returned),
        No_Return_Reason=(no_return_reason or "").strip() or None, requested_by=supervisor,
        status="pending_sk").returning(smr_t.c["id"]))).scalar_one()

    for it in items:
        sap = str(it.get("SAP_Code") or "").strip()
        qty = float(it.get("Requested_Qty") or 0)
        if qty <= 0:
            return {"error": f"quantity for {sap} must be > 0"}
        r = inv[sap]
        stock = await _stock_snapshot(session, site_id, sap)
        await session.execute(insert(smr_items_t).values(
            request_id=req_id, SAP_Code=sap, Material_Code=r[1], Equipment_Description=r[2],
            UOM=r[3], Requested_Qty=qty, Stock_At_Request=stock,
            Available_Flag=1 if stock >= qty else 0,
            Notes=(it.get("Notes") or "").strip() or None))

    await write_audit(session, supervisor, "SMR_CREATE", "supervisor_material_requests",
                      f"{request_no} site={site_id} worker={worker_id} lines={len(items)}")
    return {"created": True, "request_no": request_no, "request_id": req_id, "lines": len(items)}


async def approve_smr(session: AsyncSession, *, sk_username: str, request_id: int) -> dict:
    header = (await session.execute(select(smr_t).where(smr_t.c["id"] == request_id))).mappings().first()
    if header is None:
        return {"error": "request not found"}
    if header["status"] != "pending_sk":
        return {"error": f"request already {header['status']}"}
    items = await smr_items(session, request_id)
    if not items:
        return {"error": "no items to approve"}

    site_id = header["Site_ID"]
    worker_name = header["Worker_Name"]
    job_tank = header["Job_Tank_Place"]
    ppe_flag = "Y" if int(header["Old_PPE_Returned"] or 0) else "N"
    ppe_reason = (header.get("No_Return_Reason") or "").strip()
    request_no = header["request_no"]
    today = _dt.date.today().isoformat()

    posted = []
    for it in items:
        qty = float(it.get("Requested_Qty") or 0)
        if qty <= 0:
            continue
        remarks = (f"SMR {request_no} · {job_tank} · PPE returned: {ppe_flag}"
                   + (f" · Reason: {ppe_reason}" if ppe_flag == "N" and ppe_reason else ""))
        pid = (await session.execute(insert(pending_issues_t).values(
            Date=today, SAP_Code=it["SAP_Code"], Quantity=qty, Work_Type="SUPERVISOR_REQUEST",
            Remarks=remarks, Issued_By=sk_username, Issued_To=worker_name, Tank_No=job_tank,
            Site_ID=site_id, status="pending_hod", Source_Ref=f"SMR:{request_no}:{it['id']}",
            Requested_By=header.get("requested_by")).returning(pending_issues_t.c["id"]))).scalar_one()
        posted.append(pid)

    if not posted:
        return {"error": "nothing to post"}

    await session.execute(update(smr_t).where(smr_t.c["id"] == request_id).values(
        status="approved", sk_decided_by=sk_username, sk_decided_at=func.now(),
        posted_pending_ids=_json.dumps(posted)))
    await write_audit(session, sk_username, "SMR_APPROVE", "supervisor_material_requests",
                      f"{request_no} → {len(posted)} pending_issues")
    return {"approved": True, "request_no": request_no, "staged_issues": len(posted)}


async def reject_smr(session: AsyncSession, *, sk_username: str, request_id: int, reason: str) -> dict:
    res = await session.execute(update(smr_t).where(
        (smr_t.c["id"] == request_id) & (smr_t.c["status"] == "pending_sk")
    ).values(status="rejected", sk_decided_by=sk_username, sk_decided_at=func.now(),
             sk_reject_reason=reason or ""))
    if res.rowcount == 0:
        return {"error": "request not found or already decided"}
    await write_audit(session, sk_username, "SMR_REJECT", "supervisor_material_requests",
                      f"id={request_id} reason={reason or '-'}")
    return {"rejected": True, "id": request_id}
