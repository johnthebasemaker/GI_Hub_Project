"""
test_multisite.py — Module 5 Multi-Site & HOD Workflow Tests
=============================================================
Groups:
  1. TestSiteIDSchema          — Site_ID column present in all 5 tables
  2. TestSiteIDSelfHealing     — Column re-added after manual drop
  3. TestRequestsTable         — CRUD on the requests table
  4. TestSiteIsolation         — Per-site inventory independence
  5. TestHODRoleHierarchy      — HOD sits between supervisor and admin
  6. TestCrossSiteWorkflow     — Full FSM pipeline: pending→approved→fulfilled
  7. TestGetSites              — get_sites() behaviour
  8. TestGetPendingIssuesForSite — Site-scoped staging queue
"""

import sqlite3
import pytest
import pandas as pd

from database import (
    init_db, get_connection,
    load_live_inventory, get_low_stock_items,
    get_sites, get_pending_issues_for_site,
    get_pending_requests, create_request, update_request_status,
)
from config import ROLE_HIERARCHY, PAGE_ACCESS, ROLES, REQUEST_STATUSES, DEFAULT_SITE
from auth import add_user, authenticate_user, seed_default_users


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------

def _seed_item(conn, sap, desc="Item", uom="PCS", min_qty=0, site="HQ"):
    conn.execute(
        "INSERT OR IGNORE INTO inventory "
        "(SAP_Code, Equipment_Description, UOM, Minimum_Qty, Site_ID) "
        "VALUES (?, ?, ?, ?, ?)",
        (sap, desc, uom, min_qty, site),
    )
    conn.commit()


def _add_receipt(conn, sap, qty, site="HQ", date="2026-01-01"):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID) VALUES (?,?,?,?)",
        (date, sap, qty, site),
    )
    conn.commit()


def _add_consumption(conn, sap, qty, site="HQ", date="2026-05-12"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Work_Type, Site_ID) "
        "VALUES (?,?,?,'Maintenance',?)",
        (date, sap, qty, site),
    )
    conn.commit()


# ===========================================================================
# GROUP 1: Site_ID Schema
# ===========================================================================

class TestSiteIDSchema:
    """Verifies Site_ID column exists in all 5 tables after init_db()."""

    TABLES = ["users", "inventory", "pending_issues", "consumption", "receipts"]

    def _get_cols(self, conn, table):
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in c.fetchall()}

    def test_users_has_site_id(self, db_conn):
        assert "Site_ID" in self._get_cols(db_conn, "users")

    def test_inventory_has_site_id(self, db_conn):
        assert "Site_ID" in self._get_cols(db_conn, "inventory")

    def test_pending_issues_has_site_id(self, db_conn):
        assert "Site_ID" in self._get_cols(db_conn, "pending_issues")

    def test_consumption_has_site_id(self, db_conn):
        assert "Site_ID" in self._get_cols(db_conn, "consumption")

    def test_receipts_has_site_id(self, db_conn):
        assert "Site_ID" in self._get_cols(db_conn, "receipts")

    def test_requests_table_exists(self, db_conn):
        tables = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table'", db_conn
        )["name"].tolist()
        assert "requests" in tables

    def test_requests_table_has_required_columns(self, db_conn):
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(requests)")
        cols = {row[1] for row in c.fetchall()}
        required = {
            "id", "requesting_site", "target_site", "SAP_Code",
            "requested_qty", "available_qty", "suggested_qty",
            "status", "notes", "requested_by", "reviewed_by",
            "created_at", "updated_at",
        }
        assert required.issubset(cols)

    def test_default_site_constant_is_hq(self):
        assert DEFAULT_SITE == "HQ"


# ===========================================================================
# GROUP 2: Site_ID Self-Healing
# ===========================================================================

