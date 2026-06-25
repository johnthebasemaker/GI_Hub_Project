"""Tab 8 — Master Data (R19 SME-parity port).

Five sub-views via st.radio:
  • Equipment                      — full CRUD against sme_equipment table
  • LINING SYSTEM MATERIAL CONSM   — full CRUD against sme_recipe table
  • Materials_DetailsAvailable_Qty — read-only view of ERP `inventory` joined
                                     with v_inventory_with_sme (is_sme flag)
  • ➕ Add Location                — system_settings 'sme_location' CRUD
  • ➕ Add Type                    — system_settings 'sme_equipment_type' CRUD

All CRUD goes to SME-specific tables OR system_settings — never to the ERP
ledger (pending_issues / consumption / receipts / returns) so the routing
rule is preserved.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

import database as D

from . import data_layer
from .colors import scheme_for_table
from .downloads import sme_xlsx_download, sme_pdf_download


_DEFAULT_LOCATIONS = {"Brown Field", "TRAIN J", "TRAIN K"}
_DEFAULT_TYPES = {"Vessel", "Tank", "Column", "Pipe", "Reactor"}


# ---------------------------------------------------------------------------
# Sub-view: Equipment CRUD
# ---------------------------------------------------------------------------

def _render_equipment_crud(site_id: str) -> None:
    eq = data_layer.load_equipment(site_id)
    locs = D.get_sme_locations(site_id=site_id)
    types = D.get_sme_equipment_types(site_id=site_id)

    # ── Add Equipment ──────────────────────────────────────────────────────
    with st.expander("➕ Add new equipment", expanded=False):
        # System code multiselect — codes available in recipe master
        recipe = data_layer.load_recipe()
        all_codes = sorted(recipe["Lining_System_Code"].astype(str).unique())
        code_opts = [
            f"Code {c} — {recipe[recipe['Lining_System_Code'] == c].iloc[0].get('Lining_System_Name') or ''}"
            for c in all_codes
        ]
        picked_labels = st.multiselect(
            "Lining System Codes",
            code_opts, default=[],
            key="seq_codes_pre",
        )
        picked_codes = [lbl.split(" — ")[0].replace("Code", "").strip()
                        for lbl in picked_labels]

        # Identity
        c1, c2 = st.columns(2)
        with c1:
            new_tag = st.text_input("Equipment Tag No.*", key="seq_tag")
        with c2:
            new_loc = st.selectbox(
                "Location*", ["— pick —"] + locs, key="seq_loc",
            )

        # Shared metadata
        c3, c4, c5 = st.columns(3)
        with c3:
            new_name = st.text_input("Equipment Name", key="seq_name")
        with c4:
            new_type = st.selectbox(
                "Type", ["— pick —"] + types, key="seq_type",
            )
        with c5:
            new_substrate = st.text_input("Substrate", key="seq_substrate")

        # Per-code SQM inputs
        per_code_sqm: dict[str, float] = {}
        for code in picked_codes:
            sn_rows = recipe[recipe["Lining_System_Code"] == code]
            sn = sn_rows.iloc[0].get("Lining_System_Name") if not sn_rows.empty else ""
            per_code_sqm[code] = st.number_input(
                f"Surface_Area_SQM for Code {code} ({sn})",
                min_value=0.0, step=1.0,
                key=f"seq_sqm_{code}",
            )

        if st.button("💾 Save Equipment", type="primary",
                     key="seq_save"):
            if not new_tag.strip():
                st.error("Equipment Tag No. is required.")
            elif new_loc == "— pick —":
                st.error("Pick a Location.")
            elif not picked_codes:
                st.error("Pick at least one Lining System Code.")
            elif any(v <= 0 for v in per_code_sqm.values()):
                st.error("All per-code Surface_Area_SQM values must be > 0.")
            else:
                conn = D.get_connection()
                try:
                    for code, sqm in per_code_sqm.items():
                        conn.execute(
                            "INSERT OR IGNORE INTO sme_equipment "
                            "(Site_ID, Equipment_Tag_No, Name, Location, Type, "
                            " Substrate, Lining_System_Code, Surface_Area_SQM) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (site_id, new_tag.strip(), new_name.strip() or None,
                             new_loc, new_type if new_type != "— pick —" else None,
                             new_substrate.strip() or None, code, float(sqm)),
                        )
                        D.upsert_sme_sqm_progress(
                            site_id=site_id,
                            equipment_tag=new_tag.strip(),
                            lining_system_code=code,
                            original_sqm=float(sqm),
                            conn=conn,
                        )
                    conn.commit()
                finally:
                    conn.close()
                data_layer.bust_estimator_cache()
                st.success(f"✅ Saved {new_tag} with {len(picked_codes)} code(s).")
                st.rerun()

    # ── Search + view + edit + delete grid ─────────────────────────────────
    st.markdown("---")
    st.markdown("#### Existing equipment")

    # Search filter
    cs1, cs2 = st.columns([3, 1])
    with cs1:
        q = st.text_input("Search", key="md_eq_search", placeholder="tag, name, location...")
    with cs2:
        if eq.empty:
            cols_for_filter = []
        else:
            cols_for_filter = [c for c in eq.columns if c != "Site_ID"]
        search_col = st.selectbox(
            "in column", ["(any)"] + cols_for_filter, key="md_eq_search_col",
        )
    view = eq.copy()
    if q.strip():
        qs = q.strip().lower()
        if search_col == "(any)":
            view = view[view.astype(str).apply(
                lambda r: r.str.lower().str.contains(qs).any(), axis=1,
            )]
        elif search_col in view.columns:
            view = view[view[search_col].astype(str).str.lower().str.contains(qs)]

    # Editable grid with checkbox column
    if view.empty:
        st.info("No equipment rows.")
    else:
        # Add a sl.no + checkbox column
        edit_df = view.reset_index(drop=True).copy()
        edit_df.insert(0, "Sl. No.", range(1, len(edit_df) + 1))
        edit_df.insert(1, "☐ Select", False)
        edited = st.data_editor(
            edit_df,
            disabled=["Sl. No.", "Site_ID", "Equipment_Tag_No.",
                      "Lining_System_Code"],
            column_config={
                "☐ Select": st.column_config.CheckboxColumn("☐ Select"),
            },
            hide_index=True, use_container_width=True,
            num_rows="fixed",
            key="md_eq_editor",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save edits", key="md_eq_save_edits"):
                editor_state = st.session_state.get(
                    "md_eq_editor", {}
                )
                edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}
                if not edited_rows:
                    st.info("No edits detected.")
                else:
                    conn = D.get_connection()
                    n = 0
                    try:
                        for row_idx, changes in edited_rows.items():
                            row = edit_df.iloc[int(row_idx)]
                            tag = row["Equipment_Tag_No."]
                            code = row["Lining_System_Code"]
                            # Map editable columns
                            updates = {}
                            for col, val in changes.items():
                                if col in ("Sl. No.", "☐ Select", "Site_ID",
                                           "Equipment_Tag_No.",
                                           "Lining_System_Code"):
                                    continue
                                updates[col] = val
                            if updates:
                                set_clause = ", ".join(
                                    f'"{c}" = ?' for c in updates
                                )
                                conn.execute(
                                    f"UPDATE sme_equipment SET {set_clause} "
                                    "WHERE Site_ID=? AND Equipment_Tag_No=? "
                                    "AND Lining_System_Code=?",
                                    (*updates.values(), site_id, tag, code),
                                )
                                n += 1
                                if "Surface_Area_SQM" in updates:
                                    D.upsert_sme_sqm_progress(
                                        site_id=site_id,
                                        equipment_tag=tag,
                                        lining_system_code=code,
                                        original_sqm=float(updates["Surface_Area_SQM"]),
                                        conn=conn,
                                    )
                        conn.commit()
                    finally:
                        conn.close()
                    data_layer.bust_estimator_cache()
                    st.success(f"✅ Saved {n} row(s).")
                    st.rerun()
        with c2:
            if st.button("🗑 Delete selected", key="md_eq_delete"):
                checked = edited[edited["☐ Select"] == True]
                if checked.empty:
                    st.info("Nothing checked.")
                else:
                    conn = D.get_connection()
                    n = 0
                    try:
                        for _, r in checked.iterrows():
                            conn.execute(
                                "DELETE FROM sme_equipment "
                                "WHERE Site_ID=? AND Equipment_Tag_No=? "
                                "AND Lining_System_Code=?",
                                (site_id, r["Equipment_Tag_No."],
                                 r["Lining_System_Code"]),
                            )
                            conn.execute(
                                "DELETE FROM sme_sqm_progress "
                                "WHERE Site_ID=? AND Equipment_Tag_No=? "
                                "AND Lining_System_Code=?",
                                (site_id, r["Equipment_Tag_No."],
                                 r["Lining_System_Code"]),
                            )
                            n += 1
                        conn.commit()
                    finally:
                        conn.close()
                    data_layer.bust_estimator_cache()
                    st.success(f"✅ Removed {n} row(s).")
                    st.rerun()

        # Per-table download
        sme_xlsx_download(
            f"⬇ Excel — Equipment", view,
            report_name="Master_Equipment", site_id=site_id,
            key="md_eq_xlsx",
            color_scheme=scheme_for_table("sme_equipment"),
            title="Equipment Master",
        )


# ---------------------------------------------------------------------------
# Sub-view: Recipe CRUD
# ---------------------------------------------------------------------------

def _render_recipe_crud(site_id: str) -> None:
    rec = data_layer.load_recipe()
    st.caption(f"{len(rec)} recipe rows (global — not site-scoped).")

    with st.expander("➕ Add recipe row", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            r_code = st.text_input("Lining_System_Code*", key="rec_code")
        with c2:
            r_mc = st.text_input("Material_Code*", key="rec_mc")
        with c3:
            r_for1 = st.number_input("For_1_SQM*", min_value=0.0,
                                     step=0.01, key="rec_for1")
        c4, c5, c6 = st.columns(3)
        with c4:
            r_sn = st.text_input("Lining_System_Name", key="rec_sn")
        with c5:
            r_mn = st.text_input("Material_Name", key="rec_mn")
        with c6:
            r_uom = st.text_input("UOM", key="rec_uom")
        r_nature = st.text_input("Nature (optional)", key="rec_nature")
        if st.button("💾 Save Recipe Row", type="primary", key="rec_save"):
            if not (r_code.strip() and r_mc.strip() and r_for1 > 0):
                st.error("Lining_System_Code, Material_Code, and For_1_SQM are required.")
            else:
                conn = D.get_connection()
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO sme_recipe "
                        "(Lining_System_Code, Lining_System_Name, "
                        " Material_Code, Material_Name, UOM, Nature, "
                        " For_1_SQM) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (r_code.strip(), r_sn.strip() or None,
                         r_mc.strip(), r_mn.strip() or None,
                         r_uom.strip() or None, r_nature.strip() or None,
                         float(r_for1)),
                    )
                    conn.commit()
                finally:
                    conn.close()
                data_layer.bust_estimator_cache()
                st.success(f"✅ Saved recipe row {r_code}/{r_mc}.")
                st.rerun()

    # Editable grid
    st.markdown("---")
    if rec.empty:
        st.info("No recipe rows.")
        return
    edit_df = rec.copy()
    edit_df.insert(0, "Sl. No.", range(1, len(edit_df) + 1))
    edit_df.insert(1, "☐ Select", False)
    edited = st.data_editor(
        edit_df,
        disabled=["Sl. No.", "Lining_System_Code", "Material_Code"],
        column_config={
            "☐ Select": st.column_config.CheckboxColumn("☐ Select"),
        },
        hide_index=True, use_container_width=True,
        num_rows="fixed",
        key="md_rec_editor",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save edits", key="md_rec_save_edits"):
            editor_state = st.session_state.get("md_rec_editor", {})
            edited_rows = editor_state.get("edited_rows", {}) if isinstance(editor_state, dict) else {}
            if not edited_rows:
                st.info("No edits detected.")
            else:
                conn = D.get_connection()
                n = 0
                try:
                    for row_idx, changes in edited_rows.items():
                        row = edit_df.iloc[int(row_idx)]
                        updates = {
                            k: v for k, v in changes.items()
                            if k not in ("Sl. No.", "☐ Select",
                                         "Lining_System_Code", "Material_Code")
                        }
                        if updates:
                            set_clause = ", ".join(
                                f'"{c}" = ?' for c in updates
                            )
                            conn.execute(
                                f"UPDATE sme_recipe SET {set_clause} "
                                "WHERE Lining_System_Code=? AND Material_Code=?",
                                (*updates.values(), row["Lining_System_Code"],
                                 row["Material_Code"]),
                            )
                            n += 1
                    conn.commit()
                finally:
                    conn.close()
                data_layer.bust_estimator_cache()
                st.success(f"✅ Saved {n} recipe row(s).")
                st.rerun()
    with c2:
        if st.button("🗑 Delete selected", key="md_rec_delete"):
            checked = edited[edited["☐ Select"] == True]
            if checked.empty:
                st.info("Nothing checked.")
            else:
                conn = D.get_connection()
                n = 0
                try:
                    for _, r in checked.iterrows():
                        conn.execute(
                            "DELETE FROM sme_recipe "
                            "WHERE Lining_System_Code=? AND Material_Code=?",
                            (r["Lining_System_Code"], r["Material_Code"]),
                        )
                        n += 1
                    conn.commit()
                finally:
                    conn.close()
                data_layer.bust_estimator_cache()
                st.success(f"✅ Removed {n} recipe row(s).")
                st.rerun()

    sme_xlsx_download(
        f"⬇ Excel — Recipe", rec,
        report_name="Master_Recipe", site_id=site_id,
        key="md_rec_xlsx",
        color_scheme=scheme_for_table("sme_recipe"),
        title="Recipe Master (LINING SYSTEM MATERIAL CONSM)",
    )


# ---------------------------------------------------------------------------
# Sub-view: Materials_DetailsAvailable_Qty (read-only ERP join)
# ---------------------------------------------------------------------------

def _render_inventory_read(site_id: str) -> None:
    st.caption(
        "Read-only view of the ERP `inventory` master with the computed "
        "`is_sme` flag (1 if the material participates in any "
        "`sme_recipe` row). Inventory is authored on the ERP's Material "
        "Details tab; this view shows the derived SME flag."
    )
    conn = D.get_connection()
    try:
        df = pd.read_sql(
            "SELECT SAP_Code, Material_Code, Equipment_Description, "
            "       UOM, Minimum_Qty, Opening_Stock, Category, is_sme "
            "FROM v_inventory_with_sme ORDER BY SAP_Code",
            conn,
        )
    finally:
        conn.close()
    if df.empty:
        st.info("No inventory rows.")
        return
    f = st.columns(3)
    with f[0]:
        q = st.text_input("Search", key="md_inv_q",
                          placeholder="SAP, Material_Code, description…")
    with f[1]:
        sme_only = st.checkbox("SME-flagged only", key="md_inv_sme_only")
    with f[2]:
        cat_opts = ["All"] + sorted([c for c in df["Category"].dropna().unique()])
        cat_pick = st.selectbox("Category", cat_opts, key="md_inv_cat")
    view = df.copy()
    if sme_only:
        view = view[view["is_sme"] == 1]
    if cat_pick != "All":
        view = view[view["Category"] == cat_pick]
    if q.strip():
        qs = q.strip().lower()
        view = view[view.astype(str).apply(
            lambda r: r.str.lower().str.contains(qs).any(), axis=1,
        )]
    st.dataframe(view, use_container_width=True, hide_index=True,
                 height=min(35 * (len(view) + 1) + 3, 460))
    sme_xlsx_download(
        f"⬇ Excel — Inventory",
        view, report_name="Materials_DetailsAvailable_Qty",
        site_id=site_id, key="md_inv_xlsx",
        color_scheme=scheme_for_table("inventory"),
        title="Materials_DetailsAvailable_Qty",
    )


# ---------------------------------------------------------------------------
# Sub-view: ➕ Add Location  (system_settings 'sme_location')
# ---------------------------------------------------------------------------

def _render_location_crud(site_id: str) -> None:
    locs = D.get_sme_locations(site_id=site_id)
    st.caption(f"{len(locs)} locations for site **{site_id}**")
    if locs:
        st.dataframe(pd.DataFrame({"Location": locs}),
                     use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("➕ Add Location")
        new_val = st.text_input("Location Name", key="md_loc_new")
        if st.button("💾 Save Location", type="primary",
                     key="md_loc_save", use_container_width=True):
            if not new_val.strip():
                st.warning("Enter a value first.")
            else:
                inserted = D.add_sme_setting(
                    "sme_location", new_val.strip(), site_id,
                )
                if inserted:
                    st.success(f"Added '{new_val.strip()}'.")
                    st.rerun()
                else:
                    st.info("That value already exists for this site.")
    with c2:
        st.subheader("🗑 Remove Location")
        if locs:
            to_remove = st.selectbox(
                "Pick a location", locs, key="md_loc_del",
            )
            if to_remove in _DEFAULT_LOCATIONS:
                st.caption("ℹ️ This is a default location — it's safe to remove "
                           "from this site; the HQ seed copy remains.")
            if st.button("Delete", key="md_loc_del_btn",
                         use_container_width=True):
                n = D.delete_sme_setting(
                    "sme_location", to_remove, site_id,
                )
                if n:
                    st.success(f"Removed '{to_remove}'.")
                    st.rerun()
                else:
                    st.warning("Nothing was removed.")


# ---------------------------------------------------------------------------
# Sub-view: ➕ Add Type  (system_settings 'sme_equipment_type')
# ---------------------------------------------------------------------------

def _render_type_crud(site_id: str) -> None:
    types = D.get_sme_equipment_types(site_id=site_id)
    st.caption(f"{len(types)} equipment types for site **{site_id}**")
    if types:
        st.dataframe(pd.DataFrame({"Equipment Type": types}),
                     use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("➕ Add Type")
        new_val = st.text_input("Type Name", key="md_type_new")
        if st.button("💾 Save Type", type="primary",
                     key="md_type_save", use_container_width=True):
            if not new_val.strip():
                st.warning("Enter a value first.")
            else:
                inserted = D.add_sme_setting(
                    "sme_equipment_type", new_val.strip(), site_id,
                )
                if inserted:
                    st.success(f"Added '{new_val.strip()}'.")
                    st.rerun()
                else:
                    st.info("That value already exists for this site.")
    with c2:
        st.subheader("🗑 Remove Type")
        if types:
            to_remove = st.selectbox(
                "Pick a type", types, key="md_type_del",
            )
            if to_remove in _DEFAULT_TYPES:
                st.caption("ℹ️ This is a default type — it's safe to remove "
                           "from this site; the HQ seed copy remains.")
            if st.button("Delete", key="md_type_del_btn",
                         use_container_width=True):
                n = D.delete_sme_setting(
                    "sme_equipment_type", to_remove, site_id,
                )
                if n:
                    st.success(f"Removed '{to_remove}'.")
                    st.rerun()
                else:
                    st.warning("Nothing was removed.")


# ---------------------------------------------------------------------------
# Tab dispatcher
# ---------------------------------------------------------------------------

def render(site_id: str, username: str | None) -> None:
    if not site_id:
        st.error("Master Data requires a site context. Pick one from the sidebar.")
        return
    st.caption(
        "🔐 Master Data writes only touch SME-specific tables (sme_equipment, "
        "sme_recipe, sme_sqm_progress) and system_settings. The ERP ledger "
        "(pending_issues, consumption, receipts, returns) is never modified "
        "from this surface — that's the routing rule from Round 18."
    )
    sub = st.radio(
        "Select Table to Manage",
        [
            "Equipment",
            "LINING SYSTEM MATERIAL CONSM",
            "Materials_DetailsAvailable_Qty",
            "➕ Add Location",
            "➕ Add Type",
        ],
        horizontal=True, key="_t8_sub",
    )
    st.markdown("---")
    if sub == "Equipment":
        _render_equipment_crud(site_id)
    elif sub == "LINING SYSTEM MATERIAL CONSM":
        _render_recipe_crud(site_id)
    elif sub == "Materials_DetailsAvailable_Qty":
        _render_inventory_read(site_id)
    elif sub == "➕ Add Location":
        _render_location_crud(site_id)
    else:
        _render_type_crud(site_id)
