# Persistent program work — October 9, 2026

Agere now carries a complete population finding into durable local review work.
The underlying machinery is shared across programs; its first adapters consume the
existing heart-failure and affordability workflow assessments. This is an addition
to the interpreter/model stack, not a replacement for broader clinical reasoning.
All records, experiments and example operations are synthetic.

## The implemented path

1. A typed `worklist` graph node receives a complete upstream patient set, including
   its unknown-membership receipt. It assesses every definitely selected patient
   and produces a content-addressed proposal. The ten-patient display preview is
   never the worklist denominator. Missing workflow assessments remain unknown.
2. An explicit save materializes patient/program items in `program-work.sqlite3`.
   The same patient/program has one stable identity across repeated queries.
   Rerunning leaves ownership, due dates and history intact.
3. `/programs` provides a paged queue, program/state filters, owner/due-date editing,
   source-bound review evidence, unresolved inputs and operational history.
4. Registered transitions cover review, offer, reported consent/enrollment, active
   participation, monitoring, holds, decline, review closure and completion.
   Notes and patient-specific source records are required where applicable.
5. Immutable assessment snapshots, append-only hash-linked events, request
   idempotency and expected revisions protect provenance and concurrent edits.
   A stale proposal or stale source assessment is rejected. New evidence preserves
   ownership, flags reassessment and reopens closed work for review. An unchanged
   refresh adds no events. Work outside a refresh scope is not silently closed.
6. A frontier model can propose the graph but cannot save work, consent, contact a
   patient or record a completed program through the graph operation. All queue
   calculations and state changes execute in local Python/SQLite.

## Actual execution

Source: `artifacts/agere-population-stack-v3/examples/program-work-execution.json`.

| Attached review workflow | Synthetic patients assessed | Review candidates saved | Duplicate items on repeat save |
|---|---:|---:|---:|
| Heart failure medication review | 10,000 | 2,015 | 0 |
| Affordability and access review | 10,000 | 162 | 0 |

These are patient-program work items. The programs may overlap; their counts must
not be summed and called unique patients. Independent scans of the raw attached
workflow assessments agree with the saved candidate counts. They do not independently
validate those clinical assessments. No new paid model calls were needed for these
local operations.

Source: `examples/program-work-execution-live.json`. One actual Claude
`claude-opus-5-5` request compiled the heart-failure worklist question into `cohort`
and `worklist` nodes and reproduced the complete-population count. Reported usage:
6,436 input and 357 output tokens; estimated cost $0.032884, not an invoice.
The maximum-length ASCII question request with the actual catalog is 19,514 bytes,
below the existing 20,000-byte model request limit. This is not a guarantee for all
Unicode questions. Exact requests/results use existing shared caching/budget logic.

An isolated lifecycle simulation at `examples/program-lifecycle-demo` exercised
all the way through reported completion and replayed each operational request.
Its nine events include creation and eight staff-report steps. It shares a
read-only source-warehouse link but owns a separate operational database. It is
not included in the main board's offers/enrollments/completions. The main board
contains one explicitly labeled simulated reviewer assignment from UI verification.

## Verification

86 focused checks passed across program work, graph composition, longitudinal
reconstruction, local prediction, typed cohorts and integrated stack behavior.
New checks cover full membership, empty/unknown scope, duplicate saves, concurrent
writers, request reuse, cross-patient evidence rejection, stale assessments,
current-evidence completion requirements, reopening and append-only history.
The graph cache is bound to the current clinical input assessments, including
missing/deleted rows. Old graph outputs cannot hide a changed worklist input.

Run:

```sh
.venv/bin/python -m unittest test_program_work test_longitudinal test_analysis_graph test_warehouse_prediction test_cohort_stack test_integrated_stack
.venv/bin/python run_program_work.py
# Optional authorized synthetic frontier verification:
.venv/bin/python run_program_work.py --live
```

## Practical limits and next build

This is a local review/program prototype. Staff names and consent are self-reported,
not identity-provider authenticated or independently verified. Evidence links prove
patient/source identity, not semantic support for a judgment. Hash chains and SQLite
triggers are application integrity controls, not protection against a privileged
person rewriting the database. No clinical benefit, payment entitlement, or real
contact is established by a state transition. The current source snapshot is static;
there is no scheduled ingestion/refresh service.

HF review does not yet establish current HFrEF phenotype, actual medication use,
maximum tolerated dose, complete GDMT eligibility or program eligibility. These
remain visible unresolved inputs. The next core increment is versioned phenotype,
therapy-state and outcome/measure contracts, with adequate source binding and
unknowns, followed by accountable financial/outcome measurement. External action
agents remain a future layer.
