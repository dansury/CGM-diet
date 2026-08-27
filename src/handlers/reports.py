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
from src.handlers.features import mark_used, menu_of
from src.ingest.units import format_value
from src.keyboards import confirm_delete, health_setup, stats_windows
from src.reporting import (
    DISCLAIMER,
    format_activity,
    format_cgm_summary,
    format_product_verdict,
    format_recommendations,
    format_sleep_short,
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
        workouts_today = [
            row
            for row in await repo.load_workouts(session, user, since=start)
            if to_local(row.started_at, user).date() == today
        ]
        if workouts_today:
            from src.analytics.workout import kind_label

            lines.append("<b>Тренировки</b>")
            for row in workouts_today:
                stamp = to_local(row.started_at, user)
                duration = f" · {row.duration_min:.0f} мин" if row.duration_min else ""
                energy = f" · ≈ {row.kcal:.0f} ккал" if row.kcal else ""
                lines.append(
                    f"• {stamp:%H:%M} {row.title or kind_label(row.kind)}{duration}{energy}"
                )
            lines.append("")
        if len(lines) <= 2:
            lines.append("Сегодня записей пока нет. Пришлите фото еды или показание сахара.")
        from src.handlers.body import day_progress_text

        progress = await day_progress_text(session, user, now=now)
        if progress:
            lines.append(progress)
    await message.answer("\n".join(lines), reply_markup=await menu_of(message.chat.id))


# ------------------------------------------------------------------ /stats

@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message) -> None:
    await mark_used(message.chat.id, "stats")
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
        # Сон — отдельная карточка (/sleep); в статистике от него одна строка,
        # чтобы режим было видно рядом с цифрами по еде (`spec/sleep.md`).
        from src.handlers.sleep import build_report as build_sleep_report

        sleep_line = format_sleep_short(await build_sleep_report(session, user))

    blocks = [format_stats(stats, unit=unit, window=window)]
    if sleep_line:
        blocks.append(sleep_line)
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
    await mark_used(message.chat.id, "graph")
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
    # Вес и состав тела — только если пользователь их вводил (`spec/body.md`).
    from src.handlers.body import send_weight_charts

    await send_weight_charts(message, tg_id=message.chat.id)
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
    await mark_used(message.chat.id, "export")
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
        f"активность — {totals['activity']}, взвешивания — {totals['weights']}, "
        f"тренировки — {totals['workouts']}.\n\n"
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
    await mark_used(message.chat.id, "health")
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
        sleep_nights = sum(1 for s in samples if s.kind == "sleep")
        if sleep_nights:
            contrast_text += (
                "\n\nСон с телефона тоже приходит — разбор в /sleep."
            )
    await message.answer(
        _health_status_text(total_steps) + contrast_text,
        reply_markup=health_setup(step="menu"),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("hs:"))
async def on_health_step(callback: CallbackQuery) -> None:
    """Инструкция листается кнопками — человек не ищет её в README."""
    step = callback.data.split(":", 1)[1]
    texts = {
        "how": _health_how_text,
        "keys": lambda: _health_keys_text(callback.from_user.id),
        "app": _health_app_text,
        "menu": lambda: _health_status_text(None),
    }
    render = texts.get(step)
    if render is None:
        await callback.answer()
        return
    await callback.answer()
    await callback.message.edit_text(
        render(),
        reply_markup=health_setup(step=step),
        disable_web_page_preview=True,
    )


def _health_status_text(total_steps: int | None) -> str:
    tail = (
        f"\n\nЗа 7 дней получено шагов: <b>{total_steps}</b>."
        if total_steps is not None
        else ""
    )
    return (
        "⌚️ <b>Samsung Health</b>\n\n"
        "Шаги, тренировки и сон с телефона помогают увидеть, "
        "как прогулка после еды меняет ваш сахар.\n\n"
        "Samsung Health не отдаёт данные сайтам напрямую — их забирает "
        "с телефона маленькое приложение-мост через Health Connect "
        "и присылает сюда.\n\n"
        "Настройка занимает 5 минут и делается один раз." + tail
    )


