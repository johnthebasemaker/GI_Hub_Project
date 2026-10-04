"""
backend/api/stock.py — derived (computed) stock endpoints.

These reproduce the SQLite "v_*" reporting views as Postgres-native SQL run at
request time (the views themselves are NOT created on PG — see the pivot note in
docs/POSTGRES_MIGRATION.md; the API computes them instead). The SQL is a faithful
port of the SQLite view definitions in backend/models.py, with:
  * mixed-case identifiers double-quoted (PG folds unquoted names),
  * every non-aggregated column added to GROUP BY (PG is strict; SQLite is not),
  * SQLite date math (julianday / date('now') / date('now','+30 days'))
    rewritten as PG date arithmetic (date - date -> int days; ((now() AT TIME ZONE 'UTC')::date)(+30)).

Parity against the SQLite views on the real data is asserted by
backend/api/parity_check.py — run it after changing any SQL here.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_roles, resolve_site_param, resolve_site_write,
                   site_scope)
from .db import get_session


def _empty_page(limit: int, offset: int) -> dict:
    return {"total": 0, "limit": limit, "offset": offset, "count": 0, "items": []}

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
# ⚠️ PHASE 19a: a site's ACCEPTED minimum (inventory_site_overrides, written
# when the HOD accepts a recommendation — POST /stock/smart-min/accept) beats
# the item's global Minimum_Qty, exactly as legacy's get_min_qty_for resolved
# it: COALESCE(site override, inventory default, 0). Every consumer of this
# SQL (Dashboard, low-stock, HOD auto-draft, reports, WhatsApp STOCK) sees the
# per-site minimum with no change of its own. The frozen SQLite v_site_stock
# never had the join, so the parity checker runs SQL_SITE_STOCK_PARITY below.
_SITE_STOCK_TMPL = """
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
    {min_expr}                           AS "Minimum_Qty",
    SUM(a.rec)                           AS "Total_Received",
    SUM(a.con)                           AS "Total_Consumed",
    SUM(a.ret)                           AS "Total_Returned",
    SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS "Current_Stock"
FROM activity a
LEFT JOIN inventory i ON TRIM(i."SAP_Code") = a."SAP_Code"{join}
GROUP BY a."SAP_Code", a."Site_ID",
         i."Equipment_Description", i."Material_Code", i."UOM", i."Minimum_Qty"{group}
