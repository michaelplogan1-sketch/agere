# Protocol review with linked documentary evidence

Implemented October 9, 2026 against the active original-cohort v1.18 synthetic
warehouse. This connects clinical protocol review, item-level medication evidence
and immutable program-selection context in the actual question/work path.

## Delivered behavior

Protocol definitions can now declare `supporting_documentary_contracts`, using
registered evidence contracts. These supplemental receipts travel with the
patient's protocol receipt and into program-review assessments. Their identity
includes source, rule and code dependencies; changed supporting evidence makes
the affected protocol/work context stale. Definitions reject unknown, repeated
or excessive contract references.

The first application is reduced-EF review version 2. It attaches medication
regimen-confirmation and dispensing-label-report context. Its clinical trigger
and readiness logic remain unchanged. A reported medication, historical list,
received pharmacy copy and original response retain their distinct meanings;
none establishes current use, tolerance, formulation equivalence or eligibility.
The same mechanism is available to other registered protocols and contracts.

In `/stack`, inspect a protocol result, then choose **Inspect linked
medication/source account**. The source display includes the item-level chain and
unresolved clinical questions. Program work items also expose their attached
protocol/source context. No work items or staff events were manufactured to
exercise that latter display; proposal/assessment provenance was verified through
the real execution path and focused tests.

## Observed results

Full-population natural-language composition and a dependent worklist graph ran
against all **1,614 synthetic patients** in the **1,049,339-record** active
warehouse. The raw release and patient roster were unchanged.

| Result | Count | Meaning |
|---|---:|---|
| Reduced-EF review criterion supported | 14 | Received evidence supports review, not a treatment recommendation |
| Insufficient protocol evidence | 1,600 | Not a negative clinical finding |
| Regimen-confirmation documentary review supported | 731 | The registered documentary review condition is present |
| Regimen-confirmation documentary review unresolved | 883 | Documentary condition cannot be settled |
| Dispensing-label documentary report supported | 54 | The declared label-report condition is supported |
| Dispensing-label documentary report contradicted | 677 | The declared condition is contradicted by its source account |
| Dispensing-label documentary report unresolved | 883 | The declared condition cannot be settled |
| Review candidates assessed with protocol/source provenance | 14 | One proposal contains the complete selected group |
| Work items created / external actions / new paid API calls | 0 / 0 / 0 | No clinical execution or hosted inference |

Every number above is recorded in
`artifacts/agere-protocol-source-context-v1/verification.json`. Protocol counts
match the prior independent full-population result in
`artifacts/agere-input-fingerprints-v1/protocol-question.json`; the two documentary
contract counts match the earlier public-source medication verification. The
supplemental evidence did not turn documentation into clinical readiness.

For example, synthetic patient `SYN-07948F79-7DB` has received EF observations
supporting review and a medication source chain reporting non-use of carvedilol.
The linked regimen remains **not verified**. The browser shows the reported
product, interview, historical list, pharmacy request, original response,
received copy and review entry with their separate dates and source IDs. This is
traceable review context, not proof that treatment should change.

## Verification and economics

`regression.log` records **84 passing focused checks**, covering source changes,
patient-specific invalidation, missing receipts, unchanged clinical logic,
selection provenance and definition validation. Imported fixture cases can repeat
between suites; these totals must not be summed as unique tests across milestones.

`question.json`, `source-examples.json`, `work-question.json` and
`work-assessments.json` record the actual full-population execution and persisted
assessments. `http-verification.json` records live source inspection;
`browser-verification.json` records the rendered protocol-to-medication chain.
The browser screenshot tool was already failing; no pixel/layout verification is
claimed. No new browser errors appeared in the verified protocol flow.

Shared contract code hashes are computed once per preparation, preserving
per-patient evidence checks. The initial full run was interrupted before this
optimization and its log is retained. No speedup is claimed from that incomplete
run. Current source fingerprint reuse avoids rehashing unchanged raw payloads;
protocol preparation and supplemental snapshots still incur local processing.
All inference for this integration is deterministic local code, with zero paid
model calls. Local compute/electricity is not priced here.

## Remaining work

This milestone supplies reusable documentary context and dependency invalidation.
It does not establish clinical correctness, automatic regimen reconciliation,
clinical eligibility, benefit or improved outcomes. More clinical/payer protocol
breadth, qualified learned interpretation, outcome-safe prediction and
provider/contract accounting remain in `AUTONOMOUS_STACK_QUEUE.md`.

The next substantive implementation is the original-cohort outcome target and
prediction path: distinguish observed care events from record notifications,
enforce availability-time and follow-up rules, and connect qualified baseline/model
results to the existing question/state path. Fit only if the observed targets
support a meaningful comparison. Preserve the frozen model experiments and
separate evaluation/generator truth throughout.
