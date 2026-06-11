"""
pages_internal/reports_page.py — Reports & Analytics (Claude Design adapt)
==========================================================================
Site filtering rule:
  • HOD       → forced to user.site_id (own site only)
  • Admin     → 'All Sites' default, can pick any site
  • Supervisor → 'All Sites' default, can pick any site

Preservation:
  • The existing AI Executive Summary panel still ships (now lives inside
    the Generate Report tab so the legacy flow keeps working).
  • The legacy mailer-driven xlsx EOD/Monthly/Low-Stock buttons remain
    available via "📧 Quick EOD Email" inside the Generate tab.
"""

from __future__ import annotations

import datetime
import html
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from config import AI_ENABLED
from database import (
    get_connection,
    report_daily_consumption,
    report_daily_receipts,
    report_monthly_summary,
    report_pr_status,
    report_fefo_compliance,
    report_audit_export,
    add_report_schedule,
    list_report_schedules,
    toggle_report_schedule,
    delete_report_schedule,
    mark_schedule_run,
    add_archive_entry,
    list_archive,
    delete_archive_entry,
    log_audit_action,
    set_app_setting,
    get_app_setting,
    get_low_stock_items,
    get_short_dated_stock,
)
from cache_layer import (
    cached_sites,
    cached_low_stock_items,
    cached_short_dated_stock,
    cached_burn_rate_and_forecast,
)
from ui_components import (
    render_brand_header,
    render_empty_state,
    render_hero_metrics,
    status_pill_html,
)
from reports import (
    generate_report_pdf,
    generate_report_excel,
    generate_report_csv,
)

# Claude design tokens — kept in sync with the Reports.html mock.
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

_REPORT_TYPES = [
    ("daily",     "📋", "Daily Consumption",   "All material issues for a given day/range, by work type"),
    ("receipts",  "📥", "Daily Receipts",      "All material receipts for a given day/range — by supplier + lot + SAR value"),
    ("monthly",   "📅", "Monthly Summary",     "Per-SAP opening, issued, received, closing + SAR value"),
    ("lowstock",  "⚠️", "Low Stock Alert",     "Materials below minimum — reorder recommendations"),
    ("burnrate",  "📈", "Burn Rate Analysis",  "30-day consumption trends, days-of-supply per item"),
    ("valuation", "💰", "Inventory Valuation", "Stock × Unit_Cost (standard cost) — SAR rollup"),
    ("expiry",    "🏷️", "Shelf-Life / Expiry", "Lots by expiry date — expired, critical, warning"),
    ("pr",        "📋", "PR Status Report",    "All purchase requests and their current state"),
    ("fefo",      "✅", "FEFO Compliance",     "First-expiry-first-out adherence audit"),
    ("audit",     "📜", "Full Audit Report",   "Complete system audit log for a date range"),
]
_REPORT_TYPE_MAP = {t[0]: t for t in _REPORT_TYPES}


# ---------------------------------------------------------------------------
# Site selector — enforces HOD = own site, admin = pick any
# ---------------------------------------------------------------------------
def _site_filter(user: dict, key_suffix: str = "default") -> tuple[str | None, str]:
    """
    Returns (site_id_or_None, site_label).

    Role hierarchy: store_keeper < supervisor < hod < admin
      • Admin            → can pick any site (dropdown), "All Sites" default
      • HOD + Supervisor → locked to their own site
      • Store Keeper     → locked to their own site (kept for future use)

    `key_suffix` namespaces the selectbox key so the helper can be called
    once per tab (Generate, AI Insights, …) without colliding.
    """
    role = user.get("role", "")
    own = user.get("site_id", "HQ")
    if role != "admin":
        role_label = {"hod": "HOD", "supervisor": "Supervisor",
                      "store_keeper": "Store Keeper"}.get(role, role.title())
        st.caption(f"🔒 Site scope locked to **{own}** ({role_label})")
        return own, own

    options = ["All Sites"] + (cached_sites() or [])
    pick = st.selectbox(
        "Site filter",
        options,
        index=0,
        key=f"_rep_site_{key_suffix}",
    )
    return (None if pick == "All Sites" else pick), pick


# ---------------------------------------------------------------------------
# Column hygiene — drop columns that have no data in the result set.
# ---------------------------------------------------------------------------
# Reports JOIN inventory + receipts + consumption + lots — many optional
# fields (Lot_Number, PR_Number, Expiry_Date, Supplier, etc.) are NULL
# for some report types. Showing all-empty columns makes the table look
# misaligned and wastes precious PDF/Excel width. We strip any column
# whose every row is null/blank/NaN, while always preserving the two
# identifier columns the user explicitly asked for.
_ALWAYS_KEEP = {"SAP_Code", "Material_Code", "Date"}