"""
SQL_SITE_STOCK = _SITE_STOCK_TMPL.format(
    min_expr='COALESCE(o."Minimum_Qty", i."Minimum_Qty", 0)',
    join=('\nLEFT JOIN inventory_site_overrides o\n'
          '       ON TRIM(o."SAP_Code") = a."SAP_Code" AND o."Site_ID" = a."Site_ID"'),
    group=', o."Minimum_Qty"')
SQL_SITE_STOCK_PARITY = _SITE_STOCK_TMPL.format(
    min_expr='COALESCE(i."Minimum_Qty", 0)', join="", group="")

# v_lot_balance — per-lot remaining quantity.
# Phase 16: remaining = received − consumed − RETURNED ± transfers. A returned
# can is no longer on the shelf; before Phase 16 `returns` had no lot column and
# the balance could not see it. Plus what the Lot Register workbook knows
# (MFD, where the expiry came from, batch reference, DN) — descriptive only.
_LOT_SUM = """COALESCE((
        SELECT SUM(x."Quantity") FROM {t} x
        WHERE x."Lot_Number" = l."Lot_Number"
          AND TRIM(x."SAP_Code") = l."SAP_Code"
          AND COALESCE(x."Site_ID",'HQ') = l."Site_ID"
    ), 0)"""
_LOT_XFER = """COALESCE((
        SELECT SUM(t."Qty") FROM lot_transfers t
        WHERE t."{col}" = l."Lot_Number"
          AND t."SAP_Code" = l."SAP_Code"
          AND COALESCE(t."Site_ID",'HQ') = l."Site_ID"
    ), 0)"""
# A roll batch (CHEMOLINE): its receipts carry no roll numbers — 36 rolls
# arrive as one "36 ROL" line — so what arrived per batch is the ROLL REGISTER
# (`lot_units`, from the Lot Register workbook). Received = whichever is larger:
# the receipts naming the batch, or its registered rolls (0 for a non-roll lot).
_LOT_UNITS = """COALESCE((
        SELECT COUNT(*) FROM lot_units u
        WHERE u."Lot_Number" = l."Lot_Number"
          AND u."SAP_Code" = l."SAP_Code"
          AND u."Site_ID" = l."Site_ID"
    ), 0)"""
_LOT_RECEIVED = f"GREATEST({_LOT_SUM.format(t='receipts')}, {_LOT_UNITS})"
SQL_LOT_BALANCE = f"""
SELECT
    l."Lot_Number",
    l."SAP_Code",
    l."Site_ID",
    l."Received_Date",
    l."Expiry_Date",
    l."Supplier",
    l."PR_Number",
    l."Status",
    l."MFD_Date",
    l."Expiry_Source",
    l."Batch_Ref",
    l."DN_No",
    l."Source",
    {_LOT_RECEIVED} AS "Received_Qty",
    {_LOT_SUM.format(t="consumption")} AS "Consumed_Qty",
    {_LOT_SUM.format(t="returns")} AS "Returned_Qty",
    {_LOT_RECEIVED}
    - {_LOT_SUM.format(t="consumption")}
    - {_LOT_SUM.format(t="returns")}
    - {_LOT_XFER.format(col="From_Lot")}
    + {_LOT_XFER.format(col="To_Lot")} AS "Remaining_Qty"
FROM lots l
"""

# Parity projection (tools/parity_check.py). The frozen SQLite `v_lot_balance`
# predates Phase 16: it has none of the descriptive columns, its `returns` carry
# no lot and it has no `lot_units`. So parity is asserted on the legacy column
# set, with the returned quantity added back to Remaining — the ONE place the
# two balances must differ. The roll register is not adjusted for: the legacy
# schema has no `lot_units` table, so the GREATEST() is a no-op wherever a
# SQLite source exists to compare against.
SQL_LOT_BALANCE_PARITY = f"""
SELECT lb."Lot_Number", lb."SAP_Code", lb."Site_ID", lb."Received_Date",
       lb."Expiry_Date", lb."Supplier", lb."PR_Number", lb."Status",
       lb."Received_Qty", lb."Consumed_Qty",
       lb."Remaining_Qty" + lb."Returned_Qty" AS "Remaining_Qty"
FROM ({SQL_LOT_BALANCE}) lb
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
    (CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date) - ((now() AT TIME ZONE 'UTC')::date))
                                         AS "Days_Until_Expiry",
    CASE
        WHEN CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date) < ((now() AT TIME ZONE 'UTC')::date)
            THEN 'Expired'
        WHEN CAST(substring(r."Expiry_Date" FROM 1 FOR 10) AS date)
             <= (((now() AT TIME ZONE 'UTC')::date) + 30)
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
    "by-site":  {"sql": SQL_SITE_STOCK, "site": True,  "order": '"SAP_Code", "Site_ID"',      "view": "v_site_stock",
                 "parity_sql": SQL_SITE_STOCK_PARITY},
    "lots":     {"sql": SQL_LOT_BALANCE, "site": True, "order": '"Lot_Number", "SAP_Code"',   "view": "v_lot_balance",
                 "parity_sql": SQL_LOT_BALANCE_PARITY},
    "expiring": {"sql": SQL_EXPIRING,   "site": True,  "order": '"Days_Until_Expiry"',         "view": "v_expiring_stock"},
}

router = APIRouter(prefix="/stock", tags=["stock (derived)"])


