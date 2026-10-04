"""
backend/api/services/sme_groups.py — Phase 14c: the Surface Shield attribution
queue, one JOB at a time.

WHAT THE OPERATOR ASKED FOR (Track 1). Do not ask for a system code, an
equipment tag and an SQM on EVERY material. Group the unattributed consumption
by DATE and EQUIPMENT (the Consumption Log's `Tank No.`), suggest the system
code from the materials in the group, and ask for the SQM ONCE.

WHAT IT FIXES (defect D3). Each Phase 13 attribution credited its own SQM to
`Done_SQM` on approval, so a four-component PU job of 13.37 m² attributed per
material credited 53.48 m². A group credits its area ONCE.

HOW IT IS BUILT — a coordinating layer, not a rewrite. Every member row is still
attributed by `sme_link.assign()` and decided by `sme_link.decide()`, the
functions suites DA–DD pin (variance snapshots, the rejection loop, HOD edits
with a justification, Excel-edit revisions). The group adds exactly three
things on top:

  · ONE submission: one system code, one SQM, for the chosen materials
    ("Split" = submit a subset; the rest stay for another code — Q14-10);
  · ONE decision: the HOD approves or rejects the WHOLE group (Q14-11);
  · ONE credit: `decide(credit=False)` per member, then `credit_done_sqm` once.

The per-row API is kept as a GROUP OF ONE (ruling Q14-16), so a single row
behaves — and credits — exactly as it did.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD, write_audit

group_t = _MD.tables["sme_attribution_group"]
log_t = _MD.tables["sme_consumption_log"]

_SQM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:SQM|SQ\.?\s*M|M2|M²)(?![A-Za-z0-9])", re.I)
# "Sump Tank Wall 5.50 Done" — a figure with no unit, but followed by Done.
_DONE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:done|completed)\b", re.I)
# Words that describe the day, not the part: "Shell rubber lining is in
# progress 62 m2 done" names the part "Shell rubber lining".
_NOT_PART = re.compile(r"\b(?:is\s+)?in\s+progress\b|\b(?:applied|done|completed)\b", re.I)
_EDGE = " -–—=:,.;/"
AREA_MAX = 80


def parse_note(text: Optional[str]) -> Optional[dict]:
    """One store keeper's remark → {text, sqm, area} (Phase 15e).

    The field writes the day's work into the Consumption Log's Remarks in one
    recognisable shape — "<part of the equipment> - <figure> SQM Done":

        "Floor - 13.37 SQM Done"             → area "Floor",  13.37
        "Bottom B/L  - 15.36 SQM Done"       → "Bottom B/L",  15.36
        "Shell rubber lining is in progress 62 m2 done" → "Shell rubber lining", 62
        "Sump Tank Wall 5.50 Done"           → "Sump Tank Wall", 5.5
        "65 SQM Done"                        → no part, 65
        "Dyke Wall Patch Work"               → "Dyke Wall Patch Work", no figure

    ⚠️ A PRE-FILL, NEVER THE RECORD — the same stance as `hint_system_code`.
    The card shows all three for the supervisor to confirm or correct; what is
    stored is what they submit. `text` is the remark as typed (whitespace
    collapsed), which is what is kept as the job's remark."""
    t = " ".join(str(text or "").split())
    if not t:
        return None
    m = _SQM_RE.search(t) or _DONE_RE.search(t)
    sqm = None
    if m:
        try:
            v = float(m.group(1))
            sqm = v if v > 0 else None
        except ValueError:
            sqm = None
    head = t[:m.start()] if m else t
    area = " ".join(_NOT_PART.sub(" ", head).split()).strip(_EDGE).strip()
    return {"text": t, "sqm": sqm, "area": (area[:AREA_MAX] or None)}


