"""Open side-effect reference: ChSe-Decagon_monopharmacy (SNAP BioSNAP).

The file is a flat CSV — `STITCH, Individual Side Effect, Side Effect Name` —
mapping one drug (a STITCH/PubChem id) to the effects reported for it alone.
It is a *reference*, not a verdict: the only thing the bot does with it is
say «этот симптом числится в справочнике для этого препарата», which is a
lookup, not a causal claim (constitution, principles I and II).

The full archive (~10 MB gzipped) is not committed. `scripts/fetch_side_effects.py`
downloads it into `data/side_effects/`; without it the module falls back to the
committed sample so the bot, the tests and CI all still work.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.analytics.tags import normalize_name
from src.logging_setup import get_logger
from src.meds.catalog import find_drug, normalize_drug
from src.paths import repo_path

log = get_logger("meds.side_effects")

DATASET_PATH = repo_path("data", "side_effects", "ChSe-Decagon_monopharmacy.csv.gz")
SAMPLE_PATH = repo_path("seeds", "side_effects_sample.csv")
TRANSLATIONS_PATH = repo_path("config", "side_effects_ru.json")

DATASET_URL = (
    "https://snap.stanford.edu/biodata/datasets/10018/files/"
    "ChSe-Decagon_monopharmacy.csv.gz"
)


@dataclass(frozen=True, slots=True)
class SideEffect:
    cui: str
    name_en: str
    name_ru: str

    @property
    def label(self) -> str:
        return self.name_ru or self.name_en


@dataclass(frozen=True, slots=True)
class SymptomMatch:
    symptom: str
    effect: SideEffect


@dataclass(frozen=True, slots=True)
class DatasetStatus:
    path: str
    rows: int
    drugs: int
    sample: bool


def _cid_key(raw: str) -> int | None:
    """`CID000004091`, `CID1000004091`, `4091` → 4091.

    STITCH prefixes the PubChem id and pads it; matching on the numeric tail
    survives every spelling the dataset and the catalogue use.
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    value = int(digits)
    # STITCH stereo/flat ids are the CID plus a 1-digit prefix in a 9-digit field
    while value > 100_000_000:
        value -= 100_000_000
    return value or None


@lru_cache(maxsize=1)
def _translations() -> dict[str, str]:
    try:
        raw = json.loads(TRANSLATIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("side-effect translations unavailable (%s)", exc)
        return {}
    return {normalize_name(k): v for k, v in (raw.get("effects") or {}).items()}


def _open(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def _parse(path: Path) -> dict[int, tuple[SideEffect, ...]]:
    ru = _translations()
    buckets: dict[int, list[SideEffect]] = {}
    with _open(path) as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 3:
                continue
            cid = _cid_key(row[0])
            if cid is None:  # header line
                continue
            name_en = row[2].strip()
            if not name_en:
                continue
            effect = SideEffect(
                cui=row[1].strip(),
                name_en=name_en,
                name_ru=ru.get(normalize_name(name_en), ""),
            )
            buckets.setdefault(cid, []).append(effect)
    return {cid: tuple(effects) for cid, effects in buckets.items()}


@lru_cache(maxsize=1)
def _dataset() -> tuple[dict[int, tuple[SideEffect, ...]], Path, bool]:
    path, sample = (DATASET_PATH, False) if DATASET_PATH.exists() else (SAMPLE_PATH, True)
    if not path.exists():
        log.warning("no side-effect reference at %s", path)
        return {}, path, True
    try:
        data = _parse(path)
    except (OSError, ValueError, csv.Error) as exc:
        log.warning("side-effect reference unreadable (%s)", exc)
        return {}, path, sample
    if sample:
        log.info(
            "using the bundled side-effect sample; run `python -m scripts.fetch_side_effects` "
            "for the full dataset"
        )
    return data, path, sample


def load_side_effects(*, refresh: bool = False) -> dict[int, tuple[SideEffect, ...]]:
    if refresh:
        reset_cache()
    return _dataset()[0]


def dataset_status() -> DatasetStatus:
    data, path, sample = _dataset()
    return DatasetStatus(
        path=str(path),
        rows=sum(len(v) for v in data.values()),
        drugs=len(data),
        sample=sample,
    )


def side_effects_for(name: str) -> tuple[SideEffect, ...]:
    """Everything the reference lists for this drug; `()` when it is unknown."""
    entry = find_drug(name)
    if entry is None:
        return ()
    cid = _cid_key(entry.cid)
    if cid is None:
        return ()
    return load_side_effects().get(cid, ())


def match_symptoms(name: str, labels: list[str]) -> tuple[SymptomMatch, ...]:
    """Which of the user's own symptom words appear in the drug's reference list.

    Matching is on normalised Russian labels (and the English term as a
    fallback), substring in either direction: «сонливость» matches
    «сонливость днём», and the reference's «Somnolence» matches its own
    translation.
    """
    effects = side_effects_for(name)
    if not effects:
        return ()
    out: list[SymptomMatch] = []
    seen: set[str] = set()
    for label in labels:
        key = normalize_name(label)
        if not key or key in seen:
            continue
        for effect in effects:
            for candidate in (effect.name_ru, effect.name_en):
                other = normalize_name(candidate)
                if other and (key in other or other in key):
                    out.append(SymptomMatch(symptom=label, effect=effect))
                    seen.add(key)
                    break
            else:
                continue
            break
    return tuple(out)


def reset_cache() -> None:
    """Test hook: re-read the dataset and the translations."""
    _dataset.cache_clear()
    _translations.cache_clear()


__all__ = [
    "DATASET_PATH",
    "DATASET_URL",
    "DatasetStatus",
    "SAMPLE_PATH",
    "SideEffect",
    "SymptomMatch",
    "dataset_status",
    "load_side_effects",
    "match_symptoms",
    "normalize_drug",
    "reset_cache",
    "side_effects_for",
]
