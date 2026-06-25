"""
downloads.py — SME-style download helpers (R19 — full 7-scheme parity).

Replaces the simplified R18 builders with the full SME format:
- Logo at top-left (sme_logo.png embedded)
- Title bar row colored per scheme (title_bg)
- Header row colored per scheme (header_bg) with white bold text
- Data rows with row banding
- Optional GRAND TOTAL row with total_bg / total_fg
- AutoFilter on the header
- Per-sheet color schemes for multi-sheet workbooks

Excel downloads are raw .xlsx (no AES). PDF retains a password popover.
Filename pattern: SME_<ReportName>_<Site>_<YYYY-MM-DD>.<ext>
"""
from __future__ import annotations

import base64
import datetime
import io
import os
import re
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from .colors import COLOR_SCHEMES, scheme_for_location

SME_PDF_PASSWORD = "pdf2026"

# Logo asset (bundled in package)
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "sme_logo.png")

try:
    from reportlab.lib import colors as _rl_colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image as RLImage,
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
# Filenames
# ---------------------------------------------------------------------------

def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip()) or "file"


def sme_filename(report_name: str, site_id: str | None, ext: str) -> str:
    today = datetime.date.today().isoformat()
    if not ext.startswith("."):
        ext = "." + ext
    site_part = f"_{_safe(site_id)}" if site_id else ""
    return f"SME_{_safe(report_name)}{site_part}_{today}{ext}"


# ---------------------------------------------------------------------------
# Excel — SME format
# ---------------------------------------------------------------------------

def _write_sheet(
    workbook,
    worksheet,
    df: pd.DataFrame,
    *,
    title: str,
    color_scheme: str = "dashboard",
    add_grand_total: bool = True,
) -> None:
    """Apply SME's professional sheet layout to one worksheet.

    Layout (rows 1-indexed in Excel; 0-indexed in xlsxwriter API):
      Row 0-3: Logo image (left) + spacer
      Row 4:   Title bar — merged across df columns, colored title_bg
      Row 5:   Header row, colored header_bg, white bold
      Row 6+:  Data rows with banding
      Last:    Optional GRAND TOTAL row with total_bg / total_fg
    """
    scheme = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["dashboard"])
    ncols = max(1, len(df.columns))

    # Logo (best-effort)
    try:
        if os.path.exists(_LOGO_PATH):
            worksheet.insert_image(
                0, 0, _LOGO_PATH,
                {"x_scale": 0.32, "y_scale": 0.32, "x_offset": 4, "y_offset": 4},
            )
    except Exception:
        pass
    worksheet.set_row(0, 22)
    worksheet.set_row(1, 22)
    worksheet.set_row(2, 22)
    worksheet.set_row(3, 6)

    # Title bar
    title_fmt = workbook.add_format({
        "bold": True, "font_size": 14, "font_color": "#FFFFFF",
        "bg_color": scheme["title_bg"], "align": "center", "valign": "vcenter",
        "border": 1, "border_color": scheme["title_bg"],
    })
    if ncols >= 2:
        worksheet.merge_range(4, 0, 4, ncols - 1, title or "", title_fmt)
    else:
        worksheet.write(4, 0, title or "", title_fmt)
    worksheet.set_row(4, 28)

    # Header row
    header_fmt = workbook.add_format({
        "bold": True, "font_color": "#FFFFFF",
        "bg_color": scheme["header_bg"], "align": "center",
        "valign": "vcenter", "border": 1, "border_color": "#666666",
    })
    for c, col in enumerate(df.columns):
        worksheet.write(5, c, str(col), header_fmt)
    worksheet.set_row(5, 22)

    # Data rows
    body_fmt = workbook.add_format({
        "border": 1, "border_color": "#D1D5DB", "valign": "vcenter",
    })
    alt_fmt = workbook.add_format({
        "border": 1, "border_color": "#D1D5DB", "valign": "vcenter",
        "bg_color": "#F9FAFB",
    })
    num_fmt = workbook.add_format({
        "border": 1, "border_color": "#D1D5DB", "valign": "vcenter",
        "num_format": "#,##0.###",
    })
    num_alt_fmt = workbook.add_format({
        "border": 1, "border_color": "#D1D5DB", "valign": "vcenter",
        "num_format": "#,##0.###", "bg_color": "#F9FAFB",
    })

    for r, (_, row) in enumerate(df.iterrows(), start=6):
        banded = (r - 6) % 2 == 1
        for c, col in enumerate(df.columns):
            val = row[col]
            if pd.isna(val):
                worksheet.write(r, c, "", alt_fmt if banded else body_fmt)
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                worksheet.write_number(r, c, float(val),
                                       num_alt_fmt if banded else num_fmt)
            else:
                worksheet.write(r, c, val, alt_fmt if banded else body_fmt)

    # AutoFilter
    if len(df) > 0:
        worksheet.autofilter(5, 0, 5 + len(df), ncols - 1)

    # Optional GRAND TOTAL — only sum numeric columns
    if add_grand_total and len(df) > 0:
        total_fmt = workbook.add_format({
            "bold": True, "bg_color": scheme["total_bg"],
            "font_color": scheme["total_fg"], "border": 1,
            "border_color": "#444444", "num_format": "#,##0.###",
            "align": "right",
        })
        total_label_fmt = workbook.add_format({
            "bold": True, "bg_color": scheme["total_bg"],
            "font_color": scheme["total_fg"], "border": 1,
            "border_color": "#444444", "align": "left",
        })
        tr = 6 + len(df)
        for c, col in enumerate(df.columns):
            try:
                series = pd.to_numeric(df[col], errors="coerce")
                if series.notna().any():
                    worksheet.write_number(tr, c, float(series.sum()), total_fmt)
                else:
                    worksheet.write(tr, c, "GRAND TOTAL" if c == 0 else "",
                                    total_label_fmt)
            except Exception:
                worksheet.write(tr, c, "GRAND TOTAL" if c == 0 else "",
                                total_label_fmt)
        worksheet.set_row(tr, 22)

    # Autosize
    for c, col in enumerate(df.columns):
        try:
            width = max(
                len(str(col)) + 2,
                min(38, int(df[col].astype(str).str.len().max() or 12) + 2),
            )
        except Exception:
            width = 16
        worksheet.set_column(c, c, width)
    worksheet.freeze_panes(6, 0)


