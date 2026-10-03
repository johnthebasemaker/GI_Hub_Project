"""
backend/api/entry.py — data-entry endpoints (ledger writes) for the new UI.

Thin HTTP layer over backend/api/services/ledger.py. Owns the transaction
boundary and input validation; the business rules live in the service.

  POST /entry/*  — stage a receipt/issue/return/adjustment (status=pending_hod) for
                 HOD approval; the HOD portal commits them to the ledger.

Actor: the acting username is the authenticated user, recorded on the ledger
row and in the audit log. Staging WRITES are exact-locked to store_keeper
(+ admin) — mirroring the legacy Entry Log page lock; other roles read via
Records/Stock but do not stage entries.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import LargeBinary, insert, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import (get_current_user, require_roles, resolve_site_param,
                   resolve_site_write, site_filter_applies, site_row_visible,
                   site_scope)
from .db import get_session
from . import entry_docs
from .services import emailer
from .services import idempotency as idem
from .services import ledger
from .services import quality
from .services import whatsapp as wa
from .services.notifications import dispatch, notify
from .stock import SQL_SITE_STOCK

router = APIRouter(prefix="/entry", tags=["data entry"])


# ── A replay is not a second entry (offline-queue double replay, 2026-09-26) ──
#
# The PWA offline queue (frontend/src/offline/queue.ts) removes an entry from
# IndexedDB only when the server's ANSWER arrives. A page that navigates or
# reloads while a replay is in flight has committed the row on the server and
# lost the answer, so the next page load replayed it again — a second pending
# receipt or issue for one physical movement. The same happens to an online
# submit whose response is lost after the commit.
#
# The client now mints ONE `Idempotency-Key` per submission, keeps it on the
# queued entry, and resends it on every replay. These routes CLAIM the key in
# the same transaction that stages the row (services/idempotency.py — the
# claim-then-fill protocol procurement already uses), so the key and the row
# commit together or not at all, and a repeat is handed the first answer.
# ⚠️ A missing key means "not asked for" and behaves exactly as before — the
# API is also called by tools and the E2E harness.
IdemKey = Header(default=None, alias="Idempotency-Key")


async def _idem_claim(session, key: Optional[str], action: str, body: BaseModel,
                      user: dict) -> Optional[dict]:
    return await idem.claim(session, key=key, action=action,
                            body=body.model_dump(mode="json"), username=user["username"])


async def _idem_finish(session, key: Optional[str], action: str, user: dict,
                       result: dict) -> None:
    await idem.finish(session, key=key, action=action, username=user["username"],
                      result=result)


async def _notify_hod_staged(session, *, kind_label: str, site_id: str, actor: str,
                             ref, detail: str) -> None:
    """Tell the site's HOD(s) that a new entry is waiting for approval."""
    await dispatch(session, event_key="entry_staged", recipient_role="hod",
                   recipient_site=site_id, wa_template="action_required",
                   title=f"{kind_label} awaiting approval",
                   body=f"{detail} — submitted by {actor}", link_page="/hod/approvals",
                   related_table="pending", related_ref=str(ref), created_by=actor)

# Columns a client may pass under `extra` on a receipt: real receipts columns
# minus the ones handled explicitly, minus id/blobs.
_RECEIPT_BASE = {
    "id", "Date", "SAP_Code", "Quantity", "Supplier", "Remarks",
    "Site_ID", "Expiry_Date", "PR_Number", "Lot_Number",
}
_RECEIPT_EXTRA_OK = {
    c.name for c in ledger.receipts_t.columns
    if c.name not in _RECEIPT_BASE and not isinstance(c.type, LargeBinary)
}


class ReceiptIn(BaseModel):
    Date: str = Field(..., description="Receipt date, YYYY-MM-DD")
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Supplier: Optional[str] = None
    Remarks: Optional[str] = None
    Expiry_Date: Optional[str] = Field(None, description="YYYY-MM-DD; auto-creates a lot")
    PR_Number: Optional[str] = None
    Lot_Number: Optional[str] = None
    MFD_Date: Optional[str] = Field(None, description="YYYY-MM-DD manufacture date of the lot (Phase 16c)")
    entry_uom: Optional[str] = Field(None, description="pack UoM the qty is entered in; converted to base")
    mtc_document_id: Optional[int] = Field(None, description="MTC upload id (required for Rubber materials)")
    wbs: Optional[str] = Field(None, description="WBS Number (required once the site has active WBS)")
    Bin_Location: Optional[str] = Field(None, description="Bin / shelf tag (parity B5)")
    attachment_ids: list[int] = Field(default_factory=list,
                                      description="entry_attachments ids (gated by require_entry_documents)")
    extra: Optional[dict[str, Any]] = Field(
        None, description="Optional extra receipts columns (logistics fields)")


# --- receipt entry guards (2026-07 UAT): MTC gate + pack→base UoM conversion -
#
# QSEP 2026-08: the category test moved to services/quality.py. It used to be
# inlined here and wired to exactly TWO call sites, while warehouse goods-in,
# DN creation and DN receipt all walked past it. `_mtc_category` is kept as a
# thin alias because it is part of this module's tested surface.
async def _mtc_category(session) -> str:
    """Parity A3 — the LEGACY rubber trigger is an exact inventory Category
    match (config.py MTC_REQUIRED_CATEGORY = "Surface Shields"), NOT a
    description token. Configurable via app_settings mtc_required_category."""
    return await quality.controlled_category(session)


async def _receipt_meta(session, sap: str) -> dict:
    row = (await session.execute(text(
        'SELECT "UOM", "Category" FROM inventory WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'
    ), {"s": sap})).first()
    base_uom = row[0] if row else None
    is_rubber = await quality.is_controlled(session, sap_code=sap)
    convs = [dict(m) for m in (await session.execute(text(
        'SELECT "Pack_UOM", "Factor" FROM uom_conversions '
        'WHERE TRIM("SAP_Code") = TRIM(:s) ORDER BY "Pack_UOM"'), {"s": sap})).mappings().all()]
    # Phase 16c — is this a lot item, and how long does it keep? The Receive
    # form asks for the batch, MFD and expiry only when it is.
    from .services import lots as _lots
    mode = (await _lots.tracking(session)).get(sap)
    life = (await session.execute(text(
        'SELECT "Shelf_Life_Months" FROM inventory WHERE TRIM("SAP_Code") = TRIM(:s)'),
        {"s": sap})).scalar()
    return {"sap_code": sap, "base_uom": base_uom, "is_rubber": is_rubber, "conversions": convs,
            "lot_mode": mode, "shelf_life_months": life}


async def _apply_receipt_guards(session, data: dict) -> Optional[int]:
    """Convert an entry (pack) UoM to the base UoM, and pick up any MTC.

    Mutates data['Quantity']/['Remarks'] in place; returns the
    mtc_document_id to link post-stage. Raises HTTPException only for a UoM
    it cannot convert.

    ⚠️ This used to REFUSE a Surface-Shield receipt with no certificate. The
    2026-08-12 ruling removed that: material arriving from a vendor or from
    Logistics is booked in whether or not the paper has caught up, and the
    block moved to issue. A receipt is a statement that something physically
    happened, and refusing to record it does not un-happen it — it just makes
    real stock invisible to everyone downstream.
    """
    sap = str(data["SAP_Code"]).strip()
    # Phase 16c — a lot's dates must make sense (ruling Q16-7: an expiry before
    # the manufacture date is a typo, never loaded)
    mfd, exp = (data.get("MFD_Date") or "")[:10], (data.get("Expiry_Date") or "")[:10]
    for label, v in (("MFD", mfd), ("expiry", exp)):
        if v:
            try:
                dt.date.fromisoformat(v)
            except ValueError:
                raise HTTPException(422, f"{label} date {v!r} is not YYYY-MM-DD")
    if mfd and exp and exp < mfd:
        raise HTTPException(422, f"expiry {exp} is before the manufacture date {mfd} — "
                                 f"check the label")
    if mfd and mfd > dt.date.today().isoformat():
        raise HTTPException(422, f"manufacture date {mfd} is in the future")
    meta = await _receipt_meta(session, sap)
    entry_uom = (data.get("entry_uom") or "").strip()
    if entry_uom and meta["base_uom"] and entry_uom != meta["base_uom"]:
        factor = next((float(c["Factor"]) for c in meta["conversions"]
                       if c["Pack_UOM"] == entry_uom), None)
        if factor is None:
            raise HTTPException(422, f"no pack→base conversion for {entry_uom!r} on {sap}")
        orig = float(data["Quantity"])
        data["Quantity"] = round(orig * factor, 6)
        note = f"[{orig:g} {entry_uom} × {factor:g} → {data['Quantity']:g} {meta['base_uom']}]"
        data["Remarks"] = ((str(data.get("Remarks") or "").strip() + " " + note).strip())
    # A controlled material with no certificate attached is no longer refused;
    # `_warn_if_uncertified` picks that up AFTER staging, once it can tell
    # whether the site already inherits one from the PO or the DN.
    return data.get("mtc_document_id")


