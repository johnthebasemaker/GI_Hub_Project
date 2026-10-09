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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, attach_material_names, write_audit  # metadata + audit writer
from .notifications import dispatch, notify

pr_master_t = _MD.tables["pr_master"]
pr_registry_t = _MD.tables["pr_registry"]
purchase_orders_t = _MD.tables["purchase_orders"]
po_items_t = _MD.tables["po_items"]
po_assignments_t = _MD.tables["po_assignments"]
warehouses_t = _MD.tables["warehouses"]
inventory_t = _MD.tables["inventory"]
po_reschedule_t = _MD.tables["po_reschedule_requests"]
po_force_closures_t = _MD.tables["po_force_closures"]
vendors_t = _MD.tables["vendors"]
po_returns_t = _MD.tables["po_returns"]

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


async def _next_pr_number(session: AsyncSession, *, site_id: str,
                          username: str) -> str:
    """Reserve the next site PR number: PR-YYYYMMDD-NNNN (resets daily).

    ⚠️ THE DATABASE DECIDES WHO GOT THE NUMBER, NOT WHOEVER READ LAST. This was
    a read-then-write with nothing behind it —

        last = SELECT ... LIKE 'PR-20260822-%' ORDER BY id DESC LIMIT 1
        nxt  = int(last.split('-')[-1]) + 1

    — and `pr_master."PR_Number"` cannot be unique because a PR is MANY LINES.
    Two HODs creating a PR in the same second both read `0003` and both wrote
    `0004`, and from that moment two different purchase requests were one PR to
    every query in the system: the Logistics queue, the PO, the audit trail.
    Nothing raised. It was simply wrong, quietly, forever.

    `pr_registry` is the table where a PR number appears ONCE and can therefore
    carry a primary key. The insert is the reservation; a conflict means
    somebody else took that number in the microsecond between, so we look again
    and try the next one.

    The scan reads the HIGHEST number in EITHER table, and both halves matter:

      · the registry holds numbers RESERVED but whose lines are not written
        yet — the window the old version fell straight through;
      · `pr_master` holds numbers that exist but were never registered, which
        is any row an import, a fixture or a pre-migration path wrote.

    Reserving alone would not be enough on its own: an unregistered number in
    `pr_master` would be handed out again and the INSERT would happily succeed.
    """
    today = _dt.date.today().strftime("%Y%m%d")
    prefix = f"PR-{today}-"
    for _ in range(60):
        last = (await session.execute(text(
            'SELECT MAX(n) FROM ('
            '  SELECT "PR_Number" AS n FROM pr_registry WHERE "PR_Number" LIKE :p'
            '  UNION ALL'
            '  SELECT "PR_Number" AS n FROM pr_master   WHERE "PR_Number" LIKE :p'
            ") x"), {"p": prefix + "%"})).scalar_one_or_none()
        nxt = 1
        if last:
            try:
                nxt = int(str(last).split("-")[-1]) + 1
            except (ValueError, IndexError):
                nxt = 1
        candidate = f"{prefix}{nxt:04d}"
        taken = (await session.execute(
            pg_insert(pr_registry_t)
            .values(PR_Number=candidate, Site_ID=site_id, created_by=username)
            .on_conflict_do_nothing(index_elements=["PR_Number"])
            .returning(pr_registry_t.c["PR_Number"]))).scalar_one_or_none()
        if taken is not None:
            return candidate
    # 60 collisions in one call means something other than contention — a
    # corrupted registry, or a clock that has stopped. Refuse loudly rather
    # than spinning or handing back a number we could not reserve.
    raise RuntimeError(
        f"could not reserve a PR number under {prefix} after 60 attempts — "
        f"check pr_registry for corrupt rows")