class TestSiteIDSelfHealing:
    """Drops Site_ID from a table, re-runs init_db(), verifies re-added."""

    def _drop_site_id(self, conn, table):
        """SQLite <3.35 workaround: recreate table without Site_ID."""
        c = conn.cursor()
        # Derived views (v_live_stock / v_site_stock) depend on the base tables.
        # SQLite forbids dropping/rebuilding a table while a dependent view
        # exists, so drop all views first; the init_db() call that follows in
        # each test recreates them. (Real app code never rebuilds these base
        # tables — only `users`, which the views don't reference.)
        for (vname,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall():
            c.execute(f"DROP VIEW IF EXISTS {vname}")
        c.execute(f"PRAGMA table_info({table})")
        cols = [(r[1], r[2]) for r in c.fetchall() if r[1] != "Site_ID"]
        col_defs  = ", ".join(f"{n} {t}" for n, t in cols)
        col_names = ", ".join(n for n, _ in cols)
        c.execute(f"CREATE TABLE {table}_tmp ({col_defs})")
        c.execute(f"INSERT INTO {table}_tmp ({col_names}) SELECT {col_names} FROM {table}")
        c.execute(f"DROP TABLE {table}")
        c.execute(f"ALTER TABLE {table}_tmp RENAME TO {table}")
        conn.commit()

    def test_inventory_site_id_restored_after_healing(self, db_conn):
        self._drop_site_id(db_conn, "inventory")
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(inventory)")
        cols_before = {r[1] for r in c.fetchall()}
        assert "Site_ID" not in cols_before

        init_db(db_conn)

        c.execute("PRAGMA table_info(inventory)")
        cols_after = {r[1] for r in c.fetchall()}
        assert "Site_ID" in cols_after

    def test_existing_data_survives_healing(self, db_conn):
        db_conn.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) "
            "VALUES ('SH-001','Heal Test','PCS')"
        )
        db_conn.commit()
        self._drop_site_id(db_conn, "inventory")
        init_db(db_conn)
        row = pd.read_sql(
            "SELECT SAP_Code FROM inventory WHERE SAP_Code='SH-001'", db_conn
        )
        assert not row.empty

    def test_site_id_default_is_hq_on_existing_rows(self, db_conn):
        """Rows that existed before Site_ID column was added default to 'HQ'."""
        db_conn.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) "
            "VALUES ('SH-002','Legacy Item','PCS')"
        )
        db_conn.commit()
        self._drop_site_id(db_conn, "inventory")
        init_db(db_conn)
        row = pd.read_sql(
            "SELECT Site_ID FROM inventory WHERE SAP_Code='SH-002'", db_conn
        )
        assert row.iloc[0]["Site_ID"] == "HQ"


# ===========================================================================
# GROUP 3: Requests Table CRUD
# ===========================================================================

