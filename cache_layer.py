"""
cache_layer.py — Streamlit-side read cache for hot DB queries
==============================================================
Thin wrappers around the read-only functions in `database.py`.

Why this module exists
----------------------
`database.py` is the pure Python data-access layer and intentionally has
ZERO Streamlit imports (see CLAUDE.md / ARCHITECTURE.md). To gain Streamlit's
`@st.cache_data` benefits without polluting `database.py`, we put the cached
versions here and route hot reads through them from `main.py`.

Contract preserved
------------------
- SQL and math inside `database.py` are NEVER touched by this module.
- These wrappers only invoke the existing functions and cache their results.
- Every cached read opens its own short-lived connection (so the wrapper
  signature stays hashable — `sqlite3.Connection` is not hashable, and a
  caller-provided conn would silently defeat the cache anyway).

Invalidation
------------
Call `bust_inventory_cache()` after any write that could affect live stock,
low-stock, burn-rate, or short-dated calculations — EOD commit, receipt
approval, cart submit, inventory edit, etc. The dropdown caches
(`cached_work_types`, `cached_sites`) live longer and are cleared by
`bust_settings_cache()` when an admin edits those settings.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from database import (
    get_work_types,
    get_tank_nos,
    load_live_inventory,
    get_low_stock_items,
    get_burn_rate_and_forecast,
    get_sites,
    get_short_dated_stock,
    get_item_snapshot,
    get_fefo_lots,
    get_inventory_valuation,
    get_total_inventory_value,
    get_value_by_site,
    get_consumption_value_window,
)


# ---------------------------------------------------------------------------
# LIVE / TRANSACTIONAL READS — short TTL (data changes via EOD, receipts, etc.)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def cached_live_inventory(site_id: Optional[str] = None) -> pd.DataFrame:
    """Cached wrapper for database.load_live_inventory. TTL 30s."""
    return load_live_inventory(conn=None, site_id=site_id)


@st.cache_data(ttl=30, show_spinner=False)
def cached_low_stock_items(site_id: Optional[str] = None) -> pd.DataFrame:
    """Cached wrapper for database.get_low_stock_items. TTL 30s."""
    return get_low_stock_items(conn=None, site_id=site_id)


@st.cache_data(ttl=60, show_spinner=False)
def cached_short_dated_stock(site_id: Optional[str] = None) -> pd.DataFrame:
    """Cached wrapper for database.get_short_dated_stock. TTL 60s."""
    return get_short_dated_stock(conn=None, site_id=site_id)


@st.cache_data(ttl=30, show_spinner=False)
def cached_fefo_lots(
    sap_code: str,
    site_id: Optional[str] = None,
):
    """
    Cached wrapper for database.get_fefo_lots. TTL 30s.
    Same invalidation guarantees as cached_item_snapshot — busted by
    bust_inventory_cache() after any write that affects stock.
    """
    return get_fefo_lots(sap_code=sap_code, site_id=site_id, conn=None)


@st.cache_data(ttl=30, show_spinner=False)
def cached_item_snapshot(
    sap_code: str,
    site_id: Optional[str] = None,
    lookback_days: int = 30,
) -> dict:
    """
    Cached wrapper for database.get_item_snapshot. TTL 30s.

    Used by the Scan-to-Inspect panel — read-only, hashable arg set, opens
    its own short-lived connection. Invalidated by `bust_inventory_cache()`
    so post-EOD / post-receipt views stay accurate.
    """
    return get_item_snapshot(
        sap_code=sap_code,
        site_id=site_id,
        lookback_days=lookback_days,
        conn=None,
    )


@st.cache_data(ttl=300, show_spinner=False)
def cached_burn_rate_and_forecast(
    site_id: Optional[str] = None,
    lookback_days: int = 30,
) -> pd.DataFrame:
    """Cached wrapper for database.get_burn_rate_and_forecast. TTL 5min."""
    return get_burn_rate_and_forecast(
        conn=None, site_id=site_id, lookback_days=lookback_days
    )


# ---------------------------------------------------------------------------
# SETTINGS / DROPDOWN READS — long TTL (admin-managed, rarely edited)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def cached_work_types(site_id: Optional[str] = None) -> list[str]:
    """Cached wrapper for database.get_work_types. TTL 10min."""
    return get_work_types(conn=None, site_id=site_id)


@st.cache_data(ttl=600, show_spinner=False)
def cached_tank_nos(site_id: Optional[str] = None) -> list[str]:
    """Cached wrapper for database.get_tank_nos. TTL 10min."""
    return get_tank_nos(conn=None, site_id=site_id)


@st.cache_data(ttl=600, show_spinner=False)
def cached_sites() -> list[str]:
    """Cached wrapper for database.get_sites. TTL 10min."""
    return get_sites(conn=None)


# ---------------------------------------------------------------------------
# INVALIDATION HELPERS
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def cached_inventory_valuation(site_id: Optional[str] = None) -> pd.DataFrame:
    """Cached per-item valuation. TTL 60s."""
    return get_inventory_valuation(site_id=site_id)


@st.cache_data(ttl=60, show_spinner=False)
def cached_total_inventory_value(site_id: Optional[str] = None) -> float:
    """Cached SAR rollup for KPI cards. TTL 60s."""
    return get_total_inventory_value(site_id=site_id)


@st.cache_data(ttl=60, show_spinner=False)
def cached_value_by_site() -> pd.DataFrame:
    """Per-site rollup — feeds Admin Overview. TTL 60s."""
    return get_value_by_site()


@st.cache_data(ttl=120, show_spinner=False)
def cached_consumption_value(site_id: Optional[str] = None,
                             days: int = 30) -> float:
    """Cached N-day consumption SAR value. TTL 120s."""
    return get_consumption_value_window(site_id=site_id, days=days)


def bust_inventory_cache() -> None:
    """
    Clears all caches whose underlying data depends on inventory / receipts /
    consumption / returns. Call after any write that touches those tables.
    """
    cached_live_inventory.clear()
    cached_low_stock_items.clear()
    cached_short_dated_stock.clear()
    cached_burn_rate_and_forecast.clear()
    cached_item_snapshot.clear()
    cached_fefo_lots.clear()
    cached_inventory_valuation.clear()
    cached_total_inventory_value.clear()
    cached_value_by_site.clear()
    cached_consumption_value.clear()


def bust_settings_cache() -> None:
    """
    Clears dropdown caches sourced from system_settings.
    Call after an admin/HOD edits Work_Type, Tank_No, or Site values.
    """
    cached_work_types.clear()
    cached_tank_nos.clear()
    cached_sites.clear()


def bust_all_caches() -> None:
    """Nuclear option — clears every cache_layer entry. Useful after restore/import."""
    bust_inventory_cache()
    bust_settings_cache()