def notes_of(rows: list[dict]) -> list[dict]:
    """The DISTINCT remarks on a job's rows, each with the rows that carry it.

    A day on one equipment can carry two notes — J022 on 2026-09-22: "Floor -
    9.25 SQM Done" and "Top of Brick Coving Applied - 4.82 SQM Done". They are
    two jobs (ruling 2026-09-30): the card offers each note, and picking one
    ticks its own materials and pre-fills its figures. Order = first seen."""
    out: dict[str, dict] = {}
    for r in rows:
        p = parse_note(r.get("remarks"))
        if p is None:
            continue
        e = out.setdefault(p["text"].lower(), {**p, "ids": []})
        e["ids"].append(r.get("consumption_id"))
    return list(out.values())


def sqm_hint(remarks: list[Optional[str]]) -> Optional[float]:
    """The area a store keeper already typed ("Floor - 13.37 SQM Done").

    ⚠️ A PRE-FILL, NEVER THE RECORD — the same stance as `hint_system_code`.
    Remarks is free text an HOD can edit. When the group's rows disagree the
    first stated figure is offered and the field confirms it."""
    for r in remarks:
        p = parse_note(r)
        if p and p["sqm"]:
            return p["sqm"]
    return None


def _day(v) -> str:
    return str(v or "")[:10]


def _sap(v) -> str:
    return str(v or "").replace(" ", "").strip()


# ── the system-code suggestion ────────────────────────────────────────────────
async def suggest(session: AsyncSession, *, site_id: str, tag: str,
                  saps: list[str], remarks: list[Optional[str]]) -> list[dict]:
    """Candidate system codes for one group, best first.

    Ranked by: (1) RECIPE COVERAGE — the share of the group's materials that
    code's recipe lists (a PU 1 mm job drawing all four 1042-x components is
    1.0 for PUL1 and 0 for rubber lining); (2) the `LS <code>` a store keeper
    typed in Remarks; (3) RECENCY — the code last approved on this tag.
    Candidates are only the codes THIS TAG carries, because the pair is
    re-validated on submit exactly as `assign()` validates it.
    """
    from .sme_link import hint_system_code
    codes = [r[0] for r in (await session.execute(text(
        'SELECT DISTINCT TRIM("Lining_System_Code") FROM sme_equipment '
        'WHERE "Site_ID" = :s AND "Equipment_Tag_No" = :t '
        'AND COALESCE(TRIM("Lining_System_Code"), \'\') <> \'\''),
        {"s": site_id, "t": tag})).all()]
    if not codes:
        return []
    recipe: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for code, sap, name in (await session.execute(text(
            'SELECT TRIM("Lining_System_Code"), "SAP_Code", MAX("Lining_System_Name") '
            'FROM sme_recipe WHERE TRIM("Lining_System_Code") = ANY(:c) '
            'GROUP BY 1, 2'), {"c": codes})).all():
        recipe[code].add(_sap(sap))
        if name:
            names[code] = name
    hinted = {hint_system_code(r) for r in remarks} - {None}
    recent = {r[0]: r[1] for r in (await session.execute(text(
        'SELECT "Lining_System_Code", MAX(hod_decided_at) FROM sme_attribution_group '
        "WHERE \"Site_ID\" = :s AND \"Equipment_Tag_No\" = :t AND status = 'committed' "
        'GROUP BY 1'), {"s": site_id, "t": tag})).all()}
    want = {_sap(s) for s in saps}
    out = []
    for c in codes:
        covered = sorted(want & recipe.get(c, set()))
        missing = sorted(want - recipe.get(c, set()))
        out.append({"code": c, "name": names.get(c), "coverage": round(len(covered) / len(want), 4) if want else 0.0,
                    "covered": covered, "missing": missing, "hinted": c in hinted,
                    "last_used": recent.get(c).isoformat() if recent.get(c) else None})
    def _recency(x) -> float:
        ts = recent.get(x["code"])
        return -ts.timestamp() if ts else 0.0
    out.sort(key=lambda x: (-x["coverage"], 0 if x["hinted"] else 1, _recency(x), x["code"]))
    return out


