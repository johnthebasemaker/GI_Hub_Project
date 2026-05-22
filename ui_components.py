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
    Call once at the top of main.py, after st.set_page_config().
    """
    st.markdown(f"""
    <style>
    /* ── Google Fonts ── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&display=swap');

    html, body, [class*="css"] {{
        font-family: 'Inter', sans-serif;
    }}

    /* ── Hide Streamlit default branding ── */
    #MainMenu, footer {{ visibility: hidden; }}
    header {{ background: transparent !important; }}

    /* ── Sidebar styling ── */
    section[data-testid="stSidebar"] {{
        background: {DARK_SURFACE} !important;
        border-right: 1px solid {DARK_BORDER};
    }}

    /* ── Metric card overrides ── */
    div[data-testid="stMetric"] {{
        background: {DARK_SURFACE_2};
        border: 1px solid {DARK_BORDER};
        border-radius: 12px;
        padding: 1rem 1.25rem;
        transition: box-shadow 0.2s ease;
    }}
    div[data-testid="stMetric"]:hover {{
        box-shadow: 0 0 0 2px {BRAND_GOLD}44;
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
        transition: all 0.2s ease;
    }}
    div[data-testid="stButton"] > button[kind="primary"]:hover {{
        transform: translateY(-1px);
        box-shadow: 0 4px 16px {BRAND_GOLD}55;
    }}

    /* ── Tab styling ── */
    button[data-baseweb="tab"] {{
        font-weight: 600;
        color: {TEXT_MUTED} !important;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: {BRAND_GOLD} !important;
        border-bottom-color: {BRAND_GOLD} !important;
    }}

    /* ── Expander ── */
    details summary {{
        font-weight: 600;
        color: {TEXT_SECONDARY};
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
    
    /* 🚀 UPGRADE: Make the collapsed sidebar arrow massive and branded */
[data-testid="collapsedControl"] {{
        background-color: #D4AF37 !important; /* Brand Gold */
        color: #0A192F !important; /* Brand Navy */
        border-radius: 0px 8px 8px 0px !important;
        padding: 5px 15px 5px 10px !important;
        border: 2px solid #0A192F !important;
        box-shadow: 4px 4px 10px rgba(0,0,0,0.3) !important;
        transition: all 0.3s ease !important;
        z-index: 999999 !important; /* Ensure it floats above everything */
    }}
        
    /* Make it glow when hovered */
    [data-testid="collapsedControl"]:hover {{
        background-color: #F5A623 !important;
        cursor: pointer;
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
