"""Phase 15d — surface preparation (Garnet) as explicit DATA, not a naming rule.

ESC1 and ESC2 are BLASTING: the sub-activity codes of the man-hour norms for
blasting concrete (ESC1) and steel (ESC2). The planner decides "surface prep,
not a lining system" by the rule *no recipe line names this code*
(`planner.lining_codes`). The 2026-09-27 workbooks added Garnet recipe lines
under exactly those codes — read by that rule, blasting would have become a
lining system: the man-hour plan, the supervisor's execution form and the
lining progress would all have moved.

So a code is SURFACE PREP when it has a row in `sme_prep_baseline`, and every
list of lining systems subtracts `prep_codes()`. A prep code:

  · is benchmarked from the BASELINE table — KG of Garnet per m² for an OLD or
    a NEW surface (the operator's Track 4) — never from its recipe line. Until
    somebody saves a figure, NEW falls back to the workbook's `For_1_SQM`
    (ruling Q15-9: 20 steel / 18 concrete on 2026-09-27) and OLD has no
    benchmark at all, which the queue flags HIGH like any missing benchmark;
  · is offered for EVERY equipment tag, by the tag's substrate (ruling Q15-5):
    CV / CONCRETE → ESC1, ME / TANK / VESSEL → ESC2 — not only for tags that
    carry the code, because no tag does: Garnet is used on all equipment;
  · credits NO area anywhere (ruling Q15-6: benchmark and variance only). The
    blasted area is already recorded by the supervisor's blasting execution
    entries (`sme_surface_prep_progress`); crediting it again from the Garnet
    draw would count every blasted m² twice. `execution.credit_done_sqm`
    refuses a prep code, so no path can put blasting on lining progress.

The estimator never sees any of this: it is driven by the codes the EQUIPMENT
carries (`codesByTag`), and no equipment carries a prep code (Q15-6). Both SME
engines are untouched, so the parity goldens are byte-identical (rule 1c).
"""
from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MATERIAL_GROUP = "GARNET"
STATES = ("OLD", "NEW")
STATE_LABEL = {"OLD": "Old surface", "NEW": "New surface"}
SUBSTRATE_LABEL = {"CONCRETE": "Concrete", "STEEL_VESSEL": "Steel / Vessel"}


def norm_state(v: Optional[str]) -> Optional[str]:
    """'old' / 'Old surface' / 'NEW' → 'OLD' / 'NEW'; anything else → None."""
    s = str(v or "").strip().upper()
    for st in STATES:
        if s == st or s.startswith(st + " "):
            return st
    return None


async def prep_codes(session: AsyncSession) -> set[str]:
    return {str(c).strip() for (c,) in (await session.execute(text(
        'SELECT DISTINCT "Prep_Code" FROM sme_prep_baseline'))).all() if c}


async def is_prep(session: AsyncSession, code: Optional[str]) -> bool:
    c = str(code or "").strip()
    return bool(c) and c in await prep_codes(session)


def without_prep(codes: Iterable, prep: set[str]) -> list:
    """`codes` minus the prep codes, order kept. For every lining-system list."""
    return [c for c in codes if str(c or "").strip() not in prep]


async def garnet_saps(session: AsyncSession) -> set[str]:
    """The SAPs that are Garnet.

    Every SAP on a recipe line under a prep code (1429 AUSTRALIAN on
    2026-09-27), and — operator ruling Q15-4, "treat AREEJ as Garnet" until the
    workbook lists it — any controlled-category (Surface Shield) material whose
    description says GARNET (1363 AREEJ).
    """
    rows = (await session.execute(text('''
        SELECT REPLACE(TRIM(r."SAP_Code"), ' ', '')
        FROM sme_recipe r
        WHERE TRIM(r."Lining_System_Code") IN (SELECT "Prep_Code" FROM sme_prep_baseline)
        UNION
        SELECT REPLACE(TRIM(i."SAP_Code"), ' ', '')
        FROM inventory i
        WHERE i."Equipment_Description" ILIKE '%garnet%'
          AND LOWER(TRIM(COALESCE(i."Category", ''))) = LOWER(:cat)
    '''), {"cat": await _controlled(session)})).all()
    return {str(r[0]) for r in rows if r[0]}


