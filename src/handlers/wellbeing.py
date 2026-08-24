"""Wellbeing check-ins: score 1–5, then symptoms from a per-user glossary.

The glossary is dynamic (`spec/wellbeing.md`): it starts from a seeded list and
re-orders itself by how often each user actually taps a symptom; anything typed
or spoken that is not in the list is added to *that user's* glossary and becomes
a button next time. Score 5 short-circuits — feeling fine needs no symptom list.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import local_now, session_scope, to_utc
from src.handlers.states import WellbeingFlow
from src.keyboards import main_menu, symptom_picker, wellbeing_score
from src.vision import recognize

router = Router(name="wellbeing")

SELECTED_KEY = "wb_selected"
SCORE_KEY = "wb_score"
EXTRA_KEY = "wb_extra"
NOTE_KEY = "wb_note"

ASK_SCORE = "🙂 Как самочувствие прямо сейчас? 5 — отлично, 1 — очень плохо."


@router.message(Command("wellbeing"))
@router.message(F.text == "🙂 Самочувствие")
async def cmd_wellbeing(message: Message, state: FSMContext) -> None:
    await state.set_state(WellbeingFlow.scoring)
    await state.update_data({SELECTED_KEY: [], EXTRA_KEY: [], NOTE_KEY: None})
    await message.answer(ASK_SCORE, reply_markup=wellbeing_score())


@router.callback_query(F.data.startswith("wb:score:"))
async def on_score(callback: CallbackQuery, state: FSMContext) -> None:
    score = int(callback.data.rsplit(":", 1)[1])
    await callback.answer()
    if score >= 5:
        await _save(callback.from_user.id, state, score=score)
        await state.clear()
        await callback.message.edit_text("✅ Записал: самочувствие 5/5. Отлично!")
        return
    await state.set_state(WellbeingFlow.picking)
    await state.update_data({SCORE_KEY: score})
    keyboard, empty = await _picker(callback.from_user.id, state)
    hint = "" if not empty else "\nСписок пуст — добавьте свой симптом кнопкой «➕ Другое»."
    await callback.message.edit_text(
        f"Отметил {score}/5. Что беспокоит? Можно выбрать несколько.{hint}",
        reply_markup=keyboard,
    )


async def _picker(tg_id: int, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get(SELECTED_KEY) or [])
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        symptoms = await repo.list_symptoms(session, user)
        pairs = [(s.id, s.label) for s in symptoms]
    return symptom_picker(pairs, selected), not pairs


@router.callback_query(F.data.startswith("wb:sym:"), WellbeingFlow.picking)
async def on_symptom_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    symptom_id = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get(SELECTED_KEY) or [])
    selected.symmetric_difference_update({symptom_id})
    await state.update_data({SELECTED_KEY: sorted(selected)})
    keyboard, _ = await _picker(callback.from_user.id, state)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data == "wb:other", WellbeingFlow.picking)
async def on_other(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WellbeingFlow.free_text)
    await callback.answer()
    await callback.message.answer(
        "Напишите или наговорите, что чувствуете — добавлю в ваш список симптомов."
    )


@router.callback_query(F.data == "wb:voice", WellbeingFlow.picking)
async def on_voice_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WellbeingFlow.free_text)
    await callback.answer()
    await callback.message.answer("🎤 Запишите голосовое — расшифрую и разберу.")


@router.message(WellbeingFlow.free_text)
async def on_free_text(message: Message, state: FSMContext) -> None:
    await handle_free_text(message, state, message.text or "")


async def handle_free_text(message: Message, state: FSMContext, text: str) -> None:
    """Shared by typed text and voice transcripts (see `intake.on_voice`)."""
    if not text.strip():
        await message.answer("Не расслышал, попробуйте ещё раз.")
        return
    try:
        score, labels, note = await recognize.extract_symptoms(text)
    except recognize.RecognitionError:
        score, labels, note = None, [], text
    data = await state.get_data()
    extra = list(data.get(EXTRA_KEY) or [])
    for label in labels:
        if label not in extra:
            extra.append(label)
    updates = {EXTRA_KEY: extra, NOTE_KEY: note or text}
    if score is not None and data.get(SCORE_KEY) is None:
        updates[SCORE_KEY] = score
    await state.update_data(updates)

    if data.get(SCORE_KEY) is None and score is None:
        await state.set_state(WellbeingFlow.scoring)
        await message.answer(
            "Записал: " + (", ".join(extra) if extra else text) + "\n\n" + ASK_SCORE,
            reply_markup=wellbeing_score(),
        )
        return
    await state.set_state(WellbeingFlow.picking)
    keyboard, _ = await _picker(message.chat.id, state)
    await message.answer(
        "Добавил: " + (", ".join(labels) if labels else text) + "\nЕщё что-то?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "wb:done", WellbeingFlow.picking)
async def on_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    score = int(data.get(SCORE_KEY) or 3)
    labels = await _selected_labels(callback.from_user.id, state)
    await _save(
        callback.from_user.id,
        state,
        score=score,
        labels=labels,
        note=data.get(NOTE_KEY),
    )
    await state.clear()
    await callback.answer("Записано")
    body = ", ".join(labels) if labels else "без симптомов"
    await callback.message.edit_text(
        f"✅ Самочувствие {score}/5 · {body}\n"
        "Сопоставлю с сахаром и едой — смотрите /stats и /graph."
    )


async def _selected_labels(tg_id: int, state: FSMContext) -> list[str]:
    data = await state.get_data()
    selected = set(data.get(SELECTED_KEY) or [])
    extra = list(data.get(EXTRA_KEY) or [])
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        symptoms = await repo.list_symptoms(session, user, limit=100)
        labels = [s.label for s in symptoms if s.id in selected]
    for label in extra:
        if label not in labels:
            labels.append(label)
    return labels


async def _save(
    tg_id: int,
    state: FSMContext,
    *,
    score: int,
    labels: list[str] | None = None,
    note: str | None = None,
) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, tg_id)
        await repo.save_checkin(
            session,
            user,
            at=to_utc(local_now(user), user),
            score=score,
            symptom_labels=labels or [],
            note=note,
            source="buttons",
        )


__all__ = ["ASK_SCORE", "handle_free_text", "router"]
