"""
test_logistics.py — Module 6 PR Tracking & Logistics Tests
=============================================================
Automatically tests:
  1. Receipt Schema (Self-healing columns)
  2. PR Balance Math & Auto-Fulfillment 
  3. Partial vs Full Deliveries
  4. Shelf-Life Expiry Logic (Red / Amber alerts)
"""

import pytest
import pandas as pd
import datetime

from database import (
    process_receipt_delivery, 
    get_short_dated_stock,
    get_pr_balance
)

# ===========================================================================
# GROUP 1: PR Math & Logistics Receiving
# ===========================================================================

class TestLogisticsReceiving:
    
    def test_receipt_columns_auto_heal(self, db_conn):
        """Validates that init_db() automatically created the new logistics columns."""
        df = pd.read_sql("PRAGMA table_info(receipts)", db_conn)
        cols = df["name"].tolist()
        assert "Supplier" in cols
        assert "PR_Number" in cols
        assert "Expiry_Date" in cols

    def test_partial_delivery_keeps_pr_open(self, db_conn):
        """If we request 100 but receive 40, the PR should stay 'open'."""
        db_conn.execute(
            "INSERT INTO pr_master (PR_Number, SAP_Code, Requested_Qty, Site_ID, status) "
            "VALUES ('PR-100', 'SAP-1', 100, 'HQ', 'open')"
        )
        db_conn.commit()

        # Receive only 40
        process_receipt_delivery(
            db_conn, "2026-05-17", "SAP-1", 40, "Vendor A", "Partial", "HQ", "PR-100"
        )
        
        status_df = pd.read_sql("SELECT status FROM pr_master WHERE PR_Number='PR-100'", db_conn)
        assert status_df.iloc[0]["status"] == "open"
        
        balance = get_pr_balance(db_conn, "PR-100")
        assert balance["balance"] == 60.0

    def test_full_delivery_closes_pr_automatically(self, db_conn):
        """If we request 50 and receive 50, the system must auto-close the PR."""
        db_conn.execute(
            "INSERT INTO pr_master (PR_Number, SAP_Code, Requested_Qty, Site_ID, status) "
            "VALUES ('PR-200', 'SAP-2', 50, 'HQ', 'open')"
        )
        db_conn.commit()

        # Receive all 50
        process_receipt_delivery(
            db_conn, "2026-05-17", "SAP-2", 50, "Vendor B", "Full", "HQ", "PR-200"
        )
        
        status_df = pd.read_sql("SELECT status FROM pr_master WHERE PR_Number='PR-200'", db_conn)
        assert status_df.iloc[0]["status"] == "closed"
        
        balance = get_pr_balance(db_conn, "PR-200")
        assert balance["balance"] == 0.0

    def test_over_delivery_closes_pr_and_prevents_negative_balance(self, db_conn):
        """If we request 10 but the supplier sends 12, balance should be 0, not -2."""
        db_conn.execute(
            "INSERT INTO pr_master (PR_Number, SAP_Code, Requested_Qty, Site_ID, status) "
            "VALUES ('PR-300', 'SAP-3', 10, 'HQ', 'open')"
        )
        db_conn.commit()

        process_receipt_delivery(
            db_conn, "2026-05-17", "SAP-3", 12, "Vendor C", "Extra sent", "HQ", "PR-300"
        )
        
        balance = get_pr_balance(db_conn, "PR-300")
        assert balance["balance"] == 0.0  # Math floor protects against negatives
        
        status_df = pd.read_sql("SELECT status FROM pr_master WHERE PR_Number='PR-300'", db_conn)
        assert status_df.iloc[0]["status"] == "closed"


# ===========================================================================
# GROUP 2: Shelf-Life & Expiry Logic
# ===========================================================================

class TestShelfLifeLogic:
    
    def test_expired_items_flagged_red(self, db_conn):
        """Items where Expiry_Date is in the past must be flagged 🔴 Expired."""
        past_date = (datetime.date.today() - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID, Expiry_Date) "
            "VALUES ('2026-01-01', 'EXP-1', 10, 'HQ', ?)", (past_date,)
        )
        db_conn.commit()

        df = get_short_dated_stock(db_conn, "HQ")
        assert not df.empty
        assert df.iloc[0]["Status"] == "🔴 Expired"

    def test_short_dated_items_flagged_amber(self, db_conn):
        """Items expiring within 30 days must be flagged 🟡 Short-Dated."""
        soon_date = (datetime.date.today() + datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID, Expiry_Date) "
            "VALUES ('2026-01-01', 'EXP-2', 20, 'HQ', ?)", (soon_date,)
        )
        db_conn.commit()

        df = get_short_dated_stock(db_conn, "HQ")
        assert not df.empty
        assert df.iloc[0]["Status"] == "🟡 Short-Dated"

    def test_long_dated_items_are_hidden(self, db_conn):
        """Items expiring far in the future should not clutter the warning board."""
        future_date = (datetime.date.today() + datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID, Expiry_Date) "
            "VALUES ('2026-01-01', 'EXP-3', 100, 'HQ', ?)", (future_date,)
        )
        db_conn.commit()

        df = get_short_dated_stock(db_conn, "HQ")
        assert df.empty  # Should be totally filtered out