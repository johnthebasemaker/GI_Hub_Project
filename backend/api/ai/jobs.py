"""
backend/api/ai/jobs.py — async AI job queue over the ai_jobs table (Phase AI-3).

Why a job table instead of awaiting inline: vision OCR takes 5–120 s
(qwen2.5vl cold start ~30–90 s) — longer than proxy timeouts and mobile
patience. POST /ai/jobs inserts a row and spawns an in-process
asyncio.create_task; React polls GET /ai/jobs/{id} every ~2 s. Jobs survive
page reloads and locked phones because state lives in Postgres, not the
connection.

Concurrency discipline (same as the report scheduler's last_run claim):
the queued→running transition is a single atomic UPDATE guarded on
status='queued' — if two workers ever race one job, exactly one wins.
Orphan sweep: on startup, rows still 'queued'/'running' from a previous
process are failed with a clear message (their in-process task died with
the old server; the user just resubmits the photo).

The worker calls the Ollama client through the module object (`aic.…`) —
the same monkeypatch seam the assistant tests use, so the suite runs the
FULL job lifecycle with a fake model and no Ollama.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import json
import logging

from sqlalchemy import select, update

from ..db import SessionLocal
from ..services.ledger import _MD
from . import client as aic
from . import fuzzy, ocr

logger = logging.getLogger("gi.ai.jobs")

ai_jobs_t = _MD.tables["ai_jobs"]
inventory_t = _MD.tables["inventory"]

JOB_KINDS = ("ocr_consumption", "ocr_delivery_note", "tool_identify")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


async def create_job(session, *, kind: str, actor: str, site_id: str | None,
                     image_b64: str) -> int:
    """Insert a queued job row. The caller commits and then spawns run_job."""
    from sqlalchemy import insert
    payload = json.dumps({"image_b64": image_b64})
    return (await session.execute(insert(ai_jobs_t).values(
        kind=kind, status="queued", actor=actor, Site_ID=site_id,
        payload_json=payload).returning(ai_jobs_t.c["id"]))).scalar_one()


async def _vision_preflight() -> str | None:
    """Friendly error string when image OCR can't run, else None — port of the
    legacy two-check preflight (server reachable, vision model pulled)."""
    if not await aic.health():
        return (f"Local AI is offline (Ollama not reachable at {aic.OLLAMA_HOST}). "
                "Ask your admin to start the Ollama service, or use the Paste tab.")
    installed = await aic.list_models()
    if installed and aic.MODEL_VISION not in installed:
        return (f"Vision model {aic.MODEL_VISION} is not installed on the AI "
                f"server — ask your admin to run `ollama pull {aic.MODEL_VISION}`. "
                "The Paste tab works meanwhile.")
    return None


async def _resolve(kind: str, parsed: dict, session) -> dict:
    """Fuzzy-match every material_text against the inventory master so the
    review grid opens with auto/pick/unknown states already computed."""
    inv_rows = (await session.execute(select(
        inventory_t.c["SAP_Code"], inventory_t.c["Equipment_Description"],
        inventory_t.c["Material_Code"], inventory_t.c["UOM"]))).mappings().all()
    inventory = [dict(r) for r in inv_rows]
    if kind == "ocr_consumption":
        return {"rows": fuzzy.resolve_rows(parsed["rows"], inventory)}
    return {"header": parsed["header"],
            "items": fuzzy.resolve_rows(parsed["items"], inventory)}


async def run_job(job_id: int) -> None:
    """The worker. Own sessions (the request that spawned us is long gone);
    never raises — every failure lands in ai_jobs.error for the poller."""
    async with SessionLocal() as s:
        claimed = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["id"] == job_id,
            ai_jobs_t.c["status"] == "queued",
        ).values(status="running", started_at=_now()))
        if claimed.rowcount == 0:  # raced by another worker — theirs now
            await s.rollback()
            return
        row = (await s.execute(select(ai_jobs_t.c["kind"], ai_jobs_t.c["payload_json"])
                               .where(ai_jobs_t.c["id"] == job_id))).first()
        await s.commit()

    kind = row.kind
    try:
        err = await _vision_preflight()
        if err:
            raise RuntimeError(err)
        image_b64 = json.loads(row.payload_json or "{}").get("image_b64", "")

        if kind == "tool_identify":
            # Smart Scan tier-2 (AI-4): catalogue-constrained when the
            # tool_catalogue has rows, freeform naming when it's empty.
            async with SessionLocal() as s:
                cat_t = _MD.tables["tool_catalogue"]
                catalogue = [dict(m) for m in (await s.execute(select(
                    cat_t.c["class_name"], cat_t.c["display_name"]))).mappings()]
            async with aic.GEN_SEMAPHORE:
                raw = await aic.generate(
                    aic.MODEL_VISION, "Identify the tool.",
                    system=ocr.tool_prompt(catalogue), images=[image_b64],
                    temperature=0.1, num_predict=256)
            result = ocr.parse_tool_reply(raw, catalogue)
        else:
            async with aic.GEN_SEMAPHORE:
                raw = await aic.generate(
                    aic.MODEL_VISION, ocr.USER_PROMPTS[kind],
                    system=ocr.SYSTEM_PROMPTS[kind], images=[image_b64],
                    temperature=0.1, num_predict=1024)
            parsed = ocr.parse_vision_reply(kind, raw)

        async with SessionLocal() as s:
            if kind != "tool_identify":
                result = await _resolve(kind, parsed, s)
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="done", finished_at=_now(),
                                    result_json=json.dumps(result, ensure_ascii=False)))
            await s.commit()
    except Exception as e:
        logger.warning("ai job %s failed: %s", job_id, e)
        async with SessionLocal() as s:
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="error", finished_at=_now(),
                                    error=str(e)[:500]))
            await s.commit()


def spawn(job_id: int) -> None:
    """Fire-and-forget worker task (kept referenced so GC can't collect it)."""
    task = asyncio.create_task(run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


_RUNNING: set[asyncio.Task] = set()


async def fail_orphans() -> int:
    """Startup sweep: jobs still queued/running belonged to a dead process —
    their asyncio task no longer exists. Fail them with a clear message."""
    async with SessionLocal() as s:
        res = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["status"].in_(["queued", "running"])
        ).values(status="error", finished_at=_now(),
                 error="server restarted while this job was in flight — "
                       "please resubmit the photo"))
        await s.commit()
        return res.rowcount


def to_b64(prepped_jpeg: bytes) -> str:
    return base64.b64encode(prepped_jpeg).decode("ascii")
