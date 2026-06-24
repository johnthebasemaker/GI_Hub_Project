"""
pages_internal/daily_issue_log.py — Entry Log (Consumption / Receipt / Returnables)
=====================================================================================
Extracted from main.py during Phase 2 structure refactor.
SQL strings, math, and form logic are unchanged.
"""

import datetime
import html

import pandas as pd
import streamlit as st

from config import (
    SYSTEM_COLS, OPTIONAL_ISSUE_COLS, BRAND_GOLD, TEXT_MUTED, HIDDEN_FORM_COLS,
)
from database import (
    get_connection,
    queue_whatsapp_alert,
    insert_returnable_item,
    mark_item_returned,
    get_returnable_items,
    get_user_last_entry_defaults,
    pwa_stage_pending_issues,
    stage_pending_receipts_bulk,
    # Phase 7B — Supervisor Material Request workflow
    list_supervisor_requests,
    get_supervisor_request,
    update_supervisor_request_item,
    delete_supervisor_request_item,
    approve_supervisor_request,
    reject_supervisor_request,
    get_open_returnables_for_employee,
)
from cache_layer import (
    cached_work_types,
    cached_tank_nos,
    cached_short_dated_stock,
    cached_item_snapshot,
    cached_fefo_lots,
    bust_inventory_cache,
)
from ui_components import (
    render_brand_header,
    render_barcode_scanner,
    render_item_snapshot,
    render_fefo_panel,
    render_empty_state,
    render_ocr_review_grid,
    render_stock_badge,
    render_aggrid,
    LOAN_STATUS_BADGE_JS,
    # Phase 7E — form draft recovery
    render_form_recovery_banner,
    auto_save_form_draft,
    render_manual_save_draft_button,
    clear_form_draft,
)

# Phase 7E — state keys per form. File-uploader widget keys are intentionally
# EXCLUDED (UploadedFile objects can't be JSON-serialised). The staging queue
# itself lives in pending_issues / pending_receipts so it's already durable.
# These keys cover the in-flight row a user might be building when the
# network drops — restoring them lets them pick up exactly where they left off.
_SK_CONSUMPTION_STATE_KEYS = [
    "item_selectbox", "tank_no_select", "wbs_consumption_select",
    "override_expiry_ck", "cons_attach_scope",
]
_SK_RECEIPT_STATE_KEYS = [
    "rcpt_pr_link", "rcpt_item_selectbox", "rcpt_mtc_number",
    "rcpt_attach_scope", "rcpt_attach_dn",
]

# Phase 5 — OCR upload pipeline. Lazy-imported inside the helper functions
# below so the ai module's Ollama probe doesn't fire when the page renders
# without anyone using the OCR features.


