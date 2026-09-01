"""First-run questionnaire: goals, age, height, weight, sex, conditions, target weight.

Runs once, right after the welcome message, for a brand-new user
(`common.cmd_start`). Every step can be skipped or interrupted — a photo
switches straight to normal recognition, an explicit command or menu button
ends the questionnaire and lets the usual handler take over. The first step is
the goal picker (`handlers/goals.py`) — the answer decides whether a target
weight is worth asking at all. Numeric fields,
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
    onboarding_meals_picker,
    onboarding_pregnancy_picker,
    onboarding_sex_picker,
    onboarding_skip,
)
from src.logging_setup import get_logger

router = Router(name="onboarding")
log = get_logger("handlers.onboarding")

STEP_KEY = "onb_step"
QUEUE_KEY = "onb_queue"
STEPS: tuple[str, ...] = (
    "focus",
    "meals",
    "age",
    "height",
    "weight",
    "sex",
    "conditions",
    "goal",
)
#: шаги сахарного трека — не в статическом списке, их вставляет `after_focus`
SUGAR_STEPS: tuple[str, ...] = ("dia", "dia_meds", "sugar_method", "sugar_pitch")

_MENU_TEXTS = {text for row in MENU_ROWS for text in row} | {"◀️ Меню", "❌ Отменить"}

MEALS_PROMPT = (
    "🍽 Сколько раз в день вы обычно едите? Разобью дневной калораж поровну "
    "между приёмами пищи — так проще держать порции под контролем. Не знаете "
    "точно — посчитаю сам по вашей статистике, как только наберётся история."
)

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
    "Начнём с главного — зачем вы здесь, а потом несколько чисел для расчётов. "
    "Любой вопрос можно пропустить и заполнить позже через /body; состав тела "
    "(биоимпеданс) добавляется в любой момент — просто пришлите фото весов или "
    "напишите «вес 82,4 жир 24%»."
)

FINISH = (
    "Профиль готов, спасибо! Всё это можно поменять в /body в любой момент."
)

FINISH_NO_WEIGHT_GOAL = (
    "Профиль готов, спасибо! Цель по весу не спрашиваю — вы пришли не за этим; "
    "если понадобится, она в /body."
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
        text = FINISH_NO_WEIGHT_GOAL if data.get("onb_no_weight_goal") else FINISH
        await _finish(message, state, text=text)
        return
    step = queue.pop(0)
    await state.update_data({QUEUE_KEY: queue, STEP_KEY: step})
    await state.set_state(OnboardingFlow.asking)
    if step == "focus":
        from src.handlers.goals import ask_focus

        await ask_focus(message, state, skippable=True)
        return
    if step == "meals":
        await message.answer(MEALS_PROMPT, reply_markup=onboarding_meals_picker())
        return
    if step == "sex":
        await message.answer(
            "⚧ Ваш пол — нужен для формулы обмена веществ.",
            reply_markup=onboarding_sex_picker(),
        )
        return
    if step == "pregnant":
        await message.answer("🤰 Вы сейчас беременны?", reply_markup=onboarding_pregnancy_picker())
        return
    if step in SUGAR_STEPS:
        from src.handlers.sugar import ask_step

        await ask_step(message, state, step)
        return
    await message.answer(PROMPTS[step], reply_markup=onboarding_skip())


async def _finish(message: Message, state: FSMContext, *, text: str = FINISH) -> None:
    await state.clear()
    await message.answer(text, reply_markup=await menu_of(message.chat.id))


async def after_focus(message: Message, state: FSMContext, selected: list[str]) -> None:
    """Цели названы — дальше анкета идёт под них.

    Целевой вес спрашиваем только у того, кто пришёл менять вес: человеку с
    целью «держать сахар в норме» вопрос «какой вес хотите видеть на весах»
    не нужен и звучит навязчиво (`spec/onboarding.md` § Цели). Ему вместо
    этого достаются вопросы сахарного трека.
    """
    from src import goals, sugar

    data = await state.get_data()
    queue = list(data.get(QUEUE_KEY) or [])
    dropped = False
    if not goals.wants_weight_goal(selected) and "goal" in queue:
        queue.remove("goal")
        dropped = True
    # Пришёл за сахаром — спрашиваем про сахар, и сразу: эти вопросы задают
    # рамку всему остальному разговору (`spec/onboarding.md` § Сахарный трек).
    if sugar.wants_sugar_track(selected):
        queue = [step for step in SUGAR_STEPS if step not in queue] + queue
    await state.update_data({QUEUE_KEY: queue, "onb_no_weight_goal": dropped})
    await _ask_next(message, state)


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


@router.callback_query(F.data.startswith("onb:meals:"), OnboardingFlow.asking)
async def on_meals(callback: CallbackQuery, state: FSMContext) -> None:
    count = callback.data.split(":")[2]
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        user.meals_per_day = int(count)
    await callback.answer("Записал")
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
    if step == "focus":
        # Вместо кнопки человек написал цель словами — это тоже ответ.
        from src.handlers.goals import save_note

        await save_note(message, state, text)
    elif step == "dia_meds":
        from src.handlers.sugar import save_meds

        await save_meds(message, state, text)
    elif step == "weight":
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
        elif step == "meals":
            from src.analytics.plate import MAX_MEALS_PER_DAY, MIN_MEALS_PER_DAY

            if not MIN_MEALS_PER_DAY <= value <= MAX_MEALS_PER_DAY:
                await message.answer(
                    f"Приёмов пищи в день — от {MIN_MEALS_PER_DAY} до {MAX_MEALS_PER_DAY}.",
                    reply_markup=onboarding_skip(),
                )
                return
            user.meals_per_day = int(value)
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


#: публичное имя для шагов, которые живут в других модулях (`handlers/sugar.py`)
advance = _ask_next

__all__ = ["SUGAR_STEPS", "advance", "after_focus", "router", "start"]
