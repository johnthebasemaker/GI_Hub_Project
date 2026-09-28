"""
backend/api/services/units.py — Phase 14a: PACKS and BASE units, converted in
ONE place.

A Surface Shield SAP is COUNTED in packs — Can, Bag, ROL: what the store
handles, what the Consumption Log records, what the ledger holds — and
MEASURED in base units — KG, M2, EA: what the recipe's `For_1_SQM`, the SME
seed and every benchmark speak. Before Phase 14 nothing converted between them,
and three latent defects followed (PROPOSED_PHASE14_PLAN.md §0). D1: the Phase
13 variance compared a CAN with a KILOGRAM. D2: the QR form's KG figure was
posted into a ledger kept in cans.

⚠️ THE LEDGER IS NEVER CONVERTED (operator ruling: convert at READ, store
packs). `consumption.Quantity`, `receipts.Quantity`, lots, minimums and
`Unit_Cost` stay per pack, so the Excel sync still matches workbook lines by
quantity (rule 3a), FEFO still counts drums and nothing historical is
rewritten. The base figure is DERIVED, here, whenever it is needed.

⚠️ ONE HOME, TWO TWINS. `factor()` (Python) and `FACTOR_SQL` (SQL) must agree;
suite 14A pins them against each other on every kind of row. A third copy —
a `* "Unit_Size"` inlined in some report — is how a unit leak starts, and a
source guard in suite 14A looks for one. The frontend's `lib/units.ts` is a
DISPLAY twin fed by `GET /meta/unit-sizes`, which reads THIS module.

THE FACTOR, precisely:

  · not a Surface Shield              → None. No conversion concept; the pack
                                        IS the unit, and nothing shows a base.
  · Unit Size given and > 0           → that number (Q14-4: the Inventory
                                        sheet's `Unit Size` is the single
                                        source of truth; the SME seed's
                                        `Package Size` is NOT consulted).
  · no Unit Size, but the pack is
    itself a MEASURE (KG, M2, EA …)   → 1. A kilogram "pack" holds a kilogram.
  · no Unit Size, container pack      → None — UNKNOWN. ⚠️ Never 1: treating
    (Can, Bag, ROL, Bottle …)           one can as one kilogram is exactly
                                        defect D1, and "cannot compute" is an
                                        honest answer where a wrong number is
                                        not.
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Pack UOMs that are already a MEASURE: a "pack" of these holds exactly one
# base unit. Compared upper-cased and trimmed. Containers (CAN, BAG, ROL,
# BOTTLE, DRUM, PAIL, BOX …) are deliberately absent.
MEASURE_UOMS = frozenset({
    "KG", "KGS", "G", "GM", "GR", "TON", "TONS", "MT",
    "L", "LT", "LTR", "LTRS", "LITRE", "LITER", "ML",
    "M", "MTR", "M2", "SQM", "SQ.M", "M3",
    "EA", "EACH", "NO", "NOS", "PC", "PCS",
})


def is_measure_uom(uom: Optional[str]) -> bool:
    return str(uom or "").strip().upper() in MEASURE_UOMS


# Phase 15d — a MASS pack whose Unit Size is not 1 is not its own base: the
# Inventory sheet counts Garnet in TON with Unit Size 1000, so one pack is
# 1000 KG, and calling that base "TON" read 9 TON of stock as 9000 TON.
MASS_UOMS = frozenset({"KG", "KGS", "G", "GM", "GR", "TON", "TONS", "MT"})


def _measure_base(pack_uom: Optional[str], unit_size) -> str:
    u = str(pack_uom or "").strip().upper()
    try:
        us = float(unit_size) if unit_size is not None else 1.0
    except (TypeError, ValueError):
        us = 1.0
    return "KG" if u in MASS_UOMS and abs(us - 1.0) > 1e-9 else u


def factor(*, is_surface_shield: bool, unit_size, pack_uom: Optional[str]) -> Optional[float]:
    """Base units per pack, or None. See the module docstring for the rules."""
    if not is_surface_shield:
        return None
    try:
        us = float(unit_size) if unit_size is not None else None
    except (TypeError, ValueError):
        us = None
    if us is not None and us > 0:
        return us
    if is_measure_uom(pack_uom):
        return 1.0
    return None


def base_qty(pack_qty, fac: Optional[float]) -> Optional[float]:
    """pack × factor, or None when the factor is unknown. Rounded to 4 dp —
    the precision the ledger already keeps."""
    if fac is None or pack_qty is None:
        return None
    return round(float(pack_qty) * fac, 4)


def pack_from_base(base, fac: Optional[float]) -> Optional[float]:
    """The inverse — used when a paper form was filled in KG (ruling Q14-3):
    the ledger still receives PACKS."""
    if fac is None or not fac or base is None:
        return None
    return round(float(base) / fac, 4)


def base_uom(*, pack_uom: Optional[str], stored: Optional[str],
             fac: Optional[float]) -> Optional[str]:
    """The unit a base figure is expressed in. The stored value wins (the sync
    fills it); a measure pack is its own base; an unknown factor has none."""
    if stored and str(stored).strip():
        return str(stored).strip()
    if fac is not None and is_measure_uom(pack_uom):
        return _measure_base(pack_uom, fac)
    return None


def default_base_uom(*, pack_uom: Optional[str], unit_size,
                     seed_uom: Optional[str]) -> Optional[str]:
    """What the sync stores in `inventory.Base_UOM` for a Surface Shield row.

    The SME seed's UOM names the unit the estimator measures this material in
    (KG, M2, EA …) — only its NAME is used, never its package size (Q14-4). A
    measure pack is its own base; otherwise a kilogram is the house default for
    a container of lining chemical.
    """
    if seed_uom and str(seed_uom).strip():
        u = str(seed_uom).strip().upper()
        return "M2" if u == "SQM" else u
    if is_measure_uom(pack_uom):
        return _measure_base(pack_uom, unit_size)
    try:
        return "KG" if unit_size is not None and float(unit_size) > 0 else None
    except (TypeError, ValueError):
        return None


# ── the SQL twin ──────────────────────────────────────────────────────────────
# `{i}` is the inventory alias. Needs the bind parameter `:ss_category` (the
# controlled category, from `quality.controlled_category`). Kept as a template
# so every caller joins inventory its own way and still shares ONE expression.
_MEASURE_LIST = ", ".join(f"'{u}'" for u in sorted(MEASURE_UOMS))
FACTOR_SQL = (
    "(CASE WHEN LOWER(TRIM({i}.\"Category\")) <> LOWER(:ss_category) THEN NULL "
    "      WHEN {i}.\"Unit_Size\" > 0 THEN {i}.\"Unit_Size\" "
    "      WHEN UPPER(TRIM({i}.\"UOM\")) IN (" + _MEASURE_LIST + ") THEN 1.0 "
    "      ELSE NULL END)")


def factor_sql(alias: str = "i") -> str:
    return FACTOR_SQL.format(i=alias)


def base_qty_sql(qty_expr: str, alias: str = "i") -> str:
    """`ROUND(qty × factor, 4)` — NULL when the factor is unknown."""
    return f"ROUND(CAST(({qty_expr}) * {factor_sql(alias)} AS numeric), 4)"


# ── lookups ───────────────────────────────────────────────────────────────────
async def _ss_category(session: AsyncSession) -> str:
    from . import quality
    return await quality.controlled_category(session)


def _norm(sap) -> str:
    return str(sap or "").replace(" ", "").strip()


async def unit_info(session: AsyncSession, sap: str) -> dict:
    """Everything a caller needs to convert one SAP. Never raises for an
    unknown SAP — returns a record with no factor."""
    cat = await _ss_category(session)
    row = (await session.execute(text(
        'SELECT i."SAP_Code", i."UOM", i."Category", i."Unit_Size", i."Base_UOM" '
        'FROM inventory i '
        "WHERE REPLACE(TRIM(i.\"SAP_Code\"), ' ', '') = :s LIMIT 1"),
        {"s": _norm(sap)})).mappings().first()
    if row is None:
        return {"sap": _norm(sap), "is_surface_shield": False, "pack_uom": None,
                "unit_size": None, "factor": None, "base_uom": None}
    ss = str(row["Category"] or "").strip().lower() == cat.strip().lower()
    fac = factor(is_surface_shield=ss, unit_size=row["Unit_Size"], pack_uom=row["UOM"])
    return {"sap": _norm(sap), "is_surface_shield": ss, "pack_uom": row["UOM"],
            "unit_size": row["Unit_Size"], "factor": fac,
            "base_uom": base_uom(pack_uom=row["UOM"], stored=row["Base_UOM"], fac=fac)}


async def unit_map(session: AsyncSession, saps: Optional[Iterable[str]] = None) -> dict:
    """{sap: unit_info} for every Surface Shield SAP (or just `saps`). Feeds
    `GET /meta/unit-sizes`, the frontend's only source for base figures."""
    cat = await _ss_category(session)
    rows = (await session.execute(text(
        'SELECT i."SAP_Code", i."UOM", i."Category", i."Unit_Size", i."Base_UOM" '
        'FROM inventory i WHERE LOWER(TRIM(i."Category")) = LOWER(:c)'),
        {"c": cat})).mappings().all()
    wanted = {_norm(s) for s in saps} if saps is not None else None
    out = {}
    for r in rows:
        k = _norm(r["SAP_Code"])
        if wanted is not None and k not in wanted:
            continue
        fac = factor(is_surface_shield=True, unit_size=r["Unit_Size"], pack_uom=r["UOM"])
        out[k] = {"sap": k, "is_surface_shield": True, "pack_uom": r["UOM"],
                  "unit_size": r["Unit_Size"], "factor": fac,
                  "base_uom": base_uom(pack_uom=r["UOM"], stored=r["Base_UOM"], fac=fac)}
    return out
