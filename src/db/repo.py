"""Persistence helpers. Handlers talk to the DB only through this module.

Everything takes an `AsyncSession` and leaves the commit to the caller where a
handler needs several writes to land together. See `spec/data_model.md`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics.activity import ActivityBucket
from src.analytics.meds import MedicationLike
from src.analytics.symptoms import CheckinLike
from src.analytics.tags import normalize_name
from src.analytics.windows import GlucosePoint, MealLike
from src.db.models import (
    ActivitySample,
    AnalysisResult,
    CheckinSymptom,
    Correction,
    DictionaryEntry,
    GlucoseReading,
    Meal,
    MealItem,
    MediaFile,
    Medication,
    NutritionMemory,
    Product,
    ProductPhoto,
    SettingsKV,
    Symptom,
    User,
    Weight,
    WellbeingCheckin,
    utcnow,
)
from src.ingest.nutrition import Remembered, per_100
from src.vision.schemas import (
    GlucoseDraft,
    LabDraft,
    MealDraft,
    MedicationDraft,
    ProductDraft,
    meal_to_dict,
)

# Seeded symptom glossary — the starting buttons before the user's own
# vocabulary takes over (see `spec/wellbeing.md` § Dynamic glossary).
SEED_SYMPTOMS: tuple[tuple[str, str], ...] = (
    ("sleepiness", "сонливость"),
    ("sweating", "потливость"),
    ("brain_fog", "туман в голове"),
    ("hunger", "сильный голод"),
    ("thirst", "жажда"),
    ("palpitations", "сердцебиение"),
    ("tremor", "дрожь"),
    ("headache", "головная боль"),
    ("irritability", "раздражительность"),
    ("hot_flush", "приливы"),
    ("weakness", "слабость"),
    ("nausea", "тошнота"),
)


# ------------------------------------------------------------------ users

async def get_or_create_user(
    session: AsyncSession,
    tg_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_id))
    if user is not None:
        return user
    user = User(tg_id=tg_id, username=username, first_name=first_name)
    session.add(user)
    await session.flush()
    await seed_symptoms(session, user)
    return user


async def get_user(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


# ------------------------------------------------------------------ media

async def save_media(
    session: AsyncSession,
    user: User,
    *,
    kind: str,
    tg_file_id: str | None = None,
    tg_unique_id: str | None = None,
    mime: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
) -> MediaFile:
    media = MediaFile(
        user_id=user.id,
        kind=kind,
        tg_file_id=tg_file_id,
        tg_unique_id=tg_unique_id,
        mime=mime,
        size_bytes=size_bytes,
        sha256=sha256,
    )
    session.add(media)
    await session.flush()
    return media


# ------------------------------------------------------------------ meals

async def save_meal(
    session: AsyncSession,
    user: User,
    draft: MealDraft,
    *,
    eaten_at: datetime,
    media_id: int | None = None,
    confirmed: bool = True,
    product_id: int | None = None,
) -> Meal:
    totals = draft.totals()
    meal = Meal(
        user_id=user.id,
        eaten_at=eaten_at,
        source=draft.source,
        title=draft.title or (draft.items[0].name if draft.items else None),
        raw_text=draft.raw_text,
        note=draft.notes or None,
        media_id=media_id,
        kcal=totals["kcal"] or None,
        protein_g=totals["protein_g"] or None,
        fat_g=totals["fat_g"] or None,
        carbs_g=totals["carbs_g"] or None,
        fiber_g=totals["fiber_g"] or None,
        confidence=draft.confidence,
        confirmed=confirmed,
    )
    session.add(meal)
    await session.flush()
    for item in draft.items:
        session.add(
            MealItem(
                meal_id=meal.id,
                product_id=product_id,
                name=item.name,
                name_norm=normalize_name(item.name),
                portion_g=item.portion_g,
                kcal=item.kcal,
                protein_g=item.protein_g,
                fat_g=item.fat_g,
                carbs_g=item.carbs_g,
                fiber_g=item.fiber_g,
                tags=item.tags or [],
            )
        )
    await session.flush()
    # Populate `meal.items` eagerly: callers read totals straight after saving,
    # and a lazy load outside the async context raises MissingGreenlet.
    await session.refresh(meal, attribute_names=["items"])
    return meal


async def load_meals(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[Meal]:
    stmt = select(Meal).where(Meal.user_id == user.id).order_by(Meal.eaten_at)
    if since is not None:
        stmt = stmt.where(Meal.eaten_at >= since)
    return list(await session.scalars(stmt))


async def load_meal_likes(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[MealLike]:
    """Meals reshaped for the analytics layer (tags and item names flattened)."""
    meals = await load_meals(session, user, since=since)
    out: list[MealLike] = []
    for meal in meals:
        tags: list[str] = []
        names: list[str] = []
        for item in meal.items:
            names.append(item.name_norm)
            for tag in item.tags or []:
                if tag not in tags:
                    tags.append(tag)
        out.append(
            MealLike(
                id=meal.id,
                eaten_at=_aware(meal.eaten_at),
                tags=tags,
                items=list(dict.fromkeys(names)),
                carbs_g=meal.carbs_g,
            )
        )
    return out


# ------------------------------------------------------------------ glucose

async def save_glucose(
    session: AsyncSession,
    user: User,
    drafts: Iterable[GlucoseDraft],
    *,
    source: str = "manual",
    media_id: int | None = None,
    confirmed: bool = True,
) -> list[GlucoseReading]:
    """Insert readings, skipping exact duplicates (re-sent screenshots)."""
    saved: list[GlucoseReading] = []
    for draft in drafts:
        exists = await session.scalar(
            select(GlucoseReading.id).where(
                GlucoseReading.user_id == user.id,
                GlucoseReading.measured_at == draft.measured_at,
                GlucoseReading.value_mmol == draft.value_mmol,
            )
        )
        if exists:
            continue
        reading = GlucoseReading(
            user_id=user.id,
            measured_at=draft.measured_at,
            value_mmol=draft.value_mmol,
            unit_input=draft.unit_input,
            source=source,
            device=draft.device,
            trend=draft.trend,
            media_id=media_id,
            confirmed=confirmed,
        )
        session.add(reading)
        saved.append(reading)
    await session.flush()
    return saved


async def load_glucose(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[GlucoseReading]:
    stmt = (
        select(GlucoseReading)
        .where(GlucoseReading.user_id == user.id)
        .order_by(GlucoseReading.measured_at)
    )
    if since is not None:
        stmt = stmt.where(GlucoseReading.measured_at >= since)
    return list(await session.scalars(stmt))


async def load_points(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[GlucosePoint]:
    readings = await load_glucose(session, user, since=since)
    return [GlucosePoint(at=_aware(r.measured_at), value=r.value_mmol) for r in readings]


# ------------------------------------------------------------------ products

async def find_product(
    session: AsyncSession, user: User, *, barcode: str | None, name: str
) -> Product | None:
    if barcode:
        found = await session.scalar(
            select(Product).where(
                Product.barcode == barcode,
                (Product.user_id == user.id) | (Product.user_id.is_(None)),
            )
        )
        if found is not None:
            return found
    return await session.scalar(
        select(Product).where(Product.user_id == user.id, Product.name_norm == normalize_name(name))
    )


async def save_product(
    session: AsyncSession,
    user: User,
    draft: ProductDraft,
    *,
    media_ids: Sequence[tuple[int, str]] = (),
    confirmed: bool = True,
) -> Product:
    """Upsert a scanned package and attach its photos (front/back)."""
    product = await find_product(session, user, barcode=draft.barcode, name=draft.name)
    if product is None:
        product = Product(user_id=user.id, name=draft.name, name_norm=normalize_name(draft.name))
        session.add(product)
    product.brand = draft.brand or product.brand
    product.barcode = draft.barcode or product.barcode
    for field_name in (
        "kcal_100",
        "protein_100",
        "fat_100",
        "carbs_100",
        "sugars_100",
        "fiber_100",
    ):
        value = getattr(draft, field_name)
        if value is not None:
            setattr(product, field_name, value)
    if draft.ingredients:
        product.ingredients = draft.ingredients
        product.ingredients_text = ", ".join(draft.ingredients)
    if draft.additives:
        product.additives = draft.additives
    if draft.flags:
        product.flags = draft.flags
    product.confirmed = confirmed
    await session.flush()
    for media_id, side in media_ids:
        session.add(ProductPhoto(product_id=product.id, media_id=media_id, side=side))
    await session.flush()
    return product


# ------------------------------------------------------------------ wellbeing

async def seed_symptoms(session: AsyncSession, user: User) -> None:
    for slug, label in SEED_SYMPTOMS:
        session.add(Symptom(user_id=user.id, slug=slug, label=label))
    await session.flush()


async def list_symptoms(session: AsyncSession, user: User, *, limit: int = 12) -> list[Symptom]:
    """Glossary buttons: the user's most-used symptoms first."""
    stmt = (
        select(Symptom)
        .where(Symptom.user_id == user.id, Symptom.is_active.is_(True))
        .order_by(Symptom.hits.desc(), Symptom.id)
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def upsert_symptom(session: AsyncSession, user: User, label: str) -> Symptom:
    """Add a symptom the user typed/said; the glossary grows per user.

    Matching is by *normalised label* first and only then by slug: seeded rows
    carry an English slug (`sleepiness`) with a Russian label (`сонливость`), so
    a slug-only lookup would create a duplicate every time the user taps a
    seeded button — and split that symptom's statistics in half.
    """
    norm = normalize_name(label)
    if not norm:
        norm = "symptom"
    existing = await session.scalars(select(Symptom).where(Symptom.user_id == user.id))
    for symptom in existing:
        if normalize_name(symptom.label) == norm or symptom.slug == norm.replace(" ", "_"):
            return symptom
    found = Symptom(user_id=user.id, slug=norm.replace(" ", "_")[:64], label=label.strip()[:128])
    session.add(found)
    await session.flush()
    return found


async def save_checkin(
    session: AsyncSession,
    user: User,
    *,
    at: datetime,
    score: int,
    symptom_labels: Sequence[str] = (),
    note: str | None = None,
    source: str = "buttons",
    media_id: int | None = None,
) -> WellbeingCheckin:
    checkin = WellbeingCheckin(
        user_id=user.id, at=at, score=score, note=note, source=source, media_id=media_id
    )
    session.add(checkin)
    await session.flush()
    for label in symptom_labels:
        symptom = await upsert_symptom(session, user, label)
        symptom.hits += 1
        symptom.last_used_at = utcnow()
        session.add(CheckinSymptom(checkin_id=checkin.id, symptom_id=symptom.id))
    await session.flush()
    return checkin


async def load_checkins(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[tuple[WellbeingCheckin, list[str]]]:
    stmt = (
        select(WellbeingCheckin)
        .where(WellbeingCheckin.user_id == user.id)
        .order_by(WellbeingCheckin.at)
    )
    if since is not None:
        stmt = stmt.where(WellbeingCheckin.at >= since)
    checkins = list(await session.scalars(stmt))
    labels: dict[int, str] = {
        s.id: s.label
        for s in await session.scalars(select(Symptom).where(Symptom.user_id == user.id))
    }
    return [
        (c, [labels.get(cs.symptom_id, "?") for cs in c.symptoms])
        for c in checkins
    ]


# ------------------------------------------------------------------ misc rows

async def load_checkin_likes(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[CheckinLike]:
    """Check-ins reshaped for analytics, with timestamps forced to UTC-aware.

    SQLite hands back naive datetimes; the analytics layer compares them against
    tz-aware glucose points, so the conversion has to happen at the boundary.
    """
    return [
        CheckinLike(at=_aware(c.at), score=c.score, symptoms=labels)
        for c, labels in await load_checkins(session, user, since=since)
    ]


async def load_activity_buckets(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[ActivityBucket]:
    """Step samples reshaped for `analytics.activity`, timestamps UTC-aware."""
    out: list[ActivityBucket] = []
    for sample in await load_activity(session, user, since=since):
        start = _aware(sample.start_at)
        end = _aware(sample.end_at) if sample.end_at else start + timedelta(minutes=15)
        out.append(ActivityBucket(start_at=start, end_at=end, steps=sample.steps or 0))
    return out


async def save_weight(
    session: AsyncSession, user: User, *, measured_at: datetime, weight_kg: float
) -> Weight:
    row = Weight(user_id=user.id, measured_at=measured_at, weight_kg=weight_kg)
    session.add(row)
    await session.flush()
    return row


async def save_medication(
    session: AsyncSession,
    user: User,
    *,
    taken_at: datetime,
    name: str,
    dose_text: str | None = None,
    form: str | None = None,
    note: str | None = None,
    source: str = "text",
    media_id: int | None = None,
) -> Medication:
    """Log one dose. `slug`/`cid` are resolved here so every write path — photo,
    text, dictionary button — lands with the same reference key."""
    from src.meds.catalog import normalize_drug, resolve_cid

    slug = normalize_drug(name)
    row = Medication(
        user_id=user.id,
        taken_at=taken_at,
        name=name,
        slug=slug or None,
        cid=resolve_cid(name),
        dose_text=dose_text,
        form=form,
        note=note,
        source=source,
        media_id=media_id,
    )
    session.add(row)
    await session.flush()
    return row


async def save_medication_draft(
    session: AsyncSession,
    user: User,
    draft: MedicationDraft,
    *,
    taken_at: datetime,
    media_id: int | None = None,
    source: str = "photo",
) -> Medication:
    row = await save_medication(
        session,
        user,
        taken_at=taken_at,
        name=draft.name,
        dose_text=draft.dose_text,
        form=draft.form,
        note=draft.note,
        source=source,
        media_id=media_id,
    )
    await bump_dictionary(
        session,
        user,
        kind="medication",
        label=draft.name,
        payload={
            "dose_text": draft.dose_text,
            "form": draft.form,
            "inn": draft.inn,
            "slug": row.slug,
            "cid": row.cid,
        },
    )
    return row


async def load_medications(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[Medication]:
    stmt = select(Medication).where(Medication.user_id == user.id).order_by(Medication.taken_at)
    if since is not None:
        stmt = stmt.where(Medication.taken_at >= since)
    return list(await session.scalars(stmt))


async def load_medication_likes(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[MedicationLike]:
    """Doses reshaped for the analytics layer (no ORM beyond this module)."""
    return [
        MedicationLike(
            id=row.id,
            taken_at=_aware(row.taken_at),
            name=row.name,
            slug=row.slug or normalize_name(row.name),
        )
        for row in await load_medications(session, user, since=since)
    ]


# ------------------------------------------------------------------ dictionary

#: how many sightings a kind needs before it is offered as a shortcut.
#: Meals must be seen twice ("блюда, которые встретились более 1 раза");
#: a medication photographed once is already a routine.
MIN_HITS: dict[str, int] = {"meal": 2, "item": 2, "medication": 1}

DICTIONARY_KINDS: tuple[str, ...] = ("meal", "item", "medication")


async def bump_dictionary(
    session: AsyncSession,
    user: User,
    *,
    kind: str,
    label: str,
    payload: dict | None = None,
    hits: int = 1,
) -> DictionaryEntry | None:
    """Count one sighting of `label`; create the entry on the first one.

    A hidden entry (user tapped 🗑) keeps counting but stays hidden — deleting a
    shortcut must not be undone by the next meal.
    """
    label = (label or "").strip()
    key = normalize_name(label)
    if not key or kind not in DICTIONARY_KINDS:
        return None
    entry = await session.scalar(
        select(DictionaryEntry).where(
            DictionaryEntry.user_id == user.id,
            DictionaryEntry.kind == kind,
            DictionaryEntry.key_norm == key,
        )
    )
    if entry is None:
        entry = DictionaryEntry(
            user_id=user.id,
            kind=kind,
            key_norm=key,
            label=label[:128],
            payload=payload,
            hits=hits,
            last_used_at=utcnow(),
        )
        session.add(entry)
    else:
        entry.hits += hits
        entry.last_used_at = utcnow()
        if payload:
            entry.payload = payload
    await session.flush()
    return entry


def _visible(kind_column: Any = DictionaryEntry.kind) -> Any:
    """`hits >= MIN_HITS[kind]`, expressed for the query layer."""
    from sqlalchemy import case

    return DictionaryEntry.hits >= case(MIN_HITS, value=kind_column, else_=1)


def _order() -> tuple[Any, ...]:
    return (
        DictionaryEntry.pinned.desc(),
        DictionaryEntry.hits.desc(),
        DictionaryEntry.last_used_at.desc(),
    )


async def suggest_dictionary(
    session: AsyncSession,
    user: User,
    prefix: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 6,
) -> list[DictionaryEntry]:
    """Prefix first, substring second — «гре» finds «гречка», «чка» still finds it."""
    key = normalize_name(prefix)
    if not key:
        return []
    base = select(DictionaryEntry).where(
        DictionaryEntry.user_id == user.id,
        DictionaryEntry.is_active.is_(True),
        _visible(),
    )
    if kinds:
        base = base.where(DictionaryEntry.kind.in_(tuple(kinds)))
    found: list[DictionaryEntry] = []
    seen: set[int] = set()
    for condition in (
        DictionaryEntry.key_norm.startswith(key),
        DictionaryEntry.key_norm.contains(key),
    ):
        rows = await session.scalars(base.where(condition).order_by(*_order()).limit(limit))
        for row in rows:
            if row.id not in seen:
                seen.add(row.id)
                found.append(row)
        if len(found) >= limit:
            break
    return found[:limit]


async def list_dictionary(
    session: AsyncSession,
    user: User,
    *,
    kind: str | None = None,
    limit: int = 30,
    offset: int = 0,
    include_hidden: bool = False,
) -> list[DictionaryEntry]:
    stmt = select(DictionaryEntry).where(DictionaryEntry.user_id == user.id, _visible())
    if kind:
        stmt = stmt.where(DictionaryEntry.kind == kind)
    if not include_hidden:
        stmt = stmt.where(DictionaryEntry.is_active.is_(True))
    stmt = stmt.order_by(*_order()).offset(offset).limit(limit)
    return list(await session.scalars(stmt))


async def get_dictionary_entry(
    session: AsyncSession, user: User, entry_id: int
) -> DictionaryEntry | None:
    return await session.scalar(
        select(DictionaryEntry).where(
            DictionaryEntry.id == entry_id, DictionaryEntry.user_id == user.id
        )
    )


async def touch_dictionary(session: AsyncSession, entry: DictionaryEntry) -> None:
    entry.hits += 1
    entry.last_used_at = utcnow()
    await session.flush()


async def hide_dictionary(session: AsyncSession, entry: DictionaryEntry) -> None:
    entry.is_active = False
    await session.flush()


async def remember_meal(session: AsyncSession, user: User, draft: MealDraft) -> None:
    """One confirmed meal → dictionary sightings for the dish and its items."""
    title = (draft.title or "").strip()
    if title:
        await bump_dictionary(session, user, kind="meal", label=title, payload=meal_to_dict(draft))
    for item in draft.items:
        await bump_dictionary(session, user, kind="item", label=item.name)


# ------------------------------------------------------------------ nutrition memory

async def remember_nutrition(
    session: AsyncSession, user: User, *, name: str, values: Remembered
) -> NutritionMemory | None:
    """Store the user's БЖУ for a dish, per 100 g. Re-entering it overwrites.

    Constitution III: what the user typed is theirs and outranks every later
    machine estimate — see `spec/dictionary.md` § Память БЖУ.
    """
    label = (name or "").strip()
    key = normalize_name(label)
    if not key or values.empty:
        return None
    row = await session.scalar(
        select(NutritionMemory).where(
            NutritionMemory.user_id == user.id, NutritionMemory.key_norm == key
        )
    )
    if row is None:
        row = NutritionMemory(user_id=user.id, key_norm=key, label=label[:128], hits=1)
        session.add(row)
    else:
        row.hits += 1
        row.label = label[:128]
    for column in ("kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "portion_g"):
        value = getattr(values, column)
        if value is not None:
            setattr(row, column, value)
    row.last_used_at = utcnow()
    await session.flush()
    return row


async def load_nutrition_memory(
    session: AsyncSession, user: User, names: Iterable[str] | None = None
) -> dict[str, Remembered]:
    """`{key_norm: Remembered}` — everything remembered, or just what `names` needs."""
    stmt = select(NutritionMemory).where(NutritionMemory.user_id == user.id)
    rows = list(await session.scalars(stmt))
    keys = {normalize_name(n) for n in names} if names is not None else None
    out: dict[str, Remembered] = {}
    for row in rows:
        # Substring matching happens in `nutrition.match_memory`, so a narrowing
        # filter here must keep any key that overlaps one of the names.
        if keys is not None and not any(
            key and (key == row.key_norm or key in row.key_norm or row.key_norm in key)
            for key in keys
        ):
            continue
        out[row.key_norm] = Remembered(
            kcal=row.kcal,
            protein_g=row.protein_g,
            fat_g=row.fat_g,
            carbs_g=row.carbs_g,
            fiber_g=row.fiber_g,
            portion_g=row.portion_g,
        )
    return out


async def remember_meal_macros(
    session: AsyncSession, user: User, draft: MealDraft
) -> list[str]:
    """Persist БЖУ the user typed on this draft. Returns the dishes remembered."""
    saved: list[str] = []
    for item in draft.items:
        if item.macros_source != "user":
            continue
        row = await remember_nutrition(session, user, name=item.name, values=per_100(item))
        if row is not None:
            saved.append(item.name)
    return saved


# ------------------------------------------------------------------ settings kv

async def get_setting(session: AsyncSession, key: str) -> Any:
    row = await session.get(SettingsKV, key)
    return row.value if row is not None else None


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(SettingsKV, key)
    if row is None:
        session.add(SettingsKV(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    await session.flush()


async def all_settings(session: AsyncSession) -> dict[str, Any]:
    return {row.key: row.value for row in await session.scalars(select(SettingsKV))}


async def save_labs(
    session: AsyncSession, user: User, draft: LabDraft, *, media_id: int | None = None
) -> list[AnalysisResult]:
    rows: list[AnalysisResult] = []
    taken_at = draft.taken_at or utcnow()
    for marker in draft.markers:
        row = AnalysisResult(
            user_id=user.id,
            taken_at=taken_at,
            panel=draft.panel,
            marker=marker.marker,
            value=marker.value,
            value_text=marker.value_text,
            unit=marker.unit,
            ref_low=marker.ref_low,
            ref_high=marker.ref_high,
            flag=marker.flag,
            media_id=media_id,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def save_correction(
    session: AsyncSession,
    user: User,
    *,
    entity_type: str,
    entity_id: int | None,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> Correction:
    row = Correction(
        user_id=user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
    )
    session.add(row)
    await session.flush()
    return row


async def load_activity(
    session: AsyncSession, user: User, *, since: datetime | None = None
) -> list[ActivitySample]:
    stmt = (
        select(ActivitySample)
        .where(ActivitySample.user_id == user.id)
        .order_by(ActivitySample.start_at)
    )
    if since is not None:
        stmt = stmt.where(ActivitySample.start_at >= since)
    return list(await session.scalars(stmt))


async def upsert_activity(
    session: AsyncSession, user: User, samples: Iterable[ActivitySample]
) -> int:
    """Idempotent ingest — the phone relay re-sends overlapping ranges."""
    inserted = 0
    for sample in samples:
        if sample.external_id:
            exists = await session.scalar(
                select(ActivitySample.id).where(
                    ActivitySample.user_id == user.id,
                    ActivitySample.external_id == sample.external_id,
                )
            )
            if exists:
                continue
        sample.user_id = user.id
        session.add(sample)
        inserted += 1
    await session.flush()
    return inserted


# ------------------------------------------------------------------ erasure

async def delete_user_data(session: AsyncSession, user: User, *, drop_user: bool = False) -> None:
    """Hard-delete everything about a user (GDPR-style erasure).

    Explicit per-table deletes rather than a cascade on `users`: SQLite does
    not enforce FK cascades unless PRAGMA foreign_keys is on, and the user row
    itself must survive a `/delete` that only wipes records.
    """
    for model in (
        CheckinSymptom,
        WellbeingCheckin,
        MealItem,
        Meal,
        GlucoseReading,
        Weight,
        Medication,
        AnalysisResult,
        ActivitySample,
        ProductPhoto,
        Product,
        MediaFile,
        Correction,
        DictionaryEntry,
        NutritionMemory,
    ):
        if model is CheckinSymptom:
            checkin_ids = select(WellbeingCheckin.id).where(WellbeingCheckin.user_id == user.id)
            await session.execute(
                delete(CheckinSymptom).where(CheckinSymptom.checkin_id.in_(checkin_ids))
            )
            continue
        if model is MealItem:
            meal_ids = select(Meal.id).where(Meal.user_id == user.id)
            await session.execute(delete(MealItem).where(MealItem.meal_id.in_(meal_ids)))
            continue
        if model is ProductPhoto:
            product_ids = select(Product.id).where(Product.user_id == user.id)
            await session.execute(
                delete(ProductPhoto).where(ProductPhoto.product_id.in_(product_ids))
            )
            continue
        await session.execute(delete(model).where(model.user_id == user.id))
    await session.execute(delete(Symptom).where(Symptom.user_id == user.id))
    if drop_user:
        await session.execute(delete(User).where(User.id == user.id))
    await session.flush()


# ------------------------------------------------------------------ helpers

def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; the analytics layer needs tz-aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def counts(session: AsyncSession, user: User) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, model in (
        ("meals", Meal),
        ("glucose", GlucoseReading),
        ("checkins", WellbeingCheckin),
        ("products", Product),
        ("labs", AnalysisResult),
        ("activity", ActivitySample),
        ("medications", Medication),
    ):
        out[name] = int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.user_id == user.id)
            )
            or 0
        )
    return out


def day_bounds(now: datetime, *, days: int = 1) -> datetime:
    return now - timedelta(days=days)


__all__ = [
    "DICTIONARY_KINDS",
    "MIN_HITS",
    "SEED_SYMPTOMS",
    "all_settings",
    "bump_dictionary",
    "counts",
    "day_bounds",
    "delete_user_data",
    "find_product",
    "get_dictionary_entry",
    "get_or_create_user",
    "get_setting",
    "get_user",
    "hide_dictionary",
    "list_dictionary",
    "list_symptoms",
    "load_activity",
    "load_activity_buckets",
    "load_checkin_likes",
    "load_checkins",
    "load_glucose",
    "load_meal_likes",
    "load_meals",
    "load_medication_likes",
    "load_medications",
    "load_points",
    "remember_meal",
    "save_checkin",
    "save_correction",
    "save_glucose",
    "save_labs",
    "save_meal",
    "save_media",
    "save_medication",
    "save_medication_draft",
    "save_product",
    "save_weight",
    "seed_symptoms",
    "set_setting",
    "suggest_dictionary",
    "touch_dictionary",
    "upsert_activity",
    "upsert_symptom",
    "delete_user_data",
]
