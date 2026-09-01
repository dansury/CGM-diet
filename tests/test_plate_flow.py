"""Тарелка и анализы в потоке обработчиков: что видит пользователь."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.db import repo
from src.handlers import body, common, confirm, dictionary, intake, labs, plate
from src.vision.schemas import ItemDraft, MealDraft

TG_ID = 626262
NOW = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)


@dataclass
class FakeUser:
    id: int = TG_ID
    username: str | None = "tester"
    first_name: str | None = "Тест"


@dataclass
class FakeChat:
    id: int = TG_ID


@dataclass
class FakeMessage:
    text: str | None = None
    chat: FakeChat = field(default_factory=FakeChat)
    from_user: FakeUser = field(default_factory=FakeUser)
    bot: Any = None
    sent: list[dict] = field(default_factory=list)
    reply_markup: Any = None

    async def answer(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"text": text, **kwargs})
        self.reply_markup = kwargs.get("reply_markup")
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"edited": text, **kwargs})
        self.reply_markup = kwargs.get("reply_markup")
        return self

    async def edit_reply_markup(self, **kwargs: Any) -> FakeMessage:
        self.reply_markup = kwargs.get("reply_markup")
        return self

    @property
    def texts(self) -> list[str]:
        return [s.get("text") or s.get("edited") or "" for s in self.sent]


@dataclass
class FakeCallback:
    data: str
    message: FakeMessage
    from_user: FakeUser = field(default_factory=FakeUser)
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append(text)


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


async def _confirm_draft(state: FSMContext, draft: MealDraft) -> FakeMessage:
    from src.handlers import views

    await views.show_meal_draft(FakeMessage(), state, draft)
    card = FakeMessage()
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=card), state)
    return card


async def _confirm_meal(state: FSMContext) -> FakeMessage:
    return await _confirm_draft(
        state,
        MealDraft(
            title="Гречка с курицей",
            items=[
                ItemDraft(name="гречка", portion_g=200, carbs_g=45, tags=["whole_grain"]),
                ItemDraft(name="курица", portion_g=150, tags=["protein"]),
            ],
        ),
    )


async def _confirm_snack(state: FSMContext) -> FakeMessage:
    return await _confirm_draft(
        state,
        MealDraft(
            title="Кофе с молоком",
            items=[ItemDraft(name="кофе с молоком", portion_g=200, tags=["milk"])],
        ),
    )


async def test_the_written_meal_shows_time_and_copyable_macros(engine, session, state):
    card = await _confirm_meal(state)
    written = card.texts[-1]
    assert "✅ Записано: " in written
    assert "<code>Гречка с курицей\n" in written
    assert "ккал" in written.split("</code>")[0]


async def test_every_item_gets_a_button_into_the_dictionary(engine, session, state):
    card = await _confirm_meal(state)
    markup = card.sent[-1]["reply_markup"]
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert len(labels) == 3  # две позиции и само блюдо
    assert all(label.startswith("⭐️ ") for label in labels)

    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    await dictionary.on_pin(FakeCallback(data=data[0], message=card))
    user = await repo.get_user(session, TG_ID)
    assert [e.label for e in await repo.list_dictionary(session, user, kind="item")] == ["гречка"]


async def test_the_day_summary_counts_the_meals(engine, session, state):
    card = await _confirm_draft(
        state,
        MealDraft(
            title="Гречка с курицей",
            items=[
                ItemDraft(name="гречка", portion_g=200, kcal=180, carbs_g=45, tags=["whole_grain"]),
                ItemDraft(name="курица", portion_g=150, kcal=250, tags=["protein"]),
            ],
        ),
    )
    assert "🍽 Приёмов пищи: 1 из 3" in card.texts[-1]


async def test_meal_confirmation_shows_a_calorie_bar_for_this_meal_when_a_goal_exists(
    engine, session, state
):
    await intake.handle_text(FakeMessage(text="рост 178 мне 30 пол мужской"), state)
    await intake.handle_text(FakeMessage(text="вес 90"), state)
    await intake.handle_text(FakeMessage(text="цель 80"), state)
    await body.on_rate(FakeCallback(data="bd:rate:50", message=FakeMessage()), state)

    card = await _confirm_draft(
        state,
        MealDraft(
            title="Гречка с курицей",
            items=[
                ItemDraft(name="гречка", portion_g=200, kcal=180, carbs_g=45, tags=["whole_grain"]),
                ItemDraft(name="курица", portion_g=150, kcal=250, tags=["protein"]),
            ],
        ),
    )
    written = card.texts[-1]
    assert "🍽" in written
    assert "430" in written  # съедено за этот приём
    assert "ккал на приём" in written


async def test_no_active_goal_means_no_calorie_bar_for_the_meal(engine, session, state):
    card = await _confirm_meal(state)
    assert "ккал на приём" not in card.texts[-1]


async def test_the_plate_is_scored_right_after_the_meal_is_written(engine, session, state):
    card = await _confirm_meal(state)
    written = card.texts[-1]
    assert "Тарелка" in written
    assert "овощи" in written
    assert "приём" in written.lower()


async def test_a_snack_alone_says_nothing_about_the_plate(engine, session, state):
    card = await _confirm_snack(state)
    assert "Тарелка" not in card.texts[-1]


async def test_a_snack_lands_in_the_plate_of_the_meal_that_follows(engine, session, state):
    await _confirm_snack(state)
    card = await _confirm_meal(state)
    written = card.texts[-1]
    assert "Тарелка" in written
    # 200 г кофе с молоком вошли в состав той же тарелки
    assert "прочее" in written


async def test_an_even_plate_is_left_without_advice(engine, session, state):
    card = await _confirm_draft(
        state,
        MealDraft(
            title="Салат с курицей и гречкой",
            items=[
                ItemDraft(name="салат", portion_g=300, tags=["vegetable"]),
                ItemDraft(name="яблоко", portion_g=100, tags=["fruit"]),
                ItemDraft(name="гречка", portion_g=200, tags=["whole_grain"]),
                ItemDraft(name="курица", portion_g=200, tags=["protein"]),
            ],
        ),
    )
    assert "Тарелка" not in card.texts[-1]


async def test_the_plate_can_be_switched_off_in_settings(engine, session, state):
    await common.cmd_set(FakeMessage(text="/set plate off"))
    user = await repo.get_user(session, TG_ID)
    await session.refresh(user)
    assert user.plate_enabled is False

    card = await _confirm_meal(state)
    assert "Тарелка" not in card.texts[-1]


async def test_meals_per_day_is_settable_and_resettable(engine, session):
    message = FakeMessage(text="/set meals 5")
    await common.cmd_set(message)
    user = await repo.get_user(session, TG_ID)
    await session.refresh(user)
    assert user.meals_per_day == 5
    assert "5" in message.texts[-1]

    await common.cmd_set(FakeMessage(text="/set meals auto"))
    await session.refresh(user)
    assert user.meals_per_day is None

    bad = FakeMessage(text="/set meals 99")
    await common.cmd_set(bad)
    assert "Не понял значение" in bad.texts[-1]


async def test_the_plate_card_shows_the_measured_rhythm(engine, session):
    await repo.get_or_create_user(session, TG_ID)
    await session.commit()
    message = FakeMessage()
    await plate.cmd_plate(message)
    text = message.texts[-1]
    assert "Гарвардская тарелка" in text
    assert "Приёмов пищи в день: 3" in text


async def test_repo_hands_the_plate_math_portions_and_tags(engine, session, state):
    await _confirm_meal(state)
    user = await repo.get_user(session, TG_ID)
    meals = await repo.load_plate_meals(session, user)
    assert [item.portion_g for item in meals[0].items] == [200, 150]
    assert meals[0].items[0].tags == ["whole_grain"]


async def test_saving_a_lab_panel_answers_with_food_sources(engine, session, state):
    from src.handlers import views
    from src.vision import recognize

    draft = await recognize.recognize_labs(text="ферритин 8", now=NOW)
    await views.show_lab_draft(FakeMessage(), state, draft)
    card = FakeMessage()
    await confirm.lab_ok(FakeCallback(data="lab:ok", message=card), state)

    written = "\n".join(card.texts)
    assert "Сохранено показателей" in written
    assert "Ферритин" in written
    assert "чечевица" in written
    assert "врачу" in written


async def test_labs_command_reports_the_latest_panel(engine, session, state):
    from src.handlers import views
    from src.vision import recognize

    draft = await recognize.recognize_labs(text="ферритин 8", now=NOW)
    await views.show_lab_draft(FakeMessage(), state, draft)
    await confirm.lab_ok(FakeCallback(data="lab:ok", message=FakeMessage()), state)

    message = FakeMessage()
    await labs.cmd_labs(message)
    assert "Ферритин" in message.texts[-1]
