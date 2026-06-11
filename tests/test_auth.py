"""
test_auth.py — Authentication, RBAC, and User Management tests
===============================================================
Tests every Streamlit-free function in auth.py:
  - bcrypt password hashing & verification
  - authenticate_user() credential checking
  - seed_default_users() idempotency
  - add_user() / delete_user() / reset_password() CRUD
  - Role hierarchy and page-access permission matrix
  - Last-admin lockout guard
  - Low-stock alert trigger (integration with database.py)

NOTE: login_form(), get_current_user(), logout() depend on Streamlit
session_state and are NOT tested here — they are verified manually.
"""

import sqlite3
import pandas as pd
import pytest

from auth import (
    hash_password, verify_password,
    authenticate_user, seed_default_users,
    get_all_users, add_user, delete_user, reset_password,
)
from config import ROLE_HIERARCHY, PAGE_ACCESS, ROLES
from database import init_db


# ===========================================================================
# GROUP 1: Password Hashing
# ===========================================================================

class TestPasswordHashing:
    """Validates bcrypt hash/verify behaviour."""

    def test_hash_returns_non_empty_string(self):
        h = hash_password("password123")
        assert isinstance(h, str) and len(h) > 0

    def test_correct_password_verifies_true(self):
        h = hash_password("secret")
        assert verify_password("secret", h) is True

    def test_wrong_password_verifies_false(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_empty_password_does_not_match_non_empty_hash(self):
        h = hash_password("notempty")
        assert verify_password("", h) is False

    def test_hash_does_not_contain_plaintext(self):
        plain = "supersecret123"
        h = hash_password(plain)
        assert plain not in h

    def test_bcrypt_uses_random_salt(self):
        """Same plaintext → different hashes each call (random salt)."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        # But both verify correctly
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True

    def test_verify_handles_corrupt_hash_gracefully(self):
        """Corrupted hash string must not raise — return False instead."""
        assert verify_password("anypassword", "not_a_valid_bcrypt_hash") is False

    def test_hash_starts_with_bcrypt_prefix(self):
        """All valid bcrypt hashes begin with $2b$ or $2a$."""
        h = hash_password("test")
        assert h.startswith(("$2b$", "$2a$"))


# ===========================================================================
# GROUP 2: authenticate_user()
# ===========================================================================

class TestAuthenticateUser:
    """Validates the authenticate_user() login function."""

    def test_correct_credentials_return_user_dict(self, db_conn):
        add_user("alice", "pass123", "store_keeper", "HQ", db_conn)
        result = authenticate_user("alice", "pass123", db_conn)
        assert result is not None
        assert result["username"] == "alice"
        assert result["role"] == "store_keeper"

    def test_wrong_password_returns_none(self, db_conn):
        add_user("bob", "correct", "supervisor", "HQ", db_conn)
        assert authenticate_user("bob", "wrong", db_conn) is None

    def test_nonexistent_user_returns_none(self, db_conn):
        assert authenticate_user("nobody", "anything", db_conn) is None

    def test_result_contains_all_expected_keys(self, db_conn):
        add_user("carol", "mypass", "admin", "HQ", db_conn)
        result = authenticate_user("carol", "mypass", db_conn)
        assert result is not None
        for key in ("username", "role", "display_label", "icon", "site_id"):
            assert key in result, f"Missing key '{key}' in auth result"

    def test_role_is_returned_correctly(self, db_conn):
        add_user("dave_sup", "pass", "supervisor", "HQ", db_conn)
        result = authenticate_user("dave_sup", "pass", db_conn)
        assert result["role"] == "supervisor"

    def test_username_is_case_sensitive(self, db_conn):
        """Usernames are case-sensitive — 'Alice' ≠ 'alice'."""
        add_user("CaseSensitive", "pass", "store_keeper", "HQ", db_conn)
        assert authenticate_user("casesensitive", "pass", db_conn) is None

    def test_empty_username_returns_none(self, db_conn):
        assert authenticate_user("", "anything", db_conn) is None

    def test_display_label_matches_config(self, db_conn):
        add_user("supertest", "pass", "supervisor", "HQ", db_conn)
        result = authenticate_user("supertest", "pass", db_conn)
        assert result["display_label"] == ROLES["supervisor"]["label"]


# ===========================================================================
# GROUP 3: seed_default_users()
# ===========================================================================

class TestSeedDefaultUsers:
    """Verifies the default user seeding is correct and idempotent."""

    def test_seed_creates_four_users(self, db_conn):
        """Module 5: admin + hod + supervisor + worker = 4 default users."""
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT * FROM users", db_conn)
        assert len(df) == 4

    def test_seed_creates_admin_user(self, db_conn):
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT username FROM users WHERE role='admin'", db_conn)
        assert "admin" in df["username"].values

    def test_seed_creates_supervisor_user(self, db_conn):
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT username FROM users WHERE role='supervisor'", db_conn)
        assert "supervisor" in df["username"].values

    def test_seed_creates_worker_user(self, db_conn):
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT username FROM users WHERE role='store_keeper'", db_conn)
        assert "worker" in df["username"].values

    def test_seed_is_idempotent_called_twice(self, db_conn):
        seed_default_users(db_conn)
        seed_default_users(db_conn)   # second call
        df = pd.read_sql("SELECT * FROM users", db_conn)
        assert len(df) == 4, f"Expected 4 users, got {len(df)} after double seed"

    def test_default_admin_password_authenticates(self, db_conn):
        seed_default_users(db_conn)
        result = authenticate_user("admin", "admin2026", db_conn)
        assert result is not None and result["role"] == "admin"

    def test_default_supervisor_password_authenticates(self, db_conn):
        seed_default_users(db_conn)
        result = authenticate_user("supervisor", "super2026", db_conn)
        assert result is not None and result["role"] == "supervisor"

    def test_default_worker_password_authenticates(self, db_conn):
        seed_default_users(db_conn)
        result = authenticate_user("worker", "floor2026", db_conn)
        assert result is not None and result["role"] == "store_keeper"

    def test_seed_does_not_run_if_users_exist(self, db_conn):
        """Seed must check table emptiness — existing users are not overwritten."""
        add_user("existing_admin", "mypass", "admin", "HQ", db_conn)
        seed_default_users(db_conn)   # should be a no-op
        df = pd.read_sql("SELECT * FROM users", db_conn)
        # Only 1 user (ours), not 5 (ours + 4 defaults)
        assert len(df) == 1


# ===========================================================================
# GROUP 4: User CRUD Operations
# ===========================================================================

class TestUserCRUD:
    """Tests add_user / delete_user / reset_password / get_all_users."""

    def test_add_user_returns_true_on_success(self, db_conn):
        assert add_user("newuser", "pass123", "store_keeper", "HQ", db_conn) is True

    def test_added_user_appears_in_get_all_users(self, db_conn):
        add_user("visible_user", "pass", "supervisor", "HQ", db_conn)
        df = get_all_users(db_conn)
        assert "visible_user" in df["username"].values

    def test_add_duplicate_username_returns_false(self, db_conn):
        add_user("dupuser", "pass1", "store_keeper", "HQ", db_conn)
        assert add_user("dupuser", "pass2", "admin", "HQ", db_conn) is False

    def test_add_user_invalid_role_raises_value_error(self, db_conn):
        with pytest.raises(ValueError, match="Invalid role"):
            add_user("baduser", "pass", "god_mode", "HQ", db_conn)

    def test_delete_user_returns_true_on_success(self, db_conn):
        add_user("to_delete", "pass", "store_keeper", "HQ", db_conn)
        assert delete_user("to_delete", db_conn) is True

    def test_deleted_user_not_in_get_all_users(self, db_conn):
        add_user("gone_user", "pass", "store_keeper", "HQ", db_conn)
        delete_user("gone_user", db_conn)
        df = get_all_users(db_conn)
        assert "gone_user" not in df["username"].values

    def test_delete_nonexistent_user_returns_false(self, db_conn):
        assert delete_user("phantom_user", db_conn) is False

    def test_reset_password_returns_true_on_success(self, db_conn):
        add_user("resetme", "oldpass", "store_keeper", "HQ", db_conn)
        assert reset_password("resetme", "newpass", db_conn) is True

    def test_new_password_authenticates_after_reset(self, db_conn):
        add_user("pwdchange", "original", "supervisor", "HQ", db_conn)
        reset_password("pwdchange", "updated", db_conn)
        assert authenticate_user("pwdchange", "updated", db_conn) is not None

    def test_old_password_fails_after_reset(self, db_conn):
        add_user("strictreset", "oldpwd", "store_keeper", "HQ", db_conn)
        reset_password("strictreset", "newpwd", db_conn)
        assert authenticate_user("strictreset", "oldpwd", db_conn) is None

    def test_reset_nonexistent_user_returns_false(self, db_conn):
        assert reset_password("nobody_here", "anypass", db_conn) is False

    def test_get_all_users_excludes_password_hash(self, db_conn):
        add_user("safe_user", "pass", "store_keeper", "HQ", db_conn)
        df = get_all_users(db_conn)
        assert "password_hash" not in df.columns

    def test_get_all_users_returns_dataframe(self, db_conn):
        df = get_all_users(db_conn)
        assert isinstance(df, pd.DataFrame)


# ===========================================================================
# GROUP 5: Last-Admin Lockout Guard
# ===========================================================================

class TestLastAdminGuard:
    """
    delete_user() must refuse to delete the last remaining admin account
    to prevent complete system lockout.
    """

    def test_cannot_delete_last_admin(self, db_conn):
        add_user("only_admin", "pass", "admin", "HQ", db_conn)
        result = delete_user("only_admin", db_conn)
        assert result is False, "Should not be able to delete the only admin"

    def test_can_delete_admin_when_another_exists(self, db_conn):
        add_user("admin_one", "pass", "admin", "HQ", db_conn)
        add_user("admin_two", "pass", "admin", "HQ", db_conn)
        result = delete_user("admin_one", db_conn)
        assert result is True, "Should be able to delete admin when another exists"

    def test_can_delete_non_admin_regardless(self, db_conn):
        """Workers and supervisors can always be deleted (no lockout risk)."""
        add_user("spare_admin",  "pass", "admin",      "HQ", db_conn)
        add_user("a_supervisor", "pass", "supervisor", "HQ", db_conn)
        add_user("a_worker",     "pass", "store_keeper", "HQ", db_conn)
        assert delete_user("a_supervisor", db_conn) is True
        assert delete_user("a_worker",     db_conn) is True


# ===========================================================================
# GROUP 6: Role Permission Matrix
# ===========================================================================

class TestRolePermissionMatrix:
    """
    Validates the agreed RBAC permission matrix from the architecture plan.
    These are pure config tests — no DB required.
    """

    def test_admin_outranks_supervisor(self):
        assert ROLE_HIERARCHY["admin"] > ROLE_HIERARCHY["supervisor"]

    def test_supervisor_outranks_worker(self):
        assert ROLE_HIERARCHY["supervisor"] > ROLE_HIERARCHY["store_keeper"]

    def test_admin_can_access_all_pages(self):
        for page, min_role in PAGE_ACCESS.items():
            assert ROLE_HIERARCHY["admin"] >= ROLE_HIERARCHY[min_role], (
                f"Admin should be able to access '{page}'"
            )

    def test_worker_can_only_access_daily_issue_log(self):
        worker_level = ROLE_HIERARCHY["store_keeper"]
        for page, min_role in PAGE_ACCESS.items():
            required = ROLE_HIERARCHY[min_role]
            if page == "📝 Entry Log":
                assert worker_level >= required, f"Worker must access '{page}'"
            else:
                assert worker_level < required, (
                    f"Worker should NOT access '{page}' but the role level allows it"
                )

    def test_supervisor_can_access_dashboard(self):
        sup = ROLE_HIERARCHY["supervisor"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["📦 Live Dashboard"]]
        assert sup >= req

    def test_supervisor_cannot_access_admin_portal(self):
        sup = ROLE_HIERARCHY["supervisor"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["🛡️ Admin Portal"]]
        assert sup < req

    def test_supervisor_can_access_reports_for_download(self):
        """
        Module 4 decision: supervisors can access the Reports page to download
        Excel files. Email delivery is gated inside the page itself (admin-only),
        NOT at the PAGE_ACCESS level.
        """
        sup = ROLE_HIERARCHY["supervisor"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["📊 Reports"]]
        assert sup >= req, (
            "Supervisors must be able to reach the Reports page "
            "(email section is gated admin-only inside the page)"
        )

    def test_all_roles_defined_in_roles_config(self):
        for role in ROLE_HIERARCHY:
            assert role in ROLES, f"Role '{role}' missing from ROLES config"

    def test_all_page_access_roles_are_valid(self):
        for page, min_role in PAGE_ACCESS.items():
            assert min_role in ROLE_HIERARCHY, (
                f"Page '{page}' requires role '{min_role}' which is not in ROLE_HIERARCHY"
            )


# ===========================================================================
# GROUP 7: Low-Stock Alert Trigger (auth→database integration)
# ===========================================================================

class TestLowStockAlertTrigger:
    """
    Verifies that the get_low_stock_items() function that drives the
    sidebar badge produces correct results. Tests the trigger conditions
    that supervisors and admins see.
    """

    def _seed_item(self, conn, sap, desc="Item", min_qty=10):
        conn.execute(
            "INSERT OR IGNORE INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
            "VALUES (?, ?, 'PCS', ?)",
            (sap, desc, min_qty),
        )
        conn.commit()

    def test_adequate_stock_produces_empty_low_stock_df(self, db_conn):
        from database import get_low_stock_items
        self._seed_item(db_conn, "ALERT-OK", min_qty=5)
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES ('2026-01-01','ALERT-OK',50)"
        )
        db_conn.commit()
        low = get_low_stock_items(db_conn)
        assert "ALERT-OK" not in (low["SAP_Code"].values if not low.empty else [])

    def test_low_stock_item_triggers_alert(self, db_conn):
        from database import get_low_stock_items
        self._seed_item(db_conn, "ALERT-LOW", min_qty=20)
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES ('2026-01-01','ALERT-LOW',5)"
        )
        db_conn.commit()
        low = get_low_stock_items(db_conn)
        assert not low.empty
        assert "ALERT-LOW" in low["SAP_Code"].values

    def test_zero_stock_triggers_alert(self, db_conn):
        from database import get_low_stock_items
        self._seed_item(db_conn, "ALERT-ZERO", min_qty=1)
        # No receipts — stock = 0
        low = get_low_stock_items(db_conn)
        assert "ALERT-ZERO" in (low["SAP_Code"].values if not low.empty else [])

    def test_shortage_value_is_correct(self, db_conn):
        from database import get_low_stock_items
        self._seed_item(db_conn, "ALERT-GAP", min_qty=50)
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES ('2026-01-01','ALERT-GAP',30)"
        )
        db_conn.commit()
        low = get_low_stock_items(db_conn)
        row = low[low["SAP_Code"] == "ALERT-GAP"]
        assert float(row.iloc[0]["Shortage"]) == 20.0  # 50 - 30

    def test_multiple_items_only_low_ones_flagged(self, db_conn):
        from database import get_low_stock_items
        # OK item
        self._seed_item(db_conn, "MIX-OK",  min_qty=5)
        db_conn.execute("INSERT INTO receipts (Date,SAP_Code,Quantity) VALUES ('2026-01-01','MIX-OK',100)")
        # LOW item
        self._seed_item(db_conn, "MIX-LOW", min_qty=50)
        db_conn.execute("INSERT INTO receipts (Date,SAP_Code,Quantity) VALUES ('2026-01-01','MIX-LOW',10)")
        db_conn.commit()
        low = get_low_stock_items(db_conn)
        codes = low["SAP_Code"].values if not low.empty else []
        assert "MIX-LOW" in codes
        assert "MIX-OK"  not in codes
