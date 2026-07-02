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

## Endpoints

### Meta + entities (read)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness + DB connectivity |
| GET | `/meta/sites` | distinct `Site_ID`s (for a site picker) |
| GET | `/meta/inventory-summary` | exact item counts by site / category |
| GET | `/{entity}` | paginated list — `?limit=&offset=&site_id=` |
| GET | `/{entity}/{id}` | one row by primary key |

Entities: `inventory` (PK `SAP_Code`), `receipts`, `consumption`, `returns`,
`lots`, `purchase-orders`, `equipment`, `employees`, `vendors`, `warehouses`.
List responses are `{total, limit, offset, count, items}`, ordered by the
explicit primary key. Entities with a `Site_ID` column accept `?site_id=`.

### Derived stock (computed — v2)

Postgres-native ports of the SQLite reporting views, computed at request time
(`backend/api/stock.py`). Row-for-row parity with the SQLite views is asserted by
`backend/api/parity_check.py` (also runs in CI).

| Method | Path | Mirrors | Notes |
|---|---|---|---|
| GET | `/stock/live` | `v_live_stock` | current stock per SAP_Code (global) |
| GET | `/stock/by-site` | `v_site_stock` | per SAP_Code + Site_ID; `?site_id=` |
| GET | `/stock/lots` | `v_lot_balance` | per-lot remaining qty; `?site_id=` |
| GET | `/stock/expiring` | `v_expiring_stock` | days-to-expiry + status; `?site_id=&within_days=` |

### Writes (v2 — master data only)

| Method | Path | Notes |
|---|---|---|
| POST | `/{entity}` | create; returns the new row (201) |
| PUT | `/{entity}/{id}` | update given fields; returns the row |
| DELETE | `/{entity}/{id}` | delete by id |

Writable entities: **`vendors`, `warehouses`, `employees`** only (reference data,
no ledger business rules). `created_at`/`updated_at` are auto-managed. Unknown or
secret columns → 422; constraint violations → 400.

**Ledger tables stay read-only** (`receipts`/`consumption`/`returns`/`inventory`/
`lots`/`purchase-orders`) — their writes carry identity-math / FEFO / audit logic
that must be ported into a dedicated **services layer** (a later milestone), not
issued as naive INSERTs. Attempting to POST them returns 405.

## Design notes / guardrails

- **Writes are scoped to master data** (vendors/warehouses/employees). Ledger
  writes are deferred to a services layer (see above) — accuracy over speed.
- **Accuracy first.** Derived figures are *ports of the SQLite views* verified
  row-for-row against SQLite by `parity_check.py` (in CI), never hand-estimated.
  Non-derived reads are raw table rows / exact GROUP BY counts.
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
