"""Импорт истории CGM из выгрузок LibreView и Dexcom Clarity.

Ошибка здесь тихая: чужой порядок дат или непереведённые мг/дл дают правдоподобные
числа не в том месте графика, и заметить это по отчёту нельзя.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.ingest.cgm_csv import CLARITY, LIBREVIEW, UnknownFormat, parse_cgm_csv

LIBRE_HEADER = (
    "Device,Serial Number,Device Timestamp,Record Type,Historic Glucose mmol/L,"
    "Scan Glucose mmol/L,Notes,Strip Glucose mmol/L\n"
)
LIBRE_TITLE = "Данные о глюкозе,Создано,15-01-2024 10:30 UTC,Создано пользователем,И И\n"

CLARITY_HEADER = (
    "Index,Timestamp (YYYY-MM-DDThh:mm:ss),Event Type,Event Subtype,Patient Info,"
    "Device Info,Source Device ID,Glucose Value (mg/dL)\n"
)


def _libre(*rows: str, header: str = LIBRE_HEADER) -> bytes:
    return (LIBRE_TITLE + header + "".join(rows)).encode("utf-8")


def _clarity(*rows: str, header: str = CLARITY_HEADER) -> bytes:
    return (header + "".join(rows)).encode("utf-8")


# ------------------------------------------------------------------ LibreView

def test_libreview_historic_scan_and_strip_all_count():
    data = _libre(
        "FreeStyle LibreLink,ABC1,15-01-2024 08:12,0,5.4,,,\n",
        "FreeStyle LibreLink,ABC1,15-01-2024 08:27,1,,7.1,,\n",
        "FreeStyle LibreLink,ABC1,15-01-2024 09:00,2,,,,6.2\n",
    )
    result = parse_cgm_csv(data)
    assert result.source == LIBREVIEW
    assert [r.value_mmol for r in result.readings] == [5.4, 7.1, 6.2]
    assert result.device == "FreeStyle LibreLink"
    assert result.readings[0].measured_at == datetime(2024, 1, 15, 8, 12)


def test_libreview_non_glucose_rows_are_skipped_not_guessed():
    """Запись инсулина и заметка — не замеры, и молча числами стать не должны."""
    data = _libre(
        "FreeStyle LibreLink,ABC1,15-01-2024 08:12,0,5.4,,,\n",
        "FreeStyle LibreLink,ABC1,15-01-2024 12:00,4,,,обед,\n",
        "FreeStyle LibreLink,ABC1,15-01-2024 13:00,5,,,,\n",
    )
    result = parse_cgm_csv(data)
    assert len(result.readings) == 1
    assert result.skipped_rows == 2


def test_libreview_mgdl_export_is_converted():
    header = LIBRE_HEADER.replace("mmol/L", "mg/dL")
    data = _libre("Libre 3,ABC1,15-01-2024 08:12,0,99,,,\n", header=header)
    result = parse_cgm_csv(data)
    assert result.unit_input == "mg/dL"
    assert result.readings[0].value_mmol == pytest.approx(5.49, abs=0.01)


def test_impossible_values_are_rejected_not_stored():
    data = _libre(
        "Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n",
        "Libre 3,ABC1,15-01-2024 08:27,0,99.9,,,\n",
        "Libre 3,ABC1,15-01-2024 08:42,0,0.2,,,\n",
    )
    result = parse_cgm_csv(data)
    assert [r.value_mmol for r in result.readings] == [5.4]
    assert result.rejected == 2


# ------------------------------------------------------------------ даты

def test_month_first_export_is_recognised_by_the_whole_file():
    """`01/15/2024` месяцем пятнадцатым не бывает — значит, файл американский."""
    data = _libre(
        "Libre 3,ABC1,01/15/2024 08:12,0,5.4,,,\n",
        "Libre 3,ABC1,01/16/2024 08:12,0,5.6,,,\n",
    )
    result = parse_cgm_csv(data)
    assert [r.measured_at for r in result.readings] == [
        datetime(2024, 1, 15, 8, 12),
        datetime(2024, 1, 16, 8, 12),
    ]


def test_ambiguous_dates_default_to_day_first():
    data = _libre("Libre 3,ABC1,03-04-2024 08:12,0,5.4,,,\n")
    assert parse_cgm_csv(data).readings[0].measured_at == datetime(2024, 4, 3, 8, 12)


def test_one_ambiguous_row_follows_the_file_not_itself():
    """В файле есть 16-е число — значит, и `03-04` читается днём вперёд."""
    data = _libre(
        "Libre 3,ABC1,03-04-2024 08:12,0,5.4,,,\n",
        "Libre 3,ABC1,16-04-2024 08:12,0,5.6,,,\n",
    )
    result = parse_cgm_csv(data)
    assert result.readings[0].measured_at == datetime(2024, 4, 3, 8, 12)


# ------------------------------------------------------------------ Clarity

def test_clarity_reads_egv_rows_only():
    data = _clarity(
        "1,,FirstName,,Иван,,,\n",
        "2,,Device,,,Dexcom G6 Mobile,,\n",
        "11,2024-01-15T08:12:34,EGV,,,,DEX1,98\n",
        "12,2024-01-15T08:17:34,EGV,,,,DEX1,104\n",
        "13,2024-01-15T12:00:00,Insulin,Fast-Acting,,,DEX1,\n",
    )
    result = parse_cgm_csv(data)
    assert result.source == CLARITY
    assert len(result.readings) == 2
    assert result.skipped_rows == 3
    assert result.device == "DEX1"
    assert result.readings[0].measured_at == datetime(2024, 1, 15, 8, 12, 34)
    assert result.readings[0].value_mmol == pytest.approx(5.44, abs=0.01)


def test_clarity_low_and_high_become_the_sensor_edges():
    """`Low`/`High` — слова Dexcom для краёв диапазона, а не пропуск."""
    data = _clarity(
        "11,2024-01-15T08:12:34,EGV,,,,DEX1,Low\n",
        "12,2024-01-15T08:17:34,EGV,,,,DEX1,High\n",
    )
    values = [r.value_mmol for r in parse_cgm_csv(data).readings]
    assert values[0] < 2.5 < values[1]


def test_clarity_mmol_export_is_taken_as_is():
    header = CLARITY_HEADER.replace("mg/dL", "mmol/L")
    data = _clarity("11,2024-01-15T08:12:34,EGV,,,,DEX1,5.4\n", header=header)
    assert parse_cgm_csv(data).readings[0].value_mmol == 5.4


# ------------------------------------------------------------------ прочее

def test_a_foreign_csv_is_refused_not_half_parsed():
    with pytest.raises(UnknownFormat):
        parse_cgm_csv(b"a,b,c\n1,2,3\n")


def test_windows_1251_export_still_decodes():
    data = (LIBRE_TITLE + LIBRE_HEADER + "Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n").encode("cp1251")
    assert parse_cgm_csv(data).readings[0].value_mmol == 5.4


def test_a_huge_file_is_cut_and_says_so():
    rows = [f"Libre 3,ABC1,15-01-2024 08:{n:02d},0,5.4,,,\n" for n in range(0, 10)]
    result = parse_cgm_csv(_libre(*rows), max_rows=4)
    assert len(result.readings) == 4
    assert result.truncated is True


def test_span_names_the_covered_period():
    data = _libre(
        "Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n",
        "Libre 3,ABC1,20-01-2024 08:12,0,5.6,,,\n",
    )
    first, last = parse_cgm_csv(data).span
    assert (first.day, last.day) == (15, 20)


# ------------------------------------------------------------------ запись

async def test_bulk_insert_localises_and_deduplicates(session):
    """Время из файла — стенные часы человека, а повторная загрузка — не дубль."""
    from datetime import UTC

    from src.db import repo

    user = await repo.get_or_create_user(session, 501)
    user.tz = "Europe/Moscow"
    data = _libre(
        "Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n",
        "Libre 3,ABC1,15-01-2024 08:27,0,5.6,,,\n",
    )
    result = parse_cgm_csv(data)
    added = await repo.save_glucose_bulk(session, user, result.readings, source=result.source)
    assert added == 2

    rows = await repo.load_glucose(session, user)
    assert len(rows) == 2
    # 08:12 в Москве — это 05:12 UTC, а не 08:12 UTC
    assert rows[0].measured_at.astimezone(UTC) == datetime(2024, 1, 15, 5, 12, tzinfo=UTC)
    assert rows[0].source == LIBREVIEW

    again = await repo.save_glucose_bulk(session, user, result.readings, source=result.source)
    assert again == 0
    assert len(await repo.load_glucose(session, user)) == 2

    # а новая точка внутри того же периода всё-таки добавляется
    extra = list(result.readings)
    extra.append(
        parse_cgm_csv(_libre("Libre 3,ABC1,15-01-2024 08:42,0,6.0,,,\n")).readings[0]
    )
    assert await repo.save_glucose_bulk(session, user, extra, source=LIBREVIEW) == 1
    assert len(await repo.load_glucose(session, user)) == 3


async def test_import_drops_the_stats_cache(session):
    from src import food_stats
    from src.db import repo

    user = await repo.get_or_create_user(session, 502)
    user.food_stats_at = datetime(2026, 9, 14, 10, 0)
    result = parse_cgm_csv(_libre("Libre 3,ABC1,15-01-2024 08:12,0,5.4,,,\n"))
    await repo.save_glucose_bulk(session, user, result.readings, source=LIBREVIEW)
    assert user.food_stats_at is None
    assert (
        await repo.load_food_stats(
            session, user, key_type="tag", window="1h", ttl=food_stats.CACHE_TTL
        )
        is None
    )
