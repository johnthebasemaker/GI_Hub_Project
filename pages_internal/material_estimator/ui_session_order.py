"""Tab 2 — Session Order Report.

Computed picture for the current priority order: per-equipment fulfillment,
material allocations, and the suggestion engine's best pause scenario.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .downloads import sme_secure_multi_sheet_xlsx_download, sme_secure_pdf_download
from .theming import status_pill


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    alloc, feas, inv = engine_runner.run_allocation(site_id, priority_order)
    if alloc.empty:
        st.info("No allocation computed — load equipment and recipe data first.")
        return

    st.subheader(f"Plan summary — {len(feas)} equipment items")

    summary = feas[[
        "Priority_Rank", "Equipment_Tag_No.", "Name",
        "Total_Demand_Qty", "Total_Allocated_Qty", "Total_Shortfall_Qty",
        "Completion_Pct", "Status",
    ]].copy()
    summary["Completion_Pct"] = summary["Completion_Pct"].round(1)
    st.dataframe(summary, use_container_width=True, hide_index=True)

    sheets = [
        {"name": "Plan Summary", "df": summary},
        {"name": "Per-Material Allocation",
         "df": alloc[[
             "Priority_Rank", "Equipment_Tag_No.", "Name", "Material_Code",
             "Material_Name", "UOM", "Demand_Qty", "Allocated_Qty",
             "Shortfall_Qty", "Fulfillment_Rate",
         ]]},
    ]
    c1, c2 = st.columns(2)
    with c1:
        sme_secure_multi_sheet_xlsx_download(
            "⬇ Excel — Session Order Report",
            sheets=sheets,
            file_stem="SME_Session_Order",
            key="session_order_xlsx", username=username,
        )
    with c2:
        sme_secure_pdf_download(
            "⬇ PDF — Session Order Report",
            sheets=sheets,
            file_stem="SME_Session_Order",
            key="session_order_pdf", username=username,
            title="SME Session Order Report",
        )

    st.markdown("---")
    st.subheader("Per-equipment material breakdown")
    for _, row in feas.iterrows():
        tag = row["Equipment_Tag_No."]
        with st.expander(
            f"{row['Priority_Rank']:>2}. {tag} — {row['Name']}  "
            f"({row['Completion_Pct']:.1f}%)",
            expanded=False,
        ):
            st.markdown(status_pill(row["Status"]), unsafe_allow_html=True)
            mat = alloc[alloc["Equipment_Tag_No."] == tag][[
                "Material_Code", "Material_Name", "UOM",
                "Demand_Qty", "Allocated_Qty", "Shortfall_Qty",
                "Fulfillment_Rate",
            ]].copy()
            mat["Fulfillment_Rate"] = (mat["Fulfillment_Rate"] * 100).round(1)
            st.dataframe(mat, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Suggestion engine — best pause scenario")
    if st.button("🔮 Run suggestion engine", key="_sme_sugg_run"):
        equip, recipe, inv_view, _ = data_layer.build_estimator_inputs(site_id)
        demand, inv_clean = AE.build_demand_matrix(equip, recipe, inv_view)
        sugg_df, best = AE.run_suggestion_engine(demand, inv_clean, priority_order)
        if sugg_df.empty:
            st.info("Nothing to suggest — every tag is already fully ready.")
        else:
            st.dataframe(
                sugg_df.head(10), use_container_width=True, hide_index=True,
            )
            if not best.empty:
                rec = sugg_df[sugg_df["Recommended"]].iloc[0]
                st.success(
                    f"⭐ Best: pause **{rec['Pause_Tag']}** — unlocks "
                    f"{rec['Newly_Completable_Count']} fully completable tag(s)."
                )
