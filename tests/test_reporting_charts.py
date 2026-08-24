"""User-facing text and chart rendering."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.analytics.stats import KeyStats
from src.analytics.windows import GlucosePoint
from src.charts.render import render_ranking, render_timeline, render_wellbeing
from src.reporting import (
    format_meal_draft,
    format_product,
    format_product_verdict,
    format_recommendations,
    format_stats,
)
from src.vision.schemas import ItemDraft, MealDraft, ProductDraft

T0 = datetime(2026, 8, 24, 8, 0)

STAT = KeyStats(
    key="added_sugar",
    key_type="tag",
    window="1h",
    n=9,
    mean_delta=3.2,
    median_delta=3.0,
    max_delta=5.1,
    ci_low=2.4,
    ci_high=4.0,
    mean_without=0.9,
    contrast=2.3,
    p_value=0.004,
    confidence="high",
)


def test_meal_draft_shows_totals_and_asks_for_confirmation():
    draft = MealDraft(
        title="Обед",
        items=[ItemDraft(name="рис", portion_g=150, kcal=200, carbs_g=45)],
        confidence=0.8,
    )
    text = format_meal_draft(draft, eaten_at=T0)
    assert "Обед" in text and "рис" in text
    assert "200 ккал" in text
    assert "Всё верно?" in text


def test_stats_use_the_russian_tag_label_not_the_slug():
    text = format_stats([STAT])
    assert "добавленный сахар" in text
    assert "added_sugar" not in text


def test_stats_explain_the_absence_of_data():
    assert "недостаточно данных" in format_stats([]).lower()


def test_recommendations_require_actionable_evidence():
    weak = KeyStats(
        key="fruit", key_type="tag", window="1h", n=4, mean_delta=0.4,
        median_delta=0.4, max_delta=0.6, confidence="low",
    )
    assert "нет компонентов" in format_recommendations([weak])
    assert "Сократить" in format_recommendations([STAT])


def test_product_verdict_grounds_the_answer_in_history():
    draft = ProductDraft(name="Йогурт", sugars_100=11.9, flags=["added_sugar"])
    text = format_product_verdict(draft, [STAT], "mmol/L")
    assert "наблюдалось 9 раз" in text
    assert "заменить" in text


def test_product_verdict_without_history_says_so():
    draft = ProductDraft(name="Новинка", flags=["added_sugar"])
    text = format_product_verdict(draft, [], "mmol/L")
    assert "ещё не было" in text


def test_product_card_lists_sugars_and_flags():
    draft = ProductDraft(name="Йогурт", brand="Дымов", sugars_100=11.9, flags=["added_sugar"])
    text = format_product(draft, mode="check")
    assert "Дымов" in text and "11.9" in text and "добавленный сахар" in text


def _png_header(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def test_timeline_chart_renders_png():
    points = [GlucosePoint(at=T0 + timedelta(minutes=15 * i), value=5 + i % 5) for i in range(20)]
    png = render_timeline(points, [(T0 + timedelta(hours=1), "Овсянка")], checkins=[(T0, 3)])
    assert _png_header(png) and len(png) > 5000


def test_ranking_chart_renders_even_with_no_data():
    assert _png_header(render_ranking([]))
    assert _png_header(render_ranking([STAT]))


def test_wellbeing_chart_renders_with_symptom_markers():
    png = render_wellbeing(
        [(T0 + timedelta(hours=i), 3) for i in range(4)],
        symptom_series={"сонливость": [(T0 + timedelta(hours=2), 1)]},
    )
    assert _png_header(png)