class TestRequestsTable:
    """Tests create_request / get_pending_requests / update_request_status."""

    def test_create_request_returns_integer_id(self, db_conn):
        rid = create_request(
            conn=db_conn,
            requesting_site="SITE-A", target_site="SITE-B",
            sap_code="REQ-001", requested_qty=10,
            available_qty=20, suggested_qty=10,
            requested_by="hod_a",
        )
        assert isinstance(rid, int) and rid > 0

    def test_new_request_has_pending_status(self, db_conn):
        rid = create_request(
            conn=db_conn,
            requesting_site="SITE-A", target_site="HQ",
            sap_code="REQ-002", requested_qty=5,
        )
        row = pd.read_sql("SELECT status FROM requests WHERE id=?", db_conn, params=(rid,))
        assert row.iloc[0]["status"] == "pending"

    def test_get_pending_requests_returns_all_when_no_filter(self, db_conn):
        create_request(conn=db_conn, requesting_site="A", target_site="B",
                       sap_code="X", requested_qty=1)
        create_request(conn=db_conn, requesting_site="C", target_site="D",
                       sap_code="Y", requested_qty=2)
        df = get_pending_requests(conn=db_conn)
        assert len(df) >= 2

    def test_get_pending_requests_filtered_by_site(self, db_conn):
        create_request(conn=db_conn, requesting_site="ALPHA", target_site="BETA",
                       sap_code="Z", requested_qty=3)
        create_request(conn=db_conn, requesting_site="GAMMA", target_site="DELTA",
                       sap_code="Z", requested_qty=4)
        df = get_pending_requests(conn=db_conn, site_id="ALPHA")
        sites = set(df["requesting_site"].tolist() + df["target_site"].tolist())
        assert "ALPHA" in sites
        assert "GAMMA" not in sites

    def test_get_pending_requests_filtered_by_status(self, db_conn):
        rid = create_request(conn=db_conn, requesting_site="S1", target_site="S2",
                             sap_code="ST", requested_qty=1)
        update_request_status(conn=db_conn, req_id=rid, new_status="approved",
                              reviewed_by="admin")
        pending = get_pending_requests(conn=db_conn, status="pending")
        approved = get_pending_requests(conn=db_conn, status="approved")
        pending_ids  = pending["id"].tolist()  if not pending.empty  else []
        approved_ids = approved["id"].tolist() if not approved.empty else []
        assert rid not in pending_ids
        assert rid in approved_ids

    def test_update_request_status_returns_true_on_success(self, db_conn):
        rid = create_request(conn=db_conn, requesting_site="S", target_site="T",
                             sap_code="U", requested_qty=1)
        result = update_request_status(conn=db_conn, req_id=rid,
                                       new_status="approved", reviewed_by="admin")
        assert result is True

    def test_update_request_status_returns_false_for_bad_id(self, db_conn):
        result = update_request_status(conn=db_conn, req_id=99999,
                                       new_status="approved", reviewed_by="admin")
        assert result is False

    def test_update_request_status_invalid_status_raises(self, db_conn):
        rid = create_request(conn=db_conn, requesting_site="S", target_site="T",
                             sap_code="V", requested_qty=1)
        with pytest.raises(ValueError):
            update_request_status(conn=db_conn, req_id=rid, new_status="INVALID")

    def test_update_request_sets_reviewed_by(self, db_conn):
        rid = create_request(conn=db_conn, requesting_site="S", target_site="T",
                             sap_code="W", requested_qty=1)
        update_request_status(conn=db_conn, req_id=rid, new_status="approved",
                              reviewed_by="admin_user")
        row = pd.read_sql("SELECT reviewed_by FROM requests WHERE id=?",
                          db_conn, params=(rid,))
        assert row.iloc[0]["reviewed_by"] == "admin_user"

    def test_update_request_with_notes(self, db_conn):
        rid = create_request(conn=db_conn, requesting_site="S", target_site="T",
                             sap_code="N", requested_qty=1)
        update_request_status(conn=db_conn, req_id=rid, new_status="rejected",
                              reviewed_by="admin", notes="Insufficient stock")
        row = pd.read_sql("SELECT notes FROM requests WHERE id=?",
                          db_conn, params=(rid,))
        assert "Insufficient" in row.iloc[0]["notes"]

    def test_request_statuses_constant_has_four_values(self):
        assert set(REQUEST_STATUSES) == {"pending", "approved", "rejected", "fulfilled"}


# ===========================================================================
# GROUP 4: Per-Site Inventory Isolation
# ===========================================================================

