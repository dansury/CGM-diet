"""Component statistics: aggregation, contrast, confidence grading."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analytics.stats import (
    MEANINGFUL_RISE,
    aggregate,
    grade_confidence,
    mann_whitney_p,
    mean_ci,
)
from src.analytics.windows import Excursion, MealLike

T0 = datetime(2026, 8, 24, 8, 0)


def test_mean_ci_matches_the_t_interval():
    mean, sd, low, high = mean_ci([3.9, 3.2, 4.1, 3.5, 3.8])
    assert mean == pytest.approx(3.7, abs=0.01)
    assert low < mean < high
    assert sd == pytest.approx(0.35, abs=0.01)


def test_mean_ci_of_a_single_value_has_no_interval():
    mean, sd, low, high = mean_ci([4.2])
    assert (mean, sd, low, high) == (4.2, None, None, None)


def test_mann_whitney_separates_clearly_different_groups():
    p = mann_whitney_p([3.9, 3.2, 4.1, 3.5, 3.8], [0.8, 1.1, 0.9, 1.4, 1.0])
    assert p is not None and p < 0.05


def test_mann_whitney_is_none_for_tiny_groups():
    assert mann_whitney_p([1.0, 2.0], [3.0, 4.0]) is None


def test_identical_groups_are_not_significant():
    p = mann_whitney_p([2.0] * 5, [2.0] * 5)
    assert p is None or p > 0.05


@pytest.mark.parametrize(
    ("n", "ci_low", "mean", "p", "expected"),
    [
        (2, 1.0, 3.0, 0.01, "low"),      # not enough observations
        (5, 0.4, 2.0, 0.20, "medium"),   # CI excludes zero
        (5, -0.2, 2.0, 0.02, "medium"),  # significant contrast
        (9, 1.2, 3.5, 0.008, "high"),    # everything lines up
        (9, 1.2, 0.4, 0.008, "medium"),  # significant but clinically small
        (4, -0.5, 0.3, 0.9, "low"),
    ],
)
def test_confidence_grading(n, ci_low, mean, p, expected):
    assert grade_confidence(n=n, ci_low=ci_low, mean_delta=mean, p_value=p) == expected


def _make(deltas_with_tag: list[float], deltas_without: list[float]):
    meals: list[MealLike] = []
    excursions: list[Excursion] = []
    index = 0
    for delta in deltas_with_tag:
        index += 1
        meals.append(MealLike(id=index, eaten_at=T0 + timedelta(hours=index), tags=["added_sugar"]))
        excursions.append(
            Excursion(
                meal_id=index,
                eaten_at=T0 + timedelta(hours=index),
                window="1h",
                baseline=5.0,
                peak=5.0 + delta,
                delta=delta,
            )
        )
    for delta in deltas_without:
        index += 1
        meals.append(MealLike(id=index, eaten_at=T0 + timedelta(hours=index), tags=["vegetable"]))
        excursions.append(
            Excursion(
                meal_id=index,
                eaten_at=T0 + timedelta(hours=index),
                window="1h",
                baseline=5.0,
                peak=5.0 + delta,
                delta=delta,
            )
        )
    return meals, excursions


def test_aggregate_scores_a_clear_offender_as_high():
    meals, excursions = _make(
        [3.9, 3.2, 4.1, 3.5, 3.8, 4.4, 3.1, 3.6, 4.0],
        [0.8, 1.1, 0.9, 1.4, 1.0, 0.7, 1.2, 0.6],
    )
    stats = {s.key: s for s in aggregate(meals, excursions, key_type="tag")}
    sugar = stats["added_sugar"]
    assert sugar.n == 9
    assert sugar.mean_delta > MEANINGFUL_RISE
    assert sugar.mean_without == pytest.approx(0.96, abs=0.02)
    assert sugar.contrast > 2.0
    assert sugar.confidence == "high"
    assert sugar.actionable


def test_aggregate_drops_keys_below_the_observation_floor():
    meals, excursions = _make([3.0, 3.1], [1.0, 1.1, 0.9, 1.2])
    assert not [s for s in aggregate(meals, excursions, key_type="tag") if s.key == "added_sugar"]


def test_aggregate_ignores_contaminated_and_empty_excursions():
    """A contaminated or reading-less excursion must not reach the numbers."""
    meals, excursions = _make([3.0, 3.1, 3.2, 3.3, 9.9], [1.0, 1.1, 0.9])
    excursions[4].contaminated = True  # the 9.9 outlier is a double meal
    sugar = next(s for s in aggregate(meals, excursions, key_type="tag") if s.key == "added_sugar")
    assert sugar.n == 4
    assert sugar.max_delta == pytest.approx(3.3)

    meals, excursions = _make([3.0, 3.1, 3.2, 9.9], [1.0, 1.1, 0.9])
    excursions[3].delta = None  # no reading landed inside the window
    sugar = next(s for s in aggregate(meals, excursions, key_type="tag") if s.key == "added_sugar")
    assert sugar.n == 3


def test_aggregate_returns_nothing_without_usable_data():
    assert aggregate([], [], key_type="tag") == []


def test_results_are_sorted_by_mean_delta():
    meals, excursions = _make([4.0] * 5, [1.0] * 5)
    meals.append(MealLike(id=99, eaten_at=T0, tags=["fruit"]))
    stats = aggregate(meals, excursions, key_type="tag")
    assert stats == sorted(stats, key=lambda s: (-s.mean_delta, -s.n))
