"""Measured statistics and a static, escaped, annotated sample report."""

import html
import json
import statistics
from collections import Counter
from pathlib import Path

from .models import GROUPS, Record
from .selection import SPLITS
from .validation import leakage_count, load_dataset


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def metrics(records: list[Record]) -> dict:
    lengths = [len(r.note.text) for r in records]
    return {
        "generated_records": len(records),
        "patient_count": len({r.patient_id for r in records}),
        "records_by_style": dict(sorted(Counter(r.note.style.value for r in records).items())),
        "records_by_split": {s: sum(r.split == s for r in records) for s in SPLITS},
        "patients_by_split": {
            s: len({r.patient_id for r in records if r.split == s}) for s in SPLITS
        },
        "note_length_unit": "Unicode code points",
        "mean_note_length": statistics.mean(lengths) if lengths else None,
        "median_note_length": statistics.median(lengths) if lengths else None,
        **{
            g + "_frequency": dict(
                sorted(
                    Counter(f.code for r in records for f in getattr(r.selected_facts, g)).items()
                )
            )
            for g in GROUPS
        },
    }


def sample_html(records: list[Record], passed: bool) -> str:
    examples = []
    # Prefer one example per style before filling the bounded preview.
    ordered = []
    seen = set()
    for record in records:
        if record.note.style not in seen:
            ordered.append(record)
            seen.add(record.note.style)
    ordered += [r for r in records if r not in ordered]
    for record in ordered[:12]:
        pieces, cursor = [], 0
        for annotation in sorted(record.annotations, key=lambda a: a.start):
            # Corrupt datasets can still be inspected; never trust offsets as HTML.
            if annotation.start < cursor or annotation.end > len(record.note.text):
                continue
            pieces.append(html.escape(record.note.text[cursor : annotation.start]))
            title = f"{annotation.entity_type} · {annotation.source_table}:{annotation.source_row}"
            pieces.append(
                f'<mark title="{html.escape(title, quote=True)}">'
                f"{html.escape(record.note.text[annotation.start : annotation.end])}</mark>"
            )
            cursor = annotation.end
        pieces.append(html.escape(record.note.text[cursor:]))
        annotation_json = json.dumps([a.model_dump() for a in record.annotations], indent=2)
        examples.append(
            f"<article><h2>{html.escape(record.note.style.value)} · {record.split}</h2>"
            f"<p>{html.escape(record.encounter_id)} · validation: "
            f"{'passed' if passed else 'FAILED — see validation_report.json'}</p>"
            f"<pre>{''.join(pieces)}</pre><details><summary>Extraction target</summary>"
            f"<pre>{html.escape(record.target.model_dump_json(indent=2))}</pre></details>"
            "<details><summary>Fact annotations and lineage</summary>"
            f"<pre>{html.escape(annotation_json)}"
            "</pre></details></article>"
        )
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ClinicalNoteForge sample report</title><style>
body{font:16px system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;
background:#f5f7fa;color:#182637}article{background:white;padding:1.5rem;margin:1.5rem 0;
border:1px solid #ccd5df;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;
line-height:1.7}mark{background:#d4eef0;color:#152f36;padding:.1em}summary{cursor:pointer}
</style><h1>ClinicalNoteForge</h1><p>Synthetic data only. Not clinically validated.
Highlights are authoritative inserted facts; hover for source table and row.</p>"""
        + "".join(examples)
        + "</html>"
    )


def report(directory: Path) -> dict:
    records, manifest, rejected, issues = load_dataset(directory)
    statistics_data = metrics(records)
    failure_messages = [x for r in records for x in r.generation.failures]
    failure_messages += [x for r in rejected for x in r["issues"]]
    failure_messages += issues
    attempted = len(records) + len(rejected)
    validation = {
        "passed": not issues,
        "issues": issues,
        "total_eligible_encounters": manifest.get("total_eligible_encounters"),
        "attempted_encounters": attempted,
        "generated_records": len(records),
        "rejected_records": len(rejected),
        "generation_acceptance_rate": len(records) / attempted if attempted else None,
        "placeholder_validation_failures": sum("placeholder_" in x for x in failure_messages),
        "annotation_offset_failures": sum("annotation_offset:" in x for x in failure_messages),
        "target_validation_failures": sum("target_mismatch:" in x for x in failure_messages),
        "missing_selected_fact_failures": sum(
            "missing_selected_fact:" in x for x in failure_messages
        ),
        "patient_leakage_count": leakage_count(records),
        "failure_metric_unit": "issue occurrences (including retried generation attempts)",
        **statistics_data,
    }
    write_json(directory / "statistics.json", statistics_data)
    write_json(directory / "validation_report.json", validation)
    samples = directory / "samples"
    samples.mkdir(exist_ok=True)
    (samples / "examples.html").write_text(sample_html(records, not issues), encoding="utf-8")
    return validation
