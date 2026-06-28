"""
config.py — General Industries Lightning Hub v2.0
==================================================
Single source of truth for all application-level constants.
Import from here in all modules — never hardcode values elsewhere.
"""

# ---------------------------------------------------------------------------
# APPLICATION IDENTITY
# ---------------------------------------------------------------------------
APP_NAME     = "General Industries Lightning Hub"
APP_SUBTITLE = "Enterprise Inventory Management"
APP_ICON     = "⚡"
APP_VERSION  = "2.0.0"
DB_FILE      = "gi_database.db"

# ---------------------------------------------------------------------------
# BRAND COLORS — General Industries Corporate Identity
# ---------------------------------------------------------------------------
BRAND_BLUE       = "#003366"   # Deep Navy Blue — primary
BRAND_GOLD       = "#D4AF37"   # Corporate Gold  — accent
BRAND_BLUE_LIGHT = "#1A4D80"   # Interactive / hover states
BRAND_GOLD_LIGHT = "#F0D060"   # Subtle highlights
BRAND_BLUE_DARK  = "#001F40"   # Pressed / active states

# Dark Mode Surfaces
DARK_BG        = "#0A1628"   # App background
DARK_SURFACE   = "#162038"   # Card / panel
DARK_SURFACE_2 = "#1E3050"   # Elevated / nested card
DARK_BORDER    = "#2A4060"   # Dividers

# Text
TEXT_PRIMARY   = "#F0F4F8"
TEXT_SECONDARY = "#C0CCD8"
TEXT_MUTED     = "#7A8FA0"

# Semantic Status
COLOR_OK       = "#22C55E"   # Adequate stock
COLOR_LOW      = "#F59E0B"   # Low stock
COLOR_CRITICAL = "#EF4444"   # Empty / critical

# ---------------------------------------------------------------------------
# ROLE DEFINITIONS
# ---------------------------------------------------------------------------
ROLES = {
    "admin":          {"label": "Admin",              "icon": "👑",  "color": BRAND_GOLD},
    "logistics":      {"label": "Logistics",          "icon": "🚚",  "color": "#0EA5E9"},
    "hod":            {"label": "Head of Department", "icon": "🏛️", "color": "#6366F1"},
    "warehouse_user": {"label": "Warehouse",          "icon": "🏭",  "color": "#10B981"},
    "supervisor":     {"label": "Supervisor",         "icon": "🛡️", "color": BRAND_BLUE_LIGHT},
    "store_keeper":   {"label": "Store Keeper",       "icon": "🗝️",  "color": TEXT_MUTED},
}

# Hierarchy is used by `_can_access()` for cascading visibility. The new
# procurement roles (logistics, warehouse_user) are parallel ladders — they
# do not inherit Site-scoped pages. _EXACT_ROLE_PAGES in main.py locks each
# procurement page to its exact role so a HOD cannot see Logistics Portal
# just because they're "above" warehouse_user numerically.
ROLE_HIERARCHY = {
    "store_keeper":   0,
    "warehouse_user": 1,
    "supervisor":     1,
    "hod":            2,
    "logistics":      3,
    "admin":          4,
}

# Minimum role required to VIEW each page
PAGE_ACCESS = {
    "📦 Live Dashboard":      "supervisor",
    "📝 Entry Log":           "store_keeper",
    "📋 HOD Portal":          "hod",            # HOD + Admin; EOD Commit lives here
    "🛡️ Supervisor Portal":  "supervisor",     # Phase 7B — exact-locked in main.py
    "🚚 Logistics Portal":    "logistics",      # exact-locked in main.py (admin shadow allowed)
    "🏭 Warehouse Portal":    "warehouse_user", # exact-locked in main.py (admin shadow allowed)
    "🛡️ Admin Portal":        "admin",
    "📊 Reports":             "supervisor",
    "🧪 Material Estimator":  "hod",            # Round 17 — exact-locked to {hod, admin}
    "🕒 Man-Hours":           "hod",            # §2Z — exact-locked to {hod, admin}
}

# Cross-site request status FSM: pending → approved|rejected → fulfilled
REQUEST_STATUSES = ["pending", "approved", "rejected", "fulfilled"]
DEFAULT_SITE     = "HQ"   # Site assigned to all legacy rows on schema upgrade

