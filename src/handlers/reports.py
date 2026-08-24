"""Read-only commands: /today /stats /graph /export /delete /health."""

from __future__ import annotations

from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.analytics import activity as activity_mod
from src.analytics import cgm_metrics
from src.analytics import symptoms as symptoms_mod
from src.analytics.stats import KeyStats, aggregate
from src.analytics.tags import normalize_name
from src.analytics.windows import GlucosePoint, build_excursions
from src.charts.render import render_ranking, render_timeline, render_wellbeing
from src.config import load_settings
from src.db import repo
from src.db.models import User
from src.export import build_export
from src.handlers.deps import local_now, session_scope, to_local
from src.ingest.units import format_value
from src.keyboards import confirm_delete, main_menu, stats_windows
from src.reporting import (
    DISCLAIMER,
    format_activity,
    format_cgm_summary,
    format_product_verdict,
    format_recommendations,
    format_stats,
    format_symptoms,
)
from src.vision.schemas import ProductDraft

router = Router(name="reports")

DEFAULT_PERIOD_DAYS = 30


def _windows(user: User) -> tuple[tuple[int, int], tuple[int, int]]:
    return (
        (user.window_1h_start, user.window_1h_end),
        (user.window_2h_start, user.window_2h_end),
    )


async def _compute_stats(
    session, user: User, *, window: str = "1h", key_type: str = "tag", days: int = DEFAULT_PERIOD_DAYS
) -> list[KeyStats]:
    since = local_now(user) - timedelta(days=days)
    meals = await repo.load_meal_likes(session, user, since=since)
    points = await repo.load_points(session, user, since=since - timedelta(hours=6))
    if not meals or not points:
        return []
    window_1h, window_2h = _windows(user)
    excursions = build_excursions(
        meals,
        points,
        window_1h=window_1h,
        window_2h=window_2h,
        baseline_window=user.baseline_window,
    )
    return aggregate(
        meals,
        excursions[window],
        key_type=key_type,
        window=window,
        min_observations=load_settings().min_observations,
    )


# ------------------------------------------------------------------ /today

@router.message(Command("today"))
@router.message(F.text == "📅 Сегодня")
async def cmd_today(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        meals = await repo.load_meals(session, user, since=start)
        readings = await repo.load_glucose(session, user, since=start)
        checkins = await repo.load_checkins(session, user, since=start)
        unit = user.glucose_unit
        today = now.date()
        lines = [f"📅 <b>{today:%d.%m.%Y}</b>", ""]
        meals_today = [m for m in meals if to_local(m.eaten_at, user).date() == today]
        if meals_today:
            lines.append("<b>Еда</b>")
            for meal in meals_today:
                stamp = to_local(meal.eaten_at, user)
                carbs = f" · угл {meal.carbs_g:.0f} г" if meal.carbs_g else ""
                lines.append(f"• {stamp:%H:%M} {meal.title or 'приём пищи'}{carbs}")
            lines.append("")
        readings_today = [r for r in readings if to_local(r.measured_at, user).date() == today]
        if readings_today:
            lines.append("<b>Сахар</b>")
            for reading in readings_today[-12:]:
                stamp = to_local(reading.measured_at, user)
                lines.append(f"• {stamp:%H:%M} {format_value(reading.value_mmol, unit)}")
            lines.append("")
        checkins_today = [
            (c, labels) for c, labels in checkins if to_local(c.at, user).date() == today
        ]
        if checkins_today:
            lines.append("<b>Самочувствие</b>")
            for checkin, labels in checkins_today:
                stamp = to_local(checkin.at, user)
                tail = f" — {', '.join(labels)}" if labels else ""
                lines.append(f"• {stamp:%H:%M} {checkin.score}/5{tail}")
            lines.append("")
        if len(lines) <= 2:
            lines.append("Сегодня записей пока нет. Пришлите фото еды или показание сахара.")
    await message.answer("\n".join(lines), reply_markup=main_menu())


# ------------------------------------------------------------------ /stats

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message) -> None:
    await _send_stats(message, window="1h", key_type="tag", edit=False)


@router.callback_query(F.data.startswith("stats:"))
async def on_stats_callback(callback: CallbackQuery, bot: Bot) -> None:
    _, kind, value = callback.data.split(":", 2)
    await callback.answer()
    if kind == "chart":
        await _send_ranking_chart(callback.message, window="1h", key_type="tag")
        return
    window = value if kind == "w" else "1h"
    key_type = value if kind == "k" else "tag"
    await _send_stats(callback.message, window=window, key_type=key_type, edit=True)


async def _send_stats(message: Message, *, window: str, key_type: str, edit: bool) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        stats = await _compute_stats(session, user, window=window, key_type=key_type)
        points = await repo.load_points(
            session, user, since=local_now(user) - timedelta(days=DEFAULT_PERIOD_DAYS)
        )
        checkins = await repo.load_checkin_likes(
            session, user, since=local_now(user) - timedelta(days=DEFAULT_PERIOD_DAYS)
        )
        meals = await repo.load_meal_likes(
            session, user, since=local_now(user) - timedelta(days=DEFAULT_PERIOD_DAYS)
        )
        unit = user.glucose_unit

    blocks = [format_stats(stats, unit=unit, window=window)]
    if points:
        blocks.append(format_cgm_summary(cgm_metrics.summarize(points), unit=unit))
    if checkins:
        contexts = symptoms_mod.build_context(
            checkins, points, [(m.eaten_at, m.tags) for m in meals]
        )
        blocks.append(format_symptoms(symptoms_mod.aggregate_symptoms(contexts), unit=unit))
    blocks.append(format_recommendations(stats, unit=unit))
    text = "\n\n".join(blocks)
    keyboard = stats_windows(active=window)
    if edit:
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=keyboard)


