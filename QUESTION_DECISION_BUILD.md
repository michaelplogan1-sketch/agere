# G3 — original question to durable internal work

Delivered October 11, 2026. G3 remains active; the broader release is incomplete.

A saved source answer now has a connected decision path in the actual Agere
workspace. It recovers the original question from its exact source plan, binds
patient/date/window, answer/context/source hashes and rule versions, derives
review topics, matches buyer-declared services and payer prerequisites, saves
owned internal work and reopens a documentary follow-up under the same decision.
The existing matching, patient context, participation, readiness and work stores
remain in use. No evaluator supplies need tags or finding choices to this path.
Manual service exploration remains explicitly separate in the UI.

The first implementation is a deliberately simple cited-source-type baseline.
It routes documentary review topics, not clinical indications, positive findings,
urgency, service benefit or patient eligibility. Normal and negated accounts are
not converted to treatment need. Generic notes, encounters and labs are left
unmapped. Unknown coverage and unmatched service topics stay visible.

## Verified behavior

- Actual functional case P-1004: three cited findings supplied two review topics;
  one additional finding stayed unmapped. The exact four unresolved questions
  remained in the decision and follow-up.
- A synthetic buyer configuration supplied the service and team owner. Existing
  payer, patient/support and participation checks traveled into the match and
  internal handoff. Benefits, current consent and service suitability remained
  unverified. No external dispatch, clinical review or care delivery was claimed.
- Repeat preparation and queue requests retained identity. The internal handoff,
  owned task and unchanged-evidence follow-up reopened exactly after server
  restart. A follow-up does not close work or infer patient outcomes.
- Browser verification covered saved-answer entry, preparation and saved-decision
  reopening. Screenshots are in the receipt directory.
- 67 focused checks passed across decision, scorecard, existing capabilities and
  patient context. Negative checks include cross-patient citations, stale answer,
  source/rule/buyer changes, later/unknown receipt dates, unrelated options/tasks,
  supplied evaluator tags and mismatched follow-up questions.
- All 170 frozen protected files remained byte-identical. Original cohort and
  warehouse configuration were unchanged. No new provider requests or paid
  inference calls were made by this increment.

## Measured progress and limits

The existing acceptance report now computes **one of six individual workflows**
as mechanically connected, using the actual new functional receipts. This is a
narrow engineering result, not clinical or model qualification. The full release
still has zero of nine broad engineering requirements accepted, because its
individual requirement needs all six cases and its population/VBC/OAP,
prediction, discovery, financial and portability requirements remain separate.

Cross-domain probes passed only saved answer IDs to the shared runtime. They
found no mapped topics for the respiratory (3 unmapped findings),
cardiometabolic (19) and payer (2) saved answers. Appointment evidence supplied
a navigation review topic with 11 other findings unmapped; its new connected
work has not yet been demonstrated. Medication remains blocked by the existing
incomplete answer/source scope. These are observed gaps, not successful clinical
abstentions or completed domain integrations. No failed model request was retried.

The actual live follow-up demonstration used unchanged evidence. Changed-record
comparison and cross-patient rejection passed synthetic unit fixtures; changed
warehouse evidence, a new interpretation and downstream work reassessment still
need a connected workflow demonstration. Work uses existing multiple-store
adapters; comprehensive transaction recovery remains a G6 requirement.

## Next action

Stay in G3. Extend the shared planner beyond source-type topics for the measured
unmapped narrative/measurement findings, using exact bounded findings/citations
and buyer capability definitions. Compare any grounded candidate with the current
local baseline, preserve unknowns and expose supporting finding IDs. Carry the
same decision contract through respiratory, cardiometabolic, appointment and
payer work, and handle the medication complete-answer blocker explicitly. Then
verify changed evidence through the same decision/work/follow-up chain. Do not
restart G2 selection tuning or substitute catalogs. Preserve all ten product
areas and proceed to G4 only after the G3 conditions are met.

Evidence: `artifacts/agere-question-decision-v1/implementation-receipt.json`.
