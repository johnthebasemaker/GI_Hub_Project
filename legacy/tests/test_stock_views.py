"""
test_stock_views.py — v_live_stock / v_site_stock parity with the dashboard
============================================================================
These views are the backbone of the AI NL search. Their whole reason to exist
is that their Current_Stock matches load_live_inventory() to the unit. If that
parity ever breaks, AI answers would silently disagree with the Live Dashboard.

Also pins the behaviour that fixes the field bug: an item with receipts but NO
consumption must STILL appear (the model's old INNER JOIN dropped such rows).
"""

import pandas as pd
import pytest

from database import load_live_inventory


def _item(conn, sap, desc="Widget", uom="PCS", min_qty=10, mat="M-1"):
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, Material_Code, UOM, Minimum_Qty) "
        "VALUES (?, ?, ?, ?, ?)",
        (sap, desc, mat, uom, min_qty),
    )
    conn.commit()


def _receipt(conn, sap, qty, date="2026-06-01", site="HQ"):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


def _consume(conn, sap, qty, date="2026-06-01", site="HQ"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


def _return(conn, sap, qty, date="2026-06-01", site="HQ"):
    conn.execute(
        "INSERT INTO returns (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Parity: v_live_stock == load_live_inventory(None)
# ---------------------------------------------------------------------------
class TestGlobalParity:
    def test_view_matches_dashboard_per_item(self, db_conn):
        _item(db_conn, "V-1", min_qty=10)
        _item(db_conn, "V-2", min_qty=5)
        _receipt(db_conn, "V-1", 100); _consume(db_conn, "V-1", 30); _return(db_conn, "V-1", 5)
        _receipt(db_conn, "V-2", 40);  _consume(db_conn, "V-2", 50)  # negative stock

        dash = load_live_inventory(db_conn).set_index("SAP_Code")["Current_Stock"].to_dict()
        view = pd.read_sql(
            "SELECT SAP_Code, Current_Stock FROM v_live_stock", db_conn
        ).set_index("SAP_Code")["Current_Stock"].to_dict()

        assert view["V-1"] == pytest.approx(dash["V-1"])   # 100 - 30 - 5 = 65
        assert view["V-2"] == pytest.approx(dash["V-2"])   # 40 - 50 = -10
        assert view["V-1"] == pytest.approx(65.0)
        assert view["V-2"] == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# Non-available data: LEFT JOIN keeps items with no activity
# ---------------------------------------------------------------------------
class TestNonAvailableData:
    def test_item_with_no_transactions_still_appears(self, db_conn):
        _item(db_conn, "ZERO-1", min_qty=10)  # no receipts/consumption/returns at all
        view = pd.read_sql("SELECT * FROM v_live_stock WHERE SAP_Code='ZERO-1'", db_conn)
        assert len(view) == 1
        assert view.iloc[0]["Current_Stock"] == pytest.approx(0.0)
        assert view.iloc[0]["Total_Received"] == pytest.approx(0.0)

    def test_item_received_but_never_consumed_appears(self, db_conn):
        # This is the exact case the AI's old INNER JOIN consumption DROPPED.
        _item(db_conn, "NOCONS-1", min_qty=10)
        _receipt(db_conn, "NOCONS-1", 3)   # below minimum, never consumed
        low = pd.read_sql(
            "SELECT SAP_Code, Current_Stock, Minimum_Qty FROM v_live_stock "
            "WHERE Current_Stock < Minimum_Qty",
            db_conn,
        )
        assert "NOCONS-1" in low["SAP_Code"].tolist()
        assert low.set_index("SAP_Code").loc["NOCONS-1", "Current_Stock"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Low-stock query shape the model is told to produce
# ---------------------------------------------------------------------------
class TestLowStockQuery:
    def test_low_stock_filter_returns_only_below_minimum(self, db_conn):
        _item(db_conn, "OK-1", min_qty=10)
        _item(db_conn, "LOW-1", min_qty=10)
        _receipt(db_conn, "OK-1", 50)   # well above min
        _receipt(db_conn, "LOW-1", 4)   # below min

        rows = pd.read_sql(
            "SELECT SAP_Code FROM v_live_stock WHERE Current_Stock < Minimum_Qty",
            db_conn,
        )["SAP_Code"].tolist()
        assert "LOW-1" in rows
        assert "OK-1" not in rows


# ---------------------------------------------------------------------------
# Per-site view isolates sites and matches per-site dashboard
# ---------------------------------------------------------------------------
class TestSiteStock:
    def test_site_scope_isolation_matches_dashboard(self, db_conn):
        _item(db_conn, "S-1", min_qty=10)
        _receipt(db_conn, "S-1", 50, site="HQ")
        _receipt(db_conn, "S-1", 80, site="SiteB")
        _consume(db_conn, "S-1", 10, site="HQ")

        hq_view = pd.read_sql(
            "SELECT Current_Stock FROM v_site_stock WHERE SAP_Code='S-1' AND Site_ID='HQ'",
            db_conn,
        ).iloc[0]["Current_Stock"]
        hq_dash = load_live_inventory(db_conn, site_id="HQ").set_index("SAP_Code").loc["S-1", "Current_Stock"]

        assert hq_view == pytest.approx(hq_dash)   # 50 - 10 = 40
        assert hq_view == pytest.approx(40.0)
