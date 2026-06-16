"""
pages_internal/admin_portal.py — Admin Portal (Claude Design adapt)
=====================================================================
Preservation Rule honored end-to-end:

  • Pending Requests approve/reject + WhatsApp + admin-notes flow
    — UNCHANGED.
  • Master Database Editor (View / Add / Manage Columns + QR labels
    + Logistics Receipt form) — UNCHANGED.
  • Dropdown Manager (Work Type CRUD) — UNCHANGED.
  • Audit Log filters + display — preserved; just restyled.
  • auth.render_user_management_tab — called UNCHANGED.

NEW tabs added per Claude Admin Portal mock:
  • 🖥️ System Overview (live KPIs + service health + audit feed)
  • 📱 WhatsApp Console (queue log + manual send + event mappings)
  • 🔑 Access Control (recent logins + force-password reset + 2FA view)

Settings tab now extends with maintenance mode + backup-now + danger zone.
"""

from __future__ import annotations

import datetime
import html
import os
import shutil
from collections import defaultdict

import pandas as pd
import streamlit as st

from config import SYSTEM_COLS, AI_ENABLED
from database import (
    get_connection,
    get_pending_requests,
    update_request_status,
    process_receipt_delivery,
    queue_whatsapp_alert,
    get_phone_by_username,
    log_audit_action,
    get_app_setting,
    set_app_setting,
    get_whatsapp_log,
    list_bug_reports,
    update_bug_report,
)
from cache_layer import (
    cached_work_types,
    cached_sites,
    cached_live_inventory,
    cached_low_stock_items,
    cached_short_dated_stock,
    bust_inventory_cache,
    bust_settings_cache,
)
from auth import render_user_management_tab
from ui_components import (
    render_brand_header_admin,
    render_empty_state,
    render_hero_metrics,
    status_pill_html,
)


# Claude design tokens — kept aligned with Admin Portal.html `C` map.
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
    "bg":     "#0A1628",
}

# ─── DB path: used by overview (size on disk) + settings backup ────────────
try:
    from config import DB_FILE as _DB_FILE  # if defined
except Exception:
    _DB_FILE = os.path.join(os.path.dirname(__file__), "..", "gi_database.db")


# ===========================================================================
# Inline HTML-table helpers (kept local — same shape as HOD tab tables)
# ===========================================================================
def _esc(v) -> str:
    if v is None:
        return "—"
    try:
        if pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        pass
    return html.escape(str(v))


def _html_table(rows_html: str, columns: list[str]) -> str:
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


def _severity_from_action(action: str) -> str:
    """Map an audit action_type to a design severity (info / warn / crit)."""
    a = (action or "").upper()
    if any(k in a for k in ("FAIL", "REJECT", "DELETE", "PURGE", "EMERG",
                            "ROLLBACK", "RESET", "DESTRUCTIVE")):
        return "crit"
    if any(k in a for k in ("SUSPEND", "REVOKE", "ROTATE", "FLAG",
                            "DOWNGRADE", "WARNING")):
        return "warn"
    return "info"


def _sev_icon(sev: str) -> str:
    return {"crit": "🔴", "warn": "🟡", "info": "🟢"}.get(sev, "🟢")


def _sev_col(sev: str) -> str:
    return {"crit": _C["crit"], "warn": _C["low"], "info": _C["ok"]}[sev]


# ===========================================================================
# PAGE  — top-level routing
# ===========================================================================
def page_admin_portal(user: dict) -> None:
    # Compute global health so the brand header shows the right pulse colour
    # without making the rest of the page slower.
    sys_ok = True
    try:
        _low_g = cached_low_stock_items()
        if _low_g is not None and not _low_g.empty and len(_low_g) > 20:
            sys_ok = False
    except Exception:
        sys_ok = True

    render_brand_header_admin("Administrator Portal", status_ok=sys_ok)
    st.markdown(
        f'<h1 style="color:{_C["text"]};font-size:21px;font-weight:700;'
        f'letter-spacing:-0.02em;margin:0 0 14px 0;">🛡️ Admin Portal</h1>',
        unsafe_allow_html=True,
    )

    # Hero strip (kept — admin overview at a glance)
    try:
        _low_g    = cached_low_stock_items()
        _expiry_g = cached_short_dated_stock()
        _sites_n  = len(cached_sites() or [])
        _low_n    = 0 if _low_g is None or _low_g.empty else len(_low_g)
        _expiry_n = 0 if _expiry_g is None or _expiry_g.empty else len(_expiry_g)
        _pr_conn = get_connection()
        try:
            _pending_req_n = _pr_conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status='pending'"
            ).fetchone()[0] or 0
        except Exception:
            _pending_req_n = 0
        finally:
            _pr_conn.close()
        render_hero_metrics([
            {"label": "Sites managed", "value": _sites_n, "tone": "neutral"},
            {"label": "Pending cross-site requests", "value": _pending_req_n,
             "tone": "neutral" if _pending_req_n == 0 else "low",
             "delta": "queue clear" if _pending_req_n == 0 else "Pending Requests tab"},
            {"label": "Critical items (all sites)", "value": _low_n + _expiry_n,
             "tone": "ok" if (_low_n + _expiry_n) == 0
                     else ("low" if (_low_n + _expiry_n) < 15 else "critical"),
             "delta": "all healthy" if (_low_n + _expiry_n) == 0
                     else f"{_low_n} low · {_expiry_n} expiry"},
        ])
    except Exception:
        pass

    st.write("")

    tab_labels = [
        "🖥️ Overview", "📨 Pending Requests", "🏢 Global Sites",
        "👥 Users", "🗄️ Master DB Editor", "📜 Audit Logs",
        "📱 WhatsApp Console", "⚙️ Settings", "🔑 Access Control",
        "💬 Reports & Bugs",
        # Phase 5 — cross-site oversight of the procurement chain
        "🚚 Logistics Oversight",
    ]
    tabs = st.tabs(tab_labels)
    with tabs[0]: _render_overview_tab(user)
    with tabs[1]: _render_pending_requests_tab(user)
    with tabs[2]: _render_global_sites_tab(user)
    with tabs[3]: _render_users_tab(user)
    with tabs[4]: _render_master_db_editor_tab(user)
    with tabs[5]: _render_audit_logs_tab(user)
    with tabs[6]: _render_whatsapp_console_tab(user)
    with tabs[7]: _render_settings_tab(user)
    with tabs[8]: _render_access_control_tab(user)
    with tabs[9]: _render_bugs_tab(user)
    with tabs[10]: _render_logistics_oversight_tab(user)


