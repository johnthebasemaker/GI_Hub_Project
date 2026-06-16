"""
pages_internal/warehouse_portal.py
==================================
Warehouse Portal — Phase C of the procurement chain.

Sits between Logistics (which routes POs to a warehouse) and the Site SK
(which finally confirms physical receipt). The warehouse never sees
prices — every PO drill-down on this page goes through helpers that
force-blank Unit_Price + Total_Price.

Tabs:
  1. 🔔 Incoming Assignments — POs Logistics has routed to my warehouse
  2. 📦 Receive Goods        — mark PO items as physically received
  3. 📝 Prepare DN           — build a site-bound delivery note (RL/BL safe)
  4. ✈️ Outbound DNs         — track the DN state machine
  5. ↩️ Returns from Site    — accept site-flagged returns
  6. 📂 History              — closed assignments + delivered DNs
"""

from __future__ import annotations

import html
import json
import datetime

import pandas as pd
import streamlit as st

from config import BRAND_GOLD, TEXT_PRIMARY, TEXT_MUTED, classify_rl_bl_family
from database import (
    get_connection,
    list_warehouses,
    list_assignments_for_warehouse, get_assignment_detail,
    acknowledge_assignment, record_warehouse_receipt,
    create_delivery_note, submit_dn_for_logistics,
    list_dns, get_dn_detail,
    request_reschedule,
    record_internal_return,
    raise_vendor_return,
    get_sites,
)
from ui_components import (
    render_brand_header,
    render_aggrid,
    render_empty_state,
)


# Mirror Logistics palette so the two portals visually rhyme.
_C = {
    "surf":   "#162038",
    "surf2":  "#1E3050",
    "border": "#2A4060",
    "gold":   "#D4AF37",
    "goldLt": "#F0D060",
    "text":   "#F0F4F8",
    "muted":  "#7A8FA0",
    "ok":     "#22C55E",
    "low":    "#F59E0B",
    "crit":   "#EF4444",
    "sky":    "#0EA5E9",
    "emerald":"#10B981",
}


def _esc(v) -> str:
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return html.escape(s) if s else "—"


def _kv_row(label: str, value) -> str:
    return (
        f'<div style="display:flex;justify-content:space-between;'
        f'padding:4px 0;border-bottom:1px dashed {_C["border"]}40;">'
        f'<span style="color:{_C["muted"]};font-size:12px;">{html.escape(label)}</span>'
        f'<span style="color:{_C["text"]};font-size:12.5px;font-weight:600;">'
        f'{_esc(value)}</span>'
        f'</div>'
    )


def _section_card(title: str, body_html: str) -> str:
    return (
        f'<div style="background:{_C["surf"]};border:1px solid {_C["border"]};'
        f'border-radius:12px;padding:14px 18px;margin-bottom:14px;">'
        f'<div style="color:{_C["muted"]};font-size:11px;letter-spacing:0.1em;'
        f'text-transform:uppercase;margin-bottom:8px;">{html.escape(title)}</div>'
        f'{body_html}</div>'
    )


def _hero(label: str, value: str, sub: str = "", accent: str = None) -> str:
    accent = accent or _C["gold"]
    return (
        f'<div style="flex:1;min-width:120px;background:{_C["surf2"]};'
        f'border:1px solid {_C["border"]};border-radius:10px;padding:12px 14px;">'
        f'<div style="color:{_C["muted"]};font-size:10px;letter-spacing:0.1em;'
        f'text-transform:uppercase;">{html.escape(label)}</div>'
        f'<div style="color:{accent};font-size:22px;font-weight:800;margin-top:4px;">'
        f'{html.escape(value)}</div>'
        f'<div style="color:{_C["muted"]};font-size:11px;margin-top:2px;">'
        f'{html.escape(sub)}</div></div>'
    )


