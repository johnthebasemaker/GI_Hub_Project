"""Tab 1 — Selective Equipment Entry (R19 SME-parity port).

Left column (1/2.65): filters (Location · Type · Code) + tag search + add +
session priority sortable + per-tag rows with remove buttons.

Right column (1.65/2.65): selected tag detail card + per-system-code
expanders with KPI metrics + plotly material table.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
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


def get_current_priority(site_id: str | None) -> list[str]:
    stored = st.session_state.get(_STATE_KEY)
    if stored:
        return list(stored)
    return engine_runner.get_default_priority(site_id)


def _ensure_state(site_id: str | None) -> list[str]:
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = engine_runner.get_default_priority(site_id)
    return st.session_state[_STATE_KEY]


def _render_left_panel(
    eq: pd.DataFrame,
    site_id: str | None,
    alloc: pd.DataFrame,
) -> str | None:
    # Filters
    all_locs = sorted([l for l in eq["Location"].dropna().unique()])
    all_types = sorted([t for t in eq["Type"].dropna().unique()])
    locs = st.multiselect(
        "Location", all_locs, default=[],
        placeholder="All locations", key="t1_loc",
    )
    types_scope = (
        sorted(eq[eq["Location"].isin(locs)]["Type"].dropna().unique())
        if locs else all_types
    )
    types = st.multiselect(
        "Type", types_scope, default=[],
        placeholder="All types", key="t1_type",
    )
    scoped = eq.copy()
    if locs:
        scoped = scoped[scoped["Location"].isin(locs)]
    if types:
        scoped = scoped[scoped["Type"].isin(types)]
    scoped_codes = sorted(scoped["Lining_System_Code"].astype(str).unique())
    codes = st.multiselect(
        "System Code", scoped_codes, default=[],
        placeholder="All codes", key="t1_code",
    )
    if codes:
        scoped = scoped[scoped["Lining_System_Code"].astype(str).isin(codes)]

    # Tag search + add
    scoped_tags = scoped["Equipment_Tag_No."].drop_duplicates().tolist()
    tag_options = [
        f"{t} — {scoped[scoped['Equipment_Tag_No.'] == t].iloc[0].get('Name') or ''}"
        for t in scoped_tags
    ]
    tag_pick = st.selectbox(
        "Search tag",
        ["— pick a tag —"] + tag_options,
        index=0, key="tag_select",
    )
    selected_tag = None
    if tag_pick and tag_pick != "— pick a tag —":
        selected_tag = tag_pick.split(" — ")[0].strip()
    session_tags = _ensure_state(site_id)
    is_in_session = selected_tag in session_tags if selected_tag else False
    if selected_tag:
        st.caption("✓ Already in session" if is_in_session else " ")
    if st.button(
        "＋ Add to Session", type="primary",
        disabled=not selected_tag or is_in_session,
        key="add_btn", use_container_width=True,
    ):
        session_tags.append(selected_tag)
        st.session_state[_STATE_KEY] = session_tags
        engine_runner._cached_allocation.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("**Session Priority List**")
    st.caption("Drag to reorder. Top = highest priority.")

    if not session_tags:
        st.info("Add tags above to begin building the priority list.")
        return selected_tag

    # Per-tag display labels with priority # + name + loc
    display_labels = []
    for i, t in enumerate(session_tags, start=1):
        meta = eq[eq["Equipment_Tag_No."] == t]
        name = (meta.iloc[0].get("Name") if not meta.empty else "") or ""
        loc  = (meta.iloc[0].get("Location") if not meta.empty else "") or ""
        display_labels.append(f"#{i}  ||  {t}  ||  {name}  ||  {loc}")

    if _HAS_SORTABLE:
        sortable_key = "sess_sort_" + "_".join(session_tags)[:120]
        new_order_labels = sort_items(display_labels, direction="vertical")
        if new_order_labels and new_order_labels != display_labels:
            new_order = []
            for lbl in new_order_labels:
                # tag is the 2nd field after splitting on '||'
                parts = lbl.split("||")
                if len(parts) >= 2:
                    new_order.append(parts[1].strip())
            if new_order and set(new_order) == set(session_tags):
                st.session_state[_STATE_KEY] = new_order
                engine_runner._cached_allocation.clear()
                st.rerun()
    else:
        st.warning(
            "`streamlit-sortables` not installed — drag UX unavailable."
        )
        for lbl in display_labels:
            st.write(lbl)

    # Per-tag fulfillment + remove
    st.markdown("**Per-tag fulfillment**")
    for i, t in enumerate(session_tags):
        meta = eq[eq["Equipment_Tag_No."] == t]
        name = (meta.iloc[0].get("Name") if not meta.empty else "") or ""
        if alloc is not None and not alloc.empty:
            mat = alloc[alloc["Equipment_Tag_No."] == t]
            pct = float(mat["Fulfillment_Rate"].mean() * 100) if not mat.empty else 0.0
        else:
            pct = 0.0
        row = st.columns([0.6, 5.4, 2, 0.8])
        row[0].markdown(f"**#{i+1}**")
        row[1].markdown(f"{status_dot(pct)} `{t}` — {name}",
                        unsafe_allow_html=True)
        row[2].markdown(fulfil_pill(pct), unsafe_allow_html=True)
        if row[3].button("✕", key=f"_t1_rm_{t}", help="Remove from session"):
            session_tags.remove(t)
            st.session_state[_STATE_KEY] = session_tags
            engine_runner._cached_allocation.clear()
            st.rerun()

    if st.button("Clear All", key="_t1_clear",
                 use_container_width=True):
        st.session_state[_STATE_KEY] = []
        engine_runner._cached_allocation.clear()
        st.rerun()
    return selected_tag


def _render_right_panel(
    selected_tag: str | None,
    eq: pd.DataFrame,
    recipe: pd.DataFrame,
    alloc: pd.DataFrame,
) -> None:
    if not selected_tag:
        st.markdown(
            "<div style='display:flex;justify-content:center;align-items:center;"
            "height:300px;border:2px dashed #D1D5DB;border-radius:10px;"
            "color:#9CA3AF;font-size:14px;'>"
            "Pick a tag in the left panel to see its detail."
            "</div>",
            unsafe_allow_html=True,
        )
        return
    meta = eq[eq["Equipment_Tag_No."] == selected_tag]
    if meta.empty:
        st.warning(f"Tag {selected_tag} not in current equipment master.")
        return
    first = meta.iloc[0]
    name = first.get("Name") or ""
    loc  = first.get("Location") or "—"
    type_ = first.get("Type") or "—"
    substrate = first.get("Substrate") or "—"

    # Info card
    st.markdown(
        f"""
        <div style="border:1px solid #F0C040;background:#FFFBEB;
        border-radius:10px;padding:14px 16px;">
          <div style="font-size:13px;color:#A16207;font-weight:700;">
            Equipment Tag
          </div>
          <div style="font-size:22px;color:#1F2937;font-weight:800;">
            {selected_tag}
          </div>
          <div style="margin-top:6px;">{loc_badge(loc)}</div>
          <div style="font-size:14px;color:#374151;margin-top:8px;">
            <b>{name}</b>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;
                      margin-top:10px;font-size:12px;color:#4B5563;">
            <div><b>Type:</b> {type_}</div>
            <div><b>Substrate:</b> {substrate}</div>
            <div><b>Codes:</b> {meta['Lining_System_Code'].nunique()}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(" ")

    # Per-system-code expanders
    for _, sysr in meta.iterrows():
        code = str(sysr["Lining_System_Code"])
        sqm = float(sysr["Surface_Area_SQM"])
        sn_rows = recipe[recipe["Lining_System_Code"] == code]
        short_name = ""
        if not sn_rows.empty:
            short_name = sn_rows.iloc[0].get("Lining_System_Name") or ""
        sys_alloc = alloc[
            (alloc["Equipment_Tag_No."] == selected_tag)
            & (alloc["Material_Code"].isin(sn_rows["Material_Code"]))
        ] if not sn_rows.empty else pd.DataFrame()
        if not sys_alloc.empty:
            pct = float(sys_alloc["Fulfillment_Rate"].mean() * 100)
        else:
            pct = 100.0
        with st.expander(
            f"System Code {code} · {short_name} · {sqm:,.2f} SQM · "
            f"Coverage: {pct:.1f}%",
            expanded=False,
        ):
            mcols = st.columns(4)
            mcols[0].metric("System Code", code)
            mcols[1].metric("Short Name", short_name or "—")
            mcols[2].metric("Surface Area", f"{sqm:,.2f}")
            mcols[3].metric("Coverage", f"{pct:.1f}%")
            if not sys_alloc.empty:
                plotly_mat_table(
                    sys_alloc, key_suffix=f"t1_{selected_tag}_{code}",
                    show_sqm=True, tag=selected_tag, code=code,
                    total_sqm_for_sc=sqm,
                    allocated_label="Allocated",
                )

    # Equipment grand total
    eq_alloc = alloc[alloc["Equipment_Tag_No."] == selected_tag]
    if not eq_alloc.empty:
        total_demand = float(eq_alloc["Demand_Qty"].sum())
        total_alloc = float(eq_alloc["Allocated_Qty"].sum())
        eq_pct = (total_alloc / total_demand * 100) if total_demand else 100.0
        st.markdown(
            f"""
            <div style="background:#FEF3C7;border:1px solid #F0C040;
            border-radius:8px;padding:10px 14px;margin-top:12px;
            display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
            font-size:12px;color:#374151;">
              <div><b># System Codes:</b> {meta['Lining_System_Code'].nunique()}</div>
              <div><b>Total Demand:</b> {total_demand:,.2f}</div>
              <div><b>Coverage:</b> {eq_pct:.1f}%</div>
              <div style="text-align:right;">{fulfil_pill(eq_pct)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render(site_id: str | None, username: str | None) -> None:
    eq = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    if eq.empty:
        st.info("No equipment loaded for this site.")
        return
    session_tags = _ensure_state(site_id)
    order = session_tags or engine_runner.get_default_priority(site_id)
    alloc, _feas, _inv = engine_runner.run_allocation(site_id, order)

    left, right = st.columns([1, 1.65])
    with left:
        selected = _render_left_panel(eq, site_id, alloc)
    with right:
        _render_right_panel(selected, eq, recipe, alloc)
