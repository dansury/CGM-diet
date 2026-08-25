"""Owner registry: `/users` — who uses the bot and how actively.

Ported from GrowthProducer (`handlers/admin_panel.py` § /users), trimmed to what
this bot actually stores: the user row plus meal/glucose counters. Same owner
gate as `handlers/admin.py` — a non-owner does not match the router, so the
update falls through and the command stays invisible. See `spec/bot.md`
§ Реестр пользователей.
"""

from __future__ import annotations

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config import load_settings
from src.db import repo
from src.handlers.deps import session_scope
from src.logging_setup import get_logger

router = Router(name="admin_users")
log = get_logger("handlers.admin_users")

USERS_LIMIT = 50
MESSAGE_LIMIT = 4000


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and load_settings().is_owner(user_id)


router.message.filter(
    F.chat.type == "private",
    F.func(lambda event: _is_owner(getattr(event.from_user, "id", None))),
)


def user_label(tg_id: int, username: str | None, first_name: str | None) -> str:
    """`<b>Имя</b> · @username · <code>id</code>` — used here and in the alerts."""
    parts = []
    if first_name:
        parts.append(f"<b>{html.escape(first_name)}</b>")
    if username:
        parts.append(f"@{html.escape(username)}")
    parts.append(f"<code>{tg_id}</code>")
    return " · ".join(parts)


def _stamp(value: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    return value.strftime(fmt) if isinstance(value, datetime) else "—"


async def render_users(limit: int = USERS_LIMIT) -> str:
    async with session_scope() as session:
        total, blocked = await repo.count_users(session)
        users = await repo.list_users(session, limit=limit)
        activity = await repo.user_activity(session)

    head = f"👥 <b>Пользователи ({total})</b>"
    if blocked:
        head += f" · 🚫 заблокировали: {blocked}"
    lines = [head, ""]
    if not users:
        lines.append("Пока никого — ждём первых /start.")
        return "\n".join(lines)
    if total > len(users):
        lines.append(f"Показаны последние {len(users)}.")
        lines.append("")
    for index, user in enumerate(users, 1):
        meals, readings, last_record = activity.get(user.id, (0, 0, None))
        mark = " 🚫" if user.blocked_at else ""
        onboarded = "" if user.onboarded else " · без анкеты"
        lines.append(
            f"{index}. {user_label(user.tg_id, user.username, user.first_name)}{mark}"
            f" · с {_stamp(user.created_at, '%d.%m.%Y')}{onboarded}"
        )
        lines.append(
            f"   🍽 {meals} · 🩸 {readings} · запись: {_stamp(last_record)}"
            f" · был(а): {_stamp(user.last_seen_at)}"
        )
    return "\n".join(lines)


@router.message(Command("users"))
async def show_users(message: Message) -> None:
    try:
        text = await render_users()
    except Exception as exc:  # the owner sees why, the bot keeps running
        log.exception("users listing failed")
        text = f"❌ Не удалось получить список: {html.escape(str(exc))}"
    await message.answer(text[:MESSAGE_LIMIT])


__all__ = ["render_users", "router", "show_users", "user_label"]
