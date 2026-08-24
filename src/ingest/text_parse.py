"""Deterministic parsing of free-text entries (no model call).

The bot must recognise “сахар 8”, “глюкоза 4.5 натощак”, “вес 72,3”,
“выпила метформин 500 в 8:30”, “самочувствие 3” without spending a model call
or a round-trip. Anything this module cannot classify falls through to the LLM
meal parser. See `spec/ingest.md` § Text router.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from src.ingest.units import MGDL, MMOL, canonical_unit, guess_unit, is_plausible, to_mmol

_NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"

_GLUCOSE_WORDS = r"(?:сахар|глюкоз\w*|гк|уровень\s+сахара|glucose|sugar|bg)"
_UNIT = r"(ммоль/?л?|ммоль|mmol/?l?|мг/?дл|mg/?dl)"

# "сахар 8", "глюкоза 4.5 ммоль", "гк 130 mg/dl", "сахар: 6,1"
_GLUCOSE_RE = re.compile(
    rf"{_GLUCOSE_WORDS}\s*[:=\-—]?\s*{_NUM}\s*{_UNIT}?", re.IGNORECASE | re.UNICODE
)
# bare "8.9 ммоль/л" without the word
_BARE_UNIT_RE = re.compile(rf"{_NUM}\s*{_UNIT}", re.IGNORECASE | re.UNICODE)

_WEIGHT_RE = re.compile(
    rf"(?:вес|весы|weight)\s*[:=\-—]?\s*(\d{{2,3}}(?:[.,]\d{{1,2}})?)\s*(?:кг|kg)?",
    re.IGNORECASE,
)
_WELLBEING_RE = re.compile(
    r"(?:самочувстви\w*|состояни\w*|自)\s*[:=\-—]?\s*([1-5])(?:\s*/\s*5)?", re.IGNORECASE
)
_MED_RE = re.compile(
    r"(?:принял\w*|выпил\w*|приняла|таблетк\w*|препарат)\s+([А-Яа-яA-Za-z][\w\-]{2,40})"
    r"(?:\s+(\d+\s*(?:мг|мкг|г|ед|ме|mg|units?)))?",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(?:в\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)\b")
# Month must be two digits (01–12): that is what keeps "сахар 9.1" from
# parsing as 9 January. Day may be one or two digits.
_DATE_RE = re.compile(r"\b([0-3]?\d)[./]([01]\d)(?:[./](\d{2,4}))?\b")

_FASTING_RE = re.compile(r"натощак|до\s+еды|fasting", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"\bвчера\b", re.IGNORECASE)
# Words that carried a value already extracted — dropped from `leftover` so
# the LLM meal parser is not handed "вчера в , , выпила".
_LEFTOVER_NOISE = re.compile(
    r"\b(вчера|сегодня|натощак|до\s+еды|в|мг|мкг|ед|кг)\b", re.IGNORECASE
)


@dataclass(slots=True)
class ParsedText:
    """Everything one free-text message yielded."""

    glucose: list[tuple[float, str]] = field(default_factory=list)  # (value, raw unit)
    weight_kg: float | None = None
    wellbeing: int | None = None
    medications: list[tuple[str, str | None]] = field(default_factory=list)
    at: datetime | None = None
    fasting: bool = False
    leftover: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.glucose or self.weight_kg or self.wellbeing or self.medications)


def _num(raw: str) -> float:
    return float(raw.replace(",", "."))


def parse_text(text: str, *, now: datetime | None = None) -> ParsedText:
    """Extract structured facts from a free-text message.

    `now` (timezone-aware, in the user's local zone) anchors relative times such
    as “вчера в 21:00”; it is injected rather than read from the clock so the
    behaviour is testable.
    """
    result = ParsedText()
    if not text:
        return result
    consumed: list[tuple[int, int]] = []

    for match in _GLUCOSE_RE.finditer(text):
        value = _num(match.group(1))
        unit_raw = match.group(2)
        unit = canonical_unit(unit_raw) if unit_raw else guess_unit(value)
        if is_plausible(value, unit):
            result.glucose.append((value, unit))
            consumed.append(match.span())

    if not result.glucose:
        for match in _BARE_UNIT_RE.finditer(text):
            value = _num(match.group(1))
            unit = canonical_unit(match.group(2))
            if is_plausible(value, unit):
                result.glucose.append((value, unit))
                consumed.append(match.span())

    weight = _WEIGHT_RE.search(text)
    if weight:
        kg = _num(weight.group(1))
        if 25.0 <= kg <= 400.0:
            result.weight_kg = kg
            consumed.append(weight.span())

    wellbeing = _WELLBEING_RE.search(text)
    if wellbeing:
        result.wellbeing = int(wellbeing.group(1))
        consumed.append(wellbeing.span())

    for match in _MED_RE.finditer(text):
        name = match.group(1).strip()
        dose = (match.group(2) or "").strip() or None
        result.medications.append((name, dose))
        consumed.append(match.span())

    result.fasting = bool(_FASTING_RE.search(text))
    result.at, when_spans = _parse_when(text, now=now, consumed=consumed)
    consumed.extend(when_spans)

    leftover = text
    for start, end in sorted(consumed, reverse=True):
        leftover = leftover[:start] + " " + leftover[end:]
    leftover = _LEFTOVER_NOISE.sub(" ", leftover)
    result.leftover = re.sub(r"\s{2,}", " ", leftover).strip(" ,.;:—-")
    return result


def _parse_when(
    text: str, *, now: datetime | None, consumed: list[tuple[int, int]]
) -> tuple[datetime | None, list[tuple[int, int]]]:
    """Resolve “в 8:30”, “вчера в 21:00”, “23.08 в 9:15” to a local datetime.

    Spans already claimed by a value match are skipped so “сахар 9.1” cannot
    be re-read as a date and “130 mg/dl” as a time. Returns the resolved
    moment plus the spans it consumed.
    """
    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and start < span[1] for start, end in consumed)

    # Date first: "23.08" must not be eaten by the time pattern's dot form.
    date_match = next((m for m in _DATE_RE.finditer(text) if not overlaps(m.span())), None)
    if date_match:
        consumed = [*consumed, date_match.span()]
    time_match = next((m for m in _TIME_RE.finditer(text) if not overlaps(m.span())), None)
    if time_match is None:
        return None, []
    spans = [time_match.span()]
    hh, mm = int(time_match.group(1)), int(time_match.group(2))
    base = now or datetime.now()
    day: date = base.date()

    if date_match:
        spans.append(date_match.span())
        dd, mo = int(date_match.group(1)), int(date_match.group(2))
        year_raw = date_match.group(3)
        year = base.year
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        try:
            day = date(year, mo, dd)
        except ValueError:
            day = base.date()
    elif _YESTERDAY_RE.search(text):
        day = base.date() - timedelta(days=1)

    stamp = datetime.combine(day, time(hh, mm))
    if base.tzinfo is not None:
        stamp = stamp.replace(tzinfo=base.tzinfo)
    # “в 23:40” typed just after midnight means yesterday, not 23 hours ahead.
    if not date_match and not _YESTERDAY_RE.search(text) and stamp - base > timedelta(minutes=90):
        stamp -= timedelta(days=1)
    return stamp, spans


def to_mmol_pairs(parsed: ParsedText) -> list[tuple[float, str]]:
    """Convert every captured reading to (mmol value, original unit)."""
    return [(to_mmol(value, unit), unit) for value, unit in parsed.glucose]


__all__ = ["MGDL", "MMOL", "ParsedText", "parse_text", "to_mmol_pairs"]
