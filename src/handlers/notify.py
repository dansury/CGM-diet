"""Reminders as the person wants them: `/notify` and the buttons under a
delivered notification.

The owner's template is where every notification starts; anything chosen here
wins over it, and «умное» reads the time out of this person's own records.
Routing only — the schedule maths lives in `src/analytics/notify.py`, the
wording in `src/reporting.py` (`spec/notifications.md`).
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.analytics import notify as notify_math
from src.db import repo
from src.handlers.deps import local_now, session_scope
from src.handlers.states import NotifyFlow
from src.keyboards import cancel_only, notification_list, notification_modes
from src.logging_setup import get_logger
from src.reporting import (
    NOTIFY_EMPTY,
    NOTIFY_INTRO,
    NOTIFY_PHOTO_HINT,
    NOTIFY_REPLY_HINT,
    NOTIFY_SAVED,
    NOTIFY_SMART_THIN,
    NOTIFY_TIME_ASK,
    NOTIFY_TIME_BAD,
    format_notification_card,
    notification_summary,
)

router = Router(name="notify")
log = get_logger("handlers.notify")


async def smart_for(session, user, template: notify_math.Template) -> tuple[str, ...]:
    """«Умное» время этого человека для этого уведомления.

    Пустой ответ — записей ещё мало; вызывающий берёт время шаблона, а не
    выдумывает привычку по трём точкам.
    """
    if not template.signal:
        return ()
    now = local_now(user)
    since = now - timedelta(days=notify_math.WINDOW_DAYS)
    moments = await repo.notification_signal_times(session, user, template.signal, since=since)
    slots = user.meals_per_day if "meal" in template.signal else None
    slots = slots or len(template.times) or notify_math.DEFAULT_SLOTS
    return notify_math.smart_times(
        notify_math.recent_minutes(moments, now=now),
        slots=slots,
        lead_min=template.lead_min,
    )


async def _schedules(session, user) -> list[tuple[notify_math.Template, notify_math.Schedule]]:
    templates = await repo.list_notification_templates(session, only_enabled=True)
    prefs = await repo.notification_prefs(session, user)
    out = []
    for row in templates:
        template = repo.template_of(row)
        pref = repo.pref_of(prefs.get(row.code))
        smart = ()
        if (pref and pref.mode == "smart") or (
            (pref is None or pref.mode == "default") and template.mode == "smart"
        ):
            smart = await smart_for(session, user, template)
        out.append((template, notify_math.resolve(template, pref, smart=smart)))
    return out


@router.message(Command("notify"))
async def cmd_notify(message: Message) -> None:
    await _render_list(message)


async def _render_list(message: Message, *, edit: bool = False) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.seed_notifications(session)
        pairs = await _schedules(session, user)
    if not pairs:
        await message.answer(NOTIFY_EMPTY)
        return
    rows = [
        (template.code, template.title, notification_summary(schedule.mode, schedule.times))
        for template, schedule in pairs
    ]
    markup = notification_list(rows)
    if edit:
        await message.edit_text(NOTIFY_INTRO, reply_markup=markup)
    else:
        await message.answer(NOTIFY_INTRO, reply_markup=markup)


@router.callback_query(F.data == "ntf:list")
async def on_back_to_list(callback: CallbackQuery) -> None:
    await callback.answer()
    await _render_list(callback.message, edit=True)


@router.callback_query(F.data == "ntf:close")
async def on_close(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Напоминания закрыты. Открыть снова — /notify")


@router.callback_query(F.data.startswith("ntf:open:"))
async def on_open(callback: CallbackQuery) -> None:
    code = callback.data.rsplit(":", 1)[1]
    await callback.answer()
    await _render_card(callback.message, code, edit=True)


async def _render_card(message: Message, code: str, *, edit: bool = False) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        row = await repo.get_notification_template(session, code)
        if row is None:
            await message.answer(NOTIFY_EMPTY)
            return
        template = repo.template_of(row)
        prefs = await repo.notification_prefs(session, user)
        pref = repo.pref_of(prefs.get(code))
        smart = await smart_for(session, user, template) if template.signal else ()
        schedule = notify_math.resolve(template, pref, smart=smart)
    mode = pref.mode if pref is not None else "default"
    text = format_notification_card(
        template.title,
        template.body,
        mode=mode,
        times=schedule.times,
        admin_times=template.times,
    )
    if mode == "smart" and not smart:
        text += "\n\n" + NOTIFY_SMART_THIN
    markup = notification_modes(code, mode)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ntf:mode:"))
async def on_mode(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, code, mode = callback.data.split(":", 3)
    if mode not in notify_math.USER_MODES:
        await callback.answer()
        return
    if mode == "fixed":
        # Своё время невозможно выбрать кнопкой — его нужно назвать.
        await state.set_state(NotifyFlow.awaiting_time)
        await state.update_data({"ntf_code": code})
        await callback.answer()
        await callback.message.answer(NOTIFY_TIME_ASK, reply_markup=cancel_only())
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.set_notification_pref(session, user, code, mode=mode, times="")
    await callback.answer(NOTIFY_SAVED)
    await _render_card(callback.message, code, edit=True)


@router.message(NotifyFlow.awaiting_time)
async def on_time_typed(message: Message, state: FSMContext) -> None:
    times = notify_math.parse_times(message.text or "")
    if not times:
        await message.answer(NOTIFY_TIME_BAD, reply_markup=cancel_only())
        return
    data = await state.get_data()
    code = str(data.get("ntf_code") or "")
    await state.clear()
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.set_notification_pref(
            session, user, code, mode="fixed", times=",".join(times)
        )
    await message.answer(NOTIFY_SAVED)
    await _render_card(message, code)


# ------------------------------------------------- кнопки под уведомлением

@router.callback_query(F.data.startswith("ntf:cam:"))
async def on_answer_photo(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(NOTIFY_PHOTO_HINT)


@router.callback_query(F.data.startswith("ntf:txt:"))
async def on_answer_text(callback: CallbackQuery) -> None:
    """«Ответить текстом»: дальше работает обычный разбор ввода, а он уже
    заглядывает в «мои блюда» раньше модели (`spec/dictionary.md`)."""
    await callback.answer()
    await callback.message.answer(NOTIFY_REPLY_HINT)


@router.callback_query(F.data == "ntf:dismiss")
async def on_dismiss(callback: CallbackQuery) -> None:
    await callback.answer("Хорошо")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # сообщение могло быть уже изменено — это не ошибка
        log.debug("notification markup already gone", exc_info=True)


__all__ = ["cmd_notify", "router", "smart_for"]
