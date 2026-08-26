"""Гарвардская тарелка: классификация, сессии, режим питания, совет."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.analytics import plate
from src.reporting import format_plate_advice

NOW = datetime(2026, 8, 25, 13, 0, tzinfo=UTC)


def meal(offset_min: int, items: list[plate.PlateItem], *, id: int | None = 1) -> plate.PlateMeal:
    return plate.PlateMeal(id=id, eaten_at=NOW + timedelta(minutes=offset_min), items=items)


def test_tags_decide_the_quarter_of_the_plate():
    assert plate.classify(plate.PlateItem("салат", 100, ["vegetable"])) == "veg"
    assert plate.classify(plate.PlateItem("гречка", 100, ["whole_grain"])) == "grain"
    assert plate.classify(plate.PlateItem("курица", 100, ["protein"])) == "protein"
    assert plate.classify(plate.PlateItem("банан", 100, ["fruit"])) == "fruit"
    # чечевица — белок, а не «крахмалистое»
    assert plate.classify(plate.PlateItem("чечевица", 100, ["legume"])) == "protein"


def test_potato_and_white_flour_are_never_vegetables_or_whole_grain():
    assert plate.classify(plate.PlateItem("картофель фри", 200, ["potato"])) == "refined"
    assert plate.classify(plate.PlateItem("белый хлеб", 60, ["refined_flour"])) == "refined"
    assert plate.classify(plate.PlateItem("белый рис", 200, ["white_rice"])) == "refined"


def test_the_name_is_enough_when_the_model_returned_no_tags():
    assert plate.classify(plate.PlateItem("помидоры", 100)) == "veg"
    assert plate.classify(plate.PlateItem("лосось", 100)) == "protein"


def test_shares_and_score_follow_the_masses():
    score = plate.score_items(
        [
            plate.PlateItem("салат", 300, ["vegetable"]),
            plate.PlateItem("яблоко", 100, ["fruit"]),
            plate.PlateItem("гречка", 200, ["whole_grain"]),
            plate.PlateItem("курица", 200, ["protein"]),
        ]
    )
    assert score.mass_g == 800
    assert round(score.shares["veg"], 3) == 0.375
    assert score.score == 100.0
    assert not score.estimated_mass


def test_a_plate_of_refined_carbs_scores_low():
    score = plate.score_items([plate.PlateItem("картошка с булкой", 500, ["potato"])])
    assert score.score == 0.0
    assert score.grams["refined"] == 500


def test_a_missing_portion_is_flagged_not_dropped():
    score = plate.score_items([plate.PlateItem("салат", None, ["vegetable"])])
    assert score.estimated_mass
    assert score.mass_g == plate.FALLBACK_PORTION_G


def test_dishes_within_the_window_are_one_meal():
    meals = [
        meal(0, [plate.PlateItem("суп", 300, ["vegetable"])]),
        meal(25, [plate.PlateItem("котлета", 150, ["protein"])]),
        meal(300, [plate.PlateItem("ужин", 200, ["protein"])]),
    ]
    sessions = plate.group_sessions(meals, window_min=60)
    assert [len(s.meals) for s in sessions] == [2, 1]
    assert sessions[0].mass_g == 450


def test_the_window_defaults_to_an_hour_until_there_is_history():
    assert plate.session_window_min([]) == plate.DEFAULT_SESSION_MIN


def test_the_window_follows_the_users_own_meal_length():
    meals = [meal(i * 40, [plate.PlateItem("еда", 100, ["protein"])], id=i) for i in range(6)]
    assert plate.session_window_min(meals) == 40


def test_meals_per_day_needs_enough_days_of_history():
    one_day = [meal(i * 300, [plate.PlateItem("еда", 100)], id=i) for i in range(3)]
    assert plate.estimate_meals_per_day(one_day, window_min=60) is None


def test_meals_per_day_is_the_median_over_days_with_food():
    meals = []
    idx = 0
    for day in range(7):
        for hour in (8, 13, 19):
            idx += 1
            meals.append(
                plate.PlateMeal(
                    id=idx,
                    eaten_at=NOW.replace(hour=hour) + timedelta(days=day),
                    items=[plate.PlateItem("еда", 200, ["protein"])],
                )
            )
    assert plate.estimate_meals_per_day(meals, window_min=60) == 3


def test_the_user_setting_outranks_the_statistics():
    meals = [
        plate.PlateMeal(
            id=i,
            eaten_at=NOW + timedelta(days=i // 3, hours=6 * (i % 3)),
            items=[plate.PlateItem("еда", 200, ["protein"])],
        )
        for i in range(21)
    ]
    rhythm = plate.measure_rhythm(meals, meals_per_day=5)
    assert rhythm.meals_per_day == 5
    assert rhythm.meals_source == "user"
    assert plate.measure_rhythm(meals).meals_source == "stats"


def test_advice_names_what_is_missing_now_and_for_the_rest_of_the_day():
    current = plate.group_sessions(
        [meal(0, [plate.PlateItem("гречка", 200, ["whole_grain"])])], window_min=60
    )[0]
    rhythm = plate.Rhythm(
        meals_per_day=3,
        meals_source="default",
        session_min=60,
        session_source="default",
        meal_mass_g=500,
        mass_source="default",
    )
    advice = plate.advise(current=current, day_sessions=[current], rhythm=rhythm)
    gaps = {gap.category: gap.grams for gap in advice.now}
    assert gaps["veg"] == 188  # 500 г × 0.375
    assert gaps["protein"] == 125
    assert "grain" not in gaps  # злаков уже 200 г из 125
    assert advice.meals_left == 2
    assert {gap.category for gap in advice.day_gaps} >= {"veg", "protein"}


def test_the_text_stays_a_proportion_never_a_verdict():
    current = plate.group_sessions(
        [meal(0, [plate.PlateItem("салат", 250, ["vegetable"])])], window_min=60
    )[0]
    rhythm = plate.measure_rhythm([])
    advice = plate.advise(current=current, day_sessions=[current], rhythm=rhythm)
    text = format_plate_advice(advice)
    assert "Тарелка" in text
    assert "/plate" in text
    assert "/set plate off" not in text
    first_text = format_plate_advice(advice, with_rule=True)
    assert "/set plate off" in first_text
    assert "Гарвардская тарелка" in first_text
    for forbidden in ("норм", "диагноз", "нельзя", "вредно"):
        assert forbidden not in text.lower()


def test_gap_recommendations_are_rounded_to_50g():
    from src.reporting import _round_gap_50

    # protein/veg round up
    assert _round_gap_50("protein", 125) == 150
    assert _round_gap_50("veg", 188) == 200
    assert _round_gap_50("veg", 100) == 100
    assert _round_gap_50("protein", 51) == 100
    # grain/fruit round down
    assert _round_gap_50("grain", 125) == 100
    assert _round_gap_50("fruit", 62) == 50
    assert _round_gap_50("grain", 49) == 0


def test_advice_text_shows_rounded_gaps():
    current = plate.group_sessions(
        [meal(0, [plate.PlateItem("гречка", 200, ["whole_grain"])])], window_min=60
    )[0]
    rhythm = plate.Rhythm(
        meals_per_day=3,
        meals_source="default",
        session_min=60,
        session_source="default",
        meal_mass_g=500,
        mass_source="default",
    )
    advice = plate.advise(current=current, day_sessions=[current], rhythm=rhythm)
    text = format_plate_advice(advice)
    # veg gap is 188 -> ceil to 200
    assert "+200 г" in text
    # protein gap is 125 -> ceil to 150
    assert "+150 г" in text
