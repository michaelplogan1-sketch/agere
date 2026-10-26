# Deterministic definitions — Mike 2026-10-25

Branch: `codex/definitions-2026-10-25`; starting commit: `5179d29d729589e4a354c29e3756c91df9d9a890`.

G3 candidate implementation. Event volumes/rates/shares include deceased event owners; patient counts/lists retain the living rule. Generic hospital admissions include observation; explicit inpatient and published specifications retain their scope. Default pharmacy spend nets all pharmacy transactions by event date. Medical spend/cost per claim uses payer paid on counted claims by service date.

## Verification

- 24 focused synthetic definition checks pass.
- Expanded acceptance: 328 tests passed, one optional fixture skipped. Latest focused definition run: 24/24 pass. Additional selector regression run: 96 tests, one optional skip and one error already present in the baseline (test_cohort_stack request-size check).
- Full regression: baseline 508 modules / 69 failing tests; candidate 509 modules / 61 failing tests; **0 new failing test names**. Eight baseline failure names are absent; some are timing/fixture-sensitive, so this is not a claim of eight independently established bug fixes. The full name sets and hashes are in `DEFINITIONS_2026-10-25_REGRESSION.json`.
- Baseline root is a verified clean 5179d29 checkout. Scoped baseline output: `/tmp/fail_base3_codex_definitions.txt`; candidate output: `/tmp/fail_codex_defs_verified.txt`. These unique paths preserve the result because the shared `/tmp/fail_base3.txt` was overwritten during the task. The requested candidate path `/tmp/fail_codex_defs.txt` now contains the same verified failure list.
- Historical VBC cascade certificate retained unchanged. The changed family reports uncalibrated rather than claiming the old certificate covers these definitions. No protected qualification was run.

## Files

