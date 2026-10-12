# Continue Agere

The owner is Claude Code (BUILD_OWNER.json). Codex is paused (PARALLEL_WORK.md). Checkpoint: AGERE_BUILD_CONTROL.json.
The repo is https://github.com/michaelplogan1-sketch/agere (private), branch `main`.

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

## Measured
Development benchmark (bench/, 50 questions): 49/50 correct, $0, no API calls, p50 0.65 s.
Frontier+SQL baseline: 49/50, $21.12. A held-out set (bench/questions/heldout.json) is written independently of
the compiler.

## Restart
Kill the process listening on :8885, then run `.venv/bin/python serve_current_warehouse.py --port 8885`.
The semantic layer rebuilds automatically per source digest. After editing fingerprinted operator files, run
analytic_registry.compute_all.

## Next
Held-out results and fixes; survival / time-to-event; lab-trajectory change points (signal_math);
value-of-information next-evidence; observation-aware deterioration; a UI on the MVP.
