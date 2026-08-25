"""Confirmation callbacks: the moment a draft becomes a row.

Every «✅» writes; every «✏️ Исправить» records a `corrections` row alongside the
new value, because the user's corrections are training data for nothing less
than their own statistics.
"""

from __future__ import annotations

import re
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import local_now, session_scope, to_utc
from src.handlers.states import GlucoseFlow, LabFlow, MealFlow, ProductFlow
from src.handlers.views import DRAFT_KEY, EATEN_AT_KEY, FILES_KEY
from src.ingest.correction import apply_meal_correction
from src.ingest.nutrition import Remembered
from src.ingest.units import MGDL, MMOL, format_value
from src.keyboards import cancel_only
from src.logging_setup import get_logger
from src.reporting import format_remembered_label
from src.vision import recognize
from src.vision.schemas import (
    ItemDraft,
    MealDraft,
    glucose_from_dicts,
    glucose_to_dicts,
    lab_from_dict,
    meal_from_dict,
    meal_to_dict,
    product_from_dict,
)

router = Router(name="confirm")
log = get_logger("handlers.confirm")

MACROS_PROMPT = (
    "✏️ <b>БЖУ</b>\n"
    "Напишите или наговорите числа — например:\n"
    "<code>б 12 ж 6 у 40</code> — если блюдо одно;\n"
    "<code>овсянка 200 г б 12 ж 6 у 40 292 ккал</code> — если блюд несколько.\n"
    "Числа — на съеденную порцию. Ккал можно не называть: посчитаю сам.\n"
    "Запомню их за этим блюдом и в следующий раз подставлю без оценки."
)

PRODUCT_MACROS_PROMPT = (
    "✏️ <b>БЖУ с этикетки</b>\n"
    "Напишите или наговорите то, что напечатано на упаковке <b>на 100 г</b>:\n"
    "<code>ккал 86 б 16 ж 1.8 у 2</code>\n"
    "Можно по одному полю: <code>углеводы 12</code>.\n"
    "Эти числа запомню за продуктом и буду подставлять вместо оценки."
)


async def _drop(callback: CallbackQuery, state: FSMContext, text: str = "Отменено.") -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(text)


# ------------------------------------------------------------------ meals

@router.callback_query(F.data == "meal:ok", MealFlow.confirming)
async def meal_ok(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = meal_from_dict(data.get(DRAFT_KEY) or {})
    raw_stamp = data.get(EATEN_AT_KEY)
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        eaten_local = datetime.fromisoformat(raw_stamp) if raw_stamp else local_now(user)
        media_id = None
        file_ids = data.get(FILES_KEY) or []
        if file_ids:
            media = await repo.save_media(session, user, kind="meal", tg_file_id=file_ids[0])
            media_id = media.id
        meal = await repo.save_meal(
            session, user, draft, eaten_at=to_utc(eaten_local, user), media_id=media_id
        )
        # Second sighting of a dish turns it into a one-tap button (/my).
        await repo.remember_meal(session, user, draft)
        # …and БЖУ typed by hand stay with the dish for good.
        await repo.remember_meal_macros(session, user, draft)
        # То же для чисел, прочитанных с этикетки (`spec/dictionary.md`).
        await repo.remember_meal_macros(session, user, draft, source="label")
        title = meal.title or "приём пищи"
        shortcut = await repo.suggest_dictionary(session, user, title, kinds=("meal",), limit=1)
        # Полоса дневного коридора — только если цель задана (`spec/body.md`).
        from src.handlers.body import day_progress_text

        progress = await day_progress_text(session, user, now=local_now(user))
        # Гарвардская тарелка — после каждой записи, если не выключена
        # (`spec/plate.md`); сбой оценки не имеет права съесть подтверждение.
        from src.handlers.plate import plate_advice_text

        try:
            plate_text = await plate_advice_text(session, user, now=local_now(user))
        except Exception:
            log.exception("plate advice failed")
            plate_text = None
    await state.clear()
    await callback.answer("Записано")
    tail = (
        "\n⭐️ Это блюдо теперь в личном словаре — в следующий раз хватит одной кнопки (/my)."
        if shortcut
        else ""
    )
    await callback.message.edit_text(
        f"✅ Записано: <b>{title}</b> в {eaten_local:%H:%M}.\n"
        f"Через час-полтора пришлите сахар — и приём попадёт в статистику.{tail}"
        + (f"\n\n{progress}" if progress else "")
        + (f"\n\n{plate_text}" if plate_text else "")
    )


@router.callback_query(F.data == "meal:edit", MealFlow.confirming)
async def meal_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MealFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите, что поправить — например:\n"
        "<code>гречка 250, курица 100, салат 150</code>\n"
        "Формат: продукт и граммы через запятую.",
        reply_markup=cancel_only(),
    )


