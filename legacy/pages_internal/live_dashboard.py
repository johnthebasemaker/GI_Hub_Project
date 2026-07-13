"""
pages_internal/live_dashboard.py — Live Inventory Dashboard
============================================================
2026-06 round: switched the main inventory grid from AgGrid to a custom HTML
table matching the HOD pending-issues style — gold SAP code, monospace
numbers, em-dash for blanks, status pill at the right. Per-column filter
inputs sit above the table so the user can narrow the catalogue without
the chunky AgGrid header chrome.
"""

import html as _html
import streamlit as st
import pandas as pd

from config import AI_ENABLED
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
    render_top_consumed_bar,
    render_stock_vs_minimum_bar,
    render_burn_rate_chart,
    render_burn_alert_banner,
    render_hero_metrics,
)

# Brand tokens (kept in sync with hod_portal._C)
_C = {
    "surf":   "#162038",
    "surf2":  "#1E3050",
    "border": "#2A4060",
    "gold":   "#D4AF37",
    "text":   "#F0F4F8",
    "muted":  "#7A8FA0",
    "dim":    "#4A6080",
    "ok":     "#22C55E",
    "low":    "#F59E0B",
    "crit":   "#EF4444",
}


def _cell(val) -> str:
    if val is None: return "—"
    try:
        if pd.isna(val): return "—"
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return _html.escape(s) if s else "—"


def _status_pill(status: str) -> str:
    palette = {
        "OK":         ("#15803D", "#86EFAC"),
        "Low":        ("#854D0E", "#FDE68A"),
        "Below Min":  ("#9A3412", "#FDBA74"),
        "Empty":      ("#7F1D1D", "#FCA5A5"),
    }
    bg_dark, fg = palette.get(status, ("#374151", "#D1D5DB"))
    return (
        f'<span style="background:{bg_dark}33;border:1px solid {bg_dark}88;'
        f'color:{fg};padding:2px 9px;border-radius:999px;'
        f'font-size:11px;font-weight:700;">{_html.escape(status)}</span>'
    )


