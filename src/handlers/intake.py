"""Everything the user sends first lands here: photos, text, voice, documents.

Routing (`spec/ingest.md` § Router):

1. an explicit mode («🛒 Проверить продукт», /eat, /sugar) wins;
2. otherwise a photo is classified by the model (food / CGM screen / label /
   lab form) and dispatched;
3. free text is first run through the deterministic parser — «сахар 8» never
   costs a model call — and only the leftover goes to the meal parser;
4. voice is transcribed, then re-enters step 3.

A draft is shown for confirmation; nothing is written until the user taps.
"""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.analytics import workout as workout_math
from src.config import load_settings
from src.db import repo
from src.handlers import views
from src.handlers.deps import (
    album_buffer,
    download_photo,
    local_now,
    session_scope,
    to_utc,
)
from src.handlers.states import (
    BodyFlow,
    GlucoseFlow,
    LabFlow,
    MealFlow,
    MedicationFlow,
    ProductFlow,
    WellbeingFlow,
    WorkoutFlow,
)
from src.ingest.correction import apply_meal_correction, split_macros
from src.ingest.text_parse import parse_text
from src.ingest.units import to_mmol
from src.keyboards import cancel_only, main_menu, photo_kind
from src.llm import ImagePart, get_client, model_selection
from src.logging_setup import get_logger
from src.reporting import (
    describe_food_hint,
    describe_food_retry,
    food_examples,
    glucose_examples,
    glucose_hint,
    glucose_prompt,
    quoted,
)
from src.vision import recognize
from src.vision.schemas import GlucoseDraft

router = Router(name="intake")
log = get_logger("handlers.intake")

MODE_KEY = "pending_mode"
PHOTOS_KEY = "pending_photos"


# ------------------------------------------------------------------ modes

@router.message(F.text == "🛒 Проверить продукт")
@router.message(Command("check"))
async def start_check_mode(message: Message, state: FSMContext) -> None:
    await state.update_data({MODE_KEY: "check_product"})
    await message.answer(
        "🛒 Режим проверки перед покупкой.\n"
        "Пришлите фото упаковки — лучше две: лицевую сторону и состав.\n"
        "Ничего не запишу как съеденное, просто скажу, что говорят ваши данные.",
        reply_markup=cancel_only(),
    )


@router.message(F.text == "🍽 Записать еду")
@router.message(Command("eat"))
async def start_meal_mode(message: Message, state: FSMContext) -> None:
    await state.update_data({MODE_KEY: "meal"})
    await message.answer(
        "🍽 Пришлите фото еды или опишите текстом / голосом.", reply_markup=cancel_only()
    )


@router.message(F.text == "🩸 Записать сахар")
@router.message(Command("sugar"))
async def start_glucose_mode(message: Message, state: FSMContext) -> None:
    await state.update_data({MODE_KEY: "glucose"})
    await message.answer(
        glucose_prompt(glucose_examples(message.chat.id)),
        reply_markup=cancel_only(),
    )


@router.message(F.text == "⚖️ Записать вес")
async def start_weight_mode(message: Message, state: FSMContext) -> None:
    from src.handlers.body import cmd_weight

    await cmd_weight(message, state)


# ------------------------------------------------------------------ photos

@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        # Albums arrive as separate updates; buffer and handle them once.
        album_buffer.add(
            message.media_group_id,
            message,
            lambda _gid, messages: _handle_photos(messages, state, bot),
        )
        return
    await _handle_photos([message], state, bot)


