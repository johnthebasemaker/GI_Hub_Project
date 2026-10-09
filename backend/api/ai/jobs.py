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
import contextlib
import datetime as _dt
import json
import logging
import os
import uuid
from typing import Optional

from sqlalchemy import select, update

from ..db import SessionLocal
from ..services.ledger import _MD
from . import client as aic
from . import route as _route
from . import fuzzy, ocr

logger = logging.getLogger("gi.ai.jobs")

ai_jobs_t = _MD.tables["ai_jobs"]
inventory_t = _MD.tables["inventory"]

# ── ⚠️ WHO OWNS A JOB, AND HOW A SWEEP KNOWS (2026-09-02) ────────────────────
#
# THE BUG. `fail_orphans` used to fail EVERY queued/running row at startup, on
# the reasoning that a job in flight died with the process that was running it.
# That holds for one process and breaks for `uvicorn --workers 4`, which is what
# production runs: when a single worker crashed and uvicorn respawned it, the
# new process's lifespan swept away the in-flight jobs of the three workers that
# were still running them. A supervisor five minutes into a six-minute form read
# was told "server restarted while this job was in flight" by a server that had
# not restarted. Invisible at deploy (all four workers boot before any job
# exists); it only bites on a respawn.
#
# WHY OWNERSHIP ALONE DOES NOT FIX IT. `WORKER_ID` says who claimed a row. It
# cannot say whether that process is still alive — the other workers are
# separate OS processes with no shared memory and nothing to ask. Liveness has
# to be written somewhere every worker can read, and the row is that place.
#
# So the owner beats every HEARTBEAT_SECONDS while it works, and the sweep
# reaps only rows that have not been touched for ORPHAN_STALE_SECONDS. A slow
# job keeps beating and is left alone; a dead owner's job stops and is reaped.
#
# ⚠️ THE STALE WINDOW IS NOT THE JOB TIMEOUT, and must not be "fixed" to match
# it. A vision read can legitimately run 900 s (client.VISION_TIMEOUT_S) and
# spend much of that queued on the 2-permit generation semaphore. Sizing the
# sweep off the job's DURATION would mean waiting 15 minutes to reap a corpse;
# sizing it off the BEAT means five missed beats, whatever the job's length.
WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
HEARTBEAT_SECONDS = float(os.environ.get("GI_AI_HEARTBEAT_S", "30"))
ORPHAN_STALE_SECONDS = float(os.environ.get("GI_AI_ORPHAN_STALE_S", "180"))
ORPHAN_SWEEP_SECONDS = float(os.environ.get("GI_AI_ORPHAN_SWEEP_S", "300"))

# ⚠️ HOW LONG EACH LANE ACTUALLY TAKES, MEASURED, BECAUSE THE UI WAS LYING.
#
# The upload card said "This usually takes under a minute" for every lane. On
# 2026-09-02 the operator's own three documents were timed end-to-end through
# these exact prompts and budgets on the dev Mac (qwen2.5vl:7b, 28 of 29 layers
# on GPU, output layer on CPU):
#
#   delivery note, 4 typed items, 1,536 predict ................  92 s
#   consumption sheet, 30 handwritten rows, 3,072 predict ...... 212 s
#   printed GI consumption form, 5 rows, 2,600 predict ......... 399 s
#
# All three read CORRECTLY. The bug reported as "it only loads and never gets
# the results" was a 6½-minute job behind a 60-second promise and an unlabelled
# spinner — the person watching it gave up, which is the rational response to a
# progress indicator that has stopped meaning anything.
#
# These are medians to SHOW somebody, not deadlines to enforce; nothing branches
# on them. They are deliberately generous, because a job that finishes early
# reads as fast and one that overruns its own estimate reads as broken.
#
# ⚠️ THEY ARE HARDWARE-SPECIFIC. The Hetzner CPX42 is CPU-only and will differ;
# `GI_AI_EXPECTED_<KIND>_S` overrides one lane without a deploy.
EXPECTED_SECONDS: dict[str, int] = {
    "ocr_delivery_note": 95,
    "ocr_consumption": 215,
    "ocr_purchase_doc": 240,
    "ocr_consumption_form": 400,          # the Phase 9c printed form (form_jobs)
    "ocr_consumption_second": 90,         # Phase 23b — the skipped rows, in the background
    "tool_identify": 30,
}
DEFAULT_EXPECTED_SECONDS = 180


