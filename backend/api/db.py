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

from .config import async_database_url

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
