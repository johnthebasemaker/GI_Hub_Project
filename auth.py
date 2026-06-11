"""
auth.py — General Industries Lightning Hub
==========================================
Authentication, session management, and RBAC enforcement.
Uses bcrypt for ALL password operations — plaintext NEVER stored or logged.

Public API (Streamlit-free — safe for pytest):
  hash_password(plain)                                    → str
  verify_password(plain, hashed)                         → bool
  authenticate_user(username, password, conn)            → dict | None
  seed_default_users(conn)                               → None  (idempotent)
  get_all_users(conn)                                    → pd.DataFrame
  add_user(username, plain_password, role, site_id, conn) → bool
  delete_user(username, conn)                            → bool
  reset_password(username, new_plain, conn)              → bool

Streamlit-dependent (not tested directly):
  login_form()       → None  (renders login screen, sets session_state)
  get_current_user() → dict | None
  logout()           → None
"""

import sqlite3
import bcrypt
import pandas as pd
import streamlit as st
from database import get_connection, submit_registration_request, log_audit_action

from config import (
    APP_NAME, APP_ICON, APP_VERSION,
    BRAND_BLUE, BRAND_GOLD, BRAND_GOLD_LIGHT, BRAND_BLUE_LIGHT,
    DARK_BG, DARK_SURFACE, DARK_SURFACE_2, DARK_BORDER,
    TEXT_PRIMARY, TEXT_MUTED,
    ROLES, ROLE_HIERARCHY, PAGE_ACCESS,
)
from database import get_connection, get_sites

# Session-state key used across all modules
_SESSION_KEY = "gi_user"

# Default credentials — must be changed after first login
# Tuple shape: (username, plain_password, role, site_id)
_DEFAULT_USERS = [
    ("admin",      "admin2026",  "admin",      "HQ"),
    ("hod",        "hod2026",    "hod",        "HQ"),
    ("supervisor", "super2026",  "supervisor", "HQ"),
    ("worker",     "floor2026",  "store_keeper", "HQ"),
]


# ===========================================================================
# PURE CRYPTOGRAPHY HELPERS  (no Streamlit, fully testable)
# ===========================================================================

def hash_password(plain: str) -> str:
    """
    Returns a bcrypt hash of `plain`. Each call produces a unique hash
    because bcrypt generates a fresh random salt every time.
    Store the returned string in the database — never the plaintext.
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Returns True iff `plain` matches the stored bcrypt `hashed` string.
    Safe against timing attacks (bcrypt constant-time comparison).
    Returns False on any error rather than raising.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ===========================================================================
# USER MANAGEMENT  (pure Python — testable)
# ===========================================================================

def seed_default_users(conn: sqlite3.Connection = None) -> None:
    """
    Seeds the four default users (admin / hod / supervisor / worker) if the
    users table is empty. Completely idempotent — safe to call on every start.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    c = conn.cursor()
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        for username, plain, role, site_id in _DEFAULT_USERS:
            c.execute(
                # INSERT OR IGNORE: if two workers race past the count check
                # simultaneously, the second one silently skips duplicates
                # instead of crashing with UNIQUE constraint.
                "INSERT OR IGNORE INTO users (username, password_hash, role, Site_ID) VALUES (?, ?, ?, ?)",
                (username, hash_password(plain), role, site_id),
            )
        conn.commit()

    if _owns:
        conn.close()


