"""
pwa/ — Standalone FastAPI + static PWA for offline scan-and-stage
==================================================================
Lives ALONGSIDE the Streamlit app, not inside it. Two separate processes
share the same SQLite database (WAL mode makes that safe). Use case:
warehouse Wi-Fi drops — store keepers keep scanning on their phones, the
PWA queues entries in IndexedDB, and pushes them to `pending_issues` (the
same staging table the web app uses) when connectivity returns.

Architecture:
  pwa/api.py            — FastAPI service (auth, inventory, batch staging)
  pwa/static/index.html — single-file PWA UI
  pwa/static/sw.js      — service worker (offline shell)
  pwa/static/app.webmanifest — PWA install metadata
  pwa/run.py            — uvicorn launcher

This package has ZERO Streamlit imports and never invokes Streamlit
session_state. It uses only the pure-Python functions in database.py and
auth.py — the same way our test suite does.
"""
