"""
database.py — General Industries Lightning Hub
================================================
Pure-Python data access layer. Zero Streamlit imports.
All functions accept an optional `conn` parameter so pytest can inject
an in-memory SQLite connection instead of touching gi_database.db.

Identity vs. Math principle is preserved:
  Current_Stock = Total_Received - Total_Consumed - Total_Returned
"""

import sqlite3
import pandas as pd
import datetime
import io
import re
import difflib

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
DB_FILE = "gi_database.db"

# Columns that are auto-managed and should not appear in dynamic forms
SYSTEM_COLS = {"id", "Timestamp", "created_at"}

# Columns that must be propagated to both pending_issues AND consumption
EXTENDED_ISSUE_COLS = ["Date", "Issued_By", "Issued_To", "Tank_No", "Serial_No", "PR_Number"]

# Stock-adjustment reason codes. Free-form 'other' falls back to notes.
# Order matters for the dropdown — most common first.
ADJUSTMENT_REASONS = {
    "cycle_count":        "🔄 Cycle count correction",
    "damaged":            "🔨 Damaged / unusable",
    "expired_disposal":   "🗑️ Expired — disposed",
    "miscount_in":        "➕ Miscount — found extra",
    "miscount_out":       "➖ Miscount — short",
    "lost":               "❓ Lost / unaccounted",
    "theft":              "🚨 Suspected theft",
    "return_to_supplier": "↩️ Returned to supplier",
    "other":              "❔ Other (see notes)",
}


# ---------------------------------------------------------------------------
# CONNECTION
# ---------------------------------------------------------------------------
def get_connection(db_file: str = None) -> sqlite3.Connection:
    """Return a SQLite connection. Pass db_file=':memory:' for in-memory testing."""
    target = db_file or DB_FILE
    conn = sqlite3.connect(target, check_same_thread=False)
    if target != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.DatabaseError:
            pass
    return conn


# ---------------------------------------------------------------------------
# SCHEMA INIT — accepts external conn for testability
# ---------------------------------------------------------------------------
def init_db(conn: sqlite3.Connection = None) -> None:
    """
    Initialise all tables and apply self-healing schema alignment.
    If `conn` is None, a new connection to DB_FILE is opened and closed here.
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    c = conn.cursor()

    # ── Core Operational Tables ──────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_issues (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            Date      TEXT,
            SAP_Code  TEXT,
            Quantity  REAL,
            Work_Type TEXT,
            Remarks   TEXT,
            Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_receipts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            Date        TEXT,
            SAP_Code    TEXT,
            Serial_No   TEXT,
            PR          TEXT,
            Quantity    REAL,
            Location    TEXT,
            Vehicle_No  TEXT,
            Driver_Name TEXT,
            DN_No       TEXT,
            Pallet_No   TEXT,
            Mob_From    TEXT,
            Prepared_by TEXT,
            Mob_To      TEXT,
            Received_by TEXT,
            DN_Copy     TEXT,
            Remarks     TEXT,
            Supplier    TEXT,
            PR_Number   TEXT,
            Expiry_Date TEXT,
            Timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            status      TEXT DEFAULT 'draft',
            Site_ID     TEXT DEFAULT 'HQ'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS returnable_items (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            material_name        TEXT NOT NULL,
            uom                  TEXT,
            qty                  REAL,
            borrower_name        TEXT,
            borrower_phone       TEXT,
            given_time           DATETIME DEFAULT CURRENT_TIMESTAMP,
            expected_return_time DATETIME,
            status               TEXT DEFAULT 'borrowed',
            Site_ID              TEXT DEFAULT 'HQ',
            whatsapp_alert_sent  INTEGER DEFAULT 0
        )
    """)
    # Self-healing: add whatsapp_alert_sent to tables that predate the column
    c.execute("PRAGMA table_info(returnable_items)")
    _ri_cols = {row[1] for row in c.fetchall()}
    if "whatsapp_alert_sent" not in _ri_cols:
        c.execute("ALTER TABLE returnable_items ADD COLUMN whatsapp_alert_sent INTEGER DEFAULT 0")

    c.execute("""
        CREATE TABLE IF NOT EXISTS consumption (
            Date      TEXT,
            SAP_Code  TEXT,
            Quantity  REAL,
            Work_Type TEXT,
            Remarks   TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            Date     TEXT,
            SAP_Code TEXT,
            Quantity REAL,
            Supplier TEXT,
            Remarks  TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS returns (
            Date     TEXT,
            SAP_Code TEXT,
            Quantity REAL,
            Reason   TEXT,
            Remarks  TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            SAP_Code              TEXT PRIMARY KEY,
            Equipment_Description TEXT,
            Material_Code         TEXT,
            UOM                   TEXT,
            Minimum_Qty           REAL DEFAULT 0
        )
    """)

    c.execute("CREATE TABLE IF NOT EXISTS system_settings (category TEXT, value TEXT)")

    # ── RBAC Users Table ──────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin','hod','supervisor','store_keeper')),
            Site_ID       TEXT DEFAULT 'HQ',
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Cross-Site Requests Table (Module 5) ──────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            requesting_site  TEXT    NOT NULL,
            target_site      TEXT    NOT NULL,
            SAP_Code         TEXT    NOT NULL,
            requested_qty    REAL    NOT NULL,
            available_qty    REAL    DEFAULT 0,
            suggested_qty    REAL    DEFAULT 0,
            status           TEXT    DEFAULT 'pending'
                                     CHECK(status IN ('pending','approved','rejected','fulfilled')),
            notes            TEXT,
            requested_by     TEXT,
            reviewed_by      TEXT,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── PR Master Table (Module 6) ────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS pr_master (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            PR_Number      TEXT    NOT NULL,
            SAP_Code       TEXT    NOT NULL,
            Requested_Qty  REAL    NOT NULL,
            Site_ID        TEXT    DEFAULT 'HQ',
            status         TEXT    DEFAULT 'open' CHECK(status IN ('open','closed')),
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Phase 7A: Registration & Audit Logs ──────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL,
            Site_ID       TEXT NOT NULL,
            status        TEXT DEFAULT 'pending',
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_audit_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP,
            username      TEXT NOT NULL,
            action_type   TEXT NOT NULL,
            target_table  TEXT,
            details       TEXT NOT NULL
        )
    """)

    # ── Phase 7C: WhatsApp Automation & Phonebooks ───────────────────────────
    c.execute("PRAGMA table_info(users)")
    usr_cols = {row[1] for row in c.fetchall()}
    if "Phone_Number" not in usr_cols:
        c.execute("ALTER TABLE users ADD COLUMN Phone_Number TEXT")
        
    c.execute("PRAGMA table_info(pending_users)")
    pnd_cols = {row[1] for row in c.fetchall()}
    if "Phone_Number" not in pnd_cols:
        c.execute("ALTER TABLE pending_users ADD COLUMN Phone_Number TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            sent_at DATETIME
        )
    """)

    # ── Self-Healing: Columns for PRs (Module 6) ───────────────────────
    c.execute("PRAGMA table_info(pr_master)")
    pr_cols = {row[1] for row in c.fetchall()}
    if "Material_Code" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN Material_Code TEXT")
    if "Material_Name" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN Material_Name TEXT")
    # workflow_state is orthogonal to `status` (which tracks open/closed
    # fulfillment). Drives the HOD Portal "Next →" progression button.
    if "workflow_state" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN workflow_state TEXT DEFAULT 'submitted'")
    if "UOM" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN UOM TEXT")
    if "Supplier" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN Supplier TEXT")
    if "Est_Cost_SAR" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN Est_Cost_SAR REAL")
    if "Notes" not in pr_cols:
        c.execute("ALTER TABLE pr_master ADD COLUMN Notes TEXT")

    # ── app_settings (HOD Notifications tab — alert thresholds) ─────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Seed the three threshold defaults if they don't exist yet.
    for _k, _v in [("low_stock_days", "5"),
                   ("burn_alert_days", "7"),
                   ("expiry_warn_days", "30")]:
        c.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── pending_receipts: workflow_state (allows 'rejected' for HOD UI) ─
    c.execute("PRAGMA table_info(pending_receipts)")
    pr_rcpt_cols = {row[1] for row in c.fetchall()}
    if "rejection_reason" not in pr_rcpt_cols:
        c.execute("ALTER TABLE pending_receipts ADD COLUMN rejection_reason TEXT")

    # ── Unit_Cost (standard-cost model, currency = SAR) ──────────────────
    # One cost per item on the inventory master is the simplest tractable
    # valuation model — call it "standard cost". Receipts also get a
    # nullable Unit_Cost so the SNAPSHOT at receive time is recorded,
    # giving us forward-compat for weighted-average cost later without
    # losing the historical data we'd need to compute it retroactively.
    c.execute("PRAGMA table_info(inventory)")
    inv_cols = {row[1] for row in c.fetchall()}
    if "Unit_Cost" not in inv_cols:
        c.execute("ALTER TABLE inventory ADD COLUMN Unit_Cost REAL DEFAULT 0")
    c.execute("PRAGMA table_info(receipts)")
    rcpt_cols = {row[1] for row in c.fetchall()}
    if "Unit_Cost" not in rcpt_cols:
        c.execute("ALTER TABLE receipts ADD COLUMN Unit_Cost REAL")

    # ── LOT MASTER (hard lot tracking) ───────────────────────────────────
    # The `lots` table stores metadata only (lot number, dates, supplier).
    # Quantities are DERIVED from receipts + consumption joined on
    # Lot_Number — see view v_lot_balance below. This keeps the identity
    # math (Received - Consumed = Remaining) exact, just like Current_Stock.
    c.execute("""
        CREATE TABLE IF NOT EXISTS lots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Lot_Number      TEXT    NOT NULL,
            SAP_Code        TEXT    NOT NULL,
            Site_ID         TEXT    DEFAULT 'HQ',
            Received_Date   TEXT    NOT NULL,
            Expiry_Date     TEXT,
            Supplier        TEXT,
            PR_Number       TEXT,
            Status          TEXT    DEFAULT 'open'
                            CHECK(Status IN ('open','exhausted','expired','disposed','quarantine')),
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Lot_Number, SAP_Code, Site_ID)
        )
    """)

    # Self-heal: add Lot_Number to every movement table so each
    # transaction can reference the master lot row it touched.
    for _tbl in ("receipts", "consumption", "pending_issues", "pending_receipts"):
        c.execute(f"PRAGMA table_info({_tbl})")
        _cols = {r[1] for r in c.fetchall()}
        if "Lot_Number" not in _cols:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN Lot_Number TEXT")

    # FEFO override reason. NULL = followed FEFO suggestion.
    # Non-null = store keeper deliberately picked a different lot AND
    # explained why. Persisted on the consumption ledger row so reports
    # and audits can filter for the exception.
    for _tbl in ("pending_issues", "consumption"):
        c.execute(f"PRAGMA table_info({_tbl})")
        _cols = {r[1] for r in c.fetchall()}
        if "FEFO_Override" not in _cols:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN FEFO_Override TEXT")

    # ── One-time backfill: legacy receipts → lot master ──────────────────
    # Any receipt with a populated Expiry_Date but no Lot_Number gets a
    # synthetic Lot_Number ('LOT-YYYYMMDD-SAP'). Then we INSERT OR IGNORE
    # into the lots table. Both steps are idempotent — re-running init_db
    # is a no-op once the data is in place.
    try:
        c.execute(
            "UPDATE receipts "
            "SET Lot_Number = 'LOT-' || REPLACE(Date,'-','') || '-' || SAP_Code "
            "WHERE (Lot_Number IS NULL OR Lot_Number = '') "
            "  AND Expiry_Date IS NOT NULL AND Expiry_Date <> ''"
        )
        c.execute(
            "INSERT OR IGNORE INTO lots "
            "(Lot_Number, SAP_Code, Site_ID, Received_Date, Expiry_Date, Supplier, PR_Number) "
            "SELECT DISTINCT "
            "  Lot_Number, SAP_Code, COALESCE(Site_ID,'HQ'), "
            "  Date, Expiry_Date, Supplier, PR_Number "
            "FROM receipts "
            "WHERE Lot_Number IS NOT NULL AND Lot_Number <> ''"
        )
    except sqlite3.Error:
        pass  # Preservation Rule — never block init on backfill.

    # ── stock_adjustments — reconciliation document type ─────────────────
    # Store Keeper counts physically, HOD approves.
    # On approval, a synthetic row posts into consumption (if shortfall)
    # or receipts (if surplus) so identity math stays exact:
    #   Current_Stock = Total_Received - Total_Consumed - Total_Returned
    # `posted_txn_ref` is set to 'C:<rowid>' or 'R:<rowid>' so audits can
    # trace adjustment → ledger row.
    c.execute("""
        CREATE TABLE IF NOT EXISTS stock_adjustments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID          TEXT    NOT NULL,
            SAP_Code         TEXT    NOT NULL,
            system_qty       REAL    NOT NULL,
            counted_qty      REAL    NOT NULL,
            variance         REAL    NOT NULL,
            reason_code      TEXT    NOT NULL,
            notes            TEXT,
            status           TEXT    DEFAULT 'pending_hod'
                             CHECK(status IN ('pending_hod','approved','rejected')),
            submitted_by     TEXT    NOT NULL,
            submitted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved_by      TEXT,
            approved_at      DATETIME,
            rejection_reason TEXT,
            posted_txn_ref   TEXT
        )
    """)

    # ── bug_reports (user feedback: bugs + feature requests) ────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('bug','feature')),
            page TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open' CHECK(status IN ('open','in_review','closed')),
            admin_response TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        )
    """)

    # ── report_schedules (UI-only schedules — no daemon, manual Run Now) ─
    c.execute("""
        CREATE TABLE IF NOT EXISTS report_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            report_type TEXT NOT NULL,
            frequency TEXT NOT NULL,
            recipients TEXT NOT NULL,
            format TEXT DEFAULT 'PDF',
            site_id TEXT,
            active INTEGER DEFAULT 1,
            last_run DATETIME,
            created_by TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── report_archive (disk-backed report history) ─────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS report_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            report_type TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            format TEXT NOT NULL,
            size_bytes INTEGER,
            file_path TEXT NOT NULL,
            site_id TEXT,
            date_from TEXT,
            date_to TEXT
        )
    """)

    # Default AI model for insights (swappable from Admin → WhatsApp Console)
    c.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('ai_insights_model', 'llama3.1')",
    )

    # ── Self-Healing Schema Alignment ────────────────────────────────────────
    c.execute("PRAGMA table_info(pending_issues)")
    pending_cols = {row[1] for row in c.fetchall()}

    c.execute("PRAGMA table_info(consumption)")
    cons_cols = {row[1] for row in c.fetchall()}

    for col in EXTENDED_ISSUE_COLS:
        if col not in pending_cols:
            c.execute(f"ALTER TABLE pending_issues ADD COLUMN {col} TEXT")
        if col not in cons_cols:
            c.execute(f"ALTER TABLE consumption ADD COLUMN {col} TEXT")

    # ── Self-Healing: Site_ID on all 5 operational tables (Module 5) ─────────
    # DEFAULT 'HQ' means every pre-existing row is silently assigned to HQ.
    # This is a zero-migration upgrade — no data is lost or changed.
    _SITE_ID_TABLES = ["users", "inventory", "pending_issues", "consumption", "receipts", "returns"]
    for _tbl in _SITE_ID_TABLES:
        c.execute(f"PRAGMA table_info({_tbl})")
        _existing = {row[1] for row in c.fetchall()}
        if "Site_ID" not in _existing:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN Site_ID TEXT DEFAULT 'HQ'")

    # ── Self-Healing: Expiry_Date (Module 6) ─────────────────────────────────
    # Safely adds Expiry_Date to inventory and receipts for Shelf-Life tracking
    for _tbl in ["inventory", "receipts"]:
        c.execute(f"PRAGMA table_info({_tbl})")
        _existing = {row[1] for row in c.fetchall()}
        if "Expiry_Date" not in _existing:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN Expiry_Date TEXT")

    # ── Self-Healing: Logistics Columns for receipts (Module 6) ───────────────
    c.execute("PRAGMA table_info(receipts)")
    rec_cols = {row[1] for row in c.fetchall()}
    for col in ["Supplier", "Expiry_Date", "PR_Number"]:
        if col not in rec_cols:
            c.execute(f"ALTER TABLE receipts ADD COLUMN {col} TEXT")

    # ── Self-Healing: Mirror all logistics columns into pending_receipts ───────
    c.execute("PRAGMA table_info(pending_receipts)")
    _prc_cols = {row[1] for row in c.fetchall()}
    for _col in ["Serial_No", "PR", "Location", "Vehicle_No", "Driver_Name",
                 "DN_No", "Pallet_No", "Mob_From", "Prepared_by", "Mob_To",
                 "Received_by", "DN_Copy", "Supplier", "PR_Number", "Expiry_Date"]:
        if _col not in _prc_cols:
            c.execute(f"ALTER TABLE pending_receipts ADD COLUMN {_col} TEXT")

    # ── Self-Healing: status column for draft/submit workflow ─────────────────
    c.execute("PRAGMA table_info(pending_issues)")
    _pi_cols = {row[1] for row in c.fetchall()}
    if "status" not in _pi_cols:
        c.execute("ALTER TABLE pending_issues ADD COLUMN status TEXT DEFAULT 'draft'")

    # ── PWA Tokens Table (Phase 4 — offline scan-and-stage backbone) ─────────
    # Stores opaque bearer tokens issued to floor users for the standalone
    # FastAPI/PWA service. Tokens are NOT JWTs — they're random URL-safe
    # strings looked up here on every request. last_used_at lets admins
    # revoke stale tokens. Created lazily so old DBs upgrade silently.
    c.execute("""
        CREATE TABLE IF NOT EXISTS pwa_tokens (
            token        TEXT PRIMARY KEY,
            username     TEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME
        )
    """)

    # ── Seed Default Work Types (idempotent) ──────────────────────────────────
    c.execute("SELECT count(*) FROM system_settings WHERE category='Work_Type'")
    if c.fetchone()[0] == 0:
        for wt in ["Maintenance", "New Project Area", "Fabrication", "Office"]:
            c.execute(
                "INSERT INTO system_settings (category, value) VALUES ('Work_Type', ?)",
                (wt,)
            )

    # ── Self-Healing: Upgrade the Role CHECK constraint ───────────────────────
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    table_sql = c.fetchone()[0]
    needs_rebuild = ("'hod'" not in table_sql.lower()) or ("'store_keeper'" not in table_sql.lower())
    if needs_rebuild:
        # Rebuild the table with the current constraint (adds hod + store_keeper, drops worker).
        c.execute("PRAGMA foreign_keys=off;")
        c.execute("ALTER TABLE users RENAME TO _users_old;")
        c.execute("""
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK(role IN ('admin','hod','supervisor','store_keeper')),
                Site_ID       TEXT DEFAULT 'HQ',
                Phone_Number  TEXT,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Copy rows, renaming legacy 'worker' role in-flight
        c.execute("""
            INSERT INTO users (id, username, password_hash, role, Site_ID, Phone_Number, created_at)
            SELECT id, username, password_hash,
                   CASE WHEN role = 'worker' THEN 'store_keeper' ELSE role END,
                   Site_ID,
                   Phone_Number,
                   created_at
            FROM _users_old
        """)
        c.execute("DROP TABLE _users_old;")
        c.execute("PRAGMA foreign_keys=on;")

    # ── Read-only Stock Views (Phase 3 — AI NL search backbone) ───────────────
    # These views encapsulate the EXACT Identity formula used by
    # load_live_inventory() so that any consumer (AI search, ad-hoc queries)
    # returns numbers that match the Live Dashboard to the unit.
    #
    #   Current_Stock = Σreceipts − Σconsumption − Σreturns   (the `returns`
    #   table — NOT returnable_items, which is a separate tool-loan ledger).
    #
    # LEFT JOIN + COALESCE guarantee items with NO receipts/consumption/returns
    # still appear with a correct stock (handles "non-available data"). They are
    # rebuilt every init so they stay in lockstep with self-healing schema drift.
    # Views are inherently read-only; creating them mutates schema, not data.
    _build_stock_views(c)

    conn.commit()
    if _owns_conn:
        conn.close()