# ── the queue ─────────────────────────────────────────────────────────────────
async def queue(session: AsyncSession, *, site_id: Optional[str]) -> dict:
    """The Phase 13 ledger sweep (every unattributed Surface Shield draw, however
    it got there — ruling Q13-6), grouped by (site, date, resolved equipment)."""
    from . import reconcile as RC
    from . import sme_link as SL
    from . import units as U
    sw = await SL.sweep(session, site_id=site_id, limit=5000, offset=0)
    items = sw["items"]
    resolvers: dict[str, object] = {}
    umap = await U.unit_map(session, [i.get("sap_code") for i in items])
    groups: dict[tuple, dict] = {}
    unmapped: dict[tuple, int] = defaultdict(int)
    excluded = 0
    for it in items:
        site = it["site_id"]
        if site not in resolvers:
            resolvers[site] = await RC.resolver(session, site)
        tag, st = resolvers[site](it.get("tank_no"))
        if st == "ignored":
            excluded += 1        # non-equipment: stays in stock, no SQM (Q14-9)
            continue
        if st != "mapped":
            unmapped[(site, str(it.get("tank_no") or "").strip())] += 1
            continue
        k = (site, _day(it.get("work_date")), tag)
        g = groups.setdefault(k, {"site_id": site, "work_date": k[1], "tag": tag,
                                  "rows": [], "rejected": [], "edited": 0,
                                  "prev_code": None, "prev_sqm": None})
        u = umap.get(_sap(it.get("sap_code")))
        base = U.base_qty(it.get("quantity"), u["factor"]) if u else None
        g["rows"].append({**{k2: it.get(k2) for k2 in (
            "consumption_id", "sap_code", "material_code", "material_name", "quantity",
            "uom", "remarks", "reason", "rejection_reason", "rejected_by", "log_id",
            "log_status")},
            "base_qty": base, "base_uom": (u or {}).get("base_uom"),
            "unit_known": (u is None) or (u.get("factor") is not None)})
        if it.get("reason") == "rejected":
            g["rejected"].append({"consumption_id": it.get("consumption_id"),
                                  "reason": it.get("rejection_reason"),
                                  "by": it.get("rejected_by")})
        if it.get("reason") == "edited":
            g["edited"] += 1
        if it.get("prev_code") and not g["prev_code"]:
            g["prev_code"], g["prev_sqm"] = it.get("prev_code"), it.get("prev_sqm")
    # ⚠️ PHASE 15d — GARNET IS ITS OWN CARD. It is surface preparation, benchmarked
    # per surface (Old / New) under a prep code chosen by the equipment's
    # substrate, with its OWN area (the blasted m² is not the lined m²). Left on
    # the lining card it would only ever read "not in recipe".
    from . import prep as PR
    garnet = await PR.garnet_saps(session)
    prep_names = {c: n for c, n in (await session.execute(text(
        'SELECT TRIM("Lining_System_Code"), MAX("Lining_System_Name") FROM sme_recipe '
        'WHERE TRIM("Lining_System_Code") IN (SELECT "Prep_Code" FROM sme_prep_baseline) '
        'GROUP BY 1'))).all()}
    prep_options = [{"code": c, "name": prep_names.get(c) or
                     ("Blasting — concrete" if c == "ESC1" else "Blasting — steel / vessel")}
                    for c in sorted(await PR.prep_codes(session))]
    out = []
    for (site, day, tag), g in sorted(groups.items(), key=lambda kv: (
            0 if kv[1]["rejected"] else 1, kv[0][1], kv[0][2])):
        prep_rows = [r for r in g["rows"] if _sap(r["sap_code"]) in garnet]
        if prep_rows:
            ids = {r["consumption_id"] for r in prep_rows}
            p = {**g, "rows": prep_rows,
                 "rejected": [x for x in g["rejected"] if x["consumption_id"] in ids],
                 "edited": sum(1 for r in prep_rows if r.get("reason") == "edited"),
                 "kind": "prep", "candidates": [], "suggested_code": None,
                 "prep_code": await PR.code_for_tag(session, site_id=site, tag=tag),
                 "prep_options": prep_options,
                 "surface_hint": await PR.last_state(session, site_id=site, tag=tag),
                 "sqm_hint": sqm_hint([r["remarks"] for r in prep_rows]),
                 "notes": notes_of(prep_rows),
                 "key": f"{site}|{day}|{tag}|prep"}
            out.append(p)
            g = {**g, "rows": [r for r in g["rows"] if r["consumption_id"] not in ids],
                 "rejected": [x for x in g["rejected"] if x["consumption_id"] not in ids]}
            g["edited"] = sum(1 for r in g["rows"] if r.get("reason") == "edited")
            if not g["rows"]:
                continue
        saps = [r["sap_code"] for r in g["rows"]]
        remarks = [r["remarks"] for r in g["rows"]]
        cands = await suggest(session, site_id=site, tag=tag, saps=saps, remarks=remarks)
        g["kind"] = "lining"
        g["candidates"] = cands
        g["suggested_code"] = g["prev_code"] or (cands[0]["code"] if cands else None)
        g["sqm_hint"] = g["prev_sqm"] or sqm_hint(remarks)
        g["notes"] = notes_of(g["rows"])
        g["key"] = f"{site}|{day}|{tag}"
        out.append(g)
    # rejected jobs first, then oldest first — the prep cards interleaved
    out.sort(key=lambda j: (0 if j["rejected"] else 1, j["work_date"], j["tag"],
                            1 if j.get("kind") == "prep" else 0))
    return {"groups": out, "total_rows": sw.get("total"),
            "unmapped": [{"site_id": s, "tank_no": t, "rows": n}
                         for (s, t), n in sorted(unmapped.items(), key=lambda x: -x[1])],
            "excluded_non_equipment": excluded}


