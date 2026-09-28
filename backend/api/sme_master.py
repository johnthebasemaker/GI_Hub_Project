"""
backend/api/sme_master.py — SME Phase S6: Master Data CRUD (cutover day).

The sme_* write freeze existed to prevent dual-write drift while the legacy
Streamlit app was still the system of record. The cutover migration has been
executed, making the new stack the sole writer — so the legacy Tab 8 Master
Data CRUD (database.py §R20.5 helpers, the semantic contract) is ported here:

  * equipment  — site-scoped. Create inserts ONE row per (tag, code) and
                 upserts the matching sme_sqm_progress row with
                 Original_SQM = Surface_Area_SQM, preserving Done_SQM (the
                 legacy Smart Entry pairing). Delete cascades that progress
                 row. Update deliberately does NOT touch progress (legacy
                 cell-edit semantics).
  * recipes    — global; unique (Lining_System_Code,
                 Execution_Sub_Activity_Code, Material_Code, SAP_Code).
  * materials  — the SME-owned sme_inventory_seed ONLY (Canon Rule 2: ERP
                 `inventory` is never read or written here). Create is an
                 upsert on Material_Code (legacy INSERT OR REPLACE); update
                 never renames the PK.
  * progress   — upsert with None-preserving semantics (legacy
                 upsert_sme_sqm_progress); standalone scopes without an
                 equipment-master row are allowed (legacy SQM editor).
  * manpower-norms — the productivity benchmarks from
                 Manpower_Hour_Details.xlsx, global. Identity is five parts
                 (Type, Lining_System_Code, Execution_Sub_Activity_Code,
                 Activity, Variant_Key) — see models.SmeManpowerNorm for why
                 each one is load-bearing. Each norm carries a CREW: a
                 role → headcount map in its own table, replaced wholesale on
                 write because a role removed from a crew has to disappear and
                 an upsert cannot express an absence.
  * roles      — the role/designation master behind every crew figure and the
                 roster's dropdown. Workbook-sourced rows are the vocabulary
                 the benchmarks are expressed in and cannot be renamed or
                 deleted; an HOD's own additions can.
  * settings   — location / type dropdown values in system_settings
                 (categories sme_location / sme_equipment_type), site-scoped;
                 deletion refuses values still used by equipment at the site.

Access is the legacy PAGE_ACCESS exact-lock {hod, admin}; HOD writes are
pinned to their own site. Unlike legacy (which did not audit Master Data),
every write lands a system_audit_log row — new-stack convention.

The allocation engines are untouched: CRUD changes the model's INPUTS, never
its math, so the TS/Python golden parity (suite G / parity:sme) is unaffected.
Ordering stays explicit-PK (Canon Rule 1).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_roles, resolve_site_param, site_scope
from .db import get_session
from .services.ledger import _MD, write_audit
from .sme import SQL_SME_MATERIALS, _rows

router = APIRouter(prefix="/sme/master", tags=["SME master data (S6)"],
                   dependencies=[Depends(require_roles("hod"))])

equipment_t = _MD.tables["sme_equipment"]
recipe_t = _MD.tables["sme_recipe"]
norm_t = _MD.tables["sme_manpower_norm"]
norm_role_t = _MD.tables["sme_manpower_norm_role"]
mh_roles_t = _MD.tables["mh_roles"]
seed_t = _MD.tables["sme_inventory_seed"]
progress_t = _MD.tables["sme_sqm_progress"]
settings_t = _MD.tables["system_settings"]

_SETTING_KINDS = {"locations": "sme_location", "types": "sme_equipment_type"}


def _sap(v: str | None) -> str:
    """Whitespace-stripped SAP code — the ERP writes "1043 - 2" for "1043-2".
    Mirrors sme_engine.sap_norm so a hand-entered row lands in the same
    component pool the workbook loader would have created."""
    return "".join((v or "").split())


def _write_site(user: dict, site_id: Optional[str]) -> str:
    """Site pin for WRITES: scoped users always their own site (naming another
    → 403, visible boundary); unscoped users must say which site (422)."""
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


async def _upsert_progress(session: AsyncSession, site: str, tag: str, code: str,
                           original_sqm: Optional[float],
                           done_sqm: Optional[float]) -> None:
    """Legacy upsert_sme_sqm_progress: None kwargs preserve existing values
    (Done_SQM survives equipment re-entry / recipe reloads)."""
    existing = (await session.execute(
        select(progress_t.c["Original_SQM"], progress_t.c["Done_SQM"])
        .where(progress_t.c["Site_ID"] == site,
               progress_t.c["Equipment_Tag_No"] == tag,
               progress_t.c["Lining_System_Code"] == code))).first()
    new_orig = float(original_sqm) if original_sqm is not None else \
        (float(existing[0]) if existing else 0.0)
    new_done = float(done_sqm) if done_sqm is not None else \
        (float(existing[1]) if existing else 0.0)
    stmt = pg_insert(progress_t).values(
        Site_ID=site, Equipment_Tag_No=tag, Lining_System_Code=code,
        Original_SQM=new_orig, Done_SQM=new_done, updated_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=["Site_ID", "Equipment_Tag_No", "Lining_System_Code"],
        set_={"Original_SQM": stmt.excluded["Original_SQM"],
              "Done_SQM": stmt.excluded["Done_SQM"], "updated_at": func.now()})
    await session.execute(stmt)


# ─── Equipment ────────────────────────────────────────────────────────────────
_EQ_TEXT_FIELDS = ("Name", "Location", "Type", "Substrate",
                   "Lining_System_Short_Name", "Lining_Type", "Lining_System",
                   "Material_Spec", "Design", "Lining_Area_Location", "Sl_No",
                   "Project", "WBS_No", "IO_No", "Sub_Location", "Drawing_No",
                   "Dia_L", "Ht_W", "Remaraks")  # legacy Excel typo preserved


class EquipmentCreate(BaseModel):
    Equipment_Tag_No: str = Field(min_length=1, max_length=120)
    Lining_System_Code: str = Field(min_length=1, max_length=40)
    Surface_Area_SQM: float = Field(gt=0)  # legacy Smart Entry: SQM must be > 0
    Equipment_Total_SQM: Optional[float] = None
    Name: Optional[str] = None
    Location: Optional[str] = None
    Type: Optional[str] = None
    Substrate: Optional[str] = None
    Lining_System_Short_Name: Optional[str] = None
    Lining_Type: Optional[str] = None
    Lining_System: Optional[str] = None
    Material_Spec: Optional[str] = None
    Design: Optional[str] = None
    Lining_Area_Location: Optional[str] = None
    Sl_No: Optional[str] = None
    Project: Optional[str] = None
    WBS_No: Optional[str] = None
    IO_No: Optional[str] = None
    Sub_Location: Optional[str] = None
    Drawing_No: Optional[str] = None
    Dia_L: Optional[str] = None
    Ht_W: Optional[str] = None
    Remaraks: Optional[str] = None
    site_id: Optional[str] = None


class EquipmentPatch(BaseModel):
    Equipment_Tag_No: Optional[str] = Field(default=None, min_length=1, max_length=120)
    Lining_System_Code: Optional[str] = Field(default=None, min_length=1, max_length=40)
    Surface_Area_SQM: Optional[float] = Field(default=None, gt=0)
    Equipment_Total_SQM: Optional[float] = None
    Name: Optional[str] = None
    Location: Optional[str] = None
    Type: Optional[str] = None
    Substrate: Optional[str] = None
    Lining_System_Short_Name: Optional[str] = None
    Lining_Type: Optional[str] = None
    Lining_System: Optional[str] = None
    Material_Spec: Optional[str] = None
    Design: Optional[str] = None
    Lining_Area_Location: Optional[str] = None
    Sl_No: Optional[str] = None
    Project: Optional[str] = None
    WBS_No: Optional[str] = None
    IO_No: Optional[str] = None
    Sub_Location: Optional[str] = None
    Drawing_No: Optional[str] = None
    Dia_L: Optional[str] = None
    Ht_W: Optional[str] = None
    Remaraks: Optional[str] = None
    site_id: Optional[str] = None


@router.get("/equipment", summary="Full equipment master rows (grid source)")
async def list_equipment(site_id: Optional[str] = None,
                         user: dict = Depends(require_roles("hod")),
                         session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(equipment_t)
    if site is not None:
        stmt = stmt.where(equipment_t.c["Site_ID"] == site)
    return {"items": _rows(await session.execute(stmt.order_by(equipment_t.c["id"])))}


@router.post("/equipment", status_code=201,
             summary="Add one equipment (tag × code) row + seed its SQM progress")
async def create_equipment(body: EquipmentCreate,
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    site = _write_site(user, body.site_id)
    tag = body.Equipment_Tag_No.strip()
    code = body.Lining_System_Code.strip()
    dup = (await session.execute(
        select(func.count()).select_from(equipment_t)
        .where(equipment_t.c["Site_ID"] == site,
               equipment_t.c["Equipment_Tag_No"] == tag,
               equipment_t.c["Lining_System_Code"] == code))).scalar_one()
    if dup:
        raise HTTPException(409, f"{tag} already carries system code {code} at {site}")
    values = {"Site_ID": site, "Equipment_Tag_No": tag,
              "Lining_System_Code": code,
              "Surface_Area_SQM": body.Surface_Area_SQM,
              "Equipment_Total_SQM": body.Equipment_Total_SQM}
    for f in _EQ_TEXT_FIELDS:
        v = getattr(body, f)
        if v is not None:
            values[f] = v
    new_id = (await session.execute(
        insert(equipment_t).values(**values).returning(equipment_t.c["id"]))).scalar_one()
    # Legacy Smart Entry pairing: seed Original_SQM, preserve any Done_SQM.
    await _upsert_progress(session, site, tag, code,
                           original_sqm=body.Surface_Area_SQM, done_sqm=None)
    await write_audit(session, user["username"], "SME_CREATE_EQUIPMENT",
                      "sme_equipment", f"{site}/{tag}/{code} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id}


@router.patch("/equipment/{eq_id}", summary="Edit an equipment row (no progress cascade)")
async def update_equipment(eq_id: int, body: EquipmentPatch,
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    changes.pop("site_id", None)
    if not changes:
        raise HTTPException(422, "no fields to update")
    stmt = update(equipment_t).where(equipment_t.c["id"] == eq_id)
    scope = site_scope(user)
    if scope is not None:
        stmt = stmt.where(equipment_t.c["Site_ID"] == scope)
    try:
        res = await session.execute(stmt.values(**changes))
        if res.rowcount != 1:
            raise HTTPException(404, "equipment row not found")
        await write_audit(session, user["username"], "SME_UPDATE_EQUIPMENT",
                          "sme_equipment", f"id={eq_id} fields={sorted(changes)}")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "that (tag, system code) already exists at the site")
    return {"updated": True}


class SqmOverride(BaseModel):
    # None CLEARS the override and hands the row back to the workbook.
    Surface_Area_SQM: Optional[float] = Field(default=None, gt=0)


@router.patch("/equipment/{eq_id}/sqm",
              summary="Set (or clear) the operator SQM override — THE APP WINS")
async def set_equipment_sqm(eq_id: int, body: SqmOverride,
                            user: dict = Depends(require_roles("hod")),
                            session: AsyncSession = Depends(get_session)):
    """Correct a tank's surface area from the UI and have it STICK.

    `Surface_Area_SQM` drives demand, so a plain edit that the next workbook
    sync reverted would surface a week later as a wrong buy list with nothing
    to point at. Writing `SQM_Override` alongside it is what survives both the
    ordinary upsert and `--sme-reseed` (bulk_import.restore_sqm_overrides), and
    what lets the sync REPORT the divergence instead of resolving it silently.

    Passing `Surface_Area_SQM: null` clears the override; the row then follows
    `Equipment.xlsx` again from the next sync.
    """
    row = (await session.execute(
        select(equipment_t.c["Site_ID"], equipment_t.c["Equipment_Tag_No"],
               equipment_t.c["Lining_System_Code"],
               equipment_t.c["Surface_Area_SQM"])
        .where(equipment_t.c["id"] == eq_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "equipment row not found")
    site, tag, code, was = row

    if body.Surface_Area_SQM is None:
        await session.execute(update(equipment_t)
                              .where(equipment_t.c["id"] == eq_id)
                              .values(SQM_Override=None, SQM_Override_By=None,
                                      SQM_Override_At=None))
        await write_audit(session, user["username"], "SME_CLEAR_SQM_OVERRIDE",
                          "sme_equipment",
                          f"{site}/{tag}/{code} id={eq_id} — workbook resumes ownership")
        await session.commit()
        return {"updated": True, "override": None,
                "Surface_Area_SQM": float(was or 0)}

    sqm = float(body.Surface_Area_SQM)
    await session.execute(update(equipment_t)
                          .where(equipment_t.c["id"] == eq_id)
                          .values(Surface_Area_SQM=sqm, SQM_Override=sqm,
                                  SQM_Override_By=user["username"],
                                  SQM_Override_At=func.now()))
    # Keep the progress row's Original_SQM in step — Done_SQM is preserved by
    # `_upsert_progress`'s None-means-keep contract.
    await _upsert_progress(session, site, tag, code,
                           original_sqm=sqm, done_sqm=None)
    await write_audit(session, user["username"], "SME_SET_SQM_OVERRIDE",
                      "sme_equipment",
                      f"{site}/{tag}/{code} id={eq_id} {float(was or 0):g} → {sqm:g}")
    await session.commit()
    return {"updated": True, "override": sqm, "Surface_Area_SQM": sqm,
            "previous": float(was or 0)}


@router.delete("/equipment/{eq_id}",
               summary="Delete an equipment row (cascades its SQM-progress entry)")
async def delete_equipment(eq_id: int,
                           user: dict = Depends(require_roles("hod")),
                           session: AsyncSession = Depends(get_session)):
    row = (await session.execute(
        select(equipment_t.c["Site_ID"], equipment_t.c["Equipment_Tag_No"],
               equipment_t.c["Lining_System_Code"])
        .where(equipment_t.c["id"] == eq_id))).first()
    scope = site_scope(user)
    if row is None or (scope is not None and row[0] != scope):
        raise HTTPException(404, "equipment row not found")
    site, tag, code = row
    await session.execute(delete(progress_t).where(
        progress_t.c["Site_ID"] == site,
        progress_t.c["Equipment_Tag_No"] == tag,
        progress_t.c["Lining_System_Code"] == code))
    await session.execute(delete(equipment_t).where(equipment_t.c["id"] == eq_id))
    await write_audit(session, user["username"], "SME_DELETE_EQUIPMENT",
                      "sme_equipment", f"{site}/{tag}/{code} id={eq_id}")
    await session.commit()
    return {"deleted": True}


# ─── Recipes (global — Canon Rule 3: not site-scoped by design) ──────────────
class RecipeCreate(BaseModel):
    Lining_System_Code: str = Field(min_length=1, max_length=40)
    # Part of the identity since f1d3b7a24c60 — one system/material/SAP can
    # legitimately carry a different quantity per execution sub-activity
    # (LSC2 Resin A: 0.2700 as ESC21 primer, 1.4674 as ESC22 screed).
    Execution_Sub_Activity_Code: str = Field(default="", max_length=40)
    Material_Code: str = Field(min_length=1, max_length=80)
    SAP_Code: Optional[str] = Field(default=None, max_length=40)
    For_1_SQM: float = Field(default=0, ge=0)
    Lining_System_Name: Optional[str] = None
    Material_Name: Optional[str] = None
    Material_Description: Optional[str] = None
    UOM: Optional[str] = None
    Nature: Optional[str] = None
    Substrate: Optional[str] = None
    System_Keys: Optional[str] = None
    Lining_Thickness: Optional[str] = None
    Lining_System: Optional[str] = None
    Lining_Type: Optional[str] = None
    Package_Size: Optional[str] = None
    Sl_No: Optional[str] = None


class RecipePatch(BaseModel):
    Lining_System_Code: Optional[str] = Field(default=None, min_length=1, max_length=40)
    Execution_Sub_Activity_Code: Optional[str] = Field(default=None, max_length=40)
    Material_Code: Optional[str] = Field(default=None, min_length=1, max_length=80)
    SAP_Code: Optional[str] = Field(default=None, max_length=40)
    For_1_SQM: Optional[float] = Field(default=None, ge=0)
    Lining_System_Name: Optional[str] = None
    Material_Name: Optional[str] = None
    Material_Description: Optional[str] = None
    UOM: Optional[str] = None
    Nature: Optional[str] = None
    Substrate: Optional[str] = None
    System_Keys: Optional[str] = None
    Lining_Thickness: Optional[str] = None
    Lining_System: Optional[str] = None
    Lining_Type: Optional[str] = None
    Package_Size: Optional[str] = None
    Sl_No: Optional[str] = None


@router.get("/recipes", summary="Full recipe/BOM rows (grid source)")
async def list_recipes(session: AsyncSession = Depends(get_session)):
    return {"items": _rows(await session.execute(
        select(recipe_t).order_by(recipe_t.c["id"])))}


@router.post("/recipes", status_code=201, summary="Add a recipe line")
async def create_recipe(body: RecipeCreate,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    code = body.Lining_System_Code.strip()
    esc = (body.Execution_Sub_Activity_Code or "").strip()
    mat = body.Material_Code.strip()
    sap = (body.SAP_Code or "").strip() or None
    # Identity is (code, SUB-ACTIVITY, material, SAP). PU component lines share
    # a material and differ only by variant SAP (1041 / 1041-1 / …); coat lines
    # share all three of those and differ only by sub-activity. Probing without
    # the ESC refuses the second coat as a duplicate of the first.
    dup = (await session.execute(
        select(func.count()).select_from(recipe_t)
        .where(recipe_t.c["Lining_System_Code"] == code,
               recipe_t.c["Execution_Sub_Activity_Code"] == esc,
               recipe_t.c["Material_Code"] == mat,
               recipe_t.c["SAP_Code"].is_(None) if sap is None
               else recipe_t.c["SAP_Code"] == sap))).scalar_one()
    if dup:
        raise HTTPException(409, f"system {code} already has a line for {mat}"
                                 + (f" (SAP {sap})" if sap else "")
                                 + (f" under {esc}" if esc else ""))
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    values["Lining_System_Code"], values["Material_Code"] = code, mat
    values["Execution_Sub_Activity_Code"] = esc
    new_id = (await session.execute(
        insert(recipe_t).values(**values).returning(recipe_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "SME_CREATE_RECIPE",
                      "sme_recipe", f"{code}/{mat} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id}


@router.patch("/recipes/{rec_id}", summary="Edit a recipe line")
async def update_recipe(rec_id: int, body: RecipePatch,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    try:
        res = await session.execute(
            update(recipe_t).where(recipe_t.c["id"] == rec_id).values(**changes))
        if res.rowcount != 1:
            raise HTTPException(404, "recipe row not found")
        await write_audit(session, user["username"], "SME_UPDATE_RECIPE",
                          "sme_recipe", f"id={rec_id} fields={sorted(changes)}")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "that (system code, sub-activity, material, "
                                 "SAP) line already exists")
    return {"updated": True}


@router.delete("/recipes/{rec_id}", summary="Delete a recipe line")
async def delete_recipe(rec_id: int,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    res = await session.execute(delete(recipe_t).where(recipe_t.c["id"] == rec_id))
    if res.rowcount != 1:
        raise HTTPException(404, "recipe row not found")
    await write_audit(session, user["username"], "SME_DELETE_RECIPE",
                      "sme_recipe", f"id={rec_id}")
    await session.commit()
    return {"deleted": True}


# ─── Materials — sme_inventory_seed ONLY (Canon Rule 2) ──────────────────────
class MaterialUpsert(BaseModel):
    Material_Code: str = Field(min_length=1, max_length=80)
    # 2026-07-30 COMPONENT IDENTITY: (Material_Code, SAP_Code) is the row key.
    # A multi-part system is four rows sharing one Material_Code, so the SAP is
    # what says WHICH drum this is. Blank is legal for a single-component
    # material that predates the workbook's SAP column.
    SAP_Code: str = Field(default="", max_length=80)
    Material_Name: Optional[str] = None
    Item: Optional[str] = None
    Vendor: Optional[str] = None
    Purchasing_Document: Optional[str] = None
    Document_Date: Optional[str] = None
    Nature: Optional[str] = None
    UOM: Optional[str] = None
    Initial_Available_Qty: float = Field(default=0, ge=0)
    Initial_Ordered_Qty: float = Field(default=0, ge=0)


class MaterialPatch(BaseModel):
    # Material_Code and SAP_Code are the PK and deliberately not patchable
    # (legacy dropped Material_Code from SET so a cell-edit could never silently
    # rename the baseline row; the same reasoning covers the SAP).
    Material_Name: Optional[str] = None
    Item: Optional[str] = None
    Vendor: Optional[str] = None
    Purchasing_Document: Optional[str] = None
    Document_Date: Optional[str] = None
    Nature: Optional[str] = None
    UOM: Optional[str] = None
    Initial_Available_Qty: Optional[float] = Field(default=None, ge=0)
    Initial_Ordered_Qty: Optional[float] = Field(default=None, ge=0)


@router.get("/materials", summary="Seed rows + derived availability (grid source)")
async def list_materials(session: AsyncSession = Depends(get_session)):
    return {"items": _rows(await session.execute(
        text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code", s."SAP_Code"')))}


@router.post("/materials", status_code=201,
             summary="Create or re-baseline a material seed "
                     "(upsert on Material_Code + SAP_Code)")
async def upsert_material(body: MaterialUpsert,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    values = body.model_dump()
    values["Material_Code"] = body.Material_Code.strip()
    values["SAP_Code"] = _sap(body.SAP_Code)
    keys = ("Material_Code", "SAP_Code")
    stmt = pg_insert(seed_t).values(**values, updated_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=list(keys),
        set_={**{k: stmt.excluded[k] for k in values if k not in keys},
              "updated_at": func.now()})
    await session.execute(stmt)
    await write_audit(session, user["username"], "SME_UPSERT_MATERIAL",
                      "sme_inventory_seed",
                      f'{values["Material_Code"]}/{values["SAP_Code"] or "—"}')
    await session.commit()
    return {"created": True, "Material_Code": values["Material_Code"],
            "SAP_Code": values["SAP_Code"]}


@router.patch("/materials/{material_code}", summary="Edit a material seed row")
async def update_material(material_code: str, body: MaterialPatch,
                          sap_code: str = Query(
                              ..., description="Variant SAP identifying WHICH "
                              "component of this Material_Code to edit"),
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    # sap_code is REQUIRED: one Material_Code can be four physical components,
    # and a code-only WHERE would rewrite all four with one component's figures.
    res = await session.execute(
        update(seed_t).where(seed_t.c["Material_Code"] == material_code.strip(),
                             seed_t.c["SAP_Code"] == _sap(sap_code))
        .values(**changes, updated_at=func.now()))
    if res.rowcount != 1:
        raise HTTPException(404, "material seed row not found")
    await write_audit(session, user["username"], "SME_UPDATE_MATERIAL",
                      "sme_inventory_seed",
                      f"{material_code}/{_sap(sap_code) or '—'} "
                      f"fields={sorted(changes)}")
    await session.commit()
    return {"updated": True}


@router.delete("/materials/{material_code}",
               summary="Delete a material seed row (ERP ledger untouched)")
async def delete_material(material_code: str,
                          sap_code: str = Query(
                              ..., description="Variant SAP identifying WHICH "
                              "component of this Material_Code to delete"),
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        delete(seed_t).where(seed_t.c["Material_Code"] == material_code.strip(),
                             seed_t.c["SAP_Code"] == _sap(sap_code)))
    if res.rowcount != 1:
        raise HTTPException(404, "material seed row not found")
    await write_audit(session, user["username"], "SME_DELETE_MATERIAL",
                      "sme_inventory_seed",
                      f"{material_code.strip()}/{_sap(sap_code) or '—'}")
    await session.commit()
    return {"deleted": True}


# ─── SQM progress (upsert editor) ─────────────────────────────────────────────
class ProgressUpsert(BaseModel):
    Equipment_Tag_No: str = Field(min_length=1, max_length=120)
    Lining_System_Code: str = Field(min_length=1, max_length=40)
    Original_SQM: Optional[float] = Field(default=None, ge=0)
    Done_SQM: Optional[float] = Field(default=None, ge=0)
    site_id: Optional[str] = None


@router.get("/progress", summary="Full SQM-progress rows (grid source)")
async def list_progress(site_id: Optional[str] = None,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    site = resolve_site_param(user, site_id)
    stmt = select(progress_t)
    if site is not None:
        stmt = stmt.where(progress_t.c["Site_ID"] == site)
    stmt = stmt.order_by(progress_t.c["Site_ID"], progress_t.c["Equipment_Tag_No"],
                         progress_t.c["Lining_System_Code"])
    return {"items": _rows(await session.execute(stmt))}


@router.put("/progress", summary="Upsert one SQM-progress row "
            "(omitted fields keep their current values)")
async def put_progress(body: ProgressUpsert,
                       user: dict = Depends(require_roles("hod")),
                       session: AsyncSession = Depends(get_session)):
    if body.Original_SQM is None and body.Done_SQM is None:
        raise HTTPException(422, "provide Original_SQM and/or Done_SQM")
    site = _write_site(user, body.site_id)
    tag, code = body.Equipment_Tag_No.strip(), body.Lining_System_Code.strip()
    await _upsert_progress(session, site, tag, code,
                           original_sqm=body.Original_SQM, done_sqm=body.Done_SQM)
    await write_audit(session, user["username"], "SME_UPSERT_PROGRESS",
                      "sme_sqm_progress",
                      f"{site}/{tag}/{code} orig={body.Original_SQM} done={body.Done_SQM}")
    await session.commit()
    return {"upserted": True}


# ─── Location / Type dropdowns (system_settings) ──────────────────────────────
class SettingBody(BaseModel):
    value: str = Field(min_length=1, max_length=120)
    site_id: Optional[str] = None


def _setting_category(kind: str) -> str:
    cat = _SETTING_KINDS.get(kind)
    if cat is None:
        raise HTTPException(404, f"unknown setting kind {kind!r} "
                                 f"(use one of {sorted(_SETTING_KINDS)})")
    return cat


@router.get("/settings", summary="Location + type dropdown values for a site")
async def list_settings(site_id: Optional[str] = None,
                        user: dict = Depends(require_roles("hod")),
                        session: AsyncSession = Depends(get_session)):
    site = _write_site(user, site_id)  # same pin: the editor is per-site
    out = {}
    for kind, cat in _SETTING_KINDS.items():
        rows = (await session.execute(
            select(settings_t.c["value"]).where(
                settings_t.c["category"] == cat,
                func.coalesce(settings_t.c["Site_ID"], "") == site)
            .order_by(settings_t.c["id"]))).scalars().all()
        out[kind] = list(rows)
    return {"site_id": site, **out}


@router.post("/settings/{kind}", status_code=201,
             summary="Add a location/type dropdown value")
async def add_setting(kind: str, body: SettingBody,
                      user: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    cat = _setting_category(kind)
    site = _write_site(user, body.site_id)
    value = body.value.strip()
    dup = (await session.execute(
        select(func.count()).select_from(settings_t).where(
            settings_t.c["category"] == cat, settings_t.c["value"] == value,
            func.coalesce(settings_t.c["Site_ID"], "") == site))).scalar_one()
    if dup:
        raise HTTPException(409, f"{value!r} already exists")
    await session.execute(insert(settings_t).values(
        category=cat, value=value, Site_ID=site))
    await write_audit(session, user["username"], "SME_ADD_SETTING",
                      "system_settings", f"{cat}:{site}:{value}")
    await session.commit()
    return {"created": True}


@router.delete("/settings/{kind}", summary="Remove a location/type dropdown value "
               "(refused while equipment at the site still uses it)")
async def delete_setting(kind: str, value: str, site_id: Optional[str] = None,
                         user: dict = Depends(require_roles("hod")),
                         session: AsyncSession = Depends(get_session)):
    cat = _setting_category(kind)
    site = _write_site(user, site_id)
    value = value.strip()
    # Legacy guard: a location/type still referenced by equipment can't go.
    col = equipment_t.c["Location"] if kind == "locations" else equipment_t.c["Type"]
    in_use = (await session.execute(
        select(func.count()).select_from(equipment_t).where(
            equipment_t.c["Site_ID"] == site,
            func.trim(func.coalesce(col, "")) == value))).scalar_one()
    if in_use:
        raise HTTPException(409, f"{value!r} is used by {in_use} equipment row(s)")
    res = await session.execute(delete(settings_t).where(
        settings_t.c["category"] == cat, settings_t.c["value"] == value,
        func.coalesce(settings_t.c["Site_ID"], "") == site))
    if not res.rowcount:
        raise HTTPException(404, "value not found")
    await write_audit(session, user["username"], "SME_DELETE_SETTING",
                      "system_settings", f"{cat}:{site}:{value}")
    await session.commit()
    return {"deleted": True}


# ─── Manpower norms (global — the benchmark master) ──────────────────────────
class NormBase(BaseModel):
    Type: str = Field(min_length=1, max_length=10)
    Lining_System_Code: str = Field(min_length=1, max_length=40)
    Execution_Sub_Activity_Code: str = Field(min_length=1, max_length=40)
    Activity: str = Field(min_length=1, max_length=120)
    Variant_Key: str = Field(default="", max_length=40)
    Sub_Activity: Optional[str] = None
    System: Optional[str] = None
    Activity_Code: Optional[str] = None
    Crew_Size: float = Field(default=0, ge=0)
    Hours_Per_Shift: float = Field(default=0, ge=0)
    Manhours_Per_Shift: float = Field(default=0, ge=0)
    Standard_Productivity_Per_Shift: float = Field(default=0, ge=0)
    SQM_Per_Hour_Per_Person: float = Field(default=0, ge=0)
    Remarks: Optional[str] = None
    # role code → headcount. Absent means "leave the crew alone" on a PATCH;
    # an empty dict means "this norm has no crew", which is a real answer.
    Crew: Optional[dict[str, float]] = None


class NormPatch(BaseModel):
    Type: Optional[str] = Field(default=None, min_length=1, max_length=10)
    Lining_System_Code: Optional[str] = Field(default=None, min_length=1, max_length=40)
    Execution_Sub_Activity_Code: Optional[str] = Field(default=None, min_length=1,
                                                       max_length=40)
    Activity: Optional[str] = Field(default=None, min_length=1, max_length=120)
    Variant_Key: Optional[str] = Field(default=None, max_length=40)
    Sub_Activity: Optional[str] = None
    System: Optional[str] = None
    Activity_Code: Optional[str] = None
    Crew_Size: Optional[float] = Field(default=None, ge=0)
    Hours_Per_Shift: Optional[float] = Field(default=None, ge=0)
    Manhours_Per_Shift: Optional[float] = Field(default=None, ge=0)
    Standard_Productivity_Per_Shift: Optional[float] = Field(default=None, ge=0)
    SQM_Per_Hour_Per_Person: Optional[float] = Field(default=None, ge=0)
    Remarks: Optional[str] = None
    Crew: Optional[dict[str, float]] = None


async def _crew_map(session: AsyncSession, norm_ids: list[int]) -> dict:
    """{norm_id: {role_code: headcount}} for a batch — one query, not N."""
    if not norm_ids:
        return {}
    out: dict[int, dict] = {}
    rows = (await session.execute(
        select(norm_role_t.c["Norm_ID"], norm_role_t.c["Role_Code"],
               norm_role_t.c["Headcount"])
        .where(norm_role_t.c["Norm_ID"].in_(norm_ids)))).all()
    for nid, role, head in rows:
        out.setdefault(int(nid), {})[str(role)] = float(head or 0)
    return out


async def _known_roles(session: AsyncSession) -> dict:
    return {str(r.Role_Code): str(r.Name) for r in (await session.execute(
        select(mh_roles_t.c["Role_Code"], mh_roles_t.c["Name"])
        .where(mh_roles_t.c["status"] == "active"))).all()}


async def _write_crew(session: AsyncSession, norm_id: int, crew: dict,
                      known: dict) -> None:
    """Replace a norm's crew. Refuses a role the master does not know — a
    typo'd role code would otherwise become an invisible zero in every plan."""
    unknown = sorted(set(crew) - set(known))
    if unknown:
        raise HTTPException(422, f"unknown role code(s): {unknown}. Add them "
                                 f"under Roles first.")
    await session.execute(delete(norm_role_t)
                          .where(norm_role_t.c["Norm_ID"] == norm_id))
    for role_code, headcount in crew.items():
        if float(headcount or 0) <= 0:
            continue        # zero is how a role is REMOVED, not stored
        await session.execute(insert(norm_role_t).values(
            Norm_ID=norm_id, Role_Code=role_code, Headcount=float(headcount)))


