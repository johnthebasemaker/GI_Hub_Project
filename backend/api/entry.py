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

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import LargeBinary, insert, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_roles, resolve_site_param
from .db import get_session
from . import entry_docs
from .services import emailer
from .services import ledger
from .services import whatsapp as wa
from .services.notifications import dispatch, notify
from .stock import SQL_SITE_STOCK

router = APIRouter(prefix="/entry", tags=["data entry"])


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
    entry_uom: Optional[str] = Field(None, description="pack UoM the qty is entered in; converted to base")
    mtc_document_id: Optional[int] = Field(None, description="MTC upload id (required for Rubber materials)")
    wbs: Optional[str] = Field(None, description="WBS Number (required once the site has active WBS)")
    Bin_Location: Optional[str] = Field(None, description="Bin / shelf tag (parity B5)")
    attachment_ids: list[int] = Field(default_factory=list,
                                      description="entry_attachments ids (gated by require_entry_documents)")
    extra: Optional[dict[str, Any]] = Field(
        None, description="Optional extra receipts columns (logistics fields)")


# --- receipt entry guards (Phase 6): MTC gate + pack→base UoM conversion -----
async def _mtc_category(session) -> str:
    """Parity A3 — the LEGACY rubber trigger is an exact inventory Category
    match (config.py MTC_REQUIRED_CATEGORY = "Surface Shields"), NOT a
    description token. Configurable via app_settings mtc_required_category."""
    v = (await session.execute(text(
        "SELECT value FROM app_settings WHERE key = 'mtc_required_category'"))).scalar()
    return (v or "Surface Shields").strip()


async def _receipt_meta(session, sap: str) -> dict:
    row = (await session.execute(text(
        'SELECT "UOM", "Category" FROM inventory WHERE TRIM("SAP_Code") = TRIM(:s) LIMIT 1'
    ), {"s": sap})).first()
    base_uom = row[0] if row else None
    is_rubber = bool(row and str(row[1] or "").strip().lower()
                     == (await _mtc_category(session)).lower())
    convs = [dict(m) for m in (await session.execute(text(
        'SELECT "Pack_UOM", "Factor" FROM uom_conversions '
        'WHERE TRIM("SAP_Code") = TRIM(:s) ORDER BY "Pack_UOM"'), {"s": sap})).mappings().all()]
    return {"sap_code": sap, "base_uom": base_uom, "is_rubber": is_rubber, "conversions": convs}


async def _apply_receipt_guards(session, data: dict) -> Optional[int]:
    """Enforce the MTC gate for Rubber materials and convert an entry (pack) UoM
    to the base UoM. Mutates data['Quantity']/['Remarks'] in place; returns the
    mtc_document_id to link post-stage. Raises HTTPException on a failed gate."""
    sap = str(data["SAP_Code"]).strip()
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
    mtc_id = data.get("mtc_document_id")
    if meta["is_rubber"] and not mtc_id:
        raise HTTPException(422, f"{sap} is a Rubber material — an MTC document is required")
    return mtc_id


async def _link_mtc(session, mtc_id: Optional[int], pending_id) -> None:
    if mtc_id and pending_id is not None:
        await session.execute(text(
            "UPDATE mtc_documents SET pending_receipt_id = :pid WHERE id = :mid"),
            {"pid": int(pending_id), "mid": int(mtc_id)})


_mtc_t = ledger._MD.tables["mtc_documents"]


