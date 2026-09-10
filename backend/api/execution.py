"""
backend/api/execution.py — Phase 5 consumption workflow endpoints.

Routing mirrors who holds the knowledge, not who is senior:

  POST /execution/entries            store keeper OR supervisor (see below)
  POST /execution/entries/{id}/submit          store keeper → supervisor
  POST /execution/entries/{id}/supervisor      supervisor   → HOD
  POST /execution/entries/{id}/decision        HOD approve / reject
  GET  /execution/entries            the queue, filtered by status
  GET  /execution/entries/{id}       one entry with its variance
  GET  /execution/activities         what a supervisor may open directly

⚠️ The supervisor route accepts NO material fields. That is the control, not an
omission: a supervisor is measured against the consumption a store keeper
counted, and letting them edit it would let a bad number be tidied by the
person it reflects on. Only the HOD can change both sides, and only with a
justification the supervisor is notified of.
"""
from __future__ import annotations

import asyncio as _aio
import base64
import io
import json
from typing import Optional

from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Query,
                     Response, UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import insert

from .ai import form_jobs
from .ai import jobs as aijobs
from .ai import ocr
from .ai import ocr_form as OF
from .auth import (get_current_user, require_roles, resolve_site_param,
                   site_row_visible, site_scope)
from .db import get_session
from .services import consumption_form as CF
from .services import execution as X
from .services.ledger import _MD

router = APIRouter(prefix="/execution", tags=["execution"])

norm_t = _MD.tables["sme_manpower_norm"]
recipe_t = _MD.tables["sme_recipe"]
form_t = _MD.tables["sme_consumption_form"]
entry_t = _MD.tables["sme_execution_entry"]
ai_jobs_t = _MD.tables["ai_jobs"]


def _write_site(user: dict, requested: Optional[str]) -> str:
    """The site a write lands on. Below logistics a user is pinned to their own;
    above it, one must be named — a global write with no site is how a row ends
    up belonging to nobody."""
    own = site_scope(user)
    if own:
        return own
    sid = (requested or "").strip()
    if not sid:
        raise HTTPException(422, "site_id is required for a global role")
    return sid


class MaterialIn(BaseModel):
    Material_Code: str = Field(min_length=1)
    SAP_Code: Optional[str] = ""
    Actual_Qty: float = Field(ge=0)
    UOM: Optional[str] = None
    Lot_No: Optional[str] = None


class OpenIn(BaseModel):
    work_date: str
    equipment_tag: str
    # '' or omitted = system-agnostic (surface prep belongs to no lining
    # system). Sent as the empty string, never null — see models.SmeExecutionEntry.
    lining_system_code: str = ""
    execution_sub_activity_code: str
    variant_key: str = ""
    # 'Day' | 'Night' | None. Optional on purpose: the Track 2 chase alert reads
    # it, and a supervisor who does not set it is simply not covered by that
    # alert rather than blocked from filing. `None` stays NULL — see
    # models.SmeExecutionEntry.Shift for why NULL is never guessed at.
    shift: Optional[str] = Field(default=None, pattern="^(Day|Night)$")
    materials: list[MaterialIn] = []
    site_id: Optional[str] = None


class ManpowerIn(BaseModel):
    Role_Code: str = Field(min_length=1)
    Headcount: float = Field(ge=0)
    Hours: float = Field(ge=0)


class MaterialLineIn(BaseModel):
    id: int
    Actual_Qty: Optional[float] = Field(default=None, ge=0)
    Lot_No: Optional[str] = None


class SupervisorIn(BaseModel):
    actual_sqm: float = Field(gt=0)
    manpower: list[ManpowerIn]
    material_variance_reason: str = Field(min_length=1)
    manpower_variance_reason: str = Field(min_length=1)
    # ⚠️ PHASE 9d: the supervisor now sends material figures, which Phase 5
    # explicitly refused. They authored the paper; refusing their numbers would
    # mean refusing the record. What replaces the old control is that their
    # figure is kept as `Supervisor_Qty` beside what the camera read.
    materials: list[MaterialLineIn] = []
    execution_sub_activity_code: Optional[str] = None
    variant_key: Optional[str] = None
    site_id: Optional[str] = None


