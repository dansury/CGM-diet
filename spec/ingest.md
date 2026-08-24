# ingest — маршрутизация и распознавание ввода

## Router (`src/handlers/intake.py`)

Приоритет:
1. **Явный режим** (`pending_mode` в FSM): `check_product` / `meal` / `glucose` —
   ставится кнопками меню и командами `/check`, `/eat`, `/sugar`.
2. **Автоклассификация фото** (`recognize.classify_photo`) →
   `food | glucose_screen | food_label | lab_report | other`. `other` → 4 кнопки.
3. **Текст**: сначала `ingest.text_parse` (без модели), остаток → `parse_meal_text`.
4. **Голос**: `_transcribe` (SpeechKit, см. ниже) → тот же маршрут, что и текст;
   в состоянии `WellbeingFlow.free_text` — сразу в извлечение симптомов.
5. **Документ**: `image/*` → анализы по фото; `pdf` → `ingest.pdf.pdf_to_text`,
   при пустом тексте — просьба прислать фото страницы.

Альбомы: `deps.AlbumBuffer` копит сообщения по `media_group_id` и через
1.2 с отдаёт их одним пакетом (нужно для двух сторон упаковки).

## Голос (`src/ingest/speechkit.py`)

Yandex SpeechKit STT v1, REST, транспорт httpx. Порт из GrowthProducer
(`src/tools/speechkit.py`).

```
recognize_voice(audio, *, api_key, folder_id=None, lang="ru-RU", client=None) -> SpeechResult
SpeechResult(text, duration_sec, language, segments)
SpeechKitAuthError (401/403) · SpeechKitQuotaExceeded (429) · UnsupportedFormat (не OGG/пусто)
MAX_BYTES = 950_000 · MAX_DURATION_SEC = 25 · OPUS_SAMPLE_RATE = 48000
```

- лимиты API: <1 МБ и <30 с на запрос; длинное голосовое режется по границам
  OGG-страниц на ~25-секундные куски, каждый достраивается в самостоятельный
  поток (`seqno` с нуля, BOS/EOS, CRC=0 — SpeechKit это допускает). Чистый
  Python, ffmpeg не нужен;
- параметры запроса: `format=oggopus`, `sampleRateHertz=48000`,
  `model=general:rc`, `profanityFilter=false`, `folderId`;
- тексты кусков склеиваются пробелом в порядке следования;
- 5xx → пустая строка (сбой одного куска не роняет остальные).

Маршрутизация в `handlers/intake`:

```
_transcribe(message, data, mime) -> str|None      # None = пользователю уже сказали, почему
_transcribe_via_llm(message, data, mime) -> str|None
```

- `settings.speechkit_available` → SpeechKit; иначе сразу `client.transcribe`;
- 429 → «перегружено, попробуйте через минуту», ничего не пишем;
- 401/403, не-OGG (`audio`-вложение), прочий сбой → фолбэк на
  `client.transcribe`; ключ, который не приняли, уходит в ERROR-лог владельцу;
- пустой транскрипт → «ничего не расслышал».

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

Черновики (`src/vision/schemas.py`): `MealDraft/ItemDraft` (`ItemDraft.estimated`
— числа добраны из таблицы, см. ниже), `ProductDraft`,
`GlucoseDraft`, `LabDraft/MarkerDraft`. `MarkerDraft.flag` вычисляется из
референса документа. Пары `*_to_dict`/`*_from_dict` — для хранения в FSM.

## Нутриенты (`src/ingest/nutrition.py`)

Модель регулярно возвращает название без чисел (`kcal: null`, БЖУ `null`) —
карточка тогда печатала «0 ккал · Б 0 · Ж 0 · У 0», то есть выдавала пропуск за
измерение. Добор пропусков:

