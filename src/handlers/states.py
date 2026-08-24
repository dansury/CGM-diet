"""FSM states. One group per flow (`spec/bot.md` § Flows)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MealFlow(StatesGroup):
    confirming = State()
    editing = State()
    editing_macros = State()   # «✏️ БЖУ» — только числа, без пересбора карточки
    retiming = State()


class GlucoseFlow(StatesGroup):
    confirming = State()
    editing = State()


class ProductFlow(StatesGroup):
    confirming = State()
    awaiting_second_side = State()
    editing = State()
    editing_macros = State()   # «✏️ БЖУ» — числа на 100 г с этикетки


class LabFlow(StatesGroup):
    confirming = State()
    editing = State()


class MedicationFlow(StatesGroup):
    confirming = State()
    editing = State()
    retiming = State()


class WellbeingFlow(StatesGroup):
    scoring = State()
    picking = State()
    free_text = State()


class SettingsFlow(StatesGroup):
    editing = State()


__all__ = [
    "GlucoseFlow",
    "LabFlow",
    "MealFlow",
    "MedicationFlow",
    "ProductFlow",
    "SettingsFlow",
    "WellbeingFlow",
]
