"""Body metrics, weight goals and the daily energy corridor.

Pure numbers: no ORM, no aiogram, no user-facing wording (`CLAUDE.md` #7).
The dietetic bounds below are the point of this module — a goal the user types
is never used raw, it is clamped and every clamp is reported back so the person
sees why their number changed. See `spec/body.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

MALE = "m"
FEMALE = "f"

ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "high": 1.725,
    "athlete": 1.9,
}
ACTIVITY_LABELS: dict[str, str] = {
    "sedentary": "сидячий образ жизни",
    "light": "лёгкая активность, 1–3 тренировки в неделю",
    "moderate": "средняя активность, 3–5 тренировок в неделю",
    "high": "высокая активность, 6–7 тренировок в неделю",
    "athlete": "очень высокая, спорт дважды в день",
}

#: энергия одного килограмма массы тела, ккал
KCAL_PER_KG = 7700.0

#: темп изменения веса — доли массы тела в неделю
LOSS_RATE_SHARE = (0.0025, 0.01)
GAIN_RATE_SHARE = (0.00125, 0.005)
LOSS_RATE_KG = (0.1, 1.0)
GAIN_RATE_KG = (0.05, 0.5)

#: дефицит не глубже четверти суточного расхода
MAX_DEFICIT_SHARE = 0.25
#: нижний порог суточной калорийности; при неизвестном поле берём женский
MIN_KCAL = {MALE: 1500.0, FEMALE: 1200.0}
MIN_KCAL_UNKNOWN = 1200.0

#: ИМТ, ниже которого цель на снижение не строится
BMI_UNDERWEIGHT = 18.5

DEFAULT_WEIGHT_KG = 70.0


# ------------------------------------------------------------------ basics

def bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    if not weight_kg or not height_cm or height_cm <= 0:
        return None
    metres = height_cm / 100.0
    return round(weight_kg / (metres * metres), 1)


def bmi_category(value: float | None) -> str | None:
    """Классификация ВОЗ — справка, не диагноз (`spec/clinical.md`)."""
    if value is None:
        return None
    if value < 16.0:
        return "выраженный дефицит массы тела"
    if value < BMI_UNDERWEIGHT:
        return "дефицит массы тела"
    if value < 25.0:
        return "нормальный диапазон"
    if value < 30.0:
        return "избыточная масса тела"
    if value < 35.0:
        return "ожирение I степени"
    if value < 40.0:
        return "ожирение II степени"
    return "ожирение III степени"


def age_from(birth_year: int | None, *, today: date | None = None) -> int | None:
    if not birth_year:
        return None
    year = (today or date.today()).year
    age = year - birth_year
    return age if 5 <= age <= 120 else None


def lean_mass(weight_kg: float | None, body_fat_pct: float | None) -> float | None:
    if not weight_kg or body_fat_pct is None:
        return None
    if not 3.0 <= body_fat_pct <= 70.0:
        return None
    return round(weight_kg * (1.0 - body_fat_pct / 100.0), 1)


def bmr(
    weight_kg: float | None,
    *,
    height_cm: float | None = None,
    age: int | None = None,
    sex: str | None = None,
    body_fat_pct: float | None = None,
) -> float | None:
    """Basal metabolic rate, kcal/day.

    Katch-McArdle when the body-fat percentage is known — it needs no sex and
    no age, and a bioimpedance scale gives exactly that number. Otherwise
    Mifflin-St Jeor, the formula dietitians default to.
    """
    lean = lean_mass(weight_kg, body_fat_pct)
    if lean is not None:
        return round(370.0 + 21.6 * lean, 0)
    if not weight_kg or not height_cm or not age:
        return None
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    if sex == MALE:
        return round(base + 5.0, 0)
    if sex == FEMALE:
        return round(base - 161.0, 0)
    # Пол не назван — берём середину между мужской и женской поправкой.
    return round(base - 78.0, 0)


def tdee(bmr_kcal: float | None, activity: str | None) -> float | None:
    if not bmr_kcal:
        return None
    factor = ACTIVITY_FACTORS.get(activity or "", ACTIVITY_FACTORS["light"])
    return round(bmr_kcal * factor, 0)


def safe_rate_range(weight_kg: float | None, kind: str) -> tuple[float, float]:
    """Диапазон безопасного темпа, кг/неделю, по рекомендациям диетологов."""
    weight = weight_kg or DEFAULT_WEIGHT_KG
    if kind == "gain":
        share, hard = GAIN_RATE_SHARE, GAIN_RATE_KG
    else:
        share, hard = LOSS_RATE_SHARE, LOSS_RATE_KG
    low = max(hard[0], round(weight * share[0], 2))
    high = min(hard[1], round(weight * share[1], 2))
    if low > high:
        low = high
    return round(low, 2), round(high, 2)


def rate_options(weight_kg: float | None, kind: str) -> list[float]:
    """Темпы, которые можно предложить кнопками: только внутри безопасных рамок."""
    low, high = safe_rate_range(weight_kg, kind)
    ladder = [0.1, 0.125, 0.25, 0.35, 0.5, 0.75, 1.0]
    inside = [round(value, 3) for value in ladder if low - 1e-9 <= value <= high + 1e-9]
    if not inside:
        inside = [round((low + high) / 2, 2)]
    return inside


# ------------------------------------------------------------------ plan

@dataclass(slots=True)
class EnergyPlan:
    """Дневной ориентир и всё, из чего он получен."""

    kind: str
    target_kcal: float
    tdee_kcal: float | None
    bmr_kcal: float | None
    rate_kg_week: float
    delta_kcal: float                    # < 0 дефицит, > 0 профицит
    weeks: float | None = None
    eta: date | None = None
    to_goal_kg: float | None = None
    capped: list[str] = field(default_factory=list)
    estimated: bool = False              # TDEE не посчитан, взята оценка


class PlanImpossible(ValueError):
    """Цель, которую нельзя обслуживать безопасно (её и не строим)."""


def build_plan(
    *,
    kind: str,
    weight_kg: float | None,
    target_weight_kg: float | None = None,
    rate_kg_week: float | None = None,
    height_cm: float | None = None,
    age: int | None = None,
    sex: str | None = None,
    activity: str | None = None,
    body_fat_pct: float | None = None,
    pregnant: bool = False,
    today: date | None = None,
) -> EnergyPlan:
    """Собрать дневной коридор калорий под цель, урезав её до безопасной.

    Каждое срабатывание ограничителя пишется в `capped` — молча подменять
    названный человеком темп нельзя (`spec/body.md`).
    """
    today = today or date.today()
    capped: list[str] = []
    bmr_kcal = bmr(
        weight_kg, height_cm=height_cm, age=age, sex=sex, body_fat_pct=body_fat_pct
    )
    maintenance = tdee(bmr_kcal, activity)
    estimated = maintenance is None
    if maintenance is None:
        # Без роста и возраста считать нечего; берём грубый ориентир 30 ккал/кг,
        # помечаем оценкой и просим дозаполнить профиль.
        maintenance = round((weight_kg or DEFAULT_WEIGHT_KG) * 30.0, 0)

    if kind == "lose" and pregnant:
        # Дефицит при беременности небезопасен независимо от ИМТ — цель не строим
        # вовсе (`spec/clinical.md`, принцип I).
        raise PlanImpossible("pregnant")

    current_bmi = bmi(weight_kg, height_cm)
    if kind == "lose" and current_bmi is not None and current_bmi < BMI_UNDERWEIGHT:
        raise PlanImpossible("bmi_underweight")

    if kind == "maintain":
        return EnergyPlan(
            kind=kind,
            target_kcal=float(maintenance),
            tdee_kcal=None if estimated else maintenance,
            bmr_kcal=bmr_kcal,
            rate_kg_week=0.0,
            delta_kcal=0.0,
            to_goal_kg=None,
            capped=capped,
            estimated=estimated,
        )

    low, high = safe_rate_range(weight_kg, kind)
    rate = abs(rate_kg_week) if rate_kg_week else high
    if rate > high:
        capped.append(f"темп ограничен {high:g} кг/нед — быстрее диетологи менять вес не советуют")
        rate = high
    elif rate < low:
        capped.append(f"темп поднят до {low:g} кг/нед — иначе цель недостижима")
        rate = low

    delta = rate * KCAL_PER_KG / 7.0
    if kind == "lose":
        delta = -delta
        limit = maintenance * MAX_DEFICIT_SHARE
        if -delta > limit:
            delta = -limit
            capped.append(
                f"дефицит ограничен {limit:.0f} ккал — это 25 % от суточного расхода"
            )
    target = maintenance + delta
    floor = MIN_KCAL.get(sex or "", MIN_KCAL_UNKNOWN)
    if kind == "lose" and target < floor:
        target = floor
        capped.append(f"ниже {floor:.0f} ккал в день не опускаемся")
    # темп пересчитываем из фактического дефицита: он мог быть урезан
    effective_delta = target - maintenance
    rate = round(abs(effective_delta) * 7.0 / KCAL_PER_KG, 2)

    to_goal = None
    weeks = None
    eta = None
    if target_weight_kg and weight_kg:
        to_goal = round(target_weight_kg - weight_kg, 1)
        if rate > 0 and abs(to_goal) >= 0.1:
            weeks = round(abs(to_goal) / rate, 1)
            eta = today + timedelta(days=int(weeks * 7))
    return EnergyPlan(
        kind=kind,
        target_kcal=round(target, 0),
        tdee_kcal=None if estimated else maintenance,
        bmr_kcal=bmr_kcal,
        rate_kg_week=rate,
        delta_kcal=round(effective_delta, 0),
        weeks=weeks,
        eta=eta,
        to_goal_kg=to_goal,
        capped=capped,
        estimated=estimated,
    )


def goal_kind(weight_kg: float | None, target_weight_kg: float | None) -> str:
    if weight_kg is None or target_weight_kg is None:
        return "maintain"
    difference = target_weight_kg - weight_kg
    if difference <= -0.5:
        return "lose"
    if difference >= 0.5:
        return "gain"
    return "maintain"


# ------------------------------------------------------------------ the day

@dataclass(slots=True)
class DayBalance:
    target_kcal: float
    consumed_kcal: float
    burned_kcal: float
    carbs_g: float = 0.0

    @property
    def allowance_kcal(self) -> float:
        """Сколько всего можно за сутки с учётом того, что потрачено сверх нормы."""
        return self.target_kcal + self.burned_kcal

    @property
    def available_kcal(self) -> float:
        return round(self.allowance_kcal - self.consumed_kcal, 0)

    @property
    def share(self) -> float:
        allowance = self.allowance_kcal
        if allowance <= 0:
            return 0.0
        return self.consumed_kcal / allowance

    @property
    def over(self) -> bool:
        return self.consumed_kcal > self.allowance_kcal


def day_balance(
    *,
    target_kcal: float,
    consumed_kcal: float,
    burned_kcal: float = 0.0,
    carbs_g: float = 0.0,
) -> DayBalance:
    return DayBalance(
        target_kcal=round(target_kcal, 0),
        consumed_kcal=round(consumed_kcal, 0),
        burned_kcal=round(burned_kcal, 0),
        carbs_g=round(carbs_g, 0),
    )


def merge_burn(
    workouts: list[tuple[datetime, datetime | None, float | None]],
    samples: list[tuple[datetime, datetime | None, float | None]],
) -> float:
    """Сумма ккал за период без двойного счёта.

    Одна и та же пробежка приходит и руками, и с телефона; пересекающаяся по
    времени запись из `activity_samples` отбрасывается в пользу ручной —
    её пользователь подтвердил сам (`spec/workout.md`).
    """
    total = sum(kcal or 0.0 for _, _, kcal in workouts)
    for start, end, kcal in samples:
        if not kcal:
            continue
        finish = end or start
        overlaps = any(
            start <= (w_end or w_start) and w_start <= finish
            for w_start, w_end, _ in workouts
        )
        if not overlaps:
            total += kcal
    return round(total, 0)


# ------------------------------------------------------------------ trend

@dataclass(slots=True)
class WeightTrend:
    first_at: datetime
    last_at: datetime
    first_kg: float
    last_kg: float
    n: int

    @property
    def change_kg(self) -> float:
        return round(self.last_kg - self.first_kg, 1)

    @property
    def days(self) -> float:
        return max((self.last_at - self.first_at).total_seconds() / 86400.0, 0.0)

    @property
    def rate_kg_week(self) -> float | None:
        if self.days < 3:
            return None
        return round(self.change_kg * 7.0 / self.days, 2)


def weight_trend(
    series: list[tuple[datetime, float]], *, window_days: int = 30
) -> WeightTrend | None:
    """Тренд по замерам за окно; None, если замер один или их нет."""
    if not series:
        return None
    ordered = sorted(series, key=lambda item: item[0])
    last_at = ordered[-1][0]
    inside = [item for item in ordered if (last_at - item[0]).days <= window_days]
    if len(inside) < 2:
        return None
    return WeightTrend(
        first_at=inside[0][0],
        last_at=inside[-1][0],
        first_kg=inside[0][1],
        last_kg=inside[-1][1],
        n=len(inside),
    )


def safe_corridor(
    start_at: datetime, start_kg: float, *, rate_kg_week: float, kind: str, weeks: int = 26
) -> list[tuple[datetime, float]]:
    """Линия ожидаемого веса при выбранном темпе — для графика."""
    sign = -1.0 if kind == "lose" else (1.0 if kind == "gain" else 0.0)
    return [
        (start_at + timedelta(weeks=step), round(start_kg + sign * rate_kg_week * step, 2))
        for step in range(weeks + 1)
    ]


__all__ = [
    "ACTIVITY_FACTORS",
    "ACTIVITY_LABELS",
    "DEFAULT_WEIGHT_KG",
    "FEMALE",
    "KCAL_PER_KG",
    "MALE",
    "MIN_KCAL",
    "DayBalance",
    "EnergyPlan",
    "PlanImpossible",
    "WeightTrend",
    "age_from",
    "bmi",
    "bmi_category",
    "bmr",
    "build_plan",
    "day_balance",
    "goal_kind",
    "lean_mass",
    "merge_burn",
    "rate_options",
    "safe_corridor",
    "safe_rate_range",
    "tdee",
    "weight_trend",
]
