"""Handler-level flows driven with lightweight fakes.

aiogram's dispatcher is not exercised here (that is the known gap in
`docs/bmad/06-qa-plan.md`), but every handler body is: routing decisions, FSM
transitions and the DB writes they cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.db import repo
from src.handlers import confirm, intake, reports, wellbeing
from src.handlers.states import MealFlow, ProductFlow, WellbeingFlow

TG_ID = 424242


@dataclass
class FakeUser:
    id: int = TG_ID
    username: str | None = "tester"
    first_name: str | None = "Тест"


@dataclass
class FakeChat:
    id: int = TG_ID


@dataclass
class FakePhoto:
    file_id: str = "photo-1"


@dataclass
class FakeMessage:
    text: str | None = None
    caption: str | None = None
    photo: list[FakePhoto] | None = None
    media_group_id: str | None = None
    document: Any = None
    voice: Any = None
    audio: Any = None
    chat: FakeChat = field(default_factory=FakeChat)
    from_user: FakeUser = field(default_factory=FakeUser)
    sent: list[dict] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"text": text, **kwargs})
        return self

    async def answer_photo(self, photo: Any, **kwargs: Any) -> FakeMessage:
        self.sent.append({"photo": photo, **kwargs})
        return self

    async def answer_document(self, document: Any, **kwargs: Any) -> FakeMessage:
        self.sent.append({"document": document, **kwargs})
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.sent.append({"edited": text, **kwargs})
        return self

    async def edit_reply_markup(self, **kwargs: Any) -> FakeMessage:
        self.sent.append({"markup": kwargs.get("reply_markup")})
        return self

    async def delete(self) -> None:
        self.sent.append({"deleted": True})

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


class FakeBot:
    """Only what the handlers touch: `download`."""

    def __init__(self, payload: bytes = b"fake-jpeg") -> None:
        self.payload = payload
        self.downloads: list[str] = []

    async def download(self, file_id: str):
        import io

        self.downloads.append(file_id)
        return io.BytesIO(self.payload)


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=TG_ID, user_id=TG_ID)
    )


# ------------------------------------------------------------------ text

async def test_plain_glucose_text_is_saved_without_a_draft(engine, session, state):
    message = FakeMessage(text="сахар 8.2")
    await intake.handle_text(message, state)
    assert any("Записал" in t for t in message.texts)

    user = await repo.get_user(session, TG_ID)
    readings = await repo.load_glucose(session, user)
    assert [r.value_mmol for r in readings] == [8.2]
    assert await state.get_state() is None  # no confirmation needed


async def test_mixed_text_saves_every_field(engine, session, state):
    message = FakeMessage(text="сахар 9.1, вес 72,3, самочувствие 3, выпила метформин 500 мг")
    await intake.handle_text(message, state)
    user = await repo.get_user(session, TG_ID)
    totals = await repo.counts(session, user)
    assert totals["glucose"] == 1
    assert totals["checkins"] == 1


async def test_meal_description_produces_a_draft_awaiting_confirmation(engine, session, state):
    message = FakeMessage(text="съела овсянку с бананом")
    await intake.handle_text(message, state)
    assert await state.get_state() == MealFlow.confirming.state
    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["meals"] == 0  # nothing written yet


async def test_confirming_the_draft_writes_the_meal(engine, session, state):
    message = FakeMessage(text="съела овсянку с бананом")
    await intake.handle_text(message, state)
    callback = FakeCallback(data="meal:ok", message=FakeMessage())
    await confirm.meal_ok(callback, state)

    user = await repo.get_user(session, TG_ID)
    meals = await repo.load_meals(session, user)
    assert len(meals) == 1
    assert meals[0].items
    assert await state.get_state() is None


async def test_editing_the_draft_records_a_correction(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)
    assert await state.get_state() == MealFlow.editing.state

    edit = FakeMessage(text="овсянка 300, банан 60")
    await confirm.meal_apply_edit(edit, state)
    assert await state.get_state() == MealFlow.confirming.state

    from sqlalchemy import select

    from src.db.models import Correction

    corrections = list(await session.scalars(select(Correction)))
    assert len(corrections) == 1
    assert "овсянка 300" in corrections[0].new_value


# ------------------------------------------------------------------ photos

async def test_a_photo_is_classified_and_becomes_a_meal_draft(engine, session, state):
    message = FakeMessage(photo=[FakePhoto()])
    await intake.on_photo(message, state, FakeBot())
    assert await state.get_state() == MealFlow.confirming.state
    assert any("Гречка" in t for t in message.texts)


async def test_check_mode_routes_a_photo_to_the_product_verdict(engine, session, state):
    await intake.start_check_mode(FakeMessage(), state)
    message = FakeMessage(photo=[FakePhoto()])
    await intake.on_photo(message, state, FakeBot())
    assert await state.get_state() == ProductFlow.confirming.state
    assert any("Проверка продукта" in t for t in message.texts)
    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["meals"] == 0  # nothing eaten yet


async def test_the_second_package_side_is_merged_into_one_card(engine, session, state):
    await intake.start_check_mode(FakeMessage(), state)
    await intake.on_photo(FakeMessage(photo=[FakePhoto("front")]), state, FakeBot())
    await confirm.product_more(FakeCallback(data="prod:more", message=FakeMessage()), state)
    assert await state.get_state() == ProductFlow.awaiting_second_side.state

    bot = FakeBot()
    await intake.on_photo(FakeMessage(photo=[FakePhoto("back")]), state, bot)
    assert bot.downloads == ["back", "front"]  # both sides re-sent together
    data = await state.get_data()
    from src.handlers.views import FILES_KEY

    assert data[FILES_KEY] == ["front", "back"]


async def test_saving_a_product_remembers_it(engine, session, state):
    await intake.start_check_mode(FakeMessage(), state)
    await intake.on_photo(FakeMessage(photo=[FakePhoto()]), state, FakeBot())
    await confirm.product_save(FakeCallback(data="prod:save", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["products"] == 1


# ------------------------------------------------------------------ wellbeing

async def test_score_five_closes_the_dialog_immediately(engine, session, state):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    callback = FakeCallback(data="wb:score:5", message=FakeMessage())
    await wellbeing.on_score(callback, state)

    assert await state.get_state() is None
    user = await repo.get_user(session, TG_ID)
    checkins = await repo.load_checkin_likes(session, user)
    assert [c.score for c in checkins] == [5]
    assert checkins[0].symptoms == []


async def test_low_score_opens_the_symptom_picker(engine, session, state):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:2", message=FakeMessage()), state)
    assert await state.get_state() == WellbeingFlow.picking.state


async def test_selected_symptoms_are_saved_and_counted(engine, session, state):
    user = await repo.get_or_create_user(session, TG_ID)
    symptoms = await repo.list_symptoms(session, user)
    target = symptoms[0]

    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:3", message=FakeMessage()), state)
    await wellbeing.on_symptom_toggle(
        FakeCallback(data=f"wb:sym:{target.id}", message=FakeMessage()), state
    )
    await wellbeing.on_done(FakeCallback(data="wb:done", message=FakeMessage()), state)

    checkins = await repo.load_checkin_likes(session, user)
    assert checkins[-1].symptoms == [target.label]
    await session.refresh(target)
    assert target.hits == 1


async def test_done_without_any_symptom_is_valid(engine, session, state):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:4", message=FakeMessage()), state)
    await wellbeing.on_done(FakeCallback(data="wb:done", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    checkins = await repo.load_checkin_likes(session, user)
    assert checkins[-1].score == 4 and checkins[-1].symptoms == []


async def test_free_text_adds_a_symptom_to_the_personal_glossary(engine, session, state):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:2", message=FakeMessage()), state)
    await wellbeing.on_other(FakeCallback(data="wb:other", message=FakeMessage()), state)
    await wellbeing.on_free_text(FakeMessage(text="кружится голова"), state)
    await wellbeing.on_done(FakeCallback(data="wb:done", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    labels = [s.label for s in await repo.list_symptoms(session, user, limit=100)]
    # the mock STT/extractor returns сонливость + потливость
    assert "сонливость" in labels
    checkins = await repo.load_checkin_likes(session, user)
    assert checkins[-1].symptoms


# ------------------------------------------------------------------ reports

async def test_today_lists_what_was_logged(engine, session, state):
    await intake.handle_text(FakeMessage(text="сахар 8.2"), state)
    message = FakeMessage()
    await reports.cmd_today(message)
    body = "\n".join(message.texts)
    assert "Сахар" in body and "8.2" in body


async def test_today_is_explicit_when_empty(engine, session, state):
    message = FakeMessage()
    await reports.cmd_today(message)
    assert "записей пока нет" in "\n".join(message.texts)


async def test_stats_explains_the_missing_data(engine, session, state):
    message = FakeMessage()
    await reports.cmd_stats(message)
    assert "недостаточно данных" in "\n".join(message.texts).lower()


async def test_export_sends_a_zip(engine, session, state):
    await repo.get_or_create_user(session, TG_ID)
    await session.commit()
    message = FakeMessage()
    await reports.cmd_export(message)
    document = message.sent[0]["document"]
    assert document.filename == "cgm-diet-export.zip"


async def test_delete_asks_before_wiping_and_then_wipes(engine, session, state):
    await intake.handle_text(FakeMessage(text="сахар 8.2"), state)
    prompt = FakeMessage()
    await reports.cmd_delete(prompt)
    assert "необратимо" in "\n".join(prompt.texts)

    await reports.on_delete_yes(FakeCallback(data="del:yes", message=FakeMessage()), state)
    user = await repo.get_user(session, TG_ID)
    assert set((await repo.counts(session, user)).values()) == {0}
