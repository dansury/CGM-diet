"""Turn raw user input into drafts. See `spec/ingest.md`.

Every function here is *pure I/O + parsing*: it calls the model, validates the
JSON into a dataclass and returns it. Persistence and confirmation live in the
handlers, so a failed recognition never leaves half a row behind.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from src.analytics import workout as workout_kinds
from src.analytics.tags import normalize_tags
from src.ingest.nutrition import fill_meal
from src.ingest.units import canonical_unit, guess_unit, is_plausible, to_mmol
from src.llm import ChatMessage, ImagePart, LLMClient, extract_json, get_client, model_selection
from src.llm.base import LLMError
from src.logging_setup import get_logger
from src.vision import prompts
from src.vision.schemas import (
    GlucoseDraft,
    ItemDraft,
    LabDraft,
    MarkerDraft,
    MealDraft,
    MeasurementDraft,
    MedicationDraft,
    ProductDraft,
    WorkoutDraft,
)

log = get_logger("vision.recognize")


class RecognitionError(RuntimeError):
    """The model answered, but not with anything usable."""


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return out


async def _ask_json(
    client: LLMClient,
    *,
    prompt: str,
    images: list[ImagePart] | None = None,
    max_tokens: int = 1400,
) -> Any:
    # `current()` reads a process cache filled at startup — the recognition
    # layer never touches the DB (`CLAUDE.md` #7).
    try:
        if images:
            completion = await client.vision(
                images,
                prompt,
                system=prompts.SYSTEM,
                model=model_selection.current("vision"),
                max_tokens=max_tokens,
            )
        else:
            completion = await client.chat(
                [ChatMessage("system", prompts.SYSTEM), ChatMessage("user", prompt)],
                model=model_selection.current("text"),
                max_tokens=max_tokens,
            )
    except LLMError as exc:
        raise RecognitionError(str(exc)) from exc
    try:
        return extract_json(completion.text)
    except ValueError as exc:
        log.warning("unparseable model output: %s", completion.text[:200])
        raise RecognitionError("модель вернула неразборчивый ответ") from exc


# ---------------------------------------------------------------- routing

PHOTO_KINDS = (
    "food",
    "glucose_screen",
    "food_label",
    "lab_report",
    "medication",
    "body_scale",
    "workout",
    "other",
)


async def classify_photo(
    images: list[ImagePart], *, client: LLMClient | None = None
) -> tuple[str, float]:
    """Cheap first pass: what did the user just send?

    Auto-routing keeps the common case (фото тарелки) to a single tap, and the
    handler still offers “это не еда” buttons when the guess is wrong.
    Falls back to `other` on any failure so the handler asks instead of guessing.
    """
    client = client or get_client()
    try:
        payload = await _ask_json(client, prompt=prompts.CLASSIFY, images=images, max_tokens=120)
    except RecognitionError:
        return "other", 0.0
    if not isinstance(payload, dict):
        return "other", 0.0
    kind = str(payload.get("kind") or "other").strip().lower()
    if kind not in PHOTO_KINDS:
        kind = "other"
    return kind, _f(payload.get("confidence")) or 0.0


# ---------------------------------------------------------------- meals

def _meal_from_payload(payload: dict, *, source: str, raw_text: str | None = None) -> MealDraft:
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    items: list[ItemDraft] = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        items.append(
            ItemDraft(
                name=name,
                portion_g=_f(raw.get("portion_g")),
                kcal=_f(raw.get("kcal")),
                protein_g=_f(raw.get("protein_g")),
                fat_g=_f(raw.get("fat_g")),
                carbs_g=_f(raw.get("carbs_g")),
                fiber_g=_f(raw.get("fiber_g")),
                tags=normalize_tags(raw.get("tags"), name=name),
            )
        )
    draft = MealDraft(
        title=str(payload.get("title") or "").strip(),
        items=items,
        confidence=_f(payload.get("confidence")),
        notes=str(payload.get("notes") or "").strip(),
        source=source,
        raw_text=raw_text,
    )
    # The model regularly returns a name with every macro `null`; without this
    # the card would print «0 ккал · Б 0 · Ж 0 · У 0» as if it were measured.
    fill_meal(draft)
    return draft


async def recognize_meal_photo(
    images: list[ImagePart], *, hint: str = "", client: LLMClient | None = None
) -> MealDraft:
    client = client or get_client()
    prompt = prompts.FOOD_PHOTO + (f"\nПодсказка пользователя: {hint}\n" if hint else "")
    payload = await _ask_json(client, prompt=prompt, images=images)
    draft = _meal_from_payload(payload, source="photo", raw_text=hint or None)
    if not draft.items:
        raise RecognitionError("на фото не удалось распознать еду")
    return draft


async def parse_meal_text(text: str, *, client: LLMClient | None = None) -> MealDraft:
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.TEXT_MEAL + text)
    draft = _meal_from_payload(payload, source="text", raw_text=text)
    if not draft.items:
        raise RecognitionError("не удалось разобрать описание еды")
    return draft


# ---------------------------------------------------------------- labels

async def recognize_label(
    images: list[ImagePart], *, client: LLMClient | None = None
) -> ProductDraft:
    """Read a package. Two photos (front + back) are merged into one card."""
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.LABEL_PHOTO, images=images)
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    per100 = payload.get("per_100") or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise RecognitionError("не удалось прочитать название продукта")
    barcode = str(payload.get("barcode") or "").strip() or None
    if barcode and not barcode.isdigit():
        barcode = None
    return ProductDraft(
        name=name,
        brand=(str(payload.get("brand") or "").strip() or None),
        barcode=barcode,
        kcal_100=_f(per100.get("kcal")),
        protein_100=_f(per100.get("protein_g")),
        fat_100=_f(per100.get("fat_g")),
        carbs_100=_f(per100.get("carbs_g")),
        sugars_100=_f(per100.get("sugars_g")),
        fiber_100=_f(per100.get("fiber_g")),
        ingredients=[str(i).strip() for i in (payload.get("ingredients") or []) if str(i).strip()],
        additives=[str(i).strip() for i in (payload.get("additives") or []) if str(i).strip()],
        flags=normalize_tags(payload.get("flags"), name=name),
        confidence=_f(payload.get("confidence")),
    )


# ---------------------------------------------------------------- glucose

def _resolve_stamp(raw: Any, fallback_time: Any, *, now: datetime) -> datetime | None:
    """Build a local datetime from whatever the model managed to read."""
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=now.tzinfo)
            except ValueError:
                continue
    if isinstance(fallback_time, str) and fallback_time.strip():
        try:
            hh, mm = (int(p) for p in fallback_time.strip().split(":")[:2])
        except ValueError:
            return None
        # A screen showing only "HH:MM" means today, unless that is in the
        # future — then it is yesterday's reading.
        stamp = datetime.combine(now.date(), time(hh, mm)).replace(tzinfo=now.tzinfo)
        if stamp - now > timedelta(minutes=10):
            stamp -= timedelta(days=1)
        return stamp
    return None


async def recognize_glucose_screenshot(
    images: list[ImagePart], *, now: datetime, client: LLMClient | None = None
) -> tuple[list[GlucoseDraft], str | None]:
    """Return the readings plus the device name the screen advertised."""
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.GLUCOSE_SCREENSHOT, images=images)
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    unit = canonical_unit(payload.get("unit"))
    device = (str(payload.get("device") or "").strip() or None)
    drafts: list[GlucoseDraft] = []
    for raw in payload.get("readings") or []:
        if not isinstance(raw, dict):
            continue
        value = _f(raw.get("value"))
        if value is None:
            continue
        effective_unit = unit if is_plausible(value, unit) else guess_unit(value)
        if not is_plausible(value, effective_unit):
            continue
        stamp = _resolve_stamp(raw.get("measured_at"), raw.get("time"), now=now)
        if stamp is None:
            stamp = now
        drafts.append(
            GlucoseDraft(
                measured_at=stamp,
                value_mmol=to_mmol(value, effective_unit),
                unit_input=effective_unit,
                trend=(str(raw.get("trend") or "").strip() or None),
                device=device,
            )
        )
    if not drafts:
        raise RecognitionError("не удалось прочитать значения глюкозы")
    drafts.sort(key=lambda d: d.measured_at)
    return drafts, device


# ---------------------------------------------------------------- labs

async def recognize_labs(
    *,
    images: list[ImagePart] | None = None,
    text: str | None = None,
    now: datetime,
    client: LLMClient | None = None,
) -> LabDraft:
    client = client or get_client()
    prompt = prompts.LAB_REPORT + (f"\nТекст документа:\n{text}\n" if text else "")
    payload = await _ask_json(client, prompt=prompt, images=images, max_tokens=2000)
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    taken_at: datetime | None = None
    raw_date = str(payload.get("taken_at") or "").strip()
    if raw_date:
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                parsed: date = datetime.strptime(raw_date, fmt).date()
            except ValueError:
                continue
            taken_at = datetime.combine(parsed, time(12, 0)).replace(tzinfo=now.tzinfo)
            break
    markers: list[MarkerDraft] = []
    for raw in payload.get("markers") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("marker") or "").strip()
        if not name:
            continue
        markers.append(
            MarkerDraft(
                marker=name,
                value=_f(raw.get("value")),
                value_text=(str(raw.get("value_text") or "").strip() or None),
                unit=(str(raw.get("unit") or "").strip() or None),
                ref_low=_f(raw.get("ref_low")),
                ref_high=_f(raw.get("ref_high")),
            )
        )
    if not markers:
        raise RecognitionError("в документе не найдено показателей")
    return LabDraft(
        panel=(str(payload.get("panel") or "").strip() or None),
        taken_at=taken_at or now,
        markers=markers,
        confidence=_f(payload.get("confidence")),
    )


# ---------------------------------------------------------------- wellbeing

async def correct_meal(
    draft: MealDraft,
    instruction: str = "",
    *,
    images: list[ImagePart] | None = None,
    client: LLMClient | None = None,
) -> MealDraft:
    """Model-side correction: the draft goes in, a corrected draft comes out.

    Reached for wording the deterministic pass in `ingest/correction.py` could
    not place, or when the correction itself is a photo (an angle the first
    shot missed, an extra dish) rather than words — `images` carries that photo
    straight to the model alongside the current draft (`spec/ingest.md` §
    Корректировки). Either way the model edits the existing items instead of
    re-recognising the meal from scratch.
    """
    import json

    from src.vision.schemas import meal_to_dict

    client = client or get_client()
    prompt = prompts.MEAL_CORRECTION + "\nТекущий приём пищи:\n" + json.dumps(
        meal_to_dict(draft), ensure_ascii=False
    )
    if instruction:
        prompt += "\nУточнение пользователя:\n" + instruction
    if images:
        prompt += "\nПользователь прислал фото — используйте его, чтобы дополнить или исправить приём пищи."
    payload = await _ask_json(client, prompt=prompt, images=images)
    if not isinstance(payload, dict) or not payload.get("items"):
        raise RecognitionError("не понял правку")
    corrected = _meal_from_payload(payload, source=draft.source, raw_text=instruction or None)
    corrected.confidence = 1.0
    corrected.notes = "учтена ваша правка"
    return corrected


# ---------------------------------------------------------------- medications

async def recognize_medication(
    images: list[ImagePart], *, client: LLMClient | None = None
) -> MedicationDraft:
    """Read the package: name, substance, dose as printed. Nothing else.

    Deliberately narrow — the model is never asked what the drug is *for* or
    how to take it (`spec/meds.md`, constitution I).
    """
    client = client or get_client()
    payload = await _ask_json(
        client, prompt=prompts.MEDICATION_PHOTO, images=images, max_tokens=400
    )
    if not isinstance(payload, dict):
        raise RecognitionError("не разобрал упаковку")
    name = str(payload.get("name") or "").strip()
    inn = str(payload.get("inn") or "").strip()
    if not name and not inn:
        raise RecognitionError("не вижу названия препарата")
    return MedicationDraft(
        name=name or inn,
        inn=inn or None,
        dose_text=(str(payload.get("dose_text") or "").strip() or None),
        form=(str(payload.get("form") or "").strip() or None),
        confidence=_f(payload.get("confidence")),
    )


# ---------------------------------------------------------------- workouts

_WORKOUT_INTENSITIES = ("low", "moderate", "high")
_SWEAT = ("yes", "light", "no")


def _workout_from_payload(
    payload: Any, *, source: str, now: datetime, raw_text: str | None = None
) -> WorkoutDraft:
    """Numbers only: the kcal estimate is the calculator's job, not the model's."""
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    title = str(payload.get("title") or "").strip()
    kind = workout_kinds.resolve_kind(str(payload.get("kind") or "") or title or raw_text or "")
    intensity = str(payload.get("intensity") or "").strip().lower()
    sweat = str(payload.get("sweat") or "").strip().lower()
    rpe_raw = _f(payload.get("rpe"))
    steps_raw = _f(payload.get("steps"))
    kcal = _f(payload.get("kcal"))
    started_at = _resolve_stamp(payload.get("started_at"), payload.get("time"), now=now)
    draft = WorkoutDraft(
        kind=kind,
        title=title or workout_kinds.kind_label(kind),
        duration_min=_f(payload.get("duration_min")),
        intensity=intensity if intensity in _WORKOUT_INTENSITIES else None,
        distance_m=_f(payload.get("distance_m")),
        steps=int(steps_raw) if steps_raw else None,
        avg_hr=_f(payload.get("avg_hr")),
        rpe=int(rpe_raw) if rpe_raw and 1 <= rpe_raw <= 10 else None,
        sweat=sweat if sweat in _SWEAT else None,
        kcal=kcal,
        kcal_source="device" if kcal and source == "photo" else ("user" if kcal else "estimated"),
        started_at=started_at,
        note=str(payload.get("note") or "").strip(),
        source=source,
        confidence=_f(payload.get("confidence")),
        raw_text=raw_text,
    )
    if draft.duration_min is None and draft.steps:
        draft.duration_min = workout_kinds.minutes_from_steps(draft.steps)
    return draft


async def parse_workout_text(
    text: str, *, now: datetime, client: LLMClient | None = None
) -> WorkoutDraft:
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.WORKOUT_TEXT + text, max_tokens=600)
    return _workout_from_payload(payload, source="text", now=now, raw_text=text)


async def recognize_workout_photo(
    images: list[ImagePart],
    *,
    now: datetime,
    hint: str = "",
    client: LLMClient | None = None,
) -> WorkoutDraft:
    """Экран часов, приложения или страница бумажного дневника — в том числе от руки."""
    client = client or get_client()
    prompt = prompts.WORKOUT_PHOTO + (f"\nПодсказка пользователя: {hint}\n" if hint else "")
    payload = await _ask_json(client, prompt=prompt, images=images, max_tokens=700)
    draft = _workout_from_payload(payload, source="photo", now=now, raw_text=hint or None)
    if draft.duration_min is None and draft.steps is None and draft.distance_m is None:
        # Ни одного числа — карточка была бы пустой; пусть скажет словами.
        raise RecognitionError("на фото не видно ни времени, ни расстояния")
    return draft


# ---------------------------------------------------------------- body

async def recognize_body_photo(
    images: list[ImagePart], *, client: LLMClient | None = None
) -> MeasurementDraft:
    """Экран весов или распечатка биоимпеданса. Состав тела необязателен."""
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.BODY_SCALE, images=images, max_tokens=400)
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    draft = MeasurementDraft(
        weight_kg=_f(payload.get("weight_kg")),
        body_fat_pct=_f(payload.get("body_fat_pct")),
        muscle_mass_kg=_f(payload.get("muscle_mass_kg")),
        water_pct=_f(payload.get("water_pct")),
        bone_mass_kg=_f(payload.get("bone_mass_kg")),
        visceral_fat=_f(payload.get("visceral_fat")),
        bmr_kcal=_f(payload.get("bmr_kcal")),
        confidence=_f(payload.get("confidence")),
    )
    if draft.weight_kg is not None and not 25.0 <= draft.weight_kg <= 400.0:
        draft.weight_kg = None
    if draft.weight_kg is None and not draft.has_composition:
        raise RecognitionError("не разобрал показания весов")
    return draft


async def extract_symptoms(
    text: str, *, client: LLMClient | None = None
) -> tuple[int | None, list[str], str]:
    """(score, symptom labels, leftover note) from free text or a voice transcript."""
    client = client or get_client()
    payload = await _ask_json(client, prompt=prompts.SYMPTOM_EXTRACT + text, max_tokens=600)
    if not isinstance(payload, dict):
        raise RecognitionError("ожидался JSON-объект")
    score = payload.get("score")
    score_int = int(score) if isinstance(score, (int, float)) and 1 <= int(score) <= 5 else None
    symptoms = [str(s).strip() for s in (payload.get("symptoms") or []) if str(s).strip()]
    return score_int, symptoms, str(payload.get("note") or "").strip()


__all__ = [
    "PHOTO_KINDS",
    "RecognitionError",
    "classify_photo",
    "correct_meal",
    "extract_symptoms",
    "parse_meal_text",
    "parse_workout_text",
    "recognize_body_photo",
    "recognize_workout_photo",
    "recognize_glucose_screenshot",
    "recognize_label",
    "recognize_labs",
    "recognize_meal_photo",
    "recognize_medication",
]
