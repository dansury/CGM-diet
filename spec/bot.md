# bot — команды, клавиатуры, FSM, обработчики

## Runtime (`src/bot.py`)

`BOT_MODE=polling` (по умолчанию) → `dispatcher.start_polling`.
`BOT_MODE=webhook` → uvicorn с `src.web.app:create_app`.
`build_bot(settings)` — `ParseMode.HTML` по умолчанию.
`build_dispatcher()` — сначала `user_tracking.register(dp)` (outer-middleware на
`message`/`callback_query` + роутер `my_chat_member`), затем `build_router()`.
`COMMANDS` — 20 команд (`/workouts` в меню не выносится), регистрируются в меню Telegram при старте.
`prepare_runtime(bot, settings)` до первого апдейта (и в polling, и в webhook):
`wire_error_reporter` → `load_active_models` → каталог свободных моделей →
`scheduler.start(bot)` (напоминание о взвешивании — `spec/body.md`; недельная
подсказка о возможностях — `spec/features.md`; «бот вас не видит» для
наблюдения за сном — `spec/sleep.md`).
Каждый шаг деградирует молча — ни один не мешает боту стартовать.

## Команды

| Команда | Что делает | Файл |
|---|---|---|
| `/start` | регистрация, согласие, анкета о теле при первом запуске | `handlers/common.py`, `handlers/onboarding.py` |
| `/my` | личный словарь всех сущностей: запись одной кнопкой | `handlers/dictionary.py` |
| `/meds` | журнал лекарств + справка по побочкам | `handlers/meds.py` |
| `/help` | подробная справка | `handlers/common.py` |
| `/menu` | вернуть клавиатуру | `handlers/common.py` |
| `/cancel` | отменить текущий ввод (то же, что `❌`) | `handlers/common.py` |
| `/settings`, `/set` | пояс, единицы, окна, базовая линия, `weighin`, `plate`, `meals`, `sleep` | `handlers/common.py` |
| `/plate` | Гарвардская тарелка: что настроено и как менять | `handlers/plate.py` |
| `/labs` | анализы: маркеры вне референса и продукты-источники | `handlers/labs.py` |
| `/hidden` | скрытые возможности и возврат их в меню | `handlers/features.py` |
| `/eat`, `/sugar`, `/check` | явные режимы ввода | `handlers/intake.py` |
| `/wellbeing` | опрос самочувствия | `handlers/wellbeing.py` |
| `/body` | профиль тела, замеры, цель, дневной коридор | `handlers/body.py` |
| `/weight` | ввести вес и биоимпеданс | `handlers/body.py` |
| `/workout`, `/workouts` | записать тренировку или ходьбу; журнал за неделю | `handlers/workout.py` |
| `/today` | записи за сегодня | `handlers/reports.py` |
| `/stats` | статистика + метрики + симптомы + рекомендации | `handlers/reports.py` |
| `/graph` | таймлайн, самочувствие, рейтинг | `handlers/reports.py` |
| `/export` | ZIP с CSV | `handlers/reports.py` |
| `/delete` | удаление с подтверждением | `handlers/reports.py` |
| `/health` | пошаговая инструкция Samsung Health, ключи, ссылка на мост | `handlers/reports.py` |
| `/sleep` | сон: длительность, режим, связи; переключатель наблюдения | `handlers/sleep.py` |
| `/model`, `/models`, `/errors`, `/whereami` | только владелец, только в личке | `handlers/admin.py` |
| `/users`, `/bot_settings` | панель владельца, только в личке | `handlers/admin_panel.py` |

## Клавиатуры (`src/keyboards.py`)

Reply-меню (`MENU_ROWS`): `🍽 Записать еду`, `🩸 Записать сахар`,
`🛒 Проверить продукт`, `🙂 Самочувствие`, `🏃 Тренировка`, `⚖️ Вес и цель`,
`📊 Статистика`, `📈 График`, `⭐️ Мой словарь`, `💊 Лекарства`.
`main_menu(hidden?)` убирает кнопки возможностей, от которых пользователь
отказался (`spec/features.md`); клавиатуру собирает `features.menu_of(chat_id)`.

На каждой карточке распознавания — расшифровка текстом и кнопки
`✅ Подтвердить`, `✏️ Скорректировать`, `✏️ БЖУ`, `❌ Отменить` (правку принимаем текстом
**и голосом**). `✏️ БЖУ` — отдельный вход только для чисел: на карточке еды
(`meal:macros`) числа на съеденную порцию, на карточке продукта
(`prod:macros`) — на 100 г с этикетки. Введённые числа запоминаются за
блюдом (`spec/dictionary.md` § Память БЖУ).

