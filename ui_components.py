"""
ui_components.py — General Industries Lightning Hub
====================================================
Premium, reusable UI building blocks. Zero business logic here.
  - Branded header
  - AgGrid wrapper  (filtering / sorting / pagination / column drag)
  - Plotly charts   (donut stock status, bar top-consumed)
  - Barcode scanner (HTML5 camera, mobile-first)
  - KPI metric cards
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from config import (
    BRAND_BLUE, BRAND_GOLD, BRAND_BLUE_LIGHT, BRAND_GOLD_LIGHT,
    DARK_BG, DARK_SURFACE, DARK_SURFACE_2, DARK_BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    COLOR_OK, COLOR_LOW, COLOR_CRITICAL,
    CHART_COLORS, PLOTLY_TEMPLATE,
    STOCK_STATUS_OK, STOCK_STATUS_LOW, STOCK_STATUS_CRITICAL,
    APP_NAME, APP_SUBTITLE, APP_ICON, APP_VERSION,
    AGGRID_HEIGHT, AGGRID_PAGE_SIZE, AGGRID_THEME,
)

# ── AgGrid optional import (graceful fallback to st.dataframe) ──────────────
try:
    from st_aggrid import (
        AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode,
    )
    try:
        from st_aggrid import ColumnsAutoSizeMode
        _HAS_AUTO_SIZE = True
    except ImportError:
        _HAS_AUTO_SIZE = False
    AGGRID_AVAILABLE = True
except ImportError:
    AGGRID_AVAILABLE = False
    class JsCode:  # minimal stub so module-level constants don't NameError
        def __init__(self, code: str): self._code = code


# ---------------------------------------------------------------------------
# Pre-built AgGrid cell-style: coloured pill badges for a "Status" column.
# Import this constant in any page that needs status badges:
#   from ui_components import STATUS_BADGE_JS
# then: render_aggrid(df, column_styles={"Status": STATUS_BADGE_JS})
# ---------------------------------------------------------------------------
STATUS_BADGE_JS = JsCode("""
function(params) {
    const palette = {
        'OK':        {bg:'rgba(34,197,94,0.15)',  color:'#4ADE80', bd:'rgba(34,197,94,0.28)'},
        'Low':       {bg:'rgba(249,115,22,0.15)', color:'#FB923C', bd:'rgba(249,115,22,0.28)'},
        'Below Min': {bg:'rgba(245,158,11,0.15)', color:'#FCD34D', bd:'rgba(245,158,11,0.28)'},
        'Empty':     {bg:'rgba(239,68,68,0.15)',  color:'#F87171', bd:'rgba(239,68,68,0.28)'},
    };
    const p = palette[params.value];
    if (!p) return {};
    return {
        background:     p.bg,
        color:          p.color,
        border:         '1px solid ' + p.bd,
        borderRadius:   '4px',
        textAlign:      'center',
        fontWeight:     '600',
        fontSize:       '0.76rem',
        padding:        '2px 7px',
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
    };
}
""")

# ---------------------------------------------------------------------------
# Pre-built AgGrid cell-style: coloured pill badges for a loan "Status" column.
# Import: from ui_components import LOAN_STATUS_BADGE_JS
# ---------------------------------------------------------------------------
LOAN_STATUS_BADGE_JS = JsCode("""
function(params) {
    const palette = {
        'Overdue': {bg:'rgba(239,68,68,0.15)',  color:'#F87171', bd:'rgba(239,68,68,0.28)'},
        'On Loan': {bg:'rgba(34,197,94,0.15)',  color:'#4ADE80', bd:'rgba(34,197,94,0.28)'},
    };
    const p = palette[params.value];
    if (!p) return {};
    return {
        background:     p.bg,
        color:          p.color,
        border:         '1px solid ' + p.bd,
        borderRadius:   '4px',
        textAlign:      'center',
        fontWeight:     '600',
        fontSize:       '0.76rem',
        padding:        '2px 7px',
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
    };
}
""")


# ===========================================================================
# GLOBAL CSS INJECTION
# ===========================================================================
def inject_custom_css() -> None:
    """
    Injects global CSS overrides for the GI brand theme.
    v2.1 — Glassmorphism "Blue & Gold" overhaul:
      • Frosted-glass cards, expanders, forms, and metric cards
      • Global deep-navy background with radial gold/blue glow accents
      • @keyframes fadeInUp page-load animation
      • Sidebar radio buttons replaced with smooth gold pill navigation
    v2.2 — Light-mode overlay (Phase 4): when st.session_state["theme"]=='light'
      a second <style> block is appended whose rules outrank the dark defaults
      via specificity + !important. Dark CSS is never mutated; toggling the
      theme is cheap and reversible. Default theme is 'dark' (unchanged).
    Call once at the top of main.py, after st.set_page_config().
    """
    st.markdown(f"""
    <style>
    /* ── Google Fonts ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', sans-serif;
    }}

    /* ══════════════════════════════════════════════════════════════
       GLOBAL BACKGROUND — deep navy with radial glow accents
       ══════════════════════════════════════════════════════════════ */
    .stApp {{
        background:
            radial-gradient(ellipse at 2%   2%,   rgba(37,  99, 235, 0.16) 0%, transparent 44%),
            radial-gradient(ellipse at 98%  2%,   rgba(30,  64, 175, 0.13) 0%, transparent 40%),
            radial-gradient(ellipse at 98%  98%,  rgba(251,191,  36, 0.09) 0%, transparent 44%),
            linear-gradient(160deg, #020C1B 0%, #0A1929 60%, #020C1B 100%);
        background-attachment: fixed;
    }}

    /* ── Fade-in-up animation (cards, metrics, page sections) ── */
    @keyframes fadeInUp {{
        from {{ opacity: 0; transform: translateY(18px); }}
        to   {{ opacity: 1; transform: translateY(0);    }}
    }}

    /* ── Hide Streamlit default branding ── */
    #MainMenu, footer {{ visibility: hidden; }}
    header {{ background: transparent !important; }}

    /* ── Sidebar — frosted glass panel ── */
    section[data-testid="stSidebar"] {{
        background: rgba(5, 15, 35, 0.82) !important;
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(255, 215, 0, 0.10);
    }}

    /* ══════════════════════════════════════════════════════════════
       SIDEBAR — Pill Navigation  (replaces plain radio buttons)
       Requires Chrome 105+ / Firefox 121+ / Safari 15.4+ for :has()
       ══════════════════════════════════════════════════════════════ */

    /* Hide the native radio circles */
    [data-testid="stSidebar"] [role="radiogroup"] input[type="radio"] {{
        display: none !important;
    }}

    /* Base label — left-bar accent style */
    [data-testid="stSidebar"] [role="radiogroup"] label {{
        display: flex;
        align-items: center;
        width: 100%;
        padding: 0.48rem 1rem;
        margin: 0.12rem 0;
        border-radius: 0 8px 8px 0;
        border: none;
        border-left: 3px solid transparent;
        cursor: pointer;
        font-size: 0.88rem;
        font-weight: 500;
        color: {TEXT_SECONDARY};
        transition: background 0.18s ease, border-left-color 0.18s ease, color 0.18s ease;
        background: transparent;
    }}

    /* Hover state */
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {{
        background: rgba(212, 175, 55, 0.07);
        border-left-color: rgba(212, 175, 55, 0.45);
        color: {TEXT_PRIMARY};
    }}

    /* Selected / active state — gold left bar */
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
        background: rgba(212, 175, 55, 0.12);
        border-left-color: {BRAND_GOLD};
        color: {TEXT_PRIMARY};
        font-weight: 700;
        box-shadow: none;
    }}

    /* ══════════════════════════════════════════════════════════════
       GI CARD SYSTEM — Glassmorphism (Dark Mode default)
       ══════════════════════════════════════════════════════════════ */
    .gi-card {{
        background: rgba(10, 25, 47, 0.50) !important;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 215, 0, 0.10) !important;
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.40),
                    inset 0 1px 0 rgba(255, 255, 255, 0.04);
        transition: all 0.3s ease;
        animation: fadeInUp 0.45s ease both;
    }}
    .gi-card:hover {{
        border-color: rgba(251, 191, 36, 0.35) !important;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55),
                    0 0 24px rgba(251, 191, 36, 0.07);
        transform: translateY(-2px);
    }}

    /* ── Metric cards — glass ── */
    div[data-testid="stMetric"] {{
        background: rgba(10, 25, 47, 0.55) !important;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 215, 0, 0.12) !important;
        border-radius: 14px;
        padding: 1rem 1.25rem;
        transition: all 0.3s ease;
        animation: fadeInUp 0.40s ease both;
    }}
    div[data-testid="stMetric"]:hover {{
        border-color: rgba(251, 191, 36, 0.40) !important;
        box-shadow: 0 4px 20px rgba(251, 191, 36, 0.10);
        transform: translateY(-1px);
    }}
    div[data-testid="stMetricValue"] {{
        color: {BRAND_GOLD} !important;
        font-size: 2rem !important;
        font-weight: 700 !important;
    }}

    /* ── Buttons ── */
    div[data-testid="stButton"] > button[kind="primary"] {{
        background: linear-gradient(135deg, {BRAND_GOLD}, {BRAND_GOLD_LIGHT});
        color: {BRAND_BLUE} !important;
        font-weight: 700;
        border: none;
        border-radius: 8px;
        transition: all 0.25s ease;
    }}
    div[data-testid="stButton"] > button[kind="primary"]:hover {{
        transform: translateY(-2px);
        box-shadow: 0 6px 20px {BRAND_GOLD}55;
    }}

    /* ── Tab styling ── */
    button[data-baseweb="tab"] {{
        font-weight: 600;
        color: {TEXT_MUTED} !important;
        transition: all 0.2s ease;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: {BRAND_GOLD} !important;
        border-bottom-color: {BRAND_GOLD} !important;
    }}

    /* ── Expanders — glass ── */
    [data-testid="stExpander"] {{
        background: rgba(10, 25, 47, 0.42) !important;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 215, 0, 0.10) !important;
        border-radius: 14px !important;
        overflow: hidden;
        margin-bottom: 0.75rem;
        transition: all 0.3s ease;
    }}
    [data-testid="stExpander"]:hover {{
        border-color: rgba(251, 191, 36, 0.28) !important;
    }}
    details summary {{
        font-weight: 600;
        color: {TEXT_SECONDARY};
    }}

    /* ── Forms — glass ── */
    [data-testid="stForm"] {{
        background: rgba(10, 25, 47, 0.45) !important;
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 215, 0, 0.10) !important;
        border-radius: 14px;
        padding: 1rem 1.25rem 0.5rem 1.25rem;
        margin-bottom: 0.75rem;
        transition: all 0.3s ease;
    }}

    /* ── AgGrid text colour fix for dark theme ── */
    .ag-theme-streamlit .ag-header-cell-label {{
        font-weight: 600;
        color: {BRAND_GOLD} !important;
    }}

    /* ── KPI badge used in sidebar ── */
    .low-stock-badge {{
        background: {COLOR_CRITICAL}22;
        border: 1px solid {COLOR_CRITICAL};
        color: {COLOR_CRITICAL};
        border-radius: 20px;
        padding: 2px 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }}

    /* ── Collapsed sidebar toggle ── */
    [data-testid="collapsedControl"] {{
        background-color: {BRAND_GOLD} !important;
        color: {BRAND_BLUE} !important;
        border-radius: 0px 8px 8px 0px !important;
        padding: 5px 15px 5px 10px !important;
        border: 2px solid {BRAND_BLUE} !important;
        box-shadow: 4px 4px 10px rgba(0,0,0,0.3) !important;
        transition: all 0.3s ease !important;
        z-index: 999999 !important;
    }}
    [data-testid="collapsedControl"]:hover {{
        background-color: {BRAND_GOLD_LIGHT} !important;
        cursor: pointer;
    }}

    /* ══════════════════════════════════════════════════════════════
       LIGHT MODE OVERRIDES
       All glass elements swap to frosted-white with warm-gold accents
       ══════════════════════════════════════════════════════════════ */

    /* Light mode: page background */
    [data-theme="light"] .stApp {{
        background:
            radial-gradient(ellipse at 2%   2%,   rgba(37,  99, 235, 0.06) 0%, transparent 45%),
            radial-gradient(ellipse at 98%  98%,  rgba(251,191,  36, 0.05) 0%, transparent 45%),
            linear-gradient(160deg, #EEF2FF 0%, #F8FAFC 60%, #EEF2FF 100%);
        background-attachment: fixed;
    }}

    /* Light mode: sidebar */
    [data-theme="light"] section[data-testid="stSidebar"] {{
        background: rgba(248, 250, 252, 0.90) !important;
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border-right: 1px solid #E5E7EB !important;
    }}

    /* Light mode: sidebar pills */
    [data-theme="light"] [data-testid="stSidebar"] [role="radiogroup"] label {{
        color: #374151;
    }}
    [data-theme="light"] [data-testid="stSidebar"] [role="radiogroup"] label:hover {{
        background: rgba(180, 83, 9, 0.07);
        border-left-color: rgba(180, 83, 9, 0.40);
        color: #B45309;
    }}
    [data-theme="light"] [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
        background: rgba(180, 83, 9, 0.10);
        border-left-color: #B45309;
        color: #B45309;
        font-weight: 700;
    }}

    /* Light mode: .gi-card */
    [data-theme="light"] .gi-card {{
        background: rgba(255, 255, 255, 0.65) !important;
        border-color: rgba(180, 83, 9, 0.14) !important;
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.07),
                    inset 0 1px 0 rgba(255, 255, 255, 0.90);
    }}
    [data-theme="light"] .gi-card:hover {{
        border-color: rgba(180, 83, 9, 0.32) !important;
        box-shadow: 0 8px 28px rgba(0, 0, 0, 0.11);
    }}

    /* Light mode: expanders */
    [data-theme="light"] [data-testid="stExpander"] {{
        background: rgba(255, 255, 255, 0.65) !important;
        border-color: #E5E7EB !important;
        box-shadow: 0 1px 6px rgba(0,0,0,0.05) !important;
    }}

    /* Light mode: forms */
    [data-theme="light"] [data-testid="stForm"] {{
        background: rgba(255, 255, 255, 0.65) !important;
        border-color: #E5E7EB !important;
        box-shadow: 0 1px 6px rgba(0,0,0,0.04) !important;
    }}

    /* Light mode: metric cards */
    [data-theme="light"] div[data-testid="stMetric"] {{
        background: rgba(255, 255, 255, 0.70) !important;
        border: 1px solid #E5E7EB !important;
    }}
    [data-theme="light"] div[data-testid="stMetricValue"] {{
        color: #B45309 !important;
    }}

    /* Light mode: AgGrid header */
    [data-theme="light"] .ag-theme-streamlit .ag-header-cell-label {{
        color: {BRAND_BLUE} !important;
    }}

    /* Light mode: tab text */
    [data-theme="light"] button[data-baseweb="tab"] {{
        color: #374151 !important;
    }}
    [data-theme="light"] button[data-baseweb="tab"][aria-selected="true"] {{
        color: #B45309 !important;
        border-bottom-color: #B45309 !important;
    }}

    /* Light mode: primary buttons */
    [data-theme="light"] div[data-testid="stButton"] > button[kind="primary"] {{
        background: linear-gradient(135deg, {BRAND_GOLD}, {BRAND_GOLD_LIGHT});
        color: {BRAND_BLUE} !important;
    }}

    /* ══════════════════════════════════════════════════════════════
       v2.2 GOAL 1: AGGRID — Full Glass Grid Theme
       ag-grid ignores [data-theme] so dark + light are explicit.
       ══════════════════════════════════════════════════════════════ */

    /* Outer glass panel */
    .ag-theme-streamlit .ag-root-wrapper {{
        background: rgba(10, 25, 47, 0.42) !important;
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border: 1px solid rgba(255, 215, 0, 0.12) !important;
        border-radius: 12px !important;
        overflow: hidden;
    }}

    /* Header — deep navy glass */
    .ag-theme-streamlit .ag-header {{
        background: rgba(5, 15, 40, 0.88) !important;
        border-bottom: 1px solid rgba(255, 215, 0, 0.22) !important;
    }}
    .ag-theme-streamlit .ag-header-cell-label {{
        color: #FBBF24 !important;
        font-weight: 700 !important;
    }}
    .ag-theme-streamlit .ag-header-cell:hover {{
        background: rgba(251, 191, 36, 0.08) !important;
    }}

    /* Alternating row stripes */
    .ag-theme-streamlit .ag-row-even {{
        background: rgba(10, 22, 42, 0.35) !important;
    }}
    .ag-theme-streamlit .ag-row-odd {{
        background: rgba(15, 30, 58, 0.46) !important;
    }}

    /* Row hover — subtle gold tint */
    .ag-theme-streamlit .ag-row:hover,
    .ag-theme-streamlit .ag-row-hover {{
        background: rgba(251, 191, 36, 0.09) !important;
    }}

    /* Selected row — gold accent (Admin bulk-approve) */
    .ag-theme-streamlit .ag-row-selected {{
        background: rgba(251, 191, 36, 0.18) !important;
        border-left: 3px solid rgba(251, 191, 36, 0.85) !important;
    }}
    .ag-theme-streamlit .ag-row-selected:hover {{
        background: rgba(251, 191, 36, 0.24) !important;
    }}

    /* Cell borders — very subtle */
    .ag-theme-streamlit .ag-cell {{
        border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
        color: {TEXT_PRIMARY} !important;
    }}

    /* Floating filter row inputs */
    .ag-theme-streamlit .ag-floating-filter-input input,
    .ag-theme-streamlit .ag-text-field-input {{
        background: rgba(10, 25, 47, 0.65) !important;
        border: 1px solid rgba(255, 215, 0, 0.15) !important;
        color: {TEXT_PRIMARY} !important;
        border-radius: 6px;
        transition: border-color 0.2s ease;
    }}
    .ag-theme-streamlit .ag-floating-filter-input input:focus,
    .ag-theme-streamlit .ag-text-field-input:focus {{
        border-color: #FBBF24 !important;
        box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.20) !important;
        outline: none;
    }}

    /* Pagination bar */
    .ag-theme-streamlit .ag-paging-panel {{
        background: rgba(5, 15, 40, 0.78) !important;
        border-top: 1px solid rgba(255, 215, 0, 0.12) !important;
        color: {TEXT_SECONDARY} !important;
    }}
    .ag-theme-streamlit button.ag-paging-button {{
        color: {TEXT_SECONDARY} !important;
        transition: color 0.2s ease;
    }}
    .ag-theme-streamlit button.ag-paging-button:hover:not([disabled]) {{
        color: #FBBF24 !important;
    }}

    /* ── AgGrid Light Mode ── */
    [data-theme="light"] .ag-theme-streamlit .ag-root-wrapper {{
        background: rgba(255, 255, 255, 0.65) !important;
        border-color: #E5E7EB !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-header {{
        background: rgba(240, 244, 255, 0.92) !important;
        border-bottom-color: #D1D5DB !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-header-cell-label {{
        color: {BRAND_BLUE} !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-row-even {{
        background: rgba(248, 250, 252, 0.82) !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-row-odd {{
        background: rgba(238, 242, 255, 0.72) !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-row:hover,
    [data-theme="light"] .ag-theme-streamlit .ag-row-hover {{
        background: rgba(180, 83, 9, 0.07) !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-row-selected {{
        background: rgba(180, 83, 9, 0.14) !important;
        border-left-color: rgba(180, 83, 9, 0.75) !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-cell {{
        color: #1F2937 !important;
        border-right-color: rgba(0, 0, 0, 0.06) !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-paging-panel {{
        background: rgba(240, 244, 255, 0.88) !important;
        border-top-color: #E5E7EB !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-floating-filter-input input,
    [data-theme="light"] .ag-theme-streamlit .ag-text-field-input {{
        background: rgba(255, 255, 255, 0.85) !important;
        border-color: #D1D5DB !important;
        color: #1F2937 !important;
    }}
    [data-theme="light"] .ag-theme-streamlit .ag-floating-filter-input input:focus,
    [data-theme="light"] .ag-theme-streamlit .ag-text-field-input:focus {{
        border-color: #B45309 !important;
        box-shadow: 0 0 0 2px rgba(180, 83, 9, 0.18) !important;
    }}

    /* ══════════════════════════════════════════════════════════════
       v2.2 GOAL 2: FROSTED INPUT FIELDS + FOCUS GLOW
       ══════════════════════════════════════════════════════════════ */

    /* Text, number, textarea — frosted base */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stTextArea"] textarea {{
        background: rgba(255, 255, 255, 0.05) !important;
        border: 1px solid rgba(255, 255, 255, 0.14) !important;
        border-radius: 8px !important;
        color: {TEXT_PRIMARY} !important;
        transition: all 0.25s ease !important;
    }}

    /* Gold focus glow */
    [data-testid="stTextInput"]:focus-within input,
    [data-testid="stNumberInput"]:focus-within input,
    [data-testid="stTextArea"]:focus-within textarea {{
        border-color: {BRAND_GOLD} !important;
        box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.15),
                    0 0 14px rgba(251, 191, 36, 0.10) !important;
        background: rgba(255, 255, 255, 0.08) !important;
        outline: none !important;
    }}

    /* Selectbox frosted */
    [data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child {{
        background: rgba(255, 255, 255, 0.05) !important;
        border: 1px solid rgba(255, 255, 255, 0.14) !important;
        border-radius: 8px !important;
        transition: all 0.25s ease !important;
    }}
    [data-testid="stSelectbox"]:focus-within [data-baseweb="select"] > div:first-child {{
        border-color: {BRAND_GOLD} !important;
        box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.15) !important;
    }}

    /* ── Input Light Mode ── */
    [data-theme="light"] [data-testid="stTextInput"] input,
    [data-theme="light"] [data-testid="stNumberInput"] input,
    [data-theme="light"] [data-testid="stTextArea"] textarea {{
        background: rgba(255, 255, 255, 0.82) !important;
        border-color: #D1D5DB !important;
        color: #1F2937 !important;
    }}
    [data-theme="light"] [data-testid="stTextInput"]:focus-within input,
    [data-theme="light"] [data-testid="stNumberInput"]:focus-within input,
    [data-theme="light"] [data-testid="stTextArea"]:focus-within textarea {{
        border-color: #B45309 !important;
        box-shadow: 0 0 0 3px rgba(180, 83, 9, 0.15),
                    0 0 12px rgba(180, 83, 9, 0.08) !important;
    }}
    [data-theme="light"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child {{
        background: rgba(255, 255, 255, 0.82) !important;
        border-color: #D1D5DB !important;
    }}
    [data-theme="light"] [data-testid="stSelectbox"]:focus-within [data-baseweb="select"] > div:first-child {{
        border-color: #B45309 !important;
        box-shadow: 0 0 0 3px rgba(180, 83, 9, 0.15) !important;
    }}

    /* ══════════════════════════════════════════════════════════════
       v2.2 GOAL 3: ENHANCED TABS + HEADING TYPOGRAPHY
       ══════════════════════════════════════════════════════════════ */

    /* Upgraded inactive tab */
    button[data-baseweb="tab"] {{
        font-weight: 500 !important;
        color: rgba(148, 163, 184, 0.75) !important;
        transition: all 0.25s ease !important;
        border-radius: 6px 6px 0 0;
    }}
    button[data-baseweb="tab"]:hover {{
        color: rgba(251, 191, 36, 0.85) !important;
        background: rgba(251, 191, 36, 0.05) !important;
    }}

    /* Active tab — glowing gold underline */
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: #FBBF24 !important;
        font-weight: 700 !important;
        border-bottom: 2px solid #FBBF24 !important;
        filter: drop-shadow(0 0 8px rgba(251, 191, 36, 0.40));
    }}

    /* Heading depth-shadow (pop against glass) */
    h1, h2, h3 {{
        text-shadow: 0 2px 10px rgba(0, 0, 0, 0.45),
                     0 1px 4px  rgba(0, 0, 0, 0.50) !important;
    }}

    /* ── Tab + Heading Light Mode ── */
    [data-theme="light"] button[data-baseweb="tab"] {{
        color: #6B7280 !important;
    }}
    [data-theme="light"] button[data-baseweb="tab"]:hover {{
        color: rgba(180, 83, 9, 0.85) !important;
        background: rgba(180, 83, 9, 0.04) !important;
    }}
    [data-theme="light"] button[data-baseweb="tab"][aria-selected="true"] {{
        color: #B45309 !important;
        border-bottom-color: #B45309 !important;
        filter: drop-shadow(0 0 8px rgba(180, 83, 9, 0.30));
    }}
    [data-theme="light"] h1,
    [data-theme="light"] h2,
    [data-theme="light"] h3 {{
        text-shadow: 0 1px 6px rgba(0, 0, 0, 0.12) !important;
    }}

    /* ══════════════════════════════════════════════════════════════
       v2.2 GOAL 4: GLASSMORPHIC ALERT BOXES
       Universal blur base + type-specific tints.
       Dual-selector strategy covers both baseweb kind attr
       and Streamlit-specific data-testid variants.
       ══════════════════════════════════════════════════════════════ */

    /* Universal glass base applied to all alert wrappers */
    [data-testid="stAlert"],
    [data-testid="stAlert"] [data-baseweb="notification"] {{
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
        border-radius: 10px !important;
        overflow: hidden;
        border-width: 1px !important;
        border-style: solid !important;
        transition: all 0.25s ease;
    }}

    /* Success — glassmorphic green */
    [data-baseweb="notification"][kind="positive"],
    [data-testid="stSuccessMessage"] {{
        background: rgba(16, 185, 129, 0.12) !important;
        border-color: rgba(16, 185, 129, 0.30) !important;
    }}

    /* Error — glassmorphic red */
    [data-baseweb="notification"][kind="negative"],
    [data-testid="stErrorMessage"] {{
        background: rgba(239, 68, 68, 0.12) !important;
        border-color: rgba(239, 68, 68, 0.30) !important;
    }}

    /* Warning — glassmorphic amber */
    [data-baseweb="notification"][kind="warning"],
    [data-testid="stWarningMessage"] {{
        background: rgba(251, 191, 36, 0.10) !important;
        border-color: rgba(251, 191, 36, 0.28) !important;
    }}

    /* Info — glassmorphic blue */
    [data-baseweb="notification"][kind="info"],
    [data-testid="stInfoMessage"] {{
        background: rgba(59, 130, 246, 0.12) !important;
        border-color: rgba(59, 130, 246, 0.28) !important;
    }}

    /* ══════════════════════════════════════════════════════════════
       MOBILE COLLAPSE (Phase 4) — phones get stacked single-column
       layout for any st.columns() block AND st.tabs scrolls horizontally.
       Zero-touch: applies globally, no page-by-page edits required.
       ══════════════════════════════════════════════════════════════ */
    @media (max-width: 768px) {{
        /* Force st.columns / row layouts to stack vertically. */
        [data-testid="stHorizontalBlock"] {{
            flex-direction: column !important;
            gap: 0.5rem !important;
        }}
        [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"],
        [data-testid="stHorizontalBlock"] > div {{
            width: 100% !important;
            min-width: 0 !important;
            flex: 1 1 100% !important;
        }}
        /* Make tab strips horizontally scrollable so 8 HOD tabs don't crush. */
        [data-baseweb="tab-list"] {{
            overflow-x: auto !important;
            flex-wrap: nowrap !important;
        }}
        /* Tighten card padding on small screens. */
        .gi-card, div[data-testid="stMetric"] {{
            padding: 0.9rem 1rem !important;
        }}
        /* Brand header subtitle hides on tiny screens to save vertical room. */
        .gi-brand-sub {{ display: none !important; }}
    }}

    /* ══════════════════════════════════════════════════════════════
       BACKGROUND GRID OVERLAY — subtle gold grid lines on dark bg
       Matches the Claude Design mockup reference. pointer-events:none
       so it never intercepts clicks.
       ══════════════════════════════════════════════════════════════ */
    .stApp::before {{
        content: '';
        position: fixed;
        inset: 0;
        background-image:
            linear-gradient(rgba(212,175,55,0.030) 1px, transparent 1px),
            linear-gradient(90deg, rgba(212,175,55,0.030) 1px, transparent 1px);
        background-size: 42px 42px;
        pointer-events: none;
        z-index: 0;
    }}
    /* Light-mode: softer navy grid on cream */
    [data-theme="light"] .stApp::before {{
        background-image:
            linear-gradient(rgba(0,51,102,0.045) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0,51,102,0.045) 1px, transparent 1px);
    }}

    /* ══════════════════════════════════════════════════════════════
       FORM LABELS — uppercase small-caps with tracking
       Matches the Claude Design mockup (USERNAME / PASSWORD style).
       Applied globally so all form inputs share the same visual rhythm.
       ══════════════════════════════════════════════════════════════ */
    [data-testid="stTextInput"] > label,
    [data-testid="stNumberInput"] > label,
    [data-testid="stSelectbox"] > label,
    [data-testid="stTextArea"] > label,
    [data-testid="stDateInput"] > label,
    [data-testid="stFileUploader"] > label,
    [data-testid="stCheckbox"] > label,
    [data-testid="stRadio"] > label {{
        text-transform: uppercase !important;
        font-size: 0.72rem !important;
        letter-spacing: 0.09em !important;
        font-weight: 600 !important;
        color: {TEXT_MUTED} !important;
    }}
    /* Light-mode: slightly darker so labels stay readable on cream */
    [data-theme="light"] [data-testid="stTextInput"] > label,
    [data-theme="light"] [data-testid="stNumberInput"] > label,
    [data-theme="light"] [data-testid="stSelectbox"] > label,
    [data-theme="light"] [data-testid="stTextArea"] > label,
    [data-theme="light"] [data-testid="stDateInput"] > label,
    [data-theme="light"] [data-testid="stFileUploader"] > label,
    [data-theme="light"] [data-testid="stCheckbox"] > label,
    [data-theme="light"] [data-testid="stRadio"] > label {{
        color: #4B5563 !important;
    }}

    </style>
    """, unsafe_allow_html=True)

    # Phase 4 — light-mode overlay. No-op by default; emits an additional
    # stylesheet AFTER the dark CSS when theme=='light' so its rules win on
    # cascade. The dark CSS above is untouched.
    if st.session_state.get("theme", "dark") == "light":
        _inject_light_mode_overlay()


def _inject_light_mode_overlay() -> None:
    """
    Light-mode CSS overlay. Flips backgrounds + text only — gold/blue accents
    stay since they work on both palettes. Applied with !important so it wins
    over the dark default without us having to edit the dark stylesheet.
    """
    st.markdown("""
    <style>
    /* ── Warm cream background, no glow ── */
    .stApp {
        background:
            radial-gradient(ellipse at 2% 2%,   rgba(212,175, 55,0.10) 0%, transparent 44%),
            radial-gradient(ellipse at 98% 98%, rgba(  0, 51,102,0.06) 0%, transparent 44%),
            linear-gradient(160deg, #F7F3E9 0%, #FBF8F0 60%, #F2EDDF 100%) !important;
        background-attachment: fixed !important;
    }

    /* ── Sidebar: white frosted ── */
    section[data-testid="stSidebar"] {
        background: rgba(255, 252, 245, 0.92) !important;
        border-right: 1px solid rgba(0, 51, 102, 0.12) !important;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label {
        color: #2D3F50 !important;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
        background: rgba(180, 83, 9, 0.12) !important;
        border-left-color: #B45309 !important;
        color: #7C2D12 !important;
        font-weight: 700 !important;
    }

    /* ── Cards + metrics: white over cream ── */
    .gi-card, div[data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.78) !important;
        border: 1px solid rgba(0, 51, 102, 0.14) !important;
        box-shadow: 0 4px 18px rgba(0, 51, 102, 0.08) !important;
    }

    /* ── Body text: navy on cream ── */
    .stApp, .stApp p, .stApp label, .stApp span:not([style*="color"]),
    .stApp div:not([style*="color"]), .stApp li {
        color: #2D3F50 !important;
    }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
        color: #003366 !important;
    }

    /* ── Form inputs: white fill, navy border ── */
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    [data-baseweb="select"] div,
    .stDateInput input, .stNumberInput input, .stTextInput input {
        background: #FFFFFF !important;
        color: #003366 !important;
        border-color: rgba(0, 51, 102, 0.22) !important;
    }

    /* ── Tabs + expanders: warm tone ── */
    button[data-baseweb="tab"] { color: #5B6F82 !important; }
    button[data-baseweb="tab"][aria-selected="true"] { color: #003366 !important; }
    [data-testid="stExpander"] {
        background: rgba(255, 255, 255, 0.65) !important;
        border: 1px solid rgba(0, 51, 102, 0.12) !important;
    }

    /* ── Dataframe / AgGrid: keep crisp on cream ── */
    .stDataFrame, .stDataFrame > div { background: #FFFFFF !important; }

    /* ── Buttons keep gold gradient — already on-brand for both themes ── */
    </style>
    """, unsafe_allow_html=True)


def inject_keyboard_shortcuts() -> None:
    """
    Adds project-wide keyboard shortcuts via a tiny inline <script>.
    Zero JS dependencies; runs in the main page DOM. Shortcuts:

      `/`     → focus the first visible text/search input on the page
      `Esc`   → blur the current input (handy to "let go" of a field)
      `Enter` → when focused on a number/text input, click the nearest
                primary button (mirrors the natural "submit" expectation
                Streamlit doesn't give you for free).

    All handlers skip when the user is typing in a text field (except
    Esc/Enter which target those explicitly). Safe to call multiple times —
    a global flag prevents double-binding.
    """
    st.markdown("""
    <script>
    (function () {
      if (window.__giShortcutsInstalled) return;
      window.__giShortcutsInstalled = true;

      function isTypingTarget(el) {
        if (!el) return false;
        const tag = el.tagName;
        return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable;
      }

      function focusSearch() {
        // Prefer a search-style input (Streamlit selectbox text), then any text input.
        const sels = [
          'input[type="search"]',
          'input[placeholder*="Search" i]',
          'input[placeholder*="search" i]',
          '[data-baseweb="input"] input',
        ];
        for (const s of sels) {
          const el = document.querySelector(s);
          if (el) { el.focus(); el.select && el.select(); return true; }
        }
        return false;
      }

      function clickNearestPrimary(fromEl) {
        // Walk up to the nearest Streamlit block, then look for a primary button.
        let node = fromEl;
        for (let i = 0; node && i < 8; i++) {
          const btn = node.querySelector
            ? node.querySelector('button[kind="primary"]')
            : null;
          if (btn) { btn.click(); return true; }
          node = node.parentElement;
        }
        // Fallback — any primary button on the page.
        const btn = document.querySelector('button[kind="primary"]');
        if (btn) { btn.click(); return true; }
        return false;
      }

      document.addEventListener("keydown", function (e) {
        // `/` focuses search, but only when NOT already typing somewhere.
        if (e.key === "/" && !isTypingTarget(document.activeElement)) {
          if (focusSearch()) { e.preventDefault(); }
          return;
        }
        // Esc blurs the active input.
        if (e.key === "Escape" && isTypingTarget(document.activeElement)) {
          document.activeElement.blur();
          return;
        }
        // Enter on a single-line input submits the surrounding form's primary
        // button (Streamlit's default Enter behaviour is "just commit value").
        if (e.key === "Enter" && document.activeElement &&
            document.activeElement.tagName === "INPUT" &&
            document.activeElement.type !== "textarea") {
          if (clickNearestPrimary(document.activeElement)) {
            e.preventDefault();
          }
        }
      }, true);
    })();
    </script>
    """, unsafe_allow_html=True)


def render_theme_toggle(sidebar: bool = True) -> None:
    """
    Renders a small theme toggle. Call inside the sidebar block.
    Persists in st.session_state['theme']. Default 'dark' preserves the
    current look for every existing user; switching the toggle is the only
    way the light overlay activates.
    """
    container = st.sidebar if sidebar else st
    current = st.session_state.get("theme", "dark")
    new_val = container.toggle(
        "🌞 Light mode",
        value=(current == "light"),
        key="_theme_toggle",
        help="Switch between dark (default) and light cream theme.",
    )
    desired = "light" if new_val else "dark"
    if desired != current:
        st.session_state["theme"] = desired
        st.rerun()


# ===========================================================================
# BRANDED HEADER
# ===========================================================================
def render_brand_header(subtitle: str = None) -> None:
    """
    Compact corporate header: small-caps app name + live date on one line,
    page subtitle in gold below. Matches the Claude Design topline treatment.
    """
    import datetime
    sub = subtitle or APP_SUBTITLE
    today = datetime.date.today().strftime("%d %b %Y")
    st.markdown(f"""
    <div style="padding:0.55rem 0 0.45rem 0;border-bottom:1px solid {DARK_BORDER};margin-bottom:1.2rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="color:{TEXT_MUTED};font-size:0.68rem;font-weight:700;
                         text-transform:uppercase;letter-spacing:0.13em;">
                General Industries ERP
            </span>
            <span style="color:{TEXT_MUTED};font-size:0.68rem;letter-spacing:0.04em;">{today}</span>
        </div>
        <p style="color:{BRAND_GOLD};font-size:0.88rem;margin:0.18rem 0 0 0;
                  font-weight:500;letter-spacing:0.03em;">{sub}</p>
    </div>
    """, unsafe_allow_html=True)


def render_feedback_sidebar(user: dict, pages: list[str]) -> None:
    """
    Compact 'Report Bug · Request Feature' card for the sidebar. Two small
    buttons each open a Streamlit dialog that asks which page + a 200-char
    description, then writes to `bug_reports` via `submit_bug_report`.

    Pass the user dict (uses `username`) and the list of page labels visible
    to this user — the dropdown is built from that, plus an 'Other' fallback.
    Safe to call inside a `with st.sidebar:` block.
    """
    from database import submit_bug_report

    st.markdown(
        f"<div style='color:{TEXT_MUTED};font-size:0.68rem;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.13em;margin:0.35rem 0 0.35rem 0;'>"
        f"FEEDBACK</div>",
        unsafe_allow_html=True,
    )

    @st.dialog("💬 Send Feedback")
    def _feedback_dialog(report_type: str):
        icon = "🐛" if report_type == "bug" else "💡"
        label = "Report a Bug" if report_type == "bug" else "Request a Feature"
        st.markdown(f"### {icon} {label}")
        st.caption(
            "Tell us what's broken or what you'd like added — your admin gets "
            "this in the **Reports & Bugs** tab."
        )
        page_options = list(pages) + ["Other"]
        chosen_page = st.selectbox(
            "Which page does this relate to?",
            options=page_options,
            key=f"_fb_page_{report_type}",
        )
        description = st.text_area(
            "Describe (max 200 characters)",
            key=f"_fb_desc_{report_type}",
            max_chars=200,
            height=110,
            placeholder=(
                "What goes wrong / steps to reproduce…"
                if report_type == "bug"
                else "What would help you most…"
            ),
        )
        st.caption(
            f"{len(description) if description else 0} / 200 characters"
        )
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Cancel", width="stretch", key=f"_fb_cancel_{report_type}"):
                st.rerun()
        with c2:
            if st.button(
                f"Submit {icon}",
                type="primary",
                width="stretch",
                key=f"_fb_submit_{report_type}",
                disabled=not (description and description.strip()),
            ):
                ok, msg = submit_bug_report(
                    username=user.get("username", "anonymous"),
                    report_type=report_type,
                    page=chosen_page,
                    description=description,
                )
                if ok:
                    st.toast(msg, icon="✅")
                    st.rerun()
                else:
                    st.error(msg)

    bug_col, feat_col = st.columns(2)
    with bug_col:
        if st.button(
            "🐛 Bug",
            width="stretch",
            key="_sb_feedback_bug",
            help="Report something broken",
        ):
            _feedback_dialog("bug")
    with feat_col:
        if st.button(
            "💡 Idea",
            width="stretch",
            key="_sb_feedback_feature",
            help="Suggest a new feature",
        ):
            _feedback_dialog("feature")


def render_brand_header_admin(subtitle: str = "Administrator Portal",
                              status_ok: bool = True) -> None:
    """
    Admin variant — gold subtitle accent + a "system status" pulse chip on
    the right (green = operational, amber = warnings). Matches the Claude
    Design Admin Portal topline.
    """
    import datetime
    today = datetime.date.today().strftime("%d %b %Y")
    pulse_col = "#22C55E" if status_ok else "#F59E0B"
    status_lbl = "All systems operational" if status_ok else "Degraded — see Overview"
    st.markdown(f"""
    <div style="padding:0.55rem 0 0.45rem 0;border-bottom:1px solid {DARK_BORDER};margin-bottom:1.2rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <div>
                <div style="color:{TEXT_MUTED};font-size:0.68rem;font-weight:700;
                            text-transform:uppercase;letter-spacing:0.13em;">
                    General Industries ERP
                </div>
                <p style="color:{BRAND_GOLD}CC;font-size:0.82rem;margin:0.18rem 0 0 0;
                          font-weight:500;letter-spacing:0.03em;">{subtitle}</p>
            </div>
            <div style="display:flex;align-items:center;gap:10px;">
                <span class="gi-pulse" style="display:inline-block;width:8px;height:8px;
                      border-radius:50%;background:{pulse_col};
                      box-shadow:0 0 6px {pulse_col}88;
                      animation:gi-pulse-anim 2s ease-in-out infinite;"></span>
                <span style="color:{pulse_col};font-size:0.72rem;font-weight:600;">
                    {status_lbl}</span>
                <span style="color:{TEXT_MUTED};font-size:0.68rem;letter-spacing:0.04em;
                       padding-left:8px;border-left:1px solid {DARK_BORDER};">{today}</span>
            </div>
        </div>
    </div>
    <style>
        @keyframes gi-pulse-anim {{
            0%,100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: 0.5; transform: scale(0.92); }}
        }}
    </style>
    """, unsafe_allow_html=True)


def render_brand_header_hod(subtitle: str = "HOD Management Portal") -> None:
    """
    Same layout as `render_brand_header` but with the HOD purple accent
    instead of the default brand-gold subtitle colour. Visually signals
    that the user is inside a privileged management surface.
    """
    import datetime
    today = datetime.date.today().strftime("%d %b %Y")
    purple = "#A855F7"
    st.markdown(f"""
    <div style="padding:0.55rem 0 0.45rem 0;border-bottom:1px solid {DARK_BORDER};margin-bottom:1.2rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="color:{TEXT_MUTED};font-size:0.68rem;font-weight:700;
                         text-transform:uppercase;letter-spacing:0.13em;">
                General Industries ERP
            </span>
            <span style="color:{TEXT_MUTED};font-size:0.68rem;letter-spacing:0.04em;">{today}</span>
        </div>
        <p style="color:{purple}CC;font-size:0.82rem;margin:0.18rem 0 0 0;
                  font-weight:500;letter-spacing:0.03em;">{subtitle}</p>
    </div>
    """, unsafe_allow_html=True)


# ===========================================================================
# HOD STATUS PILL — inline HTML helper for custom row-by-row tables.
#
# Returns a span string suitable for f-string injection into HTML tables.
# Centralised so the EOD Commit, Pending Receipts, PR, and Shelf-Life tabs
# all show the SAME pill for the SAME status. Keep the keys in sync with
# the JSX `Badge` map in /Claude Design CNCEC Project/HOD Portal.html.
# ===========================================================================
_HOD_PILL_MAP = {
    "pending":     ("#7A8FA018", "#7A8FA044", "#7A8FA0",  "Pending"),
    "flagged":     ("#F59E0B18", "#F59E0B44", "#F59E0B",  "⚠️ Flagged"),
    "approved":    ("#22C55E18", "#22C55E44", "#22C55E",  "✅ Approved"),
    "rejected":    ("#EF444418", "#EF444444", "#EF4444",  "❌ Rejected"),
    "committed":   ("#D4AF3718", "#D4AF3744", "#D4AF37",  "📤 Committed"),
    "draft":       ("#7A8FA018", "#7A8FA044", "#7A8FA0",  "Draft"),
    "submitted":   ("#1A4D8018", "#1A4D8044", "#5DA4D4",  "Submitted"),
    "in_progress": ("#F59E0B18", "#F59E0B44", "#F59E0B",  "In Progress"),
    "received":    ("#22C55E18", "#22C55E44", "#22C55E",  "✅ Received"),
    "sent":        ("#22C55E18", "#22C55E44", "#22C55E",  "Sent"),
    "open":        ("#1A4D8018", "#1A4D8044", "#5DA4D4",  "Open"),
    "closed":      ("#22C55E18", "#22C55E44", "#22C55E",  "Closed"),
    "expired":     ("#EF444418", "#EF444444", "#EF4444",  "EXPIRED"),
    "critical":    ("#EF444418", "#EF444444", "#EF4444",  "Critical"),
    "warning":     ("#F59E0B18", "#F59E0B44", "#F59E0B",  "Warning"),
    "ok":          ("#22C55E18", "#22C55E44", "#22C55E",  "OK"),
}


def status_pill_html(status: str, label_override: str | None = None) -> str:
    """Return a span HTML string for a status pill. Unknown status → grey 'Pending'."""
    bg, bd, col, lbl = _HOD_PILL_MAP.get(str(status).lower(), _HOD_PILL_MAP["pending"])
    if label_override:
        lbl = label_override
    return (
        f'<span style="background:{bg};border:1px solid {bd};color:{col};'
        f'font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:4px;'
        f'white-space:nowrap;">{lbl}</span>'
    )


# ===========================================================================
# AGGRID WRAPPER
# ===========================================================================
def render_aggrid(
    df: pd.DataFrame,
    key: str = "aggrid",
    height: int = AGGRID_HEIGHT,
    fit_columns: bool = True,
    page_size: int = AGGRID_PAGE_SIZE,
    column_styles: dict | None = None,
) -> dict | None:
    """
    Renders a DataFrame using AgGrid with:
      - Column filtering (per-column filter boxes)
      - Column resizing + drag-to-reorder
      - Sortable columns
      - Pagination
      - Side-bar panel for column visibility toggle
    Falls back to st.dataframe if st-aggrid is not installed.

    column_styles: optional dict mapping column names to JsCode cellStyle
      functions, e.g. {"Status": STATUS_BADGE_JS} for coloured pill badges.

    Returns the AgGrid response dict (contains selected rows etc.)
    or None if using fallback.
    """
    if df is None or df.empty:
        render_empty_state(
            icon="📭",
            title="No data to display",
            hint="Add records via the Admin Portal or Daily Issue Log to populate this grid.",
        )
        return None

    if not AGGRID_AVAILABLE:
        st.caption("ℹ️ Install `streamlit-aggrid` for enhanced grid features.")
        st.dataframe(df, use_container_width=True, hide_index=True)
        return None

    gb = GridOptionsBuilder.from_dataframe(df)

    # Default column behaviour
    gb.configure_default_column(
        filter=True,
        resizable=True,
        sortable=True,
        editable=False,
        wrapHeaderText=True,
        autoHeaderHeight=True,
        floatingFilter=True,        # per-column filter row below header
        filterParams={"buttons": ["reset", "apply"]},
    )

    # Pagination
    gb.configure_pagination(
        paginationAutoPageSize=False,
        paginationPageSize=page_size,
    )

    # Side-bar: column visibility + filter panels
    gb.configure_side_bar(filters_panel=True, columns_panel=True)

    # Row virtualization for large grids (Phase 2.2).
    # rowBuffer controls how many off-screen rows are rendered ahead of scroll.
    gb.configure_grid_options(
        rowBuffer=20,
        suppressRowVirtualisation=False,
    )

    # Per-column JsCode cell styles (e.g. STATUS_BADGE_JS for pill badges)
    if column_styles:
        for _col, _style in column_styles.items():
            if _col in df.columns:
                gb.configure_column(_col, cellStyle=_style)

    grid_options = gb.build()

    kwargs = {
        "data": df,
        "gridOptions": grid_options,
        "height": height,
        "theme": AGGRID_THEME,
        "update_mode": GridUpdateMode.NO_UPDATE,
        "data_return_mode": DataReturnMode.FILTERED_AND_SORTED,
        "allow_unsafe_jscode": True,
        "key": key,
        "use_container_width": True,
    }

    if _HAS_AUTO_SIZE:
        kwargs["columns_auto_size_mode"] = (
            ColumnsAutoSizeMode.FIT_CONTENTS if fit_columns
            else ColumnsAutoSizeMode.NO_AUTOSIZE
        )

    return AgGrid(**kwargs)


# ===========================================================================
# PLOTLY CHARTS
# ===========================================================================
def render_stock_donut(live_df: pd.DataFrame) -> None:
    """
    Renders a Plotly donut chart categorising inventory items into:
      ✅ Adequate  |  ⚠️ Low Stock  |  🔴 Critical/Empty
    """
    if live_df.empty:
        st.info("No inventory data for chart.")
        return

    def _classify(row):
        stock = row.get("Current_Stock", 0)
        min_q = row.get("Minimum_Qty", 0)
        if stock <= 0:
            return STOCK_STATUS_CRITICAL
        elif stock < min_q:
            return STOCK_STATUS_LOW
        return STOCK_STATUS_OK

    df = live_df.copy()
    df["Status"] = df.apply(_classify, axis=1)
    counts = df["Status"].value_counts().reset_index()
    counts.columns = ["Status", "Count"]

    color_map = {
        STOCK_STATUS_OK:       COLOR_OK,
        STOCK_STATUS_LOW:      COLOR_LOW,
        STOCK_STATUS_CRITICAL: COLOR_CRITICAL,
    }

    fig = px.pie(
        counts,
        values="Count",
        names="Status",
        hole=0.55,
        color="Status",
        color_discrete_map=color_map,
        template=PLOTLY_TEMPLATE,
        title="Stock Health Overview",
    )
    fig.update_traces(
        textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>Items: %{value}<extra></extra>",
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT_PRIMARY, "family": "Inter"},
        legend={"orientation": "h", "y": -0.15},
        margin={"t": 50, "b": 20, "l": 0, "r": 0},
        title_font={"color": BRAND_GOLD, "size": 16},
        annotations=[{
            "text": f"<b>{len(df)}</b><br><span style='font-size:11px'>Items</span>",
            "x": 0.5, "y": 0.5, "font_size": 18,
            "showarrow": False, "font_color": TEXT_PRIMARY,
        }],
    )
    st.plotly_chart(fig, use_container_width=True)


def render_top_consumed_bar(live_df: pd.DataFrame, top_n: int = 10) -> None:
    """
    Renders a Plotly horizontal bar chart of the top-N most consumed items,
    derived from the Total_Consumed column of the live inventory DataFrame.
    """
    if live_df.empty or "Total_Consumed" not in live_df.columns:
        st.info("No consumption data for chart.")
        return

    df = live_df[live_df["Total_Consumed"] > 0].copy()
    if df.empty:
        st.info("No consumption recorded yet.")
        return

    desc_col = "Equipment_Description" if "Equipment_Description" in df.columns else "SAP_Code"
    df["Label"] = df[desc_col].astype(str).str[:35]  # truncate long names
    top = df.nlargest(top_n, "Total_Consumed")

    fig = px.bar(
        top,
        x="Total_Consumed",
        y="Label",
        orientation="h",
        color="Total_Consumed",
        color_continuous_scale=[[0, "#3B82F6"], [1, BRAND_GOLD]],
        template=PLOTLY_TEMPLATE,
        title=f"Top {top_n} Consumed Items",
        labels={"Total_Consumed": "Total Consumed", "Label": ""},
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT_PRIMARY, "family": "Inter"},
        margin={"t": 50, "b": 20, "l": 0, "r": 20},
        yaxis={"categoryorder": "total ascending"},
        coloraxis_showscale=False,
        title_font={"color": BRAND_GOLD, "size": 16},
    )
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>Consumed: %{x:,.1f}<extra></extra>",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_stock_vs_minimum_bar(live_df: pd.DataFrame, max_items: int = 20) -> None:
    """
    Horizontal progress-bar chart: current stock vs minimum threshold.
    Each bar is color-coded by status; a gold tick marks the minimum.
    Shows the most critical items first (lowest stock/min ratio).
    """
    if live_df.empty:
        return

    desc_col = "Equipment_Description" if "Equipment_Description" in live_df.columns else "SAP_Code"
    cols_needed = [c for c in [desc_col, "SAP_Code", "Current_Stock", "Minimum_Qty", "UOM"]
                   if c in live_df.columns]
    df = live_df[cols_needed].copy()
    df["Label"] = df[desc_col].astype(str).str[:32]
    df = df[df["Minimum_Qty"] > 0]  # only items with thresholds set

    if df.empty:
        st.info("No minimum quantity thresholds configured yet.")
        return

    # Sort by ratio (most critical first), cap at max_items
    df["_ratio"] = df["Current_Stock"] / df["Minimum_Qty"].clip(lower=0.01)
    df = df.nsmallest(max_items, "_ratio")

    def _bar_color(row) -> str:
        s, m = row["Current_Stock"], row["Minimum_Qty"]
        if s <= 0:        return COLOR_CRITICAL          # empty  → red
        if s < m:         return "#F59E0B"               # below  → amber
        if s < m * 1.25:  return COLOR_LOW               # close  → orange
        return COLOR_OK                                   # healthy → green

    df["_color"] = df.apply(_bar_color, axis=1)

    uom_vals = df["UOM"].fillna("").astype(str).tolist() if "UOM" in df.columns else [""] * len(df)
    x_max = max(df["Minimum_Qty"].max() * 1.6, df["Current_Stock"].max() * 1.05, 1.0)

    fig = go.Figure()

    # One bar trace per status color to get a meaningful legend
    color_labels = {
        COLOR_CRITICAL: "Empty",
        "#F59E0B":       "Below Min",
        COLOR_LOW:       "Low (< 125% min)",
        COLOR_OK:        "OK",
    }
    for color_val, group in df.groupby("_color", sort=False):
        idxs = group.index.tolist()
        fig.add_trace(go.Bar(
            x=group["Current_Stock"].clip(lower=0),
            y=group["Label"],
            orientation="h",
            marker_color=color_val,
            marker_opacity=0.82,
            name=color_labels.get(color_val, ""),
            customdata=list(zip(
                group["Minimum_Qty"],
                group["Current_Stock"],
                [uom_vals[df.index.get_loc(i)] for i in idxs],
            )),
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Stock: <b>%{x:,.1f}</b> %{customdata[2]}<br>"
                "Minimum: %{customdata[0]:,.1f} %{customdata[2]}<extra></extra>"
            ),
        ))

    # Gold tick markers for the minimum threshold
    fig.add_trace(go.Scatter(
        x=df["Minimum_Qty"],
        y=df["Label"],
        mode="markers",
        marker=dict(
            symbol="line-ns",
            size=20,
            color=BRAND_GOLD,
            line=dict(color=BRAND_GOLD, width=2.5),
        ),
        name="Minimum threshold",
        hovertemplate="Min qty: %{x:,.1f}<extra></extra>",
    ))

    fig.update_layout(
        barmode="overlay",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT_PRIMARY, "family": "Inter"},
        title={"text": "Stock vs Minimum Threshold — most critical first",
               "font": {"color": BRAND_GOLD, "size": 15}},
        xaxis={"range": [0, x_max], "gridcolor": "rgba(255,255,255,0.05)",
               "title": "Quantity"},
        yaxis={"categoryorder": "array",
               "categoryarray": df["Label"].tolist()[::-1]},
        legend={"orientation": "h", "y": -0.14, "x": 0.5, "xanchor": "center",
                "font": {"size": 11}, "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 50, "b": 80, "l": 0, "r": 20},
        height=max(320, len(df) * 30 + 120),
    )
    st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# KPI CARDS ROW
# ===========================================================================
def render_kpi_row(live_df: pd.DataFrame) -> None:
    """Renders a row of 4 KPI metric cards from the live inventory DataFrame."""
    total_items  = len(live_df) if not live_df.empty else 0
    total_recv   = live_df["Total_Received"].sum()  if not live_df.empty else 0
    total_cons   = live_df["Total_Consumed"].sum()  if not live_df.empty else 0
    low_count    = 0
    if not live_df.empty and "Minimum_Qty" in live_df.columns:
        low_count = int((live_df["Current_Stock"] < live_df["Minimum_Qty"]).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 Total SKUs",        f"{total_items:,}")
    c2.metric("📥 Total Received",    f"{total_recv:,.1f}")
    c3.metric("📤 Total Consumed",    f"{total_cons:,.1f}")
    c4.metric("⚠️ Low Stock Items",   f"{low_count}",
              delta=f"-{low_count} need attention" if low_count else "All OK",
              delta_color="inverse")


# ===========================================================================
# UI POLISH HELPERS (Phase 2.2)
# ===========================================================================
def render_empty_state(
    icon: str = "📭",
    title: str = "No data yet",
    hint: str = "",
) -> None:
    """
    Branded empty-state card for replacing inconsistent `st.info` / `st.warning`
    placeholders. Call when a DataFrame is empty or a section has no content.
    """
    hint_html = (
        f'<div style="color:{TEXT_MUTED};font-size:0.85rem;margin-top:6px;">{hint}</div>'
        if hint else ""
    )
    st.markdown(
        f'<div style="text-align:center;padding:1.6rem 1rem;'
        f'background:rgba(10,25,47,0.45);border:1px dashed rgba(255,215,0,0.15);'
        f'border-radius:14px;margin:0.5rem 0;">'
        f'<div style="font-size:2rem;line-height:1;margin-bottom:6px;">{icon}</div>'
        f'<div style="color:{TEXT_PRIMARY};font-weight:600;font-size:1rem;">{title}</div>'
        f'{hint_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


class skeleton_block:
    """
    Context manager that shows a lightweight loading skeleton, then clears
    itself when the `with` block exits.

    Usage:
        with skeleton_block(rows=4, label="Loading inventory…"):
            df = expensive_query()
        render_grid(df)

    The skeleton is rendered into a single `st.empty()` placeholder so it
    disappears the moment real content is drawn after the block.
    """

    def __init__(self, rows: int = 4, label: str = "Loading…"):
        self.rows = max(1, int(rows))
        self.label = label
        self._slot = None

    def __enter__(self):
        self._slot = st.empty()
        bars = "".join(
            f'<div style="height:14px;border-radius:6px;margin:8px 0;'
            f'background:linear-gradient(90deg,rgba(255,255,255,0.05),'
            f'rgba(255,255,255,0.12),rgba(255,255,255,0.05));"></div>'
            for _ in range(self.rows)
        )
        self._slot.markdown(
            f'<div style="padding:0.75rem 1rem;background:rgba(10,25,47,0.40);'
            f'border:1px solid rgba(255,215,0,0.10);border-radius:12px;'
            f'animation:fadeInUp 0.3s ease both;">'
            f'<div style="color:{TEXT_MUTED};font-size:0.8rem;'
            f'margin-bottom:6px;">⏳ {self.label}</div>'
            f'{bars}</div>',
            unsafe_allow_html=True,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._slot is not None:
            self._slot.empty()
        return False  # don't swallow exceptions


def responsive_columns(spec):
    """
    Mobile-aware wrapper around `st.columns`.

    `spec` is the same value you'd pass to `st.columns` — an int or a list
    of weights. On narrow viewports, this collapses to a single column.

    Mobile detection: set `st.session_state["is_mobile"] = True` from a
    layout hook (e.g. streamlit-js-eval) or query param. Defaults to False
    when the flag is unset, preserving today's desktop behaviour.
    """
    is_mobile = bool(st.session_state.get("is_mobile", False))
    if is_mobile:
        n = spec if isinstance(spec, int) else len(spec)
        # Return a list of single-column proxies so callers using `with col:`
        # still work — they just stack vertically.
        return [st.container() for _ in range(n)]
    return st.columns(spec)


# ===========================================================================
# SIDEBAR ERROR CHIP — reusable for surfaced failures
# ===========================================================================
def render_sidebar_error_chip(label: str, tooltip: str = "") -> None:
    """Compact amber chip for sidebar surfacing of recoverable failures."""
    st.markdown(
        f'<div title="{tooltip}" style="'
        'display:inline-block;padding:6px 10px;border-radius:999px;'
        'background:rgba(245,158,11,0.18);border:1px solid rgba(245,158,11,0.55);'
        'color:#F59E0B;font-size:0.82rem;font-weight:600;margin:4px 0;">'
        f'⚠️ {label}</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# SCAN-TO-INSPECT — per-item snapshot card (Phase 4 #1)
# ===========================================================================
def _sparkline(values: list[float], color: str, height_px: int = 70):
    """Small inline bar chart for the 30-day series. Returns a Plotly figure."""
    fig = go.Figure(go.Bar(
        y=values,
        marker=dict(color=color, line=dict(width=0)),
        hovertemplate="%{y}<extra></extra>",
    ))
    fig.update_layout(
        height=height_px,
        margin=dict(l=0, r=0, t=4, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
        showlegend=False,
        bargap=0.18,
    )
    return fig


def _daily_series(df: pd.DataFrame, lookback_days: int = 30) -> list[float]:
    """
    Returns a list of `lookback_days` daily totals, oldest → newest, padding
    missing days with 0. Robust to mixed Date string formats.
    """
    if df is None or df.empty or "Date" not in df.columns:
        return [0.0] * lookback_days
    dates = pd.to_datetime(df["Date"], errors="coerce")
    s = pd.DataFrame({"d": dates.dt.date, "q": pd.to_numeric(df["Quantity"], errors="coerce").fillna(0.0)})
    s = s.dropna(subset=["d"])
    if s.empty:
        return [0.0] * lookback_days
    grouped = s.groupby("d")["q"].sum()
    today = pd.Timestamp.today().date()
    days = [today - pd.Timedelta(days=i).to_pytimedelta() for i in range(lookback_days - 1, -1, -1)]
    return [float(grouped.get(d, 0.0)) for d in days]


def _mini_svg_sparkline(vals: list[float], color: str,
                        w: int = 70, h: int = 22) -> str:
    """
    70×22 inline SVG polyline from a daily-values list.
    Falls back to a flat-line when all values are zero.
    """
    if not vals or len(vals) < 2 or max(vals, default=0) == 0:
        mid = h // 2
        return (
            f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'style="overflow:visible;display:block;">'
            f'<line x1="0" y1="{mid}" x2="{w}" y2="{mid}" '
            f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.35"/></svg>'
        )
    mx  = max(vals)
    n   = len(vals)
    pad = 1.5  # top/bottom padding in px
    pts = " ".join(
        f"{i / (n - 1) * w:.1f},{h - pad - (v / mx) * (h - 2 * pad):.1f}"
        for i, v in enumerate(vals)
    )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'style="overflow:visible;display:block;">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def render_stock_badge(
    snap: dict,
    site_id: str = "",
    warning_text: str = "",
) -> None:
    """
    Highlighted, READ-ONLY current-stock chip rendered next to the Quantity
    input on the Entry Log forms. Visually distinct from the surrounding
    Streamlit inputs:
      - gold gradient + gold border
      - large coloured value (🟢/🟡/🔴 based on stock vs Minimum_Qty)
      - pointer-events:none + user-select:none → can't be focused or edited
      - optional red warning slot below the value (used by the consumption
        form when the typed qty would exceed available stock)

    `snap` is a dict from get_item_snapshot / cached_item_snapshot. Always
    pass the SITE-scoped snapshot here — physical stock at the user's site
    is what bounds a consumption.
    """
    if not snap or not snap.get("found"):
        st.markdown(
            f'<div style="padding:10px 14px;background:rgba(10,25,47,0.45);'
            f'border:1px dashed rgba(255,215,0,0.20);border-radius:10px;'
            f'margin:4px 0 6px;color:{TEXT_MUTED};font-size:0.82rem;'
            f'pointer-events:none;user-select:none;">'
            f'📦 Current stock unknown — pick a material first.'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    current = float(snap.get("current_stock") or 0.0)
    minimum = float(snap.get("minimum_qty") or 0.0)
    uom     = str(snap.get("uom") or "")

    if current <= 0:
        color = COLOR_CRITICAL
        icon  = "🔴"
    elif minimum > 0 and current < minimum:
        color = COLOR_LOW
        icon  = "🟡"
    else:
        color = COLOR_OK
        icon  = "🟢"

    site_html = (
        f' at <span style="color:{TEXT_PRIMARY};font-weight:600;">{site_id}</span>'
        if site_id else ""
    )
    warn_html = (
        f'<div style="color:{COLOR_CRITICAL};font-size:0.82rem;font-weight:600;'
        f'margin-top:6px;">⚠️ {warning_text}</div>'
        if warning_text else ""
    )
    min_html = (
        f' <span style="color:{TEXT_MUTED};font-size:0.75rem;font-weight:500;">'
        f'(min {minimum:g})</span>'
        if minimum > 0 else ""
    )

    st.markdown(
        f'<div style="background:linear-gradient(135deg, rgba(212,175,55,0.10),'
        f' rgba(10,25,47,0.55));'
        f'border:1px solid rgba(212,175,55,0.42);border-left:5px solid {color};'
        f'border-radius:10px;padding:10px 14px;margin:4px 0 6px;'
        f'pointer-events:none;user-select:none;cursor:not-allowed;">'
        f'<div style="color:{TEXT_MUTED};font-size:0.72rem;text-transform:uppercase;'
        f'letter-spacing:0.06em;">📦 Current stock{site_html}</div>'
        f'<div style="color:{color};font-size:1.5rem;font-weight:800;line-height:1.15;'
        f'margin-top:2px;">{icon} {current:g} '
        f'<span style="color:{TEXT_MUTED};font-size:0.85rem;font-weight:500;">{uom}</span>'
        f'{min_html}</div>'
        f'{warn_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_item_snapshot(snap: dict, lookback_days: int = 30) -> None:
    """
    Compact per-item card (Claude Design v2):
      - SAP code (gold monospace, 11 px) + description (white bold, truncated)
      - Three inline KPIs: SITE STOCK / 30-DAY BURN / DAILY RATE
      - Right column: "30-DAY TREND" label + 70×22 SVG polyline + status badge
      - Full 30-day history (consumption + receipts) in a collapsed expander

    Pass `snap` from `cached_item_snapshot(sap_code, site_id)`.
    Safe to call when snap["found"] is False — shows a small "not found" note.
    """
    if not snap or not snap.get("found"):
        st.caption(f"Item not found in inventory: `{snap.get('sap_code','')}`")
        return

    current    = float(snap.get("current_stock") or 0.0)
    minimum    = float(snap.get("minimum_qty") or 0.0)
    cons_total = float(snap.get("cons_total") or 0.0)
    daily_rate = cons_total / float(lookback_days) if lookback_days else 0.0
    uom        = str(snap.get("uom") or "")
    cons_df    = snap.get("cons_df")

    # Status colour + badge label
    if current <= 0:
        stock_col = COLOR_CRITICAL
        badge_lbl = "Empty"
        badge_bg  = "rgba(239,68,68,0.14)"
        badge_bd  = "rgba(239,68,68,0.38)"
    elif minimum > 0 and current < minimum:
        stock_col = "#F59E0B"
        badge_lbl = "Below Min"
        badge_bg  = "rgba(245,158,11,0.14)"
        badge_bd  = "rgba(245,158,11,0.38)"
    elif minimum > 0 and current < minimum * 1.25:
        stock_col = COLOR_LOW
        badge_lbl = "Low"
        badge_bg  = "rgba(249,115,22,0.14)"
        badge_bd  = "rgba(249,115,22,0.38)"
    else:
        stock_col = COLOR_OK
        badge_lbl = "OK"
        badge_bg  = "rgba(34,197,94,0.14)"
        badge_bd  = "rgba(34,197,94,0.38)"

    svg  = _mini_svg_sparkline(_daily_series(cons_df, lookback_days), stock_col)
    sap  = str(snap.get("sap_code", ""))
    desc = str(snap.get("description") or "")

    stats_html = (
        f'<div style="flex:1;min-width:55px;">'
        f'  <div style="color:#4A6080;font-size:9.5px;letter-spacing:0.05em;'
        f'       text-transform:uppercase;margin-bottom:2px;">SITE STOCK</div>'
        f'  <div style="color:{stock_col};font-size:1.1rem;font-weight:700;line-height:1;">'
        f'       {current:g}</div>'
        f'  <div style="color:#4A6080;font-size:9.5px;margin-top:1px;">{uom}</div>'
        f'</div>'
        f'<div style="width:1px;background:rgba(42,64,96,0.5);'
        f'     align-self:stretch;margin:0 6px;"></div>'
        f'<div style="flex:1;min-width:55px;">'
        f'  <div style="color:#4A6080;font-size:9.5px;letter-spacing:0.05em;'
        f'       text-transform:uppercase;margin-bottom:2px;">30-DAY BURN</div>'
        f'  <div style="color:{TEXT_PRIMARY};font-size:1.1rem;font-weight:700;line-height:1;">'
        f'       {cons_total:g}</div>'
        f'  <div style="color:#4A6080;font-size:9.5px;margin-top:1px;">{uom}</div>'
        f'</div>'
        f'<div style="width:1px;background:rgba(42,64,96,0.5);'
        f'     align-self:stretch;margin:0 6px;"></div>'
        f'<div style="flex:1;min-width:55px;">'
        f'  <div style="color:#4A6080;font-size:9.5px;letter-spacing:0.05em;'
        f'       text-transform:uppercase;margin-bottom:2px;">DAILY RATE</div>'
        f'  <div style="color:{TEXT_PRIMARY};font-size:1.1rem;font-weight:700;line-height:1;">'
        f'       {daily_rate:.1f}</div>'
        f'  <div style="color:#4A6080;font-size:9.5px;margin-top:1px;">/day</div>'
        f'</div>'
    )

    st.markdown(
        f'<div style="background:rgba(30,48,80,0.55);border:1px solid rgba(42,64,96,0.9);'
        f'border-radius:8px;padding:11px 14px;margin:6px 0 8px 0;">'
        # Top row: SAP + description LEFT | trend label + sparkline + badge RIGHT
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
        f'gap:12px;margin-bottom:10px;">'
        f'  <div style="flex:1;min-width:0;">'
        f'    <div style="color:rgba(212,175,55,0.78);font-size:11px;font-family:monospace;'
        f'         margin-bottom:2px;white-space:nowrap;">{sap}</div>'
        f'    <div style="color:{TEXT_PRIMARY};font-size:13px;font-weight:600;'
        f'         white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px;">'
        f'         {desc}</div>'
        f'  </div>'
        f'  <div style="text-align:right;flex-shrink:0;">'
        f'    <div style="color:#4A6080;font-size:9.5px;margin-bottom:4px;'
        f'         letter-spacing:0.05em;text-transform:uppercase;">30-DAY TREND</div>'
        f'    {svg}'
        f'    <div style="margin-top:5px;">'
        f'      <span style="background:{badge_bg};border:1px solid {badge_bd};'
        f'             color:{stock_col};font-size:10px;font-weight:700;'
        f'             padding:2px 8px;border-radius:4px;">{badge_lbl}</span>'
        f'    </div>'
        f'  </div>'
        f'</div>'
        # Bottom row: 3-stat strip
        f'<div style="display:flex;align-items:flex-start;">'
        f'{stats_html}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Detailed 30-day history — collapsible, closed by default
    with st.expander(f"📊 30-day history", expanded=False):
        col_c, col_r = st.columns(2)
        with col_c:
            avg_per_day = cons_total / float(lookback_days) if lookback_days else 0.0
            st.markdown(
                f'**📉 Consumption ({lookback_days}d)**<br>'
                f'<span style="color:{TEXT_MUTED};font-size:0.82rem;">'
                f'Total <b style="color:{TEXT_PRIMARY};">{cons_total:g}</b> · '
                f'Avg/day <b style="color:{TEXT_PRIMARY};">{avg_per_day:.2f}</b></span>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(
                _sparkline(_daily_series(cons_df, lookback_days), COLOR_LOW),
                use_container_width=True,
                config={"displayModeBar": False},
            )
            with st.expander(
                f"View {len(cons_df) if cons_df is not None else 0} row(s)", expanded=False
            ):
                if cons_df is not None and not cons_df.empty:
                    st.dataframe(cons_df, hide_index=True, use_container_width=True)
                else:
                    st.caption("No consumption in this window.")

        with col_r:
            rcpt_total = float(snap.get("rcpt_total") or 0.0)
            rcpt_df    = snap.get("rcpt_df")
            last_rcpt  = snap.get("last_receipt_date") or "—"
            st.markdown(
                f'**📥 Receipts ({lookback_days}d)**<br>'
                f'<span style="color:{TEXT_MUTED};font-size:0.82rem;">'
                f'Total <b style="color:{TEXT_PRIMARY};">{rcpt_total:g}</b> · '
                f'Latest <b style="color:{TEXT_PRIMARY};">{last_rcpt}</b></span>',
                unsafe_allow_html=True,
            )
            st.plotly_chart(
                _sparkline(_daily_series(rcpt_df, lookback_days), COLOR_OK),
                use_container_width=True,
                config={"displayModeBar": False},
            )
            with st.expander(
                f"View {len(rcpt_df) if rcpt_df is not None else 0} row(s)", expanded=False
            ):
                if rcpt_df is not None and not rcpt_df.empty:
                    st.dataframe(rcpt_df, hide_index=True, use_container_width=True)
                else:
                    st.caption("No receipts in this window.")


# ===========================================================================
# OCR REVIEW GRID (Phase 5 — preview / edit / pick before submit)
# ===========================================================================
def render_ocr_review_grid(
    resolved_rows: list[dict],
    inventory_df: pd.DataFrame,
    *,
    key_prefix: str,
    columns: list[str],
    column_config: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Editable grid for OCR-extracted rows. Handles three states per row:

      'auto'    → SAP_Code already filled (high-confidence fuzzy match)
      'pick'    → render a selectbox of candidates ABOVE the grid for the
                  user to choose; the chosen SAP back-fills into the grid.
      'unknown' → SAP cell stays empty; user must type/pick from selectbox.

    Returns (edited_df, pick_choices) where pick_choices maps row index →
    chosen SAP_Code so callers can validate completeness before submit.

    `columns` is the list of column names to expose in the editor (caller
    decides). `column_config` is a Streamlit data_editor column_config dict
    if the caller wants to force types / disable cells.
    """
    if not resolved_rows:
        render_empty_state(icon="📭", title="No rows to review", hint="Upload an image or paste text above.")
        return pd.DataFrame(), {}

    # Pre-render the candidate pickers ABOVE the grid so users see
    # ambiguous rows distinctly. Their choice flows into the grid via
    # session_state, then we rebuild the DF with those choices applied.
    pick_choices: dict[int, str] = {}
    needs_pick = [(i, r) for i, r in enumerate(resolved_rows) if r.get("match_state") == "pick"]
    if needs_pick:
        st.markdown("**🤔 Ambiguous matches — pick the right one:**")
        for i, row in needs_pick:
            cands = row.get("candidates", [])
            labels = ["— pick one —"] + [
                f"[{c['SAP_Code']}] {c['Equipment_Description']}  ({int(c['score']*100)}%)"
                for c in cands
            ]
            key = f"{key_prefix}_pick_{i}"
            chosen_label = st.selectbox(
                f"Row {i+1}: **{row.get('material_text','')}**",
                options=labels,
                index=st.session_state.get(key, 0),
                key=key,
            )
            ci = labels.index(chosen_label) - 1
            if ci >= 0:
                pick_choices[i] = cands[ci]["SAP_Code"]
                # Back-fill resolved row so it appears in the editable grid.
                row["SAP_Code"] = cands[ci]["SAP_Code"]
                row["Equipment_Description"] = cands[ci]["Equipment_Description"]
                row["Material_Code"] = cands[ci].get("Material_Code", "")
                if not row.get("UOM"):
                    row["UOM"] = cands[ci].get("UOM", "")

    # Build the DataFrame for the editor.
    df = pd.DataFrame(resolved_rows)
    # Keep only the columns the caller asked for, in order, preserving any
    # missing column as a blank column so the user can still type.
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    df = df[columns].copy()

    # Status pill column — read-only, gives the user a quick read on which
    # rows still need attention. Computed from the underlying resolved rows.
    status_icons = []
    for r in resolved_rows:
        ms = r.get("match_state")
        if ms == "auto":
            status_icons.append("✅ auto")
        elif ms == "pick":
            status_icons.append("✋ pick" if not r.get("SAP_Code") else "✅ picked")
        else:
            status_icons.append("✏️ manual")
    df.insert(0, "Match", status_icons)

    cfg = {"Match": st.column_config.TextColumn("Match", disabled=True, width="small")}
    if column_config:
        cfg.update(column_config)

    edited = st.data_editor(
        df,
        column_config=cfg,
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_editor",
    )
    return edited, pick_choices


# ===========================================================================
# CONFIRM HELPER (Round 15 — destructive-action two-step gate)
# ===========================================================================
def render_confirm(
    button_key: str,
    *,
    action_label: str,
    body: str,
    danger: bool = False,
    confirm_label: str | None = None,
    cancel_label: str = "Cancel",
) -> bool:
    """Two-step confirmation pattern for destructive actions.

    First call from the parent renders the initial primary-styled action
    button. Clicking it sets `_confirm::{button_key}` in session_state and
    reruns; on the next render the helper draws an inline confirmation card
    plus Confirm + Cancel buttons. Returns True only when the user clicks
    Confirm — caller wraps its destructive mutation in
    `if render_confirm(...): do_the_thing()`.

    Args:
        button_key: stable widget key prefix. Must be unique per call site.
        action_label: text on the initial action button (e.g. "🗑️ Reject").
        body: short explanation rendered inside the confirmation card.
        danger: red accent when True, gold otherwise.
        confirm_label: text on the Confirm button. Defaults to action_label.
    """
    import streamlit as st
    state_key = f"_confirm::{button_key}"
    confirm_label = confirm_label or action_label

    if not st.session_state.get(state_key):
        if st.button(
            action_label,
            key=f"{button_key}::trigger",
            type="primary",
            use_container_width=True,
        ):
            st.session_state[state_key] = True
            st.rerun()
        return False

    # Confirmation card.
    accent = "#EF4444" if danger else "#F59E0B"
    bg     = "rgba(239,68,68,0.10)" if danger else "rgba(245,158,11,0.10)"
    border = "rgba(239,68,68,0.40)" if danger else "rgba(245,158,11,0.40)"
    st.markdown(
        f'<div style="background:{bg};border:1px solid {border};'
        f'border-left:4px solid {accent};border-radius:8px;'
        f'padding:10px 12px;margin:4px 0 6px 0;">'
        f'<b style="color:{accent};">⚠️ Please confirm</b><br>'
        f'<span style="color:#7A8FA0;font-size:12px;">{body}</span></div>',
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    confirmed = False
    with c1:
        if st.button(
            confirm_label,
            key=f"{button_key}::confirm",
            type="primary",
            use_container_width=True,
        ):
            confirmed = True
            st.session_state.pop(state_key, None)
    with c2:
        if st.button(
            cancel_label,
            key=f"{button_key}::cancel",
            use_container_width=True,
        ):
            st.session_state.pop(state_key, None)
            st.rerun()
    return confirmed


# ===========================================================================
# HERO METRIC STRIP (Phase 4 — at-a-glance KPIs for each portal page)
# ===========================================================================
def render_hero_metrics(metrics: list[dict]) -> None:
    """
    Renders a horizontal row of branded metric cards above a page's main
    content. Each metric is a dict:
        {"label": str, "value": str|int|float, "delta": str|None,
         "tone": "ok" | "low" | "critical" | "neutral" (default)}

    `tone` colour-codes the accent stripe so a glance at the strip tells the
    user where to look. The strip auto-collapses to one column per metric on
    mobile via the global @media (max-width: 768px) rule.
    """
    if not metrics:
        return

    tone_color = {
        "ok":       COLOR_OK,
        "low":      COLOR_LOW,
        "critical": COLOR_CRITICAL,
        "neutral":  BRAND_GOLD,
    }
    cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        accent = tone_color.get(m.get("tone", "neutral"), BRAND_GOLD)
        delta_html = (
            f'<div style="color:{TEXT_MUTED};font-size:0.78rem;margin-top:4px;">{m["delta"]}</div>'
            if m.get("delta") else ""
        )
        with col:
            st.markdown(
                f'<div style="padding:14px 18px;background:rgba(10,25,47,0.55);'
                f'border:1px solid rgba(255,215,0,0.14);border-left:4px solid {accent};'
                f'border-radius:12px;animation:fadeInUp 0.4s ease both;">'
                f'<div style="color:{TEXT_MUTED};font-size:0.78rem;'
                f'text-transform:uppercase;letter-spacing:0.05em;">{m["label"]}</div>'
                f'<div style="color:{accent};font-size:1.7rem;font-weight:700;line-height:1.1;'
                f'margin-top:4px;">{m["value"]}</div>'
                f'{delta_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


# ===========================================================================
# FEFO LOT PANEL (Phase 4 — First-Expiry-First-Out picking)
# ===========================================================================
def render_fefo_panel(lots_df: pd.DataFrame, max_rows: int = 5) -> None:
    """
    Compact inline FEFO lot list (Claude Design v2).

    Amber card with header "🏷️ FEFO — First Expiry, First Out".
    Each lot row: received-on date (monospace) + ×qty dim  |  Exp date + "USE FIRST" badge
    First lot with remaining stock gets the amber "USE FIRST" badge.
    No expander — all lots visible at a glance (capped at max_rows).
    """
    if lots_df is None or lots_df.empty:
        return

    available = lots_df[lots_df["Remaining_Qty"] > 0].copy()
    if available.empty:
        st.caption("📦 No lots with remaining stock for this material.")
        return

    _AMBER = "#F59E0B"
    _DIM   = "#4A6080"

    rows_html = ""
    for i, (_, row) in enumerate(lots_df.head(max_rows).iterrows()):
        remaining = float(row.get("Remaining_Qty", 0))
        is_first  = i == 0 and remaining > 0

        border_top = "border-top:1px solid rgba(42,64,96,0.32);" if i > 0 else ""
        lot_id     = str(row.get("Lot_Date", f"Lot {i+1}"))
        exp_raw    = row.get("Expiry_Date")
        exp_str    = str(exp_raw) if exp_raw else "No expiry"
        days       = row.get("Days_Until_Expiry")

        if days is not None and not pd.isna(days):
            exp_color = _AMBER if float(days) < 90 else _DIM
        else:
            exp_color = _DIM

        use_first_html = (
            f'<span style="background:rgba(245,158,11,0.13);'
            f'border:1px solid rgba(245,158,11,0.30);color:{_AMBER};'
            f'font-size:9.5px;font-weight:700;padding:1px 6px;'
            f'border-radius:4px;margin-left:6px;white-space:nowrap;">USE FIRST</span>'
        ) if is_first else ""

        rows_html += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:5px 0;{border_top}">'
            f'  <div style="display:flex;align-items:center;gap:8px;">'
            f'    <span style="color:{TEXT_PRIMARY};font-family:monospace;font-size:11.5px;">'
            f'      {lot_id}</span>'
            f'    <span style="color:{_DIM};font-size:11.5px;">×{remaining:g}</span>'
            f'  </div>'
            f'  <div style="display:flex;align-items:center;">'
            f'    <span style="color:{exp_color};font-size:11.5px;">Exp {exp_str}</span>'
            f'    {use_first_html}'
            f'  </div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:rgba(245,158,11,0.05);border:1px solid rgba(245,158,11,0.18);'
        f'border-radius:8px;padding:10px 12px;margin:6px 0 8px 0;">'
        f'<div style="color:{_AMBER};font-size:11.5px;font-weight:600;margin-bottom:6px;">'
        f'🏷️ FEFO — First Expiry, First Out</div>'
        f'{rows_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# LOW-STOCK SIDEBAR BADGE
# ===========================================================================
def render_low_stock_sidebar_badge(low_stock_df: pd.DataFrame) -> None:
    """
    Shows a compact low-stock alert inside the sidebar.
    Call this inside a `with st.sidebar:` block.
    Only rendered for supervisor / admin roles (main.py gates this).
    """
    if low_stock_df is None or low_stock_df.empty:
        st.markdown(
            f'<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.22);'
            f'border-radius:10px;padding:0.5rem 0.7rem;display:flex;align-items:center;gap:9px;">'
            f'<span style="font-size:1.05rem;line-height:1;">✅</span>'
            f'<span style="color:{COLOR_OK};font-size:0.8rem;font-weight:600;">All levels adequate</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    count = len(low_stock_df)
    st.markdown(
        f'<div style="background:rgba(217,119,6,0.10);border:1px solid rgba(217,119,6,0.28);'
        f'border-radius:10px;padding:0.5rem 0.7rem;display:flex;align-items:center;gap:10px;">'
        f'<div style="min-width:30px;height:30px;border-radius:50%;'
        f'background:rgba(217,119,6,0.88);display:flex;align-items:center;justify-content:center;'
        f'font-size:0.82rem;font-weight:800;color:#fff;flex-shrink:0;">{count}</div>'
        f'<div>'
        f'<div style="color:#F59E0B;font-size:0.81rem;font-weight:700;line-height:1.2;">'
        f'Items Below Min</div>'
        f'<div style="color:{TEXT_MUTED};font-size:0.69rem;margin-top:1px;">Requires attention</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    with st.expander("View items", expanded=False):
        cols_to_show = ["SAP_Code", "Equipment_Description", "Current_Stock", "Minimum_Qty", "Shortage"]
        cols_available = [c for c in cols_to_show if c in low_stock_df.columns]
        st.dataframe(low_stock_df[cols_available], hide_index=True, use_container_width=True)


# ===========================================================================
# HTML5 BARCODE / QR SCANNER  (mobile-first, camera-based)
# ===========================================================================
def render_barcode_scanner(input_key: str = "barcode_scan_result") -> str | None:
    """
    Renders an HTML5 camera barcode/QR scanner using the html5-qrcode CDN library.
    Designed for mobile phones and tablets on the warehouse floor.

    Returns the SAP code string that was scanned (from st.session_state),
    or None if nothing has been scanned yet.

    Usage in a page:
        scanned = render_barcode_scanner()
        if scanned:
            st.session_state["sap_code_input"] = scanned
    """
    # Session state key for storing the latest scan
    if input_key not in st.session_state:
        st.session_state[input_key] = ""

    scanner_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
      <style>
        body {{
          margin: 0; padding: 8px;
          background: {DARK_SURFACE}; color: {TEXT_PRIMARY};
          font-family: 'Inter', sans-serif;
        }}
        #reader {{ width: 100%; border-radius: 8px; overflow: hidden; }}
        #result-box {{
          margin-top: 12px;
          background: {DARK_SURFACE_2};
          border: 1px solid {BRAND_GOLD};
          border-radius: 8px;
          padding: 10px 14px;
          display: none;
        }}
        #result-box h4 {{ margin: 0 0 4px 0; color: {BRAND_GOLD}; font-size: 0.8rem; }}
        #result-text {{
          font-size: 1.4rem; font-weight: 700; color: {TEXT_PRIMARY};
          word-break: break-all;
        }}
        #copy-btn {{
          margin-top: 8px;
          padding: 6px 16px;
          background: {BRAND_GOLD};
          color: {BRAND_BLUE};
          border: none; border-radius: 6px;
          font-weight: 700; font-size: 0.85rem;
          cursor: pointer; width: 100%;
        }}
        #copy-btn:hover {{ background: {BRAND_GOLD_LIGHT}; }}
        #status {{ font-size: 0.75rem; color: {TEXT_MUTED}; margin-top: 6px; text-align:center; }}
      </style>
    </head>
    <body>
      <div id="reader"></div>
      <div id="result-box">
        <h4>✅ SCANNED CODE</h4>
        <div id="result-text">—</div>
        <button id="copy-btn" onclick="copyResult()">📋 Copy Code</button>
      </div>
      <div id="status">Point camera at barcode or QR code</div>

      <script>
        let lastCode = "";

        // Phase 4 — audio + haptic feedback for gloved floor users.
        // Web Audio: short, friendly two-tone "ding" using sine oscillators.
        // No external assets, no autoplay-policy issues (triggered by user
        // gesture chain: camera permission grant). Vibration: 60 ms pulse on
        // devices that support it (most Android, no-op on iOS).
        let audioCtx = null;
        function beepDing() {{
          try {{
            if (!audioCtx) {{
              const AC = window.AudioContext || window.webkitAudioContext;
              if (!AC) return;
              audioCtx = new AC();
            }}
            const now = audioCtx.currentTime;
            function tone(freq, start, dur) {{
              const osc = audioCtx.createOscillator();
              const gain = audioCtx.createGain();
              osc.type = "sine";
              osc.frequency.value = freq;
              gain.gain.setValueAtTime(0.0001, now + start);
              gain.gain.exponentialRampToValueAtTime(0.18, now + start + 0.01);
              gain.gain.exponentialRampToValueAtTime(0.0001, now + start + dur);
              osc.connect(gain).connect(audioCtx.destination);
              osc.start(now + start);
              osc.stop(now + start + dur);
            }}
            tone(880, 0,    0.10);
            tone(1320, 0.08, 0.14);
          }} catch (e) {{ /* silent fail; never block scan */ }}
        }}
        function vibrate(ms) {{
          try {{ if (navigator.vibrate) navigator.vibrate(ms); }} catch (e) {{}}
        }}

        function onScanSuccess(decodedText) {{
          if (decodedText === lastCode) return;
          lastCode = decodedText;
          document.getElementById("result-text").innerText = decodedText;
          document.getElementById("result-box").style.display = "block";
          document.getElementById("status").innerText = "Scan successful — copy the code above";
          // Sensory confirmation for floor users (gloves, noisy environment).
          beepDing();
          vibrate(60);
          // Notify parent Streamlit frame
          window.parent.postMessage({{
            type: "streamlit:setComponentValue",
            value: decodedText
          }}, "*");
        }}

        function onScanError(err) {{
          // Suppress per-frame errors (expected during scanning)
        }}

        function copyResult() {{
          const text = document.getElementById("result-text").innerText;
          navigator.clipboard.writeText(text).then(() => {{
            document.getElementById("copy-btn").innerText = "✅ Copied!";
            setTimeout(() => {{ document.getElementById("copy-btn").innerText = "📋 Copy Code"; }}, 2000);
          }});
        }}

        const html5QrCode = new Html5Qrcode("reader");
        Html5Qrcode.getCameras().then(cameras => {{
          if (cameras && cameras.length) {{
            // Prefer rear camera on mobile
            const cam = cameras.length > 1 ? cameras[1] : cameras[0];
            html5QrCode.start(
              cam.id,
              {{ fps: 10, qrbox: {{ width: 250, height: 150 }} }},
              onScanSuccess, onScanError
            );
          }} else {{
            document.getElementById("status").innerText = "No camera found on this device.";
          }}
        }}).catch(err => {{
          document.getElementById("status").innerText = "Camera access denied. Use manual input below.";
        }});
      </script>
    </body>
    </html>
    """

    st.components.v1.html(scanner_html, height=380, scrolling=False)

    # Manual fallback — always visible below the scanner
    manual = st.text_input(
        "📟 Manual SAP Code Entry (or paste scanned result here)",
        value=st.session_state[input_key],
        key=f"{input_key}_manual",
        placeholder="e.g. 1001234",
    )
    if manual:
        st.session_state[input_key] = manual

    return st.session_state[input_key] or None


# ===========================================================================
# PREDICTIVE ANALYTICS — BURN RATE
# ===========================================================================

def render_burn_rate_chart(forecast_df: pd.DataFrame, top_n: int = 15) -> None:
    """
    Horizontal bar chart of Days_Remaining per material, color-coded by urgency.
    Shows the top_n most urgent items (fewest days remaining).
    Red < 7 days, Amber 7–14 days, Green ≥ 14 days.
    Includes a dashed vertical threshold line at 7 days.
    """
    if forecast_df is None or forecast_df.empty:
        st.info("No burn rate data available — no consumption recorded in the last 30 days.")
        return

    df = forecast_df.dropna(subset=["Days_Remaining"]).copy()
    if df.empty:
        st.info("No burn rate data available — all active materials have undefined burn rates.")
        return

    desc_col = "Equipment_Description" if "Equipment_Description" in df.columns else "SAP_Code"
    df["Label"] = df[desc_col].astype(str).str[:35]
    df = df.nsmallest(top_n, "Days_Remaining")

    def _color(days: float) -> str:
        if days < 10:
            return COLOR_CRITICAL
        if days < 30:
            return COLOR_LOW
        return COLOR_OK

    df["_color"] = df["Days_Remaining"].apply(_color)

    _legend_map = {
        COLOR_CRITICAL: "< 10 days (Critical)",
        COLOR_LOW:       "10–30 days (Low)",
        COLOR_OK:        "≥ 30 days (OK)",
    }

    fig = go.Figure()
    for color_val, group in df.groupby("_color", sort=False):
        fig.add_trace(go.Bar(
            x=group["Days_Remaining"],
            y=group["Label"],
            orientation="h",
            marker_color=color_val,
            name=_legend_map.get(color_val, ""),
            customdata=group[["Daily_Burn_Rate", "Current_Stock", "SAP_Code"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Days Remaining: <b>%{x:.1f}</b><br>"
                "Daily Burn Rate: %{customdata[0]:.3f} units/day<br>"
                "Current Stock: %{customdata[1]:,.1f}<br>"
                "SAP Code: %{customdata[2]}<extra></extra>"
            ),
        ))

    fig.add_vline(
        x=30,
        line_dash="dash",
        line_color=COLOR_LOW,
        annotation_text="30-day alert",
        annotation_font_color=COLOR_LOW,
        annotation_position="top",
    )
    fig.update_layout(
        title=dict(text=f"Burn Rate Forecast — Days of Stock Remaining (Top {top_n} Most Urgent)", font=dict(color=BRAND_GOLD, size=16)),
        barmode="overlay",
        yaxis=dict(categoryorder="total ascending", color=TEXT_PRIMARY),
        xaxis=dict(title="Days Remaining", color=TEXT_PRIMARY),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT_PRIMARY, family="Inter"),
        margin=dict(t=60, b=20, l=0, r=20),
        template=PLOTLY_TEMPLATE,
        legend=dict(orientation="h", y=-0.15, font=dict(color=TEXT_PRIMARY)),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_burn_alert_banner(forecast_df: pd.DataFrame) -> None:
    """
    Inline alert banner listing materials with < 7 days of stock remaining.
    Renders nothing if there are no alerts — safe to call unconditionally.
    """
    if forecast_df is None or forecast_df.empty:
        return

    alert_df = forecast_df[forecast_df["Burn_Alert"] == True].copy()
    if alert_df.empty:
        return

    count = len(alert_df)
    item_word = "item" if count == 1 else "items"
    st.markdown(
        f'<div style="background:{COLOR_CRITICAL}22; border:1px solid {COLOR_CRITICAL}; '
        f'border-radius:10px; padding:0.6rem 1rem; margin-bottom:1rem;">'
        f'<span style="color:{COLOR_CRITICAL}; font-weight:700; font-size:0.95rem;">'
        f'🔥 {count} {item_word} will run out within 7 days at the current burn rate</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    with st.expander("View Critical Items", expanded=False):
        cols_to_show = [c for c in [
            "SAP_Code", "Equipment_Description", "UOM",
            "Current_Stock", "Daily_Burn_Rate", "Days_Remaining",
        ] if c in alert_df.columns]
        st.dataframe(alert_df[cols_to_show], hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7E — draft_bus: client-side + server-side form draft recovery
# ═══════════════════════════════════════════════════════════════════════════
# Two-tier safety net for in-flight form data in low-connectivity sites:
#   1. Client localStorage via streamlit-local-storage (per-browser, per-device).
#      Survives WebSocket drops, page reloads, browser tab crashes.
#   2. Server-side form_drafts table (cross-device + manual Save Draft).
#      1/min throttle for server-side writes per spec Q4(a).
#
# If the streamlit-local-storage package fails to import (corporate proxy,
# air-gapped env), helpers fall through to SERVER-SIDE-ONLY mode with NO
# user-visible error (spec Q6(a)) — drafts still recover across sessions.
# ═══════════════════════════════════════════════════════════════════════════
import json as _json_7e
import time as _time_7e
from datetime import datetime as _dt_7e

# Optional dependency. Silent fallback per spec Q6(a).
try:
    from streamlit_local_storage import LocalStorage as _LocalStorage  # type: ignore
    _HAS_LOCAL_STORAGE = True
except Exception:
    _LocalStorage = None  # type: ignore
    _HAS_LOCAL_STORAGE = False


def _draft_ls():
    """Lazy singleton — instantiated once per Streamlit session.

    Returns None when `GI_SUPPRESS_LOCAL_STORAGE=1` is set — the test
    harness uses this because AppTest doesn't drive the component's
    JS iframe and the script run hangs past the 30s timeout. Server-side
    draft layer continues to function unchanged."""
    import os as _os
    if _os.environ.get("GI_SUPPRESS_LOCAL_STORAGE") == "1":
        return None
    if not _HAS_LOCAL_STORAGE:
        return None
    if "_draft_ls_singleton" not in st.session_state:
        try:
            st.session_state["_draft_ls_singleton"] = _LocalStorage()
        except Exception:
            return None
    return st.session_state["_draft_ls_singleton"]


def _draft_local_key(form_id: str, username: str) -> str:
    return f"gi_draft::{username}::{form_id}"


def _draft_throttle_key(form_id: str) -> str:
    return f"_draft_last_server_save::{form_id}"


def _draft_capture_payload(state_keys: list[str]) -> dict:
    """Snapshot the current values of st.session_state[k] for every k in
    state_keys that exists. Missing keys are silently skipped — a form with
    optional widgets stays serialisable."""
    payload = {}
    for k in state_keys or ():
        if k in st.session_state:
            v = st.session_state[k]
            # Streamlit widgets can hold non-JSON types; coerce via default=str.
            payload[k] = v
    return payload


def _draft_rehydrate(payload: dict, state_keys: list[str]) -> None:
    """Write payload values back into st.session_state for each known key.
    Streamlit picks them up on the next rerun's widget render."""
    if not isinstance(payload, dict):
        return
    for k in state_keys or ():
        if k in payload:
            st.session_state[k] = payload[k]


def render_form_recovery_banner(
    form_id: str,
    username: str,
    site_id: str,
    state_keys: list[str],
) -> None:
    """Mount-time check. Renders the 🛟 Restore draft banner if a draft is
    available from either localStorage OR the server-side form_drafts table.

    Spec Q3(a) — when both layers have a draft, show a comparison dialog so
    the user picks (Local vs Cloud + timestamps).
    """
    from database import get_form_draft as _db_get_draft

    # Skip if user has already chosen this session.
    decided_key = f"_draft_decided::{form_id}::{username}"
    if st.session_state.get(decided_key):
        return

    # ── Pull both sides ──────────────────────────────────────────────────
    local_payload = None
    local_ts = None
    ls = _draft_ls()
    if ls is not None:
        try:
            raw = ls.getItem(_draft_local_key(form_id, username))
            if raw:
                blob = _json_7e.loads(raw)
                local_payload = blob.get("payload")
                local_ts      = blob.get("updated_at")
        except Exception:
            local_payload = None

    server_payload = None
    server_ts = None
    try:
        srv = _db_get_draft(username, form_id)
        if srv:
            server_payload = srv.get("payload")
            server_ts      = srv.get("updated_at")
    except Exception:
        server_payload = None

    if not local_payload and not server_payload:
        return  # nothing to restore

    # ── Conflict dialog (both present) ───────────────────────────────────
    if local_payload and server_payload:
        st.markdown(
            f'<div style="background:{BRAND_GOLD}14;'
            f'border:1px solid {BRAND_GOLD}66;border-radius:8px;'
            f'padding:12px 16px;margin:8px 0;">'
            f'<b style="color:{BRAND_GOLD};">🛟 Two drafts available — '
            f'pick one to restore</b><br>'
            f'<span style="color:{TEXT_SECONDARY};font-size:12.5px;">'
            f'Both this browser and the server have a saved draft. '
            f'Pick whichever you trust.</span></div>',
            unsafe_allow_html=True,
        )
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button(f"🖥️ Restore Local · {local_ts or '—'}",
                         key=f"_draft_restore_local::{form_id}",
                         use_container_width=True):
                _draft_rehydrate(local_payload, state_keys)
                st.session_state[decided_key] = True
                st.toast("🛟 Local draft restored.", icon="✅")
                st.rerun()
        with col_b:
            if st.button(f"☁️ Restore Cloud · {server_ts or '—'}",
                         key=f"_draft_restore_server::{form_id}",
                         use_container_width=True):
                _draft_rehydrate(server_payload, state_keys)
                st.session_state[decided_key] = True
                st.toast("☁️ Cloud draft restored.", icon="✅")
                st.rerun()
        with col_c:
            if st.button("🗑️ Discard both",
                         key=f"_draft_discard_both::{form_id}",
                         use_container_width=True):
                clear_form_draft(form_id, username)
                st.session_state[decided_key] = True
                st.rerun()
        return

    # ── Single-side restore banner ───────────────────────────────────────
    picked_payload = local_payload or server_payload
    picked_ts = local_ts or server_ts
    picked_src = "browser" if local_payload else "server"
    st.markdown(
        f'<div style="background:{BRAND_GOLD}14;'
        f'border:1px solid {BRAND_GOLD}66;border-radius:8px;'
        f'padding:10px 14px;margin:8px 0;">'
        f'<b style="color:{BRAND_GOLD};">🛟 Saved draft found</b> '
        f'<span style="color:{TEXT_SECONDARY};font-size:12.5px;">'
        f'({picked_src}, updated {picked_ts or "—"})</span></div>',
        unsafe_allow_html=True,
    )
    col_r, col_d = st.columns(2)
    with col_r:
        if st.button("✅ Restore draft",
                     key=f"_draft_restore::{form_id}",
                     type="primary",
                     use_container_width=True):
            _draft_rehydrate(picked_payload, state_keys)
            st.session_state[decided_key] = True
            st.toast("🛟 Draft restored.", icon="✅")
            st.rerun()
    with col_d:
        if st.button("🗑️ Discard",
                     key=f"_draft_discard::{form_id}",
                     use_container_width=True):
            clear_form_draft(form_id, username)
            st.session_state[decided_key] = True
            st.rerun()


def auto_save_form_draft(
    form_id: str,
    username: str,
    site_id: str,
    state_keys: list[str],
) -> None:
    """Per-rerun snapshot. Always writes to localStorage; throttles server-side
    writes to 1/min per spec Q4(a). Idempotent — empty payload → no-op."""
    payload = _draft_capture_payload(state_keys)
    if not payload:
        return

    blob = {
        "payload":    payload,
        "updated_at": _dt_7e.utcnow().isoformat(timespec="seconds"),
    }

    # ── Layer 1: localStorage (every rerun) ──────────────────────────────
    ls = _draft_ls()
    if ls is not None:
        try:
            ls.setItem(
                _draft_local_key(form_id, username),
                _json_7e.dumps(blob, default=str),
            )
        except Exception:
            pass  # silent fallback per spec Q6(a)

    # ── Layer 2: server-side (throttled to 1/min) ────────────────────────
    last = st.session_state.get(_draft_throttle_key(form_id), 0.0)
    now = _time_7e.time()
    if now - last >= 60.0:
        try:
            from database import upsert_form_draft as _db_upsert
            _db_upsert(username, form_id, payload, site_id=site_id)
            st.session_state[_draft_throttle_key(form_id)] = now
        except Exception:
            pass


def render_manual_save_draft_button(
    form_id: str,
    username: str,
    site_id: str,
    state_keys: list[str],
    *,
    label: str = "💾 Save Draft",
    key_suffix: str = "",
) -> None:
    """Explicit Save Draft button — bypasses the 1/min throttle. Placed next
    to Submit by callers (spec Q5)."""
    btn_key = f"_draft_manual_save::{form_id}::{key_suffix or 'default'}"
    if st.button(label, key=btn_key, use_container_width=True):
        payload = _draft_capture_payload(state_keys)
        if not payload:
            st.toast("Nothing to save yet.", icon="ℹ️")
            return
        ls = _draft_ls()
        if ls is not None:
            try:
                blob = {
                    "payload":    payload,
                    "updated_at": _dt_7e.utcnow().isoformat(timespec="seconds"),
                }
                ls.setItem(
                    _draft_local_key(form_id, username),
                    _json_7e.dumps(blob, default=str),
                )
            except Exception:
                pass
        try:
            from database import upsert_form_draft as _db_upsert
            _db_upsert(username, form_id, payload, site_id=site_id)
            # Reset throttle so the next auto-save isn't immediate.
            st.session_state[_draft_throttle_key(form_id)] = _time_7e.time()
            st.toast("💾 Draft saved.", icon="✅")
        except Exception as e:
            st.toast(f"Couldn't save server-side: {type(e).__name__}", icon="⚠️")


def clear_form_draft(form_id: str, username: str) -> None:
    """Called after successful submit. Clears local AND server. Idempotent
    on missing entries — never raises."""
    ls = _draft_ls()
    if ls is not None:
        try:
            ls.deleteItem(_draft_local_key(form_id, username))
        except Exception:
            pass
    try:
        from database import delete_form_draft as _db_del
        _db_del(username, form_id)
    except Exception:
        pass
    # Reset the once-this-session "decided" flag so a fresh draft can start.
    st.session_state.pop(f"_draft_decided::{form_id}::{username}", None)
