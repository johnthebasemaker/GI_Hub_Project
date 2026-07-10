# Production Cutover — legacy SQLite → PostgreSQL

`cutover_migrate.py` is the one-shot, heavily-verified data migration for
cutover day. It wraps the proven core copier
(`backend/migrate_sqlite_to_postgres.py` — the same code `dual_ci` exercises on
every CI reload) with production pre-flight, post-load fixes and a verification
battery.

## What it does

| Stage | Detail |
|---|---|
| **Pre-flight** | SQLite `integrity_check`; target reachable; **refuses a non-empty target without `--wipe`** |
| **Load** | Full schema from `backend/models.py` (the contract) + all data, chunked. The 3 rowid-ledger tables (`receipts`/`consumption`/`returns`) keep `id := sqlite rowid` so `posted_txn_ref` (`C:{rowid}`/`R:{rowid}`) stays valid. SQLite's loose typing is coerced (junk in numeric/date columns → NULL, counted). PG sequences reset past `MAX(id)`. Source-only columns are reported loudly. |
| **Post-load** | `alembic_version` stamped to the current head (future Alembic migrations apply cleanly). Phone columns (`users`/`pending_users`/`employees`) normalised to the **+E.164** project rule — unparseable values are left untouched and listed. |
| **Verify** | Per-table row-count parity · dual_ci semantic aggregates (stock identity, valuation, FEFO lots, man-hours, SME SQM) · **UOM-conversion integrity** (no zero/NULL factors, no duplicate `SAP+From_UOM` mappings, orphan SAPs listed) · soft-FK orphan scan (ledger→inventory, po_items→PO, dn_items→DN, smr_items→SMR) — advisory unless `--strict` because the legacy ledger never enforced them. |

Exit code `0` only when every blocking check passes.

## Cutover-day runbook

1. **Freeze legacy writes** — stop the Streamlit app (and the legacy WhatsApp
   worker if running): `pkill -f streamlit` on the host.
2. **Final backup** of the source:
   `cp gi_database.db gi_database.cutover-$(date +%Y%m%d).db`
3. **Provision the target** (Hetzner `deploy/` kit): `docker compose up -d db`
   — an EMPTY `gihub` database.
4. **Run the migration** (from the repo root, venv active):

   ```bash
   .venv/bin/python scripts/migration/cutover_migrate.py \
       --source gi_database.db \
       --target postgresql+psycopg2://gihub:<pw>@localhost:5432/gihub \
       --wipe
   ```

5. **Manual follow-ups** (the script reminds you):
   - `psql … -f scripts/create_ai_readonly_role.sql` (re-run after ANY reload —
     the REVOKEs are wiped by a wipe-load),
   - `VACUUM ANALYZE;`,
   - confirm `deploy/.env` secrets on the server (`JWT_SECRET`, `WHATSAPP_*`,
     `SMTP_*`, `EMAIL_LOGISTICS_TO`).
6. **Point the API at the target** (`DATABASE_URL` in `deploy/.env`), start the
   stack, and run the smoke gates against production:

   ```bash
   DATABASE_URL=… JWT_SECRET=… .venv/bin/python -m backend.api.service_tests
   DATABASE_URL=… JWT_SECRET=… .venv/bin/python -m backend.api.parity_check
   ```

7. **Re-verify any time** without reloading:

   ```bash
   .venv/bin/python scripts/migration/cutover_migrate.py --verify-only \
       --source gi_database.cutover-YYYYMMDD.db --target postgresql+psycopg2://…
   ```

## Notes

- The script **never writes to the source** SQLite file (opened read-only).
- New-stack-only tables (`auth_sessions`, `ai_jobs`, `whatsapp_outbox`,
  `email_outbox`, `phone_otp`, `sla_dismissals`, `mh_*`) are created empty —
  they have no SQLite counterpart by design.
- Legacy SQLite **views are not migrated** to Postgres: the FastAPI layer
  computes those aggregations itself (`backend/api/stock.py` — parity-checked
  against the SQLite views by `backend.api.parity_check`).
- SME S6 (master-data CRUD) remains a separate cutover-day work item; this
  script moves the data either way (sme_* tables are ordinary tables).
