# Shared planner contracts and measured execution

Delivered October 11, 2026. All six declared development planning questions now execute with exact patient scope, compared with one in the preceding release. Every result remains **partial**. Full evaluated workflow completion remains **0/6**; this is an engineering correction, not clinical qualification or full product acceptance.

## Executable integration

The natural-language question path now loads selected operator parameter contracts from the runtime definitions. Nineteen operators have typed planning schemas, with shared semantic validators for source state, assertions, context, functional/domain review and evidence queries. Other operators retain their existing examples; the entire operator catalog is not fully typed. Input/output contracts distinguish population membership from reports, validate dependencies, and preserve exact runtime program IDs. Readable source streams are kept distinct from streams with cohort predicate fields.

The planner makes one authoritative requirement declaration per question segment. Local compilation retains selector gaps, adds explicit unmet requirements, and verifies that delivering nodes or their dependencies cover the selected operators. It cannot treat an unrelated branch as fulfillment. Runtime validation remains strict, including mapped source-state relations and the one-to-eight evidence-clause limit.

The provider receives selected parameter schemas as planning data within the existing small structured response envelope. Parameters remain a JSON string on the wire and are parsed and validated locally; provider-enforced nested parameter typing is **not** claimed. Direct nested schema variants were rejected by the provider and remain saved as failures. Request size accounting includes all transmitted content. No full patient chart enters the planner.

## Results on unchanged development questions

| Question | Executed path | Work proposal | Remaining limitation |
|---|---|---|---|
| Medication | Patient cohort → assertion review → worklist | HF review; 1 candidate | HF review is a limited program selection, not a validated general medication match; missing-input acquisition unmet |
| Respiratory | Patient cohort → domain review → worklist | Respiratory source review; 0 selected, 0 candidates | No detailed source interpretation; restrictive nomination and unavailable acquisition remain explicit |
| Cardiometabolic | Patient cohort → worklist | Cardiometabolic source review; 1 candidate | Original measurements/reports are not interpreted by this plan |
| Functional | Patient cohort → functional review → worklist | Function/support review; 1 candidate | Missing-input acquisition unmet; clinical suitability unvalidated |
| Appointment | Patient cohort → worklist | Appointment follow-through source review; 1 candidate | Original booking/status reports are not interpreted by this plan |
| Payer | Patient cohort → worklist | Affordability review; 1 assessed, 0 candidates | Limited/unvalidated program choice; enrollment and eligibility interpretation unmet |

These six previously inspected original-training examples are engineering regressions. Expectations are assistant-authored, not clinician-adjudicated. No frozen evaluation was tuned, no model fitted or promoted, and no independent generalization claim is supported. Successful graph execution does not mean a complete or appropriate answer. Some assertions that a capability is unavailable reflect planner selection limitations and need further work.

## Verification and preservation

73 focused checks pass: 60 planner/graph/contract checks and 13 workflow scorecard checks. Actual-catalog transport probes cover six selected-operator combinations with maximum-length 1,500-character questions; the largest selector is 18,205 bytes and the six planning requests span 10,134–14,940 bytes under the existing 20,000-byte bound. A separate actual maximum-question wire exercise also passes. Offline transport tests do not establish model quality.

All six saved final graphs validate under the final runtime with exact patient predicates and unchanged planning questions. `verify_planner_contract_delivery.py` reproduces the scorecard and validates those traces without provider calls. Live API, workspace and browser checks pass at `/stack-evaluation`; partial plans, empty proposals and remaining source failures are visible.

All 170 protected files and 897 prior ledger rows remain exact. Work histories remain 382 items/385 events and 19 capability tasks/49 events; no new tasks, events or human reviews were created. All 360 statement readings, 19 Documents research files and 33 historical source routes remain unchanged. Original-cohort v1.23 remains active: 1,614 patients and 1,238,686 received records. No migration, external action or care delivery occurred.

This increment recorded 35 provider requests: 21 validated and 14 failed. Known estimated usage is $1.024252, including the failed response with returned usage. Thirteen HTTP 400 schema rejections returned no usage, so the total estimate is unknown rather than zero for those requests; no invoice is verified. Ledger totals are 932 rows: 891 validated and 41 failed. Spend remains uncapped. Exact failed requests and four historical pending outbound approvals remain protected against silent retry.

The live scorecard file at `artifacts/agere-workflow-evaluation-v1/scorecard.json` was deliberately replaced with the current report. Its exact preceding contents are archived at `artifacts/agere-planner-contracts-v1/before-scorecard.json`; preceding traces and receipts are retained. The explicit `planning-trace-map.json` selects the corrected cardiometabolic trace without overwriting its failed earlier trace.

## Readiness and next work

The shared planner execution boundary is improved; the stack is not complete. Original source interpretation remains two executed, three errors and one blocked by scope. Three candidate-category coverage gaps remain. Two prior answer-linked internal handoffs retain manually selected service tags; the new planning proposals are not connected to those original answers.

Next correct the measured source-reference ambiguity and reconciliation overflow for new contexts while retaining historical routes and receipts. Then fix candidate coverage and connect original answers to service work with preserved lineage and appropriate service selection. Keep planner selection/program suitability as measured gaps. The ten full-product acceptance areas, research/model qualification and clinical validation remain incomplete.

Evidence: `artifacts/agere-planner-contracts-v1/implementation-receipt.json`, `scorecard.json`, `reproducibility.json`, `planning-trace-map.json`, `acceptance-checks.log`, `scorecard-checks-final.log`, `local-contract-transport.json`, `final-wire-check/verification.json`, `http-verification.json`, `browser-verification.json`, `preservation.json` and `usage-accounting.json`.
