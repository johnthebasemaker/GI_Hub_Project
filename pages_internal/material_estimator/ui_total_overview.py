"""Tab 7 — Total Overview.

Project-wide roll-up — independent of priority order. Aggregates total
demand vs total available across every equipment item and every material.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .downloads import sme_download_pair
from .widgets import dbl_click_metric, plotly_mat_table


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip, recipe, inv, _ = data_layer.build_estimator_inputs(site_id)
    if equip.empty or recipe.empty:
        st.info("Need equipment + recipe master to compute totals.")
        return

    demand, inv_clean = AE.build_demand_matrix(equip, recipe, inv)
    if demand.empty:
        st.info("No demand rows — recipes don't match any equipment.")
        return

    total = demand.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    )["Demand_Qty"].sum()
    total = total.merge(
        inv_clean[["Material_Code", "Available_Qty"]],
        on="Material_Code", how="left",
    )
    total["Available_Qty"] = total["Available_Qty"].fillna(0.0)
    if "Ordered_Qty" in inv.columns:
        total = total.merge(
            inv[["Material_Code", "Ordered_Qty"]],
            on="Material_Code", how="left",
        )
        total["Ordered_Qty"] = total["Ordered_Qty"].fillna(0.0)
    else:
        total["Ordered_Qty"] = 0.0
    total["Gap_Qty"] = (
        total["Demand_Qty"] - total["Available_Qty"] - total["Ordered_Qty"]
    ).clip(lower=0).round(3)
    total["Coverage_Pct"] = (
        (total["Available_Qty"] + total["Ordered_Qty"])
        / total["Demand_Qty"].replace(0, pd.NA) * 100
    ).clip(0, 100).round(1).fillna(100)

    c1, c2, c3 = st.columns(3)
    with c1:
        dbl_click_metric("Distinct materials", str(len(total)), "to_mat",
                         drilldown_title="All materials in plan",
                         drilldown_df=total)
    with c2:
        gap_df = total[total["Gap_Qty"] > 0]
        dbl_click_metric("With gap", str(len(gap_df)), "to_gap",
                         drilldown_title="Materials with positive net gap",
                         drilldown_df=gap_df,
                         help_text="Demand exceeds Available + Ordered.")
    with c3:
        cov_df = total[total["Coverage_Pct"] >= 100]
        dbl_click_metric("Fully covered", str(len(cov_df)), "to_cov",
                         drilldown_title="Materials with full coverage",
                         drilldown_df=cov_df)

    # Plotly-styled coverage table
    pretty = total.rename(columns={
        "Demand_Qty": "Demand_Qty",
        "Available_Qty": "Allocated_Qty",  # so plotly_mat_table colors the row
    }).copy()
    pretty["Shortfall_Qty"] = pretty["Gap_Qty"]
    pretty["Fulfillment_Rate"] = pretty["Coverage_Pct"] / 100
    plotly_mat_table(
        pretty.sort_values("Gap_Qty", ascending=False),
        key_suffix="to_main", height=420,
        allocated_label="Available",
    )
    sme_download_pair(
        total, report_name="Total_Overview",
        title="Total Overview", key="total_overview",
        site_id=site_id, sheet_name="Total",
    )
