"""Тренировки: распознавание, доп. вопросы, MET-оценка, запись, формулировки.

См. `spec/workout.md`. Проверяется то, где ошибка тихая: выбор MET, порядок
вопросов и то, что оценка энергозатрат нигде не выдаётся за измерение.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.analytics import workout as wo
from src.db import repo
from src.reporting import format_workout_draft, format_workouts
from src.vision import recognize
from src.vision.schemas import WorkoutDraft, workout_from_dict, workout_to_dict

NOW = datetime(2026, 8, 24, 19, 0, tzinfo=UTC)


# ------------------------------------------------------------------ каталог

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("бегал по парку", "running"),
        ("час на велосипеде", "cycling"),
        ("силовая в зале", "strength"),
        ("ходил пешком до работы", "walking"),
        ("плавал в бассейне", "swimming"),
        ("что-то делал", "other"),
    ],
)
def test_free_wording_maps_onto_the_closed_catalogue(text, expected):
    assert wo.resolve_kind(text) == expected


def test_pulse_outranks_the_sweat_question():
    assert wo.resolve_intensity(avg_hr=165, age=40, sweat="no")[0] == "high"
    assert wo.resolve_intensity(stated="low", avg_hr=165, age=40)[0] == "high"
    # без пульса решает названная интенсивность, и только потом пот
    assert wo.resolve_intensity(stated="low", sweat="yes")[0] == "low"
    assert wo.resolve_intensity(sweat="yes")[0] == "high"
    assert wo.resolve_intensity()[0] == wo.DEFAULT_INTENSITY


def test_speed_picks_the_met_inside_a_kind():
    slow = wo.met_for("running", "moderate", kmh=8.0)
    fast = wo.met_for("running", "moderate", kmh=14.0)
    assert fast > slow


def test_the_met_formula_scales_with_weight_and_time():
    light = wo.kcal_estimate(kind="walking", intensity="moderate", minutes=60, weight_kg=60)
    heavy = wo.kcal_estimate(kind="walking", intensity="moderate", minutes=60, weight_kg=100)
    assert heavy.kcal > light.kcal
    # 3.8 MET * 3.5 * 60 кг / 200 * 60 мин
    assert light.kcal == pytest.approx(239.0, abs=1.0)
    assert wo.kcal_estimate(kind="walking", intensity="moderate", minutes=None) is None


def test_an_unknown_weight_is_assumed_and_said_out_loud():
    estimate = wo.kcal_estimate(kind="running", intensity="moderate", minutes=30)
    assert estimate.assumed_weight is True
    assert estimate.weight_kg == wo.DEFAULT_WEIGHT_KG


def test_steps_alone_still_give_an_estimate():
    assert wo.kcal_from_steps(10000, 80.0) == pytest.approx(424.0, abs=1.0)
    assert wo.minutes_from_steps(11000) == pytest.approx(100.0, abs=1.0)
    assert wo.kcal_from_steps(0) is None


def test_questions_are_asked_in_order_and_skipped_when_pulse_is_known():
    assert wo.missing_questions(duration_min=None, intensity=None, sweat=None) == [
        "duration",
        "intensity",
        "sweat",
    ]
    assert wo.missing_questions(duration_min=40, intensity=None, sweat=None) == [
        "intensity",
        "sweat",
    ]
    assert wo.missing_questions(duration_min=40, intensity="high", sweat=None) == []
    assert wo.missing_questions(duration_min=40, intensity=None, sweat=None, avg_hr=150) == []
    # шаги заменяют вопрос о длительности
    assert "duration" not in wo.missing_questions(
        duration_min=None, intensity="low", sweat="no", steps=9000
    )


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("40 минут", 40.0), ("1.5 часа", 90.0), ("2 ч", 120.0), ("45", 45.0), ("полчаса", 30.0)],
)
def test_duration_is_parsed_without_a_model(text, minutes):
    assert wo.parse_duration(text) == minutes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("бегал 40 минут", True),
        ("10000 шагов", True),
        ("час на велике", True),
        ("овсянка с бананом", False),
        ("сахар 8.2", False),
        ("салат 200 г", False),
    ],
)
def test_a_workout_report_is_recognised_before_any_model_call(text, expected):
    assert wo.looks_like_workout(text) is expected


# ------------------------------------------------------------------ распознавание

async def test_text_recognition_fills_the_draft_and_leaves_kcal_to_the_calculator():
    draft = await recognize.parse_workout_text("бегал 40 минут", now=NOW)
    assert draft.kind == "running"
    assert draft.duration_min == 40
    assert draft.distance_m == 6500
    assert draft.kcal is None          # калории считает не модель


async def test_photo_recognition_reads_a_handwritten_page():
    draft = await recognize.recognize_workout_photo([], now=NOW)
    assert draft.kind == "strength"
    assert draft.avg_hr == 138
    assert draft.sweat == "yes"
    assert draft.started_at is not None and draft.started_at.hour == 19


def test_a_draft_survives_the_fsm_round_trip():
    draft = WorkoutDraft(kind="cycling", duration_min=75, started_at=NOW, sweat="light")
    restored = workout_from_dict(workout_to_dict(draft))
    assert restored == draft


# ------------------------------------------------------------------ хранение

async def test_workouts_are_stored_and_read_back_in_order(session):
    user = await repo.get_or_create_user(session, 21)
    for offset, kind in ((0, "running"), (2, "yoga")):
        await repo.save_workout(
            session,
            user,
            WorkoutDraft(kind=kind, duration_min=30, kcal=200.0, met=6.0),
            started_at=NOW + timedelta(hours=offset),
        )
    rows = await repo.load_workouts(session, user)
    assert [row.kind for row in rows] == ["running", "yoga"]
    assert rows[0].kcal == 200.0
    assert (await repo.counts(session, user))["workouts"] == 2


# ------------------------------------------------------------------ тексты

def test_an_estimate_is_never_presented_as_a_measurement():
    draft = WorkoutDraft(kind="running", title="Пробежка", duration_min=40)
    estimate = wo.kcal_estimate(kind="running", intensity="moderate", minutes=40, weight_kg=82)
    text = format_workout_draft(draft, estimate=estimate, started_at=NOW)
    assert "≈" in text
    assert "оценка" in text.lower()
    assert "MET" in text


def test_a_number_from_the_watch_is_shown_as_measured_not_estimated():
    draft = WorkoutDraft(
        kind="running", duration_min=40, kcal=511.0, kcal_source="device", title="Пробежка"
    )
    text = format_workout_draft(draft, estimate=None, started_at=NOW)
    assert "511" in text
    assert "с экрана" in text


def test_the_empty_journal_explains_what_to_send():
    assert "бегал" in format_workouts([])
