"""
pages_internal/supervisor_portal.py — Supervisor Portal (Phase 7B)
==================================================================
Single top-level page exact-locked to {supervisor, admin}.

ONE tab in 7B: 📦 Request Material, with 3 sub-tabs:
  🆕 New Request    — supervisor builds + submits a request
  📋 My Requests    — own history, status filter, cancel-while-pending
  📊 Intent vs Actual — last-N-days approved requests with variance

The portal never writes to consumption / pending_issues directly. On SK
approval the helper layer mirrors lines into pending_issues — see
database.approve_supervisor_request().
"""
from __future__ import annotations

import datetime
import html

import pandas as pd
import streamlit as st

from config import (
    BRAND_GOLD, TEXT_PRIMARY, TEXT_MUTED, TEXT_SECONDARY,
    COLOR_OK, COLOR_LOW, COLOR_CRITICAL,
)
from database import (
    get_connection,
    list_employees_for_site,
    create_supervisor_request,
    list_supervisor_requests,
    get_supervisor_request,
    cancel_supervisor_request,
    report_supervisor_intent_vs_actual,
)
from cache_layer import cached_live_inventory
from ui_components import (
    render_brand_header, render_empty_state,
    # Phase 7E — form draft recovery
    render_form_recovery_banner, auto_save_form_draft,
    render_manual_save_draft_button, clear_form_draft,
)