async def _handle_photos(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    message = messages[0]
    images: list[ImagePart] = []
    file_ids: list[str] = []
    for item in messages:
        if not item.photo:
            continue
        photo = item.photo[-1]
        try:
            images.append(await download_photo(bot, photo.file_id))
            file_ids.append(photo.file_id)
        except Exception:
            log.exception("photo download failed")
    if not images:
        await message.answer("Не удалось скачать фото, попробуйте ещё раз.")
        return

    data = await state.get_data()
    mode = data.get(MODE_KEY)
    caption = " ".join(filter(None, (m.caption for m in messages))).strip()

    if await state.get_state() == ProductFlow.awaiting_second_side.state:
        # «➕ Вторая сторона»: re-send the first photo together with the new one
        # so the model sees front and back in a single turn and merges them.
        previous = data.get(views.FILES_KEY) or []
        earlier: list[ImagePart] = []
        for file_id in previous:
            try:
                earlier.append(await download_photo(bot, file_id))
            except Exception:
                log.warning("could not re-download the first side %s", file_id)
        await _process_label(
            message,
            state,
            earlier + images,
            [*previous, *file_ids],
            mode=data.get(views.MODE_KEY_VIEW) or "eaten",
        )
        return

    if mode == "check_product":
        await _process_label(message, state, images, file_ids, mode="check")
        return
    if mode == "glucose":
        await _process_glucose(message, state, images, file_ids)
        return
    if mode == "meal":
        await _process_food(message, state, images, file_ids, hint=caption)
        return
    if mode == "workout":
        from src.handlers import workout

        await workout.start_from_photo(message, state, images, file_ids, hint=caption)
        return

    status = await message.answer("🔎 Смотрю, что на фото…")
    kind, confidence = await recognize.classify_photo(images)
    await status.delete()
    await _dispatch(message, state, images, file_ids, kind=kind, hint=caption)


async def _dispatch(
    message: Message,
    state: FSMContext,
    images: list[ImagePart],
    file_ids: list[str],
    *,
    kind: str,
    hint: str = "",
) -> None:
    if kind == "food":
        await _process_food(message, state, images, file_ids, hint=hint)
    elif kind == "glucose_screen":
        await _process_glucose(message, state, images, file_ids)
    elif kind == "food_label":
        await _process_label(message, state, images, file_ids, mode="eaten")
    elif kind == "lab_report":
        await _process_labs(message, state, images, file_ids)
    elif kind == "medication":
        await _process_medication(message, state, images, file_ids)
    elif kind == "body_scale":
        await _process_body(message, state, images)
    elif kind == "workout":
        from src.handlers import workout

        await workout.start_from_photo(message, state, images, file_ids, hint=hint)
    else:
        await state.update_data({PHOTOS_KEY: file_ids})
        await message.answer(
            "Не понял, что на фото. Подскажите, что это?", reply_markup=photo_kind()
        )


@router.callback_query(F.data.startswith("kind:"))
async def on_kind_chosen(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    kind = callback.data.split(":", 1)[1]
    await callback.answer()
    if kind == "drop":
        await state.update_data({PHOTOS_KEY: None})
        await callback.message.edit_text("Отменено.")
        return
    data = await state.get_data()
    file_ids = data.get(PHOTOS_KEY) or []
    if not file_ids:
        await callback.message.edit_text("Фото уже неактуально — пришлите заново.")
        return
    images = [await download_photo(bot, file_id) for file_id in file_ids]
    await callback.message.edit_text("Принято, обрабатываю…")
    await _dispatch(callback.message, state, images, file_ids, kind=kind)


@router.callback_query(F.data == "photo:reroute")
async def on_reroute(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.answer("Что это было?", reply_markup=photo_kind())


# ------------------------------------------------------------------ processors

async def _process_food(
    message: Message,
    state: FSMContext,
    images: list[ImagePart],
    file_ids: list[str],
    *,
    hint: str = "",
) -> None:
    try:
        draft = await recognize.recognize_meal_photo(images, hint=hint)
    except recognize.RecognitionError as exc:
        await message.answer(
            f"Не получилось распознать еду: {exc}\n"
            + describe_food_hint(
                food_examples(message.chat.id, await views.personal_examples(message.chat.id))
            )
        )
        return
    await views.show_meal_draft(message, state, draft, file_ids=file_ids)


async def _process_glucose(
    message: Message, state: FSMContext, images: list[ImagePart], file_ids: list[str]
) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        unit = user.glucose_unit
    try:
        drafts, device = await recognize.recognize_glucose_screenshot(images, now=now)
    except recognize.RecognitionError as exc:
        await message.answer(
            f"Не смог прочитать показания: {exc}\n{glucose_hint(glucose_examples(message.chat.id))}"
        )
        return
    await views.show_glucose_draft(message, state, drafts, unit=unit, file_ids=file_ids)


async def _process_label(
    message: Message,
    state: FSMContext,
    images: list[ImagePart],
    file_ids: list[str],
    *,
    mode: str,
) -> None:
    try:
        draft = await recognize.recognize_label(images)
    except recognize.RecognitionError as exc:
        await message.answer(f"Этикетка не читается: {exc}\nПопробуйте снять состав крупнее.")
        return
    await views.show_product_draft(message, state, draft, mode=mode, file_ids=file_ids)


async def _process_medication(
    message: Message, state: FSMContext, images: list[ImagePart], file_ids: list[str]
) -> None:
    try:
        draft = await recognize.recognize_medication(images)
    except recognize.RecognitionError as exc:
        await message.answer(
            f"Не разобрал упаковку: {exc}\n"
            "Можно написать словами — «выпил метформин 850»."
        )
        return
    await views.show_medication_draft(message, state, draft, file_ids=file_ids)


async def _process_body(
    message: Message, state: FSMContext, images: list[ImagePart]
) -> None:
    from src.handlers.body import show_measurement_draft

    try:
        draft = await recognize.recognize_body_photo(images)
    except recognize.RecognitionError as exc:
        await message.answer(
            f"Не разобрал показания весов: {exc}\nНапишите вес числом — «82,4»."
        )
        return
    await show_measurement_draft(message, state, draft)


async def _process_labs(
    message: Message,
    state: FSMContext,
    images: list[ImagePart] | None,
    file_ids: list[str],
    *,
    text: str | None = None,
) -> None:
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    try:
        draft = await recognize.recognize_labs(images=images, text=text, now=now)
    except recognize.RecognitionError as exc:
        await message.answer(f"Не разобрал анализы: {exc}")
        return
    await views.show_lab_draft(message, state, draft, file_ids=file_ids)


# ------------------------------------------------------------------ documents

@router.message(F.document)
async def on_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """PDF/photo documents: lab reports mostly."""
    document = message.document
    mime = (document.mime_type or "").lower()
    if mime.startswith("image/"):
        image = await download_photo(bot, document.file_id)
        await _process_labs(message, state, [image], [document.file_id])
        return
    if "pdf" not in mime and not (document.file_name or "").lower().endswith(".pdf"):
        await message.answer("Пока понимаю только PDF и изображения.")
        return
    buffer = await bot.download(document.file_id)
    data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
    from src.ingest.pdf import pdf_to_text

    text = pdf_to_text(data)
    if not text:
        await message.answer(
            "В PDF нет текстового слоя. Пришлите, пожалуйста, фото страницы — прочитаю с картинки."
        )
        return
    await _process_labs(message, state, None, [document.file_id], text=text)


# ------------------------------------------------------------------ voice

@router.message(F.voice | F.audio)
async def on_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    voice = message.voice or message.audio
    buffer = await bot.download(voice.file_id)
    data = buffer.read() if hasattr(buffer, "read") else bytes(buffer)
    text = await _transcribe(message, data, mime=voice.mime_type or "audio/ogg")
    if text is None:
        return
    await message.answer(f"🎤 Расслышал: «{text}»")
    if await _route_voice(message, state, text):
        return
    await handle_text(message, state, text_override=text)


async def _transcribe(message: Message, data: bytes, *, mime: str) -> str | None:
    """Voice → text: SpeechKit when configured, the LLM endpoint otherwise.

    Returns `None` when nothing usable came back — the user has already been
    told why (`spec/ingest.md` § Голос).
    """
    settings = load_settings()
    if settings.speechkit_available:
        from src.ingest import speechkit

        try:
            result = await speechkit.recognize_voice(
                data,
                api_key=settings.yandex_speechkit_api_key,
                folder_id=settings.yandex_folder_id,
                lang=settings.speechkit_lang,
            )
        except speechkit.UnsupportedFormat:
            # Telegram `voice` is always OGG/Opus; an `audio` attachment is not.
            log.info("speechkit: unsupported format %s, falling back", mime)
            return await _transcribe_via_llm(message, data, mime=mime)
        except speechkit.SpeechKitQuotaExceeded:
            await message.answer(
                "Распознавание голоса сейчас перегружено. Попробуйте через минуту "
                "или напишите текстом."
            )
            return None
        except speechkit.SpeechKitAuthError:
            log.error("speechkit key rejected — check YANDEX_SPEECHKIT_API_KEY / YANDEX_FOLDER_ID")
            return await _transcribe_via_llm(message, data, mime=mime)
        except Exception as exc:
            log.warning("speechkit failed: %s", exc)
            return await _transcribe_via_llm(message, data, mime=mime)
        text = result.text.strip()
        if not text:
            await message.answer("Ничего не расслышал, попробуйте ещё раз.")
            return None
        return text
    return await _transcribe_via_llm(message, data, mime=mime)


async def _transcribe_via_llm(message: Message, data: bytes, *, mime: str) -> str | None:
    try:
        text = await get_client().transcribe(
            data, mime=mime, model=model_selection.current("stt")
        )
    except Exception as exc:
        log.warning("stt failed: %s", exc)
        await message.answer(
            "Распознавание голоса сейчас недоступно. Напишите, пожалуйста, текстом."
        )
        return None
    text = (text or "").strip()
    if not text:
        await message.answer("Ничего не расслышал, попробуйте ещё раз.")
        return None
    return text


async def _route_voice(message: Message, state: FSMContext, text: str) -> bool:
    """A correction can be spoken: hand the transcript to the waiting flow.

    Without this a voice note in `MealFlow.editing` would fall through to the
    catch-all text handler and start a *new* meal instead of correcting the
    open card (`spec/ingest.md` § Корректировки).
    """
    current = await state.get_state()
    if current is None:
        return False
    if current == WellbeingFlow.free_text.state:
        from src.handlers.wellbeing import handle_free_text

        await handle_free_text(message, state, text)
        return True

    from src.handlers import body, confirm, meds, workout

    routes = {
        BodyFlow.awaiting.state: body.on_value,
        WorkoutFlow.editing.state: workout.workout_apply_edit,
        WorkoutFlow.retiming.state: workout.workout_apply_time,
        WorkoutFlow.awaiting_hr.state: workout.workout_apply_hr,
        MealFlow.editing.state: confirm.meal_apply_edit,
        MealFlow.editing_macros.state: confirm.meal_apply_macros,
        MealFlow.retiming.state: confirm.meal_apply_time,
        GlucoseFlow.editing.state: confirm.glucose_apply_edit,
        LabFlow.editing.state: confirm.lab_apply_edit,
        ProductFlow.editing.state: confirm.product_apply_edit,
        ProductFlow.editing_macros.state: confirm.product_apply_macros,
        MedicationFlow.editing.state: meds.med_apply_edit,
        MedicationFlow.retiming.state: meds.med_apply_time,
    }
    handler = routes.get(current)
    if handler is None:
        return False
    await handler(message, state, text_override=text)
    return True


# ------------------------------------------------------------------ text

@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext) -> None:
    await handle_text(message, state)


async def _handle_body_facts(
    message: Message, state: FSMContext, parsed, *, text: str, at
) -> bool:
    """Вес, рост, пол, биоимпеданс и целевой вес. `True` — сообщение исчерпано."""
    from src.handlers import body

    facts = parsed.body
    handled = False
    if parsed.weight_kg:
        await body.save_weight_entry(
            message, weight_kg=parsed.weight_kg, text=text, source="text", at=at
        )
        handled = True
    elif not facts.is_empty and (facts.height_cm or facts.age or facts.sex):
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, message.chat.id)
            await repo.upsert_body_profile(
                session,
                user,
                height_cm=facts.height_cm,
                sex=facts.sex,
                birth_year=(local_now(user).year - facts.age) if facts.age else None,
            )
        await message.answer("📋 Профиль обновил. Смотрите /body.", reply_markup=main_menu())
        handled = True
    if facts.target_weight_kg:
        await body.offer_goal(message, state, target_weight_kg=facts.target_weight_kg)
        handled = True
    return handled


async def handle_text(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Deterministic parse first, model only for what is left."""
    text = (text_override or message.text or "").strip()
    if not text:
        return
    data = await state.get_data()
    if data.get(MODE_KEY) == "workout" and parse_text(text).is_empty:
        # «🏃 Тренировка» → следующий текст про тренировку. Но если в нём
        # однозначный факт («сахар 8.2»), режим не мешает его записать.
        from src.handlers import workout

        await state.update_data({MODE_KEY: None})
        if await workout.start_from_text(message, state, text):
            return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        unit = user.glucose_unit
        parsed = parse_text(text, now=now)
        stamp_local = parsed.at or now
        saved: list[str] = []

        if parsed.glucose:
            drafts = [
                GlucoseDraft(
                    measured_at=to_utc(stamp_local, user),
                    value_mmol=to_mmol(value, raw_unit),
                    unit_input=raw_unit,
                )
                for value, raw_unit in parsed.glucose
            ]
            rows = await repo.save_glucose(session, user, drafts, source="text")
            if rows:
                from src.ingest.units import format_value

                saved.append(
                    "🩸 сахар "
                    + ", ".join(format_value(r.value_mmol, unit) for r in rows)
                    + f" в {stamp_local:%H:%M}"
                )
        for name, dose in parsed.medications:
            await repo.save_medication(
                session, user, taken_at=to_utc(stamp_local, user), name=name, dose_text=dose
            )
            saved.append(f"💊 {name}{' ' + dose if dose else ''}")
        if parsed.wellbeing is not None:
            await repo.save_checkin(
                session,
                user,
                at=to_utc(stamp_local, user),
                score=parsed.wellbeing,
                source="text",
                note=parsed.leftover or None,
            )
            saved.append(f"🙂 самочувствие {parsed.wellbeing}/5")

    if saved:
        await message.answer("Записал: " + "; ".join(saved), reply_markup=main_menu())

    # Тело идёт своим путём: вес пишется вместе с биоимпедансом, а профиль и
    # цель меняют дневной коридор калорий (`spec/body.md`).
    if await _handle_body_facts(message, state, parsed, text=text, at=stamp_local):
        return

    leftover = parsed.leftover
    if not leftover or len(leftover) < 3:
        if not saved:
            await message.answer(
                "Не понял. Пришлите фото еды, напишите "
                f"{quoted(glucose_examples(message.chat.id)[:1])} или нажмите кнопку меню.",
                reply_markup=main_menu(),
            )
        return

    if workout_math.looks_like_workout(leftover):
        from src.handlers import workout

        if await workout.start_from_text(message, state, leftover):
            return

    # БЖУ, названные прямо во вводе («овсянка 200 г б 12 ж 6 у 40»), не уходят
    # в модель: она бы вернула свою оценку, а числа пользователя сильнее
    # (`spec/ingest.md` § Нутриенты).
    food_text, macro_instruction = split_macros(leftover)
    if not food_text or len(food_text) < 3:
        if not saved:
            await message.answer(
                describe_food_retry(
                    food_examples(message.chat.id, await views.personal_examples(message.chat.id))
                ),
                reply_markup=main_menu(),
            )
        return

    # The personal dictionary answers before the model does: «то же, что вчера»
    # costs one tap and no API call (`spec/dictionary.md`). Named БЖУ mean the
    # user is describing this plate, not reusing a saved one.
    if not macro_instruction:
        from src.handlers.dictionary import offer_suggestions

        if await offer_suggestions(message, state, food_text):
            return

    # Anything left over is treated as a meal description.
    try:
        draft = await recognize.parse_meal_text(food_text)
    except recognize.RecognitionError:
        if not saved:
            await message.answer(
                describe_food_retry(
                    food_examples(message.chat.id, await views.personal_examples(message.chat.id))
                ),
                reply_markup=main_menu(),
            )
        return
    if macro_instruction:
        draft = apply_meal_correction(draft, macro_instruction).draft
        await views.remember_typed_macros(message, draft)
    await views.show_meal_draft(message, state, draft, eaten_at_local=parsed.at)


__all__ = ["MODE_KEY", "PHOTOS_KEY", "handle_text", "router"]
