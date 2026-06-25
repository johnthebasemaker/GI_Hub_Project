"""Tab 5 — Execution Plan (R19 SME-parity port — READ-ONLY).

Three sub-views (SME order):
  ⚙️ Execution Plan        — equipment + system code selectors with
                              critical card, materials table, and
                              procurement priority sections.
  📋 Progress List          — SQM done / remaining per (tag × system),
                              numbered production-detail blocks.
  📊 Consumption Comparison — expected vs actual via the ERP `consumption`
                              ledger (READ-ONLY — no submit form).
"""
from __future__ import annotations

import datetime as _dt

import pandas as pd
import streamlit as st

import database as D

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .downloads import (
    sme_download_pair,
    sme_multi_sheet_download_pair,
)
from .widgets import (
    code_chip,
    dbl_click_metric,
    fulfil_pill,
    loc_badge,
    plotly_mat_table,
    status_dot,
)


# ---------------------------------------------------------------------------
# Sub-view: Execution Plan (per-tag + per-system drilldown)
# ---------------------------------------------------------------------------

def _render_execution_plan(
    site_id: str | None, priority_order: list[str], username: str | None,
) -> None:
    session_tags = priority_order or engine_runner.get_default_priority(site_id)
    if not session_tags:
        st.info(
            "📭 No tags in priority. Use **🔍 Selective Equipment Entry** "
            "first, then return here."
        )
        return

    equip = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    alloc, _feas, _inv = engine_runner.run_allocation(site_id, session_tags)
    if equip.empty or alloc.empty:
        st.warning("Engine data unavailable.")
        return

    # Tag + system code selectors
    cols = st.columns(2)
    with cols[0]:
        tag_options = [
            f"{t} — {equip[equip['Equipment_Tag_No.'] == t].iloc[0].get('Name') or ''}"
            for t in session_tags
            if not equip[equip['Equipment_Tag_No.'] == t].empty
        ]
        sel = st.selectbox("Equipment", tag_options, key="exec_tag")
        sel_tag = sel.split(" — ")[0].strip() if sel else None
    with cols[1]:
        if sel_tag:
            avail_codes = sorted(
                equip[equip["Equipment_Tag_No."] == sel_tag]["Lining_System_Code"]
                    .astype(str).unique()
            )
            sel_code = st.selectbox(
                "System Code", avail_codes, key="exec_code",
            )
        else:
            sel_code = None

    if not (sel_tag and sel_code):
        st.info("Pick an equipment + system code to drill in.")
        return

    # Critical System Code card
    code_mask = (alloc["Equipment_Tag_No."] == sel_tag) \
        & (alloc["Material_Code"].isin(
            recipe[recipe["Lining_System_Code"] == sel_code]["Material_Code"]
        ))
    code_alloc = alloc[code_mask]
    code_pct = float(code_alloc["Fulfillment_Rate"].mean() * 100) if not code_alloc.empty else 100.0
    code_sqm = float(equip[(equip["Equipment_Tag_No."] == sel_tag)
                           & (equip["Lining_System_Code"].astype(str) == sel_code)]
                     ["Surface_Area_SQM"].sum())
    sn_rows = recipe[recipe["Lining_System_Code"] == sel_code]
    sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
    short_count = int((code_alloc["Shortfall_Qty"] > 0).sum())

    st.markdown(
        f"""
        <div style="background:#FEF3C7;border:1px solid #F0C040;
        border-radius:10px;padding:12px 16px;margin-top:8px;">
          <div style="font-size:13px;color:#A16207;font-weight:700;">
            Critical System Code
          </div>
          <div style="font-size:18px;color:#1F2937;font-weight:800;
                      margin-top:4px;">
            {code_chip(sel_code, sn or '')} &nbsp;
            <span style="color:#374151;">{code_sqm:,.2f} SQM</span> &nbsp;
            <span style="float:right;">{fulfil_pill(code_pct)}</span>
          </div>
          <div style="font-size:12px;color:#4B5563;margin-top:6px;">
            {short_count} material(s) with shortfall · allocation status as above.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Materials table — all materials across all system codes for sel_tag
    st.markdown("#### All materials for this equipment")
    tag_alloc_all = alloc[alloc["Equipment_Tag_No."] == sel_tag]
    plotly_mat_table(
        tag_alloc_all,
        key_suffix=f"t5_exec_{sel_tag}",
        height=300,
        allocated_label="Allocated",
    )

    # 1️⃣ Critical Code Shortages
    st.markdown("---")
    short_in_code = code_alloc[code_alloc["Shortfall_Qty"] > 0]
    if not short_in_code.empty:
        st.markdown(
            f"<div style='background:#FEE2E2;border:1px solid #DC2626;"
            f"border-radius:6px;padding:8px 12px;color:#991B1B;"
            f"font-weight:700;'>1️⃣ CRITICAL — Code {sel_code} Shortages "
            f"({len(short_in_code)})</div>",
            unsafe_allow_html=True,
        )
        plotly_mat_table(
            short_in_code, key_suffix=f"t5_crit_{sel_tag}_{sel_code}",
            height=240,
        )

    # 2️⃣ Other Code Shortages
    other_short = tag_alloc_all[
        (tag_alloc_all["Shortfall_Qty"] > 0)
        & (~tag_alloc_all["Material_Code"].isin(
            sn_rows["Material_Code"] if not sn_rows.empty else []
        ))
    ]
    if not other_short.empty:
        st.markdown(
            f"<div style='background:#FFEDD5;border:1px solid #F97316;"
            f"border-radius:6px;padding:8px 12px;color:#9A3412;"
            f"font-weight:700;margin-top:10px;'>"
            f"2️⃣ Other Code Shortages ({len(other_short)})</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(
            other_short[[
                "Lining_System_Code", "Material_Code", "Material_Name",
                "Demand_Qty", "Allocated_Qty", "Shortfall_Qty",
            ]] if "Lining_System_Code" in other_short.columns else other_short,
            use_container_width=True, hide_index=True,
        )

    # 3️⃣ Fully Covered Codes
    codes_at_tag = sorted(
        equip[equip["Equipment_Tag_No."] == sel_tag]["Lining_System_Code"].astype(str).unique()
    )
    covered_codes = []
    for c in codes_at_tag:
        c_alloc = alloc[
            (alloc["Equipment_Tag_No."] == sel_tag)
            & (alloc["Material_Code"].isin(
                recipe[recipe["Lining_System_Code"] == c]["Material_Code"]
            ))
        ]
        if not c_alloc.empty and float(c_alloc["Fulfillment_Rate"].mean() * 100) >= 100:
            covered_codes.append(c)
    if covered_codes:
        st.markdown(
            f"<div style='background:#D1FAE5;border:1px solid #10B981;"
            f"border-radius:6px;padding:8px 12px;color:#065F46;"
            f"font-weight:700;margin-top:10px;'>"
            f"3️⃣ Fully Covered Codes: {', '.join(covered_codes)}</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Sub-view: Progress List
# ---------------------------------------------------------------------------

def _render_progress_list(site_id: str | None) -> None:
    prog = data_layer.load_sqm_progress(site_id)
    equip = data_layer.load_equipment(site_id)
    if prog.empty or equip.empty:
        st.info("No progress data yet.")
        return

    # Join location + equipment name
    eq_meta = equip[["Equipment_Tag_No.", "Name", "Location"]].drop_duplicates()
    eq_meta = eq_meta.rename(columns={"Equipment_Tag_No.": "Equipment_Tag_No."})
    df = prog.merge(eq_meta, on="Equipment_Tag_No.", how="left")
    df = df.rename(columns={
        "Equipment_Tag_No.": "Equipment Tag",
        "Lining_System_Code": "System Code",
        "Original_SQM": "Total SQM",
        "Done_SQM": "Completed SQM",
        "Name": "Equipment Name",
    })
    df["Completed SQM"] = df["Completed SQM"].fillna(0).astype(float)
    if "Done_SQM_staged" in df.columns:
        df["In-flight SQM"] = df["Done_SQM_staged"].fillna(0).astype(float)
    else:
        df["In-flight SQM"] = 0.0
    df["Total SQM"] = df["Total SQM"].fillna(0).astype(float)
    df["Remaining SQM"] = (
        df["Total SQM"] - df["Completed SQM"] - df["In-flight SQM"]
    ).clip(lower=0).round(2)
    df["Completion %"] = (
        (df["Completed SQM"] + df["In-flight SQM"])
        / df["Total SQM"].replace(0, pd.NA) * 100
    ).clip(0, 100).round(1).fillna(0)

    # KPI strip
    total = float(df["Total SQM"].sum())
    completed = float(df["Completed SQM"].sum())
    remaining = float(df["Remaining SQM"].sum())
    pct = (completed / total * 100) if total else 0.0
    k = st.columns(4)
    k[0].metric("Total SQM", f"{total:,.2f}")
    k[1].metric("Completed SQM", f"{completed:,.2f}")
    k[2].metric("Remaining SQM", f"{remaining:,.2f}")
    k[3].metric("Completion %", f"{pct:.1f}%")
    st.markdown("---")

    # Filters
    f1, f2 = st.columns(2)
    with f1:
        loc_opts = ["All"] + sorted([l for l in df["Location"].dropna().unique()])
        loc_pick = st.selectbox("Location", loc_opts, key="prog_loc_f")
    with f2:
        status_pick = st.selectbox(
            "Status",
            ["All", "✅ Complete", "🔄 In Progress", "⏳ Not Started"],
            key="prog_status_f",
        )
    fdf = df.copy()
    if loc_pick != "All":
        fdf = fdf[fdf["Location"] == loc_pick]
    if status_pick == "✅ Complete":
        fdf = fdf[fdf["Completion %"] >= 100]
    elif status_pick == "🔄 In Progress":
        fdf = fdf[(fdf["Completion %"] > 0) & (fdf["Completion %"] < 100)]
    elif status_pick == "⏳ Not Started":
        fdf = fdf[fdf["Completion %"] == 0]

    # Progress table
    show_cols = [
        "Location", "Equipment Tag", "Equipment Name", "System Code",
        "Total SQM", "Completed SQM", "In-flight SQM",
        "Remaining SQM", "Completion %",
    ]
    show = fdf[[c for c in show_cols if c in fdf.columns]]

    def _style(row):
        try:
            p = float(row["Completion %"])
        except (TypeError, ValueError):
            p = 0.0
        if p >= 100:
            bg = "rgba(16,185,129,0.12)"
        elif p > 0:
            bg = "rgba(249,115,22,0.10)"
        else:
            bg = "rgba(239,68,68,0.10)"
        return [f"background-color:{bg}"] * len(row)
    if not show.empty:
        styled = show.style.apply(_style, axis=1).format({
            "Total SQM": "{:,.2f}", "Completed SQM": "{:,.2f}",
            "In-flight SQM": "{:,.2f}", "Remaining SQM": "{:,.2f}",
            "Completion %": "{:.1f}%",
        })
        st.dataframe(styled, use_container_width=True, hide_index=True,
                     height=min(35 * (len(show) + 1) + 3, 460))

    # Production details: per (tag × system) with consumption ledger lookups
    st.markdown("---")
    st.markdown("### 📋 Production Details (from ERP consumption ledger)")
    conn = D.get_connection()
    try:
        params: list = []
        site_pred = ""
        if site_id:
            site_pred = " AND c.Site_ID = ?"
            params.append(site_id)
        # Pull SME-routed consumption (Source_Ref starts with 'SME:'); falls
        # back to material_code based aggregation otherwise.
        sme_log = D.get_sme_consumption_log(site_id=site_id, conn=conn)
    finally:
        conn.close()
    if sme_log is None or sme_log.empty:
        st.caption("(No SME consumption_log entries yet.)")
    else:
        # Group by (tag, system)
        sme_log_committed = sme_log[sme_log["status"] == "committed"]
        if not sme_log_committed.empty:
            grouped = sme_log_committed.groupby(
                ["Equipment_Tag_No", "Lining_System_Code"], as_index=False,
            )
            n_blocks = 0
            for (tag, code), g in grouped:
                n_blocks += 1
                meta = equip[equip["Equipment_Tag_No."] == tag]
                tag_name = (meta.iloc[0].get("Name") if not meta.empty else "") or ""
                loc = (meta.iloc[0].get("Location") if not meta.empty else "") or "—"
                st.markdown(
                    f"<div style='background:#FEF3C7;border-left:3px solid #F0C040;"
                    f"padding:6px 12px;margin-top:10px;border-radius:4px;'>"
                    f"<b>#{n_blocks}</b> &nbsp; "
                    f"<code>{tag}</code> · {tag_name} · "
                    f"{code_chip(code, '')} · {loc_badge(loc)}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                detail = g.rename(columns={
                    "entry_date": "Date",
                    "Equipment_Tag_No": "Equipment Tag",
                    "Lining_System_Code": "System Code",
                    "Material_Code": "Material Code",
                    "Actual_Qty": "Consumed Qty",
                    "SQM_Completed": "SQM Done",
                })[[
                    "Date", "SQM Done", "Material Code",
                    "Consumed Qty",
                ]]
                st.dataframe(detail, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Sub-view: Consumption Comparison (expected vs actual; READ-ONLY)
# ---------------------------------------------------------------------------

def _render_consumption_comparison(
    site_id: str | None, priority_order: list[str],
) -> None:
    """Expected (from engine) vs Actual (from ERP `consumption` ledger).
    Joins on Material_Code. Pure read; no writes."""
    session_tags = priority_order or engine_runner.get_default_priority(site_id)
    alloc, _feas, _inv = engine_runner.run_allocation(site_id, session_tags)
    if alloc.empty:
        st.info("No allocation to compare against.")
        return

    today = _dt.date.today()
    default_start = today - _dt.timedelta(days=30)

    # Filters
    equip = data_layer.load_equipment(site_id)
    locs = sorted([l for l in equip["Location"].dropna().unique()]) if not equip.empty else []
    f1, f2, f3 = st.columns(3)
    with f1:
        start = st.date_input("From", value=default_start, key="cmp_from")
    with f2:
        end = st.date_input("To", value=today, key="cmp_to")
    with f3:
        loc_pick = st.multiselect(
            "Location", locs, default=[], placeholder="All", key="cmp_loc",
        )
    f4, f5 = st.columns(2)
    with f4:
        eq_opts = equip["Equipment_Tag_No."].drop_duplicates().tolist() \
            if not equip.empty else []
        if loc_pick:
            eq_opts = equip[equip["Location"].isin(loc_pick)]["Equipment_Tag_No."].drop_duplicates().tolist()
        eq_pick = st.multiselect(
            "Equipment Tag", eq_opts, default=[],
            placeholder="All", key="cmp_eq",
        )
    with f5:
        sc_opts = sorted(equip["Lining_System_Code"].astype(str).unique()) \
            if not equip.empty else []
        sc_pick = st.multiselect(
            "System Code", sc_opts, default=[],
            placeholder="All", key="cmp_sc",
        )

    # Apply scope to expected (alloc)
    exp = alloc.copy()
    if eq_pick:
        exp = exp[exp["Equipment_Tag_No."].isin(eq_pick)]

    expected = exp.groupby(
        ["Material_Code", "Material_Name", "UOM"], as_index=False,
    )["Allocated_Qty"].sum().rename(columns={"Allocated_Qty": "Expected_Qty"})

    # Actuals from ERP consumption ledger (SAP-keyed → Material_Code)
    conn = D.get_connection()
    try:
        params: list = [start.isoformat(), end.isoformat()]
        site_pred = ""
        if site_id:
            site_pred = " AND c.Site_ID = ?"
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

    if actual.empty and expected.empty:
        st.info("No data for the selected filters.")
        return

    cmp = expected.merge(actual, on="Material_Code", how="outer").fillna(0)
    cmp["Variance_Qty"] = (cmp["Actual_Consumed"] - cmp["Expected_Qty"]).round(3)
    cmp["Variance_Pct"] = (
        cmp["Variance_Qty"] / cmp["Expected_Qty"].replace(0, pd.NA) * 100
    ).round(1).fillna(0)

    # KPI strip
    rows_n = len(cmp)
    tot_exp = float(cmp["Expected_Qty"].sum())
    tot_act = float(cmp["Actual_Consumed"].sum())
    var = tot_act - tot_exp
    var_pct = (var / tot_exp * 100) if tot_exp else 0.0
    k = st.columns(4)
    k[0].metric("Rows", rows_n)
    k[1].metric("Total Expected", f"{tot_exp:,.3f}")
    k[2].metric("Total Actual", f"{tot_act:,.3f}")
    k[3].metric("Variance", f"{var:+,.3f}", f"{var_pct:+.1f}%")

    # Table
    def _style(row):
        try:
            p = float(row["Variance_Pct"])
        except (TypeError, ValueError):
            p = 0.0
        if abs(p) < 1:
            bg = "rgba(16,185,129,0.10)"  # on target
        elif p > 1:
            bg = "rgba(249,115,22,0.12)"  # over
        else:
            bg = "rgba(59,130,246,0.10)"  # under
        return [f"background-color:{bg}"] * len(row)
    if not cmp.empty:
        styled = cmp.style.apply(_style, axis=1).format({
            "Expected_Qty": "{:,.3f}",
            "Actual_Consumed": "{:,.3f}",
            "Variance_Qty": "{:+,.3f}",
            "Variance_Pct": "{:+.1f}%",
        })
        st.dataframe(styled, use_container_width=True, hide_index=True,
                     height=min(35 * (len(cmp) + 1) + 3, 460))
    st.caption(
        "Source: ERP `consumption` ledger (joined to `inventory` for "
        "Material_Code). Read-only — write path is the SK Consumption tab."
    )
    sme_download_pair(
        cmp, report_name="Consumption_Comparison",
        title="Consumption Comparison", key="t5_cmp",
        site_id=site_id, color_scheme="execution",
    )


# ---------------------------------------------------------------------------
# Tab dispatcher
# ---------------------------------------------------------------------------

def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    sub = st.radio(
        "View",
        ["⚙️ Execution Plan", "📋 Progress List", "📊 Consumption Comparison"],
        horizontal=True, key="_t5_sub",
    )
    st.markdown("---")
    if sub == "⚙️ Execution Plan":
        _render_execution_plan(site_id, priority_order, username)
    elif sub == "📋 Progress List":
        _render_progress_list(site_id)
    else:
        _render_consumption_comparison(site_id, priority_order)
