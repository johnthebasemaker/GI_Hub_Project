"""
pages_internal/hod_portal.py — Head of Department Portal + EOD Confirm Dialog
=============================================================================
Claude Design adapt — Preservation Rule honored:
  * EOD confirm dialog ('type COMMIT' modal) — UNCHANGED.
  * WhatsApp post-EOD queuing — UNCHANGED.
  * Cross-site inquiry_cart workflow — additive (matrix on top).
  * PR PDF upload + Outlook email + PDF download — UNCHANGED.
  * commit_eod, commit_pending_receipts, process_receipt_delivery
    signatures — UNCHANGED.
  * 'My Requests' tab — preserved at end.
"""

import datetime
import html

import pandas as pd
import streamlit as st

from config import AGGRID_HEIGHT, BRAND_GOLD, TEXT_PRIMARY, TEXT_MUTED, COLOR_OK, COLOR_LOW, COLOR_CRITICAL
from database import (
    get_connection,
    commit_eod,
    get_low_stock_items,
    get_pending_issues_for_site,
    get_pending_requests,
    create_request,
    update_request_status,
    process_pr_pdf,
    process_receipt_delivery,
    get_pending_receipts_for_hod,
    commit_pending_receipts,
    queue_whatsapp_alert,
    log_audit_action,
    get_phone_by_username,
    # HOD adapt
    get_app_setting,
    set_app_setting,
    insert_manual_pr,
    update_pr_workflow_state,
    reject_pending_receipt,
    get_all_sites_stock_matrix,
    get_receipt_history,
    hod_approve_pending_issue,
    hod_reject_pending_issue,
    hod_approve_all_pending_issues,
    get_work_types,
    get_tank_nos,
    add_site_dropdown_value,
    delete_site_dropdown_value,
)
from cache_layer import (
    cached_work_types,
    cached_tank_nos,
    cached_sites,
    cached_live_inventory,
    cached_low_stock_items,
    cached_short_dated_stock,
    cached_burn_rate_and_forecast,
    bust_inventory_cache,
    bust_settings_cache,
)
from ui_components import (
    render_brand_header_hod,
    render_aggrid,
    render_burn_rate_chart,
    render_burn_alert_banner,
    render_empty_state,
    render_hero_metrics,
    status_pill_html,
)

# Design colour tokens — kept in lock-step with HOD Portal.html `C` map.
_C = {
    "surf":   "#162038",
    "surf2":  "#1E3050",
    "border": "#2A4060",
    "gold":   "#D4AF37",
    "goldLt": "#F0D060",
    "blueLt": "#1A4D80",
    "text":   "#F0F4F8",
    "muted":  "#7A8FA0",
    "dim":    "#4A6080",
    "ok":     "#22C55E",
    "low":    "#F59E0B",
    "crit":   "#EF4444",
    "purple": "#A855F7",
}


# ===========================================================================
# HTML-TABLE HELPERS  (custom row-by-row tables that match Claude Design)
# ---------------------------------------------------------------------------
# We keep these inline — they are only used by the two tabs that need per-row
# action buttons (EOD Commit + Pending Receipts). All other tabs use AgGrid.
# ===========================================================================
def _esc(v) -> str:
    """HTML-escape a cell value; None / NaN render as an em-dash."""
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    return html.escape(str(v))


def _html_table(rows_html: str, columns: list[str]) -> str:
    """Wrap a sequence of <tr> rows in the standard HOD-table chrome."""
    head = "".join(
        f'<th style="padding:8px 10px;color:{_C["muted"]};font-weight:600;'
        f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
        f'text-align:left;white-space:nowrap;">{html.escape(c)}</th>'
        for c in columns
    )
    return (
        f'<div style="overflow-x:auto;border-radius:8px;border:1px solid {_C["border"]};">'
        f'<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
        f'<thead><tr style="background:{_C["surf2"]};border-bottom:1px solid {_C["border"]};">'
        f'{head}</tr></thead>'
        f'<tbody>{rows_html}</tbody></table></div>'
    )


# ===========================================================================
# EOD COMMIT CONFIRMATION DIALOG  (UNCHANGED — type COMMIT to enable)
# ===========================================================================
@st.dialog("Confirm EOD Commit")
def _eod_commit_dialog() -> None:
    site_id = st.session_state.get("_eod_pending_site", "")
    user = st.session_state.get("_eod_pending_user", {})
    edited_admin_df = st.session_state.get("_eod_pending_df")
    pending_count = st.session_state.get("_eod_pending_count", 0)

    if edited_admin_df is None:
        st.error("No pending data found. Close this dialog and try again.")
        return

    # ── PRE-FLIGHT: negative-stock guard ────────────────────────────────────
    # Choke-point safety check. Even if Entry-Log's over-issue guard was
    # bypassed (HOD edits inside this review, OCR bulk staging, PWA queue),
    # this is the LAST gate before the ledger is touched.
    from database import validate_eod_no_negative_stock
    _gate_conn = get_connection()
    try:
        violations = validate_eod_no_negative_stock(
            _gate_conn, site_id, edited_admin_df,
        )
    finally:
        _gate_conn.close()

    if violations:
        st.error(
            f"🛑 **Cannot commit — {len(violations)} item(s) would go negative.**\n\n"
            "Fix the staging rows (reduce qty, drop the entry, or receive stock "
            "first), then re-open this dialog."
        )
        # Render a clean violation table
        rows_html = []
        for v in violations:
            rows_html.append(
                f'<tr style="background:rgba(239,68,68,0.06);'
                f'border-bottom:1px solid rgba(42,64,96,0.4);">'
                f'<td style="padding:7px 10px;color:#D4AF37CC;font-family:monospace;'
                f'font-size:11.5px;">{html.escape(str(v["sap_code"]))}</td>'
                f'<td style="padding:7px 10px;color:#F0F4F8;max-width:220px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{html.escape(str(v["name"]))}</td>'
                f'<td style="padding:7px 10px;color:#F0F4F8;font-weight:600;'
                f'font-family:monospace;">{v["current"]:g} '
                f'<span style="color:#7A8FA0;">{html.escape(str(v["uom"]))}</span></td>'
                f'<td style="padding:7px 10px;color:#F59E0B;font-weight:700;'
                f'font-family:monospace;">{v["to_consume"]:g}</td>'
                f'<td style="padding:7px 10px;color:#EF4444;font-weight:700;'
                f'font-family:monospace;">-{v["deficit"]:g}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<div style="overflow-x:auto;border-radius:8px;'
            f'border:1px solid rgba(42,64,96,0.5);">'
            f'<table style="width:100%;border-collapse:collapse;font-size:12.5px;">'
            f'<thead><tr style="background:rgba(30,48,80,0.6);">'
            f'<th style="padding:8px 10px;color:#7A8FA0;font-weight:600;'
            f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
            f'text-align:left;">SAP</th>'
            f'<th style="padding:8px 10px;color:#7A8FA0;font-weight:600;'
            f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
            f'text-align:left;">Material</th>'
            f'<th style="padding:8px 10px;color:#7A8FA0;font-weight:600;'
            f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
            f'text-align:left;">Current Stock</th>'
            f'<th style="padding:8px 10px;color:#7A8FA0;font-weight:600;'
            f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
            f'text-align:left;">Trying to Consume</th>'
            f'<th style="padding:8px 10px;color:#7A8FA0;font-weight:600;'
            f'font-size:10px;letter-spacing:0.07em;text-transform:uppercase;'
            f'text-align:left;">Deficit</th>'
            f'</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>',
            unsafe_allow_html=True,
        )
        st.info(
            "💡 **Common fixes:** receive the inbound stock first, raise a "
            "stock adjustment to correct the system qty, or reduce the "
            "consumption to fit available stock."
        )
        if st.button("Close", width="stretch", key="_eod_blocked_close"):
            for k in ("_eod_pending_df", "_eod_pending_site",
                      "_eod_pending_user", "_eod_pending_count",
                      "_eod_confirm_check"):
                st.session_state.pop(k, None)
            st.rerun()
        return

    st.warning(
        f"You are about to commit **{pending_count}** pending row(s) for site **{site_id}** "
        f"on **{datetime.date.today().isoformat()}**.\n\n"
        "This moves data into the permanent ledger and recomputes live stock. "
        "**It cannot be undone.**"
    )
    confirm_checked = st.checkbox(
        "I have reviewed the pending rows and confirm the commit.",
        key="_eod_confirm_check",
        value=False,
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Cancel", width="stretch", key="_eod_cancel_btn"):
            for k in ("_eod_pending_df", "_eod_pending_site",
                      "_eod_pending_user", "_eod_pending_count",
                      "_eod_confirm_check"):
                st.session_state.pop(k, None)
            st.rerun()
    with c2:
        disabled = not confirm_checked
        if st.button(
            "Confirm Commit",
            type="primary",
            width="stretch",
            disabled=disabled,
            key="_eod_confirm_btn",
        ):
            conn = get_connection()
            try:
                c = conn.cursor()
                c.execute(
                    "DELETE FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ? "
                    "AND COALESCE(status,'pending_hod') = 'pending_hod'",
                    (site_id,),
                )
                # The EOD tab JOINs inventory (Material_Code/Material_Name/UOM)
                # for the display table; those columns don't exist on
                # pending_issues and INSERT-from-DataFrame would crash. Filter
                # the frame to only schema-valid columns before persisting.
                _pi_cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(pending_issues)"
                ).fetchall()}
                _safe_df = edited_admin_df[
                    [c for c in edited_admin_df.columns if c in _pi_cols]
                ]
                _safe_df.to_sql(
                    "pending_issues", conn,
                    if_exists="append", index=False,
                )
                conn.commit()
                n = commit_eod(conn)

                # --- 📱 WHATSAPP AUTOMATION (preserved) ---
                low_df = get_low_stock_items(conn)
                if not low_df.empty:
                    hod_phone = get_phone_by_username(user.get("username", ""))
                    if hod_phone:
                        critical_count = len(low_df[low_df["Current_Stock"] <= 0])
                        warning_count = len(low_df) - critical_count
                        alert_msg = (
                            f"🚨 *POST-EOD STOCK ALERT ({site_id})*\n"
                            "Commit successful, but your inventory has dropped below safe levels:\n\n"
                            f"🔴 {critical_count} Critical (Empty/Negative)\n"
                            f"🟡 {warning_count} Low Stock\n\n"
                            "Please check the Live Dashboard."
                        )
                        queue_whatsapp_alert(hod_phone, alert_msg)
            finally:
                conn.close()

            bust_inventory_cache()

            for k in ("_eod_pending_df", "_eod_pending_site",
                      "_eod_pending_user", "_eod_pending_count",
                      "_eod_confirm_check"):
                st.session_state.pop(k, None)

            st.balloons()
            st.success(f"✅ {n} record(s) committed to Master Database!")
            st.rerun()


