# Data connection architecture

Agere sits on top of a customer's whole warehouse and answers from all of it. The modeled (semantic) layer is a fast path for common questions. It is never the limit of what Agere can see.

## Principles
1. **Read the warehouse as it is.** Agere never requires data to be moved or restructured. It connects, profiles and maps.
2. **Every source is accounted for.** Each received table and field is either mapped to the canonical ontology or explicitly declared unused, with a reason and a reviewer. An unaccounted source blocks activation.
3. **Standard ontology, standard codes.** Canonical domains follow OMOP CDM and FHIR: person, coverage, visit, condition, procedure, drug exposure and fill, measurement, observation, claim, claim line, document, provider, organization. Concepts are defined by code sets (ICD-10-CM, SNOMED CT, LOINC, RxNorm, NDC, HCPCS/CPT, DRG, CARC/RARC), not by table names.
4. **Fast path, full fallback.** Modeled concepts and governed metrics answer exactly and cheaply. A question the model cannot answer goes to raw-catalog retrieval, and the answer names the raw sources it used.
5. **Never answer "none" without looking everywhere.** A zero, empty or implausible result triggers a full-catalog probe before it ships.
6. **Every answer shows its lineage:** the metric or engine and its version, plus the tables, fields and codes read.
7. **Every release is diffed.** Concept counts, source mix, field bindings and vocabularies are compared with the previous release. Silent widening or narrowing is flagged.

## Connection flow
| Step | What happens | Component |
|---|---|---|
| Connect | Read-only access to the warehouse (SQL, files, or native formats such as CCLF, X12 837/835, HL7 ADT/ORU, payer extracts) | adapters, raw ingestion |
| Profile | Every table, field, type, null rate, low-cardinality values and code columns, detected by pattern (ICD, LOINC, HCPCS, NDC, NPI, MBI) | source_profile |
| Map | Proposed mappings to canonical domains and concepts; standard-code columns auto-map, ambiguous mappings go to a review queue once and are reused after that | onboarding matcher |
| Verify | Round-trip aggregates, known-answer tests per concept, source coverage gate, release-diff guard | onboarding verify, coverage gate, diff guard |
| Activate | Goes live only when every source is accounted for and the verification receipts pass | warehouse_activation |
| Answer | Fast path → governed metrics and engines → raw-catalog SQL; empty-result probe; cross-check on uncertified routes | answer ladder |
| Learn | Verified answers carried across releases; frequently raw-answered questions promoted into the fast path | learning loop, compiler growth |

## Status (2026-10-14)
| Piece | State |
|---|---|
| Profile | Exists (source_profile); not yet used at answer time |
| Onboarding contract | Built and proven once (OMOP round trip 31/31); not yet the activation path for each release |
| Concept bindings | 60 concepts (conditions, measurements, encounters); services such as wellness visits were missing until semantic-layer/16 |
| Source coverage gate | In progress |
| Release-diff guard | In progress |
| Raw-catalog retrieval for the SQL writer | In progress |
| Empty-result catalog probe and cross-check | In progress |
| Source lineage in answers | Next |
| Auto-mapping by standard-code detection | Next |
| Native-format ingestion (CCLF, X12, HL7, payer extracts; v2.8/v2.9 raw/) | Next |

## Status (2026-10-17): customer onboarding built
| Piece | State |
|---|---|
| Connectors | Snowflake, BigQuery, Databricks (credentials from env; SDK required), Postgres, SQLite, DuckDB, file drops (CSV/TSV/JSON/JSONL/Parquet) |
| Workspace lifecycle | `onboarding/workspace.py`: profile -> auto-map -> auto-approve -> build -> raw passthrough -> verify -> generated docs -> ready |
| Review queue | Low-confidence fields, missing required fields, uncertain table choices; decisions persist and re-apply on rebuild |
| Raw tables | Every source table (or only unmapped ones) kept queryable as raw_<table> |
| Analyst docs | Generated per workspace from its own data (`hybrid/catalog.py`) |
| Answering | Hybrid core per workspace (`hybrid/live.py`), metered through the provider ledger |
| API and app | `/api/workspaces/*` and `/workspaces` (service, outside the global lock) |
