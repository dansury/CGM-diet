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


def test_a_dish_photographed_in_bursts_does_not_shrink_the_window():
    """Пять фото одного обеда подряд — не «пять приёмов по две минуты».

    Такие разрывы утягивали медиану к нижней границе (30 мин), и настоящий
    обед разваливался на несколько «приёмов пищи».
    """
    offsets = [0, 2, 5, 6, 46, 48, 49, 94, 96, 144]  # три обеда по 3–4 фото
    meals = [meal(offset, [plate.PlateItem("еда", 100, ["protein"])], id=i)
             for i, offset in enumerate(offsets)]
    assert plate.session_window_min(meals) > plate.MIN_SESSION_MIN


def test_the_days_meals_are_counted_even_when_each_is_several_photos():
    """Регрессия: «Приём 1 из 3» весь день, сколько бы человек ни ел."""
    day = [
        # завтрак: два блюда с разрывом 40 мин
        meal(0, [plate.PlateItem("блины", 150, ["refined_flour"])], id=1),
        meal(40, [plate.PlateItem("блины с яблоком", 150, ["refined_flour"]),
                  plate.PlateItem("кофе", 200, ["milk"])], id=2),
        # ужин: четыре записи подряд
        meal(552, [plate.PlateItem("салат с курицей", 200, ["protein"])], id=3),
        meal(552, [plate.PlateItem("лосось", 200, ["protein"])], id=4),
        meal(555, [plate.PlateItem("малина", 50, ["fruit"])], id=5),
        meal(559, [plate.PlateItem("арбуз", 150, ["fruit"])], id=6),
    ]
    window = plate.session_window_min(day)
    sessions = plate.group_sessions(day, window_min=window)
    assert len(plate.meal_sessions(sessions)) == 2
    assert plate.count_meals_today(day, day_start=NOW, window_min=window) == 2


def test_a_lone_snack_is_not_counted_as_a_meal_anywhere():
    coffee = [meal(i * 300, [plate.PlateItem("кофе", 200, ["milk"])], id=i) for i in range(10)]
    assert plate.count_meals_today(coffee, day_start=NOW, window_min=60) == 0
    assert plate.estimate_meals_per_day(coffee, window_min=60) is None
    assert plate.typical_meal_mass(coffee, window_min=60) is None


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


def test_meals_per_day_only_looks_at_the_last_five_days():
    """Старая привычка не должна перевешивать то, как человек ест сейчас."""
    meals = []
    idx = 0
    # 10 старых дней по 2 приёма — вне окна в 5 дней
    for day in range(10):
        for hour in (8, 19):
            idx += 1
            meals.append(
                plate.PlateMeal(
                    id=idx,
                    eaten_at=NOW.replace(hour=hour) + timedelta(days=day),
                    items=[plate.PlateItem("еда", 200, ["protein"])],
                )
            )
    # 5 последних дней по 4 приёма — то, что попадает в скользящее окно
    for day in range(10, 15):
        for hour in (8, 12, 16, 20):
            idx += 1
            meals.append(
                plate.PlateMeal(
                    id=idx,
                    eaten_at=NOW.replace(hour=hour) + timedelta(days=day),
                    items=[plate.PlateItem("еда", 200, ["protein"])],
                )
            )
    assert plate.estimate_meals_per_day(meals, window_min=60) == 4


def test_meals_per_day_window_is_relative_to_now_when_given():
    """`now` — например, вечер записи еды, а не время последней записи в истории."""
    meals = [
        plate.PlateMeal(
            id=idx,
            eaten_at=NOW.replace(hour=hour) + timedelta(days=day),
            items=[plate.PlateItem("еда", 200, ["protein"])],
        )
        for day in range(6)
        for idx, hour in enumerate((8, 13, 19), start=day * 3)
    ]
    # окно от последней записи (день 5) видит все 6 дней — хватает статистики
    assert plate.estimate_meals_per_day(meals, window_min=60) == 3
    # то же окно, но «сегодня» — день 0: впереди пока нет истории вовсе
    assert plate.estimate_meals_per_day(meals, window_min=60, now=NOW) is None


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


def test_a_coffee_or_a_handful_of_nuts_is_not_a_plate():
    assert not plate.is_meal([plate.PlateItem("кофе с молоком", 200, ["milk"])])
    assert not plate.is_meal([plate.PlateItem("орехи", 40, ["nuts"])])
    assert plate.is_meal(
        [
            plate.PlateItem("гречка", 150, ["whole_grain"]),
            plate.PlateItem("курица", 120, ["protein"]),
        ]
    )
    # масса перекуса в счёт не идёт: еды здесь всё ещё мало
    assert not plate.is_meal(
        [
            plate.PlateItem("кофе", 300, ["milk"]),
            plate.PlateItem("яблоко", 100, ["fruit"]),
        ]
    )


def test_a_snack_joins_the_plate_when_a_meal_lands_next_to_it():
    sessions = plate.group_sessions(
        [
            meal(0, [plate.PlateItem("кофе", 200, ["milk"])]),
            meal(
                20,
                [
                    plate.PlateItem("салат", 200, ["vegetable"]),
                    plate.PlateItem("курица", 150, ["protein"]),
                ],
            ),
        ],
        window_min=60,
    )
    assert len(sessions) == 1
    assert plate.is_meal(sessions[0].items)
    assert plate.score_items(sessions[0].items).grams["extra"] == 200


def test_a_lonely_snack_is_not_counted_as_a_meal_of_the_day():
    snack = plate.group_sessions([meal(0, [plate.PlateItem("кофе", 200, ["milk"])])], window_min=60)
    lunch = plate.group_sessions(
        [
            meal(
                180,
                [
                    plate.PlateItem("гречка", 200, ["whole_grain"]),
                    plate.PlateItem("курица", 200, ["protein"]),
                ],
            )
        ],
        window_min=60,
    )
    advice = plate.advise(
        current=lunch[0],
        day_sessions=[*snack, *lunch],
        rhythm=plate.measure_rhythm([]),
    )
    assert advice.meals_done == 1
    assert advice.meals_left == 2


def test_even_proportions_need_no_advice():
    even = plate.score_items(
        [
            plate.PlateItem("салат", 300, ["vegetable"]),
            plate.PlateItem("яблоко", 100, ["fruit"]),
            plate.PlateItem("гречка", 200, ["whole_grain"]),
            plate.PlateItem("курица", 200, ["protein"]),
        ]
    )
    assert plate.is_balanced(even)
    skewed = plate.score_items([plate.PlateItem("картошка", 500, ["potato"])])
    assert not plate.is_balanced(skewed)


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
