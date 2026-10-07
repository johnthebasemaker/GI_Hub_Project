"""
backend/api/lot_register.py — Phase 16c: lots, their expiry, and which to use first.

    GET /lot-register            every lot with its balance and expiry status
                                 (Lot Register page; ruling Q16-16: store keeper,
                                 HOD, QC, Head of Qualities — admin as always)
    GET /lot-register/options    the open lots of ONE material in FEFO order —
                                 the Issue form's lot picker; and, for a roll
                                 item, the rolls still on the shelf
    GET /lot-register/units      the rolls of one batch, with the tank each went to

Read-only. A lot's quantity is always computed from the ledger
(`stock.SQL_LOT_BALANCE`); nothing here writes one. FEFO stays allow-and-log
(locked 2026-06-30): the picker defaults to the FEFO lot and asks for a reason
when another is chosen, it never blocks.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param
from .db import get_session
from .services import lots as LOTS
from .stock import SQL_LOT_BALANCE

router = APIRouter(prefix="/lot-register", tags=["lots"])

# Ruling Q16-16(a). `require_roles` always admits the admin.
_READERS = require_roles("store_keeper", "hod", "qc", "qc_hod")

# Expiry buckets, in days. A lot inside the first is "expiring_30", etc.
BUCKETS = (30, 60, 90)
# The statuses the Admin Console writes (console._LOT_STATUSES) — `open`,
# `quarantined`, `disposed`. ⚠️ Spelled as the console spells it: this set once
# said "quarantine", so a quarantined lot stayed in the Issue picker — even as
# its FEFO default — while `ledger._FEFO_PICK` (Status = 'open') skipped it.
_INACTIVE = {"disposed", "quarantined", "expired"}


def lot_status(row: dict, today: Optional[_dt.date] = None) -> tuple[str, Optional[int]]:
    """(status, days_left). Order matters: a used-up lot is `exhausted` whatever
    its date, and an admin's disposal / quarantine outranks the calendar."""
    today = today or _dt.date.today()
    if float(row.get("Remaining_Qty") or 0) <= 1e-9:
        return "exhausted", None
    if str(row.get("Status") or "open") in _INACTIVE and row.get("Status") != "expired":
        return str(row["Status"]), None
    exp = str(row.get("Expiry_Date") or "")[:10]
    try:
        days = (_dt.date.fromisoformat(exp) - today).days
    except ValueError:
        return "no_expiry", None
    if days < 0:
        return "expired", days
    for b in BUCKETS:
        if days <= b:
            return f"expiring_{b}", days
    return "ok", days


async def _rows(session: AsyncSession, *, site: Optional[str], sap: Optional[str] = None,
                q: Optional[str] = None) -> list[dict]:
    where, params = [], {}
    if site is not None:
        where.append('lb."Site_ID" = :site')
        params["site"] = site
    if sap:
        where.append('lb."SAP_Code" = :sap')
        params["sap"] = sap.strip()
    if q and q.strip():
        where.append('(lb."Lot_Number" ILIKE :q OR lb."SAP_Code" ILIKE :q '
                     'OR i."Equipment_Description" ILIKE :q OR lb."Batch_Ref" ILIKE :q)')
        params["q"] = f"%{q.strip()}%"
    sql = f'''
        SELECT lb.*, i."Equipment_Description", i."Material_Code", i."UOM",
               i."Shelf_Life_Months",
               (SELECT COUNT(*) FROM lot_units u WHERE u."SAP_Code" = lb."SAP_Code"
                  AND u."Lot_Number" = lb."Lot_Number" AND u."Site_ID" = lb."Site_ID") AS "Units"
        FROM ({SQL_LOT_BALANCE}) lb
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = lb."SAP_Code"
        {"WHERE " + " AND ".join(where) if where else ""}'''
    rows = [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]
    today = _dt.date.today()
    for r in rows:
        r["status"], r["days_left"] = lot_status(r, today)
        for k in ("Received_Qty", "Consumed_Qty", "Returned_Qty", "Remaining_Qty"):
            r[k] = round(float(r.get(k) or 0), 4)
    return rows


def _fefo_key(r: dict):
    """Valid lots by expiry, then lots with no expiry, then EXPIRED lots last —
    the order `ledger._FEFO_PICK` uses."""
    exp = str(r.get("Expiry_Date") or "")[:10]
    rank = 1 if not exp else (2 if exp < _dt.date.today().isoformat() else 0)
    return (rank, exp, str(r.get("Received_Date") or ""), str(r["Lot_Number"]))


