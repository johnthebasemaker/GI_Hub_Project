"""
test_database.py — Schema integrity, CRUD, and self-healing tests
==================================================================
Tests in this file verify that the database layer:
  1. Creates all required tables on first run
  2. Seeds the correct default data
  3. Performs self-healing schema alignment idempotently
  4. Handles basic CRUD on the inventory table
  5. Correctly filters system columns from dynamic forms
"""

import sqlite3
import pandas as pd
import pytest

from database import (
    init_db,
    get_work_types,
    EXTENDED_ISSUE_COLS,
    SYSTEM_COLS,
)


# ===========================================================================
# GROUP 1: Table Creation
# ===========================================================================

class TestTableCreation:
    """Verifies that init_db() creates every required table."""

    REQUIRED_TABLES = {
        "pending_issues",
        "consumption",
        "receipts",
        "returns",
        "inventory",
        "system_settings",
        "users",
    }

    def _get_table_names(self, conn: sqlite3.Connection) -> set:
        df = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            conn,
        )
        return set(df["name"].tolist())

    def test_all_required_tables_exist(self, db_conn):
        """All 7 tables must be present after init_db()."""
        tables = self._get_table_names(db_conn)
        assert self.REQUIRED_TABLES.issubset(tables), (
            f"Missing tables: {self.REQUIRED_TABLES - tables}"
        )

    def test_init_db_is_idempotent(self, db_conn):
        """Calling init_db() twice must not raise or duplicate anything."""
        init_db(db_conn)   # second call on the same connection
        tables = self._get_table_names(db_conn)
        assert self.REQUIRED_TABLES.issubset(tables)

    def test_pending_issues_has_id_primary_key(self, db_conn):
        """pending_issues must have an auto-increment primary key named 'id'."""
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(pending_issues)")
        cols = {row[1]: row for row in c.fetchall()}
        assert "id" in cols
        assert cols["id"][5] == 1  # pk flag

    def test_inventory_has_minimum_qty_column(self, db_conn):
        """inventory table must include Minimum_Qty for low-stock alerts."""
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(inventory)")
        col_names = [row[1] for row in c.fetchall()]
        assert "Minimum_Qty" in col_names

    def test_users_table_role_constraint(self, db_conn):
        """users table must enforce role CHECK constraint."""
        c = db_conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("baduser", "hash", "god_mode"),   # invalid role
            )


# ===========================================================================
# GROUP 2: Default Seed Data
# ===========================================================================

class TestSeedData:
    """Verifies the default data seeded by init_db()."""

    def test_four_default_work_types(self, db_conn):
        """Exactly 4 default Work_Type options are seeded."""
        types = get_work_types(db_conn)
        assert len(types) == 4

    def test_default_work_type_names(self, db_conn):
        """The 4 seeded work types match the expected names."""
        types = get_work_types(db_conn)
        expected = {"Maintenance", "New Project Area", "Fabrication", "Office"}
        assert set(types) == expected

    def test_work_types_not_duplicated_on_second_init(self, db_conn):
        """Calling init_db() again must not double-seed work types."""
        init_db(db_conn)  # second call
        types = get_work_types(db_conn)
        assert len(types) == 4, f"Expected 4, got {len(types)}: {types}"


# ===========================================================================
# GROUP 3: Self-Healing Schema Alignment
# ===========================================================================

