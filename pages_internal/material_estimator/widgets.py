"""widgets.py — UI parity helpers ported from SME.

Helpers:
- dbl_click_metric:    KPI card with click-to-expand drilldown.
- plotly_mat_table:    Color-coded per-material allocation table (Plotly).
- status_dot:          Inline ● colored by completion %.
- fulfil_pill:         Inline pill colored by completion bucket.
- loc_badge:           Location chip.
- days_of_continuation_block: post-Submit-Batch runway report (Phase 4 hook).

All colors map to the ERP yellow/amber palette; no SME-specific dark/light
toggle (ERP has its own theming).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False


_ERP_AMBER = "#FBBF24"
_ERP_AMBER_LIGHT = "#FEF3C7"
_ERP_BORDER = "#D97706"
_ERP_TEXT = "#1F2937"


# ---------------------------------------------------------------------------
# Inline HTML pills
# ---------------------------------------------------------------------------

def status_dot(pct: float) -> str:
    """Colored ● dot based on completion %."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    if p >= 100:
        color = "#10B981"  # green
    elif p >= 90:
        color = "#F97316"  # orange
    elif p >= 80:
        color = "#EAB308"  # yellow
    else:
        color = "#EF4444"  # red
    return f'<span style="color:{color};font-size:18px;line-height:1">●</span>'


