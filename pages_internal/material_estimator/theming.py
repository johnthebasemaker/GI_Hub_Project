"""theming.py — light CSS for Material Estimator tabs."""
import streamlit as st


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .sme-pill {
          display: inline-block; padding: 2px 10px; border-radius: 12px;
          font-size: 12px; font-weight: 600; margin-right: 4px;
        }
        .sme-pill-full   { background:#DCFCE7; color:#166534; }
        .sme-pill-part   { background:#FEF3C7; color:#92400E; }
        .sme-pill-block  { background:#FEE2E2; color:#991B1B; }
        .sme-loc-chip {
          display:inline-block; padding:1px 8px; border-radius:8px;
          background:#E0E7FF; color:#3730A3; font-size:11px; font-weight:600;
        }
        .sme-bottleneck { color:#991B1B; font-weight:600; }
        .sme-header {
          background: linear-gradient(90deg, #FCD34D 0%, #FBBF24 100%);
          padding: 12px 18px; border-radius: 8px; margin-bottom: 12px;
          color: #1F2937; font-size: 18px; font-weight: 700;
        }
        .sme-site-banner {
          background:#F3F4F6; padding:8px 14px; border-radius:6px;
          font-size:13px; color:#374151; margin-bottom:10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_pill(status: str) -> str:
    if "Fully Ready" in status:
        return f'<span class="sme-pill sme-pill-full">✅ Ready</span>'
    if "Blocked" in status:
        return f'<span class="sme-pill sme-pill-block">🔴 Blocked</span>'
    return f'<span class="sme-pill sme-pill-part">🟡 Partial</span>'


def loc_chip(loc: str) -> str:
    safe = (loc or "—").replace("<", "").replace(">", "")
    return f'<span class="sme-loc-chip">📍 {safe}</span>'