# Phase 7E — state keys that participate in draft recovery for this form.
_SMR_STATE_KEYS = [
    "smr_worker_pick", "smr_job_tank", "smr_ppe_radio",
    "smr_ppe_reason", "_smr_cart",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _worker_label(row: pd.Series) -> str:
    """Name-first format per user spec: 'Ahmed Ali (EMP-1042)'."""
    name = (row.get("Name") or "").strip()
    eid  = (row.get("ID_Number") or "").strip()
    return f"{name} ({eid})" if name else eid


def _status_pill(status: str) -> str:
    palette = {
        "pending_sk": (COLOR_LOW,      "Pending SK"),
        "approved":   (COLOR_OK,       "Approved"),
        "rejected":   (COLOR_CRITICAL, "Rejected"),
        "cancelled":  (TEXT_MUTED,     "Cancelled"),
    }
    color, label = palette.get(status, (TEXT_MUTED, status or "—"))
    return (
        f'<span style="background:{color}22;color:{color};font-size:11.5px;'
        f'padding:2px 8px;border-radius:10px;font-weight:600;'
        f'white-space:nowrap;">{html.escape(label)}</span>'
    )


# ---------------------------------------------------------------------------
# Sub-tab 1: 🆕 New Request
# ---------------------------------------------------------------------------
def _render_new_request_subtab(user: dict, site_id: str) -> None:
    actor = user.get("username", "supervisor")

    # Phase 7E — mount-time recovery banner. Renders only if a draft exists
    # in either localStorage or the server-side form_drafts table.
    render_form_recovery_banner(
        "supervisor_request", actor, site_id, _SMR_STATE_KEYS,
    )

    # ── Active workers bound to this site ────────────────────────────────
    workers = list_employees_for_site(site_id, status_filter="active")
    if workers.empty:
        st.markdown(
            f'<div style="background:{COLOR_CRITICAL}1A;'
            f'border:1px solid {COLOR_CRITICAL};border-radius:8px;'
            f'padding:14px 18px;margin-bottom:14px;">'
            f'<span style="color:{COLOR_CRITICAL};font-weight:700;">'
            f'🚫 No active workers at <code>{html.escape(site_id)}</code>.</span><br>'
            f'<span style="color:{TEXT_SECONDARY};font-size:12.5px;">'
            f'Ask the HOD to add workers via 📋 HOD Portal → 👷 Employees, or '
            f'contact Admin to assign existing employees to this site.</span></div>',
            unsafe_allow_html=True,
        )
        return

    # ── Header form ──────────────────────────────────────────────────────
    st.markdown(
        f'<h4 style="color:{TEXT_PRIMARY};font-weight:600;margin:0 0 10px 0;">'
        f'1) Worker &amp; Job</h4>', unsafe_allow_html=True,
    )

    col_w, col_j = st.columns([2, 2])
    with col_w:
        worker_labels = [_worker_label(r) for _, r in workers.iterrows()]
        worker_id_map = dict(zip(worker_labels, workers["ID_Number"].tolist()))
        picked = st.selectbox(
            "Worker",
            worker_labels,
            key="smr_worker_pick",
            help=f"Active employees bound to {site_id}.",
        )
        worker_id = worker_id_map.get(picked, "")
    with col_j:
        job_tank = st.text_input(
            "Job / Tank / Place number",
            key="smr_job_tank",
            placeholder="e.g. Tank-42 · Bay-A · WBS-200/300/12",
        ).strip()

    col_ppe, col_reason = st.columns([1, 2])
    with col_ppe:
        ppe_choice = st.radio(
            "Old PPE returned?",
            ["Yes", "No"],
            horizontal=True,
            key="smr_ppe_radio",
        )
    with col_reason:
        if ppe_choice == "No":
            no_reason = st.text_area(
                "Reason — old PPE NOT returned",
                key="smr_ppe_reason",
                height=68,
                placeholder="Required when 'No'.",
            ).strip()
        else:
            no_reason = ""
            st.caption("✓ Old PPE returned — no reason needed.")

    # ── Item cart (search + add pattern, matches HOD Cross-Site) ─────────
    st.markdown(
        f'<h4 style="color:{TEXT_PRIMARY};font-weight:600;margin:18px 0 10px 0;">'
        f'2) Items</h4>', unsafe_allow_html=True,
    )
    inv = cached_live_inventory(site_id=site_id)
    if inv is None or inv.empty:
        st.warning("Inventory empty for this site.")
        return

    if "_smr_cart" not in st.session_state:
        st.session_state["_smr_cart"] = []

    inv_disp = inv.copy()
    inv_disp["__search__"] = (
        "[" + inv_disp["SAP_Code"].astype(str).str.strip() + "] "
        + inv_disp["Equipment_Description"].astype(str)
    )

    col_pick, col_qty, col_add = st.columns([4, 1, 1])
    with col_pick:
        pick = st.selectbox(
            "Material",
            inv_disp["__search__"].tolist(),
            key="smr_item_pick",
        )
    with col_qty:
        qty = st.number_input("Qty", min_value=0.0, step=1.0, key="smr_item_qty")
    with col_add:
        st.write("")
        add_clicked = st.button("➕ Add", use_container_width=True, key="smr_add_btn")

    if pick:
        sap = pick.split("]")[0].replace("[", "").strip()
        match = inv_disp[inv_disp["SAP_Code"].astype(str).str.strip() == sap]
        cur_stock = float(match.iloc[0].get("Current_Stock", 0) or 0) if not match.empty else 0.0
        stock_color = COLOR_OK if cur_stock >= qty and qty > 0 else (COLOR_LOW if cur_stock > 0 else COLOR_CRITICAL)
        st.markdown(
            f'<div style="font-size:12.5px;color:{TEXT_MUTED};margin:-6px 0 8px 0;">'
            f'Live stock at <b>{html.escape(site_id)}</b>: '
            f'<b style="color:{stock_color};">{cur_stock:g}</b>'
            f'</div>', unsafe_allow_html=True,
        )

    if add_clicked:
        if not pick or qty <= 0:
            st.warning("Pick a material and enter a positive Qty.")
        else:
            sap = pick.split("]")[0].replace("[", "").strip()
            mr = inv_disp[inv_disp["SAP_Code"].astype(str).str.strip() == sap].iloc[0]
            cur_stock = float(mr.get("Current_Stock", 0) or 0)
            st.session_state["_smr_cart"].append({
                "SAP_Code": sap,
                "Description": mr.get("Equipment_Description", ""),
                "UOM": mr.get("UOM", ""),
                "Requested_Qty": float(qty),
                "Stock_At_Request": cur_stock,
                "Notes": "",
            })
            st.rerun()

    cart = st.session_state["_smr_cart"]
    if cart:
        df = pd.DataFrame(cart)
        # Render with available-flag colour cues.
        def _row_html(i, row):
            avail = row["Stock_At_Request"] >= row["Requested_Qty"]
            warn = "" if avail else (
                f'<span style="color:{COLOR_CRITICAL};font-weight:700;"> ⚠️ short</span>'
            )
            return (
                f'<tr><td style="padding:6px 10px;">{i+1}</td>'
                f'<td style="padding:6px 10px;font-family:monospace;">{html.escape(str(row["SAP_Code"]))}</td>'
                f'<td style="padding:6px 10px;">{html.escape(str(row["Description"]))}</td>'
                f'<td style="padding:6px 10px;">{html.escape(str(row.get("UOM") or ""))}</td>'
                f'<td style="padding:6px 10px;text-align:right;font-weight:700;">{row["Requested_Qty"]:g}</td>'
                f'<td style="padding:6px 10px;text-align:right;">{row["Stock_At_Request"]:g}{warn}</td>'
                f'</tr>'
            )
        rows_html = "".join(_row_html(i, r) for i, r in df.iterrows())
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;">'
            f'<thead><tr style="background:rgba(212,175,55,0.10);color:{BRAND_GOLD};">'
            f'<th style="padding:8px 10px;text-align:left;">#</th>'
            f'<th style="padding:8px 10px;text-align:left;">SAP</th>'
            f'<th style="padding:8px 10px;text-align:left;">Description</th>'
            f'<th style="padding:8px 10px;text-align:left;">UOM</th>'
            f'<th style="padding:8px 10px;text-align:right;">Req Qty</th>'
            f'<th style="padding:8px 10px;text-align:right;">Stock Now</th>'
            f'</tr></thead><tbody>{rows_html}</tbody></table>',
            unsafe_allow_html=True,
        )
        col_clr, col_save, col_sub = st.columns([1, 1, 2])
        with col_clr:
            if st.button("🗑️ Clear cart", use_container_width=True, key="smr_clr"):
                st.session_state["_smr_cart"] = []
                st.rerun()
        with col_save:
            # Phase 7E — manual Save Draft (bypasses 1/min server throttle).
            render_manual_save_draft_button(
                "supervisor_request", actor, site_id, _SMR_STATE_KEYS,
                key_suffix="smr_main",
            )
        with col_sub:
            if st.button("📨 Submit Request to Store Keeper",
                         type="primary",
                         use_container_width=True,
                         key="smr_submit"):
                ok, msg = create_supervisor_request(
                    site_id=site_id,
                    worker_id=worker_id,
                    job_tank_place=job_tank,
                    old_ppe_returned=(1 if ppe_choice == "Yes" else 0),
                    no_return_reason=no_reason,
                    items=cart,
                    supervisor_username=actor,
                )
                if ok:
                    st.success(f"✅ Submitted as {msg}. Store Keeper has been notified.")
                    st.session_state["_smr_cart"] = []
                    # Phase 7E — wipe the draft once the row is safely persisted.
                    clear_form_draft("supervisor_request", actor)
                    st.rerun()
                else:
                    st.error(f"🚫 {msg}")
    else:
        st.caption("Cart is empty — add at least one item above.")

    # Phase 7E — per-rerun auto-save (localStorage every run + server 1/min).
    auto_save_form_draft(
        "supervisor_request", actor, site_id, _SMR_STATE_KEYS,
    )


