"""
test_fefo.py — First-Expiry-First-Out lot breakdown
====================================================
Allocation contract: total site consumption for a SAP_Code is absorbed by
lots in FEFO order. Remaining_Qty per lot drives the "pull from this lot"
suggestion. No-expiry lots fall to the bottom but still appear.
"""

import datetime

import pandas as pd
import pytest

from database import get_fefo_lots


def _item(conn, sap, desc="Widget"):
    conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) VALUES (?, ?, ?)",
        (sap, desc, "PCS"),
    )
    conn.commit()


def _receipt(conn, sap, qty, *, date="2026-06-01", site="HQ",
             expiry=None, supplier=None, pr=None):
    conn.execute(
        "INSERT INTO receipts (Date, SAP_Code, Quantity, Site_ID, Expiry_Date, Supplier, PR_Number) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date, sap, qty, site, expiry, supplier, pr),
    )
    conn.commit()


def _consume(conn, sap, qty, date="2026-06-02", site="HQ"):
    conn.execute(
        "INSERT INTO consumption (Date, SAP_Code, Quantity, Site_ID) VALUES (?, ?, ?, ?)",
        (date, sap, qty, site),
    )
    conn.commit()


def _d(offset):  # offset days from today
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------
class TestFefoOrdering:
    def test_nearest_expiry_first(self, db_conn):
        _item(db_conn, "FEFO-1")
        _receipt(db_conn, "FEFO-1", 5, expiry=_d(60))
        _receipt(db_conn, "FEFO-1", 5, expiry=_d(10))   # should sort first
        _receipt(db_conn, "FEFO-1", 5, expiry=_d(120))
        df = get_fefo_lots("FEFO-1", site_id="HQ", conn=db_conn)
        assert df.iloc[0]["Expiry_Date"] == _d(10)
        assert df.iloc[-1]["Expiry_Date"] == _d(120)

    def test_no_expiry_falls_to_bottom_but_still_present(self, db_conn):
        _item(db_conn, "FEFO-2")
        _receipt(db_conn, "FEFO-2", 7, expiry=None)        # no expiry
        _receipt(db_conn, "FEFO-2", 3, expiry=_d(20))
        df = get_fefo_lots("FEFO-2", site_id="HQ", conn=db_conn)
        assert len(df) == 2
        # Lot with expiry leads, no-expiry trails.
        assert df.iloc[0]["Expiry_Status"] == "Short-Dated"
        assert df.iloc[-1]["Expiry_Status"] == "No Expiry"


# ---------------------------------------------------------------------------
# FEFO allocation correctness
# ---------------------------------------------------------------------------
class TestFefoAllocation:
    def test_consumption_absorbs_earliest_lot_first(self, db_conn):
        _item(db_conn, "ALLOC-1")
        _receipt(db_conn, "ALLOC-1", 10, expiry=_d(5))    # lot A — closest expiry
        _receipt(db_conn, "ALLOC-1", 10, expiry=_d(50))   # lot B
        _consume(db_conn, "ALLOC-1", 7)                   # 7 of A absorbed
        df = get_fefo_lots("ALLOC-1", site_id="HQ", conn=db_conn).set_index("Expiry_Date")
        assert df.loc[_d(5),  "Allocated_Qty"] == pytest.approx(7.0)
        assert df.loc[_d(5),  "Remaining_Qty"] == pytest.approx(3.0)
        assert df.loc[_d(50), "Allocated_Qty"] == pytest.approx(0.0)
        assert df.loc[_d(50), "Remaining_Qty"] == pytest.approx(10.0)

    def test_consumption_spills_into_next_lot(self, db_conn):
        _item(db_conn, "ALLOC-2")
        _receipt(db_conn, "ALLOC-2", 10, expiry=_d(5))
        _receipt(db_conn, "ALLOC-2", 10, expiry=_d(50))
        _consume(db_conn, "ALLOC-2", 13)                  # exhausts A + 3 of B
        df = get_fefo_lots("ALLOC-2", site_id="HQ", conn=db_conn).set_index("Expiry_Date")
        assert df.loc[_d(5),  "Remaining_Qty"] == pytest.approx(0.0)
        assert df.loc[_d(50), "Remaining_Qty"] == pytest.approx(7.0)

    def test_consumption_exceeds_total_clips_at_zero(self, db_conn):
        _item(db_conn, "ALLOC-3")
        _receipt(db_conn, "ALLOC-3", 5, expiry=_d(10))
        _consume(db_conn, "ALLOC-3", 99)                  # more consumed than received
        df = get_fefo_lots("ALLOC-3", site_id="HQ", conn=db_conn)
        assert df.iloc[0]["Remaining_Qty"] == pytest.approx(0.0)
        # Never negative — clipped at zero
        assert (df["Remaining_Qty"] >= 0).all()


# ---------------------------------------------------------------------------
# Site scoping (lots are physically at a site)
# ---------------------------------------------------------------------------
class TestFefoSiteScope:
    def test_site_scope_filters_lots_and_consumption(self, db_conn):
        _item(db_conn, "SITE-1")
        _receipt(db_conn, "SITE-1", 10, site="HQ",    expiry=_d(20))
        _receipt(db_conn, "SITE-1", 10, site="SiteB", expiry=_d(20))
        _consume(db_conn, "SITE-1", 4, site="HQ")

        hq = get_fefo_lots("SITE-1", site_id="HQ",    conn=db_conn)
        sb = get_fefo_lots("SITE-1", site_id="SiteB", conn=db_conn)

        assert len(hq) == 1 and hq.iloc[0]["Remaining_Qty"] == pytest.approx(6.0)
        assert len(sb) == 1 and sb.iloc[0]["Remaining_Qty"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Empty / not-found behaviour
# ---------------------------------------------------------------------------
class TestFefoEdgeCases:
    def test_unknown_sap_returns_empty_frame_not_error(self, db_conn):
        df = get_fefo_lots("DOES-NOT-EXIST", site_id="HQ", conn=db_conn)
        assert df.empty
        # Must still have the contract columns so the UI can render gracefully.
        for c in ("SAP_Code", "Remaining_Qty", "Expiry_Status"):
            assert c in df.columns

    def test_no_lots_no_consumption_returns_empty(self, db_conn):
        _item(db_conn, "NEW-1")
        df = get_fefo_lots("NEW-1", site_id="HQ", conn=db_conn)
        assert df.empty
