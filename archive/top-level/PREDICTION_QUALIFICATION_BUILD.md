# Active-source prediction qualification — October 10, 2026

The active warehouse now has five source-bound protected model-build reports connected to its actual outcome questions. The prior active `/api/stack/outcome-learning` returned no attached reports. Both index-day baseline questions and receipt-time decision questions now open readable qualification counts, class shortfalls, exact received-evidence examples and the saved model-build result. The new `/api/stack/prediction-qualification` endpoint exposes the same registry; the legacy endpoint uses it when installed and otherwise preserves its prior behavior.

**No predictor qualifies for fitting.** This delivery connects the current failed qualifications honestly; it does not manufacture useful forecasts or treat recorded zeros as clinical negatives.

## Architecture and boundaries

The new registry adapts the existing `baseline_evidence`, `decision_landmarks` and `outcome_learning` machinery. Their task definitions, labels, feature cutoffs, grouping rules, reserved cohorts and fitting plan are unchanged. The five tasks and plan were saved before this build inspected their exports. No target windows were tuned to seek class variation.

Feature and target files remain separate. Original patient families, repeated text, shared records and supplied households retain the existing transitive protection logic. The original 460 nontraining and 52 reserved patients remain excluded from development. The runtime view exposes aggregate qualification and received-source references; it does not expose private family assignments as patient evidence. The underlying protected rows remain offline build artifacts.

Each attached report verifies its implementation, source snapshot, current evidence identity, task parameters, X/y hashes, lineage-policy hash, protected register, qualification and model report. Fitted fixture weights and development-prediction files are also verified without loading model pickles. A stale or altered report becomes unavailable with a reason, while valid sibling reports remain inspectable. Invalid attachment leaves the previous pointer intact; activation uses atomic replacement. The interface selects reports by exact dataset identity and labels their saved build population separately from a question's selected population.

## Actual v1.23 results

All tasks assess the unchanged original cohort of **1,614 synthetic patients**, using the declared cutoff **2029-08-15**.

| Task | Timing-eligible recorded labels | Eligible after development protections | Train / calibration / development-validation | Status |
|---|---:|---:|---|---|
| Discharge after admission notification | 5 positive | 2 | 2 positive / 0 / 0 | Not fitted |
| Admission after discharge, source dates | 11 zero | 7 | 6 zero / 1 zero / 0 | Not fitted |
| Admission after discharge, clinical dates | 18 zero | 12 | 11 zero / 1 zero / 0 | Not fitted |
| Future discharge after admission receipt | 7 zero | 6 | 6 zero / 0 / 0 | Not fitted |
| Future admission after discharge receipt | 74 zero | 43 | 35 zero / 3 zero / 5 zero | Not fitted |

Fitting requires both recorded classes in each development subdivision, under the unchanged engineering minimums of 10/5/5 per class. Those minima are not clinical statistical guarantees. Source capture, adjudicated endpoints, clinically useful risk calibration, intervention benefit and independent clinical validation remain unestablished. No weights were generated for these five tasks. Older failure receipts remain unchanged.

## Verification

The final focused suite passed **59 tests, including 13 new registry cases**. It covers both adapters, exact joins, source and implementation changes, export/report tampering, partition protections, mid-build mutations, partial report failure, pointer preservation, fixture-fitted weight/prediction integrity and explicit no-fit behavior. Existing fitting fixture results are not clinical performance evidence.

The independent full-cohort audit verified **23,728 source-reference occurrences** across the five exports (1,352 / 7,054 / 6,360 / 425 / 8,537), **94 index-clock dependencies** and **29 historical endpoint exclusions** across the receipt-time tasks. It checked exact raw-record hashes, patient joins, source/arrival cutoffs, future-window semantics, class counts and protected group separation.

All five real question → report → patient-source HTTP paths passed. Their recorded question times were 0.581–1.673 seconds with the existing runtime cache; the five-report status request took 1.466 seconds. These are individual local measurements, not general performance guarantees. Browser verification confirmed the receipt-time report, its class-deficit table and patient-source inspection. The final screenshot is saved with the receipt. Python/JavaScript syntax checks pass.

Evidence: `artifacts/agere-prediction-qualification-v1/implementation-receipt.json`, `source-verification.json`, `http-verification.json`, the five separate datasets/build reports, `focused-tests.log`, `preservation.json` and browser evidence.

## Preservation and continuation

The prior 78 protected files, eight paid answers, six buyer tasks, functional work history and 19 separate Documents research files remain unchanged/current. This module extends the protected list to 79. Source/configuration, frozen Kev/d1 experiments and earlier model failures are preserved. No new provider calls, token-count calls, human reviews or external actions occurred; accounting remains uncapped and unchanged. Server PID 861 / session 43730 serves port 8885.

Full product acceptance remains incomplete. Do not rerun these known single-class tasks without changed source evidence or a separately justified, predeclared task. Continue independent work on audited stale-answer retirement, richer clinical and benefit applicability, buyer execution adapters and concrete research integration. Qualified prediction requires defensible endpoint variation and evaluation, not additional model complexity alone.
