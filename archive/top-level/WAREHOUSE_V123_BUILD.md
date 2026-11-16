# Agere received-source integration, v1.23

October 10, 2026. All patients and records are synthetic. The full Agere product remains unfinished.

## Activation status

Qualified and activated. `config/agere/active_warehouse.json` points to `artifacts/agere-healthcare-v123`; the prior connection is preserved in activation history. Owned server **13112** now serves http://127.0.0.1:8885/stack. All build and qualification jobs completed. Actual HTTP question/patient/measure endpoints and served JavaScript passed. Browser DOM checks confirmed current source counts, transport results and full-population measure composition; no console errors were returned. Screenshot tooling returned a blank background, so no pixel-layout check is claimed. See `activation.json`, `source-extension/http-verification.json` and `source-extension/browser-verification.json`.

## Connected capabilities

The v1.23 adapter transports 1,238,686 received records across 158 source categories for the same 1,614 synthetic patients. Every one of the prior 1,049,339 rows remains unchanged. The increment adds 189,347 records, including 12,503 records in 45 newly mapped categories. No private generator answers enter runtime evidence. Source and manifest identities are pinned in `artifacts/agere-warehouse-v123-inventory/inventory.json`; the candidate connection is `artifacts/agere-healthcare-v123/connection.json`.

A separate source catalog now carries hospital, procedure/pathology, prescription reassessment/renewal, home-health and community-support accounts into the reusable evidence layer. It preserves literal fields, source clocks, typed same-patient references and unresolved links. Narrative transport preserves nested free-text leaves while keeping their discrete siblings, such as medication-change flags and goal timeframes. An inpatient echocardiogram mapping requires the explicit test, field, quality and report status; its acquisition time remains separate from receipt time. These mappings do not establish complete clinical understanding or treatment eligibility.

Four new documentary requirements run through ordinary population questions, complete patient membership, source inspection and cache replay:

| Received documentary criterion | Supported | Contradicted | Unresolved |
|---|---:|---:|---:|
| Prescriber reassessment request | 731 | 6 | 877 |
| Renewal clarification deferral | 702 | 44 | 868 |
| Reported transport difficulty | 51 | 163 | 1,400 |
| Community-service delivery report | 28 | 40 | 1,546 |

Each patient set matches an independent public received-source query. All four replay with 1,614 cached patient receipts. Counts describe documentary support, not current care gaps, successful interventions or clinical accuracy. Exact evidence: `source-extension-verification.json` and `source-extension/` under the candidate build.

The source audit checks 12,503 new accounts, 113,837 literal fields and 25,159 typed same-patient links. It retains 31 references whose target was not received. Mixed text/discrete context checks retain 18 medication siblings. Only four immutable quoted specification packages transferred; zero prior patient receipts or human reviews transferred. New-source measure composition retains 449 known denominator patients, 115 known numerator patients and 1,165 unresolved patients under an explicitly descriptive engineering specification. Leaving optional components unbound keeps the result unresolved. All 1,614 review assessments carry current measure provenance; 115 are separate review candidates. These are synthetic specifications, not official payer measures or settlement results.

## Economical execution

All received records have lexical coverage; mapped narratives additionally receive local embeddings. The index contains 2,334,396 passages and 107,251 dense-eligible unique texts. It reused 89,618 embeddings and computed 17,633 new ones. The annotation layer binds 34,621 source spans using 19,057 persistent cache hits, 11,930 within-batch reuses and 3,634 new unique predictions. See `stack/run.json` and `annotations/run.json`. No paid API calls or new trained weights were created by this migration. Local hardware costs are not priced, and cache counts are not accuracy measurements.

A derived stream/patient index supports population source queries while preserving all record content. `population-source-index.json` confirms use of that index for an 81-row received-home-health query; its single observed elapsed time is not a comparative speedup claim. Public-source validation now reads all new categories in one batch rather than rescanning the whole warehouse separately for each category. The stopped unbatched qualification log is preserved.

The planner selects bounded source contracts before detailed planning. Every source name and operator meaning remains visible; detailed fields and larger literal dictionaries move into the selected planning stage. Use of unloaded fields and dropped question requirements is rejected. The existing 20,000-byte request limit stays intact. Actual serialized request construction was verified with simulated provider responses: 18,843 selection bytes and 18,253 planning bytes for the tested 1,500-character question. No provider was called; this is a request-contract check, not evidence of frontier planning quality.

## Qualification and limits

Source extension, full-cohort general queries, source completeness, renal analysis, literal financial evidence, hybrid retrieval, three baseline preparations and native documentary contracts passed. Diagnostic qualification and live activation checks also passed. The diagnostic audit preserves 134 LVEF records: 112 usable literal measurements and 22 invalid numeric values. Ten records carry explicit acquisition dates. The independently checked low-EF review population contains 15 patients; the remaining 1,599 have insufficient evidence for that review criterion. These are neither clinical accuracy estimates nor treatment eligibility. Earlier focused suite totals overlap and must not be added as unique tests: 114 initial source/context; 65 source/planner/activation; 66 measurement/context; 41 final planner/graph; 6 activation; 3 derived-index checks.

The three newly prepared baseline tasks remain unqualified for prediction: timing-eligible recorded labels are 5 positives, 11 zeros and 18 zeros respectively, with only one class in each task. No predictor was fitted. Source absence does not establish a clinical negative. Earlier failed forecasts, frozen Kev/d1 comparisons, reserved cohorts and weak shadow students remain preserved; none automatically gains authority on this source snapshot.

Broader qualified narrative interpretation, useful calibrated prediction, clinical/program breadth, payer/provider attribution and contract settlement, portable customer packaging and full curriculum acceptance remain incomplete. No clinical reviews, treatment decisions, outreach, employer access, external publication or vendor-superiority claims were created.

## Reproduction

Use the existing fresh build; do not overwrite or rebuild it to repeat passing checks. New source versions require deliberate inventory and a new output directory.

- Build: `build_healthcare_warehouse_stack.py --release /Users/mikel/synthetic-patient-ecosystem/releases/healthcare-warehouse-v1.23 --output artifacts/agere-healthcare-v123 --stage all --reuse-model-cache artifacts/agere-healthcare-v118/stack` (completed).
- Extension: `verify_received_source_extension.py --build artifacts/agere-healthcare-v123 --previous artifacts/agere-healthcare-v118 --inventory artifacts/agere-warehouse-v123-inventory` (passed).
- General: `verify_healthcare_warehouse_stack.py --build artifacts/agere-healthcare-v123` (passed).
- Native requirements: `verify_native_requirements.py --build artifacts/agere-healthcare-v123 --out artifacts/agere-healthcare-v123/native-requirements` (passed).
- Diagnostics: `verify_received_diagnostics.py --build artifacts/agere-healthcare-v123`.
- Activate: `activate_healthcare_warehouse.py --build artifacts/agere-healthcare-v123`.
- Serve: `serve_current_warehouse.py --allow-live-stack --port 8885`.
- Live verification: `verify_received_extension_http.py --build artifacts/agere-healthcare-v123`.

Run Python scripts with `.venv/bin/python`. `artifacts/agere-healthcare-v123/implementation-receipt.json` binds the implementation files and qualification/live evidence by SHA-256. Full-stack acceptance remains incomplete.