def fulfil_pill(pct: float) -> str:
    """Inline pill: '🟢 100%' / '🟡 87.5%' / '🔴 0%' colored by bucket."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    if p >= 100:
        emoji, bg, fg = "🟢", "#D1FAE5", "#065F46"
    elif p >= 90:
        emoji, bg, fg = "🟠", "#FFEDD5", "#9A3412"
    elif p >= 80:
        emoji, bg, fg = "🟡", "#FEF3C7", "#92400E"
    else:
        emoji, bg, fg = "🔴", "#FEE2E2", "#991B1B"
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:12px;'
        f'background:{bg};color:{fg};font-size:12px;font-weight:600">'
        f'{emoji} {p:.1f}%</span>'
    )


def loc_badge(loc: str) -> str:
    safe = (loc or "—").replace("<", "").replace(">", "")
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:8px;'
        f'background:{_ERP_AMBER_LIGHT};color:{_ERP_TEXT};'
        f'font-size:11px;font-weight:600;border:1px solid {_ERP_BORDER}">'
        f'📍 {safe}</span>'
    )


# ---------------------------------------------------------------------------
# KPI tile with click-to-expand drilldown
# ---------------------------------------------------------------------------

def dbl_click_metric(
    label: str,
    value: str,
    state_key: str,
    *,
    drilldown_title: str = "",
    drilldown_df: pd.DataFrame | None = None,
    help_text: str = "",
    delta: str = "",
) -> None:
    """KPI card as a popover. Click → opens drilldown table.

    Mirrors the SME `dbl_click_metric` UX exactly:
    - Bold label on top line, big value on second line, optional delta on third.
    - Click opens a popover with the drilldown DataFrame (capped at 200 rows).
    - 'No detail data' fallback when drilldown_df is empty/None.
    """
    btn_label = f"**{label}**\n\n{value}"
    if delta:
        btn_label += f"\n\n{delta}"
    with st.popover(btn_label, use_container_width=True):
        if drilldown_title:
            st.subheader(drilldown_title)
        if help_text:
            st.caption(help_text)
        _df = drilldown_df if drilldown_df is not None else pd.DataFrame()
        if len(_df):
            _MAX = 200
            total = len(_df)
            if total > _MAX:
                st.caption(f"Showing top {_MAX:,} of {total:,} rows")
                _df = _df.head(_MAX)
            st.dataframe(
                _df, use_container_width=True, hide_index=True,
                height=min(35 * (len(_df) + 1) + 3, 420),
            )
        else:
            st.info("No detail data available for this metric.")


# ---------------------------------------------------------------------------
# Plotly material table
# ---------------------------------------------------------------------------

def plotly_mat_table(
    df: pd.DataFrame,
    key_suffix: str,
    *,
    height: int = 380,
    allocated_label: str = "Allocated",
) -> None:
    """Colour-coded material allocation table. Renders via styled DataFrame
    (no Plotly dep required — falls back gracefully)."""
    if df is None or df.empty:
        st.info("No materials to display.")
        return
    base_cols = ["Material_Code", "Material_Name", "UOM",
                 "Demand_Qty", "Allocated_Qty",
                 "Shortfall_Qty", "Fulfillment_Rate"]
    avail = [c for c in base_cols if c in df.columns]
    df2 = df[avail].copy()
    if "Ordered_Qty" in df.columns:
        try:
            insert_at = df2.columns.get_loc("Allocated_Qty")
        except KeyError:
            insert_at = len(df2.columns)
        df2.insert(insert_at, "Ordered_Qty", df["Ordered_Qty"])

    rename = {
        "Material_Code": "Code",
        "Material_Name": "Material Name",
        "Demand_Qty": "Demand",
        "Ordered_Qty": "On Order",
        "Allocated_Qty": allocated_label,
        "Shortfall_Qty": "Shortfall",
        "Fulfillment_Rate": "Fulfil %",
    }
    df2 = df2.rename(columns=rename)
    # Convert Fulfil % from 0-1 → percent
    if "Fulfil %" in df2.columns and df2["Fulfil %"].max() <= 1.5:
        df2["Fulfil %"] = (df2["Fulfil %"] * 100).round(1)

    fmt = {
        "Demand":         "{:,.3f}",
        allocated_label:  "{:,.3f}",
        "Shortfall":      "{:,.3f}",
        "Fulfil %":       "{:.1f}%",
    }
    if "On Order" in df2.columns:
        fmt["On Order"] = "{:,.3f}"

    def style_row(row):
        pct = row.get("Fulfil %", 100)
        try:
            p = float(pct)
        except (TypeError, ValueError):
            p = 100.0
        if p >= 100:
            bg, tc = "rgba(16,185,129,0.12)", "#10B981"
        elif p >= 90:
            bg, tc = "rgba(249,115,22,0.12)", "#F97316"
        elif p >= 80:
            bg, tc = "rgba(234,179,8,0.12)", "#EAB308"
        else:
            bg, tc = "rgba(239,68,68,0.12)", "#EF4444"
        styles = [f"background-color:{bg}"] * len(row)
        if "Fulfil %" in row.index:
            idx = list(row.index).index("Fulfil %")
            styles[idx] = (
                f"background-color:{bg};color:{tc};font-weight:700"
            )
        return styles

    styled = df2.style.apply(style_row, axis=1).format(fmt)
    st.dataframe(
        styled, use_container_width=True, hide_index=True,
        height=height, key=f"_sme_mat_tbl_{key_suffix}",
    )


# ---------------------------------------------------------------------------
# Days-of-Continuation runway report (post-Submit-Batch — wired in Phase 4)
# ---------------------------------------------------------------------------

def days_of_continuation_block(
    *,
    daily_consumption_per_material: dict[str, float],
    inventory_view: pd.DataFrame,
) -> None:
    """Inline runway block: how many days of production each material
    sustains at the just-submitted batch's rate.

    daily_consumption_per_material = {material_code: qty_in_this_batch}
    inventory_view must carry Material_Code, Available_Qty, Ordered_Qty,
    Material_Name, UOM.
    """
    if not daily_consumption_per_material or inventory_view is None or inventory_view.empty:
        return

    rows = []
    for mc, qty in daily_consumption_per_material.items():
        if qty <= 0:
            continue
        inv_row = inventory_view[inventory_view["Material_Code"] == mc]
        if inv_row.empty:
            avail = 0.0
            ord_q = 0.0
            name = mc
            uom = ""
        else:
            avail = float(inv_row.iloc[0].get("Available_Qty") or 0)
            ord_q = float(inv_row.iloc[0].get("Ordered_Qty") or 0)
            name = inv_row.iloc[0].get("Material_Name") or mc
            uom = inv_row.iloc[0].get("UOM") or ""
        days = avail / qty if qty else float("inf")
        days_with_po = (avail + ord_q) / qty if qty else float("inf")
        rows.append({
            "Material_Code": mc,
            "Material": name,
            "UOM": uom,
            "Daily burn": round(qty, 3),
            "Available": round(avail, 3),
            "On order": round(ord_q, 3),
            "Days remaining": (
                "∞" if days == float("inf") else round(days, 1)
            ),
            "Days w/ open PO": (
                "∞" if days_with_po == float("inf") else round(days_with_po, 1)
            ),
            "_sort_days": days if days != float("inf") else 10**9,
        })
    if not rows:
        return
    df = pd.DataFrame(rows).sort_values("_sort_days").drop(columns=["_sort_days"])

    finite_days = [
        r["_sort_days"] if isinstance(r.get("_sort_days"), (int, float)) else None
        for r in rows
    ]
    finite_days = [d for d in finite_days if d is not None and d < 10**8]
    bottleneck = min(finite_days) if finite_days else None

    st.markdown("### 📆 Days of Continuation")
    if bottleneck is not None:
        bn_row = df.iloc[0]
        st.warning(
            f"Available stock will sustain this output for "
            f"**{bottleneck:.1f} day(s)** before the first material "
            f"(**{bn_row['Material']}**) runs out."
        )

    def style_runway(row):
        try:
            d = float(row["Days remaining"]) if row["Days remaining"] != "∞" else 999
        except (TypeError, ValueError):
            d = 999
        if d < 3:
            bg = "rgba(239,68,68,0.18)"
        elif d < 7:
            bg = "rgba(234,179,8,0.18)"
        else:
            bg = "rgba(16,185,129,0.10)"
        return [f"background-color:{bg}"] * len(row)

    styled = df.style.apply(style_runway, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)