# ===========================================================================
# TAB 1 — EOD COMMIT  (stat strip + filter pills + custom HTML table)
# ===========================================================================
def _render_eod_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📤 End-of-Day Commit ({_esc(site_id)})</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Review pending consumption entries, approve / reject row-by-row, '
        f'then commit to the master ledger.</p>',
        unsafe_allow_html=True,
    )

    conn = get_connection()
    try:
        pending_df = get_pending_issues_for_site(conn, site_id)
        # JOIN inventory so the table can show Material_Code, Material_Name
        # and UOM — none of which live on `pending_issues`.
        inv_df = pd.read_sql(
            "SELECT SAP_Code, Material_Code, Equipment_Description AS Material_Name, UOM "
            "FROM inventory", conn,
        )
    finally:
        conn.close()

    if pending_df.empty:
        render_empty_state(
            icon="✅",
            title="Staging queue is clear",
            hint="When a store keeper submits an issue log, it appears here for your EOD commit.",
        )
        return

    pending_df = pending_df.copy()
    if not inv_df.empty:
        pending_df = pending_df.merge(
            inv_df, on="SAP_Code", how="left", suffixes=("", "_inv"),
        )

    # Normalise the status column for filtering. Pending-from-DB stays "pending".
    pending_df["_status"] = pending_df.get("status", "pending_hod").fillna("pending_hod")
    pending_df["_status"] = pending_df["_status"].replace(
        {"pending_hod": "pending", "": "pending"}
    )

    def _cnt(s: str) -> int:
        return int((pending_df["_status"] == s).sum())

    total_n  = len(pending_df)
    pend_n   = _cnt("pending")
    flag_n   = _cnt("flagged")
    appr_n   = _cnt("approved")
    rej_n    = _cnt("rejected")

    # 4-card stat strip
    render_hero_metrics([
        {"label": "📋 Total entries", "value": total_n,
         "tone": "neutral", "delta": "in staging queue"},
        {"label": "⏳ Pending", "value": pend_n,
         "tone": "low" if pend_n else "ok",
         "delta": "awaiting review" if pend_n else "all clear"},
        {"label": "⚠️ Flagged", "value": flag_n,
         "tone": "critical" if flag_n else "neutral",
         "delta": "need attention" if flag_n else "—"},
        {"label": "✅ Approved", "value": appr_n,
         "tone": "ok" if appr_n else "neutral",
         "delta": "ready to commit"},
    ])
    st.write("")

    # Filter pills
    filt_key = "_hod_eod_filter"
    if filt_key not in st.session_state:
        st.session_state[filt_key] = "all"
    pill_cols = st.columns([1, 1, 1, 1, 1, 1, 4])
    for i, label in enumerate(["all", "pending", "flagged", "approved", "rejected"]):
        with pill_cols[i]:
            if st.button(
                label.capitalize(),
                key=f"_hod_eod_pill_{label}",
                type="primary" if st.session_state[filt_key] == label else "secondary",
                use_container_width=True,
            ):
                st.session_state[filt_key] = label
                st.rerun()
    active_filter = st.session_state[filt_key]

    # Top action row — Approve-All + Commit
    a1, a2 = st.columns([1, 1])
    with a1:
        if st.button("✅ Approve All Pending", use_container_width=True,
                     disabled=(pend_n == 0), key="_hod_eod_approve_all"):
            n = hod_approve_all_pending_issues(site_id=site_id)
            st.toast(f"Approved {n} pending row(s)", icon="✅")
            st.rerun()
    with a2:
        if st.button("📤 Commit EOD to Master", type="primary",
                     use_container_width=True, key="_hod_eod_commit_btn"):
            st.session_state["_eod_pending_df"] = pending_df.drop(columns=["_status"], errors="ignore")
            st.session_state["_eod_pending_site"] = site_id
            st.session_state["_eod_pending_user"] = user
            st.session_state["_eod_pending_count"] = total_n
            _eod_commit_dialog()
            return

    # Filter the view
    if active_filter != "all":
        view_df = pending_df[pending_df["_status"] == active_filter]
    else:
        view_df = pending_df

    if view_df.empty:
        st.caption(f"No rows match the '{active_filter}' filter.")
        return

    # Custom HTML table — shows EVERY user-filled column from the Entry Log.
    # Per-row buttons can't be embedded in HTML, so the action panel is
    # rendered separately below.
    display_cols = [
        ("Date", "Date"),
        ("SAP_Code", "SAP"),
        ("Material_Code", "Mat Code"),
        ("Material_Name", "Material"),
        ("UOM", "UOM"),
        ("Quantity", "Qty"),
        ("Work_Type", "Work Type"),
        ("PR_Number", "PR"),
        ("Tank_No", "Tank"),
        ("Serial_No", "Serial"),
        ("Issued_By", "Issued By"),
        ("Issued_To", "Issued To"),
        ("Remarks", "Remarks"),
        ("Status", "Status"),
    ]
    # Drop columns that don't exist in the dataframe (defensive)
    display_cols = [
        (c, h) for c, h in display_cols
        if c == "Status" or c in view_df.columns
    ]

    rows_html = []
    for i, (_, r) in enumerate(view_df.iterrows()):
        bg = _C["surf2"] + "44" if i % 2 else "transparent"
        cells = []
        for col, _ in display_cols:
            if col == "Status":
                cells.append(
                    f'<td style="padding:7px 10px;">{status_pill_html(r["_status"])}</td>'
                )
            elif col == "SAP_Code":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["gold"]}CC;'
                    f'font-family:monospace;font-size:11.5px;white-space:nowrap;">'
                    f'{_esc(r.get(col))}</td>'
                )
            elif col == "Material_Code":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["muted"]};'
                    f'font-family:monospace;font-size:11px;white-space:nowrap;">'
                    f'{_esc(r.get(col))}</td>'
                )
            elif col == "Material_Name":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["text"]};font-size:12px;'
                    f'max-width:220px;overflow:hidden;text-overflow:ellipsis;'
                    f'white-space:nowrap;" title="{_esc(r.get(col))}">{_esc(r.get(col))}</td>'
                )
            elif col == "Quantity":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:700;'
                    f'white-space:nowrap;">{_esc(r.get(col))}</td>'
                )
            else:
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                    f'white-space:nowrap;max-width:140px;overflow:hidden;'
                    f'text-overflow:ellipsis;" title="{_esc(r.get(col))}">'
                    f'{_esc(r.get(col))}</td>'
                )
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            + "".join(cells) + "</tr>"
        )
    st.markdown(_html_table("".join(rows_html), [h for _, h in display_cols]),
                unsafe_allow_html=True)

    # Per-row Approve / Reject panel — now shows the full entry details
    actionable = view_df[view_df["_status"].isin(["pending", "flagged"])].head(20)
    if not actionable.empty:
        st.markdown(
            f'<div style="color:{_C["muted"]};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin:14px 0 6px 0;">'
            f'Row Actions · top {len(actionable)} actionable</div>',
            unsafe_allow_html=True,
        )
        for _, r in actionable.iterrows():
            rid = int(r["id"])
            cA, cB, cC = st.columns([8, 1, 1])
            with cA:
                # Build a rich two-line summary so the HOD can decide without
                # cross-referencing the table above.
                top_line = (
                    f'<span style="color:{_C["gold"]};font-family:monospace;font-size:12px;">'
                    f'[{_esc(r.get("SAP_Code"))}]</span> '
                    f'<span style="color:{_C["muted"]};font-family:monospace;font-size:11px;">'
                    f'{_esc(r.get("Material_Code")) or "—"}</span> · '
                    f'<span style="color:{_C["text"]};font-weight:600;">'
                    f'{_esc(r.get("Material_Name") or "—")}</span> · '
                    f'<b style="color:{_C["text"]};">{_esc(r.get("Quantity"))}</b> '
                    f'<span style="color:{_C["muted"]};">{_esc(r.get("UOM")) or ""}</span>'
                )
                # Second line: every non-empty entry-form field
                meta_pairs = []
                for label, key in [
                    ("Date", "Date"), ("Work Type", "Work_Type"),
                    ("PR", "PR_Number"), ("Tank", "Tank_No"),
                    ("Serial", "Serial_No"),
                    ("By", "Issued_By"), ("To", "Issued_To"),
                    ("Remarks", "Remarks"),
                ]:
                    v = r.get(key)
                    if v not in (None, "", "nan") and not (
                        isinstance(v, float) and pd.isna(v)
                    ):
                        meta_pairs.append(
                            f'<span style="color:{_C["dim"]};">{label}:</span> '
                            f'<span style="color:{_C["muted"]};">{_esc(v)}</span>'
                        )
                bottom_line = " · ".join(meta_pairs) if meta_pairs else ""
                st.markdown(
                    f'<div style="padding:8px 0;border-bottom:1px solid {_C["border"]}33;">'
                    f'<div style="font-size:12.5px;line-height:1.5;">{top_line}</div>'
                    f'<div style="font-size:11px;margin-top:3px;line-height:1.4;">{bottom_line}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with cB:
                if st.button("✓", key=f"_hod_eod_appr_{rid}", use_container_width=True):
                    hod_approve_pending_issue(rid)
                    st.rerun()
            with cC:
                if st.button("✗", key=f"_hod_eod_rej_{rid}", use_container_width=True):
                    hod_reject_pending_issue(rid)
                    st.rerun()

    # Flagged banner (informational)
    if flag_n > 0:
        st.markdown(
            f'<div style="margin-top:12px;background:{_C["low"]}10;'
            f'border:1px solid {_C["low"]}33;border-radius:8px;padding:10px 14px;'
            f'color:{_C["low"]};font-size:13px;">'
            f'⚠️ <strong>{flag_n} flagged item(s)</strong> — verify with the store '
            f'keeper before committing.</div>',
            unsafe_allow_html=True,
        )


# ===========================================================================
# TAB 2 — CROSS-SITE INQUIRY  (matrix top + existing single-target form below)
# ===========================================================================
def _render_crosssite_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🌐 Cross-Site Inquiry</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Request material from another branch. A cart of <b>more than 5 items</b> '
        f'automatically escalates a WhatsApp to the target site HOD.</p>',
        unsafe_allow_html=True,
    )

    # ── Single-target inquiry flow (classic, preserved verbatim) ────────
    if "inquiry_cart" not in st.session_state:
        st.session_state["inquiry_cart"] = []

    st.subheader("Request Material From Another Branch (classic flow)")
    conn = get_connection()

    col1, col2 = st.columns(2)
    with col1:
        all_sites = cached_sites()
        other_sites = [s for s in all_sites if s != site_id]
        target_site = st.selectbox("Select Target Branch:", other_sites)

        inv_df = pd.read_sql("SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn)
        inv_df["Search_String"] = "[" + inv_df["SAP_Code"].astype(str) + "] " + inv_df["Equipment_Description"].astype(str)
        item_selection = st.selectbox("Select Material:", inv_df["Search_String"].tolist())

        req_qty = st.number_input("Quantity Needed:", min_value=1.0, step=1.0)
        notes = st.text_area("Justification / Notes:")

    with col2:
        if target_site and item_selection:
            sap_code = item_selection.split("]")[0].replace("[", "").strip()
            live_target_df = cached_live_inventory(site_id=target_site)
            match = live_target_df[live_target_df["SAP_Code"] == sap_code]
            avail_qty = float(match.iloc[0]["Current_Stock"]) if not match.empty else 0.0
            suggested = min(req_qty, avail_qty)

            st.markdown(f"### 📊 Live Stock at **{target_site}**")
            st.metric("Available Quantity", f"{avail_qty}")
            st.metric(
                "Suggested Transfer Qty", f"{suggested}",
                delta="Based on availability" if avail_qty > 0 else "Out of stock",
                delta_color="normal" if avail_qty > 0 else "inverse",
            )

            if st.button("➕ Add to List", type="primary", use_container_width=True):
                if avail_qty <= 0:
                    st.error(f"Cannot request. {target_site} has no stock of this item.")
                else:
                    st.session_state["inquiry_cart"].append({
                        "Target Site":    target_site,
                        "SAP Code":       sap_code,
                        "Description":    item_selection,
                        "Qty":            req_qty,
                        "Notes":          notes,
                        "_available_qty": avail_qty,
                        "_suggested_qty": suggested,
                    })
                    st.toast(
                        f"🛒 Added to cart ({len(st.session_state['inquiry_cart'])} items)",
                        icon="🛒",
                    )
                    st.rerun()

    if st.session_state["inquiry_cart"]:
        st.write("---")
        st.markdown(
            f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
            f'margin:0 0 8px 0;">🛒 Your Request Cart</h4>',
            unsafe_allow_html=True,
        )
        cart_rows_html = []
        for i, item in enumerate(st.session_state["inquiry_cart"]):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            cart_rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;color:{_C["purple"]}DD;font-weight:600;'
                f'white-space:nowrap;">{_esc(item["Target Site"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
                f'font-size:11.5px;">{_esc(item["SAP Code"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};max-width:260px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{_esc(item["Description"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:700;">'
                f'{_esc(item["Qty"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{_esc(item.get("Notes"))}">{_esc(item.get("Notes"))}</td>'
                f'</tr>'
            )
        st.markdown(
            _html_table("".join(cart_rows_html),
                        ["Target Site", "SAP Code", "Description", "Qty", "Notes"]),
            unsafe_allow_html=True,
        )

        col_submit, col_clear = st.columns([3, 1])
        with col_submit:
            if st.button("📨 Submit All Requests to Admin", type="primary", use_container_width=True):
                count = len(st.session_state["inquiry_cart"])
                for item in st.session_state["inquiry_cart"]:
                    create_request(
                        conn,
                        requesting_site=site_id,
                        target_site=item["Target Site"],
                        sap_code=item["SAP Code"],
                        requested_qty=item["Qty"],
                        available_qty=item["_available_qty"],
                        suggested_qty=item["_suggested_qty"],
                        notes=item["Notes"],
                        requested_by=user["username"],
                    )
                _tgt_sites = list({i["Target Site"] for i in st.session_state["inquiry_cart"]})
                _admin_phones_df = pd.read_sql(
                    "SELECT Phone_Number FROM users WHERE role = 'admin' "
                    "AND Phone_Number IS NOT NULL AND Phone_Number != ''",
                    conn,
                )
                _admin_creation_msg = (
                    f"🚚 *NEW CROSS-SITE TRANSFER REQUEST*\n"
                    f"📍 Requesting Site: *{site_id}*\n"
                    f"🎯 Target Site(s): *{', '.join(_tgt_sites)}*\n"
                    f"👤 Submitted by: {user['username']}\n"
                    f"📦 Items in cart: *{count}*\n\n"
                    f"Please log in to the Admin Portal → Pending Requests to review and approve."
                )
                for _, _ap in _admin_phones_df.iterrows():
                    if _ap["Phone_Number"] and len(str(_ap["Phone_Number"])) >= 5:
                        queue_whatsapp_alert(str(_ap["Phone_Number"]), _admin_creation_msg)

                # ── ESCALATION: >5 items also pings the target-site HOD ─────
                # Cuts the admin out of the critical path for bulky carts —
                # the target HOD can approve directly from their portal.
                if count > 5:
                    for _tgt in _tgt_sites:
                        _tgt_hod_q = pd.read_sql(
                            "SELECT Phone_Number, username FROM users "
                            "WHERE role='hod' AND Site_ID=? "
                            "AND Phone_Number IS NOT NULL AND Phone_Number<>'' "
                            "LIMIT 1",
                            conn, params=(_tgt,),
                        )
                        if not _tgt_hod_q.empty:
                            _ph = str(_tgt_hod_q.iloc[0]["Phone_Number"])
                            _msg = (
                                f"📥 *BULK CROSS-SITE REQUEST — {count} items*\n"
                                f"From: *{site_id}* (HOD {user['username']})\n"
                                f"To:   *{_tgt}*\n\n"
                                f"Open your HOD Portal → Cross-Site Inquiry → "
                                f"📥 Incoming Requests to review."
                            )
                            queue_whatsapp_alert(_ph, _msg)
                            log_audit_action(
                                user["username"], "CROSS_SITE_BULK_ESCALATION",
                                "requests",
                                f"to_hod={_tgt_hod_q.iloc[0]['username']} count={count}",
                            )

                st.session_state["inquiry_cart"] = []
                st.success(f"✅ {count} request(s) submitted to Admin"
                           + (f" + escalated to target HOD(s)" if count > 5 else "") + ".")
                st.rerun()
        with col_clear:
            if st.button("🗑️ Clear List", use_container_width=True):
                st.session_state["inquiry_cart"] = []
                st.rerun()

    conn.close()

    # ── INCOMING REQUESTS (where I am the target site) ──────────────────
    # Powers the >5-item escalation flow: a target-site HOD lands here,
    # reviews the bulk request from the requesting HOD, and approves
    # without waiting on the admin.
    st.divider()
    st.markdown(
        f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
        f'margin:6px 0 4px 0;">📥 Incoming Cross-Site Requests</h4>'
        f'<p style="color:{_C["muted"]};font-size:12px;margin:0 0 10px 0;">'
        f'Requests addressed to <b style="color:{_C["gold"]};">{html.escape(str(site_id))}</b> '
        f'from other site HODs. Approving makes the requesting HOD able to '
        f'fulfill the transfer.</p>',
        unsafe_allow_html=True,
    )
    _in_conn = get_connection()
    try:
        incoming_df = pd.read_sql(
            """SELECT r.id, r.requesting_site, r.target_site, r.SAP_Code,
                      r.requested_qty, r.status, r.created_at, r.requested_by,
                      r.notes, COALESCE(i.Equipment_Description,'') AS Material
               FROM requests r
               LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code
               WHERE r.target_site = ?
                 AND r.status IN ('pending','open')
               ORDER BY r.created_at DESC""",
            _in_conn, params=(site_id,),
        )
    except Exception:
        incoming_df = pd.DataFrame()
    finally:
        _in_conn.close()

    if incoming_df.empty:
        st.caption("No incoming cross-site requests right now.")
    else:
        # Bulk-request grouping: same requesting_site + same minute = same cart.
        # We pill the requesting site + show the row count so a >5 escalation
        # stands out visually.
        rows_html = []
        for i, (_, r) in enumerate(incoming_df.iterrows()):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;color:{_C["purple"]}DD;font-weight:600;">'
                f'{_esc(r["requesting_site"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
                f'font-size:11.5px;">{_esc(r["SAP_Code"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};max-width:200px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{_esc(r["Material"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:600;">'
                f'{_esc(r["requested_qty"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;">'
                f'{_esc(r["requested_by"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{_esc(r.get("notes"))}">{_esc(r.get("notes"))}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11px;'
                f'white-space:nowrap;">{_esc(r["created_at"])}</td>'
                f'</tr>'
            )
        st.markdown(
            _html_table("".join(rows_html),
                        ["From", "SAP", "Material", "Qty", "By", "Notes", "When"]),
            unsafe_allow_html=True,
        )

        # Per-row approve / reject for the top 10 incoming requests
        st.markdown(
            f'<div style="color:{_C["muted"]};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin:14px 0 6px 0;">'
            f'Approve incoming requests</div>',
            unsafe_allow_html=True,
        )
        for _, r in incoming_df.head(10).iterrows():
            rid = int(r["id"])
            cA, cB, cC = st.columns([6, 1, 1])
            with cA:
                st.markdown(
                    f'<div style="padding:6px 0;color:{_C["text"]};font-size:12.5px;">'
                    f'<span style="color:{_C["purple"]};font-weight:600;">'
                    f'{_esc(r["requesting_site"])}</span> requests '
                    f'<b>{_esc(r["requested_qty"])}</b> of '
                    f'<span style="color:{_C["gold"]};font-family:monospace;">'
                    f'[{_esc(r["SAP_Code"])}]</span> '
                    f'{_esc(r["Material"])}</div>',
                    unsafe_allow_html=True,
                )
            with cB:
                if st.button("✓ Approve", key=f"_xs_in_appr_{rid}",
                             use_container_width=True):
                    _c2 = get_connection()
                    try:
                        update_request_status(
                            _c2, rid, "approved", user["username"],
                            f"Approved by target-site HOD {user['username']}",
                        )
                        log_audit_action(
                            user["username"], "HOD_APPROVE_INCOMING_REQUEST",
                            "requests", f"id={rid}",
                        )
                    finally:
                        _c2.close()
                    st.toast("Approved", icon="✅")
                    st.rerun()
            with cC:
                if st.button("✗ Reject", key=f"_xs_in_rej_{rid}",
                             use_container_width=True):
                    _c2 = get_connection()
                    try:
                        update_request_status(
                            _c2, rid, "rejected", user["username"],
                            f"Rejected by target-site HOD {user['username']}",
                        )
                        log_audit_action(
                            user["username"], "HOD_REJECT_INCOMING_REQUEST",
                            "requests", f"id={rid}",
                        )
                    finally:
                        _c2.close()
                    st.toast("Rejected", icon="🚫")
                    st.rerun()


