"""`/export` — every row the bot holds about the user, as a ZIP of CSVs.

Export is the user's copy of their own data (and the honest counterpart to
`/delete`): raw rows, not summaries, so the file opens in Excel or feeds a
doctor's spreadsheet. See `spec/bot.md` § Export.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ActivitySample,
    AnalysisResult,
    DictionaryEntry,
    GlucoseReading,
    Meal,
    MealItem,
    Medication,
    Product,
    Symptom,
    User,
    Weight,
    WellbeingCheckin,
)

_TABLES: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    (
        "meals",
        Meal,
        ("id", "eaten_at", "source", "title", "kcal", "protein_g", "fat_g", "carbs_g",
         "fiber_g", "confidence", "confirmed", "note"),
    ),
    (
        "meal_items",
        MealItem,
        ("id", "meal_id", "name", "name_norm", "portion_g", "kcal", "protein_g", "fat_g",
         "carbs_g", "fiber_g", "tags"),
    ),
    (
        "glucose_readings",
        GlucoseReading,
        ("id", "measured_at", "value_mmol", "unit_input", "source", "device", "trend"),
    ),
    ("weights", Weight, ("id", "measured_at", "weight_kg", "note")),
    (
        "medications",
        Medication,
        ("id", "taken_at", "name", "slug", "cid", "dose_text", "form", "source", "note"),
    ),
    (
        "user_dictionary",
        DictionaryEntry,
        ("id", "kind", "key_norm", "label", "hits", "pinned", "is_active", "last_used_at"),
    ),
    (
        "analysis_results",
        AnalysisResult,
        ("id", "taken_at", "panel", "marker", "value", "value_text", "unit", "ref_low",
         "ref_high", "flag"),
    ),
    (
        "wellbeing_checkins",
        WellbeingCheckin,
        ("id", "at", "score", "note", "source"),
    ),
    (
        "products",
        Product,
        ("id", "brand", "name", "barcode", "kcal_100", "protein_100", "fat_100", "carbs_100",
         "sugars_100", "fiber_100", "ingredients_text", "flags"),
    ),
    (
        "activity_samples",
        ActivitySample,
        ("id", "kind", "start_at", "end_at", "steps", "distance_m", "kcal", "avg_hr", "source"),
    ),
    ("symptoms", Symptom, ("id", "slug", "label", "hits", "last_used_at")),
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return "|".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return str(value)


def rows_to_csv(header: Sequence[str], rows: list[Sequence[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return buffer.getvalue()


async def build_export(session: AsyncSession, user: User) -> bytes:
    """ZIP archive with one CSV per table, UTF-8 with BOM for Excel."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, model, columns in _TABLES:
            if name == "meal_items":
                meal_ids = select(Meal.id).where(Meal.user_id == user.id)
                stmt = select(model).where(model.meal_id.in_(meal_ids))
            else:
                stmt = select(model).where(model.user_id == user.id)
            records = list(await session.scalars(stmt))
            rows = [[getattr(r, column, None) for column in columns] for r in records]
            zf.writestr(f"{name}.csv", "﻿" + rows_to_csv(columns, rows))
        zf.writestr(
            "README.txt",
            "Экспорт данных CGM-diet.\n"
            "Разделитель — точка с запятой, кодировка UTF-8 с BOM.\n"
            "Глюкоза хранится в ммоль/л (value_mmol); unit_input — единицы ввода.\n"
            "Все даты в ISO-8601.\n",
        )
    return archive.getvalue()


__all__ = ["build_export", "rows_to_csv"]
