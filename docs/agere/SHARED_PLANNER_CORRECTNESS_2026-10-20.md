# Shared planner A–C correctness handoff

Date: 2026-10-20. Candidate source: `/Users/mikel/clinical-insights-local`.
Gate: G3 implementation work; no G3/G5 acceptance promotion.
Integration and deployment remain with the build owner. This chat did not update the live service at :8885, the active warehouse selection, BUILD_OWNER.json, AGERE_BUILD_CONTROL.json, or PARALLEL_WORK.md.

## Result

A–C are implemented in the single shared request/compiler/graph path and ready for a fresh-copy review. Existing governed metric and quality/RAF engines supply the definitions. Legacy answer routes were not reinstated.

- **A — age:** `age.age_at(birth_date, as_of)` supplies analytical age in Python and SQLite, including filters, groups, averages, cohorts, safety, scheduling, quality and risk calculations. The request catalog hides `age_latest`; SQL executors reject it. The historical storage column remains for compatibility, but there are no analytical reads. Explicit questions about captured reported age can still request `age_reported`. Missing, invalid and future birth dates remain unknown. CMS RAF uses the same function at its specified payment-year reference date and no longer substitutes feed age for missing birth date.
- **B — local coverage:** a versioned request vocabulary binds denial, no-show, PMPM, readmission, LOS, RAF, utilization and quality definitions, plus named condition, drug-fill and encounter concepts. Metric parameters carry an ID, version and definition digest. The compiler validates and dispatches them through the registered `governed_metric` graph operation. Population restrictions apply to numerator and denominator. Grammar supports bare business phrasing and independent payer, period, grouping and exclusion modifiers.
- **B — reusable interpretations:** a complete, executed, model-checked hosted interpretation may seed a request shape with typed numeric/date/quoted-string slots. New literals are substituted into executable bindings and the entire original question recompiles. Unmatched extra clauses cannot be dropped. Source identity, schema, policy/definition identity and population bind reuse. Corrections normalize case, whitespace and trailing punctuation, revoke the family, evict exact caches and prevent silent reactivation. This is model-checked reuse, not independent adjudication or automatic weight training.
- **B — concurrency and accounting:** language interpretation releases the service's shared answer lock; an isolated Qwen process has a bounded timeout and nonblocking busy check. Provider calls retain usage on failed interpretation and missing cost receipts remain unknown. A slow interpreter cannot hold up unrelated grammar answers.
- **C — ED:** ED visits, ER visits and emergency visits compile to deduplicated `utilization_event` ED rows. Answers include source combinations and the count outside EHR, derived using the same filters. The named `ed_visits` measure is version 2 and uses this definition too.

## Evidence

Warehouse source digest: `de1e487344c2c5662c55cbe8302497f559bd7cff85d06572f78b1fb2fabedacd`.
As-of: 2030-05-27. The warehouse was read through mode=ro connections in a temporary cache-free workspace.

- **Age > 65: 886**, equal to a separate direct SQL birth-date calculation.
- **2029 ED events: 1,501**, equal to a direct all-source total, including a claims-only member.
- **Local routing: 36 base development questions + 144 modifier variants = 180 compiled and executed, with model calls forbidden. Frontier calls per 100 questions: 0.** The fixed workload and its hash are recorded in the regression JSON. This is an authored development workload covering the supplied examples and requested domains, not proof against the unspecified original 36 questions or the sealed evaluation.
- **311 tests run: 304 passed, 7 skipped:** 191 in the main suite (7 skipped), 120 in the compatibility suite. These include hand-computed fixture arithmetic for scoped metric denominators, actual production graph dispatch/version retention, age boundaries, claims-only ED coverage, checked unfamiliar wording reused with new values and years, correction/source/policy/population invalidation, unsupported-clause rejection, failure accounting, and interpreter concurrency/timeout behavior.
- `git diff --check` and the G3 continuity checker pass. The continuity checker is not product or clinical acceptance.

Artifacts: `artifacts/shared-planner-correctness-2026-10-20/regression.json`, `tests.log`, `integration-tests.log`, `implementation.patch`, and `implementation-manifest.json`.

Reproduce the fixed workload:

```sh
.venv/bin/python verify_shared_planner_correctness.py --execute --output artifacts/shared-planner-correctness-2026-10-20/regression.json
```

