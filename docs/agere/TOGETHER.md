# Agere "together": fast path + semantic analyst

On heldout10, Agere's fast path and an LLM over Agere's own semantic layer (the bench's `frontier_semantic`) were right
on different questions. When the two agree, the answer is very likely right, and one reconcile turn settles most
disagreements (`bench/reconcile.py`). "Together" makes that combination the product's answer path, so the full stack can
be entered in the benchmark as one contestant (`agere` adapter → `/api/stack/compose`).

## Where it runs

`service.py` → `conversation.handle_compose` → `Stack.compose` (`clinical_core/agere/clinical/stack_runtime.py`).

- `Stack.compose` dispatches to `together.compose_together` when `AGERE_TOGETHER` is on (the default), the server
  allows live models (`--allow-live-stack`) and planning is `auto`.
- `Stack._compose` is the fast path, with the same body as before. `AGERE_TOGETHER=off`, a server without live
  models, or any other planning policy runs `_compose` only and returns exactly what it returned before.

## Flow

1. **Question start.** The semantic analyst starts in a worker thread (`together._pool`, 8 workers). It reads
   `<build>/stack/semantic.sqlite3` and gets the documented semantic model as a cached system block: tables and column
   docs, concept code sets, adopted definitions, governed metric and measure definitions, and verified example
   queries. It has one bounded read-only `query_sql` tool (8 calls max). Model: `AGERE_SEMANTIC_MODEL`, default
   `claude-haiku-5-5` (the bake-off winner). If the build has no semantic layer, nothing starts.
2. **Fast path.** `Stack._compose` runs on the request thread with no changes.
3. **Wait.** The request waits for the analyst until `AGERE_TOGETHER_BUDGET_S` (default 90 s) after the question
   started. While it waits and reconciles, it releases the service's global stack lock, then re-acquires it before
   `handle_compose` does its logging and learning writes.
4. **Decision.**

   | Situation | Shipped answer | Confidence |
   |---|---|---|
   | Analyst timed out, failed, or no semantic layer | fast answer, unchanged | unchanged |
   | Fast path raised, analyst answered | analyst answer | `unverified` |
   | Fast path raised, no analyst answer | the fast path's error propagates | n/a |
   | Analytic engine answer (`survival`, `risk_adjustment`, `target_trial`, `forecast`, `program_effect`, `trajectory` anywhere in the graph) | fast answer, never replaced | lowered to `low` only if the analyst says unsupported or its answer differs |
   | Both unsupported | the abstention; the analyst's missing items are merged in | unchanged |
   | Same canonical answer (`semantic_analyst.canonical`) | fast answer | `verified`, `confidence_detail.evidence = 'two_independent_paths_agree'` |
   | Different answers | one reconcile turn (see below) | see below |

5. **Reconcile** (`semantic_analyst.reconcile`, the logic of `bench/reconcile.py`). Model: `AGERE_RECONCILE_MODEL`,
   default `claude-opus-5-5`. Bounded by `AGERE_RECONCILE_BUDGET_S` (default 60 s).
   - The reconciler sees both answers in blind order, each with its SQL and grounding.
   - It may run up to 4 SQL checks.
   - The question's own stated definitions win over governed defaults.
   - Verdicts:
     - Fast answer chosen: the fast answer ships, `unverified` (it stays `low` if it was already low).
     - Analyst answer chosen: the analyst's answer ships, `unverified`. The fast reading is added to grounding.
     - `neither`: the reconciler's corrected answer ships, `unverified`.
     - Abstain, timeout, error or an invalid verdict: the fast answer ships as `partial` with confidence `low`. Both
       readings are added to `grounding` (`{'source': 'together', 'reading': ..., 'answer': ..., 'sql': ...}`).

## Receipts and cost

- `result['together']` holds `decision`, `winner` (`fast | analyst | reconciler | both | none`), `reason` and
  `analyst_state`. It also has `analyst` and `reconcile` parts (model, cost, new paid calls, cache hits, SQL calls,
  seconds, error, last SQL), the two budgets, `analyst_wait_s` and `elapsed_s`.
- The analyst and reconcile calls go through the ledgered `context_student.send`, called with `max_bytes` (the docs
  are about 141K characters) and a 180 s timeout. Each worker opens its own `BudgetLedger` connection. As a result:
  - cost lands in the provider budget ledger;
  - an identical request replays from the ledger cache at no cost;
  - failures keep their reservation.
- The calls are appended to `planner_receipt.steps` (stages `together_semantic_analyst` and `together_reconcile`).
  Both `new_paid_api_calls` and `planner_receipt.new_paid_api_calls` include them, and `planning_economics` is
  recomputed from the receipt, so the bench adapter's cost completeness check holds.
- An analyst still running at the deadline marks the cost unknown (`new_api_estimate_usd: null`). It finishes in the
  background, and its calls land in the ledger and its cache.

## Shared implementation (bench and product send identical prompts)

| Product module | Holds | Bench re-export |
|---|---|---|
| `clinical_core/agere/clinical/semantic_docs.py` | documentation builder; examples loader (`config/agere/semantic_examples.json`) | `bench/semantic_context.py` |
| `clinical_core/agere/clinical/semantic_analyst.py` | `SemanticWarehouse` (per-thread read-only connections), `bounded_query`, `CONTRACT`, `SEMANTIC_RULES`, `SEMANTIC_SQL_TOOL`, `semantic_context`, `tool_loop`, `analyze`, `RECONCILE_RULES`, `canonical`, `candidate`, `reconcile` | `bench/common.py`, `bench/adapters.py`, `bench/reconcile.py` |

- Both modules import only the standard library and each other. They are the only product modules the bench may
  import (`bench.test_bench ... test_context_is_documentation_from_text_not_product_imports`).
- The tool loop takes `send(body) -> (response, cost_usd, cache_hit)`. The bench posts directly with its key; the
  product uses the ledgered send.
- Verified examples cite only development or challenge questions. `semantic_docs.load_examples` rejects any other
  split. `test_together.ExampleProvenanceTests` checks that no example id or question text appears in a heldout set
  (`bench/questions/heldout*.json`), `bench/capability` or `bench/acceptance`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `AGERE_TOGETHER` | `on` | `off` restores the fast path exactly |
| `AGERE_SEMANTIC_MODEL` | `claude-haiku-5-5` | semantic analyst model |
| `AGERE_RECONCILE_MODEL` | `claude-opus-5-5` | reconcile model |
| `AGERE_TOGETHER_BUDGET_S` | `90` | wait for the analyst, from question start |
| `AGERE_RECONCILE_BUDGET_S` | `60` | reconcile turn limit |

## Tests

`test_together.py` uses fake transports and makes no paid calls. It covers:

- agree → verified;
- disagree → reconcile pick (analyst or fast);
- abstain → partial/low with both readings;
- engine answer preserved, lowered only on disagreement or unsupported;
- both unsupported;
- fast error → analyst answer;
- analyst timeout → fast answer unchanged with unknown cost;
- no semantic layer;
- flag off / no live models / non-auto planning identical to the fast path;
- one sqlite connection per thread;
- examples exclude heldout, capability and acceptance questions;
- the stack lock is released during the wait and re-acquired after.

## Open

- The design note (`tmp/AGERE_TOGETHER_DESIGN.md`) proposes pointing the analyst at `canonical.sqlite3` with the
  canonical docs (`docs/agere/CANONICAL_MODEL.md`, `bench.adapters.frontier_canonical`). This build uses the semantic
  layer, the configuration that was measured.
- No live run yet. Planned: start a second service on :8886 from this branch, ask a few development questions, and
  check `together` receipts and ledger costs.
