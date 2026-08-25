"""Registry upserts and owner alerts about newcomers and blocks.

Ported from GrowthProducer (`handlers/user_tracking.py`). Two paths:

- `UserTrackingMiddleware` (outer, message + callback_query): every private-chat
  update refreshes the registry row; a NEW row triggers an owner DM
  «🆕 Новый пользователь». Fail-soft — a broken alert never blocks the update.
- `my_chat_member` in a private chat: `kicked` means the user blocked the bot,
  `member` means they came back — both are written to the row and DM'd to the
  owner.

Writes are throttled to one per user per hour unless the visible profile
changed, so the middleware does not turn every message into an UPDATE.
See `spec/bot.md` § Реестр пользователей.
"""

from __future__ import annotations

import time
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject

from src.config import load_settings
from src.db import repo
from src.handlers.admin_users import user_label
from src.handlers.deps import session_scope
from src.logging_setup import get_logger

log = get_logger("handlers.user_tracking")

router = Router(name="user_tracking")

#: skip the refresh write when the same user was seen this recently and nothing
#: visible about them changed
SEEN_TTL_SEC = 3600
# tg_id -> (monotonic ts, username, first_name)
_seen: dict[int, tuple[float, str | None, str | None]] = {}


def reset_seen_cache() -> None:
    """Test hook: forget the write throttle."""
    _seen.clear()


def _owner_ids() -> tuple[int, ...]:
    try:
        return load_settings().owner_tg_ids
    except Exception:
        return ()


async def notify_owners(bot: Any, text: str, *, skip: int | None = None) -> None:
    for owner_id in _owner_ids():
        if owner_id == skip:
            continue
        try:
            await bot.send_message(chat_id=owner_id, text=text)
        except Exception:
            log.warning("owner notification failed for %s", owner_id, exc_info=True)


async def track(event: Message | CallbackQuery, bot: Any) -> None:
    user = event.from_user
    if user is None or user.is_bot:
        return
    # a Message carries the chat itself, a CallbackQuery only through its message
    chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
    if chat is None or getattr(chat, "type", None) != "private":
        return

    username = (user.username or "").strip().lstrip("@") or None
    first_name = (user.first_name or "").strip() or None
    cached = _seen.get(user.id)
    now = time.monotonic()
    if (
        cached is not None
        and now - cached[0] < SEEN_TTL_SEC
        and cached[1] == username
        and cached[2] == first_name
    ):
        return

    async with session_scope() as session:
        _, is_new = await repo.touch_user(
            session, user.id, username=username, first_name=first_name
        )
    _seen[user.id] = (now, username, first_name)
    if not is_new or bot is None:
        return
    log.info("new user %s (@%s)", user.id, username)
    await notify_owners(
        bot,
        "🆕 <b>Новый пользователь</b>\n"
        f"{user_label(user.id, username, first_name)}\n\n"
        "Все пользователи — /users",
        skip=user.id,
    )


class UserTrackingMiddleware(BaseMiddleware):
    """Outer middleware: registry upsert + new-user alert. Never blocks updates."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):  # type: ignore[no-untyped-def]
        if isinstance(event, (Message, CallbackQuery)):
            try:
                await track(event, data.get("bot") or getattr(event, "bot", None))
            except Exception:
                log.exception("user tracking failed")
        return await handler(event, data)


@router.my_chat_member(F.chat.type == "private")
async def on_private_my_chat_member(event: ChatMemberUpdated) -> None:
    member = event.new_chat_member
    if member is None:
        return
    status = str(getattr(member, "status", ""))
    if status not in {"kicked", "member"}:
        return
    blocked = status == "kicked"
    user = event.from_user
    tg_id = user.id if user is not None else event.chat.id
    username = user.username if user is not None else None
    first_name = user.first_name if user is not None else None

    try:
        async with session_scope() as session:
            await repo.set_user_blocked(session, tg_id, blocked)
    except Exception:
        log.warning("could not mark block state for %s", tg_id, exc_info=True)
    _seen.pop(tg_id, None)

    headline = (
        "🚫 <b>Пользователь заблокировал бота</b>"
        if blocked
        else "✅ <b>Пользователь разблокировал бота</b>"
    )
    await notify_owners(
        event.bot,
        f"{headline}\n{user_label(tg_id, username, first_name)}",
        skip=tg_id,
    )


def register(dispatcher: Any) -> None:
    """Router + outer middlewares on the dispatcher (called from `build_dispatcher`)."""
    dispatcher.include_router(router)
    dispatcher.message.outer_middleware(UserTrackingMiddleware())
    dispatcher.callback_query.outer_middleware(UserTrackingMiddleware())


__all__ = [
    "SEEN_TTL_SEC",
    "UserTrackingMiddleware",
    "notify_owners",
    "on_private_my_chat_member",
    "register",
    "reset_seen_cache",
    "router",
    "track",
]
