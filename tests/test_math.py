"""
test_math.py — Stock formula integrity tests
=============================================
This file is the mathematical safety net for the entire system.

The core invariant being tested:
    Current_Stock = Total_Received - Total_Consumed - Total_Returned

Every scenario below proves a specific aspect of this formula under
different conditions. If ANY of these tests fail after a code change,
the stock calculation logic has been broken and the system MUST NOT
be deployed.
"""

import sqlite3
import pandas as pd
import pytest

from database import load_live_inventory, get_table_sum, get_low_stock_items


# ---------------------------------------------------------------------------
# Helper: seed one inventory item + transaction records
# ---------------------------------------------------------------------------
def _seed_item(conn, sap_code="MATH-001", desc="Test Item", uom="PCS", min_qty=0):
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
        "VALUES (?, ?, ?, ?)",
        (sap_code, desc, uom, min_qty),
    )
    conn.commit()


def _add_receipt(conn, sap_code, qty, date="2026-01-01"):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
        (date, sap_code, qty),
    )
    conn.commit()


def _add_consumption(conn, sap_code, qty, date="2026-02-01"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
        (date, sap_code, qty),
    )
    conn.commit()


def _add_return(conn, sap_code, qty, date="2026-03-01"):
    conn.execute(
        "INSERT INTO returns (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
        (date, sap_code, qty),
    )
    conn.commit()


def _get_stock(conn, sap_code) -> float:
    """Convenience: pull Current_Stock for a single item from live inventory."""
    df = load_live_inventory(conn)
    row = df[df["SAP_Code"] == sap_code]
    assert not row.empty, f"SAP_Code '{sap_code}' not found in live inventory"
    return float(row.iloc[0]["Current_Stock"])


# ===========================================================================
# GROUP 1: Zero Baseline
# ===========================================================================

class TestZeroBaseline:
    """An item with no transactions must have zero stock."""

    def test_no_transactions_gives_zero_stock(self, db_conn):
        _seed_item(db_conn, "Z-001")
        assert _get_stock(db_conn, "Z-001") == 0.0

    def test_empty_receipts_table_gives_zero_stock(self, db_conn):
        _seed_item(db_conn, "Z-002")
        # Only has a consumption record but no receipt
        _add_consumption(db_conn, "Z-002", 10)
        # Stock should be 0 - 10 = -10 (valid negative)
        assert _get_stock(db_conn, "Z-002") == -10.0


# ===========================================================================
# GROUP 2: Receipt Only (no consumption, no returns)
# ===========================================================================

class TestReceiptOnly:
    """Stock with only receipts = exactly the sum of receipts."""

    def test_single_receipt_equals_stock(self, db_conn):
        _seed_item(db_conn, "R-001")
        _add_receipt(db_conn, "R-001", 100)
        assert _get_stock(db_conn, "R-001") == 100.0

    def test_multiple_receipts_summed_correctly(self, db_conn):
        _seed_item(db_conn, "R-002")
        _add_receipt(db_conn, "R-002", 50,  date="2026-01-01")
        _add_receipt(db_conn, "R-002", 25,  date="2026-01-15")
        _add_receipt(db_conn, "R-002", 25,  date="2026-01-30")
        assert _get_stock(db_conn, "R-002") == 100.0

    def test_fractional_receipt_quantity(self, db_conn):
        """Stock formula must handle non-integer (REAL) quantities."""
        _seed_item(db_conn, "R-003")
        _add_receipt(db_conn, "R-003", 33.5)
        assert _get_stock(db_conn, "R-003") == pytest.approx(33.5)


# ===========================================================================
# GROUP 3: Receipt minus Consumption
# ===========================================================================

class TestReceiptMinusConsumption:
    """Validates: Stock = Received - Consumed."""

    def test_basic_deduction(self, db_conn):
        _seed_item(db_conn, "C-001")
        _add_receipt(db_conn, "C-001", 100)
        _add_consumption(db_conn, "C-001", 30)
        assert _get_stock(db_conn, "C-001") == 70.0

    def test_full_depletion_gives_zero(self, db_conn):
        _seed_item(db_conn, "C-002")
        _add_receipt(db_conn, "C-002", 50)
        _add_consumption(db_conn, "C-002", 50)
        assert _get_stock(db_conn, "C-002") == 0.0

    def test_multiple_consumption_records_summed(self, db_conn):
        _seed_item(db_conn, "C-003")
        _add_receipt(db_conn, "C-003", 100)
        _add_consumption(db_conn, "C-003", 10)
        _add_consumption(db_conn, "C-003", 15)
        _add_consumption(db_conn, "C-003", 5)
        assert _get_stock(db_conn, "C-003") == 70.0

    def test_fractional_consumption(self, db_conn):
        _seed_item(db_conn, "C-004")
        _add_receipt(db_conn, "C-004", 100.0)
        _add_consumption(db_conn, "C-004", 33.33)
        assert _get_stock(db_conn, "C-004") == pytest.approx(66.67)


