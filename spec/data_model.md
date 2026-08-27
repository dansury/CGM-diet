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
                   plate_enabled meals_per_day? last_hint_at?
                   last_seen_at? blocked_at?          -- реестр владельца (`spec/bot.md`)
                   sleep_presence_enabled last_presence_reminder_at?
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
                   body_fat_pct muscle_mass_kg water_pct bone_mass_kg visceral_fat
                   bmr_kcal source(manual|text|voice|photo|scale)   -- биоимпеданс необязателен
body_profile       id user_id* height_cm birth_year sex(m|f) activity
                   pregnant(bool|null) conditions(text|null)
                   weight_prompt_days last_weight_prompt_at updated_at   -- uq(user_id)
body_goals         id user_id kind(lose|maintain|gain) target_weight_kg start_weight_kg
                   rate_kg_week target_kcal target_date started_at is_active
workouts           id user_id started_at ended_at? kind title duration_min
                   intensity(low|moderate|high) distance_m steps avg_hr rpe sweat
                   kcal kcal_source(estimated|user|device) met source media_id note
medications        id user_id taken_at name slug cid dose_text form
                   source(text|photo|dictionary) media_id note   -- журнал, не назначения
user_dictionary    id user_id kind(meal|item|product|medication|symptom) key_norm label payload
                   hits pinned is_active last_used_at   -- uq(user_id,kind,key_norm)
user_nutrition     id user_id key_norm label kcal protein_g fat_g carbs_g fiber_g portion_g
                   hits last_used_at   -- БЖУ пользователя на 100 г; uq(user_id,key_norm)
settings_kv        key value(JSON) updated_at           -- выбор модели владельцем
analysis_results   id user_id taken_at panel marker value value_text unit
                   ref_low ref_high flag(low|normal|high) media_id raw_text
symptoms           id user_id? slug label hits is_active last_used_at   -- uq(user_id,slug)
wellbeing_checkins id user_id at score(1..5) note source media_id
checkin_symptoms   id checkin_id symptom_id severity?
activity_samples   id user_id external_id kind(steps|workout|sleep|heart_rate)
                   start_at end_at steps distance_m kcal avg_hr source payload
                   -- uq(user_id, external_id)
presence_pings     id user_id at source(telegram)   -- отметки появлений, `spec/sleep.md`
food_stats         id user_id key_type(item|tag|product) key window n
                   mean_delta median_delta max_delta ci_low ci_high confidence updated_at
corrections        id user_id entity_type entity_id field old_value new_value created_at
feature_flags      id user_id feature status(new|shown|accepted|declined) shown
                   last_shown_at? used_at?   -- uq(user_id,feature)
