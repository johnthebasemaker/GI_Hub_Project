"""Tab 0 — Dashboard (R19 SME-parity port).

Two sub-views:
  📈 Project Overview                — 7-card KPI strip + gauge + multi-chart
  🛒 Material Requirement & Procurement — 4-card strip + per-location
                                          breakdown + grand procurement table

Filters: Location · Type · System Code · Substrate (4-column strip).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .charts import (
    render_design_gauge,
    render_design_hbar,
    render_plotly_grouped_bar_by_location,
    render_plotly_stacked_hbar,
)
from .colors import scheme_for_location
from .downloads import sme_download_pair, sme_multi_sheet_download_pair
from .widgets import (
    code_chip,
    dbl_click_metric,
    fulfil_pill,
    loc_badge,
    plotly_mat_table,
    status_dot,
)


def _build_dashboard_frames(
    site_id: str | None,
    priority_order: list[str],
    *,
    locs: list[str], types: list[str],
    codes: list[str], substrates: list[str],
) -> dict:
    equip, recipe, inv, prog = data_layer.build_estimator_inputs(site_id)
    if equip.empty or recipe.empty:
        return {"empty": True}
    # Apply filters
    eq = equip.copy()
    if locs:
        eq = eq[eq["Location"].isin(locs)]
    if types:
        eq = eq[eq["Type"].isin(types)]
    if codes:
        eq = eq[eq["Lining_System_Code"].astype(str).isin(codes)]
    if substrates and "Substrate" in eq.columns:
        eq = eq[eq["Substrate"].isin(substrates)]
    if eq.empty:
        return {"empty": True}

    filtered_tags = eq["Equipment_Tag_No."].drop_duplicates().tolist()
    proj_sqm = float(eq["Surface_Area_SQM"].sum())

    # Run allocation on filtered tags only
    order = [t for t in priority_order if t in filtered_tags] + \
            [t for t in filtered_tags if t not in priority_order]
    demand, inv_clean = AE.build_demand_matrix(eq, recipe, inv)
    if demand.empty:
        return {"empty": True}
    alloc = AE.allocate_sequential(demand, inv_clean, order)
    feas  = AE.compute_feasibility(alloc)

    # Per-tag fulfillment + coverable SQM
    cov_by_tag = alloc.groupby("Equipment_Tag_No.")["Fulfillment_Rate"].mean()
    eq_sqm = eq.groupby("Equipment_Tag_No.")["Surface_Area_SQM"].sum()
    can_sqm_per_tag = (cov_by_tag.reindex(eq_sqm.index).fillna(0) * eq_sqm).round(3)
    can_sqm = float(can_sqm_per_tag.sum())
    short_sqm = max(0.0, proj_sqm - can_sqm)
    f_cov = (can_sqm / proj_sqm * 100) if proj_sqm else 100.0

    # Material balance (aggregated across the filtered plan)
    mat_demand = demand.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    )["Demand_Qty"].sum().rename(columns={"Demand_Qty": "Total Demand"})
    inv_join = inv.set_index("Material_Code")[["Available_Qty", "Ordered_Qty"]] \
        if not inv.empty else pd.DataFrame()
    if not inv_join.empty:
        mat_demand = mat_demand.merge(
            inv_join, left_on="Material_Code", right_index=True, how="left",
        )
    else:
        mat_demand["Available_Qty"] = 0.0
        mat_demand["Ordered_Qty"] = 0.0
    mat_demand["Available_Qty"] = mat_demand["Available_Qty"].fillna(0)
    mat_demand["Ordered_Qty"]   = mat_demand["Ordered_Qty"].fillna(0)
    mat_demand["Shortfall"] = (
        mat_demand["Total Demand"] - mat_demand["Available_Qty"]
    ).clip(lower=0).round(3)
    mat_demand["Net Shortfall"] = (
        mat_demand["Shortfall"] - mat_demand["Ordered_Qty"]
    ).clip(lower=0).round(3)
    mat_demand["Coverage %"] = (
        (mat_demand["Available_Qty"] + mat_demand["Ordered_Qty"])
        / mat_demand["Total Demand"].replace(0, pd.NA) * 100
    ).clip(0, 100).round(1).fillna(100)
    mat_demand = mat_demand.rename(columns={
        "Material_Code": "Code",
        "Material_Name": "Material Name",
        "Available_Qty": "Available",
        "Ordered_Qty":   "On Order",
    })[[
        "Code", "Material Name", "UOM",
        "Available", "On Order", "Total Demand",
        "Shortfall", "Net Shortfall", "Coverage %",
    ]]

    # Per-location roll-up
    loc_groups = eq.groupby("Location", dropna=False)
    loc_rows = []
    for loc_name, grp in loc_groups:
        loc_tags = grp["Equipment_Tag_No."].drop_duplicates().tolist()
        loc_sqm = float(grp["Surface_Area_SQM"].sum())
        loc_can = float(can_sqm_per_tag.reindex(loc_tags).fillna(0).sum())
        loc_pct = (loc_can / loc_sqm * 100) if loc_sqm else 100.0
        loc_rows.append({
            "location": loc_name or "—",
            "tags": len(loc_tags),
            "sqm": loc_sqm,
            "can_sqm": loc_can,
            "pct": loc_pct,
            "available": loc_can,
            "shortage": max(0, loc_sqm - loc_can),
        })

    # Per-system-code roll-up
    code_groups = alloc.groupby("Material_Code")["Fulfillment_Rate"].mean()
    by_code = recipe.merge(
        alloc.groupby(["Lining_System_Code", "Material_Code"], as_index=False)
            .agg(Demand=("Demand_Qty", "sum"),
                 Allocated=("Allocated_Qty", "sum")),
        on=["Lining_System_Code", "Material_Code"], how="right",
    )
    sys_rollup = by_code.groupby(
        "Lining_System_Code", as_index=False,
    ).agg(
        Total_Demand=("Demand", "sum"),
        Total_Allocated=("Allocated", "sum"),
    )
    sys_rollup["Coverage %"] = (
        sys_rollup["Total_Allocated"] / sys_rollup["Total_Demand"].replace(0, pd.NA) * 100
    ).clip(0, 100).round(1).fillna(100)

    # Material chart items (top shortages)
    mat_chart_items = []
    short_mat = mat_demand[mat_demand["Shortfall"] > 0].sort_values(
        "Coverage %",
    ).head(10)
    for _, r in short_mat.iterrows():
        mat_chart_items.append({
            "label": f"{r['Code']} · {str(r['Material Name'])[:24]}",
            "pct": float(r["Coverage %"]),
        })

    return {
        "empty": False,
        "eq": eq, "alloc": alloc, "feas": feas, "inv": inv,
        "demand": demand, "recipe": recipe, "prog": prog,
        "filtered_tags": filtered_tags,
        "proj_sqm": proj_sqm, "can_sqm": can_sqm,
        "short_sqm": short_sqm, "f_cov": f_cov,
        "mat_demand": mat_demand,
        "loc_rows": loc_rows,
        "sys_rollup": sys_rollup,
        "mat_chart_items": mat_chart_items,
        "can_sqm_per_tag": can_sqm_per_tag,
        "eq_sqm_by_tag": eq_sqm,
        "cov_by_tag": cov_by_tag,
    }


def _render_filters(equip: pd.DataFrame) -> dict:
    c1, c2, c3, c4 = st.columns(4)
    all_locs = sorted([l for l in equip["Location"].dropna().unique()]) \
        if not equip.empty else []
    all_types = sorted([t for t in equip["Type"].dropna().unique()]) \
        if not equip.empty else []
    with c1:
        locs = st.multiselect(
            "Location", all_locs, default=[],
            placeholder="All locations", key="dash_loc",
        )
    with c2:
        scoped_types = (
            sorted(equip[equip["Location"].isin(locs)]["Type"].dropna().unique())
            if locs else all_types
        )
        types = st.multiselect(
            "Type", scoped_types, default=[],
            placeholder="All types", key="dash_type",
        )
    with c3:
        scoped_codes_df = equip.copy()
        if locs:
            scoped_codes_df = scoped_codes_df[scoped_codes_df["Location"].isin(locs)]
        if types:
            scoped_codes_df = scoped_codes_df[scoped_codes_df["Type"].isin(types)]
        scoped_codes = sorted([
            str(c) for c in scoped_codes_df["Lining_System_Code"].dropna().unique()
        ])
        codes = st.multiselect(
            "System Code", scoped_codes, default=[],
            placeholder="All codes", key="dash_code",
        )
    with c4:
        subs = (
            sorted(equip["Substrate"].dropna().unique())
            if "Substrate" in equip.columns else []
        )
        substrates = st.multiselect(
            "Substrate", subs, default=[],
            placeholder="All substrates", key="dash_substrate",
        )
    return {"locs": locs, "types": types, "codes": codes, "substrates": substrates}


def _render_project_overview(d: dict, site_id: str | None) -> None:
    # ── 7-card KPI strip ───────────────────────────────────────────────────
    eq = d["eq"]; alloc = d["alloc"]; mat_demand = d["mat_demand"]
    eq_drill = eq[["Equipment_Tag_No.", "Name", "Location",
                   "Type", "Lining_System_Code", "Surface_Area_SQM"]] \
        .rename(columns={"Equipment_Tag_No.": "Equipment Tag No."})
    sqm_drill = eq[["Equipment_Tag_No.", "Name", "Location",
                    "Lining_System_Code", "Surface_Area_SQM"]] \
        .rename(columns={"Equipment_Tag_No.": "Equipment Tag No.",
                         "Surface_Area_SQM": "Total SQM"})
    # Coverable SQM per (tag, code)
    cov_per_pair = alloc.groupby(
        ["Equipment_Tag_No.", "Lining_System_Code"], as_index=False,
    )["Fulfillment_Rate"].mean()
    eq_pair_sqm = eq.groupby(
        ["Equipment_Tag_No.", "Lining_System_Code"], as_index=False,
    )["Surface_Area_SQM"].sum()
    cov_pair = cov_per_pair.merge(
        eq_pair_sqm, on=["Equipment_Tag_No.", "Lining_System_Code"], how="left",
    )
    cov_pair["Coverable SQM"] = (
        cov_pair["Surface_Area_SQM"] * cov_pair["Fulfillment_Rate"]
    ).round(2)
    cov_pair["SQM Deficit"] = (
        cov_pair["Surface_Area_SQM"] - cov_pair["Coverable SQM"]
    ).clip(lower=0).round(2)
    cov_drill = cov_pair[[
        "Equipment_Tag_No.", "Lining_System_Code",
        "Surface_Area_SQM", "Coverable SQM", "SQM Deficit",
    ]].rename(columns={"Equipment_Tag_No.": "Equipment Tag No.",
                       "Lining_System_Code": "System Code",
                       "Surface_Area_SQM": "Total SQM"})
    def_drill = cov_drill[cov_drill["SQM Deficit"] > 0].sort_values(
        "SQM Deficit", ascending=False,
    )
    critical_drill = mat_demand[mat_demand["Coverage %"] < 50].sort_values(
        "Coverage %",
    )

    f_cov = d["f_cov"]
    delta_str = f"{f_cov - 100:+.1f}%" if f_cov < 100 else "✓ On target"

    cols = st.columns(7)
    with cols[0]:
        dbl_click_metric("Equipment", f"{len(d['filtered_tags'])}", "t0_equip",
                         drilldown_title="Equipment in plan",
                         drilldown_df=eq_drill)
    with cols[1]:
        dbl_click_metric("Total SQM", f"{d['proj_sqm']:,.1f}", "t0_sqm",
                         drilldown_title="SQM by Equipment & System Code",
                         drilldown_df=sqm_drill)
    with cols[2]:
        dbl_click_metric("Coverable SQM", f"{d['can_sqm']:,.2f}", "t0_cov",
                         drilldown_title="Coverable SQM by Equipment & System Code",
                         drilldown_df=cov_drill)
    with cols[3]:
        dbl_click_metric("SQM Deficit", f"{d['short_sqm']:,.2f}", "t0_def",
                         drilldown_title="SQM Deficit by Equipment & System Code",
                         drilldown_df=def_drill)
    with cols[4]:
        dbl_click_metric("Overall Coverage", f"{f_cov:.1f}%", "t0_ov_cov",
                         drilldown_title="Coverage by Equipment",
                         drilldown_df=cov_drill,
                         delta=delta_str)
    with cols[5]:
        dbl_click_metric("Shortfall SQM", f"{d['short_sqm']:,.2f}", "t0_short",
                         drilldown_title="Shortfall SQM",
                         drilldown_df=def_drill)
    with cols[6]:
        dbl_click_metric(
            "Critical (<50%)",
            f"{int((mat_demand['Coverage %'] < 50).sum())}",
            "t0_crit",
            drilldown_title="Critical Materials (Coverage < 50%)",
            drilldown_df=critical_drill,
        )
    st.markdown("---")

    # ── Gauge + mini stacked bar side-by-side ─────────────────────────────
    g1, g2 = st.columns([1, 1.2])
    with g1:
        render_design_gauge(f_cov, d["can_sqm"], d["proj_sqm"])
    with g2:
        render_plotly_stacked_hbar(
            items=[{
                "label": "Project",
                "available": d["can_sqm"],
                "shortage": d["short_sqm"],
            }],
            title="Demand vs Available (SQM)",
            height=120,
        )

    # ── Coverage by Location ───────────────────────────────────────────────
    st.markdown("#### Coverage by Location")
    render_plotly_grouped_bar_by_location(rows=d["loc_rows"])
    loc_strip = st.columns(max(1, len(d["loc_rows"])))
    for i, lr in enumerate(d["loc_rows"]):
        with loc_strip[i]:
            st.markdown(
                f"{loc_badge(lr['location'])}  "
                f"<span style='font-size:12px;color:#374151;'>"
                f"{lr['tags']} tags · {lr['sqm']:,.1f} SQM · "
                f"<b>{lr['pct']:.1f}%</b></span>",
                unsafe_allow_html=True,
            )

    # ── Coverage by System Code ────────────────────────────────────────────
    st.markdown("#### Coverage by System Code")
    sc_items = []
    for _, r in d["sys_rollup"].sort_values("Coverage %").iterrows():
        sc_items.append({
            "label": f"Code {r['Lining_System_Code']}",
            "pct": float(r["Coverage %"]),
        })
    render_design_hbar(sc_items)

    # ── Coverage by Material ──────────────────────────────────────────────
    if d["mat_chart_items"]:
        st.markdown("#### Coverage by Material (lowest 10)")
        render_design_hbar(d["mat_chart_items"])

    # ── Full Material Balance ─────────────────────────────────────────────
    st.markdown("#### Full Material Balance")
    def _style_mat(row):
        try:
            p = float(row["Coverage %"])
        except (TypeError, ValueError):
            p = 100
        if p >= 100:
            bg = "rgba(16,185,129,0.10)"
        elif p >= 80:
            bg = "rgba(234,179,8,0.12)"
        else:
            bg = "rgba(239,68,68,0.15)"
        return [f"background-color:{bg}"] * len(row)
    styled = d["mat_demand"].style.apply(_style_mat, axis=1).format({
        "Available":     "{:,.3f}",
        "On Order":      "{:,.3f}",
        "Total Demand":  "{:,.3f}",
        "Shortfall":     "{:,.3f}",
        "Net Shortfall": "{:,.3f}",
        "Coverage %":    "{:.1f}%",
    })
    st.dataframe(styled, use_container_width=True, hide_index=True,
                 height=min(35 * (len(d["mat_demand"]) + 1) + 3, 480))

    # ── Stock-Only Materials ──────────────────────────────────────────────
    in_plan = set(d["mat_demand"]["Code"])
    inv_all = d["inv"]
    stock_only = inv_all[~inv_all["Material_Code"].isin(in_plan)] \
        if not inv_all.empty else pd.DataFrame()
    if not stock_only.empty:
        stock_only = stock_only[stock_only["Available_Qty"] > 0]
    if not stock_only.empty:
        with st.expander(
            f"📦 Stock-Only Materials ({len(stock_only)}) — present in "
            "inventory but not used by this plan", expanded=False,
        ):
            st.dataframe(
                stock_only.rename(columns={
                    "Material_Code": "Code",
                    "Material_Name": "Material Name",
                    "Available_Qty": "Available",
                    "Ordered_Qty":   "On Order",
                })[["Code", "Material Name", "UOM", "Available", "On Order"]],
                use_container_width=True, hide_index=True,
            )

    # ── Downloads ──────────────────────────────────────────────────────────
    sme_download_pair(
        d["mat_demand"], report_name="Material_Balance",
        title="Material Balance", key="dash_balance",
        site_id=site_id, color_scheme="dashboard",
        sheet_name="Material Balance",
    )


def _render_procurement(d: dict, site_id: str | None) -> None:
    # 4-card strip
    cols = st.columns(4)
    with cols[0]:
        dbl_click_metric("Equipment", f"{len(d['filtered_tags'])}", "t0p_equip",
                         drilldown_df=d["eq"])
    with cols[1]:
        dbl_click_metric("Total SQM", f"{d['proj_sqm']:,.1f}", "t0p_sqm",
                         drilldown_df=d["eq"])
    with cols[2]:
        dbl_click_metric("Coverable SQM", f"{d['can_sqm']:,.2f}", "t0p_cov",
                         drilldown_df=d["eq"])
    with cols[3]:
        dbl_click_metric("SQM Deficit", f"{d['short_sqm']:,.2f}", "t0p_def",
                         drilldown_df=d["eq"])
    st.markdown("---")

    # Per-location → per-code expanders
    for lr in d["loc_rows"]:
        loc_name = lr["location"]
        loc_tags = d["eq"][d["eq"]["Location"] == loc_name]["Equipment_Tag_No."].tolist()
        loc_alloc = d["alloc"][d["alloc"]["Equipment_Tag_No."].isin(loc_tags)]
        if loc_alloc.empty:
            continue
        st.markdown(
            f"### {status_dot(lr['pct'])} {loc_badge(loc_name)} "
            f"&nbsp; <span style='font-size:13px;color:#374151;'>"
            f"{lr['tags']} tags · {lr['sqm']:,.1f} SQM · "
            f"<b>{lr['pct']:.1f}%</b></span>",
            unsafe_allow_html=True,
        )
        # Per-code expanders within the location
        loc_eq = d["eq"][d["eq"]["Location"] == loc_name]
        codes_at_loc = sorted(loc_eq["Lining_System_Code"].astype(str).unique())
        for code in codes_at_loc:
            code_alloc = loc_alloc[
                loc_alloc["Equipment_Tag_No."].isin(
                    loc_eq[loc_eq["Lining_System_Code"] == code]["Equipment_Tag_No."]
                )
            ]
            if code_alloc.empty:
                continue
            code_sqm = float(loc_eq[loc_eq["Lining_System_Code"] == code]["Surface_Area_SQM"].sum())
            code_cov = float(code_alloc["Fulfillment_Rate"].mean() * 100)
            sn = ""
            sn_rows = d["recipe"][d["recipe"]["Lining_System_Code"] == code]
            if not sn_rows.empty:
                sn = sn_rows.iloc[0].get("Lining_System_Name") or ""
            with st.expander(
                f"  Code {code} · {sn} · {code_sqm:,.1f} SQM · {code_cov:.1f}%",
                expanded=False,
            ):
                # 5 KPI metrics
                mcols = st.columns(5)
                mcols[0].metric("System Code", str(code))
                mcols[1].metric("Short Name", sn or "—")
                mcols[2].metric("SQM Total", f"{code_sqm:,.1f}")
                mcols[3].metric("Coverable SQM", f"{code_sqm * code_cov / 100:,.1f}")
                mcols[4].metric("SQM Deficit",
                                f"{code_sqm * (1 - code_cov / 100):,.1f}")
                # Per-code material table
                mat = code_alloc.groupby(
                    ["Material_Code", "Material_Name", "UOM"], as_index=False,
                ).agg(
                    Demand_Qty=("Demand_Qty", "sum"),
                    Allocated_Qty=("Allocated_Qty", "sum"),
                    Shortfall_Qty=("Shortfall_Qty", "sum"),
                )
                mat["Fulfillment_Rate"] = (
                    mat["Allocated_Qty"] / mat["Demand_Qty"].replace(0, pd.NA)
                ).fillna(1.0)
                plotly_mat_table(mat, key_suffix=f"t0p_{loc_name}_{code}",
                                 allocated_label="Available")
        st.markdown("---")

    # Grand procurement table
    st.markdown("### Grand Procurement Summary")
    grand = d["mat_demand"].copy()
    st.dataframe(grand, use_container_width=True, hide_index=True,
                 height=min(35 * (len(grand) + 1) + 3, 460))
    sme_download_pair(
        grand, report_name="Grand_Procurement",
        title="Grand Procurement", key="dash_grand",
        site_id=site_id, color_scheme="dashboard",
    )

    # Net Order List (only shortages)
    net = grand[grand["Net Shortfall"] > 0]
    if not net.empty:
        st.markdown("### 🛒 Net Order List (after open POs)")
        st.dataframe(net, use_container_width=True, hide_index=True,
                     height=min(35 * (len(net) + 1) + 3, 320))
        sme_download_pair(
            net, report_name="Net_Order_List",
            title="Net Order List", key="dash_net",
            site_id=site_id, color_scheme="dashboard",
        )


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    # Pre-load equipment for filter scoping
    equip_all = data_layer.load_equipment(site_id)
    if equip_all.empty:
        st.info(
            "📭 No SME equipment loaded yet for this site. "
            "Bootstrap via `python3 scripts/sme_bootstrap.py --site-id <SITE>` "
            "or use the Master Data tab."
        )
        return

    filters = _render_filters(equip_all)
    st.markdown("---")
    sub = st.radio(
        "View",
        ["📈 Project Overview", "🛒 Material Requirement & Procurement"],
        horizontal=True, key="_sme_dash_sub",
    )

    d = _build_dashboard_frames(
        site_id, priority_order,
        locs=filters["locs"], types=filters["types"],
        codes=filters["codes"], substrates=filters["substrates"],
    )
    if d.get("empty"):
        st.info("No data after applying filters.")
        return
    if sub == "📈 Project Overview":
        _render_project_overview(d, site_id)
    else:
        _render_procurement(d, site_id)
