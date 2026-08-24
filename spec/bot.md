# bot — команды, клавиатуры, FSM, обработчики

## Runtime (`src/bot.py`)

`BOT_MODE=polling` (по умолчанию) → `dispatcher.start_polling`.
`BOT_MODE=webhook` → uvicorn с `src.web.app:create_app`.
`build_bot(settings)` — `ParseMode.HTML` по умолчанию.
`COMMANDS` — 13 команд, регистрируются в меню Telegram при старте.
`prepare_runtime(bot, settings)` до первого апдейта (и в polling, и в webhook):
`wire_error_reporter` → `load_active_models` → каталог свободных моделей.
Каждый шаг деградирует молча — ни один не мешает боту стартовать.

## Команды

| Команда | Что делает | Файл |
|---|---|---|
| `/start` | регистрация, согласие, онбординг | `handlers/common.py` |
| `/my` | личный словарь: запись одной кнопкой | `handlers/dictionary.py` |
| `/meds` | журнал лекарств + справка по побочкам | `handlers/meds.py` |
| `/help` | подробная справка | `handlers/common.py` |
| `/menu` | вернуть клавиатуру | `handlers/common.py` |
| `/settings`, `/set` | пояс, единицы, окна, базовая линия | `handlers/common.py` |
| `/eat`, `/sugar`, `/check` | явные режимы ввода | `handlers/intake.py` |
| `/wellbeing` | опрос самочувствия | `handlers/wellbeing.py` |
| `/today` | записи за сегодня | `handlers/reports.py` |
| `/stats` | статистика + метрики + симптомы + рекомендации | `handlers/reports.py` |
| `/graph` | таймлайн, самочувствие, рейтинг | `handlers/reports.py` |
| `/export` | ZIP с CSV | `handlers/reports.py` |
| `/delete` | удаление с подтверждением | `handlers/reports.py` |
| `/health` | инструкция и токен Samsung Health | `handlers/reports.py` |
| `/model`, `/models`, `/errors`, `/whereami` | только владелец, только в личке | `handlers/admin.py` |

## Клавиатуры (`src/keyboards.py`)

Reply-меню: `🍽 Записать еду`, `🩸 Записать сахар`, `🛒 Проверить продукт`,
`🙂 Самочувствие`, `📊 Статистика`, `📈 График`, `⭐️ Мой словарь`, `💊 Лекарства`.

На каждой карточке распознавания — расшифровка текстом и две кнопки:
`✅ Подтвердить` и `✏️ Скорректировать` (правку принимаем текстом **и голосом**).

Callback-грамматика `<domain>:<action>[:<arg>]` (лимит Telegram — 64 байта):

```
meal:ok|edit|time|drop         glu:ok|edit|unit|drop
prod:eat|save|more|drop        lab:ok|drop
kind:food|glucose_screen|food_label|lab_report|medication|drop   photo:reroute
med:ok|edit|time|drop         prod:edit   lab:edit
dict:use:<id>|rm:<id>|new|page:<kind>:<n>|mode:<kind>:<use|del>|close
mdl:lvl:<global|slot|free> | mdl:slot:<slot> | mdl:set:<target>:<idx> | mdl:close
wb:score:<1..5> | wb:sym:<id> | wb:other | wb:voice | wb:done
stats:w:<1h|2h> | stats:k:<tag|item> | stats:chart
del:yes|no
```

Черновики **не** передаются в callback-data — они лежат в FSM. Устаревшая
кнопка не может воскресить чужие данные.

## FSM (`src/handlers/states.py`)

```
MealFlow.confirming|editing|retiming
GlucoseFlow.confirming|editing
ProductFlow.confirming|awaiting_second_side|editing
LabFlow.confirming|editing
MedicationFlow.confirming|editing|retiming
WellbeingFlow.scoring|picking|free_text
SettingsFlow.editing
```

Ключи данных: `draft`, `draft_files`, `eaten_at`, `taken_at`, `draft_mode`,
`pending_mode`, `pending_photos`, `wb_selected`, `wb_score`, `wb_extra`,
`wb_note`, `dict_pending`, `mdl_candidates`, `mdl_target`.

## Порядок роутеров (`src/handlers/__init__.py`)

`admin → common → reports → wellbeing → dictionary → meds → confirm → intake → errors`.
`admin` первый и полностью отфильтрован (владелец + личка): чужому апдейту он
просто не соответствует и тот идёт дальше. `intake` предпоследний: он ловит
любой текст и любое фото. `errors` — наблюдатель `router.errors`, обработчиков
сообщений не содержит.

## Плумбинг (`src/handlers/deps.py`)

```
session_scope() -> AsyncSession           # commit/rollback на обработчик
user_tz(user) / local_now(user) / to_utc(dt, user) / to_local(dt, user)
download_photo(bot, file_id) -> ImagePart # лимит 12 МБ
AlbumBuffer.add(group_id, message, flush) # дебаунс 1.2 с
sha256(data)
```

## Потоки

**Еда:** фото → `classify_photo` → `recognize_meal_photo` → `views.show_meal_draft`
(FSM `MealFlow.confirming`) → `meal:ok` → `repo.save_meal`.
`meal:edit` → строка `гречка 250, курица 100` → `_parse_edit` пересчитывает
нутриенты пропорционально порции, сохраняет теги, пишет `corrections`.

**Глюкоза:** скриншот → `recognize_glucose_screenshot` → карточка →
`glu:ok` (сохранить), `glu:unit` (пересчитать шкалу), `glu:edit` (`8.2 в 9:15`).

**Продукт:** фото(а) → `recognize_label` → в режиме `check` текст строится
`reports.product_verdict_text` по статистике пользователя; `prod:eat`
превращает продукт в `MealDraft` с порцией 100 г (редактируемой).

**Анализы:** фото/PDF/текст → `recognize_labs` → карточка с пометками
`🔺/🔻/✅` → `lab:ok`.

**Лекарство:** фото упаковки → `recognize_medication` → карточка `med:*` →
`repo.save_medication_draft` (журнал + личный словарь). `spec/meds.md`.

**Свободный текст:** `parse_text` пишет сахар/вес/лекарства/самочувствие сразу
(они однозначны). Названные БЖУ («овсянка 200 г б 12 ж 6 у 40») отделяются
`split_macros` и применяются к черновику после распознавания, минуя оценку
модели и подсказку словаря. Остаток сначала идёт в личный словарь
(`dictionary.offer_suggestions` — подсказки по первым буквам, запись одной
кнопкой, модель не зовётся) и только потом в `parse_meal_text`.

**Правка:** любая карточка → `✏️ Скорректировать` → текст или голосовое →
слияние с распознаванием (`spec/ingest.md` § Корректировки). Введённые в правке
БЖУ («овсянка 200 г б 12 ж 6 у 40») запоминаются за блюдом, о чём бот сообщает
отдельным сообщением «📌 Запомнил ваши БЖУ …» — так же, как и БЖУ, названные
сразу во вводе (`spec/dictionary.md` § Память БЖУ).

## Отчёты (`src/handlers/reports.py`)

`_compute_stats(session, user, window, key_type, days=30)` — общий путь для
`/stats`, графиков и вердикта по продукту: `load_meal_likes` + `load_points`
(с запасом 6 ч назад для базовых линий) → `build_excursions` → `aggregate`.
Окна берутся из профиля пользователя, минимум наблюдений — из настроек.
