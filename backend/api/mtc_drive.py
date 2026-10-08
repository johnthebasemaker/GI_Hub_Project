"""
backend/api/mtc_drive.py — certificates from Drive on the Lots page (Phase 22c,
rulings Q22-10/11).

    GET  /drive/mtc                               files, QC's proposals, lots with no MTC
    POST /drive/mtc/{file_id}/assign              propose a file for lot(s) — admin, HOD, QC
    POST /drive/mtc/assignments/{id}/confirm      QC files it as the lot's certificate
    POST /drive/mtc/assignments/{id}/reject       QC turns it down

The matching itself is `services/mtc_links.py` (run after every Drive pull).
An exact match is filed by the pull; everything a person proposes waits for
QC (Q22-10) — a certificate on file clears the issue gate, so the last word on
an uncertain one is Quality's.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, resolve_site_param
from .db import get_session
from .services import mtc_links as MTC
from .services.ledger import write_audit

router = APIRouter(prefix="/drive/mtc", tags=["drive"])

# the Lots & Expiry readers (lot_register._READERS) and the admin
_READERS = require_roles("store_keeper", "hod", "qc", "qc_hod", "admin")
_PROPOSERS = require_roles("admin", "hod", "qc", "qc_hod")      # Q22-10: "I will assign"
_QC = require_roles("qc", "qc_hod")                             # …"then QC confirms"


@router.get("", summary="Drive certificates, QC's proposals, lots with no MTC (Phase 22c)")
async def overview(site_id: Optional[str] = None, user: dict = Depends(_READERS),
                   session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    return await MTC.overview(session, site)


class LotRef(BaseModel):
    SAP_Code: str
    Lot_Number: str


class AssignIn(BaseModel):
    lots: list[LotRef] = Field(..., min_length=1, max_length=50)
    note: Optional[str] = None


@router.post("/{file_id}/assign", summary="Propose this Drive certificate for lot(s) — QC confirms")
async def assign(file_id: int, body: AssignIn, user: dict = Depends(_PROPOSERS),
                 session: AsyncSession = Depends(get_session)):
    f = (await session.execute(text(
        "SELECT id, name FROM drive_files WHERE id = :i AND kind = 'mtc'"), {"i": file_id})).first()
    if f is None:
        raise HTTPException(404, "no such certificate file")
    made = 0
    for lot in body.lots:
        row = (await session.execute(text(
            'SELECT COALESCE("Site_ID", \'HQ\') FROM lots WHERE TRIM("SAP_Code") = TRIM(:s) '
            'AND "Lot_Number" = :l LIMIT 1'), {"s": lot.SAP_Code, "l": lot.Lot_Number})).first()
        if row is None:
            raise HTTPException(422, f"no lot {lot.Lot_Number} of SAP {lot.SAP_Code}")
        res = await session.execute(text('''
            INSERT INTO mtc_assignments (drive_file_id, "SAP_Code", "Lot_Number", "Site_ID",
                source, status, proposed_by, note)
            SELECT :f, :s, :l, :site, 'manual', 'proposed', :u, :n
            WHERE NOT EXISTS (SELECT 1 FROM mtc_assignments WHERE drive_file_id = :f
                AND "SAP_Code" = :s AND "Lot_Number" = :l AND status IN ('proposed', 'confirmed'))'''),
            {"f": file_id, "s": lot.SAP_Code.strip(), "l": lot.Lot_Number, "site": row[0],
             "u": user["username"], "n": body.note})
        made += res.rowcount or 0
    await write_audit(session, user["username"], "MTC_PROPOSE", "mtc_assignments",
                      f"{f[1]} → " + ", ".join(f"{x.SAP_Code}/{x.Lot_Number}" for x in body.lots))
    await session.execute(text(
        "UPDATE drive_files SET link_status = 'suggested' WHERE id = :i AND "
        "COALESCE(link_status, 'unlinked') = 'unlinked'"), {"i": file_id})
    await session.commit()
    return {"proposed": made}


async def _decide(aid: int, user: dict, session: AsyncSession, confirm: bool) -> dict:
    a = (await session.execute(text(
        'SELECT a.status, a."SAP_Code", a."Lot_Number", a."Site_ID", f.name FROM mtc_assignments a '
        "JOIN drive_files f ON f.id = a.drive_file_id WHERE a.id = :i"), {"i": aid})).first()
    if a is None:
        raise HTTPException(404, "no such proposal")
    if a[0] != "proposed":
        raise HTTPException(409, f"already {a[0]}")
    # a QC is scoped to their site (resolve_site_param refuses another one)
    resolve_site_param(user, a[3])
    if confirm:
        res = await MTC.confirm_assignment(session, aid, user=user["username"])
        if not res["ok"]:
            raise HTTPException(409, res["reason"])
    else:
        await session.execute(text(
            "UPDATE mtc_assignments SET status = 'rejected', decided_by = :u, "
            "decided_at = CURRENT_TIMESTAMP WHERE id = :i"), {"u": user["username"], "i": aid})
        res = {"ok": True}
    await write_audit(session, user["username"], "MTC_CONFIRM" if confirm else "MTC_REJECT",
                      "mtc_assignments", f"{a[4]} → {a[1]}/{a[2]}")
    await session.commit()
    return res


@router.post("/assignments/{aid}/confirm", summary="QC files the proposed certificate")
async def confirm(aid: int, user: dict = Depends(_QC), session: AsyncSession = Depends(get_session)):
    return await _decide(aid, user, session, True)


@router.post("/assignments/{aid}/reject", summary="QC turns the proposal down")
async def reject(aid: int, user: dict = Depends(_QC), session: AsyncSession = Depends(get_session)):
    return await _decide(aid, user, session, False)
