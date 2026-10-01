# ClinicalNoteForge data card — schema 2.0

## Purpose and intended use

ClinicalNoteForge produces synthetic clinical-note/structured-JSON pairs for software development,
NLP experimentation and educational/research portfolio use. A planned downstream project will
fine-tune and evaluate a small information-extraction model using `note.text` and `target`.
This card describes the generation pipeline and the bundled smoke-test artifact, not a large
released population dataset.

**Source patients are synthetic. Generated notes are synthetic. The dataset must not be treated
as representative of all real-world clinical documentation. No claim is made that generated
language has been clinically validated.**

## Origin and composition

The adapter accepts structured Synthea-compatible CSV exports. The included fixtures were
fabricated specifically for this repository; they were not downloaded, extracted from an EHR,
or produced by running Synthea. Names and IDs are explicitly artificial. No real patient data,
SSNs, contact information or credentials are supplied. Terminology-like descriptions and codes
are examples, not a validated coding reference.

The verified fixture has 10 patients, 21 encounters, 11 condition rows, 11 medication rows,
23 observation rows and 10 procedure rows. Twenty encounters are outpatient-eligible; one
inpatient encounter is excluded. Two encounters per eligible patient exercise longitudinal
history. One unlinked observation is deliberately excluded. Numeric and text observations,
empty units, a stopped condition and a future medication exercise representation and timing.

The seed-42 template build contains 20 notes: 16 train, 2 validation and 2 test. It has 10 concise,
6 SOAP-style and 4 narrative notes, zero rejections and zero patient leakage. Acceptance is 100%;
mean/median note length is 269.95/264 Unicode code points. These measured structural results do
not measure clinical accuracy. The fixture is too small for meaningful model training or
population/generalization conclusions.

## Generation method

CSV normalization validates schemas, references and typed intervals. Encounter-specific temporal
selection finds active conditions/medications and linked observations/procedures. A configured
seed determines allowed fact subsets and note styles. Default selection includes all relevant
facts; it does not attach lifetime history indiscriminately.

Offline templates or an optional compatible LLM generate placeholder templates. All selected
descriptions, measurements and units are inserted by application code from source facts.
The renderer cannot independently rewrite those inserted values. The LLM receives short per-note
aliases, entity types and semantic values of selected facts to understand context, while remaining
prohibited from independently writing those values or inventing clinical content. Source codes
and permanent IDs are not sent to the model; they remain provenance metadata. Semantic inputs
are data, not instructions, and require a provider suitable for synthetic data handling.
Templates must satisfy deterministic coverage/syntax checks. Invalid generations are retried
and/or logged as rejections and excluded from the training files. Accepted LLM templates are
cached, with metadata and revalidation on reuse.

Annotations are produced during substitution. Targets are restricted to facts expressed through
required placeholders. Source codes are not appended to ordinary clinical prose and are excluded
from the primary extraction target; this separates note-expressed information from terminology
metadata rather than training trivial code copying. No dates, clinical
interpretations, symptoms, adherence, new treatment recommendations, doses or frequencies are
inferred. Medication strengths already embedded in source descriptions are retained verbatim.

## Fields and schema

The complete versioned contract is `examples/record.schema.json`. Each record contains:

- `schema_version`, content-derived `record_id`, `patient_id`, `encounter_id`, and `split`.
- `note`: style and plain-text extraction input.
- `target`: conditions/medications/procedures with description; observations also have
  value and unit. Values stay strings to preserve precision and text. Missing units are empty.
- `selected_facts`: normalized allowed source facts, timing, IDs and row-level provenance.
- `annotations`: inserted descriptions, measurement names/values/units and context spans, plus
  permanent fact IDs and source codes as metadata. The placeholder field holds a short alias.
  Offsets are zero-based, half-open Unicode code points and always slice-verified.
- `source`: synthetic source designation plus typed patient/encounter context and provenance.
- `generation`: renderer/provider/model, prompt version, seed, settings, original template,
  attempts, failure history and cache use.
- `validation`: per-record structural pass status and issues.

Source patient context can include synthetic names, race and ethnicity; these are not rendered or
placed in extraction targets. Additional CSV columns are not propagated as raw rows. Source row
hashes cover those original rows; source-file hashes and observed counts/headers are in the manifest.
If encounter-linked observations themselves contain synthetic demographics or addresses, those
selected observation values can appear in notes. No real input data is permitted by intended use.

