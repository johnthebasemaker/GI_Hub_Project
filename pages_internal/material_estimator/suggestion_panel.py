"""suggestion_panel.py — Port of SME's `_run_suggestion_engine` + UI panel.

Analyzes reordering scenarios:
- By Equipment: simulate pausing each not-fully-ready tag; pick the best one
  to unlock the most additional fully-buildable tags.
- By System Code: roll up suggestions per equipment.

Two-column rendering matches the SME layout.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import allocation_engine as AE
from . import data_layer


def _run_suggestion_engine(
    site_id: str | None,
    priority_order: list[str],
) -> dict:
    equip, recipe, inv, _ = data_layer.build_estimator_inputs(site_id)
    if equip.empty or recipe.empty or not priority_order:
        return {"eq_suggestions": [], "sc_suggestions": []}
    demand, inv_clean = AE.build_demand_matrix(equip, recipe, inv)
    if demand.empty:
        return {"eq_suggestions": [], "sc_suggestions": []}
    sugg_df, _best = AE.run_suggestion_engine(demand, inv_clean, priority_order)
    if sugg_df is None or sugg_df.empty:
        return {"eq_suggestions": [], "sc_suggestions": []}
    eq_suggestions = []
    for _, row in sugg_df.iterrows():
        eq_suggestions.append({
            "pause_tag": row["Pause_Tag"],
            "pause_name": row["Pause_Name"],
            "newly_completable_count": int(row["Newly_Completable_Count"]),
            "newly_completable_tags": row["Newly_Completable_Tags"],
            "avg_completion_gain_pct": float(row["Avg_Completion_Gain_Pct"]),
            "recommended": bool(row["Recommended"]),
        })
    # System Code suggestions: aggregate per system code across all tags
    sc_demand = demand.groupby("Material_Code", as_index=False).agg(
        total_demand=("Demand_Qty", "sum"),
    )
    sc_suggestions = []
    if "Lining_System_Code" in equip.columns:
        sc_tag_pairs = equip[
            ["Equipment_Tag_No.", "Lining_System_Code"]
        ].drop_duplicates()
        by_code = sc_tag_pairs.groupby("Lining_System_Code").size().reset_index(
            name="tag_count",
        )
        for _, row in by_code.sort_values("tag_count", ascending=False).head(5).iterrows():
            sc_suggestions.append({
                "code": row["Lining_System_Code"],
                "tag_count": int(row["tag_count"]),
            })
    return {"eq_suggestions": eq_suggestions, "sc_suggestions": sc_suggestions}


def render_suggestion_panel(
    site_id: str | None,
    priority_order: list[str],
    panel_key: str,
) -> None:
    """Two-column panel (By Equipment | By System Code). Matches SME UI."""
    if not priority_order:
        return
    if not st.button(
        "🔮 Run Smart Reorder Suggestion",
        key=f"_sme_sugg_btn_{panel_key}",
    ):
        st.caption(
            "Click to analyze whether pausing certain equipment can unlock "
            "more fully-buildable tags downstream."
        )
        return

    sug = _run_suggestion_engine(site_id, priority_order)
    eq = sug["eq_suggestions"]
    sc = sug["sc_suggestions"]
    if not eq and not sc:
        st.info("Nothing to suggest — every tag is fully ready.")
        return

    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown(
            "#### By Equipment  "
            "<span style='color:#6B7280;font-size:12px;'>"
            "(top scenarios — pause tag → unlock more)</span>",
            unsafe_allow_html=True,
        )
        if not eq:
            st.caption("No pause scenarios applicable.")
        for s in eq[:6]:
            recommended_chip = (
                '<span style="background:#F0C040;color:#000;padding:1px 8px;'
                'border-radius:10px;font-size:11px;font-weight:700;">⭐ BEST</span> '
                if s["recommended"] else ""
            )
            unlocks = (
                f"Unlocks <b>{s['newly_completable_count']}</b> tag(s): "
                f"<span style='color:#10B981;font-size:12px;'>{s['newly_completable_tags']}</span>"
                if s["newly_completable_tags"] != "—"
                else f"Avg gain <b>{s['avg_completion_gain_pct']:.1f}%</b>"
            )
            st.markdown(
                f"<div style='border:1px solid rgba(0,0,0,0.08);"
                f"border-radius:8px;padding:8px 10px;margin-bottom:6px;"
                f"background:#FAFAFA;'>"
                f"{recommended_chip}"
                f"<b>Pause:</b> {s['pause_tag']} — {s['pause_name']}<br/>"
                f"<span style='font-size:12px;color:#374151;'>{unlocks}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    with c_right:
        st.markdown(
            "#### By System Code  "
            "<span style='color:#6B7280;font-size:12px;'>"
            "(most-used systems in this plan)</span>",
            unsafe_allow_html=True,
        )
        if not sc:
            st.caption("No system-code roll-up available.")
        for s in sc:
            st.markdown(
                f"<div style='border:1px solid rgba(0,0,0,0.08);"
                f"border-radius:8px;padding:8px 10px;margin-bottom:6px;"
                f"background:#FAFAFA;'>"
                f"<b>Code {s['code']}</b> · used on "
                f"<b>{s['tag_count']}</b> tag(s)"
                f"</div>",
                unsafe_allow_html=True,
            )
