# Agere answer pipeline: FMEA

Failure Mode and Effects Analysis of the path from a typed question to a delivered answer, in the AIAG/VDA style
used in aerospace and automotive manufacturing. The machine-readable source is [`fmea.json`](fmea.json); the ranked
table below is regenerated from it by `.venv/bin/python tools/quality/defects.py --update-fmea`.

## Pipeline stages

| Stage | Where it lives | What can go wrong |
|---|---|---|
| Question parse | `local_compiler.py` (grammar, unexplained-word and unused-number guards), `analysis_graph.requested_shape` | a qualifier or a requested part is dropped; wrong answer shape |
| Routing | `economical_planner.py` rungs (compiler -> exact-question cache -> operator router -> verified SQL -> frontier), `operator_router.py` | wrong engine; false "data missing" refusal; answering the unanswerable |
| Population | cohort specs, VBC/measure denominators, `semantic_layer.py` | wrong denominator or population; source data defects |
| Definitions | concept library, `drug_classes`, `config/agere/definitions.json` | value set too narrow or broad; wrong clock |
| SQL generation | `nl2sql.py` | snapshot duplicates, undated records, wrong entity, hallucinated fields, wrong window |
| Verification | `verify.py` (lint, sanity, signature), two-model agreement, judge | correlated errors pass; unit mismatch |
| Execution | `sql_query`, analysis graph execution, service | errors and timeouts; partial current period |
| Answer rendering | `analysis_graph.build_answer` / `headline` / `standards` / `benchmark_references` | invalid contract; right numbers, wrong presentation; undisclosed definition |
| Follow-ups | `conversation.py`, `rewrite_dropped`, plan cache | slot dropped in a rewrite; cache reuse across a changed slot |
| Learning reuse | `learning.py` | stale learned plan; wrong answer captured as verified |

## How the numbers are set

- **Severity (S)** is judged from the effect on the reader: a wrong number delivered with confidence is 8, the same
  number carrying a "verified" receipt or a fabricated answer to an unanswerable question is 9, a refusal 5, a
  formatting difference 2.
- **Occurrence (O)** is measured wherever the benchmark can measure it (bands in `fmea.json`). Sources, in order of
  preference:
  1. *Adjudicated engine errors*: failures that `bench/adjudicate.py` classified as `engine_error`, assigned to one
     failure mode by the patterns in `fmea.json` (first match in `classification_order`). The latest adjudicated run
     of each blind set is used (M, N, O: 132 answerable questions). These adjudications were printed but not saved
     at the time; they were recovered from the session log into `bench/results/adjudications/`, and
     `bench/adjudicate.py` now saves every new adjudication there.
  2. *Scored failures* from every full blind run (bench scorer reasons: execution error, shape mismatch, false refusal,
     invalid contract, answering the unanswerable).
  3. *Control replay*: the new answer-level controls re-run on every preserved blind answer, counting how often they
     fire on answers the scorer failed.
  Modes the benchmark cannot see (follow-ups, cache reuse, stale learning, source data defects) carry an explicit
  judgment and say so.
- **Detection (D)** is how likely the failure is caught before the reader acts on it. `detection_before` is the state
  before the answer-level controls in `clinical_core/agere/clinical/answer_checks.py`; `detection` is after.
- **RPN** = S x O x D. The table is ranked by residual RPN.

## Ranked failure modes

