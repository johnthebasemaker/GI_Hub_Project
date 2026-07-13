"""
test_pwa_api.py — FastAPI endpoints for the floor PWA
======================================================
Uses FastAPI's TestClient. No network, no real Streamlit. The PWA service
opens its own connections to gi_database.db via `database.get_connection`,
so we point the test at an isolated DB file via a monkey-patched DB_FILE
and reset state between tests.
"""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import database
import auth


@pytest.fixture
def pwa_client(monkeypatch):
    """
    Spin up the PWA app against a TEMP SQLite file (not gi_database.db).
    Seeds one known user 'floor' with password 'pw' so the login test has
    a deterministic target.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    monkeypatch.setattr(database, "DB_FILE", tmp.name)

    # Initialise schema in the temp DB.
    conn = database.get_connection()
    try:
        database.init_db(conn)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, Site_ID) VALUES (?, ?, ?, ?)",
            ("floor", auth.hash_password("pw"), "store_keeper", "HQ"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO inventory (SAP_Code, Equipment_Description, UOM, Minimum_Qty) "
            "VALUES ('PWA-1', 'Test Widget', 'PCS', 5)"
        )
        conn.commit()
    finally:
        conn.close()

    # Import app AFTER patching DB_FILE so init_db at import sees the new path.
    # The pwa.api module calls init_db() at import time, so we need a fresh import.
    import importlib, pwa.api as api_mod
    importlib.reload(api_mod)
    client = TestClient(api_mod.app)
    yield client
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# /api/login
# ---------------------------------------------------------------------------
class TestLogin:
    def test_valid_login_returns_token_and_user(self, pwa_client):
        r = pwa_client.post("/api/login", json={"username": "floor", "password": "pw"})
        assert r.status_code == 200
        j = r.json()
        assert j["token"]
        assert j["username"] == "floor"
        assert j["role"] == "store_keeper"
        assert j["site_id"] == "HQ"

    def test_wrong_password_is_401(self, pwa_client):
        r = pwa_client.post("/api/login", json={"username": "floor", "password": "nope"})
        assert r.status_code == 401

    def test_unknown_user_is_401(self, pwa_client):
        r = pwa_client.post("/api/login", json={"username": "ghost", "password": "x"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Token-gated endpoints
# ---------------------------------------------------------------------------
def _token(client):
    return client.post("/api/login", json={"username": "floor", "password": "pw"}).json()["token"]


class TestTokenGate:
    def test_no_token_blocks_inventory(self, pwa_client):
        r = pwa_client.get("/api/inventory")
        assert r.status_code == 401

    def test_garbage_token_blocks_inventory(self, pwa_client):
        r = pwa_client.get("/api/inventory", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401

    def test_whoami_returns_user(self, pwa_client):
        t = _token(pwa_client)
        r = pwa_client.get("/api/whoami", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200
        assert r.json()["username"] == "floor"


# ---------------------------------------------------------------------------
# /api/inventory
# ---------------------------------------------------------------------------
class TestInventory:
    def test_returns_seeded_item(self, pwa_client):
        t = _token(pwa_client)
        r = pwa_client.get("/api/inventory", headers={"Authorization": f"Bearer {t}"})
        assert r.status_code == 200
        j = r.json()
        assert j["count"] >= 1
        codes = [i["SAP_Code"] for i in j["items"]]
        assert "PWA-1" in codes


# ---------------------------------------------------------------------------
# /api/pending_issues/batch
# ---------------------------------------------------------------------------
class TestBatchStage:
    def test_batch_inserts_into_pending_issues_with_draft_status(self, pwa_client):
        t = _token(pwa_client)
        payload = {"items": [
            {"SAP_Code": "PWA-1", "Quantity": 3, "Work_Type": "Maintenance"},
            {"SAP_Code": "PWA-1", "Quantity": 1.5},
        ]}
        r = pwa_client.post(
            "/api/pending_issues/batch",
            json=payload,
            headers={"Authorization": f"Bearer {t}"},
        )
        assert r.status_code == 200
        assert r.json()["inserted"] == 2

        # Verify rows landed in pending_issues with status='draft' and Site_ID
        conn = database.get_connection()
        try:
            rows = conn.execute(
                "SELECT SAP_Code, Quantity, status, Site_ID, Issued_By "
                "FROM pending_issues WHERE SAP_Code='PWA-1'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        for sap, qty, st, site, by in rows:
            assert sap == "PWA-1"
            assert st == "draft"
            assert site == "HQ"
            # Issued_By defaults to the token's username when client didn't supply one.
            assert by == "floor"

    def test_batch_validates_payload(self, pwa_client):
        t = _token(pwa_client)
        # Missing SAP_Code → pydantic 422
        r = pwa_client.post(
            "/api/pending_issues/batch",
            json={"items": [{"Quantity": 1}]},
            headers={"Authorization": f"Bearer {t}"},
        )
        assert r.status_code == 422

    def test_empty_batch_is_ok_with_zero_inserts(self, pwa_client):
        t = _token(pwa_client)
        r = pwa_client.post(
            "/api/pending_issues/batch",
            json={"items": []},
            headers={"Authorization": f"Bearer {t}"},
        )
        assert r.status_code == 200
        assert r.json()["inserted"] == 0


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
class TestOps:
    def test_healthz(self, pwa_client):
        r = pwa_client.get("/healthz")
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True
        assert "inventory" in j and "users" in j and "pending_issues" in j

    def test_static_shell_served(self, pwa_client):
        r = pwa_client.get("/")
        assert r.status_code == 200
        assert "<!DOCTYPE html>" in r.text or "<html" in r.text.lower()

    def test_manifest_served(self, pwa_client):
        r = pwa_client.get("/app.webmanifest")
        assert r.status_code == 200
        assert "GI Floor" in r.text


# ---------------------------------------------------------------------------
# Schema self-healing
# ---------------------------------------------------------------------------
class TestSchemaSelfHealing:
    def test_pwa_tokens_table_exists(self, pwa_client):
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pwa_tokens'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
