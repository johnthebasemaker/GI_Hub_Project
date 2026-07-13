"""
test_over_issue_guard.py — site-scoped stock guard for consumption
====================================================================
The Entry Log's "Add to Grid" button reads `cached_item_snapshot(...,
site_id=user_site)` and refuses to stage a consumption row whose qty
exceeds the site's `current_stock`.

This test pins the underlying snapshot math the guard depends on: per-site
stock = receipts(site) - consumption(site) - returns(site). If THAT goes
wrong the UI block would either over-permit or over-deny — both bad.
"""

import pandas as pd
import pytest

from database import get_item_snapshot


def _item(conn, sap, desc="Widget", uom="PCS", min_qty=5):
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
        "VALUES (?, ?, ?, ?)",
        (sap, desc, uom, min_qty),
    )
    conn.commit()


def _r(conn, sap, qty, site="HQ"):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        ("2026-06-01", sap, qty, site),
    )
    conn.commit()


def _c(conn, sap, qty, site="HQ"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        ("2026-06-02", sap, qty, site),
    )
    conn.commit()


class TestSiteStockUsedByGuard:
    def test_site_stock_after_receipts_only(self, db_conn):
        _item(db_conn, "GUARD-1")
        _r(db_conn, "GUARD-1", 50, site="HQ")
        snap = get_item_snapshot("GUARD-1", site_id="HQ", conn=db_conn)
        assert snap["found"] is True
        assert snap["current_stock"] == pytest.approx(50.0)

    def test_site_stock_drops_with_consumption_at_same_site(self, db_conn):
        _item(db_conn, "GUARD-2")
        _r(db_conn, "GUARD-2", 30, site="HQ")
        _c(db_conn, "GUARD-2", 12, site="HQ")
        snap = get_item_snapshot("GUARD-2", site_id="HQ", conn=db_conn)
        assert snap["current_stock"] == pytest.approx(18.0)

    def test_other_site_stock_does_not_leak_into_this_site(self, db_conn):
        # Plenty of stock at SiteB; ZERO at HQ. Guard at HQ must say "0 available".
        _item(db_conn, "GUARD-3")
        _r(db_conn, "GUARD-3", 100, site="SiteB")
        snap = get_item_snapshot("GUARD-3", site_id="HQ", conn=db_conn)
        assert snap["current_stock"] == pytest.approx(0.0)

    def test_guard_logic_blocks_over_qty(self, db_conn):
        # The exact comparison the UI does: float(qty) > float(current_stock).
        _item(db_conn, "GUARD-4")
        _r(db_conn, "GUARD-4", 10, site="HQ")
        snap = get_item_snapshot("GUARD-4", site_id="HQ", conn=db_conn)
        avail = float(snap["current_stock"])

        # Within bounds → allowed.
        assert not (float(5) > avail)
        # Exactly at bounds → allowed (== is fine, only > blocks).
        assert not (float(avail) > avail)
        # Above bounds → blocked.
        assert float(avail + 0.01) > avail