def authenticate_user(
    username: str, password: str, conn: sqlite3.Connection = None
) -> dict | None:
    """
    Validates credentials against the users table.
    Returns {username, role, display_label, icon, site_id} on success, or None on failure.
    Never reveals whether the username or password was wrong (timing-safe).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        row = pd.read_sql(
            "SELECT username, password_hash, role, COALESCE(Site_ID,'HQ') AS Site_ID "
            "FROM users WHERE username = ?",
            conn,
            params=(username.strip(),),
        )
    finally:
        if _owns:
            conn.close()

    if row.empty:
        # Run a dummy verify to maintain constant time even on unknown usernames
        verify_password(password, "$2b$12$dummyhashfordummyuser0000000000000000000000000")
        return None

    stored_hash = row.iloc[0]["password_hash"]
    if not verify_password(password, stored_hash):
        return None

    role = row.iloc[0]["role"]
    role_meta = ROLES.get(role, {"label": role, "icon": "?"})
    return {
        "username":      row.iloc[0]["username"],
        "role":          role,
        "display_label": role_meta["label"],
        "icon":          role_meta["icon"],
        "site_id":       row.iloc[0]["Site_ID"],   # Used for data isolation throughout the app
    }


def get_all_users(conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Returns all users without the password_hash column."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    df = pd.read_sql(
        "SELECT id, username, role, COALESCE(Site_ID,'HQ') AS Site_ID, created_at "
        "FROM users ORDER BY role, username",
        conn,
    )
    if _owns:
        conn.close()
    return df


def add_user(
    username: str,
    plain_password: str,
    role: str,
    site_id: str = "HQ",
    conn: sqlite3.Connection = None,
) -> bool:
    """
    Adds a new user. Returns True on success, False if username already exists.
    Raises ValueError for invalid roles.
    site_id defaults to 'HQ' — pass the actual site when creating workers/HODs.
    """
    if role not in ROLE_HIERARCHY:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {list(ROLE_HIERARCHY)}")

    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID) VALUES (?, ?, ?, ?)",
            (username.strip(), hash_password(plain_password), role, site_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Duplicate username
    finally:
        if _owns:
            conn.close()


def delete_user(username: str, conn: sqlite3.Connection = None) -> bool:
    """
    Deletes a user by username. Returns True if a row was deleted, False otherwise.
    Refuses to delete the last admin account to prevent lockout.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        # Lockout guard: don't remove the last admin
        admin_count = pd.read_sql(
            "SELECT count(*) as n FROM users WHERE role='admin'", conn
        ).iloc[0]["n"]
        target_role = pd.read_sql(
            "SELECT role FROM users WHERE username=?", conn, params=(username,)
        )
        if not target_role.empty and target_role.iloc[0]["role"] == "admin" and admin_count <= 1:
            return False  # Would lock out all admins

        c = conn.cursor()
        c.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        return c.rowcount > 0
    finally:
        if _owns:
            conn.close()


