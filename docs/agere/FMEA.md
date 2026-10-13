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
| 2 | FM-23 | learning_reuse | Wrong answer captured as a verified training pair | 8 | 10 | 7 | 560 | 640 | 24 hosted-route engine errors / 132 | - |
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
| 14 | FM-14 | verification | Unit mismatch (cents vs dollars, days vs hours, fraction vs percent) | 8 | 6 | 3 | 144 | 240 | 1 adjudicated / 132; QC-UNITS fired on 3 wrong / 0 right | QC-UNITS |
| 15 | FM-11 | sql_generation | Nonexistent field, stream or join (hallucinated schema) | 8 | 6 | 3 | 144 | 144 | 1 adjudicated / 132 | - |
| 16 | FM-16 | execution | Partial current period in a time series (latest month/quarter/year only partly observed) | 6 | 7 | 3 | 126 | 378 | QC-PERIOD fired on 19 wrong / 0 right | QC-PERIOD |
| 17 | FM-04 | routing | False "data is missing" refusal on an answerable question | 5 | 8 | 3 | 120 | 160 | 4 adjudicated / 132; 37 scored / 1718 | - |
| 18 | FM-22 | learning_reuse | Stale learned plan reused after the data or semantic layer changed | 8 | 3 | 4 | 96 | 96 | judgment (no benchmark measures this mode) | - |
| 19 | FM-15 | execution | Execution error, timeout or no answer | 4 | 10 | 2 | 80 | 80 | 800 scored / 1718 | - |
| 20 | FM-12 | sql_generation | Wrong time window or as-of cutoff | 7 | 2 | 5 | 70 | 84 | 0 adjudicated / 132 | QC-NUMBERS |
| 21 | FM-20 | follow_ups | Follow-up rewrite drops or changes a slot | 7 | 3 | 3 | 63 | 63 | judgment (no benchmark measures this mode) | - |
| 22 | FM-18 | answer_rendering | Right numbers, wrong presentation (rounding, column names, split rows, partial flag on a right value) | 2 | 10 | 3 | 60 | 60 | 22 adjudicated / 132; 10 scored / 1718 | - |
| 23 | FM-17 | answer_rendering | Invalid answer contract or truncated result | 5 | 5 | 2 | 50 | 50 | 8 scored / 1718 | - |
| 24 | FM-03 | routing | Question routed to the wrong engine or rung | 6 | 2 | 4 | 48 | 48 | 0 acceptance fails / 140 | - |
| 25 | FM-21 | follow_ups | Cached plan or answer reused across a changed slot or context | 8 | 2 | 2 | 32 | 32 | judgment (no benchmark measures this mode) | - |
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

## Remaining gaps (not closed here)

- **FM-13 / FM-23, correlated errors and learning from them.** Every adjudicated hosted-route engine error passed lint,
  sanity and two-model agreement, and would have been captured as a verified pair. The answer checks are independent
  of the models but cover only part of the mode. Needed: quarantine verified pairs that carry quality warnings, and
  semantic-layer encodings of the mechanics behind FM-05/06/07 (measure age bands, attribution, value sets, clocks).
- **FM-05 / FM-06 definitions.** Measure denominators (HEDIS age bands, attribution) and value sets are not encoded for
  the SQL writer; QC-DENOM makes the denominator visible but cannot judge it.
- **Follow-ups (FM-20/21) and learning reuse (FM-22)** have no benchmark; occurrence is judged.
- **Adjudication coverage.** Blind sets K, L and P have no saved adjudication; their trend uses strict scores only.
