"""CGM history from a vendor CSV export: LibreView and Dexcom Clarity.

Both apps let a person download their own archive, and that archive is the
cheapest possible source of readings: thousands of points, already timestamped,
no screenshots to recognise. What the two formats share is exactly one idea —
a row per reading — so the parser is a per-format header map plus one shared
row loop.

The vendors' own quirks the parser has to survive:

* LibreView writes a title line *above* the header, and its glucose column is
  named for the unit the account uses (`Historic Glucose mmol/L` or `… mg/dL`);
  `Record Type` separates the sensor's own 15-minute history (0) from manual
  scans (1) and finger sticks (2).
* Clarity keeps its metadata in data rows (`Event Type` = `FirstName`, `Device`)
  with an empty timestamp, and writes out-of-range readings as the words `Low`
  and `High` rather than numbers.
* Neither is honest about the date order: the same export is `15-01-2024` in
  Europe and `01/15/2024` in the US. Ambiguity is resolved per file, not per
  row — see `_pick_date_order`.

The values themselves are never trusted: anything outside the physiological
range is dropped, the same as a misread screenshot (`spec/ingest.md` § Единицы).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

from src.ingest.units import MGDL, MMOL, is_plausible, to_mmol
from src.logging_setup import get_logger
from src.vision.schemas import GlucoseDraft

log = get_logger("ingest.cgm_csv")

LIBREVIEW = "libreview"
CLARITY = "clarity"

#: A person cannot review 30k rows, and neither can the confirmation step.
MAX_ROWS = 20000

# LibreView `Record Type` values that are a glucose reading.
_LIBRE_HISTORIC = "0"
_LIBRE_SCAN = "1"
_LIBRE_STRIP = "2"
_LIBRE_GLUCOSE_TYPES = {_LIBRE_HISTORIC, _LIBRE_SCAN, _LIBRE_STRIP}

# Day-first and month-first spellings of the same instant, tried in the order
# `_pick_date_order` decides on.
_DAY_FIRST = ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d.%m.%Y %H:%M",
              "%d-%m-%Y %I:%M %p", "%d/%m/%Y %I:%M %p")
_MONTH_FIRST = ("%m-%d-%Y %H:%M", "%m/%d/%Y %H:%M",
                "%m-%d-%Y %I:%M %p", "%m/%d/%Y %I:%M %p")
_UNAMBIGUOUS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S")


@dataclass(slots=True)
class CsvImport:
    """What one file turned into. `readings` are ready for `repo.save_glucose`."""

    source: str                       # libreview|clarity
    device: str | None = None
    unit_input: str = MMOL
    readings: list[GlucoseDraft] = field(default_factory=list)
    skipped_rows: int = 0             # rows that are not a glucose reading
    rejected: int = 0                 # readings outside the physiological range
    truncated: bool = False           # the file had more than MAX_ROWS readings

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if not self.readings:
            return None
        stamps = [r.measured_at for r in self.readings]
        return min(stamps), max(stamps)


class UnknownFormat(Exception):
    """Neither vendor's header was recognised — nothing was parsed."""


def parse_cgm_csv(data: bytes, *, max_rows: int = MAX_ROWS) -> CsvImport:
    """Vendor CSV → readings. Raises `UnknownFormat` when the header is foreign."""
    rows = list(csv.reader(io.StringIO(_decode(data))))
    header_idx, header = _find_header(rows)
    if header is None:
        raise UnknownFormat("не нашёл строку заголовков")
    columns = {_norm(name): idx for idx, name in enumerate(header)}
    body = rows[header_idx + 1 :]
    if _is_libreview(columns):
        return _parse_libreview(columns, body, max_rows=max_rows)
    if _is_clarity(columns):
        return _parse_clarity(columns, body, max_rows=max_rows)
    raise UnknownFormat("не похоже ни на LibreView, ни на Dexcom Clarity")


# ------------------------------------------------------------------ formats

def _is_libreview(columns: dict[str, int]) -> bool:
    return "record type" in columns and any(k.startswith("historic glucose") for k in columns)


def _is_clarity(columns: dict[str, int]) -> bool:
    return "event type" in columns and any(k.startswith("glucose value") for k in columns)