# ===========================================================================
# TAB 3 — BURN RATE  (compact bar chart + existing Plotly chart + table)
# ===========================================================================
def _render_burn_rate_tab(site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📈 Burn Rate Forecast</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Projected stock depletion based on the last 30 days of consumption.</p>',
        unsafe_allow_html=True,
    )

    forecast_df = cached_burn_rate_and_forecast(site_id=site_id)

    if forecast_df is None or forecast_df.empty:
        render_empty_state(
            icon="📈",
            title="Not enough consumption history yet",
            hint="The burn-rate chart needs at least a few committed consumption rows.",
        )
        return

    # Compact horizontal-bar chart — bar colour reflects BURN INTENSITY
    # (red = burning fast, amber = mid, green = slow), giving a HOD an
    # at-a-glance read on which items to refill first. The days-remaining
    # badge on the right amplifies the same signal.
    bar_df = forecast_df.copy()
    if "Daily_Burn_Rate" in bar_df.columns:
        bar_df["_monthly"] = pd.to_numeric(
            bar_df["Daily_Burn_Rate"], errors="coerce",
        ).fillna(0) * 30.0
    else:
        bar_df["_monthly"] = 0
    bar_df = bar_df.sort_values("_monthly", ascending=False).head(10)
    max_m = max(float(bar_df["_monthly"].max() or 1.0), 1.0)

    rows = []
    for _, r in bar_df.iterrows():
        monthly = float(r["_monthly"])
        pct = monthly / max_m * 100.0

        # Intensity colour — proportional to monthly burn.
        if pct >= 66:
            bar_a, bar_b = _C["crit"], "#F87171"
            qty_col = _C["crit"]
        elif pct >= 33:
            bar_a, bar_b = _C["low"], "#FBBF24"
            qty_col = _C["low"]
        else:
            bar_a, bar_b = _C["ok"], "#34D399"
            qty_col = _C["ok"]

        # Days-remaining badge — based on stock, not burn intensity.
        days_left_raw = r.get("Days_Remaining")
        try:
            dl = (
                int(float(days_left_raw))
                if days_left_raw is not None and not pd.isna(days_left_raw)
                else 999
            )
        except (TypeError, ValueError):
            dl = 999
        dl_col = _C["crit"] if dl <= 7 else (_C["low"] if dl <= 30 else _C["ok"])
        dl_lbl = "EMPTY" if dl <= 0 else f"{dl}d"

        uom = str(r.get("UOM") or "").strip()
        rows.append(
            f'<div style="display:grid;grid-template-columns:200px 1fr 86px 64px;'
            f'gap:12px;align-items:center;padding:5px 0;'
            f'border-bottom:1px solid {_C["border"]}22;">'
            # Material name
            f'<div style="color:{_C["text"]};font-size:11.5px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;" '
            f'title="{_esc(r.get("Equipment_Description"))}">'
            f'{_esc(r.get("Equipment_Description"))}</div>'
            # Bar
            f'<div style="background:{_C["bg" if False else "border"]}33;'
            f'border-radius:99px;height:7px;overflow:hidden;position:relative;">'
            f'<div style="width:{pct:.1f}%;height:100%;border-radius:99px;'
            f'background:linear-gradient(90deg,{bar_a},{bar_b});'
            f'box-shadow:0 0 8px {bar_a}55;transition:width .4s ease;"></div></div>'
            # Monthly figure
            f'<div style="color:{qty_col};font-size:11.5px;font-weight:700;'
            f'text-align:right;font-variant-numeric:tabular-nums;'
            f'font-family:\'SF Mono\',Menlo,monospace;">'
            f'{monthly:,.0f}{("/"+uom) if uom else "/mo"}</div>'
            # Days-left badge
            f'<div style="text-align:right;">'
            f'<span style="background:{dl_col}18;border:1px solid {dl_col}44;color:{dl_col};'
            f'font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;'
            f'white-space:nowrap;letter-spacing:0.04em;">{dl_lbl}</span></div></div>'
        )

    st.markdown(
        f'<div style="background:linear-gradient(180deg,{_C["surf2"]},{_C["surf"]});'
        f'border:1px solid {_C["border"]};border-radius:12px;'
        f'padding:14px 18px 6px 18px;margin-bottom:18px;'
        f'box-shadow:0 1px 0 {_C["border"]}44;">'
        # Header row with legend
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin-bottom:10px;">'
        f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
        f'letter-spacing:0.01em;">Monthly Consumption · Top 10</div>'
        f'<div style="display:flex;gap:10px;align-items:center;font-size:10px;'
        f'color:{_C["dim"]};letter-spacing:0.06em;text-transform:uppercase;">'
        f'<span><span style="display:inline-block;width:8px;height:8px;border-radius:99px;'
        f'background:{_C["crit"]};margin-right:5px;vertical-align:middle;"></span>High</span>'
        f'<span><span style="display:inline-block;width:8px;height:8px;border-radius:99px;'
        f'background:{_C["low"]};margin-right:5px;vertical-align:middle;"></span>Mid</span>'
        f'<span><span style="display:inline-block;width:8px;height:8px;border-radius:99px;'
        f'background:{_C["ok"]};margin-right:5px;vertical-align:middle;"></span>Low</span>'
        f'</div></div>'
        + "".join(rows) +
        f'</div>',
        unsafe_allow_html=True,
    )

    # Preserved: existing burn alert banner + full plotly chart
    render_burn_alert_banner(forecast_df)
    render_burn_rate_chart(forecast_df)

    st.subheader("Detailed Forecast Table")
    detail_cols = [c for c in [
        "SAP_Code", "Equipment_Description", "UOM",
        "Current_Stock", "Daily_Burn_Rate", "Days_Remaining",
    ] if c in forecast_df.columns]
    render_aggrid(forecast_df[detail_cols].copy(), key="burn_rate_grid", height=AGGRID_HEIGHT)