def expected_seconds(kind: str) -> int:
    env = os.environ.get(f"GI_AI_EXPECTED_{(kind or '').upper()}_S")
    if env:
        try:
            return max(1, int(float(env)))
        except ValueError:
            pass
    return EXPECTED_SECONDS.get(kind, DEFAULT_EXPECTED_SECONDS)


def progress(row) -> dict:
    """The timing facts a poller needs in order to tell the truth.

    ⚠️ `stale` IS COMPUTED HERE, NOT IN THE BROWSER, so the "Interrupted"
    banner and the orphan sweep can never disagree about what a dead job is.
    Two thresholds drifting apart would show a supervisor a Retry button for a
    job that is about to succeed, or leave them watching a spinner for a row
    the server has already given up on.

    A `queued` row is never stale: it has no owner yet, so there is nothing to
    have stopped beating. It ages against `created_at` only so that the sweep
    can eventually reap a row whose process died between the commit and
    `spawn()`.
    """
    now = _now()
    started = row["started_at"] or row["created_at"]
    beat = row["heartbeat_at"] or row["started_at"] or row["created_at"]
    running = row["status"] in ("queued", "running")
    since_beat = (now - beat).total_seconds() if beat else 0.0
    return {
        "started_at": started,
        "heartbeat_at": row["heartbeat_at"],
        # Elapsed is server-computed for the same reason: a phone with a wrong
        # clock must not be able to render "started 3 hours ago".
        "elapsed_s": int((now - started).total_seconds()) if started else 0,
        "expected_s": expected_seconds(row["kind"]),
        "stale": bool(running and row["status"] == "running"
                      and since_beat > ORPHAN_STALE_SECONDS),
        "stale_after_s": int(ORPHAN_STALE_SECONDS),
        # The worker clears `payload_json` when it finishes OR fails, and the
        # orphan sweep clears it too — so a retry that re-queues the row in
        # place is only possible while the image is still held. When it is not,
        # the honest answer is "send the photo again", and the UI says so.
        "can_requeue": bool(running and row["payload_json"]),
    }


async def requeue(session, row, spawner) -> dict:
    """Hand a stalled job back to a live worker. Shared by both OCR lanes.

    ⚠️ THE CLAIM IS THE SAME ATOMIC UPDATE AS EVERYWHERE ELSE, guarded on the
    row still being `running` with the same stale heartbeat we just read. Under
    `--workers 4` the honest race is: the original owner was not dead, only
    quiet, and it finishes between our read and our write. The guard means it
    keeps its result and we return "it came back on its own" instead of
    discarding a six-minute read and starting another.
    """
    from fastapi import HTTPException
    from sqlalchemy import update as _update

    prog = progress(row)
    if row["status"] not in ("queued", "running"):
        raise HTTPException(
            409, f"that job already finished with status '{row['status']}' — "
                 f"there is nothing to re-run.")
    if not prog["stale"]:
        raise HTTPException(
            409, "that job is still being worked on. Re-running it would start "
                 "a second read of the same page on a server that reads one at "
                 "a time, which makes the wait longer, not shorter.")
    if not prog["can_requeue"]:
        raise HTTPException(
            409, "the photograph is no longer held for this job, so it cannot "
                 "be re-run from here. Upload it again.")

    res = await session.execute(_update(ai_jobs_t).where(
        ai_jobs_t.c["id"] == row["id"],
        ai_jobs_t.c["status"] == "running",
        ai_jobs_t.c["heartbeat_at"] == row["heartbeat_at"],
    ).values(status="queued", started_at=None, finished_at=None,
             worker_id=None, heartbeat_at=None, error=None))
    await session.commit()
    if res.rowcount == 0:
        return {"job_id": row["id"], "status": "unchanged",
                "message": "that job moved on while you were asking — poll it "
                           "again rather than starting a second read."}
    spawner(row["id"])
    return {"job_id": row["id"], "status": "queued",
            "message": "Re-reading the page."}


JOB_KINDS = ("ocr_consumption", "ocr_delivery_note", "tool_identify",
             # QSEP slice 6 — a SCANNED PR/PO. Needed because a real
             # purchase order in this project (PO#4710003121) is a scan with
             # zero extractable text, which pdfplumber can never read.
             "ocr_purchase_doc")

