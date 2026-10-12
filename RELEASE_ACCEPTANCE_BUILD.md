# Computed release acceptance

The scorecard now computes individual engineering completion from saved execution
traces. It no longer hard-codes every case as incomplete or the total as zero.
The app and API expose passed, failed, blocked and unassessed checks with source
paths and explicit remaining evidence. This is acceptance infrastructure, not a
claim that the product workflows are finished.

Ten checks cover category coverage, transport, original question/context identity,
unit accounting, cited findings or explicit uncertainty, planner/answer binding,
supported service selection, owned internal work, independent reopen and follow-up.
Clinical and model qualification stay separate. Supported unresolved clinical
facts can pass engineering checks; missing implementation cannot.

The finite local-release contract covers all ten preserved scope areas through
nine engineering requirements, two qualification requirements and one external
production dependency. Population/VBC, prediction, discovery, financial and
portable-operation evidence need dedicated verifiers. Those results remain
unassessed; their existing isolated components are neither erased nor promoted.
The evaluator can consume dedicated engineering receipts only with exact required
check IDs, the same snapshot and unchanged hashed evidence. Engineering receipts
cannot grant clinical/model qualification or production authority.

Current measured result on the six saved development cases: three failed
(medication, cardiometabolic, payer) and three blocked (respiratory, functional,
appointment), zero complete. Source category/transport failures and disconnected
planning remain visible. Five source-linked handoffs were independently reopened
through actual APIs and retain identity, ownership and source payload. Follow-up
and a shared decision context remain missing from the evaluated path.

Verification: 23 focused tests, including positive completion, explicit unknowns,
removed lineage, stale/cross-patient inputs, manual service choice, missing units,
changed capability evidence and snapshot mismatch. Actual HTTP and rendered browser
checks pass. All 107 referenced traces and 170 protected files verify. Original
stage results are unchanged. No new model calls, training, work dispatch, clinical
reviews, warehouse migration or source-selection promotion occurred.

Evidence: `artifacts/agere-release-acceptance-v1/implementation-receipt.json`.
The prior published scorecard is preserved as `before-scorecard.json` in that folder.
The next gate is the existing evidence-selection candidate qualification, with
its earlier partial checks and failed/interrupted artifacts preserved.
