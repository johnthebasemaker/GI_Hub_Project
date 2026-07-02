"""
backend/api/stock.py — derived (computed) stock endpoints.

These reproduce the SQLite "v_*" reporting views as Postgres-native SQL run at
request time (the views themselves are NOT created on PG — see the pivot note in
docs/POSTGRES_MIGRATION.md; the API computes them instead). The SQL is a faithful
port of the SQLite view definitions in backend/models.py, with:
  * mixed-case identifiers double-quoted (PG folds unquoted names),
  * every non-aggregated column added to GROUP BY (PG is strict; SQLite is not),
  * SQLite date math (julianday / date('now') / date('now','+30 days'))
    rewritten as PG date arithmetic (date - date -> int days; CURRENT_DATE(+30)).

Parity against the SQLite views on the real data is asserted by
backend/api/parity_check.py — run it after changing any SQL here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session

# --- ported view SQL (Postgres dialect) --------------------------------------

# v_live_stock — global (per SAP_Code) current stock.
SQL_LIVE_STOCK = """
SELECT
    TRIM(i."SAP_Code")               AS "SAP_Code",
    i."Equipment_Description"        AS "Equipment_Description",
    i."Material_Code"                AS "Material_Code",
    i."UOM"                          AS "UOM",
    COALESCE(i."Minimum_Qty", 0)     AS "Minimum_Qty",
    COALESCE(r."Total_Received", 0)  AS "Total_Received",
    COALESCE(c."Total_Consumed", 0)  AS "Total_Consumed",
    COALESCE(rt."Total_Returned", 0) AS "Total_Returned",
    COALESCE(r."Total_Received", 0)
      - COALESCE(c."Total_Consumed", 0)
      - COALESCE(rt."Total_Returned", 0) AS "Current_Stock"
FROM inventory i
LEFT JOIN (
    SELECT TRIM("SAP_Code") AS "SAP_Code", SUM("Quantity") AS "Total_Received"
    FROM receipts GROUP BY TRIM("SAP_Code")
) r  ON r."SAP_Code"  = TRIM(i."SAP_Code")
LEFT JOIN (
    SELECT TRIM("SAP_Code") AS "SAP_Code", SUM("Quantity") AS "Total_Consumed"
    FROM consumption GROUP BY TRIM("SAP_Code")
) c  ON c."SAP_Code"  = TRIM(i."SAP_Code")
LEFT JOIN (
    SELECT TRIM("SAP_Code") AS "SAP_Code", SUM("Quantity") AS "Total_Returned"
    FROM returns GROUP BY TRIM("SAP_Code")
) rt ON rt."SAP_Code" = TRIM(i."SAP_Code")
"""

# v_site_stock — per (SAP_Code, Site_ID) current stock.
SQL_SITE_STOCK = """
WITH activity AS (
    SELECT TRIM("SAP_Code") AS "SAP_Code", COALESCE("Site_ID",'HQ') AS "Site_ID",
           SUM("Quantity") AS rec, 0 AS con, 0 AS ret
    FROM receipts    GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
    UNION ALL
    SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'),
           0, SUM("Quantity"), 0
    FROM consumption GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
    UNION ALL
    SELECT TRIM("SAP_Code"), COALESCE("Site_ID",'HQ'),
           0, 0, SUM("Quantity")
    FROM returns     GROUP BY TRIM("SAP_Code"), COALESCE("Site_ID",'HQ')
)
SELECT
    a."SAP_Code"                         AS "SAP_Code",
    a."Site_ID"                          AS "Site_ID",
    i."Equipment_Description"            AS "Equipment_Description",
    i."Material_Code"                    AS "Material_Code",
    i."UOM"                              AS "UOM",
    COALESCE(i."Minimum_Qty", 0)         AS "Minimum_Qty",
    SUM(a.rec)                           AS "Total_Received",
    SUM(a.con)                           AS "Total_Consumed",
    SUM(a.ret)                           AS "Total_Returned",
    SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS "Current_Stock"
FROM activity a
LEFT JOIN inventory i ON TRIM(i."SAP_Code") = a."SAP_Code"
GROUP BY a."SAP_Code", a."Site_ID",
         i."Equipment_Description", i."Material_Code", i."UOM", i."Minimum_Qty"
"""

# v_lot_balance — per-lot remaining quantity (receipts - consumption +/- transfers).
SQL_LOT_BALANCE = """
SELECT
    l."Lot_Number",
    l."SAP_Code",
    l."Site_ID",
    l."Received_Date",
    l."Expiry_Date",
    l."Supplier",
    l."PR_Number",
    l."Status",
    COALESCE((
        SELECT SUM(r."Quantity") FROM receipts r
        WHERE r."Lot_Number" = l."Lot_Number"
          AND r."SAP_Code"   = l."SAP_Code"
          AND COALESCE(r."Site_ID",'HQ') = l."Site_ID"
    ), 0) AS "Received_Qty",
    COALESCE((
        SELECT SUM(c."Quantity") FROM consumption c
        WHERE c."Lot_Number" = l."Lot_Number"
          AND c."SAP_Code"   = l."SAP_Code"
          AND COALESCE(c."Site_ID",'HQ') = l."Site_ID"
    ), 0) AS "Consumed_Qty",
    COALESCE((
        SELECT SUM(r."Quantity") FROM receipts r
        WHERE r."Lot_Number" = l."Lot_Number"
          AND r."SAP_Code"   = l."SAP_Code"
          AND COALESCE(r."Site_ID",'HQ') = l."Site_ID"
    ), 0) - COALESCE((
        SELECT SUM(c."Quantity") FROM consumption c
        WHERE c."Lot_Number" = l."Lot_Number"
          AND c."SAP_Code"   = l."SAP_Code"
          AND COALESCE(c."Site_ID",'HQ') = l."Site_ID"
    ), 0)
    - COALESCE((
        SELECT SUM(t."Qty") FROM lot_transfers t
        WHERE t."From_Lot" = l."Lot_Number"
          AND t."SAP_Code" = l."SAP_Code"
          AND COALESCE(t."Site_ID",'HQ') = l."Site_ID"
    ), 0)
    + COALESCE((
        SELECT SUM(t."Qty") FROM lot_transfers t
        WHERE t."To_Lot" = l."Lot_Number"
          AND t."SAP_Code" = l."SAP_Code"
          AND COALESCE(t."Site_ID",'HQ') = l."Site_ID"
    ), 0) AS "Remaining_Qty"
