"""
test_item_snapshot.py — Scan-to-Inspect math contracts
========================================================
Verifies that `database.get_item_snapshot` honours the project's identity
formula and site-scoping rules without re-implementing them. SQL is not
asserted; behaviour is.
"""

import datetime

import pytest

from database import get_item_snapshot


def _seed_item(conn, sap, desc="Widget", uom="PCS", min_qty=10):
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
        "VALUES (?, ?, ?, ?)",
        (sap, desc, uom, min_qty),
    )
    conn.commit()


def _receipt(conn, sap, qty, date="2026-06-01", site="HQ"):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


def _consumption(conn, sap, qty, date="2026-06-01", site="HQ"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Identity formula contract
# ---------------------------------------------------------------------------
class TestIdentityFormula:
    """Current_Stock = Σreceipts − Σconsumption − Σreturned."""

    def test_global_stock_equals_receipts_minus_consumption(self, db_conn):
        _seed_item(db_conn, "SNAP-1")
        _receipt(db_conn, "SNAP-1", 100.0)
        _consumption(db_conn, "SNAP-1", 30.0)

        snap = get_item_snapshot("SNAP-1", site_id=None, conn=db_conn)

        assert snap["found"] is True
        assert snap["current_stock"] == pytest.approx(70.0)
        assert snap["rcpt_total"] == pytest.approx(100.0)
        assert snap["cons_total"] == pytest.approx(30.0)

    def test_unknown_sap_returns_not_found(self, db_conn):
        snap = get_item_snapshot("DOES-NOT-EXIST", conn=db_conn)
        assert snap["found"] is False
        assert snap["current_stock"] == 0.0
        assert snap["cons_df"].empty
        assert snap["rcpt_df"].empty


# ---------------------------------------------------------------------------
# Site scoping contract
# ---------------------------------------------------------------------------
class TestSiteScoping:
    """site_id=None → global; site_id='HQ' → filtered to that site."""

    def test_site_scope_isolates_sums(self, db_conn):
        _seed_item(db_conn, "SNAP-2")
        _receipt(db_conn, "SNAP-2", 50.0, site="HQ")
        _receipt(db_conn, "SNAP-2", 80.0, site="SiteB")
        _consumption(db_conn, "SNAP-2", 10.0, site="HQ")
        _consumption(db_conn, "SNAP-2", 20.0, site="SiteB")

        hq = get_item_snapshot("SNAP-2", site_id="HQ", conn=db_conn)
        global_view = get_item_snapshot("SNAP-2", site_id=None, conn=db_conn)

        assert hq["current_stock"] == pytest.approx(40.0)   # 50 - 10
        assert global_view["current_stock"] == pytest.approx(100.0)  # 130 - 30


# ---------------------------------------------------------------------------
# Lookback window contract
# ---------------------------------------------------------------------------
class TestLookbackWindow:
    """Old rows are excluded from the N-day window but still count in stock."""

    def test_old_consumption_excluded_from_window_but_in_stock(self, db_conn):
        _seed_item(db_conn, "SNAP-3")
        _receipt(db_conn, "SNAP-3", 100.0, date="2026-01-01")
        # One row inside the 30-day window, one well outside it
        recent = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        old    = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        _consumption(db_conn, "SNAP-3", 5.0, date=recent)
        _consumption(db_conn, "SNAP-3", 12.0, date=old)

        snap = get_item_snapshot("SNAP-3", site_id=None, conn=db_conn, lookback_days=30)

        # Stock identity includes ALL consumption (100 - 5 - 12 = 83)
        assert snap["current_stock"] == pytest.approx(83.0)
        # …but the 30-day window only shows the recent one
        assert snap["cons_total"] == pytest.approx(5.0)
        assert len(snap["cons_df"]) == 1
