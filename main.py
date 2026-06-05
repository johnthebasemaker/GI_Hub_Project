"""
main.py — General Industries Lightning Hub v2.0
================================================
Enterprise entry point. Run with:  streamlit run main.py

Architecture:
  main.py          → page routing + RBAC gate
  database.py      → all SQL queries, schema init, EOD commit
  auth.py          → bcrypt login, session state, user management UI
  ui_components.py → AgGrid, Plotly charts, branded widgets
  config.py        → constants, roles, brand colours
  mailer.py        → COMING in Module 4
"""

import streamlit as st
import pandas as pd
import datetime

# ── Project modules ──────────────────────────────────────────────────────────
from config import (
    APP_NAME, APP_ICON, APP_VERSION,
    BRAND_GOLD, BRAND_BLUE, TEXT_MUTED,
    SYSTEM_COLS, OPTIONAL_ISSUE_COLS,
    PAGE_ACCESS, ROLE_HIERARCHY, ROLES,
    AGGRID_HEIGHT,
)
from database import (
    get_connection, init_db,
    get_work_types, load_live_inventory,
    commit_eod, get_low_stock_items,
    get_sites, get_pending_issues_for_site,
    get_pending_requests, create_request, update_request_status,
    get_short_dated_stock, process_pr_pdf, process_receipt_delivery,
    get_burn_rate_and_forecast,
    get_pending_receipts_for_hod, commit_pending_receipts,
    get_returnable_items, insert_returnable_item,
    mark_item_returned, get_overdue_unreported_items,
    queue_whatsapp_alert, log_audit_action,
    get_phone_by_username,
)
from auth import (
    seed_default_users,
    login_form, get_current_user, logout,
    render_user_management_tab,
)
from ui_components import (
    inject_custom_css, render_brand_header,
    render_aggrid, render_top_consumed_bar, render_stock_vs_minimum_bar,
    render_low_stock_sidebar_badge, render_barcode_scanner,
    render_burn_rate_chart, render_burn_alert_banner,
)
from mailer import (
    build_daily_report, build_monthly_report, build_low_stock_report,
    send_eod_report, parse_recipients, get_default_recipients,
)

# ===========================================================================
# PAGE CONFIG  (must be the very first Streamlit call)
# ===========================================================================
st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_custom_css()

# ===========================================================================
# DATABASE INIT + SEED DEFAULT USERS
# ===========================================================================
init_db()             # Creates all tables, self-heals schema
seed_default_users()  # Seeds admin/supervisor/worker if users table is empty


# ===========================================================================
# AUTH GATE  — unauthenticated users see ONLY the login screen
# ===========================================================================
def _require_login() -> dict:
    user = get_current_user()
    if user is None:
        login_form()
        st.stop()
    return user

def _can_access(role: str, page: str) -> bool:
    required = PAGE_ACCESS.get(page, "admin")
    return ROLE_HIERARCHY.get(role, -1) >= ROLE_HIERARCHY.get(required, 99)


