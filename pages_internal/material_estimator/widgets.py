"""widgets.py — UI parity helpers ported from SME (R19 expanded).

Adds show_sqm support to plotly_mat_table, plus loc_badge tuned to the
SME's three named locations (Brown Field / TRAIN J / TRAIN K).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False


_LOC_BG = {
    "Brown Field": ("#1E5799", "#FFFFFF"),
    "TRAIN J":     ("#A0620A", "#FFFFFF"),
    "TRAIN K":     ("#1A6B48", "#FFFFFF"),
}


# ---------------------------------------------------------------------------
# Inline HTML pills
# ---------------------------------------------------------------------------

def status_dot(pct: float) -> str:
    try:
        p = float(pct)
    except (TypeError, ValueError):
        p = 0.0
    if p >= 100:
        color = "#10B981"
    elif p >= 90:
        color = "#F97316"
    elif p >= 80:
        color = "#EAB308"
    else:
        color = "#EF4444"
    return f'<span style="color:{color};font-size:18px;line-height:1">●</span>'


def fulfil_pill(pct: float) -> str:
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
    bg, fg = _LOC_BG.get(loc, ("#374151", "#FFFFFF"))
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:8px;'
        f'background:{bg};color:{fg};font-size:11px;font-weight:700;">'
        f'📍 {safe}</span>'
    )


def code_chip(code: str | int, short_name: str = "") -> str:
    label = f"Code {code}" + (f" · {short_name}" if short_name else "")
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:8px;'
        f'background:#312E81;color:#FFFFFF;font-size:11px;font-weight:700;">'
        f'{label}</span>'
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
    height: int = 95,
) -> None:
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
# Plotly material table — supports show_sqm per SME spec
# ---------------------------------------------------------------------------

def plotly_mat_table(
    df: pd.DataFrame,
    key_suffix: str,
    *,
    height: int = 380,
    show_sqm: bool = False,
    tag: str = "",
    code: str = "",
    allocated_label: str = "Allocated",
    total_sqm_for_sc: float | None = None,
) -> None:
    """SME-spec colored material table. With show_sqm=True, adds 4 SQM
    columns derived from each material's own fulfillment rate."""
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
        "Material_Code":    "Code",
        "Material_Name":    "Material Name",
        "Demand_Qty":       "Demand",
        "Ordered_Qty":      "On Order",
        "Allocated_Qty":    allocated_label,
        "Shortfall_Qty":    "Shortfall",
        "Fulfillment_Rate": "Fulfil %",
    }
    df2 = df2.rename(columns=rename)
    # Normalize Fulfil % into 0-100 range
    if "Fulfil %" in df2.columns and not df2.empty:
        try:
            if df2["Fulfil %"].max() <= 1.5:
                df2["Fulfil %"] = (df2["Fulfil %"] * 100).round(1)
        except Exception:
            pass

    # SME show_sqm: 4 SQM columns derived per-material from fulfillment
    if show_sqm and tag and code and total_sqm_for_sc:
        total = float(total_sqm_for_sc)
        if "Fulfil %" in df2.columns:
            ratio = df2["Fulfil %"].astype(float) / 100.0
        else:
            ratio = pd.Series([1.0] * len(df2))
        ratio = ratio.clip(0, 1)
        df2["SQM Total"]   = round(total, 2)
        df2["SQM Done"]    = (total * ratio).round(2)
        df2["SQM Deficit"] = (total * (1 - ratio)).round(2)
        df2["SQM Done %"]  = (ratio * 100).round(1)

    fmt = {
        "Demand":         "{:,.3f}",
        allocated_label:  "{:,.3f}",
        "Shortfall":      "{:,.3f}",
        "Fulfil %":       "{:.1f}%",
    }
    if "On Order" in df2.columns:
        fmt["On Order"] = "{:,.3f}"
    if "SQM Total" in df2.columns:
        fmt.update({"SQM Total": "{:,.2f}", "SQM Done": "{:,.2f}",
                    "SQM Deficit": "{:,.2f}", "SQM Done %": "{:.1f}%"})

    def style_row(row):
        try:
            p = float(row.get("Fulfil %", 100))
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
# Days-of-Continuation post-Submit-Batch
# ---------------------------------------------------------------------------

def days_of_continuation_block(
    *,
    daily_consumption_per_material: dict[str, float],
    inventory_view: pd.DataFrame,
) -> None:
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
            "Days remaining": ("∞" if days == float("inf") else round(days, 1)),
            "Days w/ open PO": (
                "∞" if days_with_po == float("inf") else round(days_with_po, 1)
            ),
            "_sort": days if days != float("inf") else 10**9,
        })
    if not rows:
        return
    df = pd.DataFrame(rows).sort_values("_sort").drop(columns=["_sort"])
    finite = [r["_sort"] if False else None for r in rows]  # safe-ish
    try:
        finite = [(r.get("_sort") if isinstance(r, dict) else None) for r in rows]
        finite = [d for d in finite if d is not None and d < 10**8]
    except Exception:
        finite = []

    st.markdown("### 📆 Days of Continuation")
    if finite:
        bn = min(finite)
        bn_row = df.iloc[0]
        st.warning(
            f"Available stock will sustain this output for "
            f"**{bn:.1f} day(s)** before the first material "
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
