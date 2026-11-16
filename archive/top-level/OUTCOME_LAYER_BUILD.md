# Agere: shared outcome definitions connected to population analysis

October 9, 2026. All patient data and executions described here are synthetic.
Current release: `artifacts/agere-outcome-stack-v3/`. This is a completed connected
component, not a claim that the full clinical/VBC product is finished.

## What runs now

`clinical_core/agere/clinical/outcomes.py` consumes the existing longitudinal ledger
through a versioned contract. It selects the first unambiguous index observation
inside a declared window, independently of whether an endpoint occurs. It evaluates
follow-up for every selected patient and persists exact index/endpoint evidence,
source clocks, encounter constraints, exclusions and unresolved inputs.

The `outcome` population-graph operator connects those results to existing cohort
composition, source analysis and statistical comparison operators. Four examples
are available in the live `/stack` workbench. Registered example questions compile
locally; the frontier planner also receives compact outcome definitions and the
typed operator. `/api/stack/outcomes?id=...` retrieves an individual current receipt;
`/api/stack/outcome-contracts` exposes the registered definitions.

The engine distinguishes:

- No observed index from a valid index with no matching endpoint.
- Explicit clinical dates from source notification timestamps, with no fallback.
- Calendar-mature follow-up from an incomplete window. Neither proves capture completeness.
- Explicit source-scoped encounter identities from provisional observation links.
- Repeated identified records from ambiguous same-day indices or conflicting dates.
- Recorded labels from clinical truth. A recorded zero never establishes no care.

`mature_labeled` produces a fixed-horizon comparison population. It excludes
immature positives as well as immature observations without an endpoint; retaining
only the former would select patients according to their result. Full positive
observation membership remains available for descriptive inspection.

The engine accepts registered mapped record-event selectors rather than hard-coding
HF or CKD logic. The initial definitions exercise admission/discharge observations.
Adding a domain definition still requires correct source/event semantics; inventing
a JSON entry is not sufficient to establish a clinical endpoint.

## Warehouse execution

Source: `artifacts/agere-outcome-stack-v3/run.json` and `graph-1.json` through
`graph-4.json`. Index window: January 1 through September 30, 2026. Availability
cutoff: September 30, 2026. Follow-up: days 1 through 30, inclusive.

| Registered observation definition | Patients evaluated | Valid dated index | Matching endpoint | Mature window, no matching received endpoint | No observed index | Unresolved index timing |
|---|---:|---:|---:|---:|---:|---:|
| Admission notification → same-source discharge notification | 10,000 | 144 | 144 | 0 | 9,856 | 0 |
| Discharge notification → admission notification across contributing sources | 10,000 | 144 | 0 | 144 | 9,856 | 0 |
| Discharge → admission using explicit clinical dates only | 10,000 | 0 | 0 | 0 | 9,856 | 144 |

All 144 admission/discharge observation links are provisional: the records lack
encounter identities. The clinical-date definition cannot resolve the 144 index
observations because mapped clinical dates are absent. These are not validated
hospital stays or a CMS readmission measure.

`earlier-cutoff.json` evaluates the same discharge/admission definition with an
August 31 availability cutoff. All 144 indexed patients have incomplete follow-up;
none is converted to a negative label. This is current-snapshot filtering, not a
reconstruction of superseded historical records.

The connected comparison uses 144 mature, labeled patients: 3 with a positive
recorded financial-hardship screen and 141 without an observed positive screen.
Neither group has a matching post-discharge admission notification. Financial
screens are not restricted to baseline. This verifies the shared calculation path;
it provides no evidence of a financial-hardship effect or a useful predictor.
The existing beta-binomial calculation is prior-sensitive in these sparse groups.

## Training connection and economics

The CLI exports one auditable candidate-label file per definition, each retaining
all 10,000 patients, null labels, window maturity and receipt identifiers. Files
contain observed-record labels, not patient-truth labels. Fixed-horizon usability
requires a mature window even for positives. The clinical label remains null.

No model was fitted. These particular exports have no useful positive/negative
variation among their usable labels, so they cannot support a credible discriminative
training result. Future training also requires protected source-family partitions,
features available at the index, richer outcome variation and separate evaluation
worlds. Existing Kev/d1 experiments, reserved families, reader checkpoints and the
other chat's synthetic generator were not modified.

All new warehouse computations and replays used **zero paid API calls**. Exact
graph replay and persistent patient receipts were verified. API-free is not free
local computation. The shared ledger and deterministic evaluator do this work;
loading the existing retrieval models during stack startup does not mean an LLM
interpreted these outcomes.

`planner-payload-check.json` records a real-catalog request of 18,324 bytes and
19,794 bytes with a maximum-length ASCII question, within the existing 20,000-byte
request bound. This was a captured request check, not a paid provider call or a
claim about measured token billing. Duplicate contract prose and concept-system
keys were compacted while retaining the executable definitions.

## Verification and remaining integration

112 focused checks passed across outcomes, longitudinal bindings, graph composition,
economical planning, documentary requirements, program work and existing research
prediction. Coverage includes date boundaries, same-day ambiguity, explicit dual
clinical roles in one source record, late arrivals, cross-patient rejection,
encounter scope, full membership, cache invalidation and label exports. The browser
executed the full-population example and retrieved an individual source receipt;
JavaScript syntax and local HTTP endpoints were checked. See
`implementation-receipt.json` for file hashes and verification details.

Versions v1 and v2 preserve earlier development runs. v3 is the current code-bound
release; earlier immutable receipts should not be presented as current.

Still to build: reviewed narrative-to-outcome event bindings; generalized adjudicated
episodes and recurrent-event handling; enrollment/source-capture and competing-event
contracts; baseline-available feature assembly; clinical/payer measure eligibility;
provider attribution; and outcome-specific evidence references inside persistent
program assessments. Existing work queues can receive population selections, but
this increment does not establish a completed action or its outcome benefit.

The next shared build should connect clinically meaningful cross-source state and
versioned protocol requirements to these outcome inputs. It should preserve the
same broad warehouse question path and the distinction between missing care,
missing records and unresolved interpretation.
