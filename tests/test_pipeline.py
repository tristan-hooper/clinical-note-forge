"""Offline contract tests, including corrupt artifacts and a mocked compatible provider."""

import csv
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clinicalnoteforge.cli import app
from clinicalnoteforge.config import Config, load_config
from clinicalnoteforge.ingestion import SyntheaCSVAdapter, timestamp
from clinicalnoteforge.models import GROUPS, NoteStyle, Record
from clinicalnoteforge.pipeline import build, make_record
from clinicalnoteforge.rendering import (
    DeterministicTemplateRenderer,
    GenerationError,
    LLMRenderer,
    TemplateError,
    bindings,
    compatible_request,
    substitute,
    validate_template,
)
from clinicalnoteforge.reporting import report
from clinicalnoteforge.selection import (
    active,
    canonical_encounters,
    patient_splits,
    select_facts,
)
from clinicalnoteforge.validation import leakage_count, load_dataset, record_issues

FIXTURE = Path(__file__).parent / "fixtures" / "synthea"


@pytest.fixture
def canonical():
    return canonical_encounters(SyntheaCSVAdapter().load(FIXTURE))[0]


def edit_csv(directory, table, edit):
    path = directory / f"{table}.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        header, rows = reader.fieldnames, list(reader)
    header, rows = edit(header, rows)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def copied(tmp_path):
    directory = tmp_path / "input"
    shutil.copytree(FIXTURE, directory)
    return directory


def test_ingestion_joins_and_preserves_source_precision():
    data = SyntheaCSVAdapter().load(FIXTURE)
    assert len(data.patients) == 10 and len(data.encounters) == 21
    encounters = canonical_encounters(data)
    assert len(encounters) == 20
    first = encounters[0]
    assert first.patient.patient_id == first.encounter.patient_id == "synthetic-p01"
    assert first.encounter.age == 62
    assert {f.value for f in first.relevant_facts.observations} == {"61.00", "8.20"}
    assert all(f.source.row >= 2 for f in first.relevant_facts.conditions)
    assert all(
        f.encounter_id == first.encounter.encounter_id for f in first.relevant_facts.observations
    )


def test_optional_tables_and_extra_columns(tmp_path):
    directory = copied(tmp_path)
    for table in GROUPS:
        (directory / f"{table}.csv").unlink()
    edit_csv(
        directory,
        "patients",
        lambda h, rows: (
            [*h, "EXTRA"],
            [{**r, "EXTRA": "ignored"} for r in rows],
        ),
    )
    (directory / "unrelated.csv").write_text("unrelated\nvalue\n")
    data = SyntheaCSVAdapter().load(directory)
    assert len(data.warnings) == 4
    assert not data.facts_by_patient
    result = build(directory, tmp_path / "dataset", Config())
    assert result["passed"] and result["generated_records"] == 20


@pytest.mark.parametrize(
    "table,column",
    [("patients", "BIRTHDATE"), ("encounters", "PATIENT"), ("observations", "VALUE")],
)
def test_missing_required_columns(tmp_path, table, column):
    directory = copied(tmp_path)
    edit_csv(
        directory,
        table,
        lambda h, rows: (
            [x for x in h if x != column],
            [{k: v for k, v in r.items() if k != column} for r in rows],
        ),
    )
    with pytest.raises(ValueError, match=f"{table}.csv: missing required columns.*{column}"):
        SyntheaCSVAdapter().load(directory)


@pytest.mark.parametrize(
    "table,change,message",
    [
        ("patients", {"Id": "synthetic-p02"}, "duplicate patient"),
        ("encounters", {"PATIENT": "unknown"}, "unknown patient"),
        ("observations", {"ENCOUNTER": "synthetic-e02-a"}, "invalid encounter/patient"),
        ("encounters", {"STOP": "2023-01-01T00:00:00Z"}, "STOP precedes START"),
        ("observations", {"VALUE": ""}, "at least 1 character"),
    ],
)
def test_invalid_input_fails_clearly(tmp_path, table, change, message):
    directory = copied(tmp_path)
    edit_csv(directory, table, lambda h, rows: (h, [{**rows[0], **change}, *rows[1:]]))
    with pytest.raises(ValueError, match=message):
        SyntheaCSVAdapter().load(directory)


