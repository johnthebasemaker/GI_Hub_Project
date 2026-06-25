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
    c1.metric("Distinct materials", len(total))
    c2.metric("Materials with gap",
              int((total["Gap_Qty"] > 0).sum()))
    c3.metric("Fully covered",
              int((total["Coverage_Pct"] >= 100).sum()))

    st.dataframe(
        total.sort_values("Gap_Qty", ascending=False),
        use_container_width=True, hide_index=True,
    )
    sme_download_pair(
        total, report_name="Total_Overview",
        title="Total Overview", key="total_overview",
        site_id=site_id, sheet_name="Total",
    )
