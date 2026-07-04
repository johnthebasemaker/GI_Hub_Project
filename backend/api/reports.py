"""
backend/api/reports.py — downloadable reports (Excel / PDF / CSV).

The old app's exportable reports are the biggest capability the new stack
lacked. Each report is a query over the live data; the same rows render to any
of three formats. Reports are a management view → gated at role level ≥ 2
(hod / logistics / admin).

  GET /reports                      → list of available reports + their filters
  GET /reports/{key}?format=xlsx    → the file download (xlsx | pdf | csv)
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level
from .db import get_session
from .stock import SQL_EXPIRING, SQL_SITE_STOCK

router = APIRouter(prefix="/reports", tags=["reports"],
                   dependencies=[Depends(require_level(2))])


# --- data (each returns title, columns, rows) --------------------------------
async def _run(session: AsyncSession, sql: str, params: dict):
    res = await session.execute(text(sql), params)
    columns = list(res.keys())
    rows = [list(r) for r in res.all()]
    return columns, rows


def _cutoff(days: int) -> str:
    days = max(1, min(days, 3650))
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


async def rep_stock(session, *, site_id=None, **_):
    sql = f'SELECT * FROM ({SQL_SITE_STOCK}) s'
    params: dict = {}
    if site_id:
        sql += ' WHERE s."Site_ID" = :site'
        params["site"] = site_id
    sql += ' ORDER BY s."SAP_Code", s."Site_ID"'
    cols, rows = await _run(session, sql, params)
    return "Current Stock by Site", cols, rows


async def rep_expiring(session, *, within_days=30, **_):
    sql = f'SELECT * FROM ({SQL_EXPIRING}) e WHERE e."Days_Until_Expiry" <= :w ORDER BY e."Days_Until_Expiry"'
    cols, rows = await _run(session, sql, {"w": max(0, min(within_days, 3650))})
    return f"Expiring Stock (≤ {within_days} days)", cols, rows


async def rep_consumption(session, *, site_id=None, days=30, **_):
    where = '"Date" >= :cutoff'
    params = {"cutoff": _cutoff(days)}
    if site_id:
        where += ' AND COALESCE("Site_ID", \'HQ\') = :site'
        params["site"] = site_id
    sql = f'''
        SELECT TRIM("SAP_Code") AS "SAP_Code", COALESCE("Site_ID",'HQ') AS "Site_ID",
               ROUND(CAST(SUM("Quantity") AS NUMERIC), 3) AS "Total_Consumed",
               COUNT(*) AS "Transactions", MAX("Date") AS "Last_Issue"
        FROM consumption WHERE {where}
        GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
        ORDER BY "Total_Consumed" DESC'''
    cols, rows = await _run(session, sql, params)
    return f"Consumption (last {days} days)", cols, rows


async def rep_receipts(session, *, site_id=None, days=30, **_):
    where = '"Date" >= :cutoff'
    params = {"cutoff": _cutoff(days)}
    if site_id:
        where += ' AND COALESCE("Site_ID", \'HQ\') = :site'
        params["site"] = site_id
    sql = f'''
        SELECT "Date", TRIM("SAP_Code") AS "SAP_Code", "Quantity", "Supplier",
               COALESCE("Site_ID",'HQ') AS "Site_ID", "PR_Number", "Lot_Number", "Expiry_Date"
        FROM receipts WHERE {where}
        ORDER BY "Date" DESC, "SAP_Code" LIMIT 5000'''
    cols, rows = await _run(session, sql, params)
    return f"Goods Receipts (last {days} days)", cols, rows


async def rep_purchase_orders(session, *, status=None, **_):
    where, params = "", {}
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    sql = f'''
        SELECT "PO_Number", "PR_Number", "Site_ID", "Vendor_Name", "PO_Date",
               "Expected_Delivery", status, created_by, created_at
        FROM purchase_orders {where}
        ORDER BY "PO_Number" DESC LIMIT 5000'''
    cols, rows = await _run(session, sql, params)
    return "Purchase Orders", cols, rows


async def rep_inventory(session, *, site_id=None, **_):
    where, params = "", {}
    if site_id:
        where = 'WHERE "Site_ID" = :site'
        params["site"] = site_id
    sql = f'''
        SELECT "SAP_Code", "Equipment_Description", "Material_Code", "Category",
               "UOM", "Minimum_Qty", "Unit_Cost", "Opening_Stock", "Site_ID", "Expiry_Date"
        FROM inventory {where} ORDER BY "SAP_Code" LIMIT 10000'''
    cols, rows = await _run(session, sql, params)
    return "Inventory Master", cols, rows


REPORTS = {
    "stock":           {"fn": rep_stock,           "label": "Current Stock",   "filters": ["site_id"],
                        "desc": "Current stock per item and site (received − consumed − returned)."},
    "expiring":        {"fn": rep_expiring,        "label": "Expiring Stock",  "filters": ["within_days"],
                        "desc": "Lots by days-to-expiry, flagged expired / short-dated."},
    "consumption":     {"fn": rep_consumption,     "label": "Consumption",     "filters": ["site_id", "days"],
                        "desc": "Consumption per item over a period."},
    "receipts":        {"fn": rep_receipts,        "label": "Goods Receipts",  "filters": ["site_id", "days"],
                        "desc": "Goods receipts over a period."},
    "purchase-orders": {"fn": rep_purchase_orders, "label": "Purchase Orders", "filters": ["status"],
                        "desc": "Purchase orders with status."},
    "inventory":       {"fn": rep_inventory,       "label": "Inventory Master","filters": ["site_id"],
                        "desc": "The full inventory master list."},
}


# --- renderers (all take title, columns, rows, username) ---------------------
def _xl_val(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def _latin(s: str) -> str:
    return s.encode("latin-1", "ignore").decode("latin-1")


def to_csv(title, columns, rows, username) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 cleanly


def to_xlsx(title, columns, rows, username) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = (title[:31] or "Report")
    ws.append(columns)
    fill = PatternFill("solid", fgColor="0A192F")
    font = Font(bold=True, color="FFFFFF")
    for c in ws[1]:
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([_xl_val(v) for v in r])
    for i, col in enumerate(columns, 1):
        widths = [len(str(col))] + [len(str(r[i - 1])) for r in rows[:200]]
        ws.column_dimensions[get_column_letter(i)].width = min(max(max(widths) + 2, 10), 45)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(title, columns, rows, username) -> bytes:
    from fpdf import FPDF
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, _latin(title.upper()), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "", 9)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf.cell(0, 6, _latin(f"Generated by {username}  ·  {stamp}  ·  {len(rows)} rows"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if not rows:
        pdf.cell(0, 10, "No data for this report.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())
    col_w = pdf.epw / max(len(columns), 1)
    pdf.set_font("helvetica", "B", 8)
    pdf.set_fill_color(10, 25, 47)
    pdf.set_text_color(255, 255, 255)
    for c in columns:
        pdf.cell(col_w, 7, _latin(str(c))[:18], border=1, fill=True, align="C")
    pdf.ln(7)
    pdf.set_font("helvetica", "", 7)
    pdf.set_text_color(0, 0, 0)
    for row in rows:
        if pdf.get_y() > 190:
            pdf.add_page()
        for v in row:
            pdf.cell(col_w, 6, _latin("" if v is None else str(v))[:24], border=1, align="C")
        pdf.ln(6)
    return bytes(pdf.output())


_FORMATS = {
    "xlsx": (to_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf":  (to_pdf,  "application/pdf"),
    "csv":  (to_csv,  "text/csv"),
}


@router.get("", summary="Available reports + their filters")
async def list_reports():
    return {"reports": [
        {"key": k, "label": v["label"], "description": v["desc"], "filters": v["filters"]}
        for k, v in REPORTS.items()
    ]}


@router.get("/{key}", summary="Download a report (xlsx | pdf | csv)")
async def download_report(key: str, format: str = Query("xlsx"),
                          site_id: Optional[str] = None, days: int = 30,
                          within_days: int = 30, status: Optional[str] = None,
                          user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    if key not in REPORTS:
        raise HTTPException(404, f"unknown report {key!r}")
    if format not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    title, columns, rows = await REPORTS[key]["fn"](
        session, site_id=site_id, days=days, within_days=within_days, status=status)
    render, media = _FORMATS[format]
    data = render(title, columns, rows, user["username"])
    stamp = _dt.date.today().isoformat()
    fname = f"{key}-{stamp}.{format}"
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
