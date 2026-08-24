"""FSM states. One group per flow (`spec/bot.md` § Flows)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MealFlow(StatesGroup):
    confirming = State()
    editing = State()
    retiming = State()


class GlucoseFlow(StatesGroup):
    confirming = State()
    editing = State()


class ProductFlow(StatesGroup):
    confirming = State()
    awaiting_second_side = State()


class LabFlow(StatesGroup):
    confirming = State()


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
    "ProductFlow",
    "SettingsFlow",
    "WellbeingFlow",
]
