# Same-source reader comparison

Implemented and engineering-verified at 2026-10-11T11:48:12.188218+00:00. This adds executable comparison controls; it does not establish clinical accuracy or complete Agere product acceptance.

The exact-statement picker now offers a comparison of current cohort lexical and windowed MiniLM classifiers, with optional pinned Kev and d1 readers. Every role receives the same complete received text field and marked target, retains its original feature contract, and saves its own source-bound reading. Native readers additionally consume the packet's date metadata; the cohort heads consume text and target features. Role receipts, probabilities, training overlap, failures, abstentions and disagreements remain inspectable. Both all-unknown abstention and training-only per-axis majority baselines are included. Saved comparisons reopen through the question workspace and link to the existing source-interpretation review controls. No human review or work item is fabricated.

Comparisons are immutable and bound to exact source, warehouse, target, code, cohort and model identities. Changed inputs fail inspection. Exact replay does not repeat inference; failed roles remain recorded. Remote providers cannot be selected through this route. No fitting, promotion or clinical execution occurs.

## Qualification findings

The current cohort lexical and windowed MiniLM heads are verified and usable as shadow proposals. Their supervision is an unreviewed machine-teacher snapshot. Both have no qualified selective threshold and can represent only 16 of the shared contract's 24 labels. Missing classes are `other_person`, `retracted`; `planned`, `unknown` time; and `administration`, `care_completion`, `record_request`, `source_coverage`. The new class-coverage inspector exposes these exact limits and model identities. These heads cannot replace the full statement reader contract. Native readers receive all label choices, which is not proof of accuracy.

The older global student artifacts fail current contract verification and are not attached to v1.23. They remain preserved. The comparison uses the current registered cohort models instead; no stale manifest was rewritten and no frozen experiment was refit.

The additive exact-date audit also checks full ISO dates written inside the marked target, preserves exact source offsets, and compares them mechanically with the received source date. The HbA1c target explicitly contains `2027-01-28`, 200 days before the source date `2027-08-16`; its legacy packet reference date is absent. The audit now exposes that source fact without changing any model input, historical reading, prediction or clinical event binding. Other date formats and adjacent time references remain unassessed.

## Development design

Before inference, 12 purposively selected exact passages were declared across medication, respiratory, cardiometabolic, functional, appointment and payer workflows. They cover four original-training patient families, excluded from every partition of the compared cohort and from protected/reserved/outer evaluation selection. Normalized source-template overlap is also absent. Six workflows are not six independent patient families or a representative benchmark.

The 28 scored axis expectations were authored by the assistant from visible source text. They are not clinician-adjudicated labels; ambiguous axes were left unscored. Targets are offline evaluation artifacts and never model input or runtime clinical evidence. No tuning, relabeling or refitting is permitted based on these results. Models may disagree with the engineering expectations for reasons that require adjudication. Shared mistakes are retained rather than converted into consensus authority.

The payer examples deliberately include financial repricing and an administrative billing instruction. `source_coverage` means availability of clinical source data, not insurance coverage; the clinical relation taxonomy has no insurance-entitlement class. This comparison cannot validate a payer matching engine.

## Remaining work

Full source-meaning synthesis, planning and buyer-service matching still need separate shared-workflow evaluations on appropriate contracts. Grouped source-plan answers and cross-group reconciliation remain pending. Representative clinician adjudication, qualified acceptance thresholds, broader training label coverage, independent evaluation, clinical deployment and production integration are not complete.

## Verified results

94 focused checks pass (37 new), along with live API preparation/replay/inspection for all 12 cases, role-coverage/date APIs, invalid and cross-patient rejection, and the actual browser source-picker → comparison → saved reload → interpretation-control path. All 48 declared reader executions completed; the browser created two additional local readings. No human review was submitted.

| Role | Matches among 28 declared axes | Abstentions on those axes |
|---|---:|---:|
| abstain_all | 2 | 28 |
| training_majority | 15 | 0 |
| cohort_lexical | 11 | 7 |
| cohort_windowed_minilm | 12 | 9 |
| kev | 20 | 7 |
| d1 | 7 | 17 |

These counts measure agreement with selected assistant-authored expectations, not clinical accuracy. The 12 cases share four patients and nine source records. Neither statistical superiority nor independent representative quality is established. Both trained heads match fewer declared axes than the constant training-majority baseline. Kev has more matches on this selection but retains substantial errors/uncertainty. d1 abstains often. No role is promoted.

Observed disagreements include prescription non-issuance, negation extending into later return precautions, medication context influencing a blood-pressure label, past measurement dates, appointment requests and financial relation limits. Every original reading remains available. The class inspector addresses unsupported-role visibility; the date audit exposes lost literal evidence. They do not repair model weights or establish that remaining interpretations are correct.

Median local elapsed seconds per role (including model loading and checks inside the reader call, excluding outer comparison verification): cohort_lexical 1.096s, cohort_windowed_minilm 0.599s, kev 20.731s, d1 38.702s. Saved comparisons perform identity checks even when inference is cached. These are development timings, not end-to-end product latency guarantees.

All 161 prior protected files, all 837 ledger rows, all 382 work items / 385 work events / 13 capability tasks / 37 capability events, 310 prior semantic readings, 19 separate Documents research files and 33 pinned source routes remain exact. There are 50 new local readings, no reviews or work actions. The continuation manifest now protects 166 files. A new explicit future-fit exclusion register preserves the four diagnosed families and nine source templates; future preparation must incorporate it before fitting. No production training, model promotion, source migration, external call or new API cost occurred.

Evidence: `artifacts/agere-reader-comparison-v1/implementation-receipt.json`, `predeclaration.json`, `http-verification.json`, `additive-http-verification.json`, `browser-verification.json`, `preservation.json`, `target-date-audits.json`, `role-coverage.json`, `focused-tests.log`, `additive-checks.log` and `browser-reader-comparison.png`.
