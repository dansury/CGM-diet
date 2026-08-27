"""Сон: ночи из Health Connect, оценка по появлениям, контрасты, напоминание."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.analytics import sleep as sleep_mod
from src.analytics.sleep import DayIntake, SleepInterval, SleepNight
from src.analytics.windows import GlucosePoint
from src.db import repo
from src.db.models import ActivitySample
from src.vision.schemas import ItemDraft, MealDraft

TZ = ZoneInfo("Europe/Moscow")  # UTC+3
NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """Локальное время пользователя, приведённое к UTC — как в БД."""
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ).astimezone(UTC)


# ------------------------------------------------------------------ Health Connect

def test_stage_records_merge_into_one_night():
    intervals = [
        SleepInterval(local(1, 23, 0), local(2, 1, 0), stage="light"),
        SleepInterval(local(2, 1, 0), local(2, 3, 0), stage="deep"),
        SleepInterval(local(2, 3, 0), local(2, 7, 0), stage="rem"),
    ]
    nights = sleep_mod.nights_from_intervals(intervals, TZ)
    assert len(nights) == 1
    assert nights[0].duration_min == 8 * 60
    assert nights[0].date.day == 2


def test_awake_stages_do_not_extend_a_night():
    intervals = [
        SleepInterval(local(1, 23, 0), local(2, 7, 0), stage="light"),
        SleepInterval(local(2, 7, 0), local(2, 9, 0), stage="awake"),
    ]
    nights = sleep_mod.nights_from_intervals(intervals, TZ)
    assert nights[0].duration_min == 8 * 60


def test_a_nap_is_not_a_night_and_does_not_replace_one():
    intervals = [
        SleepInterval(local(1, 23, 0), local(2, 6, 0)),
        SleepInterval(local(2, 14, 0), local(2, 15, 0)),  # 1 ч — дневной сон
    ]
    nights = sleep_mod.nights_from_intervals(intervals, TZ)
    assert len(nights) == 1
    assert nights[0].duration_min == 7 * 60


# ------------------------------------------------------------------ появления в чате

def burst(day: int, hour: int, minute: int, count: int, step_min: int = 6) -> list[datetime]:
    return [local(day, hour, minute + step_min * i) for i in range(count)]


def test_a_single_glance_at_three_in_the_morning_is_not_a_wake_up():
    pings = [
        *burst(1, 22, 0, 3),          # вечер: отбой около 22:12
        local(2, 3, 0),               # одиночное появление ночью
        *burst(2, 7, 30, 3),          # утро: подъём 07:30
    ]
    nights = sleep_mod.nights_from_presence(pings, TZ)
    assert len(nights) == 1
    night = nights[0]
    assert night.wake_at == local(2, 7, 30)
    assert night.bedtime == local(1, 22, 12)
    assert night.estimated is True


def test_a_long_night_session_does_count_as_being_awake():
    """Полчаса активности в три ночи — это «ещё не лёг», и ночь укорачивается."""
    pings = [
        *burst(1, 22, 0, 3),
        *burst(2, 3, 0, sleep_mod.NIGHT_MIN_PINGS + 1, step_min=10),  # 30+ минут ночью
        *burst(2, 7, 30, 3),
    ]
    nights = sleep_mod.nights_from_presence(pings, TZ)
    assert nights[0].bedtime == local(2, 3, 30)
    assert nights[0].duration_min == 4 * 60


def test_short_daytime_appearance_still_counts():
    session = sleep_mod.PresenceSession(local(2, 14, 0), local(2, 14, 1), pings=2)
    assert sleep_mod.is_significant(session, TZ)
    night_session = sleep_mod.PresenceSession(local(2, 3, 0), local(2, 3, 1), pings=2)
    assert not sleep_mod.is_significant(night_session, TZ)


def test_pings_further_apart_than_the_gap_are_separate_sessions():
    sessions = sleep_mod.presence_sessions([local(2, 9, 0), local(2, 9, 20), local(2, 12, 0)])
    assert [s.pings for s in sessions] == [2, 1]


def test_an_implausible_span_is_not_reported_as_a_night():
    pings = [*burst(1, 22, 0, 3), *burst(1, 23, 0, 3)]  # тот же вечер, ночи нет
    assert sleep_mod.nights_from_presence(pings, TZ) == []


def test_days_since_presence():
    assert sleep_mod.days_since_presence([], now=NOW) is None
    assert sleep_mod.days_since_presence([NOW - timedelta(days=2)], now=NOW) == pytest.approx(2.0)


# ------------------------------------------------------------------ режим

def nights_of(*specs: tuple[int, int, int, int]) -> list[SleepNight]:
    """(день отбоя, час отбоя, день подъёма, час подъёма) -> ночи."""
    out = []
    for bed_day, bed_hour, wake_day, wake_hour in specs:
        bedtime, wake = local(bed_day, bed_hour), local(wake_day, wake_hour)
        out.append(
            SleepNight(
                date=wake.astimezone(TZ).date(),
                bedtime=bedtime,
                wake_at=wake,
                duration_min=int((wake - bedtime).total_seconds() // 60),
            )
        )
    return out


def test_regularity_survives_bedtimes_around_midnight():
    nights = nights_of(
        (1, 23, 2, 7), (2, 23, 3, 7), (3, 0, 4, 8), (4, 0, 5, 8),
    )
    stats = sleep_mod.summarize(nights, TZ)
    # 23:00 и 00:00 отстоят на час, а не на 23 часа
    assert stats.bedtime_sd_min is not None and stats.bedtime_sd_min < 60
    assert stats.regular is True
    assert 1380 <= (stats.median_bedtime_min or 0) or (stats.median_bedtime_min or 0) <= 60


def test_a_floating_schedule_reads_as_irregular():
    nights = nights_of(
        (1, 21, 2, 5), (2, 23, 3, 7), (3, 2, 4, 10), (4, 20, 5, 4),
    )
    stats = sleep_mod.summarize(nights, TZ)
    assert stats.regular is False


def test_short_nights_are_counted():
    nights = nights_of((1, 1, 1, 6), (2, 23, 3, 7))
    stats = sleep_mod.summarize(nights, TZ)
    assert stats.short_nights == 1
    short, long = sleep_mod.split_by_duration(nights)
    assert len(short) == 1 and len(long) == 1


def test_regularity_split_needs_enough_nights():
    assert sleep_mod.split_by_regularity(nights_of((1, 23, 2, 7)), TZ) == (set(), set())


# ------------------------------------------------------------------ контрасты

def build_nights(short_days: list[int], long_days: list[int]) -> list[SleepNight]:
    nights = []
    for day in short_days:
        nights += nights_of((day, 2, day, 7))           # 5 ч
    for day in long_days:
        nights += nights_of((day - 1, 22, day, 7))      # 9 ч
    return nights


def test_days_after_short_nights_are_compared_on_calories():
    nights = build_nights([2, 3, 4], [5, 6, 7])
    intakes = [
        DayIntake(date=local(day, 12).astimezone(TZ).date(), kcal=kcal, carbs_g=kcal / 10)
        for day, kcal in ((2, 2600), (3, 2500), (4, 2700), (5, 2000), (6, 2100), (7, 1900))
    ]
    report = sleep_mod.build_report(nights, TZ, intakes=intakes)
    kcal = next(c for c in report.contrasts if c.metric == "kcal")
    assert kcal.n_a == 3 and kcal.n_b == 3
    assert kcal.mean_a == pytest.approx(2600.0)
    assert kcal.difference == pytest.approx(600.0)
    assert kcal.meaningful


def test_glucose_is_averaged_per_local_day():
    points = [
        GlucosePoint(at=local(2, 8), value=5.0),
        GlucosePoint(at=local(2, 20), value=7.0),
        GlucosePoint(at=local(3, 8), value=6.0),
    ]
    daily = sleep_mod.daily_mean_glucose(points, TZ)
    assert daily[local(2, 8).astimezone(TZ).date()] == pytest.approx(6.0)


def test_a_contrast_without_three_days_on_each_side_is_not_shown():
    nights = build_nights([2], [3, 4, 5])
    intakes = [
        DayIntake(date=local(day, 12).astimezone(TZ).date(), kcal=2000.0)
        for day in (2, 3, 4, 5)
    ]
    report = sleep_mod.build_report(nights, TZ, intakes=intakes)
    assert all(c.meaningful for c in report.contrasts)
    assert not [c for c in report.contrasts if c.metric == "kcal"]


def test_empty_input_yields_an_empty_report():
    report = sleep_mod.build_report([], TZ)
    assert report.stats.n_nights == 0
    assert report.contrasts == []


# ------------------------------------------------------------------ формулировки

def test_the_card_says_association_and_names_its_source():
    from src.reporting import format_sleep

    nights = build_nights([2, 3, 4], [5, 6, 7])
    intakes = [
        DayIntake(date=local(day, 12).astimezone(TZ).date(), kcal=kcal)
        for day, kcal in ((2, 2600), (3, 2500), (4, 2700), (5, 2000), (6, 2100), (7, 1900))
    ]
    text = format_sleep(sleep_mod.build_report(nights, TZ, intakes=intakes))
    assert "Samsung Health" in text
    assert "съедено за день" in text
    for forbidden in ("повышает", "вызывает", "из-за недосыпа"):
        assert forbidden not in text.lower()


def test_the_presence_card_admits_it_is_an_estimate():
    from src.reporting import format_sleep

    nights = build_nights([2, 3, 4], [5, 6, 7])
    for night in nights:
        night.source = "presence"
    text = format_sleep(sleep_mod.build_report(nights, TZ))
    assert "приблизительная" in text


def test_the_empty_card_offers_both_ways():
    from src.reporting import format_sleep

    text = format_sleep(sleep_mod.build_report([], TZ))
    assert "/health" in text and "появлениям" in text


# ------------------------------------------------------------------ хранение

async def test_presence_pings_are_thinned(session):
    user = await repo.get_or_create_user(session, 501)
    assert await repo.save_presence(session, user, at=NOW)
    assert not await repo.save_presence(session, user, at=NOW + timedelta(minutes=1))
    assert await repo.save_presence(
        session, user, at=NOW + timedelta(minutes=repo.PRESENCE_MIN_GAP_MIN)
    )
    assert len(await repo.load_presence(session, user)) == 2
    assert await repo.last_presence_at(session, user) == NOW + timedelta(
        minutes=repo.PRESENCE_MIN_GAP_MIN
    )


async def test_sleep_samples_reach_analytics_with_their_stage(session):
    user = await repo.get_or_create_user(session, 502)
    await repo.upsert_activity(
        session,
        user,
        [
            ActivitySample(
                external_id="sleep-1",
                kind="sleep",
                start_at=local(1, 23),
                end_at=local(2, 7),
                payload={"stage": "light"},
            ),
            ActivitySample(
                external_id="steps-1", kind="steps", start_at=local(2, 9), end_at=local(2, 10),
                steps=500,
            ),
        ],
    )
    intervals = await repo.load_sleep_intervals(session, user)
    assert len(intervals) == 1
    assert intervals[0].stage == "light"


async def test_daily_intake_groups_by_the_users_local_day(session):
    user = await repo.get_or_create_user(session, 503)
    user.tz = "Europe/Moscow"
    draft = MealDraft(title="Ужин", items=[ItemDraft(name="рис", kcal=500, carbs_g=100)])
    # 22:00 UTC = 01:00 следующего дня в Москве
    await repo.save_meal(session, user, draft, eaten_at=datetime(2026, 8, 2, 22, 0, tzinfo=UTC))
    days = await repo.daily_intake(session, user)
    assert [d.date.day for d in days] == [3]
    assert days[0].kcal == pytest.approx(500.0)


async def test_only_watchers_who_went_quiet_get_a_reminder(session):
    watching = await repo.get_or_create_user(session, 504)
    watching.sleep_presence_enabled = True
    await repo.save_presence(session, watching, at=NOW - timedelta(days=3))
    fresh = await repo.get_or_create_user(session, 505)
    fresh.sleep_presence_enabled = True
    await repo.save_presence(session, fresh, at=NOW - timedelta(hours=2))
    off = await repo.get_or_create_user(session, 506)
    await repo.save_presence(session, off, at=NOW - timedelta(days=9))
    await session.flush()

    due = await repo.users_due_for_presence_reminder(session, now=NOW)
    assert [u.tg_id for u in due] == [504]

    await repo.mark_presence_reminder(session, watching, NOW)
    assert await repo.users_due_for_presence_reminder(session, now=NOW) == []


async def test_erasure_takes_presence_with_it(session):
    user = await repo.get_or_create_user(session, 507)
    await repo.save_presence(session, user, at=NOW)
    await repo.delete_user_data(session, user)
    assert await repo.load_presence(session, user) == []


# ------------------------------------------------------------------ напоминание

class RecordingBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))


async def test_the_reminder_explains_what_to_do_and_repeats_rarely(engine, session):
    from src import scheduler

    user = await repo.get_or_create_user(session, 508)
    user.sleep_presence_enabled = True
    await repo.save_presence(session, user, at=NOW - timedelta(days=3))
    await session.commit()

    bot = RecordingBot()
    night = NOW.replace(hour=1)
    assert await scheduler.run_presence_reminders(bot, now=night) == 0

    assert await scheduler.run_presence_reminders(bot, now=NOW) == 1
    text = bot.messages[0][1]
    assert "/set sleep off" in text and "/health" in text
    # повторный тик через час молчит
    assert await scheduler.run_presence_reminders(bot, now=NOW + timedelta(hours=1)) == 0


# ------------------------------------------------------------------ /sleep

async def test_the_command_switches_the_watch_on_and_off(engine, session):
    from src.handlers import common
    from src.handlers import sleep as sleep_handler
    from tests.test_handlers_flow import FakeCallback, FakeMessage

    tg_id = 509
    message = FakeMessage(text="/sleep")
    message.chat.id = tg_id
    message.from_user.id = tg_id
    await sleep_handler.cmd_sleep(message)
    assert "Сон" in message.texts[0]

    callback = FakeCallback(data="sl:on", message=FakeMessage())
    callback.from_user.id = tg_id
    await sleep_handler.on_sleep_step(callback)
    user = await repo.get_user(session, tg_id)
    await session.refresh(user)
    assert user.sleep_presence_enabled
    # включение сразу ставит первую отметку — иначе напоминание сработает зря
    assert await repo.load_presence(session, user)

    off = FakeCallback(data="sl:off", message=FakeMessage())
    off.from_user.id = tg_id
    await sleep_handler.on_sleep_step(off)
    await session.refresh(user)
    assert not user.sleep_presence_enabled

    setting = FakeMessage(text="/set sleep on")
    setting.chat.id = setting.from_user.id = tg_id
    await common.cmd_set(setting)
    await session.refresh(user)
    assert user.sleep_presence_enabled


async def test_the_how_card_is_honest_about_telegram(engine, session):
    from src.handlers.sleep import HOW_TEXT

    assert "не сообщает ботам" in HOW_TEXT
    assert "/health" in HOW_TEXT


async def test_presence_is_recorded_only_for_watchers(engine, session):
    from src.handlers.presence import record_presence, reset_presence_cache

    reset_presence_cache()
    user = await repo.get_or_create_user(session, 510)
    await session.commit()
    assert not await record_presence(510)  # опция выключена

    reset_presence_cache()
    user.sleep_presence_enabled = True
    await session.commit()
    assert await record_presence(510)
    assert not await record_presence(510)  # троттлинг в процессе

    reset_presence_cache()
    assert not await record_presence(999999)  # незнакомого не заводим
    assert await repo.get_user(session, 999999) is None