### Отмена

`❌ Отменить` — одна кнопка на все вводы: карточки еды, сахара, анализов,
лекарства, продукта, выбор типа фото, опрос самочувствия, подсказки словаря и
каждое приглашение «напишите текстом» (`keyboards.cancel_button`,
`keyboards.cancel_only`). Callback один — `x:cancel`, обработчик один —
`common.on_cancel`: `state.clear()` и «❌ Отменено. Ничего не записал.»
Черновик живёт в FSM, поэтому отмена ничего не пишет в дневник и ничего не
спрашивает. То же самое текстом — `/cancel`.

`/health` — карточка Samsung Health с кнопками `📲 Как подключить`,
`🔑 Мои ключи`, `📦 Приложение-мост` (`hs:how|keys|app|menu`), инструкция
листается прямо в чате (`spec/health_sync.md` § Инструкция).

`/sleep` — карточка сна с кнопками `❓ Как это работает`,
`👀 Следить за сном` / `🚫 Выключить наблюдение`, `⌚️ Samsung Health`
(`sl:how|on|off|health|menu`, `spec/sleep.md`).

Callback-грамматика `<domain>:<action>[:<arg>]` (лимит Telegram — 64 байта):

```
meal:ok|edit|macros|time|drop   glu:ok|edit|unit|drop
prod:eat|save|macros|drop       lab:ok|drop
kind:food|glucose_screen|food_label|lab_report|medication|body_scale|workout|drop
photo:reroute
med:ok|edit|time|drop         prod:edit   lab:edit
dict:use:<id>|rm:<id>|pin:<id>|new|page:<kind>:<n>|mode:<kind>:<use|del>|close
x:cancel                       # общий крестик на всех клавиатурах
mdl:lvl:<global|slot|free> | mdl:slot:<slot> | mdl:set:<target>:<idx> | mdl:close
wb:score:<1..5> | wb:sym:<id> | wb:other | wb:voice | wb:done
stats:w:<1h|2h> | stats:k:<tag|item> | stats:chart
del:yes|no                     hs:how|keys|app|menu
feat:ok:<key>|no:<key>|show:<key>|close
bd:menu|profile|weight|goal|chart|close | bd:field:<name> | bd:sex:<m|f>
bd:act:<level> | bd:rate:<кг/нед ×100> | bd:save|bd:drop | bd:preg:<y|n>
onb:skip | onb:sex:<m|f> | onb:preg:<y|n>   # анкета при первом запуске
gl:pick:<key> | gl:other | gl:done          # цели — `spec/onboarding.md`
wo:ok|edit|time|hr|drop | wo:dur:<мин|other> | wo:int:<low|moderate|high>
wo:sweat:<yes|light|no>
botadm:<users|data|models|errors|health>   # панель владельца
```

Черновики **не** передаются в callback-data — они лежат в FSM. Устаревшая
кнопка не может воскресить чужие данные.

## FSM (`src/handlers/states.py`)

```
MealFlow.confirming|editing|editing_macros|retiming
GlucoseFlow.confirming|editing
ProductFlow.confirming|editing|editing_macros
LabFlow.confirming|editing
MedicationFlow.confirming|editing|retiming
WellbeingFlow.scoring|picking|free_text
BodyFlow.awaiting              # какое поле ждём — в ключе `body_field`
BodyFlow.confirming            # карточка замера с фото весов
OnboardingFlow.asking          # анкета при первом запуске — `spec/onboarding.md`
GoalsFlow.note                 # «Свой вариант» цели — свободный текст
WorkoutFlow.confirming|asking|editing|retiming|awaiting_hr
SettingsFlow.editing
```

Ключи данных: `draft`, `draft_files`, `eaten_at`, `taken_at`, `draft_mode`,
`pending_mode`, `pending_photos`, `wb_selected`, `wb_score`, `wb_extra`,
`wb_note`, `dict_pending`, `mdl_candidates`, `mdl_target`, `body_field`,
`wo_pending`, `started_at`, `onb_step`, `onb_queue`.

## Порядок роутеров (`src/handlers/__init__.py`)

