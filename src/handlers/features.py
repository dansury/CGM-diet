"""Рассказ о неиспользованных возможностях и скрытое меню (`spec/features.md`).

Не чаще раза в неделю и не в первую неделю знакомства — ровно одна возможность,
о которой человек ещё ничего не знает, выбранная под его цели. «Не нужно»
уносит её из меню навсегда; работать она не перестаёт — `/hidden` возвращает.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommandScopeChat,
    CallbackQuery,
    Message,
    ReplyKeyboardMarkup,
)

from src import features, goals
from src.db import repo
from src.handlers.deps import session_scope
from src.keyboards import feature_hint, hidden_features, main_menu
from src.logging_setup import get_logger
from src.reporting import format_feature_hint, format_hidden_list

router = Router(name="features")
log = get_logger("handlers.features")

ACCEPTED = "👍 Хорошо. Возможность на месте — команда {command} всегда под рукой."
ACCEPTED_PLAIN = "👍 Хорошо, больше об этом не напомню."
DECLINED = (
    "🚫 Убрал «{title}» из меню и больше не напомню.\n"
    "Если понадобится — она в /hidden."
)
RESTORED = "↩️ Вернул «{title}» в меню."


async def menu_for(session, user) -> ReplyKeyboardMarkup:
    """Клавиатура пользователя: без того, от чего он отказался."""
    hidden = await repo.hidden_features(session, user)
    return main_menu(hidden)


async def menu_of(chat_id: int) -> ReplyKeyboardMarkup:
    """То же самое, когда сессии под рукой нет. Fail-soft: сбой чтения даёт
    полное меню, а не отсутствие клавиатуры."""
    try:
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, chat_id)
            return await menu_for(session, user)
    except Exception:  # noqa: BLE001
        log.warning("could not build menu for %s", chat_id, exc_info=True)
        return main_menu()


async def mark_used(chat_id: int, key: str) -> None:
    """Отметить обращение к возможности, у которой нет своей строки в БД.

    Fail-soft: подсказки — сервис, их учёт не может ронять сам отчёт.
    """
    try:
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, chat_id)
            await repo.mark_feature_used(session, user, key)
    except Exception:  # noqa: BLE001
        log.warning("could not mark feature %s as used", key, exc_info=True)


async def sync_commands(bot: Bot, chat_id: int, hidden: set[str]) -> None:
    """Убрать/вернуть команды скрытых возможностей в меню Telegram.

    Полностью fail-soft: меню команд — украшение, его сбой не должен ронять
    обработчик, который человек только что нажал.
    """
    from src.bot import COMMANDS

    skip = {
        features.BY_KEY[key].command.lstrip("/")
        for key in hidden
        if key in features.BY_KEY and features.BY_KEY[key].command
    }
    visible = [command for command in COMMANDS if command.command not in skip]
    try:
        await bot.set_my_commands(visible, scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception:  # noqa: BLE001 — меню команд не стоит упавшего апдейта
        log.warning("could not sync command menu for %s", chat_id, exc_info=True)


async def maybe_send_hint(bot: Bot, chat_id: int, *, at: datetime | None = None) -> str | None:
    """Отправить одну подсказку, если есть о чём рассказать.

    Возвращает ключ возможности или `None`. Первой идёт та, что служит целям,
    названным при знакомстве (`spec/onboarding.md` § Цели). Отметку о показе
    ставим до отправки: повторный тик не должен слать второе сообщение о том же.
    `at` — момент тика: недельный интервал считается по нему, а не по часам
    процесса.
    """
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, chat_id)
        totals = await repo.counts(session, user)
        states = await repo.feature_states(session, user)
        profile = await repo.get_body_profile(session, user)
        priority = goals.feature_order(goals.decode(profile.focus if profile else None))
        feature = features.pick_hint(totals, states, priority=priority)
        if feature is None:
            return None
        await repo.mark_feature_shown(session, user, feature.key, at=at)
    try:
        await bot.send_message(
            chat_id,
            format_feature_hint(feature),
            reply_markup=feature_hint(feature.key),
        )
    except Exception:  # noqa: BLE001 — заблокированный чат не должен шуметь
        log.warning("feature hint not delivered to %s", chat_id, exc_info=True)
        return None
    return feature.key


@router.callback_query(F.data.startswith("feat:ok:"))
async def on_accept(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    feature = features.get(key)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.set_feature_status(session, user, key, features.STATUS_ACCEPTED)
    await callback.answer("Отлично")
    if callback.message is not None:
        text = (
            ACCEPTED.format(command=feature.command)
            if feature and feature.command
            else ACCEPTED_PLAIN
        )
        await callback.message.edit_text(text)


@router.callback_query(F.data.startswith("feat:no:"))
async def on_decline(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    feature = features.get(key)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.set_feature_status(session, user, key, features.STATUS_DECLINED)
        hidden = await repo.hidden_features(session, user)
    await callback.answer("Скрыл")
    if callback.message is not None:
        await callback.message.edit_text(
            DECLINED.format(title=feature.title if feature else key)
        )
        await callback.message.answer("Меню обновил.", reply_markup=main_menu(hidden))
    if callback.bot is not None:
        await sync_commands(callback.bot, callback.from_user.id, hidden)


@router.callback_query(F.data.startswith("feat:show:"))
async def on_restore(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    feature = features.get(key)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.set_feature_status(session, user, key, features.STATUS_ACCEPTED)
        hidden = await repo.hidden_features(session, user)
    await callback.answer("Вернул")
    if callback.message is not None:
        await callback.message.edit_text(RESTORED.format(title=feature.title if feature else key))
        await callback.message.answer("Меню обновил.", reply_markup=main_menu(hidden))
    if callback.bot is not None:
        await sync_commands(callback.bot, callback.from_user.id, hidden)


@router.callback_query(F.data == "feat:close")
async def on_close(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_text("Хорошо.")


@router.message(Command("hidden"))
async def cmd_hidden(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
        hidden = await repo.hidden_features(session, user)
    items = [features.BY_KEY[key] for key in hidden if key in features.BY_KEY]
    if not items:
        await message.answer(format_hidden_list([]))
        return
    await message.answer(format_hidden_list(items), reply_markup=hidden_features(items))


__all__ = [
    "mark_used",
    "maybe_send_hint",
    "menu_for",
    "menu_of",
    "router",
    "sync_commands",
]
