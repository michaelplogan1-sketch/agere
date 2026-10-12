# G2 activation decision — retain the default

The broader source candidate is **not promoted**. G2's activation decision is
complete with a negative result; this is not a claim that the candidate, default,
or clinical interpretation has been qualified for clinical use. The existing
passage/reference route remains the default and the broader proposal stays
available for explicit inspection. The full product scope remains unchanged.

The actual evaluation screen and API now show the six-case comparison, failures,
unknown costs and links to the two saved candidate answers. A report with changed
source snapshot, candidate code or input evidence is displayed as stale. These
machine-authored diagnostics are confined to the engineering view and never
become patient facts, model inputs, or service-selection targets.

## What the declared development comparison found

The same six original training/development questions and original-cohort v1.23
snapshot were used. No model, threshold or inference prompt was tuned. The audit
retains exact received fields, record hashes and uncertain labels; it is not
clinician adjudication or independent evaluation. Record counts are not unique
clinical facts, and copied records were not counted as independent confirmation.

| Question | Added documentary evidence | Candidate answer result |
|---|---|---|
| Medication | 36 direct, 53 supporting records | Eight groups; no complete-answer cross-group interpretation. Expanded typed-reference closure also leaves 7 received records outside scope. |
| Respiratory | 3 off-question statin-tolerability records | Reader rejected for missing required source-unit dispositions; failed attempt preserved, no retry. |
| Cardiometabolic | 14 direct measurement records, 1 off-question ankle report, 75 uncertain | Four groups; complete-answer interpretation unavailable. Expanded closure leaves 4 received records outside scope. |
| Functional | 2 direct records | All 15 selected source units accounted for; caregiver availability and walking-path hazard appear in cited findings. Quality remains unvalidated. |
| Appointment | 2 supporting, 3 off-question payment-plan records, 1 uncertain | All 24 units accounted for, but answer includes billing material and an overbroad no-link statement despite a received reply reference. |
| Payer | 16 direct dated coverage/eligibility records | Two groups; complete-answer interpretation unavailable. |

Baseline references are retained, but preserving those references did not ensure
closure for newly added records. Multigroup cases stopped at the observed missing
complete-answer contract: additional per-group calls could not establish that
contract. The existing whole-record preparations remain available.

## Costs and verification

Nine new inference requests: eight validated at the software contract and one
failed. Recorded-price API estimate, including the failed response: **$0.844336**.
The functional saved-answer inference estimate grew from $0.263992 to $0.326092;
appointment grew from $0.299792 to $0.481700. These totals include stored source
passes and reconciliation, not all historical failed alternatives. Pricing is the
existing October 8 recorded table, not an invoice or a new price verification.

Observed candidate synthesis times were 98.47 seconds for functional, 157.36 for
appointment and 10.10 for the rejected respiratory attempt. Baseline timings came
from older/cache-dependent paths and cannot establish a latency improvement.
Actual human-review minutes and compute rates are unavailable. Total review cost
and cost per clinically accepted answer remain **unknown**, as does completion
cost for blocked multigroup answers; none is represented as zero.

Twenty-nine focused tests pass, including changed-code, changed-snapshot,
changed-evidence and report-integrity controls. JavaScript syntax, actual API and
rendered-browser checks pass. Both new saved answers reopen and replay with zero
additional inference. All 170 protected files, the active snapshot and original
cohort remain unchanged. Generator and separate research corpus were not edited.

G3 is next: bind the original question, exact evidence/answer versions, supported
buyer/payer options, persistent work and follow-up through one shared decision.
The missing group-level answer, reference omissions and reader/linkage failures
remain explicit defects; they are not silently waived. Broader model/source
qualification belongs to G5 under new declared development evidence, not repeated
tuning against these results. Population/VBC/OAP and the remaining release gates
remain in the canonical full queue.

Evidence: `artifacts/agere-source-selection-v1/decision/implementation-receipt.json`.
