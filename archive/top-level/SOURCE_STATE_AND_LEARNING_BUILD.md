# Agere: documentary state and reviewed skill preparation

October 9, 2026. Personal synthetic development stack. The proposed full product
is not complete. This increment connects source meaning to a typed population
state operation and adds reproducible preparation of reviewed training examples.

## What now executes

`clinical_core/agere/clinical/source_state.py` projects existing interpretation
receipts into a specified concept, relationship and clock. It does not create a
second clinical truth store. Derived rows share `interpretation-state.sqlite3`.

The `source_state` graph node returns complete matched, nonmatched and unknown
memberships. Its output can feed the existing set, comparison, analysis and work
operators. Three authored questions compile locally in `/stack`:

- Inspect medication use reports: beta-blocker reported use, source-statement time.
- Inspect dated nephrology completion: completion accounts, explicit clinical time.
- Find documented affordability reports: reported financial barrier, source-statement time.

Each question analyzes source completeness for its entire matched group. These
are reusable relation contracts, not disease-specific eligibility rules. Supported
medication relationships include order, dispense, administration, observed use,
reported use, hold/stop and dose change; each stays separate. Care order and care
completion stay separate. Other concepts/relationships require an explicit adapter.

Literal structured fields and consistent explicit source reviews can establish
documentary accounts. Unreviewed machine interpretations remain pending. Same-date
opposing accounts and conflicting reviews do not vote. Explicitly unresolved or
incomplete reviews, stale source readings and unplaced relevant evidence prevent
an affirmative/negative membership decision. Source, clinical and literal-reference
dates are distinct selectable clocks. Copied statements cannot refresh state.

The result reports the latest usable account on its chosen clock and preserves
prior and excluded accounts. It does not infer a validity interval extending to the
warehouse cutoff, actual medication ingestion, referral fulfillment, episode identity,
clinical appropriateness, tolerance, indication, consent or payer eligibility.
Unmapped content remains visible even when an assessed report is documented.

Per-patient projection keys include the interpretation receipt, contract and code.
Changing a review invalidates affected projections and aggregate results; unrelated
patient projections are reusable. GET `/api/stack/source-state?id=...` validates
the interpretation/source revisions and exposes the exact evidence.

## Complete-population execution

| Query | Synthetic patients assessed | Affirmed matches | Explicit negative nonmatches | Unknown |
|---|---:|---:|---:|---:|
| Beta-blocker reported use | 10,000 | 0 | 0 | 10,000 |
| Explicitly dated nephrology completion | 10,000 | 0 | 0 | 10,000 |
| Documented affordability report | 10,000 | 110 | 2,022 | 7,868 |

Source: `artifacts/agere-source-state-v1/run.json`, `results[1].result.counts`.
For affordability, unknown comprises 6,666 with no usable mapped report and 1,202
with unresolved interpretation. The source layer contains 1,270 pending narrative
assertions in that scope. These counts describe this declared query and received
synthetic snapshot; they are not prevalence, clinical sensitivity or assistance
eligibility. The 110 matched patients all feed the downstream completeness analysis.

The medication and nephrology results expose absent usable relation/date bindings;
they do not say that none of these patients take treatment or received care.
Related medication orders/dispenses and care plans remain inspectable. The current
source readers have not semantically adjudicated the entire warehouse.

All three graphs ran and replayed locally with network calls blocked in the runner.
Zero new paid model calls. The graph planner schema includes the new operation;
its maximum ASCII-question request measured 19,933 bytes without calling a provider.

## Training: what exists and what this adds

Agere is a system of models, rules, retrieval and calculations, not a single trained
foundation model. Earlier builds fitted relevance/domain/context/relation/action
heads and research predictors. Their supervision and limitations differ; see
`TRAINING_BUILD_REPORT.md`, `AGERE_STACK_RECONCILIATION.md`, existing model cards
and `clinical_core/agere/training.py`. Those earlier datasets and weights are
preserved. Kev/d1 remain experimental and their frozen comparison is unchanged.

The new `semantic_learning.py` consumes the source-reader's reviewed export and
prepares the same statement/time/relation contract used by Perplexity, Jev, Kev
and d1. It adds:

