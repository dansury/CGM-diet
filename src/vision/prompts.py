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
Числа обязательны: для каждой позиции дай portion_g, kcal, protein_g, fat_g,
carbs_g, fiber_g. Точных данных нет — ставь типичную оценку для такого блюда и
такой порции и снижай confidence; `null` допустим, только если позиция вообще
не еда. Ноль пиши, лишь когда нутриента в продукте действительно нет
(например, углеводы в отварной курице).
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
Числа обязательны: для каждой позиции дай portion_g, kcal, protein_g, fat_g,
carbs_g, fiber_g. Пользователь не назвал вес — возьми типичную порцию
(«наггетсы» ≈ 150 г) и оцени нутриенты по ней, снизив confidence. `null` и 0
допустимы, только если нутриента в продукте действительно нет.
Текст пользователя:
"""

MEAL_CORRECTION = f"""TASK: meal_correction
Есть уже распознанный приём пищи и уточнение пользователя. Верни ИСПРАВЛЕННЫЙ
приём пищи в том же JSON:
{{
  "title": "краткое название",
  "items": [{{"name","portion_g","kcal","protein_g","fat_g","carbs_g","fiber_g","tags":[]}}],
  "notes": ""
}}
Правила:
- меняй только то, чего касается уточнение; остальные позиции переноси как есть,
  вместе с их числами;
- если поменялась порция — пересчитай нутриенты этой позиции пропорционально;
- если позицию просят убрать — убери; просят добавить — добавь с оценкой
  нутриентов: portion_g, kcal, protein_g, fat_g, carbs_g, fiber_g заполнены
  числами, а не null;
- ничего не выдумывай сверх сказанного, слово пользователя всегда сильнее фото.
Допустимые tags: {_TAG_LIST}.
"""

CLASSIFY = """TASK: classify_photo
Определи, что изображено. Верни JSON:
{"kind": "food" | "glucose_screen" | "food_label" | "lab_report" | "medication"
       | "body_scale" | "workout" | "other",
 "confidence": 0.0-1.0}
Пояснения:
- food — тарелка/блюдо/еда как она есть;
- glucose_screen — экран приложения CGM или глюкометра с показателями сахара;
- food_label — упаковка продукта, состав, пищевая ценность, штрихкод;
- lab_report — бланк лабораторного анализа;
- medication — упаковка лекарства, блистер, флакон, инструкция к препарату;
- body_scale — экран напольных весов или распечатка анализа состава тела;
- workout — экран трекера/часов/фитнес-приложения или дневник тренировок,
  в том числе рукописный;
- other — всё остальное.
Упаковка лекарства и упаковка продукта питания различаются: у лекарства есть
название препарата и дозировка (мг/мл), у продукта — пищевая ценность на 100 г.
"""

MEDICATION_PHOTO = """TASK: medication_photo
На фото упаковка лекарства (коробка, блистер, флакон). Верни JSON:
{
  "name": "как написано на упаковке, торговое название",
  "inn": "международное непатентованное название (действующее вещество) или null",
  "dose_text": "дозировка одной единицы, как напечатано: «850 мг», «5 мг/мл» или null",
  "form": "таблетки|капсулы|раствор|сироп|мазь|спрей|инъекция|другое|null",
  "confidence": 0.0-1.0
}
Только то, что видно на упаковке. Не предлагай схему приёма, не считай дозу,
не пиши показания и противопоказания — этого не требуется.
"""

WORKOUT_TEXT = """TASK: workout_text
Пользователь рассказал о тренировке, прогулке или физической работе.
Верни JSON:
{
  "kind": "walking|running|cycling|swimming|strength|hiit|elliptical|rowing|yoga|
           stretching|dance|football|basketball|tennis|boxing|skiing|skating|
           stairs|housework|other",
  "title": "как назвал сам пользователь",
  "duration_min": число|null,
  "intensity": "low|moderate|high"|null,   // только если пользователь сам сказал
  "distance_m": число|null,
  "steps": число|null,
  "avg_hr": число|null,
  "rpe": 1-10|null,
  "sweat": "yes|light|no"|null,
  "kcal": число|null,        // только если пользователь назвал сам или это с экрана часов
  "time": "HH:MM"|null,      // когда началась, если сказано
  "note": "остальное",
  "confidence": 0.0-1.0
}
Ничего не додумывай: не названо — null. Калории не оценивай сам, их считает
калькулятор. Расстояние переводи в метры, длительность — в минуты.
Текст пользователя:
"""

WORKOUT_PHOTO = """TASK: workout_photo
На фото — экран фитнес-трекера, часов, приложения или страница бумажного
дневника тренировок (возможно, написанная от руки).
Прочитай, что там записано, включая рукописный текст, и верни JSON:
{
  "kind": "walking|running|cycling|swimming|strength|hiit|elliptical|rowing|yoga|
           stretching|dance|football|basketball|tennis|boxing|skiing|skating|
           stairs|housework|other",
  "title": "название тренировки как написано",
  "duration_min": число|null, "intensity": "low|moderate|high"|null,
  "distance_m": число|null, "steps": число|null, "avg_hr": число|null,
  "rpe": 1-10|null, "sweat": "yes|light|no"|null,
  "kcal": число|null,        // только если число напечатано/написано на фото
  "time": "HH:MM"|null, "note": "прочие пометки", "confidence": 0.0-1.0
}
Читай только то, что видно. Неразборчивое поле — null, и снизь confidence.
"""

BODY_SCALE = """TASK: body_scale
На фото — экран напольных весов, приложение весов или распечатка
биоимпедансного анализа состава тела. Верни JSON:
{
  "weight_kg": число|null,
  "body_fat_pct": число|null,
  "muscle_mass_kg": число|null,
  "water_pct": число|null,
  "bone_mass_kg": число|null,
  "visceral_fat": число|null,
  "bmr_kcal": число|null,
  "confidence": 0.0-1.0
}
Проценты бери как проценты, массы — в килограммах. Если показатель на экране
в фунтах — пересчитай в килограммы. Не выводи ничего, чего нет на фото, и не
оценивай состояние человека.
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
    "BODY_SCALE",
    "CLASSIFY",
    "FOOD_PHOTO",
    "GLUCOSE_SCREENSHOT",
    "LABEL_PHOTO",
    "LAB_REPORT",
    "MEAL_CORRECTION",
    "MEDICATION_PHOTO",
    "SYMPTOM_EXTRACT",
    "SYSTEM",
    "TEXT_MEAL",
    "WORKOUT_PHOTO",
    "WORKOUT_TEXT",
]
