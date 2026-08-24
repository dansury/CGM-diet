# ingest — маршрутизация и распознавание ввода

## Router (`src/handlers/intake.py`)

Приоритет:
1. **Явный режим** (`pending_mode` в FSM): `check_product` / `meal` / `glucose` —
   ставится кнопками меню и командами `/check`, `/eat`, `/sugar`.
2. **Автоклассификация фото** (`recognize.classify_photo`) →
   `food | glucose_screen | food_label | lab_report | other`. `other` → 4 кнопки.
3. **Текст**: сначала `ingest.text_parse` (без модели), остаток → `parse_meal_text`.
4. **Голос**: `client.transcribe` → тот же маршрут, что и текст; в состоянии
   `WellbeingFlow.free_text` — сразу в извлечение симптомов.
5. **Документ**: `image/*` → анализы по фото; `pdf` → `ingest.pdf.pdf_to_text`,
   при пустом тексте — просьба прислать фото страницы.

Альбомы: `deps.AlbumBuffer` копит сообщения по `media_group_id` и через
1.2 с отдаёт их одним пакетом (нужно для двух сторон упаковки).

## Промпты (`src/vision/prompts.py`)

Каждый начинается с `TASK: <name>`. Общий `SYSTEM`: только валидный JSON, без
markdown, без назначений и диагнозов, `null` вместо выдумки.

| Константа | Задача | Возвращает |
|---|---|---|
| `CLASSIFY` | тип фото | `{kind, confidence}` |
| `FOOD_PHOTO` | еда | `{title, confidence, items[], notes}` |
| `LABEL_PHOTO` | этикетка (1–2 фото) | `{brand, name, barcode, per_100{}, ingredients[], additives[], flags[], confidence}` |
| `GLUCOSE_SCREENSHOT` | экран CGM | `{unit, device, readings[{measured_at,value,trend}], confidence}` |
| `LAB_REPORT` | анализы | `{panel, taken_at, markers[{marker,value,unit,ref_low,ref_high}], confidence}` |
| `TEXT_MEAL` | еда текстом | как `FOOD_PHOTO` |
| `SYMPTOM_EXTRACT` | самочувствие | `{score, symptoms[], note}` |

`tags`/`flags` ограничены словарём `analytics/tags.TAGS`.

## Распознавание (`src/vision/recognize.py`)

```
classify_photo(images) -> (kind, confidence)          # при сбое -> ("other", 0.0)
recognize_meal_photo(images, hint="") -> MealDraft
parse_meal_text(text) -> MealDraft
recognize_label(images) -> ProductDraft
recognize_glucose_screenshot(images, now) -> ([GlucoseDraft], device)
recognize_labs(images?|text?, now) -> LabDraft
extract_symptoms(text) -> (score?, [symptom], note)
RecognitionError — ответ пришёл, но непригоден
```

Правила валидации:
- пустой список items/markers/readings → `RecognitionError`;
- значение глюкозы вне физиологичного диапазона отбрасывается;
- единица перепроверяется по величине (`guess_unit`), если заявленная не бьётся;
- `measured_at: null` + `time: "HH:MM"` → сегодняшняя дата, будущее время
  откатывается на сутки;
- `barcode` только из цифр, иначе `None`;
- теги прогоняются через `normalize_tags` (неизвестные отбрасываются, при пустом
  списке выводятся из названия).

Черновики (`src/vision/schemas.py`): `MealDraft/ItemDraft`, `ProductDraft`,
`GlucoseDraft`, `LabDraft/MarkerDraft`. `MarkerDraft.flag` вычисляется из
референса документа. Пары `*_to_dict`/`*_from_dict` — для хранения в FSM.

## Текст без модели (`src/ingest/text_parse.py`)

`parse_text(text, now) -> ParsedText{glucose[(value,unit)], weight_kg, wellbeing,
medications[(name,dose)], at, fasting, leftover}`.

- глюкоза: `(сахар|глюкоза|гк|glucose|sugar|bg) [:=-]? число [единицы]?`;
  без ключевого слова — только при явных единицах (`8.9 ммоль/л`);
- вес `25..400` кг, самочувствие `1..5`, лекарства по глаголу приёма;
- время: **дата ищется первой** (`ДД.ММ`, месяц обязательно двузначный) — иначе
  «сахар 9.1» читается как 9 января; затем время `ЧЧ:ММ` вне уже занятых
  диапазонов; «вчера» и «время в будущем» откатывают на сутки;
- `leftover` — остаток текста, из которого вычищены распознанные фрагменты и
  служебные слова; он уходит в разбор еды.

## Единицы (`src/ingest/units.py`)

`MG_DL_PER_MMOL = 18.0182`. Диапазоны правдоподобия: ммоль/л `1.0..33.3`,
мг/дл `18..600`. `guess_unit(v)`: `<=33.3` → ммоль/л (перекрытие 18–33
разрешается в пользу ммоль/л, пользователь переключает кнопкой).
`format_value` / `format_delta` печатают в единицах пользователя;
отрицательная дельта — с настоящим минусом «−».

## PDF (`src/ingest/pdf.py`)

`pdf_to_text(data, max_pages=10) -> str`. PyMuPDF — опциональный extra `[pdf]`.
Нет библиотеки или нет текстового слоя → `""` → бот просит фото страницы.


## Корректировки (`src/ingest/correction.py`)

Правка **сливается** с распознаванием, а не заменяет его: то, чего пользователь
не назвал, сохраняет свои числа.

```
Change(kind: portion|rename|remove|add, item, before, after) ; describe() -> str
CorrectionResult(draft, changes[], unmatched[])
apply_meal_correction(draft, instruction) -> CorrectionResult
```

Разбор по предложениям (`;`, перевод строки), внутри — по запятым и «и»;
переименование («не гречка, а перловка») владеет своей запятой и разбирается
целиком. Клаузы:

| Формулировка | Что делает |
|---|---|
| `убери / удали / без / минус <блюдо>` | удаляет позицию |
| `добавь / плюс / ещё <блюдо> [N]` | добавляет (или меняет порцию, если уже есть) |
| `не <A>, а <B>` · `вместо <A> <B>` · `<A> -> <B>` | переименовывает, порция и нутриенты остаются |
| `<блюдо> [было] N [г]` | ставит порцию и **пересчитывает нутриенты** пропорционально |
| `N г` при одной позиции | то же для единственного блюда |

Поиск позиции: точное `normalize_name` → подстрока → общий корень слова
(`STEM_LEN = 4`, «гречки» ≈ «гречневая»). Неразобранное копится в `unmatched`.

**Эскалация к модели.** Есть `unmatched` или ноль изменений → `recognize.correct_meal(draft, instruction)`:
модель получает текущий черновик JSON и уточнение и возвращает исправленный
черновик (`TASK: meal_correction`), то есть правит, а не распознаёт заново.

**Голосом.** Транскрипт в `handlers/intake._route_voice` уходит в открытый
поток: `MealFlow.editing|retiming`, `GlucoseFlow.editing`, `LabFlow.editing`,
`ProductFlow.editing`, `MedicationFlow.editing|retiming`. Без этого голосовое
в режиме правки начинало бы новую запись вместо исправления карточки.

Каждая правка пишет строку в `corrections` (сырой текст пользователя) —
конституция III: слово пользователя сильнее машины и хранится.

Карточка после правки показывает блок «Учтено из вашей правки» — видно, что
правка встроена, а не что карточка собрана заново.

## Лекарства

`classify_photo` знает вид `medication`; `recognize_medication(images)` читает с
упаковки только название, МНН, дозировку и форму — ни показаний, ни схемы
приёма (`spec/meds.md`).
