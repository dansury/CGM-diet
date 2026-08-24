"""Draft objects produced by recognition, before the user confirms them.

Nothing here touches the DB: a draft is what the model *proposed*. It becomes a
row only after the user taps “Верно” or edits it (see `spec/ingest.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ItemDraft:
    name: str
    portion_g: float | None = None
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None
    fiber_g: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MealDraft:
    title: str = ""
    items: list[ItemDraft] = field(default_factory=list)
    confidence: float | None = None
    notes: str = ""
    source: str = "photo"
    raw_text: str | None = None

    def totals(self) -> dict[str, float]:
        keys = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")
        return {k: round(sum(getattr(i, k) or 0.0 for i in self.items), 1) for k in keys}


@dataclass(slots=True)
class ProductDraft:
    name: str = ""
    brand: str | None = None
    barcode: str | None = None
    kcal_100: float | None = None
    protein_100: float | None = None
    fat_100: float | None = None
    carbs_100: float | None = None
    sugars_100: float | None = None
    fiber_100: float | None = None
    ingredients: list[str] = field(default_factory=list)
    additives: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class GlucoseDraft:
    measured_at: datetime
    value_mmol: float
    unit_input: str = "mmol/L"
    trend: str | None = None
    device: str | None = None


@dataclass(slots=True)
class MarkerDraft:
    marker: str
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None

    @property
    def flag(self) -> str | None:
        if self.value is None:
            return None
        if self.ref_low is not None and self.value < self.ref_low:
            return "low"
        if self.ref_high is not None and self.value > self.ref_high:
            return "high"
        if self.ref_low is None and self.ref_high is None:
            return None
        return "normal"


@dataclass(slots=True)
class LabDraft:
    panel: str | None = None
    taken_at: datetime | None = None
    markers: list[MarkerDraft] = field(default_factory=list)
    confidence: float | None = None


__all__ = [
    "GlucoseDraft",
    "ItemDraft",
    "LabDraft",
    "MarkerDraft",
    "MealDraft",
    "ProductDraft",
]
