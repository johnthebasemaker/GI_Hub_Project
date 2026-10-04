"""
backend/api/services/sme_history.py — the Surface Shield daily log (Phase 20a).

WHAT MANAGEMENT ASKED FOR. One place that answers, day by day: what Surface
Shield material was drawn, what the field WROTE about it in the Excel
consumption log (the Remarks, where the SQM comes from), how many square
metres that job did, and whether the HOD approved it. Before this, those four
facts lived in three tables and two screens.

READ-ONLY, AND IT INVENTS NOTHING. Every figure comes from a row that already
exists:

  consumption                the draw, and `Remarks` VERBATIM (Excel's words)
  sme_consumption_log        one row per material: packs, KG, the priority
                             flag, the HOD's edit and justification
  sme_attribution_group      the JOB: one day, one equipment, one system code,
                             one SQM, its status and who decided it
  sme_consumption_revision   an Excel edit of an already-approved job
  sme_groups.queue()         draws not yet filed (no SQM yet)

THE GROUPING is the field's own (Phase 14c): DATE → JOB (equipment + system) →
MATERIALS. So a number here always matches a card on the Execution page.

THE STATUS, in the words the operator asked for (Phase 20 brief, Track 1):

  approved    the HOD approved it; its SQM counts
  pending     filed, with the HOD
  rejected    sent back with a reason; still in the field queue
  not_filed   drawn, but nobody has stated the area yet
  + `edited_in_excel` on an approved job whose workbook line changed after
    approval (the approved figures keep counting until the HOD decides)

⚠️ A REJECTED JOB THAT WAS RE-FILED IS NOT SHOWN TWICE. Re-filing moves its
rows into the new job (`sme_groups.submit`), so a rejected job with no rows
left has been superseded; the new job is the one shown.

⚠️ THE REMARK IS SHOWN AS TYPED (ruling Q20-3). `excel_remarks` are the
consumption rows' `Remarks`, distinct and in order, never parsed into
something else. `job_note` is what the supervisor/HOD filed, shown second and
only when it differs. `sqm_from_remark` is `parse_note`'s reading, set beside
the filed SQM so a difference is visible: that difference is the question
management asks.

⚠️ GARNET IS SURFACE PREP (Phase 15d): its own section (`kind = prep`). Its
area is benchmark-only and credits no lining SQM, so it is never summed into
"SQM approved".
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import sme_groups as G

STATUSES = ("approved", "pending", "rejected", "not_filed")
DEFAULT_DAYS = 30
MAX_DAYS = 366
_STATUS_OF = {"committed": "approved", "staged": "pending", "rejected": "rejected"}


def _r(x, nd: int = 3) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _norm(s) -> str:
    return " ".join(str(s or "").split()).lower()


def window(dfrom: Optional[str], dto: Optional[str]) -> tuple[str, str]:
    """(from, to) as ISO dates; the last DEFAULT_DAYS when not given."""
    try:
        d1 = _dt.date.fromisoformat(dto) if dto else _dt.date.today()
        d0 = (_dt.date.fromisoformat(dfrom) if dfrom
              else d1 - _dt.timedelta(days=DEFAULT_DAYS - 1))
    except ValueError:
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    if d0 > d1:
        raise HTTPException(422, "date_from must be on or before date_to")
    if (d1 - d0).days + 1 > MAX_DAYS:
        raise HTTPException(422, f"at most {MAX_DAYS} days at a time")
    return d0.isoformat(), d1.isoformat()


def _distinct(xs) -> list[str]:
    seen, out = set(), []
    for x in xs:
        t = " ".join(str(x or "").split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _sums(materials: list[dict]) -> dict:
    """Drawn quantity per base unit (KG, L…) and in packs per pack unit."""
    base: dict[str, float] = defaultdict(float)
    packs: dict[str, float] = defaultdict(float)
    for m in materials:
        if m.get("base_qty") is not None and m.get("base_uom"):
            base[m["base_uom"]] += float(m["base_qty"])
        if m.get("pack_qty") is not None:
            packs[m.get("pack_uom") or "pack"] += float(m["pack_qty"])
    return {"base": {k: round(v, 3) for k, v in base.items()},
            "packs": {k: round(v, 3) for k, v in packs.items()}}


async def _filed_jobs(session: AsyncSession, *, site_id: Optional[str], dfrom: str,
                      dto: str, prep: set[str], umap: dict) -> list[dict]:
    where = ['g."Work_Date" BETWEEN :a AND :b']
    p: dict = {"a": dfrom, "b": dto}
    if site_id:
        where.append('g."Site_ID" = :s')
        p["s"] = site_id
    groups = (await session.execute(text(
        f'SELECT g.* FROM sme_attribution_group g WHERE {" AND ".join(where)} '
        'ORDER BY g."Work_Date" DESC, g.id'), p)).mappings().all()
    if not groups:
        return []
    ids = [int(g["id"]) for g in groups]
    members = (await session.execute(text('''
        SELECT l.id, l.group_id, l."SAP_Code", l."Material_Code", l."Pack_Qty",
               l."Unit_Size_Used", l."Actual_Qty", l."Expected_Qty", l."Variance_Pct",
               l."Priority_Flag", l."HOD_Edit_Justification", l.hod_edited,
               l."Original_SQM_Completed", l.status AS log_status, l."Consumption_ID",
               c."Remarks", c."Quantity" AS c_qty, c."Lot_Number", c."Issued_By",
               inv."Equipment_Description", inv."UOM"
        FROM sme_consumption_log l
        LEFT JOIN consumption c ON c.id = l."Consumption_ID"
        LEFT JOIN LATERAL (SELECT "Equipment_Description", "UOM" FROM inventory i
                           WHERE TRIM(i."SAP_Code") = TRIM(l."SAP_Code") LIMIT 1) inv ON TRUE
        WHERE l.group_id = ANY(:ids)
        ORDER BY l.id'''), {"ids": ids})).mappings().all()
    revised = {int(r[0]) for r in (await session.execute(text(
        "SELECT DISTINCT \"Log_ID\" FROM sme_consumption_revision "
        "WHERE status = 'staged' AND \"Log_ID\" = ANY(:l)"),
        {"l": [int(m["id"]) for m in members]})).all()} if members else set()
    by: dict[int, list] = defaultdict(list)
    for m in members:
        by[int(m["group_id"])].append(m)

    out = []
    for g in groups:
        rows = by.get(int(g["id"]), [])
        status = _STATUS_OF.get(g["status"])
        if status is None:
            continue
        if status == "rejected" and not rows:
            continue                     # re-filed: the new job is the one shown
        code = str(g["Lining_System_Code"] or "").strip()
        mats = []
        for m in rows:
            u = umap.get(G._sap(m["SAP_Code"])) or {}
            pack_qty = m["Pack_Qty"] if m["Pack_Qty"] is not None else m["c_qty"]
            mats.append({"sap": m["SAP_Code"], "material_code": m["Material_Code"],
                         "description": m["Equipment_Description"],
                         "pack_qty": _r(pack_qty), "pack_uom": m["UOM"],
                         "base_qty": _r(m["Actual_Qty"]), "base_uom": u.get("base_uom"),
                         "expected_base": _r(m["Expected_Qty"]),
                         "variance_pct": _r(m["Variance_Pct"], 1),
                         "high_priority": m["Priority_Flag"] == "HIGH",
                         "lot": m["Lot_Number"], "issued_by": m["Issued_By"],
                         "remark": m["Remarks"]})
        excel = _distinct(m["Remarks"] for m in rows)
        note = " ".join(str(g["notes"] or "").split()) or None
        sqm = _r(g["SQM_Completed"])
        from_remark = G.sqm_hint(excel)
        just = next((m["HOD_Edit_Justification"] for m in rows
                     if m["HOD_Edit_Justification"]), None)
        orig = next((m["Original_SQM_Completed"] for m in rows
                     if m["hod_edited"] and m["Original_SQM_Completed"] is not None), None)
        out.append({
            "key": f"g{g['id']}", "group_id": int(g["id"]),
            "kind": "prep" if code in prep else "lining", "status": status,
            "edited_in_excel": any(int(m["id"]) in revised for m in rows),
            "site_id": g["Site_ID"], "work_date": str(g["Work_Date"])[:10],
            "tag": g["Equipment_Tag_No"], "code": code, "work_area": g["Work_Area"],
            "surface_state": g["Surface_State"],
            "sqm": sqm, "sqm_from_remark": _r(from_remark),
            "sqm_differs": (from_remark is not None and sqm is not None
                            and abs(float(from_remark) - float(sqm)) > 0.005),
            "original_sqm": _r(orig), "hod_justification": just,
            "excel_remarks": excel,
            "job_note": note if note and _norm(note) not in {_norm(x) for x in excel} else None,
            "submitted_by": g["submitted_by"],
            "submitted_at": g["submitted_at"].isoformat() if g["submitted_at"] else None,
            "decided_by": g["hod_username"],
            "decided_at": g["hod_decided_at"].isoformat() if g["hod_decided_at"] else None,
            "rejected_reason": g["rejected_reason"],
            "high_priority": any(x["high_priority"] for x in mats),
            "materials": mats, "drawn": _sums(mats)})
    return out


async def _unfiled_jobs(session: AsyncSession, *, site_id: Optional[str], dfrom: str,
                        dto: str, prep_saps: set[str], umap: dict) -> list[dict]:
    """Draws nobody has filed yet, as (date, equipment) jobs — the field
    queue's grouping, WITHOUT its system-code suggestions.

    ⚠️ LEAN ON PURPOSE. `sme_groups.queue()` ranks candidate codes per card
    (several queries a card) for a supervisor about to file; the log only
    reports, and calling the whole queue made every log read cost the field's
    work-screen query — enough, on the real ledger, to slow a supervisor's own
    refresh beside it (E2E caught the latency). Same sweep, same tank
    resolution, same grouping; only the window's rows are grouped."""
    from . import reconcile as RC
    from . import sme_link as SL
    from . import units as U
    sw = await SL.sweep(session, site_id=site_id, limit=5000, offset=0)
    resolvers: dict[str, object] = {}
    groups: dict[tuple, dict] = {}
    for it in sw["items"]:
        day = G._day(it.get("work_date"))
        if not (dfrom <= day <= dto):
            continue
        # a rejected row belongs to its rejected job (shown there); an Excel
        # edit of an approved row is a revision of that job
        if it.get("reason") in ("rejected", "edited"):
            continue
        site = it["site_id"]
        if site not in resolvers:
            resolvers[site] = await RC.resolver(session, site)
        tag, st = resolvers[site](it.get("tank_no"))
        if st != "mapped":
            continue                       # unmapped / non-equipment: no job to show
        kind = "prep" if G._sap(it.get("sap_code")) in prep_saps else "lining"
        g = groups.setdefault((site, day, tag, kind), {"site": site, "day": day,
                                                        "tag": tag, "kind": kind, "rows": []})
        g["rows"].append(it)
    out = []
    for (site, day, tag, kind), g in groups.items():
        mats = []
        for r in g["rows"]:
            u = umap.get(G._sap(r.get("sap_code")))
            mats.append({"sap": r.get("sap_code"), "material_code": r.get("material_code"),
                         "description": r.get("material_name"),
                         "pack_qty": _r(r.get("quantity")), "pack_uom": r.get("uom"),
                         "base_qty": _r(U.base_qty(r.get("quantity"), u["factor"])) if u else None,
                         "base_uom": (u or {}).get("base_uom"),
                         "expected_base": None, "variance_pct": None, "high_priority": False,
                         "lot": None, "issued_by": None, "remark": r.get("remarks")})
        excel = _distinct(r.get("remarks") for r in g["rows"])
        out.append({
            "key": f"q{site}|{day}|{tag}|{kind}", "group_id": None, "kind": kind,
            "status": "not_filed", "edited_in_excel": False, "site_id": site,
            "work_date": day, "tag": tag,
            "code": SL.hint_system_code(excel[0]) if excel else None, "work_area": None,
            "surface_state": None, "sqm": None, "sqm_from_remark": _r(G.sqm_hint(excel)),
            "sqm_differs": False, "original_sqm": None, "hod_justification": None,
            "excel_remarks": excel, "job_note": None, "submitted_by": None,
            "submitted_at": None, "decided_by": None, "decided_at": None,
            "rejected_reason": None, "high_priority": False,
            "materials": mats, "drawn": _sums(mats)})
    return out


