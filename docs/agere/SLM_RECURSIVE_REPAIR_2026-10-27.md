# SLM learning and shared execution repair — October 27, 2026

The original typed-model study has finished. Its first round learned useful query generation, but its sparse corrective round made performance worse. Both candidates remain unselected. The original SQL learner stopped before fitting because the profile loader required a raw store that its ordinary semantic warehouse did not contain. These outcomes are retained separately from the recovered runs.

| Original typed model | Acceptable verification queries /208 | Wrong query results | Declines | Calibration acceptable /39 | Selection |
| --- | --- | --- | --- | --- | --- |
| Pinned base | 0 | 0 | 208 | 0 | baseline |
| First trained round | 126 | 36 | 46 | 31 | not selected |
| Sparse corrective round | 88 | 31 | 89 | 23 | not selected |

The completed review freshly reproduced these outputs across three authored source fixtures. The second round gained no verification case over its parent and lost48 previously passing calibration/verification cases. Passing64/65 representative fitting tasks did not establish reliable unfamiliar-family performance. More optimization steps alone did not improve this experiment.

## What changed in the general stack

The existing SQL compiler, executor and product investigator now detect arithmetic on received ISO date text before SQLite silently coerces it to numbers. The check follows table/CTE aliases and uses received value types; numeric epochs, money, explicit date conversions and numeric year extraction remain valid. It returns specific repair feedback rather than guessing a replacement calculation. An authored workflow test follows the existing planner's error/repair/review path and returns an average of4days for two stays lasting3 and5days. This is a reusable type/calc repair, without a disease-specific route.

Explicit inpatient/outpatient wording now survives the shared literal check. Previously a query could name the diagnosis and dates while dropping the requested encounter restriction. These checks are necessary protections, not a proof that every SQL query means what a clinician asked.

Posthoc replay of the first model's examined outputs gives:

| Shared guard replay | Acceptable /208 | Wrong results passing guards | Declines / repair fallbacks |
| --- | --- | --- | --- |
| Before | 126 | 36 | 46 |
| With fixes | 126 | 20 | 62 |

Sixteen wrong results are prevented, with zero previously correct results lost across the three fixtures. The second model likewise changes from88correct/31wrong/89declines to88/15/105. This measures error detection on examined engineering outputs. It does not establish new correct answers, independent generalization or medical accuracy;20 wrong first-round proposals remain.

The received-source profile now supports ordinary SQLite relations. It observes actual tables, columns, row counts and non-NULL counts without fabricating raw record streams or clinical vocabularies. The SQL writer receives that actual schema rather than the native warehouse's hard-coded table list. A focused test verifies the real training/serving messages, changed source values and absence of invented native columns. This fixes the SQL training failure and the corresponding general warehouse portability defect.

Adapter qualification now includes the shared SQL guard dependency. A changed guard invalidates stale qualification. The candidate remains off by default.

## Revised recursive study

The already queued larger studies were amended before their fitting began. Corrective rounds retain every approved original fit example and add repeats only from failed fit families. They use one declared corrective epoch. Calibration/verification tasks remain unchanged and never enter fitting or select correction examples. This addresses a plausible sparse-rehearsal cause of the observed forgetting; its effectiveness remains unmeasured until the new comparisons finish. Existing original failures and negative candidates are preserved.

A finite local chain is active in the canonical checkout:

1. Recover the SQL learner on the original5,447-question bank.
2. Run the combined typed learner on6,071questions/124families.
3. Run the combined SQL learner on the same bank. An explicit transfer is allowed only from a qualified new-contract SQL recovery candidate, with comparisons against the base and parent.

Each study has up to two rounds, fresh native completion review and no default activation. The chain stops on an infrastructure or audit failure and preserves final evidence. It never checks Claude availability or starts a clinical workflow. An initial recovery startup was stopped to correct the missing guard dependency; its log/status and old registry remain identifiable. The restarted attempt has a new declared contract and fresh registries.

## Verification and actual limits

Final guarded regression:549checks,545passed and four optional/data skips; zero provider sends, blocked send attempts or protected-corpus reads. Source stayed frozen throughout. Seventeen focused adapter/type/rehearsal checks also pass. Separately,23 provider-profiling/math checks pass after an obsolete authored fixture received coherent birth dates: the previous fixture relied on reported ages despite the governed age definition requiring birth dates. Production age policy was not relaxed. The fixture failure was reproduced on the unchanged baseline before correction.

All5,447 recovered SQL targets and6,071 combined targets per learner pass independent/native checks on three sources. Real serving/training prompts build for all104 original and124 combined families. These are supervision/engineering checks, not model-answer accuracy. No protected evaluation questions, publisher guideline text or invented clinical labels enter fitting.

The completed original typed study took9,795seconds, about163minutes, including fitting, probing and comparisons. Stack-provider inference calls were zero. Hardware dollars, engineering-agent cost and physician-adjudicated right-answer counts were not measured, so total cost per clinically correct answer remains unknown.

The new recovered/combined model runs are still active; their final gains and regressions are not yet available. Ordinary complete-note/dose/answer/deliverable and browser/physician acceptance still await the explicitly authorized subscription continuation. G3 and all ten product areas remain open. No default runtime/model/knowledge promotion or clinical action occurred.

Current evidence: `artifacts/agere-semantic-fixes-2026-10-27/verification.json`, `guard-replay-final-v2.json`, `verification-final/tests.json`, `native-preflight.json`, `recovery-plan.json`, `recovery-status.json`, `recovery-outcomes.md` and the eventual per-job result/review files. The completed original typed review and failed original SQL attempt remain in the prior learning directories. Weights, authored data and runtime evidence stay local.