# ── submit ────────────────────────────────────────────────────────────────────
async def _share_expected(session: AsyncSession, group_id: int, sqm: float,
                          status: str = "staged") -> None:
    """Two draws of the SAME material in one job share ONE expectation
    (rate × SQM) in proportion to what each drew — otherwise each would be
    measured against the whole job's need and both would read as under-use."""
    from .sme_link import DEFAULT_TOLERANCE_PCT, classify, variance_pct
    rows = (await session.execute(select(log_t).where(
        log_t.c["group_id"] == group_id, log_t.c["status"] == status))).mappings().all()
    by_sap: dict[str, list] = defaultdict(list)
    for r in rows:
        by_sap[_sap(r["SAP_Code"])].append(r)
    for sap, rs in by_sap.items():
        rate = rs[0]["Bench_For_1_SQM"]
        total = sum(float(r["Actual_Qty"] or 0) for r in rs)
        exp_sap = None if rate is None else round(float(rate) * sqm, 4)
        var = variance_pct(total, exp_sap)
        for r in rs:
            share = (None if exp_sap is None else
                     round(exp_sap * (float(r["Actual_Qty"] or 0) / total), 4) if total else exp_sap)
            tol = float(r["Variance_Tolerance_Pct"] or DEFAULT_TOLERANCE_PCT)
            await session.execute(update(log_t).where(log_t.c["id"] == r["id"]).values(
                Expected_Qty=share or 0.0, Variance_Pct=var, Priority_Flag=classify(var, tol)))


