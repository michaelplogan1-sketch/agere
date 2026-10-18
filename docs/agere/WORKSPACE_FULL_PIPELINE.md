# Workspaces on the full answer pipeline

Status 2026-10-17. Code: `clinical_core/agere/onboarding/workspace_stack.py`, `Workspace.ask(pipeline=...)` and the
`pipeline` build step in `onboarding/workspace.py`. Tests: `test_workspace_stack.py`. Offline acceptance runner:
`bench/workspace_acceptance.py`.

## What changed

Before this change, `Workspace.ask` answered only through the hybrid core (`hybrid/live.answer`). A native workspace now
answers the way the demo build does: `together.compose_together` over a product `Stack`. That covers the local
compiler, governed metrics, the analytic engines through `analysis_graph.execute`, and the hybrid core in the analyst
slot. The two paths are reconciled by together's rules.

- `workspace_stack.support(ws)` returns `(ok, reason)`. The full pipeline needs `stack/stack.sqlite3` and
  `stack/run.json` (the canonical record store), plus `semantic.sqlite3` at `semantic_layer.VERSION` with the same
  `source_digest`.
- `workspace_stack.prepare(ws)` builds `<ws>/pipeline/`. It is idempotent and rebuilds when a source file's size or
  mtime changes. On the pilot it takes about 3 s.
- `workspace_stack.stack_for(ws, allow_live=..., send_factory=...)` returns a `WorkspaceStack`, a `Stack` subclass.
  One stack is cached per folder build and liveness.
- `Workspace.ask(question, pipeline=None|'full'|'hybrid', live=True)`:
  - `None` picks `full` when the workspace supports it, else `hybrid`.
  - If an automatic `full` run raises, the question is answered by the hybrid core and `out['pipeline_fallback']`
    gives the reason.
  - An explicit `full` on an unsupported workspace raises `ValueError` with the reason.
  - `live=False` means no hosted call at all.
- The answer comes back in the workspace envelope: `answer {count|value|patient_ids|table[{keys,value}]}`,
  `decision`, `interpretation`, `cost_usd`, `answer_type`. This is the shape the workspace UI and `ask_many` already
  read. The envelope also carries `pipeline`, `route`, `operations`, `status`, `headline`, `missing`,
  `workspace_decline` and `result` (the trimmed compose result).
- The `pipeline` step runs after `docs` in `run()` and `rebuild()`, for native workspaces only. It records its outcome
  and never fails the build.

## What the planner, engines and governed metrics actually read

| artifact | read by | workspace source |
|---|---|---|
| `run.json` (`snapshot.as_of`, `source_digest`, `source_run`) | planner (`retriever.meta`), every engine (as_of), `semantic_layer.ensure`, `source_profile.load`, result-cache keys | written from `stack/run.json`. The native run has no `source_run`, so the delivery digest is used. |
| `stack.sqlite3` `patients`, `records` | cohort selection, graph universe, explain/aggregate/source_changes, compiler profile | APFS clone of `stack/stack.sqlite3`. `result_cache`, `analyses` and `proposals` are added, plus WAL and the stream index (`warehouse_concurrency.configure`). |
| `semantic.sqlite3` (semantic-layer/18) | nearly every engine through `semantic_layer.ensure`, governed metrics, `sql` nodes, the hybrid core inside together | APFS clone of `<ws>/semantic.sqlite3`, the workspace layer with `raw_*` tables. |
| `stream-records.sqlite3` | `record_store` (speed only; falls back to `records`) | APFS clone |
| `source-profile.json` | local compiler, grounding, metric/SQL lint | copied, then validated (rebuilt from `stack.sqlite3` if stale) |
| `annotation-link.json` + `evidence/` | `analysis_graph.execute` opens it for **every** graph (`evidence_connection`, vocabulary in `catalog`) | **empty** evidence index over the workspace's patients. `evidence` predicates decline as "unmapped", never as negative. |
| `question-plans.sqlite3`, `provider-receipts/`, `model-cache.sqlite3`, `briefing.json`, `program-documents.sqlite3` | planner cache, receipts, encoder cache, engine caches | created in the folder on first use |
| retriever `encoder` (MiniLM) | engine-lean router, semantic cache, schema rerank | local `models/minilm-l6-v2` (free). Passage search is not built. |
| retrieval index (`vectors.npy`, passages, FTS) | only `retrieve` nodes | **absent**. It would need an embedding pass over every record. `retrieve` nodes decline. |
| `forecast-link`, `longitudinal-link`, `clinical-state-link`, `medication-terminology-link`, `cohort-values.json` | `forecast`, `sequence`/`longitudinal`, `state_review`, `medication_identity`, frontier hints | absent. These nodes decline with the missing link named. The frontier planner runs without observed-value hints. |

Clones use `clonefile(2)`: copy-on-write, no extra space, and the workspace's own files are never opened for writing.
Where cloning is unavailable the files are copied. That costs disk on Linux hosts, where the pipeline folder could
instead be written directly as `<ws>/stack`.

