# Finalization evidence — October 1, 2026

## Baseline and changes

Before edits, 46 tests, Ruff and the four offline CLI operations passed. There was no Git history;
project files were untracked. The correction pass preserves the package architecture and permanent
fact IDs, introduces schema/prompt 2.0, supplies semantic short-alias prompts, separates codes from
primary extraction labels/prose, refines known same-day condition ordering and balances limited
encounter sampling across patients. Schema 1.0 artifacts must be rebuilt from source.

## Executed checks

Commands below use the installed CLI from the project virtual environment. Outputs were checked,
not just submitted. Paths are repository-relative; external data lives under ignored `.cache/`.

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest -q

clinicalnoteforge inspect --input tests/fixtures/synthea
clinicalnoteforge build --input tests/fixtures/synthea --output output/final-v2 --renderer template --seed 42
clinicalnoteforge validate output/final-v2
clinicalnoteforge report output/final-v2

clinicalnoteforge inspect --input .cache/official-synthea
clinicalnoteforge build --input .cache/official-synthea --output output/official-synthea-final --renderer template --seed 42 --limit 100
clinicalnoteforge validate output/official-synthea-final
clinicalnoteforge report output/official-synthea-final

python -m build
```

The full suite passes **75 tests**. Ruff lint and formatting pass. The source distribution and
`clinicalnoteforge-2.0.0-py3-none-any.whl` build successfully. The wheel was installed into a fresh
`.cache/wheel-env` environment, with the offline CLI exercised against the bundled fixture.

| Measurement | Fabricated fixture | Official sample, limit 100 |
| --- | ---: | ---: |
| Input patients | 10 | 108 |
| Input encounters | 21 | 5,571 |
| Eligible outpatient encounters | 20 | 4,960 |
| Selected attempts / accepted / rejected | 20 / 20 / 0 | 100 / 100 / 0 |
| Train / validation / test records | 16 / 2 / 2 | 79 / 11 / 10 |
| Unique patients by split | 8 / 1 / 1 | 79 / 11 / 10 |
| Concise / SOAP / narrative | 10 / 6 / 4 | 38 / 29 / 33 |
| Mean / median characters | 269.95 / 264 | 1322.43 / 749.5 |
| Placeholder / missing-fact / offset / target failures | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Patient leakage | 0 | 0 |
| Generation acceptance | 100% | 100% |

Lengths are Unicode code points. These are structural measurements, not clinical accuracy.
Every final accepted annotation slice and target reconstructs correctly. Representative notes
were inspected across all styles and splits, including medications, observations and procedures.
All 2,355 selected facts in the official run were compared against normalized input values and
row provenance: 720 conditions, 220 medications, 1,171 observations and 244 procedures.
The official sample includes social/history findings and synthetic demographic observations;
their source classifications are retained rather than clinically reinterpreted.

## Official Synthea evidence

The [official downloads page](https://synthetichealth.github.io/downloads.html) linked the
[latest CSV sample ZIP](https://synthetichealth.github.io/synthea-sample-data/downloads/latest/synthea_sample_data_csv_latest.zip).
The retrieved archive SHA-256 was:

```text
d61417b551e5b0997c33851b339c157421751f0ea68c18ea686ceb1850907c35
```

Although advertised as a 100-patient sample, the actual export contains 108 patient rows. All
six supported tables loaded: 108 patients, 5,571 encounters, 3,517 conditions, 3,850 medications,
68,648 observations and 15,884 procedures. Other CSV tables were tolerated. No input schema
warnings occurred. The sample and its generated notes are not committed or included in packages.
This verifies the currently linked export, not every Synthea version or configuration. Current
[CSV constants](https://github.com/synthetichealth/synthea/blob/master/src/main/java/org/mitre/synthea/export/CSVConstants.java)
and [note exporter](https://github.com/synthetichealth/synthea/blob/master/src/main/java/org/mitre/synthea/export/ClinicalNoteExporter.java)
were also inspected. Synthea was not installed or run locally.

## LLM evidence and limits

No compatible `CLINICALNOTEFORGE_API_KEY` or `OPENAI_API_KEY` was available in the environment.
No live provider call was executed. The online integration is implemented and tested through
deterministic/mocked prompt, compatible-HTTP, semantic-alias, narrative-variation, cache,
literal-value, malformed-response, retry and rejection tests. These tests do not establish
real-provider acceptance rates or real-LLM linguistic quality.

The LLM sees selected semantic values for context but must output placeholders. Structural
checks protect inserted values and reject detectable violations; they cannot certify every
clinical assertion, negation, paraphrase or interpretation. Same-day condition ordering remains
unknown when no origin encounter can be resolved. Synthetic-to-real generalization and clinical
validity remain unmeasured.

## Project 2 handoff

Use `output/final-v2/train.jsonl`, `validation.jsonl` and `test.jsonl`, schema **2.0**.
The same three filenames exist for larger builds. Pair `note.text` with `target` directly:

```python
import json
from pathlib import Path

with Path("output/final-v2/train.jsonl").open(encoding="utf-8") as stream:
    pairs = [{"input": r["note"]["text"], "target": r["target"]} for r in map(json.loads, stream)]
```

Targets contain description arrays for conditions, medications and procedures; observations also
contain lexical string `value` and `unit`. Empty units remain empty strings. Terminology codes,
permanent IDs, temporal fields and provenance stay outside the primary target. Keep patient
partitions intact. The tiny fixture verifies the handoff contract; it is not a training corpus
sufficient for a useful model. Code normalization is a separate downstream objective.