# ⚠️ THE OUTPUT BUDGET IS PER LANE, AND ONE NUMBER FOR ALL OF THEM WAS A BUG.
#
# Every image lane used to share `num_predict=1024`, which is a property of the
# ANSWER, not of the model — and the four lanes have answers of wildly different
# size. Measured 2026-09-01 on the operator's own files:
#
#   a delivery note ....... 4 items x 3 fields ... ~350 tokens ... finished
#   a consumption log .... 30 rows x 9 fields ... ~2,400 tokens ... CUT at 13
#
# So the DN lane always fitted and the consumption lane never could, which is
# precisely why the failure presented as "the model cannot read free-form
# tables" and survived a whole phase. It read them fine; it was interrupted.
#
# `ocr.salvage_truncated_json` now rescues a clipped reply rather than throwing
# the whole page away, and these budgets are what stop it being needed. Both
# halves matter: a budget alone still loses the tail of an unusually long sheet,
# and salvage alone silently drops rows nobody knows are missing.
# ⚠️ SINCE SLICE 11e THESE ARE DERIVED, NOT DECLARED. The numbers live in
# `route.POLICIES` — one owner for every per-lane budget — and this dict is a
# read-through so the name, the suite pins and the reasoning above all keep
# working. Two literal copies of "3072" is precisely how a budget and the lane
# it belongs to drift apart, which is the bug this comment block describes.
NUM_PREDICT = {k: v.num_predict for k, v in _route.POLICIES.items()
               if v.vision and k != "ocr_consumption_form"}
# ⚠️ NOT `route.DEFAULT_POLICY.num_predict`, which is 512 — the CHAT budget.
# Binding it there silently halved the fallback for an unlisted VISION lane
# from 1024 to 512, which is the same class of mistake as the one-budget-for-
# four-lanes bug above: a number that looks like a sensible default until it
# meets a page. Every lane is named in `route.POLICIES` now, so nothing reads
# this; it stays, at its own value, so that an unlisted lane added in a hurry
# gets a vision-sized budget rather than a chat-sized one.
DEFAULT_NUM_PREDICT = 1024


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


async def create_job(session, *, kind: str, actor: str, site_id: str | None,
                     image_b64: str) -> int:
    """Insert a queued job row. The caller commits and then spawns run_job.

    ⚠️ THE IMAGE LIVES ONLY UNTIL THE JOB FINISHES. `run_job` sets
    `payload_json = NULL` on both terminal transitions, because this column
    holds a base64 photograph and nothing reads it after the worker has. Kept,
    it made `ai_jobs` grow by the size of every photo ever OCR'd — and it made
    every sequential scan of the table (the orphan sweep, the summary cache)
    drag those photographs through shared buffers to read a status column.
    Alembic b8d3f1a72c94 indexes those scans and reclaims the existing rows.
    """
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
        # the paper's date as written travels with the rows — until Phase 21d's
        # follow-up it was dropped here, so a photographed page always opened
        # on TODAY's date whatever the paper said
        return {"rows": fuzzy.resolve_rows(parsed["rows"], inventory),
                "date_text": str(parsed.get("date_text") or "").strip()}
    if kind == "ocr_purchase_doc":
        # A scanned purchase document carries the EXACT material code, so it
        # is matched on the code first and only falls back to fuzzy text.
        # That ordering matters: fuzzy-matching a description when a code was
        # printed on the page throws away the one unambiguous key the
        # document had, and "ELECTRIC INSULATION" resembles several rows in
        # the master while GI-7000009 resembles exactly one.
        by_code = {str(r["Material_Code"] or "").strip().upper(): r
                   for r in inventory if r["Material_Code"]}
        exact, needs_fuzzy = [], []
        for it in parsed["items"]:
            hit = by_code.get(it.get("material_code", ""))
            if hit:
                exact.append({**it, "SAP_Code": hit["SAP_Code"],
                              "Equipment_Description": hit["Equipment_Description"],
                              "uom": it.get("uom") or hit["UOM"] or "",
                              "match_state": "auto", "match_source": "code"})
            else:
                needs_fuzzy.append(it)
        resolved = fuzzy.resolve_rows(needs_fuzzy, inventory) if needs_fuzzy else []
        for r in resolved:
            r.setdefault("match_source", "text")
        return {"doc_type": parsed.get("doc_type", ""),
                "header": parsed["header"], "items": exact + resolved}
    return {"header": parsed["header"],
            "items": fuzzy.resolve_rows(parsed["items"], inventory)}


