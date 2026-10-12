# Question-linked source phrase acquisition — October 10, 2026

Unresolved-question candidate passages now support open-vocabulary specialist phrase/concept proposals. The browser previews one exact received passage, its complete text field and reported structured siblings; a saved proposal can become an exact question-linked task in the existing learning lab. This adds machine-assisted selection beyond the old annotation vocabulary. It does not assess all unmarked cohort fields or train a new model.

The additive `source_span_proposals.py` module reuses current original-person eligibility, exact source checks, received-field transport, the existing Claude gateway/shared uncapped ledger, and `question_learning.prepare`. The old question, queue, native plan, cohort, model and paid-answer implementations remain unchanged. Runtime requests contain received synthetic source data only. Generated structured-display passages are rejected. Whole fields exceeding the 20,000-byte provider-request contract are refused without truncation. Other text fields and records are explicitly outside the request.

Every proposed quote and occurrence is verified against the candidate and received source before it can be saved. Source/question/implementation changes invalidate replay and selection. Pending or failed attempts cannot silently repeat. An append-only selection receipt identifies the machine origin; selecting a proposal creates no source review, supervision label, clinical conclusion or clinical action. The existing task remains a selected development lane. Zero-to-six proposals and explicit abstention are supported.

## Actual execution

Two existing clinical questions were exercised through the real HTTP path using `claude-opus-5-5`:

- Referral status: one proposal, **patient-reported referral wait-list status**, exact source quote. The model retained uncertainty about whether “after” refers to the event or arrival date. Receipt `f429ca359f5a33540157468984026dd93cc71aa96ef5632ebd09e4aebaa4f64d`; selected task `995bbf8367f79588ab09182345c556e8dc4aca9e31d8f666a0ada948e7895216`.
- Reassessment: three proposals covering a planned follow-up, baseline blood pressure and reported partial medication use. The model distinguished the older plan from later completion. Receipt `e262ef82d0ad0489ad78eaf2b9825d3c5aee5ea51c13b4410d6cbb589df90d57`; selected task `9d9c0ad02775b4245bdc7f7a266c284fe12690316997f2bef8f14f95a672ebb5`.

Requests were **3,105 / 3,930 bytes**; returned usage totals **2,858 input / 1,407 output tokens**. Two paid calls cost **$0.039572 estimated**, not invoiced. Two provider token-count requests. Provider transport/parse elapsed times were 12.72 / 22.96 seconds; these are not end-to-end UI latency. Exact replay made no additional inference/token-count call. Both original questions remained unchanged, and source reviews remain zero.

**Observed quality failure retained:** the reassessment response says original outside record `WHM-R-6559d0bf57036fa778e32a` “was not received.” That record is actually another returned candidate; it was outside this one-record submission. This is unsupported scope wording, despite valid exact quotes and retained uncertainty. Machine explanation quality, relevance, clinical correctness and warehouse coverage are not established. Future acquisition should provide compact verified availability/typed-reference context and assess whether it reduces this error without expanding to whole charts. Do not silently rewrite the saved response or count this as a clinically correct example.

## Verification and boundaries

- **23 focused tests pass**: 12 new proposal tests plus 11 existing question-task tests. Covers hidden-source exclusion, exact Unicode/repeated occurrences, separate scalar/text context, protected-family rejection, fabricated quotes/labels, abstention, changed sources/questions, races, overlength refusal, immutable provenance and no failed retry.
- Actual HTTP: two live calls, cached replay, source-task selection/provenance, invalid source/schema rejection, unchanged questions, original 192-task queue and zero reviews. First verification invocation used a nonexistent candidate GET route and was corrected before any model call; no repeated paid request resulted.
- Browser: candidate discovery → eligibility → selected source preview → saved proposals → task selection → learning-lab link. Screenshot and browser verification are under `artifacts/agere-source-span-proposals-v1/`.
- **57 protected code/config/model files**, current cohort/windowed identities, five earlier paid answers and all **19 external research Markdown files** preserved. No generator, reserved cohort or frozen experiment changes.
- No new fitted weights, reviewer credentials, clinician reviews, benefit approvals or external care actions.

## Next capability

Apply separately versioned broad acquisition to the 125 unmarked sampled fields, retaining existing source-family partitions and complete source context. The current question path is connected, but the 76-category cohort inventory has not been automatically converted into new targets. Keep source-level availability separate from model wording, qualify relevance/meaning independently, and resume the full population/VBC, recommendations, prediction/discovery and portability acceptance queue. Do not fit more variants merely to improve scores on the already inspected narrow development set.
