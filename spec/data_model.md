# data_model — таблицы, репозиторий, экспорт

## Соглашения

- Все timestamp: `DateTime(timezone=True)`, хранение в UTC.
- Глюкоза: `value_mmol` (каноническая ммоль/л) + `unit_input` (что ввёл пользователь).
- Нутриенты в граммах, энергия в ккал.
- `_aware(dt)` в `repo` — граница: SQLite отдаёт naive, аналитика требует aware.

## Таблицы

```
users              id tg_id* username first_name locale tz glucose_unit sensor
                   window_1h_start/end window_2h_start/end baseline_window
                   consent_at onboarded created_at
media_files        id user_id kind(meal|glucose|label|lab|voice) tg_file_id tg_unique_id
                   mime size_bytes sha256 local_path
products           id user_id? barcode brand name name_norm
                   kcal_100 protein_100 fat_100 carbs_100 sugars_100 fiber_100
                   ingredients_text ingredients[] additives[] flags[] source confirmed
product_photos     id product_id media_id side(front|back)
meals              id user_id eaten_at source(photo|text|voice|label) title raw_text note
                   media_id kcal protein_g fat_g carbs_g fiber_g confidence confirmed corrected
meal_items         id meal_id product_id? name name_norm portion_g kcal protein_g fat_g
                   carbs_g fiber_g tags[]
glucose_readings   id user_id measured_at value_mmol unit_input
                   source(manual|text|screenshot|cgm_api) device trend raw_text media_id confirmed
weights            id user_id measured_at weight_kg note
medications        id user_id taken_at name dose_text note      -- журнал, не назначения
analysis_results   id user_id taken_at panel marker value value_text unit
                   ref_low ref_high flag(low|normal|high) media_id raw_text
symptoms           id user_id? slug label hits is_active last_used_at   -- uq(user_id,slug)
wellbeing_checkins id user_id at score(1..5) note source media_id
checkin_symptoms   id checkin_id symptom_id severity?
activity_samples   id user_id external_id kind(steps|workout|sleep|heart_rate)
                   start_at end_at steps distance_m kcal avg_hr source payload
                   -- uq(user_id, external_id)
food_stats         id user_id key_type(item|tag|product) key window n
                   mean_delta median_delta max_delta ci_low ci_high confidence updated_at
corrections        id user_id entity_type entity_id field old_value new_value created_at
```

Индексы: `meals(user_id, eaten_at)`, `glucose_readings(user_id, measured_at)`,
`wellbeing_checkins(user_id, at)`, `activity_samples(user_id, start_at)`,
`meal_items(name_norm)`, `products(user_id, name_norm)`.

## Репозиторий (`src/db/repo.py`)

```
get_or_create_user(session, tg_id, username?, first_name?) -> User   # засевает глоссарий
save_media(session, user, kind, tg_file_id?, ...) -> MediaFile
save_meal(session, user, draft, eaten_at, media_id?, confirmed=True, product_id?) -> Meal
load_meals(session, user, since?) -> list[Meal]
load_meal_likes(session, user, since?) -> list[MealLike]        # для analytics
save_glucose(session, user, drafts, source, media_id?) -> list[GlucoseReading]  # дедуп
load_points(session, user, since?) -> list[GlucosePoint]
find_product / save_product(session, user, draft, media_ids=[(id, side)]) -> Product  # upsert
seed_symptoms / list_symptoms(limit=12) / upsert_symptom(label)
save_checkin(session, user, at, score, symptom_labels, note?, source) -> WellbeingCheckin
load_checkins -> [(WellbeingCheckin, labels)] ; load_checkin_likes -> [CheckinLike]
load_activity / load_activity_buckets / upsert_activity(samples) -> int
save_weight / save_medication / save_labs / save_correction
counts(session, user) -> dict[str,int]
delete_user_data(session, user, drop_user=False)
```

`SEED_SYMPTOMS` — 12 стартовых симптомов (сонливость, потливость, туман в
голове, сильный голод, жажда, сердцебиение, дрожь, головная боль,
раздражительность, приливы, слабость, тошнота).

`delete_user_data` делает явные `DELETE` по таблицам: SQLite не выполняет
каскады без `PRAGMA foreign_keys=ON`, а строка пользователя должна пережить
`/delete`.

`save_meal` завершается `session.refresh(meal, ["items"])` — вызывающий читает
итоги сразу, а ленивая загрузка вне async-контекста даёт `MissingGreenlet`.

## Экспорт (`src/export.py`)

`build_export(session, user) -> bytes` — ZIP, по одному CSV на таблицу плюс
`README.txt`. Разделитель `;`, UTF-8 **с BOM** (иначе Excel ломает кириллицу),
даты ISO-8601, списки склеены через `|`.
