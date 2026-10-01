"""Build in a staging directory, verify, then publish a complete artifact."""

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path

from . import __version__
from .config import Config
from .ingestion import SyntheaCSVAdapter, digest
from .models import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    CanonicalEncounter,
    Note,
    Record,
    Source,
    Validation,
)
from .rendering import (
    DeterministicTemplateRenderer,
    GenerationError,
    LLMRenderer,
    NoteRenderer,
    TemplateError,
    bindings,
    extraction_target,
    substitute,
)
from .reporting import report, write_json
from .selection import (
    SPLITS,
    canonical_encounters,
    choose_style,
    patient_splits,
    sample_encounters,
    select_facts,
)
from .validation import record_issues


def make_record(
    canonical: CanonicalEncounter, config: Config, split: str, renderer: NoteRenderer
) -> Record:
    selected = select_facts(canonical, config)
    style = choose_style(canonical.encounter.encounter_id, config)
    rendered = renderer.render(canonical, selected, style)
    note, annotations = substitute(rendered.template, bindings(canonical, selected))
    record = Record(
        record_id=digest(
            [
                SCHEMA_VERSION,
                canonical.encounter.encounter_id,
                note,
                selected.model_dump(mode="json"),
                config.seed,
            ]
        ),
        patient_id=canonical.patient.patient_id,
        encounter_id=canonical.encounter.encounter_id,
        split=split,
        note=Note(style=style, text=note),
        target=extraction_target(selected),
        selected_facts=selected,
        annotations=annotations,
        source=Source(patient=canonical.patient, encounter=canonical.encounter),
        generation=rendered.generation,
        validation=Validation(passed=True),
    )
    if issues := record_issues(record):
        raise TemplateError(issues)
    return record


def build(
    input_dir: Path, output_dir: Path, config: Config, renderer: NoteRenderer | None = None
) -> dict:
    if output_dir.exists():
        raise ValueError("output already exists; choose a new directory to preserve existing data")
    data = SyntheaCSVAdapter().load(input_dir)
    eligible = canonical_encounters(data)
    if not eligible:
        raise ValueError("no eligible outpatient encounters")
    partitions = patient_splits((e.patient.patient_id for e in eligible), config)
    renderer = renderer or (
        LLMRenderer(config) if config.renderer == "llm" else DeterministicTemplateRenderer(config)
    )
    records, rejected = [], []
    attempted = sample_encounters(eligible, config)
    for canonical in attempted:
        try:
            records.append(
                make_record(
                    canonical,
                    config,
                    partitions[canonical.patient.patient_id],
                    renderer,
                )
            )
        except (GenerationError, TemplateError) as exc:
            rejected.append(
                {
                    "encounter_id": canonical.encounter.encounter_id,
                    "patient_id": canonical.patient.patient_id,
                    "issues": exc.failures if isinstance(exc, GenerationError) else exc.issues,
                }
            )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".clinicalnoteforge-", dir=output_dir.parent
    ) as temporary:
        stage = Path(temporary) / "dataset"
        stage.mkdir()
        for split in SPLITS:
            (stage / f"{split}.jsonl").write_text(
                "".join(r.model_dump_json() + "\n" for r in records if r.split == split),
                encoding="utf-8",
            )
        (stage / "rejected.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rejected),
            encoding="utf-8",
        )
        manifest = {
            "dataset_schema_version": SCHEMA_VERSION,
            "package_version": __version__,
            "prompt_version": PROMPT_VERSION,
            "seed": config.seed,
            "renderer": config.renderer,
            "config": config.model_dump(mode="json"),
            "source_files": data.files,
            "source_warnings": data.warnings,
            "total_eligible_encounters": len(eligible),
            "attempted_encounters": len(attempted),
            "eligible_patient_ids": sorted(partitions),
            "record_count": len(records),
            "rejected_count": len(rejected),
            "records_by_split": {s: Counter(r.split for r in records)[s] for s in SPLITS},
            "output_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(stage.glob("*.jsonl"))
            },
        }
        write_json(stage / "manifest.json", manifest)
        validation = report(stage)
        if not validation["passed"]:
            raise ValueError(f"built dataset failed validation: {validation['issues']}")
        stage.rename(output_dir)
    return validation
