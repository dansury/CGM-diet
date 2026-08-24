"""Controlled vocabulary of meal *components*.

Statistics are only useful if “белый рис” and “рис белый отварной” collapse to
the same thing. Two levels are used:

* `name_norm` — a normalised free-text item name (lowercase, no punctuation,
  no portion words), used for per-dish statistics;
* `tags` — a closed vocabulary of components, used for the cross-dish
  conclusions the user actually acts on (“сахар в составе”, “белая мука”).

The vision/text prompts are told to emit only tags from `TAGS`; anything else
is dropped by `normalize_tags` so the statistics layer never sees free-form
labels. See `spec/analytics.md` § Components.
"""

from __future__ import annotations

import re
import unicodedata

TAGS: dict[str, str] = {
    "added_sugar": "добавленный сахар",
    "refined_flour": "белая мука",
    "white_rice": "белый рис",
    "potato": "картофель",
    "whole_grain": "цельное зерно",
    "starch": "крахмалистое",
    "fruit": "фрукты",
    "dried_fruit": "сухофрукты",
    "juice": "сок",
    "sweet_drink": "сладкий напиток",
    "milk": "молоко",
    "dairy_fermented": "кисломолочное",
    "cheese": "сыр",
    "protein": "белок",
    "red_meat": "красное мясо",
    "processed_meat": "мясная переработка",
    "fish": "рыба",
    "egg": "яйцо",
    "legume": "бобовые",
    "nuts": "орехи",
    "vegetable": "овощи",
    "fiber": "клетчатка",
    "fat_added": "добавленный жир",
    "fried": "жареное",
    "alcohol": "алкоголь",
    "sweetener": "подсластитель",
    "ultra_processed": "ультраобработанное",
    "high_gi": "высокий ГИ",
    "low_gi": "низкий ГИ",
}

# Fallback keyword -> tag mapping, applied when the model returns no tags at
# all (older models, degraded output). Deliberately conservative.
_KEYWORD_TAGS: tuple[tuple[str, str], ...] = (
    ("сахар", "added_sugar"),
    ("мед", "added_sugar"),
    ("варенье", "added_sugar"),
    ("торт", "refined_flour"),
    ("булк", "refined_flour"),
    ("хлеб белый", "refined_flour"),
    ("батон", "refined_flour"),
    ("макарон", "refined_flour"),
    ("рис белый", "white_rice"),
    ("белый рис", "white_rice"),
    ("картоф", "potato"),
    ("греч", "whole_grain"),
    ("овсян", "whole_grain"),
    ("сок", "juice"),
    ("кола", "sweet_drink"),
    ("лимонад", "sweet_drink"),
    ("банан", "fruit"),
    ("яблок", "fruit"),
    ("изюм", "dried_fruit"),
    ("финик", "dried_fruit"),
    ("курин", "protein"),
    ("говядин", "red_meat"),
    ("колбас", "processed_meat"),
    ("сосиск", "processed_meat"),
    ("рыб", "fish"),
    ("лосос", "fish"),
    ("яйц", "egg"),
    ("фасол", "legume"),
    ("чечевиц", "legume"),
    ("орех", "nuts"),
    ("огурц", "vegetable"),
    ("помидор", "vegetable"),
    ("салат", "vegetable"),
    ("творог", "dairy_fermented"),
    ("йогурт", "dairy_fermented"),
    ("кефир", "dairy_fermented"),
    ("молоко", "milk"),
    ("сыр", "cheese"),
    ("вино", "alcohol"),
    ("пиво", "alcohol"),
    ("жарен", "fried"),
)

_PORTION_WORDS = re.compile(
    r"\b(\d+[.,]?\d*)\s*(г|гр|грамм\w*|мл|кг|л|шт|штук\w*|ложк\w*|стакан\w*|порци\w*|кусок|куска|кусочк\w*)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation/portions, collapse spaces, unify ё→е."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", name).lower().replace("ё", "е")
    text = _PORTION_WORDS.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return text


def normalize_tags(tags: list[str] | None, *, name: str = "") -> list[str]:
    """Keep only known tags; infer from the name when the model returned none."""
    out: list[str] = []
    for tag in tags or []:
        slug = str(tag).strip().lower().replace("-", "_").replace(" ", "_")
        if slug in TAGS and slug not in out:
            out.append(slug)
    if not out and name:
        out = infer_tags(name)
    return out


def infer_tags(name: str) -> list[str]:
    norm = normalize_name(name)
    out: list[str] = []
    for keyword, tag in _KEYWORD_TAGS:
        if keyword in norm and tag not in out:
            out.append(tag)
    return out


def tag_label(tag: str) -> str:
    return TAGS.get(tag, tag)


__all__ = ["TAGS", "infer_tags", "normalize_name", "normalize_tags", "tag_label"]
