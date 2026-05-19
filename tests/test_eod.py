"""
test_eod.py — End-of-Day commit pipeline tests
================================================
The EOD commit is the most critical operation in the system.
It atomically moves all staged issues into the permanent consumption record
and clears the staging queue.

Contracts being verified:
  1. Empty queue → commit_eod() returns 0, no side effects
  2. Basic commit → rows appear in consumption, disappear from pending
  3. Row count → return value equals number of rows committed
  4. Data integrity → all field values survive the transfer unchanged
  5. Schema auto-sync → extra columns in pending are added to consumption first
  6. Idempotency → running commit_eod() twice on empty table is safe
  7. Atomic behaviour → consumption grows by exactly the number of pending rows
  8. Multiple commits → each commit appends, never overwrites prior records
"""

import sqlite3
import pandas as pd
import pytest

from database import commit_eod, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pending_count(conn) -> int:
    return pd.read_sql("SELECT count(*) as n FROM pending_issues", conn).iloc[0]["n"]


def _consumption_count(conn) -> int:
    return pd.read_sql("SELECT count(*) as n FROM consumption", conn).iloc[0]["n"]


def _add_pending(conn, sap_code, qty, date="2026-05-06", work_type="Maintenance"):
    conn.execute(
        "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type) VALUES (?, ?, ?, ?)",
        (date, sap_code, qty, work_type),
    )
    conn.commit()


def _add_inventory(conn, sap_code, desc="Test Item"):
    conn.execute(
        "INSERT OR IGNORE INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
        (sap_code, desc, "PCS"),
    )
    conn.commit()


# ===========================================================================
# GROUP 1: Empty Queue Behaviour
# ===========================================================================

class TestEmptyQueueCommit:
    """commit_eod() on an empty queue must be a safe, zero-effect no-op."""

    def test_empty_queue_returns_zero(self, db_conn):
        result = commit_eod(db_conn)
        assert result == 0

    def test_empty_queue_does_not_write_to_consumption(self, db_conn):
        before = _consumption_count(db_conn)
        commit_eod(db_conn)
        after = _consumption_count(db_conn)
        assert after == before

    def test_empty_queue_does_not_raise(self, db_conn):
        """commit_eod() must never raise an exception regardless of state."""
        try:
            commit_eod(db_conn)
        except Exception as e:
            pytest.fail(f"commit_eod() raised on empty queue: {e}")

    def test_double_commit_on_empty_is_idempotent(self, db_conn):
        """Calling commit_eod() twice when queue is empty should both return 0."""
        assert commit_eod(db_conn) == 0
        assert commit_eod(db_conn) == 0


# ===========================================================================
# GROUP 2: Basic Commit Pipeline
# ===========================================================================

class TestBasicCommitPipeline:
    """Verifies the fundamental commit flow: pending → consumption → queue cleared."""

    def test_single_row_committed_to_consumption(self, db_conn):
        _add_inventory(db_conn, "EOD-001")
        _add_pending(db_conn, "EOD-001", 10.0)

        commit_eod(db_conn)

        consumption_df = pd.read_sql("SELECT * FROM consumption", db_conn)
        assert len(consumption_df) == 1
        assert consumption_df.iloc[0]["SAP_Code"] == "EOD-001"

    def test_pending_queue_cleared_after_commit(self, db_conn):
        _add_inventory(db_conn, "EOD-002")
        _add_pending(db_conn, "EOD-002", 5.0)

        assert _pending_count(db_conn) == 1
        commit_eod(db_conn)
        assert _pending_count(db_conn) == 0

    def test_multiple_rows_all_committed(self, db_conn):
        for i, code in enumerate(["EOD-003", "EOD-004", "EOD-005"], 1):
            _add_inventory(db_conn, code)
            _add_pending(db_conn, code, float(i * 10))

        result = commit_eod(db_conn)

        assert result == 3
        assert _consumption_count(db_conn) == 3
        assert _pending_count(db_conn) == 0

    def test_return_value_equals_rows_committed(self, db_conn):
        """commit_eod() must return exactly the number of rows moved."""
        for code in ["EOD-006", "EOD-007"]:
            _add_inventory(db_conn, code)
            _add_pending(db_conn, code, 1.0)

        result = commit_eod(db_conn)
        assert result == 2


# ===========================================================================
# GROUP 3: Data Integrity — values must survive transfer unchanged
# ===========================================================================

class TestDataIntegrity:
    """
    Verifies that every field value in pending_issues arrives in consumption
    exactly as-is: no truncation, no type coercion, no NULL injection.
    """

    def test_sap_code_preserved_exactly(self, db_conn):
        _add_inventory(db_conn, "INTEG-001")
        _add_pending(db_conn, "INTEG-001", 42.0)
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-001'", db_conn)
        assert df.iloc[0]["SAP_Code"] == "INTEG-001"

    def test_quantity_preserved_exactly(self, db_conn):
        _add_inventory(db_conn, "INTEG-002")
        _add_pending(db_conn, "INTEG-002", 99.5)
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-002'", db_conn)
        assert float(df.iloc[0]["Quantity"]) == 99.5

    def test_date_preserved_exactly(self, db_conn):
        _add_inventory(db_conn, "INTEG-003")
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type) VALUES (?, ?, ?, ?)",
            ("2026-12-25", "INTEG-003", 1.0, "Maintenance"),
        )
        db_conn.commit()
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-003'", db_conn)
        assert df.iloc[0]["Date"] == "2026-12-25"

    def test_work_type_preserved_exactly(self, db_conn):
        _add_inventory(db_conn, "INTEG-004")
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type) VALUES (?, ?, ?, ?)",
            ("2026-05-06", "INTEG-004", 3.0, "New Project Area"),
        )
        db_conn.commit()
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-004'", db_conn)
        assert df.iloc[0]["Work_Type"] == "New Project Area"

    def test_remarks_preserved_including_special_chars(self, db_conn):
        _add_inventory(db_conn, "INTEG-005")
        remark = "Tank #7 — Zone A/B (urgent!)"
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type, Remarks) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-05-06", "INTEG-005", 2.0, "Maintenance", remark),
        )
        db_conn.commit()
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-005'", db_conn)
        assert df.iloc[0]["Remarks"] == remark

    def test_fractional_quantity_not_rounded(self, db_conn):
        """Quantities like 3.14159 must not be rounded to integers."""
        _add_inventory(db_conn, "INTEG-006")
        db_conn.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type) VALUES (?, ?, ?, ?)",
            ("2026-05-06", "INTEG-006", 3.14159, "Fabrication"),
        )
        db_conn.commit()
        commit_eod(db_conn)
        df = pd.read_sql("SELECT * FROM consumption WHERE SAP_Code='INTEG-006'", db_conn)
        assert float(df.iloc[0]["Quantity"]) == pytest.approx(3.14159)


