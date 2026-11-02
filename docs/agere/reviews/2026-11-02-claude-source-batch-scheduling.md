# Independent source-batch scheduling review — November 2, 2026

This review used the existing Claude subscription on supplied source/tests and receipts. It did not execute code, read the warehouse independently, adjudicate clinical truth or qualify a model. G3 performance work; all ten acceptance areas and the G5 qualification dependency remain.

## Initial exact review

**Verdict:** I found no deadlock or ordering bug in the scheduler itself. I did find one concrete defect, plus several risks that the shown evidence cannot clear.

**Concrete defect**

1. **New dispatch can happen after a failure has already occurred** (`_run_source_batches`, dispatch loop vs `wait`).
   - Failures are only seen after `wait()` returns, and the dispatch pass at the top of the next iteration never checks pending futures.
   - If a worker fails while the main thread is handling another completion, the next pass still submits new batches. This is possible with 3 workers.
   - The failure test hides this. With 2 workers, a barrier and a sleep, the failing future is always observed before any dispatch.
   - Fix: before each dispatch pass, scan `pending` for futures where `f.done() and f.exception()`. If one exists, stop dispatching and raise after the in-flight work drains. Add a test with 3 workers where the failure lands during processing of another completion.

**Lesser issues**

2. **Only the first exception propagates.** Other failed in-flight futures are never retrieved, so their exceptions are silently dropped, and "exception was never retrieved" noise is possible. Their ledger receipts survive, but the raised error under-reports. Collect all errors after the pool drains and chain or aggregate them. Successful in-flight results are also discarded; this is acceptable only because the work is cached.

3. **The conflict key may be too narrow.** Batches are serialised on `(record_id, date)` only. If the paid-request or source-field cache key is content- or prompt-based, two different records with identical text or evidence could issue the same request key concurrently. That would be a duplicate paid request or a reserve race on two connections. I can't see `reserve`, so I can't confirm it. Either serialise on the actual request/cache key, or add a test where two disjoint records map to the same request key.

4. **Cap enforcement across connections is unverified.** Each worker opens its own `BudgetLedger`. If `reserve` does a check-then-insert without `BEGIN IMMEDIATE`, an explicit `cap_usd` can be overshot by up to the worker count. Uncapped is the default, but the cap path needs a concurrent test.

5. **Worker ledgers are plain `BudgetLedger(path)` objects.**
   - They drop any subclass, wrapper, or extra state on the caller's ledger. This includes `legacy_summary`, event hooks, and any `ledger_event` target tied to the original object.
   - `read_batch` shadows `ledger` correctly, but any path that reaches the original ledger through `tools.stack` or the default gateway would reuse that connection concurrently. The comment on the connection says it is "never shared concurrently".
   - Confirm that `judge` and `system_one` use only the passed ledger.

6. **Shared mutable state in threads is unaudited.**
   - `bind_assertion_observations` writes to the stack DB from several threads. It needs a per-call connection and a busy timeout.
   - `component()` lineage and metrics, if backed by a mutable list in a contextvar, are copied by reference by `copy_context()`. That could interleave or race.

7. **Resource bound.** Worst case is 3 patient workers × 3 batch workers = 9 threads and 9 SQLite connections, each running `executescript` DDL on open. This is acceptable, but lock contention at the 30s timeout is possible. The "worker limit is not a patient cap" claim holds here: the limit is scheduling only.

**Evidence gaps**

- The retained receipt has `provider_calls: 0` and about 0.056s for both baseline and candidate. It shows output equivalence on a cached path only, with no real concurrent paid dispatch and no speedup. That matches your own claim, so no performance claim should be made.
- The 118 passing tests don't cover items 1, 3, 4 or 6.

**Checks that look correct**

- Ordering is preserved through the blocked-earlier-source reservation, and results are returned by index.
- A pool of at least 1 always dispatches the first waiting batch, so the loop cannot spin or deadlock.
- Context is copied per submission.
- Invalid worker values raise before any dispatch.
- Unknown source identity falls back to serial on the original ledger.
- Pool exit waits for in-flight work, so receipts are preserved.

## Exact follow-up review

**Verdict:** I found no remaining concrete defect in the diff or tests shown. The first review's items 1 and 2 are closed, and items 3 to 5 are covered by tests. Four residual risks are not cleared by what I can see.

**What I checked**

