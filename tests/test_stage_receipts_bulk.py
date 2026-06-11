"""
test_stage_receipts_bulk.py — delivery-note bulk submitter
============================================================
Exercises the database.stage_pending_receipts_bulk path used by the OCR
upload UI. Same write semantics as the manual Receive Material form, so
the HOD's Pending Receipts tab sees the rows identically.
"""

import pandas as pd
import pytest

from database import stage_pending_receipts_bulk


@pytest.fixture
def inv_seeded(db_conn):
    db_conn.execute(
        "INSERT INTO inventory (SAP_Code, Equipment_Description, UOM) "
        "VALUES ('P-6M', 'Pipe 6m DN50', 'Nos'), ('C-DBL', 'Double Clamp', 'Nos')"
    )
    db_conn.commit()
    return db_conn


def _header():
    return {
        "DN_No": "15668", "Date": "2026-06-02",
        "Mob_From": "GI - ABU HADRIYAH",
        "Driver_Name": "Imran", "Vehicle_No": "3909",
        "Prepared_by": "Harshavardhan",
        "Mob_To": "CNCEC-RAS AL KHAIR",
    }


class TestBulkInsert:
    def test_inserts_with_draft_status_and_header_applied(self, inv_seeded):
        rows = [
            {"SAP_Code": "P-6M",  "Quantity": 45.0, "UOM": "Nos"},
            {"SAP_Code": "C-DBL", "Quantity": 200.0, "UOM": "Nos"},
        ]
        n = stage_pending_receipts_bulk(
            rows=rows, header=_header(),
            username="floor", site_id="HQ",
            conn=inv_seeded,
        )
        assert n == 2

        df = pd.read_sql(
            "SELECT SAP_Code, Quantity, status, Site_ID, DN_No, Mob_From, Mob_To, "
            "       Driver_Name, Vehicle_No, Prepared_by "
            "FROM pending_receipts ORDER BY SAP_Code",
            inv_seeded,
        )
        assert len(df) == 2
        # Header applied to every row.
        for _, row in df.iterrows():
            assert row["status"] == "draft"
            assert row["Site_ID"] == "HQ"
            assert row["DN_No"] == "15668"
            assert row["Mob_From"] == "GI - ABU HADRIYAH"
            assert row["Mob_To"] == "CNCEC-RAS AL KHAIR"
            assert row["Vehicle_No"] == "3909"

    def test_skips_rows_missing_sap_or_qty(self, inv_seeded):
        rows = [
            {"SAP_Code": "P-6M", "Quantity": 45.0},
            {"SAP_Code": "",     "Quantity": 10.0},   # bad
            {"SAP_Code": "C-DBL"},                    # bad
        ]
        n = stage_pending_receipts_bulk(
            rows=rows, header=_header(),
            username="floor", site_id="HQ",
            conn=inv_seeded,
        )
        assert n == 1

    def test_empty_rows_returns_zero(self, inv_seeded):
        n = stage_pending_receipts_bulk(
            rows=[], header=_header(),
            username="floor", site_id="HQ",
            conn=inv_seeded,
        )
        assert n == 0

    def test_unknown_header_keys_dropped_silently(self, inv_seeded):
        bad = dict(_header())
        bad["totally_made_up_field"] = "x"
        n = stage_pending_receipts_bulk(
            rows=[{"SAP_Code": "P-6M", "Quantity": 1}],
            header=bad, username="floor", site_id="HQ",
            conn=inv_seeded,
        )
        assert n == 1  # extra key tolerated, row inserted

    def test_visible_to_hod_pending_receipts_query(self, inv_seeded):
        # End-to-end: rows must show up in the same SELECT the HOD uses.
        stage_pending_receipts_bulk(
            rows=[{"SAP_Code": "P-6M", "Quantity": 45}],
            header=_header(), username="floor", site_id="HQ",
            conn=inv_seeded,
        )
        df = pd.read_sql(
            "SELECT * FROM pending_receipts "
            "WHERE COALESCE(status,'draft')='draft' AND COALESCE(Site_ID,'HQ')='HQ'",
            inv_seeded,
        )
        assert len(df) == 1
        assert df.iloc[0]["SAP_Code"] == "P-6M"