def _hero_strip(cards: list[str]) -> None:
    st.markdown(
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">'
        + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


def _safe_date(v):
    if not v:
        return datetime.date.today()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return datetime.date.today()


def _resolve_warehouse(user: dict) -> str | None:
    """Determine which warehouse this user is bound to. warehouse_user
    inherits the Warehouse_ID from their user profile. Admin is allowed to
    pick any active warehouse for shadow access."""
    role = user.get("role")
    if role == "warehouse_user":
        wh = user.get("warehouse_id") or _user_warehouse_from_db(user["username"])
        return wh
    if role == "admin":
        wh_df = list_warehouses()
        if wh_df.empty:
            return None
        # Cached pick across the session
        key = "_wh_admin_shadow_wh"
        pick = st.sidebar.selectbox(
            "🏭 Shadow warehouse",
            wh_df["Warehouse_ID"].tolist(),
            key=key,
            help="Admin: pick which warehouse to view as.",
        )
        return pick
    return None


def _user_warehouse_from_db(username: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT Warehouse_ID FROM users WHERE username = ?",
            (username,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ===========================================================================
# TAB 1 — Incoming Assignments
# ===========================================================================
def _tab_incoming(user: dict, wh_id: str) -> None:
    st.markdown("### 🔔 Incoming Assignments")
    st.caption("POs Logistics has routed to this warehouse. Acknowledge to confirm receipt.")

    df = list_assignments_for_warehouse(wh_id)
    if df.empty:
        render_empty_state(
            icon="📭", title="No assignments yet",
            hint="Logistics assigns POs from Logistics Portal → 🏭 Assign to Warehouse.",
        )
        return

    pending = (df["status"] == "assigned").sum()
    ack = (df["status"] == "acknowledged").sum()
    rec = (df["status"].isin(["partial", "received"])).sum()
    _hero_strip([
        _hero("AWAITING ACK", str(int(pending)), "to acknowledge", _C["low"]),
        _hero("ACKED", str(int(ack)), "waiting on goods", _C["sky"]),
        _hero("IN/RECEIVED", str(int(rec)), "items arrived", _C["emerald"]),
    ])

    render_aggrid(
        df.rename(columns={
            "assignment_id": "Assign #",
            "PO_Number": "PO No.",
            "PR_Number": "PR No.",
            "Vendor_Name": "Vendor",
            "Site_ID": "Dest Site",
            "Expected_Delivery": "Expected",
            "assigned_at": "Assigned",
            "acknowledged_at": "Acked",
            "status": "Status",
        }),
        height=300, key="_wh_assignments_grid",
    )

    st.divider()
    open_pendings = df[df["status"] == "assigned"]
    if open_pendings.empty:
        st.caption("Nothing to acknowledge.")
        return
    aid = st.selectbox(
        "Acknowledge assignment",
        open_pendings["assignment_id"].tolist(),
        format_func=lambda i: (
            f"#{int(i)} · PO {open_pendings.set_index('assignment_id').loc[i, 'PO_Number']} "
            f"· {open_pendings.set_index('assignment_id').loc[i, 'Vendor_Name'] or '—'}"
        ),
        key="_wh_ack_pick",
    )
    if st.button("✅ Acknowledge", type="primary", key="_wh_ack_btn"):
        ok, msg = acknowledge_assignment(int(aid), user["username"])
        (st.success if ok else st.error)(msg)
        if ok:
            st.rerun()


# ===========================================================================
# TAB 2 — Receive Goods (against an assignment)
# ===========================================================================
def _tab_receive(user: dict, wh_id: str) -> None:
    st.markdown("### 📦 Receive Goods")
    st.caption(
        "Record qty actually received against an acknowledged assignment. "
        "Over-delivery is blocked at the data layer."
    )

    open_df = list_assignments_for_warehouse(
        wh_id, status_filter=["acknowledged", "partial"],
    )
    if open_df.empty:
        render_empty_state(
            icon="📭", title="No assignments ready to receive",
            hint="Acknowledge the assignment first in the previous tab.",
        )
        return

    aid = st.selectbox(
        "Assignment",
        open_df["assignment_id"].tolist(),
        format_func=lambda i: (
            f"#{int(i)} · PO {open_df.set_index('assignment_id').loc[i, 'PO_Number']}"
        ),
        key="_wh_rec_aid",
    )
    detail = get_assignment_detail(int(aid))
    items = detail["items"]
    h = detail["po_header"]

    body = (
        _kv_row("PO Number", h.get("PO_Number"))
        + _kv_row("Vendor", f"{h.get('Vendor_Code') or '—'} · {h.get('Vendor_Name') or '—'}")
        + _kv_row("Inco / Payment",
                  f"{h.get('Inco_Terms') or '—'} / {h.get('Payment_Terms') or '—'}")
        + _kv_row("Expected", detail["assignment"].get("Expected_Delivery"))
    )
    st.markdown(_section_card("PO snapshot (no prices)", body),
                unsafe_allow_html=True)

    if items.empty:
        st.info("Assignment has no items.")
        return

    # The grid the warehouse fills in with received qty
    items_disp = items.copy()
    if "Open_Qty" not in items_disp.columns:
        items_disp["Open_Qty"] = (
            items_disp["Qty"].fillna(0)
            - items_disp["Delivered_Qty"].fillna(0)
            + items_disp["Returned_Qty"].fillna(0)
        )
    items_disp["Receive Now"] = 0.0
    cols_show = [c for c in [
        "id", "Material_Code", "Description", "UOM", "rl_bl_family",
        "Qty", "Delivered_Qty", "Open_Qty", "Receive Now",
    ] if c in items_disp.columns]
    edited = st.data_editor(
        items_disp[cols_show],
        use_container_width=True, height=320,
        disabled=[c for c in cols_show if c != "Receive Now"],
        key="_wh_rec_editor",
    )
    if st.button("📥 Record receipt", type="primary", key="_wh_rec_btn"):
        received_map = {
            int(r["id"]): float(r["Receive Now"] or 0)
            for _, r in edited.iterrows()
            if float(r.get("Receive Now") or 0) > 0
        }
        if not received_map:
            st.error("Type a quantity in 'Receive Now' for at least one row.")
        else:
            ok, msg = record_warehouse_receipt(
                int(aid), received_map, user["username"],
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.toast("📥 Goods recorded", icon="📦")
                st.rerun()


# ===========================================================================
# TAB 3 — Prepare Delivery Note
# ===========================================================================
def _tab_prepare_dn(user: dict, wh_id: str) -> None:
    st.markdown("### 📝 Prepare Delivery Note")
    st.caption(
        "Pick the PO + destination site, then the lines to ship. **RL and "
        "BL families must travel on separate DNs** — the system rejects "
        "mixed-family DNs by design."
    )

    # Source assignments (received or partial)
    src = list_assignments_for_warehouse(
        wh_id, status_filter=["acknowledged", "partial", "received"],
    )
    if src.empty:
        render_empty_state(
            icon="📦", title="No POs at this warehouse yet",
            hint="Receive goods against an acknowledged assignment first.",
        )
        return

    po_pick = st.selectbox(
        "PO Number",
        src["PO_Number"].unique().tolist(),
        key="_wh_dn_po",
    )
    sites = sorted([s for s in (get_sites() or []) if s])
    site_pick = st.selectbox(
        "Destination Site",
        sites if sites else ["HQ"],
        index=0,
        key="_wh_dn_site",
    )

    # Pull the assignment's PO items via the shielded helper. hide_prices is
    # implicit because get_assignment_detail forces it.
    aid = int(src[src["PO_Number"] == po_pick]["assignment_id"].iloc[0])
    detail = get_assignment_detail(aid)
    items = detail["items"]
    if items.empty:
        st.info("No items on this PO/assignment.")
        return

    items_disp = items.copy()
    items_disp["Ship Qty"]   = 0.0
    items_disp["Lot_Number"] = ""
    items_disp["Expiry_Date"] = ""
    items_disp["Remarks"]    = ""
    cols_show = [c for c in [
        "id", "Material_Code", "Description", "UOM", "rl_bl_family",
        "Qty", "Delivered_Qty", "Returned_Qty",
        "Ship Qty", "Lot_Number", "Expiry_Date", "Remarks",
    ] if c in items_disp.columns]
    edited = st.data_editor(
        items_disp[cols_show],
        use_container_width=True, height=320,
        disabled=[c for c in cols_show if c not in
                  ("Ship Qty", "Lot_Number", "Expiry_Date", "Remarks")],
        key="_wh_dn_editor",
    )

    st.markdown("#### DN header")
    h1, h2, h3 = st.columns(3)
    with h1:
        dn_date = st.date_input("DN Date",
                                  value=datetime.date.today(),
                                  key="_wh_dn_date")
        vehicle = st.text_input("Vehicle No.", key="_wh_dn_vehicle")
    with h2:
        driver  = st.text_input("Driver Name", key="_wh_dn_driver")
        phone   = st.text_input("Driver Phone", key="_wh_dn_phone")
    with h3:
        prepared = st.text_input("Prepared By",
                                   value=user["username"],
                                   key="_wh_dn_prep")
        remarks = st.text_input("Remarks", key="_wh_dn_remarks")

    if st.button("📝 Save DN draft", type="primary", key="_wh_dn_save"):
        line_items = []
        for _, r in edited.iterrows():
            try:
                qty = float(r.get("Ship Qty") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            line_items.append({
                "po_item_id": int(r["id"]),
                "Qty":        qty,
                "Lot_Number": r.get("Lot_Number") or None,
                "Expiry_Date": r.get("Expiry_Date") or None,
                "Remarks":    r.get("Remarks") or None,
            })
        if not line_items:
            st.error("Enter a Ship Qty > 0 for at least one row.")
        else:
            ok, msg, dn_no = create_delivery_note(
                po_number=po_pick, warehouse_id=wh_id,
                site_id=site_pick, line_items=line_items,
                header={
                    "DN_Date": dn_date.isoformat() if dn_date else None,
                    "Vehicle_No": vehicle, "Driver_Name": driver,
                    "Driver_Phone": phone, "Prepared_By": prepared,
                    "Remarks": remarks,
                },
                created_by=user["username"],
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.toast(f"📝 DN {dn_no}", icon="📝")
                st.session_state["_wh_last_dn_drafted"] = dn_no
                st.rerun()


# ===========================================================================
# TAB 4 — Outbound DNs (state machine viewer + submit-to-logistics action)
# ===========================================================================
def _tab_outbound_dns(user: dict, wh_id: str) -> None:
    st.markdown("### ✈️ Outbound Delivery Notes")
    st.caption(
        "Every DN this warehouse has prepared. Drafts can be submitted to "
        "Logistics for delivery-date approval; HOD approves the content; "
        "SK finally confirms physical receipt."
    )

    status_filter = st.multiselect(
        "Status filter",
        ["draft", "pending_logistics", "logistics_approved",
         "pending_hod", "hod_approved", "pending_sk",
         "received", "rejected", "cancelled"],
        default=["draft", "pending_logistics", "pending_hod", "pending_sk"],
        key="_wh_dn_status_filter",
    )
    df = list_dns(warehouse_id=wh_id, status_filter=status_filter or None)
    if df.empty:
        render_empty_state(icon="🗒️", title="No DNs match the filter")
        return

    render_aggrid(df, height=320, key="_wh_dn_grid")

    st.divider()
    drafts = df[df["status"] == "draft"]
    if not drafts.empty:
        st.markdown("**Submit a draft to Logistics**")
        pick = st.selectbox(
            "Draft DN",
            drafts["DN_Number"].tolist(),
            key="_wh_dn_submit_pick",
        )
        if st.button("📨 Submit to Logistics", type="primary",
                     key="_wh_dn_submit_btn"):
            ok, msg = submit_dn_for_logistics(pick, user["username"])
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

    st.divider()
    st.markdown("**Inspect a DN**")
    dn_pick = st.selectbox(
        "DN", df["DN_Number"].tolist(), key="_wh_dn_inspect"
    )
    if dn_pick:
        detail = get_dn_detail(dn_pick)
        h = detail["header"]
        body = (
            _kv_row("PO Number", h.get("PO_Number"))
            + _kv_row("Site", h.get("Site_ID"))
            + _kv_row("Status", h.get("status"))
            + _kv_row("RL/BL family", h.get("rl_bl_family") or "—")
            + _kv_row("Vehicle / Driver",
                      f"{h.get('Vehicle_No') or '—'} · {h.get('Driver_Name') or '—'}")
            + _kv_row("Logistics", f"{h.get('logistics_decided_by') or '—'} "
                      f"({h.get('logistics_decision') or '—'})")
            + _kv_row("HOD", h.get("hod_decided_by"))
            + _kv_row("SK", h.get("sk_received_by"))
        )
        st.markdown(_section_card(f"DN {dn_pick}", body),
                    unsafe_allow_html=True)
        if not detail["items"].empty:
            render_aggrid(detail["items"], height=260, key="_wh_dn_items_grid")

        # Reschedule request shortcut for the Warehouse role
        with st.expander("🔁 Request reschedule for this DN", expanded=False):
            current = h.get("DN_Date") or "—"
            new_d = st.date_input(
                "Requested new delivery date",
                value=_safe_date(h.get("DN_Date")),
                key=f"_wh_dn_resch_date_{dn_pick}",
            )
            reason = st.text_area(
                "Reason", key=f"_wh_dn_resch_reason_{dn_pick}",
                max_chars=400, height=70,
            )
            if st.button(
                "Submit reschedule request",
                key=f"_wh_dn_resch_btn_{dn_pick}",
            ):
                if not reason.strip():
                    st.error("Reason is required.")
                else:
                    ok, msg = request_reschedule(
                        po_number=h.get("PO_Number"),
                        dn_number=dn_pick,
                        current_date=current,
                        requested_date=new_d.isoformat(),
                        reason=reason,
                        requested_by_role="warehouse_user",
                        requested_by=user["username"],
                    )
                    (st.success if ok else st.error)(msg)


# ===========================================================================
# TAB 5 — Returns from Site
# ===========================================================================
def _tab_returns_from_site(user: dict, wh_id: str) -> None:
    st.markdown("### ↩️ Returns from Site")
    st.caption(
        "If a site flags defective material from a DN this warehouse "
        "delivered, raise the return → it lands in Logistics' Vendor "
        "Returns queue and reopens the originating PO line."
    )

    df = list_dns(warehouse_id=wh_id,
                  status_filter=["received", "pending_sk", "hod_approved"])
    if df.empty:
        render_empty_state(icon="📦", title="No delivered DNs eligible for return")
        return

    dn_pick = st.selectbox(
        "DN", df["DN_Number"].tolist(), key="_wh_ret_dn_pick",
    )
    detail = get_dn_detail(dn_pick)
    items = detail["items"]
    if items.empty:
        st.info("DN has no items.")
        return

    items_disp = items.copy()
    items_disp["Return Qty"] = 0.0
    cols_show = [c for c in [
        "id", "Material_Code", "Description", "UOM", "rl_bl_family",
        "Qty", "sk_received_qty", "status", "Return Qty",
    ] if c in items_disp.columns]
    edited = st.data_editor(
        items_disp[cols_show],
        use_container_width=True, height=300,
        disabled=[c for c in cols_show if c != "Return Qty"],
        key="_wh_ret_editor",
    )
    reason = st.text_area("Reason *", key="_wh_ret_reason",
                            max_chars=400, height=80)

    if st.button("↩️ Raise return to vendor", type="primary",
                 key="_wh_ret_btn"):
        items_payload = [
            {"dn_item_id": int(r["id"]), "qty": float(r.get("Return Qty") or 0)}
            for _, r in edited.iterrows()
            if float(r.get("Return Qty") or 0) > 0
        ]
        if not items_payload:
            st.error("Enter a Return Qty > 0 on at least one row.")
        elif not reason.strip():
            st.error("Reason is required.")
        else:
            ok, msg = record_internal_return(
                dn_number=dn_pick, items=items_payload,
                reason=reason, raised_by_role="warehouse_user",
                raised_by=user["username"],
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()


# ===========================================================================
# TAB 6 — History
# ===========================================================================
def _tab_history(user: dict, wh_id: str) -> None:
    st.markdown("### 📂 History")
    st.caption("Closed assignments + completed DNs.")

    sub1, sub2 = st.tabs(["Completed DNs", "Closed assignments"])
    with sub1:
        df = list_dns(
            warehouse_id=wh_id,
            status_filter=["received", "rejected", "cancelled"],
        )
        if df.empty:
            st.info("No completed DNs yet.")
        else:
            render_aggrid(df, height=380, key="_wh_hist_dns")
    with sub2:
        df = list_assignments_for_warehouse(
            wh_id, status_filter=["received", "closed", "cancelled"],
        )
        if df.empty:
            st.info("No closed assignments yet.")
        else:
            render_aggrid(df, height=380, key="_wh_hist_asg")


# ===========================================================================
# PAGE ENTRY POINT
# ===========================================================================
def page_warehouse_portal(user: dict) -> None:
    render_brand_header("Warehouse — Receive · Prepare DN · Ship to Site")

    wh_id = _resolve_warehouse(user)
    if not wh_id:
        st.error(
            "🛑 Your account is not bound to a warehouse. Ask Admin to set "
            "your Warehouse_ID in Admin Portal → Users."
        )
        return

    # Title strip
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"margin-bottom:6px;'>"
        f"<span style='font-size:22px;'>🏭</span>"
        f"<span style='color:{BRAND_GOLD};font-weight:800;font-size:20px;'>"
        f"Warehouse {html.escape(wh_id)}</span>"
        f"<span style='color:{TEXT_MUTED};font-size:12px;margin-left:8px;'>"
        f"(prices hidden — Logistics-only)</span></div>",
        unsafe_allow_html=True,
    )

    t1, t2, t3, t4, t5, t6 = st.tabs([
        "🔔 Incoming Assignments",
        "📦 Receive Goods",
        "📝 Prepare DN",
        "✈️ Outbound DNs",
        "↩️ Returns from Site",
        "📂 History",
    ])
    with t1: _tab_incoming(user, wh_id)
    with t2: _tab_receive(user, wh_id)
    with t3: _tab_prepare_dn(user, wh_id)
    with t4: _tab_outbound_dns(user, wh_id)
    with t5: _tab_returns_from_site(user, wh_id)
    with t6: _tab_history(user, wh_id)
