"""Tab 0 — Project Overview + Material Requirement & Procurement.

8-card KPI strip ported from SME, every tile click-through to a drilldown
via dbl_click_metric.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
from .downloads import sme_download_pair
from .widgets import dbl_click_metric, fulfil_pill, plotly_mat_table


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    view = st.radio(
        "View",
        ["📊 Project Overview", "📦 Material Requirement & Procurement"],
        horizontal=True, key="_sme_dash_view",
    )

    alloc, feas, inv_clean = engine_runner.run_allocation(site_id, priority_order)
    inv_view = data_layer.load_inventory_view(site_id)
    equip = data_layer.load_equipment(site_id)
    prog = data_layer.load_sqm_progress(site_id)

    if view == "📊 Project Overview":
        # ── Derived KPI inputs (mirror SME's Project Overview math) ──────
        equip_count = int(equip["Equipment_Tag_No."].nunique()) if not equip.empty else 0
        total_sqm = float(equip["Surface_Area_SQM"].sum()) if not equip.empty else 0.0

        if not alloc.empty:
            # Per-equipment available coverage = sum of allocated SQM contribution
            cov_by_eq = alloc.groupby("Equipment_Tag_No.")["Fulfillment_Rate"].mean()
            # Coverage SQM = sum(surface × min fulfillment per system) — proxy
            can_sqm = float((cov_by_eq * equip.groupby("Equipment_Tag_No.")["Surface_Area_SQM"].sum()).sum())
        else:
            can_sqm = 0.0
        short_sqm = max(0.0, total_sqm - can_sqm)
        overall_cov = (can_sqm / total_sqm * 100) if total_sqm else 100.0

        ready = int((feas["Status"].str.contains("Fully Ready", na=False)).sum()) if not feas.empty else 0
        blocked = int((feas["Status"].str.contains("Blocked", na=False)).sum()) if not feas.empty else 0
        partial = (len(feas) - ready - blocked) if not feas.empty else 0
        critical = int((feas["Completion_Pct"] < 50).sum()) if not feas.empty else 0

        # Drilldown dataframes
        feas_table = feas[[
            "Priority_Rank", "Equipment_Tag_No.", "Name",
            "Completion_Pct", "Status",
            "Bottleneck_Material_Code", "Bottleneck_Material_Name",
            "Bottleneck_Shortfall",
        ]] if not feas.empty else pd.DataFrame()
        ready_drill = feas_table[feas_table["Status"].str.contains("Fully Ready", na=False)] if not feas.empty else pd.DataFrame()
        blocked_drill = feas_table[feas_table["Status"].str.contains("Blocked", na=False)] if not feas.empty else pd.DataFrame()
        critical_drill = feas_table[feas_table["Completion_Pct"] < 50] if not feas.empty else pd.DataFrame()
        sqm_drill = equip[["Equipment_Tag_No.", "Name", "Location",
                           "Lining_System_Code", "Surface_Area_SQM"]] if not equip.empty else pd.DataFrame()

        # ── KPI strip — 4×2 grid matching SME ──────────────────────────────
        st.markdown("#### Project KPIs")
        r1 = st.columns(4)
        with r1[0]:
            dbl_click_metric("Equipment", str(equip_count), "k_eq",
                             drilldown_title="Equipment in plan",
                             drilldown_df=equip,
                             help_text="Unique equipment tags loaded for this site.")
        with r1[1]:
            dbl_click_metric("Total SQM", f"{total_sqm:,.1f}", "k_sqm",
                             drilldown_title="Per-equipment surface area",
                             drilldown_df=sqm_drill)
        with r1[2]:
            dbl_click_metric("Coverage SQM", f"{can_sqm:,.1f}", "k_cov",
                             drilldown_title="What current inventory can build",
                             drilldown_df=feas_table,
                             help_text="Σ(surface area × avg material fulfillment) per equipment.")
        with r1[3]:
            dbl_click_metric("SQM Deficit", f"{short_sqm:,.1f}", "k_def",
                             drilldown_title="Remaining SQM not covered by stock",
                             drilldown_df=feas_table[feas_table["Completion_Pct"] < 100] if not feas.empty else pd.DataFrame())
        r2 = st.columns(4)
        with r2[0]:
            dbl_click_metric("Overall Coverage", f"{overall_cov:.1f}%", "k_oc",
                             drilldown_title="Overall plan coverage",
                             drilldown_df=feas_table)
        with r2[1]:
            dbl_click_metric("Fully ready", str(ready), "k_ready",
                             drilldown_title="Tags ready to build today",
                             drilldown_df=ready_drill)
        with r2[2]:
            dbl_click_metric("Partial", str(partial), "k_part",
                             drilldown_title="Tags that can be partly built",
                             drilldown_df=feas_table[
                                 feas_table["Status"].str.contains("Partial", na=False)
                             ] if not feas.empty else pd.DataFrame())
        with r2[3]:
            dbl_click_metric("Critical (<50%)", str(critical), "k_crit",
                             drilldown_title="Tags blocked below 50% completion",
                             drilldown_df=critical_drill,
                             help_text="Worst-coverage equipment that should be deprioritised or have materials urgently procured.")

        st.markdown("---")
        if feas.empty:
            st.info(
                "No equipment + recipe data yet. Run "
                "`python3 scripts/sme_bootstrap.py --site-id <site>` or use "
                "the Master Data tab to add entries."
            )
            return

        st.subheader("Feasibility by priority order")
        feas_display = feas_table.copy()
        feas_display["Status"] = feas_display.apply(
            lambda r: (
                "🟢 Ready" if "Fully Ready" in str(r["Status"])
                else "🔴 Blocked" if "Blocked" in str(r["Status"])
                else f"🟡 {r['Completion_Pct']:.1f}%"
            ),
            axis=1,
        )
        st.dataframe(feas_display, use_container_width=True, hide_index=True)
        sme_download_pair(
            feas, report_name="Project_Overview",
            title="Project Overview", key="dash_overview",
            site_id=site_id, sheet_name="Overview",
        )

    else:  # Procurement
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

        # Stock-only materials (in inventory but not in any active recipe)
        if not inv_view.empty:
            in_plan = set(alloc["Material_Code"]) if not alloc.empty else set()
            stock_only = inv_view[~inv_view["Material_Code"].isin(in_plan)]
            stock_only = stock_only[stock_only["Available_Qty"] > 0]
            if not stock_only.empty:
                with st.expander(
                    f"📦 Stock-only materials ({len(stock_only)}) — present "
                    "in inventory but not in any active recipe",
                    expanded=False,
                ):
                    st.dataframe(stock_only, use_container_width=True, hide_index=True)