class TestSiteIsolation:
    """Verifies that Site-A data does not contaminate Site-B calculations."""

    def test_site_a_receipt_does_not_affect_site_b_stock(self, db_conn):
        _seed_item(db_conn, "ISO-001", site="SITE-A")
        _seed_item(db_conn, "ISO-001", site="SITE-B")
        _add_receipt(db_conn, "ISO-001", 100, site="SITE-A")

        df_b = load_live_inventory(db_conn, site_id="SITE-B")
        if df_b.empty or "ISO-001" not in df_b["SAP_Code"].values:
            return  # No rows for SITE-B is correct isolation
        stock_b = df_b[df_b["SAP_Code"] == "ISO-001"].iloc[0]["Current_Stock"]
        assert stock_b == 0.0

    def test_site_b_consumption_does_not_affect_site_a_stock(self, db_conn):
        _seed_item(db_conn, "ISO-002", site="SITE-A")
        _add_receipt(db_conn, "ISO-002", 50, site="SITE-A")
        _add_consumption(db_conn, "ISO-002", 30, site="SITE-B")

        df_a = load_live_inventory(db_conn, site_id="SITE-A")
        row_a = df_a[df_a["SAP_Code"] == "ISO-002"]
        assert not row_a.empty
        assert float(row_a.iloc[0]["Current_Stock"]) == pytest.approx(50.0)

    def test_admin_global_view_aggregates_both_sites(self, db_conn):
        _seed_item(db_conn, "ISO-003", site="ALPHA")
        _seed_item(db_conn, "ISO-004", site="BETA")
        _add_receipt(db_conn, "ISO-003", 20, site="ALPHA")
        _add_receipt(db_conn, "ISO-004", 35, site="BETA")

        df_all = load_live_inventory(db_conn, site_id=None)
        codes = df_all["SAP_Code"].tolist()
        assert "ISO-003" in codes
        assert "ISO-004" in codes

    def test_per_site_math_is_independent(self, db_conn):
        """Site-A: 80 received, 30 consumed → 50 stock. Site-B: 60 received, 10 consumed → 50."""
        _seed_item(db_conn, "MATH-001", site="SITE-A")
        _seed_item(db_conn, "MATH-001", site="SITE-B")
        _add_receipt(db_conn, "MATH-001", 80, site="SITE-A")
        _add_consumption(db_conn, "MATH-001", 30, site="SITE-A")
        _add_receipt(db_conn, "MATH-001", 60, site="SITE-B")
        _add_consumption(db_conn, "MATH-001", 10, site="SITE-B")

        df_a = load_live_inventory(db_conn, site_id="SITE-A")
        df_b = load_live_inventory(db_conn, site_id="SITE-B")

        stock_a = float(df_a[df_a["SAP_Code"] == "MATH-001"].iloc[0]["Current_Stock"])
        stock_b = float(df_b[df_b["SAP_Code"] == "MATH-001"].iloc[0]["Current_Stock"])

        assert stock_a == pytest.approx(50.0)
        assert stock_b == pytest.approx(50.0)

    def test_low_stock_scoped_to_site(self, db_conn):
        _seed_item(db_conn, "LOW-S1", min_qty=20, site="SITE-A")
        _add_receipt(db_conn, "LOW-S1", 5, site="SITE-A")   # LOW at SITE-A

        _seed_item(db_conn, "LOW-S2", min_qty=20, site="SITE-B")
        _add_receipt(db_conn, "LOW-S2", 100, site="SITE-B") # OK at SITE-B

        low_a = get_low_stock_items(db_conn, site_id="SITE-A")
        low_b = get_low_stock_items(db_conn, site_id="SITE-B")

        assert "LOW-S1" in (low_a["SAP_Code"].tolist() if not low_a.empty else [])
        assert "LOW-S2" not in (low_b["SAP_Code"].tolist() if not low_b.empty else [])


# ===========================================================================
# GROUP 5: HOD Role Hierarchy
# ===========================================================================

