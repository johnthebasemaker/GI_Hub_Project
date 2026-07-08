"""
backend/api/services/procurement.py — the PR → PO → warehouse chain.

Ports the Logistics-side procurement logic from database.py:
  * submit_pr        — submit_pr_to_logistics()  (HOD flips a PR to 'submitted')
  * pr_queue         — list_prs_for_logistics()   (the Logistics queue)
  * create_po_from_pr— create_po_manual()         (header + po_items + flip PR 'in_po')
  * assign_po        — assign_po_to_warehouse()

RL/BL family separation is preserved: each po_item is tagged via
classify_rl_bl_family (RL and BL must never share a PO group).
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit  # reuse metadata + audit writer
from .notifications import notify

pr_master_t = _MD.tables["pr_master"]
purchase_orders_t = _MD.tables["purchase_orders"]
po_items_t = _MD.tables["po_items"]
po_assignments_t = _MD.tables["po_assignments"]
warehouses_t = _MD.tables["warehouses"]
inventory_t = _MD.tables["inventory"]
po_reschedule_t = _MD.tables["po_reschedule_requests"]
po_force_closures_t = _MD.tables["po_force_closures"]
vendors_t = _MD.tables["vendors"]

# RL/BL family tokens — verbatim from config.py (RL_BL_FAMILY_TOKENS).
_RL_BL_TOKENS = {
    "RL": ("RL-", "RUBBER LINING", "RUBBER-LINING"),
    "BL": ("BL-", "BRICK LINING", "BRICK-LINING", "BRICK MATERIAL"),
}


def classify_rl_bl_family(material_code: str | None, description: str | None) -> str | None:
    blob = f"{material_code or ''} {description or ''}".upper()
    for family, tokens in _RL_BL_TOKENS.items():
        if any(tok in blob for tok in tokens):
            return family
    return None


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


async def _next_pr_number(session: AsyncSession) -> str:
    """Auto-generate a site PR number: PR-YYYYMMDD-NNNN (sequence resets daily).

    Mirrors the SMR numbering in services/supervisor.py — take the newest row
    with today's prefix and increment its suffix.
    """
    today = _dt.date.today().strftime("%Y%m%d")
    prefix = f"PR-{today}-"
    last = (await session.execute(select(pr_master_t.c["PR_Number"]).where(
        pr_master_t.c["PR_Number"].like(prefix + "%")
    ).order_by(pr_master_t.c["id"].desc()).limit(1))).scalar_one_or_none()
    nxt = 1
    if last:
        try:
            nxt = int(str(last).split("-")[-1]) + 1
        except (ValueError, IndexError):
            nxt = 1
    return f"{prefix}{nxt:04d}"


# --- reads -------------------------------------------------------------------
async def hod_prs(session: AsyncSession, site_id: str | None):
    """Site PRs grouped by PR_Number — the HOD's own queue (to submit)."""
    where = '"status" = \'open\''
    params: dict = {}
    if site_id:
        where += " AND COALESCE(\"Site_ID\",'HQ') = :site"
        params["site"] = site_id
    sql = text(f'''
        SELECT "PR_Number", COALESCE("Site_ID",'HQ') AS "Site_ID",
               COUNT(*) AS line_count, SUM("Requested_Qty") AS total_qty,
               MAX(COALESCE(logistics_status,'site_draft')) AS logistics_status
        FROM pr_master WHERE {where}
        GROUP BY "PR_Number", COALESCE("Site_ID",'HQ')
        ORDER BY "PR_Number" DESC''')
    return _rows(await session.execute(sql, params))


async def pr_queue(session: AsyncSession, site_id: str | None):
    """The Logistics queue — PRs submitted and still open."""
    where = ("COALESCE(logistics_status,'site_draft') = 'submitted' "
             "AND \"status\" = 'open'")
    params: dict = {}
    if site_id:
        where += " AND COALESCE(\"Site_ID\",'HQ') = :site"
        params["site"] = site_id
    sql = text(f'''
        SELECT "PR_Number", COALESCE("Site_ID",'HQ') AS "Site_ID",
               COUNT(*) AS line_count, SUM("Requested_Qty") AS total_qty,
               MIN(submitted_to_logistics_at) AS submitted_at
        FROM pr_master WHERE {where}
        GROUP BY "PR_Number", COALESCE("Site_ID",'HQ')
        ORDER BY submitted_at DESC''')
    return _rows(await session.execute(sql, params))