async def _link_mtc(session, mtc_id: Optional[int], pending_id) -> None:
    if mtc_id and pending_id is not None:
        await session.execute(text(
            "UPDATE mtc_documents SET pending_receipt_id = :pid WHERE id = :mid"),
            {"pid": int(pending_id), "mid": int(mtc_id)})


async def _open_site_inspection(session, *, data: dict, mtc_id: Optional[int],
                                pending_id, actor: str) -> None:
    """QSEP — controlled material arriving AT A SITE needs a QC to look at it.

    Called after the receipt is staged, so the inspection can carry the
    pending_receipts id as its source_ref and the unique constraint can make
    a retried submission idempotent. `open_inspection` returns None for
    anything outside the controlled category, so this is a no-op for the
    other 430 materials in the master.
    """
    if pending_id is None:
        return
    await quality.open_inspection(
        session, sap_code=str(data["SAP_Code"]).strip(), material_code=None,
        lot=(data.get("Lot_Number") or None), qty=float(data["Quantity"]),
        source_type="site_receipt", source_ref=str(pending_id),
        site_id=data["Site_ID"], mtc_document_id=mtc_id, created_by=actor)


_mtc_t = ledger._MD.tables["mtc_documents"]


async def _warn_if_uncertified(session, *, data: dict, mtc_id: Optional[int],
                               actor: str) -> None:
    """A Surface Shield was just received with no certificate covering it.

    The 2026-08-12 counterpart to dropping the receipt block. Nothing is
    refused; Logistics is told, because they are the ones who can get the
    document from the supplier and the material cannot be issued until they
    do. Runs INSIDE the receipt transaction — the notification is part of
    the receipt being recorded, not a best-effort afterthought.

    The `visible_mtc` check first is what stops this being a nag: if the site
    already inherits a certificate from the purchase order or the delivery
    note, the paperwork exists and nobody needs chasing.
    """
    if mtc_id:
        return
    sap = str(data["SAP_Code"]).strip()
    site = data.get("Site_ID")
    if await quality.visible_mtc(session, sap_code=sap, site_id=site):
        return
    await quality.warn_mtc_missing(
        session, sap_code=sap, material_code=None,
        where="a site goods receipt", site_id=site, created_by=actor)


async def _alert_mtc_missing(session, exc: HTTPException, actor: str) -> None:
    """Phase 7b — the parked 'missing-MTC → Logistics email' follow-up.

    ⚠️ Retargeted 2026-08-12. It used to fire when the MTC gate blocked a
    RECEIPT; receipts are no longer blocked, so it now fires when the gate
    blocks an ISSUE. That is the same event in business terms — a Surface
    Shield is stuck for want of a certificate and only Logistics can unstick
    it — and it is the moment somebody is actually standing there unable to
    work, so it is if anything the better trigger.

    Best-effort (post-rollback) — never changes the 422 the store keeper sees.
    """
    if exc.status_code != 422 or "Material Test Certificate" not in str(exc.detail):
        return
    try:
        await session.rollback()   # the begin() block already rolled back; be safe
        await emailer.send_email(
            session, to=emailer.logistics_to(),
            subject="MTC missing — issue to the field blocked",
            body=(f"A material issue was blocked because no Material Test Certificate "
                  f"is on file for the material at that site.\n\nDetail: {exc.detail}\n"
                  f"Attempted by: {actor}\n\n"
                  f"Please obtain the MTC from the supplier and attach it to the "
                  f"purchase order — the site will inherit it automatically."),
            event_key="mtc_missing", related_table="pending_issues",
            created_by=actor)
        await session.commit()
    except Exception:  # noqa: BLE001 — alerting must never mask the 422
        await session.rollback()


class ConsumptionIn(BaseModel):
    Date: str
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Work_Type: Optional[str] = None
    Issued_To: Optional[str] = None
    Issued_By: Optional[str] = None
    PR_Number: Optional[str] = None
    Tank_No: Optional[str] = None
    Serial_No: Optional[str] = None
    Remarks: Optional[str] = None
    Requested_By: Optional[str] = None
    Lot_Number: Optional[str] = Field(None, description="explicit lot; blank → FEFO auto-pick")
    FEFO_Override: Optional[str] = None
    wbs: Optional[str] = Field(None, description="WBS Number (required once the site has active WBS)")
    attachment_ids: list[int] = Field(default_factory=list)
    # QSEP slice 4 — the PPE half of the ORDINARY issue form (Option A).
    # Optional here and mandatory in services/ppe.py, because whether they
    # are required depends on the material the user picked: making them
    # required in the schema would break every non-PPE issue in the product.
    employee_id_number: Optional[str] = Field(
        None, description="PPE only: who is receiving it (employees.ID_Number)")
    safety_doc_id: Optional[int] = Field(
        None, description="PPE only: entry_attachments id, doc_type='safety_approval'")
    early_reason: Optional[str] = Field(
        None, description="PPE only: mandatory when replacing gear that has not expired")


class ReturnIn(BaseModel):
    Date: str
    SAP_Code: str
    Quantity: float = Field(..., gt=0)
    Site_ID: str
    Reason: Optional[str] = None
    Remarks: Optional[str] = None
    # Parity A2 — legacy return gates (enforced when require_entry_documents is on)
    Return_DN_No: Optional[str] = None
    source_receipt_id: Optional[int] = Field(
        None, description="the receipt being returned against (30-day window)")
    override_reason: Optional[str] = Field(
        None, description="justification when returning against a receipt older than 30 days")
    attachment_ids: list[int] = Field(default_factory=list)
    # Track 4 — the QC rejection this return discharges. When set, the return
    # is capped at the rejected quantity, the DN and the document become
    # mandatory REGARDLESS of require_entry_documents, and the inspection is
    # marked as discharged so the same rejection cannot be returned twice.
    qc_return_no: Optional[str] = Field(
        None, description="Return No from a QC rejection (QCR-YYYYMMDD-<id>)")


class AdjustmentIn(BaseModel):
    SAP_Code: str
    Site_ID: str
    system_qty: float = Field(..., description="on-system qty")
    counted_qty: float = Field(..., description="physically counted qty")
    reason_code: str
    notes: Optional[str] = None
    Lot_Number: Optional[str] = Field(None, description="set → dispose this lot")


