"""
backend/api/ai/router.py — /ai endpoints (Phase AI-0 health + AI-1 assistant).

SSE contract for /ai/assistant (media type text/event-stream):
    data: {"status": "queued"}      only when waiting on the gen semaphore
    data: {"token": "..."}          one per model chunk
    data: {"done": true}            always the final event
    data: {"error": "...", "done": true}   disabled/offline (HTTP still 200 —
                                            SSE consumers read events, not codes)

Feature flags live in app_settings (admin console → Settings): `ai_enabled`
is the master switch, `ai_assistant_enabled` gates this endpoint. Missing
keys default ON — the runtime Ollama health check is the real gate, same
philosophy as legacy AI_ENABLED=True.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import time as _perf
from typing import Optional

from fastapi import (APIRouter, Body, Depends, File, HTTPException,
                     UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, insert, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (get_current_user, require_level, require_roles, site_row_visible,
                    site_scope)
from ..db import SessionLocal, get_session
from ..services.ledger import _MD, write_audit
from ..services.procurement import classify_rl_bl_family
from . import client as aic
from . import handwritten as hw
from . import jobs as ai_jobs
from . import manual_qa
from . import tutorials as ai_tutorials
from . import ocr
from . import pdf_extract
from . import trace as ai_trace

settings_t = _MD.tables["app_settings"]
inventory_t = _MD.tables["inventory"]

router = APIRouter(prefix="/ai", tags=["ai"])

_FLAG_DEFAULTS = {"ai_enabled": "1", "ai_assistant_enabled": "1",
                  "ai_doc_intel_enabled": "1", "ai_ocr_enabled": "1",
                  "ai_nl_search_enabled": "1", "ai_insights_enabled": "1",
                  "ai_submission_intel_enabled": "1",  # T1 reviewer summaries
                  # QSEP slice 6 — the scanned PR/PO vision lane. Separate
                  # from ai_ocr_enabled so a site can keep handwriting OCR
                  # while turning off the heavier purchase-document lane.
                  "ocr_purchase_scans": "1"}


async def _flags(session: AsyncSession) -> dict[str, bool]:
    rows = (await session.execute(
        select(settings_t.c["key"], settings_t.c["value"])
        .where(settings_t.c["key"].in_(list(_FLAG_DEFAULTS))))).all()
    got = {r.key: r.value for r in rows}
    return {k: (got.get(k, d) == "1") for k, d in _FLAG_DEFAULTS.items()}


@router.get("/health", summary="AI layer status (flags + Ollama + model + manual)")
async def ai_health(user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_assistant_enabled"]):
        return {"ok": False, "enabled": False,
                "message": "AI features are switched off in Settings."}
    ok, msg = await manual_qa.health()
    # The tutorial index is reported rather than assumed: before the Hetzner
    # cutover puts the renders in object storage (ruling Q3) a production box
    # has no manifests at all, and "no deep links appeared" should be a visible
    # state rather than a mystery.
    return {"ok": ok, "enabled": True, "message": msg,
            "model": aic.MODEL_CHAT,
            "tutorials": ai_tutorials.stats()}


class AskIn(BaseModel):
    question: str


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/assistant", summary="Hub Assistant — SSE token stream")
async def assistant(body: AskIn = Body(...),
                    user: dict = Depends(get_current_user)):
    role, username = user["role"], user["username"]
    site_id = site_scope(user) or ""

    async def gen():
        # ⚠️ ONE TRACE ID PER TURN, MINTED BEFORE ANYTHING CAN GO WRONG, so a
        # request that dies in the flag check is still a row that says so. A
        # tracer that only records successful requests describes a system
        # nobody is having trouble with.
        tid = ai_trace.new_trace_id()
        req = ai_trace.Span("ai.request", trace_id=tid, lane="assistant",
                            role=role, username=username, site_id=site_id)
        req.__enter__()
        # The QUESTION is recorded, the ANSWER is not (ruling Q11, 2026-09-02):
        # what people actually ask is the most valuable eval material in the
        # system and nothing was retaining it. See trace.py for what is refused.
        req.attrs(question=ai_trace.clip_question(body.question),
                  question_chars=len(body.question or ""))
        queued_ms = 0
        try:
            # Flags need their own session: the request-scoped one would be
            # closed by the time this generator streams.
            async with SessionLocal() as s:
                flags = await _flags(s)
            if not (flags["ai_enabled"] and flags["ai_assistant_enabled"]):
                req.outcome("disabled")
                yield _sse({"error": "AI features are switched off in Settings.",
                            "done": True})
                return

            # Greetings skip health + semaphore entirely (in-process fast path).
            canned = manual_qa.greeting_reply(body.question)
            if canned is not None:
                req.outcome("greeting")
                yield _sse({"token": canned})
                yield _sse({"done": True})
                return

            # Generation semaphore: emit "queued" only when actually waiting so
            # the UI can say "waiting for a free AI slot…" instead of freezing.
            _q0 = _perf.perf_counter()
            try:
                await asyncio.wait_for(aic.GEN_SEMAPHORE.acquire(), timeout=0.05)
            except (asyncio.TimeoutError, TimeoutError):
                yield _sse({"status": "queued"})
                await aic.GEN_SEMAPHORE.acquire()
                # ⚠️ QUEUE WAIT IS MEASURED SEPARATELY FROM GENERATION. On a box
                # that holds one warm model, "the assistant is slow" is often
                # two people asking at once rather than a slow model, and the
                # two have opposite fixes.
                queued_ms = int((_perf.perf_counter() - _q0) * 1000)
            try:
                async for chunk in manual_qa.answer_manual_question(
                        body.question, role, username, trace_id=tid):
                    yield _sse({"token": chunk})
                # ⚠️ PHASE 12f — AFTER the answer, never instead of it, and it
                # can never break it. The deep link is computed from the
                # QUESTION and the ROLE alone: no model, no clock, no database,
                # so a cached answer (P11-7) still gets one and the same
                # question always returns the same second. If anything here
                # raises, the turn ends exactly as it did before the feature
                # existed — P11-3's rule, that an add-on must never convert a
                # diagnostic into an outage.
                try:
                    hit = ai_tutorials.match(body.question, role)
                except Exception:  # noqa: BLE001
                    hit = None
                if hit:
                    req.attrs(tutorial=hit["tutorial_id"],
                              tutorial_beat=hit["beat"])
                    yield _sse({"tutorial": hit})
                yield _sse({"done": True})
            finally:
                aic.GEN_SEMAPHORE.release()
        finally:
            req.attrs(queued_ms=queued_ms)
            req.__exit__(None, None, None)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --- Phase AI-2: document intelligence (PR/PO PDF extraction) -------------------
# Preview-confirm workflow: these endpoints parse and return a preview — no
# PR or PO row is written. The React side lets the user review/edit, then
# confirms through the EXISTING audited services (POST /hod/prs →
# procurement.create_pr, POST /logistics/pos → create_po_from_pr), which
# fixes the legacy silent-insert flaw (PR/PO PDF uploads never wrote an audit
# row).
#
# QSEP slice 6 changed two things about them.
#
# **THE DOCUMENT IS NOW STORED.** These endpoints used to `await file.read()`,
# parse the bytes and drop them: a PR created from a scan had no scan, and
# "all documents must be securely stored" was simply unmet. The upload is
# persisted to `entry_attachments` FIRST — before parsing, so a file that
# defeats the parser is still on file — and the returned `attachment_id` is
# what the confirm step links.
#
# **A SCAN GOES TO THE VISION LANE.** Calibrated on a real file:
# `PO#4710003121_PR681.pdf` is a genuine GI purchase order that was printed,
# signed and scanned back in. It carries ZERO text characters, so pdfplumber
# returns empty pages, the layout regexes match nothing, and the old
# behaviour was a cheerful 200 with an empty item list. The lane is chosen by
# `pdf_extract.looks_scanned()` — whether text CAME OUT — never by the
# content type, because a .pdf is not evidence of text and routing on the
# extension sends that file down the dead path forever.

async def _require_doc_intel(session: AsyncSession) -> None:
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_doc_intel_enabled"]):
        raise HTTPException(503, "Document intelligence is switched off in Settings.")


_MAX_DOC_MB = 15
_DOC_MIMES = ("application/pdf", "image/")


async def _read_pdf_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > _MAX_DOC_MB * 1024 * 1024:
        raise HTTPException(422, f"file too large ({_MAX_DOC_MB} MB max)")
    if not data:
        raise HTTPException(422, "empty file")
    mime = (file.content_type or "").lower()
    if mime and not any(mime.startswith(p) for p in _DOC_MIMES):
        raise HTTPException(422, "upload a PDF or a photo of the document")
    return data


async def _store_scan(session: AsyncSession, *, file: UploadFile, data: bytes,
                      doc_type: str, user: dict) -> int:
    """Persist the upload and return its entry_attachments id.

    Stored BEFORE parsing, deliberately. A document that defeats the parser
    is exactly the one somebody will need to look at, and storing only on
    success would lose precisely those.

    `Site_ID` comes from the caller's own binding and is left NULL for the
    unscoped roles (logistics, admin) who raise POs across every site —
    `entry_attachments.Site_ID` is nullable and the Document Library filters
    on it, so a NULL is "not site-specific" rather than a lie about which
    site it belongs to.
    """
    from ..services.ledger import write_audit
    attachments_t = _MD.tables["entry_attachments"]
    aid = (await session.execute(insert(attachments_t).values(
        Site_ID=(user.get("site_id") or None), doc_type=doc_type,
        doc_number=_dt.date.today().strftime("%d%m%y"),
        file_name=file.filename or f"{doc_type}.pdf",
        mime_type=(file.content_type or None), file_size=len(data),
        file_blob=data, uploaded_by=user["username"],
    ).returning(attachments_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "SCAN_UPLOAD", "entry_attachments",
                      f"id={aid} type={doc_type} name={file.filename!r} "
                      f"bytes={len(data)}")
    return aid


async def _queue_vision(session: AsyncSession, *, data: bytes, user: dict,
                        attachment_id: int, doc_hint: str) -> dict:
    """Hand a scanned purchase document to the vision worker."""
    flags = await _flags(session)
    if not flags.get("ocr_purchase_scans", True):
        raise HTTPException(
            503, "This document has no readable text (it is a scan), and the "
                 "scanned-document reader is switched off in Settings. The "
                 "file has been stored — enter the lines manually.")
    try:
        prepped = ocr.prep_image_for_vision(_pdf_first_page_png(data)
                                            if data[:4] == b"%PDF" else data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            422, f"The document was stored (id {attachment_id}) but could not be "
                 f"prepared for reading: {e}")
    job_id = await ai_jobs.create_job(session, kind="ocr_purchase_doc",
                                   actor=user["username"],
                                   site_id=user.get("site_id") or None,
                                   image_b64=ai_jobs.to_b64(prepped))
    await session.commit()
    ai_jobs.spawn(job_id)
    return {"mode": "vision", "job_id": job_id, "attachment_id": attachment_id,
            "doc_hint": doc_hint,
            "message": ("This document is a scan with no readable text, so it is "
                        "being read by the vision model. Poll /ai/jobs/{job_id}.")}


def _pdf_first_page_png(data: bytes) -> bytes:
    """Render page 1 of a scanned PDF to PNG bytes for the vision model.

    pdfplumber's page renderer is used rather than adding a dependency; it is
    already installed for the text lane. A scanned PO is one page in every
    sample we have, and rendering the whole document would multiply the
    vision cost for pages that are signature blocks.
    """
    import io as _io

    import pdfplumber
    with pdfplumber.open(_io.BytesIO(data)) as pdf:
        if not pdf.pages:
            raise ValueError("the PDF has no pages")
        img = pdf.pages[0].to_image(resolution=170)
        buf = _io.BytesIO()
        img.original.save(buf, format="PNG")
        return buf.getvalue()


@router.post("/extract/pr", summary="Extract a Purchase Request PDF (preview only)")
async def extract_pr(file: UploadFile = File(...),
                     user: dict = Depends(require_level(2)),
                     session: AsyncSession = Depends(get_session)):
    """pdfplumber runs in a worker thread (CPU-bound); the event loop stays
    free. Items are matched to the inventory master exactly like legacy
    (strict Material_Code match, case-insensitive) but returned as a preview —
    matched rows are pre-shaped as create-PR lines, unmatched ones carry the
    legacy context window so the admin can add them to the master DB."""
    import asyncio as _aio
    await _require_doc_intel(session)
    data = await _read_pdf_upload(file)
    # Stored FIRST — a file that defeats the parser is the one somebody will
    # need to look at, and storing only on success loses exactly those.
    attachment_id = await _store_scan(session, file=file, data=data,
                                      doc_type="pr_scan", user=user)
    is_pdf = data[:4] == b"%PDF"
    if not is_pdf or pdf_extract.looks_scanned(data):
        return await _queue_vision(session, data=data, user=user,
                                   attachment_id=attachment_id, doc_hint="PR")
    try:
        parsed = await _aio.to_thread(pdf_extract.parse_pr_pdf, data)
    except pdf_extract.PdfExtractError as e:
        await session.commit()   # keep the stored document; the parse failed
        raise HTTPException(422, f"{e} (the file was stored as attachment "
                                 f"{attachment_id})")

    codes = [it["material_code"] for it in parsed["items"]]
    inv = {}
    if codes:
        rows = (await session.execute(select(
            inventory_t.c["Material_Code"], inventory_t.c["SAP_Code"],
            inventory_t.c["Equipment_Description"], inventory_t.c["UOM"])
            .where(func.upper(func.trim(inventory_t.c["Material_Code"]))
                   .in_(codes)))).all()
        inv = {str(r[0]).strip().upper(): r for r in rows}

    matched, unmatched = [], []
    for it in parsed["items"]:
        hit = inv.get(it["material_code"])
        if hit is not None:
            matched.append({"SAP_Code": str(hit[1]),
                            "Material_Code": it["material_code"],
                            "Material_Name": hit[2] or "",
                            "UOM": hit[3] or "",
                            "Requested_Qty": it["qty"]})
        else:
            unmatched.append(it)
    await session.commit()
    return {"mode": "text", "attachment_id": attachment_id,
            "pr_number": parsed["pr_number"], "matched": matched,
            "unmatched": unmatched,
            "hint": ("confirm via POST /hod/prs with the matched lines and "
                     f"source_attachment_id={attachment_id} — unmatched codes "
                     "must be added to the Master DB first")}


@router.post("/extract/po", summary="Extract a Purchase Order PDF (preview only)")
async def extract_po(file: UploadFile = File(...),
                     user: dict = Depends(require_level(3)),
                     session: AsyncSession = Depends(get_session)):
    """Header + line items + shipment schedule, all three legacy layouts.
    The header prefills the Create-PO form (PR number, PO number, vendor);
    PO LINES still derive from the submitted PR on confirm — the locked
    'simplified DN/PO chain' ruling — so extracted items are shown for
    review/reconciliation against the PR, not inserted directly."""
    import asyncio as _aio
    await _require_doc_intel(session)
    data = await _read_pdf_upload(file)
    attachment_id = await _store_scan(session, file=file, data=data,
                                      doc_type="po_scan", user=user)
    is_pdf = data[:4] == b"%PDF"
    if not is_pdf or pdf_extract.looks_scanned(data):
        # This is the branch a real GI purchase order takes:
        # PO#4710003121_PR681.pdf has zero text and five page images.
        return await _queue_vision(session, data=data, user=user,
                                   attachment_id=attachment_id, doc_hint="PO")
    try:
        parsed = await _aio.to_thread(pdf_extract.parse_po_pdf, data,
                                      classify_rl_bl_family)
    except pdf_extract.PdfExtractError as e:
        await session.commit()
        raise HTTPException(422, f"{e} (the file was stored as attachment "
                                 f"{attachment_id})")
    await session.commit()
    return {"mode": "text", "attachment_id": attachment_id, **parsed}


# --- Phase AI-3: handwriting OCR (async jobs + offline paste lane) ----------------
# Exact-locked to {store_keeper, admin} — the legacy Daily Issue Log lock.
# Image lane: POST /ai/jobs returns an id immediately; an in-process worker
# (jobs.run_job — atomic queued→running claim) does prep-checked-at-upload
# image → qwen2.5vl → JSON parse → fuzzy resolve; React polls /ai/jobs/{id}.
# Paste lane: pure-Python, synchronous, works with Ollama down.

async def _require_ocr(session: AsyncSession) -> None:
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_ocr_enabled"]):
        raise HTTPException(503, "OCR import is switched off in Settings.")


@router.post("/jobs", status_code=202, summary="Queue a vision-OCR job (photo upload)")
async def create_ocr_job(file: UploadFile = File(...), kind: str = "ocr_consumption",
                         user: dict = Depends(require_roles("store_keeper")),
                         session: AsyncSession = Depends(get_session)):
    import asyncio as _aio
    await _require_ocr(session)
    if kind not in ai_jobs.JOB_KINDS:
        raise HTTPException(422, f"kind must be one of {list(ai_jobs.JOB_KINDS)}")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(422, "image too large (20 MB max)")
    try:
        # Prep NOW (worker thread — Pillow is CPU-bound) so a corrupt/HEIC-
        # without-codec photo fails fast with a friendly 422, not a dead job.
        prepped = await _aio.to_thread(ocr.prep_image_for_vision, data)
    except ocr.ImagePrepError as e:
        raise HTTPException(422, str(e))
    job_id = await ai_jobs.create_job(
        session, kind=kind, actor=user["username"],
        site_id=(user.get("site_id") or None), image_b64=ai_jobs.to_b64(prepped))
    await session.commit()
    ai_jobs.spawn(job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs/{job_id}", summary="Poll a vision-OCR job")
async def get_ocr_job(job_id: int,
                      user: dict = Depends(require_roles("store_keeper")),
                      session: AsyncSession = Depends(get_session)):
    t = _MD.tables["ai_jobs"]
    row = (await session.execute(select(t).where(t.c["id"] == job_id))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, f"job {job_id} not found")
    # Owner-only polling (admin may inspect any job).
    if user["role"] != "admin" and row["actor"] != user["username"]:
        raise HTTPException(403, "not your job")
    out = {"id": row["id"], "kind": row["kind"], "status": row["status"],
           "error": row["error"], "created_at": row["created_at"],
           "finished_at": row["finished_at"],
           **ai_jobs.progress(row)}
    if row["status"] == "done" and row["result_json"]:
        out["result"] = json.loads(row["result_json"])
    return out


@router.post("/jobs/{job_id}/requeue",
             summary="Re-run a job whose worker went away")
async def requeue_ocr_job(job_id: int,
                          user: dict = Depends(require_roles("store_keeper")),
                          session: AsyncSession = Depends(get_session)):
    """Put a stalled job back on the queue, using the image already stored.

    ⚠️ ONLY WHEN THE OWNER IS ACTUALLY GONE. Re-queueing a job that is merely
    slow starts a SECOND six-minute vision read of the same page on a box that
    holds one warm model — the first read then finishes into a row a second
    worker has re-claimed, and the person waiting gets neither result any
    sooner. `progress().stale` is the same predicate the orphan sweep uses, so
    the button appears exactly when the server has already concluded nobody is
    working on it.
    """
    t = _MD.tables["ai_jobs"]
    row = (await session.execute(select(t).where(t.c["id"] == job_id))
           ).mappings().first()
    if row is None:
        raise HTTPException(404, f"job {job_id} not found")
    if user["role"] != "admin" and row["actor"] != user["username"]:
        raise HTTPException(403, "not your job")
    return await ai_jobs.requeue(session, row, ai_jobs.spawn)


class FromAttachmentIn(BaseModel):
    attachment_id: int
    kind: str = "ocr_delivery_note"


@router.post("/jobs/from-attachment", status_code=202,
             summary="Queue a vision-OCR job over an ALREADY-UPLOADED entry "
                     "attachment (parity C3 — doc assist on the entry forms)")
async def create_ocr_job_from_attachment(body: FromAttachmentIn,
                                         user: dict = Depends(require_roles("store_keeper")),
                                         session: AsyncSession = Depends(get_session)):
    """The SK just photographed/attached the delivery note for the batch —
    read the SAME bytes back out of entry_attachments and extract the DN
    header / consumption rows so the form can auto-fill. No re-upload."""
    import asyncio as _aio
    await _require_ocr(session)
    if body.kind not in ai_jobs.JOB_KINDS:
        raise HTTPException(422, f"kind must be one of {list(ai_jobs.JOB_KINDS)}")
    att_t = _MD.tables["entry_attachments"]
    row = (await session.execute(select(att_t).where(
        att_t.c["id"] == body.attachment_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "attachment not found")
    # Same authz rule as the attachment download: uploader, or any allowed
    # role within the attachment's site (admin implicit via require_roles).
    if user["role"] != "admin" and row["uploaded_by"] != user["username"] \
            and (user.get("site_id") or "") != (row["Site_ID"] or ""):
        raise HTTPException(403, "not your attachment")
    try:
        prepped = await _aio.to_thread(ocr.prep_image_for_vision, row["file_blob"])
    except ocr.ImagePrepError:
        raise HTTPException(422, "This attachment isn't a readable photo "
                                 "(PDF/XLSX can't be OCR'd here) — photograph "
                                 "the note instead.")
    job_id = await ai_jobs.create_job(
        session, kind=body.kind, actor=user["username"],
        site_id=(user.get("site_id") or None), image_b64=ai_jobs.to_b64(prepped))
    await session.commit()
    ai_jobs.spawn(job_id)
    return {"job_id": job_id, "status": "queued"}


class PasteIn(BaseModel):
    text: str


@router.post("/paste/{kind}", summary="Offline paste lane (same result shape)")
async def parse_paste(kind: str, body: PasteIn = Body(...),
                      user: dict = Depends(require_roles("store_keeper")),
                      session: AsyncSession = Depends(get_session)):
    """Pure-Python twin of the OCR lane — parses pasted text instantly and
    runs the same fuzzy resolution, so the review grid is lane-agnostic.
    Works with Ollama completely offline."""
    await _require_ocr(session)
    if kind not in ai_jobs.JOB_KINDS:
        raise HTTPException(422, f"kind must be one of {list(ai_jobs.JOB_KINDS)}")
    try:
        parsed = (ocr.parse_consumption_paste(body.text)
                  if kind == "ocr_consumption"
                  else ocr.parse_delivery_note_paste(body.text))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return await ai_jobs._resolve(kind, parsed, session)


# --- Handwritten consumption forms (docs/features/handwritten-ocr spec) ----------
class HandwrittenForm(BaseModel):
    form_id: Optional[str] = None
    date_text: Optional[str] = None
    date_iso: Optional[str] = None
    rows: list[dict] = []


class HandwrittenBatchIn(BaseModel):
    forms: list[HandwrittenForm]


@router.post("/ocr/handwritten-process",
             summary="Deterministic post-processing for handwritten forms")
async def handwritten_process(body: HandwrittenBatchIn,
                              user: dict = Depends(require_roles("store_keeper")),
                              session: AsyncSession = Depends(get_session)):
    """Runs the handwritten-form spec stages (corrections → ditto → qty rules
    → spec fuzzy match → substitutions → batch stock simulation → flags) over
    transcribed rows from the vision or paste lane, and returns the JSON rows
    plus the 17-column legacy TSV export. READ-ONLY: posting still goes
    through the normal Issue flow."""
    await _require_ocr(session)
    total = sum(len(f.rows) for f in body.forms)
    if not body.forms or total == 0:
        raise HTTPException(422, "no rows to process")
    if len(body.forms) > 10 or total > hw.ROWS_PER_BATCH:
        raise HTTPException(422, "batch limits: 10 forms / 300 rows")

    scope = site_scope(user)
    inv_q = select(inventory_t.c["SAP_Code"], inventory_t.c["Equipment_Description"],
                   inventory_t.c["Material_Code"], inventory_t.c["UOM"])
    if scope is not None:
        inv_q = inv_q.where(func.coalesce(inventory_t.c["Site_ID"], "HQ") == scope)
    inventory = [dict(m) for m in (await session.execute(inv_q)).mappings().all()]

    stock_rows = (await session.execute(sa_text('''
        SELECT TRIM(i."SAP_Code") AS sap,
               COALESCE((SELECT SUM(r."Quantity") FROM receipts r
                         WHERE TRIM(r."SAP_Code")=TRIM(i."SAP_Code")),0)
             - COALESCE((SELECT SUM(c."Quantity") FROM consumption c
                         WHERE TRIM(c."SAP_Code")=TRIM(i."SAP_Code")),0)
             - COALESCE((SELECT SUM(t."Quantity") FROM returns t
                         WHERE TRIM(t."SAP_Code")=TRIM(i."SAP_Code")),0) AS stock
        FROM inventory i''' + (
        ' WHERE COALESCE(i."Site_ID", \'HQ\') = :site' if scope is not None else '')),
        ({"site": scope} if scope is not None else {}))).all()
    stock = {r.sap: float(r.stock or 0) for r in stock_rows}

    # 04 column map: the vision/paste lanes emit issued_to/material_text/
    # qty_text — translate to the spec's logical fields.
    forms = []
    for f in body.forms:
        rows = []
        for r in f.rows:
            rows.append({
                "source_row_no": r.get("sno") or r.get("source_row_no"),
                "received_by": r.get("received_by") or r.get("issued_to"),
                "tank_no": r.get("tank_no"),
                "product_name_raw": (r.get("product_name_raw")
                                     or r.get("material_text")),
                "qty": (r.get("qty_text") if str(r.get("qty_text") or "").strip()
                        else r.get("qty", r.get("quantity"))),
                "work_type": r.get("work_type"),
                "struck_through": bool(r.get("struck_through")),
            })
        forms.append({"form_id": f.form_id, "date_text": f.date_text,
                      "date_iso": f.date_iso, "rows": rows})
    return hw.process_batch(forms, inventory, stock)


# --- Phase AI-4: Smart Scan --------------------------------------------------------
# QR badge decoding happens ENTIRELY client-side (jsQR over the live camera
# feed — video never leaves the browser). This endpoint is the fast server
# verification for the decoded ID string: employee lookup + active check,
# exactly the legacy Tier-1 semantics. Plain DB read — no AI flag needed.

@router.get("/badge/{id_number}", summary="Verify a scanned employee badge (Tier 1)")
async def verify_badge(id_number: str,
                       user: dict = Depends(require_roles("store_keeper")),
                       session: AsyncSession = Depends(get_session)):
    emp_t = _MD.tables["employees"]
    row = (await session.execute(select(
        emp_t.c["ID_Number"], emp_t.c["Name"], emp_t.c["Phone_Number"],
        emp_t.c["Department"], emp_t.c["status"], emp_t.c["Site_ID"])
        .where(func.trim(emp_t.c["ID_Number"]) == id_number.strip()).limit(1))
    ).first()
    # Badge IDs are printed on physical badges and encoded in the QR sheets this
    # same system generates, so an unscoped lookup hands every site's staff name
    # and personal phone number to any store keeper. Hide employees positively
    # assigned to a DIFFERENT site, answering exactly as for an unknown badge so
    # the ID's existence isn't leaked either.
    #
    # Deliberately NOT `site_row_visible`: employees."Site_ID" is nullable and
    # postdates most rows, so treating blank as "not yours" would break badge
    # scanning for every employee recorded before the column existed. Blank
    # means unassigned staff, not another site's.
    scope = site_scope(user)
    if row is not None and scope is not None:
        row_site = (row.Site_ID or "").strip()
        if row_site and row_site != scope:
            row = None
    if row is None:
        return {"found": False,
                "message": f"No employee with badge ID {id_number!r}."}
    active = (row.status or "").lower() == "active"
    return {"found": True, "active": active, "id_number": row.ID_Number,
            "name": row.Name, "phone": row.Phone_Number or "",
            "department": row.Department or "",
            "message": None if active else
            f"{row.Name} is INACTIVE — loans need an active employee."}


# --- Phase AI-5: analytics AI ------------------------------------------------------
# NL→SQL: gated to UNSCOPED roles (logistics/admin, level ≥ 3) for V1 — the
# generated SQL can't be site-scoped reliably, so scoped roles are excluded
# by design. Execution runs on the gi_ai_ro read-only PG login (role-level
# statement_timeout + default_transaction_read_only + REVOKEd users tables)
# AFTER passing the PG-hardened safety gate — two independent walls.
from . import analytics


class NlSearchIn(BaseModel):
    question: str


async def _audit_ai_query(session: AsyncSession, user: dict, *, lane: str,
                          question: str, result: dict) -> None:
    """Audit every AI data question (audit A03-F10).

    The NL lane turns free text into SQL that runs against the database, and it
    used to leave no trace at all — a refused exfiltration attempt and a routine
    stock question were equally invisible. Record who asked what, which lane ran
    it, whether it succeeded, and the SQL that was actually executed, so the
    attempt is reconstructable afterwards.

    Best-effort: an audit failure must never turn a working answer into a 500.
    """
    try:
        sql = (result.get("sql") or "").strip().replace("\n", " ")
        detail = (f"lane={lane} ok={bool(result.get('ok'))} "
                  f"scope={site_scope(user)!r} q={question[:200]!r}")
        if sql:
            detail += f" sql={sql[:400]!r}"
        await write_audit(session, user["username"], "AI_QUERY", "ai", detail)
        await session.commit()
    except Exception:  # noqa: BLE001 — never mask the answer
        await session.rollback()


@router.post("/nl-search", summary="Plain-English database query (logistics/admin)")
async def nl_search(body: NlSearchIn = Body(...),
                    user: dict = Depends(require_level(3)),
                    session: AsyncSession = Depends(get_session)):
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_nl_search_enabled"]):
        raise HTTPException(503, "NL search is switched off in Settings.")
    if not body.question.strip():
        raise HTTPException(422, "ask a question")
    out = await analytics.run_nl_query(body.question.strip())
    await _audit_ai_query(session, user, lane="nl-search",
                          question=body.question.strip(), result=out)
    return out


# --- Phase C: "Chat with your data" ------------------------------------------------
# Two-lane router (see ai/query_router.py): deterministic site-scoped SQL
# templates for everyone level ≥2 (works with AI switched off / Ollama down),
# then the existing NL→SQL lane as a fallback for UNSCOPED roles only — the
# AI-5 scoping ruling stands: generated SQL is never run for a scoped user.
from . import query_router as qr


class DataQueryIn(BaseModel):
    question: str


@router.get("/query/examples", summary="Example questions for the Ask-your-data card")
async def query_examples(user: dict = Depends(require_level(2))):
    return {"examples": qr.EXAMPLES}


@router.post("/query", summary="Chat with your data — template router + NL fallback (level ≥2)")
async def data_query(body: DataQueryIn = Body(...),
                     user: dict = Depends(require_level(2)),
                     session: AsyncSession = Depends(get_session)):
    q = body.question.strip()
    if not q:
        raise HTTPException(422, "ask a question")
    scope = site_scope(user)
    known_sites: list[str] = []
    if scope is None:
        col = inventory_t.c["Site_ID"]
        res = await session.execute(select(func.distinct(col)).where(col.isnot(None)))
        known_sites = [r[0] for r in res.all()]

    templ = await qr.run_query(session, q, site_scope=scope, known_sites=known_sites)
    if templ is not None:
        await _audit_ai_query(session, user, lane="query/template",
                              question=q, result=templ)
        return templ

    flags = await _flags(session)
    if scope is None and user["level"] >= 3 and flags["ai_enabled"] and flags["ai_nl_search_enabled"]:
        out = await analytics.run_nl_query(q)
        out["mode"] = "nl"
        await _audit_ai_query(session, user, lane="query/nl", question=q, result=out)
        return out

    return {"ok": False, "mode": "template", "sql": "", "columns": [], "rows": [],
            "message": "I couldn't map that question to your data. Try one of the examples.",
            "examples": qr.EXAMPLES}


@router.post("/insights", summary="AI insights — 5 SQL probes + streamed commentary")
async def insights(site_id: Optional[str] = None,
                   user: dict = Depends(require_level(2)),
                   session: AsyncSession = Depends(get_session)):
    """SSE: one `probe` event per firing probe (deterministic numbers,
    immediate), then a `commentary` event per probe as the LLM narrates —
    progressive rendering, and the numbers never wait on the model."""
    from ..auth import resolve_site_param
    sid = resolve_site_param(user, site_id)

    async def gen():
        async with SessionLocal() as s:
            flags = await _flags(s)
            if not (flags["ai_enabled"] and flags["ai_insights_enabled"]):
                yield _sse({"error": "AI insights are switched off in Settings.",
                            "done": True})
                return
            fired = []
            for kind, icon, probe_fn, confidence in analytics.PROBES:
                try:
                    data = await probe_fn(s, sid)
                except Exception:
                    data = None
                if not data:
                    continue
                fired.append((kind, data))
                yield _sse({"probe": {"id": kind, "icon": icon,
                                      "metric": data.get("metric", "—"),
                                      "metric_label": data.get("metric_label", ""),
                                      "severity": data.get("severity", "ok"),
                                      "confidence": confidence,
                                      "data": data}})
        for kind, data in fired:
            commentary = await analytics.llm_commentary(kind, data)
            yield _sse({"commentary": {"id": kind, **commentary}})
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class EodIn(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD, default today
    site_id: Optional[str] = None


@router.post("/eod-summary", summary="Streaming end-of-day executive summary")
async def eod_summary(body: EodIn = Body(...),
                      user: dict = Depends(require_level(2))):
    import datetime as _dt

    from ..auth import resolve_site_param
    sid = resolve_site_param(user, body.site_id)
    day = (body.date or _dt.date.today().isoformat())[:10]

    async def gen():
        async with SessionLocal() as s:
            flags = await _flags(s)
            if not (flags["ai_enabled"] and flags["ai_insights_enabled"]):
                yield _sse({"error": "AI summaries are switched off in Settings.",
                            "done": True})
                return
            if not await aic.health():
                yield _sse({"error": "Local AI is offline — ask your admin to "
                                     "start Ollama.", "done": True})
                return
            try:
                context = await analytics.build_eod_context(s, day, sid)
            except Exception as e:
                yield _sse({"error": f"Could not build the day context: "
                                     f"{type(e).__name__}", "done": True})
                return
        try:
            await asyncio.wait_for(aic.GEN_SEMAPHORE.acquire(), timeout=0.05)
        except (asyncio.TimeoutError, TimeoutError):
            yield _sse({"status": "queued"})
            await aic.GEN_SEMAPHORE.acquire()
        try:
            async for chunk in aic.stream(
                    aic.MODEL_CHAT,
                    f"Daily warehouse snapshot:\n\n{context}\n\n"
                    f"Write the executive summary now.",
                    system=analytics.EOD_SYSTEM_PROMPT,
                    temperature=0.3, num_predict=320):
                yield _sse({"token": chunk})
            yield _sse({"done": True})
        except RuntimeError as e:
            yield _sse({"error": str(e), "done": True})
        finally:
            aic.GEN_SEMAPHORE.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ─── T1: Submission Intelligence — reviewer summaries ────────────────────────
# Deterministic stats (submission_stats.py) are the ONLY source of numbers;
# the LLM (llama3.1:8b, one-warm-model) merely rephrases them and every
# failure path falls back to the rock-solid deterministic template. Cached in
# ai_jobs (kind='submission_summary') so review screens never recompute.
import datetime as _sdt  # noqa: E402

from . import submission_stats as substats  # noqa: E402

_SUMMARY_TTL_MIN = 15
_SUMMARY_SYSTEM = (
    "You are an inventory reviewer's assistant. Rephrase the FACTS json into "
    "1-2 short plain sentences for the reviewer. NEVER invent, change or "
    "compute numbers — only use the numbers given. No preamble, no headings.")


def _deterministic_summary(f: dict) -> tuple[str, str]:
    """(summary, tone) from extracted features — the guaranteed fallback."""
    if f["kind"] == "staged-issue":
        s30 = f["stats_30d"]
        if f["first_time_material"]:
            return (f"First issue of {f['sap_code']} at {f['site']} in 60 days — "
                    f"no usage history to compare against.", "warning")
        parts, tone = [], "success"
        dev = f["deviation_pct"]
        if dev is not None and abs(dev) > 20:
            parts.append(f"This material is being issued {abs(dev):.0f}% "
                         f"{'more' if dev > 0 else 'less'} than its 30-day "
                         f"average ({s30['mean_issue_qty']} per issue).")
            tone = "warning"
        if f["off_pattern_day"]:
            parts.append("The issue date falls outside this material's usual "
                         "consumption days.")
            tone = "warning"
        if not parts:
            parts.append(f"Usual consumption. Qty {f['qty']} is in line with the "
                         f"30-day average of {s30['mean_issue_qty']} per issue "
                         f"({s30['issues']} issues in 30 days).")
        return " ".join(parts), tone
    if f["kind"] == "xsite":
        rate = f["target_stats_30d"]["mean_daily_qty"]
        if f["days_cover_now"] is None:
            return (f"{f['target_site']} holds {f['target_stock']} of "
                    f"{f['sap_code']} and shows no consumption in 30 days — "
                    f"granting {f['requested_qty']} carries no forecast risk.",
                    "success")
        after = f["days_cover_after"]
        base = (f"If you give {f['requested_qty']} of {f['sap_code']} to "
                f"{f['requesting_site']}, {f['target_site']} drops from "
                f"{f['days_cover_now']} to {after if after is not None and after > 0 else 0} "
                f"days of cover (30-day avg use {rate}/day).")
        if after is not None and after <= 0:
            return base + " This would run the site OUT of stock.", "error"
        if after is not None and after < 14:
            return base + " The site will be short within two weeks.", "warning"
        return base, "success"
    return "No summary available for this submission kind.", "info"


async def _cached_summary(session: AsyncSession, kind: str, ref_id: int) -> dict | None:
    ai_jobs_t = _MD.tables["ai_jobs"]
    cutoff = _sdt.datetime.now() - _sdt.timedelta(minutes=_SUMMARY_TTL_MIN)
    row = (await session.execute(
        select(ai_jobs_t.c["result_json"])
        .where(ai_jobs_t.c["kind"] == "submission_summary",
               ai_jobs_t.c["status"] == "done",
               ai_jobs_t.c["payload_json"] == json.dumps({"kind": kind, "ref": ref_id}),
               ai_jobs_t.c["created_at"] >= cutoff)
        .order_by(ai_jobs_t.c["id"].desc()).limit(1))).first()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except ValueError:
            return None
    return None


@router.get("/submission-summary",
            summary="Reviewer intelligence for one pending submission "
                    "(deterministic stats, optional local-LLM phrasing)")
async def submission_summary(kind: str, ref_id: int,
                             user: dict = Depends(get_current_user),
                             session: AsyncSession = Depends(get_session)):
    if kind == "staged-issue":
        if user["level"] < 2:  # HOD reviews staged issues
            raise HTTPException(403, "reviewer access required")
        feats = await substats.staged_issue_features(session, ref_id)
    elif kind == "xsite":
        if user["level"] < 2:  # target-site HOD reviews cross-site requests
            raise HTTPException(403, "reviewer access required")
        feats = await substats.xsite_features(session, ref_id)
    else:
        raise HTTPException(404, f"unknown submission kind {kind!r}")
    if feats is None:
        raise HTTPException(404, f"{kind} {ref_id} not found")

    # The level check above says "a reviewer", not "THIS row's reviewer" — so a
    # scoped HOD could walk ref_id and read another site's staged-issue detail
    # (material, qty, issued-to, 30/60-day usage) that hod.py guards carefully.
    # A cross-site request legitimately has two parties, so either side may look.
    scope = site_scope(user)
    row_sites = ([feats.get("site")] if kind == "staged-issue"
                 else [feats.get("target_site"), feats.get("requesting_site")])
    if not any(site_row_visible(scope, s) for s in row_sites):
        raise HTTPException(404, f"{kind} {ref_id} not found")

    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_submission_intel_enabled"]):
        summary, tone = _deterministic_summary(feats)
        return {"kind": kind, "ref_id": ref_id, "summary": summary,
                "tone": tone, "source": "deterministic", "facts": feats}

    cached = await _cached_summary(session, kind, ref_id)
    if cached is not None:
        return {"kind": kind, "ref_id": ref_id, **cached, "cached": True,
                "facts": feats}

    summary, tone = _deterministic_summary(feats)
    source = "deterministic"
    try:  # optional phrasing — every failure path keeps the deterministic text
        if await aic.health():
            phrased = await aic.generate(
                aic.MODEL_CHAT,
                f"FACTS:\n{json.dumps(feats, ensure_ascii=False)}\n\n"
                f"Deterministic draft (keep every number exactly): {summary}",
                system=_SUMMARY_SYSTEM, temperature=0.2, num_predict=120)
            phrased = (phrased or "").strip()
            if phrased:
                summary, source = phrased, "ai"
    except Exception:
        pass  # deterministic text stands

    ai_jobs_t = _MD.tables["ai_jobs"]
    from sqlalchemy import insert as _insert
    await session.execute(_insert(ai_jobs_t).values(
        kind="submission_summary", status="done", actor=user["username"],
        Site_ID=feats.get("site") or feats.get("target_site"),
        payload_json=json.dumps({"kind": kind, "ref": ref_id}),
        result_json=json.dumps({"summary": summary, "tone": tone, "source": source},
                               ensure_ascii=False)))
    await session.commit()
    return {"kind": kind, "ref_id": ref_id, "summary": summary, "tone": tone,
            "source": source, "facts": feats}
