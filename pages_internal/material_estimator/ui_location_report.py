"""Tab 3 — Location Report."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
from .downloads import (
    build_location_report_excel,
    sme_custom_xlsx_download,
    sme_pdf_download,
)


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip = data_layer.load_equipment(site_id)
    if equip.empty:
        st.info("No equipment loaded.")
        return

    alloc, feas, _ = engine_runner.run_allocation(site_id, priority_order)

    view = st.radio(
        "View", ["📍 Location Based", "📋 All Equipment"],
        horizontal=True, key="_sme_loc_view",
    )

    if view == "📍 Location Based":
        locations = sorted(equip["Location"].dropna().unique().tolist())
        if not locations:
            st.warning("No locations on equipment master.")
            return
        loc = st.selectbox("Location", locations, key="_sme_loc_pick")
        tags_at_loc = set(
            equip[equip["Location"] == loc]["Equipment_Tag_No."].tolist()
        )
        feas_loc = feas[feas["Equipment_Tag_No."].isin(tags_at_loc)] if not feas.empty else feas
        alloc_loc = alloc[alloc["Equipment_Tag_No."].isin(tags_at_loc)] if not alloc.empty else alloc

        c1, c2, c3 = st.columns(3)
        c1.metric("Equipment at location", len(tags_at_loc))
        c2.metric("Fully ready", int(
            (feas_loc["Status"].str.contains("Fully Ready", na=False)).sum()
        ) if not feas_loc.empty else 0)
        c3.metric("Total demand qty", f"{alloc_loc['Demand_Qty'].sum():,.1f}"
                  if not alloc_loc.empty else "0")

        st.dataframe(
            feas_loc[[
                "Priority_Rank", "Equipment_Tag_No.", "Name",
                "Completion_Pct", "Status",
            ]],
            use_container_width=True, hide_index=True,
        )

        if not alloc_loc.empty:
            payload = build_location_report_excel(
                report_name=f"Location_{loc}",
                site_id=site_id,
                location=loc,
                feasibility_for_location=feas_loc,
                materials_for_location=alloc_loc,
            )
            sheets = [
                {"name": "Summary", "df": feas_loc,
                 "title": f"Equipment Feasibility — {loc}"},
                {"name": "Materials", "df": alloc_loc,
                 "title": f"Per-Material Allocation — {loc}"},
            ]
            c1, c2 = st.columns(2)
            with c1:
                sme_custom_xlsx_download(
                    f"⬇ Excel — {loc}",
                    payload,
                    report_name=f"Location_{loc}",
                    site_id=site_id,
                    key=f"loc_xlsx_{loc}",
                )
            with c2:
                sme_pdf_download(
                    f"⬇ PDF — {loc}",
                    sheets=sheets,
                    report_name=f"Location_{loc}",
                    site_id=site_id,
                    key=f"loc_pdf_{loc}",
                    title=f"Location Report — {loc}",
                )
    else:
        rollup = equip.groupby(
            ["Location"], dropna=False, as_index=False,
        ).agg(
            Equipment_Count=("Equipment_Tag_No.", "nunique"),
            Total_SQM=("Surface_Area_SQM", "sum"),
        )
        st.dataframe(rollup, use_container_width=True, hide_index=True)
