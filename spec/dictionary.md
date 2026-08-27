# dictionary — личный словарь, подсказки по первым буквам, память БЖУ

Цель: то, что человек ест и принимает регулярно, вводится **одним нажатием**,
а не фотографией и подтверждением каждый раз.

## Что попадает в словарь

Все именованные сущности, которые человек может повторить, — один словарь.

| kind | Когда | Порог показа |
|---|---|---|
| `meal` | при `meal:ok` — заголовок приёма | со **второго** раза (`hits >= 2`) |
| `item` | при `meal:ok` — каждое блюдо состава | со второго раза |
| `product` | при `save_product` (`prod:save`, `prod:eat`) — «бренд название» | **сразу** |
| `medication` | при `med:ok` и при разборе текста | **сразу** (`hits >= 1`) |
| `symptom` | при `upsert_symptom` — кнопка, текст, голос | **сразу** |

Порог зашит в
`MIN_HITS = {"meal": 2, "item": 2, "product": 1, "medication": 1, "symptom": 1}`
(`src/db/repo.py`), список видов — в `DICTIONARY_KINDS`. Требование владельца:
«блюда, которые встретились более одного раза»; упаковка, лекарство и симптом
названы сознательно, ждать второго раза не нужно.

Сахар, вес и анализы в словарь не попадают: это числа, а не имена — повторять
кнопкой нечего.

## Ротация

Все словари ротируются: **последний ввод — первым**. Порядок один на всё
(`repo._rotation(last_used_at, hits, id)`):

```
pinned desc, (last_used_at IS NULL) asc, last_used_at desc, hits desc, id asc
```

`last_used_at` обновляют `bump_dictionary` и `touch_dictionary`; ни разу не
использованные записи (сидовые симптомы) стоят позади в исходном порядке.
`NULLS LAST` выражен через `case` — SQLite понимает саму конструкцию только
с 3.30. Тем же порядком сортируется глоссарий симптомов (`list_symptoms`,
`spec/wellbeing.md`).

## Таблица

```
user_dictionary  id user_id kind(meal|item|product|medication|symptom) key_norm label
                 payload(JSON) hits pinned is_active last_used_at created_at
                 -- uq(user_id, kind, key_norm) ; ix(user_id, kind, key_norm)
```

`payload` — то, чем восстанавливают запись без модели: для еды и для отдельной
позиции `meal_to_dict(draft)` (у позиции — блюдо из неё одной), для упаковки
`product_to_dict(draft)`, для лекарства `{"dose_text","form","inn","slug","cid"}`,
для симптома `{"slug"}`.
Удаление — `is_active=False` (запись перестаёт предлагаться и не воскресает
сама; `hits` сохраняется, чтобы повторное подтверждение не считалось «первым»).

## Репозиторий (`src/db/repo.py`)

```
bump_dictionary(session, user, *, kind, label, payload=None, hits=1) -> DictEntry
suggest_dictionary(session, user, prefix, *, kinds=None, limit=6) -> list[DictEntry]
  # 1) префикс по key_norm  2) вхождение подстроки  3) ротация (§ Ротация);
  #    неактивные исключены
list_dictionary(session, user, *, kind=None, limit=30, include_hidden=False)
example_labels(session, user, *, kinds=("item","meal"), limit=5) -> list[str]
  # названия для примеров в подсказках (`spec/bot.md` § Примеры в подсказках);
  #    те же пороги и та же ротация, лекарства и симптомы не берутся
get_dictionary_entry(session, user, entry_id) -> DictEntry | None
touch_dictionary(session, entry) -> None            # hits+1, last_used_at=now
hide_dictionary(session, entry) -> None             # is_active=False
```

## UX (`src/handlers/dictionary.py`)

**Порядок** — подсказка всегда впереди модели:

1. пользователь пишет текст → детерминированный `parse_text` (сахар/вес/лекарства);
2. остаток ≥ 2 символов → `suggest_dictionary(prefix=остаток)`;
3. есть совпадения → карточка «Может быть, это?» с inline-клавиатурой
   (до 6 кнопок, по одной в ряд) + `➕ Разобрать как новое`;
4. нажатие `dict:use:<id>` — по виду записи:
   - `meal`/`item` — карточка подтверждения из `payload`, модель не зовётся;
   - `product` — карточка продукта (`views.show_product_draft`, режим `eaten`);
   - `medication` — запись приёма «сейчас», сразу;
   - `symptom` — опрос самочувствия с уже отмеченным симптомом
     (`WellbeingFlow.scoring`, `spec/wellbeing.md`);
5. совпадений нет или нажали «новое» → как раньше, `recognize.parse_meal_text`.

`/my` (и кнопка `⭐️ Мой словарь`) — разделы 🍽 Блюда / 🥄 Продукты /
🛒 Упаковки / 💊 Лекарства / 🙂 Самочувствие (`keyboards.KIND_TABS`, по две
вкладки в ряд), до 12 кнопок, сверху — последняя запись; `🗑 Удалить`
переключает клавиатуру в режим удаления.

