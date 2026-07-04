"""Alembic environment for the new-stack Postgres.

target_metadata is backend/models.py's Base.metadata — the 64 real tables. The
SME/derived VIEWs are NOT modelled here (they live in models.SME_AND_DERIVED_VIEWS
and are created by dual_ci), so autogenerate leaves them alone. Alembic takes
over AFTER the initial dual_ci load, for incremental schema changes.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the `backend` package importable (env.py is backend/alembic/env.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend import models  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = models.Base.metadata


def _sync_url() -> str:
    """DATABASE_URL normalised to the sync psycopg2 driver (Alembic is sync)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        url = "postgresql+psycopg2://postgres@127.0.0.1:5433/gihub"
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
