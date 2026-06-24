"""engine_runner.py — cached engine wrappers.

The allocation engine is pure pandas. Caching its output saves a few hundred
ms on every priority-order rerun.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer


@st.cache_data(show_spinner=False, ttl=60)
def _cached_allocation(
    site_id: str | None,
    priority_tuple: tuple,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    equip, recipe, inv, _ = data_layer.build_estimator_inputs(site_id)
    if equip.empty or recipe.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    demand, inv_clean = AE.build_demand_matrix(equip, recipe, inv)
    if demand.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    alloc = AE.allocate_sequential(demand, inv_clean, list(priority_tuple))
    feas  = AE.compute_feasibility(alloc)
    return alloc, feas, inv_clean


def run_allocation(
    site_id: str | None,
    priority_order: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (allocation_df, feasibility_df, cleaned_inventory_df)."""
    return _cached_allocation(site_id, tuple(priority_order))


def get_default_priority(site_id: str | None) -> list[str]:
    """Default priority = order rows appear in the equipment master."""
    eq = data_layer.load_equipment(site_id)
    if eq.empty:
        return []
    return eq["Equipment_Tag_No."].drop_duplicates().tolist()


def procurement_list(
    site_id: str | None,
    priority_order: list[str],
) -> pd.DataFrame:
    alloc, _, inv = run_allocation(site_id, priority_order)
    if alloc.empty:
        return pd.DataFrame()
    proc = AE.build_procurement_list(alloc, inv)
    # Layer on Ordered_Qty so the procurement card reflects open POs.
    inv_full = data_layer.load_inventory_view(site_id)
    if not inv_full.empty and "Ordered_Qty" in inv_full.columns:
        proc = proc.merge(
            inv_full[["Material_Code", "Ordered_Qty"]],
            on="Material_Code", how="left",
        )
        proc["Ordered_Qty"] = proc["Ordered_Qty"].fillna(0.0)
        proc["Net_Shortfall"] = (
            proc["Shortage_Qty_To_Buy"] - proc["Ordered_Qty"]
        ).clip(lower=0).round(3)
    return proc
