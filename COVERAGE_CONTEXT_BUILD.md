# Received coverage in the question-to-work path

Delivered October 10, 2026. `coverage_sources.py` reads exact received enrollment and eligibility records for the selected patient and decision date; `coverage_work.py` binds a chosen record into the existing service match, queue and internal evidence handoff. The UI supports explicit source selection and preserves the source identity, reported plan, arrival date, service reference date, conflicts, supersession and absent fields. It does not automatically select the latest report or infer Original Medicare from generic Medicare.

The received warehouse has 1,614 registration coverage records and 31,154 eligibility responses. These are source accounts, not independently verified current membership, covered benefits, provider participation or authorization. Historical responses remain historical; uninsured reports are not converted into a self-pay election. Future/out-of-period and superseded records cannot supply the selected routing context. A changed coverage dependency blocks reuse of the match and marks its handoff outdated. Unrelated clinical notes do not change the coverage fingerprint.

## Evidence

- 82 focused/regression tests pass across coverage, action readiness, capability matching, record evidence, program work and synthesis work. JavaScript syntax and Python compilation pass.
- Actual HTTP exercises the saved P-1001 answer, exact source selection, matching, idempotent queue/handoff, spoofed payer rejection, and preservation of earlier readiness.
- Browser exercises the same saved answer through explicit selection, match, internal queue and current coverage attachment; no console errors. Screenshot: `artifacts/agere-coverage-context-v1/source-linked-handoff.jpg`.
- P-1001 source `WHF-R-06120b49f9402026499cfb` reports Medicare and fictional plan `WH-FICTIONAL-PLAN-9d129a8824e631` for July 23, 2029, received July 26. At the August 15 decision it remains a historical response, with Medicare subtype unknown. Fifteen received reports are exposed; none is automatically selected.
- Synthetic buyer `synthetic-coverage-buyer` and task `a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443` carry the exact report. Existing review and matching modules, saved answer engines and active warehouse configuration are unchanged.

## Remaining work

Next: versioned buyer/provider participation evidence scoped to an exact service, plan, geography and period, linked to imported document clauses. Current source tables do not establish that participation. Clinical appropriateness, current entitlement, network, authorization, consent/goals, authenticated reviews, service reservation and external execution remain unresolved. The imported document packages currently available are synthetic engineering specifications; no real payer contracts or human clinical reviews are implied. Coverage selection is operator-directed; automated interpretation still needs qualification. More than 200 patient coverage reports requires explicit pagination rather than silent omission.

No new paid inference, human reviews, external actions, training, source migration or protected experiment edits. This deterministic source-joining work uses local code because it does not require a model. Full queue acceptance remains false; the existing continuation heartbeat stays active.
