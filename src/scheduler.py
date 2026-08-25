"""Фоновые напоминания: «пора взвеситься» (`spec/body.md`), еженедельный
рассказ об одной неиспользованной возможности (`spec/features.md`) и
«бот вас не видит» для наблюдения за сном (`spec/sleep.md`).

Отдельная задача asyncio, а не cron: бот и так живёт процессом (polling или
uvicorn), и одна корутина с часовым тиком дешевле любой внешней обвязки.
Задача полностью безопасна к падениям: любая ошибка внутри тика логируется и
цикл продолжается — напоминание не имеет права уронить бота.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram import Bot

from src.db import repo
from src.handlers.deps import session_scope, to_local
from src.keyboards import weight_prompt
from src.logging_setup import get_logger
from src.reporting import WEIGHT_PROMPT

log = get_logger("scheduler")

TICK_SECONDS = 3600
#: в какие часы локального времени пользователя уместно писать
QUIET_START = 9
QUIET_END = 20

_task: asyncio.Task | None = None
_hints_task: asyncio.Task | None = None
_presence_task: asyncio.Task | None = None


def start_scheduler(bot: Bot, *, interval_s: int = TICK_SECONDS) -> asyncio.Task | None:
    """Поднять фоновые циклы. Повторный вызов не плодит вторую задачу."""
    global _task, _hints_task, _presence_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("scheduler needs a running loop; skipped")
        return None
    if _hints_task is None or _hints_task.done():
        _hints_task = loop.create_task(feature_hint_loop(bot, interval_s=interval_s))
    if _presence_task is None or _presence_task.done():
        _presence_task = loop.create_task(presence_reminder_loop(bot, interval_s=interval_s))
    if _task is not None and not _task.done():
        return _task
    _task = loop.create_task(weight_reminder_loop(bot, interval_s=interval_s))
    return _task


async def stop_scheduler() -> None:
    global _task, _hints_task, _presence_task
    for task in (_task, _hints_task, _presence_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except Exception:  # остановка не должна шуметь, включая CancelledError
            pass
        except asyncio.CancelledError:
            pass
    _task = None
    _hints_task = None
    _presence_task = None


async def weight_reminder_loop(bot: Bot, *, interval_s: int = TICK_SECONDS) -> None:
    while True:
        try:
            await run_weight_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("weight reminder tick failed")
        await asyncio.sleep(interval_s)


async def run_weight_reminders(bot: Bot, *, now: datetime | None = None) -> int:
    """Один тик: кому пора взвеситься — тому и пишем. Возвращает число писем.

    Отметка о напоминании ставится **до** отправки: сеть может отвалиться на
    середине рассылки, и повторный тик не должен слать второе сообщение.
    """
    moment = now or datetime.now(UTC)
    sent = 0
    async with session_scope() as session:
        due = await repo.users_due_for_weight(session, now=moment)
        for user, profile in due:
            local = to_local(moment, user)
            if not QUIET_START <= local.hour < QUIET_END:
                continue
            await repo.mark_weight_prompt(session, profile, moment)
            try:
                await bot.send_message(
                    user.tg_id, WEIGHT_PROMPT, reply_markup=weight_prompt()
                )
                sent += 1
            except Exception:
                # Заблокированный чат — обычное дело; метку не откатываем,
                # иначе бот будет долбиться в него каждый час.
                log.warning("weight reminder not delivered to %s", user.tg_id, exc_info=True)
    return sent


async def feature_hint_loop(bot: Bot, *, interval_s: int = TICK_SECONDS) -> None:
    while True:
        try:
            await run_feature_hints(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("feature hint tick failed")
        await asyncio.sleep(interval_s)


async def run_feature_hints(bot: Bot, *, now: datetime | None = None) -> int:
    """Один тик: кому неделю ничего не рассказывали — тому одну возможность.

    Больше `MAX_HINTS` раз об одной и той же возможности не говорим никогда, а
    отказ («Не нужно») снимает её с рассылки навсегда — всё это решает
    `features.pick_hint`.
    """
    from src.features import HINT_PERIOD_DAYS
    from src.handlers.features import maybe_send_hint

    moment = now or datetime.now(UTC)
    async with session_scope() as session:
        due = await repo.users_due_for_hint(
            session, now=moment, period_days=HINT_PERIOD_DAYS
        )
        targets = [
            user.tg_id
            for user in due
            if QUIET_START <= to_local(moment, user).hour < QUIET_END
        ]
    sent = 0
    for tg_id in targets:
        if await maybe_send_hint(bot, tg_id):
            sent += 1
    return sent


async def presence_reminder_loop(bot: Bot, *, interval_s: int = TICK_SECONDS) -> None:
    while True:
        try:
            await run_presence_reminders(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("presence reminder tick failed")
        await asyncio.sleep(interval_s)


async def run_presence_reminders(bot: Bot, *, now: datetime | None = None) -> int:
    """Один тик: у кого включено наблюдение за сном, но бот их не видит.

    Молчащая функция хуже выключенной: человек думает, что сон считается, а
    ночей нет. Поэтому после суток тишины — одно письмо с инструкцией и
    предложением выключить, не чаще раза в три дня.
    """
    from src.analytics.sleep import PRESENCE_SILENCE_DAYS
    from src.reporting import SLEEP_PRESENCE_REMINDER

    moment = now or datetime.now(UTC)
    sent = 0
    async with session_scope() as session:
        due = await repo.users_due_for_presence_reminder(
            session, now=moment, silent_days=PRESENCE_SILENCE_DAYS
        )
        for user in due:
            local = to_local(moment, user)
            if not QUIET_START <= local.hour < QUIET_END:
                continue
            # Метку ставим до отправки: оборванная сеть не должна превращаться
            # в повторное письмо на следующем тике.
            await repo.mark_presence_reminder(session, user, moment)
            try:
                await bot.send_message(user.tg_id, SLEEP_PRESENCE_REMINDER)
                sent += 1
            except Exception:
                log.warning("presence reminder not delivered to %s", user.tg_id, exc_info=True)
    return sent


__all__ = [
    "QUIET_END",
    "QUIET_START",
    "TICK_SECONDS",
    "feature_hint_loop",
    "presence_reminder_loop",
    "run_feature_hints",
    "run_presence_reminders",
    "run_weight_reminders",
    "start_scheduler",
    "stop_scheduler",
    "weight_reminder_loop",
]
