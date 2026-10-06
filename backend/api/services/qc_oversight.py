"""
backend/api/services/qc_oversight.py — what the Head of Qualities looks at.

⚠️ EVERY QUERY IN THIS MODULE IS FILTERED TO THE CONTROLLED CATEGORY, and that
is the role's boundary rather than a convenience. A `qc_hod` reads across every
site — the exemption in `auth.QC_OVERSIGHT_ROLES` — so without the category
filter the same account would be a cross-site window onto PPE, tools, consumables
and every price on every purchase order. The filter is applied in SQL, in each
function, never left to a caller to remember: a route that forgot it would leak
silently and look exactly like a working page.

The category itself is `quality.controlled_category()` — an app_settings value
defaulting to 'Surface Shields', matched EXACTLY on `inventory."Category"`.
Never a description token: a token match would drag in every item whose text
happens to mention shielding, and the role's scope would quietly grow whenever
somebody edited a description.

⚠️ THIS MODULE READS. The only write in the whole role is an escalation, which
is a message; see `raise_escalation` at the bottom and the three-path allowlist
in readonly.py.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit
from .notifications import dispatch
from .quality import controlled_category

esc_t = _MD.tables["qc_escalations"]
rules_t = _MD.tables["qc_stagnation_rules"]

# Where an escalation may be aimed. A Head of Qualities asks the people who can
# actually fix the thing: the inspector on the spot, the warehouse holding it,
# or Logistics who can get the certificate from the supplier.
TARGET_ROLES = ("qc", "warehouse_user", "logistics")
KINDS = ("mtc_demand", "inspection_request", "transfer_suggestion")

DEFAULT_STAGNANT_DAYS = 90
DEFAULT_EXPIRY_WARN_DAYS = 60


async def thresholds(session: AsyncSession) -> dict:
    """The stagnation policy for the controlled category.

    Falls back to the seeded defaults rather than raising, so a box whose rule
    row was deleted still shows a dashboard — with the numbers it is using
    stated on the page, never applied invisibly.
    """
    cat = await controlled_category(session)
    row = (await session.execute(select(rules_t).where(
        rules_t.c["Category"] == cat))).mappings().first()
    if row is None:
        return {"Category": cat, "stagnant_days": DEFAULT_STAGNANT_DAYS,
                "expiry_warn_days": DEFAULT_EXPIRY_WARN_DAYS,
                "source": "default (no rule row for this category)"}
    return {"Category": cat, "stagnant_days": int(row["stagnant_days"]),
            "expiry_warn_days": int(row["expiry_warn_days"]),
            "source": "qc_stagnation_rules"}


async def set_thresholds(session: AsyncSession, *, username: str,
                         stagnant_days: int, expiry_warn_days: int) -> dict:
    cat = await controlled_category(session)
    res = await session.execute(update(rules_t)
                                .where(rules_t.c["Category"] == cat)
                                .values(stagnant_days=int(stagnant_days),
                                        expiry_warn_days=int(expiry_warn_days),
                                        updated_by=username,
                                        updated_at=_dt.datetime.now()))
    if res.rowcount == 0:
        await session.execute(text(
            'INSERT INTO qc_stagnation_rules ("Category", stagnant_days, '
            "expiry_warn_days, updated_by) VALUES (:c, :s, :e, :u)"),
            {"c": cat, "s": int(stagnant_days), "e": int(expiry_warn_days),
             "u": username})
    await write_audit(session, username, "QC_HOD_SET_THRESHOLDS",
                      "qc_stagnation_rules",
                      f"{cat}: stagnant={stagnant_days}d expiry={expiry_warn_days}d")
    return await thresholds(session)


# ── the category filter, written once ────────────────────────────────────────
# Every SELECT below joins through this. Inlining it per query is how one of
# them eventually forgets.
_CONTROLLED = (
    'EXISTS (SELECT 1 FROM inventory i '
    '        WHERE TRIM(i."SAP_Code") = TRIM({sap}) '
    '          AND LOWER(TRIM(i."Category")) = LOWER(:cat))'
)


async def surface_shield_pos(session: AsyncSession,
                             site_id: Optional[str] = None) -> list[dict]:
    """Surface Shield PO lines, and whether a certificate exists for each.

    The question the role actually asks is not "which POs exist" but "which
    ordered material is going to arrive without paperwork" — so the MTC
    presence rides on every row rather than living on a second screen.
    """
    cat = await controlled_category(session)
    sql = text(f'''
        SELECT p."PO_Number", p."PR_Number", p."Site_ID", p."Vendor_Name",
               p."PO_Date", p."Expected_Delivery", p.status,
               it."Material_Code", it."Description", it."Qty", it."UOM",
               it.id AS po_item_id,
               COALESCE(i."SAP_Code", '') AS "SAP_Code",
               (SELECT COUNT(*) FROM mtc_documents m
                 WHERE (m.po_item_id = it.id
                        OR (m."Material_Code_Ref" IS NOT NULL
                            AND UPPER(TRIM(m."Material_Code_Ref"))
                                = UPPER(TRIM(it."Material_Code")))
                        OR (i."SAP_Code" IS NOT NULL
                            AND TRIM(m."SAP_Code") = TRIM(i."SAP_Code")))
                   AND COALESCE(m.status, 'attached') <> 'void') AS mtc_count
        FROM po_items it
        JOIN purchase_orders p ON p."PO_Number" = it."PO_Number"
        LEFT JOIN inventory i
               ON UPPER(TRIM(i."Material_Code")) = UPPER(TRIM(it."Material_Code"))
        WHERE LOWER(TRIM(COALESCE(i."Category", ''))) = LOWER(:cat)
          {'AND COALESCE(p."Site_ID", \'\') = :site' if site_id else ''}
        ORDER BY p."PO_Date" DESC NULLS LAST, p."PO_Number", it.line_no''')
    params = {"cat": cat}
    if site_id:
        params["site"] = site_id
    rows = [dict(r) for r in (await session.execute(sql, params)).mappings().all()]
    for r in rows:
        r["Has_MTC"] = int(r.pop("mtc_count") or 0) > 0
    return rows


async def mtc_register(session: AsyncSession, site_id: Optional[str] = None,
                       limit: int = 500) -> list[dict]:
    """Every certificate on file for controlled material, cross-site."""
    cat = await controlled_category(session)
    sql = text(f'''
        SELECT m.id, m."SAP_Code", m."Material_Code", m."Site_ID",
               m."Warehouse_ID", m."Lot_Number", m."Quantity", m.mtc_number,
               m.file_name, m.status, m.submitted_by, m.submitted_at,
               m."DN_Number", m.po_item_id, m.qc_inspection_id,
               i."Equipment_Description"
        FROM mtc_documents m
        LEFT JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(m."SAP_Code")
        WHERE LOWER(TRIM(COALESCE(i."Category", ''))) = LOWER(:cat)
          {'AND COALESCE(m."Site_ID", \'\') = :site' if site_id else ''}
        ORDER BY m.submitted_at DESC NULLS LAST
        LIMIT :lim''')
    params = {"cat": cat, "lim": int(limit)}
    if site_id:
        params["site"] = site_id
    return [dict(r) for r in (await session.execute(sql, params)).mappings().all()]


async def usage_by_site(session: AsyncSession) -> list[dict]:
    """Which sites are consuming which controlled materials, and how recently.

    "Track exactly which sites materials are being used at" — so the answer is
    per (site, material), with the last movement date, because a site that drew
    2,000 kg last March and nothing since is a different conversation from one
    drawing steadily.
    """
    cat = await controlled_category(session)
    sql = text('''
        SELECT COALESCE(c."Site_ID", 'HQ') AS "Site_ID",
               TRIM(c."SAP_Code") AS "SAP_Code",
               i."Equipment_Description", i."UOM",
               SUM(c."Quantity") AS consumed_qty,
               COUNT(*) AS draws,
               MAX(c."Date") AS last_used
        FROM consumption c
        JOIN inventory i ON TRIM(i."SAP_Code") = TRIM(c."SAP_Code")
        WHERE LOWER(TRIM(COALESCE(i."Category", ''))) = LOWER(:cat)
        GROUP BY 1, 2, 3, 4
        ORDER BY MAX(c."Date") DESC NULLS LAST, SUM(c."Quantity") DESC''')
    return [dict(r) for r in (await session.execute(sql, {"cat": cat})).mappings().all()]


async def stagnation(session: AsyncSession) -> dict:
    """Controlled lots that are sitting still, or running out of time.

    ⚠️ TWO DIFFERENT PROBLEMS, REPORTED SEPARATELY. A lot RECEIVED AND NEVER
    TOUCHED and a lot USED UNTIL MARCH AND THEN ABANDONED both look like
    "stagnant" if you only measure days since the last movement — but the first
    is a delivery nobody wanted and the second is a job that stopped. The row
    says which by carrying `last_movement` and `basis`.

    The redistribution suggestion is exactly that: the sites already drawing
    this material, most recent first. It is a CONTACT list, never a transfer —
    moving stock is somebody else's authority, and this role only has messages.
    """
    cat = await controlled_category(session)
    rule = await thresholds(session)
    today = _dt.date.today()

    # Phase 21a: the ONE lot balance (received − consumed − RETURNED ±
    # transfers). This used to subtract consumption only, so a lot sent back
    # to the supplier still looked like stagnant stock on the shelf.
    from ..stock import SQL_LOT_BALANCE
    sql = text(f'''
        WITH bal AS (
            SELECT lb."Lot_Number", TRIM(lb."SAP_Code") AS "SAP_Code",
                   COALESCE(lb."Site_ID", 'HQ') AS "Site_ID",
                   lb."Received_Date", lb."Expiry_Date", lb."Supplier", lb."Status",
                   lb."Remaining_Qty" AS remaining_qty,
                   (SELECT MAX(c."Date") FROM consumption c
                     WHERE c."Lot_Number" = lb."Lot_Number"
                       AND TRIM(c."SAP_Code") = TRIM(lb."SAP_Code")
                       AND COALESCE(c."Site_ID",'HQ') = COALESCE(lb."Site_ID",'HQ'))
                   AS last_consumed
            FROM ({SQL_LOT_BALANCE}) lb
        )
        SELECT b.*, i."Equipment_Description", i."UOM"
        FROM bal b
        JOIN inventory i ON TRIM(i."SAP_Code") = b."SAP_Code"
        WHERE LOWER(TRIM(COALESCE(i."Category", ''))) = LOWER(:cat)
          AND b.remaining_qty > 0
        ORDER BY b."Expiry_Date" NULLS LAST, b."Received_Date"''')
    rows = [dict(r) for r in (await session.execute(sql, {"cat": cat})).mappings().all()]

    def _d(v):
        try:
            return _dt.date.fromisoformat(str(v)[:10])
        except (TypeError, ValueError):
            return None

    stagnant, expiring, expired = [], [], []
    for r in rows:
        last = _d(r.get("last_consumed"))
        recv = _d(r.get("Received_Date"))
        basis_date = last or recv
        r["last_movement"] = str(basis_date) if basis_date else None
        # NEVER TOUCHED vs STOPPED. Same number of idle days, different problem
        # and a different conversation with the site.
        r["basis"] = ("never used since receipt" if last is None
                      else "last drawn")
        r["idle_days"] = (today - basis_date).days if basis_date else None
        exp = _d(r.get("Expiry_Date"))
        r["days_to_expiry"] = (exp - today).days if exp else None
        r["remaining_qty"] = round(float(r.get("remaining_qty") or 0), 3)

        if exp and r["days_to_expiry"] is not None and r["days_to_expiry"] < 0:
            expired.append(r)
        elif exp and r["days_to_expiry"] is not None \
                and r["days_to_expiry"] <= rule["expiry_warn_days"]:
            expiring.append(r)
        if r["idle_days"] is not None and r["idle_days"] >= rule["stagnant_days"]:
            stagnant.append(r)

    users = await usage_by_site(session)
    by_sap: dict[str, list] = {}
    for u in users:
        by_sap.setdefault(str(u["SAP_Code"]), []).append(
            {"Site_ID": u["Site_ID"], "last_used": u["last_used"],
             "consumed_qty": round(float(u["consumed_qty"] or 0), 3)})
    for bucket in (stagnant, expiring, expired):
        for r in bucket:
            # Where it could go: sites already drawing this material, most
            # recent first, EXCLUDING the one already holding it.
            r["could_move_to"] = [c for c in by_sap.get(str(r["SAP_Code"]), [])
                                  if c["Site_ID"] != r["Site_ID"]][:5]

    return {"thresholds": rule, "stagnant": stagnant, "expiring": expiring,
            "expired": expired,
            "counts": {"stagnant": len(stagnant), "expiring": len(expiring),
                       "expired": len(expired), "lots_held": len(rows)}}


async def overview(session: AsyncSession) -> dict:
    """The KPI strip: what is uncertified, what is going off, what is open."""
    from ..health_monitor import missing_mtc_rows

    cat = await controlled_category(session)
    missing = await missing_mtc_rows(session, None)
    stag = await stagnation(session)
    open_esc = (await session.execute(text(
        "SELECT COUNT(*) FROM qc_escalations WHERE status = 'open'"))).scalar() or 0
    sites = {str(m.get("place")) for m in missing if m.get("where") == "site"}
    whs = {str(m.get("place")) for m in missing if m.get("where") == "warehouse"}
    return {
        "category": cat,
        "uncertified_materials": len(missing),
        "sites_affected": len(sites),
        "warehouses_affected": len(whs),
        "places_affected": sorted(sites | whs),
        "stagnant_lots": stag["counts"]["stagnant"],
        "expiring_lots": stag["counts"]["expiring"],
        "expired_lots": stag["counts"]["expired"],
        "controlled_lots_held": stag["counts"]["lots_held"],
        "open_escalations": int(open_esc),
        "thresholds": stag["thresholds"],
    }


# ── the one write ────────────────────────────────────────────────────────────
async def raise_escalation(session: AsyncSession, *, username: str,
                           target_role: str, target_site: Optional[str],
                           target_warehouse: Optional[str], kind: str,
                           message: str, sap_code: Optional[str] = None,
                           material_code: Optional[str] = None,
                           lot_number: Optional[str] = None,
                           po_number: Optional[str] = None) -> dict:
    """Log the ask, then send it. In that order, and both.

    ⚠️ EXACTLY ONE TARGET PLACE (operator ruling Q12). Neither, or both, is
    refused rather than defaulted: a broadcast to every site QC about one
    site's material is the kind of message people learn to ignore, and an
    escalation nobody reads is worse than none because it LOOKS like the
    problem was raised.
    """
    target_role = (target_role or "").strip()
    kind = (kind or "").strip()
    message = (message or "").strip()
    site = (target_site or "").strip() or None
    wh = (target_warehouse or "").strip() or None

    if target_role not in TARGET_ROLES:
        return {"error": f"target_role must be one of {list(TARGET_ROLES)}"}
    if kind not in KINDS:
        return {"error": f"kind must be one of {list(KINDS)}"}
    if not message:
        return {"error": "say what you are asking for — an escalation with no "
                         "message is a notification nobody can act on"}
    if bool(site) == bool(wh):
        return {"error": "name EXACTLY ONE target — a site or a warehouse. A "
                         "message aimed at everywhere is one nobody owns"}
    if wh and target_role == "qc":
        pass   # a warehouse QC is a real recipient; both axes are legal for qc
    if site and target_role == "warehouse_user":
        return {"error": "a warehouse user belongs to a warehouse, not a site — "
                         "name the warehouse instead"}

    label = sap_code or material_code or "controlled material"
    place = site or wh
    titles = {
        "mtc_demand": f"MTC required — {label} at {place}",
        "inspection_request": f"Inspection requested — {label} at {place}",
        "transfer_suggestion": f"Redistribution suggested — {label} at {place}",
    }
    nid = await dispatch(
        session, event_key=f"qc_hod_{kind}", severity="warning",
        recipient_role=target_role, recipient_site=site,
        recipient_warehouse=wh, wa_template="action_required",
        title=titles[kind], body=f"{message}\n\n— raised by {username} (Quality)",
        link_page="/qc/inspections", related_table="qc_escalations",
        related_ref=label, created_by=username)

    new_id = (await session.execute(text(
        'INSERT INTO qc_escalations (raised_by, target_role, target_site, '
        '  target_warehouse, kind, "SAP_Code", "Material_Code", "Lot_Number", '
        '  "PO_Number", message, notification_id) '
        "VALUES (:by, :role, :site, :wh, :kind, :sap, :mat, :lot, :po, :msg, :nid) "
        "RETURNING id"),
        {"by": username, "role": target_role, "site": site, "wh": wh,
         "kind": kind, "sap": sap_code, "mat": material_code,
         "lot": lot_number, "po": po_number, "msg": message,
         "nid": nid})).scalar_one()

    await write_audit(session, username, "QC_HOD_ESCALATION", "qc_escalations",
                      f"#{new_id} {kind} → {target_role}@{place} ({label})")
    return {"created": True, "id": new_id, "notification_id": nid}


async def list_escalations(session: AsyncSession, status: Optional[str] = None,
                           limit: int = 300) -> list[dict]:
    stmt = select(esc_t)
    if status:
        stmt = stmt.where(esc_t.c["status"] == status)
    return [dict(r) for r in (await session.execute(
        stmt.order_by(esc_t.c["id"].desc()).limit(limit))).mappings().all()]


async def resolve_escalation(session: AsyncSession, *, username: str,
                             esc_id: int, note: str) -> dict:
    """Close one, with a note saying what happened.

    The transition is asserted the same way every procurement transition is
    (slice 8c): update WHERE the state is what we expect, and treat a zero
    rowcount as an error rather than a shrug. Closing an already-closed
    escalation would otherwise overwrite the first resolution note — the
    record of what actually fixed it.
    """
    note = (note or "").strip()
    if not note:
        return {"error": "say what happened — a resolution with no note loses "
                         "the only thing worth keeping"}
    res = await session.execute(
        update(esc_t)
        .where((esc_t.c["id"] == int(esc_id)) & (esc_t.c["status"] == "open"))
        .values(status="resolved", resolved_by=username,
                resolved_at=_dt.datetime.now(), resolution_note=note))
    if res.rowcount == 0:
        cur = (await session.execute(select(esc_t.c["status"]).where(
            esc_t.c["id"] == int(esc_id)))).scalar_one_or_none()
        if cur is None:
            return {"error": f"escalation #{esc_id} does not exist"}
        return {"error": f"escalation #{esc_id} is already {cur} — its "
                         f"resolution note is the record of what fixed it and "
                         f"is not overwritten"}
    await write_audit(session, username, "QC_HOD_ESCALATION_RESOLVED",
                      "qc_escalations", f"#{esc_id}")
    return {"resolved": True, "id": int(esc_id)}
