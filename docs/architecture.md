# Architecture and contracts

## Source boundary

`EHRAdapter.load(Path) -> SourceData` is the ingestion seam. `SyntheaCSVAdapter` uses stdlib CSV
parsing and returns typed patients, encounters and fact collections indexed by patient. Raw rows
are confined to ingestion; their hashes are retained. Required column sets are defined in
`ingestion.REQUIRED`. Extra columns are ignored and included in the row/file digest. Missing
optional tables are warned about; malformed present tables fail. Duplicate patient/encounter IDs,
cross-patient references, missing clinical values and reversed intervals are rejected. Conditions
whose origin encounter is unavailable retain their identifier, emit a warning and use the
documented calendar-date fallback. Other unresolved fact/encounter references remain errors.

Source provenance is `{table, row, row_sha256}`. `row` is the 1-based CSV **record** number with
header counted as row 1; it is not necessarily the physical line number for multiline cells.
SHA-256 hashes of full input files and detected columns/counts are stored in the manifest.
Fact IDs hash table, row number and original row hash. They remain stable for identical source
data and preserve distinct duplicate rows. Names are synthetic context only and are not rendered.

## Time and selection

All timestamp fields require an explicit timezone offset and normalize to UTC. Synthea condition
START/STOP fields are calendar dates, so their active interval is inclusive on both ends and
compared with the UTC encounter date. Medication intervals are inclusive UTC instants. Calendar
dates are not converted to artificial midnight instants. Age is completed calendar years at
the UTC encounter date; no encounter before birth is allowed.
When a condition starts on the current date and a resolved originating encounter starts later,
it is excluded from the earlier encounter. The known origin start is saved in the condition model
for the same check during revalidation; it is not an inferred onset timestamp. If the origin is
missing/unavailable, inclusive date filtering remains the fallback, with unresolved same-day
precision explicitly acknowledged. Medication START/STOP are already timestamped, so the
calendar-date ambiguity does not apply; their exact instant boundaries remain authoritative.

Only `ambulatory`, `outpatient` and `wellness` classes are eligible. Active conditions and
medications can originate in earlier encounters. Observations and procedures are included only
by matching encounter ID and patient identity, including events after encounter start within
the linked encounter. There is no fuzzy temporal fallback for unlinked events.

Fact selection filters by a seed/encounter/fact hash and optionally caps each group using hash
ranks. Default fraction 1 includes all relevant facts; fraction 0 includes no clinical facts.
Style selection uses a local seeded RNG. No global RNG or CSV traversal order affects selection.
The compact temporal pass scans each patient's indexed history; streaming or interval indexes
are future options if large-export performance warrants them.

## Controlled language and labels

`NoteRenderer.render(canonical, selected, style) -> RenderedNote` returns a template plus generation
metadata. The two implementations share the exact same bindings, checks and substitution code.
The offline renderer groups facts into three fixed outpatient styles. Ordered selected facts get
per-note aliases such as `COND_1`, `MED_1`, `OBS_1_NAME/VALUE/UNIT` and `PROC_1`. Aliases map to
permanent fact IDs and row provenance through bindings; exported annotations preserve that map.
Duplicate semantic values stay distinct when their source facts differ. Duplicate permanent IDs
are rejected. The LLM sees selected aliases, entity types and semantic values plus style instructions.
These values inform structure, but every factual value must be expressed as a placeholder.
Source codes, unselected facts, full patient attributes and permanent hash IDs are not in its prompt.
Every selected description, observation value and unit is required; age, sex and encounter context
are required too. Application code remains the authority over inserted values.

Checks reject unknown/missing tokens, invalid brackets, code fences, numeric literals outside
tokens, exact known semantic-value literals outside tokens, empty/oversized templates and more
than three occurrences of a token. Exact literal matching is deliberately conservative and can
reject connective words that coincide with source values; it does not detect every paraphrase.
Up to three repeats
are deliberately supported. Empty source units still require a unit token, but produce no
zero-width annotation. Unicode substitution creates half-open Python character spans directly;
there is no string-search annotation heuristic. Every rendered annotation is slice-checked.

Schema 2.0 targets contain only selected facts whose required tokens passed coverage validation.
Codes remain in selected source facts and annotation metadata, but not default prose or the primary
extraction target. Schema 1.0 artifacts must be rebuilt rather than silently reinterpreted. Observation values
remain lexical strings; no float conversion or unit normalization occurs. Source temporal fields,
systems, reasons, demographics and provenance are not silently added to extraction targets.

LLM retry failures are recorded by issue category without persisting arbitrary provider errors.
Rejected encounters go to `rejected.jsonl`. Accepted templates and metadata are cached; cache hits
are revalidated. The cache key covers provider/model, settings, prompt version, seed, source
context and selection. This replays accepted output; it does not make fresh LLM calls reproducible.
Transport uses a configured HTTPS Chat Completions endpoint, environment credentials, a response
size limit, timeouts and no redirects. It rejects nontext or truncated responses.

## Partitions and export

Patients in the full eligible cohort are ordered by seed/patient hash. Largest-remainder
allocation assigns patient counts to the configured proportions (default 80/10/10), with stable
ties. The complete patient assignment is made before applying the encounter limit or rejecting
generation. Changing the cohort can change partition assignments; changing the record limit
cannot. Changing the seed can change both partitions and style/selection. Small cohorts may
have empty partitions, and encounter counts need not follow patient proportions exactly.
When a limit is smaller than the cohort, patients and their encounters are hash-ranked using the
seed; round-robin chooses one encounter per patient before a second. The global limit counts
generation attempts, not accepted notes; rejections do not trigger replacement sampling. This
broadens patient representation without stratifying by split or claiming population representativeness.
Unlimited or oversized limits retain the full stable chronological output. Sampling never changes
patient assignments.

Every output record carries its selected facts, context, original template and generation
metadata. Record identity hashes schema version, encounter ID, final text, selected facts and
seed. Train/validation/test JSONL files and rejection logs are checksummed in the manifest.
Builds occur in a sibling temporary directory, revalidate before publication and refuse existing
output directories. They publish accepted records even when some generations are rejected,
with CLI exit 2 and explicit acceptance statistics. A wholly rejected run still produces an
inspectable rejection artifact; it is not a useful training dataset.

## Revalidation and reports

The saved schema, context and selections reconstruct the expected template substitution,
annotations and extraction target. Validation checks source identities, active intervals,
required tokens, exact spans, record hashes, manifest counts, seeded assignments, duplicate
records and cross-split patient leakage. Saved `validation.passed` is never trusted alone.
Corrupt records produce issues and a nonzero CLI exit; structurally malformed manifests fail
at the validated boundary. Reports are derived from loaded records, with failure issue counts
including the saved history of generation retries (not necessarily calls made during this build
when cache entries are replayed). HTML escapes note text, targets and provenance.

These checks establish structural ground truth and artifact consistency. They do not establish
clinical validity, complete hallucination detection, cryptographic authenticity, or independent
agreement with original CSV files no longer present. LLM prose may contain unsupported assertions
that token checks cannot reliably identify. Human review and downstream evaluation remain necessary.
