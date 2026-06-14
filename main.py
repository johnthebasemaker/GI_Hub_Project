"""
main.py — General Industries Lightning Hub v2.0
================================================
Enterprise entry point. Run with:  streamlit run main.py

Architecture (post-Phase-2 refactor):
  main.py            → page routing + RBAC gate ONLY
  pages_internal/    → one module per page; same function signatures as before
  database.py        → all SQL queries, schema init, EOD commit (Streamlit-free)
  cache_layer.py     → @st.cache_data wrappers for hot reads
  auth.py            → bcrypt login, session state, user management UI
  ui_components.py   → AgGrid, Plotly charts, branded widgets
  config.py          → constants, roles, brand colours
  mailer.py          → SMTP + Outlook EOD report dispatch
"""

import streamlit as st

# ── Project modules ──────────────────────────────────────────────────────────
from config import (
    APP_NAME, APP_ICON, APP_VERSION,
    BRAND_GOLD, TEXT_PRIMARY, TEXT_MUTED,
    PAGE_ACCESS, ROLE_HIERARCHY, ROLES,
)
from database import (
    init_db,
    get_connection,
    get_overdue_unreported_items,
    log_audit_action,
)
from cache_layer import cached_low_stock_items
from auth import (
    seed_default_users,
    login_form, get_current_user, logout,
)
from ui_components import (
    inject_custom_css,
    inject_keyboard_shortcuts,
    render_low_stock_sidebar_badge,
    render_sidebar_error_chip,
    render_theme_toggle,
    render_feedback_sidebar,
)

