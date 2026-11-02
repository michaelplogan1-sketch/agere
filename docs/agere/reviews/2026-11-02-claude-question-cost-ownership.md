I did not run anything. Every finding below comes from reading the supplied diff, tests and final telemetry source, so none is a reproduced defect. I could not see `BudgetLedger`, `model_policy`, `document_review` or the callers of the changed helpers, which limits findings 3 to 5.

## Findings, in priority order

### 1. A reuse-only row hides an untagged charge (introduced logic gap, from the code)
`attempts_since` flags missing tags only when a row has no tags at all (`not who`). The following sequence defeats it:
- Question A's worker pays for a call but writes no charge tag, for example on a path that was not wrapped.
- Question B then reuses that validated row and writes a `reused=1` tag.

The row now has `who={B: reused}`:
- **For A:** `question_id not in who`, so the row is skipped as "another question made this call". A, the true payer, reports complete and known spend that omits the call.
- **For B:** `reused=True`, so it is free and `tagged`. B is correct about its own cost.
- **For everyone else:** nobody flags the paid call as ambiguous, and no receipt shows it.

A row reserved inside the window with zero charge owners (`charge_owners == []`) should be treated as `window`/unresolved for every overlapping probe, not skipped. `ambiguity` is also computed only for the question's own entry. The new "missing tags" test covers only the case of no tags at all.

**Required test:** A pays through an unwrapped worker, B reuses, both finish, and neither receipt may claim a known total.

### 2. Worker context copying may change request identity (likely defect, unverified)
`QuestionLedgerPath` was designed so the cache-bound producers see unchanged inputs. But `inherit_context` copies every ContextVar into answer_guard, atomic_questions, system_one and backstop workers, not only `CURRENT` and `OWNER`. `Probe.start` also calls `model_policy.mark_question()`, which looks like context-scoped state. If model resolution or `_evaluating()` reads a ContextVar, requests from those workers now resolve differently from the baseline.

If model resolution is context-dependent, the model choice (the Haiku-first ladder) and therefore the request body and key change. That means cache misses, new paid calls, and a violation of the stated source-key preservation. The same copying could also change evaluation suppression.

**Required test:** for each changed dispatcher, compare the request hash and body from a worker with and without an active Probe against the baseline. If they differ, pass only the ownership vars or restore the policy state.

### 3. Existing tests do not exercise the wrapped path type (coverage gap and risk)
`QuestionLedgerPath` is not a `Path`. It has no `.parent`, `.exists`, `/`, `.resolve`, `__eq__`, `__hash__` or `__repr__`.

The helper also has no wrapped-path coverage under an active Probe:
- **`together._ledger_path`:** it used to return `Path(explicit)`. It now returns a wrapped object inside a question and the raw `explicit` outside one, so a string `stack.ledger_path` is no longer normalized to `Path`. Any caller using Path methods breaks, and only inside a question, which the 315 tests mostly don't exercise.
- **`system_one.attribute`:** it used `str(path) if path` and `Path()` normalization in `_open_ledger`. It now passes raw or wrapped values to `BudgetLedger`, and `if path` changed to `is not None`, so an empty string behaves differently.
- **Identity comparisons:** any path equality or dict-key use (connection pooling, "is this the default ledger") sees identity comparison for wrapped paths.
- **Serialization:** any `json.dumps`, pickling or `isinstance(Path)` use is at risk.

SQLite accepts PathLike, and the document_review test shows `BudgetLedger` retains the wrapper. Whether `BudgetLedger` or its callers call Path methods on `.path` is the unverified part.

**Required tests:** run `together._ledger_path`, `ledgered_typed_send_factory` and `system_one.attribute` under an active Probe with both str and Path inputs.

### 4. Stale ownership if a bound path outlives its question (hypothesis)
`bind_ledger_path` captures `CURRENT` once. If a factory or closure is built inside question A and reused later, `ledgered_typed_send_factory` being the candidate, a worker with no context writes tags to A. B then skips those rows as "another question made this call" and reports complete, known spend that is too low. With context present it degrades to a conflict, which is safe but permanent.

Check whether any bound path is cached across questions. Add a test where the bound path is used after A finishes, during B.

### 5. Receipt and learning consequences are untested (risk)
- **Unknown values:** ambiguity now yields `api_calls`, `api_usd`, `no_paid_call` and `llm_free` as `None`, and `paid_calls` as `None` when a row is ambiguous. Any consumer that sums these or tests `not no_paid_call` treats unknown as paid or fails with a `TypeError`. I did not see those consumers.
- **`record()`:** `answer_costs.api_calls` becomes NULL, so downstream aggregates need a check.
- **Poisoned windows:** any untagged ledger write during the window turns every overlapping question's cost into unknown. This includes rows from background work, other processes, or unwrapped paths. That is fail-safe and probably intended, but it should be an acknowledged availability trade-off with a test.

### 6. Smaller observations
- **Tagging mechanics:**
  - `tag()` commits `ledger.db`. If it runs inside a caller's open transaction, it commits that transaction early. This is likely pre-existing, but worth confirming.
  - A tag write failure on a reuse tag is recorded as an issue, so it marks the question unknown even though a reuse is free. This is conservative and acceptable.
  - `OWNER.reset` swallows `ValueError` only. A reused token raises `RuntimeError`, though `_owner_token` is cleared first.
- **answer_guard coverage:** answer_guard's 23 errors and two failures are unchanged from baseline, but those fixtures cover `PendingCheck` and `_bounded`, which are two of the changed paths. The substitute test mocks `_run`, so it cannot catch regressions in the real flow.
- **Dispatcher late-completion:** background work finishing after `Probe.finish` is now tagged to the finished question. That correctly keeps it out of later questions' receipts but leaves it unreceipted. This is consistent with existing background-continuation reporting.

## Verified as sound from the code
- **Conflict handling:** a conflicting bound versus context owner returns without tagging and flags both owners.
- **Binding:** an already-bound path is returned unchanged.
- **`inherit_context`:** it gives each worker a fresh copy, with one parent snapshot at submission.
- **Retry, failed and pending paths:** the attempts remain separate rows, and unpriced rows give `usd_known=False` without losing ownership.
- **Early cache paths:** the early cache paths in the gateway and `context_student` add reuse tags without changing request keys. The cached-lineage test also asserts the `calls` rows are unchanged.
- **Planner counts:** not demanding planner/ledger count equality is right. `receipt()` only uses planner counts as a cross-check where `reported > calls`.

## Verdict
There is one concrete logic gap in the missing-tag ambiguity handling (finding 1), and one plausible key-preservation risk (finding 2) that must be checked against `model_policy` and `_evaluating` before this is accepted as byte-safe. Findings 3 to 5 are unverified risks and test gaps, not reproduced defects. I'd hold acceptance until findings 1 and 2 are resolved or ruled out, and until finding 3's tests exist.
