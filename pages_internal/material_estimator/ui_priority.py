"""Tab 1 — Selective Equipment Entry (priority drag).

Falls back to a numeric input grid if streamlit-sortables isn't installed.
"""
from __future__ import annotations

import streamlit as st

from . import data_layer, engine_runner

try:
    from streamlit_sortables import sort_items
    _HAS_SORTABLE = True
except ImportError:
    _HAS_SORTABLE = False


_STATE_KEY = "_sme_priority_order"


def get_current_priority(site_id: str | None) -> list[str]:
    stored = st.session_state.get(_STATE_KEY)
    if stored:
        return stored
    return engine_runner.get_default_priority(site_id)


def render(site_id: str | None, username: str | None) -> None:
    eq = data_layer.load_equipment(site_id)
    if eq.empty:
        st.info("No equipment loaded for this site yet.")
        return

    all_tags = eq["Equipment_Tag_No."].drop_duplicates().tolist()
    current = get_current_priority(site_id)
    # Repair drift — append any new tags missing from stored order
    current = [t for t in current if t in all_tags] + \
              [t for t in all_tags if t not in current]

    st.subheader("Priority order")
    st.caption(
        "Drag equipment tags top-to-bottom in build priority. The allocation "
        "engine walks the list and assigns inventory in this order."
    )

    if _HAS_SORTABLE:
        new_order = sort_items(current, direction="vertical")
        if new_order and new_order != current:
            st.session_state[_STATE_KEY] = new_order
            engine_runner._cached_allocation.clear()
            st.rerun()
    else:
        st.warning("`streamlit-sortables` not installed — using rank input fallback.")
        ranks = {}
        for i, tag in enumerate(current, start=1):
            ranks[tag] = st.number_input(
                tag, min_value=1, max_value=len(current),
                value=i, step=1, key=f"_sme_rank_{tag}",
            )
        if st.button("Apply order", type="primary"):
            ordered = sorted(ranks.items(), key=lambda kv: kv[1])
            st.session_state[_STATE_KEY] = [k for k, _ in ordered]
            engine_runner._cached_allocation.clear()
            st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        if st.button("↺ Reset to default order", use_container_width=True):
            st.session_state.pop(_STATE_KEY, None)
            engine_runner._cached_allocation.clear()
            st.rerun()
    with c2:
        st.metric("Tags in plan", len(current))
