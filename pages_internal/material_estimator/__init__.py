"""Material Estimator portal — package entry point.

Round 17 merger of the standalone Smart Material Estimator (SME) into the
GI Hub ERP. This portal is a READ-ONLY projection over the ERP ledger:
- Available_Qty comes from `load_live_inventory()` (computed from receipts
  / consumption / returns, never stored).
- Ordered_Qty comes from `get_on_order_by_material()` (open PO outstanding).
- Recipe + Equipment master live in SME-prefixed tables seeded by
  `scripts/sme_bootstrap.py`.

Role-locked to {hod, admin} in main.py:_EXACT_ROLE_PAGES. The Master Data
tab is the only mutation surface; everything else is read-only.
"""
from __future__ import annotations

import streamlit as st

import database as D

from . import (
    ui_dashboard,
    ui_equipment_report,
    ui_execution_plan,
    ui_location_report,
    ui_master_data,
    ui_priority,
    ui_session_order,
    ui_total_overview,
)
from .theming import inject_css


def _resolve_site_id(user: dict) -> str | None:
    """For HOD, lock to user's bound site. For Admin, expose a picker
    (shadow pattern, mirrors warehouse_portal)."""
    role = (user.get("role") or "").lower()
    if role == "hod":
        return user.get("site_id") or "HQ"

    # Admin shadow — sidebar picker over distinct Site_IDs in the ERP.
    conn = D.get_connection()
    try:
        sites = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT Site_ID FROM users "
                "WHERE Site_ID IS NOT NULL ORDER BY Site_ID"
            ).fetchall()
        ]
    finally:
        conn.close()
    if not sites:
        sites = ["HQ"]

    default_site = user.get("site_id") or sites[0]
    if default_site not in sites:
        sites = [default_site] + sites
    return st.sidebar.selectbox(
        "🌐 SME shadow site",
        sites,
        index=sites.index(default_site),
        key="_sme_admin_shadow_site",
        help="Admin only — pick which site's plan to view.",
    )


def page_material_estimator(user: dict) -> None:
    inject_css()
    role = (user.get("role") or "").lower()
    username = user.get("username")
    site_id = _resolve_site_id(user)

    st.markdown(
        '<div class="sme-header">🧪 Material Estimator</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="sme-site-banner">Scope: <b>Site {site_id}</b>'
        f' · Role: <b>{role}</b></div>',
        unsafe_allow_html=True,
    )

    priority_order = ui_priority.get_current_priority(site_id)

    tabs = st.tabs([
        "📊 Dashboard",
        "🔍 Selective Equipment Entry",
        "📦 Session Order Report",
        "📍 Location Report",
        "📋 Equipment Report",
        "⚙️ Execution Plan",
        "📈 Total Overview",
        "🗄️ Master Data",
    ])
    with tabs[0]:
        ui_dashboard.render(site_id, priority_order, username)
    with tabs[1]:
        ui_priority.render(site_id, username)
    with tabs[2]:
        ui_session_order.render(site_id, priority_order, username)
    with tabs[3]:
        ui_location_report.render(site_id, priority_order, username)
    with tabs[4]:
        ui_equipment_report.render(site_id, priority_order, username)
    with tabs[5]:
        ui_execution_plan.render(site_id, priority_order, username)
    with tabs[6]:
        ui_total_overview.render(site_id, priority_order, username)
    with tabs[7]:
        ui_master_data.render(site_id, username)


__all__ = ["page_material_estimator"]
