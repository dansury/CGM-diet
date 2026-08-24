"""User corrections are applied *to* a recognition, not instead of it.

«убери салат, гречки было 250» must keep the chicken the model saw, keep its
nutrients, rescale the buckwheat and drop the salad — not reduce the meal to
whatever fits in one typed line. Everything the user did not mention survives.

Deterministic clauses first (they cost nothing and are testable); anything left
unparsed is handed to the model together with the current draft, which returns
a corrected draft rather than a fresh recognition. See `spec/ingest.md`
§ Корректировки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.analytics.tags import infer_tags, normalize_name
from src.ingest.nutrition import fill_meal
from src.vision.schemas import ItemDraft, MealDraft

NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")

#: how a remembered macro is named in the card («Б 5 · Ж 2 · У 30»)
MACRO_LABELS = {
    "protein_g": "Б",
    "fat_g": "Ж",
    "carbs_g": "У",
    "fiber_g": "клетчатка",
    "kcal": "ккал",
}

_REMOVE = re.compile(r"^(?:убери(?:те)?|убрать|удали(?:те)?|минус|без)\s+(?P<name>.+)$", re.I)
_ADD = re.compile(
    r"^(?:добавь(?:те)?|добавить|плюс|ещё|еще)\s+(?P<name>.+?)"
    r"(?:\s+(?P<portion>\d+(?:[.,]\d+)?)\s*(?:г|гр|g)?)?$",
    re.I,
)
_RENAME = re.compile(
    r"^(?:(?:это\s+|тут\s+|там\s+)?(?:был[аои]?\s+)?вместо\s+(?P<old1>.+?)\s+(?P<new1>.+)"
    r"|(?:это\s+|тут\s+|там\s+)?(?:был[аои]?\s+)?не\s+(?P<old2>.+?)\s*,?\s*"
    r"(?:\bа\b|\bэто\b)\s+(?P<new2>.+)"
    r"|(?P<old3>.+?)\s*(?:->|→)\s*(?P<new3>.+))$",
    re.I,
)
_PORTION = re.compile(
    r"^(?P<name>.+?)\s+(?:было\s+|это\s+)?(?P<portion>\d+(?:[.,]\d+)?)\s*(?:г|гр|g|грамм\w*)?$",
    re.I,
)
_BARE_PORTION = re.compile(r"^(?P<portion>\d+(?:[.,]\d+)?)\s*(?:г|гр|g|грамм\w*)$", re.I)

#: «120 ккал» — the unit trails the number, unlike every other macro
_KCAL_AFTER = re.compile(r"(?<![\w.,])(?P<value>\d+(?:[.,]\d+)?)\s*(?:ккал|кк|калори\w*)\b", re.I)
#: «б 5», «белки 20», «У: 30,5», «жиров 12 г»
_MACRO = re.compile(
    r"(?<!\w)(?P<key>белк\w*|протеин\w*|жир\w*|углевод\w*|клетчатк\w*|волокн\w*"
    r"|ккал|калори\w*|[бжук])\s*[:=\-]?\s*(?P<value>\d+(?:[.,]\d+)?)\s*(?:г|гр|g)?(?!\w)",
    re.I,
)
#: what is left of a macro clause after the numbers: «гречка 250 г»
_TRAILING_PORTION = re.compile(
    r"^(?P<name>.*?)\s*(?P<portion>\d+(?:[.,]\d+)?)\s*(?:г|гр|g|грамм\w*)?$", re.I
)

_MACRO_FIELD = {
    "б": "protein_g",
    "бел": "protein_g",
    "про": "protein_g",
    "ж": "fat_g",
    "жир": "fat_g",
    "у": "carbs_g",
    "угл": "carbs_g",
    "к": "kcal",
    "кка": "kcal",
    "кал": "kcal",
    "кле": "fiber_g",
    "вол": "fiber_g",
}


def _macro_field(key: str) -> str | None:
    key = key.lower()
    return _MACRO_FIELD.get(key) or _MACRO_FIELD.get(key[:3])


_SENTENCES = re.compile(r"[;\n]")
_SPLIT = re.compile(r"[,]|(?<!\w)и(?!\w)")

#: shortest prefix two words must share to count as the same thing —
#: «гречки» ≈ «гречневая», while «сыр» (3 chars) never swallows «сырники»
STEM_LEN = 4


@dataclass(slots=True)
class Change:
    kind: str  # portion|rename|remove|add|macros
    item: str
    before: str = ""
    after: str = ""

    def describe(self) -> str:
        verbs = {
            "portion": "порция",
            "rename": "название",
            "remove": "убрано",
            "add": "добавлено",
            "macros": "БЖУ",
        }
        head = f"{verbs.get(self.kind, self.kind)}: {self.item}"
        if self.before and self.after:
            return f"{head} — {self.before} → {self.after}"
        if self.after:
            return f"{head} — {self.after}"
        return head


@dataclass(slots=True)
class CorrectionResult:
    draft: MealDraft
    changes: list[Change] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def _num(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _same_stem(a: str, b: str) -> bool:
    """«гречки» ≈ «гречневая»: Russian cases differ in the tail, not the root."""
    for left in a.split():
        for right in b.split():
            size = min(len(left), len(right))
            if size >= STEM_LEN and left[:STEM_LEN] == right[:STEM_LEN]:
                return True
    return False


def _find(items: list[ItemDraft], name: str) -> ItemDraft | None:
    """Exact normalised name, then substring, then a shared word stem."""
    key = normalize_name(name)
    if not key:
        return None
    for item in items:
        if normalize_name(item.name) == key:
            return item
    for item in items:
        other = normalize_name(item.name)
        if other and (key in other or other in key):
            return item
    for item in items:
        if _same_stem(key, normalize_name(item.name)):
            return item
    return None


def _rescale(item: ItemDraft, portion: float) -> None:
    """A corrected weight must not silently keep the old calories."""
    if item.portion_g and item.portion_g > 0:
        scale = portion / item.portion_g
        for name in NUTRIENTS:
            value = getattr(item, name)
            if value is not None:
                setattr(item, name, round(value * scale, 1))
    item.portion_g = portion


def _clone(draft: MealDraft) -> MealDraft:
    return MealDraft(
        title=draft.title,
        items=[
            ItemDraft(
                name=i.name,
                portion_g=i.portion_g,
                kcal=i.kcal,
                protein_g=i.protein_g,
                fat_g=i.fat_g,
                carbs_g=i.carbs_g,
                fiber_g=i.fiber_g,
                tags=list(i.tags or []),
                estimated=i.estimated,
                macros_source=i.macros_source,
            )
            for i in draft.items
        ],
        confidence=draft.confidence,
        notes=draft.notes,
        source=draft.source,
        raw_text=draft.raw_text,
    )


def _clauses(text: str) -> list[str]:
    """Sentences first: a rename («не гречка, а перловка») owns its own comma."""
    out: list[str] = []
    for sentence in _SENTENCES.split(text):
        sentence = sentence.strip(" .!")
        if not sentence:
            continue
        if _RENAME.match(sentence):
            out.append(sentence)
            continue
        for part in _SPLIT.split(sentence):
            part = part.strip(" .!")
            if part:
                out.append(part)
    return out


def _parse_macros(clause: str) -> tuple[dict[str, float], str]:
    """Pull БЖУ/ккал out of a clause; return them plus the leftover text.

    «гречка 250 г б 5 ж 2 у 30» → ({protein_g: 5, ...}, «гречка 250 г»).
    """
    values: dict[str, float] = {}
    rest = clause

    def take(match: re.Match[str], name: str) -> None:
        value = _num(match.group("value"))
        if value is not None and name not in values:
            values[name] = value

    for match in list(_KCAL_AFTER.finditer(rest)):
        take(match, "kcal")
    rest = _KCAL_AFTER.sub(" ", rest)
    for match in list(_MACRO.finditer(rest)):
        name = _macro_field(match.group("key"))
        if name:
            take(match, name)
    rest = _MACRO.sub(" ", rest)
    return values, " ".join(rest.split())


def _set_macros(item: ItemDraft, values: dict[str, float]) -> None:
    """The user's numbers are absolute for the item's portion, not per 100 g."""
    for name, value in values.items():
        setattr(item, name, value)
    if "kcal" not in values:
        kcal = sum(
            (getattr(item, name) or 0.0) * per_g
            for name, per_g in (("protein_g", 4.0), ("fat_g", 9.0), ("carbs_g", 4.0))
        )
        if kcal > 0:
            item.kcal = round(kcal)
    item.estimated = False
    item.macros_source = "user"


def _describe_macros(values: dict[str, float]) -> str:
    return " · ".join(
        f"{MACRO_LABELS[name]} {values[name]:g}"
        for name in ("protein_g", "fat_g", "carbs_g", "fiber_g", "kcal")
        if name in values
    )


def split_macros(text: str) -> tuple[str, str]:
    """Отделить БЖУ, названные во входном тексте, от описания еды.

    «овсянка 200 г б 12 ж 6 у 40» → («овсянка 200 г», «овсянка 200 г б 12 ж 6 у 40»):
    the model is asked about the food, the numbers are then applied as the
    user's own correction so they are never re-estimated.
    """
    food: list[str] = []
    macros: list[str] = []
    for clause in _clauses(text or ""):
        values, leftover = _parse_macros(clause)
        if values:
            macros.append(clause)
            if leftover:
                food.append(leftover)
        else:
            food.append(clause)
    if not macros:
        return (text or "").strip(), ""
    return ", ".join(food), "; ".join(macros)


def apply_meal_correction(draft: MealDraft, instruction: str) -> CorrectionResult:
    """Merge `instruction` into `draft`; untouched items keep their numbers."""
    result = CorrectionResult(draft=_clone(draft))
    text = (instruction or "").strip()
    if not text:
        return result
    items = result.draft.items

    for clause in _clauses(text):
        match = _REMOVE.match(clause)
        if match:
            target = _find(items, match.group("name"))
            if target is not None:
                items.remove(target)
                result.changes.append(Change("remove", target.name))
            else:
                result.unmatched.append(clause)
            continue

        match = _ADD.match(clause)
        if match:
            name = match.group("name").strip()
            portion = _num(match.group("portion"))
            existing = _find(items, name)
            if existing is not None and portion is not None:
                before = f"{existing.portion_g:.0f} г" if existing.portion_g else "—"
                _rescale(existing, portion)
                result.changes.append(
                    Change("portion", existing.name, before, f"{portion:.0f} г")
                )
            else:
                items.append(
                    ItemDraft(name=name, portion_g=portion, tags=infer_tags(name))
                )
                result.changes.append(
                    Change("add", name, after=f"{portion:.0f} г" if portion else "")
                )
            continue

        match = _RENAME.match(clause)
        if match:
            old = match.group("old1") or match.group("old2") or match.group("old3") or ""
            new = match.group("new1") or match.group("new2") or match.group("new3") or ""
            target = _find(items, old.strip())
            if target is not None and new.strip():
                before = target.name
                target.name = new.strip()
                target.tags = infer_tags(target.name) or target.tags
                result.changes.append(Change("rename", before, before, target.name))
            else:
                result.unmatched.append(clause)
            continue

        # БЖУ must be read before the portion rules: «гречка б 5 ж 2 у 30»
        # otherwise ends up parsed as «гречка — 30 г».
        values, leftover = _parse_macros(clause)
        if values:
            name = leftover
            portion: float | None = None
            trailing = _TRAILING_PORTION.match(leftover) if leftover else None
            if trailing and trailing.group("portion"):
                name = trailing.group("name").strip()
                portion = _num(trailing.group("portion"))
            target = _find(items, name) if name else (items[0] if len(items) == 1 else None)
            if target is None and name:
                target = ItemDraft(name=name, tags=infer_tags(name))
                items.append(target)
            if target is None:
                result.unmatched.append(clause)
                continue
            if portion is not None:
                target.portion_g = portion
            _set_macros(target, values)
            result.changes.append(
                Change("macros", target.name, after=_describe_macros(values))
            )
            continue

        match = _BARE_PORTION.match(clause)
        if match and len(items) == 1:
            portion = _num(match.group("portion"))
            if portion is not None:
                before = f"{items[0].portion_g:.0f} г" if items[0].portion_g else "—"
                _rescale(items[0], portion)
                result.changes.append(
                    Change("portion", items[0].name, before, f"{portion:.0f} г")
                )
                continue

        match = _PORTION.match(clause)
        if match:
            name = match.group("name").strip()
            portion = _num(match.group("portion"))
            target = _find(items, name)
            if target is not None and portion is not None:
                before = f"{target.portion_g:.0f} г" if target.portion_g else "—"
                _rescale(target, portion)
                result.changes.append(
                    Change("portion", target.name, before, f"{portion:.0f} г")
                )
            elif portion is not None:
                items.append(ItemDraft(name=name, portion_g=portion, tags=infer_tags(name)))
                result.changes.append(Change("add", name, after=f"{portion:.0f} г"))
            else:
                result.unmatched.append(clause)
            continue

        result.unmatched.append(clause)

    if result.changes:
        result.draft.notes = "учтена ваша правка"
        result.draft.confidence = 1.0
        if not result.draft.items:
            result.draft.title = result.draft.title or "Приём пищи"
        elif not result.draft.title:
            result.draft.title = result.draft.items[0].name
        # A newly added item arrives as a bare name + portion: give it numbers,
        # otherwise the correction would zero out the meal's totals.
        fill_meal(result.draft)
    return result


__all__ = [
    "MACRO_LABELS",
    "NUTRIENTS",
    "Change",
    "CorrectionResult",
    "apply_meal_correction",
    "split_macros",
]