async def _controlled(session: AsyncSession) -> str:
    from . import quality
    return (await quality.controlled_category(session)).strip()


def code_for(type_: Optional[str], substrate: Optional[str]) -> Optional[str]:
    """Ruling Q15-5: concrete → ESC1, steel / tank / vessel → ESC2."""
    t = str(type_ or "").strip().upper()
    s = str(substrate or "").strip().upper()
    if t == "CV" or "CONCRETE" in s:
        return "ESC1"
    if t == "ME" or any(w in s for w in ("TANK", "VESSEL", "STEEL")):
        return "ESC2"
    return None


async def code_for_tag(session: AsyncSession, *, site_id: str, tag: str) -> Optional[str]:
    """The prep code this equipment's substrate calls for, or None when the
    master does not say (or says two things)."""
    rows = (await session.execute(text(
        'SELECT DISTINCT "Type", "Substrate" FROM sme_equipment '
        'WHERE "Site_ID" = :s AND "Equipment_Tag_No" = :t'),
        {"s": site_id, "t": tag})).all()
    codes = {code_for(t, s) for t, s in rows} - {None}
    if len(codes) != 1:
        return None
    code = codes.pop()
    return code if code in await prep_codes(session) else None


async def pair_ok(session: AsyncSession, *, site_id: str, tag: str, code: str) -> bool:
    """May this area be attributed to (tag, code)?

    A lining code: the tag must CARRY it (unchanged since Phase 13). A prep
    code: the tag must exist and its substrate must call for that code.
    """
    code = (code or "").strip()
    if await is_prep(session, code):
        return (await code_for_tag(session, site_id=site_id, tag=tag)) == code
    return bool((await session.execute(text(
        'SELECT COUNT(*) FROM sme_equipment WHERE "Site_ID" = :s '
        'AND "Equipment_Tag_No" = :t AND "Lining_System_Code" = :c'),
        {"s": site_id, "t": tag, "c": code})).scalar())


async def baseline(session: AsyncSession, code: str, state: str) -> dict:
    """The benchmark for (code, state): what is saved, else (NEW only) the
    workbook's figure. `source` says which: 'set' | 'workbook' | None."""
    row = (await session.execute(text(
        'SELECT "KG_Per_SQM", updated_by, updated_at FROM sme_prep_baseline '
        'WHERE "Prep_Code" = :c AND "Surface_State" = :st AND "Material_Group" = :g'),
        {"c": code, "st": state, "g": MATERIAL_GROUP})).mappings().first()
    saved = None if row is None or row["KG_Per_SQM"] is None else float(row["KG_Per_SQM"])
    workbook = await workbook_rate(session, code)
    if saved is not None:
        return {"kg_per_sqm": saved, "source": "set", "workbook": workbook,
                "updated_by": row["updated_by"], "updated_at": row["updated_at"]}
    if state == "NEW" and workbook is not None:
        return {"kg_per_sqm": workbook, "source": "workbook", "workbook": workbook,
                "updated_by": None, "updated_at": None}
    return {"kg_per_sqm": None, "source": None, "workbook": workbook,
            "updated_by": None, "updated_at": None}


async def workbook_rate(session: AsyncSession, code: str) -> Optional[float]:
    v = (await session.execute(text(
        'SELECT MAX("For_1_SQM") FROM sme_recipe WHERE TRIM("Lining_System_Code") = :c'),
        {"c": code})).scalar()
    return None if v is None else float(v)


async def rate(session: AsyncSession, code: str, state: Optional[str]) -> Optional[float]:
    st = norm_state(state)
    if st is None:
        return None
    return (await baseline(session, code, st))["kg_per_sqm"]


