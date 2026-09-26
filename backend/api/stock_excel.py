"""
backend/api/stock_excel.py — "does GI Hub's stock match the Excel workbook,
and if not, WHY" (services/stock_excel.py), over HTTP.

  any signed-in user    GET  /stock/excel-check          the latest check (own site)
  writers (below)       POST /stock/excel-check          upload the workbook → check + store
                        POST /stock/excel-check/marked   upload → the marked COPY (.xlsx)

Checking changes no stock and the original workbook is never written — the
marked file is a copy for the person fixing the workbook. The upload is open to
the roles who keep that workbook or the ledger (store keeper, warehouse, HOD,
logistics, admin); a view-only role reads the stored result.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param, site_scope
from .db import get_session
from .services import stock_excel as SE

router = APIRouter(prefix="/stock/excel-check", tags=["stock vs Excel"])
_WRITERS = ("admin", "hod", "logistics", "store_keeper", "warehouse_user")
_MAX_BYTES = 25 * 1024 * 1024


def _site(user: dict, requested: Optional[str]) -> str:
    own = site_scope(user)
    if own:
        return own
    sid = (requested or "").strip()
    if not sid:
        raise HTTPException(422, "site_id is required for a global role")
    return sid


async def _read(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(422, "upload the .xlsx workbook (CNCEC_Inventory.xlsx)")
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "workbook larger than 25 MB")
    return data


@router.get("", summary="The latest stock-vs-workbook check for your site")
async def latest(site_id: Optional[str] = Query(None),
                 user: dict = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    return {"check": await SE.latest(session, site_id=resolve_site_param(user, site_id))}


@router.post("", summary="Upload the workbook: check it against GI Hub and store the result")
async def run(file: UploadFile = File(...), site_id: Optional[str] = Form(None),
              user: dict = Depends(require_roles(*_WRITERS)),
              session: AsyncSession = Depends(get_session)):
    data = await _read(file)
    site = _site(user, site_id)
    async with session.begin():
        result = await SE.diagnose(session, data, site_id=site)
        await SE.store(session, result, site_id=site, workbook=file.filename or "",
                       username=user["username"])
    return {"check": await SE.latest(session, site_id=site)}


@router.post("/marked", summary="Upload the workbook: get back a marked COPY")
async def marked(file: UploadFile = File(...), site_id: Optional[str] = Form(None),
                 user: dict = Depends(require_roles(*_WRITERS)),
                 session: AsyncSession = Depends(get_session)):
    data = await _read(file)
    site = _site(user, site_id)
    async with session.begin():
        result = await SE.diagnose(session, data, site_id=site)
        await SE.store(session, result, site_id=site, workbook=file.filename or "",
                       username=user["username"])
    out = SE.mark_workbook(data, result,
                           checked_at=datetime.now(timezone.utc).isoformat())
    stem = (file.filename or "workbook.xlsx").rsplit(".", 1)[0]
    return Response(out, media_type="application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet", headers={
                        "Content-Disposition": f'attachment; filename="{stem} - GI Hub check.xlsx"'})
