"""Phone health ingest — Health Connect (Android) and HealthKit (iOS).

Neither platform gives third parties a server-to-server API: the data leaves
the phone or not at all. So the integration is a thin relay — something on the
device (the Android bridge app, an iOS Shortcuts automation) reads the local
store and POSTs batches here. One payload shape serves both, because the only
platform-specific part is the vocabulary of sample names, and that is mapped in
`KIND_ALIASES`.

Auth is a per-user token derived from `HEALTH_SYNC_SECRET`, so the phone never
carries the server secret and a leaked token exposes exactly one user.
See `spec/health_sync.md`.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from src.db.models import ActivitySample

KINDS = {"steps", "workout", "sleep", "heart_rate"}

#: What the two platforms call the same four things. Health Connect names come
#: through the bridge already normalised; HealthKit names arrive raw when the
#: sender is an iOS Shortcut, which has no place to rename them.
KIND_ALIASES = {
    # HealthKit / Shortcuts
    "stepcount": "steps",
    "step_count": "steps",
    "hkquantitytypeidentifierstepcount": "steps",
    "sleepanalysis": "sleep",
    "sleep_analysis": "sleep",
    "hkcategorytypeidentifiersleepanalysis": "sleep",
    "heartrate": "heart_rate",
    "hkquantitytypeidentifierheartrate": "heart_rate",
    "activeenergyburned": "workout",
    "hkworkouttypeidentifier": "workout",
    "workouts": "workout",
    # Health Connect spellings that are not ours
    "stepsrecord": "steps",
    "exercisesession": "workout",
    "sleepsession": "sleep",
    "heartraterecord": "heart_rate",
}

#: Platforms the relay is told about, for the `source` column. Anything else is
#: stored as sent — the column is a label, not a gate.
SOURCES = {"health_connect", "healthkit", "samsung_health", "shortcuts"}
DEFAULT_BUCKET_MIN = 15
MAX_SAMPLES_PER_REQUEST = 5000


class HealthSyncError(ValueError):
    """Malformed payload — answered with 400, never persisted."""


def make_token(tg_id: int, secret: str) -> str:
    """Stable per-user token; changing HEALTH_SYNC_SECRET revokes every token."""
    if not secret:
        raise HealthSyncError("HEALTH_SYNC_SECRET is not configured")
    digest = hmac.new(secret.encode(), str(tg_id).encode(), sha256).hexdigest()
    return digest[:32]


def verify_token(tg_id: int, token: str, secret: str) -> bool:
    try:
        expected = make_token(tg_id, secret)
    except HealthSyncError:
        return False
    return hmac.compare_digest(expected, (token or "").strip())


def normalize_kind(raw: Any) -> str | None:
    """Platform vocabulary → our four kinds. None = not something we store."""
    key = str(raw or "steps").strip().lower().replace(" ", "")
    if key in KINDS:
        return key
    return KIND_ALIASES.get(key.replace("-", "_")) or KIND_ALIASES.get(key)


def _parse_stamp(raw: Any) -> datetime:
    if isinstance(raw, (int, float)):  # epoch millis or seconds
        seconds = raw / 1000.0 if raw > 1e11 else float(raw)
        return datetime.fromtimestamp(seconds, tz=UTC)
    if not isinstance(raw, str) or not raw.strip():
        raise HealthSyncError("timestamp is required")
    text = raw.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HealthSyncError(f"bad timestamp: {raw}") from exc
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class SyncResult:
    accepted: int
    skipped: int


def parse_samples(payload: dict[str, Any]) -> list[ActivitySample]:
    """Validate a relay batch into unsaved `ActivitySample` rows."""
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        raise HealthSyncError("`samples` must be a list")
    if len(raw_samples) > MAX_SAMPLES_PER_REQUEST:
        raise HealthSyncError(f"too many samples (max {MAX_SAMPLES_PER_REQUEST})")
    source = str(payload.get("source") or "health_connect")[:32]
    out: list[ActivitySample] = []
    for raw in raw_samples:
        if not isinstance(raw, dict):
            continue
        kind = normalize_kind(raw.get("kind") or raw.get("type"))
        if kind is None:
            continue
        start = _parse_stamp(raw.get("start") or raw.get("start_at"))
        end_raw = raw.get("end") or raw.get("end_at")
        end = _parse_stamp(end_raw) if end_raw else start + timedelta(minutes=DEFAULT_BUCKET_MIN)
        if end < start:
            raise HealthSyncError("end must not precede start")
        out.append(
            ActivitySample(
                external_id=(str(raw.get("external_id") or raw.get("id") or "") or None),
                kind=kind,
                start_at=start,
                end_at=end,
                steps=_int(raw.get("steps")),
                distance_m=_float(raw.get("distance_m")),
                kcal=_float(raw.get("kcal") or raw.get("calories")),
                avg_hr=_float(raw.get("avg_hr") or raw.get("heart_rate")),
                source=source,
                payload=raw if len(str(raw)) < 2000 else None,
            )
        )
    return out


__all__ = [
    "DEFAULT_BUCKET_MIN",
    "HealthSyncError",
    "KINDS",
    "KIND_ALIASES",
    "MAX_SAMPLES_PER_REQUEST",
    "SOURCES",
    "SyncResult",
    "make_token",
    "normalize_kind",
    "parse_samples",
    "verify_token",
]
