"""User-facing text. All wording rules live here, in one place.

Two hard rules (`spec/clinical.md`):

1. **No prescriptions.** Никаких доз, препаратов, инсулина, диагнозов. Питание —
   можно, и только со ссылкой на собственные данные пользователя.
2. **No causal claims.** «Наблюдается связь», «средний подъём», «после этих
   блюд» — но никогда «продукт X повышает сахар».
"""

from __future__ import annotations

from datetime import datetime

from src.analytics.activity import ActivityContrast
from src.analytics.cgm_metrics import CGMSummary
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


# ------------------------------------------------------------------ meals

CORRECTION_HINT = (
    "Скорректировать можно текстом или голосовым: «убери салат», "
    "«гречки было 250», «вместо курицы индейка»."
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
) -> str:
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
    lines.append(f"<i>{CORRECTION_HINT}</i>")
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


def format_day_progress(balance, *, goal=None, trend=None) -> str:
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
    if trend is not None and trend.rate_kg_week is not None:
        lines.append(
            f"Вес за {trend.days:.0f} дн.: {trend.first_kg:g} → {trend.last_kg:g} кг "
            f"({trend.change_kg:+g} кг, {trend.rate_kg_week:+g} кг/нед)"
        )
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
        if details:
            lines.append("Профиль: " + ", ".join(details))
        lines.append(f"Активность: {ACTIVITY_LABELS.get(profile.activity, profile.activity)}")
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
    "CORRECTION_HINT",
    "DISCLAIMER",
    "WEIGHT_PROMPT",
    "format_activity",
    "format_body_card",
    "format_cgm_summary",
    "format_day_progress",
    "format_goal_plan",
    "format_measurement_draft",
    "format_labs",
    "format_meal_draft",
    "format_med_coverage",
    "format_med_side_effects",
    "format_medication_draft",
    "format_medications",
    "format_product",
    "format_product_verdict",
    "format_recommendations",
    "format_remembered_label",
    "format_remembered_macros",
    "format_stats",
    "format_symptoms",
    "format_weight_saved",
    "format_workout_draft",
    "format_workouts",
    "progress_bar",
]
