# Agere: source meaning and correction workflow

October 9, 2026. Implemented in the connected local synthetic workspace.
Follow-on population integration and current server: `INTERPRETATION_STATE_BUILD.md`.

The stack can now send an exact source occurrence, its surrounding received text
and a shared interpretation contract to Perplexity, Jev, native Kev 4B or native
Liquid d1-3B. The output separates patient assertion, time and care relationship.
For example, declining a proposed dose increase can be represented without calling
it a medication discontinuation. This is source interpretation infrastructure;
it does not complete current clinical state or broad clinical eligibility.

Open http://127.0.0.1:8885/semantics. The existing `/stack` assertion inspector also
offers “Refine individual source statements.” The server is session 82867, with
the same population-stack v3, v4 annotations and evidence-query attachments.

## Executable additions

- `clinical_core/agere/clinical/semantic_reader.py` resolves the exact patient,
  source record, received field and character offsets. It rejects unmatched,
  future-arriving, nonsynthetic or invalid sources. It uses the entire received
  field when bounded; otherwise it retains whole paragraphs and records omissions.
  Complete field context is not a claim to include the entire patient chart.
- Three bounded choice questions use the same state, instructions and options for
  all four readers. Prior reader labels and patient-identifier metadata are excluded from
  model input. Orders, fills, reported use, administration, holds, dose changes,
  care plans/completion, record requests and source coverage are separate choices.
  An explicit unknown remains available on every axis. Native Kev/d1 retain their
  pinned manifests and actual trained readout heads; no generic generation fallback.
- `Stack.refine_statement` and `/api/stack/semantic/read` execute these adapters.
  Exact content, source snapshot, parser/context contract and model identity govern
  reuse. Raw hosted responses also reuse the shared provider ledger. A cache-key
  defect involving noncanonical marshalled code objects was fixed with stable
  explicit source-contract hashing. UI/list changes alone do not invalidate inference.
- `/semantics` compares saved readings on identical target/source/context, opens
  the original context, and provides explicit accept/correct/unresolved/reject
  controls. Comparisons are within the displayed page; there is no majority vote.
- Reviews are append-only, source-bound, idempotent and revision-checked. Changed
  labels require a correction status. Reviewer identity is self-reported, not
  authenticated. The latest accepted/corrected full-field review can enter the
  training-candidate export, including reviewed unknowns and difficult negatives.
  Latest unresolved/rejected reviews supersede prior training eligibility.
- The prerequisite overlay reuses the existing clinical-state resolver/contracts.
  A qualifying reviewed financial report can supply a documentary requirement;
  existing conflicts remain unresolved. Reported use, dose tolerance, visit
  completion/currentness and clinical/program eligibility are not automatically
  supplied. Existing state artifacts are not rewritten.

Derived readings and reviews are in
`artifacts/agere-population-stack-v3/semantic-readings.sqlite3`.
Provider receipts are in that runtime's `semantic-provider-receipts/`.
GET routes provide list, exact reading inspection, patient prerequisites and
training candidates under `/api/stack/semantic`. POST `/review` saves an explicit
review. Existing loopback/origin and serialized mutation controls are retained.

## Actual model executions

Two deliberately selected synthetic development cases ran under an identical
contract on all four readers. They are integration examples, not a held-out
accuracy study, calibration set, independent clinical adjudication or model-selection
cohort. The original reserved Kev/d1 experiment and chart-to-JSON holdout were not
changed or used as new evidence.

| Reader | Dose-increase refusal: assertion / time / relation | Medication-hold account: assertion / time / relation |
|---|---|---|
| Perplexity | patient affirmed / current at source / dose change | patient affirmed / current at source / hold-stop |
| Jev | patient affirmed / current at source / dose change | patient affirmed / historical / hold-stop |
| Kev 4B | patient affirmed / unspecified / dose change | patient affirmed / historical / unknown |
| d1-3B | patient affirmed / current at source / dose change | hypothetical / current at source / hold-stop |

First source, patient `ECO-11AEB2190629`:

> metformin: patient declined the proposed dose increase after discussing preferences and options. The existing prescription was not discontinued.

Second source, patient `ECO-1B9860A33469`:

> metoprolol succinate held after patient reported dizziness.
>
> Current use remains uncertain despite the previous dispense.
>
> Reassessment planned with the prescribing team.

All readers returned dose-change for the first example. Their differences on the
second remain visible. Agreement on one selected passage is not clinical validation;
neither local model has been promoted and no model winner is named.

Sources: `artifacts/agere-semantic-reader-v1/targets.json` and
`{perplexity,jev,kev,d1}.json`. The full source context, native usage, probability
distributions, model identity and cache receipts are retained per reading.
`initial-executions/` preserves the first executions before cache-contract repair;
its hosted request hashes are the same ones reused in the final receipts.

## Verification and economics

94 focused software checks passed across the new reader, assertion reconciliation,
context passes, task context and analysis graph. They verify source boundaries,
whole-paragraph omissions, exact choices, source/snapshot invalidation, stable
cross-process identity, disagreement retention, review concurrency/idempotency,
append-only history and prerequisite behavior. They are not clinical-accuracy tests.
Both rendered page scripts passed `node --check`.

The running HTTP service returned eight current interpretations; source inspection,
four hosted cache replays, unchanged prerequisites on unreviewed cases, empty
reviewed-training export and both workspace routes passed. The browser displayed
the source-matched comparisons, full source text and review/prerequisite controls.
No fabricated user reviews were written to the live workspace. Review-write
behavior was exercised only in isolated synthetic fixtures.

Four new hosted calls were made: two Perplexity and two Jev. Their total configured
estimate was **$0.00018053**, not invoice-verified and excluding local hardware,
loading and review time. See `initial-executions/{perplexity,jev}.json` for tokens,
latency and pricing provenance. Final cache repair reused those hosted responses
without new paid calls; each local model reran both passages under the repaired
identity. Native cache replay was additionally verified without loading weights,
although manifest verification still reads the installed files.

The existing ledger settled 44 completed priced reservations, including earlier
work. The operating cap remained $50, failed reservations were retained and the
recorded remaining reservation capacity became $20.33. That is operational budget
accounting, not invoice reconciliation. See `reservation-settlement.json`.

Machine-readable implementation and HTTP receipts:
`artifacts/agere-semantic-reader-v1/implementation-receipt.json` and
`integration.json`. The implementation receipt includes code hashes and check commands.

## What remains to build

This is an optional source-refinement path, not a new automatic default for every
note. Broader extraction recall, cross-source event/therapy identity, chronology,
current use, completion, indication, tolerance and clinical applicability still
need interpretation and validation. Incomplete source context stays explicit.

Next connect these reviewed assertions and unresolved alternatives into complete-
population question results and persistent program work, with source/model/review
version invalidation. Build additional documentary-to-clinical bindings only where
their prerequisites are actually represented. Then build the split, review,
training/calibration and release process for economical local skills using fresh
patient/source families. No new weights were trained in this increment.

Clinical/program/outcome contracts, attribution and cost/denominator definitions
remain the following core milestones. The broader clinical, population-health,
access, provider, program and financial scope remains intact; HF/CKD are examples.
No real outreach, care execution, employer integration or superiority claim is
introduced by this build.
