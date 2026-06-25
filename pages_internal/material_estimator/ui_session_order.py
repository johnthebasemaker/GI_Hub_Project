"""Tab 2 — Session Order Report (R19 SME-parity port).

Full computed picture for the current session priority order:
- 4-card KPI strip
- Priority reorder sortable (in-tab)
- Per-equipment expanders with metadata + per-system-code blocks +
  plotly material table (show_sqm=True) + equipment grand-total box
- Combined procurement section with stacked horizontal bar + master table
- 5-cell grand total box
- Excel + PDF download pairs (session + order list)
- Smart Suggestions panel at the bottom
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .charts import render_plotly_stacked_hbar
from .downloads import sme_download_pair, sme_multi_sheet_download_pair
from .suggestion_panel import render_suggestion_panel
from .widgets import (
    code_chip,
    dbl_click_metric,
    fulfil_pill,
    loc_badge,
    plotly_mat_table,
    status_dot,
)

try:
    from streamlit_sortables import sort_items
    _HAS_SORTABLE = True
except ImportError:
    _HAS_SORTABLE = False


_STATE_KEY = "session_tags"


def _ensure_state(site_id: str | None) -> list[str]:
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = engine_runner.get_default_priority(site_id)
    return st.session_state[_STATE_KEY]


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    session_tags = _ensure_state(site_id)
    if not session_tags:
        st.info(
            "📭 No tags in your session yet. Go to **🔍 Selective Equipment "
            "Entry** to add some, then return here."
        )
        return

    equip = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    inv_view = data_layer.load_inventory_view(site_id)
    if equip.empty or recipe.empty:
        st.warning("Equipment + recipe master required.")
        return

    eq_session = equip[equip["Equipment_Tag_No."].isin(session_tags)]
    if eq_session.empty:
        st.warning("Session tags not found in current equipment master.")
        return

    alloc, feas, inv_clean = engine_runner.run_allocation(site_id, session_tags)
    if alloc.empty:
        st.info("Engine returned no allocation.")
        return

    # ── 4-card KPI strip ───────────────────────────────────────────────────
    proj_sqm = float(eq_session["Surface_Area_SQM"].sum())
    mat_demand = alloc.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    )["Demand_Qty"].sum()
    mat_demand = mat_demand.merge(
        inv_view[["Material_Code", "Available_Qty"]],
        on="Material_Code", how="left",
    )
    mat_demand["Available_Qty"] = mat_demand["Available_Qty"].fillna(0)
    mat_demand["Shortfall"] = (
        mat_demand["Demand_Qty"] - mat_demand["Available_Qty"]
    ).clip(lower=0)
    overall_cov = float(alloc["Fulfillment_Rate"].mean() * 100) if not alloc.empty else 100.0

    eq_list = eq_session[["Equipment_Tag_No.", "Name", "Location", "Type",
                          "Lining_System_Code", "Surface_Area_SQM"]] \
        .rename(columns={"Equipment_Tag_No.": "Equipment Tag No."})
    mat_summary = mat_demand.rename(columns={
        "Material_Code": "Code", "Material_Name": "Material Name",
        "Available_Qty": "Available", "Demand_Qty": "Demand",
    })
    to_order = mat_summary[mat_summary["Shortfall"] > 0].sort_values(
        "Shortfall", ascending=False,
    )
    cov_by_material = mat_demand.copy()
    cov_by_material["Coverage %"] = (
        cov_by_material["Available_Qty"]
        / cov_by_material["Demand_Qty"].replace(0, pd.NA) * 100
    ).clip(0, 100).round(1).fillna(100)
    cov_by_material = cov_by_material.sort_values("Coverage %").rename(columns={
        "Material_Code": "Code", "Material_Name": "Material Name",
        "Demand_Qty": "Demand", "Available_Qty": "Available",
    })

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        dbl_click_metric(
            "Equipment", str(len(session_tags)), "t2_equip",
            drilldown_title="Session Equipment List",
            drilldown_df=eq_list,
        )
    with k2:
        dbl_click_metric(
            "Materials", str(len(mat_demand)), "t2_mats",
            drilldown_title="Material Demand Summary",
            drilldown_df=mat_summary,
        )
    with k3:
        dbl_click_metric(
            "Need to Order", str(len(to_order)), "t2_order",
            drilldown_title="Materials to Procure",
            drilldown_df=to_order,
        )
    with k4:
        dbl_click_metric(
            "Overall Coverage", f"{overall_cov:.1f}%", "t2_cov",
            drilldown_title="Coverage by Material",
            drilldown_df=cov_by_material,
        )

    # ── Priority reorder (drag) ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Priority order** (drag to reorder)")
    if _HAS_SORTABLE:
        order_labels = [f"#{i+1}  ||  {t}" for i, t in enumerate(session_tags)]
        sk = "t2_sort_" + "_".join(session_tags)[:120]
        new = sort_items(order_labels, direction="vertical")
        if new and new != order_labels:
            new_order = [lbl.split("||")[1].strip() for lbl in new]
            if set(new_order) == set(session_tags):
                st.session_state[_STATE_KEY] = new_order
                engine_runner._cached_allocation.clear()
                st.rerun()

    # ── Per-equipment expanders ────────────────────────────────────────────
    for i, tag in enumerate(session_tags, start=1):
        tag_meta = eq_session[eq_session["Equipment_Tag_No."] == tag]
        if tag_meta.empty:
            continue
        first = tag_meta.iloc[0]
        name = first.get("Name") or ""
        type_ = first.get("Type") or "—"
        substrate = first.get("Substrate") or "—"
        loc = first.get("Location") or "—"
        tag_sqm = float(tag_meta["Surface_Area_SQM"].sum())
        tag_alloc = alloc[alloc["Equipment_Tag_No."] == tag]
        tag_pct = float(tag_alloc["Fulfillment_Rate"].mean() * 100) if not tag_alloc.empty else 100.0
        tag_can_sqm = tag_sqm * tag_pct / 100

        with st.expander(
            f"#{i} {tag} · {name} · {type_} | {substrate} · {loc} · "
            f"{tag_can_sqm:,.1f}/{tag_sqm:,.1f} SQM · {tag_pct:.1f}%",
            expanded=False,
        ):
            # Metadata strip
            mcols = st.columns(4)
            mcols[0].markdown(f"**Type:** {type_}")
            mcols[1].markdown(f"**Substrate:** {substrate}")
            mcols[2].markdown(f"**Total SQM:** {tag_sqm:,.2f}")
            mcols[3].markdown(loc_badge(loc), unsafe_allow_html=True)

            # Per system code block
            for _, sysr in tag_meta.iterrows():
                code = str(sysr["Lining_System_Code"])
                sys_sqm = float(sysr["Surface_Area_SQM"])
                sn_rows = recipe[recipe["Lining_System_Code"] == code]
                sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
                sys_alloc = tag_alloc[tag_alloc["Material_Code"].isin(sn_rows["Material_Code"])]
                sys_pct = float(sys_alloc["Fulfillment_Rate"].mean() * 100) if not sys_alloc.empty else 100.0
                st.markdown(
                    f"<div style='background:#EFF6FF;padding:6px 10px;"
                    f"border-left:3px solid #1E5799;border-radius:4px;"
                    f"margin-top:8px;'>"
                    f"{status_dot(sys_pct)} {code_chip(code, sn or '')} "
                    f"<span style='font-size:12px;color:#374151;'>"
                    f"· {sys_sqm:,.2f} SQM</span> "
                    f"<span style='float:right;'>{fulfil_pill(sys_pct)}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # 4 KPIs (only if shortfall)
                if not sys_alloc.empty:
                    sys_demand = float(sys_alloc["Demand_Qty"].sum())
                    sys_alloc_qty = float(sys_alloc["Allocated_Qty"].sum())
                    sys_short = float(sys_alloc["Shortfall_Qty"].sum())
                    sys_deficit = sys_sqm * (1 - sys_pct / 100)
                    kcols = st.columns(4)
                    kcols[0].metric("Demand", f"{sys_demand:,.3f}")
                    kcols[1].metric("Allocated", f"{sys_alloc_qty:,.3f}")
                    if sys_short > 0.001:
                        kcols[2].metric("Shortfall", f"{sys_short:,.3f}")
                    if sys_deficit > 0.001:
                        kcols[3].metric("SQM Deficit", f"{sys_deficit:,.2f}")
                    # Material table
                    plotly_mat_table(
                        sys_alloc, key_suffix=f"t2_{tag}_{code}",
                        show_sqm=True, tag=tag, code=code,
                        total_sqm_for_sc=sys_sqm,
                        allocated_label="Allocated",
                    )
            # Equipment grand total
            tot_d = float(tag_alloc["Demand_Qty"].sum())
            tot_a = float(tag_alloc["Allocated_Qty"].sum())
            tot_s = float(tag_alloc["Shortfall_Qty"].sum())
            st.markdown(
                f"<div style='background:#FEF3C7;padding:8px 12px;"
                f"border:1px solid #F0C040;border-radius:6px;margin-top:8px;"
                f"display:grid;grid-template-columns:repeat(4,1fr);gap:10px;"
                f"font-size:12px;color:#374151;'>"
                f"<div><b>Demand:</b> {tot_d:,.2f}</div>"
                f"<div><b>Allocated:</b> {tot_a:,.2f}</div>"
                + (f"<div><b>Shortfall:</b> {tot_s:,.2f}</div>" if tot_s > 0.001 else "<div></div>")
                + f"<div style='text-align:right;'>{fulfil_pill(tag_pct)}</div>"
                + "</div>",
                unsafe_allow_html=True,
            )

    # ── Combined Procurement Section ───────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🛒 Combined Procurement Summary")
    short_only = mat_summary[mat_summary["Shortfall"] > 0]
    if not short_only.empty:
        chart_items = []
        for _, r in short_only.sort_values("Shortfall", ascending=False).head(10).iterrows():
            chart_items.append({
                "label": f"{r['Code']} · {str(r['Material Name'])[:24]}",
                "available": float(r["Available"]),
                "shortage": float(r["Shortfall"]),
            })
        render_plotly_stacked_hbar(items=chart_items,
                                   title="Top 10 shortages")

    plotly_mat_table(alloc.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    ).agg(
        Demand_Qty=("Demand_Qty", "sum"),
        Allocated_Qty=("Allocated_Qty", "sum"),
        Shortfall_Qty=("Shortfall_Qty", "sum"),
    ).assign(
        Fulfillment_Rate=lambda d: d["Allocated_Qty"] / d["Demand_Qty"].replace(0, pd.NA),
    ).fillna({"Fulfillment_Rate": 1.0}),
        key_suffix="t2_combined",
        height=420,
    )

    # 5-cell grand total
    st.markdown(
        f"""
        <div style="background:#FEF3C7;border:1px solid #F0C040;
        border-radius:8px;padding:10px 14px;margin-top:12px;
        display:grid;grid-template-columns:repeat(5,1fr);gap:10px;
        font-size:13px;color:#1F2937;">
          <div><b>Equipment:</b> {len(session_tags)}</div>
          <div><b>Materials:</b> {len(mat_summary)}</div>
          <div><b>Total Demand:</b> {float(mat_summary['Demand'].sum()):,.2f}</div>
          <div><b>To Procure:</b> {len(to_order)}</div>
          <div style="text-align:right;">{fulfil_pill(overall_cov)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Downloads ──────────────────────────────────────────────────────────
    st.markdown("---")
    full_session_sheets = [
        {"name": "Session Plan", "df": feas,
         "title": "Session Plan Summary", "color_scheme": "session"},
        {"name": "Per-Material", "df": alloc,
         "title": "Per-Material Allocation", "color_scheme": "session"},
    ]
    sme_multi_sheet_download_pair(
        full_session_sheets, report_name="Full_Session",
        title="Full Session Report", key="t2_full",
        site_id=site_id,
    )
    if not to_order.empty:
        sme_download_pair(
            to_order, report_name="Order_List",
            title="Order List", key="t2_order",
            site_id=site_id, color_scheme="session",
        )

    # ── Smart Suggestions ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔮 Smart Reorder Suggestions")
    render_suggestion_panel(site_id, session_tags, "tab2")
