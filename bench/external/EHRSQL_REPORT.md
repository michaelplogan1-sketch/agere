# EHRSQL 2024: external public benchmark for Agere

Date: 2026-10-21. Branch `claude/ehrsql-2024`.

## What this is

[EHRSQL 2024](https://sites.google.com/view/ehrsql-2024) is the ClinicalNLP 2024 shared task on reliable
text-to-SQL over EHR data (Lee et al., [arXiv:2405.06673](https://arxiv.org/abs/2405.06673)). Questions are asked of
the MIMIC-IV demo database. About 20% of the validation and test questions can't be answered from the
schema, and the system is expected to abstain on those. The task is scored with the reliability score RS(c):
+1 for a correct answer, 0 for an abstention on an answerable question, −c for a wrong answer, +1 for abstaining on an
unanswerable question, and −c for answering one. RS(0) is plain accuracy. RS(N) sets c = N, so one wrong answer costs as
much as all N correct ones.

Data is public only: the EHRSQL question→SQL splits (CC-BY-4.0) and the open 100-patient MIMIC-IV Clinical
Database Demo 2.2 (ODbL). No PHI and no credentialed MIMIC-IV.

## Data and files

| Path | What |
|---|---|
| `artifacts/external/ehrsql-2024/download/` | the two downloaded archives: the EHRSQL repo at `f9e1aa0` (12.6 MB) and the PhysioNet demo zip (16.2 MB) |
| `artifacts/external/ehrsql-2024/mimic_iv.sqlite` | the organisers' preprocessed demo (time-shifted to 2100); the scoring DB, SQLite dialect |
| `artifacts/external/ehrsql-2024/mimic_iv.duckdb` | the same 17 tables in DuckDB (row counts identical) |
| `artifacts/external/ehrsql-2024/mimic_iv_demo_raw.duckdb` | the raw PhysioNet CSVs (31 tables), provenance only |
| `artifacts/external/ehrsql-2024/MANIFEST.json` (copy: `bench/external/ehrsql_manifest.json`) | licences, citations, sha256, row counts, split sizes |
| `bench/external/build_ehrsql_db.py` | rebuilds the DBs and the manifest from the downloads |
| `bench/external/ehrsql.py` | loader (bench question format), official scorer port, test denylist |
| `bench/external/ehrsql_test_denylist.json` | sha256 of the 1,167 test questions (normalised and exact) |
| `bench/external/ehrsql_run.py` | runs an Agere arm on a split and scores it |
| `bench/external/test_ehrsql.py` | 18 unit tests |

All 34 PhysioNet files match the published `SHA256SUMS.txt`. Executing every reference SQL against `mimic_iv.sqlite`
reproduces the organisers' `answer.json` exactly on train (5,124) and valid (1,163). Gold predictions score
RS(N) = 100, and abstaining on everything scores 19.95 on valid, which matches the published 20.0 Abstain-All baseline on test.

| Split | Questions | Unanswerable | Use |
|---|---|---|---|
| train | 5,124 | 450 (8.8%) | in-context examples now; text-to-SQL supervision later |
| valid | 1,163 | 232 (19.9%) | the numbers in this report |
| test | 1,167 | 233 (20.0%) | denylisted; `load_split('test')` refuses without `allow_test=True` |

## Test-split denylist

`ehrsql_test_denylist.json` stores two sha256 hashes per test question. One is over the normalised text (NFKC, casefold,
punctuation replaced by spaces, whitespace collapsed) and one is over the exact text. `bench.external.ehrsql.denied(question)` is
the check. The training exporter planned in `DUCKDB_PARQUET_MIGRATION_PLAN_2026-10-21.md` §5 should call it and fail
on any hit. That exporter doesn't exist yet, so this is the list plus the check function, not an enforced gate. The
example retriever in `ehrsql_run.py` already calls it. No train or valid question hits the list.

RESULTS_PLACEHOLDER
