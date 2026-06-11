"""
pages_internal/live_dashboard.py — Live Inventory Dashboard
============================================================
Extracted from main.py during Phase 2 structure refactor.
Phase 3: collapsible "Ask in plain English" panel added above the table
when AI_ENABLED is True. Existing SQL/math/cache paths are untouched.
"""

import streamlit as st

from config import AGGRID_HEIGHT, AI_ENABLED
from cache_layer import (
    cached_live_inventory,
    cached_burn_rate_and_forecast,
    cached_low_stock_items,
    cached_short_dated_stock,
    cached_inventory_valuation,
    cached_total_inventory_value,
)
from database import format_sar
from ui_components import (
    render_brand_header,
    render_aggrid,
    render_top_consumed_bar,
    render_stock_vs_minimum_bar,
    render_burn_rate_chart,
    render_burn_alert_banner,
    render_hero_metrics,
    STATUS_BADGE_JS,
)


def _render_nl_search_panel() -> None:
    """Phase 3 — natural-language inventory search via local Ollama."""
    # Lazy import keeps the page snappy when AI_ENABLED=False and avoids a
    # network probe at module load.
    from ai.nl_search import run_nl_query
    from ai.client import OLLAMA_AVAILABLE

    with st.expander("🤖 Ask in plain English (AI search)", expanded=False):
        if not OLLAMA_AVAILABLE:
            st.markdown(
                """
                <div style="background:rgba(217,119,6,0.10);border:1px solid rgba(217,119,6,0.30);
                border-radius:8px;padding:0.65rem 0.9rem;margin-bottom:0.5rem;">
                <span style="color:#F59E0B;font-weight:700;">⚠️ Ollama is not running.</span>
                <span style="color:#D1D5DB;"> To enable AI natural-language search,
                start Ollama locally:</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.code("ollama serve", language="bash")
            st.caption(
                "Then pull the `qwen2.5-coder:7b` model. "
                "Example queries: *\"show items below minimum\"*, "
                "*\"what expired last month\"*, *\"top 10 consumed this week\"*."
            )
            return

        st.caption(
            "Try: *“items expiring in 30 days at HQ”*, *“top 10 consumed materials this month”*, "
            "*“receipts above 100 units from supplier ACME”*. "
            "Read-only — your question is translated into a safe SELECT and run on a read-only connection."
        )
        question = st.text_input(
            "Your question",
            key="nl_question",
            placeholder="e.g. items below minimum at site B",
            label_visibility="collapsed",
        )
        col_run, col_clear = st.columns([1, 4])
        with col_run:
            run_clicked = st.button("Search", type="primary", key="nl_run_btn")
        with col_clear:
            if st.button("Clear", key="nl_clear_btn"):
                st.session_state.pop("nl_last_result", None)
                st.rerun()

        if run_clicked and question.strip():
            with st.spinner("Translating to SQL and querying…"):
                ok, msg, df, sql = run_nl_query(question)
            st.session_state["nl_last_result"] = (ok, msg, df, sql)

        result = st.session_state.get("nl_last_result")
        if result:
            ok, msg, df, sql = result
            if ok:
                st.success(f"Returned {len(df)} row(s).")
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.error(msg)
            if sql:
                with st.expander("Show SQL the AI generated", expanded=False):
                    st.code(sql, language="sql")


def page_live_dashboard() -> None:
    render_brand_header("Live Warehouse Stock Dashboard")
    st.title("📦 Live Inventory Dashboard")

    live_df = cached_live_inventory()
    forecast_df = cached_burn_rate_and_forecast()

    if live_df.empty:
        st.warning("No inventory data found. Please add items via the Admin Portal first.")
        return

    # Phase 4 hero strip — at-a-glance KPIs from already-cached data
    # (no new SQL added; reuses cached_low_stock_items + cached_short_dated_stock).
    low_df    = cached_low_stock_items()
    expiry_df = cached_short_dated_stock()
    total_items   = len(live_df)
    low_count     = 0 if low_df is None or low_df.empty else len(low_df)
    expiry_count  = 0 if expiry_df is None or expiry_df.empty else len(expiry_df)
    total_value   = cached_total_inventory_value()  # SAR, all sites
    render_hero_metrics([
        {"label": "Catalogue items", "value": f"{total_items:,}", "tone": "neutral"},
        {"label": "Total stock value", "value": format_sar(total_value),
         "tone": "neutral",
         "delta": "standard cost · all sites"
                  if total_value > 0 else "set Unit_Cost in Admin → DB Editor"},
        {"label": "Below minimum",   "value": low_count,
         "tone": "ok" if low_count == 0 else ("low" if low_count < 10 else "critical"),
         "delta": "all healthy" if low_count == 0 else "needs reorder"},
        {"label": "Expiring / expired", "value": expiry_count,
         "tone": "ok" if expiry_count == 0 else ("low" if expiry_count < 10 else "critical"),
         "delta": "shelf-life clear" if expiry_count == 0 else "review HOD Portal"},
    ])

    if AI_ENABLED:
        _render_nl_search_panel()

    render_burn_alert_banner(forecast_df)
    st.divider()

    display_cols = [c for c in [
        "SAP_Code", "Equipment_Description", "UOM",
        "Total_Returned", "Current_Stock", "Minimum_Qty",
    ] if c in live_df.columns]
    df_display = live_df[display_cols].copy()

    # Merge per-item valuation (Unit_Cost + Stock_Value) onto the grid so a
    # supervisor can sort the catalogue by SAR exposure.
    val_df = cached_inventory_valuation()
    if val_df is not None and not val_df.empty:
        df_display = df_display.merge(
            val_df[["SAP_Code", "Unit_Cost", "Stock_Value"]],
            on="SAP_Code", how="left",
        )
        df_display["Unit_Cost"]   = df_display["Unit_Cost"].fillna(0).round(2)
        df_display["Stock_Value"] = df_display["Stock_Value"].fillna(0).round(2)

    # Computed STATUS column — colored badge text for AgGrid
    def _status_label(row) -> str:
        s = float(row.get("Current_Stock", 0))
        m = float(row.get("Minimum_Qty", 0))
        if s <= 0:        return "Empty"
        if m > 0 and s < m:         return "Below Min"
        if m > 0 and s < m * 1.25:  return "Low"
        return "OK"

    df_display["Status"] = df_display.apply(_status_label, axis=1)
    render_aggrid(
        df_display, key="dashboard_grid", height=AGGRID_HEIGHT,
        column_styles={"Status": STATUS_BADGE_JS},
    )

    st.divider()
    with st.expander("📉 Stock vs Minimum Threshold", expanded=False):
        render_stock_vs_minimum_bar(live_df)

    with st.expander("🔥 Burn Rate Forecast (30-Day)", expanded=True):
        render_burn_rate_chart(forecast_df)

    with st.expander("📊 Top Consumed Items", expanded=False):
        render_top_consumed_bar(live_df)