async def submit(session: AsyncSession, *, site_id: str, work_date: str, tag: str,
                 code: str, sqm: float, consumption_ids: list[int],
                 notes: Optional[str], username: str,
                 surface_state: Optional[str] = None,
                 work_area: Optional[str] = None) -> dict:
    """One system code and one SQM for the chosen materials of one job.

    Phase 15e: `notes` is the job's remark (pre-filled from the store keeper's,
    kept as submitted) and `work_area` the part of the equipment; both land on
    the job and on every member row.

    Phase 15d: a GARNET job is the same call with a prep code (ESC1/ESC2) and
    `surface_state` OLD | NEW; its area is benchmark-only (services/prep.py)."""
    from . import prep as PR
    from . import reconcile as RC
    from . import sme_link as SL
    ids = sorted({int(i) for i in consumption_ids or []})
    if not ids:
        raise HTTPException(422, "pick at least one material to attribute")
    sqm = float(sqm or 0)
    if sqm <= 0:
        raise HTTPException(422, "the area covered must be greater than zero")
    code, tag = (code or "").strip(), (tag or "").strip()
    rows = (await session.execute(text(
        'SELECT id, "Site_ID", "Date", "Tank_No" FROM consumption WHERE id = ANY(:i)'),
        {"i": ids})).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(404, "one or more of those consumption rows no longer exist")
    resolve = await RC.resolver(session, site_id)
    for r in rows:
        if r["Site_ID"] != site_id:
            raise HTTPException(404, f"consumption {r['id']} was posted at {r['Site_ID']}")
        if _day(r["Date"]) != _day(work_date):
            raise HTTPException(422, f"consumption {r['id']} is dated {_day(r['Date'])}, "
                                     f"not {_day(work_date)} — a group is ONE day's job")
        rt, st = resolve(r["Tank_No"])
        if rt != tag:
            raise HTTPException(
                422, f"consumption {r['id']} is logged against "
                     f"'{r['Tank_No'] or '(blank)'}', which "
                     + ("is not mapped to any equipment — map it in Tank Aliases first"
                        if st != "mapped" else f"is {rt}, not {tag}"))
    prep = await PR.is_prep(session, code)
    state = PR.norm_state(surface_state) if prep else None
    notes = " ".join(str(notes or "").split())[:500] or None
    area = " ".join(str(work_area or "").split())[:AREA_MAX] or None
    if prep and state is None:
        raise HTTPException(422, "Garnet is benchmarked per surface: say whether this "
                                 "job was an OLD surface or a NEW surface.")
    gid = (await session.execute(pg_insert(group_t).values(
        Site_ID=site_id, Work_Date=_day(work_date), Equipment_Tag_No=tag,
        Lining_System_Code=code, SQM_Completed=sqm, status="staged", notes=notes,
        Surface_State=state, Work_Area=area,
        submitted_by=username).returning(group_t.c["id"]))).scalar_one()
    results, joined = [], 0
    for cid in ids:
        res = await SL.assign(session, consumption_id=cid, code=code, tag=tag, sqm=sqm,
                              work_date=_day(work_date), notes=notes,
                              username=username, site_id=site_id, surface_state=state)
        results.append(res)
        # A fresh or bounced row joins THIS group; a revision of an approved row
        # stays with its own job (decide_revision moves that job's credit).
        n = (await session.execute(update(log_t).where(
            log_t.c["id"] == int(res["id"]), log_t.c["status"] == "staged",
            log_t.c["Consumption_ID"] == cid).values(group_id=gid, Work_Area=area))).rowcount
        joined += n or 0
    if not joined:
        await session.execute(text('DELETE FROM sme_attribution_group WHERE id = :g'),
                              {"g": gid})
        gid = None
    else:
        await _share_expected(session, gid, sqm)
        await _notify_hod_group(session, site_id=site_id, gid=gid, tag=tag, code=code,
                                sqm=sqm, n=joined, username=username)
    await write_audit(session, username, "SME_GROUP_SUBMIT", "sme_attribution_group",
                      f"group={gid} {_day(work_date)} {tag}/{code} sqm={sqm:g} "
                      f"rows={len(ids)} joined={joined}"
                      + (f" surface={state}" if state else "")
                      + (f" area={area!r}" if area else ""))
    return {"group_id": gid, "rows": results, "joined": joined, "status": "staged",
            "Lining_System_Code": code, "Equipment_Tag_No": tag, "SQM_Completed": sqm,
            "Surface_State": state, "Work_Area": area, "notes": notes}


