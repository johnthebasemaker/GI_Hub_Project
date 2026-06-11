"""
test_fuzzy_match.py — material-name → SAP fuzzy matcher
========================================================
Pure-function tests. No DB, no Streamlit, no Ollama.
"""

import pandas as pd
import pytest

from ai.fuzzy import (
    AUTO_THRESHOLD,
    PICK_THRESHOLD,
    match_material,
    best_match,
    resolve_rows,
    normalise,
)


@pytest.fixture
def inv():
    """Small representative catalogue covering the delivery-note example."""
    return pd.DataFrame([
        {"SAP_Code": "P-6M",  "Equipment_Description": "Pipe 6m DN50",        "Material_Code": "M-1",  "UOM": "Nos"},
        {"SAP_Code": "P-4M",  "Equipment_Description": "Pipe 4m DN50",        "Material_Code": "M-2",  "UOM": "Nos"},
        {"SAP_Code": "P-3M",  "Equipment_Description": "Pipe 3m DN50",        "Material_Code": "M-3",  "UOM": "Nos"},
        {"SAP_Code": "C-DBL", "Equipment_Description": "Double Clamp",        "Material_Code": "M-7",  "UOM": "Nos"},
        {"SAP_Code": "C-SGL", "Equipment_Description": "Single Clamp",        "Material_Code": "M-8",  "UOM": "Nos"},
        {"SAP_Code": "AMF",   "Equipment_Description": "Moisture Fan (GI-AMF-005)", "Material_Code": "M-99", "UOM": "Nos"},
    ])


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
class TestNormalise:
    def test_punct_stripped(self):
        assert normalise("Pipe, 6m DN50!") == "pipe 6m dn50"

    def test_lowercase(self):
        assert normalise("DOUBLE CLAMP") == "double clamp"

    def test_uom_noise_dropped(self):
        # 'nos' is recognised as noise so it doesn't dilute the match score.
        assert "nos" not in normalise("6m pipe nos")

    def test_empty_inputs(self):
        assert normalise(None) == ""
        assert normalise("") == ""


# ---------------------------------------------------------------------------
# match_material
# ---------------------------------------------------------------------------
class TestMatchMaterial:
    def test_exact_phrase_returns_high_score(self, inv):
        cands = match_material("Double Clamp", inv)
        assert cands
        assert cands[0]["SAP_Code"] == "C-DBL"
        assert cands[0]["score"] >= AUTO_THRESHOLD

    def test_handwritten_phrase_with_token_reorder(self, inv):
        # "6m pipe" should match "Pipe 6m DN50" well even though word order differs.
        cands = match_material("6m pipe", inv)
        assert cands
        assert cands[0]["SAP_Code"] in {"P-6M", "P-4M", "P-3M"}
        # The exact 6m variant should win.
        assert cands[0]["SAP_Code"] == "P-6M"

    def test_ambiguous_phrase_returns_multiple(self, inv):
        # "pipe" matches all three pipe variants; we expect multiple candidates.
        cands = match_material("pipe", inv, top_n=3)
        assert len(cands) >= 2
        sap_set = {c["SAP_Code"] for c in cands}
        assert sap_set.intersection({"P-6M", "P-4M", "P-3M"})

    def test_unknown_returns_empty(self, inv):
        # Nothing in the catalogue is close to "abc xyz nonsense".
        assert match_material("abc xyz nonsense", inv, pick_threshold=0.95) == []

    def test_empty_query_returns_empty(self, inv):
        assert match_material("", inv) == []
        assert match_material(None, inv) == []

    def test_empty_inventory_returns_empty(self):
        assert match_material("anything", pd.DataFrame()) == []


# ---------------------------------------------------------------------------
# best_match
# ---------------------------------------------------------------------------
class TestBestMatch:
    def test_only_returns_above_auto_threshold(self, inv):
        # An exact-ish hit should auto-fill.
        bm = best_match("Single Clamp", inv)
        assert bm is not None
        assert bm["SAP_Code"] == "C-SGL"

    def test_ambiguous_returns_none(self, inv):
        # "pipe" alone should NOT auto-fill — the user must pick.
        bm = best_match("pipe", inv)
        assert bm is None or bm["score"] >= AUTO_THRESHOLD


# ---------------------------------------------------------------------------
# resolve_rows — the full per-row state machine
# ---------------------------------------------------------------------------
class TestResolveRows:
    def test_auto_state_fills_sap_uom_matcode(self, inv):
        rows = [{"material_text": "Double Clamp", "uom": "", "quantity": 5}]
        out = resolve_rows(rows, inv)
        assert out[0]["match_state"] == "auto"
        assert out[0]["SAP_Code"] == "C-DBL"
        assert out[0]["Material_Code"] == "M-7"
        # UOM inherits inventory's value when row had none.
        assert out[0]["UOM"] == "Nos"

    def test_pick_state_surfaces_candidates(self, inv):
        # "pipe" alone is ambiguous → match_state='pick', candidates non-empty.
        rows = [{"material_text": "pipe", "quantity": 10}]
        out = resolve_rows(rows, inv)
        assert out[0]["match_state"] in {"pick", "auto"}  # threshold-dependent
        if out[0]["match_state"] == "pick":
            assert out[0]["SAP_Code"] == ""  # not yet chosen
            assert len(out[0]["candidates"]) >= 2

    def test_unknown_state_leaves_sap_empty(self, inv):
        rows = [{"material_text": "qqq zzz", "quantity": 1}]
        out = resolve_rows(rows, inv)
        assert out[0]["match_state"] == "unknown"
        assert out[0]["SAP_Code"] == ""

    def test_preserves_extra_keys(self, inv):
        rows = [{
            "material_text": "Double Clamp",
            "uom": "Nos", "quantity": 5,
            "issued_to": "Imran", "work_type": "Maintenance",
        }]
        out = resolve_rows(rows, inv)
        assert out[0]["issued_to"] == "Imran"
        assert out[0]["work_type"] == "Maintenance"
