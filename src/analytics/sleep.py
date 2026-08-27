"""Sleep: how much, how regular — and what follows the next day.

Two independent sources feed the same `SleepNight` shape:

* **Health Connect** (`activity_samples.kind == "sleep"`) — real sessions from
  the watch or phone, merged into nights;
* **presence** — moments the person showed up in the chat with the bot, when
  Samsung Health is not connected. The Telegram Bot API never reports a user's
  online status, so “appeared” here means an update actually sent to the bot;
  a night built this way is `estimated`.

The presence estimator deliberately ignores short night-time appearances: a
single glance at the phone at 03:00 is not a wake-up, so a session inside
`NIGHT_CORE` counts only when it is long enough and has enough pings.

Everything downstream is a contrast, never a cause: “в дни после коротких ночей
съедено в среднем на N ккал больше”, not “недосып заставляет есть”.
See `spec/sleep.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from src.analytics.stats import MIN_OBSERVATIONS, mann_whitney_p, mean_ci
from src.analytics.windows import Excursion, GlucosePoint, MealLike

# --- nights -----------------------------------------------------------------
#: Health Connect emits sleep in stages; gaps up to this are the same night.
SEGMENT_GAP_MIN = 60
#: A night shorter/longer than this is not a night — a nap or a broken record.
MIN_NIGHT_MIN = 120
MAX_NIGHT_MIN = 840
#: Local hour that starts a new day: sleep that begins at 01:00 belongs to the
#: night before, not to the day it technically starts in.
DAY_START_HOUR = 4
#: Below this a night counts as short (7 h minus a half-hour of tolerance).
SHORT_SLEEP_MIN = 390

# --- presence ---------------------------------------------------------------
#: Pings closer than this belong to one appearance.
SESSION_GAP_MIN = 30
#: A daytime appearance counts when it has this many pings or lasts this long.
MIN_SESSION_PINGS = 2
MIN_SESSION_SPAN_MIN = 10
#: Local hours where the bar is higher — a glance at 03:00 is not a wake-up.
NIGHT_CORE = (0, 5)
NIGHT_MIN_PINGS = 3
NIGHT_MIN_SPAN_MIN = 25
#: No appearance for this long → the option cannot work, remind or switch off.
PRESENCE_SILENCE_DAYS = 1

# --- regularity -------------------------------------------------------------
#: Bedtime this far from the personal median makes the night irregular.
IRREGULAR_SHIFT_MIN = 90
#: Circular SD of bedtimes below this reads as “регулярно”.
REGULAR_SD_MIN = 60

MINUTES_PER_DAY = 1440


@dataclass(frozen=True, slots=True)
class SleepInterval:
    """One raw sleep record: a Health Connect session or one of its stages."""

    start_at: datetime
    end_at: datetime
    stage: str | None = None


@dataclass(slots=True)
class PresenceSession:
    """A run of pings with no gap longer than `SESSION_GAP_MIN`."""

    start_at: datetime
    end_at: datetime
    pings: int = 1

    @property
    def span_min(self) -> float:
        return (self.end_at - self.start_at).total_seconds() / 60.0


@dataclass(slots=True)
class SleepNight:
    """One night, keyed by the local date the person woke up on."""

    date: date
    bedtime: datetime
    wake_at: datetime
    duration_min: int
    source: str = "health"  # health|presence
    segments: int = 1
    estimated: bool = False

    @property
    def short(self) -> bool:
        return self.duration_min < SHORT_SLEEP_MIN


@dataclass(slots=True)
class SleepStats:
    n_nights: int
    mean_duration_min: float | None
    median_duration_min: float | None
    short_nights: int
    bedtime_sd_min: float | None
    wake_sd_min: float | None
    median_bedtime_min: float | None
    median_wake_min: float | None
    source: str = "health"

    @property
    def regular(self) -> bool | None:
        if self.bedtime_sd_min is None or self.wake_sd_min is None:
            return None
        return self.bedtime_sd_min <= REGULAR_SD_MIN and self.wake_sd_min <= REGULAR_SD_MIN


@dataclass(slots=True)
class SleepContrast:
    """Same shape for every «после таких ночей» comparison."""

    metric: str  # kcal|carbs|glucose|rise
    label_a: str
    label_b: str
    n_a: int
    n_b: int
    mean_a: float | None
    mean_b: float | None
    difference: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None

    @property
    def meaningful(self) -> bool:
        return (
            self.n_a >= MIN_OBSERVATIONS
            and self.n_b >= MIN_OBSERVATIONS
            and self.difference is not None
        )


@dataclass(slots=True)
class DayIntake:
    """What was eaten on one local day (built by `repo.daily_intake`)."""

    date: date
    kcal: float = 0.0
    carbs_g: float = 0.0
    meals: int = 0


# ------------------------------------------------------------------ helpers

def _local(value: datetime, tz: tzinfo) -> datetime:
    return value.astimezone(tz)


def _day_of(value: datetime, tz: tzinfo) -> date:
    """Local calendar date with the day starting at `DAY_START_HOUR`."""
    local = _local(value, tz)
    return (local - timedelta(hours=DAY_START_HOUR)).date()


def _clock_min(value: datetime, tz: tzinfo) -> float:
    local = _local(value, tz)
    return local.hour * 60 + local.minute + local.second / 60.0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _circular_median(minutes: list[float]) -> float | None:
    """Median clock time, rotated so that a midnight bedtime does not average
    to noon: 23:50 and 00:10 are 20 minutes apart, not 23 hours."""
    if not minutes:
        return None
    best: tuple[float, float] | None = None
    for anchor in minutes:
        shifted = [(m - anchor) % MINUTES_PER_DAY for m in minutes]
        shifted = [s - MINUTES_PER_DAY if s > MINUTES_PER_DAY / 2 else s for s in shifted]
        spread = sum(abs(s) for s in shifted)
        if best is None or spread < best[0]:
            median = (anchor + (_median(shifted) or 0.0)) % MINUTES_PER_DAY
            best = (spread, median)
    return round(best[1], 1) if best else None


def _circular_sd_min(minutes: list[float]) -> float | None:
    """Circular standard deviation of clock times, in minutes."""
    if len(minutes) < 2:
        return None
    angles = [m / MINUTES_PER_DAY * 2 * math.pi for m in minutes]
    cos_mean = sum(math.cos(a) for a in angles) / len(angles)
    sin_mean = sum(math.sin(a) for a in angles) / len(angles)
    r = math.hypot(cos_mean, sin_mean)
    if r <= 1e-9:
        return MINUTES_PER_DAY / 4  # scattered all over the clock
    sd_rad = math.sqrt(max(-2.0 * math.log(min(r, 1.0)), 0.0))
    return round(sd_rad * MINUTES_PER_DAY / (2 * math.pi), 1)


def clock_label(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    total = int(round(minutes)) % MINUTES_PER_DAY
    return f"{total // 60:02d}:{total % 60:02d}"


def duration_label(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    total = int(round(minutes))
    return f"{total // 60} ч {total % 60:02d} мин"


# ------------------------------------------------------------------ from Health Connect

#: Health Connect stages that mean “not asleep”; they must not extend a night.
AWAKE_STAGES = {"awake", "awake_in_bed", "out_of_bed", "unknown"}


def merge_intervals(
    intervals: list[SleepInterval], *, gap_min: int = SEGMENT_GAP_MIN
) -> list[SleepInterval]:
    """Overlapping or nearly adjacent records collapse into one segment.

    Stage records and the session that contains them arrive as separate rows;
    without merging the same night would be counted several times over.
    """
    usable = sorted(
        (
            i
            for i in intervals
            if i.end_at > i.start_at and (i.stage or "").lower() not in AWAKE_STAGES
        ),
        key=lambda i: i.start_at,
    )
    out: list[SleepInterval] = []
    for item in usable:
        if out and item.start_at - out[-1].end_at <= timedelta(minutes=gap_min):
            if item.end_at > out[-1].end_at:
                out[-1] = SleepInterval(out[-1].start_at, item.end_at)
            continue
        out.append(SleepInterval(item.start_at, item.end_at))
    return out


def nights_from_intervals(intervals: list[SleepInterval], tz: tzinfo) -> list[SleepNight]:
    """Merged sleep records → nights, keyed by the local date of waking up."""
    nights: list[SleepNight] = []
    for segment in merge_intervals(intervals):
        minutes = int(round((segment.end_at - segment.start_at).total_seconds() / 60))
        if not MIN_NIGHT_MIN <= minutes <= MAX_NIGHT_MIN:
            continue  # a nap, or a record so long it is broken
        nights.append(
            SleepNight(
                date=_local(segment.end_at, tz).date(),
                bedtime=segment.start_at,
                wake_at=segment.end_at,
                duration_min=minutes,
                source="health",
            )
        )
    return _one_per_date(nights)


def _one_per_date(nights: list[SleepNight]) -> list[SleepNight]:
    """Keep the longest night per date: a morning nap must not replace a night."""
    best: dict[date, SleepNight] = {}
    for night in nights:
        current = best.get(night.date)
        if current is None or night.duration_min > current.duration_min:
            best[night.date] = night
    return [best[key] for key in sorted(best)]


# ------------------------------------------------------------------ from presence

def presence_sessions(
    pings: list[datetime], *, gap_min: int = SESSION_GAP_MIN
) -> list[PresenceSession]:
    sessions: list[PresenceSession] = []
    for at in sorted(pings):
        if sessions and at - sessions[-1].end_at <= timedelta(minutes=gap_min):
            sessions[-1].end_at = at
            sessions[-1].pings += 1
            continue
        sessions.append(PresenceSession(start_at=at, end_at=at, pings=1))
    return sessions


def is_significant(session: PresenceSession, tz: tzinfo) -> bool:
    """Does this appearance count as “человек не спит”?

    In the small hours the bar is higher on purpose: a one-minute appearance at
    03:00 is someone checking the time, not the start of a day.
    """
    hour = _local(session.start_at, tz).hour
    if NIGHT_CORE[0] <= hour < NIGHT_CORE[1]:
        return session.pings >= NIGHT_MIN_PINGS and session.span_min >= NIGHT_MIN_SPAN_MIN
    return session.pings >= MIN_SESSION_PINGS or session.span_min >= MIN_SESSION_SPAN_MIN


def nights_from_presence(pings: list[datetime], tz: tzinfo) -> list[SleepNight]:
    """Estimate nights from appearances in the chat.

    Wake-up = the first significant appearance of a local day; bedtime = the
    last significant appearance of the day before. A night is only reported
    when both ends exist and the span is plausible.
    """
    significant = [s for s in presence_sessions(pings) if is_significant(s, tz)]
    by_day: dict[date, list[PresenceSession]] = {}
    for session in significant:
        by_day.setdefault(_day_of(session.start_at, tz), []).append(session)

    nights: list[SleepNight] = []
    for day in sorted(by_day):
        previous = by_day.get(day - timedelta(days=1))
        if not previous:
            continue
        bedtime = max(s.end_at for s in previous)
        wake_at = min(s.start_at for s in by_day[day])
        minutes = int(round((wake_at - bedtime).total_seconds() / 60))
        if not MIN_NIGHT_MIN <= minutes <= MAX_NIGHT_MIN:
            continue
        nights.append(
            SleepNight(
                date=_local(wake_at, tz).date(),
                bedtime=bedtime,
                wake_at=wake_at,
                duration_min=minutes,
                source="presence",
                segments=len(by_day[day]),
                estimated=True,
            )
        )
    return _one_per_date(nights)


def days_since_presence(pings: list[datetime], *, now: datetime) -> float | None:
    """How long the person has been invisible; `None` when never seen."""
    if not pings:
        return None
    return (now - max(pings)).total_seconds() / 86400.0


# ------------------------------------------------------------------ summary

def summarize(nights: list[SleepNight], tz: tzinfo) -> SleepStats:
    durations = [float(n.duration_min) for n in nights]
    bedtimes = [_clock_min(n.bedtime, tz) for n in nights]
    wakes = [_clock_min(n.wake_at, tz) for n in nights]
    return SleepStats(
        n_nights=len(nights),
        mean_duration_min=round(sum(durations) / len(durations), 1) if durations else None,
        median_duration_min=_median(durations),
        short_nights=sum(1 for n in nights if n.short),
        bedtime_sd_min=_circular_sd_min(bedtimes),
        wake_sd_min=_circular_sd_min(wakes),
        median_bedtime_min=_circular_median(bedtimes),
        median_wake_min=_circular_median(wakes),
        source=nights[0].source if nights else "health",
    )


def split_by_duration(
    nights: list[SleepNight], *, threshold_min: int = SHORT_SLEEP_MIN
) -> tuple[set[date], set[date]]:
    """(даты после коротких ночей, даты после обычных ночей)."""
    short = {n.date for n in nights if n.duration_min < threshold_min}
    long = {n.date for n in nights if n.duration_min >= threshold_min}
    return short, long


def split_by_regularity(
    nights: list[SleepNight], tz: tzinfo, *, shift_min: int = IRREGULAR_SHIFT_MIN
) -> tuple[set[date], set[date]]:
    """(даты после сдвинутого отбоя, даты после обычного отбоя).

    Сдвиг считается от собственной медианы отбоя — «поздно» у совы и у
    жаворонка это разное время.
    """
    if len(nights) < 2 * MIN_OBSERVATIONS:
        return set(), set()
    median = _circular_median([_clock_min(n.bedtime, tz) for n in nights])
    if median is None:
        return set(), set()
    irregular: set[date] = set()
    regular: set[date] = set()
    for night in nights:
        delta = abs((_clock_min(night.bedtime, tz) - median + MINUTES_PER_DAY / 2)
                    % MINUTES_PER_DAY - MINUTES_PER_DAY / 2)
        (irregular if delta > shift_min else regular).add(night.date)
    return irregular, regular


# ------------------------------------------------------------------ next-day values

def daily_kcal(intakes: list[DayIntake]) -> dict[date, float]:
    return {i.date: i.kcal for i in intakes if i.kcal > 0}


def daily_carbs(intakes: list[DayIntake]) -> dict[date, float]:
    return {i.date: i.carbs_g for i in intakes if i.carbs_g > 0}


def daily_mean_glucose(points: list[GlucosePoint], tz: tzinfo) -> dict[date, float]:
    buckets: dict[date, list[float]] = {}
    for point in points:
        buckets.setdefault(_local(point.at, tz).date(), []).append(point.value)
    return {day: round(sum(vs) / len(vs), 2) for day, vs in buckets.items()}


def daily_mean_rise(
    meals: list[MealLike], excursions: list[Excursion], tz: tzinfo
) -> dict[date, float]:
    """Mean usable postprandial rise per local day."""
    by_id = {m.id: m for m in meals}
    buckets: dict[date, list[float]] = {}
    for excursion in excursions:
        if not excursion.usable or excursion.delta is None:
            continue
        meal = by_id.get(excursion.meal_id)
        at = meal.eaten_at if meal is not None else excursion.eaten_at
        buckets.setdefault(_local(at, tz).date(), []).append(excursion.delta)
    return {day: round(sum(vs) / len(vs), 2) for day, vs in buckets.items()}


def contrast(
    values: dict[date, float],
    group_a: set[date],
    group_b: set[date],
    *,
    metric: str,
    label_a: str,
    label_b: str,
) -> SleepContrast:
    """Compare one daily number between two sets of days."""
    a = [values[day] for day in sorted(group_a) if day in values]
    b = [values[day] for day in sorted(group_b) if day in values]
    mean_a = round(sum(a) / len(a), 2) if a else None
    mean_b = round(sum(b) / len(b), 2) if b else None
    difference = round(mean_a - mean_b, 2) if mean_a is not None and mean_b is not None else None
    ci_low = ci_high = None
    if len(a) >= 2:
        _m, _sd, ci_low, ci_high = mean_ci(a)
    return SleepContrast(
        metric=metric,
        label_a=label_a,
        label_b=label_b,
        n_a=len(a),
        n_b=len(b),
        mean_a=mean_a,
        mean_b=mean_b,
        difference=difference,
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=mann_whitney_p(a, b),
    )


@dataclass(slots=True)
class SleepReport:
    """Everything `/sleep` and `/stats` show about sleep."""

    stats: SleepStats
    nights: list[SleepNight] = field(default_factory=list)
    contrasts: list[SleepContrast] = field(default_factory=list)


def build_report(
    nights: list[SleepNight],
    tz: tzinfo,
    *,
    intakes: list[DayIntake] | None = None,
    points: list[GlucosePoint] | None = None,
    meals: list[MealLike] | None = None,
    excursions: list[Excursion] | None = None,
) -> SleepReport:
    """Nights + «что бывает в такие дни» contrasts, in display order."""
    short, long = split_by_duration(nights)
    irregular, regular = split_by_regularity(nights, tz)
    series: list[tuple[str, dict[date, float]]] = []
    if intakes:
        series.append(("kcal", daily_kcal(intakes)))
        series.append(("carbs", daily_carbs(intakes)))
    if points:
        series.append(("glucose", daily_mean_glucose(points, tz)))
    if meals and excursions:
        series.append(("rise", daily_mean_rise(meals, excursions, tz)))

    contrasts: list[SleepContrast] = []
    for metric, values in series:
        if not values:
            continue
        for group_a, group_b, label_a, label_b in (
            (short, long, "после короткой ночи", "после обычной ночи"),
            (irregular, regular, "после сдвинутого отбоя", "после привычного отбоя"),
        ):
            if not group_a or not group_b:
                continue
            item = contrast(
                values, group_a, group_b, metric=metric, label_a=label_a, label_b=label_b
            )
            if item.meaningful:
                contrasts.append(item)
    return SleepReport(stats=summarize(nights, tz), nights=nights, contrasts=contrasts)


__all__ = [
    "AWAKE_STAGES",
    "DAY_START_HOUR",
    "DayIntake",
    "IRREGULAR_SHIFT_MIN",
    "MAX_NIGHT_MIN",
    "MIN_NIGHT_MIN",
    "MIN_SESSION_PINGS",
    "MIN_SESSION_SPAN_MIN",
    "NIGHT_CORE",
    "NIGHT_MIN_PINGS",
    "NIGHT_MIN_SPAN_MIN",
    "PRESENCE_SILENCE_DAYS",
    "PresenceSession",
    "REGULAR_SD_MIN",
    "SEGMENT_GAP_MIN",
    "SESSION_GAP_MIN",
    "SHORT_SLEEP_MIN",
    "SleepContrast",
    "SleepInterval",
    "SleepNight",
    "SleepReport",
    "SleepStats",
    "build_report",
    "clock_label",
    "contrast",
    "daily_carbs",
    "daily_kcal",
    "daily_mean_glucose",
    "daily_mean_rise",
    "days_since_presence",
    "duration_label",
    "is_significant",
    "merge_intervals",
    "nights_from_intervals",
    "nights_from_presence",
    "presence_sessions",
    "split_by_duration",
    "split_by_regularity",
    "summarize",
]
