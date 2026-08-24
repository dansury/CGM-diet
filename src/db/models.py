"""ORM models. See `spec/data_model.md` for the full table reference.

Storage conventions
-------------------
* every timestamp is timezone-aware UTC (`DateTime(timezone=True)`);
* glucose is stored canonically in **mmol/L** (`value_mmol`), the unit the user
  typed is kept in `unit_input` so an export can round-trip it;
* nutrients are grams, energy is kcal;
* user erasure is a hard delete driven by `ON DELETE CASCADE` from `users`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    locale: Mapped[str] = mapped_column(String(8), default="ru", nullable=False)
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    glucose_unit: Mapped[str] = mapped_column(String(8), default="mmol/L", nullable=False)
    sensor: Mapped[str | None] = mapped_column(String(64))
    # per-user overrides of the postprandial windows, minutes
    window_1h_start: Mapped[int] = mapped_column(Integer, default=45, nullable=False)
    window_1h_end: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    window_2h_start: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    window_2h_end: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    baseline_window: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MediaFile(Base, TimestampMixin):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tg_file_id: Mapped[str | None] = mapped_column(String(256))
    tg_unique_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # meal|glucose|label|lab|voice
    mime: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    local_path: Mapped[str | None] = mapped_column(String(512))


class Product(Base, TimestampMixin):
    """A packaged product remembered from its label photos.

    `user_id` NULL means a shared/global entry; per-user rows keep private
    scans private. Nutrients are per 100 g/ml as printed on the pack.
    """

    __tablename__ = "products"
    __table_args__ = (Index("ix_products_user_name", "user_id", "name_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    barcode: Mapped[str | None] = mapped_column(String(32), index=True)
    brand: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    kcal_100: Mapped[float | None] = mapped_column(Float)
    protein_100: Mapped[float | None] = mapped_column(Float)
    fat_100: Mapped[float | None] = mapped_column(Float)
    carbs_100: Mapped[float | None] = mapped_column(Float)
    sugars_100: Mapped[float | None] = mapped_column(Float)
    fiber_100: Mapped[float | None] = mapped_column(Float)
    ingredients_text: Mapped[str | None] = mapped_column(Text)
    ingredients: Mapped[list | None] = mapped_column(JSON)
    additives: Mapped[list | None] = mapped_column(JSON)
    flags: Mapped[list | None] = mapped_column(JSON)  # e.g. ["added_sugar","refined_flour"]
    source: Mapped[str] = mapped_column(String(32), default="label_photo", nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    photos: Mapped[list[ProductPhoto]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductPhoto(Base, TimestampMixin):
    __tablename__ = "product_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    side: Mapped[str] = mapped_column(String(16), default="front", nullable=False)  # front|back

    product: Mapped[Product] = relationship(back_populates="photos")


class Meal(Base, TimestampMixin):
    __tablename__ = "meals"
    __table_args__ = (Index("ix_meals_user_eaten", "user_id", "eaten_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    eaten_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="photo", nullable=False)
    # photo|text|voice|label
    title: Mapped[str | None] = mapped_column(String(256))
    raw_text: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fiber_g: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    corrected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    items: Mapped[list[MealItem]] = relationship(
        back_populates="meal", cascade="all, delete-orphan", lazy="selectin"
    )


class MealItem(Base, TimestampMixin):
    __tablename__ = "meal_items"
    __table_args__ = (Index("ix_meal_items_norm", "name_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), index=True, nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(256), nullable=False)
    portion_g: Mapped[float | None] = mapped_column(Float)
    kcal: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fiber_g: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list | None] = mapped_column(JSON)  # component tags, see analytics/tags.py

    meal: Mapped[Meal] = relationship(back_populates="items")


class GlucoseReading(Base, TimestampMixin):
    __tablename__ = "glucose_readings"
    __table_args__ = (
        Index("ix_glucose_user_time", "user_id", "measured_at"),
        UniqueConstraint("user_id", "measured_at", "value_mmol", name="uq_glucose_point"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_mmol: Mapped[float] = mapped_column(Float, nullable=False)
    unit_input: Mapped[str] = mapped_column(String(8), default="mmol/L", nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)
    # manual|text|screenshot|cgm_api
    device: Mapped[str | None] = mapped_column(String(64))
    trend: Mapped[str | None] = mapped_column(String(16))
    raw_text: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Weight(Base, TimestampMixin):
    __tablename__ = "weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class Medication(Base, TimestampMixin):
    """A *log* of what the user took. The bot never prescribes or doses."""

    __tablename__ = "medications"
    __table_args__ = (Index("ix_medications_user_taken", "user_id", "taken_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # normalized key + the STITCH/PubChem id the side-effect reference is keyed by
    slug: Mapped[str | None] = mapped_column(String(128), index=True)
    cid: Mapped[str | None] = mapped_column(String(16))
    dose_text: Mapped[str | None] = mapped_column(String(128))
    form: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)


class DictionaryEntry(Base, TimestampMixin):
    """Personal shortcut list: what this user records over and over.

    Meals earn a place on the *second* sighting, medications on the first —
    a drug the user photographed once is one they take on a schedule.
    See `spec/dictionary.md`.
    """

    __tablename__ = "user_dictionary"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "key_norm", name="uq_dictionary_user_kind_key"),
        Index("ix_dictionary_lookup", "user_id", "kind", "key_norm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # meal|item|medication
    key_norm: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettingsKV(Base):
    """Owner-level runtime settings (model selection). One row per key."""

    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AnalysisResult(Base, TimestampMixin):
    """One marker from a lab panel (photo, PDF or typed)."""

    __tablename__ = "analysis_results"
    __table_args__ = (Index("ix_analysis_user_marker", "user_id", "marker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    panel: Mapped[str | None] = mapped_column(String(128))
    marker: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(32))
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    flag: Mapped[str | None] = mapped_column(String(16))  # low|normal|high
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    raw_text: Mapped[str | None] = mapped_column(Text)


class Symptom(Base, TimestampMixin):
    """Dynamic per-user symptom glossary; `user_id` NULL = seeded global entry."""

    __tablename__ = "symptoms"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_symptoms_user_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WellbeingCheckin(Base, TimestampMixin):
    __tablename__ = "wellbeing_checkins"
    __table_args__ = (Index("ix_checkins_user_at", "user_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5, 5 = отлично
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="buttons", nullable=False)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))

    symptoms: Mapped[list[CheckinSymptom]] = relationship(
        back_populates="checkin", cascade="all, delete-orphan", lazy="selectin"
    )


class CheckinSymptom(Base):
    __tablename__ = "checkin_symptoms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checkin_id: Mapped[int] = mapped_column(
        ForeignKey("wellbeing_checkins.id", ondelete="CASCADE"), index=True, nullable=False
    )
    symptom_id: Mapped[int] = mapped_column(
        ForeignKey("symptoms.id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[int | None] = mapped_column(Integer)

    checkin: Mapped[WellbeingCheckin] = relationship(back_populates="symptoms")


class ActivitySample(Base, TimestampMixin):
    """Steps / workouts / sleep pulled from Samsung Health (Health Connect relay)."""

    __tablename__ = "activity_samples"
    __table_args__ = (
        Index("ix_activity_user_start", "user_id", "start_at"),
        UniqueConstraint("user_id", "external_id", name="uq_activity_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # steps|workout|sleep|heart_rate
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[float | None] = mapped_column(Float)
    kcal: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="samsung_health", nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)


class FoodStat(Base):
    """Cached per-key postprandial statistics (see `analytics/stats.py`)."""

    __tablename__ = "food_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "key_type", "key", "window", name="uq_food_stats_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    key_type: Mapped[str] = mapped_column(String(16), nullable=False)  # item|tag|product
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    window: Mapped[str] = mapped_column(String(8), default="1h", nullable=False)
    n: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mean_delta: Mapped[float | None] = mapped_column(Float)
    median_delta: Mapped[float | None] = mapped_column(Float)
    max_delta: Mapped[float | None] = mapped_column(Float)
    ci_low: Mapped[float | None] = mapped_column(Float)
    ci_high: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(8), default="low", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Correction(Base, TimestampMixin):
    """Audit trail: every user correction of a recognised value is kept."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "ActivitySample",
    "AnalysisResult",
    "Base",
    "CheckinSymptom",
    "Correction",
    "FoodStat",
    "GlucoseReading",
    "Meal",
    "MealItem",
    "MediaFile",
    "Medication",
    "Product",
    "ProductPhoto",
    "Symptom",
    "User",
    "WellbeingCheckin",
    "Weight",
    "utcnow",
]
