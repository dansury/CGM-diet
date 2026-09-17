# plate — Гарвардская тарелка

Оценка приёма пищи по пропорциям, а не по «правильности». Включена по
умолчанию, выключается `/set plate off`.

`[TG-ONLY: в веб-приложении оценки тарелки нет вообще — ни разбора после
записи, ни настроек. Перенос требует всего модуля целиком (категории, сессии,
режим питания), а не одной карточки; задача — T086.]`

## Правило

`½` — овощи и фрукты (овощей больше), `¼` — цельные злаки, `¼` — белок.
Картофель, белый рис и белая мука — **не** овощи и **не** цельные злаки:
отдельная категория `refined`, идёт в массу и не идёт ни в один ориентир.

`TARGET_SHARES = {veg 0.375, fruit 0.125, grain 0.25, protein 0.25}`
`CATEGORY_LABELS = {veg, fruit, grain, protein, refined, extra, drink, oil}`

### Рядом с тарелкой, а не её доля

В оригинальной тарелке масло и вода нарисованы **сбоку**: доли они не
занимают. Так же и здесь — `ASIDE_CATEGORIES = (drink, oil)`:

- `drink` — `water` (вода, чай, кофе), `juice`, `sweet_drink`, `milk`,
  `alcohol`. Масса считается и показывается («напитки 300 мл»), но в
  `mass_g`, `shares` и `score` не входит: иначе стакан воды «весил» бы как
  гарнир и занижал доли всего съеденного;
- `oil` — `fat_added`. Показывается граммами рядом с тарелкой.

Своей цели у них нет и быть не может: это была бы норма (`spec/clinical.md`).
`PLATE_CATEGORIES = CORE_CATEGORIES + extra` — то, что делит массу тарелки.
`MealSession.mass_g` тоже считает только тарелку, иначе ориентир одного приёма
рос бы от выпитого.

## Модель (`src/analytics/plate.py`)

```
PlateItem(name, portion_g?, tags[])          PlateMeal(id, eaten_at, items[])
PlateScore(mass_g, grams{}, shares{}, score 0..100, n_items, estimated_mass)
            ; half_share · drink_g · oil_g
PlateWeek(days, meals, balanced, score) ; balanced_share
MealSession(started_at, ended_at, meals[], items[]) ; .mass_g (тарелка) · .drink_g
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
estimate_meals_per_day(meals, window_min, tzinfo?, now?)->int|None   # None при <5 днях в окне
typical_meal_mass(meals, window_min)->float|None               # медиана, [250..1200] г
measure_rhythm(history, meals_per_day?, tzinfo?, now?)->Rhythm
advise(current, day_sessions, rhythm)->PlateAdvice
count_meals_today(history, day_start, window_min)->int   # приёмов пищи с day_start
week_summary(sessions)->PlateWeek|None       # None: настоящих приёмов не было
```

Константы: `DEFAULT_SESSION_MIN=60`, `DEFAULT_MEALS_PER_DAY=3`,
`MIN/MAX_MEALS_PER_DAY=1/7`, `MIN_DAYS_FOR_RHYTHM=5`, `RHYTHM_WINDOW_DAYS=5`,
`BURST_GAP_MIN=10`, `DEFAULT_MEAL_MASS_G=500`, `FALLBACK_PORTION_G=100`,
`MIN_GAP_G=30`, `CORE_CATEGORIES=(veg, fruit, grain, protein, refined)`,
`PLATE_CATEGORIES=CORE_CATEGORIES+extra`, `ASIDE_CATEGORIES=(drink, oil)`,
`MEAL_MIN_CORE_G=200`, `BALANCED_SCORE=80`.

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
   меньше `MEAL_MIN_CORE_G` г еды из `CORE_CATEGORIES` (орехи и сладости —
   `extra`, вода, кофе, молоко и сладкие напитки — `drink`; в счёт не идут ни
   те ни другие);
2. пропорции разошлись: `not is_balanced(score)`.

