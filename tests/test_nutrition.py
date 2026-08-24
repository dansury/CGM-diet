"""Backfill of missing macros. See `spec/ingest.md` § Нутриенты."""

from __future__ import annotations

import pytest

from src.ingest import nutrition
from src.ingest.correction import apply_meal_correction
from src.llm.mock import FIXTURES, MockClient
from src.reporting import format_meal_draft
from src.vision import recognize
from src.vision.schemas import ItemDraft, MealDraft, meal_from_dict, meal_to_dict


def test_lookup_prefers_the_longest_keyword():
    assert nutrition.lookup("куриная грудка отварная").protein_g == pytest.approx(31.0)
    assert nutrition.lookup("курица тушёная").protein_g == pytest.approx(25.0)
    assert nutrition.lookup("что-то неведомое") is None


def test_bare_item_gets_portion_and_macros():
    item = ItemDraft(name="Наггетсы")
    assert nutrition.fill_item(item) is True
    assert item.portion_g == pytest.approx(150)
    assert item.protein_g == pytest.approx(22.5)
    assert item.fat_g == pytest.approx(27.0)
    assert item.carbs_g == pytest.approx(24.0)
    assert item.kcal > 0
    assert item.estimated is True


def test_known_numbers_are_never_overwritten():
    item = ItemDraft(name="наггетсы", portion_g=200, protein_g=10.0, kcal=300.0)
    nutrition.fill_item(item)
    assert item.protein_g == pytest.approx(10.0)   # слово модели сильнее таблицы
    assert item.kcal == pytest.approx(300.0)
    assert item.portion_g == pytest.approx(200)
    assert item.fat_g == pytest.approx(36.0)       # 18 г/100 г × 200 г


def test_kcal_recovered_from_macros_without_a_table_match():
    item = ItemDraft(name="блюдо шефа", protein_g=10.0, fat_g=5.0, carbs_g=20.0)
    assert nutrition.fill_item(item) is True
    assert item.kcal == pytest.approx(165)         # 4·10 + 9·5 + 4·20


def test_unknown_item_stays_empty_rather_than_zero():
    item = ItemDraft(name="нечто неопознанное")
    assert nutrition.fill_item(item) is False
    assert item.kcal is None
    assert item.estimated is False


async def test_text_meal_with_null_macros_is_backfilled():
    client = MockClient(overrides={"text_meal": FIXTURES["text_meal_bare"]})
    draft = await recognize.parse_meal_text("наггетсы", client=client)
    totals = draft.totals()
    assert totals["kcal"] > 0
    assert totals["protein_g"] > 0 and totals["fat_g"] > 0 and totals["carbs_g"] > 0
    assert nutrition.ESTIMATE_NOTE in draft.notes


def test_card_marks_estimates_and_never_prints_a_zero_total():
    draft = MealDraft(title="Наггетсы", items=[ItemDraft(name="Наггетсы")], confidence=0.8)
    nutrition.fill_meal(draft)
    text = format_meal_draft(draft)
    assert "≈" in text
    assert "Итого: 0 ккал" not in text

    empty = MealDraft(title="Нечто", items=[ItemDraft(name="нечто неопознанное")])
    assert "оценить не удалось" in format_meal_draft(empty)


def test_correction_add_gets_numbers():
    draft = MealDraft(title="Обед", items=[ItemDraft(name="наггетсы", portion_g=150, kcal=435.0)])
    result = apply_meal_correction(draft, "добавь салат 100")
    added = next(i for i in result.draft.items if "салат" in i.name)
    assert added.kcal and added.carbs_g
    assert result.draft.totals()["kcal"] > 435


def test_estimated_flag_survives_the_fsm_round_trip():
    draft = MealDraft(items=[ItemDraft(name="наггетсы")])
    nutrition.fill_meal(draft)
    restored = meal_from_dict(meal_to_dict(draft))
    assert restored.items[0].estimated is True
