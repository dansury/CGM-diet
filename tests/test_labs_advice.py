"""Анализы → продукты-источники: только относительно референса из документа."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.analytics import labs
from src.reporting import format_lab_review

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def value(marker: str, **kwargs) -> labs.LabValue:
    kwargs.setdefault("taken_at", NOW)
    return labs.LabValue(marker=marker, **kwargs)


def test_markers_map_to_nutrients_by_name():
    assert labs.match_nutrient("Ферритин").key == "iron"
    assert labs.match_nutrient("Витамин B12 (цианокобаламин)").key == "b12"
    assert labs.match_nutrient("25(OH)D").key == "vitamin_d"
    assert labs.match_nutrient("Ширина распределения тромбоцитов") is None


def test_the_side_of_the_reference_comes_from_the_document():
    assert labs.direction(value("ферритин", value=8.0, ref_low=13.0, ref_high=150.0)) == "low"
    assert labs.direction(value("ферритин", value=200.0, ref_low=13.0, ref_high=150.0)) == "high"
    assert labs.direction(value("ферритин", value=50.0, ref_low=13.0, ref_high=150.0)) is None
    # без референса решает только флаг из распознавания
    assert labs.direction(value("ферритин", value=8.0)) is None
    assert labs.direction(value("ферритин", value=8.0, flag="low")) == "low"


def test_only_the_latest_panel_counts():
    old = value("Ферритин", value=8.0, ref_low=13.0, taken_at=NOW - timedelta(days=90))
    new = value("ферритин", value=40.0, ref_low=13.0, ref_high=150.0)
    assert labs.latest_values([old, new]) == [new]
    assert labs.review([old, new]).hints == []


def test_a_low_marker_yields_food_sources():
    review = labs.review([value("Ферритин", value=8.0, unit="нг/мл", ref_low=13.0, ref_high=150.0)])
    assert [hint.nutrient.key for hint in review.hints] == ["iron"]
    assert "чечевица" in review.hints[0].foods


def test_a_high_marker_yields_nothing_unless_food_is_the_known_lever():
    high_b12 = labs.review([value("Витамин B12", value=1200.0, ref_low=200.0, ref_high=900.0)])
    assert high_b12.hints == []
    assert len(high_b12.out_of_range) == 1

    high_ldl = labs.review([value("ЛПНП", value=5.1, ref_low=0.0, ref_high=3.0)])
    assert [hint.nutrient.key for hint in high_ldl.hints] == ["fiber"]


def test_the_text_never_diagnoses_and_never_prescribes():
    review = labs.review(
        [
            value("Ферритин", value=8.0, unit="нг/мл", ref_low=13.0, ref_high=150.0),
            value("Глюкоза", value=5.2, ref_low=3.9, ref_high=6.1),
        ]
    )
    text = format_lab_review(review)
    assert "Ферритин" in text
    assert "чечевица" in text
    assert "врачу" in text
    # маркер в норме в перечень «вне референса» не попадает
    assert "Глюкоза" not in text
    for forbidden in ("дефицит", "анемия", "принимайте", "назначаю", "препарат"):
        assert forbidden not in text.lower()


def test_no_labs_at_all_is_an_invitation_not_an_error():
    assert "Анализов пока нет" in format_lab_review(labs.review([]))
