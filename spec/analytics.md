# analytics — окна, статистика, метрики

Отдельными спеками описаны три модуля пакета: `src/analytics/plate.py` —
`spec/plate.md`, `src/analytics/labs.py` — `spec/labs.md`,
`src/analytics/sleep.py` — `spec/sleep.md`.

## Components (`src/analytics/tags.py`)

Два уровня ключей:
- `name_norm` — нормализованное название блюда (нижний регистр, ё→е, без
  пунктуации и слов порций) → статистика по блюдам;
- `tags` — **закрытый** словарь из 30 компонентов (`TAGS: slug -> русская метка`):
  `added_sugar, refined_flour, white_rice, potato, whole_grain, starch, fruit,
  dried_fruit, juice, sweet_drink, milk, dairy_fermented, cheese, protein,
  red_meat, processed_meat, fish, egg, legume, nuts, vegetable, fiber,
  fat_added, fried, alcohol, sweetener, ultra_processed, high_gi, low_gi`.

```
normalize_name(s)->str ; normalize_tags(tags, name="")->[str] ; infer_tags(name)->[str]
tag_label(slug)->str
```

Почему компоненты: «гречка с курицей» встретится 3 раза в месяц, «добавленный
сахар» — 40. Мощность выборки растёт кратно быстрее.

## Windows (`src/analytics/windows.py`)

```
GlucosePoint(at, value)          MealLike(id, eaten_at, tags[], items[], carbs_g?)
Excursion(meal_id, eaten_at, window, baseline, peak, peak_at, delta, iauc,
          n_points, contaminated) ; .usable = delta is not None and not contaminated
```

Значения по умолчанию: `WINDOW_1H=(45,90)`, `WINDOW_2H=(90,150)`,
`BASELINE_WINDOW=20`, `CONTAMINATION_GUARD_MIN=30`.

Алгоритм `compute_excursion`:
1. `baseline` = среднее точек в `[eaten_at-baseline_window; eaten_at+10мин]`;
   если пусто — ближайшая точка в ±30 мин; иначе `None` (экскурсия непригодна);
2. точки окна `[eaten_at+start; eaten_at+end]` (границы включительно);
3. `peak` = максимум в окне, `delta = peak - baseline`;
4. `iauc` — трапеции по `[eaten_at; end]`, **только площадь над базовой линией**
   (провал ниже не компенсирует предыдущий подъём);
5. `contaminated=True`, если другой приём пищи попал в `[eaten_at-30мин; end]`.

`build_excursions(meals, points, ...) -> {"1h": [...], "2h": [...]}`.

Почему окно, а не точка: CGM даёт точку каждые 5–15 мин, пальцевые измерения —
когда получится; «через час» на практике это 45–90 минут.

## Statistics (`src/analytics/stats.py`)

```
mean_ci(values) -> (mean, sd, ci_low, ci_high)     # t-интервал 95%, таблица df<30
mann_whitney_p(a, b) -> p|None                     # норм. аппроксимация + поправка на связки
                                                   # None при len<3 в любой группе
grade_confidence(n, ci_low, mean_delta, p_value) -> "low"|"medium"|"high"
aggregate(meals, excursions, key_type="tag"|"item", window, min_observations=3) -> [KeyStats]
```

`KeyStats`: `key, key_type, window, n, mean_delta, median_delta, max_delta, sd,
ci_low, ci_high, n_without, mean_without, contrast, p_value, confidence, examples`.
`actionable` = достоверность ≥ средней **и** средний подъём ≥ 1.5 ммоль/л.

Пороги: `MIN_OBSERVATIONS=3`, `MEDIUM_N=5`, `HIGH_N=8`, `MEANINGFUL_RISE=1.5`,
`P_SIGNIFICANT=0.05`.

| Уровень | Условие |
|---|---|
| low | n < 3, либо ничего из нижеперечисленного |
| medium | n ≥ 5 и (CI выше нуля **или** p < 0.05) |
| high | n ≥ 8 **и** CI выше нуля **и** p < 0.05 **и** mean ≥ 1.5 |

Группа сравнения обязательна: без неё «после риса +3» ничего не значит у
человека, у которого растёт вообще всё.

Из агрегата исключаются экскурсии без `usable`. Сортировка: по убыванию
среднего подъёма, затем по числу наблюдений.

