"""
downloads.py — SME download helpers (Round 18 — raw .xlsx, no AES wrapper)

Round 17 wrapped Excel downloads in AES-encrypted .protected.zip and gated
them behind a password popover. Per Round 18, the encryption + Excel
popover are gone: Excel files download directly as raw .xlsx with a
clearly-labeled filename. PDF retains the password popover because PDFs
often travel via email and password-on-PDF still adds value at rest.

Filename convention:
    SME_<ReportName>_<Site>_<YYYY-MM-DD>.<ext>

Why no st.download_button monkey patch:
    Per Correction #2 from Round 17, downloads are standalone helpers
    namespaced sme_*. They render st.download_button directly (Excel) or
    inside a popover (PDF). Nothing global is patched.
"""
from __future__ import annotations

import base64
import datetime
import io
import re
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ── PDF gate password (rotate by editing) ───────────────────────────────────
SME_PDF_PASSWORD = "pdf2026"

# ── Optional encryption dep for PDF only (xlsx is plain now) ────────────────
try:
    from reportlab.lib import colors as _rl_colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    _HAS_REPORTLAB = True
except ImportError:
    _HAS_REPORTLAB = False


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip()) or "file"


def sme_filename(report_name: str, site_id: str | None, ext: str) -> str:
    """SME_<ReportName>_<Site>_<YYYY-MM-DD>.<ext>"""
    today = datetime.date.today().isoformat()
    if not ext.startswith("."):
        ext = "." + ext
    site_part = f"_{_safe(site_id)}" if site_id else ""
    return f"SME_{_safe(report_name)}{site_part}_{today}{ext}"


# ---------------------------------------------------------------------------
# Excel builders — ERP yellow/amber header strip
# ---------------------------------------------------------------------------

_ERP_AMBER = "#FBBF24"
_ERP_HEADER_TEXT = "#1F2937"
_ERP_BORDER = "#D97706"


def _xlsx_header_format(workbook):
    return workbook.add_format({
        "bold": True,
        "bg_color": _ERP_AMBER,
        "color": _ERP_HEADER_TEXT,
        "border": 1,
        "border_color": _ERP_BORDER,
        "align": "left",
        "valign": "vcenter",
    })


def _xlsx_title_format(workbook):
    return workbook.add_format({
        "bold": True,
        "font_size": 14,
        "color": _ERP_HEADER_TEXT,
        "align": "left",
    })


def _xlsx_subtitle_format(workbook):
    return workbook.add_format({
        "italic": True,
        "font_size": 10,
        "color": "#6B7280",
        "align": "left",
    })


def _xlsx_section_format(workbook):
    return workbook.add_format({
        "bold": True,
        "font_size": 12,
        "color": _ERP_HEADER_TEXT,
        "bg_color": "#FEF3C7",
        "border": 1,
        "border_color": _ERP_BORDER,
        "align": "left",
        "valign": "vcenter",
    })


def _autosize(ws, df: pd.DataFrame, start_col: int = 0) -> None:
    for col_idx, col_name in enumerate(df.columns):
        try:
            width = max(
                len(str(col_name)) + 2,
                min(40, int(df[col_name].astype(str).str.len().max() or 12) + 2),
            )
        except Exception:
            width = 16
        ws.set_column(start_col + col_idx, start_col + col_idx, width)


