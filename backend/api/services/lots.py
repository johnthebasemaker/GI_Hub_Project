"""
backend/api/services/lots.py — Phase 16: which LOT a ledger row belongs to.

THE PROBLEM (PROPOSED_PHASE16_PLAN.md §1). The workbooks' `Serial No.` column
means two things. On a Surface Shield row it is the BATCH the can came from
(`3504`, `525106711A21425`); on anything else it is an EQUIPMENT / ASSET tag
(`GI-120237`). The sync stored both as `Serial_No` and never as `Lot_Number`,
so Live held 0 lots and FEFO had nothing to choose from.

THE RULE. Read the column by the item, not by the sheet:

    Surface Shield (lot-tracked)   Serial No. → Lot_Number (normalised);
                                   Serial_No keeps the cell as typed
    a ROLL item (UOM ROL)          Serial No. is the ROLL → Serial_No;
                                   its production BATCH → Lot_Number
                                   (the roll is a unit inside the lot — Q16-3)
    anything else                  Serial No. → Serial_No, exactly as before

`inventory.Lot_Tracked` overrides the automatic choice: the bricks are FALSE
(no batch, no expiry — Q16-4), set by alembic d8a3f6c1b2e9's data step.

⚠️ THE LOT FILE DESCRIBES, THE LEDGER COUNTS. Nothing in this module writes a
quantity. Lots are created from receipts that already exist; their balance is
always computed from the ledger (`stock.SQL_LOT_BALANCE`).

⚠️ HISTORY KEEPS THE LOT IT WAS GIVEN. FEFO auto-pick runs only on an issue
entered in the app (`ledger.post_consumption`). A workbook row with no batch
stays lot-less: re-tagging history would rewrite what the floor recorded.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A cell that means "no lot". Compared after upper-casing.
NONE_TOKENS = {"", "N/A", "NA", "N.A", "N.A.", "-", "--", "—", "NIL", "NONE", "NULL", "NAN"}
ROLL_UOMS = {"ROL", "ROLL", "ROLLS"}
# "4525 = 55 Cans; 1823 = 2 Cans" — several lots in one cell (Q16-2: the
# operator splits these into one row per lot; until then no lot is guessed).
_MULTI_RE = re.compile(r"[=;,&]|\bAND\b", re.I)
# "A4525" and "A 4525" are the same lot: one letter, then digits.
_LETTER_LOT_RE = re.compile(r"^([A-Z])\s*(\d+)$")
# A CHEMOLINE roll: `1O` + 11 digits; its first 10 characters are the batch
# (`Order No.` 1O25003382 → rolls 1O25003382001 … 1O25003382999).
_ROLL_RE = re.compile(r"^1O\d{11}$")


def norm_lot(v) -> Optional[str]:
    """One spelling per lot, everywhere it is compared.

        trim · collapse inner spaces · upper-case · 3504.0 → 3504
        "A4525" / "A 4525" → "A 4525" (the Lot Register's spelling)
    """
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = " ".join(str(v).split()).upper()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s in NONE_TOKENS:
        return None
    m = _LETTER_LOT_RE.match(s)
    if m:
        s = f"{m.group(1)} {m.group(2)}"
    return s


def is_multi(v) -> bool:
    """Several lots in one cell — reported, never split by guesswork."""
    return bool(v is not None and _MULTI_RE.search(str(v)))


def norm_roll(v) -> Optional[str]:
    """A roll number, with the one typing slip the operator ruled on (Q16-12b):
    rolls begin `1O` (letter O) — never `10` — so a leading `10` is corrected."""
    s = norm_lot(v)
    if not s:
        return None
    s = s.replace(" ", "")
    if s.startswith("10") and len(s) == 13 and s[2:].isdigit():
        s = "1O" + s[2:]
    return s


def roll_batch(roll: Optional[str]) -> Optional[str]:
    """The production batch a roll belongs to, by the numbering rule. The roll
    register (`lot_units`, from the Lot Register workbook) wins when it knows."""
    return roll[:10] if roll and _ROLL_RE.match(roll) else None


async def tracking(session: AsyncSession) -> dict[str, str]:
    """{SAP: 'lot' | 'roll'} for every lot-tracked item; absent = not tracked.

    Automatic rule: the controlled category (the same exact match the QC/MTC
    pipeline uses) is lot-tracked; `Lot_Tracked` overrides it either way. An
    item counted in rolls is a roll item."""
    from .quality import controlled_category
    cat = await controlled_category(session)
    rows = (await session.execute(text(
        'SELECT TRIM("SAP_Code"), "Category", UPPER(TRIM(COALESCE("UOM", \'\'))), '
        '"Lot_Tracked" FROM inventory'))).all()
    out: dict[str, str] = {}
    for sap, category, uom, flag in rows:
        tracked = flag if flag is not None else (str(category or "").strip() == cat)
        if sap and tracked:
            out[sap] = "roll" if uom in ROLL_UOMS else "lot"
    return out


async def roll_register(session: AsyncSession) -> dict[tuple[str, str], str]:
    """{(SAP, roll): batch} from `lot_units`."""
    rows = (await session.execute(text(
        'SELECT "SAP_Code", "Unit_No", "Lot_Number" FROM lot_units'))).all()
    return {(r[0], r[1]): r[2] for r in rows}


def interpret(serial, mode: Optional[str], *, sap: str = "",
              register: Optional[dict] = None) -> dict:
    """What one `Serial No.` cell means for one item.

    Returns {serial_no, lot, note}: `serial_no` is what goes in Serial_No,
    `lot` in Lot_Number, `note` one of None / 'multi' / 'roll_fixed' /
    'roll_unknown' for the sync report."""
    raw = None if serial is None else " ".join(str(serial).split()) or None
    if mode is None:
        return {"serial_no": raw, "lot": None, "note": None}
    if raw is None or raw.upper() in NONE_TOKENS:
        return {"serial_no": raw, "lot": None, "note": None}
    if is_multi(raw):
        return {"serial_no": raw, "lot": None, "note": "multi"}
    if mode == "roll":
        roll = norm_roll(raw)
        batch = (register or {}).get((sap, roll)) or roll_batch(roll)
        note = "roll_fixed" if roll and roll != raw.upper().replace(" ", "") else None
        if batch is None:
            note = "roll_unknown"
        return {"serial_no": roll, "lot": batch, "note": note}
    return {"serial_no": raw, "lot": norm_lot(raw), "note": None}


# ── lots made from the ledger ────────────────────────────────────────────────
async def sync_lots_from_ledger(session: AsyncSession, site_id: str) -> dict:
    """One `lots` row per (Lot, SAP, Site) a RECEIPT names. Idempotent.

    Received_Date = the lot's first receipt; Status 'open'; Source 'receipt'.
    An existing lot keeps everything already on it (a lot-file MFD / expiry, an
    admin's status) — only a missing Received_Date is filled."""
    res = await session.execute(text('''
        INSERT INTO lots ("Lot_Number", "SAP_Code", "Site_ID", "Received_Date",
                          "Expiry_Date", "Supplier", "PR_Number", "Status", "Source", "DN_No")
        SELECT r."Lot_Number", TRIM(r."SAP_Code"), r."Site_ID",
               MIN(SUBSTRING(r."Date" FROM 1 FOR 10)),
               MIN(NULLIF(r."Expiry_Date", '')), MIN(r."Supplier"), MIN(r."PR_Number"),
               'open', 'receipt', MIN(COALESCE(r."DN_No", r."DN_Number"))
        FROM receipts r
        WHERE r."Site_ID" = :site AND COALESCE(r."Lot_Number", '') <> ''
        GROUP BY r."Lot_Number", TRIM(r."SAP_Code"), r."Site_ID"
        ON CONFLICT ("Lot_Number", "SAP_Code", "Site_ID") DO UPDATE
           SET "Received_Date" = COALESCE(lots."Received_Date", EXCLUDED."Received_Date")
           WHERE lots."Received_Date" IS NULL
        RETURNING (xmax = 0) AS inserted'''), {"site": site_id})
    flags = [bool(r[0]) for r in res.all()]
    return {"created": sum(flags), "filled": len(flags) - sum(flags)}


async def unknown_lots(session: AsyncSession, site_id: str) -> list[dict]:
    """Lots the Consumption / Return Logs name that no receipt brought in for
    that SAP — `3502` on the 1 MM PU, Phenacin A drawing ACP powder's batch.
    Reported, never blocked: the consumption still counts toward stock."""
    rows = (await session.execute(text('''
        SELECT 'consumption' AS kind, TRIM(c."SAP_Code") AS sap, c."Lot_Number" AS lot,
               COUNT(*) AS n, SUM(c."Quantity") AS qty
        FROM consumption c
        WHERE c."Site_ID" = :site AND COALESCE(c."Lot_Number", '') <> ''
          AND NOT EXISTS (SELECT 1 FROM lots l WHERE l."Lot_Number" = c."Lot_Number"
                          AND l."SAP_Code" = TRIM(c."SAP_Code") AND l."Site_ID" = c."Site_ID")
        GROUP BY 2, 3
        UNION ALL
        SELECT 'returns', TRIM(t."SAP_Code"), t."Lot_Number", COUNT(*), SUM(t."Quantity")
        FROM returns t
        WHERE t."Site_ID" = :site AND COALESCE(t."Lot_Number", '') <> ''
          AND NOT EXISTS (SELECT 1 FROM lots l WHERE l."Lot_Number" = t."Lot_Number"
                          AND l."SAP_Code" = TRIM(t."SAP_Code") AND l."Site_ID" = t."Site_ID")
        GROUP BY 2, 3
        ORDER BY 2, 3'''), {"site": site_id})).mappings().all()
    # The lot exists for ANOTHER SAP: the likeliest explanation, worth saying.
    elsewhere = {}
    for r in (await session.execute(text(
            'SELECT "Lot_Number", string_agg(DISTINCT "SAP_Code", \', \') FROM lots '
            'WHERE "Site_ID" = :site GROUP BY 1'), {"site": site_id})).all():
        elsewhere[r[0]] = r[1]
    return [dict(r, qty=float(r["qty"] or 0), received_under=elsewhere.get(r["lot"]))
            for r in rows]


# ── the expiry notice (Phase 16c) ─────────────────────────────────────────────
EXPIRY_NOTICE_DAYS = 30


async def expiry_notices(session: AsyncSession, *, today=None) -> dict:
    """ONE notice per site, per day, to the store keeper and the HOD: the lots
    with stock left that expire within 30 days, and those already expired.

    ⚠️ NEVER ONE PER LOT PER DAY — a list of forty identical pings teaches
    people to ignore the bell. Called from the evening digest's daily run,
    which already holds the one-worker claim (services/dailyjob.py), so it
    cannot fire twice. WhatsApp copies go into that evening's digest, not out
    one by one."""
    import datetime as _dt

    from ..lot_register import _rows
    from .notifications import dispatch
    today = today or _dt.date.today()
    rows = [r for r in await _rows(session, site=None)
            if r["status"] in ("expired", "expiring_30")]
    by_site: dict[str, list[dict]] = {}
    for r in rows:
        by_site.setdefault(r["Site_ID"] or "HQ", []).append(r)
    sent = 0
    for site, items in by_site.items():
        items.sort(key=lambda r: (r["days_left"] if r["days_left"] is not None else 0))
        expired = [r for r in items if r["status"] == "expired"]
        soon = [r for r in items if r["status"] != "expired"]
        title = (f"{len(soon)} lot(s) expire within {EXPIRY_NOTICE_DAYS} days"
                 + (f", {len(expired)} already expired" if expired else "")
                 + f" — {site}")
        body = " · ".join(
            f"{r['SAP_Code']} lot {r['Lot_Number']} "
            + (f"expired {-r['days_left']}d ago" if r["status"] == "expired"
               else f"in {r['days_left']}d")
            + f" ({r['Remaining_Qty']:g} left)" for r in items[:12])
        if len(items) > 12:
            body += f" · +{len(items) - 12} more"
        for role in ("store_keeper", "hod"):
            await dispatch(session, event_key="lot_expiry", title=title, body=body,
                           severity="warning", recipient_role=role, recipient_site=site,
                           link_page="/lots", related_table="lots",
                           related_ref=f"{site}:{today.isoformat()}", delivery="evening")
        sent += 1
    return {"sites": sent, "lots": len(rows)}
