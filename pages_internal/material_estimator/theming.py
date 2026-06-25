"""theming.py — ERP-branded SME UI shell.

Reproduces the SME's sticky-header chain (title bar + tab strip + sub-view
radio all pinned while scrolling) but recolored to the ERP yellow/amber
palette. No SME dark/light toggle — ERP has its own theming.
"""
from __future__ import annotations

import base64
import os

import streamlit as st

_ERP_AMBER = "#FBBF24"
_ERP_AMBER_DARK = "#D97706"
_ERP_AMBER_LIGHT = "#FEF3C7"
_ERP_TEXT = "#1F2937"
_ERP_TEXT_SOFT = "#374151"

_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "sme_logo.png")


def _logo_data_uri() -> str:
    try:
        with open(_LOGO_PATH, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except FileNotFoundError:
        return ""


def inject_css() -> None:
    """Inject SME-style sticky chain + status pills + KPI tile polish,
    recolored to the ERP brand."""
    st.markdown(
        f"""
        <style>
        /* ── Sticky chain: title bar (top), tabs, sub-view radio ── */
        .sme-sticky-title {{
          position: sticky;
          top: 0;
          z-index: 999;
          background: linear-gradient(90deg, {_ERP_AMBER} 0%, {_ERP_AMBER_DARK} 100%);
          padding: 10px 18px;
          margin: -8px -8px 12px -8px;
          border-radius: 0 0 8px 8px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.08);
          display: flex;
          align-items: center;
          gap: 14px;
        }}
        .sme-sticky-title img {{
          height: 38px;
          width: auto;
          background: white;
          padding: 2px 6px;
          border-radius: 4px;
        }}
        .sme-sticky-title .sme-title-text {{
          color: {_ERP_TEXT};
          font-weight: 700;
          font-size: 18px;
          line-height: 1.1;
        }}
        .sme-sticky-title .sme-title-sub {{
          color: {_ERP_TEXT_SOFT};
          font-size: 12px;
          margin-top: 2px;
        }}
        .sme-sticky-title .sme-title-chip {{
          margin-left: auto;
          background: white;
          color: {_ERP_TEXT};
          padding: 4px 10px;
          border-radius: 12px;
          font-size: 11px;
          font-weight: 700;
          letter-spacing: 0.5px;
        }}

        /* Tabs themselves sticky just below the title bar */
        section[data-testid="stMain"] [data-testid="stTabs"] > div:first-of-type {{
          position: sticky;
          top: 76px;
          z-index: 998;
          background: var(--background-color, #FFFFFF);
          padding-top: 4px;
          padding-bottom: 4px;
          border-bottom: 1px solid rgba(0,0,0,0.06);
        }}

        /* Tab labels — amber underline on active */
        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{
          color: {_ERP_AMBER_DARK} !important;
          font-weight: 700 !important;
        }}
        [data-testid="stTabs"] button[role="tab"][aria-selected="true"]::after {{
          background: {_ERP_AMBER} !important;
        }}

        /* Sub-view radio (the first horizontal stRadio inside a tab panel)
           pinned just below the tab strip. Uses :has() — Chromium 105+. */
        [data-baseweb="tab-panel"] > div > div:first-child:has([data-testid="stRadio"]) {{
          position: sticky;
          top: 122px;
          z-index: 997;
          background: var(--background-color, #FFFFFF);
          padding: 4px 0;
        }}

        /* KPI tile (st.popover styled as metric) — amber accent */
        button[data-testid="stPopover"] > div {{
          border: 1px solid rgba(251,191,36,0.25) !important;
          background: linear-gradient(180deg, #FFFFFF 0%, {_ERP_AMBER_LIGHT}33 100%) !important;
        }}
        button[data-testid="stPopover"] > div:hover {{
          border-color: {_ERP_AMBER} !important;
        }}

        /* Drag-priority sortable tighter width */
        .sme-compact-sortable {{
          max-width: 460px;
        }}

        /* Page header strip (kept for backward compat with R17 banner) */
        .sme-header {{
          display: none;
        }}
        .sme-site-banner {{
          display: none;
        }}

        /* Primary button — ERP amber */
        div[data-testid="stButton"] > button[kind="primary"] {{
          background: {_ERP_AMBER} !important;
          color: {_ERP_TEXT} !important;
          border-color: {_ERP_AMBER_DARK} !important;
          font-weight: 700 !important;
        }}
        div[data-testid="stButton"] > button[kind="primary"]:hover {{
          background: {_ERP_AMBER_DARK} !important;
          color: #FFFFFF !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sticky_title(
    title: str,
    *,
    site_id: str | None = None,
    role: str | None = None,
) -> None:
    """Render the SME-style sticky title bar with ERP logo + scope chip."""
    logo_uri = _logo_data_uri()
    logo_html = (
        f'<img src="{logo_uri}" alt="SME" />' if logo_uri else ""
    )
    sub_parts = []
    if site_id:
        sub_parts.append(f"Site: <b>{site_id}</b>")
    if role:
        sub_parts.append(f"Role: <b>{role}</b>")
    sub_html = " · ".join(sub_parts)
    chip_html = (
        f'<span class="sme-title-chip">v3 · ERP-MERGED</span>'
    )
    st.markdown(
        f"""
        <div class="sme-sticky-title">
          {logo_html}
          <div>
            <div class="sme-title-text">🧪 {title}</div>
            <div class="sme-title-sub">{sub_html}</div>
          </div>
          {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# Round 17 names kept as no-op aliases so any external caller doesn't break.
def status_pill(status: str) -> str:
    from .widgets import fulfil_pill
    if "Fully Ready" in status:
        return fulfil_pill(100)
    if "Blocked" in status:
        return fulfil_pill(0)
    # Try to parse "(XX.X%)" out of the status
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", status)
    return fulfil_pill(float(m.group(1)) if m else 50.0)


def loc_chip(loc: str) -> str:
    from .widgets import loc_badge
    return loc_badge(loc)
