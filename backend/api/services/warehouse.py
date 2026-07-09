"""
backend/api/services/warehouse.py — the warehouse receiving + DN flow.

Ports the warehouse side from database.py:
  * assignments_for   — list_assignments_for_warehouse() (PRICES never joined)
  * acknowledge       — acknowledge_assignment()
  * receive           — record_warehouse_receipt()  (bump Delivered_Qty, roll status)
  * create_dn         — create_delivery_note()       (RL/BL separation + available guard)
  * ship_dn           — mark a DN in_transit (outbound)

Prices (Unit_Price / Total_Price) are never returned to the warehouse role.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch, notify

po_assignments_t = _MD.tables["po_assignments"]
purchase_orders_t = _MD.tables["purchase_orders"]
po_items_t = _MD.tables["po_items"]
delivery_notes_t = _MD.tables["delivery_notes"]
dn_items_t = _MD.tables["dn_items"]
pending_receipts_t = _MD.tables["pending_receipts"]
inventory_t = _MD.tables["inventory"]


def _rows(res):
    return [dict(m) for m in res.mappings().all()]


# --- reads -------------------------------------------------------------------
async def assignments_for(session: AsyncSession, warehouse_id: str, statuses: list[str] | None):
    where = 'a."Warehouse_ID" = :wh'
    params: dict = {"wh": warehouse_id}
    if statuses:
        keys = ",".join(f":s{i}" for i in range(len(statuses)))
        where += f" AND a.status IN ({keys})"
        params.update({f"s{i}": s for i, s in enumerate(statuses)})
    sql = text(f'''
        SELECT a.id AS assignment_id, a."PO_Number", a."Expected_Delivery",
               a.assigned_by, a.assigned_at, a.acknowledged_at, a.status, a.notes,
               po."PR_Number", po."Site_ID", po."Vendor_Name", po."PO_Date"
        FROM po_assignments a
        JOIN purchase_orders po ON po."PO_Number" = a."PO_Number"
        WHERE {where}
        ORDER BY a.assigned_at DESC''')
    return _rows(await session.execute(sql, params))


async def assignment_items(session: AsyncSession, assignment_id: int):
    """PO items for the assignment (no prices)."""
    a = (await session.execute(select(po_assignments_t.c["PO_Number"])
         .where(po_assignments_t.c["id"] == assignment_id))).first()
    if a is None:
        return []
    stmt = select(
        po_items_t.c["id"], po_items_t.c["line_no"], po_items_t.c["Material_Code"],
        po_items_t.c["Description"], po_items_t.c["Qty"], po_items_t.c["UOM"],
        po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"],
        po_items_t.c["rl_bl_family"], po_items_t.c["line_status"],
    ).where(po_items_t.c["PO_Number"] == a[0]).order_by(po_items_t.c["line_no"])
    return _rows(await session.execute(stmt))


async def dns_for(session: AsyncSession, warehouse_id: str | None, status: str | None):
    conds, params = [], {}
    if warehouse_id:
        conds.append('"Warehouse_ID" = :wh')
        params["wh"] = warehouse_id
    if status:
        conds.append("status = :st")
        params["st"] = status
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = text(f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", rl_bl_family,
               "DN_Date", "Vehicle_No", "Driver_Name", status, created_by
        FROM delivery_notes {where}
        ORDER BY "DN_Number" DESC LIMIT 500''')
    return _rows(await session.execute(sql, params))


async def dn_lines(session: AsyncSession, dn_number: str):
    stmt = select(
        dn_items_t.c["id"], dn_items_t.c["po_item_id"], dn_items_t.c["Material_Code"],
        dn_items_t.c["Description"], dn_items_t.c["Qty"], dn_items_t.c["UOM"],
        dn_items_t.c["Lot_Number"], dn_items_t.c["Expiry_Date"],
        dn_items_t.c["rl_bl_family"], dn_items_t.c["status"],
    ).where(dn_items_t.c["DN_Number"] == dn_number).order_by(dn_items_t.c["id"])
    return _rows(await session.execute(stmt))


# --- mutations ---------------------------------------------------------------
async def acknowledge(session: AsyncSession, *, username: str, assignment_id: int) -> dict:
    res = await session.execute(update(po_assignments_t).where(
        (po_assignments_t.c["id"] == assignment_id)
        & (po_assignments_t.c["status"] == "assigned")
    ).values(status="acknowledged", acknowledged_at=func.now(), acknowledged_by=username))
    if res.rowcount == 0:
        return {"error": "assignment not found or already acknowledged"}
    await write_audit(session, username, "ACK_ASSIGNMENT", "po_assignments",
                      f"id={assignment_id}")
    return {"acknowledged": True, "id": assignment_id}


async def receive(session: AsyncSession, *, username: str, assignment_id: int,
                  received_map: dict) -> dict:
    a = (await session.execute(select(po_assignments_t.c["PO_Number"], po_assignments_t.c["status"])
         .where(po_assignments_t.c["id"] == assignment_id))).first()
    if a is None:
        return {"error": "assignment not found"}
    if a[1] in ("closed", "cancelled"):
        return {"error": f"assignment is {a[1]}"}
    po_number = a[0]

    affected = 0
    for raw_id, raw_qty in received_map.items():
        try:
            item_id, qty = int(raw_id), float(raw_qty)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        line = (await session.execute(select(
            po_items_t.c["Qty"], po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"])
            .where((po_items_t.c["id"] == item_id) & (po_items_t.c["PO_Number"] == po_number)))).first()
        if line is None:
            continue
        ordered, already, returned = float(line[0] or 0), float(line[1] or 0), float(line[2] or 0)
        new_delivered = already + qty
        if new_delivered - returned > ordered + 1e-9:
            return {"error": f"cannot receive {qty}: over-delivers line {item_id} "
                             f"(ordered {ordered}, already {already})"}
        new_status = "delivered" if new_delivered - returned >= ordered - 1e-9 else "partially_delivered"
        await session.execute(update(po_items_t).where(po_items_t.c["id"] == item_id)
                              .values(Delivered_Qty=new_delivered, line_status=new_status))
        affected += 1

    if affected == 0:
        return {"error": "no valid line items in received map"}

    agg = (await session.execute(select(
        func.count(),
        func.sum(case((po_items_t.c["line_status"] == "delivered", 1), else_=0)),
    ).where(po_items_t.c["PO_Number"] == po_number))).first()
    total, done = agg[0], (agg[1] or 0)
    if done and total == done:
        await session.execute(update(po_assignments_t).where(po_assignments_t.c["id"] == assignment_id).values(status="received"))
        await session.execute(update(purchase_orders_t).where(purchase_orders_t.c["PO_Number"] == po_number).values(status="delivered"))
    else:
        await session.execute(update(po_assignments_t).where(po_assignments_t.c["id"] == assignment_id).values(status="partial"))
        await session.execute(update(purchase_orders_t).where(purchase_orders_t.c["PO_Number"] == po_number).values(status="partially_delivered"))

    await write_audit(session, username, "WAREHOUSE_RECEIVE", "po_items",
                      f"PO={po_number} assignment={assignment_id} lines={affected}")
    return {"received": True, "po_number": po_number, "lines": affected}


async def _generate_dn_number(session: AsyncSession, warehouse_id: str) -> str:
    today = _dt.date.today().isoformat().replace("-", "")
    prefix = f"DN-{warehouse_id}-{today}-"
    cnt = (await session.execute(select(func.count()).select_from(delivery_notes_t)
           .where(delivery_notes_t.c["DN_Number"].like(prefix + "%")))).scalar_one()
    return f"{prefix}{cnt + 1:03d}"


async def create_dn(session: AsyncSession, *, username: str, po_number: str, warehouse_id: str,
                    site_id: str, line_items: list[dict], header: dict | None = None) -> dict:
    if not line_items:
        return {"error": "at least one line item is required"}
    ids = [int(li["po_item_id"]) for li in line_items if li.get("po_item_id") is not None]
    if not ids:
        return {"error": "po_item_id missing on every line"}

    rows = (await session.execute(select(
        po_items_t.c["id"], po_items_t.c["Material_Code"], po_items_t.c["Description"],
        po_items_t.c["UOM"], po_items_t.c["rl_bl_family"], po_items_t.c["Qty"],
        po_items_t.c["Delivered_Qty"], po_items_t.c["Returned_Qty"],
    ).where((po_items_t.c["PO_Number"] == po_number) & (po_items_t.c["id"].in_(ids))))).all()
    by_id = {r[0]: r for r in rows}
    if len(by_id) != len(set(ids)):
        return {"error": "one or more line items not found on this PO"}

    families = {by_id[i][4] for i in ids}
    if len(families - {None}) > 1:
        return {"error": "RL/BL strict separation violated — prepare one DN per family"}
    family = next(iter(families - {None})) if families - {None} else None

    for li in line_items:
        iid = int(li["po_item_id"])
        qty = float(li.get("Qty") or 0)
        if qty <= 0:
            return {"error": f"Qty must be > 0 on line {iid}"}
        delivered, returned = float(by_id[iid][6] or 0), float(by_id[iid][7] or 0)
        shipped = (await session.execute(text(
            'SELECT COALESCE(SUM(di."Qty"),0) FROM dn_items di '
            'JOIN delivery_notes dn ON dn."DN_Number" = di."DN_Number" '
            "WHERE di.po_item_id = :iid AND dn.status NOT IN ('rejected','cancelled')"),
            {"iid": iid})).scalar_one()
        available = delivered - returned - float(shipped or 0)
        if qty > available + 1e-9:
            return {"error": f"line {iid}: shipping {qty} exceeds available {available:g} "
                             f"(delivered {delivered}, returned {returned}, on live DNs {float(shipped or 0):g})"}

    dn_number = await _generate_dn_number(session, warehouse_id)
    h = header or {}
    await session.execute(insert(delivery_notes_t).values(
        DN_Number=dn_number, PO_Number=po_number, Warehouse_ID=warehouse_id, Site_ID=site_id,
        rl_bl_family=family, DN_Date=h.get("DN_Date") or _dt.date.today().isoformat(),
        Vehicle_No=h.get("Vehicle_No"), Driver_Name=h.get("Driver_Name"),
        Driver_Phone=h.get("Driver_Phone"), Prepared_By=h.get("Prepared_By") or username,
        Remarks=h.get("Remarks"), status="draft", created_by=username))

    for li in line_items:
        iid = int(li["po_item_id"])
        base = by_id[iid]
        await session.execute(insert(dn_items_t).values(
            DN_Number=dn_number, po_item_id=iid, Material_Code=base[1], Description=base[2],
            Qty=float(li.get("Qty") or 0), UOM=base[3], Lot_Number=li.get("Lot_Number"),
            Expiry_Date=li.get("Expiry_Date"), Remarks=li.get("Remarks"),
            rl_bl_family=base[4], status="pending"))

    await write_audit(session, username, "CREATE_DN", "delivery_notes",
                      f"DN={dn_number} PO={po_number} site={site_id} lines={len(line_items)}")
    return {"created": True, "dn_number": dn_number, "lines": len(line_items)}


# --- DN multi-stage approval (Phase 6) --------------------------------------
# draft → (WH submit) pending_logistics → (Logistics vets date/logistics)
# pending_hod → (HOD vets content) hod_approved → (WH ship) in_transit →
# (SK receipt) received. A reject at either gate → 'rejected' (WH can resubmit).
async def _dn_row(session: AsyncSession, dn_number: str):
    return (await session.execute(select(
        delivery_notes_t.c["Site_ID"], delivery_notes_t.c["PO_Number"],
        delivery_notes_t.c["Warehouse_ID"], delivery_notes_t.c["created_by"],
        delivery_notes_t.c["status"],
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()


async def submit_dn(session: AsyncSession, *, username: str, dn_number: str) -> dict:
    res = await session.execute(update(delivery_notes_t).where(
        (delivery_notes_t.c["DN_Number"] == dn_number)
        & (delivery_notes_t.c["status"].in_(["draft", "prepared", "rejected"]))
    ).values(status="pending_logistics", rejection_reason=None))
    if res.rowcount == 0:
        return {"error": "DN not found or not in a submittable state"}
    await write_audit(session, username, "SUBMIT_DN", "delivery_notes", f"DN={dn_number}")
    await dispatch(session, event_key="dn_pending_logistics", recipient_role="logistics",
                   wa_template="action_required",
                   title=f"DN {dn_number} awaiting logistics approval",
                   body="Review the delivery date / logistics details.", link_page="/logistics",
                   related_table="delivery_notes", related_ref=dn_number, created_by=username)
    return {"submitted": True, "dn_number": dn_number, "status": "pending_logistics"}


async def decide_dn_logistics(session: AsyncSession, *, username: str, dn_number: str,
                              action: str, reason: str = "") -> dict:
    if action not in ("approve", "reject"):
        return {"error": "action must be approve or reject"}
    row = await _dn_row(session, dn_number)
    if row is None:
        return {"error": f"DN {dn_number} not found"}
    if row[4] != "pending_logistics":
        return {"error": f"DN {dn_number} is {row[4]} — not awaiting logistics"}
    if action == "approve":
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="pending_hod", logistics_decided_at=func.now(),
                    logistics_decided_by=username, logistics_decision="approved"))
        await dispatch(session, event_key="dn_pending_hod", recipient_role="hod",
                       recipient_site=row[0], wa_template="action_required",
                       title=f"DN {dn_number} awaiting HOD approval",
                       body="Logistics approved the delivery — review the DN content.",
                       link_page="/hod/approvals", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
        new = "pending_hod"
    else:
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="rejected", logistics_decided_at=func.now(),
                    logistics_decided_by=username, logistics_decision="rejected",
                    rejection_reason=reason or None))
        await dispatch(session, event_key="dn_rejected", recipient_warehouse=row[2],
                       severity="warning", wa_template="status_update",
                       title=f"DN {dn_number} rejected by logistics",
                       body=f"Reason: {reason or 'not given'}", link_page="/warehouse",
                       related_table="delivery_notes", related_ref=dn_number, created_by=username)
        new = "rejected"
    await write_audit(session, username, f"DN_LOGISTICS_{action.upper()}", "delivery_notes", f"DN={dn_number}")
    return {"decided": new, "dn_number": dn_number}


async def decide_dn_hod(session: AsyncSession, *, username: str, dn_number: str,
                        action: str, reason: str = "") -> dict:
    if action not in ("approve", "reject"):
        return {"error": "action must be approve or reject"}
    row = await _dn_row(session, dn_number)
    if row is None:
        return {"error": f"DN {dn_number} not found"}
    if row[4] != "pending_hod":
        return {"error": f"DN {dn_number} is {row[4]} — not awaiting HOD"}
    if action == "approve":
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="hod_approved", hod_decided_at=func.now(), hod_decided_by=username))
        await dispatch(session, event_key="dn_hod_approved", recipient_warehouse=row[2],
                       severity="success", wa_template="status_update",
                       title=f"DN {dn_number} approved — ready to ship",
                       body="HOD approved the DN content. Ship it from the Warehouse portal.",
                       link_page="/warehouse", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
        new = "hod_approved"
    else:
        await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
            .values(status="rejected", hod_decided_at=func.now(), hod_decided_by=username,
                    rejection_reason=reason or None))
        await dispatch(session, event_key="dn_rejected", recipient_warehouse=row[2],
                       severity="warning", wa_template="status_update",
                       title=f"DN {dn_number} rejected by HOD",
                       body=f"Reason: {reason or 'not given'}", link_page="/warehouse",
                       related_table="delivery_notes", related_ref=dn_number, created_by=username)
        new = "rejected"
    await write_audit(session, username, f"DN_HOD_{action.upper()}", "delivery_notes", f"DN={dn_number}")
    return {"decided": new, "dn_number": dn_number}


async def ship_dn(session: AsyncSession, *, username: str, dn_number: str) -> dict:
    # Gate: a DN may only ship once it has cleared BOTH approval stages.
    res = await session.execute(update(delivery_notes_t).where(
        (delivery_notes_t.c["DN_Number"] == dn_number)
        & (delivery_notes_t.c["status"] == "hod_approved")
    ).values(status="in_transit"))
    if res.rowcount == 0:
        return {"error": "DN not found or not HOD-approved yet (submit → logistics → HOD first)"}
    await write_audit(session, username, "SHIP_DN", "delivery_notes", f"DN={dn_number}")
    dest = (await session.execute(select(
        delivery_notes_t.c["Site_ID"], delivery_notes_t.c["PO_Number"]
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()
    if dest is not None:
        await dispatch(session, event_key="dn_shipped", recipient_role="store_keeper",
                       recipient_site=dest[0], wa_template="status_update",
                       title=f"Delivery {dn_number} incoming",
                       body=f"DN for PO {dest[1] or '—'} is in transit — receive it under Incoming Deliveries.",
                       link_page="/site/incoming", related_table="delivery_notes",
                       related_ref=dn_number, created_by=username)
    return {"shipped": True, "dn_number": dn_number, "status": "in_transit"}


# --- site side: incoming DNs → stage receipts (closes the loop) --------------
async def incoming_dns(session: AsyncSession, site_id: str | None):
    """In-transit DNs headed to a site (what the site SK is about to receive)."""
    conds = ['status = \'in_transit\'']
    params: dict = {}
    if site_id:
        conds.append('"Site_ID" = :site')
        params["site"] = site_id
    sql = text(f'''
        SELECT "DN_Number", "PO_Number", "Warehouse_ID", "Site_ID", rl_bl_family,
               "DN_Date", "Vehicle_No", "Driver_Name", status
        FROM delivery_notes WHERE {" AND ".join(conds)}
        ORDER BY "DN_Number" DESC''')
    return _rows(await session.execute(sql, params))


async def stage_dn_receipt(session: AsyncSession, *, username: str, dn_number: str,
                           actor_site: str | None) -> dict:
    """Site receives an in-transit DN → stage one pending_receipts row per line
    (status=pending_hod) at the destination site, so it flows into the HOD
    Approvals → Receipts queue (approve → commit_receipt → ledger). Maps
    Material_Code → SAP_Code via inventory (ports sk_mark_dn_received's mapping),
    then flips the DN to 'received'."""
    dn = (await session.execute(select(
        delivery_notes_t.c["PO_Number"], delivery_notes_t.c["Site_ID"],
        delivery_notes_t.c["Warehouse_ID"], delivery_notes_t.c["status"],
    ).where(delivery_notes_t.c["DN_Number"] == dn_number))).first()
    if dn is None:
        return {"error": "DN not found"}
    po_no, site_id, wh_id, status = dn
    if status != "in_transit":
        return {"error": f"DN status is {status} — only in_transit DNs can be received"}
    # Site scoping: a site user can only receive DNs for their own site (admin any).
    if actor_site and site_id != actor_site:
        return {"error": f"DN is for site {site_id}, not your site ({actor_site})"}

    items = await dn_lines(session, dn_number)
    if not items:
        return {"error": "DN has no items"}

    staged = 0
    for it in items:
        qty = float(it.get("Qty") or 0)
        if qty <= 0:
            continue
        mat = it.get("Material_Code")
        sap_row = (await session.execute(select(inventory_t.c["SAP_Code"])
                   .where(inventory_t.c["Material_Code"] == mat).limit(1))).first()
        sap = sap_row[0] if sap_row else mat
        await session.execute(insert(pending_receipts_t).values(
            Date=_dt.date.today().isoformat(), SAP_Code=sap, Quantity=qty, Site_ID=site_id,
            Supplier="WAREHOUSE", DN_No=dn_number, DN_Number=dn_number, Warehouse_ID=wh_id,
            PO_Number_Source=po_no, Lot_Number=it.get("Lot_Number"),
            Expiry_Date=it.get("Expiry_Date"), Remarks=f"Received via DN {dn_number}",
            status="pending_hod"))
        await session.execute(update(dn_items_t).where(dn_items_t.c["id"] == it["id"])
                              .values(status="received", sk_received_qty=qty))
        staged += 1

    await session.execute(update(delivery_notes_t).where(delivery_notes_t.c["DN_Number"] == dn_number)
                          .values(status="received", sk_received_at=func.now(), sk_received_by=username))
    await write_audit(session, username, "DN_RECEIVE_STAGED", "pending_receipts",
                      f"DN={dn_number} PO={po_no} site={site_id} lines={staged}")
    return {"received": True, "dn_number": dn_number, "staged": staged, "site_id": site_id,
            "message": f"Staged {staged} receipt(s) from DN {dn_number} for HOD approval"}
