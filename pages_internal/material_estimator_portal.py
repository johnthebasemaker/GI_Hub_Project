"""
Smart Material Estimator — LITERAL DROP-IN (Round 20)
=====================================================
This file is the standalone SME `app.py` (v3) ported verbatim into the ERP.
Per Round 20, the rewrite approach was reverted in favor of dropping in the
original SME code 1:1 to guarantee apple-to-apple parity (UI, KPI cards,
Plotly charts, drag sortables, CSS, dark/light theme toggle).

Surgical edits applied to the original (search '# R20 EDIT' to find them):
  - Module-level `st.set_page_config(...)`           → commented out
  - Module-level `st.download_button = ...` patch    → scoped inside fn
  - `_show_login()` + auth gate                      → deleted
  - `load_all()`                                     → rewritten to call ERP
                                                       helpers (R17/R18 data
                                                       layer) with column
                                                       aliasing to match
                                                       SME's exact casing
  - Tab 6 `📦 Inventory` block                       → deleted (R18 wired
                                                       the consumption flow
                                                       into the ERP's SK
                                                       daily_issue_log.py)
  - Locations / Types CRUD in Master Data            → routed through
                                                       D.add_sme_setting /
                                                       D.delete_sme_setting
  - SME's `consumption_log` references in            → mapped to R18's
    Consumption Comparison sub-view                    sme_consumption_log
  - All module-level imperative rendering            → wrapped in
                                                       page_material_estimator
                                                       (so it runs on tab
                                                       click, not on import)
Everything else — the entire <style> block, _apply_theme_attr(),
dbl_click_metric, plotly_mat_table, cascade_allocate, the per-tab
rendering — is preserved exactly as the SME wrote it.
"""
import io, os, sys, sqlite3, base64
from PIL import Image as _PILImage
from datetime import date, datetime
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st

# R20 EDIT: removed `sys.path.insert + from validate_data import ... + from
# allocation_engine import build_demand_matrix`. The SME standalone needed
# those for the Excel-fallback path; in the ERP we read straight from the
# database helpers below.
import database as D
# NOTE: the legacy `from pages_internal.material_estimator_engine import
# build_demand_matrix` was REMOVED here. It was unused (F401) yet it re-entered
# the half-initialized `pages_internal` package during __init__, which under
# Streamlit's loader intermittently broke
# `from pages_internal import page_material_estimator` (the cold-start ImportError).
# If the engine helper is ever needed, import it lazily inside the function:
#   from pages_internal.material_estimator_engine import build_demand_matrix

# ── Optional libs for encrypted downloads (Phase 4) ──────────────────────────
try:
    import pyzipper  # AES-encrypted ZIP wrapper for .xlsx downloads
    _HAS_PYZIPPER = True
except Exception:
    _HAS_PYZIPPER = False
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors as _rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    )
    from reportlab.lib.units import mm
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False


# ── Download encryption passwords ────────────────────────────────────────────
# Hardcoded per project decision: separate passwords for Excel-ZIP and PDF.
# Change these values to rotate the protection for all downloaded reports.
_XLSX_PASSWORD = "excel2026"
_PDF_PASSWORD  = "pdf2026"


def _xlsx_password() -> str:
    return _XLSX_PASSWORD


def _pdf_password() -> str:
    return _PDF_PASSWORD


def _current_username() -> str:
    """Username captured at login time. Falls back to 'user'."""
    return str(st.session_state.get("_login_username", "") or "user").strip() or "user"


def _safe_for_filename(text: str) -> str:
    """Strip path-unfriendly characters from a string for use in a filename."""
    if not text:
        return ""
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(text))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _standard_filename(stem: str, ext: str) -> str:
    """Apply the project filename convention:
        <ReportName>_<username>_<YYYY-MM-DD>.<ext>
    `stem` is the report-name portion. `ext` is without the leading dot.
    """
    base = _safe_for_filename(stem) or "report"
    user = _safe_for_filename(_current_username())
    ts   = date.today().strftime("%Y-%m-%d")
    return f"{base}_{user}_{ts}.{ext.lstrip('.')}"


def _rename_to_standard(file_name: str | None, fallback_stem: str | None = None) -> str | None:
    """Rewrite a download file_name to the standard convention while preserving the extension."""
    if not file_name:
        if fallback_stem:
            return _standard_filename(fallback_stem, "bin")
        return file_name
    name = str(file_name)
    if "." in name:
        stem, ext = name.rsplit(".", 1)
    else:
        stem, ext = name, "bin"
    return _standard_filename(stem, ext)


def _encrypt_xlsx_bytes(raw: bytes, password: str,
                         inner_name: str = "report.xlsx") -> bytes | None:
    """Return AES-encrypted ZIP bytes containing the raw .xlsx, or None when
    the encryption lib isn't available. ZIP wrapping is used because neither
    xlsxwriter nor openpyxl support write-side .xlsx password encryption.

    `inner_name` controls the filename stored inside the zip — when the user
    extracts the archive they get the .xlsx with this name (not the generic
    "report.xlsx")."""
    if not _HAS_PYZIPPER or not password:
        return None
    if not inner_name.lower().endswith((".xlsx", ".xlsm")):
        inner_name = inner_name.rsplit(".", 1)[0] + ".xlsx"
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w",
                              compression=pyzipper.ZIP_DEFLATED,
                              encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.writestr(inner_name, raw)
    return buf.getvalue()


def _df_for_pdf(df: pd.DataFrame, max_cols: int = 12) -> pd.DataFrame:
    """Trim a DataFrame for PDF rendering — drop interactive columns and limit width."""
    out = df.copy()
    for c in ("☐ Select", "☐ Del", "Sl. No.", "S.No"):
        if c in out.columns:
            out = out.drop(columns=[c])
    if len(out.columns) > max_cols:
        out = out.iloc[:, :max_cols]
    return out


def _pdf_from_sheets(sheets: list, password: str) -> bytes:
    """Build a multi-page password-protected PDF. sheets = list of
    {name, df, title, color_scheme(optional)} like generate_multi_sheet_excel."""
    if not _HAS_REPORTLAB:
        return b""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm, bottomMargin=12*mm,
        encrypt=password if password else None,
        title="Smart Material Estimator Report",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"],
        fontSize=14, textColor=_rl_colors.HexColor("#0F172A"),
        alignment=1, spaceAfter=8,
    )
    sub_style = ParagraphStyle(
        "Sub", parent=styles["Normal"],
        fontSize=8, textColor=_rl_colors.HexColor("#64748B"),
        alignment=1, spaceAfter=10,
    )
    story = []
    for i, spec in enumerate(sheets):
        df = _df_for_pdf(spec["df"])
        title = spec.get("title", spec.get("name", "Report"))
        story.append(Paragraph(title, title_style))
        story.append(Paragraph(
            f"Generated {date.today().isoformat()} · Smart Material Estimator",
            sub_style,
        ))
        if len(df) == 0:
            story.append(Paragraph("(no rows)", styles["Normal"]))
        else:
            # Convert to strings for the table
            data = [list(df.columns)]
            for _, row in df.iterrows():
                data.append([
                    f"{v:,.3f}" if isinstance(v, float)
                    else ("" if pd.isna(v) else str(v))
                    for v in row.tolist()
                ])
            tbl = Table(data, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), _rl_colors.HexColor("#0F172A")),
                ("TEXTCOLOR",  (0,0), (-1,0), _rl_colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 7),
                ("GRID",       (0,0), (-1,-1), 0.25, _rl_colors.HexColor("#CBD5E1")),
                ("ALIGN",      (0,0), (-1,-1), "LEFT"),
                ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0,1), (-1,-1),
                    [_rl_colors.white, _rl_colors.HexColor("#F8FAFC")]),
            ]))
            story.append(tbl)
        if i < len(sheets) - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def _pdf_from_df(df: pd.DataFrame, title: str, password: str) -> bytes:
    return _pdf_from_sheets([{"name": title[:31], "df": df, "title": title}], password)


# ── AUTO-DOWNLOAD HELPER ─────────────────────────────────────────────────────
# After password verification, fire the file to the browser without requiring
# the user to click a second download button. Streamlit cannot programmatically
# invoke `st.download_button`, so we inject a hidden <a download> into the
# parent document and click() it from a tiny components.html iframe. The
# user-gesture context (typing the password) carries through, so modern
# browsers allow the download.
def _trigger_browser_download(file_bytes: bytes, file_name: str, mime: str) -> None:
    try:
        import streamlit.components.v1 as _comp
    except Exception:
        return
    b64 = base64.b64encode(bytes(file_bytes)).decode()
    safe_name = str(file_name).replace("'", "").replace('"', "").replace("\\", "")
    safe_mime = str(mime or "application/octet-stream")
    html = f"""
<script>
(function() {{
    try {{
        var doc = (window.parent && window.parent.document)
            ? window.parent.document : document;
        var a = doc.createElement('a');
        a.href = 'data:{safe_mime};base64,{b64}';
        a.download = '{safe_name}';
        a.style.display = 'none';
        doc.body.appendChild(a);
        a.click();
        setTimeout(function() {{
            try {{ doc.body.removeChild(a); }} catch(_e){{}}
        }}, 1500);
    }} catch(e) {{ console.error('auto-download failed:', e); }}
}})();
</script>
"""
    _comp.html(html, height=0, width=0)


# Monkey-patch st.download_button so every .xlsx download is wrapped in a
# password-protected AES-ZIP using the session download password. This adds
# call-site-free encryption for the 20+ existing Excel download buttons.
_orig_download_button = st.download_button
def _secure_download_button(label, data=None, file_name=None, mime=None,
                            key=None, help=None, on_click=None, args=None,
                            kwargs=None, *, type="secondary",
                            disabled=False, use_container_width=False,
                            icon=None, **extra):
    # **extra forwards any newer Streamlit kwargs (e.g. width="stretch") so this
    # wrapper never breaks a caller using the current API even if the patch is
    # active. When width is given, drop the deprecated use_container_width to
    # avoid passing both.
    is_xlsx = (
        isinstance(data, (bytes, bytearray))
        and file_name
        and (file_name.endswith(".xlsx") or file_name.endswith(".xlsm"))
    )

    # ── Non-Excel files: rename to standard and pass through ──
    if not is_xlsx:
        if file_name:
            file_name = _rename_to_standard(file_name)
        call_kwargs = dict(
            label=label, data=data, file_name=file_name, mime=mime,
            key=key, help=help, on_click=on_click, args=args, kwargs=kwargs,
            type=type, disabled=disabled,
        )
        if "width" not in extra:
            call_kwargs["use_container_width"] = use_container_width
        if icon is not None:
            call_kwargs["icon"] = icon
        return _orig_download_button(**call_kwargs, **extra)

    # ── Excel: encrypt bytes, then gate the download behind a password popover.
    # The popover label looks like a normal button; on click it opens a small
    # panel with a password input. After the typed password matches
    # _XLSX_PASSWORD the file auto-downloads. (Fix F#5)
    # The inner archive name matches the project filename convention so that
    # extracting the zip yields a properly-named .xlsx (not "report.xlsx").
    _stem = file_name.rsplit(".", 1)[0]
    _inner_xlsx_name = _standard_filename(_stem, "xlsx")
    encrypted = _encrypt_xlsx_bytes(bytes(data), _xlsx_password(),
                                    inner_name=_inner_xlsx_name)
    if encrypted is not None:
        enc_data = encrypted
        enc_name = _standard_filename(_stem, "protected.zip")
        enc_mime = "application/zip"
    else:
        enc_data = data
        enc_name = _rename_to_standard(file_name)
        enc_mime = mime

    safe_key = key or _safe_for_filename(file_name)
    pwd_state_key = f"_xlsx_pwd__{safe_key}"

    try:
        _popover = st.popover(label, use_container_width=use_container_width,
                              disabled=disabled)
    except TypeError:
        # Older Streamlit signatures may not accept `disabled`.
        _popover = st.popover(label, use_container_width=use_container_width)

    with _popover:
        st.markdown(
            "<div style=\"font-family:'JetBrains Mono',monospace;font-size:.72rem;"
            "color:var(--t3);margin-bottom:.4rem;letter-spacing:.05em;\">"
            "🔐 Excel download is password-protected.</div>",
            unsafe_allow_html=True,
        )
        pwd_in = st.text_input(
            "Excel Password",
            type="password",
            key=pwd_state_key,
            placeholder="Enter password — download starts automatically",
            label_visibility="collapsed",
        )
        fired_key = f"_xlsx_dl_fired__{safe_key}"
        if pwd_in:
            if pwd_in == _XLSX_PASSWORD:
                if not st.session_state.get(fired_key):
                    st.session_state[fired_key] = True
                    _trigger_browser_download(enc_data, enc_name, enc_mime)
                    st.success(f"⬇ Downloading **{enc_name}**…")
                else:
                    st.success(f"✓ Download started for **{enc_name}**.")
                    if st.button("↻ Download again", key=f"_xlsx_redl__{safe_key}"):
                        st.session_state.pop(fired_key, None)
                        st.rerun()
            else:
                st.error("❌ Incorrect password.")
# R20 EDIT: monkey-patch is now SCOPED inside page_material_estimator() via
# a try/finally so other ERP portals' downloads are not affected. The
# original SME line was:  st.download_button = _secure_download_button
_SME_SECURE_DOWNLOAD_BUTTON = _secure_download_button  # exported for the wrapper


def _pdf_download_button(label: str, *, df: pd.DataFrame | None = None,
                          sheets: list | None = None, title: str,
                          file_stem: str, key: str,
                          use_container_width: bool = False,
                          disabled: bool = False) -> None:
    """Password-gated PDF download. Opens a popover; once the entered password
    matches _PDF_PASSWORD the file is auto-downloaded (no second click)."""
    if not _HAS_REPORTLAB:
        st.caption("📄 PDF unavailable — install reportlab to enable.")
        return
    if sheets is None and df is not None:
        sheets = [{"name": title[:31], "df": df, "title": title}]
    if not sheets:
        return

    safe_key = key or _safe_for_filename(file_stem)
    pwd_state_key = f"_pdf_pwd__{safe_key}"
    fired_key     = f"_pdf_dl_fired__{safe_key}"

    try:
        _popover = st.popover(label, use_container_width=use_container_width,
                              disabled=disabled)
    except TypeError:
        _popover = st.popover(label, use_container_width=use_container_width)

    with _popover:
        st.markdown(
            "<div style=\"font-family:'JetBrains Mono',monospace;font-size:.72rem;"
            "color:var(--t3);margin-bottom:.4rem;letter-spacing:.05em;\">"
            "🔐 PDF download is password-protected.</div>",
            unsafe_allow_html=True,
        )
        pwd_in = st.text_input(
            "PDF Password",
            type="password",
            key=pwd_state_key,
            placeholder="Enter password — download starts automatically",
            label_visibility="collapsed",
        )
        if not pwd_in:
            return
        if pwd_in != _PDF_PASSWORD:
            st.error("❌ Incorrect password.")
            return

        out_name = _standard_filename(file_stem, "pdf")
        if not st.session_state.get(fired_key):
            try:
                pdf_bytes = _pdf_from_sheets(sheets, _pdf_password())
            except Exception as _e:
                st.error(f"📄 PDF generation failed: {_e}")
                return
            st.session_state[fired_key] = True
            _trigger_browser_download(pdf_bytes, out_name, "application/pdf")
            st.success(f"⬇ Downloading **{out_name}**…")
        else:
            st.success(f"✓ Download started for **{out_name}**.")
            if st.button("↻ Download again", key=f"_pdf_redl__{safe_key}"):
                st.session_state.pop(fired_key, None)
                st.rerun()

# ── Page config ───────────────────────────────────────────────────────────────
# R20 EDIT: ERP's main.py already calls st.set_page_config() before this
# module is imported. Calling it again would raise. Original SME line:
#   st.set_page_config(page_title="Smart Material Estimator & Planner",
#                      page_icon="", layout="wide",
#                      initial_sidebar_state="expanded")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_A   = os.path.join(BASE_DIR, "Materials_DetailsAvailable_Qty.xlsx")
PATH_B   = os.path.join(BASE_DIR, "For_1_SQM.xlsx")
PATH_C   = os.path.join(BASE_DIR, "Equipment.xlsx")
SHEET_A, SHEET_B, SHEET_C = "Materials", "LINING SYSTEM MATERIAL CONSM", "Data Input"
LOCATION_ORDER = ["Brown Field", "TRAIN J", "TRAIN K"]
TYPE_ORDER     = []  # Loaded from `types` table at startup. Falls back to
                     # distinct equipment.type values when the table is empty.
DB_PATH = os.path.join(BASE_DIR, "sme_database.db")
# R20.2 EDIT: literal SME drop-in shipped sme_logo.png (renamed during
# Round 17 bootstrap to avoid a name collision). The original SME's
# constant was 'logo.png'; we point at the bundled file. If the file
# is missing the helpers below return empty strings — which is OK for
# the sidebar `st.image` call but BREAKS the sticky-header markdown
# (an empty interpolation leaves a blank line that closes the HTML
# block, then the next <div> becomes a code block). The sticky-header
# call below now uses a fallback comment string to keep the HTML
# block contiguous even when no logo file is present.
LOGO_PATH = os.path.join(BASE_DIR, "sme_logo.png")

@st.cache_data(show_spinner=False)
def _logo_b64() -> str:
    if os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""

# ─────────────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,300;0,14..32,400;0,14..32,500;0,14..32,600;0,14..32,700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* ── DESIGN TOKENS ── */
:root {
  --bg0: var(--background-color);
  --bg1: var(--secondary-background-color);
  --bg2: var(--secondary-background-color);
  --bg3: var(--background-color);
  --bg4: var(--secondary-background-color);
  --border:  rgba(128,128,128,.18);
  --border2: rgba(128,128,128,.28);

  --amber:      #F59E0B;
  --amber2:     #FCD34D;
  --amber3:     #D97706;
  --amber-bg:   rgba(245,158,11,.11);
  --amber-glow: rgba(245,158,11,.22);
  --green:      #10B981;
  --green-bg:   rgba(16,185,129,.11);
  --green-glow: rgba(16,185,129,.18);
  --red:        #EF4444;
  --red-bg:     rgba(239,68,68,.11);
  --orange:     #F97316;
  --orange-bg:  rgba(249,115,22,.11);
  --yellow:     #EAB308;
  --yellow-bg:  rgba(234,179,8,.11);
  --blue:       #3B82F6;
  --blue-bg:    rgba(59,130,246,.11);
  --blue-glow:  rgba(59,130,246,.18);

  --t0: var(--text-color);
  --t1: var(--text-color);
  --t2: color-mix(in srgb, var(--text-color) 82%, transparent);
  --t3: color-mix(in srgb, var(--text-color) 62%, transparent);
  --t4: color-mix(in srgb, var(--text-color) 45%, transparent);
  --t5: color-mix(in srgb, var(--text-color) 30%, transparent);

  --r-sm: 6px;
  --r-md: 10px;
  --r-lg: 14px;
}

/* ── CUSTOM SCROLLBAR ── */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border2); border-radius:99px; }
::-webkit-scrollbar-thumb:hover { background:var(--amber); }

/* ── BASE ── */
html,body,[class*="css"] { font-family:'Inter',sans-serif!important; background:var(--bg0)!important; color:var(--t1); }
.main .block-container { padding-bottom:1.5rem; margin-top:0!important; max-width:1500px; }

/* ── SIDEBAR ── */
[data-testid="stSidebar"] { background:var(--bg1)!important; border-right:none!important; box-shadow:4px 0 24px rgba(0,0,0,.18)!important; border-radius:0 var(--r-lg) var(--r-lg) 0!important; }
[data-testid="stSidebar"] * { font-family:'Inter',sans-serif!important; }
[data-testid="stSidebar"]::before {
  content:''; display:block; height:3px;
  background:linear-gradient(90deg,var(--amber3) 0%,var(--amber) 50%,transparent 100%);
  position:sticky; top:0; z-index:1;
}

/* ── HEADER CHROME ── */
header[data-testid="stHeader"] { height:0!important; min-height:0!important; padding:0!important; background:transparent!important; overflow:visible!important; }
[data-testid="collapsedControl"] { display:flex!important; visibility:visible!important; opacity:1!important; position:fixed!important; top:.45rem!important; left:.5rem!important; z-index:10001!important; }
[data-testid="stAppViewContainer"] { padding-top:0!important; }

/* ── MAIN CONTAINER TOP PADDING (clears fixed sticky header) ── */
[data-testid="stAppViewBlockContainer"],
[data-testid="stMainBlockContainer"],
section.main > div.block-container { padding-top: 78px !important; }

/* ── HAMBURGER ICON (shows when sidebar is COLLAPSED — sits in sticky header, top-left) ── */
/* Covers BOTH old (`collapsedControl`) and new (`stSidebarCollapsedControl`) Streamlit testids */
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stSidebarCollapseButton"]:not([data-testid="stSidebar"] *) {
  position: fixed !important;
  top: .9rem !important; left: 1rem !important;
  z-index: 1000001 !important;
  display: flex !important; visibility: visible !important; opacity: 1 !important;
  width: 38px !important; height: 38px !important;
}
[data-testid="collapsedControl"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebarCollapsedControl"] > button {
  background: var(--bg1) !important;
  border: 1px solid var(--amber) !important;
  border-radius: var(--r-sm) !important;
  width: 38px !important; height: 38px !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  cursor: pointer !important; transition: all .12s !important;
  box-shadow: 0 0 0 1px var(--amber), 0 2px 10px var(--amber-glow) !important;
  padding: 0 !important;
  position: relative !important;
  font-size: 0 !important;
  color: transparent !important;
  overflow: hidden !important;
}
/* Nuke every native icon/text node inside (svg, span, "keyboard_double_arrow_right" text, etc.) */
[data-testid="collapsedControl"] button *,
[data-testid="stSidebarCollapsedControl"] button *,
[data-testid="stSidebarCollapsedControl"] > button * {
  display: none !important;
  visibility: hidden !important;
  font-size: 0 !important;
  width: 0 !important; height: 0 !important;
  opacity: 0 !important;
}
[data-testid="collapsedControl"] button::after,
[data-testid="stSidebarCollapsedControl"] button::after,
[data-testid="stSidebarCollapsedControl"] > button::after {
  content: '\2630' !important;             /* ☰ — Unicode trigram */
  font-family: 'Inter', 'Helvetica', sans-serif !important;
  font-size: 22px !important;
  font-weight: 700 !important;
  color: var(--amber) !important;
  line-height: 1 !important;
  display: block !important;
  visibility: visible !important;
  opacity: 1 !important;
  position: absolute !important;
  top: 50% !important; left: 50% !important;
  transform: translate(-50%, -50%) !important;
  width: auto !important; height: auto !important;
}
[data-testid="collapsedControl"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:hover {
  background: var(--amber-bg) !important;
  box-shadow: 0 0 0 1px var(--amber), 0 4px 14px var(--amber-glow) !important;
}
[data-testid="collapsedControl"] button:hover::after,
[data-testid="stSidebarCollapsedControl"] button:hover::after {
  color: var(--amber2) !important;
}

/* ── SIDEBAR COLLAPSE BUTTON (shows when sidebar is OPEN — kills "keyboard_double_arrow_left" text) ── */
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapsedControl"] button,
[data-testid="stSidebar"] button[kind="header"],
[data-testid="stSidebarHeader"] button {
  font-size: 0 !important;
  color: transparent !important;
  position: relative !important;
  background: transparent !important;
  border: none !important;
  width: 32px !important; height: 32px !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
}
[data-testid="stSidebarCollapseButton"] button > *,
[data-testid="stSidebarCollapseButton"] button svg,
[data-testid="stSidebarCollapseButton"] button span,
[data-testid="stSidebarCollapsedControl"] button > *,
[data-testid="stSidebarCollapsedControl"] button svg,
[data-testid="stSidebarCollapsedControl"] button span,
[data-testid="stSidebar"] button[kind="header"] > *,
[data-testid="stSidebar"] button[kind="header"] svg,
[data-testid="stSidebar"] button[kind="header"] span,
[data-testid="stSidebarHeader"] button > *,
[data-testid="stSidebarHeader"] button svg,
[data-testid="stSidebarHeader"] button span {
  display: none !important;
  visibility: hidden !important;
  font-size: 0 !important;
  width: 0 !important; height: 0 !important;
}
[data-testid="stSidebarCollapseButton"] button::after,
[data-testid="stSidebarCollapsedControl"] button::after,
[data-testid="stSidebar"] button[kind="header"]::after,
[data-testid="stSidebarHeader"] button::after {
  content: '✕' !important;
  font-family: 'Inter', sans-serif !important;
  font-size: 18px !important;
  font-weight: 700 !important;
  color: var(--amber) !important;
  line-height: 1 !important;
  display: block !important;
  position: absolute !important;
  top: 50% !important; left: 50% !important;
  transform: translate(-50%, -50%) !important;
}
[data-testid="stSidebarCollapseButton"] button:hover::after,
[data-testid="stSidebarCollapsedControl"] button:hover::after,
[data-testid="stSidebar"] button[kind="header"]:hover::after,
[data-testid="stSidebarHeader"] button:hover::after {
  color: var(--amber2) !important;
}

/* ── STICKY HEADER (fixed → persists across every tab while scrolling) ── */
.sticky-header-wrap {
  position: fixed !important;
  top: 0 !important;
  left: 0 !important;
  right: 0 !important;
  z-index: 999990 !important;
  background-color: var(--background-color) !important;
  width: 100%;
  padding: .55rem 1.5rem .55rem 4rem;
  padding-bottom: 10px;
  margin-bottom: 0;
  border-bottom: 1px solid rgba(128,128,128,0.2);
  box-shadow: 0 2px 24px rgba(0,0,0,.07);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
/* When sidebar is OPEN, shrink the fixed header so it doesn't overlap the sidebar */
[data-testid="stSidebar"][aria-expanded="true"] ~ section .sticky-header-wrap,
[data-testid="stAppViewContainer"]:has([data-testid="stSidebar"][aria-expanded="true"]) .sticky-header-wrap {
  left: var(--sidebar-width, 244px) !important;
}
.sticky-header-wrap::before {
  content:''; position:absolute; top:0; left:0; right:0; height:2px;
  background:linear-gradient(90deg,var(--amber3) 0%,var(--amber) 40%,var(--amber2) 70%,transparent 100%);
}

/* ── OVERFLOW UNLOCK: only on intermediate containers that wrap sticky
   elements. Do NOT touch [data-testid="stAppViewContainer"] or section.main —
   those are the document's scroll root; setting them to overflow:visible
   freezes the page (no scroll possible). ── */
[data-testid="stAppViewBlockContainer"],
[data-testid="stMainBlockContainer"],
[data-testid="stTabs"],
[data-testid="stTabs"] > div,
[data-baseweb="tab-panel"],
[role="tabpanel"],
[data-testid="stTabsPanel"] {
  overflow: visible !important;
}

/* ── TABS (stick just under the fixed header) — multiple selectors so
       Streamlit version drift doesn't break the pin. ── */
[data-testid="stTabs"] > div:first-of-type,
[data-testid="stTabs"] > div[data-baseweb="tab-list"],
[data-testid="stTabs"] [data-baseweb="tab-list"] {
  position: sticky !important; top: 76px !important; z-index: 999990 !important;
  background: var(--bg0) !important;
  padding: .35rem .3rem;
  border-bottom: 1px solid var(--border);
  box-shadow: 0 2px 12px rgba(0,0,0,0.06);
}

/* ── STICKY SUB-VIEW RADIO (the first horizontal stRadio inside each tab) ── */
/* Uses :has() so only tabs whose first element is a radio get the sticky band.
   Sits just below the title (76px) + tabs strip (~45px) = ~122px. */
[data-baseweb="tab-panel"] [data-testid="stVerticalBlock"]
  > [data-testid="stElementContainer"]:first-of-type:has([data-testid="stRadio"]),
[data-baseweb="tab-panel"] [data-testid="stVerticalBlock"]
  > [data-testid="element-container"]:first-of-type:has([data-testid="stRadio"]),
[role="tabpanel"] [data-testid="stVerticalBlock"]
  > [data-testid="stElementContainer"]:first-of-type:has([data-testid="stRadio"]) {
  position: sticky !important;
  top: 122px !important;
  z-index: 999970 !important;
  background: var(--bg0) !important;
  padding: .45rem .25rem !important;
  border-bottom: 1px solid var(--border) !important;
  margin-bottom: .25rem !important;
  box-shadow: 0 2px 12px rgba(0,0,0,0.04);
}
[data-testid="stTabs"] [data-baseweb="tab-list"] { background:transparent; border-bottom:none; gap:.1rem; padding:0; }
[data-testid="stTabs"] [data-baseweb="tab"] {
  font-family:'JetBrains Mono',monospace!important;
  font-size:.67rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
  color:var(--t4)!important;
  padding:.5rem 1.1rem;
  border-radius:var(--r-sm);
  border-bottom:2px solid transparent;
  transition:all .18s;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover { color:var(--t2)!important; background:var(--amber-bg); }
[data-testid="stTabs"] [aria-selected="true"] { color:var(--amber)!important; border-bottom:2px solid var(--amber)!important; background:var(--amber-bg)!important; }

/* ── BUTTONS ── */
.stButton>button {
  font-family:'JetBrains Mono',monospace!important;
  font-size:.68rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase;
  background:linear-gradient(135deg,var(--amber3) 0%,var(--amber) 100%)!important;
  color:#000!important; border:none!important;
  border-radius:var(--r-sm)!important;
  padding:.5rem 1.2rem!important;
  transition:all .18s!important;
  box-shadow:0 2px 10px var(--amber-glow)!important;
}
.stButton>button:hover { background:linear-gradient(135deg,var(--amber) 0%,var(--amber2) 100%)!important; transform:translateY(-2px)!important; box-shadow:0 6px 20px var(--amber-glow)!important; }
.stButton>button:active { transform:translateY(0)!important; }

/* ── NATIVE METRICS (no float/hover animation anywhere) ── */
[data-testid="stMetric"] {
  background:var(--bg2); border:1px solid var(--border);
  border-radius:var(--r-md); padding:.9rem 1rem!important;
  cursor:default;
  position:relative; overflow:hidden;
}
[data-testid="stMetric"],
[data-testid="stMetric"] *,
[data-testid="stMetric"]:hover,
[data-testid="stMetric"]:hover *,
[data-testid="stMetric"]:focus,
[data-testid="stMetric"]:active,
[data-testid="stMetric"]:focus-within {
  transition: none !important;
  -webkit-transition: none !important;
  animation: none !important;
  -webkit-animation: none !important;
  transform: none !important;
  -webkit-transform: none !important;
  will-change: auto !important;
}
[data-testid="stMetric"]:hover,
[data-testid="stMetric"]:focus,
[data-testid="stMetric"]:active,
[data-testid="stMetric"]:focus-within {
  box-shadow: none !important;
  border-color: var(--border) !important;
  background: var(--bg2) !important;
}
[data-testid="stMetric"]::before {
  content:''; position:absolute; top:0; left:0;
  width:3px; height:100%; background:var(--amber); border-radius:99px 0 0 99px;
}
[data-testid="stMetricLabel"] { font-family:'JetBrains Mono',monospace!important; font-size:.58rem!important; letter-spacing:.13em; text-transform:uppercase; color:var(--t4)!important; padding-left:.5rem; }
[data-testid="stMetricValue"] { font-family:'JetBrains Mono',monospace!important; font-size:1.9rem!important; font-weight:700!important; color:var(--t0)!important; padding-left:.5rem; }
[data-testid="stMetricDelta"] { font-size:.72rem!important; }

/* ── ALERT BOXES ── */
[data-testid="stInfo"]    { background:var(--blue-bg)!important;   border-left:3px solid var(--blue)!important;   border-radius:var(--r-sm)!important; color:var(--t1)!important; }
[data-testid="stSuccess"] { background:var(--green-bg)!important;  border-left:3px solid var(--green)!important;  border-radius:var(--r-sm)!important; color:var(--t1)!important; }
[data-testid="stWarning"] { background:var(--amber-bg)!important;  border-left:3px solid var(--amber)!important;  border-radius:var(--r-sm)!important; color:var(--t1)!important; }
[data-testid="stError"]   { background:var(--red-bg)!important;    border-left:3px solid var(--red)!important;    border-radius:var(--r-sm)!important; color:var(--t1)!important; }

/* ── EXPANDERS ── */
[data-testid="stExpander"] { background:var(--bg2)!important; border:1px solid var(--border)!important; border-radius:var(--r-md)!important; overflow:hidden; }
[data-testid="stExpander"] summary { color:var(--t1)!important; font-weight:600; padding:.7rem 1rem!important; }
[data-testid="stExpander"] summary:hover { background:var(--amber-bg)!important; }

/* ── SELECT / INPUT ── */
[data-baseweb="select"]>div,[data-baseweb="input"]>div { background:var(--bg2)!important; border-color:var(--border2)!important; border-radius:var(--r-sm)!important; color:var(--t0)!important; }

/* ── DATAFRAME ── */
[data-testid="stDataFrame"] { border:1px solid var(--border)!important; border-radius:var(--r-md); overflow:hidden; }
[data-testid="stDataFrame"] [role="columnheader"],[data-testid="stDataFrame"] th,
[data-testid="stDataFrame"] .dvn-column-header,[data-testid="stDataFrame"] .dvn-header-row [role="cell"] {
  font-weight:700!important; color:var(--text-color)!important; background:var(--bg2)!important;
}

/* ── DIVIDERS ── */
hr { border:none!important; height:1px!important; background:linear-gradient(90deg,transparent,var(--border2),transparent)!important; margin:.8rem 0!important; }

/* ── RADIO BUTTONS ── */
[data-testid="stRadio"] label { font-family:'JetBrains Mono',monospace!important; font-size:.72rem!important; font-weight:600!important; }

/* ── CUSTOM COMPONENTS ── */

.sec-hdr {
  font-family:'JetBrains Mono',monospace; font-size:.6rem; font-weight:700;
  letter-spacing:.18em; text-transform:uppercase; color:var(--t5);
  border-bottom:1px solid var(--border); padding-bottom:.35rem; margin-bottom:.9rem;
  position:relative;
}
.sec-hdr::after { content:''; position:absolute; bottom:-1px; left:0; width:36px; height:1px; background:var(--amber); }

.card { background:var(--bg2); border:1px solid var(--border); border-radius:var(--r-md); padding:1rem 1.2rem; margin-bottom:.6rem; transition:box-shadow .2s; }
.card:hover { box-shadow:0 2px 18px rgba(0,0,0,.1); }
.card-amber { border-left:3px solid var(--amber); }
.card-green { border-left:3px solid var(--green); }
.card-blue  { border-left:3px solid var(--blue); }

.loc-badge { display:inline-flex; align-items:center; flex-shrink:0; white-space:nowrap!important; font-family:'JetBrains Mono',monospace; font-size:.62rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; padding:.2rem .7rem; border-radius:99px; }
.loc-bf { background:rgba(59,130,246,.12); color:var(--blue);  border:1px solid rgba(59,130,246,.22); }
.loc-tj { background:rgba(245,158,11,.12); color:var(--amber); border:1px solid rgba(245,158,11,.22); }
.loc-tk { background:rgba(16,185,129,.12); color:var(--green); border:1px solid rgba(16,185,129,.22); }

.pill { display:inline-flex; align-items:center; flex-shrink:0; white-space:nowrap!important; font-family:'JetBrains Mono',monospace; font-size:.68rem; font-weight:600; padding:.18rem .6rem; border-radius:99px; }
.pill-g { background:var(--green-bg); color:var(--green); border:1px solid rgba(16,185,129,.18); }
.pill-y { background:var(--yellow-bg); color:var(--yellow); border:1px solid rgba(234,179,8,.18); }
.pill-o { background:var(--orange-bg); color:var(--orange); border:1px solid rgba(249,115,22,.18); }
.pill-r { background:var(--red-bg);    color:var(--red);    border:1px solid rgba(239,68,68,.18); }

.tag-chip { display:inline-flex; align-items:center; flex-shrink:0; white-space:nowrap!important; font-family:'JetBrains Mono',monospace; font-size:.7rem; background:var(--amber-bg); color:var(--amber); border:1px solid rgba(245,158,11,.2); border-radius:var(--r-sm); padding:.15rem .55rem; margin:.12rem; }
.syscode-block { background:var(--bg3); border:1px solid var(--border2); border-radius:var(--r-md); padding:.7rem .9rem; margin:.35rem 0; }
.syscode-hdr { display:flex; align-items:center; gap:.7rem; margin-bottom:.5rem; flex-wrap:nowrap; overflow-x:auto; }
.code-badge { display:inline-flex; align-items:center; flex-shrink:0; white-space:nowrap!important; font-family:'JetBrains Mono',monospace; font-size:.68rem; font-weight:700; background:var(--amber-bg); color:var(--amber); border:1px solid rgba(245,158,11,.22); border-radius:var(--r-sm); padding:.22rem .6rem; }
.session-equip { background:var(--bg2); border:1px solid var(--border); border-radius:var(--r-md); padding:.9rem 1rem; margin-bottom:.5rem; overflow:hidden; }
.drag-handle { font-size:1rem; color:var(--t5); cursor:grab; user-select:none; padding:.2rem .4rem; }

/* ── COMPACT SORTABLE (Location Report drag-priority list) ── */
.sme-compact-sortable + div [data-testid="stIFrame"],
.sme-compact-sortable + iframe,
[class*="sortable"] {
  max-width: 460px;
}
.sme-compact-sortable + div .sortable-component,
.sme-compact-sortable ~ div .sortable-component {
  max-width: 460px;
}
.sortable-component .sortable-item,
.sortable-container .sortable-item {
  font-family: 'JetBrains Mono', monospace !important;
  font-size: .68rem !important;
  padding: .35rem .55rem !important;
  border-radius: var(--r-sm) !important;
  background: var(--bg2) !important;
  border: 1px solid var(--border) !important;
  margin-bottom: .2rem !important;
}

.grand-box {
  background:linear-gradient(135deg,var(--amber-bg) 0%,var(--bg2) 60%);
  border:1px solid var(--border2); border-left:3px solid var(--amber);
  border-radius:var(--r-lg); padding:1.2rem 1.5rem;
  position:relative; overflow:hidden;
}
.grand-box::after {
  content:''; position:absolute; top:-50px; right:-50px;
  width:140px; height:140px;
  background:radial-gradient(circle,var(--amber-glow) 0%,transparent 70%);
  pointer-events:none;
}

.status-dot-g::before { content:"●"; color:var(--green);  margin-right:.4rem; }
.status-dot-o::before { content:"●"; color:var(--orange); margin-right:.4rem; }
.status-dot-y::before { content:"●"; color:var(--yellow); margin-right:.4rem; }
.status-dot-r::before { content:"●"; color:var(--red);    margin-right:.4rem; }

/* ── POPOVER PERFORMANCE: kill open/close animations everywhere ── */
[data-baseweb="popover"],
[data-baseweb="popover"] *,
[data-testid="stPopover"],
[data-testid="stPopover"] *,
[data-testid="stPopoverBody"],
[data-baseweb="layer"] > div {
  transition: none !important;
  animation: none !important;
  animation-duration: 0s !important;
  transition-duration: 0s !important;
}

/* ── TOOLTIPS: keep them strictly below the fixed header so they never overlap it ── */
[data-baseweb="tooltip"],
[role="tooltip"],
[data-testid="stTooltipContent"],
[data-testid="stTooltipHoverTarget"] + div {
  z-index: 999985 !important;
}

/* ── KPI POPOVER BUTTONS (no float/transition — only opens on click) ── */
[data-testid="stPopover"] button {
  background:var(--bg2)!important; border:1px solid var(--border)!important;
  border-radius:var(--r-md)!important; padding:.9rem 1rem!important;
  height:auto!important; min-height:80px!important;
  text-align:left!important; white-space:pre-wrap!important;
  color:var(--t0)!important;
  position:relative!important; overflow:hidden!important;
  cursor:pointer!important;
}
[data-testid="stPopover"],
[data-testid="stPopover"] *,
[data-testid="stPopover"] button,
[data-testid="stPopover"] button *,
[data-testid="stPopover"] button:hover,
[data-testid="stPopover"] button:hover *,
[data-testid="stPopover"] button:focus,
[data-testid="stPopover"] button:active,
[data-testid="stPopover"] button:focus-within {
  transition: none !important;
  -webkit-transition: none !important;
  animation: none !important;
  -webkit-animation: none !important;
  transform: none !important;
  -webkit-transform: none !important;
  will-change: auto !important;
}
[data-testid="stPopover"] button::before {
  content:''; position:absolute; top:0; left:0;
  width:2px; height:100%; background:var(--amber); border-radius:99px 0 0 99px;
}
[data-testid="stPopover"] button:hover {
  background:var(--bg2)!important; border-color:var(--border)!important;
  box-shadow:none!important;
}
[data-testid="stPopover"] button:focus,
[data-testid="stPopover"] button:active {
  border-color:var(--amber)!important;
  box-shadow:0 0 0 1px var(--amber)!important;
}
[data-testid="stPopover"] button p { font-family:'JetBrains Mono',monospace!important; font-size:.88rem!important; color:var(--t0)!important; line-height:1.5!important; }

/* ════════════════════════════════════════════════════════════════════════
   ALIGNMENT SWEEP (Fix #8) — consistent padding, table widths, form rows
   ════════════════════════════════════════════════════════════════════════ */

/* Belt-and-braces: kill animations on the first AND last KPI in a row too. */
[data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child [data-testid="stMetric"],
[data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child [data-testid="stMetric"],
[data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child [data-testid="stPopover"] button,
[data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child [data-testid="stPopover"] button {
  transition: none !important;
  animation: none !important;
  transform: none !important;
  box-shadow: none !important;
}
[data-testid="stHorizontalBlock"] > [data-testid="column"]:first-child [data-testid="stMetric"]:hover,
[data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child [data-testid="stMetric"]:hover {
  border-color: var(--border) !important;
  background: var(--bg2) !important;
}

/* Equalise KPI column heights so adjacent cards line up. */
[data-testid="stHorizontalBlock"] > [data-testid="column"] {
  display: flex;
  flex-direction: column;
}
[data-testid="stHorizontalBlock"] > [data-testid="column"] > [data-testid="stVerticalBlock"] {
  flex: 1 1 auto;
}
[data-testid="stHorizontalBlock"] > [data-testid="column"] [data-testid="stMetric"],
[data-testid="stHorizontalBlock"] > [data-testid="column"] [data-testid="stPopover"] {
  height: 100%;
}

/* Tighten vertical rhythm — Streamlit's default block gap is too generous. */
[data-testid="stVerticalBlock"] { gap: .5rem !important; }
[data-baseweb="tab-panel"] [data-testid="stVerticalBlock"] { gap: .5rem !important; }

/* Forms: tighten input row spacing so columns line up at the same baseline. */
[data-testid="stForm"] [data-testid="stVerticalBlock"] { gap: .55rem !important; }
[data-testid="stForm"] label { margin-bottom: .15rem !important; }

/* ── UNIFORM DROPDOWN / INPUT DIMENSIONS (F#7) ──────────────────────────── */
/* Every text input, number input, date input, and select control gets a
   consistent minimum height (38px) and inner font size — without forcing a
   fixed height, which would collapse padded children (login form, custom
   widgets, etc.). Multiselect remains free to grow taller for chips. */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input {
  min-height: 38px !important;
  font-size: .82rem !important;
  line-height: 1.3 !important;
}
[data-baseweb="select"] > div,
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div {
  min-height: 38px !important;
  font-size: .82rem !important;
}
/* Don't apply size enforcement to the login card — its inputs use bespoke
   padding for the brand styling. */
.sme-login-shell .stTextInput input,
.sme-login-shell .stTextInput > div,
.sme-login-shell .stTextInput > div > div {
  height: auto !important;
  min-height: 0 !important;
}
/* Standardize input label font + spacing across all input types. */
[data-testid="stTextInput"] label,
[data-testid="stNumberInput"] label,
[data-testid="stDateInput"] label,
[data-testid="stSelectbox"] label,
[data-testid="stMultiSelect"] label {
  font-family: 'JetBrains Mono', monospace !important;
  font-size: .68rem !important;
  font-weight: 600 !important;
  letter-spacing: .04em !important;
  margin-bottom: .2rem !important;
}

/* Sub-headers — consistent top/bottom margins to avoid uneven spacing. */
.sec-hdr { margin-top: .25rem !important; }

/* Tables: full width inside their column, no horizontal scroll bleed. */
[data-testid="stDataFrame"], [data-testid="stTable"], [data-testid="stDataEditor"] {
  width: 100% !important;
}
[data-testid="stDataFrame"] > div, [data-testid="stDataEditor"] > div {
  width: 100% !important;
}

/* Expander internal padding consistent across all tabs. */
[data-testid="stExpander"] > div:nth-child(2) { padding: .85rem 1rem !important; }

/* Make the radio rows on each tab top-aligned with consistent gap. */
[data-testid="stRadio"] [role="radiogroup"] { gap: .35rem !important; flex-wrap: wrap; }

/* Equalise st.button heights when used in a flex row of action buttons. */
.stButton > button { min-height: 38px !important; }

/* Sticky-radio band — pad children so labels don't collide with the border. */
[data-baseweb="tab-panel"] [data-testid="stVerticalBlock"]
  > [data-testid="stElementContainer"]:first-of-type:has([data-testid="stRadio"])
  [data-testid="stRadio"] > div { padding: .15rem 0; }

/* ── MOBILE ── */
@media (max-width:768px) {
  .sticky-header-wrap { padding: .5rem .6rem .3rem .6rem !important; }
  [data-testid="collapsedControl"] { top: .5rem !important; left: .5rem !important; }
  [data-testid="stAppViewBlockContainer"],
  [data-testid="stMainBlockContainer"],
  section.main > div.block-container { padding-top: 70px !important; padding-left: .5rem !important; padding-right: .5rem !important; }
  [data-testid="stTabs"] > div:first-of-type { top: 64px !important; }
  [data-testid="stMetricValue"] { font-size:1.3rem!important; }
  [data-testid="stMetricLabel"] { font-size:.55rem!important; }
  .syscode-hdr { flex-wrap:wrap!important; overflow-x:visible!important; gap:.4rem!important; }
  .card { padding:.6rem .8rem!important; }
  .code-badge { font-size:.6rem!important; }
  .loc-badge { font-size:.56rem!important; }
}

/* ════════════════════════════════════════════════════════════════════════
   DESIGN INTEGRATION — appended additions (Claude design port)
   ════════════════════════════════════════════════════════════════════════ */

/* ── FROZEN TABLE HEADERS (sticky thead inside scrollable containers) ── */
[data-testid="stDataFrame"] [role="grid"] [role="row"]:first-child,
[data-testid="stDataFrame"] thead,
[data-testid="stDataFrame"] thead tr,
[data-testid="stDataFrame"] thead th {
  position: sticky !important;
  top: 0 !important;
  z-index: 5 !important;
  background: var(--bg2) !important;
}
.sme-scroll-table {
  max-height: 520px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.sme-scroll-table table { width:100%; border-collapse:collapse; font-size:.78rem; }
.sme-scroll-table thead th {
  position: sticky; top: 0; z-index: 4;
  background: var(--bg2);
  font-family:'JetBrains Mono',monospace;
  font-size:.6rem; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  color: var(--t4);
  padding:.6rem .75rem; text-align:left;
  border-bottom: 1px solid var(--border2);
  white-space:nowrap;
}
.sme-scroll-table tbody td {
  padding:.5rem .75rem;
  border-bottom: 1px solid var(--border);
  color: var(--t1);
}
.sme-scroll-table tbody tr:nth-child(even) { background: var(--bg3); }

/* ── LOGIN FORM POLISH (matches design's gradient panel) ── */
.sme-login-shell {
  min-height: 70vh;
  background: radial-gradient(ellipse at 40% 20%,
              color-mix(in srgb, var(--amber) 9%, var(--bg0)) 0%,
              var(--bg0) 70%);
  display:flex; align-items:center; justify-content:center;
}
.sme-login-card {
  width: min(420px, 92vw);
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 40px 36px;
  box-shadow: 0 30px 60px rgba(0,0,0,.45);
  position: relative; overflow: hidden;
}
/* Amber strip on top of login card removed per request. */
.sme-login-card::before { content: none !important; display: none !important; }
.sme-login-title {
  font-family:'Inter',sans-serif;
  font-size: 21px; font-weight: 800;
  color: var(--t0); letter-spacing:-.5px;
  text-align: center; margin-top: 8px;
}
.sme-login-sub {
  text-align:center; font-size:10px; color: var(--t5);
  text-transform:uppercase; letter-spacing:1.8px; margin-top:5px;
}
.sme-login-shell .stTextInput input {
  background: var(--bg0) !important;
  border: 1px solid var(--border2) !important;
  border-radius: 9px !important;
  padding: 12px 14px !important;
  color: var(--t0) !important;
  font-family:'Inter',sans-serif !important;
}
.sme-login-shell .stButton>button {
  background: linear-gradient(135deg, var(--amber3), var(--amber)) !important;
  color: #fff !important;
  border-radius: 9px !important;
  padding: 12px !important;
  font-size: 15px !important;
  font-weight: 700 !important;
  letter-spacing: .2px !important;
}

/* ── KPI POPOVER REFINEMENT (match design's amber-card on hover) ── */
[data-testid="stPopover"] button {
  background: linear-gradient(180deg, var(--bg2), var(--bg3)) !important;
}
[data-testid="stPopover"] button p:first-child {
  font-family:'JetBrains Mono',monospace !important;
  font-size:.56rem !important;
  font-weight:700 !important;
  letter-spacing:.13em !important;
  text-transform:uppercase !important;
  color: var(--t4) !important;
  margin-bottom:.35rem !important;
  line-height:1.2 !important;
}
[data-testid="stPopover"] button p:nth-child(2) {
  font-family:'JetBrains Mono',monospace !important;
  font-size: 1.45rem !important;
  font-weight: 800 !important;
  color: var(--amber) !important;
  line-height: 1.05 !important;
  margin-bottom: .25rem !important;
}
[data-testid="stPopover"] button p:nth-child(3) {
  font-family:'Inter',sans-serif !important;
  font-size:.62rem !important;
  color: var(--t5) !important;
}

/* ── SIDEBAR REFINEMENTS (location/session rows from design) ── */
[data-testid="stSidebar"] hr { margin:.6rem 0 !important; }
[data-testid="stSidebar"] .stButton>button {
  background: transparent !important;
  color: var(--t4) !important;
  border: 1px solid var(--border2) !important;
  box-shadow: none !important;
  font-size:.6rem !important;
}
[data-testid="stSidebar"] .stButton>button:hover {
  background: var(--amber-bg) !important;
  border-color: var(--amber) !important;
  color: var(--amber) !important;
  transform: none !important;
}

/* ── DESIGN SVG GAUGE + HBAR (containers) ── */
.sme-viz-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 18px;
  margin-bottom: 12px;
}
.sme-viz-card svg { display:block; width:100%; }
.sme-viz-legend {
  display:flex; gap:18px; margin-top:6px;
  font-size:10px; color: var(--t4);
  font-family:'JetBrains Mono',monospace;
}

/* ── THEME TOGGLE (explicit light mode overrides) ── */
html[data-sme-theme="light"] {
  --bg0: #F8FAFC !important;
  --bg1: #FFFFFF !important;
  --bg2: #FFFFFF !important;
  --bg3: #F1F5F9 !important;
  --bg4: #FFFFFF !important;
  --border:  rgba(15,23,42,.08) !important;
  --border2: rgba(15,23,42,.18) !important;
  --t0: #0F172A !important;
  --t1: #1E293B !important;
  --t2: rgba(15,23,42,.78) !important;
  --t3: rgba(15,23,42,.62) !important;
  --t4: rgba(15,23,42,.48) !important;
  --t5: rgba(15,23,42,.34) !important;
}
html[data-sme-theme="light"] body,
html[data-sme-theme="light"] [data-testid="stAppViewContainer"],
html[data-sme-theme="light"] .main .block-container {
  background: #F8FAFC !important;
  color: #1E293B !important;
}
html[data-sme-theme="light"] [data-testid="stSidebar"] {
  background: #FFFFFF !important;
  border-right: 1px solid rgba(15,23,42,.08) !important;
}
html[data-sme-theme="light"] .sticky-header-wrap {
  background-color: rgba(255,255,255,.92) !important;
  border-bottom: 1px solid rgba(15,23,42,.08) !important;
}
html[data-sme-theme="light"] [data-testid="stDataFrame"] thead th,
html[data-sme-theme="light"] .sme-scroll-table thead th {
  background: #F1F5F9 !important;
  color: #475569 !important;
}
html[data-sme-theme="light"] [data-testid="stPopover"] button {
  background: linear-gradient(180deg, #FFFFFF, #F8FAFC) !important;
  color: #1E293B !important;
}
html[data-sme-theme="light"] .sme-viz-card { background: #FFFFFF !important; }

/* ── FREEZE ZONE — header (78px) + tabs (~44px) + first sub-radio inside
   each tab all stick together at the top while scrolling. We target the
   first stRadio descendant of each tab panel and pin it just below the
   tabs. The tabs themselves already stick at top:78px from the existing
   STICKY HEADER rule above. ── */
[data-testid="stTabs"] [role="tabpanel"] [data-testid="stRadio"]:first-of-type,
[data-testid="stTabs"] [role="tabpanel"] [data-testid="stVerticalBlock"]:first-of-type > [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stRadio"] {
  position: sticky !important;
  top: 122px !important;          /* header 78 + tabs ≈44 */
  z-index: 999980 !important;
  background: var(--bg0) !important;
  padding: .35rem .25rem .45rem !important;
  margin: 0 -.25rem .6rem !important;
  border-bottom: 1px solid var(--border) !important;
  box-shadow: 0 4px 16px rgba(0,0,0,.08);
}
/* If a tab has no sub-radio, the next-up block (filters, sec-hdr) stays
   non-sticky — the rule above only matches existing stRadio nodes. */

@media (max-width:768px) {
  [data-testid="stTabs"] [role="tabpanel"] [data-testid="stRadio"]:first-of-type {
    top: 108px !important;
  }
}

/* ── THEME TOGGLE BUTTON STYLE (top of sidebar) ── */
.sme-theme-toggle {
  display:flex; align-items:center; justify-content:space-between;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 99px;
  padding: 4px 6px;
  margin: .2rem 0 .8rem;
}
.sme-theme-toggle button {
  flex:1; background: transparent !important;
  color: var(--t4) !important; border: none !important;
  font-family:'JetBrains Mono',monospace !important;
  font-size: .58rem !important; padding: .3rem .5rem !important;
  letter-spacing:.1em; text-transform:uppercase;
  border-radius: 99px !important;
  box-shadow:none !important;
}
.sme-theme-toggle .active {
  background: var(--amber-bg) !important;
  color: var(--amber) !important;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# ADMIN LOGIN GATE  —  change credentials here
# ─────────────────────────────────────────────────────────────────────────────
# R20 EDIT: stripped the original SME _show_login() function + the auth
# gate. ERP's main.py + auth.py handles login; we trust user[role] is
# already validated as 'hod' or 'admin' by the time this module is called.
# The original constants _ADMIN_USER/_ADMIN_PASS and the gate that called
# _show_login() + st.stop() are deleted. session_state['_authenticated']
# and ['_login_username'] are populated by page_material_estimator(user)
# at the wrapper layer below.

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    """R20 EDIT: redirected to the ERP's database connection. The SME's
    legacy SQL queries (against `locations`, `types`, `consumption_log`,
    `sqm_progress`, etc.) work because init_db() in database.py creates
    compatibility VIEWS that map these names onto the ERP-side tables
    (system_settings, sme_sqm_progress, sme_consumption_log). All writes
    against `locations` / `types` are surgically replaced below with
    calls to D.add_sme_setting / D.delete_sme_setting."""
    conn = D.get_connection()
    conn.row_factory = sqlite3.Row
    return conn

def db_available():
    """R20 EDIT: always True — the ERP DB is always present once the
    portal renders (init_db has already self-healed by the time the user
    can navigate here)."""
    return True


_DEFAULT_LOCATIONS = [
    ("Brown Field", "#3B82F6", 1),
    ("TRAIN J",     "#F59E0B", 2),
    ("TRAIN K",     "#10B981", 3),
]


def _ensure_locations_table() -> None:
    """R20 EDIT: legacy `locations` table is now a VIEW backed by
    system_settings (category='sme_location'); CREATE TABLE / INSERT here
    would fail. Seeding is owned by database.init_db (R17 helper inserts
    Brown Field / TRAIN J / TRAIN K rows for site 'HQ'). This function
    is preserved as a callable for back-compat with the rest of the SME
    code and silently no-ops."""
    return


def _refresh_location_order() -> None:
    """Reload LOCATION_ORDER in-place from DB (idempotent)."""
    if not db_available():
        return
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM locations ORDER BY sort_order, name"
        ).fetchall()
        conn.close()
    except Exception:
        return
    names = [r["name"] for r in rows] if rows else [n for n, _, _ in _DEFAULT_LOCATIONS]
    LOCATION_ORDER.clear()
    LOCATION_ORDER.extend(names)


_ensure_locations_table()
_refresh_location_order()


# ── TYPES TABLE (F#6) — mirrors Locations workflow ──────────────────────────
_DEFAULT_TYPES = [
    ("Vessel",  1),
    ("Tank",    2),
    ("Column",  3),
    ("Pipe",    4),
    ("Reactor", 5),
]
_DEFAULT_TYPE_NAMES = {n for n, _ in _DEFAULT_TYPES}


def _ensure_types_table() -> None:
    """R20 EDIT: legacy `types` table is now a VIEW backed by
    system_settings (category='sme_equipment_type'). Seeding owned by
    init_db. No-op."""
    return


def _refresh_type_order() -> None:
    """Reload TYPE_ORDER in-place from DB (idempotent)."""
    if not db_available():
        return
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT name FROM types ORDER BY sort_order, name"
        ).fetchall()
        conn.close()
    except Exception:
        return
    names = [r["name"] for r in rows] if rows else [n for n, _ in _DEFAULT_TYPES]
    TYPE_ORDER.clear()
    TYPE_ORDER.extend(names)


def _get_all_types(eq_master_df=None) -> list[str]:
    """Authoritative Type list: union of TYPE_ORDER (registered) + any types
    actually present on equipment. Preserves registered order, then appends
    extras alphabetically."""
    out = list(TYPE_ORDER)
    if eq_master_df is not None and "Type" in eq_master_df.columns:
        extras = sorted({
            str(t).strip()
            for t in eq_master_df["Type"].dropna().unique()
            if str(t).strip() and str(t).strip() not in out
        })
        out = out + extras
    return out


_ensure_types_table()
_refresh_type_order()


def _next_order_id(conn) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = f"ORD-{today}-"
    row = conn.execute(
        "SELECT order_id FROM orders_log WHERE order_id LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = int(row["order_id"].split("-")[-1]) + 1 if row else 1
    return f"{prefix}{n:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADER  (SQLite when available, fallback to Excel)
# ─────────────────────────────────────────────────────────────────────────────
# R20 EDIT: load_all() is now site-aware and pulls from ERP helpers
# instead of the SME's standalone SQLite (sme_database.db). Column
# casing + intermediate-frame shapes preserved 1:1 with the original so
# every downstream tab works untouched (this fixes the Round-19 KeyError
# on 'Lining_System_Code' — equip_sc carries it; dm carries it; the
# cached cascade allocator carries it through to alloc_df).
@st.cache_data(show_spinner="Loading project data…")
def load_all(_site_id: str):
    # 1. ERP-side reads (R17 + R18 helpers; no schema change)
    inv_raw    = D.get_sme_inventory_view(site_id=_site_id)
    eq_raw     = D.get_sme_equipment(site_id=_site_id)
    recipe_raw = D.get_sme_recipe()
    prog_raw   = D.get_sme_sqm_progress(site_id=_site_id)

    # 2. Inventory — SME engine expects these exact column names
    inv = inv_raw.rename(columns={}).copy()  # already matches: Material_Code,
                                              # Material_Name, Nature, UOM,
                                              # Available_Qty, Ordered_Qty
    inv["Available_Qty"] = pd.to_numeric(inv["Available_Qty"], errors="coerce").fillna(0)
    inv["Ordered_Qty"]   = pd.to_numeric(inv["Ordered_Qty"],   errors="coerce").fillna(0)

    # 3. Recipe — synthesize the SME-expected display columns
    recipe = recipe_raw.copy()
    if "Lining_System_Short_Name" not in recipe.columns:
        recipe["Lining_System_Short_Name"] = recipe["Lining_System_Name"].fillna("")
    if "Lining_Type" not in recipe.columns:
        recipe["Lining_Type"] = ""
    if "Material_Description" not in recipe.columns:
        recipe["Material_Description"] = recipe["Material_Name"].fillna(recipe["Material_Code"])
    recipe["Lining_System_Code"] = recipe["Lining_System_Code"].astype(str).str.strip()
    recipe["Material_Code"]      = recipe["Material_Code"].astype(str).str.strip()
    recipe["For_1_SQM"]          = pd.to_numeric(recipe["For_1_SQM"], errors="coerce").fillna(0)

    # 4. Equipment raw — every (tag × code) row with full metadata
    equip_raw_local = eq_raw.copy()
    equip_raw_local["Equipment_Tag_No."]  = equip_raw_local["Equipment_Tag_No."].astype(str).str.strip()
    equip_raw_local["Lining_System_Code"] = equip_raw_local["Lining_System_Code"].astype(str).str.strip()
    equip_raw_local["Location"] = equip_raw_local["Location"].fillna("").astype(str).str.strip()
    equip_raw_local["Type"]     = equip_raw_local["Type"].fillna("").astype(str).str.strip()
    if "Substrate" not in equip_raw_local.columns:
        equip_raw_local["Substrate"] = ""
    equip_raw_local["Substrate"] = equip_raw_local["Substrate"].fillna("").astype(str).str.strip()
    # Synthesize SME-only display fields the original UI references
    short_name_map = recipe.drop_duplicates("Lining_System_Code") \
        .set_index("Lining_System_Code")["Lining_System_Short_Name"].to_dict()
    equip_raw_local["Lining_System_Short_Name"] = (
        equip_raw_local["Lining_System_Code"].map(short_name_map).fillna("")
    )
    lining_type_map = recipe.drop_duplicates("Lining_System_Code") \
        .set_index("Lining_System_Code")["Lining_Type"].to_dict()
    equip_raw_local["Lining_Type"]    = equip_raw_local["Lining_System_Code"].map(lining_type_map).fillna("")
    equip_raw_local["Material Spec."] = ""
    equip_raw_local["Design"]         = ""
    equip_raw_local["Lining_System+"] = equip_raw_local["Lining_System_Code"]
    equip_raw_local["Name"]           = equip_raw_local["Name"].fillna("").astype(str)
    equip_raw_local["Surface_Area_SQM"] = pd.to_numeric(
        equip_raw_local["Surface_Area_SQM"], errors="coerce",
    ).fillna(0)

    # 5. SQM progress — rename R17 columns to SME's expected casing
    sqm_prog = prog_raw.rename(columns={
        "Original_SQM": "original_sqm",
        "Done_SQM":     "done_sqm",
    }).copy()
    # R18 added Done_SQM_staged — fold it into done_sqm for "in-flight" view
    if "Done_SQM_staged" in sqm_prog.columns:
        sqm_prog["done_sqm"] = (
            pd.to_numeric(sqm_prog["done_sqm"], errors="coerce").fillna(0)
            + pd.to_numeric(sqm_prog["Done_SQM_staged"], errors="coerce").fillna(0)
        )
    sqm_prog["original_sqm"] = pd.to_numeric(sqm_prog["original_sqm"], errors="coerce").fillna(0)
    sqm_prog["done_sqm"]     = pd.to_numeric(sqm_prog["done_sqm"], errors="coerce").fillna(0)
    sqm_prog["remaining_sqm"] = (sqm_prog["original_sqm"] - sqm_prog["done_sqm"]).clip(lower=0)
    sqm_prog["Equipment_Tag_No."]  = sqm_prog["Equipment_Tag_No."].astype(str).str.strip()
    sqm_prog["Lining_System_Code"] = sqm_prog["Lining_System_Code"].astype(str).str.strip()

    # 6. equip_sc — (tag × code) aggregation with progress merge
    equip_sc = (equip_raw_local
        .groupby(["Equipment_Tag_No.", "Lining_System_Code",
                  "Lining_System_Short_Name"], as_index=False)
        ["Surface_Area_SQM"].sum()
        .rename(columns={"Surface_Area_SQM": "Total_SQM_Original"}))
    equip_sc = equip_sc.merge(
        sqm_prog[["Equipment_Tag_No.", "Lining_System_Code",
                  "remaining_sqm", "done_sqm"]],
        on=["Equipment_Tag_No.", "Lining_System_Code"], how="left")
    equip_sc["remaining_sqm"] = equip_sc["remaining_sqm"].fillna(equip_sc["Total_SQM_Original"])
    equip_sc["done_sqm"]      = equip_sc["done_sqm"].fillna(0)
    equip_sc["Total_SQM"]     = equip_sc["remaining_sqm"]

    # 7. Demand matrix
    dm = equip_sc.merge(recipe, on="Lining_System_Code", suffixes=("_e", "_r"))
    # R20.1 EDIT: guard against empty merge result. If equip_sc has no rows
    # (e.g., no equipment loaded for this site yet) the merge returns an
    # empty frame which may not have `For_1_SQM` / `Total_SQM` columns —
    # downstream slice would KeyError.
    if dm.empty:
        dm = pd.DataFrame(columns=[
            "Equipment_Tag_No.", "Lining_System_Code",
            "Lining_System_Short_Name", "Total_SQM",
            "Material_Code", "Material_Name", "UOM", "Demand_Qty",
        ])
    else:
        dm["Demand_Qty"] = dm["For_1_SQM"] * dm["Total_SQM"]
        if "Lining_System_Short_Name_e" in dm.columns:
            dm = dm.rename(columns={"Lining_System_Short_Name_e": "Lining_System_Short_Name"})
            dm.drop(columns=["Lining_System_Short_Name_r"], inplace=True, errors="ignore")
        dm = dm[["Equipment_Tag_No.", "Lining_System_Code", "Lining_System_Short_Name",
                 "Total_SQM", "Material_Code", "Material_Name", "UOM", "Demand_Qty"]]

    # 8. Equipment master (one row per tag) — guard empty equip_raw
    if equip_raw_local.empty:
        eq_master = pd.DataFrame(columns=[
            "Equipment_Tag_No.", "Name", "Substrate", "Location", "Type",
            "Lining_Systems", "Lining_Type", "Material_Spec", "Design",
            "Total_SQM",
        ])
    else:
        eq_master = equip_raw_local.groupby("Equipment_Tag_No.", as_index=False).agg(
            Name          =("Name",            "first"),
            Substrate     =("Substrate",        "first"),
            Location      =("Location",        "first"),
            Type          =("Type",            "first"),
            Lining_Systems=("Lining_System+", "first"),
            Lining_Type   =("Lining_Type",     "first"),
            Material_Spec =("Material Spec.", "first"),
            Design        =("Design",          "first"),
            Total_SQM     =("Surface_Area_SQM", "sum"),
        )

    # 9. SQM reference — guard empty equip_sc
    if equip_sc.empty:
        sqm_ref = pd.DataFrame(columns=[
            "Equipment_Tag_No.", "Lining_System_Code",
            "Total_SQM", "Total_SQM_Original", "done_sqm",
        ])
    else:
        sqm_ref = equip_sc[["Equipment_Tag_No.", "Lining_System_Code",
                             "Total_SQM", "Total_SQM_Original", "done_sqm"]].drop_duplicates()

    return inv, recipe, equip_sc, dm, eq_master, sqm_ref


# R20 EDIT: removed the imperative module-level `inv, recipe, ... = load_all()`.
# That assignment now happens inside page_material_estimator(user) using
# the user's bound site_id. The cached_cascade_allocate function reads
# `dm` and `INV_POOL_INIT` from module globals which the wrapper assigns
# via `global` statements.
inv = pd.DataFrame()
recipe = pd.DataFrame()
equip_sc = pd.DataFrame()
dm = pd.DataFrame()
eq_master = pd.DataFrame()
sqm_ref = pd.DataFrame()
ALL_TAGS = []
INV_POOL_INIT = {}
INV_ORDERED_INIT = {}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def loc_badge(loc):
    cls = {"Brown Field":"loc-bf","TRAIN J":"loc-tj","TRAIN K":"loc-tk"}.get(loc,"loc-bf")
    return f'<span class="loc-badge {cls}">{loc}</span>'

def status_dot(pct):
    if pct >= 100: return "status-dot-g"
    if pct >= 90:  return "status-dot-o"
    if pct >= 80:  return "status-dot-y"
    return "status-dot-r"

def fulfil_pill(pct):
    cls = "pill-g" if pct>=100 else "pill-o" if pct>=90 else "pill-y" if pct>=80 else "pill-r"
    return f'<span class="pill {cls}">{pct:.1f}%</span>'

@st.cache_data(show_spinner=False)
def _cached_cascade_allocate(tag_order_tuple: tuple) -> pd.DataFrame:
    """Cached worker for cascade_allocate"""
    pool = dict(INV_POOL_INIT)  # mutable copy
    rows = []
    for tag in tag_order_tuple:
        tag_dm = dm[dm["Equipment_Tag_No."] == tag].copy()
        # Process system codes in numeric order for consistency
        for code in sorted(tag_dm["Lining_System_Code"].unique(), key=lambda x: int(x)):
            code_rows = tag_dm[tag_dm["Lining_System_Code"] == code]
            for _, r in code_rows.iterrows():
                mat     = r["Material_Code"]
                demand  = r["Demand_Qty"]
                before  = pool.get(mat, 0.0)
                alloc   = min(demand, before)
                short   = demand - alloc
                after   = max(0.0, before - alloc)
                pool[mat] = after
                rows.append({
                    "Equipment_Tag_No.":       tag,
                    "Lining_System_Code":      code,
                    "Lining_System_Short_Name": r["Lining_System_Short_Name"],
                    "Total_SQM":               r["Total_SQM"],
                    "Material_Code":           mat,
                    "Material_Name":           r["Material_Name"],
                    "UOM":                     r["UOM"],
                    "Demand_Qty":              round(demand, 4),
                    "Allocated_Qty":           round(alloc, 4),
                    "Shortfall_Qty":           round(short, 4),
                    "Pool_Before":             round(before, 4),
                    "Pool_After":              round(after, 4),
                })
    # R20.1 EDIT: when `rows` is empty (e.g., dm is empty, or Streamlit
    # served a stale empty cache from a prior session), `pd.DataFrame([])`
    # has NO columns at all and every downstream `.groupby(["Equipment_Tag_No.", ...])`
    # raises KeyError. Pass an explicit column list so empty results still
    # have the expected shape.
    _EXPECTED_COLS = [
        "Equipment_Tag_No.", "Lining_System_Code", "Lining_System_Short_Name",
        "Total_SQM", "Material_Code", "Material_Name", "UOM",
        "Demand_Qty", "Allocated_Qty", "Shortfall_Qty",
        "Pool_Before", "Pool_After",
    ]
    result = pd.DataFrame(rows, columns=_EXPECTED_COLS)
    if not result.empty:
        result["Fulfillment_Pct"] = (
            result["Allocated_Qty"] / result["Demand_Qty"].replace(0, np.nan) * 100
        ).fillna(100).clip(0, 100).round(2)
    else:
        result["Fulfillment_Pct"] = pd.Series(dtype=float)
    return result

def cascade_allocate(tag_order: list[str]) -> pd.DataFrame:
    """
    Cascade inventory pool through equipment in order.
    Pool is GLOBAL per material (not per system code).
    """
    # Convert list to tuple so Streamlit's cache engine can hash it safely
    return _cached_cascade_allocate(tuple(tag_order))

def tag_fulfillment(alloc_df: pd.DataFrame, tag: str) -> float:
    t = alloc_df[alloc_df["Equipment_Tag_No."] == tag]
    if t.empty: return 0.0
    d = t["Demand_Qty"].sum()
    a = t["Allocated_Qty"].sum()
    return min(100.0, a / d * 100) if d > 0 else 100.0

def syscode_fulfillment(alloc_df: pd.DataFrame, tag: str, code: str) -> float:
    t = alloc_df[(alloc_df["Equipment_Tag_No."]==tag)&(alloc_df["Lining_System_Code"]==code)]
    if t.empty: return 0.0
    d = t["Demand_Qty"].sum()
    a = t["Allocated_Qty"].sum()
    return min(100.0, a / d * 100) if d > 0 else 100.0

def sqm_can_do(alloc_df: pd.DataFrame, tag: str, code: str) -> tuple[float, float, float]:
    """
    Returns (total_sqm, sqm_can_do, sqm_shortfall) for a (tag, system_code) pair.
    Method: weighted avg fulfillment % × Total_SQM
    """
    rows = alloc_df[(alloc_df["Equipment_Tag_No."]==tag) &
                    (alloc_df["Lining_System_Code"]==code)]
    if rows.empty:
        return 0.0, 0.0, 0.0
    total_sqm = sqm_ref[
        (sqm_ref["Equipment_Tag_No."]==tag) &
        (sqm_ref["Lining_System_Code"]==code)
    ]["Total_SQM"].sum()
    d = rows["Demand_Qty"].sum()
    a = rows["Allocated_Qty"].sum()
    pct = min(1.0, a / d) if d > 0 else 1.0
    can   = round(total_sqm * pct, 2)
    short = round(total_sqm - can, 2)
    return round(total_sqm, 2), can, short


@st.dialog(" ", width="large")
def _kpi_drilldown_dialog() -> None:
    """Centered modal that renders the active KPI drill-down (navy/gold brand).
    Reads its payload from session_state so it survives in-modal reruns."""
    p = st.session_state.get("_kpi_drill", {}) or {}
    title = p.get("title", "Details")
    help_text = p.get("help", "")
    _df = p.get("df")
    _df = _df if _df is not None else pd.DataFrame()
    st.markdown(
        f"<div style='font-family:\"JetBrains Mono\",monospace;font-weight:700;"
        f"font-size:1.15rem;color:#F0C040;border-bottom:2px solid "
        f"rgba(240,192,64,.35);padding:.1rem 0 .55rem;margin-bottom:.7rem;'>"
        f"{title}</div>", unsafe_allow_html=True)
    if help_text:
        st.caption(help_text)
    if len(_df):
        _MAX_ROWS = 500
        if len(_df) > _MAX_ROWS:
            st.caption(f"Showing top {_MAX_ROWS:,} of {len(_df):,} rows")
            _df = _df.head(_MAX_ROWS)
        st.dataframe(_df, use_container_width=True, hide_index=True,
                     height=min(35 * (len(_df) + 1) + 3, 560))
    else:
        st.info("No detail data available for this metric.")


_KPI_CARD_CSS = """<style>
[class*="st-key-kpimetric_"] button{
    display:flex!important;flex-direction:column!important;align-items:flex-start!important;
    gap:.15rem;background:linear-gradient(160deg,#0E1B30,#16243C)!important;
    border:1px solid #2A4060!important;border-radius:14px!important;
    padding:.7rem .95rem!important;min-height:94px;width:100%;
    box-shadow:0 2px 10px rgba(0,0,0,.25);transition:transform .12s,border-color .12s;}
[class*="st-key-kpimetric_"] button:hover{
    border-color:rgba(240,192,64,.55)!important;transform:translateY(-1px);}
[class*="st-key-kpimetric_"] button p{margin:0!important;line-height:1.2;}
[class*="st-key-kpimetric_"] button p:first-child{
    color:#8FA3B8!important;font-size:.7rem!important;font-weight:600!important;
    letter-spacing:.05em;text-transform:uppercase;}
[class*="st-key-kpimetric_"] button p:nth-child(2){
    color:#F0C040!important;font-size:1.5rem!important;font-weight:800!important;
    font-family:'JetBrains Mono',monospace;}
[class*="st-key-kpimetric_"] button p:nth-child(3){
    color:#8FA3B8!important;font-size:.72rem!important;}
[data-testid="stDialog"] div[role="dialog"]{
    border:1px solid rgba(240,192,64,.30)!important;border-radius:16px!important;}
</style>"""


def dbl_click_metric(
    label: str,
    value: str,
    state_key: str,
    drilldown_title: str,
    drilldown_df,
    help_text: str = "",
    delta: str = "",
    height: int = 95,
) -> None:
    """KPI metric card → click opens a centered navy/gold modal drill-down
    (replaces the old clipped st.popover so wide tables pop fully)."""
    if not st.session_state.get("_kpi_css_injected"):
        st.markdown(_KPI_CARD_CSS, unsafe_allow_html=True)
        st.session_state["_kpi_css_injected"] = True

    btn_label = f"{label}\n\n{value}"
    if delta:
        btn_label += f"\n\n{delta}"
    if st.button(btn_label, key=f"kpimetric_{state_key}",
                 use_container_width=True, help=help_text or None):
        st.session_state["_kpi_drill"] = {
            "title": drilldown_title,
            "df": drilldown_df if drilldown_df is not None else pd.DataFrame(),
            "help": help_text,
        }
        _kpi_drilldown_dialog()


COLOR_SCHEMES = {
    "dashboard":   {"title_bg": "#1A2A3A", "header_bg": "#2D4A6A", "total_bg": "#F0C040", "total_fg": "#000000"},
    "brown_field": {"title_bg": "#0F2D52", "header_bg": "#1E5799", "total_bg": "#BDD7F0", "total_fg": "#000000"},
    "train_j":     {"title_bg": "#4A2E00", "header_bg": "#A0620A", "total_bg": "#FDE8A0", "total_fg": "#000000"},
    "train_k":     {"title_bg": "#0A2E1A", "header_bg": "#1A6B48", "total_bg": "#B3F0D8", "total_fg": "#000000"},
    "session":     {"title_bg": "#2D1A52", "header_bg": "#5B2D8E", "total_bg": "#E5D0F0", "total_fg": "#000000"},
    "execution":   {"title_bg": "#3A0A0A", "header_bg": "#8E1A1A", "total_bg": "#F5C6C6", "total_fg": "#000000"},
    "overview":    {"title_bg": "#0A2A2A", "header_bg": "#0E7490", "total_bg": "#A5F3FC", "total_fg": "#000000"},
}
_LOC_COLOR_MAP = {"Brown Field": "brown_field", "TRAIN J": "train_j", "TRAIN K": "train_k"}
_TABLE_COLOR_MAP = {"equipment": "brown_field", "recipe": "train_j", "inventory": "train_k"}


def generate_excel_report(df: pd.DataFrame,
                          report_title: str = "",
                          add_grand_total: bool = True,
                          color_scheme: str = "dashboard") -> bytes:
    """
    Professional Excel export using xlsxwriter:
    - Rows 0-3: space reserved for logo image (inserted at A1)
    - Row 4:    report title bar (merged, dark navy, white bold)
    - Row 5:    column headers  (navy bg, white bold, border)
    - Row 6+:   data rows       (border)
    - Last row: GRAND TOTAL     (gold bg, bold) when add_grand_total=True
    """
    buf = io.BytesIO()
    out_df = df.copy()

    # Drop helper columns that should not appear in exports
    for _drop in ("☐ Select", "Sl. No."):
        if _drop in out_df.columns:
            out_df = out_df.drop(columns=[_drop])

    # ── Grand total row ───────────────────────────────────────────────────
    if add_grand_total and len(out_df) > 0:
        num_cols = out_df.select_dtypes(include="number").columns.tolist()
        total_row = {c: "" for c in out_df.columns}
        total_row[out_df.columns[0]] = "GRAND TOTAL"
        for c in num_cols:
            try:
                total_row[c] = out_df[c].sum()
            except Exception:
                pass
        out_df = pd.concat([out_df, pd.DataFrame([total_row])], ignore_index=True)

    TITLE_ROW  = 4   # 0-indexed row for the title bar
    HEADER_ROW = 5   # 0-indexed row for column headers
    DATA_START = 6   # 0-indexed first data row
    n_cols = len(out_df.columns)
    cs = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["dashboard"])

    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        # Write data at HEADER_ROW so xlsxwriter positions cells correctly;
        # we will overwrite header row and data rows with formatted versions.
        out_df.to_excel(writer, index=False, sheet_name="Report",
                        startrow=HEADER_ROW)

        wb = writer.book
        ws = writer.sheets["Report"]

        # ── Formats ───────────────────────────────────────────────────────
        title_fmt = wb.add_format({
            "bold": True, "font_size": 13, "font_color": "#FFFFFF",
            "bg_color": cs["title_bg"], "align": "center", "valign": "vcenter",
            "border": 0,
        })
        header_fmt = wb.add_format({
            "bold": True, "font_size": 10, "font_color": "#FFFFFF",
            "bg_color": cs["header_bg"], "align": "center", "valign": "vcenter",
            "border": 1,
        })
        data_fmt = wb.add_format({
            "font_size": 9, "border": 1, "valign": "vcenter",
        })
        total_fmt = wb.add_format({
            "bold": True, "font_size": 10, "bg_color": cs["total_bg"],
            "font_color": cs["total_fg"], "border": 1, "valign": "vcenter",
        })

        # ── Logo — pre-resized to exactly 121×83 px @ 96 DPI = 1.26"×0.86" ─
        if os.path.exists(LOGO_PATH):
            _logo_buf = io.BytesIO()
            with _PILImage.open(LOGO_PATH) as _img:
                _img = _img.resize((121, 83), _PILImage.Resampling.LANCZOS)
                _img.save(_logo_buf, format="PNG", dpi=(96, 96))
            _logo_buf.seek(0)
            ws.insert_image(0, 0, "logo.png", {
                "image_data":      _logo_buf,
                "x_offset":        4,
                "y_offset":        4,
                "object_position": 1,
            })
        # 4 rows × 16 pts = 64 pts ≈ 0.889" — just enough to contain the 83 px logo
        for _r in range(4):
            ws.set_row(_r, 16)

        # ── Report metadata — right side of the header area ───────────────
        if n_cols >= 2:
            meta_label_fmt = wb.add_format({
                "font_size": 8, "bold": True, "align": "right",
                "valign": "vcenter", "font_color": "#555555",
            })
            meta_value_fmt = wb.add_format({
                "font_size": 8, "align": "left",
                "valign": "vcenter", "font_color": "#333333",
            })
            _gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
            ws.write(1, n_cols - 2, "Report Generated:", meta_label_fmt)
            ws.write(1, n_cols - 1, _gen_time,           meta_value_fmt)
            ws.write(2, n_cols - 2, "Generated By:",     meta_label_fmt)
            ws.write(2, n_cols - 1, "Smart Material Estimator", meta_value_fmt)

        # ── Title row ─────────────────────────────────────────────────────
        if report_title and n_cols > 1:
            ws.merge_range(TITLE_ROW, 0, TITLE_ROW, n_cols - 1,
                           report_title, title_fmt)
        elif report_title:
            ws.write(TITLE_ROW, 0, report_title, title_fmt)
        ws.set_row(TITLE_ROW, 22)

        # ── Re-write header row with formatting ───────────────────────────
        for col_i, col_name in enumerate(out_df.columns):
            ws.write(HEADER_ROW, col_i, col_name, header_fmt)
        ws.set_row(HEADER_ROW, 18)
        ws.autofilter(HEADER_ROW, 0, HEADER_ROW, n_cols - 1)

        # ── Re-write data rows with formatting ────────────────────────────
        is_grand_total = add_grand_total and len(out_df) > 0
        for row_i, row_vals in enumerate(out_df.itertuples(index=False, name=None)):
            fmt = total_fmt if (is_grand_total and row_i == len(out_df) - 1) else data_fmt
            for col_i, val in enumerate(row_vals):
                cell_val = "" if (val is None or (isinstance(val, float) and np.isnan(val))) else val
                ws.write(DATA_START + row_i, col_i, cell_val, fmt)

        # ── Auto-width columns ────────────────────────────────────────────
        for col_i, col_name in enumerate(out_df.columns):
            col_data = out_df.iloc[:, col_i].fillna("").astype(str)
            max_len  = max(len(str(col_name)),
                           col_data.str.len().max() if len(col_data) else 0)
            ws.set_column(col_i, col_i, min(int(max_len) + 3, 42))

    return buf.getvalue()


def generate_multi_sheet_excel(sheets: list) -> bytes:
    """
    Build one workbook with one sheet per entry in `sheets`.
    Each sheet gets its own color scheme and AutoFilter.
    sheets: list of dicts with keys:
        name (str)             — Excel tab name (max 31 chars)
        df   (DataFrame)       — data
        title (str)            — title bar text
        color_scheme (str)     — key into COLOR_SCHEMES
        add_grand_total (bool) — default True
    """
    buf = io.BytesIO()
    TITLE_ROW, HEADER_ROW, DATA_START = 4, 5, 6

    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        for spec in sheets:
            out_df = spec["df"].copy()
            title  = spec.get("title", spec["name"])
            cs     = COLOR_SCHEMES.get(spec.get("color_scheme", "dashboard"), COLOR_SCHEMES["dashboard"])
            add_gt = spec.get("add_grand_total", True)
            sname  = spec["name"][:31]

            for _drop in ("☐ Select", "Sl. No."):
                if _drop in out_df.columns:
                    out_df = out_df.drop(columns=[_drop])

            if add_gt and len(out_df) > 0:
                num_cols = out_df.select_dtypes(include="number").columns.tolist()
                total_row = {c: "" for c in out_df.columns}
                total_row[out_df.columns[0]] = "GRAND TOTAL"
                for c in num_cols:
                    try: total_row[c] = out_df[c].sum()
                    except: pass
                out_df = pd.concat([out_df, pd.DataFrame([total_row])], ignore_index=True)

            n_cols = len(out_df.columns)
            out_df.to_excel(writer, index=False, sheet_name=sname, startrow=HEADER_ROW)
            ws = writer.sheets[sname]

            title_fmt  = wb.add_format({"bold": True, "font_size": 13, "font_color": "#FFFFFF",
                "bg_color": cs["title_bg"], "align": "center", "valign": "vcenter", "border": 0})
            header_fmt = wb.add_format({"bold": True, "font_size": 10, "font_color": "#FFFFFF",
                "bg_color": cs["header_bg"], "align": "center", "valign": "vcenter", "border": 1})
            data_fmt   = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
            total_fmt  = wb.add_format({"bold": True, "font_size": 10, "border": 1,
                "bg_color": cs["total_bg"], "font_color": cs["total_fg"], "valign": "vcenter"})
            meta_label_fmt = wb.add_format({"font_size": 8, "bold": True, "align": "right",
                "valign": "vcenter", "font_color": "#555555"})
            meta_value_fmt = wb.add_format({"font_size": 8, "align": "left",
                "valign": "vcenter", "font_color": "#333333"})

            if os.path.exists(LOGO_PATH):
                _logo_buf = io.BytesIO()
                with _PILImage.open(LOGO_PATH) as _img:
                    _img = _img.resize((121, 83), _PILImage.Resampling.LANCZOS)
                    _img.save(_logo_buf, format="PNG", dpi=(96, 96))
                _logo_buf.seek(0)
                ws.insert_image(0, 0, "logo.png", {"image_data": _logo_buf,
                    "x_offset": 4, "y_offset": 4, "object_position": 1})
            for _r in range(4):
                ws.set_row(_r, 16)

            if n_cols >= 2:
                _gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
                ws.write(1, n_cols - 2, "Report Generated:", meta_label_fmt)
                ws.write(1, n_cols - 1, _gen_time, meta_value_fmt)
                ws.write(2, n_cols - 2, "Generated By:", meta_label_fmt)
                ws.write(2, n_cols - 1, "Smart Material Estimator", meta_value_fmt)

            if title and n_cols > 1:
                ws.merge_range(TITLE_ROW, 0, TITLE_ROW, n_cols - 1, title, title_fmt)
            elif title:
                ws.write(TITLE_ROW, 0, title, title_fmt)
            ws.set_row(TITLE_ROW, 22)

            for col_i, col_name in enumerate(out_df.columns):
                ws.write(HEADER_ROW, col_i, col_name, header_fmt)
            ws.set_row(HEADER_ROW, 18)
            ws.autofilter(HEADER_ROW, 0, HEADER_ROW, n_cols - 1)

            is_gt = add_gt and len(out_df) > 0
            for row_i, row_vals in enumerate(out_df.itertuples(index=False, name=None)):
                fmt = total_fmt if (is_gt and row_i == len(out_df) - 1) else data_fmt
                for col_i, val in enumerate(row_vals):
                    cell_val = "" if (val is None or (isinstance(val, float) and np.isnan(val))) else val
                    ws.write(DATA_START + row_i, col_i, cell_val, fmt)

            for col_i, col_name in enumerate(out_df.columns):
                col_data = out_df.iloc[:, col_i].fillna("").astype(str)
                max_len  = max(len(str(col_name)), col_data.str.len().max() if len(col_data) else 0)
                ws.set_column(col_i, col_i, min(int(max_len) + 3, 42))

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# EQUIPMENT REPORT — dedicated Excel writer (Location Report → Equipment Report)
#
# Each sheet has:
#   Main table  ->  Location | Type | Equipment No. | System Code | System Name | Total SQM
#   (1 blank row)
#   Summary     ->  System Code | System Name | Total SQM     (per-sheet totals)
#
# Multi-sheet mode adds a final "All System Codes" sheet with codes-only totals
# across the rows passed in (no location, no equipment columns).
# ─────────────────────────────────────────────────────────────────────────────
def _equipment_report_excel(
    *,
    location_sheets: list,
    all_eq_sheet: dict | None = None,
    include_all_codes_sheet: bool = False,
) -> bytes:
    """Build the Equipment Report workbook.

    location_sheets: list of dicts with keys:
        name (str), df (DataFrame), title (str), color_scheme (str)
    all_eq_sheet: optional dict with same keys for the combined sheet.
    include_all_codes_sheet: if True, appends an "All System Codes" sheet with
        per-code SQM totals computed from all_eq_sheet["df"] (or location rows
        concatenated, when all_eq_sheet is None).

    The "Total SQM" column is summed; all other columns are treated as labels.
    """
    SUMMARY_COLS = ["System Code", "System Name", "Total SQM"]
    # Equipment-summary columns: Tag No. first, then Equipment Name (revised F#3).
    EQ_SUMMARY_COLS = ["Equipment Tag No.", "Equipment Name",
                       "System Name", "Total SQM"]

    # Accept legacy column name "Equipment No." for backwards-compat with
    # callers that still pass it.
    def _norm_tag_col(df):
        if "Equipment No." in df.columns and "Equipment Tag No." not in df.columns:
            df = df.rename(columns={"Equipment No.": "Equipment Tag No."})
        return df

    def _write_main_with_summary(writer, wb, spec):
        out_df = _norm_tag_col(spec["df"].copy().reset_index(drop=True))
        sname  = str(spec["name"])[:31]
        title  = spec.get("title", sname)
        cs     = COLOR_SCHEMES.get(spec.get("color_scheme", "dashboard"),
                                   COLOR_SCHEMES["dashboard"])
        # Build the per-sheet System-Code summary (System Code / Name / SQM)
        if {"System Code", "System Name", "Total SQM"}.issubset(out_df.columns) and len(out_df):
            summary_df = (
                out_df.groupby(["System Code", "System Name"], as_index=False, sort=False)
                ["Total SQM"].sum()
            )
            summary_df["Total SQM"] = summary_df["Total SQM"].round(2)
            summary_df = summary_df.sort_values(
                "System Code",
                key=lambda s: s.astype(str).map(lambda v: int(v) if str(v).isdigit() else 9999),
            ).reset_index(drop=True)
        else:
            summary_df = pd.DataFrame(columns=SUMMARY_COLS)

        # Build the per-sheet Summary by Equipment:
        #   one row per equipment, System Name column is "+"-joined codes
        #   (e.g. "CBL63+RLCB4"), Total SQM = sum across that equipment's codes.
        #   Equipment Name is taken from the first row when available.
        if ({"Equipment Tag No.", "System Code", "System Name", "Total SQM"}
                .issubset(out_df.columns) and len(out_df)):
            _src_cols = ["Equipment Tag No.", "System Code", "System Name", "Total SQM"]
            if "Equipment Name" in out_df.columns:
                _src_cols = ["Equipment Name"] + _src_cols
            _ord = out_df[_src_cols].copy()
            _ord["_code_sort"] = _ord["System Code"].astype(str).map(
                lambda v: int(v) if str(v).isdigit() else 9999)
            _ord = _ord.sort_values(["Equipment Tag No.", "_code_sort"])
            _agg_spec = {
                "System Name": ("System Name",
                                lambda s: "+".join(dict.fromkeys(map(str, s)))),
                "Total SQM":   ("Total SQM", "sum"),
            }
            if "Equipment Name" in _ord.columns:
                _agg_spec["Equipment Name"] = ("Equipment Name", "first")
            eq_summary_df = _ord.groupby("Equipment Tag No.", as_index=False,
                                         sort=False).agg(**_agg_spec)
            eq_summary_df["Total SQM"] = eq_summary_df["Total SQM"].round(2)
            # Reorder columns to match EQ_SUMMARY_COLS order.
            if "Equipment Name" not in eq_summary_df.columns:
                eq_summary_df["Equipment Name"] = ""
            eq_summary_df = eq_summary_df[EQ_SUMMARY_COLS].reset_index(drop=True)
        else:
            eq_summary_df = pd.DataFrame(columns=EQ_SUMMARY_COLS)

        # Main detailed-table grand-total row
        main_df = out_df.copy()
        if len(main_df) > 0 and "Total SQM" in main_df.columns:
            gt_row = {c: "" for c in main_df.columns}
            gt_row[main_df.columns[0]] = "GRAND TOTAL"
            gt_row["Total SQM"] = round(float(main_df["Total SQM"].sum()), 2)
            main_df = pd.concat([main_df, pd.DataFrame([gt_row])], ignore_index=True)

        # Layout: title + meta in top rows, then sections in new order:
        # 1) Summary by Equipment, 2) Summary by System Code, 3) Detailed.
        TITLE_ROW = 4
        n_cols = max(len(main_df.columns), len(EQ_SUMMARY_COLS), len(SUMMARY_COLS))

        # Create the sheet via xlsxwriter (don't call to_excel — we lay out manually).
        ws = wb.add_worksheet(sname)
        writer.sheets[sname] = ws

        # ── Formats ──
        title_fmt = wb.add_format({"bold": True, "font_size": 13, "font_color": "#FFFFFF",
            "bg_color": cs["title_bg"], "align": "center", "valign": "vcenter", "border": 0})
        header_fmt = wb.add_format({"bold": True, "font_size": 10, "font_color": "#FFFFFF",
            "bg_color": cs["header_bg"], "align": "center", "valign": "vcenter", "border": 1})
        data_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
        total_fmt = wb.add_format({"bold": True, "font_size": 10, "border": 1,
            "bg_color": cs["total_bg"], "font_color": cs["total_fg"], "valign": "vcenter"})
        sub_title_fmt = wb.add_format({"bold": True, "font_size": 11, "italic": True,
            "font_color": "#FFFFFF", "bg_color": cs["header_bg"], "align": "left",
            "valign": "vcenter", "border": 1})
        meta_label_fmt = wb.add_format({"font_size": 8, "bold": True, "align": "right",
            "valign": "vcenter", "font_color": "#555555"})
        meta_value_fmt = wb.add_format({"font_size": 8, "align": "left",
            "valign": "vcenter", "font_color": "#333333"})

        # ── Logo + meta ──
        if os.path.exists(LOGO_PATH):
            _logo_buf = io.BytesIO()
            with _PILImage.open(LOGO_PATH) as _img:
                _img = _img.resize((121, 83), _PILImage.Resampling.LANCZOS)
                _img.save(_logo_buf, format="PNG", dpi=(96, 96))
            _logo_buf.seek(0)
            ws.insert_image(0, 0, "logo.png", {"image_data": _logo_buf,
                "x_offset": 4, "y_offset": 4, "object_position": 1})
        for _r in range(4):
            ws.set_row(_r, 16)
        if n_cols >= 2:
            _gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
            ws.write(1, n_cols - 2, "Report Generated:", meta_label_fmt)
            ws.write(1, n_cols - 1, _gen_time, meta_value_fmt)
            ws.write(2, n_cols - 2, "Generated By:", meta_label_fmt)
            ws.write(2, n_cols - 1, "Smart Material Estimator", meta_value_fmt)

        # ── Title bar ──
        if title and n_cols > 1:
            ws.merge_range(TITLE_ROW, 0, TITLE_ROW, n_cols - 1, title, title_fmt)
        elif title:
            ws.write(TITLE_ROW, 0, title, title_fmt)
        ws.set_row(TITLE_ROW, 22)

        # ════════════════════════════════════════════════════════════════════
        # SECTION 1 — Summary by Equipment   (Fix #5 — first)
        # ════════════════════════════════════════════════════════════════════
        cur_row = TITLE_ROW + 1
        eq_sub_row = cur_row
        eq_sub_span = min(n_cols, len(EQ_SUMMARY_COLS))
        if eq_sub_span > 1:
            ws.merge_range(eq_sub_row, 0, eq_sub_row, eq_sub_span - 1,
                           "1. Summary by Equipment (Total SQM)", sub_title_fmt)
        else:
            ws.write(eq_sub_row, 0, "1. Summary by Equipment (Total SQM)", sub_title_fmt)
        ws.set_row(eq_sub_row, 20)
        eq_hdr_row  = eq_sub_row + 1
        eq_data_row = eq_hdr_row + 1
        for col_i, col_name in enumerate(EQ_SUMMARY_COLS):
            ws.write(eq_hdr_row, col_i, col_name, header_fmt)
        ws.set_row(eq_hdr_row, 18)
        for row_i, row_vals in enumerate(
                eq_summary_df[EQ_SUMMARY_COLS].itertuples(index=False, name=None)):
            for col_i, val in enumerate(row_vals):
                cell_val = ("" if (val is None or
                                   (isinstance(val, float) and np.isnan(val))) else val)
                ws.write(eq_data_row + row_i, col_i, cell_val, data_fmt)
        if len(eq_summary_df):
            eq_gt_row = eq_data_row + len(eq_summary_df)
            ws.write(eq_gt_row, 0, "GRAND TOTAL", total_fmt)
            for _ci in range(1, len(EQ_SUMMARY_COLS) - 1):
                ws.write(eq_gt_row, _ci, "", total_fmt)
            ws.write(eq_gt_row, len(EQ_SUMMARY_COLS) - 1,
                     round(float(eq_summary_df["Total SQM"].sum()), 2), total_fmt)
            eq_block_end = eq_gt_row
        else:
            eq_block_end = eq_hdr_row

        # ════════════════════════════════════════════════════════════════════
        # SECTION 2 — Summary by System Code
        # ════════════════════════════════════════════════════════════════════
        sub_row     = eq_block_end + 2     # one blank row before subtitle
        sum_hdr_row = sub_row + 1
        sum_data_r  = sum_hdr_row + 1
        sub_span = min(n_cols, len(SUMMARY_COLS))
        if sub_span > 1:
            ws.merge_range(sub_row, 0, sub_row, sub_span - 1,
                           "2. Summary by System Code (Total SQM)", sub_title_fmt)
        else:
            ws.write(sub_row, 0, "2. Summary by System Code (Total SQM)", sub_title_fmt)
        ws.set_row(sub_row, 20)
        for col_i, col_name in enumerate(SUMMARY_COLS):
            ws.write(sum_hdr_row, col_i, col_name, header_fmt)
        ws.set_row(sum_hdr_row, 18)
        for row_i, row_vals in enumerate(
                summary_df[SUMMARY_COLS].itertuples(index=False, name=None)):
            for col_i, val in enumerate(row_vals):
                cell_val = ("" if (val is None or
                                   (isinstance(val, float) and np.isnan(val))) else val)
                ws.write(sum_data_r + row_i, col_i, cell_val, data_fmt)
        if len(summary_df):
            sum_gt_row = sum_data_r + len(summary_df)
            ws.write(sum_gt_row, 0, "GRAND TOTAL", total_fmt)
            ws.write(sum_gt_row, 1, "", total_fmt)
            ws.write(sum_gt_row, 2,
                     round(float(summary_df["Total SQM"].sum()), 2), total_fmt)
            sys_block_end = sum_gt_row
        else:
            sys_block_end = sum_hdr_row

        # ════════════════════════════════════════════════════════════════════
        # SECTION 3 — Detailed table (the full per-row data)
        # ════════════════════════════════════════════════════════════════════
        det_sub_row = sys_block_end + 2
        det_hdr_row = det_sub_row + 1
        det_data_r  = det_hdr_row + 1
        main_n_cols = len(main_df.columns)
        det_sub_span = min(n_cols, main_n_cols)
        if det_sub_span > 1:
            ws.merge_range(det_sub_row, 0, det_sub_row, det_sub_span - 1,
                           "3. Detailed Table", sub_title_fmt)
        else:
            ws.write(det_sub_row, 0, "3. Detailed Table", sub_title_fmt)
        ws.set_row(det_sub_row, 20)
        for col_i, col_name in enumerate(main_df.columns):
            ws.write(det_hdr_row, col_i, col_name, header_fmt)
        ws.set_row(det_hdr_row, 18)
        ws.autofilter(det_hdr_row, 0, det_hdr_row, main_n_cols - 1)
        for row_i, row_vals in enumerate(main_df.itertuples(index=False, name=None)):
            is_gt = (row_i == len(main_df) - 1) and len(main_df) > 0
            fmt = total_fmt if is_gt else data_fmt
            for col_i, val in enumerate(row_vals):
                cell_val = ("" if (val is None or
                                   (isinstance(val, float) and np.isnan(val))) else val)
                ws.write(det_data_r + row_i, col_i, cell_val, fmt)

        # ── Auto-width across all blocks ──
        def _max_str_len(series):
            return series.str.len().max() if len(series) else 0
        for col_i in range(n_cols):
            candidates = []
            if col_i < main_n_cols:
                _md = main_df.iloc[:, col_i].fillna("").astype(str)
                candidates.append(max(len(str(main_df.columns[col_i])),
                                      _max_str_len(_md)))
            if col_i < len(SUMMARY_COLS) and len(summary_df):
                sc = summary_df.iloc[:, col_i].fillna("").astype(str)
                candidates.append(max(len(str(SUMMARY_COLS[col_i])),
                                      _max_str_len(sc)))
            if col_i < len(EQ_SUMMARY_COLS) and len(eq_summary_df):
                ec = eq_summary_df.iloc[:, col_i].fillna("").astype(str)
                candidates.append(max(len(str(EQ_SUMMARY_COLS[col_i])),
                                      _max_str_len(ec)))
            if not candidates:
                candidates = [10]
            final_w = min(int(max(candidates)) + 3, 42)
            ws.set_column(col_i, col_i, final_w)

    # ── Helper: write the "All System Codes" sheet (codes-only totals) ──
    def _write_all_codes_sheet(writer, wb, all_rows_df: pd.DataFrame):
        cs = COLOR_SCHEMES["overview"]
        sname = "All System Codes"
        TITLE_ROW, HEADER_ROW, DATA_START = 4, 5, 6

        if (not {"System Code", "System Name", "Total SQM"}.issubset(all_rows_df.columns)
                or len(all_rows_df) == 0):
            df = pd.DataFrame(columns=SUMMARY_COLS)
        else:
            df = (all_rows_df.groupby(["System Code", "System Name"],
                                       as_index=False, sort=False)
                  ["Total SQM"].sum())
            df["Total SQM"] = df["Total SQM"].round(2)
            df = df.sort_values(
                "System Code",
                key=lambda s: s.astype(str).map(lambda v: int(v) if str(v).isdigit() else 9999),
            ).reset_index(drop=True)

        # Append grand total row
        out_df = df.copy()
        if len(out_df) > 0:
            gt_row = {c: "" for c in out_df.columns}
            gt_row["System Code"] = "GRAND TOTAL"
            gt_row["Total SQM"]   = round(float(out_df["Total SQM"].sum()), 2)
            out_df = pd.concat([out_df, pd.DataFrame([gt_row])], ignore_index=True)

        n_cols = len(out_df.columns)
        out_df.to_excel(writer, index=False, sheet_name=sname, startrow=HEADER_ROW)
        ws = writer.sheets[sname]

        title_fmt = wb.add_format({"bold": True, "font_size": 13, "font_color": "#FFFFFF",
            "bg_color": cs["title_bg"], "align": "center", "valign": "vcenter", "border": 0})
        header_fmt = wb.add_format({"bold": True, "font_size": 10, "font_color": "#FFFFFF",
            "bg_color": cs["header_bg"], "align": "center", "valign": "vcenter", "border": 1})
        data_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
        total_fmt = wb.add_format({"bold": True, "font_size": 10, "border": 1,
            "bg_color": cs["total_bg"], "font_color": cs["total_fg"], "valign": "vcenter"})
        meta_label_fmt = wb.add_format({"font_size": 8, "bold": True, "align": "right",
            "valign": "vcenter", "font_color": "#555555"})
        meta_value_fmt = wb.add_format({"font_size": 8, "align": "left",
            "valign": "vcenter", "font_color": "#333333"})

        if os.path.exists(LOGO_PATH):
            _logo_buf = io.BytesIO()
            with _PILImage.open(LOGO_PATH) as _img:
                _img = _img.resize((121, 83), _PILImage.Resampling.LANCZOS)
                _img.save(_logo_buf, format="PNG", dpi=(96, 96))
            _logo_buf.seek(0)
            ws.insert_image(0, 0, "logo.png", {"image_data": _logo_buf,
                "x_offset": 4, "y_offset": 4, "object_position": 1})
        for _r in range(4):
            ws.set_row(_r, 16)
        if n_cols >= 2:
            _gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
            ws.write(1, n_cols - 2, "Report Generated:", meta_label_fmt)
            ws.write(1, n_cols - 1, _gen_time, meta_value_fmt)
            ws.write(2, n_cols - 2, "Generated By:", meta_label_fmt)
            ws.write(2, n_cols - 1, "Smart Material Estimator", meta_value_fmt)

        if n_cols > 1:
            ws.merge_range(TITLE_ROW, 0, TITLE_ROW, n_cols - 1,
                           "Equipment Report — All System Codes (Total SQM)", title_fmt)
        else:
            ws.write(TITLE_ROW, 0, "Equipment Report — All System Codes (Total SQM)", title_fmt)
        ws.set_row(TITLE_ROW, 22)

        for col_i, col_name in enumerate(out_df.columns):
            ws.write(HEADER_ROW, col_i, col_name, header_fmt)
        ws.set_row(HEADER_ROW, 18)
        ws.autofilter(HEADER_ROW, 0, HEADER_ROW, n_cols - 1)

        for row_i, row_vals in enumerate(out_df.itertuples(index=False, name=None)):
            is_gt = (row_i == len(out_df) - 1) and len(out_df) > 0
            fmt = total_fmt if is_gt else data_fmt
            for col_i, val in enumerate(row_vals):
                cell_val = ("" if (val is None or
                                   (isinstance(val, float) and np.isnan(val))) else val)
                ws.write(DATA_START + row_i, col_i, cell_val, fmt)

        for col_i, col_name in enumerate(out_df.columns):
            col_data = out_df.iloc[:, col_i].fillna("").astype(str)
            max_len  = max(len(str(col_name)),
                           col_data.str.len().max() if len(col_data) else 0)
            ws.set_column(col_i, col_i, min(int(max_len) + 3, 42))

    # ── Compose the workbook ──
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        for spec in location_sheets:
            _write_main_with_summary(writer, wb, spec)
        if all_eq_sheet is not None:
            _write_main_with_summary(writer, wb, all_eq_sheet)
        if include_all_codes_sheet:
            # Pool all rows from the location sheets (+ all_eq_sheet if given)
            # — but for de-dup avoid double counting: prefer all_eq_sheet rows
            # when available (it already contains everything).
            if all_eq_sheet is not None and len(all_eq_sheet.get("df", pd.DataFrame())) > 0:
                pooled = all_eq_sheet["df"]
            else:
                _frames = [s["df"] for s in location_sheets if len(s.get("df", pd.DataFrame())) > 0]
                pooled = pd.concat(_frames, ignore_index=True) if _frames else pd.DataFrame()
            _write_all_codes_sheet(writer, wb, pooled)

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# LOCATION REPORT — Excel writer with main alloc table + 3 summary blocks
#
# Each sheet contains:
#   1. Main alloc table (Equipment / System Code / Material / Demand / Available
#      / Allocated / Shortfall / Coverage % …)
#   2. blank row
#   3. Summary by System Code   →  System Code | System Name | Total SQM
#   4. blank row
#   5. Summary by Equipment     →  Equipment No. | System Names (concat) | Total SQM
#   6. blank row
#   7. Summary by Material      →  Material Code | Material Name | UOM | Total Qty
#
# Used by Location Based and All Equipment downloads in the Location Report tab.
# Existing generate_excel_report / generate_multi_sheet_excel are untouched, so
# every other report in the app behaves exactly as before.
# ─────────────────────────────────────────────────────────────────────────────
def _location_report_excel(*, sheets: list) -> bytes:
    """Build the Location Report workbook.

    sheets: list of dicts, each with:
        name (str), df (alloc-style DataFrame), title (str), color_scheme (str)

    The df must contain at minimum these columns (extra columns are kept):
        Equipment_Tag_No., Lining_System_Code, Lining_System_Short_Name,
        Total_SQM, Material_Code, Material_Name, UOM, Demand_Qty
    """
    SUMMARY_COLS    = ["System Code", "System Name", "Total SQM"]
    EQ_SUMMARY_COLS = ["Equipment No.", "System Name", "Total SQM"]
    MAT_SUMMARY_COLS = ["Material Code", "Material Name", "UOM", "Total Qty"]

    def _write_sheet(writer, wb, spec):
        out_df = spec["df"].copy().reset_index(drop=True)
        sname  = str(spec["name"])[:31]
        title  = spec.get("title", sname)
        cs     = COLOR_SCHEMES.get(spec.get("color_scheme", "dashboard"),
                                   COLOR_SCHEMES["dashboard"])

        # ── Build summaries from the source df ──
        # System Code & Equipment summaries use UNIQUE (Tag, Code) Total_SQM
        # to avoid double-counting across material rows.
        if {"Equipment_Tag_No.", "Lining_System_Code",
                "Lining_System_Short_Name", "Total_SQM"}.issubset(out_df.columns) and len(out_df):
            tag_code = (out_df[["Equipment_Tag_No.", "Lining_System_Code",
                                "Lining_System_Short_Name", "Total_SQM"]]
                        .drop_duplicates(subset=["Equipment_Tag_No.",
                                                  "Lining_System_Code"]))
            sys_sum = (tag_code.groupby(
                          ["Lining_System_Code", "Lining_System_Short_Name"],
                          as_index=False, sort=False)["Total_SQM"].sum())
            sys_sum["Total_SQM"] = sys_sum["Total_SQM"].round(2)
            sys_sum = sys_sum.sort_values(
                "Lining_System_Code",
                key=lambda s: s.astype(str).map(
                    lambda v: int(v) if str(v).isdigit() else 9999),
            ).reset_index(drop=True)
            sys_sum.columns = SUMMARY_COLS

            tc = tag_code.copy()
            tc["_code_sort"] = tc["Lining_System_Code"].astype(str).map(
                lambda v: int(v) if str(v).isdigit() else 9999)
            tc = tc.sort_values(["Equipment_Tag_No.", "_code_sort"])
            eq_sum = tc.groupby("Equipment_Tag_No.", as_index=False, sort=False).agg(
                **{"System Name": ("Lining_System_Short_Name",
                                   lambda s: "+".join(dict.fromkeys(map(str, s)))),
                   "Total SQM":   ("Total_SQM", "sum")},
            )
            eq_sum["Total SQM"] = eq_sum["Total SQM"].round(2)
            eq_sum = eq_sum.rename(columns={"Equipment_Tag_No.": "Equipment No."})
            eq_sum = eq_sum[EQ_SUMMARY_COLS].reset_index(drop=True)
        else:
            sys_sum = pd.DataFrame(columns=SUMMARY_COLS)
            eq_sum  = pd.DataFrame(columns=EQ_SUMMARY_COLS)

        # Material summary uses Demand_Qty (in material UOM)
        if ({"Material_Code", "Material_Name", "UOM", "Demand_Qty"}
                .issubset(out_df.columns) and len(out_df)):
            mat_sum = (out_df.groupby(
                          ["Material_Code", "Material_Name", "UOM"],
                          as_index=False, sort=False)["Demand_Qty"].sum())
            mat_sum["Demand_Qty"] = mat_sum["Demand_Qty"].round(3)
            mat_sum = mat_sum.sort_values("Material_Code").reset_index(drop=True)
            mat_sum.columns = MAT_SUMMARY_COLS
        else:
            mat_sum = pd.DataFrame(columns=MAT_SUMMARY_COLS)

        # ── Main df + grand total row (sums numeric cols) ──
        main_df = out_df.copy()
        if len(main_df) > 0:
            num_cols = main_df.select_dtypes(include="number").columns.tolist()
            gt = {c: "" for c in main_df.columns}
            gt[main_df.columns[0]] = "GRAND TOTAL"
            for c in num_cols:
                try: gt[c] = round(float(main_df[c].sum()), 3)
                except Exception: pass
            main_df = pd.concat([main_df, pd.DataFrame([gt])], ignore_index=True)

        TITLE_ROW, HEADER_ROW, DATA_START = 4, 5, 6
        n_cols = len(main_df.columns)

        main_df.to_excel(writer, index=False, sheet_name=sname, startrow=HEADER_ROW)
        ws = writer.sheets[sname]

        title_fmt = wb.add_format({"bold": True, "font_size": 13, "font_color": "#FFFFFF",
            "bg_color": cs["title_bg"], "align": "center", "valign": "vcenter", "border": 0})
        header_fmt = wb.add_format({"bold": True, "font_size": 10, "font_color": "#FFFFFF",
            "bg_color": cs["header_bg"], "align": "center", "valign": "vcenter", "border": 1})
        data_fmt = wb.add_format({"font_size": 9, "border": 1, "valign": "vcenter"})
        total_fmt = wb.add_format({"bold": True, "font_size": 10, "border": 1,
            "bg_color": cs["total_bg"], "font_color": cs["total_fg"], "valign": "vcenter"})
        sub_title_fmt = wb.add_format({"bold": True, "font_size": 11, "italic": True,
            "font_color": "#FFFFFF", "bg_color": cs["header_bg"], "align": "left",
            "valign": "vcenter", "border": 1})
        meta_label_fmt = wb.add_format({"font_size": 8, "bold": True, "align": "right",
            "valign": "vcenter", "font_color": "#555555"})
        meta_value_fmt = wb.add_format({"font_size": 8, "align": "left",
            "valign": "vcenter", "font_color": "#333333"})

        # Logo + meta
        if os.path.exists(LOGO_PATH):
            _logo_buf = io.BytesIO()
            with _PILImage.open(LOGO_PATH) as _img:
                _img = _img.resize((121, 83), _PILImage.Resampling.LANCZOS)
                _img.save(_logo_buf, format="PNG", dpi=(96, 96))
            _logo_buf.seek(0)
            ws.insert_image(0, 0, "logo.png", {"image_data": _logo_buf,
                "x_offset": 4, "y_offset": 4, "object_position": 1})
        for _r in range(4):
            ws.set_row(_r, 16)
        if n_cols >= 2:
            _gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
            ws.write(1, n_cols - 2, "Report Generated:", meta_label_fmt)
            ws.write(1, n_cols - 1, _gen_time, meta_value_fmt)
            ws.write(2, n_cols - 2, "Generated By:", meta_label_fmt)
            ws.write(2, n_cols - 1, "Smart Material Estimator", meta_value_fmt)

        # Title bar
        if title and n_cols > 1:
            ws.merge_range(TITLE_ROW, 0, TITLE_ROW, n_cols - 1, title, title_fmt)
        elif title:
            ws.write(TITLE_ROW, 0, title, title_fmt)
        ws.set_row(TITLE_ROW, 22)

        # Header row
        for col_i, col_name in enumerate(main_df.columns):
            ws.write(HEADER_ROW, col_i, col_name, header_fmt)
        ws.set_row(HEADER_ROW, 18)
        ws.autofilter(HEADER_ROW, 0, HEADER_ROW, n_cols - 1)

        # Data rows
        for row_i, row_vals in enumerate(main_df.itertuples(index=False, name=None)):
            is_gt = (row_i == len(main_df) - 1) and len(main_df) > 0
            fmt = total_fmt if is_gt else data_fmt
            for col_i, val in enumerate(row_vals):
                cell_val = ("" if (val is None or
                                   (isinstance(val, float) and np.isnan(val))) else val)
                ws.write(DATA_START + row_i, col_i, cell_val, fmt)

        # ── helper: write a summary block at row `start_row` and return next free row ──
        def _write_summary_block(start_row, sub_title, cols, df):
            blank_row = start_row
            sub_row   = blank_row + 1
            hdr_row   = sub_row + 1
            data_r    = hdr_row + 1

            span = min(n_cols, len(cols))
            if span > 1:
                ws.merge_range(sub_row, 0, sub_row, span - 1, sub_title, sub_title_fmt)
            else:
                ws.write(sub_row, 0, sub_title, sub_title_fmt)
            ws.set_row(sub_row, 20)

            for col_i, col_name in enumerate(cols):
                ws.write(hdr_row, col_i, col_name, header_fmt)
            ws.set_row(hdr_row, 18)

            for row_i, row_vals in enumerate(df[cols].itertuples(index=False, name=None)):
                for col_i, val in enumerate(row_vals):
                    cell_val = ("" if (val is None or
                                       (isinstance(val, float) and np.isnan(val))) else val)
                    ws.write(data_r + row_i, col_i, cell_val, data_fmt)

            end_row = data_r + max(len(df) - 1, 0)
            if len(df) > 0:
                # Grand total — sum only the numeric column at index 2 (or last col)
                gt_row = data_r + len(df)
                ws.write(gt_row, 0, "GRAND TOTAL", total_fmt)
                for col_i in range(1, len(cols)):
                    if col_i == len(cols) - 1:
                        try:
                            ws.write(gt_row, col_i,
                                     round(float(df[cols[col_i]].sum()), 3), total_fmt)
                        except Exception:
                            ws.write(gt_row, col_i, "", total_fmt)
                    else:
                        ws.write(gt_row, col_i, "", total_fmt)
                end_row = gt_row
            return end_row

        # Append three summaries, each preceded by one blank row
        cur = DATA_START + len(main_df)            # row index right after main data
        cur = _write_summary_block(cur, "Summary by System Code (Total SQM)",
                                    SUMMARY_COLS, sys_sum)
        cur = _write_summary_block(cur + 1, "Summary by Equipment (Total SQM)",
                                    EQ_SUMMARY_COLS, eq_sum)
        cur = _write_summary_block(cur + 1, "Summary by Material (Total Qty)",
                                    MAT_SUMMARY_COLS, mat_sum)

        # Auto-width across main + 3 summary blocks
        def _max_len(series):
            return series.str.len().max() if len(series) else 0
        for col_i in range(n_cols):
            cands = []
            mcol = main_df.iloc[:, col_i].fillna("").astype(str)
            cands.append(max(len(str(main_df.columns[col_i])), _max_len(mcol)))
            if col_i < len(SUMMARY_COLS):
                sc = sys_sum.iloc[:, col_i].fillna("").astype(str) if len(sys_sum) else pd.Series([], dtype=str)
                cands.append(max(len(str(SUMMARY_COLS[col_i])), _max_len(sc)))
            if col_i < len(EQ_SUMMARY_COLS):
                ec = eq_sum.iloc[:, col_i].fillna("").astype(str) if len(eq_sum) else pd.Series([], dtype=str)
                cands.append(max(len(str(EQ_SUMMARY_COLS[col_i])), _max_len(ec)))
            if col_i < len(MAT_SUMMARY_COLS):
                mc = mat_sum.iloc[:, col_i].fillna("").astype(str) if len(mat_sum) else pd.Series([], dtype=str)
                cands.append(max(len(str(MAT_SUMMARY_COLS[col_i])), _max_len(mc)))
            ws.set_column(col_i, col_i, min(int(max(cands)) + 3, 42))

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        wb = writer.book
        for spec in sheets:
            _write_sheet(writer, wb, spec)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# PRINT HELPER — emits a self-contained printable HTML file as a data: URL
# that opens in a new browser tab and auto-fires window.print().
# Used by per-equipment Print buttons in the Equipment Report,
# Location Based, and All Equipment views.
# ─────────────────────────────────────────────────────────────────────────────
def _build_print_html(*, title: str, accent: str = "#F59E0B",
                       sections: list) -> str:
    """sections: list of dicts, each one of:
        {"kind": "kv",       "title": str, "rows": [("Label", "Value"), …]}
        {"kind": "table",    "title": str, "columns": [str], "rows": [[…]]}
        {"kind": "syscode",  "title": str, "code": str, "name": str,
                              "sqm": float|None, "columns": [str], "rows": [[…]]}
        {"kind": "summary",  "title": str, "columns": [str], "rows": [[…]],
                              "grand_total": (label, last-column-value)|None}
    Returns a complete HTML document string.
    """
    import html as _html_mod
    css = f"""
      *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}
      body {{ font-family:'Inter','Helvetica',sans-serif; color:#0F172A;
             background:#FFFFFF; padding:24px; font-size:12px; }}
      h1 {{ font-size:18px; font-weight:800; color:#0F172A;
            border-left:5px solid {accent}; padding-left:12px;
            margin:0 0 4px; line-height:1.15; letter-spacing:-.2px; }}
      .meta {{ font-size:10px; color:#64748B; margin-bottom:18px; }}
      h2 {{ font-size:13px; font-weight:700; color:#FFFFFF; background:#1E293B;
            padding:6px 10px; margin:18px 0 0; letter-spacing:.06em;
            text-transform:uppercase; border-radius:3px 3px 0 0; }}
      .syscode-h2 {{ background:linear-gradient(90deg,{accent},#D97706);
                     color:#000; }}
      table {{ width:100%; border-collapse:collapse; font-size:11px;
              margin-bottom:0; }}
      th {{ background:#0F172A; color:#FFFFFF; font-weight:700;
           padding:6px 8px; text-align:left; border:1px solid #1E293B;
           letter-spacing:.05em; text-transform:uppercase; font-size:9.5px; }}
      td {{ padding:5px 8px; border:1px solid #E2E8F0; color:#1E293B;
           vertical-align:top; }}
      tbody tr:nth-child(even) td {{ background:#F8FAFC; }}
      tr.gt td {{ background:#FDE68A !important; font-weight:800; color:#000; }}
      .kv {{ display:grid; grid-template-columns:160px 1fr; row-gap:4px;
            column-gap:14px; background:#F1F5F9; border:1px solid #E2E8F0;
            border-radius:4px; padding:10px 14px; margin:8px 0 6px; }}
      .kv-l {{ font-size:10px; color:#64748B; text-transform:uppercase;
              letter-spacing:.06em; font-weight:700; }}
      .kv-v {{ font-size:12px; color:#0F172A; font-weight:600;
              font-family:'JetBrains Mono','Menlo',monospace; }}
      .syscode-meta {{ display:flex; align-items:center; gap:12px;
                        background:#FEF3C7; border:1px solid #FCD34D;
                        border-bottom:none; padding:6px 10px;
                        font-size:11px; font-weight:700; }}
      .syscode-meta .badge {{ background:{accent}; color:#000;
                              padding:2px 8px; border-radius:3px;
                              font-family:'JetBrains Mono',monospace;
                              font-weight:800; font-size:11px; }}
      .right {{ text-align:right;
                font-family:'JetBrains Mono','Menlo',monospace; }}
      @page {{ margin:0.5cm; }}
      @media print {{
        body {{ padding:8mm; }}
        h1 {{ break-after:avoid; }}
        h2 {{ break-after:avoid; }}
        table {{ break-inside:auto; }}
        tr {{ break-inside:avoid; }}
      }}
    """
    _esc = lambda v: _html_mod.escape(str(v if v is not None else ""))
    def _fmt_cell(v, col_name=""):
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return "", ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if any(k in col_name.lower() for k in
                   ("qty","sqm","demand","alloc","short","ord","avail","total","fulfil","coverage","pct","%")):
                return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) and v == int(v) else f"{v:,.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}", "right"
            return _esc(v), ""
        return _esc(v), ""

    def _render_table(columns, rows, gt_label=None):
        thead = "".join(f"<th>{_esc(c)}</th>" for c in columns)
        body = []
        for ri, r in enumerate(rows):
            is_gt = gt_label and str(r[0]).startswith(gt_label)
            tds = []
            for ci, v in enumerate(r):
                txt, cls = _fmt_cell(v, columns[ci] if ci < len(columns) else "")
                tds.append(f'<td class="{cls}">{txt}</td>')
            cls = ' class="gt"' if is_gt else ""
            body.append(f"<tr{cls}>{''.join(tds)}</tr>")
        return f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    parts = []
    gen_time = datetime.now().strftime("%Y-%m-%d  %H:%M")
    parts.append(f'<h1>{_esc(title)}</h1>')
    parts.append(f'<div class="meta">Generated: {gen_time}  ·  Smart Material Estimator</div>')

    for sec in sections:
        kind = sec.get("kind", "table")
        sec_title = sec.get("title", "")
        if kind == "kv":
            if sec_title:
                parts.append(f'<h2>{_esc(sec_title)}</h2>')
            rows = sec.get("rows") or []
            kv_html = "".join(
                f'<div class="kv-l">{_esc(l)}</div><div class="kv-v">{_esc(v)}</div>'
                for l, v in rows)
            parts.append(f'<div class="kv">{kv_html}</div>')
        elif kind == "syscode":
            parts.append(
                f'<div class="syscode-meta">'
                f'<span class="badge">Code {_esc(sec.get("code",""))}</span>'
                f'<span>{_esc(sec.get("name",""))}</span>'
                f'<span style="margin-left:auto;">'
                f'{f"{float(sec['sqm']):,.2f} SQM" if sec.get("sqm") is not None else ""}'
                f'</span></div>'
            )
            parts.append(_render_table(sec.get("columns") or [],
                                        sec.get("rows") or []))
        elif kind == "summary":
            if sec_title:
                parts.append(f'<h2>{_esc(sec_title)}</h2>')
            rows = sec.get("rows") or []
            if sec.get("grand_total"):
                gt_label, gt_val = sec["grand_total"]
                rows = list(rows) + [[gt_label] + [""] * (len(sec["columns"]) - 2) + [gt_val]]
            parts.append(_render_table(sec.get("columns") or [], rows,
                                       gt_label="GRAND TOTAL"))
        else:  # plain table
            if sec_title:
                parts.append(f'<h2>{_esc(sec_title)}</h2>')
            parts.append(_render_table(sec.get("columns") or [],
                                        sec.get("rows") or []))

    body = "\n".join(parts)
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{css}</style>
</head><body>
{body}
<script>window.addEventListener("load", function() {{ setTimeout(function() {{ window.print(); }}, 250); }});</script>
</body></html>"""


def _per_equipment_payload(
    *, tag: str, tag_alloc: pd.DataFrame, eq_row,
    inv_df: pd.DataFrame, color_scheme: str = "overview",
):
    """Build (xlsx_bytes, print_html) for ONE equipment.

    Shared by Equipment Report (Location Based / All Equipment) views.
    Excel = single sheet with main alloc table + 3 summary blocks (same
    layout as a per-location download but scoped to one equipment).
    Print = HTML mirror of the on-screen layout (equipment KV + one
    syscode block per system code with its material table).
    """
    # Enrich alloc with inventory cols so the Excel matches per-location format
    _inv_lu = inv_df[["Material_Code","Available_Qty","Ordered_Qty"]].groupby(
        "Material_Code", as_index=False).first()
    enriched = tag_alloc.merge(_inv_lu, on="Material_Code", how="left")
    enriched["Available_Qty"] = enriched["Available_Qty"].fillna(0)
    enriched["Ordered_Qty"]   = enriched["Ordered_Qty"].fillna(0)
    keep = ["Equipment_Tag_No.","Lining_System_Code","Lining_System_Short_Name",
            "Total_SQM","Material_Code","Material_Name","UOM",
            "Demand_Qty","Available_Qty","Ordered_Qty",
            "Allocated_Qty","Shortfall_Qty","Fulfillment_Pct"]
    enriched = enriched[[c for c in keep if c in enriched.columns]]

    xlsx = _location_report_excel(sheets=[{
        "name":         str(tag)[:31],
        "df":           enriched,
        "title":        f"Equipment Report — {tag}",
        "color_scheme": color_scheme,
    }])

    # ── Print HTML — KV section + one syscode block per code ──
    eq_total_sqm = float(enriched.drop_duplicates(
        subset=["Lining_System_Code"])["Total_SQM"].sum()) if len(enriched) else 0.0

    sections = [{"kind":"kv","title":"Equipment Details","rows":[
        ("Equipment Tag",   str(tag)),
        ("Name",            str(eq_row.get("Name", "") if hasattr(eq_row, "get") else getattr(eq_row, "Name", ""))),
        ("Type",            str(eq_row.get("Type", "") if hasattr(eq_row, "get") else getattr(eq_row, "Type", ""))),
        ("Location",        str(eq_row.get("Location", "") if hasattr(eq_row, "get") else getattr(eq_row, "Location", ""))),
        ("Substrate",       str(eq_row.get("Substrate", "") or "—" if hasattr(eq_row, "get") else (getattr(eq_row, "Substrate", "") or "—"))),
        ("Material Spec.",  str(eq_row.get("Material_Spec", "") or "—" if hasattr(eq_row, "get") else (getattr(eq_row, "Material_Spec", "") or "—"))),
        ("Total SQM",       f"{eq_total_sqm:,.2f}"),
    ]}]

    mat_cols = ["Material Code","Material Name","UOM","Demand","Available",
                "Allocated","Shortfall","Fulfil %","SQM"]
    for code in sorted(enriched["Lining_System_Code"].unique() if len(enriched) else [],
                        key=lambda x: int(x) if str(x).isdigit() else 9999):
        code_rows = enriched[enriched["Lining_System_Code"] == code]
        if code_rows.empty:
            continue
        sname = code_rows["Lining_System_Short_Name"].iloc[0]
        sqm_v = float(code_rows["Total_SQM"].iloc[0])
        rows = [[
            r["Material_Code"], r["Material_Name"], r["UOM"],
            round(float(r["Demand_Qty"]), 3),
            round(float(r["Available_Qty"]), 3),
            round(float(r["Allocated_Qty"]), 3),
            round(float(r["Shortfall_Qty"]), 3),
            f'{float(r.get("Fulfillment_Pct", 0) or 0):.1f}%',
            f"{sqm_v:,.2f}",
        ] for _, r in code_rows.iterrows()]
        sections.append({"kind":"syscode","code":str(code),"name":str(sname),
                         "sqm":sqm_v,"columns":mat_cols,"rows":rows})

    html_doc = _build_print_html(title=f"Equipment Report — {tag}",
                                   sections=sections)
    return xlsx, html_doc


def _print_button_html(label: str, html_doc: str, key_suffix: str = "") -> str:
    """Return an `<a>` element styled as a button. Clicking opens the print
    HTML in a new tab and auto-triggers window.print()."""
    import base64
    b64 = base64.b64encode(html_doc.encode("utf-8")).decode("ascii")
    href = f"data:text/html;base64,{b64}"
    return (
        f'<a href="{href}" target="_blank" rel="noopener" '
        f'class="sme-print-btn" data-k="{key_suffix}" '
        f'style="display:inline-flex;align-items:center;gap:6px;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:.66rem;'
        f'font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        f'background:transparent;color:var(--amber);'
        f'border:1px solid var(--amber);border-radius:6px;'
        f'padding:.32rem .7rem;cursor:pointer;text-decoration:none;'
        f'transition:all .15s;">{label}</a>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# SUGGESTION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _run_suggestion_engine(tag_list: list[str]) -> dict:
    """
    For each tag, tries moving it to every earlier position.
    Returns the best single-move gain per equipment and per system code.
    """
    if len(tag_list) < 2:
        return {"eq_suggestions": [], "sc_suggestions": [], "baseline_pcts": {}}

    baseline = cascade_allocate(tag_list)

    def _eq_pct(alloc, tag):
        t = alloc[alloc["Equipment_Tag_No."] == tag]
        d = t["Demand_Qty"].sum(); a = t["Allocated_Qty"].sum()
        return round(min(100.0, a/d*100), 2) if d > 0 else 100.0

    def _sc_pct(alloc, tag, code):
        t = alloc[(alloc["Equipment_Tag_No."]==tag)&(alloc["Lining_System_Code"]==code)]
        d = t["Demand_Qty"].sum(); a = t["Allocated_Qty"].sum()
        return round(min(100.0, a/d*100), 2) if d > 0 else 100.0

    baseline_pcts = {t: _eq_pct(baseline, t) for t in tag_list}
    eq_suggestions, sc_suggestions = [], []

    for i, target in enumerate(tag_list):
        if i == 0: continue
        base_pct = baseline_pcts[target]
        best_gain = 0; best_pos = i; best_new_pct = base_pct
        for new_pos in range(0, i):
            new_order = [t for t in tag_list if t != target]
            new_order.insert(new_pos, target)
            alloc   = cascade_allocate(new_order)
            new_pct = _eq_pct(alloc, target)
            gain    = new_pct - base_pct
            if gain > best_gain:
                best_gain = gain; best_pos = new_pos; best_new_pct = new_pct
        if best_gain > 0.4:
            eq_suggestions.append({
                "tag": target, "current_pos": i+1, "suggest_pos": best_pos+1,
                "current_pct": base_pct, "new_pct": best_new_pct,
                "gain": round(best_gain, 1),
            })

    for i, tag in enumerate(tag_list):
        if i == 0: continue
        tag_dm = dm[dm["Equipment_Tag_No."] == tag]
        for code in sorted(tag_dm["Lining_System_Code"].unique(), key=lambda x: int(x)):
            sname    = tag_dm[tag_dm["Lining_System_Code"]==code]["Lining_System_Short_Name"].iloc[0]
            base_pct = _sc_pct(baseline, tag, code)
            best_gain = 0; best_pos = i; best_new_pct = base_pct
            for new_pos in range(0, i):
                new_order = [t for t in tag_list if t != tag]
                new_order.insert(new_pos, tag)
                alloc   = cascade_allocate(new_order)
                new_pct = _sc_pct(alloc, tag, code)
                gain    = new_pct - base_pct
                if gain > best_gain:
                    best_gain = gain; best_pos = new_pos; best_new_pct = new_pct
            if best_gain > 0.4:
                sc_suggestions.append({
                    "tag": tag, "code": code, "sname": sname,
                    "current_pos": i+1, "suggest_pos": best_pos+1,
                    "current_pct": base_pct, "new_pct": best_new_pct,
                    "gain": round(best_gain, 1), "is_full": best_new_pct >= 99.9,
                })

    eq_suggestions.sort(key=lambda x: x["gain"], reverse=True)
    sc_suggestions.sort(key=lambda x: (x["is_full"], x["gain"]), reverse=True)
    return {
        "eq_suggestions": eq_suggestions[:5],
        "sc_suggestions": sc_suggestions[:8],
        "baseline_pcts":  baseline_pcts,
    }


def render_suggestion_panel(tag_list: list[str], panel_key: str) -> None:
    """Renders the Smart Reordering Suggestion panel."""
    st.markdown(
        '<div class="sec-hdr" style="margin-top:1.4rem;">'
        '💡 Smart Reordering Suggestions</div>', unsafe_allow_html=True)
    st.caption("Each suggestion shows the single best position change for one equipment "
               "or system code. Moving it there frees up inventory for that item first.")

    with st.spinner("Analysing reorder scenarios…"):
        result = _run_suggestion_engine(tag_list)

    eq_sugg = result["eq_suggestions"]
    sc_sugg = result["sc_suggestions"]

    if not eq_sugg and not sc_sugg:
        return  # hide silently when no improvements found

    c_eq, c_sc = st.columns(2, gap="large")

    with c_eq:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:.65rem;'
            'font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
            'color:var(--amber);margin-bottom:.6rem;"> By Equipment</div>',
            unsafe_allow_html=True)
        if not eq_sugg:
            st.caption("No equipment-level gains found.")
        for s in eq_sugg:
            gc = "#10B981" if s["new_pct"] >= 99.9 else "#F59E0B"
            tn = eq_master.set_index("Equipment_Tag_No.")["Name"].get(s["tag"], s["tag"])
            tag_label = f"{s['tag']}  ·  {tn[:24]}"
            move_label = (
                f"Move #{s['current_pos']} → #{s['suggest_pos']}  ·  "
                f"{s['current_pct']:.1f}% → {s['new_pct']:.1f}%"
                + ("  ✅ Full completion!" if s['new_pct'] >= 99.9 else "")
            )
            st.markdown(
                f'<div class="card" style="margin-bottom:.45rem;padding:.75rem 1rem;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:.82rem;font-weight:700;color:var(--t0);">{tag_label}</span>'
                f'<span style="font-family:JetBrains Mono,monospace;font-size:.75rem;'
                f'color:{gc};font-weight:700;">+{s["gain"]:.1f}%</span></div>'
                f'<div style="font-size:.76rem;color:var(--t2);margin-top:.35rem;">'
                f'{move_label}</div></div>',
                unsafe_allow_html=True)

    with c_sc:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:.65rem;'
            'font-weight:700;letter-spacing:.1em;text-transform:uppercase;'
            'color:var(--amber);margin-bottom:.6rem;"> By System Code</div>',
            unsafe_allow_html=True)
        if not sc_sugg:
            st.caption("No system-code-level gains found.")
        for s in sc_sugg:
            gc   = "#10B981" if s["is_full"] else "#F59E0B"
            full = ('  <span style="background:rgba(16,185,129,.15);color:#10B981;'
                    'font-size:.65rem;padding:.1rem .4rem;border-radius:3px;">100% COMPLETE</span>'
                    if s["is_full"] else "")
            tn = eq_master.set_index("Equipment_Tag_No.")["Name"].get(s["tag"], s["tag"])
            sc_label   = f"Code {s['code']}  {s['sname']}  ·  {s['tag']} {tn[:20]}"
            sc_move    = (
                f"Move #{s['current_pos']} → #{s['suggest_pos']}  ·  "
                f"{s['current_pct']:.1f}% → {s['new_pct']:.1f}%"
                + ("  ✅ 100% COMPLETE" if s["is_full"] else "")
            )
            st.markdown(
                f'<div class="card" style="margin-bottom:.45rem;padding:.75rem 1rem;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:.8rem;font-weight:600;color:var(--t0);">{sc_label}</span>'
                f'<span style="font-family:JetBrains Mono,monospace;font-size:.75rem;'
                f'color:{gc};font-weight:700;">+{s["gain"]:.1f}%</span></div>'
                f'<div style="font-size:.76rem;color:var(--t2);margin-top:.3rem;">'
                f'{sc_move}</div></div>',
                unsafe_allow_html=True)


# ── Type/Description label helper ─────────────────────────────────────────────
def _eq_label(tag: str) -> str:
    """Returns 'TAG  —  Name  |  Type  |  Desc' for display labels."""
    row = eq_master[eq_master["Equipment_Tag_No."] == tag]
    if row.empty: return tag
    r = row.iloc[0]
    type_s = str(r.get("Type","") or "").strip()
    desc_s = str(r.get("Substrate","") or "").strip()
    name_s = str(r.get("Name","") or "").strip()
    parts  = [name_s]
    if type_s and type_s not in ("nan","—"): parts.append(type_s)
    if desc_s and desc_s not in ("nan","—"): parts.append(desc_s[:28])
    return f"{tag}  —  " + "  |  ".join(parts)


def plotly_mat_table(df: pd.DataFrame, key_suffix: str, height: int = 380,
                     show_sqm: bool = False, tag: str = "", code: str = "",
                     allocated_label: str = "Allocated") -> None:
    """Colour-coded material table. If show_sqm=True, adds SQM columns after qty cols."""
    base_cols = ["Material_Code", "Material_Name", "UOM",
                 "Demand_Qty", "Allocated_Qty", "Shortfall_Qty", "Fulfillment_Pct"]
    avail_cols = [c for c in base_cols if c in df.columns]
    df2 = df[avail_cols].copy()

    # Add Ordered_Qty if available
    if "Ordered_Qty" in df.columns:
        df2.insert(df2.columns.get_loc("Allocated_Qty"), "Ordered_Qty", df["Ordered_Qty"])

    rename_map = {
        "Material_Code":  "Code",
        "Material_Name":  "Material Name",
        "UOM":            "UOM",
        "Demand_Qty":     "Demand",
        "Ordered_Qty":    "On Order",
        "Allocated_Qty":  allocated_label,
        "Shortfall_Qty":  "Shortfall",
        "Fulfillment_Pct":"Fulfil %",
    }
    df2 = df2.rename(columns=rename_map)

    # SQM columns — per-material, based on each material's own fulfillment
    if show_sqm and tag and code:
        total_sqm_sc = sqm_ref[
            (sqm_ref["Equipment_Tag_No."]==tag) &
            (sqm_ref["Lining_System_Code"]==code)
        ]["Total_SQM"].sum()
        # Each material gets SQM proportional to its own fulfillment rate
        mat_fulfill = df["Fulfillment_Pct"].values / 100.0 if "Fulfillment_Pct" in df.columns else                       (df["Allocated_Qty"] / df["Demand_Qty"].replace(0, np.nan)).fillna(1.0).clip(0,1).values
        df2["SQM Total"]   = round(total_sqm_sc, 2)
        df2["SQM Done"]    = (total_sqm_sc * mat_fulfill).round(2)
        df2["SQM Deficit"] = (total_sqm_sc * (1 - mat_fulfill)).round(2)
        df2["SQM Done %"]  = (mat_fulfill * 100).round(1)

    # Pre-computed per-row SQM columns (for combined/aggregated tables)
    elif {"SQM_Total","SQM_Done","SQM_Deficit"}.issubset(df.columns):
        df2["SQM Total"]   = df["SQM_Total"].values
        df2["SQM Done"]    = df["SQM_Done"].values
        df2["SQM Deficit"] = df["SQM_Deficit"].values
        df2["SQM Done %"]  = np.where(
            df["SQM_Total"].values > 0,
            (df["SQM_Done"].values / df["SQM_Total"].replace(0, np.nan).values * 100),
            100.0
        ).round(1)

    fmt = {
        "Demand":          "{:,.3f}",
        allocated_label:   "{:,.3f}",
        "Shortfall":       "{:,.3f}",
        "Fulfil %":        "{:.1f}%",
    }
    if "On Order" in df2.columns:
        fmt["On Order"] = "{:,.3f}"
    if "SQM Total" in df2.columns:
        fmt.update({"SQM Total":"{:,.2f}","SQM Done":"{:,.2f}",
                    "SQM Deficit":"{:,.2f}","SQM Done %":"{:.1f}%"})

    fulfil_col = "Fulfil %"

    def style_row(row):
        pct = row.get(fulfil_col, 100)
        if pd.isna(pct): pct = 100.0
        if pct >= 100:
            bg, tc = "rgba(16,185,129,0.12)", "#10B981"
        elif pct >= 90:
            bg, tc = "rgba(249,115,22,0.12)",  "#F97316"
        elif pct >= 80:
            bg, tc = "rgba(234,179,8,0.12)",   "#EAB308"
        else:
            bg, tc = "rgba(239,68,68,0.12)",   "#EF4444"
        styles = [f"background-color:{bg}"] * len(row)
        fidx = list(row.index).index(fulfil_col) if fulfil_col in row.index else -1
        if fidx >= 0:
            styles[fidx] = f"background-color:{bg};color:{tc};font-weight:700"
        return styles

    styled_df = df2.style.apply(style_row, axis=1).format(fmt)
    st.dataframe(styled_df, hide_index=True, use_container_width=True,
                 height=height, key=f"tbl_{key_suffix}")



# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
# DESIGN INTEGRATION — theme toggle + inline SVG helpers (gauge + hbar)
# ─────────────────────────────────────────────────────────────────────────────
if "sme_theme" not in st.session_state:
    st.session_state.sme_theme = "dark"

# §2c — when False, the SME's own sidebar block (theme toggle, logo, branding,
# project/inventory/session panels) is suppressed so only the ERP's default nav
# shows. The theme CSS attribute is still applied outside the block.
_SME_LEGACY_SIDEBAR = False

def _apply_theme_attr():
    """Inject the data-sme-theme attribute on <html> so the light/dark CSS
    overrides take effect. Calc/data flow is untouched."""
    mode = st.session_state.get("sme_theme", "dark")
    st.markdown(
        f"""<script>
        (function(){{
          try {{
            const r = (window.parent || window).document.documentElement;
            r.setAttribute('data-sme-theme', '{mode}');
          }} catch(e) {{}}
        }})();
        </script>""",
        unsafe_allow_html=True,
    )

def _fc(p: float) -> str:
    """Coverage colour — matches design.fc()."""
    if p >= 100: return "#10B981"
    if p >= 90:  return "#F97316"
    if p >= 80:  return "#EAB308"
    return "#EF4444"

def render_design_gauge(pct: float, can_sqm: float, total_sqm: float) -> str:
    """Half-gauge SVG mirroring the Claude design's renderGauge."""
    import math
    pct = max(0.0, min(100.0, float(pct or 0)))
    w, h, cx, cy, R = 300, 168, 150, 158, 115
    sA = -math.pi
    vA = sA + (pct / 100.0) * math.pi
    def arc(r, s, e):
        x1, y1 = cx + r * math.cos(s), cy + r * math.sin(s)
        x2, y2 = cx + r * math.cos(e), cy + r * math.sin(e)
        large = 1 if (e - s) > math.pi else 0
        return f"M {x1:.1f} {y1:.1f} A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f}"
    col = _fc(pct)
    val_arc = (
        f'<path d="{arc(R, sA, vA)}" fill="none" stroke="{col}" '
        f'stroke-width="20" stroke-linecap="round"/>'
        if pct > 0 else ""
    )
    # R20.4 EDIT: returns content with NO leading indent so Streamlit's
    # markdown recognizes the opening <div> as a type-6 HTML block. The
    # SME's original 4-space-indented return value caused the SVG to
    # render as an indented code block (raw text) under some Streamlit
    # versions. Inner indentation inside <svg> is irrelevant because
    # everything inside an HTML block passes through as raw HTML.
    return (
f"""<div class="sme-viz-card">
<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">
<path d="{arc(R, sA, sA + .5*math.pi)}" fill="none" stroke="rgba(239,68,68,.18)" stroke-width="22"/>
<path d="{arc(R, sA + .5*math.pi, sA + .7*math.pi)}" fill="none" stroke="rgba(234,179,8,.18)" stroke-width="22"/>
<path d="{arc(R, sA + .7*math.pi, sA + .85*math.pi)}" fill="none" stroke="rgba(249,115,22,.18)" stroke-width="22"/>
<path d="{arc(R, sA + .85*math.pi, 0)}" fill="none" stroke="rgba(16,185,129,.18)" stroke-width="22"/>
<path d="{arc(R, sA, 0)}" fill="none" stroke="rgba(128,128,128,.18)" stroke-width="20"/>
{val_arc}
<text x="{cx-R+2}" y="{cy+20}" fill="#94A3B8" font-size="10" font-family="JetBrains Mono, monospace">0%</text>
<text x="{cx+R-22}" y="{cy+20}" fill="#94A3B8" font-size="10" font-family="JetBrains Mono, monospace">100%</text>
<text x="{cx}" y="{cy-20}" text-anchor="middle" fill="{col}" font-size="32" font-weight="800" font-family="JetBrains Mono, monospace">{pct:.1f}%</text>
<text x="{cx}" y="{cy-2}" text-anchor="middle" fill="#94A3B8" font-size="11" font-family="Inter, sans-serif">Overall Coverage</text>
<text x="{cx}" y="{cy+14}" text-anchor="middle" fill="#94A3B8" font-size="10" font-family="JetBrains Mono, monospace">{can_sqm:,.1f} / {total_sqm:,.1f} SQM</text>
</svg>
<div class="sme-viz-legend">
<span>■ Available: {can_sqm:,.1f} SQM</span>
<span>■ Shortfall: {max(0.0, total_sqm-can_sqm):,.1f} SQM</span>
</div>
</div>"""
    )

def render_design_hbar(data: list[dict], title: str = "") -> str:
    """Horizontal bar chart SVG mirroring the Claude design's renderHBar.
    data: list of {"label": str, "val": float (0–100)} dicts."""
    if not data:
        return f'<div class="sme-viz-card">{title}<div style="color:var(--t4);font-size:.75rem;">No data.</div></div>'
    w, bH, gap, padL, padR = 460, 24, 7, 140, 60
    iW = w - padL - padR
    maxV = max([float(d.get("val") or 0) for d in data] + [1.0])
    total_h = len(data) * (bH + gap) + 16
    rows = []
    for i, d in enumerate(data):
        v = float(d.get("val") or 0)
        lbl = str(d.get("label", ""))[:20]
        y = i * (bH + gap) + 6
        bW = max(2.0, (v / maxV) * iW)
        col = _fc(v)
        rows.append(
            f'<text x="{padL-7}" y="{y+bH/2+4}" text-anchor="end" '
            f'fill="#94A3B8" font-size="11" font-family="Inter">{lbl}</text>'
            f'<rect x="{padL}" y="{y}" width="{iW}" height="{bH}" rx="4" '
            f'fill="rgba(128,128,128,.12)"/>'
            f'<rect x="{padL}" y="{y}" width="{bW:.1f}" height="{bH}" rx="4" '
            f'fill="{col}" opacity=".85"/>'
            f'<text x="{padL+bW+5:.1f}" y="{y+bH/2+4}" fill="#94A3B8" '
            f'font-size="11" font-family="JetBrains Mono, monospace">{v:.1f}%</text>'
        )
    title_html = (
        f'<div style="font-family:JetBrains Mono,monospace;font-size:.6rem;'
        f'font-weight:700;letter-spacing:.13em;text-transform:uppercase;'
        f'color:var(--t4);margin-bottom:.5rem;">{title}</div>'
        if title else ""
    )
    # R20.4 EDIT: zero-indent for the same reason as render_design_gauge —
    # opening tag must be at column 0 so Streamlit's markdown treats the
    # whole thing as an HTML block, not an indented code block.
    return (
f"""<div class="sme-viz-card">
{title_html}
<svg viewBox="0 0 {w} {total_h}" preserveAspectRatio="xMidYMid meet">
{"".join(rows)}
</svg>
</div>"""
    )


# ═══════════════════════════════════════════════════════════════════════════════
# R20 EDIT: wrap from here to EOF in page_material_estimator(user).
# Everything below was previously module-level imperative rendering; we now
# defer it so Streamlit runs it on tab navigation rather than at module
# import (which would render the SME UI on the wrong page).
#
# Module-level globals (inv, recipe, equip_sc, dm, eq_master, sqm_ref,
# ALL_TAGS, INV_POOL_INIT, INV_ORDERED_INIT) are reassigned inside the
# wrapper via `global` so the SME's cached helpers (cascade_allocate,
# tag_fulfillment, syscode_fulfillment, sqm_can_do) which read these via
# closure see the site-scoped data.
# ═══════════════════════════════════════════════════════════════════════════════
def page_material_estimator(user: dict) -> None:
    """Entry point called by main.py when the user clicks the Material
    Estimator nav radio. `user` is the dict returned by auth._require_login
    (keys: username, role, site_id, …).
    """
    global inv, recipe, equip_sc, dm, eq_master, sqm_ref
    global ALL_TAGS, INV_POOL_INIT, INV_ORDERED_INIT

    # 1. Surface auth context for SME helpers that read session_state.
    st.session_state["_authenticated"]  = True
    st.session_state["_login_username"] = user.get("username") or "user"
    # §2a — Admin has no bound site, so they'd default to HQ and see zeros.
    # Give admin a sidebar single-site picker over the seeded sites instead.
    if (user.get("role") or "").lower() == "admin":
        _sites = D.get_sites() or ["HQ"]
        _site_id_p = st.sidebar.selectbox("🧪 Estimator site", _sites,
                                          key="_sme_admin_site")
    else:
        _site_id_p = user.get("site_id") or "HQ"
    st.session_state["_login_site_id"]  = _site_id_p

    # 2. If site changed since last render, clear the cascade-allocate cache
    # (which reads module-level dm/INV_POOL_INIT via closure).
    if st.session_state.get("_sme_last_site") != _site_id_p:
        try:
            _cached_cascade_allocate.clear()
        except Exception:
            pass
        try:
            load_all.clear()
        except Exception:
            pass
        st.session_state["_sme_last_site"] = _site_id_p

    # 3. Load site-scoped data into module globals so SME helpers can read.
    inv, recipe, equip_sc, dm, eq_master, sqm_ref = load_all(_site_id_p)
    if eq_master is not None and not eq_master.empty:
        ALL_TAGS = sorted(eq_master["Equipment_Tag_No."].tolist())
    else:
        ALL_TAGS = []
    if inv is not None and not inv.empty:
        INV_POOL_INIT    = inv.set_index("Material_Code")["Available_Qty"].to_dict()
        INV_ORDERED_INIT = inv.set_index("Material_Code")["Ordered_Qty"].to_dict()
    else:
        INV_POOL_INIT, INV_ORDERED_INIT = {}, {}

    if "session_tags" not in st.session_state:
        st.session_state.session_tags = []
    if "_session_key" not in st.session_state:
        import uuid as _uuid
        st.session_state["_session_key"] = str(_uuid.uuid4())
    # R20.3 EDIT: theme key MUST be initialized per render. The original
    # SME module-level `if "sme_theme" not in st.session_state:` at line
    # ~3074 only runs once at server-boot import time (when no user
    # session is bound), so it never lands in real user sessions. The
    # sidebar's Dark/Light toggle reads st.session_state.sme_theme and
    # crashes with AttributeError if it's missing.
    if "sme_theme" not in st.session_state:
        st.session_state.sme_theme = "dark"

    # Refresh location/type dropdown sources (no-op when DB unchanged).
    try:
        _refresh_location_order()
    except Exception:
        pass
    try:
        _refresh_type_order()
    except Exception:
        pass

    # 4. Scope the SME's st.download_button monkey-patch to this render
    # (Correction #2 from R17 forbade global patching).
    _orig_dl_button = st.download_button
    st.download_button = _SME_SECURE_DOWNLOAD_BUTTON
    try:
        # ── BEGIN ORIGINAL SME IMPERATIVE BODY (indented) ─────────────────────
        # ─────────────────────────────────────────────────────────────────────────────
        # §2c — apply the theme CSS even though the SME sidebar is hidden, then
        # gate the legacy sidebar chrome behind the flag (off → ERP nav only).
        _apply_theme_attr()
        if _SME_LEGACY_SIDEBAR:
            # Theme toggle — design integration (light/dark mode switch)
            _ttl, _ttr = st.columns(2, gap="small")
            with _ttl:
                if st.button("🌙 Dark",
                             key="_sme_theme_dark",
                             use_container_width=True,
                             type=("primary" if st.session_state.sme_theme == "dark" else "secondary")):
                    st.session_state.sme_theme = "dark"
                    st.rerun()
            with _ttr:
                if st.button("☀ Light",
                             key="_sme_theme_light",
                             use_container_width=True,
                             type=("primary" if st.session_state.sme_theme == "light" else "secondary")):
                    st.session_state.sme_theme = "light"
                    st.rerun()
            _apply_theme_attr()

            if os.path.exists(LOGO_PATH):
                st.image(LOGO_PATH, width=140)
            st.markdown("""
    <div style="padding:.3rem 0 1.2rem">
      <div style="font-family:'JetBrains Mono',monospace;font-size:1rem;
          font-weight:700;color:#F59E0B;">SME</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:.56rem;
          letter-spacing:.18em;text-transform:uppercase;color:var(--t5);margin-top:2px;">
Smart Material Estimator v3</div>
    </div>""", unsafe_allow_html=True)

            st.markdown('<div class="sec-hdr">📍 Project Overview</div>', unsafe_allow_html=True)
            loc_counts = eq_master.groupby("Location")["Equipment_Tag_No."].count()
            for loc in LOCATION_ORDER:
                cnt   = loc_counts.get(loc, 0)
                badge = loc_badge(loc)
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;margin-bottom:.35rem;">'
                    f'{badge}'
                    f'<span style="font-family:\'JetBrains Mono\',monospace;'
                    f'font-size:.75rem;color:var(--t3);">{cnt} equip.</span></div>',
                    unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div class="sec-hdr">📦 Inventory</div>', unsafe_allow_html=True)
            st.caption(f"📦 {len(inv)} materials  ·  "
                       f"⚠️ {(inv['Available_Qty']==0).sum()} at zero stock")

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown('<div class="sec-hdr">📋 Session</div>', unsafe_allow_html=True)
            n_sess = len(st.session_state.session_tags)
            if n_sess:
                for t in st.session_state.session_tags:
                    st.caption(f"  · {t}")
                if st.button("🗑 Clear Session", key="clear_sidebar"):
                    st.session_state.session_tags = []
                    st.rerun()
            else:
                st.caption("No equipment added yet.")

            # R20.2 TEMP: diagnostic panel — helps debug why Available_Qty/
            # Ordered_Qty are zero. Shows the data overlap between
            # sme_recipe Material_Codes and ERP inventory rows. Remove once
            # the zero-qty issue is resolved.
            with st.expander("🔬 Data diagnostic", expanded=False):
                try:
                    _conn_diag = D.get_connection()
                    _rec_codes = set(_r[0] for _r in _conn_diag.execute(
                        "SELECT DISTINCT TRIM(Material_Code) FROM sme_recipe "
                        "WHERE Material_Code IS NOT NULL"
                    ).fetchall())
                    _inv_codes = set(_r[0] for _r in _conn_diag.execute(
                        "SELECT DISTINCT TRIM(COALESCE(Material_Code,'')) FROM inventory "
                        "WHERE COALESCE(Material_Code,'') <> ''"
                    ).fetchall())
                    _matching = _rec_codes & _inv_codes
                    _missing_in_inv = _rec_codes - _inv_codes
                    # Stock check at current site
                    _site = st.session_state.get("_login_site_id") or "HQ"
                    _live = D.load_live_inventory(_conn_diag, site_id=_site)
                    _live_matching = _live[_live["Material_Code"].isin(_matching)] if not _live.empty else _live
                    _with_stock = (
                        _live_matching[_live_matching["Current_Stock"] > 0]
                        if not _live_matching.empty else _live_matching
                    )
                    _conn_diag.close()
                    st.caption(f"Site_ID in use: **{_site}**")
                    st.caption(f"sme_recipe Material_Codes: **{len(_rec_codes)}**")
                    st.caption(f"ERP inventory Material_Codes: **{len(_inv_codes)}**")
                    st.caption(f"Matching (in both): **{len(_matching)}**")
                    st.caption(
                        f"Of matching, with Current_Stock>0 at {_site}: "
                        f"**{len(_with_stock)}**"
                    )
                    if _missing_in_inv:
                        _sample = sorted(_missing_in_inv)[:5]
                        st.caption(
                            f"Recipe codes NOT in ERP inventory "
                            f"({len(_missing_in_inv)} total): "
                            f"`{', '.join(_sample)}…`"
                        )
                    if len(_matching) == 0:
                        st.warning(
                            "⚠️ **Zero Material_Code overlap.** Add inventory "
                            "rows with Material_Code values that match the "
                            "recipe (or extend the bootstrap to seed "
                            "inventory from Materials_DetailsAvailable_Qty.xlsx)."
                        )
                except Exception as _e:
                    st.caption(f"(diagnostic failed: {type(_e).__name__}: {_e})")

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("""<div style="font-family:'JetBrains Mono',monospace;font-size:.6rem;
letter-spacing:.08em;color:var(--t5);">
        🟢 100%  🟠 90–99%  🟡 80–89%  🔴 &lt;80%</div>""",
                unsafe_allow_html=True)

        # ─────────────────────────────────────────────────────────────────────────────
        # STICKY HEADER
        # ─────────────────────────────────────────────────────────────────────────────
        # R20.2 EDIT: fallback to a 1px transparent placeholder when no
        # logo file is present. An empty interpolation here would leave a
        # blank line inside the markdown HTML block, closing it; the next
        # <div> would then render as an indented code block.
        _hdr_logo = (
            f'<img src="data:image/png;base64,{_logo_b64()}" '
            f'style="height:34px;border-radius:6px;flex-shrink:0;">'
            if _logo_b64() else '<span style="display:inline-block;width:0;"></span>'
        )
        st.markdown(f"""
<div class="sticky-header-wrap">
  <div style="display:flex;align-items:center;gap:1rem;">
    {_hdr_logo}
    <div style="display:flex;flex-direction:column;gap:.1rem;">
      <span style="font-family:'JetBrains Mono',monospace;font-size:1.05rem;font-weight:700;color:var(--t0);letter-spacing:-.01em;line-height:1.2;">
        Smart Material Estimator &amp; Planner</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.58rem;color:var(--t5);letter-spacing:.08em;text-transform:uppercase;">
        System-code level · Cascading allocation · Priority-based</span>
    </div>
    <div style="margin-left:auto;display:flex;align-items:center;gap:.6rem;flex-shrink:0;">
      <span style="font-family:'JetBrains Mono',monospace;font-size:.58rem;color:var(--t5);letter-spacing:.1em;text-transform:uppercase;background:var(--amber-bg);border:1px solid rgba(245,158,11,.2);padding:.15rem .5rem;border-radius:99px;">v3</span>
      <span title="System online" style="width:7px;height:7px;border-radius:50%;background:var(--green);display:inline-block;box-shadow:0 0 7px var(--green-glow);"></span>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        # ─────────────────────────────────────────────────────────────────────────────
        # CUSTOM HAMBURGER (JS-injected — overrides Streamlit's invisible/missing toggle)
        # ─────────────────────────────────────────────────────────────────────────────
        import streamlit.components.v1 as _components
        _components.html("""
<script>
(function(){
  const PARENT = window.parent.document;
  const BTN_ID = 'ge-custom-hamburger';

  function findToggle() {
    return PARENT.querySelector('[data-testid="stSidebarCollapsedControl"] button')
|| PARENT.querySelector('[data-testid="collapsedControl"] button')
|| PARENT.querySelector('[data-testid="stSidebarCollapseButton"] button')
|| PARENT.querySelector('[data-testid="stSidebarCollapseButton"]')
|| PARENT.querySelector('[data-testid="stSidebar"] button[kind="header"]')
|| PARENT.querySelector('[data-testid="stSidebar"] [data-testid="stSidebarHeader"] button');
  }

  function makeBtn() {
    if (PARENT.getElementById(BTN_ID)) return;
    const btn = PARENT.createElement('button');
    btn.id = BTN_ID;
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Toggle sidebar');
    btn.title = 'Toggle sidebar';
    btn.textContent = '\\u2630';
    btn.style.cssText = [
      'position:fixed','top:14px','left:16px','z-index:1000001',
      'width:40px','height:40px',
      'background:rgba(245,158,11,0.12)',
      'border:1.5px solid #F59E0B',
      'border-radius:6px',
      'color:#F59E0B',
      'font-size:22px','font-weight:700','line-height:1',
      'font-family:Inter,Helvetica,sans-serif',
      'cursor:pointer',
      'display:flex','align-items:center','justify-content:center',
      'box-shadow:0 0 0 1px #F59E0B,0 2px 12px rgba(245,158,11,0.45)',
      'padding:0','margin:0',
      'transition:all .15s ease'
    ].join(';') + ';';
    btn.addEventListener('mouseenter', function(){
      btn.style.background = 'rgba(245,158,11,0.22)';
      btn.style.transform  = 'translateY(-1px)';
    });
    btn.addEventListener('mouseleave', function(){
      btn.style.background = 'rgba(245,158,11,0.12)';
      btn.style.transform  = 'translateY(0)';
    });
    btn.addEventListener('click', function(e){
      e.preventDefault();
      e.stopPropagation();
      const t = findToggle();
      if (t) { t.click(); }
    });
    PARENT.body.appendChild(btn);
  }

  // Try immediately + retry until the DOM is ready
  makeBtn();
  let tries = 0;
  const iv = setInterval(function(){
    makeBtn();
    if (++tries > 40) clearInterval(iv);
  }, 250);

  // Keep button alive across Streamlit re-renders
  const obs = new MutationObserver(function(){
    if (!PARENT.getElementById(BTN_ID)) makeBtn();
  });
  obs.observe(PARENT.body, { childList: true, subtree: false });
})();
</script>
""", height=0, width=0)

        # ─────────────────────────────────────────────────────────────────────────────
        # DROPDOWN AUTO-CLOSE (F#7)
        # Streamlit multiselect keeps its menu open after each pick. Listen for clicks
        # on option elements and dispatch an ESC keydown so the menu collapses.
        # ─────────────────────────────────────────────────────────────────────────────
        _components.html("""
<script>
(function(){
  const PARENT = window.parent.document;
  if (PARENT.__sme_dropdown_autoclose) return;
  PARENT.__sme_dropdown_autoclose = true;

  function dismiss() {
    // 1) Press ESC on the currently-focused element (closes BaseWeb menus).
    const ev = new KeyboardEvent('keydown', {
      key: 'Escape', code: 'Escape',
      keyCode: 27, which: 27,
      bubbles: true, cancelable: true,
    });
    (PARENT.activeElement || PARENT.body).dispatchEvent(ev);
    // 2) Also click the body to lose focus from BaseWeb selects.
    setTimeout(() => {
      try { PARENT.activeElement && PARENT.activeElement.blur(); } catch(e){}
    }, 0);
  }

  PARENT.addEventListener('click', function(e){
    // Streamlit/BaseWeb option elements: li[role="option"] inside menu/listbox.
    const opt = e.target.closest('li[role="option"], [data-baseweb="menu"] li');
    if (opt) {
      setTimeout(dismiss, 30);
    }
  }, true);
})();
</script>
""", height=0, width=0)

        # ─────────────────────────────────────────────────────────────────────────────
        # TABS
        # ─────────────────────────────────────────────────────────────────────────────
        # R20 EDIT: removed "📦  Inventory" tab; `tab_consume` unpacking variable
        # dropped because the tab body block has been deleted. 8 tabs now (was 9).
        tab0, tab1, tab2, tab3, tab_eqrep, tab4, tab5, tab_master = st.tabs([
            "📊  Dashboard",
            "🔍  Selective Equipment Entry",
            "📦  Session Order Report",
            "📍  Location Report",
            "📋  Equipment Report",
            "⚙️  Execution Plan",
            "📈  Total Overview",
            "🗄️  Master Data",
        ])



        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 0 · DASHBOARD (Project Overview + Material Requirement & Procurement)
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab0:

            # ── Dashboard toggle ──────────────────────────────────────────────────────
            dash_view = st.radio(
                "View", ["📈 Project Overview", "🛒 Material Requirement & Procurement"],
                horizontal=True, key="dash_view", label_visibility="collapsed",
            )
            st.markdown("<hr>", unsafe_allow_html=True)

            # ── Shared filter controls (used by both views) ───────────────────────────
            st.markdown('<div class="sec-hdr">🎛 Filter</div>', unsafe_allow_html=True)
            df1, df2_col, df3, df4 = st.columns(4)
            with df1:
                sel_locations = st.multiselect(" Location", options=LOCATION_ORDER,
                                                default=LOCATION_ORDER, key="dash_loc")
            with df2_col:
                # Type options scoped to selected locations, then unioned with the
                # registered Types table (F#6) so newly-registered types appear even
                # before any equipment exists for them.
                _type_pool = eq_master[eq_master["Location"].isin(sel_locations)] \
                             if sel_locations else eq_master
                all_types_d = _get_all_types(_type_pool)
                sel_types = st.multiselect(" Type", options=all_types_d,
                                            default=all_types_d, key="dash_type")
            with df3:
                # System Code options scoped to selected locations + types
                _eq_pool = eq_master[
                    eq_master["Location"].isin(sel_locations) &
                    eq_master["Type"].str.strip().isin(sel_types)
                ] if (sel_locations or sel_types) else eq_master
                _tags_pool = _eq_pool["Equipment_Tag_No."].tolist()
                _code_pool = dm[dm["Equipment_Tag_No."].isin(_tags_pool)]
                all_codes_d = (
                    _code_pool[["Lining_System_Code","Lining_System_Short_Name"]]
                    .drop_duplicates()
                    .sort_values("Lining_System_Code", key=lambda x: x.astype(int))
                )
                code_opts_d = [f"Code {r.Lining_System_Code} – {r.Lining_System_Short_Name}"
                               for _, r in all_codes_d.iterrows()]
                sel_codes_raw = st.multiselect(" System Code", options=code_opts_d,
                                                default=code_opts_d, key="dash_code")
                sel_codes = [c.split(" – ")[0].replace("Code ","").strip() for c in sel_codes_raw]
            with df4:
                # Substrate options scoped to selected locations + types
                all_desc_d = sorted(_eq_pool["Substrate"].dropna().unique().tolist())
                sel_substrate = st.multiselect(" Substrate", options=all_desc_d,
                                                default=all_desc_d, key="dash_substrate")

            # ── Apply filters ─────────────────────────────────────────────────────────
            filtered_eq = eq_master[
                eq_master["Location"].isin(sel_locations) &
                eq_master["Type"].str.strip().isin(sel_types) &
                eq_master["Substrate"].isin(sel_substrate)
            ]
            if sel_codes:
                tags_w_code = dm[dm["Lining_System_Code"].isin(sel_codes)]["Equipment_Tag_No."].unique()
                filtered_eq = filtered_eq[filtered_eq["Equipment_Tag_No."].isin(tags_w_code)]
            filtered_tags = filtered_eq["Equipment_Tag_No."].tolist()

            filtered_dm = dm[
                (dm["Equipment_Tag_No."].isin(filtered_tags)) &
                (dm["Lining_System_Code"].isin(sel_codes))
            ].copy()

            # ── Correct SQM using sqm_ref (never sum from dm — inflated x n_materials) ─
            proj_sqm = sqm_ref[
                sqm_ref["Equipment_Tag_No."].isin(filtered_tags) &
                sqm_ref["Lining_System_Code"].isin(sel_codes)
            ]["Total_SQM"].sum()

            # ── Material demand aggregation ───────────────────────────────────────────
            f_demand = (
                filtered_dm.groupby(["Material_Code","Material_Name","UOM"], as_index=False)
                ["Demand_Qty"].sum()
            )
            f_demand = f_demand.merge(
                inv[["Material_Code","Available_Qty","Ordered_Qty"]], on="Material_Code", how="left"
            )
            f_demand["Available_Qty"] = f_demand["Available_Qty"].fillna(0)
            f_demand["Ordered_Qty"]   = f_demand["Ordered_Qty"].fillna(0)
            f_demand["Shortfall"]     = (f_demand["Demand_Qty"] - f_demand["Available_Qty"]).clip(lower=0).round(3)
            f_demand["Net_Shortfall"] = (
                f_demand["Demand_Qty"] - f_demand["Available_Qty"] - f_demand["Ordered_Qty"]
            ).clip(lower=0).round(3)
            f_demand["Coverage_Pct"]  = (
                f_demand["Available_Qty"].clip(upper=f_demand["Demand_Qty"]) /
                f_demand["Demand_Qty"].replace(0, np.nan) * 100
            ).fillna(100).clip(0,100).round(1)

            f_total_demand = f_demand["Demand_Qty"].sum()
            f_total_avail  = f_demand["Available_Qty"].clip(upper=f_demand["Demand_Qty"]).sum()
            f_total_short  = f_demand["Shortfall"].sum()
            f_total_net    = f_demand["Net_Shortfall"].sum()
            f_cov          = (f_total_avail / f_total_demand * 100) if f_total_demand > 0 else 100
            can_sqm        = round(proj_sqm * min(1.0, f_cov/100), 2)
            short_sqm      = round(proj_sqm - can_sqm, 2)

            # ── SQM-based drill-down (per Equipment × System Code) ───────────────────
            _pair_d = filtered_dm.merge(
                inv[["Material_Code", "Available_Qty"]], on="Material_Code", how="left"
            )
            _pair_d["Available_Qty"] = _pair_d["Available_Qty"].fillna(0)
            _pair_d["Avail_Cap"]     = _pair_d[["Demand_Qty", "Available_Qty"]].min(axis=1)
            _pair_agg = _pair_d.groupby(
                ["Equipment_Tag_No.", "Lining_System_Code"], as_index=False
            ).agg(_Demand=("Demand_Qty", "sum"), _Avail=("Avail_Cap", "sum"))
            _pair_agg["Coverage %"] = (
                _pair_agg["_Avail"] / _pair_agg["_Demand"].replace(0, np.nan) * 100
            ).fillna(100).clip(0, 100).round(1)

            _dd_sqm_pair = sqm_ref[
                sqm_ref["Equipment_Tag_No."].isin(filtered_tags) &
                sqm_ref["Lining_System_Code"].isin(sel_codes)
            ][["Equipment_Tag_No.", "Lining_System_Code", "Total_SQM"]].merge(
                _pair_agg[["Equipment_Tag_No.", "Lining_System_Code", "Coverage %"]],
                on=["Equipment_Tag_No.", "Lining_System_Code"], how="left"
            )
            _dd_sqm_pair["Coverage %"]    = _dd_sqm_pair["Coverage %"].fillna(100)
            _dd_sqm_pair["Coverable SQM"] = (_dd_sqm_pair["Total_SQM"] * _dd_sqm_pair["Coverage %"] / 100).round(2)
            _dd_sqm_pair["SQM Deficit"]   = (_dd_sqm_pair["Total_SQM"] - _dd_sqm_pair["Coverable SQM"]).round(2)
            _dd_sqm_pair = _dd_sqm_pair.rename(columns={
                "Equipment_Tag_No.":  "Equipment Tag",
                "Lining_System_Code": "System Code",
                "Total_SQM":          "Total SQM",
            })

            _dd_cov_sqm_df = (_dd_sqm_pair[
                ["Equipment Tag", "System Code", "Total SQM", "Coverage %", "Coverable SQM"]
            ].sort_values("Coverage %").reset_index(drop=True))

            _dd_def_sqm_df = (_dd_sqm_pair[_dd_sqm_pair["SQM Deficit"] > 0][
                ["Equipment Tag", "System Code", "Total SQM", "Coverable SQM", "SQM Deficit"]
            ].sort_values("SQM Deficit", ascending=False).reset_index(drop=True))

            # Shared KPI drill-down frames used by both Project Overview and Procurement views
            _dd_equip_df = filtered_eq[["Equipment_Tag_No.","Name","Location","Type","Substrate"]].reset_index(drop=True)
            _dd_sqm_df   = (sqm_ref[sqm_ref["Equipment_Tag_No."].isin(filtered_tags) &
                                    sqm_ref["Lining_System_Code"].isin(sel_codes)]
                            [["Equipment_Tag_No.","Lining_System_Code","Total_SQM"]]
                            .sort_values("Total_SQM", ascending=False).reset_index(drop=True))

            # ─────────────────────────────────────────────────────────────────────────
            if dash_view == "📈 Project Overview":
            # ─────────────────────────────────────────────────────────────────────────

                # KPI strip
                k1,k2,k3,k4,k5,k6,k7 = st.columns(7)
                _dd_cov_df   = f_demand[["Material_Code","Material_Name","Demand_Qty","Available_Qty","Coverage_Pct"]].sort_values("Coverage_Pct").reset_index(drop=True)
                _dd_def_df   = f_demand[f_demand["Shortfall"]>0][["Material_Code","Material_Name","Demand_Qty","Available_Qty","Shortfall"]].sort_values("Shortfall", ascending=False).reset_index(drop=True)
                _dd_crit_df  = f_demand[f_demand["Coverage_Pct"]<50][["Material_Code","Material_Name","Demand_Qty","Available_Qty","Coverage_Pct"]].sort_values("Coverage_Pct").reset_index(drop=True)
                with k1:
                    dbl_click_metric("Equipment", str(len(filtered_tags)), "t0_equip",
                        "Equipment List", _dd_equip_df,
                        help_text="Equipment tags matching current filter selection.")
                with k2:
                    dbl_click_metric("Total SQM", f"{proj_sqm:,.1f}", "t0_sqm",
                        "SQM by Equipment & System Code", _dd_sqm_df,
                        help_text="Remaining surface area (m²) after deducting daily consumption entries.")
                with k3:
                    dbl_click_metric("Available Coverage SQM", f"{can_sqm:,.2f}", "t0_cov_sqm",
                        "Coverable SQM by Equipment & System Code", _dd_cov_sqm_df,
                        help_text="Area (m²) coverable with currently available stock = Total SQM × Coverage %. Drill-down shows per-equipment SQM coverage.")
                with k4:
                    dbl_click_metric("SQM Deficit", f"{short_sqm:,.2f}", "t0_deficit",
                        "SQM Deficit by Equipment & System Code", _dd_def_sqm_df,
                        help_text="Area (m²) that cannot be completed = Total SQM − Coverable SQM. Drill-down shows per-equipment SQM deficit.")
                with k5:
                    dbl_click_metric("Overall Coverage", f"{f_cov:.1f}%", "t0_ov_cov",
                        "Coverable SQM by Equipment & System Code", _dd_cov_sqm_df,
                        delta=f"{f_cov-100:.1f}%",
                        help_text="Allocated Qty ÷ Demand Qty × 100 across all filtered materials. Drill-down shows per-equipment SQM coverage.")
                with k6:
                    dbl_click_metric("Shortfall SQM", f"{short_sqm:,.2f}", "t0_short_sqm",
                        "SQM Deficit by Equipment & System Code", _dd_def_sqm_df,
                        help_text="Area (m²) shortfall = Total SQM − Available Coverage SQM. Drill-down shows per-equipment SQM deficit.")
                with k7:
                    dbl_click_metric("Critical (<50%)", str(int((f_demand["Coverage_Pct"]<50).sum())), "t0_critical",
                        "Critical Materials (Coverage < 50%)", _dd_crit_df,
                        help_text="Materials where Available Qty covers less than 50% of total demand.")
                st.markdown("<br>", unsafe_allow_html=True)

                row1a, row1b = st.columns([1,1.6], gap="large")

                with row1a:
                    st.markdown('<div class="sec-hdr">🎯 Overall Coverage</div>', unsafe_allow_html=True)
                    # ── Design SVG gauge (Plotly duplicate removed) ──
                    st.markdown(
                        render_design_gauge(f_cov, can_sqm, proj_sqm),
                        unsafe_allow_html=True,
                    )

                    # Demand vs Available mini stacked bar
                    fig_dm = go.Figure()
                    fig_dm.add_trace(go.Bar(name="Available",x=["Inventory"],y=[f_total_avail],
                        marker_color="#10B981",marker_opacity=.8,
                        text=[f"{f_total_avail:,.0f}"],textposition="auto",
                        textfont=dict(family="JetBrains Mono",size=10)))
                    fig_dm.add_trace(go.Bar(name="Shortfall",x=["Inventory"],y=[f_total_short],
                        marker_color="#EF4444",marker_opacity=.8,
                        text=[f"{f_total_short:,.0f}"],textposition="auto",
                        textfont=dict(family="JetBrains Mono",size=10)))
                    fig_dm.update_layout(barmode="stack",paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",margin=dict(l=0,r=0,t=0,b=0),height=120,
                        showlegend=True,
                        legend=dict(orientation="h",y=1.15,x=0,
                                    font=dict(family="JetBrains Mono",size=9),bgcolor="rgba(0,0,0,0)"),
                        xaxis=dict(showgrid=False,showticklabels=False),
                        yaxis=dict(gridcolor="rgba(128,128,128,.1)",showticklabels=False))
                    st.plotly_chart(fig_dm, use_container_width=True, key="dash_dmini")

                with row1b:
                    st.markdown('<div class="sec-hdr">📍 Coverage by Location (SQM)</div>',
                                unsafe_allow_html=True)
                    loc_rows = []
                    for loc in sel_locations:
                        loc_tags = filtered_eq[filtered_eq["Location"]==loc]["Equipment_Tag_No."].tolist()
                        loc_dm_f = filtered_dm[filtered_dm["Equipment_Tag_No."].isin(loc_tags)]
                        if loc_dm_f.empty: continue
                        loc_agg = (loc_dm_f.groupby("Material_Code",as_index=False)["Demand_Qty"].sum()
                            .merge(inv[["Material_Code","Available_Qty"]],on="Material_Code",how="left"))
                        loc_agg["Available_Qty"] = loc_agg["Available_Qty"].fillna(0)
                        loc_d = loc_agg["Demand_Qty"].sum()
                        loc_a = loc_agg["Available_Qty"].clip(upper=loc_agg["Demand_Qty"]).sum()
                        loc_s = (loc_agg["Demand_Qty"]-loc_agg["Available_Qty"]).clip(lower=0).sum()
                        loc_c = (loc_a/loc_d*100) if loc_d>0 else 100
                        # ✅ Correct SQM using sqm_ref
                        loc_sqm = sqm_ref[
                            sqm_ref["Equipment_Tag_No."].isin(loc_tags) &
                            sqm_ref["Lining_System_Code"].isin(sel_codes)
                        ]["Total_SQM"].sum()
                        loc_can = round(loc_sqm * min(1.0, loc_c/100), 2)
                        loc_rows.append({"Location":loc,"Equipment":len(loc_tags),
                            "SQM":loc_sqm,"SQM_Can":loc_can,"SQM_Short":round(loc_sqm-loc_can,2),
                            "Demand":loc_d,"Available":loc_a,"Shortfall":loc_s,"Coverage_%":round(loc_c,1)})

                    if loc_rows:
                        loc_df = pd.DataFrame(loc_rows)
                        loc_colors_map = {"Brown Field":"#3B82F6","TRAIN J":"#F59E0B","TRAIN K":"#10B981"}
                        fig_loc = go.Figure()
                        for _, lr in loc_df.iterrows():
                            c = loc_colors_map.get(lr["Location"],"#94A3B8")
                            fig_loc.add_trace(go.Bar(
                                x=[lr["Location"]],y=[lr["SQM_Can"]],name=f'{lr["Location"]} Can Do',
                                marker_color=c,marker_opacity=.8,
                                text=[f'{lr["Coverage_%"]:.0f}%\n{lr["SQM_Can"]:,.0f} SQM'],
                                textposition="inside",textfont=dict(family="JetBrains Mono",size=10,color="#fff"),
                                showlegend=False))
                            fig_loc.add_trace(go.Bar(
                                x=[lr["Location"]],y=[lr["SQM_Short"]],name=f'{lr["Location"]} Deficit',
                                marker_color="#EF4444",marker_opacity=.6,
                                text=[f'{lr["SQM_Short"]:,.0f} SQM deficit'],
                                textposition="inside",textfont=dict(family="JetBrains Mono",size=9,color="#fff"),
                                showlegend=False))
                        fig_loc.update_layout(barmode="stack",paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",margin=dict(l=0,r=0,t=10,b=0),height=220,
                            xaxis=dict(tickfont=dict(family="JetBrains Mono",size=11)),
                            yaxis=dict(gridcolor="rgba(128,128,128,.08)",
                                       title=dict(text="SQM",font=dict(family="JetBrains Mono",size=9))),
                            font=dict(family="JetBrains Mono",size=10,color="rgba(148,163,184,.8)"))
                        st.plotly_chart(fig_loc, use_container_width=True, key="dash_loc_bar")

                        # Location stat cards
                        cols_loc = st.columns(len(loc_rows))
                        for col, lr in zip(cols_loc, loc_rows):
                            dot = ("🟢" if lr["Coverage_%"]>=100 else "🟠" if lr["Coverage_%"]>=90
                                   else "🟡" if lr["Coverage_%"]>=80 else "🔴")
                            loc_html = (
                                '<div class="card" style="text-align:center;padding:.7rem;">'
                                f'<div style="font-size:1.1rem;">{dot}</div>'
                                '<div style="font-family:\'JetBrains Mono\',monospace;font-size:.72rem;'
                                f'font-weight:700;color:var(--amber);margin:.2rem 0;">{lr["Location"]}</div>'
                                '<div style="font-family:\'JetBrains Mono\',monospace;font-size:1.1rem;'
                                f'font-weight:700;color:var(--t0);">{lr["Coverage_%"]:.1f}%</div>'
                                f'<div style="font-size:.68rem;color:var(--t3);">'
                                f'{lr["SQM_Can"]:,.0f} / {lr["SQM"]:,.0f} SQM</div>'
                                f'<div style="font-size:.65rem;color:var(--t3);">{lr["Equipment"]} equipment</div>'
                                '</div>'
                            )
                            col.markdown(loc_html, unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)
                row2a, row2b = st.columns(2, gap="large")

                with row2a:
                    st.markdown('<div class="sec-hdr"> Coverage by System Code (SQM)</div>',
                                unsafe_allow_html=True)
                    sc_rows = []
                    for code in sel_codes:
                        sc_dm = filtered_dm[filtered_dm["Lining_System_Code"]==code]
                        if sc_dm.empty: continue
                        sname = sc_dm["Lining_System_Short_Name"].iloc[0]
                        sc_agg = (sc_dm.groupby("Material_Code",as_index=False)["Demand_Qty"].sum()
                            .merge(inv[["Material_Code","Available_Qty"]],on="Material_Code",how="left"))
                        sc_agg["Available_Qty"] = sc_agg["Available_Qty"].fillna(0)
                        sc_d = sc_agg["Demand_Qty"].sum()
                        sc_a = sc_agg["Available_Qty"].clip(upper=sc_agg["Demand_Qty"]).sum()
                        sc_s = (sc_agg["Demand_Qty"]-sc_agg["Available_Qty"]).clip(lower=0).sum()
                        sc_c = (sc_a/sc_d*100) if sc_d>0 else 100
                        # ✅ Correct SQM
                        sc_sqm = sqm_ref[
                            sqm_ref["Equipment_Tag_No."].isin(filtered_tags) &
                            (sqm_ref["Lining_System_Code"]==code)
                        ]["Total_SQM"].sum()
                        sc_can = round(sc_sqm * min(1.0, sc_c/100), 2)
                        sc_rows.append({"Code":f"Code {code}","Short_Name":sname,
                            "SQM":sc_sqm,"SQM_Can":sc_can,"SQM_Short":round(sc_sqm-sc_can,2),
                            "Coverage_%":round(sc_c,1)})

                    if sc_rows:
                        sc_df = pd.DataFrame(sc_rows).sort_values("Coverage_%")
                        # ── Design integration: SVG horizontal-bar ──
                        st.markdown(
                            render_design_hbar(
                                [{"label": f"{r['Code']} – {str(r['Short_Name'])[:14]}",
                                  "val": float(r["Coverage_%"])}
                                 for _, r in sc_df.iterrows()],
                            ),
                            unsafe_allow_html=True,
                        )
                        # (Duplicate Plotly bar chart removed — design SVG above is canonical.)

                        sc_show = sc_df.copy()
                        sc_show.columns = ["Code","Short Name","SQM Total","Available Material Coverage (SQM)",
                                           "SQM Deficit","Coverage %"]
                        sc_show[["SQM Total","Available Material Coverage (SQM)","SQM Deficit"]] = (
                            sc_show[["SQM Total","Available Material Coverage (SQM)","SQM Deficit"]].round(1))
                        st.dataframe(sc_show,use_container_width=True,hide_index=True,
                                     key="dash_sc_tbl")

                with row2b:
                    st.markdown('<div class="sec-hdr">🧪 Coverage by Material</div>',
                                unsafe_allow_html=True)
                    mat_rows_d = f_demand.copy().sort_values("Coverage_Pct")
                    if not mat_rows_d.empty:
                        # ── Design integration: SVG horizontal-bar ──
                        st.markdown(
                            render_design_hbar(
                                [{"label": str(r["Material_Name"] or r["Material_Code"])[:18],
                                  "val": float(r["Coverage_Pct"])}
                                 for _, r in mat_rows_d.iterrows()],
                            ),
                            unsafe_allow_html=True,
                        )
                        # (Duplicate Plotly bar chart removed — design SVG above is canonical.)

                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown('<div class="sec-hdr">📋 Full Material Balance</div>',
                            unsafe_allow_html=True)
                tbl_d = f_demand.sort_values("Coverage_Pct").copy()
                tbl_show = tbl_d[["Material_Code","Material_Name","UOM",
                                   "Available_Qty","Ordered_Qty","Demand_Qty","Shortfall","Net_Shortfall","Coverage_Pct"]].copy()
                tbl_show.columns = ["Code","Material Name","UOM",
                                     "Available","On Order","Total Demand","Shortfall","Net Shortfall","Coverage %"]

                def _style_cov(row):
                    pct = row["Coverage %"]
                    if pd.isna(pct): pct = 100.0
                    if pct>=100:   bg,tc = "rgba(16,185,129,.1)","#10B981"
                    elif pct>=90:  bg,tc = "rgba(249,115,22,.1)","#F97316"
                    elif pct>=80:  bg,tc = "rgba(234,179,8,.1)", "#EAB308"
                    else:          bg,tc = "rgba(239,68,68,.1)", "#EF4444"
                    styles = [f"background-color:{bg}"]*len(row)
                    styles[-1] = f"background-color:{bg};color:{tc};font-weight:700"
                    return styles

                styled_tbl = tbl_show.style.apply(_style_cov,axis=1).format({
                    "Available":"{:,.3f}","On Order":"{:,.3f}",
                    "Total Demand":"{:,.3f}","Shortfall":"{:,.3f}",
                    "Net Shortfall":"{:,.3f}","Coverage %":"{:.1f}%"})
                st.dataframe(styled_tbl,use_container_width=True,hide_index=True,
                             height=50+len(tbl_show)*35,key="dash_mat_tbl")

                # ── Stock-only materials (in inventory but not used in any recipe/demand) ──
                # R20.1 EDIT: filter to ONLY show SME-tracked materials
                # (i.e., materials that appear in any sme_recipe row but
                # aren't in the current plan's demand). Bolts, gloves, and
                # other generic warehouse items are excluded because they
                # have no recipe membership — they shouldn't appear in the
                # SME portal's "no demand" section.
                recipe_codes = set(dm["Material_Code"].unique())
                _all_sme_codes = set(recipe["Material_Code"].unique())
                stock_only = inv[
                    (~inv["Material_Code"].isin(recipe_codes))
                    & (inv["Material_Code"].isin(_all_sme_codes))
                ].copy()
                if not stock_only.empty:
                    st.markdown(
                        '<div class="sec-hdr" style="margin-top:.8rem;">'
                        '📦 Stock-Only Materials (No Demand in Any System Code)</div>',
                        unsafe_allow_html=True)
                    st.caption(
                        "These materials are in your inventory but are not used "
                        "in any lining system recipe. No demand is generated for them.")
                    so_show = stock_only[["Material_Code","Material_Name","UOM",
                                           "Available_Qty","Ordered_Qty"]].copy()
                    so_show["Ordered_Qty"] = so_show["Ordered_Qty"].fillna(0)
                    so_show.columns = ["Code","Material Name","UOM","Available","On Order"]
                    st.dataframe(so_show, use_container_width=True, hide_index=True,
                                 key="dash_stock_only")

                da, da_pdf, db = st.columns(3)
                with da:
                    st.download_button("⬇ Excel — Material Balance",
                        data=generate_excel_report(tbl_show.reset_index(drop=True), "Material Balance", color_scheme="dashboard"),
                        file_name="dashboard_material_balance.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)
                with da_pdf:
                    _pdf_download_button("⬇ PDF — Material Balance",
                        df=tbl_show.reset_index(drop=True),
                        title="Material Balance",
                        file_stem="dashboard_material_balance",
                        key="pdf_dash_mb",
                        use_container_width=True)

            # ─────────────────────────────────────────────────────────────────────────
            else:  # 🛒 Material Requirement & Procurement
            # ─────────────────────────────────────────────────────────────────────────

                st.markdown('<div class="sec-hdr">🛒 Material Requirement & Procurement — Location / System Code View</div>',
                            unsafe_allow_html=True)

                # KPI strip — cross-UOM "Shortfall Units" / "After Orders (Net)" removed
                # because summing kg+lt+m² across materials is not a meaningful number.
                p1,p2,p3,p4 = st.columns(4)
                with p1:
                    dbl_click_metric("Equipment", str(len(filtered_tags)), "t0p_equip",
                        "Equipment List", _dd_equip_df)
                with p2:
                    dbl_click_metric("Total SQM", f"{proj_sqm:,.1f}", "t0p_sqm",
                        "SQM by Equipment & System Code", _dd_sqm_df)
                with p3:
                    dbl_click_metric("Available Coverage SQM", f"{can_sqm:,.2f}", "t0p_cov_sqm",
                        "Coverable SQM by Equipment & System Code", _dd_cov_sqm_df)
                with p4:
                    dbl_click_metric("SQM Deficit", f"{short_sqm:,.2f}", "t0p_deficit",
                        "SQM Deficit by Equipment & System Code", _dd_def_sqm_df)
                st.markdown("<br>", unsafe_allow_html=True)

                # Per-location, per-system-code breakdown
                for loc in sel_locations:
                    loc_tags = filtered_eq[filtered_eq["Location"]==loc]["Equipment_Tag_No."].tolist()
                    if not loc_tags: continue

                    loc_dm = filtered_dm[filtered_dm["Equipment_Tag_No."].isin(loc_tags)]
                    if loc_dm.empty: continue

                    loc_sqm = sqm_ref[
                        sqm_ref["Equipment_Tag_No."].isin(loc_tags) &
                        sqm_ref["Lining_System_Code"].isin(sel_codes)
                    ]["Total_SQM"].sum()

                    loc_agg = (loc_dm.groupby("Material_Code",as_index=False)["Demand_Qty"].sum()
                        .merge(inv[["Material_Code","Available_Qty","Ordered_Qty"]],
                               on="Material_Code",how="left"))
                    loc_agg["Available_Qty"] = loc_agg["Available_Qty"].fillna(0)
                    loc_agg["Ordered_Qty"]   = loc_agg["Ordered_Qty"].fillna(0)
                    loc_agg["Shortfall"]     = (loc_agg["Demand_Qty"]-loc_agg["Available_Qty"]).clip(lower=0)
                    loc_agg["Net_Shortfall"] = (loc_agg["Demand_Qty"]-loc_agg["Available_Qty"]-loc_agg["Ordered_Qty"]).clip(lower=0)
                    loc_d = loc_agg["Demand_Qty"].sum()
                    loc_a = loc_agg["Available_Qty"].clip(upper=loc_agg["Demand_Qty"]).sum()
                    loc_c = (loc_a/loc_d*100) if loc_d>0 else 100
                    loc_can_sqm = round(loc_sqm * min(1.0, loc_c/100), 2)
                    loc_sh_sqm  = round(loc_sqm - loc_can_sqm, 2)
                    loc_dot = "🟢" if loc_c>=100 else "🟠" if loc_c>=90 else "🟡" if loc_c>=80 else "🔴"

                    badge_cls = {"Brown Field":"loc-bf","TRAIN J":"loc-tj","TRAIN K":"loc-tk"}.get(loc,"loc-bf")
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:.8rem;margin:1.2rem 0 .5rem;">'
                        f'<span style="font-size:.95rem;">{loc_dot}</span>'
                        f'<span class="loc-badge {badge_cls}">{loc}</span>'
                        f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:.75rem;'
                        f'color:var(--t3);">{len(loc_tags)} equip  ·  {loc_can_sqm:,.1f}/{loc_sqm:,.1f} SQM  ·  {loc_c:.1f}%</span>'
                        f'</div>', unsafe_allow_html=True)

                    # Per system code within this location
                    for code in sorted(sel_codes, key=lambda x: int(x)):
                        code_dm = loc_dm[loc_dm["Lining_System_Code"]==code]
                        if code_dm.empty: continue
                        sname = code_dm["Lining_System_Short_Name"].iloc[0]

                        # ✅ Correct SQM
                        code_sqm = sqm_ref[
                            sqm_ref["Equipment_Tag_No."].isin(loc_tags) &
                            (sqm_ref["Lining_System_Code"]==code)
                        ]["Total_SQM"].sum()

                        code_agg = (code_dm.groupby(["Material_Code","Material_Name","UOM"],
                                                    as_index=False)["Demand_Qty"].sum()
                            .merge(inv[["Material_Code","Available_Qty","Ordered_Qty"]],
                                   on="Material_Code",how="left"))
                        code_agg["Available_Qty"] = code_agg["Available_Qty"].fillna(0)
                        code_agg["Ordered_Qty"]   = code_agg["Ordered_Qty"].fillna(0)
                        code_agg["Shortfall"]     = (code_agg["Demand_Qty"]-code_agg["Available_Qty"]).clip(lower=0).round(3)
                        code_agg["Net_Shortfall"] = (code_agg["Demand_Qty"]-code_agg["Available_Qty"]-code_agg["Ordered_Qty"]).clip(lower=0).round(3)
                        code_agg["Coverage_Pct"]  = (
                            code_agg["Available_Qty"].clip(upper=code_agg["Demand_Qty"]) /
                            code_agg["Demand_Qty"].replace(0,np.nan)*100
                        ).fillna(100).clip(0,100).round(1)
                        code_agg["Fulfillment_Pct"] = code_agg["Coverage_Pct"]

                        cd = code_agg["Demand_Qty"].sum()
                        ca = code_agg["Available_Qty"].clip(upper=code_agg["Demand_Qty"]).sum()
                        cc = (ca/cd*100) if cd>0 else 100
                        c_can_sqm = round(code_sqm * min(1.0, cc/100), 2)
                        c_sh_sqm  = round(code_sqm - c_can_sqm, 2)
                        c_dot = "🟢" if cc>=100 else "🟠" if cc>=90 else "🟡" if cc>=80 else "🔴"

                        with st.expander(
                            f"{c_dot}  Code {code} – {sname}  ·  "
                            f"{c_can_sqm:,.1f}/{code_sqm:,.1f} SQM  ·  {cc:.1f}%",
                            expanded=False,
                        ):
                            pc1,pc2,pc3,pc4,pc5 = st.columns(5)
                            pc1.metric("System Code", f"Code {code}")
                            pc2.metric("Short Name",  sname)
                            pc3.metric("SQM Total",   f"{code_sqm:,.2f}")
                            pc4.metric("Available Material Coverage (SQM)", f"{c_can_sqm:,.2f}")
                            pc5.metric("SQM Deficit",    f"{c_sh_sqm:,.2f}")

                            # Table with Available, On Order, Demand, Shortfall, Net Shortfall
                            tbl_proc = code_agg[["Material_Code","Material_Name","UOM",
                                                 "Available_Qty","Ordered_Qty","Demand_Qty",
                                                 "Shortfall","Net_Shortfall","Fulfillment_Pct"]].copy()
                            tbl_proc.columns = ["Code","Material Name","UOM",
                                                "Available","On Order","Demand",
                                                "Shortfall","Net Shortfall (After Orders)","Fulfil %"]

                            def _style_proc(row):
                                pct = row["Fulfil %"]
                                if pd.isna(pct): pct = 100.0
                                if pct>=100:  bg,tc = "rgba(16,185,129,.1)","#10B981"
                                elif pct>=90: bg,tc = "rgba(249,115,22,.1)","#F97316"
                                elif pct>=80: bg,tc = "rgba(234,179,8,.1)", "#EAB308"
                                else:         bg,tc = "rgba(239,68,68,.1)", "#EF4444"
                                styles = [f"background-color:{bg}"]*len(row)
                                styles[-1] = f"background-color:{bg};color:{tc};font-weight:700"
                                return styles

                            styled_proc = tbl_proc.style.apply(_style_proc,axis=1).format({
                                "Available":"{:,.3f}","On Order":"{:,.3f}","Demand":"{:,.3f}",
                                "Shortfall":"{:,.3f}","Net Shortfall (After Orders)":"{:,.3f}",
                                "Fulfil %":"{:.1f}%"})
                            st.dataframe(styled_proc,use_container_width=True,hide_index=True,
                                         height=65+len(tbl_proc)*35,
                                         key=f"proc_{loc}_{code}")

                    st.markdown('<div style="border-bottom:1px solid var(--border);margin:.8rem 0;"></div>',
                                unsafe_allow_html=True)

                # Grand total procurement table
                st.markdown('<div class="sec-hdr" style="margin-top:1rem;">📦 Grand Total — All Selected Equipment</div>',
                            unsafe_allow_html=True)

                grand = f_demand.sort_values("Coverage_Pct").copy()
                grand_show = grand[["Material_Code","Material_Name","UOM",
                                     "Available_Qty","Ordered_Qty","Demand_Qty",
                                     "Shortfall","Net_Shortfall","Coverage_Pct"]].copy()
                grand_show.columns = ["Code","Material Name","UOM",
                                       "Available","On Order","Demand",
                                       "Shortfall","Net Shortfall","Coverage %"]

                def _style_grand(row):
                    pct = row["Coverage %"]
                    if pd.isna(pct): pct=100.0
                    if pct>=100:  bg,tc = "rgba(16,185,129,.1)","#10B981"
                    elif pct>=90: bg,tc = "rgba(249,115,22,.1)","#F97316"
                    elif pct>=80: bg,tc = "rgba(234,179,8,.1)", "#EAB308"
                    else:         bg,tc = "rgba(239,68,68,.1)", "#EF4444"
                    styles = [f"background-color:{bg}"]*len(row)
                    styles[-1] = f"background-color:{bg};color:{tc};font-weight:700"
                    return styles

                styled_grand = grand_show.style.apply(_style_grand,axis=1).format({
                    "Available":"{:,.3f}","On Order":"{:,.3f}","Demand":"{:,.3f}",
                    "Shortfall":"{:,.3f}","Net Shortfall":"{:,.3f}","Coverage %":"{:.1f}%"})
                st.dataframe(styled_grand,use_container_width=True,hide_index=True,
                             height=50+len(grand_show)*35,key="proc_grand_tbl")

                gc1, gc1p, gc2, gc2p = st.columns(4)
                with gc1:
                    st.download_button("⬇ Excel — Grand Procurement",
                        data=generate_excel_report(grand_show.reset_index(drop=True), "Grand Procurement Table", color_scheme="dashboard"),
                        file_name="procurement_grand_total.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)
                with gc1p:
                    _pdf_download_button("⬇ PDF — Grand Procurement",
                        df=grand_show.reset_index(drop=True),
                        title="Grand Procurement Table",
                        file_stem="procurement_grand_total",
                        key="pdf_proc_grand",
                        use_container_width=True)
                shortage_net = grand[grand["Net_Shortfall"]>0][
                    ["Material_Code","Material_Name","UOM","Available_Qty","Ordered_Qty",
                     "Demand_Qty","Shortfall","Net_Shortfall"]].copy()
                shortage_net.columns = ["Code","Material Name","UOM","Available","On Order",
                                        "Demand","Shortfall","NET TO ORDER"]
                with gc2:
                    if not shortage_net.empty:
                        st.download_button("⬇ Excel — Net Order List",
                            data=generate_excel_report(shortage_net.reset_index(drop=True), "Net Order List", color_scheme="dashboard"),
                            file_name="net_order_list.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True)
                with gc2p:
                    if not shortage_net.empty:
                        _pdf_download_button("⬇ PDF — Net Order List",
                            df=shortage_net.reset_index(drop=True),
                            title="Net Order List",
                            file_stem="net_order_list",
                            key="pdf_net_order",
                            use_container_width=True)

        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 1 · EQUIPMENT ENTRY
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab1:
            left, right = st.columns([1, 1.65], gap="large")

            # ── LEFT: filter + search + session list ────────────────────────────────
            with left:
                st.markdown('<div class="sec-hdr">🎛 Filter Equipment</div>',
                            unsafe_allow_html=True)

                # 3 filter selectors
                f_loc  = st.multiselect(" Location", options=LOCATION_ORDER,
                                         default=[], key="t1_loc",
                                         placeholder="All locations")
                all_types = _get_all_types(eq_master)
                f_type = st.multiselect(" Type", options=all_types,
                                         default=[], key="t1_type",
                                         placeholder="All types")
                all_codes_t1 = sorted(
                    dm["Lining_System_Code"].unique().tolist(), key=lambda x: int(x))
                f_code = st.multiselect(
                    " System Code", options=all_codes_t1,
                    format_func=lambda c: f"Code {c} – "
                        f"{dm[dm['Lining_System_Code']==c]['Lining_System_Short_Name'].iloc[0]}",
                    default=[], key="t1_code", placeholder="All system codes")

                # Build filtered tag list
                filtered_eq = eq_master.copy()
                if f_loc:
                    filtered_eq = filtered_eq[filtered_eq["Location"].isin(f_loc)]
                if f_type:
                    filtered_eq = filtered_eq[filtered_eq["Type"].str.strip().isin(f_type)]
                if f_code:
                    tags_with_code = dm[dm["Lining_System_Code"].isin(f_code)][
                        "Equipment_Tag_No."].unique().tolist()
                    filtered_eq = filtered_eq[filtered_eq["Equipment_Tag_No."].isin(tags_with_code)]
                filtered_tags_t1 = sorted(filtered_eq["Equipment_Tag_No."].tolist())

                st.markdown('<div class="sec-hdr" style="margin-top:.8rem;">🔍 Find Equipment</div>',
                            unsafe_allow_html=True)
                selected_tag = st.selectbox(
                    "tag_search", [""] + filtered_tags_t1,
                    format_func=lambda t: (
                        "" if t == "" else
                        f"{t}  —  "
                        f"{eq_master.set_index('Equipment_Tag_No.')['Name'].get(t,'')}"
                    ),
                    key="tag_select", label_visibility="collapsed",
                )
                ca, cb = st.columns([2,1])
                with ca:
                    already = selected_tag in st.session_state.session_tags
                    add_btn = st.button("＋ Add to Session", key="add_btn",
                                        disabled=(selected_tag=="" or already))
                with cb:
                    if already and selected_tag:
                        st.markdown(
                            '<div style="padding:.45rem 0;font-family:\'JetBrains Mono\','
                            'monospace;font-size:.7rem;color:#10B981;">✓ In session</div>',
                            unsafe_allow_html=True)
                if add_btn and selected_tag:
                    st.session_state.session_tags.append(selected_tag)
                    st.rerun()

                # ── Priority order controls ──────────────────────────────────────────
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown('<div class="sec-hdr">📋 Session Priority List</div>',
                            unsafe_allow_html=True)

                session_tags = st.session_state.session_tags
                if not session_tags:
                    st.info("Add equipment tags above to build your session.")
                else:
                    from streamlit_sortables import sort_items

                    alloc_df = cascade_allocate(session_tags)
                    tag_name = eq_master.set_index("Equipment_Tag_No.")["Name"].to_dict()
                    tag_loc  = eq_master.set_index("Equipment_Tag_No.")["Location"].to_dict()

                    # Static labels — no % to keep component state stable across reruns
                    def _item_label(i, t):
                        name = tag_name.get(t, t)[:28]
                        loc  = tag_loc.get(t, "—")
                        return f"#{i+1}  ||  {t}  ||  {name}  ||  {loc}"

                    display_items = [_item_label(i, t) for i, t in enumerate(session_tags)]

                    # Key tied to exact list — forces full re-init when items added/removed
                    sort_key = "sess_sort_" + "_".join(session_tags)
                    st.caption("⠿ Drag to reorder — order is applied instantly.")
                    sorted_display = sort_items(display_items, direction="vertical", key=sort_key)

                    # Parse tag back using explicit || delimiter
                    def _parse_tag(label):
                        parts = label.split("  ||  ")
                        return parts[1].strip() if len(parts) > 1 else label.strip()

                    new_order = [_parse_tag(l) for l in sorted_display if _parse_tag(l) in session_tags]
                    # Safety fallback
                    if len(new_order) != len(session_tags):
                        new_order = session_tags[:]

                    # Auto-apply if order changed
                    if new_order != st.session_state.session_tags:
                        st.session_state.session_tags = new_order
                        st.rerun()

                    # Show fulfillment rows with ✕ remove button
                    alloc_df2 = cascade_allocate(new_order)
                    for idx_t, t in enumerate(new_order):
                        pct  = tag_fulfillment(alloc_df2, t)
                        name = tag_name.get(t, t)
                        loc  = tag_loc.get(t, "—")
                        dot  = status_dot(pct)
                        tag_total_sqm = sqm_ref[sqm_ref["Equipment_Tag_No."]==t]["Total_SQM"].sum()
                        can_sqm  = round(tag_total_sqm * min(1.0, pct/100), 2)
                        row_c, row_x = st.columns([9, 1])
                        with row_c:
                            # Append system codes to session list display
                            _t_codes = sorted(
                                dm[dm["Equipment_Tag_No."]==t]["Lining_System_Code"].unique(),
                                key=lambda x: int(x))
                            _codes_badges = "  ".join(
                                f'<span style="font-family:\'JetBrains Mono\',monospace;'
                                f'font-size:.58rem;background:rgba(245,158,11,.15);'
                                f'color:var(--amber);border-radius:3px;padding:.1rem .3rem;">C{c}</span>'
                                for c in _t_codes)
                            _sess_parts = [
                                f'<div class="session-equip" style="margin-bottom:.22rem;">',
                                f'<span class="{dot}" style="font-family:JetBrains Mono,monospace;',
                                f'font-size:.75rem;font-weight:600;color:var(--t1);">{t}</span>',
                                f'<span style="font-size:.75rem;color:var(--t3);margin-left:.5rem;">{name[:22]}</span>',
                                f'<span style="margin-left:.4rem;">{_codes_badges}</span>',
                                f'<span style="font-family:JetBrains Mono,monospace;',
                                f'font-size:.62rem;color:var(--t4);margin-left:.4rem;">{loc}</span>',
                                f'<span style="float:right;font-family:JetBrains Mono,monospace;',
                                f'font-size:.64rem;color:var(--t3);">{can_sqm:,.1f}/{tag_total_sqm:,.1f} SQM&nbsp;&nbsp;</span>',
                                f'<span style="float:right;">{fulfil_pill(pct)}</span>',
                                '</div>',
                            ]
                            st.markdown("".join(_sess_parts), unsafe_allow_html=True)
                        with row_x:
                            if st.button("✕", key=f"rm_{t}_{idx_t}", help=f"Remove {t}"):
                                st.session_state.session_tags.remove(t)
                                st.rerun()

                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🗑 Clear All", key="clear_all"):
                        st.session_state.session_tags = []
                        st.rerun()

            # ── RIGHT: equipment info card + system-code material tables ─────────────
            with right:
                if not selected_tag:
                    st.markdown("""
    <div style="text-align:center;padding:4rem 1rem;">
      <div style="font-size:2.5rem;opacity:.12;margin-bottom:.8rem;"></div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;
                  color:var(--t5);letter-spacing:.1em;">
                SELECT AN EQUIPMENT TAG TO VIEW DETAILS</div></div>""",
                        unsafe_allow_html=True)
                else:
                    row = eq_master[eq_master["Equipment_Tag_No."]==selected_tag].iloc[0]
                    tag_codes = dm[dm["Equipment_Tag_No."]==selected_tag][
                        ["Lining_System_Code","Lining_System_Short_Name","Total_SQM"]
                    ].drop_duplicates().sort_values("Lining_System_Code", key=lambda x: x.astype(int))

                    # Info card
                    st.markdown(
                        f'<div class="card card-amber">'
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:flex-start;margin-bottom:.7rem;">'
                        f'<div><div style="font-family:\'JetBrains Mono\',monospace;'
                        f'font-size:.62rem;color:var(--t4);letter-spacing:.1em;'
                        f'text-transform:uppercase;">Equipment Tag</div>'
                        f'<div style="font-family:\'JetBrains Mono\',monospace;'
                        f'font-size:1.1rem;font-weight:700;color:#F59E0B;">'
                        f'{row["Equipment_Tag_No."]}</div></div>'
                        f'{loc_badge(str(row["Location"]))}</div>'
                        f'<div style="font-size:.95rem;font-weight:600;color:var(--t0);'
                        f'margin-bottom:.9rem;">{row["Name"]}</div>'
                        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;'
                        f'gap:.4rem .7rem;font-size:.78rem;">'
                        f'<div><span style="color:var(--t4);">Type</span><br>'
                        f'<span style="color:var(--t1);">{row["Type"]}</span></div>'
                        f'<div><span style="color:var(--t4);">Substrate</span><br>'
                        f'<span style="color:var(--t1);">{row["Substrate"] or "—"}</span></div>'
                        f'<div><span style="color:var(--t4);">Material Spec.</span><br>'
                        f'<span style="color:var(--t1);">{row["Material_Spec"] or "—"}</span></div>'
                        f'<div style="grid-column:1/-1;">'
                        f'<span style="color:var(--t4);">Lining Systems</span><br>'
                        f'<span style="color:var(--t2);font-size:.75rem;line-height:1.5;">'
                        f'{str(row["Lining_Systems"]).replace(chr(10),"<br>")}'
                        f'</span></div></div></div>',
                        unsafe_allow_html=True)

                    # System code sections
                    st.markdown('<div class="sec-hdr" style="margin-top:1rem;">'
                                '⚗️ System Code Material Requirements</div>',
                                unsafe_allow_html=True)

                    for _, sc_row in tag_codes.iterrows():
                        code  = sc_row["Lining_System_Code"]
                        sname = sc_row["Lining_System_Short_Name"]
                        sqm   = sc_row["Total_SQM"]

                        mat_rows = dm[
                            (dm["Equipment_Tag_No."]==selected_tag) &
                            (dm["Lining_System_Code"]==code)
                        ].copy()
                        mat_rows = mat_rows.merge(
                            inv[["Material_Code","Available_Qty"]], on="Material_Code", how="left")
                        mat_rows["Available_Qty"]   = mat_rows["Available_Qty"].fillna(0)
                        mat_rows["Allocated_Qty"]   = mat_rows["Available_Qty"].clip(
                            upper=mat_rows["Demand_Qty"])
                        mat_rows["Shortfall_Qty"]   = (
                            mat_rows["Demand_Qty"] - mat_rows["Allocated_Qty"]).clip(lower=0)
                        mat_rows["Fulfillment_Pct"] = (
                            mat_rows["Allocated_Qty"] /
                            mat_rows["Demand_Qty"].replace(0,np.nan) * 100
                        ).fillna(100).clip(0,100).round(2)

                        d_sum = mat_rows["Demand_Qty"].sum()
                        a_sum = mat_rows["Allocated_Qty"].sum()
                        pct   = min(100, a_sum/d_sum*100) if d_sum > 0 else 100
                        short = mat_rows["Shortfall_Qty"].sum()

                        with st.expander(
                            f"System Code {code}  ·  {sname}  ·  {sqm:,.2f} SQM  "
                            f"·  Coverage: {pct:.1f}%",
                            expanded=False,
                        ):
                            mi1,mi2,mi3,mi4 = st.columns(4)
                            _t1_mat_dd = mat_rows[["Material_Code","Material_Name","Demand_Qty","Allocated_Qty","Shortfall_Qty","Fulfillment_Pct"]].reset_index(drop=True)
                            _t1_sk = f"t1_sc_{selected_tag}_{code}"
                            with mi1:
                                st.metric("System Code", str(code))
                            with mi2:
                                dbl_click_metric("Short Name", str(sname), f"{_t1_sk}_n",
                                    f"{sname} — Material Breakdown", _t1_mat_dd)
                            with mi3:
                                dbl_click_metric("Surface Area", f"{sqm:,.2f} SQM", f"{_t1_sk}_s",
                                    f"Code {code} — Material Breakdown", _t1_mat_dd)
                            with mi4:
                                dbl_click_metric("Coverage", f"{pct:.1f}%", f"{_t1_sk}_p",
                                    f"Code {code} — Coverage Detail", _t1_mat_dd)
                            plotly_mat_table(
                                mat_rows, f"entry_{selected_tag}_{code}",
                                height=65 + len(mat_rows)*30
                            )

                    # Grand total for this equipment
                    all_mat = dm[dm["Equipment_Tag_No."]==selected_tag].merge(
                        inv[["Material_Code","Available_Qty"]], on="Material_Code", how="left")
                    all_mat["Available_Qty"] = all_mat["Available_Qty"].fillna(0)
                    all_mat["Shortfall"]     = (
                        all_mat["Demand_Qty"] -
                        all_mat["Available_Qty"].clip(upper=all_mat["Demand_Qty"])
                    ).clip(lower=0)
                    gt_demand = all_mat["Demand_Qty"].sum()
                    gt_alloc  = all_mat["Available_Qty"].clip(upper=all_mat["Demand_Qty"]).sum()
                    gt_short  = all_mat["Shortfall"].sum()
                    gt_pct    = min(100, gt_alloc/gt_demand*100) if gt_demand > 0 else 100

                    st.markdown(f"""
    <div class="grand-box" style="margin-top:.8rem;">
              <div style="font-family:'JetBrains Mono',monospace;font-size:.6rem;
                          letter-spacing:.14em;text-transform:uppercase;
                          color:#F59E0B;margin-bottom:.7rem;">
                Equipment Grand Total — {selected_tag}</div>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.7rem;">
                <div><div style="font-family:'JetBrains Mono',monospace;
                     font-size:1.3rem;font-weight:700;color:var(--t0);">
                     {len(tag_codes)}</div>
                     <div style="font-size:.62rem;text-transform:uppercase;
                     letter-spacing:.08em;color:var(--t4);">System Codes</div></div>
                <div><div style="font-family:'JetBrains Mono',monospace;
                     font-size:1.3rem;font-weight:700;color:var(--t0);">
                     {gt_demand:,.0f}</div>
                     <div style="font-size:.62rem;text-transform:uppercase;
                     letter-spacing:.08em;color:var(--t4);">Total Demand</div></div>
                <div><div style="font-family:'JetBrains Mono',monospace;
                     font-size:1.3rem;font-weight:700;
                     color:{'#10B981' if gt_pct>=100 else '#F97316' if gt_pct>=90 else '#EAB308' if gt_pct>=80 else '#EF4444'};">
                     {gt_pct:.1f}%</div>
                     <div style="font-size:.62rem;text-transform:uppercase;
                     letter-spacing:.08em;color:var(--t4);">Coverage</div></div>
              </div>
            </div>""", unsafe_allow_html=True)


        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 2 · SESSION ORDER REPORT
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab2:
            session_tags = st.session_state.session_tags

            if not session_tags:
                st.info("Add equipment tags in the Entry tab to generate a report.")
            else:
                alloc_df = cascade_allocate(session_tags)
                tag_name = eq_master.set_index("Equipment_Tag_No.")["Name"].to_dict()
                tag_loc  = eq_master.set_index("Equipment_Tag_No.")["Location"].to_dict()

                # Session KPIs
                tot_demand = alloc_df["Demand_Qty"].sum()
                tot_alloc  = alloc_df["Allocated_Qty"].sum()
                tot_short  = alloc_df["Shortfall_Qty"].sum()
                ov_pct     = min(100, tot_alloc/tot_demand*100) if tot_demand > 0 else 100
                n_mats     = alloc_df["Material_Code"].nunique()
                n_short_m  = alloc_df[alloc_df["Shortfall_Qty"]>0]["Material_Code"].nunique()

                # Cross-UOM "Total Shortfall" KPI dropped — summing kg+lt+m² across
                # materials is not a meaningful number. Use the "Need to Order" count
                # plus the per-material drill-down for actionable shortage info.
                k1,k2,k3,k4 = st.columns(4)
                _t2_equip_dd = pd.DataFrame({
                    "Equipment Tag": session_tags,
                    "Name":          [tag_name.get(t, t) for t in session_tags],
                    "Location":      [tag_loc.get(t, "—") for t in session_tags],
                })
                _t2_mats_dd  = (alloc_df.groupby(["Material_Code","Material_Name"], as_index=False)
                                [["Demand_Qty","Allocated_Qty","Shortfall_Qty"]].sum()
                                .sort_values("Material_Code").reset_index(drop=True))
                _t2_mats_dd["Coverage_%"] = (_t2_mats_dd["Allocated_Qty"] /
                    _t2_mats_dd["Demand_Qty"].replace(0, np.nan) * 100).fillna(100).clip(0,100).round(1)
                _t2_order_dd = _t2_mats_dd[_t2_mats_dd["Shortfall_Qty"]>0].reset_index(drop=True)
                _t2_cov_dd   = _t2_mats_dd.sort_values("Coverage_%").reset_index(drop=True)
                with k1:
                    dbl_click_metric("Equipment", str(len(session_tags)), "t2_equip",
                        "Session Equipment List", _t2_equip_dd)
                with k2:
                    dbl_click_metric("Materials", str(n_mats), "t2_mats",
                        "Material Demand Summary", _t2_mats_dd)
                with k3:
                    dbl_click_metric("Need to Order", str(n_short_m), "t2_order",
                        "Materials to Procure", _t2_order_dd)
                with k4:
                    dbl_click_metric("Overall Coverage", f"{ov_pct:.1f}%", "t2_cov",
                        "Coverage by Material", _t2_cov_dd)
                st.markdown("<br>", unsafe_allow_html=True)

                # ── Priority reorder (updates global session_tags) ───────────────────
                st.markdown('<div class="sec-hdr">⠿ Drag to Reorder Priority — changes reflect everywhere</div>',
                            unsafe_allow_html=True)

                from streamlit_sortables import sort_items as _sort2

                def _t2_sl(i, t):
                    return f"#{i+1}  ||  {t}  ||  {tag_name.get(t,t)[:28]}  ||  {tag_loc.get(t,'—')}"

                t2_display = [_t2_sl(i, t) for i, t in enumerate(session_tags)]
                t2_key     = "t2_sort_" + "_".join(session_tags)
                t2_sorted  = _sort2(t2_display, direction="vertical", key=t2_key)

                def _t2_parse(label):
                    parts = label.split("  ||  ")
                    return parts[1].strip() if len(parts) > 1 else label.strip()

                t2_new_order = [_t2_parse(l) for l in t2_sorted if _t2_parse(l) in session_tags]
        
                if len(t2_new_order) == len(session_tags) and t2_new_order != st.session_state.session_tags:
                    st.session_state.session_tags = t2_new_order
                    st.rerun()

                st.markdown("<br>", unsafe_allow_html=True)
                # ── Per-equipment expanders ──────────────────────────────────────────
                st.markdown('<div class="sec-hdr">Per-Equipment System Code Breakdown</div>',
                            unsafe_allow_html=True)

                for i, tag in enumerate(session_tags):
                    tag_alloc = alloc_df[alloc_df["Equipment_Tag_No."]==tag]
                    t_pct     = tag_fulfillment(alloc_df, tag)
                    dot       = status_dot(t_pct)
                    t_short   = tag_alloc["Shortfall_Qty"].sum()
                    name      = tag_name.get(tag, tag)
                    loc       = tag_loc.get(tag, "—")
                    eq_row    = eq_master[eq_master["Equipment_Tag_No."]==tag].iloc[0]

                    t_sqm      = sqm_ref[sqm_ref["Equipment_Tag_No."]==tag]["Total_SQM"].sum()
                    t_can_sqm  = round(t_sqm * min(1.0, t_pct/100), 2)
                    t_dot_char = "🟢" if t_pct>=100 else "🟠" if t_pct>=90 else "🟡" if t_pct>=80 else "🔴"
                    _t2_type = str(eq_row.get("Type","") or "").strip()
                    _t2_desc = str(eq_row.get("Substrate","") or "").strip()[:20]
                    _t2_meta = "  |  ".join(p for p in [_t2_type,_t2_desc] if p and p not in ("nan","—"))
                    with st.expander(
                        f"{t_dot_char}  #{i+1}  {tag}  ·  {name}  ·  {_t2_meta}  ·  {loc}  "
                        f"·  {t_can_sqm:,.1f}/{t_sqm:,.1f} SQM  ·  {t_pct:.1f}%",
                        expanded=False,
                    ):
                        # Equipment meta strip
                        m1,m2,m3,m4 = st.columns(4)
                        m1.markdown(f'**Type:** {eq_row["Type"]}')
                        m2.markdown(f'**Substrate:** {eq_row["Substrate"] or "—"}')
                        m3.markdown(f'**Material Spec.:** {eq_row["Material_Spec"] or "—"}')
                        m4.markdown(f'**Total SQM:** `{eq_row["Total_SQM"]:,.2f}`')
                        st.caption(f'**Lining Systems:** '
                                   f'{str(eq_row["Lining_Systems"]).replace(chr(10)," | ")}')
                        st.markdown("---")

                        # Per system code tables
                        for code in sorted(tag_alloc["Lining_System_Code"].unique(),
                                           key=lambda x: int(x)):
                            code_alloc = tag_alloc[tag_alloc["Lining_System_Code"]==code].copy()
                            sname = code_alloc["Lining_System_Short_Name"].iloc[0]
                            sqm   = code_alloc["Total_SQM"].iloc[0]
                            c_pct = syscode_fulfillment(alloc_df, tag, code)
                            c_short = code_alloc["Shortfall_Qty"].sum()
                            c_demand = code_alloc["Demand_Qty"].sum()
                            c_alloc  = code_alloc["Allocated_Qty"].sum()

                            _, c_can_sqm, c_short_sqm = sqm_can_do(alloc_df, tag, code)
                            c_dot = "🟢" if c_pct>=100 else "🟠" if c_pct>=90 else "🟡" if c_pct>=80 else "🔴"
                            st.markdown(
                                f'<div class="syscode-block">'
                                f'<div class="syscode-hdr">'
                                f'<span style="font-size:.85rem;">{c_dot}</span>'
                                f'<span class="code-badge">Code {code}</span>'
                                f'<span style="font-size:.8rem;color:var(--t1);font-weight:500;">'
                                f'{sname}</span>'
                                f'<span style="font-family:\'JetBrains Mono\',monospace;'
                                f'font-size:.72rem;color:var(--t3);">'
                                f'{c_can_sqm:,.1f}/{sqm:,.1f} SQM</span>'
                                f'<span style="margin-left:auto;">{fulfil_pill(c_pct)}</span>'
                                f'</div></div>',
                                unsafe_allow_html=True)

                            sc1,sc2,sc3,sc4 = st.columns(4)
                            _t2e_dd = code_alloc[["Material_Code","Material_Name","Demand_Qty","Allocated_Qty","Shortfall_Qty"]].reset_index(drop=True)
                            _t2e_sk = f"t2e_{tag}_{code}"
                            with sc1:
                                dbl_click_metric("Demand", f"{c_demand:,.3f}", f"{_t2e_sk}_d",
                                    f"Code {code} — {sname}: Material Detail", _t2e_dd)
                            with sc2:
                                dbl_click_metric("Allocated", f"{c_alloc:,.3f}", f"{_t2e_sk}_a",
                                    f"Code {code} — {sname}: Material Detail", _t2e_dd)
                            if c_short > 0.001:
                                with sc3:
                                    dbl_click_metric("Shortfall", f"{c_short:,.3f}", f"{_t2e_sk}_sh",
                                        f"Code {code} — {sname}: Shortfall Detail",
                                        _t2e_dd[_t2e_dd["Shortfall_Qty"]>0].reset_index(drop=True))
                                with sc4:
                                    dbl_click_metric("SQM Deficit", f"{c_short_sqm:,.2f}", f"{_t2e_sk}_sq",
                                        f"Code {code} — {sname}: Shortfall Detail",
                                        _t2e_dd[_t2e_dd["Shortfall_Qty"]>0].reset_index(drop=True))
                            plotly_mat_table(
                                code_alloc,
                                f"rep_{tag}_{code}",
                                height=65 + len(code_alloc)*30,
                                show_sqm=True, tag=tag, code=code
                            )

                        # Equipment grand total row
                        st.markdown(
                            f'<div style="background:rgba(245,158,11,.05);'
                            f'border:1px solid rgba(245,158,11,.2);border-radius:6px;'
                            f'padding:.7rem 1rem;margin-top:.5rem;'
                            f'font-family:\'JetBrains Mono\',monospace;font-size:.8rem;">'
                            f'<span style="color:#F59E0B;font-weight:700;">GRAND TOTAL — {tag}</span>'
                            f'<span style="color:var(--t3);margin-left:1.5rem;">'
                            f'Demand: <strong style="color:var(--t1);">'
                            f'{tag_alloc["Demand_Qty"].sum():,.3f}</strong></span>'
                            f'<span style="color:var(--t3);margin-left:1rem;">'
                            f'Allocated: <strong style="color:var(--t1);">'
                            f'{tag_alloc["Allocated_Qty"].sum():,.3f}</strong></span>'
                            + (f'<span style="color:var(--t3);margin-left:1rem;">'
                            f'Shortfall: <strong style="color:#EF4444;">'
                            f'{tag_alloc["Shortfall_Qty"].sum():,.3f}</strong></span>'
                            if tag_alloc["Shortfall_Qty"].sum() > 0.001 else "") +
                            f'<span style="margin-left:1rem;">{fulfil_pill(t_pct)}</span>'
                            f'</div>',
                            unsafe_allow_html=True)

                # ── Combined procurement list ─────────────────────────────────────────
                st.markdown('<div class="sec-hdr" style="margin-top:1.5rem;">'
                            '🛒 Combined Procurement List</div>',
                            unsafe_allow_html=True)

                combined = alloc_df.groupby(
                    ["Material_Code","Material_Name","UOM"], as_index=False
                ).agg(
                    Demand_Qty    =("Demand_Qty",    "sum"),
                    Allocated_Qty =("Allocated_Qty", "sum"),
                    Shortfall_Qty =("Shortfall_Qty", "sum"),
                )
                combined["Fulfillment_Pct"] = (
                    combined["Allocated_Qty"] /
                    combined["Demand_Qty"].replace(0, np.nan) * 100
                ).fillna(100).clip(0,100).round(2)

                # ── SQM per material: sum of SQM across every (tag,code) cell it touches,
                #    weighted by that cell's fulfillment ──────────────────────────────
                _sqm_per_mat = alloc_df.copy()
                _sqm_per_mat["SQM_Done_Cell"] = (
                    _sqm_per_mat["Total_SQM"] * _sqm_per_mat["Fulfillment_Pct"] / 100
                )
                _sqm_agg = _sqm_per_mat.groupby("Material_Code", as_index=False).agg(
                    SQM_Total =("Total_SQM",     "sum"),
                    SQM_Done  =("SQM_Done_Cell", "sum"),
                )
                _sqm_agg["SQM_Deficit"] = (_sqm_agg["SQM_Total"] - _sqm_agg["SQM_Done"]).round(2)
                _sqm_agg["SQM_Total"]   = _sqm_agg["SQM_Total"].round(2)
                _sqm_agg["SQM_Done"]    = _sqm_agg["SQM_Done"].round(2)
                combined = combined.merge(_sqm_agg, on="Material_Code", how="left")
                combined = combined.sort_values("Fulfillment_Pct")

                # Stacked bar (shortage only)
                shortage_only = combined[combined["Shortfall_Qty"]>0].copy()
                if not shortage_only.empty:
                    shortage_only["Label"] = (
                        shortage_only["Material_Code"] + "  " +
                        shortage_only["Material_Name"].fillna("").str[:22]
                    )
                    fig_bar = go.Figure()
                    fig_bar.add_trace(go.Bar(
                        name="Available", y=shortage_only["Label"],
                        x=shortage_only["Allocated_Qty"], orientation="h",
                        marker_color="#10B981", marker_opacity=.75,
                        text=shortage_only["Allocated_Qty"].apply(lambda v:f"{v:,.0f}"),
                        textposition="inside",
                        textfont=dict(family="JetBrains Mono",size=9,color="#fff"),
                    ))
                    fig_bar.add_trace(go.Bar(
                        name="To Order", y=shortage_only["Label"],
                        x=shortage_only["Shortfall_Qty"], orientation="h",
                        marker_color="#EF4444", marker_opacity=.75,
                        text=shortage_only["Shortfall_Qty"].apply(lambda v:f"{v:,.1f}"),
                        textposition="inside",
                        textfont=dict(family="JetBrains Mono",size=9,color="#fff"),
                    ))
                    fig_bar.update_layout(
                        barmode="stack",
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="JetBrains Mono", size=10), # Fixed Font
                        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                    bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
                        margin=dict(l=0, r=60, t=28, b=0),
                        height=max(300, len(shortage_only)*42),
                        xaxis=dict(gridcolor="rgba(128, 128, 128, 0.2)", zerolinecolor="rgba(128, 128, 128, 0.2)"), # Fixed Grids
                        yaxis=dict(gridcolor="rgba(128, 128, 128, 0.2)"), # Fixed Grids
                    )
                    st.plotly_chart(fig_bar, use_container_width=True, key="session_bar")

                plotly_mat_table(combined, "session_combined",
                                 height=90+len(combined)*30)

                # Grand total box
                st.markdown(f"""
<div class="grand-box" style="margin-top:1rem;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:.6rem;
                      letter-spacing:.14em;text-transform:uppercase;
                      color:#F59E0B;margin-bottom:.7rem;">
            ⭐ Grand Total — {len(session_tags)} Equipment Session</div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:.7rem;">
            <div><div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                 font-weight:700;color:var(--t0);">{len(session_tags)}</div>
                 <div style="font-size:.6rem;text-transform:uppercase;
                 letter-spacing:.08em;color:var(--t4);">Equipment</div></div>
            <div><div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                 font-weight:700;color:var(--t0);">{n_mats}</div>
                 <div style="font-size:.6rem;text-transform:uppercase;
                 letter-spacing:.08em;color:var(--t4);">Materials</div></div>
            <div><div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                 font-weight:700;color:var(--t0);">{tot_demand:,.0f}</div>
                 <div style="font-size:.6rem;text-transform:uppercase;
                 letter-spacing:.08em;color:var(--t4);">Total Demand</div></div>
            <div><div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                 font-weight:700;color:#EF4444;">{n_short_m}</div>
                 <div style="font-size:.6rem;text-transform:uppercase;
                 letter-spacing:.08em;color:var(--t4);">To Procure</div></div>
          </div>
        </div>""", unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)
                d1, d1p, d2, d2p = st.columns(4)
                with d1:
                    st.download_button("⬇ Excel — Full Session",
                                       data=generate_excel_report(alloc_df, "Session Full Report", color_scheme="session"),
                                       file_name="session_full_report.xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       use_container_width=True)
                with d1p:
                    _pdf_download_button("⬇ PDF — Full Session",
                        df=alloc_df, title="Session Full Report",
                        file_stem="session_full_report",
                        key="pdf_session_full",
                        use_container_width=True)
                with d2:
                    if not shortage_only.empty:
                        st.download_button("⬇ Excel — Order List",
                                           data=generate_excel_report(shortage_only, "Order List", color_scheme="session"),
                                           file_name="order_list.xlsx",
                                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                           use_container_width=True)
                with d2p:
                    if not shortage_only.empty:
                        _pdf_download_button("⬇ PDF — Order List",
                            df=shortage_only, title="Order List",
                            file_stem="order_list",
                            key="pdf_order_list",
                            use_container_width=True)


                # ── Smart Reordering Suggestions ──────────────────────────────────────
                render_suggestion_panel(session_tags, "tab2")

        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 3 · LOCATION REPORT
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab3:
            loc_report_mode = st.radio(
                "View Mode",
                ["📍 Location Based", "🌐 All Equipment"],
                horizontal=True, key="loc_report_mode",
                label_visibility="collapsed",
            )
            st.markdown("<hr>", unsafe_allow_html=True)

            # ── Per-location order state (independent from global session_tags) ──────
            if "loc_order" not in st.session_state:
                st.session_state.loc_order = {}

            # ══════════════════════════════════════════════════════════════════════════
            # ALL EQUIPMENT MODE
            # ══════════════════════════════════════════════════════════════════════════
            if loc_report_mode == "🌐 All Equipment":
                st.markdown('<div class="sec-hdr">🌐 All Equipment — Global Cascading Balance</div>',
                            unsafe_allow_html=True)
                st.caption("All equipment in file order. Inventory pool is shared globally across all locations.")

                from streamlit_sortables import sort_items as _sort3_all

                if "all_eq_order" not in st.session_state:
                    st.session_state.all_eq_order = eq_master["Equipment_Tag_No."].tolist()

                all_eq_tags = st.session_state.all_eq_order

                # ── Sortable list (compact: tag + loc + SQM only) ────────────────────
                def _ae_label(i, t):
                    loc  = eq_master.set_index("Equipment_Tag_No.")["Location"].get(t, "")
                    sqm  = eq_master.set_index("Equipment_Tag_No.")["Total_SQM"].get(t, 0)
                    return f"#{i+1}  ||  {t}  ||  {loc}  ||  {sqm:,.1f} SQM"

                _ae_display = [_ae_label(i, t) for i, t in enumerate(all_eq_tags)]
                _ae_key     = "ae_sort_" + str(len(all_eq_tags))
                st.caption("⇅ Drag to reorder — order determines cascade priority.")
                _ae_sort_l, _ae_sort_r = st.columns([2, 3])
                with _ae_sort_l:
                    st.markdown('<div class="sme-compact-sortable">', unsafe_allow_html=True)
                    _ae_sorted = _sort3_all(_ae_display, direction="vertical", key=_ae_key)
                    st.markdown('</div>', unsafe_allow_html=True)

                def _ae_parse(label):
                    parts = label.split("  ||  ")
                    return parts[1].strip() if len(parts) > 1 else label.strip()

                _ae_new_order = [_ae_parse(l) for l in _ae_sorted if _ae_parse(l) in all_eq_tags]
                if len(_ae_new_order) == len(all_eq_tags) and _ae_new_order != st.session_state.all_eq_order:
                    st.session_state.all_eq_order = _ae_new_order
                    st.rerun()

                # ── Cascade allocation across all equipment ───────────────────────────
                ae_alloc = cascade_allocate(all_eq_tags)

                ae_demand = ae_alloc["Demand_Qty"].sum()
                ae_alloc_qty = ae_alloc["Allocated_Qty"].sum()
                ae_short  = ae_alloc["Shortfall_Qty"].sum()
                ae_pct    = min(100, ae_alloc_qty / ae_demand * 100) if ae_demand > 0 else 100
                ae_sqm    = sqm_ref["Total_SQM"].sum()
                ae_can_sqm = round(ae_sqm * min(1.0, ae_pct / 100), 2)

                # KPI strip
                ae_k1, ae_k2, ae_k3, ae_k4, ae_k5 = st.columns(5)
                ae_k1.metric("Equipment", str(len(all_eq_tags)))
                ae_k2.metric("Total SQM", f"{ae_sqm:,.1f}")
                ae_k3.metric("Available Coverage SQM", f"{ae_can_sqm:,.2f}")
                ae_k4.metric("SQM Deficit", f"{ae_sqm - ae_can_sqm:,.2f}")
                ae_k5.metric("Overall Coverage", f"{ae_pct:.1f}%")
                st.markdown("<br>", unsafe_allow_html=True)

                # ── Per-equipment expanders ────────────────────────────────────────────
                st.markdown('<div class="sec-hdr">Per-Equipment Detail</div>', unsafe_allow_html=True)
                for i, tag in enumerate(all_eq_tags):
                    tag_alloc_ae = ae_alloc[ae_alloc["Equipment_Tag_No."] == tag]
                    t_pct_ae = tag_fulfillment(ae_alloc, tag)
                    t_short_ae = tag_alloc_ae["Shortfall_Qty"].sum()
                    eq_row_ae = eq_master[eq_master["Equipment_Tag_No."] == tag].iloc[0]
                    _t3a_sqm = sqm_ref[sqm_ref["Equipment_Tag_No."] == tag]["Total_SQM"].sum()
                    _t3a_cansqm = round(_t3a_sqm * min(1.0, t_pct_ae / 100), 2)
                    _t3a_dot = "✅" if t_pct_ae >= 100 else "🟠" if t_pct_ae >= 90 else "🟡" if t_pct_ae >= 80 else "🔴"
                    _t3a_type = str(eq_row_ae.get("Type", "") or "").strip()
                    _t3a_desc = str(eq_row_ae.get("Substrate", "") or "").strip()[:20]
                    _t3a_loc  = str(eq_row_ae.get("Location", "") or "").strip()
                    _t3a_meta = "  |  ".join(p for p in [_t3a_type, _t3a_desc] if p and p not in ("nan", "—"))
                    with st.expander(
                        f"{_t3a_dot}  #{i+1}  {tag}  ·  {eq_row_ae['Name']}  ·  {_t3a_meta}  ·  {_t3a_loc}  "
                        f"·  {_t3a_cansqm:,.1f}/{_t3a_sqm:,.1f} SQM  ·  {t_pct_ae:.1f}%",
                        expanded=False,
                    ):
                        # ── Per-equipment Download + Print ──
                        _ae_xlsx, _ae_html = _per_equipment_payload(
                            tag=tag, tag_alloc=tag_alloc_ae, eq_row=eq_row_ae,
                            inv_df=inv, color_scheme="overview",
                        )
                        _aeb1, _aeb2, _aeb3, _ = st.columns([1, 1, 1, 5])
                        with _aeb1:
                            st.download_button(
                                "⬇ Excel",
                                data=_ae_xlsx,
                                file_name=f"equipment_{str(tag).replace('/','_')}_{date.today()}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"dl_ae_eq_{tag}",
                                use_container_width=True,
                            )
                        with _aeb2:
                            _pdf_download_button(
                                "⬇ PDF",
                                df=tag_alloc_ae[[
                                    "Lining_System_Code","Lining_System_Short_Name",
                                    "Material_Code","Material_Name","UOM",
                                    "Demand_Qty","Allocated_Qty","Shortfall_Qty",
                                    "Fulfillment_Pct",
                                ]].reset_index(drop=True),
                                title=f"Equipment Report — {tag}",
                                file_stem=f"equipment_{str(tag).replace('/','_')}_{date.today()}",
                                key=f"pdf_ae_eq_{tag}",
                                use_container_width=True,
                            )
                        with _aeb3:
                            st.markdown(
                                _print_button_html("🖨 Print", _ae_html, f"ae_{tag}"),
                                unsafe_allow_html=True,
                            )

                        for code in sorted(tag_alloc_ae["Lining_System_Code"].unique(), key=lambda x: int(x)):
                            code_alloc_ae = tag_alloc_ae[tag_alloc_ae["Lining_System_Code"] == code].copy()
                            sname_ae = code_alloc_ae["Lining_System_Short_Name"].iloc[0]
                            sqm_ae   = code_alloc_ae["Total_SQM"].iloc[0]
                            c_pct_ae = syscode_fulfillment(ae_alloc, tag, code)
                            _, c_can_ae, _ = sqm_can_do(ae_alloc, tag, code)
                            c_dot_ae = "🟢" if c_pct_ae >= 100 else "🟠" if c_pct_ae >= 90 else "🟡" if c_pct_ae >= 80 else "🔴"
                            st.markdown(
                                f'<div class="syscode-block"><div class="syscode-hdr">'
                                f'<span style="font-size:.85rem;">{c_dot_ae}</span>'
                                f'<span class="code-badge">Code {code}</span>'
                                f'<span style="font-size:.8rem;color:var(--t1);">{sname_ae}</span>'
                                f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:.72rem;color:var(--t3);">'
                                f'{c_can_ae:,.1f}/{sqm_ae:,.1f} SQM</span>'
                                f'<span style="margin-left:auto;">{fulfil_pill(c_pct_ae)}</span>'
                                f'</div></div>', unsafe_allow_html=True)
                            plotly_mat_table(
                                code_alloc_ae, f"ae_{tag}_{code}",
                                height=65 + len(code_alloc_ae) * 30,
                                show_sqm=True, tag=tag, code=code, allocated_label="Available"
                            )
                        # Add to session
                        if tag in st.session_state.session_tags:
                            st.markdown('<span style="font-family:\'JetBrains Mono\',monospace;font-size:.7rem;color:#10B981;">✓ In session</span>', unsafe_allow_html=True)
                        else:
                            if st.button(f"＋ Add {tag} to Session", key=f"aeadd_{tag}"):
                                st.session_state.session_tags.append(tag)
                                st.rerun()

                # Smart Reordering Suggestions
                st.markdown("<br>", unsafe_allow_html=True)
                with st.expander("💡 Smart Reordering Suggestions — All Equipment", expanded=False):
                    render_suggestion_panel(all_eq_tags, "tab3_all")

                # Download — full report with all 3 summary blocks appended
                st.markdown("<br>", unsafe_allow_html=True)
                _ae_inv_lu = inv[["Material_Code","Available_Qty","Ordered_Qty"]].groupby(
                    "Material_Code", as_index=False).first()
                _ae_export = ae_alloc.merge(_ae_inv_lu, on="Material_Code", how="left")
                _ae_export["Available_Qty"] = _ae_export["Available_Qty"].fillna(0)
                _ae_export["Ordered_Qty"]   = _ae_export["Ordered_Qty"].fillna(0)
                # Enrich with Location from equipment master
                _ae_export = _ae_export.merge(
                    eq_master[["Equipment_Tag_No.", "Location"]],
                    on="Equipment_Tag_No.", how="left",
                )
                _ae_export_cols = [
                    "Location", "Equipment_Tag_No.", "Lining_System_Code", "Lining_System_Short_Name",
                    "Total_SQM", "Material_Code", "Material_Name", "UOM",
                    "Demand_Qty", "Available_Qty", "Ordered_Qty",
                    "Allocated_Qty", "Shortfall_Qty", "Fulfillment_Pct",
                ]
                _ae_export = _ae_export[[c for c in _ae_export_cols if c in _ae_export.columns]]
                _ae_c1, _ae_c2 = st.columns(2)
                with _ae_c1:
                    st.download_button(
                        "⬇ Excel — All Equipment Report",
                        data=_location_report_excel(sheets=[{
                            "name":         "All Equipment",
                            "df":           _ae_export,
                            "title":        "All Equipment — Global Report",
                            "color_scheme": "overview",
                        }]),
                        file_name=f"all_equipment_report_{date.today()}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_ae_all",
                        use_container_width=True,
                    )
                with _ae_c2:
                    _pdf_download_button(
                        "⬇ PDF — All Equipment Report",
                        df=_ae_export,
                        title="All Equipment — Global Report",
                        file_stem=f"all_equipment_report_{date.today()}",
                        key="pdf_ae_all",
                        use_container_width=True,
                    )

            from streamlit_sortables import sort_items as _sort3

            loc_color = {
                "Brown Field": ("loc-bf","#3B82F6"),
                "TRAIN J":     ("loc-tj","#F59E0B"),
                "TRAIN K":     ("loc-tk","#10B981"),
            }

            if loc_report_mode == "📍 Location Based":
                st.markdown('<div class="sec-hdr">📍 All Equipment by Location — Cascading Balance</div>',
                            unsafe_allow_html=True)
                st.caption("Drag to reorder equipment within each location. Order is independent from the session list.")

                # R20.4 TEMP: diagnostic to find why per-location sections
                # render empty. Shows LOCATION_ORDER + eq_master location
                # values + per-location tag counts so we can spot mismatches.
                with st.expander("🔬 Location Report diagnostic", expanded=False):
                    st.caption(
                        f"`LOCATION_ORDER` ({len(LOCATION_ORDER)}): "
                        f"`{LOCATION_ORDER!r}`"
                    )
                    if eq_master is None or eq_master.empty:
                        st.warning("⚠️ eq_master is EMPTY — load_all() returned no equipment.")
                    else:
                        _loc_vals = eq_master["Location"].fillna("").astype(str).str.strip()
                        _unique_locs = sorted(_loc_vals.unique())
                        st.caption(
                            f"`eq_master.Location` distinct ({len(_unique_locs)}): "
                            f"`{_unique_locs!r}`"
                        )
                        _per_loc = _loc_vals.value_counts().to_dict()
                        st.caption(
                            f"Equipment per location: `{_per_loc!r}`"
                        )
                        _matching = [
                            loc for loc in LOCATION_ORDER if loc in _unique_locs
                        ]
                        _missing = [
                            loc for loc in LOCATION_ORDER if loc not in _unique_locs
                        ]
                        if _matching:
                            st.caption(
                                f"✓ LOCATION_ORDER matching eq_master: "
                                f"`{_matching!r}`"
                            )
                        if _missing:
                            st.warning(
                                f"⚠️ LOCATION_ORDER values **NOT** in eq_master "
                                f"(per-location loop will skip these): "
                                f"`{_missing!r}`"
                            )
                        _orphan_locs = [
                            loc for loc in _unique_locs
                            if loc and loc not in LOCATION_ORDER
                        ]
                        if _orphan_locs:
                            st.warning(
                                f"⚠️ eq_master Location values **NOT** in "
                                f"LOCATION_ORDER (won't render): "
                                f"`{_orphan_locs!r}`. "
                                f"Add them via Master Data → ➕ Add Location."
                            )

            for loc in LOCATION_ORDER:
                # Skip location rendering in All Equipment mode
                if loc_report_mode != "📍 Location Based":
                    # Still seed loc_order state so it's ready when user switches modes
                    default_loc_order = eq_master[eq_master["Location"]==loc]["Equipment_Tag_No."].tolist()
                    if loc not in st.session_state.loc_order:
                        st.session_state.loc_order[loc] = default_loc_order
                    continue
                # Initialise from file order if not set
                default_loc_order = eq_master[eq_master["Location"]==loc]["Equipment_Tag_No."].tolist()
                if loc not in st.session_state.loc_order:
                    st.session_state.loc_order[loc] = default_loc_order

                # R20.5.2 — reconcile the persisted drag-order against the
                # CURRENT equipment master. st.session_state.loc_order survives
                # across data reloads (e.g. a re-bootstrap or a Master Data
                # edit), so it can hold tags that no longer exist in eq_master;
                # the per-equipment `eq_master[... == tag].iloc[0]` below would
                # then raise `IndexError: single positional indexer is
                # out-of-bounds`. Drop stale tags, append any new ones, and
                # preserve the user's ordering for everything still valid.
                _valid_loc_tags = eq_master[eq_master["Location"] == loc][
                    "Equipment_Tag_No."].tolist()
                _reconciled = [t for t in st.session_state.loc_order[loc]
                               if t in _valid_loc_tags]
                _reconciled += [t for t in _valid_loc_tags
                                if t not in _reconciled]
                if _reconciled != st.session_state.loc_order[loc]:
                    st.session_state.loc_order[loc] = _reconciled

                loc_tags_all = st.session_state.loc_order[loc]
                if not loc_tags_all:
                    continue

                badge_cls, accent = loc_color.get(loc, ("loc-bf","#3B82F6"))

                # Compute quick fulfillment with CURRENT order for labels only
                loc_alloc_preview = cascade_allocate(loc_tags_all)

                # ── Location title above sortable list ──────────────────────────────
                st.markdown(
                    f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:.68rem;'
                    f'font-weight:700;letter-spacing:.1em;color:var(--amber);margin-bottom:.3rem;">'
                    f'📍 {loc} — Drag to set build priority</div>',
                    unsafe_allow_html=True)

                # Static labels — compact: only #, tag and SQM (no truncated name) so
                # the sortable list fits a narrower column. (Fix #4)
                def _l3_label(i, t):
                    sqm = eq_master.set_index("Equipment_Tag_No.")["Total_SQM"].get(t, 0)
                    return f"#{i+1}  ||  {t}  ||  {sqm:,.1f} SQM"

                # Render the sortable inside a narrow left-column so it is compact
                # rather than full page width.
                loc_display = [_l3_label(i, t) for i, t in enumerate(loc_tags_all)]
                loc_key     = f"loc_sort_{loc}_" + "_".join(loc_tags_all)
                _sort_l, _sort_r = st.columns([2, 3])
                with _sort_l:
                    st.markdown(
                        '<div class="sme-compact-sortable">', unsafe_allow_html=True)
                    loc_sorted = _sort3(loc_display, direction="vertical", key=loc_key)
                    st.markdown('</div>', unsafe_allow_html=True)

                def _l3_parse(label):
                    parts = label.split("  ||  ")
                    return parts[1].strip() if len(parts) > 1 else label.strip()

                new_loc_order = [_l3_parse(l) for l in loc_sorted if _l3_parse(l) in loc_tags_all]
        
                if len(new_loc_order) == len(loc_tags_all) and new_loc_order != st.session_state.loc_order[loc]:
                    st.session_state.loc_order[loc] = new_loc_order
                    st.rerun()

                # Re-cascade with (possibly new) order
                loc_alloc = cascade_allocate(loc_tags_all)

                loc_demand = loc_alloc["Demand_Qty"].sum()
                loc_short  = loc_alloc["Shortfall_Qty"].sum()
                loc_pct    = min(100,
                    loc_alloc["Allocated_Qty"].sum()/loc_demand*100
                ) if loc_demand > 0 else 100

                # Location SQM (correct: sum unique (tag,code) SQM, not from dm)
                loc_sqm_total = sqm_ref[sqm_ref["Equipment_Tag_No."].isin(loc_tags_all)]["Total_SQM"].sum()
                loc_can_sqm   = round(loc_sqm_total * min(1.0, loc_pct/100), 2)
                loc_dot       = "🟢" if loc_pct>=100 else "🟠" if loc_pct>=90 else "🟡" if loc_pct>=80 else "🔴"

                # Location header
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:1rem;'
                    f'margin:1.2rem 0 .7rem;">'
                    f'<span style="font-size:.95rem;">{loc_dot}</span>'
                    f'<span class="loc-badge {badge_cls}" style="font-size:.76rem;'
                    f'padding:.28rem .9rem;">{loc}</span>'
                    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:.75rem;'
                    f'color:var(--t4);">{len(loc_tags_all)} equip</span>'
                    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:.75rem;'
                    f'color:var(--t3);">{loc_can_sqm:,.1f}/{loc_sqm_total:,.1f} SQM</span>'
                    f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:.75rem;'
                    f'color:var(--t3);">Coverage: {loc_pct:.1f}%</span>'
                    f'</div>',
                    unsafe_allow_html=True)

                # Per-equipment expanders
                for tag in loc_tags_all:
                    tag_alloc = loc_alloc[loc_alloc["Equipment_Tag_No."]==tag]
                    t_pct     = tag_fulfillment(loc_alloc, tag)
                    t_short   = tag_alloc["Shortfall_Qty"].sum()
                    eq_row    = eq_master[eq_master["Equipment_Tag_No."]==tag].iloc[0]
                    dot       = status_dot(t_pct)

                    _dot_char  = "✅" if t_pct>=100 else "🟠" if t_pct>=90 else "🟡" if t_pct>=80 else "🔴"
                    _t3_sqm    = sqm_ref[sqm_ref["Equipment_Tag_No."]==tag]["Total_SQM"].sum()
                    _t3_cansqm = round(_t3_sqm * min(1.0, t_pct/100), 2)
                    _t3_type   = str(eq_row.get("Type","") or "").strip()
                    _t3_desc   = str(eq_row.get("Substrate","") or "").strip()[:20]
                    _t3_meta   = "  |  ".join(p for p in [_t3_type,_t3_desc] if p and p not in ("nan","—"))
                    with st.expander(
                        f"{_dot_char}  {tag}  ·  {eq_row['Name']}  ·  {_t3_meta}  ·  "
                        f"{_t3_cansqm:,.1f}/{_t3_sqm:,.1f} SQM  ·  {t_pct:.1f}%",
                        expanded=False,
                    ):
                        # ── Per-equipment Download + Print ──
                        _lb_xlsx, _lb_html = _per_equipment_payload(
                            tag=tag, tag_alloc=tag_alloc, eq_row=eq_row,
                            inv_df=inv,
                            color_scheme={"Brown Field":"brown_field",
                                          "TRAIN J":"train_j",
                                          "TRAIN K":"train_k"}.get(loc, "overview"),
                        )
                        _lbb1, _lbb2, _lbb3, _ = st.columns([1, 1, 1, 5])
                        with _lbb1:
                            st.download_button(
                                "⬇ Excel",
                                data=_lb_xlsx,
                                file_name=f"equipment_{str(tag).replace('/','_')}_{date.today()}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"dl_lb_eq_{loc}_{tag}",
                                use_container_width=True,
                            )
                        with _lbb2:
                            _pdf_download_button(
                                "⬇ PDF",
                                df=tag_alloc[[
                                    "Lining_System_Code","Lining_System_Short_Name",
                                    "Material_Code","Material_Name","UOM",
                                    "Demand_Qty","Allocated_Qty","Shortfall_Qty",
                                    "Fulfillment_Pct",
                                ]].reset_index(drop=True),
                                title=f"Equipment Report — {tag} ({loc})",
                                file_stem=f"equipment_{str(tag).replace('/','_')}_{date.today()}",
                                key=f"pdf_lb_eq_{loc}_{tag}",
                                use_container_width=True,
                            )
                        with _lbb3:
                            st.markdown(
                                _print_button_html("🖨 Print", _lb_html, f"lb_{loc}_{tag}"),
                                unsafe_allow_html=True,
                            )

                        c1,c2,c3 = st.columns(3)
                        c1.markdown(f'**Type:** {eq_row["Type"]}')
                        c2.markdown(f'**Substrate:** {eq_row["Substrate"] or "—"}')
                        c3.markdown(f'**Material Spec.:** {eq_row["Material_Spec"] or "—"}')
                        st.caption(
                            f'**Lining:** {str(eq_row["Lining_Systems"]).replace(chr(10)," | ")}')
                        if t_pct >= 100:
                            st.markdown(
                                '<div style="background:var(--green-bg);border:1px solid var(--green);'
                                'border-radius:6px;padding:.5rem .9rem;margin-bottom:.5rem;'
                                'font-family:\'JetBrains Mono\',monospace;font-size:.78rem;color:var(--green);">'
                                '✅ All materials fully covered — ready to proceed</div>',
                                unsafe_allow_html=True)
                        st.markdown("---")

                        # Per system code
                        for code in sorted(tag_alloc["Lining_System_Code"].unique(),
                                           key=lambda x: int(x)):
                            code_alloc = tag_alloc[tag_alloc["Lining_System_Code"]==code].copy()
                            sname = code_alloc["Lining_System_Short_Name"].iloc[0]
                            sqm   = code_alloc["Total_SQM"].iloc[0]
                            c_pct = syscode_fulfillment(loc_alloc, tag, code)

                            _, c3_can, c3_short_sqm = sqm_can_do(loc_alloc, tag, code)
                            c3_dot = "🟢" if c_pct>=100 else "🟠" if c_pct>=90 else "🟡" if c_pct>=80 else "🔴"
                            st.markdown(
                                f'<div class="syscode-block">'
                                f'<div class="syscode-hdr">'
                                f'<span style="font-size:.85rem;">{c3_dot}</span>'
                                f'<span class="code-badge">Code {code}</span>'
                                f'<span style="font-size:.8rem;color:var(--t1);">{sname}</span>'
                                f'<span style="font-family:\'JetBrains Mono\',monospace;'
                                f'font-size:.72rem;color:var(--t3);">'
                                f'{c3_can:,.1f}/{sqm:,.1f} SQM</span>'
                                f'<span style="margin-left:auto;">{fulfil_pill(c_pct)}</span>'
                                f'</div></div>',
                                unsafe_allow_html=True)
                            plotly_mat_table(
                                code_alloc,
                                f"loc_{loc}_{tag}_{code}",
                                height=65 + len(code_alloc)*30,
                                show_sqm=True, tag=tag, code=code,
                                allocated_label="Available"
                            )

                        # Equipment grand total
                        st.markdown(
                            f'<div style="background:rgba(245,158,11,.05);'
                            f'border:1px solid rgba(245,158,11,.18);border-radius:6px;'
                            f'padding:.65rem .9rem;margin-top:.4rem;'
                            f'font-family:\'JetBrains Mono\',monospace;font-size:.77rem;">'
                            f'<span style="color:#F59E0B;font-weight:700;">TOTAL — {tag}</span>'
                            f'<span style="color:var(--t3);margin-left:1.2rem;">'
                            f'Demand: <b style="color:var(--t1);">'
                            f'{tag_alloc["Demand_Qty"].sum():,.3f}</b></span>'
                            + (f'<span style="color:var(--t3);margin-left:.8rem;">'
                            f'Shortfall: <b style="color:#EF4444;">'
                            f'{tag_alloc["Shortfall_Qty"].sum():,.3f}</b></span>'
                            if tag_alloc["Shortfall_Qty"].sum() > 0.001 else "") +
                            f'<span style="margin-left:.8rem;">{fulfil_pill(t_pct)}</span>'
                            f'</div>',
                            unsafe_allow_html=True)

                        # Add to session button
                        if tag in st.session_state.session_tags:
                            st.markdown(
                                '<span style="font-family:\'JetBrains Mono\',monospace;'
                                'font-size:.7rem;color:#10B981;">✓ In session</span>',
                                unsafe_allow_html=True)
                        else:
                            if st.button(f"＋ Add {tag} to Session",
                                         key=f"locadd_{loc}_{tag}"):
                                st.session_state.session_tags.append(tag)
                                st.rerun()

                # Shortfall charts removed per request (Fix #4) — Location Report
                # now relies on the per-equipment material tables only.

                st.markdown(
                    f'<div style="border-bottom:1px solid #1E2E46;margin:1rem 0;"></div>',
                    unsafe_allow_html=True)


            # ── Per-location Excel downloads ────────────────────────────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            if loc_report_mode == "📍 Location Based":
                st.markdown('<div class="sec-hdr">📥 Download Report per Location</div>',
                            unsafe_allow_html=True)
            _all_loc_sheets = []
            dl_loc_cols = st.columns(len(LOCATION_ORDER))
            for _dl_col, _loc_dl in zip(dl_loc_cols, LOCATION_ORDER):
                if loc_report_mode != "📍 Location Based":
                    continue
                _loc_tags_dl = st.session_state.loc_order.get(
                    _loc_dl,
                    eq_master[eq_master["Location"] == _loc_dl]["Equipment_Tag_No."].tolist()
                )
                if not _loc_tags_dl:
                    continue
                _loc_alloc_dl = cascade_allocate(_loc_tags_dl)
                if _loc_alloc_dl.empty:
                    continue
                # Enrich with inventory data for export
                _inv_lu = inv[["Material_Code","Available_Qty","Ordered_Qty"]].groupby(
                    "Material_Code", as_index=False).first()
                _loc_report = _loc_alloc_dl.merge(_inv_lu, on="Material_Code", how="left")
                _loc_report["Available_Qty"] = _loc_report["Available_Qty"].fillna(0)
                _loc_report["Ordered_Qty"]   = _loc_report["Ordered_Qty"].fillna(0)
                _export_cols = [
                    "Equipment_Tag_No.", "Lining_System_Code", "Lining_System_Short_Name",
                    "Total_SQM", "Material_Code", "Material_Name", "UOM",
                    "Demand_Qty", "Available_Qty", "Ordered_Qty",
                    "Allocated_Qty", "Shortfall_Qty", "Fulfillment_Pct",
                ]
                _export_df = _loc_report[[c for c in _export_cols if c in _loc_report.columns]]
                _loc_cs = _LOC_COLOR_MAP.get(_loc_dl, "dashboard")
                _all_loc_sheets.append({
                    "name":            _loc_dl[:31],
                    "df":              _export_df,
                    "title":           f"Location Report — {_loc_dl}",
                    "color_scheme":    _loc_cs,
                    "add_grand_total": True,
                })
                with _dl_col:
                    _xc, _pc = st.columns(2)
                    with _xc:
                        st.download_button(
                            label=f"⬇ Excel {_loc_dl}",
                            data=_location_report_excel(sheets=[{
                                "name":         _loc_dl[:31],
                                "df":           _export_df,
                                "title":        f"Location Report — {_loc_dl}",
                                "color_scheme": _loc_cs,
                            }]),
                            file_name=f"location_report_{_loc_dl.replace(' ', '_')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key=f"dl_loc_{_loc_dl}",
                        )
                    with _pc:
                        _pdf_download_button(
                            label=f"⬇ PDF {_loc_dl}",
                            df=_export_df.reset_index(drop=True),
                            title=f"Location Report — {_loc_dl}",
                            file_stem=f"location_report_{_loc_dl.replace(' ', '_')}",
                            key=f"pdf_loc_{_loc_dl}",
                            use_container_width=True,
                        )

            if _all_loc_sheets:
                # Compose multi-sheet payload for the new writer (drop the old
                # add_grand_total flag — the new writer always adds the GT row).
                _ms_sheets = [{
                    "name":         s["name"],
                    "df":           s["df"],
                    "title":        s.get("title", s["name"]),
                    "color_scheme": s.get("color_scheme", "dashboard"),
                } for s in _all_loc_sheets]
                _lc1, _lc2 = st.columns(2)
                with _lc1:
                    st.download_button(
                        "⬇ Excel — All Locations (Multi-Sheet)",
                        data=_location_report_excel(sheets=_ms_sheets),
                        file_name=f"location_report_all_{date.today()}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key="dl_loc_all",
                    )
                with _lc2:
                    _pdf_download_button(
                        "⬇ PDF — All Locations",
                        sheets=_ms_sheets,
                        title=f"Location Report — All Locations ({date.today()})",
                        file_stem=f"location_report_all_{date.today()}",
                        key="pdf_loc_all",
                        use_container_width=True,
                    )

            if loc_report_mode == "📍 Location Based":
                # ── Print Report button ───────────────────────────────────────────────
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown('<div class="sec-hdr">🖨 Print Location Report</div>',
                            unsafe_allow_html=True)
                st.markdown("""
<button onclick="window.print()"
    style="font-family:'JetBrains Mono',monospace;font-size:.68rem;
           font-weight:700;letter-spacing:.08em;text-transform:uppercase;
           background:#F59E0B;color:#000;border:none;border-radius:4px;
           padding:.52rem 1.3rem;cursor:pointer;transition:all .15s;">
    🖨 Print / Save as PDF
</button>
<style>
@media print {
    [data-testid="stSidebar"], [data-testid="stHeader"],
    .sticky-header-wrap, [data-testid="stTabs"] > div:first-of-type,
    button[onclick="window.print()"] { display:none!important; }
    [data-testid="stExpander"] { break-inside:avoid; }
    body { background:#fff!important; color:#000!important; }
}
        </style>""", unsafe_allow_html=True)

                # ── Smart Reordering Suggestions per location ─────────────────────────
                st.markdown("<br>", unsafe_allow_html=True)
                for _loc_sugg in LOCATION_ORDER:
                    _loc_tags_sugg = st.session_state.loc_order.get(
                        _loc_sugg,
                        eq_master[eq_master["Location"]==_loc_sugg]["Equipment_Tag_No."].tolist()
                    )
                    if len(_loc_tags_sugg) < 2:
                        continue
                    with st.expander(f"💡 Smart Reordering Suggestions — {_loc_sugg}", expanded=False):
                        render_suggestion_panel(_loc_tags_sugg, f"tab3_{_loc_sugg}")

        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB · EQUIPMENT REPORT (standalone)
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab_eqrep:
            st.markdown('<div class="sec-hdr">📋 Equipment Report — Tags × System Codes</div>',
                        unsafe_allow_html=True)
            st.caption("Equipment-wise details only — surface area per system code. No materials, no demand quantities.")

            _er = equip_sc[[
                "Equipment_Tag_No.", "Lining_System_Code",
                "Lining_System_Short_Name", "Total_SQM_Original"
            ]].merge(
                eq_master[["Equipment_Tag_No.", "Location", "Type", "Name"]],
                on="Equipment_Tag_No.", how="left",
            )
            _er = _er.rename(columns={
                "Name":                      "Equipment Name",
                "Equipment_Tag_No.":         "Equipment Tag No.",
                "Lining_System_Code":        "System Code",
                "Lining_System_Short_Name":  "System Name",
                "Total_SQM_Original":        "Total SQM",
            })
            _er["Total SQM"] = _er["Total SQM"].round(2)
            # Equipment Tag No. first, then Equipment Name. (Revised order — F#3)
            _er = _er[["Location", "Type", "Equipment Tag No.", "Equipment Name",
                       "System Code", "System Name", "Total SQM"]]
            _er = _er.sort_values(
                ["Location", "Equipment Tag No.", "System Code"],
                key=lambda s: s.astype(str) if s.name != "System Code"
                              else s.astype(str).map(lambda v: int(v) if str(v).isdigit() else 9999)
            ).reset_index(drop=True)

            _er_eq_count = _er["Equipment Tag No."].nunique()
            _er_loc_count = _er["Location"].nunique()
            _er_codes_count = _er["System Code"].nunique()
            _er_sqm = round(eq_master["Total_SQM"].sum(), 1)
            er_k1, er_k2, er_k3, er_k4 = st.columns(4)
            er_k1.metric("Equipment Tags", f"{_er_eq_count}")
            er_k2.metric("Locations",       f"{_er_loc_count}")
            er_k3.metric("System Codes",    f"{_er_codes_count}")
            er_k4.metric("Total SQM",       f"{_er_sqm:,.1f}")
            st.markdown("<br>", unsafe_allow_html=True)

            st.markdown('<div class="sec-hdr">Per-Location Expandable List</div>',
                        unsafe_allow_html=True)

            _today = date.today()
            _color_map = {"Brown Field":"brown_field",
                          "TRAIN J":"train_j", "TRAIN K":"train_k"}
            _loc_badge_cls = {"Brown Field":"loc-bf","TRAIN J":"loc-tj","TRAIN K":"loc-tk"}

            for _loc in LOCATION_ORDER:
                _loc_rows = _er[_er["Location"] == _loc]
                if _loc_rows.empty:
                    continue
                _loc_tag_count = _loc_rows["Equipment Tag No."].nunique()
                _loc_sqm = eq_master[eq_master["Location"] == _loc]["Total_SQM"].sum()
                _badge_cls = _loc_badge_cls.get(_loc, "loc-bf")
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:.6rem;'
                    f'margin:.4rem 0 .3rem;padding:.5rem .8rem;'
                    f'background:var(--bg2);border:1px solid var(--border);'
                    f'border-radius:var(--r-md);">'
                    f'<span class="loc-badge {_badge_cls}">{_loc}</span>'
                    f'<span style="font-family:\'JetBrains Mono\',monospace;'
                    f'font-size:.72rem;color:var(--t3);">'
                    f'{_loc_tag_count} equipment  ·  {_loc_sqm:,.1f} SQM</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                for _tag, _tag_grp in _loc_rows.groupby("Equipment Tag No.", sort=False):
                    _tag_name = _tag_grp["Equipment Name"].iloc[0]
                    _tag_type = _tag_grp["Type"].iloc[0]
                    _tag_sqm_total = _tag_grp["Total SQM"].sum()
                    _tag_codes_count = len(_tag_grp)
                    with st.expander(
                        f"🏷  {_tag}  ·  {str(_tag_name)[:32]}  ·  {_tag_type}  ·  "
                        f"{_tag_codes_count} code(s)  ·  {_tag_sqm_total:,.2f} SQM",
                        expanded=False,
                    ):
                        _eq_df_single = _tag_grp[["Location", "Type",
                                                  "Equipment Tag No.", "Equipment Name",
                                                  "System Code", "System Name",
                                                  "Total SQM"]].reset_index(drop=True)
                        _eq_scheme = _color_map.get(_loc, "overview")
                        _eq_xlsx = _equipment_report_excel(location_sheets=[{
                            "name":         str(_tag)[:31],
                            "df":           _eq_df_single,
                            "title":        f"Equipment Report — {_tag}",
                            "color_scheme": _eq_scheme,
                        }])
                        _eq_print_html = _build_print_html(
                            title=f"Equipment Report — {_tag}",
                            sections=[
                                {"kind":"kv","title":"Equipment Details","rows":[
                                    ("Equipment Tag",  str(_tag)),
                                    ("Name",           str(_tag_name)),
                                    ("Type",           str(_tag_type)),
                                    ("Location",       str(_loc)),
                                    ("System Codes",   ", ".join(
                                        sorted({str(c) for c in _tag_grp["System Code"]}))),
                                    ("Total SQM",      f"{float(_tag_sqm_total):,.2f}"),
                                ]},
                                {"kind":"table","title":"System Codes",
                                 "columns":["System Code","System Name","Total SQM"],
                                 "rows":[[r["System Code"], r["System Name"],
                                          round(float(r["Total SQM"]),2)]
                                         for _, r in _eq_df_single.iterrows()]},
                            ],
                        )
                        _b1, _b2, _b3, _ = st.columns([1, 1, 1, 5])
                        with _b1:
                            st.download_button(
                                "⬇ Excel",
                                data=_eq_xlsx,
                                file_name=f"equipment_{str(_tag).replace('/','_')}_{_today}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"dl_er_eq_{_loc}_{_tag}",
                                use_container_width=True,
                            )
                        with _b2:
                            _pdf_download_button(
                                "⬇ PDF",
                                df=_eq_df_single,
                                title=f"Equipment Report — {_tag}",
                                file_stem=f"equipment_{str(_tag).replace('/','_')}_{_today}",
                                key=f"pdf_er_eq_{_loc}_{_tag}",
                                use_container_width=True,
                            )
                        with _b3:
                            st.markdown(
                                _print_button_html("🖨 Print", _eq_print_html,
                                                    f"er_{_loc}_{_tag}"),
                                unsafe_allow_html=True,
                            )

                        for _, _row in _tag_grp.iterrows():
                            st.markdown(
                                f'<div style="display:flex;align-items:center;gap:.6rem;'
                                f'padding:.4rem .65rem;margin:.18rem 0;background:var(--bg3);'
                                f'border:1px solid var(--border);border-radius:var(--r-sm);">'
                                f'<span class="code-badge">Code {_row["System Code"]}</span>'
                                f'<span style="font-size:.78rem;color:var(--t1);">'
                                f'{_row["System Name"]}</span>'
                                f'<span style="margin-left:auto;font-family:\'JetBrains Mono\','
                                f'monospace;font-size:.78rem;font-weight:700;color:var(--amber);">'
                                f'{_row["Total SQM"]:,.2f} SQM</span></div>',
                                unsafe_allow_html=True,
                            )

            st.markdown("<br>", unsafe_allow_html=True)

            st.markdown('<div class="sec-hdr">📥 Download Equipment Report</div>',
                        unsafe_allow_html=True)
            _dl_cols = st.columns(len(LOCATION_ORDER) + 1)
            _dl_columns = ["Location", "Type", "Equipment Tag No.", "Equipment Name",
                           "System Code", "System Name", "Total SQM"]

            for _i, _loc in enumerate(LOCATION_ORDER):
                _loc_df = _er[_er["Location"] == _loc][_dl_columns].reset_index(drop=True)
                _scheme = _color_map.get(_loc, "overview")
                with _dl_cols[_i]:
                    _ex_c, _pd_c = st.columns(2)
                    with _ex_c:
                        st.download_button(
                            f"⬇ Excel {_loc}",
                            data=_equipment_report_excel(
                                location_sheets=[{
                                    "name":         _loc[:31],
                                    "df":           _loc_df,
                                    "title":        f"Equipment Report — {_loc}",
                                    "color_scheme": _scheme,
                                }],
                            ),
                            file_name=f"equipment_report_{_loc.replace(' ','_').lower()}_{_today}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_er_{_i}",
                            use_container_width=True,
                            disabled=_loc_df.empty,
                        )
                    with _pd_c:
                        _pdf_download_button(
                            f"⬇ PDF {_loc}",
                            df=_loc_df,
                            title=f"Equipment Report — {_loc}",
                            file_stem=f"equipment_report_{_loc.replace(' ','_').lower()}_{_today}",
                            key=f"pdf_er_{_i}",
                            use_container_width=True,
                            disabled=_loc_df.empty,
                        )

            with _dl_cols[-1]:
                _loc_sheets_payload = []
                for _loc in LOCATION_ORDER:
                    _loc_df = _er[_er["Location"] == _loc][_dl_columns].reset_index(drop=True)
                    if _loc_df.empty:
                        continue
                    _loc_sheets_payload.append({
                        "name":         _loc[:31],
                        "df":           _loc_df,
                        "title":        f"Equipment Report — {_loc}",
                        "color_scheme": _color_map.get(_loc, "overview"),
                    })
                _all_eq_payload = {
                    "name":         "All Equipment",
                    "df":           _er[_dl_columns].reset_index(drop=True),
                    "title":        "Equipment Report — All Locations",
                    "color_scheme": "overview",
                }
                st.download_button(
                    "⬇ Excel — All Equipment (Multi-sheet)",
                    data=_equipment_report_excel(
                        location_sheets=_loc_sheets_payload,
                        all_eq_sheet=_all_eq_payload,
                        include_all_codes_sheet=True,
                    ),
                    file_name=f"equipment_report_all_{_today}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_er_all",
                    use_container_width=True,
                )
                _pdf_download_button(
                    "⬇ PDF — All Equipment",
                    sheets=(_loc_sheets_payload + [_all_eq_payload]),
                    title=f"Equipment Report — All Locations ({_today})",
                    file_stem=f"equipment_report_all_{_today}",
                    key="pdf_er_all",
                    use_container_width=True,
                )

        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 4 · EXECUTION PLAN
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab4:
            st.markdown('<div class="sec-hdr">⚙️ Execution Plan — Critical System Code Analysis</div>',
                        unsafe_allow_html=True)

            exec_subview = st.radio(
                "View",
                ["⚙️ Execution Plan", "📋 Progress List", "📊 Consumption Comparison"],
                horizontal=True, key="exec_subview", label_visibility="collapsed"
            )
            st.markdown("<hr>", unsafe_allow_html=True)

            # ── Consumption Comparison ────────────────────────────────────────────────
            if exec_subview == "📊 Consumption Comparison":
                if not db_available():
                    st.warning("Database required for Consumption Comparison.")
                else:
                    conn = get_db()
                    _cmp_raw = pd.read_sql("""
SELECT entry_date          AS "Date",
               equipment_tag       AS "Equipment Tag",
               lining_system_code  AS "System Code",
               lining_system_name  AS "System Name",
               sqm_completed       AS "SQM Done",
               material_code       AS "Material Code",
               material_name       AS "Material Name",
               uom                 AS "UOM",
               expected_qty        AS "Expected Qty",
               consumed_qty        AS "Actual Qty"
        FROM consumption_log
            """, conn)
                    conn.close()

                    if _cmp_raw.empty:
                        st.info("No consumption entries recorded yet.")
                    else:
                        # SQM Done is per (Date × Equipment × Code) — must dedupe before
                        # summing so we don't multiply by material-row count.
                        _sqm_per_day = (
                            _cmp_raw[["Date","Equipment Tag","System Code","SQM Done"]]
                            .drop_duplicates(subset=["Date","Equipment Tag","System Code"])
                        )
                        _sqm_totals = _sqm_per_day.groupby(
                            ["Equipment Tag","System Code"], as_index=False
                        )["SQM Done"].sum().rename(columns={"SQM Done":"Total SQM Done"})

                        # Aggregate Expected + Actual across all dates per (tag,code,material).
                        _mat_totals = _cmp_raw.groupby(
                            ["Equipment Tag","System Code","System Name",
                             "Material Code","Material Name","UOM"],
                            as_index=False,
                        )[["Expected Qty","Actual Qty"]].sum()
                        _mat_totals = _mat_totals.rename(columns={
                            "Expected Qty": "Total Expected",
                            "Actual Qty":   "Total Actual",
                        })
                        _mat_totals["Variance"]   = (
                            _mat_totals["Total Actual"] - _mat_totals["Total Expected"]
                        ).round(3)
                        _mat_totals["Variance %"] = _mat_totals.apply(
                            lambda r: round(
                                (r["Variance"] / r["Total Expected"] * 100)
                                if r["Total Expected"] > 0 else 0.0, 1),
                            axis=1,
                        )

                        # Enrich with Location, Equipment Name and Surface Area SQM.
                        _eq_lu = eq_master[["Equipment_Tag_No.","Location","Name"]] \
                            .rename(columns={"Equipment_Tag_No.":"Equipment Tag",
                                             "Name":"Equipment Name"})
                        _sa_lu = equip_sc[["Equipment_Tag_No.","Lining_System_Code",
                                           "Total_SQM_Original"]] \
                            .rename(columns={
                                "Equipment_Tag_No.":     "Equipment Tag",
                                "Lining_System_Code":    "System Code",
                                "Total_SQM_Original":    "Surface Area SQM",
                            })
                        _cmp = _mat_totals.merge(_eq_lu, on="Equipment Tag", how="left")
                        _cmp = _cmp.merge(_sa_lu, on=["Equipment Tag","System Code"], how="left")
                        _cmp = _cmp.merge(_sqm_totals, on=["Equipment Tag","System Code"], how="left")

                        _cmp = _cmp[[
                            "Location","Equipment Tag","Equipment Name",
                            "System Code","System Name","Surface Area SQM",
                            "Material Code","Material Name","UOM",
                            "Total SQM Done","Total Expected","Total Actual",
                            "Variance","Variance %",
                        ]].reset_index(drop=True)

                        # ── Filters ───────────────────────────────────────────────────
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            _loc_opts = sorted(_cmp["Location"].dropna().unique().tolist())
                            _f_loc = st.multiselect("Location", options=_loc_opts,
                                default=_loc_opts, key="cmp_loc")
                        with fc2:
                            _eq_pool = _cmp[_cmp["Location"].isin(_f_loc)] if _f_loc else _cmp
                            _eq_opts = sorted(_eq_pool["Equipment Tag"].dropna().unique().tolist())
                            _f_eq  = st.multiselect("Equipment Tag", options=_eq_opts,
                                default=_eq_opts, key="cmp_eq")
                        with fc3:
                            _sc_pool = _eq_pool[_eq_pool["Equipment Tag"].isin(_f_eq)] if _f_eq else _eq_pool
                            _sc_labels = sorted(
                                {f"Code {r['System Code']} – {r['System Name']}"
                                 for _, r in _sc_pool[["System Code","System Name"]].drop_duplicates().iterrows()},
                                key=lambda s: int(s.split(" ")[1])
                            )
                            _f_sc_raw = st.multiselect("System Code", options=_sc_labels,
                                default=_sc_labels, key="cmp_sc")
                            _f_sc = [s.split(" – ")[0].replace("Code ","").strip()
                                     for s in _f_sc_raw]

                        _filt = _cmp.copy()
                        if _f_loc: _filt = _filt[_filt["Location"].isin(_f_loc)]
                        if _f_eq:  _filt = _filt[_filt["Equipment Tag"].isin(_f_eq)]
                        if _f_sc:  _filt = _filt[_filt["System Code"].isin(_f_sc)]
                        _filt = _filt.reset_index(drop=True)

                        # ── KPI strip ─────────────────────────────────────────────────
                        _t_exp = _filt["Total Expected"].sum()
                        _t_act = _filt["Total Actual"].sum()
                        _t_var = _t_act - _t_exp
                        _t_var_pct = (_t_var / _t_exp * 100) if _t_exp > 0 else 0.0
                        k1,k2,k3,k4 = st.columns(4)
                        k1.metric("Rows",            f"{len(_filt):,}")
                        k2.metric("Total Expected",  f"{_t_exp:,.3f}")
                        k3.metric("Total Actual",    f"{_t_act:,.3f}")
                        k4.metric("Variance",        f"{_t_var:+,.3f}",
                                  delta=f"{_t_var_pct:+.1f}%")
                        st.markdown("<br>", unsafe_allow_html=True)

                        # ── Table ─────────────────────────────────────────────────────
                        def _style_cmp(row):
                            v = row["Variance %"] or 0
                            if   v > 1.0:  bg = "rgba(245,158,11,.10)"   # over consumption
                            elif v < -1.0: bg = "rgba(59,130,246,.10)"   # under consumption
                            else:          bg = "rgba(16,185,129,.08)"   # on target
                            return [f"background-color:{bg}"] * len(row)

                        st.dataframe(
                            _filt.style.apply(_style_cmp, axis=1).format({
                                "Surface Area SQM": "{:,.2f}",
                                "Total SQM Done":   "{:,.2f}",
                                "Total Expected":   "{:,.3f}",
                                "Total Actual":     "{:,.3f}",
                                "Variance":         "{:+,.3f}",
                                "Variance %":       "{:+,.1f}%",
                            }),
                            use_container_width=True, hide_index=True,
                            height=min(700, 60 + len(_filt) * 35),
                            key="cmp_tbl",
                        )

                        # ── Downloads ─────────────────────────────────────────────────
                        _cmp_c1, _cmp_c2 = st.columns(2)
                        with _cmp_c1:
                            st.download_button(
                                "⬇ Excel — Consumption Comparison",
                                data=generate_excel_report(
                                    _filt,
                                    f"Consumption Comparison — {date.today()}",
                                    color_scheme="execution"),
                                file_name=f"consumption_comparison_{date.today()}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key="dl_cmp_xlsx",
                                use_container_width=True,
                            )
                        with _cmp_c2:
                            _pdf_download_button(
                                "⬇ PDF — Consumption Comparison",
                                df=_filt,
                                title=f"Consumption Comparison — {date.today()}",
                                file_stem=f"consumption_comparison_{date.today()}",
                                key="dl_cmp_pdf",
                                use_container_width=True,
                            )

            if exec_subview == "📋 Progress List":
                if not db_available():
                    st.warning("Database required for the Progress List.")
                else:
                    conn = get_db()
                    prog_df = pd.read_sql("""
WITH dynamic_done AS (
            SELECT
                equipment_tag,
                lining_system_code,
                COALESCE(SUM(sqm_completed), 0.0) AS done_sqm
            FROM consumption_log
            GROUP BY equipment_tag, lining_system_code
        )
        SELECT
            e.location                                              AS "Location",
            sp.equipment_tag                                        AS "Equipment Tag",
            e.name                                                  AS "Equipment Name",
            sp.lining_system_code                                   AS "System Code",
            e.lining_system_short_name                              AS "System Name",
            sp.original_sqm                                         AS "Total SQM",
            COALESCE(dd.done_sqm, 0.0)                             AS "Completed SQM",
            (sp.original_sqm - COALESCE(dd.done_sqm, 0.0))        AS "Remaining SQM",
            ROUND(COALESCE(dd.done_sqm, 0.0) * 100.0
                  / NULLIF(sp.original_sqm, 0), 1)                 AS "Completion %"
        FROM sqm_progress sp
        LEFT JOIN dynamic_done dd
               ON sp.equipment_tag      = dd.equipment_tag
              AND sp.lining_system_code = dd.lining_system_code
        LEFT JOIN equipment e
               ON sp.equipment_tag      = e.equipment_tag
              AND sp.lining_system_code = e.lining_system_code
        ORDER BY e.location, sp.equipment_tag,
                 CAST(sp.lining_system_code AS INTEGER)
            """, conn)

                    # Pull all consumption rows once for the production-detail panels below.
                    cons_df = pd.read_sql("""
SELECT entry_date       AS "Date",
               equipment_tag    AS "Equipment Tag",
               lining_system_code AS "System Code",
               lining_system_name AS "System Name",
               sqm_completed    AS "SQM Done",
               material_code    AS "Material Code",
               material_name    AS "Material Name",
               uom              AS "UOM",
               consumed_qty     AS "Consumed Qty"
        FROM consumption_log
        ORDER BY entry_date, equipment_tag,
                 CAST(lining_system_code AS INTEGER)
            """, conn)
                    conn.close()

                    prog_df["Status"] = prog_df["Completion %"].apply(
                        lambda p: "✅ Complete"    if (p or 0) >= 100
                                  else "🔄 In Progress" if (p or 0) > 0
                                  else "⏳ Not Started"
                    )

                    tot_orig = prog_df["Total SQM"].sum()
                    tot_done = prog_df["Completed SQM"].sum()
                    tot_rem  = prog_df["Remaining SQM"].sum()
                    tot_pct  = (tot_done / tot_orig * 100) if tot_orig > 0 else 0.0

                    pk1, pk2, pk3, pk4 = st.columns(4)
                    pk1.metric("Total SQM",     f"{tot_orig:,.2f}")
                    pk2.metric("Completed SQM", f"{tot_done:,.2f}")
                    pk3.metric("Remaining SQM", f"{tot_rem:,.2f}")
                    pk4.metric("Completion",    f"{tot_pct:.1f}%")
                    st.markdown("<br>", unsafe_allow_html=True)

                    pf1, pf2 = st.columns(2)
                    with pf1:
                        prog_locs   = ["All"] + sorted(prog_df["Location"].dropna().unique().tolist())
                        prog_loc_f  = st.selectbox("Filter Location", prog_locs, key="prog_loc_f")
                    with pf2:
                        prog_stat_opts = ["All", "✅ Complete", "🔄 In Progress", "⏳ Not Started"]
                        prog_status_f  = st.selectbox("Filter Status", prog_stat_opts, key="prog_status_f")

                    filt_prog = prog_df.copy()
                    if prog_loc_f    != "All": filt_prog = filt_prog[filt_prog["Location"] == prog_loc_f]
                    if prog_status_f != "All": filt_prog = filt_prog[filt_prog["Status"]   == prog_status_f]
                    filt_prog = filt_prog.reset_index(drop=True)

                    # Display column order (Status is internal; not shown in table).
                    _prog_cols = ["Location", "Equipment Tag", "Equipment Name",
                                  "System Code", "System Name", "Total SQM",
                                  "Completed SQM", "Remaining SQM", "Completion %"]
                    _prog_view = filt_prog[_prog_cols].copy()

                    def _style_prog(row):
                        p = row["Completion %"] or 0
                        if p >= 100:  bg, tc = "rgba(16,185,129,.1)", "#10B981"
                        elif p > 0:   bg, tc = "rgba(245,158,11,.1)",  "#F59E0B"
                        else:         bg, tc = "rgba(239,68,68,.1)",   "#EF4444"
                        styles = [f"background-color:{bg}"] * len(row)
                        ci = list(row.index).index("Completion %")
                        styles[ci] = f"background-color:{bg};color:{tc};font-weight:700"
                        return styles

                    st.dataframe(
                        _prog_view.style.apply(_style_prog, axis=1).format({
                            "Total SQM":     "{:,.2f}",
                            "Completed SQM": "{:,.2f}",
                            "Remaining SQM": "{:,.2f}",
                            "Completion %":  "{:.1f}%",
                        }),
                        use_container_width=True, hide_index=True,
                        height=min(700, 60 + len(_prog_view) * 35),
                        key="prog_list_tbl"
                    )

                    # ── Production Details — date-wise per (Equipment × System Code) ──
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.markdown('<div class="sec-hdr">📊 Production Details — Date-wise per Equipment & System Code</div>',
                                unsafe_allow_html=True)

                    _detail_blocks = []  # collect for the Excel export
                    if filt_prog.empty:
                        st.info("No rows in current filter.")
                    elif cons_df.empty:
                        st.info("No consumption entries recorded yet.")
                    else:
                        # Restrict to rows that have at least one consumption entry.
                        _pairs = filt_prog[["Equipment Tag", "System Code", "System Name",
                                            "Equipment Name", "Location"]].drop_duplicates() \
                            .reset_index(drop=True)
                        _idx = 0
                        for _, _row in _pairs.iterrows():
                            _tag = _row["Equipment Tag"]
                            _code = _row["System Code"]
                            _sub = cons_df[
                                (cons_df["Equipment Tag"] == _tag) &
                                (cons_df["System Code"] == str(_code))
                            ].copy()
                            if _sub.empty:
                                continue
                            _idx += 1
                            _sname = _row["System Name"]
                            _ename = _row["Equipment Name"]
                            _loc   = _row["Location"]
                            st.markdown(
                                f'<div style="margin-top:1rem;padding:.55rem .9rem;'
                                f'background:var(--bg2);border:1px solid var(--border);'
                                f'border-left:4px solid #F59E0B;border-radius:6px;">'
                                f'<span style="font-family:\'JetBrains Mono\',monospace;'
                                f'font-size:.8rem;font-weight:700;color:#F59E0B;">'
                                f'{_idx}. {_tag} — {_ename}</span>'
                                f'<span style="font-family:\'JetBrains Mono\',monospace;'
                                f'font-size:.72rem;color:var(--t3);margin-left:.8rem;">'
                                f'Code {_code} · {_sname} · {_loc}</span></div>',
                                unsafe_allow_html=True,
                            )

                            # SQM Done per date (one row per work date).
                            _sqm_by_date = (
                                _sub.groupby("Date", as_index=False)["SQM Done"].first()
                                .sort_values("Date").reset_index(drop=True)
                            )
                            # Material consumption per (Date × Material) — pivot for readability.
                            _mat_by_date = (
                                _sub.groupby(["Date", "Material Code", "Material Name", "UOM"],
                                             as_index=False)["Consumed Qty"].sum()
                                .sort_values(["Date", "Material Code"]).reset_index(drop=True)
                            )
                            _detail_df = _mat_by_date.merge(_sqm_by_date, on="Date", how="left")
                            _detail_df = _detail_df[["Date", "SQM Done", "Material Code",
                                                     "Material Name", "UOM", "Consumed Qty"]]
                            st.dataframe(
                                _detail_df.style.format({
                                    "SQM Done":     "{:,.2f}",
                                    "Consumed Qty": "{:,.3f}",
                                }),
                                use_container_width=True, hide_index=True,
                                height=min(360, 55 + len(_detail_df) * 33),
                                key=f"prog_detail_{_tag}_{_code}",
                            )
                            _detail_blocks.append({
                                "sheet": f"{_idx}. {str(_tag)[:8]}-{_code}"[:31],
                                "title": f"{_idx}. {_tag} · Code {_code} ({_sname}) — {_ename} [{_loc}]",
                                "df":    _detail_df,
                            })
                        if _idx == 0:
                            st.info("No consumption entries match the current filter.")

                    # ── Download — Progress List + Production Details ─────────────────
                    _dl_sheets = [{
                        "name":         "Progress List",
                        "df":           _prog_view,
                        "title":        f"Progress List — {date.today()}",
                        "color_scheme": "overview",
                    }]
                    for _b in _detail_blocks:
                        _dl_sheets.append({
                            "name":         _b["sheet"],
                            "df":           _b["df"],
                            "title":        _b["title"],
                            "color_scheme": "train_j",
                        })
                    _pl_c1, _pl_c2 = st.columns(2)
                    with _pl_c1:
                        st.download_button(
                            "⬇ Excel — Progress List",
                            data=generate_multi_sheet_excel(_dl_sheets),
                            file_name=f"progress_list_{date.today()}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_prog_list",
                            use_container_width=True,
                        )
                    with _pl_c2:
                        _pdf_download_button(
                            "⬇ PDF — Progress List",
                            sheets=_dl_sheets,
                            title=f"Progress List — {date.today()}",
                            file_stem=f"progress_list_{date.today()}",
                            key="pdf_prog_list",
                            use_container_width=True,
                        )

            session_tags = st.session_state.session_tags

            if exec_subview in ("📋 Progress List", "📊 Consumption Comparison"):
                pass  # sub-view already rendered above
            elif not session_tags:
                st.info("Add equipment tags in the Entry tab first.")
            else:
                alloc_df   = cascade_allocate(session_tags)
                tag_name   = eq_master.set_index("Equipment_Tag_No.")["Name"].to_dict()

                sel_tag = st.selectbox(
                    "Select Equipment",
                    options=session_tags,
                    format_func=lambda t: f"{t}  —  {tag_name.get(t,t)}",
                    key="exec_tag",
                )

                tag_alloc = alloc_df[alloc_df["Equipment_Tag_No."]==sel_tag]
                avail_codes = sorted(tag_alloc["Lining_System_Code"].unique(),
                                     key=lambda x: int(x))

                if not avail_codes:
                    st.warning("No system code data for this equipment.")
                else:
                    sel_code = st.selectbox(
                        "Select Critical System Code",
                        options=avail_codes,
                        format_func=lambda c: (
                            f"Code {c}  —  "
                            f"{tag_alloc[tag_alloc['Lining_System_Code']==c]['Lining_System_Short_Name'].iloc[0]}"
                        ),
                        key="exec_code",
                    )

                    st.markdown("<br>", unsafe_allow_html=True)

                    # Critical system code data
                    crit = tag_alloc[tag_alloc["Lining_System_Code"]==sel_code].copy()
                    crit_sname  = crit["Lining_System_Short_Name"].iloc[0]
                    crit_sqm    = crit["Total_SQM"].iloc[0]
                    crit_demand = crit["Demand_Qty"].sum()
                    crit_alloc  = crit["Allocated_Qty"].sum()
                    crit_short  = crit["Shortfall_Qty"].sum()
                    crit_pct    = min(100, crit_alloc/crit_demand*100) if crit_demand > 0 else 100

                    # Other system codes
                    other_codes = [c for c in avail_codes if c != sel_code]
                    other_alloc = tag_alloc[tag_alloc["Lining_System_Code"].isin(other_codes)]
                    other_short = other_alloc["Shortfall_Qty"].sum()

                    # ── Critical system code card ─────────────────────────────────────
                    st.markdown(
                        f'<div class="card card-amber">'
                        f'<div style="font-family:\'JetBrains Mono\',monospace;'
                        f'font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;'
                        f'color:#F59E0B;margin-bottom:.6rem;">Critical System Code</div>'
                        f'<div style="display:flex;align-items:center;gap:1rem;'
                        f'margin-bottom:.8rem;">'
                        f'<span class="code-badge" style="font-size:.85rem;'
                        f'padding:.3rem .8rem;">Code {sel_code}</span>'
                        f'<span style="font-size:.95rem;font-weight:600;color:var(--t0);">'
                        f'{crit_sname}</span>'
                        f'<span style="font-family:\'JetBrains Mono\',monospace;'
                        f'font-size:.8rem;color:var(--t3);">{crit_sqm:,.2f} SQM</span>'
                        f'<span style="margin-left:auto;font-family:\'JetBrains Mono\','
                        f'monospace;font-size:1.4rem;font-weight:700;'
                        f'color:{"#10B981" if crit_pct>=100 else "#F97316" if crit_pct>=90 else "#EAB308" if crit_pct>=80 else "#EF4444"};">'
                        f'{crit_pct:.1f}%</span>'
                        f'</div>'
                        f'<div style="font-size:.82rem;color:var(--t2);line-height:1.7;">'
                        f'With current inventory allocation, <strong style="color:#F59E0B;">'
                        f'{crit_pct:.1f}%</strong> of System Code {sel_code} ({crit_sname}) '
                        f'can be completed for <strong style="color:var(--t0);">{sel_tag}</strong>. '
                        + (
                            "✅ All materials for this system code are fully covered."
                            if crit_short == 0 else
                            f'⚠️ <strong style="color:#EF4444;">{crit_short:,.3f} units</strong>'
                            f' short across {(crit["Shortfall_Qty"]>0).sum()} material(s) — order these first to proceed.'
                        ) +
                        f'</div></div>',
                        unsafe_allow_html=True)

                    # Full materials table — all system codes
                    st.markdown('<div class="sec-hdr" style="margin-top:1rem;">'
                                'All Materials — Status Overview</div>',
                                unsafe_allow_html=True)
                    st.caption(
                        f"Showing all {len(tag_alloc)} material rows across "
                        f"{len(avail_codes)} system code(s) for {sel_tag}. "
                        f"Cascade balance applied (priority position #{session_tags.index(sel_tag)+1})."
                    )
                    plotly_mat_table(
                        tag_alloc.copy(),
                        f"exec_all_{sel_tag}",
                        height=80 + len(tag_alloc)*30
                    )

                    # ── ORDER PRIORITY SECTION ─────────────────────────────────────────
                    st.markdown('<div class="sec-hdr" style="margin-top:1.2rem;">'
                                '📋 Procurement Order Priority</div>',
                                unsafe_allow_html=True)

                    # 1️⃣ Critical code shortages
                    crit_short_df = crit[crit["Shortfall_Qty"]>0][
                        ["Material_Code","Material_Name","UOM","Demand_Qty",
                         "Allocated_Qty","Shortfall_Qty","Fulfillment_Pct"]
                    ].copy()

                    st.markdown(
                        f'<div style="background:var(--red-bg);border:1px solid '
                        f'rgba(239,68,68,.25);border-left:4px solid #EF4444;'
                        f'border-radius:6px;padding:.8rem 1rem;margin-bottom:.6rem;">'
                        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:.65rem;'
                        f'letter-spacing:.12em;text-transform:uppercase;color:#EF4444;'
                        f'margin-bottom:.4rem;">1️⃣ Order First — System Code {sel_code} '
                        f'({crit_sname}) · Critical Path</div>'
                        f'<div style="font-size:.8rem;color:var(--t2);">'
                        f'{"No shortages on critical system code — fully covered ✅" if crit_short_df.empty else f"{len(crit_short_df)} material(s) need to be procured for this system code before work can begin."}'
                        f'</div></div>',
                        unsafe_allow_html=True)

                    if not crit_short_df.empty:
                        plotly_mat_table(crit_short_df,
                                         f"exec_crit_{sel_tag}_{sel_code}",
                                         height=65+len(crit_short_df)*30)

                    # 2️⃣ Other system codes shortages
                    for code in other_codes:
                        code_alloc_df = tag_alloc[tag_alloc["Lining_System_Code"]==code]
                        code_short    = code_alloc_df[code_alloc_df["Shortfall_Qty"]>0]
                        sname_o       = code_alloc_df["Lining_System_Short_Name"].iloc[0]
                        code_pct      = syscode_fulfillment(alloc_df, sel_tag, code)

                        order_num = other_codes.index(code) + 2
                        st.markdown(
                            f'<div style="background:var(--amber-bg);border:1px solid '
                            f'rgba(245,158,11,.2);border-left:4px solid #F59E0B;'
                            f'border-radius:6px;padding:.8rem 1rem;margin-bottom:.5rem;">'
                            f'<div style="font-family:\'JetBrains Mono\',monospace;'
                            f'font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;'
                            f'color:#F59E0B;margin-bottom:.4rem;">'
                            f'{order_num}️⃣ Order Next — System Code {code} ({sname_o}) '
                            f'· Coverage: {code_pct:.1f}%</div>'
                            f'<div style="font-size:.8rem;color:var(--t2);">'
                            f'{"All materials covered ✅" if code_short.empty else f"{len(code_short)} material(s) short. Order after critical system code is secured."}'
                            f'</div></div>',
                            unsafe_allow_html=True)

                        if not code_short.empty:
                            code_short_display = code_short[
                                ["Material_Code","Material_Name","UOM","Demand_Qty",
                                 "Allocated_Qty","Shortfall_Qty","Fulfillment_Pct"]
                            ].copy()
                            plotly_mat_table(code_short_display,
                                             f"exec_other_{sel_tag}_{code}",
                                             height=65+len(code_short_display)*30)

                    # Summary box
                    all_short_df = tag_alloc[tag_alloc["Shortfall_Qty"]>0]
                    total_to_order = all_short_df["Shortfall_Qty"].sum()

                    st.markdown(f"""
                    <div class="grand-box" style="margin-top:1rem;">
                      <div style="font-family:'JetBrains Mono',monospace;font-size:.6rem;
                                  letter-spacing:.14em;text-transform:uppercase;
                                  color:#F59E0B;margin-bottom:.6rem;">
                        Execution Summary — {sel_tag}</div>
                      <div style="font-size:.82rem;color:var(--t2);line-height:1.8;">
                        Critical system code <strong style="color:#F59E0B;">
                        Code {sel_code} ({crit_sname})</strong> is at
                        <strong style="color:{'#10B981' if crit_pct>=100 else '#EF4444'};">
                        {crit_pct:.1f}%</strong> coverage.
                        {"All critical materials are secured — proceed to other system codes." if crit_pct>=100
                          else f"Order {len(crit_short_df)} critical material(s) totalling "
                               f"<strong style='color:#EF4444;'>{crit_short:,.3f} units</strong> first."}
                        {"" if other_short==0 else
                          f" Additionally, other system codes require "
                          f"<strong style='color:#F59E0B;'>{other_short:,.3f} units</strong>"
                          f" across {len(all_short_df[~all_short_df['Lining_System_Code'].isin([sel_code])])} material(s)."}
                        <br>
                        <strong style="color:var(--t0);">
                        Total to order for full completion: {total_to_order:,.3f} units
                        across {len(all_short_df)} material(s).</strong>
                      </div>
                    </div>""", unsafe_allow_html=True)

                    if not all_short_df.empty:
                        st.markdown("<br>", unsafe_allow_html=True)
                        _exec_df = all_short_df[
                            ["Lining_System_Code","Lining_System_Short_Name",
                             "Material_Code","Material_Name","UOM",
                             "Demand_Qty","Allocated_Qty","Shortfall_Qty","Fulfillment_Pct"]
                        ].sort_values(["Lining_System_Code","Shortfall_Qty"],
                                      ascending=[True,False]).reset_index(drop=True)
                        _exec_c1, _exec_c2 = st.columns(2)
                        with _exec_c1:
                            st.download_button(
                                f"⬇ Excel — Execution Order List — {sel_tag}",
                                data=generate_excel_report(
                                    _exec_df,
                                    f"Execution Plan – {sel_tag}", color_scheme="execution"),
                                file_name=f"execution_plan_{sel_tag.replace('/','-')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )
                        with _exec_c2:
                            _pdf_download_button(
                                f"⬇ PDF — Execution Order List — {sel_tag}",
                                df=_exec_df,
                                title=f"Execution Plan – {sel_tag}",
                                file_stem=f"execution_plan_{sel_tag.replace('/','-')}",
                                key=f"pdf_exec_{sel_tag}",
                                use_container_width=True,
                            )

        # ═══════════════════════════════════════════════════════════════════════════════
        # R20 EDIT: original SME Tab 6 `📦 Inventory` block deleted in its entirety
        # (~1400 LOC across 6 sub-views: Inventory Dashboard, Consumption, Order
        # Status, New Order, Receipt Log, Consumption Log). Round 18 wired the
        # SME consumption flow into the ERP's `daily_issue_log.py` Consumption
        # tab as the `🧪 SME Multi-Material Entry` expander, which routes through
        # stage_sme_consumption_batch() + commit_eod_with_sme_sync() for the
        # proper EOD ledger commit. Receipts are handled by the existing ERP
        # Receipt Staging tab.
        # ═══════════════════════════════════════════════════════════════════════════════


        # ═══════════════════════════════════════════════════════════════════════════════
        # TAB 5 · TOTAL OVERVIEW  (Master Filterable Datatable)
        # ═══════════════════════════════════════════════════════════════════════════════
        with tab5:
            st.markdown('<div class="sec-hdr">📈 Total Overview — Master Equipment & Material Table</div>',
                        unsafe_allow_html=True)

            # ── Build master table: one row per (Equipment, System Code) ─────────────
            # Join eq_master + sqm_ref + demand aggregation
            sqm_ref_ov = equip_sc[["Equipment_Tag_No.","Lining_System_Code",
                                    "Lining_System_Short_Name","Total_SQM_Original",
                                    "done_sqm","Total_SQM"]].drop_duplicates()

            # Total demand per (tag, code) from recipe × SQM
            dm_agg = dm.groupby(["Equipment_Tag_No.","Lining_System_Code"],
                                 as_index=False).agg(
                Total_Demand_Qty=("Demand_Qty","sum"))
            dm_agg = dm_agg.merge(
                inv[["Material_Code","Available_Qty"]].groupby("Material_Code",as_index=False).first(),
                how="cross")  # we need per-code shortfall

            # Simpler: shortfall per (tag,code) = demand − min(demand, available)
            # Use cascade_allocate with all tags in file order for true shortfall
            _all_tags_ov = eq_master["Equipment_Tag_No."].tolist()
            _alloc_ov    = cascade_allocate(_all_tags_ov)

            sc_shortfall = _alloc_ov.groupby(
                ["Equipment_Tag_No.","Lining_System_Code"], as_index=False
            ).agg(
                Shortfall_Qty=("Shortfall_Qty","sum"),
                Demand_Qty   =("Demand_Qty","sum"),
                Allocated_Qty=("Allocated_Qty","sum"),
            )

            # Master table
            master = sqm_ref_ov.rename(columns={
                "Total_SQM_Original":"Total_SQM",
                "done_sqm":          "Done_SQM",
                "Total_SQM":         "Remaining_SQM",
            })
            # Merge equipment master (with all columns from Data Input sheet)
            master = master.merge(
                eq_master[["Equipment_Tag_No.","Name","Substrate","Location","Type",
                           "Lining_Systems","Lining_Type","Material_Spec","Design"]],
                on="Equipment_Tag_No.", how="left")

            # Merge equipment_sc for Lining_Area (surface area per row, not summed)
            lining_area_ref = equip_sc[
                ["Equipment_Tag_No.","Lining_System_Code","Total_SQM_Original"]
            ].drop_duplicates().rename(columns={"Total_SQM_Original":"Lining_Area_SQM"})
            lining_area_ref["Lining_Area_SQM"] = lining_area_ref["Lining_Area_SQM"].round(3)
            master = master.merge(lining_area_ref,
                                  on=["Equipment_Tag_No.","Lining_System_Code"], how="left")

            master = master.merge(sc_shortfall[["Equipment_Tag_No.","Lining_System_Code",
                                                 "Shortfall_Qty","Demand_Qty","Allocated_Qty"]],
                                  on=["Equipment_Tag_No.","Lining_System_Code"], how="left")
            master["Shortfall_Qty"] = master["Shortfall_Qty"].fillna(0)
            master["Fulfillment_%"] = (master["Allocated_Qty"] /
                master["Demand_Qty"].replace(0,np.nan) * 100).fillna(100).clip(0,100).round(1)

            # Add serial number
            master = master.reset_index(drop=True)
            master.insert(0, "S.No", master.index + 1)

            display_master = master[[
                "S.No","Equipment_Tag_No.","Name","Substrate","Type","Location",
                "Lining_Systems","Lining_System_Code","Lining_System_Short_Name",
                "Lining_Type","Material_Spec","Design",
                "Total_SQM","Lining_Area_SQM","Done_SQM","Remaining_SQM",
                "Demand_Qty","Allocated_Qty","Shortfall_Qty","Fulfillment_%"
            ]].copy()
            display_master.columns = [
                "S.No","Equipment No","Name","Substrate","Type","Location",
                "Lining System+","System Code","System Name",
                "Lining Type","Material Spec.","Design",
                "Total SQM","Lining Area SQM","Already Done SQM","Remaining SQM",
                "Total Demand","Allocated","Shortfall Qty","Fulfil %"
            ]

            # ── Filter controls ───────────────────────────────────────────────────────
            st.markdown('<div class="sec-hdr">🎛 Filters</div>', unsafe_allow_html=True)
            ff1,ff2,ff3,ff4 = st.columns(4)
            with ff1:
                f_loc_ov = st.multiselect(" Location",
                    options=LOCATION_ORDER, default=LOCATION_ORDER, key="ov_loc")
            with ff2:
                # Type options scoped to selected locations, unioned with registered
                # Types table (F#6).
                _ov_type_pool = display_master[display_master["Location"].isin(f_loc_ov)] \
                                if f_loc_ov else display_master
                type_opts_ov = _get_all_types(_ov_type_pool)
                f_type_ov = st.multiselect(" Type",
                    options=type_opts_ov, default=type_opts_ov, key="ov_type")
            with ff3:
                # System Code options scoped to selected locations + types
                _ov_code_pool = display_master[
                    display_master["Location"].isin(f_loc_ov) &
                    display_master["Type"].isin(f_type_ov)
                ] if (f_loc_ov or f_type_ov) else display_master
                codes_ov = sorted(_ov_code_pool["System Code"].unique().tolist(), key=int)
                f_code_ov = st.multiselect(" System Code",
                    options=codes_ov, default=codes_ov, key="ov_code")
            with ff4:
                status_opts = ["All","Fully Ready (100%)","Partial (50-99%)","Blocked (<50%)"]
                f_status_ov = st.selectbox(" Status", options=status_opts, key="ov_status")

            # Apply filters
            filtered_master = display_master[
                display_master["Location"].isin(f_loc_ov) &
                display_master["Type"].isin(f_type_ov) &
                display_master["System Code"].isin(f_code_ov)
            ].copy()
            if f_status_ov == "Fully Ready (100%)":
                filtered_master = filtered_master[filtered_master["Fulfil %"] >= 100]
            elif f_status_ov == "Partial (50-99%)":
                filtered_master = filtered_master[(filtered_master["Fulfil %"] >= 50) &
                                                  (filtered_master["Fulfil %"] < 100)]
            elif f_status_ov == "Blocked (<50%)":
                filtered_master = filtered_master[filtered_master["Fulfil %"] < 50]

            # Renumber after filter
            filtered_master = filtered_master.reset_index(drop=True)
            filtered_master["S.No"] = filtered_master.index + 1

            # ── Dynamic summary KPIs ─────────────────────────────────────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            ov1,ov2,ov3,ov4,ov5,ov6 = st.columns(6)
            _ov_sqm_deficit = (
                filtered_master["Remaining SQM"] *
                (1 - filtered_master["Fulfil %"].clip(0, 100) / 100)
            ).sum()
            _t5_base_cols = ["Equipment","Sys_Code","Total SQM","Already Done SQM","Remaining SQM","Fulfil %"]
            _t5_base = filtered_master[[c for c in _t5_base_cols if c in filtered_master.columns]].copy()
            _t5_short_dd = _t5_base[filtered_master["Fulfil %"]<100].copy()
            _t5_short_dd["Shortfall SQM"] = (_t5_short_dd["Remaining SQM"] * (1 - _t5_short_dd["Fulfil %"].clip(0,100)/100)).round(2)
            _t5_short_dd = _t5_short_dd.sort_values("Shortfall SQM", ascending=False).reset_index(drop=True)
            with ov1:
                dbl_click_metric("No. of Items (filtered)", str(len(filtered_master)), "t5_rows",
                    "All Filtered by No. of Items", _t5_base.reset_index(drop=True),
                    help_text="Number of (Equipment, System Code) pairs in current filter.")
            with ov2:
                dbl_click_metric("Total SQM", f'{filtered_master["Total SQM"].sum():,.1f}', "t5_sqm",
                    "Total SQM by No. of Items (sorted desc)",
                    _t5_base.sort_values("Total SQM", ascending=False).reset_index(drop=True),
                    help_text="Sum of original SQM for filtered rows.")
            with ov3:
                dbl_click_metric("Already Done SQM", f'{filtered_master["Already Done SQM"].sum():,.1f}', "t5_done",
                    "Completed SQM by No. of Items (sorted desc)",
                    _t5_base.sort_values("Already Done SQM", ascending=False).reset_index(drop=True),
                    help_text="SQM already completed (from daily consumption entries).")
            with ov4:
                dbl_click_metric("Remaining SQM", f'{filtered_master["Remaining SQM"].sum():,.1f}', "t5_rem",
                    "Remaining SQM by No. of Items (sorted desc)",
                    _t5_base.sort_values("Remaining SQM", ascending=False).reset_index(drop=True),
                    help_text="SQM still to be completed = Total − Done.")
            with ov5:
                dbl_click_metric("Shortfall SQM", f"{_ov_sqm_deficit:,.1f}", "t5_short",
                    "by No. of Items with SQM Shortfall (sorted desc)", _t5_short_dd,
                    help_text="SQM that cannot be completed across filtered rows, weighted by material fulfillment %.")
            with ov6:
                dbl_click_metric("Avg Coverage",
                    f'{filtered_master["Fulfil %"].mean():.1f}%' if len(filtered_master) else "0%",
                    "t5_avg_cov",
                    "Coverage by by No. of Items (sorted asc)",
                    _t5_base.sort_values("Fulfil %").reset_index(drop=True),
                    help_text="Average fulfillment % across filtered (Equipment, System Code) pairs.")

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Colour-coded master table ─────────────────────────────────────────────
            def _style_master(row):
                pct = row["Fulfil %"]
                if pct >= 100:  bg,tc = "rgba(16,185,129,.1)","#10B981"
                elif pct >= 90: bg,tc = "rgba(249,115,22,.1)","#F97316"
                elif pct >= 80: bg,tc = "rgba(234,179,8,.1)", "#EAB308"
                else:           bg,tc = "rgba(239,68,68,.1)", "#EF4444"
                styles = [f"background-color:{bg}"] * len(row)
                ci = list(row.index).index("Fulfil %")
                styles[ci] = f"background-color:{bg};color:{tc};font-weight:700"
                return styles

            styled_master = (filtered_master.style
                .apply(_style_master, axis=1)
                .format({
                    "Total SQM":        "{:,.2f}",
                    "Lining Area SQM":  "{:,.3f}",
                    "Already Done SQM": "{:,.2f}",
                    "Remaining SQM":    "{:,.2f}",
                    "Total Demand":     "{:,.3f}",
                    "Allocated":        "{:,.3f}",
                    "Shortfall Qty":    "{:,.3f}",
                    "Fulfil %":         "{:.1f}%",
                }))
            st.dataframe(styled_master, use_container_width=True, hide_index=True,
                         height=min(700, 50 + len(filtered_master)*35),
                         key="ov_master_tbl")

            # ── Per-System-Code material detail (expandable) ─────────────────────────
            st.markdown("<br>", unsafe_allow_html=True)

            # Sub-metrics for the whole filtered selection
            _ov_total_sqm   = filtered_master["Total SQM"].sum()
            _ov_done_sqm    = filtered_master["Already Done SQM"].sum()
            _ov_pending_sqm = filtered_master["Remaining SQM"].sum()
            _ov_comp_pct    = (_ov_done_sqm / _ov_total_sqm * 100) if _ov_total_sqm > 0 else 0.0
            _sm1, _sm2, _sm3, _sm4 = st.columns(4)
            _sm1.metric("Total SQM",        f"{_ov_total_sqm:,.2f}")
            _sm2.metric("Already Done SQM", f"{_ov_done_sqm:,.2f}")
            _sm3.metric("Pending SQM",      f"{_ov_pending_sqm:,.2f}")
            _sm4.metric("Completion",       f"{_ov_comp_pct:.1f}%")

            st.markdown('<div class="sec-hdr">🔬 Material Detail by System Code</div>',
                        unsafe_allow_html=True)

            for code in sorted(filtered_master["System Code"].unique().tolist(), key=int):
                sc_dm = dm[dm["Lining_System_Code"]==code]
                if sc_dm.empty: continue
                sname   = filtered_master[filtered_master["System Code"]==code]["System Name"].iloc[0]
                sc_sqm  = filtered_master[filtered_master["System Code"]==code]["Total SQM"].sum()
                done_sq = filtered_master[filtered_master["System Code"]==code]["Already Done SQM"].sum()
                sc_mat  = sc_dm.groupby(["Material_Code","Material_Name","UOM"],
                                         as_index=False)["Demand_Qty"].sum()
                sc_mat  = sc_mat.merge(inv[["Material_Code","Available_Qty"]],
                                       on="Material_Code", how="left")
                sc_mat["Available_Qty"] = sc_mat["Available_Qty"].fillna(0)
                sc_mat["Shortfall"]     = (sc_mat["Demand_Qty"]-sc_mat["Available_Qty"]).clip(lower=0).round(3)
                sc_mat["Coverage_%"]    = (
                    sc_mat["Available_Qty"].clip(upper=sc_mat["Demand_Qty"])
                    / sc_mat["Demand_Qty"].replace(0,np.nan)*100
                ).fillna(100).clip(0,100).round(1)
                # SQM achievable based on MINIMUM material coverage (bottleneck)
                sc_cov_min = sc_mat["Coverage_%"].min() if len(sc_mat) else 100
                sc_cov_avg = (sc_mat["Available_Qty"].clip(upper=sc_mat["Demand_Qty"]).sum() /
                              sc_mat["Demand_Qty"].sum()*100) if sc_mat["Demand_Qty"].sum()>0 else 100
                sc_can      = sc_sqm * min(1.0, sc_cov_avg/100)
                dot = "🟢" if sc_cov_avg>=100 else "🟠" if sc_cov_avg>=90 else "🟡" if sc_cov_avg>=80 else "🔴"

                with st.expander(
                    f"{dot}  Code {code}  ·  {sname}  ·  "
                    f"{sc_can:,.1f}/{sc_sqm:,.1f} SQM  ·  Done: {done_sq:,.1f}  ·  {sc_cov_avg:.1f}%",
                    expanded=False,
                ):
                    m1,m2,m3,m4,m5 = st.columns(5)
                    _t5sc_dd = sc_mat[["Material_Code","Material_Name","UOM","Available_Qty","Demand_Qty","Shortfall","Coverage_%"]].rename(columns={"Coverage_%":"Coverage %"}).reset_index(drop=True)
                    _t5sc_sk = f"t5sc_{code}"
                    with m1:
                        st.metric("System Code", f"Code {code}")
                    with m2:
                        dbl_click_metric("Short Name", str(sname), f"{_t5sc_sk}_n",
                            f"{sname} — Material Breakdown", _t5sc_dd)
                    with m3:
                        dbl_click_metric("Total SQM", f"{sc_sqm:,.2f}", f"{_t5sc_sk}_s",
                            f"Code {code} — Material Breakdown", _t5sc_dd)
                    with m4:
                        dbl_click_metric("Already Done SQM", f"{done_sq:,.2f}", f"{_t5sc_sk}_d",
                            f"Code {code} — Material Breakdown", _t5sc_dd,
                            help_text="SQM completed via Daily Consumption entries.")
                    with m5:
                        dbl_click_metric("Coverage SQM", f"{sc_can:,.2f}  ({sc_cov_avg:.1f}%)", f"{_t5sc_sk}_p",
                            f"Code {code} — Material Breakdown", _t5sc_dd,
                            help_text="SQM coverable with current available material balance.")

                    mat_show = sc_mat[["Material_Code","Material_Name","UOM",
                                        "Available_Qty","Demand_Qty","Shortfall",
                                        "Coverage_%"]].copy()
                    mat_show.columns = ["Code","Material Name","UOM",
                                         "Available","Total Demand","Shortfall","Coverage %"]
                    def _style_ov_det(row):
                        pct=row["Coverage %"]
                        if pct>=100:  bg,tc="rgba(16,185,129,.1)","#10B981"
                        elif pct>=90: bg,tc="rgba(249,115,22,.1)","#F97316"
                        elif pct>=80: bg,tc="rgba(234,179,8,.1)", "#EAB308"
                        else:         bg,tc="rgba(239,68,68,.1)", "#EF4444"
                        styles=[f"background-color:{bg}"]*len(row)
                        ci=list(row.index).index("Coverage %")
                        styles[ci]=f"background-color:{bg};color:{tc};font-weight:700"
                        return styles
                    st.dataframe(
                        mat_show.style.apply(_style_ov_det,axis=1).format({
                            "Available":"{:,.3f}","Total Demand":"{:,.3f}",
                            "Shortfall":"{:,.3f}","Coverage %":"{:.1f}%"}),
                        use_container_width=True, hide_index=True,
                        height=65+len(mat_show)*35, key=f"ov_det_{code}")

            # ── Downloads ─────────────────────────────────────────────────────────────
            st.markdown("<br>", unsafe_allow_html=True)
            dl1, dl1b, dl2, dl2b = st.columns(4)
            with dl1:
                st.download_button(
                    "⬇ Excel — Master Table",
                    data=generate_excel_report(filtered_master, f"Total Overview — {date.today()}", color_scheme="overview"),
                    file_name=f"total_overview_{date.today()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True)
            with dl1b:
                _pdf_download_button(
                    "⬇ PDF — Master Table",
                    df=filtered_master,
                    title=f"Total Overview — {date.today()}",
                    file_stem=f"total_overview_{date.today()}",
                    key="pdf_total_ov",
                    use_container_width=True,
                )
            if db_available():
                conn = get_db()
                full_log = pd.read_sql("SELECT * FROM consumption_log ORDER BY submitted_at DESC", conn)
                conn.close()
                with dl2:
                    st.download_button(
                        "⬇ Excel — Full Consumption Log",
                        data=generate_excel_report(full_log, "Full Consumption Log", color_scheme="overview"),
                        file_name=f"consumption_log_full_{date.today()}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)
                with dl2b:
                    _pdf_download_button(
                        "⬇ PDF — Full Consumption Log",
                        df=full_log.reset_index(drop=True),
                        title="Full Consumption Log",
                        file_stem=f"consumption_log_full_{date.today()}",
                        key="pdf_full_cons_log",
                        use_container_width=True)


        # ─────────────────────────────────────────────────────────────────────────────
        # TAB: MASTER DATA — ADD EQUIPMENT
        # ─────────────────────────────────────────────────────────────────────────────


        # ─────────────────────────────────────────────────────────────────────────────
        # TAB: MASTER DATA — FULL TABLE MANAGEMENT
        # ─────────────────────────────────────────────────────────────────────────────
        with tab_master:
            st.markdown('<div class="sec-hdr">🗄️ Master Data — View, Add & Delete Records</div>',
                        unsafe_allow_html=True)

            if not db_available():
                st.error("Database not found. Run `python setup_db.py` first.")
                st.stop()

            # ── Radio selector ─────────────────────────────────────────────────────────
            md_table_sel = st.radio(
                "Select Table to Manage",
                options=[
                    "Equipment",
                    "LINING SYSTEM MATERIAL CONSM",
                    "Materials_DetailsAvailable_Qty",
                    "➕ Add Location",
                    "➕ Add Type",
                ],
                key="md_table_radio",
                horizontal=True,
            )
            # R20.5 — Materials_DetailsAvailable_Qty now reads from
            # sme_materials_view (SME-owned seed + ERP-derived live qtys),
            # NOT the ERP inventory table. Writes for this mode are routed
            # to sme_inventory_seed via D.insert/update/delete_sme_inventory_seed.
            TABLE_MAP = {
                "Equipment":                      "equipment",
                "LINING SYSTEM MATERIAL CONSM":   "recipe",
                "Materials_DetailsAvailable_Qty": "sme_materials_view",
            }
            st.markdown("<hr>", unsafe_allow_html=True)

            # ══════════════════════════════════════════════════════════════════════════
            # ADD LOCATION mode — dedicated branch (bails before the table-CRUD code).
            # ══════════════════════════════════════════════════════════════════════════
            if md_table_sel == "➕ Add Location":
                st.markdown('<div class="sec-hdr">➕ Add Location</div>',
                            unsafe_allow_html=True)
                st.caption(
                    "Locations registered here populate every Location dropdown across the "
                    "app (Dashboard filters, Inventory entry, Master Data → Equipment, etc.). "
                    "Equipment for new locations is added through the Equipment table above."
                )

                # Existing locations
                _conn_loc = get_db()
                _loc_df = pd.read_sql(
                    "SELECT name AS Name, badge_color AS Color, sort_order AS \"Sort Order\", "
                    "added_at AS \"Added At\" FROM locations "
                    "ORDER BY sort_order, name", _conn_loc)
                _conn_loc.close()

                st.markdown("**Existing Locations**")
                st.dataframe(_loc_df, use_container_width=True, hide_index=True,
                             height=min(280, 60 + len(_loc_df) * 35),
                             key="md_loc_existing_tbl")

                # Add form
                st.markdown("<br>", unsafe_allow_html=True)
                with st.form("md_add_loc_form", clear_on_submit=True):
                    st.markdown("**New Location**")
                    _lc1, _lc2, _lc3 = st.columns([2, 1, 1])
                    with _lc1:
                        _new_loc_name = st.text_input(
                            "Location Name *",
                            placeholder="e.g. TRAIN L",
                            key="md_new_loc_name",
                        )
                    with _lc2:
                        _new_loc_color = st.color_picker(
                            "Badge Colour", value="#64748B", key="md_new_loc_color",
                        )
                    with _lc3:
                        _new_loc_sort = st.number_input(
                            "Sort Order", min_value=1, max_value=999,
                            value=int(_loc_df["Sort Order"].max() + 1) if len(_loc_df) else 1,
                            step=1, key="md_new_loc_sort",
                        )
                    _submitted = st.form_submit_button(
                        "💾 Save Location", type="primary", use_container_width=False)

                if _submitted:
                    _nm = (_new_loc_name or "").strip()
                    if not _nm:
                        st.error("❌ Location name is required.")
                    elif _nm in LOCATION_ORDER:
                        st.error(f"❌ Location '{_nm}' already exists.")
                    else:
                        # R20 EDIT: locations now live in system_settings under
                        # category='sme_location' (R17 Correction #1). The
                        # badge_color + sort_order columns are display-only and
                        # not persisted; SME's loc_badge() function uses the
                        # location *name* to look up the badge color.
                        try:
                            _site_id_md = st.session_state.get("_login_site_id") or "HQ"
                            D.add_sme_setting("sme_location", _nm, _site_id_md)
                            _refresh_location_order()
                            st.cache_data.clear()
                            st.success(f"✅ Location '{_nm}' added. It now appears in every dropdown.")
                            st.rerun()
                        except Exception as _e:
                            st.error(f"❌ Database error: {_e}")

                # Delete a location (only non-default ones with no equipment).
                st.markdown("<br>", unsafe_allow_html=True)
                _custom_locs = [n for n in LOCATION_ORDER
                                if n not in {d[0] for d in _DEFAULT_LOCATIONS}]
                if _custom_locs:
                    st.markdown("**Remove a Custom Location**")
                    _del_col1, _del_col2 = st.columns([2, 1])
                    with _del_col1:
                        _del_loc = st.selectbox(
                            "Location to remove",
                            options=[""] + _custom_locs, key="md_del_loc",
                        )
                    with _del_col2:
                        if st.button("🗑️ Remove", key="md_del_loc_btn",
                                     disabled=not _del_loc):
                            # Block deletion if equipment uses this location
                            _conn_del = get_db()
                            _ref_n = _conn_del.execute(
                                "SELECT COUNT(*) FROM equipment WHERE location = ?",
                                (_del_loc,)
                            ).fetchone()[0]
                            if _ref_n > 0:
                                _conn_del.close()
                                st.error(
                                    f"❌ Cannot remove '{_del_loc}': "
                                    f"{_ref_n} equipment record(s) still reference it."
                                )
                            else:
                                # R20 EDIT: route through R17 helper.
                                _conn_del.close()
                                _site_id_md = st.session_state.get("_login_site_id") or "HQ"
                                D.delete_sme_setting("sme_location", _del_loc, _site_id_md)
                                _refresh_location_order()
                                st.cache_data.clear()
                                st.success(f"✅ Location '{_del_loc}' removed.")
                                st.rerun()
                else:
                    st.caption("No custom locations to remove. Defaults are protected.")
                st.stop()

            # ══════════════════════════════════════════════════════════════════════════
            # ADD TYPE mode — dedicated branch (mirrors Add Location) — F#6
            # ══════════════════════════════════════════════════════════════════════════
            if md_table_sel == "➕ Add Type":
                st.markdown('<div class="sec-hdr">➕ Add Type</div>',
                            unsafe_allow_html=True)
                st.caption(
                    "Types registered here populate every Type dropdown across the "
                    "app (Dashboard filters, Consumption entry, Equipment add form, "
                    "etc.). Adding a type makes it selectable even before any "
                    "equipment of that type exists."
                )

                _conn_t = get_db()
                _t_df = pd.read_sql(
                    "SELECT name AS Name, sort_order AS \"Sort Order\", "
                    "added_at AS \"Added At\" FROM types "
                    "ORDER BY sort_order, name", _conn_t)
                _conn_t.close()

                st.markdown("**Existing Types**")
                st.dataframe(_t_df, use_container_width=True, hide_index=True,
                             height=min(280, 60 + len(_t_df) * 35),
                             key="md_type_existing_tbl")

                st.markdown("<br>", unsafe_allow_html=True)
                with st.form("md_add_type_form", clear_on_submit=True):
                    st.markdown("**New Type**")
                    _tc1, _tc2 = st.columns([2, 1])
                    with _tc1:
                        _new_type_name = st.text_input(
                            "Type Name *",
                            placeholder="e.g. Drum",
                            key="md_new_type_name",
                        )
                    with _tc2:
                        _new_type_sort = st.number_input(
                            "Sort Order", min_value=1, max_value=999,
                            value=int(_t_df["Sort Order"].max() + 1) if len(_t_df) else 1,
                            step=1, key="md_new_type_sort",
                        )
                    _submitted_t = st.form_submit_button(
                        "💾 Save Type", type="primary", use_container_width=False)

                if _submitted_t:
                    _nm = (_new_type_name or "").strip()
                    if not _nm:
                        st.error("❌ Type name is required.")
                    elif _nm in TYPE_ORDER:
                        st.error(f"❌ Type '{_nm}' already exists.")
                    else:
                        # R20 EDIT: types now live in system_settings under
                        # category='sme_equipment_type' (R17 Correction #1).
                        try:
                            _site_id_md = st.session_state.get("_login_site_id") or "HQ"
                            D.add_sme_setting("sme_equipment_type", _nm, _site_id_md)
                            _refresh_type_order()
                            st.cache_data.clear()
                            st.success(f"✅ Type '{_nm}' added. It now appears in every dropdown.")
                            st.rerun()
                        except Exception as _e:
                            st.error(f"❌ Database error: {_e}")

                st.markdown("<br>", unsafe_allow_html=True)
                _custom_types = [n for n in TYPE_ORDER if n not in _DEFAULT_TYPE_NAMES]
                if _custom_types:
                    st.markdown("**Remove a Custom Type**")
                    _tdel_c1, _tdel_c2 = st.columns([2, 1])
                    with _tdel_c1:
                        _del_type = st.selectbox(
                            "Type to remove",
                            options=[""] + _custom_types, key="md_del_type",
                        )
                    with _tdel_c2:
                        if st.button("🗑️ Remove", key="md_del_type_btn",
                                     disabled=not _del_type):
                            _conn_td = get_db()
                            _ref_n = _conn_td.execute(
                                "SELECT COUNT(*) FROM equipment WHERE TRIM(type) = ?",
                                (_del_type,)
                            ).fetchone()[0]
                            if _ref_n > 0:
                                _conn_td.close()
                                st.error(
                                    f"❌ Cannot remove '{_del_type}': "
                                    f"{_ref_n} equipment record(s) still reference it."
                                )
                            else:
                                # R20 EDIT: route through R17 helper.
                                _conn_td.close()
                                _site_id_md = st.session_state.get("_login_site_id") or "HQ"
                                D.delete_sme_setting("sme_equipment_type", _del_type, _site_id_md)
                                _refresh_type_order()
                                st.cache_data.clear()
                                st.success(f"✅ Type '{_del_type}' removed.")
                                st.rerun()
                else:
                    st.caption("No custom types to remove. Defaults are protected.")
                st.stop()

            db_table = TABLE_MAP[md_table_sel]

            # ── Auto-fill helper for Equipment smart entry ─────────────────────────────
            def _get_autofill(code: str) -> dict:
                conn = get_db()
                rec = conn.execute(
                    "SELECT lining_system_short_name, lining_type FROM recipe "
                    "WHERE lining_system_code = ? LIMIT 1", (code,)
                ).fetchone()
                eq_row = conn.execute(
                    'SELECT "Lining_System", "Material Spec.", "Lining_Area/location" FROM equipment '
                    'WHERE lining_system_code = ? AND "Lining_System" IS NOT NULL LIMIT 1', (code,)
                ).fetchone()
                conn.close()
                return {
                    "lining_system_short_name": rec["lining_system_short_name"] if rec else "",
                    "lining_type":              rec["lining_type"]              if rec else "",
                    "Lining_System":            eq_row["Lining_System"]         if eq_row else "",
                    "Material Spec.":           eq_row["Material Spec."]        if eq_row else "",
                    "Lining_Area/location":     eq_row["Lining_Area/location"]  if eq_row else None,
                }

            # ══════════════════════════════════════════════════════════════════════════
            # ADD NEW ROW SECTION
            # ══════════════════════════════════════════════════════════════════════════
            st.markdown('<div class="sec-hdr">➕ Add New Row</div>', unsafe_allow_html=True)

            # ── EQUIPMENT: Smart Entry ─────────────────────────────────────────────────
            if md_table_sel == "Equipment":
                conn = get_db()
                recipe_codes_df = pd.read_sql(
                    "SELECT DISTINCT lining_system_code, lining_system_short_name, lining_type "
                    "FROM recipe ORDER BY CAST(lining_system_code AS INTEGER)", conn)
                conn.close()

                code_opts = [
                    f"Code {r.lining_system_code} — {r.lining_system_short_name}"
                    for _, r in recipe_codes_df.iterrows()
                ]
                sel_codes_display = st.multiselect(
                    "🔧 Select Lining System Code(s) *",
                    options=code_opts, key="seq_codes_pre",
                    help="One row will be inserted into the equipment table per selected code.",
                )

                if sel_codes_display:
                    conn = get_db()
                    eq_col_info = conn.execute("PRAGMA table_info(equipment)").fetchall()
                    conn.close()

                    SKIP_FOR_FORM = {
                        "id", "lining_system_code", "lining_system_short_name",
                        "lining_type",
                        "lining_system",         # actual DB col: "Lining_System"
                        "material spec.",        # actual DB col: "Material Spec."
                        "lining_area/location",  # actual DB col: "Lining_Area/location"
                        "equipment_tag", "surface_area_sqm", "location",
                        "sl. #", "sl.#", "sl. no.", "sl. no", "sl.no.",
                        # Duplicate original-header columns from dynamic_sync_table
                        # (kept in DB for autofill but excluded from the entry form).
                        "dia / l", "ht. /w", "equipment total sqm", "remaraks",
                        "project", "wbs #", "io#", "drawing #",
                    }
                    _skip_ff_lower = {s.lower() for s in SKIP_FOR_FORM}
                    shared_cols = [(n, t) for (_, n, t, *__) in eq_col_info
                                   if n.lower() not in _skip_ff_lower]

                    with st.form(key="smart_eq_form"):
                        st.markdown('<div class="sec-hdr">Equipment Identity</div>',
                                    unsafe_allow_html=True)
                        _seq_c1, _seq_c2 = st.columns(2)
                        with _seq_c1:
                            eq_tag_inp = st.text_input("🏷️ Equipment Tag No. *",
                                                       placeholder="e.g. V-1001", key="seq_tag")
                        with _seq_c2:
                            loc_inp = st.selectbox("📍 Location *", options=LOCATION_ORDER,
                                                   key="seq_loc")

                        if shared_cols:
                            st.markdown(
                                '<div class="sec-hdr" style="margin-top:.8rem;">'
                                'Equipment Details (shared across all selected codes)</div>',
                                unsafe_allow_html=True)
                            shared_inputs = {}
                            _type_options_form = _get_all_types(eq_master)
                            for _si in range(0, len(shared_cols), 3):
                                _row_cols = st.columns(3)
                                for _sj, (_sn, _st) in enumerate(shared_cols[_si:_si+3]):
                                    with _row_cols[_sj]:
                                        if any(kw in _sn.lower() for kw in ("sqm", "qty", "for_1")):
                                            shared_inputs[_sn] = st.number_input(
                                                _sn.replace("_", " ").title(),
                                                value=0.0, step=0.1, key=f"seq_sh_{_sn}")
                                        elif _sn.lower() == "type":
                                            # Drive from registered Types table (F#6)
                                            shared_inputs[_sn] = st.selectbox(
                                                _sn.replace("_", " ").title(),
                                                options=[""] + _type_options_form,
                                                key=f"seq_sh_{_sn}",
                                            )
                                        else:
                                            shared_inputs[_sn] = st.text_input(
                                                _sn.replace("_", " ").title(),
                                                key=f"seq_sh_{_sn}")
                        else:
                            shared_inputs = {}

                        st.markdown('<div class="sec-hdr" style="margin-top:.8rem;">Per Lining System Code</div>',
                                    unsafe_allow_html=True)
                        per_code_sqm = {}
                        for _cd in sel_codes_display:
                            _code = _cd.split(" — ")[0].replace("Code ", "").strip()
                            _af   = _get_autofill(_code)
                            st.markdown(
                                f'<div style="margin:.5rem 0 .3rem;">'
                                f'<span class="code-badge">Code {_code}</span>'
                                f'<span style="font-size:.8rem;color:var(--t2);margin-left:.6rem;">'
                                f'{_af["lining_system_short_name"]}</span></div>',
                                unsafe_allow_html=True)
                            _ca1, _ca2, _ca3 = st.columns(3)
                            with _ca1:
                                st.text_input("Lining System Short Name",
                                              value=_af["lining_system_short_name"],
                                              disabled=True, key=f"seq_sn_{_code}")
                            with _ca2:
                                st.text_input("Lining Type",
                                              value=_af["lining_type"],
                                              disabled=True, key=f"seq_lt_{_code}")
                            with _ca3:
                                st.text_input("Material Spec.",
                                              value=_af["Material Spec."] or "",
                                              disabled=True, key=f"seq_ms_{_code}")
                            _cb1, _cb2 = st.columns(2)
                            with _cb1:
                                st.text_input("Lining System+",
                                              value=_af["Lining_System"] or "",
                                              disabled=True, key=f"seq_ls_{_code}")
                            with _cb2:
                                per_code_sqm[_code] = st.number_input(
                                    f"Surface Area SQM * (Code {_code})",
                                    min_value=0.0, value=0.0, step=0.1, format="%.2f",
                                    key=f"seq_sqm_{_code}",
                                    help="Required — used for material demand calculation.")

                        st.markdown("<br>", unsafe_allow_html=True)
                        seq_submit = st.form_submit_button("💾 Save Equipment",
                                                           use_container_width=False)

                    if seq_submit:
                        _eq_tag_val = st.session_state.get("seq_tag", "").strip()
                        if not _eq_tag_val:
                            st.error("Equipment Tag No. is required.")
                        elif not sel_codes_display:
                            st.error("Select at least one Lining System Code.")
                        else:
                            _missing_shared = [
                                k.replace("_", " ").title()
                                for k, v in shared_inputs.items()
                                if isinstance(v, str) and v.strip() == ""
                            ]
                            _bad = [c for c in per_code_sqm if per_code_sqm[c] <= 0]
                            if _missing_shared:
                                st.error(f"Please fill in all mandatory fields: {', '.join(_missing_shared)}")
                            elif _bad:
                                st.error(f"Surface Area SQM must be > 0 for codes: {', '.join(_bad)}")
                            else:
                                # R20.5 — route writes through database.py
                                # helpers; sme_equipment is the real table
                                # (the `equipment` view is read-only).
                                try:
                                    _site_id_eq = (
                                        st.session_state.get("_login_site_id")
                                        or "HQ"
                                    )
                                    for _cd in sel_codes_display:
                                        _code = _cd.split(" — ")[0].replace("Code ", "").strip()
                                        _af   = _get_autofill(_code)
                                        _sqm  = per_code_sqm[_code]
                                        _fixed = {
                                            "location":                st.session_state.get("seq_loc", LOCATION_ORDER[0]),
                                            "lining_system_code":      _code,
                                            "lining_system_short_name": _af["lining_system_short_name"],
                                            "lining_type":             _af["lining_type"],
                                            "equipment_tag":           _eq_tag_val,
                                            "material_spec":           _af["Material Spec."] or None,
                                            "surface_area_sqm":        _sqm,
                                            "lining_system":           _af["Lining_System"] or None,
                                            "lining_area_location":    _af.get("Lining_Area/location"),
                                        }
                                        _all_vals = {**_fixed}
                                        for _k, _v in shared_inputs.items():
                                            _all_vals[_k] = _v if _v != "" else None
                                        D.insert_sme_equipment(
                                            _all_vals, site_id=_site_id_eq,
                                        )
                                        D.upsert_sme_sqm_progress(
                                            site_id=_site_id_eq,
                                            equipment_tag=_eq_tag_val,
                                            lining_system_code=_code,
                                            original_sqm=float(_sqm),
                                        )
                                    st.cache_data.clear()
                                    st.success(
                                        f"✅ Equipment **{_eq_tag_val}** saved for "
                                        f"{len(sel_codes_display)} system code(s).")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Database error: {e}")

                    # Clear Form button (outside form, still inside if sel_codes_display)
                    if st.button("🧹 Clear Equipment Form", key="seq_clr_btn"):
                        _clr_keys = (
                            ["seq_tag", "seq_loc", "seq_codes_pre"]
                            + [f"seq_sh_{n}" for n, _ in shared_cols]
                            + [k for k in list(st.session_state.keys())
                               if k.startswith(("seq_sn_", "seq_lt_", "seq_ms_", "seq_ls_", "seq_sqm_"))]
                        )
                        for _k in _clr_keys:
                            st.session_state.pop(_k, None)
                        st.rerun()

                else:
                    st.info("Select one or more Lining System Codes above to build the entry form.")

            # ── RECIPE / INVENTORY: Dynamic form from PRAGMA ───────────────────────────
            else:
                conn = get_db()
                col_info = conn.execute(f"PRAGMA table_info({db_table})").fetchall()
                conn.close()

                SKIP_DYN = {"id", "sl. #", "sl.#", "sl. no.", "sl. no", "sl.no."}
                # R20.5 — sme_materials_view exposes derived columns
                # (received_qty, consumed_qty, available_qty) that come from
                # ERP receipts/consumption joins. The form must not collect
                # these — the user fills the seed (initial_*) and the view
                # computes the rest at read time.
                if db_table == "sme_materials_view":
                    SKIP_DYN = SKIP_DYN | {
                        "received_qty", "consumed_qty", "available_qty",
                        "ordered_qty",  # derived from initial_ordered_qty
                    }
                _skip_dyn_lower = {s.lower() for s in SKIP_DYN}
                editable_cols = [(n, t) for (_, n, t, *__) in col_info
                                 if n.lower() not in _skip_dyn_lower]

                with st.form(key=f"dyn_add_{db_table}"):
                    dyn_inputs = {}
                    for _di in range(0, len(editable_cols), 3):
                        _drow = st.columns(3)
                        for _dj, (_dn, _dt) in enumerate(editable_cols[_di:_di+3]):
                            with _drow[_dj]:
                                if any(kw in _dn.lower() for kw in ("sqm", "qty", "for_1")):
                                    dyn_inputs[_dn] = st.number_input(
                                        _dn.replace("_", " ").title(),
                                        value=0.0, step=0.001, format="%.4f",
                                        key=f"dyn_{db_table}_{_dn}")
                                else:
                                    dyn_inputs[_dn] = st.text_input(
                                        _dn.replace("_", " ").title(),
                                        key=f"dyn_{db_table}_{_dn}")
                    st.markdown("<br>", unsafe_allow_html=True)
                    dyn_submit = st.form_submit_button(f"➕ Add Row to {md_table_sel}")

                if dyn_submit:
                    _missing_fields = []
                    for _fn, _fv in dyn_inputs.items():
                        if isinstance(_fv, str) and _fv.strip() == "":
                            _missing_fields.append(_fn.replace("_", " ").title())
                        elif isinstance(_fv, float) and _fv == 0.0:
                            if any(kw in _fn.lower() for kw in ("sqm", "qty")):
                                _missing_fields.append(_fn.replace("_", " ").title())
                    if _missing_fields:
                        st.error(f"Please fill in all mandatory fields: {', '.join(_missing_fields)}")
                    else:
                        # R20.5 — dispatch on db_table; all writes go through
                        # database.py helpers that route to the real sme_*
                        # tables (the views are read-only).
                        try:
                            _row = {c: (dyn_inputs[c] if dyn_inputs[c] != "" else None)
                                    for c in dyn_inputs.keys()}
                            if db_table == "recipe":
                                D.insert_sme_recipe(_row)
                            elif db_table == "sme_materials_view":
                                D.insert_sme_inventory_seed(_row)
                            else:
                                raise RuntimeError(
                                    f"Unsupported table for dynamic add: {db_table}"
                                )
                            st.cache_data.clear()
                            st.success("✅ Row added successfully.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Database error: {e}")

                # Clear Form button (outside form)
                if st.button("🧹 Clear Form", key=f"dyn_clr_{db_table}"):
                    for _k in list(st.session_state.keys()):
                        if _k.startswith(f"dyn_{db_table}_"):
                            del st.session_state[_k]
                    st.rerun()

            # ══════════════════════════════════════════════════════════════════════════
            # VIEW & DELETE SECTION
            # ══════════════════════════════════════════════════════════════════════════
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(f'<div class="sec-hdr">📋 View, Edit & Delete — {md_table_sel}</div>',
                        unsafe_allow_html=True)

            # R20.5.1 — `db_table` now resolves to a VIEW (equipment / recipe /
            # sme_materials_view), and VIEWs have no implicit `rowid`. The old
            # `ORDER BY rowid` raised "no such column: rowid", which the bare
            # except swallowed into an empty grid → "No records found" for all
            # three radios even though the data is present. Order by a real
            # column per table instead.
            _ORDER_COL = {
                "equipment":          "id",
                "recipe":             "id",
                "sme_materials_view": "material_code",
            }.get(db_table, "")
            conn = get_db()
            try:
                if _ORDER_COL:
                    view_df = pd.read_sql(
                        f'SELECT * FROM {db_table} ORDER BY "{_ORDER_COL}"', conn)
                else:
                    view_df = pd.read_sql(f"SELECT * FROM {db_table}", conn)
            except Exception:
                view_df = pd.DataFrame()
            conn.close()

            # R20.5 — sme_materials_view PK is material_code; the dispatch
            # at save / delete time routes to D.update_sme_inventory_seed /
            # D.delete_sme_inventory_seed which write to the underlying
            # sme_inventory_seed table.
            PK_MAP = {
                "equipment":          ("id",            int),
                "recipe":             ("id",            int),
                "sme_materials_view": ("material_code", str),
            }
            pk_col, pk_cast = PK_MAP.get(db_table, ("id", int))

            if view_df.empty:
                st.info("No records found in this table.")
            else:
                view_df_display = view_df.copy()
                _sl_db_cols = [c for c in view_df_display.columns
                               if c.lower().strip() in {"sl. #", "sl.#", "sl. no.", "sl.no.", "sl. no"}]
                view_df_display = view_df_display.drop(columns=_sl_db_cols, errors="ignore")

                # ── Drop duplicate original-header Excel columns added by
                # dynamic_sync_table that mirror the canonical snake_case columns
                # (e.g. "Ht. /W" alongside "ht_w"). Original-spelling columns are
                # still kept in the DB for the autofill helper, but they should not
                # appear twice in the Master Data grid. (Fix #9)
                _EQUIP_DUPLICATE_COLS = {
                    "Dia / L", "Ht. /W", "Equipment Total SQM", "Remaraks",
                    "Material Spec.", "Lining_Area/location", "Lining_System",
                    "Sl. #", "Project", "WBS #", "IO#", "Drawing #",
                }
                if db_table == "equipment":
                    _dup_drop = [c for c in view_df_display.columns
                                 if c in _EQUIP_DUPLICATE_COLS]
                    if _dup_drop:
                        view_df_display = view_df_display.drop(columns=_dup_drop,
                                                               errors="ignore")

                view_df_display.insert(0, "Sl. No.", range(1, len(view_df_display) + 1))

                # ── Search filter ─────────────────────────────────────────────────────
                _search_cols = [c for c in view_df_display.columns if c not in ("Sl. No.", "☐ Select")]
                _srch1, _srch2 = st.columns([2, 1])
                with _srch1:
                    _md_search = st.text_input(
                        "🔍 Search table...", key=f"md_search_{db_table}",
                        placeholder="Type to filter…")
                with _srch2:
                    _md_col = st.selectbox(
                        "in column", options=["All columns"] + _search_cols,
                        key=f"md_col_{db_table}", label_visibility="visible")
                if _md_search.strip():
                    if _md_col == "All columns":
                        _mask = view_df_display[_search_cols].apply(
                            lambda col: col.astype(str).str.contains(
                                _md_search.strip(), case=False, na=False)
                        ).any(axis=1)
                    else:
                        _mask = view_df_display[_md_col].astype(str).str.contains(
                            _md_search.strip(), case=False, na=False)
                    view_df_display = view_df_display[_mask]
                view_df_display = view_df_display.reset_index(drop=True)
                view_df_display["Sl. No."] = range(1, len(view_df_display) + 1)

                # Add checkbox column for bulk delete
                view_df_display.insert(0, "☐ Select", False)

                # Build column_config
                _col_cfg = {
                    "Sl. No.": st.column_config.NumberColumn("Sl. No.", disabled=True),
                    "☐ Select": st.column_config.CheckboxColumn(
                        "☐", help="Check rows to delete, then click 'Delete Selected'",
                        default=False),
                }
                if pk_col in view_df_display.columns:
                    _cfg_type = (st.column_config.NumberColumn if pk_cast == int
                                 else st.column_config.TextColumn)
                    _col_cfg[pk_col] = _cfg_type(pk_col, disabled=True)

                st.data_editor(
                    view_df_display,
                    key=f"md_editor_{db_table}",
                    num_rows="fixed",
                    hide_index=True,
                    use_container_width=True,
                    height=min(600, 50 + len(view_df_display) * 35),
                    column_config=_col_cfg,
                )
                st.caption(f"Total entries: {len(view_df_display)}")

                _btn1, _btn2, _btn3 = st.columns([2, 2, 3])

                with _btn1:
                    if st.button("💾 Save Cell Edits", type="primary", key=f"save_edits_{db_table}"):
                        _editor_state = st.session_state.get(f"md_editor_{db_table}", {})
                        _edited_rows  = _editor_state.get("edited_rows", {})
                        if not _edited_rows:
                            st.info("No changes detected in the grid.")
                        else:
                            # R20.5 — dispatch on db_table; writes go through
                            # database.py helpers that target the real sme_*
                            # tables (the views are read-only).
                            try:
                                _site_id_ed = (
                                    st.session_state.get("_login_site_id")
                                    or "HQ"
                                )
                                n_saved = 0
                                for _row_idx, _changes in _edited_rows.items():
                                    _safe = {k: v for k, v in _changes.items()
                                             if k not in ("Sl. No.", pk_col, "☐ Select")}
                                    if not _safe:
                                        continue
                                    _pk_val = view_df.iloc[int(_row_idx)][pk_col]
                                    if db_table == "equipment":
                                        D.update_sme_equipment(
                                            int(_pk_val), _safe,
                                            site_id=_site_id_ed,
                                        )
                                    elif db_table == "recipe":
                                        D.update_sme_recipe(int(_pk_val), _safe)
                                    elif db_table == "sme_materials_view":
                                        D.update_sme_inventory_seed(
                                            str(_pk_val), _safe,
                                        )
                                    else:
                                        raise RuntimeError(
                                            f"Unsupported table for edit: {db_table}"
                                        )
                                    n_saved += 1
                                st.cache_data.clear()
                                st.success(f"✅ {n_saved} row(s) updated successfully.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Database error: {e}")

                with _btn2:
                    if st.button("🗑️ Delete Selected Rows", type="secondary",
                                 key=f"del_checked_{db_table}"):
                        _editor_state = st.session_state.get(f"md_editor_{db_table}", {})
                        _edited_rows  = _editor_state.get("edited_rows", {})
                        _del_indices = [int(idx) for idx, changes in _edited_rows.items()
                                        if changes.get("☐ Select", False)]
                        if not _del_indices:
                            st.warning("No rows checked for deletion. Check the ☐ column first.")
                        else:
                            # R20.5 — dispatch on db_table; delete_sme_equipment
                            # internally cascades sme_sqm_progress.
                            try:
                                _site_id_dl = (
                                    st.session_state.get("_login_site_id")
                                    or "HQ"
                                )
                                n_deleted = 0
                                for _di in _del_indices:
                                    _del_id = view_df.iloc[_di][pk_col]
                                    if db_table == "equipment":
                                        D.delete_sme_equipment(
                                            int(_del_id), site_id=_site_id_dl,
                                        )
                                    elif db_table == "recipe":
                                        D.delete_sme_recipe(int(_del_id))
                                    elif db_table == "sme_materials_view":
                                        D.delete_sme_inventory_seed(str(_del_id))
                                    else:
                                        raise RuntimeError(
                                            f"Unsupported table for delete: {db_table}"
                                        )
                                    n_deleted += 1
                                st.cache_data.clear()
                                st.success(f"✅ {n_deleted} row(s) deleted from `{md_table_sel}`.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Database error: {e}")

                st.caption("⚠️ Deletion is permanent. Equipment rows also remove the matching sqm_progress record.")

                _md_c1, _md_c2, _ = st.columns([1, 1, 4])
                _md_export_df = view_df_display.drop(
                    columns=["Sl. No.", "☐ Select"], errors="ignore"
                ).reset_index(drop=True)
                with _md_c1:
                    st.download_button(
                        f"⬇ Excel — {md_table_sel}",
                        data=generate_excel_report(
                            _md_export_df,
                            f"{md_table_sel} — Smart Material Estimator",
                            color_scheme=_TABLE_COLOR_MAP.get(db_table, "dashboard")),
                        file_name=f"{db_table}_export_{date.today()}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_{db_table}",
                        use_container_width=True,
                    )
                with _md_c2:
                    _pdf_download_button(
                        f"⬇ PDF — {md_table_sel}",
                        df=_md_export_df,
                        title=f"{md_table_sel} — Smart Material Estimator",
                        file_stem=f"{db_table}_export_{date.today()}",
                        key=f"pdf_{db_table}",
                        use_container_width=True,
                    )

        # ── END ORIGINAL SME IMPERATIVE BODY ──────────────────────────────────
    finally:
        st.download_button = _orig_dl_button
