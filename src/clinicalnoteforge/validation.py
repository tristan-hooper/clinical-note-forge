"""Deterministic structural validation, not a clinical assertion detector."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from pydantic import Field

from .config import Config
from .ingestion import PREFIX, digest
from .models import GROUPS, PROMPT_VERSION, SCHEMA_VERSION, CanonicalEncounter, Model, Record
from .rendering import TemplateError, bindings, extraction_target, substitute
from .selection import OUTPATIENT_CLASSES, SPLITS, active, condition_active, patient_splits


class Manifest(Model):
    dataset_schema_version: str
    package_version: str
    prompt_version: str
    seed: int
    renderer: str
    config: Config
    source_files: dict[str, dict]
    source_warnings: list[str]
    total_eligible_encounters: int = Field(ge=0)
    attempted_encounters: int = Field(ge=0)
    eligible_patient_ids: list[str]
    record_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    records_by_split: dict[str, int]
    output_sha256: dict[str, str]


def record_issues(record: Record) -> list[str]:
    issues = []
    source, selected = record.source, record.selected_facts
    encounter, patient = source.encounter, source.patient
    if (
        record.patient_id != patient.patient_id
        or record.encounter_id != encounter.encounter_id
        or encounter.patient_id != patient.patient_id
    ):
        issues.append("source_identity: patient/encounter mismatch")
    birth, day = patient.birth_date, encounter.start.date()
    expected_age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    if expected_age != encounter.age or day < birth:
        issues.append("source_age: incorrect encounter age")
    if encounter.encounter_class not in OUTPATIENT_CLASSES:
        issues.append("source_class: encounter is not outpatient")
    if patient.source.table != "patients" or encounter.source.table != "encounters":
        issues.append("source_provenance: context table mismatch")
    ids = []
    for group in GROUPS:
        for fact in getattr(selected, group):
            ids.append(fact.fact_id)
            expected_id = f"{PREFIX[group]}_{
                digest(
                    [
                        group,
                        fact.source.row,
                        fact.source.row_sha256,
                    ]
                )
            }"
            if fact.fact_id != expected_id or fact.source.table != group:
                issues.append("source_provenance: fact ID or table mismatch")
            if fact.patient_id != record.patient_id:
                issues.append("source_identity: fact belongs to another patient")
            if group in ("conditions", "medications"):
                is_active = (
                    condition_active(fact, encounter)
                    if group == "conditions"
                    else active(
                        fact.start,
                        fact.stop,
                        encounter.start,
                    )
                )
                if not is_active or not fact.active_at_encounter:
                    issues.append("source_temporal: selected inactive fact")
            elif fact.encounter_id != record.encounter_id:
                issues.append("source_identity: fact belongs to another encounter")
    if len(set(ids)) != len(ids):
        issues.append("source_provenance: duplicate selected fact IDs")
    canonical = CanonicalEncounter(patient=patient, encounter=encounter, relevant_facts=selected)
    try:
        note, annotations = substitute(record.generation.template, bindings(canonical, selected))
        if note != record.note.text:
            issues.append("note_substitution: note differs from authoritative substitution")
        if annotations != record.annotations:
            issues.append("annotation_offset: annotations differ from reconstructed spans")
    except TemplateError as exc:
        issues.extend(exc.issues)
    except ValueError as exc:
        issues.append(f"source_binding: {exc}")
    for annotation in record.annotations:
        if record.note.text[annotation.start : annotation.end] != annotation.text:
            issues.append("annotation_offset: note slice differs from annotation")
    if record.target != extraction_target(selected):
        issues.append("target_mismatch: target differs from expressed selected facts")
    if not record.validation.passed or record.validation.issues:
        issues.append("record_validation: record marked invalid")
    if record.generation.prompt_version != PROMPT_VERSION:
        issues.append("generation_version: unsupported prompt version")
    if record.record_id != digest(
        [
            SCHEMA_VERSION,
            record.encounter_id,
            record.note.text,
            selected.model_dump(mode="json"),
            record.generation.seed,
        ]
    ):
        issues.append("record_identity: record hash mismatch")
    return sorted(set(issues))


def leakage_count(records: list[Record]) -> int:
    splits = defaultdict(set)
    for record in records:
        splits[record.patient_id].add(record.split)
    return sum(len(values) > 1 for values in splits.values())


def load_dataset(directory: Path) -> tuple[list[Record], dict, list[dict], list[str]]:
    """Read every split and rejection; malformed artifacts produce explicit failures."""
    manifest = Manifest.model_validate_json(
        (directory / "manifest.json").read_text(encoding="utf-8")
    ).model_dump(mode="json")
    issues, records = [], []
    if manifest.get("dataset_schema_version") != SCHEMA_VERSION:
        issues.append("schema_version: unsupported manifest schema")
    for split in SPLITS:
        path = directory / f"{split}.jsonl"
        if not path.is_file():
            issues.append(f"export_missing: {path.name}")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.get("output_sha256", {}).get(path.name):
            issues.append(f"export_checksum: {path.name}")
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            try:
                record = Record.model_validate_json(line)
            except ValueError:
                issues.append(f"export_schema: {path.name}:{number}")
                continue
            if record.split != split:
                issues.append(f"export_split: {path.name}:{number}")
            issues.extend(f"{record.record_id}: {x}" for x in record_issues(record))
            records.append(record)
    rejection_path = directory / "rejected.jsonl"
    if not rejection_path.is_file():
        issues.append("export_missing: rejected.jsonl")
        rejected = []
    else:
        if hashlib.sha256(rejection_path.read_bytes()).hexdigest() != manifest.get(
            "output_sha256", {}
        ).get("rejected.jsonl"):
            issues.append("export_checksum: rejected.jsonl")
        rejected = []
        for number, line in enumerate(rejection_path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                item = json.loads(line)
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("issues"), list)
                    or not item["issues"]
                    or not all(isinstance(x, str) for x in item["issues"])
                    or not isinstance(item.get("encounter_id"), str)
                ):
                    raise ValueError("malformed rejection")
                rejected.append(item)
            except ValueError:
                issues.append(f"export_schema: rejected.jsonl:{number}")
    if leakage_count(records):
        issues.append("patient_leakage: patient belongs to multiple splits")
    for field in ("record_id", "encounter_id"):
        if len(set(getattr(r, field) for r in records)) != len(records):
            issues.append(f"export_duplicate: {field}")
    if len(records) != manifest.get("record_count"):
        issues.append("manifest_count: generated record count mismatch")
    if len(rejected) != manifest.get("rejected_count"):
        issues.append("manifest_count: rejected record count mismatch")
    if len(records) + len(rejected) != manifest.get("attempted_encounters"):
        issues.append("manifest_count: attempted encounter count mismatch")
    if Counter(r.split for r in records) != Counter(manifest.get("records_by_split", {})):
        issues.append("manifest_count: split count mismatch")
    try:
        config = Config.model_validate(manifest["config"])
        if manifest["seed"] != config.seed or manifest["renderer"] != config.renderer:
            issues.append("manifest_config: top-level metadata differs from configuration")
        if manifest["prompt_version"] != PROMPT_VERSION:
            issues.append("generation_version: unsupported manifest prompt version")
        partition = patient_splits(manifest["eligible_patient_ids"], config)
        if len(partition) != len(manifest["eligible_patient_ids"]):
            issues.append("manifest_config: duplicate eligible patient IDs")
        if manifest["attempted_encounters"] > manifest["total_eligible_encounters"]:
            issues.append("manifest_count: attempted count exceeds eligible count")
        if any(partition.get(r.patient_id) != r.split for r in records):
            issues.append("export_split: seeded patient assignment mismatch")
        if any(r.generation.seed != config.seed for r in records):
            issues.append("generation_seed: seed differs from manifest")
        if any(r.generation.renderer != config.renderer for r in records):
            issues.append("generation_renderer: renderer differs from manifest")
    except (ValueError, KeyError, TypeError):
        issues.append("manifest_config: missing or invalid configuration")
    return records, manifest, rejected, issues
