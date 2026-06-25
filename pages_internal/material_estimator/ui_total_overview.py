"""Tab 7 — Total Overview (R19 SME-parity port).

Project-wide master table — independent of priority order.

Filters: Location · Type · System Code · Status (4 cells).
6-card KPI strip with drilldowns.
Per-system-code expanders with 5 KPIs + material detail table.
Excel + PDF download pair (color_scheme="overview").
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .downloads import sme_download_pair
from .widgets import (
    code_chip,
    dbl_click_metric,
    fulfil_pill,
    loc_badge,
    plotly_mat_table,
    status_dot,
)


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip, recipe, inv, prog = data_layer.build_estimator_inputs(site_id)
    if equip.empty or recipe.empty:
        st.info("Need equipment + recipe master.")
        return

    # ── Filters (4-cell) ───────────────────────────────────────────────────
    all_locs = sorted([l for l in equip["Location"].dropna().unique()])
    all_types = sorted([t for t in equip["Type"].dropna().unique()])
    f = st.columns(4)
    with f[0]:
        locs = st.multiselect(
            "Location", all_locs, default=[],
            placeholder="All locations", key="ov_loc",
        )
    with f[1]:
        type_scope = (
            sorted(equip[equip["Location"].isin(locs)]["Type"].dropna().unique())
            if locs else all_types
        )
        types = st.multiselect(
            "Type", type_scope, default=[],
            placeholder="All types", key="ov_type",
        )
    with f[2]:
        sc_scope_df = equip.copy()
        if locs:
            sc_scope_df = sc_scope_df[sc_scope_df["Location"].isin(locs)]
        if types:
            sc_scope_df = sc_scope_df[sc_scope_df["Type"].isin(types)]
        sc_scope = sorted(sc_scope_df["Lining_System_Code"].astype(str).unique())
        codes = st.multiselect(
            "System Code", sc_scope, default=[],
            placeholder="All codes", key="ov_code",
        )
    with f[3]:
        status_pick = st.selectbox(
            "Status",
            ["All", "Fully Ready (100%)", "Partial (50-99%)", "Blocked (<50%)"],
            key="ov_status",
        )

    # Apply filters
    eq_f = equip.copy()
    if locs:
        eq_f = eq_f[eq_f["Location"].isin(locs)]
    if types:
        eq_f = eq_f[eq_f["Type"].isin(types)]
    if codes:
        eq_f = eq_f[eq_f["Lining_System_Code"].astype(str).isin(codes)]
    if eq_f.empty:
        st.info("No equipment matches filters.")
        return

    tags_f = eq_f["Equipment_Tag_No."].drop_duplicates().tolist()
    order = [t for t in priority_order if t in tags_f] + \
            [t for t in tags_f if t not in priority_order]

    demand, inv_clean = AE.build_demand_matrix(eq_f, recipe, inv)
    alloc = AE.allocate_sequential(demand, inv_clean, order) if not demand.empty else pd.DataFrame()
    feas = AE.compute_feasibility(alloc) if not alloc.empty else pd.DataFrame()

    # Build master table
    progress_lookup = prog.set_index(
        ["Equipment_Tag_No.", "Lining_System_Code"]
    ) if not prog.empty else pd.DataFrame()

    master_rows = []
    for _, eqr in eq_f.iterrows():
        tag = eqr["Equipment_Tag_No."]
        code = str(eqr["Lining_System_Code"])
        total_sqm = float(eqr["Surface_Area_SQM"])
        done = 0.0
        if not progress_lookup.empty:
            try:
                pr = progress_lookup.loc[(tag, code)]
                done = float(pr.get("Done_SQM", 0) or 0)
            except KeyError:
                pass
        remaining = max(0.0, total_sqm - done)

        if not alloc.empty:
            sub = alloc[
                (alloc["Equipment_Tag_No."] == tag)
                & (alloc["Material_Code"].isin(
                    recipe[recipe["Lining_System_Code"] == code]["Material_Code"]
                ))
            ]
            d = float(sub["Demand_Qty"].sum())
            a = float(sub["Allocated_Qty"].sum())
            s = float(sub["Shortfall_Qty"].sum())
            pct = (a / d * 100) if d else 100.0
        else:
            d = a = s = 0.0
            pct = 0.0
        sn_rows = recipe[recipe["Lining_System_Code"] == code]
        sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
        master_rows.append({
            "S.No": len(master_rows) + 1,
            "Equipment Tag": tag,
            "Equipment Name": eqr.get("Name") or "",
            "Location": eqr.get("Location") or "—",
            "Type": eqr.get("Type") or "—",
            "Substrate": eqr.get("Substrate") or "—",
            "System Code": code,
            "System Name": sn or "",
            "Total SQM": round(total_sqm, 2),
            "Already Done SQM": round(done, 2),
            "Remaining SQM": round(remaining, 2),
            "Total Demand": round(d, 3),
            "Allocated": round(a, 3),
            "Shortfall Qty": round(s, 3),
            "Fulfil %": round(pct, 1),
        })
    master = pd.DataFrame(master_rows)

    # Apply status filter
    if status_pick == "Fully Ready (100%)":
        master = master[master["Fulfil %"] >= 100]
    elif status_pick == "Partial (50-99%)":
        master = master[(master["Fulfil %"] >= 50) & (master["Fulfil %"] < 100)]
    elif status_pick == "Blocked (<50%)":
        master = master[master["Fulfil %"] < 50]

    if master.empty:
        st.info("No rows after status filter.")
        return

    # ── 6-card KPI strip ───────────────────────────────────────────────────
    n_items = len(master)
    tot_sqm = float(master["Total SQM"].sum())
    done_sqm = float(master["Already Done SQM"].sum())
    rem_sqm = float(master["Remaining SQM"].sum())
    short_sqm = float(((1 - master["Fulfil %"] / 100) * master["Remaining SQM"]).sum())
    avg_cov = float(master["Fulfil %"].mean())

    k = st.columns(6)
    with k[0]:
        dbl_click_metric("Items", str(n_items), "t7_items",
                         drilldown_title="All filtered items",
                         drilldown_df=master)
    with k[1]:
        dbl_click_metric("Total SQM", f"{tot_sqm:,.2f}", "t7_sqm",
                         drilldown_title="Sorted by Total SQM",
                         drilldown_df=master.sort_values("Total SQM", ascending=False))
    with k[2]:
        dbl_click_metric("Already Done", f"{done_sqm:,.2f}", "t7_done",
                         drilldown_title="Sorted by Done SQM",
                         drilldown_df=master.sort_values("Already Done SQM", ascending=False))
    with k[3]:
        dbl_click_metric("Remaining", f"{rem_sqm:,.2f}", "t7_rem",
                         drilldown_title="Sorted by Remaining SQM",
                         drilldown_df=master.sort_values("Remaining SQM", ascending=False))
    with k[4]:
        dbl_click_metric("Shortfall SQM", f"{short_sqm:,.2f}", "t7_short",
                         drilldown_title="Rows with deficit",
                         drilldown_df=master[master["Fulfil %"] < 100].sort_values("Fulfil %"))
    with k[5]:
        dbl_click_metric("Avg Coverage", f"{avg_cov:.1f}%", "t7_avg",
                         drilldown_title="Sorted by coverage ascending",
                         drilldown_df=master.sort_values("Fulfil %"))

    # ── Master Table ───────────────────────────────────────────────────────
    st.markdown("---")
    def _style(row):
        try:
            p = float(row["Fulfil %"])
        except (TypeError, ValueError):
            p = 100.0
        if p >= 100:
            bg, tc = "rgba(16,185,129,0.10)", "#10B981"
        elif p >= 90:
            bg, tc = "rgba(249,115,22,0.12)", "#F97316"
        elif p >= 80:
            bg, tc = "rgba(234,179,8,0.12)", "#EAB308"
        else:
            bg, tc = "rgba(239,68,68,0.12)", "#EF4444"
        styles = [f"background-color:{bg}"] * len(row)
        idx = list(row.index).index("Fulfil %") if "Fulfil %" in row.index else -1
        if idx >= 0:
            styles[idx] = f"background-color:{bg};color:{tc};font-weight:700"
        return styles
    styled = master.style.apply(_style, axis=1).format({
        "Total SQM": "{:,.2f}", "Already Done SQM": "{:,.2f}",
        "Remaining SQM": "{:,.2f}", "Total Demand": "{:,.3f}",
        "Allocated": "{:,.3f}", "Shortfall Qty": "{:,.3f}",
        "Fulfil %": "{:.1f}%",
    })
    st.dataframe(styled, use_container_width=True, hide_index=True,
                 height=min(35 * (len(master) + 1) + 3, 480))

    # ── Sub-metrics summary ────────────────────────────────────────────────
    pending = rem_sqm
    completion_pct = (done_sqm / tot_sqm * 100) if tot_sqm else 0.0
    s = st.columns(4)
    s[0].metric("Total SQM", f"{tot_sqm:,.2f}")
    s[1].metric("Already Done SQM", f"{done_sqm:,.2f}")
    s[2].metric("Pending SQM", f"{pending:,.2f}")
    s[3].metric("Completion %", f"{completion_pct:.1f}%")

    # ── Per-system-code expanders ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Per-system-code drill-down")
    for code in sorted(master["System Code"].unique()):
        code_rows = master[master["System Code"] == code]
        sn = code_rows.iloc[0]["System Name"]
        c_total = float(code_rows["Total SQM"].sum())
        c_done = float(code_rows["Already Done SQM"].sum())
        c_can = float((code_rows["Fulfil %"] / 100 * code_rows["Remaining SQM"]).sum())
        c_pct = (c_can / c_total * 100) if c_total else 100.0
        with st.expander(
            f"{status_dot(c_pct)} Code {code} · {sn} · "
            f"{c_can:,.1f}/{c_total:,.1f} SQM · Done: {c_done:,.1f} · {c_pct:.1f}%",
            expanded=False,
        ):
            mcols = st.columns(5)
            with mcols[0]:
                dbl_click_metric("System Code", code,
                                 f"t7_c_{code}_sc",
                                 drilldown_df=code_rows)
            with mcols[1]:
                dbl_click_metric("Short Name", sn or "—",
                                 f"t7_c_{code}_sn",
                                 drilldown_df=code_rows)
            with mcols[2]:
                dbl_click_metric("Total SQM", f"{c_total:,.2f}",
                                 f"t7_c_{code}_ts",
                                 drilldown_df=code_rows)
            with mcols[3]:
                dbl_click_metric("Already Done", f"{c_done:,.2f}",
                                 f"t7_c_{code}_done",
                                 drilldown_df=code_rows)
            with mcols[4]:
                dbl_click_metric("Coverage", f"{c_pct:.1f}%",
                                 f"t7_c_{code}_cov",
                                 drilldown_df=code_rows)

            # Material detail
            if not alloc.empty:
                mat_for_code = alloc[
                    alloc["Material_Code"].isin(
                        recipe[recipe["Lining_System_Code"] == code]["Material_Code"]
                    )
                ]
                if not mat_for_code.empty:
                    plotly_mat_table(
                        mat_for_code.groupby(
                            ["Material_Code", "Material_Name", "UOM"], as_index=False,
                        ).agg(
                            Demand_Qty=("Demand_Qty", "sum"),
                            Allocated_Qty=("Allocated_Qty", "sum"),
                            Shortfall_Qty=("Shortfall_Qty", "sum"),
                        ).assign(
                            Fulfillment_Rate=lambda d:
                                d["Allocated_Qty"] / d["Demand_Qty"].replace(0, pd.NA),
                        ).fillna({"Fulfillment_Rate": 1.0}),
                        key_suffix=f"t7_{code}",
                        allocated_label="Available",
                    )

    # ── Downloads ──────────────────────────────────────────────────────────
    st.markdown("---")
    sme_download_pair(
        master, report_name="Total_Overview",
        title="Total Overview Master Table", key="t7_main",
        site_id=site_id, color_scheme="overview",
    )

    # Optional: full SME consumption ledger export
    if not master.empty:
        import database as D
        sme_log = D.get_sme_consumption_log(site_id=site_id, limit=2000)
        if sme_log is not None and not sme_log.empty:
            with st.expander(
                f"📥 SME Consumption Log ({len(sme_log)} entries)",
                expanded=False,
            ):
                st.dataframe(sme_log, use_container_width=True, hide_index=True,
                             height=min(35 * (len(sme_log) + 1) + 3, 320))
                sme_download_pair(
                    sme_log, report_name="SME_Consumption_Log",
                    title="SME Consumption Log", key="t7_cl",
                    site_id=site_id, color_scheme="overview",
                )