@router.get("", summary="Every lot with its balance and expiry status")
async def register(site_id: Optional[str] = Query(None), sap_code: Optional[str] = None,
                   q: Optional[str] = None, status: Optional[str] = None,
                   include_exhausted: bool = False,
                   user: dict = Depends(_READERS),
                   session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    rows = await _rows(session, site=site, sap=sap_code, q=q)
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    if status:
        rows = [r for r in rows if r["status"] == status
                or (status == "expiring" and r["status"].startswith("expiring_"))]
    elif not include_exhausted:
        rows = [r for r in rows if r["status"] != "exhausted"]
    rows.sort(key=lambda r: (r["SAP_Code"], _fefo_key(r)))
    exceptions = await LOTS.unknown_lots(session, site) if site is not None else []
    # Phase 21a: row by row, with the workbook SHEET and ROW to fix — lots that
    # do not exist and lots already used up. Reported, never blocked (Q21-18).
    problems = await LOTS.lot_problems(session, site)
    # Phase 22a: the problems that disappeared at the last sync, shown ✅ for a day
    fixed = await LOTS.recently_fixed(session, site)
    return {"items": rows, "summary": summary, "exceptions": exceptions,
            "problems": problems, "problems_fixed": fixed,
            "buckets": list(BUCKETS)}


@router.get("/problems.xlsx", summary="The workbook rows to fix, as Excel (Phase 22a)")
async def problems_xlsx(site_id: Optional[str] = Query(None), user: dict = Depends(_READERS),
                        session: AsyncSession = Depends(get_session)):
    """Every lot problem with the sheet and row to change in the workbook —
    what the operator fixes the workbook from. Rows are "the row at the last
    sync": inserting rows in Excel moves them."""
    import io

    import openpyxl
    from fastapi.responses import StreamingResponse
    site = resolve_site_param(user, site_id)
    probs = await LOTS.lot_problems(session, site)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lot problems"
    ws.append(["Sheet", "Row", "Date", "SAP", "Lot", "Qty", "Problem", "What to change"])
    for p in sorted(probs, key=lambda x: (str(x.get("sheet") or ""), int(x.get("row") or 0))):
        ws.append([p.get("sheet") or "(entered in the app)", p.get("row"), str(p.get("date") or "")[:10],
                   p.get("sap"), p.get("lot"), p.get("qty"),
                   p.get("problem_text") or p.get("problem"), p.get("hint") or ""])
    for col, w in zip("ABCDEFGH", (18, 8, 12, 10, 16, 8, 44, 40)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"lot-problems-{site or 'all'}-{_dt.date.today().isoformat()}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/options", summary="Open lots of one material in FEFO order (Issue form)")
async def options(sap_code: str, site_id: Optional[str] = Query(None),
                  user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    sap = sap_code.strip()
    mode = (await LOTS.tracking(session)).get(sap)
    if mode is None or site is None:
        return {"mode": mode, "items": [], "units": [], "fefo": None}
    rows = [r for r in await _rows(session, site=site, sap=sap)
            if r["status"] not in ("exhausted", "disposed", "quarantined")]
    rows.sort(key=_fefo_key)
    # ⚠️ THE SAME ORDER AS `ledger._FEFO_PICK` — expiry first (no expiry last),
    # then the oldest receipt. The picker's default and the server's
    # auto-pick must never disagree, or "leave it blank" and "take the
    # suggested lot" would post different lots.
    fefo = rows[0]["Lot_Number"] if rows else None
    items = [{"lot": r["Lot_Number"], "expiry": r.get("Expiry_Date"),
              "expiry_source": r.get("Expiry_Source"), "mfd": r.get("MFD_Date"),
              "remaining": r["Remaining_Qty"], "status": r["status"],
              "days_left": r["days_left"], "fefo": r["Lot_Number"] == fefo} for r in rows]
    units = []
    if mode == "roll" and rows:
        units = [dict(u) for u in (await session.execute(text('''
            SELECT u."Unit_No" AS unit, u."Lot_Number" AS lot, u."Location" AS location
            FROM lot_units u
            WHERE u."SAP_Code" = :sap AND u."Site_ID" = :site
              AND u."Lot_Number" = ANY(:lots)
              AND NOT EXISTS (SELECT 1 FROM consumption c WHERE TRIM(c."SAP_Code") = u."SAP_Code"
                              AND c."Serial_No" = u."Unit_No")
            ORDER BY u."Lot_Number", u."Unit_No"'''),
            {"sap": sap, "site": site, "lots": [r["Lot_Number"] for r in rows]})).mappings().all()]
        order = {r["Lot_Number"]: i for i, r in enumerate(rows)}
        units.sort(key=lambda u: (order.get(u["lot"], 99), u["unit"]))
    return {"mode": mode, "items": items, "units": units, "fefo": fefo}


@router.get("/units", summary="The rolls of one batch, and where each went")
async def units(sap_code: str, lot: str, site_id: Optional[str] = Query(None),
                user: dict = Depends(_READERS),
                session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    params = {"sap": sap_code.strip(), "lot": lot}
    site_sql = ""
    if site is not None:
        site_sql = 'AND u."Site_ID" = :site'
        params["site"] = site
    rows = [dict(r) for r in (await session.execute(text(f'''
        SELECT u."Unit_No", u."Received_Date", u."SQM", u."Pallet", u."Location",
               c."Date" AS "Used_On", c."Tank_No" AS "Tank_No"
        FROM lot_units u
        LEFT JOIN LATERAL (
            SELECT SUBSTRING(c."Date" FROM 1 FOR 10) AS "Date", c."Tank_No"
            FROM consumption c
            WHERE TRIM(c."SAP_Code") = u."SAP_Code" AND c."Serial_No" = u."Unit_No"
            ORDER BY c."Date" LIMIT 1) c ON TRUE
        WHERE u."SAP_Code" = :sap AND u."Lot_Number" = :lot {site_sql}
        ORDER BY u."Unit_No"'''), params)).mappings().all()]
    return {"items": rows, "used": sum(1 for r in rows if r["Used_On"]),
            "in_stock": sum(1 for r in rows if not r["Used_On"])}