async def run_job(job_id: int) -> None:
    """The worker. Own sessions (the request that spawned us is long gone);
    never raises — every failure lands in ai_jobs.error for the poller."""
    async with SessionLocal() as s:
        claimed = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["id"] == job_id,
            ai_jobs_t.c["status"] == "queued",
        ).values(**await claim_values()))
        if claimed.rowcount == 0:  # raced by another worker — theirs now
            await s.rollback()
            return
        row = (await s.execute(select(ai_jobs_t.c["kind"], ai_jobs_t.c["payload_json"])
                               .where(ai_jobs_t.c["id"] == job_id))).first()
        await s.commit()

    kind = row.kind
    # ⚠️ THE BEAT STARTS BEFORE THE SEMAPHORE, not after it. A third concurrent
    # job waits on `GEN_SEMAPHORE` (2 permits) for as long as the two ahead of
    # it take — minutes — with status already 'running'. Starting the heartbeat
    # after the wait would let the sweep reap a job that is patiently queued,
    # which is the same class of mistake as the bug this replaced.
    beat = asyncio.create_task(_beat(job_id))
    try:
        err = await _vision_preflight()
        if err:
            raise RuntimeError(err)
        image_b64 = json.loads(row.payload_json or "{}").get("image_b64", "")
        # Measure the image ONCE and size the context window from it. Decoding
        # a <=1.4 MB base64 payload costs about a millisecond; getting num_ctx
        # wrong costs the Ollama runner (see client.vision_num_ctx).
        img_tokens = ocr.estimate_image_tokens(
            base64.b64decode(image_b64)) if image_b64 else None

        # ⚠️ THROUGH THE GATEWAY SINCE SLICE 11e, AND THAT FIXED A REAL GAP.
        #
        # These four lanes called `aic.generate(..., images=[...])` directly,
        # which means they never reached `client.vision_json` — and
        # `vision_json` is where the cloud seam lives. So the fallback added in
        # slice 11b, described as covering "the vision lane", actually covered
        # exactly ONE of the five: the Phase 9d execution form, which is the
        # only caller that had ever used `vision_json`. The consumption sheet,
        # the delivery note, the scanned PO and Smart Scan had no cloud path at
        # all, and nothing said so.
        #
        # `route.call_vision` calls `vision_json`, which calls
        # `vision_num_ctx` — the indirection is the point (ARCHITECTURE §7a):
        # the gateway decides WHICH engine and HOW MANY TRIES; the client
        # decides how large the context window must be for the image in hand.
        #
        # `temperature=0.1` is passed explicitly to preserve exactly what these
        # lanes did before. `vision_json` defaults to 0.0 (the form lane's
        # value); changing these to match would be a behavioural change to a
        # working OCR path, and it does not belong in a routing commit.
        if kind == "tool_identify":
            # Smart Scan tier-2 (AI-4): catalogue-constrained when the
            # tool_catalogue has rows, freeform naming when it's empty.
            async with SessionLocal() as s:
                cat_t = _MD.tables["tool_catalogue"]
                catalogue = [dict(m) for m in (await s.execute(select(
                    cat_t.c["class_name"], cat_t.c["display_name"]))).mappings()]
            out = await _route.call_vision(
                kind, "Identify the tool.", system=ocr.tool_prompt(catalogue),
                image_b64=image_b64, image_tokens=img_tokens, temperature=0.1)
            result = ocr.parse_tool_reply(out.text, catalogue)
        else:
            out = await _route.call_vision(
                kind, ocr.USER_PROMPTS[kind], system=ocr.SYSTEM_PROMPTS[kind],
                image_b64=image_b64, image_tokens=img_tokens, temperature=0.1)
            parsed = ocr.parse_vision_reply(kind, out.text)

        # Phase 23b (ruling Q23-3) — the rows the first read skipped are read
        # again in the background. Decided and cut HERE, while the image is
        # still in hand; the first read's rows are written as `done` below
        # BEFORE that second call starts, so the store keeper waits 0 s for it.
        second = _plan_second_read(kind, parsed if kind != "tool_identify" else None, image_b64)
        async with SessionLocal() as s:
            if kind != "tool_identify":
                result = await _resolve(kind, parsed, s)
            if second:
                result["second_read"] = second[2]
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="done", finished_at=_now(),
                                    payload_json=None,
                                    result_json=json.dumps(result, ensure_ascii=False)))
            await s.commit()
    except Exception as e:
        second = None
        logger.warning("ai job %s failed: %s", job_id, e)
        async with SessionLocal() as s:
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="error", finished_at=_now(),
                                    payload_json=None,
                                    error=str(e)[:500]))
            await s.commit()
    finally:
        await stop_beat(beat)
    if second:
        await _run_second_read(job_id, kind, parsed, result, second)



