"""First-run questionnaire: goals first, then sequence, skip, interruption.

`spec/onboarding.md`.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.db import repo
from src.handlers import common, onboarding
from src.handlers import goals as goals_handler
from src.handlers.states import MealFlow, OnboardingFlow
from tests.test_handlers_flow import TG_ID, FakeBot, FakeCallback, FakeMessage, FakePhoto


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


async def _pick(state, *keys: str, message: FakeMessage | None = None) -> FakeMessage:
    """Отметить цели и нажать «Готово» — так проходится первый шаг анкеты."""
    card = message or FakeMessage()
    for key in keys:
        await goals_handler.on_pick(FakeCallback(data=f"gl:pick:{key}", message=card), state)
    await goals_handler.on_done(FakeCallback(data="gl:done", message=card), state)
    return card


async def test_cmd_start_runs_the_questionnaire_once_for_a_new_user(engine, session, state):
    message = FakeMessage()
    await common.cmd_start(message, state)
    assert await state.get_state() == OnboardingFlow.asking.state
    # Анкета начинается с целей, а не с рассказа о возможностях.
    assert any("С чего начнём" in t for t in message.texts)
    assert not any("возможность" in t for t in message.texts)

    # Повторный /start (или перезапуск) анкету больше не предлагает.
    await state.clear()
    second = FakeMessage()
    await common.cmd_start(second, state)
    assert await state.get_state() is None


async def test_start_asks_about_goals_first(engine, session, state):
    message = FakeMessage()
    await onboarding.start(message, state)
    assert await state.get_state() == OnboardingFlow.asking.state
    assert any("С чего начнём" in t for t in message.texts)
    data = await state.get_data()
    assert data[onboarding.STEP_KEY] == "focus"


async def test_goals_are_saved_and_the_questionnaire_goes_on(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    card = await _pick(state, "weight", "sugar")

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == "weight,sugar"
    # отмечен сахар — дальше идёт сахарный трек, а не возраст
    assert any("диабет" in text.lower() for text in card.texts)


async def test_a_goal_can_be_written_in_words(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    await onboarding.on_answer(FakeMessage(text="хочу лучше спать"), state, FakeBot())

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == "custom"
    assert profile.focus_note == "хочу лучше спать"
    # Анкета не сбилась: шаг тот же, список целей показан снова.
    assert await state.get_state() == OnboardingFlow.asking.state
    assert (await state.get_data())[onboarding.STEP_KEY] == "focus"


async def test_the_target_weight_is_asked_only_of_those_who_came_for_it(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    await _pick(state, "sugar")

    data = await state.get_data()
    assert "goal" not in data[onboarding.QUEUE_KEY]

    await onboarding.start(FakeMessage(), state)
    await _pick(state, "weight")
    assert "goal" in (await state.get_data())[onboarding.QUEUE_KEY]


async def test_walking_through_every_step_fills_the_profile(engine, session, state):
    await onboarding.start(FakeMessage(), state)

    await _pick(state, "weight")                                          # цели
    await onboarding.on_answer(FakeMessage(text="30"), state, FakeBot())   # age
    await onboarding.on_answer(FakeMessage(text="178"), state, FakeBot())  # height
    await onboarding.on_answer(FakeMessage(text="70"), state, FakeBot())   # weight
    assert await state.get_state() == OnboardingFlow.asking.state

    await onboarding.on_sex(FakeCallback(data="onb:sex:f", message=FakeMessage()), state)
    # Женский пол вставляет вопрос о беременности перед «особыми состояниями».
    data = await state.get_data()
    assert data.get(onboarding.STEP_KEY) == "pregnant"

    await onboarding.on_pregnant(FakeCallback(data="onb:preg:n", message=FakeMessage()), state)
    await onboarding.on_answer(FakeMessage(text="нет"), state, FakeBot())  # conditions
    await onboarding.on_answer(FakeMessage(text="70"), state, FakeBot())   # goal == текущий вес → maintain

    assert await state.get_state() is None  # анкета закончена

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile.focus == "weight"
    assert profile.height_cm == 178.0
    assert profile.sex == "f"
    assert profile.pregnant is False
    assert profile.conditions is None  # «нет» не хранится как состояние
    assert (await repo.last_weight(session, user)).weight_kg == 70.0
    goal = await repo.get_active_goal(session, user)
    assert goal is not None
    assert goal.kind == "maintain"


async def test_any_step_can_be_skipped(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    for _ in range(len(onboarding.STEPS)):
        await onboarding.on_skip(FakeCallback(data="onb:skip", message=FakeMessage()), state)
    assert await state.get_state() is None

    user = await repo.get_user(session, TG_ID)
    profile = await repo.get_body_profile(session, user)
    assert profile is None or profile.height_cm is None


async def test_a_photo_ends_the_questionnaire_and_is_recognised_as_food(engine, session, state):
    await onboarding.start(FakeMessage(), state)
    await _pick(state, "weight")
    message = FakeMessage(photo=[FakePhoto()])
    await onboarding.on_answer(message, state, FakeBot())

    assert await state.get_state() == MealFlow.confirming.state
    assert any("Гречка" in t for t in message.texts)
    assert any("/body" in t for t in message.texts)
