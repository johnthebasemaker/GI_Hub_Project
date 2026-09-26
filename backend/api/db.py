"""
backend/api/db.py — async SQLAlchemy engine + session dependency.

Uses AsyncSession with a pooled asyncpg engine (architecture rule #5). The engine
is created once at import; connections are lazy, so importing this module never
requires Postgres to be up (a dead DB surfaces at /health, not at startup).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import assert_instance_safe, async_database_url

# Rule 17: a process whose GI_INSTANCE and database disagree never gets as far as
# holding an engine. This line must stay ABOVE create_async_engine — the same
# import-order property rule 15's testdb relies on, used here as a refusal.
assert_instance_safe()

engine = create_async_engine(
    async_database_url(),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # transparently recycle stale connections
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a request-scoped AsyncSession."""
    async with SessionLocal() as session:
        yield session