# ===========================================================================
# TAB 1 — SYSTEM OVERVIEW (NEW)
# ===========================================================================
def _render_overview_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🖥️ System Overview</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Live snapshot of the GI Hub backend — database health, service '
        f'reachability, and recent activity.</p>',
        unsafe_allow_html=True,
    )

    # ── KPI strip ────────────────────────────────────────────────────────
    db_size_mb = None
    try:
        if os.path.exists(_DB_FILE):
            db_size_mb = os.path.getsize(_DB_FILE) / (1024 * 1024)
    except Exception:
        db_size_mb = None

    conn = get_connection()
    try:
        users_n = conn.execute(
            "SELECT COUNT(*) FROM users WHERE COALESCE(status,'active') != 'suspended'"
        ).fetchone()[0] or 0
    except Exception:
        users_n = 0
    try:
        txns_n = (
            conn.execute("SELECT COUNT(*) FROM consumption").fetchone()[0] or 0
        ) + (
            conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] or 0
        )
    except Exception:
        txns_n = 0
    try:
        audit_n = conn.execute(
            "SELECT COUNT(*) FROM system_audit_log"
        ).fetchone()[0] or 0
    except Exception:
        audit_n = 0
    conn.close()

    render_hero_metrics([
        {"label": "🗄️ DB size",
         "value": f"{db_size_mb:.1f} MB" if db_size_mb is not None else "—",
         "tone": "neutral", "delta": "on disk"},
        {"label": "👥 Users",
         "value": users_n, "tone": "ok", "delta": "active accounts"},
        {"label": "📊 Total transactions",
         "value": f"{txns_n:,}",
         "tone": "neutral", "delta": "consumption + receipts"},
        {"label": "📜 Audit events",
         "value": f"{audit_n:,}", "tone": "neutral", "delta": "all-time"},
    ])

    # ── Valuation strip ────────────────────────────────────────────────
    # Standard-cost valuation rollup. The biggest-value site lets the
    # admin spot stock concentration risk at a glance.
    try:
        from cache_layer import (
            cached_total_inventory_value, cached_value_by_site,
            cached_consumption_value,
        )
        from database import format_sar
        total_value     = cached_total_inventory_value()
        per_site        = cached_value_by_site()
        cons_value_30d  = cached_consumption_value(days=30)
        if per_site is not None and not per_site.empty:
            top_row     = per_site.iloc[0]
            top_site    = str(top_row["Site_ID"])
            top_value   = float(top_row["Stock_Value"])
            top_share   = (top_value / total_value * 100) if total_value > 0 else 0
        else:
            top_site, top_value, top_share = "—", 0.0, 0.0

        render_hero_metrics([
            {"label": "💰 Total stock value", "value": format_sar(total_value),
             "tone": "neutral" if total_value > 0 else "low",
             "delta": "standard cost · SAR" if total_value > 0
                      else "set Unit_Cost on inventory"},
            {"label": "🏭 Biggest-value site",
             "value": f"{top_site}",
             "tone": "neutral",
             "delta": f"{format_sar(top_value)} · {top_share:.0f}% of total"
                      if total_value > 0 else "no costs set"},
            {"label": "🔥 30-day consumption value",
             "value": format_sar(cons_value_30d),
             "tone": "neutral", "delta": "what got used (SAR)"},
            {"label": "📦 Pending receipts value",
             "value": "—", "tone": "neutral",
             "delta": "tracked by PR Est_Cost_SAR"},
        ])
    except Exception:
        pass  # Strip is decorative; never block admin overview.

    st.write("")

    # ── Service health + DB stats side-by-side ───────────────────────────
    col_health, col_stats = st.columns(2)

    with col_health:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:12px;">🔧 Service Health</div>',
            unsafe_allow_html=True,
        )
        # SQLite (always considered up if we reached here)
        services = [
            ("SQLite Database", True, f"WAL mode · {db_size_mb:.1f} MB" if db_size_mb else "WAL mode"),
        ]
        # WhatsApp queue
        try:
            _c = get_connection()
            pend = _c.execute(
                "SELECT COUNT(*) FROM whatsapp_queue WHERE status='pending'"
            ).fetchone()[0] or 0
            _c.close()
            services.append(
                ("WhatsApp Queue", True, f"{pend} pending dispatch"
                 if pend else "queue clear")
            )
        except Exception:
            services.append(("WhatsApp Queue", False, "queue unreachable"))
        # Ollama (AI) — best-effort
        if AI_ENABLED:
            try:
                from ai.client import OLLAMA_AVAILABLE
                services.append(
                    ("Ollama / AI", bool(OLLAMA_AVAILABLE),
                     "ready" if OLLAMA_AVAILABLE else "not reachable")
                )
            except Exception:
                services.append(("Ollama / AI", False, "module import failed"))
        # SMTP/Mail — purely informational (we don't probe SMTP here)
        services.append(("Mail / SMTP", True, "Outlook + mailto fallback"))

        for name, ok, note in services:
            col = _C["ok"] if ok else _C["crit"]
            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:'
                f'space-between;padding:7px 0;border-bottom:1px solid {_C["border"]}33;">'
                f'<div style="display:flex;align-items:center;gap:8px;">'
                f'<span class="gi-pulse" style="width:7px;height:7px;border-radius:50%;'
                f'background:{col};display:inline-block;flex-shrink:0;'
                f'box-shadow:0 0 6px {col}88;"></span>'
                f'<span style="color:{_C["text"]};font-size:13px;">{name}</span>'
                f'</div>'
                f'<div style="text-align:right;">'
                f'<span style="color:{col};font-size:12px;font-weight:600;">'
                f'{"Online" if ok else "Offline"}</span>'
                f'<span style="color:{_C["dim"]};font-size:11px;margin-left:8px;">{note}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_stats:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:12px;">📊 Database Stats</div>',
            unsafe_allow_html=True,
        )
        rows = []
        try:
            _c = get_connection()
            for label, q in [
                ("Inventory items", "SELECT COUNT(*) FROM inventory"),
                ("Consumption rows", "SELECT COUNT(*) FROM consumption"),
                ("Receipt rows", "SELECT COUNT(*) FROM receipts"),
                ("Pending issues", "SELECT COUNT(*) FROM pending_issues WHERE COALESCE(status,'pending_hod')='pending_hod'"),
                ("Pending receipts", "SELECT COUNT(*) FROM pending_receipts WHERE status='pending_hod'"),
                ("Open PR lines", "SELECT COUNT(*) FROM pr_master WHERE status='open'"),
                ("WhatsApp queue size", "SELECT COUNT(*) FROM whatsapp_queue"),
                ("Audit events", "SELECT COUNT(*) FROM system_audit_log"),
            ]:
                try:
                    n = _c.execute(q).fetchone()[0] or 0
                except Exception:
                    n = "—"
                rows.append((label, f"{n:,}" if isinstance(n, int) else n))
            _c.close()
        except Exception:
            pass

        for k, v in rows:
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:5px 0;border-bottom:1px solid {_C["border"]}33;font-size:12.5px;">'
                f'<span style="color:{_C["muted"]};">{html.escape(k)}</span>'
                f'<span style="color:{_C["text"]};font-weight:500;">{html.escape(str(v))}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")

    # ── Live audit-log feed (latest 12 entries) ──────────────────────────
    try:
        _c = get_connection()
        feed_df = pd.read_sql(
            "SELECT timestamp, username, action_type, target_table, details "
            "FROM system_audit_log ORDER BY id DESC LIMIT 12", _c,
        )
        _c.close()
        from config import localize_timestamps_df
        feed_df = localize_timestamps_df(feed_df, ["timestamp"])
    except Exception:
        feed_df = pd.DataFrame()

    st.markdown(
        f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
        f'border-radius:10px;padding:16px;">'
        f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
        f'margin-bottom:10px;">📋 Live Activity Feed</div>',
        unsafe_allow_html=True,
    )
    if feed_df.empty:
        st.caption("Audit log is empty.")
    else:
        for i, (_, r) in enumerate(feed_df.iterrows()):
            sev = _severity_from_action(r["action_type"])
            ts = str(r["timestamp"])[:19]
            border = ("" if i == len(feed_df) - 1
                      else f"border-bottom:1px solid {_C['border']}33;")
            st.markdown(
                f'<div style="display:flex;align-items:flex-start;gap:10px;'
                f'padding:8px 0;{border}">'
                f'<span style="font-size:13px;flex-shrink:0;margin-top:1px;">'
                f'{_sev_icon(sev)}</span>'
                f'<div style="flex:1;min-width:0;">'
                f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">'
                f'<span style="color:{_C["gold"]}CC;font-size:11.5px;font-family:monospace;">'
                f'{_esc(ts)}</span>'
                f'<span style="color:{_C["text"]};font-size:12.5px;font-weight:500;">'
                f'{_esc(r["action_type"])}</span>'
                f'<span style="background:{_C["border"]}66;color:{_C["muted"]};'
                f'font-size:10px;padding:1px 7px;border-radius:3px;">'
                f'{_esc(r["target_table"])}</span></div>'
                f'<div style="color:{_C["dim"]};font-size:11.5px;margin-top:2px;">'
                f'{_esc(r["username"])} · {_esc(r.get("details"))}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)


# ===========================================================================
# TAB 2 — PENDING REQUESTS (preserved approve/reject + WhatsApp flow)
# ===========================================================================
def _render_pending_requests_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📨 Pending Cross-Site Requests</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 12px 0;">'
        f'Review HOD material transfer requests. Approving notifies both the '
        f'requesting HOD <i>and</i> the target-site HOD via WhatsApp.</p>',
        unsafe_allow_html=True,
    )

    conn = get_connection()
    reqs_df = get_pending_requests(conn, status="pending")

    if reqs_df.empty:
        render_empty_state(
            icon="📭",
            title="No pending requests",
            hint="When an HOD submits a cross-site material transfer, it lands here for your review.",
        )
        conn.close()
        return

    # Timestamps already converted to GMT+3 inside get_pending_requests() via
    # the shared _localize() boundary helper. Applying localize_timestamps_df
    # again here would add a redundant +3 hours — keep the row as-is.

    reqs_df.insert(0, "☑️ Select", False)

    # Surface Material_Code + Material_Name right after SAP_Code so the
    # reviewer can see "WHAT they're asking for" at a glance instead of
    # having to scroll to the far-right of the table. The LEFT JOIN already
    # populated these columns from inventory.
    _preferred_order = [
        "☑️ Select", "id", "requesting_site", "target_site",
        "SAP_Code", "Material_Code", "Material_Name", "UOM",
        "requested_qty", "available_qty", "suggested_qty",
        "status", "notes", "requested_by", "reviewed_by",
        "created_at", "updated_at",
    ]
    _ordered = [c for c in _preferred_order if c in reqs_df.columns]
    _ordered += [c for c in reqs_df.columns if c not in _ordered]
    reqs_df = reqs_df[_ordered]

    edited_df = st.data_editor(
        reqs_df,
        use_container_width=True,
        hide_index=True,
        disabled=[col for col in reqs_df.columns if col != "☑️ Select"],
        key="bulk_req_editor",
        column_config={
            "SAP_Code":      st.column_config.TextColumn("SAP Code"),
            "Material_Code": st.column_config.TextColumn("Material Code"),
            "Material_Name": st.column_config.TextColumn("Material Name"),
            "requested_qty": st.column_config.NumberColumn("Req. Qty"),
            "available_qty": st.column_config.NumberColumn("Avail."),
            "suggested_qty": st.column_config.NumberColumn("Suggested"),
        },
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
                approvals_by_user = defaultdict(list)
                approved_count = 0

                for _, row in selected_rows.iterrows():
                    req_id         = row["id"]
                    sap_val        = row["SAP_Code"]
                    req_qty        = row["requested_qty"]
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


# ===========================================================================
# TAB 3 — GLOBAL SITE VIEWER (preserved)
# ===========================================================================
def _render_global_sites_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🏢 Cross-Site Inventory Viewer</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Live read-only view of stock at any site — useful before approving '
        f'cross-site transfer requests.</p>',
        unsafe_allow_html=True,
    )
    all_sites = cached_sites()
    target = st.selectbox(
        "Select Site to View:",
        ["-- All Sites (Global) --"] + all_sites,
    )
    site_live_df = cached_live_inventory(
        site_id=None if target == "-- All Sites (Global) --" else target,
    )
    st.dataframe(site_live_df, width="stretch", hide_index=True)


