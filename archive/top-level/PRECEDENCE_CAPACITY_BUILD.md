# Task-dependency capacity scenarios

Checkpoint 2026-10-11T04:53:40.433226+00:00. Engineering verified; clinical validation and full acceptance remain false.

Saved resource-capacity questions now accept explicit acyclic task prerequisites and finish-to-start handoff lags. The same task scope, staff pools, readiness, duration assumptions and original question remain bound. Results compare the exact no-dependency baseline and separate prerequisite delay, staff wait, not-ready work and propagated blockers. No tasks, care sequences or service slots change.

## Executable behavior

In Care capabilities → saved capacity question → saved resource scenario → Model task dependencies and handoff delays, a buyer enters a question, assumption source and explicit prerequisite edges. Each edge binds two selected tasks, a nonnegative finish-to-start lag and a reason. Duplicate/self/foreign/cyclic edges and additional authority fields are rejected. The discrete-event policy retains ready-task FIFO, service compatibility and most-specific-free-worker tie breaks. Each task requires one worker from one compatible pool.

Effective readiness is max(original ready time, all predecessor finish times plus their lags). Dependency delay is effective readiness minus original readiness; staff wait is start minus effective readiness. A resource-blocked task propagates visible root blockers to descendants. Whole-batch completion remains unavailable if any task is blocked. The original resource and pooled-work comparisons remain unchanged and inspectable. Low/central/high durations are supplied scenarios, not confidence intervals or predictions.

## Actual local scenario

The saved browser-created parent is `a1efc49d84c48acb34f54d7402f3d145d4b0b882e3212b5f94b411f8da0ee891`; four existing tasks represent two patients. One explicit hypothetical respiratory prerequisite has a ten-minute handoff lag. No real prerequisite or clinical sequence is asserted.

| Supplied duration case | Whole batch without / with dependencies | Complete by minute 30 without / with dependencies |
|---|---|---|
| Low | 70 / 70 minutes | 3 / 2 |
| Central | 80 / 80 minutes | 2 / 1 |
| High | 90 / 100 minutes | 0 / 0 |

Central result: one task waits on the supplied dependency, one is in progress, one is not ready and one is complete at minute 30. Mean dependency delay is 10 minutes and mean staff wait is 1.25 minutes, totaling 11.25 minutes after original readiness. The longer dependent respiratory sequence does not exceed the central behavioral completion at minute 80. This is assumption sensitivity, not evidence of clinical benefit or optimal staffing.

## Verification

55 focused checks (19 new), 256 small DAG scenarios and 81 exact no-dependency resource comparisons pass. Chains, fork/join, zero lag, delayed readiness, blocked roots, unchanged input/history, immutable storage, stale parents, concurrent replay and transaction rollback are checked. Actual HTTP save/read/replay and invalid-cycle/scope/authority checks pass. Browser entry and save are verified; six task links resolve and no browser errors were captured. [Receipt](artifacts/agere-precedence-capacity-v1/implementation-receipt.json) · [Screen](artifacts/agere-precedence-capacity-v1/dependency-comparison.png).

The initial HTTP/preservation harness selected the older API-created resource parent, while the browser used the equivalent prior browser-created parent. Their pool identifiers differ, with the same tasks, assumptions and numeric baseline. The initial assertions failed; exact parent IDs were reconciled and the checks rerun successfully. Both original parents and the new scenario are retained unchanged. See `initial-verification-scope-correction.json`; no failing result was erased or relabeled as a pass.

103 protected identities, all program/task/event histories, three original resource scenarios, the full-portfolio review and 19 Documents files are preserved. Zero provider/token-count calls or external actions. Ledger unchanged and uncapped. Runtime 8835/session65071, port8885.

## Remaining and next

No empirical durations/arrivals, simultaneous resources, appointments, clinical priorities, verified staff capability, optimal scheduling or measured outcome effects. Full portfolio/component proposals remain unverified for actual therapy/prescribing delivery; known ACP update content remains unreconciled. Full clinical/VBC/model/portable acceptance is incomplete.

Next: Connect explicit program/work cost assumptions to a saved buyer task/resource scenario: exact currency and unit-cost provenance, fixed versus occupied/available staff costs, cost per selected task/patient and a no-dependency baseline. Use source-linked actual financial accounts only where their definitions support the comparison; keep proposed costs, observed payments, conditional break-even and realized savings distinct. No signed payer terms or attributable outcomes may be invented. Then continue broad clinical/VBC/model and portable acceptance work.
