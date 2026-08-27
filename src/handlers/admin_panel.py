"""Owner panel: `/users` and `/bot_settings` (`spec/bot.md` § Панель владельца).

Ported from GrowthProducer's `handlers/admin_panel.py`, keeping only what this
bot actually has: a user roster with their own record counters, totals over the
base, the model slots, the error ring and a health check. Channels, publications
and monetization stayed there — there is nothing here to show for them.

Private chat only, `OWNER_TG_IDS` only, same as `handlers/admin.py`: a stranger
is not refused, the router simply does not match and the update falls through,
so the bot never leaks that these commands exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from src.config import load_settings
from src.db import repo
from src.db.models import GlucoseReading, Meal, Medication, User, WellbeingCheckin, Workout
from src.errors_report import recent_reports, render_report
from src.handlers.deps import session_scope
from src.logging_setup import get_logger

router = Router(name="admin_panel")
log = get_logger("handlers.admin_panel")

CB_PREFIX = "botadm:"
#: сколько пользователей показываем в реестре
USERS_LIMIT = 50
#: окно «за неделю» в разделе данных
RECENT_DAYS = 7
#: лимит сообщения Telegram — 4096; оставляем запас
MESSAGE_LIMIT = 4000


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and load_settings().is_owner(user_id)


owner_filter = F.func(lambda event: _is_owner(getattr(event.from_user, "id", None)))
router.message.filter(F.chat.type == "private", owner_filter)
router.callback_query.filter(owner_filter)


# ------------------------------------------------------------------ /users

async def render_users() -> str:
    """Реестр пользователей: кто есть и что каждый успел записать."""
    async with session_scope() as session:
        users = list(
            await session.scalars(
                select(User).order_by(User.created_at.desc()).limit(USERS_LIMIT)
            )
        )
        total = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        onboarded = int(
            await session.scalar(
                select(func.count()).select_from(User).where(User.onboarded.is_(True))
            )
            or 0
        )
        rows: list[str] = []
        for index, user in enumerate(users, 1):
            counters = await repo.counts(session, user)
            last_meal = await session.scalar(
                select(func.max(Meal.eaten_at)).where(Meal.user_id == user.id)
            )
            rows.append(_user_block(index, user, counters, last_meal))

    head = [
        f"👥 <b>Пользователи ({total})</b> · анкету прошли: {onboarded}",
        "",
    ]
    if not rows:
        head.append("Пока никого — ждём первых /start.")
    return "\n".join([*head, *rows])


def _user_block(index: int, user: User, counters: dict[str, int], last_meal) -> str:
    who = [f"<code>{user.tg_id}</code>"]
    if user.first_name:
        who.insert(0, f"<b>{escape(user.first_name)}</b>")
    if user.username:
        who.append(f"@{escape(user.username)}")
    created = user.created_at.strftime("%Y-%m-%d") if user.created_at else "?"
    mark = "" if user.onboarded else " · анкета не пройдена"
    lines = [f"{index}. {' · '.join(who)} · с {created} · {escape(user.tz)}{mark}"]
    lines.append(
        "   🍽 {meals} · 🩸 {glucose} · ⚖️ {weights} · 🏃 {workouts} · "
        "💊 {medications} · 🙂 {checkins}".format(**counters)
    )
    if isinstance(last_meal, datetime):
        lines.append(f"   последняя еда: {last_meal.strftime('%m-%d %H:%M')} UTC")
    return "\n".join(lines)


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    await message.answer((await _safe(render_users(), "users"))[:MESSAGE_LIMIT])


# ------------------------------------------------------------------ /bot_settings

_SECTIONS: tuple[tuple[str, str], ...] = (
    ("users", "👥 Пользователи"),
    ("data", "📊 Данные"),
    ("models", "🧠 Нейросети"),
    ("errors", "🩺 Ошибки"),
    ("health", "❤️ Health"),
)

_MAIN_TEXT = (
    "🛠 <b>Панель владельца</b>\n\n"
    "Пользователи, данные, нейросети, ошибки, здоровье сервиса.\n"
    "Смена модели — /model, полный отчёт об ошибке — /errors."
)


def owner_panel() -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура панели — одной точкой для всех входов."""
    return _MAIN_TEXT, InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data=f"{CB_PREFIX}{key}")]
            for key, title in _SECTIONS
        ]
    )


@router.message(Command("bot_settings"))
async def cmd_bot_settings(message: Message) -> None:
    text, markup = owner_panel()
    await message.answer(text, reply_markup=markup)


