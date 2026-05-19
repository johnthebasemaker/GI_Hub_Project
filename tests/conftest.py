"""
conftest.py — Shared pytest fixtures for the GI Lightning Hub test suite
=========================================================================
All fixtures use an in-memory SQLite database (:memory:).
The production file `gi_database.db` is NEVER touched by these tests.

Fixture scopes:
  - `db_conn`   : fresh empty DB, initialised schema, per-test isolation
  - `seeded_db` : db_conn + realistic sample data for 3 inventory items
"""

import pytest
import sqlite3
import sys
import os

# ── Make the project root importable so `import database` works ──────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db


# ---------------------------------------------------------------------------
# FIXTURE: clean database with schema only
# ---------------------------------------------------------------------------
@pytest.fixture
def db_conn():
    """
    Yields a fresh in-memory SQLite connection with a fully initialised schema.
    Torn down automatically after every test function.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# FIXTURE: database with realistic seed data
# ---------------------------------------------------------------------------
@pytest.fixture
def seeded_db(db_conn):
    """
    Yields db_conn populated with:
      - 3 inventory items (SAP codes 1001, 1002, 1003)
      - Receipt records for all 3 items
      - 2 pending issues (1001 and 1002) ready for EOD commit
      - 1 return record (1001)
    Minimum_Qty thresholds are set so low-stock tests can be meaningful.
    """
    c = db_conn.cursor()

    # ── Inventory master ──────────────────────────────────────────────────────
    items = [
        ("1001", "Steel Pipe DN50",   "M-100", "PCS", 10),
        ("1002", "Valve Gate 2in",    "M-200", "PCS",  5),
        ("1003", "Welding Rod 6013",  "M-300", "BOX", 20),
    ]
    c.executemany(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, Material_Code, UOM, Minimum_Qty) "
        "VALUES (?, ?, ?, ?, ?)",
        items,
    )

    # ── Receipts ──────────────────────────────────────────────────────────────
    receipts = [
        ("2026-01-01", "1001", 100.0),
        ("2026-01-01", "1002",  50.0),
        ("2026-01-01", "1003",  30.0),
    ]
    c.executemany(
        "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
        receipts,
    )

    # ── Pending Issues (staging queue, not yet committed) ─────────────────────
    pending = [
        ("2026-05-06", "1001", 15.0, "Maintenance",    ""),
        ("2026-05-06", "1002",  8.0, "New Project Area","Tank-7"),
    ]
    c.executemany(
        "INSERT INTO pending_issues (Date, SAP_Code, Quantity, Work_Type, Remarks) "
        "VALUES (?, ?, ?, ?, ?)",
        pending,
    )

    # ── Confirmed Consumption (already committed on a prior day) ──────────────
    c.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Work_Type) VALUES (?, ?, ?, ?)",
        ("2026-04-30", "1001", 10.0, "Fabrication"),
    )

    # ── Returns ───────────────────────────────────────────────────────────────
    c.execute(
        "INSERT INTO returns (Date, SAP_Code, Quantity, Reason) VALUES (?, ?, ?, ?)",
        ("2026-05-01", "1001", 5.0, "Surplus"),
    )

    db_conn.commit()
    yield db_conn