# ===========================================================================
# TAB 4 — USER MANAGEMENT (delegates verbatim to auth helper)
# ===========================================================================
def _render_users_tab(user: dict) -> None:
    # auth.render_user_management_tab owns all CRUD safely. Don't touch it.
    render_user_management_tab(current_username=user["username"])


# ===========================================================================
# TAB 5 — MASTER DB EDITOR (preserved verbatim per user request)
# ===========================================================================
def _render_master_db_editor_tab(user: dict) -> None:
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

                    log_audit_action(user["username"], "DB_EDIT", selected_table,
                                     f"Admin bulk updated records in {selected_table}")

                    bust_inventory_cache()
                    bust_settings_cache()

                    st.success("✅ Table updated!")
                except Exception as e:
                    st.error(f"Save failed: {e}")

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
                st.subheader("📥 Add New Receipt (Logistics View)")
                conn2 = get_connection()
                site_options = cached_sites()

                target_site = st.selectbox("🏢 Destination Site*", site_options, key="admin_site_select")

                all_open_prs = pd.read_sql("SELECT DISTINCT PR_Number FROM pr_master WHERE Site_ID = ? AND status = 'open'", conn2, params=(target_site,))
                pr_options = ["-- None --"] + all_open_prs["PR_Number"].tolist()

                selected_pr = st.selectbox("🔗 Link to Open PR", pr_options, key="admin_pr_select")

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
                                bust_inventory_cache()
                                st.success(msg)
                            else:
                                st.error(msg)
                conn2.close()

            else:
                st.subheader(f"New Record for `{selected_table}`")

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

                    display_cols = []
                    for col_name in editable_cols:
                        if is_transaction_table and col_name in ["SAP_Code", "Material_Code", "Equipment_Description", "UOM", "Material_Name"]:
                            continue
                        display_cols.append(col_name)

                    from config import MATERIAL_CATEGORIES
                    for i, col_name in enumerate(display_cols):
                        with form_col[i % 3]:
                            if col_name == "Date":
                                input_data[col_name] = st.date_input(col_name, datetime.date.today())
                            elif col_name == "Category":
                                input_data[col_name] = st.selectbox(
                                    "Category*", MATERIAL_CATEGORIES,
                                    index=MATERIAL_CATEGORIES.index("Others"),
                                )
                            elif "qty" in col_name.lower() or "quantity" in col_name.lower() or col_name == "Opening_Stock":
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


# ===========================================================================
# TAB 6 — AUDIT LOGS (preserved filters + design-styled chrome)
# ===========================================================================
def _render_audit_logs_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📜 Enterprise Audit Ledger</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Immutable record of every critical action, authentication event, '
        f'and data modification.</p>',
        unsafe_allow_html=True,
    )
    conn = get_connection()

    # Filters (preserved)
    col_f1, col_f2, col_f3, col_f4 = st.columns([1, 1, 1, 1])
    with col_f1:
        log_users = pd.read_sql(
            "SELECT DISTINCT username FROM system_audit_log", conn
        )["username"].tolist()
        filter_user = st.selectbox("User", ["All Users"] + log_users)
    with col_f2:
        log_actions = pd.read_sql(
            "SELECT DISTINCT action_type FROM system_audit_log", conn
        )["action_type"].tolist()
        filter_action = st.selectbox("Action", ["All Actions"] + log_actions)
    with col_f3:
        log_tables = pd.read_sql(
            "SELECT DISTINCT target_table FROM system_audit_log", conn
        )["target_table"].tolist()
        filter_table = st.selectbox("Target", ["All Targets"] + log_tables)
    with col_f4:
        log_limit = st.selectbox("Limit", [50, 100, 500, 1000])

    search = st.text_input(
        "Search details / username / action",
        placeholder="e.g. EOD, login, MAT-100…",
        key="_admin_audit_search",
    )

    query = ("SELECT timestamp, username, action_type, target_table, details "
             "FROM system_audit_log WHERE 1=1")
    params: list = []

    if filter_user != "All Users":
        query += " AND username = ?"
        params.append(filter_user)
    if filter_action != "All Actions":
        query += " AND action_type = ?"
        params.append(filter_action)
    if filter_table != "All Targets":
        query += " AND target_table = ?"
        params.append(filter_table)
    if search.strip():
        query += " AND (details LIKE ? OR username LIKE ? OR action_type LIKE ?)"
        like = f"%{search.strip()}%"
        params += [like, like, like]

    query += f" ORDER BY timestamp DESC LIMIT {log_limit}"
    audit_df = pd.read_sql(query, conn, params=tuple(params))
    conn.close()

    # UTC → GMT+3 for the timestamp column.
    from config import localize_timestamps_df
    audit_df = localize_timestamps_df(audit_df, ["timestamp"])

    if audit_df.empty:
        render_empty_state(
            icon="📜",
            title="No audit entries match these filters",
            hint="Loosen the user/action/target filter or raise the display limit.",
        )
        return

    # Render styled HTML table with severity icon + module pill
    rows_html = []
    for i, (_, r) in enumerate(audit_df.iterrows()):
        sev = _severity_from_action(r["action_type"])
        col = _sev_col(sev)
        bg = (_C["crit"] + "08" if sev == "crit"
              else (_C["surf2"] + "44" if i % 2 else "transparent"))
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            f'<td style="padding:7px 8px;text-align:center;">{_sev_icon(sev)}</td>'
            f'<td style="padding:7px 10px;color:{_C["gold"]}BB;font-family:monospace;'
            f'font-size:11.5px;white-space:nowrap;">{_esc(str(r["timestamp"])[:19])}</td>'
            f'<td style="padding:7px 10px;color:{_C["text"]};font-family:monospace;'
            f'font-size:12px;white-space:nowrap;">{_esc(r["username"])}</td>'
            f'<td style="padding:7px 10px;color:{col};font-size:12.5px;font-weight:500;'
            f'white-space:nowrap;">{_esc(r["action_type"])}</td>'
            f'<td style="padding:7px 10px;">'
            f'<span style="background:{_C["border"]}66;color:{_C["muted"]};'
            f'font-size:10.5px;padding:1px 7px;border-radius:3px;white-space:nowrap;">'
            f'{_esc(r["target_table"])}</span></td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:12px;'
            f'max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
            f'title="{_esc(r["details"])}">{_esc(r["details"])}</td>'
            f'</tr>'
        )
    st.markdown(
        _html_table("".join(rows_html),
                    ["", "Timestamp", "User", "Action", "Target", "Detail"]),
        unsafe_allow_html=True,
    )
    st.caption(f"Showing {len(audit_df):,} entries.")


