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
import os
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
# DB selection: prefer the real local DB when it exists (local dev / server),
# else fall back to the committed SANITIZED demo DB used by the public Streamlit
# Cloud share (where gi_database.db is gitignored & absent). An explicit
# GI_DB_FILE env var always wins. Backward-compatible: local/tests are unchanged
# because gi_database.db is present and bug_check patches DB_FILE explicitly.
DB_FILE = os.environ.get("GI_DB_FILE") or (
    "gi_database.db" if os.path.exists("gi_database.db") else "demo_seed.db"
)

# Columns that are auto-managed and should not appear in dynamic forms
SYSTEM_COLS = {"id", "Timestamp", "created_at"}


def _localize(df):
    """
    Apply config.auto_localize_timestamps to a DataFrame and return it.
    Lazy-imported to avoid any circular-import risk with config.py. Safe on
    None / empty DataFrames. Used at the boundary of every display-bound
    `get_*` helper so callers don't have to know about timezones.
    """
    if df is None:
        return df
    try:
        from config import auto_localize_timestamps
        return auto_localize_timestamps(df)
    except Exception:
        return df  # never break a read because of a display helper

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
    """Return a SQLite connection. Pass db_file=':memory:' for in-memory testing.

    Cloud filesystems (Streamlit Community, NFS) sometimes reject WAL mode.
    Each PRAGMA is attempted independently so one failure never blocks the
    rest. A separate corruption probe deletes and recreates the file only
    when the schema bytes themselves are unreadable.
    """
    import os
    target = db_file or DB_FILE

    conn = sqlite3.connect(target, check_same_thread=False, timeout=30)

    if target == ":memory:":
        return conn

    # PRAGMAs are best-effort — WAL may be unsupported on some cloud FS.
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA busy_timeout=30000",
    ):
        try:
            conn.execute(pragma)
        except sqlite3.DatabaseError:
            pass

    # Corruption probe — distinct from PRAGMA failures.
    # A fresh empty file returns no rows here (not an error).
    try:
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.DatabaseError:
        conn.close()
        try:
            os.remove(target)
        except OSError:
            pass
        conn = sqlite3.connect(target, check_same_thread=False, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.DatabaseError:
            pass

    return conn


# ---------------------------------------------------------------------------
# PostgreSQL migration — Phase 1 engine seam (see docs/POSTGRES_MIGRATION.md)
# ---------------------------------------------------------------------------
# This adds a SQLAlchemy Engine *alongside* the existing raw-sqlite3 path with
# ZERO behavior change: get_connection() above is untouched and remains the
# runtime path. The engine is only built when get_engine() is explicitly
# called, and SQLAlchemy is imported lazily so environments without it (or
# without psycopg2) keep running exactly as before. Later phases migrate
# callers onto the engine; Phase 1 just establishes the seam + URL resolution.
_ENGINES: dict = {}


def get_database_url() -> str:
    """Resolve the SQLAlchemy database URL.

    `DATABASE_URL` (env) wins — e.g. ``postgresql+psycopg2://user:pw@host/db``.
    Otherwise we derive a SQLite URL from DB_FILE, so the default behavior is
    identical to today (same file, same data).
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    target = DB_FILE
    if target == ":memory:":
        return "sqlite://"
    return "sqlite:///" + os.path.abspath(target)


def get_engine(url: str = None):
    """Lazily build (and cache) a SQLAlchemy Engine for `url` (default:
    get_database_url()). NOT yet used by the runtime — Phase 1 seam only.

    Raises a clear RuntimeError if SQLAlchemy isn't installed, so the optional
    dependency never breaks module import or the existing sqlite3 path.
    """
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "SQLAlchemy is not installed. Install it with "
            "`pip install SQLAlchemy` to use the Postgres engine seam."
        ) from exc
    u = url or get_database_url()
    if u not in _ENGINES:
        if u.startswith("sqlite"):
            # Mirror get_connection()'s threading posture for parity.
            _ENGINES[u] = create_engine(
                u, future=True,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
        else:
            # Postgres/other: pooled connections for the Streamlit + worker
            # threads, with liveness checks (server phases).
            _ENGINES[u] = create_engine(
                u, future=True, pool_pre_ping=True,
                pool_size=5, max_overflow=10,
            )
    return _ENGINES[u]


# ---------------------------------------------------------------------------
# Phase 2 — dialect-portability helpers (see docs/POSTGRES_MIGRATION.md)
# ---------------------------------------------------------------------------
# Small primitives that emit the right SQL for the active dialect so the
# SQLite-isms (PRAGMA self-heal, date('now'), julianday) can be expressed
# portably. These are ADDITIVE: they emit *identical* behavior on SQLite, so
# adopting them never changes the current app. Existing call sites migrate to
# them incrementally and are validated against real Postgres under the dual-CI
# phase before any cutover.
def db_dialect(conn=None) -> str:
    """'sqlite' or 'postgresql' — the active/target dialect.

    A raw sqlite3 connection is always 'sqlite'. Otherwise we read the resolved
    DATABASE_URL (so callers can ask the target dialect before connecting).
    """
    if conn is not None and isinstance(conn, sqlite3.Connection):
        return "sqlite"
    return "postgresql" if get_database_url().startswith(
        ("postgresql", "postgres")) else "sqlite"


def column_exists(table: str, col: str, conn: sqlite3.Connection = None) -> bool:
    """Portable 'does this column exist?' — SQLite uses PRAGMA table_info;
    Postgres uses information_schema. Replaces the repeated PRAGMA self-heal
    probe without changing its SQLite result."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if isinstance(conn, sqlite3.Connection):
            return any(r[1] == col
                       for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
        # SQLAlchemy connection (Postgres / other)
        from sqlalchemy import text
        return conn.execute(
            text("SELECT 1 FROM information_schema.columns "
                 "WHERE table_name=:t AND column_name=:c"),
            {"t": table, "c": col},
        ).first() is not None
    finally:
        if _owns:
            conn.close()


def now_sql() -> str:
    """SQL for 'right now' — standard, identical on both dialects."""
    return "CURRENT_TIMESTAMP"


def days_ago_sql(days: int, dialect: str = None) -> str:
    """SQL expression for the date `days` before today.

    SQLite:    date('now','-7 days')
    Postgres:  (CURRENT_DATE - INTERVAL '7 days')
    """
    d = dialect or db_dialect()
    n = int(days)
    if d == "postgresql":
        return f"(CURRENT_DATE - INTERVAL '{n} days')"
    return f"date('now','-{n} days')"


def date_diff_days_sql(later: str, earlier: str, dialect: str = None) -> str:
    """SQL expression for the integer day difference (later − earlier).

    SQLite:    CAST(julianday(later) - julianday(earlier) AS INTEGER)
    Postgres:  (date(later) - date(earlier))
    """
    d = dialect or db_dialect()
    if d == "postgresql":
        return f"(date({later}) - date({earlier}))"
    return f"CAST(julianday({later}) - julianday({earlier}) AS INTEGER)"


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
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("returnable_items", "whatsapp_alert_sent", conn=conn):
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
    # Self-heal: per-site dropdown support
    try:
        c.execute("ALTER TABLE system_settings ADD COLUMN Site_ID TEXT")
    except Exception:
        pass

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

    # ── Stock reservations (earmark on approved cross-site transfers) ─────────
    # An approved `requests` row earmarks stock at the TARGET site (the one
    # holding the stock) until the transfer is fulfilled/rejected. This NEVER
    # changes Current_Stock (identity math is untouched); it powers a derived
    # Available = Current − Reserved figure and an advisory warning.
    c.execute("""
        CREATE TABLE IF NOT EXISTS stock_reservations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            SAP_Code    TEXT NOT NULL,
            Site_ID     TEXT NOT NULL,
            Qty         REAL NOT NULL,
            request_id  INTEGER,
            status      TEXT DEFAULT 'active'
                             CHECK(status IN ('active','released')),
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            released_at DATETIME
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_resv_sap_site "
              "ON stock_reservations(SAP_Code, Site_ID, status)")

    # ── UoM pack conversions (base-UoM model) ─────────────────────────────────
    # The ledger always stores the item's BASE UoM (inventory.UOM). This table
    # is a data-entry aid only: 1 <Pack_UOM> = <Factor> base units (e.g.
    # 1 Box = 100 Pcs). Receiving "5 Box" writes 500 base units to receipts —
    # v_site_stock never reads this table, so identity math is untouched.
    c.execute("""
        CREATE TABLE IF NOT EXISTS uom_conversions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            SAP_Code   TEXT NOT NULL,
            Pack_UOM   TEXT NOT NULL,
            Factor     REAL NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(SAP_Code, Pack_UOM)
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
    # 2FA (TOTP) — opt-in per user. totp_secret holds the base32 shared secret;
    # totp_enabled flips to 1 only after the user confirms a code at enrollment.
    if "totp_secret" not in usr_cols:
        c.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    if "totp_enabled" not in usr_cols:
        c.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0")
        
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("pending_users", "Phone_Number", conn=conn):
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
    # Self-heal: error_message + attempts so the admin console can show why a
    # message landed in 'failed' and we can retry it without losing context.
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("whatsapp_queue", "error_message", conn=conn):
        c.execute("ALTER TABLE whatsapp_queue ADD COLUMN error_message TEXT")
    if not column_exists("whatsapp_queue", "attempts", conn=conn):
        c.execute("ALTER TABLE whatsapp_queue ADD COLUMN attempts INTEGER DEFAULT 0")

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
    # Phase 8A — Smart Scan AI sidecar gate. Default OFF (0).
    # When ON, the Smart Scan Tier-3 path POSTs frames to the sidecar URL
    # below. Off means the existing two-tier YOLO flow is unchanged.
    for _k, _v in [("low_stock_days", "5"),
                   ("burn_alert_days", "7"),
                   ("expiry_warn_days", "30"),
                   ("locate_anything_enabled",     "0"),
                   ("locate_anything_sidecar_url", "http://127.0.0.1:8503")]:
        c.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (_k, _v),
        )

    # ── pending_receipts: workflow_state (allows 'rejected' for HOD UI) ─
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("pending_receipts", "rejection_reason", conn=conn):
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

    # Lot split / merge — within-SAP, lot-to-lot reclassification. Recorded
    # here (NOT as movement-ledger rows) so the receipts/consumption ledger
    # stays append-only and gross totals aren't inflated. v_lot_balance below
    # subtracts transfers_out and adds transfers_in, so per-lot balances move
    # while the SAP's Current_Stock is unchanged (a transfer nets to zero).
    c.execute("""
        CREATE TABLE IF NOT EXISTS lot_transfers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            From_Lot    TEXT NOT NULL,
            To_Lot      TEXT NOT NULL,
            SAP_Code    TEXT NOT NULL,
            Site_ID     TEXT DEFAULT 'HQ',
            Qty         REAL NOT NULL,
            kind        TEXT DEFAULT 'split' CHECK(kind IN ('split','merge')),
            by_user     TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
    # Self-heal: link a disposal adjustment back to the lot it writes off, so
    # approval can tag the write-off consumption with the lot AND flip the lot
    # to 'disposed' (NULL for ordinary cycle-count adjustments).
    # Phase 2 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("stock_adjustments", "Lot_Number", conn=conn):
        c.execute("ALTER TABLE stock_adjustments ADD COLUMN Lot_Number TEXT")

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
    # The full set the SK staging form / OCR / commit_pending_receipts can
    # carry forward. Missing any of these caused process_receipt_delivery
    # to silently fail (caught by its try/except) — dropping SK input.
    c.execute("PRAGMA table_info(receipts)")
    rec_cols = {row[1] for row in c.fetchall()}
    for col in [
        "Supplier", "Expiry_Date", "PR_Number",
        "Serial_No", "PR", "Location", "Vehicle_No", "Driver_Name",
        "DN_No", "Pallet_No", "Mob_From", "Prepared_by", "Mob_To",
        "Received_by", "DN_Copy",
        # Bin/shelf put-away tag (lightweight location tracking; metadata only,
        # never read by v_site_stock). Distinct from logistics 'Location'
        # (the delivery location on the DN).
        "Bin_Location",
    ]:
        if col not in rec_cols:
            c.execute(f"ALTER TABLE receipts ADD COLUMN {col} TEXT")

    # ── Self-Healing: Mirror all logistics columns into pending_receipts ───────
    c.execute("PRAGMA table_info(pending_receipts)")
    _prc_cols = {row[1] for row in c.fetchall()}
    for _col in ["Serial_No", "PR", "Location", "Vehicle_No", "Driver_Name",
                 "DN_No", "Pallet_No", "Mob_From", "Prepared_by", "Mob_To",
                 "Received_by", "DN_Copy", "Supplier", "PR_Number", "Expiry_Date",
                 "Bin_Location"]:
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

    # ── Seed Default Work Types (idempotent, global / Site_ID IS NULL) ────────
    c.execute("SELECT count(*) FROM system_settings WHERE category='Work_Type' AND Site_ID IS NULL")
    if c.fetchone()[0] == 0:
        for wt in ["Maintenance", "New Project Area", "Fabrication", "Office"]:
            c.execute(
                "INSERT INTO system_settings (category, value) VALUES ('Work_Type', ?)",
                (wt,)
            )

    # ── Seed Default Tank Numbers (global placeholder) ─────────────────────
    c.execute("SELECT count(*) FROM system_settings WHERE category='Tank_No' AND Site_ID IS NULL")
    if c.fetchone()[0] == 0:
        for tn in ["Tank 1", "Tank 2", "Tank 3"]:
            c.execute(
                "INSERT INTO system_settings (category, value) VALUES ('Tank_No', ?)",
                (tn,)
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

    # ── Phase B (2026-06 round 3): WBS workflow ─────────────────────────────
    # `wbs_master` mirrors pr_master's per-site pattern. HOD/Admin creates
    # the allowed WBS numbers for a site; SK picks from a dropdown when
    # staging consumption / receipts so the WBS field is auditable.
    c.execute("""
        CREATE TABLE IF NOT EXISTS wbs_master (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            WBS_Number  TEXT    NOT NULL,
            Description TEXT,
            Site_ID     TEXT    NOT NULL DEFAULT 'HQ',
            status      TEXT    DEFAULT 'active' CHECK(status IN ('active','closed')),
            created_by  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(WBS_Number, Site_ID)
        )
    """)
    # Self-heal: WBS column on every transaction table (the SK enters it
    # in the entry log, HOD commit propagates it through, reports group on it).
    for _tbl in ("consumption", "receipts", "pending_issues", "pending_receipts"):
        c.execute(f"PRAGMA table_info({_tbl})")
        _cols = {r[1] for r in c.fetchall()}
        if "wbs" not in _cols and "WBS_Number" not in _cols:
            try:
                c.execute(f"ALTER TABLE {_tbl} ADD COLUMN wbs TEXT")
            except sqlite3.OperationalError:
                pass

    # ── Phase A: Category + Opening_Stock columns on inventory
    c.execute("PRAGMA table_info(inventory)")
    _inv_cols2 = {row[1] for row in c.fetchall()}
    if "Category" not in _inv_cols2:
        c.execute("ALTER TABLE inventory ADD COLUMN Category TEXT DEFAULT 'Others'")
    if "Opening_Stock" not in _inv_cols2:
        c.execute("ALTER TABLE inventory ADD COLUMN Opening_Stock REAL DEFAULT 0")

    # ── Phase A: QR Approval Requests (SK submits, HOD approves)
    c.execute("""
        CREATE TABLE IF NOT EXISTS qr_approval_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID         TEXT    NOT NULL,
            SAP_Code        TEXT    NOT NULL,
            Material_Code   TEXT,
            Equipment_Description TEXT,
            Quantity        INTEGER DEFAULT 1,
            requested_by    TEXT    NOT NULL,
            requested_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            status          TEXT    DEFAULT 'pending'
                            CHECK(status IN ('pending','approved','rejected')),
            approved_by     TEXT,
            approved_at     DATETIME,
            rejection_reason TEXT
        )
    """)

    # ── Phase A: Entry attachments (BLOB + uploads/ mirror path)
    # Linked to a movement table row OR a date+site (per-date attachments).
    # entry_table is one of: 'pending_issues','pending_receipts','returnable_items'.
    c.execute("""
        CREATE TABLE IF NOT EXISTS entry_attachments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID         TEXT    NOT NULL,
            doc_type        TEXT    NOT NULL
                            CHECK(doc_type IN ('consumption','receipt','return')),
            doc_number      TEXT    NOT NULL,
            entry_table     TEXT,
            entry_id        INTEGER,
            entry_date      TEXT,
            file_name       TEXT    NOT NULL,
            mime_type       TEXT,
            file_size       INTEGER,
            file_blob       BLOB,
            disk_path       TEXT,
            uploaded_by     TEXT    NOT NULL,
            uploaded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Phase B (2026-06 round 2): pending_returns — SK stages return,
    # HOD approves → commits to `returns` table (which drives Closing_Stock
    # and the dashboard Return column).
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_returns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID         TEXT    NOT NULL,
            SAP_Code        TEXT    NOT NULL,
            Material_Code   TEXT,
            Equipment_Description TEXT,
            Quantity        REAL    NOT NULL,
            Return_Reason   TEXT    NOT NULL,
            Return_DN_No    TEXT    NOT NULL,
            received_date   TEXT,
            received_dn_no  TEXT,
            received_qty    REAL,
            PR_Number       TEXT,
            Lot_Number      TEXT,
            override_required INTEGER DEFAULT 0,
            override_reason  TEXT,
            status          TEXT    DEFAULT 'pending_hod'
                            CHECK(status IN ('pending_hod','approved','rejected')),
            submitted_by    TEXT    NOT NULL,
            submitted_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved_by     TEXT,
            approved_at     DATETIME,
            rejection_reason TEXT
        )
    """)

    # ── returns_history — terminal archive for HOD-rejected pending returns ──
    # Backlog #20: rejected pending_returns rows otherwise accumulate forever.
    # The admin "Cleanup rejected returns" button copies rows here (copy-then-
    # delete, mirroring rejected_issues_archive) so pending_returns stays lean
    # while the audit trail is preserved. These rows never touched the `returns`
    # ledger (only HOD approval writes there), so archiving has zero effect on
    # stock identity math.
    c.execute("""
        CREATE TABLE IF NOT EXISTS returns_history (
            archive_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id      INTEGER,
            Site_ID          TEXT,
            SAP_Code         TEXT,
            Material_Code    TEXT,
            Equipment_Description TEXT,
            Quantity         REAL,
            Return_Reason    TEXT,
            Return_DN_No     TEXT,
            received_date    TEXT,
            received_dn_no   TEXT,
            received_qty     REAL,
            PR_Number        TEXT,
            Lot_Number       TEXT,
            override_required INTEGER,
            override_reason  TEXT,
            status           TEXT,
            submitted_by     TEXT,
            submitted_at     DATETIME,
            approved_by      TEXT,
            approved_at      DATETIME,
            rejection_reason TEXT,
            archived_by      TEXT,
            archived_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_returns_history_site "
        "ON returns_history(Site_ID, archived_at)"
    )

    # ── Phase A: MTC documents for rubber materials
    # Captured at SK submit time. file_blob optional (logistics flow when missing).
    c.execute("""
        CREATE TABLE IF NOT EXISTS mtc_documents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID         TEXT    NOT NULL,
            SAP_Code        TEXT    NOT NULL,
            Material_Code   TEXT,
            Lot_Number      TEXT,
            Quantity        REAL,
            mtc_number      TEXT,
            file_name       TEXT,
            mime_type       TEXT,
            file_blob       BLOB,
            disk_path       TEXT,
            status          TEXT    DEFAULT 'attached'
                            CHECK(status IN ('attached','missing','sent_to_logistics')),
            pending_receipt_id INTEGER,
            submitted_by    TEXT    NOT NULL,
            submitted_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            logistics_emailed_at DATETIME
        )
    """)

    # ════════════════════════════════════════════════════════════════════════
    # ── Phase C (Procurement chain) — Logistics + Warehouse portals ─────────
    # ════════════════════════════════════════════════════════════════════════
    # The chain is:
    #   Site HOD creates PR  →  Logistics issues PO(s) against PR
    #                        →  Logistics assigns PO items to a Warehouse
    #                        →  Warehouse receives + prepares Delivery Notes
    #                        →  Logistics approves DN delivery date
    #                        →  Site HOD approves DN → Site SK confirms receipt
    # All movement still funnels into the existing `receipts` ledger at the
    # final step, so identity math is unchanged.

    # warehouses — physical receiving locations (Factory / Yard / Hub).
    c.execute("""
        CREATE TABLE IF NOT EXISTS warehouses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            Warehouse_ID  TEXT    UNIQUE NOT NULL,
            Name          TEXT    NOT NULL,
            Location      TEXT,
            Contact_Name  TEXT,
            Contact_Phone TEXT,
            Contact_Email TEXT,
            status        TEXT    DEFAULT 'active'
                          CHECK(status IN ('active','inactive')),
            created_by    TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # vendors — supplier master, auto-fills PO creation form.
    c.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Vendor_Code     TEXT    UNIQUE NOT NULL,
            Vendor_Name     TEXT    NOT NULL,
            Address         TEXT,
            Contact_Name    TEXT,
            Contact_Phone   TEXT,
            Contact_Email   TEXT,
            Default_Inco_Terms    TEXT,
            Default_Payment_Terms TEXT,
            status          TEXT    DEFAULT 'active'
                            CHECK(status IN ('active','inactive')),
            created_by      TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # purchase_orders — one row per PO header. PO_Number is a free-text
    # captured by Logistics; in production it's a 10-digit numeric string
    # (e.g. '4720002930'). Samples mask the last 4 with X for security but
    # the column accepts both. PR_Number is the linked PR; one PR can spawn
    # many POs (split buys). source = 'manual' | 'pdf_upload'.
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number           TEXT    NOT NULL,
            PR_Number           TEXT,
            Site_ID             TEXT,
            Vendor_Code         TEXT,
            Vendor_Name         TEXT,
            Inco_Terms          TEXT,
            Payment_Terms       TEXT,
            PO_Date             TEXT,
            PO_Type             TEXT,
            Quotation_No        TEXT,
            Quotation_Date      TEXT,
            Your_Reference      TEXT,
            Our_Reference       TEXT,
            Contact_Person      TEXT,
            Contact_Email       TEXT,
            Mobile              TEXT,
            Our_Email           TEXT,
            Expected_Delivery   TEXT,
            Freight_Charges     REAL    DEFAULT 0,
            Handling_Charges    REAL    DEFAULT 0,
            Discount_Amount     REAL    DEFAULT 0,
            Total_Amount        REAL    DEFAULT 0,
            Amount_In_Words     TEXT,
            source              TEXT    DEFAULT 'manual'
                                CHECK(source IN ('manual','pdf_upload')),
            attachment_blob     BLOB,
            attachment_name     TEXT,
            attachment_mime     TEXT,
            status              TEXT    DEFAULT 'open'
                                CHECK(status IN ('open','partially_delivered','delivered','closed','force_closed','cancelled')),
            created_by          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at           DATETIME,
            closed_by           TEXT,
            close_reason        TEXT,
            UNIQUE(PO_Number)
        )
    """)

    # po_items — one row per PO line. SAP_Code intentionally absent at this
    # layer — Logistics works with Material_Code only; SAP_Code joins back
    # at the site receipt step. rl_bl_family is set by config.classify_rl_bl_family
    # at insert time and locks the line into its own splitter group.
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_items (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number         TEXT    NOT NULL,
            line_no           INTEGER,
            Material_Code     TEXT,
            Description       TEXT,
            Qty               REAL    NOT NULL,
            UOM               TEXT,
            Unit_Price        REAL    DEFAULT 0,
            Total_Price       REAL    DEFAULT 0,
            PR_Number         TEXT,
            WBS_Number        TEXT,
            Network           TEXT,
            Plant             TEXT,
            rl_bl_family      TEXT,   -- 'RL' | 'BL' | NULL — strict separation flag
            Delivered_Qty     REAL    DEFAULT 0,
            Returned_Qty      REAL    DEFAULT 0,
            line_status       TEXT    DEFAULT 'open'
                              CHECK(line_status IN ('open','partially_delivered','delivered','returned','closed','force_closed')),
            close_reason      TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # po_shipment_schedule — parses the "PO Annexure: Delivery Schedule"
    # block from sample POs into structured rows for reminder scheduling.
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_shipment_schedule (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number       TEXT    NOT NULL,
            shipment_no     TEXT,
            material_group  TEXT,
            target_date     TEXT,
            actual_date     TEXT,
            status          TEXT    DEFAULT 'pending'
                            CHECK(status IN ('pending','shipped','delivered','delayed','cancelled')),
            notes           TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # po_assignments — Logistics → Warehouse routing for a PO.
    # items_subset_json is a JSON list of po_items.id values; NULL means
    # "all items on the PO". When the Warehouse views the assignment, it
    # always sees prices blanked (enforced at the query layer, not stored).
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_assignments (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number           TEXT    NOT NULL,
            Warehouse_ID        TEXT    NOT NULL,
            items_subset_json   TEXT,
            Expected_Delivery   TEXT,
            assigned_by         TEXT    NOT NULL,
            assigned_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            acknowledged_at     DATETIME,
            acknowledged_by     TEXT,
            status              TEXT    DEFAULT 'assigned'
                                CHECK(status IN ('assigned','acknowledged','received','partial','closed','cancelled')),
            notes               TEXT
        )
    """)

    # delivery_notes — Warehouse → Site DN header. One PO may produce many
    # DNs (split by site / date / RL-vs-BL family). HOD approves the DN
    # before it lands on the SK's pending receipts.
    c.execute("""
        CREATE TABLE IF NOT EXISTS delivery_notes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            DN_Number          TEXT    UNIQUE NOT NULL,
            PO_Number          TEXT    NOT NULL,
            Warehouse_ID       TEXT    NOT NULL,
            Site_ID            TEXT    NOT NULL,
            rl_bl_family       TEXT,   -- locks the DN to a single family if non-null
            DN_Date            TEXT,
            Vehicle_No         TEXT,
            Driver_Name        TEXT,
            Driver_Phone       TEXT,
            Prepared_By        TEXT,
            Remarks            TEXT,
            status             TEXT    DEFAULT 'draft'
                               CHECK(status IN ('draft','pending_logistics','logistics_approved','pending_hod','hod_approved','pending_sk','received','rejected','cancelled')),
            logistics_decided_at  DATETIME,
            logistics_decided_by  TEXT,
            logistics_decision    TEXT,
            hod_decided_at        DATETIME,
            hod_decided_by        TEXT,
            sk_received_at        DATETIME,
            sk_received_by        TEXT,
            rejection_reason      TEXT,
            created_by         TEXT    NOT NULL,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # dn_items — DN line items. References po_items.id so we can trace
    # back to the original PO line and reduce the open balance. The
    # warehouse can edit Qty in the DN draft before sending for HOD approval.
    c.execute("""
        CREATE TABLE IF NOT EXISTS dn_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            DN_Number       TEXT    NOT NULL,
            po_item_id      INTEGER NOT NULL,
            Material_Code   TEXT,
            Description     TEXT,
            Qty             REAL    NOT NULL,
            UOM             TEXT,
            Lot_Number      TEXT,
            Expiry_Date     TEXT,
            Remarks         TEXT,
            rl_bl_family    TEXT,
            sk_received_qty REAL,
            status          TEXT    DEFAULT 'pending'
                            CHECK(status IN ('pending','received','partial','returned','cancelled')),
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # po_returns — Logistics / Warehouse / Site can raise a return. Returns
    # reopen the linked po_item (line_status flips back to 'partially_delivered'
    # or 'open' depending on Returned_Qty vs Delivered_Qty).
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_returns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number           TEXT    NOT NULL,
            po_item_id          INTEGER,
            DN_Number           TEXT,
            Material_Code       TEXT,
            Qty                 REAL    NOT NULL,
            Reason              TEXT    NOT NULL,
            raised_by_role      TEXT    NOT NULL
                                CHECK(raised_by_role IN ('logistics','warehouse_user','hod','store_keeper','admin')),
            raised_by           TEXT    NOT NULL,
            raised_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
            Expected_Resupply   TEXT,
            status              TEXT    DEFAULT 'open'
                                CHECK(status IN ('open','vendor_acknowledged','resupplied','cancelled')),
            closed_at           DATETIME,
            closed_by           TEXT,
            notes               TEXT
        )
    """)

    # po_reschedule_requests — Warehouse / Site HOD → Logistics. Logistics
    # approves with a new Expected_Delivery date or denies with a reason.
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_reschedule_requests (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            PO_Number           TEXT    NOT NULL,
            DN_Number           TEXT,
            current_date        TEXT,
            requested_date      TEXT    NOT NULL,
            reason              TEXT    NOT NULL,
            requested_by_role   TEXT    NOT NULL
                                CHECK(requested_by_role IN ('warehouse_user','hod','admin')),
            requested_by        TEXT    NOT NULL,
            requested_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            status              TEXT    DEFAULT 'pending'
                                CHECK(status IN ('pending','approved','rejected')),
            decided_by          TEXT,
            decided_at          DATETIME,
            decision_notes      TEXT
        )
    """)

    # po_force_closures — audit row written every time Logistics force-closes
    # a PR, PO, or specific line. Surfaced to Admin + originating Site HOD
    # per user spec.
    c.execute("""
        CREATE TABLE IF NOT EXISTS po_force_closures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type     TEXT    NOT NULL
                            CHECK(target_type IN ('pr','po','po_item')),
            target_ref      TEXT    NOT NULL,  -- PR_Number / PO_Number / po_items.id
            Site_ID         TEXT,
            PR_Number       TEXT,
            PO_Number       TEXT,
            reason          TEXT    NOT NULL,
            closed_by       TEXT    NOT NULL,
            closed_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes           TEXT
        )
    """)

    # delivery_reminders_sent — idempotency log for the T-2/T-1/T-0 sweep.
    # The UNIQUE constraint stops duplicate fires for the same (ref, offset)
    # even if the worker is restarted mid-day.
    c.execute("""
        CREATE TABLE IF NOT EXISTS delivery_reminders_sent (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_type    TEXT    NOT NULL CHECK(ref_type IN ('po','dn')),
            ref_number  TEXT    NOT NULL,
            target_date TEXT    NOT NULL,
            offset_days INTEGER NOT NULL,
            fired_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ref_type, ref_number, target_date, offset_days)
        )
    """)

    # Phase 6E — widen delivery_reminders_sent so the same table can dedup
    # the new 'returnable_loan' reminder events too. The original schema had
    # CHECK(ref_type IN ('po','dn')) which blocks the new ref_type. We
    # rebuild the table without the CHECK if (and only if) the probe insert
    # fails — idempotent + no data loss.
    try:
        c.execute(
            "INSERT INTO delivery_reminders_sent "
            "(ref_type, ref_number, target_date, offset_days) "
            "VALUES ('__probe_returnable_loan__', '__probe__', '1970-01-01', -9999)"
        )
        c.execute(
            "DELETE FROM delivery_reminders_sent WHERE ref_number = '__probe__'"
        )
    except sqlite3.IntegrityError:
        # CHECK constraint is still narrow → rebuild.
        c.execute("ALTER TABLE delivery_reminders_sent RENAME TO _delivery_reminders_sent_old")
        c.execute("""
            CREATE TABLE delivery_reminders_sent (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_type    TEXT    NOT NULL,
                ref_number  TEXT    NOT NULL,
                target_date TEXT    NOT NULL,
                offset_days INTEGER NOT NULL,
                fired_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ref_type, ref_number, target_date, offset_days)
            )
        """)
        c.execute(
            "INSERT INTO delivery_reminders_sent "
            "(ref_type, ref_number, target_date, offset_days, fired_at) "
            "SELECT ref_type, ref_number, target_date, offset_days, fired_at "
            "FROM _delivery_reminders_sent_old"
        )
        c.execute("DROP TABLE _delivery_reminders_sent_old")

    # app_notifications — in-app inbox (bell icon in sidebar). The same event
    # may also fire a WhatsApp via WHATSAPP_TRIGGERS in config.py; in-app
    # notifications always fire regardless of WhatsApp toggle.
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_user  TEXT,           -- exact username; OR
            recipient_role  TEXT,           -- broadcast to all users of role; OR
            recipient_site  TEXT,           -- combined with role for site-scoped
            recipient_warehouse TEXT,       -- combined with role for warehouse-scoped
            event_key       TEXT    NOT NULL,
            severity        TEXT    DEFAULT 'info'
                            CHECK(severity IN ('info','warning','critical','success')),
            title           TEXT    NOT NULL,
            body            TEXT,
            link_page       TEXT,           -- which sidebar page to deep-link
            link_anchor     TEXT,           -- tab/section anchor inside the page
            related_table   TEXT,
            related_ref     TEXT,
            read_at         DATETIME,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Self-heal: pr_master extensions for procurement chain ─────────────
    # WBS_Number / Network / Plant / Delivery_Date carry across from the
    # PR PDF columns we already extract. submitted_to_logistics_at and
    # logistics_status drive the new "submit to Logistics" button + queue.
    c.execute("PRAGMA table_info(pr_master)")
    _pr_cols2 = {row[1] for row in c.fetchall()}
    for _col, _ddl in [
        ("WBS_Number",                "TEXT"),
        ("Network",                   "TEXT"),
        ("Plant",                     "TEXT"),
        ("Delivery_Date",             "TEXT"),
        ("submitted_to_logistics_at", "DATETIME"),
        ("submitted_to_logistics_by", "TEXT"),
        ("logistics_status",          "TEXT DEFAULT 'site_draft'"),
        # site_draft → submitted → in_po → closed | force_closed
    ]:
        if _col not in _pr_cols2:
            try:
                c.execute(f"ALTER TABLE pr_master ADD COLUMN {_col} {_ddl}")
            except sqlite3.OperationalError:
                pass

    # ── Self-heal: receipts carry the upstream DN/PO/Warehouse refs ───────
    # These let the Site SK Receipt Staging show a "🚚 from warehouse" chip
    # and let auditors trace a stock movement back to its originating PO.
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    for _col in ("DN_Number", "Warehouse_ID", "PO_Number_Source"):
        if not column_exists("receipts", _col, conn=conn):
            try:
                c.execute(f"ALTER TABLE receipts ADD COLUMN {_col} TEXT")
            except sqlite3.OperationalError:
                pass
    for _col in ("DN_Number", "Warehouse_ID", "PO_Number_Source"):
        if not column_exists("pending_receipts", _col, conn=conn):
            try:
                c.execute(f"ALTER TABLE pending_receipts ADD COLUMN {_col} TEXT")
            except sqlite3.OperationalError:
                pass

    # ── Self-heal: users.Warehouse_ID for warehouse_user role scoping ─────
    c.execute("PRAGMA table_info(users)")
    _usr_cols2 = {row[1] for row in c.fetchall()}
    if "Warehouse_ID" not in _usr_cols2:
        try:
            c.execute("ALTER TABLE users ADD COLUMN Warehouse_ID TEXT")
        except sqlite3.OperationalError:
            pass
    c.execute("PRAGMA table_info(pending_users)")
    _pu_cols2 = {row[1] for row in c.fetchall()}
    if "Warehouse_ID" not in _pu_cols2:
        try:
            c.execute("ALTER TABLE pending_users ADD COLUMN Warehouse_ID TEXT")
        except sqlite3.OperationalError:
            pass

    # ── Self-heal: users role CHECK — add logistics + warehouse_user ──────
    # Mirrors the worker→store_keeper upgrade block above. We only rebuild
    # if the new roles are missing from the CHECK constraint.
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
    _users_sql_row = c.fetchone()
    _users_sql = (_users_sql_row[0] if _users_sql_row else "") or ""
    if ("'logistics'" not in _users_sql) or ("'warehouse_user'" not in _users_sql):
        try:
            c.execute("PRAGMA foreign_keys=off;")
            c.execute("ALTER TABLE users RENAME TO _users_old2;")
            c.execute("""
                CREATE TABLE users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL CHECK(role IN
                                   ('admin','logistics','hod','warehouse_user','supervisor','store_keeper')),
                    Site_ID       TEXT DEFAULT 'HQ',
                    Warehouse_ID  TEXT,
                    Phone_Number  TEXT,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Detect which columns existed on the old table so we copy a safe subset.
            c.execute("PRAGMA table_info(_users_old2)")
            _old_cols = {r[1] for r in c.fetchall()}
            _has_wh = "Warehouse_ID" in _old_cols
            _wh_col = "Warehouse_ID" if _has_wh else "NULL"
            c.execute(f"""
                INSERT INTO users (id, username, password_hash, role, Site_ID, Warehouse_ID, Phone_Number, created_at)
                SELECT id, username, password_hash, role, Site_ID, {_wh_col}, Phone_Number, created_at
                FROM _users_old2
            """)
            c.execute("DROP TABLE _users_old2;")
            c.execute("PRAGMA foreign_keys=on;")
        except sqlite3.OperationalError:
            # Never block init on the rebuild — older constraints still accept
            # the existing seed roles; the new roles will simply fail to insert
            # until the rebuild succeeds on next startup.
            pass

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

    # ── Phase 6A: CV foundation (employees, tool_catalogue, cv_model_versions) ──
    # Pure schema. No UI yet. CRUD helpers live at the bottom of this file in
    # the "Phase 6A helpers" section. Tables here are additive only.
    c.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ID_Number     TEXT UNIQUE NOT NULL,
            Name          TEXT NOT NULL,
            Phone_Number  TEXT,
            Department    TEXT,
            status        TEXT DEFAULT 'active'
                          CHECK(status IN ('active','inactive','suspended')),
            created_by    TEXT,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS cv_model_versions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            version      TEXT UNIQUE NOT NULL,
            model_path   TEXT NOT NULL,
            classes_json TEXT NOT NULL,
            mAP          REAL,
            trained_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active    INTEGER DEFAULT 0
        )
    """)
    # Partial unique index — guarantees AT MOST one active model version at a
    # time. promote_cv_model_version() handles the demote-then-promote dance
    # atomically inside a single transaction.
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_cv_models_active "
        "ON cv_model_versions(is_active) WHERE is_active = 1"
    )

    c.execute("""
        CREATE TABLE IF NOT EXISTS tool_catalogue (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name        TEXT UNIQUE NOT NULL,
            display_name      TEXT NOT NULL,
            category          TEXT,
            model_version_id  INTEGER REFERENCES cv_model_versions(id),
            min_confidence    REAL DEFAULT 0.75,
            created_by        TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Self-heal: 4 CV-audit cols on returnable_items so existing loans get
    # NULL/0 defaults and the Smart Scan flow (Phase 6D) has somewhere to write.
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    for _col, _ddl in (
        ("cv_detected",    "INTEGER DEFAULT 0"),
        ("cv_confidence",  "REAL"),
        ("cv_employee_id", "TEXT"),
        ("cv_tool_class",  "TEXT"),
    ):
        if not column_exists("returnable_items", _col, conn=conn):
            c.execute(f"ALTER TABLE returnable_items ADD COLUMN {_col} {_ddl}")

    # ── Phase 7A: Employee Site Binding ──────────────────────────────────────
    # Self-heal Site_ID on employees so the Supervisor Material Request flow
    # (Phase 7B) can filter the worker dropdown to the supervisor's site.
    # NULL = legacy / unassigned; Admin backfills via the bulk-assign widget.
    if not column_exists("employees", "Site_ID", conn=conn):
        c.execute("ALTER TABLE employees ADD COLUMN Site_ID TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS ix_employees_site ON employees(Site_ID)")

    # ── Phase 7B: Supervisor Material Request Workflow ───────────────────────
    # Two new tables (Intent) + Source_Ref self-heal on the consumption ledger
    # (Actual). The SK approval helper mirrors approved lines into pending_issues
    # so commit_eod() and the negative-stock guard handle the rest unchanged.
    c.execute("""
        CREATE TABLE IF NOT EXISTS supervisor_material_requests (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            request_no           TEXT UNIQUE,
            Site_ID              TEXT NOT NULL,
            Worker_ID            TEXT NOT NULL,
            Worker_Name          TEXT NOT NULL,
            Job_Tank_Place       TEXT NOT NULL,
            Old_PPE_Returned     INTEGER NOT NULL,
            No_Return_Reason     TEXT,
            requested_by         TEXT NOT NULL,
            requested_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            status               TEXT NOT NULL DEFAULT 'pending_sk'
                                 CHECK(status IN ('pending_sk','approved','rejected','cancelled')),
            sk_decided_by        TEXT,
            sk_decided_at        DATETIME,
            sk_reject_reason     TEXT,
            posted_pending_ids   TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_smr_site_status "
              "ON supervisor_material_requests(Site_ID, status)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_smr_requested_at "
              "ON supervisor_material_requests(requested_at)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS supervisor_material_request_items (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id            INTEGER NOT NULL
                                  REFERENCES supervisor_material_requests(id),
            SAP_Code              TEXT NOT NULL,
            Material_Code         TEXT,
            Equipment_Description TEXT,
            UOM                   TEXT,
            Requested_Qty         REAL NOT NULL,
            Stock_At_Request      REAL,
            Available_Flag        INTEGER,
            SK_Adjusted_Qty       REAL,
            Notes                 TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_smr_items_req "
              "ON supervisor_material_request_items(request_id)")

    # Self-heal Source_Ref on the consumption ledger so intent-vs-actual joins
    # work even on legacy rows (those just get NULL → not Source_Ref-traceable,
    # which is correct — they were SK-typed manual entries).
    for _tbl in ("pending_issues", "consumption"):
        c.execute(f"PRAGMA table_info({_tbl})")
        _src_cols = {row[1] for row in c.fetchall()}
        if "Source_Ref" not in _src_cols:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN Source_Ref TEXT")

    # ── Round 12 (SMR-via-SK-Grid + Auto-Attribution) self-heals ─────────────
    # 1. `Requested_By` on pending_issues + consumption — supervisor's username
    #    for SMR-sourced rows, NULL for SK-direct rows. Carried by commit_eod.
    # 2. `line_status` on supervisor_material_request_items — tracks per-line
    #    outcome after SK approval ('active' → 'withdrawn_at_staging' /
    #    'committed'). Defaults to 'active'.
    # 3. The legacy `"Approved By"` (space-named) column on `consumption` is
    #    REUSED for HOD's username at commit_eod — no migration, the column
    #    has been NULL on every existing row.
    for _tbl in ("pending_issues", "consumption"):
        c.execute(f"PRAGMA table_info({_tbl})")
        _r12_cols = {row[1] for row in c.fetchall()}
        if "Requested_By" not in _r12_cols:
            c.execute(f"ALTER TABLE {_tbl} ADD COLUMN Requested_By TEXT")
    # Phase 3 — uses the portable column_exists() helper (identical on SQLite).
    if not column_exists("supervisor_material_request_items", "line_status", conn=conn):
        c.execute(
            "ALTER TABLE supervisor_material_request_items "
            "ADD COLUMN line_status TEXT DEFAULT 'active'"
        )
        # Backfill existing rows so the column is never NULL.
        c.execute(
            "UPDATE supervisor_material_request_items "
            "SET line_status = 'active' WHERE line_status IS NULL"
        )
    # Ensure consumption has the legacy "Approved By" column (some legacy DBs
    # may lack it). Use the same space-named column the table already carries.
    c.execute("PRAGMA table_info(consumption)")
    _cons_cols = {row[1] for row in c.fetchall()}
    if "Approved By" not in _cons_cols:
        c.execute('ALTER TABLE consumption ADD COLUMN "Approved By" TEXT')

    # ── Round 13: EOD State Unification + Schema Cleanup ────────────────────
    # 1. Drop the bogus `Approved` column (cid=16 on legacy DBs). It was
    #    created when old code ran `ALTER TABLE consumption ADD COLUMN
    #    Approved By TEXT` *without quotes* — SQLite parsed `Approved` as
    #    the column name and `By TEXT` as the type. The column has always
    #    been NULL on every row; the actual data lives in the properly-
    #    quoted "Approved By" column added above (and now populated by
    #    commit_eod). Drop is gated by a NULL-only safety probe so a
    #    surprise non-NULL value never causes data loss.
    c.execute("PRAGMA table_info(consumption)")
    _cons_cols_after = {row[1] for row in c.fetchall()}
    if "Approved" in _cons_cols_after:
        try:
            nn = c.execute(
                'SELECT COUNT(*) FROM consumption WHERE "Approved" IS NOT NULL'
            ).fetchone()[0]
        except sqlite3.OperationalError:
            nn = -1  # broken column — fall through to DROP anyway
        if nn == 0 or nn == -1:
            try:
                # ALTER TABLE DROP COLUMN requires SQLite ≥ 3.35 (Mar 2021).
                # If unavailable we silently leave the column; the canonical
                # export list hides it from PDF / CSV regardless.
                c.execute('ALTER TABLE consumption DROP COLUMN "Approved"')
            except sqlite3.OperationalError:
                pass

    # 2. rejected_issues_archive — terminal log for HOD-rejected pending
    #    issues. Mirrors the pending_issues column set (auto-grown on each
    #    init_db pass) plus reject metadata. Keeps pending_issues lean while
    #    preserving full audit trail.
    c.execute("""
        CREATE TABLE IF NOT EXISTS rejected_issues_archive (
            archive_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id     INTEGER,
            SAP_Code        TEXT,
            Quantity        REAL,
            Date            TEXT,
            Site_ID         TEXT,
            Work_Type       TEXT,
            Issued_By       TEXT,
            Issued_To       TEXT,
            Tank_No         TEXT,
            Serial_No       TEXT,
            PR_Number       TEXT,
            Remarks         TEXT,
            Lot_Number      TEXT,
            FEFO_Override   TEXT,
            Source_Ref      TEXT,
            Requested_By    TEXT,
            rejected_by     TEXT NOT NULL,
            rejected_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            reject_reason   TEXT
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_rej_archive_site_date "
        "ON rejected_issues_archive(Site_ID, rejected_at)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_rej_archive_source "
        "ON rejected_issues_archive(Source_Ref)"
    )
    # Forward-compat: if a new pending_issues column appears in a later
    # round, mirror it onto the archive table so the row copy never drops
    # data. (Same self-heal idiom as receipts.)
    c.execute("PRAGMA table_info(pending_issues)")
    _pi_cols = {row[1] for row in c.fetchall()}
    c.execute("PRAGMA table_info(rejected_issues_archive)")
    _arch_cols = {row[1] for row in c.fetchall()}
    for _missing in _pi_cols - _arch_cols - {"id", "Timestamp", "status"}:
        try:
            c.execute(
                f"ALTER TABLE rejected_issues_archive ADD COLUMN {_missing} TEXT"
            )
        except sqlite3.OperationalError:
            pass  # column may have been added by a concurrent init

    # ── Round 15 (Multi-Portal Polish + Material Master) self-heals ─────────
    # 1. inventory_site_overrides — per-site Minimum_Qty without touching the
    #    global inventory row. UNIQUE(SAP_Code, Site_ID) makes the upsert
    #    pattern straightforward. Lookups use COALESCE(override, default).
    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory_site_overrides (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            SAP_Code     TEXT NOT NULL,
            Site_ID      TEXT NOT NULL,
            Minimum_Qty  REAL NOT NULL,
            updated_by   TEXT,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(SAP_Code, Site_ID)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_inv_site_override_site "
        "ON inventory_site_overrides(Site_ID)"
    )

    # 2. UNIQUE partial index on inventory.Material_Code so duplicate codes
    #    can't be inserted via the new Logistics Material Details upload.
    #    Partial (WHERE NOT NULL) so legacy NULL-Material_Code rows aren't
    #    rejected — only the populated ones must be unique.
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_material_code "
            "ON inventory(Material_Code) WHERE Material_Code IS NOT NULL "
            "AND Material_Code <> ''"
        )
    except sqlite3.OperationalError:
        # Partial indexes require SQLite ≥ 3.8.0 — every supported version
        # has it. Swallowed as belt-and-suspenders.
        pass

    # 3. temp_material_seq counter in app_settings — persists the
    #    Temp-GI-NNNNNNN counter across restarts. INSERT OR IGNORE is
    #    idempotent on existing seeds.
    c.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES "
        "('temp_material_seq', '0')"
    )

    # ── Round 16: Logistics removed from the DN approval chain ──────────────
    # New flow: Warehouse → HOD → SK (Logistics is out of the loop, except
    # for the read-only Logistics Oversight tab). Any in-flight DN sitting at
    # `pending_logistics` or `logistics_approved` is moved up to `pending_hod`
    # so the HOD's queue surfaces them immediately on next page load. The
    # CHECK constraint still permits those statuses for restore-from-backup
    # scenarios, but no code path WRITES them anymore.
    try:
        cur_mig = c.execute(
            "UPDATE delivery_notes "
            "SET status='pending_hod', "
            "    logistics_decided_at = COALESCE(logistics_decided_at, "
            "                                    CURRENT_TIMESTAMP), "
            "    logistics_decided_by = COALESCE(logistics_decided_by, "
            "                                    'system_r16_migration'), "
            "    logistics_decision   = COALESCE(logistics_decision, 'auto') "
            "WHERE status IN ('pending_logistics','logistics_approved')"
        )
        n_migrated = cur_mig.rowcount or 0
        if n_migrated > 0:
            try:
                c.execute(
                    "INSERT INTO system_audit_log (username, action_type, "
                    "target_table, details) VALUES (?, ?, ?, ?)",
                    ("system", "DN_LEGACY_MIGRATION_R16",
                     "delivery_notes",
                     f"migrated {n_migrated} DN(s) from pending_logistics/"
                     f"logistics_approved → pending_hod"),
                )
            except sqlite3.OperationalError:
                # system_audit_log schema may differ on older installs —
                # migration itself succeeds either way; the audit entry is
                # best-effort.
                pass
    except sqlite3.OperationalError:
        # delivery_notes may not exist on a brand-new DB before its CREATE
        # TABLE runs in the same init_db pass. Self-heal completes safely
        # on the next startup once the table is in place.
        pass

    # ── Phase 7C: HOD Cross-Site View notification debounce ──────────────────
    # UNIQUE(viewer, target_site, view_date) is the entire debounce — INSERT
    # OR IGNORE returns rowcount=0 on duplicate same-day attempts. No timers,
    # no race conditions across browser tabs.
    c.execute("""
        CREATE TABLE IF NOT EXISTS cross_site_views (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            viewer_username TEXT NOT NULL,
            viewer_site_id  TEXT,
            target_site_id  TEXT NOT NULL,
            view_date       TEXT NOT NULL,
            first_seen_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(viewer_username, target_site_id, view_date)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_csv_target_date "
              "ON cross_site_views(target_site_id, view_date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_csv_viewer_date "
              "ON cross_site_views(viewer_username, view_date)")

    # ── Phase 8E: LocateAnything sidecar telemetry ───────────────────────────
    # One row per /detect HTTP call (success OR failure). Drives the Admin
    # cost/benefit panel + lets us decide whether Tier-3 is actually helping
    # SKs vs adding latency. error TEXT is non-empty when the call failed.
    # accepted INTEGER is filled later by the SK side (0=rejected/dropped,
    # 1=accepted via "Use this tool"); NULL while the SK hasn't decided yet.
    c.execute("""
        CREATE TABLE IF NOT EXISTS locate_anything_calls (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            called_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            site_id         TEXT,
            sk_username     TEXT,
            yolo_top_conf   REAL,
            detection_count INTEGER,
            accepted        INTEGER,
            latency_ms      INTEGER,
            error           TEXT
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_la_calls_called_at "
              "ON locate_anything_calls(called_at)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_la_calls_site "
              "ON locate_anything_calls(site_id, called_at)")

    # ── Phase 7E: Form draft recovery (server-side secondary layer) ──────────
    # Per-user, per-form snapshot of in-flight values. UNIQUE(username, form_id)
    # → re-save overwrites in place. The client-side localStorage layer
    # (streamlit-local-storage) is the primary; this table protects against
    # device swap + browser data wipe + explicit "💾 Save Draft" clicks.
    c.execute("""
        CREATE TABLE IF NOT EXISTS form_drafts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL,
            form_id       TEXT NOT NULL,
            site_id       TEXT,
            payload_json  TEXT NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at    DATETIME,
            UNIQUE(username, form_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_form_drafts_expires "
              "ON form_drafts(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_form_drafts_user "
              "ON form_drafts(username)")

    # ── Round 17: Smart Material Estimator (SME) merge ─────────────────────
    # SME is a read-only projection engine driven by the ERP's ledger. The
    # three tables below hold project master data — equipment with surface
    # areas, lining-system recipes (qty per m²), and the running
    # equipment-tag × system-code progress tally. Available_Qty / Ordered_Qty
    # are NOT stored here — those come from load_live_inventory() and
    # get_on_order_by_material() at read time. Site_ID scopes every row to a
    # single ERP site so admin shadow / HOD per-site views work without
    # cross-contamination. Locations + equipment types live in
    # `system_settings` under categories 'sme_location' and 'sme_equipment_type'
    # (no new tables needed — mirrors the existing Work_Type / Tank_No pattern).
    c.execute("""
        CREATE TABLE IF NOT EXISTS sme_equipment (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID             TEXT    NOT NULL,
            Equipment_Tag_No    TEXT    NOT NULL,
            Name                TEXT,
            Location            TEXT,
            Type                TEXT,
            Substrate           TEXT,
            Lining_System_Code  TEXT    NOT NULL,
            Surface_Area_SQM    REAL    NOT NULL DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Equipment_Tag_No, Lining_System_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_eq_site "
              "ON sme_equipment(Site_ID)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_eq_lsc "
              "ON sme_equipment(Lining_System_Code)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS sme_recipe (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            Lining_System_Code  TEXT    NOT NULL,
            Lining_System_Name  TEXT,
            Material_Code       TEXT    NOT NULL,
            Material_Name       TEXT,
            UOM                 TEXT,
            Nature              TEXT,
            For_1_SQM           REAL    NOT NULL DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Lining_System_Code, Material_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_recipe_mc "
              "ON sme_recipe(Material_Code)")

    # R20.5 — extend sme_equipment with the 15 legacy Excel columns that
    # Equipment.xlsx carries (Sl. #, Project, WBS #, IO#, Drawing #, Design,
    # Dia / L, Ht. /W, Equipment Total SQM, Remaraks, Lining_System_Short_Name,
    # Lining_Type, Lining_System, Material Spec., Lining_Area/location).
    # SME's Master Data tab and _get_autofill helper need every one of these.
    # ALTERs are wrapped in try/except so re-running init_db is safe.
    for _alter in (
        "ALTER TABLE sme_equipment ADD COLUMN Sl_No TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Project TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN WBS_No TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN IO_No TEXT",
        # Sub_Location replaced IO# in the 2026-06 Equipment.xlsx revision.
        "ALTER TABLE sme_equipment ADD COLUMN Sub_Location TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Drawing_No TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Design TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Dia_L TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Ht_W TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Equipment_Total_SQM REAL",
        "ALTER TABLE sme_equipment ADD COLUMN Remaraks TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Lining_System_Short_Name TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Lining_Type TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Lining_System TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Material_Spec TEXT",
        "ALTER TABLE sme_equipment ADD COLUMN Lining_Area_Location TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

    # R20.5 — extend sme_recipe with the 8 legacy For_1_SQM.xlsx columns.
    for _alter in (
        "ALTER TABLE sme_recipe ADD COLUMN Sl_No TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Substrate TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN System_Keys TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Lining_Thickness TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Lining_System TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Lining_Type TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Material_Description TEXT",
        "ALTER TABLE sme_recipe ADD COLUMN Package_Size TEXT",
    ):
        try:
            c.execute(_alter)
        except sqlite3.OperationalError:
            pass

    # R20.5 — SME-owned inventory seed (separate from ERP `inventory`).
    # Holds the static baseline from Materials_DetailsAvailable_Qty.xlsx.
    # The `sme_materials_view` (defined below) joins this against ERP
    # receipts/consumption to produce a live Available_Qty without touching
    # ERP inventory rows. CRUD writes from the SME Master Data tab go HERE,
    # never to ERP `inventory`.
    c.execute("""
        CREATE TABLE IF NOT EXISTS sme_inventory_seed (
            Material_Code         TEXT PRIMARY KEY,
            Material_Name         TEXT,
            Item                  TEXT,
            Vendor                TEXT,
            Purchasing_Document   TEXT,
            Document_Date         TEXT,
            Nature                TEXT,
            UOM                   TEXT,
            Initial_Available_Qty REAL DEFAULT 0,
            Initial_Ordered_Qty   REAL DEFAULT 0,
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sme_sqm_progress (
            Site_ID             TEXT    NOT NULL,
            Equipment_Tag_No    TEXT    NOT NULL,
            Lining_System_Code  TEXT    NOT NULL,
            Original_SQM        REAL    NOT NULL DEFAULT 0,
            Done_SQM            REAL    NOT NULL DEFAULT 0,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (Site_ID, Equipment_Tag_No, Lining_System_Code)
        )
    """)
    # Round 18: two-column Done_SQM model — staged is incremented at SK
    # Submit Batch, then shifted to Done_SQM (committed) when HOD commits
    # via commit_eod_with_sme_sync. Reject path decrements staged only.
    try:
        c.execute(
            "ALTER TABLE sme_sqm_progress ADD COLUMN Done_SQM_staged REAL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # Round 18: rich SME consumption ledger. Captures the full
    # (tag × system × material × sqm × expected × actual) detail. Never
    # touched by the ERP's commit_eod path — that table writes aggregated
    # rows to pending_issues / consumption keyed on SAP_Code. Two-way audit
    # via batch_id (1 batch = 1 SK Submit Batch click) and staged_pi_ids
    # (JSON array of pending_issues.id values produced by aggregation).
    c.execute("""
        CREATE TABLE IF NOT EXISTS sme_consumption_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id            TEXT    NOT NULL,
            Site_ID             TEXT    NOT NULL,
            entry_date          TEXT    NOT NULL,
            entered_by          TEXT,
            Equipment_Tag_No    TEXT    NOT NULL,
            Lining_System_Code  TEXT    NOT NULL,
            Material_Code       TEXT    NOT NULL,
            SQM_Completed       REAL    NOT NULL DEFAULT 0,
            Expected_Qty        REAL    NOT NULL DEFAULT 0,
            Actual_Qty          REAL    NOT NULL DEFAULT 0,
            Variance_Pct        REAL,
            notes               TEXT,
            status              TEXT    NOT NULL DEFAULT 'staged'
                                CHECK(status IN ('staged','committed','rejected')),
            staged_pi_id        INTEGER,
            committed_at        DATETIME,
            rejected_at         DATETIME,
            rejected_reason     TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_cl_batch "
              "ON sme_consumption_log(batch_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_cl_status "
              "ON sme_consumption_log(status, Site_ID)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_cl_pi "
              "ON sme_consumption_log(staged_pi_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sme_cl_tag_sys "
              "ON sme_consumption_log(Site_ID, Equipment_Tag_No, Lining_System_Code)")

    # Round 18: derived view exposing is_sme as a computed flag without
    # adding a column to `inventory`. The flag is True iff the material code
    # appears in any sme_recipe row. Used by reports + admin displays. The
    # runtime dispatch in daily_issue_log.py uses is_sme_sap() directly for
    # speed; this view is for human-readable joins.
    try:
        c.execute("DROP VIEW IF EXISTS v_inventory_with_sme")
        c.execute("""
            CREATE VIEW v_inventory_with_sme AS
            SELECT i.*,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM sme_recipe r
                       WHERE TRIM(r.Material_Code) = TRIM(COALESCE(i.Material_Code,''))
                         AND TRIM(COALESCE(i.Material_Code,'')) <> ''
                   ) THEN 1 ELSE 0 END AS is_sme
            FROM inventory i
        """)
    except sqlite3.Error:
        pass  # View creation must never break startup

    # Round 20 — SME literal-drop-in compatibility VIEWS.
    # The SME's legacy app.py uses SQL against three tables that don't
    # exist in the ERP schema. These views forward the queries to the
    # actual R17/R18 tables so the SME's read paths Just Work:
    #   - locations       → system_settings (category='sme_location')
    #   - types           → system_settings (category='sme_equipment_type')
    #   - consumption_log → sme_consumption_log (R18 rich ledger), filtered
    #                       to 'committed' status so reports only show
    #                       HOD-approved consumption (matches SME semantics)
    #   - equipment       → sme_equipment with column aliases (lowercase
    #                       snake_case the SME's master-data tab uses)
    #   - recipe          → sme_recipe with same alias pattern
    #   - inventory       → already exists as a real table; the SME reads
    #                       it directly via column-aliased SELECTs
    #   - sqm_progress    → sme_sqm_progress with column aliases
    # Master-data WRITES against locations / types are surgically rerouted
    # in material_estimator_portal.py to call add_sme_setting /
    # delete_sme_setting helpers — views are read-only by design.
    for _v in ("locations", "types", "consumption_log",
               "equipment", "recipe", "sqm_progress",
               "sme_materials_view"):
        try:
            # Only drop a view (never a table) so a future ERP table named
            # 'equipment' wouldn't get nuked here.
            row = c.execute(
                "SELECT type FROM sqlite_master WHERE name = ?", (_v,)
            ).fetchone()
            if row and row[0] == "view":
                c.execute(f"DROP VIEW {_v}")
        except sqlite3.Error:
            pass
    # R20.2 EDIT: GROUP BY value so each location/type appears only ONCE
    # in the dropdown, regardless of how many sites it's seeded for.
    # Brown Field is "Brown Field" whether seeded into HQ or CNCEC; the
    # SME UI treats locations as project-internal names, not per-site.
    try:
        c.execute("""
            CREATE VIEW locations AS
            SELECT value AS name,
                   '#64748B' AS badge_color,
                   MIN(rowid) AS sort_order,
                   '' AS added_at
            FROM system_settings
            WHERE category = 'sme_location'
            GROUP BY value
        """)
    except sqlite3.Error:
        pass
    try:
        c.execute("""
            CREATE VIEW types AS
            SELECT value AS name,
                   MIN(rowid) AS sort_order,
                   '' AS added_at
            FROM system_settings
            WHERE category = 'sme_equipment_type'
            GROUP BY value
        """)
    except sqlite3.Error:
        pass
    try:
        c.execute("""
            CREATE VIEW consumption_log AS
            SELECT id,
                   entry_date,
                   Equipment_Tag_No    AS equipment_tag,
                   Lining_System_Code  AS lining_system_code,
                   SQM_Completed       AS sqm_completed,
                   Material_Code       AS material_code,
                   Expected_Qty        AS expected_qty,
                   Actual_Qty          AS consumed_qty,
                   Variance_Pct        AS variance_pct,
                   '' AS variance_status,
                   '' AS material_name,
                   '' AS uom,
                   '' AS lining_system_name,
                   committed_at        AS submitted_at,
                   Site_ID
            FROM sme_consumption_log
            WHERE status = 'committed'
        """)
    except sqlite3.Error:
        pass
    # SME's `equipment`, `recipe`, `sqm_progress` references in Master Data
    # use lowercase snake_case columns. Map them onto our PascalCase tables.
    # R20.5 — equipment VIEW exposes every legacy Excel column so the SME
    # Master Data Smart Entry form + _get_autofill helper work. Note the
    # mixed case: `_get_autofill` quotes "Lining_System", "Material Spec.",
    # and "Lining_Area/location" as SQLite identifiers, so we alias to
    # exactly those PascalCase / dotted / slashed names.
    try:
        c.execute("""
            CREATE VIEW equipment AS
            SELECT id,
                   Site_ID                  AS site_id,
                   Equipment_Tag_No         AS equipment_tag,
                   Name                     AS name,
                   Location                 AS location,
                   Type                     AS type,
                   Substrate                AS substrate,
                   Lining_System_Code       AS lining_system_code,
                   Lining_System_Short_Name AS lining_system_short_name,
                   Lining_Type              AS lining_type,
                   Material_Spec            AS "Material Spec.",
                   Design                   AS design,
                   Lining_System            AS "Lining_System",
                   Lining_Area_Location     AS "Lining_Area/location",
                   Sl_No                    AS "Sl. #",
                   Project                  AS project,
                   WBS_No                   AS "WBS #",
                   IO_No                    AS "IO#",
                   Sub_Location             AS "Sub_Location",
                   Drawing_No               AS "Drawing #",
                   Dia_L                    AS "Dia / L",
                   Ht_W                     AS "Ht. /W",
                   Equipment_Total_SQM      AS "Equipment Total SQM",
                   Remaraks                 AS remaraks,
                   Lining_System            AS lining_systems,
                   Surface_Area_SQM         AS surface_area_sqm
            FROM sme_equipment
        """)
    except sqlite3.Error:
        pass
    # R20.5 — recipe VIEW now serves real Lining_Type + extended Excel
    # columns. lining_system_short_name aliases Lining_System_Name (the
    # bootstrap stores the short name there for back-compat with reads).
    try:
        c.execute("""
            CREATE VIEW recipe AS
            SELECT id,
                   Lining_System_Code       AS lining_system_code,
                   Lining_System_Name       AS lining_system_short_name,
                   Lining_Type              AS lining_type,
                   Lining_System            AS lining_system,
                   Substrate                AS substrate,
                   System_Keys              AS system_keys,
                   Lining_Thickness         AS lining_thickness,
                   Material_Code            AS material_code,
                   COALESCE(Material_Description, Material_Name) AS material_description,
                   Material_Name            AS material_name,
                   For_1_SQM                AS for_1_sqm,
                   UOM                      AS uom,
                   Nature                   AS nature,
                   Package_Size             AS package_size,
                   Sl_No                    AS "Sl. #"
            FROM sme_recipe
        """)
    except sqlite3.Error:
        pass
    try:
        c.execute("""
            CREATE VIEW sqm_progress AS
            SELECT Site_ID            AS site_id,
                   Equipment_Tag_No   AS equipment_tag,
                   Lining_System_Code AS lining_system_code,
                   Original_SQM       AS original_sqm,
                   (COALESCE(Done_SQM,0) + COALESCE(Done_SQM_staged,0)) AS done_sqm
            FROM sme_sqm_progress
        """)
    except sqlite3.Error:
        pass

    # R20.5 — Materials view for SME Master Data tab. Wraps sme_inventory_seed
    # (the SME-owned baseline) and LEFT JOINs against ERP receipts /
    # consumption tables to surface a LIVE Available_Qty without mingling
    # writes into ERP `inventory`. Only Material_Codes present in sme_recipe
    # roll up here — keeps the view tight to ~30 SME materials and excludes
    # the 1,200+ ERP materials that aren't relevant to the estimator.
    #
    # Math:  Available_Qty = Initial_Available_Qty
    #                        + sum(receipts.Quantity)      (via SAP_Code → inventory.Material_Code)
    #                        - sum(consumption.Quantity)   (same path)
    #
    # The dynamic Master Data form introspects this view via PRAGMA
    # table_info — column names use lowercase snake_case to match SME's
    # legacy Inventory table contract.
    try:
        c.execute("""
            CREATE VIEW sme_materials_view AS
            SELECT s.Material_Code         AS material_code,
                   s.Material_Name         AS material_name,
                   s.Item                  AS item,
                   s.Vendor                AS vendor,
                   s.Purchasing_Document   AS purchasing_document,
                   s.Document_Date         AS document_date,
                   s.Nature                AS nature,
                   s.UOM                   AS uom,
                   s.Initial_Available_Qty AS initial_available_qty,
                   s.Initial_Ordered_Qty   AS initial_ordered_qty,
                   COALESCE((
                       SELECT SUM(r.Quantity)
                       FROM receipts r
                       JOIN inventory i ON r.SAP_Code = i.SAP_Code
                       WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)
                   ), 0) AS received_qty,
                   COALESCE((
                       SELECT SUM(c.Quantity)
                       FROM consumption c
                       JOIN inventory i ON c.SAP_Code = i.SAP_Code
                       WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)
                   ), 0) AS consumed_qty,
                   (s.Initial_Available_Qty
                       + COALESCE((
                           SELECT SUM(r.Quantity)
                           FROM receipts r
                           JOIN inventory i ON r.SAP_Code = i.SAP_Code
                           WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)
                         ), 0)
                       - COALESCE((
                           SELECT SUM(c.Quantity)
                           FROM consumption c
                           JOIN inventory i ON c.SAP_Code = i.SAP_Code
                           WHERE TRIM(COALESCE(i.Material_Code,'')) = TRIM(s.Material_Code)
                         ), 0)
                   ) AS available_qty,
                   s.Initial_Ordered_Qty   AS ordered_qty
            FROM sme_inventory_seed s
        """)
    except sqlite3.Error:
        pass

    # Seed default SME location / equipment-type values under system_settings
    # for legacy site 'HQ' so the new portal has populated dropdowns on first
    # render. Idempotent — INSERT OR IGNORE on (category, value, Site_ID).
    # NB: system_settings has no UNIQUE constraint by default, so we guard
    # with a SELECT-then-INSERT (safe across the cloud's parallel workers
    # because the worst case is a duplicate value, which the UI dedupes).
    _sme_seed_locations = ["Brown Field", "TRAIN J", "TRAIN K"]
    _sme_seed_types     = ["Vessel", "Tank", "Column", "Pipe", "Reactor"]
    for _val in _sme_seed_locations:
        c.execute(
            "INSERT INTO system_settings (category, value, Site_ID) "
            "SELECT 'sme_location', ?, 'HQ' WHERE NOT EXISTS ("
            "  SELECT 1 FROM system_settings "
            "  WHERE category='sme_location' AND value=? AND Site_ID='HQ')",
            (_val, _val),
        )
    for _val in _sme_seed_types:
        c.execute(
            "INSERT INTO system_settings (category, value, Site_ID) "
            "SELECT 'sme_equipment_type', ?, 'HQ' WHERE NOT EXISTS ("
            "  SELECT 1 FROM system_settings "
            "  WHERE category='sme_equipment_type' AND value=? AND Site_ID='HQ')",
            (_val, _val),
        )

    # ── Man-Hour & Labor Tracking (workstream §2Z) ──────────────────────────
    # Labor tracked the way the SME tracks material. These mh_* tables are an
    # ISOLATED, ADDITIVE domain: they READ sme_equipment / sme_recipe /
    # sme_sqm_progress (Equipment_Tag_No, Location, Lining_System_Code = the
    # "System Code", Done_SQM) only for dropdowns + context, and never write to
    # any sme_* table, the inventory ledger, or the EOD path. Site_ID is threaded
    # through every table (RULE 3). All self-heal via CREATE TABLE IF NOT EXISTS.

    # Labor roster (the "ADD EMPLOYEE" sheet). Separate from the ERP `employees`
    # master so OWN (GI) staff AND Supply (subcontractor, e.g. DMC) workers live
    # together with Designation/Type/Company. linked_id_number optionally ties an
    # OWN worker back to employees.ID_Number (no FK constraint — soft link).
    c.execute("""
        CREATE TABLE IF NOT EXISTS mh_employees (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID          TEXT    NOT NULL,
            Employee_Code    TEXT    NOT NULL,
            Name             TEXT    NOT NULL,
            Designation      TEXT,
            Worker_Type      TEXT    NOT NULL DEFAULT 'OWN'
                             CHECK(Worker_Type IN ('OWN','Supply')),
            Company          TEXT,
            linked_id_number TEXT,
            status           TEXT    NOT NULL DEFAULT 'active'
                             CHECK(status IN ('active','inactive')),
            created_by       TEXT,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Employee_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_emp_site "
              "ON mh_employees(Site_ID, status)")

    # Daily timesheet actuals (the "SAR" sheet + the new work tags). One row per
    # (employee, date, equipment-tag, system-code) — a worker may have several
    # rows/day if their work is split across tags. Hours are COMPUTED from
    # In/Out − break (8h normal + 1h unpaid break policy); the source file's
    # dirty Total/Normal/OT columns are ignored. Allocated_SQM is the worker's
    # share of the team's daily SQM (filled by the mh_production distributor).
    c.execute("""
        CREATE TABLE IF NOT EXISTS mh_timesheets (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID          TEXT    NOT NULL,
            Employee_Code    TEXT    NOT NULL,
            Work_Date        TEXT    NOT NULL,
            Location         TEXT,
            Equipment_Tag    TEXT,
            System_Code      TEXT,
            In_Time          TEXT,
            Out_Time         TEXT,
            Break_Mins       INTEGER NOT NULL DEFAULT 60,
            Total_Hours      REAL    NOT NULL DEFAULT 0,
            Normal_Hours     REAL    NOT NULL DEFAULT 0,
            OT_Hours         REAL    NOT NULL DEFAULT 0,
            Allocated_SQM    REAL    NOT NULL DEFAULT 0,
            Status           TEXT    NOT NULL DEFAULT 'PR',
            Remarks          TEXT,
            created_by       TEXT,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Employee_Code, Work_Date, Equipment_Tag, System_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_ts_site_date "
              "ON mh_timesheets(Site_ID, Work_Date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_ts_emp_date "
              "ON mh_timesheets(Employee_Code, Work_Date)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_ts_tag_sys "
              "ON mh_timesheets(Site_ID, Equipment_Tag, System_Code)")

    # Team SQM produced per day on a tag/system — the source for "distributed
    # evenly across the team". Distribution_Method drives how SQM_Done is split
    # into each worker's mh_timesheets.Allocated_SQM (even | by_hours | manual).
    c.execute("""
        CREATE TABLE IF NOT EXISTS mh_production (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID             TEXT    NOT NULL,
            Work_Date           TEXT    NOT NULL,
            Equipment_Tag       TEXT    NOT NULL,
            System_Code         TEXT    NOT NULL,
            SQM_Done            REAL    NOT NULL DEFAULT 0,
            Distribution_Method TEXT    NOT NULL DEFAULT 'even'
                                CHECK(Distribution_Method IN ('even','by_hours','manual')),
            created_by          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Work_Date, Equipment_Tag, System_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_prod_tag_sys "
              "ON mh_production(Site_ID, Equipment_Tag, System_Code)")

    # The Man-Hour Estimator — required/estimated man-hours per
    # Location / Equipment-Tag / System-Code (mirrors the material estimator).
    # Estimated_SQM is optional; when present it yields an MH-per-SQM norm.
    c.execute("""
        CREATE TABLE IF NOT EXISTS mh_manhour_estimates (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID             TEXT    NOT NULL,
            Location            TEXT,
            Equipment_Tag       TEXT    NOT NULL,
            System_Code         TEXT    NOT NULL,
            Estimated_Manhours  REAL    NOT NULL DEFAULT 0,
            Estimated_SQM       REAL,
            Basis               TEXT,
            created_by          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Equipment_Tag, System_Code)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS ix_mh_est_tag_sys "
              "ON mh_manhour_estimates(Site_ID, Equipment_Tag, System_Code)")

    # Free-text reason for an over-consumption variance, keyed at the
    # estimate-vs-actual grain (one current reason per Site/Tag/System).
    c.execute("""
        CREATE TABLE IF NOT EXISTS mh_variance_notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            Site_ID         TEXT    NOT NULL,
            Equipment_Tag   TEXT    NOT NULL,
            System_Code     TEXT    NOT NULL,
            Reason          TEXT    NOT NULL,
            entered_by      TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(Site_ID, Equipment_Tag, System_Code)
        )
    """)

    # Read-only comparison view: estimated MH vs actual consumed MH per
    # Site/Tag/System, with the latest team SQM and any variance reason.
    # Variance_Pct = (Actual − Estimated) / Estimated × 100 (NULL when no
    # estimate). Drives the Estimate-vs-Actual dashboard.
    try:
        c.execute("DROP VIEW IF EXISTS v_mh_estimate_vs_actual")
        c.execute("""
            CREATE VIEW v_mh_estimate_vs_actual AS
            SELECT
                e.Site_ID                                   AS Site_ID,
                e.Equipment_Tag                             AS Equipment_Tag,
                e.System_Code                               AS System_Code,
                e.Location                                  AS Location,
                e.Estimated_Manhours                        AS Estimated_Manhours,
                COALESCE(a.Actual_Manhours, 0)              AS Actual_Manhours,
                COALESCE(a.Actual_Manhours, 0)
                    - e.Estimated_Manhours                  AS Variance_Manhours,
                CASE WHEN e.Estimated_Manhours > 0
                     THEN ROUND((COALESCE(a.Actual_Manhours, 0)
                          - e.Estimated_Manhours) * 100.0
                          / e.Estimated_Manhours, 1)
                     ELSE NULL END                          AS Variance_Pct,
                COALESCE(p.SQM_Done, 0)                     AS SQM_Done,
                n.Reason                                    AS Variance_Reason
            FROM mh_manhour_estimates e
            LEFT JOIN (
                SELECT Site_ID, Equipment_Tag, System_Code,
                       SUM(Total_Hours) AS Actual_Manhours
                FROM mh_timesheets
                GROUP BY Site_ID, Equipment_Tag, System_Code
            ) a ON a.Site_ID = e.Site_ID
               AND a.Equipment_Tag = e.Equipment_Tag
               AND a.System_Code = e.System_Code
            LEFT JOIN (
                SELECT Site_ID, Equipment_Tag, System_Code,
                       SUM(SQM_Done) AS SQM_Done
                FROM mh_production
                GROUP BY Site_ID, Equipment_Tag, System_Code
            ) p ON p.Site_ID = e.Site_ID
               AND p.Equipment_Tag = e.Equipment_Tag
               AND p.System_Code = e.System_Code
            LEFT JOIN mh_variance_notes n
                   ON n.Site_ID = e.Site_ID
                  AND n.Equipment_Tag = e.Equipment_Tag
                  AND n.System_Code = e.System_Code
        """)
    except sqlite3.Error:
        pass  # View creation must never break startup

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
                ), 0)
                -- split/merge reclassification (within-SAP; nets to zero)
                - COALESCE((
                    SELECT SUM(t.Qty) FROM lot_transfers t
                    WHERE t.From_Lot = l.Lot_Number
                      AND t.SAP_Code = l.SAP_Code
                      AND COALESCE(t.Site_ID,'HQ') = l.Site_ID
                ), 0)
                + COALESCE((
                    SELECT SUM(t.Qty) FROM lot_transfers t
                    WHERE t.To_Lot = l.Lot_Number
                      AND t.SAP_Code = l.SAP_Code
                      AND COALESCE(t.Site_ID,'HQ') = l.Site_ID
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
def get_work_types(conn: sqlite3.Connection = None, site_id: str = None) -> list[str]:
    """Return Work_Type dropdown values.

    If site_id given, returns site-specific values; falls back to global
    (Site_ID IS NULL) when the site has none yet.
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    try:
        if site_id:
            rows = pd.read_sql(
                "SELECT value FROM system_settings WHERE category='Work_Type' AND Site_ID=?",
                conn, params=(site_id,)
            )["value"].tolist()
            if not rows:
                rows = pd.read_sql(
                    "SELECT value FROM system_settings WHERE category='Work_Type' AND Site_ID IS NULL",
                    conn
                )["value"].tolist()
        else:
            rows = pd.read_sql(
                "SELECT value FROM system_settings WHERE category='Work_Type' AND Site_ID IS NULL",
                conn
            )["value"].tolist()
    finally:
        if _owns_conn:
            conn.close()
    return rows


def get_tank_nos(conn: sqlite3.Connection = None, site_id: str = None) -> list[str]:
    """Return Tank_No dropdown values for a site (falls back to global)."""
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    try:
        if site_id:
            rows = pd.read_sql(
                "SELECT value FROM system_settings WHERE category='Tank_No' AND Site_ID=?",
                conn, params=(site_id,)
            )["value"].tolist()
            if not rows:
                rows = pd.read_sql(
                    "SELECT value FROM system_settings WHERE category='Tank_No' AND Site_ID IS NULL",
                    conn
                )["value"].tolist()
        else:
            rows = pd.read_sql(
                "SELECT value FROM system_settings WHERE category='Tank_No' AND Site_ID IS NULL",
                conn
            )["value"].tolist()
    finally:
        if _owns_conn:
            conn.close()
    return rows


# ---------------------------------------------------------------------------
# WBS Master — per-site allowed WBS numbers (mirrors pr_master pattern)
# ---------------------------------------------------------------------------
def get_wbs_for_site(
    site_id: str, conn: sqlite3.Connection = None,
    include_closed: bool = False,
) -> pd.DataFrame:
    """Return all WBS rows for a site. Active first."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM wbs_master WHERE Site_ID = ?"
        params: list = [site_id]
        if not include_closed:
            q += " AND status = 'active'"
        q += " ORDER BY status, WBS_Number"
        return _localize(pd.read_sql(q, conn, params=tuple(params)))
    finally:
        if _owns:
            conn.close()


def add_wbs(
    wbs_number: str, description: str, site_id: str,
    created_by: str, conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        wbs_number = (wbs_number or "").strip()
        if not wbs_number:
            return False, "WBS Number cannot be empty."
        try:
            conn.execute(
                "INSERT INTO wbs_master "
                "(WBS_Number, Description, Site_ID, created_by) "
                "VALUES (?, ?, ?, ?)",
                (wbs_number, (description or "").strip(), site_id, created_by),
            )
            conn.commit()
            return True, f"Added WBS '{wbs_number}' for {site_id}."
        except sqlite3.IntegrityError:
            return False, f"WBS '{wbs_number}' already exists at {site_id}."
    finally:
        if _owns:
            conn.close()


def set_wbs_status(
    wbs_number: str, site_id: str, status: str,
    conn: sqlite3.Connection = None,
) -> bool:
    if status not in ("active", "closed"):
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE wbs_master SET status = ? WHERE WBS_Number = ? AND Site_ID = ?",
            (status, wbs_number, site_id),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def report_wbs_consumption(
    date_from: str, date_to: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Site-scoped report grouped by WBS:
        WBS_Number, total consumption qty + count, total receipt qty + count,
        net value in SAR (via inventory.Unit_Cost).
    site_id=None falls back to "All Sites" (admin / supervisor).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        where_site_c = " AND COALESCE(c.Site_ID,'HQ') = ?" if site_id else ""
        where_site_r = " AND COALESCE(r.Site_ID,'HQ') = ?" if site_id else ""
        params_c: tuple = ((date_from, date_to, site_id) if site_id
                           else (date_from, date_to))
        params_r: tuple = ((date_from, date_to, site_id) if site_id
                           else (date_from, date_to))
        cons = pd.read_sql(
            "SELECT COALESCE(c.wbs,'(no WBS)') AS WBS_Number, "
            "       COUNT(*)                 AS Consumption_Rows, "
            "       COALESCE(SUM(c.Quantity),0) AS Consumption_Qty, "
            "       COALESCE(SUM(c.Quantity * COALESCE(i.Unit_Cost,0)),0) AS Consumption_Value_SAR "
            "FROM consumption c LEFT JOIN inventory i ON c.SAP_Code = i.SAP_Code "
            f"WHERE c.Date BETWEEN ? AND ?{where_site_c} "
            "GROUP BY COALESCE(c.wbs,'(no WBS)')",
            conn, params=params_c,
        )
        recs = pd.read_sql(
            "SELECT COALESCE(r.wbs,'(no WBS)') AS WBS_Number, "
            "       COUNT(*)                 AS Receipt_Rows, "
            "       COALESCE(SUM(r.Quantity),0) AS Receipt_Qty, "
            "       COALESCE(SUM(r.Quantity * COALESCE(i.Unit_Cost,0)),0) AS Receipt_Value_SAR "
            "FROM receipts r LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code "
            f"WHERE r.Date BETWEEN ? AND ?{where_site_r} "
            "GROUP BY COALESCE(r.wbs,'(no WBS)')",
            conn, params=params_r,
        )
    finally:
        if _owns:
            conn.close()

    df = pd.merge(cons, recs, on="WBS_Number", how="outer").fillna(0)
    # Reorder + descending by total spend
    cols = ["WBS_Number", "Consumption_Rows", "Consumption_Qty",
            "Consumption_Value_SAR", "Receipt_Rows", "Receipt_Qty",
            "Receipt_Value_SAR"]
    for c in cols:
        if c not in df.columns:
            df[c] = 0
    df = df[cols].sort_values("Consumption_Value_SAR", ascending=False).reset_index(drop=True)

    summary = {
        "WBS_Count": int(len(df)),
        "Total_Consumption_Qty": float(df["Consumption_Qty"].sum()),
        "Total_Receipt_Qty":     float(df["Receipt_Qty"].sum()),
        "Total_Consumption_SAR": format_sar(float(df["Consumption_Value_SAR"].sum())),
        "Total_Receipt_SAR":     format_sar(float(df["Receipt_Value_SAR"].sum())),
    }
    return df, summary


def add_site_dropdown_value(
    category: str, value: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Add a value to a site-scoped (or global) dropdown category."""
    value = value.strip()
    if not value:
        return False, "Value cannot be empty."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        existing = pd.read_sql(
            "SELECT value FROM system_settings WHERE category=? AND value=? AND "
            + ("Site_ID=?" if site_id else "Site_ID IS NULL"),
            conn,
            params=(category, value, site_id) if site_id else (category, value),
        )["value"].tolist()
        if existing:
            return False, f"'{value}' already exists."
        conn.execute(
            "INSERT INTO system_settings (category, value, Site_ID) VALUES (?,?,?)",
            (category, value, site_id),
        )
        conn.commit()
        return True, f"Added '{value}'."
    finally:
        if _owns:
            conn.close()


def delete_site_dropdown_value(
    category: str, value: str, site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Delete a value from a site-scoped (or global) dropdown category."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if site_id:
            conn.execute(
                "DELETE FROM system_settings WHERE category=? AND value=? AND Site_ID=?",
                (category, value, site_id),
            )
        else:
            conn.execute(
                "DELETE FROM system_settings WHERE category=? AND value=? AND Site_ID IS NULL",
                (category, value),
            )
        conn.commit()
        return True, f"Deleted '{value}'."
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# File-storage helpers — DB BLOB + uploads/ disk mirror (2026-06)
# ---------------------------------------------------------------------------
UPLOADS_ROOT = "uploads"


def _safe_path(*parts: str) -> str:
    """Build an attachments path, sanitising each component (drop ../ etc)."""
    import re
    cleaned = []
    for p in parts:
        s = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(p))
        cleaned.append(s or "_")
    return os.path.join(UPLOADS_ROOT, *cleaned)


def _store_blob_and_disk(
    site_id: str, doc_type: str, doc_number: str, file_obj
) -> tuple[bytes, str, str, str, int]:
    """
    Read a Streamlit UploadedFile / file-like object, persist a disk mirror
    under uploads/<Site>/<doc_type>/<doc_number>/<name>, and return:
      (blob_bytes, file_name, mime_type, disk_path, size_bytes).
    Returns (b"", "", "", "", 0) if file_obj is falsy.
    """
    if not file_obj:
        return b"", "", "", "", 0
    import os as _os
    file_name = getattr(file_obj, "name", "upload.bin")
    mime_type = getattr(file_obj, "type", "") or ""
    blob = file_obj.read() if hasattr(file_obj, "read") else bytes(file_obj)
    # Reset stream pointer so the caller can re-read if needed.
    try:
        file_obj.seek(0)
    except Exception:
        pass
    folder = _safe_path(site_id or "HQ", doc_type or "misc", doc_number or "unnumbered")
    _os.makedirs(folder, exist_ok=True)
    disk_path = _os.path.join(folder, file_name)
    try:
        with open(disk_path, "wb") as fh:
            fh.write(blob)
    except OSError:
        disk_path = ""  # Cloud filesystem may be read-only; BLOB still saves.
    return blob, file_name, mime_type, disk_path, len(blob)


def save_entry_attachment(
    site_id: str,
    doc_type: str,             # 'consumption' | 'receipt' | 'return'
    doc_number: str,
    file_obj,
    uploaded_by: str,
    entry_table: str = None,
    entry_id: int = None,
    entry_date: str = None,
    conn: sqlite3.Connection = None,
) -> int | None:
    """Persist a single attachment (BLOB + disk mirror). Returns inserted row id."""
    if not file_obj:
        return None
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        blob, fname, mime, disk_path, sz = _store_blob_and_disk(
            site_id, doc_type, doc_number, file_obj,
        )
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO entry_attachments "
            "(Site_ID, doc_type, doc_number, entry_table, entry_id, entry_date, "
            " file_name, mime_type, file_size, file_blob, disk_path, uploaded_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (site_id, doc_type, doc_number, entry_table, entry_id, entry_date,
             fname, mime, sz, blob, disk_path, uploaded_by),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def save_mtc_document(
    site_id: str,
    sap_code: str,
    material_code: str,
    lot_number: str,
    quantity: float,
    mtc_number: str,
    uploaded_file,
    pending_receipt_id: int,
    submitted_by: str,
    conn: sqlite3.Connection = None,
) -> int:
    """
    Record an MTC document for a rubber-category receipt.
    status = 'attached' when a file is given, else 'missing' (HOD-actionable).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if uploaded_file:
            blob, fname, mime, disk_path, _ = _store_blob_and_disk(
                site_id, "mtc", mtc_number or f"PRCT-{pending_receipt_id}", uploaded_file,
            )
            status = "attached"
        else:
            blob, fname, mime, disk_path, status = b"", "", "", "", "missing"
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mtc_documents "
            "(Site_ID, SAP_Code, Material_Code, Lot_Number, Quantity, mtc_number, "
            " file_name, mime_type, file_blob, disk_path, status, "
            " pending_receipt_id, submitted_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (site_id, sap_code, material_code, lot_number or "", quantity,
             (mtc_number or "").strip(), fname, mime, blob, disk_path,
             status, pending_receipt_id, submitted_by),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def get_missing_mtc_for_site(
    site_id: str, conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Rubber items the SK pushed without an MTC — used by HOD warning + logistics email."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT m.id, m.SAP_Code, m.Material_Code, m.Lot_Number, m.Quantity, "
            "       m.submitted_by, m.submitted_at, i.Equipment_Description "
            "FROM mtc_documents m "
            "LEFT JOIN inventory i ON m.SAP_Code = i.SAP_Code "
            "WHERE m.Site_ID = ? AND m.status = 'missing' "
            "ORDER BY m.submitted_at DESC",
            conn, params=(site_id,),
        )
        return _localize(df)
    finally:
        if _owns:
            conn.close()


def mark_mtc_emailed(mtc_ids: list[int], conn: sqlite3.Connection = None) -> int:
    if not mtc_ids:
        return 0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        ph = ",".join(["?"] * len(mtc_ids))
        cur = conn.cursor()
        cur.execute(
            f"UPDATE mtc_documents SET status='sent_to_logistics', "
            f"logistics_emailed_at = CURRENT_TIMESTAMP WHERE id IN ({ph})",
            tuple(mtc_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# QR approval workflow (2026-06)
# ---------------------------------------------------------------------------
def submit_qr_request(
    site_id: str, sap_code: str, requested_by: str,
    quantity: int = 1, conn: sqlite3.Connection = None,
) -> int:
    """SK submits a QR-label request. HOD approves later."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        meta = pd.read_sql(
            "SELECT COALESCE(Material_Code,'') AS Material_Code, "
            "       COALESCE(Equipment_Description,'') AS Equipment_Description "
            "FROM inventory WHERE SAP_Code = ?",
            conn, params=(sap_code,),
        )
        mat_code, eq_desc = ("", "")
        if not meta.empty:
            mat_code = str(meta.iloc[0]["Material_Code"])
            eq_desc  = str(meta.iloc[0]["Equipment_Description"])
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO qr_approval_requests "
            "(Site_ID, SAP_Code, Material_Code, Equipment_Description, "
            " Quantity, requested_by) VALUES (?,?,?,?,?,?)",
            (site_id, sap_code, mat_code, eq_desc, max(1, int(quantity)), requested_by),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def list_qr_requests(
    site_id: str = None, status: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM qr_approval_requests WHERE 1=1"
        params: list = []
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY requested_at DESC"
        return _localize(pd.read_sql(q, conn, params=tuple(params)))
    finally:
        if _owns:
            conn.close()


def approve_qr_request(
    request_id: int, approver: str, conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE qr_approval_requests SET status='approved', "
            "approved_by=?, approved_at=CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (approver, request_id),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def reject_qr_request(
    request_id: int, approver: str, reason: str = "",
    conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE qr_approval_requests SET status='rejected', "
            "approved_by=?, approved_at=CURRENT_TIMESTAMP, rejection_reason=? "
            "WHERE id = ? AND status = 'pending'",
            (approver, reason or "", request_id),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Returns workflow (SK → HOD → commits to `returns` ledger)
# ---------------------------------------------------------------------------
def get_returnable_receipts(
    site_id: str, days_back: int = 30,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """
    Receipts from the last N days that the SK is allowed to return without
    HOD override. Used to populate the material picker in the Returns tab.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT r.rowid AS receipt_id, r.Date, r.SAP_Code, "
            "       COALESCE(i.Material_Code,'') AS Material_Code, "
            "       i.Equipment_Description, i.UOM, "
            "       r.Quantity AS received_qty, "
            "       COALESCE(r.DN_No,'') AS DN_No, "
            "       COALESCE(r.PR_Number,'') AS PR_Number, "
            "       COALESCE(r.Lot_Number,'') AS Lot_Number "
            "FROM receipts r LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code "
            "WHERE COALESCE(r.Site_ID,'HQ') = ? "
            "  AND DATE(r.Date) >= DATE('now', ?) "
            "ORDER BY r.Date DESC",
            conn, params=(site_id, f"-{int(days_back)} days"),
        )
        return df
    finally:
        if _owns:
            conn.close()


def submit_return_request(
    site_id: str,
    sap_code: str,
    quantity: float,
    return_reason: str,
    return_dn_no: str,
    received_receipt_row: dict,
    submitted_by: str,
    override_required: bool = False,
    override_reason: str = "",
    conn: sqlite3.Connection = None,
) -> int:
    """Stage a return for HOD approval. Returns inserted row id."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pending_returns "
            "(Site_ID, SAP_Code, Material_Code, Equipment_Description, "
            " Quantity, Return_Reason, Return_DN_No, "
            " received_date, received_dn_no, received_qty, "
            " PR_Number, Lot_Number, override_required, override_reason, "
            " submitted_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                site_id, sap_code,
                received_receipt_row.get("Material_Code", ""),
                received_receipt_row.get("Equipment_Description", ""),
                float(quantity),
                (return_reason or "").strip(),
                (return_dn_no or "").strip(),
                received_receipt_row.get("Date"),
                received_receipt_row.get("DN_No", ""),
                float(received_receipt_row.get("received_qty", 0) or 0),
                received_receipt_row.get("PR_Number", ""),
                received_receipt_row.get("Lot_Number", ""),
                1 if override_required else 0,
                (override_reason or "").strip(),
                submitted_by,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def get_pending_returns(
    site_id: str = None, conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM pending_returns WHERE status='pending_hod'"
        params: list = []
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        q += " ORDER BY submitted_at ASC"
        return _localize(pd.read_sql(q, conn, params=tuple(params)))
    finally:
        if _owns:
            conn.close()


def approve_return_request(
    request_id: int, approver: str, conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Approve a pending return → write a row into `returns` (which reduces
    Current_Stock via the standard identity). Idempotent on status.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = pd.read_sql(
            "SELECT * FROM pending_returns WHERE id = ?", conn, params=(request_id,),
        )
        if row.empty:
            return False, "Return request not found."
        r = row.iloc[0]
        if r["status"] != "pending_hod":
            return False, f"Already {r['status']}."
        # Insert into returns ledger.
        conn.execute(
            "INSERT INTO returns (Date, SAP_Code, Quantity, Reason, Remarks, Site_ID) "
            "VALUES (DATE('now'), ?, ?, ?, ?, ?)",
            (r["SAP_Code"], float(r["Quantity"]), r["Return_Reason"],
             f"Return DN: {r['Return_DN_No']} · approved by {approver}",
             r["Site_ID"]),
        )
        conn.execute(
            "UPDATE pending_returns SET status='approved', "
            "approved_by=?, approved_at=CURRENT_TIMESTAMP WHERE id=?",
            (approver, request_id),
        )
        conn.commit()
        return True, "Approved."
    finally:
        if _owns:
            conn.close()


def reject_return_request(
    request_id: int, approver: str, reason: str = "",
    conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE pending_returns SET status='rejected', "
            "approved_by=?, approved_at=CURRENT_TIMESTAMP, "
            "rejection_reason=? WHERE id=? AND status='pending_hod'",
            (approver, reason or "", request_id),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


# The pending_returns columns copied verbatim into returns_history (order-matched
# to the INSERT below). Excludes the archive-only metadata columns.
_RETURNS_ARCHIVE_COLS = (
    "id", "Site_ID", "SAP_Code", "Material_Code", "Equipment_Description",
    "Quantity", "Return_Reason", "Return_DN_No", "received_date",
    "received_dn_no", "received_qty", "PR_Number", "Lot_Number",
    "override_required", "override_reason", "status", "submitted_by",
    "submitted_at", "approved_by", "approved_at", "rejection_reason",
)


def archive_rejected_returns(
    older_than_days: int = 30, by_user: str = "system",
    conn: sqlite3.Connection = None,
) -> int:
    """Backlog #20 — copy-then-delete HOD-rejected pending_returns rows older
    than `older_than_days` into returns_history, keeping the staging table lean
    while preserving the audit trail. Returns the number of rows archived.

    Only touches rows with status='rejected'; the `returns` ledger and pending
    (awaiting-HOD) rows are never affected, so stock identity math is untouched.
    Idempotent-safe: re-running only archives rows that still qualify.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cutoff = days_ago_sql(int(older_than_days))
        # COALESCE(approved_at, submitted_at): prefer the rejection timestamp,
        # fall back to submission for any legacy row that never got one.
        where = (
            "status = 'rejected' "
            f"AND COALESCE(approved_at, submitted_at) < {cutoff}"
        )
        _src = ", ".join(_RETURNS_ARCHIVE_COLS)
        _dst = _src.replace("id,", "original_id,", 1)
        n = conn.execute(
            f"INSERT INTO returns_history ({_dst}, archived_by) "
            f"SELECT {_src}, ? FROM pending_returns WHERE {where}",
            (by_user,),
        ).rowcount
        conn.execute(f"DELETE FROM pending_returns WHERE {where}")
        conn.commit()
        return n
    finally:
        if _owns:
            conn.close()


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
            "SELECT SAP_Code, Material_Code, Equipment_Description, UOM, "
            "Minimum_Qty, COALESCE(Opening_Stock,0) AS Opening_Stock, "
            "COALESCE(Category,'Others') AS Category "
            "FROM inventory",
            conn,
        )
    except Exception:
        inv_df = pd.read_sql(
            "SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn,
        )
        inv_df["Material_Code"] = ""
        inv_df["Minimum_Qty"] = 0
        inv_df["Opening_Stock"] = 0
        inv_df["Category"] = "Others"

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

    numeric_cols = ["Total_Received", "Total_Consumed", "Total_Returned",
                    "Minimum_Qty", "Opening_Stock"]
    for col in numeric_cols:
        if col in live_df.columns:
            live_df[col] = pd.to_numeric(live_df[col], errors="coerce").fillna(0)

    # Closing = Opening + Received - Consumed - Returned.
    # Current_Stock kept as alias for backwards-compat with callers/tests.
    if "Opening_Stock" not in live_df.columns:
        live_df["Opening_Stock"] = 0
    live_df["Current_Stock"] = (
        live_df["Opening_Stock"]
        + live_df["Total_Received"]
        - live_df["Total_Consumed"]
        - live_df["Total_Returned"]
    )
    return live_df


# ── Round 13: unified EOD commit-set ────────────────────────────────────────
# Statuses that flow from pending_issues → consumption when commit_eod runs.
# Round 12 contract was 'pending_hod' only; Round 13 widens to cover per-row
# HOD approvals (so the per-row ✓ button no longer strands rows) AND the
# 'flagged' status (it was always intended to be commit-eligible — the UI
# pill exposes it, the backend just didn't honour it). 'rejected' is NOT in
# this set; rejected rows live in rejected_issues_archive, never in the
# permanent ledger. 'draft' is SK-side staging and never reaches the HOD.
_EOD_COMMIT_STATUSES = ("pending_hod", "approved", "flagged")
_EOD_PI_STATUS_PRED = (
    "(COALESCE(status,'pending_hod') IN ('pending_hod','approved','flagged'))"
)


def commit_eod(
    conn: sqlite3.Connection = None,
    *,
    hod_username: str | None = None,
) -> int:
    """
    Atomically commits all rows in pending_issues to the consumption table,
    then clears the staging queue.

    - Auto-syncs any extra columns from pending_issues into consumption first.
    - Populates legacy `"Approved By"` (space-named column) with `hod_username`
      for every committed row when provided.
    - Flips `supervisor_material_request_items.line_status='committed'` for
      every SMR-sourced row (matched via Source_Ref).
    - Round 13: commits rows in `_EOD_COMMIT_STATUSES` (pending_hod / approved
      / flagged). Rejected rows are NOT touched here — they should already be
      in rejected_issues_archive via hod_reject_pending_issue.
    - Returns the number of rows committed (0 if queue was empty).
    """
    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    c = conn.cursor()
    pending_df = pd.read_sql(
        f"SELECT * FROM pending_issues WHERE {_EOD_PI_STATUS_PRED}",
        conn,
    )

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

    # Refresh consumption columns AFTER potential ALTERs so the "Approved By"
    # presence check is accurate.
    c.execute("PRAGMA table_info(consumption)")
    cons_cols_final = {r[1] for r in c.fetchall()}
    write_approved_by = (
        hod_username is not None and "Approved By" in cons_cols_final
    )

    # Build a SQL-safe column list — every name quoted so spaces and reserved
    # words (e.g. "Approved By") survive.
    quoted_cols = [f'"{col}"' for col in cols_to_commit]
    if write_approved_by:
        quoted_cols.append('"Approved By"')
    placeholders = ", ".join(["?"] * len(quoted_cols))
    col_names    = ", ".join(quoted_cols)

    rows_committed = 0
    smr_line_ids: list[int] = []
    for _, row in pending_df.iterrows():
        clean_values = []
        for col in cols_to_commit:
            val = row[col]
            # Guard against Streamlit accidentally passing list objects
            if isinstance(val, (list, tuple, set)):
                val = ", ".join(map(str, val))
            clean_values.append(val)
        if write_approved_by:
            clean_values.append(hod_username)

        c.execute(
            f"INSERT INTO consumption ({col_names}) VALUES ({placeholders})",
            clean_values,
        )
        rows_committed += 1

        # If this was an SMR-sourced row, remember the line_id to flip after
        # the INSERT loop. Source_Ref shape: "SMR:{request_no}:{line_id}".
        src_ref = row.get("Source_Ref") if "Source_Ref" in pending_df.columns else None
        if isinstance(src_ref, str) and src_ref.startswith("SMR:"):
            try:
                smr_line_ids.append(int(src_ref.split(":")[-1]))
            except (ValueError, IndexError):
                pass

    c.execute(f"DELETE FROM pending_issues WHERE {_EOD_PI_STATUS_PRED}")

    # Flip SMR line_status='committed' for every SMR-sourced row in this commit.
    if smr_line_ids:
        phs = ", ".join(["?"] * len(smr_line_ids))
        c.execute(
            f"UPDATE supervisor_material_request_items "
            f"SET line_status='committed' WHERE id IN ({phs})",
            smr_line_ids,
        )

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

    Round 12: surfaces `Requested_By` (already on the row) so the HOD EOD
    grid can render the triple-layer SMR visibility (banner + column + badge)
    without a second roundtrip.

    Round 13: filter widened from 'pending_hod' only to the full
    _EOD_COMMIT_STATUSES set so per-row ✓ (approved) and ✗-converted-flagged
    rows remain visible in the HOD EOD queue until commit. Rejected rows are
    routed to rejected_issues_archive by hod_reject_pending_issue and never
    appear here.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    df = pd.read_sql(
        f"SELECT * FROM pending_issues "
        f"WHERE COALESCE(Site_ID,'HQ') = ? AND {_EOD_PI_STATUS_PRED}",
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
    Returns rows from the `requests` table joined to the inventory master so
    each row carries Material_Code + Material_Name alongside SAP_Code.

    site_id=None  → all sites (Admin global view)
    site_id='X'   → only requests where requesting_site='X' OR target_site='X'
    status='pending' → filter by FSM state
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()

    clauses, params = [], []
    if site_id:
        clauses.append("(r.requesting_site = ? OR r.target_site = ?)")
        params += [site_id, site_id]
    if status:
        clauses.append("r.status = ?")
        params.append(status)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    # LEFT JOIN inventory so requests for SAP codes not in the catalogue
    # still render (with Material_Code / Material_Name as empty strings).
    # The `r.*` keeps every existing column intact for downstream callers
    # (admin Pending Requests editor, HOD My Requests table).
    df = pd.read_sql(
        f"""SELECT r.*,
                  COALESCE(i.Material_Code, '')        AS Material_Code,
                  COALESCE(i.Equipment_Description, '') AS Material_Name,
                  COALESCE(i.UOM, '')                  AS UOM
           FROM requests r
           LEFT JOIN inventory i ON r.SAP_Code = i.SAP_Code
           {where}
           ORDER BY r.created_at DESC""",
        conn, params=params,
    )
    if _owns:
        conn.close()
    return _localize(df)


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


def get_reserved_qty(
    sap_code: str, site_id: str, conn: sqlite3.Connection = None,
) -> float:
    """Sum of ACTIVE reservations earmarking this item's stock at `site_id`.
    Never affects Current_Stock — it's a separate advisory layer."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(Qty),0) FROM stock_reservations "
            "WHERE TRIM(SAP_Code) = ? AND COALESCE(Site_ID,'HQ') = ? "
            "  AND status = 'active'",
            ((sap_code or "").strip(), site_id),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        if _owns:
            conn.close()


def _reserve_for_request(conn: sqlite3.Connection, req_id: int) -> None:
    """Earmark stock at the TARGET site for an approved transfer request.
    Idempotent — skips if an active reservation already exists for the request."""
    req = conn.execute(
        "SELECT target_site, SAP_Code, requested_qty FROM requests WHERE id = ?",
        (req_id,),
    ).fetchone()
    if not req:
        return
    target_site, sap, qty = req[0], req[1], float(req[2] or 0)
    if not target_site or not sap or qty <= 0:
        return
    exists = conn.execute(
        "SELECT 1 FROM stock_reservations "
        "WHERE request_id = ? AND status = 'active' LIMIT 1", (req_id,),
    ).fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO stock_reservations (SAP_Code, Site_ID, Qty, request_id, status) "
        "VALUES (?, ?, ?, ?, 'active')",
        (str(sap).strip(), target_site, qty, req_id),
    )


def _release_reservation_for_request(conn: sqlite3.Connection, req_id: int) -> None:
    """Release any active reservation tied to a request (fulfilled/rejected)."""
    conn.execute(
        "UPDATE stock_reservations SET status='released', "
        "released_at=CURRENT_TIMESTAMP "
        "WHERE request_id = ? AND status = 'active'", (req_id,),
    )


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

    Side-effect: keeps stock_reservations in sync — an 'approved' transfer
    earmarks stock at the target site; 'fulfilled'/'rejected' releases it.
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
    updated = c.rowcount > 0

    # Keep reservations consistent with the transfer's lifecycle.
    if updated:
        if new_status == "approved":
            _reserve_for_request(conn, req_id)
        elif new_status in ("fulfilled", "rejected"):
            _release_reservation_for_request(conn, req_id)

    conn.commit()
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

        # Reservations earmark stock for approved cross-site transfers. Only
        # meaningful per-site (a reservation lives at one site). Available is a
        # derived advisory figure — Current_Stock itself is never reduced.
        reserved_qty = (
            get_reserved_qty(sap_code, site_id, conn=conn) if site_id else 0.0
        )
        available_qty = current_stock - reserved_qty

        return {
            "found": True,
            "sap_code": sap_code,
            "description":   row0["Equipment_Description"],
            "uom":           row0.get("UOM", "") or "",
            "material_code": row0.get("Material_Code", "") or "",
            "minimum_qty":   float(row0.get("Minimum_Qty") or 0.0),
            "current_stock": current_stock,
            "reserved_qty":  reserved_qty,
            "available_qty": available_qty,
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


def audit_opening_stock_changes(old_df, new_df, by_user: str) -> int:
    """Backlog #23 — log every Opening_Stock edit made through the Master DB
    Editor. Compares the pre-edit `old_df` against the saved `new_df` (both
    inventory frames keyed by SAP_Code) and writes one OPENING_STOCK_EDIT audit
    row per changed existing item ("SAP=X: <old> -> <new>"). Returns the number
    of changes logged. New items (SAP not previously present) are not treated as
    edits — their opening value is set at creation, which is the intended path.
    """
    if old_df is None or new_df is None:
        return 0
    if "SAP_Code" not in old_df.columns or "SAP_Code" not in new_df.columns:
        return 0
    if "Opening_Stock" not in old_df.columns or "Opening_Stock" not in new_df.columns:
        return 0

    def _num(v):
        try:
            if v is None:
                return 0.0
            f = float(v)
            return 0.0 if f != f else f  # NaN → 0
        except (TypeError, ValueError):
            return 0.0

    old_map = {str(r.SAP_Code): _num(r.Opening_Stock)
               for r in old_df[["SAP_Code", "Opening_Stock"]].itertuples()}
    n = 0
    for r in new_df[["SAP_Code", "Opening_Stock"]].itertuples():
        sap = str(r.SAP_Code)
        if sap not in old_map:
            continue  # newly-created item, not an edit
        new_v = _num(r.Opening_Stock)
        old_v = old_map[sap]
        if abs(new_v - old_v) > 1e-9:
            log_audit_action(
                by_user, "OPENING_STOCK_EDIT", "inventory",
                f"SAP={sap}: {old_v:g} -> {new_v:g}",
            )
            n += 1
    return n


def submit_registration_request(username: str, password_hash: str, role: str, site_id: str, phone: str) -> tuple[bool, str]:
    """Puts a new user into the pending queue for Admin approval (Now includes Phone Number).

    Procurement roles (`logistics`, `warehouse_user`) are global — they are not
    tied to a single site. For those, `site_id` may arrive as None or empty;
    we normalise to "" so the NOT NULL constraint on pending_users.Site_ID is
    satisfied without a schema change. The Admin grid renders empty Site_ID
    as blank, which is the intended UX.
    """
    site_id = (site_id or "").strip()
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

        audit_site = site_id if site_id else "N/A (global role)"
        log_audit_action(username, "REGISTRATION_REQUEST", "pending_users", f"Requested role: {role} for site: {audit_site}")
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
    """
    Drop a notification into the background queue. Strips any HTML chrome
    from the message so what's stored (and later shown to WhatsApp users +
    in the Admin Console) is plain text only — never raw `<td>` etc.
    Silent if the phone number is obviously bogus.
    """
    if not phone_number or len(phone_number) < 5:
        return

    # Strip HTML tags and collapse whitespace so WhatsApp doesn't show
    # literal markup. We do this here (write side) AND in the Admin
    # Console (read side) for defence-in-depth.
    if message:
        message = re.sub(r"<[^>]+>", " ", str(message))
        message = re.sub(r"\s+", " ", message).strip()

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
                  COALESCE(i.Material_Code,'')        AS Material_Code,
                  i.Equipment_Description             AS Equipment_Description,
                  i.Equipment_Description             AS Material_Name,
                  i.UOM,
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
        "Received_by", "DN_Copy", "Bin_Location",
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
    return _localize(df)


def insert_returnable_item(
    conn: sqlite3.Connection = None,
    material_name: str = "",
    uom: str = "",
    qty: float = 1.0,
    borrower_name: str = "",
    borrower_phone: str = "",
    expected_return_time: str = "",
    site_id: str = "HQ",
    *,
    cv_detected: int = 0,
    cv_confidence: float | None = None,
    cv_employee_id: str | None = None,
    cv_tool_class: str | None = None,
) -> None:
    """Inserts a new borrowed-item record with status='borrowed'.

    Phase 6D adds four optional CV-audit columns. Manual entries leave
    them at the SQL NULL / 0 defaults so adoption reporting can honestly
    distinguish Smart-Scan loans from manual ones.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    conn.execute(
        """INSERT INTO returnable_items
               (material_name, uom, qty, borrower_name, borrower_phone,
                expected_return_time, status, Site_ID,
                cv_detected, cv_confidence, cv_employee_id, cv_tool_class)
           VALUES (?, ?, ?, ?, ?, ?, 'borrowed', ?, ?, ?, ?, ?)""",
        (material_name, uom, qty, borrower_name, borrower_phone,
         expected_return_time, site_id,
         int(cv_detected or 0),
         (float(cv_confidence) if cv_confidence is not None else None),
         cv_employee_id,
         cv_tool_class),
    )
    conn.commit()
    if _owns:
        conn.close()


def get_open_loans_for_employee(
    id_number: str,
    site_id: str = "",
    *,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return borrowed returnable_items rows attributable to one employee.

    Matches BOTH paths a loan can be created by:
      - CV path:     returnable_items.cv_employee_id = id_number
      - Manual path: returnable_items.borrower_name = employees.Name
                     (look up the employee record by ID first)

    `site_id` filter is optional — pass "" to search across all sites
    (matches the cross-site semantics of the existing return tab when
    the caller is on a global account).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        emp_row = conn.execute(
            "SELECT Name FROM employees WHERE ID_Number = ?",
            (id_number,),
        ).fetchone()
        emp_name = emp_row[0] if emp_row else None

        # Build a single SQL with the union of both match paths.
        params: list = [id_number]
        clauses = ["cv_employee_id = ?"]
        if emp_name:
            clauses.append("borrower_name = ?")
            params.append(emp_name)
        where_match = " OR ".join(clauses)

        q = (
            f"""SELECT id, material_name, uom, qty, borrower_name,
                       borrower_phone, given_time, expected_return_time,
                       Site_ID, cv_detected, cv_confidence,
                       cv_employee_id, cv_tool_class
                FROM returnable_items
                WHERE status = 'borrowed'
                  AND ({where_match})"""
        )
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        q += " ORDER BY expected_return_time ASC"
        df = pd.read_sql_query(q, conn, params=params)
        return _localize(df)
    finally:
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


def auto_draft_prs_for_below_minimum(
    site_id: str, target_factor: float = 1.0,
    conn: sqlite3.Connection = None,
) -> tuple[int, str, int, int]:
    """
    One-click PR drafting for replenishment. Creates a single batch PR (one
    draft line per item) covering every below-minimum item at `site_id`.
    Reuses insert_manual_pr so drafts are identical to manual ones
    (workflow_state='draft', editable until submitted to Logistics).

    Requested_Qty = max(target_factor × Minimum_Qty − Current_Stock, 0):
      • target_factor=1.0 → restore exactly to minimum (the gap / shortage).
      • target_factor=1.5 → order up to 1.5× minimum (a 50% buffer), etc.

    Idempotent by design: an item that ALREADY has an open pr_master line at
    this site is skipped, so a second click never floods duplicate drafts.

    Returns (added, pr_number, skipped, total_below_min). pr_number is "" when
    nothing was drafted.
    """
    try:
        target_factor = float(target_factor)
    except (TypeError, ValueError):
        target_factor = 1.0
    if target_factor < 1.0:
        target_factor = 1.0   # never order BELOW the minimum

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        low = get_low_stock_items(conn, site_id=site_id)
        if low is None or low.empty:
            return (0, "", 0, 0)

        # Items that already have an OPEN PR at this site → don't re-draft.
        open_saps = {
            str(r[0]).strip() for r in conn.execute(
                "SELECT DISTINCT SAP_Code FROM pr_master "
                "WHERE Site_ID = ? AND status = 'open'", (site_id,),
            ).fetchall()
        }

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        pr_number = f"PR-AUTO-{site_id}-{stamp}"
        added = skipped = 0
        for _, row in low.iterrows():
            sap = str(row.get("SAP_Code") or "").strip()
            # Order up to target_factor × minimum (factor 1.0 == shortage).
            minimum = float(row.get("Minimum_Qty") or 0)
            current = float(row.get("Current_Stock") or 0)
            qty = round(max(target_factor * minimum - current, 0.0), 4)
            if not sap or qty <= 0 or sap in open_saps:
                skipped += 1
                continue
            ok, _msg = insert_manual_pr(
                pr_number=pr_number,
                sap_code=sap,
                material_code=str(row.get("Material_Code") or ""),
                material_name=str(row.get("Equipment_Description") or ""),
                requested_qty=qty,
                site_id=site_id,
                uom=str(row.get("UOM") or ""),
                supplier="",
                est_cost_sar=0.0,
                notes="Auto-drafted from below-minimum stock alert.",
                conn=conn,
            )
            if ok:
                added += 1
                open_saps.add(sap)   # guard against dup SAP within this batch
            else:
                skipped += 1
        return (added, pr_number if added else "", skipped, int(len(low)))
    finally:
        if _owns:
            conn.close()


# A PR line is editable only BEFORE it is handed to Logistics. Once submitted,
# Logistics owns it (POs may already reference the number), so edits are locked.
_PR_EDITABLE_WHERE = (
    "status = 'open' AND COALESCE(logistics_status,'site_draft') = 'site_draft'"
)


def update_pr_line(
    line_id: int,
    requested_qty: float = None,
    pr_number: str = None,
    supplier: str = None,
    est_cost_sar: float = None,
    notes: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Edit a single pr_master line (by id) BEFORE it is submitted to Logistics.
    Only the provided fields change. Refuses to touch a line that is already
    submitted/closed (guarded by _PR_EDITABLE_WHERE) so in-flight PRs can't be
    altered underneath Logistics.
    """
    sets, params = [], []
    if requested_qty is not None:
        try:
            q = float(requested_qty)
        except (TypeError, ValueError):
            return False, "Requested Qty must be a number."
        if q <= 0:
            return False, "Requested Qty must be positive."
        sets.append("Requested_Qty = ?"); params.append(q)
    if pr_number is not None:
        if not str(pr_number).strip():
            return False, "PR Number cannot be blank."
        sets.append("PR_Number = ?"); params.append(str(pr_number).strip())
    if supplier is not None:
        sets.append("Supplier = ?"); params.append(str(supplier).strip())
    if est_cost_sar is not None:
        try:
            sets.append("Est_Cost_SAR = ?"); params.append(float(est_cost_sar))
        except (TypeError, ValueError):
            return False, "Estimated cost must be a number."
    if notes is not None:
        sets.append("Notes = ?"); params.append(str(notes).strip())
    if not sets:
        return False, "Nothing to update."

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE pr_master SET {', '.join(sets)} "
            f"WHERE id = ? AND {_PR_EDITABLE_WHERE}",
            (*params, int(line_id)),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, "Line not found or already submitted to Logistics."
        return True, "PR line updated."
    finally:
        if _owns:
            conn.close()


def rename_pr_number(
    old_pr_number: str, new_pr_number: str, site_id: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Reassign the PR number on every still-draft line of a PR at `site_id`
    (e.g. turn a generated 'PR-AUTO-…' into a real assigned PR number before
    submitting to Logistics). Only acts on pre-submit lines.
    """
    old = (old_pr_number or "").strip()
    new = (new_pr_number or "").strip()
    if not old or not new:
        return False, "Both old and new PR numbers are required."
    if old == new:
        return False, "New PR number is the same as the old one."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE pr_master SET PR_Number = ? "
            f"WHERE PR_Number = ? AND Site_ID = ? AND {_PR_EDITABLE_WHERE}",
            (new, old, site_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return False, "No editable lines found (already submitted?)."
        return True, f"Renamed {cur.rowcount} line(s) → {new}."
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
                      COALESCE(r.Bin_Location,'') AS Bin_Location,
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
    return _localize(df)


def get_item_bin_locations(
    sap_code: str, site_id: str, limit: int = 5,
    conn: sqlite3.Connection = None,
) -> list[str]:
    """
    Distinct bin/shelf locations an item has been put away in at a site, most
    recent first (lightweight 'where is this stored?' lookup). Metadata only —
    derived from receipts.Bin_Location; never affects stock math. Returns [] if
    the item has no tagged bins yet.
    """
    sap = (sap_code or "").strip()
    if not sap:
        return []
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT Bin_Location FROM receipts "
            "WHERE TRIM(SAP_Code) = ? AND COALESCE(Site_ID,'HQ') = ? "
            "  AND COALESCE(TRIM(Bin_Location),'') <> '' "
            "ORDER BY rowid DESC",
            (sap, site_id),
        ).fetchall()
    except Exception:
        return []
    finally:
        if _owns:
            conn.close()
    seen, out = set(), []
    for (bin_loc,) in rows:
        b = (bin_loc or "").strip()
        if b and b not in seen:
            seen.add(b)
            out.append(b)
        if len(out) >= limit:
            break
    return out


# ── UoM pack conversions (base-UoM data-entry aid) ─────────────────────────
def add_uom_conversion(
    sap_code: str, pack_uom: str, factor: float,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Define/replace a pack: 1 <pack_uom> = <factor> base units. Factor must
    be > 0; pack_uom must differ from the item's base UoM (a pack equal to the
    base unit is meaningless)."""
    sap = (sap_code or "").strip()
    pack = (pack_uom or "").strip()
    if not sap or not pack:
        return False, "SAP code and pack unit are required."
    try:
        f = float(factor)
    except (TypeError, ValueError):
        return False, "Factor must be a number."
    if f <= 0:
        return False, "Factor must be greater than 0."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        base_uom = conn.execute(
            "SELECT UOM FROM inventory WHERE TRIM(SAP_Code) = ?", (sap,),
        ).fetchone()
        if base_uom and (base_uom[0] or "").strip().lower() == pack.lower():
            return False, "Pack unit must differ from the item's base UoM."
        conn.execute(
            "INSERT INTO uom_conversions (SAP_Code, Pack_UOM, Factor) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(SAP_Code, Pack_UOM) DO UPDATE SET Factor = excluded.Factor",
            (sap, pack, f),
        )
        conn.commit()
        return True, f"1 {pack} = {f:g} base unit(s)."
    finally:
        if _owns:
            conn.close()


def get_uom_conversions(
    sap_code: str = None, conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """All pack conversions, or just those for one item (sap_code given)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if sap_code:
            df = pd.read_sql(
                "SELECT id, SAP_Code, Pack_UOM, Factor FROM uom_conversions "
                "WHERE TRIM(SAP_Code) = ? ORDER BY Pack_UOM",
                conn, params=((sap_code or "").strip(),),
            )
        else:
            df = pd.read_sql(
                "SELECT id, SAP_Code, Pack_UOM, Factor FROM uom_conversions "
                "ORDER BY SAP_Code, Pack_UOM", conn,
            )
    finally:
        if _owns:
            conn.close()
    return df


def delete_uom_conversion(
    conv_id: int, conn: sqlite3.Connection = None,
) -> bool:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM uom_conversions WHERE id = ?", (int(conv_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        if _owns:
            conn.close()


def convert_to_base(
    sap_code: str, pack_uom: str, pack_qty: float,
    conn: sqlite3.Connection = None,
) -> float:
    """Convert a quantity entered in `pack_uom` to the item's BASE units.
    If pack_uom is blank/unknown for the item, the quantity is returned as-is
    (already base units). Never raises — entry helper."""
    try:
        q = float(pack_qty)
    except (TypeError, ValueError):
        return 0.0
    pack = (pack_uom or "").strip()
    if not pack:
        return q
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT Factor FROM uom_conversions "
            "WHERE TRIM(SAP_Code) = ? AND Pack_UOM = ?",
            ((sap_code or "").strip(), pack),
        ).fetchone()
    finally:
        if _owns:
            conn.close()
    if not row:
        return q   # unknown pack → treat as already-base
    return round(q * float(row[0]), 4)


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
            "SELECT id, phone_number, message, status, created_at, sent_at, "
            "       COALESCE(attempts,0) AS attempts, "
            "       COALESCE(error_message,'') AS error_message "
            "FROM whatsapp_queue ORDER BY id DESC LIMIT ?",
            conn, params=(int(limit),),
        )
    finally:
        if _owns:
            conn.close()
    return _localize(df)


def retry_failed_whatsapp(
    msg_ids: list[int] = None, conn: sqlite3.Connection = None,
) -> int:
    """
    Flip selected failed rows back to 'pending'. If msg_ids is None,
    retries every failed row. Returns the number of rows reset.

    Also resets `attempts` to 0 so the row gets a fresh send budget — the
    worker caps auto-retries at MAX_SEND_ATTEMPTS, and without this reset a
    manually-retried row already at the cap would immediately re-fail.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.cursor()
        if msg_ids:
            ph = ",".join(["?"] * len(msg_ids))
            cur.execute(
                f"UPDATE whatsapp_queue SET status='pending', error_message=NULL, "
                f"attempts=0 WHERE id IN ({ph}) AND status='failed'",
                tuple(msg_ids),
            )
        else:
            cur.execute(
                "UPDATE whatsapp_queue SET status='pending', error_message=NULL, "
                "attempts=0 WHERE status='failed'",
            )
        conn.commit()
        return cur.rowcount
    finally:
        if _owns:
            conn.close()


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


def hod_reject_pending_issue(
    issue_id: int,
    *,
    rejected_by: str | None = None,
    reason: str | None = None,
    conn: sqlite3.Connection = None,
) -> bool:
    """Round 13: archive-then-delete a single pending_issues row.

    Copies the row into rejected_issues_archive with the HOD's username +
    reason, then DELETEs from pending_issues so the queue stays lean. If the
    row is SMR-sourced (Source_Ref starts with 'SMR:'), flips the matching
    supervisor_material_request_items.line_status to 'rejected_at_hod' so the
    intent ledger reflects the HOD-side outcome (distinct from the SK-side
    'withdrawn_at_staging' flow).

    Back-compat: callers passing positional issue_id only continue to work;
    rejected_by + reason default to None and the archive row records that.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM pending_issues WHERE id = ?",
            (int(issue_id),),
        ).fetchone()
        if not row:
            return False

        # Resolve column names so we copy by name (defensive against schema
        # drift between pending_issues and rejected_issues_archive).
        cur = conn.execute("SELECT * FROM pending_issues WHERE id = ? LIMIT 1",
                           (int(issue_id),))
        pi_col_names = [d[0] for d in cur.description]
        row_dict = dict(zip(pi_col_names, row))

        conn.execute("PRAGMA table_info(rejected_issues_archive)")
        arch_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(rejected_issues_archive)"
        ).fetchall()}

        # Build the archive payload — only columns that exist on the archive.
        payload: dict = {}
        for k, v in row_dict.items():
            if k == "id":
                if "original_id" in arch_cols:
                    payload["original_id"] = v
                continue
            if k in arch_cols:
                payload[k] = v
        payload["rejected_by"] = (rejected_by or "").strip() or "unknown"
        if reason is not None:
            payload["reject_reason"] = (reason or "").strip() or None

        cols = list(payload.keys())
        phs  = ", ".join(["?"] * len(cols))
        quoted = ", ".join(f'"{c}"' for c in cols)
        conn.execute(
            f"INSERT INTO rejected_issues_archive ({quoted}) VALUES ({phs})",
            [payload[k] for k in cols],
        )

        # Round 13 — SMR-sourced row? Flip the matching SMR line to
        # 'rejected_at_hod' so the supervisor's intent ledger reflects the
        # HOD-side rejection without forcing a join through the archive.
        src_ref = row_dict.get("Source_Ref")
        if isinstance(src_ref, str) and src_ref.startswith("SMR:"):
            try:
                line_id = int(src_ref.split(":")[-1])
                conn.execute(
                    "UPDATE supervisor_material_request_items "
                    "SET line_status = 'rejected_at_hod' "
                    "WHERE id = ? AND line_status = 'active'",
                    (line_id,),
                )
            except (ValueError, IndexError):
                pass

        conn.execute(
            "DELETE FROM pending_issues WHERE id = ?", (int(issue_id),),
        )
        conn.commit()
        try:
            log_audit_action(
                rejected_by or "unknown", "REJECT_PENDING_ISSUE",
                "pending_issues",
                f"id={issue_id} sap={row_dict.get('SAP_Code')} "
                f"qty={row_dict.get('Quantity')} "
                f"reason={(reason or '')[:80]!r}",
            )
        except Exception:
            pass  # Audit failure must never block the reject.
        return True
    finally:
        if _owns:
            conn.close()


def hod_unapprove_pending_issue(
    issue_id: int,
    conn: sqlite3.Connection = None,
) -> bool:
    """Round 13: flip a per-row-approved pending_issues row back to
    'pending_hod' so the HOD can change their mind before bulk commit.
    No-op when the row isn't in 'approved' status."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_issues SET status='pending_hod' "
            "WHERE id = ? AND status = 'approved'",
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
            "       COALESCE(c.Tank_No,'') AS Tank_No, "
            "       COALESCE(c.Issued_By,'') AS Submitted_By, "
            "       COALESCE(c.Remarks,'') AS Remarks, "
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
            "       COALESCE(r.Remarks,'') AS Remarks, "
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
            "  COALESCE(i.Opening_Stock,0) + "
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
    lot_number: str = None,
) -> tuple[bool, str, int | None]:
    """
    Stage a physical-count → system-qty discrepancy for HOD approval.
    Returns (ok, message, adjustment_id).

    lot_number (optional): when set, this is a lot disposal write-off — on
    approval the posted consumption is tagged with the lot and the lot flips
    to 'disposed'.
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
                reason_code, notes, status, submitted_by, Lot_Number)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_hod', ?, ?)""",
            (site_id, sap_code.strip(), system_qty, counted_qty, variance,
             reason_code, (notes or "").strip(), submitted_by,
             (lot_number or None)),
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
            "SELECT Site_ID, SAP_Code, variance, reason_code, notes, status, "
            "       submitted_by, Lot_Number "
            "FROM stock_adjustments WHERE id = ?",
            (adjustment_id,),
        ).fetchone()
        if not row:
            return False, f"Adjustment #{adjustment_id} not found."
        (site_id, sap_code, variance, reason_code, notes, status,
         submitted_by, adj_lot) = row
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
            # Lot disposal: tag the write-off to the lot so v_lot_balance
            # zeroes that lot's remaining qty (not just the SAP total).
            if adj_lot and "Lot_Number" in cons_cols:
                insert_cols.append("Lot_Number")
                insert_vals.append(adj_lot)
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
        # Lot disposal: the write-off is posted → retire the lot.
        if adj_lot:
            c.execute(
                "UPDATE lots SET Status='disposed' "
                "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
                (adj_lot, sap_code, site_id),
            )
        conn.commit()
        log_audit_action(
            approver, "APPROVE_ADJUSTMENT", "stock_adjustments",
            f"id={adjustment_id} sap={sap_code} site={site_id} "
            f"var={variance:+g} posted={posted_ref}"
            + (f" lot={adj_lot}→disposed" if adj_lot else ""),
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
        _adj = conn.execute(
            "SELECT Lot_Number, SAP_Code, Site_ID FROM stock_adjustments "
            "WHERE id=? AND status='pending_hod'", (adjustment_id,),
        ).fetchone()
        cur = conn.execute(
            "UPDATE stock_adjustments "
            "SET status='rejected', approved_by=?, "
            "approved_at=CURRENT_TIMESTAMP, rejection_reason=? "
            "WHERE id=? AND status='pending_hod'",
            (approver, reason or f"Rejected by {approver}", adjustment_id),
        )
        if cur.rowcount == 0:
            conn.commit()
            return False, "Already processed or not found."
        # If this was a pending lot disposal, release the lot back to 'open'
        # (it was quarantined on submit to lock it out of FEFO).
        if _adj and _adj[0]:
            conn.execute(
                "UPDATE lots SET Status='open' "
                "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=? "
                "  AND Status='quarantine'",
                (_adj[0], _adj[1], _adj[2]),
            )
        conn.commit()
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
        return _localize(pd.read_sql(q, conn, params=tuple(params)))
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
        return _localize(pd.read_sql(q, conn, params=tuple(params)))
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


def dispose_lot(
    lot_number: str,
    sap_code: str,
    site_id: str,
    reason_code: str,
    notes: str,
    submitted_by: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, int | None]:
    """
    Submit a lot for disposal via the existing HOD stock-adjustment approval.

    Writes off the lot's remaining qty (system_qty=Remaining, counted_qty=0 →
    negative variance → a write-off consumption on approval) and quarantines
    the lot so it can't be consumed while the disposal is pending. On approval
    the lot flips to 'disposed'; on rejection it returns to 'open'. The ledger
    stays append-only (a reversal document, never an edit). Returns
    (ok, message, adjustment_id).
    """
    if reason_code not in ADJUSTMENT_REASONS:
        return False, f"Unknown reason code '{reason_code}'.", None
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        bal = conn.execute(
            "SELECT Remaining_Qty, Status FROM v_lot_balance "
            "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
            (lot_number, sap_code, site_id),
        ).fetchone()
        if not bal:
            return False, f"Lot {lot_number} not found at {site_id}.", None
        remaining, lot_status = float(bal[0] or 0), bal[1]
        if lot_status == "disposed":
            return False, f"Lot {lot_number} is already disposed.", None
        if remaining <= 0:
            return False, (f"Lot {lot_number} has no remaining qty to dispose "
                           f"(balance {remaining:g})."), None

        # Lock the lot out of FEFO while the disposal awaits approval.
        conn.execute(
            "UPDATE lots SET Status='quarantine' "
            "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
            (lot_number, sap_code, site_id),
        )
        conn.commit()

        ok, msg, adj_id = insert_stock_adjustment(
            site_id=site_id, sap_code=sap_code,
            system_qty=remaining, counted_qty=0.0,
            reason_code=reason_code,
            notes=f"Lot {lot_number} disposal · {notes or ''}".strip(" ·"),
            submitted_by=submitted_by, conn=conn, lot_number=lot_number,
        )
        if not ok:
            # Roll the quarantine back if staging failed.
            conn.execute(
                "UPDATE lots SET Status='open' "
                "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=? "
                "  AND Status='quarantine'",
                (lot_number, sap_code, site_id),
            )
            conn.commit()
            return False, msg, None
        return True, (f"Lot {lot_number} ({remaining:g}) submitted for "
                      f"disposal approval (adj #{adj_id})."), adj_id
    finally:
        if _owns:
            conn.close()


def _next_split_lot_number(conn, base_lot, sap, site) -> str:
    """Unique child lot number for a split: '<base>/S1', '/S2', … ."""
    n = 1
    while True:
        candidate = f"{base_lot}/S{n}"
        hit = conn.execute(
            "SELECT 1 FROM lots WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
            (candidate, sap, site),
        ).fetchone()
        if not hit:
            return candidate
        n += 1


def split_lot(
    lot_number: str,
    sap_code: str,
    site_id: str,
    split_qty: float,
    by_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, str | None]:
    """
    Split `split_qty` off an open lot into a NEW child lot (same SAP/site,
    inheriting expiry/supplier). Recorded as a within-SAP lot_transfer — the
    movement ledger is untouched and the SAP's Current_Stock is unchanged.
    Returns (ok, message, new_lot_number).
    """
    try:
        q = float(split_qty)
    except (TypeError, ValueError):
        return False, "Split qty must be numeric.", None
    if q <= 0:
        return False, "Split qty must be greater than 0.", None
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        bal = conn.execute(
            "SELECT vb.Remaining_Qty, l.Status, l.Expiry_Date, l.Supplier, "
            "       l.PR_Number, l.Received_Date "
            "FROM v_lot_balance vb JOIN lots l "
            "  ON l.Lot_Number=vb.Lot_Number AND l.SAP_Code=vb.SAP_Code "
            " AND l.Site_ID=vb.Site_ID "
            "WHERE vb.Lot_Number=? AND vb.SAP_Code=? AND vb.Site_ID=?",
            (lot_number, sap_code, site_id),
        ).fetchone()
        if not bal:
            return False, f"Lot {lot_number} not found at {site_id}.", None
        remaining, status = float(bal[0] or 0), bal[1]
        if status != "open":
            return False, f"Only an 'open' lot can be split (this is '{status}').", None
        if q >= remaining:
            return False, (f"Split qty {q:g} must be LESS than the lot's "
                           f"remaining {remaining:g} (use merge/dispose otherwise)."), None

        new_lot = _next_split_lot_number(conn, lot_number, sap_code, site_id)
        conn.execute(
            "INSERT INTO lots (Lot_Number, SAP_Code, Site_ID, Received_Date, "
            "Expiry_Date, Supplier, PR_Number, Status) "
            "VALUES (?,?,?,?,?,?,?, 'open')",
            (new_lot, sap_code, site_id, bal[5], bal[2], bal[3], bal[4]),
        )
        conn.execute(
            "INSERT INTO lot_transfers (From_Lot, To_Lot, SAP_Code, Site_ID, "
            "Qty, kind, by_user) VALUES (?,?,?,?,?, 'split', ?)",
            (lot_number, new_lot, sap_code, site_id, q, by_user),
        )
        conn.commit()
        log_audit_action(
            by_user or "system", "LOT_SPLIT", "lot_transfers",
            f"{lot_number} → {new_lot} qty={q:g} ({sap_code}/{site_id})",
        )
        return True, f"Split {q:g} from {lot_number} → new lot {new_lot}.", new_lot
    finally:
        if _owns:
            conn.close()


def merge_lots(
    from_lot: str,
    into_lot: str,
    sap_code: str,
    site_id: str,
    by_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """
    Move ALL of `from_lot`'s remaining qty into `into_lot` (same SAP/site).
    Recorded as a within-SAP lot_transfer; `from_lot` is marked 'exhausted'.
    Current_Stock is unchanged. Both lots must be 'open'.
    """
    if from_lot == into_lot:
        return False, "Cannot merge a lot into itself."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        def _bal(lot):
            return conn.execute(
                "SELECT Remaining_Qty, Status FROM v_lot_balance "
                "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
                (lot, sap_code, site_id),
            ).fetchone()
        src, dst = _bal(from_lot), _bal(into_lot)
        if not src:
            return False, f"Source lot {from_lot} not found at {site_id}."
        if not dst:
            return False, f"Target lot {into_lot} not found at {site_id}."
        if src[1] != "open" or dst[1] != "open":
            return False, "Both lots must be 'open' to merge."
        qty = float(src[0] or 0)
        if qty <= 0:
            return False, f"Source lot {from_lot} has no remaining qty to merge."

        conn.execute(
            "INSERT INTO lot_transfers (From_Lot, To_Lot, SAP_Code, Site_ID, "
            "Qty, kind, by_user) VALUES (?,?,?,?,?, 'merge', ?)",
            (from_lot, into_lot, sap_code, site_id, qty, by_user),
        )
        conn.execute(
            "UPDATE lots SET Status='exhausted' "
            "WHERE Lot_Number=? AND SAP_Code=? AND Site_ID=?",
            (from_lot, sap_code, site_id),
        )
        conn.commit()
        log_audit_action(
            by_user or "system", "LOT_MERGE", "lot_transfers",
            f"{from_lot} → {into_lot} qty={qty:g} ({sap_code}/{site_id})",
        )
        return True, f"Merged {qty:g} from {from_lot} into {into_lot}."
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


# ===========================================================================
# Phase C — Procurement chain helpers (Logistics + Warehouse portals)
# ===========================================================================
# These are the minimum surface Phase 2 / Phase 3 need. PO/DN/Return/
# Reschedule/ForceClose flow helpers are added in their respective phases.

def add_warehouse(
    warehouse_id: str,
    name: str,
    location: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    contact_email: str = "",
    created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Insert a new warehouse master row. UNIQUE on Warehouse_ID."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO warehouses (Warehouse_ID, Name, Location, "
            "Contact_Name, Contact_Phone, Contact_Email, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (warehouse_id.strip(), name.strip(), location.strip(),
             contact_name.strip(), contact_phone.strip(),
             contact_email.strip(), created_by),
        )
        conn.commit()
        return True, f"Warehouse {warehouse_id} added"
    except sqlite3.IntegrityError:
        return False, f"Warehouse {warehouse_id} already exists"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def list_warehouses(
    active_only: bool = True,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return all warehouses (active by default)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM warehouses"
        if active_only:
            q += " WHERE status='active'"
        q += " ORDER BY Warehouse_ID"
        return _localize(pd.read_sql_query(q, conn))
    finally:
        if _owns:
            conn.close()


def add_vendor(
    vendor_code: str,
    vendor_name: str,
    address: str = "",
    contact_name: str = "",
    contact_phone: str = "",
    contact_email: str = "",
    default_inco_terms: str = "",
    default_payment_terms: str = "",
    created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Insert a new vendor master row. UNIQUE on Vendor_Code."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO vendors (Vendor_Code, Vendor_Name, Address, "
            "Contact_Name, Contact_Phone, Contact_Email, "
            "Default_Inco_Terms, Default_Payment_Terms, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vendor_code.strip(), vendor_name.strip(), address.strip(),
             contact_name.strip(), contact_phone.strip(), contact_email.strip(),
             default_inco_terms.strip(), default_payment_terms.strip(),
             created_by),
        )
        conn.commit()
        return True, f"Vendor {vendor_code} added"
    except sqlite3.IntegrityError:
        return False, f"Vendor {vendor_code} already exists"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def list_vendors(
    active_only: bool = True,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return all vendors (active by default)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM vendors"
        if active_only:
            q += " WHERE status='active'"
        q += " ORDER BY Vendor_Name"
        return _localize(pd.read_sql_query(q, conn))
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# App notifications (in-app inbox)
# ---------------------------------------------------------------------------
# Recipient resolution: pass at least one of (recipient_user) OR
# (recipient_role [+ recipient_site OR recipient_warehouse]). The bell-icon
# query then UNIONs:
#   • user-targeted rows (recipient_user = me)
#   • role-broadcast rows where my (role, site, warehouse) match.

def queue_app_notification(
    event_key: str,
    title: str,
    body: str = "",
    severity: str = "info",
    recipient_user: str | None = None,
    recipient_role: str | None = None,
    recipient_site: str | None = None,
    recipient_warehouse: str | None = None,
    link_page: str | None = None,
    link_anchor: str | None = None,
    related_table: str | None = None,
    related_ref: str | None = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert a row into app_notifications. Returns the new id."""
    if severity not in ("info", "warning", "critical", "success"):
        severity = "info"
    if not (recipient_user or recipient_role):
        # No recipient = silently drop. Callers should validate at site of call.
        return 0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO app_notifications "
            "(recipient_user, recipient_role, recipient_site, recipient_warehouse, "
            " event_key, severity, title, body, link_page, link_anchor, "
            " related_table, related_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (recipient_user, recipient_role, recipient_site, recipient_warehouse,
             event_key, severity, title, body, link_page, link_anchor,
             related_table, related_ref),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def get_app_notifications(
    username: str,
    role: str,
    site_id: str | None = None,
    warehouse_id: str | None = None,
    unread_only: bool = False,
    limit: int = 50,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Bell-icon inbox query — user-targeted OR matching role broadcast."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Role-broadcast match: role matches AND (site is unspecified OR equal)
        # AND (warehouse is unspecified OR equal). NULL on either side of
        # site/warehouse means "any" — pattern lets us send to all-of-role.
        # The OR-group MUST be parenthesised so the optional `AND read_at IS NULL`
        # binds to BOTH branches, not just the second one (`OR ... AND ...`
        # left unparenthesised reads as `OR (... AND read_at IS NULL)` and
        # leaks read rows on the user-targeted branch).
        q = ("SELECT * FROM app_notifications WHERE ("
             "    recipient_user = ? "
             " OR (recipient_role = ? "
             "     AND (recipient_site IS NULL OR recipient_site = ?) "
             "     AND (recipient_warehouse IS NULL OR recipient_warehouse = ?))"
             ")")
        params: list = [username, role, site_id, warehouse_id]
        if unread_only:
            q += " AND read_at IS NULL"
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        df = pd.read_sql_query(q, conn, params=params)
        return _localize(df)
    finally:
        if _owns:
            conn.close()


def mark_notification_read(
    notification_id: int,
    conn: sqlite3.Connection = None,
) -> bool:
    """Mark a single notification as read."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "UPDATE app_notifications "
            "SET read_at = CURRENT_TIMESTAMP WHERE id = ? AND read_at IS NULL",
            (int(notification_id),),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def mark_all_notifications_read(
    username: str,
    role: str,
    site_id: str | None = None,
    warehouse_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Mark every notification visible to this user as read. Returns the
    number of rows updated. Mirrors the SELECT visibility rules in
    get_app_notifications so we never silently mark someone else's row."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE app_notifications "
            "SET read_at = CURRENT_TIMESTAMP "
            "WHERE read_at IS NULL AND ("
            "    recipient_user = ? "
            " OR (recipient_role = ? "
            "     AND (recipient_site IS NULL OR recipient_site = ?) "
            "     AND (recipient_warehouse IS NULL OR recipient_warehouse = ?))"
            ")",
            (username, role, site_id, warehouse_id),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        if _owns:
            conn.close()


def count_unread_notifications(
    username: str,
    role: str,
    site_id: str | None = None,
    warehouse_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Tight count query — used by the sidebar bell badge."""
    df = get_app_notifications(
        username, role, site_id=site_id, warehouse_id=warehouse_id,
        unread_only=True, limit=999, conn=conn,
    )
    return 0 if df is None or df.empty else int(len(df))


# ---------------------------------------------------------------------------
# WhatsApp trigger gate (respects config.WHATSAPP_TRIGGERS)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# PR → Logistics flow
# ---------------------------------------------------------------------------
# Site HOD marks a PR as "submitted to logistics" → it appears in the
# Logistics Portal queue. Logistics then issues 1..N POs against the PR.
# Closed PRs (status='closed' OR logistics_status='closed'/'force_closed')
# are hidden from the active queue but still visible in History.

def submit_pr_to_logistics(
    pr_number: str,
    site_id: str,
    username: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Flip every open pr_master row for this (PR_Number, Site_ID) to
    logistics_status='submitted'. Idempotent — re-submitting a row that's
    already in_po or closed is silently skipped."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pr_master "
            "SET logistics_status = 'submitted', "
            "    submitted_to_logistics_at = CURRENT_TIMESTAMP, "
            "    submitted_to_logistics_by = ? "
            "WHERE PR_Number = ? AND COALESCE(Site_ID,'HQ') = ? "
            "  AND COALESCE(logistics_status,'site_draft') "
            "      IN ('site_draft','submitted')",
            (username, pr_number, site_id),
        )
        if cur.rowcount == 0:
            return False, f"PR {pr_number} has no eligible lines to submit"
        conn.commit()
        try:
            log_audit_action(
                username, "SUBMIT_PR_TO_LOGISTICS", "pr_master",
                f"PR={pr_number} site={site_id} lines={cur.rowcount}",
            )
        except Exception:
            pass
        # In-app notification to Logistics role (broadcast)
        try:
            queue_app_notification(
                event_key="pr_submitted_to_logistics",
                title=f"New PR {pr_number} from {site_id}",
                body=f"{cur.rowcount} line(s) awaiting PO issuance",
                severity="info",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="pr_master",
                related_ref=pr_number,
                conn=conn,
            )
        except Exception:
            pass
        return True, f"Submitted {cur.rowcount} line(s) of PR {pr_number}"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def list_prs_for_logistics(
    site_id: str | None = None,
    include_history: bool = False,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """One row per (PR_Number, Site_ID) — the Logistics queue.

    Active queue = logistics_status='submitted' AND status='open'.
    History = anything closed OR force_closed (caller passes include_history=True)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT PR_Number, COALESCE(Site_ID,'HQ') AS Site_ID, "
            "       COUNT(*) AS line_count, "
            "       SUM(Requested_Qty) AS total_qty, "
            "       MIN(submitted_to_logistics_at) AS submitted_at, "
            "       MIN(Delivery_Date) AS earliest_delivery, "
            "       MAX(logistics_status) AS logistics_status, "
            "       MAX(status) AS pr_status "
            "FROM pr_master "
        )
        params: list = []
        if include_history:
            q += " WHERE COALESCE(logistics_status,'site_draft') NOT IN ('site_draft') "
        else:
            q += (" WHERE COALESCE(logistics_status,'site_draft') = 'submitted' "
                  "   AND status = 'open' ")
        if site_id:
            q += " AND COALESCE(Site_ID,'HQ') = ? "
            params.append(site_id)
        q += " GROUP BY PR_Number, Site_ID ORDER BY submitted_at DESC"
        df = pd.read_sql_query(q, conn, params=params)
        return _localize(df)
    finally:
        if _owns:
            conn.close()


def get_pr_lines(
    pr_number: str,
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """All lines for a PR (one Site or one row per site if site_id is None)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT id, PR_Number, COALESCE(Site_ID,'HQ') AS Site_ID, "
             "       SAP_Code, Material_Code, Material_Name, "
             "       Requested_Qty, UOM, "
             "       WBS_Number, Network, Plant, Delivery_Date, "
             "       Supplier, Est_Cost_SAR, Notes, "
             "       status, logistics_status, workflow_state, created_at "
             "FROM pr_master WHERE PR_Number = ?")
        params: list = [pr_number]
        if site_id:
            q += " AND COALESCE(Site_ID,'HQ') = ?"
            params.append(site_id)
        q += " ORDER BY id"
        df = pd.read_sql_query(q, conn, params=params)
        return _localize(df)
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# PO creation
# ---------------------------------------------------------------------------
# `create_po_manual()` and `process_po_pdf()` both funnel into the same
# insertion path: header → po_items (with rl_bl_family tagged at insert)
# → optional po_shipment_schedule rows. On success the linked pr_master
# rows flip to logistics_status='in_po' so the queue empties as POs land.

def _flip_pr_to_in_po(
    pr_number: str, site_id: str, conn: sqlite3.Connection,
) -> None:
    """Internal — flip all matching pr_master rows to logistics_status='in_po'
    once a PO is created for them. Site_ID may be NULL on legacy rows."""
    try:
        conn.execute(
            "UPDATE pr_master SET logistics_status='in_po' "
            "WHERE PR_Number = ? "
            "  AND COALESCE(Site_ID,'HQ') = COALESCE(?, 'HQ') "
            "  AND COALESCE(logistics_status,'site_draft') = 'submitted'",
            (pr_number, site_id or "HQ"),
        )
    except sqlite3.Error:
        pass


def create_po_manual(
    header: dict,
    items: list[dict],
    shipment_schedule: list[dict] | None = None,
    attachment_blob: bytes | None = None,
    attachment_name: str | None = None,
    attachment_mime: str | None = None,
    created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Insert a PO header + items. `header` keys (all optional except PO_Number):
       PO_Number, PR_Number, Site_ID, Vendor_Code, Vendor_Name,
       Inco_Terms, Payment_Terms, PO_Date, PO_Type, Quotation_No,
       Quotation_Date, Your_Reference, Our_Reference, Contact_Person,
       Contact_Email, Mobile, Our_Email, Expected_Delivery,
       Freight_Charges, Handling_Charges, Discount_Amount, Total_Amount,
       Amount_In_Words.
       Each item dict: Material_Code, Description, Qty, UOM, Unit_Price,
       Total_Price, PR_Number, WBS_Number, Network, Plant.
       rl_bl_family is auto-tagged from config.classify_rl_bl_family.
    """
    po_number = (header or {}).get("PO_Number", "").strip()
    if not po_number:
        return False, "PO Number is required"
    if not items:
        return False, "At least one item is required"

    try:
        from config import classify_rl_bl_family
    except ImportError:
        classify_rl_bl_family = lambda code, desc: None

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Header
        cols = [
            "PO_Number", "PR_Number", "Site_ID", "Vendor_Code", "Vendor_Name",
            "Inco_Terms", "Payment_Terms", "PO_Date", "PO_Type",
            "Quotation_No", "Quotation_Date", "Your_Reference", "Our_Reference",
            "Contact_Person", "Contact_Email", "Mobile", "Our_Email",
            "Expected_Delivery", "Freight_Charges", "Handling_Charges",
            "Discount_Amount", "Total_Amount", "Amount_In_Words", "source",
            "attachment_blob", "attachment_name", "attachment_mime", "created_by",
        ]
        vals = [
            po_number,
            header.get("PR_Number"), header.get("Site_ID"),
            header.get("Vendor_Code"), header.get("Vendor_Name"),
            header.get("Inco_Terms"), header.get("Payment_Terms"),
            header.get("PO_Date"), header.get("PO_Type"),
            header.get("Quotation_No"), header.get("Quotation_Date"),
            header.get("Your_Reference"), header.get("Our_Reference"),
            header.get("Contact_Person"), header.get("Contact_Email"),
            header.get("Mobile"), header.get("Our_Email"),
            header.get("Expected_Delivery"),
            float(header.get("Freight_Charges") or 0),
            float(header.get("Handling_Charges") or 0),
            float(header.get("Discount_Amount") or 0),
            float(header.get("Total_Amount") or 0),
            header.get("Amount_In_Words"),
            header.get("source") or "manual",
            attachment_blob, attachment_name, attachment_mime,
            created_by,
        ]
        try:
            conn.execute(
                f"INSERT INTO purchase_orders ({','.join(cols)}) "
                f"VALUES ({','.join('?'*len(cols))})",
                vals,
            )
        except sqlite3.IntegrityError:
            return False, f"PO {po_number} already exists"

        # Items
        for idx, it in enumerate(items, start=1):
            mat_code = (it.get("Material_Code") or "").strip()
            desc     = (it.get("Description") or "").strip()
            fam      = classify_rl_bl_family(mat_code, desc)
            try:
                qty = float(it.get("Qty") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue
            try:
                unit = float(it.get("Unit_Price") or 0)
            except (TypeError, ValueError):
                unit = 0.0
            try:
                total = float(it.get("Total_Price") or 0)
            except (TypeError, ValueError):
                total = 0.0
            conn.execute(
                "INSERT INTO po_items "
                "(PO_Number, line_no, Material_Code, Description, Qty, UOM, "
                " Unit_Price, Total_Price, PR_Number, WBS_Number, Network, Plant, "
                " rl_bl_family) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (po_number, idx, mat_code, desc, qty,
                 it.get("UOM"), unit, total,
                 it.get("PR_Number") or header.get("PR_Number"),
                 it.get("WBS_Number"), it.get("Network"), it.get("Plant"),
                 fam),
            )

        # Shipment schedule (optional, from PO Annexure)
        for sh in (shipment_schedule or []):
            conn.execute(
                "INSERT INTO po_shipment_schedule "
                "(PO_Number, shipment_no, material_group, target_date, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (po_number, sh.get("shipment_no"), sh.get("material_group"),
                 sh.get("target_date"), sh.get("notes")),
            )

        # Flip linked PR lines to in_po
        if header.get("PR_Number"):
            _flip_pr_to_in_po(header["PR_Number"], header.get("Site_ID") or "HQ", conn)

        conn.commit()

        # Phase 7D — Site-bound PO notification with strict vendor/financial
        # data masking. Build the payload through build_po_site_notification
        # which internally enforces hide_prices=True + hide_vendor=True so
        # there is no leak path. Fan out to BOTH HOD and SK at the
        # destination site (in-app + WhatsApp via existing po_issued gate).
        try:
            site_for_notif = header.get("Site_ID")
            if site_for_notif:
                summary = build_po_site_notification(po_number, conn=conn)
                for _role, _link in (("hod", "📋 HOD Portal"),
                                     ("store_keeper", "📝 Entry Log")):
                    queue_app_notification(
                        event_key="po_issued",
                        title=summary["title"],
                        body=summary["app_body"],
                        severity="success",
                        recipient_role=_role,
                        recipient_site=site_for_notif,
                        link_page=_link,
                        related_table="purchase_orders",
                        related_ref=po_number,
                        conn=conn,
                    )
                    for _ph in get_site_role_phones(_role, site_for_notif,
                                                    conn=conn):
                        fire_whatsapp_event("po_issued", _ph,
                                            summary["whatsapp_body"],
                                            conn=conn)
        except Exception:
            pass

        try:
            log_audit_action(
                created_by, "CREATE_PO", "purchase_orders",
                f"PO={po_number} PR={header.get('PR_Number')} "
                f"items={len(items)} vendor={header.get('Vendor_Code')}",
            )
        except Exception:
            pass
        return True, f"PO {po_number} created with {len(items)} item(s)"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def process_po_pdf(
    pdf_bytes: bytes,
    pr_number_hint: str | None = None,
    site_id_hint: str | None = None,
    created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, dict]:
    """Parse a PO PDF (matches the General Industries sample layout) and
    return (ok, message, extracted_dict). Caller can edit the dict in the
    UI before calling create_po_manual() to persist.

    Extracted shape:
        {
          "header": {PO_Number, PR_Number, PO_Date, Vendor_Code,
                     Vendor_Name, Inco_Terms, Payment_Terms, ...},
          "items":  [{Material_Code, Description, Qty, UOM, Unit_Price,
                      Total_Price}, ...],
          "shipment_schedule": [{shipment_no, material_group, target_date}, ...],
        }
    The X-mask in sample POs (last 4 chars 'XXXX') is preserved verbatim;
    production POs come through with the full 10-digit number unchanged."""
    if pdfplumber is None:
        return False, "pdfplumber not installed", {}
    if not pdf_bytes:
        return False, "Empty PDF", {}

    extracted = {"header": {}, "items": [], "shipment_schedule": []}

    try:
        from config import classify_rl_bl_family
    except ImportError:
        classify_rl_bl_family = lambda code, desc: None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            full_text = ""
            tables_all: list[list[list[str]]] = []
            for page in pdf.pages:
                full_text += "\n" + (page.extract_text() or "")
                for t in (page.extract_tables() or []):
                    tables_all.append(t)
    except Exception as e:
        return False, f"PDF parse failure: {type(e).__name__}: {e}", {}

    # ── Header field extraction (regex on the flat text) ────────────────
    H = extracted["header"]
    H["source"] = "pdf_upload"
    if pr_number_hint:
        H["PR_Number"] = pr_number_hint
    if site_id_hint:
        H["Site_ID"]   = site_id_hint

    # Patterns calibrated against the sample PO (Page 1 of 2 / Page 2 of 2).
    patterns = {
        # Round 15 — `\.?` after Order|Req covers `Purch. Order. No.` variants.
        "PO_Number":      r"Purch\.?\s*Order\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9X]+)",
        "PO_Date":        r"Purch\.?\s*Order\.?\s*Date\s*[:\-]?\s*([0-9XA-Za-z][\d\.\-/A-Za-z]+)",
        "PO_Type":        r"PO\s*Type\s*[:\-]?\s*(.+)",
        "Quotation_No":   r"Quotation\s*No\.?\s*[:\-]?\s*([^\n]*)",
        "Quotation_Date": r"Quotation\s*Date\s*[:\-]?\s*([^\n]*)",
        "PR_Number_PDF":  r"Purch\.?\s*Req\.?\s*No\.?\s*[:\-]?\s*([^\n]*)",
        "Vendor_Code":    r"Vendor\s*[:\-]?\s*0*([0-9]+)",
        "Inco_Terms":     r"Inco\s*Terms\s*[:\-]?\s*(.+)",
        "Payment_Terms":  r"Payment\s*Terms\s*[:\-]?\s*([^\n]*)",
        "Your_Reference": r"Your\s*Reference\s*[:\-]?\s*([^\n]*)",
        "Our_Reference":  r"Our\s*Reference\s*[:\-]?\s*([^\n]*)",
        "Contact_Person": r"Contact\s*[:\-]?\s*([^\n]+)",
        "Mobile":         r"Mobile\s*[:\-]?\s*([+\d\s]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, full_text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = (m.group(1) or "").strip().strip(":").strip()
            if key == "PR_Number_PDF" and val and not H.get("PR_Number"):
                H["PR_Number"] = val
            elif key != "PR_Number_PDF":
                H[key] = val

    # Vendor name — sample has it on the line right after "Vendor :<code>".
    vend_lines = re.search(
        r"Vendor\s*[:\-]?\s*\d+\s*\n([^\n]+)", full_text, re.IGNORECASE)
    if vend_lines:
        H["Vendor_Name"] = vend_lines.group(1).strip()

    # Email lines — two columns: vendor email (left) + Our_Email (right).
    emails = re.findall(r"[\w\.\-]+@[\w\.\-]+", full_text)
    if emails:
        H["Contact_Email"] = emails[0]
        if len(emails) > 1:
            H["Our_Email"] = emails[-1]

    # Footer totals
    for k, pat in {
        "Freight_Charges":  r"Freight\s*Charges\s*([\d,\.]+)",
        "Handling_Charges": r"Handling\s*Charges\s*([\d,\.]+)",
        "Discount_Amount":  r"Discount\s*Amount\s*([\d,\.]+)",
        "Total_Amount":     r"Total\s*Amount\s*([\d,\.]+)",
    }.items():
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            try:
                H[k] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

    # ── Line items (Round 15 rewrite) ───────────────────────────────────
    # GI POs ship in one of three layouts:
    #
    #   Layout A — Code-on-own-line, 7-column item row (PO#4710003114.pdf):
    #     GI-7002522
    #     001 SS 316L FILLER WIRE , DIA 2.4 MM 20.00 KG 85.00 255.00 1,955.00
    #     (srno → desc → qty → uom → unit_price → vat_amount → total_price)
    #
    #   Layout B — Single-line, 6-column item row (original sample):
    #     001  GI-8003100  CUMIFURAN SYRUP …  5,025.00  KG  10.00  50,250.00
    #
    #   Layout C — Code+srno on first line, desc+nums on second (legacy):
    #     001 GI-7002522
    #     CUMIFURAN SYRUP …  5,025.00 KG  10.00  50,250.00
    #
    # The new scanner walks line-by-line and applies each pattern. seen_lines
    # de-dupes across layouts so a row never enters the items list twice.
    NUMBER = r"[\d,]+\.?\d*"
    UOM_RE = r"[A-Za-z]{1,5}"

    # Layout A — code-only line, then srno + desc + numbers line.
    re_code_only = re.compile(r"^\s*(GI-\d{6,8})\s*$")
    # 7-column row: srno desc qty uom unit_price vat_amount total_price.
    re_row_7col = re.compile(
        rf"^\s*(\d{{1,3}})\s+(.+?)\s+({NUMBER})\s+({UOM_RE})\s+"
        rf"({NUMBER})\s+({NUMBER})\s+({NUMBER})\s*$"
    )
    # 6-column row: srno desc qty uom unit_price total_price.
    re_row_6col = re.compile(
        rf"^\s*(\d{{1,3}})\s+(.+?)\s+({NUMBER})\s+({UOM_RE})\s+"
        rf"({NUMBER})\s+({NUMBER})\s*$"
    )
    # Layout B — single-line with code in the middle.
    re_inline = re.compile(
        rf"^\s*(\d{{1,3}})\s+(GI-\d{{6,8}})\s+(.+?)\s+({NUMBER})\s+({UOM_RE})"
        rf"(?:\s+({NUMBER}))?(?:\s+({NUMBER}))?\s*$"
    )

    def _f(s: str | None) -> float:
        try:
            return float((s or "0").replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    seen_lines: set[int] = set()
    lines = full_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Try Layout A first: a bare code line, with a row on the very next line.
        m_code = re_code_only.match(line)
        if m_code and i + 1 < len(lines):
            mat = m_code.group(1).strip()
            nxt = lines[i + 1]
            m7 = re_row_7col.match(nxt)
            m6 = re_row_6col.match(nxt) if not m7 else None
            picked = m7 or m6
            if picked:
                try:
                    line_no = int(picked.group(1))
                except ValueError:
                    i += 1
                    continue
                if line_no not in seen_lines:
                    desc = picked.group(2).strip()
                    qty  = _f(picked.group(3))
                    uom  = picked.group(4).strip()
                    if m7:
                        unit  = _f(picked.group(5))
                        # group(6) is VAT — captured but not stored separately.
                        total = _f(picked.group(7))
                    else:  # m6 — no VAT column
                        unit  = _f(picked.group(5))
                        total = _f(picked.group(6))
                    if qty > 0:
                        seen_lines.add(line_no)
                        extracted["items"].append({
                            "line_no": line_no,
                            "Material_Code": mat,
                            "SAP_Code":      mat,  # GI POs use GI-NNNNNNN as the SAP code
                            "Description":   desc,
                            "Qty":           qty,
                            "UOM":           uom,
                            "Unit_Price":    unit,
                            "Total_Price":   total,
                            "rl_bl_family":  classify_rl_bl_family(mat, desc),
                        })
                        i += 2
                        continue

        # Layout B — single-line inline pattern.
        m_inl = re_inline.match(line)
        if m_inl:
            try:
                line_no = int(m_inl.group(1))
            except ValueError:
                i += 1
                continue
            if line_no in seen_lines:
                i += 1
                continue
            mat   = m_inl.group(2).strip()
            desc  = m_inl.group(3).strip()
            qty   = _f(m_inl.group(4))
            uom   = m_inl.group(5).strip()
            unit  = _f(m_inl.group(6))
            total = _f(m_inl.group(7))
            if qty > 0:
                seen_lines.add(line_no)
                extracted["items"].append({
                    "line_no":       line_no,
                    "Material_Code": mat,
                    "SAP_Code":      mat,
                    "Description":   desc,
                    "Qty":           qty,
                    "UOM":           uom,
                    "Unit_Price":    unit,
                    "Total_Price":   total,
                    "rl_bl_family":  classify_rl_bl_family(mat, desc),
                })

        # Layout C — `<srno> GI-NNNNNNN` followed by `<desc> <qty> <uom> ...`.
        m_pair = re.match(r"^\s*(\d{1,3})\s+(GI-\d{6,8})\s*$", line)
        if m_pair and i + 1 < len(lines):
            try:
                line_no = int(m_pair.group(1))
            except ValueError:
                i += 1
                continue
            if line_no in seen_lines:
                i += 1
                continue
            mat = m_pair.group(2).strip()
            nxt = lines[i + 1]
            m_rest = re.match(
                rf"^\s*(.+?)\s+({NUMBER})\s+({UOM_RE})"
                rf"(?:\s+({NUMBER}))?(?:\s+({NUMBER}))?\s*$",
                nxt,
            )
            if m_rest:
                desc = m_rest.group(1).strip()
                qty  = _f(m_rest.group(2))
                uom  = m_rest.group(3).strip()
                unit = _f(m_rest.group(4))
                total = _f(m_rest.group(5))
                if qty > 0:
                    seen_lines.add(line_no)
                    extracted["items"].append({
                        "line_no":       line_no,
                        "Material_Code": mat,
                        "SAP_Code":      mat,
                        "Description":   desc,
                        "Qty":           qty,
                        "UOM":           uom,
                        "Unit_Price":    unit,
                        "Total_Price":   total,
                        "rl_bl_family":  classify_rl_bl_family(mat, desc),
                    })
                    i += 2
                    continue

        i += 1

    # Stable sort by line_no so downstream UI displays the items in PO order.
    extracted["items"].sort(key=lambda r: r.get("line_no", 0))

    # ── PO Annexure: Delivery Schedule ──────────────────────────────────
    # Sample format: SHIPMENT 01 / BRICK MATERIALS / 05.02.2026
    sch_pat = re.compile(
        r"(SHIPMENT\s*\d+)\s+([A-Z][A-Z ]+?)\s+(\d{2}\.\d{2}\.\d{4})",
        re.IGNORECASE,
    )
    for m in sch_pat.finditer(full_text):
        ship_no = m.group(1).strip()
        mat_grp = m.group(2).strip()
        date_raw = m.group(3).strip()
        # Convert DD.MM.YYYY → YYYY-MM-DD for sortability
        try:
            d, mo, y = date_raw.split(".")
            iso = f"{y}-{mo}-{d}"
        except ValueError:
            iso = date_raw
        extracted["shipment_schedule"].append({
            "shipment_no": ship_no,
            "material_group": mat_grp,
            "target_date": iso,
        })

    if not extracted["items"]:
        return False, "No line items extracted from PDF — please use manual entry", extracted
    return True, (f"Extracted {len(extracted['items'])} item(s) — "
                  "review the preview and click Save"), extracted


# ---------------------------------------------------------------------------
# PO listing + detail
# ---------------------------------------------------------------------------

def list_pos(
    site_id: str | None = None,
    vendor_code: str | None = None,
    pr_number: str | None = None,
    open_only: bool = True,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT po.PO_Number, po.PR_Number, po.Site_ID, "
            "       po.Vendor_Code, po.Vendor_Name, po.PO_Date, "
            "       po.Expected_Delivery, po.Inco_Terms, po.Payment_Terms, "
            "       po.Total_Amount, po.status, po.source, po.created_at, "
            "       (SELECT COUNT(*) FROM po_items pi WHERE pi.PO_Number = po.PO_Number) "
            "         AS line_count, "
            "       (SELECT COALESCE(SUM(pi.Qty),0) FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS total_qty, "
            "       (SELECT COALESCE(SUM(pi.Delivered_Qty),0) FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS delivered_qty "
            "FROM purchase_orders po WHERE 1=1"
        )
        params: list = []
        if open_only:
            q += " AND po.status NOT IN ('closed','force_closed','cancelled') "
        if site_id:
            q += " AND COALESCE(po.Site_ID,'HQ') = ? "
            params.append(site_id)
        if vendor_code:
            q += " AND po.Vendor_Code = ? "
            params.append(vendor_code)
        if pr_number:
            q += " AND po.PR_Number = ? "
            params.append(pr_number)
        q += " ORDER BY po.created_at DESC"
        return _localize(pd.read_sql_query(q, conn, params=params))
    finally:
        if _owns:
            conn.close()


PO_VENDOR_MASK_FIELDS = (
    # Identity
    "Vendor_Code", "Vendor_Name", "Contact_Person", "Contact_Email",
    "Mobile", "Our_Email",
    # Commercial terms
    "Inco_Terms", "Payment_Terms", "Quotation_No", "Quotation_Date",
    "Your_Reference", "Our_Reference",
    # Financial totals
    "Freight_Charges", "Handling_Charges", "Discount_Amount",
    "Total_Amount", "Amount_In_Words",
)


def get_po_detail(
    po_number: str,
    hide_prices: bool = False,
    hide_vendor: bool = False,
    conn: sqlite3.Connection = None,
) -> dict:
    """Return {'header': dict, 'items': DataFrame, 'shipments': DataFrame,
       'assignments': DataFrame}.

    hide_prices=True blanks Unit_Price + Total_Price columns on items —
    that's what the Warehouse Portal must see (per spec: warehouse never
    sees prices).

    Phase 7D — hide_vendor=True additionally blanks the 17 commercial /
    vendor / financial header fields listed in PO_VENDOR_MASK_FIELDS. Used
    by build_po_site_notification() so the destination-site notification
    never leaks supplier identity or commercial agreement details.
    PO_Type and PO_Date are intentionally kept visible — operational
    tracking, not commercial.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        header_df = pd.read_sql_query(
            "SELECT * FROM purchase_orders WHERE PO_Number = ?",
            conn, params=(po_number,),
        )
        header = header_df.iloc[0].to_dict() if not header_df.empty else {}
        # Don't return the heavy attachment_blob in the dict — caller fetches it
        # separately if it needs it.
        header.pop("attachment_blob", None)

        if hide_vendor and header:
            for _f in PO_VENDOR_MASK_FIELDS:
                if _f in header:
                    header[_f] = None

        items = pd.read_sql_query(
            "SELECT id, line_no, Material_Code, Description, Qty, UOM, "
            "       Unit_Price, Total_Price, PR_Number, WBS_Number, Network, "
            "       Plant, rl_bl_family, Delivered_Qty, Returned_Qty, "
            "       line_status, close_reason "
            "FROM po_items WHERE PO_Number = ? ORDER BY line_no",
            conn, params=(po_number,),
        )
        if hide_prices and not items.empty:
            items["Unit_Price"]  = None
            items["Total_Price"] = None

        ships = pd.read_sql_query(
            "SELECT shipment_no, material_group, target_date, actual_date, "
            "       status, notes "
            "FROM po_shipment_schedule WHERE PO_Number = ? "
            "ORDER BY target_date",
            conn, params=(po_number,),
        )
        asg = pd.read_sql_query(
            "SELECT id, Warehouse_ID, items_subset_json, Expected_Delivery, "
            "       assigned_by, assigned_at, acknowledged_at, acknowledged_by, "
            "       status, notes "
            "FROM po_assignments WHERE PO_Number = ? "
            "ORDER BY assigned_at DESC",
            conn, params=(po_number,),
        )
        return {
            "header": header,
            "items":  _localize(items),
            "shipments":  _localize(ships),
            "assignments": _localize(asg),
        }
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Phase 7D — Site-bound PO notification builder (strict data masking)
# ---------------------------------------------------------------------------
PO_SITE_NOTIFY_MAX_LINES = 5  # top-N items shown; rest rolled into "and N more"


def build_po_site_notification(po_number: str, *,
                               conn: sqlite3.Connection = None) -> dict:
    """Build the masked operational summary for site-bound PO notifications.

    Internally enforces BOTH masks (hide_prices=True, hide_vendor=True) so
    the message body can never leak vendor identity, commercial terms, or
    financial figures — even if the caller forgets to pass the flags.

    Returns {
        "site_id":           str | None,
        "title":             str,
        "app_body":          str,   # multi-line, used by in-app notification
        "whatsapp_body":     str,   # spec Q4(a): mirrors app_body line-for-line
        "pr_numbers":        str,   # distinct PRs across items, comma-joined
        "expected_delivery": str,
        "item_count":        int,
        "total_qty":         float,
    }

    Caller is responsible for skipping notifications when site_id is falsy.
    """
    detail = get_po_detail(po_number, hide_prices=True, hide_vendor=True,
                           conn=conn)
    header = detail.get("header") or {}
    items  = detail.get("items")
    site_id = header.get("Site_ID") or None
    expected_delivery = header.get("Expected_Delivery") or "—"

    # Distinct list of PR_Numbers across line items (spec Q2(b)).
    pr_set: list[str] = []
    if items is not None and not items.empty:
        for v in items["PR_Number"].dropna().astype(str).tolist():
            v = v.strip()
            if v and v not in pr_set:
                pr_set.append(v)
    pr_numbers = ", ".join(pr_set) if pr_set else (header.get("PR_Number") or "—")

    item_count = int(0 if items is None else len(items))
    total_qty = float(0.0 if items is None or items.empty
                      else items["Qty"].fillna(0).sum())

    # Per-line summary: top 5 lines, then "… and N more" overflow.
    line_strs: list[str] = []
    if items is not None and not items.empty:
        head = items.head(PO_SITE_NOTIFY_MAX_LINES)
        for _, row in head.iterrows():
            mc  = str(row.get("Material_Code") or "—").strip() or "—"
            desc = str(row.get("Description") or "").strip()
            qty = row.get("Qty") or 0
            uom = str(row.get("UOM") or "").strip()
            try:
                qty_str = f"{float(qty):g}"
            except (TypeError, ValueError):
                qty_str = str(qty)
            head_line = f"• {mc} — {desc} — {qty_str}"
            if uom:
                head_line += f" {uom}"
            line_strs.append(head_line)
        overflow = item_count - len(head)
        if overflow > 0:
            line_strs.append(f"… and {overflow} more line(s)")

    title = f"PO {po_number} issued for delivery to {site_id or '—'}"

    app_body_lines = [
        f"PO Number: {po_number}",
        f"PR Number(s): {pr_numbers}",
        f"Expected Delivery: {expected_delivery}",
        f"Items: {item_count} line(s) · Total Qty: {total_qty:g}",
    ]
    if line_strs:
        app_body_lines.append("")
        app_body_lines.extend(line_strs)
    app_body = "\n".join(app_body_lines)

    # Spec Q4(a): WhatsApp mirrors line-for-line. Add a 🧾 header + bolden
    # the title field with WhatsApp's *asterisk* convention; everything else
    # stays identical so warehouse staff get the exact item breakdown.
    wa_body_lines = [
        f"🧾 *PO {po_number} issued — delivery to {site_id or '—'}*",
        f"PR Number(s): {pr_numbers}",
        f"Expected Delivery: {expected_delivery}",
        f"Items: *{item_count}* line(s) · Total Qty: *{total_qty:g}*",
    ]
    if line_strs:
        wa_body_lines.append("")
        wa_body_lines.extend(line_strs)
    whatsapp_body = "\n".join(wa_body_lines)

    return {
        "site_id":           site_id,
        "title":             title,
        "app_body":          app_body,
        "whatsapp_body":     whatsapp_body,
        "pr_numbers":        pr_numbers,
        "expected_delivery": expected_delivery,
        "item_count":        item_count,
        "total_qty":         total_qty,
    }


# ---------------------------------------------------------------------------
# PO → Warehouse assignment
# ---------------------------------------------------------------------------

def assign_po_to_warehouse(
    po_number: str,
    warehouse_id: str,
    expected_delivery: str | None,
    items_subset_ids: list[int] | None,
    assigned_by: str,
    notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Assign a PO (or a subset of its items) to a warehouse for receiving.
    items_subset_ids: list of po_items.id or None for 'all items'."""
    import json as _json
    if not po_number or not warehouse_id:
        return False, "PO Number and Warehouse are required"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Confirm the warehouse exists
        wh = conn.execute(
            "SELECT 1 FROM warehouses WHERE Warehouse_ID = ? AND status='active'",
            (warehouse_id,),
        ).fetchone()
        if not wh:
            return False, f"Warehouse {warehouse_id} not active / not found"
        # Confirm the PO exists
        po = conn.execute(
            "SELECT status FROM purchase_orders WHERE PO_Number = ?",
            (po_number,),
        ).fetchone()
        if not po:
            return False, f"PO {po_number} not found"
        if po[0] in ("closed", "force_closed", "cancelled"):
            return False, f"PO {po_number} is {po[0]} — cannot assign"

        subset_json = _json.dumps(items_subset_ids) if items_subset_ids else None
        conn.execute(
            "INSERT INTO po_assignments "
            "(PO_Number, Warehouse_ID, items_subset_json, Expected_Delivery, "
            " assigned_by, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (po_number, warehouse_id, subset_json, expected_delivery,
             assigned_by, notes),
        )
        # Also write the Expected_Delivery on the PO header if not set.
        if expected_delivery:
            conn.execute(
                "UPDATE purchase_orders SET Expected_Delivery = "
                "  COALESCE(Expected_Delivery, ?) "
                "WHERE PO_Number = ?",
                (expected_delivery, po_number),
            )
        conn.commit()

        # In-app notification to the Warehouse role (warehouse-scoped)
        try:
            queue_app_notification(
                event_key="po_assigned_to_warehouse",
                title=f"PO {po_number} assigned to {warehouse_id}",
                body=(f"Expected {expected_delivery or '—'}. "
                      f"{'Subset of items' if items_subset_ids else 'All items'}."),
                severity="info",
                recipient_role="warehouse_user",
                recipient_warehouse=warehouse_id,
                link_page="🏭 Warehouse Portal",
                related_table="po_assignments",
                related_ref=po_number,
                conn=conn,
            )
        except Exception:
            pass

        try:
            log_audit_action(
                assigned_by, "ASSIGN_PO_TO_WAREHOUSE", "po_assignments",
                f"PO={po_number} WH={warehouse_id} "
                f"items={'all' if not items_subset_ids else len(items_subset_ids)}",
            )
        except Exception:
            pass
        return True, f"PO {po_number} → {warehouse_id} assigned"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Reschedule
# ---------------------------------------------------------------------------

def list_pending_reschedules(
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            "SELECT * FROM po_reschedule_requests WHERE status='pending' "
            "ORDER BY requested_at DESC", conn,
        ))
    finally:
        if _owns:
            conn.close()


# Round 15 — DN statuses where the goods are physically with the Warehouse
# (post-receive-from-vendor, pre-receive-at-site). Reschedule requests in
# these states route DIRECTLY to the warehouse_user — Logistics no longer
# needs to broker the date change.
_RESCHEDULE_WAREHOUSE_DIRECT_STATUSES = (
    "pending_logistics", "logistics_approved",
    "pending_hod", "hod_approved", "pending_sk",
)


def request_reschedule(
    po_number: str,
    dn_number: str | None,
    current_date: str | None,
    requested_date: str,
    reason: str,
    requested_by_role: str,
    requested_by: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Submit a reschedule request.

    Round 15 routing rules:
      - DN-attached request AND DN status ∈ _RESCHEDULE_WAREHOUSE_DIRECT_STATUSES
        → notify the receiving warehouse directly. Logistics is bypassed.
      - Otherwise (PO-level reschedule, or DN already received) → notify
        Logistics, preserving the pre-Round-15 behaviour.
    """
    if not requested_date or not reason:
        return False, "Requested date and reason are required"
    if requested_by_role not in ("warehouse_user", "hod", "admin"):
        return False, "Role not permitted to request reschedule"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Resolve the routing target BEFORE the insert so the audit + the
        # notification reflect the same decision.
        dn_status = None
        wh_id = None
        if dn_number:
            row = conn.execute(
                "SELECT status, Warehouse_ID FROM delivery_notes "
                "WHERE DN_Number = ?",
                (dn_number,),
            ).fetchone()
            if row:
                dn_status, wh_id = row[0], row[1]

        route_to_warehouse = bool(
            dn_status and dn_status in _RESCHEDULE_WAREHOUSE_DIRECT_STATUSES
            and wh_id
        )

        cur = conn.execute(
            "INSERT INTO po_reschedule_requests "
            "(PO_Number, DN_Number, current_date, requested_date, reason, "
            " requested_by_role, requested_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (po_number, dn_number, current_date, requested_date, reason,
             requested_by_role, requested_by),
        )
        conn.commit()
        try:
            if route_to_warehouse:
                queue_app_notification(
                    event_key="reschedule_requested",
                    title=f"Reschedule requested for DN {dn_number}",
                    body=(f"From {current_date or '—'} → {requested_date}. "
                          f"{reason[:120]}"),
                    severity="warning",
                    recipient_role="warehouse_user",
                    recipient_warehouse=wh_id,
                    link_page="🏭 Warehouse Portal",
                    related_table="po_reschedule_requests",
                    related_ref=str(cur.lastrowid),
                    conn=conn,
                )
            else:
                queue_app_notification(
                    event_key="reschedule_requested",
                    title=f"Reschedule requested for PO {po_number}",
                    body=(f"From {current_date or '—'} → {requested_date}. "
                          f"{reason[:120]}"),
                    severity="warning",
                    recipient_role="logistics",
                    link_page="🚚 Logistics Portal",
                    related_table="po_reschedule_requests",
                    related_ref=str(cur.lastrowid),
                    conn=conn,
                )
        except Exception:
            pass
        return True, (
            "Reschedule request sent to the warehouse."
            if route_to_warehouse
            else "Reschedule request submitted to Logistics."
        )
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def decide_reschedule(
    request_id: int,
    approve: bool,
    decided_by: str,
    decision_notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT PO_Number, DN_Number, requested_date, requested_by "
            "FROM po_reschedule_requests WHERE id = ? AND status='pending'",
            (int(request_id),),
        ).fetchone()
        if not row:
            return False, "Reschedule request not found or already decided"
        po_number, dn_number, requested_date, requested_by = row
        new_status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE po_reschedule_requests "
            "SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP, "
            "    decision_notes = ? "
            "WHERE id = ?",
            (new_status, decided_by, decision_notes, int(request_id)),
        )
        # On approval, push the new date to the PO + linked assignment + DN.
        if approve:
            conn.execute(
                "UPDATE purchase_orders SET Expected_Delivery = ? "
                "WHERE PO_Number = ?",
                (requested_date, po_number),
            )
            conn.execute(
                "UPDATE po_assignments SET Expected_Delivery = ? "
                "WHERE PO_Number = ?",
                (requested_date, po_number),
            )
            if dn_number:
                conn.execute(
                    "UPDATE delivery_notes SET DN_Date = ? WHERE DN_Number = ?",
                    (requested_date, dn_number),
                )
        conn.commit()
        try:
            queue_app_notification(
                event_key="reschedule_decided",
                title=(f"Reschedule {'approved' if approve else 'rejected'} "
                       f"for PO {po_number}"),
                body=(f"New date: {requested_date}. {decision_notes[:120]}"
                      if approve else f"Rejected: {decision_notes[:120]}"),
                severity="success" if approve else "warning",
                recipient_user=requested_by,
                link_page="📋 HOD Portal" if requested_by else None,
                related_table="po_reschedule_requests",
                related_ref=str(request_id),
                conn=conn,
            )
        except Exception:
            pass
        try:
            log_audit_action(
                decided_by, f"RESCHEDULE_{new_status.upper()}",
                "po_reschedule_requests",
                f"id={request_id} PO={po_number} new_date={requested_date}",
            )
        except Exception:
            pass
        return True, f"Reschedule {new_status}"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Force-close
# ---------------------------------------------------------------------------

def force_close_target(
    target_type: str,         # 'pr' | 'po' | 'po_item'
    target_ref: str,          # PR_Number | PO_Number | po_items.id as str
    reason: str,
    closed_by: str,
    notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    if target_type not in ("pr", "po", "po_item"):
        return False, "Invalid target_type"
    if not reason or len(reason.strip()) < 3:
        return False, "Reason is required (3+ characters)"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        site_id = None
        pr_number = None
        po_number = None

        if target_type == "pr":
            pr_number = target_ref
            row = conn.execute(
                "SELECT DISTINCT COALESCE(Site_ID,'HQ') FROM pr_master "
                "WHERE PR_Number = ?", (target_ref,)).fetchone()
            site_id = row[0] if row else None
            conn.execute(
                "UPDATE pr_master SET status='closed', "
                "    logistics_status='force_closed' "
                "WHERE PR_Number = ?", (target_ref,),
            )
        elif target_type == "po":
            po_number = target_ref
            row = conn.execute(
                "SELECT PR_Number, Site_ID FROM purchase_orders "
                "WHERE PO_Number = ?", (target_ref,)).fetchone()
            if row:
                pr_number, site_id = row
            conn.execute(
                "UPDATE purchase_orders "
                "SET status='force_closed', closed_at=CURRENT_TIMESTAMP, "
                "    closed_by=?, close_reason=? "
                "WHERE PO_Number = ?",
                (closed_by, reason, target_ref),
            )
            conn.execute(
                "UPDATE po_items SET line_status='force_closed', "
                "    close_reason=? "
                "WHERE PO_Number = ? AND line_status NOT IN ('delivered','closed')",
                (reason, target_ref),
            )
        else:  # po_item
            try:
                item_id = int(target_ref)
            except ValueError:
                return False, "po_item target_ref must be the numeric id"
            row = conn.execute(
                "SELECT po.PO_Number, po.PR_Number, po.Site_ID "
                "FROM po_items pi JOIN purchase_orders po "
                "  ON po.PO_Number = pi.PO_Number "
                "WHERE pi.id = ?", (item_id,)).fetchone()
            if row:
                po_number, pr_number, site_id = row
            conn.execute(
                "UPDATE po_items SET line_status='force_closed', close_reason=? "
                "WHERE id = ?", (reason, item_id),
            )

        conn.execute(
            "INSERT INTO po_force_closures "
            "(target_type, target_ref, Site_ID, PR_Number, PO_Number, "
            " reason, closed_by, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (target_type, str(target_ref), site_id, pr_number, po_number,
             reason, closed_by, notes),
        )
        conn.commit()

        # Notify Admin + originating Site HOD
        try:
            ref_label = f"{target_type.upper()} {target_ref}"
            for role, role_site in (("admin", None), ("hod", site_id)):
                queue_app_notification(
                    event_key=("pr_force_closed" if target_type == "pr"
                               else "po_force_closed"),
                    title=f"Force-closed: {ref_label}",
                    body=f"Reason: {reason[:200]}",
                    severity="critical",
                    recipient_role=role,
                    recipient_site=role_site,
                    link_page=("🛡️ Admin Portal" if role == "admin"
                               else "📋 HOD Portal"),
                    related_table=("pr_master" if target_type == "pr"
                                   else "purchase_orders"),
                    related_ref=str(target_ref),
                    conn=conn,
                )
        except Exception:
            pass
        try:
            log_audit_action(
                closed_by, f"FORCE_CLOSE_{target_type.upper()}",
                "po_force_closures",
                f"target={target_type}:{target_ref} reason={reason[:80]}",
            )
        except Exception:
            pass
        return True, f"Force-closed {target_type} {target_ref}"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Vendor returns
# ---------------------------------------------------------------------------

def raise_vendor_return(
    po_number: str,
    po_item_id: int | None,
    dn_number: str | None,
    qty: float,
    reason: str,
    raised_by_role: str,
    raised_by: str,
    expected_resupply: str | None = None,
    notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    if qty is None or float(qty) <= 0:
        return False, "Return quantity must be > 0"
    if not reason or len(reason.strip()) < 3:
        return False, "Reason is required"
    if raised_by_role not in (
        "logistics", "warehouse_user", "hod", "store_keeper", "admin"
    ):
        return False, "Role not permitted to raise return"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        po = conn.execute(
            "SELECT PR_Number, Site_ID, status FROM purchase_orders "
            "WHERE PO_Number = ?", (po_number,)).fetchone()
        if not po:
            return False, f"PO {po_number} not found"
        material_code = None
        if po_item_id:
            row = conn.execute(
                "SELECT Material_Code FROM po_items WHERE id = ? "
                "AND PO_Number = ?", (int(po_item_id), po_number)).fetchone()
            if row:
                material_code = row[0]
        conn.execute(
            "INSERT INTO po_returns "
            "(PO_Number, po_item_id, DN_Number, Material_Code, Qty, Reason, "
            " raised_by_role, raised_by, Expected_Resupply, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (po_number, po_item_id, dn_number, material_code,
             float(qty), reason, raised_by_role, raised_by,
             expected_resupply, notes),
        )
        # Reopen impacted PO/PR records so they show up in Logistics again.
        if po[2] in ("closed", "delivered", "partially_delivered"):
            conn.execute(
                "UPDATE purchase_orders "
                "SET status='partially_delivered', closed_at=NULL, "
                "    closed_by=NULL, close_reason=NULL "
                "WHERE PO_Number = ?", (po_number,),
            )
        if po_item_id:
            conn.execute(
                "UPDATE po_items "
                "SET Returned_Qty = COALESCE(Returned_Qty,0) + ?, "
                "    line_status = CASE "
                "      WHEN COALESCE(Delivered_Qty,0) "
                "         - (COALESCE(Returned_Qty,0) + ?) > 0 "
                "         THEN 'partially_delivered' "
                "      ELSE 'open' END "
                "WHERE id = ?",
                (float(qty), float(qty), int(po_item_id)),
            )
        conn.commit()

        try:
            queue_app_notification(
                event_key="vendor_return_raised",
                title=f"Vendor return raised on PO {po_number}",
                body=f"{qty} units · {reason[:150]}",
                severity="warning",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="po_returns",
                related_ref=po_number,
                conn=conn,
            )
        except Exception:
            pass
        try:
            log_audit_action(
                raised_by, "VENDOR_RETURN", "po_returns",
                f"PO={po_number} qty={qty} reason={reason[:80]}",
            )
        except Exception:
            pass
        return True, f"Vendor return raised on PO {po_number}"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def list_vendor_returns(
    open_only: bool = False,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM po_returns"
        if open_only:
            q += " WHERE status = 'open'"
        q += " ORDER BY raised_at DESC"
        return _localize(pd.read_sql_query(q, conn))
    finally:
        if _owns:
            conn.close()


def list_force_closures(
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            "SELECT * FROM po_force_closures ORDER BY closed_at DESC", conn,
        ))
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Closed PR / PO history (read-only viewer)
# ---------------------------------------------------------------------------

def list_closed_pos_history(
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            "SELECT PO_Number, PR_Number, Site_ID, Vendor_Name, PO_Date, "
            "       Expected_Delivery, Total_Amount, status, "
            "       closed_at, closed_by, close_reason, created_at "
            "FROM purchase_orders "
            "WHERE status IN ('closed','force_closed','cancelled') "
            "ORDER BY COALESCE(closed_at, created_at) DESC", conn,
        ))
    finally:
        if _owns:
            conn.close()


def fire_whatsapp_event(
    event_key: str,
    phone_number: str,
    message: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """Queue a WhatsApp message ONLY if the global toggle and per-event toggle
    are both True in config.py. Returns True if queued, False if suppressed.
    In-app notifications should still be sent regardless — that's the point
    of having the gate here and not at the queue_app_notification layer.
    """
    try:
        from config import WHATSAPP_ENABLED, WHATSAPP_TRIGGERS
    except ImportError:
        return False
    if not WHATSAPP_ENABLED:
        return False
    if not WHATSAPP_TRIGGERS.get(event_key, False):
        return False
    if not phone_number or not str(phone_number).strip():
        return False
    queue_whatsapp_alert(phone_number, message)
    return True


# ===========================================================================
# Phase 3 — Warehouse Portal data layer
# ===========================================================================
# Warehouse users live one-per-warehouse and see only POs assigned to them
# by Logistics. They never see prices — every helper that returns a PO view
# to a warehouse uses get_po_detail(hide_prices=True).
#
# DN state machine:
#   draft → pending_logistics → logistics_approved → pending_hod
#         → hod_approved → pending_sk → received
# (rejected is a terminal state from any pending_* step)

import json as _json_wh  # local alias used by JSON-encoded subset payloads


# ---------------------------------------------------------------------------
# Assignment-side: WH ack + receive-against-PO
# ---------------------------------------------------------------------------

def list_assignments_for_warehouse(
    warehouse_id: str,
    status_filter: list[str] | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """POs Logistics has routed to this warehouse. PRICES NEVER JOINED."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT a.id AS assignment_id, a.PO_Number, a.items_subset_json, "
            "       a.Expected_Delivery, a.assigned_by, a.assigned_at, "
            "       a.acknowledged_at, a.acknowledged_by, a.status, a.notes, "
            "       po.PR_Number, po.Site_ID, po.Vendor_Code, po.Vendor_Name, "
            "       po.PO_Date, po.Inco_Terms, po.Payment_Terms, "
            "       po.Quotation_No, po.Quotation_Date "
            "FROM po_assignments a "
            "JOIN purchase_orders po ON po.PO_Number = a.PO_Number "
            "WHERE a.Warehouse_ID = ?"
        )
        params: list = [warehouse_id]
        if status_filter:
            q += (" AND a.status IN ("
                  + ",".join("?" * len(status_filter)) + ")")
            params.extend(status_filter)
        q += " ORDER BY a.assigned_at DESC"
        return _localize(pd.read_sql_query(q, conn, params=params))
    finally:
        if _owns:
            conn.close()


def get_assignment_detail(
    assignment_id: int,
    conn: sqlite3.Connection = None,
) -> dict:
    """Full assignment view for a warehouse_user — strictly no prices.

    Returns {'assignment': dict, 'po_header': dict, 'items': DataFrame}
    where items is the subset (if any) or all PO items, with Unit_Price +
    Total_Price hard-blanked at the boundary."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        a_df = pd.read_sql_query(
            "SELECT * FROM po_assignments WHERE id = ?",
            conn, params=(int(assignment_id),),
        )
        if a_df.empty:
            return {"assignment": {}, "po_header": {}, "items": pd.DataFrame()}
        a = a_df.iloc[0].to_dict()
        po_number = a["PO_Number"]

        # Force price-hidden PO detail
        detail = get_po_detail(po_number, hide_prices=True, conn=conn)
        items = detail["items"]
        subset_json = a.get("items_subset_json")
        if subset_json:
            try:
                ids = set(int(i) for i in _json_wh.loads(subset_json))
                items = items[items["id"].isin(ids)].reset_index(drop=True)
            except (ValueError, TypeError):
                pass

        # Defensive: blank prices again in case caller forgot.
        for col in ("Unit_Price", "Total_Price"):
            if col in items.columns:
                items[col] = None

        header = detail["header"]
        # Strip every monetary header field too.
        for col in ("Total_Amount", "Freight_Charges", "Handling_Charges",
                     "Discount_Amount", "Amount_In_Words"):
            header.pop(col, None)

        return {"assignment": a, "po_header": header, "items": items}
    finally:
        if _owns:
            conn.close()


def acknowledge_assignment(
    assignment_id: int,
    warehouse_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Warehouse confirms it has seen + accepted the routing."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE po_assignments "
            "SET status='acknowledged', acknowledged_at=CURRENT_TIMESTAMP, "
            "    acknowledged_by=? "
            "WHERE id = ? AND status='assigned'",
            (warehouse_user, int(assignment_id)),
        )
        if cur.rowcount == 0:
            return False, "Assignment not found or already acknowledged"
        conn.commit()
        # Notify Logistics (gated WhatsApp + always-in-app)
        row = conn.execute(
            "SELECT PO_Number FROM po_assignments WHERE id=?",
            (int(assignment_id),)).fetchone()
        po_number = row[0] if row else ""
        try:
            queue_app_notification(
                event_key="warehouse_acknowledged",
                title=f"Warehouse acknowledged PO {po_number}",
                body=f"Assignment #{assignment_id} acknowledged by {warehouse_user}",
                severity="info",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="po_assignments",
                related_ref=po_number,
                conn=conn,
            )
        except Exception:
            pass
        try:
            log_audit_action(
                warehouse_user, "ACK_ASSIGNMENT", "po_assignments",
                f"id={assignment_id} PO={po_number}",
            )
        except Exception:
            pass
        return True, "Acknowledged"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


def record_warehouse_receipt(
    assignment_id: int,
    received_map: dict[int, float],
    warehouse_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Mark items as physically received at the warehouse against an
    assignment. `received_map` is {po_items.id: qty_received_this_event}.
    Bumps po_items.Delivered_Qty, flips line_status, and rolls the parent
    PO header status to delivered / partially_delivered."""
    if not received_map:
        return False, "Nothing to record"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        a = conn.execute(
            "SELECT PO_Number, status FROM po_assignments WHERE id = ?",
            (int(assignment_id),)).fetchone()
        if not a:
            return False, "Assignment not found"
        if a[1] in ("closed", "cancelled"):
            return False, f"Assignment is {a[1]}"
        po_number = a[0]

        # Validate each id belongs to this PO, then bump.
        affected = 0
        for raw_id, raw_qty in received_map.items():
            try:
                item_id = int(raw_id)
                qty = float(raw_qty)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            line = conn.execute(
                "SELECT Qty, Delivered_Qty, Returned_Qty FROM po_items "
                "WHERE id = ? AND PO_Number = ?",
                (item_id, po_number)).fetchone()
            if not line:
                continue
            ordered = float(line[0] or 0)
            already = float(line[1] or 0)
            returned = float(line[2] or 0)
            new_delivered = already + qty
            # Don't allow Delivered_Qty − Returned_Qty to exceed ordered.
            if new_delivered - returned > ordered + 1e-9:
                return False, (f"Cannot receive {qty}: would over-deliver "
                               f"line #{item_id} (ordered {ordered}, "
                               f"already delivered {already})")
            # Status: open / partial / delivered, RL/BL stays separate via po_items.rl_bl_family
            if new_delivered - returned >= ordered - 1e-9:
                new_status = "delivered"
            else:
                new_status = "partially_delivered"
            conn.execute(
                "UPDATE po_items SET Delivered_Qty = ?, line_status = ? "
                "WHERE id = ?",
                (new_delivered, new_status, item_id),
            )
            affected += 1

        if affected == 0:
            return False, "No valid line items in received_map"

        # Roll assignment status
        # All items on the PO that the assignment covers — recheck after bumps.
        # Simplification: if every PO line is 'delivered', the assignment too.
        agg = conn.execute(
            "SELECT COUNT(*) AS total, "
            "       SUM(CASE WHEN line_status='delivered' THEN 1 ELSE 0 END) AS done "
            "FROM po_items WHERE PO_Number = ?", (po_number,)).fetchone()
        if agg and agg[1] and agg[0] == agg[1]:
            conn.execute(
                "UPDATE po_assignments SET status='received' WHERE id=?",
                (int(assignment_id),))
            conn.execute(
                "UPDATE purchase_orders SET status='delivered' "
                "WHERE PO_Number=?", (po_number,))
        else:
            conn.execute(
                "UPDATE po_assignments SET status='partial' WHERE id=?",
                (int(assignment_id),))
            conn.execute(
                "UPDATE purchase_orders SET status='partially_delivered' "
                "WHERE PO_Number=?", (po_number,))
        conn.commit()

        try:
            queue_app_notification(
                event_key="warehouse_received",
                title=f"PO {po_number} — items received at warehouse",
                body=f"{affected} line(s) received by {warehouse_user}",
                severity="success",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="po_assignments",
                related_ref=po_number,
                conn=conn,
            )
        except Exception:
            pass
        try:
            log_audit_action(
                warehouse_user, "WAREHOUSE_RECEIVE", "po_items",
                f"PO={po_number} assignment={assignment_id} lines={affected}",
            )
        except Exception:
            pass
        return True, f"Received {affected} line(s) at warehouse"
    except sqlite3.Error as e:
        return False, f"DB error: {e}"
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Delivery Notes
# ---------------------------------------------------------------------------

def _generate_dn_number(
    warehouse_id: str, conn: sqlite3.Connection,
) -> str:
    """Sequence-driven DN number scoped to a warehouse + day.
    Format: DN-{WAREHOUSE}-{YYYYMMDD}-{seq}, e.g. DN-WH-A-20260616-003.
    Sequence resets per (warehouse, day)."""
    today = datetime.date.today().isoformat().replace("-", "")
    prefix = f"DN-{warehouse_id}-{today}-"
    row = conn.execute(
        "SELECT COUNT(*) FROM delivery_notes WHERE DN_Number LIKE ?",
        (prefix + "%",)).fetchone()
    seq = (row[0] if row else 0) + 1
    return f"{prefix}{seq:03d}"


def create_delivery_note(
    po_number: str,
    warehouse_id: str,
    site_id: str,
    line_items: list[dict],
    header: dict | None = None,
    created_by: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str, str]:
    """Build one DN draft for (po_number, warehouse_id, site_id).

    `line_items`: [{po_item_id, Qty, Lot_Number, Expiry_Date, Remarks}, ...]
    RL/BL strict separation is enforced HERE: if the items span multiple
    families, the call is rejected with a guidance message — the caller
    (Warehouse Portal Prepare DN tab) prepares one DN per family.

    Returns (ok, message, dn_number)."""
    if not po_number or not warehouse_id or not site_id:
        return False, "PO, Warehouse and Site are required", ""
    if not line_items:
        return False, "At least one line item is required", ""

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Pull po_items rows referenced by the caller, in one shot.
        ids = [int(li.get("po_item_id")) for li in line_items
               if li.get("po_item_id") is not None]
        if not ids:
            return False, "po_item_id missing on every line", ""
        rows = conn.execute(
            "SELECT id, Material_Code, Description, UOM, rl_bl_family, "
            "       Qty, Delivered_Qty, Returned_Qty "
            "FROM po_items WHERE PO_Number = ? AND id IN ("
            + ",".join("?" * len(ids)) + ")",
            tuple([po_number] + ids),
        ).fetchall()
        by_id = {r[0]: r for r in rows}
        if len(by_id) != len(ids):
            return False, "One or more line items not found on this PO", ""

        # RL/BL strict separation
        families = {by_id[i][4] for i in ids}
        if len(families - {None}) > 1:
            return False, (
                "Strict separation violated: this DN spans multiple RL/BL "
                "families. Prepare one DN per family."), ""
        family = next(iter(families - {None})) if families - {None} else None

        # Qty + over-ship guard
        # Available = Delivered_Qty (received from vendor at WH)
        #           − Returned_Qty
        #           − already-shipped via other live DNs for the SAME po_item
        # We must NEVER conflate "received-from-vendor" with "already shipped
        # to a site". A line that's been fully received at WH can spawn many
        # DNs, each consuming a slice of the available stock.
        for li in line_items:
            iid = int(li["po_item_id"])
            try:
                qty = float(li.get("Qty") or 0)
            except (TypeError, ValueError):
                return False, f"Bad Qty on line {iid}", ""
            if qty <= 0:
                return False, f"Qty must be > 0 on line {iid}", ""
            delivered = float(by_id[iid][6] or 0)
            returned  = float(by_id[iid][7] or 0)
            shipped_row = conn.execute(
                "SELECT COALESCE(SUM(dn_items.Qty), 0) FROM dn_items "
                "JOIN delivery_notes dn ON dn.DN_Number = dn_items.DN_Number "
                "WHERE dn_items.po_item_id = ? "
                "  AND dn.status NOT IN ('rejected','cancelled')",
                (iid,)).fetchone()
            shipped = float(shipped_row[0] or 0)
            available = delivered - returned - shipped
            if qty > available + 1e-9:
                return False, (
                    f"Line {iid}: shipping {qty} would exceed available "
                    f"({available:g}; delivered {delivered}, returned "
                    f"{returned}, already on live DNs {shipped})"), ""

        dn_number = _generate_dn_number(warehouse_id, conn)
        h = header or {}
        conn.execute(
            "INSERT INTO delivery_notes "
            "(DN_Number, PO_Number, Warehouse_ID, Site_ID, rl_bl_family, "
            " DN_Date, Vehicle_No, Driver_Name, Driver_Phone, Prepared_By, "
            " Remarks, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (dn_number, po_number, warehouse_id, site_id, family,
             h.get("DN_Date") or datetime.date.today().isoformat(),
             h.get("Vehicle_No"), h.get("Driver_Name"), h.get("Driver_Phone"),
             h.get("Prepared_By") or created_by,
             h.get("Remarks"), created_by),
        )

        for li in line_items:
            iid = int(li["po_item_id"])
            base = by_id[iid]
            conn.execute(
                "INSERT INTO dn_items "
                "(DN_Number, po_item_id, Material_Code, Description, "
                " Qty, UOM, Lot_Number, Expiry_Date, Remarks, "
                " rl_bl_family, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (dn_number, iid, base[1], base[2],
                 float(li.get("Qty") or 0), base[3],
                 li.get("Lot_Number"), li.get("Expiry_Date"),
                 li.get("Remarks"), base[4]),
            )

        conn.commit()
        try:
            log_audit_action(
                created_by, "CREATE_DN", "delivery_notes",
                f"DN={dn_number} PO={po_number} site={site_id} "
                f"lines={len(line_items)}",
            )
        except Exception:
            pass
        return True, f"DN {dn_number} drafted ({len(line_items)} line(s))", dn_number
    except sqlite3.Error as e:
        return False, f"DB error: {e}", ""
    finally:
        if _owns:
            conn.close()


def submit_dn_for_logistics(
    dn_number: str,
    warehouse_user: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Round 16: Warehouse-prepared DNs now flow directly to the HOD,
    bypassing the Logistics approval step. The function name is preserved
    so the existing Warehouse Portal caller works unchanged, but the
    behaviour is:

      1. UPDATE delivery_notes SET status='pending_hod' WHERE status='draft'.
      2. Action notification → HOD at the destination site (link to HOD's
         DN Approvals tab).
      3. Awareness notification → Logistics (`severity='info'`, no action
         required) so they still know POs are flowing.

    Legacy `logistics_decide_dn` remains in place as a safety net for any
    DN rows still sitting at `pending_logistics` from before the cutover —
    the Round 16 init_db migration sweeps those forward on first startup,
    so this is belt-and-suspenders.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE delivery_notes SET status='pending_hod' "
            "WHERE DN_Number = ? AND status='draft'",
            (dn_number,),
        )
        if cur.rowcount == 0:
            return False, "DN not in draft state"
        conn.commit()
        try:
            row = conn.execute(
                "SELECT PO_Number, Site_ID FROM delivery_notes "
                "WHERE DN_Number = ?", (dn_number,)).fetchone()
            po_no  = row[0] if row else "?"
            target = row[1] if row else "?"

            # Primary fan-out → the HOD at the destination site. This is the
            # actionable notification — the HOD opens DN Approvals and
            # decides. Severity is 'info' because the row is informational
            # until the HOD acts on it; it appears in their bell with the
            # ✅/❌ workflow buttons one tab away.
            queue_app_notification(
                event_key="dn_logistics_approved",  # reuse existing channel key
                title=f"DN {dn_number} awaiting your approval",
                body=(f"PO {po_no} → Site {target}. "
                      f"Prepared by {warehouse_user}."),
                severity="info",
                recipient_role="hod",
                recipient_site=target if target != "?" else None,
                link_page="📋 HOD Portal",
                related_table="delivery_notes",
                related_ref=dn_number,
                conn=conn,
            )

            # Secondary fan-out → Logistics, awareness only. Logistics no
            # longer approves DNs but still wants to know that POs they
            # issued are flowing through to the sites. severity='info'
            # keeps the bell quiet vs. action-required notifications.
            queue_app_notification(
                event_key="dn_logistics_approved",
                title=f"DN {dn_number} prepared (info only)",
                body=(f"PO {po_no} → Site {target}. "
                      f"Awaiting HOD approval. No action required from "
                      f"Logistics."),
                severity="info",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="delivery_notes",
                related_ref=dn_number,
                conn=conn,
            )
        except Exception:
            pass
        return True, "DN submitted — pending HOD approval"
    finally:
        if _owns:
            conn.close()


def logistics_decide_dn(
    dn_number: str,
    approve: bool,
    decided_by: str,
    decision_notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """DEPRECATED in Round 16. The DN approval workflow no longer routes
    through Logistics — Warehouse-prepared DNs flow directly to the HOD
    via `submit_dn_for_logistics`. This helper is retained ONLY as a
    safety net for legacy rows at status `pending_logistics` that might
    appear from a restore-from-backup. The Round 16 init_db migration
    sweeps any such rows forward to `pending_hod` on first startup, so
    this function should never see a matching row in normal operation.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE delivery_notes "
            "SET status = ?, "
            "    logistics_decided_at = CURRENT_TIMESTAMP, "
            "    logistics_decided_by = ?, "
            "    logistics_decision = ? "
            "WHERE DN_Number = ? AND status='pending_logistics'",
            ("pending_hod" if approve else "rejected",
             decided_by,
             "approved" if approve else "rejected",
             dn_number),
        )
        if cur.rowcount == 0:
            return False, "DN not pending Logistics approval"
        if not approve:
            conn.execute(
                "UPDATE delivery_notes SET rejection_reason = ? "
                "WHERE DN_Number = ?", (decision_notes, dn_number),
            )
        conn.commit()
        # Notify HOD on approval; notify Warehouse on rejection
        try:
            row = conn.execute(
                "SELECT PO_Number, Site_ID, Warehouse_ID "
                "FROM delivery_notes WHERE DN_Number=?",
                (dn_number,)).fetchone()
            po_no, site_id, wh_id = row or (None, None, None)
            if approve:
                queue_app_notification(
                    event_key="dn_logistics_approved",
                    title=f"Incoming delivery: DN {dn_number}",
                    body=f"PO {po_no} approved by Logistics — pending your approval",
                    severity="info",
                    recipient_role="hod", recipient_site=site_id,
                    link_page="📋 HOD Portal",
                    related_table="delivery_notes",
                    related_ref=dn_number, conn=conn,
                )
            else:
                queue_app_notification(
                    event_key="dn_logistics_approved",
                    title=f"DN {dn_number} rejected by Logistics",
                    body=decision_notes[:200] or "—",
                    severity="warning",
                    recipient_role="warehouse_user",
                    recipient_warehouse=wh_id,
                    link_page="🏭 Warehouse Portal",
                    related_table="delivery_notes",
                    related_ref=dn_number, conn=conn,
                )
        except Exception:
            pass
        try:
            log_audit_action(
                decided_by, f"DN_LOGISTICS_{'APPROVE' if approve else 'REJECT'}",
                "delivery_notes", f"DN={dn_number} notes={decision_notes[:80]}",
            )
        except Exception:
            pass
        return True, f"DN {'approved' if approve else 'rejected'}"
    finally:
        if _owns:
            conn.close()


def hod_decide_dn(
    dn_number: str,
    approve: bool,
    decided_by: str,
    decision_notes: str = "",
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """HOD approves a logistics-approved DN. On approval, the DN moves to
    pending_sk AND we mirror its lines into pending_receipts (status
    `pending_sk`) so the Site SK can confirm physical receipt from their
    Entry Log without HOD doing a second pass."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        dn = conn.execute(
            "SELECT PO_Number, Site_ID, Warehouse_ID, status "
            "FROM delivery_notes WHERE DN_Number=?",
            (dn_number,)).fetchone()
        if not dn:
            return False, "DN not found"
        if dn[3] != "pending_hod":
            return False, f"DN status is {dn[3]} — not pending HOD"
        po_no, site_id, wh_id, _ = dn

        new_status = "pending_sk" if approve else "rejected"
        conn.execute(
            "UPDATE delivery_notes "
            "SET status = ?, hod_decided_at = CURRENT_TIMESTAMP, "
            "    hod_decided_by = ?, rejection_reason = ? "
            "WHERE DN_Number = ?",
            (new_status, decided_by,
             None if approve else decision_notes,
             dn_number),
        )

        if approve:
            # Mirror DN items into pending_receipts so SK sees them in
            # the existing Receipt Staging flow. status='pending_sk' is a
            # new value reserved for DN-driven rows; HOD's existing
            # Pending Receipts tab filters for 'pending_hod' so these
            # don't bleed into that tab.
            c = conn.cursor()
            c.execute("PRAGMA table_info(pending_receipts)")
            existing = {r[1] for r in c.fetchall()}
            dni = pd.read_sql_query(
                "SELECT po_item_id, Material_Code, Description, Qty, UOM, "
                "       Lot_Number, Expiry_Date, Remarks "
                "FROM dn_items WHERE DN_Number = ? AND status='pending'",
                conn, params=(dn_number,),
            )
            for _, r in dni.iterrows():
                # Resolve SAP_Code from Material_Code via inventory if present
                sap_row = conn.execute(
                    "SELECT SAP_Code FROM inventory "
                    "WHERE Material_Code = ? LIMIT 1",
                    (r["Material_Code"],)).fetchone()
                sap_code = sap_row[0] if sap_row else r["Material_Code"]
                row = {
                    "SAP_Code":  sap_code,
                    "Quantity":  float(r["Qty"] or 0),
                    "Site_ID":   site_id,
                    "status":    "pending_sk",
                    "Date":      datetime.date.today().isoformat(),
                    "DN_No":     dn_number,
                    "DN_Number": dn_number,
                    "Warehouse_ID": wh_id,
                    "PO_Number_Source": po_no,
                    "PR_Number": None,
                    "Supplier":  None,
                    "Lot_Number": r.get("Lot_Number"),
                    "Expiry_Date": r.get("Expiry_Date"),
                    "Remarks":  r.get("Remarks"),
                }
                # UOM lives on pending_receipts in some installs only.
                if "UOM" in existing:
                    row["UOM"] = r.get("UOM")
                row = {k: v for k, v in row.items() if k in existing}
                cols = list(row.keys())
                conn.execute(
                    f"INSERT INTO pending_receipts ({','.join(cols)}) "
                    f"VALUES ({','.join('?'*len(cols))})",
                    [row[k] for k in cols],
                )

        conn.commit()

        # Notifications
        try:
            if approve:
                queue_app_notification(
                    event_key="dn_auto_generated",  # repurposing slot for SK ping
                    title=f"Incoming DN {dn_number} ready to receive",
                    body=f"PO {po_no} from {wh_id} — confirm physical receipt",
                    severity="info",
                    recipient_role="store_keeper",
                    recipient_site=site_id,
                    link_page="📝 Entry Log",
                    related_table="delivery_notes",
                    related_ref=dn_number, conn=conn,
                )
            else:
                queue_app_notification(
                    event_key="dn_logistics_approved",
                    title=f"DN {dn_number} rejected by HOD",
                    body=decision_notes[:200] or "—",
                    severity="warning",
                    recipient_role="warehouse_user",
                    recipient_warehouse=wh_id,
                    link_page="🏭 Warehouse Portal",
                    related_table="delivery_notes",
                    related_ref=dn_number, conn=conn,
                )
        except Exception:
            pass
        try:
            log_audit_action(
                decided_by, f"DN_HOD_{'APPROVE' if approve else 'REJECT'}",
                "delivery_notes", f"DN={dn_number}",
            )
        except Exception:
            pass
        return True, f"DN {'approved' if approve else 'rejected'}"
    finally:
        if _owns:
            conn.close()


def sk_mark_dn_received(
    dn_number: str,
    store_keeper: str,
    received_map: dict[int, float] | None = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Site SK confirms physical receipt of a HOD-approved DN.
    Writes one row into `receipts` per DN line (so identity math sees it
    immediately), flips the DN to 'received', drops the pending_receipts
    mirror rows, and notifies Logistics + Warehouse.

    `received_map` is optional {dn_items.id: confirmed_qty}; when omitted,
    the DN's qty is taken verbatim. Partial confirmations supported."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        dn = conn.execute(
            "SELECT PO_Number, Site_ID, Warehouse_ID, status "
            "FROM delivery_notes WHERE DN_Number=?",
            (dn_number,)).fetchone()
        if not dn:
            return False, "DN not found"
        if dn[3] != "pending_sk":
            return False, f"DN status is {dn[3]} — not pending SK"
        po_no, site_id, wh_id, _ = dn

        # Inspect columns on receipts so we only insert known ones
        c = conn.cursor()
        c.execute("PRAGMA table_info(receipts)")
        rcpt_cols = {r[1] for r in c.fetchall()}

        items = pd.read_sql_query(
            "SELECT id, po_item_id, Material_Code, Description, Qty, UOM, "
            "       Lot_Number, Expiry_Date, Remarks "
            "FROM dn_items WHERE DN_Number = ?",
            conn, params=(dn_number,),
        )
        if items.empty:
            return False, "DN has no items"

        for _, r in items.iterrows():
            iid = int(r["id"])
            qty_actual = float((received_map or {}).get(iid, r["Qty"]) or 0)
            if qty_actual <= 0:
                continue
            sap_row = conn.execute(
                "SELECT SAP_Code FROM inventory "
                "WHERE Material_Code = ? LIMIT 1",
                (r["Material_Code"],)).fetchone()
            sap_code = sap_row[0] if sap_row else r["Material_Code"]
            payload = {
                "SAP_Code":  sap_code,
                "Quantity":  qty_actual,
                "Date":      datetime.date.today().isoformat(),
                "Site_ID":   site_id,
                "Supplier":  "WAREHOUSE",
                "DN_No":     dn_number,
                "DN_Number": dn_number,
                "Warehouse_ID": wh_id,
                "PO_Number_Source": po_no,
                "Lot_Number": r.get("Lot_Number"),
                "Expiry_Date": r.get("Expiry_Date"),
                "Remarks":  r.get("Remarks") or f"Received via DN {dn_number}",
                "Received_by": store_keeper,
            }
            payload = {k: v for k, v in payload.items() if k in rcpt_cols}
            cols = list(payload.keys())
            conn.execute(
                f"INSERT INTO receipts ({','.join(cols)}) "
                f"VALUES ({','.join('?'*len(cols))})",
                [payload[k] for k in cols],
            )
            # Mark the DN item line as received with the actual qty
            conn.execute(
                "UPDATE dn_items SET status='received', sk_received_qty = ? "
                "WHERE id = ?", (qty_actual, iid),
            )

        # Flip DN header + clean up the pending_receipts mirror rows
        conn.execute(
            "UPDATE delivery_notes "
            "SET status='received', sk_received_at = CURRENT_TIMESTAMP, "
            "    sk_received_by = ? WHERE DN_Number = ?",
            (store_keeper, dn_number),
        )
        conn.execute(
            "DELETE FROM pending_receipts "
            "WHERE COALESCE(DN_Number,'') = ? AND status='pending_sk'",
            (dn_number,),
        )
        conn.commit()

        # Notifications + cache bust
        try:
            queue_app_notification(
                event_key="dn_received_by_sk",
                title=f"DN {dn_number} received at site",
                body=f"PO {po_no} closed at site {site_id}",
                severity="success",
                recipient_role="logistics",
                link_page="🚚 Logistics Portal",
                related_table="delivery_notes",
                related_ref=dn_number, conn=conn,
            )
            queue_app_notification(
                event_key="dn_received_by_sk",
                title=f"DN {dn_number} received at site",
                body=f"Site {site_id} confirmed",
                severity="success",
                recipient_role="warehouse_user",
                recipient_warehouse=wh_id,
                link_page="🏭 Warehouse Portal",
                related_table="delivery_notes",
                related_ref=dn_number, conn=conn,
            )
        except Exception:
            pass
        try:
            from cache_layer import bust_inventory_cache
            bust_inventory_cache()
        except Exception:
            pass
        try:
            log_audit_action(
                store_keeper, "DN_SK_RECEIVE", "receipts",
                f"DN={dn_number} PO={po_no}",
            )
        except Exception:
            pass
        return True, f"DN {dn_number} received at site"
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# DN listing / detail
# ---------------------------------------------------------------------------

def list_dns(
    warehouse_id: str | None = None,
    site_id: str | None = None,
    status_filter: list[str] | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = "SELECT * FROM delivery_notes WHERE 1=1"
        params: list = []
        if warehouse_id:
            q += " AND Warehouse_ID = ?"
            params.append(warehouse_id)
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        if status_filter:
            q += " AND status IN (" + ",".join("?" * len(status_filter)) + ")"
            params.extend(status_filter)
        q += " ORDER BY created_at DESC"
        return _localize(pd.read_sql_query(q, conn, params=params))
    finally:
        if _owns:
            conn.close()


def get_dn_detail(
    dn_number: str,
    conn: sqlite3.Connection = None,
) -> dict:
    """Return {'header': dict, 'items': DataFrame}. No prices joined —
    safe for Warehouse + Site HOD + SK views."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        h_df = pd.read_sql_query(
            "SELECT * FROM delivery_notes WHERE DN_Number = ?",
            conn, params=(dn_number,),
        )
        h = h_df.iloc[0].to_dict() if not h_df.empty else {}
        items = pd.read_sql_query(
            "SELECT id, po_item_id, Material_Code, Description, Qty, UOM, "
            "       Lot_Number, Expiry_Date, Remarks, rl_bl_family, "
            "       sk_received_qty, status "
            "FROM dn_items WHERE DN_Number = ? ORDER BY id",
            conn, params=(dn_number,),
        )
        return {"header": h, "items": _localize(items)}
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Internal returns (site → warehouse) — uses po_returns with role-tag
# ---------------------------------------------------------------------------

def record_internal_return(
    dn_number: str,
    items: list[dict],
    reason: str,
    raised_by_role: str,
    raised_by: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """A Site HOD/SK or Warehouse user flags a DN line as defective →
    we open a po_returns row tagged with the originating DN so Logistics
    can chase the vendor. `items` is [{dn_item_id, qty}, ...]."""
    if not items:
        return False, "No items to return"
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        dn = conn.execute(
            "SELECT PO_Number FROM delivery_notes WHERE DN_Number = ?",
            (dn_number,)).fetchone()
        if not dn:
            return False, "DN not found"
        po_number = dn[0]
        affected = 0
        for it in items:
            try:
                dn_item_id = int(it["dn_item_id"])
                qty = float(it.get("qty") or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if qty <= 0:
                continue
            row = conn.execute(
                "SELECT po_item_id, Material_Code FROM dn_items "
                "WHERE id = ? AND DN_Number = ?",
                (dn_item_id, dn_number)).fetchone()
            if not row:
                continue
            po_item_id, mat = row
            ok, _ = raise_vendor_return(
                po_number=po_number, po_item_id=int(po_item_id),
                dn_number=dn_number, qty=qty, reason=reason,
                raised_by_role=raised_by_role, raised_by=raised_by,
                conn=conn,
            )
            if ok:
                conn.execute(
                    "UPDATE dn_items SET status='returned' WHERE id = ?",
                    (dn_item_id,),
                )
                affected += 1
        if affected == 0:
            return False, "No valid return lines"
        conn.commit()
        return True, f"Raised {affected} return line(s) to vendor"
    finally:
        if _owns:
            conn.close()


def list_incoming_dns_for_sk(
    site_id: str,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """SK view: DNs pending_sk at this site. Read-only summary for the
    Entry Log expander."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            "SELECT dn.DN_Number, dn.PO_Number, dn.Warehouse_ID, dn.DN_Date, "
            "       dn.Vehicle_No, dn.Driver_Name, "
            "       (SELECT COUNT(*) FROM dn_items WHERE DN_Number=dn.DN_Number) "
            "         AS line_count, "
            "       (SELECT COALESCE(SUM(Qty),0) FROM dn_items "
            "          WHERE DN_Number=dn.DN_Number) AS total_qty "
            "FROM delivery_notes dn "
            "WHERE Site_ID = ? AND status = 'pending_sk' "
            "ORDER BY hod_decided_at DESC", conn, params=(site_id,),
        ))
    finally:
        if _owns:
            conn.close()


def list_pending_hod_dns(
    site_id: str,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """HOD-side view: DNs heading to this site, sitting at pending_hod.

    Round 15 — three-way Site_ID resolution (same pattern as
    list_force_closures_for_site). Warehouse Prepare-DN historically let the
    operator pick any site from the dropdown (fixed in Phase EE), so older
    DNs may carry the wrong Site_ID — or the legacy 'HQ' default. Falling
    through to the PO's Site_ID, then the PR's Site_ID, makes those legacy
    DNs visible to the correct HOD.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            """
            SELECT dn.*
            FROM delivery_notes dn
            LEFT JOIN purchase_orders po ON dn.PO_Number = po.PO_Number
            LEFT JOIN pr_master      pr ON po.PR_Number = pr.PR_Number
            WHERE dn.status = 'pending_hod'
              AND (
                    dn.Site_ID = ?
                 OR po.Site_ID = ?
                 OR pr.Site_ID = ?
              )
            ORDER BY dn.logistics_decided_at DESC
            """,
            conn, params=(site_id, site_id, site_id),
        ))
    finally:
        if _owns:
            conn.close()


def list_pending_logistics_dns(
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Logistics queue: DNs awaiting our approval before HOD sees them."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        return _localize(pd.read_sql_query(
            "SELECT * FROM delivery_notes WHERE status = 'pending_logistics' "
            "ORDER BY created_at DESC", conn,
        ))
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Phase 4 — Site-side read helpers (HOD "In-Transit" tab)
# ---------------------------------------------------------------------------

def list_in_transit_dns_for_site(
    site_id: str,
    include_received: bool = False,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Every DN that is somewhere in the pipeline bound for this site.
    Used by HOD's read-only In-Transit view. Sorted earliest-ETA first
    (DN_Date) so the soonest deliveries surface at the top.

    Status set is `pending_logistics`, `logistics_approved` (transient — DN
    sits here briefly before HOD review), `pending_hod`, `pending_sk`.
    Passing `include_received=True` also returns the most recent received
    DNs for the same view (handy 'just landed' strip)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        active_states = (
            "pending_logistics", "logistics_approved",
            "pending_hod", "pending_sk",
        )
        if include_received:
            states = active_states + ("received",)
        else:
            states = active_states
        placeholders = ",".join("?" * len(states))
        q = (
            "SELECT dn.DN_Number, dn.PO_Number, dn.Warehouse_ID, dn.Site_ID, "
            "       dn.DN_Date, dn.Vehicle_No, dn.Driver_Name, dn.Driver_Phone, "
            "       dn.status, dn.rl_bl_family, "
            "       dn.logistics_decided_at, dn.logistics_decided_by, "
            "       dn.hod_decided_at, dn.hod_decided_by, "
            "       dn.sk_received_at, dn.sk_received_by, "
            "       dn.created_at, "
            "       (SELECT COUNT(*) FROM dn_items "
            "          WHERE DN_Number=dn.DN_Number) AS line_count, "
            "       (SELECT COALESCE(SUM(Qty),0) FROM dn_items "
            "          WHERE DN_Number=dn.DN_Number) AS total_qty "
            "FROM delivery_notes dn "
            f"WHERE dn.Site_ID = ? AND dn.status IN ({placeholders}) "
            "ORDER BY "
            "  CASE dn.status "
            "    WHEN 'pending_sk' THEN 1 "
            "    WHEN 'pending_hod' THEN 2 "
            "    WHEN 'logistics_approved' THEN 3 "
            "    WHEN 'pending_logistics' THEN 4 "
            "    WHEN 'received' THEN 5 ELSE 9 END, "
            "  COALESCE(dn.DN_Date, dn.created_at) ASC"
        )
        return _localize(pd.read_sql_query(
            q, conn, params=(site_id, *states),
        ))
    finally:
        if _owns:
            conn.close()


def list_reschedule_requests_for_site(
    site_id: str,
    status_filter: list[str] | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Reschedule requests raised against POs bound for this site.
    Joined through purchase_orders so we can filter by Site_ID. Decision
    state (`pending` / `approved` / `rejected`) drives the status pill."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT r.id, r.PO_Number, r.DN_Number, r.current_date, "
            "       r.requested_date, r.reason, r.requested_by_role, "
            "       r.requested_by, r.requested_at, r.status, "
            "       r.decided_by, r.decided_at, r.decision_notes "
            "FROM po_reschedule_requests r "
            "LEFT JOIN purchase_orders po ON po.PO_Number = r.PO_Number "
            "WHERE COALESCE(po.Site_ID, ?) = ?"
        )
        params: list = [site_id, site_id]
        if status_filter:
            q += " AND r.status IN (" + ",".join("?" * len(status_filter)) + ")"
            params.extend(status_filter)
        q += " ORDER BY r.requested_at DESC"
        return _localize(pd.read_sql_query(q, conn, params=params))
    finally:
        if _owns:
            conn.close()


def sweep_delivery_reminders(
    today: datetime.date | None = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Fire T-2 / T-1 / T-0 reminders for upcoming PO Expected_Delivery
    and DN DN_Date dates. Returns the number of fresh notifications fired.

    Idempotent via the `delivery_reminders_sent` UNIQUE constraint — calling
    this function any number of times on the same day for the same target
    fires at most ONE event per (ref_type, ref_number, target_date, offset).

    Called by the WhatsApp worker once per day. Each fire writes a row to
    `app_notifications` (always visible in the bell) and queues a WhatsApp
    via `fire_whatsapp_event` (gated by config.WHATSAPP_TRIGGERS)."""
    today = today or datetime.date.today()
    _owns = conn is None
    if _owns:
        conn = get_connection()
    fired = 0
    try:
        # Build the list of (offset, target_iso) tuples we're firing today.
        targets: list[tuple[int, str]] = []
        for offset in (2, 1, 0):
            d = today + datetime.timedelta(days=offset)
            targets.append((offset, d.isoformat()))

        for offset, iso in targets:
            event_key = {
                2: "delivery_reminder_t_minus_2",
                1: "delivery_reminder_t_minus_1",
                0: "delivery_reminder_t_zero",
            }[offset]
            label = {2: "in 2 days", 1: "tomorrow", 0: "today"}[offset]

            # ── POs landing on this date ──────────────────────────────
            pos = conn.execute(
                "SELECT PO_Number, COALESCE(Site_ID,'HQ') AS Site_ID, "
                "       Vendor_Name "
                "FROM purchase_orders "
                "WHERE Expected_Delivery = ? "
                "  AND status NOT IN ('closed','force_closed','cancelled','delivered')",
                (iso,)).fetchall()
            for po_no, site_id, vendor in pos:
                try:
                    conn.execute(
                        "INSERT INTO delivery_reminders_sent "
                        "(ref_type, ref_number, target_date, offset_days) "
                        "VALUES ('po', ?, ?, ?)",
                        (po_no, iso, offset),
                    )
                except sqlite3.IntegrityError:
                    continue  # already fired
                queue_app_notification(
                    event_key=event_key,
                    title=f"PO {po_no} due {label} ({iso})",
                    body=f"Vendor: {vendor or '—'}",
                    severity="warning" if offset > 0 else "critical",
                    recipient_role="logistics",
                    link_page="🚚 Logistics Portal",
                    related_table="purchase_orders",
                    related_ref=po_no, conn=conn,
                )
                # Also ping the originating site HOD so they're not blindsided
                if site_id:
                    queue_app_notification(
                        event_key=event_key,
                        title=f"PO {po_no} due {label}",
                        body=f"Expected at site on {iso}",
                        severity="info",
                        recipient_role="hod",
                        recipient_site=site_id,
                        link_page="📋 HOD Portal",
                        related_table="purchase_orders",
                        related_ref=po_no, conn=conn,
                    )
                fired += 1
                # WhatsApp (gated)
                try:
                    fire_whatsapp_event(
                        event_key, "",
                        f"PO {po_no} due {label} ({iso})", conn=conn,
                    )
                except Exception:
                    pass

            # ── DNs landing on this date ──────────────────────────────
            dns = conn.execute(
                "SELECT DN_Number, PO_Number, Warehouse_ID, Site_ID "
                "FROM delivery_notes "
                "WHERE DN_Date = ? AND status IN ("
                "  'logistics_approved','pending_hod','pending_sk')",
                (iso,)).fetchall()
            for dn_no, po_no, wh_id, site_id in dns:
                try:
                    conn.execute(
                        "INSERT INTO delivery_reminders_sent "
                        "(ref_type, ref_number, target_date, offset_days) "
                        "VALUES ('dn', ?, ?, ?)",
                        (dn_no, iso, offset),
                    )
                except sqlite3.IntegrityError:
                    continue
                # Notify all three roles touching the DN
                for role, scope_site, scope_wh, page in (
                    ("logistics", None, None, "🚚 Logistics Portal"),
                    ("hod", site_id, None, "📋 HOD Portal"),
                    ("warehouse_user", None, wh_id, "🏭 Warehouse Portal"),
                ):
                    queue_app_notification(
                        event_key=event_key,
                        title=f"DN {dn_no} due {label}",
                        body=f"PO {po_no} → site {site_id} on {iso}",
                        severity="warning" if offset > 0 else "critical",
                        recipient_role=role,
                        recipient_site=scope_site,
                        recipient_warehouse=scope_wh,
                        link_page=page,
                        related_table="delivery_notes",
                        related_ref=dn_no, conn=conn,
                    )
                fired += 1
                try:
                    fire_whatsapp_event(
                        event_key, "",
                        f"DN {dn_no} due {label} ({iso})", conn=conn,
                    )
                except Exception:
                    pass
        conn.commit()
        return fired
    except sqlite3.Error:
        return fired
    finally:
        if _owns:
            conn.close()


def report_po_status(
    date_from: str | None = None,
    date_to: str | None = None,
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """PO-level rollup: PO_Number, PR_Number, vendor, site, PO_Date,
    Expected_Delivery, status, ordered/delivered/returned qty totals, line
    count. Date window applies to PO_Date."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT po.PO_Number, po.PR_Number, "
            "       COALESCE(po.Site_ID,'HQ') AS Site_ID, "
            "       po.Vendor_Code, po.Vendor_Name, po.PO_Date, "
            "       po.Expected_Delivery, po.status, po.source, "
            "       (SELECT COUNT(*)            FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS Line_Count, "
            "       (SELECT COALESCE(SUM(Qty),0)         FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS Ordered_Qty, "
            "       (SELECT COALESCE(SUM(Delivered_Qty),0) FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS Delivered_Qty, "
            "       (SELECT COALESCE(SUM(Returned_Qty),0)  FROM po_items pi "
            "          WHERE pi.PO_Number = po.PO_Number) AS Returned_Qty, "
            "       po.Total_Amount, po.created_at, po.closed_at "
            "FROM purchase_orders po WHERE 1=1"
        )
        params: list = []
        if site_id:
            q += " AND COALESCE(po.Site_ID,'HQ') = ?"
            params.append(site_id)
        if date_from:
            q += " AND COALESCE(po.PO_Date, po.created_at) >= ?"
            params.append(date_from)
        if date_to:
            q += " AND COALESCE(po.PO_Date, po.created_at) <= ?"
            params.append(date_to)
        q += " ORDER BY COALESCE(po.PO_Date, po.created_at) DESC"
        df = pd.read_sql_query(q, conn, params=params)
        if df.empty:
            return df, {"POs": 0}
        summary = {
            "POs":             int(len(df)),
            "Open":            int((df["status"].isin(
                ["open", "partially_delivered"])).sum()),
            "Delivered":       int((df["status"] == "delivered").sum()),
            "Closed_or_Force": int((df["status"].isin(
                ["closed", "force_closed", "cancelled"])).sum()),
            "Total_Value_SAR": float(df["Total_Amount"].fillna(0).sum()),
        }
        return _localize(df), summary
    finally:
        if _owns:
            conn.close()


def report_warehouse_throughput(
    date_from: str | None = None,
    date_to: str | None = None,
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """Per-warehouse DN counts split by state inside the window. Date
    window applies to delivery_notes.created_at."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT Warehouse_ID, "
            "       COUNT(*) AS DNs, "
            "       SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) AS Drafts, "
            "       SUM(CASE WHEN status='pending_logistics' THEN 1 ELSE 0 END) "
            "         AS Pending_Logistics, "
            "       SUM(CASE WHEN status='pending_hod' THEN 1 ELSE 0 END) "
            "         AS Pending_HOD, "
            "       SUM(CASE WHEN status='pending_sk' THEN 1 ELSE 0 END) "
            "         AS Pending_SK, "
            "       SUM(CASE WHEN status='received' THEN 1 ELSE 0 END) "
            "         AS Received, "
            "       SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) "
            "         AS Rejected, "
            "       SUM(CASE WHEN rl_bl_family='RL' THEN 1 ELSE 0 END) "
            "         AS RL_DNs, "
            "       SUM(CASE WHEN rl_bl_family='BL' THEN 1 ELSE 0 END) "
            "         AS BL_DNs "
            "FROM delivery_notes WHERE 1=1"
        )
        params: list = []
        if site_id:
            q += " AND Site_ID = ?"
            params.append(site_id)
        if date_from:
            q += " AND DATE(created_at) >= ?"
            params.append(date_from)
        if date_to:
            q += " AND DATE(created_at) <= ?"
            params.append(date_to)
        q += " GROUP BY Warehouse_ID ORDER BY DNs DESC"
        df = pd.read_sql_query(q, conn, params=params)
        if df.empty:
            return df, {"Warehouses": 0, "DNs": 0}
        summary = {
            "Warehouses":         int(len(df)),
            "DNs":                int(df["DNs"].sum()),
            "Received":           int(df["Received"].sum()),
            "Open_pipeline":      int((df["Pending_Logistics"] + df["Pending_HOD"]
                                        + df["Pending_SK"]).sum()),
            "RL_DNs":             int(df["RL_DNs"].sum()),
            "BL_DNs":             int(df["BL_DNs"].sum()),
        }
        return _localize(df), summary
    finally:
        if _owns:
            conn.close()


def report_force_closures(
    date_from: str | None = None,
    date_to: str | None = None,
    site_id: str | None = None,
    conn: sqlite3.Connection = None,
) -> tuple[pd.DataFrame, dict]:
    """Force-closures within the window. Filterable by site (matches direct
    Site_ID column OR origin via PR/PO joins, mirroring
    list_force_closures_for_site so the report sees the same set)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT fc.id, fc.target_type, fc.target_ref, "
            "       fc.Site_ID, fc.PR_Number, fc.PO_Number, "
            "       fc.reason, fc.closed_by, fc.closed_at, fc.notes "
            "FROM po_force_closures fc "
            "LEFT JOIN purchase_orders po ON po.PO_Number = fc.PO_Number "
            "LEFT JOIN ( "
            "    SELECT DISTINCT PR_Number, Site_ID FROM pr_master "
            ") pr ON pr.PR_Number = fc.PR_Number "
            "WHERE 1=1"
        )
        params: list = []
        if site_id:
            q += (" AND (fc.Site_ID = ? "
                  "      OR COALESCE(po.Site_ID,'') = ? "
                  "      OR COALESCE(pr.Site_ID,'') = ?)")
            params += [site_id, site_id, site_id]
        if date_from:
            q += " AND DATE(fc.closed_at) >= ?"
            params.append(date_from)
        if date_to:
            q += " AND DATE(fc.closed_at) <= ?"
            params.append(date_to)
        q += " ORDER BY fc.closed_at DESC"
        df = pd.read_sql_query(q, conn, params=params)
        if df.empty:
            return df, {"Closures": 0}
        summary = {
            "Closures": int(len(df)),
            "PR":       int((df["target_type"] == "pr").sum()),
            "PO":       int((df["target_type"] == "po").sum()),
            "Line":     int((df["target_type"] == "po_item").sum()),
        }
        return _localize(df), summary
    finally:
        if _owns:
            conn.close()


def list_force_closures_for_site(
    site_id: str,
    limit: int = 50,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Force-closures originating from this site (PR/PO/po_item).

    Many force-closure rows carry Site_ID directly (resolved by
    force_close_target). Older rows may have Site_ID NULL — we also match
    via PR_Number → pr_master.Site_ID and PO_Number → purchase_orders.Site_ID
    so nothing slips through the cracks."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = (
            "SELECT fc.* FROM po_force_closures fc "
            "LEFT JOIN purchase_orders po ON po.PO_Number = fc.PO_Number "
            "LEFT JOIN ( "
            "    SELECT DISTINCT PR_Number, Site_ID FROM pr_master "
            ") pr ON pr.PR_Number = fc.PR_Number "
            "WHERE fc.Site_ID = ? "
            "   OR COALESCE(po.Site_ID, '') = ? "
            "   OR COALESCE(pr.Site_ID, '') = ? "
            "ORDER BY fc.closed_at DESC LIMIT ?"
        )
        return _localize(pd.read_sql_query(
            q, conn, params=(site_id, site_id, site_id, int(limit)),
        ))
    finally:
        if _owns:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6A helpers — CV foundation (employees, tool_catalogue, cv_model_versions)
# ═══════════════════════════════════════════════════════════════════════════
# All helpers accept an optional `conn` so the pytest harness + bug_check can
# inject an in-memory connection (matches the convention every other helper
# in this file already follows). Mutations write to `system_audit_log` via
# log_audit_action() so admins have a trail of who created what.
import json as _json_6a


def _conn_ctx_6a(conn):
    """Internal: return (conn, owns_conn) — caller-owns vs auto-open."""
    if conn is None:
        return get_connection(), True
    return conn, False


# ── Employees ──────────────────────────────────────────────────────────────
def add_employee(
    id_number: str,
    name: str,
    phone: str = "",
    department: str = "",
    created_by: str = "system",
    *,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Insert one employee. Returns (False, msg) on duplicate ID_Number — never raises.

    Phase 7A: optional `site_id` binds the employee to a Site. NULL = unassigned
    (legacy default; Admin backfills via the bulk-assign widget).
    """
    id_number = (id_number or "").strip()
    name = (name or "").strip()
    if not id_number or not name:
        return False, "ID_Number and Name are required."
    site_clean = (site_id or "").strip() or None
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO employees (ID_Number, Name, Phone_Number, Department, Site_ID, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id_number, name, (phone or "").strip(), (department or "").strip(),
             site_clean, created_by),
        )
        _c.commit()
        log_audit_action(
            created_by, "EMPLOYEE_ADD", "employees",
            f"{id_number} ({name}) site={site_clean or '—'}",
        )
        return True, f"Employee {id_number} added."
    except sqlite3.IntegrityError:
        return False, f"Employee with ID_Number '{id_number}' already exists."
    finally:
        if _owns:
            _c.close()


def update_employee(
    id_number: str,
    *,
    name: str = None,
    phone: str = None,
    department: str = None,
    status: str = None,
    site_id: str = None,
    updated_by: str = "system",
    conn: sqlite3.Connection = None,
) -> bool:
    """Update any subset of fields for an existing employee. True if a row changed.

    Phase 7A: pass `site_id=""` (empty string) to clear the site binding back
    to NULL. Any non-empty string sets the binding. Pass `site_id=None` (the
    default) to leave Site_ID untouched.
    """
    sets, params = [], []
    if name is not None:
        sets.append("Name = ?"); params.append(name.strip())
    if phone is not None:
        sets.append("Phone_Number = ?"); params.append(phone.strip())
    if department is not None:
        sets.append("Department = ?"); params.append(department.strip())
    if status is not None:
        if status not in ("active", "inactive", "suspended"):
            return False
        sets.append("status = ?"); params.append(status)
    if site_id is not None:
        sets.append("Site_ID = ?")
        params.append(site_id.strip() or None)
    if not sets:
        return False
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(id_number)
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute(f"UPDATE employees SET {', '.join(sets)} WHERE ID_Number = ?", params)
        _c.commit()
        if cur.rowcount:
            log_audit_action(updated_by, "EMPLOYEE_UPDATE", "employees", id_number)
            return True
        return False
    finally:
        if _owns:
            _c.close()


def list_employees(
    *,
    status_filter: str = None,
    site_id_filter: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return all employees (optionally filter by status and/or Site_ID) as a DataFrame.

    Phase 7A: `site_id_filter` matches Site_ID exactly. Pass the literal string
    `"__UNASSIGNED__"` to fetch employees whose Site_ID IS NULL — used by the
    Admin bulk-assign banner.
    """
    _c, _owns = _conn_ctx_6a(conn)
    try:
        q = ("SELECT id, ID_Number, Name, Phone_Number, Department, Site_ID, status, "
             "created_by, created_at, updated_at FROM employees")
        clauses, params = [], []
        if status_filter:
            clauses.append("status = ?"); params.append(status_filter)
        if site_id_filter is not None:
            if site_id_filter == "__UNASSIGNED__":
                clauses.append("Site_ID IS NULL")
            else:
                clauses.append("Site_ID = ?"); params.append(site_id_filter)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY Name COLLATE NOCASE ASC"
        return pd.read_sql_query(q, _c, params=tuple(params))
    finally:
        if _owns:
            _c.close()


def list_employees_for_site(
    site_id: str,
    *,
    status_filter: str = "active",
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Convenience wrapper used by Phase 7B's Supervisor Material Request form.

    Returns only employees bound to `site_id`. Default status_filter='active'
    excludes deactivated/suspended workers from the supervisor's picker.
    """
    return list_employees(
        status_filter=status_filter,
        site_id_filter=site_id,
        conn=conn,
    )


def bulk_assign_employees_to_site(
    id_numbers: list[str],
    site_id: str,
    *,
    updated_by: str = "system",
    conn: sqlite3.Connection = None,
) -> int:
    """Phase 7A — Admin bulk-assigns N employees to a single Site_ID.

    Returns the number of rows updated. Used by the red "unassigned" banner.
    """
    if not id_numbers or not site_id:
        return 0
    _c, _owns = _conn_ctx_6a(conn)
    try:
        placeholders = ",".join("?" * len(id_numbers))
        cur = _c.execute(
            f"UPDATE employees SET Site_ID = ?, updated_at = CURRENT_TIMESTAMP "
            f"WHERE ID_Number IN ({placeholders})",
            (site_id.strip(), *id_numbers),
        )
        _c.commit()
        if cur.rowcount:
            log_audit_action(
                updated_by, "EMPLOYEE_BULK_SITE_ASSIGN", "employees",
                f"site={site_id} count={cur.rowcount}",
            )
        return cur.rowcount
    finally:
        if _owns:
            _c.close()


def get_employee_by_id_number(
    id_number: str,
    *,
    conn: sqlite3.Connection = None,
) -> dict | None:
    """Single-employee lookup. Returns dict or None."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute(
            "SELECT id, ID_Number, Name, Phone_Number, Department, status "
            "FROM employees WHERE ID_Number = ?",
            (id_number,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        if _owns:
            _c.close()


def import_employees_csv(
    file_or_path,
    created_by: str = "system",
    *,
    conn: sqlite3.Connection = None,
) -> dict:
    """Bulk-import HR CSV with idempotent upsert on ID_Number.

    Header schema (case-insensitive): ID_Number, Name, Phone_Number, Department.
    Re-importing the same CSV is a no-op except where any non-key field
    changed — those rows go through ON CONFLICT UPDATE.

    Returns {"inserted": N, "updated": N, "skipped": N, "errors": [str, ...]}.
    """
    try:
        df = pd.read_csv(file_or_path, dtype=str).fillna("")
    except Exception as e:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": [f"CSV parse failed: {e}"]}

    # Normalise header case so HR can send any case (`id_number`, `ID_NUMBER`, etc.)
    lower_map = {c.lower(): c for c in df.columns}
    required = ["id_number", "name", "phone_number", "department"]
    missing = [r for r in required if r not in lower_map]
    if missing:
        return {"inserted": 0, "updated": 0, "skipped": 0,
                "errors": [f"CSV missing required column(s): {', '.join(missing)}"]}

    # Phase 7A — optional Site_ID column. Absent column = legacy CSV; rows get NULL.
    site_col = lower_map.get("site_id")

    _c, _owns = _conn_ctx_6a(conn)
    result = {"inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    try:
        for idx, row in df.iterrows():
            id_number = str(row[lower_map["id_number"]]).strip()
            name = str(row[lower_map["name"]]).strip()
            phone = str(row[lower_map["phone_number"]]).strip()
            dept = str(row[lower_map["department"]]).strip()
            site = str(row[site_col]).strip() if site_col else ""
            site = site or None
            if not id_number or not name:
                result["skipped"] += 1
                result["errors"].append(f"Row {idx + 2}: missing ID_Number or Name.")
                continue
            # Check whether row exists AND whether any non-key field would change.
            cur = _c.execute(
                "SELECT Name, Phone_Number, Department, Site_ID FROM employees WHERE ID_Number = ?",
                (id_number,),
            )
            existing = cur.fetchone()
            if existing is None:
                _c.execute(
                    "INSERT INTO employees (ID_Number, Name, Phone_Number, Department, Site_ID, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (id_number, name, phone, dept, site, created_by),
                )
                result["inserted"] += 1
            else:
                ex_name, ex_phone, ex_dept, ex_site = existing
                # CSV without a Site_ID column never overwrites an existing binding.
                effective_site = site if site_col else ex_site
                if (ex_name, ex_phone or "", ex_dept or "", ex_site) == (
                    name, phone, dept, effective_site,
                ):
                    result["skipped"] += 1
                else:
                    _c.execute(
                        "UPDATE employees SET Name=?, Phone_Number=?, Department=?, "
                        "Site_ID=?, updated_at=CURRENT_TIMESTAMP WHERE ID_Number = ?",
                        (name, phone, dept, effective_site, id_number),
                    )
                    result["updated"] += 1
        _c.commit()
        log_audit_action(
            created_by, "EMPLOYEE_CSV_IMPORT", "employees",
            f"inserted={result['inserted']} updated={result['updated']} skipped={result['skipped']}",
        )
        return result
    finally:
        if _owns:
            _c.close()


# ── CV model versions ──────────────────────────────────────────────────────
def register_cv_model_version(
    version: str,
    model_path: str,
    classes: list,
    mAP: float = None,
    *,
    conn: sqlite3.Connection = None,
) -> int:
    """Register a new YOLO model artifact. Inserts with is_active=0.

    Returns the new row id. Promotion to active is a separate explicit step
    via promote_cv_model_version() so admins never accidentally activate an
    un-validated model just by uploading it.
    """
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute(
            "INSERT INTO cv_model_versions (version, model_path, classes_json, mAP, is_active) "
            "VALUES (?, ?, ?, ?, 0)",
            (version, model_path, _json_6a.dumps(list(classes or [])), mAP),
        )
        _c.commit()
        return int(cur.lastrowid)
    finally:
        if _owns:
            _c.close()


def promote_cv_model_version(
    version: str,
    *,
    promoted_by: str = "system",
    conn: sqlite3.Connection = None,
) -> bool:
    """Atomically promote `version` to active; demote whatever was active.

    The partial unique index on (is_active WHERE is_active=1) makes this safe
    against partial failure — if the second UPDATE fails the transaction
    rolls back and we stay in the previous active state.
    """
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute("SELECT id FROM cv_model_versions WHERE version = ?", (version,))
        row = cur.fetchone()
        if not row:
            return False
        try:
            _c.execute("BEGIN")
            _c.execute("UPDATE cv_model_versions SET is_active = 0 WHERE is_active = 1")
            _c.execute("UPDATE cv_model_versions SET is_active = 1 WHERE version = ?", (version,))
            _c.commit()
        except Exception:
            _c.rollback()
            raise
        log_audit_action(promoted_by, "CV_MODEL_PROMOTE", "cv_model_versions", version)
        return True
    finally:
        if _owns:
            _c.close()


def get_active_cv_model(*, conn: sqlite3.Connection = None) -> dict | None:
    """Return the active model row as a dict (with `classes` parsed from JSON), or None."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute(
            "SELECT id, version, model_path, classes_json, mAP, trained_at "
            "FROM cv_model_versions WHERE is_active = 1 LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        out = dict(zip(cols, row))
        try:
            out["classes"] = _json_6a.loads(out.pop("classes_json") or "[]")
        except Exception:
            out["classes"] = []
        return out
    finally:
        if _owns:
            _c.close()


# ── Tool catalogue ─────────────────────────────────────────────────────────
def add_tool_class(
    class_name: str,
    display_name: str,
    category: str = "",
    model_version_id: int = None,
    created_by: str = "system",
    min_confidence: float = 0.75,
    *,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Register a YOLO class in the tool catalogue. Duplicates rejected, not raised."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO tool_catalogue "
            "(class_name, display_name, category, model_version_id, min_confidence, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (class_name, display_name, category, model_version_id, float(min_confidence), created_by),
        )
        _c.commit()
        log_audit_action(created_by, "TOOL_CLASS_ADD", "tool_catalogue", class_name)
        return True, f"Tool class '{class_name}' added."
    except sqlite3.IntegrityError:
        return False, f"Tool class '{class_name}' already exists."
    finally:
        if _owns:
            _c.close()


def list_tool_catalogue(
    *,
    model_version_id: int = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return tool catalogue rows, optionally restricted to one model version."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        q = ("SELECT id, class_name, display_name, category, model_version_id, "
             "min_confidence, created_by, created_at FROM tool_catalogue")
        params = ()
        if model_version_id is not None:
            q += " WHERE model_version_id = ?"
            params = (int(model_version_id),)
        q += " ORDER BY display_name COLLATE NOCASE ASC"
        return pd.read_sql_query(q, _c, params=params)
    finally:
        if _owns:
            _c.close()


def set_tool_class_min_confidence(
    class_name: str,
    min_confidence: float,
    *,
    updated_by: str = "system",
    conn: sqlite3.Connection = None,
) -> bool:
    """Override the 0.75 default for one tool class. True if a row was updated."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        cur = _c.execute(
            "UPDATE tool_catalogue SET min_confidence = ? WHERE class_name = ?",
            (float(min_confidence), class_name),
        )
        _c.commit()
        if cur.rowcount:
            log_audit_action(
                updated_by, "TOOL_CLASS_CONF", "tool_catalogue",
                f"{class_name} → {min_confidence}",
            )
            return True
        return False
    finally:
        if _owns:
            _c.close()


# ===========================================================================
# Phase 6E — Returnable loan reminder sweep (hourly cadence)
# ===========================================================================
# Four offsets, encoded as signed HOURS in the existing
# `delivery_reminders_sent` table with ref_type='returnable_loan':
#
#   offset_days = -2 → T-2h (info,     before due — borrower only)
#   offset_days =  0 → T-0  (warning,  due now — borrower only)
#   offset_days =  2 → T+2h (warning,  2h overdue — borrower + site SK)
#   offset_days = 24 → T+24h (critical, 24h overdue — borrower + SK + supervisor)
#
# In-app notifications ALWAYS fire (queue_app_notification). WhatsApp is
# gated by config.WHATSAPP_TRIGGERS per event_key.
#
# Phone resolution for the borrower has a 3-tier fallback:
#   1. CV-loan path: returnable_items.cv_employee_id → employees.Phone_Number
#   2. Manual-loan path: returnable_items.borrower_phone column directly
#   3. Neither → log an "RETURNABLE_REMINDER_NO_PHONE" audit row + skip
#      WhatsApp for the borrower (in-app still fires to site SKs).

# Public so the worker + bug_check can introspect them.
RETURNABLE_REMINDER_OFFSETS: tuple[tuple[int, str, str, str], ...] = (
    # (offset_hours, event_key, severity, label)
    (-2, "returnable_reminder_t_minus_2h", "info",     "in 2 hours"),
    ( 0, "returnable_reminder_t_zero",     "warning",  "due now"),
    ( 2, "returnable_reminder_t_plus_2h",  "warning",  "2 hours overdue"),
    (24, "returnable_reminder_t_plus_24h", "critical", "24 hours overdue"),
)


def _resolve_borrower_phone_for_loan(row: dict, *, conn=None) -> str | None:
    """3-tier fallback. Returns the phone string or None."""
    cv_emp_id = row.get("cv_employee_id")
    if cv_emp_id:
        emp = get_employee_by_id_number(cv_emp_id, conn=conn)
        if emp and emp.get("Phone_Number"):
            return emp["Phone_Number"]
    phone = row.get("borrower_phone")
    if phone and str(phone).strip():
        return str(phone).strip()
    return None


def get_site_role_phones(
    role: str,
    site_id: str,
    *,
    conn: sqlite3.Connection = None,
) -> list[str]:
    """Return the non-empty Phone_Number values for users with this role
    at this site. Empty list if none. Used by the reminder sweep to fan
    WhatsApp to every SK / Supervisor at a site.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT Phone_Number FROM users "
            "WHERE role = ? AND Site_ID = ? "
            "  AND Phone_Number IS NOT NULL AND TRIM(Phone_Number) != ''",
            (role, site_id),
        ).fetchall()
        return [str(r[0]).strip() for r in rows if r and r[0]]
    finally:
        if _owns:
            conn.close()


def _try_dedup_returnable(conn, loan_id: int, target_iso: str, offset: int) -> bool:
    """Atomic INSERT into delivery_reminders_sent. Returns True if this is
    the first fire for this (loan_id, target, offset); False if already fired."""
    try:
        conn.execute(
            "INSERT INTO delivery_reminders_sent "
            "(ref_type, ref_number, target_date, offset_days) "
            "VALUES ('returnable_loan', ?, ?, ?)",
            (str(loan_id), target_iso, offset),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def _hour_window_matches(offset_hours: int, hours_to_due: float) -> bool:
    """True iff a sweep running 'now' should fire this offset for a loan
    whose due time is `hours_to_due` away.

    Sign convention: hours_to_due > 0 = still future, < 0 = overdue.
    Each window is exactly one hour wide so the hourly sweep fires
    each event at most once per loan.

      offset = -2  → fires when hours_to_due ∈ [ 1,   2)   (between 2h and 1h before due)
      offset =  0  → fires when hours_to_due ∈ [ 0,   1)   (within the hour leading up to due)
      offset =  2  → fires when hours_to_due ∈ [-3,  -2)   (between 2h and 3h overdue)
      offset = 24  → fires when hours_to_due ∈ [-25,-24)   (between 24h and 25h overdue)
    """
    if offset_hours < 0:
        # T-Nh — N hours before due. Window [N-1, N).
        n = -offset_hours
        return (n - 1) <= hours_to_due < n
    if offset_hours == 0:
        # T-0 — "due now": fires in the hour leading up to due time.
        return 0 <= hours_to_due < 1
    # T+Nh — N hours past due. Window [-(N+1), -N).
    n = offset_hours
    return -(n + 1) <= hours_to_due < -n


def sweep_returnable_reminders(
    now: datetime.datetime | None = None,
    *,
    conn: sqlite3.Connection = None,
) -> int:
    """Fire returnable-loan reminders at the four configured offsets.

    Called by the WhatsApp worker at most once per local hour (gated by
    `app_settings.returnable_reminders_last_run_hour`). Idempotent across
    runs via the `delivery_reminders_sent` UNIQUE constraint.

    Returns the count of newly-fired events.
    """
    now = now or datetime.datetime.now()
    _owns = conn is None
    if _owns:
        conn = get_connection()
    fired = 0
    try:
        rows = conn.execute(
            "SELECT id, material_name, borrower_name, borrower_phone, "
            "       cv_employee_id, expected_return_time, Site_ID "
            "FROM returnable_items "
            "WHERE status = 'borrowed' "
            "  AND expected_return_time IS NOT NULL "
            "  AND expected_return_time != ''"
        ).fetchall()
        cols = ["id", "material_name", "borrower_name", "borrower_phone",
                "cv_employee_id", "expected_return_time", "Site_ID"]

        for r in rows:
            row = dict(zip(cols, r))
            # Parse the due timestamp robustly — accept "YYYY-MM-DD HH:MM:SS"
            # or pure date with HH:MM:SS appended, the two forms the SK form
            # writes today.
            raw = str(row["expected_return_time"])
            try:
                due = datetime.datetime.fromisoformat(raw.replace(" ", "T"))
            except ValueError:
                continue
            hours_to_due = (due - now).total_seconds() / 3600.0

            for offset, event_key, severity, label in RETURNABLE_REMINDER_OFFSETS:
                if not _hour_window_matches(offset, hours_to_due):
                    continue
                if not _try_dedup_returnable(conn, row["id"], raw, offset):
                    continue
                _fire_one_returnable_reminder(
                    conn, row, offset, event_key, severity, label,
                )
                fired += 1
        return fired
    finally:
        if _owns:
            conn.close()


def _fire_one_returnable_reminder(
    conn, row: dict, offset: int, event_key: str, severity: str, label: str,
) -> None:
    """Send the in-app + WhatsApp set for one (loan, offset) tuple.

    Recipient policy:
      offset == -2 → borrower only
      offset ==  0 → borrower only
      offset ==  2 → borrower + every SK at the site
      offset == 24 → borrower + every SK + every Supervisor at the site
    """
    loan_id = row["id"]
    material = row.get("material_name") or "(unknown tool)"
    borrower_name = row.get("borrower_name") or row.get("cv_employee_id") or "borrower"
    site_id = row.get("Site_ID") or ""

    title = f"Returnable: {material} — {label}"
    body  = (
        f"Loan #{loan_id} · {material} · borrower: {borrower_name} · "
        f"due {row.get('expected_return_time', '?')}"
    )
    related_ref = str(loan_id)

    # ── In-app: borrower (broadcast to site SKs since borrowers may not
    #     be system users) — always queued ────────────────────────────────
    # We broadcast to store_keeper@site for the borrower's "their site"
    # bell badge. At T+2 / T+24, we ALSO add the supervisor at site to the
    # recipient list (supervisor instead of HOD per the Phase 6E spec).
    queue_app_notification(
        event_key=event_key,
        title=title,
        body=body,
        severity=severity,
        recipient_role="store_keeper",
        recipient_site=site_id,
        related_table="returnable_items",
        related_ref=related_ref,
        link_page="📝 Entry Log",
        conn=conn,
    )
    if offset == 24:
        queue_app_notification(
            event_key=event_key,
            title=title,
            body=body,
            severity=severity,
            recipient_role="supervisor",
            recipient_site=site_id,
            related_table="returnable_items",
            related_ref=related_ref,
            link_page="📝 Entry Log",
            conn=conn,
        )

    # ── WhatsApp: gated by per-event toggle ──────────────────────────────
    msg = (
        f"🛠️ *Returnable item reminder*\n"
        f"Item: {material}\n"
        f"Borrower: {borrower_name}\n"
        f"Status: {label}\n"
        f"Loan #{loan_id}"
    )

    # Borrower phone — 3-tier fallback.
    borrower_phone = _resolve_borrower_phone_for_loan(row, conn=conn)
    if borrower_phone:
        fire_whatsapp_event(event_key, borrower_phone, msg, conn=conn)
    else:
        # Phone unresolvable → log only, no admin nag (per Phase 6E spec).
        log_audit_action(
            "system",
            "RETURNABLE_REMINDER_NO_PHONE",
            "returnable_items",
            f"loan={loan_id} offset={offset}h — no phone via CV or manual path",
        )

    # SK fan-out at T+2h and T+24h
    if offset in (2, 24):
        for ph in get_site_role_phones("store_keeper", site_id, conn=conn):
            fire_whatsapp_event(event_key, ph, msg, conn=conn)

    # Supervisor fan-out at T+24h only (per user spec)
    if offset == 24:
        for ph in get_site_role_phones("supervisor", site_id, conn=conn):
            fire_whatsapp_event(event_key, ph, msg, conn=conn)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7B — Supervisor Material Request workflow
# ═══════════════════════════════════════════════════════════════════════════
# Intent vs Actual ledger pattern:
#   1. Supervisor submits → INSERT supervisor_material_requests (header) +
#      supervisor_material_request_items (lines, with stock snapshot).
#   2. SK reviews → may edit / delete / add notes on lines.
#   3. SK approves → mirror each (non-zero adjusted-qty) line into the
#      existing pending_issues table with status='pending_hod'. The HOD's
#      existing EOD commit flow handles the rest — zero changes to commit_eod
#      or the negative-stock validator. Source_Ref ties the consumption row
#      back to the originating request line for intent-vs-actual reports.
#   4. Header row is NEVER deleted — preserves the original supervisor intent
#      forever for variance analysis.
# ═══════════════════════════════════════════════════════════════════════════
import json as _json_7b


def generate_smr_request_no(conn: sqlite3.Connection = None) -> str:
    """Generate SMR-YYYYMMDD-NNNN. NNNN resets per local-day, global across sites."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        today = datetime.date.today().strftime("%Y%m%d")
        prefix = f"SMR-{today}-"
        row = conn.execute(
            "SELECT request_no FROM supervisor_material_requests "
            "WHERE request_no LIKE ? ORDER BY id DESC LIMIT 1",
            (f"{prefix}%",),
        ).fetchone()
        next_n = 1
        if row and row[0]:
            try:
                next_n = int(str(row[0]).split("-")[-1]) + 1
            except (ValueError, IndexError):
                next_n = 1
        return f"{prefix}{next_n:04d}"
    finally:
        if _owns:
            conn.close()


def _smr_snapshot_stock(site_id: str, sap_code: str,
                        conn: sqlite3.Connection) -> float:
    """Inline stock lookup at request-time. Returns 0.0 if item not in inventory
    or no rows. Uses the same identity math as load_live_inventory but scoped
    to a single SAP_Code for speed."""
    row = conn.execute("""
        SELECT
            COALESCE(i.Opening_Stock, 0)
          + COALESCE((SELECT SUM(Quantity) FROM receipts
                       WHERE SAP_Code = i.SAP_Code AND Site_ID = ?), 0)
          - COALESCE((SELECT SUM(Quantity) FROM consumption
                       WHERE SAP_Code = i.SAP_Code AND Site_ID = ?), 0)
          - COALESCE((SELECT SUM(Quantity) FROM returns
                       WHERE SAP_Code = i.SAP_Code AND Site_ID = ?), 0)
              AS stock
        FROM inventory i
        WHERE TRIM(i.SAP_Code) = TRIM(?)
        LIMIT 1
    """, (site_id, site_id, site_id, sap_code)).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def create_supervisor_request(
    *,
    site_id: str,
    worker_id: str,
    job_tank_place: str,
    old_ppe_returned: int,
    no_return_reason: str,
    items: list[dict],
    supervisor_username: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Insert SMR header + N item rows in a single transaction.

    items: list of {"SAP_Code": str, "Requested_Qty": float, "Notes": str (opt)}.
    Returns (True, request_no) or (False, error_message).
    """
    job_tank_place = (job_tank_place or "").strip()
    if not site_id:
        return False, "Site is required."
    if not worker_id:
        return False, "Worker is required."
    if not job_tank_place:
        return False, "Job / Tank / Place is required."
    if old_ppe_returned not in (0, 1):
        return False, "Old PPE Returned must be Yes or No."
    if old_ppe_returned == 0 and not (no_return_reason or "").strip():
        return False, "Please give a reason — Old PPE not returned."
    if not items:
        return False, "Add at least one item."

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Worker must exist AND be bound to this site AND be active.
        wrow = conn.execute(
            "SELECT Name, status, Site_ID FROM employees WHERE ID_Number = ?",
            (worker_id,),
        ).fetchone()
        if not wrow:
            return False, f"Worker '{worker_id}' not in employee master."
        worker_name, worker_status, worker_site = wrow
        if worker_status != "active":
            return False, f"Worker '{worker_id}' is {worker_status}, not active."
        if worker_site != site_id:
            return False, (
                f"Worker '{worker_id}' is bound to site {worker_site or '—'}, "
                f"not {site_id}. Ask Admin to transfer."
            )

        # Pre-fetch inventory rows for all SAP_Codes referenced (single query).
        sap_codes = [str(it.get("SAP_Code") or "").strip() for it in items]
        if any(not s for s in sap_codes):
            return False, "Every item needs an SAP_Code."
        placeholders = ",".join("?" * len(sap_codes))
        inv_rows = conn.execute(
            f"SELECT SAP_Code, Material_Code, Equipment_Description, UOM "
            f"FROM inventory WHERE SAP_Code IN ({placeholders})",
            sap_codes,
        ).fetchall()
        inv_lookup = {str(r[0]).strip(): r for r in inv_rows}
        missing = [s for s in sap_codes if s not in inv_lookup]
        if missing:
            return False, f"Unknown SAP_Code(s): {', '.join(missing)}"

        request_no = generate_smr_request_no(conn=conn)
        cur = conn.execute(
            "INSERT INTO supervisor_material_requests "
            "(request_no, Site_ID, Worker_ID, Worker_Name, Job_Tank_Place, "
            " Old_PPE_Returned, No_Return_Reason, requested_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request_no, site_id, worker_id, worker_name, job_tank_place,
             int(old_ppe_returned), (no_return_reason or "").strip() or None,
             supervisor_username),
        )
        req_id = cur.lastrowid

        for it in items:
            sap = str(it.get("SAP_Code") or "").strip()
            qty = float(it.get("Requested_Qty") or 0)
            if qty <= 0:
                conn.rollback()
                return False, f"Quantity for {sap} must be > 0."
            inv = inv_lookup[sap]
            stock = _smr_snapshot_stock(site_id, sap, conn)
            available = 1 if stock >= qty else 0
            conn.execute(
                "INSERT INTO supervisor_material_request_items "
                "(request_id, SAP_Code, Material_Code, Equipment_Description, "
                " UOM, Requested_Qty, Stock_At_Request, Available_Flag, Notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (req_id, sap, inv[1], inv[2], inv[3],
                 qty, stock, available, (it.get("Notes") or "").strip() or None),
            )

        conn.commit()
        log_audit_action(
            supervisor_username, "SMR_SUBMIT",
            "supervisor_material_requests",
            f"{request_no} site={site_id} worker={worker_id} items={len(items)}",
        )

        # Notify every SK at this site.
        queue_app_notification(
            event_key="smr_submitted",
            title=f"New material request — {request_no}",
            body=(f"Supervisor {supervisor_username} requested {len(items)} item(s) "
                  f"for worker {worker_name} ({worker_id}) at {job_tank_place}."),
            severity="info",
            recipient_role="store_keeper",
            recipient_site=site_id,
            related_table="supervisor_material_requests",
            related_ref=request_no,
            conn=conn,
        )
        for ph in get_site_role_phones("store_keeper", site_id, conn=conn):
            fire_whatsapp_event(
                "smr_submitted", ph,
                (f"📦 *NEW MATERIAL REQUEST*\n"
                 f"Ref: *{request_no}*\n"
                 f"Site: {site_id}\n"
                 f"Worker: {worker_name} ({worker_id})\n"
                 f"Job/Tank: {job_tank_place}\n"
                 f"Items: {len(items)}\n\n"
                 f"Open the Store Keeper Portal → 🛒 Supervisor Requests."),
                conn=conn,
            )
        return True, request_no
    finally:
        if _owns:
            conn.close()


def list_supervisor_requests(
    site_id: str = None,
    status: str = None,
    requested_by: str = None,
    days: int = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return SMR headers as a DataFrame, auto-localized timestamps."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        q = ("SELECT id, request_no, Site_ID, Worker_ID, Worker_Name, "
             "       Job_Tank_Place, Old_PPE_Returned, No_Return_Reason, "
             "       requested_by, requested_at, status, "
             "       sk_decided_by, sk_decided_at, sk_reject_reason, "
             "       posted_pending_ids "
             "FROM supervisor_material_requests")
        clauses, params = [], []
        if site_id:
            clauses.append("Site_ID = ?"); params.append(site_id)
        if status:
            clauses.append("status = ?"); params.append(status)
        if requested_by:
            clauses.append("requested_by = ?"); params.append(requested_by)
        if days:
            clauses.append("requested_at >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY requested_at DESC"
        df = pd.read_sql_query(q, conn, params=tuple(params))
        from config import auto_localize_timestamps as _atz
        return _atz(df)
    finally:
        if _owns:
            conn.close()


def get_supervisor_request(
    request_id: int,
    conn: sqlite3.Connection = None,
) -> tuple[dict | None, pd.DataFrame]:
    """Return (header_dict, items_df). header_dict is None if missing."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM supervisor_material_requests WHERE id = ?",
            (request_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, pd.DataFrame()
        header = dict(zip([d[0] for d in cur.description], row))
        items = pd.read_sql_query(
            "SELECT * FROM supervisor_material_request_items "
            "WHERE request_id = ? ORDER BY id ASC",
            conn, params=(request_id,),
        )
        return header, items
    finally:
        if _owns:
            conn.close()


def update_supervisor_request_item(
    item_id: int,
    *,
    requested_qty: float = None,
    sk_adjusted_qty: float = None,
    notes: str = None,
    conn: sqlite3.Connection = None,
) -> bool:
    """SK edits a single item BEFORE approval. Refuses if parent request
    is no longer pending_sk."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT r.status FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE i.id = ?",
            (item_id,),
        ).fetchone()
        if not row or row[0] != "pending_sk":
            return False
        sets, params = [], []
        if requested_qty is not None:
            sets.append("Requested_Qty = ?"); params.append(float(requested_qty))
        if sk_adjusted_qty is not None:
            sets.append("SK_Adjusted_Qty = ?"); params.append(float(sk_adjusted_qty))
        if notes is not None:
            sets.append("Notes = ?"); params.append((notes or "").strip() or None)
        if not sets:
            return False
        params.append(item_id)
        conn.execute(
            f"UPDATE supervisor_material_request_items SET {', '.join(sets)} "
            f"WHERE id = ?", params,
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def delete_supervisor_request_item(
    item_id: int,
    conn: sqlite3.Connection = None,
) -> bool:
    """SK drops a line pre-approval. Refuses if request no longer pending_sk."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT r.status FROM supervisor_material_request_items i "
            "JOIN supervisor_material_requests r ON r.id = i.request_id "
            "WHERE i.id = ?",
            (item_id,),
        ).fetchone()
        if not row or row[0] != "pending_sk":
            return False
        conn.execute("DELETE FROM supervisor_material_request_items WHERE id = ?",
                     (item_id,))
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def cancel_supervisor_request(
    request_id: int,
    by_username: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """Supervisor cancels their own pending request. Refuses if not pending_sk."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE supervisor_material_requests "
            "SET status = 'cancelled', sk_decided_by = ?, "
            "    sk_decided_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending_sk'",
            (by_username, request_id),
        )
        conn.commit()
        if cur.rowcount:
            log_audit_action(by_username, "SMR_CANCEL",
                             "supervisor_material_requests", str(request_id))
            return True
        return False
    finally:
        if _owns:
            conn.close()


def approve_supervisor_request(
    request_id: int,
    sk_username: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Mirror approved lines into pending_issues (status='draft') so they
    land in the SK's Consumption staging grid for batch-number / final-qty
    edits. The SK's 'Submit Batch to HOD' flips them to pending_hod where
    commit_eod + the negative-stock validator take over (Round 12 change —
    previously this helper wrote pending_hod directly).

    Each row carries Requested_By=<supervisor> and Issued_By=<sk> so the
    auto-attribution pipeline propagates cleanly through commit_eod.

    SK_Adjusted_Qty=0 → line skipped (auto-delete semantics, per spec).
    Returns (True, msg) or (False, err)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        header, items = get_supervisor_request(request_id, conn=conn)
        if not header:
            return False, "Request not found."
        if header["status"] != "pending_sk":
            return False, f"Request already {header['status']} — cannot approve."
        if items.empty:
            return False, "No items to approve."

        c = conn.cursor()
        c.execute("PRAGMA table_info(pending_issues)")
        existing_cols = {r[1] for r in c.fetchall()}

        site_id     = header["Site_ID"]
        worker_name = header["Worker_Name"]
        job_tank    = header["Job_Tank_Place"]
        ppe_flag    = "Y" if int(header["Old_PPE_Returned"]) else "N"
        ppe_reason  = (header.get("No_Return_Reason") or "").strip()
        today_iso   = datetime.date.today().isoformat()
        request_no  = header["request_no"]

        posted_ids = []
        for _, it in items.iterrows():
            # Auto-exclude SK_Adjusted_Qty == 0 (treated as delete).
            adj = it.get("SK_Adjusted_Qty")
            if adj is not None and not pd.isna(adj) and float(adj) == 0.0:
                continue
            qty = float(adj if (adj is not None and not pd.isna(adj))
                        else it["Requested_Qty"])
            if qty <= 0:
                continue

            remarks = (
                f"SMR {request_no} · {job_tank} · PPE returned: {ppe_flag}"
                + (f" · Reason: {ppe_reason}" if ppe_flag == "N" and ppe_reason else "")
            )
            payload = {
                "Date":         today_iso,
                "SAP_Code":     it["SAP_Code"],
                "Quantity":     qty,
                "Work_Type":    "SUPERVISOR_REQUEST",
                "Remarks":      remarks,
                "Issued_By":    sk_username,
                "Issued_To":    worker_name,
                "Tank_No":      job_tank,
                "Site_ID":      site_id,
                # Round 12: land in SK draft grid, not HOD's EOD queue.
                "status":       "draft",
                "Source_Ref":   f"SMR:{request_no}:{int(it['id'])}",
                "Requested_By": header.get("requested_by"),
            }
            payload = {k: v for k, v in payload.items() if k in existing_cols}
            cols = list(payload.keys())
            phs  = ", ".join(["?"] * len(cols))
            cur = c.execute(
                f"INSERT INTO pending_issues ({', '.join(cols)}) VALUES ({phs})",
                [payload[k] for k in cols],
            )
            posted_ids.append(int(cur.lastrowid))

        if not posted_ids:
            conn.rollback()
            return False, "Nothing to post — every line was zeroed-out or invalid."

        conn.execute(
            "UPDATE supervisor_material_requests "
            "SET status = 'approved', sk_decided_by = ?, "
            "    sk_decided_at = CURRENT_TIMESTAMP, posted_pending_ids = ? "
            "WHERE id = ?",
            (sk_username, _json_7b.dumps(posted_ids), request_id),
        )
        conn.commit()

        log_audit_action(
            sk_username, "SMR_APPROVE",
            "supervisor_material_requests",
            f"{request_no} → {len(posted_ids)} pending_issues rows",
        )

        # Notify HOD at site + the originating supervisor.
        queue_app_notification(
            event_key="smr_approved",
            title=f"Material request approved — {request_no}",
            body=(f"SK {sk_username} accepted {len(posted_ids)} line(s) into "
                  f"the Consumption staging grid for final entry."),
            severity="success",
            recipient_role="hod",
            recipient_site=site_id,
            related_table="supervisor_material_requests",
            related_ref=request_no,
            conn=conn,
        )
        queue_app_notification(
            event_key="smr_approved",
            title=f"Your request was approved — {request_no}",
            body=f"SK {sk_username} approved {len(posted_ids)} item(s) for {worker_name}.",
            severity="success",
            recipient_user=header["requested_by"],
            related_table="supervisor_material_requests",
            related_ref=request_no,
            conn=conn,
        )
        for ph in get_site_role_phones("hod", site_id, conn=conn):
            fire_whatsapp_event(
                "smr_approved", ph,
                (f"✅ *MATERIAL REQUEST APPROVED*\n"
                 f"Ref: *{request_no}*\n"
                 f"Site: {site_id} · Worker: {worker_name}\n"
                 f"Lines now in SK staging grid: {len(posted_ids)}\n"
                 f"HOD will see them once SK submits the batch."),
                conn=conn,
            )
        sup_phone = get_phone_by_username(header["requested_by"])
        if sup_phone:
            fire_whatsapp_event(
                "smr_approved", sup_phone,
                (f"✅ *YOUR REQUEST {request_no} APPROVED*\n"
                 f"{len(posted_ids)} line(s) approved by SK {sk_username}."),
                conn=conn,
            )

        return True, (
            f"Approved — {len(posted_ids)} line(s) injected into the "
            f"Consumption staging grid. Open the 📋 Consumption Log tab "
            f"to add batch numbers and submit to HOD."
        )
    finally:
        if _owns:
            conn.close()


def reject_supervisor_request(
    request_id: int,
    sk_username: str,
    reason: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Flip the SMR header to 'rejected'. Mandatory reason. Idempotent."""
    reason = (reason or "").strip()
    if not reason:
        return False, "Rejection reason is required."
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        header, _ = get_supervisor_request(request_id, conn=conn)
        if not header:
            return False, "Request not found."
        if header["status"] != "pending_sk":
            return False, f"Request already {header['status']}."
        conn.execute(
            "UPDATE supervisor_material_requests "
            "SET status = 'rejected', sk_decided_by = ?, "
            "    sk_decided_at = CURRENT_TIMESTAMP, sk_reject_reason = ? "
            "WHERE id = ?",
            (sk_username, reason, request_id),
        )
        conn.commit()
        log_audit_action(
            sk_username, "SMR_REJECT",
            "supervisor_material_requests",
            f"{header['request_no']} reason={reason[:80]}",
        )
        # Notify supervisor.
        queue_app_notification(
            event_key="smr_rejected",
            title=f"Material request rejected — {header['request_no']}",
            body=f"SK {sk_username}: {reason}",
            severity="warning",
            recipient_user=header["requested_by"],
            related_table="supervisor_material_requests",
            related_ref=header["request_no"],
            conn=conn,
        )
        sup_phone = get_phone_by_username(header["requested_by"])
        if sup_phone:
            fire_whatsapp_event(
                "smr_rejected", sup_phone,
                (f"❌ *REQUEST REJECTED — {header['request_no']}*\n"
                 f"By: SK {sk_username}\nReason: {reason}"),
                conn=conn,
            )
        return True, "Request rejected."
    finally:
        if _owns:
            conn.close()


# ─── Round 15 helpers — Material master + per-site Min Qty ──────────────────

def next_sap_code(conn: sqlite3.Connection = None) -> str:
    """Return the next GI-NNNNNNN SAP code in sequence by reading the max
    numeric tail across inventory.SAP_Code. Format: 'GI-' + 7-digit tail."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        max_num = 0
        for (sap,) in conn.execute(
            "SELECT SAP_Code FROM inventory "
            "WHERE SAP_Code LIKE 'GI-%'"
        ).fetchall():
            try:
                tail = int(str(sap).split("-", 1)[1])
                if tail > max_num:
                    max_num = tail
            except (ValueError, IndexError):
                continue
        return f"GI-{max_num + 1:07d}"
    finally:
        if _owns:
            conn.close()


def next_temp_material_code(conn: sqlite3.Connection = None) -> str:
    """Return the next Temp-GI-NNNNNNN code in sequence and atomically bump
    the counter persisted in app_settings.temp_material_seq.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT value FROM app_settings WHERE key='temp_material_seq'"
        ).fetchone()
        try:
            current = int(cur[0]) if cur and cur[0] is not None else 0
        except (TypeError, ValueError):
            current = 0
        nxt = current + 1
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES "
            "('temp_material_seq', ?)", (str(nxt),),
        )
        conn.commit()
        return f"Temp-GI-{nxt:07d}"
    finally:
        if _owns:
            conn.close()


def bulk_upsert_materials(
    rows: list[dict],
    *,
    created_by: str,
    overwrite_duplicates: bool = False,
    conn: sqlite3.Connection = None,
) -> dict:
    """Upsert one or more material rows into the inventory master.

    Each row may contain: Material_Code, Material_Description (or
    Equipment_Description), UOM, Category, Minimum_Qty. Blank Material_Code
    triggers a Temp-GI auto-code. SAP_Code is always assigned by the helper.

    Duplicates (matching existing Material_Code) are either rejected (when
    overwrite_duplicates=False) or updated in place. Returns a dict with
    'inserted', 'updated', 'rejected' lists for the UI to display.

    Wrapped in a single transaction so partial failures don't leave the
    inventory master half-populated.
    """
    result = {"inserted": [], "updated": [], "rejected": []}
    if not rows:
        return result

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Pre-fetch existing Material_Codes once so the dedup check is O(N+M).
        existing_codes: set[str] = {
            r[0] for r in conn.execute(
                "SELECT Material_Code FROM inventory "
                "WHERE Material_Code IS NOT NULL AND Material_Code <> ''"
            ).fetchall()
        }
        # Track codes seen WITHIN this batch so a single upload can't carry
        # the same Material_Code twice and slip through.
        batch_codes: set[str] = set()

        from config import MATERIAL_CATEGORIES as _CATS
        default_cat = "Others" if "Others" in _CATS else (_CATS[0] if _CATS else "Others")

        for raw in rows:
            r = {k: v for k, v in (raw or {}).items()}
            mat_code = str(r.get("Material_Code") or "").strip()
            desc     = str(
                r.get("Material_Description")
                or r.get("Equipment_Description")
                or ""
            ).strip()
            uom      = str(r.get("UOM") or r.get("UoM") or "").strip()
            cat      = str(r.get("Category") or default_cat).strip() or default_cat
            try:
                min_q = float(r.get("Minimum_Qty") or 0)
            except (TypeError, ValueError):
                min_q = 0.0

            if not desc:
                result["rejected"].append({
                    **r, "_reason": "Material_Description is required.",
                })
                continue
            if not uom:
                result["rejected"].append({
                    **r, "_reason": "UOM is required.",
                })
                continue

            # Auto-temp code when blank.
            if not mat_code:
                mat_code = next_temp_material_code(conn=conn)

            # Batch-level dedup (catch duplicates within the upload itself).
            if mat_code in batch_codes:
                result["rejected"].append({
                    **r, "Material_Code": mat_code,
                    "_reason": "Duplicate Material_Code within this upload.",
                })
                continue
            batch_codes.add(mat_code)

            if mat_code in existing_codes:
                if not overwrite_duplicates:
                    result["rejected"].append({
                        **r, "Material_Code": mat_code,
                        "_reason": "Material_Code already exists.",
                    })
                    continue
                # Overwrite path — update by Material_Code.
                conn.execute(
                    "UPDATE inventory SET "
                    "  Equipment_Description = ?, "
                    "  UOM = ?, "
                    "  Category = ?, "
                    "  Minimum_Qty = ? "
                    "WHERE Material_Code = ?",
                    (desc, uom, cat, min_q, mat_code),
                )
                result["updated"].append({
                    "Material_Code": mat_code,
                    "Equipment_Description": desc,
                    "UOM": uom, "Category": cat, "Minimum_Qty": min_q,
                })
                continue

            # Insert path — assign SAP code, write the row.
            sap = next_sap_code(conn=conn)
            conn.execute(
                "INSERT INTO inventory "
                "(SAP_Code, Material_Code, Equipment_Description, UOM, "
                " Category, Minimum_Qty) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sap, mat_code, desc, uom, cat, min_q),
            )
            existing_codes.add(mat_code)
            result["inserted"].append({
                "SAP_Code": sap, "Material_Code": mat_code,
                "Equipment_Description": desc, "UOM": uom,
                "Category": cat, "Minimum_Qty": min_q,
            })

        conn.commit()
        try:
            log_audit_action(
                created_by, "MATERIAL_BULK_UPSERT", "inventory",
                f"inserted={len(result['inserted'])} "
                f"updated={len(result['updated'])} "
                f"rejected={len(result['rejected'])}",
            )
        except Exception:
            pass
        return result
    finally:
        if _owns:
            conn.close()


def set_site_min_qty(
    sap_code: str,
    site_id: str,
    min_qty: float,
    *,
    updated_by: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """Upsert a per-site Minimum_Qty override. Pass a negative or NaN qty
    to delete the override (falls back to inventory.Minimum_Qty)."""
    if not sap_code or not site_id:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        try:
            mq = float(min_qty)
        except (TypeError, ValueError):
            mq = -1.0
        if mq < 0:
            conn.execute(
                "DELETE FROM inventory_site_overrides "
                "WHERE SAP_Code = ? AND Site_ID = ?",
                (sap_code, site_id),
            )
        else:
            conn.execute(
                "INSERT INTO inventory_site_overrides "
                "(SAP_Code, Site_ID, Minimum_Qty, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(SAP_Code, Site_ID) DO UPDATE SET "
                "  Minimum_Qty = excluded.Minimum_Qty, "
                "  updated_by  = excluded.updated_by, "
                "  updated_at  = CURRENT_TIMESTAMP",
                (sap_code, site_id, mq, updated_by),
            )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def get_min_qty_for(
    sap_code: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> float:
    """Resolve effective Minimum_Qty for a SAP_Code at a Site_ID via
    COALESCE(site_override, inventory_default, 0)."""
    if not sap_code:
        return 0.0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE("
            "  (SELECT Minimum_Qty FROM inventory_site_overrides "
            "    WHERE SAP_Code = ? AND Site_ID = ?), "
            "  (SELECT Minimum_Qty FROM inventory WHERE SAP_Code = ?), "
            "  0)",
            (sap_code, site_id or "", sap_code),
        ).fetchone()
        try:
            return float(row[0] or 0)
        except (TypeError, ValueError):
            return 0.0
    finally:
        if _owns:
            conn.close()


# ─── Round 16 helper — PO numbers per PR line ───────────────────────────────

def get_pr_with_po_numbers(
    pr_number: str,
    conn: sqlite3.Connection = None,
) -> dict:
    """Return {pr_line_id: 'PO-001, PO-002'} mapping for a PR.

    A single PR can spawn multiple POs (split orders across vendors,
    partial-fulfilment re-orders, etc.). The PR PDF generator uses this map
    to display the PO numbers alongside each PR line.

    Joins via po_items.PR_Number → pr_master so a PO line traces back to
    the originating PR. Distinct PO_Number per line, comma-joined in
    ascending order.

    Returns an empty dict when the PR has no POs yet (the PDF shows blank
    in the PO # column in that case).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # po_items doesn't carry SAP_Code (per the schema comment in
        # init_db) — Logistics works with Material_Code, SAP joins in at
        # the site-receipt step. So we link PR-lines to POs via Material_Code.
        pr_rows = conn.execute(
            "SELECT id, Material_Code FROM pr_master WHERE PR_Number = ?",
            (pr_number,),
        ).fetchall()
        if not pr_rows:
            return {}

        # All POs issued against this PR, with the Material_Code on each PO line.
        po_rows = conn.execute(
            "SELECT DISTINCT po.PO_Number, pi.Material_Code "
            "FROM purchase_orders po "
            "JOIN po_items pi ON pi.PO_Number = po.PO_Number "
            "WHERE po.PR_Number = ? "
            "ORDER BY po.PO_Number",
            (pr_number,),
        ).fetchall()

        # Group PO_Numbers per Material_Code so a PR line that asked for
        # Material X maps to the POs that bought Material X.
        po_by_mc: dict[str, list[str]] = {}
        for po_no, mc in po_rows:
            if not mc:
                continue
            po_by_mc.setdefault(str(mc).strip(), []).append(str(po_no))

        # Map pr_line_id → comma-joined PO list (deduped, sorted).
        out: dict[int, str] = {}
        for line_id, mc in pr_rows:
            key = str(mc or "").strip()
            pos = sorted(set(po_by_mc.get(key, [])))
            if pos:
                out[int(line_id)] = ", ".join(pos)
        return out
    except sqlite3.OperationalError:
        # po_items / purchase_orders might be missing on a very old DB.
        # Return empty map — PDF renders blank in the PO column gracefully.
        return {}
    finally:
        if _owns:
            conn.close()


# ─── Round 12 helpers ────────────────────────────────────────────────────────

def withdraw_smr_line_at_staging(
    pending_issue_id: int,
    sk_username: str,
    conn: sqlite3.Connection = None,
) -> tuple[bool, str]:
    """Mark a supervisor_material_request_items row as 'withdrawn_at_staging'
    when the SK deletes/skips the corresponding pending_issues row from the
    Consumption staging grid. Resolved via the pending row's Source_Ref.

    Idempotent — second call on the same line is a no-op. SK-direct rows
    (no SMR Source_Ref) return (True, '') without touching anything.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT Source_Ref FROM pending_issues WHERE id = ?",
            (int(pending_issue_id),),
        ).fetchone()
        if not row or not row[0]:
            return True, ""  # Not SMR-sourced — no SMR record to update.
        src = str(row[0])
        if not src.startswith("SMR:"):
            return True, ""
        try:
            line_id = int(src.split(":")[-1])
        except (ValueError, IndexError):
            return False, f"Malformed Source_Ref: {src!r}"
        conn.execute(
            "UPDATE supervisor_material_request_items "
            "SET line_status = 'withdrawn_at_staging' "
            "WHERE id = ? AND line_status = 'active'",
            (line_id,),
        )
        conn.commit()
        log_audit_action(
            sk_username, "SMR_LINE_WITHDRAWN",
            "supervisor_material_request_items",
            f"line_id={line_id} src={src}",
        )
        return True, "Line withdrawn at staging."
    finally:
        if _owns:
            conn.close()


def list_smr_history(
    site_id: str | None = None,
    *,
    status_in: list[str] | tuple[str, ...] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    supervisor: str | None = None,
    tank: str | None = None,
    days: int | None = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """SMR history table for the SK 'Supervisor Requests' tab.

    Defaults: decided-only (approved + rejected + cancelled) over last
    `days` days when status_in/dates not specified. Filters compose AND-wise.
    Timestamps auto-localized to GMT+3 via _localize.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        clauses: list[str] = []
        params: list = []
        if site_id:
            clauses.append("r.Site_ID = ?"); params.append(site_id)
        if status_in:
            phs = ", ".join(["?"] * len(status_in))
            clauses.append(f"r.status IN ({phs})")
            params.extend(list(status_in))
        if date_from:
            clauses.append("DATE(r.requested_at) >= DATE(?)")
            params.append(date_from)
        if date_to:
            clauses.append("DATE(r.requested_at) <= DATE(?)")
            params.append(date_to)
        if supervisor:
            clauses.append("r.requested_by = ?"); params.append(supervisor)
        if tank:
            clauses.append("r.Job_Tank_Place = ?"); params.append(tank)
        if days and not (date_from or date_to):
            clauses.append("r.requested_at >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        q = f"""
            SELECT
              r.id                  AS id,
              r.request_no          AS request_no,
              r.Site_ID             AS Site_ID,
              r.requested_by        AS requested_by,
              r.Worker_Name         AS Worker_Name,
              r.Worker_ID           AS Worker_ID,
              r.Job_Tank_Place      AS Job_Tank_Place,
              r.requested_at        AS requested_at,
              r.status              AS status,
              r.sk_decided_by       AS sk_decided_by,
              r.sk_decided_at       AS sk_decided_at,
              r.sk_reject_reason    AS sk_reject_reason,
              (SELECT COUNT(*) FROM supervisor_material_request_items i
                 WHERE i.request_id = r.id) AS line_count
            FROM supervisor_material_requests r
            {where}
            ORDER BY r.requested_at DESC
            LIMIT 500
        """
        df = pd.read_sql(q, conn, params=tuple(params))
        return _localize(df)
    finally:
        if _owns:
            conn.close()


def report_supervisor_intent_vs_actual(
    site_id: str | None = None,
    days: int = 30,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Intent vs Actual report — joins approved SMR lines back to the
    consumption ledger via Source_Ref. Variance column is computed as
    (Actual - Requested) / Requested.

    Each row = one approved SMR line, with the eventually-consumed qty.
    Lines staged but not yet committed (still in pending_issues) show
    Actual_Qty = NULL → 'Not yet committed' downstream.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        clauses = ["r.status = 'approved'"]
        params = []
        if site_id:
            clauses.append("r.Site_ID = ?"); params.append(site_id)
        if days:
            clauses.append("r.requested_at >= datetime('now', ?)")
            params.append(f"-{int(days)} days")
        where = " AND ".join(clauses)

        q = f"""
            SELECT
              r.request_no                                AS Request_No,
              r.Site_ID                                   AS Site_ID,
              r.requested_by                              AS Supervisor,
              r.Worker_Name                               AS Worker,
              r.Job_Tank_Place                            AS Job_Tank,
              r.requested_at                              AS Requested_At,
              r.sk_decided_at                             AS Approved_At,
              i.SAP_Code                                  AS SAP_Code,
              i.Material_Code                             AS Material_Code,
              i.Equipment_Description                     AS Description,
              i.UOM                                       AS UOM,
              i.Requested_Qty                             AS Requested_Qty,
              COALESCE(i.SK_Adjusted_Qty, i.Requested_Qty) AS Approved_Qty,
              i.Stock_At_Request                          AS Stock_At_Request,
              ('SMR:' || r.request_no || ':' || i.id)    AS Source_Ref,
              (
                SELECT SUM(c.Quantity) FROM consumption c
                WHERE c.Source_Ref = ('SMR:' || r.request_no || ':' || i.id)
              )                                           AS Actual_Qty
            FROM supervisor_material_requests r
            JOIN supervisor_material_request_items i ON i.request_id = r.id
            WHERE {where}
            ORDER BY r.requested_at DESC, i.id ASC
        """
        df = pd.read_sql_query(q, conn, params=tuple(params))
        if not df.empty:
            df["Variance_Pct"] = df.apply(
                lambda r: (
                    None if pd.isna(r["Actual_Qty"]) or not r["Requested_Qty"]
                    else round(100.0 * (float(r["Actual_Qty"]) - float(r["Requested_Qty"]))
                               / float(r["Requested_Qty"]), 1)
                ),
                axis=1,
            )
        from config import auto_localize_timestamps as _atz
        return _atz(df)
    finally:
        if _owns:
            conn.close()


def get_open_returnables_for_employee(
    employee_id: str,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Phase 7B side-panel — SK reviewing an SMR sees the worker's open tool
    loans (returnable items not yet returned). Spans BOTH the CV-loan path
    (cv_employee_id) and the manual-loan path (borrower_name match by name).
    Empty df if none."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Resolve worker name once so we can match the manual-loan path too.
        nrow = conn.execute(
            "SELECT Name FROM employees WHERE ID_Number = ?", (employee_id,),
        ).fetchone()
        worker_name = nrow[0] if nrow else None
        df = pd.read_sql_query(
            """
            SELECT id, material_name, qty, uom, borrower_name,
                   given_time, expected_return_time
            FROM returnable_items
            WHERE COALESCE(status, 'borrowed') = 'borrowed'
              AND (cv_employee_id = ?
                   OR (? IS NOT NULL AND borrower_name = ?))
            ORDER BY given_time DESC
            """,
            conn, params=(employee_id, worker_name, worker_name),
        )
        from config import auto_localize_timestamps as _atz
        return _atz(df)
    finally:
        if _owns:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7C — HOD Cross-Site View notifications + indicator
# ═══════════════════════════════════════════════════════════════════════════
# Debounce contract:
#   - Notification fires once per (viewer_username, target_site_id, calendar
#     local day). Second+ views same day are silently deduped by the UNIQUE
#     constraint on cross_site_views.
#   - Admin shadowing the HOD Portal NEVER fires a notification (per spec
#     Q2(b)). HODs already know admin has global oversight.
#   - Self-view (viewer_site == target_site) NEVER fires — also impossible
#     via the UI flow which excludes the viewer's own site from the picker.
# ═══════════════════════════════════════════════════════════════════════════
def record_cross_site_view(
    viewer_username: str,
    viewer_site_id: str | None,
    target_site_id: str,
    *,
    conn: sqlite3.Connection = None,
) -> bool:
    """Idempotent per (viewer_username, target_site_id, view_date).

    Returns True if this insert created a NEW row — caller should fire the
    notification. Returns False on duplicate (already viewed today) or on
    invalid inputs (blank / self-view).
    """
    viewer_username = (viewer_username or "").strip()
    target_site_id  = (target_site_id or "").strip()
    if not viewer_username or not target_site_id:
        return False
    viewer_site_id = (viewer_site_id or "").strip() or None
    # Defensive self-view skip — UI already filters it out.
    if viewer_site_id and viewer_site_id == target_site_id:
        return False

    today = datetime.date.today().isoformat()
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO cross_site_views "
            "(viewer_username, viewer_site_id, target_site_id, view_date) "
            "VALUES (?, ?, ?, ?)",
            (viewer_username, viewer_site_id, target_site_id, today),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        if _owns:
            conn.close()


def notify_cross_site_view(
    viewer_user: dict,
    target_site_id: str,
    viewed_item: str | None = None,
    *,
    conn: sqlite3.Connection = None,
) -> bool:
    """Records the view; if first-of-day, fires app notification + audit +
    optional WhatsApp to the target site's HOD.

    Returns True if a notification was actually fired (drives the corner
    indicator's 'has been notified' phrasing). Returns False on:
      - admin viewers (admin oversight is implicit, never notify)
      - dedupe (already viewed today)
      - self-view / blank inputs

    `viewed_item` is optional — when present, it's woven into the body
    ("looking at <item>") to give the target HOD context about what may be
    coming in a transfer request. The dedupe is per-site-per-day so the
    item shown is whatever was selected at first fire time.
    """
    if not isinstance(viewer_user, dict):
        return False
    role = (viewer_user.get("role") or "").strip().lower()
    if role == "admin":
        return False  # admin shadowing → silent (spec Q2(b))

    viewer_username = (viewer_user.get("username") or "").strip()
    viewer_site_id  = (viewer_user.get("site_id") or "").strip() or None
    target_site_id  = (target_site_id or "").strip()
    if not viewer_username or not target_site_id:
        return False

    is_first = record_cross_site_view(
        viewer_username, viewer_site_id, target_site_id, conn=conn,
    )
    if not is_first:
        return False

    today = datetime.date.today().isoformat()
    viewer_site_label = viewer_site_id or "—"
    item_clause = ""
    if viewed_item:
        item_clause = f" (looking at {str(viewed_item).strip()})"

    title = f"HOD of {viewer_site_label} is viewing your stock"
    body = (
        f"{viewer_username} from {viewer_site_label}{item_clause} "
        f"is checking your stock — they may submit a transfer request shortly."
    )

    queue_app_notification(
        event_key="cross_site_viewed",
        title=title,
        body=body,
        severity="info",
        recipient_role="hod",
        recipient_site=target_site_id,
        related_table="cross_site_views",
        related_ref=f"{viewer_username}|{viewer_site_label}|{target_site_id}|{today}",
        conn=conn,
    )
    log_audit_action(
        viewer_username, "CROSS_SITE_VIEW", "cross_site_views",
        f"viewer={viewer_username} target={target_site_id} date={today}",
    )
    for ph in get_site_role_phones("hod", target_site_id, conn=conn):
        fire_whatsapp_event(
            "cross_site_viewed", ph,
            (f"👁️ *CROSS-SITE VIEW*\n"
             f"{viewer_username} from *{viewer_site_label}* "
             f"is checking your stock{item_clause}.\n"
             f"They may submit a transfer request shortly."),
            conn=conn,
        )
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Phase 7E — Form draft recovery (server-side layer)
# ═══════════════════════════════════════════════════════════════════════════
# Per-user, per-form snapshot of in-flight values. The streamlit-local-storage
# layer is the primary safety net; this server-side table covers the cases
# localStorage cannot (device swap, browser-data wipe, explicit Save Draft).
#
# Auto-save is throttled to 1/min server-side per Phase 7E spec Q4(a) — the
# UI layer enforces the throttle since it owns the cadence; helpers here are
# raw UPSERT primitives.
# ═══════════════════════════════════════════════════════════════════════════
import json as _json_7e

DRAFT_DEFAULT_TTL_DAYS = 7  # spec Q2 — covers Fri/Sat weekend cycle


def upsert_form_draft(
    username: str,
    form_id: str,
    payload: dict,
    *,
    site_id: str = None,
    ttl_days: int = DRAFT_DEFAULT_TTL_DAYS,
    conn: sqlite3.Connection = None,
) -> bool:
    """UPSERT a draft for (username, form_id). Returns True on success.

    Raises ValueError if payload is not JSON-serialisable. Non-dict payloads
    are accepted but discouraged — keep them dict-shaped so the recovery
    banner can show meaningful fields.

    expires_at defaults to now + DRAFT_DEFAULT_TTL_DAYS. Pass ttl_days=None
    for a never-expiring draft.
    """
    username = (username or "").strip()
    form_id  = (form_id or "").strip()
    if not username or not form_id:
        return False
    try:
        payload_json = _json_7e.dumps(payload, default=str)
    except (TypeError, ValueError) as e:
        raise ValueError(f"Draft payload is not JSON-serialisable: {e}") from e

    if ttl_days is None:
        expires_at = None
    else:
        expires_at = (
            datetime.datetime.utcnow() + datetime.timedelta(days=int(ttl_days))
        ).isoformat(timespec="seconds")

    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO form_drafts "
            "(username, form_id, site_id, payload_json, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(username, form_id) DO UPDATE SET "
            "  payload_json = excluded.payload_json, "
            "  site_id      = excluded.site_id, "
            "  expires_at   = excluded.expires_at, "
            "  updated_at   = CURRENT_TIMESTAMP",
            (username, form_id, site_id, payload_json, expires_at),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def get_form_draft(
    username: str,
    form_id: str,
    *,
    conn: sqlite3.Connection = None,
) -> dict | None:
    """Return {'payload': dict, 'updated_at': str, 'expires_at': str | None}
    or None if missing / expired. Expired rows are NOT auto-deleted here —
    the daily prune does that. We simply hide them from the caller."""
    username = (username or "").strip()
    form_id  = (form_id or "").strip()
    if not username or not form_id:
        return None
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json, updated_at, expires_at "
            "FROM form_drafts WHERE username = ? AND form_id = ?",
            (username, form_id),
        ).fetchone()
        if not row:
            return None
        payload_json, updated_at, expires_at = row
        if expires_at:
            try:
                exp = datetime.datetime.fromisoformat(expires_at)
                if exp < datetime.datetime.utcnow():
                    return None  # treat expired as missing
            except (ValueError, TypeError):
                pass  # unparseable → trust it
        try:
            payload = _json_7e.loads(payload_json)
        except (TypeError, ValueError):
            return None
        return {
            "payload":    payload,
            "updated_at": updated_at,
            "expires_at": expires_at,
        }
    finally:
        if _owns:
            conn.close()


def delete_form_draft(
    username: str,
    form_id: str,
    *,
    conn: sqlite3.Connection = None,
) -> bool:
    """Delete a draft. Returns True if a row was removed."""
    username = (username or "").strip()
    form_id  = (form_id or "").strip()
    if not username or not form_id:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM form_drafts WHERE username = ? AND form_id = ?",
            (username, form_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        if _owns:
            conn.close()


def list_user_drafts(
    username: str,
    *,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Per-user multi-form draft listing — feeds a future Admin 'Active
    Drafts' view (deferred to 7E.1). Hides expired rows."""
    username = (username or "").strip()
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT id, form_id, site_id, updated_at, expires_at, "
            "       LENGTH(payload_json) AS payload_bytes "
            "FROM form_drafts WHERE username = ? "
            "  AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY updated_at DESC",
            conn,
            params=(username, datetime.datetime.utcnow().isoformat(timespec="seconds")),
        )
        from config import auto_localize_timestamps as _atz
        return _atz(df)
    finally:
        if _owns:
            conn.close()


def prune_expired_form_drafts(
    *,
    conn: sqlite3.Connection = None,
) -> int:
    """Delete every row where expires_at < now. Returns rowcount.

    Idempotent — called from whatsapp_worker's poll loop, day-gated via
    app_settings.form_drafts_last_prune.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM form_drafts "
            "WHERE expires_at IS NOT NULL AND expires_at < ?",
            (datetime.datetime.utcnow().isoformat(timespec="seconds"),),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Phase 8E — LocateAnything sidecar telemetry
# ═══════════════════════════════════════════════════════════════════════════
def log_locate_anything_call(
    *,
    site_id: str | None = None,
    sk_username: str | None = None,
    yolo_top_conf: float | None = None,
    detection_count: int | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert one telemetry row. Returns the new rowid.

    Best-effort — never raises. The client wraps this in try/except so a
    DB hiccup never bubbles up to the user. `accepted` is intentionally
    NOT a kwarg here: it's filled later by mark_locate_anything_outcome()
    when the SK clicks accept/reject in the UI.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO locate_anything_calls "
            "(site_id, sk_username, yolo_top_conf, detection_count, "
            " latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (site_id, sk_username,
             (None if yolo_top_conf is None else float(yolo_top_conf)),
             (None if detection_count is None else int(detection_count)),
             (None if latency_ms is None else int(latency_ms)),
             (error or None)),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        if _owns:
            conn.close()


def mark_locate_anything_outcome(
    call_id: int,
    accepted: bool,
    *,
    conn: sqlite3.Connection = None,
) -> bool:
    """Update an earlier telemetry row with the SK's accept/reject decision.

    Returns True if a row was updated. Idempotent — second call with the
    same call_id is harmless (overwrites). Used by the SK Tier-3 panel
    AFTER the user picks "Use this tool" or "None of these".
    """
    if not call_id:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE locate_anything_calls SET accepted = ? WHERE id = ?",
            (1 if accepted else 0, int(call_id)),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        if _owns:
            conn.close()


def get_locate_anything_summary(
    *,
    days: int = 7,
    conn: sqlite3.Connection = None,
) -> dict:
    """Roll-up for the Admin Settings panel. Returns:
      {calls, errors, accepted, rejected, pending, avg_latency_ms,
       error_rate_pct, accept_rate_pct}.
    All counts default to 0 when there's no data; rates are 0.0 when
    the denominator is 0 (avoid ZeroDivisionError).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT "
            "  COUNT(*)                                 AS calls, "
            "  SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) AS errors, "
            "  SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS accepted, "
            "  SUM(CASE WHEN accepted = 0 THEN 1 ELSE 0 END) AS rejected, "
            "  SUM(CASE WHEN accepted IS NULL THEN 1 ELSE 0 END) AS pending, "
            "  AVG(latency_ms) AS avg_latency "
            "FROM locate_anything_calls "
            "WHERE called_at >= datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchone()
        calls    = int(row[0] or 0)
        errors   = int(row[1] or 0)
        accepted = int(row[2] or 0)
        rejected = int(row[3] or 0)
        pending  = int(row[4] or 0)
        avg_lat  = float(row[5] or 0.0)
        decided  = accepted + rejected
        return {
            "calls":            calls,
            "errors":           errors,
            "accepted":         accepted,
            "rejected":         rejected,
            "pending":          pending,
            "avg_latency_ms":   round(avg_lat, 1),
            "error_rate_pct":   round(100.0 * errors / calls, 1) if calls else 0.0,
            "accept_rate_pct":  round(100.0 * accepted / decided, 1) if decided else 0.0,
        }
    finally:
        if _owns:
            conn.close()


def list_recent_locate_anything_calls(
    *,
    limit: int = 20,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Recent telemetry table for the Admin Settings panel. Localised
    timestamps so display matches the rest of the site."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT id, called_at, site_id, sk_username, "
            "       yolo_top_conf, detection_count, accepted, "
            "       latency_ms, error "
            "FROM locate_anything_calls "
            "ORDER BY called_at DESC LIMIT ?",
            conn, params=(int(limit),),
        )
        from config import auto_localize_timestamps as _atz
        return _atz(df)
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Round 17 — Smart Material Estimator (SME) helpers
# ---------------------------------------------------------------------------
# Pure-Python adapters that bridge the ERP ledger to SME's allocation engine.
# The engine consumes three DataFrames (equipment, recipe, inventory) and
# returns allocation/feasibility/procurement DataFrames. It never touches
# SQLite directly — these helpers shape the inputs.
#
# Contract:
#   - `Available_Qty` is computed from receipts/consumption/returns via
#     load_live_inventory(); it is NEVER a stored scalar.
#   - `Ordered_Qty` is the open-PO outstanding quantity per Material_Code,
#     summed across open / partially-delivered POs whose lines are still live.
#   - All reads are site-scoped if a site_id is passed; global otherwise.

def get_on_order_by_material(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Open-PO outstanding quantity per Material_Code.

    Outstanding = Qty − Delivered_Qty − Returned_Qty, summed over po_items
    whose parent purchase_orders.status is 'open' or 'partially_delivered'
    AND whose line_status is not 'closed' / 'force_closed'.

    Returns columns: Material_Code, Ordered_Qty. Empty DataFrame if no rows.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        params: tuple = ()
        site_pred = ""
        if site_id:
            site_pred = " AND po.Site_ID = ?"
            params = (site_id,)
        sql = (
            "SELECT pi.Material_Code AS Material_Code, "
            "       SUM(MAX("
            "         COALESCE(pi.Qty,0) "
            "         - COALESCE(pi.Delivered_Qty,0) "
            "         - COALESCE(pi.Returned_Qty,0), 0)) AS Ordered_Qty "
            "FROM po_items pi "
            "JOIN purchase_orders po ON po.PO_Number = pi.PO_Number "
            "WHERE po.status IN ('open','partially_delivered') "
            "  AND COALESCE(pi.line_status,'open') "
            "      NOT IN ('closed','force_closed') "
            "  AND pi.Material_Code IS NOT NULL "
            "  AND TRIM(pi.Material_Code) <> ''"
            f"{site_pred} "
            "GROUP BY pi.Material_Code"
        )
        df = pd.read_sql(sql, conn, params=params)
        if df.empty:
            return pd.DataFrame(columns=["Material_Code", "Ordered_Qty"])
        df["Ordered_Qty"] = pd.to_numeric(
            df["Ordered_Qty"], errors="coerce"
        ).fillna(0.0)
        df["Material_Code"] = df["Material_Code"].astype(str).str.strip()
        return df
    finally:
        if _owns:
            conn.close()


def get_sme_inventory_view(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """SME allocation-engine inventory contract — sourced from the SME's own
    inventory baseline (`sme_inventory_seed`), NOT ERP live stock.

    Returns one row per Material_Code with columns:
        Material_Code, Material_Name, UOM, Nature,
        Available_Qty, Ordered_Qty

    R20.5.1 — REWIRED to the approved isolation model. The SME inventory
    store is deliberately kept separate from ERP `inventory`; the SME
    portal must reflect the quantities from Materials_DetailsAvailable_Qty.xlsx
    (loaded into `sme_inventory_seed`), with live movements rolled up from
    the ERP ledger:

        Available_Qty = Initial_Available_Qty
                        + receipts (SME-tagged, via SAP_Code → Material_Code)
                        - consumption (SME-tagged, same path)
        Ordered_Qty   = Initial_Ordered_Qty

    All of that math lives in the `sme_materials_view` SQL view (see init_db),
    so this helper just reads it and shapes the result to the engine contract.
    Previously this read ERP live stock + open POs, which were always 0 for
    SME materials (they were never received into ERP `inventory`), so every
    SME analytical tab showed Available/Ordered = 0.

    `site_id` is accepted for signature compatibility but the SME inventory
    baseline + ledger rollup are global (matching the standalone SME, whose
    inventory file is not site-scoped). Materials in `sme_recipe` but absent
    from the seed simply don't appear here → the engine treats them as 0
    available (shortfall), exactly as the standalone SME did when a material
    wasn't in its inventory file.
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    _EMPTY = pd.DataFrame(columns=[
        "Material_Code", "Material_Name", "UOM",
        "Nature", "Available_Qty", "Ordered_Qty",
    ])
    try:
        try:
            df = pd.read_sql(
                "SELECT material_code        AS Material_Code, "
                "       material_name        AS Material_Name, "
                "       uom                  AS UOM, "
                "       nature               AS Nature, "
                "       available_qty        AS Available_Qty, "
                "       ordered_qty          AS Ordered_Qty "
                "FROM sme_materials_view",
                conn,
            )
        except Exception:
            # View missing (pre-R20.5 DB not yet self-healed) → empty contract.
            return _EMPTY
        if df.empty:
            return _EMPTY

        df["Material_Code"] = df["Material_Code"].astype(str).str.strip()
        df = df[df["Material_Code"] != ""].copy()
        df["Available_Qty"] = pd.to_numeric(
            df["Available_Qty"], errors="coerce").fillna(0.0)
        df["Ordered_Qty"] = pd.to_numeric(
            df["Ordered_Qty"], errors="coerce").fillna(0.0)

        # Fallback-enrich Material_Name / UOM / Nature from the recipe master
        # when the seed left them blank (the recipe carries project labels).
        try:
            rec = pd.read_sql(
                "SELECT DISTINCT Material_Code, "
                "       Material_Name AS _rec_name, "
                "       UOM           AS _rec_uom, "
                "       Nature        AS _rec_nature "
                "FROM sme_recipe",
                conn,
            )
        except Exception:
            rec = pd.DataFrame(columns=[
                "Material_Code", "_rec_name", "_rec_uom", "_rec_nature",
            ])
        if not rec.empty:
            rec["Material_Code"] = rec["Material_Code"].astype(str).str.strip()
            rec = rec.drop_duplicates(subset=["Material_Code"], keep="first")
            df = df.merge(rec, on="Material_Code", how="left")
            df["Material_Name"] = (
                df["Material_Name"].replace({"": None})
                .fillna(df["_rec_name"])
            )
            df["UOM"] = df["UOM"].replace({"": None}).fillna(df["_rec_uom"])
            df["Nature"] = df["Nature"].replace({"": None}).fillna(df["_rec_nature"])
            df = df.drop(columns=["_rec_name", "_rec_uom", "_rec_nature"],
                         errors="ignore")

        return df[[
            "Material_Code", "Material_Name", "UOM",
            "Nature", "Available_Qty", "Ordered_Qty",
        ]].sort_values("Material_Code").reset_index(drop=True)
    finally:
        if _owns:
            conn.close()


def get_sme_equipment(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return SME equipment master with allocation-engine column names.

    The engine expects columns: Equipment_Tag_No., Name, Lining_System_Code,
    Surface_Area_SQM. We also surface Location / Type for the Location and
    Equipment reports (they don't break the engine).
    """
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        params: tuple = ()
        where = ""
        if site_id:
            where = " WHERE Site_ID = ?"
            params = (site_id,)
        df = pd.read_sql(
            "SELECT Site_ID, Equipment_Tag_No, Name, Location, Type, "
            "       Substrate, COALESCE(Sub_Location,'') AS Sub_Location, "
            "       Lining_System_Code, "
            "       COALESCE(Surface_Area_SQM,0) AS Surface_Area_SQM "
            f"FROM sme_equipment{where}",
            conn, params=params,
        )
        if df.empty:
            return pd.DataFrame(columns=[
                "Site_ID", "Equipment_Tag_No.", "Name", "Location", "Type",
                "Substrate", "Sub_Location", "Lining_System_Code",
                "Surface_Area_SQM",
            ])
        # The engine joins on the exact label 'Equipment_Tag_No.' (with the
        # trailing dot — see allocation_engine.py:57). Preserve that contract.
        df = df.rename(columns={"Equipment_Tag_No": "Equipment_Tag_No."})
        return df
    finally:
        if _owns:
            conn.close()


def get_sme_recipe(conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Return SME lining-system recipe master (global — recipes are not
    site-scoped). Columns shaped for the allocation engine."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT Lining_System_Code, Lining_System_Name, "
            "       Material_Code, Material_Name, UOM, Nature, "
            "       COALESCE(For_1_SQM,0) AS For_1_SQM "
            "FROM sme_recipe",
            conn,
        )
        return df
    finally:
        if _owns:
            conn.close()


def get_sme_sqm_progress(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Return per-equipment / per-system SQM progress for the given site."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        params: tuple = ()
        where = ""
        if site_id:
            where = " WHERE Site_ID = ?"
            params = (site_id,)
        return pd.read_sql(
            "SELECT Site_ID, Equipment_Tag_No AS \"Equipment_Tag_No.\", "
            "       Lining_System_Code, Original_SQM, Done_SQM "
            f"FROM sme_sqm_progress{where}",
            conn, params=params,
        )
    finally:
        if _owns:
            conn.close()


# ── system_settings-backed SME dropdown helpers ─────────────────────────────
# Mirror the get_work_types / get_tank_nos pattern. Site-specific values
# take precedence; falls back to global (Site_ID IS NULL) values, then to
# the seed-set on the 'HQ' site for first-run safety.

def _sme_settings_get(
    category: str,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> list[str]:
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        if site_id:
            rows = pd.read_sql(
                "SELECT value FROM system_settings "
                "WHERE category=? AND Site_ID=?",
                conn, params=(category, site_id),
            )["value"].tolist()
            if rows:
                return rows
        rows = pd.read_sql(
            "SELECT value FROM system_settings "
            "WHERE category=? AND Site_ID IS NULL",
            conn, params=(category,),
        )["value"].tolist()
        if rows:
            return rows
        # Last-resort: HQ seed values so a brand-new site always has a list.
        return pd.read_sql(
            "SELECT value FROM system_settings "
            "WHERE category=? AND Site_ID='HQ'",
            conn, params=(category,),
        )["value"].tolist()
    finally:
        if _owns:
            conn.close()


def get_sme_locations(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> list[str]:
    """Project-internal locations for the SME portal (Brown Field / TRAIN J
    / TRAIN K-style values). NOT to be confused with ERP Site_ID."""
    return _sme_settings_get("sme_location", site_id=site_id, conn=conn)


def get_sme_equipment_types(
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> list[str]:
    """Equipment-type dropdown values (Vessel / Tank / Column / …)."""
    return _sme_settings_get("sme_equipment_type", site_id=site_id, conn=conn)


def add_sme_setting(
    category: str,
    value: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """Append a single SME dropdown value. Idempotent — refuses to insert a
    duplicate (category, value, Site_ID) triple. Returns True on insert."""
    if category not in ("sme_location", "sme_equipment_type"):
        raise ValueError(f"Refusing to write unknown SME setting category: {category!r}")
    value = (value or "").strip()
    if not value:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM system_settings "
            "WHERE category=? AND value=? AND COALESCE(Site_ID,'')=? LIMIT 1",
            (category, value, site_id or ""),
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO system_settings (category, value, Site_ID) "
            "VALUES (?, ?, ?)",
            (category, value, site_id),
        )
        conn.commit()
        return True
    finally:
        if _owns:
            conn.close()


def delete_sme_setting(
    category: str,
    value: str,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> int:
    """Remove a single SME dropdown value scoped to a site. Returns the row
    count deleted (0 if not found). Won't touch seed-set values on 'HQ'
    because the bootstrap re-seeds them on every init_db — the UI exposes
    this for site-specific deletions only."""
    if category not in ("sme_location", "sme_equipment_type"):
        raise ValueError(f"Refusing to delete unknown SME setting category: {category!r}")
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM system_settings "
            "WHERE category=? AND value=? AND COALESCE(Site_ID,'')=?",
            (category, value, site_id or ""),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def upsert_sme_sqm_progress(
    site_id: str,
    equipment_tag: str,
    lining_system_code: str,
    *,
    original_sqm: float | None = None,
    done_sqm: float | None = None,
    conn: sqlite3.Connection = None,
) -> None:
    """Update SME progress in place. NULL kwargs leave existing values
    untouched (matches the bootstrap's preservation contract — done_sqm
    survives recipe re-loads). Used by the bootstrap script and by future
    HOD progress edits."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT Original_SQM, Done_SQM FROM sme_sqm_progress "
            "WHERE Site_ID=? AND Equipment_Tag_No=? AND Lining_System_Code=?",
            (site_id, equipment_tag, lining_system_code),
        ).fetchone()
        new_orig = (float(original_sqm) if original_sqm is not None
                    else (float(existing[0]) if existing else 0.0))
        new_done = (float(done_sqm) if done_sqm is not None
                    else (float(existing[1]) if existing else 0.0))
        conn.execute(
            "INSERT INTO sme_sqm_progress "
            "(Site_ID, Equipment_Tag_No, Lining_System_Code, "
            " Original_SQM, Done_SQM, updated_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(Site_ID, Equipment_Tag_No, Lining_System_Code) "
            "DO UPDATE SET "
            "  Original_SQM = excluded.Original_SQM, "
            "  Done_SQM     = excluded.Done_SQM, "
            "  updated_at   = CURRENT_TIMESTAMP",
            (site_id, equipment_tag, lining_system_code, new_orig, new_done),
        )
        conn.commit()
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# R20.5 — Master Data CRUD helpers (route Tab 8 writes off the compat VIEWs
# and onto the real sme_equipment / sme_recipe / sme_inventory_seed tables).
# All helpers translate the SME UI's lowercase / dotted / slashed form keys
# into the underlying PascalCase table columns, so the portal can keep its
# dynamic PRAGMA-driven form code intact.
# ---------------------------------------------------------------------------

# Maps every column alias the SME Master Data UI can emit (snake_case from
# PRAGMA table_info OR PascalCase / dotted forms hard-coded in the equipment
# Smart Entry form) onto the underlying sme_equipment column name.
_SME_EQUIPMENT_COL_MAP = {
    "site_id":                  "Site_ID",
    "equipment_tag":            "Equipment_Tag_No",
    "name":                     "Name",
    "location":                 "Location",
    "type":                     "Type",
    "substrate":                "Substrate",
    "lining_system_code":       "Lining_System_Code",
    "lining_system_short_name": "Lining_System_Short_Name",
    "lining_type":              "Lining_Type",
    "lining_system":            "Lining_System",
    "lining_systems":           "Lining_System",
    "material_spec":            "Material_Spec",
    "material spec.":           "Material_Spec",
    "design":                   "Design",
    "lining_area/location":     "Lining_Area_Location",
    "lining_area_location":     "Lining_Area_Location",
    "sl. #":                    "Sl_No",
    "sl_no":                    "Sl_No",
    "project":                  "Project",
    "wbs #":                    "WBS_No",
    "wbs_no":                   "WBS_No",
    "io#":                      "IO_No",
    "io_no":                    "IO_No",
    "drawing #":                "Drawing_No",
    "drawing_no":               "Drawing_No",
    "dia / l":                  "Dia_L",
    "dia_l":                    "Dia_L",
    "ht. /w":                   "Ht_W",
    "ht_w":                     "Ht_W",
    "equipment total sqm":      "Equipment_Total_SQM",
    "equipment_total_sqm":      "Equipment_Total_SQM",
    "remaraks":                 "Remaraks",
    "surface_area_sqm":         "Surface_Area_SQM",
}

_SME_RECIPE_COL_MAP = {
    "lining_system_code":       "Lining_System_Code",
    "lining_system_short_name": "Lining_System_Name",
    "lining_system_name":       "Lining_System_Name",
    "lining_type":              "Lining_Type",
    "lining_system":            "Lining_System",
    "substrate":                "Substrate",
    "system_keys":              "System_Keys",
    "lining_thickness":         "Lining_Thickness",
    "material_code":            "Material_Code",
    "material_description":     "Material_Description",
    "material_name":            "Material_Name",
    "for_1_sqm":                "For_1_SQM",
    "uom":                      "UOM",
    "nature":                   "Nature",
    "package_size":             "Package_Size",
    "sl. #":                    "Sl_No",
    "sl_no":                    "Sl_No",
}

_SME_INVENTORY_SEED_COL_MAP = {
    "material_code":         "Material_Code",
    "material_name":         "Material_Name",
    "item":                  "Item",
    "vendor":                "Vendor",
    "purchasing_document":   "Purchasing_Document",
    "document_date":         "Document_Date",
    "nature":                "Nature",
    "uom":                   "UOM",
    "initial_available_qty": "Initial_Available_Qty",
    "initial_ordered_qty":   "Initial_Ordered_Qty",
    # Allow plain available_qty/ordered_qty as aliases when the UI passes
    # the view's display column names (the view derives them, but on first
    # INSERT they map to the seed's initial_* slots).
    "available_qty":         "Initial_Available_Qty",
    "ordered_qty":           "Initial_Ordered_Qty",
}


def _translate_sme_cols(row: dict, col_map: dict) -> dict:
    """Lowercase + look up each key in col_map. Unknown keys are dropped
    silently (the UI may emit PRAGMA-derived columns the table doesn't
    persist, e.g. derived view columns). Returns {table_col: value}."""
    out = {}
    for k, v in (row or {}).items():
        if k is None:
            continue
        canonical = col_map.get(str(k).strip().lower())
        if canonical is None:
            # Also try the key verbatim (PascalCase / dotted forms keyed
            # directly in col_map at lower() time already handle most; this
            # catches odd one-off cases by case-insensitive exact match).
            continue
        out[canonical] = v
    return out


def insert_sme_equipment(
    row: dict,
    site_id: str,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert ONE row into sme_equipment. Site_ID comes from the kw, never
    from `row` (UI doesn't ask for it). Equipment_Tag_No + Lining_System_Code
    are required; raises ValueError if missing. Returns the new row id."""
    cols = _translate_sme_cols(row, _SME_EQUIPMENT_COL_MAP)
    cols["Site_ID"] = site_id
    if not cols.get("Equipment_Tag_No"):
        raise ValueError("Equipment_Tag_No is required")
    if not cols.get("Lining_System_Code"):
        raise ValueError("Lining_System_Code is required")
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        col_names = list(cols.keys())
        placeholders = ", ".join(["?"] * len(col_names))
        col_sql = ", ".join(f'"{c}"' for c in col_names)
        cur = conn.execute(
            f"INSERT INTO sme_equipment ({col_sql}) VALUES ({placeholders})",
            [cols[c] for c in col_names],
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def update_sme_equipment(
    eq_id: int,
    changes: dict,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Update one sme_equipment row by id. site_id is optional; when given,
    scopes the WHERE clause so cross-site edits can't happen accidentally.
    Returns rowcount."""
    cols = _translate_sme_cols(changes, _SME_EQUIPMENT_COL_MAP)
    if not cols:
        return 0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        set_sql = ", ".join(f'"{c}" = ?' for c in cols.keys())
        params = list(cols.values()) + [int(eq_id)]
        where = 'WHERE id = ?'
        if site_id:
            where += ' AND Site_ID = ?'
            params.append(site_id)
        cur = conn.execute(
            f"UPDATE sme_equipment SET {set_sql} {where}", params,
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def delete_sme_equipment(
    eq_id: int,
    site_id: str = None,
    conn: sqlite3.Connection = None,
) -> int:
    """Delete one sme_equipment row by id. Cascades the matching
    sme_sqm_progress entry. Returns sme_equipment rowcount deleted."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        # Look up tag + code so we can cascade sqm_progress.
        row = conn.execute(
            "SELECT Site_ID, Equipment_Tag_No, Lining_System_Code "
            "FROM sme_equipment WHERE id = ?",
            (int(eq_id),),
        ).fetchone()
        if not row:
            return 0
        _row_site, _tag, _code = row[0], row[1], row[2]
        if site_id and _row_site != site_id:
            return 0
        conn.execute(
            "DELETE FROM sme_sqm_progress "
            "WHERE Site_ID=? AND Equipment_Tag_No=? AND Lining_System_Code=?",
            (_row_site, _tag, _code),
        )
        cur = conn.execute(
            "DELETE FROM sme_equipment WHERE id = ?", (int(eq_id),),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def insert_sme_recipe(
    row: dict,
    conn: sqlite3.Connection = None,
) -> int:
    """Insert ONE row into sme_recipe. Lining_System_Code + Material_Code
    are required. Returns the new row id."""
    cols = _translate_sme_cols(row, _SME_RECIPE_COL_MAP)
    if not cols.get("Lining_System_Code"):
        raise ValueError("Lining_System_Code is required")
    if not cols.get("Material_Code"):
        raise ValueError("Material_Code is required")
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        col_names = list(cols.keys())
        placeholders = ", ".join(["?"] * len(col_names))
        col_sql = ", ".join(f'"{c}"' for c in col_names)
        cur = conn.execute(
            f"INSERT INTO sme_recipe ({col_sql}) VALUES ({placeholders})",
            [cols[c] for c in col_names],
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def update_sme_recipe(
    rec_id: int,
    changes: dict,
    conn: sqlite3.Connection = None,
) -> int:
    """Update one sme_recipe row by id. Returns rowcount."""
    cols = _translate_sme_cols(changes, _SME_RECIPE_COL_MAP)
    if not cols:
        return 0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        set_sql = ", ".join(f'"{c}" = ?' for c in cols.keys())
        params = list(cols.values()) + [int(rec_id)]
        cur = conn.execute(
            f"UPDATE sme_recipe SET {set_sql} WHERE id = ?", params,
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def delete_sme_recipe(
    rec_id: int,
    conn: sqlite3.Connection = None,
) -> int:
    """Delete one sme_recipe row by id. Returns rowcount."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM sme_recipe WHERE id = ?", (int(rec_id),),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def insert_sme_inventory_seed(
    row: dict,
    conn: sqlite3.Connection = None,
) -> int:
    """INSERT or REPLACE one sme_inventory_seed row. Material_Code is the
    PK; passing an existing code overwrites the seed (matches the SME
    Master Data UX where editing a code re-asserts the baseline)."""
    cols = _translate_sme_cols(row, _SME_INVENTORY_SEED_COL_MAP)
    if not cols.get("Material_Code"):
        raise ValueError("Material_Code is required")
    cols["Material_Code"] = str(cols["Material_Code"]).strip()
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        col_names = list(cols.keys())
        placeholders = ", ".join(["?"] * len(col_names))
        col_sql = ", ".join(f'"{c}"' for c in col_names)
        cur = conn.execute(
            f"INSERT OR REPLACE INTO sme_inventory_seed ({col_sql}) "
            f"VALUES ({placeholders})",
            [cols[c] for c in col_names],
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if _owns:
            conn.close()


def update_sme_inventory_seed(
    material_code: str,
    changes: dict,
    conn: sqlite3.Connection = None,
) -> int:
    """Update one sme_inventory_seed row by Material_Code. Returns rowcount."""
    cols = _translate_sme_cols(changes, _SME_INVENTORY_SEED_COL_MAP)
    # If the changes include Material_Code (e.g., renamed), drop it from
    # SET to avoid silently changing the PK on a cell-edit save.
    cols.pop("Material_Code", None)
    if not cols:
        return 0
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        set_sql = ", ".join(f'"{c}" = ?' for c in cols.keys())
        params = list(cols.values()) + [str(material_code).strip()]
        cur = conn.execute(
            f"UPDATE sme_inventory_seed SET {set_sql}, "
            f"updated_at = CURRENT_TIMESTAMP WHERE Material_Code = ?",
            params,
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


def delete_sme_inventory_seed(
    material_code: str,
    conn: sqlite3.Connection = None,
) -> int:
    """Delete one sme_inventory_seed row by Material_Code. Returns rowcount.
    Does NOT touch ERP `inventory` — receipts / consumption history is
    preserved by design (the seed is SME-owned, the ledger is ERP-owned)."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM sme_inventory_seed WHERE Material_Code = ?",
            (str(material_code).strip(),),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        if _owns:
            conn.close()


# ---------------------------------------------------------------------------
# Round 18 — SME consumption form: dispatch + staging + state-machine
# ---------------------------------------------------------------------------
# The SK's Consumption tab forks on inventory.is_sme (computed at runtime
# from sme_recipe membership). When the SK selects an SME-flagged material,
# the UI renders a multi-row form keyed on (equipment_tag × system_code ×
# material_code × sqm × actual_qty). On Submit Batch, the helper below
# aggregates qty per Material_Code, resolves the unique SAP_Code (1:1 for
# SME materials), writes one pending_issues row per material, and writes
# detailed rows to sme_consumption_log.
#
# State transitions:
#   staged → committed  : HOD commits via commit_eod_with_sme_sync. SQM
#                         shifts from Done_SQM_staged to Done_SQM.
#   staged → rejected   : HOD rejects via hod_reject_pending_issue_with_sme_sync.
#                         SQM decrements from Done_SQM_staged.
#   Per-row approve / unapprove do NOT change SME state — the row is still
#   staged from the estimator's POV; only commit_eod transitions it.

def is_sme_material(
    material_code: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """True if material_code participates in any sme_recipe row."""
    mc = (material_code or "").strip()
    if not mc:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM sme_recipe WHERE TRIM(Material_Code) = ? LIMIT 1",
            (mc,),
        ).fetchone()
        return bool(row)
    finally:
        if _owns:
            conn.close()


def is_sme_sap(
    sap_code: str,
    conn: sqlite3.Connection = None,
) -> bool:
    """True if the inventory row for sap_code carries a Material_Code that
    participates in any sme_recipe row. False for SAPs without a
    Material_Code or whose Material_Code is not in any recipe."""
    sc = (sap_code or "").strip()
    if not sc:
        return False
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT Material_Code FROM inventory "
            "WHERE TRIM(SAP_Code) = ? LIMIT 1",
            (sc,),
        ).fetchone()
        if not row:
            return False
        return is_sme_material(row[0], conn=conn)
    finally:
        if _owns:
            conn.close()


def get_sap_for_material(
    material_code: str,
    conn: sqlite3.Connection = None,
) -> str | None:
    """Resolve Material_Code → SAP_Code (1:1 by user contract). Returns
    None if no inventory row carries this material code."""
    mc = (material_code or "").strip()
    if not mc:
        return None
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT SAP_Code FROM inventory "
            "WHERE TRIM(Material_Code) = ? LIMIT 1",
            (mc,),
        ).fetchone()
        return row[0] if row else None
    finally:
        if _owns:
            conn.close()


def _bump_progress_staged(
    conn: sqlite3.Connection,
    site_id: str,
    equipment_tag: str,
    lining_system_code: str,
    delta: float,
) -> None:
    """Add `delta` (may be negative) to sme_sqm_progress.Done_SQM_staged.
    Creates the row if missing — Original_SQM defaults to 0 in that case."""
    conn.execute(
        "INSERT INTO sme_sqm_progress "
        "(Site_ID, Equipment_Tag_No, Lining_System_Code, "
        " Original_SQM, Done_SQM, Done_SQM_staged, updated_at) "
        "VALUES (?, ?, ?, 0, 0, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(Site_ID, Equipment_Tag_No, Lining_System_Code) "
        "DO UPDATE SET "
        "  Done_SQM_staged = MAX(0, "
        "    COALESCE(sme_sqm_progress.Done_SQM_staged,0) + excluded.Done_SQM_staged"
        "  ), "
        "  updated_at = CURRENT_TIMESTAMP",
        (site_id, equipment_tag, lining_system_code, float(delta)),
    )


def _shift_progress_staged_to_committed(
    conn: sqlite3.Connection,
    site_id: str,
    equipment_tag: str,
    lining_system_code: str,
    sqm: float,
) -> None:
    """Move `sqm` from Done_SQM_staged into Done_SQM. Idempotent — clamps
    staged at 0 so a double-call can't drive it negative."""
    conn.execute(
        "INSERT INTO sme_sqm_progress "
        "(Site_ID, Equipment_Tag_No, Lining_System_Code, "
        " Original_SQM, Done_SQM, Done_SQM_staged, updated_at) "
        "VALUES (?, ?, ?, 0, ?, 0, CURRENT_TIMESTAMP) "
        "ON CONFLICT(Site_ID, Equipment_Tag_No, Lining_System_Code) "
        "DO UPDATE SET "
        "  Done_SQM        = COALESCE(sme_sqm_progress.Done_SQM,0) + ?, "
        "  Done_SQM_staged = MAX(0, "
        "    COALESCE(sme_sqm_progress.Done_SQM_staged,0) - ?"
        "  ), "
        "  updated_at = CURRENT_TIMESTAMP",
        (site_id, equipment_tag, lining_system_code, float(sqm),
         float(sqm), float(sqm)),
    )


def stage_sme_consumption_batch(
    *,
    site_id: str,
    entry_date: str,
    entered_by: str,
    rows: list[dict],
    extras: dict | None = None,
    conn: sqlite3.Connection = None,
) -> dict:
    """Stage a multi-row SME consumption batch.

    `rows` is the SK's flattened grid — one dict per (equipment_tag ×
    system_code × material_code) entry:
        {
          "equipment_tag":      str,
          "lining_system_code": str,
          "material_code":      str,
          "sqm_completed":      float,
          "expected_qty":       float,
          "actual_qty":         float,
          "notes":              str | None,
        }

    `extras` carries the ERP's mandatory pending_issues fields that aren't
    captured per-material in the SME form. Required keys: Issued_To,
    Tank_No, Serial_No, PR_Number. Optional Work_Type, Source_Ref, etc.

    Behaviour:
      1. Aggregate Actual_Qty per Material_Code across all rows.
      2. For each aggregated material: resolve SAP_Code via
         get_sap_for_material (1:1 by user contract).
      3. INSERT one row into pending_issues per material with status
         'pending_hod' and the aggregated qty. Capture each new rowid.
      4. INSERT one row per detailed grid entry into sme_consumption_log
         with status='staged', batch_id (uuid hex), and staged_pi_id
         (which aggregated PI row this detail rolls up into).
      5. For each distinct (equipment_tag, system_code), bump
         Done_SQM_staged by the summed SQM.
      6. Return {batch_id, pending_issue_ids, materials_staged}.

    Raises ValueError on schema problems (missing material in inventory,
    no SAP_Code, missing extras keys).
    """
    import uuid
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        extras = dict(extras or {})
        required_extras = ("Issued_To", "Tank_No", "Serial_No", "PR_Number")
        missing = [k for k in required_extras if not (extras.get(k) or "").strip()]
        if missing:
            raise ValueError(
                f"SME consumption batch missing required extras: {missing}"
            )

        # 1. Aggregate per Material_Code
        per_material: dict[str, float] = {}
        for r in rows:
            mc = (r.get("material_code") or "").strip()
            qty = float(r.get("actual_qty") or 0)
            if not mc or qty <= 0:
                continue
            per_material[mc] = per_material.get(mc, 0.0) + qty
        if not per_material:
            raise ValueError("no positive-qty rows to stage")

        # 2. Resolve SAP_Codes + verify identity
        sap_map: dict[str, str] = {}
        for mc in per_material:
            sap = get_sap_for_material(mc, conn=conn)
            if not sap:
                raise ValueError(
                    f"Material_Code {mc!r} has no SAP_Code in inventory"
                )
            sap_map[mc] = sap

        # 3. Insert aggregated rows into pending_issues. We build the column
        # list dynamically from extras + standard fields to stay tolerant of
        # the ERP's evolving pending_issues schema.
        batch_id = uuid.uuid4().hex[:12]
        pi_col_info = conn.execute("PRAGMA table_info(pending_issues)").fetchall()
        pi_cols = {r[1] for r in pi_col_info}

        # Map material_code → list of (pi_id, qty) so we can link details
        material_to_pi: dict[str, int] = {}
        pending_ids: list[int] = []

        for mc, total_qty in per_material.items():
            payload: dict = {
                "Date":      entry_date,
                "SAP_Code":  sap_map[mc],
                "Quantity":  float(total_qty),
                "Site_ID":   site_id,
                "status":    "pending_hod",
            }
            if "Issued_By" in pi_cols:
                payload["Issued_By"] = entered_by
            for k in required_extras:
                if k in pi_cols:
                    payload[k] = extras.get(k)
            # Pass-through any other extras whose key is a real PI column.
            for k, v in extras.items():
                if k in required_extras or k in payload:
                    continue
                if k in pi_cols:
                    payload[k] = v
            # Source attribution
            if "Source_Ref" in pi_cols and "Source_Ref" not in payload:
                payload["Source_Ref"] = f"SME:{batch_id}"

            cols = list(payload.keys())
            quoted = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join(["?"] * len(cols))
            cur = conn.execute(
                f"INSERT INTO pending_issues ({quoted}) VALUES ({placeholders})",
                [payload[k] for k in cols],
            )
            material_to_pi[mc] = int(cur.lastrowid)
            pending_ids.append(int(cur.lastrowid))

        # 4. Write detailed sme_consumption_log rows
        for r in rows:
            mc = (r.get("material_code") or "").strip()
            qty = float(r.get("actual_qty") or 0)
            if not mc or qty <= 0:
                continue
            expected = float(r.get("expected_qty") or 0)
            var_pct = None
            if expected:
                var_pct = round((qty - expected) / expected * 100, 2)
            conn.execute(
                "INSERT INTO sme_consumption_log "
                "(batch_id, Site_ID, entry_date, entered_by, "
                " Equipment_Tag_No, Lining_System_Code, Material_Code, "
                " SQM_Completed, Expected_Qty, Actual_Qty, Variance_Pct, "
                " notes, status, staged_pi_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?)",
                (batch_id, site_id, entry_date, entered_by,
                 (r.get("equipment_tag") or "").strip(),
                 (r.get("lining_system_code") or "").strip(),
                 mc,
                 float(r.get("sqm_completed") or 0),
                 expected, qty, var_pct,
                 r.get("notes"),
                 material_to_pi[mc]),
            )

        # 5. Bump Done_SQM_staged per (equipment_tag, system_code).
        # SQM is per (tag, system) — same value across all materials of a
        # given (tag, system) entry. Dedupe by taking MAX SQM per pair.
        per_tag_sys: dict[tuple[str, str], float] = {}
        for r in rows:
            tag = (r.get("equipment_tag") or "").strip()
            sys = (r.get("lining_system_code") or "").strip()
            sqm = float(r.get("sqm_completed") or 0)
            if not tag or not sys or sqm <= 0:
                continue
            key = (tag, sys)
            per_tag_sys[key] = max(per_tag_sys.get(key, 0.0), sqm)
        for (tag, sys), sqm in per_tag_sys.items():
            _bump_progress_staged(conn, site_id, tag, sys, sqm)

        conn.commit()
        try:
            log_audit_action(
                entered_by, "STAGE_SME_BATCH", "sme_consumption_log",
                f"batch={batch_id} materials={len(per_material)} "
                f"pi_ids={pending_ids}",
            )
        except Exception:
            pass

        return {
            "batch_id": batch_id,
            "pending_issue_ids": pending_ids,
            "materials_staged": len(per_material),
        }
    finally:
        if _owns:
            conn.close()


def mark_sme_log_committed(
    pending_issue_ids: list[int],
    *,
    conn: sqlite3.Connection,
) -> int:
    """For each pending_issues.id in `pending_issue_ids`, flip every linked
    sme_consumption_log row from 'staged' → 'committed' and shift the
    underlying SQM from Done_SQM_staged into Done_SQM.

    Called from commit_eod_with_sme_sync AFTER commit_eod has run (and
    pending_issues rows have been moved to consumption). Idempotent — a
    second call on the same ids is a no-op because the rows are no longer
    in 'staged' status.
    """
    if not pending_issue_ids:
        return 0
    ids_clean = [int(i) for i in pending_issue_ids]
    phs = ", ".join(["?"] * len(ids_clean))
    rows = conn.execute(
        f"SELECT id, Site_ID, Equipment_Tag_No, Lining_System_Code, "
        f"       SQM_Completed FROM sme_consumption_log "
        f"WHERE status='staged' AND staged_pi_id IN ({phs})",
        ids_clean,
    ).fetchall()
    if not rows:
        return 0
    # Group SQM shifts by (site, tag, system); take MAX SQM per group to
    # match the stage-time bump (per-tag-system, not per-material).
    per_key: dict[tuple, float] = {}
    log_ids: list[int] = []
    for log_id, site, tag, sysc, sqm in rows:
        log_ids.append(int(log_id))
        if not tag or not sysc:
            continue
        key = (site, tag, sysc)
        per_key[key] = max(per_key.get(key, 0.0), float(sqm or 0))
    for (site, tag, sysc), sqm in per_key.items():
        if sqm > 0:
            _shift_progress_staged_to_committed(conn, site, tag, sysc, sqm)
    log_phs = ", ".join(["?"] * len(log_ids))
    conn.execute(
        f"UPDATE sme_consumption_log SET status='committed', "
        f"committed_at=CURRENT_TIMESTAMP WHERE id IN ({log_phs})",
        log_ids,
    )
    conn.commit()
    return len(log_ids)


def mark_sme_log_rejected(
    pending_issue_ids: list[int],
    *,
    rejected_by: str | None = None,
    reason: str | None = None,
    conn: sqlite3.Connection,
) -> int:
    """Flip every linked sme_consumption_log row from 'staged' → 'rejected'
    and decrement Done_SQM_staged. Called from
    hod_reject_pending_issue_with_sme_sync. Idempotent."""
    if not pending_issue_ids:
        return 0
    ids_clean = [int(i) for i in pending_issue_ids]
    phs = ", ".join(["?"] * len(ids_clean))
    rows = conn.execute(
        f"SELECT id, Site_ID, Equipment_Tag_No, Lining_System_Code, "
        f"       SQM_Completed FROM sme_consumption_log "
        f"WHERE status='staged' AND staged_pi_id IN ({phs})",
        ids_clean,
    ).fetchall()
    if not rows:
        return 0
    per_key: dict[tuple, float] = {}
    log_ids: list[int] = []
    for log_id, site, tag, sysc, sqm in rows:
        log_ids.append(int(log_id))
        if not tag or not sysc:
            continue
        key = (site, tag, sysc)
        per_key[key] = max(per_key.get(key, 0.0), float(sqm or 0))
    for (site, tag, sysc), sqm in per_key.items():
        if sqm > 0:
            _bump_progress_staged(conn, site, tag, sysc, -sqm)
    log_phs = ", ".join(["?"] * len(log_ids))
    conn.execute(
        f"UPDATE sme_consumption_log SET status='rejected', "
        f"rejected_at=CURRENT_TIMESTAMP, rejected_reason=? "
        f"WHERE id IN ({log_phs})",
        [reason or None, *log_ids],
    )
    conn.commit()
    return len(log_ids)


def commit_eod_with_sme_sync(
    conn: sqlite3.Connection = None,
    *,
    hod_username: str | None = None,
) -> int:
    """Wrapper around commit_eod that also flips linked sme_consumption_log
    rows to 'committed' and shifts SQM from staged to committed. Returns
    the same rows_committed count as commit_eod.

    Order matters: capture the to-be-committed pending_issues.id values
    BEFORE commit_eod deletes them, run commit_eod, then update the SME
    log using the captured ids. commit_eod itself is unchanged."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        to_commit_ids = [
            r[0] for r in conn.execute(
                f"SELECT id FROM pending_issues WHERE {_EOD_PI_STATUS_PRED}"
            ).fetchall()
        ]
        n = commit_eod(conn=conn, hod_username=hod_username)
        if to_commit_ids:
            try:
                mark_sme_log_committed(to_commit_ids, conn=conn)
            except Exception:
                # SME sync failure must NEVER undo the commit. Log and
                # continue — sync can be re-run later via the same helper
                # if the operator notices stale staged rows.
                try:
                    log_audit_action(
                        hod_username or "unknown",
                        "SME_SYNC_FAILED_ON_COMMIT",
                        "sme_consumption_log",
                        f"pi_ids={to_commit_ids}",
                    )
                except Exception:
                    pass
        return n
    finally:
        if _owns:
            conn.close()


def hod_reject_pending_issue_with_sme_sync(
    issue_id: int,
    *,
    rejected_by: str | None = None,
    reason: str | None = None,
    conn: sqlite3.Connection = None,
) -> bool:
    """Wrapper around hod_reject_pending_issue. Flips linked
    sme_consumption_log rows to 'rejected' and decrements Done_SQM_staged.
    Returns whether the underlying reject succeeded."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        ok = hod_reject_pending_issue(
            issue_id, rejected_by=rejected_by, reason=reason, conn=conn,
        )
        if ok:
            try:
                mark_sme_log_rejected(
                    [int(issue_id)],
                    rejected_by=rejected_by, reason=reason, conn=conn,
                )
            except Exception:
                try:
                    log_audit_action(
                        rejected_by or "unknown",
                        "SME_SYNC_FAILED_ON_REJECT",
                        "sme_consumption_log",
                        f"pi_id={issue_id}",
                    )
                except Exception:
                    pass
        return ok
    finally:
        if _owns:
            conn.close()


def get_sme_consumption_log(
    *,
    site_id: str | None = None,
    status: str | None = None,
    batch_id: str | None = None,
    limit: int = 500,
    conn: sqlite3.Connection = None,
) -> pd.DataFrame:
    """Read sme_consumption_log with optional filters. Used by HOD/Admin
    audit screens + the post-Submit-Batch confirmation panel."""
    _owns = conn is None
    if _owns:
        conn = get_connection()
    try:
        where: list[str] = []
        params: list = []
        if site_id:
            where.append("Site_ID = ?")
            params.append(site_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if batch_id:
            where.append("batch_id = ?")
            params.append(batch_id)
        sql = (
            "SELECT id, batch_id, Site_ID, entry_date, entered_by, "
            "       Equipment_Tag_No, Lining_System_Code, Material_Code, "
            "       SQM_Completed, Expected_Qty, Actual_Qty, Variance_Pct, "
            "       notes, status, staged_pi_id, committed_at, "
            "       rejected_at, rejected_reason, created_at "
            "FROM sme_consumption_log "
        )
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        return _localize(pd.read_sql(sql, conn, params=tuple(params)))
    finally:
        if _owns:
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Man-Hour & Labor Tracking (workstream §2Z)
# ---------------------------------------------------------------------------
# Isolated mh_* domain. These helpers WRITE only to mh_* tables and READ
# sme_equipment / sme_recipe / sme_sqm_progress read-only (for dropdowns). They
# never touch the inventory ledger, the EOD path, or any sme_* write. Hours are
# computed (8h normal + 1h unpaid break); the source attendance file's dirty
# hour columns are ignored. Site_ID threaded through every function (RULE 3).
# ═══════════════════════════════════════════════════════════════════════════

MH_NORMAL_THRESHOLD_HOURS = 8.0   # hours/day counted as "normal" before OT
MH_DEFAULT_BREAK_MINS = 60        # unpaid break deducted from gross


def _mh_time_to_minutes(value) -> "int | None":
    """Parse a clock time → minutes-since-midnight. Tolerant of datetime/time
    objects and 'HH:MM' / 'HH:MM:SS' strings. Returns None if unparseable."""
    import datetime as _dt
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, _dt.time):
        return value.hour * 60 + value.minute
    parts = str(value).strip().split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    except (ValueError, IndexError):
        return None


def compute_mh_hours(in_time, out_time, break_mins: int = MH_DEFAULT_BREAK_MINS,
                     normal_threshold: float = MH_NORMAL_THRESHOLD_HOURS
                     ) -> "tuple[float, float, float]":
    """Return (Total, Normal, OT) hours from In/Out times.

    Total  = max(0, (Out − In) − break) in hours (overnight shifts wrap +24h)
    Normal = min(Total, normal_threshold)
    OT     = max(0, Total − normal_threshold)
    """
    im = _mh_time_to_minutes(in_time)
    om = _mh_time_to_minutes(out_time)
    if im is None or om is None:
        return 0.0, 0.0, 0.0
    gross = om - im
    if gross < 0:
        gross += 24 * 60  # overnight shift guard
    net = max(0.0, (gross - int(break_mins or 0)) / 60.0)
    total = round(net, 2)
    normal = round(min(total, normal_threshold), 2)
    ot = round(max(0.0, total - normal_threshold), 2)
    return total, normal, ot


# ── mh_employees (labor roster) ─────────────────────────────────────────────
def upsert_mh_employee(site_id: str, employee_code: str, name: str, *,
                       designation: str = "", worker_type: str = "OWN",
                       company: str = "", linked_id_number: str = None,
                       status: str = "active", created_by: str = "system",
                       conn: sqlite3.Connection = None) -> "tuple[bool, str]":
    """Insert-or-update one labor-roster row (UNIQUE Site_ID + Employee_Code).
    Returns (False, msg) on bad input — never raises."""
    site_id = (site_id or "").strip()
    employee_code = (employee_code or "").strip()
    name = (name or "").strip()
    if not site_id or not employee_code or not name:
        return False, "Site_ID, Employee_Code and Name are required."
    wt = (worker_type or "OWN").strip()
    if wt not in ("OWN", "Supply"):
        return False, "Worker_Type must be 'OWN' or 'Supply'."
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO mh_employees "
            "(Site_ID, Employee_Code, Name, Designation, Worker_Type, Company, "
            " linked_id_number, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(Site_ID, Employee_Code) DO UPDATE SET "
            "  Name=excluded.Name, Designation=excluded.Designation, "
            "  Worker_Type=excluded.Worker_Type, Company=excluded.Company, "
            "  linked_id_number=excluded.linked_id_number, status=excluded.status, "
            "  updated_at=CURRENT_TIMESTAMP",
            (site_id, employee_code, name, (designation or "").strip(), wt,
             (company or "").strip(),
             (linked_id_number or "").strip() or None, status, created_by),
        )
        _c.commit()
        return True, f"Employee {employee_code} saved."
    finally:
        if _owns:
            _c.close()


def list_mh_employees(site_id: str = None, status: str = None,
                      conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Labor roster as a DataFrame, optionally filtered by site and/or status."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        where, params = [], []
        if site_id:
            where.append("Site_ID = ?"); params.append(site_id)
        if status:
            where.append("status = ?"); params.append(status)
        sql = ("SELECT id, Site_ID, Employee_Code, Name, Designation, "
               "Worker_Type, Company, linked_id_number, status, created_at "
               "FROM mh_employees ")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY Employee_Code"
        return _localize(pd.read_sql(sql, _c, params=tuple(params)))
    finally:
        if _owns:
            _c.close()


def set_mh_employee_status(emp_id: int, status: str,
                           conn: sqlite3.Connection = None) -> bool:
    """Flip an employee active/inactive."""
    if status not in ("active", "inactive"):
        return False
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute("UPDATE mh_employees SET status=?, updated_at=CURRENT_TIMESTAMP "
                   "WHERE id=?", (status, int(emp_id)))
        _c.commit()
        return True
    finally:
        if _owns:
            _c.close()


# ── mh_timesheets (daily actuals) ───────────────────────────────────────────
def add_mh_timesheet(site_id: str, employee_code: str, work_date: str,
                     in_time, out_time, *, location: str = "",
                     equipment_tag: str = "", system_code: str = "",
                     break_mins: int = MH_DEFAULT_BREAK_MINS,
                     status: str = "PR", remarks: str = "",
                     created_by: str = "system",
                     conn: sqlite3.Connection = None) -> "tuple[bool, str]":
    """Insert one timesheet line; hours are computed from In/Out − break.
    On UNIQUE collision (same site/emp/date/tag/system) updates in place."""
    site_id = (site_id or "").strip()
    employee_code = (employee_code or "").strip()
    work_date = str(work_date or "").strip()[:10]
    if not site_id or not employee_code or not work_date:
        return False, "Site_ID, Employee_Code and Work_Date are required."
    total, normal, ot = compute_mh_hours(in_time, out_time, break_mins)
    in_s = "" if in_time is None else str(in_time)
    out_s = "" if out_time is None else str(out_time)
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO mh_timesheets "
            "(Site_ID, Employee_Code, Work_Date, Location, Equipment_Tag, "
            " System_Code, In_Time, Out_Time, Break_Mins, Total_Hours, "
            " Normal_Hours, OT_Hours, Status, Remarks, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(Site_ID, Employee_Code, Work_Date, Equipment_Tag, System_Code) "
            "DO UPDATE SET In_Time=excluded.In_Time, Out_Time=excluded.Out_Time, "
            "  Location=excluded.Location, Break_Mins=excluded.Break_Mins, "
            "  Total_Hours=excluded.Total_Hours, Normal_Hours=excluded.Normal_Hours, "
            "  OT_Hours=excluded.OT_Hours, Status=excluded.Status, "
            "  Remarks=excluded.Remarks",
            (site_id, employee_code, work_date, (location or "").strip() or None,
             (equipment_tag or "").strip() or None,
             (system_code or "").strip() or None, in_s, out_s, int(break_mins or 0),
             total, normal, ot, status, (remarks or "").strip(), created_by),
        )
        _c.commit()
        return True, f"Timesheet saved ({total}h)."
    finally:
        if _owns:
            _c.close()


def list_mh_timesheets(site_id: str = None, *, work_date: str = None,
                       employee_code: str = None, equipment_tag: str = None,
                       system_code: str = None, date_from: str = None,
                       date_to: str = None,
                       conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Daily timesheet rows as a DataFrame with flexible filters."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        where, params = [], []
        for col, val in (("Site_ID", site_id), ("Work_Date", work_date),
                         ("Employee_Code", employee_code),
                         ("Equipment_Tag", equipment_tag),
                         ("System_Code", system_code)):
            if val:
                where.append(f"{col} = ?"); params.append(val)
        if date_from:
            where.append("Work_Date >= ?"); params.append(date_from)
        if date_to:
            where.append("Work_Date <= ?"); params.append(date_to)
        sql = ("SELECT id, Site_ID, Employee_Code, Work_Date, Location, "
               "Equipment_Tag, System_Code, In_Time, Out_Time, Break_Mins, "
               "Total_Hours, Normal_Hours, OT_Hours, Allocated_SQM, Status, "
               "Remarks, created_at FROM mh_timesheets ")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY Work_Date DESC, Employee_Code"
        return _localize(pd.read_sql(sql, _c, params=tuple(params)))
    finally:
        if _owns:
            _c.close()


def get_mh_employee_timeline(site_id: str = None, employee_code: str = None, *,
                             date_from: str = None, date_to: str = None,
                             conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Employee-wise, date-ordered view: where each person worked and the hours
    booked. Powers the 'neat, not clumsy' Employee-wise tab."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        where, params = [], []
        if site_id:
            where.append("t.Site_ID = ?"); params.append(site_id)
        if employee_code:
            where.append("t.Employee_Code = ?"); params.append(employee_code)
        if date_from:
            where.append("t.Work_Date >= ?"); params.append(date_from)
        if date_to:
            where.append("t.Work_Date <= ?"); params.append(date_to)
        sql = ("SELECT t.Employee_Code, COALESCE(e.Name, t.Employee_Code) AS Name, "
               "t.Work_Date, t.Location, t.Equipment_Tag, t.System_Code, "
               "t.Total_Hours, t.Normal_Hours, t.OT_Hours, t.Allocated_SQM "
               "FROM mh_timesheets t "
               "LEFT JOIN mh_employees e "
               "  ON e.Site_ID = t.Site_ID AND e.Employee_Code = t.Employee_Code ")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY t.Employee_Code, t.Work_Date"
        return _localize(pd.read_sql(sql, _c, params=tuple(params)))
    finally:
        if _owns:
            _c.close()


# ── mh_production (team SQM) + distribution ─────────────────────────────────
def set_mh_production(site_id: str, work_date: str, equipment_tag: str,
                      system_code: str, sqm_done: float, *,
                      distribution_method: str = "even",
                      created_by: str = "system",
                      conn: sqlite3.Connection = None,
                      auto_distribute: bool = True) -> "tuple[bool, str]":
    """Record the team's SQM for a day/tag/system and (optionally) distribute it
    across that day's workers into mh_timesheets.Allocated_SQM."""
    if distribution_method not in ("even", "by_hours", "manual"):
        return False, "distribution_method must be even | by_hours | manual."
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO mh_production "
            "(Site_ID, Work_Date, Equipment_Tag, System_Code, SQM_Done, "
            " Distribution_Method, created_by) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(Site_ID, Work_Date, Equipment_Tag, System_Code) "
            "DO UPDATE SET SQM_Done=excluded.SQM_Done, "
            "  Distribution_Method=excluded.Distribution_Method",
            (site_id, str(work_date)[:10], equipment_tag, system_code,
             float(sqm_done or 0), distribution_method, created_by),
        )
        _c.commit()
        if auto_distribute and distribution_method != "manual":
            distribute_mh_sqm(site_id, work_date, equipment_tag, system_code,
                              method=distribution_method, conn=_c)
        return True, "Production SQM saved."
    finally:
        if _owns:
            _c.close()


def distribute_mh_sqm(site_id: str, work_date: str, equipment_tag: str,
                      system_code: str, *, method: str = "even",
                      conn: sqlite3.Connection = None) -> int:
    """Split a day's team SQM into each worker's Allocated_SQM. 'even' = equal
    per worker; 'by_hours' = pro-rata on Total_Hours. Returns rows updated."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        row = _c.execute(
            "SELECT SQM_Done FROM mh_production WHERE Site_ID=? AND Work_Date=? "
            "AND Equipment_Tag=? AND System_Code=?",
            (site_id, str(work_date)[:10], equipment_tag, system_code),
        ).fetchone()
        if not row:
            return 0
        total_sqm = float(row[0] or 0)
        rows = _c.execute(
            "SELECT id, Total_Hours FROM mh_timesheets WHERE Site_ID=? AND "
            "Work_Date=? AND Equipment_Tag=? AND System_Code=?",
            (site_id, str(work_date)[:10], equipment_tag, system_code),
        ).fetchall()
        if not rows:
            return 0
        if method == "by_hours":
            hours_sum = sum(float(r[1] or 0) for r in rows) or 0
            for rid, hrs in rows:
                share = (total_sqm * float(hrs or 0) / hours_sum) if hours_sum else 0
                _c.execute("UPDATE mh_timesheets SET Allocated_SQM=? WHERE id=?",
                           (round(share, 3), rid))
        else:  # even
            share = total_sqm / len(rows)
            for rid, _hrs in rows:
                _c.execute("UPDATE mh_timesheets SET Allocated_SQM=? WHERE id=?",
                           (round(share, 3), rid))
        _c.commit()
        return len(rows)
    finally:
        if _owns:
            _c.close()


# ── mh_manhour_estimates + variance notes ───────────────────────────────────
def upsert_mh_estimate(site_id: str, equipment_tag: str, system_code: str,
                       estimated_manhours: float, *, location: str = "",
                       estimated_sqm: float = None, basis: str = "",
                       created_by: str = "system",
                       conn: sqlite3.Connection = None) -> "tuple[bool, str]":
    """Define/update the required man-hours for a Tag/System (the estimator)."""
    site_id = (site_id or "").strip()
    equipment_tag = (equipment_tag or "").strip()
    system_code = (system_code or "").strip()
    if not site_id or not equipment_tag or not system_code:
        return False, "Site_ID, Equipment_Tag and System_Code are required."
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO mh_manhour_estimates "
            "(Site_ID, Location, Equipment_Tag, System_Code, Estimated_Manhours, "
            " Estimated_SQM, Basis, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(Site_ID, Equipment_Tag, System_Code) DO UPDATE SET "
            "  Location=excluded.Location, "
            "  Estimated_Manhours=excluded.Estimated_Manhours, "
            "  Estimated_SQM=excluded.Estimated_SQM, Basis=excluded.Basis, "
            "  updated_at=CURRENT_TIMESTAMP",
            (site_id, (location or "").strip() or None, equipment_tag, system_code,
             float(estimated_manhours or 0),
             None if estimated_sqm in (None, "") else float(estimated_sqm),
             (basis or "").strip(), created_by),
        )
        _c.commit()
        return True, "Estimate saved."
    finally:
        if _owns:
            _c.close()


def list_mh_estimates(site_id: str = None,
                      conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Man-hour estimates as a DataFrame."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        where, params = [], []
        if site_id:
            where.append("Site_ID = ?"); params.append(site_id)
        sql = ("SELECT id, Site_ID, Location, Equipment_Tag, System_Code, "
               "Estimated_Manhours, Estimated_SQM, Basis, created_at "
               "FROM mh_manhour_estimates ")
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY Equipment_Tag, System_Code"
        return _localize(pd.read_sql(sql, _c, params=tuple(params)))
    finally:
        if _owns:
            _c.close()


def set_mh_variance_reason(site_id: str, equipment_tag: str, system_code: str,
                           reason: str, entered_by: str = "system",
                           conn: sqlite3.Connection = None) -> "tuple[bool, str]":
    """Record/replace the over-consumption reason for a Tag/System."""
    if not (reason or "").strip():
        return False, "Reason is required."
    _c, _owns = _conn_ctx_6a(conn)
    try:
        _c.execute(
            "INSERT INTO mh_variance_notes "
            "(Site_ID, Equipment_Tag, System_Code, Reason, entered_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(Site_ID, Equipment_Tag, System_Code) DO UPDATE SET "
            "  Reason=excluded.Reason, entered_by=excluded.entered_by, "
            "  created_at=CURRENT_TIMESTAMP",
            (site_id, equipment_tag, system_code, reason.strip(), entered_by),
        )
        _c.commit()
        return True, "Reason saved."
    finally:
        if _owns:
            _c.close()


def get_mh_estimate_vs_actual(site_id: str = None,
                              conn: sqlite3.Connection = None) -> pd.DataFrame:
    """Estimate-vs-Actual comparison (reads the v_mh_estimate_vs_actual view)."""
    _c, _owns = _conn_ctx_6a(conn)
    try:
        sql = "SELECT * FROM v_mh_estimate_vs_actual "
        params = ()
        if site_id:
            sql += "WHERE Site_ID = ? "
            params = (site_id,)
        sql += "ORDER BY Variance_Manhours DESC"
        return _localize(pd.read_sql(sql, _c, params=params))
    finally:
        if _owns:
            _c.close()


# ── Attendance workbook import (shared by the UI uploader + bootstrap CLI) ───
def _mh_norm(s) -> str:
    return str(s or "").strip().lower()


def _mh_str_code(v) -> str:
    """Employee codes arrive as int/float/str — normalise to a clean string."""
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return str(v).strip()


def _mh_iso_date(v) -> str:
    try:
        return pd.to_datetime(v).date().isoformat()
    except Exception:
        return str(v or "").strip()[:10]


def _mh_map_columns(df: "pd.DataFrame", wanted: dict) -> dict:
    by_norm = {_mh_norm(c): c for c in df.columns}
    out = {}
    for canon, accepted in wanted.items():
        for a in accepted:
            if a in by_norm:
                out[canon] = by_norm[a]
                break
    return out


def parse_attendance_workbook(source) -> dict:
    """Parse an attendance .xlsx (the to-john_Attendance format) into plain
    dicts. `source` may be a file path OR a file-like (Streamlit UploadedFile).

    Returns {"employees": [...], "timesheets": [...], "dates": [...]}. Pure
    parsing — no DB writes. Every distinct SAR worker is merged into the roster
    (ADD EMPLOYEE rows, if present, supply richer attributes). Location /
    Equipment Tag / System Code are imported as-is (usually blank → filled in
    the UI); hours are computed downstream from In/Out.
    """
    xls = pd.ExcelFile(source)

    # ADD EMPLOYEE sheet (often a header/legend only) ----------------------
    emp_rows: list[dict] = []
    if "ADD EMPLOYEE" in xls.sheet_names:
        edf = xls.parse("ADD EMPLOYEE")
        ecm = _mh_map_columns(edf, {
            "code": ["code"], "name": ["name"], "designation": ["designation"],
            "type": ["type"], "company": ["company"],
        })
        if "code" in ecm:
            for _, r in edf.iterrows():
                code = _mh_str_code(r.get(ecm["code"]))
                name = str(r.get(ecm.get("name"), "") or "").strip()
                if not code or not name:
                    continue
                wt = str(r.get(ecm.get("type"), "") or "").strip()
                emp_rows.append({
                    "code": code, "name": name,
                    "designation": str(r.get(ecm.get("designation"), "") or "").strip(),
                    "worker_type": "Supply" if wt.lower().startswith("supply") else "OWN",
                    "company": str(r.get(ecm.get("company"), "") or "").strip(),
                })

    # SAR sheet (daily attendance) -----------------------------------------
    timesheets: list[dict] = []
    if "SAR" in xls.sheet_names:
        sdf = xls.parse("SAR")
        scm = _mh_map_columns(sdf, {
            "location": ["location"],
            "equipment_tag": ["equipment tag #", "equipment tag"],
            "system_code": ["system code", "code "],  # rarely present in source
            "code": ["code"], "name": ["name"], "work_date": ["work date"],
            "in_time": ["in time"], "out_time": ["out time"],
            "status": ["status"], "remarks": ["remarks"],
        })
        # 'code' must be the EMPLOYEE code column, not a system-code column
        scm.pop("system_code", None)
        for _, r in sdf.iterrows():
            code = _mh_str_code(r.get(scm.get("code")))
            wdate = _mh_iso_date(r.get(scm.get("work_date")))
            if not code or not wdate:
                continue
            in_v = r.get(scm.get("in_time"))
            out_v = r.get(scm.get("out_time"))
            timesheets.append({
                "code": code,
                "name": str(r.get(scm.get("name"), "") or "").strip(),
                "work_date": wdate,
                "location": str(r.get(scm.get("location"), "") or "").strip(),
                "equipment_tag": str(r.get(scm.get("equipment_tag"), "") or "").strip(),
                "in_time": None if pd.isna(in_v) else in_v,
                "out_time": None if pd.isna(out_v) else out_v,
                "status": str(r.get(scm.get("status"), "") or "").strip() or "PR",
                "remarks": str(r.get(scm.get("remarks"), "") or "").strip(),
            })

    # Merge: every SAR worker becomes an employee ---------------------------
    by_code = {e["code"]: e for e in emp_rows}
    for t in timesheets:
        by_code.setdefault(t["code"], {
            "code": t["code"], "name": t["name"] or t["code"],
            "designation": "", "worker_type": "OWN", "company": "",
        })
    dates = sorted({t["work_date"] for t in timesheets})
    return {"employees": list(by_code.values()), "timesheets": timesheets,
            "dates": dates}


def import_mh_attendance(site_id: str, parsed: dict, *, replace: bool = True,
                         created_by: str = "import",
                         conn: sqlite3.Connection = None) -> "tuple[int, int]":
    """Bulk-import a parsed attendance workbook for one site.

    When replace=True, existing timesheets for this site on the dates present
    in the file are DELETED first (predictable re-import). Employees are always
    upserted. Returns (employees_loaded, timesheets_loaded).
    """
    site_id = (site_id or "").strip()
    _c, _owns = _conn_ctx_6a(conn)
    try:
        if replace and parsed.get("dates"):
            qmarks = ",".join("?" for _ in parsed["dates"])
            _c.execute(
                f"DELETE FROM mh_timesheets WHERE Site_ID=? AND Work_Date IN ({qmarks})",
                (site_id, *parsed["dates"]),
            )
            _c.commit()
        emp_n = 0
        for e in parsed.get("employees", []):
            ok, _ = upsert_mh_employee(
                site_id, e["code"], e["name"], designation=e.get("designation", ""),
                worker_type=e.get("worker_type", "OWN"), company=e.get("company", ""),
                created_by=created_by, conn=_c)
            emp_n += int(ok)
        ts_n = 0
        for t in parsed.get("timesheets", []):
            ok, _ = add_mh_timesheet(
                site_id, t["code"], t["work_date"], t.get("in_time"),
                t.get("out_time"), location=t.get("location", ""),
                equipment_tag=t.get("equipment_tag", ""), system_code="",
                status=t.get("status", "PR"), remarks=t.get("remarks", ""),
                created_by=created_by, conn=_c)
            ts_n += int(ok)
        return emp_n, ts_n
    finally:
        if _owns:
            _c.close()
