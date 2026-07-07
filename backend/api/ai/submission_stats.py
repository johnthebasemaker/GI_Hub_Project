"""
backend/api/ai/submission_stats.py — deterministic feature extractor for
Submission Intelligence (T1).

ALL numbers here are computed from the ledger — the LLM only ever PHRASES
these facts; it never invents or computes them. Pure reads (consumption /
pending_issues / requests / v_site_stock SQL), no writes.

Features per submission kind:
  staged-issue  (pending_issues row, SK → HOD reviewer)
      trailing 30/60-day consumption stats for (SAP, site): mean per-issue
      qty, std-dev, mean daily rate, deviation % of the submitted qty vs the
      30-day per-issue mean, z-score, first-time-material flag, off-pattern
      weekday flag.
  xsite  (requests row, requesting HOD → target-site HOD reviewer)
      target-site current stock, 30-day mean daily consumption at the target,
      days-of-cover now vs after granting the requested qty.
"""
from __future__ import annotations

import datetime as _dt
import math

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.ledger import _MD

consumption_t = _MD.tables["consumption"]
pending_issues_t = _MD.tables["pending_issues"]
requests_t = _MD.tables["requests"]


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_date(v) -> _dt.date | None:
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


async def usage_stats(session: AsyncSession, sap_code: str, site_id: str | None,
                      days: int) -> dict:
    """Trailing-N-day consumption stats for one material (site-scoped)."""
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    stmt = select(consumption_t.c["Date"], consumption_t.c["Quantity"]) \
        .where(consumption_t.c["SAP_Code"] == sap_code)
    if site_id:
        stmt = stmt.where(consumption_t.c["Site_ID"] == site_id)
    rows = [(r[0], _f(r[1])) for r in (await session.execute(stmt)).all()]
    recent = [(d, q) for d, q in ((_to_date(d), q) for d, q in rows)
              if d is not None and d.isoformat() >= cutoff]
    qtys = [q for _, q in recent]
    n = len(qtys)
    mean = sum(qtys) / n if n else 0.0
    std = math.sqrt(sum((q - mean) ** 2 for q in qtys) / n) if n else 0.0
    total = sum(qtys)
    return {
        "days": days, "issues": n, "total_qty": round(total, 3),
        "mean_issue_qty": round(mean, 3), "std_issue_qty": round(std, 3),
        "mean_daily_qty": round(total / days, 4) if days else 0.0,
        "weekdays": sorted({d.weekday() for d, _ in recent}),
    }


async def staged_issue_features(session: AsyncSession, ref_id: int) -> dict | None:
    row = (await session.execute(select(
        pending_issues_t.c["id"], pending_issues_t.c["SAP_Code"],
        pending_issues_t.c["Quantity"], pending_issues_t.c["Site_ID"],
        pending_issues_t.c["Date"], pending_issues_t.c["Issued_To"],
        pending_issues_t.c["status"], pending_issues_t.c["Work_Type"],
    ).where(pending_issues_t.c["id"] == ref_id))).mappings().first()
    if row is None:
        return None
    qty = _f(row["Quantity"])
    s30 = await usage_stats(session, row["SAP_Code"], row["Site_ID"], 30)
    s60 = await usage_stats(session, row["SAP_Code"], row["Site_ID"], 60)
    first_time = s60["issues"] == 0
    deviation_pct = (round((qty - s30["mean_issue_qty"]) / s30["mean_issue_qty"] * 100, 1)
                     if s30["mean_issue_qty"] > 0 else None)
    z = (round((qty - s30["mean_issue_qty"]) / s30["std_issue_qty"], 2)
         if s30["std_issue_qty"] > 0 else None)
    d = _to_date(row["Date"])
    off_pattern = bool(d is not None and s30["weekdays"]
                       and d.weekday() not in s30["weekdays"])
    return {"kind": "staged-issue", "ref_id": row["id"], "sap_code": row["SAP_Code"],
            "site": row["Site_ID"], "qty": qty, "issued_to": row["Issued_To"] or "",
            "work_type": row["Work_Type"] or "", "status": row["status"],
            "stats_30d": s30, "stats_60d": s60, "first_time_material": first_time,
            "deviation_pct": deviation_pct, "z_score": z, "off_pattern_day": off_pattern}


async def xsite_features(session: AsyncSession, ref_id: int) -> dict | None:
    row = (await session.execute(select(
        requests_t.c["id"], requests_t.c["requesting_site"], requests_t.c["target_site"],
        requests_t.c["SAP_Code"], requests_t.c["requested_qty"], requests_t.c["status"],
    ).where(requests_t.c["id"] == ref_id))).mappings().first()
    if row is None:
        return None
    from ..stock import SQL_SITE_STOCK
    stock_rows = (await session.execute(text(
        f'SELECT * FROM ({SQL_SITE_STOCK}) s '
        f'WHERE s."SAP_Code" = :sap AND s."Site_ID" = :site'),
        {"sap": row["SAP_Code"], "site": row["target_site"]})).mappings().all()
    stock = _f(stock_rows[0]["Current_Stock"]) if stock_rows else 0.0
    s30 = await usage_stats(session, row["SAP_Code"], row["target_site"], 30)
    rate = s30["mean_daily_qty"]
    qty = _f(row["requested_qty"])

    def _days(quantity: float) -> float | None:
        return round(quantity / rate, 1) if rate > 0 else None

    return {"kind": "xsite", "ref_id": row["id"],
            "requesting_site": row["requesting_site"], "target_site": row["target_site"],
            "sap_code": row["SAP_Code"], "requested_qty": qty, "status": row["status"],
            "target_stock": round(stock, 3), "target_stats_30d": s30,
            "days_cover_now": _days(stock),
            "days_cover_after": _days(stock - qty),
            "stock_after": round(stock - qty, 3)}
