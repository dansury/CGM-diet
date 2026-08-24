"""Drug catalogue, side-effect reference and the medication analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.analytics.meds import MedicationLike, coverage, symptom_links
from src.analytics.symptoms import CheckinLike
from src.analytics.windows import Excursion
from src.meds.catalog import find_drug, normalize_drug, resolve_cid
from src.meds.side_effects import dataset_status, match_symptoms, side_effects_for

T0 = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)


# ------------------------------------------------------------------ catalogue

def test_dose_and_pack_size_are_stripped_from_the_key():
    assert normalize_drug("Глюкофаж 850 мг №60") == "глюкофаж"
    assert normalize_drug("Нурофен Форте 400мг") == "нурофен"
    assert normalize_drug("Конкор 5 мг таб. n30") == "конкор"


def test_trade_names_resolve_to_the_substance():
    assert find_drug("глюкофаж").inn_ru == "метформин"
    assert find_drug("Сиофор 1000").inn_en == "metformin"
    assert resolve_cid("метформин") == resolve_cid("глюкофаж")


def test_unknown_drug_is_not_an_error():
    assert find_drug("волшебные капли") is None
    assert resolve_cid("волшебные капли") is None
    assert side_effects_for("волшебные капли") == ()


# ------------------------------------------------------------------ reference

def test_reference_is_available_out_of_the_box():
    status = dataset_status()
    assert status.rows > 0 and status.drugs > 0


def test_symptom_matches_the_reference_by_russian_label():
    matches = match_symptoms("Глюкофаж 850", ["тошнота", "туман в голове"])
    assert [m.symptom for m in matches] == ["тошнота"]
    assert matches[0].effect.name_en == "Nausea"


# ------------------------------------------------------------------ analytics

def _excursion(minutes: int) -> Excursion:
    return Excursion(
        meal_id=None,
        eaten_at=T0 + timedelta(minutes=minutes),
        window="1h",
        baseline=5.0,
        delta=2.0,
        n_points=4,
    )


def test_coverage_counts_excursions_inside_the_dose_window():
    meds = [MedicationLike(id=1, taken_at=T0, name="Метформин", slug="метформин")]
    excursions = [_excursion(30), _excursion(120), _excursion(60 * 20)]
    rows = coverage(excursions, meds)
    assert len(rows) == 1
    assert rows[0].n_covered == 2 and rows[0].n_total == 3


def test_coverage_stays_quiet_on_a_single_coincidence():
    meds = [MedicationLike(id=1, taken_at=T0, name="Метформин", slug="метформин")]
    assert coverage([_excursion(30), _excursion(60 * 20)], meds) == []


def test_symptom_links_need_two_hits_and_a_reference_entry():
    meds = [
        MedicationLike(id=i, taken_at=T0 + timedelta(days=i), name="Глюкофаж", slug="глюкофаж")
        for i in range(3)
    ]
    checkins = [
        CheckinLike(at=T0 + timedelta(days=i, hours=2), score=3, symptoms=["тошнота"])
        for i in range(3)
    ]
    links = symptom_links(meds, checkins, lambda name: match_symptoms(name, ["тошнота"]))
    assert len(links) == 1
    assert links[0].symptom == "тошнота" and links[0].n_after_dose == 3
    assert links[0].effect_ru == "тошнота"


def test_symptom_outside_the_window_is_not_linked():
    meds = [MedicationLike(id=1, taken_at=T0, name="Глюкофаж", slug="глюкофаж")]
    checkins = [
        CheckinLike(at=T0 + timedelta(hours=20), score=3, symptoms=["тошнота"]),
        CheckinLike(at=T0 + timedelta(hours=30), score=3, symptoms=["тошнота"]),
    ]
    assert symptom_links(meds, checkins, lambda name: match_symptoms(name, ["тошнота"])) == []


# ------------------------------------------------------------------ storage

@pytest.mark.asyncio
async def test_a_logged_dose_carries_the_reference_key(session):
    from src.db import repo

    user = await repo.get_or_create_user(session, 777)
    row = await repo.save_medication(
        session, user, taken_at=T0, name="Глюкофаж 850 мг", dose_text="850 мг"
    )
    assert row.slug == "глюкофаж"
    assert row.cid == resolve_cid("метформин")

    likes = await repo.load_medication_likes(session, user)
    assert [like.name for like in likes] == ["Глюкофаж 850 мг"]


@pytest.mark.asyncio
async def test_a_photographed_drug_lands_in_the_dictionary_at_once(session):
    from src.db import repo
    from src.vision.schemas import MedicationDraft

    user = await repo.get_or_create_user(session, 778)
    await repo.save_medication_draft(
        session,
        user,
        MedicationDraft(name="Конкор", inn="бисопролол", dose_text="5 мг"),
        taken_at=T0,
    )
    entries = await repo.list_dictionary(session, user, kind="medication")
    assert [e.label for e in entries] == ["Конкор"]
    assert entries[0].payload["dose_text"] == "5 мг"


# ------------------------------------------------------------------ wording

def test_the_side_effect_note_never_claims_a_cause():
    from src.analytics.meds import SymptomLink
    from src.reporting import format_med_side_effects

    text = format_med_side_effects(
        [
            SymptomLink(
                slug="глюкофаж",
                name="Глюкофаж",
                symptom="тошнота",
                effect_ru="тошнота",
                n_after_dose=3,
                n_total=5,
            )
        ]
    )
    assert "в справочнике побочных эффектов" in text
    assert "не доказательство причины" in text
    for forbidden in ("вызывает", "из-за препарата", "виноват"):
        assert forbidden not in text.lower()


def test_the_confounder_note_is_context_not_a_verdict():
    from src.analytics.meds import Coverage
    from src.reporting import format_med_coverage

    text = format_med_coverage([Coverage(slug="m", name="Метформин", n_covered=8, n_total=10)])
    assert "не оценка препарата" in text
    assert "снижает" not in text and "повышает" not in text
