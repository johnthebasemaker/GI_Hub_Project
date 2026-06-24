"""Tab 4 — Equipment Report."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
from .downloads import sme_secure_multi_sheet_xlsx_download, sme_secure_pdf_download


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    if equip.empty:
        st.info("No equipment loaded.")
        return

    alloc, feas, _ = engine_runner.run_allocation(site_id, priority_order)
    tags = equip["Equipment_Tag_No."].drop_duplicates().tolist()
    tag = st.selectbox("Equipment tag", tags, key="_sme_eq_report_tag")

    eq_rows = equip[equip["Equipment_Tag_No."] == tag]
    if eq_rows.empty:
        return

    # Per-system summary
    sys_summary = eq_rows[[
        "Equipment_Tag_No.", "Name", "Lining_System_Code",
        "Surface_Area_SQM",
    ]].copy()
    sys_summary = sys_summary.merge(
        recipe[["Lining_System_Code", "Lining_System_Name"]].drop_duplicates(),
        on="Lining_System_Code", how="left",
    )
    sys_summary = sys_summary.rename(columns={
        "Equipment_Tag_No.": "Equipment Tag No.",
        "Name": "Equipment Name",
        "Lining_System_Code": "System Code",
        "Lining_System_Name": "System Name",
        "Surface_Area_SQM": "Total SQM",
    })

    eq_summary = pd.DataFrame([{
        "Equipment Tag No.": tag,
        "Equipment Name": eq_rows.iloc[0]["Name"],
        "System Name": ", ".join(
            sys_summary["System Name"].dropna().astype(str).tolist()
        ),
        "Total SQM": float(eq_rows["Surface_Area_SQM"].sum()),
    }])

    detailed = alloc[alloc["Equipment_Tag_No."] == tag][[
        "Material_Code", "Material_Name", "UOM",
        "Demand_Qty", "Allocated_Qty", "Shortfall_Qty", "Fulfillment_Rate",
    ]] if not alloc.empty else pd.DataFrame()

    st.subheader("1) Summary by equipment")
    st.dataframe(eq_summary, use_container_width=True, hide_index=True)

    st.subheader("2) Summary by system code")
    st.dataframe(sys_summary, use_container_width=True, hide_index=True)

    st.subheader("3) Detailed material allocation")
    st.dataframe(detailed, use_container_width=True, hide_index=True)

    sheets = [
        {"name": "Equipment Summary", "df": eq_summary},
        {"name": "System Code Summary", "df": sys_summary},
        {"name": "Detailed Table", "df": detailed},
    ]
    c1, c2 = st.columns(2)
    with c1:
        sme_secure_multi_sheet_xlsx_download(
            f"⬇ Excel — {tag}",
            sheets, file_stem=f"SME_Equipment_{tag}",
            key=f"eqr_xlsx_{tag}", username=username,
        )
    with c2:
        sme_secure_pdf_download(
            f"⬇ PDF — {tag}",
            sheets=sheets, file_stem=f"SME_Equipment_{tag}",
            key=f"eqr_pdf_{tag}", username=username,
            title=f"Equipment Report — {tag}",
        )