@router.message(MealFlow.editing)
async def meal_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Merge the correction into the recognition; never replace it wholesale.

    `text_override` carries a transcribed voice note — a correction can be
    spoken as easily as typed (`spec/ingest.md` § Корректировки).
    """
    instruction = (text_override or message.text or "").strip()
    if not instruction:
        await message.answer("Скажите или напишите, что поправить.")
        return
    data = await state.get_data()
    old = meal_from_dict(data.get(DRAFT_KEY) or {})

    result = apply_meal_correction(old, instruction)
    new = result.draft
    if result.unmatched or not result.changes:
        # Something the deterministic pass could not place — hand the draft and
        # the instruction to the model, which edits rather than re-recognises.
        try:
            new = await recognize.correct_meal(old, instruction)
        except recognize.RecognitionError:
            if not result.changes:
                await message.answer(
                    "Не понял правку. Скажите проще — «убери салат», «гречки было 250», "
                    "«вместо курицы индейка»."
                )
                return

    if not new.items:
        await message.answer(
            "После правки не осталось ни одного блюда. Нажмите «🗑 Отменить», "
            "если запись не нужна."
        )
        return

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        # The correction itself is data: it outranks the machine recognition
        # for good (constitution, III) and feeds nothing back to the model.
        await repo.save_correction(
            session,
            user,
            entity_type="meal_draft",
            entity_id=None,
            field="items",
            old_value=_items_line(old),
            new_value=instruction,
        )
    from src.handlers.views import remember_typed_macros, show_meal_draft

    # БЖУ, введённые руками, запоминаются за блюдом — и об этом говорим вслух.
    await remember_typed_macros(message, new)

    await show_meal_draft(
        message,
        state,
        new,
        file_ids=data.get(FILES_KEY),
        eaten_at_local=(
            datetime.fromisoformat(data[EATEN_AT_KEY]) if data.get(EATEN_AT_KEY) else None
        ),
        applied=[change.describe() for change in result.changes],
    )


def _items_line(draft: MealDraft) -> str:
    return ", ".join(f"{i.name} {i.portion_g or ''}".strip() for i in draft.items)


@router.callback_query(F.data == "meal:macros", MealFlow.confirming)
async def meal_macros(callback: CallbackQuery, state: FSMContext) -> None:
    """«✏️ БЖУ» — отдельный вход для чисел: карточка не пересобирается."""
    await state.set_state(MealFlow.editing_macros)
    await callback.answer()
    await callback.message.answer(MACROS_PROMPT, reply_markup=cancel_only())


@router.message(MealFlow.editing_macros)
async def meal_apply_macros(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Числа для позиций текущей карточки; всё остальное в ней не трогаем."""
    instruction = (text_override or message.text or "").strip()
    data = await state.get_data()
    old = meal_from_dict(data.get(DRAFT_KEY) or {})
    result = apply_meal_correction(old, instruction)
    macro_changes = [change for change in result.changes if change.kind in ("macros", "portion")]
    if not macro_changes:
        await message.answer(
            "Не понял числа. Напишите, например: <code>б 12 ж 6 у 40</code> "
            "или <code>овсянка 200 г б 12 ж 6 у 40</code>."
        )
        return
    from src.handlers.views import remember_typed_macros, show_meal_draft

    await remember_typed_macros(message, result.draft)
    await show_meal_draft(
        message,
        state,
        result.draft,
        file_ids=data.get(FILES_KEY),
        eaten_at_local=(
            datetime.fromisoformat(data[EATEN_AT_KEY]) if data.get(EATEN_AT_KEY) else None
        ),
        applied=[change.describe() for change in macro_changes],
    )


