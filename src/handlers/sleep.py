"""/sleep — сколько спится, насколько ровно и что бывает в такие дни.

Источников два и они независимы: готовые сессии сна из Health Connect и
оценка по появлениям в чате для тех, кто Samsung Health не подключал.
Первый точнее и потому приоритетнее; второй включается вручную.
См. `spec/sleep.md`.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.analytics import sleep as sleep_mod
from src.analytics.windows import build_excursions
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now, session_scope, user_tz
from src.handlers.features import mark_used
from src.keyboards import sleep_setup
from src.reporting import format_sleep

router = Router(name="sleep")

SLEEP_PERIOD_DAYS = 30


async def build_report(
    session, user: User, *, days: int = SLEEP_PERIOD_DAYS
) -> sleep_mod.SleepReport:
    """Ночи из того источника, который у пользователя есть, плюс контрасты.

    Health Connect выигрывает у оценки по появлениям всегда: часы знают, когда
    человек уснул, а бот — только когда человек к нему зашёл.
    """
    tz = user_tz(user)
    since = local_now(user) - timedelta(days=days)
    intervals = await repo.load_sleep_intervals(session, user, since=since)
    nights = sleep_mod.nights_from_intervals(intervals, tz)
    if not nights and user.sleep_presence_enabled:
        pings = await repo.load_presence(session, user, since=since)
        nights = sleep_mod.nights_from_presence(pings, tz)
    if not nights:
        return sleep_mod.SleepReport(stats=sleep_mod.summarize([], tz))

    intakes = await repo.daily_intake(session, user, since=since)
    points = await repo.load_points(session, user, since=since)
    meals = await repo.load_meal_likes(session, user, since=since)
    excursions = (
        build_excursions(
            meals,
            points,
            window_1h=(user.window_1h_start, user.window_1h_end),
            window_2h=(user.window_2h_start, user.window_2h_end),
            baseline_window=user.baseline_window,
        )["1h"]
        if meals and points
        else []
    )
    return sleep_mod.build_report(
        nights,
        tz,
        intakes=intakes,
        points=points,
        meals=meals,
        excursions=excursions,
    )


async def _card(tg_id: int) -> tuple[str, bool]:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        report = await build_report(session, user)
        text = format_sleep(report, unit=user.glucose_unit)
        presence_on = user.sleep_presence_enabled
        if presence_on and report.stats.source != "presence":
            pings = await repo.load_presence(
                session, user, since=local_now(user) - timedelta(days=SLEEP_PERIOD_DAYS)
            )
            if not pings:
                text += (
                    "\n\n<i>Наблюдение по появлениям включено, но данных ещё нет — "
                    "первые ночи появятся через пару дней.</i>"
                )
    return text, presence_on


@router.message(Command("sleep"))
async def cmd_sleep(message: Message) -> None:
    await mark_used(message.chat.id, "sleep")
    text, presence_on = await _card(message.chat.id)
    await message.answer(text, reply_markup=sleep_setup(step="menu", presence_on=presence_on))


@router.callback_query(F.data.startswith("sl:"))
async def on_sleep_step(callback: CallbackQuery) -> None:
    step = callback.data.split(":", 1)[1]
    tg_id = callback.from_user.id
    if step in {"on", "off"}:
        await _switch(callback, enabled=step == "on")
        return
    if step == "health":
        from src.handlers.reports import cmd_health

        await callback.answer()
        await cmd_health(callback.message)
        return
    if step == "how":
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, tg_id)
            presence_on = user.sleep_presence_enabled
        await callback.answer()
        await callback.message.edit_text(
            HOW_TEXT, reply_markup=sleep_setup(step="how", presence_on=presence_on)
        )
        return
    text, presence_on = await _card(tg_id)
    await callback.answer()
    await callback.message.edit_text(
        text, reply_markup=sleep_setup(step="menu", presence_on=presence_on)
    )


async def _switch(callback: CallbackQuery, *, enabled: bool) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        user.sleep_presence_enabled = enabled
        if enabled:
            # Первая отметка ставится сразу: иначе напоминание «вас не видно»
            # сработает через сутки после включения на пустой истории.
            await repo.save_presence(session, user)
        user.last_presence_reminder_at = None
    await callback.answer("Наблюдение включено" if enabled else "Наблюдение выключено")
    text, presence_on = await _card(callback.from_user.id)
    head = ENABLED_TEXT if enabled else DISABLED_TEXT
    await callback.message.edit_text(
        head + "\n\n" + text,
        reply_markup=sleep_setup(step="menu", presence_on=presence_on),
    )


HOW_TEXT = (
    "❓ <b>Как бот узнаёт про сон</b>\n\n"
    "<b>1. Samsung Health (точно)</b>\n"
    "Часы и телефон сами пишут сессии сна, приложение-мост присылает их сюда. "
    "Ничего отмечать не надо. Подключение — /health.\n\n"
    "<b>2. По появлениям в чате (приблизительно)</b>\n"
    "Телеграм <b>не сообщает ботам</b>, когда вы в сети: «был(а) недавно» видят "
    "только люди в вашем списке контактов, и никакие настройки приватности "
    "этого для бота не открывают. Поэтому «появление» здесь — это момент, когда "
    "вы сами что-то отправили боту: написали, нажали кнопку, прислали фото.\n\n"
    "Из этих моментов бот берёт первое утреннее появление за подъём, "
    "а последнее вечернее — за отбой. Короткий ночной заход (посмотрели время "
    "в три часа ночи) за пробуждение не считается: ночью нужно "
    f"не меньше {sleep_mod.NIGHT_MIN_PINGS} появлений подряд "
    f"и {sleep_mod.NIGHT_MIN_SPAN_MIN} минут активности.\n\n"
    "<b>Что нужно от вас</b>\n"
    "• не блокировать бота и оставить уведомления включёнными: "
    "<i>чат с ботом → имя сверху → Уведомления</i>;\n"
    "• заходить утром и вечером — достаточно одного нажатия любой кнопки меню;\n"
    "• если бот не видит вас больше суток, он напомнит об этом один раз "
    "и не чаще раза в три дня.\n\n"
    "<b>Что хранится</b>\n"
    "Только отметки времени, не чаще одной в "
    f"{repo.PRESENCE_MIN_GAP_MIN} минут, и ничего о содержании сообщений. "
    "Они уезжают в /export и стираются вместе со всем остальным по /delete."
)

ENABLED_TEXT = (
    "👀 <b>Наблюдение за сном включено.</b>\n"
    "Бот запоминает моменты, когда вы к нему заходите, и через пару дней "
    "покажет режим. Выключить — кнопкой ниже или <code>/set sleep off</code>."
)

DISABLED_TEXT = (
    "🚫 <b>Наблюдение за сном выключено.</b>\n"
    "Новые отметки не сохраняются. Уже собранные лежат в /export "
    "и стираются по /delete."
)


__all__ = ["DISABLED_TEXT", "ENABLED_TEXT", "HOW_TEXT", "build_report", "cmd_sleep", "router"]
