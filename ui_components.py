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
        AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode,
    )
    try:
        from st_aggrid import ColumnsAutoSizeMode
        _HAS_AUTO_SIZE = True
    except ImportError:
        _HAS_AUTO_SIZE = False
    AGGRID_AVAILABLE = True
except ImportError:
    AGGRID_AVAILABLE = False


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

    /* Base pill label */
    [data-testid="stSidebar"] [role="radiogroup"] label {{
        display: flex;
        align-items: center;
        width: 100%;
        padding: 0.48rem 1rem;
        margin: 0.18rem 0;
        border-radius: 50px;
        border: 1px solid transparent;
        cursor: pointer;
        font-size: 0.88rem;
        font-weight: 500;
        color: {TEXT_SECONDARY};
        transition: all 0.3s ease;
        background: transparent;
    }}

    /* Hover state */
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {{
        background: rgba(251, 191, 36, 0.10);
        border-color: rgba(251, 191, 36, 0.40);
        color: {BRAND_GOLD};
        padding-left: 1.2rem;
    }}

    /* Selected / active state */
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
        background: rgba(251, 191, 36, 0.16);
        border-color: rgba(251, 191, 36, 0.65);
        color: {BRAND_GOLD};
        font-weight: 700;
        box-shadow: 0 2px 14px rgba(251, 191, 36, 0.13);
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
        background: rgba(180, 83, 9, 0.08);
        border-color: rgba(180, 83, 9, 0.35);
        color: #B45309;
    }}
    [data-theme="light"] [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
        background: rgba(180, 83, 9, 0.12);
        border-color: rgba(180, 83, 9, 0.55);
        color: #B45309;
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

    </style>
    """, unsafe_allow_html=True)


# ===========================================================================
# BRANDED HEADER
# ===========================================================================
def render_brand_header(subtitle: str = None) -> None:
    """Renders the GI corporate header with animated underline."""
    sub = subtitle or APP_SUBTITLE
    st.markdown(f"""
    <div style="padding: 1rem 0 0.5rem 0; border-bottom: 2px solid {DARK_BORDER}; margin-bottom: 1.5rem;">
        <div style="display:flex; align-items:baseline; gap: 0.25rem;">
            <span style="color:{BRAND_BLUE}; font-size:2rem; font-weight:900; letter-spacing:-1px;">General</span>
            <span style="color:{BRAND_GOLD}; font-size:2rem; font-weight:900; letter-spacing:-1px;">&nbsp;Industries</span>
            <span style="color:{TEXT_MUTED}; font-size:1rem; margin-left:0.75rem;">{APP_ICON} v{APP_VERSION}</span>
        </div>
        <p style="color:{TEXT_MUTED}; font-size:0.85rem; margin:0.25rem 0 0 0; letter-spacing:0.05em;
                  text-transform:uppercase;">{sub}</p>
    </div>
    """, unsafe_allow_html=True)


# ===========================================================================
# AGGRID WRAPPER
# ===========================================================================
def render_aggrid(
    df: pd.DataFrame,
    key: str = "aggrid",
    height: int = AGGRID_HEIGHT,
    fit_columns: bool = True,
    page_size: int = AGGRID_PAGE_SIZE,
) -> dict | None:
    """
    Renders a DataFrame using AgGrid with:
      - Column filtering (per-column filter boxes)
      - Column resizing + drag-to-reorder
      - Sortable columns
      - Pagination
      - Side-bar panel for column visibility toggle
    Falls back to st.dataframe if st-aggrid is not installed.

    Returns the AgGrid response dict (contains selected rows etc.)
    or None if using fallback.
    """
    if df is None or df.empty:
        st.info("No data to display.")
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
        color_continuous_scale=[[0, BRAND_BLUE_LIGHT], [1, BRAND_GOLD]],
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


def render_stock_vs_minimum_bar(live_df: pd.DataFrame) -> None:
    """
    Grouped bar chart: Current_Stock vs Minimum_Qty for all items.
    Useful for spotting gaps at a glance.
    """
    if live_df.empty:
        return

    desc_col = "Equipment_Description" if "Equipment_Description" in live_df.columns else "SAP_Code"
    df = live_df[[desc_col, "Current_Stock", "Minimum_Qty"]].copy()
    df["Label"] = df[desc_col].astype(str).str[:30]
    df = df[df["Minimum_Qty"] > 0]  # only items with thresholds set

    if df.empty:
        st.info("No minimum quantity thresholds configured yet.")
        return

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Current Stock",
        x=df["Label"], y=df["Current_Stock"],
        marker_color=BRAND_BLUE_LIGHT,
    ))
    fig.add_trace(go.Bar(
        name="Minimum Required",
        x=df["Label"], y=df["Minimum_Qty"],
        marker_color=BRAND_GOLD,
        opacity=0.7,
    ))
    fig.update_layout(
        barmode="group",
        template=PLOTLY_TEMPLATE,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": TEXT_PRIMARY, "family": "Inter"},
        title={"text": "Stock vs Minimum Threshold", "font": {"color": BRAND_GOLD, "size": 16}},
        legend={"orientation": "h", "y": 1.1},
        margin={"t": 60, "b": 60},
        xaxis={"tickangle": -35},
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
# LOW-STOCK SIDEBAR BADGE
# ===========================================================================
def render_low_stock_sidebar_badge(low_stock_df: pd.DataFrame) -> None:
    """
    Shows a compact low-stock alert inside the sidebar.
    Call this inside a `with st.sidebar:` block.
    Only rendered for supervisor / admin roles (Module 3 gates this).
    """
    if low_stock_df is None or low_stock_df.empty:
        st.success("✅ All stock levels adequate", icon="✅")
        return

    count = len(low_stock_df)
    st.markdown(
        f'<div class="low-stock-badge">🔴 {count} item{"s" if count > 1 else ""} low on stock</div>',
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

        function onScanSuccess(decodedText) {{
          if (decodedText === lastCode) return;
          lastCode = decodedText;
          document.getElementById("result-text").innerText = decodedText;
          document.getElementById("result-box").style.display = "block";
          document.getElementById("status").innerText = "Scan successful — copy the code above";
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
        if days < 7:
            return COLOR_CRITICAL
        if days < 14:
            return COLOR_LOW
        return COLOR_OK

    df["_color"] = df["Days_Remaining"].apply(_color)

    fig = go.Figure()
    for color_val, group in df.groupby("_color", sort=False):
        fig.add_trace(go.Bar(
            x=group["Days_Remaining"],
            y=group["Label"],
            orientation="h",
            marker_color=color_val,
            name={COLOR_CRITICAL: "< 7 days (Critical)", COLOR_LOW: "7–14 days (Low)", COLOR_OK: "≥ 14 days (OK)"}.get(color_val, ""),
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
        x=7,
        line_dash="dash",
        line_color=COLOR_CRITICAL,
        annotation_text="7-day alert",
        annotation_font_color=COLOR_CRITICAL,
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