# ===========================================================================
# TAB 7 — WHATSAPP CONSOLE (NEW)
# ===========================================================================
def _render_whatsapp_console_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">📱 WhatsApp Console</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Outbound queue monitor, manual sends, and alert threshold tuning. '
        f'Background worker drains the queue via <code>whatsapp_worker.py</code>.</p>',
        unsafe_allow_html=True,
    )

    # Queue stats
    conn = get_connection()
    try:
        sent_n   = conn.execute("SELECT COUNT(*) FROM whatsapp_queue WHERE status='sent'").fetchone()[0] or 0
        pend_n   = conn.execute("SELECT COUNT(*) FROM whatsapp_queue WHERE status='pending'").fetchone()[0] or 0
        fail_n   = conn.execute("SELECT COUNT(*) FROM whatsapp_queue WHERE status='failed'").fetchone()[0] or 0
        proc_n   = conn.execute("SELECT COUNT(*) FROM whatsapp_queue WHERE status='processing'").fetchone()[0] or 0
    except Exception:
        sent_n = pend_n = fail_n = proc_n = 0
    conn.close()

    render_hero_metrics([
        {"label": "✅ Sent", "value": f"{sent_n:,}", "tone": "ok"},
        {"label": "⏳ Pending", "value": pend_n,
         "tone": "low" if pend_n else "ok",
         "delta": "in queue" if pend_n else "drained"},
        {"label": "⚙️ Processing", "value": proc_n, "tone": "neutral"},
        {"label": "❌ Failed", "value": fail_n,
         "tone": "critical" if fail_n else "ok"},
    ])
    st.write("")

    # Send + thresholds side by side
    col_send, col_thresh = st.columns(2)
    with col_send:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:12px;">📤 Send Manual WhatsApp</div>',
            unsafe_allow_html=True,
        )
        recipient = st.text_input(
            "Recipient phone number",
            placeholder="+966 5X XXX XXXX",
            key="_adm_wa_to",
        )
        message = st.text_area(
            "Message",
            placeholder="[GI Hub] …",
            key="_adm_wa_msg",
            height=110,
        )
        if st.button("📱 Send WhatsApp", type="primary", key="_adm_wa_send"):
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
        st.markdown("</div>", unsafe_allow_html=True)

    with col_thresh:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:12px;">⚙️ Alert Thresholds (global)</div>',
            unsafe_allow_html=True,
        )
        try:
            low_default    = int(get_app_setting("low_stock_days", "5"))
            burn_default   = int(get_app_setting("burn_alert_days", "7"))
            expiry_default = int(get_app_setting("expiry_warn_days", "30"))
        except (TypeError, ValueError):
            low_default, burn_default, expiry_default = 5, 7, 30
        ls = st.slider("Low stock alert (days of supply)", 1, 60, low_default,
                       key="_adm_thresh_low")
        br = st.slider("Burn-rate warning (days remaining)", 1, 60, burn_default,
                       key="_adm_thresh_burn")
        ex = st.slider("Expiry warning (days before)", 1, 120, expiry_default,
                       key="_adm_thresh_exp")
        if st.button("💾 Save Thresholds", key="_adm_thresh_save"):
            set_app_setting("low_stock_days", str(ls))
            set_app_setting("burn_alert_days", str(br))
            set_app_setting("expiry_warn_days", str(ex))
            log_audit_action(
                user["username"], "UPDATE_THRESHOLDS", "app_settings",
                f"low={ls} burn={br} expiry={ex}",
            )
            st.toast("✅ Thresholds saved", icon="💾")
        st.markdown("</div>", unsafe_allow_html=True)

    st.write("")

    # Event → recipient mapping (informational read-only summary of what
    # the codebase already wires up — kept honest, no UI for changing it
    # because the wiring is in queue_whatsapp_alert call-sites, not data).
    st.markdown(
        f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
        f'border-radius:10px;padding:16px;margin-bottom:16px;">'
        f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
        f'margin-bottom:10px;">⚡ Event → Recipient (current wiring)</div>',
        unsafe_allow_html=True,
    )
    mappings = [
        ("Issue staging submitted",      "Site HOD",          "hod"),
        ("Pending receipt submitted",    "Site HOD",          "hod"),
        ("EOD committed",                "Site HOD",          "hod"),
        ("Cross-site request created",   "All admins",        "admin"),
        ("Cross-site bulk (>5 items)",   "Target site HOD",   "hod"),
        ("Cross-site request approved",  "Requesting HOD",    "hod"),
        ("Cross-site request rejected",  "Requesting HOD",    "hod"),
        ("Returnable item overdue",      "Store Keeper",      "store_keeper"),
        ("New access request",           "All admins",        "admin"),
        ("Access request approved",      "Requesting user",   "store_keeper"),
        ("Post-EOD low stock alert",     "Site HOD",          "hod"),
    ]
    rows_html = []
    for i, (event, recipient, role) in enumerate(mappings):
        bg = _C["surf2"] + "44" if i % 2 else "transparent"
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:500;">'
            f'{_esc(event)}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};">{_esc(recipient)}</td>'
            f'<td style="padding:7px 10px;color:{_C["dim"]};font-size:11px;'
            f'font-family:monospace;">{_esc(role)}</td>'
            f'</tr>'
        )
    st.markdown(
        _html_table("".join(rows_html), ["Event", "Recipient", "Role"]),
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Queue log — the table that USED to live in the HOD portal
    st.markdown(
        f'<h4 style="color:{_C["text"]};font-size:14px;font-weight:600;'
        f'margin:6px 0 8px 0;">📋 Outbound Queue Log (last 80)</h4>',
        unsafe_allow_html=True,
    )
    log_df = get_whatsapp_log(limit=80)
    if log_df is None or log_df.empty:
        st.caption("No WhatsApp messages have been queued yet.")
    else:
        # Retry-all-failed button
        from database import retry_failed_whatsapp
        ra1, _ra_spacer = st.columns([1, 4])
        with ra1:
            if st.button(f"🔄 Retry all failed ({fail_n})",
                         disabled=(fail_n == 0), key="_adm_wa_retry_all"):
                n = retry_failed_whatsapp()
                log_audit_action(
                    user["username"], "WHATSAPP_RETRY_ALL", "whatsapp_queue",
                    f"reset {n} failed → pending",
                )
                st.toast(f"🔄 {n} message(s) requeued", icon="🔄")
                st.rerun()

        # ── Sanitiser: WhatsApp message bodies sometimes carry HTML chrome
        # (from older notification templates that reused mailer fragments).
        # Strip every HTML tag and collapse whitespace so the queue log
        # never bleeds raw `<td style=...>` into the page.
        import re as _re_wa
        def _strip_html(s: str) -> str:
            if not s:
                return ""
            s = _re_wa.sub(r"<[^>]+>", " ", str(s))   # drop tags
            s = _re_wa.sub(r"\s+", " ", s).strip()    # collapse whitespace
            return s

        # ── Timestamp passthrough ─────────────────────────────────────────
        # get_whatsapp_log() returns rows that already passed through
        # _localize() (UTC → GMT+3). Re-adding +3 hours here would shift
        # the display 6 hours ahead. Just normalise empties / NaN.
        def _local_ts(s) -> str:
            if not s or str(s) in ("nan", "None", "NaT"):
                return "—"
            return str(s)

        rows_html = []
        for i, (_, r) in enumerate(log_df.iterrows()):
            bg = _C["surf2"] + "44" if i % 2 else "transparent"
            clean_msg = _strip_html(r["message"])
            err       = _strip_html(str(r.get("error_message", "") or ""))
            attempts  = int(r.get("attempts", 0) or 0)
            err_cell = (
                f'<td style="padding:7px 10px;color:#F87171;font-size:11.5px;'
                f'max-width:280px;overflow:hidden;text-overflow:ellipsis;'
                f'white-space:nowrap;" title="{_esc(err)}">{_esc(err) or "—"}</td>'
            )
            rows_html.append(
                f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
                f'<td style="padding:7px 10px;">{status_pill_html(str(r["status"]).lower())}</td>'
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:12px;'
                f'font-family:monospace;white-space:nowrap;">{_esc(r["phone_number"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["text"]};max-width:280px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
                f'title="{_esc(clean_msg)}">{_esc(clean_msg)}</td>'
                f'<td style="padding:7px 10px;color:{_C["dim"]};font-size:11.5px;'
                f'text-align:center;">{attempts}</td>'
                + err_cell +
                f'<td style="padding:7px 10px;color:{_C["muted"]};font-size:11.5px;'
                f'white-space:nowrap;">{_local_ts(r["created_at"])}</td>'
                f'<td style="padding:7px 10px;color:{_C["dim"]};font-size:11.5px;'
                f'white-space:nowrap;">{_local_ts(r.get("sent_at"))}</td>'
                f'</tr>'
            )
        st.markdown(
            _html_table("".join(rows_html),
                        ["Status", "Recipient", "Message",
                         "Tries", "Error", "Queued (GMT+3)", "Sent (GMT+3)"]),
            unsafe_allow_html=True,
        )


# ===========================================================================
# TAB 8 — SETTINGS (extended: dropdown mgr + maintenance + backup + danger)
# ===========================================================================
def _render_settings_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">⚙️ System Settings</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Operational toggles for the GI Hub. Destructive actions live in the '
        f'Danger Zone at the bottom.</p>',
        unsafe_allow_html=True,
    )

    # ── User Manual PDF download ──────────────────────────────────────────
    with st.expander("📄 Download User Manual (Branded PDF)", expanded=False):
        st.caption(
            "Builds a designed PDF from `USER_MANUAL.md` — cover page, "
            "table of contents, page headers, navy + gold brand. "
            "Suitable for printing or sharing with management."
        )
        manual_src = "USER_MANUAL.md"
        try:
            import os, datetime
            from pathlib import Path
            src = Path(manual_src)
            if not src.exists():
                st.error(f"Source file `{manual_src}` not found in the repo root.")
            else:
                meta_c1, meta_c2 = st.columns([1, 1])
                with meta_c1:
                    st.metric("Source size", f"{src.stat().st_size:,} bytes")
                with meta_c2:
                    st.metric("Last modified",
                              datetime.datetime.fromtimestamp(src.stat().st_mtime)
                              .strftime("%Y-%m-%d %H:%M"))

                if st.button("🛠️ Build PDF now", key="_adm_manual_build"):
                    with st.spinner("Rendering branded PDF…"):
                        from build_manual_pdf import build_manual_pdf
                        md = src.read_text(encoding="utf-8")
                        pdf_bytes = build_manual_pdf(md)
                    st.session_state["_adm_manual_pdf"] = pdf_bytes
                    st.session_state["_adm_manual_built_at"] = datetime.datetime.now()
                    log_audit_action(
                        user["username"], "BUILD_MANUAL_PDF", "documentation",
                        f"size={len(pdf_bytes)}",
                    )
                    st.toast(f"📄 Built {len(pdf_bytes):,} bytes", icon="📄")

                pdf_bytes = st.session_state.get("_adm_manual_pdf")
                if pdf_bytes:
                    built_at = st.session_state.get("_adm_manual_built_at")
                    st.success(
                        f"Ready — {len(pdf_bytes):,} bytes "
                        + (f"(built {built_at.strftime('%H:%M:%S')})"
                           if built_at else "")
                    )
                    st.download_button(
                        "⬇️ Download GI_Hub_User_Manual.pdf",
                        data=pdf_bytes,
                        file_name=f"GI_Hub_User_Manual_v2.0_"
                                  f"{datetime.date.today().isoformat()}.pdf",
                        mime="application/pdf",
                        type="primary",
                    )
        except Exception as e:
            st.error(f"PDF build failed: {type(e).__name__}: {e}")

    # ── AI Connection panel (Ollama host + installed models) ──────────────
    with st.expander("🤖 AI Connection (Ollama)", expanded=False):
        from ai.client import (
            OLLAMA_HOST, MODEL_CODER, MODEL_CHAT, MODEL_EMBED, MODEL_VISION,
            list_ollama_models, ollama_health,
        )
        try:
            ollama_health.clear()    # type: ignore[attr-defined]
        except Exception:
            pass
        reachable = ollama_health()
        installed = list_ollama_models() if reachable else []
        c1, c2 = st.columns([1, 2])
        with c1:
            badge = ("background:#16653433;border:1px solid #22C55E66;color:#86EFAC;"
                     if reachable else
                     "background:#7F1D1D33;border:1px solid #EF444466;color:#FCA5A5;")
            st.markdown(
                f'<div style="{badge}border-radius:8px;padding:8px 12px;'
                f'font-size:12.5px;font-weight:600;">'
                f'{"✅ Reachable" if reachable else "❌ Unreachable"}'
                f'</div>',
                unsafe_allow_html=True,
            )
        with c2:
            st.code(OLLAMA_HOST, language="text")
        st.caption(
            "Streamlit Cloud → local: set `[ollama] host = \"...\"` in App "
            "Secrets, pointing at a Tailscale/ngrok URL that reaches your Mac. "
            "Locally: leave blank to default to http://localhost:11434."
        )

        # Per-purpose model status
        st.markdown("**Models used in this app**")
        rows = [
            ("NL Search → SQL",  MODEL_CODER),
            ("Summaries / Chat", MODEL_CHAT),
            ("OCR (Vision)",     MODEL_VISION),
            ("Embeddings (RAG)", MODEL_EMBED),
        ]
        for purpose, model in rows:
            present = model in installed
            pill_color = "#22C55E" if present else "#EF4444"
            label_color = "#86EFAC" if present else "#FCA5A5"
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;'
                f'padding:5px 0;">'
                f'<span style="background:{pill_color}33;border:1px solid {pill_color}66;'
                f'color:{label_color};font-size:11px;font-weight:700;'
                f'padding:2px 8px;border-radius:999px;">'
                f'{"INSTALLED" if present else "MISSING"}</span>'
                f'<span style="color:{_C["text"]};font-size:13px;'
                f'min-width:170px;">{purpose}</span>'
                f'<code style="color:{_C["muted"]};font-size:12px;">{model}</code>'
                + (f'<code style="color:#FCA5A5;font-size:11px;margin-left:auto;">'
                   f'ollama pull {model}</code>' if not present and reachable else "")
                + '</div>',
                unsafe_allow_html=True,
            )

    # Dropdown Manager (preserved verbatim)
    with st.expander("📋 Dropdown Manager — Work Types", expanded=True):
        work_types = cached_work_types()
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
                bust_settings_cache()
                st.success(f"Added '{new_type}'!")
                st.rerun()

    # Maintenance + backup
    col_maint, col_bk = st.columns(2)
    with col_maint:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:14px 16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:8px;">🔧 Maintenance Mode</div>',
            unsafe_allow_html=True,
        )
        try:
            current = get_app_setting("maintenance_mode", "0") == "1"
        except Exception:
            current = False
        new_val = st.toggle(
            "Enable maintenance mode",
            value=current,
            help="When ON, non-admin users will see a notice and cannot use the app.",
            key="_adm_maint_toggle",
        )
        if new_val != current:
            set_app_setting("maintenance_mode", "1" if new_val else "0")
            log_audit_action(
                user["username"], "TOGGLE_MAINTENANCE", "app_settings",
                f"set={new_val}",
            )
            st.toast(
                "Maintenance mode ENABLED" if new_val else "Maintenance mode disabled",
                icon="🔧" if new_val else "✅",
            )
        st.caption(
            "⚠️ ACTIVE — Non-admin sessions will be told to come back later."
            if new_val else
            "Off — All roles can access normally."
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_bk:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:14px 16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:8px;">🗄️ Database Backup</div>',
            unsafe_allow_html=True,
        )
        last_backup = get_app_setting("last_backup_at", "")
        st.caption(
            f"Last manual backup: **{last_backup}**" if last_backup
            else "No manual backup yet on this DB."
        )
        if st.button("💾 Backup Now", key="_adm_backup_btn"):
            try:
                if not os.path.exists(_DB_FILE):
                    st.error(f"DB file not found at {_DB_FILE}")
                else:
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_dir = os.path.join(
                        os.path.dirname(_DB_FILE), "backups",
                    )
                    os.makedirs(backup_dir, exist_ok=True)
                    target = os.path.join(
                        backup_dir, f"gi_database_{ts}.db",
                    )
                    shutil.copy2(_DB_FILE, target)
                    set_app_setting(
                        "last_backup_at",
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    log_audit_action(
                        user["username"], "DB_BACKUP", "system",
                        f"copy → {target}",
                    )
                    st.success(f"Backup written to `{target}`")
            except Exception as e:
                st.error(f"Backup failed: {e}")
        st.markdown("</div>", unsafe_allow_html=True)

    # Site management — read sites from cache, allow adding via system_settings
    st.write("")
    st.markdown(
        f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
        f'border-radius:10px;padding:14px 16px;">'
        f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
        f'margin-bottom:10px;">🏭 Site Management</div>',
        unsafe_allow_html=True,
    )
    sites = cached_sites() or []
    conn = get_connection()
    try:
        per_site = pd.read_sql(
            "SELECT COALESCE(Site_ID,'HQ') AS Site_ID, COUNT(*) AS n_users "
            "FROM users GROUP BY COALESCE(Site_ID,'HQ')", conn,
        ).set_index("Site_ID")
    except Exception:
        per_site = pd.DataFrame()
    conn.close()

    rows_html = []
    for i, s in enumerate(sites):
        n_users = int(per_site.loc[s, "n_users"]) if s in per_site.index else 0
        bg = _C["surf2"] + "44" if i % 2 else "transparent"
        rows_html.append(
            f'<tr style="background:{bg};border-bottom:1px solid {_C["border"]}33;">'
            f'<td style="padding:7px 10px;color:{_C["text"]};font-weight:500;">{_esc(s)}</td>'
            f'<td style="padding:7px 10px;color:{_C["gold"]};font-family:monospace;'
            f'font-weight:700;">{_esc(s)[:4].upper()}</td>'
            f'<td style="padding:7px 10px;color:{_C["muted"]};">{n_users} user(s)</td>'
            f'<td style="padding:7px 10px;">{status_pill_html("ok", "Active")}</td>'
            f'</tr>'
        )
    if rows_html:
        st.markdown(
            _html_table("".join(rows_html),
                        ["Site Name", "Code", "Users", "Status"]),
            unsafe_allow_html=True,
        )
    else:
        st.caption("No sites configured yet.")

    with st.expander("➕ Add New Site", expanded=False):
        new_site = st.text_input(
            "Site name",
            placeholder="e.g. Site D - Dammam",
            key="_adm_new_site",
        )
        if st.button("Add Site", key="_adm_add_site"):
            if new_site.strip():
                _c = get_connection()
                _c.execute(
                    "INSERT INTO system_settings (category, value) VALUES ('Site', ?)",
                    (new_site.strip(),),
                )
                _c.commit()
                _c.close()
                bust_settings_cache()
                log_audit_action(
                    user["username"], "ADD_SITE", "system_settings",
                    new_site.strip(),
                )
                st.toast(f"Added site '{new_site.strip()}'", icon="🏭")
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    # Danger zone
    st.write("")
    st.markdown(
        f'<div style="border:1px solid {_C["crit"]}33;border-radius:10px;'
        f'padding:14px 16px;background:{_C["crit"]}06;">'
        f'<div style="color:{_C["crit"]};font-size:13px;font-weight:700;'
        f'margin-bottom:10px;">⚠️ Danger Zone</div>',
        unsafe_allow_html=True,
    )
    cdz1, cdz2 = st.columns([4, 1])
    with cdz1:
        st.markdown(
            f'<div style="color:{_C["crit"]};font-size:13px;font-weight:600;">'
            f'Purge old draft pending_issues</div>'
            f'<div style="color:{_C["muted"]};font-size:11.5px;">'
            f'Delete all <code>pending_issues</code> rows older than 30 days '
            f'that are still in <code>status=draft</code>.</div>',
            unsafe_allow_html=True,
        )
    with cdz2:
        confirm = st.text_input(
            "Type PURGE to confirm",
            key="_adm_purge_confirm",
            label_visibility="collapsed",
            placeholder="PURGE",
        )
        if st.button("Run Purge", key="_adm_purge_run",
                     disabled=confirm.strip() != "PURGE"):
            _c = get_connection()
            try:
                n = _c.execute(
                    "DELETE FROM pending_issues WHERE COALESCE(status,'draft')='draft' "
                    "AND COALESCE(Timestamp, Date) < datetime('now','-30 days')"
                ).rowcount
                _c.commit()
                log_audit_action(
                    user["username"], "PURGE_DRAFTS", "pending_issues",
                    f"removed={n}",
                )
                st.success(f"Purged {n} draft row(s).")
            except Exception as e:
                st.error(f"Purge failed: {e}")
            finally:
                _c.close()
    st.markdown("</div>", unsafe_allow_html=True)


# ===========================================================================
# TAB 9 — ACCESS CONTROL (NEW)
# ===========================================================================
def _render_access_control_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">🔑 Access Control</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Recent sign-ins, forced password reset, and the platform security '
        f'policy.</p>',
        unsafe_allow_html=True,
    )

    col_logins, col_pwd = st.columns(2)

    # Recent logins (best-effort — we don't track sessions, but auth logs
    # LOGIN_SUCCESS / LOGIN_FAILED into system_audit_log).
    with col_logins:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:14px 16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:10px;">🖥️ Recent Sign-Ins (last 10)</div>',
            unsafe_allow_html=True,
        )
        _c = get_connection()
        try:
            logins = pd.read_sql(
                "SELECT timestamp, username, action_type, details "
                "FROM system_audit_log "
                "WHERE action_type IN ('LOGIN','LOGIN_SUCCESS','LOGIN_FAILED','LOGOUT') "
                "ORDER BY id DESC LIMIT 10", _c,
            )
        except Exception:
            logins = pd.DataFrame()
        _c.close()

        if logins.empty:
            st.caption("No login activity recorded yet.")
        else:
            for _, r in logins.iterrows():
                ok = r["action_type"] not in ("LOGIN_FAILED",)
                col = _C["ok"] if ok else _C["crit"]
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;padding:7px 0;border-bottom:1px solid {_C["border"]}33;">'
                    f'<div>'
                    f'<div style="display:flex;align-items:center;gap:7px;">'
                    f'<span style="width:7px;height:7px;border-radius:50%;background:{col};'
                    f'display:inline-block;box-shadow:0 0 6px {col}88;"></span>'
                    f'<span style="color:{_C["text"]};font-size:12.5px;font-weight:500;">'
                    f'{_esc(r["username"])}</span>'
                    f'<span style="color:{col};font-size:11px;">{_esc(r["action_type"])}</span>'
                    f'</div>'
                    f'<div style="color:{_C["dim"]};font-size:11px;margin-top:2px;">'
                    f'{_esc(str(r["timestamp"])[:19])} · {_esc(r.get("details"))}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
        st.markdown("</div>", unsafe_allow_html=True)

    # Force password reset (delegates to auth via bcrypt write)
    with col_pwd:
        st.markdown(
            f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:14px 16px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
            f'margin-bottom:10px;">🔑 Force Password Reset</div>',
            unsafe_allow_html=True,
        )
        _c = get_connection()
        try:
            usernames = pd.read_sql(
                "SELECT username FROM users ORDER BY username", _c,
            )["username"].tolist()
        except Exception:
            usernames = []
        _c.close()

        target_user = st.selectbox(
            "Target user",
            ["— Select —"] + usernames,
            key="_adm_pwd_user",
        )
        new_pwd = st.text_input(
            "New password (min 8 chars)",
            type="password",
            key="_adm_pwd_new",
        )
        confirm = st.text_input(
            "Confirm",
            type="password",
            key="_adm_pwd_conf",
        )
        st.markdown(
            f'<div style="background:{_C["low"]}10;border:1px solid {_C["low"]}33;'
            f'border-radius:6px;padding:8px 10px;margin:6px 0 8px 0;'
            f'font-size:11.5px;color:{_C["low"]};">'
            f'⚠️ The user will need to log in again immediately.</div>',
            unsafe_allow_html=True,
        )
        if st.button("🔑 Reset Password", type="primary", key="_adm_pwd_btn"):
            if target_user == "— Select —":
                st.error("Pick a user.")
            elif len(new_pwd) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_pwd != confirm:
                st.error("Passwords do not match.")
            else:
                try:
                    from auth import hash_password
                    _c = get_connection()
                    _c.execute(
                        "UPDATE users SET password_hash = ? WHERE username = ?",
                        (hash_password(new_pwd), target_user),
                    )
                    _c.commit()
                    _c.close()
                    log_audit_action(
                        user["username"], "FORCE_PASSWORD_RESET", "users",
                        f"target={target_user}",
                    )
                    st.success(f"Password for `{target_user}` reset.")
                except Exception as e:
                    st.error(f"Reset failed: {e}")
        st.markdown("</div>", unsafe_allow_html=True)

    # Security policy view (data-only; editing handled in config.py / auth.py)
    st.write("")
    st.markdown(
        f'<div style="background:{_C["surf2"]};border:1px solid {_C["border"]};'
        f'border-radius:10px;padding:14px 16px;">'
        f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;'
        f'margin-bottom:10px;">🛡️ Security Policy</div>',
        unsafe_allow_html=True,
    )
    policy = [
        ("Auth backend",       "bcrypt (cost=12)"),
        ("Session storage",    "Streamlit session_state (in-memory)"),
        ("RBAC hierarchy",     "store_keeper < supervisor < hod < admin"),
        ("WAL mode",           "Enabled · busy_timeout=5000ms"),
        ("Password min length","8 characters"),
        ("Audit retention",    "Indefinite (manual purge only)"),
    ]
    grid_html = []
    for k, v in policy:
        grid_html.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:8px 12px;background:{_C["bg"]};border-radius:7px;'
            f'border:1px solid {_C["border"]}55;">'
            f'<span style="color:{_C["muted"]};font-size:12px;">{html.escape(k)}</span>'
            f'<span style="color:{_C["text"]};font-size:12px;font-weight:500;">'
            f'{html.escape(v)}</span></div>'
        )
    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
        + "".join(grid_html) + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)


