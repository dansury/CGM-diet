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
        portion = f" — {about}{item.portion_g:.0f} г" if item.portion_g else ""
        carbs = f", угл {item.carbs_g:.0f} г" if item.carbs_g is not None else ""
        lines.append(f"• {item.name}{portion}{carbs}")
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


__all__ = [
    "CONFIDENCE_LABEL",
    "CORRECTION_HINT",
    "DISCLAIMER",
    "format_activity",
    "format_cgm_summary",
    "format_labs",
    "format_meal_draft",
    "format_med_coverage",
    "format_med_side_effects",
    "format_medication_draft",
    "format_medications",
    "format_product",
    "format_product_verdict",
    "format_recommendations",
    "format_stats",
    "format_symptoms",
]