Callback-грамматика:
```
dict:use:<id> | dict:new | dict:page:<kind>:<n>
dict:mode:<kind>:<use|del> | dict:rm:<id> | dict:close
x:cancel                       # общий крестик, `spec/bot.md` § Отмена
```

Кнопки словаря — единственный путь записи «в один тап»; всё остальное
по-прежнему проходит подтверждение (конституция, IV: подтверждение уже дано
в момент, когда запись попала в словарь).

## Запись в словарь одной кнопкой

Ждать второго раза необязательно. Под сообщением «✅ Записано» висит по кнопке
`⭐️ <название>` на **каждую** позицию приёма пищи (и на само блюдо, если
позиций больше одной), которой в словаре ещё не видно; максимум
`PIN_BUTTONS_LIMIT = 6` кнопок, названия обрезаются до 32 символов.

```
confirm.meal_ok -> repo.pinnable_entries(session, user, draft) -> [DictEntry]
dict:pin:<id>   -> repo.pin_dictionary(session, entry)   # hits=MIN_HITS, pinned=True
```

`pin_dictionary` доводит `hits` до порога вида и ставит `pinned=True`: запись
сразу видна в `/my` и стоит первой в ротации. Кнопка после нажатия убирается из
клавиатуры, остальные остаются. Скрытую руками запись (`is_active=False`)
кнопка не воскрешает — `pinnable_entries` такие не предлагает.

## Память БЖУ (`user_nutrition`)

Цель: БЖУ, которые человек однажды ввёл руками, больше никогда не оцениваются
моделью — «моя овсянка» весит столько, сколько сказал пользователь.

```
user_nutrition  id user_id key_norm label
                kcal protein_g fat_g carbs_g fiber_g portion_g   -- на 100 г
                source(user|label) hits last_used_at created_at
                -- uq(user_id, key_norm) ; ix(user_id, key_norm)
```

`source` — откуда числа: `user` — названы руками, `label` — прочитаны с
этикетки. Этикетка **не** перезаписывает строку с `source="user"`
(`remember_nutrition` возвращает `None`); слово пользователя перезаписывает
этикетку всегда.

Хранение на 100 г: порция меняется — числа пересчитываются, а не врут.
`portion_g` — порция, для которой их вводили (используется, когда у новой
позиции веса нет).

```
remember_nutrition(session, user, *, name, values:Remembered, source="user")
    -> NutritionMemory|None
load_nutrition_memory(session, user, names=None) -> dict[key_norm, Remembered]
remember_meal_macros(session, user, draft, *, source="user") -> list[str]
    # позиции с macros_source == source
remember_product_macros(session, user, draft:ProductDraft) -> str|None   # source="label"
product_item_name(draft:ProductDraft) -> str                             # «бренд название»
```

Поток:

0. кнопка `✏️ БЖУ` на карточке еды (`meal:macros`) и на карточке продукта
   (`prod:macros`) — отдельный вход только для чисел; разбор тот же
   (`correction._parse_macros`), карточка не пересобирается;
1. пользователь называет БЖУ — кнопкой `✏️ БЖУ`, в правке карточки или сразу во вводе
   («овсянка 200 г б 12 ж 6 у 40»; разбор — `spec/ingest.md` § Корректировки,
   § БЖУ во вводе), позиция получает `macros_source="user"`;
2. `views.remember_typed_macros` (зовут `handlers/confirm.meal_apply_edit` и
   `handlers/intake.handle_text`) пишет память и **сразу говорит вслух**
   текстом `reporting.format_remembered_macros`:
   «📌 Запомнил ваши БЖУ: «овсянка» — Б 12 · Ж 6 · У 40, 292 ккал на 200 г.
   В следующий раз подставлю их вместо оценки. Чтобы изменить — просто введите
   новые значения.» Молча запомнить нельзя: пользователь должен видеть, что
   число закрепилось;
3. `meal:ok` пишет память ещё раз (правка могла прийти от модели) —
   `remember_nutrition` идемпотентен, повторный ввод перезаписывает значение;
4. любая следующая карточка еды проходит `views.fill_from_memory` →
   `nutrition.apply_memory` и показывает числа пользователя без «≈».

**БЖУ с этикетки** (`macros_source="label"`):

- `prod:save` и `prod:eat` → `remember_product_macros` пишет напечатанные на
  упаковке числа (они уже на 100 г, `portion_g=None`) и говорит вслух
  `reporting.format_remembered_label` («📌 Запомнил БЖУ с этикетки»);
- позиция, созданная из этикетки (`prod:eat`), получает `macros_source="label"`
  — это факт с упаковки, а не оценка модели; `meal:ok` пишет такие позиции
  вторым вызовом `remember_meal_macros(..., source="label")`;
- правка чисел кнопкой `✏️ БЖУ` на карточке продукта пишет их уже как
  `source="user"` — исправленная этикетка становится словом пользователя.

Удаление: `/delete` (`repo.delete_user_data`) уносит таблицу целиком, `/export`
отдаёт её как `user_nutrition.csv`.
