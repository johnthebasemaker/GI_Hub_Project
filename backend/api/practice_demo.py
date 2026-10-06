"""
backend/api/practice_demo.py — the self-driving demo's server half (Phase 21f).

Rulings Q21-13..17. MOUNTED ONLY IN THE PRACTICE PROCESS (`main.py` includes
this router only when `config.is_practice()`): in Live these paths do not
exist at all — a 404, not a 403 — so there is nothing to probe or misconfigure.

    GET  /practice/demo/catalog  which demos exist (for the launcher)
    POST /practice/demo/start    a practice.* user starts a demo → a ticket
    POST /practice/demo/switch   the demo "signs out and in" as another Practice
                                 role (never admin) — a real session, issued
                                 by the same code as a password login
    POST /practice/demo/reset    HOD / Admin clear every DEMO- entry, and put the
                                 demo tank's jobs back to ready

⚠️ WHY A TICKET. The role switch is a sign-in WITHOUT a password. It is only
acceptable because (1) it exists only in Practice, whose data is invented and
whose shared accounts' password the trainer hands out anyway (USER_MANUAL
§26.3), (2) it switches only between `practice.*` accounts, never to
`practice.admin` (the one account that can wipe everyone's work), (3) it needs
a ticket minted for a practice.* user within the last 30 minutes, and (4) every
switch is audited. The ticket also stops a stale page from switching roles
hours after a demo ended.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth as A
from .db import get_session
from .services.ledger import write_audit
from .services.notifications import notify

router = APIRouter(prefix="/practice/demo", tags=["practice"])

TICKET_TTL = _dt.timedelta(minutes=30)
DEMO_TAG = "DEMO-"
# role → the shared Practice account (tools/practice_overlay.ACCOUNTS). Admin is
# deliberately absent (ruling Q21-13).
ROLE_ACCOUNTS = {
    "store_keeper": "practice.storekeeper", "supervisor": "practice.supervisor",
    "hod": "practice.hod", "qc": "practice.qc", "qc_hod": "practice.qchod",
    "warehouse_user": "practice.warehouse", "logistics": "practice.logistics",
    "auditor": "practice.auditor",
}

# The demos the runner knows (frontend/src/demo/scripts). Kept here too so the
# assistant can offer one without loading the frontend catalogue (ai/router.py).
# `roles`: who may START it (the demo itself switches roles as it goes).
CATALOG = [
    {"id": "consumption-to-approval", "title": "Issue stock, then the HOD approves it",
     "start": "/entry/issue", "roles": ["store_keeper", "hod", "admin"],
     "keywords": ["issue", "consumption", "approve", "approval", "stage", "issued", "stock"]},
    {"id": "ss-bulk", "title": "Surface Shield jobs: bulk submit, then bulk approve",
     "start": "/execution", "roles": ["store_keeper", "supervisor", "hod", "admin"],
     "keywords": ["surface", "shield", "job", "jobs", "bulk", "submit", "sqm", "execution"]},
]


class SwitchIn(BaseModel):
    ticket: str = Field(..., min_length=10)
    role: str


def _require_practice_user(user: dict) -> None:
    if not str(user.get("username", "")).startswith("practice."):
        raise HTTPException(403, "the demo runs on the practice.* accounts only")


@router.get("/catalog", summary="The self-driving demos (Practice only)")
async def catalog(user: dict = Depends(A.get_current_user)):
    return {"items": [{k: d[k] for k in ("id", "title", "start", "roles")} for d in CATALOG]}


@router.post("/start", summary="Start a demo — a 30-minute ticket for role switches")
async def start(user: dict = Depends(A.get_current_user),
                session: AsyncSession = Depends(get_session)):
    _require_practice_user(user)
    ticket = A._make_token(user["username"], user.get("role", ""), user.get("site_id") or "",
                           TICKET_TTL, scope="demo")
    await write_audit(session, user["username"], "PRACTICE_DEMO_START", "users", "demo ticket")
    await session.commit()
    return {"ticket": ticket, "expires_in": int(TICKET_TTL.total_seconds())}


@router.post("/switch", summary="Become another Practice role for the demo (never admin)")
async def switch(body: SwitchIn, response: Response,
                 user: dict = Depends(A.get_current_user),
                 session: AsyncSession = Depends(get_session)):
    _require_practice_user(user)
    p = A._decode(body.ticket, "demo")                 # 401 when forged or expired
    started_by = p.get("sub")
    if not str(started_by or "").startswith("practice."):
        raise HTTPException(403, "not a demo ticket")
    target = ROLE_ACCOUNTS.get(body.role)
    if target is None:
        raise HTTPException(403, "the demo can switch to a Practice role, never to admin")
    row = await A._fetch_user(session, target)
    if row is None or row.role != body.role:
        raise HTTPException(409, f"{target} is missing — rebuild Practice (tools/practice_db.py build)")
    token = A._make_token(row.username, row.role, row.Site_ID or "", A.ACCESS_TTL,
                          warehouse_id=row.Warehouse_ID or "")
    raw_refresh, _ = await A._open_session(session, row.username, row.id, "web")
    await write_audit(session, user["username"], "PRACTICE_DEMO_SWITCH", "users",
                      f"{user['username']} → {row.username} (demo started by {started_by})")
    await session.commit()
    A._set_refresh_cookie(response, raw_refresh, A.REFRESH_TTLS["web"])
    return {"access_token": token, "token_type": "bearer",
            "user": A._public(row.username, row.role, row.Site_ID, row.Warehouse_ID)}


# Tables a demo writes to, and the column carrying its DEMO- tag.
_RESET = (
    ("pending_issues", '"Remarks"'), ("pending_receipts", '"Remarks"'),
    ("consumption", '"Remarks"'), ("receipts", '"Remarks"'), ("returns", '"Remarks"'),
)
# A demo JOB is one filed on a DEMO- tank (overlay v10 seeds DEMO-TANK-1 with two
# ready days) or one whose note carries the tag.
_DEMO_GROUPS = ('SELECT id FROM sme_attribution_group '
                'WHERE "Equipment_Tag_No" LIKE :t OR notes LIKE :t')

_CREDITED_SQL = (
    'SELECT g."Site_ID", g."Equipment_Tag_No", g."Lining_System_Code", '
    'SUM(COALESCE(g."Done_SQM_Credited", g."SQM_Completed")) AS sqm '
    f"FROM sme_attribution_group g WHERE g.id IN ({_DEMO_GROUPS}) "
    "AND g.status IN ('approved', 'committed') GROUP BY 1, 2, 3")
_UNCREDIT_SQL = (
    'UPDATE sme_sqm_progress SET "Done_SQM" = GREATEST(0, "Done_SQM" - :q), '
    "updated_at = CURRENT_TIMESTAMP "
    'WHERE "Site_ID" = :s AND "Equipment_Tag_No" = :g AND "Lining_System_Code" = :c')


async def reset_demo_data(session: AsyncSession) -> dict[str, int]:
    """Remove every DEMO- entry, and put the demo tank back as it was seeded:
    its jobs un-filed (their days are READY again, so the demo runs again),
    and the m² an approved demo job credited taken off the tank's progress.
    The seeded store-keeper draws on the demo tank are NOT tagged and stay —
    they are what the demo files. Does not commit."""
    t = DEMO_TAG + "%"
    removed: dict[str, int] = {}
    # ⚠️ m² FIRST, while the groups still exist: an approved job credited
    # COALESCE(Done_SQM_Credited, SQM_Completed) to sme_sqm_progress.Done_SQM
    # (services/execution.credit_sqm; a prep code credits nothing, and nothing
    # matching it exists on a prep tank's progress row to take back).
    credited = (await session.execute(text(_CREDITED_SQL), {"t": t})).mappings().all()
    for c in credited:
        await session.execute(text(_UNCREDIT_SQL), {
            "q": float(c["sqm"] or 0), "s": c["Site_ID"], "g": c["Equipment_Tag_No"],
            "c": c["Lining_System_Code"]})
    removed["sqm_reversed"] = len(credited)
    # a job's material rows first (they point at the job; revisions cascade)
    res = await session.execute(text(
        f"DELETE FROM sme_consumption_log WHERE group_id IN ({_DEMO_GROUPS})"), {"t": t})
    removed["sme_consumption_log"] = res.rowcount or 0
    res = await session.execute(text(
        f"DELETE FROM sme_attribution_group WHERE id IN ({_DEMO_GROUPS})"), {"t": t})
    removed["sme_attribution_group"] = res.rowcount or 0
    for table, col in _RESET:
        res = await session.execute(text(f"DELETE FROM {table} WHERE {col} LIKE :t"), {"t": t})
        removed[table] = res.rowcount or 0
    return removed


@router.post("/reset", summary="Remove every DEMO- entry (HOD / Admin, Practice only)")
async def reset(user: dict = Depends(A.require_roles("hod")),
                session: AsyncSession = Depends(get_session)):
    removed = await reset_demo_data(session)
    await write_audit(session, user["username"], "PRACTICE_DEMO_RESET", "practice",
                      ", ".join(f"{k}={v}" for k, v in removed.items() if v) or "nothing")
    await session.commit()
    return {"removed": removed}


# ── the assistant's half (ruling Q21-15) ─────────────────────────────────────
# An explicit ask to be SHOWN, or to have it DONE. "How do I …" is NOT one:
# that is a manual question, answered as before — with the matching demo
# offered beside the answer (ai/router.py).
_ASK = ("show me", "demo", "do it for me", "for me", "walk me through", "perform",
        "run the", "auto demo", "can you do", "do this", "show how")


def wants_a_demo(question: str) -> bool:
    q = " " + " ".join(str(question or "").lower().replace("?", " ").split()) + " "
    return any(f" {a} " in q for a in _ASK)


def match_demo(question: str, role: str) -> dict | None:
    """The demo a request is about — TWO distinct keywords in common, the same
    rule as the tutorial matcher (one shared word is a coincidence, two is a
    topic). Only demos this role may start (rule 9: the fence, then the score)."""
    words = {w.strip(".,?!'\"") for w in str(question or "").lower().split()}
    best, best_n = None, 1
    for d in CATALOG:
        if role not in d["roles"] and role != "admin":
            continue
        n = len(words & set(d["keywords"]))
        if n > best_n:
            best, best_n = d, n
    return {k: best[k] for k in ("id", "title", "start")} if best else None


async def record_unsupported(session: AsyncSession, username: str, question: str) -> bool:
    """ "I can't show that visually yet — I've sent your request to the admin."
    One Feedback row per (user, question) per day — a repeated ask is not a
    second report. Returns True when a new row was written."""
    q = " ".join(str(question).split())[:400]
    desc = f"Demo request: {q}"
    seen = (await session.execute(text(
        "SELECT 1 FROM bug_reports WHERE username = :u AND description = :d "
        "AND created_at >= CURRENT_DATE LIMIT 1"), {"u": username, "d": desc})).first()
    if seen:
        return False
    await session.execute(text(
        "INSERT INTO bug_reports (username, type, page, description, status, title, severity) "
        "VALUES (:u, 'feature', 'assistant', :d, 'open', 'Demo request (Practice)', 'low')"),
        {"u": username, "d": desc})
    await notify(session, event_key="feedback_submitted", recipient_role="admin",
                 severity="info", title=f"Demo request from {username}",
                 body=q[:140], link_page="/admin/console", related_table="bug_reports")
    await session.commit()
    return True
