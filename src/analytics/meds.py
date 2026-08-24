"""Medications as *context* for the numbers, never as their explanation.

Two questions this module answers, both descriptive:

* **coverage** — how many of the excursions behind a food verdict fell inside
  the window after a dose. A drug taken with every dinner is a confounder the
  user must see, otherwise «после риса сахар выше» is a claim about the drug;
* **symptom links** — a symptom the user recorded after a dose that the open
  reference also lists for that drug. This is a lookup coincidence, reported
  as such (`spec/meds.md` § Формулировки).

No ORM, no aiogram: the layer takes `MedicationLike` / `CheckinLike` /
`Excursion` and returns dataclasses (`CLAUDE.md` #7).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.analytics.symptoms import CheckinLike
from src.analytics.windows import Excursion

#: how long after a dose a record is considered "in the window". Deliberately
#: wide: most oral drugs act for hours, and a narrower window would silently
#: hide the confounder rather than report it.
DEFAULT_WINDOW_H = 8

#: a single coincidence is noise; below this nothing is shown (constitution, II)
MIN_HITS = 2


@dataclass(frozen=True, slots=True)
class MedicationLike:
    id: int | None
    taken_at: datetime
    name: str
    slug: str = ""

    @property
    def key(self) -> str:
        return self.slug or self.name


@dataclass(frozen=True, slots=True)
class DoseWindow:
    slug: str
    name: str
    start: datetime
    end: datetime

    def covers(self, at: datetime) -> bool:
        return self.start <= at <= self.end


@dataclass(slots=True)
class Coverage:
    slug: str
    name: str
    n_covered: int
    n_total: int

    @property
    def share(self) -> float:
        return self.n_covered / self.n_total if self.n_total else 0.0


@dataclass(slots=True)
class SymptomLink:
    slug: str
    name: str
    symptom: str
    effect_ru: str
    n_after_dose: int
    n_total: int
    examples: list[datetime] = field(default_factory=list)


def dose_windows(
    meds: Sequence[MedicationLike], *, hours: int = DEFAULT_WINDOW_H
) -> list[DoseWindow]:
    span = timedelta(hours=hours)
    return [
        DoseWindow(slug=m.key, name=m.name, start=m.taken_at, end=m.taken_at + span)
        for m in sorted(meds, key=lambda m: m.taken_at)
    ]


def coverage(
    excursions: Sequence[Excursion],
    meds: Sequence[MedicationLike],
    *,
    hours: int = DEFAULT_WINDOW_H,
    min_hits: int = MIN_HITS,
) -> list[Coverage]:
    """Per drug: in how many usable excursions was a dose already on board."""
    usable = [e for e in excursions if e.usable]
    if not usable or not meds:
        return []
    windows = dose_windows(meds, hours=hours)
    hits: dict[str, Coverage] = {}
    for window in windows:
        hits.setdefault(
            window.slug,
            Coverage(slug=window.slug, name=window.name, n_covered=0, n_total=len(usable)),
        )
    for excursion in usable:
        seen: set[str] = set()
        for window in windows:
            if window.slug in seen or not window.covers(excursion.eaten_at):
                continue
            seen.add(window.slug)
            hits[window.slug].n_covered += 1
    out = [row for row in hits.values() if row.n_covered >= min_hits]
    out.sort(key=lambda row: (-row.n_covered, row.name))
    return out


def symptom_links(
    meds: Sequence[MedicationLike],
    checkins: Sequence[CheckinLike],
    lookup: Callable[[str], Sequence[object]],
    *,
    hours: int = DEFAULT_WINDOW_H,
    min_hits: int = MIN_HITS,
) -> list[SymptomLink]:
    """Symptoms recorded after a dose that the reference also lists for the drug.

    `lookup(name) -> [SymptomMatch]` is injected (`meds.side_effects.match_symptoms`
    bound to the user's symptom labels) so this module stays free of file and
    dataset concerns.
    """
    if not meds or not checkins:
        return []
    windows = dose_windows(meds, hours=hours)
    totals: dict[str, int] = {}
    for checkin in checkins:
        for label in checkin.symptoms:
            totals[label] = totals.get(label, 0) + 1

    counters: dict[tuple[str, str], SymptomLink] = {}
    for checkin in checkins:
        if not checkin.symptoms:
            continue
        covering = [w for w in windows if w.covers(checkin.at)]
        if not covering:
            continue
        for window in {w.slug: w for w in covering}.values():
            matches = lookup(window.name)
            for match in matches:
                symptom = getattr(match, "symptom", "")
                effect = getattr(match, "effect", None)
                if symptom not in checkin.symptoms:
                    continue
                key = (window.slug, symptom)
                link = counters.get(key)
                if link is None:
                    link = counters[key] = SymptomLink(
                        slug=window.slug,
                        name=window.name,
                        symptom=symptom,
                        effect_ru=getattr(effect, "label", "") or "",
                        n_after_dose=0,
                        n_total=totals.get(symptom, 0),
                    )
                link.n_after_dose += 1
                if len(link.examples) < 3:
                    link.examples.append(checkin.at)

    out = [link for link in counters.values() if link.n_after_dose >= min_hits]
    out.sort(key=lambda link: (-link.n_after_dose, link.name, link.symptom))
    return out


__all__ = [
    "DEFAULT_WINDOW_H",
    "MIN_HITS",
    "Coverage",
    "DoseWindow",
    "MedicationLike",
    "SymptomLink",
    "coverage",
    "dose_windows",
    "symptom_links",
]