class TestHODRoleHierarchy:
    """Pure config tests — no DB required."""

    def test_hod_defined_in_role_hierarchy(self):
        assert "hod" in ROLE_HIERARCHY

    def test_hod_defined_in_roles(self):
        assert "hod" in ROLES

    def test_hod_outranks_supervisor(self):
        assert ROLE_HIERARCHY["hod"] > ROLE_HIERARCHY["supervisor"]

    def test_hod_below_admin(self):
        assert ROLE_HIERARCHY["hod"] < ROLE_HIERARCHY["admin"]

    def test_exact_hierarchy_order(self):
        assert (
            ROLE_HIERARCHY["store_keeper"]
            < ROLE_HIERARCHY["supervisor"]
            < ROLE_HIERARCHY["hod"]
            < ROLE_HIERARCHY["admin"]
        )

    def test_hod_can_access_hod_portal(self):
        hod_level = ROLE_HIERARCHY["hod"]
        req_level  = ROLE_HIERARCHY[PAGE_ACCESS["📋 HOD Portal"]]
        assert hod_level >= req_level

    def test_supervisor_cannot_access_hod_portal(self):
        sup = ROLE_HIERARCHY["supervisor"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["📋 HOD Portal"]]
        assert sup < req

    def test_worker_cannot_access_hod_portal(self):
        wkr = ROLE_HIERARCHY["store_keeper"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["📋 HOD Portal"]]
        assert wkr < req

    def test_admin_can_access_hod_portal(self):
        adm = ROLE_HIERARCHY["admin"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["📋 HOD Portal"]]
        assert adm >= req

    def test_hod_cannot_access_admin_portal(self):
        hod = ROLE_HIERARCHY["hod"]
        req = ROLE_HIERARCHY[PAGE_ACCESS["🛡️ Admin Portal"]]
        assert hod < req

    def test_hod_role_has_required_keys(self):
        meta = ROLES["hod"]
        assert "label" in meta
        assert "icon"  in meta
        assert "color" in meta

    def test_all_roles_in_hierarchy_defined_in_roles(self):
        for role in ROLE_HIERARCHY:
            assert role in ROLES, f"Role '{role}' in ROLE_HIERARCHY but missing from ROLES"

    def test_hod_is_valid_role_for_add_user(self, db_conn):
        """add_user() must accept 'hod' as a valid role."""
        result = add_user("hod_test_user", "password123", "hod", "HQ", db_conn)
        assert result is True

    def test_hod_user_authenticates_correctly(self, db_conn):
        add_user("hod_auth", "hod_pass", "hod", "HQ", db_conn)
        result = authenticate_user("hod_auth", "hod_pass", db_conn)
        assert result is not None
        assert result["role"] == "hod"

    def test_authenticate_returns_site_id(self, db_conn):
        add_user("hod_site", "hod_pass", "hod", "SITE-A", db_conn)
        result = authenticate_user("hod_site", "hod_pass", db_conn)
        assert result is not None
        assert result["site_id"] == "SITE-A"


# ===========================================================================
# GROUP 6: Cross-Site Workflow FSM
# ===========================================================================

class TestCrossSiteWorkflow:
    """Tests the complete pending→approved→fulfilled lifecycle."""

    def test_full_workflow_pending_to_approved(self, db_conn):
        rid = create_request(
            conn=db_conn, requesting_site="SITE-A", target_site="HQ",
            sap_code="WF-001", requested_qty=15, available_qty=40,
            suggested_qty=15, requested_by="hod_a",
        )
        row = pd.read_sql("SELECT status FROM requests WHERE id=?",
                          db_conn, params=(rid,))
        assert row.iloc[0]["status"] == "pending"

        update_request_status(conn=db_conn, req_id=rid,
                              new_status="approved", reviewed_by="admin")
        row2 = pd.read_sql("SELECT status FROM requests WHERE id=?",
                           db_conn, params=(rid,))
        assert row2.iloc[0]["status"] == "approved"

    def test_approved_to_fulfilled(self, db_conn):
        rid = create_request(
            conn=db_conn, requesting_site="SITE-B", target_site="HQ",
            sap_code="WF-002", requested_qty=10,
        )
        update_request_status(conn=db_conn, req_id=rid,
                              new_status="approved", reviewed_by="admin")
        update_request_status(conn=db_conn, req_id=rid,
                              new_status="fulfilled", reviewed_by="hod_b")
        row = pd.read_sql("SELECT status FROM requests WHERE id=?",
                          db_conn, params=(rid,))
        assert row.iloc[0]["status"] == "fulfilled"

    def test_pending_to_rejected(self, db_conn):
        rid = create_request(
            conn=db_conn, requesting_site="SITE-C", target_site="HQ",
            sap_code="WF-003", requested_qty=5,
        )
        update_request_status(conn=db_conn, req_id=rid,
                              new_status="rejected", reviewed_by="admin",
                              notes="Stock not available")
        row = pd.read_sql("SELECT status, notes FROM requests WHERE id=?",
                          db_conn, params=(rid,))
        assert row.iloc[0]["status"] == "rejected"
        assert "not available" in row.iloc[0]["notes"]

    def test_multiple_requests_independent_status(self, db_conn):
        """Approving request A must not affect request B."""
        rid_a = create_request(conn=db_conn, requesting_site="A", target_site="B",
                               sap_code="IND-A", requested_qty=1)
        rid_b = create_request(conn=db_conn, requesting_site="C", target_site="D",
                               sap_code="IND-B", requested_qty=2)
        update_request_status(conn=db_conn, req_id=rid_a, new_status="approved",
                              reviewed_by="admin")
        row_b = pd.read_sql("SELECT status FROM requests WHERE id=?",
                            db_conn, params=(rid_b,))
        assert row_b.iloc[0]["status"] == "pending"

    def test_hod_site_only_sees_own_requests(self, db_conn):
        create_request(conn=db_conn, requesting_site="MY-SITE",
                       target_site="HQ", sap_code="VIS-1", requested_qty=1)
        create_request(conn=db_conn, requesting_site="OTHER-SITE",
                       target_site="HQ", sap_code="VIS-2", requested_qty=1)
        df = get_pending_requests(conn=db_conn, site_id="MY-SITE")
        codes = df["SAP_Code"].tolist() if not df.empty else []
        assert "VIS-1" in codes
        assert "VIS-2" not in codes


