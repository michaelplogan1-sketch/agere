# Agere: source-bound longitudinal evidence

Implemented October 9, 2026. All records and runs are synthetic. The existing
[workbench](http://127.0.0.1:8885/stack) now has **Reconstruct records around
admissions** and **Expose missing clinical timing** examples, plus free-form
frontier composition into the same local operators.

## What changed

- Each source record can yield multiple explicit event roles. Admission and
  discharge dates in one record no longer need to agree in the new ledger.
- Narrative annotations resolve back to the exact received text field and span.
  A bracketed date on that statement or an explicit prior-note section becomes a
  documentary reference date. DOB and signature headers are not assigned to every
  assertion. Conflicting, invalid and future references stay unresolved.
- Source-event, arrival, explicit clinical and narrative-reference dates are
  independent query clocks. No missing clinical date is filled with a record date.
- `sequence` computes full population membership for bounded observed event pairs;
  it can feed set operations, comparisons, analysis, retrieval or model review.
- `longitudinal` reconstructs context around selected events, provisional
  admission/discharge pairs, repeated text and opposing documentary accounts.
  Plans and record requests do not become completed care. Missingness alternatives
  name the evidence needed to distinguish them without invented probabilities.
- Existing result caches include the attached ledger manifest identity. The ledger
  checks source/snapshot/reader/code identities. Existing clinical logic and model
  weights remain their original versions.

The ledger is a new derived artifact, not a source-generator change. The initial
v1/v2 builds are retained as development history; v3 is attached to the live stack.

## Actual execution

| Recorded quantity | Result | File |
|---|---:|---|
| Synthetic patients | 10,000 | `artifacts/agere-longitudinal-v3/run.json` |
| Source-record observations | 313,586 | same |
| Source-bound text annotations | 103,163 | same |
| Observations with an explicit clinical date | 88,896 | same |
| Narrative annotations with a literal reference date | 25,387 | same |
| Patients with an admission→discharge source-date pair within the specified window | 144 | `artifacts/agere-population-stack-v3/examples/longitudinal-execution.json` |
| Provisional paired episodes in that population | 144 | same |
| Surrounding observations, ±30 source-date days | 1,706 | same |
| Repeated text families in selected-concept received accounts | 122 | same |
| Opposing same-concept/date/clock accounts in this selected example | 0 | same |
| Admission anchors without an explicit clinical date | 144 | same, clinical-clock run |
| Observations unplaceable on that clinical clock | 4,743 | same, clinical-clock run |

Account reconciliation examines selected-concept annotations received by cutoff,
including history outside the context window; its scope is explicit in each
patient result. Context previews are bounded while population counts use full
membership. Repeat families are not independent corroboration. Missing annotations
and no observed conflicts do not establish complete or correct extraction.

An independent raw ADT scan agrees on all 144 source-date pairs and finds no
explicit clinical admission dates. Switching to the clinical clock therefore
leaves admission timing unresolved and yields no dated surrounding context.
See [raw count receipt](artifacts/agere-population-stack-v3/examples/longitudinal-independent-counts.json).

One actual `claude-opus-5-5` call compiled the question into cohort → sequence →
longitudinal and returned the same 144 matching patients. Provider-reported usage:
6,396 input and 1,062 output tokens. The locally estimated charge is $0.046824 using
the stored October 8 price table, not an invoice-verified amount. Local execution
and cached replay made zero new paid calls. See
[frontier receipt](artifacts/agere-population-stack-v3/examples/longitudinal-execution-live.json).
The real catalog with a 1,500-character ASCII question serializes to 19,096 bytes,
inside the existing 20,000-byte request guard; overlong encoded inputs still fail
before a paid call. Duplicate vocabulary and telemetry are omitted from prompts.

## Verification and limits

74 focused unit checks passed, including 16 new longitudinal checks. These cover
exact source binding, exclusion of simulator-only original document text, copied
sections, future/conflicting dates, opposing assertions, request-versus-observation
relations, multi-role records, cross-patient/source pairing, tied-date ambiguity,
late arrivals, complete membership beyond previews and explicit missing timing.
The actual warehouse and live planner runs are separate saved integration receipts.

This is documentary reconstruction, not a validated clinical causal or state model.
The literal-date parser is deliberately narrow; relative temporal expressions and
broader narrative interpretation remain incomplete. Proximity is not proof of
encounter identity or a verified readmission. No current admission state is inferred
from a missing discharge. Superseded historical records cannot be recovered by
filtering today's snapshot. The previous `clinical_timeline` operator still uses
its original contract; the new multi-event logic is in the longitudinal path.

The [completed research intake](AGERE_RESEARCH_INTAKE_2026-10-09.md) identifies the
next core additions: accountable populations and medication/tolerance interpretation,
persistent program work, outcome/commercial/OAP measurement and typed dependencies
for supported downstream estimation and decisions.

## Run

```sh
.venv/bin/python -m unittest test_longitudinal test_analysis_graph test_warehouse_prediction test_cohort_stack test_integrated_stack
.venv/bin/python run_longitudinal_stack.py
.venv/bin/python run_longitudinal_stack.py --live
```

`--live` enables the authorized synthetic frontier route and uses the shared usage
ledger. Local example replay needs no API credentials. To rebuild after changing
event extraction, choose a fresh output directory with `build_longitudinal_stack.py
--output artifacts/<new-version>`; it attaches that verified version on success.
