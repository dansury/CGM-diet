"""Settings parsing and the pure helpers behind the handlers."""

from __future__ import annotations

import pytest

from src.config import ConfigError, Settings, load_settings, normalize_database_url, reset_cache
from src.handlers.common import _apply_setting, _parse_window
from src.ingest.correction import apply_meal_correction
from src.vision.schemas import ItemDraft, MealDraft


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("postgres+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite:///data/x.db", "sqlite+aiosqlite:///data/x.db"),
        ("sqlite+aiosqlite:///data/x.db", "sqlite+aiosqlite:///data/x.db"),
    ],
)
def test_database_url_normalisation(raw, expected):
    assert normalize_database_url(raw) == expected


def test_windows_are_parsed_from_the_environment(monkeypatch):
    monkeypatch.setenv("WINDOW_1H", "30-75")
    reset_cache()
    assert load_settings(refresh=True).window_1h == (30, 75)


def test_malformed_window_is_rejected(monkeypatch):
    monkeypatch.setenv("WINDOW_1H", "90-30")
    reset_cache()
    with pytest.raises(ConfigError):
        load_settings(refresh=True)


def test_mock_mode_is_implied_by_a_missing_key():
    assert Settings(llm_mock=True).vision_available
    assert not Settings().stt_available


def test_parse_window_helper():
    assert _parse_window("45-90") == (45, 90)
    with pytest.raises(ValueError):
        _parse_window("90-45")


class _FakeUser:
    tz = "UTC"
    glucose_unit = "mmol/L"
    window_1h_start = 45
    window_1h_end = 90
    window_2h_start = 90
    window_2h_end = 150
    baseline_window = 20


def test_apply_setting_validates_input():
    user = _FakeUser()
    assert "Europe/Moscow" in _apply_setting(user, "tz", "Europe/Moscow")
    assert user.tz == "Europe/Moscow"
    _apply_setting(user, "unit", "мг/дл")
    assert user.glucose_unit == "mg/dL"
    _apply_setting(user, "window1", "30-80")
    assert (user.window_1h_start, user.window_1h_end) == (30, 80)
    with pytest.raises(ValueError):
        _apply_setting(user, "tz", "Nowhere/Nothing")
    with pytest.raises(ValueError):
        _apply_setting(user, "baseline", "500")


def test_manual_edit_rescales_nutrients_to_the_new_portion():
    old = MealDraft(
        title="Обед",
        items=[ItemDraft(name="рис", portion_g=100, kcal=130, carbs_g=28, tags=["white_rice"])],
    )
    new = apply_meal_correction(old, "рис 200, курица 120").draft
    rice = next(i for i in new.items if i.name == "рис")
    assert rice.portion_g == 200
    assert rice.kcal == 260  # doubled with the portion
    assert rice.tags == ["white_rice"]  # tags survive the correction
    chicken = next(i for i in new.items if i.name == "курица")
    assert chicken.portion_g == 120
    assert chicken.kcal  # добавленная позиция получает числа, а не нули
    assert new.notes.startswith("учтена ваша правка")


def test_router_tree_builds():
    from src.handlers import build_router

    router = build_router()
    assert [r.name for r in router.sub_routers] == [
        "admin",
        "admin_panel",
        "common",
        "onboarding",
        "reports",
        "sleep",
        "features",
        "goals",
        "sugar",
        "plate",
        "labs",
        "wellbeing",
        "body",
        "workout",
        "dictionary",
        "meds",
        "confirm",
        "intake",
        "errors",
    ]
