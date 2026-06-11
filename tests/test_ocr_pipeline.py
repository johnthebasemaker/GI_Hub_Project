"""
test_ocr_pipeline.py — paste + vision-LLM lanes for the OCR upload feature
============================================================================
Paste lanes are 100% offline. Vision lanes are exercised by monkey-patching
`ai.client.ollama_vision_generate` so no Ollama is needed.
"""

import json
import pytest

from ai import ocr


# ---------------------------------------------------------------------------
# PASTE LANE — consumption
# ---------------------------------------------------------------------------
class TestParseConsumptionPaste:
    def test_comma_separated_rows(self):
        text = """\
Imran, 6m pipe, Nos, 45, Maintenance
Johnson, Double Clamp, Nos, 200, Fabrication
"""
        res = ocr.parse_consumption_paste(text)
        assert res.ok
        assert len(res.rows) == 2
        assert res.rows[0]["issued_to"] == "Imran"
        assert res.rows[0]["material_text"] == "6m pipe"
        assert res.rows[0]["quantity"] == 45.0
        assert res.rows[1]["work_type"] == "Fabrication"

    def test_tab_separated_with_header(self):
        text = "Name\tMaterial\tUOM\tQty\tWork\n" \
               "Imran\t6m pipe\tNos\t45\tMaint"
        res = ocr.parse_consumption_paste(text)
        assert res.ok
        assert len(res.rows) == 1  # header skipped
        assert res.rows[0]["material_text"] == "6m pipe"

    def test_empty_input_returns_error(self):
        res = ocr.parse_consumption_paste("")
        assert not res.ok

    def test_unparseable_input_returns_error(self):
        res = ocr.parse_consumption_paste("\n\n   \n")
        assert not res.ok

    def test_missing_qty_defaults_to_zero(self):
        res = ocr.parse_consumption_paste("Imran, 6m pipe, Nos")
        assert res.ok
        assert res.rows[0]["quantity"] == 0.0


# ---------------------------------------------------------------------------
# PASTE LANE — delivery note
# ---------------------------------------------------------------------------
class TestParseDeliveryNotePaste:
    def test_full_header_and_items(self):
        text = """\
DN_No: 15668
Date: 2026-06-02
Mob_From: GI - ABU HADRIYAH
Driver_Name: Imran
Vehicle_No: 3909
Prepared_by: Harshavardhan
Mob_To: CNCEC-RAS AL KHAIR

6m pipe, Nos, 45
4m pipe, Nos, 60
Double Clamp, Nos, 200
"""
        res = ocr.parse_delivery_note_paste(text)
        assert res.ok
        assert res.header["DN_No"] == "15668"
        assert res.header["Mob_From"] == "GI - ABU HADRIYAH"
        assert res.header["Mob_To"] == "CNCEC-RAS AL KHAIR"
        assert res.header["Vehicle_No"] == "3909"
        assert len(res.items) == 3
        assert res.items[0]["material_text"] == "6m pipe"
        assert res.items[0]["quantity"] == 45.0
        assert res.items[2]["material_text"] == "Double Clamp"

    def test_synonyms_in_header_keys(self):
        # 'Customer Name:' and 'Vehicle No:' should map to canonical keys.
        text = """\
Ref No: 15668
Customer Name: GI - ABU HADRIYAH
Vehicle No: 3909
Driver: Imran
6m pipe, Nos, 45
"""
        res = ocr.parse_delivery_note_paste(text)
        assert res.ok
        assert res.header["DN_No"] == "15668"
        assert res.header["Mob_From"] == "GI - ABU HADRIYAH"
        assert res.header["Vehicle_No"] == "3909"
        assert res.header["Driver_Name"] == "Imran"

    def test_no_items_returns_error_even_with_header(self):
        res = ocr.parse_delivery_note_paste("DN_No: 1\nDate: 2026-06-02")
        assert not res.ok


# ---------------------------------------------------------------------------
# VISION LANE — Ollama mocked
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_vision(monkeypatch):
    """Replace the vision call with a programmable canned response."""
    canned = {"value": ""}

    def fake_vision(model, prompt, image_b64_list, **kw):
        return canned["value"]

    monkeypatch.setattr(ocr, "OLLAMA_AVAILABLE", True)
    monkeypatch.setattr(ocr, "ollama_vision_generate", fake_vision)
    return canned


class TestVisionConsumption:
    def test_clean_json_parsed(self, mock_vision):
        mock_vision["value"] = json.dumps({"rows": [
            {"issued_to": "Imran", "material_text": "6m pipe",
             "uom": "Nos", "quantity": 45, "work_type": "Maintenance"},
        ]})
        res = ocr.extract_consumption_from_image(b"fake")
        assert res.ok
        assert res.rows[0]["material_text"] == "6m pipe"
        assert res.rows[0]["quantity"] == 45.0

    def test_fenced_json_parsed(self, mock_vision):
        mock_vision["value"] = (
            "Sure, here you go:\n```json\n"
            + json.dumps({"rows": [{"material_text": "X", "quantity": 1}]})
            + "\n```\n"
        )
        res = ocr.extract_consumption_from_image(b"fake")
        assert res.ok
        assert res.rows[0]["material_text"] == "X"

    def test_garbage_response_returns_not_ok(self, mock_vision):
        mock_vision["value"] = "I cannot read this image. Sorry!"
        res = ocr.extract_consumption_from_image(b"fake")
        assert not res.ok

    def test_no_ollama_returns_friendly_message(self, monkeypatch):
        monkeypatch.setattr(ocr, "OLLAMA_AVAILABLE", False)
        res = ocr.extract_consumption_from_image(b"fake")
        assert not res.ok
        assert "ollama" in res.message.lower()


class TestVisionDeliveryNote:
    def test_parses_header_and_items(self, mock_vision):
        mock_vision["value"] = json.dumps({
            "header": {
                "DN_No": "15668", "Date": "2026-06-02",
                "Mob_From": "GI - ABU HADRIYAH",
                "Driver_Name": "Imran", "Vehicle_No": "3909",
                "Prepared_by": "Harshavardhan",
                "Mob_To": "CNCEC-RAS AL KHAIR",
            },
            "items": [
                {"material_text": "6m pipe",      "uom": "Nos", "quantity": 45},
                {"material_text": "Double Clamp", "uom": "Nos", "quantity": 200},
            ],
        })
        res = ocr.extract_delivery_note_from_image(b"fake")
        assert res.ok
        assert res.header["DN_No"] == "15668"
        assert res.header["Mob_To"] == "CNCEC-RAS AL KHAIR"
        assert len(res.items) == 2

    def test_no_items_returns_not_ok(self, mock_vision):
        mock_vision["value"] = json.dumps({"header": {"DN_No": "X"}})
        res = ocr.extract_delivery_note_from_image(b"fake")
        assert not res.ok