async def submit_one(session: AsyncSession, *, consumption_id: int, code: str, tag: str,
                     sqm: float, work_date: Optional[str], notes: Optional[str],
                     username: str, site_id: Optional[str],
                     surface_state: Optional[str] = None) -> dict:
    """The per-row API as a GROUP OF ONE (ruling Q14-16).

    Exactly the Phase 13 semantics — no same-day or same-tank requirement, the
    row's own date when none is given — and the response is the row's own, plus
    the group it now belongs to. A revision of an approved row stays with its
    own job; no empty group is left behind."""
    from . import sme_link as SL
    row = (await session.execute(text(
        'SELECT "Site_ID", "Date" FROM consumption WHERE id = :i'),
        {"i": consumption_id})).mappings().first()
    res = await SL.assign(session, consumption_id=consumption_id, code=code, tag=tag,
                          sqm=sqm, work_date=work_date, notes=notes,
                          username=username, site_id=site_id,
                          surface_state=surface_state)
    staged = (await session.execute(select(log_t.c["id"]).where(
        log_t.c["id"] == int(res["id"]), log_t.c["status"] == "staged",
        log_t.c["Consumption_ID"] == consumption_id,
        log_t.c["group_id"].is_(None)))).scalar()
    if staged is not None and row is not None:
        gid = (await session.execute(pg_insert(group_t).values(
            Site_ID=row["Site_ID"], Work_Date=_day(work_date or row["Date"]),
            Equipment_Tag_No=tag.strip(), Lining_System_Code=code.strip(),
            SQM_Completed=float(sqm), status="staged", notes=notes,
            Surface_State=res.get("Surface_State"),
            submitted_by=username).returning(group_t.c["id"]))).scalar_one()
        await session.execute(update(log_t).where(log_t.c["id"] == int(res["id"]))
                              .values(group_id=gid))
        res = {**res, "group_id": gid}
    elif staged is None:
        # A bounced row re-answered in place keeps its job; keep the job's
        # answer in step with the row's (it is a group of one).
        gid = (await session.execute(select(log_t.c["group_id"]).where(
            log_t.c["id"] == int(res["id"])))).scalar()
        if gid:
            await session.execute(update(group_t).where(
                group_t.c["id"] == gid, group_t.c["status"].in_(("staged", "rejected")))
                .values(Lining_System_Code=code.strip(), Equipment_Tag_No=tag.strip(),
                        SQM_Completed=float(sqm), status="staged", rejected_reason=None,
                        Surface_State=res.get("Surface_State")))
        res = {**res, "group_id": gid}
    return res


async def _notify_hod_group(session, *, site_id, gid, tag, code, sqm, n, username) -> None:
    from .notifications import dispatch
    try:
        await dispatch(session, event_key="sme_group_submitted",
                       title=f"Surface Shield job to approve — {tag}",
                       body=f"{n} material(s) on {tag} attributed to {code}, {sqm:g} m². "
                            f"Approve or reject the job as a whole.",
                       recipient_role="hod", recipient_site=site_id,
                       link_page="/execution", related_table="sme_attribution_group",
                       related_ref=str(gid), created_by=username)
    except Exception:                                   # noqa: BLE001 — never fatal
        pass


