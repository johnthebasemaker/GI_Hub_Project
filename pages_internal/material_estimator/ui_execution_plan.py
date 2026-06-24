"""Tab 5 — Execution Plan.

READ-ONLY in the merged ERP. Three sub-views:
  ⚙️  Execution Plan        — sequential day-by-day build (uses allocation).
  📋 Progress List          — Original_SQM / Done_SQM / Remaining per tag.
  📊 Consumption Comparison — expected vs ACTUAL from ERP consumption ledger.

No consumption-submit form. No Days-of-Continuation block. The ERP's
EOD-commit pipeline is the only path that can write to consumption.
"""
from __future__ import annotations

import datetime as _dt

import pandas as pd
import streamlit as st

import database as D

from . import data_layer, engine_runner


def _render_execution_plan(site_id: str | None, priority_order: list[str]) -> None:
    alloc, feas, _ = engine_runner.run_allocation(site_id, priority_order)
    if feas.empty:
        st.info("Run the allocation engine first (load equipment + recipes).")
        return
    st.caption(
        "Equipment listed in build order. ✅ rows can be started today; "
        "🟡 rows can be partially built; 🔴 rows are fully blocked."
    )
    for _, row in feas.iterrows():
        tag = row["Equipment_Tag_No."]
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            c1.markdown(
                f"**{row['Priority_Rank']:>2}. {tag} — {row['Name']}**  "
                f"\n_{row['Status']}_"
            )
            c2.metric("Completion", f"{row['Completion_Pct']:.1f}%")
            c3.metric("Demand qty", f"{row['Total_Demand_Qty']:,.1f}")
            mat = alloc[alloc["Equipment_Tag_No."] == tag]
            short = mat[mat["Shortfall_Qty"] > 0]
            if not short.empty:
                st.markdown("**Shortfalls:**")
                st.dataframe(
                    short[["Material_Code", "Material_Name",
                           "Demand_Qty", "Allocated_Qty", "Shortfall_Qty"]],
                    use_container_width=True, hide_index=True,
                )


def _render_progress_list(site_id: str | None) -> None:
    prog = data_layer.load_sqm_progress(site_id)
    if prog.empty:
        st.info("No progress records yet.")
        return
    prog = prog.copy()
    prog["Remaining_SQM"] = (prog["Original_SQM"] - prog["Done_SQM"]).clip(lower=0)
    prog["Progress_Pct"] = (
        prog["Done_SQM"] / prog["Original_SQM"].replace(0, pd.NA) * 100
    ).round(1).fillna(0)
    st.dataframe(
        prog.sort_values(["Equipment_Tag_No.", "Lining_System_Code"]),
        use_container_width=True, hide_index=True,
    )


def _render_consumption_comparison(
    site_id: str | None,
    priority_order: list[str],
) -> None:
    """Expected (from engine) vs Actual (from ERP `consumption` ledger).
    Joins on Material_Code. Pure read; no writes."""
    alloc, _, _ = engine_runner.run_allocation(site_id, priority_order)
    if alloc.empty:
        st.info("No allocation to compare.")
        return

    today = _dt.date.today()
    default_start = today - _dt.timedelta(days=30)
    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input("From", value=default_start, key="_sme_cc_from")
    with c2:
        end = st.date_input("To", value=today, key="_sme_cc_to")

    conn = D.get_connection()
    try:
        params: list = [start.isoformat(), end.isoformat()]
        site_pred = ""
        if site_id:
            site_pred = " AND Site_ID = ?"
            params.append(site_id)
        actual = pd.read_sql(
            "SELECT i.Material_Code, "
            "       SUM(c.Quantity) AS Actual_Consumed "
            "FROM consumption c "
            "LEFT JOIN inventory i ON i.SAP_Code = c.SAP_Code "
            f"WHERE DATE(c.Date) BETWEEN ? AND ?{site_pred} "
            "GROUP BY i.Material_Code",
            conn, params=tuple(params),
        )
    finally:
        conn.close()

    if actual.empty:
        st.info(
            f"No ERP consumption ledger rows between {start} and {end} "
            f"for this site."
        )
        return

    expected = alloc.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    )["Allocated_Qty"].sum().rename(columns={"Allocated_Qty": "Expected_Qty"})
    comp = expected.merge(actual, on="Material_Code", how="left")
    comp["Actual_Consumed"] = comp["Actual_Consumed"].fillna(0.0)
    comp["Variance_Qty"] = (comp["Actual_Consumed"] - comp["Expected_Qty"]).round(3)
    comp["Variance_Pct"] = (
        comp["Variance_Qty"] / comp["Expected_Qty"].replace(0, pd.NA) * 100
    ).round(1).fillna(0)

    st.caption(
        "Negative variance = under-consumed (under plan). "
        "Positive variance = over-consumed (above plan). Source: ERP `consumption`."
    )
    st.dataframe(comp, use_container_width=True, hide_index=True)


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    sub = st.radio(
        "Sub-view",
        ["⚙️ Execution Plan", "📋 Progress List", "📊 Consumption Comparison"],
        horizontal=True, key="_sme_exec_sub",
    )
    if sub == "⚙️ Execution Plan":
        _render_execution_plan(site_id, priority_order)
    elif sub == "📋 Progress List":
        _render_progress_list(site_id)
    else:
        _render_consumption_comparison(site_id, priority_order)
