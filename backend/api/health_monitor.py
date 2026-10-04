"""
backend/api/health_monitor.py — the Morning Briefing agent.

Every morning at GI_BRIEFING_HOUR (default 07:00 server-local, same wall-clock
convention as the 16:00 evening digest and the Friday 17:00 executive report)
this scans the operational tables for things that have gone quiet in a bad way
— work that is waiting on somebody, stock that cannot move, gear that is about
to expire — and dispatches one digest per recipient scope:

  · one ALL-SITES briefing to every admin, and
  · one site-scoped briefing per distinct HOD site.

WHY A SEPARATE AGENT RATHER THAN MORE EVENT TRIGGERS. The existing
notifications all fire on a state CHANGE: a DN was submitted, goods were
received, a tool was lent. Every problem this agent finds is the ABSENCE of a
change — a draft nobody submitted, an inspection nobody performed, a tool
nobody brought back. Nothing happens, so no trigger fires, and the longer it
stays broken the quieter it gets. That class of fault needs something that
looks on a timer.

THREE DESIGN RULES, each of which is load-bearing:

1. **A probe may not break the briefing.** Every probe runs inside its own
   guard and a failure becomes a line in the digest naming the probe, not a
   lost run. A monitor that dies when one query breaks is worse than no
   monitor, because its silence reads as "all clear".

2. **Silence when there is nothing wrong.** A daily "all systems normal" is
   read for a week and ignored forever after, and the one morning it matters
   it looks like all the others. Nothing is dispatched on a clean run — but
   EVERY run, clean or not, writes an audit row, so "did the agent run?" is
   answerable without spending a recipient's attention to answer it.

3. **The body is ONE LINE.** Meta rejects template parameters containing
   newlines (#132000), and `dispatch()` sends the same title/body to the bell
   and to WhatsApp. The digest is •-separated on a single line for exactly the
   reason `notifications._compile_digest` is.

Manual trigger for ops and testing:
    GET  /health/briefing        preview (admin sees all sites, HOD their own)
    POST /admin/health/run       build AND dispatch now (admin)
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, require_roles, resolve_site_param
from .db import get_session
from .services.ledger import _MD, write_audit
from .services.notifications import dispatch

log = logging.getLogger("gi.health")
router = APIRouter(tags=["health"])

users_t = _MD.tables["users"]

# ── tunables ─────────────────────────────────────────────────────────────────
# Defaults live here; each is overridable per deployment through app_settings
# so an operator can retune the noise floor without a release. They are
# thresholds on ATTENTION, not on correctness — a DN in draft for two days is
# fine, one sitting there for a week is a forgotten truck.
_DEFAULTS = {
    "health_dn_draft_days":        3,    # drafts nobody submitted
    "health_ppe_hours":            48,   # PPE about to expire
    "health_pending_approval_days": 2,   # staged entries awaiting an HOD
    "health_pr_stale_days":        5,    # PRs submitted, no PO raised
    "health_outbox_hours":         24,   # failed messages worth chasing
    "health_max_examples":         5,    # named examples per finding
}


async def _setting_int(session: AsyncSession, key: str) -> int:
    """An app_settings integer, falling back to the built-in default.

    A malformed value falls back rather than raising: this is a monitoring
    agent, and a typo in a settings row must not be the reason nobody hears
    about a week-old draft.
    """
    default = _DEFAULTS[key]
    raw = (await session.execute(
        text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})).scalar()
    try:
        return int(str(raw).strip()) if raw is not None else default
    except (TypeError, ValueError):
        log.warning("health: app_settings[%s]=%r is not an integer — using %d",
                    key, raw, default)
        return default


async def _enabled(session: AsyncSession) -> bool:
    raw = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = 'health_briefing_enabled'"))).scalar()
    return (raw if raw is not None else "1").strip() != "0"


# ── findings ─────────────────────────────────────────────────────────────────
def _finding(key: str, title: str, severity: str, items: list[str],
             link_page: str, detail: str | None = None) -> dict | None:
    """Build a finding, or None when the probe found nothing.

    Returning None for "nothing wrong" is what lets the caller stay silent on a
    clean run without every probe repeating the same emptiness check.
    """
    if not items:
        return None
    return {"key": key, "title": title, "severity": severity,
            "count": len(items), "items": items, "link_page": link_page,
            "detail": detail or "; ".join(items[:3])}


def _site_clause(site: Optional[str], col: str = '"Site_ID"') -> tuple[str, dict]:
    """SQL fragment + params narrowing a probe to one site.

    ⚠️ `site is None` means UNRESTRICTED (admin), while `site == ''` means a
    scoped caller with no site of their own and must match NOTHING. Collapsing
    the two with `if site:` is precisely how a site-less account ends up
    reading every site — the same fail-closed rule the read scoping follows.
    """
    if site is None:
        return "", {}
    return f" AND COALESCE({col}, '') = :site ", {"site": site}


async def probe_dn_draft_stale(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Delivery Notes left in draft — a truck nobody loaded.

    Auto-drafted DNs land in `draft` deliberately (the system cannot know a
    vehicle exists), which makes this the one queue that grows quietly by
    design. It is the reason the probe exists.
    """
    days = await _setting_int(session, "health_dn_draft_days")
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site)
    rows = (await session.execute(text(f"""
        SELECT "DN_Number", "Site_ID", "created_at", auto_generated
          FROM delivery_notes
         WHERE COALESCE(status, 'draft') = 'draft'
           AND created_at < CURRENT_TIMESTAMP - make_interval(days => :days)
           {clause}
         ORDER BY created_at
    """), {"days": days, **params})).all()
    items = [f"{r.DN_Number} ({r.Site_ID}, "
             f"{'auto-drafted' if r.auto_generated else 'manual'}, "
             f"{(_dt.datetime.now() - r.created_at).days}d)" for r in rows]
    return _finding(
        "dn_draft_stale", f"{len(items)} Delivery Note(s) stuck in draft >{days}d",
        "warning", items[:cap], "/warehouse",
        detail=(f"Add vehicle and driver, then submit: "
                f"{', '.join(i.split(' ')[0] for i in items[:cap])}"
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_ppe_expiring(session: AsyncSession, site: Optional[str]) -> dict | None:
    """PPE whose usable time runs out within the window.

    ⚠️ Expiry is a SUGGESTED REPLACEMENT DATE, never a restriction (operator
    ruling Q5) — this is a procurement prompt for the store, not an alert about
    a worker, and it must never read as one. The names are here because the
    operator asked to see whose gear it is: a list of quantities cannot be
    checked by a human and a list of names can.
    """
    hours = await _setting_int(session, "health_ppe_hours")
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site)
    horizon = (_dt.date.today() + _dt.timedelta(days=max(1, hours // 24))).isoformat()
    rows = (await session.execute(text(f"""
        SELECT "SAP_Code", "Description", employee_name, employee_id_number,
               expires_on, "Site_ID"
          FROM ppe_distributions
         WHERE status = 'active' AND expires_on IS NOT NULL
           AND expires_on <= :horizon
           {clause}
         ORDER BY expires_on
    """), {"horizon": horizon, **params})).all()
    items = [f"{r.employee_name or r.employee_id_number}: "
             f"{r.Description or r.SAP_Code} (expires {r.expires_on})" for r in rows]
    return _finding(
        "ppe_expiring", f"{len(items)} PPE item(s) reaching their replacement date",
        "info", items[:cap], "/ppe/forecast",
        detail=("Suggested replacements, not a restriction — "
                + "; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_qc_blocked_stock(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Controlled material sitting at a site that CANNOT be issued.

    This is the expensive-to-notice one. The Store Keeper only discovers it at
    the moment they try to issue and are refused; until then the stock looks
    perfectly available on every screen. The probe reuses
    `quality._cleared_totals` rather than reimplementing the arithmetic — if
    the gate's definition of "cleared" ever changes, this moves with it instead
    of quietly disagreeing.
    """
    from .services import quality
    cap = await _setting_int(session, "health_max_examples")
    category = await quality.controlled_category(session)
    clause, params = _site_clause(site, 'a."Site_ID"')
    # On-hand per (site, SAP) for the controlled category only — the same
    # receipts − consumption − returns arithmetic the stock views use.
    rows = (await session.execute(text(f"""
        WITH activity AS (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS "Site_ID",
                   SUM("Quantity") AS rec, 0 AS con, 0 AS ret
              FROM receipts    GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, SUM("Quantity"), 0
              FROM consumption GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, 0, SUM("Quantity")
              FROM returns     GROUP BY 1, 2
        )
        SELECT a.sap, a."Site_ID" AS site,
               SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS on_hand
          FROM activity a
          JOIN inventory i ON TRIM(i."SAP_Code") = a.sap
         WHERE i."Category" = :cat {clause}
         GROUP BY a.sap, a."Site_ID"
        HAVING SUM(a.rec) - SUM(a.con) - SUM(a.ret) > 0
    """), {"cat": category, **params})).all()

    items: list[str] = []
    for r in rows:
        t = await quality._cleared_totals(session, sap=r.sap, site=r.site)
        blocked = float(r.on_hand) - float(t["available_for_issue"])
        if blocked > 1e-9:
            why = ("never inspected" if t["inspections"] == 0
                   else f"{t['pending_inspections']} inspection(s) pending"
                   if t["approved_qty"] <= 1e-9 else "approved quantity exhausted")
            items.append(f"{r.sap} at {r.site}: {blocked:g} blocked ({why})")
    return _finding(
        "qc_blocked_stock", f"{len(items)} controlled material(s) cannot be issued",
        "critical", items[:cap], "/qc/inspections",
        detail=("Surface Shields on the shelf that QC has not released — "
                + "; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def missing_mtc_rows(session: AsyncSession, site: Optional[str]) -> list[dict]:
    """Surface Shields with stock and no certificate, split by WHERE they are.

    Two populations, because they are two different problems with two
    different sets of people who can fix them:

      site       on-hand at a site (receipts − consumption − returns > 0) with
                 nothing that `visible_mtc` will accept. This is material a
                 store keeper will be refused on, at the counter, in front of
                 whoever is waiting for it.
      warehouse  delivered against a purchase order into a warehouse and not
                 yet covered. It has not blocked anybody YET — it will block
                 the receiving site the moment it is shipped, which is the
                 cheapest possible moment to have already fixed it.

    ⚠️ Both use the SAME resolvers the gate uses — `visible_mtc` for the site
    and `find_mtc` for the warehouse. Reimplementing "has a certificate" here
    would produce an alert that disagrees with the refusal, and an alert that
    names materials which are actually fine is one people learn to skip.

    `warn_mtc_missing` already fires at goods-in. This is the sweep that
    catches what nobody acted on — the receipt notification is a single event
    that scrolls away, and the material stays unissuable long after it does.
    """
    from .services import quality
    category = await quality.controlled_category(session)
    out: list[dict] = []

    # ── at a site ────────────────────────────────────────────────────────────
    clause, params = _site_clause(site, 'a."Site_ID"')
    rows = (await session.execute(text(f"""
        WITH activity AS (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS "Site_ID",
                   SUM("Quantity") AS rec, 0 AS con, 0 AS ret
              FROM receipts    GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, SUM("Quantity"), 0
              FROM consumption GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, 0, SUM("Quantity")
              FROM returns     GROUP BY 1, 2
        )
        SELECT a.sap, a."Site_ID" AS site,
               MAX(i."Equipment_Description") AS name,
               SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS on_hand
          FROM activity a
          JOIN inventory i ON TRIM(i."SAP_Code") = a.sap
         WHERE i."Category" = :cat {clause}
         GROUP BY a.sap, a."Site_ID"
        HAVING SUM(a.rec) - SUM(a.con) - SUM(a.ret) > 0
    """), {"cat": category, **params})).all()
    for r in rows:
        if await quality.visible_mtc(session, sap_code=r.sap, site_id=r.site) is None:
            out.append({"where": "site", "place": r.site, "sap": r.sap,
                        "name": r.name, "qty": float(r.on_hand)})

    # ── at a warehouse ───────────────────────────────────────────────────────
    # A warehouse holds stock as `po_items.Delivered_Qty` under an assignment,
    # not as ledger rows — the ledger starts at the receiving SITE. Site-scoped
    # briefings skip this half entirely: a warehouse is not inside any one
    # site, so attributing its stock to a site would be an invention.
    if site is None:
        wrows = (await session.execute(text("""
            SELECT a."Warehouse_ID" AS wh, pi.id AS po_item_id,
                   pi."Material_Code" AS mat, pi."Description" AS name,
                   (COALESCE(pi."Delivered_Qty",0) - COALESCE(pi."Returned_Qty",0)) AS qty
              FROM po_items pi
              JOIN po_assignments a ON a."PO_Number" = pi."PO_Number"
              JOIN inventory i ON i."Material_Code" = pi."Material_Code"
             WHERE i."Category" = :cat
               AND COALESCE(pi."Delivered_Qty",0) - COALESCE(pi."Returned_Qty",0) > 0
        """), {"cat": category})).all()
        for r in wrows:
            found = await quality.find_mtc(session, material_code=r.mat,
                                           warehouse_id=r.wh, po_item_id=r.po_item_id)
            if found is None:
                out.append({"where": "warehouse", "place": r.wh, "sap": r.mat,
                            "name": r.name, "qty": float(r.qty)})
    return out


async def probe_missing_mtc(session: AsyncSession, site: Optional[str]) -> dict | None:
    """The digest line. The role-targeted alerts are dispatched separately —
    see `dispatch_missing_mtc` — because the people who can FIX this are not
    the admins and HODs the briefing goes to."""
    cap = await _setting_int(session, "health_max_examples")
    rows = await missing_mtc_rows(session, site)
    items = [f"{r['sap']} ({r['name'] or '—'}) at {r['place']}: {r['qty']:g} "
             f"{'in the warehouse' if r['where'] == 'warehouse' else 'on site'}"
             for r in rows]
    return _finding(
        "missing_mtc", f"{len(items)} Surface Shield(s) have no Material Test Certificate",
        "critical", items[:cap], "/logistics",
        detail=("Controlled material that cannot be issued until a certificate "
                "is uploaded — " + "; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_returnables_overdue(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Tools past their expected return time.

    ⚠️ The per-loan overdue alert fires when somebody OPENS the Returnables
    list, not on a timer — so on a site where nobody opens that page, no alert
    has ever been sent. This probe is the only thing that surfaces those.
    """
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site)
    rows = (await session.execute(text(f"""
        SELECT id, material_name, borrower_name, expected_return_time, "Site_ID"
          FROM returnable_items
         WHERE status = 'borrowed'
           AND expected_return_time < CURRENT_TIMESTAMP
           {clause}
         ORDER BY expected_return_time
    """), params)).all()
    now = _dt.datetime.now()
    items = [f"{r.material_name} with {r.borrower_name or 'unnamed borrower'} "
             f"({max(0, (now - r.expected_return_time).days)}d overdue)" for r in rows]
    return _finding(
        "returnables_overdue", f"{len(items)} tool loan(s) overdue",
        "warning", items[:cap], "/entry/returnables",
        detail=("; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_pending_approvals(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Entries staged for an HOD and still waiting.

    Stock has not moved for any of these. A site that stops approving looks
    exactly like a site that stopped working.
    """
    days = await _setting_int(session, "health_pending_approval_days")
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site)
    rows = (await session.execute(text(f"""
        SELECT "SAP_Code", "Site_ID", "Timestamp", "Issued_By"
          FROM pending_issues
         WHERE COALESCE(status, 'draft') <> 'rejected'
           AND "Timestamp" < CURRENT_TIMESTAMP - make_interval(days => :days)
           {clause}
         ORDER BY "Timestamp"
    """), {"days": days, **params})).all()
    items = [f"{r.SAP_Code} at {r.Site_ID} from {r.Issued_By or 'unknown'} "
             f"({(_dt.datetime.now() - r.Timestamp).days}d)" for r in rows]
    return _finding(
        "pending_approvals", f"{len(items)} staged entr(y/ies) awaiting approval >{days}d",
        "warning", items[:cap], "/approvals",
        detail=("Stock has not moved for these — "
                + "; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_negative_stock(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Stock below zero — an impossible state, so a real data fault.

    More was issued than ever arrived. It is never self-correcting and it
    silently poisons every downstream number: reorder suggestions, valuations,
    the PPE forecast's netting. Reported as critical because it means one of
    the ledgers is wrong, not merely low.
    """
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site, 'a."Site_ID"')
    rows = (await session.execute(text(f"""
        WITH activity AS (
            SELECT TRIM("SAP_Code") AS sap, COALESCE("Site_ID",'HQ') AS "Site_ID",
                   SUM("Quantity") AS rec, 0 AS con, 0 AS ret
              FROM receipts    GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, SUM("Quantity"), 0
              FROM consumption GROUP BY 1, 2
            UNION ALL
            SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'), 0, 0, SUM("Quantity")
              FROM returns     GROUP BY 1, 2
        )
        SELECT a.sap, a."Site_ID" AS site,
               SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS on_hand
          FROM activity a
         WHERE 1=1 {clause}
         GROUP BY a.sap, a."Site_ID"
        HAVING SUM(a.rec) - SUM(a.con) - SUM(a.ret) < 0
         ORDER BY 3
    """), params)).all()
    items = [f"{r.sap} at {r.site}: {float(r.on_hand):g}" for r in rows]
    return _finding(
        "negative_stock", f"{len(items)} material(s) at NEGATIVE stock",
        "critical", items[:cap], "/stock",
        detail=("More issued than ever received — a ledger is wrong: "
                + "; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


async def probe_outbox_failures(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Messages that failed to leave the building.

    Notifications are best-effort everywhere by design, which is right — a
    messaging outage must never roll back a goods receipt. The cost of that
    choice is that failures are invisible unless something counts them, and a
    silently dead WhatsApp channel looks identical to a quiet week.

    Not site-filtered: the outbox is infrastructure, and a broken channel is
    everybody's problem. A site-scoped HOD sees it too, deliberately — they are
    the ones who will otherwise conclude the system stopped notifying them.
    """
    hours = await _setting_int(session, "health_outbox_hours")
    items: list[str] = []
    for table, label in (("whatsapp_outbox", "WhatsApp"), ("email_outbox", "email")):
        try:
            n = (await session.execute(text(f"""
                SELECT COUNT(*) FROM {table}
                 WHERE status = 'failed'
                   AND created_at > CURRENT_TIMESTAMP - make_interval(hours => :h)
            """), {"h": hours})).scalar_one()
        except Exception:  # noqa: BLE001 — an absent table is not a finding
            continue
        if n:
            items.append(f"{n} {label} message(s) failed in the last {hours}h")
    return _finding(
        "outbox_failures", "Outbound messages are failing", "warning",
        items, "/admin/console", detail="; ".join(items))


async def probe_pr_stale(session: AsyncSession, site: Optional[str]) -> dict | None:
    """Purchase Requests submitted to Logistics with no PO raised."""
    days = await _setting_int(session, "health_pr_stale_days")
    cap = await _setting_int(session, "health_max_examples")
    clause, params = _site_clause(site, 'p."Site_ID"')
    rows = (await session.execute(text(f"""
        SELECT DISTINCT p."PR_Number", p."Site_ID",
               MIN(p.submitted_to_logistics_at) AS since
          FROM pr_master p
         WHERE p.submitted_to_logistics_at IS NOT NULL
           AND p.submitted_to_logistics_at
               < CURRENT_TIMESTAMP - make_interval(days => :days)
           AND NOT EXISTS (SELECT 1 FROM po_items pi
                            WHERE pi."PR_Number" = p."PR_Number")
           {clause}
         GROUP BY p."PR_Number", p."Site_ID"
         ORDER BY 3
    """), {"days": days, **params})).all()
    items = [f"{r.PR_Number} ({r.Site_ID}, "
             f"{(_dt.datetime.now() - r.since).days}d)" for r in rows]
    return _finding(
        "pr_stale", f"{len(items)} Purchase Request(s) with no PO after {days}d",
        "warning", items[:cap], "/purchase-requests",
        detail=("; ".join(items[:cap])
                + (f" (+{len(items) - cap} more)" if len(items) > cap else "")))


Probe = Callable[[AsyncSession, Optional[str]], Awaitable[Optional[dict]]]

# ── Track 2 (slice 10b): uncertified material staged for TODAY'S DAY SHIFT ───
#
# ⚠️ A SHARPER QUESTION THAN `probe_missing_mtc`, AND BOTH ARE WANTED.
# That probe asks "is there uncertified stock on hand?" — a standing condition,
# true for weeks, and correctly reported every morning until somebody uploads
# the document. This one asks "is uncertified material about to be needed by the
# crew that starts in an hour?", which is a different and much more actionable
# fact: it has a deadline, and the deadline is today.
#
# ⚠️ NULL Shift IS SKIPPED, NOT ASSUMED. Every entry filed before slice 10b has
# a NULL shift and there is no honest backfill. Treating NULL as 'Day' would
# make this probe scream about a year of history on its first morning; treating
# it as 'Night' would be an equally invented answer. It reports what it knows.
#
# ⚠️ AND IT REUSES `visible_mtc`, like every other MTC path here. Re-implementing
# "has a certificate" would produce an alert that disagrees with the refusal at
# the counter, and an alert naming material that is actually fine is one people
# learn to skip.
_DAY_SHIFT_STATES = ("DRAFT_SUPERVISOR", "PENDING_SK", "PENDING_HOD")


async def day_shift_uncertified(session: AsyncSession,
                                site: Optional[str] = None) -> list[dict]:
    """Rows for controlled material on today's day-shift entries with no MTC."""
    from .services import quality
    category = await quality.controlled_category(session)
    clause, params = _site_clause(site, 'e."Site_ID"')
    rows = (await session.execute(text(f"""
        SELECT e."Site_ID"           AS site,
               e."Entry_No"          AS entry_no,
               e."Equipment_Tag_No"  AS tag,
               m."SAP_Code"          AS sap,
               i."Equipment_Description" AS name,
               SUM(m."Actual_Qty")   AS qty
          FROM sme_execution_entry e
          JOIN sme_execution_entry_material m ON m."Entry_ID" = e.id
          JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(m."SAP_Code")
         WHERE e."Work_Date" = :today
           AND e."Shift" = 'Day'
           AND e.status = ANY(:states)
           AND i."Category" = :cat
           AND COALESCE(m."Actual_Qty", 0) > 0
           {clause}
         GROUP BY 1, 2, 3, 4, 5
    """), {"today": _dt.date.today().isoformat(), "cat": category,
           "states": list(_DAY_SHIFT_STATES), **params})).all()

    out: list[dict] = []
    for r in rows:
        if await quality.visible_mtc(session, sap_code=r.sap,
                                     site_id=r.site) is not None:
            continue
        out.append({"site": r.site, "entry_no": r.entry_no, "tag": r.tag,
                    "sap": r.sap, "name": r.name, "qty": float(r.qty or 0),
                    "po": await _po_for(session, r.sap, r.site)})
    return out


async def _po_for(session: AsyncSession, sap: str, site: str) -> str | None:
    """The most recent purchase order this material reached the site on.

    ⚠️ BEST-EFFORT, AND None IS AN HONEST ANSWER. The chase message names the PO
    so Logistics can go straight to the supplier who owes the certificate; when
    the trail does not lead to one — material moved between sites, or received
    before purchase orders were tracked — the message says the PO is unknown
    rather than naming a plausible wrong one. A chase quoting the wrong order
    number is worse than one quoting none.
    """
    try:
        row = (await session.execute(text("""
            SELECT d."PO_Number"
              FROM dn_items di
              JOIN delivery_notes d ON d."DN_Number" = di."DN_Number"
             WHERE TRIM(di."Material_Code") IN (
                       SELECT TRIM("Material_Code") FROM inventory
                        WHERE TRIM("SAP_Code") = TRIM(:sap))
               AND d."Site_ID" = :site
               AND d."PO_Number" IS NOT NULL
             ORDER BY d.id DESC LIMIT 1
        """), {"sap": sap, "site": site})).first()
        return row[0] if row else None
    except Exception:                                       # noqa: BLE001
        return None


async def probe_day_shift_mtc(session: AsyncSession,
                              site: Optional[str]) -> dict | None:
    """Briefing finding: today's day shift is about to be blocked."""
    rows = await day_shift_uncertified(session, site)
    if not rows:
        return None
    listed = [f"{r['sap']} ({r['name'] or '—'}) {r['qty']:g} on {r['entry_no']}"
              f"{' · PO ' + r['po'] if r['po'] else ' · PO unknown'}"
              for r in rows]
    return {
        "key": "day_shift_mtc", "severity": "critical",
        "title": f"{len(rows)} uncertified material(s) staged for TODAY'S day shift",
        "count": len(rows), "items": listed[:8],
        "link_page": "/qc/inspections",
        "detail": ("These lines are on day-shift entries dated today and have no "
                   "Material Test Certificate on file, so the store keeper "
                   "cannot issue them. Logistics can attach the certificate to "
                   "the purchase order, the warehouse to the delivery note, or "
                   "the store keeper can upload it directly."),
    }


PROBES: list[tuple[str, Probe]] = [
    # First in the list because it sorts first anyway (critical) and because it
    # is the only finding here with a deadline measured in hours.
    ("day_shift_mtc",       probe_day_shift_mtc),
    ("missing_mtc",         probe_missing_mtc),
    ("qc_blocked_stock",    probe_qc_blocked_stock),
    ("negative_stock",      probe_negative_stock),
    ("dn_draft_stale",      probe_dn_draft_stale),
    ("returnables_overdue", probe_returnables_overdue),
    ("pending_approvals",   probe_pending_approvals),
    ("pr_stale",            probe_pr_stale),
    ("ppe_expiring",        probe_ppe_expiring),
    ("outbox_failures",     probe_outbox_failures),
]

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


async def build_briefing(session: AsyncSession, *, site: Optional[str]) -> dict:
    """Run every probe and assemble the briefing for one scope.

    ⚠️ EVERY PROBE IS INDIVIDUALLY GUARDED. A probe that raises becomes a
    `probe_failed` finding naming itself, and the rest of the briefing is
    delivered. The alternative — one bad query killing the run — produces
    silence, and silence from a monitor is indistinguishable from good news.
    """
    findings: list[dict] = []
    failed: list[str] = []
    for key, probe in PROBES:
        try:
            f = await probe(session, site)
            if f:
                findings.append(f)
        except Exception as e:  # noqa: BLE001 — see the docstring
            log.exception("health probe %s failed", key)
            failed.append(f"{key} ({type(e).__name__})")
    if failed:
        findings.append({
            "key": "probe_failed", "title": f"{len(failed)} health probe(s) errored",
            "severity": "warning", "count": len(failed), "items": failed,
            "link_page": "/admin/console",
            "detail": "This briefing is incomplete: " + ", ".join(failed),
        })
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 3), f["key"]))
    return {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "site": site, "findings": findings,
        "total": sum(f["count"] for f in findings),
        "worst": findings[0]["severity"] if findings else "none",
        "probes_run": len(PROBES), "probes_failed": len(failed),
    }


def compile_body(briefing: dict, max_chars: int = 950) -> str:
    """Findings → ONE line, •-separated, inside the template parameter cap.

    Single-line for the same reason as the evening digest: Meta rejects a
    template parameter containing a newline (#132000), and `dispatch()` hands
    the same body to WhatsApp and to the bell. Truncation is explicit — a
    "(+N more)" tail rather than a sentence that stops mid-word.
    """
    parts = [f"• {f['title']} — {f['detail']}" if f.get("detail") else f"• {f['title']}"
             for f in briefing["findings"]]
    out = ""
    for i, p in enumerate(parts):
        p = " ".join(p.split())
        nxt = p if not out else f"{out}  {p}"
        if len(nxt) > max_chars:
            return f"{out}  …(+{len(parts) - i} more)" if out else p[:max_chars]
        out = nxt
    return out or "No issues found."


async def _recipient_scopes(session: AsyncSession) -> dict[Optional[str], list[str]]:
    """admin → the all-sites briefing; each HOD → their own site's.

    Same shape as the weekly executive report, deliberately: one place to learn
    how "who gets the scheduled thing" works in this system.
    """
    rows = (await session.execute(select(
        users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"]
    ).where(users_t.c["role"].in_(("admin", "hod"))))).all()
    scopes: dict[Optional[str], list[str]] = {}
    for r in rows:
        scope = None if r.role == "admin" else ((r.Site_ID or "").strip() or None)
        scopes.setdefault(scope, []).append(r.username)
    return scopes


async def run_and_dispatch(session: AsyncSession, *, force: bool = False) -> dict:
    """One briefing run: build per scope, dispatch only what has findings.

    `force=True` sends even a clean briefing — for an operator proving the
    channel works end to end, which is the one time "all clear" is the message
    somebody actually wants.
    """
    if not await _enabled(session):
        return {"skipped": "health_briefing_enabled=0"}

    scopes = await _recipient_scopes(session)
    sent = 0
    summary: dict[str, dict] = {}
    for scope, usernames in scopes.items():
        b = await build_briefing(session, site=scope)
        summary[scope or "ALL"] = {"findings": len(b["findings"]),
                                   "total": b["total"], "worst": b["worst"]}
        if not b["findings"] and not force:
            continue
        # Severity carries to the channel: a critical briefing must not sit in
        # the evening digest queue until 16:00 (dispatch enforces that), and a
        # routine one should not use the critical template.
        critical = b["worst"] == "critical"
        title = (f"Daily System Health — {scope or 'all sites'} — "
                 f"{b['total']} item(s) need attention" if b["findings"]
                 else f"Daily System Health — {scope or 'all sites'} — all clear")
        for username in usernames:
            await dispatch(
                session, recipient_user=username, event_key="health_briefing",
                severity=b["worst"] if b["findings"] else "success",
                wa_template="critical_alert" if critical else "status_update",
                title=title, body=compile_body(b),
                link_page="/dashboard", related_table="app_notifications",
                related_ref=f"health:{scope or 'ALL'}",
                created_by="health-monitor", delivery="urgent")
            sent += 1

    mtc = await dispatch_missing_mtc(session, force=force)
    sent += mtc["dispatched"]

    # Audited on EVERY run, including a clean one that dispatched nothing —
    # this is how "is the agent alive?" gets answered without a daily
    # all-clear message that trains people to ignore the channel.
    await write_audit(session, "health-monitor", "HEALTH_BRIEFING", "app_notifications",
                      f"scopes={len(scopes)} dispatched={sent} "
                      f"mtc_alerts={mtc['dispatched']}/{mtc['materials']} " +
                      " ".join(f"{k}:{v['total']}/{v['worst']}"
                               for k, v in sorted(summary.items())))
    await session.commit()
    log.info("health briefing: %d scope(s), %d dispatch(es) — %s",
             len(scopes), sent, summary)
    return {"scopes": len(scopes), "dispatched": sent, "summary": summary,
            "missing_mtc": mtc}


# ── the missing-MTC alert, routed to the people who can act on it ────────────
#
# The briefing goes to admins and HODs. That is the wrong audience for this one
# finding: an HOD cannot obtain a certificate from a supplier, and the store
# keeper who is about to be refused at the counter is not on the list at all.
# So this finding gets its own dispatch with its own routing, keyed on WHERE
# the material is sitting.
#
# Logistics is on BOTH lists, and that is the point rather than an oversight —
# they are the only role that can actually get the document from the supplier.
# Everyone else on each list is someone the missing certificate is about to
# cost something: the warehouse cannot ship it usefully, the store keeper
# cannot issue it, the QC cannot complete an inspection against it.
_MTC_ALERT_ROLES = {
    "warehouse": ("logistics", "warehouse_user", "qc"),
    "site":      ("store_keeper", "hod", "qc", "logistics"),
}


async def dispatch_missing_mtc(session: AsyncSession, *, force: bool = False) -> dict:
    """One alert per place holding uncertified Surface Shields.

    Grouped BY PLACE, not per material: a warehouse holding nine uncertified
    materials should get one message listing nine, not nine messages. The
    daily repeat is deliberate — this is a standing condition, not an event,
    and it stops the day somebody uploads the certificate.

    Scoping rides on `dispatch`'s own recipient fields: `recipient_site` for a
    site's SK/HOD/QC, `recipient_warehouse` for a warehouse's user and QC.
    That is what keeps a site's store keeper out of another site's alert, and
    it is the same mechanism every other notification uses rather than a
    second, parallel idea of who-sees-what.
    """
    rows = await missing_mtc_rows(session, None)
    if not rows and not force:
        return {"dispatched": 0, "materials": 0, "places": 0}

    places: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        places.setdefault((r["where"], r["place"]), []).append(r)

    sent = 0
    for (where, place), found in sorted(places.items()):
        listed = ", ".join(f"{f['sap']} ({f['name'] or '—'}) {f['qty']:g}"
                           for f in found[:6])
        more = f" (+{len(found) - 6} more)" if len(found) > 6 else ""
        at = "warehouse" if where == "warehouse" else "site"
        body = (f"{len(found)} Surface Shield material(s) at {at} {place} have no "
                f"Material Test Certificate on file: {listed}{more}. "
                "The stock is booked in and counted, but no store keeper can "
                "issue it to the field until the certificate is uploaded. "
                "Logistics can attach it to the purchase order, the warehouse "
                "to the delivery note, or the store keeper can upload it directly.")
        for role in _MTC_ALERT_ROLES[where]:
            await dispatch(
                session, event_key="mtc_missing_daily", severity="warning",
                recipient_role=role,
                # Exactly one of these is set per branch. A warehouse is not
                # inside a site, so tagging a warehouse alert with a site would
                # send it to store keepers who cannot see the stock it names.
                recipient_site=place if where == "site" else None,
                recipient_warehouse=place if where == "warehouse" else None,
                wa_template="action_required",
                title=f"MTC outstanding — {len(found)} material(s) at {place}",
                body=body, link_page="/qc/inspections",
                related_table="mtc_documents", related_ref=f"mtc-daily:{place}",
                created_by="health-monitor")
            sent += 1

    # ── the Head of Qualities: ONE unscoped message, not one per place ───────
    #
    # ⚠️ THE ALERTS ABOVE CANNOT REACH A qc_hod, AND THE REASON IS THE
    # VISIBILITY RULE, NOT THE RECIPIENT LIST. A notification is visible when
    #
    #     recipient_role = role
    #     AND (recipient_site IS NULL OR recipient_site = site)
    #
    # and every message above sets `recipient_site` to a specific place. A
    # Head of Qualities carries site_id '' — it is cross-site by design — so
    # 'SITE-A' != '' and the row is invisible to them. Adding 'qc_hod' to
    # `_MTC_ALERT_ROLES` would have looked right, changed nothing, and left the
    # bell empty while the condition it describes was live at six sites.
    #
    # Relaxing the visibility rule is NOT the fix: it would leak every
    # site-scoped notification to every unscoped role. A second dispatch with
    # `recipient_site=None` is, and it is the right SHAPE for oversight anyway
    # — six messages saying one thing is how somebody responsible for all six
    # sites learns to ignore them.
    if rows:
        listed = ", ".join(
            f"{place} ({len(found)})" for (where, place), found
            in sorted(places.items()))
        await dispatch(
            session, event_key="mtc_missing_daily_oversight", severity="warning",
            recipient_role="qc_hod", wa_template="action_required",
            title=(f"Surface Shield without MTC — {len(rows)} material(s) "
                   f"across {len(places)} location(s)"),
            body=(f"{len(rows)} Surface Shield material(s) are on hand with no "
                  f"Material Test Certificate: {listed}. None of it can be "
                  f"issued to the field until the certificates are uploaded. "
                  f"Open Quality Oversight to see the register and chase the "
                  f"site, the warehouse or Logistics."),
            link_page="/qc-hod", related_table="mtc_documents",
            related_ref=f"mtc-daily-oversight:{_dt.date.today().isoformat()}",
            created_by="health-monitor")
        sent += 1

    return {"dispatched": sent, "materials": len(rows), "places": len(places)}


async def dispatch_day_shift_mtc(session: AsyncSession, *,
                                 force: bool = False) -> dict:
    """The chase, on three channels chosen by WHO is being asked.

    ⚠️ THE CHANNEL IS DECIDED BY THE RECIPIENT, NOT BY URGENCY.

      · in-app + WhatsApp  → colleagues (Logistics, the site SK/HOD, QC). These
        numbers are in `users`; the message is internal and goes out
        automatically like every other notification.
      · EMAIL              → Logistics only (operator ruling, slice 10b). They
        are the role that can actually obtain the document from a supplier, and
        they live in a mailbox. Emailing everybody would make three copies of
        one message and teach people to filter it.
      · WhatsApp DRAFT     → anybody outside the company. Never sent
        automatically; written to the outbox at `status='draft'` for a human to
        release in the WhatsApp Console. A chase that leaves the company is read
        as the company's position, and an automated one naming the wrong
        purchase order is a commercial mistake nobody reviewed.
    """
    from .services import emailer
    from .services import whatsapp as wa

    rows = await day_shift_uncertified(session, None)
    if not rows and not force:
        return {"found": 0, "internal": 0, "emails": 0, "drafts": 0}

    by_site: dict[str, list[dict]] = {}
    for r in rows:
        by_site.setdefault(r["site"], []).append(r)

    internal = 0
    for site, found in sorted(by_site.items()):
        listed = ", ".join(
            f"{f['sap']} {f['qty']:g} on {f['entry_no']}"
            f"{' (PO ' + f['po'] + ')' if f['po'] else ' (PO unknown)'}"
            for f in found[:6])
        more = f" (+{len(found) - 6} more)" if len(found) > 6 else ""
        body = (f"TODAY'S DAY SHIFT at {site}: {len(found)} Surface Shield "
                f"line(s) have no Material Test Certificate and cannot be "
                f"issued — {listed}{more}. The crew is on site; the paperwork "
                f"is not.")
        for role in ("logistics", "store_keeper", "hod", "qc"):
            await dispatch(
                session, event_key="mtc_day_shift", severity="critical",
                recipient_role=role, recipient_site=site,
                wa_template="critical_alert",
                title=f"Day shift blocked — {len(found)} uncertified line(s) at {site}",
                body=body, link_page="/qc/inspections",
                related_table="sme_execution_entry",
                related_ref=f"dayshift:{site}:{_dt.date.today().isoformat()}",
                created_by="health-monitor")
            internal += 1

    # ⚠️ THE HEAD OF QUALITIES NEEDS AN UNSCOPED COPY, for the same reason
    # `dispatch_missing_mtc` gives one: a notification is visible when
    # `recipient_site IS NULL OR recipient_site = site`, and a qc_hod carries
    # site_id '' by design — so every site-scoped row above is invisible to
    # them. Adding the role to the loop would look right and change nothing.
    if rows:
        summary = ", ".join(f"{s} ({len(v)})" for s, v in sorted(by_site.items()))
        await dispatch(
            session, event_key="mtc_day_shift_oversight", severity="critical",
            recipient_role="qc_hod", recipient_site=None,
            wa_template="critical_alert",
            title=f"Day shift blocked at {len(by_site)} site(s) — no MTC",
            body=(f"Uncertified Surface Shield staged for today's day shift: "
                  f"{summary}. Nothing can be issued against these lines."),
            link_page="/qc-hod", related_table="sme_execution_entry",
            related_ref=f"dayshift-oversight:{_dt.date.today().isoformat()}",
            created_by="health-monitor")
        internal += 1

    # ── the email, to Logistics only ───────────────────────────────────────
    emails = 0
    if rows and emailer.enabled():
        lines = [f"  · {r['site']} / {r['entry_no']} / {r['tag']}: "
                 f"{r['sap']} {r['name'] or ''} qty {r['qty']:g}"
                 f"{' — PO ' + r['po'] if r['po'] else ' — PO unknown'}"
                 for r in rows]
        res = await emailer.send_email(
            session, to=emailer.logistics_to(),
            subject=(f"[GI Hub] Day shift blocked — {len(rows)} uncertified "
                     f"Surface Shield line(s)"),
            body=("The following material is staged for TODAY'S day shift and "
                  "has no Material Test Certificate on file. The store keeper "
                  "cannot issue it.\n\n" + "\n".join(lines) +
                  "\n\nAttach the certificate to the purchase order in the "
                  "Logistics Portal, or ask the warehouse to attach it to the "
                  "delivery note.\n"),
            event_key="mtc_day_shift", related_table="sme_execution_entry",
            related_ref=_dt.date.today().isoformat(),
            created_by="health-monitor")
        emails = 1 if res.get("status") == "sent" else 0

    # ── the supplier chase, as a DRAFT ─────────────────────────────────────
    drafts = 0
    supplier_to = os.environ.get("GI_SUPPLIER_CHASE_TO", "").strip()
    if rows and supplier_to:
        with_po = [r for r in rows if r["po"]]
        if with_po:
            pos = sorted({r["po"] for r in with_po})
            res = await wa.draft_text(
                session, to=supplier_to,
                body=("General Industries: we are missing Material Test "
                      f"Certificates for purchase order(s) {', '.join(pos)}. "
                      "The material is on site and cannot be used until the "
                      "certificate is received. Please send it today."),
                event_key="mtc_supplier_chase",
                related_table="sme_execution_entry",
                related_ref=_dt.date.today().isoformat(),
                created_by="health-monitor",
                reason="supplier chase — review before sending")
            drafts = 1 if res.get("status") == "draft" else 0

    return {"found": len(rows), "internal": internal, "emails": emails,
            "drafts": drafts, "sites": len(by_site)}


async def briefing_loop() -> None:
    """Daemon: fire the morning briefing once per day at GI_BRIEFING_HOUR.

    Started from the FastAPI lifespan beside the digest and weekly-report
    loops; disabled by GI_SCHEDULER=0 like both of them. One bad run logs and
    waits for tomorrow rather than killing the loop — a monitor that dies on
    its first bad night is the one shape of monitor worse than none.
    """
    import asyncio

    from .db import SessionLocal
    from .services import dailyjob

    hour = int(os.environ.get("GI_BRIEFING_HOUR", "7"))
    minute = int(os.environ.get("GI_BRIEFING_MINUTE", "0"))
    log.info("morning-briefing scheduler started (daily %02d:%02d local)", hour, minute)
    while True:
        now = _dt.datetime.now()
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += _dt.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            async with SessionLocal() as s:
                # ⚠️ ONE WORKER RUNS THIS, NOT FOUR. Every worker wakes on the
                # same clock tick; without the claim each dispatched its own
                # copy and every recipient got the briefing four times. See
                # services/dailyjob.py — the claim is one atomic statement
                # because four workers waking together is exactly the window a
                # check-then-write leaves open.
                if not await dailyjob.claim(s, "morning_briefing", nxt):
                    log.info("morning briefing: another worker has it")
                    continue
                res = await run_and_dispatch(s)
                # Track 2: the day-shift chase rides the SAME 07:00 claim
                # rather than a second 07:30 timer. Two alerts half an hour
                # apart trains people to ignore both, and a separate loop would
                # need its own claim to avoid the 4x it was written to prevent.
                res["day_shift"] = await dispatch_day_shift_mtc(s)
                # Phase 19c: the daily chase of overdue tool loans rides the
                # same claim, for the same reason (services/loan_chaser.py).
                from .services import loan_chaser
                try:
                    res["loan_chaser"] = await loan_chaser.run(s)
                except Exception as e:  # noqa: BLE001 — never costs the briefing
                    await s.rollback()
                    res["loan_chaser"] = f"failed: {type(e).__name__}"
                    log.exception("loan chaser failed")
                await dailyjob.note_result(s, "morning_briefing", str(res))
            log.info("morning briefing run: %s", res)
        except Exception:  # noqa: BLE001 — one bad run must not kill the loop
            log.exception("morning briefing run failed")


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("/health/briefing", summary="Preview the daily system-health briefing")
async def preview_briefing(site_id: Optional[str] = Query(None),
                           user: dict = Depends(require_level(2)),
                           session: AsyncSession = Depends(get_session)):
    """Read-only preview. Level 2 so an HOD can see their own site's briefing;
    `resolve_site_param` refuses a scoped caller asking for another site rather
    than silently rewriting it, and a scoped caller with no site of their own
    resolves to '' — which every probe treats as matching nothing."""
    return await build_briefing(session, site=resolve_site_param(user, site_id))


@router.post("/admin/health/run", summary="Run and dispatch the health briefing now (admin)")
async def run_briefing_now(force: bool = Query(False),
                           user: dict = Depends(require_roles()),
                           session: AsyncSession = Depends(get_session)):
    return await run_and_dispatch(session, force=force)