_PLACEHOLDER_VALUES = {"", "None", "nan", "NaT", "<NA>", "null", "NULL"}


def _is_empty_value(v) -> bool:
    """True if `v` is empty for the purposes of column-drop."""
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() in _PLACEHOLDER_VALUES


def _strip_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    drop_cols: list[str] = []
    for col in df.columns:
        if col in _ALWAYS_KEEP:
            continue
        s = df[col]
        # Numeric all-zero columns ARE kept — zero is real data (e.g.
        # "0 units consumed"). Only strip truly empty / null / NaN /
        # placeholder-string columns.
        if pd.api.types.is_numeric_dtype(s):
            continue
        # Iterate rather than rely on .replace() — None ↔ string coercion
        # is finicky on object dtype and a single missed value would leave
        # the column in the report when the user wanted it gone.
        if all(_is_empty_value(v) for v in s):
            drop_cols.append(col)
    return df.drop(columns=drop_cols) if drop_cols else df


# ---------------------------------------------------------------------------
# Report execution — runs the right fetcher for a chosen report_type.
# Returns (df, summary_dict). Empty df is OK; the renderer handles it.
# ---------------------------------------------------------------------------
def _run_report(
    report_type: str,
    date_from: datetime.date,
    date_to: datetime.date,
    site_id: str | None,
) -> tuple[pd.DataFrame, dict]:
    df, summary = _run_report_raw(report_type, date_from, date_to, site_id)
    return _strip_empty_columns(df), summary


def _run_report_raw(
    report_type: str,
    date_from: datetime.date,
    date_to: datetime.date,
    site_id: str | None,
) -> tuple[pd.DataFrame, dict]:
    df_from, df_to = date_from.isoformat(), date_to.isoformat()
    if report_type == "daily":
        return report_daily_consumption(df_from, df_to, site_id=site_id)
    if report_type == "receipts":
        return report_daily_receipts(df_from, df_to, site_id=site_id)
    if report_type == "monthly":
        return report_monthly_summary(df_from, df_to, site_id=site_id)
    if report_type == "lowstock":
        conn = get_connection()
        try:
            df = get_low_stock_items(conn, site_id=site_id)
        finally:
            conn.close()
        if df is None or df.empty:
            return pd.DataFrame(), {"Below_Min": 0, "Critical": 0}
        sub = df.copy()
        cur = pd.to_numeric(sub.get("Current_Stock", 0), errors="coerce").fillna(0)
        minimum = pd.to_numeric(sub.get("Minimum_Qty", 0), errors="coerce").fillna(0)
        return df, {
            "Below_Min": int(len(df)),
            "Critical":  int(((cur <= 0)).sum()),
            "Shortfall_sum": float((cur - minimum).where(lambda x: x < 0).fillna(0).sum()),
        }
    if report_type == "burnrate":
        # NB: `df or default` triggers __bool__ on a DataFrame, which pandas
        # rejects as ambiguous. Use an explicit None check.
        df = cached_burn_rate_and_forecast(site_id=site_id)
        if df is None or df.empty:
            return pd.DataFrame(), {"Items": 0}
        days = pd.to_numeric(df.get("Days_Remaining", 0), errors="coerce").fillna(999)
        return df, {
            "Items":    int(len(df)),
            "Critical": int((days <= 7).sum()),
            "Low":      int(((days > 7) & (days <= 30)).sum()),
            "OK":       int((days > 30).sum()),
        }
    if report_type == "expiry":
        conn = get_connection()
        try:
            df = get_short_dated_stock(conn, site_id=site_id)
        finally:
            conn.close()
        if df is None or df.empty:
            return pd.DataFrame(), {"Expired": 0, "Critical": 0, "Warning": 0}
        # Buckets derived inline from Expiry_Date
        today = pd.Timestamp.today().normalize()
        exp = pd.to_datetime(df.get("Expiry_Date"), errors="coerce")
        days_left = (exp - today).dt.days
        return df, {
            "Expired":  int((days_left < 0).sum()),
            "Critical": int(((days_left >= 0) & (days_left <= 30)).sum()),
            "Warning":  int(((days_left > 30) & (days_left <= 90)).sum()),
        }
    if report_type == "pr":
        return report_pr_status(site_id=site_id)
    if report_type == "fefo":
        return report_fefo_compliance(df_from, df_to, site_id=site_id)
    if report_type == "audit":
        return report_audit_export(df_from, df_to)
    if report_type == "valuation":
        # Standard-cost inventory valuation. Date filter is informational
        # only (live snapshot — we don't have point-in-time stock history).
        from database import get_inventory_valuation, format_sar
        df = get_inventory_valuation(site_id=site_id)
        if df is None or df.empty:
            return pd.DataFrame(), {"Items": 0, "Total_Value_SAR": 0}
        # Sort by Stock_Value desc — biggest exposure first.
        df = df.sort_values("Stock_Value", ascending=False).reset_index(drop=True)
        total_value   = float(df["Stock_Value"].sum())
        items_with_cost = int((df["Unit_Cost"] > 0).sum())
        items_no_cost   = int((df["Unit_Cost"] == 0).sum())
        # If you sum the top 10 vs the total, you see concentration risk.
        top10_share = (
            float(df.head(10)["Stock_Value"].sum() / total_value * 100)
            if total_value > 0 else 0
        )
        return df, {
            "Items":           int(len(df)),
            "Items_with_cost": items_with_cost,
            "Items_no_cost":   items_no_cost,
            "Total_Value":     format_sar(total_value),
            "Top10_share":     f"{top10_share:.0f}%",
        }
    return pd.DataFrame(), {}