<!-- fmea-table:start -->
| Rank | ID | Stage | Failure mode | S | O | D | RPN | RPN before | Evidence | Controls added |
|---:|---|---|---|---:|---:|---:|---:|---:|---|---|
| 1 | FM-13 | verification | Correlated two-model error passes the agreement check | 9 | 10 | 8 | 720 | 810 | 24 hosted-route engine errors / 132 | QC-CLOCK, QC-DENOM, QC-NUMBERS, QC-PARTS, QC-PERIOD, QC-UNITS, QC-VERSION |
| 2 | FM-23 | learning_reuse | Wrong answer captured as a verified training pair | 8 | 10 | 8 | 640 | 640 | 24 hosted-route engine errors / 132 | - |
| 3 | FM-05 | population | Wrong denominator or population restriction (age band, roster, attribution, organisation scope) | 8 | 8 | 6 | 384 | 512 | 4 adjudicated / 132 | QC-DENOM |
| 4 | FM-06 | definitions | Concept or value set too narrow or too broad (drug class, visit types, lab concept) | 7 | 8 | 6 | 336 | 336 | 6 adjudicated / 132 | - |
| 5 | FM-08 | sql_generation | Snapshot/version duplicates counted or summed (encounters, claims, appointments, accounts) | 8 | 9 | 4 | 288 | 432 | 7 adjudicated / 132; QC-VERSION fired on 11 wrong / 13 right | QC-VERSION |
| 6 | FM-01 | question_parse | Explicit qualifier or requested part silently dropped (in-person, first-generation, same draw time, "how many and how much") | 8 | 7 | 5 | 280 | 392 | 2 adjudicated / 132; QC-NUMBERS fired on 5 wrong / 0 right | QC-NUMBERS, QC-PARTS |
| 7 | FM-07 | definitions | Wrong clock or date field (arrival vs event, submission vs adjudication) | 7 | 8 | 5 | 280 | 392 | 3 adjudicated / 132; QC-CLOCK fired on 4 wrong / 0 right | QC-CLOCK |
| 8 | FM-09 | sql_generation | Undated or null records mishandled (undated rows picked as latest, undated baseline joins drop everyone) | 7 | 6 | 6 | 252 | 252 | 1 adjudicated / 132 | - |
| 9 | FM-10 | sql_generation | Wrong entity counted (patients vs rows vs encounters) | 8 | 6 | 5 | 240 | 240 | 1 adjudicated / 132 | - |
| 10 | FM-24 | routing | Answers a question the data cannot support | 9 | 8 | 3 | 216 | 216 | 6 scored / 212 | - |
| 11 | FM-19 | answer_rendering | Defensible alternative definition used without the user seeing it | 4 | 10 | 5 | 200 | 240 | 14 adjudicated / 132 | QC-DENOM |
| 12 | FM-25 | population | Source data defects distort results (feed gaps, partial feeds, generator defects) | 6 | 6 | 5 | 180 | 180 | judgment (no benchmark measures this mode) | - |
| 13 | FM-02 | question_parse | Wrong answer shape (count vs list vs rate vs table) | 5 | 8 | 4 | 160 | 160 | 1 adjudicated / 132; 63 scored / 1718 | - |
| 14 | FM-11 | sql_generation | Nonexistent field, stream or join (hallucinated schema) | 8 | 6 | 3 | 144 | 144 | 1 adjudicated / 132 | - |
| 15 | FM-14 | verification | Unit mismatch (cents vs dollars, days vs hours, fraction vs percent) | 8 | 6 | 3 | 144 | 240 | 1 adjudicated / 132; QC-UNITS fired on 3 wrong / 0 right | QC-UNITS |
| 16 | FM-16 | execution | Partial current period in a time series (latest month/quarter/year only partly observed) | 6 | 7 | 3 | 126 | 378 | QC-PERIOD fired on 19 wrong / 0 right | QC-PERIOD |
| 17 | FM-33 | verification | The benchmark reference itself is wrong, so a correct engine answer is counted as a defect (or a wrong one as correct) | 6 | 5 | 4 | 120 | 210 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | MUT-AUDIT |
| 18 | FM-04 | routing | False "data is missing" refusal on an answerable question | 5 | 8 | 3 | 120 | 120 | 4 adjudicated / 132; 37 scored / 1718 | - |
| 19 | FM-22 | learning_reuse | Stale learned plan reused after the data or semantic layer changed | 8 | 3 | 4 | 96 | 96 | judgment (no benchmark measures this mode) | - |
| 20 | FM-26 | routing | Exact-local recognizer answers a narrower or broader population than asked (e.g. all AFib vs CHA2DS2-VASc >= 2) | 8 | 4 | 3 | 96 | 256 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | CT-FIT |
| 21 | FM-27 | answer_rendering | A correct empty answer is rejected by the shape / zero-result checks and a worse proposer answers | 7 | 4 | 3 | 84 | 196 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | CT-SHAPE |
| 22 | FM-15 | execution | Execution error, timeout or no answer | 4 | 10 | 2 | 80 | 80 | 800 scored / 1718 | - |
| 23 | FM-28 | verification | System One label "unknown", an error or an outage silently becomes a documented reason (or "none documented") | 8 | 3 | 3 | 72 | 168 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | CT-S1-REVIEW |
| 24 | FM-35 | execution | Production defect rate drifts (refusals, internal errors, user-reported wrong answers) with nobody noticing | 6 | 4 | 3 | 72 | 192 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | SIX-SIGMA-P |
| 25 | FM-12 | sql_generation | Wrong time window or as-of cutoff | 7 | 2 | 5 | 70 | 84 | 0 adjudicated / 132 | QC-NUMBERS |
| 26 | FM-20 | follow_ups | Follow-up rewrite drops or changes a slot | 7 | 3 | 3 | 63 | 63 | judgment (no benchmark measures this mode) | - |
| 27 | FM-32 | question_parse | A sequence window in weeks / months / years compiles to the wrong day bound, or a "first" anchor matches every event | 7 | 3 | 3 | 63 | 147 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | SEQ-WINDOW, SEQ-RANK |
| 28 | FM-18 | answer_rendering | Right numbers, wrong presentation (rounding, column names, split rows, partial flag on a right value) | 2 | 10 | 3 | 60 | 60 | 22 adjudicated / 132; 10 scored / 1718 | - |
| 29 | FM-17 | answer_rendering | Invalid answer contract or truncated result | 5 | 5 | 2 | 50 | 50 | 8 scored / 1718 | - |
| 30 | FM-31 | verification | Claude outage: the System One fallback returns a candidate a check judged to answer a different question | 8 | 2 | 3 | 48 | 48 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | - |
| 31 | FM-03 | routing | Question routed to the wrong engine or rung | 6 | 2 | 4 | 48 | 48 | 0 acceptance fails / 140 | - |
| 32 | FM-30 | execution | A paid System One / Jev / Perplexity call never reaches the cascade usage or receipt | 5 | 3 | 3 | 45 | 90 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | CT-S1-COST |
| 33 | FM-34 | learning_reuse | Sealed truth rows leak into Agere (printed, saved or trained on) | 9 | 2 | 2 | 36 | 108 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | SEALED-AGG |
| 34 | FM-29 | routing | System One routing drops or permanently skips a cascade tier | 6 | 2 | 3 | 36 | 36 | judgment (no benchmark measures this mode; 2026-10-22 cascade review) | - |
| 35 | FM-21 | follow_ups | Cached plan or answer reused across a changed slot or context | 8 | 2 | 2 | 32 | 32 | judgment (no benchmark measures this mode) | - |
<!-- fmea-table:end -->

