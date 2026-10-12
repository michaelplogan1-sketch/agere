# Response to the external Agere review — October 12, 2026

Implementation and automatic continuation remain PAUSED at the user's explicit request. This is a review and a proposed change of sequence, not a product delivery or authorization to implement it. No product code, warehouse, frozen experiment, model, saved answer, or design checkout was changed. No new product inference was requested. The last sealed delivery remains GRAPH_OBSERVATION_BUILD.md.

## Assessment

The review's central criticism is valid: the volume of connected engineering and preservation checks has outpaced evidence of broad, useful answer correctness. A basic count exposes a semantic failure. The next hypothesis layer should be deferred while we establish independently checked question-to-answer acceptance and correct the shared vocabulary/measurement path. I own that sequencing problem. The prior architecture reviews had already identified integration debt and insufficient end-to-end qualification; further receipts did not resolve it.

Useful work exists: deterministic population operators, source clocks and provenance, evidence reconciliation, selected raw-source audits, durable questions and work, and product inference accounting. Those components do not establish a competitive analytics product. Unit-test counts, executable plans, partial answers, and conservative unknowns must not substitute for answering the question correctly.

## Verified failure, with corrections to the external diagnosis

Reviewed the saved provider plan and evaluated its pure predicates against the current received warehouse using read-only SQLite. No live compose request or cache-writing cohort selection was executed.

Saved plan: `artifacts/agere-healthcare-v123/stack/provider-receipts/49adf01afb22612aaf1da1a78266ebbbc831c6fb3272c32775b09bb076fbce9a.json` (text response only).

It interprets the question as documented diabetes plus **any observed** HbA1c value greater than 9 at snapshot 2029-08-15. This is not a latest-result or current-disease-control definition.

| Check | Observed result |
|---|---|
| Condition rows | 6,929; 201 have `icd10`, 6,728 have `snomed` |
| Lab rows | 128,066; none contains `code`, `code_system`, `loinc`, or `loinc_code`; `name` and adapter alias `test` are present |
| P-1008 | ICD-10 E11.9/E11.319; received HbA1c 9.4% plus later observed values above 9 |
| P-4007 | ICD-10 E10.9/E10.10; received HbA1c 11.8% |
| Saved diabetes predicate, both patients | `true` |
| Saved A1c predicate, both patients | `unknown` |
| Independent literal SQL join below | P-1008 and P-4007 |

The plan requires lab `code` values 4548-4 or 17856-6, although that field is absent. It does not bind the available HbA1c names or require percent units. That is the immediate demonstrated failure for these two patients. Restricting diagnoses to ICD-10 is a separate broad coverage problem, but it does not explain these misses. The plan itself declares its code values and units unvalidated; executing it anyway does not satisfy the analytical request.

Consequently, the external review's claimed reference answer of **one** is incomplete under the saved interpretation: there are **at least two clear documented positives**. This is not a claim that a complete SNOMED-aware phenotype, current A1c status, or all narrative evidence has been adjudicated. The reported 0 matched / 1,227 unknown is not a defensible complete zero answer either. Both the product and its benchmark need an explicit semantic contract.

Reproducible independent SQL against `artifacts/agere-healthcare-v123/stack/stack.sqlite3`, opened with `mode=ro`:

```sql
SELECT DISTINCT c.patient
FROM records c JOIN records l ON l.patient = c.patient
WHERE c.stream = 'conditions'
  AND substr(json_extract(c.payload, '$.icd10'), 1, 3)
      IN ('E08', 'E09', 'E10', 'E11', 'E13')
  AND l.stream = 'labs'
  AND json_extract(l.payload, '$.test') IN
      ('HbA1c', 'Hemoglobin A1c', 'Hemoglobin A1c/Hemoglobin.total')
  AND json_type(l.payload, '$.value') IN ('integer', 'real')
  AND json_extract(l.payload, '$.value') > 9
  AND json_extract(l.payload, '$.unit') = '%'
ORDER BY c.patient;
-- P-1008, P-4007
```

Trace reproduction uses `cohorts.validate` and `cohorts.evaluate_patient` with the saved n1/n2 specifications and the patient's received conditions, labs, and problem_lists. These functions returned true/unknown for both people. `cohorts.select` was deliberately not used because it writes result caches.

## Other findings: agreement and limits

