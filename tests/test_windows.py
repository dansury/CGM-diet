"""Postprandial window math — baseline, peak, delta, iAUC, contamination."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analytics.windows import (
    GlucosePoint,
    MealLike,
    build_excursions,
    compute_baseline,
    compute_excursion,
)

T0 = datetime(2026, 8, 24, 8, 0)


def series(pairs: list[tuple[int, float]]) -> list[GlucosePoint]:
    return [GlucosePoint(at=T0 + timedelta(minutes=m), value=v) for m, v in pairs]


CURVE = series(
    [(-20, 5.2), (-5, 5.4), (15, 6.8), (30, 8.1), (50, 9.2), (70, 8.4), (95, 7.0), (120, 6.1)]
)


def test_baseline_is_the_pre_meal_mean():
    assert compute_baseline(CURVE, T0, baseline_window=20) == pytest.approx(5.3)


def test_baseline_falls_back_to_the_nearest_reading():
    sparse = series([(-28, 5.0)])
    assert compute_baseline(sparse, T0, baseline_window=20) == 5.0


def test_baseline_is_none_when_nothing_is_close():
    assert compute_baseline(series([(-200, 5.0)]), T0) is None


def test_delta_is_peak_minus_baseline_inside_the_window():
    meal = MealLike(id=1, eaten_at=T0)
    ex = compute_excursion(meal, CURVE, window=(45, 90), window_name="1h")
    assert ex.baseline == pytest.approx(5.3)
    assert ex.peak == pytest.approx(9.2)
    assert ex.delta == pytest.approx(3.9)
    assert ex.n_points == 2


def test_two_hour_window_sees_the_later_points():
    meal = MealLike(id=1, eaten_at=T0)
    ex = compute_excursion(meal, CURVE, window=(90, 150), window_name="2h")
    assert ex.peak == pytest.approx(7.0)
    assert ex.delta == pytest.approx(1.7)


def test_window_bounds_are_inclusive():
    curve = series([(-10, 5.0), (45, 8.0), (90, 6.0)])
    ex = compute_excursion(MealLike(id=1, eaten_at=T0), curve, window=(45, 90))
    assert ex.n_points == 2


def test_no_readings_in_window_means_unusable():
    curve = series([(-10, 5.0), (200, 9.0)])
    ex = compute_excursion(MealLike(id=1, eaten_at=T0), curve, window=(45, 90))
    assert ex.delta is None
    assert not ex.usable


def test_iauc_counts_only_the_area_above_baseline():
    flat = series([(-10, 5.0), (30, 5.0), (60, 5.0)])
    ex = compute_excursion(MealLike(id=1, eaten_at=T0), flat, window=(45, 90))
    assert ex.iauc == 0.0


def test_iauc_is_positive_for_a_rise():
    meal = MealLike(id=1, eaten_at=T0)
    ex = compute_excursion(meal, CURVE, window=(45, 90))
    assert ex.iauc > 0


def test_a_second_meal_inside_the_window_contaminates_the_excursion():
    meal = MealLike(id=1, eaten_at=T0)
    ex = compute_excursion(
        meal, CURVE, window=(45, 90), other_meals=[T0, T0 + timedelta(minutes=40)]
    )
    assert ex.contaminated
    assert not ex.usable


def test_build_excursions_returns_both_windows():
    result = build_excursions([MealLike(id=1, eaten_at=T0)], CURVE)
    assert set(result) == {"1h", "2h"}
    assert result["1h"][0].window == "1h"
