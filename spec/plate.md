# plate — Гарвардская тарелка

Оценка приёма пищи по пропорциям, а не по «правильности». Включена по
умолчанию, выключается `/set plate off`.

## Правило

`½` — овощи и фрукты (овощей больше), `¼` — цельные злаки, `¼` — белок.
Картофель, белый рис и белая мука — **не** овощи и **не** цельные злаки:
отдельная категория `refined`, идёт в массу и не идёт ни в один ориентир.

`TARGET_SHARES = {veg 0.375, fruit 0.125, grain 0.25, protein 0.25}`
`CATEGORY_LABELS = {veg, fruit, grain, protein, refined, extra}`

## Модель (`src/analytics/plate.py`)

```
PlateItem(name, portion_g?, tags[])          PlateMeal(id, eaten_at, items[])
PlateScore(mass_g, grams{}, shares{}, score 0..100, n_items, estimated_mass)
MealSession(started_at, ended_at, meals[], items[]) ; .mass_g
Gap(category, grams)
Rhythm(meals_per_day, meals_source(user|stats|default), session_min,
       session_source(stats|default), meal_mass_g, mass_source)
PlateAdvice(score, now[Gap], day_gaps[Gap], meals_done, meals_left, rhythm)
```

```
classify(item)->category                     # теги → категория, иначе infer_tags(name)
core_mass_g(items)->float                    # масса категорий CORE_CATEGORIES
is_meal(items)->bool                         # core_mass_g >= MEAL_MIN_CORE_G
is_balanced(score)->bool                     # score.score >= BALANCED_SCORE
score_items(items)->PlateScore               # score = 100·Σmin(share,target)/Σtarget
session_window_min(meals, default=60)->int   # медиана разрывов (10..150] мин, [30..120]
group_sessions(meals, window_min)->[MealSession]
meal_sessions(sessions)->[MealSession]       # только is_meal, без одиночных перекусов
estimate_meals_per_day(meals, window_min, tzinfo?)->int|None   # None при <5 днях
typical_meal_mass(meals, window_min)->float|None               # медиана, [250..1200] г
measure_rhythm(history, meals_per_day?, tzinfo?)->Rhythm
advise(current, day_sessions, rhythm)->PlateAdvice
count_meals_today(history, day_start, window_min)->int   # приёмов пищи с day_start
```

Константы: `DEFAULT_SESSION_MIN=60`, `DEFAULT_MEALS_PER_DAY=3`,
`MIN/MAX_MEALS_PER_DAY=2/8`, `MIN_DAYS_FOR_RHYTHM=5`, `BURST_GAP_MIN=10`,
`DEFAULT_MEAL_MASS_G=500`, `FALLBACK_PORTION_G=100`, `MIN_GAP_G=30`,
`CORE_CATEGORIES=(veg, fruit, grain, protein, refined)`, `MEAL_MIN_CORE_G=200`,
`BALANCED_SCORE=80`.

## Что считается приёмом пищи в статистике

Одна тарелка = одна `MealSession`, в которой есть настоящая еда (`is_meal`).
Перекус, оставшийся один, приёмом пищи не считается нигде: ни в `meals_done`,
ни в `estimate_meals_per_day`, ни в `typical_meal_mass` — иначе числитель
(«приём N») и знаменатель («из M») считались бы по разным правилам.

## Приём пищи как серия

Обед из нескольких блюд — это одна тарелка. Записи, идущие подряд с разрывом не
больше `session_min`, склеиваются в `MealSession`. `session_min` — час по
умолчанию и собственное среднее время еды, когда истории хватает.

`session_window_min` считает медиану **только** по разрывам из
`(BURST_GAP_MIN .. SESSION_SAMPLE_LIMIT_MIN]`. Разрывы короче `BURST_GAP_MIN`
(10 мин) — это одно и то же сидение, снятое несколькими фото: они ничего не
говорят о длительности приёма пищи, а в медиане утягивали окно к нижней границе
30 мин и разрывали настоящий обед на несколько «приёмов». Меньше трёх годных
разрывов — окно остаётся часом.

## Когда показываем

Разбор тарелки выходит после записи, только если выполнено всё:

1. текущая сессия — блюдо, а не перекус: `is_meal(items)`, т.е. в ней не
   меньше `MEAL_MIN_CORE_G` г еды из `CORE_CATEGORIES` (кофе, орехи, молоко,
   сладкие напитки — категория `extra`, в счёт не идут);
2. пропорции разошлись: `not is_balanced(score)`.

Перекус сам по себе не оценивается и молча ждёт: если существенная еда
попадает в то же окно `session_min`, `group_sessions` склеивает их — и перекус
входит в состав уже настоящей тарелки. Перекус, оставшийся сам по себе, не
считается приёмом пищи в `meals_done`, но его граммы идут в дневной итог.

## Сколько приёмов пищи в день

Приоритет: `users.meals_per_day` (задал пользователь) → медиана **приёмов пищи**
(`meal_sessions`) по дням с едой (нужно ≥5 таких дней) → 3.

Сколько приёмов уже было сегодня — `count_meals_today(history, day_start,
window_min)`; это же число показывает дневной итог после каждой записи
(`spec/body.md` § Дневной коридор).

## Совет

Ориентир одного приёма = `meal_mass_g × TARGET_SHARES`; дневной =
`× meals_per_day`. `now` — чего не хватает в текущей тарелке, `day_gaps` — что
остаётся на `meals_left` приёмов. Пробелы меньше 30 г не показываем.

## Первый vs. последующие показы

Первый показ (feature «plate» ещё не отмечена `used_at`): полный блок
с полосой прогресса, `PLATE_RULE` и `PLATE_OFF_HINT`. Последующие: заголовок
`🥗 Тарелка /plate` без полосы и без правила/подсказки.

## Округление рекомендаций

Пробелы в `➕` и `🗓` округляются до 50 г: белок и овощи — `ceil(g/50)*50`,
остальные категории — `floor(g/50)*50`. Пробелы <30 г по-прежнему не
показываются; после округления пробел 0 г тоже опускается.

## Поток (`src/handlers/plate.py`)

```
plate_advice_text(session, user, now)->str|None   # None: выключено, сегодня
                                                  # пусто, перекус или баланс
/plate -> format_plate_settings(...) + plate_settings(enabled) keyboard
plt:on|off  -> toggle plate_enabled, refresh card
plt:meals   -> plate_meals_picker sub-menu
plt:mauto   -> meals_per_day=NULL, refresh card
plt:medit   -> hint /set meals N
```
Вызов — из `confirm.meal_ok` после записи, в одном сообщении с полосой
дневного коридора; любая ошибка оценки логируется и не отменяет запись
(`spec/bot.md` § Потоки).

## Тексты (`src/reporting.py`)

```
PLATE_RULE ; PLATE_OFF_HINT
format_plate_score(score, with_score=True)->str   # with_score=False: «Тарелка /plate»
format_plate_advice(advice, with_rule=False)->str
format_plate_settings(enabled, meals_per_day, measured, session_min)->str
```
Только пропорции и граммы. Ни «нормы», ни «правильно/неправильно»
(`spec/clinical.md`).

## Настройки

```
/set plate on|off      # users.plate_enabled
/set meals 2..8|auto   # users.meals_per_day, auto => NULL => по статистике
/plate                 # inline-кнопки: Выключить/Включить, Количество приёмов
```