async def _paged(session: AsyncSession, key: str, *, site_id: Optional[str],
                 limit: int, offset: int, extra_where: str = "",
                 extra_params: Optional[dict] = None,
                 q: Optional[str] = None, category: Optional[str] = None) -> dict:
    spec = DERIVED[key]
    filters, params = [], dict(extra_params or {})
    if spec["site"] and site_id is not None:
        filters.append('sub."Site_ID" = :site_id')
        params["site_id"] = site_id
    if q and q.strip():
        # Free-text: SAP code / lot number on the view itself, plus description
        # and category via the inventory master (all views expose SAP_Code).
        cols = ['sub."SAP_Code" ILIKE :q']
        if '"Lot_Number"' in spec["sql"]:
            cols.append('sub."Lot_Number" ILIKE :q')
        cols.append('sub."SAP_Code" IN (SELECT TRIM(i."SAP_Code") FROM inventory i '
                    'WHERE i."Equipment_Description" ILIKE :q OR i."Category" ILIKE :q)')
        filters.append("(" + " OR ".join(cols) + ")")
        params["q"] = f"%{q.strip()}%"
    if category and category.strip():
        filters.append('sub."SAP_Code" IN (SELECT TRIM(i."SAP_Code") FROM inventory i '
                       'WHERE TRIM(i."Category") = :category)')
        params["category"] = category.strip()
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
                     q: Optional[str] = Query(None, max_length=120),
                     category: Optional[str] = Query(None, max_length=80),
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    # This view aggregates across ALL sites (it has no Site_ID column), so a
    # site-scoped user reading it would leak other sites' quantities.
    if site_scope(user) is not None:
        raise HTTPException(403, "the global stock view is restricted to "
                                 "logistics/admin — use /stock/by-site")
    return await _paged(session, "live", site_id=None, limit=limit, offset=offset,
                        q=q, category=category)