# ===========================================================================
# TAB 4 — PENDING RECEIPTS  (custom HTML table + inline ✓/✗ + bulk approve)
# ===========================================================================
def _render_pending_receipts_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📬 Pending Receipts — Awaiting Approval</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 12px 0;">'
        f'Receipts staged by Store Keepers. Approving commits to <code>receipts</code> '
        f'and updates live stock.</p>',
        unsafe_allow_html=True,
    )

    # ── Rubber MTC missing — banner + logistics email draft (2026-06)
    from database import get_missing_mtc_for_site, mark_mtc_emailed
    missing_mtc = get_missing_mtc_for_site(site_id)
    if missing_mtc is not None and not missing_mtc.empty:
        st.error(
            f"⚠️ **{len(missing_mtc)} rubber item(s) received without MTC.** "
            "Please follow up with Logistics."
        )
        with st.expander("Show rubber items missing MTC", expanded=False):
            st.dataframe(
                missing_mtc[["SAP_Code", "Equipment_Description", "Lot_Number",
                             "Quantity", "submitted_by", "submitted_at"]],
                use_container_width=True, hide_index=True,
            )
            mc1, mc2 = st.columns([1, 1])
            with mc1:
                if st.button("✉️ Draft Logistics Email", key="_hod_mtc_email"):
                    from mailer import draft_rubber_mtc_email
                    ok, msg = draft_rubber_mtc_email(site_id, missing_mtc)
                    if ok:
                        mark_mtc_emailed([int(x) for x in missing_mtc["id"].tolist()])
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            with mc2:
                if st.button("✓ Mark all as sent", key="_hod_mtc_mark"):
                    mark_mtc_emailed([int(x) for x in missing_mtc["id"].tolist()])
                    st.toast("Marked sent_to_logistics", icon="✅")
                    st.rerun()

    conn_pr = get_connection()
    try:
        pending_rcpt_df = get_pending_receipts_for_hod(conn_pr, site_id=site_id)
    finally:
        conn_pr.close()

    n = len(pending_rcpt_df) if pending_rcpt_df is not None else 0
    if n == 0:
        st.success("✅ No pending receipts awaiting approval.")
        return

    # Pending banner
    st.markdown(
        f'<div style="background:{_C["low"]}10;border:1px solid {_C["low"]}33;'
        f'border-radius:8px;padding:10px 14px;margin-bottom:14px;'
        f'color:{_C["low"]};font-size:13px;">'
        f'⏳ <strong>{n} pending receipt(s)</strong> require your approval before '
        f'stock levels update.</div>',
        unsafe_allow_html=True,
    )

    # Bulk action
    if st.button(f"✅ Approve All ({n})", key="_hod_rcpt_appr_all"):
        with get_connection() as _c:
            committed = commit_pending_receipts(_c, site_id=site_id, username=user["username"])
        if committed > 0:
            log_audit_action(
                user["username"], "HOD_COMMIT_RECEIPTS", "receipts",
                f"Approved + committed {committed} staged receipt(s) for site {site_id}",
            )
            bust_inventory_cache()
            st.toast(f"✅ {committed} receipt(s) committed", icon="✅")
            st.rerun()

    # Display table — SAP → Material_Code → Equipment_Description per spec.
    show_cols = [c for c in ["Date", "SAP_Code", "Material_Code",
                             "Equipment_Description", "UOM",
                             "Quantity", "Supplier", "PR_Number", "Site_ID"]
                 if c in pending_rcpt_df.columns]
    rows_html = []
    for i, (_, r) in enumerate(pending_rcpt_df.iterrows()):
        bg = _C["surf2"] + "44" if i % 2 else "transparent"
        cells = []
        for c in show_cols:
            if c == "SAP_Code":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["gold"]}CC;'
                    f'font-family:monospace;font-size:11.5px;">{_esc(r.get(c))}</td>'
                )
            elif c == "Quantity":
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:700;">'
                    f'{_esc(r.get(c))}</td>'
                )
            else:
                cells.append(
                    f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;">'
                    f'{_esc(r.get(c))}</td>'
                )
        cells.append(
            f'<td style="padding:7px 10px;">{status_pill_html("pending")}</td>'
        )
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            + "".join(cells) + "</tr>"
        )
    st.markdown(
        _html_table("".join(rows_html), show_cols + ["Status"]),
        unsafe_allow_html=True,
    )

    # Per-row reject (approval is via bulk only — commit_pending_receipts
    # currently has no per-row API, so we soft-reject one-at-a-time here).
    st.markdown(
        f'<div style="color:{_C["muted"]};font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.08em;margin:14px 0 6px 0;">'
        f'Reject individual receipt</div>',
        unsafe_allow_html=True,
    )
    for _, r in pending_rcpt_df.head(10).iterrows():
        rid = int(r["id"])
        cA, cB = st.columns([6, 1])
        with cA:
            st.markdown(
                f'<div style="padding:6px 0;color:{_C["text"]};font-size:12.5px;">'
                f'<span style="color:{_C["gold"]};font-family:monospace;">[{_esc(r.get("SAP_Code"))}]</span> '
                f'<span style="color:{_C["muted"]};">Mat: {_esc(r.get("Material_Code"))}</span> · '
                f'<b>{_esc(r.get("Equipment_Description"))}</b> — '
                f'<b>{_esc(r.get("Quantity"))}</b> {_esc(r.get("UOM"))} · '
                f'<span style="color:{_C["muted"]};">{_esc(r.get("Supplier"))}</span></div>',
                unsafe_allow_html=True,
            )
        with cB:
            if st.button("✗ Reject", key=f"_hod_rcpt_rej_{rid}", use_container_width=True):
                if reject_pending_receipt(rid, reason=f"Rejected by HOD {user['username']}",
                                          username=user["username"]):
                    st.toast(f"Receipt #{rid} rejected", icon="🚫")
                    st.rerun()


