# Agere source assertion reconciliation — October 9, 2026

Implemented in `clinical_core/agere/clinical/assertion_reconciliation.py`, connected
to the population DAG, exact local planner, chart-pass review and `/stack`.
All patient data and development cases are synthetic.

## What changed

A shared documentary assertion contract now joins literal structured fields and
existing source-bound reader annotations. It retains patient/subject, concept,
relation, polarity, value, source unit, exact narrative span or structured field,
source hash, model identity, interpretation authority, separate date clocks,
copy/history qualifiers and reader review reasons.

It distinguishes prescription orders, dispensing records, administration records,
observed use and observation coverage. `medication_taking_observed=false` means
use was not observed; it does not become a negative use assertion. Referral orders
and explicit attendance outcomes remain separate. Financial Boolean fields retain
both positive and explicitly negative reports. Strings are not coerced to Boolean.
Other narrative relations remain the existing reader's proposals, not silently
upgraded interpretations or claims of new extraction coverage.

The same reducer groups accounts across sources and every chart pass. It exposes
same-date opposing accounts, changes between dated documentary accounts, unresolved
clock relationships, subject/qualification exclusions and repeated statements.
A literal reference date remains different from a clinical date or document date.
Copied/identical undated statements cannot refresh patient state merely because
a later note repeats them. No majority vote or assumed independent corroboration
is used. A report's changing polarity is not a demonstrated clinical change.

Each patient has a content-addressed derived receipt in
`artifacts/agere-population-stack-v3/assertion-receipts.sqlite3`. This is an audit
receipt over existing warehouse/longitudinal artifacts, not another clinical truth
store. Source/code/reader versions control reuse. Inspection verifies the current
source records against the saved hashes. Exact original artifacts and the frozen
Kev/d1 comparison remain unchanged.

Open evidence steps specify interpretation review, source-coverage investigation,
conflicting-account review, medication-state confirmation, care-completion evidence,
and current assistance prerequisites where applicable. These are information and
review requirements, not automatic outreach tasks, clinical treatment instructions,
prioritized intervention lists or verified program eligibility.

## Executed warehouse results

Source: `artifacts/agere-assertion-reconciliation-v1/run.json`.
Each scope processes all **10,000 synthetic patients**, locally with **zero paid
calls**. Rows overlap in people, sources and assertions; do not add them as unique
patients or independent facts.

| Scope | Literal structured assertions | Existing machine proposals | Patients with mapped assertions | Repeated statement groups |
|---|---:|---:|---:|---:|
| Medication | 88,505 | 25,683 | 2,837 | 5,423 |
| Outside care / nephrology | 0 | 40,000 | 8,451 | 5,539 |
| Affordability | 3,334 | 1,270 | 3,334 | 67 |

Outside-care structured count is zero for the declared concepts in this snapshot:
the observed structured referral services are gastroenterology and physical
therapy. Their presence is not interpreted as nephrology attendance or outside
care. Narrative outside-care/source-coverage proposals still require validation.

| Scope | Received source units | Units with an in-scope assertion | Units without an in-scope assertion |
|---|---:|---:|---:|
| Medication | 442,770 | 72,812 | 369,958 |
| Outside care / nephrology | 422,772 | 35,105 | 387,667 |
| Affordability | 365,278 | 4,604 | 360,674 |

Unmapped units include task-irrelevant material and metadata as well as potentially
missed meaning. These are accounting numbers, not extraction recall measurements.
Every received unit is accounted for. Whole source transport is not whole chart
understanding. No same-date opposing groups or dated polarity transitions were
found by these mappings on this snapshot; the failure-boundary tests exercise both
behaviors. Zero observed conflicts does not prove the charts contain none.

The final request preflight fits a 1,500-character ASCII question in the existing
20,000-byte provider cap (19,910 bytes). Non-ASCII or changed catalogs still pass
through the existing strict serialized-request cap; no silent truncation is added.

## Specialist integration and running interfaces

The existing Perplexity reviews were replayed with network access explicitly
blocked in the verification process. Nine proposals for ECO-00171F43EAB5 link to
37 in-scope source assertions. Three proposals for ECO-08BED74E4AF5 link to two
assertions: the structured affordability report and its narrative representation.
Every source assertion binds to an actual prepared pass. Replays made zero new
paid calls. Agreement and related source references do not establish semantic
acceptance; both machine proposal and clinical-state boundaries are retained.
See `specialist-integration.json` and the two `*-reconciled.json` receipts.

- Graph operation: `assertion_review`, with explicit `streams` and `concepts`.
- Full-population presets: Reconcile medication / outside-care / affordability assertions.
- GET `/api/stack/assertions?id=...`: exact persisted assertion receipt with source checks.
- POST `/api/stack/reconcile-passes`: bind shared assertions to every prepared chart pass.
- POST `/api/stack/pass-review`: existing typed review now also returns the source
  reconciliation and proposal-to-assertion bindings when concepts are declared.

The three preset questions compile locally and execute over the complete selected
population; cached graph replay makes no new paid calls. The small patient preview
prioritizes opposing accounts and then patients with assertions; it is explicitly
not a prevalence sample. Complete denominators and all receipts are retained.

**69 focused checks passed** across assertion reconciliation, chart passes, source
context and population graphs. Generated JavaScript passed `node --check`. HTTP
checks cover all three whole-population plans, exact replay, receipt inspection
and cross-pass binding. The browser displays the new controls and result accounting.

## What remains

This implements documentary reconciliation and reusable support bindings, not a
newly trained model or general clinical semantic acceptance. Narrative recall,
cross-sentence coreference, condition/medication indications, current actual use,
maximum tolerated dose, appropriateness, clinical/payer eligibility and true
clinical change remain unresolved unless supported by a future reviewed contract.
No treatment or outreach occurs. No superiority, outcome benefit or universal token
savings is established. The local work does avoid repeated hosted inference for
this deterministic transformation; local hardware costs are not yet priced.

Next: train/validate shared relation and current-state skills on fresh examples,
with exact support and source-family separation, then connect accepted evidence to
versioned clinical/program/outcome requirements. Do not retune on the frozen Kev/d1
reserved cohort, turn annotations into human labels, or narrow the product to the
three demonstration scopes.