def build_excel_bytes(
    df: pd.DataFrame,
    *,
    report_name: str,
    site_id: str | None = None,
    sheet_name: str = "Report",
    subtitle: str | None = None,
) -> bytes:
    """Single-sheet .xlsx with ERP-branded title + amber header strip."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        safe = (sheet_name or "Report")[:31]
        # Start data at row 3 so we can render a title block above
        df.to_excel(writer, sheet_name=safe, index=False, startrow=3)
        ws = writer.sheets[safe]
        ws.write(0, 0, f"SME · {report_name}", _xlsx_title_format(wb))
        sub_parts = []
        if site_id:
            sub_parts.append(f"Site: {site_id}")
        sub_parts.append(f"Generated: {datetime.date.today().isoformat()}")
        if subtitle:
            sub_parts.append(subtitle)
        ws.write(1, 0, " · ".join(sub_parts), _xlsx_subtitle_format(wb))
        hdr_fmt = _xlsx_header_format(wb)
        for col_idx, col_name in enumerate(df.columns):
            ws.write(3, col_idx, col_name, hdr_fmt)
        _autosize(ws, df)
        ws.freeze_panes(4, 0)
    return buf.getvalue()


def build_multi_sheet_excel(
    sheets: list[dict],
    *,
    report_name: str,
    site_id: str | None = None,
) -> bytes:
    """sheets = [{name, df, title?, subtitle?}, ...] → branded multi-sheet xlsx."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        hdr_fmt = _xlsx_header_format(wb)
        title_fmt = _xlsx_title_format(wb)
        sub_fmt = _xlsx_subtitle_format(wb)
        for s in sheets:
            name = (s.get("name") or "Sheet")[:31]
            df = s["df"] if s.get("df") is not None else pd.DataFrame()
            df.to_excel(writer, sheet_name=name, index=False, startrow=3)
            ws = writer.sheets[name]
            ws.write(0, 0, f"SME · {s.get('title') or s.get('name') or report_name}", title_fmt)
            sub_parts = []
            if site_id:
                sub_parts.append(f"Site: {site_id}")
            sub_parts.append(f"Generated: {datetime.date.today().isoformat()}")
            if s.get("subtitle"):
                sub_parts.append(s["subtitle"])
            ws.write(1, 0, " · ".join(sub_parts), sub_fmt)
            for col_idx, col_name in enumerate(df.columns):
                ws.write(3, col_idx, col_name, hdr_fmt)
            _autosize(ws, df)
            ws.freeze_panes(4, 0)
    return buf.getvalue()


def build_equipment_report_excel(
    *,
    report_name: str,
    site_id: str | None,
    equipment_summary: pd.DataFrame,
    system_summary: pd.DataFrame,
    detailed: pd.DataFrame,
) -> bytes:
    """Faithful port of SME's `_equipment_report_excel` 3-section format.

    Sections in order:
      1) Summary by Equipment   — cols: Equipment Tag No., Equipment Name,
                                  System Name, Total SQM
      2) Summary by System Code — cols: Equipment Tag No., Equipment Name,
                                  System Code, System Name, Total SQM
      3) Detailed Table         — per-material alloc (Material_Code,
                                  Material_Name, UOM, Demand_Qty,
                                  Allocated_Qty, Shortfall_Qty,
                                  Fulfillment_Rate)
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        ws = wb.add_worksheet("Equipment Report")
        writer.sheets["Equipment Report"] = ws
        title_fmt   = _xlsx_title_format(wb)
        sub_fmt     = _xlsx_subtitle_format(wb)
        section_fmt = _xlsx_section_format(wb)
        hdr_fmt     = _xlsx_header_format(wb)

        ws.write(0, 0, f"SME · {report_name}", title_fmt)
        ws.write(1, 0, f"Site: {site_id or 'all'} · Generated: "
                       f"{datetime.date.today().isoformat()}", sub_fmt)
        cursor = 3

        for idx, (label, section_df) in enumerate([
            ("1) Summary by Equipment", equipment_summary),
            ("2) Summary by System Code", system_summary),
            ("3) Detailed Table", detailed),
        ], start=1):
            if idx > 1:
                cursor += 1  # blank spacer row
            ws.merge_range(
                cursor, 0, cursor, max(0, len(section_df.columns) - 1),
                label, section_fmt,
            )
            cursor += 1
            for col_idx, col_name in enumerate(section_df.columns):
                ws.write(cursor, col_idx, col_name, hdr_fmt)
            cursor += 1
            for _, row in section_df.iterrows():
                for col_idx, col_name in enumerate(section_df.columns):
                    val = row[col_name]
                    ws.write(cursor, col_idx,
                             "" if pd.isna(val) else val)
                cursor += 1

        # Best-effort autosize across the union of all sections' columns.
        all_widths: dict[int, int] = {}
        for section_df in (equipment_summary, system_summary, detailed):
            for col_idx, col_name in enumerate(section_df.columns):
                try:
                    w = max(
                        len(str(col_name)) + 2,
                        min(40, int(section_df[col_name].astype(str)
                            .str.len().max() or 12) + 2),
                    )
                except Exception:
                    w = 16
                all_widths[col_idx] = max(all_widths.get(col_idx, 12), w)
        for col_idx, w in all_widths.items():
            ws.set_column(col_idx, col_idx, w)
    return buf.getvalue()


def build_location_report_excel(
    *,
    report_name: str,
    site_id: str | None,
    location: str,
    feasibility_for_location: pd.DataFrame,
    materials_for_location: pd.DataFrame,
    summary_blocks: list[dict] | None = None,
) -> bytes:
    """Faithful port of SME's `_location_report_excel` — per-location alloc
    matrix + summary blocks. `summary_blocks` = [{title, df}, ...]."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        ws = wb.add_worksheet((location or "Location")[:31])
        writer.sheets[(location or "Location")[:31]] = ws
        title_fmt   = _xlsx_title_format(wb)
        sub_fmt     = _xlsx_subtitle_format(wb)
        section_fmt = _xlsx_section_format(wb)
        hdr_fmt     = _xlsx_header_format(wb)
        ws.write(0, 0, f"SME · {report_name} — {location}", title_fmt)
        ws.write(1, 0, f"Site: {site_id or 'all'} · Generated: "
                       f"{datetime.date.today().isoformat()}", sub_fmt)
        cursor = 3
        sections = [
            ("1) Equipment feasibility at this location",
             feasibility_for_location),
            ("2) Per-material allocation at this location",
             materials_for_location),
        ]
        for blk in (summary_blocks or []):
            sections.append((blk["title"], blk["df"]))
        for idx, (label, section_df) in enumerate(sections, start=1):
            if idx > 1:
                cursor += 1
            ws.merge_range(
                cursor, 0, cursor,
                max(0, len(section_df.columns) - 1),
                label, section_fmt,
            )
            cursor += 1
            for col_idx, col_name in enumerate(section_df.columns):
                ws.write(cursor, col_idx, col_name, hdr_fmt)
            cursor += 1
            for _, row in section_df.iterrows():
                for col_idx, col_name in enumerate(section_df.columns):
                    val = row[col_name]
                    ws.write(cursor, col_idx,
                             "" if pd.isna(val) else val)
                cursor += 1
        ws.set_column(0, 30, 18)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF builders (unchanged — still password-protected)
