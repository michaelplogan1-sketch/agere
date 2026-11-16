# Program and financial evidence accountability

Delivered October 9, 2026 against the active original-cohort v1.18 synthetic warehouse. This increment connects financial account evidence to immutable source-review queues and program assessments. It does not install a payer agreement or establish payment eligibility.

## What now executes

- `accountability_review` accepts any complete selected patient set, a versioned evidence-prerequisite definition and a received-by cutoff. Its saved proposal contains every selected patient's receipt, exact financial dependencies, and internal review requests. Previews and queue pages do not truncate the assessed population.
- Account discrepancies produce account-specific requests with source IDs, unresolved clocks/links, accounting residuals and received-cash differences. Unassigned financial sources produce a separate patient-level binding-review request. Reconciled accounts do not generate discrepancy tasks.
- Coverage reports, service-specific coverage verifications and care-team relationships provide exact source context. They do not satisfy patient attribution or payer-contract requirements.
- The development definition separately requests effective contract terms, performance/payment periods, measure/payment rules, patient attribution and program/outcome links. Contract-level requests are stored once per selected-population proposal; patient-specific requirements remain attached to each patient. All five currently lack installed verified bindings.
- A worklist can take an earlier accountability result as an optional second, typed context input. The first input must still be a population. Financial results cannot become clinical membership. Clinical/protocol evidence and financial/contract evidence remain separate in each immutable assessment.
- `/programs` now offers **Attach financial and contract evidence** when preparing a full-population worklist. `/stack` has complete-population and clinical-to-program examples, queue pagination and exact patient evidence inspection. `/api/stack/accountability` exposes the same saved evidence.
- Changed source data or definitions invalidate affected patient context before work is saved or advanced. An unchanged patient's context remains usable when another patient changes. Prior proposals, assessments and events remain retained. A definition cannot change under its already registered version.

The portable requirement definition (`accountability_contracts.json`) and source mapping (`accountability_source_maps.json`) are separate. The input-binding evaluator can retain exact patient-level stream/field inputs under a new definition version. Mere field presence remains `received_input_requires_review`. Global contract-document interpretation and verified term activation are still missing engineering; the present global prerequisites are explicitly unbound.

## Current source evidence and results

All numbers below describe synthetic received records or engineering checks, not clinical accuracy.

| Quantity | Complete population | Supported reduced-EF review population |
|---|---:|---:|
| Patients assessed | 1,614 | 14 |
| Financial account-review requests | 9,806 | 196 |
| Patient-level financial account-binding reviews | 1,583 | 14 |
| Patient-specific program prerequisite reviews | 3,228 | 28 |
| Shared contract prerequisite reviews | 3 | 3 |
| Total internal requests | 14,620 | 241 |
| Coverage/care-team context records retained | 31,494 | 495 |

Sources: `artifacts/agere-program-accountability-v1/verification.json`, `http-verification.json`, `source-context-audit.json`. Every account-review set was compared with its saved financial reconciliation, and every source context hash/field was checked against a separate query of the received warehouse. All 31,494 context records matched. Those sources do not establish contract attribution.

The clinical-to-program graph preserves all 14 selected assessments with both protocol and accountability receipts. The standalone program workspace deliberately uses its existing broader HF workflow: **292 review candidates**, comprising 278 unresolved-phenotype reviews and 14 low-EF treatment-evidence reviews, among 1,614 assessed patients. Adding accountability preserves that workflow's counts. These are different population definitions, not conflicting estimates of confirmed HFrEF prevalence. See `workspace-http-verification.json` and `workspace-initial-assertion.json`.

The public-input inventory reads the top-level fields of all 1,049,339 received records across 113 streams. It identifies no structured VBC contract/attribution/performance-period binding. This field inventory is not an exhaustive semantic search of notes or proof that agreements are absent from all documents/external systems. `unbound` is the correct current state.

## Verification and economics

- 69 focused checks cover graph input types, full populations, source changes, unaffected-patient reuse, immutable definitions/history, existing program lifecycle behavior, source context and financial reconciliation. The final transaction fix was followed by 10 accountability checks. Do not sum the suites as distinct tests.
- Initial large-cohort execution exposed a SQLite cache-spill lock when schema registration reopened the receipt database inside a large write transaction. The transaction rolled back. The final dependency check now reads the separate source index without reopening the receipt database; the full run succeeds. The failure receipt is preserved.
- Both real local questions, exact replay, live API pagination and source inspection pass. The clinical subset reuses all 14 patient accountability and financial receipts.
- The program workspace's final API check compares attachment-on versus the same existing attachment-off workflow. Invalid programs/contracts are rejected; the unrelated cohort endpoint still works.
- Browser DOM verification covers the queue, exact patient evidence and workspace attachment control, with no captured JavaScript errors. Pixel screenshots are not verified.
- Offline simulated frontier-wire check still fits the existing 20,000-byte bound: selection 18,660 bytes, selected plan 16,031 bytes. It is a contract/wire test, not a live model-quality result.
- **Zero new paid API calls, zero external actions, zero live work items created, no new model weights or human reviews.** Proposals and evidence receipts were persisted locally. Local hardware/runtime cost is not priced by the API ledger.

## Remaining capabilities

Actual contract ingestion, clause interpretation/review, effective-version activation, contract-specific attribution, denominators, outcomes, risk adjustment and settlement calculations remain unsupported in this path. Existing source amounts and staff-reported completion cannot fill those gaps. No savings, hardship, provider-quality ranking or clinical benefit is established.

Next implementation: make persistent review work consume reusable protocol definitions beyond the two existing hard-coded HF/affordability workflow adapters. Reuse current protocol/source receipts, preserve all supported/unresolved/nonmatching states and version changes, and expose those programs through the question/API/workspace path. Then continue source-backed program/measure intake and broader explanation/measurement work. The full autonomous queue is not complete; current-source classifiers remain shadow-only and prospective predictors remain unqualified.

## Reproducibility

- Build: `.venv/bin/python build_program_accountability_stack.py --output <fresh-directory>`.
- Live verification: `.venv/bin/python verify_program_accountability_http.py --output <artifact-directory>`.
- Workspace attachment: `.venv/bin/python verify_accountability_workspace.py` (uses the fixed milestone artifact directory).
- Tests: `.venv/bin/python -m unittest test_program_accountability test_analysis_graph test_program_work test_protocol_source_context test_financial_accounts test_planner_scope -q`.
- Detailed artifacts, initial failures and implementation manifest: `artifacts/agere-program-accountability-v1/`.
