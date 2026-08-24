"""Fill in nutrition the model left empty. See `spec/ingest.md` § Нутриенты.

A model asked for «наггетсы» sometimes answers with a name and nothing else:
every macro `null`. Summed up that becomes a card reading «0 ккал · Б 0 · Ж 0 ·
У 0», which is worse than no answer — it looks like a measurement. This module
holds a small reference table of per-100 g values for common foods and fills
only the fields that came back empty; anything the model (or the user) did
state is never overwritten. Items with no table match keep their `None`s — the
card then asks for grams instead of printing a fabricated zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.analytics.tags import normalize_name
from src.vision.schemas import ItemDraft, MealDraft


@dataclass(frozen=True, slots=True)
class Ref:
    """Per-100 g reference plus a typical portion for that food."""

    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    portion_g: float


# Keyword (matched as a substring of the normalised name) -> per-100 g values.
# Sources: обобщённые табличные значения (USDA / Роспотребнадзор), округлённые.
# The longest matching keyword wins, so «куриная грудка» beats «курин».
TABLE: tuple[tuple[str, Ref], ...] = (
    # мясо, птица, рыба
    ("наггетс", Ref(290, 15.0, 18.0, 16.0, 1.0, 150)),
    ("куриная грудка", Ref(165, 31.0, 3.6, 0.0, 0.0, 120)),
    ("курин", Ref(190, 25.0, 9.0, 1.0, 0.0, 150)),
    ("куриц", Ref(190, 25.0, 9.0, 1.0, 0.0, 150)),
    ("котлет", Ref(250, 15.0, 18.0, 8.0, 0.5, 120)),
    ("шашлык", Ref(240, 22.0, 17.0, 1.0, 0.0, 200)),
    ("говядин", Ref(220, 26.0, 13.0, 0.0, 0.0, 150)),
    ("свинин", Ref(280, 22.0, 21.0, 0.0, 0.0, 150)),
    ("индейк", Ref(160, 28.0, 5.0, 0.0, 0.0, 150)),
    ("колбас", Ref(300, 12.0, 27.0, 2.0, 0.0, 80)),
    ("сосиск", Ref(280, 11.0, 25.0, 2.0, 0.0, 100)),
    ("бекон", Ref(400, 13.0, 39.0, 1.0, 0.0, 40)),
    ("ветчин", Ref(180, 17.0, 12.0, 1.0, 0.0, 60)),
    ("лосос", Ref(200, 20.0, 13.0, 0.0, 0.0, 150)),
    ("сельд", Ref(250, 18.0, 19.0, 0.0, 0.0, 100)),
    ("тунец", Ref(130, 24.0, 4.0, 0.0, 0.0, 120)),
    ("креветк", Ref(95, 20.0, 1.5, 0.0, 0.0, 120)),
    ("рыб", Ref(140, 20.0, 6.0, 0.0, 0.0, 150)),
    ("яйц", Ref(150, 13.0, 11.0, 1.0, 0.0, 110)),
    ("омлет", Ref(180, 11.0, 14.0, 2.0, 0.0, 180)),
    # гарниры и крупы
    ("картофель фри", Ref(310, 3.5, 15.0, 40.0, 3.5, 150)),
    ("пюре", Ref(90, 2.0, 3.0, 14.0, 1.5, 200)),
    ("картоф", Ref(90, 2.0, 1.5, 17.0, 2.0, 200)),
    ("греч", Ref(110, 4.0, 1.5, 21.0, 2.7, 180)),
    ("овсян", Ref(90, 3.0, 2.0, 15.0, 2.0, 220)),
    ("рис", Ref(130, 2.7, 0.6, 28.0, 0.5, 180)),
    ("макарон", Ref(160, 6.0, 1.5, 31.0, 2.0, 200)),
    ("паста", Ref(160, 6.0, 1.5, 31.0, 2.0, 200)),
    ("перловк", Ref(120, 3.5, 0.5, 26.0, 3.0, 180)),
    ("булгур", Ref(130, 4.5, 0.5, 26.0, 4.0, 180)),
    ("киноа", Ref(130, 4.5, 2.0, 21.0, 2.8, 180)),
    ("фасол", Ref(120, 8.0, 0.5, 18.0, 6.0, 150)),
    ("чечевиц", Ref(115, 9.0, 0.4, 20.0, 8.0, 150)),
    ("горох", Ref(115, 8.0, 0.4, 20.0, 7.0, 150)),
    # хлеб, выпечка, снеки
    ("хлеб", Ref(250, 8.0, 3.0, 47.0, 3.0, 60)),
    ("батон", Ref(260, 8.0, 3.0, 50.0, 2.0, 60)),
    ("булк", Ref(300, 8.0, 6.0, 54.0, 2.0, 80)),
    ("пирог", Ref(300, 6.0, 12.0, 42.0, 1.5, 120)),
    ("блин", Ref(230, 6.0, 9.0, 31.0, 1.0, 150)),
    ("сырник", Ref(220, 14.0, 9.0, 20.0, 0.5, 150)),
    ("пельмен", Ref(250, 12.0, 11.0, 26.0, 1.0, 250)),
    ("пицц", Ref(270, 11.0, 11.0, 31.0, 2.0, 250)),
    ("бургер", Ref(260, 13.0, 12.0, 25.0, 1.5, 220)),
    ("шаурм", Ref(210, 12.0, 10.0, 18.0, 1.5, 300)),
    ("печенье", Ref(420, 6.0, 18.0, 60.0, 2.0, 60)),
    ("шоколад", Ref(540, 6.0, 32.0, 55.0, 5.0, 40)),
    ("конфет", Ref(450, 4.0, 20.0, 62.0, 1.0, 40)),
    ("торт", Ref(380, 5.0, 20.0, 45.0, 1.0, 120)),
    ("мороженое", Ref(210, 4.0, 11.0, 24.0, 0.5, 100)),
    ("чипс", Ref(530, 6.0, 32.0, 53.0, 4.0, 50)),
    ("орех", Ref(600, 18.0, 54.0, 15.0, 7.0, 30)),
    # молочное
    ("творог", Ref(120, 17.0, 5.0, 3.0, 0.0, 150)),
    ("йогурт", Ref(80, 4.0, 2.5, 10.0, 0.0, 150)),
    ("кефир", Ref(55, 3.0, 2.5, 4.0, 0.0, 200)),
    ("смета", Ref(200, 2.5, 20.0, 3.0, 0.0, 30)),
    ("сыр", Ref(340, 24.0, 27.0, 2.0, 0.0, 40)),
    ("молоко", Ref(60, 3.0, 3.2, 4.7, 0.0, 200)),
    # овощи, фрукты, салаты
    ("салат", Ref(70, 1.5, 4.5, 5.0, 2.0, 150)),
    ("огурц", Ref(15, 0.8, 0.1, 3.0, 1.0, 150)),
    ("помидор", Ref(20, 0.9, 0.2, 4.0, 1.2, 150)),
    ("капуст", Ref(30, 1.8, 0.2, 5.0, 2.5, 150)),
    ("морков", Ref(40, 0.9, 0.2, 9.0, 2.8, 100)),
    ("овощ", Ref(45, 1.5, 1.0, 7.0, 2.5, 150)),
    ("банан", Ref(90, 1.1, 0.3, 23.0, 2.6, 120)),
    ("яблок", Ref(52, 0.3, 0.2, 14.0, 2.4, 180)),
    ("апельсин", Ref(47, 0.9, 0.1, 12.0, 2.4, 180)),
    ("виноград", Ref(70, 0.7, 0.2, 18.0, 0.9, 150)),
    ("ягод", Ref(50, 1.0, 0.4, 11.0, 3.0, 120)),
    ("фрукт", Ref(60, 0.7, 0.3, 14.0, 2.0, 150)),
    ("суп", Ref(50, 3.0, 2.0, 5.0, 1.0, 300)),
    ("борщ", Ref(50, 2.5, 2.5, 5.0, 1.5, 300)),
    # напитки
    ("сок", Ref(45, 0.5, 0.1, 11.0, 0.3, 250)),
    ("кола", Ref(42, 0.0, 0.0, 10.5, 0.0, 330)),
    ("лимонад", Ref(40, 0.0, 0.0, 10.0, 0.0, 330)),
    ("пиво", Ref(43, 0.5, 0.0, 3.6, 0.0, 500)),
    ("вино", Ref(80, 0.1, 0.0, 2.6, 0.0, 150)),
    ("кофе с молоком", Ref(35, 1.8, 1.8, 2.8, 0.0, 200)),
    ("латте", Ref(50, 2.5, 2.5, 4.5, 0.0, 250)),
    ("капучино", Ref(45, 2.5, 2.3, 3.8, 0.0, 200)),
)

# Atwater factors — how kcal are recovered when only macros are known.
_KCAL_PER_G = {"protein_g": 4.0, "fat_g": 9.0, "carbs_g": 4.0}

_MACROS = ("protein_g", "fat_g", "carbs_g")

ESTIMATE_NOTE = "БЖУ для части позиций оценены по справочным таблицам, не измерены."


def lookup(name: str) -> Ref | None:
    """Longest keyword match against the normalised item name."""
    norm = normalize_name(name)
    if not norm:
        return None
    best: tuple[int, Ref] | None = None
    for keyword, ref in TABLE:
        if keyword in norm and (best is None or len(keyword) > best[0]):
            best = (len(keyword), ref)
    return best[1] if best else None


def _blank(value: float | None) -> bool:
    return value is None or value == 0.0


def fill_item(item: ItemDraft) -> bool:
    """Fill empty macros/kcal for one item. Returns True if anything changed.

    Values that came from the model or the user are left alone — this only ever
    writes into `None`/`0` fields.
    """
    changed = False
    ref = lookup(item.name)
    if ref is not None:
        portion = item.portion_g
        if _blank(portion):
            portion = ref.portion_g
            if all(_blank(getattr(item, m)) for m in _MACROS):
                # Portion is only invented when there are no macros to trust:
                # otherwise the model's numbers already imply its own portion.
                item.portion_g = portion
                changed = True
        factor = (portion or ref.portion_g) / 100.0
        for field in (*_MACROS, "fiber_g"):
            if _blank(getattr(item, field)):
                setattr(item, field, round(getattr(ref, field) * factor, 1))
                changed = True
    if _blank(item.kcal):
        kcal = sum(
            (getattr(item, field) or 0.0) * per_g for field, per_g in _KCAL_PER_G.items()
        )
        if kcal > 0:
            item.kcal = round(kcal)
            changed = True
    if changed:
        item.estimated = True
    return changed


def fill_meal(draft: MealDraft) -> bool:
    """Backfill every item of a meal; note in the card that numbers are estimates."""
    changed = False
    for item in draft.items:
        changed = fill_item(item) or changed
    if changed and ESTIMATE_NOTE not in draft.notes:
        draft.notes = f"{draft.notes} {ESTIMATE_NOTE}".strip()
    return changed


__all__ = ["ESTIMATE_NOTE", "TABLE", "Ref", "fill_item", "fill_meal", "lookup"]
