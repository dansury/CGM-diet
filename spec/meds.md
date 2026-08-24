# meds — лекарства: журнал, фото, справочник побочек

Бот **не назначает и не отменяет** препараты и не считает дозы (конституция, I).
Здесь только журнал приёма, распознавание упаковки и справка из открытой базы
побочных эффектов.

## Ввод

1. **Фото упаковки** — `classify_photo` возвращает `medication` →
   `recognize_medication(images)` → `MedicationDraft` → карточка `med:*`.
2. **Текст** — `parse_text` (`ingest/text_parse.py`) как и раньше: «выпил метформин 850».
3. **Личный словарь** — кнопка `dict:use:<id>` пишет дозу «сейчас» одним нажатием.

Подтверждённое лекарство всегда попадает в личный словарь (`spec/dictionary.md`),
`kind="medication"`, с первого раза.

## Черновик (`src/vision/schemas.py`)

```
MedicationDraft  name  inn?  dose_text?  form?  route?  taken_at?  note?
                 confidence?  raw_text?
med_to_dict / med_from_dict          # переживает JSON-round-trip в FSM
```

`name` — как на упаковке (торговое), `inn` — МНН, если модель его узнала.

## Справочник (`src/meds/`)

```
catalog.py
  normalize_drug(name) -> slug            # lower, латиница/кириллица, без дозы и формы
  resolve_cid(name) -> str | None         # slug → STITCH CID (PubChem CID)
  known_drugs() -> tuple[DrugEntry, ...]
  DrugEntry(slug, inn_ru, inn_en, cid, labels[])
  # источник: config/drug_cids.json (в репозитории, ~120 частых препаратов)

side_effects.py
  SideEffect(cui, name_en, name_ru)
  load_side_effects(path?, refresh=False) -> dict[cid, tuple[SideEffect, ...]]
  side_effects_for(name) -> tuple[SideEffect, ...]        # по названию препарата
  match_symptoms(name, labels[]) -> tuple[SymptomMatch, ...]
  SymptomMatch(symptom, effect)
  dataset_status() -> DatasetStatus(path, rows, drugs, sample)
```

Данные: **ChSe-Decagon_monopharmacy** (SNAP BioSNAP, CC-BY).
CSV `STITCH, Individual Side Effect, Side Effect Name`.

- рабочий файл `data/side_effects/ChSe-Decagon_monopharmacy.csv.gz` — не в
  репозитории, качается `python -m scripts.fetch_side_effects`;
- при его отсутствии грузится committed-выборка `seeds/side_effects_sample.csv`
  (частые препараты) — бот работает из коробки и в CI, `sample=True`;
- сопоставление симптом ↔ побочка: `config/side_effects_ru.json`
  (`{"<english side effect>": "<русская формулировка>"}`) плюс нормализация
  через `analytics/tags.normalize_name`.

## Аналитика (`src/analytics/meds.py`) — чистая, без ORM

```
MedicationLike(id, taken_at, name, slug)
DoseWindow(slug, name, start, end)

dose_windows(meds, *, hours=8) -> list[DoseWindow]
coverage(excursions, meds, *, hours=8) -> list[Coverage]
  Coverage(slug, name, n_covered, n_total, share)
  # сколько пригодных экскурсий пришлось на окно после приёма — это конфаундер,
  # а не эффект: цифры по еде читаются с поправкой на него
symptom_links(meds, checkins, lookup, *, hours=8, min_hits=2) -> list[SymptomLink]
  SymptomLink(slug, name, symptom, effect_ru, n_after_dose, n_total)
  # симптом отмечен в окне после приёма И числится побочкой этого препарата
  # в справочнике. lookup: (name) -> tuple[SideEffect, ...]
```

`min_hits=2` — единичное совпадение не показывается (конституция, II).

## Формулировки (`src/reporting.py`)

```
format_medications(rows, unit) -> str                  # журнал за период
format_med_coverage(coverages) -> str | None           # строка-конфаундер к /stats
format_med_side_effects(links) -> str | None           # справка по побочкам
```

Обязательно и дословно:
- «в справочнике побочных эффектов этот симптом указан для препарата» —
  **никогда** «препарат вызывает»;
- «совпадение по времени — не доказательство причины»;
- «не меняйте приём и дозу самостоятельно — только с врачом».

## Хранение

`medications` получает `slug`, `cid`, `media_id`, `source` — см. `spec/data_model.md`.