**Engine failures.** `WorkspaceStack.run_graph` catches any engine exception. It re-executes an unsupported graph that
names the missing input (for example "Missing input for briefing: No received records match concept emergency_visit"),
so `answer.status` is `unsupported` and `workspace_decline` gives the operation and the reason. A data-dependent crash
is labelled "Not computable for <op> on this workspace's data (…)".

**Live mode.** The stack meters the hybrid core through together's ledgered sender: the same ledger
(`workspace.LEDGER`), with receipts in `pipeline/provider-receipts`. A caller-supplied `send_factory` replaces the
sender through a shallow view of the cached stack. With `live=False`, the planner stays on its local rungs and the
hybrid core's sender refuses every call.

## Per engine: native workspace vs warehouse workspace

Warehouse workspaces (`onboarding/apply.py`) have no record store and a semantic layer without the product version.
`analysis_graph.execute` needs `stack.sqlite3` patients and records for every graph. `semantic_layer.ensure` would also
try to rebuild their layer from a record store that does not exist. So **every engine is unsupported on a warehouse
workspace**, and those workspaces answer through the hybrid core. `knowledge` reads only its own cited KB, but still
runs inside a graph. Making a warehouse workspace eligible would mean emitting a canonical `stack.sqlite3` from the
mapped entities. That is not part of this change.

On the native pilot (`pilot-native-v2-12`), "runs" means the engine executed on the workspace data offline. "Limited
by data" means the engine ran and correctly reported that the delivery lacks the input.

| engine | native workspace | why / what limits it |
|---|---|---|
| survival | runs (3/3 routed answered) | semantic layer stays/visits/conditions |
| explain | runs (direct graph: diabetes vs A1c>9 answered) | records + concept bindings. ED outcome declines (`emergency_visit` unbound, see gap 1). 0/21 phrasings route locally, and the demo also routes 0/21 locally (router rung). |
| uplift | limited by data | no program enrollment / treatment streams in the native delivery (treated 0) |
| queueing | runs (4/10), 6 limited | bed/clinic arrivals work. ED arrivals are not found (gap 1). |
| program_effect | runs (3/3 routed), 15 routed elsewhere | the compiler declines program-effect phrasings: the delivery has no care-management / TCM / community enrollment streams. On the demo 17 route. |
| demand | runs (6/13), 7 limited | ED / inpatient / observation / urgent-care series have no dated records (gap 1) |
| monitor | runs (9/9) | — |
| vbc | runs (24/27) | 2 limited by ED/IP records (gap 1), 1 ambiguous measure |
| risk_adjustment | runs (8/8) | package payment parameters + semantic layer |
| trajectory, profile, pathway, scheduling, calibration, network, market, contract_strategy, target_trial, line_of_therapy, trial_feasibility | run (all routed phrasings answered or partial) | — |
| claims_review | runs (6/11), 5 decline | `underpayment_review` divides by `billed`. Native payer claims include `billed = 0` (gap 2). |
| loop_closure | runs (8/13), 5 limited | no result/order/referral loops in scope for the window |
| diagnostic_delay | runs (8/10), 2 limited | no ED treat-and-release visits (gap 1) |
| risk_outcomes | runs (5/9), 4 limited | no index cases for the requested unit (ED/IP based, gap 1) |
| early_signal | limited by data (0/13) | too few indexable acute events (gap 1) |
| briefing | declines (0/8) | its fixed ED/admission topics need `emergency_visit` (gap 1) |
| discover | runs (3/4), 1 declines | ED outcome (gap 1) |
| knowledge | runs (9/14), 5 limited | KB has no entry for those questions (same as demo) |
| retrieve / evidence / forecast / sequence / longitudinal / state_review / medication_identity | decline | no passage index, narrative annotations, or fitted/longitudinal/state/terminology links in a workspace build |

## Offline acceptance on pilot-native-v2-12 (2026-10-17)

The run used every `bench/acceptance/*.json` phrasing (493, counted by `expect_operation`) through
`together.compose_together` on `stack_for(ws, allow_live=False)`. Local rungs only: compiler and exact plans, with
`AGERE_LOCAL_MODEL`/`BOTTLED`/`SEMANTIC_CACHE` off. It made 0 hosted calls and every question finished in at most
2.0 s. Raw rows are in `artifacts/workspaces/pilot-native-v2-12/pipeline/acceptance-offline-2026-10-17.json`.

