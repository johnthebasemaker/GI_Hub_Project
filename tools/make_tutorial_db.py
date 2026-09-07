#!/usr/bin/env python3
"""
tools/make_tutorial_db.py — the SYNTHETIC database every tutorial is recorded
against (Phase 12, slice 12a). Ruling **P12-0**.

    .venv/bin/python tools/make_tutorial_db.py                 # → tutorial_fixture.db
    .venv/bin/python tools/make_tutorial_db.py --out /tmp/x.db
    .venv/bin/python tools/make_tutorial_db.py --collision-check gi_database.db

⚠️ WHY THIS EXISTS, AND WHY IT IS THE FIRST SLICE OF THE PHASE RATHER THAN A
CHORE INSIDE ONE.

`tests/e2e/global-setup.ts` builds its throwaway database by running
`tools/migration/cutover_migrate.py --wipe`, and that script's source defaults
to the repo-root `gi_database.db` — the REAL one, gitignored since commit
`a09da0b` precisely because "it holds real employee names and stock". That is
correct for a test suite: the assertions want real shapes and the rows never
leave the machine.

It is NOT correct for a video. A tutorial recorded against that clone is a
recording of live employee names, live material descriptions, live SAP codes
and live quantities — and unlike a test run, the output is an MP4, which is a
file people forward, put on a phone, and send to a contractor. The prototype's
very first frame carried all four.

**So the rule is: a tutorial is recorded against synthetic data, or it is not
published.** Not "redacted" — the recorder's redaction hook only masks what
somebody thought to name, and it is a second line, not the boundary.

────────────────────────────────────────────────────────────────────────────
WHAT IS REAL HERE, AND WHAT IS INVENTED

Operator ruling (Q1, 2026-09-06): **keep the real STRUCTURE, invent everything
else.** The distinction is worth stating precisely, because it is the one a
future edit will blur:

  REAL (a classification vocabulary — it is what the UI is *shaped* like, and a
  tutorial showing categories that do not exist teaches nothing):
    · the eleven inventory categories, in roughly their real proportions
    · the UOM vocabulary
    · the fourteen lining-system codes and names
    · work types, tank numbers, SME equipment types and locations
    · the role names, and the five account names the E2E harness binds to
    · the site id `CNCEC` and the warehouse `WH-01`

  INVENTED (every value a person or a competitor would care about):
    · all material descriptions, material codes and SAP codes
    · all employee names and ID numbers
    · all vendors
    · every quantity, price, date and lot number

⚠️ ON THE FIVE ACCOUNT NAMES. `admin`, `hod`, `supervisor`, `worker` and
`Logistics` are NOT invented, and cannot be: `tests/e2e/harness/env.ts` binds
its per-role storage states to exactly those usernames, and global-setup
asserts it reset five of them. They are role-shaped account names rather than
people — the real database's two *personal* logins are deliberately absent
here. The recorder's `redaction.replace` map is what puts a friendly display
name on screen.

⚠️ ON `CNCEC`. Also not invented, for the same mechanical reason (the QC and
SME fixtures in global-setup are pinned to it). It is a customer abbreviation,
not personal data, and these videos are internal — but if that is not
acceptable for a given audience, add `CNCEC: SITE-A` to the script's
`redaction.replace` map and it never reaches a frame.

────────────────────────────────────────────────────────────────────────────
⚠️ THE OUTPUT IS DETERMINISTIC, AND THAT IS LOAD-BEARING.

One fixed seed and one pinned `ANCHOR` date, so every value that reaches a
frame is identical on every rebuild. (The bcrypt salts are not deterministic
and do not reach a frame.) Ruling Q4 says a tutorial's
`training_modules.version` is bumped from the SHA-256 of its narration script —
so if the DATASET drifted between renders, the numbers on screen would change
while the script's hash did not, and the compliance record would point at a
video nobody has seen. `DATASET_VERSION` is bumped by hand when the generated
content changes, and the recorder writes it into the manifest.

────────────────────────────────────────────────────────────────────────────
⚠️ THE SCHEMA IS BUILT BY `legacy.database.init_db()`, NOT RESTATED HERE.
Same reasoning as `tools/make_ci_fixture_db.py`: a hand-written CREATE TABLE
list is a second copy of the legacy schema that agrees with the first until
somebody adds a column.

KNOWN GAP, stated rather than discovered later: nothing seeds `pending_issues`
/ `pending_receipts` / `pending_returns`, so an HOD's approval queue is empty.
The Store Keeper tutorials stage their own rows, so it does not matter yet; the
HOD scripts in slice 12c will need it.

⚠️ AND ONE THING THAT LOOKS LIKE A GAP IN THIS FILE AND IS NOT. On a STORE
KEEPER's dashboard, "Stock vs Minimum", "Burn forecast", "Top consumed",
"Top 5 expiring lots", "Highest value on hand" and the "Stock value (SAR)"
tile all render EMPTY — and they render empty against the real data too. They
are fed by `GET /dashboard/metrics`, which is `require_level(1)`, and
`store_keeper` is level 0 (`auth.ROLE_META`). That is rule 14 working: the
panels are not entitled, so the fetch 403s and the component falls back to its
Empty state. **Do not enrich this fixture chasing them.** Measured on the
generated dataset at `CNCEC`: 94 consumption rows inside the 30-day window,
147 items carrying a minimum, 90 dated lots — every one of those panels
populates for an HOD, a supervisor or an admin.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import pathlib
import random
import sqlite3
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = _ROOT / "tutorial_fixture.db"

# ⚠️ Bump when the generated CONTENT changes. The recorder records it in every
# manifest, so "why do the numbers differ from last quarter's video?" is a
# question with an answer.
DATASET_VERSION = 2
SEED = 20260906

# ⚠️ EVERY DATE IS COMPUTED FROM THIS CONSTANT, NOT FROM THE CLOCK, and the
# trade-off is deliberate. Anchored, the dataset ages: a lot that expires "in 27
# days" is in the past a year from now. Un-anchored, the numbers on screen would
# change every day — and ruling Q4 bumps a module's `version` from the SHA-256
# of its NARRATION script, so a dataset that drifted would change what the video
# shows while the hash said nothing had changed, and the compliance record would
# point at a video nobody has seen (P10-6). Ageing is a versioned decision
# somebody makes; drift is one nobody notices. Bump this WITH `DATASET_VERSION`.
ANCHOR = _dt.date(2026, 9, 6)

# ── REAL STRUCTURE ──────────────────────────────────────────────────────────
# Categories with their real relative weights, so the "Inventory by category"
# panel has the shape a store keeper recognises.
CATEGORIES = [
    ("R/L Consumables", 95), ("BR CC PU Tools", 44), ("Safety", 36),
    ("EQUIPMENTS/TOOLS", 35), ("R/L Tools", 26), ("Surface Shields", 21),
    ("Electrical Items", 17), ("R/L Cons", 13), ("VEHICLES", 10),
    ("CONTRACTING SERVICES", 5), ("Office", 4),
]
UOMS = ["Each", "Pcs", "Kg", "Set", "Roll", "Pair", "Box", "Pallet", "Bundle",
        "METRES", "Pkt"]
LINING_SYSTEMS = [
    ("1", "CBL30"), ("2", "CBL63"), ("3", "ARTL30"), ("4", "ARTL40"),
    ("5", "PUL4"), ("6", "PUL6"), ("7", "RLCB4"), ("9", "CONDL2"),
    ("77", "RL+CBL 30THK"), ("88", "RL+CBL 63THK"),
    ("333", "CL+RL+ARL 30THK"), ("444", "CL+RL+ARL 40THK"),
    ("777", "CL+RL+CBL 30THK"), ("888", "CL+RL+CBL 63THK"),
]
WORK_TYPES = ["Maintenance", "New Project Area", "Fabrication", "Office"]
TANKS = ["Tank 1", "Tank 2", "Tank 3"]
SME_TYPES = ["CV", "ME", "Vessel", "Tank", "Column", "Pipe", "Reactor"]
SME_LOCATIONS = ["Brown Field", "TRAIN J", "TRAIN K"]

SITE = "CNCEC"                     # pinned by tests/e2e/harness/env.ts
WAREHOUSE = "WH-01"
EXTRA_SITES = ["NORTHGATE", "HARBOUR"]   # invented, so site scoping is visible

# The five the harness binds to. Passwords are the suite's own well-known test
# values; global-setup overwrites them inside the throwaway database anyway.
HARNESS_USERS = [
    ("admin", "admin2026", "admin", "", None),
    ("hod", "hod2026", "hod", SITE, None),
    ("supervisor", "super2026", "supervisor", SITE, None),
    ("worker", "floor2026", "store_keeper", SITE, None),
    ("Logistics", "logi2026", "logistics", "", None),
]

# ── INVENTED VOCABULARY ─────────────────────────────────────────────────────
# ⚠️ Names are drawn from this fixed list rather than generated, so the set is
# reviewable in a diff and `--collision-check` can prove it disjoint from the
# real employee register. Every ID_Number is in a reserved 9xxxxx block that the
# real data does not use (its employees start at 30001).
PEOPLE = [
    "Aria Bellweather", "Tomas Halversen", "Nadia Okonjo", "Ravi Mehtala",
    "Sofia Lindqvist", "Emeka Adeyoro", "Priya Ramanujan", "Luca Ferretti",
    "Hanna Ostrowski", "Mateo Villareal", "Yusuf Karadeniz", "Ingrid Solheim",
    "Dmitri Volkov", "Chiara Bonetti",
]
VENDORS = [
    ("VND-9001", "Halcyon Industrial Supply"),
    ("VND-9002", "Redmoor Linings Ltd"),
    ("VND-9003", "Castellan Safety Works"),
    ("VND-9004", "Pellworth Tooling"),
    ("VND-9005", "Vantage Electricals"),
    ("VND-9006", "Brightwater Logistics"),
]
# Noun banks per category. Generic industrial goods — nothing here identifies a
# product line, a formulation or a supplier's catalogue.
NOUNS = {
    "R/L Consumables": ["Bonding Adhesive", "Solvent Wash", "Sealing Compound",
                        "Primer Coat", "Curing Agent", "Release Film",
                        "Masking Tape", "Mixing Paddle"],
    "BR CC PU Tools":  ["Notched Trowel", "Ribbed Roller", "Spreader Blade",
                        "Corner Iron", "Seam Roller", "Gauge Comb"],
    "Safety":          ["Nitrile Gloves", "Safety Goggles", "Ear Defenders",
                        "Coverall Suit", "Half Mask Respirator", "Safety Boots",
                        "Face Shield", "Harness Lanyard"],
    "EQUIPMENTS/TOOLS": ["Torque Wrench", "Angle Grinder", "Digital Caliper",
                         "Impact Driver", "Bench Vice", "Heat Gun"],
    "R/L Tools":       ["Rubber Mallet", "Stitching Wheel", "Skiving Knife",
                        "Pinch Roller", "Edge Trimmer"],
    "Surface Shields": ["Abrasion Panel", "Wear Tile", "Impact Liner",
                        "Deflector Plate", "Guard Segment"],
    "Electrical Items": ["Cable Gland", "Junction Box", "Contactor Relay",
                         "LED Floodlight", "Extension Reel"],
    "R/L Cons":        ["Patch Kit", "Repair Strip", "Filler Rod", "Backing Cloth"],
    "VEHICLES":        ["Wheel Chock", "Tyre Gauge", "Tow Strap", "Jump Lead"],
    "CONTRACTING SERVICES": ["Scaffold Hire", "Crane Call-Off", "Blast Cleaning",
                             "Confined Space Watch"],
    "Office":          ["Ring Binder", "Label Cartridge", "Whiteboard Marker",
                        "Print Paper A4"],
}
QUALIFIERS = ["", " Grade A", " Heavy Duty", " 3MM", " 6MM", " Large", " Medium",
              " Type II", " 25KG", " 5L", " Blue", " Black", " 500ML"]


def _iso(d: _dt.date) -> str:
    return d.isoformat()


def build(out_path: pathlib.Path, today: _dt.date) -> dict:
    """Create a fresh, legacy-shaped, fully synthetic SQLite database."""
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ⚠️ IMPORT ORDER IS THE MECHANISM, exactly as in `make_ci_fixture_db.py`
    # and `legacy/bug_check.py`: `config.DB_FILE` and `database.DB_FILE` are read
    # at connect time and must be redirected BEFORE anything opens a connection.
    # Getting this wrong writes the fixture into the operator's live database.
    legacy = str(_ROOT / "legacy")
    if legacy not in sys.path:
        sys.path.insert(0, legacy)
    os.environ.pop("DATABASE_URL", None)

    import config
    config.DB_FILE = str(out_path)
    import database
    database.DB_FILE = str(out_path)
    database.init_db()

    import bcrypt
    rng = random.Random(SEED)

    conn = database.get_connection()
    try:
        c = conn.cursor()

        # ── settings vocabulary ─────────────────────────────────────────────
        for site in (SITE, *EXTRA_SITES):
            c.execute("INSERT OR IGNORE INTO system_settings (category, value) "
                      "VALUES ('Site', ?)", (site,))
        for cat, vals in (("Work_Type", WORK_TYPES), ("Tank_No", TANKS),
                          ("sme_equipment_type", SME_TYPES),
                          ("sme_location", SME_LOCATIONS)):
            for v in vals:
                c.execute("INSERT OR IGNORE INTO system_settings (category, value) "
                          "VALUES (?,?)", (cat, v))
        c.execute('INSERT OR IGNORE INTO warehouses ("Warehouse_ID","Name",'
                  '"Location",status,created_by) VALUES (?,?,?,?,?)',
                  (WAREHOUSE, "Main Warehouse", SITE, "active", "tutorial-fixture"))
        for code, name in VENDORS:
            c.execute('INSERT OR IGNORE INTO vendors ("Vendor_Code","Vendor_Name",'
                      "status,created_by) VALUES (?,?,?,?)",
                      (code, name, "active", "tutorial-fixture"))
        for i in range(1, 5):
            c.execute('INSERT OR IGNORE INTO wbs_master ("WBS_Number","Description",'
                      '"Site_ID",status,created_by) VALUES (?,?,?,?,?)',
                      (f"WBS-90{i:02d}", f"Synthetic work package {i}", SITE,
                       "active", "tutorial-fixture"))

        # ── users ───────────────────────────────────────────────────────────
        # rounds=4: this fixture is rebuilt on demand and read by nobody outside
        # the recorder. The APP's own hashing is untouched.
        for uname, pw, role, site, wh in HARNESS_USERS:
            c.execute("INSERT OR IGNORE INTO users "
                      '(username, password_hash, role, "Site_ID", "Warehouse_ID") '
                      "VALUES (?,?,?,?,?)",
                      (uname,
                       bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode(),
                       role, site, wh))

        # ── employees ───────────────────────────────────────────────────────
        # Reserved 9xxxxx ID block: the real register starts at 30001, so a
        # synthetic badge number can never be mistaken for a real one.
        for n, person in enumerate(PEOPLE):
            c.execute('INSERT OR IGNORE INTO employees ("ID_Number","Name",'
                      '"Phone_Number","Department",status,created_by,"Site_ID") '
                      "VALUES (?,?,?,?,?,?,?)",
                      (f"9000{n + 1:02d}", person, "+966500000000",
                       rng.choice(["Production", "Maintenance", "Stores", "QC"]),
                       "active", "tutorial-fixture",
                       SITE if n < 10 else rng.choice(EXTRA_SITES)))

        # ── inventory ───────────────────────────────────────────────────────
        # 290 items so the dashboard's headline count matches the scale a store
        # keeper works at. SAP codes live in a reserved 8xxxxx block.
        items: list[tuple] = []
        sap_n = 800000
        for cat, weight in CATEGORIES:
            nouns = NOUNS[cat]
            for i in range(weight):
                sap_n += 1
                desc = f"{nouns[i % len(nouns)]}{QUALIFIERS[i % len(QUALIFIERS)]}"
                uom = rng.choice(UOMS)
                # ⚠️ ~30% deliberately UN-COSTED. Ruling P10-4 says un-costed
                # inventory is never valued at zero and is reported as
                # "Not Valued (N items)" against a total labelled a floor. A
                # fixture where everything has a price would hide the behaviour
                # the tutorials most need to explain.
                cost = 0.0 if i % 10 < 3 else round(rng.uniform(4, 950), 2)
                opening = rng.choice([0, 0, 12, 25, 40, 75, 120, 200, 480])
                minimum = rng.choice([0, 0, 10, 20, 50]) if i % 5 else 0
                items.append((f"{sap_n}", f"MAT-{sap_n}", desc, cat, uom,
                              minimum, cost, opening))
        for sap, mc, desc, cat, uom, minimum, cost, opening in items:
            c.execute("INSERT OR IGNORE INTO inventory "
                      '("SAP_Code","Material_Code","Equipment_Description",'
                      '"Category","UOM","Minimum_Qty","Unit_Cost",'
                      '"Opening_Stock","Site_ID") VALUES (?,?,?,?,?,?,?,?,?)',
                      (sap, mc, desc, cat, uom, minimum, cost, opening, SITE))

        # ── movements ───────────────────────────────────────────────────────
        # ⚠️ NON-EMPTY AND RECENT IS THE POINT. Recorded against the real E2E
        # clone every dashboard panel read "No consumption in 30 days",
        # "No minimums set", "No dated lots", "No item carries a unit cost yet".
        # A tutorial of an empty dashboard teaches nothing, so the synthetic
        # dataset is not only safer footage — it is better footage.
        movers = items[:90]
        lots_made: list[tuple[str, str, str]] = []
        for n, (sap, *_rest) in enumerate(movers):
            day = today - _dt.timedelta(days=rng.randint(2, 120))
            lot = f"LOT-9{n:04d}"
            # A spread of expiries: some already gone, some inside the 30-day
            # window the "Expiring / expired lots" tile counts, most far off.
            offset = [-40, -9, 6, 18, 27, 55, 140, 320, 640][n % 9]
            expiry = today + _dt.timedelta(days=offset)
            vendor = VENDORS[n % len(VENDORS)][1]
            qty = float(rng.choice([20, 40, 60, 100, 150, 240]))
            c.execute('INSERT INTO receipts ("Date","SAP_Code","Quantity",'
                      '"Site_ID","Supplier","Lot_Number","Expiry_Date",'
                      '"Received_by","DN_Number") VALUES (?,?,?,?,?,?,?,?,?)',
                      (_iso(day), sap, qty, SITE, vendor, lot, _iso(expiry),
                       "worker", f"DN-9{n:04d}"))
            c.execute('INSERT INTO lots ("Lot_Number","SAP_Code","Site_ID",'
                      '"Received_Date","Expiry_Date","Supplier","Status") '
                      "VALUES (?,?,?,?,?,?,?)",
                      (lot, sap, SITE, _iso(day), _iso(expiry), vendor, "open"))
            lots_made.append((lot, sap, _iso(expiry)))

        # Consumption weighted into the last 30 days so "Top consumed (30 days)"
        # and the burn forecast both have something to draw.
        for n in range(140):
            sap = movers[n % len(movers)][0]
            day = today - _dt.timedelta(days=rng.randint(0, 29) if n % 3
                                        else rng.randint(30, 110))
            lot = lots_made[n % len(lots_made)][0]
            # ⚠️ NO `status` COLUMN, AND THAT IS THE SCHEMA, NOT AN OMISSION.
            # `legacy.database.init_db()` creates the BASE table; the live
            # database's `status`, `Sl_No` and `WBS` arrived later by ALTER and
            # were never added to the initialiser. Writing them here raises
            # `no such column`, which is how this was found. It costs nothing:
            # a row in `consumption` IS the committed ledger — the pending ones
            # live in `pending_issues`, which this fixture does not seed.
            c.execute('INSERT INTO consumption ("Date","SAP_Code","Quantity",'
                      '"Site_ID","Issued_To","Issued_By","Work_Type","Tank_No",'
                      '"Lot_Number") VALUES (?,?,?,?,?,?,?,?,?)',
                      (_iso(day), sap, float(rng.choice([1, 2, 3, 5, 8, 12])),
                       SITE, PEOPLE[n % len(PEOPLE)], "worker",
                       rng.choice(WORK_TYPES), rng.choice(TANKS), lot))
        for n in range(12):
            c.execute('INSERT INTO returns ("Date","SAP_Code","Quantity",'
                      '"Site_ID","Reason","Remarks") VALUES (?,?,?,?,?,?)',
                      (_iso(today - _dt.timedelta(days=rng.randint(1, 60))),
                       movers[n][0], float(rng.choice([1, 2, 4])), SITE,
                       rng.choice(["Surplus to job", "Wrong item issued",
                                   "Damaged packaging"]), "synthetic"))

        # ── the one material the tutorials NAME ─────────────────────────────
        # ⚠️ EVERY OTHER ROW HERE IS RANDOM WITHIN A SEED, WHICH IS FINE FOR
        # SCENERY AND USELESS FOR A SCRIPT. The Return Stock tutorial types a
        # material into a picker that only offers things RECEIVED IN THE LAST 30
        # DAYS at this site, so it needs one row it can rely on by name — and
        # relying on `movers[7]` would make a narration line depend on a shuffle.
        # Dated three days before the anchor so it is inside the window with
        # room to spare.
        c.execute("INSERT OR IGNORE INTO inventory "
                  '("SAP_Code","Material_Code","Equipment_Description",'
                  '"Category","UOM","Minimum_Qty","Unit_Cost","Opening_Stock",'
                  '"Site_ID") VALUES (?,?,?,?,?,?,?,?,?)',
                  ("899001", "MAT-899001", "Tutorial Demo Gasket Set",
                   "R/L Consumables", "Set", 10, 42.5, 0, SITE))
        recent = today - _dt.timedelta(days=3)
        c.execute('INSERT INTO receipts ("Date","SAP_Code","Quantity","Site_ID",'
                  '"Supplier","Lot_Number","Expiry_Date","Received_by",'
                  '"DN_Number") VALUES (?,?,?,?,?,?,?,?,?)',
                  (_iso(recent), "899001", 60.0, SITE, VENDORS[0][1],
                   "LOT-TUT-001", _iso(today + _dt.timedelta(days=400)),
                   "worker", "DN-TUT-001"))
        c.execute('INSERT INTO lots ("Lot_Number","SAP_Code","Site_ID",'
                  '"Received_Date","Expiry_Date","Supplier","Status") '
                  "VALUES (?,?,?,?,?,?,?)",
                  ("LOT-TUT-001", "899001", SITE, _iso(recent),
                   _iso(today + _dt.timedelta(days=400)), VENDORS[0][1], "open"))

        # ── SME masters ─────────────────────────────────────────────────────
        # ⚠️ NO `SAP_Code` ON `sme_recipe` OR `sme_inventory_seed`. The FROZEN
        # legacy tables predate that column; rule 1's `(Material_Code, SAP_Code)`
        # component identity lives on the Postgres side, and `cutover_migrate`
        # already protects the blank-SAP recipe rows (handover rule 4). Writing
        # it here raises `no such column`.
        sme_mats = []
        for i in range(40):
            mc = f"GI-9{i:06d}"
            sme_mats.append((mc, f"Synthetic Lining Material {i + 1:02d}",
                             rng.choice(["Kg", "Each", "Roll"])))
        for mc, name, uom in sme_mats:
            c.execute('INSERT OR IGNORE INTO sme_inventory_seed ("Material_Code",'
                      '"Material_Name","UOM","Initial_Available_Qty",'
                      '"Initial_Ordered_Qty","Vendor","Nature") '
                      "VALUES (?,?,?,?,?,?,?)",
                      (mc, name, uom, float(rng.randint(0, 4000)),
                       float(rng.randint(0, 9000)),
                       rng.choice([v[1] for v in VENDORS]), "Consumable"))
        for code, name in LINING_SYSTEMS:
            for k in range(rng.randint(4, 7)):
                mc, mname, uom = sme_mats[(int(code) + k) % len(sme_mats)]
                c.execute('INSERT OR IGNORE INTO sme_recipe ("Lining_System_Code",'
                          '"Lining_System_Name","Material_Code","Material_Name",'
                          '"UOM","For_1_SQM","Nature") VALUES (?,?,?,?,?,?,?)',
                          (code, name, mc, mname, uom,
                           round(rng.uniform(0.2, 3.5), 2), "Consumable"))
        for i in range(24):
            code, _name = LINING_SYSTEMS[i % len(LINING_SYSTEMS)]
            area = float(rng.randint(40, 900))
            c.execute('INSERT OR IGNORE INTO sme_equipment ("Site_ID",'
                      '"Equipment_Tag_No","Name","Location","Type","Substrate",'
                      '"Lining_System_Code","Surface_Area_SQM",'
                      '"Equipment_Total_SQM","WBS_No") '
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (SITE, f"EQ-9{i:03d}", f"Synthetic {SME_TYPES[i % 7]} {i + 1}",
                       rng.choice(SME_LOCATIONS), SME_TYPES[i % 7], "Carbon Steel",
                       code, area, area, f"WBS-90{(i % 4) + 1:02d}"))

        # ── the SME tier fixture (rule 1c, teachable) ───────────────────────
        # ⚠️ THE SHAPE THAT PRODUCED THE 2026-08-03 BUG REPORT, on purpose. One
        # material with ZERO on the shelf and plenty on an open purchase order,
        # beside one that is genuinely stocked. 200 m² at 1.0 per m² each, so
        # "ready now" is exactly 0% and "with ordered" is exactly 100% —
        # unambiguous on camera, and the single clearest way to teach that a
        # purchase order is not readiness (rule 1b).
        c.execute('INSERT OR IGNORE INTO sme_inventory_seed ("Material_Code",'
                  '"Material_Name","UOM","Initial_Available_Qty",'
                  '"Initial_Ordered_Qty") VALUES (?,?,?,?,?)',
                  ("GI-9900001", "Synthetic Tier Powder", "Kg", 0.0, 5000.0))
        c.execute('INSERT OR IGNORE INTO sme_inventory_seed ("Material_Code",'
                  '"Material_Name","UOM","Initial_Available_Qty",'
                  '"Initial_Ordered_Qty") VALUES (?,?,?,?,?)',
                  ("GI-9900002", "Synthetic Tier Resin", "Kg", 800.0, 0.0))
        for mc, mname in (("GI-9900001", "Synthetic Tier Powder"),
                          ("GI-9900002", "Synthetic Tier Resin")):
            c.execute('INSERT OR IGNORE INTO sme_recipe ("Lining_System_Code",'
                      '"Lining_System_Name","Material_Code","Material_Name",'
                      '"UOM","For_1_SQM") VALUES (?,?,?,?,?,?)',
                      ("9101", "TUTORIAL TIER DEMO", mc, mname, "Kg", 1.0))
        c.execute('INSERT OR IGNORE INTO sme_equipment ("Site_ID",'
                  '"Equipment_Tag_No","Name","Location","Type",'
                  '"Lining_System_Code","Surface_Area_SQM","Equipment_Total_SQM") '
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (SITE, "EQ-TIER-DEMO", "Tier demonstration vessel", "TRAIN J",
                   "Vessel", "9101", 200.0, 200.0))

        conn.commit()
        database.log_audit_action("tutorial-fixture", "TUTORIAL_FIXTURE_BUILD",
                                  "inventory",
                                  f"synthetic dataset v{DATASET_VERSION}")
    finally:
        conn.close()

    return _self_check(database)


def _self_check(database) -> dict:
    """
    ⚠️ THE FIXTURE CHECKS ITSELF, because every way it can go wrong is SILENT
    downstream. An empty table does not fail a recording — it produces a
    tutorial of a blank dashboard, which is worse, because it looks finished.
    """
    tables = ("users", "inventory", "receipts", "consumption", "returns",
              "lots", "employees", "vendors", "warehouses", "wbs_master",
              "system_settings", "system_audit_log",
              "sme_equipment", "sme_recipe", "sme_inventory_seed")
    views = ("v_site_stock", "v_lot_balance", "v_expiring_stock")
    conn = database.get_connection()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in tables}
        empty = [t for t, n in counts.items() if not n]
        if empty:
            raise RuntimeError(
                f"tutorial fixture built with EMPTY table(s): {empty} — the "
                f"recording would show a blank panel and nothing would fail.")
        for v in views:
            n = conn.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
            counts[v] = n
            if not n:
                raise RuntimeError(f"tutorial fixture produced an EMPTY view {v}")
        # The panels the prototype recorded as empty. Asserted rather than
        # hoped for — they are the difference between footage and a screenshot
        # of nothing.
        costed = conn.execute(
            'SELECT COUNT(*) FROM inventory WHERE "Unit_Cost" > 0').fetchone()[0]
        uncosted = conn.execute(
            'SELECT COUNT(*) FROM inventory WHERE COALESCE("Unit_Cost",0) = 0'
        ).fetchone()[0]
        minimums = conn.execute(
            'SELECT COUNT(*) FROM inventory WHERE "Minimum_Qty" > 0').fetchone()[0]
        for label, n in (("priced items", costed), ("un-costed items", uncosted),
                         ("items with a minimum", minimums)):
            if not n:
                raise RuntimeError(f"tutorial fixture has no {label} — a "
                                   f"dashboard panel would render empty")
        named = conn.execute(
            'SELECT COUNT(*) FROM receipts WHERE "SAP_Code" = \'899001\''
        ).fetchone()[0]
        if not named:
            raise RuntimeError(
                "the named tutorial material 899001 has no receipt — the Return "
                "Stock tutorial's picker would be empty and the recording would "
                "still succeed")
        counts["priced"] = costed
        counts["uncosted"] = uncosted
        counts["minimums"] = minimums
    finally:
        conn.close()
    return counts


def collision_check(real_db: pathlib.Path) -> int:
    """
    Prove the synthetic names are disjoint from the real register.

    ⚠️ OPT-IN, AND NEVER A DEFAULT. This is the only function that opens the
    real database, it opens it `mode=ro&immutable=1`, and it reads nothing out
    of it — it asks a membership question and prints a count. A generator that
    reached for `gi_database.db` by default would be exactly the habit rule 15
    exists to break, and it could not run on a machine that does not have it.
    """
    if not real_db.exists():
        print(f"  ⚠️  {real_db} not present — collision check skipped")
        return 0
    conn = sqlite3.connect(f"file:{real_db}?mode=ro&immutable=1", uri=True)
    try:
        real_people = {(r[0] or "").strip().lower()
                       for r in conn.execute('SELECT "Name" FROM employees')}
        real_users = {(r[0] or "").strip().lower()
                      for r in conn.execute("SELECT username FROM users")}
        real_ids = {(r[0] or "").strip()
                    for r in conn.execute('SELECT "ID_Number" FROM employees')}
        real_saps = {(r[0] or "").strip()
                     for r in conn.execute('SELECT "SAP_Code" FROM inventory')}
    finally:
        conn.close()

    hits: list[str] = []
    for p in PEOPLE:
        if p.strip().lower() in real_people or p.strip().lower() in real_users:
            hits.append(f"person {p!r}")
    for n in range(len(PEOPLE)):
        if f"9000{n + 1:02d}" in real_ids:
            hits.append(f"employee id 9000{n + 1:02d}")
    for n in range(1, 311):
        if str(800000 + n) in real_saps:
            hits.append(f"SAP {800000 + n}")
    if hits:
        print("  ❌ COLLISION with the real register:")
        for h in hits[:20]:
            print(f"       {h}")
        return 1
    print(f"  ✅ no collisions: {len(PEOPLE)} names, {len(PEOPLE)} badge numbers "
          f"and 310 SAP codes are all absent from {real_db.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--today", default=None,
                    help=f"anchor date for every generated date (YYYY-MM-DD); "
                         f"defaults to the pinned ANCHOR ({ANCHOR.isoformat()}). "
                         f"Overriding it makes the render non-reproducible.")
    ap.add_argument("--collision-check", metavar="REAL_DB", default=None,
                    help="prove the synthetic names are absent from a real "
                         "database. Read-only, opt-in, never a default.")
    args = ap.parse_args(argv)

    if args.collision_check:
        return collision_check(pathlib.Path(args.collision_check).resolve())

    out = pathlib.Path(args.out).resolve()
    # ⚠️ REFUSE TO OVERWRITE THE REAL DATABASE. The entire reason this file
    # exists is that the live one must never be what a video is recorded from;
    # the mirror of that is a generator that can never be pointed at it.
    if out.name == "gi_database.db":
        print("❌ refusing to write to gi_database.db — that is the live legacy "
              "database, not a fixture", file=sys.stderr)
        return 2

    today = _dt.date.fromisoformat(args.today) if args.today else ANCHOR
    counts = build(out, today)
    print(f"▶ wrote {out} ({out.stat().st_size // 1024} KB) "
          f"— dataset v{DATASET_VERSION}, anchored {today.isoformat()}")
    print("  " + " · ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
