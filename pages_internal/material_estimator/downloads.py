"""
downloads.py — Standalone secure-download helpers for Material Estimator

Replaces SME's monkey-patched st.download_button. These are namespaced
functions (sme_secure_xlsx_download / sme_secure_pdf_download) — they do
NOT touch the global Streamlit namespace and are safe to import from any
page in the multi-page app.

Same protection model as the legacy SME app:
- Password popover before download fires.
- Excel bytes wrapped in AES-encrypted .protected.zip (pyzipper).
- PDF bytes generated with ReportLab encrypt=password.
- Filename convention: <stem>_<username>_<YYYY-MM-DD>.<ext>

Constants are hardcoded at module top — intentional, matches legacy
behaviour. Rotate by editing these constants.
"""
from __future__ import annotations

import base64
import datetime
import io
import re
import uuid
from typing import Iterable

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ── Download passwords (rotate by editing) ──────────────────────────────────
SME_XLSX_PASSWORD = "excel2026"
SME_PDF_PASSWORD  = "pdf2026"

# Optional encryption deps — gate gracefully if missing
try:
    import pyzipper
    _HAS_PYZIPPER = True
except ImportError:
    _HAS_PYZIPPER = False

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


def _standard_filename(stem: str, ext: str, username: str | None) -> str:
    today = datetime.date.today().isoformat()
    user_part = f"_{_safe(username)}" if username else ""
    if not ext.startswith("."):
        ext = "." + ext
    return f"{_safe(stem)}{user_part}_{today}{ext}"


# ---------------------------------------------------------------------------
# Encryption wrappers
# ---------------------------------------------------------------------------

def _encrypt_xlsx_bytes(raw: bytes, password: str) -> bytes:
    """Wrap raw .xlsx bytes in an AES-encrypted .zip via pyzipper.
    Inner filename is always 'report.xlsx' (legacy SME convention)."""
    if not _HAS_PYZIPPER:
        return raw  # graceful degrade — plain xlsx if pyzipper missing
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr("report.xlsx", raw)
    return buf.getvalue()


def _df_for_pdf(df: pd.DataFrame, max_cols: int = 12) -> pd.DataFrame:
    """Trim a wide DataFrame to a printable column count for ReportLab."""
    if df is None or df.empty:
        return pd.DataFrame({"(empty)": [""]})
    if len(df.columns) <= max_cols:
        return df
    return df.iloc[:, :max_cols]


def _pdf_from_sheets(
    sheets: list[dict],
    password: str,
    landscape_orient: bool = True,
) -> bytes:
    """Build a multi-sheet landscape A4 PDF via ReportLab with encrypt=pwd.

    sheets = [{name: str, df: DataFrame, title: str}, ...]
    """
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
        fontSize=14, textColor=_rl_colors.HexColor("#1F2937"),
    )
    story = []
    for i, sheet in enumerate(sheets):
        if i > 0:
            story.append(PageBreak())
        title = sheet.get("title") or sheet.get("name", "Report")
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 4 * mm))
        df = _df_for_pdf(sheet.get("df"))
        data = [list(df.columns)] + df.astype(str).values.tolist()
        tbl = Table(data, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _rl_colors.HexColor("#FCD34D")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), _rl_colors.HexColor("#111827")),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",   (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.25, _rl_colors.HexColor("#9CA3AF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue()


def _pdf_from_df(df: pd.DataFrame, title: str, password: str) -> bytes:
    return _pdf_from_sheets(
        [{"name": title, "df": df, "title": title}], password,
    )


# ---------------------------------------------------------------------------
# Browser auto-download trigger
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
# Excel + PDF builders
# ---------------------------------------------------------------------------

