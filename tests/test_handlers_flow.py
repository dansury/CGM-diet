"""Handler-level flows driven with lightweight fakes.

aiogram's dispatcher is not exercised here (that is the known gap in
`docs/bmad/06-qa-plan.md`), but every handler body is: routing decisions, FSM
transitions and the DB writes they cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import load_settings
from src.db import repo
from src.handlers import common, confirm, dictionary, intake, reports, wellbeing
from src.handlers.states import MealFlow, ProductFlow, WellbeingFlow

TG_ID = 424242
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


@dataclass
class FakeUser:
    id: int = TG_ID
    username: str | None = "tester"
    first_name: str | None = "Тест"


@dataclass
class FakeChat:
    id: int = TG_ID


@dataclass
class FakeVoice:
    file_id: str = "voice-1"
    mime_type: str | None = "audio/ogg"


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
    bot: Any = None
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


async def test_a_correction_photo_is_merged_into_the_draft(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)
    assert await state.get_state() == MealFlow.editing.state

    photo_message = FakeMessage(photo=[FakePhoto()], caption="ещё индейка")
    await confirm.meal_apply_edit_photo(photo_message, state, FakeBot())
    assert await state.get_state() == MealFlow.confirming.state
    assert any("Гречка" in t for t in photo_message.texts)  # ответ мока на фото-правку

    from sqlalchemy import select

    from src.db.models import Correction

    corrections = list(await session.scalars(select(Correction)))
    assert len(corrections) == 1
    assert corrections[0].new_value == "ещё индейка"


async def test_the_bju_button_stores_the_numbers_and_says_so(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_macros(FakeCallback(data="meal:macros", message=FakeMessage()), state)
    assert await state.get_state() == MealFlow.editing_macros.state

    edit = FakeMessage(text="овсяная каша 200 г б 12 ж 6 у 40")
    await confirm.meal_apply_macros(edit, state)
    assert await state.get_state() == MealFlow.confirming.state
    assert any("Запомнил ваши БЖУ" in t for t in edit.texts)

    user = await repo.get_user(session, TG_ID)
    memory = await repo.load_nutrition_memory(session, user)
    # 12 г белка на 200 г → 6 г на 100 г
    assert memory["овсяная каша"].protein_g == 6.0


async def test_the_bju_button_rejects_a_phrase_without_numbers(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_macros(FakeCallback(data="meal:macros", message=FakeMessage()), state)
    edit = FakeMessage(text="ну примерно как обычно")
    await confirm.meal_apply_macros(edit, state)
    assert await state.get_state() == MealFlow.editing_macros.state  # ждём числа дальше
    assert any("Не понял числа" in t for t in edit.texts)


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


async def test_a_saved_label_remembers_its_macros(engine, session, state):
    await intake.start_check_mode(FakeMessage(), state)
    await intake.on_photo(FakeMessage(photo=[FakePhoto()]), state, FakeBot())
    message = FakeMessage()
    await confirm.product_save(FakeCallback(data="prod:save", message=message), state)

    user = await repo.get_user(session, TG_ID)
    memory = await repo.load_nutrition_memory(session, user)
    assert memory  # числа с этикетки закреплены за продуктом
    assert any("этикетки" in t for t in message.texts)


async def test_editing_the_label_bju_overrides_the_package(engine, session, state):
    await intake.start_check_mode(FakeMessage(), state)
    await intake.on_photo(FakeMessage(photo=[FakePhoto()]), state, FakeBot())
    await confirm.product_macros(FakeCallback(data="prod:macros", message=FakeMessage()), state)
    assert await state.get_state() == ProductFlow.editing_macros.state

    edit = FakeMessage(text="ккал 200 б 16 ж 2 у 12")
    await confirm.product_apply_macros(edit, state)
    assert await state.get_state() == ProductFlow.confirming.state
    assert any("Запомнил ваши БЖУ" in t for t in edit.texts)

    user = await repo.get_user(session, TG_ID)
    memory = await repo.load_nutrition_memory(session, user)
    assert any(value.carbs_g == 12.0 for value in memory.values())


# ------------------------------------------------------------------ Samsung Health

async def test_health_instruction_is_available_from_the_card(engine, session, state):
    message = FakeMessage()
    await reports.cmd_health(message)
    assert any("Samsung Health" in t for t in message.texts)

    card = FakeMessage()
    await reports.on_health_step(FakeCallback(data="hs:how", message=card))
    assert any("Health Connect" in t for t in card.texts)

    keys = FakeMessage()
    await reports.on_health_step(FakeCallback(data="hs:keys", message=keys))
    assert any("cgmdiet://setup" in t or "HEALTH_SYNC_SECRET" in t for t in keys.texts)


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


# ------------------------------------------------------------------ corrections

async def test_a_correction_merges_instead_of_replacing(engine, session, state):
    message = FakeMessage(photo=[FakePhoto()])
    await intake.on_photo(message, state, FakeBot())  # гречка + курица + салат
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)

    card = FakeMessage(text="убери салат")
    await confirm.meal_apply_edit(card, state)
    assert await state.get_state() == MealFlow.confirming.state
    text = " ".join(card.texts)
    assert "Учтено из вашей правки" in text
    items_block = text.split("Учтено из вашей правки")[0]
    bullets = [line for line in items_block.splitlines() if line.startswith("• ")]
    assert any("куриная грудка" in b for b in bullets)  # untouched item survived
    assert any("гречневая каша — 180 г" in b for b in bullets)  # with its numbers
    assert not any("салат" in b for b in bullets)
    assert "убрано: салат из огурцов" in text  # and the change is echoed back


async def test_a_correction_can_be_spoken(engine, session, state):
    await intake.on_photo(FakeMessage(photo=[FakePhoto()]), state, FakeBot())
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)

    voice = FakeMessage(text=None)
    handled = await intake._route_voice(voice, state, "гречки было 250")
    assert handled  # a voice note in editing must not start a new meal
    assert await state.get_state() == MealFlow.confirming.state
    assert any("250 г" in t for t in voice.texts)


# ------------------------------------------------------------------ medications

async def test_a_medication_photo_becomes_a_journal_entry(engine, session, state):
    from src.handlers import meds
    from src.handlers.states import MedicationFlow

    message = FakeMessage(photo=[FakePhoto()])
    await intake._process_medication(message, state, [b""], ["photo-1"])
    assert await state.get_state() == MedicationFlow.confirming.state
    assert any("Глюкофаж" in t for t in message.texts)

    await meds.med_ok(FakeCallback(data="med:ok", message=FakeMessage()), state)
    user = await repo.get_user(session, TG_ID)
    rows = await repo.load_medications(session, user)
    assert [r.name for r in rows] == ["Глюкофаж 850 мг"]
    assert rows[0].slug == "глюкофаж" and rows[0].cid  # reference key resolved


async def test_a_medication_correction_keeps_what_was_not_mentioned(engine, session, state):
    from src.handlers import meds

    await intake._process_medication(FakeMessage(photo=[FakePhoto()]), state, [b""], [])
    card = FakeMessage(text="1000 мг")
    await meds.med_apply_edit(card, state)
    text = " ".join(card.texts)
    assert "Глюкофаж" in text  # name survived a dose-only correction
    assert "1000 мг" in text


# ------------------------------------------------------------------ dictionary

async def test_the_second_meal_offers_a_one_tap_shortcut(engine, session, state):
    from src.handlers import dictionary

    for _ in range(2):
        await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
        await confirm.meal_ok(FakeCallback(data="meal:ok", message=FakeMessage()), state)

    user = await repo.get_user(session, TG_ID)
    assert [e.label for e in await repo.list_dictionary(session, user, kind="meal")] == [
        "Овсянка с бананом"
    ]

    typed = FakeMessage(text="овся")
    assert await dictionary.offer_suggestions(typed, state, "овся")
    assert any("уже записывали" in t for t in typed.texts)


async def test_typing_a_known_dish_does_not_call_the_model(engine, session, state, mock_llm):
    from src.handlers import dictionary  # noqa: F401

    for _ in range(2):
        await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
        await confirm.meal_ok(FakeCallback(data="meal:ok", message=FakeMessage()), state)

    before = len(mock_llm.calls)
    await intake.handle_text(FakeMessage(text="овсянка с бананом"), state)
    assert len(mock_llm.calls) == before  # answered from the dictionary


# ------------------------------------------------------------------ БЖУ memory

async def test_typed_macros_are_remembered_and_the_user_is_told(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)

    edit = FakeMessage(text="овсянка б 12 ж 6 у 40")
    await confirm.meal_apply_edit(edit, state)
    assert any("Запомнил" in t for t in edit.texts)
    assert any("Б 12" in t for t in edit.texts)

    user = await repo.get_user(session, TG_ID)
    memory = await repo.load_nutrition_memory(session, user)
    assert memory  # per-100 g row is there
    assert any(m.protein_g for m in memory.values())


async def test_a_remembered_dish_comes_back_with_the_users_numbers(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    await confirm.meal_edit(FakeCallback(data="meal:edit", message=FakeMessage()), state)
    await confirm.meal_apply_edit(FakeMessage(text="овсянка 200 г б 12 ж 6 у 40"), state)
    await confirm.meal_ok(FakeCallback(data="meal:ok", message=FakeMessage()), state)

    again = FakeMessage(text="съела овсянку с бананом")
    await intake.handle_text(again, state)
    data = await state.get_data()
    from src.vision.schemas import meal_from_dict

    draft = meal_from_dict(data["draft"])
    oats = next(i for i in draft.items if "овсян" in i.name.lower())
    assert oats.macros_source == "memory"
    assert oats.protein_g == 12.0  # та же порция — те же числа
    assert oats.estimated is False


# ------------------------------------------------------------------ voice

async def test_voice_is_transcribed_by_speechkit_when_it_is_configured(
    engine, session, state, monkeypatch
):
    from src.config import Settings
    from src.ingest import speechkit

    settings = load_settings()
    monkeypatch.setattr(
        "src.handlers.intake.load_settings",
        lambda: replace(
            settings,
            yandex_speechkit_api_key="key",
            yandex_folder_id="folder",
        ),
    )
    assert Settings(yandex_speechkit_api_key="k", yandex_folder_id="f").speechkit_available

    seen: dict[str, Any] = {}

    async def fake_recognize(audio, **kwargs):
        seen.update(kwargs)
        seen["bytes"] = len(audio)
        return speechkit.SpeechResult(text="сахар 8.2", duration_sec=2.0, language="ru-RU")

    monkeypatch.setattr(speechkit, "recognize_voice", fake_recognize)

    message = FakeMessage(voice=FakeVoice())
    await intake.on_voice(message, state, FakeBot(payload=b"OggS-fake"))
    assert seen["api_key"] == "key" and seen["folder_id"] == "folder"
    assert any("Расслышал" in t for t in message.texts)

    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["glucose"] == 1  # «сахар 8.2» записан


async def test_voice_falls_back_to_the_llm_when_speechkit_is_not_configured(
    engine, session, state
):
    message = FakeMessage(voice=FakeVoice())
    await intake.on_voice(message, state, FakeBot(payload=b"OggS-fake"))
    assert any("Расслышал" in t for t in message.texts)


async def test_macros_typed_with_the_meal_are_kept_and_remembered(engine, session, state):
    message = FakeMessage(text="съела овсянку 200 г б 12 ж 6 у 40")
    await intake.handle_text(message, state)

    from src.vision.schemas import meal_from_dict

    draft = meal_from_dict((await state.get_data())["draft"])
    oats = next(i for i in draft.items if "овсян" in i.name.lower())
    assert (oats.protein_g, oats.fat_g, oats.carbs_g) == (12.0, 6.0, 40.0)
    assert oats.macros_source == "user"
    assert any("Запомнил" in t for t in message.texts)

    user = await repo.get_user(session, TG_ID)
    memory = await repo.load_nutrition_memory(session, user)
    assert memory  # 12 г на 200 г → 6 г на 100 г
    assert next(iter(memory.values())).protein_g == 6.0


async def test_typing_macros_skips_the_dictionary_shortcut(engine, session, state, mock_llm):
    from src.handlers import dictionary  # noqa: F401

    for _ in range(2):
        await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
        await confirm.meal_ok(FakeCallback(data="meal:ok", message=FakeMessage()), state)

    before = len(mock_llm.calls)
    typed = FakeMessage(text="овсянка с бананом б 14 ж 7 у 44")
    await intake.handle_text(typed, state)
    assert len(mock_llm.calls) > before          # словарь не перехватил ввод с числами
    assert not any("уже записывали" in t for t in typed.texts)
    assert any("Запомнил" in t for t in typed.texts)


async def test_a_meal_without_macros_still_goes_through_the_model_unchanged(
    engine, session, state
):
    message = FakeMessage(text="съела овсянку с бананом")
    await intake.handle_text(message, state)
    assert not any("Запомнил" in t for t in message.texts)
    user = await repo.get_user(session, TG_ID)
    assert await repo.load_nutrition_memory(session, user) == {}


# ------------------------------------------------------------------ отмена

async def test_the_cross_cancels_a_meal_card_without_saving(engine, session, state):
    await intake.handle_text(FakeMessage(text="съела овсянку с бананом"), state)
    assert await state.get_state() == MealFlow.confirming.state

    card = FakeMessage()
    await common.on_cancel(FakeCallback(data="x:cancel", message=card), state)

    assert await state.get_state() is None
    assert any("Отменено" in t for t in card.texts)
    user = await repo.get_user(session, TG_ID)
    assert (await repo.counts(session, user))["meals"] == 0


async def test_the_cross_cancels_the_wellbeing_survey(engine, session, state):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:2", message=FakeMessage()), state)
    await common.on_cancel(FakeCallback(data="x:cancel", message=FakeMessage()), state)

    assert await state.get_state() is None
    user = await repo.get_user(session, TG_ID)
    assert await repo.load_checkin_likes(session, user) == []


# ------------------------------------------------------------------ ротация

async def test_a_typed_symptom_heads_the_buttons_and_is_already_ticked(
    engine, session, state
):
    await wellbeing.cmd_wellbeing(FakeMessage(), state)
    await wellbeing.on_score(FakeCallback(data="wb:score:2", message=FakeMessage()), state)
    await wellbeing.on_other(FakeCallback(data="wb:other", message=FakeMessage()), state)
    await wellbeing.on_free_text(FakeMessage(text="кружится голова"), state)

    user = await repo.get_user(session, TG_ID)
    buttons = await repo.list_symptoms(session, user)
    # мок-экстрактор возвращает сонливость + потливость — они и есть последний ввод
    assert {s.label for s in buttons[:2]} == {"сонливость", "потливость"}
    data = await state.get_data()
    assert set(data["wb_selected"]) == {buttons[0].id, buttons[1].id}


async def test_a_symptom_button_in_the_dictionary_opens_the_survey(engine, session, state):
    user = await repo.get_or_create_user(session, TG_ID)
    await repo.save_checkin(session, user, at=NOW, score=2, symptom_labels=["жажда"])
    entry = (await repo.list_dictionary(session, user, kind="symptom"))[0]

    card = FakeMessage()
    await dictionary.on_use(FakeCallback(data=f"dict:use:{entry.id}", message=card), state)

    assert await state.get_state() == WellbeingFlow.scoring.state
    assert any("жажда" in t for t in card.texts)
    data = await state.get_data()
    assert data["wb_selected"]
