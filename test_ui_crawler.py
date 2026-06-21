"""
test_ui_crawler.py — Streamlit AppTest-based UI smoke crawler
=============================================================
Catches render-time Python exceptions (NameError, AttributeError, KeyError,
ImportError, etc.) by spinning up the Streamlit app, logging in as each of
the five roles, navigating to every page the role can reach, and asserting
that `at.exception` stays empty.

Run:
    python test_ui_crawler.py            # quiet
    python test_ui_crawler.py --verbose  # streams each page result

Exits 0 on full pass, 1 on any failure. Writes UI_CRAWLER_REPORT.md.

WHAT IT CATCHES
---------------
- NameError / AttributeError / KeyError / ImportError at render time
  (this is what the BRAND_GOLD bug class looks like)
- Schema-derived crashes (column reads against a stale dict)
- Import-time errors in any page module

WHAT IT DOES NOT CATCH (yet)
----------------------------
- Errors hidden behind `@st.dialog` modals (those need explicit button clicks)
- CSS / JS layout issues
- Errors that only fire on specific form-submit paths beyond the
  opportunistic best-effort submit attempted here
"""

from __future__ import annotations

import datetime
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Throwaway DB — set up BEFORE importing project modules (same pattern as
# bug_check.py). The Streamlit app reads database.DB_FILE at runtime, so
# patching it here keeps gi_database.db untouched.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent
TMP_ROOT  = Path(tempfile.mkdtemp(prefix="gi_uicrawler_"))
TMP_DB    = TMP_ROOT / "ui_crawler.db"
TMP_UPLOADS = TMP_ROOT / "uploads"
TMP_UPLOADS.mkdir(parents=True, exist_ok=True)

# Tell main.py NOT to spawn the WhatsApp worker thread — it would race against
# AppTest's reruns and (on macOS) hit the Cocoa-main-thread guard.
os.environ["GI_SUPPRESS_EMBEDDED_WORKER"] = "1"
# Tell draft_bus (Phase 7E) NOT to instantiate streamlit-local-storage. That
# component renders a JS iframe which AppTest doesn't drive, causing the
# script run to hang past the 30s timeout. Production behaviour unchanged.
os.environ["GI_SUPPRESS_LOCAL_STORAGE"] = "1"

import database  # noqa: E402
database.DB_FILE = str(TMP_DB)
database.UPLOADS_ROOT = str(TMP_UPLOADS)

# Now safe to import the rest of the project.
import auth  # noqa: E402  (hash_password, etc.)
from config import PAGE_ACCESS  # noqa: E402

# We import _can_access / _PAGE_BLOCKED_ROLES from main lazily so the
# crawler runs even if main.py imports break — the crawler will then
# report the import error as a fail on every role.
try:
    from main import _can_access  # noqa: E402
except Exception:
    _can_access = None


# ---------------------------------------------------------------------------
# Result registry
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def record(role: str, page: str, status: str, detail: str = "") -> None:
    RESULTS.append({
        "role": role, "page": page, "status": status, "detail": detail,
    })
    if VERBOSE:
        glyph = "✅" if status == "PASS" else ("⚠️" if status == "SKIP" else "❌")
        suffix = f"  →  {detail}" if detail else ""
        print(f"  {glyph} {role:14s} · {page}{suffix}")


# ---------------------------------------------------------------------------
# Seeding — five users, one warehouse, one site row in system_settings
# ---------------------------------------------------------------------------
ROLE_SEED = [
    # (username, role, site_id, warehouse_id)
    ("crawl_admin",     "admin",          "",      None),
    ("crawl_hod",       "hod",            "CNCEC", None),
    ("crawl_sk",        "store_keeper",   "CNCEC", None),
    ("crawl_logistics", "logistics",      "",      None),
    ("crawl_warehouse", "warehouse_user", "",      "WH-MAIN"),
]


def seed() -> None:
    """Initialise the throwaway DB with one user per role + a warehouse."""
    database.init_db()
    conn = database.get_connection()
    try:
        # Ensure the Site_ID 'CNCEC' is registered so dropdowns find it.
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (category, value) "
            "VALUES ('Site', 'CNCEC')"
        )
        # One warehouse for warehouse_user binding.
        conn.execute(
            "INSERT OR IGNORE INTO warehouses (Warehouse_ID, Name, status) "
            "VALUES ('WH-MAIN', 'Main Warehouse', 'active')"
        )
        for uname, role, site, wh in ROLE_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(username, password_hash, role, Site_ID, Phone_Number, Warehouse_ID) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uname, auth.hash_password("crawl_pw"), role, site, "+966500000000", wh),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The crawler proper
# ---------------------------------------------------------------------------
def _login_as(at, username: str, role: str, site_id: str, warehouse_id: str | None) -> None:
    """Pre-populate session_state so AppTest skips the bcrypt login form."""
    from config import ROLES
    role_meta = ROLES.get(role, {"label": role, "icon": "?"})
    at.session_state["gi_user"] = {
        "username":      username,
        "role":          role,
        "display_label": role_meta["label"],
        "icon":          role_meta["icon"],
        "site_id":       site_id,
        "warehouse_id":  warehouse_id,
    }