# ---------------------------------------------------------------------------
# Sub-tab 2: 📋 My Requests
# ---------------------------------------------------------------------------
def _render_my_requests_subtab(user: dict, site_id: str) -> None:
    actor = user.get("username", "supervisor")
    col_st, col_days = st.columns(2)
    with col_st:
        status_pick = st.selectbox(
            "Status",
            ["All", "pending_sk", "approved", "rejected", "cancelled"],
            key="smr_my_status",
        )
    with col_days:
        days_pick = st.selectbox(
            "Window",
            ["Last 30 days", "Last 60 days", "Last 90 days", "All time"],
            key="smr_my_days",
        )
    days_map = {"Last 30 days": 30, "Last 60 days": 60,
                "Last 90 days": 90, "All time": None}
    df = list_supervisor_requests(
        site_id=site_id,
        status=None if status_pick == "All" else status_pick,
        requested_by=actor,
        days=days_map[days_pick],
    )
    if df.empty:
        render_empty_state(
            icon="📭", title="No requests in this window",
            hint="Use 🆕 New Request to create one.",
        )
        return

    for _, hdr in df.iterrows():
        with st.container(border=True):
            top = st.columns([3, 2, 2, 2])
            top[0].markdown(
                f'<b style="color:{BRAND_GOLD};font-family:monospace;">{hdr["request_no"]}</b><br>'
                f'<span style="color:{TEXT_MUTED};font-size:12px;">{hdr["requested_at"]}</span>',
                unsafe_allow_html=True,
            )
            top[1].markdown(
                f'<b>{html.escape(hdr["Worker_Name"])}</b><br>'
                f'<code style="font-size:11.5px;">{html.escape(hdr["Worker_ID"])}</code>',
                unsafe_allow_html=True,
            )
            top[2].markdown(
                f'<span style="color:{TEXT_MUTED};font-size:12px;">Job/Tank</span><br>'
                f'<b>{html.escape(hdr["Job_Tank_Place"])}</b>',
                unsafe_allow_html=True,
            )
            top[3].markdown(_status_pill(hdr["status"]), unsafe_allow_html=True)

            with st.expander("View items", expanded=False):
                _h, items = get_supervisor_request(int(hdr["id"]))
                if items.empty:
                    st.caption("No line items recorded.")
                else:
                    show = items[["SAP_Code", "Material_Code", "Equipment_Description",
                                  "UOM", "Requested_Qty", "Stock_At_Request",
                                  "SK_Adjusted_Qty", "Notes"]].copy()
                    show = show.rename(columns={
                        "Equipment_Description": "Description",
                        "Stock_At_Request": "Stock@Req",
                        "SK_Adjusted_Qty": "SK Adj Qty",
                    })
                    st.dataframe(show, use_container_width=True, hide_index=True)
                if hdr["status"] == "rejected" and hdr.get("sk_reject_reason"):
                    st.warning(f"SK rejection reason: {hdr['sk_reject_reason']}")

            if hdr["status"] == "pending_sk":
                if st.button("🗑️ Cancel this request",
                             key=f"smr_cancel_{hdr['id']}",
                             use_container_width=False):
                    ok = cancel_supervisor_request(int(hdr["id"]), actor)
                    if ok:
                        st.success("Request cancelled.")
                        st.rerun()
                    else:
                        st.warning("Could not cancel — SK may have already acted.")