## Controls added (poka-yoke, heal-not-block)

All controls live in `clinical_core/agere/clinical/answer_checks.py` and run once in `analysis_graph.execute` after the
answer contract is built, before the headline is written. They never change a number, the status or the answer type.
A finding is added to `answer.quality_checks` (control id, FMEA id, severity, message); warnings also go to
`answer.warnings`, which the headline and the `/ask` card already display.

| Control | Mode | What it checks | Fired on wrong / right answers (replay, all full blind runs, 2026-10-13) |
|---|---|---|---|
| QC-PERIOD | FM-16 | a time-series table whose latest month/quarter/year (or week) is cut short by the snapshot date; states days observed of days in period | 19 wrong / 0 right |
| QC-UNITS | FM-14 | percentage columns outside 0-100, cents returned for a dollar question, hours computed from whole-day LOS x 24 or answered in days | 3 / 0 |
| QC-DENOM | FM-05, FM-19 | rate/percent/share answers: reads the denominator from the query and states it (`answer.denominator`, info); warns only when a scalar rate states no denominator at all | 39 / 45 (info only; states the denominator) |
| QC-VERSION | FM-08 | count/sum over raw `record` rows of a versioned stream without DISTINCT, GROUP BY the business key or `current_record` | 11 / 13 |
| QC-NUMBERS | FM-01, FM-12 | a number or date in the question that the SQL never uses (look-back windows written as dates, spelled-out numbers and the snapshot date are recognised) | 5 / 0 |
| QC-PARTS | FM-01 | "how many ... and how much" answered with only the count or only the amount | 2 / 0 |
| QC-CLOCK | FM-07 | a timing question measured to `available_date` / `first_available_date` (arrival in the data) when the question does not ask about arrival | 4 / 0 |

Replay counts every full blind run, so a question re-run several times counts each time. The counts are printed by `tools/quality/defects.py` and recorded per mode in
`fmea.json` (`evidence.control_replay`). They are associations with failure, not proof that the flagged issue caused it.

## Verification v2 (FM-13, FM-23, FM-08/09, FM-19), 2026-10-13