```

Индексы: `body_goals(user_id, is_active)`, `workouts(user_id, started_at)`,
`weights(user_id, measured_at)`, `medications(user_id, taken_at)`, `user_dictionary(user_id, kind, key_norm)`,
`user_nutrition(user_id, key_norm)`,
`meals(user_id, eaten_at)`, `glucose_readings(user_id, measured_at)`,
`wellbeing_checkins(user_id, at)`, `activity_samples(user_id, start_at)`,
`presence_pings(user_id, at)`,
`meal_items(name_norm)`, `products(user_id, name_norm)`.

## Репозиторий (`src/db/repo.py`)

```
get_or_create_user(session, tg_id, username?, first_name?) -> User   # засевает глоссарий
get_user(session, tg_id) -> User?
touch_user(session, tg_id, username?, first_name?) -> (User, is_new)  # last_seen_at, blocked_at=NULL
set_user_blocked(session, tg_id, blocked) -> User?
list_users(session, limit=50) -> [User]      # по created_at убыв.
count_users(session) -> (всего, заблокировавших)
user_activity(session) -> {user_id: (приёмов пищи, замеров сахара, последняя запись?)}
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
save_weight(session, user, measured_at, weight_kg, composition?, source, note?) -> Weight
load_weights(session, user, since?) -> [Weight]   # только вес и состав, по возрастанию
get_body_profile / upsert_body_profile(session, user, **fields) -> BodyProfile
get_active_goal / set_goal(session, user, kind, target_weight_kg, rate, target_kcal) -> BodyGoal
clear_goal(session, user)
save_workout(session, user, draft, started_at, media_id?) -> Workout
load_workouts(session, user, since?) -> [Workout]
day_energy(session, user, day_start, day_end) -> (consumed_kcal, carbs_g, burned_kcal)
users_due_for_weight(session, now, default_days=14) -> [(User, BodyProfile)]
mark_weight_prompt(session, profile, at)
save_presence / load_presence / last_presence_at        # сон по появлениям, `spec/sleep.md`
load_sleep_intervals(session, user, since?) -> [SleepInterval]
daily_intake(session, user, since?) -> [DayIntake]      # по локальным суткам
users_watching_presence / users_due_for_presence_reminder / mark_presence_reminder
save_labs / save_correction
load_lab_values(session, user, since?) -> [LabValue]     # для analytics/labs
load_plate_meals(session, user, since?) -> [PlateMeal]   # позиции с порциями и тегами
feature_states(session, user) -> dict[key, FeatureState]
hidden_features(session, user) -> {key}
mark_feature_shown(session, user, key, at?)   # пишет и users.last_hint_at
set_feature_status(session, user, key, status) / mark_feature_used(session, user, key)
users_due_for_hint(session, now?, period_days=7) -> [User]
save_medication(session, user, taken_at, name, dose_text?, form?, note?, source, media_id?)
  # slug/cid резолвятся здесь: любой путь записи даёт один ключ справочника
save_medication_draft(session, user, draft, taken_at, media_id?, source) -> Medication
load_medications / load_medication_likes -> [MedicationLike]

bump_dictionary(session, user, kind, label, payload?, hits=1) -> DictionaryEntry?
suggest_dictionary(session, user, prefix, kinds?, limit=6)   # префикс → подстрока
list_dictionary / get_dictionary_entry / touch_dictionary / hide_dictionary
remember_meal(session, user, draft)      # заголовок + состав, по одному «замечен»
MIN_HITS = {meal: 2, item: 2, medication: 1}

remember_nutrition(session, user, name, values:Remembered) -> NutritionMemory?  # на 100 г
load_nutrition_memory(session, user, names?) -> dict[key_norm, Remembered]
remember_meal_macros(session, user, draft) -> [name]     # позиции с macros_source=="user"

get_setting / set_setting / all_settings                     # settings_kv
counts(session, user) -> dict[str,int]   # + dictionary (для features)
delete_user_data(session, user, drop_user=False)
```

`SEED_SYMPTOMS` — 12 стартовых симптомов (сонливость, потливость, туман в
голове, сильный голод, жажда, сердцебиение, дрожь, головная боль,
раздражительность, приливы, слабость, тошнота).

`delete_user_data` чистит и `user_dictionary`, и `user_nutrition`; `settings_kv`
к пользователю не относится и переживает `/delete`. `feature_flags` — тоже
настройка, а не запись дневника: скрытое меню переживает `/delete`.

`delete_user_data` делает явные `DELETE` по таблицам: SQLite не выполняет
каскады без `PRAGMA foreign_keys=ON`, а строка пользователя должна пережить
`/delete`.

`save_meal` завершается `session.refresh(meal, ["items"])` — вызывающий читает
итоги сразу, а ленивая загрузка вне async-контекста даёт `MissingGreenlet`.

## Экспорт (`src/export.py`)

`build_export(session, user) -> bytes` — ZIP, по одному CSV на таблицу плюс
`README.txt`. Разделитель `;`, UTF-8 **с BOM** (иначе Excel ломает кириллицу),
даты ISO-8601, списки склеены через `|`.
