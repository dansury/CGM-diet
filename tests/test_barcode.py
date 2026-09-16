"""Штрихкод с фотографии и состав из Open Food Facts (T039).

Тихая ошибка здесь — чужой продукт в карточке: одна неверно прочитанная цифра
даёт существующий, но не тот код. Отсюда контрольная сумма и проверка, что
ответ без чисел карточкой не становится.
"""

from __future__ import annotations

import io

import pytest

from src.ingest.barcode import is_valid, normalize, read_barcodes
from src.ingest.openfoodfacts import lookup_product, parse_product

# ------------------------------------------------------------------ код

def test_check_digit_rejects_a_misread_digit():
    assert is_valid("4600682000174")
    assert not is_valid("4600682000175")


def test_upc_and_ean8_are_understood_too():
    assert is_valid("012345678905")      # UPC-A
    assert is_valid("96385074")          # EAN-8
    assert not is_valid("1234")          # ни то ни другое
    assert not is_valid("абвгдежзиклмн")


def test_upc_is_spelled_as_ean13_for_the_database():
    assert normalize("012345678905") == "0012345678905"
    assert normalize("4600682000174") == "4600682000174"


# ------------------------------------------------------------------ чтение

_L = {0: "0001101", 1: "0011001", 2: "0010011", 3: "0111101", 4: "0100011",
      5: "0110001", 6: "0101111", 7: "0111011", 8: "0110111", 9: "0001011"}
_G = {0: "0100111", 1: "0110011", 2: "0011011", 3: "0100001", 4: "0011101",
      5: "0111001", 6: "0000101", 7: "0010001", 8: "0001001", 9: "0010111"}
_R = {0: "1110010", 1: "1100110", 2: "1101100", 3: "1000010", 4: "1011100",
      5: "1001110", 6: "1010000", 7: "1000100", 8: "1001000", 9: "1110100"}
_PARITY = {0: "LLLLLL", 1: "LLGLGG", 2: "LLGGLG", 3: "LLGGGL", 4: "LGLLGG",
           5: "LGGLLG", 6: "LGGGLL", 7: "LGLGLG", 8: "LGLGGL", 9: "LGGLGL"}


def _ean13_png(code: str) -> bytes:
    """Рисуем настоящий EAN-13 — иначе тест проверял бы заглушку, а не zbar."""
    from PIL import Image, ImageDraw

    digits = [int(c) for c in code]
    bits = "101"
    for i, parity in enumerate(_PARITY[digits[0]]):
        bits += (_L if parity == "L" else _G)[digits[i + 1]]
    bits += "01010"
    for i in range(7, 13):
        bits += _R[digits[i]]
    bits += "101"

    scale, height, quiet = 3, 120, 12
    image = Image.new("L", ((len(bits) + quiet * 2) * scale, height + 20), 255)
    draw = ImageDraw.Draw(image)
    for i, bit in enumerate(bits):
        if bit == "1":
            x = (i + quiet) * scale
            draw.rectangle([x, 5, x + scale - 1, height], fill=0)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _zbar_ready() -> bool:
    try:
        from PIL import Image  # noqa: F401
        from pyzbar import pyzbar  # noqa: F401
    except Exception:
        return False
    return True


needs_zbar = pytest.mark.skipif(
    not _zbar_ready(), reason="pyzbar/pillow — опциональный extra [barcode]"
)


@needs_zbar
def test_a_real_ean13_is_read_off_the_picture():
    assert read_barcodes(_ean13_png("4600682000174")) == ["4600682000174"]


@needs_zbar
def test_a_picture_without_a_barcode_yields_nothing():
    from PIL import Image

    blank = io.BytesIO()
    Image.new("L", (200, 200), 255).save(blank, "PNG")
    assert read_barcodes(blank.getvalue()) == []


def test_garbage_bytes_do_not_raise():
    assert read_barcodes(b"not an image") == []


# ------------------------------------------------------------------ OFF

def _payload(**product) -> dict:
    base = {
        "product_name": "Гречка ядрица",
        "brands": "Мистраль,Mistral",
        "nutriments": {
            "energy-kcal_100g": 308,
            "proteins_100g": 12.6,
            "fat_100g": 3.3,
            "carbohydrates_100g": 57.1,
            "sugars_100g": 1.4,
            "fiber_100g": 11.0,
        },
        "ingredients_text": "крупа гречневая ядрица",
        "additives_tags": ["en:e330", "en:e471"],
    }
    base.update(product)
    return {"status": 1, "product": base}


def test_a_found_product_becomes_a_card():
    draft = parse_product(_payload(), barcode="4600682000174")
    assert draft is not None
    assert draft.name == "Гречка ядрица"
    assert draft.brand == "Мистраль"      # первый бренд, не вся строка
    assert draft.barcode == "4600682000174"
    assert draft.kcal_100 == 308
    assert draft.carbs_100 == 57.1
    assert draft.ingredients == ["крупа гречневая ядрица"]
    assert draft.additives == ["E330", "E471"]


def test_the_russian_name_wins_when_there_is_one():
    draft = parse_product(_payload(product_name_ru="Гречневая крупа"), barcode="1")
    assert draft.name == "Гречневая крупа"


def test_an_unknown_code_is_not_a_card():
    assert parse_product({"status": 0}, barcode="4600682000174") is None
    assert parse_product({"status": 1, "product": {}}, barcode="1") is None


def test_a_product_without_numbers_is_not_a_card():
    """Название без состава не стоит показа: за составом и ходили."""
    assert parse_product(_payload(nutriments={}), barcode="1") is None


def test_impossible_nutriments_are_dropped():
    draft = parse_product(
        _payload(nutriments={"energy-kcal_100g": 5000, "proteins_100g": 12.6}),
        barcode="1",
    )
    assert draft.kcal_100 is None
    assert draft.protein_100 == 12.6


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, params=None):
        self.calls.append((url, params or {}))
        if self._error:
            raise self._error
        return self._response


async def test_lookup_asks_for_the_code_and_only_the_fields_we_use():
    client = _FakeClient(_FakeResponse(_payload()))
    draft = await lookup_product("4600682000174", client=client)
    assert draft.name == "Гречка ядрица"
    url, params = client.calls[0]
    assert url.endswith("/4600682000174.json")
    assert "nutriments" in params["fields"]


async def test_an_unreachable_database_is_not_an_error_for_the_user():
    client = _FakeClient(error=RuntimeError("network is down"))
    assert await lookup_product("4600682000174", client=client) is None


async def test_a_404_means_unknown_not_broken():
    client = _FakeClient(_FakeResponse({}, status_code=404))
    assert await lookup_product("4600682000174", client=client) is None
