# Clinical documentary state and evidence prerequisites

Built October 9, 2026. All source patients and execution examples are synthetic.
Current attachment: `artifacts/agere-clinical-state-v2`, linked from population
stack v3 through `clinical-state-link.json`. The earlier v1 artifact is preserved
as development history; it is not the current code-bound attachment.

## What now executes

- A versioned prerequisite evaluator shared across HF therapy, renal review and
  affordability. It binds source observations to declared requirements and reports
  satisfied, not-satisfied or unresolved information requirements.
- Reconciliation of explicit positive/negative documentation, same-date conflicts,
  historical/excluded observations, unknown dates, unobserved evidence and
  unsupported interpretations. Repeated accounts cannot outvote contradictions;
  later arrival does not make an older observation clinically newer.
- Separate source and clinical-observation clocks, exact source/quote validation,
  patient-identity checks and code/source/assessment fingerprints.
- Supported single-ingredient dose notation normalization, with supply, reported
  use, exception mentions and observation flags kept distinct. Combination doses,
  PRN schedules, equivalence, targets and tolerance are not inferred.
- `state_review` in the population graph, consuming complete selected membership.
  A bounded patient preview does not change aggregate counts. Empty populations
  stay empty and stale artifact identities are rejected.
- A local API and **Inspect clinical prerequisites** in the `/programs` patient
  view; a full-population example in `/stack`. No new external actions execute.

These are internal documentary requirements, not reviewed clinical protocols.
Even a fully documented information contract would not establish treatment or
payer eligibility. Unsupported bindings represent missing software capability;
unobserved evidence can reflect unavailable data, outside care, extraction gaps
or missing care. We do not infer which explanation is true.

## Actual synthetic execution

Sources: `artifacts/agere-population-stack-v3/examples/clinical-state-execution.json`
and `clinical-state-execution-live.json`; artifact metadata in
`artifacts/agere-clinical-state-v2/run.json`.

| Requirement / inventory | Result | Meaning |
|---|---:|---|
| Patients evaluated | 10,000 | Complete observed population |
| HF documentation | 2,015 documented; 7,985 unobserved | Documentary finding, not established current HFrEF |
| EF on an explicit clinical clock | 2,015 historical/excluded; 7,985 unobserved | No usable current binding from these retained source facts |
| eGFR collection date | 4,396 undated; 5,604 unobserved | Source dates do not substitute for collection dates |
| Reported affordability assessment | 3,334 documented; 6,666 unobserved | Includes explicit negative reports; not 3,334 hardship cases |
| Source records marked taking-not-observed | 30,218 records | Not unique patients and not evidence of nonadherence |

All three complete contracts remain unresolved across the population, because
some required interpretations are not implemented and other required evidence is
unavailable. This is **not** a finding that every patient has three care gaps.
Current medication use, appropriate exceptions, maximum tolerated dose, confirmed
goals, program consent and payer eligibility remain unsupported or semantically
unresolved. The reader uses existing retained HF/affordability source-bound facts;
this increment does not add comprehensive arbitrary-note extraction.

The independent raw inventory agrees on population size, patients with eGFR but
no explicit collection date, financial-screen patients and taking-observation
flags. These checks verify source accounting, not clinical correctness.

## Model use and verification

Local evaluation made zero new paid API calls. One actual Claude `claude-opus-5-5`
request composed `cohort` + `state_review` and reproduced the full local counts.
Recorded usage: 6,572 input tokens, 503 output tokens; estimated cost $0.036348,
not an invoice. Replay made zero new paid calls. The maximum-length ASCII planner
request measured 19,759 bytes against the existing 20,000-byte bound; this is not
a guarantee for arbitrary Unicode questions.

100 focused checks passed across state, program work, graph execution, timelines,
predictors, cohorts and the integrated stack. New checks cover explicit negatives,
conflicting reports, distinct clocks, future arrivals, excluded subjects/history,
source binding, dose notation, missing prerequisites, complete/empty membership
and stale artifact rejection. Frontend scripts passed Node syntax checks.

```sh
.venv/bin/python -m unittest test_clinical_state test_program_work test_analysis_graph test_longitudinal test_warehouse_prediction test_cohort_stack test_integrated_stack
.venv/bin/python run_clinical_state.py
# A fresh attachment requires a new output directory:
.venv/bin/python run_clinical_state.py --build artifacts/agere-clinical-state-next
# Optional authorized synthetic frontier execution:
.venv/bin/python run_clinical_state.py --live
```

The saved implementation receipt records file identities and browser verification.
The next clinical increment is reviewed terminology/protocol applicability and
actual state interpretation. Verified outcome/measure/contract accounting remains
a separate unfinished capability. See `AGERE_STACK_RECONCILIATION.md` for the full
discussed-versus-built inventory.