# ------------------------------------------------------------------ /graph

@router.message(Command("graph"))
@router.message(F.text == "📈 График")
async def cmd_graph(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        since = now - timedelta(days=3)
        points = await repo.load_points(session, user, since=since)
        meals = await repo.load_meals(session, user, since=since)
        checkins = await repo.load_checkins(session, user, since=since)
        meal_marks = [(to_local(m.eaten_at, user), m.title or "еда") for m in meals]
        point_marks = [GlucosePoint(at=to_local(p.at, user), value=p.value) for p in points]
        score_marks = [(to_local(c.at, user), c.score) for c, _ in checkins]
    if not point_marks and not meal_marks:
        await message.answer("Пока нечего рисовать — нет ни еды, ни показаний за 3 дня.")
        return
    png = render_timeline(
        point_marks, meal_marks, checkins=score_marks, title="Последние 3 дня"
    )
    await message.answer_photo(
        BufferedInputFile(png, filename="timeline.png"),
        caption="Глюкоза, приёмы пищи и самочувствие",
    )
    if score_marks:
        symptom_png = render_wellbeing(score_marks, title="Самочувствие за 3 дня")
        await message.answer_photo(BufferedInputFile(symptom_png, filename="wellbeing.png"))
    await _send_ranking_chart(message, window="1h", key_type="tag")


async def _send_ranking_chart(message: Message, *, window: str, key_type: str) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        stats = await _compute_stats(session, user, window=window, key_type=key_type)
    if not stats:
        await message.answer("Для рейтинга компонентов пока мало наблюдений.")
        return
    png = render_ranking(stats)
    await message.answer_photo(
        BufferedInputFile(png, filename="ranking.png"),
        caption="Средний подъём сахара после компонентов (цвет — достоверность)",
    )


# ------------------------------------------------------------------ /export

@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        archive = await build_export(session, user)
    await message.answer_document(
        BufferedInputFile(archive, filename="cgm-diet-export.zip"),
        caption="Ваши данные: по одному CSV на таблицу, UTF-8 с BOM.",
    )


# ------------------------------------------------------------------ /delete

@router.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        totals = await repo.counts(session, user)
    await message.answer(
        "🗑 <b>Удаление данных</b>\n\n"
        f"Будут удалены: приёмы пищи — {totals['meals']}, показания сахара — "
        f"{totals['glucose']}, отметки самочувствия — {totals['checkins']}, "
        f"продукты — {totals['products']}, анализы — {totals['labs']}, "
        f"активность — {totals['activity']}.\n\n"
        "Это необратимо. Сначала можно сделать /export.",
        reply_markup=confirm_delete(),
    )


@router.callback_query(F.data == "del:yes")
async def on_delete_yes(callback: CallbackQuery, state: FSMContext) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.delete_user_data(session, user)
        await repo.seed_symptoms(session, user)
    await state.clear()
    await callback.answer("Удалено")
    await callback.message.edit_text("🗑 Все ваши данные удалены. Можно начать заново: /start")


@router.callback_query(F.data == "del:no")
async def on_delete_no(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.edit_text("Отменено, данные на месте.")


# ------------------------------------------------------------------ /health

@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    settings = load_settings()
    base = settings.webhook_base_url or "https://<ваш-домен>"
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        samples = await repo.load_activity(session, user, since=local_now(user) - timedelta(days=7))
        contrast_text = ""
        if samples:
            since = local_now(user) - timedelta(days=DEFAULT_PERIOD_DAYS)
            meals = await repo.load_meal_likes(session, user, since=since)
            points = await repo.load_points(session, user, since=since)
            window_1h, window_2h = _windows(user)
            excursions = build_excursions(
                meals, points, window_1h=window_1h, window_2h=window_2h,
                baseline_window=user.baseline_window,
            )
            buckets = await repo.load_activity_buckets(session, user, since=since)
            contrast_text = "\n\n" + format_activity(
                activity_mod.contrast_by_activity(meals, excursions["1h"], buckets),
                unit=user.glucose_unit,
            )
        total_steps = sum(s.steps or 0 for s in samples)
    from src.health.samsung import HealthSyncError, make_token

    try:
        token = make_token(message.chat.id, settings.health_sync_secret)
    except HealthSyncError:
        token = "— не настроен HEALTH_SYNC_SECRET на сервере"
    await message.answer(
        "⌚️ <b>Samsung Health / Health Connect</b>\n\n"
        "Прямого API у Samsung Health нет — данные забирает приложение-мост на "
        "телефоне (Health Connect) и отправляет их сюда:\n"
        f"<code>POST {base}/health/samsung</code>\n"
        f"Заголовок: <code>X-Health-Token: {token}</code>\n"
        f"Ваш chat_id: <code>{message.chat.id}</code>\n\n"
        "Формат и пример запроса — в README, раздел «Health sync».\n"
        f"За 7 дней получено шагов: {total_steps}." + contrast_text
    )


# ------------------------------------------------------------------ helper

async def product_verdict_text(tg_id: int, draft: ProductDraft) -> str:
    """«Можно ли мне это?» — answered from the user's own statistics."""
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        tag_stats = await _compute_stats(session, user, window="1h", key_type="tag")
        item_stats = await _compute_stats(session, user, window="1h", key_type="item")
        unit = user.glucose_unit
    wanted = set(draft.flags or [])
    matches = [s for s in tag_stats if s.key in wanted]
    name_norm = normalize_name(draft.name)
    matches += [s for s in item_stats if s.key and s.key in name_norm]
    matches.sort(key=lambda s: -s.mean_delta)
    return format_product_verdict(draft, matches, unit) + "\n\n" + DISCLAIMER


__all__ = ["cmd_stats", "cmd_today", "product_verdict_text", "router"]
