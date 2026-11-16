# Agere: clinical inputs connected to protocol review

October 9, 2026. All patient data and runs are synthetic. Current release:
`artifacts/agere-care-protocols-v2/`. This adds executable shared infrastructure;
it does not complete the proposed clinical/payer decision system.

## What now runs together

`clinical_inputs.py` separates dataset mappings in `clinical_input_maps.json`
from numeric/range predicates and source provenance. It consumes structured
measurements and existing exact-span EF interpretations, preserving units,
clinical/source clocks, historical/copy status and interpretation authority.
It reuses the existing reader outputs; it is not a new reader or trained model.
Numeric ranges are evaluated without inventing a midpoint. Conflicting same-date
values are not averaged. Unusable relevant results prevent silent fallback to
an older value. Historical evidence remains visible separately from unobserved
inputs. A confirmed historical witness can support a history screen even when
another observation is unresolved; unreviewed extraction cannot establish it.

`care_protocols.py` applies registered, hashed definitions from
`care_protocols.json`. Typed measurement, age and documentary inputs share
three-valued all/any/not logic. Missing prerequisites show their conditional
impact on the trigger and readiness, without invented probabilities. Readiness
is restricted to the explicitly registered documentary inputs; it is not complete
clinical readiness. Current dated EF can be known even when it has improved.

The `protocol_review` population operator connects these evaluations to the
existing cohort/analysis graph and workbench. All selected patients are evaluated;
preview rows never define a denominator. `/api/stack/care-protocols` exposes the
registered definitions, and `/api/stack/protocol-review?id=...` exposes individual
source-bound receipts. The local example compiler handles the examples without
a model call; frontier composition also receives the registered operator contract.

`selection_context.py` carries exact protocol receipts alongside existing
documentary selection context into immutable program assessments. A union member
outside a rule's evaluated population is explicitly not assessed. Source/review
or rule changes make earlier references stale before saving or advancing work.
The existing program workflow still determines its own candidacy; selecting a
protocol population never establishes enrollment, treatment, payment or completion.

## Full synthetic warehouse results

Every protocol evaluated all **10,000 synthetic patients**. Counts are from
`artifacts/agere-care-protocols-v2/run.json`, also retained in `graph-1.json`
through `graph-4.json`.

| Registered review | Supported recorded criterion | Source interpretation required | Criterion not supported | Insufficient evidence |
|---|---:|---:|---:|---:|
| Reduced-EF history / therapy review | 0 | 887 | 0 | 9,113 |
| Low-eGFR / specialist follow-through | 1,061 | 0 | 4,912 | 4,027 |
| Affordability / assistance review | 110 | 0 | 2,022 | 7,868 |

These are rule execution counts, not accuracy or clinical eligibility estimates.
The reduced-EF signals remain unreviewed machine interpretations. A nonmatch on
the kidney criterion does not rule out kidney disease or another referral
indication. No protocol establishes missing care from missing records.

The fourth graph reuses all 10,000 affordability evaluations, selects 110 supported
reports and prepares 110 existing-program assessments with exact protocol
selection context. `program-assessment.json` demonstrates that connection.
No program items were materialized, people contacted, or human source reviews
fabricated by this build. Source statements explicitly denying an affordability
barrier remain distinct from unresolved reports.

`patient-1.json` retains a historical EF 38% signal and its original quotations.
`patient-2.json` retains a source-dated eGFR 26.9, an unresolved clinical date and
an unobserved nephrology completion account. `patient-3.json` retains the
supported structured affordability report. These are synthetic examples.

## Source authority and scope

The EF review screen is informed by the [2022 AHA/ACC/HFSA guideline slide
set](https://www.heart.org/-/media/832EA0F4E73948848612F228F7FA2D35.pdf), using
its EF classification and distinction between prior reduced EF and later
improvement. This is not a complete GDMT, target-dose or maximum-tolerated-dose
protocol. Symptoms, phenotype, indication, alternatives, current vitals/labs,
contraindications, tolerance and patient goals still require additional bindings.

The renal screen is informed by the adult referral figure and chronicity
requirements in [KDIGO 2024](https://kdigo.org/wp-content/uploads/2024/03/KDIGO-2024-CKD-Guideline.pdf).
It deliberately does not establish CKD from a single measurement, assess every
referral indication or infer completed specialist care. The source dates identify
the versions implemented; no claim is made that these definitions automatically
incorporate future guideline changes. The affordability definition is an internal
documentary review workflow, with assistance-program eligibility remaining separate.

## Economics, training and checks

Zero paid API calls and no new model weights. Existing retrieval weights load at
stack startup; that is not evidence of LLM interpretation of these results.
Patient results and graphs are reused by exact input/code/rule/review identity.
Unchanged patients can retain their cached evaluation after another patient's
review changes. Local execution and inspection still have real CPU, disk and
review costs. The captured real-catalog frontier request with a 1,500-character
ASCII question is 19,946 bytes, inside the existing 20,000-byte request bound.
No provider call was made for that check.

159 focused checks passed across the new layer, source state, documentary
requirements, graph composition, planning, program work, outcomes and clinical
state. Tests cover contradictory values, ranges, unit conversion, clock separation,
copy-forward/history, other-person/signature/negated evidence, unresolved upstream
membership, exact source binding, immutable work references and revocation.
The release receipt records browser/API verification and file hashes.

No frozen Kev/d1 benchmark, reserved cohort, model checkpoint, generator source,
employer environment or architecture artwork was modified. These receipts are
not gold clinical labels and have not been used to train a model.

## What remains

The shared path is reusable; these three definitions demonstrate it rather than
limit product scope. Broader reviewed narrative relations and numeric extraction,
clinical phenotype/chronicity and current-regimen/exception interpretation,
full clinical/payer protocol bindings, baseline-available prediction features,
reviewed outcome events, attribution and program-to-outcome/payment accountability
remain. Action-execution agents remain outside this increment.

Next shared build: connect source-verified longitudinal clinical/medication
relations and effective rule prerequisites so the same patient evidence can drive
broader recommendations and baseline-safe outcome analyses. Keep diagnosis,
program eligibility and payment eligibility distinct; retain the complete
population and cost receipts.
