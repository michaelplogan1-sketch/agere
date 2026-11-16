# Additional source-learning cohorts and visible model failures

October 10, 2026. Executable synthetic development capability; no clinical qualification.

The active v1.23 workspace now supports separately registered acquisition cohorts without replacing the original 192-task review queue, native plan, question source task or historical v1.18 experiments. Patient families, source hashes, normalized field templates and exact model inputs form connected components. Previous partitions constrain those components; conflicts are excluded rather than reassigned. Prior unplanned reading exposure is fitting-only. Existing planned calibration/validation readings keep their original assignments.

The new 500-field sample spans **76 source categories**. It contains **306 eligible targets: 209 fitting / 49 calibration / 48 development validation**, over 206 components. **118 targets inherit earlier assignments; 69 conflicting targets are excluded; 125 unmarked fields remain unassessed.** The larger design satisfies the existing sample minimums. This is a selected development cohort, not an independent clinical evaluation.

All **306 Kev readings completed**, using one installed model load. The full declared set was frozen, with no failure filtering, generated human labels or partition reassignment. Native execution recorded **332,868 input tokens**, **318.46 inference seconds**, and **32.24 seconds for the one model load**. Repeated load-time values on individual readings refer to that same load and must not be added together. Maximum MLX allocated memory was 9,611,595,212 bytes; process lifetime peak RSS was 3,536,617,472 bytes. These are different memory measurements. No paid API calls or API charges occurred.

## What the fit actually established

| Representation | Executed outcome | Development result |
|---|---|---|
| Lexical character features + classifiers | Fitted, source-bound shadow reader | 22/48 exact teacher matches (45.8%); no supported calibration threshold; zero accepted rows |
| Installed frozen MiniLM + classifiers | Failed before completing the fit | Overlength input; full source text was not truncated or silently dropped |

Both outcomes are preserved. The lexical fit took 0.370 seconds and produced 1,103,641 bytes of model artifacts. Foundation weights were not updated. Scores are agreement with unreviewed machine suggestions, not clinical accuracy. Missing training classes, poor selective calibration and absent independent clinical validation keep automatic acceptance disabled.

The original reassessment question can invoke the lexical cohort reader and replay its exact receipt. On that source, the lexical model proposes unknown statement/current-at-source/reported-use, while the existing native proposal is patient-affirmed/planned/care-plan. Neither is promoted into the answer, current patient state or care execution. The discrepancy is visible rather than concealed by a summary score.

## Connected inspection and verification

The learning workspace displays each cohort's counts, native acquisition, fitting failures and calibration evidence. Development disagreements open exact received passages and their saved teacher proposals using the existing review controls. Filters and pagination retain validation membership. Inspection creates no human review or training label.

**91 focused tests pass** (88 cohort/queue/question/reader/student checks plus 3 diagnostics checks). Real HTTP checks verified shadow reading, exact cache replay, diagnostic counts, invalid/forged requests, unchanged original question and the original queue. Browser checks exercised cohort display, exact-source disagreement inspection and the original question's cached fitted reader. Forty-six protected code/config/attachment files still match their saved hashes; five earlier saved answers inspect successfully. The 19 Documents research Markdown files remain unchanged.

Evidence: `artifacts/agere-learning-cohorts-v1/implementation-receipt.json`, `acquisition-summary.json`, `cohort-500/plan.json`, `cohort-500/models/run.json`, `http-verification.json`, `browser-verification.json`, `preservation-audit.json`, `research-corpus-continuity.json`, and the two saved screenshots.

## Continue from the actual limitation

Implement a separately versioned complete-window MiniLM representation using the existing pinned encoder/window machinery. Preserve the failed fixed-length fit, labels, cohort and partition assignments. Reuse saved native supervision; do not reacquire or select new partitions based on scores. Show the new representation's real calibration/abstention and source-level behavior; a larger sample or successful fit does not imply qualified clinical behavior. Then resume the broader population/VBC, prediction/discovery and portable product acceptance queue.

No generator, reserved cohort, original question engine, spending policy or external research corpus edits. Spending remains uncapped. Original account dispatch approval remains pending without retry or bypass. Full queue acceptance remains false.

Owned local server: PID **88928**, session **96245**, port **8885**. No acquisition/verification job remains active for this delivery.
