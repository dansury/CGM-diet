"""Render a draft, stash it in the FSM, wait for the user's tap.

Drafts are held in FSM data (JSON-serialisable dicts) rather than in callback
payloads, so a button pressed twice or long after the fact cannot resurrect
stale data belonging to someone else.
"""

from __future__ import annotations

from datetime import datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.db import repo
from src.handlers.deps import session_scope
from src.handlers.states import (
    GlucoseFlow,
    LabFlow,
    MealFlow,
    MedicationFlow,
    ProductFlow,
)
from src.ingest.nutrition import apply_memory
from src.ingest.units import format_value
from src.keyboards import (
    confirm_glucose,
    confirm_labs,
    confirm_meal,
    confirm_medication,
    product_actions,
)
from src.logging_setup import get_logger
from src.reporting import (
    format_labs,
    format_meal_draft,
    format_medication_draft,
    format_product,
)
from src.vision.schemas import (
    GlucoseDraft,
    LabDraft,
    MealDraft,
    MedicationDraft,
    ProductDraft,
    glucose_to_dicts,
    lab_to_dict,
    meal_to_dict,
    med_to_dict,
    product_to_dict,
)

log = get_logger("handlers.views")

DRAFT_KEY = "draft"
FILES_KEY = "draft_files"
EATEN_AT_KEY = "eaten_at"
TAKEN_AT_KEY = "taken_at"
MODE_KEY_VIEW = "draft_mode"


async def show_meal_draft(
    message: Message,
    state: FSMContext,
    draft: MealDraft,
    *,
    file_ids: list[str] | None = None,
    eaten_at_local: datetime | None = None,
    applied: list[str] | None = None,
) -> None:
    """`applied` — what a correction just changed, echoed back so the user sees
    that the edit was *merged*, not that the card was rebuilt from scratch."""
    await fill_from_memory(message, draft)
    await state.set_state(MealFlow.confirming)
    await state.update_data(
        {
            DRAFT_KEY: meal_to_dict(draft),
            FILES_KEY: file_ids or [],
            EATEN_AT_KEY: eaten_at_local.isoformat() if eaten_at_local else None,
        }
    )
    await message.answer(
        format_meal_draft(draft, eaten_at=eaten_at_local, applied=applied),
        reply_markup=confirm_meal(),
    )


async def fill_from_memory(message: Message, draft: MealDraft) -> None:
    """Подставить БЖУ, которые пользователь однажды ввёл сам.

    Fail-soft: a lookup that goes wrong must not swallow the card — the draft
    is then shown with the machine's own numbers (`spec/dictionary.md`
    § Память БЖУ).
    """
    if not draft.items:
        return
    try:
        async with session_scope() as session:
            user = await repo.get_or_create_user(session, message.chat.id)
            memory = await repo.load_nutrition_memory(
                session, user, [item.name for item in draft.items]
            )
    except Exception:
        log.exception("nutrition memory lookup failed")
        return
    if memory:
        apply_memory(draft, memory)


async def show_medication_draft(
    message: Message,
    state: FSMContext,
    draft: MedicationDraft,
    *,
    file_ids: list[str] | None = None,
    taken_at_local: datetime | None = None,
    applied: list[str] | None = None,
) -> None:
    await state.set_state(MedicationFlow.confirming)
    await state.update_data(
        {
            DRAFT_KEY: med_to_dict(draft),
            FILES_KEY: file_ids or [],
            TAKEN_AT_KEY: taken_at_local.isoformat() if taken_at_local else None,
        }
    )
    await message.answer(
        format_medication_draft(draft, taken_at=taken_at_local, applied=applied),
        reply_markup=confirm_medication(),
    )


async def show_glucose_draft(
    message: Message,
    state: FSMContext,
    drafts: list[GlucoseDraft],
    *,
    unit: str,
    file_ids: list[str] | None = None,
) -> None:
    await state.set_state(GlucoseFlow.confirming)
    await state.update_data({DRAFT_KEY: glucose_to_dicts(drafts), FILES_KEY: file_ids or []})
    lines = ["🩸 <b>Распознанные показания</b>", ""]
    for item in drafts:
        trend = f" {item.trend}" if item.trend else ""
        lines.append(f"• {item.measured_at:%d.%m %H:%M} — {format_value(item.value_mmol, unit)}{trend}")
    if drafts[0].device:
        lines.append(f"\nУстройство: {drafts[0].device}")
    lines.append("\nВсё верно? Скорректировать можно текстом или голосом.")
    await message.answer("\n".join(lines), reply_markup=confirm_glucose())


async def show_product_draft(
    message: Message,
    state: FSMContext,
    draft: ProductDraft,
    *,
    mode: str,
    file_ids: list[str] | None = None,
    applied: list[str] | None = None,
) -> None:
    await state.set_state(ProductFlow.confirming)
    await state.update_data(
        {DRAFT_KEY: product_to_dict(draft), FILES_KEY: file_ids or [], MODE_KEY_VIEW: mode}
    )
    if mode == "check":
        from src.handlers.reports import product_verdict_text

        text = await product_verdict_text(message.chat.id, draft)
    else:
        text = format_product(draft, mode=mode)
    if applied:
        text += "\n\n<b>Учтено из вашей правки:</b>\n" + "\n".join(f"• {a}" for a in applied)
    await message.answer(text, reply_markup=product_actions(mode=mode))


async def show_lab_draft(
    message: Message,
    state: FSMContext,
    draft: LabDraft,
    *,
    file_ids: list[str] | None = None,
    applied: list[str] | None = None,
) -> None:
    await state.set_state(LabFlow.confirming)
    await state.update_data({DRAFT_KEY: lab_to_dict(draft), FILES_KEY: file_ids or []})
    text = format_labs(draft)
    if applied:
        text += "\n\n<b>Учтено из вашей правки:</b>\n" + "\n".join(f"• {a}" for a in applied)
    await message.answer(text, reply_markup=confirm_labs())


__all__ = [
    "DRAFT_KEY",
    "EATEN_AT_KEY",
    "FILES_KEY",
    "MODE_KEY_VIEW",
    "TAKEN_AT_KEY",
    "show_glucose_draft",
    "show_lab_draft",
    "fill_from_memory",
    "show_meal_draft",
    "show_medication_draft",
    "show_product_draft",
]
