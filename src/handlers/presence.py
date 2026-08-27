"""Passive sleep watch: remember when the person shows up in the chat.

The Telegram Bot API gives a bot **no** access to a user's online status or
“last seen” — no privacy setting unlocks it, because the field is never sent to
bots at all. So the only honest signal is an update the person actually sent
us: a message, a button press, an edit. That is what this middleware records,
and `analytics.sleep` turns the shape of those appearances into nights.

Two guards keep it cheap and quiet:

* an in-process throttle, so at most one write per user per
  `repo.PRESENCE_MIN_GAP_MIN` minutes;
* the option itself — nothing is stored for a user who has not switched the
  sleep watch on (`users.sleep_presence_enabled`).

See `spec/sleep.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from src.db import repo
from src.handlers.deps import session_scope
from src.logging_setup import get_logger

log = get_logger("handlers.presence")

#: tg_id -> when we last touched the DB for them
_seen: dict[int, datetime] = {}
#: The throttle is a convenience, not state: drop it whole rather than grow.
MAX_CACHED_USERS = 10_000


def reset_presence_cache() -> None:
    """Tests and a restarted dispatcher start from a clean throttle."""
    _seen.clear()


def _from_user_id(event: TelegramObject) -> int | None:
    inner = getattr(event, "event", None) if isinstance(event, Update) else event
    user = getattr(inner, "from_user", None)
    return getattr(user, "id", None)


async def record_presence(tg_id: int, *, at: datetime | None = None) -> bool:
    """Store one appearance if the option is on and the throttle allows it."""
    moment = at or datetime.now(UTC)
    last = _seen.get(tg_id)
    if last is not None and moment - last < timedelta(minutes=repo.PRESENCE_MIN_GAP_MIN):
        return False
    if len(_seen) >= MAX_CACHED_USERS:
        _seen.clear()
    _seen[tg_id] = moment
    async with session_scope() as session:
        user = await repo.get_user(session, tg_id)
        # No get_or_create here: a bot that has never met this person must not
        # start a profile just to log that they exist.
        if user is None or not user.sleep_presence_enabled:
            return False
        return await repo.save_presence(session, user, at=moment)


class PresenceMiddleware(BaseMiddleware):
    """Outer middleware on `dispatcher.update`; never blocks the handler."""

    async def __call__(self, handler: Any, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_id = _from_user_id(event)
        if tg_id is not None:
            try:
                await record_presence(tg_id)
            except Exception:  # a presence write may never break an update
                log.warning("presence not recorded for %s", tg_id, exc_info=True)
        return await handler(event, data)


__all__ = ["MAX_CACHED_USERS", "PresenceMiddleware", "record_presence", "reset_presence_cache"]