async def pr_lines(session: AsyncSession, pr_number: str, site_id: str | None):
    stmt = select(
        pr_master_t.c["id"], pr_master_t.c["PR_Number"], pr_master_t.c["Site_ID"],
        pr_master_t.c["SAP_Code"], pr_master_t.c["Material_Code"], pr_master_t.c["Material_Name"],
        pr_master_t.c["Requested_Qty"], pr_master_t.c["UOM"], pr_master_t.c["Est_Cost_SAR"],
        pr_master_t.c["logistics_status"],
    ).where(pr_master_t.c["PR_Number"] == pr_number)
    if site_id:
        stmt = stmt.where(func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
    return _rows(await session.execute(stmt.order_by(pr_master_t.c["id"])))


async def po_list(session: AsyncSession, status: str | None):
    where, params = "", {}
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    sql = text(f'''
        SELECT "PO_Number", "PR_Number", "Site_ID", "Vendor_Name", "PO_Date",
               "Expected_Delivery", status, created_by, created_at
        FROM purchase_orders {where}
        ORDER BY "PO_Number" DESC LIMIT 500''')
    return _rows(await session.execute(sql, params))


async def po_items(session: AsyncSession, po_number: str):
    stmt = select(
        po_items_t.c["id"], po_items_t.c["line_no"], po_items_t.c["Material_Code"],
        po_items_t.c["Description"], po_items_t.c["Qty"], po_items_t.c["UOM"],
        po_items_t.c["Unit_Price"], po_items_t.c["Total_Price"], po_items_t.c["PR_Number"],
        po_items_t.c["rl_bl_family"], po_items_t.c["line_status"],
    ).where(po_items_t.c["PO_Number"] == po_number).order_by(po_items_t.c["line_no"])
    return _rows(await session.execute(stmt))


# --- mutations ---------------------------------------------------------------
async def create_pr(session: AsyncSession, *, username: str, site_id: str,
                    lines: list[dict], supplier: str | None = None,
                    notes: str | None = None, delivery_date: str | None = None) -> dict:
    """Create one site PR (draft) from a set of lines — ports insert_manual_pr().

    Each line is validated + enriched against the ERP inventory master (SAP_Code
    must exist; Material_Code / Material_Name / UOM are backfilled when blank).
    Rows land status='open', workflow_state='draft', logistics_status='site_draft'
    so the HOD's queue lists them for submission to Logistics. Returns the
    auto-generated PR_Number.
    """
    if not (site_id or "").strip():
        return {"error": "site is required"}
    if not lines:
        return {"error": "add at least one line"}

    prepared: list[dict] = []
    for ln in lines:
        sap = str(ln.get("SAP_Code") or "").strip()
        if not sap:
            return {"error": "every line needs a SAP_Code"}
        try:
            qty = float(ln.get("Requested_Qty") or 0)
        except (TypeError, ValueError):
            return {"error": f"line {sap}: qty is not a number"}
        if qty <= 0:
            return {"error": f"line {sap}: qty must be > 0"}
        inv = (await session.execute(select(
            inventory_t.c["Material_Code"], inventory_t.c["Equipment_Description"],
            inventory_t.c["UOM"],
        ).where(func.trim(inventory_t.c["SAP_Code"]) == sap).limit(1))).first()
        if inv is None:
            return {"error": f"SAP {sap} not in inventory master"}
        try:
            est = float(ln.get("Est_Cost_SAR") or 0)
        except (TypeError, ValueError):
            est = 0.0
        prepared.append({
            "SAP_Code": sap,
            "Material_Code": (str(ln.get("Material_Code") or "").strip() or (inv[0] or "")),
            "Material_Name": (str(ln.get("Material_Name") or "").strip() or (inv[1] or "")),
            "Requested_Qty": qty,
            "UOM": (str(ln.get("UOM") or "").strip() or (inv[2] or "")),
            "Est_Cost_SAR": est,
            "Notes": (str(ln.get("Notes") or "").strip() or (notes or "")),
        })

    pr_number = await _next_pr_number(session)
    for ln in prepared:
        await session.execute(insert(pr_master_t).values(
            PR_Number=pr_number, Site_ID=site_id, SAP_Code=ln["SAP_Code"],
            Material_Code=ln["Material_Code"], Material_Name=ln["Material_Name"],
            Requested_Qty=ln["Requested_Qty"], UOM=ln["UOM"],
            Est_Cost_SAR=ln["Est_Cost_SAR"], Supplier=(supplier or None),
            Notes=(ln["Notes"] or None), Delivery_Date=(delivery_date or None),
            status="open", workflow_state="draft", logistics_status="site_draft"))

    await write_audit(session, username, "CREATE_PR", "pr_master",
                      f"PR={pr_number} site={site_id} lines={len(prepared)}")
    return {"created": True, "pr_number": pr_number, "site_id": site_id,
            "lines": len(prepared)}


async def submit_pr(session: AsyncSession, *, username: str, pr_number: str, site_id: str) -> dict:
    res = await session.execute(update(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == pr_number)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft").in_(["site_draft", "submitted"]))
    ).values(logistics_status="submitted", submitted_to_logistics_at=func.now(),
             submitted_to_logistics_by=username))
    if res.rowcount == 0:
        return {"error": f"PR {pr_number} has no eligible lines to submit"}
    await write_audit(session, username, "SUBMIT_PR_TO_LOGISTICS", "pr_master",
                      f"PR={pr_number} site={site_id} lines={res.rowcount}")
    await notify(session, event_key="pr_submitted_to_logistics", recipient_role="logistics",
                 title=f"New PR {pr_number} from {site_id}",
                 body=f"{res.rowcount} line(s) awaiting PO issuance.",
                 link_page="/logistics", related_table="pr_master", related_ref=pr_number)
    return {"submitted": True, "pr_number": pr_number, "lines": res.rowcount}


