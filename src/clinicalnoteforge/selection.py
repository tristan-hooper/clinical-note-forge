"""Temporal relevance, seeded fact selection, and leakage-safe patient partitions."""

import random
from collections import defaultdict
from datetime import date, datetime

from .config import Config
from .ingestion import SourceData, digest
from .models import GROUPS, CanonicalEncounter, ConditionFact, Encounter, SelectedFacts

OUTPATIENT_CLASSES = {"ambulatory", "outpatient", "wellness"}
SPLITS = ("train", "validation", "test")


def active(start: date | datetime, stop: date | datetime | None, at: date | datetime) -> bool:
    return start <= at and (stop is None or stop >= at)


def condition_active(fact: ConditionFact, encounter: Encounter) -> bool:
    day = encounter.start.date()
    return active(fact.start, fact.stop, day) and not (
        fact.start == day
        and fact.encounter_id is not None
        and fact.originating_encounter_start is not None
        and fact.originating_encounter_start > encounter.start
    )


def canonical_encounters(data: SourceData) -> list[CanonicalEncounter]:
    result = []
    for encounter in sorted(data.encounters.values(), key=lambda e: (e.start, e.encounter_id)):
        if encounter.encounter_class not in OUTPATIENT_CLASSES:
            continue
        facts = data.facts_by_patient.get(encounter.patient_id, SelectedFacts())
        # ponytail: per-patient history scans; add interval indexes for large exports.
        selected = SelectedFacts(
            conditions=[
                f.model_copy(update={"active_at_encounter": True})
                for f in facts.conditions
                if condition_active(f, encounter)
            ],
            medications=[
                f.model_copy(update={"active_at_encounter": True})
                for f in facts.medications
                if active(f.start, f.stop, encounter.start)
            ],
            observations=[
                f for f in facts.observations if f.encounter_id == encounter.encounter_id
            ],
            procedures=[f for f in facts.procedures if f.encounter_id == encounter.encounter_id],
        )
        result.append(
            CanonicalEncounter(
                patient=data.patients[encounter.patient_id],
                encounter=encounter,
                relevant_facts=selected,
            )
        )
    return result


def select_facts(encounter: CanonicalEncounter, config: Config) -> SelectedFacts:
    chosen = {}
    for group in GROUPS:
        # Hash ranks avoid dependence on traversal order or global RNG state.
        facts = sorted(getattr(encounter.relevant_facts, group), key=lambda f: f.fact_id)
        facts = [
            f
            for f in facts
            if int(
                digest(
                    [
                        config.seed,
                        encounter.encounter.encounter_id,
                        f.fact_id,
                    ]
                ),
                16,
            )
            / (1 << 256)
            < config.selection_fraction
        ]
        if config.max_facts_per_group is not None:
            facts = sorted(facts, key=lambda f: digest([config.seed, f.fact_id]))[
                : config.max_facts_per_group
            ]
        chosen[group] = sorted(facts, key=lambda f: f.fact_id)
    return SelectedFacts(**chosen)


def choose_style(encounter_id: str, config: Config):
    return random.Random(digest([config.seed, encounter_id, "style"])).choices(
        config.styles,
        weights=config.style_weights,
        k=1,
    )[0]


def sample_encounters(
    eligible: list[CanonicalEncounter], config: Config
) -> list[CanonicalEncounter]:
    """Hash-ranked round-robin: one encounter per patient before any second encounter."""
    if config.max_records is None or config.max_records >= len(eligible):
        return eligible
    by_patient = defaultdict(list)
    for encounter in eligible:
        by_patient[encounter.patient.patient_id].append(encounter)
    patients = sorted(by_patient, key=lambda p: (digest([config.seed, p, "sample-patient"]), p))
    for encounters in by_patient.values():
        encounters.sort(
            key=lambda e: (
                digest([config.seed, e.encounter.encounter_id, "sample-encounter"]),
                e.encounter.encounter_id,
            )
        )
    result = []
    round_number = 0
    while len(result) < config.max_records:
        for patient in patients:
            if round_number < len(by_patient[patient]):
                result.append(by_patient[patient][round_number])
                if len(result) == config.max_records:
                    return result
        round_number += 1
    return result


def patient_splits(patient_ids, config: Config) -> dict[str, str]:
    ordered = sorted(set(patient_ids), key=lambda p: (digest([config.seed, p, "split"]), p))
    # Largest-remainder allocation gives small fixture cohorts usable held-out partitions.
    exact = [len(ordered) * x for x in config.split_proportions]
    counts = [int(x) for x in exact]
    for i in sorted(range(3), key=lambda i: (-(exact[i] - counts[i]), i))[
        : len(ordered) - sum(counts)
    ]:
        counts[i] += 1
    result = {}
    cursor = 0
    for split, count in zip(SPLITS, counts, strict=True):
        for patient in ordered[cursor : cursor + count]:
            result[patient] = split
        cursor += count
    return result
