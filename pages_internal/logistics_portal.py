"""
pages_internal/logistics_portal.py
==================================
Logistics Portal — Phase C of the procurement chain.

The Logistics role lives between Site HOD (who creates PRs) and Warehouse
(which physically receives goods). This page hosts the 8 tabs the role
needs to do its job end-to-end without touching any pre-existing flow.

Tabs:
  1. 📥 Incoming PRs   — PRs site HODs have submitted for PO issuance
  2. 🧾 Create PO      — manual entry OR PDF upload (pdfplumber extract)
  3. 📋 Open POs       — every active PO with drilldown
  4. 🏭 Assign to WH   — route a PO (or subset of items) to a warehouse
  5. 🔁 Reschedules    — incoming reschedule requests from WH / Site HOD
  6. 🛑 Force-Close    — close PR / PO / line with reason (audited)
  7. ↩️ Vendor Returns — returns to vendor with expected resupply date
  8. 📂 History        — closed PRs / POs (read-only)

This module never edits existing tables directly — everything funnels
through the helpers in database.py, which were added in Phase 1 + the
PO/PR/assignment helpers added at the top of this phase.
"""

from __future__ import annotations

import html
import json
import datetime

import pandas as pd
import streamlit as st

from config import (
    BRAND_GOLD, TEXT_PRIMARY, TEXT_MUTED, DARK_BORDER,
    auto_localize_timestamps,
    classify_rl_bl_family,
)
from database import (
    get_connection,
    log_audit_action,
    # PR queue
    list_prs_for_logistics, get_pr_lines,
    # POs
    create_po_manual, process_po_pdf, list_pos, get_po_detail,
    list_closed_pos_history,
    # Vendors / warehouses
    add_vendor, list_vendors, list_warehouses,
    # Assignment / reschedule / force-close / vendor returns
    assign_po_to_warehouse,
    list_pending_reschedules, decide_reschedule,
    force_close_target,
    raise_vendor_return, list_vendor_returns,
    list_force_closures,
    get_sites,
    # Round 15 — material master
    bulk_upsert_materials, next_sap_code, next_temp_material_code,
    set_site_min_qty, get_min_qty_for,
)
from ui_components import (
    render_brand_header,
    render_aggrid,
    render_empty_state,
    status_pill_html,
)


# ---------------------------------------------------------------------------
# Colour palette — mirrors HOD portal so the look is consistent.
# ---------------------------------------------------------------------------
_C = {
    "surf":   "#162038",
    "surf2":  "#1E3050",
    "border": "#2A4060",
    "gold":   "#D4AF37",
    "goldLt": "#F0D060",
    "blueLt": "#1A4D80",
    "text":   "#F0F4F8",
    "muted":  "#7A8FA0",
    "ok":     "#22C55E",
    "low":    "#F59E0B",
    "crit":   "#EF4444",
    "sky":    "#0EA5E9",
}


def _esc(v) -> str:
    """HTML-escape a cell value; None / NaN render as em-dash."""
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return html.escape(s) if s else "—"


def _section_card(title: str, body_html: str) -> str:
    return (
        f'<div style="background:{_C["surf"]};border:1px solid {_C["border"]};'
        f'border-radius:12px;padding:14px 18px;margin-bottom:14px;">'
        f'<div style="color:{_C["muted"]};font-size:11px;letter-spacing:0.1em;'
        f'text-transform:uppercase;margin-bottom:8px;">{html.escape(title)}</div>'
        f'{body_html}'
        f'</div>'
    )


def _kv_row(label: str, value) -> str:
    return (
        f'<div style="display:flex;justify-content:space-between;'
        f'padding:4px 0;border-bottom:1px dashed {_C["border"]}40;">'
        f'<span style="color:{_C["muted"]};font-size:12px;">{html.escape(label)}</span>'
        f'<span style="color:{_C["text"]};font-size:12.5px;font-weight:600;">'
        f'{_esc(value)}</span>'
        f'</div>'
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
        f'{html.escape(sub)}</div>'
        f'</div>'
    )


def _hero_strip(cards_html: list[str]) -> None:
    st.markdown(
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;">'
        + "".join(cards_html) +
        '</div>',
        unsafe_allow_html=True,
    )