async def last_state(session: AsyncSession, *, site_id: str, tag: str) -> Optional[str]:
    """The Old/New answer last given for this equipment (ruling Q15-7: asked
    per job, pre-filled from the equipment's last answer)."""
    v = (await session.execute(text(
        'SELECT "Surface_State" FROM sme_attribution_group '
        'WHERE "Site_ID" = :s AND "Equipment_Tag_No" = :t '
        'AND "Surface_State" IS NOT NULL AND status IN (\'staged\', \'committed\') '
        'ORDER BY id DESC LIMIT 1'), {"s": site_id, "t": tag})).scalar()
    return norm_state(v)


async def table(session: AsyncSession) -> list[dict]:
    """The setup page's rows: every (prep code, state) with its benchmark."""
    rows = (await session.execute(text(
        'SELECT "Prep_Code", "Surface_State", "Substrate_Class" FROM sme_prep_baseline '
        'WHERE "Material_Group" = :g ORDER BY "Prep_Code" DESC, "Surface_State" DESC'),
        {"g": MATERIAL_GROUP})).mappings().all()
    out = []
    for r in rows:
        b = await baseline(session, r["Prep_Code"], r["Surface_State"])
        out.append({"code": r["Prep_Code"], "state": r["Surface_State"],
                    "state_label": STATE_LABEL.get(r["Surface_State"], r["Surface_State"]),
                    "substrate": SUBSTRATE_LABEL.get(r["Substrate_Class"], r["Substrate_Class"]),
                    **b})
    return out


async def set_baseline(session: AsyncSession, *, code: str, state: str,
                       kg_per_sqm: Optional[float], username: str) -> dict:
    """Save one benchmark (None clears it). Audited, old → new. A committed
    job keeps the benchmark it was measured against (snapshotted on the row)."""
    from .ledger import write_audit
    st = norm_state(state)
    if st is None:
        raise HTTPException(422, "state must be OLD or NEW")
    if kg_per_sqm is not None and not (0 < float(kg_per_sqm) <= 1000):
        raise HTTPException(422, "KG per m² must be greater than 0 (and at most 1000)")
    old = (await session.execute(text(
        'SELECT "KG_Per_SQM" FROM sme_prep_baseline WHERE "Prep_Code" = :c '
        'AND "Surface_State" = :st AND "Material_Group" = :g'),
        {"c": code, "st": st, "g": MATERIAL_GROUP})).first()
    if old is None:
        raise HTTPException(404, f"no Garnet baseline for {code!r} — the prep codes are "
                                 f"{', '.join(sorted(await prep_codes(session)))}")
    await session.execute(text(
        'UPDATE sme_prep_baseline SET "KG_Per_SQM" = :v, updated_by = :u, '
        'updated_at = CURRENT_TIMESTAMP WHERE "Prep_Code" = :c AND "Surface_State" = :st '
        'AND "Material_Group" = :g'),
        {"v": None if kg_per_sqm is None else float(kg_per_sqm), "u": username,
         "c": code, "st": st, "g": MATERIAL_GROUP})
    await write_audit(session, username, "SME_PREP_BASELINE", "sme_prep_baseline",
                      f"{MATERIAL_GROUP} {code} {st}: {old[0]} → {kg_per_sqm} KG/m²")
    return await baseline(session, code, st)


async def pair_message(session: AsyncSession, *, site_id: str, tag: str,
                       code: str) -> Optional[str]:
    """Why (tag, prep code) is refused, in words; None for a lining code (its
    callers keep their own message)."""
    if not await is_prep(session, code):
        return None
    want = await code_for_tag(session, site_id=site_id, tag=tag)
    if want is None:
        return (f"the equipment master does not say whether {tag} is concrete or "
                f"steel / vessel (its Type / Substrate), so its Garnet cannot be "
                f"benchmarked. Fill them in SME → Master Data → Equipment.")
    kind = "concrete" if want == "ESC1" else "steel / vessel"
    return f"{tag} is {kind} — its Garnet is {want}, not {code}."