@router.get("/by-site", summary="Current stock per SAP_Code + Site_ID — v_site_stock")
async def stock_by_site(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                        site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                        q: Optional[str] = Query(None, max_length=120),
                        category: Optional[str] = Query(None, max_length=80),
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return _empty_page(limit, offset)
    return await _paged(session, "by-site", site_id=site_id, limit=limit, offset=offset,
                        q=q, category=category)


@router.get("/smart-min", summary="Recommended minimum stock + red/amber/green per SAP and site")
async def smart_min(site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                    status: Optional[str] = Query(None, pattern="^(red|amber|green|none)$"),
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    """Phase 18 Track 4 — `services/smart_min.py` holds the method.

    Read-only and site-scoped exactly like `/stock/by-site`: a site-scoped
    user sees their own site, an unscoped one every site (or the one asked
    for). Nothing is written — a manual `Minimum_Qty` stays the operator's."""
    from .services import smart_min as SM
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": [], "counts": {"red": 0, "amber": 0, "green": 0, "none": 0},
                "sites": {}, "params": {}}
    out = await SM.compute(session, site_id or None)
    if status:
        out["items"] = [r for r in out["items"] if r["Status"] == status]
    return out


class SitePaceIn(BaseModel):
    site_id: Optional[str] = None
    # null or 0 clears the site's rate (back to the global rate / approved work)
    sqm_per_day: Optional[float] = Field(None, ge=0, le=100_000)


@router.put("/smart-min/pace",
            summary="Set (or clear) one site's planned SQM per day for Surface Shield minimums")
async def smart_min_pace(body: SitePaceIn = Body(...),
                         user: dict = Depends(require_roles("hod")),
                         session: AsyncSession = Depends(get_session)):
    """Ruling Q6 option B: a site plans its own lining rate. The Reorder
    signals tab suggests the site's approved SQM per day over the last 30 days
    and the HOD keeps it or types their own. A site HOD may only set their own
    site (`resolve_site_write`); admin names the site. Audited."""
    from .services import smart_min as SM
    from .services.ledger import write_audit
    site = resolve_site_write(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id is required")
    known = (await session.execute(text(
        'SELECT 1 FROM sme_equipment WHERE "Site_ID" = :s LIMIT 1'), {"s": site})).first()
    if known is None:
        raise HTTPException(422, f"{site} has no SQM plan — there is nothing to pace")
    key = SM.SITE_PACE_PREFIX + site
    rate = body.sqm_per_day or 0
    if rate > 0:
        val = f"{rate:g}"
        res = await session.execute(text("UPDATE app_settings SET value = :v WHERE key = :k"),
                                    {"k": key, "v": val})
        if res.rowcount == 0:
            await session.execute(text("INSERT INTO app_settings (key, value) VALUES (:k, :v)"),
                                  {"k": key, "v": val})
    else:
        await session.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
    await write_audit(session, user["username"], "SS_PACE_SET", "app_settings",
                      f"{key}={rate:g}" if rate > 0 else f"{key} cleared")
    await session.commit()
    return {"site_id": site, "sqm_per_day": rate or None}


class MinAcceptItem(BaseModel):
    site_id: Optional[str] = None
    sap_code: str = Field(..., min_length=1, max_length=60)
    minimum_qty: float = Field(..., ge=0, le=10_000_000)


class MinAcceptIn(BaseModel):
    items: list[MinAcceptItem] = Field(..., min_length=1, max_length=500)


@router.post("/smart-min/accept",
             summary="The HOD accepts (or edits) recommended minimums for their site")
async def smart_min_accept(body: MinAcceptIn = Body(...),
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    """Phase 19a, ruling Q19-1: only the HOD accepts (admin, as everywhere, is
    admitted by `require_roles`). Logistics reads the result and cannot accept.

    Each ticked row becomes the site's minimum in `inventory_site_overrides`.
    It beats the item's global Minimum_Qty everywhere SQL_SITE_STOCK is read,
    and it never expires: it stays until the HOD accepts another. The audit
    row keeps the recommendation it came from, so "why is the minimum 90?" has
    an answer. All or nothing: one row for a foreign site (403) or an unknown
    SAP (422) rejects the whole submission."""
    from .services import smart_min as SM
    from .services.ledger import write_audit
    from .services.notifications import notify

    rows = []
    for it in body.items:
        site = resolve_site_write(user, it.site_id)
        if not site:
            raise HTTPException(422, "site_id is required")
        rows.append((site, it.sap_code.strip(), float(it.minimum_qty)))
    saps = sorted({sap for _, sap, _ in rows})
    known = {r[0] for r in (await session.execute(text(
        'SELECT TRIM("SAP_Code") FROM inventory WHERE TRIM("SAP_Code") = ANY(:s)'),
        {"s": saps})).all()}
    unknown = [sap for sap in saps if sap not in known]
    if unknown:
        raise HTTPException(422, f"not in the item list: {', '.join(unknown[:10])}")

    recs: dict[tuple, dict] = {}
    for site in sorted({r[0] for r in rows}):
        for r in (await SM.compute(session, site))["items"]:
            recs[(r["SAP_Code"].strip(), site)] = r
    old = await SM.accepted_minimums(session, None)
    for site, sap, qty in rows:
        await session.execute(text(
            'INSERT INTO inventory_site_overrides ("SAP_Code", "Site_ID", "Minimum_Qty", '
            'updated_by, updated_at) VALUES (:p, :s, :q, :u, CURRENT_TIMESTAMP) '
            'ON CONFLICT ("SAP_Code", "Site_ID") DO UPDATE SET "Minimum_Qty" = :q, '
            'updated_by = :u, updated_at = CURRENT_TIMESTAMP'),
            {"p": sap, "s": site, "q": qty, "u": user["username"]})
        rec = recs.get((sap, site)) or {}
        was = old.get((SM._norm(sap), site))
        await write_audit(session, user["username"], "MIN_ACCEPT", "inventory_site_overrides",
                          f"{sap}@{site}: {was['qty'] if was else '-'} -> {qty:g} "
                          f"(recommended {rec.get('Recommended_Min', '-')}, "
                          f"basis {rec.get('Basis', '-')})")
    for site in sorted({r[0] for r in rows}):
        n = sum(1 for r in rows if r[0] == site)
        await notify(session, event_key="min_accepted", recipient_role="logistics",
                     title=f"{site}: {n} minimum(s) accepted by the HOD",
                     body=f"{user['username']} set {n} site minimum(s) on Reorder signals.",
                     link_page="/stock?tab=reorder")
    await session.commit()
    return {"accepted": len(rows),
            "items": [{"site_id": s_, "sap_code": p_, "minimum_qty": q_} for s_, p_, q_ in rows]}


@router.get("/lots", summary="Per-lot remaining quantity — v_lot_balance")
async def stock_lots(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                     site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                     q: Optional[str] = Query(None, max_length=120),
                     category: Optional[str] = Query(None, max_length=80),
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return _empty_page(limit, offset)
    return await _paged(session, "lots", site_id=site_id, limit=limit, offset=offset,
                        q=q, category=category)


@router.get("/expiring", summary="Receipts with expiry: days-to-expiry + status — v_expiring_stock")
async def stock_expiring(limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                         site_id: Optional[str] = Query(None, description="Filter by Site_ID"),
                         within_days: Optional[int] = Query(
                             None, description="Only rows expiring within N days (incl. already expired)"),
                         q: Optional[str] = Query(None, max_length=120),
                         category: Optional[str] = Query(None, max_length=80),
                         user: dict = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return _empty_page(limit, offset)
    extra_where, extra_params = "", {}
    if within_days is not None:
        extra_where = 'sub."Days_Until_Expiry" <= :within_days'
        extra_params["within_days"] = within_days
    return await _paged(session, "expiring", site_id=site_id, limit=limit, offset=offset,
                        extra_where=extra_where, extra_params=extra_params,
                        q=q, category=category)


# --- scan-to-dashboard material card (QR ecosystem, 2026-07-24) ----------------
#: Printed labels are not all bare SAP codes. This repo's generators emit one
#: (documents.py `_qr_png`), but the operator's older stickers carry
#: "1163|Cable Tie Wire ( Nylon)" — SAP, a delimiter, then the description.
#: The scanner parses this client-side too (frontend/src/lib/barcode.ts); doing
#: it here as well means an old cached bundle, a hand-typed code or a
#: third-party scanner app still resolves instead of 404-ing.
_SCAN_DELIMS = re.compile(r"[|;\t\n\r]")


_SCAN_TAGGED = re.compile(
    r"(?:sap|mat(?:erial)?)(?:[_ ]?code)?\s*[:=]\s*([A-Za-z0-9._/-]+)", re.I)


def _scan_tokens(raw: str) -> list[str]:
    """Identifier candidates inside a scanned payload, best first."""
    text = (raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    tagged = _SCAN_TAGGED.search(text)          # "SAP: 1163" / "MAT=GI-700…"
    cands = [text, *(_SCAN_DELIMS.split(text))]
    if tagged:
        cands.insert(1, tagged.group(1))
    for cand in cands:
        c = cand.strip()
        if c and c not in out:
            out.append(c)
    return out


async def _resolve_material(session: AsyncSession, raw: str):
    """First inventory row matching a scan, by SAP code or by Material_Code.

    Both sides are whitespace-normalized (ERP rows like "1043 - 2"), and the
    lookup is case-insensitive so a hand-typed "gi-7001394" resolves.
    """
    for tok in _scan_tokens(raw):
        norm = tok.replace(" ", "")
        row = (await session.execute(text('''
            SELECT "SAP_Code", "Equipment_Description", "Material_Code",
                   "Category", "UOM", "Site_ID", "Minimum_Qty", "Unit_Cost"
            FROM inventory
            WHERE REPLACE(TRIM("SAP_Code"), ' ', '') = :v
               OR UPPER(REPLACE(TRIM(COALESCE("Material_Code", '')), ' ', '')) = UPPER(:v)
            ORDER BY CASE WHEN REPLACE(TRIM("SAP_Code"), ' ', '') = :v THEN 0 ELSE 1 END
            LIMIT 1'''), {"v": norm})).mappings().first()
        if row is not None:
            return row
    return None


@router.get("/material-card",
            summary="One material's stock, trend, lots and movements "
                    "(role-scoped: site-pinned for level <3, global for admin)")
async def material_card(sap: str = Query(..., max_length=200),
                        days: int = Query(30, ge=7, le=365),
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    """Backs the QR-scan Material Intelligence page. Scoping is the standard
    rule: site_scope() pins SK / supervisor / warehouse / HOD to their own
    site's ledger rows; admin & logistics see the global picture. `sap` accepts
    a SAP code, a Material_Code, or a raw label payload like
    "1163|Cable Tie Wire ( Nylon)" — see `_resolve_material`."""
    norm = sap.strip().replace(" ", "")
    if not norm:
        raise HTTPException(422, "sap must not be blank")
    scope = site_scope(user)
    if scope == "":
        raise HTTPException(403, "no site is assigned to your account")

    inv = await _resolve_material(session, sap)
    if inv is None:
        raise HTTPException(404, f"no inventory item matches {sap!r}")
    # Everything below keys on the RESOLVED SAP, not the scanned text — a scan
    # may have arrived as a Material_Code or a delimited label payload.
    norm = str(inv["SAP_Code"]).strip().replace(" ", "")

    site_w = "AND COALESCE(\"Site_ID\",'HQ') = :site" if scope else ""
    params: dict = {"sap": norm}
    if scope:
        params["site"] = scope

    stock = (await session.execute(text(f'''
        SELECT COALESCE((SELECT SUM("Quantity") FROM receipts
                         WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}), 0)
             - COALESCE((SELECT SUM("Quantity") FROM consumption
                         WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}), 0)
             - COALESCE((SELECT SUM("Quantity") FROM returns
                         WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}), 0)
        '''), params)).scalar() or 0

    import datetime as _dt
    window = [(_dt.date.today() - _dt.timedelta(days=i)).isoformat()
              for i in range(days - 1, -1, -1)]
    params["from"] = window[0]
    rows = (await session.execute(text(f'''
        SELECT substr("Date", 1, 10) AS d, 'r' AS k, SUM("Quantity") AS q
        FROM receipts
        WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap
          AND "Date" >= :from {site_w} GROUP BY 1
        UNION ALL
        SELECT substr("Date", 1, 10), 'c', SUM("Quantity")
        FROM consumption
        WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap
          AND "Date" >= :from {site_w} GROUP BY 1'''), params)).all()
    by_day: dict[str, dict] = {d: {"date": d, "received": 0.0, "consumed": 0.0}
                               for d in window}
    for d, k, q in rows:
        if d in by_day:
            by_day[d]["received" if k == "r" else "consumed"] += float(q or 0)
    series = list(by_day.values())
    # Running stock BACKWARDS from today: stock[i-1] = stock[i] - received + consumed.
    # Gives the balance line its history without a second pass over the ledger.
    bal = float(stock)
    for pt in reversed(series):
        pt["balance"] = round(bal, 4)
        bal = bal - pt["received"] + pt["consumed"]

    consumed_win = sum(x["consumed"] for x in series)
    received_win = sum(x["received"] for x in series)
    # Burn rate over the WHOLE window (not just days with movement): a material
    # issued once a month burns slowly, and averaging only active days would
    # claim a month of stock is a day of stock.
    per_day = consumed_win / days if days else 0.0
    days_cover = round(float(stock) / per_day, 1) if per_day > 0 else None

    # Open lots, earliest expiry first — the FEFO picture for this material.
    # `lots` carries no quantity column (it is a lot REGISTER); the remaining
    # balance is derived per lot exactly as SQL_LOT_BALANCE does it.
    site_l = 'AND COALESCE(l."Site_ID",\'HQ\') = :site' if scope else ""
    lots = [dict(m) for m in (await session.execute(text(f'''
        SELECT l."Lot_Number", l."Expiry_Date", l."Status", l."Site_ID",
               COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                         WHERE r."Lot_Number" = l."Lot_Number"
                           AND r."SAP_Code" = l."SAP_Code"
                           AND COALESCE(r."Site_ID",'HQ') = l."Site_ID"), 0)
             - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                         WHERE c."Lot_Number" = l."Lot_Number"
                           AND c."SAP_Code" = l."SAP_Code"
                           AND COALESCE(c."Site_ID",'HQ') = l."Site_ID"), 0)
               AS "Remaining_Qty"
        FROM lots l
        WHERE REPLACE(TRIM(l."SAP_Code"),' ','') = :sap {site_l}
        ORDER BY COALESCE(NULLIF(l."Expiry_Date",''), '9999-12-31'), l."Lot_Number"
        LIMIT 25'''), params)).mappings().all()]

    # Last movements, newest first — what actually happened to this material.
    # Counterparty column differs per ledger: receipts name the supplier,
    # issues the recipient, returns carry only a reason.
    moves = [dict(m) for m in (await session.execute(text(f'''
        SELECT "Date" AS d, 'Received' AS kind, "Quantity" AS qty,
               COALESCE("Supplier",'') AS party, COALESCE("Site_ID",'') AS site
        FROM receipts
        WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}
        UNION ALL
        SELECT "Date", 'Issued', "Quantity",
               COALESCE("Issued_To",''), COALESCE("Site_ID",'')
        FROM consumption
        WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}
        UNION ALL
        SELECT "Date", 'Returned', "Quantity",
               COALESCE("Reason",''), COALESCE("Site_ID",'')
        FROM returns
        WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap {site_w}
        ORDER BY 1 DESC LIMIT 12'''), params)).mappings().all()]

    # Per-site split — only meaningful for the unscoped (global) roles.
    by_site: list[dict] = []
    if not scope:
        by_site = [{"site": r[0] or "—", "stock": round(float(r[1] or 0), 4)}
                   for r in (await session.execute(text('''
            SELECT COALESCE("Site_ID",'HQ') AS s, SUM(q) FROM (
                SELECT "Site_ID", "Quantity" AS q FROM receipts
                 WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap
                UNION ALL SELECT "Site_ID", -"Quantity" FROM consumption
                 WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap
                UNION ALL SELECT "Site_ID", -"Quantity" FROM returns
                 WHERE REPLACE(TRIM("SAP_Code"),' ','') = :sap
            ) t GROUP BY 1 ORDER BY 2 DESC'''), {"sap": norm})).all()]

    minimum = float(inv["Minimum_Qty"] or 0)
    return {"sap_code": str(inv["SAP_Code"]).strip(),
            "description": (inv["Equipment_Description"] or "").strip(),
            "material_code": (inv["Material_Code"] or "").strip() or None,
            "category": (inv["Category"] or "").strip(),
            "uom": (inv["UOM"] or "").strip(),
            "scope": scope or None,
            "current_stock": float(stock),
            "minimum_qty": minimum,
            "unit_cost": float(inv["Unit_Cost"] or 0),
            "stock_value": round(float(stock) * float(inv["Unit_Cost"] or 0), 2),
            "below_minimum": bool(minimum > 0 and float(stock) < minimum),
            "window_days": days,
            "avg_daily_consumption": round(per_day, 4),
            "days_of_cover": days_cover,
            "series": series,
            "lots": lots,
            "movements": moves,
            "by_site": by_site,
            "totals": {
                # The 30d names are kept: they are the shipped contract, and
                # the window defaults to 30. They mean "over window_days".
                "received_30d": round(received_win, 4),
                "consumed_30d": round(consumed_win, 4)}}