# ===========================================================================
# TAB 10 — REPORTS & BUGS (user feedback inbox)
# ===========================================================================
def _render_bugs_tab(user: dict) -> None:
    st.markdown(
        f'<h3 style="color:{_C["text"]};font-size:16px;font-weight:600;'
        f'margin:0 0 4px 0;">💬 Bug Reports & Feature Requests</h3>'
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Submitted by users via the sidebar Feedback widget. Update status '
        f'and reply directly here.</p>',
        unsafe_allow_html=True,
    )

    # Stat strip
    all_df = list_bug_reports(limit=2000)
    open_n   = int((all_df["status"] == "open").sum()) if not all_df.empty else 0
    review_n = int((all_df["status"] == "in_review").sum()) if not all_df.empty else 0
    closed_n = int((all_df["status"] == "closed").sum()) if not all_df.empty else 0
    bug_n    = int((all_df["type"] == "bug").sum()) if not all_df.empty else 0
    feat_n   = int((all_df["type"] == "feature").sum()) if not all_df.empty else 0

    render_hero_metrics([
        {"label": "🐛 Bugs",
         "value": bug_n,
         "tone": "critical" if bug_n > 5 else ("low" if bug_n else "ok"),
         "delta": "user-reported"},
        {"label": "💡 Feature requests",
         "value": feat_n,
         "tone": "neutral",
         "delta": "user-suggested"},
        {"label": "⏳ Open",
         "value": open_n,
         "tone": "low" if open_n else "ok",
         "delta": "awaiting triage"},
        {"label": "✅ Closed",
         "value": closed_n,
         "tone": "ok",
         "delta": "resolved"},
    ])
    st.write("")

    # Filters
    col_t, col_s, col_u = st.columns(3)
    with col_t:
        type_filter = st.selectbox(
            "Type", ["All", "bug", "feature"], key="_adm_bug_type",
        )
    with col_s:
        status_filter = st.selectbox(
            "Status", ["All", "open", "in_review", "closed"],
            key="_adm_bug_status",
        )
    with col_u:
        users_l = sorted(all_df["username"].unique().tolist()) if not all_df.empty else []
        user_filter = st.selectbox(
            "User", ["All users"] + users_l, key="_adm_bug_user",
        )

    view_df = list_bug_reports(
        status_filter=None if status_filter == "All" else status_filter,
        type_filter=None if type_filter == "All" else type_filter,
        user_filter=None if user_filter == "All users" else user_filter,
    )

    if view_df.empty:
        render_empty_state(
            icon="💬",
            title="No feedback matches these filters",
            hint="Users submit bugs + ideas from the sidebar Feedback widget.",
        )
        return

    # Render rows as expandable cards (per-row status update + reply needs
    # state-bearing widgets — st.expander is the right control here).
    for _, r in view_df.iterrows():
        rid = int(r["id"])
        rtype = r["type"]
        icon = "🐛" if rtype == "bug" else "💡"
        accent = _C["crit"] if rtype == "bug" else _C["purple"]
        status = r["status"] or "open"
        status_chip = status_pill_html(
            "rejected" if status == "closed" and False else
            ("flagged" if status == "in_review" else
             ("approved" if status == "closed" else "pending"))
        )
        # Header label rendered as raw text (expander label can't be HTML)
        header = (
            f"{icon} [{rtype.upper()}] {r['username']} · "
            f"{(r['description'] or '')[:60]}"
            + ("…" if r['description'] and len(r['description']) > 60 else "")
        )
        with st.expander(header, expanded=(status == "open")):
            # Metadata row
            st.markdown(
                f'<div style="display:flex;gap:16px;flex-wrap:wrap;'
                f'padding:4px 0 10px 0;font-size:12px;color:{_C["muted"]};">'
                f'<span><b style="color:{accent};">{icon} {rtype.title()}</b></span>'
                f'<span>👤 <b style="color:{_C["text"]};">{html.escape(str(r["username"]))}</b></span>'
                f'<span>📄 Page: <b style="color:{_C["gold"]};">{html.escape(str(r["page"]))}</b></span>'
                f'<span>🕒 {html.escape(str(r["created_at"])[:19])}</span>'
                f'<span>{status_chip}</span>'
                f'</div>'
                f'<div style="background:{_C["bg"]};border:1px solid {_C["border"]}55;'
                f'border-radius:7px;padding:10px 12px;color:{_C["text"]};'
                f'font-size:13px;line-height:1.55;margin-bottom:10px;'
                f'white-space:pre-wrap;">{html.escape(str(r["description"]))}</div>',
                unsafe_allow_html=True,
            )
            if r.get("admin_response"):
                st.markdown(
                    f'<div style="background:{_C["ok"]}10;border:1px solid {_C["ok"]}33;'
                    f'border-radius:7px;padding:10px 12px;color:{_C["text"]};'
                    f'font-size:12.5px;line-height:1.55;margin-bottom:10px;">'
                    f'<div style="color:{_C["ok"]};font-size:11px;font-weight:700;'
                    f'text-transform:uppercase;letter-spacing:0.07em;margin-bottom:4px;">'
                    f'Admin response</div>'
                    f'{html.escape(str(r["admin_response"]))}</div>',
                    unsafe_allow_html=True,
                )

            # Update controls
            ucol1, ucol2 = st.columns([1, 2])
            with ucol1:
                new_status = st.selectbox(
                    "Status",
                    ["open", "in_review", "closed"],
                    index=["open", "in_review", "closed"].index(status),
                    key=f"_adm_bug_st_{rid}",
                )
            with ucol2:
                response = st.text_area(
                    "Reply to user (optional)",
                    value=r.get("admin_response") or "",
                    key=f"_adm_bug_resp_{rid}",
                    height=80,
                    max_chars=500,
                )

            if st.button(
                "💾 Save",
                key=f"_adm_bug_save_{rid}",
                type="primary",
                width="stretch",
            ):
                ok = update_bug_report(
                    rid,
                    status=new_status,
                    admin_response=response if response.strip() else None,
                )
                if ok:
                    log_audit_action(
                        user["username"], "UPDATE_BUG_REPORT", "bug_reports",
                        f"id={rid} status={new_status}",
                    )
                    st.toast("✅ Updated", icon="💾")
                    st.rerun()
                else:
                    st.error("Update failed.")


