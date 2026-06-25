"""Tab 3 — Location Report (R19 SME-parity port).

Two sub-views:
  🌐 All Equipment    — single drag-sortable list across all locations
  📍 Location Based   — per-location drag-sortable + per-location color
                        scheme on Excel downloads
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer, engine_runner
from .colors import scheme_for_location
from .downloads import (
    location_report_excel,
    sme_custom_xlsx_download,
    sme_download_pair,
    sme_pdf_download,
)
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


_SESSION_KEY = "session_tags"
_ALL_EQ_KEY = "all_eq_order"
_LOC_ORDER_KEY = "loc_order"


def _ensure_session_tags(site_id: str | None) -> list[str]:
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = engine_runner.get_default_priority(site_id)
    return st.session_state[_SESSION_KEY]


def _tag_metric_row(
    tag: str, eq: pd.DataFrame, alloc: pd.DataFrame,
) -> tuple[str, str, float, float, float]:
    meta = eq[eq["Equipment_Tag_No."] == tag]
    name = (meta.iloc[0].get("Name") if not meta.empty else "") or ""
    loc = (meta.iloc[0].get("Location") if not meta.empty else "") or "—"
    sqm = float(meta["Surface_Area_SQM"].sum())
    tag_alloc = alloc[alloc["Equipment_Tag_No."] == tag]
    pct = float(tag_alloc["Fulfillment_Rate"].mean() * 100) if not tag_alloc.empty else 100.0
    can_sqm = sqm * pct / 100
    return name, loc, sqm, can_sqm, pct


def _render_equipment_expander(
    tag: str, eq: pd.DataFrame, recipe: pd.DataFrame, alloc: pd.DataFrame,
    *, key_prefix: str, color_scheme: str, site_id: str | None,
    on_add_to_session=None,
) -> None:
    name, loc, sqm, can_sqm, pct = _tag_metric_row(tag, eq, alloc)
    meta = eq[eq["Equipment_Tag_No."] == tag]
    if meta.empty:
        return
    first = meta.iloc[0]
    type_ = first.get("Type") or "—"
    substrate = first.get("Substrate") or "—"

    # Per-equipment Excel + PDF download (color_scheme honored)
    code_rows = []
    eq_alloc = alloc[alloc["Equipment_Tag_No."] == tag]
    for _, sysr in meta.iterrows():
        code = str(sysr["Lining_System_Code"])
        sys_sqm = float(sysr["Surface_Area_SQM"])
        sn_rows = recipe[recipe["Lining_System_Code"] == code]
        sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
        code_rows.append({
            "Equipment Tag No.": tag, "Equipment Name": name,
            "Location": loc, "System Code": code, "System Name": sn or "",
            "Total SQM": sys_sqm,
        })
    code_df = pd.DataFrame(code_rows)

    cols_dl = st.columns(2)
    with cols_dl[0]:
        sme_download_pair(
            code_df, report_name=f"Equipment_{tag}",
            title=f"Equipment {tag}", key=f"{key_prefix}_eq_{tag}",
            site_id=site_id, color_scheme=color_scheme,
            sheet_name=f"Equipment {tag}",
        )
    # Print HTML
    with cols_dl[1]:
        if st.button("🖨 Print", key=f"_print_{key_prefix}_{tag}",
                     use_container_width=True):
            st.session_state[f"_print_open_{tag}"] = True
            st.info(
                "Use your browser's Print (Ctrl/Cmd-P). "
                "CSS @media print rules hide the sidebar + tab strip."
            )

    # Readiness chip
    if pct >= 100:
        st.success("✅ All materials fully covered — ready to proceed")

    # Metadata
    md_cols = st.columns(4)
    md_cols[0].markdown(f"**Type:** {type_}")
    md_cols[1].markdown(f"**Substrate:** {substrate}")
    md_cols[2].markdown(f"**Total SQM:** {sqm:,.2f}")
    md_cols[3].markdown(loc_badge(loc), unsafe_allow_html=True)

    # Per system code blocks
    for _, sysr in meta.iterrows():
        code = str(sysr["Lining_System_Code"])
        sys_sqm = float(sysr["Surface_Area_SQM"])
        sn_rows = recipe[recipe["Lining_System_Code"] == code]
        sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
        sys_alloc = eq_alloc[eq_alloc["Material_Code"].isin(sn_rows["Material_Code"])]
        sys_pct = float(sys_alloc["Fulfillment_Rate"].mean() * 100) if not sys_alloc.empty else 100.0
        st.markdown(
            f"<div style='background:#F3F4F6;padding:6px 10px;"
            f"border-left:3px solid #6366F1;border-radius:4px;"
            f"margin-top:8px;'>"
            f"{status_dot(sys_pct)} {code_chip(code, sn or '')} "
            f"<span style='font-size:12px;color:#374151;'>"
            f"· {sys_sqm:,.2f} SQM</span> "
            f"<span style='float:right;'>{fulfil_pill(sys_pct)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if not sys_alloc.empty:
            plotly_mat_table(
                sys_alloc, key_suffix=f"{key_prefix}_{tag}_{code}",
                show_sqm=True, tag=tag, code=code,
                total_sqm_for_sc=sys_sqm,
                allocated_label="Available",
            )

    # Equipment total box
    if not eq_alloc.empty:
        tot_d = float(eq_alloc["Demand_Qty"].sum())
        tot_s = float(eq_alloc["Shortfall_Qty"].sum())
        st.markdown(
            f"<div style='background:#FEF3C7;padding:8px 12px;"
            f"border:1px solid #F0C040;border-radius:6px;margin-top:8px;"
            f"display:grid;grid-template-columns:repeat(4,1fr);gap:10px;"
            f"font-size:12px;color:#374151;'>"
            f"<div><b>Demand:</b> {tot_d:,.2f}</div>"
            + (f"<div><b>Shortfall:</b> {tot_s:,.2f}</div>" if tot_s > 0.001 else "<div></div>")
            + f"<div></div>"
            + f"<div style='text-align:right;'>{fulfil_pill(pct)}</div>"
            + "</div>",
            unsafe_allow_html=True,
        )

    # Add to session button
    if on_add_to_session:
        if st.button(
            "＋ Add to Session",
            key=f"_add_session_{key_prefix}_{tag}",
            use_container_width=True,
        ):
            on_add_to_session(tag)


def _drag_list(items: list[str], key: str) -> list[str]:
    """Return new order if changed, else original."""
    if not items:
        return items
    if not _HAS_SORTABLE:
        for it in items:
            st.write(it)
        return items
    new = sort_items(items, direction="vertical")
    return new if new and new != items else items


def _render_all_equipment(
    site_id: str | None, equip: pd.DataFrame,
    recipe: pd.DataFrame, alloc: pd.DataFrame,
    on_add_to_session,
) -> None:
    all_eq_tags = equip["Equipment_Tag_No."].drop_duplicates().tolist()
    if _ALL_EQ_KEY not in st.session_state:
        st.session_state[_ALL_EQ_KEY] = list(all_eq_tags)
    # Repair drift
    current = [t for t in st.session_state[_ALL_EQ_KEY] if t in all_eq_tags] \
        + [t for t in all_eq_tags if t not in st.session_state[_ALL_EQ_KEY]]
    st.session_state[_ALL_EQ_KEY] = current

    st.markdown("**Drag to set global build order across every location**")
    display = []
    for i, t in enumerate(current, start=1):
        meta = equip[equip["Equipment_Tag_No."] == t]
        loc = (meta.iloc[0].get("Location") if not meta.empty else "") or "—"
        sqm = float(meta["Surface_Area_SQM"].sum())
        display.append(f"#{i}  ||  {t}  ||  {loc}  ||  {sqm:,.1f} SQM")
    new_display = _drag_list(display, key="ae_sort_" + str(len(current)))
    if new_display and new_display != display:
        new_order = [d.split("||")[1].strip() for d in new_display]
        if set(new_order) == set(current):
            st.session_state[_ALL_EQ_KEY] = new_order
            engine_runner._cached_allocation.clear()
            st.rerun()

    # 5-cell KPI strip
    total_sqm = float(equip["Surface_Area_SQM"].sum())
    cov_by_tag = alloc.groupby("Equipment_Tag_No.")["Fulfillment_Rate"].mean() \
        if not alloc.empty else pd.Series(dtype=float)
    can_sqm = float((cov_by_tag * equip.groupby("Equipment_Tag_No.")["Surface_Area_SQM"].sum()).sum()) if not cov_by_tag.empty else 0.0
    overall = (can_sqm / total_sqm * 100) if total_sqm else 100.0

    k = st.columns(5)
    with k[0]:
        dbl_click_metric("Equipment", str(len(all_eq_tags)), "t3a_eq",
                         drilldown_df=equip)
    with k[1]:
        dbl_click_metric("Total SQM", f"{total_sqm:,.1f}", "t3a_sqm",
                         drilldown_df=equip)
    with k[2]:
        dbl_click_metric("Coverable SQM", f"{can_sqm:,.2f}", "t3a_cov",
                         drilldown_df=equip)
    with k[3]:
        dbl_click_metric("SQM Deficit", f"{max(0, total_sqm - can_sqm):,.2f}",
                         "t3a_def", drilldown_df=equip)
    with k[4]:
        dbl_click_metric("Overall Coverage", f"{overall:.1f}%", "t3a_ov",
                         drilldown_df=equip)

    # Per-equipment expanders
    for i, tag in enumerate(current, start=1):
        name, loc, sqm, can_sqm_e, pct = _tag_metric_row(tag, equip, alloc)
        with st.expander(
            f"{status_dot(pct)} #{i} {tag} · {name} · "
            f"{can_sqm_e:,.1f}/{sqm:,.1f} SQM · {pct:.1f}%",
            expanded=False,
        ):
            _render_equipment_expander(
                tag, equip, recipe, alloc,
                key_prefix="t3a", color_scheme="dashboard",
                site_id=site_id,
                on_add_to_session=on_add_to_session,
            )

    # Smart Suggestions
    with st.expander("🔮 Smart Reorder Suggestions", expanded=False):
        render_suggestion_panel(site_id, current, "tab3_all")

    # Full multi-sheet download
    if current and not alloc.empty:
        sheets = [
            {"name": "All Equipment", "df": equip[[
                "Equipment_Tag_No.", "Name", "Location", "Type",
                "Lining_System_Code", "Surface_Area_SQM",
            ]].rename(columns={"Equipment_Tag_No.": "Equipment Tag No."}),
             "color_scheme": "dashboard", "title": "All Equipment"},
            {"name": "Allocation", "df": alloc,
             "color_scheme": "dashboard", "title": "Allocation Detail"},
        ]
        sme_custom_xlsx_download(
            "⬇ Excel — All Equipment (multi-sheet)",
            location_report_excel(sheets),
            report_name="All_Equipment",
            site_id=site_id, key="t3a_all",
        )


def _render_location_based(
    site_id: str | None, equip: pd.DataFrame,
    recipe: pd.DataFrame, alloc: pd.DataFrame,
    on_add_to_session,
) -> None:
    if _LOC_ORDER_KEY not in st.session_state:
        st.session_state[_LOC_ORDER_KEY] = {}
    locs_in_data = sorted([l for l in equip["Location"].dropna().unique()])
    if not locs_in_data:
        st.info("No locations available in equipment master.")
        return

    per_loc_sheets: list[dict] = []
    for loc_name in locs_in_data:
        loc_eq = equip[equip["Location"] == loc_name]
        loc_tags_all = loc_eq["Equipment_Tag_No."].drop_duplicates().tolist()
        if not loc_tags_all:
            continue

        # Per-location session order
        existing = st.session_state[_LOC_ORDER_KEY].get(loc_name, [])
        current = [t for t in existing if t in loc_tags_all] \
            + [t for t in loc_tags_all if t not in existing]
        st.session_state[_LOC_ORDER_KEY][loc_name] = current

        # Color scheme
        scheme = scheme_for_location(loc_name)

        st.markdown("---")
        # Location header
        loc_sqm = float(loc_eq["Surface_Area_SQM"].sum())
        cov_by_tag = alloc.groupby("Equipment_Tag_No.")["Fulfillment_Rate"].mean() \
            if not alloc.empty else pd.Series(dtype=float)
        loc_can = float((cov_by_tag.reindex(loc_tags_all).fillna(0)
                         * loc_eq.groupby("Equipment_Tag_No.")["Surface_Area_SQM"].sum()
                         .reindex(loc_tags_all).fillna(0)).sum())
        loc_pct = (loc_can / loc_sqm * 100) if loc_sqm else 100.0
        st.markdown(
            f"### {status_dot(loc_pct)} {loc_badge(loc_name)} "
            f"&nbsp; <span style='font-size:13px;color:#374151;'>"
            f"{len(loc_tags_all)} tags · {loc_sqm:,.1f} SQM · "
            f"<b>{loc_pct:.1f}%</b></span>",
            unsafe_allow_html=True,
        )

        # Drag list per location
        st.caption(f"📍 {loc_name} — drag to set build priority")
        display = []
        for i, t in enumerate(current, start=1):
            sqm_t = float(loc_eq[loc_eq["Equipment_Tag_No."] == t]["Surface_Area_SQM"].sum())
            display.append(f"#{i}  ||  {t}  ||  {sqm_t:,.1f} SQM")
        sk = f"loc_sort_{loc_name}_" + "_".join(current)[:120]
        new = _drag_list(display, key=sk)
        if new and new != display:
            new_order = [d.split("||")[1].strip() for d in new]
            if set(new_order) == set(current):
                st.session_state[_LOC_ORDER_KEY][loc_name] = new_order
                engine_runner._cached_allocation.clear()
                st.rerun()

        # Per-equipment expanders
        for i, tag in enumerate(current, start=1):
            name, _loc, sqm, can_sqm_e, pct = _tag_metric_row(tag, equip, alloc)
            type_ = (loc_eq[loc_eq["Equipment_Tag_No."] == tag].iloc[0].get("Type")
                     if not loc_eq[loc_eq["Equipment_Tag_No."] == tag].empty else "—") or "—"
            substrate = (loc_eq[loc_eq["Equipment_Tag_No."] == tag].iloc[0].get("Substrate")
                         if not loc_eq[loc_eq["Equipment_Tag_No."] == tag].empty else "—") or "—"
            with st.expander(
                f"{status_dot(pct)} {tag} · {name} · {type_} | {substrate} · "
                f"{can_sqm_e:,.1f}/{sqm:,.1f} SQM · {pct:.1f}%",
                expanded=False,
            ):
                _render_equipment_expander(
                    tag, equip, recipe, alloc,
                    key_prefix=f"t3l_{loc_name}", color_scheme=scheme,
                    site_id=site_id,
                    on_add_to_session=on_add_to_session,
                )

        # Per-location Excel + PDF
        loc_code_rows = []
        for tag in current:
            meta = loc_eq[loc_eq["Equipment_Tag_No."] == tag]
            for _, sysr in meta.iterrows():
                code = str(sysr["Lining_System_Code"])
                sn_rows = recipe[recipe["Lining_System_Code"] == code]
                sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
                loc_code_rows.append({
                    "Equipment Tag No.": tag,
                    "Equipment Name": meta.iloc[0].get("Name") or "",
                    "System Code": code, "System Name": sn or "",
                    "Total SQM": float(sysr["Surface_Area_SQM"]),
                })
        if loc_code_rows:
            loc_df = pd.DataFrame(loc_code_rows)
            cdl = st.columns(2)
            with cdl[0]:
                from .downloads import sme_xlsx_download
                sme_xlsx_download(
                    f"⬇ Excel — {loc_name}",
                    loc_df, report_name=f"Location_{loc_name}",
                    site_id=site_id, key=f"t3l_xlsx_{loc_name}",
                    color_scheme=scheme,
                    title=f"Location Report — {loc_name}",
                    sheet_name=loc_name[:31],
                )
            with cdl[1]:
                sme_pdf_download(
                    f"⬇ PDF — {loc_name}",
                    df=loc_df, report_name=f"Location_{loc_name}",
                    site_id=site_id, key=f"t3l_pdf_{loc_name}",
                    color_scheme=scheme,
                    title=f"Location Report — {loc_name}",
                )
            per_loc_sheets.append({
                "name": loc_name[:31], "df": loc_df,
                "title": f"Location — {loc_name}",
                "color_scheme": scheme,
            })

        # Per-location smart suggestions
        if len(current) >= 2:
            with st.expander(
                f"🔮 Smart Reorder Suggestions ({loc_name})", expanded=False,
            ):
                render_suggestion_panel(site_id, current, f"tab3_{loc_name}")

    # Multi-location Excel + PDF
    if per_loc_sheets:
        st.markdown("---")
        cml = st.columns(2)
        with cml[0]:
            sme_custom_xlsx_download(
                "⬇ Excel — All Locations (Multi-Sheet)",
                location_report_excel(per_loc_sheets),
                report_name="All_Locations",
                site_id=site_id, key="t3l_all",
            )
        with cml[1]:
            sme_pdf_download(
                "⬇ PDF — All Locations",
                sheets=per_loc_sheets, report_name="All_Locations",
                site_id=site_id, key="t3l_all_pdf",
                title="All Locations",
            )


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    if equip.empty:
        st.info("No equipment loaded for this site.")
        return

    sub = st.radio(
        "View Mode", ["📍 Location Based", "🌐 All Equipment"],
        horizontal=True, key="_t3_mode",
    )

    # Allocation for either sub-view (use all-eq order for All Equipment;
    # session_tags for Location Based as the default for downstream calcs)
    if sub == "🌐 All Equipment":
        if _ALL_EQ_KEY not in st.session_state:
            st.session_state[_ALL_EQ_KEY] = equip["Equipment_Tag_No."].drop_duplicates().tolist()
        order = st.session_state[_ALL_EQ_KEY]
    else:
        order = _ensure_session_tags(site_id) or engine_runner.get_default_priority(site_id)

    alloc, _feas, _inv = engine_runner.run_allocation(site_id, order)

    def _add_to_session(tag: str) -> None:
        sess = _ensure_session_tags(site_id)
        if tag not in sess:
            sess.append(tag)
            st.session_state[_SESSION_KEY] = sess
            engine_runner._cached_allocation.clear()
            st.toast(f"Added {tag} to Session", icon="➕")
            st.rerun()
        else:
            st.toast(f"{tag} already in Session", icon="ℹ️")

    if sub == "🌐 All Equipment":
        _render_all_equipment(site_id, equip, recipe, alloc, _add_to_session)
    else:
        _render_location_based(site_id, equip, recipe, alloc, _add_to_session)