# ---------------------------------------------------------------------------
# Format encoders + archive helper (saves to disk + indexes in DB)
# ---------------------------------------------------------------------------
_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "reports_archive"


def _encode_report(
    *,
    report_type: str,
    fmt: str,
    df: pd.DataFrame,
    summary: dict,
    site_label: str,
    date_from: datetime.date,
    date_to: datetime.date,
    generated_by: str,
) -> tuple[bytes, str, str]:
    """Returns (bytes, mime, suggested_filename)."""
    rt = _REPORT_TYPE_MAP.get(report_type, _REPORT_TYPES[0])
    title = f"{rt[2]} — {site_label}"
    subtitle = (f"{date_from.isoformat()} → {date_to.isoformat()}"
                if report_type not in ("pr", "lowstock") else "")
    fname_base = (
        f"GI_{report_type}_{date_from.strftime('%Y%m%d')}_"
        f"{date_to.strftime('%Y%m%d')}_{site_label.replace(' ','_')}"
    )

    if fmt == "PDF":
        return (
            generate_report_pdf(
                title=title, df=df, generated_by=generated_by,
                site_label=site_label,
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                subtitle=subtitle,
                summary=summary,
            ),
            "application/pdf",
            f"{fname_base}.pdf",
        )
    if fmt == "Excel":
        return (
            generate_report_excel(title=title, df=df, summary=summary,
                                  sheet_name=report_type.title()),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            f"{fname_base}.xlsx",
        )
    if fmt == "CSV":
        return generate_report_csv(df), "text/csv", f"{fname_base}.csv"
    raise ValueError(f"Unknown format: {fmt}")


def _save_to_archive(
    *,
    name: str,
    report_type: str,
    generated_by: str,
    fmt: str,
    payload: bytes,
    site_label: str,
    date_from: datetime.date,
    date_to: datetime.date,
) -> Path:
    """Write payload to reports_archive/ and index the row."""
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = {"PDF": "pdf", "Excel": "xlsx", "CSV": "csv"}.get(fmt, "bin")
    fpath = _ARCHIVE_DIR / f"{report_type}_{ts}.{ext}"
    fpath.write_bytes(payload)
    add_archive_entry(
        name=name,
        report_type=report_type,
        generated_by=generated_by,
        format=fmt,
        size_bytes=len(payload),
        file_path=str(fpath),
        site_id=site_label,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
    )
    return fpath