### Кэш (`src/food_stats.py`)

```
stats_for(session, user, key_type="tag", window="1h", days=30) -> [KeyStats]
rebuild(session, user, days=30)                      # пересчёт без вопросов
KEY_TYPES=("tag","item") · WINDOWS=("1h","2h") · DEFAULT_PERIOD_DAYS=30 · CACHE_TTL=1 ч
```

`/stats` спрашивают по многу раз подряд: четыре кнопки окон, два типа ключа,
плюс вердикт по продукту. Пересчёт один и тот же, поэтому результат лежит в
`food_stats`, а штамп сборки — в `users.food_stats_at`.

- промах (`NULL`-штамп, штамп старше `CACHE_TTL`) → один разбор экскурсий
  заполняет **все четыре** пары `(key_type, window)`: дорого именно построение
  экскурсий, а не агрегат;
- пустой ответ кэшируется наравне с непустым — иначе человек, у которого ни
  один ключ не набрал `min_observations`, пересчитывал бы всё при каждом
  касании;
- сброс (`repo.invalidate_food_stats`) — новая еда (`save_meal`) и новый замер
  (`save_glucose`, только если что-то реально вставилось); удаление данных
  (`delete_user_data`) уносит и строки, и штамп;
- `CACHE_TTL` нужен из-за скользящего окна в 30 дней: данные не менялись, а
  край периода уехал;
- `days` не по умолчанию считается мимо кэша: кэш хранит ровно один период.

## Недельный дайджест (`src/analytics/digest.py`, `src/digest.py`)

```
build_digest(now, meals, excursions, points, buckets?, weights?, key_type="tag", days=7)
  -> WeeklyDigest
WeeklyDigest: since until meals_now meals_before days_with_meals
              glucose_now? glucose_before? risen[KeyChange] calmed[KeyChange]
              steps_now? steps_before? weight_now? weight_before?
              ; mean_shift · tir_shift · steps_shift · weight_shift · has_content
KeyChange(key key_type now? before? n_now n_before kind) ; delta
  kind ∈ new|gone|up|down|same
WEEK_DAYS=7 · MIN_WEEK_POINTS=20 · MEANINGFUL_SHIFT=0.8 ммоль/л
MEANINGFUL_MEAN_SHIFT=0.5 · MEANINGFUL_TIR_SHIFT=5 п.п.
src/digest.py: build(session, user, days=7) -> WeeklyDigest   # два окна из одной выборки
```

`/stats` отвечает «что верно за тридцать дней» — в понедельник и в пятницу
одинаково. Дайджест отвечает на другой вопрос: **изменилось ли что-нибудь**.
Две равные недели рядом, ничего больше.

Из чего складывается:

| Строка | Условие |
|---|---|
| средний сахар | обе недели ≥ `MIN_WEEK_POINTS` замеров, сдвиг ≥ `MEANINGFUL_MEAN_SHIFT` |
| время в диапазоне | те же замеры, сдвиг ≥ `MEANINGFUL_TIR_SHIFT` |
| компонент вырос/успокоился | `aggregate` по каждой неделе отдельно, сдвиг среднего подъёма ≥ `MEANINGFUL_SHIFT` |
| новый компонент | набрался только на этой неделе **и** сам по себе ≥ `MEANINGFUL_SHIFT` |
| пропал компонент | набирался только на прошлой неделе и был ≥ `MEANINGFUL_SHIFT` |
| еда, шаги, вес | ≥ 5000 шагов разницы, ≥ 0.3 кг |

Порог обязателен: без него дайджест каждую неделю сообщал бы о дрожании
выборки. Пустой дайджест (`has_content == False`) не отправляется.

Отдача: `/week` и кнопка «🗓 Неделя» под `/stats`; рассылка —
`scheduler.run_weekly_digests` по понедельникам с `DIGEST_HOUR`, слот
занимается через `notification_sends` (`code="weekly_digest"`), выключается
`/set week off` (`users.weekly_digest_enabled`).

Формулировки — `reporting.format_weekly_digest`: «средний подъём выше», а не
«стал поднимать»; хвост «это сравнение двух недель, а не объяснение»
(`spec/clinical.md`).