@router.callback_query(F.data == "meal:time", MealFlow.confirming)
async def meal_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MealFlow.retiming)
    await callback.answer()
    await callback.message.answer(
        "Во сколько это было? Например <code>13:40</code> или «вчера в 21:00».",
        reply_markup=cancel_only(),
    )


@router.message(MealFlow.retiming)
async def meal_apply_time(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    from src.ingest.text_parse import parse_text

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    parsed = parse_text(text_override or message.text or "", now=now)
    if parsed.at is None:
        await message.answer("Не понял время. Напишите, например, <code>13:40</code>.")
        return
    data = await state.get_data()
    from src.handlers.views import show_meal_draft

    await show_meal_draft(
        message,
        state,
        meal_from_dict(data.get(DRAFT_KEY) or {}),
        file_ids=data.get(FILES_KEY),
        eaten_at_local=parsed.at,
    )


@router.callback_query(F.data == "meal:drop")
async def meal_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await _drop(callback, state, "Приём пищи не записан.")


# ------------------------------------------------------------------ glucose

@router.callback_query(F.data == "glu:ok", GlucoseFlow.confirming)
async def glucose_ok(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    drafts = glucose_from_dicts(data.get(DRAFT_KEY) or [])
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        media_id = None
        file_ids = data.get(FILES_KEY) or []
        if file_ids:
            media = await repo.save_media(session, user, kind="glucose", tg_file_id=file_ids[0])
            media_id = media.id
        rows = await repo.save_glucose(
            session, user, drafts, source="screenshot", media_id=media_id
        )
        unit = user.glucose_unit
    await state.clear()
    await callback.answer("Сохранено")
    await callback.message.edit_text(
        f"✅ Сохранено показаний: {len(rows)}"
        + (f" (последнее {format_value(rows[-1].value_mmol, unit)})" if rows else "")
    )


@router.callback_query(F.data == "glu:unit", GlucoseFlow.confirming)
async def glucose_flip_unit(callback: CallbackQuery, state: FSMContext) -> None:
    """The 18–33 overlap between the scales is resolved by one tap, not a guess."""
    from src.ingest.units import MG_DL_PER_MMOL

    data = await state.get_data()
    drafts = glucose_from_dicts(data.get(DRAFT_KEY) or [])
    for draft in drafts:
        if draft.unit_input == MGDL:
            draft.value_mmol = round(draft.value_mmol * MG_DL_PER_MMOL, 2)
            draft.unit_input = MMOL
        else:
            draft.value_mmol = round(draft.value_mmol / MG_DL_PER_MMOL, 2)
            draft.unit_input = MGDL
    await state.update_data({DRAFT_KEY: glucose_to_dicts(drafts)})
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        unit = user.glucose_unit
    await callback.answer("Единицы переключены")
    lines = ["🩸 <b>Распознанные показания</b>", ""]
    for item in drafts:
        lines.append(f"• {item.measured_at:%d.%m %H:%M} — {format_value(item.value_mmol, unit)}")
    lines.append("\nВсё верно?")
    from src.keyboards import confirm_glucose

    await callback.message.edit_text("\n".join(lines), reply_markup=confirm_glucose())


@router.callback_query(F.data == "glu:edit", GlucoseFlow.confirming)
async def glucose_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GlucoseFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите правильные значения: <code>8.2 в 9:15</code> "
        "(можно несколько строк).",
        reply_markup=cancel_only(),
    )


@router.message(GlucoseFlow.editing)
async def glucose_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    from src.ingest.text_parse import parse_text
    from src.ingest.units import to_mmol
    from src.vision.schemas import GlucoseDraft

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        unit = user.glucose_unit
    drafts: list[GlucoseDraft] = []
    for line in (text_override or message.text or "").splitlines():
        parsed = parse_text(line, now=now)
        for value, raw_unit in parsed.glucose:
            drafts.append(
                GlucoseDraft(
                    measured_at=parsed.at or now,
                    value_mmol=to_mmol(value, raw_unit),
                    unit_input=raw_unit,
                )
            )
    if not drafts:
        await message.answer("Не разобрал. Например: <code>8.2 в 9:15</code>")
        return
    from src.handlers.views import show_glucose_draft

    await show_glucose_draft(message, state, drafts, unit=unit)


@router.callback_query(F.data == "glu:drop")
async def glucose_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await _drop(callback, state, "Показания не сохранены.")


# ------------------------------------------------------------------ products

@router.callback_query(F.data == "prod:save", ProductFlow.confirming)
async def product_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = product_from_dict(data.get(DRAFT_KEY) or {})
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        media_ids: list[tuple[int, str]] = []
        for index, file_id in enumerate(data.get(FILES_KEY) or []):
            media = await repo.save_media(session, user, kind="label", tg_file_id=file_id)
            media_ids.append((media.id, "front" if index == 0 else "back"))
        product = await repo.save_product(session, user, draft, media_ids=media_ids)
        name = product.name
        # БЖУ с этикетки — такой же факт, как названный руками: запоминаем.
        remembered = await repo.remember_product_macros(session, user, draft)
    await state.clear()
    await callback.answer("Запомнил")
    await callback.message.edit_text(
        f"💾 Продукт «{name}» сохранён. Пришлёте его снова — узнаю по названию или штрихкоду."
    )
    if remembered:
        await callback.message.answer(format_remembered_label(draft, remembered))


@router.callback_query(F.data == "prod:eat", ProductFlow.confirming)
async def product_eat(callback: CallbackQuery, state: FSMContext) -> None:
    """Log the package as a meal; portion defaults to 100 g and is editable."""
    data = await state.get_data()
    draft = product_from_dict(data.get(DRAFT_KEY) or {})
    item = ItemDraft(
        name=repo.product_item_name(draft),
        portion_g=100.0,
        kcal=draft.kcal_100,
        protein_g=draft.protein_100,
        fat_g=draft.fat_100,
        carbs_g=draft.carbs_100,
        fiber_g=draft.fiber_100,
        tags=draft.flags,
        # Числа напечатаны на упаковке — это не оценка модели.
        macros_source="label" if draft.kcal_100 is not None else "",
    )
    meal = MealDraft(title=draft.name, items=[item], confidence=draft.confidence, source="label")
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        media_ids: list[tuple[int, str]] = []
        for index, file_id in enumerate(data.get(FILES_KEY) or []):
            media = await repo.save_media(session, user, kind="label", tg_file_id=file_id)
            media_ids.append((media.id, "front" if index == 0 else "back"))
        await repo.save_product(session, user, draft, media_ids=media_ids)
        remembered = await repo.remember_product_macros(session, user, draft)
    await callback.answer()
    if remembered:
        await callback.message.answer(format_remembered_label(draft, remembered))
    await state.update_data({DRAFT_KEY: meal_to_dict(meal)})
    from src.handlers.views import show_meal_draft

    await callback.message.answer("Сколько граммов вы съели? Проверьте порцию:")
    await show_meal_draft(callback.message, state, meal)


@router.callback_query(F.data == "prod:more", ProductFlow.confirming)
async def product_more(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductFlow.awaiting_second_side)
    await callback.answer()
    await callback.message.answer(
        "Пришлите фото второй стороны упаковки (состав и пищевая ценность) — объединю.",
        reply_markup=cancel_only(),
    )


@router.callback_query(F.data == "prod:macros", ProductFlow.confirming)
async def product_macros(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductFlow.editing_macros)
    await callback.answer()
    await callback.message.answer(PRODUCT_MACROS_PROMPT, reply_markup=cancel_only())


@router.message(ProductFlow.editing_macros)
async def product_apply_macros(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Числа на 100 г с этикетки: правим только их и сразу запоминаем."""
    instruction = (text_override or message.text or "").strip()
    data = await state.get_data()
    draft = product_from_dict(data.get(DRAFT_KEY) or {})
    applied = _apply_label_macros(draft, instruction)
    if not applied:
        await message.answer(
            "Не понял числа. Напишите, например: <code>ккал 86 б 16 ж 1.8 у 2</code>."
        )
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.save_correction(
            session,
            user,
            entity_type="product_draft",
            entity_id=None,
            field="macros",
            old_value=draft.name,
            new_value=instruction,
        )
        # Правка руками — это уже слово пользователя, не этикетка.
        await repo.remember_nutrition(
            session,
            user,
            name=repo.product_item_name(draft),
            values=Remembered(
                kcal=draft.kcal_100,
                protein_g=draft.protein_100,
                fat_g=draft.fat_100,
                carbs_g=draft.carbs_100,
                fiber_g=draft.fiber_100,
                portion_g=None,
            ),
            source="user",
        )
    from src.handlers.views import show_product_draft

    await message.answer(format_remembered_label(draft, repo.product_item_name(draft), typed=True))
    await show_product_draft(
        message,
        state,
        draft,
        mode=data.get("draft_mode") or "eaten",
        file_ids=data.get(FILES_KEY),
        applied=applied,
    )


@router.callback_query(F.data == "prod:edit", ProductFlow.confirming)
async def product_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите или наговорите, что поправить на карточке:\n"
        "<code>это творог 5%</code> · <code>углеводы 12</code> · <code>ккал 86</code>",
        reply_markup=cancel_only(),
    )


@router.message(ProductFlow.editing)
async def product_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Merge into the label reading: untouched fields keep what was scanned."""
    instruction = (text_override or message.text or "").strip()
    data = await state.get_data()
    draft = product_from_dict(data.get(DRAFT_KEY) or {})
    applied = _apply_product_correction(draft, instruction)
    if not applied:
        await message.answer(
            "Не понял правку. Например: <code>углеводы 12</code>, "
            "<code>это творог 5%</code>."
        )
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.save_correction(
            session,
            user,
            entity_type="product_draft",
            entity_id=None,
            field="label",
            old_value=draft.name,
            new_value=instruction,
        )
    from src.handlers.views import show_product_draft

    await show_product_draft(
        message,
        state,
        draft,
        mode=data.get("draft_mode") or "eaten",
        file_ids=data.get(FILES_KEY),
        applied=applied,
    )


_LABEL_FIELDS = {
    "kcal": ("kcal_100", "ккал"),
    "protein_g": ("protein_100", "белки"),
    "fat_g": ("fat_100", "жиры"),
    "carbs_g": ("carbs_100", "углеводы"),
    "fiber_g": ("fiber_100", "клетчатка"),
}


def _apply_label_macros(draft, instruction: str) -> list[str]:
    """«ккал 86 б 16 ж 1.8 у 2» → числа на 100 г. Mutates `draft` in place.

    Разбор тот же, что у «✏️ БЖУ» на карточке еды (`src/ingest/correction`),
    поэтому «б/ж/у» и «белки/жиры/углеводы» работают одинаково везде.
    """
    from src.ingest.correction import _parse_macros

    values, leftover = _parse_macros(instruction or "")
    applied: list[str] = []
    for name, (attribute, label) in _LABEL_FIELDS.items():
        if name not in values:
            continue
        before = getattr(draft, attribute)
        setattr(draft, attribute, values[name])
        applied.append(f"{label} на 100 г — {before if before is not None else '—'} → {values[name]:g}")
    sugars = re.search(r"сахар\w*\s*[:—-]?\s*(\d+(?:[.,]\d+)?)", leftover, re.I)
    if sugars:
        value = float(sugars.group(1).replace(",", "."))
        draft.sugars_100 = value
        applied.append(f"сахар на 100 г — {value:g}")
    return applied


_PRODUCT_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("kcal_100", "ккал", ("ккал", "калори", "энергет")),
    ("protein_100", "белки", ("белк", "белок", "протеин")),
    ("fat_100", "жиры", ("жир",)),
    ("carbs_100", "углеводы", ("углевод",)),
    ("sugars_100", "сахар", ("сахар",)),
    ("fiber_100", "клетчатка", ("клетчатк", "волокн")),
)


def _apply_product_correction(draft, instruction: str) -> list[str]:
    """Mutates `draft` in place; returns human-readable descriptions of changes."""
    applied: list[str] = []
    text = (instruction or "").strip()
    if not text:
        return applied
    for clause in re.split(r"[,;\n]", text):
        clause = clause.strip(" .")
        if not clause:
            continue
        number = re.search(r"(\d+(?:[.,]\d+)?)", clause)
        lowered = clause.lower()
        matched = False
        for attribute, label, keywords in _PRODUCT_FIELDS:
            if number and any(keyword in lowered for keyword in keywords):
                value = float(number.group(1).replace(",", "."))
                before = getattr(draft, attribute)
                setattr(draft, attribute, value)
                applied.append(f"{label} на 100 г — {before if before is not None else '—'} → {value:g}")
                matched = True
                break
        if matched:
            continue
        name = re.sub(r"^(?:это|называется|не\s+\S+\s*,?\s*а)\s+", "", clause, flags=re.I).strip()
        if name and name.lower() != (draft.name or "").lower() and not number:
            applied.append(f"название — {draft.name or '—'} → {name}")
            draft.name = name
    return applied


@router.callback_query(F.data == "prod:drop")
async def product_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await _drop(callback, state, "Продукт не сохранён.")


# ------------------------------------------------------------------ labs

@router.callback_query(F.data == "lab:ok", LabFlow.confirming)
async def lab_ok(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = lab_from_dict(data.get(DRAFT_KEY) or {})
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        media_id = None
        file_ids = data.get(FILES_KEY) or []
        if file_ids:
            media = await repo.save_media(session, user, kind="lab", tg_file_id=file_ids[0])
            media_id = media.id
        rows = await repo.save_labs(session, user, draft, media_id=media_id)
        await repo.mark_feature_used(session, user, "labs")
        # Продукты-источники по маркерам вне референса (`spec/labs.md`).
        from src.handlers.labs import lab_review_text

        try:
            review = await lab_review_text(
                session, user, header="🧪 <b>С учётом этого результата</b>"
            )
        except Exception:
            log.exception("lab review failed")
            review = None
    await state.clear()
    await callback.answer("Сохранено")
    flagged = [r for r in rows if r.flag in {"high", "low"}]
    tail = ""
    if flagged:
        tail = (
            "\n\nВне референса: "
            + ", ".join(r.marker for r in flagged)
            + ".\nЭто повод показать результат врачу — я диагнозов не ставлю."
        )
    await callback.message.edit_text(f"✅ Сохранено показателей: {len(rows)}.{tail}")
    if review:
        await callback.message.answer(review)


@router.callback_query(F.data == "lab:edit", LabFlow.confirming)
async def lab_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LabFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите или наговорите правильные значения — маркер и число:\n"
        "<code>глюкоза 6.1, HbA1c 5.8</code>",
        reply_markup=cancel_only(),
    )


@router.message(LabFlow.editing)
async def lab_apply_edit(
    message: Message, state: FSMContext, *, text_override: str | None = None
) -> None:
    """Only the markers named are touched; the rest of the panel stays as read."""
    import re

    instruction = (text_override or message.text or "").strip()
    data = await state.get_data()
    draft = lab_from_dict(data.get(DRAFT_KEY) or {})
    applied: list[str] = []
    for clause in re.split(r"[,;\n]", instruction):
        clause = clause.strip(" .")
        match = re.match(r"^(?P<name>.+?)[\s:=]+(?P<value>\d+(?:[.,]\d+)?)$", clause)
        if not match:
            continue
        name = match.group("name").strip().lower()
        value = float(match.group("value").replace(",", "."))
        for marker in draft.markers:
            if name in marker.marker.lower() or marker.marker.lower() in name:
                before = marker.value
                marker.value = value
                marker.value_text = None
                applied.append(f"{marker.marker} — {before if before is not None else '—'} → {value:g}")
                break
        else:
            from src.vision.schemas import MarkerDraft

            draft.markers.append(MarkerDraft(marker=match.group("name").strip(), value=value))
            applied.append(f"добавлен маркер {match.group('name').strip()} — {value:g}")
    if not applied:
        await message.answer("Не разобрал. Например: <code>глюкоза 6.1</code>")
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.save_correction(
            session,
            user,
            entity_type="lab_draft",
            entity_id=None,
            field="markers",
            old_value=", ".join(m.marker for m in draft.markers),
            new_value=instruction,
        )
    from src.handlers.views import show_lab_draft

    await show_lab_draft(message, state, draft, file_ids=data.get(FILES_KEY), applied=applied)


@router.callback_query(F.data == "lab:drop")
async def lab_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await _drop(callback, state, "Анализы не сохранены.")


__all__ = ["router"]