| engine | phrasings | routed locally | answered/partial | unsupported (engine) | unsupported (workspace decline) | error | not routed locally | routed to another op |
|---|---|---|---|---|---|---|---|---|
| briefing | 10 | 8 | 0 | 0 | 8 | 0 | 2 | 0 |
| calibration | 9 | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| claims_review | 17 | 11 | 6 | 0 | 5 | 0 | 6 | 0 |
| contract_strategy | 8 | 8 | 8 | 0 | 0 | 0 | 0 | 0 |
| demand | 30 | 13 | 6 | 7 | 0 | 0 | 17 | 0 |
| diagnostic_delay | 10 | 10 | 8 | 2 | 0 | 0 | 0 | 0 |
| discover | 17 | 4 | 3 | 0 | 1 | 0 | 12 | 1 |
| early_signal | 15 | 13 | 0 | 13 | 0 | 0 | 2 | 0 |
| explain | 21 | 0 | 0 | 0 | 0 | 0 | 21 | 0 |
| knowledge | 36 | 14 | 9 | 5 | 0 | 0 | 22 | 0 |
| line_of_therapy | 10 | 10 | 9 | 1 | 0 | 0 | 0 | 0 |
| loop_closure | 15 | 13 | 8 | 5 | 0 | 0 | 2 | 0 |
| market | 13 | 8 | 8 | 0 | 0 | 0 | 5 | 0 |
| monitor | 26 | 9 | 9 | 0 | 0 | 0 | 15 | 2 |
| network | 8 | 8 | 8 | 0 | 0 | 0 | 0 | 0 |
| outreach | 23 | 6 | 6 | 0 | 0 | 0 | 17 | 0 |
| pathway | 18 | 10 | 10 | 0 | 0 | 0 | 7 | 1 |
| profile | 23 | 12 | 12 | 0 | 0 | 0 | 11 | 0 |
| program_effect | 26 | 3 | 3 | 0 | 0 | 0 | 8 | 15 |
| queueing | 10 | 10 | 4 | 6 | 0 | 0 | 0 | 0 |
| risk_adjustment | 8 | 8 | 8 | 0 | 0 | 0 | 0 | 0 |
| risk_outcomes | 10 | 9 | 5 | 4 | 0 | 0 | 1 | 0 |
| scheduling | 10 | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| survival | 17 | 3 | 3 | 0 | 0 | 0 | 14 | 0 |
| target_trial | 11 | 10 | 10 | 0 | 0 | 0 | 1 | 0 |
| trajectory | 11 | 2 | 2 | 0 | 0 | 0 | 9 | 0 |
| trial_feasibility | 9 | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| uplift | 10 | 10 | 0 | 10 | 0 | 0 | 0 | 0 |
| vbc | 62 | 27 | 24 | 3 | 0 | 0 | 24 | 11 |
| **total** | 493 | 267 | 197 | 56 | 14 | 0 | 196 | 30 |

How to read the columns:

- **answered/partial:** 157 answered and 40 partial. 5 of the partials are together's `unchecked_zero`, and some
  `network` partials are empty results such as "no attributed PCPs".
- **not routed locally:** the local compiler declined the phrasing. With live models on, these go to the metric router,
  operator router, hosted SQL and the frontier.
  - Local routing of the same 493 phrasings on the demo build's profile is close: 279 phrasings route to the expected
    operation on the demo vs 267 on the pilot. The rows are in `pipeline/local-routing-vs-demo-2026-10-17.json`.
  - The real differences are data-driven. On program_effect (17 demo vs 3 pilot) the compiler correctly finds no
    enrollment streams. claims_review, loop_closure, risk_outcomes and monitor each differ by 1–2.
- **error:** a crash. There were none. Every failure is a routed unsupported answer or a local decline.

## Gaps found (follow-ups, not fixed here)

1. **ED / inpatient events invisible to concept bindings on native deliveries.**
   - Native staging emits `adt_messages` with HL7 codes (`patient_class` E/I/O, `trigger_event` A01/A04/A03). The
     `emergency_visit`, `inpatient_admission` and `observation_stay` bindings match only an `adt` stream with decoded
     `event`/`patient_class`, or ED/IP encounters. Native encounters are all outpatient (POS 11/02).
   - Effect: briefing, ED/IP demand series, ED queueing arrivals, early_signal, diagnostic_delay ED cases, risk_outcomes
     index cases and explain/discover ED outcomes all come up empty.
   - The semantic layer itself has these events (`utilization_event`, 6,794 rows), so SQL/metric answers are fine.
   - Fix: in `ingest/canonical.py`, stage ADT as the `adt` stream with decoded fields, or add `adt_messages` bindings in
     `concepts.py`.
2. **claims_review `underpayment_review` divides by `billed`.** Native payer claims include `billed = 0`, so the engine
   declines ("Not computable … ZeroDivisionError"). Fix: skip claims with `billed <= 0` in the ratio-of-billed model.
3. **No narrative evidence annotations or retrieval index in workspace builds.** `evidence`/`retrieve`-based plans
   decline. Building them means an annotation/embedding pass, which is not cheap.
4. **Warehouse workspaces:** hybrid only (see above).

## Small fixes made while wiring this (main service behaviour unchanged on the demo build)

- `local_compiler._stream_vocabulary` added a phantom `adt` stream even when the profile has none. That made a
  `KeyError: 'adt'` in `_literal_stream_question` for many questions on any delivery without an `adt` stream. The
  phantom is now added only when the stream exists. The demo has `adt`, so it is unchanged.
- `monitor.peer_outliers` recomputed `np.mean(logs)` once per claim, which is quadratic. On the pilot (one organization
  with tens of thousands of claims) claims_review provider review effectively hung. The mean is now computed once per
  organization. Results are identical, and the run takes 1 s instead of more than 5 min.
- `together._run_hybrid` / `_run_analyst` use `stack.send_factory` and `stack.docs_dir` when the stack has them. The
  service `Stack` has neither, so its behaviour is unchanged.
