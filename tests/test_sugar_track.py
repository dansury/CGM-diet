"""Сахарный трек анкеты и предложение присылать замеры.

`spec/onboarding.md` § Сахарный трек.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src import sugar
from src.db import repo
from src.handlers import goals as goals_handler
from src.handlers import onboarding
from src.handlers import sugar as sugar_handler
from tests.test_handlers_flow import TG_ID, FakeBot, FakeCallback, FakeMessage


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


async def _pick_goals(state, *keys: str) -> FakeMessage:
    card = FakeMessage()
    for key in keys:
        await goals_handler.on_pick(FakeCallback(data=f"gl:pick:{key}", message=card), state)
    await goals_handler.on_done(FakeCallback(data="gl:done", message=card), state)
    return card


# ---------------------------------------------------------------- каталог


def test_methods_round_trip_and_drop_unknown_keys():
    assert sugar.encode_methods(["cgm", "meter", "нечто"]) == "meter,cgm"
    assert sugar.decode_methods("meter,cgm") == ("meter", "cgm")
    assert sugar.tracks_glucose("meter") is True
    # «спросили, ничем не меряет» — не то же самое, что «не спрашивали»
    assert sugar.tracks_glucose("") is False
    assert sugar.tracks_glucose(None) is False


def test_no_is_not_a_medication():
    assert sugar.normalize_meds("  Нет. ") is None
    assert sugar.normalize_meds("") is None
    assert sugar.normalize_meds("метформин 1000 утром") == "метформин 1000 утром"


def test_the_track_belongs_to_the_sugar_goal_only():
    assert sugar.wants_sugar_track(["weight", "sugar"]) is True
    assert sugar.wants_sugar_track(["weight", "labs"]) is False


# ---------------------------------------------------------------- анкета


async def test_the_sugar_goal_adds_the_three_questions(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")

    assert any("диабет" in text.lower() for text in card.texts)
    data = await state.get_data()
    assert data[onboarding.STEP_KEY] == "dia"
    # остальные шаги трека ждут своей очереди, а вопрос о весе снят
    assert data[onboarding.QUEUE_KEY][:3] == ["dia_meds", "sugar_method", "sugar_pitch"]
    assert "goal" not in data[onboarding.QUEUE_KEY]


async def test_another_goal_never_sees_the_sugar_questions(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "weight")

    assert not any("диабет" in text.lower() for text in card.texts)
    data = await state.get_data()
    assert not set(onboarding.SUGAR_STEPS) & set(data[onboarding.QUEUE_KEY])


async def test_the_answers_are_written_down_as_given(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")

    await sugar_handler.on_diabetes(FakeCallback(data="sg:dia:t2", message=card), state)
    await onboarding.on_answer(FakeMessage(text="метформин 1000 утром"), state, FakeBot())

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.diabetes == "t2"
    assert profile.diabetes_meds == "метформин 1000 утром"


async def test_the_medication_question_asks_to_log_them_too(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")
    await sugar_handler.on_diabetes(FakeCallback(data="sg:dia:pre", message=card), state)

    asked = " ".join(card.texts)
    assert "/meds" in asked
    # бот записывает лекарства, но не назначает и не считает дозы
    assert "дозы я не считаю" in asked.lower()


async def test_measuring_turns_the_offer_on_and_explains_why(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")
    await sugar_handler.on_diabetes(FakeCallback(data="sg:dia:t1", message=card), state)
    await onboarding.on_answer(FakeMessage(text="нет"), state, FakeBot())
    await sugar_handler.on_method(FakeCallback(data="sg:m:cgm", message=card), state)
    await sugar_handler.on_done(FakeCallback(data="sg:done", message=card), state)

    user = await repo.get_user(session, TG_ID)
    await session.refresh(user)
    profile = await repo.get_body_profile(session, user)
    assert profile.glucose_methods == "cgm"
    assert user.glucose_prompt_enabled is True
    said = " ".join(card.texts)
    assert "каждый" in said.lower()  # педантичность проговаривается
    assert "замер" in said.lower()


async def test_measuring_nothing_leaves_the_user_alone(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")
    await sugar_handler.on_diabetes(FakeCallback(data="sg:dia:no", message=card), state)
    await onboarding.on_answer(FakeMessage(text="нет"), state, FakeBot())
    await sugar_handler.on_method(FakeCallback(data="sg:m:none", message=card), state)
    await sugar_handler.on_done(FakeCallback(data="sg:done", message=card), state)

    user = await repo.get_user(session, TG_ID)
    await session.refresh(user)
    profile = await repo.get_body_profile(session, user)
    assert profile.glucose_methods == ""      # спросили, ничем не меряет
    assert user.glucose_prompt_enabled is False


async def test_none_and_a_device_are_mutually_exclusive(engine, session, state):
    await state.update_data({sugar_handler.SELECTED_KEY: ["meter"]})
    card = FakeMessage()
    await sugar_handler.on_method(FakeCallback(data="sg:m:none", message=card), state)
    assert (await state.get_data())[sugar_handler.SELECTED_KEY] == ["none"]
    await sugar_handler.on_method(FakeCallback(data="sg:m:meter", message=card), state)
    assert (await state.get_data())[sugar_handler.SELECTED_KEY] == ["meter"]


async def test_the_pitch_survives_a_skipped_question(engine, session, state):
    """Пропуск вопроса о способах не должен унести с собой объяснение."""
    await onboarding.start(FakeMessage(), state)
    card = await _pick_goals(state, "sugar")
    await sugar_handler.on_diabetes(FakeCallback(data="sg:dia:unknown", message=card), state)
    await onboarding.on_skip(FakeCallback(data="onb:skip", message=card), state)  # лекарства
    await onboarding.on_skip(FakeCallback(data="onb:skip", message=card), state)  # способы

    assert any("Как это работает" in text for text in card.texts)


# ---------------------------------------------------------------- после еды


async def _record_a_meal(state) -> FakeMessage:
    from src.handlers import confirm, intake

    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)
    return card


async def test_the_reading_is_asked_for_after_every_meal(engine, session, state):
    user = await repo.get_or_create_user(session, TG_ID)
    user.glucose_prompt_enabled = True
    await session.commit()

    card = await _record_a_meal(state)
    said = " ".join(card.texts)
    assert "замер" in said.lower()
    markup = [entry.get("reply_markup") for entry in card.sent if entry.get("reply_markup")]
    buttons = [
        button.callback_data
        for row in (markup[-1].inline_keyboard if markup else [])
        for button in row
    ]
    assert "sg:log" in buttons


async def test_someone_who_does_not_measure_is_not_asked(engine, session, state):
    await repo.get_or_create_user(session, TG_ID)
    await session.commit()

    card = await _record_a_meal(state)
    assert "замер" not in " ".join(card.texts).lower()


async def test_the_button_opens_the_usual_glucose_input(engine, session, state):
    from src.handlers.intake import MODE_KEY

    card = FakeMessage()
    await sugar_handler.on_log_glucose(FakeCallback(data="sg:log", message=card), state)
    assert (await state.get_data())[MODE_KEY] == "glucose"
    assert any("глюкометр" in text.lower() for text in card.texts)


async def test_the_offer_can_be_switched_off_by_hand(engine, session, state):
    from src.handlers import common

    user = await repo.get_or_create_user(session, TG_ID)
    user.glucose_prompt_enabled = True
    await session.commit()

    await common.cmd_set(FakeMessage(text="/set sugar off"))
    await session.refresh(user)
    assert user.glucose_prompt_enabled is False
