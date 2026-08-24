"""Drug name → normalised slug → STITCH/PubChem CID.

The side-effect dataset is keyed by STITCH ids (`CID000004091`), which are
PubChem compound ids with a `CID` prefix and zero padding. Users type trade
names in Russian («Глюкофаж 850»), so the mapping goes through a curated
catalogue of common substances with their synonyms — see `config/drug_cids.json`.

An unknown drug is not an error: it is logged like any other, it just has no
reference entry to show.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from src.logging_setup import get_logger
from src.paths import repo_path

log = get_logger("meds.catalog")

CATALOG_PATH = repo_path("config", "drug_cids.json")

# dose and form noise that must not become part of the key
_NOISE = re.compile(
    r"\b(\d+[.,]?\d*\s*(мг|мкг|г|мл|ме|iu|mg|mcg|ml|%)|таб(летк\w*)?|капс(ул\w*)?|"
    r"раствор\w*|сироп\w*|мазь|гель|спрей|ампул\w*|№\s*\d+|n\s*\d+|форте|ретард|"
    r"пролонг\w*|sr|xr)\b",
    re.IGNORECASE,
)
# pack size: «№60», and its NFKC form «No60» — no word boundary to lean on
_COUNT = re.compile(r"(№|no)\s*\d+", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DrugEntry:
    slug: str
    inn_ru: str
    inn_en: str
    cid: str
    labels: tuple[str, ...] = ()


def normalize_drug(name: str) -> str:
    """Lowercase, strip dose/form words and punctuation, unify ё→е."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    text = _COUNT.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@lru_cache(maxsize=1)
def _catalog() -> tuple[tuple[DrugEntry, ...], dict[str, DrugEntry]]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("drug catalog unavailable (%s); medications stay unmapped", exc)
        return (), {}
    entries: list[DrugEntry] = []
    index: dict[str, DrugEntry] = {}
    for slug, item in (raw.get("drugs") or {}).items():
        entry = DrugEntry(
            slug=slug,
            inn_ru=item.get("inn_ru") or slug,
            inn_en=item.get("inn_en") or slug,
            cid=str(item.get("cid") or ""),
            labels=tuple(item.get("labels") or ()),
        )
        entries.append(entry)
        for label in (slug, entry.inn_ru, entry.inn_en, *entry.labels):
            key = normalize_drug(label)
            if key:
                index.setdefault(key, entry)
    return tuple(entries), index


def known_drugs() -> tuple[DrugEntry, ...]:
    return _catalog()[0]


def find_drug(name: str) -> DrugEntry | None:
    """Exact normalised match, then the longest synonym contained in the name."""
    key = normalize_drug(name)
    if not key:
        return None
    _, index = _catalog()
    hit = index.get(key)
    if hit is not None:
        return hit
    best: tuple[int, DrugEntry] | None = None
    for label, entry in index.items():
        if len(label) >= 4 and label in key and (best is None or len(label) > best[0]):
            best = (len(label), entry)
    return best[1] if best else None


def resolve_cid(name: str) -> str | None:
    entry = find_drug(name)
    return entry.cid or None if entry else None


def reset_cache() -> None:
    """Test hook: re-read `config/drug_cids.json`."""
    _catalog.cache_clear()


__all__ = [
    "CATALOG_PATH",
    "DrugEntry",
    "find_drug",
    "known_drugs",
    "normalize_drug",
    "reset_cache",
    "resolve_cid",
]
