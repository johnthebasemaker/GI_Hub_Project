# GI Hub API (FastAPI)

A read-only REST API over the GI Hub ERP data, served from **PostgreSQL** via
**async SQLAlchemy**. This is the decoupled foundation for the future React
frontend.

It is a **separate process** from the Streamlit app. Streamlit still runs on
SQLite and is completely unaffected by this — see
[`docs/POSTGRES_MIGRATION.md`](../../docs/POSTGRES_MIGRATION.md) for why the app
stays on SQLite while Postgres backs the API (the ORM quotes identifiers, so the
mixed-case problem that blocks Streamlit-on-PG does not apply here).

## Run it locally

Prerequisites:
1. Local Postgres on port **5433** with the `gihub` database populated. The
   migration/dual-CI tooling loads it from `gi_database.db`:
   ```bash
   DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5433/gihub \
       .venv/bin/python backend/dual_ci.py --source gi_database.db
   ```
2. `.venv` with deps installed: `pip install -r requirements.txt`
   (adds `fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `greenlet`).

Then, from the repo root:
```bash
./run_api.sh
```
Open **http://localhost:8000/docs** for the interactive Swagger UI.

Point it at a different database with `DATABASE_URL` (psycopg2/plain Postgres
URLs are auto-normalised to the asyncpg driver):
```bash
DATABASE_URL=postgresql://user:pass@host:5432/db ./run_api.sh
```

## Endpoints (v1 — read only)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness + DB connectivity |
| GET | `/meta/sites` | distinct `Site_ID`s (for a site picker) |
| GET | `/meta/inventory-summary` | exact item counts by site / category |
| GET | `/{entity}` | paginated list — `?limit=&offset=&site_id=` |
| GET | `/{entity}/{id}` | one row by primary key |

Entities: `inventory` (PK `SAP_Code`), `receipts`, `consumption`, `returns`,
`lots`, `purchase-orders`, `equipment`, `employees`, `vendors`, `warehouses`.

List responses are `{total, limit, offset, count, items}`. Rows are ordered by
the explicit primary key. Entities with a `Site_ID` column accept `?site_id=`.

## Design notes / guardrails

- **Read-only.** Writes (POST/PUT/DELETE) are intentionally deferred to v2.
- **Accuracy first.** v1 serves raw table rows and *exact* GROUP BY counts only.
  Derived/computed figures (e.g. "live stock", which is a SQLite view today) are
  deferred to v2 so they can be ported to Postgres *with parity tests* rather
  than reimplemented by hand.
- **Secrets excluded.** Credential-bearing tables (`users`, `pending_users`,
  `*_tokens`, `qr_approval_requests`) are not exposed, and any column whose name
  looks secret (password/token/hash/…) plus all binary blobs are scrubbed from
  responses.
- **Site scoping** (`?site_id=`) and **explicit PK ordering** follow the backend
  architecture rules in `docs/POSTGRES_MIGRATION.md`.

## Layout

```
backend/api/
  main.py    FastAPI app: entity list + /health + /meta/* aggregates
  crud.py    generic read-only router factory (Core Table -> list/detail)
  db.py      async engine + AsyncSession dependency
  config.py  DATABASE_URL normalisation + CORS origins
```