# ---------------------------------------------------------------------------
# COLUMN CONSTANTS
# ---------------------------------------------------------------------------
SYSTEM_COLS         = {"id", "Timestamp", "created_at", "Site_ID", "status"}
# EXTENDED_ISSUE_COLS — Round 12: dropped 'Issued_By' (now auto-filled
# server-side from the logged-in user) and 'Technician' (retired field).
EXTENDED_ISSUE_COLS = ["Date", "Issued_To", "Tank_No", "Serial_No", "PR_Number"]
OPTIONAL_ISSUE_COLS: set[str] = set()  # All entry fields are mandatory (2026-06).

# HIDDEN_FORM_COLS — single source of truth for "do not render this column
# as a manual form field, even if it exists on pending_issues / receipts /
# consumption schemas". Round 12:
#   - Technician           (legacy column, kept in DB, no longer collected)
#   - Issued_By            (auto-filled with SK username server-side)
#   - "Approved By"        (legacy space-named column, HOD username at commit)
#   - Approved_By          (defensive — alt spelling)
#   - Requested_By         (auto-filled with supervisor username for SMR rows)
#   - Source_Ref           (system bookkeeping, e.g. SMR:<request_no>:<id>)
#   - FEFO_Override        (set via dedicated FEFO override expander only)
#   - Lot_Number           (set via FEFO auto-pick / override flow)
HIDDEN_FORM_COLS = {
    "Technician",
    "Issued_By",
    "Approved By",
    "Approved_By",
    "Requested_By",
    "Source_Ref",
    "FEFO_Override",
    "Lot_Number",
}

# CONSUMPTION_EXPORT_COLS — canonical column list for the Admin Master DB
# Editor's "Export as PDF" path on the consumption table. Each tuple is
# (db_col, display_label). The export joins inventory for Material_Code +
# Equipment_Description + UOM (those are NOT stored on the consumption row).
# Excludes legacy / system columns: Technician, status, FEFO_Override,
# Source_Ref, the bogus `Approved` (with type "By TEXT") legacy column.
# Round 13.
CONSUMPTION_EXPORT_COLS = [
    ("Date",                  "Date"),
    ("SAP_Code",              "SAP Code"),
    ("Material_Code",         "Material Code"),       # joined from inventory
    ("Equipment_Description", "Material"),            # joined from inventory
    ("UOM",                   "UOM"),                 # joined from inventory
    ("Quantity",              "Quantity"),
    ("Work_Type",             "Work Type"),
    ("PR_Number",             "PR Number"),
    ("Tank_No",               "Tank No"),
    ("Serial_No",             "Serial No"),
    ("Lot_Number",            "Lot Number"),
    ("Issued_By",             "Issued By"),
    ("Issued_To",             "Issued To"),
    ("Requested_By",          "Requested By"),
    ("Approved By",           "Approved By"),         # legacy space-named col
    ("Remarks",               "Remarks"),
    ("Site_ID",               "Site"),
]

# Material categories — drive category-filtered reports and the MTC workflow.
# Items in MTC_REQUIRED_CATEGORY are forced to supply a Material Test
# Certificate (MTC) doc + number on receipt staging.
MATERIAL_CATEGORIES = [
    "Consumable", "Equipments", "Utilities", "Maintenance",
    "Others", "Surface Shields", "Tools", "QC items",
]
MTC_REQUIRED_CATEGORY = "Surface Shields"
# Back-compat alias — older code paths still reference RUBBER_CATEGORY.
RUBBER_CATEGORY = MTC_REQUIRED_CATEGORY

# ---------------------------------------------------------------------------
# RL / BL strict separation (procurement)
# ---------------------------------------------------------------------------
# Rubber Lining and Brick Lining items must NEVER be aggregated with each
# other or with anything else in PO splitting, DN preparation, or warehouse
# receipt calculations. The match is substring + case-insensitive against
# Material_Code OR Equipment_Description — if either contains one of these
# tokens, the line is tagged with that family and the splitter forces it
# into its own DN/PO group.
RL_BL_FAMILY_TOKENS = {
    "RL": ("RL-", "RUBBER LINING", "RUBBER-LINING"),
    "BL": ("BL-", "BRICK LINING", "BRICK-LINING", "BRICK MATERIAL"),
}