def generate_excel_report(
    df: pd.DataFrame,
    *,
    report_title: str = "",
    color_scheme: str = "dashboard",
    add_grand_total: bool = True,
    sheet_name: str = "Report",
) -> bytes:
    """Single-sheet SME-format Excel. Returns raw bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        safe = (sheet_name or "Report")[:31]
        ws = wb.add_worksheet(safe)
        writer.sheets[safe] = ws
        _write_sheet(wb, ws, df, title=report_title,
                     color_scheme=color_scheme,
                     add_grand_total=add_grand_total)
    return buf.getvalue()


def generate_multi_sheet_excel(sheets: list[dict]) -> bytes:
    """Multi-sheet workbook. Each sheet = {name, df, title?, color_scheme?,
    add_grand_total?}."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        for s in sheets:
            safe = (s.get("name") or "Sheet")[:31]
            ws = wb.add_worksheet(safe)
            writer.sheets[safe] = ws
            _write_sheet(
                wb, ws, s.get("df", pd.DataFrame()),
                title=s.get("title") or s.get("name") or "",
                color_scheme=s.get("color_scheme", "dashboard"),
                add_grand_total=s.get("add_grand_total", True),
            )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Specialized SME-format reports
# ---------------------------------------------------------------------------

def equipment_report_excel(
    *,
    location_sheets: list[dict],
    all_eq_sheet: dict | None = None,
    include_all_codes_sheet: bool = False,
    all_codes_sheet: dict | None = None,
) -> bytes:
    """Faithful port of SME's _equipment_report_excel.

    Each entry in location_sheets is {name, df, color_scheme}. all_eq_sheet
    and all_codes_sheet are optional consolidated sheets that follow the
    same {name, df} convention with the dashboard scheme.
    """
    sheets: list[dict] = []
    for s in location_sheets:
        sheets.append({
            "name": s.get("name", "Location"),
            "df":   s.get("df", pd.DataFrame()),
            "title": s.get("title") or s.get("name", "Equipment Report"),
            "color_scheme": s.get("color_scheme", "dashboard"),
            "add_grand_total": True,
        })
    if all_eq_sheet is not None:
        sheets.append({
            "name": all_eq_sheet.get("name", "All Equipment"),
            "df":   all_eq_sheet.get("df", pd.DataFrame()),
            "title": all_eq_sheet.get("title", "All Equipment"),
            "color_scheme": "dashboard",
            "add_grand_total": True,
        })
    if include_all_codes_sheet and all_codes_sheet is not None:
        sheets.append({
            "name": all_codes_sheet.get("name", "All System Codes"),
            "df":   all_codes_sheet.get("df", pd.DataFrame()),
            "title": all_codes_sheet.get("title", "All System Codes"),
            "color_scheme": "overview",
            "add_grand_total": True,
        })
    return generate_multi_sheet_excel(sheets)