async def create_po_from_pr(session: AsyncSession, *, username: str, pr_number: str,
                            site_id: str, po_number: str, vendor_code: str | None = None,
                            vendor_name: str | None = None,
                            expected_delivery: str | None = None) -> dict:
    lines = (await session.execute(select(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == pr_number)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "submitted")
    ))).mappings().all()
    if not lines:
        return {"error": "no submitted PR lines for this PR/site"}

    exists = (await session.execute(select(func.count()).select_from(purchase_orders_t)
              .where(purchase_orders_t.c["PO_Number"] == po_number))).scalar_one()
    if exists:
        return {"error": f"PO {po_number} already exists"}

    today = _dt.date.today().isoformat()
    await session.execute(insert(purchase_orders_t).values(
        PO_Number=po_number, PR_Number=pr_number, Site_ID=site_id,
        Vendor_Code=vendor_code, Vendor_Name=vendor_name, PO_Date=today,
        Expected_Delivery=expected_delivery, source="api", created_by=username, status="open"))

    for idx, ln in enumerate(lines, start=1):
        mat = (ln.get("Material_Code") or "").strip()
        desc = ln.get("Material_Name") or ""
        qty = float(ln.get("Requested_Qty") or 0)
        unit = float(ln.get("Est_Cost_SAR") or 0)
        await session.execute(insert(po_items_t).values(
            PO_Number=po_number, line_no=idx, Material_Code=mat, Description=desc,
            Qty=qty, UOM=ln.get("UOM"), Unit_Price=unit, Total_Price=round(qty * unit, 2),
            PR_Number=pr_number, WBS_Number=ln.get("WBS_Number"), Network=ln.get("Network"),
            Plant=ln.get("Plant"), rl_bl_family=classify_rl_bl_family(mat, desc), line_status="open"))

    await session.execute(update(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == pr_number)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "submitted")
    ).values(logistics_status="in_po"))

    await write_audit(session, username, "CREATE_PO", "purchase_orders",
                      f"PO={po_number} PR={pr_number} site={site_id} lines={len(lines)}")
    return {"created": True, "po_number": po_number, "lines": len(lines)}


