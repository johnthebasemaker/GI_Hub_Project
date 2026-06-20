"""
pages_internal — Page modules extracted from main.py
=====================================================
Each module contributes ONE page function with the same signature it had
when it lived in main.py. The router in main.py imports and calls them
unchanged, so RBAC and routing behaviour are preserved.

Why "pages_internal" and not "pages"
------------------------------------
Streamlit auto-discovers any folder literally named `pages/` and turns its
contents into multi-page navigation entries. We have a custom sidebar + RBAC
gate in main.py and do NOT want Streamlit's auto-page sidebar. Naming the
folder `pages_internal` avoids that auto-discovery.

Re-exports
----------
`from pages_internal import page_live_dashboard, page_daily_issue_log, ...`
works at the package level so main.py's import block is short.
"""

from .live_dashboard import page_live_dashboard
from .reports_page import page_reports
from .daily_issue_log import page_daily_issue_log
from .hod_portal import page_hod_portal
from .admin_portal import page_admin_portal
from .logistics_portal import page_logistics_portal
from .warehouse_portal import page_warehouse_portal
from .supervisor_portal import page_supervisor_portal

__all__ = [
    "page_live_dashboard",
    "page_reports",
    "page_daily_issue_log",
    "page_hod_portal",
    "page_admin_portal",
    "page_logistics_portal",
    "page_warehouse_portal",
    "page_supervisor_portal",
]
