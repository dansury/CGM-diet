"""Тело и цель: расчёты, безопасные рамки, хранение, формулировки.

Ошибка здесь тихая — человек увидит красивую полосу и будет есть на 900 ккал,
поэтому проверяются именно ограничители (`spec/body.md`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.analytics import body as body_math
from src.db import repo
from src.reporting import (
    format_body_card,
    format_day_progress,
    format_day_totals,
    format_goal_plan,
    format_meal_progress,
    progress_bar,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
TODAY = date(2026, 8, 24)


# ------------------------------------------------------------------ математика

def test_bmi_and_its_category_are_descriptive():
    assert body_math.bmi(82.0, 178.0) == pytest.approx(25.9, abs=0.05)
    assert body_math.bmi_category(body_math.bmi(82.0, 178.0)) == "избыточная масса тела"
    assert body_math.bmi(None, 178.0) is None


def test_bmr_prefers_katch_mcardle_when_body_fat_is_known():
    with_fat = body_math.bmr(82.0, height_cm=178.0, age=44, sex="m", body_fat_pct=24.0)
    without = body_math.bmr(82.0, height_cm=178.0, age=44, sex="m")
    # 370 + 21.6 * 62.3 lean kg
    assert with_fat == pytest.approx(1716.0, abs=2.0)
    assert without == pytest.approx(1717.5, abs=2.0)
    assert body_math.bmr(82.0) is None  # без роста и возраста считать нечего


def test_safe_rate_never_exceeds_one_percent_of_body_weight():
    low, high = body_math.safe_rate_range(60.0, "lose")
    assert high == pytest.approx(0.6)
    assert low == pytest.approx(0.15)
    # и не больше килограмма в неделю даже при очень большом весе
    assert body_math.safe_rate_range(200.0, "lose")[1] == 1.0
    assert body_math.safe_rate_range(80.0, "gain")[1] == pytest.approx(0.4)


def test_an_impatient_goal_is_clamped_and_the_clamp_is_reported():
    plan = body_math.build_plan(
        kind="lose",
        weight_kg=70.0,
        target_weight_kg=55.0,
        rate_kg_week=3.0,          # «минус три кило в неделю»
        height_cm=165.0,
        age=35,
        sex="f",
        activity="light",
        today=TODAY,
    )
    assert plan.rate_kg_week <= 0.7
    assert plan.target_kcal >= body_math.MIN_KCAL["f"]
    assert plan.capped, "урезание темпа обязано быть показано пользователю"


def test_the_daily_floor_holds_even_for_a_small_maintenance():
    plan = body_math.build_plan(
        kind="lose",
        weight_kg=52.0,
        target_weight_kg=48.0,
        rate_kg_week=1.0,
        height_cm=158.0,
        age=60,
        sex="f",
        activity="sedentary",
        today=TODAY,
    )
    assert plan.target_kcal >= 1200.0
    assert any("1200" in reason for reason in plan.capped)


def test_a_deficit_is_never_deeper_than_a_quarter_of_the_daily_spend():
    plan = body_math.build_plan(
        kind="lose",
        weight_kg=120.0,
        target_weight_kg=90.0,
        rate_kg_week=1.0,
        height_cm=180.0,
        age=40,
        sex="m",
        activity="sedentary",
        today=TODAY,
    )
    assert plan.tdee_kcal is not None
    assert plan.tdee_kcal - plan.target_kcal <= plan.tdee_kcal * 0.25 + 1


def test_losing_weight_below_the_healthy_bmi_is_refused():
    with pytest.raises(body_math.PlanImpossible):
        body_math.build_plan(
            kind="lose",
            weight_kg=48.0,
            target_weight_kg=44.0,
            height_cm=170.0,
            age=25,
            sex="f",
            activity="light",
            today=TODAY,
        )


def test_losing_weight_while_pregnant_is_refused_regardless_of_bmi():
    with pytest.raises(body_math.PlanImpossible):
        body_math.build_plan(
            kind="lose",
            weight_kg=70.0,
            target_weight_kg=65.0,
            height_cm=170.0,
            age=30,
            sex="f",
            activity="light",
            pregnant=True,
            today=TODAY,
        )


def test_a_plan_without_height_is_marked_as_a_rough_estimate():
    plan = body_math.build_plan(
        kind="lose", weight_kg=90.0, target_weight_kg=80.0, today=TODAY
    )
    assert plan.estimated is True
    assert plan.tdee_kcal is None


def test_goal_kind_reads_the_direction():
    assert body_math.goal_kind(90.0, 80.0) == "lose"
    assert body_math.goal_kind(60.0, 65.0) == "gain"
    assert body_math.goal_kind(70.0, 70.2) == "maintain"


def test_meal_target_kcal_splits_the_day_target_by_meals():
    assert body_math.meal_target_kcal(1800.0, 3) == pytest.approx(600.0)
    assert body_math.meal_target_kcal(1800.0, 2) == pytest.approx(900.0)
    assert body_math.meal_target_kcal(1800.0, 0) == pytest.approx(1800.0)  # без деления на 0


def test_day_balance_counts_the_workout_into_the_allowance():
    balance = body_math.day_balance(
        target_kcal=1900, consumed_kcal=2000, burned_kcal=400, carbs_g=210
    )
    assert balance.allowance_kcal == 2300
    assert balance.available_kcal == 300
    assert balance.over is False
    assert body_math.day_balance(target_kcal=1900, consumed_kcal=2100).over is True


def test_merge_burn_does_not_count_the_same_run_twice():
    start = NOW
    manual = [(start, start + timedelta(minutes=40), 400.0)]
    phone = [
        (start + timedelta(minutes=5), start + timedelta(minutes=45), 380.0),  # та же пробежка
        (start + timedelta(hours=6), start + timedelta(hours=6, minutes=30), 150.0),
    ]
    assert body_math.merge_burn(manual, phone) == 550.0


def test_weight_trend_needs_two_points():
    series = [(NOW - timedelta(days=14), 84.0), (NOW, 82.6)]
    trend = body_math.weight_trend(series)
    assert trend is not None
    assert trend.change_kg == pytest.approx(-1.4)
    assert trend.rate_kg_week == pytest.approx(-0.7, abs=0.01)
    assert body_math.weight_trend([(NOW, 82.0)]) is None


# ------------------------------------------------------------------ хранение

async def test_weight_is_stored_with_optional_bioimpedance(session):
    user = await repo.get_or_create_user(session, 7)
    await repo.save_weight(session, user, measured_at=NOW, weight_kg=82.4)
    await repo.save_weight(
        session,
        user,
        measured_at=NOW + timedelta(days=14),
        weight_kg=81.0,
        composition={"body_fat_pct": 24.1, "muscle_mass_kg": 58.6},
        source="photo",
    )
    rows = await repo.load_weights(session, user)
    assert [row.weight_kg for row in rows] == [82.4, 81.0]
    assert rows[0].body_fat_pct is None
    assert rows[1].body_fat_pct == 24.1
    assert (await repo.last_weight(session, user)).weight_kg == 81.0


async def test_only_one_goal_stays_active(session):
    user = await repo.get_or_create_user(session, 8)
    await repo.set_goal(
        session, user, kind="lose", target_weight_kg=80.0, start_weight_kg=90.0,
        rate_kg_week=0.5, target_kcal=1900.0, started_at=NOW,
    )
    await repo.set_goal(
        session, user, kind="lose", target_weight_kg=78.0, start_weight_kg=90.0,
        rate_kg_week=0.25, target_kcal=2100.0, started_at=NOW,
    )
    goal = await repo.get_active_goal(session, user)
    assert goal.target_weight_kg == 78.0
    await repo.clear_goal(session, user)
    assert await repo.get_active_goal(session, user) is None


async def test_profile_patch_keeps_the_fields_it_was_not_given(session):
    user = await repo.get_or_create_user(session, 9)
    await repo.upsert_body_profile(session, user, height_cm=178.0, sex="m")
    await repo.upsert_body_profile(session, user, birth_year=1982)
    profile = await repo.get_body_profile(session, user)
    assert (profile.height_cm, profile.sex, profile.birth_year) == (178.0, "m", 1982)
    assert profile.weight_prompt_days == 14


async def test_day_energy_sums_meals_and_workouts(session):
    from src.vision.schemas import ItemDraft, MealDraft, WorkoutDraft

    user = await repo.get_or_create_user(session, 10)
    await repo.save_meal(
        session,
        user,
        MealDraft(title="Обед", items=[ItemDraft(name="рис", portion_g=200, kcal=260, carbs_g=56)]),
        eaten_at=NOW,
    )
    await repo.save_workout(
        session,
        user,
        WorkoutDraft(kind="running", duration_min=40, kcal=420.0),
        started_at=NOW + timedelta(hours=1),
        ended_at=NOW + timedelta(hours=1, minutes=40),
    )
    totals = await repo.day_energy(
        session, user, start=NOW - timedelta(hours=12), end=NOW + timedelta(hours=12)
    )
    assert totals["consumed_kcal"] == 260.0
    assert totals["carbs_g"] == 56.0
    assert totals["burned_kcal"] == 420.0


async def test_the_weighing_reminder_waits_for_the_interval_and_for_the_gap(session):
    user = await repo.get_or_create_user(session, 11)
    await repo.upsert_body_profile(session, user, height_cm=178.0)
    await repo.save_weight(session, user, measured_at=NOW, weight_kg=82.0)

    assert await repo.users_due_for_weight(session, now=NOW + timedelta(days=7)) == []
    due = await repo.users_due_for_weight(session, now=NOW + timedelta(days=15))
    assert [u.tg_id for u, _ in due] == [11]

    profile = await repo.get_body_profile(session, user)
    await repo.mark_weight_prompt(session, profile, NOW + timedelta(days=15))
    # напомнили — второй раз в тот же день не пишем
    assert await repo.users_due_for_weight(session, now=NOW + timedelta(days=16)) == []


async def test_a_user_without_a_body_profile_is_never_nagged(session):
    await repo.get_or_create_user(session, 12)
    assert await repo.users_due_for_weight(session, now=NOW + timedelta(days=100)) == []


async def test_delete_wipes_the_body_data(session):
    user = await repo.get_or_create_user(session, 13)
    await repo.upsert_body_profile(session, user, height_cm=170.0)
    await repo.save_weight(session, user, measured_at=NOW, weight_kg=70.0)
    await repo.set_goal(
        session, user, kind="lose", target_weight_kg=65.0, start_weight_kg=70.0,
        rate_kg_week=0.5, target_kcal=1700.0, started_at=NOW,
    )
    await repo.delete_user_data(session, user)
    assert await repo.get_body_profile(session, user) is None
    assert await repo.load_weights(session, user) == []
    assert await repo.get_active_goal(session, user) is None


# ------------------------------------------------------------------ тексты

def test_the_progress_bar_marks_an_overshoot_separately():
    assert progress_bar(0.0) == "░" * 10
    assert progress_bar(0.5).count("▓") == 5
    assert "▒" in progress_bar(1.3)


def test_the_daily_corridor_is_shown_as_a_reference_not_a_prescription():
    plan = body_math.build_plan(
        kind="lose", weight_kg=95.0, target_weight_kg=85.0, height_cm=180.0,
        age=45, sex="m", activity="light", today=TODAY,
    )
    text = format_goal_plan(plan, kind="lose", target_weight_kg=85.0)
    assert "TDEE" in text and "BMR" in text          # видно, из чего посчитано
    assert "с врачом" in text
    for forbidden in ("вы должны", "назначаю", "диагноз"):
        assert forbidden not in text.lower()


def test_the_day_progress_names_the_numbers_behind_the_bar():
    balance = body_math.day_balance(
        target_kcal=1900, consumed_kcal=1420, burned_kcal=260, carbs_g=145
    )
    text = format_day_progress(balance)
    assert "1420" in text and "2160" in text
    assert "Осталось" in text


def test_the_meal_progress_bar_names_its_own_numbers():
    balance = body_math.day_balance(target_kcal=600, consumed_kcal=350)
    text = format_meal_progress(balance)
    assert "350" in text and "600" in text
    assert "▓" in text or "░" in text
    assert "Этот приём" in text


def test_day_progress_carries_the_meal_bar_line():
    balance = body_math.day_balance(target_kcal=1900, consumed_kcal=700, burned_kcal=0)
    text = format_day_progress(balance, meal="🍽 Этот приём: ▓░ 50% (300 из 600 ккал)")
    assert "Этот приём" in text


def test_the_day_total_without_a_goal_names_no_norm():
    balance = body_math.day_balance(target_kcal=0.0, consumed_kcal=646, carbs_g=72)
    text = format_day_totals(balance)
    assert "646" in text and "72" in text
    assert "/body" in text                       # как завести цель
    assert "%" not in text and "Осталось" not in text  # нормы без цели нет


def test_the_body_card_asks_for_a_goal_when_there_is_none():
    text = format_body_card(profile=None, last=None, goal=None, plan=None, trend=None)
    assert "Цель не задана" in text
    assert "вес" in text.lower()


# ------------------------------------------------------------------ T054

def test_the_measured_tdee_reads_the_weight_that_actually_moved():
    """Съедено 2000, ушло 3 кг за 42 дня — значит, тратилось ≈ 2550."""
    from datetime import UTC, datetime, timedelta

    from src.analytics.body import measured_tdee

    start = datetime(2026, 8, 1, tzinfo=UTC)
    weights = [(start, 85.0), (start + timedelta(days=42), 82.0)]
    intake = [((start + timedelta(days=d)).date(), 2000.0) for d in range(43)]
    measured = measured_tdee(weights, intake)
    assert measured is not None
    assert measured.kcal == 2550
    assert measured.days == 42
    assert measured.weight_change_kg == -3.0


def test_a_short_stretch_is_not_enough_to_measure_anything():
    from datetime import UTC, datetime, timedelta

    from src.analytics.body import MIN_TDEE_DAYS, measured_tdee

    start = datetime(2026, 8, 1, tzinfo=UTC)
    days = MIN_TDEE_DAYS - 1
    weights = [(start, 85.0), (start + timedelta(days=days), 84.0)]
    intake = [((start + timedelta(days=d)).date(), 2000.0) for d in range(days + 1)]
    assert measured_tdee(weights, intake) is None


def test_days_without_food_records_are_missing_not_zero():
    """Незаписанный день — не ноль калорий; при дырах измерять нечего."""
    from datetime import UTC, datetime, timedelta

    from src.analytics.body import measured_tdee

    start = datetime(2026, 8, 1, tzinfo=UTC)
    weights = [(start, 85.0), (start + timedelta(days=42), 82.0)]
    sparse = [((start + timedelta(days=d)).date(), 2000.0) for d in range(0, 43, 3)]
    assert measured_tdee(weights, sparse) is None


def test_an_absurd_result_is_thrown_away_not_shown():
    """Втрое больше формулы — это дыры в записях, а не обмен веществ."""
    from datetime import UTC, datetime, timedelta

    from src.analytics.body import measured_tdee

    start = datetime(2026, 8, 1, tzinfo=UTC)
    weights = [(start, 95.0), (start + timedelta(days=30), 80.0)]
    intake = [((start + timedelta(days=d)).date(), 2000.0) for d in range(31)]
    assert measured_tdee(weights, intake, formula_tdee=2200) is None
    # без сверки с формулой число всё же считается — решает вызывающий
    assert measured_tdee(weights, intake) is not None


def test_the_plan_says_when_it_stopped_using_the_formula():
    from src.analytics.body import build_plan
    from src.reporting import format_goal_plan

    plan = build_plan(
        kind="lose", weight_kg=85.0, target_weight_kg=80.0, height_cm=178,
        age=44, sex="m", activity="light", measured_tdee_kcal=2550,
    )
    assert plan.tdee_source == "measured"
    assert plan.tdee_kcal == 2550
    text = format_goal_plan(plan, kind="lose", target_weight_kg=80.0)
    assert "по вашим замерам" in text

    formula = build_plan(
        kind="lose", weight_kg=85.0, target_weight_kg=80.0, height_cm=178,
        age=44, sex="m", activity="light",
    )
    assert formula.tdee_source == "formula"
    assert "по вашим замерам" not in format_goal_plan(formula, kind="lose", target_weight_kg=80.0)


# ------------------------------------------------------------------ T053

def test_steps_below_the_activity_baseline_add_nothing():
    """Коэффициент активности их уже оплатил — иначе день посчитается дважды."""
    from src.analytics.body import steps_burn

    assert steps_burn(9000, 80, "moderate") == 0.0
    assert steps_burn(3000, 80, "sedentary") == 0.0


def test_steps_above_the_baseline_are_counted_once():
    from src.analytics.body import steps_burn

    # 12000 при сидячем уровне: сверх базовых 4000 — 8000 шагов
    assert steps_burn(12000, 80, "sedentary") == round(8000 * 80 * 0.0005, 0)
    # тот же человек с высоким уровнем активности — почти ничего сверх
    assert steps_burn(12000, 80, "high") == 0.0


def test_steps_inside_a_recorded_walk_are_not_counted_twice():
    from datetime import UTC, datetime, timedelta

    from src.analytics.body import steps_outside_workouts

    start = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
    workouts = [(start, start + timedelta(minutes=40), 210.0)]
    buckets = [
        (start + timedelta(minutes=10), start + timedelta(minutes=25), 2000),  # внутри
        (start + timedelta(hours=3), start + timedelta(hours=3, minutes=15), 1500),
    ]
    assert steps_outside_workouts(buckets, workouts) == 1500


def test_the_day_balance_names_steps_separately():
    from src.analytics.body import day_balance
    from src.reporting import format_day_progress

    balance = day_balance(
        target_kcal=1900, consumed_kcal=1200, burned_kcal=0,
        steps=14000, weight_kg=80, activity="sedentary",
    )
    assert balance.steps_kcal == 400
    assert balance.burned_kcal == 400
    text = format_day_progress(balance)
    assert "Шагов: 14000" in text
    assert "≈ 400 ккал" in text


def test_a_quiet_day_says_so_without_adding_calories():
    from src.analytics.body import day_balance
    from src.reporting import format_day_progress

    balance = day_balance(
        target_kcal=1900, consumed_kcal=1200, steps=3000,
        weight_kg=80, activity="sedentary",
    )
    assert balance.steps_kcal == 0.0
    assert "в пределах вашего обычного уровня" in format_day_progress(balance)


# ------------------------------------------------------------------ T068

def test_a_short_night_costs_the_extra_waking_hours():
    from src.analytics.body import sleep_adjust

    # три недоспанных часа при BMR 1700: ≈ 53 ккал
    assert sleep_adjust(1700, 5.0, typical_hours=8.0) == 53.0
    assert sleep_adjust(1700, 8.0, typical_hours=8.0) == 0.0


def test_a_long_night_goes_the_other_way():
    from src.analytics.body import sleep_adjust

    assert sleep_adjust(1700, 11.0, typical_hours=8.0) < 0


def test_sleep_without_a_bmr_changes_nothing():
    from src.analytics.body import sleep_adjust

    assert sleep_adjust(None, 5.0) == 0.0
    assert sleep_adjust(1700, None) == 0.0


def test_a_broken_night_cannot_swallow_the_corridor():
    from src.analytics.body import MAX_SLEEP_KCAL, sleep_adjust

    assert sleep_adjust(2500, 0.5, typical_hours=8.0) == MAX_SLEEP_KCAL


def test_the_corridor_names_the_night_without_claiming_consequences():
    from src.analytics.body import day_balance
    from src.reporting import format_day_progress

    balance = day_balance(
        target_kcal=1900, consumed_kcal=1200, sleep_hours=5.0,
        typical_sleep_hours=8.0, bmr_kcal=1700,
    )
    assert balance.sleep_kcal == 53.0
    text = format_day_progress(balance)
    assert "Ночь 5.0 ч" in text
    for forbidden in ("наберёте", "из-за", "приведёт", "вызывает"):
        assert forbidden not in text.lower()
