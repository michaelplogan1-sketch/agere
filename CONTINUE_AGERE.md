# Continue Agere

The owner is Claude Code (see BUILD_OWNER.json). Codex works in /Users/mikel/agere-codex on `codex/*` branches; see PARALLEL_WORK.md.
The repo is https://github.com/michaelplogan1-sketch/agere (private). Commit to `main`; other agents use pull requests.

## State (2026-10-12)
- Answer path: /api/stack/compose. The Opus planner writes a graph over cohort (concept/measurement/record atoms), aggregate, explain, sql and the earlier operators. The result carries `answer{status, answer_type, count|patient_ids|value|table, missing, grounding, concepts}`.
- Semantic layer: stack/semantic.sqlite3 holds patient, condition, measurement, encounter, stay, link, concept_patient and record tables. It rebuilds automatically per source digest (~70 s).
- Benchmark (bench/ on codex/answer-benchmark): Agere 38/50, frontier+SQL 49/50 ($21.12), Agere $3.26.
- Blocked: the Anthropic API credit balance is exhausted. 12 questions await a rerun.

## Restart
Kill the process listening on :8885, then run `.venv/bin/python serve_current_warehouse.py --port 8885 --allow-live-stack`.
Startup marks interrupted provider reservations as abandoned so they can be retried.
After editing fingerprinted operator files, recompute operators with clinical_core.agere.clinical.analytic_registry.compute_all (~85 s).

## Next
1. Rerun: `cd ../agere-codex && ../clinical-insights-local/.venv/bin/python bench/run.py --system agere --sql-timeout 180 --ids C05 C09 C12 C18 C19 C20 D04 D09 D10 D24 D25 D26`, then `bench/score.py`.
2. Codex C2: insight-tier benchmark (PARALLEL_WORK.md).
3. Cost: measure a Sonnet 5.5 planner and parameterised plan reuse against the benchmark.
