"""
Smart Material Estimator (SME) — Bootstrap Loader
==================================================
One-shot loader for the SME master data into the ERP's gi_database.db.

Three Excel files seed three SME-owned tables:
  - For_1_SQM.xlsx                      → sme_recipe         (global)
  - Equipment.xlsx                      → sme_equipment      (per Site_ID)
  - Materials_DetailsAvailable_Qty.xlsx → sme_inventory_seed (global)

The estimator's Available_Qty is COMPUTED at read time as
    Initial_Available_Qty + receipts.sum - consumption.sum
via the `sme_materials_view` SQL view — see database.py. This script only
seeds the static baselines (Initial_* columns + master attributes).

R20.5 — switched to INSERT OR IGNORE semantics so manual edits made via the
Master Data tab survive a re-bootstrap. To force-overwrite seed values from
Excel, pass --force.

Run:
    python3 scripts/sme_bootstrap.py --site-id HQ
    python3 scripts/sme_bootstrap.py --site-id CNCEC
    python3 scripts/sme_bootstrap.py --site-id HQ --dry-run
    python3 scripts/sme_bootstrap.py --site-id HQ --force
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

# Allow running as a script from any CWD by anchoring imports to repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

import database as D  # noqa: E402

_SEED_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "sme_seed_data")
_FILE_INV  = os.path.join(_SEED_DIR, "Materials_DetailsAvailable_Qty.xlsx")
_FILE_REC  = os.path.join(_SEED_DIR, "For_1_SQM.xlsx")
_FILE_EQ   = os.path.join(_SEED_DIR, "Equipment.xlsx")


# ---------------------------------------------------------------------------
# Cleaners
# ---------------------------------------------------------------------------

def _clean_recipe(df_b: pd.DataFrame) -> pd.DataFrame:
    """Normalize For_1_SQM.xlsx into the sme_recipe contract.

    R20.5 — keeps every Excel column the SME UI cares about. Renames the
    Excel column "System Key's" to System_Keys, "PACKAGE SIZE" to
    Package_Size, and "Sl. #" to Sl_No to match table column names.
    """
    df = df_b.copy()
    # Rename oddball Excel headers to table-friendly names.
    df = df.rename(columns={
        "Sl. #":         "Sl_No",
        "System Key's":  "System_Keys",
        "PACKAGE SIZE":  "Package_Size",
        "Lining_Thicknes": "Lining_Thickness",  # typo in source spreadsheet
    })
    keep = [
        "Sl_No", "Lining_System_Code", "Substrate", "Lining_System",
        "System_Keys", "Lining_Thickness", "Lining_System_Short_Name",
        "Lining_Type", "Material_Code", "Material_Description",
        "Material_Name", "For_1_SQM", "UOM", "Package_Size",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.dropna(subset=["Lining_System_Code", "Material_Code"])

    # Material_Code may carry comma-separated alternates — explode them.
    df["Material_Code"] = df["Material_Code"].astype(str).str.strip()
    df = df.assign(
        Material_Code=df["Material_Code"].str.split(r",\s*")
    ).explode("Material_Code").reset_index(drop=True)
    df["Material_Code"] = df["Material_Code"].str.strip()

    df["Lining_System_Code"] = (
        df["Lining_System_Code"].astype(float).astype(int).astype(str)
    )
    df["For_1_SQM"] = pd.to_numeric(df["For_1_SQM"], errors="coerce").fillna(0.0)

    # Coerce all string columns + strip "nan" sentinels.
    for col in ("Sl_No", "Substrate", "Lining_System", "System_Keys",
                "Lining_Thickness", "Lining_System_Short_Name", "Lining_Type",
                "Material_Description", "Material_Name", "UOM", "Package_Size"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": None})
    return df


def _blank(v) -> bool:
    """True when an Excel cell is effectively empty."""
    return pd.isna(v) or str(v).strip() in ("", "nan", "None")


def _clean_equipment(df_c: pd.DataFrame,
                     short_name_code_map: dict | None = None) -> pd.DataFrame:
    """Normalize Equipment.xlsx into the sme_equipment contract.

    R20.5 — keeps every Excel column the SME UI cares about and renames
    oddball Excel headers (Sl. #, WBS #, IO#, Drawing #, Dia / L, Ht. /W,
    Equipment Total SQM, Material Spec., Lining_Area/location) to the
    snake_case table column names.

    2026-07-07 — civil-work areas (e.g. "PPA Storage Tank Area") have no
    Equipment_Tag_No. in the sheet; their Name IS their identity (the
    original SME DB stored the name as the tag). Backfill the tag from Name,
    and backfill a missing Lining_System_Code from Lining_System_Short_Name
    via the recipe map, instead of silently dropping those rows.
    """
    df = df_c.copy()
    df = df.rename(columns={
        "Sl. #":                "Sl_No",
        "WBS #":                "WBS_No",
        "IO#":                  "IO_No",
        "Drawing #":            "Drawing_No",
        "Dia / L":              "Dia_L",
        "Ht. /W":               "Ht_W",
        "Equipment Total SQM":  "Equipment_Total_SQM",
        "Material Spec.":       "Material_Spec",
        "Lining_Area/location": "Lining_Area_Location",
    })
    keep = [
        "Sl_No", "Project", "WBS_No", "IO_No", "Sub_Location", "Location",
        "Type", "Substrate", "Equipment_Tag_No.", "Name", "Drawing_No",
        "Design", "Dia_L", "Ht_W", "Equipment_Total_SQM", "Remaraks",
        "Lining_System_Code", "Lining_System_Short_Name", "Lining_Type",
        "Lining_System", "Material_Spec", "Lining_Area_Location",
        "Surface_Area_SQM",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()

    # Tag backfill: name-identified civil areas keep their Name as the tag.
    _no_tag = df["Equipment_Tag_No."].apply(_blank)
    _has_name = ~df["Name"].apply(_blank)
    _fill_tag = _no_tag & _has_name
    if int(_fill_tag.sum()):
        df.loc[_fill_tag, "Equipment_Tag_No."] = (
            df.loc[_fill_tag, "Name"].astype(str).str.strip()
        )
        print(f"  Backfilled Equipment_Tag_No. from Name for "
              f"{int(_fill_tag.sum())} row(s) "
              f"({df.loc[_fill_tag, 'Equipment_Tag_No.'].nunique()} area(s)).")

    # Code backfill: some rows carry only the short name (e.g. CBL30).
    if short_name_code_map:
        _no_code = df["Lining_System_Code"].apply(_blank)
        _short = df["Lining_System_Short_Name"].astype(str).str.strip()
        _mapped = _short.map(short_name_code_map)
        _fill_code = _no_code & _mapped.notna()
        if int(_fill_code.sum()):
            df["Lining_System_Code"] = df["Lining_System_Code"].astype(object)
            df.loc[_fill_code, "Lining_System_Code"] = _mapped[_fill_code]
            print(f"  Backfilled Lining_System_Code from short name for "
                  f"{int(_fill_code.sum())} row(s).")

    df = df.dropna(subset=["Equipment_Tag_No.", "Lining_System_Code"])

    for col in ("Sl_No", "Project", "WBS_No", "IO_No", "Sub_Location",
                "Drawing_No", "Design", "Dia_L", "Ht_W", "Remaraks",
                "Equipment_Tag_No.", "Name", "Location", "Type", "Substrate",
                "Lining_System_Short_Name", "Lining_Type", "Lining_System",
                "Material_Spec", "Lining_Area_Location"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": None})

    # Normalize Location casing to the portal's canonical LOCATION_ORDER
    # (e.g. "BROWN FIELD" → "Brown Field") so the dashboard shows one entry
    # per location instead of case-variant duplicates.
    _LOCATION_CANON = {"Brown Field", "TRAIN J", "TRAIN K"}
    _loc_lookup = {c.lower(): c for c in _LOCATION_CANON}
    if "Location" in df.columns:
        df["Location"] = df["Location"].map(
            lambda v: _loc_lookup.get(str(v).strip().lower(), v)
            if v is not None else v
        )

    # Drop rows whose Lining_System_Code is not numeric. The 2026-06 file
    # carries "To_Be_Confirmed_LSC" placeholder rows for equipment not yet
    # assigned a lining system — they can't be material-estimated and would
    # break the portal's integer code-sort. Skip them (and report the count).
    _code_num = pd.to_numeric(df["Lining_System_Code"], errors="coerce")
    _dropped = int(_code_num.isna().sum())
    if _dropped:
        print(f"  Skipping {_dropped} row(s) with non-numeric "
              f"Lining_System_Code (e.g. To_Be_Confirmed_LSC).")
    df = df[_code_num.notna()].copy()

    df["Lining_System_Code"] = (
        df["Lining_System_Code"].astype(float).astype(int).astype(str)
    )
    df["Surface_Area_SQM"] = pd.to_numeric(
        df["Surface_Area_SQM"], errors="coerce",
    )
    if "Equipment_Total_SQM" in df.columns:
        df["Equipment_Total_SQM"] = pd.to_numeric(
            df["Equipment_Total_SQM"], errors="coerce",
        )
    before = len(df)
    df = df.dropna(subset=["Surface_Area_SQM"])
    df = df[df["Surface_Area_SQM"] > 0].reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  [warn] dropped {before - after} equipment rows "
              f"with null / zero Surface_Area_SQM")

    # Aggregate area-split rows. The file lists one row per physical area
    # (Bottom, Shell, Dish End…) for a given equipment+lining-code, each with
    # its own SQM. sme_equipment holds one row per (tag, code), so SUM the area
    # SQM into the per-(tag,code) total (else the first area would win and the
    # rest would be silently dropped → undercounted material). Area labels are
    # joined for context; every other field takes the first non-empty value.
    _GROUP = ["Equipment_Tag_No.", "Lining_System_Code"]

    def _first_real(s):
        for x in s:
            if pd.notna(x) and str(x).strip() not in ("", "None", "nan"):
                return x
        return None

    def _join_areas(s):
        seen, out = set(), []
        for x in s:
            t = "" if x is None else str(x).strip()
            if t and t not in ("None", "nan") and t not in seen:
                seen.add(t)
                out.append(t)
        return " + ".join(out) if out else None

    agg_spec = {
        c: _first_real for c in df.columns
        if c not in _GROUP and c not in ("Surface_Area_SQM", "Lining_Area_Location")
    }
    agg_spec["Surface_Area_SQM"] = "sum"
    if "Lining_Area_Location" in df.columns:
        agg_spec["Lining_Area_Location"] = _join_areas

    _combos_before = len(df)
    df = df.groupby(_GROUP, as_index=False).agg(agg_spec)
    if len(df) != _combos_before:
        print(f"  Aggregated {_combos_before} area rows → {len(df)} "
              f"unique (tag, code) rows (SQM summed).")
    return df


def _clean_inventory_seed(df_a: pd.DataFrame) -> pd.DataFrame:
    """Normalize Materials_DetailsAvailable_Qty.xlsx into sme_inventory_seed.

    The Excel often has multiple PO lines per Material_Code (one per
    purchase document). We aggregate: sum Available_Qty + Ordered_Qty;
    pick the most recent Document_Date and first non-null vendor / PO #
    for display.
    """
    df = df_a.copy()
    df = df.rename(columns={
        "Vendor/supplying plant": "Vendor",
        "Purchasing Document":    "Purchasing_Document",
        "Document Date":          "Document_Date",
    })
    keep = [
        "Item", "Vendor", "Purchasing_Document", "Document_Date",
        "Material_Code", "Material_Name", "Nature", "UOM",
        "Available_Qty", "Ordered_Qty",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.dropna(subset=["Material_Code"])
    df["Material_Code"] = df["Material_Code"].astype(str).str.strip()
    df = df[df["Material_Code"] != ""].copy()

    # Coerce numerics
    df["Available_Qty"] = pd.to_numeric(df.get("Available_Qty"), errors="coerce").fillna(0.0)
    df["Ordered_Qty"]   = pd.to_numeric(df.get("Ordered_Qty"),   errors="coerce").fillna(0.0)

    # Document_Date → ISO string (sqlite-friendly), most recent wins on agg.
    if "Document_Date" in df.columns:
        df["Document_Date"] = pd.to_datetime(
            df["Document_Date"], errors="coerce",
        ).dt.strftime("%Y-%m-%d")

    # Aggregate by Material_Code: sum qty, take first non-null for the rest.
    agg = (df.groupby("Material_Code", as_index=False)
             .agg({
                 "Material_Name":        lambda s: next((x for x in s if pd.notna(x) and str(x).strip()), None),
                 "Item":                 lambda s: next((x for x in s if pd.notna(x)), None),
                 "Vendor":               lambda s: next((x for x in s if pd.notna(x) and str(x).strip()), None),
                 "Purchasing_Document":  lambda s: next((str(int(x)) for x in s if pd.notna(x)), None),
                 "Document_Date":        "max",
                 "Nature":               lambda s: next((x for x in s if pd.notna(x) and str(x).strip()), None),
                 "UOM":                  lambda s: next((x for x in s if pd.notna(x) and str(x).strip()), None),
                 "Available_Qty":        "sum",
                 "Ordered_Qty":          "sum",
             }))

    # Cast Item to text (it comes through as float in Excel).
    if "Item" in agg.columns:
        agg["Item"] = agg["Item"].apply(
            lambda x: None if pd.isna(x) else str(int(x)) if isinstance(x, float) else str(x).strip()
        )
    return agg


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_recipes(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    force: bool,
) -> int:
    """Load recipes. R20.5 — INSERT OR IGNORE by default so manual edits
    survive. --force does an upfront DELETE + INSERT to re-baseline from Excel."""
    if force:
        conn.execute("DELETE FROM sme_recipe")
    rows = 0
    for _, r in df.iterrows():
        # Prefer 'Lining_System_Short_Name'; fall back to 'Lining_Type'.
        name = (r.get("Lining_System_Short_Name")
                or r.get("Lining_Type") or None)
        material_name = (r.get("Material_Name")
                         or r.get("Material_Description") or None)
        conn.execute(
            "INSERT OR IGNORE INTO sme_recipe "
            "(Lining_System_Code, Lining_System_Name, Material_Code, "
            " Material_Name, UOM, Nature, For_1_SQM, "
            " Sl_No, Substrate, System_Keys, Lining_Thickness, "
            " Lining_System, Lining_Type, Material_Description, Package_Size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(r["Lining_System_Code"]).strip(),
                name,
                str(r["Material_Code"]).strip(),
                material_name,
                r.get("UOM"),
                None,  # Nature isn't in File B; reserved for future use
                float(r["For_1_SQM"]) if pd.notna(r["For_1_SQM"]) else 0.0,
                r.get("Sl_No"),
                r.get("Substrate"),
                r.get("System_Keys"),
                r.get("Lining_Thickness"),
                r.get("Lining_System"),
                r.get("Lining_Type"),
                r.get("Material_Description"),
                r.get("Package_Size"),
            ),
        )
        rows += 1
    return rows


def _load_equipment(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    site_id: str,
    force: bool,
) -> int:
    """Load equipment for the given Site_ID. R20.5 — INSERT OR IGNORE by
    default so manual edits survive. --force does an upfront DELETE +
    INSERT to re-baseline from Excel for this site only."""
    if force:
        conn.execute("DELETE FROM sme_equipment WHERE Site_ID = ?", (site_id,))
    rows = 0
    for _, r in df.iterrows():
        conn.execute(
            "INSERT OR IGNORE INTO sme_equipment "
            "(Site_ID, Equipment_Tag_No, Name, Location, Type, Substrate, "
            " Lining_System_Code, Surface_Area_SQM, "
            " Sl_No, Project, WBS_No, IO_No, Sub_Location, Drawing_No, Design, "
            " Dia_L, Ht_W, Equipment_Total_SQM, Remaraks, "
            " Lining_System_Short_Name, Lining_Type, Lining_System, "
            " Material_Spec, Lining_Area_Location) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?)",
            (
                site_id,
                str(r["Equipment_Tag_No."]).strip(),
                r.get("Name"),
                r.get("Location"),
                r.get("Type"),
                # Substrate is the xlsx Substrate column ONLY (TANK / VESSEL /
                # CONCRETE). It is a DIFFERENT concept from Lining_Type and must
                # never borrow from it — doing so caused the R-fix data bug where
                # Substrate held lining names and Lining_Type was empty.
                r.get("Substrate"),
                str(r["Lining_System_Code"]).strip(),
                float(r["Surface_Area_SQM"]),
                r.get("Sl_No"),
                r.get("Project"),
                r.get("WBS_No"),
                r.get("IO_No"),
                r.get("Sub_Location"),
                r.get("Drawing_No"),
                r.get("Design"),
                r.get("Dia_L"),
                r.get("Ht_W"),
                float(r["Equipment_Total_SQM"])
                    if pd.notna(r.get("Equipment_Total_SQM")) else None,
                r.get("Remaraks"),
                r.get("Lining_System_Short_Name"),
                r.get("Lining_Type"),
                r.get("Lining_System"),
                r.get("Material_Spec"),
                r.get("Lining_Area_Location"),
            ),
        )
        rows += 1
    return rows


def _load_inventory_seed(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    force: bool,
) -> int:
    """Load sme_inventory_seed (SME-owned baseline, separate from ERP
    inventory). R20.5 — INSERT OR IGNORE by default; --force re-baselines.

    Aggregated row per Material_Code is the contract."""
    if force:
        conn.execute("DELETE FROM sme_inventory_seed")
    rows = 0
    for _, r in df.iterrows():
        conn.execute(
            "INSERT OR IGNORE INTO sme_inventory_seed "
            "(Material_Code, Material_Name, Item, Vendor, Purchasing_Document, "
            " Document_Date, Nature, UOM, "
            " Initial_Available_Qty, Initial_Ordered_Qty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(r["Material_Code"]).strip(),
                r.get("Material_Name"),
                r.get("Item"),
                r.get("Vendor"),
                r.get("Purchasing_Document"),
                r.get("Document_Date"),
                r.get("Nature"),
                r.get("UOM"),
                float(r.get("Available_Qty") or 0.0),
                float(r.get("Ordered_Qty")   or 0.0),
            ),
        )
        rows += 1
    return rows


def _seed_progress(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    site_id: str,
) -> int:
    """Upsert SME SQM progress per (tag × system_code). Always uses the
    upsert helper which preserves existing Done_SQM (idempotent — safe
    to re-run regardless of --force)."""
    grouped = df.groupby(
        ["Equipment_Tag_No.", "Lining_System_Code"], as_index=False,
    )["Surface_Area_SQM"].sum()
    rows = 0
    for _, r in grouped.iterrows():
        D.upsert_sme_sqm_progress(
            site_id=site_id,
            equipment_tag=str(r["Equipment_Tag_No."]).strip(),
            lining_system_code=str(r["Lining_System_Code"]).strip(),
            original_sqm=float(r["Surface_Area_SQM"]),
            conn=conn,
        )
        rows += 1
    return rows


def _seed_site_dropdowns(conn: sqlite3.Connection, site_id: str) -> None:
    """Copy HQ's seed location/type values onto a brand-new Site_ID so the
    portal has populated dropdowns on first render."""
    for category in ("sme_location", "sme_equipment_type"):
        existing = conn.execute(
            "SELECT COUNT(*) FROM system_settings "
            "WHERE category=? AND Site_ID=?",
            (category, site_id),
        ).fetchone()[0]
        if existing:
            continue
        seeds = conn.execute(
            "SELECT value FROM system_settings "
            "WHERE category=? AND Site_ID='HQ'",
            (category,),
        ).fetchall()
        for (val,) in seeds:
            conn.execute(
                "INSERT INTO system_settings (category, value, Site_ID) "
                "VALUES (?, ?, ?)",
                (category, val, site_id),
            )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Bootstrap SME master data into gi_database.db",
    )
    ap.add_argument("--site-id", required=True,
                    help="ERP Site_ID to load equipment + progress under")
    ap.add_argument("--db", default=None,
                    help="Override DB path (default: ERP gi_database.db)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse + report row counts; no DB writes")
    ap.add_argument("--force", action="store_true",
                    help="Wipe existing rows before INSERT (re-baseline from "
                         "Excel — discards any manual edits made in the "
                         "Master Data tab).")
    ap.add_argument("--equipment-only", action="store_true",
                    help="Only reload sme_equipment (+ sqm progress) from "
                         "Equipment.xlsx; skip recipe + inventory_seed. Use "
                         "when ONLY Equipment.xlsx changed. Pair with --force "
                         "to re-baseline the site's equipment.")
    args = ap.parse_args()

    for p in (_FILE_INV, _FILE_REC, _FILE_EQ):
        if not os.path.exists(p):
            print(f"[error] missing seed file: {p}", file=sys.stderr)
            return 2

    print("=" * 60)
    print(f"  SME Bootstrap — Site_ID={args.site_id}  "
          f"{'(DRY RUN)' if args.dry_run else ''}"
          f"{'  [FORCE]' if args.force else '  [ignore-mode]'}")
    print("=" * 60)
    if not args.force:
        print("  Note: INSERT OR IGNORE — existing rows are preserved so")
        print("        manual Master Data edits survive. Pass --force to")
        print("        wipe + reload from Excel.")

    print("\n[1/4] Cleaning recipe master from For_1_SQM.xlsx …")
    rec_raw = pd.read_excel(_FILE_REC,
                            sheet_name="LINING SYSTEM MATERIAL CONSM")
    rec_raw.columns = rec_raw.columns.str.strip()
    recipe = _clean_recipe(rec_raw)
    print(f"      {len(recipe):>4} clean recipe rows")

    print("\n[2/4] Cleaning equipment master from Equipment.xlsx …")
    eq_raw = pd.read_excel(_FILE_EQ, sheet_name="Data Input")
    eq_raw.columns = eq_raw.columns.str.strip()
    _code_map = (
        recipe.dropna(subset=["Lining_System_Short_Name"])
              .drop_duplicates("Lining_System_Short_Name")
              .set_index("Lining_System_Short_Name")["Lining_System_Code"]
              .to_dict()
    )
    equip = _clean_equipment(eq_raw, short_name_code_map=_code_map)
    print(f"      {len(equip):>4} clean equipment rows "
          f"({equip['Equipment_Tag_No.'].nunique()} unique tags)")

    print("\n[3/4] Cleaning inventory seed from "
          "Materials_DetailsAvailable_Qty.xlsx …")
    inv_raw = pd.read_excel(_FILE_INV)
    inv_raw.columns = inv_raw.columns.str.strip()
    inv = _clean_inventory_seed(inv_raw)
    print(f"      {len(inv):>4} unique Material_Codes "
          f"(aggregated from {len(inv_raw)} PO lines)")

    if args.dry_run:
        print("\n[dry-run] no DB writes; exiting.")
        return 0

    db_path = args.db or D.DB_FILE
    print(f"\n[4/4] Writing to {db_path} …")
    conn = sqlite3.connect(db_path)
    try:
        D.init_db(conn)  # self-heal first
        eq_n   = _load_equipment(conn, equip, site_id=args.site_id, force=args.force)
        prog_n = _seed_progress(conn, equip, site_id=args.site_id)
        _seed_site_dropdowns(conn, site_id=args.site_id)
        if args.equipment_only:
            rec_n = inv_n = "skipped (--equipment-only)"
        else:
            rec_n  = _load_recipes(conn, recipe, force=args.force)
            inv_n  = _load_inventory_seed(conn, inv, force=args.force)
        conn.commit()
        print(f"      sme_recipe         : {rec_n}")
        print(f"      sme_equipment      : attempted {eq_n} rows  "
              f"(Site_ID={args.site_id})")
        print(f"      sme_inventory_seed : {inv_n}")
        print(f"      sme_sqm_progress   : {prog_n} rows (Done_SQM preserved)")
    finally:
        conn.close()

    print("\n✅ Bootstrap complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