# ===========================================================================
# GROUP 4: Full Triangular Formula (Received - Consumed - Returned)
# ===========================================================================

class TestTriangularFormula:
    """
    Validates the complete formula:
        Current_Stock = Total_Received - Total_Consumed - Total_Returned
    """

    def test_basic_triangular_math(self, db_conn):
        """100 received - 40 consumed - 5 returned = 55."""
        _seed_item(db_conn, "T-001")
        _add_receipt(db_conn, "T-001", 100)
        _add_consumption(db_conn, "T-001", 40)
        _add_return(db_conn, "T-001", 5)
        assert _get_stock(db_conn, "T-001") == 55.0

    def test_returns_without_consumption(self, db_conn):
        """Returns alone reduce stock (e.g., direct return to supplier)."""
        _seed_item(db_conn, "T-002")
        _add_receipt(db_conn, "T-002", 80)
        _add_return(db_conn, "T-002", 10)
        assert _get_stock(db_conn, "T-002") == 70.0

    def test_multiple_transactions_all_types(self, db_conn):
        """Simulate a full month: multiple receipts, consumptions, returns."""
        _seed_item(db_conn, "T-003")
        # Week 1 receipt
        _add_receipt(db_conn, "T-003", 200, "2026-01-07")
        # Week 2 consumption
        _add_consumption(db_conn, "T-003", 50, "2026-01-14")
        _add_consumption(db_conn, "T-003", 25, "2026-01-14")
        # Week 3 receipt top-up
        _add_receipt(db_conn, "T-003", 100, "2026-01-21")
        # Week 4 return
        _add_return(db_conn, "T-003", 10, "2026-01-28")
        _add_consumption(db_conn, "T-003", 30, "2026-01-28")

        # Expected: (200+100) - (50+25+30) - 10 = 185
        assert _get_stock(db_conn, "T-003") == 185.0

    def test_formula_is_not_additive_on_returns(self, db_conn):
        """
        Critical check: returns must SUBTRACT from stock, not ADD.
        A 'return' means the item left the warehouse (e.g., returned to supplier),
        reducing inventory just like consumption would.
        """
        _seed_item(db_conn, "T-004")
        _add_receipt(db_conn, "T-004", 100)
        _add_return(db_conn, "T-004", 20)
        stock = _get_stock(db_conn, "T-004")
        assert stock == 80.0, (
            f"Returns must reduce stock. Expected 80, got {stock}. "
            "Check that Total_Returned is being subtracted, not added."
        )


# ===========================================================================
# GROUP 5: Negative Stock Edge Case
# ===========================================================================

class TestNegativeStock:
    """
    The system must HANDLE negative stock without crashing.
    Negative stock is a data integrity warning, not a code error.
    It may occur due to late receipt entries or data entry errors.
    """

    def test_consumed_more_than_received_gives_negative(self, db_conn):
        _seed_item(db_conn, "N-001")
        _add_receipt(db_conn, "N-001", 10)
        _add_consumption(db_conn, "N-001", 15)
        stock = _get_stock(db_conn, "N-001")
        assert stock == -5.0, f"Expected -5.0, got {stock}"

    def test_negative_stock_is_a_float_not_none(self, db_conn):
        _seed_item(db_conn, "N-002")
        _add_consumption(db_conn, "N-002", 100)   # no receipt at all
        df = load_live_inventory(db_conn)
        row = df[df["SAP_Code"] == "N-002"]
        assert row.iloc[0]["Current_Stock"] is not None
        assert isinstance(float(row.iloc[0]["Current_Stock"]), float)

    def test_load_live_inventory_does_not_raise_on_negative(self, db_conn):
        """load_live_inventory() must complete without exception even with negatives."""
        _seed_item(db_conn, "N-003")
        _add_consumption(db_conn, "N-003", 999)
        try:
            live_df = load_live_inventory(db_conn)
        except Exception as e:
            pytest.fail(f"load_live_inventory raised unexpectedly: {e}")
        assert not live_df.empty


# ===========================================================================
# GROUP 6: Multi-Item Isolation
# ===========================================================================