# ===========================================================================
# SIDEBAR
# ===========================================================================
def render_sidebar(user: dict) -> str:
    role     = user["role"]
    username = user["username"]

    with st.sidebar:
        st.markdown(
            f"<div style='text-align:center; padding:0.5rem 0;'>"
            f"<span style='font-size:1.5rem;'>{APP_ICON}</span> "
            f"<span style='color:{BRAND_GOLD}; font-weight:700;'>GI Hub</span> "
            f"<span style='color:{TEXT_MUTED}; font-size:0.7rem;'>v{APP_VERSION}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        role_meta = ROLES.get(role, {"icon": "?", "label": role, "color": TEXT_MUTED})
        st.markdown(
            f"<div style='background:{role_meta['color']}22; border:1px solid {role_meta['color']}55;"
            f"border-radius:8px; padding:0.5rem 0.75rem; margin-bottom:0.75rem;'>"
            f"<span style='font-size:1rem;'>{role_meta['icon']}</span> "
            f"<strong style='color:{role_meta['color']};'>{username}</strong><br>"
            f"<span style='color:{TEXT_MUTED}; font-size:0.75rem;'>{role_meta['label']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Build the list of allowed pages
        visible_pages = []
        for p in PAGE_ACCESS:
            if _can_access(role, p):
                # Hide HOD Portal from Admin (Admin uses the Admin Portal)
                if p == "📋 HOD Portal" and role == "admin":
                    continue
                visible_pages.append(p)
                
        page = st.radio("Navigate to:", visible_pages, key="nav_radio")
        st.divider()

        if role in ("supervisor", "admin"):
            st.markdown("**📊 Inventory Alerts**")
            try:
                low_df = get_low_stock_items()
                render_low_stock_sidebar_badge(low_df)
            except Exception:
                st.caption("Could not load stock alerts.")
        st.divider()

        if st.button("🚪 Sign Out", width="stretch"):
            # 📝 AUDIT LOG INJECTION
            from database import log_audit_action
            log_audit_action(username, "LOGOUT", "System", "User explicitly signed out")
            
            logout()

    return page


# ===========================================================================
# PAGE 1: LIVE DASHBOARD
# ===========================================================================
def page_live_dashboard() -> None:
    render_brand_header("Live Warehouse Stock Dashboard")
    st.title("📦 Live Inventory Dashboard")

    conn = get_connection()
    live_df = load_live_inventory(conn)
    forecast_df = get_burn_rate_and_forecast(conn)
    conn.close()

    if live_df.empty:
        st.warning("No inventory data found. Please add items via the Admin Portal first.")
        return

    render_burn_alert_banner(forecast_df)
    st.divider()

    display_cols = [c for c in [
        "SAP_Code", "Equipment_Description", "UOM",
        "Total_Returned", "Current_Stock", "Minimum_Qty",
    ] if c in live_df.columns]
    render_aggrid(live_df[display_cols].copy(), key="dashboard_grid", height=AGGRID_HEIGHT)

    st.divider()
    with st.expander("📉 Stock vs Minimum Threshold", expanded=False):
        render_stock_vs_minimum_bar(live_df)

    with st.expander("🔥 Burn Rate Forecast (30-Day)", expanded=True):
        render_burn_rate_chart(forecast_df)

    with st.expander("📊 Top Consumed Items", expanded=False):
        render_top_consumed_bar(live_df)


# ===========================================================================
# PAGE 2: ENTRY LOG
# ===========================================================================
def page_daily_issue_log(user: dict) -> None:
    render_brand_header("Entry Log")
    st.title("📝 Entry Log")

    site_id    = user.get("site_id", "HQ")
    work_types = get_work_types()

    # Shared inventory list used by the Issue and Receipt tabs
    conn = get_connection()
    try:
        inv_list = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, Material_Code, UOM FROM inventory", conn
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
    form_cols = [
        row[1] for row in c.fetchall()
        if row[1] not in (SYSTEM_COLS | {"SAP_Code"})
    ]
    conn.close()

    tab_issue, tab_receipt_stage, tab_returnables = st.tabs([
        "📋 Consumption Log", "📦 Receipt Staging", "🔄 Returnable Items"
    ])

    # ── TAB 1: Daily Issue Log ─────────────────────────────────────────────────
    with tab_issue:
        with st.expander("➕ Scan / Add New Item to Queue", expanded=True):
            with st.expander("📷 Barcode / QR Scanner (mobile camera)", expanded=False):
                scanned = render_barcode_scanner(input_key="issue_barcode")

            st.write("**1. Select Material**")
            preselect_idx = None
            if scanned and not inv_list.empty:
                matches = [i for i, s in enumerate(search_options) if scanned in s]
                if matches:
                    preselect_idx = matches[0]

            selected_item = st.selectbox(
                "Search by SAP Code or Description",
                options=search_options,
                index=preselect_idx,
                placeholder="Start typing… e.g. 'Tank' or '1001'",
                key="item_selectbox",
            )

            sap_code = None
            if selected_item:
                sap_code = selected_item.split("]")[0].replace("[", "").strip()
                if not inv_list.empty:
                    match = inv_list[inv_list["SAP_Code"] == sap_code]
                    if not match.empty:
                        item_details = match.iloc[0]
                        st.info(
                            f"📋 **Mat Code:** {item_details.get('Material_Code','N/A')} "
                            f"| **UOM:** {item_details.get('UOM','N/A')}"
                        )

            st.write("**2. Fill Entry Details**")
            input_data = {}
            n_cols = 2 if len(form_cols) <= 4 else 3
            cols = st.columns(n_cols)
            for i, col_name in enumerate(form_cols):
                with cols[i % n_cols]:
                    if col_name == "Date":
                        input_data[col_name] = st.date_input(f"{col_name}*", datetime.date.today())
                    elif "qty" in col_name.lower() or "quantity" in col_name.lower():
                        input_data[col_name] = st.number_input(f"{col_name}*", min_value=0.1, step=1.0)
                    elif col_name == "Work_Type":
                        input_data[col_name] = st.selectbox(f"{col_name}*", work_types)
                    elif col_name in OPTIONAL_ISSUE_COLS:
                        input_data[col_name] = st.text_input(f"{col_name} (Optional)")
                    else:
                        input_data[col_name] = st.text_input(f"{col_name}*")

            st.divider()
            override_expiry = st.checkbox("⚠️ Override Expiry Warning (I confirm the expiring batch has been pulled first)")
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
                if True:
                    conn2 = get_connection()
                    input_data["Site_ID"] = site_id
                    input_data["status"]  = "draft"

                    if not override_expiry:
                        expiry_df = get_short_dated_stock(conn2, site_id=site_id)
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
                    st.success("✅ Added to staging queue!")
                    st.rerun()

        st.subheader("📋 Staging Queue")
        conn3 = get_connection()
        pending_df = pd.read_sql("""
            SELECT p.id, p.Date, p.SAP_Code,
                   i.Equipment_Description AS Material_Name, i.UOM, p.*
            FROM pending_issues p
            LEFT JOIN inventory i ON p.SAP_Code = i.SAP_Code
            WHERE COALESCE(p.Site_ID,'HQ') = ? AND COALESCE(p.status,'draft') = 'draft'
        """, conn3, params=(site_id,))
        pending_df = pending_df.loc[:, ~pending_df.columns.duplicated()]

        if pending_df.empty:
            st.info("No items in the staging queue yet.")
        else:
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

            btn_save, btn_submit = st.columns([1, 2])
            with btn_save:
                if st.button("💾 Save Draft Edits", use_container_width=True):
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
                    st.success("Draft queue saved.")
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
                        conn3.execute(
                            "UPDATE pending_issues SET status = 'pending_hod' WHERE COALESCE(Site_ID,'HQ') = ? AND COALESCE(status,'draft') = 'draft'",
                            (site_id,)
                        )
                        conn3.commit()

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
                        st.rerun()

        conn3.close()

    # ── TAB 2: Receipt Staging ─────────────────────────────────────────────────
    with tab_receipt_stage:
        st.subheader("📦 Stage Inbound Receipts")
        st.caption("Add received materials to the draft queue, then submit to HOD for approval.")

        # Discover receipt form columns dynamically — mirrors consumption log pattern
        # Open a fresh connection: the shared `conn` was closed after tab setup above.
        _pragma_conn = get_connection()
        _rcpt_all_cols = _pragma_conn.execute("PRAGMA table_info(pending_receipts)").fetchall()
        _pragma_conn.close()
        rcpt_form_cols = [
            row[1] for row in _rcpt_all_cols
            if row[1] not in (SYSTEM_COLS | {"SAP_Code"})
        ]
        OPTIONAL_RECEIPT_COLS = {
            "Supplier", "Remarks", "PR_Number", "Expiry_Date",
            "Serial_No", "PR", "Vehicle_No", "Driver_Name", "DN_No",
            "Pallet_No", "Mob_From", "Prepared_by", "Mob_To", "DN_Copy",
        }

        with st.expander("➕ Add Receipt to Queue", expanded=True):
            st.write("**1. Select Material**")
            sel_rcpt_item = st.selectbox(
                "Search by SAP Code or Description",
                options=search_options,
                index=None,
                placeholder="Start typing…",
                key="rcpt_item_selectbox",
            )
            rcpt_sap = None
            if sel_rcpt_item:
                rcpt_sap = sel_rcpt_item.split("]")[0].replace("[", "").strip()
                if not inv_list.empty:
                    m = inv_list[inv_list["SAP_Code"] == rcpt_sap]
                    if not m.empty:
                        st.info(
                            f"📋 **Mat Code:** {m.iloc[0].get('Material_Code','N/A')} "
                            f"| **UOM:** {m.iloc[0].get('UOM','N/A')}"
                        )

            st.write("**2. Fill Receipt Details**")
            rcpt_input = {}
            rn_cols = 2 if len(rcpt_form_cols) <= 4 else 3
            r_cols = st.columns(rn_cols)
            for i, col_name in enumerate(rcpt_form_cols):
                with r_cols[i % rn_cols]:
                    if col_name == "Date":
                        rcpt_input[col_name] = st.date_input(f"{col_name}*", datetime.date.today(), key=f"rcpt_{col_name}")
                    elif "qty" in col_name.lower() or "quantity" in col_name.lower():
                        rcpt_input[col_name] = st.number_input(f"{col_name}*", min_value=0.1, step=1.0, key=f"rcpt_{col_name}")
                    elif col_name == "Expiry_Date":
                        rcpt_input[col_name] = st.date_input(f"{col_name} (Optional)", value=None, key=f"rcpt_{col_name}")
                    elif col_name in OPTIONAL_RECEIPT_COLS:
                        rcpt_input[col_name] = st.text_input(f"{col_name} (Optional)", key=f"rcpt_{col_name}")
                    else:
                        rcpt_input[col_name] = st.text_input(f"{col_name}*", key=f"rcpt_{col_name}")

            if st.button("Add to Receipt Queue ⬇️", type="primary", key="rcpt_add_btn"):
                if not rcpt_sap:
                    st.error("⚠️ Please select a material from the search box. This field is mandatory.")
                    st.stop()
                rcpt_missing = [
                    col for col, val in rcpt_input.items()
                    if col not in OPTIONAL_RECEIPT_COLS
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
                conn_ra.execute(
                    f"INSERT INTO pending_receipts ({', '.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals,
                )
                conn_ra.commit()
                conn_ra.close()
                st.success("✅ Added to receipt queue!")
                st.rerun()

        st.subheader("📋 Receipt Draft Queue")
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

        if rcpt_draft_df.empty:
            st.info("No receipts in the draft queue yet.")
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

            rbtn_save, rbtn_submit = st.columns([1, 2])
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
                    st.success("Receipt draft saved.")
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
                        st.rerun()

        conn_rq.close()

    # ── TAB 3: Returnable Items ────────────────────────────────────────────────
    with tab_returnables:
        st.subheader("🔄 Returnable Items — Tool Tracking")
        st.caption("Log items temporarily given to personnel and track when they are due back.")

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
                st.success(f"✅ '{ri_material}' issued to {ri_borrower}.")
                st.rerun()

        st.subheader("📋 Currently Borrowed Items")
        conn_ri = get_connection()
        ri_df = get_returnable_items(conn_ri, site_id=site_id)
        conn_ri.close()

        borrowed_df = ri_df[ri_df["status"] == "borrowed"].copy()

        if borrowed_df.empty:
            st.success("✅ No items currently on loan.")
        else:
            now = pd.Timestamp.now()
            borrowed_df["expected_return_time"] = pd.to_datetime(borrowed_df["expected_return_time"], errors="coerce")
            borrowed_df["⚠️ Overdue"] = borrowed_df["expected_return_time"] < now

            st.dataframe(
                borrowed_df[["id", "material_name", "uom", "qty",
                             "borrower_name", "borrower_phone",
                             "given_time", "expected_return_time", "⚠️ Overdue"]],
                hide_index=True, use_container_width=True,
            )

            st.divider()
            st.write("**Mark an item as returned:**")
            item_options = {
                f"[#{r['id']}] {r['material_name']} — {r['borrower_name']}": r["id"]
                for _, r in borrowed_df.iterrows()
            }
            selected_label = st.selectbox("Select item", list(item_options.keys()), key="ri_return_select")
            if st.button("✅ Mark as Returned", key="ri_return_btn"):
                mark_item_returned(item_id=item_options[selected_label])
                st.success("Item marked as returned.")
                st.rerun()

# ===========================================================================
# PAGE 3: HOD PORTAL (hod + admin)
# ===========================================================================
def page_hod_portal(user: dict) -> None:
    render_brand_header("Head of Department Portal")
    st.title("🏛️ HOD Portal")
    
    site_id = user.get("site_id", "HQ")
    st.caption(f"Managing Site: **{site_id}**")

    tab_eod, tab_inquiry, tab_my_reqs, tab_shelf, tab_pr, tab_receive, tab_burn, tab_pending_rcpt = st.tabs([
        "🚀 EOD Commit", "🔍 Cross-Site Inquiry", "✅ My Requests",
        "🕒 Shelf-Life Alerts", "📄 Site PRs", "📥 Receive Material", "🔥 Burn Rate",
        "📬 Pending Receipts",
    ])

    # ... (Keep Tab 1, Tab 2, and Tab 3 exactly as they are) ...

    # ── TAB 4: Shelf-Life Alerts (Module 6) ──────────────────────────────────
    with tab_shelf:
        st.subheader("⚠️ Priority Consumption Board")
        st.markdown("Items requiring immediate attention to prevent expiration waste.")
        
        conn = get_connection()
        shelf_df = get_short_dated_stock(conn, site_id)
        conn.close()

        if shelf_df.empty:
            st.success("✅ No expiring or short-dated stock at your site!")
        else:
            # Display Red/Amber status visually
            st.dataframe(
                shelf_df,
                width="stretch",
                hide_index=True,
            )

    # ── TAB 5: Upload Purchase Request (Module 6) ────────────────────────────
    with tab_pr:
        st.subheader("📄 Site Purchase Requests (PR)")
        
        # --- NEW: HOD PR Visibility ---
        conn = get_connection()
        # Upgraded query to dynamically calculate Pending Quantity
        my_prs = pd.read_sql("""
            SELECT 
                p.PR_Number, p.SAP_Code, p.Material_Code, p.Material_Name, 
                p.Requested_Qty,
                (p.Requested_Qty - COALESCE((
                    SELECT SUM(Quantity) FROM receipts 
                    WHERE PR_Number = p.PR_Number AND SAP_Code = p.SAP_Code AND Site_ID = p.Site_ID
                ), 0)) AS Pending_Qty,
                p.status, p.created_at
            FROM pr_master p
            WHERE p.Site_ID = ? 
            ORDER BY p.created_at DESC
        """, conn, params=(site_id,))
        conn.close()
        
        if my_prs.empty:
            st.info("No Purchase Requests found for your site.")
        else:
            st.write(f"**Current PRs for {site_id}**")
            # Highlight Open vs Closed status
            st.dataframe(my_prs, width="stretch", hide_index=True)
            
            # --- NEW: Logistics Email Dispatcher ---
            st.write("---")
            st.markdown("**📧 Notify Logistics (Pending Balance Follow-up)**")
            
            # Only allow emailing about PRs that are still open
            open_prs_only = my_prs[my_prs["status"] == "open"]["PR_Number"].unique()
            
            if len(open_prs_only) > 0:
                # Upgraded to 3 columns to fit the PDF button
                col_a, col_b, col_c = st.columns([2, 1, 1])
                with col_a:
                    pr_to_email = st.selectbox("Select PR for Actions:", open_prs_only, key="email_pr_select")
                
                with col_b:
                    st.write("") # Spacing alignment
                    st.write("")
                    if st.button("📧 Draft Outlook Email", type="secondary", width="stretch"):
                        pr_data = my_prs[my_prs["PR_Number"] == pr_to_email]
                        from mailer import draft_logistics_email_via_outlook
                        ok, msg = draft_logistics_email_via_outlook(pr_to_email, site_id, pr_data)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)
                            
                with col_c:
                    st.write("") # Spacing alignment
                    st.write("")
                    # --- NEW: PDF GENERATOR ---
                    pr_data = my_prs[my_prs["PR_Number"] == pr_to_email]
                    from reports import generate_pr_pdf
                    pdf_bytes = generate_pr_pdf(pr_to_email, site_id, pr_data, generated_by=user["username"])
                    
                    st.download_button(
                        label="📥 Download PR PDF",
                        data=pdf_bytes,
                        file_name=f"PR_{pr_to_email}_{site_id}_Record.pdf",
                        mime="application/pdf",
                        type="primary",
                        width="stretch"
                    )
                    # 📝 AUDIT LOG INJECTION
                    # (Streamlit triggers downloads instantly, so we log it silently when the button renders)
            else:
                st.success("All PRs for your site are currently fulfilled and closed!")
            
        st.divider()
        # ------------------------------

        st.markdown("**Upload New PR PDF**")
        st.markdown("The system will automatically extract the PR Number and match materials to the Master Inventory.")
        
        uploaded_file = st.file_uploader("Select PR PDF", type=["pdf"])
        
        if uploaded_file is not None:
            if st.button("Processing Upload...", type="primary"):
                with st.spinner("Extracting tables and fuzzy-matching SAP Codes..."):
                    pdf_bytes = uploaded_file.read()
                    conn = get_connection()
                    success, msg = process_pr_pdf(pdf_bytes, site_id, conn)
                    conn.close()
                    
                    if success:
                        if "WARNING" in msg:
                            st.warning(msg) # Shows yellow if some items were skipped
                        else:
                            st.success(msg) # Shows green if 100% matched
                    else:
                        st.error(msg)

    # ── TAB 1: EOD Commit (Moved from Admin) ─────────────────────────────────
    with tab_eod:
        st.subheader(f"End-of-Day Review & Commit ({site_id})")
        conn = get_connection()
        # Admins get 'God View' of all staging items. HODs only see their own site.
        if user["role"] == "admin":
            # Admins get god-view; store_keepers/supervisors only see their own site
            pending_df = get_pending_issues_for_site(conn, user.get("site_id", "HQ"))
        else:
            pending_df = get_pending_issues_for_site(conn, site_id)

        if pending_df.empty:
            st.info("No items pending in your site's staging queue.")
        else:
            work_types = get_work_types()
            admin_col_cfg = {
                "id": None, "Timestamp": None, "Site_ID": None,
                "Work_Type": st.column_config.SelectboxColumn("Work Type", options=work_types),
            }
            edited_admin_df = st.data_editor(
                pending_df, column_config=admin_col_cfg,
                num_rows="dynamic", width="stretch", key="hod_eod_editor",
            )
            btn1, btn2 = st.columns(2)
            with btn1:
                if st.button("💾 Save Edits", width="stretch"):
                    c = conn.cursor()
                    c.execute("DELETE FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ?", (site_id,))
                    edited_admin_df.to_sql("pending_issues", conn, if_exists="append", index=False)
                    conn.commit()
                    st.success("Edits saved!")
                    st.rerun()
            with btn2:
                if st.button("🚀 COMMIT SITE LOG TO MASTER", type="primary", width="stretch"):
                    c = conn.cursor()
                    c.execute("DELETE FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ? AND COALESCE(status,'pending_hod') = 'pending_hod'", (site_id,))
                    edited_admin_df.to_sql("pending_issues", conn, if_exists="append", index=False)
                    conn.commit()
                    n = commit_eod(conn) 
                    
                    # --- 📱 WHATSAPP AUTOMATION INJECTION ---
                    # Check if this EOD commit caused any items to drop below minimum
                    low_df = get_low_stock_items(conn)
                    if not low_df.empty:
                        hod_phone = get_phone_by_username(user["username"])
                        if hod_phone:
                            critical_count = len(low_df[low_df["Current_Stock"] <= 0])
                            warning_count = len(low_df) - critical_count
                            
                            alert_msg = f"🚨 *POST-EOD STOCK ALERT ({site_id})*\nCommit successful, but your inventory has dropped below safe levels:\n\n🔴 {critical_count} Critical (Empty/Negative)\n🟡 {warning_count} Low Stock\n\nPlease check the Live Dashboard."
                            queue_whatsapp_alert(hod_phone, alert_msg)
                    # ----------------------------------------
                    
                    st.balloons()
                    st.success(f"✅ {n} record(s) committed to Master Database!")
                    st.rerun()
        conn.close()

    # ── TAB 2: Cross-Site Inquiry ────────────────────────────────────────────
    with tab_inquiry:
        if "inquiry_cart" not in st.session_state:
            st.session_state["inquiry_cart"] = []

        st.subheader("Request Material From Another Branch")
        conn = get_connection()
        
        col1, col2 = st.columns(2)
        with col1:
            all_sites = get_sites(conn)
            other_sites = [s for s in all_sites if s != site_id]
            target_site = st.selectbox("Select Target Branch:", other_sites)
            
            # Fetch global inventory to pick an item
            inv_df = pd.read_sql("SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn)
            inv_df["Search_String"] = "[" + inv_df["SAP_Code"].astype(str) + "] " + inv_df["Equipment_Description"].astype(str)
            item_selection = st.selectbox("Select Material:", inv_df["Search_String"].tolist())
            
            req_qty = st.number_input("Quantity Needed:", min_value=1.0, step=1.0)
            notes = st.text_area("Justification / Notes:")

        with col2:
            if target_site and item_selection:
                sap_code = item_selection.split("]")[0].replace("[", "").strip()
                
                # Check live stock at the target site
                live_target_df = load_live_inventory(conn, site_id=target_site)
                match = live_target_df[live_target_df["SAP_Code"] == sap_code]
                
                avail_qty = float(match.iloc[0]["Current_Stock"]) if not match.empty else 0.0
                suggested = min(req_qty, avail_qty)

                st.markdown(f"### 📊 Live Stock at **{target_site}**")
                st.metric("Available Quantity", f"{avail_qty}")
                st.metric("Suggested Transfer Qty", f"{suggested}", delta="Based on availability" if avail_qty > 0 else "Out of stock", delta_color="normal" if avail_qty > 0 else "inverse")

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
                        st.success(f"Added to cart. {len(st.session_state['inquiry_cart'])} item(s) in cart.")
                        st.rerun()

        if st.session_state["inquiry_cart"]:
            st.write("---")
            st.markdown("### 🛒 Your Request Cart")

            display_cols = ["Target Site", "SAP Code", "Description", "Qty", "Notes"]
            cart_display_df = pd.DataFrame(st.session_state["inquiry_cart"])[display_cols]
            st.dataframe(cart_display_df, use_container_width=True, hide_index=True)

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
                    # ── Goal 1: Notify ALL Admin accounts — creation trigger ──
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
                    # ──────────────────────────────────────────────────────────

                    st.session_state["inquiry_cart"] = []
                    st.success(f"✅ {count} request(s) submitted to Admin.")
                    st.rerun()
            with col_clear:
                if st.button("🗑️ Clear List", use_container_width=True):
                    st.session_state["inquiry_cart"] = []
                    st.rerun()

        conn.close()

    # ── TAB 3: My Requests ───────────────────────────────────────────────────
    with tab_my_reqs:
        st.subheader("My Outbound Requests")
        conn = get_connection()
        reqs_df = get_pending_requests(conn, site_id=site_id)
        
        if reqs_df.empty:
            st.info("You have no material requests.")
        else:
            display_cols = ["id", "target_site", "SAP_Code", "requested_qty", "status", "created_at"]
            st.dataframe(reqs_df[display_cols], width="stretch")
            
            # Simple Fulfillment Button for approved requests
            approved_df = reqs_df[reqs_df["status"] == "approved"]
            if not approved_df.empty:
                st.write("---")
                st.write("**📦 Mark Incoming Transfers as Received:**")
                req_to_fulfill = st.selectbox("Select Approved Request:", approved_df["id"].tolist())
                if st.button("Confirm Delivery Received", type="primary"):
                    update_request_status(conn, req_to_fulfill, "fulfilled", user["username"], "Delivery received at site")
                    st.success("Inventory Transfer Complete!")
                    st.rerun()
        conn.close()
    
    # ── TAB 6: Receive Material (Module 6) ───────────────────────────────────
    with tab_receive:
        st.subheader("📥 Receive Material (Incoming Deliveries)")
        st.markdown("Log shipments arriving at your site. If linked to a PR, the system automatically tracks fulfillment.")

        conn = get_connection()
        
        # 1. DYNAMIC DROPDOWN: Select PR (Outside the form to trigger live updates)
        open_prs = pd.read_sql("SELECT DISTINCT PR_Number FROM pr_master WHERE Site_ID = ? AND status = 'open'", conn, params=(site_id,))
        pr_options = ["-- None (Direct Purchase) --"] + open_prs["PR_Number"].tolist()
        
        selected_pr = st.selectbox("🔗 Link to Open PR (Filters Material List)", pr_options, key="hod_pr_select")
        
        # 2. DYNAMIC FILTER: Load Materials based on PR selection
        if selected_pr == "-- None (Direct Purchase) --":
            inv_list_db = pd.read_sql("SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn)
        else:
            inv_list_db = pd.read_sql("""
                SELECT i.SAP_Code, i.Equipment_Description, i.UOM 
                FROM pr_master p
                JOIN inventory i ON p.SAP_Code = i.SAP_Code
                WHERE p.PR_Number = ? AND p.Site_ID = ?
            """, conn, params=(selected_pr, site_id))
            
        if not inv_list_db.empty:
            inv_list_db["Search_String"] = "[" + inv_list_db["SAP_Code"].astype(str) + "] " + inv_list_db["Equipment_Description"].astype(str)
            material_options = inv_list_db["Search_String"].tolist()
        else:
            material_options = []

        # 3. RECEIPT FORM
        _RECEIPT_SPECIAL = {
            "id", "Timestamp", "Date", "SAP_Code", "Quantity",
            "Site_ID", "Expiry_Date", "PR_Number", "status",
        }
        _rc = conn.cursor()
        _rc.execute("PRAGMA table_info(receipts)")
        receipt_extra_cols = [row[1] for row in _rc.fetchall() if row[1] not in _RECEIPT_SPECIAL]

        with st.form("hod_receive_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                sel_item = st.selectbox("Select Material*", material_options, index=None)
                qty = st.number_input("Quantity Received*", min_value=0.1, step=1.0)
                date_val = st.date_input("Delivery Date*", datetime.date.today())
            with col2:
                exp_date = st.date_input("Expiry Date (Optional)", value=None)
                receipt_extra_vals = {}
                for _col in receipt_extra_cols:
                    _optional = _col in {"Remarks", "Supplier"}
                    _label = f"{_col} (Optional)" if _optional else f"{_col}*"
                    receipt_extra_vals[_col] = st.text_input(_label)

            if st.form_submit_button("💾 Save Receipt", type="primary"):
                if not sel_item:
                    st.error("⚠️ Please select a material.")
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
                        log_audit_action(user["username"], "RECEIVE_MATERIAL", "receipts", f"Received qty: {qty} of SAP: {sap_code} at {site_id}")
                        
                        st.success(msg)
                    else:
                        st.error(msg)
        conn.close()

    # ── TAB 7: Burn Rate Forecast (Predictive Analytics) ─────────────────────
    with tab_burn:
        st.subheader("🔥 Burn Rate Forecast — Predictive Analytics")
        st.markdown(
            "Projected stock depletion based on the last **30 days** of consumption. "
            "Items marked critical will run out within **7 days**."
        )

        conn = get_connection()
        forecast_df = get_burn_rate_and_forecast(conn, site_id=site_id)
        conn.close()

        render_burn_alert_banner(forecast_df)
        render_burn_rate_chart(forecast_df)

        if not forecast_df.empty:
            st.subheader("Detailed Forecast Table")
            detail_cols = [c for c in [
                "SAP_Code", "Equipment_Description", "UOM",
                "Current_Stock", "Daily_Burn_Rate", "Days_Remaining",
            ] if c in forecast_df.columns]
            render_aggrid(forecast_df[detail_cols].copy(), key="burn_rate_grid", height=AGGRID_HEIGHT)

    # ── TAB 8: Pending Receipts (HOD Approval) ────────────────────────────────
    with tab_pending_rcpt:
        st.subheader("📬 Pending Receipts — Awaiting Approval")
        st.markdown(
            "These receipts were staged by the Store Keeper and are pending your review. "
            "Committing them will move them into the permanent **receipts** table and update live inventory."
        )

        conn_pr = get_connection()
        pending_rcpt_df = get_pending_receipts_for_hod(conn_pr, site_id=site_id)

        if pending_rcpt_df.empty:
            st.success("✅ No pending receipts awaiting approval.")
        else:
            st.info(f"**{len(pending_rcpt_df)}** receipt(s) from Store Keeper queued for your approval.")
            render_aggrid(
                pending_rcpt_df.drop(columns=["id"], errors="ignore"),
                key="pending_rcpt_grid",
                height=AGGRID_HEIGHT,
            )

            st.divider()
            if st.button("✅ Commit All Pending Receipts to Inventory", type="primary", key="hod_commit_rcpt_btn"):
                committed = commit_pending_receipts(conn_pr, site_id=site_id, username=user["username"])
                if committed > 0:
                    log_audit_action(
                        user["username"], "HOD_COMMIT_RECEIPTS", "receipts",
                        f"Approved and committed {committed} staged receipt(s) for site {site_id}",
                    )
                    st.success(f"✅ {committed} receipt(s) committed. Live inventory updated.")
                    st.rerun()
                else:
                    st.warning("Nothing to commit.")

        conn_pr.close()


# ===========================================================================
# PAGE 3: ADMIN PORTAL
# ===========================================================================
def page_admin_portal(user: dict) -> None:
    render_brand_header("Admin Portal")
    st.title("🛡️ Admin Portal")

    tab_overview, tab_reqs, tab_sites, tab_users, tab_settings, tab_db, tab_audit = st.tabs([
       "📊 System Overview", "📨 Pending Requests", "🏢 Global Site Viewer", "👥 User Management", "⚙️ Settings", "🗄️ Master DB Editor", "📜 Audit Logs"
    ])
    
    # ── NEW TAB: Global Site Viewer ──────────────────────────────────────────
    with tab_sites:
        st.subheader("🏢 Cross-Site Inventory Viewer")
        conn = get_connection()
        all_sites = get_sites(conn)
        
        target = st.selectbox("Select Site to View:", ["-- All Sites (Global) --"] + all_sites)
        
        # Load inventory based on selection
        if target == "-- All Sites (Global) --":
            site_live_df = load_live_inventory(conn, site_id=None)
        else:
            site_live_df = load_live_inventory(conn, site_id=target)
            
        st.dataframe(site_live_df, width="stretch", hide_index=True)
        conn.close()

    # ── TAB 1: Pending Cross-Site Requests ───────────────────────────────────
    with tab_reqs:
        st.subheader("Review HOD Material Requests")
        conn = get_connection()
        reqs_df = get_pending_requests(conn, status="pending")

        if reqs_df.empty:
            st.info("No pending requests to review.")
        else:
            reqs_df.insert(0, "☑️ Select", False)

            edited_df = st.data_editor(
                reqs_df,
                use_container_width=True,
                hide_index=True,
                disabled=[col for col in reqs_df.columns if col != "☑️ Select"],
                key="bulk_req_editor",
            )

            st.write("---")
            admin_notes = st.text_input("Admin Notes (Optional / Required for Rejection):")

            col_approve, col_reject = st.columns(2)

            with col_approve:
                if st.button("✅ Approve Selected", type="primary", use_container_width=True):
                    selected_rows = edited_df[edited_df["☑️ Select"] == True]
                    if selected_rows.empty:
                        st.warning("⚠️ No rows selected.")
                    else:
                        from collections import defaultdict
                        approvals_by_user = defaultdict(list)
                        approved_count = 0

                        for _, row in selected_rows.iterrows():
                            req_id         = row["id"]
                            sap_val        = row["SAP_Code"]
                            req_qty        = row["requested_qty"]
                            req_date       = row["created_at"]
                            target_site    = row.get("target_site", "Unknown Source")
                            req_site       = row.get("requesting_site", row.get("Site_ID", "Unknown Destination"))
                            requester_user = row.get("requested_by", row.get("username", "hod"))

                            inv_df = pd.read_sql(
                                "SELECT Material_Code, Equipment_Description FROM inventory WHERE SAP_Code = ?",
                                conn, params=(sap_val,)
                            )
                            mat_code = inv_df.iloc[0]["Material_Code"]         if not inv_df.empty else "N/A"
                            mat_desc = inv_df.iloc[0]["Equipment_Description"] if not inv_df.empty else "Unknown Material"

                            update_request_status(conn, req_id, "approved", user["username"], admin_notes)

                            approvals_by_user[requester_user].append({
                                "req_id":      req_id,
                                "sap_val":     sap_val,
                                "mat_code":    mat_code,
                                "mat_desc":    mat_desc,
                                "req_qty":     req_qty,
                                "target_site": target_site,
                                "req_site":    req_site,
                            })
                            approved_count += 1

                        for requester_user, items in approvals_by_user.items():
                            target_phone = get_phone_by_username(requester_user)
                            if target_phone and len(target_phone) >= 5:
                                item_lines = "\n".join(
                                    f"• {i['req_qty']}x [{i['sap_val']}] {i['mat_desc']} "
                                    f"({i['target_site']} ➡️ {i['req_site']})"
                                    for i in items
                                )
                                msg = f"""✅ *BATCH TRANSFER APPROVED*
👤 Requested By: {requester_user}

📦 *Approved Items ({len(items)}):*
{item_lines}

📝 *Admin Instructions:*
{admin_notes if admin_notes.strip() else "N/A"}"""
                                queue_whatsapp_alert(target_phone, msg)

                        # ── Goal 1: Notify Target HOD(s) — approval trigger ──────
                        _items_by_target = defaultdict(list)
                        for _ru, _ri in approvals_by_user.items():
                            for _itm in _ri:
                                _items_by_target[_itm["target_site"]].append(_itm)

                        for _tgt_site, _tgt_items in _items_by_target.items():
                            _tgt_hod_df = pd.read_sql(
                                "SELECT Phone_Number FROM users WHERE role = 'hod' "
                                "AND Site_ID = ? AND Phone_Number IS NOT NULL "
                                "AND Phone_Number != '' LIMIT 1",
                                conn, params=(_tgt_site,),
                            )
                            if not _tgt_hod_df.empty:
                                _tgt_phone = str(_tgt_hod_df.iloc[0]["Phone_Number"])
                                if _tgt_phone and len(_tgt_phone) >= 5:
                                    _pack_lines = "\n".join(
                                        f"• {_i['req_qty']}x [{_i['sap_val']}] {_i['mat_desc']}"
                                        f" → to *{_i['req_site']}*"
                                        for _i in _tgt_items
                                    )
                                    queue_whatsapp_alert(_tgt_phone, (
                                        f"📦 *TRANSFER ORDER — {_tgt_site}*\n"
                                        f"Admin has approved the following items for outbound transfer "
                                        f"from your site. Please arrange packing:\n\n"
                                        f"{_pack_lines}\n\n"
                                        f"📝 Admin Notes: "
                                        f"{admin_notes if admin_notes.strip() else 'N/A'}"
                                    ))
                        # ─────────────────────────────────────────────────────────

                        st.success(f"✅ {approved_count} request(s) approved. WhatsApp notifications queued.")
                        st.rerun()

            with col_reject:
                if st.button("❌ Reject Selected", use_container_width=True):
                    if not admin_notes or admin_notes.strip() == "":
                        st.error("⚠️ Admin Notes are required to reject a request. Please provide a reason.")
                        st.stop()

                    selected_rows = edited_df[edited_df["☑️ Select"] == True]
                    if selected_rows.empty:
                        st.warning("⚠️ No rows selected.")
                    else:
                        from collections import defaultdict
                        rejections_by_user = defaultdict(list)
                        rejected_count = 0

                        for _, row in selected_rows.iterrows():
                            req_id         = row["id"]
                            sap_val        = row["SAP_Code"]
                            req_qty        = row["requested_qty"]
                            requester_user = row.get("requested_by", row.get("username", "hod"))

                            inv_df = pd.read_sql(
                                "SELECT Equipment_Description FROM inventory WHERE SAP_Code = ?",
                                conn, params=(sap_val,)
                            )
                            mat_desc = inv_df.iloc[0]["Equipment_Description"] if not inv_df.empty else "Unknown Material"

                            update_request_status(conn, row["id"], "rejected", user["username"], admin_notes)

                            rejections_by_user[requester_user].append({
                                "req_id":   req_id,
                                "sap_val":  sap_val,
                                "mat_desc": mat_desc,
                                "req_qty":  req_qty,
                            })
                            rejected_count += 1

                        for requester_user, items in rejections_by_user.items():
                            target_phone = get_phone_by_username(requester_user)
                            if target_phone and len(target_phone) >= 5:
                                item_lines = "\n".join(
                                    f"• {i['req_qty']}x [{i['sap_val']}] {i['mat_desc']} (Request #{i['req_id']})"
                                    for i in items
                                )
                                msg = f"""❌ *BATCH TRANSFER REJECTED*
👤 Requested By: {requester_user}

📦 *Rejected Items ({len(items)}):*
{item_lines}

📝 *Reason:*
{admin_notes}"""
                                queue_whatsapp_alert(target_phone, msg)

                        st.warning(f"❌ {rejected_count} request(s) rejected.")
                        st.rerun()
        conn.close()

    # ── TAB 2: User Management ────────────────────────────────────────────────
    with tab_users:
        render_user_management_tab(current_username=user["username"])

    with tab_settings:
        st.subheader("Dropdown Manager")
        work_types = get_work_types()
        st.write("**Current Work Types:**", ", ".join(work_types))
        new_type = st.text_input("New Work Type Name", key="new_wt_input")
        if st.button("Add to Dropdown", key="add_wt_btn"):
            if new_type.strip():
                conn = get_connection()
                conn.execute(
                    "INSERT INTO system_settings (category, value) VALUES ('Work_Type', ?)",
                    (new_type.strip(),),
                )
                conn.commit()
                conn.close()
                st.success(f"Added '{new_type}'!")
                st.rerun()

    # ── TAB 4: Master DB Editor (FIXED: Smart Search added) ───────────────────
    with tab_db:
        st.subheader("Master Database Editor")
        conn = get_connection()
        tables_df = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", conn
        )
        table_list     = tables_df["name"].tolist()
        selected_table = st.selectbox("Select Table:", table_list, key="table_selector")

        if selected_table:
            c = conn.cursor()
            c.execute(f"PRAGMA table_info({selected_table})")
            editable_cols = [col[1] for col in c.fetchall() if col[1] not in SYSTEM_COLS]

            editor_mode = st.radio(
                "Action:",
                ["📝 View / Edit Data", "➕ Add New Entry", "⚙️ Manage Columns"],
                horizontal=True,
                key="editor_mode",
            )

            if editor_mode == "📝 View / Edit Data":
                target_df = pd.read_sql(f"SELECT * FROM {selected_table}", conn)
                if "password_hash" in target_df.columns:
                    target_df["password_hash"] = "••••••••"
                
                # --- PDF EXPORTER INJECTION ---
                col_view, col_export = st.columns([4, 1])
                with col_view:
                    st.caption(f"{len(target_df):,} rows in `{selected_table}`")
                with col_export:
                    from reports import generate_universal_pdf
                    pdf_bytes = generate_universal_pdf(f"Master Data: {selected_table}", target_df, user["username"])
                    st.download_button(
                        label="📄 Export as PDF",
                        data=pdf_bytes,
                        file_name=f"GI_{selected_table}_export.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
                # ------------------------------

                # Inject label-select column for inventory table only
                if selected_table == "inventory":
                    target_df.insert(0, "🏷️ Print Label", False)
                    col_cfg = {"🏷️ Print Label": st.column_config.CheckboxColumn("🏷️ Print Label", default=False)}
                else:
                    col_cfg = {}

                edited_df = st.data_editor(
                    target_df, num_rows="dynamic",
                    column_config=col_cfg if col_cfg else None,
                    width="stretch", key=f"editor_{selected_table}",
                )
                if st.button("💾 Save Table Updates", type="primary"):
                    try:
                        save_df = edited_df.drop(columns=["🏷️ Print Label"], errors="ignore")
                        c.execute(f"DELETE FROM {selected_table}")
                        save_df.to_sql(selected_table, conn, if_exists="append", index=False)
                        conn.commit()
                        
                        # 📝 AUDIT LOG INJECTION
                        from database import log_audit_action
                        log_audit_action(user["username"], "DB_EDIT", selected_table, f"Admin bulk updated records in {selected_table}")
                        
                        st.success("✅ Table updated!")
                    except Exception as e:
                        st.error(f"Save failed: {e}")

                # ── QR Label Generator (inventory table only) ────────────────
                if selected_table == "inventory":
                    st.divider()
                    st.subheader("🖨️ QR Code Label Generator")
                    label_col = "🏷️ Print Label"
                    if label_col in edited_df.columns:
                        selected_for_labels = edited_df[edited_df[label_col] == True]
                    else:
                        selected_for_labels = edited_df.iloc[0:0]
                    label_count = len(selected_for_labels)
                    st.caption(f"{label_count} material{'s' if label_count != 1 else ''} selected for label printing.")
                    if st.button("🖨️ Generate QR Labels for Selected", type="primary", disabled=label_count == 0):
                        try:
                            from reports import generate_qr_labels_pdf
                            label_items = selected_for_labels[["SAP_Code", "Equipment_Description"]].to_dict("records")
                            pdf_bytes = generate_qr_labels_pdf(label_items)
                            st.download_button(
                                label=f"📥 Download QR Labels PDF ({label_count} label{'s' if label_count != 1 else ''})",
                                data=pdf_bytes,
                                file_name="GI_QR_Labels.pdf",
                                mime="application/pdf",
                                type="primary",
                                use_container_width=True,
                            )
                        except ImportError as e:
                            st.error(str(e))

            elif editor_mode == "➕ Add New Entry":
                if selected_table == "users":
                    st.info("Use the User Management tab to add users safely.")
                    
                elif selected_table == "receipts":
                    # --- CUSTOM LOGISTICS FORM FOR RECEIPTS ---
                    st.subheader("📥 Add New Receipt (Logistics View)")
                    conn2 = get_connection()
                    site_options = get_sites(conn2)
                    
                    # 1. DYNAMIC DROPDOWN: Site Selection
                    target_site = st.selectbox("🏢 Destination Site*", site_options, key="admin_site_select")
                    
                    # 2. DYNAMIC DROPDOWN: PR Selection (Filtered by Site)
                    all_open_prs = pd.read_sql("SELECT DISTINCT PR_Number FROM pr_master WHERE Site_ID = ? AND status = 'open'", conn2, params=(target_site,))
                    pr_options = ["-- None --"] + all_open_prs["PR_Number"].tolist()
                    
                    selected_pr = st.selectbox("🔗 Link to Open PR", pr_options, key="admin_pr_select")
                    
                    # 3. DYNAMIC FILTER: Material Selection (Filtered by PR)
                    if selected_pr == "-- None --":
                        inv_list_db = pd.read_sql("SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn2)
                    else:
                        inv_list_db = pd.read_sql("""
                            SELECT i.SAP_Code, i.Equipment_Description, i.UOM 
                            FROM pr_master p
                            JOIN inventory i ON p.SAP_Code = i.SAP_Code
                            WHERE p.PR_Number = ? AND p.Site_ID = ?
                        """, conn2, params=(selected_pr, target_site))
                        
                    if not inv_list_db.empty:
                        inv_list_db["Search_String"] = "[" + inv_list_db["SAP_Code"].astype(str) + "] " + inv_list_db["Equipment_Description"].astype(str)
                        material_options = inv_list_db["Search_String"].tolist()
                    else:
                        material_options = []

                    # 4. RECEIPT FORM
                    with st.form("admin_receipt_form", clear_on_submit=True):
                        c1, c2 = st.columns(2)
                        with c1:
                            sel_item = st.selectbox("Select Material*", material_options, index=None)
                            qty = st.number_input("Quantity Received*", min_value=0.1, step=1.0)
                            date_val = st.date_input("Delivery Date*", datetime.date.today())
                        with c2:
                            exp_date = st.date_input("Expiry Date (Optional)", value=None)
                            supplier = st.text_input("Supplier / Vendor")
                            remarks = st.text_input("Remarks")
                            
                        if st.form_submit_button("💾 Save Receipt", type="primary"):
                            if not sel_item:
                                st.error("⚠️ Please select a material.")
                            else:
                                sap_code = sel_item.split("]")[0].replace("[", "").strip()
                                pr_val = selected_pr if selected_pr != "-- None --" else None
                                exp_val = str(exp_date) if exp_date else None
                                
                                ok, msg = process_receipt_delivery(
                                    conn2, str(date_val), sap_code, qty, supplier, remarks, target_site, pr_val, exp_val
                                )
                                if ok:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                    conn2.close()
                    
                else:
                    # --- GENERIC FORM FOR ALL OTHER TABLES ---
                    st.subheader(f"New Record for `{selected_table}`")
                    
                    # FIX 1 & 4: Implement Smart Search for ALL transaction tables
                    is_transaction_table = selected_table != "inventory" and "SAP_Code" in editable_cols
                    sap_code_val = None

                    if is_transaction_table:
                        st.write("**1. Select Material**")
                        try:
                            inv_list_db = pd.read_sql("SELECT SAP_Code, Equipment_Description, Material_Code, UOM FROM inventory", conn)
                            inv_list_db["Search_String"] = "[" + inv_list_db["SAP_Code"].astype(str) + "] " + inv_list_db["Equipment_Description"].astype(str)
                            search_options_db = inv_list_db["Search_String"].tolist()
                        except:
                            search_options_db = []
                            inv_list_db = pd.DataFrame()

                        selected_item_db = st.selectbox(
                            "Search by SAP Code or Description",
                            options=search_options_db,
                            index=None,
                            placeholder="Start typing...",
                            key=f"search_{selected_table}"
                        )
                        if selected_item_db:
                            sap_code_val = selected_item_db.split("]")[0].replace("[", "").strip()
                            match_db = inv_list_db[inv_list_db["SAP_Code"] == sap_code_val]
                            if not match_db.empty:
                                item_details_db = match_db.iloc[0]
                                st.info(
                                    f"📋 **Mat Code:** {item_details_db.get('Material_Code','N/A')} "
                                    f"| **UOM:** {item_details_db.get('UOM','N/A')}"
                                )
                        st.write("**2. Fill Entry Details**")

                    with st.form(f"insert_{selected_table}"):
                        input_data = {}
                        form_col = st.columns(3)
                        
                        # Filter out columns that are handled by the Smart Search or should be removed
                        display_cols = []
                        for col_name in editable_cols:
                            if is_transaction_table and col_name in ["SAP_Code", "Material_Code", "Equipment_Description", "UOM", "Material_Name"]:
                                continue
                            display_cols.append(col_name)

                        for i, col_name in enumerate(display_cols):
                            with form_col[i % 3]:
                                if col_name == "Date":
                                    input_data[col_name] = st.date_input(col_name, datetime.date.today())
                                elif "qty" in col_name.lower() or "quantity" in col_name.lower():
                                    input_data[col_name] = st.number_input(col_name, step=1.0)
                                else:
                                    input_data[col_name] = st.text_input(col_name)

                        if st.form_submit_button("Submit New Entry"):
                            if is_transaction_table and not sap_code_val:
                                st.error("⚠️ Please select a material from the dropdown.")
                            else:
                                if is_transaction_table:
                                    input_data["SAP_Code"] = sap_code_val
                                    
                                cols_str     = ", ".join(input_data.keys())
                                placeholders = ", ".join(["?"] * len(input_data))
                                values = [
                                    str(v) if isinstance(v, datetime.date) else v
                                    for v in input_data.values()
                                ]
                                try:
                                    c.execute(f"INSERT INTO {selected_table} ({cols_str}) VALUES ({placeholders})", values)
                                    conn.commit()
                                    st.success("✅ Entry added!")
                                except Exception as e:
                                    st.error(f"Failed: {e}")

            elif editor_mode == "⚙️ Manage Columns":
                st.subheader("Column Management")
                if selected_table == "users":
                    st.info("The users table schema is managed by auth.py — do not modify columns here.")
                else:
                    mc1, mc2, mc3 = st.columns(3)
                    with mc1:
                        st.write("**➕ Add Column**")
                        add_col = st.text_input("Column Name", key="add_col")
                        if st.button("Add", key="add_col_btn"):
                            try:
                                c.execute(f"ALTER TABLE {selected_table} ADD COLUMN {add_col} TEXT")
                                conn.commit()
                                st.success("Added!")
                                st.rerun()
                            except Exception as e:
                                st.error(e)
                    with mc2:
                        st.write("**✏️ Rename Column**")
                        old = st.selectbox("Column", editable_cols, key="ren_old")
                        new = st.text_input("New Name", key="ren_new")
                        if st.button("Rename", key="rename_btn"):
                            try:
                                c.execute(f"ALTER TABLE {selected_table} RENAME COLUMN {old} TO {new}")
                                conn.commit()
                                st.success("Renamed!")
                                st.rerun()
                            except Exception as e:
                                st.error(e)
                    with mc3:
                        st.write("**🗑️ Drop Column**")
                        drop = st.selectbox("Column to Delete", editable_cols, key="drop_col")
                        if st.button("Delete Column", key="drop_btn"):
                            try:
                                c.execute(f"ALTER TABLE {selected_table} DROP COLUMN {drop}")
                                conn.commit()
                                st.success("Dropped!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"SQLite Drop Failed: {e}")
        conn.close()

# ── TAB 5: System Audit Logs (Module 7) ──────────────────────────────────
    with tab_audit:
        st.subheader("📜 Enterprise Audit Ledger")
        st.markdown("Immutable record of all critical system actions, authentications, and data modifications.")
        
        conn = get_connection()
        
        # Add dynamic filters for the Admin
        col_f1, col_f2, col_f3 = st.columns(3)
        
        with col_f1:
            log_users = pd.read_sql("SELECT DISTINCT username FROM system_audit_log", conn)["username"].tolist()
            filter_user = st.selectbox("Filter by User", ["All Users"] + log_users)
            
        with col_f2:
            log_actions = pd.read_sql("SELECT DISTINCT action_type FROM system_audit_log", conn)["action_type"].tolist()
            filter_action = st.selectbox("Filter by Action", ["All Actions"] + log_actions)
            
        with col_f3:
            log_limit = st.selectbox("Display Limit", [50, 100, 500, 1000])

        # Build the dynamic SQL query based on filters
        query = "SELECT timestamp, username, action_type, target_table, details FROM system_audit_log WHERE 1=1"
        params = []
        
        if filter_user != "All Users":
            query += " AND username = ?"
            params.append(filter_user)
        if filter_action != "All Actions":
            query += " AND action_type = ?"
            params.append(filter_action)
            
        query += f" ORDER BY timestamp DESC LIMIT {log_limit}"
        
        # Fetch and display
        audit_df = pd.read_sql(query, conn, params=tuple(params))
        
        if audit_df.empty:
            st.info("No audit logs found matching the current filters.")
        else:
            # Rename columns for a cleaner UI
            audit_df = audit_df.rename(columns={
                "timestamp": "Time (UTC)", 
                "username": "User", 
                "action_type": "Action", 
                "target_table": "Target", 
                "details": "Details"
            })
            st.dataframe(audit_df, width="stretch", hide_index=True)
            
        conn.close()


# ===========================================================================
# PAGE 4: REPORTS
# ===========================================================================
def page_reports(user: dict) -> None:
    render_brand_header("Reports & Export")
    st.title("📊 Reports")

    role = user["role"]
    today = datetime.date.today()

    with st.expander("📋 Daily Issue Log Report", expanded=True):
        st.markdown("Includes **committed consumption** for the selected date plus any **pending items**.")
        report_date = st.date_input("Report Date", value=today, key="daily_report_date")
        col_gen, col_dl = st.columns([1, 2])
        with col_gen:
            if st.button("⚙️ Generate Daily Report", key="gen_daily"):
                with st.spinner("Building Excel report…"):
                    xlsx = build_daily_report(report_date=report_date)
                st.session_state["daily_xlsx"] = xlsx
                st.success("Report ready — click Download.")
        with col_dl:
            if "daily_xlsx" in st.session_state:
                st.download_button(
                    label="📥 Download Daily Report (.xlsx)",
                    data=st.session_state["daily_xlsx"],
                    file_name=f"GI_Daily_Report_{report_date.strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_daily",
                )

    with st.expander("📅 Monthly Consumption Pivot", expanded=False):
        st.markdown("Pivots the full consumption log by **month × SAP Code**.")
        col_gen2, col_dl2 = st.columns([1, 2])
        with col_gen2:
            if st.button("⚙️ Generate Monthly Report", key="gen_monthly"):
                with st.spinner("Building monthly pivot…"):
                    xlsx = build_monthly_report()
                st.session_state["monthly_xlsx"] = xlsx
                st.success("Report ready — click Download.")
        with col_dl2:
            if "monthly_xlsx" in st.session_state:
                st.download_button(
                    label="📥 Download Monthly Report (.xlsx)",
                    data=st.session_state["monthly_xlsx"],
                    file_name=f"GI_Monthly_Report_{today.strftime('%Y%m')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_monthly",
                )

    with st.expander("⚠️ Low-Stock Warning Report", expanded=False):
        st.markdown(
            "Lists all inventory items where **Current Stock < Minimum Qty**. "
            "🔴 Red rows = empty/negative · 🟡 Amber rows = below minimum."
        )
        col_gen3, col_dl3 = st.columns([1, 2])
        with col_gen3:
            if st.button("⚙️ Generate Low-Stock Report", key="gen_lowstock"):
                with st.spinner("Scanning inventory…"):
                    xlsx = build_low_stock_report()
                st.session_state["lowstock_xlsx"] = xlsx
                st.success("Report ready — click Download.")
        with col_dl3:
            if "lowstock_xlsx" in st.session_state:
                st.download_button(
                    label="📥 Download Low-Stock Report (.xlsx)",
                    data=st.session_state["lowstock_xlsx"],
                    file_name=f"GI_LowStock_{today.strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_lowstock",
                )

    st.divider()
    if role in ["admin", "hod"]:
        st.subheader("📧 Generate & Email EOD Report")
        st.markdown(
            "Builds the Daily Issue Log and sends it as an Excel attachment "
            "to the recipients below. Default list loaded from `.env`.\n"
            "> Edit the list directly before sending — one email per line or comma-separated."
        )

        default_rcpts = get_default_recipients()
        recipients_raw = st.text_area(
            "Recipients (edit before sending)",
            value=default_rcpts,
            height=80,
            placeholder="manager@company.com, supervisor@company.com",
            key="email_recipients",
        )
        eod_date = st.date_input("Report Date for Email", value=today, key="eod_email_date")

        if st.button("📧 Generate & Email EOD Report", type="primary", key="send_eod_btn"):
            recipients = parse_recipients(recipients_raw)
            if not recipients:
                st.error("⚠️ Please enter at least one recipient email address.")
            else:
                with st.spinner(f"Sending to {len(recipients)} recipient(s)…"):
                    ok, msg = send_eod_report(recipients, report_date=eod_date)
                if ok:
                    st.success(msg)
                    st.balloons()
                else:
                    st.error(f"❌ Send failed: {msg}")
    else:
        st.info(
            "📧 Email delivery is restricted to **Admin and HOD** users. "
            "Contact your manager to send the EOD report."
        )


# ===========================================================================
# MAIN ROUTER
# ===========================================================================
def main() -> None:
    # 1. Enforce login — stops here if unauthenticated
    user = _require_login()
    role = user["role"]

    # 2. Overdue returnable-items banner for Store Keepers
    if role == "store_keeper":
        conn_ov = get_connection()
        overdue_items = get_overdue_unreported_items(conn_ov, user.get("site_id", "HQ"))
        conn_ov.close()
        if not overdue_items.empty:
            names = ", ".join(overdue_items["material_name"].tolist())
            st.error(
                f"⚠️ **OVERDUE ITEMS — Action Required:** {len(overdue_items)} borrowed item(s) "
                f"are past their expected return time: **{names}**. "
                f"Go to the **Returnable Items** tab to follow up or mark them returned."
            )

    # 3. Build sidebar, get selected page
    page = render_sidebar(user)

    # 4. Final role check (defence-in-depth)
    if not _can_access(role, page):
        st.error("🛑 You do not have permission to view this page.")
        return

    # 5. Route to page
    if page == "📦 Live Dashboard":
        page_live_dashboard()
    elif page == "📝 Entry Log":
        page_daily_issue_log(user)
    elif page == "📋 HOD Portal":
        page_hod_portal(user)
    elif page == "🛡️ Admin Portal":
        page_admin_portal(user)
    elif page == "📊 Reports":
        page_reports(user)

if __name__ == "__main__":
    main()