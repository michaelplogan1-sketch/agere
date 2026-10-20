# Shared planner: capable interpretation and connected synthetic work

G3 now has an active, checked hosted interpreter behind the shared request compiler. The local service uses complete deterministic parsing when available and the configured frontier model for unfamiliar wording. Mike explicitly corrected the spending interpretation: use the best capable way to answer; no assistant-selected zero-spend restriction or monetary ceiling. Usage accounting, bounded inputs, caching and failed-request protections remain.

## Delivered behavior

The configured Claude Opus 5.5 interpreter proposes the existing typed request, never executable model SQL. It receives received-schema definitions, canonical operator parameter schemas, analytical capability descriptions and additional schema on request. The compiler validates the source-bound contract. A separately prompted same-model pass checks the original question against both the actual SQL and the analytical parameters. This is an engineering check, not independent clinical/model qualification.

An unfamiliar question no longer waits for repeated unqualified Qwen guesses before escalation. Successful checked hosted interpretations can answer observed-data questions and prepare synthetic work. Local/injected development proposals remain candidates. Explicit frontier selection is honored. Deterministic and exact-source cached questions require no new hosted call. Cache identity includes the code, field definitions, operator contracts, model, source and saved population.

Corrections discovered during actual execution:

- The review previously invented missing relationships because it could not see implicit joins and correlated EXISTS. It now sees compiled SQL, semantic definitions and engine schemas.
- Discontiguous wording can bind to several literal spans instead of being falsely marked unresolved. Fixed-window fields such as readmitted_30d retain their actual numeric meaning.
- Engine selection previously had names without useful capability/schema context. PMPM, survival, outreach and case-mix-adjusted aggregation now route automatically through the same cohort contract.
- SQL uses explicit comparison bounds; existing engine date windows are inclusive. Engine contracts and review now expose that difference. The inspected calendar-year engine requests all use January 1 through December 31.
- Multiple analyses retain separately rendered answers, rather than dropping every output except the last.
- The answer wrapper previously replaced a selected outreach worklist with its input population. It now preserves exactly the selected subset, checks that it is inside the original cohort, and prevents ambiguous combinations of different worklists from creating work.
- Wording-based metric annotations could overwrite the executed engine's definition with obsolete catalog prose. A sealed engine receipt now remains the method authority.
- Unknown API pricing is no longer represented as a complete zero-cost estimate. Known API usage remains distinct from total compute/development/review cost.

## Evidence

86 focused regression tests pass, including 29 shared-planner tests. Existing VBC empty-slice numerical warnings remain. Twelve predeclared handwritten development cases pass: clinical grouping and polarity, financial threshold mutation, ratios, operational counts, entity-specific claim linkage, a new sensor table, exact saved scope, aggregate thresholds, and refusal of an unavailable preference constraint. These are development fixtures, not a benchmark, clinical validation or universal accuracy estimate.

Four actual warehouse questions automatically select and execute vbc, survival, outreach and aggregate. Dates are checked against the declared 2029 window. The outreach result has exactly ten selected IDs, not the 230-person input diabetes cohort. Numbers returned by those engines have not been independently clinically adjudicated; model-based outreach values remain assumptions and simulations, not realized savings or demonstrated clinical benefit.

The actual local API additionally passes:

1. Deterministic 4,115-patient count with zero new hosted calls.
2. A new PCP/diabetes grouped-count question with a greater-than-two group threshold, matching separate direct SQL; its exact repeat makes zero new calls.
3. A saved-cohort age filter retaining only the exact parent population.
4. One compound question returning both PMPM and a 90-day readmission survival analysis.
5. A ten-slot outreach result saved with exactly ten patients, ten synthetic review tasks, a command-center mockup and one completed five-step simulated journey. No real outreach or care occurs.

Browser accessibility inspection confirms ten patients, ten work cards and the visible “Demo: complete · Journey complete” label on the completed simulation. Review status remains separate from simulation progress.

The fresh grouped question took 13.75 seconds and $0.306712 in estimated API usage; its exact replay took 0.319 seconds with no new API usage. The compound question took 13.56 seconds and $0.221843. These are individual engineering observations, not a controlled cost comparison; local compute, development and human review are unpriced.

Evidence is in `artifacts/agere-shared-frontier-v1/`: retained failed initial probes, corrected probes, declared acceptance cases/results, three actual-engine iterations, live API log, live summary, browser check and tests.log. No protected benchmark was run or rescored. Existing engines' internal diagnostic outputs are not being promoted to externally validated results.

## Runtime and remaining scope

Runtime source: 48c39e3 in /Users/mikel/agere-mvp-run, port 8885, hosted execution enabled. Verified PID 38117 / exec session 24366. Recovery source: c706f5f; its hosted-disabled/unqualified-local limitations remain documented. The pre-existing v2.8 configuration overlay, warehouse, models and saved histories were preserved.

Example workspace: http://127.0.0.1:8885/action-center?context=ANS-069b10292a8721e64b3e574d

G3 remains active; G5 remains blocked on independent qualification and the full ten-area acceptance flag remains false. This delivery closes the measured unfamiliar-language routing failure and the tested question-to-synthetic-work connection. It does not finish current-medication/clinical temporal reconciliation, qualify every operator/data field, support arbitrary new analytical methods, establish fair comparative economics, or prove every possible question can be answered. Ambiguity and unmet requirements must stay explicit. The original HF/GDMT/current-dose requirement is not declared solved by these observed-data tests.

Next implementation: close the remaining clinical/time/evidence interpretation gaps through shared primitives and existing engines, with changed-condition/window/polarity checks and end-to-end saved-work evidence. Preserve the original question and the entire ten-area charter. Do not reintroduce per-question handlers, substitute extra model catalogs for product work, or run unvalidated benchmarks.