def _plan_second_read(kind: str, parsed: Optional[dict], image_b64: str):
    """(gaps, strip_b64, state) when the consumption page has printed rows the
    first read did not return, else None. Off with GI_OCR_SECOND_READ=0."""
    if kind != "ocr_consumption" or not parsed or not image_b64:
        return None
    if os.environ.get("GI_OCR_SECOND_READ", "1").strip() in ("0", "false", "no"):
        return None
    from . import second_read as SR
    try:
        rows = parsed.get("rows") or []
        cut = SR.strip_image(base64.b64decode(image_b64), SR.missing_rows(rows),
                             read=SR.read_snos(rows))
    except Exception as e:  # noqa: BLE001 — a failed cut never costs the first read
        logger.warning("second read: could not cut the strips: %s", e)
        return None
    if not cut:
        return None
    strip, meta = cut
    gaps = meta["rows"]          # the gap rows with handwriting — the only ones asked about
    state = SR.initial_state(gaps, meta, expected_seconds("ocr_consumption_second"),
                             _now().isoformat(timespec="seconds"))
    return gaps, base64.b64encode(strip).decode(), state


async def _run_second_read(job_id: int, kind: str, parsed: dict, result: dict, second) -> None:
    """Read the gap strips, add what they hold, record the outcome. Never raises."""
    import time as _time

    from . import second_read as SR
    gaps, strip_b64, state = second
    t0 = _time.perf_counter()
    try:
        out = await _route.call_vision(
            SR.LANE, SR.user_prompt(gaps), system=SR.SYSTEM_PROMPT,
            image_b64=strip_b64, image_tokens=ocr.estimate_image_tokens(base64.b64decode(strip_b64)),
            temperature=0.1)
        rows2 = SR.parse_rows(out.text)
        added = SR.accept(rows2, gaps, parsed.get("rows") or [])
        resolved: list[dict] = []
        if added:
            async with SessionLocal() as s:
                resolved = (await _resolve(kind, {"rows": added, "date_text": ""}, s))["rows"]
            for r, a in zip(resolved, added):
                r["sno"] = a.get("sno")
                r["second_read"] = True
        state = {**state, "status": "done", "added": resolved,
                 "added_snos": [r.get("sno") for r in resolved],
                 "seconds": round(_time.perf_counter() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        logger.warning("ai job %s second read failed: %s", job_id, e)
        state = {**state, "status": "error", "error": str(e)[:200],
                 "seconds": round(_time.perf_counter() - t0, 1)}
    try:
        async with SessionLocal() as s:
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(result_json=json.dumps({**result, "second_read": state},
                                                           ensure_ascii=False)))
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("ai job %s: could not record the second read: %s", job_id, e)


def spawn(job_id: int) -> None:
    """Fire-and-forget worker task (kept referenced so GC can't collect it)."""
    task = asyncio.create_task(run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


_RUNNING: set[asyncio.Task] = set()


async def claim_values() -> dict:
    """The columns every worker stamps when it wins the queued→running race.

    Shared by `jobs.run_job` and `form_jobs.run_job` so the two cannot drift:
    a lane that claimed a row without stamping a heartbeat would be reaped by
    the sweep 180 s later, mid-read, which is the original bug wearing the
    fix's clothes.
    """
    now = _now()
    return {"status": "running", "started_at": now,
            "worker_id": WORKER_ID, "heartbeat_at": now}


async def _beat(job_id: int) -> None:
    """Touch `heartbeat_at` while this process is still working on `job_id`.

    ⚠️ NEVER RAISES, and never lets a database hiccup end the job it is
    describing. A missed beat costs nothing until five of them accumulate; a
    heartbeat task that propagated an exception would cancel the read it exists
    to protect, which is strictly worse than the condition it reports.

    The `status = 'running'` guard means a beat that lands after the job has
    finished updates nothing, so a late beat cannot resurrect a terminal row.
    """
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with SessionLocal() as s:
                await s.execute(update(ai_jobs_t).where(
                    ai_jobs_t.c["id"] == job_id,
                    ai_jobs_t.c["status"] == "running",
                ).values(heartbeat_at=_now()))
                await s.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.debug("heartbeat for job %s missed: %s", job_id, e)


async def stop_beat(task: asyncio.Task | None) -> None:
    """Cancel a heartbeat and wait for it to actually stop.

    Awaited in a `finally`, so it runs on every exit path including the ones
    that raise. A beat left running would keep a finished job looking alive to
    the sweep — harmless, since the beat's own `status = 'running'` guard makes
    it a no-op on a terminal row, but it would also leak a task per job for the
    life of the process.
    """
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def sweep_orphans() -> int:
    """Fail jobs whose owner is gone. NOT jobs that are merely slow.

    ⚠️ THIS REPLACED A SWEEP THAT FAILED EVERY UNFINISHED ROW. See the module
    header for why that was wrong under `--workers 4`. The predicate is now
    "nobody has touched this for ORPHAN_STALE_SECONDS", which is true of a
    dead owner and false of a live one, however long its job runs.

    `COALESCE(heartbeat_at, started_at, created_at)` covers all three ages a
    row can have: beating (running normally), claimed but not yet beaten (the
    first 30 s), and never claimed at all (a `queued` row whose process died
    between the commit and `spawn`, which nothing else would ever reap).

    Runs at startup AND on a timer, because a worker that dies while its
    siblings stay up leaves an orphan no startup ever sees — uvicorn respawns
    the dead worker, but the respawn happens in seconds, long before the row
    goes stale. Without the timer the fix would trade one silent failure for
    another.
    """
    from sqlalchemy import func as _func
    cutoff = _now() - _dt.timedelta(seconds=ORPHAN_STALE_SECONDS)
    async with SessionLocal() as s:
        res = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["status"].in_(["queued", "running"]),
            _func.coalesce(ai_jobs_t.c["heartbeat_at"],
                           ai_jobs_t.c["started_at"],
                           ai_jobs_t.c["created_at"]) < cutoff,
        ).values(status="error", finished_at=_now(), payload_json=None,
                 error="this job stopped responding (the process running it "
                       "went away) — please resubmit the photo"))
        await s.commit()
        return res.rowcount


