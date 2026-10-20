> Spending correction, 2026-10-20: Mike explicitly authorizes the best capable question-answering path, including hosted models. Earlier pending-permission/no-paid-call statements below are superseded. Usage accounting and benchmark restrictions remain.

# Continue Agere

The owner is Codex under Mike's October 19 handoff (BUILD_OWNER.json). Work on
`codex/agere-validated-proof`; preserve concurrent edits. AGERE_BUILD_CONTROL.json
owns the gate and exact next action. Mike requested removal of the continuation
reminder on October 19; the heartbeat is deleted. Continue in human chat turns.

Never run or rescore a benchmark until all selected references are independently
validated. Gold-50 and native 150 remain blocked on reference audits; reuse saved
answers once reconciled. Gold 10 is validated but too small for a general superiority claim.

The live service on :8885 runs from /Users/mikel/agere-mvp-run. Inspect its actual
process and revision before updating; retain a known-good recovery commit.

Current verified service policy: :8885 serves UI source `4bba3b0` from
`/Users/mikel/agere-mvp-run` (PID 49741, session 54553; Python core `28881ce`).
Shared typed composition, chief-of-staff briefing, synthetic actions and manual
monitoring remain connected. The field is now an original TextQL-informed
point-and-thread structure: incoming records form shared meaning and branch into
views, simulated work and monitoring links. Cumulative motion and read-only
recorded-activity hooks remain. Ambient motion is illustrative; this does not
activate model retraining. See AGERE_CHIEF_STAFF_2026-10-20.md and
artifacts/agere-noise-to-action-v1/implementation-receipt.json. Recovery: `28881ce`.
General source discovery, analytical expressiveness, clinical/time reconciliation
and independent qualification remain unfinished. Follow the existing G3/G5
checkpoint and docs/agere/GENERAL_QUESTION_ARCHITECTURE_PLAN.md. No additional
spending approval is pending.

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