async def _alert_mtc_missing(session, exc: HTTPException, actor: str) -> None:
    """Phase 7b — the parked 'missing-MTC → Logistics email' follow-up: when the
    MTC gate blocks a Rubber receipt, email the logistics inbox so they chase
    the certificate with the supplier. Best-effort (post-rollback) — never
    changes the 422 the store keeper sees."""
    if exc.status_code != 422 or "MTC document is required" not in str(exc.detail):
        return
    try:
        await session.rollback()   # the begin() block already rolled back; be safe
        await emailer.send_email(
            session, to=emailer.logistics_to(),
            subject="MTC missing — Rubber receipt blocked",
            body=(f"A goods receipt was blocked because no Material Test Certificate "
                  f"was attached.\n\nDetail: {exc.detail}\nEntered by: {actor}\n\n"
                  f"Please obtain the MTC from the supplier so the receipt can be re-entered."),
            event_key="mtc_missing", related_table="pending_receipts",
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
):
    if body.extra:
        bad = [k for k in body.extra if k not in _RECEIPT_EXTRA_OK]
        if bad:
            raise HTTPException(422, f"unknown/for-bidden receipt columns: {bad}")

    data = body.model_dump()
    try:
        async with session.begin():
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
            await entry_docs.link_attachments(session, doc_ids,
                                              entry_table="pending_receipts",
                                              entry_date=body.Date)
            await _notify_hod_staged(session, kind_label="Receipt", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · qty {data['Quantity']:g} · {body.Site_ID}")
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
):
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            await entry_docs.assert_wbs(session, site_id=body.Site_ID, wbs=body.wbs)
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
    except HTTPException:
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
):
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            data = body.model_dump()
            strict = await entry_docs.docs_required(session)
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
            elif strict:
                raise HTTPException(422, "pick the source receipt this return is against")
            result = await ledger.stage_return(session, username=user["username"], data=data)
            await entry_docs.link_attachments(session, doc_ids,
                                              entry_table="pending_returns",
                                              entry_date=body.Date)
            await _notify_hod_staged(session, kind_label="Return", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · qty {body.Quantity:g} · {body.Site_ID}")
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
    the return quantity."""
    days = 365 if days > 30 else 30
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = (await session.execute(text(
        'SELECT id, "Date", "Quantity", "DN_No", "Supplier", "Lot_Number" '
        'FROM receipts WHERE TRIM("SAP_Code") = TRIM(:sap) '
        "AND COALESCE(\"Site_ID\",'HQ') = :site AND \"Date\" >= :cutoff "
        'ORDER BY "Date" DESC, id DESC LIMIT 100'),
        {"sap": sap, "site": site_id, "cutoff": cutoff})).mappings().all()
    return {"items": [dict(r) for r in rows], "window_days": days}


@router.post("/adjustments", status_code=201, summary="Submit a stock-count adjustment for HOD approval")
async def create_adjustment(
    body: AdjustmentIn = Body(...),
    user: dict = Depends(require_roles("store_keeper")),
    session: AsyncSession = Depends(get_session),
):
    if body.reason_code not in ledger.ADJUSTMENT_REASONS:
        raise HTTPException(422, f"unknown reason_code {body.reason_code!r}")
    if abs(body.counted_qty - body.system_qty) < 1e-9:
        raise HTTPException(400, "counted qty matches system qty — no adjustment needed")
    try:
        async with session.begin():
            if not await ledger.sap_exists(session, body.SAP_Code):
                raise HTTPException(404, f"SAP_Code {body.SAP_Code!r} not in inventory")
            result = await ledger.stage_adjustment(session, username=user["username"], data=body.model_dump())
            variance = body.counted_qty - body.system_qty
            await _notify_hod_staged(session, kind_label="Adjustment", site_id=body.Site_ID,
                                     actor=user["username"], ref=result.get("id") or result.get("pending_id"),
                                     detail=f"{body.SAP_Code} · variance {variance:+g} · {body.Site_ID}")
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
             summary="Upload a Material Test Certificate (required for Rubber receipts)")
async def upload_mtc(file: UploadFile = File(...), sap_code: str = Form(...),
                     site_id: str = Form(...), mtc_number: Optional[str] = Form(None),
                     lot_number: Optional[str] = Form(None),
                     user: dict = Depends(require_roles("store_keeper")),
                     session: AsyncSession = Depends(get_session)):
    blob = await file.read()
    site = resolve_site_param(user, site_id) or site_id
    async with session.begin():
        mid = (await session.execute(insert(_mtc_t).values(
            Site_ID=site, SAP_Code=sap_code.strip(), mtc_number=mtc_number,
            Lot_Number=lot_number, file_name=file.filename, mime_type=file.content_type,
            file_blob=blob, status="attached", submitted_by=user["username"]
        ).returning(_mtc_t.c["id"]))).scalar_one()
        # Tell logistics the certificate they were chasing has arrived.
        await dispatch(session, event_key="mtc_uploaded", recipient_role="logistics",
                       wa_template="status_update", severity="success",
                       title=f"MTC uploaded for {sap_code.strip()}",
                       body=(f"{user['username']} attached MTC {mtc_number or '—'} "
                             f"(lot {lot_number or '—'}) at {site}."),
                       link_page="/logistics", related_table="mtc_documents",
                       related_ref=str(mid), created_by=user["username"])
    return {"id": mid, "file_name": file.filename}


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
                      session: AsyncSession = Depends(get_session)):
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
            # Parity A1/A4 — batch gates: one supporting document covers the
            # whole batch (legacy "Whole entry" scope); WBS checked per row.
            doc_ids = await entry_docs.assert_entry_docs(
                session, doc_type=_DOC_TYPE[body.kind],
                attachment_ids=body.attachment_ids, username=user["username"])
            for i, m in enumerate(parsed):
                if not await ledger.sap_exists(session, m.SAP_Code):
                    raise HTTPException(404, f"row {i}: SAP_Code {m.SAP_Code!r} not in inventory")
                if body.kind in ("receipt", "consumption"):
                    await entry_docs.assert_wbs(session, site_id=m.Site_ID,
                                                wbs=getattr(m, "wbs", None))
            for m in parsed:
                row = m.model_dump()
                # Receipt guards (MTC gate + UoM convert) apply only to receipts.
                mtc_id = await _apply_receipt_guards(session, row) if body.kind == "receipt" else None
                res = await stager(session, username=user["username"], data=row)
                if body.kind == "receipt":
                    await _link_mtc(session, mtc_id, res.get("pending_id"))
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
        return {"staged": len(staged), "pending_ids": staged, "kind": body.kind}
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
    site_id = site_id or None
    sap = sap_code.strip()

    where, params = 's."SAP_Code" = :sap', {"sap": sap}
    if site_id:
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
    if site_id:
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
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)

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
            whatsapp_alert_sent=0).returning(_returnables_t.c["id"]))).scalar_one()
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


@router.post("/returnables/{rid}/return", summary="Mark a loaned tool as returned")
async def mark_returned(rid: int,
                        user: dict = Depends(require_roles("store_keeper")),
                        session: AsyncSession = Depends(get_session)):
    t = _returnables_t
    async with session.begin():
        row = (await session.execute(select(
            t.c["Site_ID"], t.c["status"], t.c["material_name"],
            t.c["borrower_name"], t.c["borrower_phone"],
        ).where(t.c["id"] == rid))).first()
        if row is None:
            raise HTTPException(404, f"returnable {rid} not found")
        scope = resolve_site_param(user, None)
        if scope and (row.Site_ID or "").strip() != scope:
            raise HTTPException(403, "this loan belongs to another site")
        if row.status == "returned":
            raise HTTPException(409, "already returned")
        await session.execute(update(t).where(t.c["id"] == rid).values(status="returned"))
        await ledger.write_audit(session, user["username"], "RETURNABLE_RETURN",
                                 "returnable_items", f"id={rid}")
        await notify(session, event_key="loan_returned", recipient_role="store_keeper",
                     recipient_site=row.Site_ID, severity="success",
                     title=f"Tool returned: {row.material_name}",
                     body=f"{row.borrower_name} returned it — confirmed by {user['username']}.",
                     link_page="/entry/returnables", related_table="returnable_items",
                     related_ref=str(rid))
    # Confirm to the borrower on WhatsApp — best-effort, post-commit.
    if row.borrower_phone and wa.enabled():
        try:
            await wa.send_template(
                session, to=row.borrower_phone, template_key="status_update",
                variables=[f"Tool return confirmed: {row.material_name}",
                           f"Received back at {row.Site_ID or 'the'} store. Thank you."],
                event_key="loan_returned", related_table="returnable_items",
                related_ref=str(rid), created_by=user["username"])
            await session.commit()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            await session.rollback()
    return {"returned": True, "id": rid}
