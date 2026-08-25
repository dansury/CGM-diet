"""Гарвардская тарелка: оценка приёма пищи и её настройки (`spec/plate.md`).

Обработчик только маршрутизирует: вся арифметика — в `src/analytics/plate.py`,
все формулировки — в `src/reporting.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics import plate as plate_math
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now, session_scope, to_utc, user_tz
from src.logging_setup import get_logger
from src.reporting import format_plate_advice, format_plate_settings

router = Router(name="plate")
log = get_logger("handlers.plate")

#: сколько истории берём на оценку режима питания
HISTORY_DAYS = 60


async def plate_advice_text(
    session: AsyncSession, user: User, *, now: datetime
) -> str | None:
    """Текст оценки тарелки после только что записанного приёма пищи.

    `None`, когда говорить не о чем: оценка выключена, сегодня ещё нечего
    оценивать, последняя запись — перекус (кофе, горсть орехов), а не блюдо,
    или пропорции тарелки и так собраны. Fail-soft у вызывающего: подсказка
    не имеет права съесть подтверждение записи.
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
    advice = plate_math.advise(current=current, day_sessions=sessions, rhythm=rhythm)
    # Ровные пропорции разбора не требуют.
    if plate_math.is_balanced(advice.score):
        return None
    await repo.mark_feature_used(session, user, "plate")
    return format_plate_advice(advice, with_rule=advice.meals_done <= 1)


@router.message(Command("plate"))
async def cmd_plate(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.from_user.id)
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
    await message.answer(text)


__all__ = ["plate_advice_text", "router"]