# --- manual PO creation (free-text lines, prices, unlisted PR) ---------------
async def create_po_manual(session: AsyncSession, *, username: str, header: dict,
                           lines: list[dict]) -> dict:
    po_number = str(header.get("po_number") or "").strip()
    if not po_number:
        return {"error": "PO number is required"}
    if not lines:
        return {"error": "add at least one line"}
    exists = (await session.execute(select(func.count()).select_from(purchase_orders_t)
              .where(purchase_orders_t.c["PO_Number"] == po_number))).scalar_one()
    if exists:
        return {"error": f"PO {po_number} already exists"}

    prepared: list[dict] = []
    for i, ln in enumerate(lines, start=1):
        try:
            qty = float(ln.get("Qty") or 0)
        except (TypeError, ValueError):
            return {"error": f"line {i}: qty is not a number"}
        if qty <= 0:
            return {"error": f"line {i}: qty must be > 0"}
        try:
            unit = float(ln.get("Unit_Price") or 0)
        except (TypeError, ValueError):
            unit = 0.0
        mat = str(ln.get("Material_Code") or "").strip()
        desc = str(ln.get("Description") or "").strip()
        if not (mat or desc):
            return {"error": f"line {i}: a material code or description is required"}
        prepared.append({"mat": mat, "desc": desc, "qty": qty, "unit": unit,
                         "uom": ln.get("UOM"), "pr": (str(ln.get("PR_Number") or "").strip() or None),
                         "wbs": ln.get("WBS_Number"), "net": ln.get("Network"), "plant": ln.get("Plant")})

    pr_number = str(header.get("pr_number") or "").strip() or None
    today = _dt.date.today().isoformat()
    total = round(sum(p["qty"] * p["unit"] for p in prepared), 2)
    await session.execute(insert(purchase_orders_t).values(
        PO_Number=po_number, PR_Number=pr_number,
        Site_ID=(header.get("site_id") or None),
        Vendor_Code=(header.get("vendor_code") or None),
        Vendor_Name=(header.get("vendor_name") or None),
        Inco_Terms=(header.get("inco_terms") or None),
        Payment_Terms=(header.get("payment_terms") or None),
        PO_Date=(header.get("po_date") or today),
        Expected_Delivery=(header.get("expected_delivery") or None),
        Total_Amount=total, source="manual", created_by=username, status="open"))
    for idx, p in enumerate(prepared, start=1):
        await session.execute(insert(po_items_t).values(
            PO_Number=po_number, line_no=idx, Material_Code=p["mat"], Description=p["desc"],
            Qty=p["qty"], UOM=p["uom"], Unit_Price=p["unit"],
            Total_Price=round(p["qty"] * p["unit"], 2), PR_Number=(p["pr"] or pr_number),
            WBS_Number=p["wbs"], Network=p["net"], Plant=p["plant"],
            rl_bl_family=classify_rl_bl_family(p["mat"], p["desc"]), line_status="open"))
    # If the referenced PR exists and is submitted, link it (harmless if unlisted).
    if pr_number:
        await session.execute(update(pr_master_t).where(
            (pr_master_t.c["PR_Number"] == pr_number)
            & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "submitted")
        ).values(logistics_status="in_po"))
    await write_audit(session, username, "CREATE_PO_MANUAL", "purchase_orders",
                      f"PO={po_number} lines={len(prepared)} total={total}")
    return {"created": True, "po_number": po_number, "lines": len(prepared), "total": total}