- **Capture after checks (FM-23).** The planner no longer records verified pairs. It leaves the candidate pair in
  `planner_receipt.hosted_sql.capture`, and `conversation.handle_compose` calls `learning.capture_verified` after the
  answer has been built and annotated. A pair is recorded only when it carries no warning from `QUARANTINE_CONTROLS`,
  the judge's query raised no oracle finding, no independent candidate disagrees, and there are not two defensible
  readings. Anything else goes to the `quarantined` table with its reasons. `learning.quarantine` remains as a backstop.
- **Independent candidates (FM-13).** See `verification_v2.py`. Candidates come from the hosted family, the bottled local
  model (when `MODEL_DIR` is present) and the relaxed compiler. They are executed, then clustered by result. Two agreeing
  families are accepted. When only hosted models agree, the answer is accepted only if no blocking oracle or answer check
  fires; otherwise it goes to the judge, which sees the findings. A relaxed-compiler dissent blocks capture but does not
  route by default (`AGERE_COMPILER_DISSENT=route`). When the primary matches a free candidate, the second hosted call is
  skipped. `AGERE_VERIFICATION_V2=off` restores the two-model ladder.
- **Oracles (`oracles.py`, also run by `answer_checks.annotate`).**
  - QC-PERTURB covers slot/operator binding, look-back windows and stray years.
  - QC-PARTITION covers the TLP identity, plus undated rows a window drops and rows compared with distinct business keys.
    Both of those findings are info only.
  - QC-SHAPE flags a rate answered as a list or count.
  - QC-EMPTY flags a zero or empty answer to a count or list question.
- **Readings (FM-19).** When two candidates differ by a stated DEFINITION dimension and their result shape and magnitude
  are comparable, `answer.readings` holds both readings with the default first, plus one clarifying question. A
  QC-READINGS warning is added, and `/ask` shows the two readings side by side.
- **Calibrated gates.** `calibrated_thresholds.py` and `tools/calibration/fit_thresholds.py` use Learn-then-Test. The target
  is error among accepted ≤ 2% with 90% confidence. `semantic_cache.similarity` was certified at 0.810 on 1,003
  non-benchmark eval cases (295 accepted, 0 wrong), with the search floor at 0.80. The learned router (264 held-out cases),
  the decider (6 delivered picks) and the typed route (0) could not be certified. Their hand-set values remain in force,
  and each record states why.

Replay over saved receipts (`tools/quality/verification_replay.py` → `verification_v2_replay.json`). The scope is the
hosted route only, in the latest adjudicated M/N/O runs: 24 engine errors and 59 right answers. No model is called, and
the local model is not run.

| Policy | Wrong caught (routed or warned) | Right flagged | Extra judge calls | Second calls saved |
|---|---:|---:|---:|---:|
| default | 8 / 24 (+1 via readings) | 1 / 59 (1.7%) | 4 | 3 |
| compiler dissent routes | 11 / 24 | 7 / 59 (11.9%) | 13 | 3 |

Capture: wrong pairs recorded as verified fall from 24 to 12, and right pairs from 59 to 51. The precision of each
control on M/N/O, counted as fired on wrong / fired on right, is:

| Control | Wrong / right |
|---|---|
| QC-CLOCK | 3/0 |
| QC-UNITS | 1/0 |
| QC-SHAPE | 1/0 |
| QC-EMPTY | 1/0 (8/0 over all full blind runs) |
| lint (`lower(drug_class) LIKE`) | 2/1 |
| QC-PARTITION, info only | 1/4 |
| QC-PERTURB | 0/0 after the `= 1` flag fix |

## Metric layer (FM-05, FM-06, FM-07, FM-08), 2026-10-13

`config/agere/metrics/*.json` and `clinical_core/agere/clinical/metrics.py` hold typed metric definitions. Each one states
its grain, population (age band, attribution), denominator, numerator, time anchor (date field, period, trailing window,
snapshot, follow-up days), value sets with provenance, versioning rule, unit and expected range, citations, and a
canonical SQL template over the semantic layer. There are 35 templated metrics: utilization 14, revenue cycle 10,
clinical quality 6 and operations 5. Each of the 25 measures in `config/agere/measures` also becomes a metric backed by
the measure engine. Every metric either cites a source (definitions.json, a measure, or a direct citation) or is marked
internal and states why.

