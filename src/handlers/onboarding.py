"""First-run questionnaire: age, height, weight, sex, conditions, goal.

Runs once, right after the welcome message, for a brand-new user
(`common.cmd_start`). Every step can be skipped or interrupted — a photo
switches straight to normal recognition, an explicit command or menu button
ends the questionnaire and lets the usual handler take over. Numeric fields,
weight and the goal reuse `handlers/body.py` so the dietetic safety clamps and
disclaimers stay in one place (`CLAUDE.md` #6/#7). See `spec/onboarding.md`.
"""

from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import download_photo, local_now, session_scope
from src.handlers.features import menu_of
from src.handlers.states import OnboardingFlow
from src.keyboards import (
    MENU_ROWS,
    onboarding_pregnancy_picker,
    onboarding_sex_picker,
    onboarding_skip,
)
from src.logging_setup import get_logger

router = Router(name="onboarding")
log = get_logger("handlers.onboarding")

STEP_KEY = "onb_step"
QUEUE_KEY = "onb_queue"
STEPS: tuple[str, ...] = ("age", "height", "weight", "sex", "conditions", "goal")

_MENU_TEXTS = {text for row in MENU_ROWS for text in row} | {"◀️ Меню", "❌ Отменить"}

PROMPTS = {
    "age": "🎂 Сколько вам полных лет? Это нужно для точного расчёта обмена веществ.",
    "height": "📏 Ваш рост в сантиметрах — например «178».",
    "weight": "⚖️ Ваш текущий вес числом — например «82,4». Можно прислать и фото весов.",
    "conditions": (
        "🩺 Есть ли особые состояния — беременность, диабет, заболевания почек и т.п.? "
        "Опишите словами или напишите «нет». Это не расшифровка и не назначение: я "
        "просто не буду предлагать снижение веса, если это опасно."
    ),
    "goal": (
        "🎯 Какой вес хотите видеть на весах? Напишите число — например «75». Если "
        "пока не думали об этом — пропустите, зададите цель позже в /body."
    ),
}

INTRO = (
    "Прежде чем начать — несколько вопросов о вас, они нужны для расчётов. Любой "
    "можно пропустить и заполнить позже через /body; состав тела (биоимпеданс) "
    "всегда можно добавить в любой момент — просто пришлите фото весов или напишите "
    "«вес 82,4 жир 24%»."
)

FINISH = (
    "Профиль готов, спасибо! Всё это можно поменять в /body в любой момент."
)


async def start(message: Message, state: FSMContext) -> None:
    """Called once, right after `common.WELCOME`, for a brand-new user."""
    await state.update_data({QUEUE_KEY: list(STEPS)})
    await message.answer(INTRO)
    await _ask_next(message, state)


async def _ask_next(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    queue = list(data.get(QUEUE_KEY) or [])
    if not queue:
        await _finish(message, state)
        return
    step = queue.pop(0)
    await state.update_data({QUEUE_KEY: queue, STEP_KEY: step})
    await state.set_state(OnboardingFlow.asking)
    if step == "sex":
        await message.answer(
            "⚧ Ваш пол — нужен для формулы обмена веществ.",
            reply_markup=onboarding_sex_picker(),
        )
        return
    if step == "pregnant":
        await message.answer("🤰 Вы сейчас беременны?", reply_markup=onboarding_pregnancy_picker())
        return
    await message.answer(PROMPTS[step], reply_markup=onboarding_skip())


async def _finish(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(FINISH, reply_markup=await menu_of(message.chat.id))


def _first_number(text: str) -> float | None:
    match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)", text or "")
    return float(match.group(1).replace(",", ".")) if match else None


def _looks_like_navigation(text: str | None) -> bool:
    return bool(text) and (text.startswith("/") or text in _MENU_TEXTS)


async def _should_handle(message: Message, state: FSMContext) -> bool:
    """Step aside for an explicit command/menu button or an active input mode.

    Without this, tapping «🍽 Записать еду» mid-questionnaire would have its
    following answer swallowed by the next onboarding question instead of
    reaching `intake` (`spec/onboarding.md`).
    """
    from src.handlers.intake import MODE_KEY

    data = await state.get_data()
    if data.get(MODE_KEY):
        return False
    if message.photo:
        return True
    return not _looks_like_navigation(message.text)


@router.callback_query(F.data == "onb:skip", OnboardingFlow.asking)
async def on_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_next(callback.message, state)


@router.callback_query(F.data.startswith("onb:sex:"), OnboardingFlow.asking)
async def on_sex(callback: CallbackQuery, state: FSMContext) -> None:
    sex = callback.data.split(":")[2]
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, sex=sex)
    await callback.answer("Записал")
    await callback.message.edit_reply_markup(reply_markup=None)
    if sex == "f":
        data = await state.get_data()
        queue = list(data.get(QUEUE_KEY) or [])
        queue.insert(0, "pregnant")
        await state.update_data({QUEUE_KEY: queue})
    await _ask_next(callback.message, state)