def classify_rl_bl_family(material_code: str, description: str) -> str | None:
    """Return 'RL', 'BL', or None. Used by the PO/DN splitter to enforce
    strict separation — RL and BL families never share a DN or PO group."""
    blob = f"{material_code or ''} {description or ''}".upper()
    for family, tokens in RL_BL_FAMILY_TOKENS.items():
        if any(tok in blob for tok in tokens):
            return family
    return None

# ---------------------------------------------------------------------------
# WhatsApp triggers — easy on/off per procurement event
# ---------------------------------------------------------------------------
# Every procurement-flow event funnels through `_fire_whatsapp(event_key, ...)`
# in database.py, which consults this dict. Flip a value to False to silence
# WhatsApp for that event; in-app notifications still fire. App-level toggle
# `WHATSAPP_ENABLED` is the master switch (False = all WhatsApp off, in-app only).
WHATSAPP_ENABLED = True

WHATSAPP_TRIGGERS = {
    # PR → Logistics
    "pr_submitted_to_logistics":   True,   # site HOD → Logistics
    "pr_force_closed":             True,   # Logistics → Admin + originating Site HOD
    # PO → Site / Warehouse
    "po_issued":                   True,   # Logistics → Site HOD
    "po_assigned_to_warehouse":    True,   # Logistics → Warehouse lead
    "po_force_closed":             True,   # Logistics → Admin + Site HOD
    # Warehouse ↔ Logistics
    "warehouse_acknowledged":      False,  # Warehouse → Logistics (low value, off by default)
    "warehouse_received":          True,   # Warehouse → Logistics
    # DN flow
    "dn_logistics_approved":       True,   # Logistics → Site HOD
    "dn_auto_generated":           True,   # Warehouse lead
    "dn_received_by_sk":           False,  # Site SK confirms → optional ping
    # Reschedule + Returns
    "reschedule_requested":        True,   # Warehouse / Site HOD → Logistics
    "reschedule_decided":          True,   # Logistics → requester
    "vendor_return_raised":        True,   # → Logistics
    # Delivery reminders
    "delivery_reminder_t_minus_2": True,
    "delivery_reminder_t_minus_1": True,
    "delivery_reminder_t_zero":    True,
    # Phase 6E — Returnable-loan reminders (hourly sweep, signed-hour offsets)
    "returnable_reminder_t_minus_2h": True,   # info     — 2h before due (borrower)
    "returnable_reminder_t_zero":     True,   # warning  — due now (borrower)
    "returnable_reminder_t_plus_2h":  True,   # warning  — 2h overdue (borrower + site SK)
    "returnable_reminder_t_plus_24h": True,   # critical — 24h overdue (+ site supervisor)
    # Phase 7B — Supervisor Material Request workflow
    "smr_submitted": True,   # supervisor → site SKs
    "smr_approved":  True,   # SK → site HOD + originating supervisor
    "smr_rejected":  True,   # SK → originating supervisor
    "smr_cancelled": True,   # supervisor → site SKs (request withdrawn)
    # Phase 7C — HOD Cross-Site view notification (debounced once-per-day)
    # Default OFF — in-app notification + bell badge is sufficient. Flip to
    # True if HODs explicitly want a WhatsApp ping per first-of-day view.
    "cross_site_viewed": False,
}

# Allowed attachment MIME suffixes (PDF, JPEG, JPG, XLSX per 2026-06 spec).
ATTACHMENT_ALLOWED = ("pdf", "jpeg", "jpg", "xlsx")

# ---------------------------------------------------------------------------
# CHART SETTINGS
# ---------------------------------------------------------------------------
CHART_COLORS = [
    BRAND_GOLD, BRAND_BLUE_LIGHT, COLOR_OK,
    COLOR_LOW, COLOR_CRITICAL, "#2E7D8C", "#8B3A62", "#4A90D9",
]

STOCK_STATUS_OK       = "✅ Adequate"
STOCK_STATUS_LOW      = "⚠️ Low Stock"
STOCK_STATUS_CRITICAL = "🔴 Critical/Empty"
PLOTLY_TEMPLATE       = "plotly_dark"