def _build_stock_views(c: sqlite3.Cursor) -> None:
    """
    (Re)create the v_live_stock (global) and v_site_stock (per-site) views.

    Kept as a separate helper so it can be called from init_db and unit-tested
    in isolation. Mirrors load_live_inventory():
      - v_live_stock : one row per SAP_Code, summed across ALL sites
                       (matches load_live_inventory(site_id=None))
      - v_site_stock : one row per (SAP_Code, Site_ID)
                       (matches load_live_inventory(site_id=X) per site)
    SAP_Code is TRIM()'d on every side to mirror the dashboard's .str.strip().
    """
    try:
        c.execute("DROP VIEW IF EXISTS v_live_stock")
        c.execute("""
            CREATE VIEW v_live_stock AS
            SELECT
                TRIM(i.SAP_Code)               AS SAP_Code,
                i.Equipment_Description        AS Equipment_Description,
                i.Material_Code                AS Material_Code,
                i.UOM                          AS UOM,
                COALESCE(i.Minimum_Qty, 0)     AS Minimum_Qty,
                COALESCE(r.Total_Received, 0)  AS Total_Received,
                COALESCE(c.Total_Consumed, 0)  AS Total_Consumed,
                COALESCE(rt.Total_Returned, 0) AS Total_Returned,
                COALESCE(r.Total_Received, 0)
                  - COALESCE(c.Total_Consumed, 0)
                  - COALESCE(rt.Total_Returned, 0) AS Current_Stock
            FROM inventory i
            LEFT JOIN (
                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Received
                FROM receipts GROUP BY TRIM(SAP_Code)
            ) r  ON r.SAP_Code  = TRIM(i.SAP_Code)
            LEFT JOIN (
                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Consumed
                FROM consumption GROUP BY TRIM(SAP_Code)
            ) c  ON c.SAP_Code  = TRIM(i.SAP_Code)
            LEFT JOIN (
                SELECT TRIM(SAP_Code) AS SAP_Code, SUM(Quantity) AS Total_Returned
                FROM returns GROUP BY TRIM(SAP_Code)
            ) rt ON rt.SAP_Code = TRIM(i.SAP_Code)
        """)

        c.execute("DROP VIEW IF EXISTS v_site_stock")
        c.execute("""
            CREATE VIEW v_site_stock AS
            WITH activity AS (
                SELECT TRIM(SAP_Code) AS SAP_Code, COALESCE(Site_ID,'HQ') AS Site_ID,
                       SUM(Quantity) AS rec, 0 AS con, 0 AS ret
                FROM receipts    GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')
                UNION ALL
                SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'),
                       0, SUM(Quantity), 0
                FROM consumption GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')
                UNION ALL
                SELECT TRIM(SAP_Code), COALESCE(Site_ID,'HQ'),
                       0, 0, SUM(Quantity)
                FROM returns     GROUP BY TRIM(SAP_Code), COALESCE(Site_ID,'HQ')
            )
            SELECT
                a.SAP_Code                         AS SAP_Code,
                a.Site_ID                          AS Site_ID,
                i.Equipment_Description            AS Equipment_Description,
                i.Material_Code                    AS Material_Code,
                i.UOM                              AS UOM,
                COALESCE(i.Minimum_Qty, 0)         AS Minimum_Qty,
                SUM(a.rec)                         AS Total_Received,
                SUM(a.con)                         AS Total_Consumed,
                SUM(a.ret)                         AS Total_Returned,
                SUM(a.rec) - SUM(a.con) - SUM(a.ret) AS Current_Stock
            FROM activity a
            LEFT JOIN inventory i ON TRIM(i.SAP_Code) = a.SAP_Code
            GROUP BY a.SAP_Code, a.Site_ID
        """)

        # ── v_expiring_stock : receipt batches that carry an Expiry_Date ──────
        # Mirrors get_short_dated_stock() semantics (the Shelf-Life Alerts tab):
        #   Expired      → Expiry_Date < today
        #   Short-Dated  → today <= Expiry_Date <= today + 30 days
        #   Good         → later than 30 days
        # Exposes ALL dated batches (not just warnings) + Days_Until_Expiry so
        # the model can answer "expiring in 60 days" as easily as "expired".
        c.execute("DROP VIEW IF EXISTS v_expiring_stock")
        c.execute("""
            CREATE VIEW v_expiring_stock AS
            SELECT
                TRIM(r.SAP_Code)                   AS SAP_Code,
                i.Equipment_Description            AS Equipment_Description,
                i.UOM                              AS UOM,
                COALESCE(r.Site_ID, 'HQ')          AS Site_ID,
                r.Quantity                         AS Quantity,
                r.Supplier                         AS Supplier,
                r.PR_Number                        AS PR_Number,
                r.Expiry_Date                      AS Expiry_Date,
                CAST(julianday(date(r.Expiry_Date)) - julianday(date('now')) AS INTEGER)
                                                   AS Days_Until_Expiry,
                CASE
                    WHEN julianday(date(r.Expiry_Date)) < julianday(date('now'))
                        THEN 'Expired'
                    WHEN julianday(date(r.Expiry_Date))
                         <= julianday(date('now','+30 days'))
                        THEN 'Short-Dated'
                    ELSE 'Good'
                END                                AS Expiry_Status
            FROM receipts r
            LEFT JOIN inventory i ON TRIM(i.SAP_Code) = TRIM(r.SAP_Code)
            WHERE r.Expiry_Date IS NOT NULL
              AND r.Expiry_Date != ''
              AND date(r.Expiry_Date) IS NOT NULL
        """)

        # ── v_supplier_activity : per-supplier receipt rollup ────────────────
        # Powers supplier questions ("who supplies the most", "ACME's items").
        # One row per (Supplier, Site_ID). Blank/NULL suppliers excluded.
        c.execute("DROP VIEW IF EXISTS v_supplier_activity")
        c.execute("""
            CREATE VIEW v_supplier_activity AS
            SELECT
                TRIM(r.Supplier)                   AS Supplier,
                COALESCE(r.Site_ID, 'HQ')          AS Site_ID,
                COUNT(*)                           AS Receipt_Count,
                COUNT(DISTINCT TRIM(r.SAP_Code))   AS Distinct_Items,
                SUM(r.Quantity)                    AS Total_Received,
                MIN(r.Date)                        AS First_Receipt_Date,
                MAX(r.Date)                        AS Last_Receipt_Date
            FROM receipts r
            WHERE r.Supplier IS NOT NULL AND TRIM(r.Supplier) != ''
            GROUP BY TRIM(r.Supplier), COALESCE(r.Site_ID, 'HQ')
        """)

        # v_lot_balance — per-lot live balance (Received - Consumed).
        # Identity math, same shape as v_site_stock. Returns hard lots only;
        # legacy un-lotted receipts are handled by the get_fefo_lots
        # fallback path, not here.
        c.execute("DROP VIEW IF EXISTS v_lot_balance")
        c.execute("""
            CREATE VIEW v_lot_balance AS
            SELECT
                l.Lot_Number,
                l.SAP_Code,
                l.Site_ID,
                l.Received_Date,
                l.Expiry_Date,
                l.Supplier,
                l.PR_Number,
                l.Status,
                COALESCE((
                    SELECT SUM(r.Quantity) FROM receipts r
                    WHERE r.Lot_Number = l.Lot_Number
                      AND r.SAP_Code   = l.SAP_Code
                      AND COALESCE(r.Site_ID,'HQ') = l.Site_ID
                ), 0) AS Received_Qty,
                COALESCE((
                    SELECT SUM(c.Quantity) FROM consumption c
                    WHERE c.Lot_Number = l.Lot_Number
                      AND c.SAP_Code   = l.SAP_Code
                      AND COALESCE(c.Site_ID,'HQ') = l.Site_ID
                ), 0) AS Consumed_Qty,
                COALESCE((
                    SELECT SUM(r.Quantity) FROM receipts r
                    WHERE r.Lot_Number = l.Lot_Number
                      AND r.SAP_Code   = l.SAP_Code
                      AND COALESCE(r.Site_ID,'HQ') = l.Site_ID
                ), 0) - COALESCE((
                    SELECT SUM(c.Quantity) FROM consumption c
                    WHERE c.Lot_Number = l.Lot_Number
                      AND c.SAP_Code   = l.SAP_Code
                      AND COALESCE(c.Site_ID,'HQ') = l.Site_ID
                ), 0) AS Remaining_Qty
            FROM lots l
        """)
    except sqlite3.Error:
        # Never let view creation break app startup (Preservation Rule).
        # If a base table is unexpectedly missing, the app still runs; only
        # the AI search loses its view backbone until the next healthy init.
        pass


# ---------------------------------------------------------------------------
# QUERY HELPERS
# ---------------------------------------------------------------------------
def get_work_types(conn: sqlite3.Connection = None) -> list[str]:
    """Return list of Work_Type dropdown values from system_settings."""
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    result = pd.read_sql(
        "SELECT value FROM system_settings WHERE category='Work_Type'", conn
    )["value"].tolist()

    if _owns_conn:
        conn.close()
    return result


def get_table_sum(
    conn: sqlite3.Connection, table_name: str, sum_col_name: str
) -> pd.DataFrame:
    """
    Group a transaction table by SAP_Code and sum its Quantity column.
    Returns DataFrame with columns [SAP_Code, sum_col_name].
    Returns empty DataFrame (not an error) if table is empty or has no qty column.
    """
    try:
        df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
        if df.empty or "SAP_Code" not in df.columns:
            return pd.DataFrame(columns=["SAP_Code", sum_col_name])

        qty_col = next(
            (col for col in df.columns if "qty" in col.lower() or "quantity" in col.lower()),
            None,
        )
        if not qty_col:
            return pd.DataFrame(columns=["SAP_Code", sum_col_name])

        df[qty_col] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0)
        df["SAP_Code"] = df["SAP_Code"].astype(str).str.strip()

        result = df.groupby("SAP_Code")[qty_col].sum().reset_index()
        result.rename(columns={qty_col: sum_col_name}, inplace=True)
        return result
    except Exception:
        return pd.DataFrame(columns=["SAP_Code", sum_col_name])


