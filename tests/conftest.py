"""Shared fixtures: in-memory DB, mock LLM, deterministic settings."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

os.environ.setdefault("LLM_MOCK", "true")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from src.config import load_settings, reset_cache  # noqa: E402
from src.db import models  # noqa: E402,F401
from src.db.base import Base  # noqa: E402
from src.db.engine import create_sessionmaker, reset_engine  # noqa: E402
from src.llm import reset_client  # noqa: E402
from src.llm.mock import MockClient  # noqa: E402


@pytest.fixture(autouse=True)
def _settings():
    reset_cache()
    settings = load_settings(refresh=True)
    yield settings
    reset_cache()


@pytest.fixture(autouse=True)
def mock_llm():
    client = MockClient()
    reset_client(client)
    yield client
    reset_client(None)


@pytest_asyncio.fixture
async def engine():
    """A fresh in-memory database per test, wired into the global accessors."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    reset_engine(eng)
    yield eng
    reset_engine(None)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    factory = create_sessionmaker(engine)
    async with factory() as s:
        yield s
        await s.commit()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
