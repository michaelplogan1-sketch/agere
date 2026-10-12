# Documentary evidence in protocol decisions and version impact

Delivered October 9, 2026 (receipts timestamped October 10 UTC), on the unchanged original-cohort v1.18 synthetic warehouse. This continues `PROTOCOL_PROGRAMS_BUILD.md`.

The new typed `requirement` binding consumes a registered documentary result directly in protocol trigger/readiness logic. It pins the requirement ID, version and definition hash, and retains its exact same-patient receipt and hash. Supported, contradicted, unobserved, undated and conflicting evidence retain distinct meanings. This is a reusable connection for the existing requirement catalog, not another condition-specific executor.

Affordability protocol version 2 uses `native_affordability_report` over received financial-screen accounts. Version 1 retains the exact original definition hash in `care_protocol_history.json`, addressable as `affordability_action_review@1`, with its original all-unresolved result preserved. Unqualified IDs resolve to the active definition; stored program/selection provenance now uses explicit versions. Definition history is a local static registry, not a general payer-document review/approval system.

The implemented path is:

`received source accounts → pinned documentary requirement → three-valued protocol criterion/readiness → immutable program assessment → existing review-work lifecycle`

Cached native interpretations are checked against newly projected received source accounts before a new protocol result uses them. Added, removed or changed relevant sources cannot silently reuse an outdated materialized account analysis. Source and review changes invalidate the affected patient; other patient receipts remain reusable. Existing protocol supporting context stays separate from these explicitly declared decision inputs.

The workbench exposes the requirement rule and a source-account inspection control. The existing program workspace dynamically lists version 2, while explicit archived versions remain callable. No work items, reviews, outreach or care events were fabricated in the live workspace.

## Full original-cohort evidence

| Affordability review definition | Criterion supported | Criterion not supported | Insufficient evidence |
|---|---:|---:|---:|
| Version 1: legacy documentary binding | 0 | 0 | 1,614 |
| Version 2: received-screen requirement | 115 | 334 | 1,165 |

Source: `artifacts/agere-protocol-requirements-v1/verification.json`, `protocol-summary.json`, `legacy-version-1.json` and `http-verification.json`. All values describe synthetic received documentation. A negative screen does not exclude financial need; a missing screen does not establish absent care or hardship. The 115 positive documentary review candidates are not verified assistance-program beneficiaries.

An independent query of public received `source_records` agrees on every patient's supported/contradicted/unresolved state. The audit checked 1,614 exact requirement receipts and 1,347 source-field references. The supported-selection question produces 115 linked program assessments; the full program question assesses all 1,614 patients with the same 115 candidates. Existing financial-account and assistance eligibility prerequisites remain separate.

## Version-change capability

The new `protocol_change` graph operation compares explicit versions of the same protocol over the complete selected population and one source snapshot. It persists every patient transition and both original protocol receipt hashes; inspection checks freshness per patient. It records changed definition fields, criterion/readiness changes and patients entering/leaving source review. The operator does not activate rules, change work items, or compare clinical outcomes.

The local question **Compare affordability protocol versions** and the API `/api/stack/protocol-change` expose the comparison. Version 1 → 2 resolves 449 formerly unknown documentary criterion/readiness results: 115 positive and 334 negative. The remaining 1,165 stay unresolved; 115 enter source review and none exit. These are changes in interpretation coverage, not proven clinical accuracy or better care. Every one of the 1,614 transitions and 3,228 before/after receipts was independently checked against persisted source results (`version-impact-audit.json`).

## Verification and economics

- 128 focused checks passed for the first connection, followed by 50 focused checks after version-impact integration. Suites overlap; these are not unique aggregate case counts. They cover conflict/unknown logic, version pins, same-patient evidence, added/deleted/tampered raw records, stale work blocking, immutable comparison receipts, archived versions and graph behavior.
- Real registered questions, full-cohort program proposals, API source inspection, current/archived catalog exposure and exact graph replay pass. No live program work was materialized.
- Offline provider-wire simulation with the actual expanded catalog: 19,049-byte selection request and 16,179-byte selected planning request, within the existing 20,000-byte bound. No provider was called and semantic planner quality was not measured by this simulation.
- Browser functional verification is recorded separately. Pixel screenshot quality is not claimed.
- Zero new paid API calls, foundation/student weights, human reference labels, source migrations, or changes to frozen Kev/d1 experiments. Current predictor qualification remains unsuccessful; no new predictive score is introduced.

## Remaining build

The next substantive capability is broader versioned program/measure document intake and source-to-rule review, using this explicit version-impact machinery before changing active definitions. `program_registry.py` already has quoted research-candidate intake, but its legacy rule input bindings are narrow and separate from these typed requirements. Reconcile those layers rather than adding another competing rule engine. Preserve exact received document versions/effective periods, quoted evidence, unresolved semantic bindings, denominator/attribution requirements and honest review authority. Neither source quotation nor a clinical-content approval establishes complete payer-contract equivalence.

Broader qualified interpretation, usable outcome-safe prediction, contract/attribution semantics, general planner catalog paging and full design-to-code acceptance remain unfinished. This report does not mark the entire stack complete.

## Reproduce

- `.venv/bin/python build_protocol_requirement_stack.py --output <fresh-directory>`
- `.venv/bin/python verify_protocol_requirement_http.py --output <that-directory>` (owned local server on port 8885)
- `.venv/bin/python verify_protocol_version_impact.py --output <that-directory>`
- `.venv/bin/python -m unittest test_protocol_requirements test_protocol_change test_care_protocols test_protocol_programs test_protocol_source_context test_analysis_graph test_planner_scope -q`

Evidence and implementation SHA-256 manifest: `artifacts/agere-protocol-requirements-v1/`.
