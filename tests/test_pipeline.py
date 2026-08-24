"""End-to-end: seeded history -> excursions -> statistics -> user-facing text.

This is the test that would catch a regression a user would actually notice:
the sugary meals must come out on top with a defensible confidence, and the
walk-after-lunch contrast must be visible.
"""

from __future__ import annotations

import pytest

from seeds.seed_demo import build_demo
from src.analytics.activity import contrast_by_activity
from src.analytics.cgm_metrics import summarize
from src.analytics.stats import aggregate
from src.analytics.symptoms import aggregate_symptoms, build_context
from src.analytics.windows import build_excursions
from src.db import repo
from src.export import build_export
from src.reporting import format_recommendations, format_stats

TG_ID = 777001


@pytest.fixture
async def seeded(session):
    await build_demo(session, TG_ID, days=14)
    user = await repo.get_user(session, TG_ID)
    return session, user


async def test_seed_produces_a_full_history(seeded):
    session, user = seeded
    totals = await repo.counts(session, user)
    assert totals["meals"] == 42
    assert totals["glucose"] > 200
    assert totals["activity"] == 42


async def test_sugary_components_rank_above_vegetables(seeded):
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    excursions = build_excursions(meals, points)
    stats = {s.key: s for s in aggregate(meals, excursions["1h"], key_type="tag")}

    assert "added_sugar" in stats and "vegetable" in stats
    assert stats["added_sugar"].mean_delta > stats["vegetable"].mean_delta
    assert stats["refined_flour"].mean_delta > stats["protein"].mean_delta


async def test_the_worst_component_reaches_a_reportable_confidence(seeded):
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    stats = aggregate(meals, build_excursions(meals, points)["1h"], key_type="tag")
    top = stats[0]
    assert top.confidence in {"medium", "high"}
    assert top.actionable
    assert top.p_value is not None and top.p_value < 0.05


async def test_two_hour_window_shows_a_smaller_rise_than_one_hour(seeded):
    """The seeded curve peaks around 65 min, so the later window must be lower."""
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    excursions = build_excursions(meals, points)
    one_hour = aggregate(meals, excursions["1h"], key_type="tag")
    two_hour = {s.key: s for s in aggregate(meals, excursions["2h"], key_type="tag")}
    top = one_hour[0]
    assert two_hour[top.key].mean_delta < top.mean_delta


async def test_per_dish_statistics_are_available_too(seeded):
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    stats = aggregate(meals, build_excursions(meals, points)["1h"], key_type="item")
    assert stats
    assert all(s.key_type == "item" for s in stats)


async def test_walking_after_a_meal_lowers_the_rise(seeded):
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    buckets = await repo.load_activity_buckets(session, user)
    contrast = contrast_by_activity(meals, build_excursions(meals, points)["1h"], buckets)
    assert contrast.meaningful
    assert contrast.difference > 0  # sitting still rises more


async def test_symptoms_line_up_with_higher_glucose(seeded):
    session, user = seeded
    points = await repo.load_points(session, user)
    contexts = build_context(await repo.load_checkin_likes(session, user), points)
    stats = aggregate_symptoms(contexts)
    assert stats
    assert stats[0].contrast > 0


async def test_cgm_summary_is_computable(seeded):
    session, user = seeded
    summary = summarize(await repo.load_points(session, user))
    assert summary.n > 200
    assert summary.tir is not None
    assert summary.gmi is not None


async def test_reports_mention_the_offender_and_stay_non_causal(seeded):
    session, user = seeded
    meals = await repo.load_meal_likes(session, user)
    points = await repo.load_points(session, user)
    stats = aggregate(meals, build_excursions(meals, points)["1h"], key_type="tag")
    text = format_stats(stats, window="1h")
    assert "средний подъём" in text
    assert "наблюдается связь" in text.lower()
    assert "повышает сахар" not in text  # no causal claim, ever
    advice = format_recommendations(stats)
    assert "Сократить" in advice


async def test_export_contains_every_table(seeded):
    import io
    import zipfile

    session, user = seeded
    archive = await build_export(session, user)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert {
            "meals.csv",
            "meal_items.csv",
            "glucose_readings.csv",
            "weights.csv",
            "body_profile.csv",
            "body_goals.csv",
            "workouts.csv",
            "README.txt",
        } <= names
        meals_csv = zf.read("meals.csv").decode("utf-8-sig")
    assert meals_csv.count("\n") == 43  # header + 42 meals


async def test_delete_after_export_leaves_nothing(seeded):
    session, user = seeded
    await repo.delete_user_data(session, user)
    assert set((await repo.counts(session, user)).values()) == {0}