# ===========================================================================
# TAB 5 — PURCHASE REQUESTS  (Create form + status workflow + existing PDF)
# ===========================================================================
def _render_pr_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📋 Purchase Requests ({_esc(site_id)})</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Create PR lines manually <b>or</b> upload a PR PDF — both paths land in '
        f'the same table. Promote each line through draft → submitted → approved → '
        f'in progress → received.</p>',
        unsafe_allow_html=True,
    )

    conn = get_connection()
    inv_df = pd.read_sql(
        "SELECT SAP_Code, Material_Code, Equipment_Description, UOM FROM inventory",
        conn,
    )
    conn.close()

    # ── 1) MANUAL CREATE FORM (mirrors every field the PDF feature stores) ──
    with st.expander("➕ Create New PR (manual entry)", expanded=False):
        inv_df["_label"] = (
            "[" + inv_df["SAP_Code"].astype(str) + "] "
            + inv_df["Equipment_Description"].astype(str)
        )
        c1, c2 = st.columns(2)
        with c1:
            pr_number = st.text_input(
                "PR Number *",
                placeholder="e.g. 3001234567",
                key="_hod_pr_form_number",
            )
            material_label = st.selectbox(
                "Material (SAP code + description) *",
                options=inv_df["_label"].tolist(),
                index=None,
                placeholder="Pick from master inventory…",
                key="_hod_pr_form_material",
            )
            # Show auto-lookup feedback
            sap_code = ""
            material_code = ""
            material_name = ""
            uom_lookup = ""
            if material_label:
                sap_code = material_label.split("]")[0].replace("[", "").strip()
                row = inv_df[inv_df["SAP_Code"] == sap_code]
                if not row.empty:
                    material_code = str(row.iloc[0].get("Material_Code") or "")
                    material_name = str(row.iloc[0].get("Equipment_Description") or "")
                    uom_lookup = str(row.iloc[0].get("UOM") or "")
                st.markdown(
                    f'<div style="background:{_C["blueLt"]}18;border:1px solid {_C["blueLt"]}44;'
                    f'border-radius:7px;padding:8px 12px;margin:4px 0;'
                    f'color:#5DA4D4;font-size:12px;">'
                    f'<b>SAP:</b> {_esc(sap_code)} · '
                    f'<b>Material Code:</b> {_esc(material_code) or "N/A"} · '
                    f'<b>UOM:</b> {_esc(uom_lookup) or "N/A"}</div>',
                    unsafe_allow_html=True,
                )
            requested_qty = st.number_input(
                "Requested Qty *", min_value=0.01, step=1.0,
                key="_hod_pr_form_qty",
            )
        with c2:
            supplier = st.text_input(
                "Preferred Supplier (optional)",
                placeholder="e.g. Gulf Pipe Supply",
                key="_hod_pr_form_supplier",
            )
            est_cost = st.number_input(
                "Estimated Cost (SAR, optional)",
                min_value=0.0, step=10.0,
                key="_hod_pr_form_cost",
            )
            uom_override = st.text_input(
                "UOM (auto-filled from inventory, can override)",
                value=uom_lookup,
                key="_hod_pr_form_uom",
            )
            notes = st.text_area(
                "Notes (optional)",
                placeholder="Any context — urgency, justification, alt-supplier…",
                key="_hod_pr_form_notes",
                height=70,
            )

        if st.button("📋 Create PR Draft", type="primary", key="_hod_pr_create_btn"):
            if not pr_number.strip() or not material_label:
                st.error("PR Number and Material are required.")
            else:
                ok, msg = insert_manual_pr(
                    pr_number=pr_number,
                    sap_code=sap_code,
                    material_code=material_code,
                    material_name=material_name,
                    requested_qty=requested_qty,
                    site_id=site_id,
                    uom=uom_override,
                    supplier=supplier,
                    est_cost_sar=est_cost,
                    notes=notes,
                )
                if ok:
                    log_audit_action(
                        user["username"], "CREATE_MANUAL_PR", "pr_master",
                        f"{pr_number} / {sap_code} qty={requested_qty}",
                    )
                    st.toast(f"✅ {msg}", icon="📋")
                    st.rerun()
                else:
                    st.error(msg)

    # ── 2) EXISTING PR PDF UPLOAD (preserved verbatim) ──────────────────
    with st.expander("📄 Upload PR PDF (auto-extract via pdfplumber)", expanded=False):
        st.caption(
            "The system extracts the PR Number and matches Material Codes (GI-XXXXXXX) "
            "to your master inventory automatically. Same destination table as the "
            "manual form above."
        )
        uploaded_file = st.file_uploader("Select PR PDF", type=["pdf"], key="_hod_pr_pdf")
        if uploaded_file is not None:
            if st.button("Process Upload", type="primary", key="_hod_pr_pdf_process"):
                with st.spinner("Extracting tables and matching SAP codes…"):
                    pdf_bytes = uploaded_file.read()
                    conn = get_connection()
                    try:
                        success, msg = process_pr_pdf(pdf_bytes, site_id, conn)
                    finally:
                        conn.close()
                if success:
                    st.warning(msg) if "WARNING" in msg else st.success(msg)
                else:
                    st.error(msg)

    # ── 3) PR LIST + STATUS WORKFLOW ────────────────────────────────────
    conn = get_connection()
    pr_df = pd.read_sql(
        """SELECT
              p.id, p.PR_Number, p.SAP_Code, p.Material_Code,
              COALESCE(p.Material_Name, i.Equipment_Description) AS Material_Name,
              COALESCE(p.UOM, i.UOM) AS UOM,
              p.Requested_Qty,
              (p.Requested_Qty - COALESCE((
                  SELECT SUM(Quantity) FROM receipts
                  WHERE PR_Number = p.PR_Number AND SAP_Code = p.SAP_Code
                    AND Site_ID = p.Site_ID
              ), 0)) AS Pending_Qty,
              p.status, p.workflow_state, p.Supplier, p.Est_Cost_SAR,
              p.created_at
           FROM pr_master p
           LEFT JOIN inventory i ON p.SAP_Code = i.SAP_Code
           WHERE p.Site_ID = ?
           ORDER BY p.created_at DESC""",
        conn, params=(site_id,),
    )
    conn.close()

    if pr_df.empty:
        render_empty_state(
            icon="📋",
            title="No purchase requests on file",
            hint="Use the form above or upload a PR PDF to start tracking fulfillment.",
        )
        return

    st.write(f"**Current PRs for {site_id}** — {len(pr_df)} line(s)")

    # Render as HTML table for badge fidelity + "Next →" buttons rendered below.
    cols = ["PR No.", "SAP", "Material", "UOM", "Qty Req", "Qty Pending",
            "Supplier", "Est. SAR", "Workflow"]
    rows_html = []
    for i, (_, r) in enumerate(pr_df.iterrows()):
        bg = _C["surf2"] + "44" if i % 2 else "transparent"
        ws = (r.get("workflow_state") or "submitted").lower()
        est = r.get("Est_Cost_SAR")
        est_txt = f"SAR {float(est):,.0f}" if est and float(est) > 0 else "—"
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            f'<td style="padding:7px 10px;color:{_C["gold"]};font-weight:600;white-space:nowrap;">'
            f'{_esc(r["PR_Number"])}</td>'
            f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
            f'font-size:11.5px;">{_esc(r["SAP_Code"])}</td>'
            f'<td style="padding:7px 10px;color:{_C["text"]};max-width:200px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            f'{_esc(r.get("Material_Name"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};">{_esc(r.get("UOM"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:600;">{_esc(r.get("Requested_Qty"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["low"]};">{_esc(r.get("Pending_Qty"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;">{_esc(r.get("Supplier"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;">{_esc(est_txt)}</td>'
            f'<td style="padding:7px 10px;">{status_pill_html(ws)}</td>'
            f'</tr>'
        )
    st.markdown(_html_table("".join(rows_html), cols), unsafe_allow_html=True)

    # ── 4) NOTIFY LOGISTICS (preserved: Outlook email + PDF download) ───
    st.divider()
    st.markdown("**📧 Notify Logistics (Pending Balance Follow-up)**")
    open_prs_only = pr_df[pr_df["status"] == "open"]["PR_Number"].unique()

    if len(open_prs_only) > 0:
        col_a, col_b, col_c = st.columns([2, 1, 1])
        with col_a:
            pr_to_email = st.selectbox(
                "Select PR for Actions:", open_prs_only, key="email_pr_select",
            )
        with col_b:
            st.write("")
            st.write("")
            if st.button("📧 Draft Outlook Email", type="secondary", width="stretch"):
                pr_data = pr_df[pr_df["PR_Number"] == pr_to_email]
                from mailer import draft_logistics_email_via_outlook
                ok, msg = draft_logistics_email_via_outlook(pr_to_email, site_id, pr_data)
                (st.success if ok else st.error)(msg)
        with col_c:
            st.write("")
            st.write("")
            pr_data = pr_df[pr_df["PR_Number"] == pr_to_email]
            from reports import generate_pr_pdf
            pdf_bytes = generate_pr_pdf(
                pr_to_email, site_id, pr_data, generated_by=user["username"],
            )
            st.download_button(
                label="📥 Download PR PDF",
                data=pdf_bytes,
                file_name=f"PR_{pr_to_email}_{site_id}_Record.pdf",
                mime="application/pdf",
                type="primary",
                width="stretch",
            )
    else:
        st.success("All PRs for your site are currently fulfilled and closed!")


# ===========================================================================
# TAB 6 — RECEIVE MATERIAL  (existing form + new history table below)
# ===========================================================================
def _render_receive_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📥 Receive Material (Incoming Deliveries)</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Log shipments arriving at your site. If linked to a PR, the system '
        f'automatically tracks fulfillment.</p>',
        unsafe_allow_html=True,
    )

    conn = get_connection()
    open_prs = pd.read_sql(
        "SELECT DISTINCT PR_Number FROM pr_master WHERE Site_ID = ? AND status = 'open'",
        conn, params=(site_id,),
    )
    pr_options = ["-- None (Direct Purchase) --"] + open_prs["PR_Number"].tolist()

    selected_pr = st.selectbox(
        "🔗 Link to Open PR (Filters Material List)",
        pr_options, key="hod_pr_select",
    )

    if selected_pr == "-- None (Direct Purchase) --":
        inv_list_db = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn,
        )
    else:
        inv_list_db = pd.read_sql(
            """SELECT i.SAP_Code, i.Equipment_Description, i.UOM
               FROM pr_master p
               JOIN inventory i ON p.SAP_Code = i.SAP_Code
               WHERE p.PR_Number = ? AND p.Site_ID = ?""",
            conn, params=(selected_pr, site_id),
        )

    if not inv_list_db.empty:
        inv_list_db["Search_String"] = (
            "[" + inv_list_db["SAP_Code"].astype(str) + "] "
            + inv_list_db["Equipment_Description"].astype(str)
        )
        material_options = inv_list_db["Search_String"].tolist()
    else:
        material_options = []

    _RECEIPT_SPECIAL = {
        "id", "Timestamp", "Date", "SAP_Code", "Quantity",
        "Site_ID", "Expiry_Date", "PR_Number", "status",
    }
    _rc = conn.cursor()
    _rc.execute("PRAGMA table_info(receipts)")
    receipt_extra_cols = [row[1] for row in _rc.fetchall() if row[1] not in _RECEIPT_SPECIAL]

    with st.form("hod_receive_form", clear_on_submit=True):
        # Build a flat list of (render_fn, key) so columns balance row-by-row.
        # All fields mandatory per 2026-06 spec — no '(Optional)' suffix.
        receipt_extra_vals: dict[str, str] = {}
        fixed_fields = [
            ("material",  "Select Material *",       "selectbox_material"),
            ("qty",       "Quantity Received *",     "number_qty"),
            ("date",      "Delivery Date *",         "date_delivery"),
            ("exp",       "Expiry Date (Optional)",  "date_expiry"),
        ]
        all_fields = fixed_fields + [("extra", f"{c} *", c) for c in receipt_extra_cols]

        # Split half/half so both columns are the same height.
        midpoint = (len(all_fields) + 1) // 2
        left_fields, right_fields = all_fields[:midpoint], all_fields[midpoint:]
        col1, col2 = st.columns(2)
        sel_item = qty = date_val = exp_date = None

        def _render_one(kind, label, key):
            nonlocal sel_item, qty, date_val, exp_date
            if kind == "material":
                sel_item = st.selectbox(label, material_options, index=None, key=key)
            elif kind == "qty":
                qty = st.number_input(label, min_value=0.1, step=1.0, key=key)
            elif kind == "date":
                date_val = st.date_input(label, datetime.date.today(), key=key)
            elif kind == "exp":
                exp_date = st.date_input(label, value=None, key=key)
            else:
                receipt_extra_vals[key] = st.text_input(label, key=f"_recv_{key}")

        with col1:
            for f in left_fields:
                _render_one(*f)
        with col2:
            for f in right_fields:
                _render_one(*f)

        if st.form_submit_button("💾 Save Receipt", type="primary"):
            # All-mandatory check
            missing = []
            if not sel_item:           missing.append("Material")
            if not qty:                missing.append("Quantity")
            if not date_val:           missing.append("Delivery Date")
            # Expiry_Date is OPTIONAL — skip.
            for k, v in receipt_extra_vals.items():
                if not str(v).strip():
                    missing.append(k)
            if missing:
                st.error(f"⚠️ All fields are mandatory. Missing: {', '.join(missing)}")
            else:
                sap_code = sel_item.split("]")[0].replace("[", "").strip()
                pr_val = selected_pr if selected_pr != "-- None (Direct Purchase) --" else None
                exp_val = str(exp_date) if exp_date else None

                ok, msg = process_receipt_delivery(
                    conn, str(date_val), sap_code, qty,
                    supplier=receipt_extra_vals.get("Supplier", ""),
                    remarks=receipt_extra_vals.get("Remarks", ""),
                    site_id=site_id,
                    pr_number=pr_val,
                    expiry_date=exp_val,
                    extra_fields={k: v for k, v in receipt_extra_vals.items()
                                  if k not in {"Supplier", "Remarks"}},
                )
                if ok:
                    log_audit_action(
                        user["username"], "RECEIVE_MATERIAL", "receipts",
                        f"Received qty: {qty} of SAP: {sap_code} at {site_id}",
                    )
                    bust_inventory_cache()
                    st.success(msg)
                else:
                    st.error(msg)
    conn.close()

    # NEW — receipt history table
    st.markdown(
        f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
        f'margin:24px 0 8px 0;">📋 Receipt History (last 50 at {_esc(site_id)})</h4>',
        unsafe_allow_html=True,
    )
    hist_df = get_receipt_history(site_id=site_id, limit=50)
    if hist_df.empty:
        st.caption("No receipts on file for this site yet.")
    else:
        render_aggrid(hist_df, key="_hod_receipt_history", height=320)


# ===========================================================================
# TAB 7 — SHELF-LIFE ALERTS  (3-card strip + action buttons)
# ===========================================================================
def _render_shelflife_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">⚠️ Shelf-Life Alerts</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Lots approaching or past their expiry date. Disposal = audited.</p>',
        unsafe_allow_html=True,
    )

    shelf_df = cached_short_dated_stock(site_id=site_id)
    if shelf_df is None or shelf_df.empty:
        st.success("✅ No expiring or short-dated stock at your site!")
        return

    # Determine days-left bucket from the Status column the view exposes.
    today = pd.Timestamp.today().normalize()
    s = shelf_df.copy()
    if "Expiry_Date" in s.columns:
        s["_exp"] = pd.to_datetime(s["Expiry_Date"], errors="coerce")
        s["_days_left"] = (s["_exp"] - today).dt.days
    else:
        s["_days_left"] = 0

    def _bucket(d):
        if pd.isna(d):
            return "warning"
        d = int(d)
        if d < 0:
            return "expired"
        if d <= 30:
            return "critical"
        if d <= 90:
            return "warning"
        return "ok"

    s["_bucket"] = s["_days_left"].apply(_bucket)

    expired_n  = int((s["_bucket"] == "expired").sum())
    critical_n = int((s["_bucket"] == "critical").sum())
    warning_n  = int((s["_bucket"] == "warning").sum())

    render_hero_metrics([
        {"label": "🔴 Expired", "value": expired_n,
         "tone": "critical" if expired_n else "ok",
         "delta": "isolate + dispose" if expired_n else "shelf clear"},
        {"label": "🟠 Critical (≤30d)", "value": critical_n,
         "tone": "low" if critical_n else "ok",
         "delta": "use immediately" if critical_n else "—"},
        {"label": "🟡 Warning (≤90d)", "value": warning_n,
         "tone": "low" if warning_n else "ok",
         "delta": "plan first-out"},
    ])
    st.write("")

    if expired_n > 0:
        st.markdown(
            f'<div style="background:{_C["crit"]}16;border:1px solid {_C["crit"]}55;'
            f'border-radius:8px;padding:10px 14px;margin-bottom:14px;'
            f'color:{_C["crit"]};font-size:13px;">'
            f'🔴 <strong>ACTION REQUIRED:</strong> {expired_n} expired lot(s) must '
            f'be physically isolated and flagged for disposal. Do not issue.</div>',
            unsafe_allow_html=True,
        )

    # Render table — sorted by days-left ascending (worst first)
    s = s.sort_values("_days_left", ascending=True)
    cols = ["SAP", "Material", "Lot", "Qty", "Expiry", "Days Left", "Status"]
    rows_html = []
    for i, (_, r) in enumerate(s.head(30).iterrows()):
        bg = _C["crit"] + "08" if r["_bucket"] == "expired" else (
            _C["surf2"] + "44" if i % 2 else "transparent"
        )
        bucket = r["_bucket"]
        col = {"expired": _C["crit"], "critical": _C["crit"],
               "warning": _C["low"], "ok": _C["ok"]}[bucket]
        days_disp = (
            f'{abs(int(r["_days_left"]))} days ago'
            if r["_days_left"] < 0 else f'{int(r["_days_left"])} days'
        )
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
            f'font-size:11.5px;">{_esc(r.get("SAP_Code"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["text"]};max-width:200px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            f'{_esc(r.get("Equipment_Description"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};font-family:monospace;'
            f'font-size:11.5px;">{_esc(r.get("Lot_Number") or r.get("PR_Number"))}</td>'
            f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:600;">'
            f'{_esc(r.get("Quantity"))}</td>'
            f'<td style="padding:7px 10px;color:{col};font-size:12px;font-weight:'
            f'{700 if bucket == "expired" else 400};white-space:nowrap;">'
            f'{_esc(r.get("Expiry_Date"))}</td>'
            f'<td style="padding:7px 10px;color:{col};font-weight:700;white-space:nowrap;">'
            f'{days_disp}</td>'
            f'<td style="padding:7px 10px;">{status_pill_html(bucket)}</td>'
            f'</tr>'
        )
    st.markdown(_html_table("".join(rows_html), cols), unsafe_allow_html=True)

    # Action row — bulk disposal log
    if expired_n:
        if st.button(f"🗑️ Log Disposal — {expired_n} Expired Lot(s)",
                     key="_hod_shelf_dispose"):
            log_audit_action(
                user["username"], "LOG_DISPOSAL", "inventory",
                f"Site={site_id} disposed expired lots count={expired_n}",
            )
            st.toast("Disposal logged to audit trail", icon="🗑️")


# ===========================================================================
# TAB 8 — NOTIFICATIONS  (manual WhatsApp + thresholds + log)
# ===========================================================================
def _render_notifications_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🔔 Notifications</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Send manual WhatsApp messages, configure alert thresholds, and review '
        f'the outgoing message log.</p>',
        unsafe_allow_html=True,
    )

    col_send, col_thresh = st.columns(2)

    # Manual send
    with col_send:
        st.markdown(
            f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
            f'margin:0 0 8px 0;">📤 Send Manual WhatsApp</h4>',
            unsafe_allow_html=True,
        )
        recipient = st.text_input(
            "Recipient phone number",
            placeholder="+966 5X XXX XXXX",
            key="_hod_notif_to",
        )
        message = st.text_area(
            "Message",
            placeholder="Type your notification message…",
            key="_hod_notif_msg",
            height=110,
        )
        if st.button("📱 Send WhatsApp", type="primary", key="_hod_notif_send"):
            if not recipient.strip() or not message.strip():
                st.error("Both recipient and message are required.")
            else:
                queue_whatsapp_alert(recipient.strip(), message.strip())
                log_audit_action(
                    user["username"], "MANUAL_WHATSAPP", "whatsapp_queue",
                    f"to={recipient!r} len={len(message)}",
                )
                st.toast(f"📱 Queued for {recipient}", icon="📱")
                st.rerun()

    # Thresholds
    with col_thresh:
        st.markdown(
            f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
            f'margin:0 0 8px 0;">⚙️ Alert Thresholds</h4>',
            unsafe_allow_html=True,
        )
        try:
            low_stock_default = int(get_app_setting("low_stock_days", "5"))
            burn_default      = int(get_app_setting("burn_alert_days", "7"))
            expiry_default    = int(get_app_setting("expiry_warn_days", "30"))
        except (TypeError, ValueError):
            low_stock_default, burn_default, expiry_default = 5, 7, 30

        low_stock = st.slider(
            "Low stock alert (days of supply)",
            min_value=1, max_value=60, value=low_stock_default,
            key="_hod_thresh_lowstock",
        )
        burn_alert = st.slider(
            "Burn-rate warning (days remaining)",
            min_value=1, max_value=60, value=burn_default,
            key="_hod_thresh_burn",
        )
        expiry_warn = st.slider(
            "Expiry warning (days before)",
            min_value=1, max_value=120, value=expiry_default,
            key="_hod_thresh_expiry",
        )
        if st.button("💾 Save Thresholds", key="_hod_thresh_save"):
            set_app_setting("low_stock_days", str(low_stock))
            set_app_setting("burn_alert_days", str(burn_alert))
            set_app_setting("expiry_warn_days", str(expiry_warn))
            log_audit_action(
                user["username"], "UPDATE_THRESHOLDS", "app_settings",
                f"low={low_stock} burn={burn_alert} expiry={expiry_warn}",
            )
            st.toast("✅ Thresholds saved", icon="💾")
    # Notification Log lives in the Admin Portal → WhatsApp Console tab.
    # HODs use this tab only for manual sends + threshold tuning.


# ===========================================================================
# TAB 9 — STOCK ADJUSTMENTS  (HOD approves Store Keeper physical-count diffs)
# ===========================================================================
def _render_adjustments_tab(user: dict, site_id: str) -> None:
    from database import (
        get_pending_stock_adjustments,
        get_stock_adjustment_history,
        approve_stock_adjustment,
        reject_stock_adjustment,
        ADJUSTMENT_REASONS,
    )

    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🧮 Stock Adjustments — Awaiting Approval</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 12px 0;">'
        f'Reconciliation between physical shelf count and system stock. '
        f'Approving posts a synthetic ledger row so live stock equals reality.</p>',
        unsafe_allow_html=True,
    )

    pending = get_pending_stock_adjustments(site_id=site_id)
    short_n = int((pending["variance"] < 0).sum()) if not pending.empty else 0
    surplus_n = int((pending["variance"] > 0).sum()) if not pending.empty else 0

    render_hero_metrics([
        {"label": "⏳ Pending", "value": len(pending),
         "tone": "low" if len(pending) else "ok",
         "delta": "awaiting your review" if len(pending) else "all clear"},
        {"label": "➖ Shortfalls", "value": short_n,
         "tone": "critical" if short_n else "neutral",
         "delta": "stock to reduce"},
        {"label": "➕ Surpluses", "value": surplus_n,
         "tone": "neutral",
         "delta": "stock to add"},
    ])
    st.write("")

    if pending.empty:
        render_empty_state(
            icon="✅",
            title="No adjustments to review",
            hint="When a Store Keeper submits a physical-count difference, it appears here.",
        )
    else:
        # Render table
        cols = ["#", "SAP", "Material", "UOM", "System", "Counted",
                "Variance", "Reason", "By", "When"]
        rows_html = []
        for i, (_, r) in enumerate(pending.iterrows()):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            v = float(r["variance"])
            v_col = _C["crit"] if v < 0 else _C["ok"]
            reason_lbl = ADJUSTMENT_REASONS.get(r["reason_code"], r["reason_code"])
            rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;color:{_C["gold"]};font-family:monospace;'
                f'font-size:11.5px;">#{int(r["id"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
                f'font-size:11.5px;">{_esc(r["SAP_Code"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};max-width:220px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{_esc(r.get("Material_Name"))}">{_esc(r.get("Material_Name"))}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};">{_esc(r.get("UOM"))}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-family:monospace;'
                f'font-size:11.5px;">{float(r["system_qty"]):g}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};font-family:monospace;'
                f'font-size:11.5px;font-weight:600;">{float(r["counted_qty"]):g}</td>'
                f'<td style="padding:7px 10px;color:{v_col};font-weight:700;'
                f'font-family:monospace;">{v:+g}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'white-space:nowrap;">{_esc(reason_lbl)}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'font-family:monospace;">{_esc(r["submitted_by"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11px;'
                f'white-space:nowrap;">{_esc(str(r["submitted_at"])[:16])}</td>'
                f'</tr>'
            )
        st.markdown(_html_table("".join(rows_html), cols), unsafe_allow_html=True)

        # Per-row approve/reject panel
        st.markdown(
            f'<div style="color:{_C["muted"]};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin:14px 0 6px 0;">'
            f'Review · approve or reject each adjustment</div>',
            unsafe_allow_html=True,
        )
        for _, r in pending.iterrows():
            aid = int(r["id"])
            v = float(r["variance"])
            v_col = _C["crit"] if v < 0 else _C["ok"]
            reason_lbl = ADJUSTMENT_REASONS.get(r["reason_code"], r["reason_code"])
            cA, cB, cC = st.columns([8, 1, 1])
            with cA:
                notes_blob = (f' · 📝 {_esc(r["notes"])}'
                              if r.get("notes") else "")
                st.markdown(
                    f'<div style="padding:8px 0;border-bottom:1px solid {_C["border"]}33;">'
                    f'<div style="font-size:12.5px;">'
                    f'<span style="color:{_C["gold"]};font-family:monospace;">'
                    f'[{_esc(r["SAP_Code"])}]</span> '
                    f'<span style="color:{_C["text"]};font-weight:600;">'
                    f'{_esc(r.get("Material_Name"))}</span> · '
                    f'<span style="color:{_C["muted"]};">'
                    f'System {float(r["system_qty"]):g} → Counted '
                    f'<b style="color:{_C["text"]};">{float(r["counted_qty"]):g}</b> '
                    f'{_esc(r.get("UOM"))}</span> · '
                    f'<b style="color:{v_col};font-family:monospace;">{v:+g}</b>'
                    f'</div>'
                    f'<div style="font-size:11px;color:{_C["dim"]};margin-top:2px;">'
                    f'{_esc(reason_lbl)} · by {_esc(r["submitted_by"])}{notes_blob}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with cB:
                if st.button("✓", key=f"_hod_adj_appr_{aid}",
                             use_container_width=True):
                    ok, msg = approve_stock_adjustment(
                        aid, approver=user["username"],
                    )
                    if ok:
                        bust_inventory_cache()
                        st.toast(msg, icon="✅")
                        st.rerun()
                    else:
                        st.error(msg)
            with cC:
                if st.button("✗", key=f"_hod_adj_rej_{aid}",
                             use_container_width=True):
                    ok, msg = reject_stock_adjustment(
                        aid, approver=user["username"],
                        reason=f"Rejected by HOD {user['username']}",
                    )
                    if ok:
                        st.toast(msg, icon="🚫")
                        st.rerun()
                    else:
                        st.error(msg)

    # History at bottom
    st.divider()
    st.markdown(
        f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
        f'margin:6px 0 8px 0;">📋 Recent Adjustments History (last 30)</h4>',
        unsafe_allow_html=True,
    )
    hist = get_stock_adjustment_history(site_id=site_id, limit=30)
    if hist.empty:
        st.caption("No adjustments on file yet for this site.")
    else:
        cols = ["#", "SAP", "Material", "Variance", "Reason",
                "Status", "Submitted By", "Approved By", "Ledger Ref"]
        rows_html = []
        for i, (_, r) in enumerate(hist.iterrows()):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            v = float(r["variance"])
            v_col = _C["crit"] if v < 0 else _C["ok"]
            reason_lbl = ADJUSTMENT_REASONS.get(r["reason_code"], r["reason_code"])
            rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;color:{_C["gold"]};font-family:monospace;'
                f'font-size:11.5px;">#{int(r["id"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
                f'font-size:11.5px;">{_esc(r["SAP_Code"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};max-width:200px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{_esc(r.get("Material_Name"))}</td>'
                f'<td style="padding:7px 10px;color:{v_col};font-weight:700;'
                f'font-family:monospace;">{v:+g}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'white-space:nowrap;">{_esc(reason_lbl)}</td>'
                f'<td style="padding:7px 10px;">{status_pill_html(r["status"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'font-family:monospace;">{_esc(r["submitted_by"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'font-family:monospace;">{_esc(r.get("approved_by"))}</td>'
                f'<td style="padding:7px 10px;color:{_C["dim"]};font-size:11px;'
                f'font-family:monospace;">{_esc(r.get("posted_txn_ref"))}</td>'
                f'</tr>'
            )
        st.markdown(_html_table("".join(rows_html), cols), unsafe_allow_html=True)


# ===========================================================================
# TAB 10 — MY REQUESTS  (preserved working feature, kept at end)
# ===========================================================================
def _render_my_requests_tab(user: dict, site_id: str) -> None:
    st.subheader("My Outbound Requests")
    conn = get_connection()
    reqs_df = get_pending_requests(conn, site_id=site_id)

    if reqs_df.empty:
        render_empty_state(
            icon="📦",
            title="No outbound requests yet",
            hint="Use the Cross-Site Inquiry tab to request material from another branch.",
        )
    else:
        rows_html = []
        for i, (_, r) in enumerate(reqs_df.iterrows()):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;color:{_C["gold"]};font-weight:600;'
                f'font-family:monospace;font-size:11.5px;">#{_esc(r["id"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["purple"]}DD;font-weight:600;'
                f'white-space:nowrap;">{_esc(r["target_site"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["gold"]}CC;font-family:monospace;'
                f'font-size:11.5px;">{_esc(r["SAP_Code"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:700;">'
                f'{_esc(r["requested_qty"])}</td>'
                f'<td style="padding:7px 10px;">{status_pill_html(str(r["status"]).lower())}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'white-space:nowrap;">{_esc(r["created_at"])}</td>'
                f'</tr>'
            )
        st.markdown(
            _html_table("".join(rows_html),
                        ["#", "To Site", "SAP", "Qty", "Status", "Created"]),
            unsafe_allow_html=True,
        )

        approved_df = reqs_df[reqs_df["status"] == "approved"]
        if not approved_df.empty:
            st.write("---")
            st.write("**📦 Mark Incoming Transfers as Received:**")
            req_to_fulfill = st.selectbox("Select Approved Request:", approved_df["id"].tolist())
            if st.button("Confirm Delivery Received", type="primary"):
                update_request_status(
                    conn, req_to_fulfill, "fulfilled",
                    user["username"], "Delivery received at site",
                )
                bust_inventory_cache()
                st.success("Inventory Transfer Complete!")
                st.rerun()
    conn.close()


# ===========================================================================
# SITE CONFIG TAB — per-site Work Type and Tank No management
# ===========================================================================
def _render_site_config_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<p style="color:{TEXT_MUTED};font-size:12.5px;margin:0 0 14px 0;">'
        f'Manage dropdown values that appear in the Entry Log for <b style="color:{BRAND_GOLD};">'
        f'{html.escape(site_id)}</b>. These override the global defaults for your site.</p>',
        unsafe_allow_html=True,
    )

    for category, label, icon in [
        ("Work_Type", "Work Types", "🔧"),
        ("Tank_No",   "Tank Numbers", "🛢️"),
    ]:
        st.markdown(
            f'<div style="color:{TEXT_MUTED};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin:16px 0 8px 0;">'
            f'{icon} {label}</div>',
            unsafe_allow_html=True,
        )
        # Load site-specific values only (not global fallback) for management
        conn_cfg = get_connection()
        try:
            import pandas as _pd
            site_vals = _pd.read_sql(
                "SELECT rowid, value FROM system_settings WHERE category=? AND Site_ID=? ORDER BY value",
                conn_cfg, params=(category, site_id)
            )
        finally:
            conn_cfg.close()

        if site_vals.empty:
            global_vals = (
                cached_work_types() if category == "Work_Type" else cached_tank_nos()
            )
            st.caption(
                f"No site-specific values set — entry form uses global defaults: "
                f"{', '.join(global_vals) or '(none)'}. Add values below to override."
            )
        else:
            for _, row in site_vals.iterrows():
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.markdown(
                        f'<div style="padding:6px 10px;background:rgba(26,40,56,0.6);'
                        f'border:1px solid rgba(42,64,96,0.5);border-radius:6px;'
                        f'color:#F0F4F8;font-size:13px;">{html.escape(str(row["value"]))}</div>',
                        unsafe_allow_html=True,
                    )
                with c2:
                    if st.button("🗑️", key=f"_del_{category}_{row['rowid']}",
                                 use_container_width=True, help="Delete this value"):
                        ok, msg = delete_site_dropdown_value(category, row["value"], site_id=site_id)
                        if ok:
                            bust_settings_cache()
                            log_audit_action(user["username"], f"DELETE_{category}",
                                             "system_settings",
                                             f"site={site_id} value={row['value']!r}")
                            st.toast(msg, icon="🗑️")
                            st.rerun()
                        else:
                            st.error(msg)

        with st.form(key=f"_add_{category}_form"):
            new_val = st.text_input(
                f"Add new {label[:-1]}",
                placeholder=f"e.g. {'Maintenance' if category == 'Work_Type' else 'Tank 4'}",
                key=f"_new_{category}_input",
            )
            if st.form_submit_button(f"➕ Add {label[:-1]}", type="primary"):
                if not new_val.strip():
                    st.error("Please enter a value.")
                else:
                    ok, msg = add_site_dropdown_value(category, new_val, site_id=site_id)
                    if ok:
                        bust_settings_cache()
                        log_audit_action(user["username"], f"ADD_{category}",
                                         "system_settings",
                                         f"site={site_id} value={new_val!r}")
                        st.toast(msg, icon="✅")
                        st.rerun()
                    else:
                        st.error(msg)


# ===========================================================================
# TAB — RETURNS: review SK-staged returns, approve → ledger row + email
# ===========================================================================
def _render_returns_tab(user: dict, site_id: str) -> None:
    from database import (
        get_pending_returns, approve_return_request, reject_return_request,
    )

    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">↩️ Pending Returns — {_esc(site_id)}</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 12px 0;">'
        f'Returns staged by Store Keepers. Approving writes to the <code>returns</code> '
        f'ledger (Closing_Stock recomputes) and opens a logistics email draft.</p>',
        unsafe_allow_html=True,
    )

    pending = get_pending_returns(site_id=site_id)
    if pending is None or pending.empty:
        st.success("✅ No pending returns.")
        return

    # Stat strip
    n_total = len(pending)
    n_override = int((pending["override_required"] == 1).sum())
    render_hero_metrics([
        {"label": "Pending returns", "value": n_total, "tone": "low" if n_total else "ok"},
        {"label": "Beyond 30-day window", "value": n_override,
         "tone": "critical" if n_override else "neutral",
         "delta": "needs override" if n_override else "—"},
    ])
    st.write("")

    for _, r in pending.iterrows():
        rid = int(r["id"])
        is_override = int(r.get("override_required") or 0) == 1
        border_color = "#EF4444" if is_override else "#2A4060"
        st.markdown(
            f'<div style="border:1px solid {border_color}66;'
            f'border-radius:8px;padding:10px 14px;margin:6px 0;'
            f'background:rgba(30,48,80,0.18);">'
            f'<div style="color:{_C["text"]};font-weight:600;font-size:13.5px;">'
            f'<span style="color:{_C["gold"]};font-family:monospace;">'
            f'[{_esc(r["SAP_Code"])}]</span>'
            f' · Mat: {_esc(r.get("Material_Code") or "—")}'
            f' · {_esc(r.get("Equipment_Description"))}'
            f'</div>'
            f'<div style="color:{_C["muted"]};font-size:12px;margin-top:3px;">'
            f'Qty: <b style="color:{_C["text"]};">{_esc(r["Quantity"])}</b>'
            f' / Received: {_esc(r.get("received_qty"))}'
            f'  ·  Reason: {_esc(r["Return_Reason"])}'
            f'  ·  Return DN: {_esc(r["Return_DN_No"])}'
            f'  ·  Original DN: {_esc(r.get("received_dn_no") or "—")}'
            f' ({_esc(r.get("received_date") or "—")})'
            f'  ·  by {_esc(r["submitted_by"])} on {_esc(r["submitted_at"])}'
            f'</div>'
            + (f'<div style="color:#FCA5A5;font-size:12px;margin-top:4px;">'
               f'⚠️ <b>Override requested.</b> Reason: {_esc(r.get("override_reason") or "—")}'
               f'</div>' if is_override else "")
            + '</div>',
            unsafe_allow_html=True,
        )
        cA, cB, _spc = st.columns([1, 1, 5])
        with cA:
            if st.button("✓ Approve", key=f"_ret_appr_{rid}", type="primary",
                         use_container_width=True):
                ok, msg = approve_return_request(rid, approver=user["username"])
                if not ok:
                    st.error(msg)
                else:
                    # Open the logistics email draft
                    from mailer import draft_return_logistics_email
                    row_dict = {k: r.get(k) for k in r.index}
                    sent_ok, sent_msg = draft_return_logistics_email(site_id, row_dict)
                    bust_inventory_cache()
                    st.toast("Approved · stock updated", icon="✅")
                    if sent_ok:
                        st.success(f"Approved #{rid}. {sent_msg}")
                    else:
                        st.warning(f"Approved #{rid}, but email draft failed: {sent_msg}")
                    st.rerun()
        with cB:
            if st.button("✗ Reject", key=f"_ret_rej_{rid}",
                         use_container_width=True):
                reject_return_request(rid, approver=user["username"],
                                      reason="Rejected by HOD")
                st.toast(f"Rejected #{rid}", icon="🚫")
                st.rerun()


# ===========================================================================
# TAB 12 — DOC: attachment browser (Consumption / Receipt / Return sub-tabs)
# ===========================================================================
def _render_doc_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📎 Document Library — {_esc(site_id)}</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Browse and download files attached by Store Keepers during '
        f'Consumption / Receipt / Returnable submissions.</p>',
        unsafe_allow_html=True,
    )

    sub_consumption, sub_receipt, sub_return = st.tabs([
        "📋 Consumption", "📥 Receipt", "↩️ Return",
    ])
    for sub_widget, doc_type, label in [
        (sub_consumption, "consumption", "Consumption"),
        (sub_receipt,     "receipt",     "Receipt"),
        (sub_return,      "return",      "Return"),
    ]:
        with sub_widget:
            _render_doc_subtab(site_id, doc_type, label)


