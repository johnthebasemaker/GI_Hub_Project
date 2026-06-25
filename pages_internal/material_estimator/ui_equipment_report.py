"""Tab 4 — Equipment Report (R19 SME-parity port).

Equipment-wise details only — surface area per system code. No materials,
no demand quantities.

Column order: Location → Type → Equipment Tag No. → Equipment Name →
System Code → System Name → Total SQM. Sorted by Location → Tag → Code.

Top KPIs: Equipment Tags · Locations · System Codes · Total SQM.

Per-location expandable list with per-equipment expanders, each containing
its own Excel + PDF download button using the location's color scheme.

Bottom: per-location Excel buttons + per-location PDF buttons + a
multi-sheet "All Equipment" workbook (each location its own sheet) plus
"All Equipment" + "All System Codes" consolidated sheets.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import data_layer, engine_runner
from .colors import scheme_for_location
from .downloads import (
    equipment_report_excel,
    sme_custom_xlsx_download,
    sme_pdf_download,
    sme_xlsx_download,
)
from .widgets import code_chip, loc_badge


def _build_master(equip: pd.DataFrame, recipe: pd.DataFrame) -> pd.DataFrame:
    if equip.empty:
        return pd.DataFrame()
    df = equip.copy()
    # Join short name from recipe
    sn = recipe[["Lining_System_Code", "Lining_System_Name"]].drop_duplicates()
    df = df.merge(sn, on="Lining_System_Code", how="left")
    df = df.rename(columns={
        "Equipment_Tag_No.": "Equipment Tag No.",
        "Name": "Equipment Name",
        "Lining_System_Code": "System Code",
        "Lining_System_Name": "System Name",
        "Surface_Area_SQM": "Total SQM",
    })
    col_order = [
        "Location", "Type", "Equipment Tag No.", "Equipment Name",
        "System Code", "System Name", "Total SQM",
    ]
    for c in col_order:
        if c not in df.columns:
            df[c] = None
    df = df[col_order]

    # Sort: Location, Tag, then numeric System Code where possible
    def _code_sort(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 9999
    df["_code_sort"] = df["System Code"].map(_code_sort)
    df = df.sort_values(
        ["Location", "Equipment Tag No.", "_code_sort"],
    ).drop(columns=["_code_sort"]).reset_index(drop=True)
    return df


def render(site_id: str | None, priority_order: list[str], username: str | None) -> None:
    equip = data_layer.load_equipment(site_id)
    recipe = data_layer.load_recipe()
    if equip.empty:
        st.info("No equipment loaded.")
        return

    master = _build_master(equip, recipe)
    if master.empty:
        st.info("No equipment rows.")
        return

    st.caption(
        "Equipment-wise details only — surface area per system code. "
        "No materials, no demand quantities."
    )

    # ── 4-cell KPI strip ───────────────────────────────────────────────────
    n_tags = master["Equipment Tag No."].nunique()
    n_locs = master["Location"].nunique()
    n_codes = master["System Code"].nunique()
    total_sqm = float(master["Total SQM"].sum())
    k = st.columns(4)
    k[0].metric("Equipment Tags", n_tags)
    k[1].metric("Locations", n_locs)
    k[2].metric("System Codes", n_codes)
    k[3].metric("Total SQM", f"{total_sqm:,.2f}")
    st.markdown("---")

    # ── Per-location expandable list ───────────────────────────────────────
    locs_in_data = sorted(master["Location"].dropna().unique())
    per_loc_sheets: list[dict] = []

    for loc_name in locs_in_data:
        loc_master = master[master["Location"] == loc_name]
        loc_tags = loc_master["Equipment Tag No."].dropna().unique()
        loc_sqm = float(loc_master["Total SQM"].sum())

        # Location header
        st.markdown(
            f"### {loc_badge(loc_name)} "
            f"&nbsp; <span style='font-size:13px;color:#374151;'>"
            f"{len(loc_tags)} tags · {loc_sqm:,.2f} SQM</span>",
            unsafe_allow_html=True,
        )

        # Per-equipment expanders
        for tag in loc_tags:
            tag_master = loc_master[loc_master["Equipment Tag No."] == tag]
            if tag_master.empty:
                continue
            first = tag_master.iloc[0]
            tag_name = first.get("Equipment Name") or ""
            tag_type = first.get("Type") or "—"
            n_codes_t = tag_master["System Code"].nunique()
            tag_sqm = float(tag_master["Total SQM"].sum())

            with st.expander(
                f"🏷 {tag} · {tag_name} · {tag_type} · "
                f"{n_codes_t} code(s) · {tag_sqm:,.2f} SQM",
                expanded=False,
            ):
                # Per-equipment Excel + PDF + Print
                cdl = st.columns(3)
                with cdl[0]:
                    sme_xlsx_download(
                        f"⬇ Excel — {tag}",
                        tag_master.copy(),
                        report_name=f"Equipment_{tag}",
                        site_id=site_id,
                        key=f"t4_xlsx_{tag}",
                        color_scheme=scheme_for_location(loc_name),
                        title=f"Equipment Report — {tag}",
                        sheet_name=str(tag)[:31],
                    )
                with cdl[1]:
                    sme_pdf_download(
                        f"⬇ PDF — {tag}",
                        df=tag_master.copy(),
                        report_name=f"Equipment_{tag}",
                        site_id=site_id, key=f"t4_pdf_{tag}",
                        color_scheme=scheme_for_location(loc_name),
                        title=f"Equipment Report — {tag}",
                    )
                with cdl[2]:
                    if st.button(
                        "🖨 Print", key=f"_t4_print_{tag}",
                        use_container_width=True,
                    ):
                        st.info(
                            "Use your browser Print (Ctrl/Cmd-P)."
                        )

                # Per-system-code rows
                for _, row in tag_master.iterrows():
                    code = str(row["System Code"])
                    sn = row["System Name"] or ""
                    sqm = float(row["Total SQM"])
                    st.markdown(
                        f"<div style='display:grid;grid-template-columns:auto 1fr auto;"
                        f"gap:10px;padding:4px 0;border-bottom:1px solid #F3F4F6;'>"
                        f"<span>{code_chip(code, '')}</span>"
                        f"<span style='font-size:13px;color:#374151;'>{sn}</span>"
                        f"<span style='text-align:right;font-weight:700;color:#F0C040;'>"
                        f"{sqm:,.2f} SQM</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        # Collect per-location sheet for the multi-sheet download
        per_loc_sheets.append({
            "name": loc_name[:31], "df": loc_master,
            "title": f"Equipment — {loc_name}",
            "color_scheme": scheme_for_location(loc_name),
        })

    # ── Per-location Excel + PDF buttons (consolidated row) ────────────────
    st.markdown("---")
    st.markdown("#### Per-location quick downloads")
    if per_loc_sheets:
        ncols = min(3, len(per_loc_sheets))
        rows = (len(per_loc_sheets) + ncols - 1) // ncols
        idx = 0
        for _ in range(rows):
            cols = st.columns(ncols)
            for c in range(ncols):
                if idx >= len(per_loc_sheets):
                    break
                sh = per_loc_sheets[idx]
                idx += 1
                with cols[c]:
                    cc = st.columns(2)
                    with cc[0]:
                        sme_xlsx_download(
                            f"⬇ {sh['name']} Excel",
                            sh["df"],
                            report_name=f"Equipment_{sh['name']}",
                            site_id=site_id,
                            key=f"t4_loc_x_{sh['name']}",
                            color_scheme=sh["color_scheme"],
                            title=sh["title"],
                            sheet_name=sh["name"],
                        )
                    with cc[1]:
                        sme_pdf_download(
                            f"⬇ {sh['name']} PDF",
                            df=sh["df"],
                            report_name=f"Equipment_{sh['name']}",
                            site_id=site_id,
                            key=f"t4_loc_p_{sh['name']}",
                            color_scheme=sh["color_scheme"],
                            title=sh["title"],
                        )

    # ── Multi-sheet Excel + PDF ─────────────────────────────────────────────
    st.markdown("---")
    all_eq_sheet = {"name": "All Equipment", "df": master,
                    "title": "All Equipment"}
    all_codes_df = master[[
        "System Code", "System Name",
    ]].drop_duplicates().sort_values("System Code")
    all_codes_sheet = {"name": "All System Codes", "df": all_codes_df,
                       "title": "All System Codes"}

    payload = equipment_report_excel(
        location_sheets=per_loc_sheets,
        all_eq_sheet=all_eq_sheet,
        include_all_codes_sheet=True,
        all_codes_sheet=all_codes_sheet,
    )
    mc = st.columns(2)
    with mc[0]:
        sme_custom_xlsx_download(
            "⬇ Excel — All Equipment (multi-sheet)",
            payload, report_name="All_Equipment",
            site_id=site_id, key="t4_all_x",
        )
    with mc[1]:
        all_sheets_pdf = per_loc_sheets + [
            {**all_eq_sheet, "color_scheme": "dashboard"},
            {**all_codes_sheet, "color_scheme": "overview"},
        ]
        sme_pdf_download(
            "⬇ PDF — All Equipment",
            sheets=all_sheets_pdf,
            report_name="All_Equipment", site_id=site_id,
            key="t4_all_p", title="All Equipment Report",
        )