# ===========================================================================
# TAB 1 — GENERATE REPORT
# ===========================================================================
def _render_generate_tab(user: dict) -> None:
    st.markdown(
        f'<div style="color:{_C["dim"]};font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.08em;margin:0 0 8px 0;">'
        f'1. Select Report Type</div>',
        unsafe_allow_html=True,
    )

    if "_rep_type" not in st.session_state:
        st.session_state["_rep_type"] = "daily"
    sel = st.session_state["_rep_type"]

    grid_cols = st.columns(4)
    for i, (rid, icon, label, desc) in enumerate(_REPORT_TYPES):
        with grid_cols[i % 4]:
            is_active = sel == rid
            if st.button(
                f"{icon}  {label}",
                key=f"_rep_card_{rid}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
                help=desc,
            ):
                st.session_state["_rep_type"] = rid
                st.session_state.pop("_rep_last_result", None)
                st.rerun()

    rt = _REPORT_TYPE_MAP[st.session_state["_rep_type"]]
    st.caption(f"**{rt[2]}** — {rt[3]}")

    st.markdown(
        f'<div style="color:{_C["dim"]};font-size:11px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.08em;margin:16px 0 8px 0;">'
        f'2. Configure</div>',
        unsafe_allow_html=True,
    )

    today = datetime.date.today()
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        date_from = st.date_input(
            "From date",
            value=today - datetime.timedelta(days=30),
            key="_rep_from",
        )
    with c2:
        date_to = st.date_input("To date", value=today, key="_rep_to")
    with c3:
        site_id, site_label = _site_filter(user, key_suffix="generate")
    with c4:
        fmt = st.selectbox(
            "Format", ["PDF", "Excel", "CSV"], key="_rep_fmt",
        )

    # Run + preview
    if st.button("▶ Generate Report", type="primary", key="_rep_run"):
        if date_from > date_to:
            st.error("From date must be before To date.")
        else:
            with st.spinner("Querying the warehouse ledger…"):
                df, summary = _run_report(
                    st.session_state["_rep_type"], date_from, date_to, site_id,
                )
            st.session_state["_rep_last_result"] = {
                "df": df, "summary": summary, "type": rt[0],
                "fmt": fmt, "from": date_from, "to": date_to,
                "site_label": site_label,
            }
            st.toast("✅ Report ready — preview below", icon="📊")
            st.rerun()

    result = st.session_state.get("_rep_last_result")
    if not result:
        st.info("Pick a report type and click **Generate** to see the preview.")
        return

    # Preview header
    rt2 = _REPORT_TYPE_MAP[result["type"]]
    st.write("")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin-bottom:10px;">'
        f'<div>'
        f'<h3 style="color:{_C["text"]};font-size:15px;font-weight:600;margin:0;">'
        f'{rt2[1]} {html.escape(rt2[2])}</h3>'
        f'<div style="color:{_C["dim"]};font-size:12px;margin-top:2px;">'
        f'{result["from"].isoformat()} → {result["to"].isoformat()} · '
        f'{html.escape(str(result["site_label"]))} · {result["fmt"]}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # Summary strip
    summary = result["summary"] or {}
    if summary:
        cards = []
        for k, v in list(summary.items())[:4]:
            label = str(k).replace("_", " ").title()
            v_str = (f"{v:,.0f}" if isinstance(v, (int, float)) and v == v else str(v))
            cards.append({
                "label": label,
                "value": v_str,
                "tone":  "critical" if str(v).upper() in ("URGENT", "EMPTY", "EXPIRED")
                          else ("low" if "critical" in label.lower() or "expired" in label.lower()
                                else "neutral"),
            })
        render_hero_metrics(cards)
        st.write("")

    # Preview table
    df_preview = result["df"]
    if df_preview is None or df_preview.empty:
        render_empty_state(
            icon="📭",
            title="No data in this window",
            hint="Try widening the date range or picking another site.",
        )
    else:
        show = df_preview.head(50)
        st.dataframe(show, hide_index=True, width="stretch")
        if len(df_preview) > 50:
            st.caption(f"Showing 50 of {len(df_preview):,} rows. Full file in download.")

    # Download + email + WhatsApp + archive row
    st.divider()
    payload, mime, fname = _encode_report(
        report_type=result["type"],
        fmt=result["fmt"],
        df=df_preview,
        summary=summary,
        site_label=str(result["site_label"]),
        date_from=result["from"],
        date_to=result["to"],
        generated_by=user.get("username", "anonymous"),
    )

    dl_col, save_col, em_col, wa_col = st.columns([1, 1, 1, 1])
    with dl_col:
        st.download_button(
            f"↓ Download {result['fmt']}",
            data=payload,
            file_name=fname,
            mime=mime,
            type="primary",
            use_container_width=True,
            key="_rep_dl",
        )
    with save_col:
        if st.button("📁 Save to Archive", use_container_width=True, key="_rep_arc"):
            try:
                fpath = _save_to_archive(
                    name=f"{rt2[2]} — {result['from'].isoformat()} to {result['to'].isoformat()}",
                    report_type=result["type"],
                    generated_by=user.get("username", "anonymous"),
                    fmt=result["fmt"],
                    payload=payload,
                    site_label=str(result["site_label"]),
                    date_from=result["from"],
                    date_to=result["to"],
                )
                log_audit_action(
                    user["username"], "ARCHIVE_REPORT", "report_archive",
                    f"type={result['type']} fmt={result['fmt']}",
                )
                st.toast(f"✅ Archived → {fpath.name}", icon="📁")
            except Exception as e:
                st.error(f"Archive failed: {e}")
    with em_col:
        if st.button("📧 Email", use_container_width=True, key="_rep_em"):
            st.session_state["_rep_show_email"] = True
    with wa_col:
        if st.button("📱 WhatsApp", use_container_width=True, key="_rep_wa"):
            st.session_state["_rep_show_wa"] = True

    if st.session_state.get("_rep_show_email"):
        recipients = st.text_input(
            "Recipients (comma-separated)",
            key="_rep_em_to",
            placeholder="hod@generalind.sa, supervisor@generalind.sa",
        )
        if st.button("Send email draft", key="_rep_em_send", type="primary"):
            if not recipients.strip():
                st.error("At least one recipient is required.")
            else:
                try:
                    from mailer import send_eod_report, parse_recipients
                    # The xlsx EOD path expects a date — reuse only when type=daily
                    if result["type"] == "daily":
                        ok, msg = send_eod_report(
                            parse_recipients(recipients), report_date=result["from"],
                        )
                    else:
                        ok, msg = False, (
                            "Email auto-send is wired for the Daily report. "
                            "Use Download → drag into your mail client for other types."
                        )
                    (st.success if ok else st.warning)(msg)
                except Exception as e:
                    st.error(f"Email failed: {e}")

    if st.session_state.get("_rep_show_wa"):
        st.caption(
            "WhatsApp delivery sends a text *summary* of this report to the "
            "selected number via the existing queue."
        )
        phone = st.text_input("Phone number (+966…)", key="_rep_wa_to")
        if st.button("Queue WhatsApp", key="_rep_wa_send", type="primary"):
            if not phone.strip():
                st.error("Phone number required.")
            else:
                try:
                    from database import queue_whatsapp_alert
                    msg_lines = [
                        f"📊 *{rt2[2]} — {result['site_label']}*",
                        f"📅 {result['from']} → {result['to']}",
                        "",
                    ]
                    for k, v in list(summary.items())[:6]:
                        msg_lines.append(f"• {k.replace('_',' ').title()}: {v}")
                    queue_whatsapp_alert(phone.strip(), "\n".join(msg_lines))
                    log_audit_action(
                        user["username"], "REPORT_WHATSAPP", "whatsapp_queue",
                        f"to={phone!r} type={result['type']}",
                    )
                    st.toast(f"📱 Queued for {phone}", icon="📱")
                except Exception as e:
                    st.error(f"WhatsApp failed: {e}")

    # AI executive summary (preserved from legacy reports page)
    if AI_ENABLED and result["type"] == "daily":
        st.divider()
        with st.expander("🤖 AI Executive Summary (legacy)", expanded=False):
            try:
                from ai.client import OLLAMA_AVAILABLE
                from ai.summarize import stream_eod_summary
                if not OLLAMA_AVAILABLE:
                    st.caption(
                        "Local AI server not reachable. Start `ollama serve` "
                        "and pull `llama3.1:8b` to enable this panel."
                    )
                else:
                    if st.button("✨ Generate Summary", key="_legacy_ai_btn"):
                        st.write_stream(stream_eod_summary(result["from"]))
            except Exception as e:
                st.caption(f"AI summary unavailable: {e}")


