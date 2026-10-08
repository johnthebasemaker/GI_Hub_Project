"""
backend/api/ai/paper_compare.py — a consumption paper against the workbook rows
already typed from it (Phase 22e, ruling Q22-19).

The Consumption Log is one row per paper line, in paper order, with *Prepared
by* = the shift's preparer (measured 189/189 on 5–6 Oct). So a paper that is
already in the workbook has ONE row per line there, and OCR Import compares
instead of staging — staging it too would take the stock down twice.

PAIRING IS ONE TO ONE. Two identical lines (same item, tank, quantity and
worker — the operator's example) must pair with two workbook rows, never both
with the first. Four passes, strongest first, each over what is still unpaired:

    1. same SAP, quantity, tank and worker
    2. same SAP, quantity and tank
    3. same SAP and quantity
    4. same SAP                      → "differs", with what differs

then a paper line left over is MISSING from the workbook, and a workbook row
left over is EXTRA (typed, but not on this paper — usually another page of the
same shift). Within a pass, the nearest row in paper order wins, so a
duplicate's first copy pairs with the first row.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .paper_fields import tank_norm


def _name(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def _qty(v) -> Optional[float]:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _tank(v) -> str:
    """The comparable part of a tank: its normalised tag, or `others`."""
    s = str(v or "").strip()
    return "others" if s.lower().rstrip("s") == "other" else tank_norm(s)


async def workbook_rows(session: AsyncSession, site: str, date: str,
                        prepared_by: Optional[str]) -> list[dict]:
    """The workbook's consumption rows of that date (and preparer, when known),
    Surface Shields left out, in workbook order."""
    params = {"s": site, "d": date}
    who = ""
    if prepared_by:
        who = ('AND LOWER(TRIM(COALESCE(c."Prepared_By", c."Issued_By", \'\'))) '
               '= LOWER(TRIM(:p))')
        params["p"] = prepared_by
    rows = (await session.execute(text(f'''
        SELECT c.id, TRIM(c."SAP_Code") AS sap, c."Quantity" AS qty, c."Tank_No" AS tank,
               c."Work_Type" AS work_type, c."Issued_To" AS issued_to,
               c."Source_Sheet" AS sheet, c."Source_Row" AS row
        FROM consumption c
        WHERE c."Site_ID" = :s AND LEFT(c."Date", 10) = :d
          AND c."Source_Ref" LIKE 'XLSX:%'
          AND COALESCE(c."Item_Type", '') NOT ILIKE '%surface%'
          {who}
        ORDER BY c."Source_Row" NULLS LAST, c.id'''), params)).mappings().all()
    return [dict(r) for r in rows]


def align(paper: list[dict], workbook: list[dict]) -> dict:
    """{"lines": [{index, status: same|differs|missing, row, sheet, diffs}],
        "extra": [workbook rows not on the paper], "counts": {...}}"""
    P = [{"i": i, "sap": str(r.get("SAP_Code") or "").strip(), "qty": _qty(r.get("quantity")),
          "tank": _tank(r.get("tank")), "who": _name(r.get("issued_to")),
          "wt": str(r.get("work_type") or "").strip().upper()} for i, r in enumerate(paper)]
    W = [{"j": j, "sap": str(r["sap"] or "").strip(), "qty": _qty(r["qty"]),
          "tank": _tank(r["tank"]), "who": _name(r["issued_to"]),
          "wt": str(r["work_type"] or "").strip().upper(), "raw": r} for j, r in enumerate(workbook)]
    taken: set[int] = set()
    pair: dict[int, int] = {}
    passes = (("sap", "qty", "tank", "who"), ("sap", "qty", "tank"), ("sap", "qty"), ("sap",))
    for keys in passes:
        for p in P:
            if p["i"] in pair or not p["sap"]:
                continue
            best = None
            for w in W:
                if w["j"] in taken or any(p[k] != w[k] for k in keys):
                    continue
                # nearest in order: the workbook keeps the paper's order
                dist = abs(w["j"] - p["i"])
                if best is None or dist < best[0]:
                    best = (dist, w["j"])
            if best is not None:
                pair[p["i"]] = best[1]
                taken.add(best[1])
    lines = []
    for p in P:
        if p["i"] not in pair:
            lines.append({"index": p["i"], "status": "missing", "row": None, "sheet": None,
                          "diffs": {}})
            continue
        w = W[pair[p["i"]]]
        diffs = {}
        if p["qty"] != w["qty"]:
            diffs["quantity"] = [p["qty"], w["qty"]]
        if p["tank"] and w["tank"] and p["tank"] != w["tank"]:
            diffs["tank"] = [paper[p["i"]].get("tank"), w["raw"]["tank"]]
        if p["wt"] and w["wt"] and p["wt"] != w["wt"]:
            diffs["work_type"] = [paper[p["i"]].get("work_type"), w["raw"]["work_type"]]
        if p["who"] and w["who"] and p["who"] != w["who"]:
            diffs["issued_to"] = [paper[p["i"]].get("issued_to"), w["raw"]["issued_to"]]
        lines.append({"index": p["i"], "status": "differs" if diffs else "same",
                      "row": w["raw"]["row"], "sheet": w["raw"]["sheet"], "diffs": diffs})
    extra = [{"row": w["raw"]["row"], "sheet": w["raw"]["sheet"], "SAP_Code": w["sap"],
              "quantity": w["qty"], "tank": w["raw"]["tank"]} for w in W if w["j"] not in taken]
    counts = {s: sum(1 for x in lines if x["status"] == s) for s in ("same", "differs", "missing")}
    counts["extra"] = len(extra)
    return {"lines": lines, "extra": extra, "counts": counts}
