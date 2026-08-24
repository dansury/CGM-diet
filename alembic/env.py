"""Alembic env — async-aware, reads DATABASE_URL from the environment."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from src.config import Settings, ensure_sqlite_parent_dir, normalize_database_url
from src.db import models  # noqa: F401  -- registers tables on the metadata
from src.db.base import metadata as target_metadata

try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass


def _database_url() -> str:
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    return normalize_database_url(raw) if raw else Settings().database_url


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", _database_url())

ensure_sqlite_parent_dir(config.get_main_option("sqlalchemy.url") or "")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
