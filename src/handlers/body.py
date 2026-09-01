"""Тело и цель: профиль, взвешивания, биоимпеданс, дневной коридор калорий.

Маршрутизация и ничего кроме: расчёты — в `src/analytics/body.py`, тексты — в
`src/reporting.py`, запись — в `src/db/repo.py` (`CLAUDE.md` #6, #7).
См. `spec/body.md`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.analytics import body as body_math
from src.charts.render import render_body_composition, render_weight
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now, session_scope, to_local, to_utc, user_tz
from src.handlers.features import menu_of
from src.handlers.states import BodyFlow
from src.ingest.text_parse import parse_text
from src.keyboards import (
    activity_picker,
    body_menu,
    cancel_only,
    confirm_measurement,
    pregnancy_picker,
    rate_picker,
    sex_picker,
)
from src.logging_setup import get_logger
from src.reporting import (
    format_body_card,
    format_day_progress,
    format_day_totals,
    format_goal_plan,
    format_meal_progress,
    format_meals_today,
    format_measurement_draft,
    format_weight_saved,
)
from src.vision.schemas import (
    MeasurementDraft,
    measurement_from_dict,
    measurement_to_dict,
)

router = Router(name="body")
log = get_logger("handlers.body")

FIELD_KEY = "body_field"
DRAFT_KEY = "draft"

PROMPTS = {
    "weight": (
        "⚖️ Напишите вес числом — «82,4».\n"
        "Если весы показывают состав тела, можно сразу: "
        "<code>82,4 жир 24% мышцы 58 кг вода 55%</code>.\n"
        "Или пришлите фото экрана весов — прочитаю."
    ),
    "height": "📏 Ваш рост в сантиметрах — например «178».",
    "age": "🎂 Сколько вам полных лет?",
    "goal": "🎯 Какой вес хотите видеть на весах? Напишите число — «75».",
    "conditions": (
        "🩺 Есть ли у вас особые состояния или заболевания, которые стоит учитывать "
        "(диабет, заболевания почек, беременность и т.п.)? Опишите словами или "
        "напишите «нет». Это не заменяет визит к врачу, но я не буду предлагать "
        "снижение веса, если оно опасно."
    ),
}

# Поля, где ждём текст как есть, а не число (`on_value` не гонит их через
# `_first_number`).
TEXT_FIELDS = {"conditions"}


# ------------------------------------------------------------------ /body

@router.message(Command("body"))
@router.message(F.text == "⚖️ Вес и цель")
async def cmd_body(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, has_goal, show_pregnancy = await _body_card(message.chat.id)
    await message.answer(text, reply_markup=body_menu(has_goal=has_goal, show_pregnancy=show_pregnancy))


@router.message(Command("weight"))
async def cmd_weight(message: Message, state: FSMContext) -> None:
    await _ask_field(message, state, "weight")


async def _body_card(tg_id: int) -> tuple[str, bool, bool]:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        profile = await repo.get_body_profile(session, user)
        goal = await repo.get_active_goal(session, user)
        last = await repo.last_weight(session, user)
        series = [
            (to_local(row.measured_at, user), row.weight_kg)
            for row in await repo.load_weights(session, user)
        ]
        plan = await _plan_for(session, user) if goal else None
        age = body_math.age_from(profile.birth_year) if profile else None
        if last is not None:
            last.measured_at = to_local(last.measured_at, user)
    value = body_math.bmi(last.weight_kg if last else None, profile.height_cm if profile else None)
    text = format_body_card(
        profile=profile,
        last=last,
        goal=goal,
        plan=plan,
        trend=body_math.weight_trend(series),
        bmi_value=value,
        bmi_note=body_math.bmi_category(value),
        age=age,
    )
    return text, goal is not None, bool(profile and profile.sex == "f")


async def _plan_for(session, user: User, *, weight_kg: float | None = None) -> body_math.EnergyPlan | None:
    """Пересобрать коридор из профиля и активной цели (веса меняются — план тоже)."""
    goal = await repo.get_active_goal(session, user)
    if goal is None:
        return None
    profile = await repo.get_body_profile(session, user)
    last = await repo.last_weight(session, user)
    weight = weight_kg or (last.weight_kg if last else None) or goal.start_weight_kg
    try:
        return body_math.build_plan(
            kind=goal.kind,
            weight_kg=weight,
            target_weight_kg=goal.target_weight_kg,
            rate_kg_week=goal.rate_kg_week,
            height_cm=profile.height_cm if profile else None,
            age=body_math.age_from(profile.birth_year) if profile else None,
            sex=profile.sex if profile else None,
            activity=profile.activity if profile else None,
            body_fat_pct=last.body_fat_pct if last else None,
            pregnant=bool(profile.pregnant) if profile else False,
            today=local_now(user).date(),
        )
    except body_math.PlanImpossible:
        return None


# ------------------------------------------------------------------ прогресс

async def day_progress_text(session, user: User, *, now: datetime) -> str | None:
    """Итог дня — то, что показывается после каждого приёма пищи.

    С целью — полоса коридора, остаток и полоса калорий текущего приёма;
    без цели «73 % нормы» ничего не значит, поэтому остаются только съеденные
    калории и подсказка завести цель. `None` — когда за день нечего показать
    (`spec/body.md` § Дневной коридор).
    """
    goal = await repo.get_active_goal(session, user)
    plan = await _plan_for(session, user) if goal is not None else None
    target = (plan.target_kcal if plan else None) or (goal.target_kcal if goal else None)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    totals = await repo.day_energy(
        session,
        user,
        start=to_utc(start_local, user),
        end=to_utc(start_local + timedelta(days=1), user),
    )
    meals, meal_bar = await _meal_progress(
        session, user, start_local=start_local, target_kcal=target
    )
    if not target:
        # Цели нет (или коридор не посчитался) — но съеденное за день человек
        # вправе видеть всегда; процентов и остатка без цели не показываем.
        if not totals["consumed_kcal"] and not totals["burned_kcal"]:
            return None
        return format_day_totals(
            body_math.day_balance(target_kcal=0.0, **totals), meals=meals
        )
    balance = body_math.day_balance(target_kcal=target, **totals)
    series = [
        (to_local(row.measured_at, user), row.weight_kg)
        for row in await repo.load_weights(session, user)
    ]
    return format_day_progress(
        balance, goal=goal, trend=body_math.weight_trend(series), meals=meals, meal=meal_bar
    )


#: сколько истории берём на оценку режима питания для строки «приёмов пищи»
MEALS_HISTORY_DAYS = 60


async def _meal_progress(
    session, user: User, *, start_local: datetime, target_kcal: float | None
) -> tuple[str, str]:
    """(строка «🍽 Приёмов пищи: N из M», полоса калорий текущего приёма).

    Считает `src/analytics/plate.py`: приёмом пищи считается сессия с настоящей
    едой, одинокий кофе — нет (`spec/plate.md`). Сбой расчёта убирает обе
    строки и не трогает остальной итог дня.

    Ориентир на один приём — суточный ориентир, делённый на число приёмов
    (`body_math.meal_target_kcal`). Число приёмов — по умолчанию 3, если
    пользователь не назвал своё (в анкете или `/set meals`) и статистика за
    последние `plate.RHYTHM_WINDOW_DAYS` дней не показывает больше трёх
    (`spec/plate.md` § Сколько приёмов пищи в день). Полоса не показывается
    без цели по калориям и для перекуса — только для настоящего блюда.
    """
    from src.analytics import plate as plate_math

    try:
        history = await repo.load_plate_meals(
            session, user, since=to_utc(start_local - timedelta(days=MEALS_HISTORY_DAYS), user)
        )
        now_utc = to_utc(start_local, user)
        rhythm = plate_math.measure_rhythm(
            history, meals_per_day=user.meals_per_day, tzinfo=user_tz(user), now=now_utc
        )
        day_start = now_utc
        done = plate_math.count_meals_today(
            history, day_start=day_start, window_min=rhythm.session_min
        )
        meals_line = format_meals_today(done, rhythm.meals_per_day)
    except Exception:
        log.exception("meals-per-day count failed")
        return "", ""

    if not target_kcal:
        return meals_line, ""
    try:
        # Статистика делит калораж на число приёмов, только когда их видно
        # больше 3 в день — иначе безопаснее суточный ориентир по умолчанию
        # (`spec/plate.md`); своя настройка пользователя всегда в приоритете.
        if rhythm.meals_source == "user":
            meals_for_kcal = rhythm.meals_per_day
        elif rhythm.meals_source == "stats" and rhythm.meals_per_day > plate_math.DEFAULT_MEALS_PER_DAY:
            meals_for_kcal = rhythm.meals_per_day
        else:
            meals_for_kcal = plate_math.DEFAULT_MEALS_PER_DAY
        today = [meal for meal in history if meal.eaten_at >= day_start]
        if not today:
            return meals_line, ""
        current = plate_math.group_sessions(today, window_min=rhythm.session_min)[-1]
        if not plate_math.is_meal(current.items):
            return meals_line, ""
        energy = await repo.day_energy(
            session, user, start=current.started_at, end=current.ended_at + timedelta(minutes=1)
        )
        balance = body_math.day_balance(
            target_kcal=body_math.meal_target_kcal(target_kcal, meals_for_kcal),
            consumed_kcal=energy["consumed_kcal"],
        )
        return meals_line, format_meal_progress(balance)
    except Exception:
        log.exception("meal calorie bar failed")
        return meals_line, ""


# ------------------------------------------------------------------ callbacks

@router.callback_query(F.data == "bd:close")
async def on_close(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "bd:menu")
async def on_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    text, has_goal, show_pregnancy = await _body_card(callback.from_user.id)
    await callback.message.answer(
        text, reply_markup=body_menu(has_goal=has_goal, show_pregnancy=show_pregnancy)
    )


@router.callback_query(F.data.in_({"bd:weight", "bd:goal"}))
@router.callback_query(F.data.startswith("bd:field:"))
async def on_field(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")
    field = raw[2] if len(raw) > 2 else raw[1]
    await callback.answer()
    if field == "sex":
        await callback.message.answer("⚧ Укажите пол — он нужен для формулы обмена.",
                                      reply_markup=sex_picker())
        return
    if field == "activity":
        await callback.message.answer(
            "🏃 Насколько подвижен ваш обычный день?", reply_markup=activity_picker()
        )
        return
    if field == "pregnant":
        await callback.message.answer("🤰 Сейчас беременны?", reply_markup=pregnancy_picker())
        return
    if field == "focus":
        from src.handlers.goals import ask_focus

        await ask_focus(callback.message, state)
        return
    await _ask_field(callback.message, state, field)


async def _ask_field(message: Message, state: FSMContext, field: str) -> None:
    await state.set_state(BodyFlow.awaiting)
    await state.update_data({FIELD_KEY: field})
    await message.answer(PROMPTS.get(field, "Напишите значение числом."), reply_markup=cancel_only())


@router.callback_query(F.data.startswith("bd:sex:"))
async def on_sex(callback: CallbackQuery) -> None:
    sex = callback.data.split(":")[2]
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, sex=sex)
    await callback.answer("Записал")
    await callback.message.edit_text(
        "⚧ Пол: " + ("мужской" if sex == "m" else "женский") + ". Открыть /body."
    )


@router.callback_query(F.data.startswith("bd:preg:"))
async def on_pregnant(callback: CallbackQuery) -> None:
    pregnant = callback.data.split(":")[2] == "y"
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, pregnant=pregnant)
    await callback.answer("Записал")
    await callback.message.edit_text(
        ("🤰 Беременность отмечена." if pregnant else "Беременность не отмечена.")
        + " Открыть /body."
    )


@router.callback_query(F.data.startswith("bd:act:"))
async def on_activity(callback: CallbackQuery) -> None:
    level = callback.data.split(":")[2]
    if level not in body_math.ACTIVITY_FACTORS:
        await callback.answer()
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, activity=level)
    await callback.answer("Записал")
    await callback.message.edit_text(
        f"🏃 Активность: {body_math.ACTIVITY_LABELS[level]}.\n"
        "Коридор калорий пересчитан — смотрите /body."
    )


@router.callback_query(F.data.startswith("bd:rate:"))
async def on_rate(callback: CallbackQuery, state: FSMContext) -> None:
    """Темп выбран кнопкой — цель сохраняется уже урезанной до безопасной."""
    rate = int(callback.data.split(":")[2]) / 100.0
    data = await state.get_data()
    target = data.get("goal_target")
    await state.clear()
    if not target:
        await callback.answer()
        await callback.message.answer("Цель устарела — задайте её заново: /body")
        return
    await callback.answer()
    text = await _save_goal(callback.from_user.id, target_weight_kg=float(target), rate=rate)
    await callback.message.edit_text(text)


# ------------------------------------------------------------------ ввод значений

@router.message(BodyFlow.awaiting)
async def on_value(message: Message, state: FSMContext, *, text_override: str | None = None) -> None:
    """Одно поле за раз: что именно ждём — в `body_field`."""
    text = (text_override or message.text or "").strip()
    data = await state.get_data()
    field = data.get(FIELD_KEY) or "weight"
    if field in TEXT_FIELDS:
        await state.clear()
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, message.chat.id)
            await repo.upsert_body_profile(session, user, conditions=normalize_conditions(text))
        await message.answer("🩺 Записал.", reply_markup=await menu_of(message.chat.id))
        return
    value = _first_number(text)
    if value is None:
        await message.answer("Нужно число. Например: «82,4».", reply_markup=cancel_only())
        return

    if field == "weight":
        await state.clear()
        await save_weight_entry(message, weight_kg=value, text=text, source="text")
        return
    if field == "goal":
        await offer_goal(message, state, target_weight_kg=value)
        return

    await state.clear()
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        if field == "height":
            if not 100.0 <= value <= 250.0:
                await message.answer("Рост в сантиметрах, от 100 до 250.")
                return
            await repo.upsert_body_profile(session, user, height_cm=value)
            answer = f"📏 Рост: {value:g} см"
        elif field == "age":
            if not 10 <= value <= 120:
                await message.answer("Возраст от 10 до 120 лет.")
                return
            year = local_now(user).year - int(value)
            await repo.upsert_body_profile(session, user, birth_year=year)
            answer = f"🎂 Возраст: {int(value)}"
        else:
            answer = "Не понял, какое поле заполняем. Откройте /body."
    await message.answer(answer, reply_markup=await menu_of(message.chat.id))


_NO_CONDITIONS = {"нет", "нету", "не", "no", "none", "-", "здоров", "здорова"}


def normalize_conditions(text: str) -> str | None:
    """«Нет» и подобное — не состояние, а его отсутствие: не хранить как текст."""
    cleaned = (text or "").strip()
    if not cleaned or cleaned.lower().strip(".! ") in _NO_CONDITIONS:
        return None
    return cleaned


def _first_number(text: str) -> float | None:
    import re

    match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)", text or "")
    return float(match.group(1).replace(",", ".")) if match else None


async def offer_goal(message: Message, state: FSMContext, *, target_weight_kg: float) -> None:
    """Кнопки темпа строятся из веса пользователя: вне безопасных рамок их нет.

    Цель кладётся в FSM, а не в callback-data: кнопка, нажатая через сутки,
    не должна воскрешать чужое число (`spec/bot.md`).
    """
    await state.update_data({"goal_target": target_weight_kg})
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        last = await repo.last_weight(session, user)
        weight = last.weight_kg if last else None
    kind = body_math.goal_kind(weight, target_weight_kg)
    if weight is None:
        await state.clear()
        await message.answer(
            "Сначала нужен текущий вес — напишите «вес 82,4», и я вернусь к цели.",
            reply_markup=await menu_of(message.chat.id),
        )
        return
    if kind == "maintain":
        await state.clear()
        text = await _save_goal(message.chat.id, target_weight_kg=target_weight_kg, rate=0.0)
        await message.answer(text, reply_markup=await menu_of(message.chat.id))
        return
    options = body_math.rate_options(weight, kind)
    low, high = body_math.safe_rate_range(weight, kind)
    word = "снижения" if kind == "lose" else "набора"
    await message.answer(
        f"🎯 Цель: {target_weight_kg:g} кг (сейчас {weight:g} кг).\n"
        f"Выберите темп {word}. Безопасный диапазон для вашего веса — "
        f"{low:g}–{high:g} кг в неделю; более быстрый темп диетологи не рекомендуют "
        "(мышцы и самочувствие страдают раньше, чем жировая масса).",
        reply_markup=rate_picker(options, recommended=options[len(options) // 2]),
    )


async def _save_goal(tg_id: int, *, target_weight_kg: float, rate: float) -> str:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        profile = await repo.get_body_profile(session, user)
        last = await repo.last_weight(session, user)
        weight = last.weight_kg if last else None
        kind = body_math.goal_kind(weight, target_weight_kg)
        now = local_now(user)
        try:
            plan = body_math.build_plan(
                kind=kind,
                weight_kg=weight,
                target_weight_kg=target_weight_kg,
                rate_kg_week=rate or None,
                height_cm=profile.height_cm if profile else None,
                age=body_math.age_from(profile.birth_year) if profile else None,
                sex=profile.sex if profile else None,
                activity=profile.activity if profile else None,
                body_fat_pct=last.body_fat_pct if last else None,
                pregnant=bool(profile.pregnant) if profile else False,
                today=now.date(),
            )
        except body_math.PlanImpossible as exc:
            reason = exc.args[0] if exc.args else ""
            if reason == "pregnant":
                return (
                    "При беременности цель на снижение веса я не строю — это вопрос "
                    "к врачу, который ведёт беременность, а не к калькулятору."
                )
            return (
                "При вашем росте и весе ИМТ уже ниже нормального диапазона, поэтому "
                "цель на снижение я не строю. Такой вопрос стоит обсуждать с врачом."
            )
        await repo.set_goal(
            session,
            user,
            kind=kind,
            target_weight_kg=target_weight_kg,
            start_weight_kg=weight,
            rate_kg_week=plan.rate_kg_week,
            target_kcal=plan.target_kcal,
            started_at=to_utc(now, user),
            target_date=(
                to_utc(datetime.combine(plan.eta, now.time()).replace(tzinfo=now.tzinfo), user)
                if plan.eta
                else None
            ),
        )
        # профиль нужен и для напоминаний о взвешивании
        await repo.upsert_body_profile(session, user)
    return format_goal_plan(plan, kind=kind, target_weight_kg=target_weight_kg) + (
        "\n\nПосле каждого приёма пищи буду показывать полосу дневного коридора."
    )


# ------------------------------------------------------------------ запись веса

async def save_weight_entry(
    message: Message,
    *,
    weight_kg: float,
    text: str = "",
    composition: dict[str, float] | None = None,
    source: str = "text",
    at: datetime | None = None,
) -> None:
    """Единственный путь записи взвешивания — им пользуются и текст, и фото."""
    if not 25.0 <= weight_kg <= 400.0:
        await message.answer("Вес должен быть от 25 до 400 кг.")
        return
    facts = parse_text(text).body if text else None
    payload = dict(composition or {})
    if facts is not None:
        payload.update(facts.composition)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        previous = await repo.last_weight(session, user)
        row = await repo.save_weight(
            session,
            user,
            measured_at=to_utc(at or now, user),
            weight_kg=weight_kg,
            composition=payload,
            source=source,
        )
        if facts is not None:
            await repo.upsert_body_profile(
                session,
                user,
                height_cm=facts.height_cm,
                sex=facts.sex,
                birth_year=(now.year - facts.age) if facts.age else None,
            )
        goal = await repo.get_active_goal(session, user)
        if previous is not None:
            previous.measured_at = to_local(previous.measured_at, user)
        row.measured_at = to_local(row.measured_at, user)
        answer = format_weight_saved(row, previous=previous, goal=goal)
        progress = await day_progress_text(session, user, now=now)
    if progress:
        answer += "\n\n" + progress
    await message.answer(answer, reply_markup=await menu_of(message.chat.id))


# ------------------------------------------------------------------ фото весов

async def show_measurement_draft(
    message: Message, state: FSMContext, draft: MeasurementDraft
) -> None:
    await state.set_state(BodyFlow.confirming)
    await state.update_data({DRAFT_KEY: measurement_to_dict(draft)})
    await message.answer(format_measurement_draft(draft), reply_markup=confirm_measurement())


@router.callback_query(F.data == "bd:save", BodyFlow.confirming)
async def on_save_measurement(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = measurement_from_dict(data.get(DRAFT_KEY) or {})
    await state.clear()
    await callback.answer()
    if not draft.weight_kg:
        await callback.message.answer(
            "Веса на фото не было — напишите его числом, состав тела я уже запомнил."
        )
        return
    composition = {
        "body_fat_pct": draft.body_fat_pct,
        "muscle_mass_kg": draft.muscle_mass_kg,
        "water_pct": draft.water_pct,
        "bone_mass_kg": draft.bone_mass_kg,
        "visceral_fat": draft.visceral_fat,
        "bmr_kcal": draft.bmr_kcal,
    }
    await save_weight_entry(
        callback.message,
        weight_kg=draft.weight_kg,
        composition={k: v for k, v in composition.items() if v is not None},
        source="photo",
    )


# ------------------------------------------------------------------ графики

@router.callback_query(F.data == "bd:chart")
async def on_chart(callback: CallbackQuery) -> None:
    await callback.answer()
    await send_weight_charts(callback.message, tg_id=callback.from_user.id)


async def send_weight_charts(message: Message, *, tg_id: int) -> bool:
    """График веса и, если биоимпеданс вводился, состав тела. `False` — нечего рисовать."""
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        rows = await repo.load_weights(session, user)
        goal = await repo.get_active_goal(session, user)
        series = [(to_local(row.measured_at, user), row.weight_kg) for row in rows]
        fat = [
            (to_local(row.measured_at, user), row.body_fat_pct)
            for row in rows
            if row.body_fat_pct is not None
        ]
        muscle = [
            (to_local(row.measured_at, user), row.muscle_mass_kg)
            for row in rows
            if row.muscle_mass_kg is not None
        ]
    if not series:
        return False
    corridor = None
    if goal is not None and goal.rate_kg_week:
        corridor = body_math.safe_corridor(
            series[0][0], series[0][1], rate_kg_week=goal.rate_kg_week, kind=goal.kind
        )
        # коридор не рисуем дальше последнего замера более чем на месяц
        horizon = series[-1][0] + timedelta(days=30)
        corridor = [point for point in corridor if point[0] <= horizon]
    png = render_weight(
        series,
        target_kg=goal.target_weight_kg if goal else None,
        corridor=corridor,
        title="Вес",
    )
    await message.answer_photo(
        BufferedInputFile(png, filename="weight.png"),
        caption="Замеры веса, цель и ожидаемый темп",
    )
    if len(fat) >= 2 or len(muscle) >= 2:
        composition = render_body_composition(fat, muscle, title="Состав тела")
        await message.answer_photo(
            BufferedInputFile(composition, filename="composition.png"),
            caption="Состав тела по вашим замерам",
        )
    return True


__all__ = [
    "day_progress_text",
    "offer_goal",
    "router",
    "save_weight_entry",
    "send_weight_charts",
    "show_measurement_draft",
]
