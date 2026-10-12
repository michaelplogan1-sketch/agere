# Independent answer benchmark

Latest attempt per question; unrun questions are not passes. Reference results are SQL self-checks, not product accuracy.

| System | Attempted / suite | Correct | Coverage | Correct abstentions | False assertions | p50 / p95 seconds | Known API USD | Cost complete | USD / correct |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| agere | 3 / 50 | 0 | 0 | 0 | 0 | 23.8882 / 36.2189 | 0.2582 | True | unknown |
| frontier_sql | 2 / 50 | 2 | 1 | 1 | 0 | 20.3913 / 31.7752 | 0.1379 | True | 0.0690 |
| reference | 50 / 50 | 50 | 45 | 5 | 0 | 8.1353 / 17.2500 | 0.0000 | True | 0.0000 |

Common attempted questions across non-reference systems: 2.
Coverage counts complete structured answer attempts, including wrong answers; correctness also requires no unresolved members. Unsupported scoring checks explicit prerequisite phrases; it is a mechanical rubric, not independent clinical adjudication.

## Per question

| ID | Split | Type | agere | frontier_sql | reference |
|---|---|---|---|---|---|
| C01 | challenge | count | unrun | unrun | pass |
| C02 | challenge | count | unrun | unrun | pass |
| C03 | challenge | count | unrun | unrun | pass |
| C04 | challenge | count | unrun | unrun | pass |
| C05 | challenge | patient_set | unrun | unrun | pass |
| C06 | challenge | count | unrun | unrun | pass |
| C07 | challenge | count | unrun | unrun | pass |
| C08 | challenge | count | unrun | unrun | pass |
| C09 | challenge | count | unrun | unrun | pass |
| C10 | challenge | value | unrun | unrun | pass |
| C11 | challenge | patient_set | unrun | unrun | pass |
| C12 | challenge | count | unrun | unrun | pass |
| C13 | challenge | count | unrun | unrun | pass |
| C14 | challenge | table | unrun | unrun | pass |
| C15 | challenge | value | unrun | unrun | pass |
| C16 | challenge | unsupported | unrun | unrun | pass |
| C17 | challenge | unsupported | unrun | unrun | pass |
| C18 | challenge | unsupported | missing_answer | pass | pass |
| C19 | challenge | unsupported | unrun | unrun | pass |
| C20 | challenge | unsupported | unrun | unrun | pass |
| D01 | development | count | missing_answer | pass | pass |
| D02 | development | count | unrun | unrun | pass |
| D03 | development | count | unrun | unrun | pass |
| D04 | development | count | unrun | unrun | pass |
| D05 | development | patient_set | missing_answer | unrun | pass |
| D06 | development | count | unrun | unrun | pass |
| D07 | development | patient_set | unrun | unrun | pass |
| D08 | development | count | unrun | unrun | pass |
| D09 | development | count | unrun | unrun | pass |
| D10 | development | patient_set | unrun | unrun | pass |
| D11 | development | count | unrun | unrun | pass |
| D12 | development | count | unrun | unrun | pass |
| D13 | development | patient_set | unrun | unrun | pass |
| D14 | development | count | unrun | unrun | pass |
| D15 | development | table | unrun | unrun | pass |
| D16 | development | table | unrun | unrun | pass |
| D17 | development | count | unrun | unrun | pass |
| D18 | development | count | unrun | unrun | pass |
| D19 | development | count | unrun | unrun | pass |
| D20 | development | count | unrun | unrun | pass |
| D21 | development | count | unrun | unrun | pass |
| D22 | development | count | unrun | unrun | pass |
| D23 | development | count | unrun | unrun | pass |
| D24 | development | count | unrun | unrun | pass |
| D25 | development | count | unrun | unrun | pass |
| D26 | development | count | unrun | unrun | pass |
| D27 | development | value | unrun | unrun | pass |
| D28 | development | value | unrun | unrun | pass |
| D29 | development | count | unrun | unrun | pass |
| D30 | development | table | unrun | unrun | pass |

## Measurement limits

- Development and challenge cases were authored from received data; challenges are visible and are not an independently sealed clinical test set.
- Counts, units, date rules, tables and patient sets are checked against separate raw-source SQL. SQL semantics still require independent review.
- Latencies are observed endpoint/tool-loop times with existing caches; see each row for concurrency. No controlled cold-start or deployment performance claim.
- API estimates use recorded token usage and dated project prices. Local compute, engineering-chat usage and human review are excluded. Unknown costs stay unknown.
- Different suite/source identities must not be interpreted as a controlled comparison. Both systems must complete the same cases before comparing aggregate accuracy.

Run identities: `[["c428263f463d98b8c71d53bbdff4df59d56a1478e71440422313a9aaa1537397", "3f89188c6d529e1347161a558651583a80780e866edf44b45e8591e46109a206", "claude-opus-5-5"], ["c428263f463d98b8c71d53bbdff4df59d56a1478e71440422313a9aaa1537397", "3f89188c6d529e1347161a558651583a80780e866edf44b45e8591e46109a206", null]]`
