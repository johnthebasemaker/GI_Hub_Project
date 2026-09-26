"""
backend/api/instance.py — which environment this process IS, and the tripwire
that refuses a request prepared for the other one (rule 17).

Two things, both deliberately small:

  GET /instance     unauthenticated. {instance, label, …}. The ONLY source the
                    SPA uses to decide whether to paint the Practice banner —
                    never the login toggle's own state (vector V10: a person who
                    believes they are practising while they are on Live is the
                    contamination that costs money).

  instance_guard    ASGI middleware. A request that DECLARES an environment in
                    `X-GI-Instance` and declares the other one is refused 409
                    before any route runs.

⚠️ THE HEADER IS AN ASSERTION, NEVER A SELECTOR. Nothing here chooses a
database — this process has exactly one and learned it from its own
environment at import. The header exists for vector V4: the SPA's offline queue
replays a stored mutation against whatever base is CURRENT, under whatever
token is current, so a receipt practised offline would reach Live under a
perfectly valid Live session and every server-side wall would wave it through.
The SPA stamps each queued entry with the environment it was made in; this
guard is what makes that stamp binding rather than advisory.

An ABSENT header passes: curl, the Meta webhook, pre-rule-17 native builds and
the E2E harness send none, and none of them is the risk. A PRESENT header that
is garbled is refused — a declaration nobody can read is not one to guess at.

Method-agnostic and path-agnostic by construction, for the reason readonly.py
gives: a per-endpoint check fails open on the next endpoint somebody adds.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from .config import INSTANCE_LABELS, instance, normalize_instance
from .db import get_session

HEADER = "x-gi-instance"

# Paths that answer "what are you?" must answer a client that is confused
# about it — that is the whole point of asking.
_EXEMPT = frozenset({"/instance", "/health"})

# app_settings keys the Practice build/reset stamps. Live never writes them.
PRACTICE_KEYS = ("practice_dataset_version", "practice_seeded_at",
                 "practice_reset_at", "practice_reset_by")

router = APIRouter(tags=["instance"])


def declared_mismatch(declared: str | None, actual: str) -> bool:
    """The whole decision, pure, so suite TR tests every branch directly."""
    if declared is None:
        return False
    # An EMPTY declaration is garbled, not absent: `normalize_instance('')`
    # reads as Live for GI_INSTANCE's sake, but a client that sent the header
    # meant to say something and did not.
    if not declared.strip():
        return True
    return normalize_instance(declared) != actual


async def instance_guard(request, call_next):
    declared = request.headers.get(HEADER)
    if declared is not None:
        path = request.url.path.rstrip("/") or "/"
        actual = instance()
        if path not in _EXEMPT and declared_mismatch(declared, actual):
            meant = INSTANCE_LABELS.get(normalize_instance(declared) or "",
                                        repr(declared))
            return JSONResponse(status_code=409, content={
                "detail": (f"this request was prepared for {meant} but reached "
                           f"{INSTANCE_LABELS[actual]} — it was not applied"),
                "instance": actual,
            })
    return await call_next(request)


@router.get("/instance", summary="Which environment this API is (Live / Practice)")
async def get_instance(session: AsyncSession = Depends(get_session)):
    inst = instance()
    out = {"instance": inst, "label": INSTANCE_LABELS[inst],
           "practice": inst == "training"}
    if inst == "training":
        from backend import models
        t = models.Base.metadata.tables["app_settings"]
        try:
            rows = (await session.execute(
                select(t.c["key"], t.c["value"]).where(t.c["key"].in_(PRACTICE_KEYS))
            )).all()
            out.update({k.replace("practice_", ""): v for k, v in rows})
        except Exception:  # noqa: BLE001 — identity must answer even if the DB is mid-reset
            pass
    return out