def _render_dashboard_html_table(df: pd.DataFrame, columns: list[str]) -> None:
    """
    Render `df` as a screenshot-3 style table:
      • Per-column text filter row above the table (st.text_input, columns).
      • One <table> with gold SAP cells, monospace numerics, em-dash blanks,
        status pill at the right.
    """
    if df.empty:
        st.caption("No items to display.")
        return

    # ── 1. Filter row — only the searchable text columns ──────────────────
    # Numeric / pill columns aren't useful as text filters and add latency.
    # SAP_Code, Material_Code, Equipment_Description, Category are the
    # only ones that get a live-keyup input. All other columns render an
    # empty cell so the table's column alignment stays intact.
    LIVE_FILTER_COLS = {
        "SAP_Code", "Material_Code", "Equipment_Description", "Category",
    }
    try:
        from st_keyup import st_keyup as _live_input
        _LIVE = True
    except ImportError:
        _live_input = None
        _LIVE = False

    # Round 15 — Filter UI redesign. The old per-column filter strip gave
    # every column equal width, so the searchable inputs (SAP, Mat Code,
    # Description, Category) were squeezed alongside ten unused spacers
    # and unreadable on mobile. New layout:
    #   1. ONE prominent live search box that filters across all four
    #      searchable columns at once — works great in landscape AND mobile.
    #   2. Existing per-column filter strip collapsed into an expander for
    #      power users who want to filter on Description but not Category.
    st.markdown(
        f'<div style="color:{_C["dim"]};font-size:10.5px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.08em;margin:6px 0 4px 0;">'
        f'Filter Live {"(typing-live)" if _LIVE else "(press Enter)"} — '
        f'SAP / Material Code / Description / Category</div>',
        unsafe_allow_html=True,
    )
    if _LIVE:
        global_needle = (_live_input(
            label="Search across all columns",
            key="_dash_keyup_global",
            placeholder="🔎 Search across SAP, Material Code, Description, Category",
            debounce=180,
            label_visibility="collapsed",
        ) or "").strip()
    else:
        global_needle = st.text_input(
            "Search across all columns",
            key="_dash_filter_global",
            label_visibility="collapsed",
            placeholder="🔎 Search across SAP, Material Code, Description, Category",
        ).strip()

    filters: dict[str, str] = {}
    with st.expander(
        "Advanced — filter per column",
        expanded=False,
    ):
        filter_cols = st.columns(len(columns))
        for col_widget, col_name in zip(filter_cols, columns):
            with col_widget:
                if col_name not in LIVE_FILTER_COLS:
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    continue
                if _LIVE:
                    val = _live_input(
                        label=col_name,
                        key=f"_dash_keyup_{col_name}",
                        placeholder=col_name,
                        debounce=180,
                        label_visibility="collapsed",
                    )
                else:
                    val = st.text_input(
                        col_name, key=f"_dash_filter_{col_name}",
                        label_visibility="collapsed", placeholder=col_name,
                    )
                filters[col_name] = (val or "").strip()

    # ── 2. Apply filters (case-insensitive substring per column) ──────────
    view = df.copy()
    # Global search first: OR-match across all four searchable columns.
    if global_needle:
        cols_to_search = [c for c in LIVE_FILTER_COLS if c in view.columns]
        if cols_to_search:
            mask = False
            for c in cols_to_search:
                m = view[c].astype(str).str.contains(
                    global_needle, case=False, na=False, regex=False,
                )
                mask = m if mask is False else (mask | m)
            view = view[mask]
    # Then per-column narrowing (AND across columns).
    for col_name, needle in filters.items():
        if needle and col_name in view.columns:
            view = view[
                view[col_name].astype(str).str.contains(
                    needle, case=False, na=False, regex=False,
                )
            ]

    if view.empty:
        st.caption("No rows match the current filters.")
        return

    # ── 3. Render rows ─────────────────────────────────────────────────────
    NUM_COLS = {"Opening_Stock", "Receipt", "Consumption", "Return",
                "Closing_Stock", "Minimum_Qty", "Unit_Cost", "Stock_Value"}

    rows_html = []
    for i, (_, r) in enumerate(view.iterrows()):
        bg = _C["surf2"] + "33" if i % 2 else "transparent"
        tds = []
        for c in columns:
            v = r.get(c)
            if c == "SAP_Code":
                tds.append(
                    f'<td style="padding:7px 10px;color:{_C["gold"]};'
                    f'font-family:monospace;font-weight:700;">{_cell(v)}</td>'
                )
            elif c == "Material_Code":
                tds.append(
                    f'<td style="padding:7px 10px;color:{_C["muted"]};'
                    f'font-family:monospace;font-size:11.5px;">{_cell(v)}</td>'
                )
            elif c == "Equipment_Description":
                tds.append(
                    f'<td style="padding:7px 10px;color:{_C["text"]};">'
                    f'{_cell(v)}</td>'
                )
            elif c == "Status":
                tds.append(
                    f'<td style="padding:7px 10px;">{_status_pill(str(v))}</td>'
                )
            elif c in NUM_COLS:
                # Right-aligned monospace numbers
                tds.append(
                    f'<td style="padding:7px 10px;color:{_C["text"]};'
                    f'font-family:monospace;text-align:right;">{_cell(v)}</td>'
                )
            else:
                tds.append(
                    f'<td style="padding:7px 10px;color:{_C["muted"]};'
                    f'font-size:12px;">{_cell(v)}</td>'
                )
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            + "".join(tds) + "</tr>"
        )

    head = "".join(
        f'<th style="padding:8px 10px;color:{_C["muted"]};font-weight:600;'
        f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
        f'text-align:{"right" if c in NUM_COLS else "left"};'
        f'white-space:nowrap;">{_html.escape(c)}</th>'
        for c in columns
    )
    st.markdown(
        f'<div style="overflow-x:auto;border-radius:8px;'
        f'border:1px solid {_C["border"]};margin-top:6px;">'
        f'<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
        f'<thead><tr style="background:{_C["surf2"]};'
        f'border-bottom:1px solid {_C["border"]};">{head}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    st.caption(f"{len(view):,} of {len(df):,} item(s)")


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


def _render_kpi_drilldown_panel(
    which: str,
    live_df,
    low_df,
    expiry_df,
    total_value: float,
) -> None:
    """Round 15 — inline drill-down for each KPI card. Renders a focused
    table + a one-line takeaway. Caller toggles via `_live_kpi_drilldown`
    in session_state."""
    if which == "catalogue":
        st.markdown("##### 📦 All catalogue items")
        cols = [c for c in [
            "SAP_Code", "Material_Code", "Equipment_Description", "UOM",
            "Category", "Current_Stock",
        ] if c in live_df.columns]
        st.dataframe(
            live_df[cols].head(500),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            f"Showing {min(500, len(live_df)):,} of {len(live_df):,} items. "
            f"Use the catalogue filters below for deeper search."
        )
        return

    if which == "value":
        st.markdown("##### 💰 Top-value items (by Stock_Value)")
        val_df = cached_inventory_valuation()
        if val_df is None or val_df.empty:
            st.caption("Set Unit_Cost in Admin → DB Editor to populate this view.")
            return
        cols = [c for c in [
            "SAP_Code", "Material_Code", "Equipment_Description", "UOM",
            "Current_Stock", "Unit_Cost", "Stock_Value",
        ] if c in val_df.columns]
        top = val_df.sort_values("Stock_Value", ascending=False).head(20)
        st.dataframe(top[cols], use_container_width=True, hide_index=True)
        share = (
            float(top["Stock_Value"].sum()) / float(val_df["Stock_Value"].sum())
            if float(val_df["Stock_Value"].sum() or 0) > 0 else 0
        )
        st.caption(
            f"Top 20 items represent {share:.1%} of total stock value "
            f"({format_sar(total_value)})."
        )
        return

    if which == "below_min":
        st.markdown("##### ⚠️ Items below minimum threshold")
        if low_df is None or low_df.empty:
            st.success("All items above Minimum_Qty — nothing to reorder.")
            return
        cols = [c for c in [
            "SAP_Code", "Material_Code", "Equipment_Description", "UOM",
            "Current_Stock", "Minimum_Qty", "Shortage",
        ] if c in low_df.columns]
        st.dataframe(low_df[cols], use_container_width=True, hide_index=True)
        st.caption(
            f"{len(low_df):,} item(s) below minimum. Reorder via the PR flow "
            f"in the HOD Portal."
        )
        return

    if which == "expiring":
        st.markdown("##### ⏳ Expiring or expired lots")
        if expiry_df is None or expiry_df.empty:
            st.success("Shelf-life clear — no items within the expiry window.")
            return
        cols = [c for c in [
            "SAP_Code", "Equipment_Description", "Lot_Number", "Expiry_Date",
            "Status", "Quantity",
        ] if c in expiry_df.columns]
        st.dataframe(expiry_df[cols], use_container_width=True, hide_index=True)
        st.caption(
            f"{len(expiry_df):,} item(s) require shelf-life review. "
            f"HOD Portal → Shelf-Life tab for actions."
        )
        return


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

    # Round 15 — KPI click-through. A row of subtle "Details" buttons under
    # the hero strip toggles an inline drill-down panel below. State lives
    # in st.session_state so the panel survives reruns and the same click
    # collapses it.
    _KPI_KEY = "_live_kpi_drilldown"
    active = st.session_state.get(_KPI_KEY)
    kpi_btn_cols = st.columns(4)
    kpi_labels = ("catalogue", "value", "below_min", "expiring")
    kpi_btn_labels = (
        f"🔎 All items ({total_items:,})",
        f"🔎 Stock value (SAR)",
        f"🔎 Below minimum ({low_count})",
        f"🔎 Expiring / expired ({expiry_count})",
    )
    for col, key, lbl in zip(kpi_btn_cols, kpi_labels, kpi_btn_labels):
        with col:
            is_active = (active == key)
            if st.button(
                ("✅ " if is_active else "") + lbl,
                key=f"_live_kpi_btn_{key}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                # Same-click → collapse; different-click → swap.
                st.session_state[_KPI_KEY] = None if is_active else key
                st.rerun()

    if active:
        _render_kpi_drilldown_panel(
            active, live_df, low_df, expiry_df, total_value,
        )

    if AI_ENABLED:
        _render_nl_search_panel()

    render_burn_alert_banner(forecast_df)
    st.divider()

    # Rename to the friendlier labels the user requested while keeping
    # backend column names intact for filters/sorts.
    df_display = live_df.copy()
    df_display = df_display.rename(columns={
        "Total_Received": "Receipt",
        "Total_Consumed": "Consumption",
        "Total_Returned": "Return",
        "Current_Stock":  "Closing_Stock",
    })

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

    # Final column order per 2026-06 spec.
    desired_order = [
        "SAP_Code", "Material_Code", "Equipment_Description", "UOM",
        "Opening_Stock", "Receipt", "Consumption", "Return", "Closing_Stock",
        "Minimum_Qty", "Unit_Cost", "Stock_Value", "Category",
    ]
    display_cols = [c for c in desired_order if c in df_display.columns]
    df_display = df_display[display_cols + [
        c for c in df_display.columns if c not in display_cols
    ]]

    # Computed STATUS column for the pill cell.
    def _status_label(row) -> str:
        s = float(row.get("Closing_Stock", row.get("Current_Stock", 0)) or 0)
        m = float(row.get("Minimum_Qty", 0) or 0)
        if s <= 0:        return "Empty"
        if m > 0 and s < m:         return "Below Min"
        if m > 0 and s < m * 1.25:  return "Low"
        return "OK"

    df_display["Status"] = df_display.apply(_status_label, axis=1)

    # Final column list — Status pushed to the right.
    columns_to_show = [c for c in display_cols if c in df_display.columns] + ["Status"]
    _render_dashboard_html_table(df_display, columns_to_show)

    st.divider()
    with st.expander("📉 Stock vs Minimum Threshold", expanded=False):
        render_stock_vs_minimum_bar(live_df)

    with st.expander("🔥 Burn Rate Forecast (30-Day)", expanded=True):
        render_burn_rate_chart(forecast_df)

    with st.expander("📊 Top Consumed Items", expanded=False):
        render_top_consumed_bar(live_df)