- **Dispatch after failure (item 1).** `failed.set()` runs under `dispatch_lock` before the worker's future completes. The check and `pool.submit` are atomic under the same lock, so a late failure cannot slip past the dispatch check. `test_worker_failure_during_completion_handling_prevents_later_dispatch` would fail without the check, because D would be dispatched after A is consumed.
- **Empty `wait` hang.** `wait()` on an empty set blocks forever with no timeout. I could not construct a path to it:
  - With nothing in flight, the first waiting batch always dispatches.
  - With `failed` set, the failing future is still in `pending` until `result()` raises, so `pending` is never empty at that point.
- **Error aggregation (item 2).** The `except BaseException` block cancels queued futures and drains the rest. It adds notes to the primary exception, which keeps its type and `kind`. Unretrieved-exception noise is gone.
- **Journal sequence.** `Execution.append` now holds the lock across the sequence read, the insert and the increment. I saw no re-entrant call from inside `append`, so the non-reentrant `Lock` should be safe.
- **Ordering and local state.** The blocked-earlier-source reservation is unchanged. `read_batch` shadows `found`, `checks` and `unresolved` locally, and the merge happens only in the main thread in batch order.

**Residual risks (unconfirmed, not demonstrated defects)**

1. **`BudgetLedger(ledger.path)` rebuilds the worker ledger from the path only.**
   - If the caller's ledger lives at `:memory:` or a non-file path, workers silently get separate, empty databases. That would bypass the cap and the receipts.
   - Any state set on the instance after construction (hooks, flags, a `legacy_summary` source) is also dropped. The `type(...) is BudgetLedger` guard covers subclasses but not instance mutation.
   - Fix: also fall back to serial when the path is not a real file. Optionally compare relevant instance attributes against a fresh instance.
2. **Contextvar-backed lineage state.** `copy_context()` copies references to mutable objects.
   - The `append` lock fixes the journal sequence only.
   - If `component()`'s stack, a parent's metrics dict or the `cost_telemetry` tag structures are shared mutables, workers can interleave on them.
   - The event and `call_questions` counts in the test are a good sign, but they exercise only `reserved` and `request_finished`.
3. **Does the same-text test reach the scheduler?** `test_identical_text_in_distinct_records...` may place both sources in one transport batch. If so, it proves distinct keys but not concurrent safety. Assert that `_run_source_batches` actually ran with more than one batch, for example by checking `peak_in_flight`.
4. **Masked errors in `invoke`'s `finally`.** If `worker.close()` raises, it replaces the original exception. This is minor. Guard it, or add the close failure as a note.

**Evidence limits**

- Passing tests and byte-exact cached-path outputs support engineering equivalence only.
- The paired live run covers one 6-note case, with serial 1.581s against parallel 0.747s. That is not a general speedup claim.
- The probability and check differences across live arms are preserved, and no equivalence claim should be built on them.

## Owner assessment and resulting candidate

The dispatch/failure race was a valid concurrency concern. A worker marks failure under the same lock used for submission; a deterministic three-worker test fails the pre-correction schedule. In-flight futures drain, all exceptions are retrieved, and additional errors attach notes to the original exception so its subscription-limit type/kind survives. Custom ledger subclasses remain serial. The copied request context exposed a mutable append sequence in request_lineage; append now commits each sequence under a per-execution lock. Actual request-event and question-cost attribution tests cover the copied context. Typed observation/binding functions open their own SQLite connections with30s busy timeout; reserve already uses BEGIN IMMEDIATE.

Following the second review, in-memory/nonfile and customized ledger instances also retain the original serial connection. A close failure cannot mask an existing source exception. The same-text/different-record test explicitly asserts two transport batches. These final defensive additions were owner-tested after the second review; there was no third model review. component metrics are invocation-local dictionaries and cost_telemetry.CURRENT is a question-ID string; neither introduces a shared mutable metrics stack.

The old/new live arms each made9Jev requests with identical request hashes and knownAPIestimate$0.000562002; measured1.580728s and0.747426s. Findings, coverage, unresolved source/field/disposition and labels are exact, while raw probability/check receipts differ and remain retained. This is one paired six-note authored access example, not a whole-clinical-workflow speedup, clinical qualification or a superiority result. The full original57patient workflow has not been repeated for this scheduling increment.

The current source-reader semantics, complete source texts, field questions, thresholds, source-cache keys, output order and all prior attempts remain. document_review.py, span_repair.py and model_policy.py remain byte-identical to fa28466. Default3batch workers may overlap under existing3patient workers; AGERE_DOCUMENT_BATCH_WORKERS=1 is the serial scheduling recovery. No source-family/clinical/outcome labels, new trained weights or clinical trace promotion are implied.
