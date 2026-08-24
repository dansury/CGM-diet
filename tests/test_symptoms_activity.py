"""Wellbeing↔glucose contrast and the food↔activity contrast."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analytics.activity import ActivityBucket, contrast_by_activity, steps_after
from src.analytics.symptoms import (
    CheckinLike,
    aggregate_symptoms,
    build_context,
    nearest_glucose,
)
from src.analytics.windows import Excursion, GlucosePoint, MealLike

T0 = datetime(2026, 8, 24, 8, 0)


def test_nearest_glucose_respects_the_match_window():
    points = [GlucosePoint(at=T0, value=6.0)]
    assert nearest_glucose(points, T0 + timedelta(minutes=10)) == 6.0
    assert nearest_glucose(points, T0 + timedelta(minutes=90)) is None


def test_context_links_a_checkin_to_the_preceding_meal():
    points = [GlucosePoint(at=T0 + timedelta(minutes=60), value=9.1)]
    contexts = build_context(
        [CheckinLike(at=T0 + timedelta(minutes=60), score=2, symptoms=["сонливость"])],
        points,
        [(T0, ["added_sugar"])],
    )
    assert contexts[0].minutes_since_meal == 60
    assert contexts[0].meal_tags == ["added_sugar"]
    assert contexts[0].glucose == 9.1


def test_context_ignores_a_meal_too_long_ago():
    contexts = build_context(
        [CheckinLike(at=T0 + timedelta(hours=6), score=3, symptoms=[])], [], [(T0, ["fruit"])]
    )
    assert contexts[0].minutes_since_meal is None


def test_symptom_contrast_surfaces_higher_glucose():
    checkins, points = [], []
    for i in range(6):
        at = T0 + timedelta(hours=i)
        checkins.append(CheckinLike(at=at, score=2, symptoms=["сонливость"]))
        points.append(GlucosePoint(at=at, value=10.0 + i * 0.1))
    for i in range(6):
        at = T0 + timedelta(hours=12 + i)
        checkins.append(CheckinLike(at=at, score=5, symptoms=[]))
        points.append(GlucosePoint(at=at, value=5.5 + i * 0.1))
    stats = aggregate_symptoms(build_context(checkins, points))
    sleepiness = next(s for s in stats if s.symptom == "сонливость")
    assert sleepiness.n == 6
    assert sleepiness.contrast > 3.0
    assert sleepiness.p_value < 0.05
    assert sleepiness.share_postprandial == 0.0


def test_rare_symptoms_are_not_reported():
    contexts = build_context(
        [CheckinLike(at=T0, score=2, symptoms=["редкость"])],
        [GlucosePoint(at=T0, value=8.0)],
    )
    assert aggregate_symptoms(contexts) == []


def test_steps_after_prorates_partial_buckets():
    buckets = [
        ActivityBucket(start_at=T0 - timedelta(minutes=10), end_at=T0 + timedelta(minutes=5), steps=150),
        ActivityBucket(start_at=T0 + timedelta(minutes=5), end_at=T0 + timedelta(minutes=20), steps=600),
    ]
    # 5 of 15 minutes from the first bucket (50) + all of the second (600)
    assert steps_after(buckets, T0) == 650


def test_activity_contrast_shows_a_smaller_rise_after_walking():
    meals, excursions, buckets = [], [], []
    for i in range(10):
        at = T0 + timedelta(hours=i)
        walked = i % 2 == 0
        meals.append(MealLike(id=i + 1, eaten_at=at))
        excursions.append(
            Excursion(
                meal_id=i + 1,
                eaten_at=at,
                window="1h",
                baseline=5.0,
                delta=1.2 if walked else 3.4,
            )
        )
        buckets.append(
            ActivityBucket(
                start_at=at, end_at=at + timedelta(minutes=60), steps=2500 if walked else 80
            )
        )
    contrast = contrast_by_activity(meals, excursions, buckets)
    assert contrast.meaningful
    assert contrast.n_active == 5 and contrast.n_sedentary == 5
    assert contrast.difference == pytest.approx(2.2, abs=0.01)
    assert contrast.p_value < 0.05


def test_activity_contrast_without_data_is_not_meaningful():
    assert not contrast_by_activity([], [], []).meaningful