async def render_data() -> str:
    """Итоги по базе: сколько всего записей и сколько из них за неделю."""
    since = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    models = (
        ("Пользователи", User, User.created_at),
        ("Приёмы пищи", Meal, Meal.eaten_at),
        ("Измерения сахара", GlucoseReading, GlucoseReading.measured_at),
        ("Тренировки", Workout, Workout.started_at),
        ("Лекарства", Medication, Medication.taken_at),
        ("Самочувствие", WellbeingCheckin, WellbeingCheckin.at),
    )
    lines = [f"📊 <b>Данные</b> (всего · за {RECENT_DAYS} дн.)", ""]
    async with session_scope() as session:
        for title, model, stamp in models:
            total = int(await session.scalar(select(func.count()).select_from(model)) or 0)
            recent = int(
                await session.scalar(
                    select(func.count()).select_from(model).where(stamp >= since)
                )
                or 0
            )
            lines.append(f"• {title}: <b>{total}</b> · {recent}")
    return "\n".join(lines)


async def render_models() -> str:
    """Модели по слотам — то же, что показывает /models, плюс ключи."""
    from src.handlers.admin import LEVEL_NAMES, explain_models
    from src.llm import model_selection

    settings = load_settings()
    lines = [
        "🧠 <b>Нейросети</b>",
        "",
        "OpenRouter: " + ("✅" if settings.openrouter_api_key else "❌")
        + " · SpeechKit: " + ("✅" if settings.speechkit_available else "❌")
        + (" · LLM_MOCK" if settings.llm_mock else ""),
        "",
    ]
    for slot, item in (await explain_models()).items():
        level = LEVEL_NAMES.get(item.level, item.level)
        lines.append(f"• <b>{slot}</b> — <code>{item.model_id}</code> ({level})")
        hint = model_selection.SLOT_LABELS.get(slot)
        if hint:
            lines.append(f"  <i>{hint}</i>")
    lines += ["", "Сменить — /model"]
    return "\n".join(lines)


def render_errors() -> str:
    reports = recent_reports(5)
    if not reports:
        return "🩺 <b>Ошибки</b>\n\nЧисто — с момента старта процесса ни одной."
    blocks = [render_report(report) for report in reports]
    return "\n\n".join([f"🩺 <b>Последние ошибки: {len(reports)}</b>", *blocks])


async def render_health() -> str:
    """Проверки, каждая из которых что-то реально дёргает, а не читает конфиг."""
    settings = load_settings()
    lines = ["❤️ <b>Health</b>", ""]
    try:
        async with session_scope() as session:
            users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
        lines.append(f"✅ БД отвечает · пользователей: {users}")
    except Exception as exc:
        lines.append(f"❌ БД: {escape(type(exc).__name__)}")
    lines.append(
        ("✅" if settings.vision_available else "❌")
        + " LLM"
        + (" (LLM_MOCK — модель не вызывается)" if settings.llm_mock else "")
    )
    lines.append(("✅" if settings.stt_available else "❌") + " распознавание голоса")
    lines.append(
        ("✅" if settings.health_sync_secret else "❌") + " health-sync (Samsung Health)"
    )
    lines.append(f"Режим: <code>{escape(settings.bot_mode)}</code> · env {escape(settings.app_env)}")
    return "\n".join(lines)


_RENDERERS = {
    "users": render_users,
    "data": render_data,
    "models": render_models,
    "errors": render_errors,
    "health": render_health,
}


async def _safe(awaitable, section: str) -> str:
    """Сбой одного раздела не имеет права уронить всю панель."""
    try:
        return await awaitable
    except Exception as exc:
        log.exception("admin panel section failed: %s", section)
        return f"❌ Раздел <code>{escape(section)}</code>: {escape(str(exc))}"


@router.callback_query(F.data.startswith(CB_PREFIX))
async def on_section(callback: CallbackQuery) -> None:
    section = (callback.data or "")[len(CB_PREFIX) :]
    render = _RENDERERS.get(section)
    if render is None:
        await callback.answer()
        return
    result = render()
    text = result if isinstance(result, str) else await _safe(result, section)
    await callback.answer()
    _, markup = owner_panel()
    await callback.message.answer(text[:MESSAGE_LIMIT], reply_markup=markup)


__all__ = [
    "cmd_bot_settings",
    "cmd_users",
    "owner_panel",
    "render_data",
    "render_errors",
    "render_health",
    "render_models",
    "render_users",
    "router",
]
