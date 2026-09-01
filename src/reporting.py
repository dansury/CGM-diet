"""User-facing text. All wording rules live here, in one place.

Two hard rules (`spec/clinical.md`):

1. **No prescriptions.** Никаких доз, препаратов, инсулина, диагнозов. Питание —
   можно, и только со ссылкой на собственные данные пользователя.
2. **No causal claims.** «Наблюдается связь», «средний подъём», «после этих
   блюд» — но никогда «продукт X повышает сахар».
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape

from src.analytics.activity import ActivityContrast
from src.analytics.cgm_metrics import CGMSummary
from src.analytics.labs import FoodHint, LabReview, LabValue
from src.analytics.plate import TARGET_SHARES, PlateAdvice, PlateScore, category_label
from src.analytics.sleep import (
    SHORT_SLEEP_MIN,
    SleepContrast,
    SleepReport,
    clock_label,
    duration_label,
)
from src.analytics.stats import KeyStats
from src.analytics.symptoms import SymptomStats
from src.analytics.tags import tag_label
from src.ingest.units import format_delta, format_value
from src.vision.schemas import LabDraft, MealDraft, MedicationDraft, ProductDraft

DISCLAIMER = (
    "⚠️ Бот не ставит диагнозы и не назначает лекарства. "
    "Он показывает закономерности в ваших собственных данных. "
    "Решения по лечению — только с врачом."
)

CONFIDENCE_LABEL = {
    "high": "🟢 высокая",
    "medium": "🟡 средняя",
    "low": "⚪️ низкая",
}


def _num(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


# --------------------------------------------------------------- примеры

# Одни и те же три примера в каждой подсказке перестают читаться: человек
# видит их как узор, а не как инструкцию. Пулы ротируются, и не чаще чем
# через раз третьим пунктом подмешивается пример из того, что человек
# записывал сам (`spec/bot.md` § Примеры в подсказках).

CORRECTION_EXAMPLES: tuple[tuple[str, ...], ...] = (
    ("убери салат", "гречки было 250"),
    ("вместо курицы индейка", "добавь хлеб 50"),
    ("салата не было", "риса было 150"),
    ("замени рис на гречку", "убери масло"),
)

FOOD_EXAMPLES: tuple[tuple[str, ...], ...] = (
    ("овсянка с бананом",),
    ("гречка с курицей и салат",),
    ("творог с ягодами",),
    ("омлет из двух яиц и хлеб",),
)

GLUCOSE_EXAMPLES: tuple[tuple[str, ...], ...] = (
    ("сахар 8.2", "гк 130 mg/dl в 8:30"),
    ("глюкоза 4.5 натощак", "сахар 7,1 в 21:30"),
    ("сахар 6.4 перед едой", "гк 9.8 через час"),
)

ITEMS_EXAMPLES: tuple[tuple[str, ...], ...] = (
    ("гречка 250, курица 100, салат 150",),
    ("рис 200, рыба 120, овощи 100",),
    ("овсянка 250, банан 120",),
)

# Порции для примера, собранного из личного словаря: название остаётся в
# именительном падеже, поэтому «сырники 200 г» читается, а «убери сырники» —
# уже нет, и склонять чужие слова бот не берётся.
PERSONAL_PORTIONS = (150, 200, 250)

# Счётчики ротации живут в процессе, а не в БД: подсказка, повторившаяся
# после рестарта, не стоит ни запроса, ни записи на каждое приглашение.
_rotation: dict[tuple[str, int], int] = {}


def reset_examples() -> None:
    """Сбросить ротацию — нужно только тестам."""
    _rotation.clear()


def _tick(slot: str, user_key: int) -> int:
    value = _rotation.get((slot, user_key), 0)
    _rotation[(slot, user_key)] = value + 1
    return value


def _rotate(pool: tuple[tuple[str, ...], ...], tick: int) -> list[str]:
    return list(pool[tick % len(pool)])


def _personal_share(tick: int) -> bool:
    """Не чаще чем через раз — иначе подсказка превращается в напоминание
    о том, что бот следит за тем, что человек ест."""
    return tick % 2 == 0


def _examples(
    slot: str,
    pool: tuple[tuple[str, ...], ...],
    user_key: int,
    personal: Sequence[str],
    *,
    with_portion: bool,
) -> list[str]:
    tick = _tick(slot, user_key)
    examples = _rotate(pool, tick)
    if personal and _personal_share(tick):
        index = tick // 2
        label = personal[index % len(personal)]
        if with_portion:
            label = f"{label} {PERSONAL_PORTIONS[index % len(PERSONAL_PORTIONS)]} г"
        examples.append(label)
    return examples


def correction_examples(user_key: int = 0, personal: Sequence[str] = ()) -> list[str]:
    """Примеры правки карточки; личный — с весом, его же и просят чаще всего."""
    return _examples("correction", CORRECTION_EXAMPLES, user_key, personal, with_portion=True)


def food_examples(user_key: int = 0, personal: Sequence[str] = ()) -> list[str]:
    """Примеры описания еды словами; личный — название как оно записано."""
    return _examples("food", FOOD_EXAMPLES, user_key, personal, with_portion=False)


def glucose_examples(user_key: int = 0) -> list[str]:
    """Сахар — числа, а не имена: подмешивать из словаря нечего."""
    return _examples("glucose", GLUCOSE_EXAMPLES, user_key, (), with_portion=False)


def items_example(user_key: int = 0, personal: Sequence[str] = ()) -> str:
    """Строка «продукт и граммы через запятую» для ручного ввода состава."""
    tick = _tick("items", user_key)
    if len(personal) >= 2 and _personal_share(tick):
        index = tick // 2
        return ", ".join(
            f"{label} {PERSONAL_PORTIONS[(index + shift) % len(PERSONAL_PORTIONS)]}"
            for shift, label in enumerate(personal[:3])
        )
    return _rotate(ITEMS_EXAMPLES, tick)[0]


DISH_EXAMPLES: tuple[tuple[str, ...], ...] = (
    ("овсянка",),
    ("гречка",),
    ("творог",),
    ("рис",),
)


def dish_example(user_key: int = 0, personal: Sequence[str] = ()) -> str:
    """Название блюда для примера с БЖУ — своё, если оно есть в словаре."""
    return _examples("dish", DISH_EXAMPLES, user_key, personal, with_portion=False)[-1]


def macros_prompt(dish: str | None = None) -> str:
    return (
        "✏️ <b>БЖУ</b>\n"
        "Напишите или наговорите числа — например:\n"
        "<code>б 12 ж 6 у 40</code> — если блюдо одно;\n"
        f"<code>{dish or dish_example()} 200 г б 12 ж 6 у 40 292 ккал</code> — "
        "если блюд несколько.\n"
        "Числа — на съеденную порцию. Ккал можно не называть: посчитаю сам.\n"
        "Запомню их за этим блюдом и в следующий раз подставлю без оценки."
    )


def macros_retry(dish: str | None = None) -> str:
    return (
        "Не понял числа. Напишите, например: <code>б 12 ж 6 у 40</code> "
        f"или <code>{dish or dish_example()} 200 г б 12 ж 6 у 40</code>."
    )


def quoted(examples: Sequence[str]) -> str:
    return ", ".join(f"«{example}»" for example in examples)


# ------------------------------------------------------------------ meals


def correction_hint(examples: Sequence[str] | None = None) -> str:
    return (
        "Скорректировать можно текстом или голосовым: "
        f"{quoted(examples or correction_examples())}."
    )


def correction_retry(examples: Sequence[str] | None = None) -> str:
    return f"Не понял правку. Скажите проще — {quoted(examples or correction_examples())}."


def describe_food_hint(examples: Sequence[str] | None = None) -> str:
    return f"Можно описать словами — {quoted(examples or food_examples())}."


def describe_food_retry(examples: Sequence[str] | None = None) -> str:
    return (
        f"Не понял, что записать. Опишите еду ({quoted(examples or food_examples())}) "
        "или пришлите фото."
    )


def glucose_prompt(examples: Sequence[str] | None = None) -> str:
    return (
        "🩸 Пришлите скриншот CGM/глюкометра или напишите значение — "
        f"{quoted(examples or glucose_examples())}."
    )


def format_glucose_after_meal() -> str:
    """Предложение прислать замер после записи еды (`spec/onboarding.md`).

    Просьба, а не обещание: бот не знает, каким этот сахар будет и почему, —
    он только сопоставит замер с тем, что было съедено
    (`.specify/memory/constitution.md`, принцип II).
    """
    return (
        "🩸 Через 1–2 часа пришлите замер — число, фото глюкометра или скриншот "
        "датчика. Сопоставлю с этой едой."
    )


def glucose_hint(examples: Sequence[str] | None = None) -> str:
    return f"Напишите значение текстом — {quoted(examples or glucose_examples())}."


def meal_edit_prompt(sample: str | None = None) -> str:
    return (
        "Напишите, что поправить — например:\n"
        f"<code>{sample or items_example()}</code>\n"
        "Формат: продукт и граммы через запятую."
    )


def _applied_block(applied: list[str] | None) -> list[str]:
    """Echo what a correction changed — the user must see it was merged in,
    not that the card was rebuilt from scratch."""
    if not applied:
        return []
    return ["", "<b>Учтено из вашей правки:</b>", *(f"• {line}" for line in applied)]


def format_meal_draft(
    draft: MealDraft,
    *,
    eaten_at: datetime | None = None,
    applied: list[str] | None = None,
    examples: Sequence[str] | None = None,
) -> str:
    """`examples` — примеры правки для подсказки под карточкой; без них берётся
    очередной вариант из общего пула (`spec/bot.md` § Примеры в подсказках)."""
    totals = draft.totals()
    lines = [f"🍽 <b>{draft.title or 'Приём пищи'}</b>"]
    if eaten_at:
        lines.append(f"🕒 {eaten_at:%d.%m %H:%M}")
    for item in draft.items:
        about = "≈ " if item.estimated else ""
        # Per-item kcal: in the grand total they are invisible, and it is the
        # per-item number that shows the user a steak was priced as a salad.
        # A zero is never printed — it is a gap, not a measurement.
        parts: list[str] = []
        if item.portion_g:
            parts.append(f"{item.portion_g:.0f} г")
        if item.kcal:
            parts.append(f"{item.kcal:.0f} ккал")
        if item.carbs_g is not None:
            parts.append(f"угл {item.carbs_g:.0f} г")
        tail = f" — {about}{' · '.join(parts)}" if parts else ""
        lines.append(f"• {item.name}{tail}")
    lines.append("")
    if any(totals[k] for k in ("kcal", "protein_g", "fat_g", "carbs_g")):
        about = "≈ " if any(i.estimated for i in draft.items) else ""
        lines.append(
            f"Итого: {about}{totals['kcal']:.0f} ккал · Б {totals['protein_g']:.0f} · "
            f"Ж {totals['fat_g']:.0f} · У {totals['carbs_g']:.0f} · "
            f"клетчатка {totals['fiber_g']:.0f} г"
        )
    else:
        # Нули вместо чисел выглядели бы как измерение — честнее попросить вес.
        lines.append(
            "Итого: оценить не удалось. Напишите вес порции или уточните блюдо — пересчитаю."
        )
    if draft.confidence is not None:
        lines.append(f"Уверенность распознавания: {draft.confidence * 100:.0f}%")
    if draft.notes:
        lines.append(f"<i>{draft.notes}</i>")
    lines.extend(_applied_block(applied))
    lines.append("")
    lines.append("Всё верно?")
    lines.append(f"<i>{correction_hint(examples)}</i>")
    return "\n".join(lines)


SUGAR_AFTER_MEAL_HINT = "Через час-полтора пришлите сахар — и приём попадёт в статистику."
DICTIONARY_SHORTCUT_HINT = (
    "⭐️ Это блюдо теперь в личном словаре — в следующий раз хватит одной кнопки (/my)."
)


def format_meal_macros_line(draft: MealDraft) -> str:
    """Одна строка БЖУ приёма пищи. Пусто, когда считать нечего."""
    totals = draft.totals()
    if not any(totals[key] for key in ("kcal", "protein_g", "fat_g", "carbs_g")):
        return ""
    about = "≈ " if any(item.estimated for item in draft.items) else ""
    return (
        f"{about}{totals['kcal']:.0f} ккал · Б {totals['protein_g']:.0f} · "
        f"Ж {totals['fat_g']:.0f} · У {totals['carbs_g']:.0f} · "
        f"клетчатка {totals['fiber_g']:.0f} г"
    )


def format_meal_saved(
    draft: MealDraft, *, title: str, eaten_at: datetime, shortcut: bool = False
) -> str:
    """Подтверждение записи еды: время, название и БЖУ.

    Название и числа идут одним моноширинным блоком — в Telegram такой блок
    копируется одним касанием, и человек может перенести строку куда угодно,
    не переписывая её руками.
    """
    body = escape(title)
    macros = format_meal_macros_line(draft)
    if macros:
        body = f"{body}\n{macros}"
    lines = [f"✅ Записано: {eaten_at:%H:%M} <code>{body}</code>", SUGAR_AFTER_MEAL_HINT]
    if shortcut:
        lines.append(DICTIONARY_SHORTCUT_HINT)
    return "\n".join(lines)


def format_remembered_macros(draft: MealDraft, names: list[str]) -> str:
    """«Запомнил» называет сами числа — иначе опечатку не заметить."""
    lines: list[str] = []
    for item in draft.items:
        if item.name not in names:
            continue
        macros = " · ".join(
            f"{label} {value:g}"
            for label, value in (("Б", item.protein_g), ("Ж", item.fat_g), ("У", item.carbs_g))
            if value is not None
        )
        basis = f" на {item.portion_g:.0f} г" if item.portion_g else " на 100 г"
        kcal = f", {item.kcal:.0f} ккал" if item.kcal else ""
        lines.append(f"• «{item.name}» — {macros}{kcal}{basis}")
    body = "\n".join(lines)
    return (
        "📌 Запомнил ваши БЖУ:\n"
        f"{body}\n"
        "В следующий раз подставлю их вместо оценки. "
        "Чтобы изменить — нажмите «✏️ БЖУ» и введите новые значения."
    )


def format_remembered_label(draft: ProductDraft, name: str, *, typed: bool = False) -> str:
    """«Запомнил» для продукта: числа на 100 г, как они напечатаны на упаковке."""
    macros = " · ".join(
        f"{label} {value:g}"
        for label, value in (
            ("Б", draft.protein_100),
            ("Ж", draft.fat_100),
            ("У", draft.carbs_100),
        )
        if value is not None
    )
    kcal = f"{draft.kcal_100:.0f} ккал" if draft.kcal_100 is not None else ""
    numbers = " · ".join(part for part in (kcal, macros) if part) or "—"
    head = (
        "📌 Запомнил ваши БЖУ:"
        if typed
        else "📌 Запомнил БЖУ с этикетки:"
    )
    return (
        f"{head}\n"
        f"• «{name}» — {numbers} на 100 г\n"
        "В следующий раз подставлю их вместо оценки. "
        "Чтобы изменить — нажмите «✏️ БЖУ» и введите новые значения."
    )


def format_product(draft: ProductDraft, *, mode: str = "eaten") -> str:
    """`mode`: `eaten` — уже съедено, `check` — проверка перед покупкой."""
    head = "🛒 <b>Проверка продукта</b>" if mode == "check" else "🏷 <b>Продукт с этикетки</b>"
    lines = [head, f"<b>{draft.brand + ' · ' if draft.brand else ''}{draft.name}</b>"]
    if draft.kcal_100 is not None:
        lines.append(
            f"На 100 г: {_num(draft.kcal_100, 0)} ккал · Б {_num(draft.protein_100)} · "
            f"Ж {_num(draft.fat_100)} · У {_num(draft.carbs_100)}"
        )
    if draft.sugars_100 is not None:
        lines.append(f"Из них сахаров: <b>{_num(draft.sugars_100)} г</b>")
    if draft.fiber_100 is not None:
        lines.append(f"Клетчатка: {_num(draft.fiber_100)} г")
    if draft.ingredients:
        shown = ", ".join(draft.ingredients[:8])
        lines.append(f"Состав: {shown}{'…' if len(draft.ingredients) > 8 else ''}")
    if draft.flags:
        lines.append("Метки: " + ", ".join(tag_label(f) for f in draft.flags))
    return "\n".join(lines)


def format_product_verdict(draft: ProductDraft, matches: list[KeyStats], unit: str) -> str:
    """Before-purchase answer, grounded in the user's own history."""
    lines = [format_product(draft, mode="check"), ""]
    actionable = [m for m in matches if m.actionable]
    if actionable:
        lines.append("📊 <b>Что говорят ваши данные</b>")
        for stat in actionable[:3]:
            lines.append(
                f"• «{_key_label(stat)}»: наблюдалось {stat.n} раз, "
                f"средний подъём {format_delta(stat.mean_delta, unit)} "
                f"({CONFIDENCE_LABEL[stat.confidence]} достоверность)"
            )
        lines.append("")
        lines.append("Похоже, этот продукт стоит взять в меньшем объёме или заменить.")
    elif matches:
        lines.append(
            "📊 Похожие компоненты в вашей истории пока встречались редко — "
            "уверенного вывода нет. Если купите, отправьте фото после еды: "
            "наберём наблюдений."
        )
    else:
        lines.append(
            "📊 Таких компонентов в вашей истории ещё не было. "
            "Съедите — пришлите фото, и продукт попадёт в статистику."
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ stats

def _key_label(stat: KeyStats) -> str:
    return tag_label(stat.key) if stat.key_type == "tag" else stat.key


def format_stats(
    stats: list[KeyStats], *, unit: str = "mmol/L", window: str = "1h", limit: int = 8
) -> str:
    if not stats:
        return (
            "Пока недостаточно данных для статистики.\n"
            "Нужно, чтобы приёмы пищи и измерения сахара пересеклись хотя бы "
            "по трём приёмам. Присылайте фото еды и показания — и вернитесь сюда."
        )
    title = "через 1 час" if window == "1h" else "через 2 часа"
    lines = [f"📊 <b>Подъём сахара после еды ({title})</b>", ""]
    for stat in stats[:limit]:
        lines.append(
            f"<b>{_key_label(stat)}</b> — {CONFIDENCE_LABEL[stat.confidence]}\n"
            f"   наблюдений: {stat.n} · средний подъём {format_delta(stat.mean_delta, unit)} · "
            f"медиана {format_delta(stat.median_delta, unit)} · "
            f"максимум {format_delta(stat.max_delta, unit)}"
        )
        if stat.contrast is not None and stat.mean_without is not None:
            lines.append(
                f"   без этого компонента: {format_delta(stat.mean_without, unit)} "
                f"(разница {format_delta(stat.contrast, unit)}"
                + (f", p={stat.p_value:.3f}" if stat.p_value is not None else "")
                + ")"
            )
        lines.append("")
    lines.append(
        "<i>Формулировка: наблюдается связь, а не доказанная причина. "
        "Чем больше наблюдений — тем выше достоверность.</i>"
    )
    return "\n".join(lines)


def format_recommendations(stats: list[KeyStats], *, unit: str = "mmol/L") -> str:
    """Nutrition-only suggestions, each backed by the user's own numbers."""
    actionable = [s for s in stats if s.actionable][:3]
    if not actionable:
        return (
            "Пока нет компонентов с достаточной достоверностью, чтобы что-то "
            "советовать убирать. Продолжайте вести дневник."
        )
    lines = ["🥗 <b>Что стоит попробовать изменить</b>", ""]
    for stat in actionable:
        lines.append(
            f"• Сократить <b>{_key_label(stat)}</b>. Основание: {stat.n} наблюдений, "
            f"средний подъём {format_delta(stat.mean_delta, unit)}, "
            f"без него — {format_delta(stat.mean_without or 0, unit)}."
        )
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def format_cgm_summary(summary: CGMSummary, *, unit: str = "mmol/L") -> str:
    if summary.n == 0:
        return "Показаний глюкозы пока нет."
    lines = [
        f"🩸 <b>Глюкоза за период</b> ({summary.n} измерений, {summary.days:.1f} дн.)",
        f"Среднее: {format_value(summary.mean, unit)}",
        f"Вариабельность CV: {_num(summary.cv, 1, '%')} (цель < 36%)",
        f"Время в диапазоне 3.9–10.0: {_num(summary.tir, 1, '%')} (цель > 70%)",
        f"Выше диапазона: {_num(summary.tar, 1, '%')} · ниже: {_num(summary.tbr, 1, '%')}",
        f"GMI: {_num(summary.gmi, 2, '%')} · расчётный HbA1c: {_num(summary.ea1c, 2, '%')}",
    ]
    if summary.mage is not None:
        lines.append(f"MAGE (размах колебаний): {_num(summary.mage, 2)} ммоль/л")
    if summary.hbgi is not None:
        lines.append(f"Индексы риска: LBGI {_num(summary.lbgi, 2)} · HBGI {_num(summary.hbgi, 2)}")
    lines.append("")
    lines.append("<i>Это описательные метрики, не диагноз.</i>")
    return "\n".join(lines)


def format_symptoms(stats: list[SymptomStats], *, unit: str = "mmol/L") -> str:
    if not stats:
        return "Отметок самочувствия пока мало — статистики по симптомам нет."
    lines = ["🧭 <b>Симптомы и сахар</b>", ""]
    for stat in stats[:6]:
        lines.append(
            f"<b>{stat.symptom}</b> — {stat.n} отметок\n"
            f"   сахар в этот момент: {format_value(stat.mean_glucose or 0, unit)}"
            + (
                f" · в остальные моменты: {format_value(stat.mean_without, unit)}"
                if stat.mean_without is not None
                else ""
            )
        )
        if stat.share_postprandial is not None:
            lines.append(f"   после еды (до 2.5 ч): {stat.share_postprandial:.0f}% случаев")
        if stat.n_low_glucose:
            lines.append(f"   ⚠️ из них при сахаре ниже 3.9: {stat.n_low_glucose}")
        lines.append("")
    lines.append("<i>Симптомы могут иметь много причин. Это связь, а не объяснение.</i>")
    return "\n".join(lines)


def format_activity(contrast: ActivityContrast, *, unit: str = "mmol/L") -> str:
    if not contrast.meaningful:
        return (
            "Данных об активности пока мало. Подключите Samsung Health "
            "(/health), чтобы сравнить приёмы пищи с прогулкой и без."
        )
    return (
        "🚶 <b>Еда и активность</b>\n"
        f"После еды с прогулкой (≥{contrast.threshold} шагов за час): "
        f"средний подъём {format_delta(contrast.mean_active or 0, unit)} "
        f"({contrast.n_active} набл.)\n"
        f"Без прогулки: {format_delta(contrast.mean_sedentary or 0, unit)} "
        f"({contrast.n_sedentary} набл.)\n"
        f"Разница: <b>{format_delta(contrast.difference or 0, unit)}</b>"
        + (f" · p={contrast.p_value:.3f}" if contrast.p_value is not None else "")
    )


# --------------------------------------------------------------- сон

SLEEP_SOURCE_LABEL = {
    "health": "Samsung Health",
    "presence": "по появлениям в чате",
}

SLEEP_EMPTY = (
    "😴 <b>Сон</b>\n\n"
    "Пока не из чего считать ночи. Подключите Samsung Health (/health) — "
    "часы отдают готовые сессии сна — либо включите наблюдение по появлениям "
    "в чате кнопкой ниже."
)

SLEEP_PRESENCE_REMINDER = (
    "😴 <b>Наблюдение за сном не работает</b>\n\n"
    "Вы включили оценку сна по появлениям в чате, но больше суток "
    "не заходили к боту — ночей из этого не построить.\n\n"
    "Что можно сделать:\n"
    "• просто отмечаться утром и вечером — хватает пары нажатий любой кнопки "
    "или короткого сообщения;\n"
    "• проверить, что бот не заблокирован и уведомления от него разрешены: "
    "<i>чат с ботом → имя сверху → Уведомления</i>;\n"
    "• подключить Samsung Health (/health) — тогда ночи считаются сами, "
    "без вашего участия;\n"
    "• либо выключить функцию: <code>/set sleep off</code>.\n\n"
    "Пока данных нет, бот про сон ничего не показывает."
)


def _sleep_metric_text(contrast: SleepContrast, unit: str) -> str | None:
    """Одна строка контраста; величины — в единицах своей метрики."""
    if contrast.difference is None:
        return None
    if contrast.metric == "kcal":
        head = "съедено за день"
        value_a = f"{contrast.mean_a:.0f} ккал"
        value_b = f"{contrast.mean_b:.0f} ккал"
        diff = f"{abs(contrast.difference):.0f} ккал"
    elif contrast.metric == "carbs":
        head = "углеводов за день"
        value_a = f"{contrast.mean_a:.0f} г"
        value_b = f"{contrast.mean_b:.0f} г"
        diff = f"{abs(contrast.difference):.0f} г"
    elif contrast.metric == "glucose":
        head = "средний сахар за день"
        value_a = format_value(contrast.mean_a or 0, unit)
        value_b = format_value(contrast.mean_b or 0, unit)
        diff = format_delta(abs(contrast.difference), unit)
    else:  # rise
        head = "средний подъём после еды"
        value_a = format_delta(contrast.mean_a or 0, unit)
        value_b = format_delta(contrast.mean_b or 0, unit)
        diff = format_delta(abs(contrast.difference), unit)
    direction = "больше" if contrast.difference > 0 else "меньше"
    tail = f" · p={contrast.p_value:.3f}" if contrast.p_value is not None else ""
    return (
        f"• {head}: {contrast.label_a} — <b>{value_a}</b> ({contrast.n_a} дн.), "
        f"{contrast.label_b} — {value_b} ({contrast.n_b} дн.); "
        f"разница {diff} {direction}{tail}"
    )


def format_sleep(report: SleepReport, *, unit: str = "mmol/L") -> str:
    """Карточка сна: сколько, насколько ровно и что бывает в такие дни.

    Формулировки те же, что и везде: наблюдаемая связь, а не причина
    (`spec/clinical.md`). Короткий сон не «поднимает сахар» — в дни после
    коротких ночей средние цифры такие-то.
    """
    stats = report.stats
    if not stats.n_nights:
        return SLEEP_EMPTY
    source = SLEEP_SOURCE_LABEL.get(stats.source, stats.source)
    lines = [
        "😴 <b>Сон</b>",
        f"Источник: {source} · ночей учтено: {stats.n_nights}",
        "",
        f"Средняя длительность: <b>{duration_label(stats.mean_duration_min)}</b>",
        f"Обычный отбой: {clock_label(stats.median_bedtime_min)} · "
        f"подъём: {clock_label(stats.median_wake_min)}",
    ]
    short_hours = SHORT_SLEEP_MIN // 60
    lines.append(
        f"Коротких ночей (меньше {short_hours} ч): "
        f"{stats.short_nights} из {stats.n_nights}"
    )
    if stats.bedtime_sd_min is not None:
        steady = "ровный" if stats.regular else "плавающий"
        lines.append(
            f"Режим {steady}: отбой гуляет на ±{stats.bedtime_sd_min:.0f} мин, "
            f"подъём — на ±{(stats.wake_sd_min or 0):.0f} мин"
        )
    if stats.source == "presence":
        lines.append(
            "<i>Оценка по появлениям в чате — она приблизительная: "
            "бот видит только моменты, когда вы к нему заходите.</i>"
        )

    rows = [text for c in report.contrasts if (text := _sleep_metric_text(c, unit))]
    if rows:
        lines += ["", "<b>Что бывает в такие дни</b>", *rows]
    else:
        lines += [
            "",
            "Связей с едой и сахаром пока не видно — нужно хотя бы по три дня "
            "в каждой группе.",
        ]
    return "\n".join(lines)


def format_sleep_short(report: SleepReport) -> str:
    """Одна строка для /stats — без контрастов, только режим."""
    stats = report.stats
    if not stats.n_nights:
        return ""
    return (
        f"😴 Сон: {duration_label(stats.mean_duration_min)} в среднем за "
        f"{stats.n_nights} ноч., отбой около {clock_label(stats.median_bedtime_min)}"
    )


def format_labs(draft: LabDraft) -> str:
    lines = [f"🧪 <b>{draft.panel or 'Анализы'}</b>"]
    if draft.taken_at:
        lines.append(f"Дата: {draft.taken_at:%d.%m.%Y}")
    lines.append("")
    marks = {"high": "🔺", "low": "🔻", "normal": "✅", None: "•"}
    for marker in draft.markers:
        ref = ""
        if marker.ref_low is not None or marker.ref_high is not None:
            ref = f" (норма {_num(marker.ref_low)}–{_num(marker.ref_high)})"
        value = marker.value_text or _num(marker.value, 2)
        lines.append(f"{marks[marker.flag]} {marker.marker}: <b>{value}</b> {marker.unit or ''}{ref}")
    lines.append("")
    lines.append("Сохранить в дневник?")
    lines.append(
        "<i>Скорректировать можно текстом или голосовым: «глюкоза 6.1», "
        "«HbA1c 5.8».</i>"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ medications

def format_medication_draft(
    draft: MedicationDraft,
    *,
    taken_at: datetime | None = None,
    applied: list[str] | None = None,
) -> str:
    lines = [f"💊 <b>{draft.name or 'Препарат'}</b>"]
    if draft.inn and draft.inn.lower() not in (draft.name or "").lower():
        lines.append(f"Действующее вещество: {draft.inn}")
    if draft.dose_text:
        lines.append(f"Дозировка на упаковке: {draft.dose_text}")
    if draft.form:
        lines.append(f"Форма: {draft.form}")
    if taken_at:
        lines.append(f"🕒 приём {taken_at:%d.%m %H:%M}")
    if draft.confidence is not None:
        lines.append(f"Уверенность распознавания: {draft.confidence * 100:.0f}%")
    lines.extend(_applied_block(applied))
    lines.append("")
    lines.append("Записать этот приём?")
    lines.append(
        "<i>Это только журнал: бот не назначает препараты и не считает дозы. "
        "Скорректировать можно текстом или голосовым — «метформин 1000».</i>"
    )
    return "\n".join(lines)


def format_medications(rows: list[tuple[datetime, str, str | None]], *, days: int = 30) -> str:
    """Journal of doses; `rows` are (local time, name, dose)."""
    if not rows:
        return (
            "💊 <b>Лекарства</b>\n\nПока пусто. Сфотографируйте упаковку или напишите "
            "«выпил метформин 850» — запись попадёт в дневник и в личный словарь."
        )
    lines = [f"💊 <b>Лекарства за {days} дн.</b>", ""]
    for at, name, dose in rows[-30:]:
        lines.append(f"• {at:%d.%m %H:%M} — {name}{(' ' + dose) if dose else ''}")
    return "\n".join(lines)


def format_med_coverage(coverages: list) -> str | None:
    """A drug present in most of the observations is a confounder the user must
    see: without it «после риса выше» is silently a claim about the drug."""
    rows = [c for c in coverages if c.share >= 0.3]
    if not rows:
        return None
    lines = ["💊 <b>Что ещё было в эти окна</b>"]
    for row in rows[:3]:
        lines.append(
            f"• {row.name}: приём попал в {row.n_covered} из {row.n_total} наблюдений "
            f"({row.share * 100:.0f}%)"
        )
    lines.append(
        "<i>Это не оценка препарата, а контекст: цифры по еде читаются с поправкой "
        "на то, что было принято.</i>"
    )
    return "\n".join(lines)


def format_med_side_effects(links: list) -> str | None:
    """Reference lookup, never a causal claim (`spec/meds.md` § Формулировки)."""
    if not links:
        return None
    lines = ["💊 <b>Справка по побочным эффектам</b>", ""]
    for link in links[:5]:
        effect = link.effect_ru or link.symptom
        lines.append(
            f"• «{link.symptom}» отмечено {link.n_after_dose} раз(а) в течение "
            f"8 часов после приёма «{link.name}»; в справочнике побочных эффектов "
            f"для этого препарата значится «{effect}»."
        )
    lines.append("")
    lines.append(
        "<i>Совпадение по времени — не доказательство причины. Не меняйте приём "
        "и дозу самостоятельно: покажите это врачу.</i>"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ body & workouts

BODY_DISCLAIMER = (
    "⚠️ Коридор калорий — ориентир, посчитанный из ваших же цифр, а не назначение. "
    "При беременности, заболеваниях почек и печени, диабете и приёме лекарств "
    "согласуйте его с врачом."
)

WEIGHT_PROMPT = (
    "⚖️ <b>Пора взвеситься</b>\n\n"
    "Прошло две недели с последнего замера. Встаньте на весы утром, натощак, "
    "и пришлите число — «вес 82,4». Если весы умеют состав тела, можно добавить "
    "«жир 24% мышцы 58 кг вода 55%» или просто прислать фото экрана."
)

BAR_WIDTH = 10


def progress_bar(share: float, *, width: int = BAR_WIDTH) -> str:
    """Полоса из 10 клеток: съедено / остаток / перебор."""
    share = max(share, 0.0)
    filled = min(int(round(share * width)), width)
    if share > 1.0:
        over = min(int(round((share - 1.0) * width)), width)
        return "▒" * over + "▓" * max(width - over, 0)
    return "▓" * filled + "░" * (width - filled)


def format_meals_today(meals_done: int, meals_per_day: int) -> str:
    """«Приёмов пищи: 2 из 3». Пусто, когда сегодня ещё ни одного приёма."""
    if meals_done <= 0:
        return ""
    return f"🍽 Приёмов пищи: {meals_done} из {meals_per_day}"


def format_day_progress(balance, *, goal=None, trend=None, meals: str = "") -> str:
    """Дневной коридор после приёма пищи (`spec/body.md` § Дневной коридор)."""
    share = balance.share
    lines = ["📊 <b>Сегодня</b>"]
    if goal is not None and goal.target_weight_kg:
        rate = f" · {goal.rate_kg_week:g} кг/нед" if goal.rate_kg_week else ""
        lines.append(f"🎯 цель {goal.target_weight_kg:g} кг{rate}")
    lines.append(
        f"{progress_bar(share)} {share * 100:.0f}% "
        f"({balance.consumed_kcal:.0f} из {balance.allowance_kcal:.0f} ккал)"
    )
    if balance.burned_kcal:
        lines.append(
            f"Ориентир {balance.target_kcal:.0f} ккал + тренировки ≈ {balance.burned_kcal:.0f} ккал"
        )
    if balance.over:
        lines.append(f"Сверх ориентира: <b>{-balance.available_kcal:.0f} ккал</b>")
    else:
        lines.append(f"Осталось на сегодня: <b>{balance.available_kcal:.0f} ккал</b>")
    if balance.carbs_g:
        lines.append(f"Углеводы за день: {balance.carbs_g:.0f} г")
    if meals:
        lines.append(meals)
    if trend is not None and trend.rate_kg_week is not None:
        lines.append(
            f"Вес за {trend.days:.0f} дн.: {trend.first_kg:g} → {trend.last_kg:g} кг "
            f"({trend.change_kg:+g} кг, {trend.rate_kg_week:+g} кг/нед)"
        )
    return "\n".join(lines)


GOAL_HINT = "🎯 Задайте цель — /body — и буду показывать коридор и остаток на день."


def format_day_totals(balance, *, meals: str = "") -> str:
    """Итог дня без коридора: цели нет — процентов и остатка тоже нет.

    Съеденное за день человек вправе видеть всегда; «73 % нормы» без цели —
    выдуманная норма (`spec/body.md` § Дневной коридор).
    """
    parts = [f"съедено {balance.consumed_kcal:.0f} ккал"]
    if balance.carbs_g:
        parts.append(f"углеводы {balance.carbs_g:.0f} г")
    if balance.burned_kcal:
        parts.append(f"тренировки ≈ {balance.burned_kcal:.0f} ккал")
    head = "📊 <b>Сегодня</b>: " + " · ".join(parts)
    return "\n".join(part for part in (head, meals, GOAL_HINT) if part)


# ------------------------------------------------------------------ Harvard plate

PLATE_RULE = (
    "Гарвардская тарелка: ½ — овощи и фрукты, ¼ — цельные злаки, ¼ — белок. "
    "Картофель и белая мука в «половину овощей» не идут."
)
PLATE_OFF_HINT = "Отключить оценку тарелки: <code>/set plate off</code>"

_PLATE_ORDER = ("veg", "fruit", "grain", "protein", "refined", "extra")


def format_plate_score(score: PlateScore, *, with_score: bool = True) -> str:
    """Состав тарелки по массе — только доли, без оценок «правильно/нет»."""
    if score.mass_g <= 0:
        return ""
    if with_score:
        lines = [f"🥗 <b>Тарелка</b> — {score.score:.0f} из 100"]
        lines.append(progress_bar(score.score / 100.0))
    else:
        lines = ["🥗 <b>Тарелка</b> /plate"]
    for category in _PLATE_ORDER:
        grams = score.grams.get(category)
        if not grams:
            continue
        share = score.shares.get(category, 0.0) * 100
        target = TARGET_SHARES.get(category)
        aim = f" (ориентир {target * 100:.0f}%)" if target else ""
        lines.append(f"• {category_label(category)}: {share:.0f}%{aim} — {grams:.0f} г")
    if score.estimated_mass:
        lines.append("<i>Часть порций я оценил сам — назовите граммы, и доли станут точнее.</i>")
    return "\n".join(lines)


def format_meal_kcal_progress(meal_kcal: float, meal_kcal_budget: float) -> str:
    """Полоса калорий этого приёма — суточный ориентир, делённый на приёмы.

    `spec/plate.md` § Калории приёма. Показывается при каждой записи, в
    отличие от полосы пропорций, которая говорит только при дисбалансе.
    """
    share = meal_kcal / meal_kcal_budget
    return (
        f"🍽 {progress_bar(share)} {share * 100:.0f}% "
        f"({meal_kcal:.0f} из {meal_kcal_budget:.0f} ккал на приём)"
    )


_ROUND_UP_CATEGORIES = {"veg", "protein"}


def _round_gap_50(category: str, grams: float) -> int:
    """Round gap to 50 g: protein/veg up, everything else down."""
    import math

    if category in _ROUND_UP_CATEGORIES:
        return int(math.ceil(grams / 50.0)) * 50
    return int(grams / 50.0) * 50


def _format_gap(gap, *, round_50: bool) -> str:
    g = _round_gap_50(gap.category, gap.grams) if round_50 else int(round(gap.grams))
    return f"{category_label(gap.category)} +{g} г"


def format_plate_advice(advice: PlateAdvice, *, with_rule: bool = False) -> str:
    """Что добрать в этот приём пищи и что остаётся на день.

    Рекомендация по питанию — она разрешена (`spec/clinical.md`), но говорит
    только о пропорциях тарелки и никогда о болезнях и «нормах» человека.
    """
    parts = [format_plate_score(advice.score, with_score=with_rule)]
    rhythm = advice.rhythm
    if advice.now:
        gaps = ", ".join(_format_gap(gap, round_50=True) for gap in advice.now)
        parts.append(f"➕ До полной тарелки: {gaps}")
    else:
        parts.append("✅ Пропорции тарелки в этот приём пищи собраны.")
    source = {
        "user": "вы задали",
        "stats": "по вашей статистике",
        "default": "по умолчанию",
    }[rhythm.meals_source]
    tail = (
        f"Приём {advice.meals_done} из {rhythm.meals_per_day} за день ({source})."
        if advice.meals_left
        else f"Это {advice.meals_done}-й приём пищи из {rhythm.meals_per_day} ({source})."
    )
    parts.append(tail)
    if advice.meals_left and advice.day_gaps:
        rest = ", ".join(_format_gap(gap, round_50=True) for gap in advice.day_gaps)
        word = "приём" if advice.meals_left == 1 else "приёма"
        parts.append(f"🗓 На оставшиеся {advice.meals_left} {word}: {rest}.")
    elif not advice.day_gaps:
        parts.append("🗓 За день пропорции тарелки уже набраны.")
    if with_rule:
        parts.append(f"<i>{PLATE_RULE}</i>")
        parts.append(f"<i>{PLATE_OFF_HINT}</i>")
    return "\n".join(part for part in parts if part)


def format_plate_settings(
    *, enabled: bool, meals_per_day: int | None, measured: int | None, session_min: int
) -> str:
    """Карточка /plate: что настроено и как это можно поменять."""
    lines = ["🥗 <b>Гарвардская тарелка</b>", PLATE_RULE, ""]
    lines.append(f"Оценка после каждой еды: {'включена' if enabled else 'выключена'}")
    if meals_per_day:
        lines.append(f"Приёмов пищи в день: {meals_per_day} (задано вами)")
    elif measured:
        lines.append(f"Приёмов пищи в день: {measured} (по вашей статистике)")
    else:
        lines.append("Приёмов пищи в день: 3 (по умолчанию — статистики пока мало)")
    lines.append(
        f"Один приём пищи — это все блюда подряд в течение {session_min} мин"
        + (" (по вашей статистике)" if session_min != 60 else "")
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ labs → food

LAB_ADVICE_DISCLAIMER = (
    "⚠️ Это не расшифровка анализов и не назначение. Сравниваю только с референсом "
    "из вашего же документа, а продукты называю как пищевые источники нутриента. "
    "Показатель вне референса — повод показать результат врачу; БАДы и дозы — тоже к нему."
)


def format_lab_value(value: LabValue) -> str:
    marks = {"high": "🔺", "low": "🔻", "normal": "✅"}
    ref = ""
    if value.ref_low is not None or value.ref_high is not None:
        ref = f" (референс {_num(value.ref_low)}–{_num(value.ref_high)})"
    from src.analytics.labs import direction as _direction

    mark = marks.get(_direction(value) or "normal", "•")
    return f"{mark} {value.marker}: <b>{value.display}</b> {value.unit or ''}{ref}".strip()


def format_food_hint(hint: FoodHint) -> str:
    side = "ниже" if hint.direction == "low" else "выше"
    lines = [
        f"<b>{hint.value.marker}</b> — {side} референса из документа "
        f"({hint.value.display} {hint.value.unit or ''}).".replace("  ", " "),
        f"Пищевые источники ({hint.nutrient.label}): " + ", ".join(hint.foods) + ".",
    ]
    if hint.nutrient.note:
        lines.append(f"<i>{hint.nutrient.note}.</i>")
    return "\n".join(lines)


def format_lab_review(review: LabReview, *, header: str = "🧪 <b>Ваши анализы</b>") -> str:
    """Последние значения маркеров + продукты-источники для тех, что вне нормы."""
    if review.n_markers == 0:
        return (
            "🧪 Анализов пока нет. Пришлите фото, PDF или текст результата — "
            "сохраню маркеры с референсами из документа."
        )
    lines = [header, ""]
    for value in review.out_of_range:
        lines.append(format_lab_value(value))
    if not review.out_of_range:
        lines.append("✅ Все сохранённые маркеры в пределах референсов из ваших документов.")
    if review.hints:
        lines.append("")
        lines.append("🥑 <b>Продукты-источники</b>")
        for hint in review.hints:
            lines.append(format_food_hint(hint))
            lines.append("")
    lines.append(LAB_ADVICE_DISCLAIMER)
    return "\n".join(lines).strip()


# ------------------------------------------------------------------ feature hints

def format_feature_hint(feature) -> str:
    """Рассказ об одной неиспользованной возможности — коротко и по делу."""
    lines = [
        "💡 <b>Одна возможность, которой вы ещё не пользовались</b>",
        "",
        f"<b>{feature.title}</b>",
        feature.blurb,
    ]
    if feature.command:
        lines.append(f"Команда: {feature.command}")
    lines.append("")
    lines.append("«Не нужно» — уберу из меню и больше не напомню.")
    return "\n".join(lines)


def format_hidden_list(features) -> str:
    if not features:
        return (
            "Скрытых возможностей нет — в меню всё, что бот умеет.\n"
            "Скрыть что-то можно кнопкой «🚫 Не нужно» в подсказке."
        )
    lines = ["🙈 <b>Скрытые возможности</b>", "", "Они работают — просто убраны из меню:"]
    for feature in features:
        command = f" — {feature.command}" if feature.command else ""
        lines.append(f"• <b>{feature.title}</b>{command}")
    lines.append("")
    lines.append("Кнопкой ниже верну любую обратно в меню.")
    return "\n".join(lines)


def format_goal_plan(plan, *, kind: str, target_weight_kg: float | None) -> str:
    """Из чего получился ориентир — и что в цели пришлось урезать."""
    words = {"lose": "снижение", "gain": "набор", "maintain": "удержание"}
    lines = [f"🎯 <b>Цель: {words.get(kind, kind)} веса</b>"]
    if target_weight_kg:
        lines.append(f"Целевой вес: <b>{target_weight_kg:g} кг</b>")
    if plan.bmr_kcal:
        lines.append(f"Основной обмен (BMR): ≈ {plan.bmr_kcal:.0f} ккал")
    if plan.tdee_kcal:
        lines.append(f"Суточный расход (TDEE): ≈ {plan.tdee_kcal:.0f} ккал")
    if kind != "maintain":
        word = "дефицит" if plan.delta_kcal < 0 else "профицит"
        lines.append(
            f"Темп: {plan.rate_kg_week:g} кг/нед → {word} {abs(plan.delta_kcal):.0f} ккал в день"
        )
    lines.append(f"<b>Ориентир на день: {plan.target_kcal:.0f} ккал</b>")
    if plan.weeks and plan.eta:
        lines.append(f"При таком темпе — около {plan.weeks:g} нед., ориентир {plan.eta:%d.%m.%Y}")
    if plan.estimated:
        lines.append(
            "<i>Рост и возраст не заполнены — расход посчитан грубо. "
            "Добавьте их в /body, и ориентир станет точнее.</i>"
        )
    if plan.capped:
        lines.append("")
        lines.append("<b>Что пришлось поправить в цели:</b>")
        lines.extend(f"• {reason}" for reason in plan.capped)
    lines.append("")
    lines.append(BODY_DISCLAIMER)
    return "\n".join(lines)


def format_body_card(
    *,
    profile=None,
    last=None,
    goal=None,
    plan=None,
    trend=None,
    bmi_value: float | None = None,
    bmi_note: str | None = None,
    age: int | None = None,
) -> str:
    from src.analytics.body import ACTIVITY_LABELS

    lines = ["⚖️ <b>Тело и цель</b>", ""]
    if last is not None:
        lines.append(f"Вес: <b>{last.weight_kg:g} кг</b> ({last.measured_at:%d.%m})")
        composition = _composition_line(last)
        if composition:
            lines.append(composition)
    else:
        lines.append("Вес: — · напишите «вес 82,4» или пришлите фото весов")
    if profile is not None:
        details = []
        if profile.height_cm:
            details.append(f"рост {profile.height_cm:g} см")
        if age:
            details.append(f"возраст {age}")
        if profile.sex:
            details.append("мужской" if profile.sex == "m" else "женский")
        if profile.pregnant:
            details.append("беременность")
        if details:
            lines.append("Профиль: " + ", ".join(details))
        lines.append(f"Активность: {ACTIVITY_LABELS.get(profile.activity, profile.activity)}")
        if profile.conditions:
            lines.append(f"Особые состояния: {profile.conditions}")
        focus = _focus_line(profile)
        if focus:
            lines.append(focus)
    if bmi_value:
        note = f" — по классификации ВОЗ это {bmi_note}" if bmi_note else ""
        lines.append(f"ИМТ: {bmi_value:g}{note}")
    if trend is not None and trend.rate_kg_week is not None:
        lines.append(
            f"Динамика: {trend.first_kg:g} → {trend.last_kg:g} кг за {trend.days:.0f} дн. "
            f"({trend.rate_kg_week:+g} кг/нед)"
        )
    lines.append("")
    if goal is not None and plan is not None:
        lines.append(format_goal_plan(plan, kind=goal.kind, target_weight_kg=goal.target_weight_kg))
    elif goal is not None:
        lines.append(f"🎯 Цель: {goal.target_weight_kg:g} кг")
    else:
        lines.append(
            "🎯 Цель не задана. Задайте её — и после каждого приёма пищи "
            "буду показывать полосу дневного коридора."
        )
    return "\n".join(lines)


def _focus_line(profile) -> str | None:
    """Зачем человек пришёл — своими словами, как он их выбрал (`src/goals.py`)."""
    from src.goals import decode, titles

    picked = decode(getattr(profile, "focus", None))
    if not picked:
        return None
    labels = titles(picked, note=getattr(profile, "focus_note", None) or None)
    return "Цели: " + " · ".join(label.lower() for label in labels) if labels else None


def _composition_line(row) -> str:
    parts = []
    for label, value, suffix in (
        ("жир", row.body_fat_pct, "%"),
        ("мышцы", row.muscle_mass_kg, " кг"),
        ("вода", row.water_pct, "%"),
        ("кости", row.bone_mass_kg, " кг"),
        ("висцеральный", row.visceral_fat, ""),
    ):
        if value is not None:
            parts.append(f"{label} {value:g}{suffix}")
    return "Состав тела: " + " · ".join(parts) if parts else ""


def format_measurement_draft(draft) -> str:
    lines = ["⚖️ <b>Показания весов</b>", ""]
    if draft.weight_kg:
        lines.append(f"Вес: <b>{draft.weight_kg:g} кг</b>")
    else:
        lines.append("Вес не разобрал — допишите его текстом.")
    for label, value, suffix in (
        ("Жир", draft.body_fat_pct, "%"),
        ("Мышечная масса", draft.muscle_mass_kg, " кг"),
        ("Вода", draft.water_pct, "%"),
        ("Костная масса", draft.bone_mass_kg, " кг"),
        ("Висцеральный жир", draft.visceral_fat, ""),
        ("Основной обмен", draft.bmr_kcal, " ккал"),
    ):
        if value is not None:
            lines.append(f"{label}: {value:g}{suffix}")
    if draft.confidence is not None:
        lines.append(f"Уверенность распознавания: {draft.confidence * 100:.0f}%")
    lines.append("")
    lines.append("Записать этот замер?")
    return "\n".join(lines)


def format_weight_saved(row, *, previous=None, goal=None) -> str:
    lines = [f"⚖️ Записал: <b>{row.weight_kg:g} кг</b> ({row.measured_at:%d.%m %H:%M})"]
    composition = _composition_line(row)
    if composition:
        lines.append(composition)
    if previous is not None:
        delta = row.weight_kg - previous.weight_kg
        days = max((row.measured_at - previous.measured_at).days, 0)
        since = f" за {days} дн." if days else ""
        lines.append(f"С прошлого замера: {delta:+.1f} кг{since}")
    if goal is not None and goal.target_weight_kg:
        left = goal.target_weight_kg - row.weight_kg
        lines.append(f"До цели {goal.target_weight_kg:g} кг: {left:+.1f} кг")
    return "\n".join(lines)


def format_workout_draft(
    draft,
    *,
    estimate=None,
    started_at: datetime | None = None,
    applied: list[str] | None = None,
) -> str:
    from src.analytics.workout import INTENSITY_LABELS, SWEAT_LABELS, kind_label

    lines = [f"🏃 <b>{draft.title or kind_label(draft.kind)}</b>"]
    if started_at:
        lines.append(f"🕒 {started_at:%d.%m %H:%M}")
    if draft.duration_min:
        lines.append(f"Длительность: {draft.duration_min:.0f} мин")
    if draft.distance_m:
        lines.append(f"Расстояние: {draft.distance_m / 1000:g} км")
    if draft.steps:
        lines.append(f"Шаги: {draft.steps}")
    if draft.intensity:
        lines.append(f"Интенсивность: {INTENSITY_LABELS.get(draft.intensity, draft.intensity)}")
    if draft.avg_hr:
        lines.append(f"Средний пульс: {draft.avg_hr:.0f}")
    if draft.sweat:
        lines.append(f"Пот: {SWEAT_LABELS.get(draft.sweat, draft.sweat)}")
    if draft.note:
        lines.append(f"<i>{draft.note}</i>")
    lines.append("")
    if draft.kcal and draft.kcal_source != "estimated":
        source = "с экрана" if draft.kcal_source == "device" else "с ваших слов"
        lines.append(f"Энергозатраты: <b>{draft.kcal:.0f} ккал</b> ({source})")
    elif estimate is not None:
        tail = ", вес взят как 70 кг" if estimate.assumed_weight else ""
        lines.append(
            f"Энергозатраты: <b>≈ {estimate.kcal:.0f} ккал</b> "
            f"(MET {estimate.met:g}, {estimate.minutes:.0f} мин{tail})"
        )
        lines.append("<i>Это оценка по общепринятым коэффициентам, а не измерение.</i>")
    else:
        lines.append("Энергозатраты оценю, как только будет известна длительность.")
    lines.extend(_applied_block(applied))
    lines.append("")
    lines.append("Записать тренировку?")
    lines.append("<i>Поправить можно текстом или голосовым — «было 50 минут».</i>")
    return "\n".join(lines)


def format_workouts(rows: list[tuple[datetime, str, float | None, float | None]], *, days: int = 7) -> str:
    """`rows` — (локальное время, название, минуты, ккал)."""
    if not rows:
        return (
            "🏃 <b>Тренировки</b>\n\nПока пусто. Напишите «бегал 40 минут», наговорите "
            "голосом или пришлите фото трекера — посчитаю примерные энергозатраты."
        )
    lines = [f"🏃 <b>Тренировки за {days} дн.</b>", ""]
    total = 0.0
    for at, title, minutes, kcal in rows[-30:]:
        duration = f" · {minutes:.0f} мин" if minutes else ""
        energy = f" · ≈ {kcal:.0f} ккал" if kcal else ""
        total += kcal or 0.0
        lines.append(f"• {at:%d.%m %H:%M} {title}{duration}{energy}")
    if total:
        lines.append("")
        lines.append(f"Итого ≈ {total:.0f} ккал")
    return "\n".join(lines)


__all__ = [
    "BODY_DISCLAIMER",
    "CONFIDENCE_LABEL",
    "DISCLAIMER",
    "LAB_ADVICE_DISCLAIMER",
    "PLATE_OFF_HINT",
    "PLATE_RULE",
    "WEIGHT_PROMPT",
    "correction_examples",
    "correction_hint",
    "correction_retry",
    "describe_food_hint",
    "describe_food_retry",
    "food_examples",
    "glucose_examples",
    "glucose_hint",
    "format_glucose_after_meal",
    "glucose_prompt",
    "dish_example",
    "items_example",
    "macros_prompt",
    "macros_retry",
    "meal_edit_prompt",
    "quoted",
    "reset_examples",
    "SLEEP_PRESENCE_REMINDER",
    "format_activity",
    "format_body_card",
    "format_cgm_summary",
    "format_day_progress",
    "format_day_totals",
    "format_goal_plan",
    "format_measurement_draft",
    "format_labs",
    "format_meal_draft",
    "format_meal_kcal_progress",
    "format_meal_macros_line",
    "format_meal_saved",
    "format_med_coverage",
    "format_med_side_effects",
    "format_medication_draft",
    "format_medications",
    "format_meals_today",
    "format_feature_hint",
    "format_food_hint",
    "format_hidden_list",
    "format_lab_review",
    "format_lab_value",
    "format_plate_advice",
    "format_plate_score",
    "format_plate_settings",
    "format_product",
    "format_product_verdict",
    "format_recommendations",
    "format_remembered_label",
    "format_remembered_macros",
    "format_sleep",
    "format_sleep_short",
    "format_stats",
    "format_symptoms",
    "format_weight_saved",
    "format_workout_draft",
    "format_workouts",
    "progress_bar",
]