Schema 2.0 intentionally changes the primary target contract from schema 1.0, which required codes.
Old artifacts are not silently reinterpreted or migrated; rebuild from the original source. Numeric
observation values remain lexical strings. Synthetic-to-real generalization is unknown unless
separately evaluated; grounding does not guarantee clinical realism.

## Train/validation/test strategy

Patients, not notes or encounters, are assigned to exactly one partition. The seed orders the
full eligible patient cohort; largest-remainder allocation applies default 80/10/10 proportions.
All eligible encounters for a patient retain that partition even with limits or rejections.
When limiting encounters, seeded hash ranks order patients and each patient's encounters, then a
round-robin selects one encounter per patient before a second. This avoids earliest-record bias
and reduces domination by high-encounter patients. It is not a population-representative sample
and does not stratify small samples to guarantee nonempty splits. Unlimited builds retain all
eligible encounters in stable chronological order.
Automatic validation fails on cross-split patient leakage. Small cohorts or encounter limits may
produce empty splits. Cohort changes can change assignments. Avoid repartitioning the exported
notes or treating the fixture's two held-out patients as meaningful evaluation evidence.

## Provenance and reproducibility

Facts retain source table, row record number and row hash. Patient and encounter identifiers
remain available. Fact IDs incorporate source row identity, preserving duplicate source rows;
row reordering changes those IDs. JSONL checksums, package/schema/prompt versions, config, seed,
source file hashes, eligible patient IDs and build counts are recorded in the manifest.

The offline renderer is reproducible for identical source/config/seed; byte-equivalence is tested.
LLM calls are not inherently reproducible; accepted cache entries replay prior output with
provider/model/settings and failure history. Secrets remain environment variables. Reported
checksums establish consistency with a saved manifest, not authenticity or independent medical
truth. Revalidation reconstructs outputs from saved facts rather than rereading original inputs.

## Known limitations and potential biases

Synthea models and this fixture may simplify disease trajectories, coding, demographics,
comorbidity and care pathways. The included ten adult-like example patients, three repeated
condition/medication patterns and binary recorded sex values are intentionally limited and
not demographic coverage. No claim of fairness or representation is supported.

Template language is repetitive; source terminology descriptions can still make extraction easier
than real documentation even without exposed codes. Lack of clinical shorthand, negation,
uncertainty, misspellings, contradictory
notes and genuine documentation context creates domain mismatch. Models trained on this data
could learn template/coding shortcuts and fail on real notes. Do not infer generalization or
clinical performance from synthetic acceptance rates.

Condition dates have day-level precision. On the start day, a known later originating encounter
excludes the condition from earlier encounters. The resolved origin start is saved for validation;
it is not fabricated as a diagnosis timestamp. Unavailable origins use inclusive calendar-date
fallback, so unknown same-day ordering remains unresolved. Medication timestamps are precise
UTC instants and retain inclusive start/stop checks. Source-coded social/history findings remain
conditions as supplied; the pipeline does not reinterpret them as clinically validated diagnoses.

Placeholder checking cannot reliably detect every unsupported assertion in LLM prose. Negation,
causality, spelled-out values and invented clinical statements may evade structural checks.
All inserted facts can be correct while the overall note is medically implausible. Human review
and appropriate downstream evaluations are needed. Synthetic generation is not evidence of
de-identification quality, privacy certification or absence of all possible resemblance to reality.

## Inappropriate uses

Do not use these notes for patient care, diagnosis, prescribing, clinical decision support,
automated medical advice, compliance claims or claims of clinical validation. Do not treat this
pipeline as a de-identification tool or feed real patient records into it. Do not use the tiny
fixture to substantiate research performance, demographic representativeness or deployment safety.

## Licensing considerations

Original pipeline code and repository-created fixtures/examples are offered under MIT. No
third-party implementation or external dataset is redistributed here. Dependencies, Synthea,
clinical terminology systems and datasets generated elsewhere have their own licensing and
usage conditions; users must evaluate those for their chosen inputs and outputs. This project's
license does not confer rights to arbitrary healthcare records or terminology assets.
