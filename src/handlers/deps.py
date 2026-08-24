"""Shared handler plumbing: sessions, time zones, downloads, album buffering."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.engine import get_sessionmaker
from src.db.models import User
from src.llm import ImagePart
from src.logging_setup import get_logger

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None  # type: ignore[assignment]

log = get_logger("handlers.deps")

MAX_PHOTO_BYTES = 12 * 1024 * 1024
# How long to wait for the rest of a Telegram album before processing it.
ALBUM_DEBOUNCE_SEC = 1.2


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """One transaction per handler; commit on success, rollback on failure."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def user_tz(user: User | None):
    if user is None or ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(user.tz)
    except Exception:
        return UTC


def local_now(user: User | None) -> datetime:
    return datetime.now(tz=user_tz(user))


def to_utc(value: datetime, user: User | None) -> datetime:
    """Interpret a naive datetime as the user's local time, return UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=user_tz(user))
    return value.astimezone(UTC)


def to_local(value: datetime, user: User | None) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(user_tz(user))


async def download_photo(bot: Bot, file_id: str) -> ImagePart:
    """Fetch a Telegram photo into memory as an `ImagePart`."""
    buffer = await bot.download(file_id)
    data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("файл слишком большой")
    return ImagePart(data=data, mime="image/jpeg")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class AlbumBuffer:
    """Collects the photos of one Telegram album before handling it.

    Telegram delivers an album as N separate updates. Two-sided label scans rely
    on that: front and back must reach the model in the same call. Each group is
    flushed once, `ALBUM_DEBOUNCE_SEC` after its last photo.
    """

    debounce: float = ALBUM_DEBOUNCE_SEC
    _groups: dict[str, list] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    def add(
        self,
        group_id: str,
        message,
        flush: Callable[[str, list], Awaitable[None]],
    ) -> None:
        self._groups.setdefault(group_id, []).append(message)
        task = self._tasks.get(group_id)
        if task is not None:
            task.cancel()
        self._tasks[group_id] = asyncio.create_task(self._flush_later(group_id, flush))

    async def _flush_later(self, group_id: str, flush) -> None:
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        messages = self._groups.pop(group_id, [])
        self._tasks.pop(group_id, None)
        if messages:
            try:
                await flush(group_id, messages)
            except Exception:  # a broken album must not kill the dispatcher
                log.exception("album flush failed for %s", group_id)


album_buffer = AlbumBuffer()


__all__ = [
    "ALBUM_DEBOUNCE_SEC",
    "AlbumBuffer",
    "album_buffer",
    "download_photo",
    "local_now",
    "session_scope",
    "sha256",
    "to_local",
    "to_utc",
    "user_tz",
]
