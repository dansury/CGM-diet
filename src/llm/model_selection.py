"""Which model serves which slot, and who gets to change it.

Two levels, priority **slot > global**, and below both the value from
`Settings` (i.e. the env). Each level lives under its own key, so «одну модель
на всё» never silently overwrites a slot tuned by hand.

The resolved mapping is kept in a process cache: `src/vision/recognize.py`
asks `current(slot)` on every call and must not touch the DB for it
(`CLAUDE.md` #7). The cache is filled at startup and after every change.
See `spec/models.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from src.config import Settings, load_settings
from src.logging_setup import get_logger
from src.paths import repo_path

log = get_logger("llm.model_selection")

CATALOG_PATH = repo_path("config", "models.json")

SLOTS: tuple[str, ...] = ("vision", "text", "stt")

SLOT_LABELS: dict[str, str] = {
    "vision": "фото (еда, этикетки, экран CGM, анализы, лекарства)",
    "text": "текст (разбор блюда, симптомы)",
    "stt": "голосовые",
}

KEY_GLOBAL = "model_global"
KEY_SLOTS = "models"

Level = str  # slot|global|env


class UnknownSlot(ValueError):
    """A slot outside `SLOTS`."""


@dataclass(frozen=True, slots=True)
class CatalogModel:
    id: str
    label: str
    tier: str = "paid"  # free|paid
    usd_per_1k: float | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class Resolved:
    model_id: str
    level: Level


_cache: dict[str, str] = {}


# ---------------------------------------------------------------- catalogue

@lru_cache(maxsize=1)
def load_catalog() -> dict[str, tuple[CatalogModel, ...]]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("model catalog unavailable (%s)", exc)
        return {}
    out: dict[str, tuple[CatalogModel, ...]] = {}
    for slot, block in raw.items():
        if slot.startswith("_") or not isinstance(block, dict):
            continue
        out[slot] = tuple(
            CatalogModel(
                id=item["id"],
                label=item.get("label") or item["id"],
                tier=item.get("tier") or "paid",
                usd_per_1k=item.get("usd_per_1k"),
                note=item.get("note") or "",
            )
            for item in block.get("models") or ()
            if item.get("id")
        )
    return out


def candidates(slot: str) -> tuple[CatalogModel, ...]:
    """What the owner is offered for this slot."""
    if slot not in SLOTS:
        raise UnknownSlot(slot)
    return load_catalog().get(slot, ())


def is_known(slot: str, model_id: str) -> bool:
    return any(model.id == model_id for model in candidates(slot))


def reset_catalog_cache() -> None:
    """Test hook: re-read `config/models.json`."""
    load_catalog.cache_clear()


# ---------------------------------------------------------------- resolution

def env_default(slot: str, settings: Settings | None = None) -> str:
    s = settings or load_settings()
    return {"vision": s.vision_model, "text": s.text_model, "stt": s.stt_model}.get(slot, "")


def resolve(slot: str, stored: dict[str, Any], *, settings: Settings | None = None) -> Resolved:
    """`stored` is the raw settings_kv snapshot: {model_global, models}."""
    if slot not in SLOTS:
        raise UnknownSlot(slot)
    per_slot = (stored.get(KEY_SLOTS) or {}) if isinstance(stored, dict) else {}
    chosen = per_slot.get(slot)
    if chosen:
        return Resolved(str(chosen), "slot")
    glob = stored.get(KEY_GLOBAL) if isinstance(stored, dict) else None
    # A global pick is only honoured where the slot's catalogue lists it:
    # a text model cannot transcribe an .ogg, whoever picked it "for everything".
    if glob and is_known(slot, str(glob)):
        return Resolved(str(glob), "global")
    return Resolved(env_default(slot, settings), "env")


def resolve_all(stored: dict[str, Any], *, settings: Settings | None = None) -> dict[str, Resolved]:
    return {slot: resolve(slot, stored, settings=settings) for slot in SLOTS}


# ---------------------------------------------------------------- process cache

def refresh(mapping: dict[str, str]) -> None:
    _cache.clear()
    _cache.update({k: v for k, v in mapping.items() if v})


def current(slot: str) -> str | None:
    """The active model id, or None → the client falls back to its own default."""
    return _cache.get(slot) or None


def snapshot() -> dict[str, str]:
    return dict(_cache)


def reset() -> None:
    _cache.clear()


__all__ = [
    "CATALOG_PATH",
    "KEY_GLOBAL",
    "KEY_SLOTS",
    "SLOTS",
    "SLOT_LABELS",
    "CatalogModel",
    "Resolved",
    "UnknownSlot",
    "candidates",
    "current",
    "env_default",
    "is_known",
    "load_catalog",
    "refresh",
    "reset",
    "reset_catalog_cache",
    "resolve",
    "resolve_all",
    "snapshot",
]
