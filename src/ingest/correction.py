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

_SENTENCES = re.compile(r"[;\n]")
_SPLIT = re.compile(r"[,]|(?<!\w)и(?!\w)")

#: shortest prefix two words must share to count as the same thing —
#: «гречки» ≈ «гречневая», while «сыр» (3 chars) never swallows «сырники»
STEM_LEN = 4


@dataclass(slots=True)
class Change:
    kind: str  # portion|rename|remove|add
    item: str
    before: str = ""
    after: str = ""

    def describe(self) -> str:
        verbs = {
            "portion": "порция",
            "rename": "название",
            "remove": "убрано",
            "add": "добавлено",
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
    "NUTRIENTS",
    "Change",
    "CorrectionResult",
    "apply_meal_correction",
]
