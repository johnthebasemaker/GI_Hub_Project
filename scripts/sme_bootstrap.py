"""
Smart Material Estimator (SME) — Bootstrap Loader
==================================================
One-shot loader for the SME master data (equipment surface areas + lining-
system recipes) into the ERP's gi_database.db.

The estimator is a READ-ONLY projection over the ERP ledger — Available_Qty
and Ordered_Qty come from receipts/consumption and purchase_orders at read
time. This script only seeds the project master data the engine joins on.

Run:
    python3 scripts/sme_bootstrap.py --site-id HQ
    python3 scripts/sme_bootstrap.py --site-id HQ --dry-run

Idempotent:
- sme_equipment   : DELETE rows for the target Site_ID, then INSERT fresh.
- sme_recipe      : DELETE all rows (recipes are global, not site-scoped),
                    then INSERT fresh.
- sme_sqm_progress: UPSERT — preserves Done_SQM on re-load (matches the
                    legacy SME setup_db.py contract).

Source Excel files live in scripts/sme_seed_data/ next to this script.
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
# Cleaners — port of validate_data.clean_recipe / clean_equipment
# ---------------------------------------------------------------------------
# Inventory is NOT loaded into the ERP from Excel — ERP `inventory` already
# carries Material_Code, and stock comes from the ledger. We only need
# recipes + equipment from Excel.

def _clean_recipe(df_b: pd.DataFrame) -> pd.DataFrame:
    df = df_b.copy()
    keep = [
        "Lining_System_Code", "Lining_System_Short_Name", "Lining_Type",
        "Material_Code", "Material_Description", "Material_Name",
        "For_1_SQM", "UOM",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.dropna(subset=["Lining_System_Code", "Material_Code"])

    df["Material_Code"] = df["Material_Code"].astype(str).str.strip()
    df = df.assign(
        Material_Code=df["Material_Code"].str.split(r",\s*")
    ).explode("Material_Code").reset_index(drop=True)
    df["Material_Code"] = df["Material_Code"].str.strip()

    df["Lining_System_Code"] = (
        df["Lining_System_Code"].astype(float).astype(int).astype(str)
    )
    df["For_1_SQM"] = pd.to_numeric(df["For_1_SQM"], errors="coerce").fillna(0.0)

    for col in ("Lining_System_Short_Name", "Lining_Type",
                "Material_Name", "Material_Description", "UOM"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": None})
    return df


def _clean_equipment(df_c: pd.DataFrame) -> pd.DataFrame:
    df = df_c.copy()
    keep = [
        "Location", "Type", "Lining_System_Code", "Lining_System_Short_Name",
        "Lining_Type", "Equipment_Tag_No.", "Name", "Description",
        "Surface_Area_SQM",
    ]
    df = df[[c for c in keep if c in df.columns]].copy()
    df = df.dropna(subset=["Equipment_Tag_No.", "Lining_System_Code"])

    for col in ("Equipment_Tag_No.", "Name", "Description", "Location", "Type",
                "Lining_System_Short_Name", "Lining_Type"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace({"nan": None})

    df["Lining_System_Code"] = (
        df["Lining_System_Code"].astype(float).astype(int).astype(str)
    )
    df["Surface_Area_SQM"] = pd.to_numeric(
        df["Surface_Area_SQM"], errors="coerce",
    )
    before = len(df)
    df = df.dropna(subset=["Surface_Area_SQM"])
    df = df[df["Surface_Area_SQM"] > 0].reset_index(drop=True)
    after = len(df)
    if before != after:
        print(f"  [warn] dropped {before - after} equipment rows "
              f"with null / zero Surface_Area_SQM")
    return df


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_recipes(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    # Recipes are GLOBAL (no Site_ID). Wipe + reload.
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
            " Material_Name, UOM, Nature, For_1_SQM) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(r["Lining_System_Code"]).strip(),
             name,
             str(r["Material_Code"]).strip(),
             material_name,
             r.get("UOM"),
             None,  # Nature isn't in File B; reserved for future use
             float(r["For_1_SQM"]) if pd.notna(r["For_1_SQM"]) else 0.0),
        )
        rows += 1
    return rows


def _load_equipment(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    site_id: str,
) -> int:
    # Equipment IS site-scoped. Wipe + reload for this Site_ID only.
    conn.execute("DELETE FROM sme_equipment WHERE Site_ID = ?", (site_id,))
    rows = 0
    for _, r in df.iterrows():
        conn.execute(
            "INSERT OR IGNORE INTO sme_equipment "
            "(Site_ID, Equipment_Tag_No, Name, Location, Type, Substrate, "
            " Lining_System_Code, Surface_Area_SQM) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (site_id,
             str(r["Equipment_Tag_No."]).strip(),
             r.get("Name"),
             r.get("Location"),
             r.get("Type"),
             r.get("Lining_Type"),  # 'Substrate' analogue from File C
             str(r["Lining_System_Code"]).strip(),
             float(r["Surface_Area_SQM"])),
        )
        rows += 1
    return rows


def _seed_progress(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    site_id: str,
) -> int:
    # Sum Surface_Area_SQM per (tag × system) — same shape as legacy SME.
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
            # Don't pass done_sqm — the helper preserves the existing value.
            conn=conn,
        )
        rows += 1
    return rows


def _seed_site_dropdowns(conn: sqlite3.Connection, site_id: str) -> None:
    """If the target Site_ID has no SME location/type values yet, copy the
    HQ seed set onto it. Lets brand-new sites have populated dropdowns on
    first portal render without forcing the admin into Master Data first."""
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
    args = ap.parse_args()

    for p in (_FILE_INV, _FILE_REC, _FILE_EQ):
        if not os.path.exists(p):
            print(f"[error] missing seed file: {p}", file=sys.stderr)
            return 2

    print("=" * 60)
    print(f"  SME Bootstrap — Site_ID={args.site_id}  "
          f"{'(DRY RUN)' if args.dry_run else ''}")
    print("=" * 60)

    print("\n[1/3] Cleaning recipe master from For_1_SQM.xlsx …")
    rec_raw = pd.read_excel(_FILE_REC,
                            sheet_name="LINING SYSTEM MATERIAL CONSM")
    rec_raw.columns = rec_raw.columns.str.strip()
    recipe = _clean_recipe(rec_raw)
    print(f"      {len(recipe):>4} clean recipe rows")

    print("\n[2/3] Cleaning equipment master from Equipment.xlsx …")
    eq_raw = pd.read_excel(_FILE_EQ, sheet_name="Data Input")
    eq_raw.columns = eq_raw.columns.str.strip()
    equip = _clean_equipment(eq_raw)
    print(f"      {len(equip):>4} clean equipment rows "
          f"({equip['Equipment_Tag_No.'].nunique()} unique tags)")

    if args.dry_run:
        print("\n[dry-run] no DB writes; exiting.")
        return 0

    db_path = args.db or D.DB_FILE
    print(f"\n[3/3] Writing to {db_path} …")
    conn = sqlite3.connect(db_path)
    try:
        D.init_db(conn)  # self-heal first
        rec_n = _load_recipes(conn, recipe)
        eq_n  = _load_equipment(conn, equip, site_id=args.site_id)
        prog_n = _seed_progress(conn, equip, site_id=args.site_id)
        _seed_site_dropdowns(conn, site_id=args.site_id)
        conn.commit()
        print(f"      sme_recipe        : {rec_n} rows")
        print(f"      sme_equipment     : {eq_n} rows  (Site_ID={args.site_id})")
        print(f"      sme_sqm_progress  : {prog_n} rows (Done_SQM preserved)")
    finally:
        conn.close()

    print("\n✅ Bootstrap complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