FROM lots l
"""

# v_expiring_stock — receipts carrying an expiry, with days-to-expiry + status.
# SQLite date() is lenient (junk -> NULL); PG cast raises, so guard with a regex
# and cast the first 10 chars (YYYY-MM-DD).
SQL_EXPIRING = r"""
SELECT
    TRIM(r."SAP_Code")                   AS "SAP_Code",
    i."Equipment_Description"            AS "Equipment_Description",
    i."UOM"                              AS "UOM",
    COALESCE(r."Site_ID", 'HQ')          AS "Site_ID",
    r."Quantity"                         AS "Quantity",
    r."Supplier"                         AS "Supplier",
    r."PR_Number"                        AS "PR_Number",
    r."Expiry_Date"                      AS "Expiry_Date",
    (CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date) - CURRENT_DATE)
                                         AS "Days_Until_Expiry",
    CASE
        WHEN CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date) < CURRENT_DATE
            THEN 'Expired'
        WHEN CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date)
             <= (CURRENT_DATE + 30)
            THEN 'Short-Dated'
        ELSE 'Good'
    END                                  AS "Expiry_Status"
FROM receipts r
LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(r."SAP_Code")
WHERE r."Expiry_Date" IS NOT NULL
  AND r."Expiry_Date" <> ''
  AND substring(r."Expiry_Date" FROM 1 FOR 10) ~ '^\d{4}-\d{2}-\d{2}$'
"""

# Registry: name -> (sql, has_site_col, order_by). Used by the router and by the
# parity checker (which maps each back to its SQLite v_* view).
DERIVED = {
    "live":     {"sql": SQL_LIVE_STOCK, "site": False, "order": '"SAP_Code"',                 "view": "v_live_stock"},
    "by-site":  {"sql": SQL_SITE_STOCK, "site": True,  "order": '"SAP_Code", "Site_ID"',      "view": "v_site_stock"},
    "lots":     {"sql": SQL_LOT_BALANCE, "site": True, "order": '"Lot_Number", "SAP_Code"',   "view": "v_lot_balance"},
    "expiring": {"sql": SQL_EXPIRING,   "site": True,  "order": '"Days_Until_Expiry"',         "view": "v_expiring_stock"},
}

router = APIRouter(prefix="/stock", tags=["stock (derived)"])


async def _paged(session: AsyncSession, key: str, *, site_id: Optional[str],
                 limit: int, offset: int, extra_where: str = "",
                 extra_params: Optional[dict] = None) -> dict:
    spec = DERIVED[key]
    filters, params = [], dict(extra_params or {})
    if spec["site"] and site_id is not None:
        filters.append('sub."Site_ID" = :site_id')
        params["site_id"] = site_id
    if extra_where:
        filters.append(extra_where)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""

    total = (await session.execute(
        text(f'SELECT count(*) FROM ({spec["sql"]}) sub{where}'), params)).scalar_one()

    params.update(limit=limit, offset=offset)
    rows = (await session.execute(
        text(f'SELECT * FROM ({spec["sql"]}) sub{where} '
             f'ORDER BY {spec["order"]} LIMIT :limit OFFSET :offset'), params)).mappings().all()
    return {"total": total, "limit": limit, "offset": offset,
            "count": len(rows), "items": [dict(r) for r in rows]}


@router.get("/live", summary="Live stock per SAP_Code (global) — v_live_stock")
async def stock_live(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                     session: AsyncSession = Depends(get_session)):
    return await _paged(session, "live", site_id=None, limit=limit, offset=offset)


@router.get("/by-site", summary="Current stock per SAP_Code + Site_ID — v_site_stock")
async def stock_by_site(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                        site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                        session: AsyncSession = Depends(get_session)):
    return await _paged(session, "by-site", site_id=site_id, limit=limit, offset=offset)


@router.get("/lots", summary="Per-lot remaining quantity — v_lot_balance")
async def stock_lots(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                     site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                     session: AsyncSession = Depends(get_session)):
    return await _paged(session, "lots", site_id=site_id, limit=limit, offset=offset)


@router.get("/expiring", summary="Receipts with expiry: days-to-expiry + status — v_expiring_stock")
async def stock_expiring(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                         site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                         within_days: Optional[int] = Query(
                             None, description="Only rows expiring within N days (incl. already expired)"),
                         session: AsyncSession = Depends(get_session)):
    extra_where, extra_params = "", {}
    if within_days is not None:
        extra_where = 'sub."Days_Until_Expiry" <= :within_days'
        extra_params["within_days"] = within_days
    return await _paged(session, "expiring", site_id=site_id, limit=limit, offset=offset,
                        extra_where=extra_where, extra_params=extra_params)
