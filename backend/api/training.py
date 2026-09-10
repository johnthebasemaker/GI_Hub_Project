"""
backend/api/training.py — Track 5: the training hub and the SOFT gate.

⚠️ THE GATE IS SOFT, AND THAT IS AN OPERATOR RULING (Q5.1), NOT A SHORTCUT.

Phase 9 made photographing a paper form the PRIMARY way consumption is filed. A
hard gate would mean a supervisor standing in a plant at 06:00, holding a filled
sheet, cannot file it because they have not watched a six-minute video. This
project has twice ruled against exactly that shape:

  · FEFO consumption is allow-and-log, not hard-block (locked 2026-06-30);
  · the MTC gate was MOVED OUT of receipt and dispatch on 2026-08-12, because
    "refusing to record something that has physically happened is the one thing
    an inventory system must never do".

So an untrained user meets an unskippable interstitial, and may proceed with
"Watch Later". The control is VISIBILITY, not refusal: the deferral is recorded,
counted, and shown to the HOD with a name against it. A supervisor who defers
eleven times is a conversation, not a locked account.

⚠️ WHICH MEANS THE GATE STATE IS ADVISORY AND THE UI IS ITS ONLY ENFORCER.
`GET /training/gate/{feature}` reports; nothing here refuses an upload. If a
future slice makes the gate hard, it must be enforced SERVER-SIDE in
`POST /execution/ocr/upload` — a UI-only gate is not a control — and it will
need the admin override the 06:00 case demands.

────────────────────────────────────────────────────────────────────────────
⚠️ COMPLIANCE IS PER (user, module, VERSION). Bumping `training_modules.version`
invalidates every acknowledgement of the previous one by construction. Keyed on
(user, module) alone, re-recording a tutorial because the workflow changed would
leave everybody certified against a video they have never seen — worse than no
record at all, because it looks like evidence.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user, require_level, require_roles
from .db import get_session
from .services.ledger import _MD, write_audit

router = APIRouter(tags=["training"])

modules_t = _MD.tables["training_modules"]
assets_t = _MD.tables["training_assets"]
compliance_t = _MD.tables["training_compliance"]

# BCP-47. `ta-Latn` is Tanglish — Tamil written in Latin script, which is what
# people actually speak on site and what the avatar videos are recorded in.
LANGUAGES = ("en", "ta", "ta-Latn", "ar")

# What fraction of a video counts as watched. Not 100%: a player that must reach
# the final frame fails on a seek, a buffer stall, or a 3-second outro, and a
# gate people cannot satisfy honestly is one they satisfy dishonestly.
COMPLETE_AT = 0.90


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _roles_of(module: dict) -> set[str]:
    return {r.strip().lower()
            for r in str(module.get("required_roles") or "").split(",")
            if r.strip()}


async def _module_by_key(session: AsyncSession, key: str) -> dict | None:
    row = (await session.execute(select(modules_t)
           .where(modules_t.c["module_key"] == key))).mappings().first()
    return dict(row) if row else None


async def _state(session: AsyncSession, username: str,
                 module: dict) -> dict:
    """This user's standing against THIS version of the module."""
    row = (await session.execute(select(compliance_t).where(
        compliance_t.c["username"] == username,
        compliance_t.c["module_id"] == module["id"],
        # ⚠️ THE VERSION IS IN THE LOOKUP, not only in the unique key. Reading
        # without it would find last version's acknowledgement and report the
        # user as trained on a video that no longer exists.
        compliance_t.c["module_version"] == module["version"],
    ))).mappings().first()
    rec = dict(row) if row else {}
    return {
        "module_key": module["module_key"], "title": module["title"],
        "version": module["version"],
        "acknowledged": bool(rec.get("acknowledged_at")),
        "completed": bool(rec.get("completed_at")),
        "watched_seconds": int(rec.get("watched_seconds") or 0),
        "deferrals": int(rec.get("deferrals") or 0),
        "deferred_at": rec.get("deferred_at"),
        "acknowledged_at": rec.get("acknowledged_at"),
    }


