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
import json
from typing import Optional

from fastapi import (APIRouter, Body, Depends, File, HTTPException,
                     UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_level, require_roles
from ..db import SessionLocal, get_session
from ..services.ledger import _MD
from ..services.procurement import classify_rl_bl_family
from . import client as aic
from . import jobs as ai_jobs
from . import manual_qa
from . import ocr
from . import pdf_extract

settings_t = _MD.tables["app_settings"]
inventory_t = _MD.tables["inventory"]

router = APIRouter(prefix="/ai", tags=["ai"])

_FLAG_DEFAULTS = {"ai_enabled": "1", "ai_assistant_enabled": "1",
                  "ai_doc_intel_enabled": "1", "ai_ocr_enabled": "1",
                  "ai_nl_search_enabled": "1", "ai_insights_enabled": "1"}


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
    return {"ok": ok, "enabled": True, "message": msg,
            "model": aic.MODEL_CHAT}


class AskIn(BaseModel):
    question: str


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/assistant", summary="Hub Assistant — SSE token stream")
async def assistant(body: AskIn = Body(...),
                    user: dict = Depends(get_current_user)):
    role, username = user["role"], user["username"]

    async def gen():
        # Flags need their own session: the request-scoped one would be
        # closed by the time this generator streams.
        async with SessionLocal() as s:
            flags = await _flags(s)
        if not (flags["ai_enabled"] and flags["ai_assistant_enabled"]):
            yield _sse({"error": "AI features are switched off in Settings.",
                        "done": True})
            return

        # Greetings skip health + semaphore entirely (in-process fast path).
        canned = manual_qa.greeting_reply(body.question)
        if canned is not None:
            yield _sse({"token": canned})
            yield _sse({"done": True})
            return

        # Generation semaphore: emit "queued" only when actually waiting so
        # the UI can say "waiting for a free AI slot…" instead of freezing.
        try:
            await asyncio.wait_for(aic.GEN_SEMAPHORE.acquire(), timeout=0.05)
        except (asyncio.TimeoutError, TimeoutError):
            yield _sse({"status": "queued"})
            await aic.GEN_SEMAPHORE.acquire()
        try:
            async for chunk in manual_qa.answer_manual_question(
                    body.question, role, username):
                yield _sse({"token": chunk})
            yield _sse({"done": True})
        finally:
            aic.GEN_SEMAPHORE.release()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --- Phase AI-2: document intelligence (PR/PO PDF extraction) -------------------
# Preview-confirm workflow: these endpoints ONLY parse and return a preview —
# nothing is written. The React side lets the user review/edit, then confirms
# through the EXISTING audited services (POST /hod/prs → procurement.create_pr,
# POST /logistics/pos → create_po_from_pr), which fixes the legacy
# silent-insert flaw (PR/PO PDF uploads never wrote an audit row).

async def _require_doc_intel(session: AsyncSession) -> None:
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_doc_intel_enabled"]):
        raise HTTPException(503, "Document intelligence is switched off in Settings.")


async def _read_pdf_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(422, "PDF too large (15 MB max)")
    return data


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
    try:
        parsed = await _aio.to_thread(pdf_extract.parse_pr_pdf, data)
    except pdf_extract.PdfExtractError as e:
        raise HTTPException(422, str(e))

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
    return {"pr_number": parsed["pr_number"], "matched": matched,
            "unmatched": unmatched,
            "hint": ("confirm via POST /hod/prs with the matched lines — "
                     "unmatched codes must be added to the Master DB first")}


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
    try:
        parsed = await _aio.to_thread(pdf_extract.parse_po_pdf, data,
                                      classify_rl_bl_family)
    except pdf_extract.PdfExtractError as e:
        raise HTTPException(422, str(e))
    return parsed


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
           "finished_at": row["finished_at"]}
    if row["status"] == "done" and row["result_json"]:
        out["result"] = json.loads(row["result_json"])
    return out


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
        emp_t.c["Department"], emp_t.c["status"])
        .where(func.trim(emp_t.c["ID_Number"]) == id_number.strip()).limit(1))
    ).first()
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


@router.post("/nl-search", summary="Plain-English database query (logistics/admin)")
async def nl_search(body: NlSearchIn = Body(...),
                    user: dict = Depends(require_level(3)),
                    session: AsyncSession = Depends(get_session)):
    flags = await _flags(session)
    if not (flags["ai_enabled"] and flags["ai_nl_search_enabled"]):
        raise HTTPException(503, "NL search is switched off in Settings.")
    if not body.question.strip():
        raise HTTPException(422, "ask a question")
    return await analytics.run_nl_query(body.question.strip())


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