class SkVerifyIn(BaseModel):
    materials: list[MaterialLineIn] = []
    reason: str = ""
    site_id: Optional[str] = None


class MaterialEdit(BaseModel):
    id: int
    Actual_Qty: Optional[float] = Field(default=None, ge=0)
    Lot_No: Optional[str] = None


class ManpowerEdit(BaseModel):
    id: int
    Headcount: Optional[float] = Field(default=None, ge=0)
    Hours: Optional[float] = Field(default=None, ge=0)


class DecisionIn(BaseModel):
    approve: bool
    reject_reason: str = ""
    justification: str = ""
    actual_sqm: Optional[float] = Field(default=None, gt=0)
    materials: list[MaterialEdit] = []
    manpower: list[ManpowerEdit] = []
    # ⚠️ Ruling Q2-D. Approving past an uncleared certificate is allowed and
    # costs a written reason plus a notification to the Head of Qualities. The
    # material was applied days ago; refusing outright only strands the record.
    qsep_override: bool = False
    qsep_reason: str = ""
    site_id: Optional[str] = None


@router.get("/activities", summary="Sub-activities, and who may open each")
async def activities(site_id: Optional[str] = None,
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    """The picker behind both entry forms.

    `manpower_only` is what the UI keys on: those activities consume no Surface
    Shield, so a supervisor opens them directly AND the lining-system dropdown
    is hidden — surface prep belongs to no system, and forcing a choice would
    trap the hours under whichever one was guessed.
    """
    material_keys = {(str(a), str(b)) for a, b in (await session.execute(
        select(recipe_t.c["Lining_System_Code"],
               recipe_t.c["Execution_Sub_Activity_Code"]).distinct())).all()}
    rows = (await session.execute(
        select(norm_t.c["Lining_System_Code"],
               norm_t.c["Execution_Sub_Activity_Code"], norm_t.c["Activity"],
               norm_t.c["Sub_Activity"], norm_t.c["Variant_Key"],
               norm_t.c["Type"], norm_t.c["Crew_Size"],
               norm_t.c["Standard_Productivity_Per_Shift"])
        .order_by(norm_t.c["Type"], norm_t.c["Lining_System_Code"],
                  norm_t.c["Execution_Sub_Activity_Code"]))).mappings().all()
    out = []
    for r in rows:
        code, esc = str(r["Lining_System_Code"]), str(r["Execution_Sub_Activity_Code"])
        only = (code, esc) not in material_keys
        out.append({
            **{k: r[k] for k in r.keys()},
            "manpower_only": only,
            # A norm whose system column holds an ESC code describes work that
            # belongs to no lining system; entries for it store ''.
            "system_agnostic": only and not code.startswith("LSC"),
        })
    return {"items": out}


@router.post("/entries", status_code=201, summary="Open an execution entry")
async def open_entry(body: OpenIn = Body(...),
                     user: dict = Depends(require_roles("store_keeper",
                                                        "supervisor", "hod")),
                     session: AsyncSession = Depends(get_session)):
    sid = _write_site(user, body.site_id)
    res = await X.open_entry(
        session, username=user["username"], role=user["role"], site_id=sid,
        work_date=body.work_date, equipment_tag=body.equipment_tag,
        code=body.lining_system_code, esc=body.execution_sub_activity_code,
        variant=body.variant_key, shift=body.shift,
        materials=[m.model_dump() for m in body.materials])
    await session.commit()
    return res


@router.post("/entries/{entry_id}/sk-verify", summary="Store keeper → HOD")
async def sk_verify(entry_id: int, body: SkVerifyIn = Body(...),
                    user: dict = Depends(require_roles("store_keeper", "hod")),
                    session: AsyncSession = Depends(get_session)):
    """The store keeper checks the supervisor's figures against the store.

    ⚠️ THIS REPLACED `POST /entries/{id}/submit`, and the direction reversed
    with it. The old route sent an SK draft TO the supervisor; there is no such
    draft any more — the record starts with the supervisor's paper.
    """
    res = await X.sk_verify(
        session, username=user["username"], entry_id=entry_id,
        site_id=resolve_site_param(user, body.site_id),
        materials=[m.model_dump(exclude_unset=True) for m in body.materials],
        reason=body.reason)
    await session.commit()
    return res


@router.post("/entries/{entry_id}/supervisor",
             summary="Supervisor files the form → SK (or HOD if manpower-only)")
async def supervisor_submit(entry_id: int, body: SupervisorIn = Body(...),
                            user: dict = Depends(require_roles("supervisor", "hod")),
                            session: AsyncSession = Depends(get_session)):
    res = await X.supervisor_submit(
        session, username=user["username"], entry_id=entry_id,
        site_id=resolve_site_param(user, body.site_id),
        actual_sqm=body.actual_sqm,
        manpower=[m.model_dump() for m in body.manpower],
        material_reason=body.material_variance_reason,
        manpower_reason=body.manpower_variance_reason,
        materials=[m.model_dump(exclude_unset=True) for m in body.materials],
        esc=body.execution_sub_activity_code, variant=body.variant_key)
    await session.commit()
    return res


@router.post("/entries/{entry_id}/decision", summary="HOD approve / reject")
async def hod_decision(entry_id: int, body: DecisionIn = Body(...),
                       user: dict = Depends(require_roles("hod")),
                       session: AsyncSession = Depends(get_session)):
    res = await X.hod_decide(
        session, username=user["username"], entry_id=entry_id,
        site_id=resolve_site_param(user, body.site_id), approve=body.approve,
        reject_reason=body.reject_reason, justification=body.justification,
        qsep_override=body.qsep_override, qsep_reason=body.qsep_reason,
        edits={"Actual_SQM": body.actual_sqm,
               "materials": [m.model_dump(exclude_unset=True)
                             for m in body.materials],
               "manpower": [m.model_dump() for m in body.manpower]})
    await session.commit()
    return res


@router.get("/entries", summary="The execution queue")
async def list_entries(status: Optional[str] = Query(default=None),
                       site_id: Optional[str] = None,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    return {"items": await X.list_entries(
        session, site_id=resolve_site_param(user, site_id),
        statuses=statuses or None)}


@router.get("/entries/{entry_id}", summary="One entry, with its variance")
async def get_entry(entry_id: int, site_id: Optional[str] = None,
                    user: dict = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    return await X.get_entry(session, entry_id,
                             resolve_site_param(user, site_id))


# ─── Phase 6: reporting + exports ────────────────────────────────────────────
# ⚠️ RULE 12. Every export routes through `reports.to_csv` / `reports.to_xlsx`,
# which apply `_defuse` (csv) and `xl_val` (xlsx). These reports carry
# `Material_Variance_Reason`, `Manpower_Variance_Reason` and
# `HOD_Edit_Justification` — FREE TEXT typed by a supervisor and opened in
# Excel by an HOD, which is exactly the shape the rule exists for. Never hand
# rows to `csv.writer` or openpyxl directly here.
_EXPORT_MEDIA = {
    "csv": ("text/csv", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "xlsx"),
}


def _export(fmt: str, title: str, columns: list[str], rows: list[list],
            username: str):
    from fastapi.responses import StreamingResponse

    from .reports import to_csv, to_xlsx
    fmt = (fmt or "xlsx").lower()
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(422, f"format must be one of {sorted(_EXPORT_MEDIA)}")
    media, ext = _EXPORT_MEDIA[fmt]
    data = (to_csv(title, columns, rows, username) if fmt == "csv"
            else to_xlsx(title, columns, rows, username))
    import io as _io
    fname = f"{title.lower().replace(' ', '_')}.{ext}"
    return StreamingResponse(_io.BytesIO(data), media_type=media,
                             headers={"Content-Disposition":
                                      f'attachment; filename="{fname}"'})


_VARIANCE_COLUMNS = [
    "Entry_No", "Work_Date", "Equipment_Tag_No", "Lining_System_Code",
    "Execution_Sub_Activity_Code", "Variant_Key", "status", "Actual_SQM",
    "Material_Actual", "Material_Benchmark", "Material_Variance",
    "Material_Variance_Pct", "Manpower_Actual_Manhours",
    "Manpower_Benchmark_Manhours", "Manpower_Variance_Manhours",
    "Manpower_Variance_Pct", "Actual_Headcount", "Benchmark_Crew_Size",
    "Material_Variance_Reason", "Manpower_Variance_Reason",
    "supervisor_username", "hod_username", "hod_edited",
    "HOD_Edit_Justification",
]

_REASON_COLUMNS = [
    "Entry_No", "Work_Date", "Equipment_Tag_No",
    "Execution_Sub_Activity_Code", "status", "supervisor_username",
    "Material_Variance_Reason", "Manpower_Variance_Reason", "hod_username",
    "hod_edited", "Changed", "HOD_Edit_Justification", "Reject_Reason",
]

_PREP_COLUMNS = [
    "Equipment_Tag_No", "Execution_Sub_Activity_Code", "Variant_Key",
    "Activity", "Done_SQM", "Equipment_Area_SQM", "Coverage_Pct",
    "Entry_Count", "Last_Entry_No",
]


@router.get("/report/variance", summary="Actual vs benchmark, per entry")
async def report_variance(date_from: Optional[str] = None,
                          date_to: Optional[str] = None,
                          status: Optional[str] = None,
                          site_id: Optional[str] = None,
                          format: Optional[str] = Query(default=None),
                          user: dict = Depends(require_roles("hod", "supervisor",
                                                             "auditor")),
                          session: AsyncSession = Depends(get_session)):
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    data = await X.variance_report(
        session, site_id=resolve_site_param(user, site_id),
        date_from=date_from, date_to=date_to, statuses=statuses or None)
    if not format:
        return data
    rows = [[r.get(c) for c in _VARIANCE_COLUMNS] for r in data["items"]]
    return _export(format, "Execution Variance", _VARIANCE_COLUMNS, rows,
                   user["username"])


@router.get("/report/reasons", summary="Stated reasons and HOD corrections")
async def report_reasons(site_id: Optional[str] = None,
                         format: Optional[str] = Query(default=None),
                         user: dict = Depends(require_roles("hod", "auditor")),
                         session: AsyncSession = Depends(get_session)):
    items = await X.reason_log(session,
                               site_id=resolve_site_param(user, site_id))
    if not format:
        return {"items": items}
    rows = [[r.get(c) for c in _REASON_COLUMNS] for r in items]
    return _export(format, "Variance Reason Log", _REASON_COLUMNS, rows,
                   user["username"])


@router.get("/report/surface-prep", summary="Surface-prep area, kept apart "
                                            "from lining progress")
async def report_surface_prep(site_id: Optional[str] = None,
                              format: Optional[str] = Query(default=None),
                              user: dict = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    data = await X.surface_prep_report(
        session, site_id=resolve_site_param(user, site_id))
    if not format:
        return data
    rows = [[r.get(c) for c in _PREP_COLUMNS] for r in data["items"]]
    return _export(format, "Surface Prep Progress", _PREP_COLUMNS, rows,
                   user["username"])


# ── the printed consumption form (Phase 9c) ──────────────────────────────────
# ⚠️ MOUNTED HERE, NOT UNDER `/mh`. Man-Hours is exact-locked to {hod, admin}
# and a supervisor is the person who actually carries this paper into the
# plant — putting the download behind that lock would hand the form to
# everybody except its user. `/execution` already belongs to exactly the three
# roles the ruling names (SK, supervisor, HOD), which is why the queue they
# share lives here too.
_FORM_ROLES = ("store_keeper", "supervisor", "hod")


@router.get("/forms", summary="Lining systems that have a printable form")
async def form_systems(user: dict = Depends(require_roles(*_FORM_ROLES)),
                       session: AsyncSession = Depends(get_session)):
    """The picker. Only systems that HAVE a recipe are offered — a menu entry
    that always errors is worse than no entry."""
    return {"items": await CF.available_systems(session)}


@router.get("/forms/generated",
            summary="Forms printed for this site, newest first")
async def forms_generated(status: Optional[str] = Query(None,
                                                        pattern="^(open|consumed|void)$"),
                          limit: int = Query(50, ge=1, le=500),
                          site_id: Optional[str] = Query(None),
                          user: dict = Depends(require_roles(*_FORM_ROLES)),
                          session: AsyncSession = Depends(get_session)):
    """What has been printed and not yet filed — the supervisor's own question,
    and the one an HOD asks when paper goes missing."""
    site = resolve_site_param(user, site_id)
    stmt = select(form_t)
    if site is not None:
        stmt = stmt.where(form_t.c["Site_ID"] == site)
    if status:
        stmt = stmt.where(form_t.c["status"] == status)
    rows = (await session.execute(
        stmt.order_by(form_t.c["id"].desc()).limit(limit))).mappings().all()
    return {"items": [dict(r) | {"created_at": str(dict(r).get("created_at") or ""),
                                 "consumed_at": str(dict(r).get("consumed_at") or "")}
                      for r in rows]}


@router.get("/forms/{system_code}",
            summary="Generate and download printable consumption forms")
async def form_download(system_code: str,
                        esc: Optional[str] = Query(
                            None, description="One sub-activity; omit for every "
                                              "material on the system"),
                        copies: int = Query(
                            1, ge=1, le=CF.MAX_COPIES,
                            description="How many FORMS to print. Each one is a "
                                        "separate numbered sheet with its own QR "
                                        "code — a form longer than 18 materials "
                                        "still spans several A4 pages."),
                        site_id: Optional[str] = Query(None),
                        user: dict = Depends(require_roles(*_FORM_ROLES)),
                        session: AsyncSession = Depends(get_session)):
    """⚠️ THIS IS A WRITE, despite being a GET. Every download REGISTERS a new
    form with its own `Form_UUID`, because two prints of "the same" form are two
    physical sheets and slice 9d has to be able to tell them apart on the way
    back in. Downloading twice deliberately gives you two different papers, not
    one paper twice — the alternative is a duplicate-detection rule that cannot
    distinguish a re-print from a re-photograph.

    ⚠️ AND `copies` IS COUNTED IN FORMS, NOT IN SHEETS OF A4 (Phase 13a, ruling
    Q13-1). A form is what the QR identifies, what the fingerprint pins the row
    order of, and what the intake consumes; a 40-material recipe has always
    printed as three A4 pages under ONE identity. Counting the user's number in
    sheets would make it mean something the system has no concept of, so the UI
    asks for forms and shows the resulting sheet count beside the box.

    It stays a GET so a browser can open it in a new tab and the native shells
    can hand it to the OS viewer; the audit row records who printed what.
    """
    site = _write_site(user, site_id)
    # ⚠️ BEFORE THE TRANSACTION, because `check_bucket_shared` COMMITS — calling
    # it inside `session.begin()` would end the transaction the registry rows
    # are supposed to be atomic within. It also fails open on storage trouble,
    # which is the right direction for a print: nobody should be unable to
    # print paper because a counter table is unwell.
    #
    # Only a bulk run is throttled. A double-click on a single download costs
    # one spare sheet; a double-click on `copies=200` costs 400 registry rows.
    if copies > 1:
        from .ratelimit import check_bucket_shared
        await check_bucket_shared(
            session, f"formprint:{user['username']}", 3, 60,
            "that is three bulk print runs in a minute — wait a moment. Each "
            "run registers every sheet it prints, and a run nobody collects "
            "leaves those sheets showing as outstanding.")
    async with session.begin():
        pdf, row = await CF.generate(session, site_id=site,
                                     code=system_code.strip(),
                                     esc=(esc or "").strip() or None,
                                     username=user["username"], role=user["role"],
                                     copies=copies)
    fname = (f"consumption-{row['Lining_System_Code']}"
             + (f"-{row['Execution_Sub_Activity_Code']}"
                if row["Execution_Sub_Activity_Code"] else "")
             + (f"-x{copies}-{row['Batch_UUID']}" if copies > 1
                else f"-{row['Form_UUID']}")
             + ".pdf")
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"',
                 # The UI needs the ids it just created without parsing the PDF,
                 # so the registry row rides back on the headers. `X-Form-UUID`
                 # keeps naming the FIRST sheet, so a pre-13a caller reading it
                 # still gets a real form id rather than a batch id it cannot
                 # look up.
                 "X-Form-UUID": row["Form_UUID"],
                 "X-Form-Rows": str(row["Row_Count"]),
                 "X-Form-Batch": row["Batch_UUID"],
                 "X-Form-Count": str(row["Batch_Size"]),
                 "Access-Control-Expose-Headers":
                     "X-Form-UUID, X-Form-Rows, X-Form-Batch, X-Form-Count"})


# ── the OCR lane (Phase 9d) ──────────────────────────────────────────────────
# ⚠️ A JOB, NOT AN INLINE AWAIT. Vision OCR takes 5–120 s on a 7B model with a
# cold start — longer than proxy timeouts and far longer than a supervisor
# standing in a plant on mobile data will hold a page open. Same contract as
# `ai/jobs.py`: POST returns 202 with an id, React polls, and the state lives in
# Postgres so a locked phone loses nothing.
_ALLOWED_UPLOAD = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/heic": "heic", "image/heif": "heic", "application/pdf": "pdf",
}
_MAX_UPLOAD = 20 * 1024 * 1024


@router.post("/ocr/upload", status_code=202,
             summary="Photograph a filled consumption form (supervisor)")
async def ocr_upload(file: UploadFile = File(...),
                     site_id: Optional[str] = Form(None),
                     user: dict = Depends(require_roles("supervisor", "hod",
                                                        "store_keeper")),
                     session: AsyncSession = Depends(get_session)):
    """Queue a photographed form for reading.

    ⚠️ RAW IS NOT ACCEPTED (ruling Q6). CR2/NEF/ARW need libraw, weigh 20–50 MB
    and come from a DSLR nobody carries into a tank. PDF is accepted because
    office scanners and phone scanner apps default to it.
    """
    sid = _write_site(user, site_id)
    mime = (file.content_type or "").lower()
    if mime not in _ALLOWED_UPLOAD:
        raise HTTPException(
            415, f"{mime or 'that file type'} cannot be read. Send a JPG, PNG, "
                 f"HEIC or PDF photograph of the whole form.")
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "that file was empty")
    if len(raw) > _MAX_UPLOAD:
        raise HTTPException(
            413, f"that file is {len(raw) // (1024 * 1024)} MB; the limit is "
                 f"{_MAX_UPLOAD // (1024 * 1024)} MB. Most phones let you send "
                 f"a smaller copy.")

    # ⚠️ NORMALISED AT THE DOOR, not deep inside the worker.
    #
    # This used to base64 the raw upload straight into `payload_json`: a 20 MB
    # HEIC became ~27 MB of text in a job row, kept forever, and a 300 dpi A4
    # scan reached the model at full resolution where every extra tile is
    # wall-clock the supervisor waits for. `prep_source_image` caps the long
    # edge at a size that still decodes the QR and still rectifies for row
    # crops (see its docstring), and rasterising the PDF here rather than in the
    # worker means an unreadable file is refused NOW — while the person who
    # took the photo is still standing at the desk — instead of surfacing as a
    # dead job minutes later.
    if mime == "application/pdf":
        raw = await _aio.to_thread(form_jobs._pdf_first_page, raw)
    try:
        raw = await _aio.to_thread(ocr.prep_source_image, raw)
    except ocr.ImagePrepError as e:
        raise HTTPException(422, str(e)) from e

    jid = (await session.execute(insert(ai_jobs_t).values(
        kind="ocr_consumption_form", status="queued", actor=user["username"],
        Site_ID=sid,
        payload_json=json.dumps({"image_b64": base64.b64encode(raw).decode(),
                                 # Always a JPEG by this point — the worker's
                                 # PDF branch stays for jobs queued before this
                                 # change and for any future caller.
                                 "mime": "image/jpeg", "role": user["role"]}),
    ).returning(ai_jobs_t.c["id"]))).scalar_one()
    await session.commit()
    form_jobs.spawn(jid)
    return {"job_id": jid, "status": "queued",
            "message": "Reading the form — this usually takes under a minute."}