# Back-compat alias. `fail_orphans` was the startup-sweep name for two phases
# and reads as "fail them all", which is precisely the behaviour that was
# wrong; the new name says what the function now decides.
fail_orphans = sweep_orphans


async def orphan_sweep_loop() -> None:
    """Periodic sweep. One per worker; the UPDATE is idempotent and guarded,
    so four workers racing it is harmless — whoever runs first reaps, the
    others match nothing.

    Also carries the `rate_buckets` housekeeping, because it is the only loop in
    the process whose job is already "delete rows nothing will read again" and a
    second timer for one DELETE would be ceremony.
    """
    while True:
        try:
            await asyncio.sleep(ORPHAN_SWEEP_SECONDS)
            n = await sweep_orphans()
            if n:
                logger.info("orphan sweep failed %s stranded job(s)", n)
            try:
                from ..ratelimit import sweep_rate_buckets
                async with SessionLocal() as s:
                    await sweep_rate_buckets(s)
            except Exception as e:                          # noqa: BLE001
                logger.debug("rate-bucket sweep skipped: %s", e)
            # ⚠️ TRACE RETENTION TAKES THE DAILY CLAIM; THE TWO SWEEPS ABOVE DO
            # NOT NEED ONE. Those are idempotent DELETEs of rows nothing will
            # read again, so four workers racing them is waste and nothing more.
            # This one runs once a day rather than every five minutes, and a
            # claim is how a once-a-day job stays once-a-day under
            # `--workers 4` — the exact shape of the bug that had three daily
            # loops sending four copies of every message (P10-2).
            try:
                from ..services import dailyjob as _dj
                from . import trace as _tr
                due = _now() - _dt.timedelta(days=1)
                async with SessionLocal() as s:
                    won = await _dj.claim(s, "ai_trace_retention", due)
                if won:
                    n = await _tr.sweep_retention()
                    if n:
                        logger.info("ai_traces retention removed %s span(s)", n)
                    # Same claim, same reason: the answer cache's stale rows are
                    # already unreachable (the manual/prompt hashes see to that),
                    # so this only reclaims the space.
                    from . import answer_cache as _ac
                    m = await _ac.sweep()
                    if m:
                        logger.info("ai_answer_cache dropped %s stale answer(s)", m)
            except Exception as e:                          # noqa: BLE001
                logger.debug("trace retention skipped: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.warning("orphan sweep loop: %s", e)


def to_b64(prepped_jpeg: bytes) -> str:
    return base64.b64encode(prepped_jpeg).decode("ascii")