| File | Change |
|---|---|
| `clinical_core/agere/clinical/aggregate.py` | Counts hospital stays from all-source inpatient and observation utilization events, including claims-only stays. |
| `clinical_core/agere/clinical/analysis_graph.py` | Selects payer-paid PMPM by default; explicit allowed amounts retain their requested basis. |
| `clinical_core/agere/clinical/analyst_insights.py` | Uses all event owners for event summaries; includes observation in generic admissions; nets pharmacy transactions; averages counted medical claims. |
| `clinical_core/agere/clinical/claims_composition.py` | Compiles default pharmacy spend against all transaction deltas by event date and medical cost per claim against all counted claims. |
| `clinical_core/agere/clinical/claims_language.py` | Shares transaction money/date vocabulary and counted medical claim defaults; retains explicit fill metrics. |
| `clinical_core/agere/clinical/cohort_families.py` | Reads the shared living scope and distinguishes generic hospital events from explicit inpatient events, retaining discharge disposition constraints. |
| `clinical_core/agere/clinical/cohorts.py` | Grounds hospital-stay cohort predicates in canonical all-source events without creating physical encounters. |
| `clinical_core/agere/clinical/comparison_composition.py` | Allows pharmacy transaction measures to use the existing named-group comparison grammar. |
| `clinical_core/agere/clinical/composition_language.py` | Describes event-rate denominators as all patients unless a living scope is explicit. |
| `clinical_core/agere/clinical/concepts.py` | Defines hospital_stay as the union of inpatient and observation; preserves the inpatient concept. |
| `clinical_core/agere/clinical/demand.py` | Adds all-source hospital admission volume, including observation, with the existing exact population/date restrictions. |
| `clinical_core/agere/clinical/graph_planner.py` | Advertises the hospital_admissions demand measure in the existing operator schema. |
| `clinical_core/agere/clinical/insight_families.py` | Separates living patient lists from all-patient event shares/rates, broadens generic admissions, and nets pharmacy transaction spend. |
| `clinical_core/agere/clinical/local_compiler.py` | Separates hospital and inpatient aliases in population, pathway, survival and forecast plans; increments compiler version. |
| `clinical_core/agere/clinical/monitor.py` | Includes observation in the admission coding-channel diagnostic. |
| `clinical_core/agere/clinical/pathway.py` | Pools inpatient and observation when the question requests the hospital-stay state, retaining explicit inpatient states. |
| `clinical_core/agere/clinical/population_engines.py` | Increments engine-form version so old definition contracts are not silently reused. |
| `clinical_core/agere/clinical/population_sql.py` | Reads living_only from decision context and exposes distinct patient and event population predicates plus hospital-event selection. |
| `clinical_core/agere/clinical/request_language.py` | Compiles admissions/hospitalizations/stays/discharges with observation and preserves explicit inpatient qualifiers; exposes exact-record pharmacy metadata. |
| `clinical_core/agere/clinical/request_plan.py` | Applies living restrictions by patient/event grain and joins pharmacy metadata only on a unique exact source record. |
| `clinical_core/agere/clinical/request_vocabulary.py` | Maps generic hospital admission rates to the new governed hospital metric. |
| `clinical_core/agere/clinical/semantic_layer.py` | Keeps the hospital union out of physical encounter projection, preserving published measure row counts. |
| `clinical_core/agere/clinical/sql_query.py` | Prevents event-per-patient rates from regaining a living filter during SQL enforcement while preserving lab enforcement and outer scope. |
| `clinical_core/agere/clinical/survival.py` | Reads the hospital-stay encounter selector from all-source inpatient and observation events. |
| `clinical_core/agere/clinical/trial_feasibility.py` | Evaluates hospital-stay encounter criteria against canonical inpatient and observation events. |
| `clinical_core/agere/clinical/vbc.py` | Uses payer paid on counted service-date medical claims and net pharmacy deltas by event month; retains explicitly requested allowed amounts. |
| `clinical_core/agere/clinical/vbc_families.py` | Renders the new medical/pharmacy finance definitions and increments its version; retains explicit inpatient/public-specification families. |
| `config/agere/metrics/utilization.json` | Adds hospital_admissions_per_1000 for inpatient plus observation and narrows the existing inpatient metric matcher to explicit inpatient wording. |
| `test_answer_shape_windows.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_cohort_families.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_composition_language.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_demand.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_dev_wrong_answers.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_dq_families.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_insight_generalization.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_mlr.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_pathway.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_population_forms.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_temporal_composition.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_vbc.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `test_warehouse_v22.py` | Updates synthetic fixtures or assertions for Mike 2026-10-25; preserves the independent calculation and existing test purpose. |
| `tools/calibration/family_variants.py` | Updates authored calibration variants and independent arithmetic to the new definitions; does not refit certificates or access protected evaluation. |
| `test_definitions_2026_10_25.py` | Adds 24 focused authored synthetic tests for all four definitions, execution/enforcement, all-source hospital selectors, and missing-transaction refusal. |
| `AGERE_BUILD_CONTROL.json` | Records this explicitly requested G3 definition increment, evidence, unsealed status, Git blocker and exact handoff action. |
| `docs/agere/DEFINITIONS_2026-10-25_CODEX.md` | Provides the file-by-file change report and delivery limitations. |
| `docs/agere/DEFINITIONS_2026-10-25_REGRESSION.json` | Preserves full baseline/candidate failure sets, counts, hashes and verification scope. |

## Delivery limits

Git staging failed with `Operation not permitted` creating `/Users/mikel/clinical-insights-local/.git/worktrees/codex-definitions/index.lock`. The shared Git directory is outside this session's writable sandbox. No commit SHA exists for this work; no branch/history or runtime was changed.

The continuity checker fails on a historical receipt absent from this worktree: `artifacts/agere-followup-code-status-ce09ee3/implementation-receipt.json`. This failure existed at turn start. The owner receipt remains unchanged because this branch has not been sealed or activated.

No edits to decision_context.py, proposers.py, backstop_agent.py, tools/dev_set/, published measure specifications, stored qualification results, the generator, or research corpus. The full product charter remains unchanged.

Overlapping hospital-union and explicit inpatient/observation pathway states require separate analyses; the engine refuses an ambiguous overlapping state model. Clinical/model qualification and runtime deployment remain separate from this deterministic engineering evidence.