def test_temporal_boundaries_and_offsets():
    day = date(2024, 1, 10)
    assert active(day, day, day)
    assert not active(day, None, day - timedelta(days=1))
    assert not active(day, day, day + timedelta(days=1))
    at = datetime(2024, 1, 10, 10, tzinfo=UTC)
    assert active(at, at, at)
    assert active(at, None, at + timedelta(seconds=1))
    assert not active(at, at, at + timedelta(microseconds=1))
    assert not active(at, None, at - timedelta(microseconds=1))
    assert timestamp("2024-01-10T05:00:00-05:00") == at
    with pytest.raises(ValueError, match="requires UTC offset"):
        timestamp("2024-01-10T10:00:00")


def test_temporal_selection_uses_history_without_future_or_unlinked_facts():
    encounters = canonical_encounters(SyntheaCSVAdapter().load(FIXTURE))
    first, followup = [e for e in encounters if e.patient.patient_id == "synthetic-p01"]
    assert {f.code for f in first.relevant_facts.conditions} == {"44054006", "10509002"}
    assert {f.code for f in followup.relevant_facts.conditions} == {"44054006"}
    assert {f.code for f in followup.relevant_facts.medications} == {"860975"}
    assert {f.code for f in followup.relevant_facts.observations} == {"29463-7", "fixture-text"}
    assert not first.relevant_facts.procedures and len(followup.relevant_facts.procedures) == 1
    assert (
        first.relevant_facts.conditions[0].fact_id == followup.relevant_facts.conditions[0].fact_id
    )


def test_medication_instant_and_condition_day_boundaries_in_join():
    data = SyntheaCSVAdapter().load(FIXTURE)
    visit = data.encounters["synthetic-e01-a"]
    med = data.facts_by_patient["synthetic-p01"].medications[0]
    med.stop = visit.start
    condition = data.facts_by_patient["synthetic-p01"].conditions[0]
    condition.start = condition.stop = visit.start.date()
    first = canonical_encounters(data)[0]
    assert med.fact_id in {f.fact_id for f in first.relevant_facts.medications}
    assert condition.fact_id in {f.fact_id for f in first.relevant_facts.conditions}
    visit.start += timedelta(seconds=1)
    shifted = next(
        e for e in canonical_encounters(data) if e.encounter.encounter_id == visit.encounter_id
    )
    assert med.fact_id not in {f.fact_id for f in shifted.relevant_facts.medications}
    assert condition.fact_id in {f.fact_id for f in shifted.relevant_facts.conditions}


def test_seeded_selection_and_targets_exclude_unselected(canonical):
    config = Config(selection_fraction=0.5, max_facts_per_group=1)
    selected = select_facts(canonical, config)
    assert selected == select_facts(canonical, config)
    assert all(len(getattr(selected, group)) <= 1 for group in GROUPS)
    record = make_record(
        canonical, Config(selection_fraction=0), "train", DeterministicTemplateRenderer(Config())
    )
    assert all(not getattr(record.target, group) for group in GROUPS)
    assert not record_issues(record)


@pytest.mark.parametrize("style", list(NoteStyle))
def test_styles_reproducible_complete_and_offsets(canonical, style):
    config = Config()
    selected = select_facts(canonical, config)
    renderer = DeterministicTemplateRenderer(config)
    rendered = renderer.render(canonical, selected, style)
    assert rendered == renderer.render(canonical, selected, style)
    allowed = bindings(canonical, selected)
    assert not validate_template(rendered.template, allowed)
    note, annotations = substitute(rendered.template, allowed)
    assert all(note[a.start : a.end] == a.text for a in annotations)
    assert {a.source_code for a in annotations if a.source_code} == {
        f.code for group in GROUPS for f in getattr(selected, group)
    }
    assert {a.fact_id for a in annotations if a.source_code} == {
        f.fact_id for group in GROUPS for f in getattr(selected, group)
    }


def test_repeated_entities_and_unicode_offsets(canonical):
    allowed = bindings(canonical, canonical.relevant_facts)
    template = "Café — " + " ".join(f"[[{key}]]" for key in allowed) + " [[AGE]]"
    note, annotations = substitute(template, allowed)
    ages = [a for a in annotations if a.placeholder == "AGE"]
    assert len(ages) == 2 and ages[0].start != ages[1].start
    assert all(note[a.start : a.end] == a.text for a in annotations)
    assert ages[0].start == len("Café — ")


@pytest.mark.parametrize(
    "mutation,issue",
    [
        (lambda t: t + " [[UNKNOWN]]", "placeholder_unknown"),
        (lambda t: t.replace("[[AGE]]", ""), "missing_selected_fact"),
        (lambda t: t + " value 8.2", "placeholder_numeric_literal"),
        (lambda t: t + " [[broken]]", "placeholder_malformed"),
        (lambda t: "```" + t, "placeholder_malformed"),
        (lambda t: t + " [[AGE]] [[AGE]] [[AGE]]", "placeholder_duplicate"),
    ],
)
def test_invalid_template_rejected(canonical, mutation, issue):
    allowed = bindings(canonical, canonical.relevant_facts)
    valid = " ".join(f"[[{key}]]" for key in allowed)
    with pytest.raises(TemplateError, match=issue):
        substitute(mutation(valid), allowed)


