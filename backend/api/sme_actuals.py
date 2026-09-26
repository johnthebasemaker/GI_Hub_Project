"""
backend/api/sme_actuals.py — the SME portal's ACTUAL-consumption side.

Two screens' worth of API, both added 2026-08-04 with the Surface-Shield
routing:

  /sme/actuals/aliases      the `Tank No.` → equipment mapping an operator owns
  /sme/actuals/consumption  Surface-Shield draw logged from the workbook, and
                            the assignment of each row to equipment + SQM

═══════════════════════════════════════════════════════════════════════════════
⚠️  RULE 1a — THIS MODULE NEVER MOVES AN ESTIMATOR NUMBER.
═══════════════════════════════════════════════════════════════════════════════

Everything here reads and writes `sme_consumption_log` and `sme_tank_alias`.
`sme_inventory_seed` — the sole source of every SME quantity — is **not
imported, not selected and not written** anywhere in this file.

That is the whole point of the design. The operator asked to see actual
physical consumption *beside* the plan, not netted off it: an issue from the
warehouse is a warehouse event, and letting it reduce `available_qty` would
re-couple the two halves that rule 1a separated (and would silently move every
readiness figure on every SME tab). So the log is a SIDE NOTE — the UI renders
it as "Actual Physical Balance" next to the estimator's figures, adjacent and
never merged.

The `/summary` endpoint below is what that side note reads. It reports, per
material: how much the workbook says was physically drawn, and — for context
only — the estimator's own untouched seed figures are fetched by the CALLER
from its existing snapshot. This module does not join the two.

WHY ROWS ARRIVE UNASSIGNED. The Consumption Log states a `Tank No.` and a
quantity but never a system code or an area, and `Tank No.` is frequently
ambiguous (`TNK-091` matches both TRAIN J and TRAIN K — see
`bulk_import.match_alias`). Rather than guess, the sync lands those rows with
`status='unassigned'` and an empty tag, and this API is how a human resolves
them: pick the equipment, pick the system code, type the SQM actually covered.
Assigning is what computes `Expected_Qty` (= `For_1_SQM × SQM_Completed`) and
therefore the variance.

Role lock: `require_roles("hod")` — the same exact-lock as `/sme/master` and
`/mh`, because assigning consumption to equipment is master-data-grade work.
Auditors are read-only everywhere by `readonly.py`'s method-keyed middleware,
which is not modified here and to which nothing is added.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, resolve_site_param, site_row_visible, site_scope
from .bulk_import import alias_norm, match_alias
from .db import get_session
from .services.ledger import _MD, write_audit
from .sme import _rows

router = APIRouter(prefix="/sme/actuals", tags=["SME actual consumption"],
                   dependencies=[Depends(require_roles("hod"))])

alias_t = _MD.tables["sme_tank_alias"]
log_t = _MD.tables["sme_consumption_log"]
equipment_t = _MD.tables["sme_equipment"]
recipe_t = _MD.tables["sme_recipe"]


async def _material_names(session: AsyncSession,
                          codes: set[str]) -> dict[str, str]:
    """Material_Code → its human name, for the tables that only had the code.

    ⚠️ The name is read from `sme_recipe`, NOT from `sme_inventory_seed`. Both
    carry a `Material_Name`, but rule 1a makes the seed the sole source of every
    SME QUANTITY and this module deliberately never touches it (see the banner
    above) — reaching into it for a label would put the one table this file must
    not import one edit away from a quantity read. The recipe already defines
    which materials the estimator models, so it is both the safer source and the
    right grain.

    A material with no recipe line simply has no name here and the caller falls
    back to the code. That is honest: a missing label is not worth a second
    lookup path into the table rule 1a fences off.
    """
    if not codes:
        return {}
    rows = (await session.execute(
        select(recipe_t.c["Material_Code"],
               func.min(recipe_t.c["Material_Name"]).label("name"))
        .where(recipe_t.c["Material_Code"].in_(codes))
        .group_by(recipe_t.c["Material_Code"]))).all()
    return {r[0]: r[1] for r in rows if r[1]}


def _write_site(user: dict, site_id: Optional[str]) -> str:
    """Same contract as sme_master._write_site — scoped users are pinned to
    their own site, unscoped users must name one."""
    scope = site_scope(user)
    if scope is None:
        site = (site_id or "").strip()
        if not site:
            raise HTTPException(422, "site_id is required")
        return site
    if site_id is not None and site_id.strip() and site_id.strip() != scope:
        raise HTTPException(403, "you may only modify data for your own site")
    if not scope:
        raise HTTPException(403, "your account has no site; ask an admin")
    return scope


# ─── Phase 14b: QR ⇄ Excel reconciliation buckets ────────────────────────────
@router.get("/reconciliation",
            summary="How the QR execution entries and the Excel log compared, per bucket")
async def list_reconciliation(site_id: Optional[str] = None,
                              status: Optional[str] = None,
                              user: dict = Depends(require_roles("hod")),
                              session: AsyncSession = Depends(get_session)):
    """One row per (day, tag, SAP) bucket. `qr_extra` (the paper claims more
    than the book) and `possible_duplicate` are the HOD's to look at; the rest
    is the audit trail of why the ledger holds max(QR, Excel) and never the
    sum. Read services/reconcile.py for the arithmetic."""
    from .services import reconcile as RC
    site = resolve_site_param(user, site_id)
    items = await RC.list_buckets(session, site_id=site, status=status)
    open_ = [i for i in items if i["status"] in ("qr_extra", "over_ledger", "possible_duplicate")
             and not i.get("acknowledged_by")]
    return {"items": items, "needs_attention": len(open_)}


class ReconAck(BaseModel):
    note: str = Field(min_length=3)


@router.post("/reconciliation/{recon_id}/acknowledge",
             summary="HOD: I have looked at this conflict / possible duplicate")
async def acknowledge_reconciliation(recon_id: int, body: ReconAck,
                                     user: dict = Depends(require_roles("hod")),
                                     session: AsyncSession = Depends(get_session)):
    """⚠️ AN ACKNOWLEDGEMENT CHANGES NO QUANTITY. The fix for a `qr_extra` is
    made on the execution entry (its posted quantity is corrected through the
    entry — rule 3a), and for a duplicate on the workbook. This records that
    somebody looked, and why they were content — a new situation in the same
    bucket clears it (services/reconcile.upsert_bucket)."""
    from .services import reconcile as RC
    row = (await session.execute(select(RC.recon_t).where(
        RC.recon_t.c["id"] == recon_id))).mappings().first()
    if row is None or not site_row_visible(site_scope(user), row["Site_ID"]):
        raise HTTPException(404, "reconciliation row not found")
    await session.execute(update(RC.recon_t).where(RC.recon_t.c["id"] == recon_id).values(
        acknowledged_by=user["username"], acknowledged_at=func.now(),
        acknowledge_note=body.note.strip()))
    await write_audit(session, user["username"], "RECON_ACKNOWLEDGED",
                      "consumption_reconciliation",
                      f"{row['Work_Date']} {row['Equipment_Tag_No']} {row['SAP_Code']} "
                      f"[{row['status']}] — {body.note.strip()[:200]}")
    await session.commit()
    return {"acknowledged": True, "id": recon_id}


# ─── tank aliases ─────────────────────────────────────────────────────────────
@router.get("/aliases", summary="Tank No. aliases and what they resolve to")
async def list_aliases(site_id: Optional[str] = None,
                       status: Optional[str] = None,
                       user: dict = Depends(require_roles("hod")),
                       session: AsyncSession = Depends(get_session)):
    """The resolve queue. `unresolved` first — those are the ones holding rows."""
    site = resolve_site_param(user, site_id)
    stmt = select(alias_t)
    if site is not None:
        stmt = stmt.where(alias_t.c["Site_ID"] == site)
    if status:
        stmt = stmt.where(alias_t.c["status"] == status)
    items = _rows(await session.execute(stmt.order_by(
        # unresolved (0) before mapped/ignored, then biggest impact first
        func.coalesce(text("CASE WHEN status = 'unresolved' THEN 0 ELSE 1 END"), 1),
        alias_t.c["row_count"].desc(), alias_t.c["alias_raw"])))
    return {"items": items,
            "unresolved": sum(1 for i in items if i["status"] == "unresolved")}


@router.get("/aliases/{alias_id}/candidates",
            summary="Equipment tags this alias could plausibly mean")
async def alias_candidates(alias_id: int,
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    """What the matcher saw, so the operator resolves with the same evidence.

    An alias reaches the screen precisely because this list is not of length 1
    — either empty (nothing matched) or ambiguous (`TNK-091` → TRAIN J *and*
    TRAIN K). The full tag list is returned alongside so any equipment can be
    chosen, not only the near-misses.
    """
    row = (await session.execute(
        select(alias_t).where(alias_t.c["id"] == alias_id))).mappings().first()
    scope = site_scope(user)
    if row is None or (scope is not None and row["Site_ID"] != scope):
        raise HTTPException(404, "alias not found")
    tag_index: dict[str, set[str]] = {}
    all_tags = sorted({t for (t,) in (await session.execute(
        select(equipment_t.c["Equipment_Tag_No"]).distinct()
        .where(equipment_t.c["Site_ID"] == row["Site_ID"]))).all()})
    for t in all_tags:
        tag_index.setdefault(alias_norm(t), set()).add(t)
    return {"alias": dict(row),
            "candidates": match_alias(row["alias_norm"], tag_index),
            "all_tags": all_tags}


class AliasResolve(BaseModel):
    Equipment_Tag_No: Optional[str] = None   # None + status='ignored' is valid
    status: str = Field(default="mapped", pattern="^(mapped|ignored|unresolved)$")
    # Retro-apply to the rows this alias is already holding. Default TRUE: the
    # whole reason an operator opens this screen is the held rows.
    apply_to_logged: bool = True


@router.patch("/aliases/{alias_id}", summary="Resolve one tank alias")
async def resolve_alias(alias_id: int, body: AliasResolve,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(alias_t).where(alias_t.c["id"] == alias_id))).mappings().first()
    scope = site_scope(user)
    if row is None or (scope is not None and row["Site_ID"] != scope):
        raise HTTPException(404, "alias not found")
    tag = (body.Equipment_Tag_No or "").strip()
    if body.status == "mapped" and not tag:
        raise HTTPException(422, "mapping an alias needs an Equipment_Tag_No")
    if tag:
        known = (await session.execute(
            select(func.count()).select_from(equipment_t)
            .where(equipment_t.c["Site_ID"] == row["Site_ID"],
                   equipment_t.c["Equipment_Tag_No"] == tag))).scalar_one()
        if not known:
            raise HTTPException(422, f"no equipment {tag!r} at {row['Site_ID']}")

    await session.execute(update(alias_t).where(alias_t.c["id"] == alias_id)
                          .values(Equipment_Tag_No=(tag or None),
                                  status=body.status,
                                  resolved_by=user["username"],
                                  resolved_at=func.now()))

    # Retro-apply. The held rows carry the raw alias in `notes` ("Tank No. X ·
    # …"), so they are found by that rather than by a join the log does not
    # have. Only rows still `unassigned` are touched — an operator's later
    # per-row assignment is never overwritten by a bulk alias decision.
    touched = 0
    if body.apply_to_logged and body.status == "mapped" and tag:
        res = await session.execute(
            update(log_t)
            .where(log_t.c["Site_ID"] == row["Site_ID"],
                   log_t.c["status"] == "unassigned",
                   log_t.c["notes"].like(f"Tank No. {row['alias_raw']} ·%"))
            .values(Equipment_Tag_No=tag))
        touched = res.rowcount or 0

    await write_audit(session, user["username"], "SME_RESOLVE_TANK_ALIAS",
                      "sme_tank_alias",
                      f"{row['alias_raw']!r} → {tag or '(ignored)'} "
                      f"[{body.status}] · {touched} logged row(s) tagged")
    await session.commit()
    return {"updated": True, "rows_tagged": touched}


# ─── actual consumption ───────────────────────────────────────────────────────
@router.get("/consumption", summary="Logged Surface-Shield draw (assignment queue)")
async def list_consumption(site_id: Optional[str] = None,
                           status: Optional[str] = None,
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(log_t)
    if site is not None:
        stmt = stmt.where(log_t.c["Site_ID"] == site)
    if status:
        stmt = stmt.where(log_t.c["status"] == status)
    items = _rows(await session.execute(
        stmt.order_by(log_t.c["entry_date"].desc(), log_t.c["id"].desc())))
    # `sme_consumption_log` stores the code only. An operator assigning a draw
    # to equipment is reading down a column of GI-6000012s, so the name goes
    # beside it rather than in a tooltip.
    names = await _material_names(session, {i["Material_Code"] for i in items})
    for i in items:
        i["Material_Name"] = names.get(i["Material_Code"])
    return {"items": items,
            "unassigned": sum(1 for i in items if i["status"] == "unassigned")}


class AssignConsumption(BaseModel):
    Equipment_Tag_No: str = Field(min_length=1, max_length=120)
    Lining_System_Code: str = Field(min_length=1, max_length=40)
    # The area this draw actually covered. The workbook never states it — it
    # records a quantity issued — so a human types it here, and it is what
    # makes Expected_Qty (and therefore the variance) meaningful.
    SQM_Completed: float = Field(default=0, ge=0)
    notes: Optional[str] = None


@router.patch("/consumption/{log_id}",
              summary="Assign a logged draw to equipment + record the SQM done")
async def assign_consumption(log_id: int, body: AssignConsumption,
                             user: dict = Depends(require_roles("hod")),
                             session: AsyncSession = Depends(get_session)):
    """Turn an unassigned row into a real, comparable observation.

    `Expected_Qty = For_1_SQM × SQM_Completed` is computed HERE, from the
    recipe, at assignment time — which is the first moment both halves are
    known. Before that the row deliberately carries `Expected_Qty = 0` rather
    than a variance measured against a guessed system code.
    """
    row = (await session.execute(
        select(log_t).where(log_t.c["id"] == log_id))).mappings().first()
    scope = site_scope(user)
    if row is None or (scope is not None and row["Site_ID"] != scope):
        raise HTTPException(404, "consumption row not found")
    tag = body.Equipment_Tag_No.strip()
    code = body.Lining_System_Code.strip()

    pair = (await session.execute(
        select(func.count()).select_from(equipment_t)
        .where(equipment_t.c["Site_ID"] == row["Site_ID"],
               equipment_t.c["Equipment_Tag_No"] == tag,
               equipment_t.c["Lining_System_Code"] == code))).scalar_one()
    if not pair:
        raise HTTPException(422, f"{tag} does not carry system code {code} "
                                 f"at {row['Site_ID']}")

    per = (await session.execute(
        select(func.coalesce(func.sum(recipe_t.c["For_1_SQM"]), 0.0))
        .where(func.trim(recipe_t.c["Lining_System_Code"]) == code,
               recipe_t.c["Material_Code"] == row["Material_Code"]))).scalar_one()
    sqm = float(body.SQM_Completed or 0)
    expected = round(float(per or 0) * sqm, 4)
    actual = float(row["Actual_Qty"] or 0)
    # Variance is undefined against a zero expectation — leave it NULL rather
    # than publishing a divide-by-zero dressed up as a percentage.
    variance = round(100.0 * (actual - expected) / expected, 4) if expected else None

    await session.execute(update(log_t).where(log_t.c["id"] == log_id).values(
        Equipment_Tag_No=tag, Lining_System_Code=code, SQM_Completed=sqm,
        Expected_Qty=expected, Variance_Pct=variance, status="committed",
        committed_at=func.now(),
        notes=(body.notes if body.notes is not None else row["notes"])))
    await write_audit(session, user["username"], "SME_ASSIGN_ACTUAL",
                      "sme_consumption_log",
                      f"id={log_id} → {tag}/{code} sqm={sqm:g} "
                      f"expected={expected:g} actual={actual:g}")
    await session.commit()
    return {"updated": True, "Expected_Qty": expected, "Actual_Qty": actual,
            "Variance_Pct": variance}


@router.delete("/consumption/{log_id}", summary="Remove a logged draw")
async def delete_consumption(log_id: int,
                             user: dict = Depends(require_roles("hod")),
                             session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(log_t.c["Site_ID"]).where(log_t.c["id"] == log_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "consumption row not found")
    await session.execute(delete(log_t).where(log_t.c["id"] == log_id))
    await write_audit(session, user["username"], "SME_DELETE_ACTUAL",
                      "sme_consumption_log", f"id={log_id}")
    await session.commit()
    return {"deleted": True}


@router.get("/summary",
            summary="Actual physical draw per material — the SIDE NOTE, never netted")
async def actuals_summary(site_id: Optional[str] = None,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    """Per-material totals for the "Actual Physical Balance" side note.

    ⚠️ Reads `sme_consumption_log` ONLY. It does not touch — and must never
    touch — `sme_inventory_seed`: the estimator's availability is not reduced
    by this, by ruling (rule 1a). The UI places these figures BESIDE the
    estimator's, labelled as observed physical draw, and does no arithmetic
    between the two.
    """
    site = resolve_site_param(user, site_id)
    stmt = select(
        log_t.c["Material_Code"],
        func.sum(log_t.c["Actual_Qty"]).label("Actual_Drawn_Qty"),
        func.sum(log_t.c["Expected_Qty"]).label("Expected_Qty"),
        func.sum(log_t.c["SQM_Completed"]).label("SQM_Completed"),
        func.count().label("Rows"),
        func.count().filter(log_t.c["status"] == "unassigned").label("Unassigned_Rows"),
    ).group_by(log_t.c["Material_Code"]).order_by(log_t.c["Material_Code"])
    if site is not None:
        stmt = stmt.where(log_t.c["Site_ID"] == site)
    items = _rows(await session.execute(stmt))
    names = await _material_names(session, {i["Material_Code"] for i in items})
    for i in items:
        i["Material_Name"] = names.get(i["Material_Code"])
    return {"items": items,
            "note": "Observed physical draw. The SME estimator's availability "
                    "is deliberately NOT reduced by these figures (rule 1a) — "
                    "they sit beside the plan, never inside it."}