def reset_password(
    username: str,
    new_plain_password: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """
    Replaces a user's password hash. Returns True if the user existed, False otherwise.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        c = conn.cursor()
        c.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(new_plain_password), username),
        )
        conn.commit()
        return c.rowcount > 0
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# SESSION STATE HELPERS
# ===========================================================================

def get_current_user() -> dict | None:
    """Returns the logged-in user dict from session_state, or None."""
    return st.session_state.get(_SESSION_KEY)


def logout() -> None:
    """Clears the login session and triggers a full page rerun."""
    st.session_state.pop(_SESSION_KEY, None)
    st.rerun()


# ===========================================================================
# LOGIN FORM  (Streamlit UI — renders the login gate)
# ===========================================================================

def login_form() -> None:
    """
    Renders a full-page, branded login screen.
    On successful authentication, sets st.session_state[_SESSION_KEY]
    and calls st.rerun() to load the main app.
    This function never returns normally — it either reruns or stays put.
    """
    # Centre the login card using columns
    _, col, _ = st.columns([1, 1.4, 1])

    with col:
        # ── Branded header ─────────────────────────────────────────────────
        st.markdown(f"""
        <div style="text-align:center; padding: 2rem 0 1.5rem 0;">
            <div style="width:72px;height:72px;border-radius:50%;
                background:rgba(10,25,47,0.92);
                border:2px solid rgba(212,175,55,0.65);
                box-shadow:0 0 24px rgba(212,175,55,0.22),inset 0 0 18px rgba(212,175,55,0.06);
                display:flex;align-items:center;justify-content:center;
                margin:0 auto 1.25rem auto;font-size:1.85rem;line-height:1;">
                {APP_ICON}
            </div>
            <div>
                <span style="color:{TEXT_PRIMARY}; font-size:1.8rem; font-weight:900;">General</span>
                <span style="color:{BRAND_GOLD}; font-size:1.8rem; font-weight:900;"> Industries</span>
            </div>
            <p style="color:{TEXT_MUTED}; font-size:0.8rem; margin:0.25rem 0 0 0;
                      letter-spacing:0.1em; text-transform:uppercase;">
                Enterprise Inventory Management · v{APP_VERSION}
            </p>
        </div>
        """, unsafe_allow_html=True)

        # ── Setup Tabs ─────────────────────────────────────────────────────
        tab_login, tab_register = st.tabs(["🔐 Sign In", "📝 Request Access"])

        # ── TAB 1: Login card ──────────────────────────────────────────────
        with tab_login:
            with st.container(border=True):
                st.markdown(
                    f"<h4 style='text-align:center; color:{TEXT_PRIMARY}; "
                    f"margin-bottom:1.25rem;'>Sign In to Continue</h4>",
                    unsafe_allow_html=True,
                )

                username = st.text_input(
                    "Username",
                    placeholder="Enter your username",
                    key="login_username",
                ).strip()
                password = st.text_input(
                    "Password",
                    type="password",
                    placeholder="Enter your password",
                    key="login_password",
                ).strip()

                st.markdown("<br>", unsafe_allow_html=True)

                if st.button("🔐  Sign In", type="primary", use_container_width=True):
                    if not username or not password:
                        st.error("Please enter both username and password.")
                    else:
                        user = authenticate_user(username, password)
                        if user:
                            st.session_state[_SESSION_KEY] = user
                            
                            # 🕵️ Intercept HTTP Headers to get Device/Browser Info
                            device_info = "Web Browser"
                            if hasattr(st, "context") and hasattr(st.context, "headers"):
                                device_info = st.context.headers.get("User-Agent", "Unknown Device")
                                
                            log_audit_action(username, "LOGIN", "System", f"Device: {device_info}")
                            
                            st.success(f"Welcome, {user['display_label']} {user['icon']}")
                            st.rerun()
                        else:
                            log_audit_action(username, "LOGIN_FAILED", "System", "Invalid credentials attempted")
                            st.error("❌ Invalid username or password.")

                st.markdown(
                    f"<p style='text-align:center; color:{TEXT_MUTED}; font-size:0.75rem; "
                    f"margin-top:1rem;'>Contact your system admin if you cannot log in.</p>",
                    unsafe_allow_html=True,
                )

        # ── TAB 2: Registration card ───────────────────────────────────────
        with tab_register:
            with st.container(border=True):
                st.markdown(
                    f"<h4 style='text-align:center; color:{TEXT_PRIMARY}; "
                    f"margin-bottom:0.5rem;'>New Employee Registration</h4>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"<p style='text-align:center; color:{TEXT_MUTED}; font-size:0.85rem; margin-bottom:1.25rem;'>Submit your details. An Admin will review your access request.</p>", unsafe_allow_html=True)

                with st.form("register_form", clear_on_submit=True):
                    new_user = st.text_input("Desired Username").strip()
                    new_pass = st.text_input("Password", type="password").strip()
                    
                    # Ensure formatting matches global standards, defaulting to the +966 region format
                    new_phone = st.text_input("WhatsApp Number (Include Country Code, e.g., +9665...)").strip()
                    new_role = st.selectbox("Requested Role", ["Store Keeper", "Supervisor", "Head of Department"])
                    
                    conn = get_connection()
                    import pandas as pd
                    try:
                        sites = pd.read_sql("SELECT DISTINCT value FROM system_settings WHERE category='Site'", conn)["value"].tolist()
                        site_options = sites if sites else ["HQ"]
                    except:
                        site_options = ["HQ"]
                    conn.close()
                    
                    new_site = st.selectbox("Assigned Site", site_options)
                    
                    if st.form_submit_button("Submit Access Request", type="primary", use_container_width=True):
                        if not new_user or not new_pass or not new_phone:
                            st.error("All fields (including WhatsApp number) are required.")
                        elif not new_phone.startswith("+"):
                            st.error("WhatsApp number MUST start with the '+' country code.")
                        else:
                            role_map = {
                                "Store Keeper": "store_keeper",
                                "Supervisor": "supervisor",
                                "Head of Department": "hod"
                            }
                            ok, msg = submit_registration_request(new_user, hash_password(new_pass), role_map[new_role], new_site, new_phone)
                            if ok:
                                st.success(msg)
                                
                                # --- 📱 WHATSAPP AUTOMATION INJECTION ---
                                from database import queue_whatsapp_alert, get_phone_by_username
                                
                                # Fetch the Admin's phone number
                                admin_phone = get_phone_by_username("admin")
                                if admin_phone:
                                    alert_msg = f"🔔 *NEW ACCESS REQUEST*\n👤 User: {new_user}\n🏢 Site: {new_site}\n🛠️ Role: {new_role}\n\nPlease review in the Admin Portal."
                                    queue_whatsapp_alert(admin_phone, alert_msg)
                                # ----------------------------------------
                                
                            else:
                                st.error(msg)

        # ── Default credentials hint (remove in production) ────────────────
        with st.expander("🔑 Default Credentials (First-Time Setup)", expanded=False):
            st.markdown(f"""
            | Role | Username | Password |
            |---|---|---|
            | 👑 Admin | `admin` | `admin2026` |
            | 🏛️ HOD | `hod` | `hod2026` |
            | 🛡️ Supervisor | `supervisor` | `super2026` |
            | 🗝️ Store Keeper | `worker` | `floor2026` |

            > ⚠️ **Change these immediately** via Admin → User Management after first login.
            """)

        st.markdown(
            f'<p style="text-align:center;color:{TEXT_MUTED};font-size:0.7rem;'
            f'margin-top:1.5rem;letter-spacing:0.07em;opacity:0.65;">'
            f'GI Lightning Hub &nbsp;·&nbsp; General Industries ERP &nbsp;·&nbsp; v{APP_VERSION}</p>',
            unsafe_allow_html=True,
        )


# ===========================================================================
# USER MANAGEMENT UI  (called from main.py Admin Portal)
# ===========================================================================

def render_user_management_tab(current_username: str) -> None:
    """
    Renders the full User Management UI tab.
    Pass the currently logged-in username so we can prevent self-deletion.
    """
    st.subheader("👥 User Management")
    
    # --- NEW: Fetch active sites for the dropdown ---
    active_sites = get_sites()
    # ------------------------------------------------

    # ── Current Users Table ────────────────────────────────────────────────
    st.markdown("**Current Users**")
    users_df = get_all_users()
    if users_df.empty:
        st.warning("No users found. This should not happen.")
    else:
        # Style the role column visually
        def _badge(role):
            color = ROLES.get(role, {}).get("color", TEXT_MUTED)
            icon  = ROLES.get(role, {}).get("icon", "?")
            label = ROLES.get(role, {}).get("label", role)
            return f"{icon} {label}"

        display_df = users_df.copy()
        display_df["Role"] = display_df["role"].apply(_badge)

        conn = get_connection() 
        display_df = pd.read_sql("SELECT id, username, role, Site_ID, Phone_Number, created_at FROM users", conn)
        
        # 1. Rename the columns cleanly BEFORE calling the dataframe
        display_df = display_df.rename(columns={
            "username": "Username", 
            "role": "Role",
            "created_at": "Created", 
            "Site_ID": "Site", 
            "Phone_Number": "Phone"
        })
        
        # 2. Pass the clean data into Streamlit
        st.dataframe(
            display_df[["Username", "Role", "Site", "Phone", "Created"]],
            use_container_width=True,
            hide_index=True,
        )

    # --- PHASE 7A: PENDING REGISTRATIONS ---
    st.divider()
    st.subheader("⏳ Pending Access Requests")
    
    conn = get_connection()
    c = conn.cursor()
    
    # 🛠️ Fetch Phone_Number
    pending_df = pd.read_sql("SELECT id, username, role, Site_ID, Phone_Number, created_at FROM pending_users WHERE status = 'pending'", conn)
    
    if pending_df.empty:
        st.info("No pending requests.")
    else:
        st.dataframe(pending_df, hide_index=True, use_container_width=True)
        col_approve, col_reject = st.columns(2)
        with col_approve:
            with st.form("approve_user_form"):
                target_id = st.selectbox("Select ID to Approve", pending_df["id"].tolist())
                if st.form_submit_button("✅ Approve User", type="primary"):
                    target_row = pd.read_sql("SELECT * FROM pending_users WHERE id = ?", conn, params=(target_id,))
                    if not target_row.empty:
                        t_user = target_row.iloc[0]
                        c.execute("INSERT INTO users (username, password_hash, role, Site_ID, Phone_Number) VALUES (?, ?, ?, ?, ?)",
                                  (t_user["username"], t_user["password_hash"], t_user["role"], t_user["Site_ID"], t_user["Phone_Number"]))
                        c.execute("UPDATE pending_users SET status = 'approved' WHERE id = ?", (target_id,))
                        conn.commit()
                        
                        # --- 📱 WHATSAPP INJECTION: Welcome Message ---
                        from database import queue_whatsapp_alert
                        if t_user.get("Phone_Number"):
                            welcome_msg = f"🎉 *ACCESS GRANTED*\nWelcome to the General Industries HUB, {t_user['username']}!\n\nYour request for the '{t_user['role']}' role at {t_user['Site_ID']} has been approved by the Admin. You may now log in to the system."
                            queue_whatsapp_alert(t_user["Phone_Number"], welcome_msg)
                        # ----------------------------------------------
                        
                        from database import log_audit_action
                        log_audit_action(current_username, "APPROVE_USER", "users", f"Approved access for {t_user['username']}")
                        st.success(f"User {t_user['username']} approved and activated!")
                        st.rerun()
                        
        with col_reject:
            with st.form("reject_user_form"):
                rej_id = st.selectbox("Select ID to Reject", pending_df["id"].tolist())
                if st.form_submit_button("❌ Reject Request"):
                    # We need to fetch the phone number before we reject them
                    target_row = pd.read_sql("SELECT username, Phone_Number FROM pending_users WHERE id = ?", conn, params=(rej_id,))
                    if not target_row.empty:
                        t_name = target_row.iloc[0]["username"]
                        t_phone = target_row.iloc[0].get("Phone_Number")
                        
                        c.execute("UPDATE pending_users SET status = 'rejected' WHERE id = ?", (rej_id,))
                        conn.commit()
                        
                        # --- 📱 WHATSAPP INJECTION: Rejection Message ---
                        from database import queue_whatsapp_alert
                        if t_phone:
                            reject_msg = f"❌ *ACCESS DENIED*\nHello {t_name},\nYour registration request for the General Industries HUB has been declined by the Admin. Please contact your supervisor for further details."
                            queue_whatsapp_alert(t_phone, reject_msg)
                        # ------------------------------------------------
                        
                        log_audit_action(current_username, "REJECT_USER", "pending_users", f"Rejected access for {t_name}")
                        st.warning("Request rejected.")
                        st.rerun()
                    
    conn.close() # Safely close the connection when done
    st.divider()

    # ── Three columns: Add / Reset / Delete ───────────────────────────────
    col_add, col_reset, col_delete = st.columns(3)

    # ── Add User ──────────────────────────────────────────────────────────
    with col_add:
        st.markdown("**➕ Add New User**")
        with st.form("form_add_user", clear_on_submit=True):
            new_user = st.text_input("Username", placeholder="e.g. jsmith")
            new_pass = st.text_input("Password", type="password")
            new_role = st.selectbox(
                "Role",
                options=list(ROLE_HIERARCHY.keys()),
                format_func=lambda r: f"{ROLES[r]['icon']} {ROLES[r]['label']}",
            )
            
            # --- NEW: Site Selection Dropdown ---
            new_site = st.selectbox("Assign to Site", active_sites)
            # ------------------------------------

            if st.form_submit_button("Create User", type="primary", use_container_width=True):
                if not new_user.strip() or not new_pass:
                    st.error("Username and password are required.")
                elif len(new_pass) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    # --- NEW: Pass new_site to add_user ---
                    ok = add_user(new_user.strip(), new_pass, new_role, site_id=new_site)
                    if ok:
                        st.success(f"✅ User **{new_user}** created as {new_role} at {new_site}.")
                        st.rerun()
                    else:
                        st.error(f"Username **{new_user}** already exists.")

    # ── Reset Password ────────────────────────────────────────────────────
    with col_reset:
        st.markdown("**🔑 Reset Password**")
        all_users_list = get_all_users()["username"].tolist() if not users_df.empty else []
        with st.form("form_reset_pwd", clear_on_submit=True):
            target_user = st.selectbox("Select User", all_users_list, key="reset_user_select")
            new_pwd_1   = st.text_input("New Password", type="password")
            new_pwd_2   = st.text_input("Confirm Password", type="password")
            if st.form_submit_button("Reset Password", use_container_width=True):
                if not new_pwd_1:
                    st.error("Password cannot be empty.")
                elif new_pwd_1 != new_pwd_2:
                    st.error("Passwords do not match.")
                elif len(new_pwd_1) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    ok = reset_password(target_user, new_pwd_1)
                    if ok:
                        st.success(f"✅ Password for **{target_user}** updated.")
                    else:
                        st.error("Update failed. User not found.")

    # ── Delete User ───────────────────────────────────────────────────────
    with col_delete:
        st.markdown("**🗑️ Remove User**")
        # Exclude the currently logged-in admin from the delete list
        deletable = [u for u in all_users_list if u != current_username]
        if not deletable:
            st.info("No other users to remove.")
        else:
            with st.form("form_delete_user", clear_on_submit=True):
                del_user = st.selectbox("Select User", deletable, key="del_user_select")
                st.warning(f"⚠️ This permanently removes **{del_user}**.", icon="⚠️")
                if st.form_submit_button("🗑️ Delete User", use_container_width=True):
                    ok = delete_user(del_user)
                    if ok:
                        st.success(f"User **{del_user}** removed.")
                        st.rerun()
                    else:
                        st.error(
                            "Cannot delete. This may be the last admin account."
                        )
