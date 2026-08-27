"""First-run questionnaire: sequence, skip, interruption by a photo.

`spec/onboarding.md`.
"""

from __future__ import annotations

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.db import repo
from src.handlers import common, onboarding
from src.handlers.states import MealFlow, OnboardingFlow
from tests.test_handlers_flow import TG_ID, FakeBot, FakeCallback, FakeMessage, FakePhoto


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


async def test_cmd_start_runs_the_questionnaire_once_for_a_new_user(engine, session, state):
    message = FakeMessage()
    await common.cmd_start(message, state)
    assert await state.get_state() == OnboardingFlow.asking.state
    assert any("лет" in t for t in message.texts)  # анкета началась сразу за приветствием

    # Повторный /start (или перезапуск) анкету больше не предлагает.
    await state.clear()
    second = FakeMessage()
    await common.cmd_start(second, state)
    assert await state.get_state() is None


async def test_start_asks_the_first_question(engine, session, state):
    message = FakeMessage()
    await onboarding.start(message, state)
    assert await state.get_state() == OnboardingFlow.asking.state
    assert any("лет" in t for t in message.texts)


async def test_walking_through_every_step_fills_the_profile(engine, session, state):
    await onboarding.start(FakeMessage(), state)

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
    message = FakeMessage(photo=[FakePhoto()])
    await onboarding.on_answer(message, state, FakeBot())

    assert await state.get_state() == MealFlow.confirming.state
    assert any("Гречка" in t for t in message.texts)
    assert any("/body" in t for t in message.texts)