Main test suite:

```sh
.venv/bin/python -m unittest test_shared_planner test_shared_planner_correctness test_compiler_rules test_metric_layer test_vbc test_measures test_scheduling test_risk_adjustment test_aggregate_explain test_learning -q
```

Compatibility suite (explicit fixture classes, no legacy reference-reproduction test):

```sh
.venv/bin/python -m unittest test_metric_first.SchemaTests test_metric_first.FixtureCompute test_metric_first.RouteTests test_compiler_answer_shape test_hybrid test_analysis_graph test_graph_contracts test_learning_cohorts test_cohort_stack -q
```

## Remaining limits and integration notes

One workload answer, `RAF by payer`, is partial because some payer groups have no qualifying inputs. All 180 requests compile and execute locally; that does not mean all data needed for every result exists. Empty denominators remain null. No model output or invented zero fills these gaps.

Request shapes deliberately preserve wording and predicate structure; they generalize bound literals and calendar periods, not arbitrary paraphrases. Unit fixtures exercise hosted acceptance with controlled model responses; this work made no paid frontier calls. The separate legacy verified/training store was not promoted as the shared planner's reuse authority.

The original broad `test_metric_first` sweep also selected two obsolete old-route ordering tests and a historical reference-reproduction test. The latter stopped on a missing age UDF in its direct test connection; no benchmark score is claimed. Final verification uses the explicit non-reference fixture classes above. Old-route ordering is intentionally not restored. Existing model-load and empty-slice warnings in the main suite did not fail tests.

No new independent clinical adjudication, protected head-to-head evaluation or benchmark superiority claim was made. The separate 20-item audit remains Mike's agree/change/drop decision. Audit answers were not modified or used as development fixtures.

Next step: integration owner review this exact patch/manifest, run the fresh-copy checks, then manage deployment and the central continuity checkpoint. All broader product charter and G5 qualification requirements remain open as recorded by the owner.

## Follow-up: modifier order and ordinary phrasing

After the original candidate was integrated as 5e70b6d and deployed by the owner in 780816d, Mike asked this lane to continue. The new candidate is based on 270c3b4 and changes only `request_language.py`, its new `test_request_language_composition.py`, and this handoff note.

Measured failure: seven of twelve diagnostic phrases still required language-model fallback, including `no-show rate by payer in 2029`, `PMPM by payer in 2029 excluding ckd`, `patients on a statin by payer in 2029 excluding ckd`, `How many patients are over 65?`, and `average age by payer`. Grouping previously captured all text to the end of a question.

The shared grammar now consumes catalog-backed grouping dimensions independently of the remaining predicates and period, preserves literal source spans, and supports unambiguous field-based aggregates and ordinary copular phrasing. Literal values containing grouping/date words are masked during syntax recognition. Unknown constraints and ambiguous event periods continue to decline. No new answer path or metric definition was added.

Verification on this candidate:

- The same twelve diagnostic phrases improve from **5/12 to 12/12 locally compiled**, with no model calls. These are development probes, not an unseen benchmark.
- **112 focused tests pass**, including all six orders of period/group/exclusion for governed denial rate and ED event counts; medication-period binding; age and sum arithmetic; quoted values; unknown clauses; original shared-planner correctness; owner-added concept-quote protections; conversation/workflow integration; and graph contracts.
- The original, unchanged fixed workload remains **180/180 compiled and executed, 0 frontier calls per 100 questions**. Age and ED direct totals remain 886 and 1,501.
- Separate handwritten warehouse SQL matches every row for `Count ED visits by payer in 2029 excluding ckd` and `average age by payer` (17 payer groups each).
- No paid API calls, protected evaluation execution, source-data changes, control changes or live-runtime changes were made in this follow-up.

Evidence and exact patch: `artifacts/shared-planner-modifier-order-2026-10-20/`. The original candidate is deployed; this follow-up remains a frozen integration candidate. The integration owner retains deployment and continuity-checkpoint ownership.

Reproduce the focused suite:

```sh
.venv/bin/python -m unittest test_request_language_composition test_shared_planner test_shared_planner_correctness test_frontier_conversation_integration test_answer_workflow test_analysis_graph test_graph_contracts -q
```
