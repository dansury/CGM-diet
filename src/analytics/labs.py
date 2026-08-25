"""Lab markers outside the document's own reference → food sources.

The line this module must not cross is in `spec/clinical.md`: a marker outside
the reference printed *in the user's own document* is a fact worth naming and
showing to a doctor. It is never a diagnosis, never a deficiency verdict and
never a supplement plan. All this module adds on top of the fact is a list of
foods that are known sources of the matching nutrient — an ordinary nutrition
recommendation, which the constitution allows.

The catalogue lives in `config/nutrient_foods.json`; see `spec/labs.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from src.analytics.tags import normalize_name
from src.logging_setup import get_logger
from src.paths import repo_path

log = get_logger("analytics.labs")

CATALOG_PATH = repo_path("config", "nutrient_foods.json")


@dataclass(frozen=True, slots=True)
class LabValue:
    """One stored marker, reshaped for analytics (no ORM below this line)."""

    marker: str
    taken_at: datetime
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    flag: str | None = None  # low|normal|high
    panel: str | None = None

    @property
    def display(self) -> str:
        if self.value_text:
            return self.value_text
        if self.value is None:
            return "—"
        return f"{self.value:g}"


@dataclass(frozen=True, slots=True)
class Nutrient:
    key: str
    label: str
    markers: tuple[str, ...]
    foods: tuple[str, ...]
    note: str = ""
    when: str = "low"  # какая сторона референса связана с питанием


@dataclass(frozen=True, slots=True)
class FoodHint:
    """Marker outside the reference + foods that carry the nutrient."""

    nutrient: Nutrient
    value: LabValue
    direction: str  # low|high

    @property
    def foods(self) -> tuple[str, ...]:
        return self.nutrient.foods


@dataclass(frozen=True, slots=True)
class LabReview:
    hints: list[FoodHint] = field(default_factory=list)
    out_of_range: list[LabValue] = field(default_factory=list)
    n_markers: int = 0


@lru_cache(maxsize=1)
def load_catalog() -> tuple[Nutrient, ...]:
    """Read the nutrient catalogue; a missing file degrades to «no hints»."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — reference data, never fatal
        log.warning("nutrient catalogue unavailable (%s); labs stay descriptive", exc)
        return ()
    out: list[Nutrient] = []
    for key, entry in (raw.get("nutrients") or {}).items():
        out.append(
            Nutrient(
                key=key,
                label=entry.get("label") or key,
                markers=tuple(normalize_name(m) for m in entry.get("markers") or ()),
                foods=tuple(entry.get("foods") or ()),
                note=entry.get("note") or "",
                when=entry.get("when") or "low",
            )
        )
    return tuple(out)


def reset_catalog() -> None:
    """Test hook: re-read `config/nutrient_foods.json`."""
    load_catalog.cache_clear()


def match_nutrient(marker: str) -> Nutrient | None:
    """Marker name → nutrient. Longest match wins: «железо» ⊂ «железо связ.»."""
    norm = normalize_name(marker)
    if not norm:
        return None
    best: tuple[int, Nutrient] | None = None
    for nutrient in load_catalog():
        for alias in nutrient.markers:
            if not alias:
                continue
            if norm == alias or alias in norm or norm in alias:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), nutrient)
    return best[1] if best else None


def direction(value: LabValue) -> str | None:
    """Which side of the document's own reference the value fell on."""
    if value.flag in {"low", "high"}:
        return value.flag
    if value.value is None:
        return None
    if value.ref_low is not None and value.value < value.ref_low:
        return "low"
    if value.ref_high is not None and value.value > value.ref_high:
        return "high"
    return None


def latest_values(values: list[LabValue]) -> list[LabValue]:
    """Last measurement per marker, newest first: an old panel is history."""
    best: dict[str, LabValue] = {}
    for value in values:
        key = normalize_name(value.marker)
        current = best.get(key)
        if current is None or value.taken_at > current.taken_at:
            best[key] = value
    return sorted(best.values(), key=lambda v: v.taken_at, reverse=True)


def review(values: list[LabValue]) -> LabReview:
    """Latest markers → what is outside the reference and what food matches."""
    latest = latest_values(values)
    out_of_range: list[LabValue] = []
    hints: list[FoodHint] = []
    seen: set[str] = set()
    for value in latest:
        side = direction(value)
        if side is None:
            continue
        out_of_range.append(value)
        nutrient = match_nutrient(value.marker)
        if nutrient is None or nutrient.when != side or nutrient.key in seen:
            continue
        seen.add(nutrient.key)
        hints.append(FoodHint(nutrient=nutrient, value=value, direction=side))
    return LabReview(hints=hints, out_of_range=out_of_range, n_markers=len(latest))


__all__ = [
    "CATALOG_PATH",
    "FoodHint",
    "LabReview",
    "LabValue",
    "Nutrient",
    "direction",
    "latest_values",
    "load_catalog",
    "match_nutrient",
    "reset_catalog",
    "review",
]