Перекус сам по себе не оценивается и молча ждёт: если существенная еда
попадает в то же окно `session_min`, `group_sessions` склеивает их — и перекус
входит в состав уже настоящей тарелки. Перекус, оставшийся сам по себе, не
считается приёмом пищи в `meals_done`, но его граммы идут в дневной итог.

## Сколько приёмов пищи в день

Приоритет: `users.meals_per_day` (задал пользователь — в анкете знакомства
или `/set meals`) → медиана **приёмов пищи** (`meal_sessions`) по дням с едой
внутри скользящего окна `RHYTHM_WINDOW_DAYS` (5 дней от `now`, а без него —
от последнего приёма в истории; нужно ≥5 таких дней внутри окна) → 3. Окно
не даёт полугодовой истории перевешивать то, как человек ест сейчас.

Сколько приёмов уже было сегодня — `count_meals_today(history, day_start,
window_min)`; это же число показывает дневной итог после каждой записи
(`spec/body.md` § Дневной коридор).

### Приёмы пищи для деления калоража

Дневной ориентир по калориям делится на число приёмов для полосы одного
приёма (`spec/body.md` § Полоса калорий одного приёма) — но не тем же числом,
что идёт в пробелы тарелки. Там, где для пропорций тарелки достаточно любой
статистики, для калорий действует более осторожное правило: по умолчанию 3,
пока пользователь не назвал своё число или статистика за `RHYTHM_WINDOW_DAYS`
не показывает **больше** 3 — тогда берётся она. Здесь `rhythm.meals_source`
из того же `measure_rhythm` решает: `user` — берём как есть, `stats` — только
если `rhythm.meals_per_day > DEFAULT_MEALS_PER_DAY`, иначе снова 3.

## Тарелка за период

`week_summary(sessions)` складывает **граммы** всех настоящих тарелок и делит
один раз — среднее из долей завысило бы вклад маленьких тарелок (перекус на
200 г весил бы как обед на килограмм). Плюс счётчик: сколько тарелок из
скольких набрали `BALANCED_SCORE`, за сколько дней с записями.

Показывается в `/stats` (`handlers/plate.plate_week_text`, 7 дней) —
до этого доли жили ровно одно сообщение после записи и к следующему дню
пропадали.

## Совет

Ориентир одного приёма = `meal_mass_g × TARGET_SHARES`; дневной =
`× meals_per_day`. `now` — чего не хватает в текущей тарелке, `day_gaps` — что
остаётся на `meals_left` приёмов. Пробелы меньше 30 г не показываем.

## Показ

Полоса состава тарелки (`— N из 100` + `progress_bar`) идёт на **каждом**
показе: в сообщении после записи их две — состав тарелки и дневной коридор
калорий (`spec/body.md`), это разные величины и одна другую не заменяет.

Первый показ (feature «plate» ещё не отмечена `used_at`) добавляет `PLATE_RULE`
и `PLATE_OFF_HINT`; последующие — без них.

## Округление рекомендаций

Пробелы в `➕` и `🗓` округляются до 50 г: белок и овощи — `ceil(g/50)*50`,
остальные категории — `floor(g/50)*50`. Пробелы <30 г по-прежнему не
показываются; после округления пробел 0 г тоже опускается.

## Поток (`src/handlers/plate.py`)

```
plate_advice_text(session, user, now)->str|None   # None: выключено, сегодня
                                                  # пусто, перекус или баланс
plate_week_text(session, user, now, days=7)->str|None   # свод для /stats
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
format_plate_week(week, days=7)->str             # доли за период + «собранных N из M»
format_plate_advice(advice, with_rule=False)->str
format_plate_settings(enabled, meals_per_day, measured, session_min)->str
```
Только пропорции и граммы. Ни «нормы», ни «правильно/неправильно»
(`spec/clinical.md`). Строка «Рядом с тарелкой: напитки N мл, масло N г — в
доли не входят» печатается, только когда есть что назвать, и ничего не требует.

## Настройки

```
/set plate on|off      # users.plate_enabled
/set meals 2..8|auto   # users.meals_per_day, auto => NULL => по статистике
/plate                 # inline-кнопки: Выключить/Включить, Количество приёмов
```
