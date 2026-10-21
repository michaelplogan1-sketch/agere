# Original input scope integrated into Ask — October 20, 2026

Activated `f16b208c517cfc7ed119329eaf9dc03beb60abcc` at http://127.0.0.1:8885/ask. Rollback is `8e83d0d945486e6caa9a4921ee2b8bd395e8fc5e`. This closes the explicit original-input follow-up defect within G3/B2, a shared prerequisite for reliable compound workflows. G3 remains active, G5 incomplete, and full MVP acceptance false.

Saved answers now retain their explicit input population separately from patients selected by the result. The existing question-scope selector exposes **Original input**, including an explicitly empty population. Selecting it passes a server-owned, dependency-checked population through the existing conversation path. It never silently substitutes input patients for result patients and does not confer patient-work authority. An unspecified population remains distinct from an empty one. Input IDs participate in the saved context identity and dependency seal; stale, invalidated or tampered contexts cannot authorize follow-up. Existing answers from an older context policy require a fresh question before reuse.

The UI preserves an explicit input selection during pending or failed requests and when using follow-up shortcuts. Summary-only results no longer offer shortcuts that imply a selected result cohort. These are shared context/API/UI changes; no new question-specific handler, model, clinical eligibility rule or automatic interpretation claim was introduced.

Evidence:

- **121 tests passed** on both primary source (34.331 seconds) and the frozen final release (34.982 seconds). Explicit modules: summary follow-up, Ask UI, general-analysis workflow, conversation, answer workflow/dependencies and shared planner. Checks cover nonempty/empty/unavailable inputs, result-vs-input selection, stale/tampered dependencies, invalidated ancestors, forged client fields, no inherited action authority and UI error/shortcut preservation. The initial failing reproduction and intermediate runs remain preserved. Existing NumPy empty-array and imported unclosed-file warnings remain in logs.
- Five owner-authored execution fixtures on an isolated warehouse clone cover financial, clinical and access summaries plus empty and unspecified input. Independent source SQL matches all five. These contracts demonstrate execution, not automatic language or clinical interpretation. Fixtures were prepared on `b65acc4`; final `f16b208` adds only the Ask shortcut-selection repair. Final HTTP and frozen tests run against `f16b208`.
- Six actual candidate HTTP checks pass: three domain follow-ups return the exact three input patients, empty input returns zero, unspecified input refuses, and the default result scope refuses a summary with no selected patients. Saved contexts reopen with current dependencies and no patient-work authority. Final browser verification shows the summary scope choices and the explicit three-patient follow-up.
- Actual live browser workflow: **List patients with diabetes** returns 292 received-record matches (0.5 seconds). Scoped **Count ED visits in 2029** returns 118 events involving 67 patients (0.6 seconds), while retaining the original 292. Explicit **Original input → count them** returns exactly 292 (0.4 seconds). A separate read-only source query matches the 118 events and all 67 contributing patient IDs. Saved contexts are current, exact parent/input identity is preserved, and the final summary follow-up has no patient-work authority. These timings describe this workflow only.
- Live context IDs: population `ANS-8e943a90f0f45edf78933185`, summary `ANS-d15077d9fa64e711fb6f458c`, input follow-up `ANS-b480ffef8186b2428ab6e0bc`. DOM and screenshots are retained. Source hashes, live health, config hash and original warehouse stat checks pass. Runtime `/Users/mikel/agere-mvp-run`, PID97629, session82475, log `/private/tmp/agere-live-summary-scope.log`. Temporary candidate8919 stopped.

Owner review caught and repaired the failed-turn selection reset and shortcut override before final activation. A candidate browser navigation occurred before its restarted server was ready and showed connection refused; after health verification a fresh test tab completed the checks. No application guard was relaxed. No authored fixture was copied into the active warehouse.

Zero paid provider calls and zero recorded API cost. Existing app credentials/integration and original warehouse remain unchanged. This increment improves source-bound context reuse; it does not train model weights or establish automatic learning quality. No protected evaluation, real care, outreach, payment, publication, new service, or separate design/generator/research edits.

Remaining blockers:

- **B1:** Original EF/current-therapy/dose/barrier semantics and conditional work are not demonstrated complete. Next work must address received-source meaning and supported unknowns before expanding clinical actions.
- **B2:** Explicit input preservation after a summary is repaired. Broad unfamiliar language, temporal and multi-domain composition remain unproven.
- **B3:** Generated output meaning and representative end-to-end latency have unresolved failures. Existing queueing chart axis-label contrast remains a presentation limitation.
- **B4:** Independent model and clinical qualification remain unverified.

All ten acceptance areas remain in scope. Continue the existing G3 plan with original compound clinical/current-state semantics, retaining broad-domain regressions and the shared context path. Do not substitute additional helper/UI increments for that next requirement.

Receipt: `artifacts/agere-summary-scope-v1/implementation-receipt.json`.