def _render_doc_subtab(site_id: str, doc_type: str, label: str) -> None:
    today = datetime.date.today()
    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        df_from = st.date_input(
            f"From", value=today - datetime.timedelta(days=30),
            key=f"_doc_{doc_type}_from",
        )
    with f2:
        df_to = st.date_input(
            f"To", value=today, key=f"_doc_{doc_type}_to",
        )
    with f3:
        doc_filter = st.text_input(
            "Filter by Doc Number (optional)",
            placeholder="DN_No / DDMMYY / Return DN…",
            key=f"_doc_{doc_type}_num",
        )

    conn = get_connection()
    try:
        q = (
            "SELECT id, doc_number, file_name, mime_type, file_size, "
            "       entry_date, uploaded_by, uploaded_at, entry_table, entry_id "
            "FROM entry_attachments "
            "WHERE Site_ID = ? AND doc_type = ? "
            "AND COALESCE(entry_date, DATE(uploaded_at)) BETWEEN ? AND ?"
        )
        params: list = [site_id, doc_type, df_from.isoformat(), df_to.isoformat()]
        if doc_filter.strip():
            q += " AND doc_number LIKE ?"
            params.append(f"%{doc_filter.strip()}%")
        q += " ORDER BY uploaded_at DESC"
        rows_df = pd.read_sql(q, conn, params=tuple(params))
    finally:
        conn.close()

    if rows_df.empty:
        st.caption(f"No {label.lower()} attachments in this window.")
        return

    st.caption(f"{len(rows_df)} file(s).")
    for _, r in rows_df.iterrows():
        c1, c2, c3 = st.columns([5, 2, 1])
        with c1:
            st.markdown(
                f"**{_esc(r['file_name'])}** &nbsp; "
                f"<span style='color:{_C['muted']};font-size:12px;'>"
                f"Doc #{_esc(r['doc_number'])} · "
                f"{int(r['file_size'] or 0):,} bytes · "
                f"by {_esc(r['uploaded_by'])} on {_esc(r['uploaded_at'])}"
                f"</span>",
                unsafe_allow_html=True,
            )
        with c2:
            st.caption(f"entry: {r['entry_table']} #{r['entry_id']}")
        with c3:
            conn2 = get_connection()
            try:
                blob_row = conn2.execute(
                    "SELECT file_blob, mime_type FROM entry_attachments WHERE id = ?",
                    (int(r["id"]),),
                ).fetchone()
            finally:
                conn2.close()
            if blob_row and blob_row[0]:
                st.download_button(
                    "⬇️", data=blob_row[0],
                    file_name=str(r["file_name"]),
                    mime=blob_row[1] or "application/octet-stream",
                    key=f"_doc_dl_{doc_type}_{r['id']}",
                )


