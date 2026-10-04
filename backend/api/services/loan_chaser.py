"""
backend/api/services/loan_chaser.py — the daily chase of overdue tool loans
(Phase 19c, ruling Q19-3).

Until Phase 19 an overdue loan was chased ONCE: the first time somebody opened
the Returnable Items page after it fell due (`whatsapp_alert_sent`). A borrower
who ignored that message was never reminded again, and a loan coming back in
parts had no way to say "2 still out".

Every day, for every open loan past its due time:

  * the BORROWER is reminded (WhatsApp, when the loan has a phone and WhatsApp
    is configured), naming what is STILL OUT, which for a partly returned loan
    is the remainder ("2 of 5 EA");
  * the site's STORE KEEPERS get ONE in-app summary per site, not one per
    loan; a daily list nobody can read is a list nobody reads;
  * a loan more than ESCALATE_AFTER_DAYS overdue is escalated to the site's
    HOD, ONCE per loan (`hod_escalated_at`), partial or not.

⚠️ IT RUNS INSIDE THE 07:00 MORNING-BRIEFING CLAIM (`health_monitor.
briefing_loop` → `services/dailyjob.claim`), like the day-shift MTC chase. One
worker of four sends, once a day. A second timer would need a second claim to
avoid the 4× that `dailyjob` exists to prevent.

`run()` is also callable directly (the service tests do). `now` is LOCAL naive
time, the clock every loan column is written in (P18-local-clock).
"""
from __future__ import annotations

import datetime as _dt
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import ledger
from . import whatsapp as wa
from .notifications import dispatch, notify

log = logging.getLogger("gi.loan_chaser")

ESCALATE_AFTER_DAYS = 3


def _q(x) -> str:
    return f"{float(x or 0):g}"


async def run(session: AsyncSession, now: _dt.datetime | None = None) -> dict:
    """Chase every overdue open loan once. Returns what it did (for the
    briefing's daily_job_runs.last_result and for tests). Commits."""
    t = ledger._MD.tables["returnable_items"]
    now = now or _dt.datetime.now()
    rows = (await session.execute(select(
        t.c["id"], t.c["material_name"], t.c["qty"], t.c["qty_returned"], t.c["uom"],
        t.c["borrower_name"], t.c["borrower_phone"], t.c["Site_ID"],
        t.c["expected_return_time"], t.c["hod_escalated_at"],
    ).where(t.c["status"] == "borrowed", t.c["expected_return_time"].is_not(None),
            t.c["expected_return_time"] < now).order_by(t.c["Site_ID"], t.c["id"]))).all()

    reminded = escalated = 0
    by_site: dict[str, list] = {}
    for r in rows:
        lent = float(r.qty or 1)
        out = max(lent - float(r.qty_returned or 0), 0.0)
        unit = f" {r.uom}" if r.uom else ""
        still = (f"{_q(out)} of {_q(lent)}{unit}" if (r.qty_returned or 0) > 0
                 else f"{_q(out)}{unit}")
        days = (now - r.expected_return_time).total_seconds() / 86400
        by_site.setdefault(r.Site_ID or "", []).append((r, still, days))

        if r.borrower_phone and wa.enabled():
            try:
                await wa.send_template(
                    session, to=r.borrower_phone, template_key="critical_alert",
                    variables=[f"Tool overdue: {r.material_name}",
                               f"{still} still to return — it was due "
                               f"{r.expected_return_time:%Y-%m-%d %H:%M}. "
                               "Please bring it back to the store."],
                    event_key="returnable_reminder", related_table="returnable_items",
                    related_ref=str(r.id))
                reminded += 1
            except Exception as e:  # noqa: BLE001 — one bad number never stops the chase
                log.warning("loan %s reminder failed: %s", r.id, e)
        await session.execute(update(t).where(t.c["id"] == r.id).values(last_reminded_at=now))

        if days > ESCALATE_AFTER_DAYS and r.hod_escalated_at is None:
            await dispatch(session, event_key="returnable_escalated", recipient_role="hod",
                           recipient_site=r.Site_ID, severity="warning",
                           wa_template="critical_alert",
                           title=f"Tool {int(days)} days overdue: {r.material_name}",
                           body=(f"{r.borrower_name} still has {still} — due "
                                 f"{r.expected_return_time:%Y-%m-%d %H:%M}. The borrower has "
                                 "been reminded daily."),
                           link_page="/entry/returnables", related_table="returnable_items",
                           related_ref=str(r.id))
            await session.execute(update(t).where(t.c["id"] == r.id).values(hod_escalated_at=now))
            escalated += 1

    for site, items in by_site.items():
        partly = sum(1 for r, _s, _d in items if (r.qty_returned or 0) > 0)
        lines = "; ".join(f"#{r.id} {r.material_name} ({still}, {r.borrower_name})"
                          for r, still, _d in items[:8])
        more = f"; and {len(items) - 8} more" if len(items) > 8 else ""
        await notify(session, event_key="returnable_daily_overdue", recipient_role="store_keeper",
                     recipient_site=site or None, severity="warning",
                     title=(f"{len(items)} tool loan(s) overdue"
                            + (f", {partly} partly returned" if partly else "")),
                     body=lines + more, link_page="/entry/returnables")
    await session.commit()
    return {"overdue": len(rows), "reminded": reminded, "escalated": escalated,
            "sites": len(by_site)}
