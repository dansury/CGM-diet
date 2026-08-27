"""Two middlewares that write the conversation down for `/last_msg_…`.

Incoming updates go through an outer middleware, outgoing ones through a
session middleware on the Bot — that is the only place where every
`send_message` is seen, whatever handler produced it.

Only the text and the inline buttons are stored: a photo, a voice message or a
document leaves a type mark and its caption, never the content
(`spec/bot.md` § Журнал переписки). Both paths are fail-soft — a broken log
line has no right to swallow an update or to stop a message from being sent.
"""

from __future__ import annotations

from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from src.db import repo
from src.handlers.deps import session_scope
from src.logging_setup import get_logger

log = get_logger("handlers.message_log")

#: методы Bot API, которые пишем в журнал: имя метода -> вид записи
_OUTGOING_KINDS = {
    "SendMessage": "text",
    "SendPhoto": "photo",
    "SendDocument": "document",
}


def serialize_markup(markup: Any) -> list | None:
    """Инлайн-клавиатура → `[[{"t": подпись, "cb"|"url": значение}]]`."""
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return None
    out: list[list[dict[str, str]]] = []
    for row in rows:
        line: list[dict[str, str]] = []
        for button in row:
            item = {"t": getattr(button, "text", "") or ""}
            if getattr(button, "callback_data", None):
                item["cb"] = button.callback_data
            elif getattr(button, "url", None):
                item["url"] = button.url
            line.append(item)
        if line:
            out.append(line)
    return out or None


def _incoming(event: Message | CallbackQuery) -> tuple[str, str] | None:
    """Вид записи и текст входящего апдейта; `None` — писать нечего.

    Колбэк отличается от сообщения отсутствием собственного чата — тем же
    признаком, что и в `handlers/user_tracking.py`.
    """
    if not hasattr(event, "chat"):
        return "callback", getattr(event, "data", "") or ""
    if event.text is not None:
        return "text", event.text
    caption = event.caption or ""
    if event.photo:
        return "photo", caption
    if event.voice or event.audio:
        return "voice", caption
    if event.document:
        return "document", caption
    return None


async def record(
    tg_id: int, *, direction: str, kind: str, text: str, buttons: list | None = None
) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        await repo.log_message(
            session, user, direction=direction, kind=kind, text=text, buttons=buttons
        )


async def write_incoming(event: Message | CallbackQuery) -> None:
    """Одна строка про входящий апдейт; чужой чат и служебное — мимо."""
    user = event.from_user
    if user is None or getattr(user, "is_bot", False):
        return
    chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
    if chat is None or getattr(chat, "type", None) != "private":
        return
    parsed = _incoming(event)
    if parsed is None:
        return
    kind, text = parsed
    await record(user.id, direction="in", kind=kind, text=text)


class LogIncomingMiddleware(BaseMiddleware):
    """Outer middleware: что человек прислал боту. Апдейт не задерживает."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):  # type: ignore[no-untyped-def]
        if isinstance(event, (Message, CallbackQuery)):
            try:
                await write_incoming(event)
            except Exception:
                log.exception("incoming message log failed")
        return await handler(event, data)


async def write_outgoing(method: Any) -> None:
    """Одна строка про исходящий вызов Bot API.

    Только личные чаты (`chat_id > 0`): групповые сообщения к переписке с
    пользователем не относятся.
    """
    kind = _OUTGOING_KINDS.get(type(method).__name__)
    if kind is None:
        return
    try:
        chat_id = int(getattr(method, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        return
    if chat_id <= 0:
        return
    text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
    await record(
        chat_id,
        direction="out",
        kind=kind,
        text=text,
        buttons=serialize_markup(getattr(method, "reply_markup", None)),
    )


class LogOutgoingMiddleware:
    """Session middleware бота: что бот ответил. Отправку не задерживает."""

    async def __call__(self, make_request, bot, method):  # type: ignore[no-untyped-def]
        response = await make_request(bot, method)
        try:
            await write_outgoing(method)
        except Exception:
            log.exception("outgoing message log failed")
        return response


def register(dispatcher: Any, bot: Any = None) -> None:
    """Обе точки записи; `bot=None` — только входящие (полезно в тестах)."""
    dispatcher.message.outer_middleware(LogIncomingMiddleware())
    dispatcher.callback_query.outer_middleware(LogIncomingMiddleware())
    if bot is not None:
        bot.session.middleware(LogOutgoingMiddleware())


__all__ = [
    "LogIncomingMiddleware",
    "LogOutgoingMiddleware",
    "record",
    "register",
    "serialize_markup",
    "write_incoming",
    "write_outgoing",
]