@router.get("/manpower-norms", summary="Productivity benchmarks (grid source)")
async def list_manpower_norms(session: AsyncSession = Depends(get_session)):
    rows = _rows(await session.execute(select(norm_t).order_by(norm_t.c["id"])))
    crews = await _crew_map(session, [int(r["id"]) for r in rows])
    known = await _known_roles(session)
    # Which norms consume no Surface Shield — the manpower-ONLY activities a
    # supervisor opens without a store keeper. Computed, never stored: it is a
    # fact ABOUT the recipe table and would go stale the moment a recipe
    # line is added.
    from .services import prep as PR
    prep = await PR.prep_codes(session)     # Phase 15d: Garnet lines don't count
    material_keys = {(str(a), str(b)) for a, b in (await session.execute(
        select(recipe_t.c["Lining_System_Code"],
               recipe_t.c["Execution_Sub_Activity_Code"]).distinct())).all()
        if str(a or "").strip() not in prep}
    for r in rows:
        crew = crews.get(int(r["id"]), {})
        r["Crew"] = crew
        r["Crew_Text"] = " · ".join(
            f"{known.get(k, k)} {int(v) if float(v).is_integer() else v}"
            for k, v in sorted(crew.items()))
        r["Manpower_Only"] = (str(r.get("Lining_System_Code")),
                              str(r.get("Execution_Sub_Activity_Code"))
                              ) not in material_keys
    return {"items": rows, "roles": known}


