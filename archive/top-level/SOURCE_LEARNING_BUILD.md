# Agere: source-grounded training acquisition

October 9, 2026. All source data, model examples and measurements are synthetic.
The full intended Agere stack remains unfinished.

## New executable path

`learning_queue.py` now connects the current received warehouse to the existing
statement-reader/review/training path. `/learn` offers source/lane filters, exact
source inspection, model comparison, explicit phrase marking and the existing
accept/correct/unresolved/reject controls. Marking a phrase defines a task; it
does not create a label. Saving a model proposal also creates no reviewed label.
Only an explicit source review can enter the existing reviewed-training export.

The generator ancestry register for the attached ecosystem-v2.2 release was
matched to the warehouse release hash and exact patient roster. An isolated
preparation function copies only `patient_id`, `source_family` and `split` from
that register. Clinical truth, scenarios, latent outcomes and task answers are
not exported or passed to the queue/readers. The ancestry file is stored by the
generator alongside its hidden data; its three-field metadata use is explicit
and separate from the observed-data runtime.

The queue excludes reserved patient IDs and whole reserved/calibration families
from the frozen Kev/d1 experiment, plus all generator calibration/validation
families. Only the generator training partition supplies acquisition examples.
This does not establish independence from older annotation-model development:
these are development sources, not a new clinical evaluation cohort.

Candidate selection scans received fields and existing annotation spans, checks
their source hashes/offsets, deduplicates normalized full-field/concept wording,
and samples across source/concept strata. Simulator document names/IDs and numbers
are masked only for grouping; source text reaching a reader is unchanged. This
is a heuristic and does not guarantee semantic deduplication. Patient/family caps
prevent repeated variants from dominating. The independent audit lane does not
use disagreement scores; a separate extraction-audit lane retains received fields
with no annotation. Neither lane is a prevalence sample or a retrieval benchmark.

Queue manifests pin source, reader, lineage policy, protection register and code.
Inspection rechecks the current source. A changed source/protection/code artifact
requires rebuilding. Marked targets preserve exact Unicode code-point offsets,
including repeated phrase occurrences. They are stored separately from reviews.

## Executed warehouse scan

Files: `artifacts/agere-source-learning-v1/lineage/manifest.json` and
`artifacts/agere-source-learning-v1/queue-deduplicated/run.json`.

| Quantity | Recorded result |
|---|---:|
| Synthetic warehouse patients / generator source families | 10,000 / 1,614 |
| Patients in generator training partition | 7,247 |
| Training patients remaining after reserved-family protections | 7,001 |
| Received records scanned | 313,586 |
| Existing annotation occurrences scanned | 103,163 |
| Records excluded by reserved-family protections | 11,029 |
| Records excluded by other nontraining partitions | 81,528 |
| Eligible received text fields | 146,262 |
| Eligible fields without annotations | 121,671 |
| Unique eligible normalized templates | 30,627 |
| Selected tasks / patients / source families | 96 / 95 / 91 |
| Extraction audit / score-independent audit / interpretation priority | 24 / 24 / 48 |

The 96 tasks span 12 source streams: ADT, allergies, coverage, documents,
encounters, labs, medication history, medication orders, notes, procedure orders,
referrals and vitals. Many unannotated fields contain routine or nonclinical
language. **121,671 is not a count of missed clinical findings or missing care.**
It measures transport fields outside this existing annotation inventory.

An initial grouping receipt remains in `queue/`; the attached queue is
`queue-deduplicated/`. The second version removes simulator identifiers from
grouping. No model outcomes were used to choose that duplicate rule or the queue.

## Bounded model suggestions

`build_source_learning.py suggest` executed the first eight marked tasks in the
frozen acquisition order with the lexical student, MiniLM student, Perplexity and
Jev. The same eight sources were presented to each reader; failures remain in the
attempt record. There were 32 attempt rows and 29 successful saved readings:

| Reader | Saved readings | Explicit failures |
|---|---:|---|
| Lexical student | 8 | None in this batch |
| MiniLM student | 6 | Two full contexts exceeded its token limit; no truncation |
| Perplexity | 8 | None in this batch |
| Jev | 7 | One response failed the existing probability-distribution validator |

The failed Jev time probabilities summed to 0.99 under a contract requiring a
normalized distribution. The raw response and failed reservation remain saved.
This is a provider-response format/validation failure, not a measured clinical
interpretation error. No paid retry or offline label acceptance was performed.

Five of seven completed Jev/Perplexity pairs matched across all axes; two differed.
This is agreement accounting, not accuracy or a model winner. None became a
consensus label. The UI shows model proposals and execution failures separately.

There were 16 hosted attempts, 15 validated responses and one failed response.
They added $8.00 in conservative budget reservations. The existing dated pricing
table estimates $0.000937922 for the 15 validated responses only. That estimate
excludes failed-response cost, local computation and review effort, and is not an
invoice. All 29 successful outputs replayed from exact caches with zero new paid
calls. Details are in `acquisition-summary.json` and the queue's suggestion receipt.

## What this exposed

One selected document occurrence is the word “Palliative” in the clinician's
specialty/signature block. Its surrounding document also describes a consultation.
Some readers proposed completed care for that marked occurrence. The distinction
between the exact target's role and nearby clinical content needs structural
context; a matching medical term does not establish an event. This example remains
unreviewed and does not enter clinical state or training as an accepted label.

The live browser also exercised an unannotated ADT `reason` field, marked the exact
phrase “acute illness assessment,” and called the lexical reader. Its proposed
care-completion label remains unreviewed; the short field alone does not establish
that proposed relation. This proves the acquisition path can reach content outside
the prior concept extractor, not that the student's interpretation is correct.

There are now 42 saved current-contract source readings and zero human review
revisions. This increment creates acquisition capability and machine proposals;
it does not train additional weights or claim improved accuracy. Existing student
model manifests and the reserved Kev/d1 experiment remain unchanged.

## Verification and next build

155 focused checks cover the queue plus training, source readers, state composition,
assertion reconciliation, chart passes and analysis graphs. Queue-specific checks
cover ancestry exclusion, nontraining partitions, missing lineage, exact source
and Unicode offsets, repeated phrase marking, deduplication, stale-source refusal,
immutable inputs, and separation of execution failures from interpretation.
The browser exercised filtering, source inspection, target marking, local inference,
reader comparison and visible failure reporting. No review was fabricated.

Next: add section/role-aware source context for body text, signatures, lists,
copied history and competing events, then expand source-grounded skill training.
Teacher suggestions can support supervision with explicit machine provenance;
they must not be silently promoted to clinician reviews. Keep fresh evaluation
families separate and keep retrieval/extraction failures apart from interpretation.
Continue broader cross-source clinical-state and program/outcome bindings as shared
capabilities; HF/CKD remain examples, not the scope limit.

```sh
.venv/bin/python build_source_learning.py build --release /Users/mikel/synthetic-patient-ecosystem/releases/ecosystem-v2.2 --output artifacts/NEW_SOURCE_LEARNING_RUN --size 96
.venv/bin/python build_source_learning.py suggest --maximum-items 8 --providers student_lexical student_minilm perplexity jev --live
```

Suggestion runs write separate attempt receipts. Failed provider reservations are
retained; successful exact requests replay. No automatic retraining or care
execution is triggered by preparing or labeling a queue.
