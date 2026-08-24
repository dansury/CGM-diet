"""Medication reference: name normalisation and open side-effect data.

The bot logs what the user took and can look a symptom up in a public
reference. It never prescribes, never doses, never says a drug *caused*
anything. See `spec/meds.md` and the constitution, principle I.
"""

from src.meds.catalog import DrugEntry, known_drugs, normalize_drug, resolve_cid
from src.meds.side_effects import (
    DatasetStatus,
    SideEffect,
    SymptomMatch,
    dataset_status,
    load_side_effects,
    match_symptoms,
    side_effects_for,
)

__all__ = [
    "DatasetStatus",
    "DrugEntry",
    "SideEffect",
    "SymptomMatch",
    "dataset_status",
    "known_drugs",
    "load_side_effects",
    "match_symptoms",
    "normalize_drug",
    "resolve_cid",
    "side_effects_for",
]
