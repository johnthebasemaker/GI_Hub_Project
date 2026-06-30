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

Lazy re-exports (PEP 562)
-------------------------
`from pages_internal import page_live_dashboard, page_material_estimator, ...`
works at the package level. The page modules are imported LAZILY via
`__getattr__` — NOT eagerly here. Eager imports during this `__init__` left a
window where Streamlit's polling module-loader could observe the package
half-built and raise `cannot import name 'page_material_estimator'` at cold
start (especially with the heavy 7,200-LOC estimator module). Deferring each
import until after `__init__` fully completes removes that race entirely.
"""
from __future__ import annotations

import importlib

# Exported name → submodule that defines it.
_PAGE_MODULES = {
    "page_live_dashboard":    "live_dashboard",
    "page_reports":           "reports_page",
    "page_daily_issue_log":   "daily_issue_log",
    "page_hod_portal":        "hod_portal",
    "page_admin_portal":      "admin_portal",
    "page_logistics_portal":  "logistics_portal",
    "page_warehouse_portal":  "warehouse_portal",
    "page_supervisor_portal": "supervisor_portal",
    "page_material_estimator": "material_estimator_portal",
    "page_manhour_portal":    "manhour_portal",
}

__all__ = list(_PAGE_MODULES)


def __getattr__(name: str):
    """Import the owning submodule on first access (after __init__ completes)."""
    mod = _PAGE_MODULES.get(name)
    if mod is None:
        raise AttributeError(f"module 'pages_internal' has no attribute {name!r}")
    module = importlib.import_module(f".{mod}", __name__)
    return getattr(module, name)


def __dir__():
    return sorted(__all__)