# --- reads -------------------------------------------------------------------
async def hod_prs(session: AsyncSession, site_id: str | None):
    """Site PRs grouped by PR_Number — the HOD's own queue (to submit)."""
    where = '"status" = \'open\''
    params: dict = {}
    if site_id:
        where += " AND COALESCE(\"Site_ID\",'HQ') = :site"
        params["site"] = site_id
    # `draft_lines` is what the Submit button is allowed to depend on. The
    # aggregated `logistics_status` is a MAX over the group, and MAX is
    # LEXICOGRAPHIC: a PR holding both site_draft and submitted lines reports
    # 'submitted', which would hide the button while real draft lines were
    # still waiting. A count cannot lie in that direction.
    sql = text(f'''
        SELECT "PR_Number", COALESCE("Site_ID",'HQ') AS "Site_ID",
               COUNT(*) AS line_count, SUM("Requested_Qty") AS total_qty,
               MAX(COALESCE(logistics_status,'site_draft')) AS logistics_status,
               COUNT(*) FILTER (
                   WHERE COALESCE(logistics_status,'site_draft') = 'site_draft'
               ) AS draft_lines,
               COUNT(*) FILTER (
                   WHERE COALESCE(logistics_status,'site_draft') = 'submitted'
               ) AS submitted_lines,
               COUNT(*) FILTER (
                   WHERE COALESCE(logistics_status,'site_draft') = 'in_po'
               ) AS in_po_lines
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
    if site_id is not None:
        stmt = stmt.where(func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
    return _rows(await session.execute(stmt.order_by(pr_master_t.c["id"])))


async def po_list(session: AsyncSession, status: str | None):
    """Purchase orders, each carrying its CURRENT assignment.

    The assignment columns were added 2026-08-13. Without them the Logistics
    grid had no way to know a PO was already assigned, so it offered `Assign`
    on every row forever and a second click silently created a second
    assignment — a second warehouse notification, and two warehouses each
    believing the goods were theirs. The UI could not have got this right; it
    was never told.

    LEFT JOIN on the newest assignment, so an UNassigned PO still returns its
    row with NULLs. There is no 'cancelled' assignment state — the only three
    ever written are assigned · partial · received (the last two by the
    warehouse as it receives), and all three mean "this PO has a warehouse".
    """
    where, params = "", {}
    if status:
        where = "WHERE po.status = :status"
        params["status"] = status
    sql = text(f'''
        SELECT po."PO_Number", po."PR_Number", po."Site_ID", po."Vendor_Name",
               po."PO_Date", po."Expected_Delivery", po.status, po.created_by,
               po.created_at,
               a."Warehouse_ID"  AS assigned_warehouse,
               a.assigned_by     AS assigned_by,
               a.assigned_at     AS assigned_at,
               a.status          AS assignment_status
        FROM purchase_orders po
        LEFT JOIN LATERAL (
            SELECT "Warehouse_ID", assigned_by, assigned_at, status
            FROM po_assignments
            WHERE "PO_Number" = po."PO_Number"
            ORDER BY id DESC LIMIT 1
        ) a ON TRUE
        {where}
        ORDER BY po."PO_Number" DESC LIMIT 500''')
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
async def _verify_scan(session: AsyncSession, attachment_id: int | None,
                       *, expect: str, username: str) -> int | None:
    """Validate a `source_attachment_id` before it is stored on a PR/PO.

    QSEP slice 6. The id arrives from the client on the confirm step, so it
    is checked rather than trusted — an unvalidated integer here would let a
    caller staple any attachment in the system to their PR, including one
    from another site, and the Document Library would then serve it under
    that PR's name to anyone who can read it.

    Two conditions: the row must be the RIGHT DOCUMENT TYPE (a consumption
    note is not a PR scan), and it must have been uploaded by THIS user. The
    uploader check is what makes it a link to your own extract call rather
    than a pointer at somebody else's document.
    """
    if attachment_id is None:
        return None
    att = _MD.tables["entry_attachments"]
    row = (await session.execute(select(att.c["doc_type"], att.c["uploaded_by"])
           .where(att.c["id"] == int(attachment_id)))).first()
    if row is None:
        raise ValueError(f"unknown attachment {attachment_id}")
    if row[0] != expect:
        raise ValueError(f"attachment {attachment_id} is a {row[0]} document, "
                         f"not a {expect}")
    if row[1] != username:
        raise ValueError(f"attachment {attachment_id} was uploaded by someone else")
    return int(attachment_id)


async def create_pr(session: AsyncSession, *, username: str, site_id: str,
                    lines: list[dict], supplier: str | None = None,
                    notes: str | None = None, delivery_date: str | None = None,
                    source_attachment_id: int | None = None) -> dict:
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
        code = str(ln.get("Material_Code") or "").strip().upper()
        if not sap and code:
            # Phase 23d (ruling Q23-5): a PR for a catalogue item GI Hub does not
            # stock yet. If the item master has the code after all, it is that
            # SAP; otherwise the line carries the GI code and NO SAP (none is
            # invented) and links itself when the workbook adds the item.
            stocked = (await session.execute(select(inventory_t.c["SAP_Code"]).where(
                func.upper(func.trim(inventory_t.c["Material_Code"])) == code).limit(1))).scalar()
            if stocked:
                sap = str(stocked).strip()
            else:
                cat = (await session.execute(text(
                    'SELECT description, uom FROM material_catalog WHERE "Material_Code" = :c '
                    "AND removed_at IS NULL"), {"c": code})).first()
                if cat is None:
                    return {"error": f"{code} is not in the material catalogue"}
                try:
                    qty = float(ln.get("Requested_Qty") or 0)
                except (TypeError, ValueError):
                    return {"error": f"line {code}: qty is not a number"}
                if qty <= 0:
                    return {"error": f"line {code}: qty must be > 0"}
                try:
                    est = float(ln.get("Est_Cost_SAR") or 0)
                except (TypeError, ValueError):
                    est = 0.0
                prepared.append({
                    "SAP_Code": "", "Material_Code": code,
                    "Material_Name": (str(ln.get("Material_Name") or "").strip() or cat[0] or ""),
                    "Requested_Qty": qty,
                    "UOM": (str(ln.get("UOM") or "").strip() or cat[1] or ""),
                    "Est_Cost_SAR": est,
                    "Notes": (str(ln.get("Notes") or "").strip() or (notes or "")),
                })
                continue
        if not sap:
            return {"error": "every line needs a SAP_Code (or a catalogue GI code)"}
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

    try:
        scan_id = await _verify_scan(session, source_attachment_id,
                                     expect="pr_scan", username=username)
    except ValueError as e:
        return {"error": str(e)}

    pr_number = await _next_pr_number(session, site_id=site_id,
                                      username=username)
    for ln in prepared:
        await session.execute(insert(pr_master_t).values(
            PR_Number=pr_number, Site_ID=site_id, SAP_Code=ln["SAP_Code"],
            Material_Code=ln["Material_Code"], Material_Name=ln["Material_Name"],
            Requested_Qty=ln["Requested_Qty"], UOM=ln["UOM"],
            Est_Cost_SAR=ln["Est_Cost_SAR"], Supplier=(supplier or None),
            Notes=(ln["Notes"] or None), Delivery_Date=(delivery_date or None),
            status="open", workflow_state="draft", logistics_status="site_draft",
            source_attachment_id=scan_id))
    if scan_id:
        # Bind the stored scan to the rows it produced, so the Document
        # Library can answer "which PR came from this document".
        att = _MD.tables["entry_attachments"]
        await session.execute(update(att).where(att.c["id"] == scan_id)
                              .values(entry_table="pr_master", doc_number=pr_number))

    await write_audit(session, username, "CREATE_PR", "pr_master",
                      f"PR={pr_number} site={site_id} lines={len(prepared)}"
                      + (f" scan={scan_id}" if scan_id else ""))
    return {"created": True, "pr_number": pr_number, "site_id": site_id,
            "lines": len(prepared), "source_attachment_id": scan_id}


# The PR line's journey. Stated as data so the guards below and any future
# transition read from ONE definition rather than each carrying its own idea of
# what may follow what.
#
#   site_draft ──submit──> submitted ──po_raised──> in_po ──> closed
#        │                     │                      │
#        └──────────── force_closed ─────────────────-┘
PR_TRANSITIONS = {
    "submit":    ("site_draft",),
    "po_raised": ("submitted",),
}


async def _pr_states(session: AsyncSession, pr_number: str,
                     site_id: str) -> dict[str, int]:
    """How many lines of this PR sit in each state — the diagnosis behind a
    refusal. "No eligible lines" is not actionable; "already submitted" is."""
    # ⚠️ RAW SQL, on purpose. Built through the ORM, the same
    # `func.coalesce(logistics_status, 'site_draft')` written in both the
    # SELECT and the GROUP BY renders as two DIFFERENT bind parameters
    # ($1 and $5), and Postgres then refuses the statement — it cannot see that
    # the two expressions are the same one. A literal is the shortest honest
    # fix; the alternative is a labelled subquery for a two-column aggregate.
    rows = (await session.execute(text(
        "SELECT COALESCE(logistics_status, 'site_draft') AS st, COUNT(*) "
        'FROM pr_master '
        'WHERE "PR_Number" = :pr AND COALESCE("Site_ID", \'HQ\') = :site '
        "GROUP BY COALESCE(logistics_status, 'site_draft')"),
        {"pr": pr_number, "site": site_id})).all()
    return {str(s): int(n) for s, n in rows}


def _state_refusal(pr_number: str, states: dict, wanted: tuple) -> str:
    """Why the transition was refused, in the words of what IS there."""
    if not states:
        return f"PR {pr_number} has no lines at this site"
    named = ", ".join(f"{n} line(s) {s}" for s, n in sorted(states.items()))
    return (f"PR {pr_number} has nothing in state "
            f"{' or '.join(wanted)} — it holds {named}")


async def submit_pr(session: AsyncSession, *, username: str, pr_number: str, site_id: str) -> dict:
    """Draft → submitted, ONCE.

    ⚠️ THIS USED TO ACCEPT `logistics_status IN ('site_draft', 'submitted')`,
    which made a second submit a silent success: the UPDATE matched the already
    submitted rows, rewrote their timestamp, and fired a SECOND
    `pr_submitted_to_logistics` notification. Logistics saw one PR arrive twice
    and had no way to tell which was real.

    Now the transition is read, then attempted against its expected state, and
    a zero rowcount is an ERROR rather than a shrug — the two halves of the
    guard, because the read alone loses a race and the UPDATE alone cannot say
    why it matched nothing.
    """
    states = await _pr_states(session, pr_number, site_id)
    wanted = PR_TRANSITIONS["submit"]
    if not any(states.get(s) for s in wanted):
        return {"error": _state_refusal(pr_number, states, wanted)}

    res = await session.execute(update(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == pr_number)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft").in_(wanted))
    ).values(logistics_status="submitted", submitted_to_logistics_at=func.now(),
             submitted_to_logistics_by=username))
    if res.rowcount == 0:
        # The read said there was work and the write found none, so somebody
        # else moved it in between. Refusing is right: the other caller has
        # already sent Logistics their notification.
        return {"error": f"PR {pr_number} was submitted by somebody else while "
                         f"this request was in flight"}
    await write_audit(session, username, "SUBMIT_PR_TO_LOGISTICS", "pr_master",
                      f"PR={pr_number} site={site_id} lines={res.rowcount}")
    await dispatch(session, event_key="pr_submitted_to_logistics", recipient_role="logistics",
                   wa_template="action_required", title=f"New PR {pr_number} from {site_id}",
                   body=f"{res.rowcount} line(s) awaiting PO issuance.",
                   link_page="/logistics", related_table="pr_master", related_ref=pr_number,
                   created_by=username)
    return {"submitted": True, "pr_number": pr_number, "lines": res.rowcount}


async def create_po_from_pr(session: AsyncSession, *, username: str, pr_number: str,
                            site_id: str, po_number: str, vendor_code: str | None = None,
                            vendor_name: str | None = None,
                            expected_delivery: str | None = None,
                            source_attachment_id: int | None = None,
                            line_ids: list[int] | None = None) -> dict:
    """Submitted lines → a PO, and those lines only.

    ⚠️ A PR MAY CARRY SEVERAL POs (operator ruling Q7, 2026-08-20). Partial
    fulfilment splits one request across vendors or deliveries, so the lock is
    NOT one PO per PR — it is per LINE. `line_ids` selects which submitted lines
    this PO covers; omitting it takes all of them, which is what every existing
    caller does and means. Lines this PO does not cover stay `submitted` and
    remain available to the next one.

    ⚠️ THE FLIP TO `in_po` IS ASSERTED, NOT ASSUMED. It used to be a fire-and-
    forget UPDATE whose rowcount nobody read, so a second PO raised against the
    same PR matched ZERO rows and passed silently — the PO was created, the PR
    was left exactly as it was, and only the vendor eventually noticed that the
    same material had been ordered twice.
    """
    stmt = select(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == pr_number)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "submitted"))
    if line_ids:
        stmt = stmt.where(pr_master_t.c["id"].in_(list(line_ids)))
    lines = (await session.execute(stmt)).mappings().all()
    if not lines:
        states = await _pr_states(session, pr_number, site_id)
        if line_ids:
            return {"error": f"none of the {len(line_ids)} selected line(s) of "
                             f"PR {pr_number} are submitted and still free — "
                             f"the PR holds "
                             + (", ".join(f"{n} {s}" for s, n
                                          in sorted(states.items())) or "nothing")}
        return {"error": _state_refusal(pr_number, states,
                                        PR_TRANSITIONS["po_raised"])}
    if line_ids and len(lines) != len(set(line_ids)):
        return {"error": f"{len(set(line_ids)) - len(lines)} of the selected "
                         f"line(s) are not submitted lines of PR {pr_number} at "
                         f"this site — nothing was ordered"}

    exists = (await session.execute(select(func.count()).select_from(purchase_orders_t)
              .where(purchase_orders_t.c["PO_Number"] == po_number))).scalar_one()
    if exists:
        return {"error": f"PO {po_number} already exists"}

    today = _dt.date.today().isoformat()
    try:
        scan_id = await _verify_scan(session, source_attachment_id,
                                     expect="po_scan", username=username)
    except ValueError as e:
        return {"error": str(e)}
    await session.execute(insert(purchase_orders_t).values(
        PO_Number=po_number, PR_Number=pr_number, Site_ID=site_id,
        Vendor_Code=vendor_code, Vendor_Name=vendor_name, PO_Date=today,
        Expected_Delivery=expected_delivery, source="api", created_by=username,
        status="open", source_attachment_id=scan_id))
    if scan_id:
        att = _MD.tables["entry_attachments"]
        await session.execute(update(att).where(att.c["id"] == scan_id)
                              .values(entry_table="purchase_orders",
                                      doc_number=po_number))

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

    # THE TRANSITION, asserted. Exactly the lines this PO covers move to
    # `in_po`; a mismatch means somebody else consumed one while we were
    # building the PO, and the whole thing must fail rather than ship a PO
    # whose lines are already on another.
    flipped = await session.execute(update(pr_master_t).where(
        (pr_master_t.c["id"].in_([ln["id"] for ln in lines]))
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "submitted")
    ).values(logistics_status="in_po"))
    if flipped.rowcount != len(lines):
        raise RuntimeError(
            f"PR {pr_number}: expected to move {len(lines)} line(s) to in_po "
            f"but moved {flipped.rowcount} — another PO claimed them while "
            f"this one was being built. Nothing has been committed.")

    await write_audit(session, username, "CREATE_PO", "purchase_orders",
                      f"PO={po_number} PR={pr_number} site={site_id} lines={len(lines)}")
    # QSEP notification gap #1. Raising the PO was audited and otherwise
    # silent, so the site that submitted the PR learned nothing: they had
    # asked for material and had no way to know whether anybody had ordered
    # it short of opening the Logistics portal, which their role cannot.
    await dispatch(session, event_key="po_created_for_pr", severity="success",
                   recipient_role="hod", recipient_site=site_id,
                   wa_template="status_update",
                   title=f"Your PR is on order — PO {po_number}",
                   body=(f"{username} raised PO {po_number} against PR {pr_number} "
                         f"({len(lines)} line(s))."),
                   link_page="/hod/prs", related_table="purchase_orders",
                   related_ref=po_number, created_by=username)
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


# --- HOD draft-PR management: edit a line + rename the PR number -------------
_PR_LINE_EDITABLE = {"Requested_Qty", "Supplier", "Est_Cost_SAR", "Material_Name",
                     "UOM", "Notes", "WBS_Number", "Delivery_Date"}


async def update_pr_line(session: AsyncSession, *, username: str, line_id: int,
                         fields: dict, caller_site: str | None = None) -> dict:
    row = (await session.execute(select(
        pr_master_t.c["PR_Number"], pr_master_t.c["logistics_status"], pr_master_t.c["Site_ID"]
    ).where(pr_master_t.c["id"] == line_id))).first()
    if row is None:
        return {"error": f"PR line {line_id} not found"}
    if (row[1] or "site_draft") != "site_draft":
        return {"error": f"PR {row[0]} is {row[1]} — only draft lines can be edited"}
    if caller_site is not None and (row[2] or "HQ") != caller_site:
        return {"error": "you may only edit PRs for your own site"}
    clean = {k: v for k, v in fields.items() if k in _PR_LINE_EDITABLE}
    if not clean:
        return {"error": "no editable fields provided"}
    if "Requested_Qty" in clean:
        try:
            q = float(clean["Requested_Qty"])
        except (TypeError, ValueError):
            return {"error": "Requested_Qty must be a number"}
        if q <= 0:
            return {"error": "Requested_Qty must be > 0"}
        clean["Requested_Qty"] = q
    if clean.get("Est_Cost_SAR") is not None:
        try:
            clean["Est_Cost_SAR"] = float(clean["Est_Cost_SAR"])
        except (TypeError, ValueError):
            return {"error": "Est_Cost_SAR must be a number"}
    await session.execute(update(pr_master_t).where(pr_master_t.c["id"] == line_id).values(**clean))
    await write_audit(session, username, "PR_LINE_EDIT", "pr_master",
                      f"line={line_id} pr={row[0]} {sorted(clean)}")
    return {"updated": True, "id": line_id, "fields": sorted(clean)}


async def rename_pr(session: AsyncSession, *, username: str, old_pr: str,
                    site_id: str, new_pr: str) -> dict:
    new_pr = (new_pr or "").strip()
    if not new_pr:
        return {"error": "a new PR number is required"}
    if new_pr == old_pr:
        return {"error": "the new PR number is the same as the old one"}
    # ⚠️ BOTH TABLES, and each catches something the other cannot.
    #
    #   · `pr_registry` knows numbers that are RESERVED — including one a
    #     transaction has taken but not yet written lines for, which is exactly
    #     the window the reservation exists to close and which `pr_master`
    #     cannot see;
    #   · `pr_master` knows numbers that EXIST — including rows written by a
    #     path that never went through `create_pr` (an import, a fixture, a
    #     migration), which the registry has no record of.
    #
    # Checking only the registry would let a rename walk straight onto a real
    # PR that had never been registered.
    exists = (await session.execute(text(
        'SELECT (SELECT COUNT(*) FROM pr_registry WHERE "PR_Number" = :n) '
        '     + (SELECT COUNT(*) FROM pr_master   WHERE "PR_Number" = :n)'),
        {"n": new_pr})).scalar_one()
    if exists:
        return {"error": f"PR {new_pr} already exists"}
    res = await session.execute(update(pr_master_t).where(
        (pr_master_t.c["PR_Number"] == old_pr)
        & (func.coalesce(pr_master_t.c["Site_ID"], "HQ") == site_id)
        & (func.coalesce(pr_master_t.c["logistics_status"], "site_draft") == "site_draft")
    ).values(PR_Number=new_pr))
    if res.rowcount == 0:
        return {"error": f"PR {old_pr} has no draft lines to rename at {site_id}"}
    # Move the reservation with the PR. Renaming without this would leave the
    # old number reserved forever AND the new one unreserved — so a later PR
    # could be issued the number this one now carries.
    await session.execute(update(pr_registry_t)
                          .where(pr_registry_t.c["PR_Number"] == old_pr)
                          .values(PR_Number=new_pr))
    await write_audit(session, username, "PR_RENAME", "pr_master",
                      f"{old_pr}→{new_pr} site={site_id} lines={res.rowcount}")
    return {"renamed": True, "old_pr": old_pr, "new_pr": new_pr, "lines": res.rowcount}


# --- logistics vendor-returns (raise to vendor → reopen PO line) -------------
async def raise_vendor_return(session: AsyncSession, *, username: str, po_number: str,
                              po_item_id: int, qty: float, reason: str,
                              expected_resupply: str | None = None, notes: str | None = None) -> dict:
    if not (reason or "").strip():
        return {"error": "a reason is required"}
    if qty <= 0:
        return {"error": "qty must be > 0"}
    line = (await session.execute(select(
        po_items_t.c["Material_Code"], po_items_t.c["Delivered_Qty"],
        po_items_t.c["Returned_Qty"], po_items_t.c["Qty"], po_items_t.c["PO_Number"],
    ).where((po_items_t.c["id"] == po_item_id) & (po_items_t.c["PO_Number"] == po_number)))).first()
    if line is None:
        return {"error": f"PO line {po_item_id} not found on {po_number}"}
    delivered, returned, ordered = float(line[1] or 0), float(line[2] or 0), float(line[3] or 0)
    on_hand = delivered - returned
    if qty > on_hand + 1e-9:
        return {"error": f"cannot return {qty:g} — only {on_hand:g} delivered-and-unreturned on this line"}

    rid = (await session.execute(insert(po_returns_t).values(
        PO_Number=po_number, po_item_id=po_item_id, Material_Code=line[0], Qty=qty,
        Reason=reason, raised_by_role="logistics", raised_by=username,
        Expected_Resupply=expected_resupply, status="open", notes=notes
    ).returning(po_returns_t.c["id"]))).scalar_one()

    # Reopen the PO line: track the return + flip it back to open so the vendor
    # re-delivering is expected again.
    new_returned = returned + qty
    reopened = (delivered - new_returned) < ordered - 1e-9
    await session.execute(update(po_items_t).where(po_items_t.c["id"] == po_item_id).values(
        Returned_Qty=new_returned,
        line_status="open" if reopened else po_items_t.c["line_status"]))
    if reopened:
        await session.execute(update(purchase_orders_t).where(
            (purchase_orders_t.c["PO_Number"] == po_number)
            & (purchase_orders_t.c["status"].in_(["delivered", "closed"]))
        ).values(status="partially_delivered"))

    await write_audit(session, username, "VENDOR_RETURN_RAISE", "po_returns",
                      f"id={rid} po={po_number} line={po_item_id} qty={qty:g}: {reason}")
    await dispatch(session, event_key="vendor_return_raised", recipient_role="logistics",
                   severity="warning", wa_template="action_required",
                   title=f"Vendor return raised on PO {po_number}",
                   body=f"{line[0] or 'line'} × {qty:g} — {reason}"
                        + (f" (resupply {expected_resupply})" if expected_resupply else ""),
                   link_page="/logistics", related_table="po_returns", related_ref=str(rid),
                   created_by=username)
    return {"raised": True, "id": rid, "po_number": po_number, "reopened_line": reopened}


async def list_vendor_returns(session: AsyncSession, status: str | None):
    stmt = select(po_returns_t).order_by(po_returns_t.c["id"].desc()).limit(500)
    if status:
        stmt = stmt.where(po_returns_t.c["status"] == status)
    # `po_returns` records only the Material_Code, and the row is a decision
    # about physical goods going back to a vendor — name what they are.
    return await attach_material_names(
        session, _rows(await session.execute(stmt)), code_key="Material_Code")


async def close_vendor_return(session: AsyncSession, *, username: str, return_id: int,
                              notes: str | None = None) -> dict:
    row = (await session.execute(select(
        po_returns_t.c["status"], po_returns_t.c["PO_Number"],
        po_returns_t.c["Material_Code"], po_returns_t.c["Qty"],
        po_returns_t.c["raised_by"], po_returns_t.c["Reason"])
        .where(po_returns_t.c["id"] == return_id))).first()
    if row is None:
        return {"error": f"vendor return {return_id} not found"}
    if row[0] == "closed":
        return {"error": f"vendor return {return_id} is already closed"}
    await session.execute(update(po_returns_t).where(po_returns_t.c["id"] == return_id).values(
        status="closed", closed_at=func.now(), closed_by=username,
        notes=notes if notes is not None else po_returns_t.c["notes"]))
    await write_audit(session, username, "VENDOR_RETURN_CLOSE", "po_returns", f"id={return_id}")
    # QSEP notification gap #4. Raising a vendor return notifies logistics;
    # CLOSING it — the resupply has landed and the reopened PO line is
    # covered — told nobody, least of all the person who raised it and had
    # been chasing the vendor.
    for who in {row[4], username} - {username}:
        await dispatch(session, event_key="vendor_return_closed", severity="success",
                       recipient_user=who, wa_template="status_update",
                       title=f"Vendor return closed — PO {row[1]}",
                       body=(f"{username} closed the return of {float(row[3] or 0):g} × "
                             f"{row[2] or '—'}." + (f" {notes.strip()}" if notes else "")),
                       link_page="/logistics", related_table="po_returns",
                       related_ref=str(return_id), created_by=username)
    await dispatch(session, event_key="vendor_return_closed", severity="success",
                   recipient_role="logistics", wa_template="status_update",
                   title=f"Vendor return closed — PO {row[1]}",
                   body=(f"{username} closed return #{return_id} "
                         f"({float(row[3] or 0):g} × {row[2] or '—'})."),
                   link_page="/logistics", related_table="po_returns",
                   related_ref=str(return_id), created_by=username)
    return {"closed": True, "id": return_id, "po_number": row[1]}


# --- reschedule workflow (H7) ------------------------------------------------
# WH/HOD raise a reschedule request → Logistics decides → approved date is
# pushed onto the PO (and its warehouse assignments). In-app notify only
# (WhatsApp/email stay parked).
async def raise_reschedule(session: AsyncSession, *, username: str, role: str,
                           po_number: str, requested_date: str, reason: str,
                           dn_number: str | None = None,
                           urgency: str = "normal") -> dict:
    """Ask Logistics to move a PO's delivery date.

    QSEP slice 6 — "Urgent Delivery" is this, with `urgency="urgent"`, and it
    is the same table on purpose: a request to bring a delivery FORWARD is a
    reschedule to an earlier date, and it needs the identical mandatory
    reason, the identical one-open-request rule and the identical approve
    step that pushes the new date onto the PO. A parallel table would have
    duplicated all of that to change one word.

    What `urgent` actually buys is `severity="critical"`, and that is not
    cosmetic: `notifications.dispatch()` forces a critical event out
    IMMEDIATELY even when the request carries X-Delivery-Preference:
    evening. An urgent request that waits for the 16:00 digest arrives after
    the working day it was meant to change.
    """
    if not (requested_date or "").strip():
        return {"error": "a requested delivery date is required"}
    if not (reason or "").strip():
        return {"error": "a reason is required"}
    urgency = (urgency or "normal").strip().lower()
    if urgency not in ("normal", "urgent"):
        return {"error": "urgency must be 'normal' or 'urgent'"}
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
        requested_by=username, status="pending", urgency=urgency
    ).returning(po_reschedule_t.c["id"]))).scalar_one()
    urgent = urgency == "urgent"
    await write_audit(session, username, "RAISE_RESCHEDULE", "po_reschedule_requests",
                      f"id={rid} PO={po_number} → {requested_date}"
                      + (" URGENT" if urgent else ""))
    await dispatch(
        session, event_key="reschedule_raised", recipient_role="logistics",
        # critical → dispatch() sends NOW, bypassing the evening digest.
        severity="critical" if urgent else "info",
        wa_template="critical_alert" if urgent else "action_required",
        title=("URGENT delivery requested — PO " if urgent
               else "Reschedule requested — PO ") + po_number,
        body=(f"{role} {username} asks to bring PO {po_number} forward to "
              f"{requested_date} (currently {po[0] or 'unscheduled'}). "
              f"Reason: {reason}" if urgent else
              f"{role} {username} requests {requested_date}. Reason: {reason}"),
        link_page="/logistics", related_table="po_reschedule_requests",
        related_ref=str(rid), created_by=username)
    return {"raised": True, "id": rid, "po_number": po_number, "urgency": urgency}


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
    await dispatch(session, event_key="reschedule_decided", recipient_user=row["requested_by"],
                   severity=("success" if action == "approve" else "warning"),
                   wa_template="status_update",
                   title=f"Reschedule {new_status} — PO {row['PO_Number']}",
                   body=(f"New delivery date: {row['requested_date']}" if action == "approve"
                         else f"Rejected: {decision_notes or 'no reason given'}"),
                   link_page="/warehouse", related_table="po_reschedule_requests",
                   related_ref=str(req_id), created_by=username)
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
    await dispatch(session, event_key="force_close", recipient_role="logistics",
                   severity="warning", wa_template="critical_alert",
                   title=f"Force-closed {target_type} {target_ref}",
                   body=f"{username}: {reason}. Undo available for {FORCE_UNDO_WINDOW_H}h.",
                   link_page="/logistics", related_table="po_force_closures", related_ref=str(cid),
                   created_by=username)
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

    # Already assigned? Refuse, and say to whom.
    #
    # Hiding the button (2026-08-13) is the visible half of this fix; THIS is
    # the half that holds. A disabled button does not survive a double-click
    # before the grid refetches, a stale tab, or anybody calling the endpoint
    # directly — and the failure was silent by construction: two rows in
    # po_assignments, two `po_assigned_to_warehouse` notifications, and two
    # warehouses each told the goods were coming to them. Nothing raised, so
    # the first anyone knew was a warehouse waiting for a delivery that had
    # been routed elsewhere.
    #
    # Re-assignment is refused rather than replaced on purpose: moving a PO
    # that another warehouse has already been told to expect is a decision,
    # not a correction. There is no route for it today, and inventing a silent
    # one here would be the same bug wearing a different shape.
    prior = (await session.execute(select(
        po_assignments_t.c["Warehouse_ID"], po_assignments_t.c["status"]
    ).where(po_assignments_t.c["PO_Number"] == po_number)
     .order_by(po_assignments_t.c["id"].desc()).limit(1))).first()
    if prior is not None:
        if prior[0] == warehouse_id:
            # Idempotent: the same warehouse twice is a double-click, not a
            # conflict. Report success without writing a second row or
            # notifying the warehouse again.
            return {"assigned": True, "po_number": po_number,
                    "warehouse_id": warehouse_id, "already": True}
        return {"error": f"PO {po_number} is already assigned to {prior[0]} "
                         f"({prior[1]}) — it cannot be assigned to "
                         f"{warehouse_id} as well"}

    # THE CLAIM, asserted. The `prior` read above loses a race by construction —
    # two Logistics users clicking Assign in the same instant both see None.
    # This INSERT is conditional on there STILL being no assignment, so exactly
    # one of them writes a row and the other is told who won.
    claimed = (await session.execute(text(
        'INSERT INTO po_assignments ("PO_Number", "Warehouse_ID", '
        '                            "Expected_Delivery", assigned_by, notes, status) '
        "SELECT :po, :wh, :exp, :by, :notes, 'assigned' "
        'WHERE NOT EXISTS (SELECT 1 FROM po_assignments WHERE "PO_Number" = :po) '
        "RETURNING id"),
        {"po": po_number, "wh": warehouse_id, "exp": expected_delivery,
         "by": username, "notes": notes or ""},
    )).scalar_one_or_none()
    if claimed is None:
        winner = (await session.execute(select(
            po_assignments_t.c["Warehouse_ID"])
            .where(po_assignments_t.c["PO_Number"] == po_number)
            .order_by(po_assignments_t.c["id"].desc()).limit(1))).scalar()
        if winner == warehouse_id:
            return {"assigned": True, "po_number": po_number,
                    "warehouse_id": warehouse_id, "already": True}
        return {"error": f"PO {po_number} was assigned to {winner} while this "
                         f"request was in flight — it cannot go to "
                         f"{warehouse_id} as well"}
    if expected_delivery:
        await session.execute(update(purchase_orders_t).where(
            purchase_orders_t.c["PO_Number"] == po_number).values(
            Expected_Delivery=func.coalesce(purchase_orders_t.c["Expected_Delivery"], expected_delivery)))
    await write_audit(session, username, "ASSIGN_PO", "po_assignments",
                      f"PO={po_number} warehouse={warehouse_id}")
    await dispatch(session, event_key="po_assigned_to_warehouse", recipient_role="warehouse_user",
                   recipient_warehouse=warehouse_id, wa_template="action_required",
                   title=f"PO {po_number} assigned to {warehouse_id}",
                   body="Acknowledge and receive it in the Warehouse portal.",
                   link_page="/warehouse", related_table="po_assignments", related_ref=po_number,
                   created_by=username)
    return {"assigned": True, "po_number": po_number, "warehouse_id": warehouse_id}
