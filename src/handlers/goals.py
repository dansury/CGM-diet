"""Goal picker: the multiple-choice question the questionnaire opens with.

Used twice — as the first onboarding step (`spec/onboarding.md`) and from the
«🎯 Мои цели» button of `/body`. The picker itself is the same in both places;
only what happens after «Готово» differs: onboarding moves on to the next
question, `/body` just confirms. Selection lives in the FSM, the saved answer
in `body_profile.focus` / `focus_note`.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import goals
from src.db import repo
from src.handlers.deps import session_scope
from src.handlers.features import menu_of
from src.handlers.states import GoalsFlow, OnboardingFlow
from src.keyboards import cancel_only, focus_picker
from src.logging_setup import get_logger

router = Router(name="goals")
log = get_logger("handlers.goals")

#: выбранные, но ещё не сохранённые цели
SELECTED_KEY = "focus_sel"
#: куда вернуться после «Своего варианта» — в анкету или в карточку /body
SKIPPABLE_KEY = "focus_skippable"

PROMPT = (
    "🎯 <b>С чего начнём?</b>\n"
    "Отметьте всё, что про вас — от этого зависит, о чём я буду говорить в первую "
    "очередь. Ответы можно поменять когда угодно в /body."
)
OTHER_PROMPT = "✍️ Напишите свою цель одной строкой — сохраню как есть."
SAVED = "🎯 Цели записал: {titles}."
EMPTY = "🎯 Цели не отмечены — ничего страшного, можно вернуться к ним в /body."


async def ask_focus(message: Message, state: FSMContext, *, skippable: bool = False) -> None:
    """Показать список целей. `skippable` — анкета (можно пропустить шаг)."""
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        profile = await repo.get_body_profile(session, user)
        selected = list(goals.decode(profile.focus if profile else None))
    await state.update_data({SELECTED_KEY: selected, SKIPPABLE_KEY: skippable})
    await message.answer(PROMPT, reply_markup=focus_picker(selected, skippable=skippable))


async def _selection(state: FSMContext) -> list[str]:
    data = await state.get_data()
    return list(data.get(SELECTED_KEY) or [])


async def _save(chat_id: int, keys: list[str], *, note: str | None = None) -> None:
    """Сохранить выбор. Пустая строка — «спросили, целей не назвал»."""
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, chat_id)
        fields: dict[str, str] = {"focus": goals.encode(keys)}
        if goals.CUSTOM in keys:
            if note is not None:
                fields["focus_note"] = note
        else:
            fields["focus_note"] = ""
        await repo.upsert_body_profile(session, user, **fields)


@router.callback_query(F.data.startswith("gl:pick:"))
async def on_pick(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in goals.BY_KEY:
        await callback.answer()
        return
    selected = await _selection(state)
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    await state.update_data({SELECTED_KEY: selected})
    data = await state.get_data()
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=focus_picker(selected, skippable=bool(data.get(SKIPPABLE_KEY)))
        )


@router.callback_query(F.data == "gl:other")
async def on_other(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(GoalsFlow.note)
    if callback.message is not None:
        await callback.message.answer(OTHER_PROMPT, reply_markup=cancel_only())


@router.message(GoalsFlow.note)
async def on_note(message: Message, state: FSMContext) -> None:
    await save_note(message, state, message.text or "")


async def save_note(message: Message, state: FSMContext, text: str) -> None:
    """Свой вариант цели: сохраняем как написано и возвращаем к списку."""
    note = goals.normalize_note(text)
    selected = await _selection(state)
    if note and goals.CUSTOM not in selected:
        selected.append(goals.CUSTOM)
    await state.update_data({SELECTED_KEY: selected})
    await _save(message.chat.id, selected, note=note)
    data = await state.get_data()
    skippable = bool(data.get(SKIPPABLE_KEY))
    # Анкета продолжается там же, где остановилась: свободный текст не должен
    # выбрасывать человека из неё (`spec/onboarding.md`).
    await state.set_state(OnboardingFlow.asking if skippable else None)
    await message.answer(
        "Записал. Что-то ещё отметить — или «Готово».",
        reply_markup=focus_picker(selected, skippable=skippable),
    )


@router.callback_query(F.data == "gl:done")
async def on_done(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers import onboarding

    selected = await _selection(state)
    data = await state.get_data()
    await _save(callback.from_user.id, selected)
    await callback.answer("Записал")
    note = None
    if goals.CUSTOM in selected:
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, callback.from_user.id)
            profile = await repo.get_body_profile(session, user)
            note = profile.focus_note if profile else None
    labels = goals.titles(selected, note=note)
    if callback.message is not None:
        await callback.message.edit_text(
            SAVED.format(titles=" · ".join(label.lower() for label in labels))
            if labels
            else EMPTY
        )
    if data.get(onboarding.STEP_KEY) == "focus":
        await onboarding.after_focus(callback.message, state, selected)
        return
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(
            "Открыть /body — там же можно поменять.", reply_markup=await menu_of(callback.from_user.id)
        )


__all__ = ["ask_focus", "router", "save_note"]