async def _upsert(session: AsyncSession, username: str, module: dict,
                  **values) -> None:
    """Insert-or-update this user's row for THIS module version.

    `ON CONFLICT` on the natural key rather than a read-then-write: two tabs
    posting progress at once is ordinary, and a check-then-insert would raise
    an IntegrityError on the loser for no reason.
    """
    stmt = pg_insert(compliance_t).values(
        username=username, module_id=module["id"],
        module_version=module["version"], updated_at=_now(), **values)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=["username", "module_id", "module_version"],
        set_={**values, "updated_at": _now()}))


# ── reading ─────────────────────────────────────────────────────────────────
@router.get("/training/modules", summary="Modules that apply to me, and my state")
async def my_modules(user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    rows = [dict(m) for m in (await session.execute(
        select(modules_t).where(modules_t.c["active"] == 1)
        .order_by(modules_t.c["id"]))).mappings()]
    out = []
    for mod in rows:
        req = _roles_of(mod)
        # A module with no `required_roles` applies to everybody; one that names
        # roles applies only to those. Admin sees every module regardless, so an
        # administrator can review what their staff are being asked to watch.
        if req and user["role"] not in req and user["role"] != "admin":
            continue
        assets = [dict(a) for a in (await session.execute(
            select(assets_t.c["language"], assets_t.c["storage_uri"],
                   assets_t.c["captions_uri"], assets_t.c["duration_s"])
            .where(assets_t.c["module_id"] == mod["id"])
            .order_by(assets_t.c["language"]))).mappings()]
        out.append({**await _state(session, user["username"], mod),
                    "description": mod["description"],
                    "required_roles": sorted(req),
                    "gates_feature": mod["gates_feature"],
                    "mandatory": bool(req) and user["role"] in req,
                    # No asset yet = the videos are not published. Said plainly
                    # rather than rendering a player pointed at nothing.
                    "assets": assets, "published": bool(assets)})
    return {"modules": out, "languages": list(LANGUAGES),
            "complete_at_pct": int(COMPLETE_AT * 100)}


@router.get("/training/gate/{feature}",
            summary="May this user use `feature` — and if not, what to show")
async def gate(feature: str, user: dict = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)):
    """⚠️ ADVISORY. Reports; never refuses. See the module docstring.

    `allowed` is ALWAYS true today — a soft gate that returned false would be a
    hard gate with extra steps. `show_interstitial` is what the UI acts on.
    """
    rows = [dict(m) for m in (await session.execute(
        select(modules_t).where(modules_t.c["gates_feature"] == feature,
                                modules_t.c["active"] == 1))).mappings()]
    pending = []
    for mod in rows:
        req = _roles_of(mod)
        if req and user["role"] not in req:
            continue
        st = await _state(session, user["username"], mod)
        if not st["acknowledged"]:
            pending.append(st)
    return {"feature": feature,
            # Soft gate: work is never blocked. Operator ruling Q5.1.
            "allowed": True,
            "show_interstitial": bool(pending),
            "pending": pending}


# ── writing ─────────────────────────────────────────────────────────────────
class ProgressIn(BaseModel):
    module_key: str
    watched_seconds: int = Field(ge=0)
    language: Optional[str] = None


