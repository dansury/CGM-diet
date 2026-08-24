"""Prompts for every recognition task.

Every prompt starts with a `TASK: <name>` marker — the mock client routes on
it, and it makes model logs greppable. All prompts demand raw JSON and forbid
clinical advice; interpretation happens later, from the user's own data.
See `spec/ingest.md`.
"""

from __future__ import annotations

from src.analytics.tags import TAGS

_TAG_LIST = ", ".join(TAGS)

SYSTEM = (
    "Ты — точный экстрактор структурированных данных для дневника питания и глюкозы. "
    "Отвечай ТОЛЬКО валидным JSON без markdown-обёртки и без комментариев. "
    "Не назначай лечение, не считай дозы лекарств, не ставь диагнозы. "
    "Если данных не хватает — ставь null, не выдумывай."
)

FOOD_PHOTO = f"""TASK: food_photo
Определи, что на фото еды.
Верни JSON:
{{
  "title": "короткое название блюда",
  "confidence": 0.0-1.0,
  "items": [
    {{"name": "продукт", "portion_g": число|null, "kcal": число|null,
      "protein_g": число|null, "fat_g": число|null, "carbs_g": число|null,
      "fiber_g": число|null, "tags": ["из списка ниже"]}}
  ],
  "notes": "как оценивалась порция"
}}
Допустимые tags: {_TAG_LIST}.
Оценивай порции по опорным объектам (тарелка ~26 см, вилка, рука).
Если на фото нет еды — верни {{"title": "", "items": [], "confidence": 0, "notes": "no_food"}}.
"""

LABEL_PHOTO = f"""TASK: label_photo
На фото — упаковка продукта (может быть 2 фото: лицевая и оборотная сторона одного
и того же продукта; объедини их в одну карточку).
Верни JSON:
{{
  "brand": "бренд|null", "name": "название продукта",
  "barcode": "цифры штрихкода|null",
  "per_100": {{"kcal": ч|null, "protein_g": ч|null, "fat_g": ч|null,
               "carbs_g": ч|null, "sugars_g": ч|null, "fiber_g": ч|null}},
  "ingredients": ["состав по порядку, как напечатан"],
  "additives": ["E-добавки и добавки по названию"],
  "flags": ["из списка ниже"],
  "confidence": 0.0-1.0
}}
Допустимые flags: {_TAG_LIST}.
Пищевую ценность бери строго на 100 г/мл. Если указана на порцию — пересчитай
и укажи это в поле name.
"""

GLUCOSE_SCREENSHOT = """TASK: glucose_screenshot
На изображении — экран CGM-приложения или глюкометра.
Верни JSON:
{
  "unit": "mmol/L" | "mg/dL",
  "device": "название устройства|null",
  "readings": [{"measured_at": "YYYY-MM-DDTHH:MM:SS", "value": число,
                "trend": "up|up_slow|flat|down_slow|down|null"}],
  "confidence": 0.0-1.0
}
Правила:
- время — локальное время с экрана, без часового пояса;
- если на экране только одно текущее значение — верни одну запись;
- если виден график с подписанными точками — верни все читаемые точки;
- единицы определи по величине: значения 3–25 — это mmol/L, 50–450 — mg/dL;
- если дата не видна — оставь только время: "T HH:MM:SS" запрещено, вместо
  этого верни "measured_at": null, и укажи "time": "HH:MM".
"""

LAB_REPORT = """TASK: lab_report
Перед тобой результат лабораторного анализа (фото, скан или текст).
Верни JSON:
{
  "panel": "название панели|null",
  "taken_at": "YYYY-MM-DD|null",
  "markers": [{"marker": "название", "value": число|null, "value_text": "строка|null",
               "unit": "единицы|null", "ref_low": число|null, "ref_high": число|null}],
  "confidence": 0.0-1.0
}
Референсные интервалы бери из самого документа. Ничего не интерпретируй.
"""

TEXT_MEAL = f"""TASK: text_meal
Пользователь описал приём пищи текстом. Разбери его на продукты.
Верни JSON:
{{
  "title": "короткое название",
  "confidence": 0.0-1.0,
  "items": [{{"name": "продукт", "portion_g": ч|null, "kcal": ч|null, "protein_g": ч|null,
              "fat_g": ч|null, "carbs_g": ч|null, "fiber_g": ч|null, "tags": [...]}}],
  "notes": ""
}}
Допустимые tags: {_TAG_LIST}.
Текст пользователя:
"""

SYMPTOM_EXTRACT = """TASK: symptom_extract
Пользователь описал самочувствие (текст или расшифровка голосового).
Верни JSON:
{
  "score": 1..5|null,       // 5 = отлично, 1 = очень плохо
  "symptoms": ["короткие названия симптомов в именительном падеже, по-русски"],
  "note": "остальное, что стоит сохранить"
}
Не ставь диагнозов, не предполагай причин. Только то, что сказал пользователь.
Текст:
"""

__all__ = [
    "FOOD_PHOTO",
    "GLUCOSE_SCREENSHOT",
    "LABEL_PHOTO",
    "LAB_REPORT",
    "SYMPTOM_EXTRACT",
    "SYSTEM",
    "TEXT_MEAL",
]