# --- reschedule workflow (H7) ------------------------------------------------
# WH/HOD raise a reschedule request → Logistics decides → approved date is
# pushed onto the PO (and its warehouse assignments). In-app notify only
# (WhatsApp/email stay parked).
async def raise_reschedule(session: AsyncSession, *, username: str, role: str,
                           po_number: str, requested_date: str, reason: str,
                           dn_number: str | None = None) -> dict:
    if not (requested_date or "").strip():
        return {"error": "a requested delivery date is required"}
    if not (reason or "").strip():
        return {"error": "a reason is required"}
    po = (await session.execute(select(
        purchase_orders_t.c["Expected_Delivery"], purchase_orders_t.c["status"]
    ).where(purchase_orders_t.c["PO_Number"] == po_number))).first()
    if po is None:
        return {"error": f"PO {po_number} not found"}
    if po[1] in ("closed", "force_closed", "cancelled"):
        return {"error": f"PO {po_number} is {po[1]} — cannot reschedule"}
    # One open request at a time per PO.
    dup = (await session.execute(select(func.count()).select_from(po_reschedule_t).where(
        (po_reschedule_t.c["PO_Number"] == po_number)
        & (po_reschedule_t.c["status"] == "pending")))).scalar_one()
    if dup:
        return {"error": f"PO {po_number} already has a pending reschedule request"}
    rid = (await session.execute(insert(po_reschedule_t).values(
        PO_Number=po_number, DN_Number=dn_number, current_date=po[0],
        requested_date=requested_date, reason=reason, requested_by_role=role,
        requested_by=username, status="pending"
    ).returning(po_reschedule_t.c["id"]))).scalar_one()
    await write_audit(session, username, "RAISE_RESCHEDULE", "po_reschedule_requests",
                      f"id={rid} PO={po_number} → {requested_date}")
    await notify(session, event_key="reschedule_raised", recipient_role="logistics",
                 title=f"Reschedule requested — PO {po_number}",
                 body=f"{role} {username} requests {requested_date}. Reason: {reason}",
                 link_page="/logistics", related_table="po_reschedule_requests",
                 related_ref=str(rid))
    return {"raised": True, "id": rid, "po_number": po_number}


async def list_reschedules(session: AsyncSession, status: str | None):
    stmt = select(po_reschedule_t).order_by(po_reschedule_t.c["id"].desc()).limit(500)
    if status:
        stmt = stmt.where(po_reschedule_t.c["status"] == status)
    return _rows(await session.execute(stmt))


async def decide_reschedule(session: AsyncSession, *, username: str, req_id: int,
                            action: str, decision_notes: str = "") -> dict:
    if action not in ("approve", "reject"):
        return {"error": "action must be approve or reject"}
    row = (await session.execute(select(po_reschedule_t).where(
        po_reschedule_t.c["id"] == req_id))).mappings().first()
    if row is None:
        return {"error": f"reschedule request {req_id} not found"}
    if row["status"] != "pending":
        return {"error": f"request {req_id} already {row['status']}"}
    new_status = "approved" if action == "approve" else "rejected"
    await session.execute(update(po_reschedule_t).where(po_reschedule_t.c["id"] == req_id).values(
        status=new_status, decided_by=username, decided_at=func.now(),
        decision_notes=decision_notes or None))
    if action == "approve":
        await session.execute(update(purchase_orders_t).where(
            purchase_orders_t.c["PO_Number"] == row["PO_Number"]).values(
            Expected_Delivery=row["requested_date"]))
        await session.execute(update(po_assignments_t).where(
            po_assignments_t.c["PO_Number"] == row["PO_Number"]).values(
            Expected_Delivery=row["requested_date"]))
    await write_audit(session, username, f"RESCHEDULE_{new_status.upper()}",
                      "po_reschedule_requests", f"id={req_id} PO={row['PO_Number']}")
    await notify(session, event_key="reschedule_decided", recipient_user=row["requested_by"],
                 severity=("success" if action == "approve" else "warning"),
                 title=f"Reschedule {new_status} — PO {row['PO_Number']}",
                 body=(f"New delivery date: {row['requested_date']}" if action == "approve"
                       else f"Rejected: {decision_notes or 'no reason given'}"),
                 link_page="/warehouse", related_table="po_reschedule_requests",
                 related_ref=str(req_id))
    return {"decided": new_status, "id": req_id, "po_number": row["PO_Number"],
            "new_date": row["requested_date"] if action == "approve" else None}


