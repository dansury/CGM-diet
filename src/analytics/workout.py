"""Workouts: a closed catalogue of activity kinds and a MET energy estimate.

Pure numbers, like the rest of `src/analytics` — no ORM, no aiogram, no
wording. The estimate is deliberately a *model*, never a measurement: every
number that leaves this module is meant to be printed with a «≈»
(`spec/workout.md`, `spec/clinical.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INTENSITIES = ("low", "moderate", "high")
INTENSITY_LABELS = {
    "low": "лёгкая",
    "moderate": "средняя",
    "high": "высокая",
}
SWEAT_LABELS = {"yes": "вспотел(а)", "light": "слегка", "no": "нет"}

#: slug -> (метка, {интенсивность: MET}, синонимы для распознавания)
KINDS: dict[str, tuple[str, dict[str, float], tuple[str, ...]]] = {
    "walking": ("ходьба", {"low": 2.8, "moderate": 3.8, "high": 5.0},
                ("ходьб", "гуля", "прогул", "шаг", "пешк", "walk")),
    "running": ("бег", {"low": 6.0, "moderate": 9.8, "high": 12.5},
                ("бег", "бега", "пробеж", "run", "джогг")),
    "cycling": ("велосипед", {"low": 4.0, "moderate": 8.0, "high": 12.0},
                ("вел", "байк", "cycl", "bike", "катал")),
    "swimming": ("плавание", {"low": 5.0, "moderate": 7.0, "high": 10.0},
                 ("плав", "бассейн", "swim")),
    "strength": ("силовая", {"low": 3.5, "moderate": 5.0, "high": 6.0},
                 ("силов", "штанг", "гантел", "качал", "зал", "тренаж", "жим", "присед")),
    "hiit": ("интервальная", {"low": 6.0, "moderate": 8.0, "high": 10.0},
             ("интервал", "hiit", "табат", "кроссфит", "crossfit")),
    "elliptical": ("эллипс", {"low": 4.6, "moderate": 5.5, "high": 7.0},
                   ("эллипс", "орбитрек")),
    "rowing": ("гребля", {"low": 4.8, "moderate": 7.0, "high": 8.5},
               ("гребл", "гребн", "row")),
    "yoga": ("йога", {"low": 2.3, "moderate": 3.0, "high": 4.0},
             ("йог", "пилат", "yoga")),
    "stretching": ("растяжка", {"low": 2.0, "moderate": 2.3, "high": 2.8},
                   ("растяж", "стретч", "разминк")),
    "dance": ("танцы", {"low": 3.5, "moderate": 5.0, "high": 7.8},
              ("танц", "dance", "зумб")),
    "football": ("футбол", {"low": 5.0, "moderate": 7.0, "high": 10.0},
                 ("футбол", "мяч гон")),
    "basketball": ("баскетбол", {"low": 4.5, "moderate": 6.5, "high": 8.0},
                   ("баскет",)),
    "tennis": ("теннис", {"low": 5.0, "moderate": 7.3, "high": 8.0},
               ("теннис", "падел", "бадминтон", "сквош")),
    "boxing": ("бокс", {"low": 5.5, "moderate": 7.8, "high": 12.8},
               ("бокс", "единоборств", "карате", "борьб", "мма")),
    "skiing": ("лыжи", {"low": 4.3, "moderate": 7.0, "high": 9.0},
               ("лыж", "ski", "сноуборд")),
    "skating": ("коньки", {"low": 4.0, "moderate": 7.0, "high": 9.0},
                ("коньк", "ролик", "skate")),
    "stairs": ("лестница", {"low": 4.0, "moderate": 6.0, "high": 8.8},
               ("лестниц", "ступен", "этаж")),
    "housework": ("работа по дому", {"low": 2.3, "moderate": 3.3, "high": 4.5},
                  ("убор", "огород", "дач", "грядк", "копал", "сад")),
    "other": ("тренировка", {"low": 3.0, "moderate": 5.0, "high": 7.0},
              ("трениров", "занят", "workout", "фитнес", "физкультур")),
}

#: MET по скорости — бег 8 км/ч и 14 км/ч это разные тренировки
SPEED_METS: dict[str, tuple[tuple[float, float], ...]] = {
    "walking": ((3.2, 2.8), (4.8, 3.5), (5.6, 4.3), (6.4, 5.0), (99.0, 6.3)),
    "running": ((8.0, 8.3), (9.7, 9.8), (11.3, 11.0), (12.9, 12.8), (99.0, 14.5)),
    "cycling": ((16.0, 4.0), (19.0, 6.8), (22.0, 8.0), (26.0, 10.0), (99.0, 12.0)),
}

#: ккал на шаг на килограмм массы — используется, когда времени ходьбы нет
KCAL_PER_STEP_PER_KG = 0.00053

DEFAULT_WEIGHT_KG = 70.0
DEFAULT_INTENSITY = "moderate"

_HR_LOW = 0.64      # доля от максимального пульса
_HR_MODERATE = 0.77


@dataclass(slots=True)
class Estimate:
    kcal: float
    met: float
    weight_kg: float
    minutes: float
    assumed_weight: bool = False
    basis: str = "met"          # met|speed|hr|steps


def kind_label(kind: str) -> str:
    entry = KINDS.get(kind)
    return entry[0] if entry else KINDS["other"][0]


def resolve_kind(text: str | None) -> str:
    """Свести свободное название к слагу из закрытого словаря."""
    lowered = (text or "").strip().lower().replace("ё", "е")
    if not lowered:
        return "other"
    if lowered in KINDS:
        return lowered
    for slug, (_label, _mets, synonyms) in KINDS.items():
        if slug == "other":
            continue
        if any(token in lowered for token in synonyms):
            return slug
    return "other"


def resolve_intensity(
    *,
    stated: str | None = None,
    rpe: int | None = None,
    sweat: str | None = None,
    avg_hr: float | None = None,
    age: int | None = None,
) -> tuple[str, str]:
    """Интенсивность и то, откуда она взялась.

    Приоритет: пульс > названная интенсивность > RPE > пот. Пульс объективен,
    пот — самый слабый признак, поэтому он решает, только когда больше нечему.
    """
    if avg_hr and age:
        maximum = 220.0 - age
        if maximum > 0:
            share = avg_hr / maximum
            if share < _HR_LOW:
                return "low", "пульс"
            if share < _HR_MODERATE:
                return "moderate", "пульс"
            return "high", "пульс"
    if stated in INTENSITIES:
        return stated, "ваша оценка"
    if rpe:
        if rpe <= 3:
            return "low", "RPE"
        if rpe <= 6:
            return "moderate", "RPE"
        return "high", "RPE"
    if sweat == "yes":
        return "high", "вспотели"
    if sweat == "light":
        return "moderate", "слегка вспотели"
    if sweat == "no":
        return "low", "не вспотели"
    return DEFAULT_INTENSITY, "по умолчанию"


def met_for(kind: str, intensity: str, *, kmh: float | None = None) -> float:
    table = KINDS.get(kind, KINDS["other"])[1]
    if kmh and kind in SPEED_METS:
        for limit, met in SPEED_METS[kind]:
            if kmh <= limit:
                return met
    return table.get(intensity, table[DEFAULT_INTENSITY])


def speed_kmh(distance_m: float | None, minutes: float | None) -> float | None:
    if not distance_m or not minutes or minutes <= 0:
        return None
    return round(distance_m / 1000.0 / (minutes / 60.0), 1)


def kcal_estimate(
    *,
    kind: str,
    intensity: str,
    minutes: float | None,
    weight_kg: float | None = None,
    distance_m: float | None = None,
    avg_hr: float | None = None,
    age: int | None = None,
) -> Estimate | None:
    """`kcal = MET · 3.5 · вес / 200 · минуты` — стандартная MET-формула."""
    if not minutes or minutes <= 0:
        return None
    weight = weight_kg or DEFAULT_WEIGHT_KG
    kmh = speed_kmh(distance_m, minutes)
    met = met_for(kind, intensity, kmh=kmh)
    basis = "speed" if kmh and kind in SPEED_METS else "met"
    if avg_hr and age:
        basis = "hr"
    kcal = met * 3.5 * weight / 200.0 * minutes
    return Estimate(
        kcal=round(kcal, 0),
        met=met,
        weight_kg=weight,
        minutes=round(minutes, 0),
        assumed_weight=weight_kg is None,
        basis=basis,
    )


def kcal_from_steps(steps: int | None, weight_kg: float | None = None) -> float | None:
    """Ходьба, о которой известно только число шагов."""
    if not steps or steps <= 0:
        return None
    return round(steps * (weight_kg or DEFAULT_WEIGHT_KG) * KCAL_PER_STEP_PER_KG, 0)


def minutes_from_steps(steps: int | None) -> float | None:
    """~110 шагов в минуту — обычный прогулочный темп."""
    if not steps or steps <= 0:
        return None
    return round(steps / 110.0, 0)


_ACTIVITY_HINT = re.compile(
    r"(трениров|бегал|бежал|пробеж|побегал|ходил|гулял|прогул|шаг\w*|плавал|бассейн|"
    r"вел\w*|качал|силов|зал\b|йог|растяж|танцевал|футбол|теннис|бокс|лыж|коньк|"
    r"эллипс|гребл|кроссфит|интервал|поход|walk|run|workout)",
    re.IGNORECASE,
)
_MEASURE_HINT = re.compile(
    r"(\d+\s*(?:мин\w*|час\w*|ч\b|км|м\b|шаг\w*|раз)|\d{3,}|\bполчаса\b|\bчас\w*\b)",
    re.IGNORECASE,
)


def looks_like_workout(text: str | None) -> bool:
    """Похоже ли сообщение на отчёт о тренировке — до всякой модели.

    Нужны оба признака: слово про движение и число (время, расстояние, шаги).
    Одного «зал» мало — так «салат в зале ожидания» не станет тренировкой.
    """
    if not text:
        return False
    lowered = text.lower().replace("ё", "е")
    return bool(_ACTIVITY_HINT.search(lowered) and _MEASURE_HINT.search(lowered))


QUESTION_DURATION = "duration"
QUESTION_INTENSITY = "intensity"
QUESTION_SWEAT = "sweat"


def missing_questions(
    *,
    duration_min: float | None,
    intensity: str | None,
    sweat: str | None,
    avg_hr: float | None = None,
    steps: int | None = None,
) -> list[str]:
    """Чего не хватает, чтобы оценка была осмысленной, в порядке спрашивания.

    Без длительности считать нечего вовсе; интенсивность и пот уточняют MET.
    Пульс отвечает на оба вопроса сразу, поэтому при нём ничего не спрашиваем.
    """
    questions: list[str] = []
    if not duration_min and not steps:
        questions.append(QUESTION_DURATION)
    if avg_hr:
        return questions
    if intensity not in INTENSITIES:
        questions.append(QUESTION_INTENSITY)
        if sweat is None:
            questions.append(QUESTION_SWEAT)
    return questions


_DURATION_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d)?)\s*(час\w*|ч\b|мин\w*|м\b|h\b|min)", re.IGNORECASE
)


def parse_duration(text: str | None) -> float | None:
    """«40 минут», «1.5 часа», «90» → минуты."""
    if not text:
        return None
    total = 0.0
    for value, unit in _DURATION_RE.findall(text):
        number = float(value.replace(",", "."))
        total += number * 60.0 if unit.lower().startswith(("час", "ч", "h")) else number
    if total:
        return round(total, 0)
    bare = re.fullmatch(r"\s*(\d{1,3})\s*", text)
    if bare:
        return float(bare.group(1))
    lowered = text.lower()
    if "полчаса" in lowered:
        return 30.0
    if "полтора час" in lowered:
        return 90.0
    if re.search(r"\bчас\b", lowered):
        return 60.0
    return None


__all__ = [
    "DEFAULT_INTENSITY",
    "DEFAULT_WEIGHT_KG",
    "INTENSITIES",
    "INTENSITY_LABELS",
    "KINDS",
    "QUESTION_DURATION",
    "QUESTION_INTENSITY",
    "QUESTION_SWEAT",
    "SWEAT_LABELS",
    "Estimate",
    "kcal_estimate",
    "kcal_from_steps",
    "kind_label",
    "looks_like_workout",
    "met_for",
    "minutes_from_steps",
    "missing_questions",
    "parse_duration",
    "resolve_intensity",
    "resolve_kind",
    "speed_kmh",
]