def test_patient_partition_deterministic_and_disjoint():
    patients = [f"p{i}" for i in range(10)]
    partition = patient_splits(patients, Config())
    assert partition == patient_splits(reversed(patients), Config())
    assert list(partition.values()).count("train") == 8
    assert list(partition.values()).count("validation") == 1
    assert list(partition.values()).count("test") == 1
    assert partition != patient_splits(patients, Config(seed=17))
    assert set(patient_splits(patients, Config(split_proportions=(1, 0, 0))).values()) == {"train"}


def test_end_to_end_export_validation_and_reproducibility(tmp_path):
    output = tmp_path / "a"
    result = build(FIXTURE, output, Config())
    assert result["passed"] and result["generated_records"] == 20
    assert result["rejected_records"] == result["patient_leakage_count"] == 0
    assert result["generation_acceptance_rate"] == 1
    assert set(result["records_by_style"]) == {style.value for style in NoteStyle}
    assert result["records_by_split"] == {"train": 16, "validation": 2, "test": 2}
    records, manifest, rejected, issues = load_dataset(output)
    assert not issues and not rejected and manifest["record_count"] == 20
    assert all(not record_issues(r) for r in records)
    assert leakage_count(records) == 0
    assert all(Record.model_validate_json(r.model_dump_json()) == r for r in records)
    html = (output / "samples" / "examples.html").read_text(encoding="utf-8")
    assert "<mark" in html and "Extraction target" in html
    second = tmp_path / "b"
    build(FIXTURE, second, Config())
    for filename in ["train.jsonl", "validation.jsonl", "test.jsonl", "manifest.json"]:
        assert (output / filename).read_bytes() == (second / filename).read_bytes()
    with pytest.raises(ValueError, match="output already exists"):
        build(FIXTURE, output, Config())


def test_limit_does_not_reassign_patient_splits(tmp_path):
    full, limited = tmp_path / "full", tmp_path / "limited"
    build(FIXTURE, full, Config())
    build(FIXTURE, limited, Config(max_records=4))
    full_records = load_dataset(full)[0]
    limited_records = load_dataset(limited)[0]
    partition = {r.patient_id: r.split for r in full_records}
    assert len(limited_records) == 4
    assert all(partition[r.patient_id] == r.split for r in limited_records)


def test_revalidation_detects_note_target_annotation_and_hidden_fact_corruption(canonical):
    record = make_record(canonical, Config(), "train", DeterministicTemplateRenderer(Config()))
    modified = record.model_copy(deep=True)
    modified.note.text += " unsupported content"
    assert any("note_substitution" in x for x in record_issues(modified))
    modified = record.model_copy(deep=True)
    modified.target.conditions[0].description = "Hidden diagnosis"
    assert any("target_mismatch" in x for x in record_issues(modified))
    modified = record.model_copy(deep=True)
    modified.annotations[0].start += 1
    assert any("annotation_offset" in x for x in record_issues(modified))


def test_dataset_leakage_and_corruption_fail_validation(tmp_path):
    output = tmp_path / "data"
    build(FIXTURE, output, Config())
    train = output / "train.jsonl"
    first = json.loads(train.read_text(encoding="utf-8").splitlines()[0])
    first["split"] = "test"
    with (output / "test.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    result = report(output)
    assert not result["passed"] and result["patient_leakage_count"] == 1
    assert any("export_checksum" in x for x in result["issues"])
    assert CliRunner().invoke(app, ["validate", str(output)]).exit_code == 1


def test_cli_offline_workflow(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["inspect", "--input", str(FIXTURE)]).exit_code == 0
    output = tmp_path / "cli"
    result = runner.invoke(
        app,
        [
            "build",
            "--input",
            str(FIXTURE),
            "--output",
            str(output),
            "--renderer",
            "template",
            "--seed",
            "42",
        ],
    )
    assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["validate", str(output)]).exit_code == 0
    assert runner.invoke(app, ["report", str(output)]).exit_code == 0
    assert (
        runner.invoke(app, ["build", "--input", str(FIXTURE), "--output", str(output)]).exit_code
        == 1
    )


