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
  * recipes    — global; unique (Lining_System_Code, Material_Code).
  * materials  — the SME-owned sme_inventory_seed ONLY (Canon Rule 2: ERP
                 `inventory` is never read or written here). Create is an
                 upsert on Material_Code (legacy INSERT OR REPLACE); update
                 never renames the PK.
  * progress   — upsert with None-preserving semantics (legacy
                 upsert_sme_sqm_progress); standalone scopes without an
                 equipment-master row are allowed (legacy SQM editor).
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

from fastapi import APIRouter, Depends, HTTPException
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
seed_t = _MD.tables["sme_inventory_seed"]
progress_t = _MD.tables["sme_sqm_progress"]
settings_t = _MD.tables["system_settings"]

_SETTING_KINDS = {"locations": "sme_location", "types": "sme_equipment_type"}


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
    mat = body.Material_Code.strip()
    sap = (body.SAP_Code or "").strip() or None
    # Identity is (code, material, SAP) — PU component lines share a material
    # and differ only by variant SAP (1041 / 1041-1 / …).
    dup = (await session.execute(
        select(func.count()).select_from(recipe_t)
        .where(recipe_t.c["Lining_System_Code"] == code,
               recipe_t.c["Material_Code"] == mat,
               recipe_t.c["SAP_Code"].is_(None) if sap is None
               else recipe_t.c["SAP_Code"] == sap))).scalar_one()
    if dup:
        raise HTTPException(409, f"system {code} already has a line for {mat}"
                                 + (f" (SAP {sap})" if sap else ""))
    values = {k: v for k, v in body.model_dump().items() if v is not None}
    values["Lining_System_Code"], values["Material_Code"] = code, mat
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
        raise HTTPException(409, "that (system code, material, SAP) line "
                                 "already exists")
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
    # Material_Code is the PK and deliberately not patchable (legacy dropped it
    # from SET so a cell-edit could never silently rename the baseline row).
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
        text(SQL_SME_MATERIALS + ' ORDER BY s."Material_Code"')))}


@router.post("/materials", status_code=201,
             summary="Create or re-baseline a material seed (upsert on Material_Code)")
async def upsert_material(body: MaterialUpsert,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    values = body.model_dump()
    values["Material_Code"] = body.Material_Code.strip()
    stmt = pg_insert(seed_t).values(**values, updated_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=["Material_Code"],
        set_={**{k: stmt.excluded[k] for k in values if k != "Material_Code"},
              "updated_at": func.now()})
    await session.execute(stmt)
    await write_audit(session, user["username"], "SME_UPSERT_MATERIAL",
                      "sme_inventory_seed", values["Material_Code"])
    await session.commit()
    return {"created": True, "Material_Code": values["Material_Code"]}


@router.patch("/materials/{material_code}", summary="Edit a material seed row")
async def update_material(material_code: str, body: MaterialPatch,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to update")
    res = await session.execute(
        update(seed_t).where(seed_t.c["Material_Code"] == material_code.strip())
        .values(**changes, updated_at=func.now()))
    if res.rowcount != 1:
        raise HTTPException(404, "material seed row not found")
    await write_audit(session, user["username"], "SME_UPDATE_MATERIAL",
                      "sme_inventory_seed", f"{material_code} fields={sorted(changes)}")
    await session.commit()
    return {"updated": True}


@router.delete("/materials/{material_code}",
               summary="Delete a material seed row (ERP ledger untouched)")
async def delete_material(material_code: str,
                          user: dict = Depends(require_roles("hod")),
                          session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        delete(seed_t).where(seed_t.c["Material_Code"] == material_code.strip()))
    if res.rowcount != 1:
        raise HTTPException(404, "material seed row not found")
    await write_audit(session, user["username"], "SME_DELETE_MATERIAL",
                      "sme_inventory_seed", material_code.strip())
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
