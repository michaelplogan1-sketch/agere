# Revised offline learning: source selection repair

The revised SQL study is still fitting; there are no new completed model-answer gains to report. Claude and other stack providers remain off. The previously observed sparse correction regression remains recorded:126/208 acceptable queries fell to88/208, and neither candidate was activated.

The next measured defect is broader than a disease or question: compact typed-model prompts sometimes omitted relations needed by their own approved SQL targets. Checking one fitting question per family found four affected families out of75; checking every question found349 affected fitting questions. Training a native-valid answer while hiding its schema is inconsistent supervision. This is a separate discovered weakness; it does not establish the cause of the earlier regression.

| Required source tables available in the real serving prompt | Before | Repaired |
| --- | --- | --- |
| Fitting questions | 5,331/5,680 | 5,680/5,680 |
| Calibration questions | 84/87 | 87/87 |
| Verification questions | 296/304 | 304/304 |

All6,071 approved targets execute acceptably on each of three authored source fixtures. This measures source availability and supervision consistency, not trained-model accuracy. These are examined development questions; they are not an independent clinical test. The average fitting prompt grows from2,929 to3,199characters, about9%; tables grow from5.45 to8.28. Completeness has a measured context cost.

The shared schema prefetcher keeps its ranked starting slice, then adds explicitly named received relations, exact category matches from the existing received-value search, and candidate dimensions indicated by named identifier columns. Identifier naming is a discovery hint; it neither establishes a foreign key nor generates a join. No expected answer, SQL target or reserved question informs serving-time selection. This applies to the shared planner and actual typed training/serving messages.

Before fitting, the adapter task loader now checks whether the actual serving prompt exposes the target's received relations. Missing schemas fail preparation before any gradient fitting. SQL scopes distinguish CTEs from received tables. No target-only schema is inserted into training prompts. Changed source-discovery code also invalidates adapter qualification.

The final frozen engineering suite has558checks:554passed and four optional/data skips, zero failures/errors, zero provider sends or blocked send attempts, and zero protected-corpus read attempts. Source remained unchanged throughout. Two additional local orchestration checks verify exact process identity and refuse interruption after a new candidate has begun fitting. These tests establish engineering behavior, not medical qualification.

The original SQL experiment keeps its frozen source. A finite local successor waits for its completed result and fresh native review, then replaces only queued studies before they begin fitting. If that boundary has already passed, it preserves the running original chain until completion. Process identity includes PID, UTC start and complete command; an unrelated/reused PID is never signaled. The two successor studies run the full6,071-question bank with fresh base initialization, full approved-fit rehearsal and one corrective epoch. They retain separate calibration/verification families, fresh source/parent comparisons, all negative candidates and install=false. They never retry a failed chain implicitly, poll Claude, merge/push code, start a server or execute clinical care.

The source repair remains in an isolated candidate checkout until the frozen experiments finish and their exact revisions can be reviewed together. The known-good runtime and the original checkout source/configuration stay unchanged. Final model gains or regressions will be written to `artifacts/agere-schema-repair-2026-10-27/revised-outcomes.md` and per-study result/review files. This report does not claim those studies are finished.

Previously demonstrated shared SQL/date/encounter checks still prevent16 wrong first-round proposals while retaining126 correct queries in posthoc replay;20 wrong proposals remain. That is error prevention, not16 newly correct answers. G3, full automatic clinical note/dose/answer/artifact acceptance, clinical/model qualification, total cost per clinically correct answer and the ten-area product backlog remain open.

Evidence: `artifacts/agere-schema-repair-2026-10-27/all-prompts-before.json`, `all-prompts-final.json`, `verification/tests.json`, `finish-plan.json`, `revised-status.json` and eventual result/review files. Provider charges from these studies are absent, but hardware and engineering costs were not measured; total cost per clinically correct answer remains unknown.
