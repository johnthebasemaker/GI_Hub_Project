"""
backend/api/services/requests_sync.py — the *Pending Material Follow-up* folder:
what was requested, what has arrived, what is still pending (Phase 22d,
rulings Q22-12/13, plan §1.2 22d).

The folder holds the request workbooks the site sends by mail
(`Request 22-09-2026.xlsx`, `August Request.xlsx`, `Material Request 03-08-26
(upd 10-08-26).xlsx`, `CNCEC_RL Indent and Pending Supply (27-06-26) …`), an
older status table (`Received and Pending supply … (21-07-26)`, whose
"Without PR" lines are the April requests), and the roll-up
`CNCEC_Indents Over all Supply and Pending Details.xlsx`.

Ruling Q22-12: the REQUEST workbooks are the source; the roll-up is a CHECK —
its pending figure is compared with GI Hub's and every difference is listed
with sheet and row, never "fixed".

Four layouts, read by their HEADERS, not their positions:

    SAP Code · Material Code · Material Description / Material Name /
    Equipment Description · UOM · Qty / REQ. QTY. / Req. Qty. ·
    Received on dd/mm/yy … (one or more) / Received dd/mm/yy / Available QTY. ·
    Pending Qty / Balance · PR# ("Without PR") · Type · Remarks · Date

The request date is the row's Date, else the date in the file name (day
first), else the month in the name ("August Request" → 1 Aug), else none.

RECEIVED, BY GI HUB. A request line's received quantity is worked out from
the Receipt Log: receipts of that SAP at the site, on or after the request
date, given out to the requests oldest first (FIFO), so two requests for the
same item never both claim the same delivery. The workbook's own "Received
on …" figures are kept beside it, and a difference is flagged.

SURFACE SHIELDS are never part of this (the operator's brief): a line whose
material is a Surface Shield is skipped.

NO SAP CODE. A line whose Material Code is `N/A` and has no SAP Code cannot be
matched to stock: it is listed for the operator to add the SAP code in the
workbook (ruling Q22-12 — "you tell me, I will add it").

ON ORDER (Q22-13). A pending line requested WITHOUT a PR counts as on order in
Smart Reorder (`pending_no_pr`), labelled, so the reorder suggestion stops
asking for what was already requested. A line WITH a PR is left to the PO
tracking, which already counts it.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import re
import warnings
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SITE = "CNCEC"
SUMMARY_RX = re.compile(r"over\s*all", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_NA = {"", "N/A", "NA", "#N/A", "-", "NONE", "NIL"}


def _s(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return None if s.upper() in _NA else s


def _num(v) -> Optional[float]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))
        return float(m.group()) if m else None


def request_date_from_name(name: str, year_hint: int = 2026) -> Optional[_dt.date]:
    """The first date in the name, day first (`22-09-2026`, `03-08-26`,
    `(27-06-26)`), else the month (`August Request` → 1 Aug)."""
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{2,4})", name)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return _dt.date(y, mo, d)
        except ValueError:
            pass
    low = name.lower()
    for mname, mo in _MONTHS.items():
        if re.search(rf"\b{mname}\b", low):
            return _dt.date(year_hint, mo, 1)
    return None


def _date_cell(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if not v:
        return None
    m = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})", str(v))     # ISO (the ledger's Date)
    if m:
        try:
            return _dt.date(*(int(x) for x in m.groups()))
        except ValueError:
            return None
    return request_date_from_name(str(v))


# ── reading one workbook ──────────────────────────────────────────────────────
_COLS = {
    "sap": ("sap code", "sap"),
    "code": ("material code",),
    "desc": ("material description", "material name", "equipment description", "description"),
    "uom": ("uom",),
    "qty": ("req. qty.", "req. qty", "req qty", "qty", "quantity", "requested qty"),
    "pending": ("pending qty", "pending qty.", "balance", "pending"),
    "pr": ("pr#", "pr no", "pr"),
    "type": ("type",),
    "remarks": ("remarks",),
    "date": ("date",),
    "received_total": ("received qty.", "received qty", "available qty."),
}


def _header_index(row: tuple) -> Optional[dict]:
    cells = [re.sub(r"\s+", " ", str(c or "").strip().lower()) for c in row]
    idx: dict[str, Any] = {}
    for key, names in _COLS.items():
        for i, c in enumerate(cells):
            if c in names:
                idx.setdefault(key, i)
    idx["received"] = [i for i, c in enumerate(cells) if c.startswith("received ")
                       and i != idx.get("received_total")]
    if "qty" in idx and ("code" in idx or "sap" in idx) and "desc" in idx:
        return idx
    return None


def read_requests(data: bytes, name: str) -> dict:
    """{"layout", "date", "lines": [{sheet, row, sap, code, desc, uom, qty,
    wb_received, wb_pending, pr, without_pr, type, remarks, date}]} — every
    sheet, every row under a header row the reader recognises."""
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    # A STATUS table ("Received and Pending supply … (21-07-26)") is dated when
    # it was written, not when things were asked for: its lines have no request
    # date, so every receipt can count towards them.
    snapshot = bool(re.search(r"received\s+and\s+pending", name, re.I))
    file_date = None if snapshot else request_date_from_name(name)
    lines: list[dict] = []
    layout = None
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(min_row=1, values_only=True))
            idx = None
            for i, r in enumerate(rows, start=1):
                if idx is None:
                    idx = _header_index(r)
                    if idx:
                        layout = layout or ",".join(sorted(k for k in idx if idx[k] not in (None, [])))
                    continue
                g = lambda k: r[idx[k]] if k in idx and idx[k] < len(r) else None  # noqa: E731
                qty = _num(g("qty"))
                desc = _s(g("desc"))
                if not qty or not desc:
                    continue
                rec = [(_num(r[j]) or 0.0) for j in idx["received"] if j < len(r)]
                wb_rec = sum(rec) if idx["received"] else _num(g("received_total"))
                pr = _s(g("pr"))
                lines.append({
                    "sheet": ws.title, "row": i, "sap": _s(g("sap")), "code": _s(g("code")),
                    "desc": desc, "uom": _s(g("uom")), "qty": qty,
                    "wb_received": wb_rec, "wb_pending": _num(g("pending")),
                    "pr": pr, "without_pr": (pr is None) or ("without" in pr.lower()),
                    "type": _s(g("type")), "remarks": _s(g("remarks")),
                    "date": _date_cell(g("date")) or file_date})
    finally:
        wb.close()
    return {"layout": layout, "date": file_date, "lines": lines}


# ── resolving and allocating ─────────────────────────────────────────────────
async def _inventory(session: AsyncSession, site: str) -> tuple[dict, dict, set]:
    rows = (await session.execute(text(
        'SELECT TRIM("SAP_Code") AS sap, UPPER(TRIM(COALESCE("Material_Code", \'\'))) AS code, '
        '"Category" AS cat FROM inventory WHERE COALESCE("Site_ID", \'\') = :s'), {"s": site})).all()
    by_code = {r.code: r.sap for r in rows if r.code and r.code not in _NA}
    saps = {r.sap: r.cat for r in rows}
    ss = {r.sap for r in rows if r.cat and "surface" in r.cat.lower()}
    return by_code, saps, ss


def resolve_sap(line: dict, by_code: dict, saps: dict) -> Optional[str]:
    if line.get("sap") and line["sap"] in saps:
        return line["sap"]
    code = (line.get("code") or "").upper().replace(" ", "")
    return by_code.get(code) or (line["sap"] if line.get("sap") else None)


def allocate(lines: list[dict], receipts: dict[str, list[tuple]]) -> None:
    """FIFO: each line (oldest request first) takes receipts of its SAP dated on
    or after its request date that no earlier line took. Sets `gi_received`."""
    left = {sap: [[d, q] for d, q in sorted(rs)] for sap, rs in receipts.items()}
    for ln in sorted(lines, key=lambda x: (x.get("date") or _dt.date.min, x["id"])):
        ln["gi_received"] = 0.0
        sap = ln.get("sap_resolved")
        if not sap:
            continue
        need = ln["qty"]
        for rec in left.get(sap, []):
            if need <= 1e-9:
                break
            d, q = rec
            if q <= 1e-9 or (ln.get("date") and d < ln["date"]):
                continue
            take = min(q, need)
            rec[1] -= take
            need -= take
            ln["gi_received"] += take


async def _receipts(session: AsyncSession, site: str) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for sap, d, q in (await session.execute(text(
            'SELECT TRIM("SAP_Code"), "Date", "Quantity" FROM receipts '
            'WHERE COALESCE("Site_ID", \'\') = :s AND "Quantity" > 0'), {"s": site})).all():
        day = _date_cell(str(d)[:10]) if d else None
        if day:
            out.setdefault(sap, []).append((day, float(q or 0)))
    return out


# ── the sync (a Drive linker) ────────────────────────────────────────────────
async def link_requests(session: AsyncSession, *, read_bytes=None, site: str = SITE) -> dict:
    """Re-read every cached request workbook into material_requests /
    material_request_lines (a changed file replaces its lines). The roll-up is
    stored as a summary (`is_summary`), never as requests."""
    read_bytes = read_bytes or (lambda p: Path(p).read_bytes())
    files = (await session.execute(text(
        "SELECT id, name, cache_path, md5, modified_time FROM drive_files WHERE kind = 'pending' "
        "AND removed_at IS NULL AND cache_path IS NOT NULL AND lower(name) LIKE '%.xls%'"))).mappings().all()
    by_code, saps, ss = await _inventory(session, site)
    out = {"files": 0, "lines": 0, "skipped_surface_shield": 0, "no_sap": 0, "unreadable": []}
    live_ids = []
    for f in files:
        try:
            parsed = read_requests(read_bytes(f["cache_path"]), f["name"])
        except Exception:  # noqa: BLE001 — one unreadable file never stops the rest
            out["unreadable"].append(f["name"])
            continue
        summary = bool(SUMMARY_RX.search(f["name"]))
        rid = (await session.execute(text('''
            INSERT INTO material_requests (drive_file_id, file_name, request_date, layout,
                is_summary, "Site_ID", synced_at)
            VALUES (:f, :n, :d, :l, :s, :site, CURRENT_TIMESTAMP)
            ON CONFLICT (drive_file_id) DO UPDATE SET file_name = :n, request_date = :d,
                layout = :l, is_summary = :s, synced_at = CURRENT_TIMESTAMP
            RETURNING id'''), {"f": f["id"], "n": f["name"], "d": parsed["date"],
                               "l": parsed["layout"], "s": summary, "site": site})).scalar()
        live_ids.append(rid)
        await session.execute(text("DELETE FROM material_request_lines WHERE request_id = :r"),
                              {"r": rid})
        out["files"] += 1
        for ln in parsed["lines"]:
            sap = resolve_sap(ln, by_code, saps)
            if sap and sap in ss:
                out["skipped_surface_shield"] += 1
                continue
            if not sap:
                out["no_sap"] += 0 if summary else 1
            await session.execute(text('''
                INSERT INTO material_request_lines (request_id, sheet, row_no, "SAP_Code",
                    "Material_Code", description, uom, requested_qty, wb_received, wb_pending,
                    pr_ref, without_pr, item_type, remarks, request_date)
                VALUES (:r, :sh, :rw, :sap, :code, :desc, :uom, :qty, :wr, :wp, :pr, :wo, :ty,
                        :rm, :d)'''),
                {"r": rid, "sh": ln["sheet"], "rw": ln["row"], "sap": sap, "code": ln["code"],
                 "desc": ln["desc"], "uom": ln["uom"], "qty": ln["qty"], "wr": ln["wb_received"],
                 "wp": ln["wb_pending"], "pr": ln["pr"], "wo": ln["without_pr"],
                 "ty": ln["type"], "rm": ln["remarks"], "d": ln["date"]})
            out["lines"] += 0 if summary else 1
        await session.execute(text(
            "UPDATE drive_files SET link_status = 'linked', parsed_date = :d WHERE id = :i"),
            {"d": parsed["date"], "i": f["id"]})
    # files gone from Drive: their requests go too (the workbook was the source)
    await session.execute(text(
        "DELETE FROM material_request_lines WHERE request_id IN (SELECT id FROM material_requests "
        "WHERE NOT (id = ANY(:ids)))"), {"ids": live_ids or [-1]})
    await session.execute(text("DELETE FROM material_requests WHERE NOT (id = ANY(:ids))"),
                          {"ids": live_ids or [-1]})
    return out


# ── reading it back ──────────────────────────────────────────────────────────
async def _lines(session: AsyncSession, *, summary: bool) -> list[dict]:
    return [dict(r) for r in (await session.execute(text('''
        SELECT l.*, r.file_name, r.request_date AS file_date, r."Site_ID" AS site,
               r.drive_file_id
        FROM material_request_lines l JOIN material_requests r ON r.id = l.request_id
        WHERE r.is_summary = :s ORDER BY l.request_date NULLS FIRST, l.id'''),
        {"s": summary})).mappings().all()]


async def overview(session: AsyncSession, site: Optional[str]) -> dict:
    """Every request line with GI Hub's received and pending, the lines that
    need a SAP code, and the roll-up's disagreements."""
    lines = await _lines(session, summary=False)
    if site is not None:
        lines = [ln for ln in lines if (ln["site"] or "") == site]
    for ln in lines:
        ln["date"] = ln["request_date"]
        ln["sap_resolved"] = ln["SAP_Code"]
        ln["qty"] = float(ln["requested_qty"] or 0)
    by_site: dict[str, list] = {}
    for ln in lines:
        by_site.setdefault(ln["site"] or SITE, []).append(ln)
    for s, group in by_site.items():
        allocate(group, await _receipts(session, s))
    today = _dt.date.today()
    items = []
    for ln in lines:
        gi_rec = round(ln["gi_received"], 3)
        pending = round(max(ln["qty"] - gi_rec, 0.0), 3)
        wb_rec = ln["wb_received"]
        items.append({
            "id": ln["id"], "file": ln["file_name"], "drive_file_id": ln["drive_file_id"],
            "sheet": ln["sheet"], "row": ln["row_no"],
            "date": ln["request_date"].isoformat() if ln["request_date"] else None,
            "SAP_Code": ln["SAP_Code"], "Material_Code": ln["Material_Code"],
            "description": ln["description"], "uom": ln["uom"], "requested": ln["qty"],
            "received": gi_rec, "wb_received": wb_rec, "pending": pending,
            "wb_pending": ln["wb_pending"],
            "differs": wb_rec is not None and abs(float(wb_rec or 0) - gi_rec) > 1e-6,
            "pr": ln["pr_ref"], "without_pr": bool(ln["without_pr"]),
            "type": ln["item_type"], "remarks": ln["remarks"], "site": ln["site"],
            "age_days": (today - ln["request_date"]).days if ln["request_date"] else None,
        })
    needs_sap = [{"file": i["file"], "sheet": i["sheet"], "row": i["row"],
                  "Material_Code": i["Material_Code"], "description": i["description"]}
                 for i in items if not i["SAP_Code"]]
    return {"items": items, "needs_sap": needs_sap,
            "summary_check": await summary_check(session, items)}


