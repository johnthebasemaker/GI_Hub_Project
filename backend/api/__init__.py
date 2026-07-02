"""
backend.api — FastAPI service (async SQLAlchemy over PostgreSQL).

This is the decoupled REST foundation for the future React frontend. It is a
SEPARATE process from the Streamlit app (which stays on SQLite) and talks to
Postgres through the ORM in `backend/models.py` — so identifiers are quoted
automatically and the mixed-case problem that blocks Streamlit-on-PG does not
apply here.

Run it (from the repo root):
    ./run_api.sh
    # or:
    DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/gihub \
        .venv/bin/uvicorn backend.api.main:app --reload --port 8000

Then open http://localhost:8000/docs
"""
