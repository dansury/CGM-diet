"""Catalogue of currently-free LLMs (shir-man).

Free models come and go and their daily quotas move; a static list in the repo
would be wrong within a week. The catalogue is fetched from
`shir-man.com/api/free-llm/top-models`, cached on disk for a day, and used for
two things: the 🆓 section of the owner's `/model` menu, and the 429 fallback
chain (`src/llm/fallback.py`).

Failing to reach it is never fatal: the last cache wins, and with no cache at
all the bot simply has no free alternates. See `spec/models.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.logging_setup import get_logger
from src.paths import repo_path

log = get_logger("llm.free_catalog")

CATALOG_URL = "https://shir-man.com/api/free-llm/top-models"
CACHE_PATH = repo_path("data", "free_models.json")
DEFAULT_MAX_AGE_H = 24
REQUEST_TIMEOUT_S = 20.0


class FreeCatalogUnavailable(RuntimeError):
    """No fresh answer and no cache to fall back on."""


@dataclass(frozen=True, slots=True)
class FreeModel:
    id: str
    label: str
    provider: str = "openrouter"
    context_tokens: int = 0
    daily_quota: int | None = None
    notes: str | None = None


def _parse_models(payload: Any) -> list[FreeModel]:
    items = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    out: list[FreeModel] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        out.append(
            FreeModel(
                id=model_id,
                label=str(item.get("label") or item.get("name") or model_id),
                provider=str(item.get("provider") or "openrouter"),
                context_tokens=int(item.get("context_tokens") or item.get("context") or 0),
                daily_quota=item.get("daily_quota"),
                notes=item.get("notes") or item.get("note"),
            )
        )
    out.sort(key=lambda m: (-(m.daily_quota or 0), m.id))
    return out


def _read_cache() -> tuple[list[FreeModel], datetime | None]:
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    stamp: datetime | None = None
    try:
        stamp = datetime.fromisoformat(str(raw.get("fetched_at"))).astimezone(UTC)
    except (TypeError, ValueError):
        stamp = None
    return _parse_models(raw), stamp


def _write_cache(models: list[FreeModel]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "context_tokens": m.context_tokens,
                "daily_quota": m.daily_quota,
                "notes": m.notes,
            }
            for m in models
        ],
    }
    tmp = CACHE_PATH.with_suffix(".tmp")
    # atomic: a half-written cache must never be parsed on the next start
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CACHE_PATH)


async def refresh_free_models(*, client: Any | None = None) -> list[FreeModel]:
    import httpx

    owns = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=10.0))
    try:
        response = await http.get(CATALOG_URL)
        response.raise_for_status()
        models = _parse_models(response.json())
    except Exception as exc:  # network, HTTP, JSON — all handled the same way
        cached, _ = _read_cache()
        if cached:
            log.warning("free catalog refresh failed (%s); using cache", exc)
            return cached
        raise FreeCatalogUnavailable(str(exc)) from exc
    finally:
        if owns:
            await http.aclose()
    if models:
        _write_cache(models)
    return models


async def load_free_models(
    *,
    max_age_h: int = DEFAULT_MAX_AGE_H,
    allow_refresh: bool = True,
    client: Any | None = None,
) -> list[FreeModel]:
    cached, stamp = _read_cache()
    if cached and stamp is not None:
        age_h = (datetime.now(UTC) - stamp).total_seconds() / 3600
        if age_h < max_age_h:
            return cached
    if not allow_refresh:
        return cached
    try:
        return await refresh_free_models(client=client)
    except FreeCatalogUnavailable:
        return cached


__all__ = [
    "CACHE_PATH",
    "CATALOG_URL",
    "DEFAULT_MAX_AGE_H",
    "FreeCatalogUnavailable",
    "FreeModel",
    "load_free_models",
    "refresh_free_models",
]