async def summary_check(session: AsyncSession, items: list[dict]) -> list[dict]:
    """The roll-up workbook against GI Hub (Q22-12: a CHECK): each of its lines
    is matched to a request line by material code (or description) and date;
    a pending figure that differs is listed with the roll-up's sheet and row."""
    roll = await _lines(session, summary=True)
    out = []
    for r in roll:
        cands = [i for i in items
                 if ((r["Material_Code"] and i["Material_Code"] == r["Material_Code"])
                     or (not r["Material_Code"] and (i["description"] or "").lower()
                         == (r["description"] or "").lower()))
                 and (not r["request_date"] or not i["date"]
                      or i["date"] == r["request_date"].isoformat())]
        if not cands:
            out.append({"file": r["file_name"], "sheet": r["sheet"], "row": r["row_no"],
                        "description": r["description"], "rollup_pending": r["wb_pending"],
                        "gi_pending": None, "why": "no request workbook has this line"})
            continue
        gi = round(sum(i["pending"] for i in cands), 3)
        if r["wb_pending"] is not None and abs(float(r["wb_pending"]) - gi) > 1e-6:
            out.append({"file": r["file_name"], "sheet": r["sheet"], "row": r["row_no"],
                        "description": r["description"], "rollup_pending": r["wb_pending"],
                        "gi_pending": gi, "why": "pending differs"})
    return out


async def pending_no_pr(session: AsyncSession) -> dict[tuple[str, str], float]:
    """{(SAP, site): qty requested WITHOUT a PR and not yet received} — what
    Smart Reorder counts as on order (Q22-13)."""
    ov = await overview(session, None)
    out: dict[tuple[str, str], float] = {}
    for i in ov["items"]:
        if i["without_pr"] and i["SAP_Code"] and i["pending"] > 0:
            k = (i["SAP_Code"], i["site"] or SITE)
            out[k] = out.get(k, 0.0) + i["pending"]
    return out


def as_json(o: Any) -> str:
    return json.dumps(o, default=str)
