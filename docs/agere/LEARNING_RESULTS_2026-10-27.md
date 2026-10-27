# Agere learning: actual before and after

The revised model studies are still running locally, without Claude. The first SQL fitting pass has1,300 steps; final quality comparisons follow fitting. The remaining dependency is local compute, not permission or another source edit. The finite successor is already waiting to run the repaired typed and SQL studies after the original SQL study's reviewed completion. No experimental model is installed.

The current results report is generated from completed receipts at `artifacts/agere-schema-repair-2026-10-27/before-after.md`, with machine-readable identities and domain breakdowns in `before-after.json`. A finite local collector updates those files when the study state or reviewed outputs change. The final durable-preservation step also regenerates the report. Incomplete/unreviewed runs remain pending; different question banks are never pooled into one accuracy percentage.

| Completed measurement | Before | After | What it establishes |
| --- | --- | --- | --- |
| Original typed model, first fitting round | 0/208 correct;208 declined | 126correct,36wrong,46declined | Query generation learned useful patterns; candidate unselected |
| Original sparse corrective round | 126/208 correct | 88correct,31wrong,89declined | Regression:0 newly correct and38 previously correct verification answers lost; candidate unselected |
| Shared execution guards, first typed round | 36 wrong proposals pass | 20 wrong proposals pass | 16 prevented;126 correct retained; declines46to62; no newly correct answers claimed |
| Required source schemas in fitting prompts | 5,331/5,680 | 5,680/5,680 | 349 missing schemas repaired; not model accuracy |
| Required source schemas in comparison prompts | 84/87 calibration;296/304 verification | 87/87;304/304 | Prompt consistency on the examined development questions |
| Answer-format classifier, majority-class baseline to calibrated lexical head | 35.6% forced-choice accuracy | 91.4% forced-choice accuracy | Accepts80/315 with0 observed confident errors;235 decline; answer shape only |
| Revised SQL/typed models with full rehearsal | Pending | Pending | No final gain or regression claim yet |

The schema checks cover6,071 approved targets on each of three authored fixtures. Final source verification has554 passed checks, four optional/data skips, zero failures and no provider/protected-corpus reads. Four additional reporting checks verify paired gains/losses, overlapping source outcomes, receipt hashes and the difference between38 verification losses and48 combined calibration/verification losses. They do not add clinical qualification.

The large runs use pinned Qwen3-4B local LoRA, native compiled/SQLite execution, actual received-source discovery, literal/type checks and source-separated fitting/calibration/verification families. Corrective rounds retain every original fitting example and add fit-only corrections. Jev/Perplexity/UltraBERT/rerankers and frontier deliverables have other stack roles; these adapter studies do not establish new clinical training for those models. The completed answer-format classifier is a separate component study with its own denominator and majority-class comparison.

Actual hardware, engineering and physician-review costs are unmeasured, so total cost per clinically correct answer remains unknown. These are adaptive authored engineering results, not clinical, universal-query or competitor-superiority evidence. G3, the automatic full-note/dose/answer/deliverable workflow and all ten product acceptance areas remain open.
