# Interpreted evidence returns to its internal request

October 10, 2026. A completed bounded interpretation can now be attached to the exact task, internal packet review, unresolved requirement and prepared follow-up context that requested the evidence. The care queue displays the returned answer, source-bound question counts and current or historical dependency status. The existing interpretation button replays or generates through the normal route, then returns the saved result to that request.

This is a **machine evidence attachment**. It cannot change a reviewer decision, close a requirement, advance task status, record human review, establish consent or claim care delivery. It needs no new reviewer credential because it does not exercise the reviewer action; the existing scoped credential checks still govern packet execution, internal review and preparation of new follow-up contexts. Broader production authentication remains incomplete.

`followup_answers.py` validates the immutable follow-up identity, exact context and patient, current packet/review/requirement and canonical saved answer. A multi-pass source-stage receipt is rejected until reconciliation completes. It prepares the existing evidence-needs receipt, rechecks dependencies before saving, and persists a content-addressed attachment in an append-only SQLite table. Repeating the same attachment returns the same receipt. Inspection marks changed packet reviews, sources, answers, needs or implementation dependencies stale while preserving history.

The API adds `POST /api/stack/packet-followup/answer` and `GET /api/stack/packet-followup/answers?id=<task>`. The existing follow-up UI displays these receipts and uses an optional saved-answer callback from the main interpretation component. No protected clinical/context/review engines were modified.

## Verification

- 64 focused tests passed across attachment, packet follow-up, internal execution, action state, stable/default synthesis and uncapped accounting. Seven new tests cover unchanged review state, exact context/patient binding, pending reconciliation rejection, stale history, changed sources/needs, dependency changes during attachment and rejection of forged role/resolution fields.
- Real HTTP attached answer `fc6d15d4fd41a67aafd6615ba5c5c34c53688afc6f7373f3e69357abb3c59815` to task `a102c3a9d7c9b460a2e6665c048b78ba6e6c76c9d3ff3705fbfeeecacd3b6443` through follow-up `1dd91c30d1b453db2674f423eac4f29ec71e5d77df7253d12492b50faf8e2e6c`.
- Attachment `44c7d8a53674ab0f6de913c2aabfa1cde6ffb5a536115c7bc114d84556f0093c` is current and idempotent. It records 12 findings, 25 exact citations, six of 14 questions with located records and eight unbound questions. Source presence does not resolve any question's meaning.
- Three malformed or mismatched requests were rejected. The entire internal execution/review response was identical before and after attachment; the task stays at revision 7.
- This integration made zero paid calls, created no human reviews, provisioned no credentials and executed no external actions. The separate default-synthesis milestone paid for the saved answer already reused here.
- Twenty preserved engine/catalog files, including the default and stable synthesis engines, and the active original-cohort v1.23 configuration are unchanged. Existing Documents research, frozen experiments, reserved cohorts and generator remain untouched.

Detailed evidence: `artifacts/agere-followup-answers-v1/implementation-receipt.json` and adjacent HTTP, before/after, test, browser and preservation artifacts.

## Remaining work

The live synthetic requirement remains unresolved and 14 task-route prerequisites still require review. Eight interpretation questions remain unbound. Retrieval sufficiency, clinical correctness, clinician identity, payer/benefit/participation verification, service reservation, delivery and outcomes are not established. Continue broader warehouse interpretation and evidence acquisition, source-family-safe learning, qualified prediction/discovery, financial/VBC reasoning and portable acceptance. These delivered follow-through pieces do not complete the full product queue.
