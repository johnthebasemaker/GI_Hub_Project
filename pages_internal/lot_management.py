"""
lot_management.py — shared Lot Management UI.

Rendered by BOTH the Admin portal (cross-site, site_id=None) and the HOD portal
(site-scoped, site_id=<their site>). Drives the lot lifecycle:

  • Quarantine / Release  → mark_lot_status (open ↔ quarantine). FEFO already
    skips non-'open' lots, so a quarantined lot won't be auto-suggested.
  • Mark Expired          → mark_lot_status (→ expired).
  • Dispose               → dispose_lot: writes off the remaining qty via the
    EXISTING HOD stock-adjustment approval flow; the lot is quarantined while
    pending and flips to 'disposed' on approval (or back to 'open' if rejected).

  • Split / Merge      → split_lot / merge_lots: within-SAP, lot-to-lot
    reclassification recorded in lot_transfers (the movement ledger is NOT
    touched; v_lot_balance nets transfers in/out so Current_Stock is unchanged).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from database import (
    get_all_lots, mark_lot_status, dispose_lot, ADJUSTMENT_REASONS,
    split_lot, merge_lots,
)

# Disposal reason codes that make sense for a lot (subset of ADJUSTMENT_REASONS).
_DISPOSAL_REASONS = ["expired_disposal", "damaged", "lost", "other"]

_STATUS_BADGE = {
    "open":       ("#16A34A", "🟢 open"),
    "quarantine": ("#D97706", "🟧 quarantine"),
    "expired":    ("#DC2626", "🔴 expired"),
    "disposed":   ("#6B7280", "⚫ disposed"),
    "exhausted":  ("#6B7280", "⚪ exhausted"),
}


def render_lot_management(user: dict, site_id: str | None = None) -> None:
    """site_id=None → cross-site (admin); site_id='X' → that site only (HOD)."""
    scope = site_id or "all sites"
    st.markdown(
        f"#### 🧪 Lot Management — {scope}")
    st.caption(
        "Quarantine, expire, or dispose lots. Disposal posts a write-off for "
        "HOD approval (the lot is locked out of FEFO until approved). "
        "Remaining qty is always derived — never edited.")

    df = get_all_lots(site_id=site_id)
    if df is None or df.empty:
        st.info("No lots on record for this scope yet.")
        return

    # ── KPI strip by status ────────────────────────────────────────────────
    counts = df["Status"].value_counts().to_dict()
    kpis = st.columns(5)
    for col, key in zip(kpis, ["open", "quarantine", "expired", "disposed", "exhausted"]):
        col.metric(_STATUS_BADGE[key][1], int(counts.get(key, 0)))

    # ── Filters ────────────────────────────────────────────────────────────
    f1, f2 = st.columns([1, 2])
    with f1:
        status_sel = st.multiselect(
            "Status", options=list(_STATUS_BADGE.keys()),
            default=["open", "quarantine", "expired"],
            key=f"lotmgmt_status_{scope}")
    with f2:
        search = st.text_input(
            "🔍 Search (lot / SAP code)", key=f"lotmgmt_search_{scope}",
            placeholder="Type to filter…")

    view = df.copy()
    if status_sel:
        view = view[view["Status"].isin(status_sel)]
    if search.strip():
        s = search.strip().lower()
        view = view[
            view["Lot_Number"].astype(str).str.lower().str.contains(s)
            | view["SAP_Code"].astype(str).str.lower().str.contains(s)
        ]

    show_cols = [c for c in ["Lot_Number", "SAP_Code", "Site_ID", "Expiry_Date",
                             "Received_Qty", "Consumed_Qty", "Remaining_Qty",
                             "Status"] if c in view.columns]
    st.dataframe(view[show_cols].reset_index(drop=True),
                 use_container_width=True, hide_index=True)

    # ── Act on a single lot ────────────────────────────────────────────────
    st.markdown("##### Act on a lot")
    # Only lots that can still transition (not already disposed/exhausted).
    actionable = df[df["Status"].isin(["open", "quarantine", "expired"])].copy()
    if actionable.empty:
        st.caption("No lots are in an actionable state (open / quarantine / expired).")
        return

    def _label(r):
        return (f"{r['Lot_Number']} · {r['SAP_Code']} · {r['Site_ID']} · "
                f"rem {float(r['Remaining_Qty']):g} · {r['Status']}")

    options = {_label(r): r for _, r in actionable.iterrows()}
    pick = st.selectbox("Select a lot", options=["—"] + list(options.keys()),
                        key=f"lotmgmt_pick_{scope}")
    if pick == "—":
        return
    row = options[pick]
    lot, sap, lsite = row["Lot_Number"], row["SAP_Code"], row["Site_ID"]
    remaining = float(row["Remaining_Qty"])
    cur_status = row["Status"]

    a1, a2, a3 = st.columns(3)

    # Quarantine ↔ Release
    with a1:
        if cur_status == "quarantine":
            if st.button("🟢 Release to open", key=f"lot_rel_{scope}",
                         use_container_width=True):
                ok, msg = mark_lot_status(lot, sap, lsite, "open", user["username"])
                (st.toast(msg, icon="🟢") if ok else st.error(msg))
                if ok:
                    st.rerun()
        else:
            if st.button("🟧 Quarantine", key=f"lot_qua_{scope}",
                         use_container_width=True):
                ok, msg = mark_lot_status(lot, sap, lsite, "quarantine", user["username"])
                (st.toast(msg, icon="🟧") if ok else st.error(msg))
                if ok:
                    st.rerun()

    # Mark expired
    with a2:
        if cur_status != "expired":
            if st.button("🔴 Mark expired", key=f"lot_exp_{scope}",
                         use_container_width=True):
                ok, msg = mark_lot_status(lot, sap, lsite, "expired", user["username"])
                (st.toast(msg, icon="🔴") if ok else st.error(msg))
                if ok:
                    st.rerun()

    # Dispose (write-off via HOD approval)
    with a3:
        st.caption(f"Dispose remaining **{remaining:g}**")
    with st.expander("🗑️ Dispose this lot (needs HOD approval)", expanded=False):
        if remaining <= 0:
            st.caption("Nothing to dispose — remaining qty is 0.")
        else:
            dr = st.selectbox(
                "Reason", options=_DISPOSAL_REASONS,
                format_func=lambda k: ADJUSTMENT_REASONS.get(k, k),
                key=f"lot_disp_reason_{scope}")
            dn = st.text_input("Notes (optional)", key=f"lot_disp_notes_{scope}",
                               placeholder="e.g. drum leaked in storage")
            st.warning(
                f"This submits a write-off of **{remaining:g}** for HOD approval. "
                f"The lot is quarantined until approved.")
            if st.button("🗑️ Submit disposal for approval",
                         type="primary", key=f"lot_disp_btn_{scope}"):
                ok, msg, _adj = dispose_lot(
                    lot, sap, lsite, dr, dn, user["username"])
                (st.toast(msg, icon="🗑️") if ok else st.error(msg))
                if ok:
                    st.rerun()

    # ── Split / Merge (within-SAP reclassification) ───────────────────────
    if cur_status == "open":
        with st.expander("✂️ Split / 🔗 Merge this lot", expanded=False):
            st.caption("Reclassifies qty between lots of the SAME item & site. "
                       "Current stock is unchanged.")
            sp_col, mg_col = st.columns(2)

            # Split: peel a qty off into a new child lot.
            with sp_col:
                st.markdown("**✂️ Split**")
                sp_qty = st.number_input(
                    f"Qty to split off (< {remaining:g})",
                    min_value=0.0, max_value=float(max(remaining - 0.0001, 0.0)),
                    step=1.0, key=f"lot_split_qty_{scope}")
                if st.button("✂️ Split lot", key=f"lot_split_btn_{scope}",
                             use_container_width=True, disabled=(sp_qty <= 0)):
                    ok, msg, _new = split_lot(lot, sap, lsite, sp_qty, user["username"])
                    (st.toast(msg, icon="✂️") if ok else st.error(msg))
                    if ok:
                        st.rerun()

            # Merge: fold this lot's remaining into another open lot.
            with mg_col:
                st.markdown("**🔗 Merge into**")
                targets = df[
                    (df["SAP_Code"] == sap) & (df["Site_ID"] == lsite)
                    & (df["Status"] == "open") & (df["Lot_Number"] != lot)
                ]["Lot_Number"].tolist()
                if not targets:
                    st.caption("No other open lot of this item to merge into.")
                else:
                    into = st.selectbox("Target lot", options=targets,
                                        key=f"lot_merge_into_{scope}")
                    st.caption(f"Moves all **{remaining:g}** from {lot} → {into}; "
                               f"{lot} becomes exhausted.")
                    if st.button("🔗 Merge", key=f"lot_merge_btn_{scope}",
                                 use_container_width=True):
                        ok, msg = merge_lots(lot, into, sap, lsite, user["username"])
                        (st.toast(msg, icon="🔗") if ok else st.error(msg))
                        if ok:
                            st.rerun()