# ===========================================================================
# TAB 1 — Incoming PRs
# ===========================================================================
def _tab_incoming_prs(user: dict) -> None:
    st.markdown(f"### 📥 Incoming Purchase Requests")
    st.caption(
        "PRs that site HODs have submitted for Logistics action. Closed PRs "
        "move to the History tab automatically."
    )

    # Optional site filter (Logistics + Admin both see all sites by default)
    sites = ["All sites"] + sorted(get_sites() or [])
    filt = st.selectbox("Filter by site", sites, key="_logi_pr_site_filter")
    site_id = None if filt == "All sites" else filt

    queue = list_prs_for_logistics(site_id=site_id)
    if queue.empty:
        render_empty_state(
            icon="📭",
            title="No PRs awaiting Logistics action",
            hint="Site HODs submit PRs from HOD Portal → Purchase Requests → 🚚 Submit to Logistics.",
        )
        return

    # Hero strip
    open_n = len(queue)
    total_qty = float(queue["total_qty"].sum() or 0)
    sites_n = queue["Site_ID"].nunique()
    _hero_strip([
        _hero("OPEN PRs", str(open_n), "awaiting PO", _C["gold"]),
        _hero("TOTAL QTY", f"{total_qty:,.0f}", "units across queue", _C["sky"]),
        _hero("SITES", str(sites_n), "submitting", _C["ok"]),
    ])

    # Queue table
    render_aggrid(queue.rename(columns={
        "PR_Number": "PR No.",
        "Site_ID": "Site",
        "line_count": "Lines",
        "total_qty": "Total Qty",
        "submitted_at": "Submitted",
        "earliest_delivery": "Earliest Delivery",
        "logistics_status": "Status",
    }), height=280, key="_logi_pr_queue_grid")

    st.divider()
    st.markdown("**Inspect a PR**")
    pr_choices = queue["PR_Number"] + "  ·  " + queue["Site_ID"]
    label_to_pair: dict[str, tuple[str, str]] = {}
    for lbl, pr, st_ in zip(pr_choices, queue["PR_Number"], queue["Site_ID"]):
        label_to_pair[lbl] = (pr, st_)
    sel = st.selectbox(
        "Open PR detail",
        options=list(label_to_pair.keys()),
        key="_logi_pr_detail_sel",
    )
    if sel:
        pr_no, st_id = label_to_pair[sel]
        lines = get_pr_lines(pr_no, site_id=st_id)
        if lines.empty:
            st.info("No lines on this PR.")
            return

        st.markdown(
            _section_card(
                "PR Detail",
                _kv_row("PR Number", pr_no)
                + _kv_row("Site", st_id)
                + _kv_row("Lines", len(lines))
                + _kv_row("Total Qty",
                          f"{float(lines['Requested_Qty'].sum() or 0):,.2f}")
                + _kv_row("Earliest Delivery",
                          lines["Delivery_Date"].min() if "Delivery_Date" in lines else "—"),
            ),
            unsafe_allow_html=True,
        )
        # Show columns useful for PO creation
        cols_show = [
            "Material_Code", "Material_Name", "Requested_Qty", "UOM",
            "WBS_Number", "Network", "Plant", "Delivery_Date",
            "Supplier", "Est_Cost_SAR",
        ]
        cols_show = [c for c in cols_show if c in lines.columns]
        render_aggrid(lines[cols_show], height=320, key="_logi_pr_lines_grid")

        # Quick-create-PO shortcut
        if st.button(
            "🧾 Use this PR to create a PO",
            type="primary",
            key="_logi_pr_to_po_btn",
        ):
            st.session_state["_logi_create_po_pr"] = pr_no
            st.session_state["_logi_create_po_site"] = st_id
            st.session_state["_logi_active_tab"] = 1  # nudge user to next tab
            st.toast(f"Loaded PR {pr_no} into Create PO tab — switch tabs to continue.",
                     icon="🧾")