1. Exact occurrence deduplication: multiple reviewed reader outputs are one example.
2. Explicit patient-family provenance and train/calibration/validation assignments.
   Patient IDs are not assumed to establish independent source families.
3. Exclusion of registered local-decision reserved/calibration families and explicit
   additional protected families. The existing frozen register is checksum-verified.
4. Connected-component checks across patient family, repeated normalized template,
   source hash and identical model input. Related material cannot bridge partitions.
5. Conflicting-label, incomplete-context and exact-target checks. Source IDs and
   review metadata remain outside the model input.
6. Immutable partition files and SHA-256 manifest. Changed/revoked reviews or source
   contracts prevent the fit-input loader from reusing the earlier snapshot.
7. Per-axis class coverage and basic fit-data readiness. The loader exposes training
   rows only; calibration and validation data are never returned as fitting rows.

This is data preparation and a guarded fit-input interface, not a new trainer,
calibrator or model-release service. Full adaptation, release thresholds and routing
integration remain to be built. The current audit contains 8 saved current-reader
outputs, 0 recorded review revisions and 0 eligible reviewed occurrences in this new
source-review workflow. This does not mean the project has no earlier training data.
No clinician review was fabricated and no new model weights were trained.

`/semantics` now has **Training readiness**, backed by
GET `/api/stack/learning-readiness`. Read-only CLI:

```sh
.venv/bin/python prepare_semantic_learning.py
```

To freeze genuinely reviewed examples after supplying a verified lineage policy:

```sh
.venv/bin/python prepare_semantic_learning.py --family-policy FAMILY_POLICY.json --output NEW_DATASET_DIRECTORY
```

The policy has `lineage_source`, `patient_families` (patient ID → source-family ID),
`family_partitions` (family ID → train/calibration/validation), and
`additional_protected_families`. Do not manufacture a lineage map or reassign a
reserved family just to obtain a nonempty training set.

## What to train next

Train stable skills with exact supervision; maintain changing knowledge externally.

| Role | Training target | Required evidence before use |
|---|---|---|
| Source reader | Subject, proposition, time, relationship, uncertainty; later spans and cross-source event links | Reviewed original context, difficult negatives, source/patient-family separation; retrieval misses scored separately |
| Retriever/reranker | Decisive supporting, contradicting and excluding passages | Relevance labels that preserve exception/negative evidence and exact source boundaries |
| Question compiler | Question → correct typed population, clock, denominator and executable operators | Reviewed executable plans across clinical, utilization, access, provider, program and financial questions |
| Router | Cheapest route that meets a task-specific quality requirement | Actual quality, failures, latency, local/API cost and review effort; calibrated escalation |
| Explanation | Supported findings, uncertainty and evidence-linked action rationale | Every consequential assertion tied to a computed result or source; causal claims require a supported design |

Program/measure definitions, guideline versions, payer exclusions, attribution,
denominators and financial arithmetic should be inspectable retrieval/rule/code
inputs. Updating them should invalidate affected answers rather than require every
model to relearn the rule. Broad raw-text fine-tuning is not a substitute for missing
connectors, source identity, explicit calculations or sufficient outcome evidence.

## Verification and remaining product work

153 focused software checks passed. Both rendered workspace JavaScript bundles
passed syntax checks. HTTP and browser execution verified complete membership,
110-patient downstream analysis, exact source inspection, cached replay and the
training-readiness counts. These establish software integration, not clinical
accuracy, outcome benefit or superiority over competing products.

Current server session: **54400**, port **8885**, with the previous runtime,
annotations, evidence-query attachment and live-route configuration preserved.
Prior session 10993 was stopped. Runtime: `artifacts/agere-population-stack-v3`.
Receipts: `artifacts/agere-source-state-v1/run.json`, `integration.json`,
`learning-readiness.json` and `implementation-receipt.json`.

Still needed: broader extraction and calibrated interpretation, cross-source
therapy/episode identities and patient-state validity; clinical/program/outcome
contracts with attribution and denominators; further warehouse adapters for
discovery/prediction/recommendations; fitting/calibration/release from reviewed
skills; governed ingestion, access control and deployment. Existing mathematical
components are not all connected to arbitrary warehouse questions. Actual action
execution agents remain intentionally deferred.