# ===========================================================================
# TAB 2 — SCHEDULED (UI-only + Run Now)
# ===========================================================================
def _render_scheduled_tab(user: dict) -> None:
    st.markdown(
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Define recurring report definitions and a target distribution list. '
        f'Click <b>Run Now</b> to execute a schedule immediately — no daemon '
        f'is wired, automatic delivery is not enabled.</p>',
        unsafe_allow_html=True,
    )

    # Create new schedule
    with st.expander("➕ New Scheduled Report", expanded=False):
        nc1, nc2, nc3, nc4 = st.columns(4)
        with nc1:
            sched_label = st.text_input("Label*", key="_sch_label",
                                        placeholder="e.g. Daily EOD email")
        with nc2:
            sched_type = st.selectbox(
                "Report type", [t[2] for t in _REPORT_TYPES],
                key="_sch_type",
            )
        with nc3:
            sched_freq = st.selectbox(
                "Frequency",
                ["Daily at 06:00", "Daily at 17:00", "Weekly Mon 07:00",
                 "1st of month 06:00"],
                key="_sch_freq",
            )
        with nc4:
            sched_fmt = st.selectbox(
                "Format", ["PDF", "Excel", "CSV"], key="_sch_fmt",
            )
        sched_recipients = st.text_input(
            "Recipients (email, comma-separated)*", key="_sch_rcpt",
            placeholder="hod@x.com, supervisor@x.com",
        )
        if st.button("➕ Create Schedule", key="_sch_create", type="primary"):
            if not sched_label.strip() or not sched_recipients.strip():
                st.error("Label and Recipients are required.")
            else:
                # Map back to type id
                type_id = next(
                    (t[0] for t in _REPORT_TYPES if t[2] == sched_type), "daily",
                )
                ok, msg = add_report_schedule(
                    label=sched_label, report_type=type_id, frequency=sched_freq,
                    recipients=sched_recipients, format=sched_fmt,
                    site_id=user.get("site_id") if user.get("role") == "hod" else None,
                    created_by=user.get("username", ""),
                )
                if ok:
                    log_audit_action(
                        user["username"], "CREATE_SCHEDULE", "report_schedules",
                        f"label={sched_label!r} type={type_id}",
                    )
                    st.toast("✅ Schedule created", icon="📅")
                    st.rerun()
                else:
                    st.error(msg)

    # Existing schedules
    df = list_report_schedules()
    if df.empty:
        render_empty_state(
            icon="📅", title="No schedules yet",
            hint="Use the form above to schedule a recurring report.",
        )
        return

    for _, r in df.iterrows():
        sid = int(r["id"])
        rt = _REPORT_TYPE_MAP.get(r["report_type"], _REPORT_TYPES[0])
        active = int(r.get("active") or 0) == 1
        bg = _C["surf"] if active else _C["surf"] + "88"
        opacity = "1" if active else "0.65"
        st.markdown(
            f'<div style="background:{bg};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:14px 16px;margin-bottom:10px;'
            f'display:flex;align-items:center;gap:14px;flex-wrap:wrap;'
            f'opacity:{opacity};">'
            f'<span style="font-size:20px;flex-shrink:0;">{rt[1]}</span>'
            f'<div style="flex:1;min-width:160px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;">'
            f'{html.escape(str(r["label"]))}</div>'
            f'<div style="color:{_C["dim"]};font-size:11.5px;margin-top:2px;">'
            f'{html.escape(str(r["frequency"]))} · {html.escape(str(r["recipients"]))} '
            f'· <span style="color:{_C["muted"]};">{rt[2]}</span></div>'
            f'</div>'
            f'<div style="color:{_C["dim"]};font-size:11.5px;white-space:nowrap;">'
            f'Last: {html.escape(str(r.get("last_run") or "Never"))}</div>'
            f'<span>{status_pill_html("approved" if active else "rejected", "Active" if active else "Paused")}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        ac1, ac2, ac3 = st.columns([1, 1, 1])
        with ac1:
            if st.button(
                "Pause" if active else "Activate",
                key=f"_sch_toggle_{sid}",
                use_container_width=True,
            ):
                toggle_report_schedule(sid)
                st.rerun()
        with ac2:
            if st.button("▶ Run Now", key=f"_sch_run_{sid}",
                         use_container_width=True, type="primary"):
                today = datetime.date.today()
                # 7-day window for one-off runs as a sensible default
                date_from = today - datetime.timedelta(days=7)
                df_data, summary = _run_report(
                    r["report_type"], date_from, today,
                    r.get("site_id"),
                )
                fpath = None
                try:
                    payload, mime, fname = _encode_report(
                        report_type=r["report_type"], fmt=r["format"],
                        df=df_data, summary=summary,
                        site_label=str(r.get("site_id") or "All Sites"),
                        date_from=date_from, date_to=today,
                        generated_by=user.get("username", "system"),
                    )
                    fpath = _save_to_archive(
                        name=f"[Scheduled] {r['label']} — {today.isoformat()}",
                        report_type=r["report_type"],
                        generated_by=user.get("username", "system"),
                        fmt=r["format"],
                        payload=payload,
                        site_label=str(r.get("site_id") or "All Sites"),
                        date_from=date_from, date_to=today,
                    )
                    mark_schedule_run(sid)
                    log_audit_action(
                        user["username"], "RUN_SCHEDULE", "report_schedules",
                        f"id={sid} → {fpath.name}",
                    )
                    st.toast(f"✅ Run complete → {fpath.name}", icon="▶")
                    st.rerun()
                except Exception as e:
                    st.error(f"Run failed: {e}")
        with ac3:
            if st.button("🗑️ Delete", key=f"_sch_del_{sid}",
                         use_container_width=True):
                delete_report_schedule(sid)
                st.rerun()


# ===========================================================================
# TAB 3 — AI INSIGHTS (llama3.1 + fixed SQL probes)
# ===========================================================================
def _render_ai_insights_tab(user: dict) -> None:
    site_id, site_label = _site_filter(user, key_suffix="ai")
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin-bottom:14px;flex-wrap:wrap;gap:8px;">'
        f'<div>'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;">'
        f'<span style="font-size:18px;">🤖</span>'
        f'<span style="color:{_C["text"]};font-size:14px;font-weight:600;">'
        f'AI-Powered Inventory Analysis</span>'
        f'<span style="background:{_C["purple"]}22;border:1px solid {_C["purple"]}55;'
        f'color:{_C["purple"]};font-size:10px;font-weight:700;padding:2px 7px;'
        f'border-radius:4px;">BETA</span>'
        f'</div>'
        f'<div style="color:{_C["muted"]};font-size:12.5px;">'
        f'Findings from your transaction history, stock levels, burn rates '
        f'and compliance data — re-run any time.</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    if not AI_ENABLED:
        st.warning(
            "AI is disabled in this build. Set `AI_ENABLED = True` in `config.py`."
        )
        return

    try:
        from ai.client import OLLAMA_AVAILABLE
    except Exception:
        OLLAMA_AVAILABLE = False

    if not OLLAMA_AVAILABLE:
        st.markdown(
            f'<div style="background:{_C["low"]}10;border:1px solid {_C["low"]}33;'
            f'border-radius:8px;padding:10px 14px;color:{_C["low"]};font-size:13px;">'
            f'⚠️ Ollama is not reachable. Start <code>ollama serve</code> and '
            f'pull <code>{html.escape(get_app_setting("ai_insights_model", "llama3.1"))}</code>. '
            f'Probes will still compute metrics — only the narrative commentary '
            f'will be unavailable.</div>',
            unsafe_allow_html=True,
        )

    if st.button("🔄 Regenerate Insights", type="primary",
                 key="_ai_regen", use_container_width=False):
        with st.spinner(f"Analysing data for {site_label}…"):
            from ai.insights import build_insights
            st.session_state["_ai_insights"] = build_insights(site_id=site_id)
        st.rerun()

    insights = st.session_state.get("_ai_insights")
    if insights is None:
        st.info("Click **Regenerate Insights** to start the analysis.")
        return
    if not insights:
        render_empty_state(
            icon="🤖",
            title="No notable signals right now",
            hint="The probes ran but found no spikes, stockouts, or expirations.",
        )
        return

    for ins in insights:
        sev = ins.get("severity", "ok")
        col = {"crit": _C["crit"], "low": _C["low"], "ok": _C["ok"]}[sev]
        sev_lbl = {"crit": "Critical", "low": "Warning", "ok": "Positive"}[sev]
        with st.expander(
            f"{ins['icon']}  {ins['title']}  ·  {ins['metric']}",
            expanded=(sev == "crit"),
        ):
            st.markdown(
                f'<div style="display:flex;gap:10px;align-items:center;'
                f'flex-wrap:wrap;margin-bottom:8px;">'
                f'<span style="background:{col}18;border:1px solid {col}44;color:{col};'
                f'font-size:10px;font-weight:700;padding:1px 7px;border-radius:3px;">'
                f'{sev_lbl}</span>'
                f'<span style="color:{_C["dim"]};font-size:11px;">'
                f'Confidence: {ins["confidence"]}%</span>'
                f'<div style="flex:1;background:{_C["border"]};border-radius:2px;height:4px;max-width:120px;">'
                f'<div style="width:{ins["confidence"]}%;height:100%;border-radius:2px;'
                f'background:linear-gradient(90deg,{col},{col}AA);"></div></div>'
                f'<span style="color:{col};font-size:13px;font-weight:700;margin-left:auto;">'
                f'{html.escape(str(ins["metric"]))} · {html.escape(str(ins["metric_label"]))}</span>'
                f'</div>'
                f'<p style="color:{_C["muted"]};font-size:13px;line-height:1.7;margin:6px 0 10px 0;">'
                f'{html.escape(ins["body"])}</p>'
                f'<div style="color:{_C["text"]};font-size:12px;font-weight:600;margin-bottom:6px;">'
                f'💡 Recommendations:</div>',
                unsafe_allow_html=True,
            )
            for i, rec in enumerate(ins["recs"], 1):
                st.markdown(
                    f'<div style="display:flex;gap:8px;align-items:flex-start;'
                    f'padding:7px 10px;background:{_C["surf2"]};border-radius:6px;'
                    f'border:1px solid {_C["border"]}66;margin-bottom:5px;">'
                    f'<span style="color:{_C["gold"]};font-weight:700;'
                    f'flex-shrink:0;font-size:12px;">{i}.</span>'
                    f'<span style="color:{_C["text"]};font-size:12.5px;line-height:1.5;">'
                    f'{html.escape(rec)}</span></div>',
                    unsafe_allow_html=True,
                )


# ===========================================================================
# TAB 4 — ARCHIVE (disk-backed, with re-download / re-send / delete)
# ===========================================================================
def _render_archive_tab(user: dict) -> None:
    st.markdown(
        f'<p style="color:{_C["muted"]};font-size:12.5px;margin:0 0 14px 0;">'
        f'Previously generated reports stored on disk + indexed in DB. HODs '
        f'see archives scoped to their site; admins see everything.</p>',
        unsafe_allow_html=True,
    )

    role = user.get("role", "")
    site_scope = user.get("site_id") if role == "hod" else None

    search = st.text_input(
        "Search archive (name / type)",
        key="_arc_search", placeholder="e.g. daily, monthly, MAT-…",
    )

    df = list_archive(site_filter=None, type_filter=None, limit=500)
    if site_scope and not df.empty:
        df = df[df["site_id"].isin([site_scope, "All Sites", None])]
    if search.strip() and not df.empty:
        s = search.strip().lower()
        df = df[
            df["name"].astype(str).str.lower().str.contains(s, na=False)
            | df["report_type"].astype(str).str.lower().str.contains(s, na=False)
        ]

    if df.empty:
        render_empty_state(
            icon="📁", title="Archive is empty",
            hint="Generate a report in the **Generate Report** tab, then click **Save to Archive**.",
        )
        return

    for _, r in df.iterrows():
        aid = int(r["id"])
        rt = _REPORT_TYPE_MAP.get(r["report_type"], ("?", "📄", r["report_type"], ""))
        size_kb = (int(r["size_bytes"]) / 1024) if r["size_bytes"] else 0
        size_lbl = (f"{size_kb / 1024:.1f} MB" if size_kb > 1024 else f"{size_kb:.0f} KB")
        st.markdown(
            f'<div style="background:{_C["surf"]};border:1px solid {_C["border"]};'
            f'border-radius:10px;padding:12px 16px;margin-bottom:8px;'
            f'display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
            f'<span style="font-size:20px;flex-shrink:0;">{rt[1]}</span>'
            f'<div style="flex:1;min-width:200px;">'
            f'<div style="color:{_C["text"]};font-size:13px;font-weight:600;">'
            f'{html.escape(str(r["name"]))}</div>'
            f'<div style="color:{_C["dim"]};font-size:11.5px;margin-top:2px;">'
            f'{html.escape(str(r["generated_at"])[:19])} · '
            f'<span style="color:{_C["gold"]};font-family:monospace;">'
            f'{html.escape(str(r["generated_by"]))}</span> · '
            f'<span style="color:{_C["muted"]};">{html.escape(str(r["format"]))}</span> · '
            f'{size_lbl}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        a1, a2 = st.columns([1, 1])
        with a1:
            try:
                if r["file_path"] and os.path.exists(r["file_path"]):
                    with open(r["file_path"], "rb") as fp:
                        data = fp.read()
                    mime_map = {"PDF": "application/pdf",
                                "Excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                "CSV": "text/csv"}
                    st.download_button(
                        "↓ Download",
                        data=data,
                        file_name=Path(r["file_path"]).name,
                        mime=mime_map.get(r["format"], "application/octet-stream"),
                        key=f"_arc_dl_{aid}",
                        use_container_width=True,
                    )
                else:
                    st.caption(f"⚠️ File missing on disk: {r['file_path']}")
            except Exception as e:
                st.caption(f"⚠️ {e}")
        with a2:
            if st.button("🗑️ Delete", key=f"_arc_del_{aid}", use_container_width=True):
                ok, msg = delete_archive_entry(aid)
                if ok:
                    log_audit_action(
                        user["username"], "DELETE_ARCHIVE", "report_archive",
                        f"id={aid}",
                    )
                    st.toast("Deleted from archive", icon="🗑️")
                    st.rerun()


# ===========================================================================
# PAGE — top-level routing
# ===========================================================================
def page_reports(user: dict) -> None:
    render_brand_header("Reports & Analytics")
    st.markdown(
        f'<h1 style="color:{_C["text"]};font-size:21px;font-weight:700;'
        f'letter-spacing:-0.02em;margin:0 0 14px 0;">📊 Reports</h1>',
        unsafe_allow_html=True,
    )

    tabs = st.tabs([
        "📊 Generate Report", "📅 Scheduled",
        "🤖 AI Insights", "📁 Archive",
    ])
    with tabs[0]: _render_generate_tab(user)
    with tabs[1]: _render_scheduled_tab(user)
    with tabs[2]: _render_ai_insights_tab(user)
    with tabs[3]: _render_archive_tab(user)