def _set_page(at, page_label: str) -> None:
    """Set the sidebar nav radio to a specific page label."""
    at.session_state["nav_radio"] = page_label
    # AppTest's SafeSessionState proxy only supports __setitem__ /
    # __getitem__ / __contains__. Use `in` to test presence.
    if "_wh_admin_shadow_wh" not in at.session_state:
        at.session_state["_wh_admin_shadow_wh"] = "WH-MAIN"


def _exception_summary(exc_list) -> str:
    """Compact human-readable summary of at.exception list."""
    if not exc_list:
        return ""
    parts = []
    for e in exc_list:
        # AppTest's exception entries have .value (string) attribute
        msg = getattr(e, "value", str(e))
        parts.append(msg.splitlines()[0] if msg else "<unknown>")
    return "  ||  ".join(parts)


def _opportunistic_form_submit(at) -> str | None:
    """Best-effort: fill any text_input with "x" and click the first
    form_submit_button. Returns a one-line error summary if the submit
    surfaced an exception, else None. Silently no-ops if no widgets exist."""
    try:
        # Fill widgets that accept text
        for ti in list(at.text_input):
            try:
                if not ti.value:
                    ti.set_value("x")
            except Exception:
                pass
        # Click first submit button
        submit_btns = list(at.button) + (list(getattr(at, "form_submit_button", [])) if hasattr(at, "form_submit_button") else [])
        # AppTest 1.x: form submit buttons surface under .button as well.
        if submit_btns:
            try:
                submit_btns[0].click()
                at.run(timeout=30)
                if at.exception:
                    return _exception_summary(at.exception)
            except Exception as e:
                return f"form-submit raised: {type(e).__name__}: {e}"
    except Exception as e:
        return f"opportunistic submit walker raised: {type(e).__name__}: {e}"
    return None


def crawl_role(uname: str, role: str, site_id: str, warehouse_id: str | None) -> None:
    """Visit every page the role can reach and check at.exception."""
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as e:
        record(role, "(import AppTest)", "FAIL",
               f"streamlit.testing.v1 unavailable: {e}")
        return

    if _can_access is None:
        record(role, "(import main)", "FAIL", "main.py failed to import")
        return

    # Pages this role is supposed to be able to reach.
    from main import _PAGE_BLOCKED_ROLES  # picked up via the same import path
    visible_pages = [p for p in PAGE_ACCESS if _can_access(role, p)]
    if not visible_pages:
        record(role, "(no visible pages)", "SKIP",
               "role has zero pages — check RBAC config")
        return

    # Also assert each blocked page is INDEED inaccessible.
    for page, blocked_roles in _PAGE_BLOCKED_ROLES.items():
        if role in blocked_roles:
            if _can_access(role, page):
                record(role, page, "FAIL",
                       "role should be blocked but _can_access returned True")
            else:
                record(role, f"{page} (blocked)", "PASS", "")

    for page in visible_pages:
        try:
            at = AppTest.from_file(str(REPO_ROOT / "main.py"), default_timeout=30)
            _login_as(at, uname, role, site_id, warehouse_id)
            _set_page(at, page)
            at.run()

            if at.exception:
                record(role, page, "FAIL", _exception_summary(at.exception))
                continue

            # Opportunistic form-submit attempt (best-effort).
            sub_err = _opportunistic_form_submit(at)
            if sub_err:
                record(role, f"{page} (form-submit)", "FAIL", sub_err)
                continue

            record(role, page, "PASS", "")
        except Exception as e:
            # Crawler itself blew up (not the app) — log and continue.
            tb_last = traceback.format_exc().strip().splitlines()[-1]
            record(role, page, "FAIL", f"crawler error: {tb_last}")


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def write_report() -> Path:
    out = REPO_ROOT / "UI_CRAWLER_REPORT.md"
    pass_n = sum(1 for r in RESULTS if r["status"] == "PASS")
    fail_n = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skip_n = sum(1 for r in RESULTS if r["status"] == "SKIP")
    lines = [
        f"# UI Crawler Report",
        f"_Generated {datetime.datetime.now().isoformat(timespec='seconds')}_",
        "",
        f"**{pass_n} passed · {fail_n} failed · {skip_n} skipped**",
        "",
        "| Role | Page | Status | Detail |",
        "|---|---|---|---|",
    ]
    for r in RESULTS:
        glyph = "✅" if r["status"] == "PASS" else ("⚠️" if r["status"] == "SKIP" else "❌")
        lines.append(f"| {r['role']} | {r['page']} | {glyph} {r['status']} | {r['detail'].replace('|', '\\|')} |")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"▶ UI crawler · DB → {TMP_DB}")
    try:
        seed()
    except Exception as e:
        print(f"✖ Seeding crashed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    for uname, role, site, wh in ROLE_SEED:
        if VERBOSE:
            print(f"\n── Role: {role} ({uname}) ──")
        crawl_role(uname, role, site, wh)

    out = write_report()
    print()
    fail_n = sum(1 for r in RESULTS if r["status"] == "FAIL")
    pass_n = sum(1 for r in RESULTS if r["status"] == "PASS")
    skip_n = sum(1 for r in RESULTS if r["status"] == "SKIP")
    print(f"▶ {pass_n} passed, {fail_n} failed, {skip_n} skipped")
    print(f"▶ Report: {out.relative_to(REPO_ROOT)}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        try:
            shutil.rmtree(TMP_ROOT, ignore_errors=True)
        except Exception:
            pass
    sys.exit(rc)
