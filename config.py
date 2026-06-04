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
    "admin":      {"label": "Admin",              "icon": "👑",  "color": BRAND_GOLD},
    "hod":        {"label": "Head of Department", "icon": "🏛️", "color": "#6366F1"},
    "supervisor": {"label": "Supervisor",         "icon": "🛡️", "color": BRAND_BLUE_LIGHT},
    "worker":     {"label": "Floor Worker",       "icon": "👷",  "color": TEXT_MUTED},
}

# worker=0 < supervisor=1 < hod=2 < admin=3
# All existing relative-ordering tests remain valid after this shift.
ROLE_HIERARCHY = {"worker": 0, "supervisor": 1, "hod": 2, "admin": 3}

# Minimum role required to VIEW each page
PAGE_ACCESS = {
    "📦 Live Dashboard":  "supervisor",
    "📝 Daily Issue Log": "worker",
    "📋 HOD Portal":      "hod",          # HOD + Admin; EOD Commit lives here
    "🛡️ Admin Portal":    "admin",
    "📊 Reports":         "supervisor",
}

# Cross-site request status FSM: pending → approved|rejected → fulfilled
REQUEST_STATUSES = ["pending", "approved", "rejected", "fulfilled"]
DEFAULT_SITE     = "HQ"   # Site assigned to all legacy rows on schema upgrade

# ---------------------------------------------------------------------------
# COLUMN CONSTANTS
# ---------------------------------------------------------------------------
SYSTEM_COLS         = {"id", "Timestamp", "created_at", "Site_ID", "status"}
EXTENDED_ISSUE_COLS = ["Date", "Issued_By", "Issued_To", "Tank_No", "Serial_No", "PR_Number"]
OPTIONAL_ISSUE_COLS = {"Remarks", "Serial_No", "PR_Number"}

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
# LEGACY — removed in Module 3
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = "admin2026"
