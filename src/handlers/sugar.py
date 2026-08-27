"""The sugar track: three questions and the offer to send readings.

Reached only from the questionnaire, and only when «Держать сахар в норме» is
among the goals (`spec/onboarding.md` § Сахарный трек). The questions add
context the user typed about themselves — a type of diabetes, the medication
they take, what they measure with. None of it is interpreted: the bot records,
it does not diagnose, dose or cancel a treatment
(`.specify/memory/constitution.md`, принцип I).

The last step explains what the person is signing up for — photographing every
meal, even a cup of coffee — and, if they measure their sugar at all, turns on
the per-meal offer to send a reading. Someone who measures nothing is never
asked for a number they do not have.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import sugar
from src.db import repo
from src.handlers.deps import session_scope
from src.keyboards import diabetes_picker, glucose_method_picker
from src.logging_setup import get_logger

router = Router(name="sugar")
log = get_logger("handlers.sugar")

#: шаги анкеты, за которые отвечает этот модуль (в порядке очереди)
STEPS: tuple[str, ...] = ("dia", "dia_meds", "sugar_method", "sugar_pitch")
#: отмеченные, но ещё не сохранённые способы измерения
SELECTED_KEY = "sugar_methods_sel"

DIA_PROMPT = (
    "🩸 <b>Есть ли у вас диабет?</b>\n"
    "Отвечать не обязательно. Это нужно только для того, чтобы я аккуратнее "
    "подбирал слова: диагнозов я не ставлю и лечение не назначаю."
)

MEDS_PROMPT = (
    "💊 <b>Какие лекарства вы принимаете?</b> Перечислите словами — запишу как "
    "есть. Если никаких, так и напишите: «нет».\n\n"
    "И сразу важное: лекарства стоит отмечать в боте наравне с приёмами пищи — "
    "командой /meds или фото упаковки. Сахар после одной и той же тарелки "
    "выглядит по-разному в зависимости от того, что и когда было принято; без "
    "этих отметок половина картины теряется. Дозы я не считаю и назначения не "
    "меняю — только записываю рядом с едой."
)

METHOD_PROMPT = (
    "📟 <b>Чем вы меряете сахар?</b> Отметьте всё, чем пользуетесь, — от этого "
    "зависит, о чём я буду просить дальше."
)

PITCH = (
    "🎯 <b>Как это работает</b>\n\n"
    "Я — инструмент, которым можно собрать вашу личную диету: такую, на которой "
    "ваш сахар держится в целевых значениях. Это не лечение и не замена врачу — "
    "это подбор еды по вашим собственным замерам.\n\n"
    "Чтобы это заработало, нужна педантичность: фотографируйте <b>каждый</b> "
    "приём пищи — даже чашку кофе, даже одну печенюшку. Я смотрю, как после "
    "каждого меняются ваши показатели, и со временем говорю, какие продукты и "
    "их составляющие подходят именно вам, а какие — нет.\n\n"
    "Пропуски данные не портят — они их обесценивают: по половине дневника "
    "связь не читается."
)

PITCH_TRACKING = (
    "🩸 Дальше после каждой записи еды я буду предлагать прислать замер: число "
    "или фото с глюкометра, скриншот с датчика, или просто «сахар 7,2». "
    "Интереснее всего через 1–2 часа после еды.\n"
    "Надоест — <code>/set sugar off</code>."
)

PITCH_NO_TRACKING = (
    "📊 Замеры вы не снимаете — значит, просить их я не буду. Дневник еды всё "
    "равно работает: калории, состав и режим питания. Появится глюкометр или "
    "датчик — включите замеры в <code>/set sugar on</code>."
)

MEDS_SAVED = "Записал. Отмечайте приёмы лекарств в /meds — они идут в ту же картину, что и еда."


async def ask_step(message: Message, state: FSMContext, step: str) -> None:
    """Задать один шаг сахарного трека (вызывается из `onboarding._ask_next`)."""
    if step == "dia":
        await message.answer(DIA_PROMPT, reply_markup=diabetes_picker())
        return
    if step == "dia_meds":
        from src.keyboards import onboarding_skip

        await message.answer(MEDS_PROMPT, reply_markup=onboarding_skip())
        return
    if step == "sugar_method":
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, message.chat.id)
            profile = await repo.get_body_profile(session, user)
            selected = list(sugar.decode_methods(profile.glucose_methods if profile else None))
        await state.update_data({SELECTED_KEY: selected})
        await message.answer(
            METHOD_PROMPT, reply_markup=glucose_method_picker(selected, skippable=True)
        )
        return
    await show_pitch(message, state)


async def show_pitch(message: Message, state: FSMContext) -> None:
    """Рассказ о том, ради чего всё это, и включение предложения замеров.

    Отдельный шаг очереди, а не хвост предыдущего вопроса: пропуск вопроса о
    способах не должен уносить с собой объяснение.
    """
    from src.handlers import onboarding

    tracking = False
    try:
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, message.chat.id)
            profile = await repo.get_body_profile(session, user)
            tracking = sugar.tracks_glucose(profile.glucose_methods if profile else None)
            user.glucose_prompt_enabled = tracking
    except Exception:  # noqa: BLE001 — объяснение важнее, чем флаг
        log.exception("sugar pitch state failed")
    await message.answer(PITCH + "\n\n" + (PITCH_TRACKING if tracking else PITCH_NO_TRACKING))
    await onboarding.advance(message, state)


async def save_meds(message: Message, state: FSMContext, text: str) -> None:
    """Свободный ответ про лекарства: сохраняем как написано, ничего не разбирая."""
    from src.handlers import onboarding

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.upsert_body_profile(session, user, diabetes_meds=sugar.normalize_meds(text))
    await message.answer(MEDS_SAVED)
    await onboarding.advance(message, state)


@router.callback_query(F.data.startswith("sg:dia:"))
async def on_diabetes(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers import onboarding

    key = (callback.data or "").split(":", 2)[2]
    if key not in sugar.DIABETES_BY_KEY:
        await callback.answer()
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        await repo.upsert_body_profile(session, user, diabetes=key)
    await callback.answer("Записал")
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
        await onboarding.advance(callback.message, state)


@router.callback_query(F.data.startswith("sg:m:"))
async def on_method(callback: CallbackQuery, state: FSMContext) -> None:
    """Отметить или снять способ. «Никакими» — исключающий вариант."""
    key = (callback.data or "").split(":", 2)[2]
    if key != sugar.NONE and key not in sugar.METHODS_BY_KEY:
        await callback.answer()
        return
    data = await state.get_data()
    selected = list(data.get(SELECTED_KEY) or [])
    if key in selected:
        selected.remove(key)
    elif key == sugar.NONE:
        selected = [sugar.NONE]
    else:
        selected = [item for item in selected if item != sugar.NONE] + [key]
    await state.update_data({SELECTED_KEY: selected})
    await callback.answer()
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=glucose_method_picker(selected, skippable=True)
        )


@router.callback_query(F.data == "sg:done")
async def on_done(callback: CallbackQuery, state: FSMContext) -> None:
    from src.handlers import onboarding

    data = await state.get_data()
    selected = list(data.get(SELECTED_KEY) or [])
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        # Пустая строка — «спросили, ничем не меряет»; NULL значил бы «не спрашивали».
        await repo.upsert_body_profile(
            session, user, glucose_methods=sugar.encode_methods(selected)
        )
    await callback.answer("Записал")
    if callback.message is not None:
        titles = sugar.method_titles(selected)
        await callback.message.edit_text(
            "📟 Измерения: " + (" · ".join(title.lower() for title in titles) if titles else "нет")
        )
        await onboarding.advance(callback.message, state)


@router.callback_query(F.data == "sg:log")
async def on_log_glucose(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «🩸 Записать сахар» под записью еды — тот же режим, что и в меню."""
    from src.handlers.intake import MODE_KEY
    from src.keyboards import cancel_only
    from src.reporting import glucose_examples, glucose_prompt

    await state.update_data({MODE_KEY: "glucose"})
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            glucose_prompt(glucose_examples(callback.from_user.id)), reply_markup=cancel_only()
        )


__all__ = ["STEPS", "ask_step", "router", "save_meds", "show_pitch"]
