"""Catalogue of user-facing features and the rules for talking about them.

A bot that can do fifteen things teaches none of them by existing. Once a week —
never in the first week, and never during the first-run questionnaire — the user
hears about exactly one feature they have never used, chosen from the goals they
named at the start, with two buttons. «Отлично» means «noted», «Не нужно» means the
feature is hidden from the menu and never mentioned again; it stays reachable
through `/hidden`.

Two messages per feature is the whole budget. See `spec/features.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

#: сколько раз всего можно рассказать об одной возможности
MAX_HINTS = 2
#: не чаще раза в неделю
HINT_PERIOD_DAYS = 7

STATUS_NEW = "new"
STATUS_SHOWN = "shown"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class Feature:
    key: str
    title: str
    blurb: str
    command: str | None = None
    menu_button: str | None = None
    #: чем считать «пользователь этим пользовался» — ключ `repo.counts`
    counter: str | None = None


@dataclass(frozen=True, slots=True)
class FeatureState:
    status: str = STATUS_NEW
    shown: int = 0
    used: bool = False


FEATURES: tuple[Feature, ...] = (
    Feature(
        key="plate",
        title="Гарвардская тарелка",
        blurb=(
            "После каждого фото еды показываю, как приём пищи ложится в тарелку: "
            "половина — овощи и фрукты, четверть — цельные злаки, четверть — белок. "
            "И подсказываю, чего добрать сегодня."
        ),
        command="/plate",
        counter="plate",
    ),
    Feature(
        key="labs",
        title="Анализы крови",
        blurb=(
            "Пришлите фото, PDF или текст анализа — сохраню маркеры с референсами "
            "из самого документа, отмечу, что вне нормы, и покажу продукты-источники."
        ),
        command="/labs",
        counter="labs",
    ),
    Feature(
        key="workout",
        title="Тренировки",
        blurb=(
            "«бегал 40 минут», голосовое, фото трекера или дневника — уточню "
            "длительность и интенсивность и оценю энергозатраты."
        ),
        command="/workout",
        menu_button="🏃 Тренировка",
        counter="workouts",
    ),
    Feature(
        key="body",
        title="Вес, состав тела и цель",
        blurb=(
            "Рост, возраст, биоимпеданс и целевой вес — покажу дневной коридор "
            "калорий с безопасным темпом и полосу прогресса после каждой еды."
        ),
        command="/body",
        menu_button="⚖️ Вес и цель",
        counter="weights",
    ),
    Feature(
        key="wellbeing",
        title="Самочувствие",
        blurb=(
            "Оценка 1–5 и симптомы кнопками. Потом сопоставлю отметки с сахаром "
            "в эти же минуты."
        ),
        command="/wellbeing",
        menu_button="🙂 Самочувствие",
        counter="checkins",
    ),
    Feature(
        key="meds",
        title="Лекарства",
        blurb=(
            "Фото упаковки — и препарат в журнале. Плюс справка из открытой базы "
            "побочных эффектов: совпадение по времени, не причина."
        ),
        command="/meds",
        menu_button="💊 Лекарства",
        counter="medications",
    ),
    Feature(
        key="check",
        title="Проверка продукта перед покупкой",
        blurb=(
            "Фото упаковки в режиме «🛒 Проверить продукт» — отвечу по вашей "
            "собственной статистике и ничего не запишу как съеденное."
        ),
        command="/check",
        menu_button="🛒 Проверить продукт",
        counter="products",
    ),
    Feature(
        key="dictionary",
        title="Личный словарь",
        blurb="Всё, что вы записываете повторно, становится кнопкой в одно нажатие.",
        command="/my",
        menu_button="⭐️ Мой словарь",
        counter="dictionary",
    ),
    Feature(
        key="graph",
        title="График",
        blurb="Таймлайн еды и сахара, самочувствие и рейтинг компонентов картинкой.",
        command="/graph",
        menu_button="📈 График",
    ),
    Feature(
        key="stats",
        title="Статистика по компонентам",
        blurb=(
            "После каких компонентов сахар поднимается выше — с числом наблюдений "
            "и достоверностью."
        ),
        command="/stats",
        menu_button="📊 Статистика",
    ),
    Feature(
        key="health",
        title="Samsung Health",
        blurb="Шаги, сон и тренировки приезжают сами — сравню приёмы пищи с прогулкой и без.",
        command="/health",
        counter="activity",
    ),
    Feature(
        key="sleep",
        title="Сон",
        blurb=(
            "Сколько спите и насколько ровный режим — и что бывает в дни после "
            "коротких ночей: калории и сахар рядом."
        ),
        command="/sleep",
        counter="presence",
    ),
    Feature(
        key="export",
        title="Выгрузка данных",
        blurb="ZIP с CSV по всем таблицам — данные ваши и уезжают целиком.",
        command="/export",
    ),
)

BY_KEY: dict[str, Feature] = {feature.key: feature for feature in FEATURES}
#: кнопка меню → возможность, чтобы отказ убирал кнопку
BUTTON_FEATURE: dict[str, str] = {
    feature.menu_button: feature.key for feature in FEATURES if feature.menu_button
}


def get(key: str) -> Feature | None:
    return BY_KEY.get(key)


def is_used(feature: Feature, counts: dict[str, int], state: FeatureState) -> bool:
    """Пользовался ли человек этим: строка в БД или явная отметка обращения."""
    if state.used:
        return True
    if feature.counter:
        return counts.get(feature.counter, 0) > 0
    return False


def hidden_keys(states: dict[str, FeatureState]) -> set[str]:
    return {key for key, state in states.items() if state.status == STATUS_DECLINED}


def pick_hint(
    counts: dict[str, int],
    states: dict[str, FeatureState],
    *,
    priority: tuple[str, ...] | list[str] = (),
) -> Feature | None:
    """Одна возможность, о которой уместно рассказать сейчас.

    Первыми идут возможности, которые служат названным при знакомстве целям
    (`src/goals.py` → `feature_order`), дальше — порядок каталога. Пропускаем
    всё, чем уже пользовались, от чего отказались, что уже приняли и о чём
    рассказали `MAX_HINTS` раз.
    """
    ordered = [BY_KEY[key] for key in priority if key in BY_KEY]
    ordered += [feature for feature in FEATURES if feature.key not in set(priority)]
    for feature in ordered:
        state = states.get(feature.key, FeatureState())
        if state.status in {STATUS_DECLINED, STATUS_ACCEPTED}:
            continue
        if state.shown >= MAX_HINTS:
            continue
        if is_used(feature, counts, state):
            continue
        return feature
    return None


__all__ = [
    "BUTTON_FEATURE",
    "BY_KEY",
    "FEATURES",
    "HINT_PERIOD_DAYS",
    "MAX_HINTS",
    "STATUS_ACCEPTED",
    "STATUS_DECLINED",
    "STATUS_NEW",
    "STATUS_SHOWN",
    "Feature",
    "FeatureState",
    "get",
    "hidden_keys",
    "is_used",
    "pick_hint",
]
