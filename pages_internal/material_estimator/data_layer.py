"""
data_layer.py — Material Estimator data adapter

Single entry-point for fetching the three DataFrames the allocation engine
needs (equipment, recipe, inventory). All reads route through database.py
helpers added in Round 17 Phase 1. Caches are keyed on site_id so the admin
shadow site picker invalidates cleanly.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import database as D


# Cache TTL is short so an EOD commit shows up in the estimator within a
# minute even if bust_inventory_cache wasn't called by every code path.
_TTL_SECONDS = 60


@st.cache_data(show_spinner=False, ttl=_TTL_SECONDS)
def load_inventory_view(site_id: str | None) -> pd.DataFrame:
    """Live inventory shaped for the allocation engine.
    Columns: Material_Code, Material_Name, UOM, Nature, Available_Qty,
             Ordered_Qty."""
    return D.get_sme_inventory_view(site_id=site_id)


@st.cache_data(show_spinner=False, ttl=_TTL_SECONDS)
def load_equipment(site_id: str | None) -> pd.DataFrame:
    """Equipment master for the given site, engine-shaped columns."""
    return D.get_sme_equipment(site_id=site_id)


@st.cache_data(show_spinner=False, ttl=_TTL_SECONDS)
def load_recipe() -> pd.DataFrame:
    """Lining-system recipe master (global)."""
    return D.get_sme_recipe()


@st.cache_data(show_spinner=False, ttl=_TTL_SECONDS)
def load_sqm_progress(site_id: str | None) -> pd.DataFrame:
    """Per-(tag × system) Original_SQM + Done_SQM for this site."""
    return D.get_sme_sqm_progress(site_id=site_id)


def bust_estimator_cache() -> None:
    """Call after any write to SME tables. Hooked by Master Data CRUD."""
    load_inventory_view.clear()
    load_equipment.clear()
    load_recipe.clear()
    load_sqm_progress.clear()


def build_estimator_inputs(site_id: str | None) -> tuple[
    pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
]:
    """Bundle the three engine inputs + the progress frame in one call.

    Returns:
        equip   : Equipment master (Equipment_Tag_No., Lining_System_Code,
                  Surface_Area_SQM, etc.)
        recipe  : Recipe master (Lining_System_Code, Material_Code, For_1_SQM,
                  …)
        inv     : Inventory view (Material_Code, Available_Qty, Ordered_Qty,
                  …)
        prog    : Progress (Equipment_Tag_No., Lining_System_Code,
                  Original_SQM, Done_SQM)
    """
    equip = load_equipment(site_id)
    recipe = load_recipe()
    inv = load_inventory_view(site_id)
    prog = load_sqm_progress(site_id)
    # Adjust effective Surface_Area_SQM by subtracting done progress so the
    # engine's demand reflects remaining-to-build work, not original-total.
    if not equip.empty and not prog.empty:
        eq = equip.merge(
            prog[["Equipment_Tag_No.", "Lining_System_Code", "Done_SQM"]],
            on=["Equipment_Tag_No.", "Lining_System_Code"],
            how="left",
        )
        eq["Done_SQM"] = eq["Done_SQM"].fillna(0.0)
        eq["Surface_Area_SQM"] = (
            eq["Surface_Area_SQM"] - eq["Done_SQM"]
        ).clip(lower=0)
        eq = eq.drop(columns=["Done_SQM"])
        equip = eq
    return equip, recipe, inv, prog
