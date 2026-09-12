"""Notifications: the smart slot, admin/user priority, and once-per-slot-per-day.

Everything here is the quiet kind of failure the test policy asks for
(`CLAUDE.md` #8): a schedule that silently sends twice, or never.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.analytics.notify import (
    Pref,
    Template,
    due_slots,
    parse_time,
    parse_times,
    recent_minutes,
    resolve,
    smart_times,
)
from src.db import repo

MEAL = Template(
    code="meal",
    title="Пора записать еду",
    body="",
    mode="fixed",
    times=("08:30", "13:30", "19:00"),
    signal=("meal",),
)


# ------------------------------------------------------------------ parsing

def test_time_is_read_from_any_sane_spelling():
    assert parse_time("8:05") == 485
    assert parse_time("08.05") == 485
    assert parse_time("9") == 540
    assert parse_time("25:00") is None
    assert parse_time("07:60") is None
    assert parse_time("вечером") is None


def test_times_keep_order_and_drop_duplicates():
    assert parse_times("9:00, 08:30, 9:00, чушь") == ("09:00", "08:30")


# --------------------------------------------------------------- smart time

def _minutes(*hhmm: str) -> list[int]:
    return [parse_time(value) for value in hhmm]


def test_smart_time_finds_the_three_usual_meals():
    # three weeks of breakfast ~08:40, lunch ~13:10, dinner ~19:20
    minutes = _minutes(
        "08:35", "08:40", "08:45", "08:38", "08:42",
        "13:05", "13:10", "13:15", "13:08", "13:12",
        "19:15", "19:20", "19:25", "19:18", "19:22",
    )
    assert smart_times(minutes, slots=3, lead_min=15) == ("08:25", "12:55", "19:05")


def test_smart_time_needs_a_habit_not_three_points():
    assert smart_times(_minutes("08:30", "13:00", "19:00"), slots=3) == ()


def test_a_single_night_record_does_not_become_a_daily_notification():
    minutes = _minutes("08:35", "08:40", "08:45", "08:38", "08:42", "08:41", "02:10")
    times = smart_times(minutes, slots=2, lead_min=10)
    assert len(times) == 1 and times[0].startswith("08:")


def test_two_slots_minutes_apart_are_one_habit():
    minutes = _minutes("12:00", "12:05", "12:10", "12:15", "12:20", "12:25", "12:30", "12:35")
    assert len(smart_times(minutes, slots=3, lead_min=0)) == 1


def test_lead_time_can_cross_midnight():
    minutes = _minutes("00:05", "00:10", "00:07", "00:06", "00:09", "00:08")
    assert smart_times(minutes, slots=1, lead_min=15) == ("23:50",)


def test_only_the_recent_window_counts():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    moments = [now - timedelta(days=1), now - timedelta(days=40)]
    assert len(recent_minutes(moments, now=now, window_days=21)) == 1


# ---------------------------------------------------- admin/user priority

def test_the_admin_template_is_what_everyone_starts_from():
    schedule = resolve(MEAL, None)
    assert (schedule.mode, schedule.times) == ("fixed", ("08:30", "13:30", "19:00"))
    assert resolve(MEAL, Pref("meal", "default", ("07:00",))).times == MEAL.times


def test_the_persons_own_time_wins():
    schedule = resolve(MEAL, Pref("meal", "fixed", ("07:00", "12:00")))
    assert schedule.times == ("07:00", "12:00")


def test_empty_own_time_falls_back_to_the_template():
    assert resolve(MEAL, Pref("meal", "fixed", ())).times == MEAL.times


def test_smart_uses_the_computed_times_and_says_so():
    schedule = resolve(MEAL, Pref("meal", "smart"), smart=("08:15", "12:55"))
    assert (schedule.mode, schedule.times) == ("smart", ("08:15", "12:55"))


def test_smart_without_a_habit_keeps_the_template_times():
    schedule = resolve(MEAL, Pref("meal", "smart"), smart=())
    assert (schedule.mode, schedule.times) == ("smart", MEAL.times)


def test_the_person_can_switch_it_off():
    assert resolve(MEAL, Pref("meal", "off")).active is False


def test_a_notification_the_owner_disabled_is_off_for_everyone():
    disabled = replace(MEAL, enabled=False)
    assert resolve(disabled, Pref("meal", "fixed", ("07:00",))).active is False


# ------------------------------------------------------------- due windows

def _at(hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime(2026, 9, 12, hour, minute, tzinfo=UTC)


def test_a_slot_stays_due_long_enough_for_a_slow_tick():
    schedule = resolve(MEAL, None)
    assert due_slots(schedule, local_now=_at("08:30")) == ("08:30",)
    assert due_slots(schedule, local_now=_at("08:55")) == ("08:30",)
    assert due_slots(schedule, local_now=_at("09:05")) == ()


def test_a_late_evening_slot_does_not_leak_into_the_morning():
    schedule = resolve(Template(code="x", title="x", body="", times=("23:50",)), None)
    assert due_slots(schedule, local_now=_at("00:10")) == ("23:50",)
    assert due_slots(schedule, local_now=_at("08:00")) == ()


# ------------------------------------------------------------------- repo

@pytest.mark.asyncio
async def test_seeds_appear_once(session):
    await repo.seed_notifications(session)
    await repo.seed_notifications(session)
    rows = await repo.list_notification_templates(session)
    assert [row.code for row in rows] == ["meal", "cgm"]
    assert all(row.enabled for row in rows)


@pytest.mark.asyncio
async def test_a_slot_is_claimed_once_per_local_day(session):
    user = await repo.get_or_create_user(session, 555)
    claim = dict(code="meal", slot="08:30")
    assert await repo.claim_notification_slot(session, user, sent_on="2026-09-12", **claim)
    assert not await repo.claim_notification_slot(session, user, sent_on="2026-09-12", **claim)
    assert await repo.claim_notification_slot(session, user, sent_on="2026-09-13", **claim)


@pytest.mark.asyncio
async def test_one_persons_claim_does_not_block_another(session):
    first = await repo.get_or_create_user(session, 1)
    second = await repo.get_or_create_user(session, 2)
    claim = dict(code="meal", slot="08:30", sent_on="2026-09-12")
    assert await repo.claim_notification_slot(session, first, **claim)
    assert await repo.claim_notification_slot(session, second, **claim)


@pytest.mark.asyncio
async def test_the_persons_choice_round_trips(session):
    user = await repo.get_or_create_user(session, 555)
    await repo.set_notification_pref(session, user, "meal", mode="fixed", times="07:00,12:00")
    pref = repo.pref_of((await repo.notification_prefs(session, user))["meal"])
    assert (pref.mode, pref.times) == ("fixed", ("07:00", "12:00"))
    await repo.set_notification_pref(session, user, "meal", mode="off")
    pref = repo.pref_of((await repo.notification_prefs(session, user))["meal"])
    assert pref.mode == "off"


@pytest.mark.asyncio
async def test_deleting_a_notification_removes_what_people_set_about_it(session):
    user = await repo.get_or_create_user(session, 555)
    await repo.seed_notifications(session)
    await repo.set_notification_pref(session, user, "meal", mode="off")
    await repo.delete_notification_template(session, "meal")
    assert await repo.get_notification_template(session, "meal") is None
    assert "meal" not in await repo.notification_prefs(session, user)


@pytest.mark.asyncio
async def test_signal_times_come_back_in_the_persons_own_zone(session):
    from src.vision.schemas import ItemDraft, MealDraft

    user = await repo.get_or_create_user(session, 555)
    user.tz = "Europe/Moscow"       # UTC+3
    draft = MealDraft(title="овсянка", items=[ItemDraft(name="овсянка", portion_g=200)])
    eaten = datetime(2026, 9, 10, 5, 30, tzinfo=UTC)   # 08:30 local
    await repo.save_meal(session, user, draft, eaten_at=eaten)
    moments = await repo.notification_signal_times(
        session, user, ["meal"], since=eaten - timedelta(days=1)
    )
    assert [m.hour for m in moments] == [8]
    assert recent_minutes(moments, now=eaten + timedelta(hours=1)) == [8 * 60 + 30]
