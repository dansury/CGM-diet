"""Corrections merge into a recognition; they never replace it."""

from __future__ import annotations

import pytest

from src.ingest.correction import apply_meal_correction
from src.vision.schemas import ItemDraft, MealDraft


def draft() -> MealDraft:
    return MealDraft(
        title="Гречка с курицей и салатом",
        items=[
            ItemDraft(name="гречневая каша", portion_g=180, kcal=200, carbs_g=38),
            ItemDraft(name="куриная грудка", portion_g=120, kcal=198, protein_g=37),
            ItemDraft(name="салат из огурцов", portion_g=150, kcal=45),
        ],
        confidence=0.7,
    )


def names(result) -> list[str]:
    return [item.name for item in result.draft.items]


def test_untouched_items_survive_a_correction():
    result = apply_meal_correction(draft(), "убери салат")
    assert names(result) == ["гречневая каша", "куриная грудка"]
    kept = result.draft.items[1]
    assert kept.portion_g == 120 and kept.kcal == 198  # numbers not re-derived


def test_portion_change_rescales_only_that_item():
    result = apply_meal_correction(draft(), "гречки было 250")
    buckwheat = result.draft.items[0]
    assert buckwheat.portion_g == 250
    assert buckwheat.kcal == pytest.approx(277.8, abs=0.1)
    assert result.draft.items[1].kcal == 198


def test_declension_still_finds_the_item():
    # «гречки» / «гречневая» share a stem but no substring
    result = apply_meal_correction(draft(), "гречки 250")
    assert result.changes and result.changes[0].kind == "portion"


def test_rename_keeps_the_portion_and_the_rest_of_the_meal():
    result = apply_meal_correction(draft(), "это была не гречка, а перловка")
    assert names(result) == ["перловка", "куриная грудка", "салат из огурцов"]
    assert result.draft.items[0].portion_g == 180


def test_several_clauses_apply_together():
    result = apply_meal_correction(draft(), "убери салат, гречки было 250, добавь хлеб 50")
    assert names(result) == ["гречневая каша", "куриная грудка", "хлеб"]
    assert len(result.changes) == 3


def test_unknown_wording_is_reported_not_swallowed():
    result = apply_meal_correction(draft(), "там всё было немного другое")
    assert not result.changes
    assert result.unmatched  # escalated to the model by the handler


def test_bare_weight_applies_only_to_a_single_item_meal():
    single = MealDraft(items=[ItemDraft(name="овсянка", portion_g=200, kcal=150)])
    assert apply_meal_correction(single, "250 г").draft.items[0].portion_g == 250
    assert not apply_meal_correction(draft(), "250 г").changes  # ambiguous → untouched
