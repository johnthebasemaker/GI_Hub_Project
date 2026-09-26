"""
backend/api/services/announcements.py — Phase 14d: "What's new", reaching only
the people a feature concerns.

AUTHORED AS CODE (ruling Q14-13). An announcement is a YAML file in
`docs/announcements/`, reviewed in the PR that ships the feature:

    key: sme-grouped-queue            # unique, stable
    title: Surface Shield jobs — one area per job
    body: |
      The queue now groups …
    routes: [/execution]              # where the feature lives (required)
    roles: [supervisor, hod]          # optional — NARROWS the audience, never widens it
    sites: [CNCEC]                    # optional — NULL means every site
    tutorial: sup_grouped_queue_v1    # optional Training Hub module key
    rerender: true                    # optional — the tutorial needs re-recording
    manual: "4.9a.2"                  # optional USER_MANUAL.md section

`sync()` loads the files into `feature_announcements` as DRAFTS. An admin
previews, publishes (now or at a time) or retracts. Nobody types announcement
copy into a production form — the sentence a user reads is one a person
approved in a diff, the same shape as P12-1.

⚠️ THE AUDIENCE COMES FROM THE NAVIGATION MATRIX, NOT A NEW LIST (rule 14).
`backend/api/data/nav_access.json` is a snapshot of which roles may open each
route, produced from `frontend/src/config/nav.tsx` by
`frontend/scripts/nav_access_dump.mjs` (role levels from `auth.ROLE_META`).
Suite 14D fails when the snapshot and the manifest disagree, so the snapshot
cannot drift. The audience is every role that can open ANY of the routes,
intersected with `roles:` when given. A route the matrix does not know — or can
only resolve as `/records/*` / `/master/*` — is REFUSED, never guessed: an
announcement pointing someone at a page they cannot open is worse than none.

⚠️ RULE 13. A `manual:` that names a section USER_MANUAL.md does not have is
refused at sync: the link would be dead on arrival.

⚠️ IN-APP ONLY (Q14-13). The bell (`notify`, no WhatsApp) and the What's-new
panel. Practice (rule 17) is a second database, so the same sync there gives a
trainee the same panel with their own read receipts.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit

ROOT = Path(__file__).resolve().parents[3]
ANN_DIR = ROOT / "docs" / "announcements"
NAV_SNAPSHOT = ROOT / "backend" / "api" / "data" / "nav_access.json"
MANUAL = ROOT / "USER_MANUAL.md"

ann_t = _MD.tables["feature_announcements"]
read_t = _MD.tables["feature_announcement_reads"]

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
EVENT_KEY = "feature_announcement"


# ── the navigation matrix ─────────────────────────────────────────────────────
def nav_access(path: Path = NAV_SNAPSHOT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audience_for(routes: list[str], roles: Optional[list[str]] = None,
                 access: Optional[dict] = None) -> list[str]:
    """Roles that can open ANY of `routes`, narrowed by `roles`. Raises
    ValueError on a route the matrix cannot vouch for."""
    access = access or nav_access()
    if not access.get("sane"):
        raise ValueError("the nav snapshot is not sane — regenerate it")
    known: dict[str, list[str]] = access["routes"]
    publics: list[str] = access.get("publics") or []
    every = list(access.get("roleLevels") or {})
    out: list[str] = []
    for r in routes:
        path = (str(r).rstrip("/") or "/")
        if path in known:
            allowed = known[path]
        elif any(path.startswith(p) for p in publics):
            allowed = every
        elif path.startswith("/records/") or path.startswith("/master/"):
            raise ValueError(f"{path}: /records/* and /master/* are built by .map() "
                             "and the matrix cannot resolve them — name the page "
                             "that links to it instead")
        else:
            raise ValueError(f"{path}: not a route in the navigation matrix")
        out += [x for x in allowed if x not in out]
    if roles:
        bad = [x for x in roles if x not in every]
        if bad:
            raise ValueError(f"unknown role(s): {', '.join(bad)}")
        out = [x for x in out if x in roles]
        if not out:
            raise ValueError("roles: narrows the audience to nobody — none of those "
                             "roles can open the announced route(s)")
    return out


# ── the manual (rule 13) ──────────────────────────────────────────────────────
def manual_sections(text_: Optional[str] = None) -> set[str]:
    """Section numbers that exist as headings: `## 4.9a …`, `### 4.9a.2 …`."""
    src = text_ if text_ is not None else MANUAL.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"^#{2,5}\s+(\d+(?:\.\w+)*)\b", src, re.M)}


# ── the files ─────────────────────────────────────────────────────────────────
def parse_file(path: Path, *, access: Optional[dict] = None,
               sections: Optional[set[str]] = None) -> dict:
    raw = path.read_bytes()
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError("an announcement is a YAML mapping")
    key = str(doc.get("key") or "").strip()
    if not _KEY_RE.match(key):
        raise ValueError("key: lower-case letters, digits and '-', 3–64 characters")
    title, body = str(doc.get("title") or "").strip(), str(doc.get("body") or "").strip()
    if not title or not body:
        raise ValueError("title: and body: are required")
    if len(title) > 120:
        raise ValueError("title: at most 120 characters — it is a heading, not a paragraph")
    routes = doc.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("routes: is required — the page(s) the feature lives on")
    roles = doc.get("roles")
    if roles is not None and not isinstance(roles, list):
        raise ValueError("roles: is a list")
    sites = doc.get("sites")
    if sites is not None and (not isinstance(sites, list) or not sites):
        raise ValueError("sites: is a non-empty list, or absent for every site")
    audience = audience_for([str(r) for r in routes],
                            [str(r) for r in roles] if roles else None, access)
    manual = str(doc["manual"]).strip() if doc.get("manual") is not None else None
    if manual:
        secs = sections if sections is not None else manual_sections()
        if manual not in secs:
            raise ValueError(f"manual: USER_MANUAL.md has no section {manual} (rule 13)")
    return {"key": key, "title": title, "body": body,
            "routes": [str(r) for r in routes], "audience_roles": audience,
            "sites": [str(x) for x in sites] if sites else None,
            "tutorial_module": (str(doc["tutorial"]).strip() if doc.get("tutorial") else None),
            "rerender": bool(doc.get("rerender")),
            "manual_section": manual,
            "source_path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "source_sha256": hashlib.sha256(raw).hexdigest()}


def load_dir(directory: Path = ANN_DIR) -> tuple[list[dict], list[dict]]:
    """(parsed, problems). A bad file is reported by name and skipped — one
    typo never stops the others loading."""
    access, sections = nav_access(), manual_sections()
    good, bad, seen = [], [], {}
    for p in sorted(directory.glob("*.yaml")) if directory.exists() else []:
        try:
            a = parse_file(p, access=access, sections=sections)
        except ValueError as e:
            bad.append({"file": p.name, "problem": str(e)})
            continue
        if a["key"] in seen:
            bad.append({"file": p.name, "problem": f"key {a['key']} is also used by {seen[a['key']]}"})
            continue
        seen[a["key"]] = p.name
        good.append(a)
    return good, bad


# ── the table ─────────────────────────────────────────────────────────────────
def _row(r) -> dict:
    d = dict(r)
    for k in ("routes", "audience_roles", "sites"):
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "sites" else None)
    for k in ("publish_at", "published_at", "retracted_at", "created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def sync(session: AsyncSession, *, username: str = "system",
               directory: Path = ANN_DIR) -> dict:
    """Load the files. New keys become DRAFTS; a changed file updates its row's
    text and audience whatever its status (a published announcement corrected
    in a PR is corrected for everybody). Nothing is deleted: a file that
    disappears leaves its row, which an admin can retract."""
    parsed, problems = load_dir(directory)
    added = updated = 0
    for a in parsed:
        vals = {**a, "routes": json.dumps(a["routes"]),
                "audience_roles": json.dumps(a["audience_roles"]),
                "sites": json.dumps(a["sites"]) if a["sites"] else None}
        cur = (await session.execute(select(ann_t.c["id"], ann_t.c["source_sha256"]).where(
            ann_t.c["key"] == a["key"]))).first()
        if cur is None:
            await session.execute(pg_insert(ann_t).values(**vals, status="draft"))
            added += 1
        elif cur[1] != a["source_sha256"]:
            await session.execute(update(ann_t).where(ann_t.c["id"] == cur[0])
                                  .values(**vals, updated_at=func.now()))
            updated += 1
    if added or updated:
        await write_audit(session, username, "ANNOUNCEMENT_SYNC", "feature_announcements",
                          f"added={added} updated={updated} problems={len(problems)}")
    return {"added": added, "updated": updated, "files": len(parsed), "problems": problems}


async def _get(session: AsyncSession, key: str) -> dict:
    r = (await session.execute(select(ann_t).where(ann_t.c["key"] == key))).mappings().first()
    if r is None:
        raise HTTPException(404, f"no announcement '{key}' — sync the files first")
    return dict(r)


async def _ring(session: AsyncSession, a: dict, username: str) -> int:
    """The bell, once per audience role (and site, when narrowed). In-app only."""
    from .notifications import notify
    roles = json.loads(a["audience_roles"])
    sites = json.loads(a["sites"]) if a.get("sites") else [None]
    n = 0
    for role in roles:
        for site in sites:
            await notify(session, event_key=EVENT_KEY, title=f"What's new — {a['title']}",
                         body=a["body"][:280], severity="info", recipient_role=role,
                         recipient_site=site, link_page=(json.loads(a["routes"]) or [None])[0],
                         related_table="feature_announcements", related_ref=str(a["id"]))
            n += 1
    return n


async def publish(session: AsyncSession, *, key: str, username: str,
                  at: Optional[datetime] = None) -> dict:
    a = await _get(session, key)
    if a["status"] == "published":
        raise HTTPException(409, f"'{key}' is already published")
    now = (await session.execute(select(func.now()))).scalar_one()
    if at is not None:
        at = at.replace(tzinfo=None) if at.tzinfo is None else at.astimezone(now.tzinfo).replace(tzinfo=None)
    naive_now = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
    if at is not None and at > naive_now:
        await session.execute(update(ann_t).where(ann_t.c["id"] == a["id"]).values(
            status="scheduled", publish_at=at, published_by=username,
            retracted_at=None, retracted_by=None))
        await write_audit(session, username, "ANNOUNCEMENT_SCHEDULE", "feature_announcements",
                          f"{key} at {at.isoformat()}")
        return {"key": key, "status": "scheduled", "publish_at": at.isoformat()}
    await session.execute(update(ann_t).where(ann_t.c["id"] == a["id"]).values(
        status="published", published_at=func.now(), published_by=username,
        publish_at=None, retracted_at=None, retracted_by=None))
    rung = await _ring(session, a, username)
    await write_audit(session, username, "ANNOUNCEMENT_PUBLISH", "feature_announcements",
                      f"{key} → {', '.join(json.loads(a['audience_roles']))} ({rung} bell rows)")
    return {"key": key, "status": "published", "bells": rung}


async def retract(session: AsyncSession, *, key: str, username: str) -> dict:
    a = await _get(session, key)
    if a["status"] == "retracted":
        raise HTTPException(409, f"'{key}' is already retracted")
    await session.execute(update(ann_t).where(ann_t.c["id"] == a["id"]).values(
        status="retracted", retracted_at=func.now(), retracted_by=username, publish_at=None))
    # The bell rows go too: a retracted sentence should not keep being read.
    await session.execute(text(
        "DELETE FROM app_notifications WHERE related_table = 'feature_announcements' "
        "AND related_ref = :r AND read_at IS NULL"), {"r": str(a["id"])})
    await write_audit(session, username, "ANNOUNCEMENT_RETRACT", "feature_announcements", key)
    return {"key": key, "status": "retracted"}


async def release_due(session: AsyncSession) -> int:
    """Publish every scheduled announcement whose time has come. Called on the
    read paths — no scheduler to forget to start; the first reader after the
    time releases it, exactly once (the UPDATE … WHERE status='scheduled' is
    the claim)."""
    due = (await session.execute(text(
        "UPDATE feature_announcements SET status = 'published', published_at = now(), "
        "publish_at = NULL WHERE status = 'scheduled' AND publish_at <= now() "
        "RETURNING id"))).scalars().all()
    for i in due:
        a = dict((await session.execute(select(ann_t).where(ann_t.c["id"] == i))).mappings().one())
        await _ring(session, a, a.get("published_by") or "system")
    return len(due)


async def admin_list(session: AsyncSession) -> list[dict]:
    await release_due(session)
    reads = dict((await session.execute(text(
        "SELECT announcement_id, COUNT(*) FROM feature_announcement_reads GROUP BY 1"))).all())
    out = []
    for r in (await session.execute(select(ann_t).order_by(ann_t.c["created_at"].desc(),
                                                            ann_t.c["id"].desc()))).mappings().all():
        d = _row(r)
        d["reads"] = int(reads.get(d["id"], 0))
        out.append(d)
    return out


async def whats_new(session: AsyncSession, *, user: dict, include_read: bool = False,
                    limit: int = 20) -> list[dict]:
    """Published announcements for THIS user's role (and site), newest first,
    unread only unless asked."""
    await release_due(session)
    role, site = user.get("role"), user.get("site_id") or None
    rows = (await session.execute(text(
        "SELECT a.*, r.read_at FROM feature_announcements a "
        "LEFT JOIN feature_announcement_reads r ON r.announcement_id = a.id "
        "  AND r.username = :u "
        "WHERE a.status = 'published' "
        "ORDER BY a.published_at DESC, a.id DESC LIMIT :lim"),
        {"u": user.get("username"), "lim": int(limit) * 4})).mappings().all()
    out = []
    for r in rows:
        d = _row(r)
        if role not in d["audience_roles"]:
            continue
        if d["sites"] and site not in d["sites"] and role != "admin":
            continue
        if d.get("read_at") is not None and not include_read:
            continue
        d["read"] = d.pop("read_at") is not None
        for k in ("source_path", "source_sha256", "published_by", "retracted_by",
                  "retracted_at", "publish_at"):
            d.pop(k, None)
        out.append(d)
        if len(out) >= limit:
            break
    return out


async def mark_read(session: AsyncSession, *, user: dict, ids: list[int]) -> int:
    visible = {a["id"] for a in await whats_new(session, user=user, include_read=True, limit=200)}
    n = 0
    for i in ids:
        if int(i) not in visible:
            continue
        n += (await session.execute(pg_insert(read_t).values(
            announcement_id=int(i), username=user["username"]).on_conflict_do_nothing())).rowcount or 0
    return n