# ---------------------------------------------------------------------------
# AGGRID DEFAULTS
# ---------------------------------------------------------------------------
AGGRID_HEIGHT    = 450   # px
AGGRID_PAGE_SIZE = 25
AGGRID_THEME     = "streamlit"

# ---------------------------------------------------------------------------
# AI FEATURES (Phase 3) — optional local Ollama integration
# ---------------------------------------------------------------------------
# Toggle off if Ollama isn't installed or you want to deploy without AI.
# When False, AI panels render a small "AI disabled in settings" hint instead
# of calling the local server.
AI_ENABLED = True

# ---------------------------------------------------------------------------
# LEGACY — removed in Module 3
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = "admin2026"

# ---------------------------------------------------------------------------
# TIMEZONE — display offset vs UTC
# ---------------------------------------------------------------------------
# DB timestamps default to SQLite CURRENT_TIMESTAMP (UTC). The launchd plists
# set TZ=Asia/Riyadh so Python datetime.now() returns local time, but rows
# already written via DEFAULT CURRENT_TIMESTAMP remain UTC. Helpers below
# convert at display time.
import datetime as _dt
import os as _os

TZ_NAME = _os.environ.get("TZ", "Asia/Riyadh")
TZ_OFFSET_HOURS = int(_os.environ.get("GI_TZ_OFFSET_HOURS", "3"))


def utc_to_local(value, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Convert a single UTC timestamp (string from SQLite or datetime) into a
    formatted local-time string. Empty / nan / None / "—" pass through as "—".
    """
    if value is None:
        return "—"
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "nat", "—"):
        return "—"
    if isinstance(value, _dt.datetime):
        t = value
    else:
        try:
            t = _dt.datetime.fromisoformat(s.replace(" ", "T"))
        except (ValueError, TypeError):
            return s  # unparseable — show as-is rather than break the UI
    t = t + _dt.timedelta(hours=TZ_OFFSET_HOURS)
    return t.strftime(fmt)


def localize_timestamps_df(df, columns: list[str], fmt: str = "%Y-%m-%d %H:%M:%S"):
    """
    Apply utc_to_local to every named column in a DataFrame. Missing columns
    are silently skipped so callers don't crash on schema drift. Returns the
    same DataFrame for chaining.
    """
    if df is None or len(df) == 0:
        return df
    for col in columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: utc_to_local(v, fmt))
    return df


# Canonical timestamp column names across the whole project. Any column
# matching one of these (case-sensitive) is treated as a UTC timestamp
# eligible for auto-localization on display. Extend this set rather than
# adding new ad-hoc names in queries.
_DEFAULT_TS_COLS = (
    # snake_case (most of the new tables)
    "created_at", "updated_at", "sent_at", "approved_at",
    "requested_at", "submitted_at", "uploaded_at",
    "logistics_emailed_at", "received_at", "rejected_at",
    "last_used_at", "last_run", "generated_at",
    "given_time", "expected_return_time",
    "delivered_at", "reviewed_at",
    # PascalCase / lowercase (legacy SQLite default-column names)
    "Timestamp", "timestamp",
)


def auto_localize_timestamps(df, extra_cols: list[str] = None,
                             fmt: str = "%Y-%m-%d %H:%M:%S"):
    """
    Detect timestamp columns by name in `df` and convert each to GMT+3 strings.

    Recognised columns: every name in `_DEFAULT_TS_COLS` plus anything passed
    in `extra_cols`. Anything else is left alone. Idempotent — already-formatted
    timestamps pass through unchanged because `utc_to_local` returns the input
    string when it can't be parsed as ISO8601.

    Use this in any tab that does `pd.read_sql(...)` to avoid hand-listing
    columns. For one-off custom column names, call `localize_timestamps_df`
    directly.
    """
    if df is None or len(df) == 0:
        return df
    targets = set(_DEFAULT_TS_COLS) | set(extra_cols or [])
    cols_present = [c for c in df.columns if c in targets]
    if not cols_present:
        return df
    return localize_timestamps_df(df, cols_present, fmt)