def _parse_libreview(
    columns: dict[str, int], body: list[list[str]], *, max_rows: int
) -> CsvImport:
    historic = _column(columns, "historic glucose")
    scan = _column(columns, "scan glucose")
    strip = _column(columns, "strip glucose")
    unit = _unit_of(historic[0] if historic else "")
    out = CsvImport(source=LIBREVIEW, unit_input=unit)
    stamp_at = columns.get("device timestamp")
    type_at = columns.get("record type")
    device_at = columns.get("device")
    order = _pick_date_order(_cells(body, stamp_at))

    by_type = {
        _LIBRE_HISTORIC: historic[1] if historic else None,
        _LIBRE_SCAN: scan[1] if scan else None,
        _LIBRE_STRIP: strip[1] if strip else None,
    }
    for row in body:
        record_type = _cell(row, type_at)
        if record_type not in _LIBRE_GLUCOSE_TYPES:
            out.skipped_rows += 1
            continue
        value_at = by_type.get(record_type)
        raw = _cell(row, value_at)
        measured_at = _parse_stamp(_cell(row, stamp_at), order)
        if measured_at is None or not raw:
            out.skipped_rows += 1
            continue
        if out.device is None:
            out.device = _cell(row, device_at) or None
        _append(out, measured_at, raw, unit, max_rows=max_rows)
        if out.truncated:
            break
    return out


def _parse_clarity(
    columns: dict[str, int], body: list[list[str]], *, max_rows: int
) -> CsvImport:
    value = _column(columns, "glucose value")
    unit = _unit_of(value[0] if value else "")
    out = CsvImport(source=CLARITY, unit_input=unit)
    stamp_at = next((idx for key, idx in columns.items() if key.startswith("timestamp")), None)
    type_at = columns.get("event type")
    device_at = columns.get("source device id")
    order = _pick_date_order(_cells(body, stamp_at))

    for row in body:
        # Clarity keeps account metadata in data rows; only EGV is a reading
        # the sensor produced, and only it has a timestamp.
        if _cell(row, type_at).upper() != "EGV":
            out.skipped_rows += 1
            continue
        measured_at = _parse_stamp(_cell(row, stamp_at), order)
        raw = _cell(row, value[1]) if value else ""
        if measured_at is None or not raw:
            out.skipped_rows += 1
            continue
        if out.device is None:
            out.device = _cell(row, device_at) or None
        _append(out, measured_at, raw, unit, max_rows=max_rows)
        if out.truncated:
            break
    return out


# ------------------------------------------------------------------ helpers

def _append(
    out: CsvImport, measured_at: datetime, raw: str, unit: str, *, max_rows: int
) -> None:
    if len(out.readings) >= max_rows:
        out.truncated = True
        return
    value = _number(raw, unit)
    if value is None or not is_plausible(value, unit):
        out.rejected += 1
        return
    out.readings.append(
        GlucoseDraft(
            measured_at=measured_at,
            value_mmol=to_mmol(value, unit),
            unit_input=unit,
            device=out.device,
        )
    )


def _number(raw: str, unit: str) -> float | None:
    """A cell to a number. `Low`/`High` are Dexcom's words for the sensor's edges."""
    text = raw.strip().replace(",", ".")
    low = text.lower()
    if low in {"low", "lo"}:
        return 2.2 if unit == MMOL else 40.0
    if low in {"high", "hi"}:
        return 22.2 if unit == MMOL else 400.0
    try:
        return float(text)
    except ValueError:
        return None


def _unit_of(column_name: str) -> str:
    return MGDL if "mg/dl" in column_name.lower() else MMOL


def _column(columns: dict[str, int], prefix: str) -> tuple[str, int] | None:
    for name, idx in columns.items():
        if name.startswith(prefix):
            return name, idx
    return None


def _find_header(rows: list[list[str]]) -> tuple[int, list[str] | None]:
    """LibreView puts a title line above the header; Clarity starts with it."""
    for idx, row in enumerate(rows[:5]):
        norm = {_norm(cell) for cell in row}
        if "record type" in norm or "event type" in norm:
            return idx, row
    return 0, None


def _pick_date_order(samples: list[str]) -> tuple[str, ...]:
    """Day-first or month-first, decided once per file.

    A single row cannot tell `03-04` apart; a file usually can — some row in it
    has a day past the twelfth. Nothing decisive found → day-first, because
    both exports are written in the account's locale and the bot's audience is
    not in the US.
    """
    for text in samples:
        match = re.match(r"\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            return _DAY_FIRST
        if second > 12:
            return _MONTH_FIRST
    return _DAY_FIRST


def _parse_stamp(text: str, order: tuple[str, ...]) -> datetime | None:
    """Naive local time, as the vendor wrote it — `repo` attaches the zone."""
    value = text.strip()
    if not value:
        return None
    for fmt in (*_UNAMBIGUOUS, *order):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _cells(body: list[list[str]], idx: int | None) -> list[str]:
    return [_cell(row, idx) for row in body if _cell(row, idx)]


def _norm(name: str) -> str:
    return name.strip().lower().lstrip("﻿")


def _decode(data: bytes) -> str:
    """Vendor exports arrive as UTF-8 (with BOM) or Windows-1251 from RU apps."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


__all__ = [
    "CLARITY",
    "LIBREVIEW",
    "MAX_ROWS",
    "CsvImport",
    "UnknownFormat",
    "parse_cgm_csv",
]