def page_daily_issue_log(user: dict) -> None:
    render_brand_header("Entry Log")
    st.title("📝 Entry Log")

    site_id    = user.get("site_id", "HQ")
    work_types = cached_work_types(site_id=site_id)
    tank_nos   = cached_tank_nos(site_id=site_id)

    # Shared inventory list used by the Issue and Receipt tabs
    conn = get_connection()
    try:
        inv_list = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, Material_Code, UOM, "
            "COALESCE(Category,'Others') AS Category FROM inventory", conn
        )
        inv_list["Search_String"] = (
            "[" + inv_list["SAP_Code"].astype(str) + "] "
            + inv_list["Equipment_Description"].astype(str)
        )
        search_options = inv_list["Search_String"].tolist()
    except Exception:
        search_options = []
        inv_list = pd.DataFrame()

    c = conn.cursor()
    c.execute("PRAGMA table_info(pending_issues)")
    # Round 12: HIDDEN_FORM_COLS hides Technician, Issued_By, Source_Ref,
    # Lot_Number, FEFO_Override, Requested_By, "Approved By", Approved_By —
    # they are auto-filled server-side or set by dedicated UI affordances.
    form_cols = [
        row[1] for row in c.fetchall()
        if row[1] not in (SYSTEM_COLS | {"SAP_Code"} | HIDDEN_FORM_COLS)
    ]
    conn.close()

    (tab_issue, tab_receipt_stage, tab_return_items,
     tab_returnables, tab_adjust, tab_qr, tab_smr) = st.tabs([
        "📋 Consumption Log", "📦 Receipt Staging", "↩️ Return Items",
        "🔄 Returnable Items", "🧮 Stock Count",
        "🏷️ QR Label Request",
        "🛒 Supervisor Requests",   # Phase 7B
    ])

    # ── TAB 1: Daily Issue Log ─────────────────────────────────────────────────
    with tab_issue:
        # Phase 7E — restore in-flight row selections (Tank/WBS/material/etc.)
        # if a draft exists in either localStorage or the server-side table.
        render_form_recovery_banner(
            "sk_consumption", user.get("username", "sk"),
            site_id, _SK_CONSUMPTION_STATE_KEYS,
        )

        # Phase 5 — bulk OCR upload (handwritten consumption list)
        with st.expander("📷 Upload Handwritten Consumption List (OCR)", expanded=False):
            _render_consumption_ocr(user=user, site_id=site_id, inv_list=inv_list, work_types=work_types)

        # Phase 2.2 UI polish — barcode scanner promoted from a nested expander
        # to a sibling expander, removing one level of nesting so users on the
        # warehouse floor don't have to drill 3 deep to scan a code.
        with st.expander("📷 Barcode / QR Scanner (mobile camera)", expanded=False):
            scanned = render_barcode_scanner(input_key="issue_barcode")
            # Auto-select the matching item in the form below — same pattern as
            # the recent-pills buttons.  The old preselect_idx approach is silently
            # ignored by Streamlit once the selectbox key has any prior session
            # state, so we must write to st.session_state["item_selectbox"] directly.
            if scanned and not inv_list.empty:
                _scan_hits = [s for s in search_options if scanned.strip() in s]
                if _scan_hits:
                    _target = _scan_hits[0]
                    if st.session_state.get("item_selectbox") != _target:
                        st.session_state["item_selectbox"] = _target
                        # Only clear the non-widget backing state; Streamlit forbids
                        # writing to a widget key ("issue_barcode_manual") after the
                        # widget has already been instantiated this run.
                        st.session_state["issue_barcode"] = ""
                        st.rerun()
                else:
                    st.warning(
                        f"⚠️ No inventory item matches **{scanned}**. "
                        "Check the SAP code or use the search box below."
                    )

        with st.expander("➕ Scan / Add New Item to Queue", expanded=True):
            # ── Phase 4: Recently-scanned ring buffer ────────────────────
            # Quick-tap pills for the last 5 items this user touched.
            # Saves keystrokes when issuing similar items in a row.
            RECENT_KEY = "recent_scans_issue"
            recents = st.session_state.get(RECENT_KEY, [])
            if recents and not inv_list.empty:
                st.caption("⏱️ Recent:")
                rcols = st.columns(min(5, len(recents)))
                for rc, rsap in zip(rcols, recents[:5]):
                    with rc:
                        # Show SAP + a short description for context.
                        desc_match = inv_list[inv_list["SAP_Code"] == rsap]
                        label = rsap
                        if not desc_match.empty:
                            d = str(desc_match.iloc[0]["Equipment_Description"])
                            label = f"{rsap} · {d[:18]}" + ("…" if len(d) > 18 else "")
                        if st.button(label, key=f"recent_{rsap}", use_container_width=True):
                            # Set the selectbox to this item via session_state,
                            # then rerun so the box picks the new value up.
                            idx_list = inv_list.index[inv_list["SAP_Code"] == rsap].tolist()
                            if idx_list:
                                st.session_state["item_selectbox"] = search_options[idx_list[0]]
                                st.rerun()

            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
                '1. Select Material</div>',
                unsafe_allow_html=True,
            )
            selected_item = st.selectbox(
                "Search by SAP Code or Description",
                options=search_options,
                index=None,
                placeholder="Start typing… e.g. 'Tank' or '1001'",
                key="item_selectbox",
            )

            sap_code = None
            if selected_item:
                sap_code = selected_item.split("]")[0].replace("[", "").strip()

                # Push to the ring buffer — front of list, deduped, capped at 5.
                _rb = st.session_state.get(RECENT_KEY, [])
                _rb = [sap_code] + [c for c in _rb if c != sap_code]
                st.session_state[RECENT_KEY] = _rb[:5]

                # Scan-to-Inspect snapshot — auto-by-role:
                # admins see global totals; everyone else is scoped to their site.
                snap_site = None if user.get("role") == "admin" else site_id
                snap = cached_item_snapshot(sap_code=sap_code, site_id=snap_site)
                render_item_snapshot(snap)

                # FEFO suggestion — which lot to pull from first. Always site-
                # scoped for floor users (lots are physically at a site), even
                # for admin who otherwise sees global totals.
                fefo_df = cached_fefo_lots(sap_code=sap_code, site_id=site_id)
                render_fefo_panel(fefo_df)

                # ── FEFO OVERRIDE (audit-trailed exception path) ──────────
                # When 2+ open lots exist and the user MUST pull from a
                # non-FEFO bin (physical access, damaged bin, etc.), they
                # can override here. The reason is mandatory and lands on
                # the consumption ledger row + audit log.
                _override_key  = f"_fefo_override_lot__{sap_code}"
                _override_why  = f"_fefo_override_why__{sap_code}"
                _open_lots_df = (
                    fefo_df[fefo_df.get("Remaining_Qty", 0) > 0]
                    if fefo_df is not None and not fefo_df.empty
                    and "Lot_Number" in fefo_df.columns else None
                )
                if _open_lots_df is not None and len(_open_lots_df) >= 2:
                    fefo_top_lot = str(_open_lots_df.iloc[0]["Lot_Number"])
                    with st.expander(
                        "🔄 Pull from a different lot (FEFO override)",
                        expanded=False,
                    ):
                        st.caption(
                            "Use this **only** if you physically cannot pull "
                            "from the FEFO-suggested lot. Your reason will be "
                            "recorded on the consumption record and shared "
                            "with the HOD."
                        )
                        # Show other lots, FEFO suggestion as a disabled reference
                        other_lots = _open_lots_df[
                            _open_lots_df["Lot_Number"] != fefo_top_lot
                        ]
                        if other_lots.empty:
                            st.caption("No other open lots available.")
                        else:
                            lot_options = [
                                f"{r['Lot_Number']}  ·  Exp {r.get('Expiry_Date') or 'no expiry'}  "
                                f"·  Remaining {float(r.get('Remaining_Qty') or 0):g}"
                                for _, r in other_lots.iterrows()
                            ]
                            chosen_label = st.selectbox(
                                "Lot to pull from instead",
                                ["— Keep FEFO suggestion —"] + lot_options,
                                key=f"_fefo_lot_choice__{sap_code}",
                            )
                            if chosen_label != "— Keep FEFO suggestion —":
                                chosen_lot = chosen_label.split("  ·")[0].strip()
                                why = st.text_input(
                                    "Reason for override (required, min 5 chars)",
                                    key=_override_why,
                                    placeholder="e.g. FEFO bin blocked by pallet, expected to clear EOD",
                                    max_chars=200,
                                )
                                if len(why.strip()) >= 5:
                                    st.session_state[_override_key] = chosen_lot
                                    st.markdown(
                                        f"<div style='color:#F59E0B;font-size:0.85rem;'>"
                                        f"⚠️ FEFO override active: pulling from "
                                        f"<b>{chosen_lot}</b> instead of "
                                        f"<b>{fefo_top_lot}</b>.</div>",
                                        unsafe_allow_html=True,
                                    )
                                else:
                                    st.session_state.pop(_override_key, None)
                                    st.caption(
                                        "Type at least 5 characters of reason "
                                        "to activate the override."
                                    )
                            else:
                                st.session_state.pop(_override_key, None)

            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;'
                'margin-top:10px;margin-bottom:6px;">'
                '2. Fill Entry Details</div>',
                unsafe_allow_html=True,
            )
            # Phase 4 — smart defaults from this user's most recent entry.
            # Pulls Issued_By / Issued_To / Tank_No / Work_Type / PR_Number from
            # their last commit (or last draft) at this site. Read-only and
            # silent-fail; the form still works if the lookup returns nothing.
            _defaults = get_user_last_entry_defaults(
                username=user.get("username", ""),
                site_id=site_id,
            )
            # Phase 5 — site-scoped stock for the qty-adjacent badge AND
            # the over-issue guard at submit. Always scoped to the user's
            # site (admins included) because consumption is physical: you
            # can only issue what is at THIS site, regardless of role.
            issue_site_snap = None
            if sap_code:
                issue_site_snap = cached_item_snapshot(sap_code=sap_code, site_id=site_id)
            input_data = {}
            n_cols = 2 if len(form_cols) <= 4 else 3
            cols = st.columns(n_cols)
            for i, col_name in enumerate(form_cols):
                with cols[i % n_cols]:
                    if col_name == "Date":
                        input_data[col_name] = st.date_input(f"{col_name}*", datetime.date.today())
                    elif "qty" in col_name.lower() or "quantity" in col_name.lower():
                        # Highlighted, non-editable site-stock badge above the qty field.
                        render_stock_badge(issue_site_snap or {}, site_id=site_id)
                        input_data[col_name] = st.number_input(f"{col_name}*", min_value=0.1, step=1.0)
                    elif col_name == "Work_Type":
                        _wt_default = _defaults.get("Work_Type", "")
                        _wt_idx = work_types.index(_wt_default) if _wt_default in work_types else 0
                        input_data[col_name] = st.selectbox(
                            f"{col_name}*", work_types, index=_wt_idx,
                        )
                    elif col_name == "Tank_No" and tank_nos:
                        _tn_default = _defaults.get("Tank_No", "")
                        _tn_idx = tank_nos.index(_tn_default) if _tn_default in tank_nos else 0
                        input_data[col_name] = st.selectbox(
                            "Tank_No*", tank_nos, index=_tn_idx,
                            key="tank_no_select",
                        )
                    elif col_name == "wbs":
                        from database import get_wbs_for_site
                        _wbs_df = get_wbs_for_site(site_id)
                        _wbs_opts = _wbs_df["WBS_Number"].tolist() if not _wbs_df.empty else []
                        if not _wbs_opts:
                            st.warning("⚠️ No active WBS for this site. Ask HOD to add WBS numbers in Site Config.")
                            input_data[col_name] = st.text_input(
                                "WBS Number*", value=_defaults.get("wbs", ""),
                            )
                        else:
                            _wbs_default = _defaults.get("wbs", "")
                            _wbs_idx = (_wbs_opts.index(_wbs_default)
                                        if _wbs_default in _wbs_opts else 0)
                            input_data[col_name] = st.selectbox(
                                "WBS Number*", _wbs_opts, index=_wbs_idx,
                                key="wbs_consumption_select",
                            )
                    else:
                        # Required text fields also pre-fill from history where useful.
                        input_data[col_name] = st.text_input(
                            f"{col_name}*",
                            value=_defaults.get(col_name, ""),
                        )

            st.markdown(
                '<div style="background:rgba(245,158,11,0.06);'
                'border:1px solid rgba(245,158,11,0.22);border-left:3px solid #F59E0B;'
                'border-radius:7px;padding:8px 12px;margin:10px 0 4px 0;">'
                '<span style="color:#F59E0B;font-size:12.5px;font-weight:600;">'
                '⚠️ Override Expiry Warning</span>'
                '<span style="color:#7A8FA0;font-size:12px;"> — check below once you\'ve '
                'physically pulled the expiring batch first.</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            override_expiry = st.checkbox(
                "I confirm the expiring batch has been pulled first",
                key="override_expiry_ck",
            )
            if st.button("Add to Grid ⬇️", type="primary"):
                missing_fields = [
                    col for col, val in input_data.items()
                    if col not in OPTIONAL_ISSUE_COLS
                    and (val is None or str(val).strip() == "")
                ]
                if not sap_code:
                    st.error("⚠️ Please select a material from the search box before submitting.")
                    st.stop()
                if missing_fields:
                    st.error(f"⚠️ The following required fields are empty: **{', '.join(missing_fields)}**. Every field marked * is mandatory.")
                    st.stop()
                # Phase 5 — over-issue guard: block consumption when the
                # requested qty exceeds the SITE's current stock. Uses the
                # already-fetched site snapshot to avoid a second query.
                if issue_site_snap and issue_site_snap.get("found"):
                    _qty_val = next(
                        (v for k, v in input_data.items()
                         if "qty" in k.lower() or "quantity" in k.lower()),
                        None,
                    )
                    if _qty_val is not None:
                        _avail = float(issue_site_snap.get("current_stock") or 0.0)
                        if float(_qty_val) > _avail:
                            _uom = issue_site_snap.get("uom", "") or ""
                            st.error(
                                f"🛑 **Insufficient stock at {site_id}.**\n\n"
                                f"Requested: **{float(_qty_val):g} {_uom}**\n"
                                f"Available: **{_avail:g} {_uom}**\n\n"
                                "Reduce the quantity or receive more stock before issuing."
                            )
                            st.stop()
                if True:
                    conn2 = get_connection()
                    input_data["Site_ID"] = site_id
                    input_data["status"]  = "draft"

                    # Attach Lot_Number: respect a deliberate override if
                    # the user set one in the FEFO expander, else fall back
                    # to the system's FEFO suggestion. Best-effort — a
                    # missing FEFO chain just leaves Lot_Number unset.
                    _ov_key = f"_fefo_override_lot__{sap_code}"
                    _ov_why = f"_fefo_override_why__{sap_code}"
                    _overridden_lot = st.session_state.get(_ov_key)
                    _overridden_reason = st.session_state.get(_ov_why, "").strip()
                    try:
                        from database import suggest_fefo_lot_for_consumption
                        _suggested = suggest_fefo_lot_for_consumption(
                            sap_code=sap_code, site_id=site_id, conn=conn2,
                        )
                    except Exception:
                        _suggested = None

                    if _overridden_lot and len(_overridden_reason) >= 5:
                        input_data["Lot_Number"]    = _overridden_lot
                        input_data["FEFO_Override"] = _overridden_reason
                        # Audit + WhatsApp the HOD — this is an exception
                        # event worth surfacing in real time.
                        try:
                            from database import log_audit_action
                            log_audit_action(
                                user.get("username", ""),
                                "FEFO_OVERRIDE", "pending_issues",
                                f"sap={sap_code} site={site_id} "
                                f"chose={_overridden_lot} fefo_was={_suggested} "
                                f"reason={_overridden_reason!r}",
                            )
                            hod_q = pd.read_sql(
                                "SELECT Phone_Number FROM users WHERE role='hod' "
                                "AND Site_ID=? AND Phone_Number IS NOT NULL "
                                "AND Phone_Number<>'' LIMIT 1",
                                conn2, params=(site_id,),
                            )
                            if not hod_q.empty:
                                queue_whatsapp_alert(
                                    str(hod_q.iloc[0]["Phone_Number"]),
                                    (f"⚠️ *FEFO OVERRIDE — {site_id}*\n"
                                     f"👤 {user.get('username','')}\n"
                                     f"📦 [{sap_code}]\n"
                                     f"Pulled: {_overridden_lot}\n"
                                     f"FEFO suggested: {_suggested or 'n/a'}\n"
                                     f"Reason: {_overridden_reason}"),
                                )
                        except Exception:
                            pass
                        # Clear override so the next submission starts fresh.
                        st.session_state.pop(_ov_key, None)
                        st.session_state.pop(_ov_why, None)
                    elif _suggested:
                        input_data["Lot_Number"] = _suggested

                    if not override_expiry:
                        expiry_df = cached_short_dated_stock(site_id=site_id)
                        if not expiry_df.empty:
                            item_expiry = expiry_df[expiry_df["SAP_Code"] == sap_code]
                            if not item_expiry.empty:
                                exp_row = item_expiry.iloc[0]
                                conn2.close()
                                st.error(
                                    f"⚠️ **STOP — Expiring Stock Detected!**\n\n"
                                    f"There is a batch of **{exp_row['Equipment_Description']}** "
                                    f"at your site with status **{exp_row['Status']}** "
                                    f"(Expiry: **{exp_row['Expiry_Date']}**, Qty: {exp_row['Quantity']}).\n\n"
                                    f"Please physically pull from the expiring batch first. "
                                    f"Once done, check **'⚠️ Override Expiry Warning'** above to proceed."
                                )
                                st.stop()

                    # Round 12 — auto-fill Issued_By with the logged-in SK's
                    # username (no manual textbox). Requested_By stays NULL
                    # on SK-direct rows; SMR-sourced rows arrive via
                    # approve_supervisor_request which sets it explicitly.
                    input_data["Issued_By"] = user.get("username", "")
                    columns      = ["SAP_Code"] + list(input_data.keys())
                    placeholders = ", ".join(["?"] * len(columns))
                    values = [sap_code] + [
                        str(v) if isinstance(v, datetime.date) else v
                        for v in input_data.values()
                    ]
                    conn2.execute(
                        f"INSERT INTO pending_issues ({', '.join(columns)}) VALUES ({placeholders})",
                        values,
                    )
                    conn2.commit()
                    conn2.close()
                    st.toast("✅ Added to staging queue", icon="📥")
                    st.rerun()

        conn3 = get_connection()
        pending_df = pd.read_sql("""
            SELECT p.id, p.Date, p.SAP_Code,
                   i.Equipment_Description AS Material_Name, i.UOM, p.*
            FROM pending_issues p
            LEFT JOIN inventory i ON p.SAP_Code = i.SAP_Code
            WHERE COALESCE(p.Site_ID,'HQ') = ? AND COALESCE(p.status,'draft') = 'draft'
        """, conn3, params=(site_id,))
        pending_df = pending_df.loc[:, ~pending_df.columns.duplicated()]
        _pq_count  = len(pending_df)
        _pq_badge  = (
            f'<span style="background:rgba(212,175,55,0.18);border:1px solid rgba(212,175,55,0.40);'
            f'color:#D4AF37;font-size:11px;font-weight:700;padding:1px 8px;'
            f'border-radius:999px;margin-left:8px;">{_pq_count}</span>'
        ) if _pq_count else ""
        st.markdown(
            f'<div style="display:flex;align-items:center;margin:0.9rem 0 0.5rem 0;">'
            f'<span style="color:#F0F4F8;font-size:1rem;font-weight:700;">📋 Staging Queue</span>'
            f'{_pq_badge}'
            f'</div>',
            unsafe_allow_html=True,
        )

        if pending_df.empty:
            render_empty_state(
                icon="📋",
                title="Staging queue is empty",
                hint="Scan or pick a material above and click **Add to Grid** to start your shift.",
            )
        else:
            # Round 12 — surface how many lines in this batch came from a
            # Supervisor Material Request. Helps the SK know what to enrich
            # (batch numbers, final qty) before submitting to HOD.
            _smr_mask = (
                pending_df.get("Source_Ref", pd.Series([], dtype=object))
                .fillna("").astype(str).str.startswith("SMR:")
            )
            _smr_count = int(_smr_mask.sum()) if not pending_df.empty else 0
            if _smr_count:
                st.markdown(
                    f'<div style="background:rgba(212,175,55,0.10);'
                    f'border:1px solid rgba(212,175,55,0.35);'
                    f'border-left:3px solid {BRAND_GOLD};'
                    f'border-radius:7px;padding:8px 12px;margin:0 0 10px 0;">'
                    f'<span style="color:{BRAND_GOLD};font-weight:700;">🛡️ '
                    f'{_smr_count} supervisor-requested line(s)</span>'
                    f'<span style="color:{TEXT_MUTED};font-size:12px;"> '
                    f'in this batch. Add batch numbers / adjust quantities '
                    f'before submitting to HOD.</span></div>',
                    unsafe_allow_html=True,
                )
            view_df = pending_df.set_index("id")
            col_cfg = {
                "SAP_Code":      st.column_config.TextColumn("SAP Code",      disabled=True),
                "Material_Name": st.column_config.TextColumn("Material Name", disabled=True),
                "UOM":           st.column_config.TextColumn("UOM",           disabled=True),
                "Work_Type":     st.column_config.SelectboxColumn("Work Type", options=work_types),
                "Timestamp":     None,
                "status":        None,
            }
            edited_df = st.data_editor(
                view_df, column_config=col_cfg,
                num_rows="dynamic", width="stretch", key="staging_editor",
            )

            # Phase 5 — Attachments expander shown above the submit buttons.
            from config import ATTACHMENT_ALLOWED as _AT
            with st.expander("📎 Attach Documents (Optional)", expanded=False):
                st.caption(
                    "Attach reference documents (PDF / JPEG / JPG / XLSX). "
                    "Auto doc number for consumption = DDMMYY of submission date."
                )
                cons_scope = st.radio(
                    "Apply attachments to:",
                    ["Whole entry (batch)", "Specific date"],
                    horizontal=True, key="cons_attach_scope",
                )
                cons_attach_date = None
                if cons_scope == "Specific date":
                    cons_attach_date = st.date_input(
                        "Pick date for these attachments",
                        value=datetime.date.today(), key="cons_attach_date",
                    )
                cons_files = st.file_uploader(
                    "Files (multiple allowed)",
                    type=list(_AT),
                    accept_multiple_files=True,
                    key="cons_attach_files",
                )

            btn_save, btn_draft, btn_submit = st.columns([1, 1, 2])
            with btn_draft:
                # Phase 7E — manual Save Draft (bypasses 1/min server throttle).
                render_manual_save_draft_button(
                    "sk_consumption", user.get("username", "sk"),
                    site_id, _SK_CONSUMPTION_STATE_KEYS,
                    label="🛟 Save Form Draft",
                    key_suffix="sk_cons_main",
                )
            with btn_save:
                if st.button("💾 Save Draft Edits", use_container_width=True):
                    # Round 12 — detect SMR-sourced rows the SK removed from
                    # the editor BEFORE the wipe-and-reinsert. Each missing
                    # row gets withdraw_smr_line_at_staging() so the SMR
                    # record reflects 'withdrawn_at_staging' for that line.
                    from database import withdraw_smr_line_at_staging
                    _kept_ids = set(int(i) for i in edited_df.index.tolist())
                    for _row_id in pending_df["id"].astype(int).tolist():
                        if _row_id in _kept_ids:
                            continue
                        # Only call for SMR-sourced rows — the helper is
                        # silent on SK-direct deletions.
                        withdraw_smr_line_at_staging(
                            pending_issue_id=_row_id,
                            sk_username=user.get("username", ""),
                            conn=conn3,
                        )

                    save_cols = [col for col in edited_df.columns if col not in {"Material_Name", "UOM", "Timestamp"}]
                    ph = ", ".join(["?"] * (len(save_cols) + 1))
                    c2 = conn3.cursor()
                    c2.execute(
                        "DELETE FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ? AND COALESCE(status,'draft') = 'draft'",
                        (site_id,)
                    )
                    for idx, row in edited_df.iterrows():
                        vals = [idx] + [row[col] for col in save_cols]
                        c2.execute(
                            f"INSERT INTO pending_issues (id, {', '.join(save_cols)}) VALUES ({ph})",
                            vals,
                        )
                    conn3.commit()
                    st.toast("💾 Draft queue saved", icon="💾")
                    st.rerun()

            with btn_submit:
                if st.button("📨 Submit Grid to HOD", type="primary", use_container_width=True):
                    items_df = pd.read_sql("""
                        SELECT p.SAP_Code, i.Equipment_Description, p.Quantity
                        FROM pending_issues p
                        LEFT JOIN inventory i ON p.SAP_Code = i.SAP_Code
                        WHERE COALESCE(p.Site_ID,'HQ') = ? AND COALESCE(p.status,'draft') = 'draft'
                    """, conn3, params=(site_id,))

                    if items_df.empty:
                        st.warning("⚠️ No draft items to submit.")
                    else:
                        # Round 12 — belt-and-suspenders: same negative-stock
                        # validator that gates HOD's EOD commit now runs at
                        # the SK Submit step too. Stock can still shift
                        # between SK submit and HOD commit, so the HOD-side
                        # check stays in place.
                        from database import validate_eod_no_negative_stock
                        _viol = validate_eod_no_negative_stock(
                            conn3, site_id, items_df,
                        )
                        if _viol:
                            _rows = "\n".join(
                                f"• [{v['sap_code']}] {v['name']} — "
                                f"current {v['current']:g} {v['uom']}, "
                                f"to consume {v['to_consume']:g} → "
                                f"deficit {v['deficit']:g}"
                                for v in _viol
                            )
                            st.error(
                                "🛑 **Insufficient stock for one or more lines.**\n\n"
                                + _rows
                                + "\n\nReduce quantities or receive more stock "
                                + "before submitting."
                            )
                            st.stop()
                        conn3.execute(
                            "UPDATE pending_issues SET status = 'pending_hod' WHERE COALESCE(Site_ID,'HQ') = ? AND COALESCE(status,'draft') = 'draft'",
                            (site_id,)
                        )
                        conn3.commit()

                        # Persist attached docs (if any) under doc_type='consumption'.
                        # Doc number = DDMMYY of the date or today.
                        if cons_files:
                            from database import save_entry_attachment
                            attach_date = cons_attach_date or datetime.date.today()
                            doc_num = attach_date.strftime("%d%m%y")
                            for f in cons_files:
                                save_entry_attachment(
                                    site_id=site_id, doc_type="consumption",
                                    doc_number=doc_num, file_obj=f,
                                    uploaded_by=user["username"],
                                    entry_table="pending_issues",
                                    entry_date=attach_date.isoformat(),
                                    conn=conn3,
                                )

                        hod_q = pd.read_sql(
                            "SELECT Phone_Number FROM users WHERE role = 'hod' AND Site_ID = ? LIMIT 1",
                            conn3, params=(site_id,)
                        )
                        if not hod_q.empty and hod_q.iloc[0]["Phone_Number"]:
                            item_lines = "\n".join(
                                f"• {r['Quantity']}x [{r['SAP_Code']}] {r['Equipment_Description']}"
                                for _, r in items_df.iterrows()
                            )
                            msg = (
                                f"📝 *ISSUE STAGING SUBMITTED ({site_id})*\n"
                                f"👤 Store Keeper: {user['username']}\n\n"
                                f"📦 *Submitted Items ({len(items_df)}):*\n"
                                f"{item_lines}\n\n"
                                f"Queue is ready for your EOD commit review."
                            )
                            queue_whatsapp_alert(hod_q.iloc[0]["Phone_Number"], msg)

                        st.success(f"✅ {len(items_df)} item(s) submitted to HOD for review!")
                        # Phase 7E — staging queue has been moved to
                        # status='pending_hod'; wipe the in-flight row draft.
                        clear_form_draft(
                            "sk_consumption", user.get("username", "sk"),
                        )
                        st.rerun()

        conn3.close()

        # Phase 7E — per-rerun auto-save (every run → localStorage;
        # server-side throttled to 1/min inside the helper).
        auto_save_form_draft(
            "sk_consumption", user.get("username", "sk"),
            site_id, _SK_CONSUMPTION_STATE_KEYS,
        )

    # ── TAB 2: Receipt Staging ─────────────────────────────────────────────────
    with tab_receipt_stage:
        st.subheader("📦 Stage Inbound Receipts")
        st.caption("Add received materials to the draft queue, then submit to HOD for approval.")

        # Phase 7E — restore in-flight row selections (PR link / SAP /
        # MTC number / attachment metadata) if a draft exists.
        render_form_recovery_banner(
            "sk_receipt_staging", user.get("username", "sk"),
            site_id, _SK_RECEIPT_STATE_KEYS,
        )

        # Phase 3 — Procurement chain. DNs that already passed Logistics +
        # HOD approval land here as ready-to-confirm rows. Confirming
        # writes directly into `receipts` so Live Dashboard reflects the
        # stock the same minute.
        _render_incoming_dns_expander(user=user, site_id=site_id)

        # Phase 5 — bulk OCR upload (delivery note image or pasted text)
        with st.expander("📷 Upload Delivery Note (OCR)", expanded=False):
            _render_receipt_ocr(user=user, site_id=site_id, inv_list=inv_list)

        # Discover receipt form columns dynamically — mirrors consumption log pattern
        # Open a fresh connection: the shared `conn` was closed after tab setup above.
        _pragma_conn = get_connection()
        _rcpt_all_cols = _pragma_conn.execute("PRAGMA table_info(pending_receipts)").fetchall()
        _pragma_conn.close()
        # `rejection_reason` lives on pending_receipts but is HOD-side state;
        # the SK should never see/fill it on the staging form.
        rcpt_form_cols = [
            row[1] for row in _rcpt_all_cols
            if row[1] not in (
                SYSTEM_COLS | {"SAP_Code", "rejection_reason"} | HIDDEN_FORM_COLS
            )
        ]
        # All receipt fields mandatory per 2026-06 spec.
        OPTIONAL_RECEIPT_COLS: set[str] = set()

        with st.expander("➕ Add Receipt to Queue", expanded=True):
            # ── PR-linking (moved from HOD Receive Material) ──────────────
            # Loads open PRs for THIS site only. If a PR is chosen, the
            # material picker is restricted to the PR's line items and
            # the form's PR_Number field auto-fills + locks.
            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
                '1. Link to Open PR (Optional)</div>',
                unsafe_allow_html=True,
            )
            _conn_pr = get_connection()
            try:
                _open_prs = pd.read_sql(
                    "SELECT DISTINCT PR_Number FROM pr_master "
                    "WHERE Site_ID = ? AND status = 'open' "
                    "ORDER BY PR_Number",
                    _conn_pr, params=(site_id,),
                )
            finally:
                _conn_pr.close()
            _pr_options = ["-- None (Direct Receipt) --"] + _open_prs["PR_Number"].tolist()
            selected_pr = st.selectbox(
                "Link this receipt to an open PR",
                _pr_options, index=0, key="rcpt_pr_link",
                help="Selecting a PR filters the material list to that PR's items "
                     "and auto-fills the PR_Number field below.",
            )
            _linked_pr = None if selected_pr == "-- None (Direct Receipt) --" else selected_pr

            # Filter the material picker by the chosen PR.
            if _linked_pr:
                _conn_f = get_connection()
                try:
                    _pr_saps = pd.read_sql(
                        "SELECT DISTINCT SAP_Code FROM pr_master "
                        "WHERE PR_Number = ? AND Site_ID = ?",
                        _conn_f, params=(_linked_pr, site_id),
                    )["SAP_Code"].astype(str).str.strip().tolist()
                finally:
                    _conn_f.close()
                rcpt_search_options = [
                    s for s in search_options
                    if s.split("]")[0].replace("[", "").strip() in _pr_saps
                ] or search_options  # fall back to all if PR has zero items
            else:
                rcpt_search_options = search_options

            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;margin:10px 0 6px 0;">'
                '2. Select Material</div>',
                unsafe_allow_html=True,
            )
            sel_rcpt_item = st.selectbox(
                "Search by SAP Code or Description",
                options=rcpt_search_options,
                index=None,
                placeholder="Start typing…",
                key="rcpt_item_selectbox",
            )
            rcpt_sap = None
            rcpt_category = None
            mtc_number = ""
            mtc_file = None
            if sel_rcpt_item:
                rcpt_sap = sel_rcpt_item.split("]")[0].replace("[", "").strip()
                if not inv_list.empty:
                    m = inv_list[inv_list["SAP_Code"] == rcpt_sap]
                    if not m.empty:
                        _mat_code = m.iloc[0].get('Material_Code', 'N/A')
                        _uom      = m.iloc[0].get('UOM', 'N/A')
                        rcpt_category = str(m.iloc[0].get('Category', 'Others') or 'Others')
                        st.markdown(
                            f'<div style="background:rgba(59,130,246,0.09);'
                            f'border:1px solid rgba(59,130,246,0.27);'
                            f'border-radius:7px;padding:8px 12px;margin:4px 0 6px 0;'
                            f'font-size:12.5px;color:#93C5FD;">'
                            f'📋 <b style="color:#BFDBFE;">Mat Code:</b> {_mat_code}'
                            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
                            f'<b style="color:#BFDBFE;">UOM:</b> {_uom}'
                            f'&nbsp;&nbsp;|&nbsp;&nbsp;'
                            f'<b style="color:#BFDBFE;">Category:</b> {html.escape(rcpt_category)}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

            # Rubber-MTC gate: prompt for MTC number + file if this is rubber.
            # Missing MTC won't block submission — HOD will see a warning and
            # can email logistics for the missing doc.
            from config import RUBBER_CATEGORY, ATTACHMENT_ALLOWED
            if rcpt_category == RUBBER_CATEGORY:
                st.warning(
                    "⚠️ **Rubber material** — please attach the MTC document. "
                    "If not available, the HOD will be alerted to follow up with Logistics."
                )
                mtc_c1, mtc_c2 = st.columns([1, 2])
                with mtc_c1:
                    mtc_number = st.text_input(
                        "MTC Number",
                        placeholder="e.g. MTC-2026-001234",
                        key="rcpt_mtc_number",
                    )
                with mtc_c2:
                    mtc_file = st.file_uploader(
                        "MTC Document (PDF / JPEG / JPG / XLSX)",
                        type=list(ATTACHMENT_ALLOWED),
                        accept_multiple_files=False,
                        key="rcpt_mtc_file",
                    )

            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;'
                'margin-top:10px;margin-bottom:6px;">'
                '3. Fill Receipt Details</div>',
                unsafe_allow_html=True,
            )
            # Phase 5 — site-scoped stock for the qty-adjacent badge on the
            # receipt form. Info only — no block here, because a receipt
            # ADDS stock (the qty is what's incoming, not what's leaving).
            rcpt_site_snap = None
            if rcpt_sap:
                rcpt_site_snap = cached_item_snapshot(sap_code=rcpt_sap, site_id=site_id)
            rcpt_input = {}
            rn_cols = 2 if len(rcpt_form_cols) <= 4 else 3
            r_cols = st.columns(rn_cols)
            for i, col_name in enumerate(rcpt_form_cols):
                with r_cols[i % rn_cols]:
                    if col_name == "Date":
                        rcpt_input[col_name] = st.date_input(f"{col_name}*", datetime.date.today(), key=f"rcpt_{col_name}")
                    elif "qty" in col_name.lower() or "quantity" in col_name.lower():
                        # Highlighted, non-editable current-stock badge above the qty field.
                        render_stock_badge(rcpt_site_snap or {}, site_id=site_id)
                        rcpt_input[col_name] = st.number_input(f"{col_name}*", min_value=0.1, step=1.0, key=f"rcpt_{col_name}")
                    elif col_name == "Expiry_Date":
                        rcpt_input[col_name] = st.date_input(
                            f"{col_name} (Optional)", value=None,
                            key=f"rcpt_{col_name}",
                        )
                    elif col_name == "PR_Number" and _linked_pr:
                        # Auto-filled + read-only when a PR is linked above.
                        st.text_input(
                            f"{col_name}* (auto-filled from PR link)",
                            value=_linked_pr,
                            disabled=True,
                            key=f"rcpt_{col_name}_locked",
                        )
                        rcpt_input[col_name] = _linked_pr
                    elif col_name == "wbs":
                        from database import get_wbs_for_site
                        _wbs_df_r = get_wbs_for_site(site_id)
                        _wbs_opts_r = _wbs_df_r["WBS_Number"].tolist() if not _wbs_df_r.empty else []
                        if not _wbs_opts_r:
                            st.warning("⚠️ No active WBS for this site. Ask HOD to add WBS numbers in Site Config.")
                            rcpt_input[col_name] = st.text_input(
                                "WBS Number*", key=f"rcpt_{col_name}",
                            )
                        else:
                            rcpt_input[col_name] = st.selectbox(
                                "WBS Number*", _wbs_opts_r, index=0,
                                key=f"rcpt_{col_name}",
                            )
                    else:
                        rcpt_input[col_name] = st.text_input(f"{col_name}*", key=f"rcpt_{col_name}")

            if st.button("Add to Receipt Queue ⬇️", type="primary", key="rcpt_add_btn"):
                if not rcpt_sap:
                    st.error("⚠️ Please select a material from the search box. This field is mandatory.")
                    st.stop()
                _RCPT_OPTIONAL = {"Expiry_Date"}
                rcpt_missing = [
                    col for col, val in rcpt_input.items()
                    if col not in _RCPT_OPTIONAL
                    and (val is None or str(val).strip() == "")
                ]
                if rcpt_missing:
                    st.error(f"⚠️ Missing required fields: **{', '.join(rcpt_missing)}**. Every field marked * is mandatory.")
                    st.stop()
                conn_ra = get_connection()
                insert_cols = ["SAP_Code"] + list(rcpt_input.keys()) + ["status", "Site_ID"]
                insert_vals = [rcpt_sap] + [
                    str(v) if isinstance(v, datetime.date) and v is not None else v
                    for v in rcpt_input.values()
                ] + ["draft", site_id]
                placeholders = ", ".join(["?"] * len(insert_cols))
                cur_ra = conn_ra.cursor()
                cur_ra.execute(
                    f"INSERT INTO pending_receipts ({', '.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals,
                )
                pending_rcpt_id = cur_ra.lastrowid

                # Rubber-MTC capture — always insert a row so HOD can see
                # status='attached' vs status='missing'.
                if rcpt_category == RUBBER_CATEGORY:
                    from database import save_mtc_document
                    save_mtc_document(
                        conn=conn_ra,
                        site_id=site_id,
                        sap_code=rcpt_sap,
                        material_code=str(inv_list.loc[inv_list["SAP_Code"] == rcpt_sap, "Material_Code"].iloc[0])
                                       if not inv_list.empty and (inv_list["SAP_Code"] == rcpt_sap).any() else "",
                        lot_number=str(rcpt_input.get("Lot_Number", "") or ""),
                        quantity=float(rcpt_input.get("Quantity", 0) or 0),
                        mtc_number=mtc_number.strip(),
                        uploaded_file=mtc_file,
                        pending_receipt_id=pending_rcpt_id,
                        submitted_by=user["username"],
                    )
                conn_ra.commit()
                conn_ra.close()
                if rcpt_category == RUBBER_CATEGORY and not mtc_file:
                    st.toast("⚠️ Added — MTC missing, HOD will follow up", icon="⚠️")
                else:
                    st.toast("✅ Added to receipt queue", icon="📦")
                st.rerun()

        conn_rq = get_connection()
        rcpt_draft_df = pd.read_sql(
            """SELECT pr.id, pr.Date, pr.SAP_Code,
                      i.Equipment_Description AS Material_Name, i.UOM,
                      pr.Quantity, pr.Supplier, pr.Expiry_Date, pr.PR_Number, pr.Remarks
               FROM pending_receipts pr
               LEFT JOIN inventory i ON pr.SAP_Code = i.SAP_Code
               WHERE pr.status = 'draft' AND COALESCE(pr.Site_ID,'HQ') = ?
               ORDER BY pr.Timestamp ASC""",
            conn_rq, params=(site_id,)
        )
        _rq_count = len(rcpt_draft_df)
        _rq_badge = (
            f'<span style="background:rgba(212,175,55,0.18);border:1px solid rgba(212,175,55,0.40);'
            f'color:#D4AF37;font-size:11px;font-weight:700;padding:1px 8px;'
            f'border-radius:999px;margin-left:8px;">{_rq_count}</span>'
        ) if _rq_count else ""
        st.markdown(
            f'<div style="display:flex;align-items:center;margin:0.9rem 0 0.5rem 0;">'
            f'<span style="color:#F0F4F8;font-size:1rem;font-weight:700;">📋 Receipt Draft Queue</span>'
            f'{_rq_badge}'
            f'</div>',
            unsafe_allow_html=True,
        )

        if rcpt_draft_df.empty:
            render_empty_state(
                icon="📦",
                title="Receipt draft queue is empty",
                hint="Add inbound material above, then submit the batch to HOD for approval.",
            )
        else:
            rcpt_view = rcpt_draft_df.set_index("id")
            rcpt_col_cfg = {
                "SAP_Code":     st.column_config.TextColumn("SAP Code",      disabled=True),
                "Material_Name":st.column_config.TextColumn("Material Name", disabled=True),
                "UOM":          st.column_config.TextColumn("UOM",           disabled=True),
            }
            edited_rcpt = st.data_editor(
                rcpt_view, column_config=rcpt_col_cfg,
                num_rows="dynamic", width="stretch", key="rcpt_staging_editor",
            )

            # Phase 5 — Receipt attachments expander
            from config import ATTACHMENT_ALLOWED as _AT_RC
            with st.expander("📎 Attach Documents (Optional)", expanded=False):
                st.caption(
                    "Attach reference docs (PDF / JPEG / JPG / XLSX). "
                    "Doc number defaults to the DN No. of the receipt; "
                    "leave blank to auto-generate."
                )
                rc_scope = st.radio(
                    "Apply attachments to:",
                    ["Whole entry (batch)", "Specific date"],
                    horizontal=True, key="rcpt_attach_scope",
                )
                rc_attach_date = None
                if rc_scope == "Specific date":
                    rc_attach_date = st.date_input(
                        "Pick date", value=datetime.date.today(),
                        key="rcpt_attach_date",
                    )
                rc_dn_override = st.text_input(
                    "DN No. (optional override)",
                    placeholder="Defaults to the DN_No on each row",
                    key="rcpt_attach_dn",
                )
                rc_files = st.file_uploader(
                    "Files (multiple allowed)",
                    type=list(_AT_RC),
                    accept_multiple_files=True,
                    key="rcpt_attach_files",
                )

            rbtn_save, rbtn_draft, rbtn_submit = st.columns([1, 1, 2])
            with rbtn_draft:
                # Phase 7E — manual Save Draft (bypasses 1/min server throttle).
                render_manual_save_draft_button(
                    "sk_receipt_staging", user.get("username", "sk"),
                    site_id, _SK_RECEIPT_STATE_KEYS,
                    label="🛟 Save Form Draft",
                    key_suffix="sk_rcpt_main",
                )
            with rbtn_save:
                if st.button("💾 Save Draft Edits", key="rcpt_save_btn", use_container_width=True):
                    save_rc = [c for c in edited_rcpt.columns if c not in {"Material_Name", "UOM"}]
                    rph = ", ".join(["?"] * (len(save_rc) + 1))
                    rc = conn_rq.cursor()
                    rc.execute(
                        "DELETE FROM pending_receipts WHERE status = 'draft' AND COALESCE(Site_ID,'HQ') = ?",
                        (site_id,)
                    )
                    for idx, row in edited_rcpt.iterrows():
                        rc.execute(
                            f"INSERT INTO pending_receipts (id, {', '.join(save_rc)}) VALUES ({rph})",
                            [idx] + [row[c] for c in save_rc],
                        )
                    conn_rq.commit()
                    st.toast("💾 Receipt draft saved", icon="💾")
                    st.rerun()

            with rbtn_submit:
                if st.button("📨 Submit to HOD for Approval", type="primary", key="rcpt_submit_btn", use_container_width=True):
                    count_q = conn_rq.execute(
                        "SELECT COUNT(*) FROM pending_receipts WHERE status = 'draft' AND COALESCE(Site_ID,'HQ') = ?",
                        (site_id,)
                    ).fetchone()[0]
                    if count_q == 0:
                        st.warning("⚠️ No draft receipts to submit.")
                    else:
                        items_for_msg = pd.read_sql(
                            """SELECT pr.SAP_Code, i.Equipment_Description, pr.Quantity
                               FROM pending_receipts pr
                               LEFT JOIN inventory i ON pr.SAP_Code = i.SAP_Code
                               WHERE pr.status = 'draft' AND COALESCE(pr.Site_ID,'HQ') = ?""",
                            conn_rq, params=(site_id,)
                        )
                        conn_rq.execute(
                            "UPDATE pending_receipts SET status = 'pending_hod' WHERE status = 'draft' AND COALESCE(Site_ID,'HQ') = ?",
                            (site_id,)
                        )
                        conn_rq.commit()

                        # Persist attachments — doc_number = override OR first row's DN_No OR DDMMYY.
                        if rc_files:
                            from database import save_entry_attachment
                            attach_date = rc_attach_date or datetime.date.today()
                            dn_no = (rc_dn_override or "").strip()
                            if not dn_no:
                                _dn_lookup = pd.read_sql(
                                    "SELECT DN_No FROM pending_receipts "
                                    "WHERE status='pending_hod' AND COALESCE(Site_ID,'HQ')=? "
                                    "AND DN_No IS NOT NULL AND DN_No <> '' LIMIT 1",
                                    conn_rq, params=(site_id,),
                                )
                                dn_no = (
                                    str(_dn_lookup.iloc[0]["DN_No"])
                                    if not _dn_lookup.empty else attach_date.strftime("DN-%d%m%y")
                                )
                            for f in rc_files:
                                save_entry_attachment(
                                    site_id=site_id, doc_type="receipt",
                                    doc_number=dn_no, file_obj=f,
                                    uploaded_by=user["username"],
                                    entry_table="pending_receipts",
                                    entry_date=attach_date.isoformat(),
                                    conn=conn_rq,
                                )

                        hod_phone_q = pd.read_sql(
                            "SELECT Phone_Number FROM users WHERE role = 'hod' AND Site_ID = ? LIMIT 1",
                            conn_rq, params=(site_id,)
                        )
                        if not hod_phone_q.empty and hod_phone_q.iloc[0]["Phone_Number"]:
                            r_lines = "\n".join(
                                f"• {r['Quantity']}x [{r['SAP_Code']}] {r['Equipment_Description']}"
                                for _, r in items_for_msg.iterrows()
                            )
                            queue_whatsapp_alert(
                                hod_phone_q.iloc[0]["Phone_Number"],
                                (
                                    f"📦 *RECEIPT STAGING SUBMITTED ({site_id})*\n"
                                    f"👤 Store Keeper: {user['username']}\n\n"
                                    f"*Items Submitted ({count_q}):*\n"
                                    f"{r_lines}\n\n"
                                    f"Please review in HOD Portal → Pending Receipts."
                                ),
                            )

                        st.success(f"✅ {count_q} receipt(s) submitted to HOD for approval!")
                        # Phase 7E — wipe the in-flight row draft.
                        clear_form_draft(
                            "sk_receipt_staging", user.get("username", "sk"),
                        )
                        st.rerun()

        conn_rq.close()

        # Phase 7E — per-rerun auto-save.
        auto_save_form_draft(
            "sk_receipt_staging", user.get("username", "sk"),
            site_id, _SK_RECEIPT_STATE_KEYS,
        )

    # ── TAB 3: Returnable Items ────────────────────────────────────────────────
    with tab_returnables:
        st.caption("Track tools and equipment borrowed from the store on a temporary basis.")

        # ── 📷 Smart Scan (Phase 6D) ───────────────────────────────────────────
        # New flow at the top of the tab. The existing manual form below
        # is UNCHANGED — Smart Scan writes results into the manual form's
        # session_state keys so the SK can review + confirm before issue.
        _render_smart_scan_expander(site_id)

        with st.expander("➕ Issue a Returnable Item", expanded=True):
            ri1, ri2 = st.columns(2)
            with ri1:
                ri_material = st.text_input("Material / Tool Name*", key="ri_mat")
                ri_uom      = st.text_input("UOM (e.g. Pcs, Set)", key="ri_uom")
                ri_qty      = st.number_input("Quantity*", min_value=0.1, step=1.0, key="ri_qty")
            with ri2:
                ri_borrower      = st.text_input("Borrower Name*", key="ri_borrower")
                ri_phone         = st.text_input("Borrower WhatsApp No. (Optional, +966...)", key="ri_phone")
                ri_expected_back = st.date_input(
                    "Expected Return Date*", datetime.date.today() + datetime.timedelta(days=1),
                    key="ri_return_date"
                )
                _TIME_PRESETS = {"04:15 PM": "16:15:00", "06:15 PM": "18:15:00"}
                ri_time_preset = st.selectbox(
                    "Expected Return Time*",
                    list(_TIME_PRESETS.keys()) + ["Custom Time..."],
                    key="ri_time_preset",
                )
                if ri_time_preset == "Custom Time...":
                    ri_custom_time = st.time_input(
                        "Custom Time*", value=datetime.time(16, 15), key="ri_custom_time"
                    )
                    ri_time_str = ri_custom_time.strftime("%H:%M:%S")
                else:
                    ri_time_str = _TIME_PRESETS[ri_time_preset]

            if st.button("Issue Item 📤", type="primary", key="ri_issue_btn"):
                if not ri_material or not ri_borrower:
                    st.error("⚠️ Material name and borrower name are required.")
                    st.stop()
                expected_dt = f"{ri_expected_back} {ri_time_str}"
                insert_returnable_item(
                    material_name=ri_material,
                    uom=ri_uom,
                    qty=ri_qty,
                    borrower_name=ri_borrower,
                    borrower_phone=ri_phone or "",
                    expected_return_time=expected_dt,
                    site_id=site_id,
                )
                bust_inventory_cache()
                st.toast(f"✅ '{ri_material}' issued to {ri_borrower}", icon="📤")
                st.rerun()

        st.markdown(
            '<div style="font-size:11px;color:#4A6080;font-weight:700;'
            'text-transform:uppercase;letter-spacing:0.08em;'
            'margin:0.9rem 0 0.4rem 0;">Currently Borrowed Items</div>',
            unsafe_allow_html=True,
        )
        # ── 📷 Return-by-scan filter (Phase 6D) ────────────────────────────
        # Scanning a badge here filters the grid below to ONLY that
        # employee's open loans. Clear-filter button restores the full grid.
        _render_return_scan_filter(site_id)

        conn_ri = get_connection()
        ri_df = get_returnable_items(conn_ri, site_id=site_id)
        conn_ri.close()

        from config import auto_localize_timestamps
        ri_df = auto_localize_timestamps(ri_df)

        borrowed_df = ri_df[ri_df["status"] == "borrowed"].copy()

        # Apply the return-flow filter if a badge was scanned + locked.
        _filter_emp_id = st.session_state.get("_rs_filter_employee_id")
        if _filter_emp_id and not borrowed_df.empty:
            from database import get_open_loans_for_employee as _gol
            try:
                _filtered = _gol(_filter_emp_id, site_id=site_id)
                _allowed_ids = set(_filtered["id"].tolist()) if not _filtered.empty else set()
                borrowed_df = borrowed_df[borrowed_df["id"].isin(_allowed_ids)].copy()
            except Exception:
                # If the filter helper trips, fail open (show full grid)
                # rather than blocking the SK from acting.
                pass

        if borrowed_df.empty:
            st.success("✅ No items currently on loan.")
        else:
            now = pd.Timestamp.now()
            borrowed_df["expected_return_time"] = pd.to_datetime(
                borrowed_df["expected_return_time"], errors="coerce"
            )
            borrowed_df["is_overdue"] = borrowed_df["expected_return_time"] < now

            # Overdue banner — shows item names and escalation message
            overdue_names = borrowed_df.loc[borrowed_df["is_overdue"], "material_name"].tolist()
            if overdue_names:
                names_str = " · ".join(overdue_names)
                st.markdown(
                    f'<div style="background:rgba(239,68,68,0.09);'
                    f'border:1px solid rgba(239,68,68,0.33);'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:12px;'
                    f'color:#EF4444;font-size:13px;">'
                    f'⚠️ <strong>OVERDUE ITEMS:</strong> {names_str} '
                    f'— Contact borrower immediately or escalate to supervisor.'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Build display DataFrame with a "Status" pill column
            borrowed_df["Status"] = borrowed_df["is_overdue"].map(
                {True: "Overdue", False: "On Loan"}
            )
            borrowed_df["Expected Return"] = borrowed_df["expected_return_time"].dt.strftime(
                "%d/%m/%Y %H:%M"
            ).fillna("—")

            disp_cols = [c for c in [
                "id", "material_name", "uom", "qty",
                "borrower_name", "borrower_phone",
                "given_time", "Expected Return", "Status",
            ] if c in borrowed_df.columns or c in ("Expected Return", "Status")]
            render_aggrid(
                borrowed_df[disp_cols],
                key="borrowed_items_grid",
                height=280,
                column_styles={"Status": LOAN_STATUS_BADGE_JS},
            )

            st.markdown(
                '<div style="font-size:11px;color:#4A6080;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.08em;'
                'margin:0.9rem 0 0.4rem 0;">Mark as Returned</div>',
                unsafe_allow_html=True,
            )
            item_options = {
                f"[#{r['id']}] {r['material_name']} — {r['borrower_name']}": r["id"]
                for _, r in borrowed_df.iterrows()
            }
            selected_label = st.selectbox(
                "Select item to mark returned",
                list(item_options.keys()),
                key="ri_return_select",
                label_visibility="collapsed",
            )
            if st.button("✅ Mark as Returned", type="primary", key="ri_return_btn"):
                mark_item_returned(item_id=item_options[selected_label])
                bust_inventory_cache()
                st.toast("✅ Item marked as returned", icon="🔄")
                st.rerun()

    # ── TAB 3: Return Items (deduct stock — needs HOD approval) ───────────────
    with tab_return_items:
        _render_return_items_tab(user=user, site_id=site_id)

    # ── TAB 4: Stock Count (physical-count adjustment) ────────────────────────
    with tab_adjust:
        _render_stock_count_tab(user=user, site_id=site_id, inv_list=inv_list)

    # ── TAB 5: QR Label Request (SK → HOD approval) ───────────────────────────
    with tab_qr:
        _render_qr_request_tab(user=user, site_id=site_id, inv_list=inv_list,
                               search_options=search_options)

    # ── TAB 6: Supervisor Material Requests (Phase 7B) ────────────────────────
    with tab_smr:
        _render_supervisor_requests_tab(user=user, site_id=site_id)


def _render_return_items_tab(user: dict, site_id: str) -> None:
    """
    Real-return workflow (multi-row staging):
      1. Pick a material from last 30 days of receipts (or override → 12 months).
      2. If multiple receipts exist, pick the exact one.
      3. Fill qty / reason / DN / attachment + click 'Add to Grid'.
      4. Repeat for more items — they accumulate in a session-only queue.
      5. Click 'Submit Batch to HOD' to ship the whole queue at once.
    HOD approval writes each row to the `returns` ledger.
    """
    from config import ATTACHMENT_ALLOWED as _AT_RET
    from database import (
        get_returnable_receipts, get_work_types,
        submit_return_request, save_entry_attachment,
        queue_whatsapp_alert,
    )

    st.subheader("↩️ Return Items")
    st.caption(
        "Return material previously received from logistics. "
        "Pick items one at a time and click **Add to Grid** to queue them, "
        "then submit the whole batch to HOD for approval."
    )

    queue_key = "_ret_queue"
    if queue_key not in st.session_state:
        st.session_state[queue_key] = []

    allow_older = st.checkbox(
        "Override 30-day window (request HOD approval for older receipts)",
        key="_ret_override",
        help="When checked, the picker widens to the last 12 months.",
    )
    days_back = 365 if allow_older else 30

    recv_df = get_returnable_receipts(site_id=site_id, days_back=days_back)
    work_types = get_work_types(site_id=site_id) or [
        "Defective", "Wrong item", "Excess", "Damaged", "Other"
    ]

    # ── Row-builder form ────────────────────────────────────────────────────
    with st.expander("➕ Add a Return Line", expanded=True):
        if recv_df.empty:
            st.info(f"No receipts in the last {days_back} days for this site.")
        else:
            recv_df = recv_df.copy()
            recv_df["_mat_label"] = (
                "[" + recv_df["SAP_Code"].astype(str) + "] "
                + recv_df["Equipment_Description"].astype(str).fillna("")
            )
            mat_choices = sorted(recv_df["_mat_label"].unique().tolist())

            sel_mat = st.selectbox(
                "Material (only items received in this window)",
                options=mat_choices, index=None,
                placeholder="Start typing…", key="_ret_mat",
            )
            picked = None
            if sel_mat:
                sap = sel_mat.split("]")[0].replace("[", "").strip()
                rows_for_sap = (
                    recv_df[recv_df["SAP_Code"].astype(str).str.strip() == sap]
                    .reset_index(drop=True)
                )
                if len(rows_for_sap) > 1:
                    rows_for_sap["_pick_label"] = (
                        rows_for_sap["Date"].astype(str)
                        + "  ·  DN: " + rows_for_sap["DN_No"].astype(str)
                        + "  ·  Received Qty: " + rows_for_sap["received_qty"].astype(str)
                    )
                    pick = st.selectbox(
                        "Which receipt is being returned?",
                        options=rows_for_sap["_pick_label"].tolist(), index=None,
                        placeholder="Pick the exact receipt row…", key="_ret_pick",
                    )
                    if pick:
                        picked = rows_for_sap[
                            rows_for_sap["_pick_label"] == pick
                        ].iloc[0]
                else:
                    picked = rows_for_sap.iloc[0]

            if picked is not None:
                st.markdown(
                    f'<div style="background:rgba(34,197,94,0.07);'
                    f'border:1px solid rgba(34,197,94,0.25);border-radius:7px;'
                    f'padding:8px 12px;margin:6px 0;font-size:12.5px;color:#86EFAC;">'
                    f'📋 <b>Received:</b> {html.escape(str(picked["Date"]))}'
                    f' · <b>DN No.:</b> {html.escape(str(picked["DN_No"] or "—"))}'
                    f' · <b>PR:</b> {html.escape(str(picked["PR_Number"] or "—"))}'
                    f' · <b>Lot:</b> {html.escape(str(picked["Lot_Number"] or "—"))}'
                    f' · <b>Received Qty:</b> {picked["received_qty"]}'
                    f' {html.escape(str(picked["UOM"] or ""))}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                c1, c2 = st.columns(2)
                with c1:
                    ret_qty = st.number_input(
                        "Return Quantity*", min_value=0.01,
                        max_value=float(picked["received_qty"]),
                        step=1.0, key="_ret_qty",
                    )
                    ret_reason = st.selectbox(
                        "Reason*", work_types, index=None,
                        placeholder="Select a reason…", key="_ret_reason",
                    )
                with c2:
                    ret_dn = st.text_input(
                        "Return DN No.*", key="_ret_dn",
                        placeholder="e.g. RDN-2026-0042",
                    )
                    override_note = ""
                    if allow_older:
                        override_note = st.text_input(
                            "Override justification (sent to HOD)*",
                            key="_ret_over_note",
                            placeholder="Why are you returning beyond the 30-day window?",
                        )

                ret_files = st.file_uploader(
                    "📎 Attach Return DN / supporting docs (PDF / JPEG / JPG / XLSX)*",
                    type=list(_AT_RET), accept_multiple_files=True,
                    key="_ret_files",
                )

                if st.button("➕ Add to Grid", type="secondary", key="_ret_add_btn"):
                    problems = []
                    if not ret_qty:        problems.append("Return Quantity")
                    if not ret_reason:     problems.append("Reason")
                    if not ret_dn.strip(): problems.append("Return DN No.")
                    if not ret_files:      problems.append("Attachment")
                    if allow_older and not override_note.strip():
                        problems.append("Override justification")
                    if problems:
                        st.error(
                            f"⚠️ Missing required field(s): {', '.join(problems)}"
                        )
                    else:
                        # Read file bytes now — UploadedFile is single-use and
                        # disappears on the next rerun.
                        captured_files = [
                            {
                                "name": f.name,
                                "type": getattr(f, "type", "") or "",
                                "data": f.read(),
                            }
                            for f in ret_files
                        ]
                        st.session_state[queue_key].append({
                            "sap": sap,
                            "Material_Code": str(picked.get("Material_Code", "") or ""),
                            "Equipment_Description": str(picked.get("Equipment_Description", "") or ""),
                            "Quantity": float(ret_qty),
                            "Reason": ret_reason,
                            "Return_DN_No": ret_dn.strip(),
                            "received_date": str(picked.get("Date", "") or ""),
                            "received_dn_no": str(picked.get("DN_No", "") or ""),
                            "received_qty": float(picked.get("received_qty", 0) or 0),
                            "PR_Number": str(picked.get("PR_Number", "") or ""),
                            "Lot_Number": str(picked.get("Lot_Number", "") or ""),
                            "UOM": str(picked.get("UOM", "") or ""),
                            "override_required": bool(allow_older),
                            "override_reason": override_note,
                            "files": captured_files,
                        })
                        st.toast(
                            f"➕ Added {ret_qty} × [{sap}] to the return grid",
                            icon="📦",
                        )
                        st.rerun()

    # ── Draft queue table + bulk submit ─────────────────────────────────────
    queue = st.session_state[queue_key]
    st.markdown(
        f'<div style="display:flex;align-items:center;margin:0.9rem 0 0.5rem 0;">'
        f'<span style="color:#F0F4F8;font-size:1rem;font-weight:700;">'
        f'📋 Return Draft Queue</span>'
        f'<span style="background:rgba(212,175,55,0.18);border:1px solid rgba(212,175,55,0.40);'
        f'color:#D4AF37;font-size:11px;font-weight:700;padding:1px 8px;'
        f'border-radius:999px;margin-left:8px;">{len(queue)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not queue:
        render_empty_state(
            icon="↩️",
            title="No return lines queued",
            hint="Pick a material above, fill the form, click Add to Grid.",
        )
        return

    for idx, row in enumerate(queue):
        rc1, rc2 = st.columns([10, 1])
        with rc1:
            override_pill = (
                ' <span style="color:#FCA5A5;font-size:11px;font-weight:700;">'
                '⚠ Override</span>' if row["override_required"] else ""
            )
            st.markdown(
                f'<div style="padding:6px 0;color:#F0F4F8;font-size:13px;">'
                f'<span style="color:#D4AF37;font-family:monospace;">[{row["sap"]}]</span> '
                f'{html.escape(row["Equipment_Description"])} — '
                f'<b>{row["Quantity"]}</b> {html.escape(row["UOM"])} · '
                f'<span style="color:#7A8FA0;">'
                f'Reason: {html.escape(row["Reason"])} · '
                f'Return DN: {html.escape(row["Return_DN_No"])} · '
                f'Src DN: {html.escape(row["received_dn_no"] or "—")} '
                f'({html.escape(row["received_date"] or "—")}) · '
                f'📎 {len(row["files"])} file(s)'
                f'</span>'
                f'{override_pill}'
                f'</div>',
                unsafe_allow_html=True,
            )
        with rc2:
            if st.button("🗑", key=f"_ret_del_{idx}", use_container_width=True,
                         help="Remove this line"):
                st.session_state[queue_key].pop(idx)
                st.rerun()

    st.write("")
    bcA, bcB = st.columns([1, 2])
    with bcA:
        if st.button("✗ Clear Queue", key="_ret_clear", use_container_width=True):
            st.session_state[queue_key] = []
            st.rerun()
    with bcB:
        if st.button("📨 Submit Batch to HOD for Approval",
                     type="primary", key="_ret_batch_submit",
                     use_container_width=True):
            class _BytesBlob:
                def __init__(self, data, name, mime):
                    self.data = data
                    self.name = name
                    self.type = mime
                def read(self): return self.data
                def seek(self, _p): return None

            submitted_ids = []
            for row in queue:
                picked_dict = {
                    "Material_Code": row["Material_Code"],
                    "Equipment_Description": row["Equipment_Description"],
                    "Date": row["received_date"],
                    "DN_No": row["received_dn_no"],
                    "PR_Number": row["PR_Number"],
                    "Lot_Number": row["Lot_Number"],
                    "received_qty": row["received_qty"],
                }
                rid = submit_return_request(
                    site_id=site_id, sap_code=row["sap"],
                    quantity=row["Quantity"],
                    return_reason=row["Reason"],
                    return_dn_no=row["Return_DN_No"],
                    received_receipt_row=picked_dict,
                    submitted_by=user["username"],
                    override_required=row["override_required"],
                    override_reason=row["override_reason"],
                )
                submitted_ids.append(rid)
                for fmeta in row["files"]:
                    save_entry_attachment(
                        site_id=site_id, doc_type="return",
                        doc_number=row["Return_DN_No"],
                        file_obj=_BytesBlob(fmeta["data"], fmeta["name"], fmeta["type"]),
                        uploaded_by=user["username"],
                        entry_table="pending_returns", entry_id=rid,
                        entry_date=str(datetime.date.today()),
                    )

            # One WhatsApp ping summarising the batch
            conn_n = get_connection()
            try:
                hod_q = pd.read_sql(
                    "SELECT Phone_Number FROM users "
                    "WHERE role='hod' AND Site_ID=? LIMIT 1",
                    conn_n, params=(site_id,),
                )
                if not hod_q.empty and hod_q.iloc[0]["Phone_Number"]:
                    lines = "\n".join(
                        f"• {row['Quantity']}x [{row['sap']}] {row['Equipment_Description']} — {row['Reason']}"
                        for row in queue
                    )
                    has_override = any(r["override_required"] for r in queue)
                    queue_whatsapp_alert(
                        hod_q.iloc[0]["Phone_Number"],
                        (
                            f"↩️ *RETURN BATCH ({site_id})*\n"
                            f"👤 Store Keeper: {user['username']}\n\n"
                            f"*Lines ({len(queue)}):*\n{lines}\n\n"
                            + ("⚠️ One or more lines need 30-day override approval.\n"
                               if has_override else "")
                            + "Please review in HOD Portal → Returns."
                        ),
                    )
            finally:
                conn_n.close()

            st.session_state[queue_key] = []
            st.toast(
                f"✅ Submitted {len(submitted_ids)} return line(s) to HOD",
                icon="📨",
            )
            st.rerun()


def _render_qr_request_tab(user: dict, site_id: str,
                           inv_list: pd.DataFrame,
                           search_options: list) -> None:
    st.subheader("🏷️ Request QR Labels")
    st.caption(
        "Pick one or more materials and set the label quantity per item, "
        "then submit the batch for HOD approval. After approval, HOD "
        "downloads the QR PDF from the HOD Portal."
    )
    from database import submit_qr_request, list_qr_requests

    sel_items = st.multiselect(
        "Search materials by SAP / description (multi-select)",
        options=search_options,
        key="qr_req_multi",
        placeholder="Start typing — pick one or more materials…",
    )

    # Per-item qty editor (renders rows for the selected items)
    qty_map: dict[str, int] = {}
    if sel_items:
        st.caption("Set number of labels per item:")
        for itm in sel_items:
            sap = itm.split("]")[0].replace("[", "").strip()
            qty_map[sap] = int(st.number_input(
                f"{itm}", min_value=1, step=1, value=1,
                key=f"qr_qty_{sap}",
            ))

    if st.button("📨 Submit Batch for HOD Approval",
                 type="primary", key="qr_req_btn",
                 disabled=not sel_items):
        submitted = 0
        for sap, q in qty_map.items():
            submit_qr_request(
                site_id=site_id, sap_code=sap,
                requested_by=user["username"], quantity=q,
            )
            submitted += 1
        st.toast(f"✅ Submitted {submitted} request(s) to HOD", icon="🏷️")
        st.rerun()

    st.markdown("---")
    st.caption("Your recent requests:")
    mine = list_qr_requests(site_id=site_id)
    if not mine.empty:
        from config import auto_localize_timestamps
        mine = auto_localize_timestamps(mine)
        mine_view = mine[mine["requested_by"] == user["username"]]
        if mine_view.empty:
            st.caption("No requests yet.")
        else:
            st.dataframe(
                mine_view[["id", "SAP_Code", "Equipment_Description",
                           "Quantity", "status", "requested_at",
                           "approved_by", "approved_at"]],
                use_container_width=True, hide_index=True,
            )
    else:
        st.caption("No requests yet.")


# ===========================================================================
# Stock Count tab — Store Keeper physical-count → HOD-approved adjustment
# ===========================================================================
def _render_stock_count_tab(user: dict, site_id: str,
                            inv_list: pd.DataFrame) -> None:
    """
    Submit a physical-count discrepancy as a reconciliation document.

    Flow: pick material → system shows current stock → enter counted qty →
    pick reason → optional notes → submit. The row lands in
    `stock_adjustments` with status='pending_hod' and shows up in the HOD
    Portal Adjustments tab. On HOD approval, a synthetic ledger row posts
    so the perpetual-inventory identity stays exact.
    """
    from database import (
        insert_stock_adjustment,
        get_stock_adjustment_history,
        ADJUSTMENT_REASONS,
    )

    st.markdown(
        '<div style="font-size:11px;color:#4A6080;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">'
        '1. Select Material to Count</div>',
        unsafe_allow_html=True,
    )

    if inv_list is None or inv_list.empty:
        st.warning("Inventory is empty — add items via Admin Portal first.")
        return

    options = inv_list["Search_String"].tolist()
    selected = st.selectbox(
        "Search by SAP Code or Description",
        options=options,
        index=None,
        placeholder="Pick material to reconcile…",
        key="adj_item_selectbox",
    )

    if not selected:
        st.caption(
            "Use this tab when the **physical shelf count** disagrees with the "
            "system stock — damage, expiry disposal, miscount correction, "
            "found-extra, etc. After HOD approval, the ledger updates and "
            "your live stock matches reality again."
        )

        # History at bottom even when no item picked
        st.divider()
        st.markdown(
            '<div style="font-size:11px;color:#4A6080;font-weight:700;'
            'text-transform:uppercase;letter-spacing:0.08em;margin:0.5rem 0 0.4rem 0;">'
            'Recent Adjustments at This Site</div>',
            unsafe_allow_html=True,
        )
        hist = get_stock_adjustment_history(site_id=site_id, limit=15)
        if hist.empty:
            st.caption("No adjustments on file yet.")
        else:
            show = hist[["id", "SAP_Code", "Material_Name", "variance",
                         "reason_code", "status", "submitted_by",
                         "submitted_at"]].copy()
            show["reason_code"] = show["reason_code"].map(
                lambda r: ADJUSTMENT_REASONS.get(r, r)
            )
            st.dataframe(show, hide_index=True, use_container_width=True)
        return

    sap_code = selected.split("]")[0].replace("[", "").strip()

    # Snapshot of current site stock (read-only, what we'll record as system_qty)
    snap = cached_item_snapshot(sap_code=sap_code, site_id=site_id)
    system_qty = float(snap.get("current_stock") or 0.0) if snap else 0.0
    uom = str(snap.get("uom") or "") if snap else ""

    st.markdown(
        '<div style="font-size:11px;color:#4A6080;font-weight:700;'
        'text-transform:uppercase;letter-spacing:0.08em;'
        'margin:14px 0 6px 0;">2. Enter Count Details</div>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            f'<div style="padding:10px 14px;background:rgba(10,25,47,0.45);'
            f'border:1px dashed rgba(255,215,0,0.22);border-radius:10px;'
            f'margin-bottom:8px;">'
            f'<div style="color:#7A8FA0;font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.06em;">📊 System Qty at {site_id}</div>'
            f'<div style="color:#F0F4F8;font-size:1.4rem;font-weight:700;'
            f'line-height:1.2;margin-top:2px;">{system_qty:g} '
            f'<span style="color:#7A8FA0;font-size:0.85rem;font-weight:500;">'
            f'{uom}</span></div></div>',
            unsafe_allow_html=True,
        )
        counted_qty = st.number_input(
            "🔢 Counted Qty (physical shelf) *",
            min_value=0.0,
            value=system_qty,
            step=1.0,
            key="adj_counted_qty",
            help="Whatever you actually count on the shelf right now.",
        )
        variance = counted_qty - system_qty

        # Variance preview
        if abs(variance) < 1e-9:
            var_color, var_lbl = "#7A8FA0", "No variance · nothing to submit"
        elif variance > 0:
            var_color, var_lbl = "#22C55E", f"➕ Found {variance:g} extra"
        else:
            var_color, var_lbl = "#EF4444", f"➖ Short by {abs(variance):g}"
        st.markdown(
            f'<div style="padding:8px 12px;background:rgba(10,25,47,0.35);'
            f'border-left:3px solid {var_color};border-radius:6px;'
            f'color:{var_color};font-weight:600;font-size:0.92rem;">'
            f'{var_lbl}</div>',
            unsafe_allow_html=True,
        )

    with c2:
        # Reason dropdown
        reason_labels = list(ADJUSTMENT_REASONS.values())
        reason_keys   = list(ADJUSTMENT_REASONS.keys())
        # Default reason depends on variance direction
        default_idx = (
            reason_keys.index("miscount_in") if variance > 0
            else reason_keys.index("cycle_count")
        )
        chosen_label = st.selectbox(
            "🏷️ Reason Code *",
            reason_labels,
            index=default_idx,
            key="adj_reason",
        )
        reason_code = reason_keys[reason_labels.index(chosen_label)]
        notes = st.text_area(
            "📝 Notes (optional)",
            placeholder="e.g. found in box behind shelf 3 / damaged in transit / …",
            key="adj_notes",
            max_chars=300,
            height=110,
        )

    st.markdown(
        f'<div style="background:rgba(245,158,11,0.06);'
        f'border:1px solid rgba(245,158,11,0.22);border-left:3px solid #F59E0B;'
        f'border-radius:7px;padding:8px 12px;margin:10px 0;'
        f'color:#F59E0B;font-size:12.5px;">'
        f'⚠️ Submitting this sends the count to your HOD for approval. '
        f'No stock changes until they approve.</div>',
        unsafe_allow_html=True,
    )

    submit_disabled = abs(variance) < 1e-9
    if st.button(
        "📤 Submit Count for HOD Approval",
        type="primary",
        disabled=submit_disabled,
        key="adj_submit_btn",
    ):
        ok, msg, _adj_id = insert_stock_adjustment(
            site_id=site_id,
            sap_code=sap_code,
            system_qty=system_qty,
            counted_qty=counted_qty,
            reason_code=reason_code,
            notes=notes,
            submitted_by=user.get("username", ""),
        )
        if ok:
            # Notify the site HOD via WhatsApp
            try:
                conn_n = get_connection()
                hod_q = pd.read_sql(
                    "SELECT Phone_Number FROM users WHERE role='hod' AND Site_ID=? "
                    "AND Phone_Number IS NOT NULL AND Phone_Number<>'' LIMIT 1",
                    conn_n, params=(site_id,),
                )
                conn_n.close()
                if not hod_q.empty:
                    queue_whatsapp_alert(
                        str(hod_q.iloc[0]["Phone_Number"]),
                        (f"🧮 *STOCK ADJUSTMENT — {site_id}*\n"
                         f"👤 By: {user.get('username','')}\n"
                         f"📦 [{sap_code}] {snap.get('description','')}\n"
                         f"System: {system_qty:g} {uom} → Counted: {counted_qty:g} {uom}\n"
                         f"Variance: {variance:+g} · Reason: {chosen_label}\n\n"
                         f"Approve in HOD Portal → Adjustments tab."),
                    )
            except Exception:
                pass
            st.toast(msg, icon="📤")
            st.rerun()
        else:
            st.error(msg)


# ===========================================================================
# Phase 5 — OCR upload helpers (consumption + delivery note)
# ===========================================================================
def _render_consumption_ocr(user: dict, site_id: str, inv_list: pd.DataFrame,
                            work_types: list) -> None:
    """
    Two-lane (image upload OR paste text) bulk consumption-stager.

    Lane A — Image: uploads picture → vision LLM → rows.
    Lane B — Paste: textarea (Issued_To, Material, UOM, Qty, Work_Type) → rows.

    Both lanes feed the SAME fuzzy-match + editable preview + submit flow.
    On submit, each row lands in pending_issues with status='draft' (the
    HOD's EOD review picks it up exactly like a hand-typed entry).
    """
    from ai.fuzzy import resolve_rows
    from ai.ocr import (
        extract_consumption_from_image, parse_consumption_paste,
    )

    if inv_list is None or inv_list.empty:
        st.warning("Inventory is empty — add items via Admin Portal first.")
        return

    lane = st.radio(
        "Input method",
        ["📷 Image upload (vision AI)", "📝 Paste text"],
        horizontal=True, key="cons_ocr_lane",
    )

    parsed = None
    if lane.startswith("📷"):
        # Round 14 — accept HEIC/HEIF too. iPhone shares often arrive as
        # HEIC even when renamed to .JPG; without these extensions Streamlit
        # rejects them at the browser before our prep step can handle it.
        img = st.file_uploader(
            "Upload a photo of the handwritten list",
            type=["png", "jpg", "jpeg", "webp", "heic", "heif"],
            key="cons_ocr_img",
        )
        if img is not None:
            if st.button("🔎 Extract rows from image", type="primary", key="cons_ocr_run_btn"):
                with st.spinner("Vision model reading the list…"):
                    parsed = extract_consumption_from_image(img.getvalue())
                st.session_state["cons_ocr_parsed"] = parsed
        # Persist across reruns so reviewing the editor doesn't redo OCR.
        parsed = parsed or st.session_state.get("cons_ocr_parsed")
    else:
        st.caption(
            "One row per line. Columns: **Issued To, Material, UOM, Quantity, Work Type** — "
            "separated by tab / comma / semicolon / pipe."
        )
        txt = st.text_area("Paste here", height=140, key="cons_ocr_text")
        if st.button("🔎 Parse pasted rows", type="primary", key="cons_paste_run_btn"):
            parsed = parse_consumption_paste(txt)
            st.session_state["cons_ocr_parsed"] = parsed
        parsed = parsed or st.session_state.get("cons_ocr_parsed")

    if parsed is None:
        return
    if not parsed.ok:
        st.error(parsed.message or "Could not parse input.")
        return

    # Fuzzy-resolve each row against inventory.
    resolved = resolve_rows(parsed.rows, inv_list, name_key="material_text")

    # Header section: shared fields applied to ALL rows (the "adding section").
    st.markdown("**📌 Shared fields (apply to every row below)**")
    h1, h2, h3 = st.columns(3)
    with h1:
        date_val = st.date_input("Date*", datetime.date.today(), key="cons_ocr_date")
    with h2:
        wt_idx = 0  # Default Work_Type from first row if it has one
        wt_default = next((r["work_type"] for r in resolved if r.get("work_type")), "")
        if wt_default and wt_default in work_types:
            wt_idx = work_types.index(wt_default)
        shared_wt = st.selectbox("Default Work Type", work_types, index=wt_idx, key="cons_ocr_wt")
    with h3:
        shared_pr = st.text_input("PR Number (optional)", key="cons_ocr_pr")

    h4, h5 = st.columns(2)
    with h4:
        shared_tank = st.text_input("Tank No (optional)", key="cons_ocr_tank")
    with h5:
        shared_remarks = st.text_input("Remarks (optional)", key="cons_ocr_remarks")

    # Review grid.
    st.markdown(f"**📋 {len(resolved)} row(s) ready for review**")
    edited, _picks = render_ocr_review_grid(
        resolved, inv_list,
        key_prefix="cons_ocr",
        columns=["SAP_Code", "material_text", "Equipment_Description",
                 "UOM", "quantity", "issued_to", "work_type"],
        column_config={
            "SAP_Code":              st.column_config.TextColumn("SAP Code"),
            "material_text":         st.column_config.TextColumn("Material (as written)", disabled=True),
            "Equipment_Description": st.column_config.TextColumn("Matched", disabled=True),
            "UOM":                   st.column_config.TextColumn("UOM"),
            "quantity":              st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0),
            "issued_to":             st.column_config.TextColumn("Issued To"),
            "work_type":             st.column_config.TextColumn("Work Type (per-row, blank = use shared)"),
        },
    )

    if st.button("✅ Submit all rows to draft queue", type="primary", key="cons_ocr_submit_btn"):
        _submit_consumption_ocr(
            edited_df=edited, user=user, site_id=site_id,
            date_val=date_val, shared_wt=shared_wt, shared_pr=shared_pr,
            shared_tank=shared_tank, shared_remarks=shared_remarks,
        )


def _submit_consumption_ocr(edited_df, user, site_id, date_val,
                            shared_wt, shared_pr, shared_tank, shared_remarks):
    """Validate completeness then bulk-stage to pending_issues status='draft'."""
    if edited_df is None or edited_df.empty:
        st.warning("Nothing to submit.")
        return
    rows: list[dict] = []
    incomplete = 0
    for _, r in edited_df.iterrows():
        sap = str(r.get("SAP_Code", "") or "").strip()
        qty = r.get("quantity")
        if not sap or not qty or float(qty) <= 0:
            incomplete += 1
            continue
        rows.append({
            "SAP_Code":  sap,
            "Quantity":  float(qty),
            "Date":      str(date_val),
            "Work_Type": (str(r.get("work_type", "") or "").strip() or shared_wt),
            "Issued_By": user.get("username", ""),
            "Issued_To": str(r.get("issued_to", "") or "").strip(),
            "Tank_No":   shared_tank,
            "PR_Number": shared_pr,
            "Remarks":   shared_remarks,
        })

    if not rows:
        st.error("Every row needs both a SAP Code (or candidate pick) and a Quantity > 0.")
        return
    if incomplete:
        st.warning(f"Skipping {incomplete} incomplete row(s) — fill SAP + Quantity then resubmit.")

    n = pwa_stage_pending_issues(
        rows=rows, username=user.get("username", ""), site_id=site_id,
    )
    bust_inventory_cache()
    # Clear OCR session state so the expander is ready for the next batch.
    for k in ("cons_ocr_parsed", "cons_ocr_text"):
        st.session_state.pop(k, None)
    st.toast(f"✅ Staged {n} consumption row(s) to draft queue", icon="📝")
    st.success(f"{n} row(s) staged to draft. Submit to HOD from the Staging Queue below.")
    st.rerun()


def _render_incoming_dns_expander(user: dict, site_id: str) -> None:
    """Phase 3 — Procurement chain. Shows DNs that already passed HOD
    approval and are pending physical confirmation at this site. One click
    writes rows into `receipts` and closes the DN."""
    from database import list_incoming_dns_for_sk, get_dn_detail, sk_mark_dn_received
    df = list_incoming_dns_for_sk(site_id)
    badge = f" ({len(df)})" if not df.empty else ""
    with st.expander(f"🚚 Incoming Delivery Notes from Warehouse{badge}",
                     expanded=not df.empty):
        if df.empty:
            st.caption(
                "Nothing inbound. Logistics → HOD-approved DNs will appear "
                "here for you to confirm physical receipt."
            )
            return
        for _, row in df.iterrows():
            with st.container(border=True):
                cA, cB = st.columns([3, 1])
                with cA:
                    st.markdown(
                        f"**DN {row['DN_Number']}** · PO `{row['PO_Number']}` "
                        f"· Warehouse `{row['Warehouse_ID']}` · "
                        f"DN Date {row.get('DN_Date') or '—'}"
                    )
                    st.caption(
                        f"{int(row.get('line_count') or 0)} line(s) · "
                        f"{float(row.get('total_qty') or 0):.2f} units"
                    )
                    with st.expander("View lines", expanded=False):
                        items = get_dn_detail(row["DN_Number"])["items"]
                        if not items.empty:
                            cols = [c for c in [
                                "Material_Code", "Description", "Qty", "UOM",
                                "Lot_Number", "Expiry_Date", "Remarks",
                            ] if c in items.columns]
                            st.dataframe(items[cols],
                                         use_container_width=True,
                                         hide_index=True)
                with cB:
                    if st.button(
                        "✅ Mark as Received",
                        type="primary",
                        key=f"_sk_dn_recv_{row['DN_Number']}",
                        use_container_width=True,
                    ):
                        ok, msg = sk_mark_dn_received(
                            dn_number=row["DN_Number"],
                            store_keeper=user["username"],
                        )
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.balloons()
                            st.rerun()


def _render_receipt_ocr(user: dict, site_id: str, inv_list: pd.DataFrame) -> None:
    """
    Two-lane delivery-note importer.

    Image → vision LLM → {header, items}.
    Paste → 'Key: value' header lines + comma-separated item rows.

    Header maps to pending_receipts columns: DN_No, Date, Mob_From,
    Driver_Name, Vehicle_No, Prepared_by, Mob_To. Each item row gets
    fuzzy-matched against inventory like consumption rows.
    """
    from ai.fuzzy import resolve_rows
    from ai.ocr import (
        extract_delivery_note_from_image, parse_delivery_note_paste,
    )

    if inv_list is None or inv_list.empty:
        st.warning("Inventory is empty — add items via Admin Portal first.")
        return

    lane = st.radio(
        "Input method",
        ["📷 Image upload (vision AI)", "📝 Paste text"],
        horizontal=True, key="rcpt_ocr_lane",
    )

    parsed = None
    if lane.startswith("📷"):
        # Round 14 — accept HEIC/HEIF; see consumption OCR uploader above.
        img = st.file_uploader(
            "Upload a photo of the delivery note",
            type=["png", "jpg", "jpeg", "webp", "heic", "heif"],
            key="rcpt_ocr_img",
        )
        if img is not None:
            if st.button("🔎 Extract delivery note", type="primary", key="rcpt_ocr_run_btn"):
                with st.spinner("Vision model reading the note…"):
                    parsed = extract_delivery_note_from_image(img.getvalue())
                st.session_state["rcpt_ocr_parsed"] = parsed
        parsed = parsed or st.session_state.get("rcpt_ocr_parsed")
    else:
        st.caption(
            "Paste header lines like `DN_No: 15668` then comma-separated item "
            "rows (Material, UOM, Qty). See the placeholder for an example."
        )
        txt = st.text_area(
            "Paste delivery note",
            height=240,
            key="rcpt_ocr_text",
            placeholder=(
                "DN_No: 15668\nDate: 2026-06-02\nMob_From: GI - ABU HADRIYAH\n"
                "Driver_Name: Imran\nVehicle_No: 3909\nPrepared_by: Harshavardhan\n"
                "Mob_To: CNCEC-RAS AL KHAIR\n\n"
                "6m pipe, Nos, 45\n4m pipe, Nos, 60\n3m pipe, Nos, 35"
            ),
        )
        if st.button("🔎 Parse pasted note", type="primary", key="rcpt_paste_run_btn"):
            parsed = parse_delivery_note_paste(txt)
            st.session_state["rcpt_ocr_parsed"] = parsed
        parsed = parsed or st.session_state.get("rcpt_ocr_parsed")

    if parsed is None:
        return
    if not parsed.ok:
        st.error(parsed.message or "Could not parse input.")
        return

    # Editable header.
    st.markdown("**📌 Delivery Note header** (edit any field before submit)")
    hdr = parsed.header or {}
    h1, h2, h3 = st.columns(3)
    with h1:
        dn_no = st.text_input("DN No / Ref No*", value=hdr.get("DN_No", ""), key="rcpt_ocr_dn")
        prep  = st.text_input("Prepared By", value=hdr.get("Prepared_by", ""), key="rcpt_ocr_prep")
    with h2:
        # Date may be ISO or a free string the model returned; we don't force.
        date_raw = hdr.get("Date") or datetime.date.today().isoformat()
        try:
            _d_default = datetime.date.fromisoformat(date_raw[:10])
        except ValueError:
            _d_default = datetime.date.today()
        date_val = st.date_input("Date*", _d_default, key="rcpt_ocr_date")
        driver   = st.text_input("Driver Name", value=hdr.get("Driver_Name", ""), key="rcpt_ocr_drv")
    with h3:
        mob_from = st.text_input("Customer / Mob From*", value=hdr.get("Mob_From", ""), key="rcpt_ocr_from")
        vehicle  = st.text_input("Vehicle No", value=hdr.get("Vehicle_No", ""), key="rcpt_ocr_veh")

    mob_to = st.text_input("Location / Mob To", value=hdr.get("Mob_To", ""), key="rcpt_ocr_to")

    # Fuzzy-resolve items.
    resolved = resolve_rows(parsed.items, inv_list, name_key="material_text")

    st.markdown(f"**📋 {len(resolved)} item(s) ready for review**")
    edited, _picks = render_ocr_review_grid(
        resolved, inv_list,
        key_prefix="rcpt_ocr",
        columns=["SAP_Code", "material_text", "Equipment_Description", "UOM", "quantity"],
        column_config={
            "SAP_Code":              st.column_config.TextColumn("SAP Code"),
            "material_text":         st.column_config.TextColumn("Material (as written)", disabled=True),
            "Equipment_Description": st.column_config.TextColumn("Matched", disabled=True),
            "UOM":                   st.column_config.TextColumn("UOM"),
            "quantity":              st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0),
        },
    )

    if st.button("✅ Submit delivery note to draft queue", type="primary", key="rcpt_ocr_submit_btn"):
        _submit_receipt_ocr(
            edited_df=edited, user=user, site_id=site_id,
            header={
                "DN_No": dn_no, "Date": str(date_val), "Mob_From": mob_from,
                "Driver_Name": driver, "Vehicle_No": vehicle,
                "Prepared_by": prep, "Mob_To": mob_to,
            },
        )


def _submit_receipt_ocr(edited_df, user, site_id, header):
    """Validate row completeness then bulk-stage to pending_receipts status='draft'."""
    if edited_df is None or edited_df.empty:
        st.warning("Nothing to submit.")
        return
    if not header.get("DN_No") or not header.get("Mob_From"):
        st.error("DN No and Customer (Mob From) are required.")
        return

    rows: list[dict] = []
    incomplete = 0
    for _, r in edited_df.iterrows():
        sap = str(r.get("SAP_Code", "") or "").strip()
        qty = r.get("quantity")
        if not sap or not qty or float(qty) <= 0:
            incomplete += 1
            continue
        rows.append({
            "SAP_Code": sap,
            "Quantity": float(qty),
            "UOM":      str(r.get("UOM", "") or "").strip(),
        })

    if not rows:
        st.error("Every row needs both a SAP Code and a Quantity > 0.")
        return
    if incomplete:
        st.warning(f"Skipping {incomplete} incomplete row(s).")

    n = stage_pending_receipts_bulk(
        rows=rows, header=header, username=user.get("username", ""), site_id=site_id,
    )
    bust_inventory_cache()
    for k in ("rcpt_ocr_parsed", "rcpt_ocr_text"):
        st.session_state.pop(k, None)
    st.toast(f"✅ Staged {n} receipt row(s)", icon="📦")
    st.success(f"{n} row(s) staged to draft. HOD will see them in the Pending Receipts tab.")
    st.rerun()


# ===========================================================================
# Phase 6D — 📷 Smart Scan helpers
# ===========================================================================
_SS_KEYS = (
    "_ss_step", "_ss_employee_id", "_ss_employee_name",
    "_ss_employee_phone", "_ss_employee_img_hash",
    "_ss_tool_detections", "_ss_tool_class", "_ss_tool_display_name",
    "_ss_tool_confidence", "_ss_tool_img_hash",
    "_ss_cam_employee", "_ss_cam_tool",
)


def _ss_reset() -> None:
    """Clear every Smart Scan session_state key. Used by 🔄 Start over."""
    for k in _SS_KEYS:
        st.session_state.pop(k, None)


def _hash_image_bytes(b: bytes) -> str:
    """Short stable hash so we only re-decode when the captured image
    actually changed (Streamlit reruns on every interaction)."""
    import hashlib
    return hashlib.md5(b).hexdigest()[:12] if b else ""


def _render_smart_scan_expander(site_id: str) -> None:
    """Two-step camera flow: scan badge → scan tool → write to manual form.

    The expander is opt-in (collapsed by default). Once a badge is locked,
    Step 2's camera renders. Detections are bucketed via
    `bucket_detections` and written through to the existing manual form's
    session_state keys (ri_mat, ri_borrower, ri_phone).
    """
    expanded_default = st.session_state.get("_ss_step", "idle") != "idle"
    with st.expander("📷 Smart Scan (Beta)", expanded=expanded_default):
        st.caption(
            "Scan the borrower's badge first; then scan the tool. Detections "
            "above 0.75 confidence auto-fill the form below — anything lower "
            "shows candidates for you to pick."
        )

        # ── Step 1 — Scan Employee Badge ───────────────────────────────────
        st.markdown("**Step 1 — Scan Employee Badge**")
        img = st.camera_input(
            "📸 Point camera at the employee's QR badge",
            key="_ss_cam_employee",
        )
        if img is not None:
            raw = img.getvalue()
            h = _hash_image_bytes(raw)
            # Only decode + lookup when the image actually changed.
            if h != st.session_state.get("_ss_employee_img_hash"):
                from ai.cv.qr import decode_png_to_id
                from ai.cv.smart_scan import lookup_employee_by_qr
                payload = decode_png_to_id(raw)
                emp = lookup_employee_by_qr(payload) if payload else None
                st.session_state["_ss_employee_img_hash"] = h
                if emp:
                    st.session_state["_ss_employee_id"]    = emp["ID_Number"]
                    st.session_state["_ss_employee_name"]  = emp["Name"]
                    st.session_state["_ss_employee_phone"] = emp.get("Phone_Number") or ""
                    st.session_state["_ss_step"]           = "employee_locked"
                else:
                    # Reset employee state but keep the captured image so
                    # the user can retry without re-snapping.
                    for k in ("_ss_employee_id", "_ss_employee_name",
                              "_ss_employee_phone"):
                        st.session_state.pop(k, None)
                    st.session_state["_ss_step"] = "idle"

        emp_id = st.session_state.get("_ss_employee_id")
        if emp_id:
            st.success(
                f"✅ **{emp_id}** · {st.session_state.get('_ss_employee_name','')} "
                f"({st.session_state.get('_ss_employee_phone','—')})"
            )
        elif img is not None:
            st.error(
                "🚫 Badge not recognised. Either the QR couldn't be read or "
                "the employee isn't active in the Employees master. "
                "Re-snap or use the manual entry form below."
            )

        # ── Step 2 — Scan Tool (only after Step 1 succeeds) ────────────────
        if not emp_id:
            return

        st.markdown("---")
        st.markdown("**Step 2 — Scan Tool**")

        # Check whether a CV model is active. If none, show clear message
        # and skip tool scan but still pre-fill borrower details.
        try:
            from database import get_active_cv_model
            active_model = get_active_cv_model()
        except Exception:
            active_model = None

        if not active_model:
            st.info(
                "🤖 No active CV model — ask Admin to train and promote one. "
                "You can still confirm the employee above and fill the tool "
                "field manually below."
            )
            # Pre-fill borrower fields only.
            _smart_scan_fill_borrower_only()
        else:
            tool_img = st.camera_input(
                "📸 Point camera at the tool",
                key="_ss_cam_tool",
            )
            if tool_img is not None:
                raw = tool_img.getvalue()
                h = _hash_image_bytes(raw)
                if h != st.session_state.get("_ss_tool_img_hash"):
                    from ai.cv.inference import detect_tool
                    from ai.cv.smart_scan import bucket_detections
                    dets = detect_tool(raw, top_k=5)
                    st.session_state["_ss_tool_detections"] = dets
                    st.session_state["_ss_tool_img_hash"]   = h
                    # Phase 8C — cache raw bytes so the Tier-3 path can reach
                    # them across reruns (camera_input only exposes bytes on
                    # the rerun that captured the image).
                    st.session_state["_ss_tool_img_raw"]    = raw
                    # Reset prior pick so a new image doesn't carry over.
                    for k in ("_ss_tool_class", "_ss_tool_display_name",
                              "_ss_tool_confidence",
                              "_ss_tier3_candidates",
                              "_ss_tier3_shown_logged"):
                        st.session_state.pop(k, None)

            dets = st.session_state.get("_ss_tool_detections") or []
            from ai.cv.smart_scan import bucket_detections
            mode, items = bucket_detections(dets)

            if mode == "auto":
                top = items[0]
                _accept_tool_pick(top["class_name"], top["confidence"])
                st.success(
                    f"✅ Detected **{top['class_name']}** "
                    f"(confidence {top['confidence']:.2f}) — auto-filled below."
                )
            elif mode == "candidates":
                st.warning(
                    "⚠️ Multiple candidates — pick the correct tool, "
                    "or type a new name manually below."
                )
                labels = [
                    f"{d['class_name']} · {d['confidence']:.2f}"
                    for d in items
                ]
                pick = st.radio("Top candidates", labels, key="_ss_candidate_pick")
                if st.button("Use this tool", type="primary", key="_ss_use_candidate"):
                    idx = labels.index(pick)
                    chosen = items[idx]
                    _accept_tool_pick(chosen["class_name"], chosen["confidence"])
                    st.rerun()
            else:
                # Phase 8C — mode == "manual" path. Before falling through to
                # the borrower-only prefill, opportunistically invoke the
                # LocateAnything sidecar IF the admin gate is on AND we have
                # cached image bytes to send. Tier 3 NEVER auto-accepts.
                _maybe_render_tier3_branch(site_id, dets)

        # ── Start-over reset ───────────────────────────────────────────────
        st.markdown("---")
        if st.button("🔄 Start over (clear scan)", key="_ss_reset_btn"):
            _ss_reset()
            st.rerun()


def _smart_scan_fill_borrower_only() -> None:
    """Write only the borrower details from Step 1 into the manual form's
    session_state keys. No tool fields touched."""
    name  = st.session_state.get("_ss_employee_name", "")
    phone = st.session_state.get("_ss_employee_phone", "")
    if name:
        st.session_state["ri_borrower"] = name
    if phone:
        st.session_state["ri_phone"] = phone


def _accept_tool_pick(class_name: str, conf: float) -> None:
    """Record the chosen tool class + write through to the manual form."""
    st.session_state["_ss_tool_class"]        = class_name
    st.session_state["_ss_tool_display_name"] = _display_name_for_class(class_name)
    st.session_state["_ss_tool_confidence"]   = float(conf)
    st.session_state["_ss_step"]              = "tool_picked"

    # Write through to the existing manual form's keys so the SK sees
    # everything pre-filled in the "➕ Issue a Returnable Item" expander.
    _smart_scan_fill_borrower_only()
    st.session_state["ri_mat"] = st.session_state["_ss_tool_display_name"]


def _display_name_for_class(class_name: str) -> str:
    """Look up tool_catalogue.display_name for a class; fallback to the
    raw class_name if no row exists."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT display_name FROM tool_catalogue WHERE class_name = ?",
                (class_name,),
            ).fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return class_name


def _render_return_scan_filter(site_id: str) -> None:
    """Above the Currently Borrowed grid: scan badge → filter grid to one
    employee's open loans. Clear-filter button restores the full grid."""
    active_filter = st.session_state.get("_rs_filter_employee_id")
    title = "📷 Return by Badge Scan"
    if active_filter:
        emp_name = st.session_state.get("_rs_filter_employee_name", "")
        title = f"📷 Return by Badge Scan — filtering: {active_filter} · {emp_name}"

    with st.expander(title, expanded=bool(active_filter)):
        st.caption(
            "Scan an employee's badge to filter the borrowed list below to "
            "ONLY their open loans. Speeds up the 'mark returned' flow when "
            "an employee comes back with multiple tools."
        )

        col_cam, col_btn = st.columns([2, 1])
        with col_cam:
            img = st.camera_input(
                "📸 Scan badge",
                key="_rs_cam",
                label_visibility="collapsed",
            )
        with col_btn:
            if active_filter:
                if st.button("🔄 Clear filter", key="_rs_clear_btn"):
                    for k in ("_rs_filter_employee_id",
                              "_rs_filter_employee_name",
                              "_rs_img_hash",
                              "_rs_cam"):
                        st.session_state.pop(k, None)
                    st.rerun()

        if img is not None:
            raw = img.getvalue()
            h = _hash_image_bytes(raw)
            if h != st.session_state.get("_rs_img_hash"):
                from ai.cv.qr import decode_png_to_id
                from ai.cv.smart_scan import lookup_employee_by_qr
                payload = decode_png_to_id(raw)
                emp = lookup_employee_by_qr(payload) if payload else None
                st.session_state["_rs_img_hash"] = h
                if emp:
                    st.session_state["_rs_filter_employee_id"]   = emp["ID_Number"]
                    st.session_state["_rs_filter_employee_name"] = emp["Name"]
                    st.rerun()
                else:
                    st.session_state.pop("_rs_filter_employee_id", None)
                    st.session_state.pop("_rs_filter_employee_name", None)
                    st.error(
                        "🚫 Badge not recognised. Re-snap or clear and pick "
                        "the item manually from the full grid."
                    )


# ===========================================================================
# Phase 7B — 🛒 Supervisor Requests tab (Store Keeper side)
# ===========================================================================
def _render_supervisor_requests_tab(user: dict, site_id: str) -> None:
    """SK queue for pending supervisor material requests.

    SK can: edit Requested_Qty / SK_Adjusted_Qty / Notes, delete a line, view
    the worker's open returnable loans (side-panel), then Approve or Reject
    the whole request.

    On Approve (Round 12): approve_supervisor_request() mirrors each non-zero
    line into pending_issues with status='draft' (was 'pending_hod'), so the
    SK enriches batch numbers / final qty in the Consumption Log grid before
    submitting to HOD. Source_Ref + Requested_By carry the supervisor's
    identity all the way to the consumption ledger.
    """
    actor = user.get("username", "store_keeper")

    st.markdown(
        f'<h3 style="color:{BRAND_GOLD};font-weight:700;margin:0 0 4px 0;">'
        f'🛒 Supervisor Material Requests</h3>'
        f'<p style="color:{TEXT_MUTED};margin:0 0 14px 0;font-size:13px;">'
        f'Pending requests submitted by Supervisors at <b>{html.escape(site_id)}</b>. '
        f'Edit qty / notes / drop lines as needed, then Approve. Approved lines '
        f'land in your <b>📋 Consumption Log</b> staging grid for batch-number '
        f'/ final-qty entry before you submit the batch to the HOD.</p>',
        unsafe_allow_html=True,
    )

    pending = list_supervisor_requests(
        site_id=site_id, status="pending_sk",
    )
    if pending.empty:
        render_empty_state(
            icon="📭",
            title="Nothing in the queue",
            hint="When a Supervisor submits a material request, it appears here.",
        )
        _render_smr_history_panel(site_id)
        return

    for _, hdr in pending.iterrows():
        req_id = int(hdr["id"])
        with st.container(border=True):
            top = st.columns([2, 2, 2, 1])
            top[0].markdown(
                f'<b style="color:{BRAND_GOLD};font-family:monospace;">{hdr["request_no"]}</b><br>'
                f'<span style="color:{TEXT_MUTED};font-size:12px;">'
                f'Submitted: {hdr["requested_at"]}<br>By: {html.escape(hdr["requested_by"])}'
                f'</span>',
                unsafe_allow_html=True,
            )
            top[1].markdown(
                f'<span style="color:{TEXT_MUTED};font-size:12px;">Worker</span><br>'
                f'<b>{html.escape(hdr["Worker_Name"])}</b><br>'
                f'<code style="font-size:11.5px;">{html.escape(hdr["Worker_ID"])}</code>',
                unsafe_allow_html=True,
            )
            top[2].markdown(
                f'<span style="color:{TEXT_MUTED};font-size:12px;">Job / Tank / Place</span><br>'
                f'<b>{html.escape(hdr["Job_Tank_Place"])}</b>',
                unsafe_allow_html=True,
            )
            ppe = "Yes ✓" if int(hdr["Old_PPE_Returned"]) else "No ✗"
            ppe_color = "#22C55E" if int(hdr["Old_PPE_Returned"]) else "#EF4444"
            top[3].markdown(
                f'<span style="color:{TEXT_MUTED};font-size:12px;">Old PPE returned</span><br>'
                f'<b style="color:{ppe_color};">{ppe}</b>',
                unsafe_allow_html=True,
            )
            if not int(hdr["Old_PPE_Returned"]) and hdr.get("No_Return_Reason"):
                st.caption(f"📝 Supervisor's reason: {hdr['No_Return_Reason']}")

            # ── Open returnable loans side-panel for the worker ──────────
            loans_df = get_open_returnables_for_employee(hdr["Worker_ID"])
            with st.expander(
                f"🔄 Worker's open returnable loans ({len(loans_df)})",
                expanded=False,
            ):
                if loans_df.empty:
                    st.caption("No open tool / equipment loans for this worker.")
                else:
                    st.dataframe(
                        loans_df.rename(columns={
                            "material_name": "Tool / Equipment",
                            "qty": "Qty",
                            "uom": "UOM",
                            "borrower_name": "Borrower",
                            "given_time": "Given",
                            "expected_return_time": "Due",
                        }),
                        use_container_width=True, hide_index=True,
                    )

            # ── Editable items grid ──────────────────────────────────────
            _h, items = get_supervisor_request(req_id)
            if items.empty:
                st.warning("No line items.")
                continue

            edit_df = items[["id", "SAP_Code", "Material_Code",
                             "Equipment_Description", "UOM",
                             "Requested_Qty", "Stock_At_Request",
                             "SK_Adjusted_Qty", "Notes"]].copy()
            edit_df = edit_df.rename(columns={
                "Equipment_Description": "Description",
                "Stock_At_Request": "Stock@Req",
                "SK_Adjusted_Qty": "Approved_Qty (blank = use Requested)",
            })
            edited = st.data_editor(
                edit_df,
                key=f"smr_edit_{req_id}",
                use_container_width=True,
                hide_index=True,
                disabled=["id", "SAP_Code", "Material_Code",
                          "Description", "UOM", "Stock@Req"],
                num_rows="fixed",
                column_config={
                    "id": st.column_config.NumberColumn(
                        "ID", help="Line ID (read-only)", disabled=True,
                    ),
                    "Requested_Qty": st.column_config.NumberColumn(
                        "Requested", min_value=0.0, step=1.0,
                    ),
                    "Approved_Qty (blank = use Requested)":
                        st.column_config.NumberColumn(
                            "Approved",
                            help="Set 0 to drop the line. Blank = approve as requested.",
                            min_value=0.0, step=1.0,
                        ),
                    "Notes": st.column_config.TextColumn(
                        "Notes", help="Optional SK note saved with the line.",
                    ),
                },
            )

            # Live banner if any line's effective qty exceeds current stock.
            effective = edited[[
                "SAP_Code", "Requested_Qty", "Stock@Req",
                "Approved_Qty (blank = use Requested)",
            ]].copy()
            effective["__eff__"] = effective.apply(
                lambda r: (
                    float(r["Approved_Qty (blank = use Requested)"])
                    if pd.notna(r["Approved_Qty (blank = use Requested)"])
                    else float(r["Requested_Qty"])
                ),
                axis=1,
            )
            short = effective[effective["__eff__"] > effective["Stock@Req"]]
            if not short.empty:
                st.warning(
                    f"⚠️ {len(short)} line(s) exceed current stock-at-request. "
                    f"You can still approve — but consider adjusting quantities "
                    f"OR rejecting, since the HOD's negative-stock guard will "
                    f"block commit if stock has not improved."
                )

            # ── Action row ───────────────────────────────────────────────
            col_save, col_appr, col_rej = st.columns([1, 1, 1])
            with col_save:
                if st.button("💾 Save edits", key=f"smr_save_{req_id}",
                             use_container_width=True):
                    saved = 0
                    for _, row in edited.iterrows():
                        adj = row["Approved_Qty (blank = use Requested)"]
                        ok = update_supervisor_request_item(
                            int(row["id"]),
                            requested_qty=float(row["Requested_Qty"]),
                            sk_adjusted_qty=(
                                float(adj) if pd.notna(adj) else None
                            ),
                            notes=row["Notes"] or "",
                        )
                        if ok:
                            saved += 1
                    st.toast(f"💾 Saved {saved} line(s).", icon="✅")
                    st.rerun()
            with col_appr:
                if st.button("✅ Approve", key=f"smr_appr_{req_id}",
                             type="primary", use_container_width=True):
                    # First persist any pending edits so the approval picks
                    # them up — same write path as the Save button.
                    for _, row in edited.iterrows():
                        adj = row["Approved_Qty (blank = use Requested)"]
                        update_supervisor_request_item(
                            int(row["id"]),
                            requested_qty=float(row["Requested_Qty"]),
                            sk_adjusted_qty=(
                                float(adj) if pd.notna(adj) else None
                            ),
                            notes=row["Notes"] or "",
                        )
                    ok, msg = approve_supervisor_request(req_id, actor)
                    if ok:
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"🚫 {msg}")
            with col_rej:
                with st.popover("❌ Reject", use_container_width=True):
                    reason = st.text_area(
                        "Rejection reason (required)",
                        key=f"smr_rej_reason_{req_id}",
                        height=80,
                    ).strip()
                    if st.button("Confirm reject",
                                 key=f"smr_rej_go_{req_id}",
                                 type="primary",
                                 use_container_width=True):
                        ok, msg = reject_supervisor_request(req_id, actor, reason)
                        if ok:
                            st.success("Rejected.")
                            st.rerun()
                        else:
                            st.error(f"🚫 {msg}")

    # ── Round 12 — Supervisor Request History (collapsed by default) ─────
    _render_smr_history_panel(site_id)


