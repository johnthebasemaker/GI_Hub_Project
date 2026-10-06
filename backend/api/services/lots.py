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


# ── lot problems with the workbook's sheet and row (Phase 21a) ───────────────
# Brief Track 4.3: a consumption (or return) that names a lot which does not
# exist, or one already used up, must say WHERE in the workbook it is —
# "Consumption Log, row 5,581" — so the store keeper can find and fix it.
# ⚠️ REPORTED, NEVER BLOCKED (ruling Q21-18, and Phase 16's own "reported,
# never blocked"): the consumption still counts toward stock. The same walk
# runs on the workbook BEFORE a sync (`plan_lot_problems`, called by
# `bulk_import.plan_ledger`) and on the database AFTER it (`lot_problems`).
LOT_EPS = 1e-6
PROBLEM_TEXT = {
    "unknown_lot": "no receipt of this material brought this lot in",
    "lot_used_up": "the lot was already used up before this row",
}
_KIND_ORDER = {"receipt": 0, "consumption": 1, "returns": 2}


def _closest_lot(lot: str, candidates: set[str]) -> Optional[str]:
    from difflib import get_close_matches
    m = get_close_matches(lot, sorted(candidates), n=1, cutoff=0.75)
    return m[0] if m else None


def _walk(moves: list[dict], known: dict, credit: dict, saps_of_lot: dict,
          uom: dict) -> list[dict]:
    """The shared rule. `moves`: dicts with kind (receipt / consumption /
    returns), date, sap, lot, site, qty, sheet, row (and optional id, ref).
    `known[(site, sap)]` = lots that exist for that material; `credit[(site,
    sap, lot)]` = stock the lot holds that no dated move shows (a roll register
    larger than its receipts, app receipts, transfers in − out)."""
    probs: list[dict] = []
    bal: dict[tuple, float] = {}
    order = sorted(moves, key=lambda m: (str(m["date"] or "")[:10],
                                         _KIND_ORDER.get(m["kind"], 9),
                                         m.get("row") or 0, m.get("id") or 0))
    for m in order:
        key = (m["site"], m["sap"], m["lot"])
        if key not in bal:
            bal[key] = credit.get(key, 0.0)
        q = float(m["qty"] or 0)
        if m["kind"] == "receipt":
            bal[key] += q
            continue
        bal[key] -= q
        if m["lot"] not in known.get((m["site"], m["sap"]), set()):
            others = sorted(s_ for s_ in saps_of_lot.get((m["site"], m["lot"]), set())
                            if s_ != m["sap"])
            near = None if others else _closest_lot(
                m["lot"], known.get((m["site"], m["sap"]), set()))
            hint = (f"lot {m['lot']} exists under SAP {', '.join(others)}" if others else
                    f"closest lot of this material: {near}" if near else
                    "no similar lot of this material")
            probs.append(dict(_out(m), problem="unknown_lot", hint=hint))
        elif bal[key] < -LOT_EPS and q > LOT_EPS:
            over = min(-bal[key], q)
            probs.append(dict(_out(m), problem="lot_used_up",
                              hint=f"{over:g} {uom.get(m['sap'], '')} more than the lot "
                                   f"received".replace("  ", " ")))
    for p_ in probs:
        p_["problem_text"] = PROBLEM_TEXT[p_["problem"]]
    probs.sort(key=lambda p_: (p_["sheet"] or "~", p_["row"] or 10**9, p_["date"] or ""))
    return probs


def _out(m: dict) -> dict:
    return {"sheet": m.get("sheet"), "row": m.get("row"), "date": str(m["date"] or "")[:10],
            "sap": m["sap"], "lot": m["lot"], "qty": round(float(m["qty"] or 0), 4),
            "kind": m["kind"], "site": m["site"], "id": m.get("id")}


async def _known_and_credit(session: AsyncSession, site_id: Optional[str]):
    """Lots that exist (lots table + roll register) and the undated credit
    each holds (rolls beyond receipts, transfers), per site."""
    w, prm = ("WHERE l.\"Site_ID\" = :site", {"site": site_id}) if site_id else ("", {})
    known: dict[tuple, set] = {}
    saps_of_lot: dict[tuple, set] = {}
    for site, sap, lot in (await session.execute(text(
            f'SELECT l."Site_ID", TRIM(l."SAP_Code"), l."Lot_Number" FROM lots l {w} '
            'UNION SELECT u."Site_ID", TRIM(u."SAP_Code"), u."Lot_Number" FROM lot_units u '
            + ('WHERE u."Site_ID" = :site' if site_id else '')), prm)).all():
        lot = norm_lot(lot)
        if lot:
            known.setdefault((site, sap), set()).add(lot)
            saps_of_lot.setdefault((site, lot), set()).add(sap)
    credit: dict[tuple, float] = {}
    for site, sap, lot, n in (await session.execute(text(
            'SELECT u."Site_ID", TRIM(u."SAP_Code"), u."Lot_Number", COUNT(*) FROM lot_units u '
            + ('WHERE u."Site_ID" = :site ' if site_id else '') + 'GROUP BY 1, 2, 3'), prm)).all():
        credit[(site, sap, norm_lot(lot))] = float(n)          # rolls; receipts net off below
    for site, sap, frm, to, qty in (await session.execute(text(
            'SELECT COALESCE(t."Site_ID", \'HQ\'), t."SAP_Code", t."From_Lot", t."To_Lot", t."Qty" '
            'FROM lot_transfers t ' + ('WHERE COALESCE(t."Site_ID", \'HQ\') = :site' if site_id else '')),
            prm)).all():
        credit[("x", site, sap, norm_lot(frm))] = credit.get(("x", site, sap, norm_lot(frm)), 0.0) - float(qty or 0)
        credit[("x", site, sap, norm_lot(to))] = credit.get(("x", site, sap, norm_lot(to)), 0.0) + float(qty or 0)
    uom = {str(r[0]).strip(): str(r[1] or "") for r in (await session.execute(text(
        'SELECT "SAP_Code", "UOM" FROM inventory'))).all()}
    return known, saps_of_lot, credit, uom


