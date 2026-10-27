# Agere learning: actual before and after

The original SQL fitting pass finished, but its first14 candidate comparisons all emitted repeated punctuation. That incomplete attempt and its queued successor were explicitly stopped and preserved after a shared serialization defect was reproduced on an approved fitting request. The replacement typed and SQL studies use the same approved question splits, corrected serving-format supervision and full fitting rehearsal, without Claude. No experimental model is installed.

The current results report is generated from completed receipts at `artifacts/agere-schema-repair-2026-10-27/before-after.md`, with machine-readable identities and domain breakdowns in `before-after.json`. A finite local collector updates those files when the study state or reviewed outputs change. The final durable-preservation step also regenerates the report. Incomplete/unreviewed runs remain pending; different question banks are never pooled into one accuracy percentage.

The repaired typed model has a completed first round, freshly reproduced by
native execution on all three approved source fixtures. Of 304 comparison tasks,
the untrained student declined all 304; the fitted student answered 185 correctly,
answered 33 wrongly and declined 86. It gained 185 correct answers and lost none
against that untrained student. Calibration is separate: 32 correct, 14 wrong and
41 declined of 87. The candidate remains unselected because wrong answers still
pass its guards. Its full-rehearsal corrective round and the SQL study remain
pending. The untrained student is the comparison baseline here; these numbers do
not describe the accuracy of the whole stack or independent clinical questions.

| Completed measurement | Before | After | What it establishes |
| --- | --- | --- | --- |
| Original typed model, first fitting round | 0/208 correct;208 declined | 126correct,36wrong,46declined | Query generation learned useful patterns; candidate unselected |
| Original sparse corrective round | 126/208 correct | 88correct,31wrong,89declined | Regression:0 newly correct and38 previously correct verification answers lost; candidate unselected |
| Repaired typed student, completed first round; enclosing study running | 0/304 correct;304 declined | 185correct,33wrong,86declined | Fresh native review reproduced;185 gained/0 lost against untrained student; candidate unselected |
| Shared execution guards, first typed round | 36 wrong proposals pass | 20 wrong proposals pass | 16 prevented;126 correct retained; declines46to62; no newly correct answers claimed |
| Required source schemas in fitting prompts | 5,331/5,680 | 5,680/5,680 | 349 missing schemas repaired; not model accuracy |
| Required source schemas in comparison prompts | 84/87 calibration;296/304 verification | 87/87;304/304 | Prompt consistency on the examined development questions |
| Unwanted reasoning prefixes in fitting, each adapter | 5,680/5,680 | 0/5,680 | Exact served prompt and approved completion used; not model accuracy |
| SQL fitting targets compatible with served grammar | 0/5,680 | 5,680/5,680 | Fenced format repaired; calculations and source units unchanged |
| Answer-format classifier, majority-class baseline to calibrated lexical head | 35.6% forced-choice accuracy | 91.4% forced-choice accuracy | Accepts80/315 with0 observed confident errors;235 decline; answer shape only |
| Revised SQL/typed models with full rehearsal | Pending | Pending | No final gain or regression claim yet |

The classifier's majority-only comparison has now been superseded by a stronger audit. Using the runtime's existing scalar count/value compatibility, native rules get304/315 correct with0 wrong and11 declines; the learned head gets296/315 forced-choice correct, with80 confident choices and zero useful gains beyond native. Its model bytes, weights and threshold are unchanged. Eight earlier apparent errors were strict scalar-label mismatches, so their removal is a grading correction, not learning. The old isolated candidate is quarantined and the corrected classifier qualification returns not_selected. No runtime model changed. The staged shared qualifier requires useful accepted decisions beyond native rules; its full source verification passes556checks with four skips. See the live report and `native-qualified-shape-result.json` for the separate original/corrected evidence.

The schema checks cover6,071 approved targets on each of three authored fixtures. The real pinned-tokenizer audit covers every fitting/calibration completion on all three fixtures for both adapters. Shared fixes reject empty-decoded non-EOS tokens, serialize the exact inference prefix instead of template-added reasoning, teach fenced SQL and keep malformed SQL tokenization under SQLite's authority. Final source verification has566 passed checks, four optional/data skips, zero failures and no provider/protected-corpus reads. Six additional reporting checks keep stopped partial comparisons unscored and verify paired gains/losses, source outcomes, receipt hashes and38 verification versus48 combined losses. They do not add clinical qualification.

The large runs use pinned Qwen3-4B local LoRA, native compiled/SQLite execution, actual received-source discovery, literal/type checks and source-separated fitting/calibration/verification families. Corrective rounds retain every original fitting example and add fit-only corrections. Jev/Perplexity/UltraBERT/rerankers and frontier deliverables have other stack roles; these adapter studies do not establish new clinical training for those models. The answer-format classifier remains a separate component study; its corrected qualification compares with native rules.

Actual hardware, engineering and physician-review costs are unmeasured, so total cost per clinically correct answer remains unknown. These are adaptive authored engineering results, not clinical, universal-query or competitor-superiority evidence. G3, the automatic full-note/dose/answer/deliverable workflow and all ten product acceptance areas remain open.

Separate shared workflow repairs are verified in the staged integration candidate.
The latest selection ran 563 checks: 561 passed and two optional live checks were
skipped, with no provider calls or protected-corpus reads. Reproduced examples:

| Shared behavior | Before | After |
| --- | --- | --- |
| Two 5 mg tablets twice daily | 10 mg/day | 20 mg/day, with the received instruction and arithmetic |
| Half a 5 mg tablet twice daily | 10 mg/day | 5 mg/day, with the same evidence boundary |
| Missing supply duration, next pickup nine days later | Asserted eight-day gap | Pickup retained; duration and coverage gap unknown |
| Empty and nonempty child selections | Nonempty set exposed as shared | Separate child scopes; no invented shared cohort |
| Strict bounded laboratory result used in time-to-event analysis | Bound treated as an exact value | Interval meaning retained; unresolved units and subset-only estimates explicit |
| Allocation comparison with mandatory practice minimums | Lift against an infeasible comparison policy | Feasibility checked for each policy; noncomparable lift withheld |

The latest identical 16 medication/scope/survival tests change from 22 failed
assertions/subtests and two exceptions on the old source to 16 passing checks.
They are known-defect development scenarios and controls. They do not establish
clinical accuracy, prospective model validity, measured savings or company
superiority. Earlier negative model runs, baseline failures and intermediate
verification errors remain preserved. See `WORKFLOW_INTEGRITY_2026-10-27.md`.