# ---------------------------------------------------------------------------
# Sub-tab 3: 📊 Intent vs Actual
# ---------------------------------------------------------------------------
def _render_intent_vs_actual_subtab(user: dict, site_id: str) -> None:
    st.caption(
        "Each row = one approved SMR line. **Actual** comes from the consumption "
        "ledger via Source_Ref. Blank Actual = approved but not yet committed by HOD."
    )
    days = st.selectbox(
        "Window",
        [30, 60, 90, 180, 365],
        index=0,
        format_func=lambda d: f"Last {d} days",
        key="smr_iva_days",
    )
    df = report_supervisor_intent_vs_actual(site_id=site_id, days=days)
    if df.empty:
        render_empty_state(
            icon="📊", title="No approved requests in this window",
            hint="Submit a request, get SK approval, then HOD EOD commit will populate this view.",
        )
        return

    # Drop monster Source_Ref for readability (it's an internal tracer).
    show = df.drop(columns=["Source_Ref"], errors="ignore").copy()
    # Render variance with colour highlighting via st.dataframe column_config.
    st.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Variance_Pct": st.column_config.NumberColumn(
                "Variance %",
                help="(Actual − Requested) / Requested. Blank = not committed yet.",
                format="%.1f%%",
            ),
        },
    )
    st.caption(f"{len(df)} approved line(s) in the last {days} days.")


# ---------------------------------------------------------------------------
# Page entrypoint
# ---------------------------------------------------------------------------
def page_supervisor_portal(user: dict) -> None:
    render_brand_header("Supervisor Portal")
    site_id = user.get("site_id") or "HQ"

    st.markdown(
        f'<h1 style="color:{TEXT_PRIMARY};font-size:21px;font-weight:700;'
        f'letter-spacing:-0.02em;margin:0 0 4px 0;">🛡️ Supervisor Portal</h1>'
        f'<p style="color:{TEXT_MUTED};margin:0 0 14px 0;font-size:13px;">'
        f'Site scope: <code>{html.escape(site_id)}</code>. Submit material requests '
        f'for workers on your site — the Store Keeper approves and the entries '
        f'land in the HOD\'s EOD review automatically.</p>',
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["📦 Request Material"])
    with tabs[0]:
        sub_new, sub_my, sub_iva = st.tabs([
            "🆕 New Request", "📋 My Requests", "📊 Intent vs Actual",
        ])
        with sub_new:
            _render_new_request_subtab(user, site_id)
        with sub_my:
            _render_my_requests_subtab(user, site_id)
        with sub_iva:
            _render_intent_vs_actual_subtab(user, site_id)