@router.get("/ocr/jobs/{job_id}", summary="Poll a form-reading job")
async def ocr_job(job_id: int, user: dict = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(ai_jobs_t)
           .where(ai_jobs_t.c["id"] == job_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "no such job")
    if not site_row_visible(site_scope(user), row["Site_ID"]):
        raise HTTPException(403, "that job belongs to another site")
    out = {"job_id": job_id, "status": row["status"], "error": row["error"],
           # ⚠️ THE TIMING FACTS TRAVEL WITH THE STATUS, because a status alone
           # cannot tell a supervisor the difference between "six minutes in and
           # working" and "the process died four minutes ago". The card promised
           # a minute for a read measured at 399 s, so the honest ones spun
           # until somebody gave up. See ai/jobs.EXPECTED_SECONDS.
           **aijobs.progress(row)}
    if row["result_json"]:
        out["result"] = json.loads(row["result_json"])
    return out


@router.post("/ocr/jobs/{job_id}/requeue",
             summary="Re-run a form read whose worker went away")
async def ocr_job_requeue(job_id: int,
                          user: dict = Depends(require_roles(
                              "supervisor", "hod", "store_keeper")),
                          session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(ai_jobs_t)
           .where(ai_jobs_t.c["id"] == job_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "no such job")
    if not site_row_visible(site_scope(user), row["Site_ID"]):
        raise HTTPException(403, "that job belongs to another site")
    return await aijobs.requeue(session, row, form_jobs.spawn)


@router.get("/entries/{entry_id}/qsep",
            summary="What would block this entry's approval")
async def entry_qsep(entry_id: int, site_id: Optional[str] = None,
                     user: dict = Depends(require_roles("hod", "store_keeper",
                                                        "supervisor")),
                     session: AsyncSession = Depends(get_session)):
    """⚠️ SHOWN BEFORE THE APPROVE BUTTON IS PRESSED, not after. A blocked entry
    is something an HOD can see coming and chase Logistics about; a refusal that
    arrives after they have decided teaches them to press again with the
    override on."""
    await X.get_entry(session, entry_id, resolve_site_param(user, site_id))
    return await X.qsep_status(session, entry_id)


@router.get("/entries/{entry_id}/crop", summary="A crop of the photographed form")
async def entry_crop(entry_id: int,
                     row: Optional[int] = Query(None, ge=0,
                                                description="0-based material row"),
                     field: Optional[str] = Query(None,
                                                  pattern="^(work_date|equipment|area_sqm|full)$"),
                     site_id: Optional[str] = None,
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    """The strip of the photo a number came from.

    ⚠️ WHEN THE PAGE CANNOT BE RECTIFIED THIS RETURNS THE WHOLE IMAGE AND SAYS
    SO IN A HEADER, rather than cropping by guesswork. A crop captioned "row 3"
    that is actually row 4 invites a human to confirm a quantity against the
    wrong material — worse than no crop, because it looks like verification.
    """
    entry = await X.get_entry(session, entry_id, resolve_site_param(user, site_id))
    img = (await session.execute(select(entry_t.c["OCR_Image"])
           .where(entry_t.c["id"] == entry_id))).scalar()
    if not img:
        raise HTTPException(404, "this entry has no photograph — it was typed in")

    if field == "full" or (row is None and field is None):
        return Response(content=bytes(img), media_type="image/jpeg",
                        headers={"X-Crop": "full"})

    raw = json.loads(entry.get("OCR_Raw_JSON") or "{}")
    rect, ok = OF.rectify(bytes(img), raw.get("qr_points"))
    if rect is None:
        raise HTTPException(422, "that photograph could not be decoded")
    if not ok:
        # Honest fallback: the caller gets the page, and the header says the
        # crop is not trustworthy so the UI can label it.
        return Response(content=bytes(img), media_type="image/jpeg",
                        headers={"X-Crop": "unrectified"})

    if field:
        box = CF.header_boxes()[field]
    else:
        box = CF.row_boxes(int(row))["row"]
    return Response(content=OF.crop_mm(rect, box), media_type="image/png",
                    headers={"X-Crop": "row" if row is not None else field})
