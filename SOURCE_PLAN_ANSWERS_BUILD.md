# Grouped source answers and bounded cross-group review

Delivered October 11, 2026. This is executable engineering progress on the original v1.23 synthetic warehouse, not clinical validation or full product acceptance.

The original question now retains verified answers from every declared review group. Each attachment is immutable and checks the saved answer's patient, question, context hash, exact record scope and complete one-time accounting of source units. Missing groups remain missing. Multiple saved answers require an explicit selection; no answer is silently promoted because it is newest. The selection can be reopened from the source-plan page.

After all groups are selected, a bounded cross-group review uses every original source-pass claim and its exact cited excerpts. Source units and local pass names retain distinct global identities. Original prose is preserved with a group/pass reference map. The review does not submit the complete chart, extend source coverage, establish eligibility or create a clinical action. Existing individual group answers retain their evidence-needs, buyer matching and program-review controls. The combined review's questions are visible proposals; their own work-task integration remains to be built.

## Executed example

The saved payer question for P-1006 now has two verified answers covering **24 records and 53 source units**, with 474 received records explicitly outside the selection. Eight source-reading calls and seven within-group reconciliation calls completed. A single cross-group request submitted **16 original claims and 28 exact excerpts in 26,300 bytes**.

Two real failures were diagnosed:

- The old reference codec expected object lists and failed when reconciliation supplied an already-tabular dependency structure. A separate lossless adapter now handles this shape. The three already-completed source calls were reused; no source-pass request or receipt was rewritten.
- The cross-group model response used one ambiguous bare `u5` reference in a claim-review reason. Strict parsing rejected the response. The failed call and original response remain preserved. Local recovery revalidated the unchanged findings and unaffected reviews, quarantined that one review as system-unassessed, and did not infer a referent or resend the request.

The recovered output has four cited findings, 15 retained model claim reviews and one quarantined review. Claim dispositions are four supported, ten qualified and two unassessed, including the quarantined review. These are machine judgments, not adjudicated accuracy. Eight unanswered questions and seven proposed evidence-review questions remain visible. Group completeness, exact citations and claim accounting do not establish that the interpretation is clinically correct.

## Verification and cost

- **102 focused tests pass**, including 24 new attachment, reconciliation, codec and quarantine tests. API tests verify exact save/replay, wrong-context rejection, incomplete selections and zero-call local recovery.
- Browser verification exercised saving selected group answers, reopening, cached group interpretation with the automatic attachment callback, and the recovered combined answer. No browser console warnings or errors were observed.
- Independent checks verified 24 raw record hashes, 53 source units, 29 exact narrative units, all 16 final citations, and unchanged original claim text/qualifications. All 166 prior protected files, 837 prior ledger rows, 360 statement readings, 19 separate Documents files and 33 pinned historical source routes remain intact.
- Internal histories remain 382 work items, 385 work events, 13 capability tasks and 37 capability events. No human reviews, model training, clinical execution, patient contact, warehouse migration or external publication occurred.
- **16 new inference calls: 15 validated, one failed. Estimated API cost $1.347992**, including $0.200888 for the failed response. Estimates are not invoice reconciliation. The ledger now has 853 rows, including 14 failed historical/current calls. Native token checks and exact replay protections remain active. Spending remains uncapped.

## Remaining work

The approved shared evaluation still needs source-meaning synthesis, planning and service matching across all six workflows on predeclared visible evidence. This single payer example is an engineering development case and overlaps existing training families; it is not independent evaluation. The earlier 12-statement comparison and development exclusion register remain protected.

Combined review questions still need exact parent-plan evidence-needs/work bindings. Larger cross-group requests can exceed the existing 24-record/64-claim/32-KiB claim gateway; they remain blocked rather than silently dropping records or claims. Cross-group review currently uses original source-pass claims and excerpts, not uncited text or the derived within-group conclusions. Source selection sufficiency and end-to-end clinical/payer quality remain unvalidated.

Four older payload-specific outbound approvals remain pending and untouched. Broader clinical interpretation, VBC/program applicability, qualified predictions, model-role evaluation and production portability remain in the full continuation queue.

Evidence: `artifacts/agere-source-plan-answers-v1/implementation-receipt.json`, `http-verification.json`, `preservation.json`, `focused-tests.log`, `cross-group-answer.json` and `browser-source-plan-answers.png`. Continue from this milestone; do not replay the failed outbound request or refit on diagnosed development targets.