def test_llm_controlled_retry_cache_and_semantic_values(monkeypatch, tmp_path, canonical):
    monkeypatch.setenv("CLINICALNOTEFORGE_API_KEY", "fake-test-key")
    config = Config(renderer="llm", model="mock-model", cache_dir=tmp_path / "cache", retries=1)
    selected = select_facts(canonical, config)
    template = (
        DeterministicTemplateRenderer(config).render(canonical, selected, NoteStyle.SOAP).template
    )
    calls = []

    def transport(url, key, payload, timeout):
        calls.append(payload)
        assert key == "fake-test-key" and timeout == 60
        user_message = payload["messages"][1]["content"]
        assert "Metformin" in user_message and "8.20" in user_message
        assert "[[COND_1]]" in user_message and selected.conditions[0].fact_id not in user_message
        return "Invented numeric value 3" if len(calls) == 1 else template

    renderer = LLMRenderer(config, transport)
    rendered = renderer.render(canonical, selected, NoteStyle.SOAP)
    assert rendered.generation.attempts == 2 and rendered.generation.failures
    cached = renderer.render(canonical, selected, NoteStyle.SOAP)
    assert cached.generation.cached and cached.template == template and len(calls) == 2
    assert "fake-test-key" not in next(config.cache_dir.glob("*.json")).read_text()
    for path in config.cache_dir.glob("*.json"):
        path.write_text("not json")
    regenerated = renderer.render(canonical, selected, NoteStyle.SOAP)
    assert regenerated.generation.failures == ["cache_invalid: regenerating"]
    assert len(calls) == 3


def test_rejected_llm_generations_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINICALNOTEFORGE_API_KEY", "fake-test-key")
    config = Config(
        renderer="llm", model="mock-model", max_records=2, retries=0, cache_dir=tmp_path / "cache"
    )
    renderer = LLMRenderer(config, lambda *args: "invalid [[UNKNOWN]]")
    output = tmp_path / "rejected"
    result = build(FIXTURE, output, config, renderer)
    assert result["generated_records"] == 0 and result["rejected_records"] == 2
    assert result["generation_acceptance_rate"] == 0
    assert result["placeholder_validation_failures"] > 0
    assert len(load_dataset(output)[2]) == 2
    with pytest.raises(GenerationError):
        renderer.render(
            canonical_encounters(SyntheaCSVAdapter().load(FIXTURE))[0],
            canonical_encounters(SyntheaCSVAdapter().load(FIXTURE))[0].relevant_facts,
            NoteStyle.CONCISE,
        )


def test_llm_credentials_explicit_and_template_default(monkeypatch):
    monkeypatch.delenv("CLINICALNOTEFORGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="requires API key"):
        LLMRenderer(Config(renderer="llm", model="mock-model"))
    assert Config().renderer == "template"


@pytest.mark.parametrize(
    "overrides",
    [
        {"styles": []},
        {"style_weights": [0, 0, 0]},
        {"split_proportions": (0.8, 0.1, 0.2)},
        {"style_weights": [float("nan"), 1, 1]},
        {"selection_fraction": -1},
        {"max_records": 0},
    ],
)
def test_invalid_config_rejected(overrides):
    with pytest.raises(ValueError):
        Config(**overrides)


def test_toml_config_overrides():
    config = load_config(
        Path(__file__).parents[1] / "configs" / "default.toml", seed=7, max_records=2
    )
    assert config.seed == 7 and config.max_records == 2


@pytest.mark.parametrize("manifest", [[], {}, {"dataset_schema_version": "1.0"}])
def test_malformed_manifest_has_clear_cli_failure(tmp_path, manifest):
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = CliRunner().invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1 and "Error:" in result.output


@pytest.mark.parametrize(
    "finish,content,valid",
    [
        ("stop", "template text", True),
        ("length", "truncated", False),
        ("stop", None, False),
    ],
)
def test_compatible_http_request(monkeypatch, finish, content, valid):
    from contextlib import nullcontext

    class Response:
        def read(self, size):
            assert size == 1_000_001
            return json.dumps(
                {"choices": [{"finish_reason": finish, "message": {"content": content}}]}
            ).encode()

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://provider.example/v1/chat/completions"
            assert request.get_header("Authorization") == "Bearer fake-test-key"
            assert json.loads(request.data) == {"model": "mock-model"}
            assert timeout == 12
            return nullcontext(Response())

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    if valid:
        assert (
            compatible_request(
                "https://provider.example/v1", "fake-test-key", {"model": "mock-model"}, 12
            )
            == content
        )
    else:
        with pytest.raises(ValueError):
            compatible_request(
                "https://provider.example/v1", "fake-test-key", {"model": "mock-model"}, 12
            )
