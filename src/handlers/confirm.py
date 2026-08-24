"""Confirmation callbacks: the moment a draft becomes a row.

Every «✅» writes; every «✏️ Исправить» records a `corrections` row alongside the
new value, because the user's corrections are training data for nothing less
than their own statistics.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db import repo
from src.handlers.deps import local_now, session_scope, to_utc
from src.handlers.states import GlucoseFlow, LabFlow, MealFlow, ProductFlow
from src.handlers.views import DRAFT_KEY, EATEN_AT_KEY, FILES_KEY, MODE_KEY_VIEW
from src.ingest.units import MGDL, MMOL, format_value
from src.keyboards import main_menu
from src.logging_setup import get_logger
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
    await state.clear()
    await callback.answer("Записано")
    await callback.message.edit_text(
        f"✅ Записано: <b>{meal.title or 'приём пищи'}</b> в {eaten_local:%H:%M}.\n"
        "Через час-полтора пришлите сахар — и приём попадёт в статистику."
    )


@router.callback_query(F.data == "meal:edit", MealFlow.confirming)
async def meal_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MealFlow.editing)
    await callback.answer()
    await callback.message.answer(
        "Напишите, что поправить — например:\n"
        "<code>гречка 250, курица 100, салат 150</code>\n"
        "Формат: продукт и граммы через запятую."
    )


@router.message(MealFlow.editing)
async def meal_apply_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    old = meal_from_dict(data.get(DRAFT_KEY) or {})
    new = _parse_edit(message.text or "", old)
    if not new.items:
        await message.answer("Не разобрал. Формат: <code>гречка 250, курица 100</code>")
        return
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        await repo.save_correction(
            session,
            user,
            entity_type="meal_draft",
            entity_id=None,
            field="items",
            old_value=", ".join(f"{i.name} {i.portion_g or ''}".strip() for i in old.items),
            new_value=message.text,
        )
    from src.handlers.views import show_meal_draft

    await show_meal_draft(
        message,
        state,
        new,
        file_ids=data.get(FILES_KEY),
        eaten_at_local=(
            datetime.fromisoformat(data[EATEN_AT_KEY]) if data.get(EATEN_AT_KEY) else None
        ),
    )


def _parse_edit(text: str, old: MealDraft) -> MealDraft:
    """`гречка 250, курица 100` → items, keeping nutrients scaled where known.

    Nutrients from the original recognition are rescaled by the new portion so a
    corrected weight does not silently keep the old calories.
    """
    from src.analytics.tags import infer_tags, normalize_name

    by_name = {normalize_name(i.name): i for i in old.items}
    items: list[ItemDraft] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.rsplit(maxsplit=1)
        portion: float | None = None
        name = chunk
        if len(parts) == 2:
            try:
                portion = float(parts[1].replace(",", ".").replace("г", "").strip())
                name = parts[0].strip()
            except ValueError:
                portion = None
        previous = by_name.get(normalize_name(name))
        item = ItemDraft(name=name, portion_g=portion, tags=infer_tags(name))
        if previous is not None:
            item.tags = previous.tags or item.tags
            scale = 1.0
            if portion and previous.portion_g:
                scale = portion / previous.portion_g
            for field_name in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g"):
                value = getattr(previous, field_name)
                if value is not None:
                    setattr(item, field_name, round(value * scale, 1))
        items.append(item)
    return MealDraft(
        title=old.title or (items[0].name if items else ""),
        items=items,
        confidence=1.0,
        notes="исправлено пользователем",
        source=old.source,
        raw_text=text,
    )


@router.callback_query(F.data == "meal:time", MealFlow.confirming)
async def meal_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MealFlow.retiming)
    await callback.answer()
    await callback.message.answer("Во сколько это было? Например <code>13:40</code> или «вчера в 21:00».")


@router.message(MealFlow.retiming)
async def meal_apply_time(message: Message, state: FSMContext) -> None:
    from src.ingest.text_parse import parse_text

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
    parsed = parse_text(message.text or "", now=now)
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
        "(можно несколько строк)."
    )


@router.message(GlucoseFlow.editing)
async def glucose_apply_edit(message: Message, state: FSMContext) -> None:
    from src.ingest.text_parse import parse_text
    from src.ingest.units import to_mmol
    from src.vision.schemas import GlucoseDraft

    async with session_scope() as session:
        user = await repo.get_or_create_user(session, message.chat.id)
        now = local_now(user)
        unit = user.glucose_unit
    drafts: list[GlucoseDraft] = []
    for line in (message.text or "").splitlines():
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
    await state.clear()
    await callback.answer("Запомнил")
    await callback.message.edit_text(
        f"💾 Продукт «{name}» сохранён. Пришлёте его снова — узнаю по названию или штрихкоду."
    )


@router.callback_query(F.data == "prod:eat", ProductFlow.confirming)
async def product_eat(callback: CallbackQuery, state: FSMContext) -> None:
    """Log the package as a meal; portion defaults to 100 g and is editable."""
    data = await state.get_data()
    draft = product_from_dict(data.get(DRAFT_KEY) or {})
    item = ItemDraft(
        name=f"{draft.brand + ' ' if draft.brand else ''}{draft.name}".strip(),
        portion_g=100.0,
        kcal=draft.kcal_100,
        protein_g=draft.protein_100,
        fat_g=draft.fat_100,
        carbs_g=draft.carbs_100,
        fiber_g=draft.fiber_100,
        tags=draft.flags,
    )
    meal = MealDraft(title=draft.name, items=[item], confidence=draft.confidence, source="label")
    async with session_scope() as session:
        user = await repo.get_or_create_user(session, callback.from_user.id)
        media_ids: list[tuple[int, str]] = []
        for index, file_id in enumerate(data.get(FILES_KEY) or []):
            media = await repo.save_media(session, user, kind="label", tg_file_id=file_id)
            media_ids.append((media.id, "front" if index == 0 else "back"))
        await repo.save_product(session, user, draft, media_ids=media_ids)
    await callback.answer()
    await state.update_data({DRAFT_KEY: meal_to_dict(meal)})
    from src.handlers.views import show_meal_draft

    await callback.message.answer("Сколько граммов вы съели? Проверьте порцию:")
    await show_meal_draft(callback.message, state, meal)


@router.callback_query(F.data == "prod:more", ProductFlow.confirming)
async def product_more(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductFlow.awaiting_second_side)
    await callback.answer()
    await callback.message.answer(
        "Пришлите фото второй стороны упаковки (состав и пищевая ценность) — объединю."
    )


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


@router.callback_query(F.data == "lab:drop")
async def lab_drop(callback: CallbackQuery, state: FSMContext) -> None:
    await _drop(callback, state, "Анализы не сохранены.")


__all__ = ["router"]