_SMR_HISTORY_STATUS_LABELS = {
    "approved":  ("✅", "#22C55E"),
    "rejected":  ("❌", "#EF4444"),
    "cancelled": ("🚫", "#94A3B8"),
    "pending_sk": ("⏳", "#F59E0B"),
}


def _render_smr_history_panel(site_id: str) -> None:
    """Historical SMR table for the SK tab. Filters: Date range, Supervisor,
    Tank/Job. Defaults to decided-only (approved + rejected + cancelled) over
    the last 7 days. Toggle to include pending requests.
    """
    from database import list_smr_history
    with st.expander("📜 Supervisor Request History", expanded=False):
        st.caption(
            "Decided requests at this site over the selected window. "
            "Use filters to narrow by date, supervisor, or tank / job."
        )
        # ── Filter row ─────────────────────────────────────────────────
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 2, 2])
        with fc1:
            _today = datetime.date.today()
            dr = st.date_input(
                "Date range",
                value=(_today - datetime.timedelta(days=7), _today),
                key=f"_smr_hist_dr_{site_id}",
            )
            if isinstance(dr, tuple) and len(dr) == 2:
                d_from, d_to = dr
            else:
                d_from = d_to = dr
        # Pre-fetch a wide window for the supervisor + tank options so the
        # dropdowns reflect actual history, not just the filtered slice.
        opts_df = list_smr_history(site_id=site_id, days=180)
        sup_opts = ["— Any —"] + sorted(
            set(opts_df.get("requested_by", pd.Series(dtype=str)).dropna().tolist())
        )
        tank_opts = ["— Any —"] + sorted(
            set(opts_df.get("Job_Tank_Place", pd.Series(dtype=str)).dropna().tolist())
        )
        with fc2:
            sup = st.selectbox(
                "Supervisor", sup_opts,
                key=f"_smr_hist_sup_{site_id}",
            )
        with fc3:
            tk = st.selectbox(
                "Tank / Job", tank_opts,
                key=f"_smr_hist_tank_{site_id}",
            )
        with fc4:
            include_pending = st.toggle(
                "Include pending",
                value=False,
                key=f"_smr_hist_pend_{site_id}",
                help="Off (default): show only decided requests.",
            )

        status_in = (
            None if include_pending
            else ("approved", "rejected", "cancelled")
        )
        df = list_smr_history(
            site_id=site_id,
            status_in=status_in,
            date_from=str(d_from) if d_from else None,
            date_to=str(d_to) if d_to else None,
            supervisor=(sup if sup != "— Any —" else None),
            tank=(tk if tk != "— Any —" else None),
        )
        if df.empty:
            st.info("No history matches these filters.")
            return

        # Compact summary row.
        st.caption(
            f"Showing {len(df)} request(s). "
            f"Approved: {(df['status']=='approved').sum()} · "
            f"Rejected: {(df['status']=='rejected').sum()} · "
            f"Cancelled: {(df['status']=='cancelled').sum()} · "
            f"Pending: {(df['status']=='pending_sk').sum()}"
        )

        for _, row in df.iterrows():
            icon, colour = _SMR_HISTORY_STATUS_LABELS.get(
                row["status"], ("•", TEXT_MUTED),
            )
            head = (
                f'<b style="font-family:monospace;color:{BRAND_GOLD};">'
                f'{html.escape(str(row["request_no"]))}</b> '
                f'<span style="color:{colour};font-weight:700;">'
                f'{icon} {row["status"]}</span> · '
                f'<span style="color:{TEXT_MUTED};">'
                f'{html.escape(str(row["requested_at"]))} · '
                f'by {html.escape(str(row["requested_by"] or "—"))} · '
                f'{int(row["line_count"] or 0)} line(s) · '
                f'Worker {html.escape(str(row["Worker_Name"] or "—"))} · '
                f'Job {html.escape(str(row["Job_Tank_Place"] or "—"))}'
                f'</span>'
            )
            with st.expander(" ", expanded=False):
                st.markdown(head, unsafe_allow_html=True)
                if row.get("sk_reject_reason"):
                    st.caption(
                        f"❌ Reject reason: {row['sk_reject_reason']}"
                    )
                if row.get("sk_decided_by"):
                    st.caption(
                        f"Decided by {row['sk_decided_by']} "
                        f"at {row.get('sk_decided_at') or '—'}"
                    )
                # Per-line drill-down with line_status chips.
                try:
                    _h, lines = get_supervisor_request(int(row["id"]))
                except Exception:
                    lines = pd.DataFrame()
                if not lines.empty:
                    show_cols = [c for c in [
                        "SAP_Code", "Material_Code", "Equipment_Description",
                        "UOM", "Requested_Qty", "SK_Adjusted_Qty",
                        "Stock_At_Request", "line_status", "Notes",
                    ] if c in lines.columns]
                    st.dataframe(
                        lines[show_cols],
                        use_container_width=True, hide_index=True,
                    )


