# Care-team evidence integrated — October 20, 2026

Activated `7cde54679499aac2e84b76fac11d71b07be56882` at http://127.0.0.1:8885/ask. Rollback: `f8dd1c905dc820533d65b3bca48ab2dcff22f736`. G3 remains active; full MVP acceptance is false.

The existing shared general-analysis path now exposes dated care-team relationships. Explicit assigned/active status and valid inclusive effective dates establish documentary assignment only. Referrals, ended relationships, unknown dates/status/identity, invalid intervals and tied conflicting statements remain separate. Multiple clinicians and roles are retained. A specialty visit or payer attribution is not an assignment; current responsibility is not independently verified. Flattened PCP metadata now discloses its limits. Shared policy identity includes the changed clinical-input code, so old answers cannot silently reuse changed semantics.

Evidence:

- 104 explicit regression checks pass in primary source (30.366 seconds) and frozen source (31.849 seconds), including 12 new authored unit/workflow cases. Coverage includes same-day conflicts, later ending, future availability/start, inclusive end, missing/invalid identity and dates, multiple clinicians, three roles, exact nonempty/empty scope, conditional work and stale-answer invalidation. The first frozen command ran from the primary directory; the recorded final frozen run uses the extracted release directory.
- 3,553 received records reconcile into 3,534 relationship keys: 1,614 documented assignments, 1,166 referral-only relationships and 754 with unspecified status. A separate literal row selection verifies exact source membership/status. The initial verifier assumed only `referred`; it was corrected to include the received `referral_received` status, which product code already handled. Failed verification output is retained. This is documentary reconciliation, not a clinical oracle.
- Three authored source workflows cover consulting specialists, physical therapists and injury follow-up. Saved answers preserve referrals without inventing assigned clinicians. Actual API checks verify exact work selection, a scripted records-needed exception, two monitor observations, unchanged answers and exact saved-population follow-up. Browser inspection confirms the absent assigned clinician and present referred cardiologist, plus the review exception. Authored contracts do not qualify automatic interpretation.
- Live runtime source hashes and care-team status totals match the frozen release. A fresh preflighted deterministic website question, `Count patients`, returns 4,115 in 0.4 seconds with a $0.0000 API estimate, matching the source. Context `ANS-f28df028e7feef50dfb964ca`. PID98662/session34917; log `/private/tmp/agere-live-care-team-evidence.log`. Config and original warehouse size/mtime preserved. Candidate8920 stopped.

Zero provider calls. No source migration, protected evaluation, real care, contact, payment or publication. No recurring continuation was created; explicit restoration approval remains pending after automatic approval review rejected it under the prior no-heartbeat instruction.

Remaining: dated EF/current-phenotype meaning, treatment suitability/maximum-tolerated-dose evidence, full compound conditional work, broad unfamiliar composition, generated meaning/latency and independent model/clinical qualification. All ten areas retained; G3 active/G5 incomplete. Next is EF evidence through shared primitives, then the full compound request.

Receipt: `artifacts/agere-care-team-evidence-v1/implementation-receipt.json`.
