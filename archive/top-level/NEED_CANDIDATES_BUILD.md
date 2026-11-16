# Candidate evidence for unresolved questions — October 10, 2026

An unresolved interpretation question now has a local discovery path in the actual evidence-review UI. It searches only that patient’s received warehouse evidence, returns at most eight ranked passages with exact source spans, and distinguishes records inside and outside the original answer. The user selects explicit candidate records; existing typed-reference closure and bounded record contexts feed the normal interpretation and program-review controls. The original requirement is unchanged.

`need_candidates.py`, the Stack method, four API endpoints and `need_candidates.js` implement this path. Immutable receipts bind the original answer/question, source fingerprint, retrieval implementation/model identities, query, selected records and context. Wrong-patient, altered-span, stale-input, tampered-receipt and noncandidate selections fail. Empty retrieval does not establish absent care. Candidate search uses local retrieval and makes no paid model call.

## Verified behavior

Ten new candidate tests and the final 82-test combined focused suite pass (the account receipt uses the same final suite). Actual HTTP checks cover medication, financial and outside-care questions, exact replay, invalid selection, typed closure, unchanged needs and dispatch planning. Candidate counts were 8, 8 and 6 records respectively; 8, 8 and 5 were outside the original answer.

The browser selected one imported medication document, prepared one record / four source units, and saved a three-finding / five-citation interpretation. That one call cost $0.047544 estimated. Two separate bounded financial and referral examples used one and two selected records respectively, one source call each, at $0.033616 and $0.038256 estimated. Total new estimated cost for this milestone: **$0.119416**, three calls; invoice unverified. The financial/referral starting records were explicitly selected from received inventory after inspecting broad search results, not claimed as successful automatic retrieval.

## Limits and next work

Medication discovery surfaced older records, including a 2026 imported document for a question prompted by a 2029 review. General financial search initially favored notes. Ranking, selected evidence sufficiency, retrieval recall and clinical meaning are unvalidated. The saved medication answer identifies the older scope and leaves reassessment questions open. Source-date/UTC-date mismatch is a contract question, not automatically a factual contradiction. Financial hardship, outside care completion and realized savings remain unestablished.

Full financial account follow-through is documented in `NEED_ACCOUNTS_BUILD.md`. Continue temporal/source-contract diagnosis, more useful question scoping and source-family-safe qualification, broader population/VBC/prediction/discovery work and portable acceptance. Do not treat these engineering examples as clinical validation.

Evidence: `artifacts/agere-need-candidates-v1/implementation-receipt.json`, `http-verification.json`, `browser-verification.json`, saved answers/selections and desktop screenshot. No source migration, generator/research/frozen-cohort edits, clinician reviews or external care execution.