@router.post("/receipts", status_code=201, summary="Submit a goods receipt for HOD approval")
async def create_receipt(
    body: ReceiptIn = Body(...),
    user: dict = Depends(require_roles("store_keeper")),
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = IdemKey,
):
    if body.extra:
        bad = [k for k in body.extra if k not in _RECEIPT_EXTRA_OK]
        if bad:
            raise HTTPException(422, f"unknown/for-bidden receipt columns: {bad}")

    data = body.model_dump()
    try:
        async with session.begin():
            prior = await _idem_claim(session, idempotency_key, "entry_receipt", body, user)
            if prior is not None:
                return prior
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            # Parity A4/A1 — WBS (when the site has any) + supporting document
            await entry_docs.assert_wbs(session, site_id=body.Site_ID, wbs=body.wbs)
            doc_ids = await entry_docs.assert_entry_docs(
                session, doc_type="receipt", attachment_ids=body.attachment_ids,
                username=user["username"])
            mtc_id = await _apply_receipt_guards(session, data)  # MTC gate + UoM convert
            result = await ledger.stage_receipt(session, username=user["username"], data=data)
            await _link_mtc(session, mtc_id, result.get("pending_id"))
            await _open_site_inspection(session, data=data, mtc_id=mtc_id,
                                        pending_id=result.get("pending_id"),
                                        actor=user["username"])
            await _warn_if_uncertified(session, data=data, mtc_id=mtc_id,
                                       actor=user["username"])
            await entry_docs.link_attachments(session, doc_ids,
                                              entry_table="pending_receipts",
                                              entry_date=body.Date)
            await _notify_hod_staged(session, kind_label="Receipt", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · qty {data['Quantity']:g} · {body.Site_ID}")
            await _idem_finish(session, idempotency_key, "entry_receipt", user, result)
        return result
    except HTTPException as e:
        await _alert_mtc_missing(session, e, user["username"])
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.post("/consumption", status_code=201, summary="Submit a material issue for HOD approval")
async def create_consumption(
    body: ConsumptionIn = Body(...),
    user: dict = Depends(require_roles("store_keeper")),
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = IdemKey,
):
    try:
        async with session.begin():
            prior = await _idem_claim(session, idempotency_key, "entry_consumption", body, user)
            if prior is not None:
                # A replay: the FEFO-override alert below already went out once.
                return prior
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            # Phase 9a: the WBS gate for an ISSUE moved into `stage_consumption`,
            # so it runs AFTER the work-type map has had its chance to fill the
            # number in. Receipts still assert here — they have no work type to
            # resolve from.
            doc_ids = await entry_docs.assert_entry_docs(
                session, doc_type="consumption", attachment_ids=body.attachment_ids,
                username=user["username"])
            result = await ledger.stage_consumption(session, username=user["username"], data=body.model_dump())
            await entry_docs.link_attachments(session, doc_ids,
                                              entry_table="pending_issues",
                                              entry_date=body.Date)
            await _notify_hod_staged(session, kind_label="Issue", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · qty {body.Quantity:g} · {body.Site_ID}")
            await _idem_finish(session, idempotency_key, "entry_consumption", user, result)
    except HTTPException as e:
        # An issue blocked for want of a certificate emails Logistics, who are
        # the only people who can produce one (2026-08-12).
        await _alert_mtc_missing(session, e, user["username"])
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")

    # FEFO override → alert the site HOD(s), in-app + WhatsApp (critical).
    # Best-effort + post-commit: a messaging failure never fails the issue.
    if body.FEFO_Override:
        try:
            await dispatch(session, event_key="fefo_override", recipient_role="hod",
                           recipient_site=body.Site_ID, severity="critical",
                           wa_template="critical_alert", title="FEFO override",
                           body=(f"{user['username']} bypassed FEFO issuing {body.SAP_Code} "
                                 f"× {body.Quantity:g} at {body.Site_ID} "
                                 f"(staged #{result.get('pending_id')}, pending HOD approval)."),
                           link_page="/hod/approvals", related_table="pending_issues",
                           related_ref=result.get("pending_id"), created_by=user["username"])
            await session.commit()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            await session.rollback()
    return result


@router.post("/returns", status_code=201, summary="Submit a return for HOD approval")
async def create_return(
    body: ReturnIn = Body(...),
    user: dict = Depends(require_roles("store_keeper")),
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = IdemKey,
):
    try:
        async with session.begin():
            prior = await _idem_claim(session, idempotency_key, "entry_return", body, user)
            if prior is not None:
                return prior
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            data = body.model_dump()
            strict = await entry_docs.docs_required(session)

            # ── Track 4: a return discharging a QC rejection ────────────────
            # Resolved BEFORE the ordinary gates because it TIGHTENS them.
            # `require_entry_documents` is an operator convenience switch;
            # sending rejected material back to a vendor without a delivery
            # note is not something a convenience switch should be able to
            # turn off, so this branch demands both regardless of it.
            qc_ref = (body.qc_return_no or "").strip()
            qc_row = None
            if qc_ref:
                qc_row = (await session.execute(text(
                    'SELECT id, "SAP_Code", "Site_ID", "Lot_Number", rejected_qty, '
                    '       return_posted_id, decision_reason '
                    '  FROM qc_inspections WHERE return_no = :r'
                    '   FOR UPDATE'), {"r": qc_ref})).mappings().first()
                if qc_row is None:
                    raise HTTPException(404, f"no QC rejection carries Return No {qc_ref!r}")
                if not site_row_visible(site_scope(user), qc_row.get("Site_ID")):
                    raise HTTPException(404, f"no QC rejection carries Return No {qc_ref!r}")
                # Locked above with FOR UPDATE, so two store keepers posting
                # the same Return No at once serialise here instead of both
                # passing the check and taking the quantity out twice.
                if qc_row.get("return_posted_id"):
                    raise HTTPException(
                        409, f"Return {qc_ref} was already posted "
                             f"(return #{qc_row['return_posted_id']})")
                if str(qc_row["SAP_Code"]).strip() != body.SAP_Code.strip():
                    raise HTTPException(
                        422, f"Return {qc_ref} is for {qc_row['SAP_Code']}, "
                             f"not {body.SAP_Code}")
                if (qc_row.get("Site_ID") or "") != body.Site_ID:
                    raise HTTPException(
                        422, f"Return {qc_ref} was raised at {qc_row.get('Site_ID') or '—'}, "
                             f"not {body.Site_ID}")
                cap = float(qc_row["rejected_qty"] or 0)
                if float(body.Quantity) > cap + 1e-9:
                    raise HTTPException(
                        422, f"Return {qc_ref} covers {cap:g} rejected — cannot "
                             f"return {body.Quantity:g}. Returning LESS is fine.")
                if not (body.Return_DN_No or "").strip():
                    raise HTTPException(
                        422, f"Return {qc_ref} is going back to the supplier — a "
                             "Return DN No. is required even though the entry-document "
                             "setting is off")
                if not body.attachment_ids:
                    raise HTTPException(
                        422, f"attach the signed delivery note for return {qc_ref} — "
                             "rejected material does not leave site undocumented")

            # Parity A2 — the legacy return gates, active with the master
            # require_entry_documents switch: mandatory Return DN No. +
            # attachment; returns are made against a source receipt from the
            # last 30 days (older needs a justification → flagged for HOD).
            if strict and not (body.Return_DN_No or "").strip():
                raise HTTPException(422, "Return DN No. is required")
            doc_ids = await entry_docs.assert_entry_docs(
                session, doc_type="return", attachment_ids=body.attachment_ids,
                username=user["username"])
            if body.source_receipt_id:
                src = (await session.execute(text(
                    'SELECT "Date", "Quantity", "DN_No", "SAP_Code", COALESCE("Site_ID",\'HQ\') AS s '
                    'FROM receipts WHERE id = :i'), {"i": body.source_receipt_id})).first()
                if src is None:
                    raise HTTPException(404, "source receipt not found")
                if src.SAP_Code.strip() != body.SAP_Code.strip() or src.s != body.Site_ID:
                    raise HTTPException(422, "source receipt is for a different material/site")
                if float(body.Quantity) > float(src.Quantity) + 1e-9:
                    raise HTTPException(422, f"return qty {body.Quantity:g} exceeds the "
                                             f"source receipt qty {float(src.Quantity):g}")
                age_days = (dt.date.today()
                            - dt.date.fromisoformat(str(src.Date)[:10])).days
                if age_days > 30 and not (body.override_reason or "").strip():
                    raise HTTPException(422, "receipt is older than 30 days — an override "
                                             "justification is required")
                data["received_date"] = str(src.Date)[:10]
                data["received_dn_no"] = src.DN_No
                data["received_qty"] = float(src.Quantity)
                if age_days <= 30:
                    data["override_reason"] = None   # inside the window — no flag
            elif strict and qc_row is None:
                raise HTTPException(422, "pick the source receipt this return is against")
            # ⚠️ A QC return needs NO source receipt, and that is not a
            # loosening. The source-receipt rule exists to prove a return is
            # against something real and recent; a QC rejection proves that
            # better — it names the material, the site, the lot, the quantity
            # and the inspector, and it is capped by the rejected quantity
            # above. Demanding a receipt on top would also be unsatisfiable
            # for the common case: an inspection raised at a WAREHOUSE has no
            # site receipt to point at, so the store keeper would be told to
            # pick from an empty list. If one IS supplied it is still validated
            # by the branch above; it is optional here, not ignored.
            data.pop("qc_return_no", None)   # not a pending_returns column
            result = await ledger.stage_return(session, username=user["username"], data=data)
            await entry_docs.link_attachments(session, doc_ids,
                                              entry_table="pending_returns",
                                              entry_date=body.Date)
            if qc_row is not None:
                # Discharge the rejection. Written here, at STAGE, rather than
                # when the HOD approves: the goods are physically going back
                # now, and leaving the Return No live through the approval gap
                # is precisely the window in which somebody posts it twice.
                await session.execute(text(
                    "UPDATE qc_inspections SET return_posted_id = :p "
                    " WHERE id = :i"),
                    {"p": result.get("pending_id"), "i": qc_row["id"]})
                await ledger.write_audit(
                    session, user["username"], "QC_RETURN_POSTED", "qc_inspections",
                    f"return_no={qc_ref} inspection={qc_row['id']} "
                    f"{body.SAP_Code} qty={body.Quantity:g} dn={body.Return_DN_No}")
            await _notify_hod_staged(session, kind_label="Return", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · qty {body.Quantity:g} · {body.Site_ID}"
                                            + (f" · QC {qc_ref}" if qc_ref else ""))
            if qc_row is not None:
                result["qc_return_no"] = qc_ref
            await _idem_finish(session, idempotency_key, "entry_return", user, result)
            return result
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/return-sources", summary="Receipts a return can be made against (parity A2)")
async def return_sources(sap: str, site_id: str, days: int = 30,
                         user: dict = Depends(require_roles("store_keeper")),
                         session: AsyncSession = Depends(get_session)):
    """Legacy rule: returns are picked from receipts in the last 30 days
    (365 with the override window). Rows carry qty + DN so the form can cap
    the return quantity.

    ⚠️ THE WINDOW IS MEASURED ON TWO DATES, AND HAS TO BE.

    `receipts."Date"` is the DELIVERY date, typed by the store keeper off the
    vendor's or carrier's paperwork. While people typed today's date it was a
    fair stand-in for "when did this arrive in the system". It stopped being
    one once receipts began flowing in through the DN chain carrying the
    carrier's own date: goods received this morning, dated six weeks ago on
    the document, fell outside a 30-day window that is trying to say "recent".
    That is the reported bug — newly received items missing from the dropdown
    while older ones were listed.

    `posted_at` (alembic c7a93e5d2b18) records when the row entered the
    ledger, which is what the rule actually means. A receipt qualifies on
    EITHER date, so nothing that used to be offered has been taken away.

    `posted_at IS NULL` on every receipt that predates the migration, and
    those fall back to `Date` alone. That is why the OR is written with an
    explicit NULL check rather than a COALESCE to `Date` — the two columns
    have different types (timestamp vs text), and silently casting one into
    the other is how a comparison starts quietly returning false.
    """
    days = 365 if days > 30 else 30
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = (await session.execute(text(
        'SELECT id, "Date", "Quantity", "DN_No", "Supplier", "Lot_Number", posted_at '
        'FROM receipts WHERE TRIM("SAP_Code") = TRIM(:sap) '
        "AND COALESCE(\"Site_ID\",'HQ') = :site "
        'AND ("Date" >= :cutoff '
        '     OR (posted_at IS NOT NULL AND posted_at >= CAST(:cutoff AS timestamp))) '
        # NULLS LAST rather than casting "Date" into a timestamp to sort on
        # one key: `Date` is free text, and a single malformed row would take
        # the whole endpoint down with a cast error. Newly posted receipts
        # sort to the top, which is what the store keeper is looking for;
        # historical rows keep their old Date ordering below them.
        'ORDER BY posted_at DESC NULLS LAST, "Date" DESC, id DESC '
        'LIMIT 100'),
        {"sap": sap, "site": site_id, "cutoff": cutoff})).mappings().all()
    return {"items": [dict(r) for r in rows], "window_days": days}


@router.get("/qc-return/{return_no}", summary="Look up a QC rejection by its Return No (parity Track 4)")
async def qc_return_lookup(return_no: str,
                           user: dict = Depends(require_roles("store_keeper", "hod")),
                           session: AsyncSession = Depends(get_session)):
    """Everything the return form needs, from the number the QC quoted.

    The store keeper types `QCR-20260813-41` and gets back the material, the
    site, the rejected quantity, the lot, the inspector's reason and — where
    it can be resolved — the receipt the goods came in on. They can still
    change any of it before posting; this fills the form, it does not lock it.
    Quantity in particular is capped, not fixed, because a store keeper may
    legitimately send back less than QC rejected (some of it already issued,
    some still being argued about with the vendor).

    SCOPED, and the scoping is the interesting part. A Return No is a
    guessable string — date plus a small integer — so an unscoped lookup would
    be an enumeration oracle over every rejection at every site. It is
    resolved through `site_row_visible`, the same check every other by-id
    fetch uses, and answers 404 rather than 403 on a foreign row so it does
    not confirm that a rejection exists somewhere the caller cannot look.
    """
    ref = (return_no or "").strip()
    if not ref:
        raise HTTPException(422, "give the Return No from the QC rejection")
    row = (await session.execute(text(
        'SELECT i.id, i."SAP_Code", i."Material_Code", i."Site_ID", i."Warehouse_ID", '
        '       i."Lot_Number", i.rejected_qty, i.submitted_qty, i.approved_qty, '
        '       i.decision_reason, i.inspected_by, i.inspected_at, i.status, '
        '       i.return_no, i.return_posted_id, i.source_type, i.source_ref, '
        '       inv."Equipment_Description" AS "Material_Name" '
        '  FROM qc_inspections i '
        '  LEFT JOIN inventory inv ON TRIM(inv."SAP_Code") = TRIM(i."SAP_Code") '
        ' WHERE i.return_no = :r'), {"r": ref})).mappings().first()
    if row is None:
        raise HTTPException(404, f"no QC rejection carries Return No {ref!r}")
    if not site_row_visible(site_scope(user), row.get("Site_ID")):
        raise HTTPException(404, f"no QC rejection carries Return No {ref!r}")
    if row.get("return_posted_id"):
        raise HTTPException(
            409, f"Return {ref} has already been posted (return #{row['return_posted_id']}). "
                 "A Return No covers one return — raise a new one with QC if more "
                 "of this lot is going back.")

    # The source receipt, when the inspection came from one. `source_ref` is
    # the receipt id for a site receipt; warehouse-side inspections reference
    # an assignment/line pair instead and simply have no receipt to point at,
    # which is why this is best-effort rather than required.
    source_receipt_id = None
    if row["source_type"] in ("site_receipt", "dn_receipt"):
        try:
            source_receipt_id = int(str(row["source_ref"]).split(":")[0])
        except (TypeError, ValueError):
            source_receipt_id = None

    return {
        "return_no": row["return_no"],
        "inspection_id": row["id"],
        "SAP_Code": row["SAP_Code"],
        "Material_Code": row["Material_Code"],
        "Material_Name": row["Material_Name"],
        "Site_ID": row["Site_ID"],
        "Lot_Number": row["Lot_Number"],
        # The cap. The form pre-fills this and refuses more (see create_return).
        "rejected_qty": float(row["rejected_qty"] or 0),
        "submitted_qty": float(row["submitted_qty"] or 0),
        "approved_qty": float(row["approved_qty"] or 0),
        "Reason": "defect",
        "decision_reason": row["decision_reason"],
        "inspected_by": row["inspected_by"],
        "inspected_at": str(row["inspected_at"]) if row["inspected_at"] else None,
        "source_receipt_id": source_receipt_id,
    }


@router.post("/adjustments", status_code=201, summary="Submit a stock-count adjustment for HOD approval")
async def create_adjustment(
    body: AdjustmentIn = Body(...),
    user: dict = Depends(require_roles("store_keeper")),
    session: AsyncSession = Depends(get_session),
    idempotency_key: Optional[str] = IdemKey,
):
    if body.reason_code not in ledger.ADJUSTMENT_REASONS:
        raise HTTPException(422, f"unknown reason_code {body.reason_code!r}")
    if abs(body.counted_qty - body.system_qty) < 1e-9:
        raise HTTPException(400, "counted qty matches system qty — no adjustment needed")
    try:
        async with session.begin():
            prior = await _idem_claim(session, idempotency_key, "entry_adjustment", body, user)
            if prior is not None:
                return prior
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            result = await ledger.stage_adjustment(session, username=user["username"], data=body.model_dump())
            variance = body.counted_qty - body.system_qty
            await _notify_hod_staged(session, kind_label="Adjustment", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("id") or result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · variance {variance:+g} · {body.Site_ID}")
            await _idem_finish(session, idempotency_key, "entry_adjustment", user, result)
            return result
    except HTTPException:
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


@router.get("/adjustment-reasons", tags=["data entry"], summary="Reason codes for adjustments")
async def adjustment_reasons(user: dict = Depends(get_current_user)):
    return ledger.ADJUSTMENT_REASONS


@router.get("/receipt-meta/{sap_code}",
            summary="Receipt guards metadata (rubber? base UoM? pack conversions)")
async def receipt_meta(sap_code: str, user: dict = Depends(require_roles("store_keeper")),
                       session: AsyncSession = Depends(get_session)):
    return await _receipt_meta(session, sap_code.strip())


@router.post("/mtc", status_code=201,
             summary="Upload a Material Test Certificate (mandatory for Surface Shields)")
async def upload_mtc(file: UploadFile = File(...), sap_code: str = Form(...),
                     site_id: Optional[str] = Form(None),
                     warehouse_id: Optional[str] = Form(None),
                     material_code: Optional[str] = Form(None),
                     po_item_id: Optional[int] = Form(None),
                     dn_number: Optional[str] = Form(None),
                     mtc_number: Optional[str] = Form(None),
                     lot_number: Optional[str] = Form(None),
                     quantity: Optional[float] = Form(None),
                     user: dict = Depends(require_roles(
                         "store_keeper", "warehouse_user", "logistics")),
                     session: AsyncSession = Depends(get_session)):
    """Attach the certificate. Any of the three roles that touch the chain may.

    Store Keeper, warehouse user and Logistics all upload here, and that is
    the point: after the 2026-08-12 ruling the person who HITS the gate (the
    site SK, at issue) is usually not the person HOLDING the document
    (Logistics, who got it with the PO). Whoever has the PDF files it once
    and the site inherits it — see `quality.visible_mtc`.

    * **Where it is filed.** `site_id` or `warehouse_id`, never both. With
      neither, a scoped caller files it at their own binding; a global caller
      (Logistics) may file it against a `po_item_id` or `dn_number` alone,
      which is how a certificate gets in before anyone knows which site will
      end up with the material.
    * **`material_code`.** `dn_items` carry a Material_Code and no SAP at all,
      so a certificate findable only by SAP is one the DN path can never
      match. It is resolved from the master when not supplied.
    * **`po_item_id` / `dn_number`.** The two links that carry a certificate
      downstream to the site that receives the goods. Filing with neither, and
      no site, is possible only for a warehouse user — that certificate covers
      that warehouse's own stock and reaches a site once a DN ships it.
    """
    blob = await file.read()
    site = wh = None
    dn = (dn_number or "").strip() or None
    if warehouse_id and site_id:
        raise HTTPException(422, "name a site OR a warehouse for this certificate, not both")
    if warehouse_id:
        from .auth import resolve_warehouse_param
        wh = resolve_warehouse_param(user, warehouse_id.strip())
    elif site_id:
        site = resolve_site_write(user, site_id)
    elif po_item_id is not None or dn:
        # Pinned to a PO line or a DN, so the chain says where it belongs and
        # the caller does not have to. A SITE-scoped caller is still pinned to
        # their own site — an unbound one must not slip through by naming a PO.
        site = (resolve_site_write(user, None)
                if site_filter_applies(site_scope(user)) else None)
    else:
        # No place named and nothing to pin it to: fall back to the caller's
        # own binding, which is what a store keeper has always relied on.
        site = resolve_site_write(user, None)
    sap = sap_code.strip()
    mat = (material_code or "").strip() or None
    async with session.begin():
        # Inside the transaction on purpose: a query before `session.begin()`
        # autobegins one and the `begin()` below then raises.
        if dn:
            exists = (await session.execute(text(
                'SELECT 1 FROM delivery_notes WHERE "DN_Number" = :d'), {"d": dn})).first()
            if exists is None:
                raise HTTPException(404, f"delivery note {dn!r} not found")
        if mat is None:
            row = (await session.execute(text(
                'SELECT "Material_Code" FROM inventory '
                'WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'), {"s": sap})).first()
            mat = row[0] if row else None
        mid = (await session.execute(insert(_mtc_t).values(
            Site_ID=site, Warehouse_ID=wh, SAP_Code=sap,
            Material_Code=mat, Material_Code_Ref=mat, mtc_number=mtc_number,
            Lot_Number=lot_number, Quantity=quantity, DN_Number=dn,
            po_item_id=(int(po_item_id) if po_item_id is not None else None),
            file_name=file.filename, mime_type=file.content_type,
            file_blob=blob, status="attached", submitted_by=user["username"]
        ).returning(_mtc_t.c["id"]))).scalar_one()
        # Tell logistics the certificate they were chasing has arrived.
        await dispatch(session, event_key="mtc_uploaded", recipient_role="logistics",
                       wa_template="status_update", severity="success",
                       title=f"MTC uploaded for {sap}",
                       body=(f"{user['username']} attached MTC {mtc_number or '—'} "
                             f"(lot {lot_number or '—'}) at {site or wh}."),
                       link_page="/logistics", related_table="mtc_documents",
                       related_ref=str(mid), created_by=user["username"])
    return {"id": mid, "file_name": file.filename,
            "site_id": site, "warehouse_id": wh, "material_code": mat}


# --- Bulk entry (Phase 1) -----------------------------------------------------
# The SK batches a shift's worth of lines in an editable grid, then submits them
# all at once. Atomic: every row is validated up-front and nothing stages if any
# row is bad (the SK already reviewed the grid). One HOD notification per site.
_BULK_MODEL = {"receipt": ReceiptIn, "consumption": ConsumptionIn, "return": ReturnIn}
_BULK_STAGER = {"receipt": ledger.stage_receipt, "consumption": ledger.stage_consumption,
                "return": ledger.stage_return}
_BULK_LABEL = {"receipt": "Receipt", "consumption": "Issue", "return": "Return"}


class BulkEntryIn(BaseModel):
    kind: Literal["receipt", "consumption", "return"]
    rows: list[dict[str, Any]] = Field(..., min_length=1,
                                       description="one dict per line, shaped like the single-entry body")
    attachment_ids: list[int] = Field(
        default_factory=list,
        description="batch-level supporting documents (gated by require_entry_documents)")


@router.post("/bulk", status_code=201,
             summary="Stage a batch of receipts/issues/returns for HOD approval")
async def create_bulk(body: BulkEntryIn = Body(...),
                      user: dict = Depends(require_roles("store_keeper")),
                      session: AsyncSession = Depends(get_session),
                      idempotency_key: Optional[str] = IdemKey):
    model = _BULK_MODEL[body.kind]
    stager = _BULK_STAGER[body.kind]
    label = _BULK_LABEL[body.kind]
    # Validate all rows first — atomic submit, so a bad row fails the whole batch.
    parsed, errors = [], []
    for i, raw in enumerate(body.rows):
        try:
            parsed.append(model.model_validate(raw))
        except ValidationError as e:
            errors.append({"row": i, "errors": e.errors()})
    if errors:
        raise HTTPException(422, {"message": "some rows are invalid — nothing was staged",
                                  "rows": errors})
    _DOC_TYPE = {"receipt": "receipt", "consumption": "consumption", "return": "return"}
    try:
        staged: list = []
        by_site: dict[str, int] = {}
        async with session.begin():
            prior = await _idem_claim(session, idempotency_key, "entry_bulk", body, user)
            if prior is not None:
                return prior
            # Parity A1/A4 — batch gates: one supporting document covers the
            # whole batch (legacy "Whole entry" scope); WBS checked per row.
            doc_ids = await entry_docs.assert_entry_docs(
                session, doc_type=_DOC_TYPE[body.kind],
                attachment_ids=body.attachment_ids, username=user["username"])
            for i, m in enumerate(parsed):
                if not await ledger.sap_exists(session, m.SAP_Code):
                    raise HTTPException(404, f"row {i}: SAP_Code {m.SAP_Code!r} not in inventory")
                # Consumption asserts inside `stage_consumption`, after the
                # work-type map has resolved a WBS (Phase 9a).
                if body.kind == "receipt":
                    await entry_docs.assert_wbs(session, site_id=m.Site_ID,
                                                wbs=getattr(m, "wbs", None))
            for m in parsed:
                row = m.model_dump()
                # Receipt guards (MTC gate + UoM convert) apply only to receipts.
                mtc_id = await _apply_receipt_guards(session, row) if body.kind == "receipt" else None
                res = await stager(session, username=user["username"], data=row)
                if body.kind == "receipt":
                    await _link_mtc(session, mtc_id, res.get("pending_id"))
                    await _open_site_inspection(session, data=row, mtc_id=mtc_id,
                                                pending_id=res.get("pending_id"),
                                                actor=user["username"])
                    await _warn_if_uncertified(session, data=row, mtc_id=mtc_id,
                                               actor=user["username"])
                staged.append(res.get("pending_id"))
                by_site[m.Site_ID] = by_site.get(m.Site_ID, 0) + 1
            await entry_docs.link_attachments(
                session, doc_ids,
                entry_table={"receipt": "pending_receipts", "consumption": "pending_issues",
                             "return": "pending_returns"}[body.kind],
                entry_date=(parsed[0].Date if hasattr(parsed[0], "Date") else None))
            for site_id, cnt in by_site.items():
                await _notify_hod_staged(
                    session, kind_label=f"{cnt} {label}(s)", site_id=site_id,
                    actor=user["username"], ref=",".join(str(s) for s in staged),
                    detail=f"{cnt} {label.lower()} line(s) batch-submitted")
            out = {"staged": len(staged), "pending_ids": staged, "kind": body.kind}
            await _idem_finish(session, idempotency_key, "entry_bulk", user, out)
        return out
    except HTTPException as e:
        await _alert_mtc_missing(session, e, user["username"])
        raise
    except (IntegrityError, DataError) as e:
        raise HTTPException(400, f"{type(e).__name__}: {e.orig}")


# --- Item snapshot (Phase 1) --------------------------------------------------
# Powers the entry-form "current stock + 30-day trend" panel (legacy
# render_item_snapshot / get_item_snapshot). Numbers are ledger-derived.
@router.get("/snapshot/{sap_code}",
            summary="Current stock + 30-day consumption trend for a material")
async def item_snapshot(sap_code: str, site_id: Optional[str] = None,
                        user: dict = Depends(require_roles("store_keeper")),
                        session: AsyncSession = Depends(get_session)):
    from .ai.submission_stats import usage_stats  # local import (no cycle)
    site_id = resolve_site_param(user, site_id)
    sap = sap_code.strip()

    where, params = 's."SAP_Code" = :sap', {"sap": sap}
    if site_filter_applies(site_id):
        where += ' AND s."Site_ID" = :site'
        params["site"] = site_id
    srow = (await session.execute(text(f'''
        SELECT MAX(s."Equipment_Description") AS descr, MAX(s."UOM") AS uom,
               COALESCE(SUM(s."Current_Stock"), 0) AS current_stock
        FROM ({SQL_SITE_STOCK}) s WHERE {where}'''), params)).mappings().first()

    stats = await usage_stats(session, sap, site_id, 30)

    # 30 zero-filled daily buckets for a clean sparkline.
    base = _dt.date.today() - _dt.timedelta(days=29)
    cwhere = '"SAP_Code" = :sap AND "Date" >= :cut'
    cparams = {"sap": sap, "cut": base.isoformat()}
    # ⚠️ `site_filter_applies`, NOT `if site_id:`. This is the one query in the
    # function that used the truthiness form, while the stock query six lines
    # above already used the helper — the same scope value, two different
    # rules, in one endpoint. `''` is a SCOPED caller with no site of their own
    # and must match nothing; under `if site_id:` the predicate was dropped
    # entirely and the sparkline summed EVERY site's consumption for the
    # material while the stock figure beside it correctly showed zero. A
    # store keeper is only ever site-less through misconfiguration, which is
    # exactly the case scoping has to survive.
    if site_filter_applies(site_id):
        cwhere += ' AND "Site_ID" = :site'
        cparams["site"] = site_id
    crows = (await session.execute(text(
        f'SELECT "Date" AS d, COALESCE(SUM("Quantity"), 0) AS q '
        f'FROM consumption WHERE {cwhere} GROUP BY "Date"'), cparams)).mappings().all()
    daymap = {str(r["d"])[:10]: float(r["q"] or 0) for r in crows}
    trend = [{"date": (base + _dt.timedelta(days=i)).isoformat(),
              "consumed": round(daymap.get((base + _dt.timedelta(days=i)).isoformat(), 0.0), 3)}
             for i in range(30)]

    current = float((srow or {}).get("current_stock") or 0)
    mean_daily = stats["mean_daily_qty"]
    return {
        "sap_code": sap, "site_id": site_id,
        "description": (srow or {}).get("descr"), "uom": (srow or {}).get("uom"),
        "current_stock": current,
        "mean_daily_qty": mean_daily, "total_30d": stats["total_qty"],
        "issues_30d": stats["issues"],
        "days_cover": round(current / mean_daily, 1) if mean_daily > 0 else None,
        "trend": trend,
    }


# --- Store-keeper toolbox (Phase 4) -------------------------------------------
# Count sheet → variance → staged adjustments · bin locations · returnables.
import datetime as _dt  # noqa: E402
import re as _re  # noqa: E402

from sqlalchemy import func, insert, select, text, update  # noqa: E402

_returnables_t = ledger._MD.tables["returnable_items"]


@router.get("/count-sheet", summary="Site stock list for a physical count")
async def count_sheet(site_id: Optional[str] = None,
                      user: dict = Depends(require_roles("store_keeper")),
                      session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
    where, params = "1=1", {}
    if site_id:
        where = 's."Site_ID" = :site'
        params["site"] = site_id
    rows = (await session.execute(text(f'''
        SELECT s."SAP_Code", s."Site_ID", s."Equipment_Description", s."UOM",
               s."Current_Stock" AS "System_Qty"
        FROM ({SQL_SITE_STOCK}) s WHERE {where}
        ORDER BY s."SAP_Code"'''), params)).mappings().all()
    return {"items": [dict(r) for r in rows]}


class CountRowIn(BaseModel):
    SAP_Code: str
    counted_qty: float = Field(..., ge=0)
    reason_code: Optional[str] = None
    notes: Optional[str] = None


class CountSheetIn(BaseModel):
    site_id: str
    reason_code: str = "cycle_count"
    rows: list[CountRowIn]


@router.post("/count-sheet", status_code=201,
             summary="Stage adjustments for every counted variance")
async def submit_count(body: CountSheetIn = Body(...),
                       user: dict = Depends(require_roles("store_keeper")),
                       session: AsyncSession = Depends(get_session)):
    if not body.rows:
        raise HTTPException(422, "provide at least one counted row")
    if body.reason_code not in ledger.ADJUSTMENT_REASONS:
        raise HTTPException(422, f"unknown reason_code {body.reason_code!r}")
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id is required")
    # System quantities in one query — the same derived view the count is against.
    sysmap = {r["SAP_Code"]: float(r["System_Qty"] or 0)
              for r in (await count_sheet(site_id=site, user=user, session=session))["items"]}
    staged, skipped = [], 0
    async with session.begin():
        for row in body.rows:
            sap = row.SAP_Code.strip()
            if sap not in sysmap:
                raise HTTPException(404, f"SAP_Code {sap!r} has no stock row at {site}")
            system_qty = sysmap[sap]
            if abs(row.counted_qty - system_qty) < 1e-9:
                skipped += 1
                continue
            rc = row.reason_code or body.reason_code
            if rc not in ledger.ADJUSTMENT_REASONS:
                raise HTTPException(422, f"unknown reason_code {rc!r}")
            res = await ledger.stage_adjustment(session, username=user["username"], data={
                "SAP_Code": sap, "Site_ID": site, "system_qty": system_qty,
                "counted_qty": row.counted_qty, "reason_code": rc,
                "notes": row.notes or "stock count"})
            staged.append(res.get("pending_id"))
        if staged:
            await dispatch(session, event_key="entry_staged", recipient_role="hod",
                           recipient_site=site, severity="warning", wa_template="action_required",
                           title=f"Stock count staged {len(staged)} adjustment(s)",
                           body=f"Physical count by {user['username']} at {site}.",
                           link_page="/hod/approvals", related_table="stock_adjustments",
                           related_ref=",".join(str(s) for s in staged),
                           created_by=user["username"])
    return {"staged": len(staged), "unchanged": skipped}


@router.get("/bins/{sap_code}", summary="Bin locations an item was put away in (recent first)")
async def bin_locations(sap_code: str, site_id: Optional[str] = None,
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"bins": []}
    where = '''TRIM("SAP_Code") = :sap AND COALESCE(TRIM("Bin_Location"), '') <> ''"'''.rstrip('"')
    params = {"sap": sap_code.strip()}
    if site_id:
        where += ' AND COALESCE("Site_ID", \'HQ\') = :site'
        params["site"] = site_id
    rows = (await session.execute(text(
        f'SELECT "Bin_Location" FROM receipts WHERE {where} ORDER BY id DESC LIMIT 50'
    ), params)).scalars().all()
    seen, out = set(), []
    for b in rows:
        if b not in seen:
            seen.add(b)
            out.append(b)
        if len(out) >= 5:
            break
    return {"bins": out}


# --- Returnable items (tool loans) ---------------------------------------------
class ReturnableIn(BaseModel):
    material_name: str
    borrower_name: str
    expected_return_time: str = Field(..., description="ISO datetime the tool is due back")
    qty: float = Field(1, gt=0)
    uom: Optional[str] = None
    borrower_phone: Optional[str] = None
    site_id: Optional[str] = None
    # Smart-Scan adoption audit (legacy parity): set when the borrower was
    # identified by a badge scan and/or the tool by the vision model — the
    # columns existed but were never written by the v2 create.
    cv_employee_id: Optional[str] = Field(None, description="badge-scanned ID_Number")
    cv_tool_class: Optional[str] = Field(None, description="vision-identified tool name")
    cv_confidence: Optional[float] = Field(None, ge=0, le=1)
    # Phase 18 Track 3 — what was lent, so a return scan can find the loan.
    sap_code: Optional[str] = Field(None, max_length=60,
                                    description="inventory SAP_Code of the tool")
    item_ref: Optional[str] = Field(None, max_length=120,
                                    description="the exact code scanned: serial, asset tag, sticker")


RETURN_CONDITIONS = ("ok", "damaged", "incomplete")


# ⚠️ NOT `ReturnIn`: that name is the STOCK-return body above (line ~308), and
# a second class of the same name here silently replaced it module-wide — the
# bulk endpoint resolves `_BULK_MODEL` at call time and 500'd (Phase 18, caught
# by E2E W1c). A loan return and a stock return are different things.
class LoanReturnIn(BaseModel):
    condition: Literal["ok", "damaged", "incomplete"] = "ok"
    note: Optional[str] = Field(None, max_length=500)


class LoanReturnBatchIn(LoanReturnIn):
    ids: list[int] = Field(..., min_length=1, max_length=50)


def _local_now() -> _dt.datetime:
    """Now, as naive LOCAL time — the clock every loan column is written in.

    ⚠️ PHASE 18 FIX. `given_time` is the database's CURRENT_TIMESTAMP and
    `expected_return_time` goes through `_parse_dt` (naive local), but the list
    and the nav badge compared them with `datetime.now(timezone.utc)` stripped
    of its zone — UTC wall-clock. On a UTC+3 site every loan turned OVERDUE
    three hours late, on this page, in the overdue alert and in the badge
    count, while the health monitor (local) disagreed with all three."""
    return _dt.datetime.now()


def _parse_dt(raw: str) -> _dt.datetime:
    """ISO datetime → naive LOCAL time (what the whole ledger stores).

    Timezone-aware inputs (…Z / +00:00) are converted to the server's local
    zone BEFORE the tzinfo is stripped — the old `.replace(tzinfo=None)` kept
    the UTC wall-clock and made every due-back time show 3 h early next to the
    local `given_time` (UAT bug). Naive inputs are taken as already-local."""
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "expected_return_time must be ISO format")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@router.get("/returnables", summary="Tool loans (overdue first-notified once)")
async def list_returnables(status: Optional[str] = None, site_id: Optional[str] = None,
                           user: dict = Depends(require_roles("store_keeper")),
                           session: AsyncSession = Depends(get_session)):
    site_id = resolve_site_param(user, site_id)
    if site_id == "":
        return {"items": []}
    t = _returnables_t
    now = _local_now()

    # One-time overdue notifications, deduped via whatsapp_alert_sent (legacy flag).
    od = select(t.c["id"], t.c["material_name"], t.c["borrower_name"], t.c["Site_ID"],
                t.c["borrower_phone"]).where(
        t.c["status"] == "borrowed", t.c["expected_return_time"] < now,
        func.coalesce(t.c["whatsapp_alert_sent"], 0) == 0)
    if site_id:
        od = od.where(t.c["Site_ID"] == site_id)
    overdue_rows = (await session.execute(od)).all()
    for r in overdue_rows:
        await dispatch(session, event_key="returnable_overdue", recipient_role="store_keeper",
                       recipient_site=r.Site_ID, severity="warning", wa_template="critical_alert",
                       title=f"Tool overdue: {r.material_name}",
                       body=f"Borrowed by {r.borrower_name} — past its expected return time.",
                       link_page="/entry/returnables", related_table="returnable_items",
                       related_ref=str(r.id))
        # Also chase the borrower directly when we have their number.
        if r.borrower_phone and wa.enabled():
            try:
                await wa.send_template(
                    session, to=r.borrower_phone, template_key="critical_alert",
                    variables=[f"Tool overdue: {r.material_name}",
                               "This item is past its expected return time — "
                               "please return it to the store."],
                    event_key="returnable_overdue", related_table="returnable_items",
                    related_ref=str(r.id))
            except Exception:  # noqa: BLE001 — never break the list endpoint
                pass
        await session.execute(update(t).where(t.c["id"] == r.id).values(whatsapp_alert_sent=1))
    if overdue_rows:
        await session.commit()

    stmt = select(t)
    if status:
        stmt = stmt.where(t.c["status"] == status)
    if site_id:
        stmt = stmt.where(t.c["Site_ID"] == site_id)
    rows = (await session.execute(stmt.order_by(t.c["id"].desc()).limit(500))).mappings().all()
    return {"items": [dict(r) for r in rows], "now": now.isoformat()}


@router.post("/returnables", status_code=201, summary="Loan a tool to an employee")
async def create_returnable(body: ReturnableIn = Body(...),
                            user: dict = Depends(require_roles("store_keeper")),
                            session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, body.site_id)
    if not site:
        raise HTTPException(422, "site_id is required")
    due = _parse_dt(body.expected_return_time)
    async with session.begin():
        rid = (await session.execute(insert(_returnables_t).values(
            material_name=body.material_name.strip(), uom=body.uom, qty=body.qty,
            borrower_name=body.borrower_name.strip(), borrower_phone=body.borrower_phone,
            expected_return_time=due, status="borrowed", Site_ID=site,
            whatsapp_alert_sent=0,
            cv_detected=1 if (body.cv_employee_id or body.cv_tool_class) else 0,
            cv_employee_id=(body.cv_employee_id or None),
            cv_tool_class=(body.cv_tool_class or None),
            cv_confidence=body.cv_confidence,
            SAP_Code=((body.sap_code or "").strip() or None),
            Item_Ref=((body.item_ref or "").strip() or None),
        ).returning(_returnables_t.c["id"]))).scalar_one()
        await ledger.write_audit(session, user["username"], "RETURNABLE_LOAN",
                                 "returnable_items",
                                 f"id={rid} {body.material_name} → {body.borrower_name} due {due}")
        # In-app trail for the site's store keepers (the loan ledger owners).
        await notify(session, event_key="loan_created", recipient_role="store_keeper",
                     recipient_site=site, title=f"Tool loaned: {body.material_name.strip()}",
                     body=(f"{body.borrower_name.strip()} borrowed qty {body.qty:g}"
                           f"{' ' + body.uom if body.uom else ''} — due back "
                           f"{due.strftime('%Y-%m-%d %H:%M')}."),
                     link_page="/entry/returnables", related_table="returnable_items",
                     related_ref=str(rid))
    # WhatsApp the BORROWER (the receiver) directly — best-effort, post-commit.
    if body.borrower_phone and wa.enabled():
        try:
            await wa.send_template(
                session, to=body.borrower_phone, template_key="status_update",
                variables=[f"Tool loaned to you: {body.material_name.strip()}",
                           (f"Qty {body.qty:g}{' ' + body.uom if body.uom else ''} from "
                            f"{site} store — please return by {due.strftime('%Y-%m-%d %H:%M')}.")],
                event_key="loan_created", related_table="returnable_items",
                related_ref=str(rid), created_by=user["username"])
            await session.commit()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            await session.rollback()
    return {"created": True, "id": rid}


_CONDITION_WORDS = {"ok": "in good order", "damaged": "DAMAGED",
                    "incomplete": "INCOMPLETE (parts missing)"}


async def _return_one(session: AsyncSession, user: dict, rid: int,
                      body: "LoanReturnIn") -> dict:
    """Close one loan inside the caller's transaction. Raises HTTPException.

    Records WHEN (local), WHO received it and in WHAT condition (Phase 18). A
    damaged or incomplete return also tells the site's HOD — it is the one
    return somebody has to act on (repair, replace, charge back)."""
    t = _returnables_t
    row = (await session.execute(select(
        t.c["Site_ID"], t.c["status"], t.c["material_name"],
        t.c["borrower_name"], t.c["borrower_phone"],
    ).where(t.c["id"] == rid))).first()
    if row is None:
        raise HTTPException(404, f"returnable {rid} not found")
    scope = resolve_site_param(user, None)
    if not site_row_visible(scope, row.Site_ID):
        raise HTTPException(403, "this loan belongs to another site")
    if row.status == "returned":
        raise HTTPException(409, "already returned")
    note = (body.note or "").strip() or None
    await session.execute(update(t).where(t.c["id"] == rid).values(
        status="returned", returned_time=_local_now(), returned_by=user["username"],
        return_condition=body.condition, return_note=note))
    await ledger.write_audit(session, user["username"], "RETURNABLE_RETURN",
                             "returnable_items",
                             f"id={rid} condition={body.condition}"
                             + (f" note={note}" if note else ""))
    cond = _CONDITION_WORDS[body.condition]
    await notify(session, event_key="loan_returned", recipient_role="store_keeper",
                 recipient_site=row.Site_ID,
                 severity="success" if body.condition == "ok" else "warning",
                 title=f"Tool returned: {row.material_name}",
                 body=(f"{row.borrower_name} returned it {cond} — confirmed by "
                       f"{user['username']}." + (f" Note: {note}" if note else "")),
                 link_page="/entry/returnables", related_table="returnable_items",
                 related_ref=str(rid))
    if body.condition != "ok":
        await notify(session, event_key="loan_returned_damaged", recipient_role="hod",
                     recipient_site=row.Site_ID, severity="warning",
                     title=f"Tool came back {body.condition}: {row.material_name}",
                     body=(f"Borrowed by {row.borrower_name}; received by "
                           f"{user['username']}." + (f" Note: {note}" if note else "")),
                     link_page="/entry/returnables", related_table="returnable_items",
                     related_ref=str(rid))
    return {"id": rid, "material_name": row.material_name,
            "borrower_name": row.borrower_name, "borrower_phone": row.borrower_phone,
            "Site_ID": row.Site_ID}


async def _confirm_to_borrowers(session: AsyncSession, user: dict, done: list[dict]) -> None:
    """WhatsApp each borrower their return — best-effort, post-commit."""
    if not wa.enabled():
        return
    for d in done:
        if not d.get("borrower_phone"):
            continue
        try:
            await wa.send_template(
                session, to=d["borrower_phone"], template_key="status_update",
                variables=[f"Tool return confirmed: {d['material_name']}",
                           f"Received back at {d['Site_ID'] or 'the'} store. Thank you."],
                event_key="loan_returned", related_table="returnable_items",
                related_ref=str(d["id"]), created_by=user["username"])
            await session.commit()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            await session.rollback()


@router.post("/returnables/{rid}/return", summary="Mark a loaned tool as returned")
async def mark_returned(rid: int, body: Optional[LoanReturnIn] = Body(None),
                        user: dict = Depends(require_roles("store_keeper")),
                        session: AsyncSession = Depends(get_session)):
    # The body is OPTIONAL so every existing caller (no body) still works and
    # means "returned in good order", exactly as before Phase 18.
    body = body or LoanReturnIn()
    async with session.begin():
        done = await _return_one(session, user, rid, body)
    await _confirm_to_borrowers(session, user, [done])
    return {"returned": True, "id": rid, "condition": body.condition}


@router.post("/returnables/return-batch",
             summary="Return several loans at once (a borrower's whole kit)")
async def return_batch(body: LoanReturnBatchIn = Body(...),
                       user: dict = Depends(require_roles("store_keeper")),
                       session: AsyncSession = Depends(get_session)):
    """One scan of a badge finds everything that person has out; one press
    returns the lot. ⚠️ A loan that cannot be returned (another site's, already
    back, unknown) is SKIPPED with its reason rather than failing the others —
    the tools on the counter are back whatever the ledger thinks of one id."""
    done: list[dict] = []
    skipped: list[dict] = []
    async with session.begin():
        for rid in dict.fromkeys(body.ids):            # de-duplicated, ordered
            try:
                async with session.begin_nested():
                    done.append(await _return_one(session, user, rid, body))
            except HTTPException as e:
                skipped.append({"id": rid, "status": e.status_code, "reason": e.detail})
    await _confirm_to_borrowers(session, user, done)
    return {"returned": [d["id"] for d in done], "skipped": skipped,
            "condition": body.condition}


# ⚠️ "#123" is the only loan-id syntax: bare digits are badge IDs (10-digit
# Iqama numbers) and SAP codes ("1001"), and guessing between them would return
# the wrong person's tool.
_LOAN_ID_RX = _re.compile(r"^\s*#\s*(\d{1,9})\s*$")


def _norm_code(v: Optional[str]) -> str:
    return "".join((v or "").split()).upper()


@router.get("/returnables/resolve",
            summary="A scan at the return desk → the open loans it names")
async def resolve_scan(code: str, site_id: Optional[str] = None,
                       user: dict = Depends(require_roles("store_keeper")),
                       session: AsyncSession = Depends(get_session)):
    """Turn one scanned or typed code into the loans it is about (Phase 18).

    Tried in order, most specific first:

      `loan`      "#123" — the loan id itself
      `item`      the code matches an OPEN loan's Item_Ref or SAP_Code (a tool's
                  sticker, serial or asset tag)
      `employee`  an employee badge — every open loan of that person
      `material`  an inventory item / registered unit with NO open loan here:
                  nothing to return, but the Loan form can use it
      `none`

    Site-scoped like every loan endpoint, and the badge lookup hides another
    site's employees exactly as `/ai/badge` does.
    """
    from .ai.router import verify_badge
    from .stock import _resolve_material, _scan_tokens

    site = resolve_site_param(user, site_id)
    raw = (code or "").strip()
    out: dict = {"code": raw, "kind": "none", "loans": [], "employee": None,
                 "material": None, "message": ""}
    if not raw or site == "":
        out["message"] = "Nothing to look up."
        return out
    t = _returnables_t
    open_q = select(t).where(t.c["status"] == "borrowed")
    if site:
        open_q = open_q.where(t.c["Site_ID"] == site)

    async def loans(*conds) -> list[dict]:
        rows = (await session.execute(open_q.where(*conds).order_by(
            t.c["expected_return_time"].asc().nulls_last(), t.c["id"]))).mappings().all()
        return [dict(r) for r in rows]

    m = _LOAN_ID_RX.match(raw)
    if m:
        found = await loans(t.c["id"] == int(m.group(1)))
        out.update(kind="loan" if found else "none", loans=found,
                   message="" if found else f"No open loan #{m.group(1)} at this site.")
        return out

    tokens = list(dict.fromkeys(_norm_code(x) for x in _scan_tokens(raw) if x.strip()))
    norm_ref = func.upper(func.replace(func.coalesce(t.c["Item_Ref"], ""), " ", ""))
    norm_sap = func.upper(func.replace(func.coalesce(t.c["SAP_Code"], ""), " ", ""))
    for tok in tokens:                       # the exact physical item first
        found = await loans(norm_ref == tok)
        if found:
            out.update(kind="item", loans=found)
            return out
    for tok in tokens:                       # then any loan of that SAP
        found = await loans(norm_sap == tok)
        if found:
            out.update(kind="item", loans=found)
            return out

    badge = await verify_badge(raw, user=user, session=session)
    if badge.get("found"):
        name = (badge.get("name") or "").strip().lower()
        found = await loans((func.trim(func.coalesce(t.c["cv_employee_id"], "")) == badge["id_number"])
                            | (func.lower(func.trim(func.coalesce(t.c["borrower_name"], ""))) == name))
        out.update(kind="employee", loans=found,
                   employee={k: badge.get(k) for k in ("id_number", "name", "phone",
                                                       "department", "active")},
                   message="" if found else f"{badge['name']} has nothing on loan here.")
        return out

    mat = await _resolve_material(session, raw)
    if mat is not None:
        out.update(kind="material", material={
            "SAP_Code": mat["SAP_Code"], "description": mat["Equipment_Description"],
            "uom": mat["UOM"], "item_ref": raw},
            message=f"{mat['Equipment_Description'] or mat['SAP_Code']} is not on loan here.")
        return out
    unit_t = ledger._MD.tables["asset_units"]
    uq = select(unit_t.c["SAP_Code"], unit_t.c["serial_no"], unit_t.c["asset_tag"]).where(
        (func.trim(unit_t.c["serial_no"]) == raw) | (func.trim(unit_t.c["asset_tag"]) == raw))
    if site:
        uq = uq.where(unit_t.c["Site_ID"] == site)
    unit = (await session.execute(uq.limit(1))).first()
    if unit is not None:
        mat = await _resolve_material(session, unit.SAP_Code or "")
        out.update(kind="material", material={
            "SAP_Code": unit.SAP_Code,
            "description": (mat["Equipment_Description"] if mat else None),
            "uom": (mat["UOM"] if mat else None), "item_ref": raw},
            message="That unit is not on loan here.")
        return out
    out["message"] = f"Nothing matches {raw!r} — not a loan, a badge, or an item."
    return out


# ─── Surface-Shields issue workflow (2026-07-18) ─────────────────────────────
# The SK must pick a lining SYSTEM before issuing a Surface Shields item: the
# system's recipe (joined by sme_recipe.SAP_Code — alembic b3f2a9c47d18)
# filters the material picker, and Done vs Pending SQM for the site is shown
# alongside. Read-only helper; enforcement lives in the Issue form and the
# recipes are the CNCEC RL/BL prediction project's demand model
# (demand = For_1_SQM × SQM, joined on Lining_System_Code).

@router.get("/lining-systems",
            summary="Lining systems: recipe SAPs + site SQM progress")
async def lining_systems(site_id: Optional[str] = None,
                         user: dict = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    from .sme_engine import syscode_sort_key
    from .services.ledger import _MD
    rec_t = _MD.tables["sme_recipe"]
    rows = (await session.execute(
        select(rec_t.c["Lining_System_Code"], rec_t.c["Lining_System_Name"],
               rec_t.c["Substrate"], rec_t.c["Lining_System"],
               rec_t.c["SAP_Code"], rec_t.c["Material_Code"],
               rec_t.c["Material_Name"], rec_t.c["Material_Description"],
               rec_t.c["UOM"], rec_t.c["For_1_SQM"])
        .order_by(rec_t.c["id"]))).mappings().all()

    site = resolve_site_param(user, site_id)
    sqm_sql = '''
        SELECT TRIM(e."Lining_System_Code") AS code,
               COUNT(*) AS units,
               SUM(COALESCE(p."Original_SQM", e."Surface_Area_SQM", 0)) AS original,
               SUM(COALESCE(p."Done_SQM", 0) + COALESCE(p."Done_SQM_staged", 0)) AS done
        FROM sme_equipment e
        LEFT JOIN sme_sqm_progress p
          ON p."Site_ID" = e."Site_ID"
         AND p."Equipment_Tag_No" = e."Equipment_Tag_No"
         AND p."Lining_System_Code" = e."Lining_System_Code"
        {w} GROUP BY 1'''
    if site is not None:
        sqm_rows = (await session.execute(
            text(sqm_sql.format(w='WHERE e."Site_ID" = :site')),
            {"site": site})).all()
    else:
        sqm_rows = (await session.execute(text(sqm_sql.format(w="")))).all()
    sqm = {r.code: {"equipment_count": int(r.units),
                    "original_sqm": round(float(r.original or 0), 2),
                    "done_sqm": round(float(r.done or 0), 2),
                    "pending_sqm": round(max(float(r.original or 0)
                                             - float(r.done or 0), 0.0), 2)}
           for r in sqm_rows}

    # Phase 15d: the surface-prep (Garnet) codes stay listed — the Issue form
    # needs them, because every Surface Shield is issued against a code and
    # Garnet's is ESC1/ESC2 — but FLAGGED, so the Smart Calculator (an
    # estimator) leaves them out (Q15-6) and nothing reads them as lining.
    from .services import prep as PR
    prep = await PR.prep_codes(session)
    systems: dict[str, dict] = {}
    sap_index: dict[str, list[str]] = {}
    for r in rows:
        code = str(r["Lining_System_Code"] or "").strip()
        if not code:
            continue
        s = systems.setdefault(code, {
            "code": code, "prep": code in prep,
            "short_name": (r["Lining_System_Name"] or "").strip(),
            "substrate": (r["Substrate"] or "").strip(),
            "lining_system": (r["Lining_System"] or "").strip(),
            "materials": [], "saps": []})
        sap = str(r["SAP_Code"] or "").strip() or None
        s["materials"].append({
            "sap": sap, "material_code": r["Material_Code"],
            "name": (r["Material_Name"] or "").strip(),
            "description": (r["Material_Description"] or "").strip(),
            "uom": (r["UOM"] or "").strip(),
            "for_1_sqm": float(r["For_1_SQM"] or 0)})
        if sap:
            if sap not in s["saps"]:
                s["saps"].append(sap)
            codes = sap_index.setdefault(sap, [])
            if code not in codes:
                codes.append(code)
    for code, s in systems.items():
        s["sqm"] = sqm.get(code, {"equipment_count": 0, "original_sqm": 0.0,
                                  "done_sqm": 0.0, "pending_sqm": 0.0})
    ordered = [systems[c] for c in sorted(systems, key=syscode_sort_key)]
    return {"systems": ordered, "sap_index": sap_index,
            "site": site if site is not None else "all"}
