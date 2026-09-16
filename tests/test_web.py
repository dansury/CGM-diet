"""FastAPI surface: /health probe and the phone health relay."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.health.sync import make_token
from src.web.app import create_app

SECRET = "relay-secret"


@pytest.fixture
def client(engine):
    settings = Settings(health_sync_secret=SECRET, telegram_bot_token="", app_env="test")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_endpoint_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"]["ok"] is True


def test_samsung_sync_requires_a_valid_token(client):
    payload = {"tg_id": 555, "samples": []}
    assert client.post("/health/samsung", json=payload).status_code == 403
    assert (
        client.post("/health/samsung", json=payload, headers={"X-Health-Token": "bad"}).status_code
        == 403
    )


def test_samsung_sync_requires_tg_id(client):
    response = client.post(
        "/health/samsung", json={"samples": []}, headers={"X-Health-Token": "x"}
    )
    assert response.status_code == 400


def test_samsung_sync_ingests_and_deduplicates(client):
    payload = {
        "tg_id": 555,
        "samples": [
            {
                "kind": "steps",
                "start": "2026-08-24T08:00:00Z",
                "end": "2026-08-24T08:15:00Z",
                "steps": 420,
                "external_id": "s-1",
            }
        ],
    }
    headers = {"X-Health-Token": make_token(555, SECRET)}
    first = client.post("/health/samsung", json=payload, headers=headers)
    assert first.status_code == 200 and first.json()["accepted"] == 1
    second = client.post("/health/samsung", json=payload, headers=headers)
    assert second.json()["accepted"] == 0


def test_samsung_sync_rejects_a_malformed_batch(client):
    response = client.post(
        "/health/samsung",
        json={"tg_id": 555, "samples": [{"kind": "steps"}]},
        headers={"X-Health-Token": make_token(555, SECRET)},
    )
    assert response.status_code == 400


def test_telegram_webhook_is_disabled_without_a_token(client):
    assert client.post("/telegram/webhook", json={}).status_code in (404, 503)


def test_both_relay_paths_accept_the_same_batch(client):
    """Мост, который уже стоит на телефоне, сам себя не обновит: старый путь
    остаётся навсегда (`spec/health_sync.md` § HTTP)."""
    headers = {"X-Health-Token": make_token(777, SECRET)}
    body = {
        "tg_id": 777,
        "source": "healthkit",
        "samples": [
            {
                "kind": "stepCount",
                "start": "2026-09-14T08:00:00Z",
                "end": "2026-09-14T08:15:00Z",
                "steps": 300,
                "external_id": "path-1",
            }
        ],
    }
    new_path = client.post("/health/sync", json=body, headers=headers)
    assert new_path.status_code == 200
    assert new_path.json() == {"accepted": 1, "received": 1}

    body["samples"][0]["external_id"] = "path-2"
    old_path = client.post("/health/samsung", json=body, headers=headers)
    assert old_path.status_code == 200
    assert old_path.json() == {"accepted": 1, "received": 1}
