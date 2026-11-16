# Complete-window source reader and honest semantic coverage

October 10, 2026. Additive capability after `LEARNING_COHORTS_BUILD.md`; no model promotion or clinical qualification.

The installed MiniLM encoder can now process complete source contexts through explicit overlapping windows. The new representation combines a normalized mean of all windows with a separately normalized mean of windows intersecting the exact marked phrase. Windows have 256 tokens and 32-token overlap; aggregation weights reflect newly covered tokens. The pinned encoder and its existing window/cache implementation are reused. Classifier fitting, temperature calibration and abstention use the existing policy. No foundation weights change and no novel mathematics is claimed.

The implementation preserves the failed fixed-length MiniLM run, lexical model, frozen supervision and every partition. It consumes the existing 306 machine-teacher examples with **no new teacher/API calls**. It processed **449 windows across 306 complete inputs**, including **116 multiwindow inputs**, with a maximum of 609 source-feature tokens. Character and token coverage are checked; no source is truncated or dropped. The encoder executed 447 uncached windows with two exact cache hits, recording 80,166 input tokens and 3.64 inference seconds. Total fit time was **5.49 seconds**. Newly fitted head weights occupy **47,020 bytes**, excluding the separately installed encoder, caches and evidence files.

## Actual result

The windowed model exactly agrees with the unreviewed teacher on **19/48 development-validation examples (39.6%)**. The lexical baseline agrees on 22/48 (45.8%). **Neither supports an acceptance threshold; both accept zero examples under the existing calibration policy.** These are descriptive results on the same inspected development set, not an independent model ranking or clinical accuracy measurement. All models remain shadow-only. Window coverage solves the input-length failure; it does not solve interpretation quality.

The optional reader is connected to the original question-linked source task, immutable semantic receipts, exact cache replay and existing review controls. Development disagreements open the exact source and saved teacher interpretation. The original reassessment example receives an unknown/current-at-source/reported-use proposal from both fitted readers, differing from the native planned-care proposal. No answer, clinical state, requirement, review label or work event is automatically resolved.

## Breadth is now explicit

The 500-field acquisition sample spans **76 source categories**. Its **306 fitted targets cover only 13 categories and 11 concepts**, concentrated in historical HF/CKD/medication annotations. The app now shows these separately. **125 unmarked fields across 76 categories** remain unassessed, including home health, pathology, diagnostic access, rehabilitation, functional observations, care preferences and financial/administrative sources. They are neither confirmed retrieval misses nor training labels. The source inventory is in `unmarked-field-inventory.json`; exact fitted scope is in `marked-scope.json`.

## Verification and limits

Across the two deliveries, **100 distinct focused tests pass**: 88 cohort/question/queue/reader/student checks, eight windowed-model checks and four diagnostics/coverage checks. The windowed tests include full coverage, target-marker isolation, preserved failures, exact cache replay, changed-artifact rejection and proof that validation-label changes cannot alter fitted weights or calibration. Actual HTTP checks verify optional reading, diagnostic counts, false-label/resolution rejection, current source/question receipts and the unchanged original 192-task queue. Browser verification covers visible fit failures, coverage limits, reader replay and windowed diagnostics. The earlier 46 protected code/config/attachment hashes and nine frozen cohort/baseline files remain unchanged. No human reviews or labels were invented.

Evidence: `artifacts/agere-windowed-student-v1/implementation-receipt.json`, `coverage-and-preservation.json`, `http-verification.json`, `browser-verification.json`, `focused-tests.log`, `coverage-tests.log`, and `artifacts/agere-learning-cohorts-v1/cohort-500/windowed-minilm-v1/`.

The model's code identity is frozen in its manifest. Do not edit `windowed_student.py` or `learning_cohorts.py` casually; add a separately versioned representation if changes are required. Reusing this already inspected development set is not new independent validation. Complete-window aggregation is not full cross-window attention.

## Next substantive work

Broaden question-linked source acquisition beyond existing annotation concepts. Start from the preserved unmarked-field inventory and actual unresolved question/source paths. Reuse bounded interpretation and source/quote contracts to propose exact source spans and concepts while preserving full discrete/section context, explicit abstention and review provenance. Keep machine proposals separate from source-reviewed labels; do not fit more variants simply to improve scores on the same set. Use an existing authorized hosted specialist where its capabilities are appropriate, with bounded relevant inputs and normal accounting. Preserve the separate original account-dispatch approval blocker.

Continue population/VBC interpretation, informative missingness, explainable discovery, timing-safe prediction, evidence-supported recommendations, buyer-capability execution and portable acceptance. Full queue acceptance is false. Spending is uncapped; no monetary ceiling was added. Owned server **89541**, session **85277**, port **8885**. All jobs for these deliveries are complete.