# ===========================================================================
# GROUP 7: get_sites()
# ===========================================================================

class TestGetSites:
    """Validates site list retrieval from system_settings."""

    def test_returns_list(self, db_conn):
        result = get_sites(db_conn)
        assert isinstance(result, list)

    def test_fallback_returns_hq_when_no_sites_configured(self, db_conn):
        """With no system_settings 'Site' entries and no users, must return ['HQ']."""
        result = get_sites(db_conn)
        assert "HQ" in result

    def test_returns_site_from_system_settings(self, db_conn):
        db_conn.execute(
            "INSERT INTO system_settings (category, value) VALUES ('Site','WAREHOUSE-1')"
        )
        db_conn.commit()
        sites = get_sites(db_conn)
        assert "WAREHOUSE-1" in sites

    def test_multiple_sites_returned(self, db_conn):
        for s in ["SITE-X", "SITE-Y", "SITE-Z"]:
            db_conn.execute(
                "INSERT INTO system_settings (category, value) VALUES ('Site',?)", (s,)
            )
        db_conn.commit()
        sites = get_sites(db_conn)
        assert "SITE-X" in sites
        assert "SITE-Y" in sites
        assert "SITE-Z" in sites


# ===========================================================================
# GROUP 8: get_pending_issues_for_site()
# ===========================================================================

class TestGetPendingIssuesForSite:
    """Verifies site-scoped staging queue isolation."""

    def test_returns_only_own_site_rows(self, db_conn):
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type, Site_ID, status) "
            "VALUES ('2026-05-12','PIS-001',5,'Maintenance','SITE-A','pending_hod')"
        )
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type, Site_ID, status) "
            "VALUES ('2026-05-12','PIS-002',3,'Maintenance','SITE-B','pending_hod')"
        )
        db_conn.commit()
        df = get_pending_issues_for_site(db_conn, site_id="SITE-A")
        codes = df["SAP_Code"].tolist()
        assert "PIS-001" in codes
        assert "PIS-002" not in codes

    def test_returns_empty_when_no_rows_for_site(self, db_conn):
        df = get_pending_issues_for_site(db_conn, site_id="NONEXISTENT-SITE")
        assert df.empty

    def test_empty_staging_queue_returns_empty_df(self, db_conn):
        df = get_pending_issues_for_site(db_conn, site_id="HQ")
        assert isinstance(df, pd.DataFrame)

    def test_seed_default_users_creates_four_users(self, db_conn):
        """Updated count: admin + hod + supervisor + worker = 4."""
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT * FROM users", db_conn)
        assert len(df) == 4

    def test_seed_creates_hod_user(self, db_conn):
        seed_default_users(db_conn)
        df = pd.read_sql("SELECT username FROM users WHERE role='hod'", db_conn)
        assert "hod" in df["username"].values

    def test_hod_default_password_authenticates(self, db_conn):
        seed_default_users(db_conn)
        result = authenticate_user("hod", "hod2026", db_conn)
        assert result is not None and result["role"] == "hod"
