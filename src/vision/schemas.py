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
    estimated: bool = False   # numbers came from the reference table, not the model


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
class MedicationDraft:
    """One dose the user is about to log. The bot never proposes a dose."""

    name: str = ""
    inn: str | None = None          # international nonproprietary name, if read
    dose_text: str | None = None    # «850 мг», «1 таблетка» — as printed/said
    form: str | None = None         # таблетки|капсулы|раствор…
    note: str | None = None
    confidence: float | None = None
    raw_text: str | None = None


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
    "MedicationDraft",
    "ProductDraft",
]


# --------------------------------------------------------------- (de)serialisation
# Drafts live in the FSM store between the recognition message and the user's
# tap, so they must survive a JSON round-trip.

def meal_to_dict(draft: MealDraft) -> dict:
    return {
        "title": draft.title,
        "confidence": draft.confidence,
        "notes": draft.notes,
        "source": draft.source,
        "raw_text": draft.raw_text,
        "items": [
            {
                "name": i.name,
                "portion_g": i.portion_g,
                "kcal": i.kcal,
                "protein_g": i.protein_g,
                "fat_g": i.fat_g,
                "carbs_g": i.carbs_g,
                "fiber_g": i.fiber_g,
                "tags": i.tags,
                "estimated": i.estimated,
            }
            for i in draft.items
        ],
    }


def meal_from_dict(data: dict) -> MealDraft:
    return MealDraft(
        title=data.get("title") or "",
        confidence=data.get("confidence"),
        notes=data.get("notes") or "",
        source=data.get("source") or "photo",
        raw_text=data.get("raw_text"),
        items=[ItemDraft(**item) for item in data.get("items") or []],
    )


def product_to_dict(draft: ProductDraft) -> dict:
    return {
        "name": draft.name,
        "brand": draft.brand,
        "barcode": draft.barcode,
        "kcal_100": draft.kcal_100,
        "protein_100": draft.protein_100,
        "fat_100": draft.fat_100,
        "carbs_100": draft.carbs_100,
        "sugars_100": draft.sugars_100,
        "fiber_100": draft.fiber_100,
        "ingredients": draft.ingredients,
        "additives": draft.additives,
        "flags": draft.flags,
        "confidence": draft.confidence,
    }


def product_from_dict(data: dict) -> ProductDraft:
    return ProductDraft(**data)


def med_to_dict(draft: MedicationDraft) -> dict:
    return {
        "name": draft.name,
        "inn": draft.inn,
        "dose_text": draft.dose_text,
        "form": draft.form,
        "note": draft.note,
        "confidence": draft.confidence,
        "raw_text": draft.raw_text,
    }


def med_from_dict(data: dict) -> MedicationDraft:
    return MedicationDraft(**data)


def glucose_to_dicts(drafts: list[GlucoseDraft]) -> list[dict]:
    return [
        {
            "measured_at": d.measured_at.isoformat(),
            "value_mmol": d.value_mmol,
            "unit_input": d.unit_input,
            "trend": d.trend,
            "device": d.device,
        }
        for d in drafts
    ]


def glucose_from_dicts(data: list[dict]) -> list[GlucoseDraft]:
    return [
        GlucoseDraft(
            measured_at=datetime.fromisoformat(d["measured_at"]),
            value_mmol=d["value_mmol"],
            unit_input=d.get("unit_input") or "mmol/L",
            trend=d.get("trend"),
            device=d.get("device"),
        )
        for d in data
    ]


def lab_to_dict(draft: LabDraft) -> dict:
    return {
        "panel": draft.panel,
        "taken_at": draft.taken_at.isoformat() if draft.taken_at else None,
        "confidence": draft.confidence,
        "markers": [
            {
                "marker": m.marker,
                "value": m.value,
                "value_text": m.value_text,
                "unit": m.unit,
                "ref_low": m.ref_low,
                "ref_high": m.ref_high,
            }
            for m in draft.markers
        ],
    }


def lab_from_dict(data: dict) -> LabDraft:
    return LabDraft(
        panel=data.get("panel"),
        taken_at=datetime.fromisoformat(data["taken_at"]) if data.get("taken_at") else None,
        confidence=data.get("confidence"),
        markers=[MarkerDraft(**m) for m in data.get("markers") or []],
    )


__all__ += [
    "glucose_from_dicts",
    "glucose_to_dicts",
    "lab_from_dict",
    "lab_to_dict",
    "meal_from_dict",
    "meal_to_dict",
    "med_from_dict",
    "med_to_dict",
    "product_from_dict",
    "product_to_dict",
]
