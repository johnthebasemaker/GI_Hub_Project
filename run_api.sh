#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_api.sh — launch the GI Hub FastAPI backend (async SQLAlchemy → Postgres).
#
# This is a SEPARATE process from the Streamlit app. Streamlit still runs on
# SQLite and is unaffected by this.
#
# Prereqs (local dev):
#   1. Local Postgres running on 5433 with the `gihub` database populated:
#        backend/dual_ci.py (or migrate_sqlite_to_postgres.py) loads it from
#        gi_database.db. See docs/POSTGRES_MIGRATION.md.
#   2. .venv with deps installed (fastapi, uvicorn, sqlalchemy, asyncpg).
#
# Usage:
#   ./run_api.sh                 # defaults to local PG on 5433, db `gihub`
#   DATABASE_URL=... ./run_api.sh
# Then open http://localhost:8000/docs
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://postgres@127.0.0.1:5433/gihub}"
PORT="${PORT:-8000}"

echo "GI Hub API"
echo "  DB    : ${DATABASE_URL}"
echo "  Docs  : http://localhost:${PORT}/docs"
echo "  Health: http://localhost:${PORT}/health"
echo

exec .venv/bin/uvicorn backend.api.main:app --reload --host 127.0.0.1 --port "${PORT}"