# ── the HOD decides the WHOLE job ─────────────────────────────────────────────
async def decide_group(session: AsyncSession, *, group_id: int, approve: bool,
                       edits: Optional[dict], justification: str, reject_reason: str,
                       username: str, site_id: Optional[str]) -> dict:
    from . import execution as X
    from . import sme_link as SL
    g = (await session.execute(select(group_t).where(group_t.c["id"] == group_id)
                               )).mappings().first()
    if g is None or (site_id is not None and g["Site_ID"] != site_id):
        raise HTTPException(404, f"group {group_id} not found")
    if g["status"] != "staged":
        raise HTTPException(409, f"group {group_id} is already {g['status']}")
    logs = (await session.execute(select(log_t.c["id"], log_t.c["entered_by"]).where(
        log_t.c["group_id"] == group_id, log_t.c["status"] == "staged")
        .order_by(log_t.c["id"]))).all()
    if not logs:
        raise HTTPException(409, f"group {group_id} has no rows awaiting a decision")
    results = []
    if not approve:
        reason = (reject_reason or "").strip()
        if not reason:
            raise HTTPException(422, "a rejection needs a reason — the person who filed "
                                     "this has to know what to do differently.")
        for lid, _by in logs:
            results.append(await SL.decide(
                session, log_id=int(lid), approve=False, edits=None, justification="",
                reject_reason=reason, username=username, site_id=site_id, notify=False))
        await session.execute(update(group_t).where(group_t.c["id"] == group_id).values(
            status="rejected", rejected_reason=reason, hod_username=username,
            hod_decided_at=func.now()))
        await SL._notify_submitter(session, username=logs[0][1], site_id=g["Site_ID"],
                                   log_id=int(logs[0][0]), reason=reason, actor=username)
        return {"group_id": group_id, "status": "rejected", "reason": reason,
                "rows": results, "Done_SQM_credited": 0.0}
    edits = dict(edits or {})
    for lid, _by in logs:
        results.append(await SL.decide(
            session, log_id=int(lid), approve=True, edits=edits,
            justification=justification, reject_reason="", username=username,
            site_id=site_id, credit=False))
    code = str(edits.get("Lining_System_Code") or g["Lining_System_Code"]).strip()
    tag = str(edits.get("Equipment_Tag_No") or g["Equipment_Tag_No"]).strip()
    sqm = float(edits.get("SQM_Completed", g["SQM_Completed"]) or 0)
    # ⚠️ ONCE. The job's area, credited by the same function every path uses —
    # which credits nothing for a surface-prep (Garnet) job (Phase 15d).
    from . import prep as PR
    credited = 0.0 if await PR.is_prep(session, code) else sqm
    await X.credit_done_sqm(session, site_id=g["Site_ID"], tag=tag, code=code, sqm=sqm)
    await session.execute(update(group_t).where(group_t.c["id"] == group_id).values(
        status="committed", Lining_System_Code=code, Equipment_Tag_No=tag,
        SQM_Completed=sqm, Done_SQM_Credited=credited, hod_username=username,
        hod_decided_at=func.now()))
    if "SQM_Completed" in edits:
        # decide() recomputed each row against the whole job; re-share per SAP.
        await session.execute(update(log_t).where(log_t.c["group_id"] == group_id,
                                                  log_t.c["id"].in_([int(x[0]) for x in logs]))
                              .values(status="staged"))
        await _share_expected(session, group_id, sqm)
        await session.execute(update(log_t).where(log_t.c["group_id"] == group_id,
                                                  log_t.c["id"].in_([int(x[0]) for x in logs]))
                              .values(status="committed"))
    await write_audit(session, username, "SME_GROUP_APPROVE", "sme_attribution_group",
                      f"group={group_id} {tag}/{code} sqm={sqm:g} rows={len(logs)}")
    first = results[0] if results else {}
    return {**first, "group_id": group_id, "status": "committed", "rows": results,
            "Lining_System_Code": code, "Equipment_Tag_No": tag, "SQM_Completed": sqm,
            "Surface_State": g.get("Surface_State"), "Done_SQM_credited": credited}


async def group_of_log(session: AsyncSession, log_id: int) -> Optional[int]:
    return (await session.execute(select(log_t.c["group_id"]).where(
        log_t.c["id"] == log_id))).scalar()


async def staged_groups(session: AsyncSession, *, site_id: Optional[str]) -> list[dict]:
    """The HOD's queue: one card per job, with its member rows."""
    stmt = select(group_t).where(group_t.c["status"] == "staged")
    if site_id is not None:
        stmt = stmt.where(group_t.c["Site_ID"] == site_id)
    groups = [dict(r) for r in (await session.execute(
        stmt.order_by(group_t.c["Work_Date"], group_t.c["id"]))).mappings().all()]
    if not groups:
        return []
    rows = (await session.execute(select(log_t).where(
        log_t.c["group_id"].in_([g["id"] for g in groups]),
        log_t.c["status"] == "staged").order_by(log_t.c["id"]))).mappings().all()
    by: dict[int, list] = defaultdict(list)
    for r in rows:
        by[int(r["group_id"])].append({k: r[k] for k in (
            "id", "Consumption_ID", "SAP_Code", "Material_Code", "Pack_Qty",
            "Unit_Size_Used", "Actual_Qty", "Expected_Qty", "Variance_Pct",
            "Priority_Flag", "Bench_For_1_SQM")})
    # Phase 20a: the store keeper's own words from the Excel log, verbatim —
    # the HOD reads what the SQM came from, not only the note it was filed with.
    cids = [int(r["Consumption_ID"]) for r in rows if r["Consumption_ID"] is not None]
    remark_of = {int(i): rem for i, rem in (await session.execute(text(
        'SELECT id, "Remarks" FROM consumption WHERE id = ANY(:i)'), {"i": cids})).all()} \
        if cids else {}
    excel: dict[int, list] = defaultdict(list)
    for r in rows:
        t = " ".join(str(remark_of.get(int(r["Consumption_ID"] or 0)) or "").split())
        if t and t.lower() not in {x.lower() for x in excel[int(r["group_id"])]}:
            excel[int(r["group_id"])].append(t)
    from . import prep as PR
    prep = await PR.prep_codes(session)
    for g in groups:
        g["excel_remarks"] = excel.get(int(g["id"]), [])
        g["rows"] = by.get(int(g["id"]), [])
        g["high_priority"] = any(r["Priority_Flag"] == "HIGH" for r in g["rows"])
        g["prep"] = str(g["Lining_System_Code"] or "").strip() in prep
    return groups


