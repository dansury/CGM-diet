"""Notification scheduling: admin template, user override, «smart» time.

Pure numbers and slots, like the rest of `src/analytics` — no ORM, no aiogram,
no wording. The «smart» time is an *observation* of when this person actually
records things, never a prescription of when they should eat
(`spec/notifications.md`, `spec/clinical.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

MINUTES_PER_DAY = 1440
#: how close to a slot a tick still counts as «now», minutes
SEND_WINDOW_MIN = 30
#: fewer records than this and there is no habit to speak of
MIN_EVENTS = 6
#: a cluster smaller than this is a one-off, not a habit
MIN_CLUSTER = 2
#: how far back the habit is read
WINDOW_DAYS = 21
#: default head start before the usual moment
LEAD_MIN = 15
#: two notifications closer than this are the same habit split in two
MIN_GAP_MIN = 45
#: slots when neither the profile nor the template says how many
DEFAULT_SLOTS = 3
MAX_SLOTS = 8

#: what a click on the notification opens
ACTIONS = ("camera", "text", "open")
#: template-level mode; a user picks one of these plus "default"
MODES = ("fixed", "smart", "off")
USER_MODES = ("default", "fixed", "smart", "off")
#: record kinds a template may learn from
SIGNALS = ("meal", "glucose", "activity", "weight", "wellbeing")


@dataclass(slots=True)
class Template:
    """Admin-authored notification — the default everyone starts from."""

    code: str
    title: str
    body: str
    action: str = "camera"
    mode: str = "fixed"
    times: tuple[str, ...] = ()
    signal: tuple[str, ...] = ()
    lead_min: int = LEAD_MIN
    enabled: bool = True
    sort: int = 0


@dataclass(slots=True)
class Pref:
    """What one person changed about one notification."""

    code: str
    mode: str = "default"
    times: tuple[str, ...] = ()


@dataclass(slots=True)
class Schedule:
    """Resolved plan for one notification and one person."""

    code: str
    mode: str
    times: tuple[str, ...] = field(default_factory=tuple)

    @property
    def active(self) -> bool:
        return self.mode != "off" and bool(self.times)


def parse_times(raw: str | None) -> tuple[str, ...]:
    """«8:00, 13:30,бред,25:00» -> ('08:00', '13:30'). Order kept, dupes dropped."""
    out: list[str] = []
    for chunk in str(raw or "").replace(";", ",").split(","):
        minute = parse_time(chunk)
        if minute is None:
            continue
        label = format_minute(minute)
        if label not in out:
            out.append(label)
    return tuple(out)


def parse_time(raw: str | None) -> int | None:
    """'8:05' -> 485. Anything that is not a time of day -> None."""
    text = str(raw or "").strip().replace(".", ":").replace("-", ":")
    if not text:
        return None
    head, _, tail = text.partition(":")
    if not head.isdigit():
        return None
    hour = int(head)
    minute = int(tail) if tail.isdigit() else 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def format_minute(minute: int) -> str:
    minute %= MINUTES_PER_DAY
    return f"{minute // 60:02d}:{minute % 60:02d}"


def minute_of_day(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def smart_times(
    minutes: list[int],
    *,
    slots: int = DEFAULT_SLOTS,
    lead_min: int = LEAD_MIN,
    min_events: int = MIN_EVENTS,
    min_cluster: int = MIN_CLUSTER,
    min_gap: int = MIN_GAP_MIN,
) -> tuple[str, ...]:
    """Typical moments of a habit, shifted `lead_min` earlier.

    One-dimensional k-means over minutes of day: quantile seeds, at most 25
    passes, median (not mean) per cluster so a single 2 a.m. record cannot drag
    a slot across the evening. Clusters closer than `min_gap` are then merged —
    one outlier can split a habit in two, and two notifications five minutes
    apart are noise, not a schedule. Too few records — empty result, and the
    caller falls back to the template: inventing a habit out of three points
    would be a guess dressed as a measurement.
    """
    points = sorted(m % MINUTES_PER_DAY for m in minutes if m is not None)
    slots = max(1, min(int(slots or DEFAULT_SLOTS), MAX_SLOTS))
    if len(points) < max(min_events, 1):
        return ()
    if len(points) < slots:
        slots = len(points)

    centers = _quantile_seeds(points, slots)
    assignment: list[list[int]] = []
    for _ in range(25):
        buckets: list[list[int]] = [[] for _ in centers]
        for point in points:
            best = min(range(len(centers)), key=lambda i: abs(point - centers[i]))
            buckets[best].append(point)
        moved = False
        for i, bucket in enumerate(buckets):
            if not bucket:
                continue
            center = _median(bucket)
            if center != centers[i]:
                centers[i] = center
                moved = True
        assignment = buckets
        if not moved:
            break

    found: list[tuple[int, int]] = []   # (slot minute, cluster size)
    for bucket in assignment:
        if len(bucket) < min_cluster:
            continue
        target = (_median(bucket) - max(0, lead_min)) % MINUTES_PER_DAY
        found.append((target - target % 5, len(bucket)))
    return tuple(format_minute(minute) for minute in _merge_close(found, min_gap))


def _merge_close(found: list[tuple[int, int]], min_gap: int) -> list[int]:
    """Collapse neighbouring slots; the bigger cluster keeps its time."""
    out: list[tuple[int, int]] = []
    for minute, size in sorted(found):
        if out and minute - out[-1][0] < max(1, min_gap):
            previous, previous_size = out[-1]
            keep = previous if previous_size >= size else minute
            out[-1] = (keep, previous_size + size)
            continue
        out.append((minute, size))
    return [minute for minute, _size in out]


def _quantile_seeds(points: list[int], k: int) -> list[int]:
    """Deterministic seeds: the k inner quantiles of the sample."""
    n = len(points)
    return [points[min(n - 1, int((i + 0.5) * n / k))] for i in range(k)]


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def recent_minutes(
    moments: list[datetime], *, now: datetime, window_days: int = WINDOW_DAYS
) -> list[int]:
    """Local timestamps -> minutes of day, keeping only the recent window."""
    edge = now - timedelta(days=window_days)
    return [minute_of_day(m) for m in moments if m >= edge]


def resolve(
    template: Template,
    pref: Pref | None = None,
    *,
    smart: tuple[str, ...] = (),
) -> Schedule:
    """Admin template first, the person's own choice on top of it.

    A template switched off is off for everyone: `enabled=0` is the operator
    removing the notification, not a default to be overridden.
    """
    if not template.enabled:
        return Schedule(template.code, "off", ())
    mode = pref.mode if pref is not None else "default"
    if mode not in USER_MODES:
        mode = "default"
    if mode == "default":
        mode = template.mode if template.mode in MODES else "fixed"
        times = template.times
    elif mode == "off":
        return Schedule(template.code, "off", ())
    elif mode == "fixed":
        times = (pref.times if pref else ()) or template.times
    else:  # smart
        times = smart or template.times
    if mode == "smart" and not smart:
        # No habit yet — the template's times stand in, and the mode says so.
        return Schedule(template.code, "smart", template.times)
    return Schedule(template.code, mode, tuple(times))


def due_slots(
    schedule: Schedule,
    *,
    local_now: datetime,
    window_min: int = SEND_WINDOW_MIN,
) -> tuple[str, ...]:
    """Which slots of this schedule the current tick should deliver.

    A slot stays due for `window_min` minutes: a cron that runs every quarter
    of an hour must not drop a notification it happened to step over.
    """
    if not schedule.active:
        return ()
    now = minute_of_day(local_now)
    out: list[str] = []
    for label in schedule.times:
        slot = parse_time(label)
        if slot is None:
            continue
        delta = (now - slot) % MINUTES_PER_DAY
        if delta < window_min:
            out.append(format_minute(slot))
    return tuple(out)


__all__ = [
    "ACTIONS",
    "DEFAULT_SLOTS",
    "LEAD_MIN",
    "MIN_CLUSTER",
    "MIN_EVENTS",
    "MODES",
    "SEND_WINDOW_MIN",
    "SIGNALS",
    "USER_MODES",
    "WINDOW_DAYS",
    "Pref",
    "Schedule",
    "Template",
    "due_slots",
    "format_minute",
    "minute_of_day",
    "parse_time",
    "parse_times",
    "recent_minutes",
    "resolve",
    "smart_times",
]
