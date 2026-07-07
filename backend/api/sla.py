"""
backend/api/sla.py — Admin SLA oversight: the >24h "Overdue Actions" tracker (T2).

Read side: GET /admin/overdue-actions UNIONs every pending queue in the system
(the same queue definitions /meta/work-queues counts) and surfaces items older
than the SLA window that no admin has cleared, with the responsible users
resolved by role + site / warehouse scope.

Action side:
  POST /admin/overdue-actions/{kind}/{ref_id}/clear   → sla_dismissals row
  POST /admin/overdue-actions/{kind}/{ref_id}/notify  → one in-app notification
       per responsible user via the shared notifications service, template:
       "URGENT — Dear {User Name}, From: Admin. Subject: Action required on
        pending submission {ID/Details}."

Both are audited (SLA_CLEAR / SLA_NOTIFY). Admin-only (level 4).
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level
from .db import get_session
from .services.ledger import _MD, write_audit
from .services.notifications import notify

router = APIRouter(prefix="/admin", tags=["admin sla"],
                   dependencies=[Depends(require_level(4))])

users_t = _MD.tables["users"]
dismissals_t = _MD.tables["sla_dismissals"]

SLA_HOURS_DEFAULT = 24.0

# ── queue registry ───────────────────────────────────────────────────────────
# One entry per pending queue. `collect` returns raw candidates as dicts:
#   {ref_id, summary, site (or None), warehouse (or None), ts (datetime|None)}
# Responsibility = `role` scoped by site / warehouse when the queue carries one.
_LABELS = {
    "hod-receipt": "Receipt awaiting HOD approval",
    "hod-issue": "Issue awaiting HOD approval",
    "hod-return": "Return awaiting HOD approval",
    "hod-adjustment": "Stock adjustment awaiting HOD approval",
    "sk-request": "Material request awaiting Store Keeper",
    "wh-assignment": "PO assignment awaiting Warehouse",
    "log-reschedule": "PO reschedule awaiting Logistics",
    "log-pr": "PR submission awaiting Logistics",
}
_ROLES = {
    "hod-receipt": "hod", "hod-issue": "hod", "hod-return": "hod",
    "hod-adjustment": "hod", "sk-request": "store_keeper",
    "wh-assignment": "warehouse_user", "log-reschedule": "logistics",
    "log-pr": "logistics",
}


def _age_hours(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, str):  # defensive — mirrors may carry text timestamps
        try:
            ts = _dt.datetime.fromisoformat(ts.strip().replace("Z", ""))
        except ValueError:
            return None
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    return (_dt.datetime.now() - ts).total_seconds() / 3600.0


async def _collect(session: AsyncSession) -> list[dict]:
    out: list[dict] = []

    async def _simple(kind: str, tname: str, statuses: list[str], ts_col: str,
                      ref_fmt, site_col: str | None = "Site_ID",
                      wh_col: str | None = None, status_col: str = "status"):
        t = _MD.tables[tname]
        cols = [t.c["id"], t.c[ts_col]]
        if site_col:
            cols.append(t.c[site_col])
        if wh_col:
            cols.append(t.c[wh_col])
        extra = [c for c in ref_fmt.cols if c not in {ts_col, "id", site_col, wh_col}]
        cols += [t.c[c] for c in extra]
        rows = (await session.execute(
            select(*cols).where(t.c[status_col].in_(statuses))
            .order_by(t.c["id"]))).mappings().all()
        for r in rows:
            out.append({"kind": kind, "ref_id": str(r["id"]),
                        "summary": ref_fmt.fmt(r),
                        "site": r[site_col] if site_col else None,
                        "warehouse": r[wh_col] if wh_col else None,
                        "ts": r[ts_col]})

    class _Ref:
        def __init__(self, cols, fmt):
            self.cols, self.fmt = cols, fmt

    await _simple("hod-receipt", "pending_receipts", ["pending_hod"], "Timestamp",
                  _Ref(["SAP_Code", "Quantity", "PR_Number"],
                       lambda r: f"receipt · {r['SAP_Code']} × {r['Quantity']} (PR {r['PR_Number'] or '—'})"))
    await _simple("hod-issue", "pending_issues", ["pending_hod"], "Timestamp",
                  _Ref(["SAP_Code", "Quantity", "Issued_To"],
                       lambda r: f"issue · {r['SAP_Code']} × {r['Quantity']} → {r['Issued_To'] or '—'}"))
    await _simple("hod-return", "pending_returns", ["pending_hod"], "submitted_at",
                  _Ref(["SAP_Code", "Quantity", "submitted_by"],
                       lambda r: f"return · {r['SAP_Code']} × {r['Quantity']} (by {r['submitted_by'] or '—'})"))
    await _simple("hod-adjustment", "stock_adjustments", ["pending_hod"], "submitted_at",
                  _Ref(["SAP_Code", "variance", "submitted_by"],
                       lambda r: f"adjustment · {r['SAP_Code']} Δ{r['variance']} (by {r['submitted_by'] or '—'})"))
    await _simple("sk-request", "supervisor_material_requests", ["pending_sk"],
                  "requested_at",
                  _Ref(["request_no", "Worker_Name", "requested_by"],
                       lambda r: f"{r['request_no']} · {r['Worker_Name'] or '—'} (by {r['requested_by'] or '—'})"))
    await _simple("wh-assignment", "po_assignments",
                  ["assigned", "acknowledged", "partial"], "assigned_at",
                  _Ref(["PO_Number", "status"],
                       lambda r: f"PO {r['PO_Number']} · {r['status']}"),
                  site_col=None, wh_col="Warehouse_ID")
    await _simple("log-reschedule", "po_reschedule_requests", ["pending"],
                  "requested_at",
                  _Ref(["PO_Number", "requested_date", "requested_by"],
                       lambda r: f"PO {r['PO_Number']} → {r['requested_date']} (by {r['requested_by'] or '—'})"),
                  site_col=None)

    # log-pr — PR lines land per-row; the submission unit is the PR_Number.
    pr = _MD.tables["pr_master"]
    rows = (await session.execute(
        select(pr.c["PR_Number"], pr.c["Site_ID"],
               pr.c["submitted_to_logistics_at"], pr.c["id"])
        .where(pr.c["logistics_status"] == "submitted")
        .order_by(pr.c["PR_Number"], pr.c["id"]))).mappings().all()
    by_pr: dict[str, dict] = {}
    for r in rows:
        a = by_pr.setdefault(str(r["PR_Number"]), {
            "site": r["Site_ID"], "ts": r["submitted_to_logistics_at"], "n": 0})
        a["n"] += 1
        ts = r["submitted_to_logistics_at"]
        if ts is not None and (a["ts"] is None or ts < a["ts"]):
            a["ts"] = ts
    for prn, a in by_pr.items():
        out.append({"kind": "log-pr", "ref_id": prn,
                    "summary": f"PR {prn} · {a['n']} line(s) from {a['site'] or '—'}",
                    "site": None,  # logistics is global — site is context only
                    "warehouse": None, "ts": a["ts"]})
    return out


async def _responsible(session: AsyncSession, kind: str, site: str | None,
                       warehouse: str | None) -> list[str]:
    role = _ROLES[kind]
    stmt = select(users_t.c["username"]).where(users_t.c["role"] == role)
    if site:
        stmt = stmt.where(users_t.c["Site_ID"] == site)
    if warehouse:
        wh_stmt = stmt.where(users_t.c["Warehouse_ID"] == warehouse)
        names = [r[0] for r in (await session.execute(wh_stmt.order_by(users_t.c["username"]))).all()]
        if names:  # fall back to the whole role if nobody is bound to the WH
            return names
    return [r[0] for r in (await session.execute(stmt.order_by(users_t.c["username"]))).all()]


async def _overdue_items(session: AsyncSession, hours: float) -> list[dict]:
    cleared = {(r[0], r[1]) for r in (await session.execute(
        select(dismissals_t.c["kind"], dismissals_t.c["ref_id"]))).all()}
    items = []
    for it in await _collect(session):
        if (it["kind"], it["ref_id"]) in cleared:
            continue
        age = _age_hours(it["ts"])
        if age is None or age < hours:
            continue
        items.append({
            "kind": it["kind"], "label": _LABELS[it["kind"]],
            "ref_id": it["ref_id"], "summary": it["summary"],
            "site": it["site"], "warehouse": it["warehouse"],
            "role": _ROLES[it["kind"]],
            "pending_since": it["ts"].isoformat(sep=" ", timespec="minutes")
            if isinstance(it["ts"], _dt.datetime) else str(it["ts"]),
            "age_hours": round(age, 1),
            "responsible": await _responsible(session, it["kind"],
                                              it["site"], it["warehouse"]),
        })
    items.sort(key=lambda x: -x["age_hours"])
    return items


@router.get("/overdue-actions",
            summary="Pending submissions older than the SLA window (default 24h), "
                    "with the responsible users resolved")
async def overdue_actions(hours: float = SLA_HOURS_DEFAULT,
                          session: AsyncSession = Depends(get_session)):
    if not (0 < hours <= 24 * 90):
        raise HTTPException(422, "hours must be between 0 and 2160")
    items = await _overdue_items(session, hours)
    return {"hours": hours, "count": len(items), "items": items}


@router.post("/overdue-actions/{kind}/{ref_id}/clear",
             summary="Clear (dismiss) one overdue item from the tracker")
async def clear_overdue(kind: str, ref_id: str,
                        actor: dict = Depends(require_level(4)),
                        session: AsyncSession = Depends(get_session)):
    if kind not in _LABELS:
        raise HTTPException(404, f"unknown queue kind {kind!r}")
    try:
        await session.execute(insert(dismissals_t).values(
            kind=kind, ref_id=str(ref_id), cleared_by=actor["username"]))
        await write_audit(session, actor["username"], "SLA_CLEAR",
                          "sla_dismissals", f"kind={kind} ref={ref_id}")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "already cleared")
    return {"cleared": True, "kind": kind, "ref_id": str(ref_id)}


@router.post("/overdue-actions/{kind}/{ref_id}/notify",
             summary="Send the URGENT nudge to every user responsible for one "
                     "overdue item")
async def notify_overdue(kind: str, ref_id: str,
                         actor: dict = Depends(require_level(4)),
                         session: AsyncSession = Depends(get_session)):
    if kind not in _LABELS:
        raise HTTPException(404, f"unknown queue kind {kind!r}")
    # Re-resolve from the live queues so a stale row can't be nudged.
    item = next((it for it in await _collect(session)
                 if it["kind"] == kind and it["ref_id"] == str(ref_id)), None)
    if item is None:
        raise HTTPException(404, f"{kind} {ref_id} is not pending any more")
    recipients = await _responsible(session, kind, item["site"], item["warehouse"])
    if not recipients:
        raise HTTPException(409, f"no {_ROLES[kind]} user matches this item's scope")
    details = f"{_LABELS[kind]} — {item['summary']}"
    for uname in recipients:
        # Exact template per the T2 spec.
        await notify(
            session, event_key="sla_nudge", severity="critical",
            recipient_user=uname,
            title="URGENT — Action required on pending submission",
            body=(f"URGENT — Dear {uname}, From: Admin. Subject: Action "
                  f"required on pending submission {ref_id} ({details})."),
            related_table=kind, related_ref=str(ref_id))
    await write_audit(session, actor["username"], "SLA_NOTIFY",
                      "app_notifications",
                      f"kind={kind} ref={ref_id} recipients={','.join(recipients)}")
    await session.commit()
    return {"notified": True, "kind": kind, "ref_id": str(ref_id),
            "recipients": recipients}