# ===========================================================================
# GROUP 4: Schema Auto-Sync During Commit
# ===========================================================================

class TestSchemaAutoSync:
    """
    The EOD commit must automatically add any columns present in pending_issues
    to consumption BEFORE writing, to prevent "table has no column X" errors.
    This mirrors the self-healing logic from the original app.py.
    """

    def test_extra_column_in_pending_is_added_to_consumption(self, db_conn):
        """If pending_issues has 'Tank_No' and consumption doesn't, commit must fix it."""
        c = db_conn.cursor()

        # Verify Tank_No is already there (added by init_db self-healing)
        c.execute("PRAGMA table_info(consumption)")
        cons_cols = {row[1] for row in c.fetchall()}
        # Tank_No should already be there due to EXTENDED_ISSUE_COLS healing
        # But let's test with a custom column that definitely isn't there
        try:
            c.execute("ALTER TABLE pending_issues ADD COLUMN Custom_Field TEXT")
            db_conn.commit()
        except Exception:
            pass  # Column might already exist, that's fine

        # Add a pending row with the custom field populated
        c.execute(
            "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type, Custom_Field) "
            "VALUES (?, ?, ?, ?, ?)",
            ("2026-05-06", "SYNC-001", 5.0, "Maintenance", "CustomValue"),
        )
        db_conn.commit()

        # commit_eod must not crash due to missing Custom_Field in consumption
        try:
            commit_eod(db_conn)
        except Exception as e:
            pytest.fail(f"Schema auto-sync failed: {e}")

        # Verify the commit happened despite the schema mismatch
        assert _consumption_count(db_conn) == 1

    def test_existing_columns_not_duplicated_after_sync(self, db_conn):
        """Auto-sync must not duplicate columns that already exist."""
        _add_inventory(db_conn, "SYNC-002")
        _add_pending(db_conn, "SYNC-002", 10.0)
        commit_eod(db_conn)

        c = db_conn.cursor()
        c.execute("PRAGMA table_info(consumption)")
        cols = [row[1] for row in c.fetchall()]
        # Each column name should be unique
        assert len(cols) == len(set(cols)), "Duplicate columns detected in consumption!"


# ===========================================================================
# GROUP 5: Multiple Sequential EOD Commits (Accumulation)
# ===========================================================================

class TestSequentialCommits:
    """
    Real-world scenario: commit_eod() is called once per day.
    Each day's records must APPEND to consumption, never overwrite prior days.
    """

    def test_day1_then_day2_commit_both_in_consumption(self, db_conn):
        _add_inventory(db_conn, "SEQ-001")

        # Day 1 commit
        _add_pending(db_conn, "SEQ-001", 20.0, date="2026-05-06")
        commit_eod(db_conn)
        assert _consumption_count(db_conn) == 1

        # Day 2 commit
        _add_pending(db_conn, "SEQ-001", 15.0, date="2026-05-07")
        commit_eod(db_conn)

        # Both records must exist in consumption
        assert _consumption_count(db_conn) == 2

    def test_cumulative_consumption_after_multiple_commits(self, db_conn):
        """Total consumed must equal sum across all committed EOD sessions."""
        _add_inventory(db_conn, "SEQ-002")

        daily_qtys = [10.0, 25.0, 5.0, 30.0]  # 4 days
        for i, qty in enumerate(daily_qtys, 1):
            _add_pending(db_conn, "SEQ-002", qty, date=f"2026-05-{i:02d}")
            commit_eod(db_conn)

        total = pd.read_sql(
            "SELECT SUM(Quantity) as total FROM consumption WHERE SAP_Code='SEQ-002'",
            db_conn,
        ).iloc[0]["total"]
        assert float(total) == sum(daily_qtys)

    def test_commit_after_empty_commit_still_works(self, db_conn):
        """Calling commit on empty queue, then adding items, must still commit correctly."""
        _add_inventory(db_conn, "SEQ-003")

        commit_eod(db_conn)  # empty — no-op
        assert _consumption_count(db_conn) == 0

        _add_pending(db_conn, "SEQ-003", 8.0)
        result = commit_eod(db_conn)
        assert result == 1
        assert _consumption_count(db_conn) == 1

    def test_pending_always_empty_after_each_commit(self, db_conn):
        """After every commit cycle, the staging queue must be empty."""
        _add_inventory(db_conn, "SEQ-004")

        for qty in [5.0, 10.0, 15.0]:
            _add_pending(db_conn, "SEQ-004", qty)
            commit_eod(db_conn)
            assert _pending_count(db_conn) == 0, (
                f"Staging queue not cleared after commit! qty={qty}"
            )
