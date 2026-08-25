"""Тренировки: ввод текстом, голосом и фото, доп. вопросы, оценка энергозатрат.

Обработчик только маршрутизирует: MET и калории считает
`src/analytics/workout.py`, тексты живут в `src/reporting.py`, запись — в
`src/db/repo.py`. См. `spec/workout.md`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.analytics import body as body_math
from src.analytics import workout as workout_math
from src.db import repo
from src.db.models import User
from src.handlers.deps import local_now, session_scope, to_local, to_utc
from src.handlers.features import menu_of
from src.handlers.states import WorkoutFlow
from src.keyboards import (
    cancel_only,
    confirm_workout,
    workout_duration,
    workout_intensity,
    workout_sweat,
)
from src.logging_setup import get_logger
from src.reporting import format_workout_draft, format_workouts
from src.vision import recognize
from src.vision.schemas import WorkoutDraft, workout_from_dict, workout_to_dict

router = Router(name="workout")
log = get_logger("handlers.workout")

DRAFT_KEY = "draft"
FILES_KEY = "draft_files"
PENDING_KEY = "wo_pending"
STARTED_KEY = "started_at"

QUESTION_TEXTS = {
    workout_math.QUESTION_DURATION: "⏱ Сколько это длилось?",
    workout_math.QUESTION_INTENSITY: (
        "🔥 Насколько тяжело было? Ориентир — дыхание и способность говорить."
    ),
    workout_math.QUESTION_SWEAT: "💧 Вспотели?",
}
QUESTION_KEYBOARDS = {
    workout_math.QUESTION_DURATION: workout_duration,
    workout_math.QUESTION_INTENSITY: workout_intensity,
    workout_math.QUESTION_SWEAT: workout_sweat,
}


# ------------------------------------------------------------------ вход

@router.message(Command("workout"))
@router.message(F.text == "🏃 Тренировка")
async def cmd_workout(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data({"pending_mode": "workout"})
    await message.answer(
        "🏃 Расскажите о тренировке — текстом или голосом: «бегал 40 минут», "
        "«час на велосипеде», «10 000 шагов».\n"
        "Можно прислать фото трекера, часов или страницу бумажного дневника — "
        "прочитаю, в том числе от руки.\n"
        "Дальше уточню время, интенсивность и пару мелочей, чтобы посчитать "
        "примерные энергозатраты.",
        reply_markup=cancel_only(),
    )


@router.message(Command("workouts"))
async def cmd_workouts(message: Message) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        since = local_now(user) - timedelta(days=7)
        rows = [
            (
                to_local(row.started_at, user),
                row.title or workout_math.kind_label(row.kind),
                row.duration_min,
                row.kcal,
            )
            for row in await repo.load_workouts(session, user, since=since)
        ]
    await message.answer(format_workouts(rows), reply_markup=await menu_of(message.chat.id))


async def start_from_text(message: Message, state: FSMContext, text: str) -> bool:
    """Разобрать отчёт о тренировке. `False` — не вышло, пусть текст идёт дальше."""
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    try:
        draft = await recognize.parse_workout_text(text, now=now)
    except recognize.RecognitionError:
        return False
    await _after_recognition(message, state, draft, now=now)
    return True


async def start_from_photo(
    message: Message, state: FSMContext, images: list, file_ids: list[str], *, hint: str = ""
) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    try:
        draft = await recognize.recognize_workout_photo(images, now=now, hint=hint)
    except recognize.RecognitionError as exc:
        await message.answer(
            f"Не разобрал запись о тренировке: {exc}\n"
            "Можно словами — «силовая 50 минут»."
        )
        return
    await _after_recognition(message, state, draft, now=now, file_ids=file_ids)


async def _after_recognition(
    message: Message,
    state: FSMContext,
    draft: WorkoutDraft,
    *,
    now: datetime,
    file_ids: list[str] | None = None,
) -> None:
    started = draft.started_at or now
    await state.update_data({FILES_KEY: file_ids or [], STARTED_KEY: started.isoformat()})
    questions = workout_math.missing_questions(
        duration_min=draft.duration_min,
        intensity=draft.intensity,
        sweat=draft.sweat,
        avg_hr=draft.avg_hr,
        steps=draft.steps,
    )
    await _ask_next(message, state, draft, questions)


async def _ask_next(
    message: Message, state: FSMContext, draft: WorkoutDraft, questions: list[str]
) -> None:
    """Спросить следующее из очереди — или показать карточку, если очередь пуста."""
    await state.update_data({DRAFT_KEY: workout_to_dict(draft), PENDING_KEY: questions})
    if not questions:
        await show_workout_draft(message, state, draft)
        return
    question = questions[0]
    await state.set_state(WorkoutFlow.asking)
    await message.answer(
        QUESTION_TEXTS[question], reply_markup=QUESTION_KEYBOARDS[question]()
    )


async def show_workout_draft(
    message: Message,
    state: FSMContext,
    draft: WorkoutDraft,
    *,
    applied: list[str] | None = None,
) -> None:
    data = await state.get_data()
    raw_started = data.get(STARTED_KEY)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        started = datetime.fromisoformat(raw_started) if raw_started else local_now(user)
        estimate = await _estimate(session, user, draft)
    if estimate is not None:
        draft.met = estimate.met
        if draft.kcal_source == "estimated":
            draft.kcal = estimate.kcal
    await state.set_state(WorkoutFlow.confirming)
    await state.update_data({DRAFT_KEY: workout_to_dict(draft), PENDING_KEY: []})
    await message.answer(
        format_workout_draft(draft, estimate=estimate, started_at=started, applied=applied),
        reply_markup=confirm_workout(),
    )


async def _estimate(session, user: User, draft: WorkoutDraft) -> workout_math.Estimate | None:
    """Оценка по MET с весом и возрастом пользователя, если они известны."""
    profile = await repo.get_body_profile(session, user)
    last = await repo.last_weight(session, user)
    age = body_math.age_from(profile.birth_year) if profile else None
    intensity, _basis = workout_math.resolve_intensity(
        stated=draft.intensity, rpe=draft.rpe, sweat=draft.sweat, avg_hr=draft.avg_hr, age=age
    )
    minutes = draft.duration_min or workout_math.minutes_from_steps(draft.steps)
    return workout_math.kcal_estimate(
        kind=draft.kind,
        intensity=intensity,
        minutes=minutes,
        weight_kg=last.weight_kg if last else None,
        distance_m=draft.distance_m,
        avg_hr=draft.avg_hr,
        age=age,
    )


# ------------------------------------------------------------------ доп. вопросы

async def _answer_question(
    callback: CallbackQuery, state: FSMContext, field: str, value
) -> None:
    data = await state.get_data()
    draft = workout_from_dict(data.get(DRAFT_KEY) or {})
    setattr(draft, field, value)
    pending = [q for q in (data.get(PENDING_KEY) or []) if q != _QUESTION_BY_FIELD[field]]
    await callback.answer()
    await _ask_next(callback.message, state, draft, pending)


_QUESTION_BY_FIELD = {
    "duration_min": workout_math.QUESTION_DURATION,
    "intensity": workout_math.QUESTION_INTENSITY,
    "sweat": workout_math.QUESTION_SWEAT,
}


@router.callback_query(F.data.startswith("wo:dur:"), WorkoutFlow.asking)
async def on_duration(callback: CallbackQuery, state: FSMContext) -> None:
    raw = callback.data.split(":")[2]
    if raw == "other":
        await state.set_state(WorkoutFlow.editing)
        await callback.answer()
        await callback.message.answer(
            "Напишите длительность — «50 минут», «1.5 часа».", reply_markup=cancel_only()
        )
        return
    await _answer_question(callback, state, "duration_min", float(raw))


@router.callback_query(F.data.startswith("wo:int:"), WorkoutFlow.asking)
async def on_intensity(callback: CallbackQuery, state: FSMContext) -> None:
    await _answer_question(callback, state, "intensity", callback.data.split(":")[2])


@router.callback_query(F.data.startswith("wo:sweat:"), WorkoutFlow.asking)
async def on_sweat(callback: CallbackQuery, state: FSMContext) -> None:
    await _answer_question(callback, state, "sweat", callback.data.split(":")[2])


# ------------------------------------------------------------------ карточка

@router.callback_query(F.data == "wo:ok", WorkoutFlow.confirming)
async def workout_ok(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = workout_from_dict(data.get(DRAFT_KEY) or {})
    raw_started = data.get(STARTED_KEY)
    file_ids = data.get(FILES_KEY) or []
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        started = datetime.fromisoformat(raw_started) if raw_started else local_now(user)
        media_id = None
        if file_ids:
            media = await repo.save_media(session, user, kind="workout", tg_file_id=file_ids[0])
            media_id = media.id
        estimate = await _estimate(session, user, draft)
        if estimate is not None and draft.kcal_source == "estimated":
            draft.kcal, draft.met = estimate.kcal, estimate.met
        ended = (
            started + timedelta(minutes=draft.duration_min) if draft.duration_min else None
        )
        await repo.save_workout(
            session,
            user,
            draft,
            started_at=to_utc(started, user),
            ended_at=to_utc(ended, user) if ended else None,
            media_id=media_id,
        )
        from src.handlers.body import day_progress_text

        progress = await day_progress_text(session, user, now=local_now(user))
    await state.clear()
    await callback.answer("Записано")
    title = draft.title or workout_math.kind_label(draft.kind)
    energy = f" · ≈ {draft.kcal:.0f} ккал" if draft.kcal else ""
    text = f"✅ Записал: <b>{title}</b> в {started:%H:%M}{energy}."
    if progress:
        text += "\n\n" + progress
    await callback.message.edit_text(text)


@router.callback_query(F.data == "wo:edit", WorkoutFlow.confirming)
async def workout_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkoutFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Что поправить? Напишите или наговорите — «было 50 минут», «7 км», "
        "«пульс 140».",
        reply_markup=cancel_only(),
    )


@router.callback_query(F.data == "wo:hr", WorkoutFlow.confirming)
async def workout_hr(callback: CallbackQuery, state: FSMContext) -> None:
    """Пульс сильнее любых слов об интенсивности, поэтому его спрашиваем отдельно."""
    await state.set_state(WorkoutFlow.awaiting_hr)
    await callback.answer()
    await callback.message.answer(
        "❤️ Средний пульс за тренировку — числом.", reply_markup=cancel_only()
    )


@router.callback_query(F.data == "wo:time", WorkoutFlow.confirming)
async def workout_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkoutFlow.retiming)
    await callback.answer()
    await callback.message.answer(
        "Когда это было? Напишите время — «в 19:30» или «вчера в 8:00».",
        reply_markup=cancel_only(),
    )


@router.message(WorkoutFlow.awaiting_hr)
async def workout_apply_hr(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    text = (text_override or message.text or "").strip()
    import re

    match = re.search(r"(\d{2,3})", text)
    if not match or not 30 <= int(match.group(1)) <= 230:
        await message.answer("Нужен пульс числом, от 30 до 230.")
        return
    data = await state.get_data()
    draft = workout_from_dict(data.get(DRAFT_KEY) or {})
    draft.avg_hr = float(match.group(1))
    await show_workout_draft(message, state, draft, applied=[f"пульс {draft.avg_hr:.0f}"])


@router.message(WorkoutFlow.retiming)
async def workout_apply_time(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    from src.ingest.text_parse import parse_text

    text = (text_override or message.text or "").strip()
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    parsed = parse_text(text, now=now)
    if parsed.at is None:
        await message.answer("Не понял время. Напишите «в 19:30» или «вчера в 8:00».")
        return
    data = await state.get_data()
    draft = workout_from_dict(data.get(DRAFT_KEY) or {})
    await state.update_data({STARTED_KEY: parsed.at.isoformat()})
    await show_workout_draft(message, state, draft, applied=[f"время {parsed.at:%d.%m %H:%M}"])


@router.message(WorkoutFlow.editing)
async def workout_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Правка сливается с карточкой: названное заменяет, остальное остаётся."""
    text = (text_override or message.text or "").strip()
    if not text:
        await message.answer("Скажите или напишите, что поправить.")
        return
    data = await state.get_data()
    draft = workout_from_dict(data.get(DRAFT_KEY) or {})
    pending = list(data.get(PENDING_KEY) or [])
    applied: list[str] = []

    minutes = workout_math.parse_duration(text)
    if minutes:
        draft.duration_min = minutes
        applied.append(f"длительность {minutes:.0f} мин")
        pending = [q for q in pending if q != workout_math.QUESTION_DURATION]

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    try:
        fresh = await recognize.parse_workout_text(text, now=now)
    except recognize.RecognitionError:
        fresh = None
    if fresh is not None:
        for field in ("distance_m", "steps", "avg_hr", "rpe", "sweat", "intensity"):
            value = getattr(fresh, field)
            if value is not None:
                setattr(draft, field, value)
                applied.append(f"{field}: {value}")
        if fresh.duration_min and not minutes:
            draft.duration_min = fresh.duration_min
            applied.append(f"длительность {fresh.duration_min:.0f} мин")
            pending = [q for q in pending if q != workout_math.QUESTION_DURATION]
        if fresh.kind != "other":
            draft.kind = fresh.kind
            draft.title = fresh.title or draft.title
    if not applied:
        await message.answer(
            "Не понял правку. Скажите проще — «50 минут», «7 км», «пульс 140»."
        )
        return
    if pending:
        await _ask_next(message, state, draft, pending)
        return
    await show_workout_draft(message, state, draft, applied=applied)


@router.callback_query(F.data == "wo:drop")
async def workout_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Отменено. Ничего не записал.")


__all__ = ["router", "show_workout_draft", "start_from_photo", "start_from_text"]