@router.post("/manpower-norms", status_code=201, summary="Add a benchmark")
async def create_manpower_norm(body: NormBase,
                               user: dict = Depends(require_roles("hod")),
                               session: AsyncSession = Depends(get_session)):
    values = {k: v for k, v in body.model_dump().items()
              if v is not None and k != "Crew"}
    for f in ("Type", "Lining_System_Code", "Execution_Sub_Activity_Code",
              "Activity"):
        values[f] = str(values[f]).strip()
    values["Variant_Key"] = str(values.get("Variant_Key") or "").strip()
    dup = (await session.execute(
        select(func.count()).select_from(norm_t).where(
            norm_t.c["Type"] == values["Type"],
            norm_t.c["Lining_System_Code"] == values["Lining_System_Code"],
            norm_t.c["Execution_Sub_Activity_Code"]
            == values["Execution_Sub_Activity_Code"],
            norm_t.c["Activity"] == values["Activity"],
            norm_t.c["Variant_Key"] == values["Variant_Key"]))).scalar_one()
    if dup:
        raise HTTPException(409, "a benchmark with that Type / system / "
                                 "sub-activity / activity / variant already "
                                 "exists — give this one a Variant_Key")
    new_id = (await session.execute(
        insert(norm_t).values(**values).returning(norm_t.c["id"]))).scalar_one()
    if body.Crew is not None:
        await _write_crew(session, new_id, body.Crew, await _known_roles(session))
    await write_audit(session, user["username"], "SME_CREATE_MANPOWER_NORM",
                      "sme_manpower_norm",
                      f"{values['Type']}/{values['Lining_System_Code']}/"
                      f"{values['Execution_Sub_Activity_Code']} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id}


@router.patch("/manpower-norms/{norm_id}", summary="Edit a benchmark")
async def update_manpower_norm(norm_id: int, body: NormPatch,
                               user: dict = Depends(require_roles("hod")),
                               session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    crew = changes.pop("Crew", None)
    if not changes and crew is None:
        raise HTTPException(422, "no fields to update")
    try:
        if changes:
            res = await session.execute(update(norm_t)
                                        .where(norm_t.c["id"] == norm_id)
                                        .values(**changes))
            if res.rowcount != 1:
                raise HTTPException(404, "benchmark not found")
        if crew is not None:
            await _write_crew(session, norm_id, crew,
                              await _known_roles(session))
        await write_audit(session, user["username"], "SME_UPDATE_MANPOWER_NORM",
                          "sme_manpower_norm",
                          f"id={norm_id} fields={sorted(changes)}"
                          + (" +crew" if crew is not None else ""))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "that Type / system / sub-activity / activity "
                                 "/ variant combination already exists")
    return {"updated": True}


@router.delete("/manpower-norms/{norm_id}", summary="Delete a benchmark")
async def delete_manpower_norm(norm_id: int,
                               user: dict = Depends(require_roles("hod")),
                               session: AsyncSession = Depends(get_session)):
    # norm_role rows cascade on the FK; naming it here documents the intent.
    res = await session.execute(delete(norm_t).where(norm_t.c["id"] == norm_id))
    if res.rowcount != 1:
        raise HTTPException(404, "benchmark not found")
    await write_audit(session, user["username"], "SME_DELETE_MANPOWER_NORM",
                      "sme_manpower_norm", f"id={norm_id}")
    await session.commit()
    return {"deleted": True}


# ─── Roles (global — the crew + roster vocabulary) ───────────────────────────
class RoleCreate(BaseModel):
    Role_Code: str = Field(min_length=1, max_length=40)
    Name: str = Field(min_length=1, max_length=80)
    Sort_Order: int = 0


class RolePatch(BaseModel):
    Name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    Sort_Order: Optional[int] = None
    status: Optional[str] = None


@router.get("/roles", summary="Role / designation master (grid + dropdown)")
async def list_roles(session: AsyncSession = Depends(get_session)):
    rows = _rows(await session.execute(
        select(mh_roles_t).order_by(mh_roles_t.c["Sort_Order"],
                                    mh_roles_t.c["Role_Code"])))
    used = {str(r[0]) for r in (await session.execute(
        select(norm_role_t.c["Role_Code"]).distinct())).all()}
    for r in rows:
        r["In_Use"] = str(r.get("Role_Code")) in used
    return {"items": rows}


@router.post("/roles", status_code=201, summary="Add a role")
async def create_role(body: RoleCreate,
                      user: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    code = body.Role_Code.strip().upper().replace(" ", "_")
    dup = (await session.execute(select(func.count()).select_from(mh_roles_t)
           .where(mh_roles_t.c["Role_Code"] == code))).scalar_one()
    if dup:
        raise HTTPException(409, f"role {code} already exists")
    new_id = (await session.execute(insert(mh_roles_t).values(
        Role_Code=code, Name=body.Name.strip(), Source="custom",
        Sort_Order=int(body.Sort_Order or 0),
        created_by=user["username"]).returning(mh_roles_t.c["id"]))).scalar_one()
    await write_audit(session, user["username"], "SME_CREATE_ROLE", "mh_roles",
                      f"{code} id={new_id}")
    await session.commit()
    return {"created": True, "id": new_id, "Role_Code": code}


@router.patch("/roles/{role_id}", summary="Edit a role")
async def update_role(role_id: int, body: RolePatch,
                      user: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    row = (await session.execute(select(mh_roles_t)
           .where(mh_roles_t.c["id"] == role_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "role not found")
    # A workbook role IS the vocabulary the benchmarks are expressed in.
    # Renaming one silently re-labels every crew figure that cites it.
    if row["Source"] == "workbook" and "Name" in changes:
        raise HTTPException(409, f"{row['Role_Code']} comes from "
                                 f"Manpower_Hour_Details.xlsx — rename it in "
                                 f"the workbook and re-sync, or the benchmarks "
                                 f"and the roster stop agreeing")
    await session.execute(update(mh_roles_t)
                          .where(mh_roles_t.c["id"] == role_id).values(**changes))
    await write_audit(session, user["username"], "SME_UPDATE_ROLE", "mh_roles",
                      f"id={role_id} fields={sorted(changes)}")
    await session.commit()
    return {"updated": True}


@router.delete("/roles/{role_id}", summary="Delete a role")
async def delete_role(role_id: int,
                      user: dict = Depends(require_roles("hod")),
                      session: AsyncSession = Depends(get_session)):
    row = (await session.execute(select(mh_roles_t)
           .where(mh_roles_t.c["id"] == role_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "role not found")
    if row["Source"] == "workbook":
        raise HTTPException(409, f"{row['Role_Code']} comes from the workbook "
                                 f"and cannot be deleted here")
    used = (await session.execute(select(func.count()).select_from(norm_role_t)
            .where(norm_role_t.c["Role_Code"] == row["Role_Code"]))).scalar_one()
    if used:
        raise HTTPException(409, f"{row['Role_Code']} is used by {used} crew "
                                 f"line(s) — remove it from those benchmarks "
                                 f"first")
    await session.execute(delete(mh_roles_t).where(mh_roles_t.c["id"] == role_id))
    await write_audit(session, user["username"], "SME_DELETE_ROLE", "mh_roles",
                      f"{row['Role_Code']} id={role_id}")
    await session.commit()
    return {"deleted": True}


# ═══ Phase 15d — the Garnet baseline (Old vs New surface, KG per m²) ═════════
class PrepBaselineIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    state: str = Field(min_length=1, max_length=16)
    # None clears the saved figure (NEW then falls back to the workbook's).
    kg_per_sqm: Optional[float] = None


@router.get("/prep-baseline",
            summary="Garnet benchmark per prep code × Old/New surface (KG per m²)")
async def get_prep_baseline(user: dict = Depends(require_roles("hod")),
                            session: AsyncSession = Depends(get_session)):
    """The one-time setup page (editable later). Each row says what the
    benchmark IS and where it comes from: `set` (saved here), `workbook` (NEW
    only, the For_1_SQM figure — ruling Q15-9) or none (no benchmark: Old
    surface until somebody sets it)."""
    from .services import prep as PR
    rows = await PR.table(session)
    return {"items": rows, "complete": all(r["kg_per_sqm"] is not None for r in rows),
            "garnet_saps": sorted(await PR.garnet_saps(session))}


@router.put("/prep-baseline", summary="Set (or clear) one Garnet benchmark")
async def put_prep_baseline(body: PrepBaselineIn,
                            user: dict = Depends(require_roles("hod")),
                            session: AsyncSession = Depends(get_session)):
    """⚠️ A JOB KEEPS THE BENCHMARK IT WAS MEASURED AGAINST. The figure is
    snapshotted on each attribution when it is filed, so changing it here moves
    every FUTURE variance and no past one — the same rule as a recipe rate."""
    from .services import prep as PR
    async with session.begin():
        return await PR.set_baseline(session, code=body.code.strip(), state=body.state,
                                     kg_per_sqm=body.kg_per_sqm,
                                     username=user["username"])