class TestSchemaHealing:
    """
    Verifies that init_db() surgically adds missing columns rather than
    failing or recreating tables.
    """

    def _get_columns(self, conn: sqlite3.Connection, table: str) -> set:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in c.fetchall()}

    def test_extended_cols_added_to_pending_issues(self, db_conn):
        """All EXTENDED_ISSUE_COLS appear in pending_issues after init."""
        cols = self._get_columns(db_conn, "pending_issues")
        for col in EXTENDED_ISSUE_COLS:
            assert col in cols, f"Missing column '{col}' in pending_issues"

    def test_extended_cols_added_to_consumption(self, db_conn):
        """All EXTENDED_ISSUE_COLS (except Date) appear in consumption after init."""
        cols = self._get_columns(db_conn, "consumption")
        # Date is expected to be in consumption as well
        for col in EXTENDED_ISSUE_COLS:
            assert col in cols, f"Missing column '{col}' in consumption"

    def test_manual_drop_then_reinit_heals(self):
        """
        Simulate legacy DB missing 'Issued_By':
        drop the column by recreating the table without it, then re-run
        init_db() — the column must reappear.
        """
        conn = sqlite3.connect(":memory:")
        c = conn.cursor()

        # Create a stripped-down pending_issues missing Issued_By
        c.execute("""
            CREATE TABLE pending_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                Date TEXT, SAP_Code TEXT, Quantity REAL,
                Work_Type TEXT, Remarks TEXT,
                Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE TABLE consumption (Date TEXT, SAP_Code TEXT, Quantity REAL)")
        c.execute("CREATE TABLE system_settings (category TEXT, value TEXT)")
        c.execute("CREATE TABLE receipts (Date TEXT, SAP_Code TEXT, Quantity REAL)")
        c.execute("CREATE TABLE returns (Date TEXT, SAP_Code TEXT, Quantity REAL)")
        c.execute("""CREATE TABLE inventory (SAP_Code TEXT PRIMARY KEY,
                     Equipment_Description TEXT, Material_Code TEXT,
                     UOM TEXT, Minimum_Qty REAL DEFAULT 0)""")
        conn.commit()

        # Verify the column is absent before healing
        c.execute("PRAGMA table_info(pending_issues)")
        before = {row[1] for row in c.fetchall()}
        assert "Issued_By" not in before

        # Run init_db() — it should heal the schema
        init_db(conn)

        c.execute("PRAGMA table_info(pending_issues)")
        after = {row[1] for row in c.fetchall()}
        assert "Issued_By" in after, "Schema self-healing failed for Issued_By"

        conn.close()


# ===========================================================================
# GROUP 4: Inventory CRUD
# ===========================================================================

class TestInventoryCRUD:
    """Tests Create, Read, Update, Delete on the inventory table."""

    def test_insert_inventory_item(self, db_conn):
        """Insert a single inventory item and verify it can be read back."""
        c = db_conn.cursor()
        c.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
            "VALUES (?, ?, ?, ?)",
            ("9999", "Test Pump", "PCS", 5),
        )
        db_conn.commit()

        df = pd.read_sql("SELECT * FROM inventory WHERE SAP_Code='9999'", db_conn)
        assert len(df) == 1
        assert df.iloc[0]["Equipment_Description"] == "Test Pump"

    def test_update_inventory_item(self, db_conn):
        """Update Minimum_Qty on an existing item."""
        c = db_conn.cursor()
        c.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
            "VALUES (?, ?, ?, ?)",
            ("8888", "Gate Valve", "PCS", 3),
        )
        db_conn.commit()

        c.execute("UPDATE inventory SET Minimum_Qty=10 WHERE SAP_Code='8888'")
        db_conn.commit()

        df = pd.read_sql("SELECT Minimum_Qty FROM inventory WHERE SAP_Code='8888'", db_conn)
        assert df.iloc[0]["Minimum_Qty"] == 10

    def test_delete_inventory_item(self, db_conn):
        """Delete an item and confirm it no longer exists."""
        c = db_conn.cursor()
        c.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
            ("7777", "Bolt M16", "PCS"),
        )
        db_conn.commit()
        c.execute("DELETE FROM inventory WHERE SAP_Code='7777'")
        db_conn.commit()

        df = pd.read_sql("SELECT * FROM inventory WHERE SAP_Code='7777'", db_conn)
        assert df.empty

    def test_sap_code_uniqueness(self, db_conn):
        """inventory table enforces PRIMARY KEY uniqueness on SAP_Code."""
        c = db_conn.cursor()
        c.execute(
            "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
            ("6666", "Flange", "PCS"),
        )
        db_conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
                ("6666", "Duplicate Flange", "PCS"),
            )


# ===========================================================================
# GROUP 5: System Column Filtering (Dynamic Form Guard)
# ===========================================================================

class TestSystemColumnFiltering:
    """
    Verifies that SYSTEM_COLS are always excluded from dynamic form generation.
    This prevents auto-managed columns (id, Timestamp, etc.) from appearing
    as editable fields in the UI.
    """

    def test_system_cols_constant_contains_expected_keys(self):
        """SYSTEM_COLS must include id, Timestamp, and created_at at minimum."""
        assert {"id", "Timestamp", "created_at"}.issubset(SYSTEM_COLS)

    def test_form_cols_exclude_system_cols(self, db_conn):
        """Simulates what the dynamic form generator does and checks the output."""
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(pending_issues)")
        all_cols = [row[1] for row in c.fetchall()]
        form_cols = [col for col in all_cols if col not in SYSTEM_COLS]

        for sys_col in SYSTEM_COLS:
            assert sys_col not in form_cols, (
                f"System column '{sys_col}' leaked into form_cols"
            )

    def test_form_cols_still_contain_data_fields(self, db_conn):
        """After filtering, useful columns like SAP_Code and Quantity must remain."""
        c = db_conn.cursor()
        c.execute("PRAGMA table_info(pending_issues)")
        all_cols = [row[1] for row in c.fetchall()]
        form_cols = [col for col in all_cols if col not in SYSTEM_COLS]

        assert "SAP_Code" in form_cols
        assert "Quantity" in form_cols
        assert "Work_Type" in form_cols
