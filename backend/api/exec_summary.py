"""
backend/api/exec_summary.py — HOD Executive Summary report (read-only).

One JSON endpoint assembles the whole daily/period picture for a site —
ledger activity (receipts / consumption / returns), SQM done + manpower from
the Man-Hours module, PR/PO pipeline status, the warehouse delivery plan,
actions taken vs pending, achievable-SQM capacity from the SME engine
(READ-ONLY — the frozen estimator data and both engines are untouched), and
cross-site enquiries. Sibling endpoints render the same payload as a styled
multi-sheet Excel workbook (reports.to_xlsx_sheets) and as a server-rendered
paginated PDF (exec_pdf.render_exec_pdf — measured tables, nothing cut at
the page edges).

Scope: require_roles("hod") — hod + admin, the same lock as Man-Hours/SME.
HODs are pinned to their site; admins may pass ?site_id= or omit it for all
sites. Date range [date_from, date_to] inclusive, default = today; trend
compares against the preceding same-length window plus a 7-day daily average.
"""
from __future__ import annotations

import datetime as _dt
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text as sqt
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, site_scope
from .db import get_session
from . import sme_engine
from .sme import _snapshot_rows

router = APIRouter(prefix="/hod", tags=["executive summary"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Terminal states (kept in sync with LogisticsPage._PO_TERMINAL / DN machine).
_PO_TERMINAL = ("delivered", "closed", "force_closed", "cancelled")
_DN_TERMINAL = ("draft", "rejected", "received", "closed", "cancelled")


def _valid_date(s: str, name: str) -> str:
    if not _DATE_RE.match(s or ""):
        raise HTTPException(422, f"{name} must be YYYY-MM-DD")
    return s


def _site_frag(site: str | None, col: str = '"Site_ID"') -> str:
    return f"AND COALESCE({col},'HQ') = :site" if site else ""


async def _rows(session: AsyncSession, sql: str, **params) -> list[dict]:
    return [dict(m) for m in (await session.execute(sqt(sql), params)).mappings().all()]


async def _one(session: AsyncSession, sql: str, **params):
    return (await session.execute(sqt(sql), params)).scalar_one()


def _pct_delta(cur: float, prev: float) -> float | None:
    if prev in (0, None):
        return None
    return round((cur - prev) / prev * 100.0, 1)


async def _ledger_kpi(session, table: str, site, dfrom, dto, pfrom, pto, afrom, ato) -> dict:
    """count/qty/value for the period + previous-window qty + 7-day daily avg."""
    sf = _site_frag(site)
    base = (f'FROM {table} WHERE substring("Date" FROM 1 FOR 10) '
            f'BETWEEN :dfrom AND :dto {sf}')
    cur = (await session.execute(sqt(
        f'SELECT COUNT(*) AS n, COALESCE(SUM("Quantity"),0) AS qty {base}'),
        dict(dfrom=dfrom, dto=dto, **({"site": site} if site else {})))).mappings().one()
    prev_qty = await _one(session,
        f'SELECT COALESCE(SUM("Quantity"),0) FROM {table} '
        f'WHERE substring("Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {sf}',
        dfrom=pfrom, dto=pto, **({"site": site} if site else {}))
    avg7 = await _one(session,
        f'SELECT COALESCE(SUM("Quantity"),0) / 7.0 FROM {table} '
        f'WHERE substring("Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {sf}',
        dfrom=afrom, dto=ato, **({"site": site} if site else {}))
    return {"count": int(cur["n"]), "qty": round(float(cur["qty"]), 3),
            "prev_qty": round(float(prev_qty), 3),
            "delta_pct": _pct_delta(float(cur["qty"]), float(prev_qty)),
            "daily_avg_7d": round(float(avg7), 3)}


def _capacity_from_lines(model: dict, lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """Achievable SQM under the current pool, strict-bottleneck per unit:
    the LEAST-covered material's rate caps the whole (tag, code) unit."""
    by_unit: dict[tuple[str, str], list[dict]] = {}
    for ln in lines:
        by_unit.setdefault((ln["Equipment_Tag_No"], ln["Lining_System_Code"]), []).append(ln)

    per_equipment: dict[str, dict] = {}
    per_system: dict[str, dict] = {}
    for (tag, code), rows in by_unit.items():
        unit = model["units"][(tag, code)]
        remaining = float(unit["remaining"])
        min_rate, bottleneck = 1.0, None
        for r in rows:
            rate = min(max(r["Allocated_Qty"] / r["Demand_Qty"], 0.0), 1.0) \
                if r["Demand_Qty"] > 0 else 1.0
            if rate < min_rate:
                min_rate, bottleneck = rate, r
        achievable = round(min_rate * remaining, 2)
        e = per_equipment.setdefault(tag, {
            "Equipment_Tag": tag,
            "Name": model["tag_meta"].get(tag, {}).get("Name", ""),
            "Remaining_SQM": 0.0, "Achievable_SQM": 0.0, "Bottleneck": ""})
        e["Remaining_SQM"] += remaining
        e["Achievable_SQM"] += achievable
        if bottleneck is not None and bottleneck["Shortfall_Qty"] > 0 and not e["Bottleneck"]:
            e["Bottleneck"] = f'{bottleneck["Material_Code"]} ({bottleneck["Material_Name"]})'
        s = per_system.setdefault(code, {
            "System_Code": code, "System_Name": unit.get("short_name", ""),
            "Remaining_SQM": 0.0, "Achievable_SQM": 0.0})
        s["Remaining_SQM"] += remaining
        s["Achievable_SQM"] += achievable

    def _fin(d: dict) -> dict:
        rem, ach = round(d["Remaining_SQM"], 2), round(d["Achievable_SQM"], 2)
        return {**d, "Remaining_SQM": rem, "Achievable_SQM": ach,
                "Coverage_Pct": round(ach / rem * 100.0, 1) if rem > 0 else 100.0}

    eq = sorted((_fin(v) for v in per_equipment.values()),
                key=lambda r: r["Equipment_Tag"])
    sy = sorted((_fin(v) for v in per_system.values()),
                key=lambda r: sme_engine.syscode_sort_key(r["System_Code"]))
    return eq, sy


async def _build_summary(session: AsyncSession, *, site: str | None,
                         dfrom: str, dto: str, detail_limit: int = 200) -> dict:
    d0 = _dt.date.fromisoformat(dfrom)
    d1 = _dt.date.fromisoformat(dto)
    if d1 < d0:
        raise HTTPException(422, "date_to must be on or after date_from")
    span = (d1 - d0).days + 1
    pfrom, pto = (d0 - _dt.timedelta(days=span)).isoformat(), (d0 - _dt.timedelta(days=1)).isoformat()
    afrom, ato = (d0 - _dt.timedelta(days=7)).isoformat(), (d0 - _dt.timedelta(days=1)).isoformat()
    sp = {"site": site} if site else {}
    P = dict(dfrom=dfrom, dto=dto, **sp)
    sf = _site_frag(site)

    # ── ledger KPIs + detail ─────────────────────────────────────────────────
    kpis = {t: await _ledger_kpi(session, t, site, dfrom, dto, pfrom, pto, afrom, ato)
            for t in ("receipts", "consumption", "returns")}
    receipts_detail = await _rows(session, f'''
        SELECT r."Date", TRIM(r."SAP_Code") AS "SAP_Code", i."Equipment_Description",
               r."Quantity", i."UOM", r."Supplier", r."Lot_Number", r."PR_Number",
               COALESCE(r."Site_ID",'HQ') AS "Site_ID"
        FROM receipts r LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(r."SAP_Code")
        WHERE substring(r."Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {_site_frag(site, 'r."Site_ID"')}
        ORDER BY r."Date" DESC, r.id DESC LIMIT {detail_limit}''', **P)
    consumption_detail = await _rows(session, f'''
        SELECT c."Date", TRIM(c."SAP_Code") AS "SAP_Code", i."Equipment_Description",
               c."Quantity", i."UOM", c."Issued_To", c."Work_Type", c."WBS",
               c."Lot_Number", COALESCE(c."Site_ID",'HQ') AS "Site_ID"
        FROM consumption c LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
        WHERE substring(c."Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {_site_frag(site, 'c."Site_ID"')}
        ORDER BY c."Date" DESC, c.id DESC LIMIT {detail_limit}''', **P)
    returns_detail = await _rows(session, f'''
        SELECT r."Date", TRIM(r."SAP_Code") AS "SAP_Code", i."Equipment_Description",
               r."Quantity", i."UOM", COALESCE(r."Site_ID",'HQ') AS "Site_ID"
        FROM returns r LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(r."SAP_Code")
        WHERE substring(r."Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {_site_frag(site, 'r."Site_ID"')}
        ORDER BY r."Date" DESC, r.id DESC LIMIT {detail_limit}''', **P)

    # ── SQM done + manpower (Man-Hours module, read-only) ────────────────────
    sqm_detail = await _rows(session, f'''
        SELECT "Work_Date", "Equipment_Tag", "System_Code", SUM("SQM_Done") AS "SQM_Done"
        FROM mh_production WHERE "Work_Date" BETWEEN :dfrom AND :dto {sf}
        GROUP BY "Work_Date", "Equipment_Tag", "System_Code"
        ORDER BY "Work_Date" DESC, "Equipment_Tag" LIMIT {detail_limit}''', **P)
    sqm_total = float(await _one(session,
        f'SELECT COALESCE(SUM("SQM_Done"),0) FROM mh_production '
        f'WHERE "Work_Date" BETWEEN :dfrom AND :dto {sf}', **P))
    sqm_prev = float(await _one(session,
        f'SELECT COALESCE(SUM("SQM_Done"),0) FROM mh_production '
        f'WHERE "Work_Date" BETWEEN :dfrom AND :dto {sf}',
        dfrom=pfrom, dto=pto, **sp))
    mh_total = float(await _one(session,
        f'SELECT COALESCE(SUM("Total_Hours"),0) FROM mh_timesheets '
        f'WHERE "Work_Date" BETWEEN :dfrom AND :dto {sf}', **P))
    mh_prev = float(await _one(session,
        f'SELECT COALESCE(SUM("Total_Hours"),0) FROM mh_timesheets '
        f'WHERE "Work_Date" BETWEEN :dfrom AND :dto {sf}',
        dfrom=pfrom, dto=pto, **sp))
    present = await _rows(session, f'''
        SELECT t."Employee_Code", COALESCE(e."Name", t."Employee_Code") AS "Name",
               e."Designation", e."Worker_Type",
               ROUND(SUM(t."Total_Hours")::numeric, 2) AS "Hours",
               ROUND(SUM(t."OT_Hours")::numeric, 2) AS "OT_Hours",
               ROUND(SUM(t."Allocated_SQM")::numeric, 2) AS "Allocated_SQM"
        FROM mh_timesheets t LEFT JOIN mh_employees e
             ON e."Employee_Code" = t."Employee_Code"
             AND COALESCE(e."Site_ID",'HQ') = COALESCE(t."Site_ID",'HQ')
        WHERE t."Work_Date" BETWEEN :dfrom AND :dto {_site_frag(site, 't."Site_ID"')}
        GROUP BY t."Employee_Code", e."Name", e."Designation", e."Worker_Type"
        ORDER BY "Hours" DESC LIMIT {detail_limit}''', **P)
    absent = await _rows(session, f'''
        SELECT e."Employee_Code", e."Name", e."Designation", e."Worker_Type"
        FROM mh_employees e
        WHERE e.status = 'active' {_site_frag(site, 'e."Site_ID"')}
          AND NOT EXISTS (SELECT 1 FROM mh_timesheets t
                          WHERE t."Employee_Code" = e."Employee_Code"
                            AND COALESCE(t."Site_ID",'HQ') = COALESCE(e."Site_ID",'HQ')
                            AND t."Work_Date" BETWEEN :dfrom AND :dto)
        ORDER BY e."Name" LIMIT {detail_limit}''', **P)
    active_total = int(await _one(session,
        f"SELECT COUNT(*) FROM mh_employees e WHERE e.status = 'active' "
        f"{_site_frag(site, 'e.\"Site_ID\"')}", **sp))

    # ── PR / PO pipeline ─────────────────────────────────────────────────────
    pr_open_by_state = await _rows(session, f'''
        SELECT workflow_state AS state, COUNT(DISTINCT "PR_Number") AS n
        FROM pr_master WHERE 1=1 {sf} GROUP BY workflow_state ORDER BY workflow_state''', **sp)
    pr_raised = int(await _one(session, f'''
        SELECT COUNT(DISTINCT "PR_Number") FROM pr_master
        WHERE created_at::date BETWEEN :d0 AND :d1 {sf}''', d0=d0, d1=d1, **sp))
    po_by_status = await _rows(session, f'''
        SELECT status, COUNT(*) AS n FROM purchase_orders
        WHERE 1=1 {sf} GROUP BY status ORDER BY status''', **sp)
    po_terminal = "', '".join(_PO_TERMINAL)
    po_open = int(await _one(session, f'''
        SELECT COUNT(*) FROM purchase_orders
        WHERE COALESCE(status,'open') NOT IN ('{po_terminal}') {sf}''', **sp))
    po_overdue = int(await _one(session, f'''
        SELECT COUNT(*) FROM purchase_orders
        WHERE COALESCE(status,'open') NOT IN ('{po_terminal}') {sf}
          AND COALESCE("Expected_Delivery",'') <> ''
          AND substring("Expected_Delivery" FROM 1 FOR 10) < :dto''', **P))

    # ── delivery plan from the warehouse ─────────────────────────────────────
    dn_terminal = "', '".join(_DN_TERMINAL)
    plan_to = (d1 + _dt.timedelta(days=7)).isoformat()
    dn_in_flight = await _rows(session, f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", status,
               "DN_Date", "Vehicle_No", "Driver_Name"
        FROM delivery_notes WHERE status NOT IN ('{dn_terminal}') {sf}
        ORDER BY id DESC LIMIT {detail_limit}''', **sp)
    upcoming = await _rows(session, f'''
        SELECT a."PO_Number", a."Warehouse_ID", a."Expected_Delivery", a.status,
               p."Site_ID", p."Vendor_Name"
        FROM po_assignments a LEFT JOIN purchase_orders p ON p."PO_Number" = a."PO_Number"
        WHERE COALESCE(a.status,'assigned') NOT IN ('completed','cancelled')
          AND COALESCE(a."Expected_Delivery",'') <> ''
          AND substring(a."Expected_Delivery" FROM 1 FOR 10) BETWEEN :dfrom AND :plan_to
          {_site_frag(site, 'p."Site_ID"')}
        ORDER BY a."Expected_Delivery" LIMIT {detail_limit}''',
        dfrom=dfrom, plan_to=plan_to, **sp)

    # ── actions taken vs pending ─────────────────────────────────────────────
    rejected_n = int(await _one(session, f'''
        SELECT COUNT(*) FROM rejected_issues_archive
        WHERE substring("Date" FROM 1 FOR 10) BETWEEN :dfrom AND :dto {sf}''', **P))
    dn_decided = int(await _one(session, f'''
        SELECT COUNT(*) FROM delivery_notes
        WHERE ((logistics_decided_at::date BETWEEN :d0 AND :d1)
            OR (hod_decided_at::date BETWEEN :d0 AND :d1)) {sf}''', d0=d0, d1=d1, **sp))
    pend_issues = int(await _one(session,
        f"SELECT COUNT(*) FROM pending_issues WHERE status <> 'draft' {sf}", **sp))
    dn_pending = await _rows(session, f'''
        SELECT status, COUNT(*) AS n FROM delivery_notes
        WHERE status IN ('pending_logistics','pending_hod') {sf}
        GROUP BY status''', **sp)
    smr_pending = int(await _one(session, f'''
        SELECT COUNT(*) FROM supervisor_material_requests
        WHERE status LIKE 'pending%' {sf}''', **sp))
    pr_draft = int(await _one(session, f'''
        SELECT COUNT(DISTINCT "PR_Number") FROM pr_master
        WHERE workflow_state IN ('draft','site_draft') {sf}''', **sp))

    # ── SME capacity (READ-ONLY engine run over current availability) ────────
    try:
        snap = await _snapshot_rows(session, site)
        model = sme_engine.build_model(snap["equipment"], snap["recipes"],
                                       snap["materials"], snap["progress"])
        lines = sme_engine.cascade_allocate(model, model["default_order"])
        cap_equipment, cap_system = _capacity_from_lines(model, lines)
    except Exception:  # SME data may be absent for a brand-new site
        cap_equipment, cap_system = [], []

    # ── cross-site enquiries touching this site ──────────────────────────────
    xsite_frag = ("AND (COALESCE(requesting_site,'') = :site "
                  "OR COALESCE(target_site,'') = :site)") if site else ""
    cross_site = await _rows(session, f'''
        SELECT id, requesting_site, target_site, "SAP_Code", requested_qty,
               available_qty, status, requested_by, created_at::date AS created
        FROM requests
        WHERE (status = 'pending' OR updated_at::date BETWEEN :d0 AND :d1)
        {xsite_frag} ORDER BY id DESC LIMIT {detail_limit}''', d0=d0, d1=d1, **sp)

    return {
        "site_id": site, "date_from": dfrom, "date_to": dto, "days": span,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kpis": {
            **kpis,
            "sqm_done": {"total": round(sqm_total, 2), "prev": round(sqm_prev, 2),
                         "delta_pct": _pct_delta(sqm_total, sqm_prev)},
            "man_hours": {"total": round(mh_total, 2), "prev": round(mh_prev, 2),
                          "delta_pct": _pct_delta(mh_total, mh_prev)},
            "manpower": {"present": len(present), "absent": len(absent),
                         "active_total": active_total},
        },
        "receipts_detail": receipts_detail,
        "consumption_detail": consumption_detail,
        "returns_detail": returns_detail,
        "sqm_detail": sqm_detail,
        "manpower": {"present": present, "absent": absent},
        "pr_status": {"raised_in_period": pr_raised, "open_by_state": pr_open_by_state},
        "po_status": {"by_status": po_by_status, "open": po_open, "overdue": po_overdue},
        "delivery_plan": {"dn_in_flight": dn_in_flight, "upcoming_deliveries": upcoming,
                          "window_to": plan_to},
        "actions": {
            "taken": {"receipts_posted": kpis["receipts"]["count"],
                      "issues_approved": kpis["consumption"]["count"],
                      "returns_processed": kpis["returns"]["count"],
                      "entries_rejected": rejected_n,
                      "dn_decisions": dn_decided},
            "pending": {"entry_approvals": pend_issues,
                        "dn_queue": {r["status"]: r["n"] for r in dn_pending},
                        "smr_pending": smr_pending,
                        "draft_prs": pr_draft},
        },
        "sqm_capacity": {"per_equipment": cap_equipment, "per_system": cap_system},
        "cross_site": cross_site,
    }


def _resolve_range(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    today = _dt.date.today().isoformat()
    dfrom = _valid_date(date_from or today, "date_from")
    dto = _valid_date(date_to or dfrom, "date_to")
    return dfrom, dto


def _resolve_site(user: dict, site_id: str | None) -> str | None:
    scope = site_scope(user)
    if scope is not None:  # scoped HOD — pinned; '' would match nothing (fail-closed)
        if not scope:
            raise HTTPException(403, "your account has no site assigned")
        return scope
    return (site_id or "").strip() or None  # admin: chosen site, or all sites


@router.get("/executive-summary", summary="Executive summary (JSON)")
async def executive_summary(date_from: str | None = Query(None),
                            date_to: str | None = Query(None),
                            site_id: str | None = Query(None),
                            user: dict = Depends(require_roles("hod")),
                            session: AsyncSession = Depends(get_session)):
    dfrom, dto = _resolve_range(date_from, date_to)
    site = _resolve_site(user, site_id)
    return await _build_summary(session, site=site, dfrom=dfrom, dto=dto)


@router.get("/executive-summary/export.xlsx", summary="Executive summary (Excel)")
async def executive_summary_xlsx(date_from: str | None = Query(None),
                                 date_to: str | None = Query(None),
                                 site_id: str | None = Query(None),
                                 user: dict = Depends(require_roles("hod")),
                                 session: AsyncSession = Depends(get_session)):
    from .reports import to_xlsx_sheets
    dfrom, dto = _resolve_range(date_from, date_to)
    site = _resolve_site(user, site_id)
    d = await _build_summary(session, site=site, dfrom=dfrom, dto=dto, detail_limit=2000)

    def tab(rows: list[dict], cols: list[str]):
        return cols, [[r.get(c) for c in cols] for r in rows]

    k = d["kpis"]
    overview = [
        ["Site", site or "ALL SITES"], ["Period", f"{dfrom} → {dto} ({d['days']} day(s))"],
        ["Generated", d["generated_at"]], ["", ""],
        ["Receipts (entries / qty)", f'{k["receipts"]["count"]} / {k["receipts"]["qty"]:g}'],
        ["Consumption (entries / qty)", f'{k["consumption"]["count"]} / {k["consumption"]["qty"]:g}'],
        ["Returns (entries / qty)", f'{k["returns"]["count"]} / {k["returns"]["qty"]:g}'],
        ["SQM done", f'{k["sqm_done"]["total"]:g}'],
        ["Man-hours", f'{k["man_hours"]["total"]:g}'],
        ["Manpower present / absent / active", f'{k["manpower"]["present"]} / {k["manpower"]["absent"]} / {k["manpower"]["active_total"]}'],
        ["PRs raised in period", d["pr_status"]["raised_in_period"]],
        ["Open POs (overdue)", f'{d["po_status"]["open"]} ({d["po_status"]["overdue"]})'],
        ["Pending entry approvals", d["actions"]["pending"]["entry_approvals"]],
        ["Pending SMRs / draft PRs", f'{d["actions"]["pending"]["smr_pending"]} / {d["actions"]["pending"]["draft_prs"]}'],
    ]
    sheets = [
        ("Overview", ["Metric", "Value"], overview),
        ("Receipts", *tab(d["receipts_detail"],
         ["Date", "SAP_Code", "Equipment_Description", "Quantity", "UOM", "Supplier", "Lot_Number", "PR_Number", "Site_ID"])),
        ("Consumption", *tab(d["consumption_detail"],
         ["Date", "SAP_Code", "Equipment_Description", "Quantity", "UOM", "Issued_To", "Work_Type", "WBS", "Lot_Number", "Site_ID"])),
        ("Returns", *tab(d["returns_detail"],
         ["Date", "SAP_Code", "Equipment_Description", "Quantity", "UOM", "Site_ID"])),
        ("SQM Done", *tab(d["sqm_detail"],
         ["Work_Date", "Equipment_Tag", "System_Code", "SQM_Done"])),
        ("Manpower Present", *tab(d["manpower"]["present"],
         ["Employee_Code", "Name", "Designation", "Worker_Type", "Hours", "OT_Hours", "Allocated_SQM"])),
        ("Manpower Absent", *tab(d["manpower"]["absent"],
         ["Employee_Code", "Name", "Designation", "Worker_Type"])),
        ("PR Status", ["State", "PRs"],
         [[r["state"], r["n"]] for r in d["pr_status"]["open_by_state"]]),
        ("PO Status", ["Status", "POs"],
         [[r["status"], r["n"]] for r in d["po_status"]["by_status"]]),
        ("Delivery Plan (DNs)", *tab(d["delivery_plan"]["dn_in_flight"],
         ["DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", "status", "DN_Date", "Vehicle_No", "Driver_Name"])),
        ("Upcoming Deliveries", *tab(d["delivery_plan"]["upcoming_deliveries"],
         ["PO_Number", "Warehouse_ID", "Expected_Delivery", "status", "Site_ID", "Vendor_Name"])),
        ("Capacity per Equipment", *tab(d["sqm_capacity"]["per_equipment"],
         ["Equipment_Tag", "Name", "Remaining_SQM", "Achievable_SQM", "Coverage_Pct", "Bottleneck"])),
        ("Capacity per System", *tab(d["sqm_capacity"]["per_system"],
         ["System_Code", "System_Name", "Remaining_SQM", "Achievable_SQM", "Coverage_Pct"])),
        ("Cross-Site", *tab(d["cross_site"],
         ["id", "requesting_site", "target_site", "SAP_Code", "requested_qty", "available_qty", "status", "requested_by", "created"])),
    ]
    blob = to_xlsx_sheets(sheets, user["username"])
    fname = f"executive_summary_{(site or 'ALL')}_{dfrom}_{dto}.xlsx"
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/executive-summary/export.pdf", summary="Executive summary (PDF)")
async def executive_summary_pdf(date_from: str | None = Query(None),
                                date_to: str | None = Query(None),
                                site_id: str | None = Query(None),
                                user: dict = Depends(require_roles("hod")),
                                session: AsyncSession = Depends(get_session)):
    from .exec_pdf import render_exec_pdf
    dfrom, dto = _resolve_range(date_from, date_to)
    site = _resolve_site(user, site_id)
    d = await _build_summary(session, site=site, dfrom=dfrom, dto=dto, detail_limit=500)
    blob = render_exec_pdf(d, site=site, username=user["username"])
    fname = f"executive_summary_{(site or 'ALL')}_{dfrom}_{dto}.pdf"
    return StreamingResponse(
        io.BytesIO(blob), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