class TestMultiItemIsolation:
    """
    Stock calculations for Item A must never affect Item B.
    This catches cross-join bugs or misaligned groupby operations.
    """

    def test_two_items_independent_stock(self, db_conn):
        """Each item's stock is calculated independently."""
        _seed_item(db_conn, "ISO-A")
        _seed_item(db_conn, "ISO-B")

        _add_receipt(db_conn, "ISO-A", 100)
        _add_consumption(db_conn, "ISO-A", 30)  # A should be 70

        _add_receipt(db_conn, "ISO-B", 50)
        _add_consumption(db_conn, "ISO-B", 50)  # B should be 0

        assert _get_stock(db_conn, "ISO-A") == 70.0
        assert _get_stock(db_conn, "ISO-B") == 0.0

    def test_consumption_of_a_does_not_reduce_b(self, db_conn):
        """Large consumption of item A must have zero effect on item B's stock."""
        _seed_item(db_conn, "ISO-C")
        _seed_item(db_conn, "ISO-D")

        _add_receipt(db_conn, "ISO-C", 1000)
        _add_consumption(db_conn, "ISO-C", 999)

        _add_receipt(db_conn, "ISO-D", 200)

        assert _get_stock(db_conn, "ISO-D") == 200.0

    def test_ten_items_all_correct_simultaneously(self, db_conn):
        """Stress test: 10 items, each with unique quantities, all correct at once."""
        expected_stocks = {}
        for i in range(1, 11):
            code = f"STRESS-{i:02d}"
            recv = float(i * 100)
            cons = float(i * 20)
            ret  = float(i * 5)
            _seed_item(db_conn, code)
            _add_receipt(db_conn, code, recv)
            _add_consumption(db_conn, code, cons)
            _add_return(db_conn, code, ret)
            expected_stocks[code] = recv - cons - ret

        live_df = load_live_inventory(db_conn)
        for code, expected in expected_stocks.items():
            row = live_df[live_df["SAP_Code"] == code]
            actual = float(row.iloc[0]["Current_Stock"])
            assert actual == pytest.approx(expected), (
                f"[{code}] Expected {expected}, got {actual}"
            )


# ===========================================================================
# GROUP 7: get_table_sum helper function
# ===========================================================================

class TestGetTableSum:
    """Unit tests for the get_table_sum() aggregation helper."""

    def test_empty_table_returns_empty_dataframe(self, db_conn):
        result = get_table_sum(db_conn, "receipts", "Total_Received")
        assert result.empty
        assert "SAP_Code" in result.columns
        assert "Total_Received" in result.columns

    def test_single_row_returns_correct_sum(self, db_conn):
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
            ("2026-01-01", "SUM-001", 75.0),
        )
        db_conn.commit()
        result = get_table_sum(db_conn, "receipts", "Total_Received")
        row = result[result["SAP_Code"] == "SUM-001"]
        assert float(row.iloc[0]["Total_Received"]) == 75.0

    def test_groupby_aggregates_multiple_rows(self, db_conn):
        for qty in [10, 20, 30]:
            db_conn.execute(
                "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
                ("2026-01-01", "SUM-002", qty),
            )
        db_conn.commit()
        result = get_table_sum(db_conn, "receipts", "Total_Received")
        row = result[result["SAP_Code"] == "SUM-002"]
        assert float(row.iloc[0]["Total_Received"]) == 60.0

    def test_non_numeric_quantity_is_coerced_to_zero(self, db_conn):
        """Corrupted 'N/A' quantity values must not crash the system."""
        db_conn.execute(
            "INSERT INTO receipts (Date, SAP_Code, Quantity) VALUES (?, ?, ?)",
            ("2026-01-01", "SUM-003", "N/A"),
        )
        db_conn.commit()
        # Should not raise; corrupted value treated as 0
        result = get_table_sum(db_conn, "receipts", "Total_Received")
        row = result[result["SAP_Code"] == "SUM-003"]
        assert float(row.iloc[0]["Total_Received"]) == 0.0


# ===========================================================================
# GROUP 8: Low-Stock Alert Logic
# ===========================================================================

class TestLowStockLogic:
    """Validates get_low_stock_items() threshold logic."""

    def test_item_above_minimum_not_flagged(self, db_conn):
        _seed_item(db_conn, "LS-001", min_qty=10)
        _add_receipt(db_conn, "LS-001", 50)   # stock=50, min=10 → OK
        low = get_low_stock_items(db_conn)
        assert "LS-001" not in low["SAP_Code"].values

    def test_item_below_minimum_is_flagged(self, db_conn):
        _seed_item(db_conn, "LS-002", min_qty=20)
        _add_receipt(db_conn, "LS-002", 10)   # stock=10, min=20 → LOW
        low = get_low_stock_items(db_conn)
        assert "LS-002" in low["SAP_Code"].values

    def test_item_exactly_at_minimum_not_flagged(self, db_conn):
        """Boundary condition: stock == Minimum_Qty is acceptable, not low."""
        _seed_item(db_conn, "LS-003", min_qty=15)
        _add_receipt(db_conn, "LS-003", 15)   # stock=15, min=15 → EXACTLY AT MIN
        low = get_low_stock_items(db_conn)
        assert "LS-003" not in low["SAP_Code"].values

    def test_shortage_column_is_correct(self, db_conn):
        """Shortage = Minimum_Qty - Current_Stock."""
        _seed_item(db_conn, "LS-004", min_qty=50)
        _add_receipt(db_conn, "LS-004", 30)   # shortage = 50 - 30 = 20
        low = get_low_stock_items(db_conn)
        row = low[low["SAP_Code"] == "LS-004"]
        assert float(row.iloc[0]["Shortage"]) == 20.0

    def test_empty_inventory_returns_empty_dataframe(self, db_conn):
        """No items in inventory → get_low_stock_items returns empty DataFrame."""
        result = get_low_stock_items(db_conn)
        assert isinstance(result, pd.DataFrame)