def _health_how_text() -> str:
    """Пошагово, словами телефона Samsung — без единого технического термина сверх нужного."""
    return (
        "📲 <b>Как подключить — 6 шагов</b>\n\n"
        "<b>1. Health Connect на телефоне</b>\n"
        "Android 14 и новее: <i>Настройки → Безопасность и конфиденциальность → "
        "Ещё → Health Connect</i> — он уже есть.\n"
        "Android 10–13: установите «Health Connect» из Galaxy Store "
        "или Google Play.\n\n"
        "<b>2. Разрешите Samsung Health делиться</b>\n"
        "Откройте <i>Samsung Health → ☰ (три полоски) → Настройки → "
        "Health Connect</i> и включите: шаги, тренировки, сон, пульс.\n\n"
        "<b>3. Поставьте приложение-мост</b>\n"
        "Кнопка «📦 Приложение-мост» ниже — там ссылка и что делать, "
        "если телефон предупредит про «неизвестный источник».\n\n"
        "<b>4. Вставьте свои ключи</b>\n"
        "Кнопка «🔑 Мои ключи» ниже даёт одну строку. Скопируйте её, "
        "откройте мост и нажмите «Вставить строку настройки» — все три поля "
        "заполнятся сами.\n\n"
        "<b>5. Разрешите мосту читать Health Connect</b>\n"
        "При первом запуске он спросит доступ к шагам, тренировкам, сну "
        "и пульсу — нажмите «Разрешить».\n\n"
        "<b>6. Нажмите «Синхронизировать сейчас»</b>\n"
        "Через минуту вернитесь сюда и отправьте /health — внизу появится "
        "число полученных шагов. Дальше мост присылает данные сам, раз в час.\n\n"
        "⚠️ Если данные перестали приходить: <i>Настройки → Приложения → "
        "CGM Мост → Батарея → Без ограничений</i>."
    )


def _health_keys_text(tg_id: int) -> str:
    settings = load_settings()
    base = settings.webhook_base_url or "https://&lt;ваш-домен&gt;"
    from src.health.samsung import HealthSyncError, make_token

    try:
        token = make_token(tg_id, settings.health_sync_secret)
    except HealthSyncError:
        return (
            "🔑 <b>Мои ключи</b>\n\n"
            "Сервер ещё не настроен на приём данных с телефона "
            "(<code>HEALTH_SYNC_SECRET</code> не задан). "
            "Напишите владельцу бота — без этого мост подключить нельзя."
        )
    return (
        "🔑 <b>Мои ключи</b>\n\n"
        "Скопируйте эту строку целиком и вставьте в приложение-мост "
        "(«Вставить строку настройки»):\n"
        f"<code>cgmdiet://setup?base={base}&amp;tg={tg_id}&amp;token={token}</code>\n\n"
        "Или заполните поля вручную:\n"
        f"• Адрес сервера: <code>{base}</code>\n"
        f"• Ваш ID: <code>{tg_id}</code>\n"
        f"• Токен: <code>{token}</code>\n\n"
        "🔒 Токен — только ваш: он открывает запись данных ровно в вашу карточку "
        "и ничего не читает. Никому его не пересылайте."
    )


def _health_app_text() -> str:
    settings = load_settings()
    return (
        "📦 <b>Приложение-мост</b>\n\n"
        "«CGM Мост» — бесплатное приложение без рекламы и без своего сервера: "
        "оно читает Health Connect и отправляет данные только на адрес, "
        "который вы вставите.\n\n"
        f"Скачать APK: {settings.health_bridge_url}\n\n"
        "<b>Как поставить APK</b>\n"
        "1. Откройте ссылку в браузере телефона и скачайте файл "
        "<code>cgm-bridge.apk</code>.\n"
        "2. Нажмите на скачанный файл. Телефон предупредит про «неизвестный "
        "источник» — нажмите «Настройки» и разрешите установку для браузера.\n"
        "3. Вернитесь и нажмите «Установить».\n\n"
        "Исходный код приложения — в папке <code>apps/health-bridge</code> "
        "того же репозитория: можно собрать самому, если ставить APK не хочется."
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