# ===========================================================================
# TAB 13 — QR APPROVAL (SK submits → HOD approves → HOD downloads)
# ===========================================================================
def _render_qr_approval_tab(user: dict, site_id: str) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🏷️ QR Label Approval — {_esc(site_id)}</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Review and approve QR-label print requests submitted by Store Keepers. '
        f'Approved batches become downloadable as PDF here.</p>',
        unsafe_allow_html=True,
    )
    from database import (
        list_qr_requests, approve_qr_request, reject_qr_request,
    )
    sub_pending, sub_approved = st.tabs(["⏳ Pending", "✅ Approved"])
    with sub_pending:
        df_p = list_qr_requests(site_id=site_id, status="pending")
        if df_p.empty:
            st.caption("No pending QR requests.")
        else:
            # Multi-select bulk approve / reject.
            df_view = df_p[[
                "id", "SAP_Code", "Material_Code", "Equipment_Description",
                "Quantity", "requested_by", "requested_at",
            ]].copy()
            df_view.insert(0, "✓ Select", False)
            edited = st.data_editor(
                df_view,
                column_config={
                    "✓ Select": st.column_config.CheckboxColumn("✓ Select", default=False),
                    "id": st.column_config.NumberColumn("ID", disabled=True),
                    "SAP_Code": st.column_config.TextColumn("SAP", disabled=True),
                    "Material_Code": st.column_config.TextColumn("Mat Code", disabled=True),
                    "Equipment_Description": st.column_config.TextColumn("Material", disabled=True),
                    "Quantity": st.column_config.NumberColumn("Qty", disabled=True),
                    "requested_by": st.column_config.TextColumn("By", disabled=True),
                    "requested_at": st.column_config.TextColumn("At", disabled=True),
                },
                hide_index=True, use_container_width=True, key="_qr_pending_editor",
            )
            picked_ids = [int(x) for x in edited.loc[edited["✓ Select"], "id"].tolist()]
            ba1, ba2, ba3 = st.columns([1, 1, 4])
            with ba1:
                if st.button(f"✓ Approve Selected ({len(picked_ids)})",
                             type="primary", disabled=not picked_ids,
                             key="_qr_bulk_appr"):
                    for rid in picked_ids:
                        approve_qr_request(rid, approver=user["username"])
                    st.toast(f"Approved {len(picked_ids)} request(s)", icon="✅")
                    st.rerun()
            with ba2:
                if st.button(f"✗ Reject Selected ({len(picked_ids)})",
                             disabled=not picked_ids, key="_qr_bulk_rej"):
                    for rid in picked_ids:
                        reject_qr_request(rid, approver=user["username"],
                                          reason="Rejected by HOD")
                    st.toast(f"Rejected {len(picked_ids)} request(s)", icon="🚫")
                    st.rerun()
    with sub_approved:
        df_a = list_qr_requests(site_id=site_id, status="approved")
        if df_a.empty:
            st.caption("No approved QR requests waiting for download.")
        else:
            st.dataframe(
                df_a[["id", "SAP_Code", "Material_Code", "Equipment_Description",
                      "Quantity", "approved_by", "approved_at"]],
                use_container_width=True, hide_index=True,
            )
            if st.button("📥 Download QR Labels PDF for ALL approved", type="primary"):
                try:
                    from reports import generate_qr_labels_pdf
                    items: list[dict] = []
                    for _, r in df_a.iterrows():
                        for _ in range(int(r["Quantity"] or 1)):
                            items.append({
                                "SAP_Code": r["SAP_Code"],
                                "Equipment_Description": r["Equipment_Description"],
                            })
                    pdf_bytes = generate_qr_labels_pdf(items)
                    st.download_button(
                        "Download PDF",
                        data=pdf_bytes,
                        file_name=f"GI_QR_Labels_{site_id}_{datetime.date.today()}.pdf",
                        mime="application/pdf",
                        type="primary",
                    )
                except ImportError as e:
                    st.error(str(e))


