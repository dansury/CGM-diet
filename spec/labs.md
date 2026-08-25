# labs — анализы и продукты-источники

Ввод (фото, PDF, текст) — `spec/ingest.md` § Анализы; хранение —
`analysis_results` (`spec/data_model.md`). Здесь — взгляд назад.

## Граница

Сравниваем **только** с референсом из самого документа. Дальше — не
расшифровка, а обычная рекомендация по питанию: перечень продуктов-источников
нутриента. Ни диагнозов, ни дефицитов, ни БАДов, ни доз (`spec/clinical.md`).

## Модель (`src/analytics/labs.py`)

```
LabValue(marker, taken_at, value?, value_text?, unit?, ref_low?, ref_high?,
         flag?, panel?) ; .display
Nutrient(key, label, markers[], foods[], note, when(low|high))
FoodHint(nutrient, value, direction) ; .foods
LabReview(hints[], out_of_range[], n_markers)
```

```
load_catalog()->[Nutrient]      # config/nutrient_foods.json, кэш; reset_catalog()
match_nutrient(marker)->Nutrient|None    # подстрока, побеждает самое длинное совпадение
direction(value)->low|high|None          # flag, иначе сравнение с ref_low/ref_high
latest_values(values)->[LabValue]        # последнее значение на маркер, новые сверху
review(values)->LabReview
```

`when` — сторона референса, связанная с едой: у большинства нутриентов `low`,
у `fiber` (ЛПНП, триглицериды) — `high`. Значение по другую сторону попадает в
`out_of_range`, но продуктов не получает.

## Справочник (`config/nutrient_foods.json`)

`nutrients: {key: {label, markers[], foods[], note, when}}` — 12 нутриентов:
iron, vitamin_d, b12, folate, magnesium, potassium, calcium, zinc, iodine,
protein, omega3, fiber. Недоступный файл = подсказок нет, маркеры сохраняются
и показываются как обычно.

## Поток (`src/handlers/labs.py`)

```
lab_review_text(session, user, header?)->str
/labs -> последние значения + продукты-источники
```
После `lab:ok` тот же текст уходит отдельным сообщением
(`spec/bot.md` § Потоки).

## Тексты (`src/reporting.py`)

```
LAB_ADVICE_DISCLAIMER
format_lab_value(value)->str      # 🔺/🔻/✅ маркер: значение (референс a–b)
format_food_hint(hint)->str       # «ниже референса из документа» + источники
format_lab_review(review, header?)->str
```