# --- force-close (H8): PR / PO / line, required reason, 24h undo -------------
import json as _json  # noqa: E402

FORCE_UNDO_WINDOW_H = 24


async def force_close(session: AsyncSession, *, username: str, target_type: str,
                      target_ref: str, reason: str, notes: str = "") -> dict:
    if target_type not in ("pr", "po", "line"):
        return {"error": "target_type must be pr, po or line"}
    if not (reason or "").strip():
        return {"error": "a reason is required"}
    prior: dict = {}
    site = pr = po = None

    if target_type == "po":
        row = (await session.execute(select(
            purchase_orders_t.c["status"], purchase_orders_t.c["Site_ID"]
        ).where(purchase_orders_t.c["PO_Number"] == target_ref))).first()
        if row is None:
            return {"error": f"PO {target_ref} not found"}
        if row[0] in ("force_closed", "closed", "cancelled"):
            return {"error": f"PO {target_ref} is already {row[0]}"}
        prior = {"status": row[0]}
        po, site = target_ref, row[1]
        await session.execute(update(purchase_orders_t).where(
            purchase_orders_t.c["PO_Number"] == target_ref).values(
            status="force_closed", close_reason=reason, closed_by=username, closed_at=func.now()))

    elif target_type == "pr":
        row = (await session.execute(select(
            pr_master_t.c["status"], pr_master_t.c["logistics_status"], pr_master_t.c["Site_ID"]
        ).where(pr_master_t.c["PR_Number"] == target_ref).limit(1))).first()
        if row is None:
            return {"error": f"PR {target_ref} not found"}
        if (row[1] or "") == "force_closed":
            return {"error": f"PR {target_ref} is already force-closed"}
        prior = {"status": row[0], "logistics_status": row[1]}
        pr, site = target_ref, row[2]
        await session.execute(update(pr_master_t).where(
            pr_master_t.c["PR_Number"] == target_ref).values(
            status="force_closed", logistics_status="force_closed"))

    else:  # line
        try:
            line_id = int(target_ref)
        except (TypeError, ValueError):
            return {"error": "line target_ref must be a po_items id"}
        row = (await session.execute(select(
            po_items_t.c["line_status"], po_items_t.c["PO_Number"]
        ).where(po_items_t.c["id"] == line_id))).first()
        if row is None:
            return {"error": f"PO line {line_id} not found"}
        if row[0] in ("closed", "force_closed"):
            return {"error": f"line {line_id} is already {row[0]}"}
        prior = {"line_status": row[0]}
        po = row[1]
        await session.execute(update(po_items_t).where(po_items_t.c["id"] == line_id).values(
            line_status="force_closed", close_reason=reason))

    cid = (await session.execute(insert(po_force_closures_t).values(
        target_type=target_type, target_ref=str(target_ref), Site_ID=site,
        PR_Number=pr, PO_Number=po, reason=reason, closed_by=username,
        notes=(notes or None), prior_state=_json.dumps(prior)
    ).returning(po_force_closures_t.c["id"]))).scalar_one()
    await write_audit(session, username, "FORCE_CLOSE", "po_force_closures",
                      f"id={cid} {target_type}={target_ref}: {reason}")
    await notify(session, event_key="force_close", recipient_role="logistics",
                 severity="warning", title=f"Force-closed {target_type} {target_ref}",
                 body=f"{username}: {reason}. Undo available for {FORCE_UNDO_WINDOW_H}h.",
                 link_page="/logistics", related_table="po_force_closures", related_ref=str(cid))
    return {"closed": True, "id": cid, "target_type": target_type, "target_ref": str(target_ref)}


