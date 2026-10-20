> Current task-scoped status, October 20, 2026: Codex chat01a159cf-8e56-7bb2-be7e-4889268a99c2 integrated admission fix301b65b and activated narrow release5092910 on:8885 (PID73727/session71513,rollback176aaba). Existing overlapping changes are preserved exactly; Claude remains stopped.120 affected release checks and six live grammar checks pass. The exact full HFrEF request FAILED the fresh local-only API test after20.186seconds with no paid call or new workspace; it is NOT READY. See docs/agere/REQUEST_ADMISSION_RELEASE_2026-10-20.md and AGERE_BUILD_CONTROL.json. Keep follow-up in Codex; older ownership/runtime statements below are historical for this bounded window.

> Spending correction, 2026-10-20: Mike explicitly authorizes the best capable question-answering path, including hosted models. Earlier pending-permission/no-paid-call statements below are superseded. Usage accounting and benchmark restrictions remain.

# Continue Agere

The owner is actual Claude Code session `local_1c579565-09b5-4e07-a223-1ae6016e7664`,
following Mike's explicit continuation request and Claude's message31 acknowledgment.
BUILD_OWNER.json and AGERE_BUILD_CONTROL.json own authority and next action.
Work on `codex/agere-validated-proof`; preserve other edits. No continuation reminder.

Live :8885 is source `176aabab590a938e954fd2e9a510546ee6c474dd` in
`/Users/mikel/agere-mvp-run`, PID65732/session96609, ready/synthetic and hosted-enabled.
Rollback is `270c3b4` (previous code780816d). Preserve active config overlay and histories.
See `artifacts/agere-discovery-v1/implementation-receipt.json` for sealed tests,
actual provider discovery/directSQL, independent six-query live grammar and browser
acceptance, action guards, API accounting and known limits.

Next: generic bounded derived-field value discovery in request_discovery.py and
new test_request_discovery_derived.py across three domains. The prior planner lane
made no edits and released that claim. Then shared multi-entity/temporal composition
under docs/agere/GENERAL_QUESTION_ARCHITECTURE_PLAN.md. Existing Codex reviewer
01a15936-8db1-74f1-886b-8e1ffb89030f independently checks Claude's frozen candidates;
Claude cannot independently qualify its own implementation.

All old saved contexts remain inspectable but need re-asking before new work under
the current policy. Counts/summaries are provenance; explicit current patient
selections permit synthetic review. Monitoring is manual; no autonomous retraining.
G3 remains active, G5 blocked, full acceptance false. Preserve the complete charter.
No protected benchmark rerun/rescore before independent reference validation.
During native UI navigation, unrelated evaluator progress was incidentally exposed
to the outgoing build chat after code freeze. No evaluator files were opened and
no implementation was based on it; future qualification must use an uncontaminated
reviewer and fresh held-out material. See evaluation-hygiene.json in this receipt folder.

Claude continuation handoff:
`/Users/mikel/Documents/Codex/2026-10-20/agere-coordination/CLAUDE_CONTINUATION_HANDOFF.md`.
The new product-language preference in AGERE_PRODUCT_DIRECTION.md is a pre-existing
uncommitted edit; preserve it and use purpose-specific output language.

The remaining notes are historical architecture context, not current run instructions.

## Principle
Right tool for the job. Agere plans questions itself (clinical_core/agere/clinical/local_compiler.py), executes them
with its own operators, and calls a hosted LLM only when the compiler cannot account for part of a question. The
answer records why. Run the service without `--allow-live-stack` unless hosted fallback is wanted.

## Answer path
compose -> local compiler (concepts, data dictionary, grammar) -> graph operations:
- cohort (concept/measurement/encounter/record atoms)
- aggregate (rates, shares, O/E adjustment, readmissions)
- explain (factor statistics or case review)
- discover (segment concentrations, BH-controlled, documentation guard)
- vbc (eligibility, attribution, utilization/1000 with shrinkage, completion-adjusted PMPM, risk score, quality,
  shared savings on supplied terms, metric-change decomposition with feed and event-type gap checks)
- sql (governed read-only SQL over the semantic layer stack/semantic.sqlite3)

## Historical comparison withdrawn from current claims

The visible development set and historical cached zero-dollar comparison do not support a general accuracy or cost advantage. No saved evidence currently establishes the claimed 49/50 result under a fair independent comparison. Preserve historical artifacts; do not publish those numbers as validated performance. References must be independently validated before any benchmark rerun or rescore.

## Restart
Kill the process listening on :8885, then run `.venv/bin/python serve_current_warehouse.py --port 8885`.
The semantic layer rebuilds automatically per source digest. After editing fingerprinted operator files, run
analytic_registry.compute_all.

## Next
Held-out results and fixes; survival / time-to-event; lab-trajectory change points (signal_math);
value-of-information next-evidence; observation-aware deterioration; a UI on the MVP.
