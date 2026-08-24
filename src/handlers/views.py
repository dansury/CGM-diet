"""Render a draft, stash it in the FSM, wait for the user's tap.

Drafts are held in FSM data (JSON-serialisable dicts) rather than in callback
payloads, so a button pressed twice or long after the fact cannot resurrect
stale data belonging to someone else.
"""

from __future__ import annotations

from datetime import datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.handlers.states import GlucoseFlow, LabFlow, MealFlow, ProductFlow
from src.ingest.units import format_value
from src.keyboards import confirm_glucose, confirm_labs, confirm_meal, product_actions
from src.reporting import format_labs, format_meal_draft, format_product
from src.vision.schemas import (
    GlucoseDraft,
    LabDraft,
    MealDraft,
    ProductDraft,
    glucose_to_dicts,
    lab_to_dict,
    meal_to_dict,
    product_to_dict,
)

DRAFT_KEY = "draft"
FILES_KEY = "draft_files"
EATEN_AT_KEY = "eaten_at"
MODE_KEY_VIEW = "draft_mode"


async def show_meal_draft(
    message: Message,
    state: FSMContext,
    draft: MealDraft,
    *,
    file_ids: list[str] | None = None,
    eaten_at_local: datetime | None = None,
) -> None:
    await state.set_state(MealFlow.confirming)
    await state.update_data(
        {
            DRAFT_KEY: meal_to_dict(draft),
            FILES_KEY: file_ids or [],
            EATEN_AT_KEY: eaten_at_local.isoformat() if eaten_at_local else None,
        }
    )
    await message.answer(
        format_meal_draft(draft, eaten_at=eaten_at_local), reply_markup=confirm_meal()
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
    lines.append("\nВсё верно?")
    await message.answer("\n".join(lines), reply_markup=confirm_glucose())


async def show_product_draft(
    message: Message,
    state: FSMContext,
    draft: ProductDraft,
    *,
    mode: str,
    file_ids: list[str] | None = None,
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
    await message.answer(text, reply_markup=product_actions(mode=mode))


async def show_lab_draft(
    message: Message,
    state: FSMContext,
    draft: LabDraft,
    *,
    file_ids: list[str] | None = None,
) -> None:
    await state.set_state(LabFlow.confirming)
    await state.update_data({DRAFT_KEY: lab_to_dict(draft), FILES_KEY: file_ids or []})
    await message.answer(format_labs(draft), reply_markup=confirm_labs())


__all__ = [
    "DRAFT_KEY",
    "EATEN_AT_KEY",
    "FILES_KEY",
    "MODE_KEY_VIEW",
    "show_glucose_draft",
    "show_lab_draft",
    "show_meal_draft",
    "show_product_draft",
]