def location_report_excel(sheets: list[dict]) -> bytes:
    """Faithful port of SME's _location_report_excel. Pass per-location sheets
    each with their own color_scheme (use scheme_for_location)."""
    return generate_multi_sheet_excel(sheets)


# Back-compat aliases for files not yet rewritten in R19
build_equipment_report_excel = equipment_report_excel
build_location_report_excel  = location_report_excel


# ---------------------------------------------------------------------------
# PDF (password-protected — popover preserved)
# ---------------------------------------------------------------------------

def _df_for_pdf(df: pd.DataFrame, max_cols: int = 12) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({"(empty)": [""]})
    return df.iloc[:, :max_cols] if len(df.columns) > max_cols else df


def _pdf_from_sheets(
    sheets: list[dict],
    password: str,
    *,
    site_id: str | None = None,
) -> bytes:
    if not _HAS_REPORTLAB:
        return b""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        encrypt=password,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading2"], fontSize=14,
        textColor=_rl_colors.HexColor("#1F2937"),
    )
    sub_style = ParagraphStyle(
        "sub", parent=styles["Italic"], fontSize=9,
        textColor=_rl_colors.HexColor("#6B7280"),
    )
    story: list = []
    for i, sheet in enumerate(sheets):
        if i > 0:
            story.append(PageBreak())
        # Logo top-left
        try:
            if os.path.exists(_LOGO_PATH):
                story.append(RLImage(_LOGO_PATH, width=30 * mm, height=20 * mm))
        except Exception:
            pass
        title = sheet.get("title") or sheet.get("name", "Report")
        scheme = COLOR_SCHEMES.get(
            sheet.get("color_scheme", "dashboard"), COLOR_SCHEMES["dashboard"],
        )
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
            ("BACKGROUND", (0, 0), (-1, 0),
             _rl_colors.HexColor(scheme["header_bg"])),
            ("TEXTCOLOR", (0, 0), (-1, 0), _rl_colors.HexColor("#FFFFFF")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, _rl_colors.HexColor("#9CA3AF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue()


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
    color_scheme: str = "dashboard",
    add_grand_total: bool = True,
    title: str | None = None,
    sheet_name: str = "Report",
    use_container_width: bool = True,
) -> None:
    """Raw .xlsx single-sheet download with SME-format styling."""
    payload = generate_excel_report(
        df,
        report_title=title or report_name.replace("_", " "),
        color_scheme=color_scheme,
        add_grand_total=add_grand_total,
        sheet_name=sheet_name,
    )
    st.download_button(
        label, data=payload,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_x_{key}",
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
    payload = generate_multi_sheet_excel(sheets)
    st.download_button(
        label, data=payload,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_xm_{key}",
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
    st.download_button(
        label, data=payload_bytes,
        file_name=sme_filename(report_name, site_id, "xlsx"),
        mime=_XLSX_MIME,
        key=f"_sme_xc_{key}",
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
    color_scheme: str = "dashboard",
    site_id: str | None = None,
    use_container_width: bool = True,
) -> None:
    """PDF still password-gated via popover."""
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
                    payload = _pdf_from_sheets(
                        [{"name": title, "df": df, "title": title,
                          "color_scheme": color_scheme}],
                        SME_PDF_PASSWORD, site_id=site_id,
                    )
                _trigger_browser_download(
                    payload, sme_filename(report_name, site_id, "pdf"),
                    "application/pdf",
                )
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
    color_scheme: str = "dashboard",
    add_grand_total: bool = True,
    sheet_name: str | None = None,
) -> None:
    c_xlsx, c_pdf = st.columns(2)
    with c_xlsx:
        sme_xlsx_download(
            f"⬇ Excel — {title or report_name}",
            df, report_name=report_name, site_id=site_id,
            key=f"{key}_x", color_scheme=color_scheme,
            add_grand_total=add_grand_total,
            title=title, sheet_name=sheet_name or report_name,
        )
    with c_pdf:
        sme_pdf_download(
            f"⬇ PDF — {title or report_name}",
            df=df, report_name=report_name, site_id=site_id,
            color_scheme=color_scheme,
            title=title or report_name, key=f"{key}_p",
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
            sheets, report_name=report_name, site_id=site_id,
            key=f"{key}_x",
        )
    with c_pdf:
        sme_pdf_download(
            f"⬇ PDF — {title or report_name}",
            sheets=sheets, report_name=report_name, site_id=site_id,
            title=title or report_name, key=f"{key}_p",
        )
