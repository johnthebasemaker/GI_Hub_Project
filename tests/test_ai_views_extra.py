"""
test_ai_views_extra.py — v_expiring_stock / v_supplier_activity
================================================================
These views back the hardened NL search. v_expiring_stock must agree with the
Shelf-Life Alerts tab (get_short_dated_stock) on what counts as Expired vs
Short-Dated; v_supplier_activity must roll receipts up per supplier correctly.
"""

import datetime

import pandas as pd
import pytest


def _item(conn, sap, desc="Widget", uom="PCS"):
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
        (sap, desc, uom),
    )
    conn.commit()


def _receipt(conn, sap, qty, *, date="2026-06-01", site="HQ",
             supplier=None, expiry=None):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID, Supplier, Expiry_Date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, sap, qty, site, supplier, expiry),
    )
    conn.commit()


def _d(offset_days: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()


# ---------------------------------------------------------------------------
# v_expiring_stock
# ---------------------------------------------------------------------------
class TestExpiringStock:
    def test_status_buckets_match_shelf_life_rules(self, db_conn):
        _item(db_conn, "EXP-1")
        _receipt(db_conn, "EXP-1", 10, expiry=_d(-5))   # expired
        _receipt(db_conn, "EXP-1", 10, expiry=_d(10))   # short-dated (<=30)
        _receipt(db_conn, "EXP-1", 10, expiry=_d(90))   # good

        df = pd.read_sql(
            "SELECT Expiry_Status, Days_Until_Expiry FROM v_expiring_stock "
            "ORDER BY Days_Until_Expiry",
            db_conn,
        )
        statuses = df["Expiry_Status"].tolist()
        assert "Expired" in statuses
        assert "Short-Dated" in statuses
        assert "Good" in statuses
        # The expired batch has a negative day count.
        assert df.iloc[0]["Days_Until_Expiry"] < 0

    def test_rows_without_expiry_are_excluded(self, db_conn):
        _item(db_conn, "EXP-2")
        _receipt(db_conn, "EXP-2", 5, expiry=None)   # no expiry → not in view
        _receipt(db_conn, "EXP-2", 5, expiry="")     # blank → not in view
        df = pd.read_sql("SELECT * FROM v_expiring_stock WHERE SAP_Code='EXP-2'", db_conn)
        assert df.empty

    def test_expiring_window_query(self, db_conn):
        _item(db_conn, "EXP-3")
        _receipt(db_conn, "EXP-3", 5, expiry=_d(15))  # within 60
        _receipt(db_conn, "EXP-3", 5, expiry=_d(200)) # outside 60
        df = pd.read_sql(
            "SELECT Expiry_Date FROM v_expiring_stock "
            "WHERE Days_Until_Expiry BETWEEN 0 AND 60",
            db_conn,
        )
        assert len(df) == 1


# ---------------------------------------------------------------------------
# v_supplier_activity
# ---------------------------------------------------------------------------
class TestSupplierActivity:
    def test_rollup_totals_and_counts(self, db_conn):
        _item(db_conn, "S-1")
        _item(db_conn, "S-2")
        _receipt(db_conn, "S-1", 100, supplier="ACME")
        _receipt(db_conn, "S-2", 50,  supplier="ACME")
        _receipt(db_conn, "S-1", 30,  supplier="Globex")

        df = pd.read_sql(
            "SELECT * FROM v_supplier_activity ORDER BY Total_Received DESC", db_conn
        ).set_index("Supplier")

        assert df.loc["ACME", "Total_Received"] == pytest.approx(150.0)
        assert df.loc["ACME", "Receipt_Count"] == 2
        assert df.loc["ACME", "Distinct_Items"] == 2
        assert df.loc["Globex", "Total_Received"] == pytest.approx(30.0)

    def test_blank_supplier_excluded(self, db_conn):
        _item(db_conn, "S-3")
        _receipt(db_conn, "S-3", 10, supplier=None)
        _receipt(db_conn, "S-3", 10, supplier="")
        df = pd.read_sql("SELECT * FROM v_supplier_activity", db_conn)
        assert df.empty

    def test_top_supplier_query_shape(self, db_conn):
        _item(db_conn, "S-4")
        _receipt(db_conn, "S-4", 80, supplier="Big")
        _receipt(db_conn, "S-4", 5,  supplier="Small")
        top = pd.read_sql(
            "SELECT Supplier, SUM(Total_Received) AS Total_Received "
            "FROM v_supplier_activity GROUP BY Supplier "
            "ORDER BY Total_Received DESC LIMIT 1",
            db_conn,
        )
        assert top.iloc[0]["Supplier"] == "Big"
