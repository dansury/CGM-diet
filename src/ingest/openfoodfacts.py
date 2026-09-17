"""Open Food Facts — composition by barcode, when the label will not be read.

Open Food Facts is a public, openly-licensed database of packaged food. A
barcode read off the photo (`src/ingest/barcode.py`) is an exact key into it, so
a pack whose small print is blurred, curved or in a language the model stumbles
over still produces a card — with numbers a person typed off the pack, not
numbers guessed from a photo.

The lookup is best-effort in every direction: no network, no answer, an unknown
code, a product without nutrition — all end as `None`, and the caller falls back
to asking for a better photo. Nothing here is ever the only source of a card:
what comes back goes through the same confirmation step as recognition.
"""

from __future__ import annotations

from typing import Any

from src.analytics.tags import normalize_tags
from src.logging_setup import get_logger
from src.vision.schemas import ProductDraft

log = get_logger("ingest.openfoodfacts")

API_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
REQUEST_TIMEOUT_S = 10.0
# OFF asks every client to identify itself; an anonymous flood gets rate-limited.
USER_AGENT = "CGM-diet/0.1 (https://github.com/dansury/CGM-diet)"

#: Asking for the fields we use keeps the answer a few kB instead of ~100.
FIELDS = (
    "product_name,product_name_ru,brands,nutriments,ingredients_text,"
    "ingredients_text_ru,additives_tags,nova_group,quantity"
)

# OFF nutriment keys are per 100 g/ml when suffixed `_100g`.
_NUTRIENTS = {
    "kcal_100": ("energy-kcal_100g",),
    "protein_100": ("proteins_100g",),
    "fat_100": ("fat_100g",),
    "carbs_100": ("carbohydrates_100g",),
    "sugars_100": ("sugars_100g",),
    "fiber_100": ("fiber_100g",),
}


async def lookup_product(barcode: str, *, client: Any | None = None) -> ProductDraft | None:
    """Barcode → draft, or None when nothing usable came back."""
    import httpx

    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=5.0),
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response = await http.get(
            API_URL.format(barcode=barcode), params={"fields": FIELDS}
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network, HTTP, JSON — the caller just gets None
        log.info("openfoodfacts lookup failed for %s: %s", barcode, exc)
        return None
    finally:
        if owns:
            await http.aclose()
    return parse_product(payload, barcode=barcode)


def parse_product(payload: Any, *, barcode: str) -> ProductDraft | None:
    """The API answer → draft. None when the code is unknown or unnamed."""
    if not isinstance(payload, dict) or payload.get("status") == 0:
        return None
    product = payload.get("product")
    if not isinstance(product, dict):
        return None
    name = _text(product.get("product_name_ru")) or _text(product.get("product_name"))
    if not name:
        return None
    nutriments = product.get("nutriments")
    nutriments = nutriments if isinstance(nutriments, dict) else {}
    values = {
        field: _number(nutriments, keys) for field, keys in _NUTRIENTS.items()
    }
    # A card with a name and no numbers is not worth showing: the whole point of
    # the lookup is the composition the label did not give up.
    if all(value is None for value in values.values()):
        return None
    return ProductDraft(
        name=name,
        brand=_first_brand(product.get("brands")),
        barcode=barcode,
        ingredients=_ingredients(product),
        additives=_additives(product.get("additives_tags")),
        flags=normalize_tags(None, name=name),
        # Not a recognition confidence: the numbers are transcribed by people,
        # but the code may have been misread and the entry may be someone's typo.
        confidence=0.8,
        **values,
    )


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _first_brand(value: Any) -> str | None:
    """OFF stores brands comma-separated, most specific first."""
    text = _text(value)
    return text.split(",")[0].strip() or None if text else None


def _number(nutriments: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        raw = nutriments.get(key)
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 1000:
            return round(value, 1)
    return None


def _ingredients(product: dict) -> list[str]:
    text = _text(product.get("ingredients_text_ru")) or _text(product.get("ingredients_text"))
    if not text:
        return []
    parts = [part.strip(" .;") for part in text.replace(";", ",").split(",")]
    return [part for part in parts if part][:30]


def _additives(tags: Any) -> list[str]:
    """`en:e330` → `E330`. The tag list is what OFF matched, not free text."""
    out: list[str] = []
    for tag in tags or []:
        code = _text(tag).split(":")[-1].upper()
        if code and code not in out:
            out.append(code)
    return out[:30]


__all__ = ["API_URL", "FIELDS", "lookup_product", "parse_product"]
