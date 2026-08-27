"""Формулировки записи еды: моноширинный блок и счётчик приёмов за день."""

from __future__ import annotations

from datetime import UTC, datetime

from src.analytics.body import day_balance
from src.reporting import (
    format_day_progress,
    format_day_totals,
    format_meal_saved,
    format_meals_today,
)
from src.vision.schemas import ItemDraft, MealDraft

EATEN = datetime(2026, 8, 25, 14, 12, tzinfo=UTC)


def _drumstick() -> MealDraft:
    return MealDraft(
        title="Куриная голень",
        items=[
            ItemDraft(
                name="куриная голень",
                portion_g=100,
                kcal=160,
                protein_g=18,
                fat_g=9,
                carbs_g=1,
                fiber_g=0,
            )
        ],
    )


def test_the_saved_meal_is_one_copyable_monospace_block():
    text = format_meal_saved(_drumstick(), title="Куриная голень", eaten_at=EATEN)
    assert text.startswith("✅ Записано: 14:12 <code>Куриная голень\n")
    assert "160 ккал · Б 18 · Ж 9 · У 1 · клетчатка 0 г</code>" in text
    # название и числа — один блок: копируется одним касанием
    assert text.count("<code>") == 1 and text.count("</code>") == 1
    assert "пришлите сахар" in text


def test_a_meal_without_numbers_still_names_the_dish():
    draft = MealDraft(title="Обед", items=[ItemDraft(name="обед")])
    text = format_meal_saved(draft, title="Обед", eaten_at=EATEN)
    assert "<code>Обед</code>" in text
    assert "ккал" not in text


def test_the_dish_name_cannot_break_the_markup():
    draft = MealDraft(title="<b>каша</b>", items=[ItemDraft(name="каша")])
    text = format_meal_saved(draft, title="<b>каша</b>", eaten_at=EATEN)
    assert "&lt;b&gt;каша&lt;/b&gt;" in text


def test_the_shortcut_line_appears_only_when_the_dish_landed_in_the_dictionary():
    without = format_meal_saved(_drumstick(), title="Куриная голень", eaten_at=EATEN)
    with_shortcut = format_meal_saved(
        _drumstick(), title="Куриная голень", eaten_at=EATEN, shortcut=True
    )
    assert "личном словаре" not in without
    assert "личном словаре" in with_shortcut


def test_the_day_summary_says_how_many_meals_there_were():
    assert format_meals_today(0, 3) == ""
    line = format_meals_today(2, 3)
    assert line == "🍽 Приёмов пищи: 2 из 3"

    balance = day_balance(target_kcal=1490, consumed_kcal=1050, burned_kcal=0, carbs_g=69)
    assert line in format_day_progress(balance, meals=line)
    assert line in format_day_totals(balance, meals=line)
