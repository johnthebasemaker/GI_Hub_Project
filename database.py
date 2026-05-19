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


# ---------------------------------------------------------------------------
# CONNECTION
# ---------------------------------------------------------------------------
def get_connection(db_file: str = None) -> sqlite3.Connection:
    """Return a SQLite connection. Pass db_file=':memory:' for in-memory testing."""
    return sqlite3.connect(db_file or DB_FILE, check_same_thread=False)


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
            role          TEXT NOT NULL CHECK(role IN ('admin','hod','supervisor','worker')),
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
    if "'hod'" not in table_sql.lower():
        # The old constraint is blocking HOD creation. Safely migrate the table.
        c.execute("PRAGMA foreign_keys=off;")
        c.execute("ALTER TABLE users RENAME TO _users_old;")
        c.execute("""
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK(role IN ('admin','hod','supervisor','worker')),
                Site_ID       TEXT DEFAULT 'HQ',
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Copy old data into the new structure
        c.execute("INSERT INTO users SELECT * FROM _users_old;")
        c.execute("DROP TABLE _users_old;")
        c.execute("PRAGMA foreign_keys=on;")        
            

    conn.commit()
    if _owns_conn:
        conn.close()


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
            "SELECT SAP_Code, Equipment_Description, UOM, Minimum_Qty "
            "FROM inventory",
            conn,
        )
    except Exception:
        inv_df = pd.read_sql("SELECT SAP_Code, Equipment_Description, UOM FROM inventory", conn)
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
    pending_df = pd.read_sql("SELECT * FROM pending_issues", conn)

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

    c.execute("DELETE FROM pending_issues")
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
        "SELECT * FROM pending_issues WHERE COALESCE(Site_ID,'HQ') = ?",
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
            SELECT r.SAP_Code, i.Equipment_Description, r.Quantity, 
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
    pr_number: str = None, expiry_date: str = None
) -> tuple[bool, str]:
    """
    Inserts a new receipt and automatically checks if the linked PR has been fulfilled.
    If the received quantity >= requested quantity, it automatically closes the PR.
    """
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO receipts (Date, SAP_Code, Quantity, Supplier, Remarks, Site_ID, Expiry_Date, PR_Number)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (date, sap_code, qty, supplier, remarks, site_id, expiry_date, pr_number))

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