# ===========================================================================
# TAB 2 — Create PO
# ===========================================================================
def _vendor_picker(key_prefix: str) -> dict:
    """Reusable selectbox bound to the vendor master with inline-add expander.
    Returns a dict shaped like a vendor row (or empty dict if none)."""
    vendors = list_vendors()
    if vendors is None or vendors.empty:
        st.info("No vendors yet. Add the first one below.")
        choice = "➕ Add new vendor"
        labels = [choice]
        vmap: dict[str, dict] = {choice: {}}
    else:
        labels = ["➕ Add new vendor"] + [
            f"{r['Vendor_Code']} · {r['Vendor_Name']}"
            for _, r in vendors.iterrows()
        ]
        vmap = {"➕ Add new vendor": {}}
        for _, r in vendors.iterrows():
            vmap[f"{r['Vendor_Code']} · {r['Vendor_Name']}"] = r.to_dict()
    sel = st.selectbox("Vendor", labels, key=f"{key_prefix}_vendor_sel")
    if sel == "➕ Add new vendor":
        with st.expander("➕ Add vendor", expanded=True):
            new_code = st.text_input("Vendor Code *", key=f"{key_prefix}_new_code")
            new_name = st.text_input("Vendor Name *", key=f"{key_prefix}_new_name")
            new_addr = st.text_area("Address", key=f"{key_prefix}_new_addr")
            cc1, cc2 = st.columns(2)
            with cc1:
                new_inco = st.text_input("Default Inco Terms",
                                          key=f"{key_prefix}_new_inco")
            with cc2:
                new_pay  = st.text_input("Default Payment Terms",
                                          key=f"{key_prefix}_new_pay")
            if st.button("Save vendor", key=f"{key_prefix}_new_save"):
                if not new_code or not new_name:
                    st.error("Vendor Code and Name are required.")
                else:
                    ok, msg = add_vendor(
                        vendor_code=new_code, vendor_name=new_name,
                        address=new_addr,
                        default_inco_terms=new_inco,
                        default_payment_terms=new_pay,
                        created_by=st.session_state.get("gi_user", {}).get("username", ""),
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
        return {}
    return vmap.get(sel, {})


def _po_header_form(key_prefix: str, prefill: dict | None = None) -> dict:
    """Render the header inputs and return the dict for create_po_manual()."""
    prefill = prefill or {}
    vend = _vendor_picker(key_prefix)
    inco_default = vend.get("Default_Inco_Terms") or prefill.get("Inco_Terms", "")
    pay_default  = vend.get("Default_Payment_Terms") or prefill.get("Payment_Terms", "")

    c1, c2, c3 = st.columns(3)
    with c1:
        po_no = st.text_input("PO Number *",
                              value=prefill.get("PO_Number", ""),
                              key=f"{key_prefix}_po_no")
        po_date = st.date_input(
            "PO Date",
            value=_safe_date(prefill.get("PO_Date")),
            key=f"{key_prefix}_po_date",
        )
        po_type = st.text_input("PO Type",
                                value=prefill.get("PO_Type", ""),
                                key=f"{key_prefix}_po_type")
    with c2:
        # Round 15 — PR Number is now a dropdown over open PRs (no free-text
        # typo risk). Falls back to manual entry under an expander for the
        # rare legacy case where the PR isn't in the queue yet.
        conn_pr = get_connection()
        try:
            open_prs = pd.read_sql(
                "SELECT DISTINCT PR_Number, COALESCE(Site_ID,'') AS Site_ID "
                "FROM pr_master "
                "WHERE COALESCE(status,'open') = 'open' "
                "ORDER BY PR_Number DESC",
                conn_pr,
            )
        finally:
            conn_pr.close()
        pr_choices = (
            (open_prs["PR_Number"].astype(str) + "  ·  "
             + open_prs["Site_ID"].astype(str)).tolist()
            if not open_prs.empty else []
        )
        prefill_pr = (
            prefill.get("PR_Number")
            or st.session_state.get("_logi_create_po_pr", "")
        )
        default_idx = 0
        if prefill_pr and pr_choices:
            for i, c in enumerate(pr_choices):
                if c.startswith(prefill_pr + "  ·"):
                    default_idx = i + 1  # +1 for the "— select —" sentinel
                    break
        pr_pick = st.selectbox(
            "PR Number",
            ["— Select an open PR —"] + pr_choices,
            index=default_idx,
            key=f"{key_prefix}_pr_pick",
            help="Open PRs only. Use the '➕ Add unlisted PR' expander "
                 "below for legacy / out-of-band PRs.",
        )
        pr_no_picked = (
            pr_pick.split("  ·")[0].strip()
            if pr_pick and pr_pick != "— Select an open PR —"
            else ""
        )
        # Auto-fill Site_ID from the chosen PR.
        if pr_no_picked and not open_prs.empty:
            site_row = open_prs[open_prs["PR_Number"] == pr_no_picked]
            if not site_row.empty:
                st.session_state["_logi_create_po_site"] = (
                    site_row.iloc[0]["Site_ID"] or None
                )
        with st.expander("➕ Add unlisted PR (free text)", expanded=False):
            unlisted = st.text_input(
                "Type a PR number not in the dropdown",
                value="" if pr_no_picked else prefill_pr,
                key=f"{key_prefix}_pr_unlisted",
                placeholder="Used only when the PR isn't already in pr_master",
            ).strip()
        pr_no = pr_no_picked or unlisted

        quot_no = st.text_input("Quotation No.",
                                 value=prefill.get("Quotation_No", ""),
                                 key=f"{key_prefix}_q_no")
        quot_d = st.date_input(
            "Quotation Date",
            value=_safe_date(prefill.get("Quotation_Date")),
            key=f"{key_prefix}_q_date",
        )
    with c3:
        exp_d = st.date_input(
            "Expected Delivery",
            value=_safe_date(prefill.get("Expected_Delivery")),
            key=f"{key_prefix}_exp_d",
        )
        inco = st.text_input("Inco Terms",
                              value=inco_default,
                              key=f"{key_prefix}_inco")
        pay = st.text_input("Payment Terms",
                             value=pay_default,
                             key=f"{key_prefix}_pay")

    c4, c5, c6 = st.columns(3)
    with c4:
        contact = st.text_input("Contact (vendor)",
                                 value=prefill.get("Contact_Person", ""),
                                 key=f"{key_prefix}_contact")
        contact_email = st.text_input("Contact Email",
                                       value=prefill.get("Contact_Email", ""),
                                       key=f"{key_prefix}_contact_email")
    with c5:
        our_ref = st.text_input("Our Reference",
                                 value=prefill.get("Our_Reference", ""),
                                 key=f"{key_prefix}_our_ref")
        your_ref = st.text_input("Your Reference",
                                  value=prefill.get("Your_Reference", ""),
                                  key=f"{key_prefix}_your_ref")
    with c6:
        mobile = st.text_input("Mobile",
                                value=prefill.get("Mobile", ""),
                                key=f"{key_prefix}_mobile")
        our_email = st.text_input("Our Email",
                                   value=prefill.get("Our_Email", ""),
                                   key=f"{key_prefix}_our_email")

    return {
        "PO_Number": po_no.strip(),
        "PR_Number": (pr_no or "").strip() or None,
        "Site_ID": st.session_state.get("_logi_create_po_site"),
        "Vendor_Code":   vend.get("Vendor_Code"),
        "Vendor_Name":   vend.get("Vendor_Name") or prefill.get("Vendor_Name"),
        "Inco_Terms":    inco, "Payment_Terms": pay,
        "PO_Date":       po_date.isoformat() if po_date else None,
        "PO_Type":       po_type or prefill.get("PO_Type"),
        "Quotation_No":  quot_no or None,
        "Quotation_Date": quot_d.isoformat() if quot_d else None,
        "Your_Reference": your_ref or None,
        "Our_Reference":  our_ref or None,
        "Contact_Person": contact or None,
        "Contact_Email":  contact_email or None,
        "Mobile":         mobile or None,
        "Our_Email":      our_email or None,
        "Expected_Delivery": exp_d.isoformat() if exp_d else None,
    }


def _safe_date(v):
    """Parse a date-ish value into datetime.date or return today's date as a
    reasonable default. Streamlit's date_input requires a date or None."""
    if not v:
        return datetime.date.today()
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return datetime.date.today()


def _tab_create_po(user: dict) -> None:
    st.markdown("### 🧾 Create Purchase Order")
    st.caption(
        "Either type the PO header + lines manually, or drop the PO PDF to "
        "auto-extract. Both paths land in the same `purchase_orders` table."
    )

    sub_manual, sub_pdf = st.tabs(["✍️ Manual entry", "📄 PDF upload"])

    # ── Manual ────────────────────────────────────────────────────────────
    with sub_manual:
        # Prefill from "Use this PR" shortcut on tab 1.
        pr_from_tab1 = st.session_state.get("_logi_create_po_pr")
        site_from_tab1 = st.session_state.get("_logi_create_po_site")
        if pr_from_tab1:
            st.info(f"📥 Pulling lines from PR **{pr_from_tab1}** (Site {site_from_tab1}).")

        header = _po_header_form("manual")

        # Build the line items grid
        st.markdown("#### Line items")
        if pr_from_tab1:
            pr_lines = get_pr_lines(pr_from_tab1, site_id=site_from_tab1)
            if not pr_lines.empty:
                pr_lines["Include"] = True
                pr_lines["Unit_Price"]  = 0.0
                pr_lines["Total_Price"] = 0.0
                editable_cols = [
                    "Include", "Material_Code", "Material_Name",
                    "Requested_Qty", "UOM", "Unit_Price", "Total_Price",
                    "WBS_Number", "Network", "Plant",
                ]
                editable_cols = [c for c in editable_cols if c in pr_lines.columns]
                edited = st.data_editor(
                    pr_lines[editable_cols],
                    use_container_width=True,
                    height=320,
                    num_rows="dynamic",
                    key="_logi_po_lines_editor_pr",
                )
            else:
                edited = st.data_editor(
                    pd.DataFrame([{
                        "Include": True, "Material_Code": "", "Description": "",
                        "Requested_Qty": 0.0, "UOM": "EA",
                        "Unit_Price": 0.0, "Total_Price": 0.0,
                        "WBS_Number": "", "Network": "", "Plant": "",
                    }]),
                    use_container_width=True, height=240, num_rows="dynamic",
                    key="_logi_po_lines_editor_blank",
                )
        else:
            edited = st.data_editor(
                pd.DataFrame([{
                    "Include": True, "Material_Code": "", "Description": "",
                    "Qty": 0.0, "UOM": "EA",
                    "Unit_Price": 0.0, "Total_Price": 0.0,
                    "WBS_Number": "", "Network": "", "Plant": "",
                }]),
                use_container_width=True, height=240, num_rows="dynamic",
                key="_logi_po_lines_editor_blank2",
            )

        if st.button("💾 Save PO", type="primary", key="_logi_po_save_manual"):
            if not header["PO_Number"]:
                st.error("PO Number is required.")
            else:
                items = []
                for _, r in edited.iterrows():
                    if "Include" in r and not r.get("Include"):
                        continue
                    qty = (r.get("Requested_Qty") if "Requested_Qty" in r else r.get("Qty")) or 0
                    desc = r.get("Material_Name") or r.get("Description") or ""
                    items.append({
                        "Material_Code": r.get("Material_Code") or "",
                        "Description":   desc,
                        "Qty":           qty,
                        "UOM":           r.get("UOM") or "",
                        "Unit_Price":    r.get("Unit_Price") or 0,
                        "Total_Price":   r.get("Total_Price") or 0,
                        "PR_Number":     header.get("PR_Number"),
                        "WBS_Number":    r.get("WBS_Number"),
                        "Network":       r.get("Network"),
                        "Plant":         r.get("Plant"),
                    })
                if not items:
                    st.error("Add at least one line item (mark Include = true).")
                else:
                    ok, msg = create_po_manual(
                        header=header, items=items,
                        created_by=user["username"],
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.session_state.pop("_logi_create_po_pr", None)
                        st.session_state.pop("_logi_create_po_site", None)
                        st.balloons()

    # ── PDF upload ────────────────────────────────────────────────────────
    with sub_pdf:
        st.caption(
            "Drop the PO PDF. The extractor recognises the General Industries "
            "sample layout — Vendor, Inco/Payment Terms, line items, and the "
            "PO Annexure delivery schedule."
        )
        upl = st.file_uploader(
            "PO PDF", type=["pdf"], key="_logi_po_pdf_upload",
            accept_multiple_files=False,
        )
        c_extract, c_clear = st.columns([1, 1])
        with c_extract:
            extract_clicked = st.button(
                "🔎 Extract from PDF",
                type="primary",
                disabled=upl is None,
                key="_logi_po_extract_btn",
            )
        with c_clear:
            if st.button("Clear", key="_logi_po_pdf_clear"):
                st.session_state.pop("_logi_po_extracted", None)
                st.rerun()

        if extract_clicked and upl is not None:
            pdf_bytes = upl.read()
            ok, msg, extracted = process_po_pdf(
                pdf_bytes,
                pr_number_hint=st.session_state.get("_logi_create_po_pr"),
                site_id_hint=st.session_state.get("_logi_create_po_site"),
                created_by=user["username"],
            )
            (st.success if ok else st.warning)(msg)
            if ok or extracted.get("header"):
                st.session_state["_logi_po_extracted"] = extracted
                st.session_state["_logi_po_pdf_bytes"]  = pdf_bytes
                st.session_state["_logi_po_pdf_name"]   = upl.name
                st.session_state["_logi_po_pdf_mime"]   = upl.type

        extracted = st.session_state.get("_logi_po_extracted")
        if extracted:
            st.markdown("#### Review extracted PO")
            header = _po_header_form("pdf", prefill=extracted.get("header", {}))
            items_df = pd.DataFrame(extracted.get("items", []))
            if items_df.empty:
                items_df = pd.DataFrame([{
                    "Material_Code": "", "Description": "",
                    "Qty": 0.0, "UOM": "EA",
                    "Unit_Price": 0.0, "Total_Price": 0.0,
                }])
            items_df["Include"] = True
            edit_cols = [c for c in [
                "Include", "Material_Code", "Description", "Qty", "UOM",
                "Unit_Price", "Total_Price",
            ] if c in items_df.columns]
            edited = st.data_editor(
                items_df[edit_cols],
                use_container_width=True, height=380, num_rows="dynamic",
                key="_logi_po_pdf_items_editor",
            )

            ships = pd.DataFrame(extracted.get("shipment_schedule", []))
            if not ships.empty:
                st.markdown("#### Delivery schedule (PO Annexure)")
                st.dataframe(ships, use_container_width=True, hide_index=True)

            if st.button("💾 Save PO (from PDF)", type="primary",
                         key="_logi_po_pdf_save"):
                if not header["PO_Number"]:
                    st.error("PO Number is required.")
                else:
                    items = []
                    for _, r in edited.iterrows():
                        if not r.get("Include"):
                            continue
                        items.append({
                            "Material_Code": r.get("Material_Code") or "",
                            "Description":   r.get("Description") or "",
                            "Qty":           r.get("Qty") or 0,
                            "UOM":           r.get("UOM") or "",
                            "Unit_Price":    r.get("Unit_Price") or 0,
                            "Total_Price":   r.get("Total_Price") or 0,
                            "PR_Number":     header.get("PR_Number"),
                        })
                    ok, msg = create_po_manual(
                        header={**header, "source": "pdf_upload"},
                        items=items,
                        shipment_schedule=ships.to_dict("records") if not ships.empty else None,
                        attachment_blob=st.session_state.get("_logi_po_pdf_bytes"),
                        attachment_name=st.session_state.get("_logi_po_pdf_name"),
                        attachment_mime=st.session_state.get("_logi_po_pdf_mime"),
                        created_by=user["username"],
                    )
                    (st.success if ok else st.error)(msg)
                    if ok:
                        for k in ("_logi_po_extracted", "_logi_po_pdf_bytes",
                                  "_logi_po_pdf_name", "_logi_po_pdf_mime",
                                  "_logi_create_po_pr", "_logi_create_po_site"):
                            st.session_state.pop(k, None)
                        st.balloons()


# ===========================================================================
# TAB 3 — Open POs
# ===========================================================================
def _tab_open_pos(user: dict) -> None:
    st.markdown("### 📋 Open Purchase Orders")
    st.caption("Filter, inspect, and drill into POs awaiting delivery.")

    c1, c2, c3 = st.columns(3)
    with c1:
        site_filter = st.selectbox(
            "Site", ["All sites"] + sorted(get_sites() or []),
            key="_logi_po_site",
        )
    with c2:
        vendors_df = list_vendors()
        vendor_options = ["All vendors"] + (
            sorted(vendors_df["Vendor_Code"].tolist()) if not vendors_df.empty else []
        )
        vendor_filter = st.selectbox("Vendor", vendor_options,
                                       key="_logi_po_vendor")
    with c3:
        pr_filter = st.text_input("PR Number (exact)",
                                    key="_logi_po_pr_filter")

    df = list_pos(
        site_id=None if site_filter == "All sites" else site_filter,
        vendor_code=None if vendor_filter == "All vendors" else vendor_filter,
        pr_number=pr_filter.strip() or None,
        open_only=True,
    )
    if df.empty:
        render_empty_state(icon="📦", title="No open POs match the filters")
        return

    # KPI strip
    total_amt = float(df["Total_Amount"].fillna(0).sum() or 0)
    pending = (df["status"] != "delivered").sum()
    _hero_strip([
        _hero("OPEN POs", str(len(df)), "in pipeline", _C["gold"]),
        _hero("VALUE", f"SAR {total_amt:,.0f}", "total committed", _C["sky"]),
        _hero("PENDING", str(int(pending)), "not yet delivered", _C["low"]),
    ])

    render_aggrid(df.rename(columns={
        "PO_Number": "PO No.",
        "PR_Number": "PR No.",
        "Site_ID": "Site",
        "Vendor_Name": "Vendor",
        "PO_Date": "PO Date",
        "Expected_Delivery": "Expected",
        "Total_Amount": "Total (SAR)",
        "status": "Status",
        "source": "Source",
        "line_count": "Lines",
        "total_qty": "Total Qty",
        "delivered_qty": "Delivered Qty",
    }), height=320, key="_logi_po_grid")

    st.divider()
    st.markdown("**Inspect a PO**")
    po_pick = st.selectbox("PO Number", df["PO_Number"].tolist(),
                            key="_logi_po_pick")
    if po_pick:
        detail = get_po_detail(po_pick)
        h = detail["header"]

        body = (
            _kv_row("PR Number", h.get("PR_Number"))
            + _kv_row("Vendor", f"{h.get('Vendor_Code','—')} · {h.get('Vendor_Name','—')}")
            + _kv_row("Inco / Payment", f"{h.get('Inco_Terms','—')} / {h.get('Payment_Terms','—')}")
            + _kv_row("PO Date", h.get("PO_Date"))
            + _kv_row("Expected", h.get("Expected_Delivery"))
            + _kv_row("Source", h.get("source"))
            + _kv_row("Status", h.get("status"))
            + _kv_row("Total", f"SAR {float(h.get('Total_Amount') or 0):,.2f}")
        )
        st.markdown(_section_card(f"PO {po_pick}", body), unsafe_allow_html=True)

        items = detail["items"]
        if not items.empty:
            # RL/BL family chip rendering for visual confirmation of separation.
            def _fam_chip(v):
                if v == "RL":
                    return "🟠 RL"
                if v == "BL":
                    return "🟤 BL"
                return ""
            items_disp = items.copy()
            if "rl_bl_family" in items_disp.columns:
                items_disp["Family"] = items_disp["rl_bl_family"].apply(_fam_chip)
            render_aggrid(items_disp, height=320, key="_logi_po_items_grid")

        ships = detail["shipments"]
        if not ships.empty:
            st.markdown("**Delivery schedule (PO Annexure)**")
            st.dataframe(ships, use_container_width=True, hide_index=True)

        asg = detail["assignments"]
        if not asg.empty:
            st.markdown("**Warehouse assignments**")
            st.dataframe(asg, use_container_width=True, hide_index=True)


# ===========================================================================
# TAB 4 — Assign to Warehouse
# ===========================================================================
def _tab_assign_to_warehouse(user: dict) -> None:
    st.markdown("### 🏭 Assign PO to Warehouse")
    st.caption(
        "Route a PO (or a subset of its items) to a specific warehouse for "
        "physical receipt. The Warehouse Portal will see the assignment "
        "without prices."
    )

    pos = list_pos(open_only=True)
    if pos.empty:
        render_empty_state(icon="📦", title="No open POs to assign")
        return
    whs = list_warehouses()
    if whs.empty:
        st.warning("No active warehouses on file. Admin must add at least one in Admin Portal → Warehouses.")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        po_pick = st.selectbox(
            "PO Number", pos["PO_Number"].tolist(),
            key="_logi_assign_po_pick",
        )
    with c2:
        wh_pick = st.selectbox(
            "Warehouse",
            whs["Warehouse_ID"].tolist(),
            format_func=lambda v: f"{v} · {whs.set_index('Warehouse_ID').loc[v, 'Name']}",
            key="_logi_assign_wh_pick",
        )

    detail = get_po_detail(po_pick)
    items = detail["items"]
    if items.empty:
        st.info("No items on this PO.")
        return

    items_disp = items.copy()
    items_disp["Include"] = True
    edit_cols = [c for c in [
        "Include", "id", "Material_Code", "Description",
        "Qty", "UOM", "rl_bl_family",
    ] if c in items_disp.columns]
    edited = st.data_editor(
        items_disp[edit_cols],
        use_container_width=True, height=320,
        disabled=[c for c in edit_cols if c not in ("Include",)],
        key="_logi_assign_items_editor",
    )

    cA, cB = st.columns([1, 2])
    with cA:
        exp_d = st.date_input(
            "Expected Delivery",
            value=_safe_date(detail["header"].get("Expected_Delivery")),
            key="_logi_assign_exp_d",
        )
    with cB:
        notes = st.text_input("Notes (visible to Warehouse)",
                                key="_logi_assign_notes")

    if st.button("📨 Assign to Warehouse", type="primary",
                 key="_logi_assign_btn"):
        item_ids = [int(r["id"]) for _, r in edited.iterrows() if r.get("Include")]
        if not item_ids:
            st.error("Select at least one line item.")
        else:
            # If all items selected, use None (= 'all items') for cleaner audit.
            subset = None if len(item_ids) == len(items) else item_ids
            ok, msg = assign_po_to_warehouse(
                po_number=po_pick, warehouse_id=wh_pick,
                expected_delivery=exp_d.isoformat() if exp_d else None,
                items_subset_ids=subset, assigned_by=user["username"],
                notes=notes,
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.toast(f"📨 PO {po_pick} → {wh_pick}", icon="🏭")


# ===========================================================================
# TAB 5 — Reschedules
# ===========================================================================
def _tab_reschedules(user: dict) -> None:
    st.markdown("### 🔁 Reschedule Requests")
    st.caption(
        "Incoming reschedule requests from Warehouse / Site HOD. Approving "
        "pushes the new date to the PO + assignment + DN (if linked)."
    )
    pending = list_pending_reschedules()
    if pending.empty:
        render_empty_state(icon="🗓️", title="No pending reschedule requests")
        return

    for _, row in pending.iterrows():
        with st.container(border=True):
            cA, cB = st.columns([3, 1])
            with cA:
                st.markdown(
                    f"**PO {_esc(row['PO_Number'])}** · "
                    f"DN {_esc(row.get('DN_Number'))} · "
                    f"From `{_esc(row.get('current_date'))}` → "
                    f"**`{_esc(row['requested_date'])}`**"
                )
                st.caption(
                    f"Requested by {_esc(row['requested_by'])} "
                    f"({_esc(row['requested_by_role'])}) · "
                    f"{_esc(row['reason'])}"
                )
            with cB:
                with st.popover("Decide", use_container_width=True):
                    notes = st.text_input(
                        "Decision notes",
                        key=f"_logi_resch_notes_{row['id']}",
                    )
                    bA, bB = st.columns(2)
                    with bA:
                        if st.button(
                            "✅ Approve",
                            key=f"_logi_resch_appr_{row['id']}",
                            use_container_width=True, type="primary",
                        ):
                            ok, msg = decide_reschedule(
                                int(row["id"]), approve=True,
                                decided_by=user["username"],
                                decision_notes=notes,
                            )
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    with bB:
                        if st.button(
                            "❌ Reject",
                            key=f"_logi_resch_rej_{row['id']}",
                            use_container_width=True,
                        ):
                            if not notes.strip():
                                st.error("Reason required for rejection.")
                            else:
                                ok, msg = decide_reschedule(
                                    int(row["id"]), approve=False,
                                    decided_by=user["username"],
                                    decision_notes=notes,
                                )
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()


# ===========================================================================
# TAB 6 — Force-Close
# ===========================================================================
def _tab_force_close(user: dict) -> None:
    st.markdown("### 🛑 Force-Close PR / PO / Line")
    st.caption(
        "Use sparingly. Force-closing notifies Admin and the originating Site "
        "HOD immediately. Every closure carries a mandatory reason and is "
        "logged in the audit trail."
    )

    target_type = st.radio(
        "What to close",
        ["PR (entire)", "PO (entire)", "PO line (single item)"],
        horizontal=True,
        key="_logi_fc_type",
    )
    reason = st.text_area(
        "Reason (mandatory, 3+ chars)",
        key="_logi_fc_reason",
        max_chars=400, height=80,
    )

    if target_type == "PR (entire)":
        prs = list_prs_for_logistics(include_history=False)
        if prs.empty:
            st.info("No open PRs in the Logistics queue.")
            return
        pr_pick = st.selectbox(
            "PR Number", prs["PR_Number"].unique().tolist(),
            key="_logi_fc_pr_pick",
        )
        if st.button("🛑 Force-close PR", type="primary",
                     key="_logi_fc_pr_btn"):
            ok, msg = force_close_target(
                "pr", pr_pick, reason, closed_by=user["username"],
            )
            (st.success if ok else st.error)(msg)

    elif target_type == "PO (entire)":
        pos = list_pos(open_only=True)
        if pos.empty:
            st.info("No open POs.")
            return
        po_pick = st.selectbox(
            "PO Number", pos["PO_Number"].tolist(),
            key="_logi_fc_po_pick",
        )
        if st.button("🛑 Force-close PO", type="primary",
                     key="_logi_fc_po_btn"):
            ok, msg = force_close_target(
                "po", po_pick, reason, closed_by=user["username"],
            )
            (st.success if ok else st.error)(msg)

    else:  # PO line
        pos = list_pos(open_only=True)
        if pos.empty:
            st.info("No open POs.")
            return
        po_pick = st.selectbox(
            "PO Number", pos["PO_Number"].tolist(),
            key="_logi_fc_line_po_pick",
        )
        items = get_po_detail(po_pick)["items"]
        if items.empty:
            st.info("This PO has no lines.")
            return
        items_open = items[items["line_status"].isin(
            ["open", "partially_delivered"])]
        if items_open.empty:
            st.info("No open lines on this PO.")
            return
        line_pick = st.selectbox(
            "Line item",
            items_open["id"].tolist(),
            format_func=lambda i: (
                f"#{int(items_open.set_index('id').loc[i, 'line_no'])} "
                f"· {items_open.set_index('id').loc[i, 'Material_Code']} "
                f"· {items_open.set_index('id').loc[i, 'Description'][:40]}"
            ),
            key="_logi_fc_line_pick",
        )
        if st.button("🛑 Force-close line", type="primary",
                     key="_logi_fc_line_btn"):
            ok, msg = force_close_target(
                "po_item", str(int(line_pick)),
                reason, closed_by=user["username"],
            )
            (st.success if ok else st.error)(msg)

    st.divider()
    st.markdown("#### Recent force-closures (audit)")
    fc = list_force_closures()
    if fc.empty:
        st.caption("No force-closures yet.")
    else:
        render_aggrid(fc.head(50), height=260, key="_logi_fc_grid")


# ===========================================================================
# TAB 7 — Vendor Returns
# ===========================================================================
def _tab_vendor_returns(user: dict) -> None:
    st.markdown("### ↩️ Vendor Returns")
    st.caption(
        "Raise a return to the vendor against a PO line, with reason and "
        "expected resupply date. Returning a line reopens the PO so it shows "
        "in the active queue again."
    )

    pos = list_pos(open_only=False)
    if pos.empty:
        st.info("No POs on file.")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        po_pick = st.selectbox(
            "PO Number", pos["PO_Number"].tolist(),
            key="_logi_ret_po_pick",
        )
    items = get_po_detail(po_pick)["items"]
    line_id = None
    if not items.empty:
        with c2:
            scope = st.radio(
                "Return scope",
                ["Whole PO", "Single line"],
                horizontal=True,
                key="_logi_ret_scope",
            )
        if scope == "Single line":
            line_id = st.selectbox(
                "Line",
                items["id"].tolist(),
                format_func=lambda i: (
                    f"#{int(items.set_index('id').loc[i, 'line_no'])} "
                    f"· {items.set_index('id').loc[i, 'Material_Code']} "
                    f"· qty {items.set_index('id').loc[i, 'Qty']}"
                ),
                key="_logi_ret_line_pick",
            )

    qty = st.number_input("Return quantity *", min_value=0.0, step=1.0,
                            key="_logi_ret_qty")
    reason = st.text_area("Reason *", key="_logi_ret_reason",
                            max_chars=400, height=80)
    exp_resupply = st.date_input(
        "Expected resupply",
        value=datetime.date.today() + datetime.timedelta(days=14),
        key="_logi_ret_exp",
    )
    notes = st.text_input("Notes", key="_logi_ret_notes")

    if st.button("↩️ Raise vendor return", type="primary",
                 key="_logi_ret_btn"):
        ok, msg = raise_vendor_return(
            po_number=po_pick,
            po_item_id=int(line_id) if line_id else None,
            dn_number=None,
            qty=float(qty), reason=reason,
            raised_by_role="logistics",
            raised_by=user["username"],
            expected_resupply=exp_resupply.isoformat() if exp_resupply else None,
            notes=notes,
        )
        (st.success if ok else st.error)(msg)

    st.divider()
    st.markdown("#### Open returns")
    rdf = list_vendor_returns(open_only=True)
    if rdf.empty:
        st.caption("No open returns.")
    else:
        render_aggrid(rdf, height=260, key="_logi_ret_grid")


# ===========================================================================
# TAB 8 — Material Details (Round 15)
# ===========================================================================
# Manual entry + Excel upload for the inventory master. Auto-generates SAP
# codes (GI-NNNNNNN) sequentially, auto-assigns Temp-GI-NNNNNNN when the
# Material_Code is blank, and rejects duplicate Material_Codes (configurable
# overwrite via a checkbox). Result lands in the global `inventory` table so
# every other portal picks the rows up immediately.

_MAT_DETAIL_CANONICAL_COLS = [
    ("Material_Code",          "Material Code"),
    ("Equipment_Description",  "Material Description"),
    ("UOM",                    "UoM"),
    ("Category",               "Category"),
    ("Minimum_Qty",            "Min Qty"),
]


def _tab_material_details(user: dict) -> None:
    st.markdown("### 📦 Material Details — master register")
    st.caption(
        "Add materials one-by-one or upload an Excel batch. Material Code is "
        "checked for duplicates; blank ones get a `Temp-GI-…` code. SAP Code "
        "auto-generates from the running sequence."
    )

    from config import MATERIAL_CATEGORIES
    actor = user.get("username", "logistics")

    sub_manual, sub_upload, sub_register = st.tabs([
        "➕ Manual entry", "📤 Excel upload", "📋 Current register",
    ])

    # ── ➕ Manual entry ────────────────────────────────────────────────────
    with sub_manual:
        cA, cB = st.columns(2)
        with cA:
            mat_code = st.text_input(
                "Material Code",
                key="_md_man_code",
                placeholder="Leave blank → Temp-GI-… auto-assigned",
            ).strip()
            desc = st.text_input(
                "Material Description *",
                key="_md_man_desc",
            ).strip()
            uom = st.text_input(
                "UoM *", key="_md_man_uom",
                placeholder="e.g. KG, PCS, M, L",
            ).strip()
        with cB:
            cat = st.selectbox(
                "Category", MATERIAL_CATEGORIES,
                index=MATERIAL_CATEGORIES.index("Others")
                if "Others" in MATERIAL_CATEGORIES else 0,
                key="_md_man_cat",
            )
            min_qty = st.number_input(
                "Minimum Qty (global default)",
                min_value=0.0, step=1.0, value=0.0,
                key="_md_man_minqty",
            )
            st.caption(
                "Per-site overrides can be set later by the HOD of each "
                "site from the HOD Portal."
            )

        if st.button("✅ Add material", type="primary",
                     key="_md_man_submit"):
            if not desc:
                st.error("Material Description is required.")
            elif not uom:
                st.error("UoM is required.")
            else:
                res = bulk_upsert_materials(
                    [{
                        "Material_Code": mat_code,
                        "Equipment_Description": desc,
                        "UOM": uom,
                        "Category": cat,
                        "Minimum_Qty": min_qty,
                    }],
                    created_by=actor,
                )
                if res["inserted"]:
                    new = res["inserted"][0]
                    st.success(
                        f"✅ Added — SAP `{new['SAP_Code']}` · "
                        f"Material `{new['Material_Code']}`."
                    )
                    # Clear the form so the next add starts clean.
                    for k in ("_md_man_code", "_md_man_desc", "_md_man_uom"):
                        st.session_state.pop(k, None)
                elif res["rejected"]:
                    st.error(
                        f"🚫 {res['rejected'][0].get('_reason', 'Rejected')}"
                    )

    # ── 📤 Excel upload ────────────────────────────────────────────────────
    with sub_upload:
        st.caption(
            "Excel column headers (case-insensitive): "
            "**Material_Code, Material_Description, UoM, Category, Minimum_Qty**. "
            "Rows with blank Material_Code receive a Temp-GI code; rows with "
            "duplicate Material_Code are rejected unless you tick "
            "'Overwrite existing'."
        )
        f = st.file_uploader(
            "Upload .xlsx / .xls", type=["xlsx", "xls"],
            key="_md_xl_file",
        )
        overwrite = st.checkbox(
            "Overwrite existing rows on duplicate Material_Code",
            value=False, key="_md_xl_overwrite",
        )

        if f is not None:
            try:
                xl_df = pd.read_excel(f, engine="openpyxl")
            except Exception as e:
                st.error(f"Couldn't read the workbook: {e}")
                return

            # Normalise column names: case-insensitive map onto the
            # canonical names the upserter expects.
            colmap = {}
            for c in xl_df.columns:
                key = str(c).strip().lower().replace(" ", "_")
                if key in ("material_code", "mat_code", "code"):
                    colmap[c] = "Material_Code"
                elif key in ("material_description", "description",
                             "equipment_description", "name"):
                    colmap[c] = "Equipment_Description"
                elif key in ("uom", "uo_m", "unit", "units"):
                    colmap[c] = "UOM"
                elif key in ("category", "cat"):
                    colmap[c] = "Category"
                elif key in ("minimum_qty", "min_qty", "min", "minimum"):
                    colmap[c] = "Minimum_Qty"
            xl_df = xl_df.rename(columns=colmap)

            # Cap preview at 500 rows for sanity.
            preview = xl_df.head(500).fillna("").to_dict(orient="records")
            st.caption(
                f"Parsed {len(xl_df)} row(s) "
                f"(showing first {min(500, len(xl_df))}). Review and confirm."
            )
            st.dataframe(
                xl_df.head(20), use_container_width=True, hide_index=True,
            )

            if st.button("📥 Commit upload", type="primary",
                         key="_md_xl_commit"):
                res = bulk_upsert_materials(
                    preview, created_by=actor,
                    overwrite_duplicates=overwrite,
                )
                cI, cU, cR = st.columns(3)
                cI.metric("Inserted", len(res["inserted"]))
                cU.metric("Updated",  len(res["updated"]))
                cR.metric("Rejected", len(res["rejected"]))
                if res["rejected"]:
                    st.caption("Rejected rows (with reason):")
                    st.dataframe(
                        pd.DataFrame(res["rejected"]),
                        use_container_width=True, hide_index=True,
                    )

    # ── 📋 Current register ────────────────────────────────────────────────
    with sub_register:
        conn = get_connection()
        try:
            inv_df = pd.read_sql(
                "SELECT Material_Code, Equipment_Description, UOM, "
                "       Category, Minimum_Qty, SAP_Code "
                "FROM inventory "
                "ORDER BY CASE WHEN Material_Code LIKE 'Temp-%' THEN 1 ELSE 0 END, "
                "         Material_Code",
                conn,
            )
        finally:
            conn.close()
        if inv_df.empty:
            render_empty_state(
                icon="📭",
                title="No materials yet",
                hint="Use Manual entry or Excel upload to seed the register.",
            )
            return
        # Rename for display per Round 15 spec column order.
        inv_df = inv_df.rename(columns={
            "Equipment_Description": "Material Description",
            "UOM": "UoM",
            "Minimum_Qty": "Min Qty",
            "Material_Code": "Material Code",
            "SAP_Code": "SAP Code",
        })[
            ["Material Code", "Material Description", "UoM",
             "Category", "Min Qty", "SAP Code"]
        ]
        st.dataframe(inv_df, use_container_width=True, hide_index=True)


# ===========================================================================
# TAB 9 — History
# ===========================================================================
def _tab_history(user: dict) -> None:
    st.markdown("### 📂 History — closed PRs / POs")
    st.caption(
        "Read-only archive. Closed PRs / POs are excluded from the active "
        "queue and only show here."
    )

    sub1, sub2 = st.tabs(["Closed POs", "Closed PRs"])
    with sub1:
        pos = list_closed_pos_history()
        if pos.empty:
            st.info("No closed POs yet.")
        else:
            render_aggrid(pos, height=380, key="_logi_hist_pos")
    with sub2:
        prs = list_prs_for_logistics(include_history=True)
        # Exclude active rows (we only want closed / force_closed / in_po-but-PO-closed)
        if not prs.empty:
            prs = prs[prs["pr_status"].isin(["closed"])
                      | prs["logistics_status"].isin(["force_closed", "closed", "in_po"])]
        if prs.empty:
            st.info("No closed PRs yet.")
        else:
            render_aggrid(prs, height=380, key="_logi_hist_prs")


# ===========================================================================
# PAGE ENTRY POINT  (called from main.py)
# ===========================================================================
def page_logistics_portal(user: dict) -> None:
    """Entry point. Receives the auth user dict from main.py."""
    render_brand_header("Logistics & Procurement Portal")

    # Inline page title
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:6px;'>"
        f"<span style='font-size:22px;'>🚚</span>"
        f"<span style='color:{BRAND_GOLD};font-weight:800;font-size:20px;'>"
        f"Logistics Portal</span></div>",
        unsafe_allow_html=True,
    )

    tab_labels = [
        "📥 Incoming PRs",
        "🧾 Create PO",
        "📋 Open POs",
        "🏭 Assign to Warehouse",
        "🔁 Reschedules",
        "🛑 Force-Close",
        "↩️ Vendor Returns",
        "📦 Material Details",   # Round 15 — material master entry / Excel upload
        "📂 History",
    ]
    t1, t2, t3, t4, t5, t6, t7, t8, t9 = st.tabs(tab_labels)
    with t1: _tab_incoming_prs(user)
    with t2: _tab_create_po(user)
    with t3: _tab_open_pos(user)
    with t4: _tab_assign_to_warehouse(user)
    with t5: _tab_reschedules(user)
    with t6: _tab_force_close(user)
    with t7: _tab_vendor_returns(user)
    with t8: _tab_material_details(user)
    with t9: _tab_history(user)