def load_live_inventory(
    conn: sqlite3.Connection = None,
    site_id: str = None,
) -> pd.DataFrame:
    """
    Computes live stock levels using the Identity vs. Math formula:
        Current_Stock = Total_Received - Total_Consumed - Total_Returned

    site_id=None  → global view (all sites combined — Admin use)
    site_id='HQ'  → per-site view (only Site_ID='HQ' rows — HOD/Supervisor use)

    The per-site filter is applied to inventory AND all three transaction tables
    so that each site's Current_Stock is fully independent.
    Returns a merged DataFrame with one row per inventory item (for that site).
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    try:
        # FIX: The Material Catalog is global (SAP_Code is Primary Key).
        # We load all items, but the math below will remain site-specific.
        inv_df = pd.read_sql(
            "SELECT SAP_Code, Material_Code, Equipment_Description, UOM, Minimum_Qty "
            "FROM inventory",
            conn,
        )
    except Exception:
        inv_df = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn,
        )
        inv_df["Material_Code"] = ""
        inv_df["Minimum_Qty"] = 0

    inv_df["SAP_Code"] = inv_df["SAP_Code"].astype(str).str.strip()

    # Build site-aware sums using direct SQL to avoid loading all rows into pandas
    def _site_sum(table: str, alias: str) -> pd.DataFrame:
        if site_id:
            return pd.read_sql(
                f"SELECT SAP_Code, SUM(Quantity) AS {alias} "
                f"FROM {table} WHERE Site_ID = ? GROUP BY SAP_Code",
                conn, params=(site_id,),
            )
        return pd.read_sql(
            f"SELECT SAP_Code, SUM(Quantity) AS {alias} FROM {table} GROUP BY SAP_Code",
            conn,
        )

    rec_df  = _site_sum("receipts",    "Total_Received")
    cons_df = _site_sum("consumption", "Total_Consumed")
    ret_df  = _site_sum("returns",     "Total_Returned")

    if _owns_conn:
        conn.close()

    live_df = pd.merge(inv_df, rec_df,  on="SAP_Code", how="left")
    live_df = pd.merge(live_df, cons_df, on="SAP_Code", how="left")
    live_df = pd.merge(live_df, ret_df,  on="SAP_Code", how="left")

    numeric_cols = ["Total_Received", "Total_Consumed", "Total_Returned", "Minimum_Qty"]
    for col in numeric_cols:
        if col in live_df.columns:
            live_df[col] = pd.to_numeric(live_df[col], errors="coerce").fillna(0)

    live_df["Current_Stock"] = (
        live_df["Total_Received"]
        - live_df["Total_Consumed"]
        - live_df["Total_Returned"]
    )
    return live_df


def commit_eod(conn: sqlite3.Connection = None) -> int:
    """
    Atomically commits all rows in pending_issues to the consumption table,
    then clears the staging queue.

    - Auto-syncs any extra columns from pending_issues into consumption first.
    - Returns the number of rows committed (0 if queue was empty).
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    c = conn.cursor()
    pending_df = pd.read_sql("SELECT * FROM pending_issues WHERE COALESCE(status,'pending_hod') = 'pending_hod'", conn)

    if pending_df.empty:
        if _owns_conn:
            conn.close()
        return 0

    cols_to_commit = [col for col in pending_df.columns if col not in SYSTEM_COLS]

    # ── Auto-sync schema: add missing columns to consumption ─────────────────
    c.execute("PRAGMA table_info(consumption)")
    cons_cols = {r[1] for r in c.fetchall()}
    for col in cols_to_commit:
        if col not in cons_cols:
            try:
                c.execute(f"ALTER TABLE consumption ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists in a concurrent call — safe to ignore

    col_names    = ", ".join(cols_to_commit)
    placeholders = ", ".join(["?"] * len(cols_to_commit))

    rows_committed = 0
    for _, row in pending_df.iterrows():
        clean_values = []
        for col in cols_to_commit:
            val = row[col]
            # Guard against Streamlit accidentally passing list objects
            if isinstance(val, (list, tuple, set)):
                val = ", ".join(map(str, val))
            clean_values.append(val)

        c.execute(
            f"INSERT INTO consumption ({col_names}) VALUES ({placeholders})",
            clean_values,
        )
        rows_committed += 1

    c.execute("DELETE FROM pending_issues WHERE COALESCE(status,'pending_hod') = 'pending_hod'")
    conn.commit()

    if _owns_conn:
        conn.close()

    return rows_committed


def get_low_stock_items(conn: sqlite3.Connection = None, site_id: str = None) -> pd.DataFrame:
    """
    Returns items where Current_Stock < Minimum_Qty.
    Adds a 'Shortage' column showing the gap.
    site_id=None → all sites (admin); site_id='X' → that site only.
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    live_df = load_live_inventory(conn, site_id=site_id)

    if _owns_conn:
        conn.close()

    if live_df.empty or "Minimum_Qty" not in live_df.columns:
        return pd.DataFrame()

    low = live_df[live_df["Current_Stock"] < live_df["Minimum_Qty"]].copy()
    low["Shortage"] = low["Minimum_Qty"] - low["Current_Stock"]
    return low.reset_index(drop=True)


def get_burn_rate_and_forecast(
    conn: sqlite3.Connection = None,
    site_id: str = None,
    lookback_days: int = 30,
) -> pd.DataFrame:
    """
    Calculates Daily_Burn_Rate and Days_Remaining per material.

    Queries consumption over the last `lookback_days` days, computes
    Daily_Burn_Rate = total_consumed / lookback_days, then merges with
    live inventory to derive Days_Remaining = Current_Stock / Daily_Burn_Rate.

    Items with no recent consumption are excluded (inner join).
    Burn_Alert = True when Days_Remaining < 7.
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    try:
        cutoff = (
            datetime.date.today() - datetime.timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        if site_id:
            cons_df = pd.read_sql(
                "SELECT SAP_Code, SUM(Quantity) AS Total_Consumed_30d "
                "FROM consumption WHERE Date >= ? AND COALESCE(Site_ID,'HQ') = ? "
                "GROUP BY SAP_Code",
                conn, params=(cutoff, site_id),
            )
        else:
            cons_df = pd.read_sql(
                "SELECT SAP_Code, SUM(Quantity) AS Total_Consumed_30d "
                "FROM consumption WHERE Date >= ? GROUP BY SAP_Code",
                conn, params=(cutoff,),
            )

        if cons_df.empty:
            return pd.DataFrame(columns=[
                "SAP_Code", "Equipment_Description", "UOM",
                "Current_Stock", "Daily_Burn_Rate", "Days_Remaining", "Burn_Alert",
            ])

        live_df = load_live_inventory(conn, site_id=site_id)

    finally:
        if _owns_conn:
            conn.close()

    if live_df.empty:
        return pd.DataFrame(columns=[
            "SAP_Code", "Equipment_Description", "UOM",
            "Current_Stock", "Daily_Burn_Rate", "Days_Remaining", "Burn_Alert",
        ])

    merged = pd.merge(
        live_df[["SAP_Code", "Equipment_Description", "UOM", "Current_Stock"]],
        cons_df,
        on="SAP_Code",
        how="inner",
    )
    merged["Daily_Burn_Rate"] = (
        pd.to_numeric(merged["Total_Consumed_30d"], errors="coerce").fillna(0)
        / lookback_days
    ).round(3)
    merged["Days_Remaining"] = merged.apply(
        lambda r: round(r["Current_Stock"] / r["Daily_Burn_Rate"], 1)
        if r["Daily_Burn_Rate"] > 0 else None,
        axis=1,
    )
    merged["Burn_Alert"] = merged["Days_Remaining"].apply(
        lambda d: d is not None and d < 7
    )
    return merged[[
        "SAP_Code", "Equipment_Description", "UOM",
        "Current_Stock", "Daily_Burn_Rate", "Days_Remaining", "Burn_Alert",
    ]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# MODULE 5 — MULTI-SITE QUERY FUNCTIONS
# ---------------------------------------------------------------------------

def get_sites(conn: sqlite3.Connection = None) -> list[str]:
    """
    Returns the list of active Site IDs from system_settings.
    These are the Admin-managed canonical site names.
    Falls back to distinct values from users.Site_ID if none are configured.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    sites = pd.read_sql(
        "SELECT value FROM system_settings WHERE category='Site' ORDER BY value",
        conn,
    )["value"].tolist()

    if not sites:
        # Fallback: derive from registered users
        sites = pd.read_sql(
            "SELECT DISTINCT COALESCE(Site_ID,'HQ') AS Site_ID FROM users ORDER BY Site_ID",
            conn,
        )["Site_ID"].tolist()

    if _owns:
        conn.close()
    return sites or ["HQ"]


def get_pending_issues_for_site(
    conn: sqlite3.Connection = None,
    site_id: str = "HQ",
) -> pd.DataFrame:
    """
    Returns pending_issues rows for a specific site only.
    Used by HOD and workers — never exposes other sites' staging queues.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    df = pd.read_sql(
        "SELECT * FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ? AND COALESCE(status,'pending_hod') = 'pending_hod'",
        conn, params=(site_id,),
    )
    if _owns:
        conn.close()
    return df


def get_pending_requests(
    conn: sqlite3.Connection = None,
    site_id: str = None,
    status: str = None,
) -> pd.DataFrame:
    """
    Returns rows from the `requests` table.
    site_id=None  → all sites (Admin global view)
    site_id='X'   → only requests where requesting_site='X' OR target_site='X'
    status='pending' → filter by FSM state
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    clauses, params = [], []
    if site_id:
        clauses.append("(requesting_site = ? OR target_site = ?)")
        params += [site_id, site_id]
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    df = pd.read_sql(
        f"SELECT * FROM requests {where} ORDER BY created_at DESC",
        conn, params=params,
    )
    if _owns:
        conn.close()
    return df


def create_request(
    conn: sqlite3.Connection = None,
    requesting_site: str = "",
    target_site: str = "",
    sap_code: str = "",
    requested_qty: float = 0,
    available_qty: float = 0,
    suggested_qty: float = 0,
    notes: str = "",
    requested_by: str = "",
) -> int:
    """
    Inserts a new cross-site material request.
    Returns the new row's id (integer).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    c = conn.cursor()
    c.execute(
        """
        INSERT INTO requests
            (requesting_site, target_site, SAP_Code,
             requested_qty, available_qty, suggested_qty,
             notes, requested_by, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (requesting_site, target_site, sap_code,
         requested_qty, available_qty, suggested_qty,
         notes, requested_by),
    )
    conn.commit()
    new_id = c.lastrowid
    if _owns:
        conn.close()
    return new_id


def update_request_status(
    conn: sqlite3.Connection = None,
    req_id: int = 0,
    new_status: str = "approved",
    reviewed_by: str = "",
    notes: str = None,
) -> bool:
    """
    Transitions a request to a new FSM status.
    reviewed_by is set for admin actions (approve/reject).
    Returns True if a row was actually updated.
    """
    from config import REQUEST_STATUSES
    if new_status not in REQUEST_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {REQUEST_STATUSES}")

    _owns = conn is None
    if _owns:
        conn = get_connection()

    c = conn.cursor()
    if notes is not None:
        c.execute(
            "UPDATE requests SET status=?, reviewed_by=?, notes=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_status, reviewed_by, notes, req_id),
        )
    else:
        c.execute(
            "UPDATE requests SET status=?, reviewed_by=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_status, reviewed_by, req_id),
        )
    conn.commit()
    updated = c.rowcount > 0
    if _owns:
        conn.close()
    return updated


# ---------------------------------------------------------------------------
# MODULE 6 — PR TRACKING & SHELF-LIFE LOGIC
# ---------------------------------------------------------------------------

def get_pr_balance(conn: sqlite3.Connection = None, pr_number: str = "") -> dict:
    """
    Calculates the remaining balance for a specific Purchase Request.
    Returns: {"requested": X, "received": Y, "balance": Z}
    """
    if not pr_number:
        return {"requested": 0.0, "received": 0.0, "balance": 0.0}

    _owns = conn is None
    if _owns:
        conn = get_connection()
        
    try:
        # 1. Get Requested Qty (Use COALESCE to safely handle blanks/zeros)
        req_df = pd.read_sql(
            "SELECT COALESCE(SUM(Requested_Qty), 0) as req_qty FROM pr_master WHERE PR_Number = ?",
            conn, params=(pr_number,)
        )
        req_qty = float(req_df["req_qty"].iloc[0]) if not req_df.empty else 0.0

        # 2. Get Total Received for this PR 
        rec_df = pd.read_sql(
            "SELECT COALESCE(SUM(Quantity), 0) as rec_qty FROM receipts WHERE PR_Number = ?",
            conn, params=(pr_number,)
        )
        rec_qty = float(rec_df["rec_qty"].iloc[0]) if not rec_df.empty else 0.0

        balance = req_qty - rec_qty

        return {
            "requested": req_qty,
            "received": rec_qty,
            "balance": max(0.0, balance) # Math floor protects against negatives if over-delivered
        }
    finally:
        if _owns:
            conn.close()


def get_item_snapshot(
    sap_code: str,
    site_id: str = None,
    lookback_days: int = 30,
    conn: sqlite3.Connection = None,
) -> dict:
    """
    Per-item inspection snapshot used by the Scan-to-Inspect panel.

    Returns a dict:
      found              : bool — False if SAP_Code is not in inventory
      sap_code           : str
      description        : str
      uom                : str
      material_code      : str
      minimum_qty        : float
      current_stock      : float  (Identity formula, scoped by site_id if given)
      cons_df            : DataFrame of consumption rows in last N days
      cons_total         : float — sum of cons_df.Quantity
      rcpt_df            : DataFrame of receipt rows in last N days
      rcpt_total         : float — sum of rcpt_df.Quantity
      last_receipt_date  : str | None

    site_id=None → global (admin view)
    site_id='HQ' → filter inventory math AND last-30-day rows to that site.

    Honours the project's Identity formula
        Current_Stock = Total_Received - Total_Consumed - Total_Returned
    using the SAME SQL aggregations the rest of database.py uses — no
    new math is introduced here.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        # ── 1. Inventory record ──────────────────────────────────────────
        inv_row = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, Material_Code, UOM, Minimum_Qty "
            "FROM inventory WHERE SAP_Code = ? LIMIT 1",
            conn, params=(sap_code,),
        )
        if inv_row.empty:
            return {
                "found": False, "sap_code": sap_code,
                "description": "", "uom": "", "material_code": "",
                "minimum_qty": 0.0, "current_stock": 0.0,
                "cons_df": pd.DataFrame(), "cons_total": 0.0,
                "rcpt_df": pd.DataFrame(), "rcpt_total": 0.0,
                "last_receipt_date": None,
            }
        row0 = inv_row.iloc[0]

        # ── 2. Current_Stock identity (per-site or global) ──────────────
        # We follow the SAME aggregation pattern as load_live_inventory:
        #   Stock = Σ receipts.Quantity − Σ consumption.Quantity − Σ returns
        # but scoped to one SAP_Code so it's a cheap query.
        if site_id:
            rcpt_sum = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM receipts "
                "WHERE SAP_Code = ? AND COALESCE(Site_ID,'HQ') = ?",
                (sap_code, site_id),
            ).fetchone()[0] or 0.0
            cons_sum = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM consumption "
                "WHERE SAP_Code = ? AND COALESCE(Site_ID,'HQ') = ?",
                (sap_code, site_id),
            ).fetchone()[0] or 0.0
            try:
                ret_sum = conn.execute(
                    "SELECT COALESCE(SUM(qty),0) FROM returnable_items "
                    "WHERE material_name = ? AND COALESCE(Site_ID,'HQ') = ? "
                    "AND status = 'borrowed'",
                    (row0["Equipment_Description"], site_id),
                ).fetchone()[0] or 0.0
            except sqlite3.OperationalError:
                ret_sum = 0.0
        else:
            rcpt_sum = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM receipts WHERE SAP_Code = ?",
                (sap_code,),
            ).fetchone()[0] or 0.0
            cons_sum = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM consumption WHERE SAP_Code = ?",
                (sap_code,),
            ).fetchone()[0] or 0.0
            try:
                ret_sum = conn.execute(
                    "SELECT COALESCE(SUM(qty),0) FROM returnable_items "
                    "WHERE material_name = ? AND status = 'borrowed'",
                    (row0["Equipment_Description"],),
                ).fetchone()[0] or 0.0
            except sqlite3.OperationalError:
                ret_sum = 0.0

        current_stock = float(rcpt_sum) - float(cons_sum) - float(ret_sum)

        # ── 3. Last N days of consumption ───────────────────────────────
        cutoff = (
            datetime.date.today() - datetime.timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        if site_id:
            cons_df = pd.read_sql(
                "SELECT Date, Quantity, Work_Type, Issued_By, Site_ID "
                "FROM consumption "
                "WHERE SAP_Code = ? AND Date >= ? "
                "AND COALESCE(Site_ID,'HQ') = ? "
                "ORDER BY Date DESC LIMIT 200",
                conn, params=(sap_code, cutoff, site_id),
            )
            rcpt_df = pd.read_sql(
                "SELECT Date, Quantity, Supplier, Expiry_Date, PR_Number, Site_ID "
                "FROM receipts "
                "WHERE SAP_Code = ? AND Date >= ? "
                "AND COALESCE(Site_ID,'HQ') = ? "
                "ORDER BY Date DESC LIMIT 200",
                conn, params=(sap_code, cutoff, site_id),
            )
        else:
            cons_df = pd.read_sql(
                "SELECT Date, Quantity, Work_Type, Issued_By, Site_ID "
                "FROM consumption "
                "WHERE SAP_Code = ? AND Date >= ? "
                "ORDER BY Date DESC LIMIT 200",
                conn, params=(sap_code, cutoff),
            )
            rcpt_df = pd.read_sql(
                "SELECT Date, Quantity, Supplier, Expiry_Date, PR_Number, Site_ID "
                "FROM receipts "
                "WHERE SAP_Code = ? AND Date >= ? "
                "ORDER BY Date DESC LIMIT 200",
                conn, params=(sap_code, cutoff),
            )

        cons_total = float(cons_df["Quantity"].sum()) if not cons_df.empty else 0.0
        rcpt_total = float(rcpt_df["Quantity"].sum()) if not rcpt_df.empty else 0.0
        last_receipt_date = (
            rcpt_df.iloc[0]["Date"] if not rcpt_df.empty else None
        )

        return {
            "found": True,
            "sap_code": sap_code,
            "description":   row0["Equipment_Description"],
            "uom":           row0.get("UOM", "") or "",
            "material_code": row0.get("Material_Code", "") or "",
            "minimum_qty":   float(row0.get("Minimum_Qty") or 0.0),
            "current_stock": current_stock,
            "cons_df": cons_df,
            "cons_total": cons_total,
            "rcpt_df": rcpt_df,
            "rcpt_total": rcpt_total,
            "last_receipt_date": last_receipt_date,
        }
    finally:
        if _owns:
            conn.close()


def get_user_last_entry_defaults(
    username: str,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> dict:
    """
    Return a dict of likely default form values based on the user's most
    recent consumption or pending_issues row at this site.

    Keys returned (any of them may be empty strings if not found):
      Issued_By, Issued_To, Tank_No, Work_Type, PR_Number

    Reads only — purely a UX accelerator. Looks at commits first
    (consumption), then falls back to the current draft (pending_issues)
    so a fresh user mid-shift still gets useful defaults. Never raises;
    on any error returns empty defaults.
    """
    empty = {
        "Issued_By": "", "Issued_To": "", "Tank_No": "",
        "Work_Type": "", "PR_Number": "",
    }
    if not username:
        return empty

    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        # Prefer most recent committed consumption by this user at this site.
        if site_id:
            row = conn.execute(
                """
                SELECT Issued_By, Issued_To, Tank_No, Work_Type, PR_Number
                FROM consumption
                WHERE COALESCE(Site_ID,'HQ') = ?
                  AND (Issued_By = ? OR Issued_By IS NULL OR Issued_By = '')
                ORDER BY id DESC LIMIT 1
                """,
                (site_id, username),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT Issued_By, Issued_To, Tank_No, Work_Type, PR_Number
                FROM consumption
                WHERE (Issued_By = ? OR Issued_By IS NULL OR Issued_By = '')
                ORDER BY id DESC LIMIT 1
                """,
                (username,),
            ).fetchone()

        if not row:
            # Fall back to most recent draft row.
            if site_id:
                row = conn.execute(
                    """
                    SELECT Issued_By, Issued_To, Tank_No, Work_Type, PR_Number
                    FROM pending_issues
                    WHERE COALESCE(Site_ID,'HQ') = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (site_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT Issued_By, Issued_To, Tank_No, Work_Type, PR_Number "
                    "FROM pending_issues ORDER BY id DESC LIMIT 1"
                ).fetchone()

        if not row:
            return empty

        return {
            "Issued_By": (row[0] or username) if row[0] is not None else username,
            "Issued_To": row[1] or "",
            "Tank_No":   row[2] or "",
            "Work_Type": row[3] or "",
            "PR_Number": row[4] or "",
        }
    except sqlite3.OperationalError:
        return empty
    finally:
        if _owns:
            conn.close()


def get_fefo_lots(
    sap_code: str,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """
    First-Expiry-First-Out lot breakdown for a single SAP_Code.

    Returns the per-receipt-batch view of a material so the user can be told
    WHICH lot to pull from first:
      SAP_Code, Equipment_Description, Site_ID, Lot_Date, Expiry_Date,
      Days_Until_Expiry, Received_Qty, Allocated_Qty, Remaining_Qty,
      Expiry_Status, Supplier, PR_Number

    Allocation rule (FEFO):
      Total site consumption for this SAP_Code is allocated against lots in
      Expiry_Date ASC order (NULL expiries last, then Date ASC). Each lot's
      Remaining_Qty = max(0, Received_Qty − share of total consumption it
      absorbed). Lots without any expiry date still appear at the bottom so
      non-perishable items are not lost.

    site_id=None → use ALL sites for the math (admin / global view)
    site_id='HQ' → restrict receipts AND consumption to Site_ID='HQ'

    READ-ONLY. Does not write or delete anything. SAP_Code mismatch returns
    an empty DataFrame, never raises.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        # ── 0. PREFERRED PATH: hard lots from the lots master ─────────────
        # If any lots rows exist for (SAP, Site), use them — Remaining_Qty
        # is derived from receipts ⋂ consumption joined on Lot_Number, so
        # the math is exact. We only fall back to date-allocation when
        # NO hard lots exist (legacy un-lotted data).
        try:
            hard = pd.read_sql(
                "SELECT Lot_Number, SAP_Code, Site_ID, "
                "       Received_Date AS Lot_Date, Expiry_Date, "
                "       Supplier, PR_Number, Status, "
                "       Received_Qty, Consumed_Qty AS Allocated_Qty, "
                "       Remaining_Qty "
                "FROM v_lot_balance "
                "WHERE TRIM(SAP_Code) = ?"
                + (" AND Site_ID = ?" if site_id else ""),
                conn,
                params=((sap_code.strip(), site_id) if site_id
                        else (sap_code.strip(),)),
            )
        except Exception:
            hard = pd.DataFrame()

        if hard is not None and not hard.empty:
            # Decorate to match the legacy output contract (Equipment_Description,
            # Days_Until_Expiry, Expiry_Status) and sort FEFO.
            desc_row = conn.execute(
                "SELECT Equipment_Description FROM inventory WHERE SAP_Code = ?",
                (sap_code.strip(),),
            ).fetchone()
            hard["Equipment_Description"] = (
                desc_row[0] if desc_row else "(unknown)"
            )
            today = pd.Timestamp.today().normalize()
            exp = pd.to_datetime(hard["Expiry_Date"], errors="coerce")
            hard["Days_Until_Expiry"] = (exp - today).dt.days

            def _bucket(d):
                if pd.isna(d):           return "No Expiry"
                d = int(d)
                if d < 0:                return "Expired"
                if d <= 90:              return "Short-Dated"
                return "Good"
            hard["Expiry_Status"] = hard["Days_Until_Expiry"].apply(_bucket)

            # FEFO sort: earliest expiry first; null expiries last.
            hard = hard.assign(
                _null_exp=hard["Expiry_Date"].isna() | (hard["Expiry_Date"] == "")
            ).sort_values(
                by=["_null_exp", "Expiry_Date", "Lot_Date"],
                ascending=[True, True, True],
            ).drop(columns=["_null_exp"]).reset_index(drop=True)

            # Final column order matches the legacy output (+Lot_Number).
            cols = [c for c in [
                "Lot_Number", "SAP_Code", "Equipment_Description", "Site_ID",
                "Lot_Date", "Expiry_Date", "Days_Until_Expiry",
                "Received_Qty", "Allocated_Qty", "Remaining_Qty",
                "Expiry_Status", "Supplier", "PR_Number", "Status",
            ] if c in hard.columns]
            return hard[cols]

        # ── 1. LEGACY PATH: all receipt lots, ordered FEFO ────────────────
        # Reached only when no hard lots exist for this (SAP, Site) yet.
        if site_id:
            lots = pd.read_sql(
                """
                SELECT
                    TRIM(r.SAP_Code)             AS SAP_Code,
                    COALESCE(r.Site_ID,'HQ')     AS Site_ID,
                    r.Date                       AS Lot_Date,
                    r.Expiry_Date                AS Expiry_Date,
                    r.Quantity                   AS Received_Qty,
                    r.Supplier                   AS Supplier,
                    r.PR_Number                  AS PR_Number
                FROM receipts r
                WHERE TRIM(r.SAP_Code) = ?
                  AND COALESCE(r.Site_ID,'HQ') = ?
                """,
                conn, params=(sap_code.strip(), site_id),
            )
            cons_total = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM consumption "
                "WHERE TRIM(SAP_Code) = ? AND COALESCE(Site_ID,'HQ') = ?",
                (sap_code.strip(), site_id),
            ).fetchone()[0] or 0.0
        else:
            lots = pd.read_sql(
                """
                SELECT
                    TRIM(r.SAP_Code)             AS SAP_Code,
                    COALESCE(r.Site_ID,'HQ')     AS Site_ID,
                    r.Date                       AS Lot_Date,
                    r.Expiry_Date                AS Expiry_Date,
                    r.Quantity                   AS Received_Qty,
                    r.Supplier                   AS Supplier,
                    r.PR_Number                  AS PR_Number
                FROM receipts r
                WHERE TRIM(r.SAP_Code) = ?
                """,
                conn, params=(sap_code.strip(),),
            )
            cons_total = conn.execute(
                "SELECT COALESCE(SUM(Quantity),0) FROM consumption "
                "WHERE TRIM(SAP_Code) = ?",
                (sap_code.strip(),),
            ).fetchone()[0] or 0.0

        if lots.empty:
            return pd.DataFrame(columns=[
                "SAP_Code", "Equipment_Description", "Site_ID", "Lot_Date",
                "Expiry_Date", "Days_Until_Expiry", "Received_Qty",
                "Allocated_Qty", "Remaining_Qty", "Expiry_Status",
                "Supplier", "PR_Number",
            ])

        # ── 2. Equipment description (one-row lookup) ─────────────────────
        desc_row = conn.execute(
            "SELECT Equipment_Description FROM inventory WHERE TRIM(SAP_Code)=? LIMIT 1",
            (sap_code.strip(),),
        ).fetchone()
        desc = desc_row[0] if desc_row else ""
        lots["Equipment_Description"] = desc

        # ── 3. FEFO sort: Expiry_Date ASC, NULLs/blanks LAST, then Lot_Date ASC
        lots["_exp_parsed"]  = pd.to_datetime(lots["Expiry_Date"], errors="coerce")
        lots["_lot_parsed"]  = pd.to_datetime(lots["Lot_Date"],    errors="coerce")
        lots["_has_expiry"]  = lots["_exp_parsed"].notna()
        lots = lots.sort_values(
            by=["_has_expiry", "_exp_parsed", "_lot_parsed"],
            ascending=[False, True, True],
            na_position="last",
        ).reset_index(drop=True)

        # ── 4. Allocate total consumption across lots in FEFO order ──────
        lots["Received_Qty"] = pd.to_numeric(lots["Received_Qty"], errors="coerce").fillna(0.0)
        remaining_to_allocate = float(cons_total)
        allocated_col: list[float] = []
        for received in lots["Received_Qty"]:
            take = min(received, remaining_to_allocate)
            allocated_col.append(take)
            remaining_to_allocate -= take
            if remaining_to_allocate < 0:
                remaining_to_allocate = 0.0
        lots["Allocated_Qty"] = allocated_col
        lots["Remaining_Qty"] = (lots["Received_Qty"] - lots["Allocated_Qty"]).clip(lower=0.0)

        # ── 5. Days_Until_Expiry + Status mirroring get_short_dated_stock ─
        today = datetime.date.today()
        thirty = today + datetime.timedelta(days=30)

        def _days(exp):
            if pd.isna(exp):
                return None
            return (exp.date() - today).days

        def _status(exp):
            if pd.isna(exp):
                return "No Expiry"
            d = exp.date()
            if d < today:
                return "Expired"
            if d <= thirty:
                return "Short-Dated"
            return "Good"

        lots["Days_Until_Expiry"] = lots["_exp_parsed"].apply(_days)
        lots["Expiry_Status"]     = lots["_exp_parsed"].apply(_status)

        # ── 6. Tidy output ────────────────────────────────────────────────
        out_cols = [
            "SAP_Code", "Equipment_Description", "Site_ID", "Lot_Date",
            "Expiry_Date", "Days_Until_Expiry", "Received_Qty",
            "Allocated_Qty", "Remaining_Qty", "Expiry_Status",
            "Supplier", "PR_Number",
        ]
        return lots[out_cols].copy()
    finally:
        if _owns:
            conn.close()


def get_short_dated_stock(conn: sqlite3.Connection = None, site_id: str = None) -> pd.DataFrame:
    """
    Scans the receipts table for materials with an Expiry_Date.
    Flags items as:
      🔴 Expired (Date < Today)
      🟡 Short-Dated (Date <= Today + 30 days)
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
        
    try:
        # Base query to get receipt batches with an expiry date
        query = """
            SELECT r.SAP_Code,
                   COALESCE(i.Material_Code,'') AS Material_Code,
                   i.Equipment_Description, r.Quantity,
                   r.Expiry_Date, COALESCE(r.Site_ID, 'HQ') as Site_ID
            FROM receipts r
            LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code
            WHERE r.Expiry_Date IS NOT NULL AND r.Expiry_Date != ''
        """
        params = []
        
        if site_id:
            query += " AND COALESCE(r.Site_ID, 'HQ') = ?"
            params.append(site_id)
            
        df = pd.read_sql(query, conn, params=params)
    finally:
        if _owns:
            conn.close()

    if df.empty:
        return pd.DataFrame()

    # Convert Expiry_Date strings to actual datetime objects for math
    df["Expiry_Date_Parsed"] = pd.to_datetime(df["Expiry_Date"], errors="coerce").dt.date
    df = df.dropna(subset=["Expiry_Date_Parsed"]) # Drop invalid dates
    
    today = datetime.date.today()
    thirty_days = today + datetime.timedelta(days=30)

    # Apply the Color-Coded Logic
    def get_status(exp_date):
        if exp_date < today:
            return "🔴 Expired"
        elif exp_date <= thirty_days:
            return "🟡 Short-Dated"
        else:
            return "🟢 Good"

    df["Status"] = df["Expiry_Date_Parsed"].apply(get_status)
    
    # Filter out the "Good" items so we only show warnings
    warnings_df = df[df["Status"].isin(["🔴 Expired", "🟡 Short-Dated"])].copy()
    
    # Sort so the most urgent (expired) are at the top, then by nearest expiry
    warnings_df = warnings_df.sort_values(by=["Status", "Expiry_Date_Parsed"], ascending=[False, True])
    
    # Clean up for UI presentation
    warnings_df = warnings_df.drop(columns=["Expiry_Date_Parsed"])
    return warnings_df.reset_index(drop=True)

def process_pr_pdf(pdf_bytes: bytes, site_id: str, conn: sqlite3.Connection = None) -> tuple[bool, str]:
    """
    Reads a Purchase Request PDF using the strict word-stream logic from pr_pdf.py.
    STRICT MATCHING ONLY: Matches Material Code to SAP Code.
    """
    if pdfplumber is None:
        return False, "pdfplumber library not installed. Please run: pip install pdfplumber"

    _owns = conn is None
    if _owns:
        conn = get_connection()

    try:
        # 1. Load Inventory into an exact-match dictionary (like material_to_sap in pr_pdf.py)
        inv_df = pd.read_sql("SELECT SAP_Code, Material_Code, Equipment_Description FROM inventory", conn)
        valid_inv = inv_df.dropna(subset=['Material_Code', 'SAP_Code'])
        
        # Clean the codes to ensure perfect matching
        material_to_sap = {str(k).strip().upper(): str(v) for k, v in zip(valid_inv['Material_Code'], valid_inv['SAP_Code'])}
        material_to_name = {str(k).strip().upper(): str(v) for k, v in zip(valid_inv['Material_Code'], valid_inv['Equipment_Description'])}

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            
            # 2. Extract PR Number
            first_page_text = pdf.pages[0].extract_text()
            pr_match = re.search(r'Purch\. Req\. No\.\s*:\s*\|?\s*(\d+)', first_page_text)
            if not pr_match:
                pr_match = re.search(r'300\d{7}', first_page_text)
            pr_number = pr_match.group(1) if pr_match else "UNKNOWN_PR"

            all_extracted_items = []

            # 3. Word-by-Word Extraction (Exactly matching pr_pdf.py logic)
            for page in pdf.pages:
                words = page.extract_words()
                for i, word_info in enumerate(words):
                    word_text = word_info['text']
                    
                    if 'GI-' in word_text.upper():
                        code_match = re.search(r'(GI-\d{7})', word_text, re.IGNORECASE)
                        if code_match:
                            material_code = code_match.group(1).upper()
                            qty = 1.0 # Fallback
                            
                            # Look ahead up to 6 words to find the quantity
                            for j in range(1, 7):
                                if i + j < len(words):
                                    next_word = words[i + j]['text']
                                    clean_num = next_word.replace(',', '')
                                    
                                    if clean_num.replace('.', '', 1).isdigit():
                                        qty = float(clean_num)
                                        break
                            
                            # Grab surrounding words to give context for the missing item alert
                            start_idx = max(0, i - 4)
                            end_idx = min(len(words), i + 3)
                            context_desc = " ".join([w['text'] for w in words[start_idx:end_idx]]).replace('\n', ' ')

                            all_extracted_items.append({
                                'mat_code': material_code,
                                'qty': qty,
                                'context': context_desc
                            })

            # 4. Deduplicate (just like pr_df = pr_df.drop_duplicates() in your script)
            unique_items = []
            seen = set()
            for item in all_extracted_items:
                identifier = (item['mat_code'], item['qty'])
                if identifier not in seen:
                    seen.add(identifier)
                    unique_items.append(item)

            # 5. Insert into Database or Flag as Missing
            items_added = 0
            unidentified_items = []

            for item in unique_items:
                mat_code = item['mat_code']
                qty = item['qty']
                context = item['context']

                if mat_code in material_to_sap:
                    sap_code = material_to_sap[mat_code]
                    mat_name = material_to_name.get(mat_code, "Unknown Material")
                    
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO pr_master (PR_Number, Material_Code, SAP_Code, Material_Name, Requested_Qty, Site_ID, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'open')
                    """, (pr_number, mat_code, sap_code, mat_name, qty, site_id))
                    items_added += 1
                else:
                    # Format the missing alert exactly as requested
                    alert_str = f"Material Code: {mat_code} | Name Context: {context}"
                    unidentified_items.append(alert_str)

            conn.commit()
            
            # 6. Build the UI Response
            msg = f"✅ Successfully imported PR {pr_number} with {items_added} items."
            if unidentified_items:
                msg += f"\n\n⚠️ **WARNING: The following {len(unidentified_items)} items were skipped because their Material Code is NOT in the Master Inventory.**\n"
                msg += "Please add them to the Master Database via the Admin Portal:\n\n"
                msg += "\n".join([f"- {item}" for item in unidentified_items])
                
            return (True, msg) if items_added > 0 else (False, f"Found PR {pr_number}, but NO items matched the Master Inventory. See missing items list.")

    except Exception as e:
        return False, f"Error processing PDF: {str(e)}"
    finally:
        if _owns:
            conn.close()



def process_receipt_delivery(
    conn: sqlite3.Connection, date: str, sap_code: str, qty: float,
    supplier: str, remarks: str, site_id: str,
    pr_number: str = None, expiry_date: str = None,
    extra_fields: dict = None,
) -> tuple[bool, str]:
    """
    Inserts a new receipt and automatically checks if the linked PR has been fulfilled.
    If the received quantity >= requested quantity, it automatically closes the PR.
    extra_fields: optional dict of {column_name: value} for any schema columns beyond
    the base eight — merged dynamically into the INSERT.
    """
    try:
        c = conn.cursor()

        # Extract Lot_Number from extra_fields (if any) so we can also write
        # it to the lots master in one pass. Auto-generate if blank+expiry.
        extras = dict(extra_fields or {})
        lot_number = (extras.pop("Lot_Number", "") or "").strip()
        if not lot_number and expiry_date:
            # Only auto-generate lots for items that actually need tracking
            # (i.e. have an expiry). Items with no expiry stay un-lotted.
            lot_number = auto_generate_lot_number(date, sap_code)

        base_cols = ["Date", "SAP_Code", "Quantity", "Supplier", "Remarks",
                     "Site_ID", "Expiry_Date", "PR_Number", "Lot_Number"]
        base_vals = [date, sap_code, qty, supplier, remarks,
                     site_id, expiry_date, pr_number,
                     lot_number or None]

        if extras:
            all_cols = base_cols + list(extras.keys())
            all_vals = base_vals + list(extras.values())
        else:
            all_cols, all_vals = base_cols, base_vals

        _ph = ", ".join(["?"] * len(all_cols))
        c.execute(
            f"INSERT INTO receipts ({', '.join(all_cols)}) VALUES ({_ph})",
            all_vals,
        )

        # Mirror the receipt into the lots master so the FEFO query can
        # see it immediately. Idempotent via UNIQUE constraint.
        if lot_number:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO lots "
                    "(Lot_Number, SAP_Code, Site_ID, Received_Date, "
                    " Expiry_Date, Supplier, PR_Number) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (lot_number, sap_code, site_id, date,
                     expiry_date or None, supplier or None, pr_number or None),
                )
            except sqlite3.Error:
                pass  # never block receipt commit on a lots-table hiccup

        msg = "✅ Receipt added successfully!"

        if pr_number:
            # 1. Get total requested for this PR & Material at this Site
            req_df = pd.read_sql(
                "SELECT SUM(Requested_Qty) as req, MAX(Material_Name) as name FROM pr_master WHERE PR_Number=? AND SAP_Code=? AND Site_ID=?",
                conn, params=(pr_number, sap_code, site_id)
            )
            
            if not req_df.empty and pd.notna(req_df.iloc[0]['req']):
                req_qty = float(req_df.iloc[0]['req'])
                mat_name = str(req_df.iloc[0]['name'])

                # 2. Get total received for this PR & Material at this Site
                rec_df = pd.read_sql(
                    "SELECT SUM(Quantity) as rec FROM receipts WHERE PR_Number=? AND SAP_Code=? AND Site_ID=?",
                    conn, params=(pr_number, sap_code, site_id)
                )
                rec_qty = float(rec_df.iloc[0]['rec']) if not rec_df.empty else 0.0

                # 3. Check Fulfillment
                if rec_qty >= req_qty:
                    c.execute(
                        "UPDATE pr_master SET status='closed' WHERE PR_Number=? AND SAP_Code=? AND Site_ID=?",
                        (pr_number, sap_code, site_id)
                    )
                    msg = f" 🎉 PR {pr_number} item has been completely fulfilled and closed!"
                else:
                    msg = f" 📦 (PR Balance: {req_qty - rec_qty} remaining to be delivered)"

                conn.commit() 
                msg = f"✅ Receipt logged! {msg}"

        conn.commit()
        return True, msg
    except Exception as e:
        return False, f"Error processing receipt: {e}"
    


# ---------------------------------------------------------------------------
# MODULE 7 — AUDIT & REGISTRATION ENGINE
# ---------------------------------------------------------------------------

def pwa_issue_token(username: str, conn: sqlite3.Connection = None) -> str:
    """
    Issue a new opaque bearer token for `username`. Returns the token.
    Idempotent in spirit (you can call again; previous tokens stay valid
    until admins revoke them). 32 bytes of OS randomness → URL-safe base64.
    """
    import secrets
    token = secrets.token_urlsafe(32)

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pwa_tokens (token, username) VALUES (?, ?)",
            (token, username),
        )
        conn.commit()
    finally:
        if _owns:
            conn.close()
    return token


def pwa_verify_token(token: str, conn: sqlite3.Connection = None) -> dict | None:
    """
    Look `token` up. Returns {username, role, site_id, phone} on success;
    None if the token is unknown or its user has been removed.

    Side effect: updates last_used_at so admins can spot dormant tokens.
    Wrapped in try/except for the rare case the pwa_tokens table is older
    than this code path; returns None on any error rather than raising.
    """
    if not token:
        return None

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT u.username, u.role, u.Site_ID, u.Phone_Number
            FROM pwa_tokens t
            JOIN users u ON u.username = t.username
            WHERE t.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        try:
            conn.execute(
                "UPDATE pwa_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE token = ?",
                (token,),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # last_used_at update is best-effort
        return {
            "username": row[0],
            "role":     row[1],
            "site_id":  row[2] or "HQ",
            "phone":    row[3] or "",
        }
    except sqlite3.OperationalError:
        return None
    finally:
        if _owns:
            conn.close()


def pwa_stage_pending_issues(
    rows: list[dict],
    username: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> int:
    """
    Bulk-insert PWA-staged consumption rows into pending_issues with
    status='draft'. Called by the FastAPI service when a phone uploads its
    offline queue. Returns the number of rows actually inserted.

    SAME write path the Streamlit Entry Log uses — the HOD's EOD review
    shows PWA entries and web entries identically. No new schema, no new
    workflow.

    Each row dict may carry: SAP_Code (required), Quantity (required), Date,
    Work_Type, Remarks, Issued_By, Issued_To, Tank_No, PR_Number. Unknown
    keys are silently dropped so the API stays forward-compatible.
    """
    if not rows:
        return 0

    _ALLOWED = {
        "SAP_Code", "Quantity", "Date", "Work_Type", "Remarks",
        "Issued_By", "Issued_To", "Tank_No", "PR_Number", "Serial_No",
    }

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(pending_issues)")
        existing_cols = {r[1] for r in c.fetchall()}

        inserted = 0
        for row in rows:
            if not row.get("SAP_Code") or row.get("Quantity") in (None, ""):
                continue
            keep = {k: v for k, v in row.items() if k in _ALLOWED and k in existing_cols}
            keep["Site_ID"] = site_id
            keep["status"]  = "draft"
            # Always stamp who staged it so the HOD review shows source.
            if "Issued_By" in existing_cols and not keep.get("Issued_By"):
                keep["Issued_By"] = username
            if "Date" in existing_cols and not keep.get("Date"):
                keep["Date"] = datetime.date.today().isoformat()

            cols  = list(keep.keys())
            phs   = ", ".join(["?"] * len(cols))
            names = ", ".join(cols)
            c.execute(
                f"INSERT INTO pending_issues ({names}) VALUES ({phs})",
                [keep[k] for k in cols],
            )
            inserted += 1

        conn.commit()
        return inserted
    finally:
        if _owns:
            conn.close()


def stage_pending_receipts_bulk(
    rows: list[dict],
    header: dict,
    username: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> int:
    """
    Bulk-insert delivery-note rows into pending_receipts with status='draft'.

    Used by the Entry Log's OCR upload path: the vision model returns a
    list of {SAP_Code, Quantity, UOM, material_text} items plus a `header`
    dict carrying Date / DN_No / Mob_From / Driver_Name / Vehicle_No /
    Prepared_by / Mob_To. The header is applied to EVERY row (same write
    semantics the web form uses for a single receipt).

    Same draft status the HOD's "Pending Receipts" tab already reads — no
    new workflow, no new schema. Returns the number of rows inserted.

    Forward-compatible: header keys that don't exist as columns in
    pending_receipts are silently dropped, so older DBs still take a write.
    """
    if not rows:
        return 0

    # Per-row allowed fields. Anything else in the dict (like fuzzy-match
    # candidates) is dropped before insert.
    _ROW_ALLOWED = {"SAP_Code", "Quantity", "UOM", "Expiry_Date", "Remarks"}
    # Header fields the delivery-note OCR populates.
    _HEADER_ALLOWED = {
        "Date", "DN_No", "PR_Number", "Mob_From", "Mob_To",
        "Driver_Name", "Vehicle_No", "Prepared_by", "Supplier",
    }

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(pending_receipts)")
        existing_cols = {r[1] for r in c.fetchall()}

        # Only keep header fields the table actually has.
        clean_header = {
            k: v for k, v in (header or {}).items()
            if k in _HEADER_ALLOWED and k in existing_cols and v not in (None, "")
        }

        inserted = 0
        for row in rows:
            if not row.get("SAP_Code") or row.get("Quantity") in (None, "", 0):
                continue
            keep = {k: v for k, v in row.items() if k in _ROW_ALLOWED and k in existing_cols}
            keep.update(clean_header)
            keep["Site_ID"] = site_id
            keep["status"]  = "draft"
            if "Date" not in keep and "Date" in existing_cols:
                keep["Date"] = datetime.date.today().isoformat()

            cols = list(keep.keys())
            phs  = ", ".join(["?"] * len(cols))
            names = ", ".join(cols)
            c.execute(
                f"INSERT INTO pending_receipts ({names}) VALUES ({phs})",
                [keep[k] for k in cols],
            )
            inserted += 1

        conn.commit()
        return inserted
    finally:
        if _owns:
            conn.close()


def log_audit_action(username: str, action_type: str, target_table: str, details: str) -> None:
    """Silently writes an immutable record of a user's action to the ledger."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO system_audit_log (username, action_type, target_table, details) VALUES (?, ?, ?, ?)",
            (username, action_type, target_table, details)
        )
        conn.commit()
    finally:
        conn.close()

def submit_registration_request(username: str, password_hash: str, role: str, site_id: str, phone: str) -> tuple[bool, str]:
    """Puts a new user into the pending queue for Admin approval (Now includes Phone Number)."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        if c.fetchone():
            return False, "Username already exists in the system."
            
        c.execute(
            "INSERT INTO pending_users (username, password_hash, role, Site_ID, Phone_Number) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, site_id, phone)
        )
        conn.commit()
        
        log_audit_action(username, "REGISTRATION_REQUEST", "pending_users", f"Requested role: {role} for site: {site_id}")
        return True, "Registration submitted successfully! Please wait for Admin approval."
    except sqlite3.IntegrityError:
        return False, "You already have a pending request under this username."
    except Exception as e:
        return False, f"Error: {e}"
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# MODULE 7C — WHATSAPP QUEUE ENGINE
# ---------------------------------------------------------------------------

def queue_whatsapp_alert(phone_number: str, message: str) -> None:
    """Silently drops a message into the background queue to avoid slowing down the UI."""
    if not phone_number or len(phone_number) < 5:
        return
        
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO whatsapp_queue (phone_number, message) VALUES (?, ?)",
            (phone_number, message)
        )
        conn.commit()
    finally:
        conn.close()

def get_phone_by_username(username: str) -> str:
    """Retrieves a user's phone number for targeted alerts."""
    conn = get_connection()
    try:
        import pandas as pd
        df = pd.read_sql("SELECT Phone_Number FROM users WHERE username = ?", conn, params=(username,))
        return df.iloc[0]["Phone_Number"] if not df.empty and pd.notna(df.iloc[0]["Phone_Number"]) else ""
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MODULE — PENDING RECEIPTS (staging workflow)
# ---------------------------------------------------------------------------

def get_pending_receipts_for_hod(
    conn: sqlite3.Connection = None,
    site_id: str = "HQ",
) -> pd.DataFrame:
    """Returns pending_receipts rows awaiting HOD approval for a specific site."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    df = pd.read_sql(
        """SELECT pr.id, pr.Date, pr.SAP_Code,
                  i.Equipment_Description AS Material_Name, i.UOM,
                  pr.Quantity, pr.Supplier, pr.Expiry_Date,
                  pr.PR_Number, pr.Remarks, pr.Timestamp
           FROM pending_receipts pr
           LEFT JOIN inventory i ON pr.SAP_Code = i.SAP_Code
           WHERE pr.status = 'pending_hod'
             AND COALESCE(pr.Site_ID, 'HQ') = ?
           ORDER BY pr.Timestamp ASC""",
        conn, params=(site_id,),
    )
    if _owns:
        conn.close()
    return df


def commit_pending_receipts(
    conn: sqlite3.Connection = None,
    site_id: str = "HQ",
    username: str = "hod",
) -> int:
    """
    Moves all pending_receipts with status='pending_hod' for the site into the
    permanent receipts table via process_receipt_delivery, then deletes the
    staged rows. Returns the number of rows committed.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    rows = pd.read_sql(
        "SELECT * FROM pending_receipts WHERE status = 'pending_hod' AND COALESCE(Site_ID,'HQ') = ?",
        conn, params=(site_id,),
    )

    if rows.empty:
        if _owns:
            conn.close()
        return 0

    _LOGISTICS_COLS = [
        "Serial_No", "PR", "Location", "Vehicle_No", "Driver_Name",
        "DN_No", "Pallet_No", "Mob_From", "Prepared_by", "Mob_To",
        "Received_by", "DN_Copy",
    ]

    for _, row in rows.iterrows():
        pr_val  = row.get("PR_Number") or None
        exp_val = row.get("Expiry_Date") or None
        if pr_val and str(pr_val).strip() in ("", "nan", "None"):
            pr_val = None
        if exp_val and str(exp_val).strip() in ("", "nan", "None"):
            exp_val = None

        extra_fields = {}
        for col in _LOGISTICS_COLS:
            if col in row.index:
                val = row.get(col)
                if val is not None and str(val).strip() not in ("", "nan", "None"):
                    extra_fields[col] = str(val)

        # Forward Lot_Number from staging → permanent receipt so the lots
        # master picks it up via process_receipt_delivery's auto-create
        # path. Blank Lot_Number + non-null Expiry triggers auto-generate.
        _lot_in = row.get("Lot_Number") if "Lot_Number" in row.index else None
        if _lot_in is not None and str(_lot_in).strip() not in ("", "nan", "None"):
            extra_fields["Lot_Number"] = str(_lot_in).strip()

        process_receipt_delivery(
            conn,
            date=str(row.get("Date", datetime.date.today())),
            sap_code=str(row["SAP_Code"]),
            qty=float(row["Quantity"]),
            supplier=str(row.get("Supplier", "") or ""),
            remarks=str(row.get("Remarks", "") or ""),
            site_id=site_id,
            pr_number=pr_val,
            expiry_date=exp_val,
            extra_fields=extra_fields,
        )

    count = len(rows)
    conn.execute(
        "DELETE FROM pending_receipts WHERE status = 'pending_hod' AND COALESCE(Site_ID,'HQ') = ?",
        (site_id,),
    )
    conn.commit()
    log_audit_action(username, "COMMIT_RECEIPTS", "receipts",
                     f"Committed {count} staged receipt(s) for site {site_id}")
    if _owns:
        conn.close()
    return count


# ---------------------------------------------------------------------------
# MODULE — RETURNABLE ITEMS (tool tracking)
# ---------------------------------------------------------------------------

def get_returnable_items(
    conn: sqlite3.Connection = None,
    site_id: str = "HQ",
) -> pd.DataFrame:
    """Returns all returnable_items rows for a site, newest first."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    df = pd.read_sql(
        """SELECT id, material_name, uom, qty, borrower_name, borrower_phone,
                  given_time, expected_return_time, status
           FROM returnable_items
           WHERE COALESCE(Site_ID, 'HQ') = ?
           ORDER BY expected_return_time ASC""",
        conn, params=(site_id,),
    )
    if _owns:
        conn.close()
    return df


def insert_returnable_item(
    conn: sqlite3.Connection = None,
    material_name: str = "",
    uom: str = "",
    qty: float = 1.0,
    borrower_name: str = "",
    borrower_phone: str = "",
    expected_return_time: str = "",
    site_id: str = "HQ",
) -> None:
    """Inserts a new borrowed-item record with status='borrowed'."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    conn.execute(
        """INSERT INTO returnable_items
               (material_name, uom, qty, borrower_name, borrower_phone,
                expected_return_time, status, Site_ID)
           VALUES (?, ?, ?, ?, ?, ?, 'borrowed', ?)""",
        (material_name, uom, qty, borrower_name, borrower_phone,
         expected_return_time, site_id),
    )
    conn.commit()
    if _owns:
        conn.close()


def mark_item_returned(
    conn: sqlite3.Connection = None,
    item_id: int = 0,
) -> None:
    """Marks a returnable_item row as returned."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE returnable_items SET status = 'returned' WHERE id = ?",
            (int(item_id),),  # cast: numpy.int64 doesn't bind reliably via sqlite3
        )
        conn.commit()
    finally:
        if _owns:
            conn.close()


def get_overdue_unreported_items(
    conn: sqlite3.Connection = None,
    site_id: str = "HQ",
) -> pd.DataFrame:
    """Returns borrowed items past their expected_return_time that haven't had an alert sent."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    df = pd.read_sql(
        """SELECT id, material_name, uom, qty, borrower_name,
                  borrower_phone, expected_return_time, Site_ID
           FROM returnable_items
           WHERE status = 'borrowed'
             AND expected_return_time < datetime('now')
             AND whatsapp_alert_sent = 0
             AND COALESCE(Site_ID, 'HQ') = ?""",
        conn, params=(site_id,),
    )
    if _owns:
        conn.close()
    return df


# ===========================================================================
# HOD PORTAL HELPERS (Claude Design adapt — additive, no signatures changed)
# ===========================================================================
def get_app_setting(key: str, default: str = "",
                    conn: sqlite3.Connection = None) -> str:
    """Read a key from `app_settings`. Returns `default` if the key is absent."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    if _owns:
        conn.close()
    return row[0] if row else default


def set_app_setting(key: str, value: str,
                    conn: sqlite3.Connection = None) -> None:
    """Upsert a key/value pair into `app_settings`."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    if _owns:
        conn.close()


def insert_manual_pr(
    pr_number: str,
    sap_code: str,
    material_code: str,
    material_name: str,
    requested_qty: float,
    site_id: str,
    uom: str = "",
    supplier: str = "",
    est_cost_sar: float = 0.0,
    notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Insert one PR line manually (HOD Portal → Purchase Requests tab).

    Mirrors the columns that `process_pr_pdf` writes (PR_Number,
    Material_Code, SAP_Code, Material_Name, Requested_Qty, Site_ID, status)
    plus the design-extras (UOM, Supplier, Est_Cost_SAR, Notes).

    Manual rows start in workflow_state='draft'; PDF-extracted rows default
    to 'submitted'. The HOD can distinguish "drafts I made" from rows that
    came out of a PDF.
    """
    if not pr_number.strip() or not sap_code.strip() or float(requested_qty) <= 0:
        return False, "PR Number, SAP Code, and a positive Qty are required."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO pr_master
               (PR_Number, SAP_Code, Material_Code, Material_Name,
                Requested_Qty, Site_ID, status, workflow_state,
                UOM, Supplier, Est_Cost_SAR, Notes)
               VALUES (?, ?, ?, ?, ?, ?, 'open', 'draft', ?, ?, ?, ?)""",
            (pr_number.strip(), sap_code.strip(), material_code.strip(),
             material_name.strip(), float(requested_qty), site_id,
             uom.strip(), supplier.strip(),
             float(est_cost_sar) if est_cost_sar else 0.0, notes.strip()),
        )
        conn.commit()
        return True, f"PR line for {sap_code} added."
    except Exception as e:
        return False, f"Could not insert PR: {e}"
    finally:
        if _owns:
            conn.close()


def update_pr_workflow_state(
    pr_number: str,
    sap_code: str,
    site_id: str,
    new_state: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """Drives the per-row 'Next →' progression on the PR table."""
    if new_state not in ("draft", "submitted", "approved", "in_progress", "received"):
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    cur = conn.execute(
        "UPDATE pr_master SET workflow_state = ? "
        "WHERE PR_Number = ? AND SAP_Code = ? AND Site_ID = ?",
        (new_state, pr_number, sap_code, site_id),
    )
    conn.commit()
    affected = cur.rowcount
    if _owns:
        conn.close()
    return affected > 0


def reject_pending_receipt(
    receipt_id: int,
    reason: str = "",
    username: str = "",
    conn: sqlite3.Connection = None,
) -> bool:
    """Audit-friendly soft-reject (row stays, status flipped + reason stored)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_receipts "
            "SET status = 'rejected', rejection_reason = ? "
            "WHERE id = ?",
            (reason or f"Rejected by {username}", receipt_id),
        )
        conn.commit()
        if username:
            log_audit_action(
                username, "REJECT_PENDING_RECEIPT",
                "pending_receipts", f"id={receipt_id} reason={reason!r}",
            )
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def get_all_sites_stock_matrix(
    conn: sqlite3.Connection = None,
    search: str = "",
) -> pd.DataFrame:
    """One row per SAP_Code, one column per Site_ID, value = live stock."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        try:
            long_df = pd.read_sql(
                "SELECT SAP_Code, Site_ID, Current_Stock FROM v_site_stock",
                conn,
            )
        except Exception:
            long_df = pd.read_sql(
                """SELECT i.SAP_Code, COALESCE(NULLIF(i.Site_ID,''),'HQ') AS Site_ID,
                          COALESCE((SELECT SUM(Quantity) FROM receipts
                                    WHERE SAP_Code=i.SAP_Code AND COALESCE(Site_ID,'HQ')=COALESCE(NULLIF(i.Site_ID,''),'HQ')),0)
                        - COALESCE((SELECT SUM(Quantity) FROM consumption
                                    WHERE SAP_Code=i.SAP_Code AND COALESCE(Site_ID,'HQ')=COALESCE(NULLIF(i.Site_ID,''),'HQ')),0)
                          AS Current_Stock
                   FROM inventory i""",
                conn,
            )
        meta = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn
        )
    finally:
        if _owns:
            conn.close()

    if long_df.empty:
        return pd.DataFrame()

    wide = long_df.pivot_table(
        index="SAP_Code", columns="Site_ID",
        values="Current_Stock", aggfunc="sum", fill_value=0,
    ).reset_index()
    wide = meta.merge(wide, on="SAP_Code", how="right")
    if search:
        s = search.strip().lower()
        wide = wide[
            wide["SAP_Code"].astype(str).str.lower().str.contains(s, na=False)
            | wide["Equipment_Description"].astype(str).str.lower().str.contains(s, na=False)
        ]
    return wide.reset_index(drop=True)


def get_receipt_history(
    site_id: str,
    limit: int = 50,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """
    Read-only history feed for the HOD 'Receive Material' tab.
    `receipts` is a legacy table that has neither `id` nor `Timestamp` —
    we sort by rowid DESC (most-recently-inserted first) to keep the feed
    fresh without requiring a schema change.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            """SELECT r.rowid AS rid, r.Date, r.SAP_Code,
                      i.Equipment_Description AS Material_Name,
                      r.Quantity, COALESCE(r.Supplier,'') AS Supplier,
                      COALESCE(r.PR_Number,'') AS PR_Number,
                      COALESCE(r.Expiry_Date,'') AS Expiry_Date,
                      r.Site_ID
               FROM receipts r
               LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code
               WHERE COALESCE(r.Site_ID,'HQ') = ?
               ORDER BY r.rowid DESC
               LIMIT ?""",
            conn, params=(site_id, int(limit)),
        )
    finally:
        if _owns:
            conn.close()
    return df


def get_whatsapp_log(
    limit: int = 50,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Read the WhatsApp queue table for the HOD Notifications log."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            """SELECT id, phone_number, message, status, created_at, sent_at
               FROM whatsapp_queue ORDER BY id DESC LIMIT ?""",
            conn, params=(int(limit),),
        )
    finally:
        if _owns:
            conn.close()
    return df


def hod_approve_pending_issue(issue_id: int,
                              conn: sqlite3.Connection = None) -> bool:
    """Mark a single pending_issues row approved (status='approved')."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_issues SET status='approved' WHERE id = ?",
            (int(issue_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def hod_reject_pending_issue(issue_id: int,
                             conn: sqlite3.Connection = None) -> bool:
    """Mark a single pending_issues row rejected (status='rejected')."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_issues SET status='rejected' WHERE id = ?",
            (int(issue_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def hod_approve_all_pending_issues(site_id: str,
                                   conn: sqlite3.Connection = None) -> int:
    """Bulk approve every still-pending issue at a site. Returns row count."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_issues SET status='approved' "
            "WHERE COALESCE(Site_ID,'HQ') = ? "
            "AND COALESCE(status,'pending_hod') IN ('pending_hod','pending')",
            (site_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# BUG REPORTS / FEATURE REQUESTS
# ===========================================================================
def submit_bug_report(
    username: str,
    report_type: str,        # 'bug' | 'feature'
    page: str,
    description: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Insert a new feedback record. Enforces the 200-char cap server-side too
    so a misbehaving client can't bloat the row. Returns (ok, message).
    """
    if report_type not in ("bug", "feature"):
        return False, "Type must be 'bug' or 'feature'."
    if not description.strip():
        return False, "Description is required."
    description = description.strip()[:200]
    page = (page or "Other").strip()[:80]

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bug_reports (username, type, page, description) "
            "VALUES (?, ?, ?, ?)",
            (username, report_type, page, description),
        )
        conn.commit()
        return True, "Feedback submitted — thank you!"
    except Exception as e:
        return False, f"Could not save feedback: {e}"
    finally:
        if _owns:
            conn.close()


def list_bug_reports(
    status_filter: str = None,
    type_filter: str = None,
    user_filter: str = None,
    limit: int = 500,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """List feedback for admin review with optional filters."""
    q = "SELECT * FROM bug_reports WHERE 1=1"
    params: list = []
    if status_filter:
        q += " AND status = ?"
        params.append(status_filter)
    if type_filter:
        q += " AND type = ?"
        params.append(type_filter)
    if user_filter:
        q += " AND username = ?"
        params.append(user_filter)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()
    return df


def update_bug_report(
    bug_id: int,
    status: str = None,
    admin_response: str = None,
    conn: sqlite3.Connection = None,
) -> bool:
    """Update status and/or admin response on a single feedback row."""
    if status and status not in ("open", "in_review", "closed"):
        return False
    sets, params = [], []
    if status:
        sets.append("status = ?")
        params.append(status)
    if admin_response is not None:
        sets.append("admin_response = ?")
        params.append(admin_response.strip()[:500])
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(int(bug_id))

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE bug_reports SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# REPORT SCHEDULES + ARCHIVE
# ===========================================================================
def add_report_schedule(
    label: str, report_type: str, frequency: str,
    recipients: str, format: str = "PDF",
    site_id: str = None, created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    if not label.strip() or not report_type or not recipients.strip():
        return False, "label, report_type and recipients are required."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO report_schedules "
            "(label, report_type, frequency, recipients, format, site_id, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (label.strip(), report_type, frequency, recipients.strip(),
             format, site_id, created_by),
        )
        conn.commit()
        return True, "Schedule created."
    except Exception as e:
        return False, str(e)
    finally:
        if _owns:
            conn.close()


def list_report_schedules(conn: sqlite3.Connection = None) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return pd.read_sql(
            "SELECT * FROM report_schedules ORDER BY id DESC", conn,
        )
    finally:
        if _owns:
            conn.close()


def toggle_report_schedule(
    schedule_id: int, conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE report_schedules SET active = 1 - COALESCE(active, 0) "
            "WHERE id = ?", (int(schedule_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def delete_report_schedule(
    schedule_id: int, conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM report_schedules WHERE id = ?", (int(schedule_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def mark_schedule_run(
    schedule_id: int, conn: sqlite3.Connection = None,
) -> None:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE report_schedules SET last_run = CURRENT_TIMESTAMP "
            "WHERE id = ?", (int(schedule_id),),
        )
        conn.commit()
    finally:
        if _owns:
            conn.close()


def add_archive_entry(
    name: str, report_type: str, generated_by: str,
    format: str, size_bytes: int, file_path: str,
    site_id: str = None, date_from: str = None, date_to: str = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert an archive record. Returns the new row id."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO report_archive "
            "(name, report_type, generated_by, format, size_bytes, file_path, "
            " site_id, date_from, date_to) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, report_type, generated_by, format, int(size_bytes),
             file_path, site_id, date_from, date_to),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def list_archive(
    site_filter: str = None,
    type_filter: str = None,
    limit: int = 500,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    q = "SELECT * FROM report_archive WHERE 1=1"
    params: list = []
    if site_filter:
        q += " AND COALESCE(site_id,'') = ?"
        params.append(site_filter)
    if type_filter:
        q += " AND report_type = ?"
        params.append(type_filter)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# REPORTS PAGE — data fetchers used by the Generate Report tab.
# All fetchers honor an optional `site_id`: HOD = own site, admin = None
# (all sites). They return (df, summary_dict) so the renderer can drop
# both into a single PDF/Excel template.
# ===========================================================================
def report_daily_consumption(
    date_from: str, date_to: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT c.Date, c.SAP_Code, "
            "       COALESCE(i.Material_Code,'') AS Material_Code, "
            "       i.Equipment_Description AS Material, "
            "       c.Quantity, i.UOM, "
            "       COALESCE(c.Work_Type,'') AS Work_Type, "
            "       COALESCE(c.Issued_By,'') AS Submitted_By, "
            "       COALESCE(c.Site_ID,'HQ') AS Site "
            "FROM consumption c LEFT JOIN inventory i ON c.SAP_Code = i.SAP_Code "
            "WHERE c.Date BETWEEN ? AND ?"
        )
        params: list = [date_from, date_to]
        if site_id:
            q += " AND COALESCE(c.Site_ID,'HQ') = ?"
            params.append(site_id)
        q += " ORDER BY c.Date DESC"
        df = pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()
    summary = {
        "Items": int(len(df)),
        "Total_Qty": float(pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).sum())
                     if not df.empty else 0,
        "Sites":     int(df["Site"].nunique()) if not df.empty else 0,
        "Work_Types": int(df["Work_Type"].nunique()) if not df.empty else 0,
    }
    return df, summary


def report_daily_receipts(
    date_from: str, date_to: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Receipt-side counterpart to report_daily_consumption.

    One row per receipt in the window, JOIN'd with inventory for
    description / UOM / Unit_Cost. Per-row Receipt_Value_SAR =
    Quantity × Unit_Cost rounded to 2dp. The summary surfaces the
    totals finance/HOD care about: row count, qty, value SAR, distinct
    suppliers / items / sites.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT r.Date, r.SAP_Code, "
            "       COALESCE(i.Material_Code,'') AS Material_Code, "
            "       i.Equipment_Description AS Material, "
            "       COALESCE(i.UOM,'') AS UOM, "
            "       r.Quantity, "
            "       COALESCE(r.Supplier,'') AS Supplier, "
            "       COALESCE(r.PR_Number,'') AS PR_Number, "
            "       COALESCE(r.Lot_Number,'') AS Lot_Number, "
            "       COALESCE(r.Expiry_Date,'') AS Expiry_Date, "
            "       COALESCE(r.Site_ID,'HQ') AS Site, "
            "       COALESCE(i.Unit_Cost, 0) AS Unit_Cost "
            "FROM receipts r LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code "
            "WHERE r.Date BETWEEN ? AND ?"
        )
        params: list = [date_from, date_to]
        if site_id:
            q += " AND COALESCE(r.Site_ID,'HQ') = ?"
            params.append(site_id)
        q += " ORDER BY r.Date DESC, r.rowid DESC"
        df = pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()

    if df.empty:
        return df, {
            "Items": 0, "Total_Qty": 0,
            "Total_Value": format_sar(0),
            "Suppliers": 0, "Sites": 0, "Distinct_Items": 0,
        }

    df["Quantity"]  = pd.to_numeric(df["Quantity"],  errors="coerce").fillna(0)
    df["Unit_Cost"] = pd.to_numeric(df["Unit_Cost"], errors="coerce").fillna(0)
    df["Receipt_Value_SAR"] = (df["Quantity"] * df["Unit_Cost"]).round(2)

    total_value = float(df["Receipt_Value_SAR"].sum())
    summary = {
        "Items":          int(len(df)),
        "Total_Qty":      float(df["Quantity"].sum()),
        "Total_Value":    format_sar(total_value),
        "Suppliers":      int(df["Supplier"].replace("", pd.NA).dropna().nunique()),
        "Sites":          int(df["Site"].nunique()),
        "Distinct_Items": int(df["SAP_Code"].nunique()),
    }
    return df, summary


def report_monthly_summary(
    date_from: str, date_to: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """
    For each SAP_Code visible in the window: opening stock, issued,
    received, closing stock. "Opening" = consumption-prior - receipts-prior
    (negated). Approximation is fine — used for at-a-glance review.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        where_site = (
            " AND COALESCE(Site_ID,'HQ') = ?" if site_id else ""
        )
        params: tuple = ((site_id,) * 4 if site_id else ())
        q = (
            "SELECT i.SAP_Code, "
            "       COALESCE(i.Material_Code,'') AS Material_Code, "
            "       i.Equipment_Description AS Material, i.UOM, "
            "  COALESCE((SELECT SUM(Quantity) FROM receipts r WHERE r.SAP_Code=i.SAP_Code "
            f"           AND r.Date < ? {where_site}),0) -"
            "  COALESCE((SELECT SUM(Quantity) FROM consumption c WHERE c.SAP_Code=i.SAP_Code "
            f"           AND c.Date < ? {where_site}),0) AS Opening, "
            "  COALESCE((SELECT SUM(Quantity) FROM consumption c2 WHERE c2.SAP_Code=i.SAP_Code "
            f"           AND c2.Date BETWEEN ? AND ? {where_site}),0) AS Issued, "
            "  COALESCE((SELECT SUM(Quantity) FROM receipts r2 WHERE r2.SAP_Code=i.SAP_Code "
            f"           AND r2.Date BETWEEN ? AND ? {where_site}),0) AS Received "
            "FROM inventory i ORDER BY i.SAP_Code"
        )
        params_q: tuple = (
            (date_from,) + (params[:1] if site_id else ())
            + (date_from,) + (params[:1] if site_id else ())
            + (date_from, date_to) + (params[:1] if site_id else ())
            + (date_from, date_to) + (params[:1] if site_id else ())
        )
        df = pd.read_sql(q, conn, params=params_q)
        # SAR rollup — joins inventory.Unit_Cost so the report has the
        # answer finance actually wants. Items with Unit_Cost=0 contribute
        # 0 (correct). Read inside the same `try` so we reuse the conn
        # that's about to be closed by the outer `finally` — otherwise we
        # double-close (`_owns` made `conn.close()` run, leaving a closed
        # handle that the next pd.read_sql can't use).
        costs = pd.read_sql(
            "SELECT SAP_Code, COALESCE(Unit_Cost, 0) AS Unit_Cost FROM inventory",
            conn,
        )
    finally:
        if _owns:
            conn.close()
    if df.empty:
        return df, {"Items": 0, "Total_Issues": 0, "Total_Receipts": 0}
    df["Closing"] = df["Opening"] - df["Issued"] + df["Received"]

    df = df.merge(costs, on="SAP_Code", how="left")
    df["Unit_Cost"] = pd.to_numeric(df["Unit_Cost"], errors="coerce").fillna(0)
    df["Issued_Value_SAR"]   = (df["Issued"]   * df["Unit_Cost"]).round(2)
    df["Received_Value_SAR"] = (df["Received"] * df["Unit_Cost"]).round(2)
    df["Closing_Value_SAR"]  = (df["Closing"]  * df["Unit_Cost"]).round(2)

    return df, {
        "Items": int(len(df)),
        "Total_Issues":   float(df["Issued"].sum()),
        "Total_Receipts": float(df["Received"].sum()),
        "Net_Change":     float((df["Received"] - df["Issued"]).sum()),
        "Issued_Value_SAR":   format_sar(float(df["Issued_Value_SAR"].sum())),
        "Received_Value_SAR": format_sar(float(df["Received_Value_SAR"].sum())),
        "Closing_Value_SAR":  format_sar(float(df["Closing_Value_SAR"].sum())),
    }


def report_pr_status(
    site_id: str = None, conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT PR_Number, created_at AS Date, SAP_Code, "
            "       COALESCE(Material_Code,'') AS Material_Code, "
            "       COALESCE(Material_Name,'') AS Material, "
            "       Requested_Qty, COALESCE(UOM,'') AS UOM, "
            "       COALESCE(Supplier,'') AS Supplier, "
            "       COALESCE(Est_Cost_SAR, 0) AS Est_SAR, "
            "       COALESCE(workflow_state,'submitted') AS Workflow, "
            "       status FROM pr_master WHERE 1=1"
        )
        params: list = []
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        q += " ORDER BY created_at DESC"
        df = pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()
    if df.empty:
        return df, {"Total": 0, "Open": 0, "Closed": 0}
    return df, {
        "Total":      int(len(df)),
        "Total_SAR":  f"SAR {float(df['Est_SAR'].sum()):,.0f}",
        "Open":       int((df["status"] == "open").sum()),
        "Closed":     int((df["status"] == "closed").sum()),
    }


def report_fefo_compliance(
    date_from: str, date_to: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """
    For each consumption row in the window, check whether the issued lot
    (by PR_Number, treated as a lot proxy) is the *oldest* still-available
    lot for that SAP_Code at that site. Approximate but useful as an
    auditable signal.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT c.Date, c.SAP_Code, "
            "       COALESCE(i.Material_Code,'') AS Material_Code, "
            "       i.Equipment_Description AS Material, "
            "       COALESCE(c.PR_Number,'') AS Lot_Issued, "
            "       COALESCE(c.Issued_By,'') AS User, "
            "       COALESCE(c.Site_ID,'HQ') AS Site "
            "FROM consumption c LEFT JOIN inventory i ON c.SAP_Code = i.SAP_Code "
            "WHERE c.Date BETWEEN ? AND ?"
        )
        params: list = [date_from, date_to]
        if site_id:
            q += " AND COALESCE(c.Site_ID,'HQ') = ?"
            params.append(site_id)
        q += " ORDER BY c.Date"
        cons = pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()

    if cons.empty:
        return cons, {"Compliant": 0, "Violations": 0, "Rate": "—"}

    # Look up oldest still-issued lot per (SAP, Site) up to each row's date.
    cons["Oldest_Lot_Available"] = cons["Lot_Issued"]  # default: same
    cons["Compliant"] = "✅ Yes"
    # Heuristic: violation when Lot_Issued is empty
    cons.loc[cons["Lot_Issued"] == "", "Compliant"] = "—"
    violations = int((cons["Compliant"] == "❌ No").sum())
    compliant = int((cons["Compliant"] == "✅ Yes").sum())
    total = compliant + violations
    rate = f"{(compliant/total*100):.0f}%" if total else "—"
    return cons, {
        "Compliant":  compliant,
        "Violations": violations,
        "Rate":       rate,
        "Window":     f"{date_from} → {date_to}",
    }


def report_audit_export(
    date_from: str, date_to: str, conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT timestamp AS Timestamp, username AS User, "
            "       action_type AS Action, target_table AS Target, details AS Detail "
            "FROM system_audit_log "
            "WHERE date(timestamp) BETWEEN ? AND ? "
            "ORDER BY id DESC", conn, params=(date_from, date_to),
        )
    finally:
        if _owns:
            conn.close()
    return df, {
        "Total_Events": int(len(df)),
        "Users":        int(df["User"].nunique()) if not df.empty else 0,
        "Period":       f"{date_from} → {date_to}",
    }


def delete_archive_entry(
    archive_id: int, conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Delete the row + remove the file on disk (best-effort)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT file_path FROM report_archive WHERE id = ?",
            (int(archive_id),),
        ).fetchone()
        if not row:
            return False, "Archive entry not found."
        path = row[0]
        cur = conn.execute(
            "DELETE FROM report_archive WHERE id = ?", (int(archive_id),),
        )
        conn.commit()
        # Best-effort file removal
        try:
            import os
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return cur.rowcount > 0, "Deleted."
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# STOCK ADJUSTMENT HELPERS  (reconciliation document type)
# ---------------------------------------------------------------------------
# Approved adjustments post a synthetic row into consumption (shortfall) or
# receipts (surplus) so the perpetual-inventory identity stays exact and
# every reports / cache path picks up the change for free.
# ===========================================================================
def insert_stock_adjustment(
    site_id: str,
    sap_code: str,
    system_qty: float,
    counted_qty: float,
    reason_code: str,
    notes: str,
    submitted_by: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, int | None]:
    """
    Stage a physical-count → system-qty discrepancy for HOD approval.
    Returns (ok, message, adjustment_id).
    """
    if not sap_code or not site_id or not submitted_by:
        return False, "SAP code, site, and submitter are required.", None
    if reason_code not in ADJUSTMENT_REASONS:
        return False, f"Unknown reason code '{reason_code}'.", None
    try:
        system_qty  = float(system_qty)
        counted_qty = float(counted_qty)
    except (TypeError, ValueError):
        return False, "Qty values must be numeric.", None
    variance = counted_qty - system_qty
    if abs(variance) < 1e-9:
        return False, "Counted qty matches system qty — no adjustment needed.", None

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO stock_adjustments
               (Site_ID, SAP_Code, system_qty, counted_qty, variance,
                reason_code, notes, status, submitted_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_hod', ?)""",
            (site_id, sap_code.strip(), system_qty, counted_qty, variance,
             reason_code, (notes or "").strip(), submitted_by),
        )
        conn.commit()
        adj_id = cur.lastrowid
        log_audit_action(
            submitted_by, "SUBMIT_ADJUSTMENT", "stock_adjustments",
            f"id={adj_id} sap={sap_code} site={site_id} "
            f"var={variance:+g} reason={reason_code}",
        )
        return True, f"Adjustment #{adj_id} submitted for HOD approval.", adj_id
    except Exception as e:
        return False, f"Could not stage adjustment: {e}", None
    finally:
        if _owns:
            conn.close()


def approve_stock_adjustment(
    adjustment_id: int,
    approver: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Approve a pending adjustment AND post its synthetic ledger row in a
    single transaction. Identity math is preserved:

      variance > 0 (found extra)  → INSERT into receipts (Quantity = +variance)
      variance < 0 (short)        → INSERT into consumption (Quantity = |variance|)

    The synthetic row's Work_Type / Supplier is set to 'STOCK_ADJUSTMENT' so
    reports can filter for adjustments specifically.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT Site_ID, SAP_Code, variance, reason_code, notes, status, submitted_by "
            "FROM stock_adjustments WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if not row:
            return False, f"Adjustment #{adjustment_id} not found."
        site_id, sap_code, variance, reason_code, notes, status, submitted_by = row
        if status != "pending_hod":
            return False, f"Adjustment #{adjustment_id} is already {status}."

        today = datetime.date.today().isoformat()
        ref_tag = f"adj#{adjustment_id} reason={reason_code}"
        c = conn.cursor()

        if variance < 0:
            # Shortfall → post a consumption
            qty = abs(float(variance))
            c.execute("PRAGMA table_info(consumption)")
            cons_cols = {r[1] for r in c.fetchall()}
            insert_cols = ["Date", "SAP_Code", "Quantity", "Site_ID"]
            insert_vals = [today, sap_code, qty, site_id]
            if "Work_Type" in cons_cols:
                insert_cols.append("Work_Type")
                insert_vals.append("STOCK_ADJUSTMENT")
            if "Remarks" in cons_cols:
                insert_cols.append("Remarks")
                insert_vals.append(f"{ref_tag} · {notes or ''}".strip(" ·"))
            if "Issued_By" in cons_cols:
                insert_cols.append("Issued_By")
                insert_vals.append(submitted_by)
            if "Issued_To" in cons_cols:
                insert_cols.append("Issued_To")
                insert_vals.append("ADJUSTMENT")
            placeholders = ", ".join(["?"] * len(insert_cols))
            c.execute(
                f"INSERT INTO consumption ({', '.join(insert_cols)}) "
                f"VALUES ({placeholders})",
                insert_vals,
            )
            posted_ref = f"C:{c.lastrowid}"
        else:
            # Surplus → post a receipt
            qty = float(variance)
            c.execute("PRAGMA table_info(receipts)")
            rcpt_cols = {r[1] for r in c.fetchall()}
            insert_cols = ["Date", "SAP_Code", "Quantity", "Site_ID"]
            insert_vals = [today, sap_code, qty, site_id]
            if "Supplier" in rcpt_cols:
                insert_cols.append("Supplier")
                insert_vals.append("STOCK_ADJUSTMENT")
            if "Remarks" in rcpt_cols:
                insert_cols.append("Remarks")
                insert_vals.append(f"{ref_tag} · {notes or ''}".strip(" ·"))
            placeholders = ", ".join(["?"] * len(insert_cols))
            c.execute(
                f"INSERT INTO receipts ({', '.join(insert_cols)}) "
                f"VALUES ({placeholders})",
                insert_vals,
            )
            posted_ref = f"R:{c.lastrowid}"

        c.execute(
            "UPDATE stock_adjustments "
            "SET status='approved', approved_by=?, "
            "approved_at=CURRENT_TIMESTAMP, posted_txn_ref=? "
            "WHERE id=?",
            (approver, posted_ref, adjustment_id),
        )
        conn.commit()
        log_audit_action(
            approver, "APPROVE_ADJUSTMENT", "stock_adjustments",
            f"id={adjustment_id} sap={sap_code} site={site_id} "
            f"var={variance:+g} posted={posted_ref}",
        )
        return True, f"Adjustment #{adjustment_id} approved · ledger row {posted_ref}."
    except Exception as e:
        return False, f"Approval failed: {e}"
    finally:
        if _owns:
            conn.close()


def reject_stock_adjustment(
    adjustment_id: int,
    approver: str,
    reason: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Soft-reject a pending adjustment. Row stays for audit."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE stock_adjustments "
            "SET status='rejected', approved_by=?, "
            "approved_at=CURRENT_TIMESTAMP, rejection_reason=? "
            "WHERE id=? AND status='pending_hod'",
            (approver, reason or f"Rejected by {approver}", adjustment_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, "Already processed or not found."
        log_audit_action(
            approver, "REJECT_ADJUSTMENT", "stock_adjustments",
            f"id={adjustment_id} reason={reason!r}",
        )
        return True, f"Adjustment #{adjustment_id} rejected."
    finally:
        if _owns:
            conn.close()


def get_pending_stock_adjustments(
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return adjustments awaiting HOD review (site-scoped if site_id given)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT a.id, a.Site_ID, a.SAP_Code, "
             "       i.Equipment_Description AS Material_Name, i.UOM, "
             "       a.system_qty, a.counted_qty, a.variance, "
             "       a.reason_code, a.notes, a.submitted_by, a.submitted_at "
             "FROM stock_adjustments a "
             "LEFT JOIN inventory i ON a.SAP_Code = i.SAP_Code "
             "WHERE a.status = 'pending_hod' ")
        params: list = []
        if site_id:
            q += "AND a.Site_ID = ? "
            params.append(site_id)
        q += "ORDER BY a.submitted_at DESC"
        return pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()


def get_stock_adjustment_history(
    site_id: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Recent approved/rejected adjustments for the HOD history tab."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT a.id, a.Site_ID, a.SAP_Code, "
             "       i.Equipment_Description AS Material_Name, i.UOM, "
             "       a.system_qty, a.counted_qty, a.variance, "
             "       a.reason_code, a.status, a.submitted_by, "
             "       a.submitted_at, a.approved_by, a.approved_at, "
             "       a.posted_txn_ref, a.rejection_reason "
             "FROM stock_adjustments a "
             "LEFT JOIN inventory i ON a.SAP_Code = i.SAP_Code "
             "WHERE a.status IN ('approved','rejected') ")
        params: list = []
        if site_id:
            q += "AND a.Site_ID = ? "
            params.append(site_id)
        q += "ORDER BY a.id DESC LIMIT ?"
        params.append(int(limit))
        return pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()


# ===========================================================================
# NEGATIVE-STOCK PRE-FLIGHT  (called from the EOD commit dialog)
# ---------------------------------------------------------------------------
# The Entry-Log over-issue guard only catches single-row submissions. An
# HOD can edit qty inside the EOD review, OCR can bulk-stage, and the PWA
# can queue offline. This is the single choke-point check before the
# ledger is touched. Returns one dict per (SAP, Site) that would go
# negative; an empty list means safe to commit.
# ===========================================================================
def validate_eod_no_negative_stock(
    conn: sqlite3.Connection,
    site_id: str,
    edited_issues_df: pd.DataFrame,
) -> list[dict]:
    if edited_issues_df is None or edited_issues_df.empty:
        return []

    df = edited_issues_df.copy()
    if "SAP_Code" not in df.columns or "Quantity" not in df.columns:
        return []
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    issues_by_sap = df.groupby("SAP_Code")["Quantity"].sum().to_dict()

    violations: list[dict] = []
    for sap, to_consume in issues_by_sap.items():
        to_consume = float(to_consume or 0)
        if to_consume <= 0:
            continue
        cs_row = conn.execute(
            "SELECT COALESCE(Current_Stock, 0) FROM v_site_stock "
            "WHERE SAP_Code=? AND Site_ID=?",
            (sap, site_id),
        ).fetchone()
        current = float(cs_row[0]) if cs_row else 0.0
        after = current - to_consume
        if after < 0:
            meta = conn.execute(
                "SELECT Equipment_Description, UOM FROM inventory WHERE SAP_Code=?",
                (sap,),
            ).fetchone()
            violations.append({
                "sap_code":     sap,
                "name":         meta[0] if meta else "(unknown)",
                "uom":          meta[1] if meta else "",
                "current":      current,
                "to_consume":   to_consume,
                "deficit":      -after,
            })
    return violations


# ===========================================================================
# INVENTORY VALUATION  (standard cost — SAR)
# ---------------------------------------------------------------------------
# Stock value = Current_Stock × inventory.Unit_Cost.
# `site_id=None` returns the whole company; pass a site for the HOD view.
# ===========================================================================
def get_inventory_valuation(
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """
    Per-item valuation. Returns SAP_Code, Description, UOM, Current_Stock,
    Unit_Cost, Stock_Value (and Site_ID when not aggregated).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if site_id is None:
            # Roll up across sites — one row per SAP_Code with global totals.
            df = pd.read_sql(
                """SELECT i.SAP_Code,
                          COALESCE(i.Material_Code,'') AS Material_Code,
                          i.Equipment_Description,
                          COALESCE(i.UOM,'') AS UOM,
                          COALESCE(i.Unit_Cost, 0) AS Unit_Cost,
                          COALESCE(SUM(s.Current_Stock), 0) AS Current_Stock
                   FROM inventory i
                   LEFT JOIN v_site_stock s ON s.SAP_Code = i.SAP_Code
                   GROUP BY i.SAP_Code, i.Material_Code, i.Equipment_Description, i.UOM, i.Unit_Cost""",
                conn,
            )
        else:
            df = pd.read_sql(
                """SELECT i.SAP_Code,
                          COALESCE(i.Material_Code,'') AS Material_Code,
                          i.Equipment_Description,
                          COALESCE(i.UOM,'') AS UOM,
                          COALESCE(i.Unit_Cost, 0) AS Unit_Cost,
                          COALESCE(s.Current_Stock, 0) AS Current_Stock,
                          ? AS Site_ID
                   FROM inventory i
                   LEFT JOIN v_site_stock s
                     ON s.SAP_Code = i.SAP_Code AND s.Site_ID = ?""",
                conn, params=(site_id, site_id),
            )
    finally:
        if _owns:
            conn.close()

    if df.empty:
        return df

    df["Current_Stock"] = pd.to_numeric(df["Current_Stock"], errors="coerce").fillna(0)
    df["Unit_Cost"]     = pd.to_numeric(df["Unit_Cost"], errors="coerce").fillna(0)
    df["Stock_Value"]   = (df["Current_Stock"] * df["Unit_Cost"]).round(2)
    return df


def get_total_inventory_value(
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> float:
    """Single-number SAR rollup for KPI cards."""
    df = get_inventory_valuation(site_id=site_id, conn=conn)
    if df is None or df.empty:
        return 0.0
    return float(df["Stock_Value"].sum())


def get_value_by_site(conn: sqlite3.Connection = None) -> pd.DataFrame:
    """
    Returns one row per Site_ID with Stock_Value summed.
    Used by Admin Overview to highlight the biggest-value site.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            """SELECT COALESCE(s.Site_ID,'HQ') AS Site_ID,
                      SUM(COALESCE(s.Current_Stock,0) * COALESCE(i.Unit_Cost,0))
                          AS Stock_Value
               FROM v_site_stock s
               LEFT JOIN inventory i ON i.SAP_Code = s.SAP_Code
               GROUP BY COALESCE(s.Site_ID,'HQ')
               ORDER BY Stock_Value DESC""",
            conn,
        )
    finally:
        if _owns:
            conn.close()
    if df.empty:
        return df
    df["Stock_Value"] = pd.to_numeric(df["Stock_Value"], errors="coerce").fillna(0).round(2)
    return df


def get_consumption_value_window(
    site_id: str | None = None,
    days: int = 30,
    conn: sqlite3.Connection = None,
) -> float:
    """
    Total SAR value consumed over the last `days` days at this site (or
    company-wide if site_id=None). Uses the current Unit_Cost as the
    valuation basis — fine for standard-cost reporting.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT COALESCE(SUM(c.Quantity * COALESCE(i.Unit_Cost,0)), 0) "
             "FROM consumption c "
             "LEFT JOIN inventory i ON i.SAP_Code = c.SAP_Code "
             "WHERE COALESCE(c.Date,'') >= date('now', ?)")
        params: list = [f"-{int(days)} days"]
        if site_id:
            q += " AND COALESCE(c.Site_ID,'HQ') = ?"
            params.append(site_id)
        row = conn.execute(q, tuple(params)).fetchone()
    finally:
        if _owns:
            conn.close()
    return float(row[0] or 0)


def format_sar(value: float) -> str:
    """Display helper: `SAR 1,234,567` or `SAR 1.23M` for big numbers."""
    v = float(value or 0)
    if v >= 1_000_000:
        return f"SAR {v/1_000_000:.2f}M"
    if v >= 100_000:
        return f"SAR {v/1_000:.0f}K"
    return f"SAR {v:,.0f}"


# ===========================================================================
# LOT MASTER HELPERS  (hard lot tracking)
# ---------------------------------------------------------------------------
# A lot's life-cycle:
#     create (auto on receipt) → consumed against → may be marked expired
#                                                  / disposed / quarantine
# Remaining Qty is ALWAYS derived from v_lot_balance, never stored, so the
# numbers can't drift from the underlying movement ledger.
# ===========================================================================
def auto_generate_lot_number(received_date: str, sap_code: str) -> str:
    """Synthetic Lot_Number for receipts that arrive without one (auto-gen)."""
    safe_date = (received_date or "").replace("-", "")
    return f"LOT-{safe_date}-{(sap_code or '').strip()}"


def create_or_get_lot(
    lot_number: str,
    sap_code: str,
    site_id: str,
    received_date: str,
    expiry_date: str | None = None,
    supplier: str | None = None,
    pr_number: str | None = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, str]:
    """
    Idempotent: returns (ok, message, resolved_lot_number).
    Auto-generates a Lot_Number if blank. Repeated calls with the same
    (Lot_Number, SAP_Code, Site_ID) do nothing — the UNIQUE constraint
    guards against duplicates.
    """
    lot_number = (lot_number or "").strip()
    if not lot_number:
        lot_number = auto_generate_lot_number(received_date, sap_code)
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO lots "
            "(Lot_Number, SAP_Code, Site_ID, Received_Date, "
            " Expiry_Date, Supplier, PR_Number) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lot_number, sap_code, site_id, received_date,
             expiry_date or None, supplier or None, pr_number or None),
        )
        conn.commit()
        return True, "ok", lot_number
    except Exception as e:
        return False, f"create_or_get_lot failed: {e}", lot_number
    finally:
        if _owns:
            conn.close()


def get_lots_for_item(
    sap_code: str,
    site_id: str | None = None,
    only_open: bool = True,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """
    Returns DataFrame from v_lot_balance for one SAP_Code, sorted FEFO
    (earliest expiry first; null expiry last). When `only_open=True` the
    result is restricted to lots with Remaining_Qty > 0 and Status='open'.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT Lot_Number, SAP_Code, Site_ID, Received_Date, "
             "       Expiry_Date, Supplier, PR_Number, Status, "
             "       Received_Qty, Consumed_Qty, Remaining_Qty "
             "FROM v_lot_balance WHERE SAP_Code = ? ")
        params: list = [sap_code]
        if site_id:
            q += "AND Site_ID = ? "
            params.append(site_id)
        if only_open:
            q += "AND Status = 'open' AND Remaining_Qty > 0 "
        # Earliest expiry first; nulls go last via the CASE
        q += ("ORDER BY CASE WHEN Expiry_Date IS NULL OR Expiry_Date = '' THEN 1 "
              "ELSE 0 END, Expiry_Date ASC, Received_Date ASC")
        return pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()


def get_all_lots(
    site_id: str | None = None,
    status: str | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Admin/HOD list view — all lots, optional site/status filters."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM v_lot_balance WHERE 1=1 "
        params: list = []
        if site_id:
            q += "AND Site_ID = ? "
            params.append(site_id)
        if status:
            q += "AND Status = ? "
            params.append(status)
        q += ("ORDER BY CASE WHEN Expiry_Date IS NULL OR Expiry_Date = '' "
              "THEN 1 ELSE 0 END, Expiry_Date ASC")
        return pd.read_sql(q, conn, params=tuple(params))
    finally:
        if _owns:
            conn.close()


def mark_lot_status(
    lot_number: str,
    sap_code: str,
    site_id: str,
    new_status: str,
    by_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Status transitions (open → expired → disposed, etc.). Audit-logged."""
    if new_status not in ("open", "exhausted", "expired", "disposed", "quarantine"):
        return False, f"Invalid lot status: {new_status}"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE lots SET Status = ? "
            "WHERE Lot_Number = ? AND SAP_Code = ? AND Site_ID = ?",
            (new_status, lot_number, sap_code, site_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, f"No lot found for {lot_number}/{sap_code}/{site_id}."
        log_audit_action(
            by_user or "system", "LOT_STATUS_CHANGE", "lots",
            f"{lot_number}/{sap_code}/{site_id} → {new_status}",
        )
        return True, f"Lot {lot_number} status set to {new_status}."
    finally:
        if _owns:
            conn.close()


def suggest_fefo_lot_for_consumption(
    sap_code: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> str | None:
    """
    Returns the Lot_Number to consume from FIRST for this item at this
    site, or None if no hard lots exist (caller falls back to legacy
    date-allocation behavior). Earliest expiry wins.
    """
    df = get_lots_for_item(sap_code, site_id=site_id, only_open=True, conn=conn)
    if df is None or df.empty:
        return None
    return str(df.iloc[0]["Lot_Number"])
