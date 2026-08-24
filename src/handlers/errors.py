"""Catch-all for exceptions raised inside any handler.

The user is told one sentence — their data is safe, the owner already knows.
The owner gets the whole thing (`spec/errors.md`).
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import ErrorEvent, Update

from src.errors_report import report_error
from src.logging_setup import get_logger

router = Router(name="errors")
log = get_logger("handlers.errors")

USER_MESSAGE = (
    "⚠️ Что-то пошло не так. Ваши записи целы — мы уже видим ошибку и чиним.\n"
    "Попробуйте ещё раз через минуту или пришлите данные иначе (фото/текст)."
)

PREVIEW_LIMIT = 200


def _describe(update: Update | None) -> tuple[str, str | None, dict[str, str]]:
    """Where it happened, who hit it, and just enough of the payload to reproduce."""
    context: dict[str, str] = {}
    if update is None:
        return "unknown", None, context
    message = update.message or update.edited_message
    callback = update.callback_query
    from_user = None
    if message is not None:
        from_user = message.from_user
        context["update"] = "message"
        context["chat"] = f"{message.chat.id} ({message.chat.type})"
        if message.text:
            context["text"] = message.text[:PREVIEW_LIMIT]
        elif message.caption:
            context["caption"] = message.caption[:PREVIEW_LIMIT]
        elif message.photo:
            context["payload"] = "photo"
        elif message.voice or message.audio:
            context["payload"] = "voice"
        elif message.document:
            context["payload"] = f"document {message.document.mime_type}"
    elif callback is not None:
        from_user = callback.from_user
        context["update"] = "callback_query"
        context["data"] = (callback.data or "")[:PREVIEW_LIMIT]
    else:
        context["update"] = update.event_type or "unknown"
    user = None
    if from_user is not None:
        user = f"{from_user.id} (@{from_user.username})" if from_user.username else str(from_user.id)
    return context.get("update", "unknown"), user, context


async def _reply(event: ErrorEvent) -> None:
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer("Ошибка, уже разбираемся", show_alert=False)
            if update.callback_query.message is not None:
                await update.callback_query.message.answer(USER_MESSAGE)
            return
        message = update.message or update.edited_message
        if message is not None:
            await message.answer(USER_MESSAGE)
    except Exception:  # the apology itself failing must not mask the real error
        log.warning("could not deliver the error notice to the user")


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exception = event.exception
    handler = getattr(event, "handler", None)
    where = "handler"
    callback = getattr(handler, "callback", None) if handler is not None else None
    if callback is not None:
        where = f"{getattr(callback, '__module__', '?')}.{getattr(callback, '__name__', '?')}"
    else:
        kind, _user, _ctx = _describe(event.update)
        where = f"update.{kind}"
    _kind, user, context = _describe(event.update)
    log.exception("unhandled error in %s", where, exc_info=exception)
    await _reply(event)
    await report_error(source="bot", where=where, exc=exception, user=user, context=context)
    return True  # handled: aiogram must not re-log the traceback


__all__ = ["USER_MESSAGE", "on_error", "router"]
