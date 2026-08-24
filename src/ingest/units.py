"""Glucose unit handling. Canonical storage unit is mmol/L.

`MG_DL_PER_MMOL = 18.0182` is the conversion factor for glucose specifically
(molar mass 180.16 g/mol). See `spec/analytics.md` § Units.
"""

from __future__ import annotations

MG_DL_PER_MMOL = 18.0182

MMOL = "mmol/L"
MGDL = "mg/dL"

_MMOL_ALIASES = {"mmol/l", "mmol", "ммоль/л", "ммоль", "ммоль/литр"}
_MGDL_ALIASES = {"mg/dl", "mgdl", "мг/дл", "мг%"}

# Plausible physiological ranges — anything outside is rejected as a misread.
MMOL_RANGE = (1.0, 33.3)
MGDL_RANGE = (18.0, 600.0)


def canonical_unit(unit: str | None) -> str:
    if not unit:
        return MMOL
    key = unit.strip().lower().replace(" ", "")
    if key in _MGDL_ALIASES:
        return MGDL
    if key in _MMOL_ALIASES:
        return MMOL
    return MMOL


def to_mmol(value: float, unit: str | None) -> float:
    return round(value / MG_DL_PER_MMOL, 2) if canonical_unit(unit) == MGDL else round(value, 2)


def to_mgdl(value_mmol: float) -> float:
    return round(value_mmol * MG_DL_PER_MMOL, 1)


def convert(value_mmol: float, unit: str) -> float:
    return to_mgdl(value_mmol) if canonical_unit(unit) == MGDL else round(value_mmol, 2)


def guess_unit(value: float) -> str:
    """Infer the unit from magnitude when the source did not say.

    mmol/L readings live in 1–33; mg/dL readings in 18–600. The overlap
    (18–33) is resolved in favour of mmol/L: a CGM screenshot showing “25”
    is far more likely mmol/L in a metric-market app, and the confirmation
    step lets the user flip it.
    """
    return MMOL if value <= MMOL_RANGE[1] else MGDL


def is_plausible(value: float, unit: str) -> bool:
    low, high = MGDL_RANGE if canonical_unit(unit) == MGDL else MMOL_RANGE
    return low <= value <= high


def format_value(value_mmol: float, unit: str = MMOL) -> str:
    if canonical_unit(unit) == MGDL:
        return f"{to_mgdl(value_mmol):.0f} мг/дл"
    return f"{value_mmol:.1f} ммоль/л"


def format_delta(delta_mmol: float, unit: str = MMOL) -> str:
    sign = "+" if delta_mmol >= 0 else "−"
    if canonical_unit(unit) == MGDL:
        return f"{sign}{abs(to_mgdl(delta_mmol)):.0f} мг/дл"
    return f"{sign}{abs(delta_mmol):.1f} ммоль/л"


__all__ = [
    "MGDL",
    "MG_DL_PER_MMOL",
    "MMOL",
    "canonical_unit",
    "convert",
    "format_delta",
    "format_value",
    "guess_unit",
    "is_plausible",
    "to_mgdl",
    "to_mmol",
]
