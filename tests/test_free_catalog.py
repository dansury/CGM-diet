"""Free-model catalogue parsing and its scheduled refresh (T052).

`spec/models.md`. The catalogue used to load only once, at process start
(`bot.prepare_runtime`); nothing kept `set_free_alternates` current for a bot
that runs for days without a restart — see `scheduler.run_free_catalog_refresh`.
"""

from __future__ import annotations

from src.config import load_settings
from src.llm.free_catalog import FreeModel, _parse_models


def test_models_are_parsed_and_sorted_by_daily_quota():
    payload = {
        "models": [
            {"id": "a/one", "label": "One", "daily_quota": 10},
            {"id": "a/two", "name": "Two", "daily_quota": 50},
            {"id": ""},  # no id -> dropped
            "not a dict",  # garbage row -> dropped
        ]
    }
    models = _parse_models(payload)
    assert [m.id for m in models] == ["a/two", "a/one"]
    assert models[1].label == "One"


def test_a_bare_list_payload_is_accepted_too():
    assert [m.id for m in _parse_models([{"id": "x"}])] == ["x"]


async def test_refresh_is_a_noop_when_fallback_is_disabled_or_mocked(monkeypatch):
    from src import scheduler

    calls: list[int] = []

    async def fake_load(**kwargs):
        calls.append(1)
        return [FreeModel(id="a/one", label="One")]

    monkeypatch.setattr("src.llm.free_catalog.load_free_models", fake_load)

    monkeypatch.setenv("FREE_FALLBACK_ENABLED", "0")
    monkeypatch.setenv("LLM_MOCK", "false")
    load_settings(refresh=True)
    assert await scheduler.run_free_catalog_refresh() == 0

    monkeypatch.setenv("FREE_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("LLM_MOCK", "true")  # the default the whole suite runs under
    load_settings(refresh=True)
    assert await scheduler.run_free_catalog_refresh() == 0

    assert calls == []  # never even asked — the gate is checked first


async def test_refresh_updates_the_live_fallback_pool(monkeypatch):
    from src import scheduler

    models = [FreeModel(id="a/one", label="One"), FreeModel(id="a/two", label="Two")]

    async def fake_load(**kwargs):
        return models

    monkeypatch.setattr("src.llm.free_catalog.load_free_models", fake_load)

    captured: dict[str, list[str]] = {}
    monkeypatch.setattr("src.llm.set_free_alternates", lambda ids: captured.setdefault("ids", ids))

    monkeypatch.setenv("FREE_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("LLM_MOCK", "false")
    load_settings(refresh=True)

    assert await scheduler.run_free_catalog_refresh() == 2
    assert captured["ids"] == ["a/one", "a/two"]
