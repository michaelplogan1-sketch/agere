# Question-owned dependency acquisition — October 11 evening

G3 now supports an executable local evidence-request lifecycle inside the existing
saved question and owned service work. The finite changed-evidence requirement
advances from **0/4 to 1/4 checks**. The full local release remains **6/9 engineering
requirements**, with independent clinical/model qualification and production
connections still separate and incomplete.

## Delivered behavior

The saved decision exposes absent records, unresolved declared references, wrong
reference types, and unknown or out-of-window dates. Each request retains its
original question, patient, date window, exact reference field/target, source
hashes and clocks, affected direct finding IDs, work revision, owner and actor.
Unselected historical records and linked references do not automatically become
clinically necessary evidence. Incomplete finding indexes remain explicit.

Requests and subsequent availability checks use the existing immutable question
follow-up store. Nothing is sent externally and no original task is closed or
rewritten. A matching source arrival yields `available_requires_interpretation`;
it does not establish clinical resolution. Changed references require a new
request; unknown types require reference review; date exclusions require review
of the original scope rather than silent window expansion. A source or work
change between inspection and request creation rejects the stale request.

Dependency identities are stable across unrelated arrivals. Each dependency has
a separate version based on its own source/target observations. Checks retain the
full received snapshot for audit without treating unrelated changes as a change
to that dependency. Current and original work owners are separately recorded.

## Executed evidence

- **41 focused tests passed**: acquisition, existing source-review/reassessment,
  and received-reference graph contracts. Eleven tests specifically cover this
  increment, including forged scope, another patient, stale work/sources, partial
  dates, wrong types, exact arrivals and unchanged dependency versions. Existing
  reassessment tests use explicit model stubs; they do not prove real inference.
- Actual production HTTP routes ran against an isolated copy of the real saved
  P-1004 functional question and its existing buyer-owned review task. Two
  received records were removed only from that development copy. Four requests
  represented two absent selected records and two exact references to one of them.
- Restoring one byte-equivalent record updated three dependency observations;
  the fourth dependency kept its exact version. The original decision and work
  task stayed identical. Repeated identical operations were idempotent.
- Browser interaction created an additional local request, checked its arrival,
  and retained it after reload. Accessibility/DOM state and persisted records
  confirm the result. Browser screenshots returned blank images, including after
  a viewport reset; **visual layout verification is not claimed**.
- A fresh service process reopened the four HTTP requests and their exact current
  observations. The first restart probe ran before startup completed and returned
  connection refused; the subsequent ready-server probe passed.
- The original workbench was restarted with the new routes. Actual original
  question/task and received sources were unchanged, the intact active functional
  case correctly had no missing-reference dependencies, and the live evaluation
  endpoint reports the new **1/4** check and overall **6/9** result.
- All **170 protected files** and **143 captured earlier receipt files** remain
  byte-identical. Receipt-file counts include archived copies, not distinct
  milestones. The blocked appointment payload, preparation, and reassessment
  implementation were preserved. Zero new model calls or external contacts.

Raw evidence: `artifacts/agere-decision-acquisition-v1/`. The capability verifier
independently checks the saved request/observation identities before attaching
`acquisition_tracks_blocking_dependency` to the existing release scorecard.

## Limits and next work

This is a controlled local availability replay, not an external acquisition or
clinical review. Its two-patient copy retains original run metadata for service
compatibility; the inherited full-cohort counts do not describe this replay's
coverage. Saved original model contexts are not copied or requalified, so the
replay reports the old decision as historical. Original workspace evidence is
unchanged. Complete reference components are bounded at 200 records; larger
components are rejected rather than truncated.

Real changed/conflicting/late answer interpretation, complete multi-group
reassessment, and unchanged **finding** identity remain open. Unchanged dependency
versions in this increment cannot satisfy that broader finding requirement.
The P-3002 and P-1001 transfers to Anthropic still await their specific approvals;
neither was retried or rerouted. G5 still lacks full historical/reviewer economics,
support for eight classifier labels, and independent qualification. The full
ten-area charter, population health/VBC/OAP, and the known-good G6 recovery archive
remain intact.

The next G3 implementation is the measured multi-group rejection in the existing
reassessment path: reuse complete source-plan answer selection and connect it to
the original question and replacement buyer/work decision while preserving the
pending single-group preparation. Actual changed-answer acceptance still requires
real current interpretation; new preparation-only tests cannot close it.