- **Prompt pins.** When a question matches a metric, `nl2sql.metric_context` puts the metric's definition and its
  rendered SQL template into the hosted prompt. Matching uses deterministic rules first, then the local encoder.
- **Lint rules ML-\*.** `verify.metric_lint` runs a deterministic match only. It checks grain, anchor, window, value set,
  population, source and versioning. Value-set membership is judged against the received values in the semantic layer.
  - The ladder sends a failing query back for one repair. Findings that survive the repair are delivered as warnings
    (`AGERE_METRIC_LINT=repair|warn|off`).
  - A trailing window that starts one day early is healed mechanically before the lint runs.
- **Answer annotation.** `answer_checks` adds `computed_as` (metric id and version) and QC-METRIC findings. QC-METRIC
  quarantines capture.
- **Resolution API.** `metrics.resolve(slots)` is the entry point for the compiler-rules layer and the engines.

Replay over saved receipts (`tools/quality/metric_layer_replay.py` → `metric_layer_replay.json`): the same 24 wrong and
59 right hosted answers as Verification v2. The SQL was re-executed read-only and no model was called. These numbers are
in-sample, because the metrics were written after reading these failures.

| | Count |
|---|---:|
| v2-uncaught errors that resolve to a metric | 16 / 16 |
| … flagged by metric lint or heal | 14 / 16 (13 by lint after heal, 1 by heal alone: M20) |
| … where the metric template reproduces the reference | 14 / 16 (O11 and O31 differ only in which age field is used) |
| All wrong answers caught (v2 + metric layer) | 22 / 24 |
| Right answers flagged | 3 / 59. All three are the healed trailing-window start (M14, N15, O13); the result is unchanged and nothing blocks |

The two errors that are not flagged:

- M05: the delivered query returned a table where a patient list was expected; this is a shape problem.
- N09: re-executed today, the delivered SQL returns the reference answer. The original error came from the layer as it
  was then.

The first replay also mismatched N43 to `community_referral_delivery`. Its match rule was tightened after that replay
(in-sample).

Value-set and anchor findings, all review items and nothing auto-added:

- `stay.discharge_date` is the UTC date of the discharge time. For 31 of 53 discharges in the trailing year it is one
  day later than the ADT discharge event date, and that shifts follow-up windows. N14 reproduces only when the ADT date
  is used. This belongs to the semantic layer's owner.
- Library concept `egfr` includes "Glomerular filtration rate": 568 rows, undated, mL/min, no LOINC.
- `therapeutic_category LIKE '%antihistamine%'` sweeps in 2,209 second-generation antihistamine rows.
- `drug_class LIKE '%beta%'` sweeps in 607 beta-agonist rows.
- The normalization dictionary maps "youth_health_supervision" (396) and "Adolescent med" to primary care, but they are
  not in `primary_care_visit`. This is a possible undercount.
- Condition concepts miss no ICD-10-coded row. Most condition rows are label- or SNOMED-only (201 of 6,929 carry a
  source ICD-10).
- The benchmark references disagree on which age field to use: O11, O31 and N07 use roster `age_reported`, while M33
  uses the latest age. The metrics pin `age_latest`, following the nl2sql rules; the owner decides.

## Current cascade and System One (FM-26 to FM-35), 2026-10-22

The question path is now the checked-proposer cascade (`proposers.py`): exact tiers (contract cache, learned shapes,
fully consumed grammar incl. temporal sequences), then proposers cheapest first, each accepted only after the literal
check, read-only execution, the output-shape check and `answer_checks.gate`. System One (Jev, then Perplexity) orders
the tiers (`system_one_gate.route_receipt`), checks executed answers, answers atomic per-patient sub-questions
(`atomic_questions`) and picks among executed candidates when Claude is out (`system_one_fallback`).

| Stage | Where it lives | What can go wrong (mode) |
|---|---|---|
| Exact local tiers | `economical_planner.local_plan` (incl. `clinical_tasks.local_question`), `request_language` | recognizer substitutes a population (FM-26); temporal unit / anchor misread (FM-32) |
| System One routing | `system_one_gate.route_receipt` | a tier dropped (FM-29); spend not charged (FM-30) |
| Output checks | `literal_check.shape_misfit`, `answer_checks.gate` (zero-result probe) | a correct empty answer rejected and replaced (FM-27) |
| Atomic System One | `atomic_questions.run`, `clinical_tasks.review` | unknown / error read as a reason (FM-28); spend not charged (FM-30) |
| Claude outage | `Cascade.system_one_fallback` | different-question candidate returned (FM-31) |
| Evaluation | `bench/mutation_audit.py`, `tools/quality/sealed_gate.py`, `quality_scorecard.py` | wrong reference (FM-33); sealed truth leak (FM-34); unnoticed production drift (FM-35) |

