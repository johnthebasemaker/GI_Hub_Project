"""
backend/api/dashboard.py — aggregate metrics for the main Dashboard (Phase 5
visual parity): total valuation KPI + chart series for stock-vs-min, burn
forecast, and top-consumed. Read-only; site-scoped for low roles (the same
`resolve_site_param` pin the rest of the app uses). Visible to supervisor+
(level ≥ 1), matching the Dashboard's nav gate.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_level, resolve_site_param, site_filter_applies
from .db import get_session
from .stock import SQL_LOT_BALANCE, SQL_SITE_STOCK

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _cutoff(days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


@router.get("/metrics", summary="Dashboard KPIs + chart series (valuation, stock-vs-min, burn, top-consumed)")
async def metrics(site_id: Optional[str] = None,
                  user: dict = Depends(require_level(1)),
                  session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    scoped = site_filter_applies(site_id)
    sfilter = ' AND s."Site_ID" = :site' if scoped else ''
    csite = ' AND COALESCE(c."Site_ID", \'HQ\') = :site' if scoped else ''
    p: dict = {"site": site_id} if scoped else {}
    pc: dict = {**p, "cutoff": _cutoff(30)}

    async def rows(sql: str, params: dict):
        return [dict(m) for m in (await session.execute(text(sql), params)).mappings().all()]

    async def scalar(sql: str, params: dict):
        return (await session.execute(text(sql), params)).scalar_one()

    valuation = await scalar(f'''
        SELECT COALESCE(ROUND(CAST(SUM(s."Current_Stock"*COALESCE(i."Unit_Cost",0)) AS NUMERIC),2),0)
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE 1=1 {sfilter}''', p)

    stock_vs_min = await rows(f'''
        SELECT s."SAP_Code" AS sap, COALESCE(s."Equipment_Description",'') AS name,
               s."Current_Stock" AS current, s."Minimum_Qty" AS minimum
        FROM ({SQL_SITE_STOCK}) s
        WHERE s."Minimum_Qty" > 0 {sfilter}
        ORDER BY (s."Current_Stock" / NULLIF(s."Minimum_Qty",0)) ASC
        LIMIT 10''', p)

    top_consumed = await rows(f'''
        SELECT TRIM(c."SAP_Code") AS sap, MAX(i."Equipment_Description") AS name,
               ROUND(CAST(SUM(c."Quantity") AS NUMERIC),3) AS consumed
        FROM consumption c
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
        WHERE c."Date" >= :cutoff {csite}
        GROUP BY TRIM(c."SAP_Code") ORDER BY consumed DESC LIMIT 10''', pc)

    burn_forecast = await rows(f'''
        WITH burn AS (
          SELECT TRIM(c."SAP_Code") AS sap, SUM(c."Quantity")/30.0 AS daily
          FROM consumption c WHERE c."Date" >= :cutoff {csite}
          GROUP BY TRIM(c."SAP_Code")),
        stock AS (
          SELECT s."SAP_Code" AS sap, SUM(s."Current_Stock") AS cur
          FROM ({SQL_SITE_STOCK}) s WHERE 1=1 {sfilter} GROUP BY s."SAP_Code")
        SELECT b.sap AS sap,
               ROUND(CAST(b.daily AS NUMERIC),3) AS daily_avg,
               ROUND(CAST(COALESCE(st.cur,0) AS NUMERIC),3) AS current,
               CASE WHEN b.daily > 0
                    THEN ROUND(CAST(COALESCE(st.cur,0)/b.daily AS NUMERIC),1)
                    ELSE NULL END AS days_remaining
        FROM burn b LEFT JOIN stock st ON st.sap = b.sap
        WHERE b.daily > 0
        ORDER BY days_remaining ASC NULLS LAST LIMIT 10''', pc)

    # ── Top 5 Expiring (2026-08-05; Phase 21a) ───────────────────────────────
    # A WARNING widget, never a block (FEFO is allow-and-log), and it keeps lots
    # that have already expired (negative days): those need a decision today.
    # ⚠️ Phase 21a: it reads the ONE lot balance (`SQL_LOT_BALANCE`: received −
    # consumed − returned ± transfers) and shows only lots with stock LEFT. It
    # used to rank every `Status = 'open'` lot by date, and a lot whose stock
    # was all issued stays 'open' (ruling Q21-19: lots are never auto-closed at
    # zero — a return can bring one back), so empty lots filled the widget.
    lsite = ' AND lb."Site_ID" = :site' if scoped else ''
    top_expiring = await rows(f'''
        SELECT lb."Lot_Number" AS lot, lb."SAP_Code" AS sap, lb."Site_ID" AS site,
               COALESCE(i."Equipment_Description", '') AS name,
               lb."Expiry_Date" AS expiry_date,
               (CAST(substring(lb."Expiry_Date" FROM 1 FOR 10) AS date) - CURRENT_DATE) AS days_left,
               ROUND(CAST(lb."Remaining_Qty" AS numeric), 3) AS remaining,
               COALESCE(i."UOM", '') AS uom
        FROM ({SQL_LOT_BALANCE}) lb
        LEFT JOIN LATERAL (SELECT x."Equipment_Description", x."UOM" FROM inventory x
                           WHERE TRIM(x."SAP_Code") = lb."SAP_Code" LIMIT 1) i ON TRUE
        WHERE substring(COALESCE(lb."Expiry_Date", '') FROM 1 FOR 10) ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
          AND COALESCE(lb."Status", 'open') = 'open'
          AND lb."Remaining_Qty" > 1e-9 {lsite}
        ORDER BY CAST(substring(lb."Expiry_Date" FROM 1 FOR 10) AS date) ASC, lb."Lot_Number"
        LIMIT 5''', p)

    # ── Highest Value (2026-08-05) ────────────────────────────────────────────
    # ⚠️ `Unit_Cost` DEFAULTS TO 0, so this is a partial picture by
    # construction. The widget therefore reports its own COVERAGE — "N of M
    # items have a unit cost" — rather than presenting a confidently wrong
    # total. Same principle as the ruling that keeps a GRAND TOTAL off the
    # generic xlsx path: a number stated without its caveat is worse than no
    # number, because it gets quoted.
    highest_value = await rows(f'''
        SELECT s."SAP_Code" AS sap, COALESCE(s."Equipment_Description",'') AS name,
               s."Current_Stock" AS qty, COALESCE(i."Unit_Cost",0) AS unit_cost,
               ROUND(CAST(s."Current_Stock"*COALESCE(i."Unit_Cost",0) AS NUMERIC),2) AS value
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE COALESCE(i."Unit_Cost",0) > 0 {sfilter}
        ORDER BY value DESC
        LIMIT 5''', p)
    value_coverage = await rows(f'''
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE COALESCE(i."Unit_Cost",0) > 0) AS priced
        FROM ({SQL_SITE_STOCK}) s
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = s."SAP_Code"
        WHERE 1=1 {sfilter}''', p)
    cov = value_coverage[0] if value_coverage else {"total": 0, "priced": 0}

    return {"valuation_total": float(valuation or 0), "site_id": site_id,
            "stock_vs_min": stock_vs_min, "top_consumed": top_consumed,
            "burn_forecast": burn_forecast,
            "top_expiring": top_expiring,
            "highest_value": highest_value,
            # The caveat travels WITH the data, so a consumer cannot render the
            # figures without it.
            "value_coverage": {"priced": int(cov.get("priced") or 0),
                               "total": int(cov.get("total") or 0)}}