# ===========================================================================
# PAGE  — top-level routing
# ===========================================================================
def page_hod_portal(user: dict) -> None:
    render_brand_header_hod("HOD Management Portal")
    site_id = user.get("site_id", "HQ")
    st.markdown(
        f'<h1 style="color:{TEXT_PRIMARY};font-size:21px;font-weight:700;'
        f'letter-spacing:-0.02em;margin:0 0 4px 0;">📋 HOD Portal</h1>'
        f'<p style="color:{TEXT_MUTED};font-size:12.5px;margin:0 0 14px 0;">'
        f'Managing Site: <b style="color:{BRAND_GOLD};">{html.escape(str(site_id))}</b></p>',
        unsafe_allow_html=True,
    )

    # Site-scoped hero strip (preserved)
    try:
        _low_site    = cached_low_stock_items(site_id=site_id)
        _expiry_site = cached_short_dated_stock(site_id=site_id)
        _low_n       = 0 if _low_site is None or _low_site.empty else len(_low_site)
        _expiry_n    = 0 if _expiry_site is None or _expiry_site.empty else len(_expiry_site)
        _pr_conn = get_connection()
        try:
            _pending_rcpt_n = _pr_conn.execute(
                "SELECT COUNT(*) FROM pending_receipts "
                "WHERE COALESCE(status,'pending_hod')='pending_hod' "
                "AND COALESCE(Site_ID,'HQ')=?",
                (site_id,),
            ).fetchone()[0] or 0
        except Exception:
            _pending_rcpt_n = 0
        finally:
            _pr_conn.close()
        # Site-scoped inventory valuation (SAR, standard cost)
        try:
            from cache_layer import cached_total_inventory_value, cached_consumption_value
            from database import format_sar
            _site_value     = cached_total_inventory_value(site_id=site_id)
            _site_burn_30d  = cached_consumption_value(site_id=site_id, days=30)
        except Exception:
            _site_value, _site_burn_30d = 0.0, 0.0
            format_sar = lambda v: f"SAR {v:,.0f}"

        render_hero_metrics([
            {"label": "Site stock value", "value": format_sar(_site_value),
             "tone": "neutral",
             "delta": f"{format_sar(_site_burn_30d)} consumed (30d)"
                      if _site_burn_30d > 0 else "standard cost · SAR"},
            {"label": "Below minimum (site)", "value": _low_n,
             "tone": "ok" if _low_n == 0 else ("low" if _low_n < 10 else "critical"),
             "delta": "healthy" if _low_n == 0 else "review Burn Rate"},
            {"label": "Expiring / expired", "value": _expiry_n,
             "tone": "ok" if _expiry_n == 0 else ("low" if _expiry_n < 5 else "critical"),
             "delta": "shelf-life clear" if _expiry_n == 0 else "Shelf-Life tab"},
            {"label": "Pending receipts to approve", "value": _pending_rcpt_n,
             "tone": "neutral" if _pending_rcpt_n == 0 else "low",
             "delta": "queue clear" if _pending_rcpt_n == 0 else "Pending Receipts tab"},
        ])
    except Exception:
        pass  # decorative — never block portal load

    st.write("")

    # Design-matching tab ORDER:
    # EOD → Cross-Site → Burn Rate → Pending Receipts →
    # Purchase Requests → Receive Material → Shelf-Life → Notifications → My Requests
    # NOTE (2026-06): "📥 Receive Material" moved to Store Keeper's Receipt
    # Staging tab. HOD now only reviews / approves in "📬 Pending Receipts".
    tab_labels = [
        "📤 EOD Commit", "🌐 Cross-Site", "📈 Burn Rate",
        "📬 Pending Receipts", "↩️ Returns", "🧮 Adjustments",
        "📋 Purchase Requests",
        "⚠️ Shelf-Life", "🔔 Notifications",
        "✅ My Requests", "⚙️ Site Config", "📎 DOC", "🏷️ QR Approval",
    ]
    tabs = st.tabs(tab_labels)
    with tabs[0]: _render_eod_tab(user, site_id)
    with tabs[1]: _render_crosssite_tab(user, site_id)
    with tabs[2]: _render_burn_rate_tab(site_id)
    with tabs[3]: _render_pending_receipts_tab(user, site_id)
    with tabs[4]: _render_returns_tab(user, site_id)
    with tabs[5]: _render_adjustments_tab(user, site_id)
    with tabs[6]: _render_pr_tab(user, site_id)
    with tabs[7]: _render_shelflife_tab(user, site_id)
    with tabs[8]: _render_notifications_tab(user, site_id)
    with tabs[9]: _render_my_requests_tab(user, site_id)
    with tabs[10]: _render_site_config_tab(user, site_id)
    with tabs[11]: _render_doc_tab(user, site_id)
    with tabs[12]: _render_qr_approval_tab(user, site_id)