# Page functions live in pages_internal/ — same signatures they had inline.
from pages_internal import (
    page_live_dashboard,
    page_daily_issue_log,
    page_hod_portal,
    page_admin_portal,
    page_reports,
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
inject_keyboard_shortcuts()  # Phase 4 — `/` focus search, Esc blur, Enter submit

# ===========================================================================
# DATABASE INIT + SEED DEFAULT USERS
# ===========================================================================
init_db()             # Creates all tables, self-heals schema
seed_default_users()  # Seeds admin/supervisor/worker if users table is empty


# ===========================================================================
# BACKGROUND WORKER — WhatsApp queue processor
# @st.cache_resource runs this exactly ONCE per server lifecycle.
#
# On macOS hosts where a SEPARATE `python whatsapp_worker.py` process is
# already running (e.g. via launchd), this embedded thread MUST NOT also
# run — both would compete for the same queue rows and the embedded one
# fails the macOS Cocoa-main-thread guard, flipping every message to
# 'failed'. The launchd plist sets GI_SUPPRESS_EMBEDDED_WORKER=1 to opt
# out cleanly. Streamlit-Cloud deployments (no standalone process) leave
# the env var unset and use the embedded thread + Twilio.
# ===========================================================================
import os as _os_main


@st.cache_resource
def _start_whatsapp_worker() -> str:
    if _os_main.environ.get("GI_SUPPRESS_EMBEDDED_WORKER") == "1":
        return "skipped (standalone worker)"
    import threading
    try:
        from whatsapp_worker import run_worker_loop
        t = threading.Thread(
            target=run_worker_loop,
            daemon=True,
            name="whatsapp_worker",
        )
        t.start()
        return "started"
    except Exception as e:
        return f"failed: {e}"   # Non-critical — app works without notifications

_start_whatsapp_worker()


# ===========================================================================
# AUTH GATE  — unauthenticated users see ONLY the login screen
# ===========================================================================
def _require_login() -> dict:
    user = get_current_user()
    if user is None:
        login_form()
        st.stop()
    return user


# Pages that are role-locked exactly (NOT inherited via hierarchy).
# Entry Log is for Store Keepers only — HOD reviews in HOD Portal, Admin in Admin Portal.
_EXACT_ROLE_PAGES = {
    "📝 Entry Log": {"store_keeper"},
}


def _can_access(role: str, page: str) -> bool:
    exact = _EXACT_ROLE_PAGES.get(page)
    if exact is not None:
        return role in exact
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
            f"<div style='display:flex;align-items:center;gap:10px;padding:0.6rem 0 0.5rem 0;'>"
            f"<div style='width:40px;height:40px;border-radius:10px;flex-shrink:0;"
            f"background:rgba(10,22,45,0.90);border:1.5px solid rgba(212,175,55,0.55);"
            f"box-shadow:0 0 14px rgba(212,175,55,0.18);display:flex;align-items:center;"
            f"justify-content:center;font-size:1.15rem;line-height:1;'>{APP_ICON}</div>"
            f"<div>"
            f"<div style='line-height:1.15;'>"
            f"<span style='color:{BRAND_GOLD};font-weight:800;font-size:1.15rem;'>GI</span>"
            f"<span style='color:{TEXT_PRIMARY};font-weight:800;font-size:1.15rem;'> Hub</span>"
            f"</div>"
            f"<div style='color:{TEXT_MUTED};font-size:0.67rem;margin-top:1px;"
            f"letter-spacing:0.04em;'>v{APP_VERSION}</div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        role_meta = ROLES.get(role, {"icon": "?", "label": role, "color": TEXT_MUTED})
        site_id  = user.get("site_id", "HQ") or "HQ"
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);"
            f"border-radius:10px;padding:0.55rem 0.75rem;margin-bottom:0.75rem;"
            f"display:flex;align-items:center;gap:10px;'>"
            f"<span style='font-size:1.4rem;line-height:1;flex-shrink:0;'>{role_meta['icon']}</span>"
            f"<div style='overflow:hidden;flex:1;min-width:0;'>"
            f"<div style='color:{TEXT_PRIMARY};font-weight:700;font-size:0.92rem;"
            f"line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
            f"{username}</div>"
            f"<div style='display:flex;align-items:center;gap:6px;margin-top:2px;flex-wrap:wrap;'>"
            f"<span style='color:{role_meta['color']};font-size:0.7rem;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:0.07em;'>"
            f"{role_meta['label']}</span>"
            f"<span style='background:rgba(212,175,55,0.18);border:1px solid rgba(212,175,55,0.40);"
            f"color:{BRAND_GOLD};font-size:0.62rem;font-weight:700;padding:1px 7px;"
            f"border-radius:999px;letter-spacing:0.05em;'>"
            f"📍 {site_id}</span>"
            f"</div>"
            f"</div>"
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
            st.markdown(
                f"<div style='color:{TEXT_MUTED};font-size:0.68rem;font-weight:700;"
                f"text-transform:uppercase;letter-spacing:0.13em;margin-bottom:0.35rem;"
                f"margin-top:0.15rem;'>INVENTORY ALERTS</div>",
                unsafe_allow_html=True,
            )
            try:
                low_df = cached_low_stock_items()
                render_low_stock_sidebar_badge(low_df)
            except Exception as e:
                render_sidebar_error_chip(
                    "Stock alerts unavailable",
                    tooltip="Refresh in a few minutes or check the audit log.",
                )
                try:
                    log_audit_action(
                        username,
                        "stock_alerts_failed",
                        "inventory",
                        f"{type(e).__name__}: {e}",
                    )
                except Exception:
                    pass
        st.divider()

        # Hub Assistant — role-filtered Q&A against USER_MANUAL.md.
        # Sees only the manual sections their role is allowed to read.
        with st.expander("💬 Ask Hub Assistant", expanded=False):
            st.caption("Ask anything about the part of the system you can use. "
                       "Answers come from the user manual via your local AI.")
            q_key = f"_hub_q_{user['username']}"
            ans_key = f"_hub_ans_{user['username']}"
            question = st.text_area(
                "Your question",
                placeholder="e.g. How do I stage a return?",
                key=q_key, height=80, label_visibility="collapsed",
            )
            bcol_a, bcol_b = st.columns([1, 1])
            with bcol_a:
                ask_clicked = st.button("Ask", type="primary",
                                        use_container_width=True,
                                        key=f"_hub_ask_{user['username']}")
            with bcol_b:
                if st.button("Clear", use_container_width=True,
                             key=f"_hub_clear_{user['username']}"):
                    st.session_state.pop(ans_key, None)
                    st.rerun()
            if ask_clicked and question.strip():
                try:
                    from ai.manual_qa import answer_manual_question
                    chunks: list[str] = []
                    placeholder = st.empty()
                    for piece in answer_manual_question(question, role):
                        chunks.append(piece)
                        placeholder.markdown(
                            f"<div style='font-size:12.5px;color:#E0E6ED;'>"
                            f"{''.join(chunks)}</div>",
                            unsafe_allow_html=True,
                        )
                    st.session_state[ans_key] = "".join(chunks)
                except Exception as e:
                    st.error(f"Hub Assistant: {type(e).__name__}: {e}")
            elif st.session_state.get(ans_key):
                st.markdown(
                    f"<div style='font-size:12.5px;color:#E0E6ED;'>"
                    f"{st.session_state[ans_key]}</div>",
                    unsafe_allow_html=True,
                )
        st.divider()

        # Feedback hooks — every signed-in user can flag bugs or request
        # features here. Admin reviews them in Admin Portal → Reports & Bugs.
        render_feedback_sidebar(user, pages=visible_pages)
        st.divider()

        # Phase 4 — theme switcher (default 'dark' preserves current look)
        render_theme_toggle(sidebar=False)  # we're already inside `with st.sidebar:`
        st.divider()

        if st.button("🚪 Sign Out", width="stretch"):
            # 📝 AUDIT LOG INJECTION
            log_audit_action(username, "LOGOUT", "System", "User explicitly signed out")
            logout()

    return page


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
