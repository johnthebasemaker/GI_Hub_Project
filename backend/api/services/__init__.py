"""
backend.api.services — the ledger "services layer".

Transactional write operations that port the Streamlit app's business rules
(from database.py) to async SQLAlchemy over Postgres: goods receipt, consumption
/ issue (FEFO), returns, stock adjustments, lot lifecycle. Each service writes
atomically and appends to system_audit_log, mirroring the old app so results
stay identical (see backend/api/parity_check.py for the accuracy gate).

Routers (backend/api/entry.py, …) call these; they never embed business rules.
"""
