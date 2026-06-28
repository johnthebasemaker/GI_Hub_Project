"""
Build a SANITIZED demo database — `demo_seed.db`
================================================
Generates a self-contained, populated database for the PUBLIC Streamlit Cloud
share. It contains ONLY synthetic sample data:
  • generic inventory, SME equipment/recipe, and Man-Hours rows
  • obviously-fake employee names ("Demo Worker 01" …)
  • NO real credentials and NO real PII

Login accounts are NOT stored here — the app seeds default demo accounts at
runtime (auth.seed_default_users), so no password hashes are ever committed.

The live app picks this file automatically on Streamlit Cloud, where the real
`gi_database.db` is gitignored and absent (see DB_FILE fallback in database.py).
Run:  python3 scripts/build_demo_seed.py
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import database as D  # noqa: E402

DEMO = os.path.join(_REPO, "demo_seed.db")
SITE = "DEMO-SITE"


def main() -> int:
    if os.path.exists(DEMO):
        os.remove(DEMO)
    conn = D.get_connection(DEMO)
    D.init_db(conn)
    c = conn.cursor()

    # Sites (dropdown) -------------------------------------------------------
    for s in (SITE, "DEMO-HQ"):
        c.execute("INSERT OR IGNORE INTO system_settings(category,value,Site_ID) "
                  "VALUES('Site',?,NULL)", (s,))

    # Inventory (synthetic) --------------------------------------------------
    inv = [
        ("DEMO-1001", "Epoxy Primer (demo)",   "RM-EP-01", "L",  50, 42.0,  SITE, "Rubber materials", 400),
        ("DEMO-1002", "Rubber Sheet 6mm (demo)","RM-RS-06", "M2", 30, 65.0,  SITE, "Rubber materials", 250),
        ("DEMO-1003", "Bonding Adhesive (demo)","RM-BA-02", "KG", 20, 28.5,  SITE, "Consumable",       180),
        ("DEMO-1004", "Acid Brick (demo)",      "BL-AB-01", "NO", 500, 3.2,  SITE, "Rubber materials", 5000),
        ("DEMO-1005", "Safety Gloves (demo)",   "GN-SG-01", "PR", 40, 4.5,   SITE, "Tools",            120),
        ("DEMO-1006", "Thinner (demo)",         "CN-TH-01", "L",  25, 9.0,   SITE, "Consumable",       90),
    ]
    c.executemany(
        "INSERT OR IGNORE INTO inventory (SAP_Code,Equipment_Description,Material_Code,"
        "UOM,Minimum_Qty,Unit_Cost,Site_ID,Category,Opening_Stock) VALUES (?,?,?,?,?,?,?,?,?)",
        inv)

    # SME recipe (material per 1 SQM) ---------------------------------------
    rec = [
        ("RL-100", "Rubber Lining Standard (demo)", "RM-EP-01", "Epoxy Primer (demo)",    "L",  0.25),
        ("RL-100", "Rubber Lining Standard (demo)", "RM-RS-06", "Rubber Sheet 6mm (demo)", "M2", 1.05),
        ("RL-100", "Rubber Lining Standard (demo)", "RM-BA-02", "Bonding Adhesive (demo)", "KG", 0.40),
        ("BL-200", "Brick Lining Acidproof (demo)", "BL-AB-01", "Acid Brick (demo)",       "NO", 52.0),
        ("BL-200", "Brick Lining Acidproof (demo)", "RM-EP-01", "Epoxy Primer (demo)",     "L",  0.30),
    ]
    c.executemany(
        "INSERT INTO sme_recipe (Lining_System_Code,Lining_System_Name,Material_Code,"
        "Material_Name,UOM,For_1_SQM) VALUES (?,?,?,?,?,?)", rec)

    # SME equipment ----------------------------------------------------------
    eq = [
        (SITE, "TK-101", "Storage Tank 101 (demo)", "Train J", "RL-100", 120.0),
        (SITE, "TK-102", "Storage Tank 102 (demo)", "Train J", "RL-100", 95.0),
        (SITE, "RX-201", "Reactor 201 (demo)",      "Train K", "BL-200", 60.0),
    ]
    c.executemany(
        "INSERT INTO sme_equipment (Site_ID,Equipment_Tag_No,Name,Location,"
        "Lining_System_Code,Surface_Area_SQM) VALUES (?,?,?,?,?,?)", eq)
    conn.commit()

    # Man-Hours (synthetic, fake names) -------------------------------------
    emps = [("D-01", "Demo Worker 01", "Foreman"), ("D-02", "Demo Worker 02", "Painter"),
            ("D-03", "Demo Worker 03", "Painter"), ("D-04", "Demo Worker 04", "Helper"),
            ("D-05", "Demo Sub 05",    "Helper")]
    for code, name, desig in emps:
        wt = "Supply" if code == "D-05" else "OWN"
        D.upsert_mh_employee(SITE, code, name, designation=desig, worker_type=wt,
                             company="DMC-Demo" if wt == "Supply" else "GI-Demo",
                             created_by="demo_seed", conn=conn)
    for day in ("2026-06-01", "2026-06-02", "2026-06-03"):
        for code, _, _ in emps:
            D.add_mh_timesheet(SITE, code, day, "07:30", "16:30",
                               location="Train J", equipment_tag="TK-101",
                               system_code="RL-100", created_by="demo_seed", conn=conn)
        D.set_mh_production(SITE, day, "TK-101", "RL-100", 30.0,
                            distribution_method="even", created_by="demo_seed", conn=conn)
    # one estimate so the variance dashboard has something to compare
    D.upsert_mh_estimate(SITE, "TK-101", "RL-100", 90.0, location="Train J",
                         estimated_sqm=120.0, basis="Demo budget", created_by="demo_seed", conn=conn)
    conn.commit()

    print("✅ demo_seed.db built:")
    for t in ("inventory", "sme_recipe", "sme_equipment", "mh_employees",
              "mh_timesheets", "mh_manhour_estimates", "users"):
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        tag = "  ← 0 on purpose; app seeds demo logins at runtime" if t == "users" else ""
        print(f"   {t}: {n}{tag}")
    conn.close()
    print(f"Wrote {DEMO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
