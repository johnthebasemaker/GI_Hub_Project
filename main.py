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
    get_short_dated_stock, process_pr_pdf, process_receipt_delivery # <-- Added process_receipt_delivery
)
from auth import (
    seed_default_users,
    login_form, get_current_user, logout,
    render_user_management_tab,
)
from ui_components import (
    inject_custom_css, render_brand_header,
    render_aggrid, render_kpi_row,
    render_stock_donut, render_top_consumed_bar, render_stock_vs_minimum_bar,
    render_low_stock_sidebar_badge, render_barcode_scanner,
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

    live_df = load_live_inventory()
    if live_df.empty:
        st.warning("No inventory data found. Please add items via the Admin Portal first.")
        return

    render_kpi_row(live_df)
    st.divider()

    chart_col1, chart_col2 = st.columns([1, 2])
    with chart_col1:
        render_stock_donut(live_df)
    with chart_col2:
        render_top_consumed_bar(live_df)

    st.divider()
    with st.expander("📉 Stock vs Minimum Threshold", expanded=False):
        render_stock_vs_minimum_bar(live_df)

    st.subheader("Full Inventory Table")
    display_cols = [c for c in [
        "SAP_Code", "Equipment_Description", "UOM",
        "Total_Received", "Total_Consumed", "Total_Returned",
        "Current_Stock", "Minimum_Qty",
    ] if c in live_df.columns]
    render_aggrid(live_df[display_cols].copy(), key="dashboard_grid", height=AGGRID_HEIGHT)


# ===========================================================================
# PAGE 2: DAILY ISSUE LOG 
# ===========================================================================
def page_daily_issue_log(user: dict) -> None:
    render_brand_header("Material Issue Desk")
    st.title("📝 Daily Issue Log")

    work_types = get_work_types()
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
        cols = st.columns(3)
        for i, col_name in enumerate(form_cols):
            with cols[i % 3]:
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
        if st.button("Add to Grid ⬇️", type="primary"):
            mandatory_missing = any(
                col not in OPTIONAL_ISSUE_COLS
                and (val is None or str(val).strip() == "")
                for col, val in input_data.items()
            )
            if not sap_code or mandatory_missing:
                st.error("⚠️ Please select an item and fill in all mandatory (*) fields.")
            else:
                conn2 = get_connection()
                
                # --- NEW CODE: Stamp the worker's Site_ID onto the record ---
                input_data["Site_ID"] = user.get("site_id", "HQ")
                # ------------------------------------------------------------
                
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
    """, conn3)
    pending_df = pending_df.loc[:, ~pending_df.columns.duplicated()]

    if pending_df.empty:
        st.info("No items in the staging queue yet.")
    else:
        view_df = pending_df.set_index("id")
        col_cfg = {
            "SAP_Code":      st.column_config.TextColumn("SAP Code",  disabled=True),
            "Material_Name": st.column_config.TextColumn("Material Name",  disabled=True),
            "UOM":           st.column_config.TextColumn("UOM",       disabled=True),
            "Work_Type":     st.column_config.SelectboxColumn("Work Type", options=work_types),
            "Timestamp":     None,
        }
        edited_df = st.data_editor(
            view_df, column_config=col_cfg,
            num_rows="dynamic", width="stretch", key="staging_editor",
        )
        if st.button("💾 Save Grid Edits"):
            save_cols = [col for col in edited_df.columns if col not in {"Material_Name", "UOM", "Timestamp"}]
            placeholders = ", ".join(["?"] * (len(save_cols) + 1))
            c2 = conn3.cursor()
            c2.execute("DELETE FROM pending_issues")
            for idx, row in edited_df.iterrows():
                vals = [idx] + [row[col] for col in save_cols]
                c2.execute(
                    f"INSERT INTO pending_issues (id, {', '.join(save_cols)}) VALUES ({placeholders})",
                    vals,
                )
            conn3.commit()
            st.success("Grid updates saved!")
            st.rerun()
    conn3.close()

# ===========================================================================
# PAGE 3: HOD PORTAL (hod + admin)
# ===========================================================================
def page_hod_portal(user: dict) -> None:
    render_brand_header("Head of Department Portal")
    st.title("🏛️ HOD Portal")
    
    site_id = user.get("site_id", "HQ")
    st.caption(f"Managing Site: **{site_id}**")

    tab_eod, tab_inquiry, tab_my_reqs, tab_shelf, tab_pr, tab_receive = st.tabs([
        "🚀 EOD Commit", "🔍 Cross-Site Inquiry", "✅ My Requests", "🕒 Shelf-Life Alerts", "📄 Site PRs", "📥 Receive Material"
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
            # Ensure workers/supervisors only see their own site's staging queue
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
                    c.execute("DELETE FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ?", (site_id,))
                    edited_admin_df.to_sql("pending_issues", conn, if_exists="append", index=False)
                    conn.commit()
                    n = commit_eod(conn) 
                    
                    # --- 📱 WHATSAPP AUTOMATION INJECTION ---
                    from database import queue_whatsapp_alert, get_phone_by_username, get_low_stock_items
                    
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

                if st.button("📨 Send Request to Admin", type="primary", width="stretch"):
                    if avail_qty <= 0:
                        st.error(f"Cannot request. {target_site} has no stock of this item.")
                    else:
                        create_request(
                            conn, requesting_site=site_id, target_site=target_site,
                            sap_code=sap_code, requested_qty=req_qty,
                            available_qty=avail_qty, suggested_qty=suggested,
                            notes=notes, requested_by=user["username"]
                        )
                        st.success("Request sent successfully! Awaiting Admin approval.")
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
        with st.form("hod_receive_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                sel_item = st.selectbox("Select Material*", material_options, index=None)
                qty = st.number_input("Quantity Received*", min_value=0.1, step=1.0)
                date_val = st.date_input("Delivery Date*", datetime.date.today())
            with col2:
                exp_date = st.date_input("Expiry Date (Optional)", value=None)
                supplier = st.text_input("Supplier / Vendor")
                remarks = st.text_input("Remarks")
                
            if st.form_submit_button("💾 Save Receipt", type="primary"):
                if not sel_item:
                    st.error("⚠️ Please select a material.")
                else:
                    sap_code = sel_item.split("]")[0].replace("[", "").strip()
                    pr_val = selected_pr if selected_pr != "-- None (Direct Purchase) --" else None
                    exp_val = str(exp_date) if exp_date else None
                    
                    ok, msg = process_receipt_delivery(
                        conn, str(date_val), sap_code, qty, supplier, remarks, site_id, pr_val, exp_val
                    )
                    if ok:
                        # 📝 AUDIT LOG INJECTION
                        from database import log_audit_action
                        log_audit_action(user["username"], "RECEIVE_MATERIAL", "receipts", f"Received qty: {qty} of SAP: {sap_code} at {site_id}")
                        
                        st.success(msg)
                    else:
                        st.error(msg)
        conn.close()

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
            st.dataframe(reqs_df, width="stretch")
            
            st.write("---")
            col_sel, col_act = st.columns(2)
            with col_sel:
                req_id = st.selectbox("Select Request ID to Action:", reqs_df["id"].tolist())
                admin_notes = st.text_input("Admin Notes (Optional):")
            with col_act:
                st.write("") # Spacing
                st.write("")
                if st.button("✅ Approve Transfer", type="primary", width="stretch"):
                    
                    # 🛑 STEP 1: Force Admin to type instructions
                    if not admin_notes or admin_notes.strip() == "":
                        st.error("⚠️ Please type instructions in the 'Admin Notes' box before approving.")
                        st.stop() # Halts the script here until they type something
                    
                    # 🔍 STEP 2: Extract all the rich details for the message
                    target_row = reqs_df[reqs_df["id"] == req_id].iloc[0]
                    
                    sap_val = target_row["SAP_Code"]
                    req_qty = target_row["requested_qty"]
                    req_date = target_row["created_at"]
                    target_site = target_row.get("target_site", "Unknown Source") 
                    
                    # Safely grab the requesting site and username (depending on your exact DB schema)
                    req_site = target_row.get("requesting_site", target_row.get("Site_ID", "Unknown Destination"))
                    requester_user = target_row.get("requested_by", target_row.get("username", "hod"))
                    
                    # Query the inventory to get the exact Material Names
                    inv_df = pd.read_sql("SELECT Material_Code, Equipment_Description FROM inventory WHERE SAP_Code = ?", conn, params=(sap_val,))
                    if not inv_df.empty:
                        mat_code = inv_df.iloc[0]["Material_Code"]
                        mat_desc = inv_df.iloc[0]["Equipment_Description"]
                    else:
                        mat_code = "N/A"
                        mat_desc = "Unknown Material"

                    # 💾 STEP 3: Save to Database
                    update_request_status(conn, req_id, "approved", user["username"], admin_notes)
                    
                    # 📱 STEP 4: Format and Queue the WhatsApp Message
                    from database import queue_whatsapp_alert, get_phone_by_username
                    target_phone = get_phone_by_username(requester_user)
                    
                    if target_phone and len(target_phone) >= 5:
                        # Using asterisks (*) automatically bolds text in WhatsApp!
                        msg = f"""✅ *TRANSFER APPROVED*
ID: #{req_id}
From: {target_site} ➡️ To: {req_site}

📦 *Material Details:*
• SAP Code: {sap_val}
• Mat Code: {mat_code}
• Item: {mat_desc}
• Approved Qty: {req_qty}

🕒 Requested On: {req_date}
👤 Requested By: {requester_user}

📝 *Admin Instructions:*
{admin_notes}"""
                        queue_whatsapp_alert(target_phone, msg)
                        st.success(f"✅ Approved! WhatsApp queued for {requester_user}.")
                    else:
                        st.warning(f"✅ Approved, but no valid phone number found for {requester_user}.")
                    
                    st.rerun()
                    
                if st.button("❌ Reject", width="stretch"):
                    if not admin_notes or admin_notes.strip() == "":
                        st.error("⚠️ Please provide a reason in the 'Admin Notes' box before rejecting.")
                        st.stop()
                        
                    update_request_status(conn, req_id, "rejected", user["username"], admin_notes)
                    
                    # (Optional) Add the same rich text block here if you want WhatsApp rejection alerts!
                    st.warning(f"Request #{req_id} Rejected.")
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
                st.caption(f"{len(target_df):,} rows in `{selected_table}`")
                edited_df = st.data_editor(
                    target_df, num_rows="dynamic",
                    width="stretch", key=f"editor_{selected_table}",
                )
                if st.button("💾 Save Table Updates", type="primary"):
                    try:
                        c.execute(f"DELETE FROM {selected_table}")
                        edited_df.to_sql(selected_table, conn, if_exists="append", index=False)
                        conn.commit()
                        
                        # 📝 AUDIT LOG INJECTION
                        from database import log_audit_action
                        log_audit_action(user["username"], "DB_EDIT", selected_table, f"Admin bulk updated records in {selected_table}")
                        
                        st.success("✅ Table updated!")
                    except Exception as e:
                        st.error(f"Save failed: {e}")

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

    # 2. Build sidebar, get selected page
    page = render_sidebar(user)

    # 3. Final role check (defence-in-depth)
    if not _can_access(role, page):
        st.error("🛑 You do not have permission to view this page.")
        return

    # 4. Route to page
    if page == "📦 Live Dashboard":
        page_live_dashboard()
    elif page == "📝 Daily Issue Log":
        page_daily_issue_log(user)
    elif page == "📋 HOD Portal":
        page_hod_portal(user)
    elif page == "🛡️ Admin Portal":
        page_admin_portal(user)
    elif page == "📊 Reports":
        page_reports(user)

if __name__ == "__main__":
    main()