# ── a revision of one member of an approved multi-material job ────────────────
async def revise_group_credit(session: AsyncSession, *, log: dict, code: str,
                              tag: str, sqm: float) -> bool:
    """Called by `sme_link.decide_revision` BEFORE it moves any credit.

    Returns True when the job's credit was handled here (a multi-material job),
    False when the caller's per-row movement is right (no group, or a group of
    one — whose group row is kept in step)."""
    from . import execution as X
    gid = log.get("group_id")
    if not gid:
        return False
    g = (await session.execute(select(group_t).where(group_t.c["id"] == gid)
                               )).mappings().first()
    members = (await session.execute(select(func.count()).select_from(log_t).where(
        log_t.c["group_id"] == gid))).scalar_one()
    if g is None or g["status"] != "committed":
        return False
    from . import prep as PR
    cred = 0.0 if await PR.is_prep(session, code) else sqm
    if members <= 1:
        await session.execute(update(group_t).where(group_t.c["id"] == gid).values(
            Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm,
            Done_SQM_Credited=cred))
        return False
    same = (g["Lining_System_Code"] == code and g["Equipment_Tag_No"] == tag
            and abs(float(g["SQM_Completed"] or 0) - float(sqm)) < 1e-9)
    if not same:
        credited = float(g["Done_SQM_Credited"] or g["SQM_Completed"] or 0)
        if credited:
            await X.credit_done_sqm(session, site_id=g["Site_ID"],
                                    tag=g["Equipment_Tag_No"],
                                    code=g["Lining_System_Code"], sqm=-credited)
        await X.credit_done_sqm(session, site_id=g["Site_ID"], tag=tag, code=code, sqm=sqm)
        await session.execute(update(group_t).where(group_t.c["id"] == gid).values(
            Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm,
            Done_SQM_Credited=cred))
        await session.execute(update(log_t).where(
            log_t.c["group_id"] == gid, log_t.c["id"] != log["id"]).values(
            Lining_System_Code=code, Equipment_Tag_No=tag, SQM_Completed=sqm))
        if code != g["Lining_System_Code"]:
            # a new system code is a new recipe: every member's own rate
            from .sme_link import recipe_rate
            for m in (await session.execute(select(
                    log_t.c["id"], log_t.c["Material_Code"], log_t.c["SAP_Code"]).where(
                    log_t.c["group_id"] == gid, log_t.c["id"] != log["id"]))).all():
                rate = await recipe_rate(session, code=code, material_code=m[1],
                                         sap_code=m[2], surface_state=g.get("Surface_State"))
                await session.execute(update(log_t).where(log_t.c["id"] == m[0])
                                      .values(Bench_For_1_SQM=rate))
    return True


async def reshare_committed(session: AsyncSession, *, group_id: int, sqm: float) -> None:
    """After a revision moved a committed job's answer: every member's
    expectation (rate × the job's SQM, shared per SAP) and its flag."""
    await _share_expected(session, group_id, sqm, status="committed")