@router.callback_query(F.data.startswith("onb:preg:"), OnboardingFlow.asking)
async def on_pregnant(callback: CallbackQuery, state: FSMContext) -> None:
    pregnant = callback.data.split(":")[2] == "y"
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, pregnant=pregnant)
    await callback.answer("Записал")
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_next(callback.message, state)


@router.message(OnboardingFlow.asking, _should_handle)
async def on_answer(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.photo:
        await _handle_photo(message, state, bot)
        return
    text = (message.text or "").strip()
    data = await state.get_data()
    step = data.get(STEP_KEY) or "age"
    if step == "weight":
        await _answer_weight(message, state, text)
    elif step == "conditions":
        await _answer_conditions(message, state, text)
    elif step == "goal":
        await _answer_goal(message, state, text)
    else:
        await _answer_number(message, state, step, text)


async def _answer_number(message: Message, state: FSMContext, step: str, text: str) -> None:
    value = _first_number(text)
    if value is None:
        await message.answer("Нужно число, или нажмите «Пропустить».", reply_markup=onboarding_skip())
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        if step == "height":
            if not 100.0 <= value <= 250.0:
                await message.answer("Рост в сантиметрах, от 100 до 250.", reply_markup=onboarding_skip())
                return
            await repo.upsert_body_profile(session, user, height_cm=value)
        elif step == "age":
            if not 10 <= value <= 120:
                await message.answer("Возраст от 10 до 120 лет.", reply_markup=onboarding_skip())
                return
            year = local_now(user).year - int(value)
            await repo.upsert_body_profile(session, user, birth_year=year)
    await _ask_next(message, state)


async def _answer_weight(message: Message, state: FSMContext, text: str) -> None:
    value = _first_number(text)
    if value is None:
        await message.answer("Нужно число, или нажмите «Пропустить».", reply_markup=onboarding_skip())
        return
    if not 25.0 <= value <= 400.0:
        await message.answer("Вес должен быть от 25 до 400 кг.", reply_markup=onboarding_skip())
        return
    from src.handlers.body import save_weight_entry

    await save_weight_entry(message, weight_kg=value, text=text, source="text")
    await _ask_next(message, state)


async def _answer_conditions(message: Message, state: FSMContext, text: str) -> None:
    from src.handlers.body import normalize_conditions

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.upsert_body_profile(session, user, conditions=normalize_conditions(text))
    await _ask_next(message, state)


async def _answer_goal(message: Message, state: FSMContext, text: str) -> None:
    value = _first_number(text)
    if value is None:
        await message.answer("Нужно число, или нажмите «Пропустить».", reply_markup=onboarding_skip())
        return
    from src.handlers.body import offer_goal

    # Последний шаг: `offer_goal`/`bd:rate:` сами доводят цель до конца и
    # очищают состояние — новый вопрос анкеты после этого уже не нужен.
    await offer_goal(message, state, target_weight_kg=value)


async def _handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """A photo instead of a typed answer: figure out what it is and act on it.

    Food gets recognised as usual, a photo of scales or an analysis becomes
    that draft — whichever it is, the questionnaire stops here rather than
    blocking the photo; the rest of the profile waits in `/body`.
    """
    from src.handlers import intake
    from src.vision import recognize

    photo = message.photo[-1]
    try:
        image = await download_photo(bot, photo.file_id)
    except Exception:
        log.exception("onboarding photo download failed")
        await message.answer("Не удалось скачать фото, попробуйте ещё раз.")
        return
    await state.clear()
    await message.answer("📋 Профиль можно закончить в любой момент — /body.")
    status = await message.answer("🔎 Смотрю, что на фото…")
    kind, _confidence = await recognize.classify_photo([image])
    await status.delete()
    await intake._dispatch(
        message, state, [image], [photo.file_id], kind=kind, hint=message.caption or ""
    )


__all__ = ["router", "start"]
