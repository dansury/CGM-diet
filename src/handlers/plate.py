"""Гарвардская тарелка: оценка приёма пищи и её настройки (`spec/plate.md`).

Обработчик только маршрутизирует: вся арифметика — в `src/analytics/plate.py`,
все формулировки — в `src/reporting.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics import plate as plate_math
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now, session_scope, to_utc, user_tz
from src.keyboards import plate_meals_picker, plate_settings
from src.logging_setup import get_logger
from src.reporting import format_meal_kcal_progress, format_plate_advice, format_plate_settings

router = Router(name="plate")
log = get_logger("handlers.plate")

#: сколько истории берём на оценку режима питания
HISTORY_DAYS = 60


async def _current_advice(
    session: AsyncSession, user: User, *, now: datetime
) -> plate_math.PlateAdvice | None:
    """Совет по текущему приёму пищи — общий расчёт для текста разбора и калорийной полосы.

    `None`, когда считать не о чем: оценка выключена, сегодня ещё нечего
    оценивать, последняя запись — перекус (кофе, горсть орехов), а не блюдо
    (`spec/plate.md` § Когда показываем).
    """
    if not user.plate_enabled:
        return None
    since = to_utc(now - timedelta(days=HISTORY_DAYS), user)
    history = await repo.load_plate_meals(session, user, since=since)
    if not history:
        return None
    rhythm = plate_math.measure_rhythm(
        history, meals_per_day=user.meals_per_day, tzinfo=user_tz(user)
    )
    day_start = to_utc(now.replace(hour=0, minute=0, second=0, microsecond=0), user)
    today = [meal for meal in history if meal.eaten_at >= day_start]
    if not today:
        return None
    sessions = plate_math.group_sessions(today, window_min=rhythm.session_min)
    current = sessions[-1]
    # Перекус сам по себе — не тарелка. Если существенная еда придёт в
    # ближайшее время, `group_sessions` склеит её с этим перекусом, и тогда
    # тарелку покажем уже вместе с ним (`spec/plate.md` § Когда показываем).
    if not plate_math.is_meal(current.items):
        return None
    from src.handlers.body import target_kcal_for

    target_kcal = await target_kcal_for(session, user)
    return plate_math.advise(
        current=current, day_sessions=sessions, rhythm=rhythm, target_kcal=target_kcal
    )


async def plate_advice_text(
    session: AsyncSession, user: User, *, now: datetime
) -> str | None:
    """Текст оценки тарелки после только что записанного приёма пищи.

    `None`, когда пропорции тарелки и так собраны, помимо причин
    `_current_advice`. Fail-soft у вызывающего: подсказка не имеет права
    съесть подтверждение записи.
    """
    advice = await _current_advice(session, user, now=now)
    if advice is None:
        return None
    # Ровные пропорции разбора не требуют.
    if plate_math.is_balanced(advice.score):
        return None
    first_time = await repo.mark_feature_used(session, user, "plate")
    return format_plate_advice(advice, with_rule=first_time)


async def meal_kcal_text(session: AsyncSession, user: User, *, now: datetime) -> str | None:
    """Калорийная полоса текущего приёма пищи (`spec/plate.md` § Калории приёма).

    В отличие от `plate_advice_text`, не зависит от баланса пропорций —
    показывается при каждой записи, пока есть активная цель по калориям.
    `None` без цели: процент без суточного ориентира ничего не значит.
    """
    advice = await _current_advice(session, user, now=now)
    if advice is None or not advice.meal_kcal_budget:
        return None
    return format_meal_kcal_progress(advice.meal_kcal, advice.meal_kcal_budget)


async def _plate_card(session: AsyncSession, user: User) -> tuple[str, bool]:
    now = local_now(user)
    history = await repo.load_plate_meals(
        session, user, since=to_utc(now - timedelta(days=HISTORY_DAYS), user)
    )
    window = plate_math.session_window_min(history)
    measured = plate_math.estimate_meals_per_day(
        history, window_min=window, tzinfo=user_tz(user)
    )
    text = format_plate_settings(
        enabled=user.plate_enabled,
        meals_per_day=user.meals_per_day,
        measured=measured,
        session_min=window,
    )
    await repo.mark_feature_used(session, user, "plate")
    return text, user.plate_enabled


@router.message(Command("plate"))
async def cmd_plate(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
        text, enabled = await _plate_card(session, user)
    await message.answer(text, reply_markup=plate_settings(enabled=enabled))


@router.callback_query(F.data == "plt:on")
async def cb_plate_on(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        user.plate_enabled = True
        text, enabled = await _plate_card(session, user)
    await callback.message.edit_text(text, reply_markup=plate_settings(enabled=enabled))
    await callback.answer("Оценка тарелки включена")


@router.callback_query(F.data == "plt:off")
async def cb_plate_off(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        user.plate_enabled = False
        text, enabled = await _plate_card(session, user)
    await callback.message.edit_text(text, reply_markup=plate_settings(enabled=enabled))
    await callback.answer("Оценка тарелки выключена")


@router.callback_query(F.data == "plt:meals")
async def cb_plate_meals(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        current = user.meals_per_day
    await callback.message.edit_reply_markup(
        reply_markup=plate_meals_picker(current=current)
    )
    await callback.answer()


@router.callback_query(F.data == "plt:mauto")
async def cb_plate_meals_auto(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        user.meals_per_day = None
        text, enabled = await _plate_card(session, user)
    await callback.message.edit_text(text, reply_markup=plate_settings(enabled=enabled))
    await callback.answer("Приёмы пищи: по статистике")


@router.callback_query(F.data == "plt:medit")
async def cb_plate_meals_edit(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "Сколько приёмов пищи в день? Напишите число от 2 до 8, "
        "например: <code>/set meals 4</code>"
    )
    await callback.answer()


__all__ = ["meal_kcal_text", "plate_advice_text", "router"]
