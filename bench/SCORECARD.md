> **Superseded (2026-10-13).** This table records the 2026-10-12 run, when Agere's service route was failing (39 execution errors). It does not describe the current stack. Current results: the live `/scorecard` page (blind sets with adjudicated error rates), `bench/smoke.py` and `bench/acceptance.py`.

# Independent answer benchmark

Latest attempt per question, rescored from preserved answers with the current scorer; unrun questions are not passes. Reference results are SQL self-checks, not product accuracy.

| System | Attempted / suite | Correct | Value matches | Coverage | Correct abstentions | False assertions | Execution errors | Response p50 / p95 seconds | Known API USD | Cost complete | USD / correct |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| agere | 50 / 50 | 1 | 8 | 5 | 0 | 0 | 39 | 111.4968 / 127.7915 | 1.3810 | False | unknown |
| frontier_sql | 50 / 50 | 49 | 44 | 45 | 5 | 1 | 0 | 38.6797 / 135.1645 | 21.1163 | True | 0.4309 |
| reference | 50 / 50 | 50 | 45 | 45 | 5 | 0 | 0 | 8.1353 / 17.2500 | 0.0000 | True | 0.0000 |

Common attempted questions across non-reference systems: 50.
Coverage counts answered structured attempts without declared missing requirements, including wrong answers. An unknown_count can describe explicitly excluded records and does not by itself invalidate an exact scoped answer. Unsupported scoring checks explicit prerequisite phrases; it is a mechanical rubric, not independent clinical adjudication.
Value matches include matching scalar values returned in the wrong count/value field or marked partial; they are not complete contract passes. Correct abstentions are counted separately. False assertions require a comparable structured value that disagrees with the requested scope; the scorer does not adjudicate every prose claim. A disclosed alternative interpretation can still fail exact-answer scoring.

## Common attempted questions

| System | Correct / common | Coverage | Known API USD | USD / correct |
|---|---:|---:|---:|---:|
| agere | 1 / 50 | 5 | 1.3810 | unknown |
| frontier_sql | 49 / 50 | 45 | 21.1163 | 0.4309 |

## Recorded spend across all attempts

These totals include prior failed/replaced attempts; the tables above use only the latest attempt per question.

| System | Attempts | Known API USD | Attempts with unknown cost |
|---|---:|---:|---:|
| agere | 56 | 1.756732 | 41 |
| frontier_sql | 50 | 21.116276 | 0 |
| reference | 100 | 0.000000 | 1 |

## Per answer type

| Type | agere | frontier_sql | reference |
|---|---|---|---|
| count | 0 correct / 31 attempted / 31 cases | 31 correct / 31 attempted / 31 cases | 31 correct / 31 attempted / 31 cases |
| patient_set | 1 correct / 6 attempted / 6 cases | 6 correct / 6 attempted / 6 cases | 6 correct / 6 attempted / 6 cases |
| value | 0 correct / 4 attempted / 4 cases | 4 correct / 4 attempted / 4 cases | 4 correct / 4 attempted / 4 cases |
| table | 0 correct / 4 attempted / 4 cases | 3 correct / 4 attempted / 4 cases | 4 correct / 4 attempted / 4 cases |
| unsupported | 0 correct / 5 attempted / 5 cases | 5 correct / 5 attempted / 5 cases | 5 correct / 5 attempted / 5 cases |

## Per question

| ID | Split | Type | agere | frontier_sql | reference |
|---|---|---|---|---|---|
| C01 | challenge | count | error | pass | pass |
| C02 | challenge | count | answer_type_mismatch | pass | pass |
| C03 | challenge | count | answer_type_mismatch | pass | pass |
| C04 | challenge | count | answer_type_mismatch | pass | pass |
| C05 | challenge | patient_set | error | pass | pass |
| C06 | challenge | count | error | pass | pass |
| C07 | challenge | count | error | pass | pass |
| C08 | challenge | count | error | pass | pass |
| C09 | challenge | count | error | pass | pass |
| C10 | challenge | value | error | pass | pass |
| C11 | challenge | patient_set | error | pass | pass |
| C12 | challenge | count | error | pass | pass |
| C13 | challenge | count | error | pass | pass |
| C14 | challenge | table | error | pass | pass |
| C15 | challenge | value | error | pass | pass |
| C16 | challenge | unsupported | error | pass | pass |
| C17 | challenge | unsupported | error | pass | pass |
| C18 | challenge | unsupported | invalid_answer_contract | pass | pass |
| C19 | challenge | unsupported | unsupported_contract_mismatch | pass | pass |
| C20 | challenge | unsupported | error | pass | pass |
| D01 | development | count | error | pass | pass |
| D02 | development | count | error | pass | pass |
| D03 | development | count | error | pass | pass |
| D04 | development | count | answer_type_mismatch | pass | pass |
| D05 | development | patient_set | pass | pass | pass |
| D06 | development | count | answer_type_mismatch | pass | pass |
| D07 | development | patient_set | incomplete_answer | pass | pass |
| D08 | development | count | answer_type_mismatch | pass | pass |
| D09 | development | count | answer_type_mismatch | pass | pass |
| D10 | development | patient_set | error | pass | pass |
| D11 | development | count | error | pass | pass |
| D12 | development | count | error | pass | pass |
| D13 | development | patient_set | error | pass | pass |
| D14 | development | count | error | pass | pass |
| D15 | development | table | error | pass | pass |
| D16 | development | table | error | answer_mismatch | pass |
| D17 | development | count | error | pass | pass |
| D18 | development | count | error | pass | pass |
| D19 | development | count | error | pass | pass |
| D20 | development | count | error | pass | pass |
| D21 | development | count | error | pass | pass |
| D22 | development | count | error | pass | pass |
| D23 | development | count | error | pass | pass |
| D24 | development | count | error | pass | pass |
| D25 | development | count | error | pass | pass |
| D26 | development | count | error | pass | pass |
| D27 | development | value | error | pass | pass |
| D28 | development | value | error | pass | pass |
| D29 | development | count | error | pass | pass |
| D30 | development | table | error | pass | pass |

## Measurement limits

- Development and challenge cases were authored from received data; challenges are visible and are not an independently sealed clinical test set.
- Counts, units, date rules, tables and patient sets are checked against separate raw-source SQL. SQL semantics still require independent review.
- Latency percentiles use completed responses without execution errors, including incorrect answers; fast connection refusals are excluded. Raw attempts retain every elapsed time. Existing caches and concurrency vary; this is not controlled deployment performance.
- API estimates use recorded token usage and dated project prices. Local compute, engineering-chat usage and human review are excluded. Unknown costs stay unknown.
- Different suite/source identities must not be interpreted as a controlled comparison. Both systems must complete the same cases before comparing aggregate accuracy.
- Execution errors include unavailable/interrupted endpoints; these are operational failures, not evidence that the model gave an incorrect clinical answer.

Run identities: `[["c428263f463d98b8c71d53bbdff4df59d56a1478e71440422313a9aaa1537397", "3f89188c6d529e1347161a558651583a80780e866edf44b45e8591e46109a206", "claude-opus-5-5"], ["c428263f463d98b8c71d53bbdff4df59d56a1478e71440422313a9aaa1537397", "3f89188c6d529e1347161a558651583a80780e866edf44b45e8591e46109a206", null]]`
