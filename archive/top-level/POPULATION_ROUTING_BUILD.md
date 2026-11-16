# Complete population routing and bounded internal review

G4 delivery, October 11, 2026. The saved population question now accounts for
every original roster member through documentary nominations, patient/payer
contexts, buyer services, an explicit internal-review batch and persistent work.
This reuses the existing decision/follow-up, patient/coverage/participation and
work stores. There is no new product module or database.

The Programs-to-question path supports preparing every nominated member's route,
entering an internal-review limit for each buyer service, proposing the batch and
preparing its owned internal handoffs. Every member remains visible as nominated,
waiting, blocked, unmapped, outside the cohort, uncertain about cohort membership
or without a positive nomination. Missing need remains unknown.

Received payer reports, conflicting reports, date scopes and exact plans are saved
for every nominated member. No report is silently chosen as current coverage.
Patient goals, consent, benefits and participation stay unverified. Internal work
uses the existing source-bound patient and participation matching paths.

| Program | Original members | Members with review routes | Owned internal handoffs | Waiting for internal review capacity | Need unresolved without nomination |
|---|---:|---:|---:|---:|---:|
| Respiratory | 1,614 | 100 | 4 | 96 | 1,514 |
| Depression/anxiety | 1,614 | 24 | 4 | 20 | 1,590 |

These are two compositions over the same roster, not separate populations. Each
program reused its prior handoff and prepared three additional handoffs. Source
nomination sets retain the independently reconciled received-source denominators
from the preceding delivery.

## Capacity, persistence and authority

The demonstration explicitly supplies a limit of four noncancelled internal review
tasks per service. It is a fictional workload assumption, separate from the buyer's
four declared service slots. No slots are reserved, staff rates inferred or clinical
priority established. Deterministic patient/service ID order is only a documentary
review ordering. Clinical allocation and production reservation/dispatch are not
implemented by this batch.

Existing noncancelled work consumes the conservative review envelope. Work from a
different question, held/stale work or changed source binding requires reconciliation
and cannot count as current work. Source, buyer and queue changes invalidate a saved
proposal before new execution. Duplicate execution replays an immutable receipt.
Partial failures retain completed work and explicitly stop the remaining batch;
this is not one global transaction across all existing stores. Historical execution
receipts do not establish current clinical completion.

## Evidence and acceptance

- 53 focused tests passed, including zero limits, all-member accounting, incomplete
  routing, stale queue/source rejection, cross-question work, duplicate execution and
  injected partial failure. Patient/coverage wrappers in small unit fixtures are
  explicit stubs; the full-cohort HTTP workflows use actual existing engines.
- Actual HTTP execution prepared 124 member routes and six new internal handoffs,
  preserving two existing handoffs. Both populations and all eight handoffs reopened
  after a fresh server restart with the same member dispositions.
- Browser checks submitted the internal batch proposal, switched member context,
  verified persistent dispositions and the disabled execution control when no new
  work was proposed. A rendering defect that reset the selected member was fixed.
- The initial restart verifier incorrectly expected the respiratory buyer-queue
  snapshot to stay identical after behavioral work was added. The changed shared
  queue identity was correct; member dispositions and work stayed identical. The
  failed verifier observation and corrected checks are retained separately.
- The existing finite acceptance checker verifies **6/6 local population checks**.
  The live evaluation view now consumes the hashed, snapshot-bound capability
  receipt through `config/agere/capability_acceptance.json`. Historical individual
  scorecard files remain untouched; evaluation evidence is not runtime clinical data.
- Full release engineering remains **1/9 requirements passed**. Individual workflow
  completion remains **5/6**. Model/clinical qualification and external production
  prerequisites remain unassessed; this is not full G4 or product completion.
- All 170 protected files and 241 prior sealed evidence files remain unchanged.
  No provider inference, external dispatch, patient contact, clinician review or
  actual care was performed. The original-cohort v1.23 source is unchanged.

Evidence: `artifacts/agere-population-routing-v1/implementation-receipt.json`,
`http/verification.json`, `population-acceptance-verification.json`,
`restart-verification.json`, `live-scorecard.json` and `browser-verification.json`
within that artifact directory.

## Remaining G4 work

Connect these exact population questions, member dispositions and work IDs to the
existing program/outcome-accountability and versioned contract/financial operators.
Independently reconcile received service, adjudication, payment and balance inputs.
Missing attribution, installed contract rules, actual service delivery and outcome
links must remain explicit. Scenarios cannot become realized savings or causal
benefits. G3 medication approval/changed-source interpretation, G5 role qualification
and G6 packaging remain open. All ten product areas stay in the canonical backlog.

`AGERE_BUILD_CONTROL.json` remains the sole sequence and exact next action.