`user_tracking → admin → admin_panel → common → onboarding → reports → sleep →
features → plate → labs → wellbeing → body → workout → dictionary → meds →
confirm → intake → errors`. `user_tracking` первым — его `my_chat_member`
должен видеть блокировку раньше всех. `admin` и `admin_panel` полностью
отфильтрованы (владелец + личка):
чужому апдейту они просто не соответствуют и тот идёт дальше. `onboarding` сразу после `common`,
чтобы анкета первого запуска перехватывала ответы раньше catch-all'ов
(`spec/onboarding.md`). `intake` предпоследний: он ловит любой текст и любое
фото. `errors` — наблюдатель `router.errors`, обработчиков сообщений не
содержит.

## Реестр пользователей (`src/handlers/user_tracking.py`)

Владелец узнаёт о каждом новом пользователе и о каждой блокировке бота.

```
UserTrackingMiddleware  # outer на message+callback_query, только личка
  -> repo.touch_user(session, tg_id, username?, first_name?) -> (User, is_new)
     # обновляет username/first_name/last_seen_at, снимает blocked_at
  -> is_new: DM владельцам «🆕 Новый пользователь» + ссылка на /users
  throttle: SEEN_TTL_SEC=3600 на пользователя, сбрасывается при смене профиля
  fail-soft: любая ошибка логируется, апдейт идёт дальше

router.my_chat_member (chat.type == private)
  status=kicked -> repo.set_user_blocked(tg_id, True)  -> DM «🚫 ...заблокировал бота»
  status=member -> repo.set_user_blocked(tg_id, False) -> DM «✅ ...разблокировал бота»

notify_owners(bot, text, skip?)   # settings.owner_tg_ids, skip = сам инициатор
user_label(tg_id, username?, first_name?) -> str   # «<b>Имя</b> · @user · <code>id</code>»
```

Сам реестр показывает `/users` из панели владельца
(`admin_panel.render_users`, § Панель владельца): счётчики, метка 🚫 у
заблокировавших и `last_seen_at` в строке пользователя.

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
(FSM `MealFlow.confirming`) → `meal:ok` → `repo.save_meal`. Подтверждение —
`reporting.format_meal_saved`: время, название и строка БЖУ в `<code>`
(моноширинный блок в Telegram копируется одним касанием). В том же сообщении —
полоса дневного коридора (`spec/body.md`) и оценка тарелки (`spec/plate.md`);
обе части не обязательны и не могут отменить запись. Под сообщением —
кнопки «⭐️ в словарь» на каждую позицию, которой ещё нет в словаре
(`spec/dictionary.md` § Запись в словарь одной кнопкой).
`meal:edit` → строка `гречка 250, курица 100` → `_parse_edit` пересчитывает
нутриенты пропорционально порции, сохраняет теги, пишет `corrections`.

**Глюкоза:** скриншот → `recognize_glucose_screenshot` → карточка →
`glu:ok` (сохранить), `glu:unit` (пересчитать шкалу), `glu:edit` (`8.2 в 9:15`).

**Продукт:** фото(а) → `recognize_label` → в режиме `check` текст строится
`reports.product_verdict_text` по статистике пользователя; `prod:eat`
превращает продукт в `MealDraft` с порцией 100 г (редактируемой).
Отдельного шага «вторая сторона упаковки» нет: обе стороны присылаются одним
альбомом и уходят в модель одним вызовом (`AlbumBuffer`).

**Анализы:** фото/PDF/текст → `recognize_labs` → карточка с пометками
`🔺/🔻/✅` → `lab:ok` → сохранение + отдельным сообщением продукты-источники по
маркерам вне референса (`spec/labs.md`).

**Лекарство:** фото упаковки → `recognize_medication` → карточка `med:*` →
`repo.save_medication_draft` (журнал + личный словарь). `spec/meds.md`.

**Тело:** «вес 82, жир 24%» текстом/голосом пишется сразу; фото весов →
`recognize_body_photo` → карточка `bd:save`. Цель и коридор калорий —
`spec/body.md`.

**Тренировка:** текст/голос/фото (в том числе рукописный дневник) →
`recognize.parse_workout_text` / `recognize_workout_photo` → недостающее
спрашивается кнопками (длительность → интенсивность → пот) → карточка с
оценкой ккал → `wo:ok`. `spec/workout.md`.

**Свободный текст:** `parse_text` пишет сахар/вес/лекарства/самочувствие сразу
(они однозначны). Названные БЖУ («овсянка 200 г б 12 ж 6 у 40») отделяются
`split_macros` и применяются к черновику после распознавания, минуя оценку
модели и подсказку словаря. Остаток сначала идёт в личный словарь
(`dictionary.offer_suggestions` — подсказки по первым буквам, запись одной
кнопкой, модель не зовётся) и только потом в `parse_meal_text`.