```
Ref(kcal, protein_g, fat_g, carbs_g, fiber_g, portion_g)   # на 100 г + типичная порция
TABLE: ((keyword, Ref), ...)          # ~70 распространённых блюд
lookup(name) -> Ref|None              # normalize_name, побеждает самое длинное вхождение
fill_item(item) -> bool               # заполнил ли что-нибудь
fill_meal(draft) -> bool              # + приписка ESTIMATE_NOTE в notes

Remembered(kcal, protein_g, fat_g, carbs_g, fiber_g, portion_g)  # на 100 г, любое поле None
per_100(item) -> Remembered           # абсолютные числа позиции → на 100 г
match_memory(name, memory) -> Remembered|None    # точное normalize_name, затем длиннейшее вхождение
apply_memory(draft, memory) -> list[str]         # имена позиций, куда подставили
DEFAULT_BASIS_G = 100.0               # база, когда вес позиции неизвестен
```

Правила добора (`fill_item` / `fill_meal`):
- заполняются только пустые поля (`None` или `0`); числа модели и пользователя
  не переписываются никогда;
- `portion_g` берётся из таблицы, **только** если ни один макронутриент не
  известен (иначе числа модели уже подразумевают свою порцию);
- нутриенты = `Ref × portion/100`;
- `kcal` пустой, а БЖУ есть → Атуотер `4·Б + 9·Ж + 4·У`;
- совпадения в таблице нет и считать не из чего → поля остаются пустыми,
  карточка просит вес, но нулей не показывает;
- заполненная позиция получает `ItemDraft.estimated = True`; в карточке
  порция и «Итого» помечаются `≈`; позиция с `macros_source` (`user`/`memory`)
  флаг не получает — числа пользователя не оценка, даже если клетчатку добрали
  из таблицы;
- `ESTIMATE_NOTE` приписывается только когда после добора есть хоть одна
  `estimated`-позиция.

Вызывается в `_meal_from_payload` (фото, текст, правка моделью) и в
`apply_meal_correction` после локальных правок (позиция, добавленная
пользователем, приходит без чисел).

**Память БЖУ пользователя** (`apply_memory`, порядок: память → таблица):

- ключ — `normalize_name(item.name)`; хранение и репозиторий описаны в
  `spec/dictionary.md` § Память БЖУ;
- значения хранятся на 100 г и пересчитываются на текущую порцию; порция
  берётся из памяти, если у позиции её нет;
- перезаписываются даже непустые поля — сохранённое слово пользователя сильнее
  оценки модели и таблицы (конституция III); не трогается только позиция с
  `macros_source == "user"` (правка этого же черновика новее памяти);
- `kcal` пустой → Атуотер по подставленным БЖУ; `estimated=False`,
  `macros_source="memory"`, в `notes` — `MEMORY_NOTE`;
- вызывается из `handlers/views.fill_from_memory` — единственная воронка всех
  карточек еды (фото, текст, правка, кнопка словаря); сбой похода в БД
  проглатывается (fail-soft), карточка показывается на числах модели.

Промпты `FOOD_PHOTO`, `TEXT_MEAL`, `MEAL_CORRECTION` требуют числа по каждой
позиции: нет точных данных — типичная оценка и пониженный `confidence`; `null`
только для не-еды, `0` — только для реально отсутствующего нутриента.

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
| `<блюдо> [N г] б 5 ж 2 у 30 [120 ккал]` | ставит БЖУ (и порцию, если названа) |

**БЖУ в правке** (`_parse_macros`, разбирается **до** правил порции — иначе
«гречка б 5 ж 2 у 30» читается как «гречка — 30 г»):

- ключи: `б|белки|протеин`, `ж|жиры`, `у|углеводы`, `клетчатка|волокна`,
  `к|ккал|калории`; `120 ккал` — единица после числа;
- числа абсолютные, для текущей порции позиции; порция из остатка клаузы
  (`гречка 250 г …`) ставится без пересчёта БЖУ;
- имя не названо и позиция одна → правка идёт в неё; имени нет ни в одной
  позиции → позиция добавляется;
- `kcal` не назван → Атуотер по названным БЖУ; `estimated=False`,
  `macros_source="user"`, `Change(kind="macros")`.

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