Паритет tg/web: `web/app/js/digest.js` считает ту же неделю без сравнения по
компонентам — `spec/web.md` § Неделя в сравнении.

## CGM metrics (`src/analytics/cgm_metrics.py`)

Пороги: `TIR 3.9–10.0`, `TBR L2 < 3.0`, `TAR L2 > 13.9`, разрыв > 30 мин
считается простоем сенсора.

```
percent_in(points, low, high)   # доля времени, midpoint-взвешивание
cv, gmi, ea1c, j_index, lbgi_hbgi, mage, summarize -> CGMSummary
```

Формулы: `GMI = 3.31 + 0.02392·mean(mg/dL)`; `eA1c = (46.7 + mean(mg/dL))/28.7`;
`J = 0.001·(mean+sd)²` (mg/dL); риск-трансформация Ковачева
`f = 1.509·(ln(mg/dL)^1.084 − 5.381)`, `risk = 10·f²`, `LBGI` по `f<0`,
`HBGI` по `f>0`; MAGE — средняя амплитуда экстремумов, превышающих 1 SD.

Взвешивание по времени обязательно: источники смешаны (CGM + ручные замеры +
скриншоты), и подсчёт «по числу точек» дал бы перекос в сторону всплесков
ручных измерений.

## Symptoms (`src/analytics/symptoms.py`)

```
CheckinLike(at, score, symptoms[]) -> build_context(checkins, points, meals?) -> [CheckinContext]
CheckinContext(at, score, symptoms, glucose?, minutes_since_meal?, meal_tags[])
aggregate_symptoms(contexts, min_observations=3) -> [SymptomStats]
score_series / symptom_series
```

`MATCH_WINDOW_MIN=30` (насколько далеко может быть ближайшее измерение),
`POSTPRANDIAL_MIN=150` (отметка считается постпрандиальной).
`SymptomStats`: n, среднее/медиана сахара при симптоме, среднее без него,
контраст, CI, p, доля постпрандиальных случаев, число случаев с сахаром < 3.9.

Вывод — только контраст. Причины симптома бот не называет.

## Activity (`src/analytics/activity.py`)

```
ActivityBucket(start_at, end_at, steps)
steps_after(buckets, meal_at, minutes=60) -> int     # пропорционально перекрытию
contrast_by_activity(meals, excursions, buckets, threshold=1000) -> ActivityContrast
daily_steps(buckets) -> {date: steps}
```

`ActivityContrast.meaningful` требует ≥ 3 наблюдений в каждой группе.

## Sleep

`src/analytics/sleep.py` — ночи из Health Connect или из появлений в чате,
ровность режима и контрасты «сутки после короткой/сдвинутой ночи» по калориям,
углеводам, среднему сахару и среднему подъёму. Тот же порог `MIN_OBSERVATIONS`
в каждой группе. Подробности — `spec/sleep.md`.

## Body & workouts

`src/analytics/body.py` и `src/analytics/workout.py` — чистые слои поверх
профиля, замеров веса и записей о тренировках: ИМТ, BMR/TDEE, безопасный темп
изменения веса, дневной коридор калорий, MET-оценка энергозатрат. Подробности —
`spec/body.md` и `spec/workout.md`. Оба модуля не знают ни про ORM, ни про
aiogram и оперируют числами и датаклассами.

## Ограничения

Приём лекарств — известный конфаундер, но в расчёты не входит: учесть его как
ковариату означало бы делать выводы о действии препарата (запрещено
конституцией, принцип I). Лекарства фиксируются и попадают в экспорт.


## Лекарства как контекст

`src/analytics/meds.py` — чистый слой поверх `MedicationLike` / `CheckinLike` /
`Excursion`, подробности в `spec/meds.md`. Два вывода, оба описательные:

- `coverage(excursions, meds, hours=8)` — в скольких пригодных экскурсиях уже
  «был на борту» приём; при доле ≥ 30 % `reporting.format_med_coverage`
  показывает это как конфаундер к цифрам по еде;
- `symptom_links(meds, checkins, lookup, hours=8, min_hits=2)` — симптом,
  отмеченный в окне после приёма и числящийся побочкой этого препарата в
  открытом справочнике.

Порог `min_hits=2` и там, и там: единичное совпадение не показывается вовсе.