**Анкета первого запуска:** `/start` (первый раз) → возраст → рост → вес →
пол → (женский → беременность) → особые состояния → цель, каждый шаг можно
пропустить; фото вместо ответа завершает анкету и уходит в обычную
классификацию фото (`spec/onboarding.md`).

**Правка:** любая карточка → `✏️ Скорректировать` → текст, голосовое или (для
еды) фото → слияние с распознаванием (`spec/ingest.md` § Корректировки).
Введённые в правке
БЖУ («овсянка 200 г б 12 ж 6 у 40») запоминаются за блюдом, о чём бот сообщает
отдельным сообщением «📌 Запомнил ваши БЖУ …» — так же, как и БЖУ, названные
сразу во вводе (`spec/dictionary.md` § Память БЖУ).

## Примеры в подсказках

Примеры в приглашениях не фиксированы: пул ротируется на каждое приглашение,
и **не чаще чем через раз** (`tick % 2 == 0`) последним пунктом подмешивается
пример из личного словаря пользователя. Счётчики — в процессе
(`reporting._rotation`, ключ `(slot, chat_id)`), в БД ничего не пишется;
после рестарта ротация начинается заново.

Пулы и сборка — `src/reporting.py` (пользовательские тексты только там):

```
correction_examples(user_key, personal) -> list[str]   # правка карточки, личный — с весом
food_examples(user_key, personal) -> list[str]         # описание еды словами
glucose_examples(user_key) -> list[str]                # сахар: имён нет, личных примеров нет
dish_example(user_key, personal) -> str                # название блюда для примера с БЖУ
items_example(user_key, personal) -> str               # «продукт 250, продукт 100, …»
quoted(examples) -> str                                # «a», «b», «c»
correction_hint | correction_retry | describe_food_hint | describe_food_retry
glucose_prompt | glucose_hint | macros_prompt | macros_retry | meal_edit_prompt
reset_examples()                                       # только для тестов
```

Личные примеры — названия из личного словаря (`repo.example_labels`, виды
`item` и `meal`, порядок ротации словаря). Названия подставляются **в
именительном падеже и только с весом или как есть** («сырники 200 г»):
склонять слова пользователя бот не берётся. Личных примеров нет (пустой
словарь, сбой запроса) — подсказка остаётся с общими; `items_example`
требует минимум двух названий, иначе формат «через запятую» не виден.

Где это видно: подсказка под карточкой еды (`views.show_meal_draft`),
`meal:edit` и «не понял правку» (`confirm`), `meal:macros` и «не понял числа»
(`confirm`), «опишите еду» (`intake`, `dictionary`), приглашение и ошибка
распознавания сахара (`intake`). `/start` и `/help` — справочные карточки,
они не ротируются: словаря у нового пользователя ещё нет.

## Панель владельца (`src/handlers/admin_panel.py`)

Только `OWNER_TG_IDS`, только личка (те же фильтры, что у `handlers/admin.py`):
не-владельцу роутер просто не соответствует, и бот не выдаёт, что команды есть.

```
/users            -> render_users()                # реестр пользователей
/bot_settings     -> _MAIN_TEXT + owner_panel()    # inline-панель
botadm:users|data|models|errors|health             # разделы панели
```

| Раздел | Что показывает |
|---|---|
| 👥 Пользователи | до 50 последних: `tg_id`, имя, `@username`, пояс, дата регистрации, пройдена ли анкета; под каждым — счётчики `repo.counts` (еда, сахар, вес, тренировки, лекарства, самочувствие) и последняя запись еды |
| 📊 Данные | итоги по базе: пользователи, приёмы пищи, измерения сахара, тренировки, лекарства, опросы; те же числа за 7 дней |
| 🧠 Нейросети | модели по слотам (`admin._explain`), уровень выбора, ключи провайдеров; смена — `/model` |
| 🩺 Ошибки | последние отчёты `errors_report.recent_reports` |
| ❤️ Health | БД, LLM (или `LLM_MOCK`), SpeechKit, health-sync |

Раздел «Монетизация» и всё, что связано с каналами и подписками, из
GrowthProducer не переносилось: в этом боте таких сущностей нет.

## Отчёты (`src/handlers/reports.py`)

`_compute_stats(session, user, window, key_type, days=30)` — общий путь для
`/stats`, графиков и вердикта по продукту: `load_meal_likes` + `load_points`
(с запасом 6 ч назад для базовых линий) → `build_excursions` → `aggregate`.
Окна берутся из профиля пользователя, минимум наблюдений — из настроек.