# ---------------------------------------------------------------------------

def _df_for_pdf(df: pd.DataFrame, max_cols: int = 12) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({"(empty)": [""]})
    if len(df.columns) <= max_cols:
        return df
    return df.iloc[:, :max_cols]


def _pdf_from_sheets(
    sheets: list[dict],
    password: str,
    *,
    site_id: str | None = None,
    landscape_orient: bool = True,
) -> bytes:
    if not _HAS_REPORTLAB:
        return b""
    buf = io.BytesIO()
    pagesize = landscape(A4) if landscape_orient else A4
    doc = SimpleDocTemplate(
        buf, pagesize=pagesize,
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        encrypt=password,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading2"],
        fontSize=14, textColor=_rl_colors.HexColor(_ERP_HEADER_TEXT),
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["Italic"],
        fontSize=9, textColor=_rl_colors.HexColor("#6B7280"),
    )
    story = []
    for i, sheet in enumerate(sheets):
        if i > 0:
            story.append(PageBreak())
        title = sheet.get("title") or sheet.get("name", "Report")
        story.append(Paragraph(f"SME · {title}", title_style))
        sub_parts = []
        if site_id:
            sub_parts.append(f"Site: {site_id}")
        sub_parts.append(f"Generated: {datetime.date.today().isoformat()}")
        story.append(Paragraph(" · ".join(sub_parts), sub_style))
        story.append(Spacer(1, 4 * mm))
        df = _df_for_pdf(sheet.get("df"))
        data = [list(df.columns)] + df.astype(str).values.tolist()
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _rl_colors.HexColor(_ERP_AMBER)),
            ("TEXTCOLOR",  (0, 0), (-1, 0), _rl_colors.HexColor(_ERP_HEADER_TEXT)),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, _rl_colors.HexColor("#9CA3AF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue()


def _pdf_from_df(
    df: pd.DataFrame,
    title: str,
    password: str,
    *,
    site_id: str | None = None,
) -> bytes:
    return _pdf_from_sheets(
        [{"name": title, "df": df, "title": title}],
        password, site_id=site_id,
    )


# ---------------------------------------------------------------------------
# Browser auto-download trigger (only used by PDF popover)
# ---------------------------------------------------------------------------

def _trigger_browser_download(data: bytes, file_name: str, mime: str) -> None:
    b64 = base64.b64encode(data).decode("ascii")
    nonce = uuid.uuid4().hex[:8]
    html = f"""
    <html><body>
    <script>
      const a = window.parent.document.createElement('a');
      a.href = 'data:{mime};base64,{b64}';
      a.download = {file_name!r};
      a.style.display = 'none';
      window.parent.document.body.appendChild(a);
      a.click();
      setTimeout(() => a.remove(), 800);
    </script>
    <span style="display:none">{nonce}</span>
    </body></html>
    """
    components.html(html, height=0, width=0)


# ---------------------------------------------------------------------------
# Public widgets
# ---------------------------------------------------------------------------

_XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def sme_xlsx_download(
    label: str,
    df: pd.DataFrame,
    *,
    report_name: str,
    key: str,
    site_id: str | None = None,
    sheet_name: str = "Report",
    use_container_width: bool = True,
) -> None:
    """Single-sheet xlsx — raw download, no popover."""
    payload = build_excel_bytes(
        df, report_name=report_name, site_id=site_id, sheet_name=sheet_name,
    )
    st.download_button(
        label,
        data=payload,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_xlsx_{key}",
        use_container_width=use_container_width,
    )


def sme_multi_sheet_xlsx_download(
    label: str,
    sheets: list[dict],
    *,
    report_name: str,
    key: str,
    site_id: str | None = None,
    use_container_width: bool = True,
) -> None:
    """Multi-sheet xlsx — raw download."""
    payload = build_multi_sheet_excel(
        sheets, report_name=report_name, site_id=site_id,
    )
    st.download_button(
        label,
        data=payload,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_xlsx_multi_{key}",
        use_container_width=use_container_width,
    )


def sme_custom_xlsx_download(
    label: str,
    payload_bytes: bytes,
    *,
    report_name: str,
    key: str,
    site_id: str | None = None,
    use_container_width: bool = True,
) -> None:
    """Download already-built xlsx bytes (e.g., 3-section equipment report)."""
    st.download_button(
        label,
        data=payload_bytes,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_xlsx_custom_{key}",
        use_container_width=use_container_width,
    )


def sme_pdf_download(
    label: str,
    *,
    report_name: str,
    key: str,
    df: pd.DataFrame | None = None,
    sheets: list[dict] | None = None,
    title: str = "Report",
    site_id: str | None = None,
    use_container_width: bool = True,
) -> None:
    """PDF still gated behind a password popover (encryption preserved)."""
    with st.popover(label, use_container_width=use_container_width):
        if not _HAS_REPORTLAB:
            st.error("PDF generation unavailable (reportlab not installed).")
            return
        st.caption("🔐 Password protected. Enter the PDF password.")
        pwd_key = f"_sme_pdf_pwd_{key}"
        fired_key = f"_sme_pdf_fired_{key}"
        pwd = st.text_input(
            "Password", type="password", key=pwd_key,
            label_visibility="collapsed",
        )
        if pwd and pwd == SME_PDF_PASSWORD:
            if not st.session_state.get(fired_key):
                if sheets:
                    payload = _pdf_from_sheets(
                        sheets, SME_PDF_PASSWORD, site_id=site_id,
                    )
                else:
                    payload = _pdf_from_df(
                        df if df is not None else pd.DataFrame(),
                        title, SME_PDF_PASSWORD, site_id=site_id,
                    )
                fname = sme_filename(report_name, site_id, "pdf")
                _trigger_browser_download(payload, fname, "application/pdf")
                st.session_state[fired_key] = True
            st.success("✓ Download started")
            if st.button("↻ Download again", key=f"_sme_pdf_again_{key}"):
                st.session_state[fired_key] = False
                st.rerun()
        elif pwd:
            st.error("Wrong password.")


def sme_download_pair(
    df: pd.DataFrame,
    *,
    report_name: str,
    title: str | None = None,
    key: str,
    site_id: str | None = None,
    sheet_name: str | None = None,
) -> None:
    """Excel (raw) + PDF (popover) side by side."""
    c_xlsx, c_pdf = st.columns(2)
    with c_xlsx:
        sme_xlsx_download(
            f"⬇ Excel — {title or report_name}",
            df,
            report_name=report_name,
            site_id=site_id,
            key=f"{key}_x",
            sheet_name=sheet_name or report_name,
        )
    with c_pdf:
        sme_pdf_download(
            f"⬇ PDF — {title or report_name}",
            df=df,
            report_name=report_name,
            site_id=site_id,
            title=title or report_name,
            key=f"{key}_p",
        )


def sme_multi_sheet_download_pair(
    sheets: list[dict],
    *,
    report_name: str,
    title: str | None = None,
    key: str,
    site_id: str | None = None,
) -> None:
    c_xlsx, c_pdf = st.columns(2)
    with c_xlsx:
        sme_multi_sheet_xlsx_download(
            f"⬇ Excel — {title or report_name}",
            sheets,
            report_name=report_name,
            site_id=site_id,
            key=f"{key}_x",
        )
    with c_pdf:
        sme_pdf_download(
            f"⬇ PDF — {title or report_name}",
            sheets=sheets,
            report_name=report_name,
            site_id=site_id,
            title=title or report_name,
            key=f"{key}_p",
        )
