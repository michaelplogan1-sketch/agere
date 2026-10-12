# Agere: pre-index evidence connected to outcome analysis

October 9, 2026. All patients, source records and runs are synthetic. Current
release: `artifacts/agere-baseline-stack-v1/`.

## How this implements the design

This is the connection between the product plan's longitudinal evidence layer,
forecasting inputs, missingness accounting and economical reuse. The prior build
connected clinical inputs to protocol review and program proposals. This increment
connects those reusable input primitives to each patient's outcome index and
exports separate candidate feature and target files. It does not replace the
existing models, narrow the product to one disease, or complete the whole stack.

The new `baseline` graph operation consumes a direct upstream `outcome` population.
The outcome node supplies the registered index definition and each patient's index
date. A registered baseline profile declares the window. Every matched patient is
accounted for; no resolved index means unavailable baseline, not a fabricated zero
or normal result. Unknown upstream membership remains visible in the input receipt.

`baseline_evidence.py` joins existing `clinical_inputs` mappings and reducers,
source-bound longitudinal note annotations, `medication_reconciliation` and the
Poisson block routine in `source_blocks`. Source names and class vocabulary live in
`baseline_input_maps.json`, separate from reusable evaluation; temporal policy is
in `baseline_profiles.json`. The initial adapter retains unmapped sources/classes.

The initial profile uses records with both receipt and source dates before the
index day, within a 180-day source-date window. Same-day records are excluded
because verified within-day order is unavailable. Later pharmacy adjudication
dates cannot override earlier transaction state. Unusable numeric values,
conflicting measurements, missing clinical dates and unreviewed note interpretations
retain their statuses. A fact dependent on a later source is excluded as a whole,
not rewritten by trimming away inconvenient evidence. Repeated text families are
identified separately from counts of original source annotations.

## Mathematics with a concrete input

The existing penalized Poisson segmentation now runs on the selected baseline
records, with calendar exposure clipped to the actual feature window and cutoff.
For a block with count N and calendar exposure T, the variable part of its fit is
N log(N/T), combined with the existing block penalty. The implementation keeps the
existing assumptions and uncalibrated penalty visible. This is an established
method, not a new mathematical discovery. It describes received-record intensity;
it does not prove clinical deterioration, source completeness or intervention benefit.

## Executed population results

Counts below come from `artifacts/agere-baseline-stack-v1/run.json` and the matching
`graph-1.json` through `graph-3.json`.

| Baseline task | Evaluated | Baseline available | No resolved index | Included source records | Retained machine annotations |
|---|---:|---:|---:|---:|---:|
| Before admission notifications | 10,000 | 144 | 9,856 | 2,132 | 889 |
| Before discharge notifications | 10,000 | 144 | 9,856 | 2,276 | 889 |
| Explicit clinical index dates | 10,000 | 0 | 10,000 | 0 | 0 |

For the admission task, 692 records received on/after the index day, 2,258 records
before the declared lookback and 144 index records were excluded. These record
counts concern the indexed patients; unindexed patients are not assigned a
fabricated time window. For discharge, the corresponding counts are 548, 2,258 and
144. The difference includes admission observations legitimately preceding a later
discharge index; it is not a different patient denominator.

The first inspected synthetic patient, ECO-001903E3CDE9, has a 2026-08-20 cutoff,
21 included records and 101 candidate feature columns. Exact source quotations,
measurement states, dispense reconciliation and arrival blocks are retained in
`patient-1.json`. Numeric feature values only use supported literal structured
measurements; note annotations remain explicitly unvalidated machine proposals.
Counts of annotations and text families measure observed machine outputs, not
confirmed diagnoses or independent clinical events.

## Training interface and economics

Each of the three `dataset-*` folders contains 10,000 feature rows and 10,000
separately stored target rows, plus a SHA-256 manifest. Only `values` contains
candidate predictor columns. Patient/receipt IDs are join keys, never predictors.
Outcome labels and endpoint details do not enter those feature values. File joins
are explicit and source receipts remain inspectable.

The first export has 144 rows with baseline observations and mature recorded
labels, all positive; the second has 144, all recorded zeros; the third has none.
A recorded zero means no received matching endpoint, not no clinical event. These
exports therefore do not justify fitting a discriminative model. Protected family
partitions, template independence, prospective ascertainment, source capture and
independent temporal/clinical evaluation also remain unresolved by the export.
It is a candidate data interface, not an approved training release.

No model weights were fitted or changed. Existing frozen Kev/d1 experiments,
reserved source cohorts, research predictor artifacts, employer environment and
the other chat's generator were not modified. Zero paid API calls were used.
Exact graph replay and persistent per-patient reuse were verified. Replay still
hashes inputs and incurs real local CPU/disk cost; API-free is not cost-free.

The real-catalog frontier request with a 1,500-character ASCII question is
19917 bytes, within the existing 20,000-byte request bound. Repeated definition
field names and redundant wording were compacted without raising that bound.
This is a captured payload check, not a paid call or measured token invoice.

## Verification and limits

139 focused checks passed across baseline assembly, outcomes, clinical protocol
review, graph composition, planning, program work and existing research prediction.
They cover late arrival, same-day ambiguity, future transactions, exact source
binding, copied text, measurement conflicts, partial-month exposure, full-set
execution, changed-input invalidation and feature/label separation. This is
software verification, not clinical accuracy. HTTP, browser and file-hash details
are in `implementation-receipt.json`.

The current snapshot lacks superseded/quarantined historical versions. Filtering
current records cannot reproduce the complete warehouse as it stood in the past.
Retrospective index selection can also use an index record that arrived later;
this does not establish a deployable prospective index trigger. The chart readers
were not independently validated for this task, and retrospective extraction is
not evidence the model was deployed at the index. Existing source and clinical
clock distinctions remain visible.

Next shared build: broaden supported longitudinal clinical/medication relations
and versioned clinical/payer prerequisites, then connect program participation,
attribution and outcome/payment evidence. Richer protected synthetic outcomes and
family-separated supervision are needed before a credible new predictive or
interpretation model release. Clinical eligibility, program eligibility, payment
eligibility and action execution remain distinct.
