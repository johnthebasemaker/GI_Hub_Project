"""
backend/api/documents.py — document & label generation (Phase-6 parity).

DRY by design — nothing here re-implements a renderer that already exists:
  * Tabular master-data exports reuse reports.py's to_xlsx / to_csv / to_pdf.
  * The two QR label sheets (bin labels + employee badges) share ONE FPDF grid
    backbone (`_grid_pdf`); each supplies only a per-cell draw callback.
  * SOP / User-Manual are the pre-built PDFs at the repo root, streamed as-is.

Role model: label/badge/export outputs are management artifacts → level ≥ 2,
site-scoped for the site-bearing entities (inventory, employees). The SOP and
User-Manual are reference material → any authenticated user.
"""
from __future__ import annotations

import io
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import LargeBinary, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_level, resolve_site_param, site_scope
from .db import get_session
from .reports import _FORMATS, _latin
from .services.ledger import _MD

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/documents", tags=["documents"])

_PDF_MEDIA = "application/pdf"


def _pdf_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(io.BytesIO(data), media_type=_PDF_MEDIA,
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- shared QR-label grid backbone --------------------------------------------
def _qr_png(data: str, box: int = 8):
    """Render `data` to an in-memory PNG QR (returns a BytesIO)."""
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return buf


def _grid_pdf(title: str, cells: list, draw_cell, *, cols: int = 3,
              rows_per_page: int = 4) -> bytes:
    """A4 portrait grid of `cells`, `draw_cell(pdf, x, y, w, h, item)` per cell.
    Shared by the bin-label and employee-badge sheets."""
    from fpdf import FPDF
    PAGE_W, PAGE_H = 210, 297
    MARGIN, HEADER_H = 8, 14
    MARGIN_T = MARGIN + HEADER_H
    CELL_W = (PAGE_W - 2 * MARGIN) / cols
    CELL_H = (PAGE_H - MARGIN_T - MARGIN) / rows_per_page
    per_page = cols * rows_per_page

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(auto=False)

    def _header():
        pdf.set_xy(MARGIN, MARGIN)
        pdf.set_font("helvetica", "B", 11)
        pdf.set_text_color(10, 25, 47)
        pdf.cell(PAGE_W - 2 * MARGIN, 5, _latin(f"GENERAL INDUSTRIES — {title}"),
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 8)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(PAGE_W - 2 * MARGIN, 4, _latin(f"{len(cells)} item(s)"),
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_line_width(0.2)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(MARGIN, MARGIN + HEADER_H - 1, PAGE_W - MARGIN, MARGIN + HEADER_H - 1)

    if not cells:
        pdf.add_page()
        _header()
        pdf.set_xy(MARGIN, MARGIN_T + 10)
        pdf.set_font("helvetica", "", 11)
        pdf.cell(0, 8, "No items to print for this selection.")
        return bytes(pdf.output())

    for idx, item in enumerate(cells):
        if idx % per_page == 0:
            pdf.add_page()
            _header()
        p = idx % per_page
        cx = MARGIN + (p % cols) * CELL_W
        cy = MARGIN_T + (p // cols) * CELL_H
        pdf.set_line_width(0.3)
        pdf.set_draw_color(180, 180, 180)
        pdf.rect(cx, cy, CELL_W, CELL_H)
        draw_cell(pdf, cx, cy, CELL_W, CELL_H, item)
    return bytes(pdf.output())


def _draw_bin_label(pdf, cx, cy, cw, ch, item):
    qr_size = 40
    buf = _qr_png(str(item.get("SAP_Code", "")))
    pdf.image(buf, x=cx + (cw - qr_size) / 2, y=cy + 4, w=qr_size, h=qr_size)
    pdf.set_font("helvetica", "B", 8)
    pdf.set_text_color(10, 25, 47)
    pdf.set_xy(cx + 1, cy + 4 + qr_size + 2)
    pdf.cell(cw - 2, 5, _latin(str(item.get("SAP_Code", ""))), align="C")
    pdf.set_font("helvetica", "", 6)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(cx + 1, cy + 4 + qr_size + 8)
    pdf.multi_cell(cw - 2, 3, _latin(str(item.get("Equipment_Description", "") or ""))[:60], align="C")


def _draw_badge(pdf, cx, cy, cw, ch, item):
    qr_size = 38
    name = _latin(str(item.get("Name", "")))[:32]
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(10, 25, 47)
    pdf.set_xy(cx + 1, cy + 3)
    pdf.cell(cw - 2, 5, name, align="C")
    buf = _qr_png(str(item.get("ID_Number", "")))
    qr_y = cy + 10
    pdf.image(buf, x=cx + (cw - qr_size) / 2, y=qr_y, w=qr_size, h=qr_size)
    pdf.set_font("helvetica", "B", 9)
    pdf.set_xy(cx + 1, qr_y + qr_size + 2)
    pdf.cell(cw - 2, 4.5, _latin(str(item.get("ID_Number", ""))), align="C")
    pdf.set_font("helvetica", "", 7)
    pdf.set_text_color(110, 110, 110)
    pdf.set_xy(cx + 1, qr_y + qr_size + 7)
    pdf.cell(cw - 2, 4, _latin(str(item.get("Department", "") or ""))[:32], align="C")


# --- QR bin labels ------------------------------------------------------------
@router.get("/qr-labels", summary="Printable QR bin-label sheet (inventory items)")
async def qr_labels(site_id: Optional[str] = None,
                    sap_codes: Optional[str] = Query(None, description="comma-separated SAP codes; blank = all at the site"),
                    user: dict = Depends(require_level(2)),
                    session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        raise HTTPException(403, "no site is assigned to your account")
    inv = _MD.tables["inventory"]
    stmt = select(inv.c["SAP_Code"], inv.c["Equipment_Description"])
    if site_id:
        stmt = stmt.where(inv.c["Site_ID"] == site_id)
    if sap_codes:
        # The list may REPEAT a code — one label per occurrence (the legacy
        # QR-request flow printed a per-item label quantity). Order and
        # multiplicity are preserved; the sheet caps at 600 cells.
        wanted = [s.strip() for s in sap_codes.split(",") if s.strip()]
        stmt = stmt.where(inv.c["SAP_Code"].in_(set(wanted)))
        by_code = {m["SAP_Code"]: dict(m) for m in
                   (await session.execute(stmt)).mappings().all()}
        rows = [by_code[c] for c in wanted if c in by_code][:600]
    else:
        rows = [dict(m) for m in (await session.execute(
            stmt.order_by(inv.c["SAP_Code"]).limit(600))).mappings().all()]
    data = _grid_pdf("Bin Labels", rows, _draw_bin_label)
    return _pdf_response(data, "qr-bin-labels.pdf")


# --- employee badges ----------------------------------------------------------
@router.get("/employee-badges", summary="Printable employee QR-badge sheet (3×4)")
async def employee_badges(site_id: Optional[str] = None,
                          user: dict = Depends(require_level(2)),
                          session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        raise HTTPException(403, "no site is assigned to your account")
    emp = _MD.tables["employees"]
    stmt = select(emp.c["ID_Number"], emp.c["Name"], emp.c["Department"]).where(
        emp.c["status"] == "active")
    if site_id:
        stmt = stmt.where(emp.c["Site_ID"] == site_id)
    rows = [dict(m) for m in (await session.execute(stmt.order_by(emp.c["Name"]).limit(600))).mappings().all()]
    data = _grid_pdf("Employee Badges", rows, _draw_badge)
    return _pdf_response(data, "employee-badges.pdf")


# --- single employee badge (PNG — the legacy encode_id_to_png parity) ---------
@router.get("/employee-badge/{id_number}",
            summary="One employee's QR badge as a PNG (payload = raw ID_Number)")
async def employee_badge_png(id_number: str,
                             user: dict = Depends(require_level(2)),
                             session: AsyncSession = Depends(get_session)):
    from fastapi.responses import Response
    emp = _MD.tables["employees"]
    stmt = select(emp.c["ID_Number"], emp.c["Name"], emp.c["Site_ID"]).where(
        emp.c["ID_Number"] == id_number.strip())
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(404, "no employee with that ID number")
    scope = site_scope(user)
    if scope is not None and (row.Site_ID or "") != scope:
        raise HTTPException(404, "no employee with that ID number")
    buf = _qr_png(str(row.ID_Number))
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Content-Disposition":
                             f'attachment; filename="badge_{row.ID_Number}.png"'})


# --- SOP / Manual (pre-built root PDFs, reference material for any user) -------
_DOCS = {"sop": ("GI_Hub_SOP.pdf", "GI-Hub-SOP.pdf"),
         "manual": ("GI_Hub_User_Manual.pdf", "GI-Hub-User-Manual.pdf")}


@router.get("/reference/{key}", summary="Download the SOP or User Manual (PDF)")
async def reference_doc(key: str, user: dict = Depends(get_current_user)):
    if key not in _DOCS:
        raise HTTPException(404, f"unknown document {key!r} (sop | manual)")
    src, out = _DOCS[key]
    path = os.path.join(_ROOT, src)
    if not os.path.exists(path):
        raise HTTPException(404, f"{src} is not available on the server")
    with open(path, "rb") as fh:
        data = fh.read()
    return _pdf_response(data, out)


# --- master-data exports (reuse the reports renderers) ------------------------
# entity -> (table name, has Site_ID?)
_MASTER = {
    "vendors": ("vendors", False),
    "warehouses": ("warehouses", False),
    "employees": ("employees", True),
    "inventory": ("inventory", True),
}
_SENSITIVE = ("password", "totp", "secret", "token", "hash", "salt")


@router.get("/master/{entity}", summary="Export a master table (xlsx | csv | pdf)")
async def export_master(entity: str, format: str = Query("xlsx"),
                        site_id: Optional[str] = None,
                        user: dict = Depends(require_level(2)),
                        session: AsyncSession = Depends(get_session)):
    if entity not in _MASTER:
        raise HTTPException(404, f"unknown master entity {entity!r}")
    fmt = format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {sorted(_FORMATS)}")
    tname, has_site = _MASTER[entity]
    t = _MD.tables[tname]
    out_cols = [c for c in t.columns
                if not isinstance(c.type, LargeBinary)
                and not any(s in c.name.lower() for s in _SENSITIVE)]
    stmt = select(*out_cols)
    if has_site:
        site_id = resolve_site_param(user, site_id)
        if site_id == "":
            raise HTTPException(403, "no site is assigned to your account")
        if site_id:
            stmt = stmt.where(t.c["Site_ID"] == site_id)
    order = t.c["SAP_Code"] if "SAP_Code" in t.c else t.c["id"]
    res = await session.execute(stmt.order_by(order).limit(20000))
    columns = list(res.keys())
    rows = [list(r) for r in res.all()]
    render, media = _FORMATS[fmt]
    data = render(f"{entity.title()} Master", columns, rows, user["username"])
    return StreamingResponse(io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{entity}-master.{fmt}"'})