_ORDER = {"rejected": 0, "pending": 1, "not_filed": 2, "approved": 3}


async def history(session: AsyncSession, *, site_id: Optional[str],
                  dfrom: Optional[str] = None, dto: Optional[str] = None,
                  status: Optional[str] = None, kind: Optional[str] = None,
                  tag: Optional[str] = None, code: Optional[str] = None) -> dict:
    """DATE → JOB → MATERIALS for the window, plus the window's KPIs."""
    from . import prep as PR
    from . import units as U
    a, b = window(dfrom, dto)
    prep = await PR.prep_codes(session)
    umap = await U.unit_map(session)            # every Surface Shield SAP, one query
    jobs = (await _filed_jobs(session, site_id=site_id, dfrom=a, dto=b, prep=prep, umap=umap)
            + await _unfiled_jobs(session, site_id=site_id, dfrom=a, dto=b,
                                  prep_saps=set(await PR.garnet_saps(session)), umap=umap))

    def keep(j) -> bool:
        if status == "edited":
            if not j["edited_in_excel"]:
                return False
        elif status and j["status"] != status:
            return False
        if kind and j["kind"] != kind:
            return False
        if tag and str(j["tag"] or "").strip().lower() != tag.strip().lower():
            return False
        if code and str(j["code"] or "").strip().lower() != code.strip().lower():
            return False
        return True
    jobs = [j for j in jobs if keep(j)]

    def lining_sqm(js, st) -> float:
        return round(sum(float(j["sqm"] or 0) for j in js
                         if j["kind"] == "lining" and j["status"] == st), 3)

    days: dict[str, list] = defaultdict(list)
    for j in jobs:
        days[j["work_date"]].append(j)
    out_days = []
    for d in sorted(days, reverse=True):
        js = sorted(days[d], key=lambda j: (j["kind"] == "prep", _ORDER[j["status"]],
                                            str(j["tag"] or "")))
        all_mats = [m for j in js for m in j["materials"]]
        out_days.append({
            "date": d, "jobs": js, "job_count": len(js),
            "sqm_approved": lining_sqm(js, "approved"),
            "sqm_pending": lining_sqm(js, "pending"),
            "drawn": _sums(all_mats)})
    all_mats = [m for j in jobs for m in j["materials"]]
    kpis = {
        "sqm_approved": lining_sqm(jobs, "approved"),
        "sqm_pending": lining_sqm(jobs, "pending"),
        "sqm_rejected": lining_sqm(jobs, "rejected"),
        "garnet_sqm_approved": round(sum(float(j["sqm"] or 0) for j in jobs
                                         if j["kind"] == "prep" and j["status"] == "approved"), 3),
        "jobs": {s: sum(1 for j in jobs if j["status"] == s) for s in STATUSES},
        "edited_in_excel": sum(1 for j in jobs if j["edited_in_excel"]),
        "high_priority_pending": sum(1 for j in jobs
                                     if j["status"] == "pending" and j["high_priority"]),
        "remark_differs": sum(1 for j in jobs if j["sqm_differs"]),
        "drawn": _sums(all_mats)}
    return {"date_from": a, "date_to": b, "site_id": site_id, "kpis": kpis,
            "days": out_days}