# ===========================================================================
# TAB 11 — LOGISTICS OVERSIGHT (Phase 5)
# ===========================================================================
def _render_logistics_oversight_tab(user: dict) -> None:
    """Cross-site, read-only window onto the procurement chain. Admins see
    PRs, POs, DNs, vendor returns, reschedules, and force-closures in one
    place. No new mutation paths — every action belongs in the Logistics
    or Warehouse portal where the role-locked workflow lives."""
    from database import (
        list_prs_for_logistics, list_pos, list_dns,
        list_vendor_returns, list_force_closures,
        list_pending_reschedules, list_warehouses, get_sites,
    )

    st.markdown(
        f'<h3 style="color:{BRAND_GOLD};font-weight:700;'
        f'margin:0 0 4px 0;">🚚 Logistics Oversight</h3>'
        f'<p style="color:{TEXT_MUTED};margin:0 0 14px 0;font-size:13px;">'
        f'Cross-site picture of every active PR, PO, DN, and exception. '
        f'For actions, jump to the Logistics or Warehouse portal.</p>',
        unsafe_allow_html=True,
    )

    # ── KPI strip ─────────────────────────────────────────────────────────
    pr_q   = list_prs_for_logistics()
    po_df  = list_pos(open_only=True)
    dn_df  = list_dns(status_filter=[
        "pending_logistics", "logistics_approved",
        "pending_hod", "pending_sk",
    ])
    ret_df = list_vendor_returns(open_only=True)
    fc_df  = list_force_closures()
    rs_df  = list_pending_reschedules()

    def _kpi(label, value, sub, color=BRAND_GOLD):
        return (
            f'<div style="flex:1;min-width:140px;background:#1E3050;'
            f'border:1px solid #2A4060;border-radius:10px;padding:12px 14px;">'
            f'<div style="color:#7A8FA0;font-size:10px;letter-spacing:0.1em;'
            f'text-transform:uppercase;">{label}</div>'
            f'<div style="color:{color};font-size:22px;font-weight:800;'
            f'margin-top:4px;">{value}</div>'
            f'<div style="color:#7A8FA0;font-size:11px;margin-top:2px;">'
            f'{sub}</div></div>'
        )
    st.markdown(
        '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;">'
        + "".join([
            _kpi("OPEN PRs",  str(len(pr_q)),  "awaiting PO"),
            _kpi("OPEN POs",  str(len(po_df)), "in pipeline", "#0EA5E9"),
            _kpi("ACTIVE DNs", str(len(dn_df)), "in transit", "#10B981"),
            _kpi("VENDOR RETURNS", str(len(ret_df)), "open",   "#F59E0B"),
            _kpi("RESCHEDULES", str(len(rs_df)), "awaiting decision", "#F59E0B"),
            _kpi("FORCE-CLOSURES", str(len(fc_df)), "lifetime audit", "#EF4444"),
        ]) + '</div>',
        unsafe_allow_html=True,
    )

    # ── Filters ──────────────────────────────────────────────────────────
    fA, fB = st.columns([1, 1])
    with fA:
        sites = ["All sites"] + sorted(get_sites() or [])
        site_pick = st.selectbox("Site", sites, key="_admin_overs_site")
    with fB:
        wh_df = list_warehouses()
        wh_options = ["All warehouses"] + (
            wh_df["Warehouse_ID"].tolist() if not wh_df.empty else []
        )
        wh_pick = st.selectbox("Warehouse", wh_options,
                                key="_admin_overs_wh")

    def _filter_by_site(df, col="Site_ID"):
        if site_pick != "All sites" and col in df.columns:
            return df[df[col] == site_pick].reset_index(drop=True)
        return df

    def _filter_by_wh(df, col="Warehouse_ID"):
        if wh_pick != "All warehouses" and col in df.columns:
            return df[df[col] == wh_pick].reset_index(drop=True)
        return df

    # ── Sub-tabs: PRs / POs / DNs / Returns / Closures / Reschedules ─────
    sA, sB, sC, sD, sE, sF = st.tabs([
        "📥 PRs", "📋 POs", "🚚 DNs",
        "↩️ Vendor Returns", "🛑 Force-Closures", "🔁 Reschedules",
    ])
    with sA:
        df = _filter_by_site(pr_q)
        if df.empty:
            render_empty_state(icon="📭", title="No PRs match filter")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    with sB:
        df = _filter_by_site(po_df)
        if df.empty:
            render_empty_state(icon="📦", title="No POs match filter")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    with sC:
        df = _filter_by_site(dn_df)
        df = _filter_by_wh(df)
        if df.empty:
            render_empty_state(icon="🛣️", title="No DNs match filter")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    with sD:
        if ret_df.empty:
            render_empty_state(icon="↩️", title="No open vendor returns")
        else:
            st.dataframe(ret_df, use_container_width=True, hide_index=True)
    with sE:
        df = fc_df
        if site_pick != "All sites" and "Site_ID" in df.columns:
            df = df[df["Site_ID"] == site_pick].reset_index(drop=True)
        if df.empty:
            render_empty_state(icon="🛑", title="No force-closures recorded")
        else:
            st.dataframe(df.head(100), use_container_width=True,
                         hide_index=True)
    with sF:
        if rs_df.empty:
            render_empty_state(icon="🔁",
                               title="No pending reschedule requests")
        else:
            st.dataframe(rs_df, use_container_width=True, hide_index=True)
