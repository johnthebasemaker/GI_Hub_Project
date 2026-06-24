"""Tab 8 — Master Data.

CRUD for SME-side master tables. Locations + Equipment Types live in the
existing system_settings table (categories 'sme_location',
'sme_equipment_type') — NO separate sme_locations / sme_types tables.

Restricted to HOD + Admin; the parent portal already exact-locks to those
two roles, so we don't re-check here.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import database as D

from . import data_layer


def _render_equipment_tab(site_id: str) -> None:
    eq = data_layer.load_equipment(site_id)
    st.caption(f"Site: **{site_id or '(global)'}** · {len(eq)} equipment rows")

    edit_mode = st.toggle("✏️ Edit mode", key="_sme_md_eq_edit")

    if edit_mode:
        st.warning(
            "Editing equipment master directly. Make sure changes are "
            "intentional — recipes that join on Lining_System_Code will "
            "follow this data."
        )
        edited = st.data_editor(
            eq, num_rows="dynamic", use_container_width=True,
            disabled=["Site_ID"], key="_sme_md_eq_editor",
        )
        if st.button("💾 Save equipment changes", type="primary"):
            conn = D.get_connection()
            try:
                conn.execute("DELETE FROM sme_equipment WHERE Site_ID = ?", (site_id,))
                for _, r in edited.iterrows():
                    if pd.isna(r.get("Equipment_Tag_No.")) or \
                       pd.isna(r.get("Lining_System_Code")):
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO sme_equipment "
                        "(Site_ID, Equipment_Tag_No, Name, Location, Type, "
                        " Substrate, Lining_System_Code, Surface_Area_SQM) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (site_id,
                         str(r["Equipment_Tag_No."]).strip(),
                         r.get("Name"),
                         r.get("Location"),
                         r.get("Type"),
                         r.get("Substrate"),
                         str(r["Lining_System_Code"]).strip(),
                         float(r.get("Surface_Area_SQM") or 0)),
                    )
                conn.commit()
            finally:
                conn.close()
            data_layer.bust_estimator_cache()
            st.success("✅ Saved.")
            st.rerun()
    else:
        st.dataframe(eq, use_container_width=True, hide_index=True)


def _render_recipe_tab() -> None:
    rec = data_layer.load_recipe()
    st.caption(f"{len(rec)} recipe rows (global — not site-scoped)")
    st.dataframe(rec, use_container_width=True, hide_index=True)
    st.info(
        "Recipes are bootstrapped from `For_1_SQM.xlsx` via "
        "`python3 scripts/sme_bootstrap.py`. Inline editing is disabled in "
        "this build — changes go through the bootstrap script."
    )


def _render_settings_tab(site_id: str, category: str, label: str) -> None:
    if category == "sme_location":
        values = D.get_sme_locations(site_id=site_id)
    else:
        values = D.get_sme_equipment_types(site_id=site_id)

    st.caption(f"Site: **{site_id}** · {len(values)} entries")
    if values:
        st.dataframe(
            pd.DataFrame({label: values}),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info(f"No {label.lower()} entries yet.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"➕ Add {label.lower()}")
        new_val = st.text_input(
            f"New {label.lower()}", key=f"_sme_add_{category}",
        )
        if st.button(f"Add", key=f"_sme_add_btn_{category}",
                     type="primary", use_container_width=True):
            if not new_val.strip():
                st.warning("Enter a value first.")
            else:
                inserted = D.add_sme_setting(category, new_val, site_id)
                if inserted:
                    st.success(f"Added '{new_val.strip()}'.")
                    st.rerun()
                else:
                    st.info("That value already exists for this site.")
    with c2:
        st.subheader(f"🗑️ Remove {label.lower()}")
        if values:
            to_remove = st.selectbox(
                f"Pick {label.lower()} to remove",
                values, key=f"_sme_del_{category}",
            )
            if st.button(f"Delete", key=f"_sme_del_btn_{category}",
                         use_container_width=True):
                n = D.delete_sme_setting(category, to_remove, site_id)
                if n:
                    st.success(f"Removed '{to_remove}'.")
                    st.rerun()
                else:
                    st.warning("Nothing was removed — value not found.")


def render(site_id: str, username: str | None) -> None:
    if not site_id:
        st.error("Master Data requires a site. Pick one from the sidebar.")
        return
    sub = st.radio(
        "Section",
        ["Equipment", "Recipe", "📍 Locations", "🔧 Equipment Types"],
        horizontal=True, key="_sme_md_sub",
    )
    if sub == "Equipment":
        _render_equipment_tab(site_id)
    elif sub == "Recipe":
        _render_recipe_tab()
    elif sub == "📍 Locations":
        _render_settings_tab(site_id, "sme_location", "Location")
    else:
        _render_settings_tab(site_id, "sme_equipment_type", "Equipment Type")