def build_excel_bytes(
    df: pd.DataFrame,
    sheet_name: str = "Report",
) -> bytes:
    """Plain single-sheet xlsx via xlsxwriter."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        safe_name = (sheet_name or "Report")[:31]
        df.to_excel(writer, sheet_name=safe_name, index=False)
        wb = writer.book
        ws = writer.sheets[safe_name]
        header_fmt = wb.add_format({
            "bold": True, "bg_color": "#FCD34D", "border": 1,
            "align": "left", "valign": "vcenter",
        })
        for col_idx, col_name in enumerate(df.columns):
            ws.write(0, col_idx, col_name, header_fmt)
            try:
                width = max(12, min(40, int(df[col_name].astype(str).str.len().max() or 12) + 2))
            except Exception:
                width = 16
            ws.set_column(col_idx, col_idx, width)
        ws.freeze_panes(1, 0)
    return buf.getvalue()


def build_multi_sheet_excel(sheets: list[dict]) -> bytes:
    """sheets = [{name, df}, ...] → bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        header_fmt = wb.add_format({
            "bold": True, "bg_color": "#FCD34D", "border": 1,
        })
        for s in sheets:
            name = (s.get("name") or "Sheet")[:31]
            df = s["df"]
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            for col_idx, col_name in enumerate(df.columns):
                ws.write(0, col_idx, col_name, header_fmt)
                ws.set_column(col_idx, col_idx, 18)
            ws.freeze_panes(1, 0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public download widgets — popover + password gate
# ---------------------------------------------------------------------------

def sme_secure_xlsx_download(
    label: str,
    df: pd.DataFrame,
    *,
    file_stem: str,
    key: str,
    username: str | None = None,
    sheet_name: str = "Report",
    use_container_width: bool = True,
) -> None:
    """Render an Excel download as a password-gated popover.

    Plain (unencrypted) xlsx if pyzipper isn't installed. Encrypted
    .protected.zip wrapper otherwise. Filename is rewritten to the standard
    <stem>_<user>_<date>.xlsx (or .protected.zip)."""
    with st.popover(label, use_container_width=use_container_width):
        st.caption("🔐 Password protected. Enter the Excel password.")
        pwd_key = f"_sme_xlsx_pwd_{key}"
        fired_key = f"_sme_xlsx_fired_{key}"
        pwd = st.text_input(
            "Password", type="password", key=pwd_key,
            label_visibility="collapsed",
        )
        if pwd and pwd == SME_XLSX_PASSWORD:
            if not st.session_state.get(fired_key):
                raw = build_excel_bytes(df, sheet_name=sheet_name)
                if _HAS_PYZIPPER:
                    payload = _encrypt_xlsx_bytes(raw, SME_XLSX_PASSWORD)
                    fname = _standard_filename(
                        file_stem, "protected.zip", username,
                    )
                    mime = "application/zip"
                else:
                    payload = raw
                    fname = _standard_filename(file_stem, "xlsx", username)
                    mime = (
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    )
                _trigger_browser_download(payload, fname, mime)
                st.session_state[fired_key] = True
            st.success("✓ Download started")
            if st.button("↻ Download again", key=f"_sme_xlsx_again_{key}"):
                st.session_state[fired_key] = False
                st.rerun()
        elif pwd:
            st.error("Wrong password.")


def sme_secure_multi_sheet_xlsx_download(
    label: str,
    sheets: list[dict],
    *,
    file_stem: str,
    key: str,
    username: str | None = None,
    use_container_width: bool = True,
) -> None:
    """Multi-sheet variant. sheets = [{name, df}, ...]."""
    with st.popover(label, use_container_width=use_container_width):
        st.caption("🔐 Password protected. Enter the Excel password.")
        pwd_key = f"_sme_xlsx_pwd_{key}"
        fired_key = f"_sme_xlsx_fired_{key}"
        pwd = st.text_input(
            "Password", type="password", key=pwd_key,
            label_visibility="collapsed",
        )
        if pwd and pwd == SME_XLSX_PASSWORD:
            if not st.session_state.get(fired_key):
                raw = build_multi_sheet_excel(sheets)
                if _HAS_PYZIPPER:
                    payload = _encrypt_xlsx_bytes(raw, SME_XLSX_PASSWORD)
                    fname = _standard_filename(
                        file_stem, "protected.zip", username,
                    )
                    mime = "application/zip"
                else:
                    payload = raw
                    fname = _standard_filename(file_stem, "xlsx", username)
                    mime = (
                        "application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"
                    )
                _trigger_browser_download(payload, fname, mime)
                st.session_state[fired_key] = True
            st.success("✓ Download started")
            if st.button("↻ Download again", key=f"_sme_xlsx_again_{key}"):
                st.session_state[fired_key] = False
                st.rerun()
        elif pwd:
            st.error("Wrong password.")


def sme_secure_pdf_download(
    label: str,
    *,
    file_stem: str,
    key: str,
    df: pd.DataFrame | None = None,
    sheets: list[dict] | None = None,
    title: str = "Report",
    username: str | None = None,
    use_container_width: bool = True,
) -> None:
    """Pass `df=` for single-sheet PDFs or `sheets=` for multi-sheet."""
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
                    payload = _pdf_from_sheets(sheets, SME_PDF_PASSWORD)
                else:
                    payload = _pdf_from_df(
                        df if df is not None else pd.DataFrame(),
                        title, SME_PDF_PASSWORD,
                    )
                fname = _standard_filename(file_stem, "pdf", username)
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
    file_stem: str,
    title: str,
    key: str,
    username: str | None = None,
    sheet_name: str | None = None,
) -> None:
    """Convenience: render Excel + PDF buttons side by side."""
    c_xlsx, c_pdf = st.columns(2)
    with c_xlsx:
        sme_secure_xlsx_download(
            f"⬇ Excel — {title}",
            df,
            file_stem=file_stem,
            key=f"{key}_xlsx",
            username=username,
            sheet_name=sheet_name or title,
        )
    with c_pdf:
        sme_secure_pdf_download(
            f"⬇ PDF — {title}",
            df=df,
            file_stem=file_stem,
            key=f"{key}_pdf",
            username=username,
            title=title,
        )