### Fault tree: a wrong answer is delivered without a warning (top event)

```
TOP  wrong answer delivered silently
 OR  G1 question misread and accepted
      OR  exact recognizer substitutes population/threshold ............ FM-26  [CT-FIT, literal check]
      OR  temporal window/anchor compiled wrong .......................... FM-32  [SEQ-WINDOW, SEQ-RANK, numeric check]
      OR  rewording drops a qualifier .................................... FM-01  [literal check, QC-NUMBERS]
 OR  G2 right reading, wrong data or definition
      OR  value set / denominator / clock ............................... FM-05/06/07 [QC-DENOM, QC-CLOCK]
      OR  versioned rows double counted .................................. FM-08  [QC-VERSION]
 OR  G3 checks pass a wrong answer
      AND correlated model error ........................................ FM-13
          AND agreement + lint pass (no independent oracle)
      OR  System One unknown/error read as definite ...................... FM-28  [CT-S1-REVIEW]
      OR  fallback returns a different-question candidate ................ FM-31  [fallback pool filter]
 OR  G4 checks reject the right answer and a worse one wins
      OR  correct empty set fails the shape check ........................ FM-27  [CT-SHAPE]
 OR  G5 measurement hides it
      OR  benchmark reference wrong ...................................... FM-33  [MUT-AUDIT]
      OR  production drift unobserved .................................... FM-35  [SIX-SIGMA-P, /api/scorecard]
```
Minimal cut sets of size one (single points of failure) are FM-26, FM-27, FM-28, FM-31 and FM-32; each now has a
control with a unit test. FM-13 needs two events (shared misreading AND no independent oracle) and stays the top RPN.

### Controls added 2026-10-22 (each with a test)

| Control | Mode | Where | Test |
|---|---|---|---|
| CT-FIT | FM-26 | `clinical_tasks.fits` | `test_clinical_tasks.RecognizerTests` |
| CT-SHAPE | FM-27 | `clinical_tasks.answer` (requested shape, one row per patient) | `test_clinical_tasks.EngineTests` |
| CT-S1-REVIEW | FM-28 | `clinical_tasks.review` | `test_clinical_tasks.ReviewTests` |
| CT-S1-COST | FM-30 | `clinical_tasks.review` + `analysis_graph` engine review hook | `test_clinical_tasks.ReviewTests` |
| SEQ-WINDOW, SEQ-RANK | FM-32 | `request_plan.Compiler.sequence`, `request_language.Parser.sequence` | `test_temporal_composition` |
| MUT-AUDIT | FM-33 | `bench/mutation_audit.py`, `bench/adjudicate.py` | `test_mutation_audit` |
| SEALED-AGG | FM-34 | `tools/quality/sealed_gate.aggregates` | `test_quality_gates.SealedGateTests` |
| SIX-SIGMA-P | FM-35 | `clinical_core/agere/clinical/quality_scorecard.py`, `/api/scorecard` | `test_quality_gates.ProductionTests` |

## Remaining gaps (not closed here)

- **FM-13 / FM-23, correlated errors and learning from them.** Every adjudicated hosted-route engine error passed lint,
  sanity and two-model agreement, and would have been captured as a verified pair. The answer checks are independent
  of the models but cover only part of the mode. Needed: quarantine verified pairs that carry quality warnings, and
  semantic-layer encodings of the mechanics behind FM-05/06/07 (measure age bands, attribution, value sets, clocks).
- **FM-05 / FM-06 definitions.** Measure denominators (HEDIS age bands, attribution) and value sets are not encoded for
  the SQL writer; QC-DENOM makes the denominator visible but cannot judge it.
- **Follow-ups (FM-20/21) and learning reuse (FM-22)** have no benchmark; occurrence is judged. FM-26 to FM-35 occurrence is
  judged too until the production p-chart (FM-35) has enough days to measure it.
- **FM-27 beyond clinical tasks.** Other engines still return an empty table for an empty patient set; the shape check can
  reject them the same way.
- **Adjudication coverage.** Blind sets K, L and P have no saved adjudication; their trend uses strict scores only.