# ===========================================================================
# Phase 8C — Smart Scan Tier-3 (LocateAnything sidecar fallback)
# ===========================================================================
@st.cache_data(ttl=300, show_spinner=False)
def _get_catalogue_class_names() -> list[str]:
    """Return the list of class_name values from tool_catalogue. Used to
    seed the LocateAnything prompt — catalogue-scoped per spec Q2(a).

    Cached for 5 minutes — the catalogue rarely changes mid-day and
    cutting the round-trip-per-scan saves ~30ms on every Tier-3 invocation.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT class_name FROM tool_catalogue "
                "ORDER BY class_name COLLATE NOCASE ASC"
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows if r and r[0]]
    except Exception:
        return []


def _maybe_render_tier3_branch(site_id: str, yolo_dets: list) -> None:
    """Phase 8C — bridge between YOLO "manual" outcome and the LocateAnything
    sidecar. Gate-checked: admin toggle must be on AND we must have cached
    image bytes. If anything is missing, falls through to the existing
    borrower-only prefill behaviour (zero behaviour change vs pre-8C).
    """
    from ai.locate_anything import client as la_client
    from ai.cv.smart_scan import should_invoke_tier3, tier3_to_candidates

    raw = st.session_state.get("_ss_tool_img_raw")
    if not la_client.is_enabled() or not raw or not should_invoke_tier3(yolo_dets):
        # No Tier-3 path — preserve the original UX exactly.
        if yolo_dets is not None and not yolo_dets:
            st.info(
                "🚫 No confident match. Borrower details have been "
                "pre-filled — fill the tool field manually in the form "
                "below."
            )
        _smart_scan_fill_borrower_only()
        return

    # Already fetched in a prior rerun? Skip the HTTP call and re-render.
    cached = st.session_state.get("_ss_tier3_candidates")
    if cached is None:
        import base64
        classes = _get_catalogue_class_names()
        prompt_text = (
            "locate: " + ", ".join(classes)
            if classes else "locate the tool in the image"
        )
        # Pass through context for telemetry — client writes a row per call.
        actor_user = st.session_state.get("gi_user", {}).get(
            "username", "store_keeper"
        )
        yolo_top = (
            float(yolo_dets[0].get("confidence", 0.0) or 0.0)
            if yolo_dets else None
        )
        with st.spinner("🤖 Running AI fallback (LocateAnything)…"):
            ai_dets, call_id = la_client.detect(
                base64.b64encode(raw).decode("ascii"),
                prompt=prompt_text,
                classes=classes,
                site_id=site_id,
                sk_username=actor_user,
                yolo_top_conf=yolo_top,
            )
        candidates = tier3_to_candidates(ai_dets)
        st.session_state["_ss_tier3_candidates"] = candidates
        st.session_state["_ss_tier3_call_id"]    = call_id
    else:
        candidates = cached

    if not candidates:
        # AI also came up empty — same friendly fallback as the manual path.
        st.info(
            "🤖 AI fallback found nothing either. Fill the tool manually in "
            "the form below."
        )
        _smart_scan_fill_borrower_only()
        return

    _render_tier3_panel(candidates, site_id)


def _render_tier3_panel(candidates: list[dict], site_id: str) -> None:
    """Render the amber-bordered AI fallback panel. Spec Q4 — visually
    distinct from the green Tier-2 panel so SKs understand this is a less-
    trusted AI guess. CRITICAL CONTRACT: the "Use this tool" button is
    the ONLY accept path — there is NO auto-fill code branch on this path.
    """
    # ── One-shot TIER3_SHOWN audit per scan ──────────────────────────────
    actor_user = st.session_state.get("gi_user", {}).get("username", "store_keeper")
    if not st.session_state.get("_ss_tier3_shown_logged"):
        top_score = float(candidates[0]["confidence"]) if candidates else 0.0
        try:
            from database import log_audit_action
            log_audit_action(
                actor_user, "TIER3_SHOWN", "smart_scan",
                f"site={site_id} n={len(candidates)} top_score={top_score:.3f}",
            )
        except Exception:
            pass
        st.session_state["_ss_tier3_shown_logged"] = True

    # ── Amber-bordered panel ─────────────────────────────────────────────
    st.markdown(
        '<div style="background:#F59E0B14;border:1px solid #F59E0B66;'
        'border-radius:8px;padding:14px 16px;margin:10px 0;">'
        '<b style="color:#F59E0B;">🤖 AI fallback (LocateAnything)</b><br>'
        '<span style="color:#C0CCD8;font-size:12.5px;">'
        'YOLO was uncertain — the AI sees these candidates. '
        '<b>Verify the physical tool before issuing.</b>'
        '</span></div>',
        unsafe_allow_html=True,
    )

    labels = [
        f"{d['class_name']} · confidence {d['confidence']:.2f}"
        for d in candidates
    ]
    pick = st.radio(
        "AI suggestions",
        labels,
        key="_ss_tier3_pick",
        label_visibility="collapsed",
    )

    col_use, col_none = st.columns([1, 1])
    with col_use:
        # The ONLY accept path. Mirrors the Tier-2 "Use this tool" but tags
        # the audit row distinctly so we can later measure Tier-3 vs Tier-2
        # acceptance rates.
        if st.button("✅ Use this tool", type="primary",
                     key="_ss_tier3_use",
                     use_container_width=True):
            idx = labels.index(pick)
            chosen = candidates[idx]
            try:
                from database import (
                    log_audit_action, mark_locate_anything_outcome,
                )
                log_audit_action(
                    actor_user, "TIER3_ACCEPTED", "smart_scan",
                    f"site={site_id} class={chosen['class_name']} "
                    f"conf={chosen['confidence']:.3f}",
                )
                # Phase 8E — close the loop on the telemetry row this scan
                # generated, so the Admin cost/benefit panel can compute
                # the accept rate accurately.
                call_id = st.session_state.get("_ss_tier3_call_id", 0)
                if call_id:
                    mark_locate_anything_outcome(call_id, accepted=True)
            except Exception:
                pass
            _accept_tool_pick(chosen["class_name"], chosen["confidence"])
            st.rerun()
    with col_none:
        if st.button("🚫 None of these — manual entry",
                     key="_ss_tier3_reject",
                     use_container_width=True):
            try:
                from database import (
                    log_audit_action, mark_locate_anything_outcome,
                )
                log_audit_action(
                    actor_user, "TIER3_REJECTED", "smart_scan",
                    f"site={site_id} n_shown={len(candidates)}",
                )
                call_id = st.session_state.get("_ss_tier3_call_id", 0)
                if call_id:
                    mark_locate_anything_outcome(call_id, accepted=False)
            except Exception:
                pass
            # Clear cached candidates so a fresh scan doesn't re-render
            # the panel from stale session state.
            for k in ("_ss_tier3_candidates",
                      "_ss_tier3_shown_logged",
                      "_ss_tier3_call_id"):
                st.session_state.pop(k, None)
            _smart_scan_fill_borrower_only()
            st.rerun()
