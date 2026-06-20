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
    # Phase 5 — sidebar notifications bell
    count_unread_notifications,
    get_app_notifications,
    mark_notification_read,
    mark_all_notifications_read,
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
    page_logistics_portal,
    page_warehouse_portal,
    page_supervisor_portal,
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

# Phase 7E — Passive offline indicator. Pure browser-native (navigator.onLine
# + 'online' / 'offline' events) — no Python coupling. Hidden when online;
# shows a fixed-position red pill in the top-left when the browser is offline,
# setting user expectation that the Streamlit WebSocket may be reconnecting.
# Pairs with the streamlit-local-storage draft auto-save (form_drafts table
# + localStorage) so in-flight entries are protected during the drop.
st.markdown(
    """
<div id="gi-offline-pill"
     style="display:none;position:fixed;top:72px;left:22px;
            background:#EF4444;color:#fff;padding:6px 12px;
            border-radius:14px;font-weight:700;font-size:11.5px;
            z-index:999;box-shadow:0 2px 8px rgba(0,0,0,0.35);
            letter-spacing:0.02em;">
  🔴 Reconnecting…
</div>
<script>
  (function () {
    const _p = document.getElementById('gi-offline-pill');
    if (!_p) return;
    const _set = () => { _p.style.display = navigator.onLine ? 'none' : 'block'; };
    window.addEventListener('online',  _set);
    window.addEventListener('offline', _set);
    _set();
  })();
</script>
""",
    unsafe_allow_html=True,
)

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
# Logistics + Warehouse portals are exact-role + admin-shadow so HOD/Supervisor
# never accidentally land there.
_EXACT_ROLE_PAGES = {
    "📝 Entry Log":         {"store_keeper"},
    # HOD Portal is exact-locked so the procurement roles (which sit higher
    # in the hierarchy numerically) don't inherit access. Admin can still
    # land there via shadow — see the hide-from-sidebar rule below — but
    # logistics + warehouse_user must NEVER see it.
    "📋 HOD Portal":        {"hod", "admin"},
    # Phase 7B — Supervisor Portal exact-locked so HODs / Logistics /
    # Admin don't inherit a "Request Material" surface they shouldn't have
    # (admin lands here via shadow per the same pattern as other portals).
    "🛡️ Supervisor Portal": {"supervisor", "admin"},
    "🚚 Logistics Portal":  {"logistics", "admin"},
    "🏭 Warehouse Portal":  {"warehouse_user", "admin"},
}

# Per-page deny-list. Used when a role would otherwise pass the hierarchy
# check but should be excluded by policy. Keeps PAGE_ACCESS untouched.
# - warehouse_user shares hierarchy level 1 with supervisor → would inherit
#   Reports access by default. Policy: warehouse staff don't get Reports.
# - Phase 7B: supervisor loses Reports nav. They request material via
#   🛡️ Supervisor Portal. Reports remains visible to HOD + Admin.
_PAGE_BLOCKED_ROLES = {
    "📊 Reports": {"warehouse_user", "supervisor"},
}


def _can_access(role: str, page: str) -> bool:
    if role in _PAGE_BLOCKED_ROLES.get(page, set()):
        return False
    exact = _EXACT_ROLE_PAGES.get(page)
    if exact is not None:
        return role in exact
    required = PAGE_ACCESS.get(page, "admin")
    return ROLE_HIERARCHY.get(role, -1) >= ROLE_HIERARCHY.get(required, 99)


# ===========================================================================
# SIDEBAR
# ===========================================================================
# ---------------------------------------------------------------------------
# Phase 5 — In-app notifications bell
# ---------------------------------------------------------------------------
# Lightweight inbox surfaced as a sidebar button with an unread count.
# Click → modal with the most recent notifications + per-row 👁 Mark read
# and a bulk ✅ Mark all read. Notifications are produced by all the
# procurement-chain helpers in database.py.