- **Broad evaluation gap: agree.** The absolute statement that no reference checks or costs/latencies exist is inaccurate. `audit_pre_event_state.py` and `artifacts/agere-pre-event-state-v1/source-audit.json` independently check literal source fields, dates, and membership; prior build reports record product calls and elapsed time. Those are narrow development audits, not the broad independently adjudicated answer benchmark or matched frontier-plus-SQL baseline we need. Cost per independently correct answer at stated coverage remains unestablished.
- **Offline generality: agree.** `stack_runtime.ask(live=False)` explicitly returns retrieval, and `/compose` can invoke the frontier planner on a live-enabled server. The local recipes and exact compilers do not establish broad offline question answering. Search and answer modes, and their inference policy, need clear contracts. Paid frontier planning is appropriate when its measured benefit warrants it; merely using a frontier model is not the defect.
- **Data grounding: insufficient, not wholly absent.** `graph_planner.propose` supplies capabilities and a bounded observed vocabulary. In the saved A1c plan the relevant vocabulary is empty and the code assumptions remain unvalidated. The missing capability is query-specific field/value/unit coverage validation and semantic resolution before execution.
- **Architecture: agree on the debt.** Runtime installation hooks, broad implementation-hash invalidation, multiple stores, and a large route dispatcher make changes harder to reason about. `.git` is absent. Replace these incrementally with explicit module interfaces and versioned semantic contracts behind regression checks. Retain immutable experimental artifacts and source provenance; freezing active production source forever and freezing an evaluation result are different requirements.
- **Retrieval: profile before replacing.** Dense retrieval is an exact matrix scan, but the implementation can restrict it to eligible embedded rows. The backing file's size is not proof that every request scans every byte. Separate startup/hash checking, retrieval, planning, deterministic execution, and rendering timings before choosing an index.
- **Jev: reuse only what the evidence supports.** This review did not verify a 96–99% terminology-mapping result. Existing bounded readers and historical measurements need their task, inputs, labels, and failure cases identified before calling them a solved SNOMED/ICD/LOINC normalization layer. A model's classification score is not proof of safe terminology equivalence.
- **Insight engine: valuable prior work with limited comparative claims.** `/Users/mikel/insight-engine/reports/REPORT.md` contains the cited 7/7, 5/7, 2/7 result for one synthetic hard world, plus other synthetic-world evaluations. Its handoff explicitly says the TextQL-class and Arcadia-class baselines are local reimplementations, not vendor products. Its frontier baseline receives summary tables and 60 charts, not a general SQL tool over equivalent full evidence. Reuse discovery, reconciliation, and falsification methods; do not convert these numbers into vendor superiority or transfer them to the current Agere warehouse.
- **Storage: real concern; deletion estimate unverified.** The three cited artifact directories report roughly 245 GiB combined through `du`; the volume reports 81% used. The release code attempts APFS clone copies for files and SQLite backup for databases. Logical sizes, shared extents, mutable databases, and unique evidence must be distinguished before estimating physical space recoverable. No full duplicate-content audit or deletion was performed. The separate generator stays read-only.
- **Process: simplify.** Maintain one current checkpoint and one compact results table per substantive increment. Historical continuity is useful, but document count, hash-check count, and module count should not lead progress reporting. Exact claims such as 50 documents/day and 75% process were not independently audited; they are unnecessary to establish the priority problem.

## Proposed sequence on explicit resumption

1. **Establish a recoverable code baseline and a small independent answer suite.** Initialize Git locally with code/config/tests and appropriate ignores; keep secrets, large data, models, and immutable artifacts outside source history. Start with 30 representative questions spanning counts/cohorts, temporal comparisons, source reconciliation, provider/population variation, and financial prerequisites, with 20 separate challenge cases. Predefine interpretation, as-of dates, populations, units, and expected supported/unsupported behavior. Have reference queries reviewed independently of product operators. Expand toward 200 after the harness works; building the benchmark must not become a planning-only phase. Existing frozen evaluation families stay untouched.
2. **Deliver the benchmark together with a general repair.** Add a source-preserving analytical contract for clinical concepts and observations: coding system/version, literal source labels, value/type/unit, patient identity, event versus arrival clocks, and mapping confidence/provenance. Validate code coverage before running a plan. Correct the diabetes/A1c class of failure through reusable concept/measurement resolution, not an exact-question handler. Distinguish unavailable field/system, unmapped concept, conflicting evidence, unknown date, and missing observation. A technical mapping failure must not become thousands of patient-level clinical unknowns.
3. **Measure against competent baselines.** Compare deterministic reference queries, a frontier model with read-only SQL tools and the same visible evidence, and Agere. SQL can verify counts and arithmetic; it cannot alone adjudicate why a patient was readmitted, causal effects, or a contract that is absent. Use evidence-based rubrics and independent review for those tasks. Measure exact patient-set/count correctness, supported-claim accuracy, useful-answer coverage, justified abstention, false assertions, citation support, cold/warm p50/p95 latency, product inference tokens/cost, and cost per correct useful answer at matched coverage. Track engineering-chat usage separately when available.
4. **Simplify execution where measured failures require it.** Push set operations and arithmetic into typed indexed tables/views. Choose SQLite versus DuckDB/Parquet using workload evidence and migration/reconciliation cost; changing storage technology alone will not fix missing semantics. Replace runtime patches with explicit registration/dependencies along this tested path. Separate implementation provenance hashes from deliberate compatibility/version contracts. Preserve existing saved decisions as historical evidence and migrate only with demonstrated equivalence or explicit changed semantics.
5. **Resume differentiating capability through the same acceptance path.** Integrate insight discovery, temporal/source-process reasoning, contradiction handling, targeted evidence acquisition, and provider/financial analysis as they improve useful answers on new combinations. Test ablations: simple SQL, frontier plus tools, and Agere with/without each proposed layer. Qualify a smaller planner only on corrected, independently reviewed plans with separated families; never treat its own saved outputs as ground truth. Cache validated parameterized plans with concept/schema/source/time dependencies, not loosely similar answers.

The first implementation delivery should therefore include a runnable evaluation harness, a measured before/after for the shared concept/measurement failure, transparent unsupported reasons, and the connected answer in the API and MVP. It should not claim a whole benchmark is passed because two diabetes examples now work. Show broader regression results and held-out failures. Keep the design direction from “Find Agere site inspiration,” with current product promises tied to delivered behavior.

This sequence supports master-plan areas 1 and 4 (source meaning and interpretation), 5 (qualification), and 10 (broad product acceptance), while protecting prerequisites for areas 7–9 (prediction, discovery, provider/financial performance). All ten areas remain in the canonical backlog. The hypothesis increment is deferred, not discarded. G3 remains unfinished and G5 qualification remains open; no gates were promoted by this review.

## Review boundary

Checks performed: read-only warehouse queries, pure saved-predicate evaluation, selected code/report inspection, and continuity preflight. This was not a full test-suite rerun, clinical adjudication, current vendor evaluation, UI acceptance run, or storage deduplication audit. No new product inference, implementation, Git initialization, source migration, deletion, or automation activation occurred. Only this review and pause/continuity records were written. Resume requires explicit user direction.
