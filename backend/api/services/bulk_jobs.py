"""
backend/api/services/bulk_jobs.py — Phase 20b: submit and approve in bulk.

WHAT THE OPERATOR ASKED FOR. Submitting and approving one card at a time took
too many clicks. The field selects many ready job cards and submits them to
the HOD at once; the HOD selects many jobs (by date or system code) and
approves them at once. The HOD may also approve many paper-form execution
entries at once.

⚠️ NOTHING NEW DECIDES ANYTHING. Each item goes through the SAME function its
one-card path uses: `sme_groups.submit`, `sme_groups.decide_group`,
`execution.hod_decide`. Every rule those enforce (the site wall, a job is one
day, the recipe and Garnet checks, the QSEP gate, the stock posting, the audit
row) is enforced per item. A bulk call is a loop, not a shortcut.

⚠️ ONE BAD ITEM FAILS ALONE (rulings Q20-10, Q20-12). Each item runs in its
OWN SAVEPOINT inside the request's transaction. One that fails is rolled back
to its savepoint and reported with its reason ("already approved", "another
site's", "not cleared for issue", a stock conflict); the rest are kept. The
same shape as `/hod/pending/{kind}/bulk-approve`.

⚠️ APPROVE ONLY (ruling Q20-11). A rejection needs its own written reason, so
it stays one card at a time. ⚠️ AT MOST 50 per click (Q20-13).

⚠️ IDEMPOTENT. Each id is re-read inside its savepoint, and only a STAGED job
or a PENDING_HOD entry can be approved. Sending the same ids twice approves
nothing the second time, so a job's SQM is credited once.

Notifications: the HOD hears about a bulk SUBMISSION once per site, and each
submitter hears once about their approved jobs, instead of once per job.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import sme_groups as G
from .ledger import write_audit

MAX_BATCH = 50


def _check_size(n: int) -> None:
    if n < 1:
        raise HTTPException(422, "select at least one")
    if n > MAX_BATCH:
        raise HTTPException(422, f"at most {MAX_BATCH} at a time")


def _reason(e: Exception) -> tuple[int, str]:
    if isinstance(e, HTTPException):
        return e.status_code, str(e.detail)
    return 409, f"{type(e).__name__}: {str(e)[:200]}"


async def submit_jobs(session: AsyncSession, *, jobs: list[dict], username: str,
                      site_for) -> dict:
    """Submit each job card as it stands (code, SQM, ticked materials, part,
    remark). `site_for(requested)` resolves the write site per job."""
    _check_size(len(jobs))
    batch = uuid.uuid4().hex[:12]
    done, skipped = [], []
    per_site: dict[str, list] = defaultdict(list)
    for j in jobs:
        key = j.get("key") or f"{j.get('work_date')}|{j.get('tag')}"
        try:
            site = site_for(j.get("site_id"))
            async with session.begin_nested():
                out = await G.submit(
                    session, site_id=site, work_date=j["work_date"], tag=j["tag"],
                    code=j["code"], sqm=j["sqm"], consumption_ids=j["consumption_ids"],
                    notes=j.get("notes"), username=username,
                    surface_state=j.get("surface_state"), work_area=j.get("work_area"),
                    notify=False)
            if not out.get("group_id"):
                skipped.append({"key": key, "status": 409,
                                "reason": "nothing left to file on this card"})
                continue
            done.append({"key": key, "group_id": out["group_id"], "site_id": site,
                         "sqm": out["SQM_Completed"], "tag": out["Equipment_Tag_No"]})
            per_site[site].append(out)
        except (HTTPException, IntegrityError, DataError, KeyError, ValueError) as e:
            st, why = _reason(e)
            skipped.append({"key": key, "status": st, "reason": why})
    if done:
        from .notifications import dispatch
        for site, outs in per_site.items():
            total = sum(float(o["SQM_Completed"] or 0) for o in outs)
            try:
                await dispatch(session, event_key="sme_group_submitted",
                               title=f"{len(outs)} Surface Shield job(s) to approve",
                               body=(f"{username} submitted {len(outs)} job(s), {total:g} m² in all. "
                                     "Approve them together or one by one."),
                               recipient_role="hod", recipient_site=site,
                               link_page="/execution", related_table="sme_attribution_group",
                               related_ref=f"bulk:{batch}", created_by=username)
            except Exception:                      # noqa: BLE001 — never fatal
                pass
        await write_audit(session, username, "SME_GROUP_BULK_SUBMIT", "sme_attribution_group",
                          f"batch={batch} groups={[d['group_id'] for d in done]} "
                          f"skipped={len(skipped)}")
    return {"batch": batch, "submitted": done, "skipped": skipped}


async def approve_jobs(session: AsyncSession, *, ids: list[int], username: str,
                       site_id: Optional[str]) -> dict:
    """Approve each staged job as a whole (its SQM credited once)."""
    _check_size(len(ids))
    batch = uuid.uuid4().hex[:12]
    done, skipped = [], []
    by_submitter: dict[tuple, list] = defaultdict(list)
    for gid in dict.fromkeys(int(i) for i in ids):
        try:
            async with session.begin_nested():
                g = (await session.execute(select(G.group_t).where(
                    G.group_t.c["id"] == gid).with_for_update())).mappings().first()
                if g is None or (site_id is not None and g["Site_ID"] != site_id):
                    raise HTTPException(404, "not found at your site")
                if g["status"] != "staged":
                    who = f" by {g['hod_username']}" if g["hod_username"] else ""
                    raise HTTPException(409, f"already {('approved' if g['status'] == 'committed' else g['status'])}{who}")
                out = await G.decide_group(
                    session, group_id=gid, approve=True, edits=None, justification="",
                    reject_reason="", username=username, site_id=site_id)
            done.append({"id": gid, "sqm_credited": out.get("Done_SQM_credited"),
                         "tag": g["Equipment_Tag_No"], "date": str(g["Work_Date"])[:10]})
            by_submitter[(g["submitted_by"], g["Site_ID"])].append(out)
        except (HTTPException, IntegrityError, DataError) as e:
            st, why = _reason(e)
            skipped.append({"id": gid, "status": st, "reason": why})
    if done:
        from .notifications import dispatch
        for (who, site), outs in by_submitter.items():
            if not who or who == username:
                continue
            total = sum(float(o.get("Done_SQM_credited") or 0) for o in outs)
            try:
                await dispatch(session, event_key="sme_group_approved", severity="success",
                               title=f"{len(outs)} Surface Shield job(s) approved",
                               body=f"Approved by {username}: {total:g} m² credited.",
                               recipient_user=who, recipient_site=site, link_page="/surface-shield/log",
                               related_table="sme_attribution_group",
                               related_ref=f"bulk:{batch}", created_by=username)
            except Exception:                      # noqa: BLE001 — never fatal
                pass
        await write_audit(session, username, "SME_GROUP_BULK_APPROVE", "sme_attribution_group",
                          f"batch={batch} groups={[d['id'] for d in done]} skipped={len(skipped)}")
    return {"batch": batch, "approved": done, "skipped": skipped}


async def approve_entries(session: AsyncSession, *, ids: list[int], username: str,
                          site_id: Optional[str]) -> dict:
    """Approve each PENDING_HOD execution entry as filed (ruling Q20-12).

    Approval POSTS THE AREA AND DEDUCTS THE MATERIAL (`hod_decide`). An entry
    the QSEP gate blocks (an override needs a written reason, so not in bulk),
    or one whose stock posting conflicts, fails alone and stays with the HOD."""
    from . import execution as X
    _check_size(len(ids))
    batch = uuid.uuid4().hex[:12]
    done, skipped = [], []
    for eid in dict.fromkeys(int(i) for i in ids):
        try:
            async with session.begin_nested():
                e = await X.get_entry(session, eid, site_id)
                if e["status"] != X.PENDING_HOD:
                    raise HTTPException(409, f"it is {e['status']}, not waiting for the HOD")
                await X.hod_decide(session, username=username, entry_id=eid, site_id=site_id,
                                   approve=True)
            done.append({"id": eid, "entry_no": e["Entry_No"]})
        except (HTTPException, IntegrityError, DataError, ValueError) as ex:
            st, why = _reason(ex)
            skipped.append({"id": eid, "status": st, "reason": why})
    if done:
        await write_audit(session, username, "SME_EXEC_BULK_APPROVE", "sme_execution_entry",
                          f"batch={batch} entries={[d['id'] for d in done]} skipped={len(skipped)}")
    return {"batch": batch, "approved": done, "skipped": skipped}
