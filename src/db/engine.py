"""Async engine + sessionmaker. See `spec/infra.md` § Database."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import Settings, ensure_sqlite_parent_dir, load_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    s = settings or load_settings()
    ensure_sqlite_parent_dir(s.database_url)
    return create_async_engine(s.database_url, future=True, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = create_sessionmaker(get_engine())
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine(engine: AsyncEngine | None = None) -> None:
    """Test hook: drop globals; optionally inject a pre-built engine."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = create_sessionmaker(engine) if engine is not None else None


__all__ = [
    "create_engine",
    "create_sessionmaker",
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "reset_engine",
]