# ── a flat table, for Excel / PDF / the weekly Executive Summary ─────────────
EXPORT_COLUMNS = ["Date", "Section", "Equipment", "System", "Area", "Status",
                  "SQM", "SQM in remark", "Excel remark", "Job note", "Drawn (packs)",
                  "Drawn (base)", "Decided by", "Decided at", "Reason / justification"]

_LABEL = {"approved": "Approved", "pending": "Pending HOD", "rejected": "Rejected",
          "not_filed": "Not yet filed"}


def _fmt_units(d: dict) -> str:
    return ", ".join(f"{v:g} {k}" for k, v in sorted(d.items()))


def export_rows(h: dict) -> list[list]:
    rows = []
    for day in h["days"]:
        for j in day["jobs"]:
            st = _LABEL[j["status"]] + (" (edited in Excel)" if j["edited_in_excel"] else "")
            rows.append([
                j["work_date"], "Garnet (surface prep)" if j["kind"] == "prep" else "Lining",
                j["tag"] or "", j["code"] or "", j["work_area"] or "", st,
                "" if j["sqm"] is None else j["sqm"],
                "" if j["sqm_from_remark"] is None else j["sqm_from_remark"],
                " | ".join(j["excel_remarks"]), j["job_note"] or "",
                _fmt_units(j["drawn"]["packs"]), _fmt_units(j["drawn"]["base"]),
                j["decided_by"] or "", (j["decided_at"] or "")[:16].replace("T", " "),
                j["rejected_reason"] or j["hod_justification"] or ""])
    return rows
