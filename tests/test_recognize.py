"""Recognition layer against the deterministic mock provider."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.llm import ImagePart
from src.llm.jsonx import extract_json
from src.llm.mock import MockClient
from src.vision import recognize

IMAGES = [ImagePart(b"fake-jpeg")]
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def test_extract_json_survives_fences_and_prose():
    assert extract_json('вот ответ ```json\n{"a": 1,}\n``` конец') == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("совсем не json")


async def test_meal_photo_returns_items_and_totals():
    draft = await recognize.recognize_meal_photo(IMAGES)
    assert draft.title
    assert len(draft.items) == 3
    assert draft.totals()["carbs_g"] == pytest.approx(43.0)
    assert "whole_grain" in draft.items[0].tags


async def test_label_photo_merges_into_a_product_card():
    draft = await recognize.recognize_label(IMAGES)
    assert draft.name
    assert draft.sugars_100 == pytest.approx(11.9)
    assert "added_sugar" in draft.flags
    assert draft.barcode == "4600000000017"


async def test_glucose_screenshot_is_sorted_and_converted():
    drafts, device = await recognize.recognize_glucose_screenshot(IMAGES, now=NOW)
    assert device == "FreeStyle Libre"
    assert [d.value_mmol for d in drafts] == [5.4, 8.9, 6.7]
    assert drafts == sorted(drafts, key=lambda d: d.measured_at)


async def test_glucose_screenshot_converts_mgdl():
    client = MockClient(
        overrides={
            "glucose_screenshot": {
                "unit": "mg/dL",
                "device": "Contour",
                "readings": [{"measured_at": "2026-08-24T08:00:00", "value": 126}],
            }
        }
    )
    drafts, _ = await recognize.recognize_glucose_screenshot(IMAGES, now=NOW, client=client)
    assert drafts[0].value_mmol == pytest.approx(6.99, abs=0.01)


async def test_glucose_screenshot_rejects_impossible_values():
    client = MockClient(
        overrides={
            "glucose_screenshot": {
                "unit": "mmol/L",
                "readings": [
                    {"measured_at": "2026-08-24T08:00:00", "value": 7.4},
                    {"measured_at": "2026-08-24T08:05:00", "value": 0.2},
                ],
            }
        }
    )
    drafts, _ = await recognize.recognize_glucose_screenshot(IMAGES, now=NOW, client=client)
    assert [d.value_mmol for d in drafts] == [7.4]


async def test_glucose_screenshot_time_only_resolves_to_today():
    client = MockClient(
        overrides={
            "glucose_screenshot": {
                "unit": "mmol/L",
                "readings": [{"measured_at": None, "time": "09:30", "value": 6.1}],
            }
        }
    )
    drafts, _ = await recognize.recognize_glucose_screenshot(IMAGES, now=NOW, client=client)
    assert drafts[0].measured_at.hour == 9
    assert drafts[0].measured_at.date() == NOW.date()


async def test_glucose_screenshot_future_time_rolls_back_a_day():
    client = MockClient(
        overrides={
            "glucose_screenshot": {
                "unit": "mmol/L",
                "readings": [{"measured_at": None, "time": "23:30", "value": 6.1}],
            }
        }
    )
    drafts, _ = await recognize.recognize_glucose_screenshot(IMAGES, now=NOW, client=client)
    assert drafts[0].measured_at.date() < NOW.date()


async def test_labs_flag_out_of_range_markers():
    draft = await recognize.recognize_labs(images=IMAGES, now=NOW)
    flags = {m.marker: m.flag for m in draft.markers}
    assert flags["Глюкоза"] == "high"
    assert flags["HbA1c"] == "normal"


async def test_recognition_error_on_empty_result():
    client = MockClient(overrides={"food_photo": {"title": "", "items": []}})
    with pytest.raises(recognize.RecognitionError):
        await recognize.recognize_meal_photo(IMAGES, client=client)


async def test_classify_falls_back_to_other_on_failure():
    client = MockClient(overrides={"classify_photo": {"kind": "нечто", "confidence": 1}})
    kind, _ = await recognize.classify_photo(IMAGES, client=client)
    assert kind == "other"


async def test_symptom_extraction():
    score, symptoms, _note = await recognize.extract_symptoms("сонливость после обеда")
    assert "сонливость" in symptoms
    assert score is None