async def undo_force_close(session: AsyncSession, *, username: str, closure_id: int) -> dict:
    row = (await session.execute(select(po_force_closures_t).where(
        po_force_closures_t.c["id"] == closure_id))).mappings().first()
    if row is None:
        return {"error": f"force-closure {closure_id} not found"}
    if row["reverted_at"] is not None:
        return {"error": f"closure {closure_id} was already undone"}
    # 24h window computed in-DB to sidestep naive/aware datetime issues.
    age_h = (await session.execute(text(
        "SELECT EXTRACT(EPOCH FROM (now() - closed_at))/3600.0 "
        "FROM po_force_closures WHERE id = :id"), {"id": closure_id})).scalar_one()
    if age_h is not None and age_h > FORCE_UNDO_WINDOW_H:
        return {"error": f"the {FORCE_UNDO_WINDOW_H}h undo window has elapsed ({age_h:.1f}h ago)"}
    prior = {}
    try:
        prior = _json.loads(row["prior_state"] or "{}")
    except (ValueError, TypeError):
        prior = {}

    if row["target_type"] == "po":
        await session.execute(update(purchase_orders_t).where(
            purchase_orders_t.c["PO_Number"] == row["PO_Number"]).values(
            status=prior.get("status", "open"), close_reason=None,
            closed_by=None, closed_at=None))
    elif row["target_type"] == "pr":
        await session.execute(update(pr_master_t).where(
            pr_master_t.c["PR_Number"] == row["PR_Number"]).values(
            status=prior.get("status", "open"),
            logistics_status=prior.get("logistics_status", "submitted")))
    else:  # line
        await session.execute(update(po_items_t).where(
            po_items_t.c["id"] == int(row["target_ref"])).values(
            line_status=prior.get("line_status", "open"), close_reason=None))

    await session.execute(update(po_force_closures_t).where(
        po_force_closures_t.c["id"] == closure_id).values(
        reverted_at=func.now(), reverted_by=username))
    await write_audit(session, username, "FORCE_CLOSE_UNDO", "po_force_closures",
                      f"id={closure_id} {row['target_type']}={row['target_ref']}")
    return {"reverted": True, "id": closure_id}


async def list_force_closures(session: AsyncSession):
    sql = text('''
        SELECT id, target_type, target_ref, "PR_Number", "PO_Number", reason,
               closed_by, closed_at, reverted_at, reverted_by, notes,
               EXTRACT(EPOCH FROM (now() - closed_at))/3600.0 AS age_hours
        FROM po_force_closures ORDER BY id DESC LIMIT 500''')
    return _rows(await session.execute(sql))


async def assign_po(session: AsyncSession, *, username: str, po_number: str, warehouse_id: str,
                    expected_delivery: str | None = None, notes: str = "") -> dict:
    active = (await session.execute(select(func.count()).select_from(warehouses_t).where(
        (warehouses_t.c["Warehouse_ID"] == warehouse_id)
        & (warehouses_t.c["status"] == "active")))).scalar_one()
    if not active:
        return {"error": f"warehouse {warehouse_id} not active / not found"}
    po = (await session.execute(select(purchase_orders_t.c["status"])
          .where(purchase_orders_t.c["PO_Number"] == po_number))).first()
    if po is None:
        return {"error": f"PO {po_number} not found"}
    if po[0] in ("closed", "force_closed", "cancelled"):
        return {"error": f"PO {po_number} is {po[0]} — cannot assign"}

    await session.execute(insert(po_assignments_t).values(
        PO_Number=po_number, Warehouse_ID=warehouse_id, Expected_Delivery=expected_delivery,
        assigned_by=username, notes=notes or "", status="assigned"))
    if expected_delivery:
        await session.execute(update(purchase_orders_t).where(
            purchase_orders_t.c["PO_Number"] == po_number).values(
            Expected_Delivery=func.coalesce(purchase_orders_t.c["Expected_Delivery"], expected_delivery)))
    await write_audit(session, username, "ASSIGN_PO", "po_assignments",
                      f"PO={po_number} warehouse={warehouse_id}")
    await notify(session, event_key="po_assigned_to_warehouse", recipient_role="warehouse_user",
                 recipient_warehouse=warehouse_id,
                 title=f"PO {po_number} assigned to {warehouse_id}",
                 body="Acknowledge and receive it in the Warehouse portal.",
                 link_page="/warehouse", related_table="po_assignments", related_ref=po_number)
    return {"assigned": True, "po_number": po_number, "warehouse_id": warehouse_id}
