"""Сквозные потоки тела и тренировок на лёгких фейках.

Фейки переиспользуются из `test_handlers_flow.py` — второй набор заглушек
расходился бы с первым.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.db import repo
from src.handlers import body, confirm, intake, workout
from src.handlers.states import BodyFlow, WorkoutFlow
from tests.test_handlers_flow import TG_ID, FakeCallback, FakeMessage

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


# ------------------------------------------------------------------ вес и цель

async def test_weight_with_bioimpedance_is_written_from_plain_text(engine, session, state):
    message = FakeMessage(text="вес 82,4 жир 24% мышцы 58 кг вода 55%")
    await intake.handle_text(message, state)

    user = await repo.get_user(session, TG_ID)
    rows = await repo.load_weights(session, user)
    assert len(rows) == 1
    assert rows[0].weight_kg == 82.4
    assert rows[0].body_fat_pct == 24.0
    assert rows[0].muscle_mass_kg == 58.0
    assert any("82,4" in text or "82.4" in text for text in message.texts)


async def test_profile_facts_from_text_land_in_the_profile(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.height_cm == 178.0
    assert profile.sex == "m"
    assert profile.birth_year


async def test_setting_a_goal_offers_only_safe_rates(engine, session, state):
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    message = FakeMessage(text="цель 80")
    await intake.handle_text(message, state)

    keyboard = message.sent[-1]["reply_markup"]
    rates = [
        float(button.callback_data.split(":")[2]) / 100
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data.startswith("bd:rate:")
    ]
    assert rates
    assert max(rates) <= 0.9  # 1 % от 90 кг


async def test_the_chosen_rate_becomes_a_stored_goal_with_a_daily_target(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)

    callback = FakeCallback(data="bd:rate:50", message=FakeMessage())
    await body.on_rate(callback, state)

    user = await repo.get_user(session, TG_ID)
    goal = await repo.get_active_goal(session, user)
    assert goal is not None
    assert goal.kind == "lose"
    assert goal.target_weight_kg == 80.0
    assert goal.rate_kg_week == 0.5
    assert goal.target_kcal and goal.target_kcal > 1500


async def test_pregnancy_blocks_a_weight_loss_goal(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 170 мне 30 пол женский"), state)
    user = await repo.get_user(session, TG_ID)
    await repo.upsert_body_profile(session, user, pregnant=True)
    await session.commit()

    await intake.handle_text(FakeMessage(text="вес 70"), state)
    await intake.handle_text(FakeMessage(text="цель 65"), state)

    callback = FakeCallback(data="bd:rate:50", message=FakeMessage())
    await body.on_rate(callback, state)

    assert "беременности" in callback.message.texts[-1]
    assert await repo.get_active_goal(session, user) is None


async def test_a_goal_named_before_the_first_weight_is_not_forgotten(engine, session, state):
    """«Сначала нужен вес» — обещание вернуться к цели, а не её потеря."""
    await body.on_field(FakeCallback(data="bd:goal", message=FakeMessage()), state)
    asked = FakeMessage(text="59")
    await body.on_value(asked, state)
    assert "59" in asked.texts[-1]  # цель названа и удержана

    weighed = FakeMessage(text="62")
    await body.on_value(weighed, state)

    keyboard = weighed.sent[-1]["reply_markup"]
    rates = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data.startswith("bd:rate:")
    ]
    assert rates  # к цели вернулись сами: предлагаем темп
    assert (await state.get_data())["goal_target"] == 59.0


async def test_a_weight_outside_the_limits_does_not_resume_the_goal(engine, session, state):
    await body.on_field(FakeCallback(data="bd:goal", message=FakeMessage()), state)
    await body.on_value(FakeMessage(text="59"), state)
    rejected = FakeMessage(text="4")
    await body.on_value(rejected, state)
    assert "от 25 до 400" in rejected.texts[-1]
    assert not any(
        (sent.get("reply_markup") and "bd:rate:" in str(sent["reply_markup"]))
        for sent in rejected.sent
    )


async def test_a_confirmed_meal_shows_the_daily_corridor_once_a_goal_exists(
    engine, session, state
):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    written = card.texts[-1]
    assert "Записано" in written
    assert "ккал" in written
    assert "▓" in written or "░" in written
    assert "Этот приём" in written


async def test_the_calorie_bar_and_the_plate_bar_live_side_by_side(engine, session, state):
    """Полосы две и на первом показе, и на втором: калории и состав тарелки."""
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    for _ in range(2):
        await intake.handle_text(FakeMessage(text="банан и бутерброд с сыром"), state)
        card = FakeMessage()
        await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)
        written = card.texts[-1]
        assert "📊 <b>Сегодня</b>" in written
        assert "🥗 <b>Тарелка</b> — " in written  # полоса состава, а не голый заголовок
        assert written.count("\n▓") + written.count("\n░") >= 1
        assert "ккал)" in written


async def test_without_a_goal_there_is_no_corridor_bar(engine, session, state):
    """Без цели нет коридора и процентов — только съеденное за день."""
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)
    written = card.texts[-1]
    assert "ккал)" not in written
    assert "Осталось на сегодня" not in written
    assert "Этот приём" not in written  # без цели нет и ориентира на приём


async def test_the_meal_bar_splits_the_target_by_three_meals_by_default(
    engine, session, state
):
    """Без явной настройки и статистики — по умолчанию делим на 3 приёма."""
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    plan = await body._plan_for(session, user)

    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    written = card.texts[-1]
    expected_target = round(plan.target_kcal / 3.0)
    assert f"из {expected_target} ккал" in written


async def test_the_meal_bar_follows_a_meals_per_day_the_user_set(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    user.meals_per_day = 2
    await session.commit()
    plan = await body._plan_for(session, user)

    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    written = card.texts[-1]
    expected_target = round(plan.target_kcal / 2.0)
    assert f"из {expected_target} ккал" in written


async def test_a_snack_alone_gets_no_meal_bar(engine, session, state):
    """Перекус (кофе) сам по себе — не приём пищи ни для тарелки, ни для калорий."""
    from src.handlers import views
    from src.vision.schemas import ItemDraft, MealDraft

    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    draft = MealDraft(
        title="Кофе с молоком",
        items=[ItemDraft(name="кофе с молоком", portion_g=200, kcal=90, tags=["milk"])],
    )
    await views.show_meal_draft(FakeMessage(), state, draft)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    assert "Этот приём" not in card.texts[-1]


async def test_the_guided_weight_prompt_writes_what_the_user_typed(engine, session, state):
    message = FakeMessage()
    await body.cmd_weight(message, state)
    assert await state.get_state() == BodyFlow.awaiting.state

    await body.on_value(FakeMessage(text="81,2"), state)
    user = await repo.get_user(session, TG_ID)
    assert (await repo.last_weight(session, user)).weight_kg == 81.2
    assert await state.get_state() is None


async def test_a_scale_photo_card_writes_the_composition_on_confirm(engine, session, state):
    from src.vision import recognize

    draft = await recognize.recognize_body_photo([])
    await body.show_measurement_draft(FakeMessage(), state, draft)
    assert await state.get_state() == BodyFlow.confirming.state

    await body.on_save_measurement(FakeCallback(data="bd:save", message=FakeMessage()), state)
    user = await repo.get_user(session, TG_ID)
    row = await repo.last_weight(session, user)
    assert row.weight_kg == 82.4
    assert row.body_fat_pct == 24.1
    assert row.source == "photo"


# ------------------------------------------------------------------ тренировки

async def test_a_workout_report_asks_the_missing_questions_then_writes(engine, session, state):
    message = FakeMessage(text="бегал 40 минут")
    await intake.handle_text(message, state)
    # длительность модель прочитала, значит спрашиваем про интенсивность
    assert await state.get_state() == WorkoutFlow.asking.state
    assert "тяжело" in message.texts[-1]

    await workout.on_intensity(FakeCallback(data="wo:int:moderate", message=FakeMessage()), state)
    card = FakeMessage()
    await workout.on_sweat(FakeCallback(data="wo:sweat:yes", message=card), state)
    assert await state.get_state() == WorkoutFlow.confirming.state
    assert "ккал" in card.texts[-1]

    await workout.workout_ok(FakeCallback(data="wo:ok", message=FakeMessage()), state)
    user = await repo.get_user(session, TG_ID)
    rows = await repo.load_workouts(session, user)
    assert len(rows) == 1
    assert rows[0].kind == "running"
    assert rows[0].duration_min == 40
    assert rows[0].kcal and rows[0].kcal > 0
    assert rows[0].kcal_source == "estimated"


async def test_the_estimate_uses_the_users_own_weight(engine, session, state):
    await intake.handle_text(FakeMessage(text="вес 110"), state)
    await intake.handle_text(FakeMessage(text="бегал 40 минут"), state)
    await workout.on_intensity(FakeCallback(data="wo:int:moderate", message=FakeMessage()), state)
    await workout.on_sweat(FakeCallback(data="wo:sweat:no", message=FakeMessage()), state)
    await workout.workout_ok(FakeCallback(data="wo:ok", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    heavy = (await repo.load_workouts(session, user))[0].kcal

    from src.analytics.workout import kcal_estimate

    default_weight = kcal_estimate(
        kind="running", intensity="moderate", minutes=40, distance_m=6500
    )
    assert heavy > default_weight.kcal


async def test_a_pulse_correction_reopens_the_card_with_the_new_number(engine, session, state):
    await intake.handle_text(FakeMessage(text="бегал 40 минут"), state)
    await workout.on_intensity(FakeCallback(data="wo:int:low", message=FakeMessage()), state)
    await workout.on_sweat(FakeCallback(data="wo:sweat:no", message=FakeMessage()), state)

    await workout.workout_hr(FakeCallback(data="wo:hr", message=FakeMessage()), state)
    assert await state.get_state() == WorkoutFlow.awaiting_hr.state
    card = FakeMessage()
    await workout.workout_apply_hr(FakeMessage(text="155"), state)
    await workout.workout_ok(FakeCallback(data="wo:ok", message=card), state)

    user = await repo.get_user(session, TG_ID)
    row = (await repo.load_workouts(session, user))[0]
    assert row.avg_hr == 155.0


async def test_a_workout_adds_to_the_daily_allowance(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    before = await body.day_progress_text(session, user, now=datetime.now().astimezone())

    await intake.handle_text(FakeMessage(text="бегал 40 минут"), state)
    await workout.on_intensity(FakeCallback(data="wo:int:high", message=FakeMessage()), state)
    await workout.on_sweat(FakeCallback(data="wo:sweat:yes", message=FakeMessage()), state)
    await workout.workout_ok(FakeCallback(data="wo:ok", message=FakeMessage()), state)

    after = await body.day_progress_text(session, user, now=datetime.now().astimezone())
    assert "тренировки" in after
    assert before != after



# ------------------------------------------------------------------ итог дня


async def test_the_day_bar_follows_every_confirmed_meal(engine, session, state):
    await intake.handle_text(FakeMessage(text="рост 178 мне 44 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:25", message=FakeMessage()), state)

    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    assert "Осталось на сегодня" in card.texts[-1]


async def test_without_a_goal_a_meal_still_shows_the_day_total(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    text = card.texts[-1]
    assert "Сегодня" in text and "съедено" in text
    assert "/body" in text  # подсказка завести цель
    assert "Осталось на сегодня" not in text  # остатка без цели не бывает


async def test_a_broken_day_bar_does_not_eat_the_confirmation(
    engine, session, state, monkeypatch
):
    async def boom(*args, **kwargs):
        raise RuntimeError("no plan today")

    monkeypatch.setattr(body, "day_progress_text", boom)
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)

    assert "Записано" in card.texts[-1]
    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["meals"] == 1

# ------------------------------------------------------------------ напоминание

class RecordingBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))


async def test_the_reminder_fires_once_per_interval_and_only_in_daytime(engine, session):
    from src import scheduler

    user = await repo.get_or_create_user(session, TG_ID)
    await repo.upsert_body_profile(session, user, height_cm=178.0)
    await repo.save_weight(session, user, measured_at=NOW, weight_kg=82.0)
    await session.commit()

    bot = RecordingBot()
    night = NOW.replace(hour=2) + timedelta(days=15)
    assert await scheduler.run_weight_reminders(bot, now=night) == 0

    day = NOW.replace(hour=9) + timedelta(days=15)
    assert await scheduler.run_weight_reminders(bot, now=day) == 1
    assert "взвес" in bot.messages[0][1].lower()
    # повторный тик через час не шлёт второе напоминание
    assert await scheduler.run_weight_reminders(bot, now=day + timedelta(hours=1)) == 0