def _user_warehouse_for_notif(username: str) -> str | None:
    """Lookup the warehouse_id bound to a warehouse_user account so the
    role-broadcast notifications scoped to a warehouse reach the right user."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT Warehouse_ID FROM users WHERE username = ?",
            (username,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


@st.dialog("🔔 Notifications")
def _notifications_dialog(user: dict) -> None:
    role = user.get("role")
    username = user.get("username")
    site_id  = user.get("site_id")
    wh_id    = (user.get("warehouse_id")
                or _user_warehouse_for_notif(username))
    only_unread = st.toggle(
        "Only unread", value=True, key="_notif_only_unread",
    )
    df = get_app_notifications(
        username=username, role=role,
        site_id=site_id, warehouse_id=wh_id,
        unread_only=only_unread, limit=80,
    )
    if df.empty:
        st.caption(
            "✨ All caught up. New events appear here as soon as they fire."
        )
    else:
        if st.button("✅ Mark all as read",
                     key="_notif_mark_all", use_container_width=True):
            n = mark_all_notifications_read(
                username=username, role=role,
                site_id=site_id, warehouse_id=wh_id,
            )
            st.toast(f"Marked {n} notification(s) as read", icon="✅")
            st.rerun()
        st.markdown("---")
        sev_palette = {
            "critical": ("🔴", "#EF4444"),
            "warning":  ("🟡", "#F59E0B"),
            "success":  ("🟢", "#22C55E"),
            "info":     ("🔵", "#0EA5E9"),
        }
        for _, row in df.iterrows():
            icon, color = sev_palette.get(
                row.get("severity") or "info", ("🔵", "#0EA5E9"),
            )
            unread = row.get("read_at") in (None, "", "NaT")
            bg = "rgba(212,175,55,0.06)" if unread else "transparent"
            title_html = (
                f"<div style='font-weight:600;color:#F0F4F8;font-size:13px;'>"
                f"{icon} {str(row.get('title') or '')}</div>"
            )
            body_html = ""
            if row.get("body"):
                body_html = (
                    f"<div style='color:#C0CCD8;font-size:12px;"
                    f"margin-top:3px;'>{str(row['body'])[:240]}</div>"
                )
            meta_html = (
                f"<div style='color:#7A8FA0;font-size:10.5px;margin-top:4px;'>"
                f"<span style='color:{color};font-weight:700;text-transform:"
                f"uppercase;letter-spacing:0.05em;'>"
                f"{str(row.get('severity') or 'info')}</span>"
                f" · {str(row.get('created_at') or '')}"
                f"{(' · ' + str(row['link_page'])) if row.get('link_page') else ''}"
                f"</div>"
            )
            st.markdown(
                f"<div style='background:{bg};border:1px solid #2A4060;"
                f"border-left:3px solid {color};border-radius:6px;"
                f"padding:8px 10px;margin-bottom:6px;'>"
                f"{title_html}{body_html}{meta_html}</div>",
                unsafe_allow_html=True,
            )
            if unread:
                if st.button(
                    "👁 Mark read",
                    key=f"_notif_read_{int(row['id'])}",
                    use_container_width=False,
                ):
                    mark_notification_read(int(row["id"]))
                    st.rerun()


def _render_notifications_bell(user: dict) -> None:
    """Render the bell + unread badge inside the sidebar. Tolerant of any
    DB error so a notification helper failure never breaks page loading."""
    try:
        wh_id = (user.get("warehouse_id")
                 or _user_warehouse_for_notif(user.get("username") or ""))
        n_unread = count_unread_notifications(
            username=user.get("username"),
            role=user.get("role"),
            site_id=user.get("site_id"),
            warehouse_id=wh_id,
        )
    except Exception:
        n_unread = 0

    badge_html = ""
    if n_unread > 0:
        # Cap display at 99+ so the chip stays compact
        display = "99+" if n_unread > 99 else str(n_unread)
        badge_html = (
            f"<span style='background:#EF4444;color:#fff;border-radius:999px;"
            f"font-size:10px;font-weight:800;padding:2px 7px;margin-left:6px;'>"
            f"{display}</span>"
        )
    label = f"🔔 Notifications {badge_html}"
    # Use markdown for the badge, then a real button right under it so the
    # click target is unambiguous and accessible.
    st.markdown(
        f"<div style='color:#C0CCD8;font-size:12px;margin:2px 0 4px 0;'>"
        f"{label}</div>",
        unsafe_allow_html=True,
    )
    if st.button(
        "Open inbox" if n_unread == 0 else f"Open inbox ({n_unread} unread)",
        key="_sb_notif_open", use_container_width=True,
        type="primary" if n_unread > 0 else "secondary",
    ):
        _notifications_dialog(user)
    st.divider()


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
        # Global roles (admin / logistics / warehouse_user) intentionally
        # have no Site_ID. Render a distinct "🌐 Global" chip for them and
        # for any user whose site is empty/None.
        _GLOBAL_ROLES = {"admin", "logistics", "warehouse_user"}
        site_raw = (user.get("site_id") or "").strip()
        is_global = role in _GLOBAL_ROLES or not site_raw
        if is_global:
            chip_html = (
                f"<span style='background:rgba(99,102,241,0.18);"
                f"border:1px solid rgba(99,102,241,0.40);"
                f"color:#A5B4FC;font-size:0.62rem;font-weight:700;padding:1px 7px;"
                f"border-radius:999px;letter-spacing:0.05em;'>🌐 Global</span>"
            )
        else:
            chip_html = (
                f"<span style='background:rgba(212,175,55,0.18);"
                f"border:1px solid rgba(212,175,55,0.40);"
                f"color:{BRAND_GOLD};font-size:0.62rem;font-weight:700;padding:1px 7px;"
                f"border-radius:999px;letter-spacing:0.05em;'>📍 {site_raw}</span>"
            )
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
            f"{chip_html}"
            f"</div>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Phase 5 — Notifications bell
        _render_notifications_bell(user)

        # Build the list of allowed pages
        visible_pages = []
        for p in PAGE_ACCESS:
            if _can_access(role, p):
                # Hide HOD Portal from Admin (Admin uses the Admin Portal)
                if p == "📋 HOD Portal" and role == "admin":
                    continue
                visible_pages.append(p)

        # Defensive: if the user switched roles (logged out → logged back in
        # as a different role), the cached nav_radio value can point at a
        # page that's no longer in visible_pages. Streamlit then renders the
        # radio with no selection AND keeps the stale key, so the user sees
        # an empty / wrong page list. Reset it to the first allowed page.
        if visible_pages and st.session_state.get("nav_radio") not in visible_pages:
            st.session_state["nav_radio"] = visible_pages[0]

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
                # Pre-flight the Ollama server BEFORE streaming — so an
                # unreachable server shows a friendly warning instead of
                # leaking the raw "unreachable at http://localhost:11434"
                # technical message into chat.
                try:
                    from ai.manual_qa import (
                        answer_manual_question, health as _hub_health,
                    )
                except Exception as _imp_err:
                    st.warning(
                        "💬 Hub Assistant is unavailable in this build "
                        f"({type(_imp_err).__name__})."
                    )
                else:
                    try:
                        _ok, _hmsg = _hub_health()
                    except Exception:
                        _ok, _hmsg = False, "unreachable"
                    if not _ok and ("unreachable" in _hmsg.lower()
                                    or "connection" in _hmsg.lower()):
                        st.warning(
                            "🤖 Local AI is offline. Please run `ollama serve` "
                            "in your terminal to enable the AI assistant."
                        )
                    elif not _ok:
                        # Other health failures (model not pulled, manual
                        # missing) — surface the structured message verbatim,
                        # it's already user-friendly.
                        st.warning(_hmsg)
                    else:
                        try:
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
                        except (ConnectionError, OSError, TimeoutError):
                            # Race: server died mid-stream. Same friendly
                            # message as the pre-flight branch.
                            st.warning(
                                "🤖 Local AI is offline. Please run "
                                "`ollama serve` in your terminal to enable "
                                "the AI assistant."
                            )
                        except Exception as e:
                            # Last-resort catch — keep the user out of a
                            # stack trace; surface the type only.
                            st.error(
                                f"Hub Assistant unavailable "
                                f"({type(e).__name__}). Try again shortly."
                            )
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
    elif page == "🛡️ Supervisor Portal":
        page_supervisor_portal(user)
    elif page == "🛡️ Admin Portal":
        page_admin_portal(user)
    elif page == "📊 Reports":
        page_reports(user)
    elif page == "🚚 Logistics Portal":
        page_logistics_portal(user)
    elif page == "🏭 Warehouse Portal":
        page_warehouse_portal(user)


if __name__ == "__main__":
    main()
