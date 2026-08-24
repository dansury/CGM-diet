"""Samsung Health / Health Connect relay: tokens, parsing, endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.health.samsung import HealthSyncError, make_token, parse_samples, verify_token

SECRET = "s3cret"


def test_token_is_stable_per_user_and_differs_between_users():
    assert make_token(111, SECRET) == make_token(111, SECRET)
    assert make_token(111, SECRET) != make_token(222, SECRET)


def test_token_changes_with_the_secret():
    assert make_token(111, SECRET) != make_token(111, "other")


def test_verify_rejects_a_wrong_or_empty_token():
    assert verify_token(111, make_token(111, SECRET), SECRET)
    assert not verify_token(111, "nope", SECRET)
    assert not verify_token(111, "", SECRET)


def test_verify_fails_closed_without_a_configured_secret():
    assert not verify_token(111, "anything", "")


def test_parse_iso_and_epoch_timestamps():
    samples = parse_samples(
        {
            "samples": [
                {"kind": "steps", "start": "2026-08-24T08:00:00Z", "end": "2026-08-24T08:15:00Z", "steps": 420},
                {"kind": "steps", "start": 1756022400000, "steps": 100},
            ]
        }
    )
    assert len(samples) == 2
    assert samples[0].steps == 420
    assert samples[0].start_at == datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
    assert samples[1].end_at > samples[1].start_at  # default 15-minute bucket


def test_unknown_kinds_are_dropped():
    samples = parse_samples({"samples": [{"kind": "mood", "start": "2026-08-24T08:00:00Z"}]})
    assert samples == []


def test_bad_payloads_are_rejected():
    with pytest.raises(HealthSyncError):
        parse_samples({"samples": "nope"})
    with pytest.raises(HealthSyncError):
        parse_samples({"samples": [{"kind": "steps"}]})
    with pytest.raises(HealthSyncError):
        parse_samples(
            {"samples": [{"kind": "steps", "start": "2026-08-24T09:00:00Z", "end": "2026-08-24T08:00:00Z"}]}
        )


def test_workout_fields_are_kept():
    samples = parse_samples(
        {
            "source": "health_connect",
            "samples": [
                {
                    "kind": "workout",
                    "start": "2026-08-24T18:00:00Z",
                    "end": "2026-08-24T18:40:00Z",
                    "kcal": 210,
                    "distance_m": 3200,
                    "avg_hr": 118,
                    "external_id": "w-1",
                }
            ],
        }
    )
    sample = samples[0]
    assert (sample.kcal, sample.distance_m, sample.avg_hr) == (210.0, 3200.0, 118.0)
    assert sample.source == "health_connect"
    assert sample.external_id == "w-1"
