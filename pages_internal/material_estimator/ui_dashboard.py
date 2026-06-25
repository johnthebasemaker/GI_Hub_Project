"""Tab 0 — Project Overview + Material Requirement & Procurement."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
from .downloads import sme_download_pair  # raw .xlsx (no popover) + PDF popover


def _kpi(label: str, value, help_text: str | None = None) -> None:
    st.metric(label, value, help=help_text)


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    view = st.radio(
        "View",
        ["📊 Project Overview", "📦 Material Requirement & Procurement"],
        horizontal=True, key="_sme_dash_view",
    )

    alloc, feas, inv = engine_runner.run_allocation(site_id, priority_order)
    inv_view = data_layer.load_inventory_view(site_id)
    equip = data_layer.load_equipment(site_id)

    if view == "📊 Project Overview":
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _kpi("Equipment items", int(equip["Equipment_Tag_No."].nunique())
                 if not equip.empty else 0)
        with c2:
            ready = int((feas["Status"].str.contains("Fully Ready", na=False)).sum()) \
                if not feas.empty else 0
            _kpi("Fully ready", ready)
        with c3:
            blocked = int((feas["Status"].str.contains("Blocked", na=False)).sum()) \
                if not feas.empty else 0
            _kpi("Blocked", blocked, "0% on at least one material")
        with c4:
            total_sqm = (
                float(equip["Surface_Area_SQM"].sum()) if not equip.empty else 0.0
            )
            _kpi("Remaining SQM", f"{total_sqm:,.1f}",
                 "Sum of Original_SQM − Done_SQM across all tags")
        st.markdown("---")

        if feas.empty:
            st.info(
                "No equipment + recipe data yet. Run "
                "`python3 scripts/sme_bootstrap.py --site-id <site>` "
                "or use the Master Data tab to add entries."
            )
            return

        st.subheader("Feasibility by priority order")
        st.dataframe(
            feas[[
                "Priority_Rank", "Equipment_Tag_No.", "Name",
                "Completion_Pct", "Status",
                "Bottleneck_Material_Code", "Bottleneck_Material_Name",
                "Bottleneck_Shortfall",
            ]],
            use_container_width=True, hide_index=True,
        )
        sme_download_pair(
            feas, report_name="Project_Overview",
            title="Project Overview", key="dash_overview",
            site_id=site_id, sheet_name="Overview",
        )

    else:  # Procurement view
        proc = engine_runner.procurement_list(site_id, priority_order)
        if proc.empty:
            st.success("✅ No shortages — inventory covers the full plan.")
            return
        st.subheader("Materials short for the current plan")
        st.caption(
            "Shortage = engine shortfall after Available stock. "
            "Net Shortfall = Shortage − Ordered (open POs)."
        )
        st.dataframe(proc, use_container_width=True, hide_index=True)
        sme_download_pair(
            proc, report_name="Procurement_List",
            title="Procurement List", key="dash_proc",
            site_id=site_id, sheet_name="Procurement",
        )

        # Stock-only materials (carry inventory the plan doesn't touch)
        if not inv_view.empty:
            in_plan = set(alloc["Material_Code"]) if not alloc.empty else set()
            stock_only = inv_view[~inv_view["Material_Code"].isin(in_plan)]
            stock_only = stock_only[stock_only["Available_Qty"] > 0]
            if not stock_only.empty:
                with st.expander(
                    f"📦 Stock-only materials ({len(stock_only)}) — present "
                    "in inventory but not in any active recipe", expanded=False,
                ):
                    st.dataframe(stock_only, use_container_width=True, hide_index=True)