def _settle_credit(credit: dict, received: dict) -> dict:
    """Rolls count only where they EXCEED the dated receipts (the balance is
    GREATEST(receipts, rolls)); transfers always count."""
    out: dict[tuple, float] = {}
    for k, v in credit.items():
        if k[0] == "x":
            key = k[1:]
            out[key] = out.get(key, 0.0) + v
        else:
            out[k] = out.get(k, 0.0) + max(0.0, v - received.get(k, 0.0))
    return out


async def plan_lot_problems(session: AsyncSession, site_id: str,
                            file_by_kind: dict[str, list[dict]]) -> list[dict]:
    """BEFORE a sync: the workbook's rows (as `bulk_import.plan_ledger` read
    them, `n` = Excel row) plus the receipts the APP recorded with a lot (they
    are not in the workbook). Workbook consumption is the record — the app's
    QR issues reach it through the reconcile's max(QR, Excel)."""
    known, saps_of_lot, credit, uom = await _known_and_credit(session, site_id)
    moves: list[dict] = []
    kind_of = {"receipts": "receipt", "consumption": "consumption", "returns": "returns"}
    for kind, rows in file_by_kind.items():
        k = kind_of.get(kind)
        for fr in rows or []:
            v = fr["vals"]
            lot = norm_lot(v.get("Lot_Number"))
            if not k or not lot:
                continue
            moves.append({"kind": k, "date": v.get("Date"), "sap": str(v["SAP_Code"]).strip(),
                          "lot": lot, "site": site_id, "qty": v.get("Quantity"),
                          "sheet": fr.get("sheet"), "row": fr.get("n")})
            if k == "receipt":
                known.setdefault((site_id, moves[-1]["sap"]), set()).add(lot)
                saps_of_lot.setdefault((site_id, lot), set()).add(moves[-1]["sap"])
    for d, sap, lot, qty in (await session.execute(text(
            'SELECT "Date", TRIM("SAP_Code"), "Lot_Number", "Quantity" FROM receipts '
            'WHERE "Site_ID" = :site AND COALESCE("Lot_Number", \'\') <> \'\' '
            'AND COALESCE("Source_Ref", \'\') NOT LIKE \'XLSX:%\''), {"site": site_id})).all():
        moves.append({"kind": "receipt", "date": d, "sap": sap, "lot": norm_lot(lot),
                      "site": site_id, "qty": qty, "sheet": None, "row": None})
    received: dict[tuple, float] = {}
    for m in moves:
        if m["kind"] == "receipt":
            k_ = (m["site"], m["sap"], m["lot"])
            received[k_] = received.get(k_, 0.0) + float(m["qty"] or 0)
    return _walk(moves, known, _settle_credit(credit, received), saps_of_lot, uom)


async def lot_problems(session: AsyncSession, site_id: Optional[str]) -> list[dict]:
    """AFTER a sync: the same walk over the database ledger. Rows the Excel
    sync wrote carry `Source_Sheet` / `Source_Row` (the row AT THE LAST SYNC —
    inserting rows in Excel moves them); app-written rows have none."""
    known, saps_of_lot, credit, uom = await _known_and_credit(session, site_id)
    w = 'AND x."Site_ID" = :site' if site_id else ''
    prm = {"site": site_id} if site_id else {}
    moves: list[dict] = []
    for kind, tbl, pos in (("receipt", "receipts", False), ("consumption", "consumption", True),
                           ("returns", "returns", True)):
        cols = 'x."Source_Sheet", x."Source_Row"' if pos else 'NULL, NULL'
        for rid, d, sap, lot, qty, site, sheet, row in (await session.execute(text(
                f'SELECT x.id, x."Date", TRIM(x."SAP_Code"), x."Lot_Number", x."Quantity", '
                f'COALESCE(x."Site_ID", \'HQ\'), {cols} FROM {tbl} x '
                f'WHERE COALESCE(x."Lot_Number", \'\') <> \'\' {w}'), prm)).all():
            moves.append({"kind": kind, "date": d, "sap": sap, "lot": norm_lot(lot),
                          "site": site, "qty": qty, "sheet": sheet, "row": row, "id": rid})
    received: dict[tuple, float] = {}
    for m in moves:
        if m["kind"] == "receipt":
            k_ = (m["site"], m["sap"], m["lot"])
            received[k_] = received.get(k_, 0.0) + float(m["qty"] or 0)
    return _walk(moves, known, _settle_credit(credit, received), saps_of_lot, uom)


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
