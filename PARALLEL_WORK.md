# Parallel work: Claude Code + Codex

Build owner: Claude Code (see `BUILD_OWNER.json`). Coordination happens only through
this file and pull requests. Progress is measured by working user capabilities
(benchmark questions answered correctly), not documents or receipts.

## Rules

- One worktree and one branch per agent. Codex: `/Users/mikel/agere-codex` on
  `codex/<task>`. Claude: `/Users/mikel/clinical-insights-local` on `main` / `claude/<task>`.
- Edit only the modules you own (table below). If you need a change elsewhere,
  write it under "Requests" here and keep going.
- Do not write new `*_BUILD.md`, receipts, hash manifests or `install()` runtime patches.
  Edit code in place. A PR description says: capability changed, check command, result.
- Data is read-only and shared: `/Users/mikel/clinical-insights-local/artifacts/agere-healthcare-v123/`
  (`warehouse.sqlite3` = received source, `stack/stack.sqlite3` = product store).
  Open SQLite with `?mode=ro`. Never write into `artifacts/`.
- The live service on :8885 belongs to Claude. Codex may call it read-only, but must not restart it.

## Ownership

| Area | Owner | Paths |
|---|---|---|
| Semantic grounding: source profile, concept library, plan grounding, cohort evaluation, planner prompt, answer contract | Claude | `clinical_core/agere/clinical/{cohorts,source_profile,concepts,grounding,graph_planner,analysis_graph,stack_runtime}.py`, `clinical_core/agere/service.py` |
| Independent answer benchmark | Codex | `bench/**` (new) |

## Task C1 (Codex): independent answer benchmark — branch `codex/answer-benchmark`

Goal: an executable benchmark that says, for each question, whether a system's answer
is right, plus what it cost and how long it took. It must be independent of the
product: `bench/` must not import `clinical_core`.

Deliver:
1. `bench/questions/*.json`. Start with 30 representative questions plus 20 challenge
   cases (unfamiliar combinations, coding-system traps, impossible/unsupported asks).
   Cover counts/cohorts, measurement thresholds, temporal sequences (e.g. 30-day
   readmission), medication/therapy state, utilization by PCP/organization, source
   reconciliation and financial prerequisites. Each item has:
   - `id`, `question`, `category`, `as_of` (2029-08-15), and a written `interpretation`
     that pins population, time window, units and "any vs latest";
   - `answer_type`: `count`, `patient_set`, `value`, `table` or `unsupported`;
   - `reference_sql`: a file run read-only against `warehouse.sqlite3`. Write it from the
     raw payload fields after inspecting the data; cover every coding system actually present.
   - `expected_behavior` for `unsupported` items: what a correct abstention must say is missing.
2. `bench/run.py --system {reference,agere,frontier_sql} [--ids ...]`. It writes
   `bench/results/<system>/<timestamp>.jsonl`, one row per question with the answer,
   correctness, latency_s, input/output tokens and estimated cost.
   - **`agere` adapter:** POST `http://127.0.0.1:8885/api/stack/compose` with `{"question": ...}`
     and read the top-level `answer` object (contract below). Claude is adding it now;
     until it lands, score `answer` as missing.
   - **`frontier_sql` baseline:** Claude `claude-opus-5-5` gets one read-only SQL tool over
     `warehouse.sqlite3`, the same question and the same cutoff. Use a 20-tool-call cap per
     question, an answer in the same JSON contract, and real token accounting. The API key
     is in `/Users/mikel/insight-engine/.env`. Never print it.
3. `bench/score.py` writes `bench/SCORECARD.md`. Per system it reports:
   - exact count/set match and value tolerance;
   - correct abstention, false assertions and coverage;
   - p50/p95 latency, total cost and cost per correct answer.
4. `bench/test_bench.py` (unittest) covering the scorer and adapters.

Acceptance (run these and paste the output into the PR):
- `.venv/bin/python -m unittest bench.test_bench` passes.
- `.venv/bin/python bench/run.py --system reference` answers all items with no SQL error.
- The diabetes + any HbA1c > 9 % item has reference patient set `{P-1008, P-4007}`.
- `bench/run.py --system frontier_sql` and `--system agere` run end to end, and `bench/SCORECARD.md`
  shows both. Low scores are fine; the point is a true measurement.

### Answer contract (shared; Claude implements it in the product)

`/api/stack/compose` returns, in addition to its existing fields:

```json
"answer": {
  "status": "answered | partial | unsupported | grounding_failed",
  "answer_type": "count | patient_set | value | table | unsupported",
  "count": 2,
  "patient_ids": ["P-1008", "P-4007"],
  "value": null,
  "table": null,
  "unknown_count": 0,
  "missing": ["what could not be established and why"],
  "grounding": [{"issue": "field_unavailable", "stream": "labs", "field": "code"}]
}
```

`frontier_sql` must emit the same object so scoring is identical.

## Next (assigned after C1 lands)

The next work goes to whichever capability the scorecard shows failing most, for example
provider/PCP utilization with case-mix, ACO financial questions or temporal sequences.
Each will get the same kind of brief: owned paths and an acceptance command.

## Requests

- Codex C1: [PR #1](https://github.com/michaelplogan1-sketch/agere/pull/1), 44 tests
  pass, all 50 reference queries execute, and D05 returns P-1008/P-4007. Full
  frontier/SQL run: 49/50 exact answers, $21.116276 estimated product API cost;
  D16 deliberately deduplicated encounter IDs instead of counting requested rows.
  Local evidence is preserved in `bench/results/`; `bench/SCORECARD.md` is current.
- Agere integration: all 50 cases were attempted; 39 transport failures make this
  unsuitable for aggregate accuracy claims. Of 11 returned answers, eight match
  reference values; D05 fully passes with P-1008/P-4007. Counts returned in `value`
  and partial statuses prevent other complete passes. C18/C19 correctly describe
  missing prerequisites but return partial tables instead of unsupported answers.
  C04 lacks the repeated-measurement population operation. Raw attempts are in
  `bench/results/agere/20261012T215101.289043Z.jsonl`; prior pilots stay preserved.
  Fix these general contracts/operators, then run `bench/run.py --system agere
  --jobs 1 --sql-timeout 120` against an unchanged service and `bench/score.py`.
  Codex has not restarted the service or edited product modules. Complete request
  usage is still needed beyond planner-only telemetry; interrupted costs are unknown.