@router.post("/training/progress", summary="Record watch progress (beacon)")
async def progress(body: ProgressIn = Body(...),
                   user: dict = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    mod = await _module_by_key(session, body.module_key)
    if mod is None:
        raise HTTPException(404, f"no training module {body.module_key!r}")
    dur = (await session.execute(
        select(func.max(assets_t.c["duration_s"]))
        .where(assets_t.c["module_id"] == mod["id"]))).scalar()
    prev = await _state(session, user["username"], mod)
    # ⚠️ MONOTONIC. Progress only ever goes up: a beacon that arrives out of
    # order, or a second tab starting the video again, must not erase what
    # somebody has already watched.
    watched = max(int(body.watched_seconds), prev["watched_seconds"])
    complete = bool(dur and watched >= COMPLETE_AT * float(dur))
    await _upsert(session, user["username"], mod,
                  watched_seconds=watched,
                  language=(body.language or prev.get("language")),
                  **({"completed_at": _now()}
                     if complete and not prev["completed"] else {}))
    await session.commit()
    return {**await _state(session, user["username"], mod),
            "duration_s": int(dur) if dur else None}


class AckIn(BaseModel):
    module_key: str
    language: Optional[str] = None


@router.post("/training/acknowledge", summary="I have watched and understood it")
async def acknowledge(body: AckIn = Body(...),
                      user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    """The compliance record. Audited, because it is the evidence.

    ⚠️ REFUSED UNTIL THE VIDEO IS ACTUALLY WATCHED. An acknowledgement that can
    be clicked on arrival records nothing but a click, and a compliance table
    full of those is worse than an empty one — it would be produced as proof.
    The bar is `COMPLETE_AT` of the asset's duration; a module with no published
    asset cannot be acknowledged at all.
    """
    mod = await _module_by_key(session, body.module_key)
    if mod is None:
        raise HTTPException(404, f"no training module {body.module_key!r}")
    dur = (await session.execute(
        select(func.max(assets_t.c["duration_s"]))
        .where(assets_t.c["module_id"] == mod["id"]))).scalar()
    if not dur:
        raise HTTPException(
            409, "this module has no published video yet, so there is nothing "
                 "to acknowledge. Ask your admin to publish it.")
    st = await _state(session, user["username"], mod)
    if st["watched_seconds"] < COMPLETE_AT * float(dur):
        raise HTTPException(
            409, f"watch at least {int(COMPLETE_AT * 100)}% of the video before "
                 f"acknowledging it ({st['watched_seconds']}s of {int(dur)}s so far)")
    await _upsert(session, user["username"], mod,
                  acknowledged_at=_now(), completed_at=(st["acknowledged_at"]
                                                        or _now()),
                  language=(body.language or None))
    await write_audit(session, user["username"], "TRAINING_ACKNOWLEDGED",
                      "training_compliance",
                      f"{mod['module_key']} v{mod['version']}")
    await session.commit()
    return await _state(session, user["username"], mod)


class DeferIn(BaseModel):
    module_key: str


@router.post("/training/defer", summary='"Watch later" — recorded, not punished')
async def defer(body: DeferIn = Body(...),
                user: dict = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    """⚠️ THIS IS THE SOFT GATE'S ENTIRE MECHANISM.

    Nothing is refused. The deferral is written down and counted, and the HOD
    dashboard shows it with a name against it. A supervisor who defers eleven
    times is a conversation somebody can have; a supervisor locked out at 06:00
    is a consumption entry that never gets filed.
    """
    mod = await _module_by_key(session, body.module_key)
    if mod is None:
        raise HTTPException(404, f"no training module {body.module_key!r}")
    st = await _state(session, user["username"], mod)
    await _upsert(session, user["username"], mod,
                  deferred_at=_now(), deferrals=st["deferrals"] + 1)
    await write_audit(session, user["username"], "TRAINING_DEFERRED",
                      "training_compliance",
                      f"{mod['module_key']} v{mod['version']} "
                      f"count={st['deferrals'] + 1}")
    await session.commit()
    return {**await _state(session, user["username"], mod), "allowed": True}


# ── the HOD dashboard ───────────────────────────────────────────────────────
@router.get("/training/compliance",
            summary="Who has watched what (HOD/admin)")
async def compliance_dashboard(module_key: Optional[str] = Query(None),
                               user: dict = Depends(require_level(2)),
                               session: AsyncSession = Depends(get_session)):
    """One row per person who OUGHT to have watched it, not per row that exists.

    ⚠️ DRIVEN FROM `users`, NOT FROM `training_compliance`. Listing the
    compliance table would show only the people who have engaged with the
    module at all — so somebody who has never opened it, the person most worth
    knowing about, would be invisible. The absence is the finding.
    """
    users_t = _MD.tables["users"]
    mods = [dict(m) for m in (await session.execute(
        select(modules_t).where(modules_t.c["active"] == 1)
        .order_by(modules_t.c["id"]))).mappings()
        if not module_key or m["module_key"] == module_key]

    # A scoped HOD sees their own site; admin and unscoped roles see everyone.
    site = (user.get("site_id") or "").strip()
    ustmt = select(users_t.c["username"], users_t.c["role"], users_t.c["Site_ID"])
    if user["role"] != "admin" and site:
        ustmt = ustmt.where(users_t.c["Site_ID"] == site)
    people = [dict(u) for u in (await session.execute(ustmt)).mappings()]

    out = []
    for mod in mods:
        req = _roles_of(mod)
        rows = []
        for p in people:
            if req and p["role"] not in req:
                continue
            st = await _state(session, p["username"], mod)
            rows.append({"username": p["username"], "role": p["role"],
                         "site_id": p["Site_ID"], **st})
        out.append({
            "module_key": mod["module_key"], "title": mod["title"],
            "version": mod["version"], "required_roles": sorted(req),
            "people": sorted(rows, key=lambda r: (r["acknowledged"],
                                                  -r["deferrals"],
                                                  r["username"])),
            "acknowledged": sum(1 for r in rows if r["acknowledged"]),
            "outstanding": sum(1 for r in rows if not r["acknowledged"]),
            "deferrals": sum(r["deferrals"] for r in rows),
        })
    return {"modules": out}


# ── the bytes (Phase 13c) ───────────────────────────────────────────────────
# ⚠️ RULING Q5.3 SAYS AN ASSET IS A URI, NOT AN UPLOAD, and this is what that
# URI can point at. A `LargeBinary` column would put a 200-600 MB tutorial set
# into every nightly `pg_dump` for ever AND could not serve HTTP Range — so the
# viewer could not seek. A `<video>` element that cannot seek is one the
# assistant's deep link cannot use, because the whole promise of "Watch it" is
# landing on the second where the step happens.
#
# ⚠️ SO THIS ROUTE'S REASON FOR EXISTING IS `206`, NOT CONVENIENCE. Nothing in
# the backend served a byte range before it; the only place the words appeared
# was the comment above explaining why the column is a URI.

_MEDIA_KINDS = {"mp4": ("video/mp4", ".mp4"),
                "vtt": ("text/vtt; charset=utf-8", ".vtt")}
# One chunk per Range request. 1 MiB is a compromise measured against nothing in
# particular and chosen for a reason that is: it is small enough that a seek is
# answered promptly on plant Wi-Fi and large enough that a 9 MB tutorial is not
# 9,000 round trips.
_MEDIA_CHUNK = 1024 * 1024


def _media_dir():
    """Where the rendered tutorials live.

    Shares `GI_TUTORIAL_DIR` with the deep-link matcher deliberately: the
    manifest that says a beat happens at 61.2 s and the MP4 that has a frame
    there are the same render, and two settings pointing at different
    directories is a way to serve one tutorial's timings over another's video.
    """
    import os
    import pathlib as _pl
    root = _pl.Path(__file__).resolve().parents[2]
    return _pl.Path(os.environ.get("GI_TUTORIAL_DIR")
                    or (root / "docs" / "tutorials" / "out"))


def _parse_range(header: str, size: int):
    """`bytes=start-end` → (start, end) inclusive, or None.

    Only the single-range form. A multipart range response is a different
    content type and no `<video>` element asks for one; answering a request we
    do not really implement with a 206 would be worse than ignoring the header
    and sending 200, which is what returning None does.
    """
    raw = str(header or "").strip()
    if not raw.lower().startswith("bytes=") or "," in raw:
        return None
    spec = raw[6:].strip()
    try:
        if spec.startswith("-"):                 # last N bytes
            n = int(spec[1:])
            if n <= 0:
                return None
            return max(0, size - n), size - 1
        start_s, _, end_s = spec.partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


# ⚠️ A `<video src>` CANNOT SEND AN AUTHORIZATION HEADER, and that is the whole
# reason this ticket exists (found 2026-09-10 by watching a player report
# `networkState: 3` — NETWORK_NO_SOURCE — against a route that curl streamed
# perfectly). The element issues its own plain GET; there is no hook to attach a
# bearer token to, and the response was a 401 the page had no way to show.
#
# THREE OPTIONS WERE WEIGHED, and two of them break something:
#
#   * fetch the file with the axios client and play a blob URL — keeps the
#     fence untouched and DESTROYS Range. The whole 9 MB downloads before the
#     first frame, and seeking to 61.2 s is exactly what the deep link is for.
#   * put the session JWT in the query string — a full-session credential in a
#     URL, which lands in server logs, browser history and any Referer. No.
#   * a PURPOSE-SCOPED, SHORT-LIVED ticket, which is what this is.
#
# The ticket names ONE module, ONE language and ONE user, expires in ten
# minutes, and grants nothing else anywhere in the system. It is minted by an
# endpoint that is itself behind the ordinary session and behind the SAME role
# fence, so a store keeper cannot obtain a ticket for an HOD tutorial and the
# media route re-checks the fence anyway when the ticket is spent.
#
# Same shape as the weekly-exec report link this codebase already ships — an
# unguessable capability in a URL, scoped to one artefact and one short window —
# except stateless, because a row per playback is a table that only grows.
_MEDIA_TICKET_TTL = _dt.timedelta(minutes=10)
_MEDIA_SCOPE = "training-media"


@router.get("/training/media-ticket/{module_key}/{language}",
            summary="A short-lived ticket a <video> element can carry in its URL")
async def media_ticket(module_key: str, language: str,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    """Mint the ticket, behind the ordinary session and the ordinary fence."""
    from .auth import _make_token

    if language not in LANGUAGES:
        raise HTTPException(404, f"language must be one of {list(LANGUAGES)}")
    mod = await _module_by_key(session, module_key)
    if mod is None or not mod.get("active"):
        raise HTTPException(404, f"no training module {module_key!r}")
    req = _roles_of(mod)
    if req and user["role"] not in req and user["role"] != "admin":
        raise HTTPException(
            403, "this tutorial is not for your role. If you think it should "
                 "be, ask an administrator to add your role to the module.")
    return {"ticket": _make_token(
        user["username"], user["role"], user.get("site_id") or "",
        _MEDIA_TICKET_TTL, scope=_MEDIA_SCOPE,
        extra={"mk": module_key, "lang": language}),
        "expires_in": int(_MEDIA_TICKET_TTL.total_seconds())}


def _media_viewer(request: Request, ticket: Optional[str]) -> dict:
    """Who is asking — from a ticket if there is one, else the bearer header.

    ⚠️ THE TICKET IS AN ALTERNATIVE CREDENTIAL, NOT A BYPASS. It carries a
    username and a role, and everything downstream applies the same fence to
    them that it applies to a session. A ticket for another module is refused
    by the caller, so a scope confusion cannot become a wider grant by reaching
    code that only checks the module it was handed.

    ⚠️ AND A REQUEST WITH NEITHER IS 401 HERE. This route is the ONE route in
    the training router that is not behind the blanket `Depends(get_current_user)`
    — it cannot be, because the credential arrives in the query string — so
    this function IS its guard and has to fail closed on its own. There is no
    path through it that returns a viewer without having verified a signature.
    """
    from .auth import _decode

    if ticket:
        p = _decode(ticket, _MEDIA_SCOPE)
        return {"username": p.get("sub") or "", "role": p.get("role") or "",
                "site_id": p.get("site_id") or "",
                "_ticket_for": (p.get("mk"), p.get("lang"))}
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "not authenticated")
    p = _decode(auth.split(" ", 1)[1].strip(), "access")
    return {"username": p.get("sub") or "", "role": p.get("role") or "",
            "site_id": p.get("site_id") or "", "_ticket_for": None}


# ⚠️ ITS OWN ROUTER, AND THIS IS THE ONLY REASON FOR IT. `main.py` mounts the
# training router with a blanket `dependencies=[Depends(get_current_user)]`,
# which is the right shape and fails closed — but it runs BEFORE the endpoint
# and refuses a request whose credential is in the query string, which a
# `<video>` element has no way to avoid. So this one route is mounted without
# the blanket guard and carries its own, in `_media_viewer`, which fails closed
# identically. Every other training route keeps the blanket one.
media_router = APIRouter(tags=["training"])


@media_router.get("/training/media/{module_key}/{language}.{ext}",
                  summary="Stream a published tutorial's video or captions")
async def training_media(module_key: str, language: str, ext: str,
                         request: Request,
                         ticket: Optional[str] = Query(
                             None, description="A media ticket from "
                                               "/training/media-ticket — a "
                                               "<video> element cannot send an "
                                               "Authorization header"),
                         session: AsyncSession = Depends(get_session)):
    """The video itself, with `Range` support so the player can seek.

    ⚠️ THE ROLE FENCE IS APPLIED HERE TOO, AND IT IS THE SAME FENCE. The module
    list is server-filtered and the deep link grants nothing — but a raw media
    URL would be a second door if it did not re-check, and a second door is
    exactly what P11-4 warns about. `_roles_of` is CALLED, not copied: a second
    implementation of an access decision is the thing that drifts.

    ⚠️ AND THE PATH IS DERIVED, NEVER TAKEN. The filename is built from the
    module key and language this endpoint already validated; nothing from the
    request reaches the filesystem as a path segment, so there is no traversal
    to defend against rather than a defence to get right.

    ⚠️ A MISSING FILE IS A 404, NOT AN ERROR. On a box where the renders have
    not landed the training page shows its "not published on this server" state
    and the assistant's deep links never appear at all — the same absent-feature
    contract suite CX-13 already pins for the matcher. Phase 13 must not turn a
    supported state into a broken one.
    """
    if ext not in _MEDIA_KINDS:
        raise HTTPException(404, "no such media type")
    if language not in LANGUAGES:
        raise HTTPException(404, f"language must be one of {list(LANGUAGES)}")
    user = _media_viewer(request, ticket)
    want = user.get("_ticket_for")
    if want is not None and want != (module_key, language):
        # A ticket names one module and one language. Refused HERE so a scope
        # confusion can never reach code that only checks what it was handed.
        raise HTTPException(
            403, "this ticket was issued for a different tutorial.")
    mod = await _module_by_key(session, module_key)
    if mod is None or not mod.get("active"):
        raise HTTPException(404, f"no training module {module_key!r}")
    req = _roles_of(mod)
    if req and user["role"] not in req and user["role"] != "admin":
        raise HTTPException(
            403, "this tutorial is not for your role. If you think it should "
                 "be, ask an administrator to add your role to the module.")

    mime, suffix = _MEDIA_KINDS[ext]
    # `<module_key>/<language>.<ext>` is the layout an object store will use
    # later; on disk the renders are flat, named by tutorial id. Both are tried
    # so moving to object storage changes a URI prefix and nothing else.
    base = _media_dir()
    candidates = [base / module_key / f"{language}{suffix}",
                  base / f"{module_key}{suffix}"]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise HTTPException(
            404, f"the {language} {ext} for {module_key} has not been rendered "
                 f"on this server yet.")

    size = path.stat().st_size
    rng = _parse_range(request.headers.get("range", ""), size)
    common = {"Accept-Ranges": "bytes",
              # A tutorial is immutable for a given module VERSION; a new
              # narration bumps the version (ruling Q4) rather than replacing
              # bytes under a cached URL.
              "Cache-Control": "private, max-age=3600"}

    def _stream(start: int, end: int):
        with open(path, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(_MEDIA_CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    if rng is None:
        return StreamingResponse(_stream(0, size - 1), media_type=mime,
                                 headers={**common, "Content-Length": str(size)})
    start, end = rng
    return StreamingResponse(
        _stream(start, end), status_code=206, media_type=mime,
        headers={**common,
                 "Content-Range": f"bytes {start}-{end}/{size}",
                 "Content-Length": str(end - start + 1)})


# ── admin: publish an asset, bump a version ─────────────────────────────────
class AssetIn(BaseModel):
    module_key: str
    language: str
    storage_uri: str = Field(min_length=1, max_length=1000)
    captions_uri: Optional[str] = None
    duration_s: int = Field(ge=1)


@router.post("/training/assets", status_code=201,
             summary="Publish a video for one language (admin)")
async def publish_asset(body: AssetIn = Body(...),
                        user: dict = Depends(require_roles()),
                        session: AsyncSession = Depends(get_session)):
    """⚠️ A URI, NOT AN UPLOAD (operator ruling Q5.3). The file lives on disk or
    in object storage and this records where. A `LargeBinary` column would put
    a 200-600 MB tutorial set into every nightly `pg_dump` forever and could not
    serve HTTP Range requests, so the viewer could not seek."""
    if body.language not in LANGUAGES:
        raise HTTPException(422, f"language must be one of {list(LANGUAGES)}")
    mod = await _module_by_key(session, body.module_key)
    if mod is None:
        raise HTTPException(404, f"no training module {body.module_key!r}")
    vals = {"module_id": mod["id"], "language": body.language,
            "storage_uri": body.storage_uri.strip(),
            "captions_uri": (body.captions_uri or None),
            "duration_s": body.duration_s}
    stmt = pg_insert(assets_t).values(**vals)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=["module_id", "language"],
        set_={k: v for k, v in vals.items()
              if k not in ("module_id", "language")}))
    await write_audit(session, user["username"], "TRAINING_ASSET_PUBLISHED",
                      "training_assets",
                      f"{mod['module_key']} {body.language} {body.storage_uri}")
    await session.commit()
    return {"published": True, "module_key": body.module_key,
            "language": body.language}


@router.post("/training/modules/{module_key}/bump",
             summary="New version — everybody must re-acknowledge (admin)")
async def bump_version(module_key: str, user: dict = Depends(require_roles()),
                       session: AsyncSession = Depends(get_session)):
    """⚠️ THIS INVALIDATES EVERY ACKNOWLEDGEMENT, and that is the point.

    The old rows are NOT deleted — "watched v1 on this date" stays true and
    stays auditable. They simply stop matching, because every read is keyed on
    the CURRENT version. Re-certification is a consequence of the data model
    rather than a cleanup script somebody has to remember to run.
    """
    mod = await _module_by_key(session, module_key)
    if mod is None:
        raise HTTPException(404, f"no training module {module_key!r}")
    new_v = int(mod["version"]) + 1
    await session.execute(update(modules_t)
                          .where(modules_t.c["id"] == mod["id"])
                          .values(version=new_v))
    await write_audit(session, user["username"], "TRAINING_VERSION_BUMPED",
                      "training_modules",
                      f"{module_key} v{mod['version']} -> v{new_v} "
                      f"(all prior acknowledgements now stale)")
    await session.commit()
    return {"module_key": module_key, "version": new_v,
            "message": ("Everybody required to watch this module must "
                        "acknowledge the new version.")}
