"""Deterministic offline client (`LLM_MOCK=true`).

Lets the whole bot — handlers, parsers, statistics, charts — run in CI and on a
laptop with no API key and no network. Each prompt starts with a
`TASK: <name>` marker (see `src/vision/prompts.py`); the mock routes on it and
returns a fixed, schema-valid answer. See `spec/llm.md` § Mock mode.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from src.llm.base import ChatMessage, Completion, ImagePart

_TASK_RE = re.compile(r"TASK:\s*([a-z_]+)", re.IGNORECASE)

FIXTURES: dict[str, Any] = {
    "food_photo": {
        "title": "Гречка с курицей и овощным салатом",
        "confidence": 0.72,
        "items": [
            {
                "name": "гречневая каша",
                "portion_g": 180,
                "kcal": 200,
                "protein_g": 7.0,
                "fat_g": 2.0,
                "carbs_g": 38.0,
                "fiber_g": 4.5,
                "tags": ["whole_grain", "starch"],
            },
            {
                "name": "куриная грудка",
                "portion_g": 120,
                "kcal": 198,
                "protein_g": 37.0,
                "fat_g": 4.3,
                "carbs_g": 0.0,
                "fiber_g": 0.0,
                "tags": ["protein"],
            },
            {
                "name": "салат из огурцов и помидоров",
                "portion_g": 150,
                "kcal": 45,
                "protein_g": 1.5,
                "fat_g": 2.5,
                "carbs_g": 5.0,
                "fiber_g": 2.0,
                "tags": ["vegetable", "fiber"],
            },
        ],
        "notes": "Порции оценены по размеру тарелки.",
    },
    "label_photo": {
        "brand": "Дымов",
        "name": "Йогурт питьевой клубника",
        "barcode": "4600000000017",
        "per_100": {
            "kcal": 86,
            "protein_g": 2.8,
            "fat_g": 2.5,
            "carbs_g": 12.6,
            "sugars_g": 11.9,
            "fiber_g": 0.0,
        },
        "ingredients": [
            "молоко нормализованное",
            "сахар",
            "пюре клубники",
            "закваска",
            "загуститель пектин",
            "ароматизатор натуральный",
        ],
        "additives": ["пектин", "ароматизатор"],
        "flags": ["added_sugar", "sweet_drink"],
        "confidence": 0.81,
    },
    "glucose_screenshot": {
        "unit": "mmol/L",
        "device": "FreeStyle Libre",
        "readings": [
            {"measured_at": "2026-08-24T08:05:00", "value": 5.4, "trend": "flat"},
            {"measured_at": "2026-08-24T09:05:00", "value": 8.9, "trend": "up"},
            {"measured_at": "2026-08-24T10:05:00", "value": 6.7, "trend": "down"},
        ],
        "confidence": 0.9,
    },
    "lab_report": {
        "panel": "Биохимия крови",
        "taken_at": "2026-08-20",
        "markers": [
            {
                "marker": "Глюкоза",
                "value": 6.3,
                "unit": "ммоль/л",
                "ref_low": 3.9,
                "ref_high": 5.9,
            },
            {"marker": "HbA1c", "value": 5.9, "unit": "%", "ref_low": 4.0, "ref_high": 6.0},
            {
                "marker": "Инсулин",
                "value": 14.2,
                "unit": "мкЕд/мл",
                "ref_low": 2.6,
                "ref_high": 24.9,
            },
        ],
        "confidence": 0.85,
    },
    "text_meal": {
        "title": "Овсянка с бананом",
        "confidence": 0.6,
        "items": [
            {
                "name": "овсяная каша",
                "portion_g": 200,
                "kcal": 150,
                "protein_g": 5.0,
                "fat_g": 3.0,
                "carbs_g": 27.0,
                "fiber_g": 3.0,
                "tags": ["whole_grain", "starch"],
            },
            {
                "name": "банан",
                "portion_g": 120,
                "kcal": 107,
                "protein_g": 1.3,
                "fat_g": 0.4,
                "carbs_g": 27.0,
                "fiber_g": 3.1,
                "tags": ["fruit", "high_gi"],
            },
        ],
        "notes": "",
    },
    "symptom_extract": {"score": None, "symptoms": ["сонливость", "потливость"], "note": ""},
}

_DEFAULT = {"error": "mock: unknown task"}


class MockClient:
    """Drop-in `LLMClient` returning canned, schema-valid JSON."""

    provider: ClassVar[str] = "mock"

    def __init__(self, settings: Any | None = None, overrides: dict[str, Any] | None = None) -> None:
        self._settings = settings
        self.calls: list[dict[str, Any]] = []
        self._fixtures = dict(FIXTURES)
        if overrides:
            self._fixtures.update(overrides)

    def _answer(self, prompt: str) -> Completion:
        match = _TASK_RE.search(prompt or "")
        task = match.group(1).lower() if match else ""
        payload = self._fixtures.get(task, _DEFAULT)
        self.calls.append({"task": task, "prompt": prompt})
        return Completion(
            text=json.dumps(payload, ensure_ascii=False), model="mock", raw={"mock": True}
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Completion:
        joined = "\n".join(m.content for m in messages)
        return self._answer(joined)

    async def vision(
        self,
        images: list[ImagePart],
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1200,
    ) -> Completion:
        return self._answer(f"{system or ''}\n{prompt}")

    async def transcribe(self, audio: bytes, mime: str = "audio/ogg") -> str:
        return "чувствую сонливость и потливость, самочувствие на три"

    async def aclose(self) -> None:
        return None


__all__ = ["FIXTURES", "MockClient"]
