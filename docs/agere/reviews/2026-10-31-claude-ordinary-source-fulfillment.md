# Review of the source-suspension fulfillment fix

This is a read-only review of the supplied code and diff. I executed nothing and did not see the final 109-test rerun. "Proven" below means it follows directly from the code shown. "Hypothesis" means I need something not shown.

## What holds up

- The reference is captured and attached to `result` before `A.load`. The new `except` branch keeps it, and `stop` plus the continuation record are written.
- Source suspension and service failure are separated correctly. Only a load that succeeds with `source_review_required` takes the report path. Stale, invalidated and unavailable states raise before the suspension check, so they land in the blocked branch.
- `materialize(..., source_review=True)` rejects any generation other than `saved_report`. An explicit `frontier` choice is short-circuited in `fulfill` before any dispatch, with no substitution.
- Refresh cost arithmetic is consistent. Persisted totals stay cumulative and the returned figures cover only the new render.

## Required fixes (proven from the code)

1. **`materialize` can still leak an exception out of `fulfill`, which is the same regression class.**
   - `fulfill` calls `materialize` outside any `try`.
   - Inside `materialize`, `A.load(current=True)` is wrapped only for `ValueError`.
   - If the source becomes unavailable between `fulfill`'s load and `materialize`'s load, the fallback `A.load(path,key)` is not stale or invalidated, so the code re-`raise`s.
   - `OSError` and `sqlite3.Error` are not caught there at all.
   - Result: the exception propagates to `handle_compose` and the `action_context` is lost.
   - Fix: wrap the `materialize` call in the same handler as the load. Keep the reference, block the steps, set `action_context_error`, and record an attempt. No per-call costs are lost, because `materialize` returns nothing on this path.

2. **A retained-inspection report never re-asserts currency.**
   - `create(..., retained_inspection=True)` calls `evidence_bundle` and `bundle_validator` with `current=False`, so the stale, invalidated and unavailable checks are off.
   - The validator only compares `dependencies` and the source-disposition identity.
   - A data change in the window between `materialize`'s current load and `create` can therefore produce a `ready` report, and `materialize` then stamps the older `source_review_identity` on the record.
   - A disposition flip is caught (it raises, and the step is blocked). A pure data-staleness change is caught only if it alters `dependencies`, which I cannot see.
   - Fix: after `create` and before `_save`, reload with `current=True, source_review=True`. On failure, mark the step blocked and do not stamp the identity. Add the race test (see the finite check below).

3. **A source-review workflow is never refreshed once the suspension clears.**
   - `refresh_source_view` requires `source_review` to be true.
   - Once an inspection-only record is saved (blocked programs and monitors, retained report), an ordinary non-suspended caller hits `saved.get('initialized') and not refresh_source_view` and gets that frozen record back indefinitely.
   - Fix: treat any record carrying `source_review_identity` as refreshable by any caller whose current identity differs, including the "current" identity. Preserve history and costs exactly as in the suspended refresh.

4. **`evaluation.active()` exits before the partial downgrade.**
   - On that path a suspended-source answer is returned with its original status and no `source_review_required`.
   - This violates "overall partial/unverified".
   - Fix: apply the downgrade (and the error branch's equivalent) before the early exit, or document that evaluation mode is exempt.

5. **Minor record and message misstatements.**
   - The frontier-blocked stage records `rendered: bool(result.get('workflow'))`, which is `True` although nothing rendered.
   - The "requested frontier view" reason is applied to program and monitor steps too.
   - Blocked simulation and monitor steps in source review say "Resolve the incomplete analytical answer first." They should name the suspended source reading.
   - The returned answer overwrites `status` without preserving the original, for example as `captured_status`. The immutable context keeps it, but the returned object loses it.

6. **The aggregate economics statement can mislead.**
   - After a refresh, the persisted workflow has `api_cost_known: False`.
   - The returned `planning_economics` says `api_cost_known: True`, with the scope text "All analysis and artifact attempts for this original request".
   - The code does this deliberately, to charge only the new call.
   - Add an explicit field, for example `prior_workflow_costs: {known_usd, calls, cost_known}`, or reword the scope. Otherwise unknown earlier pricing is hidden at the user-facing level.
   - Test 5 asserts persisted values but not the returned economics from `fulfill`.

## Hypotheses to verify (not proven)

- **Ordinary-caller behavior change.** The new `except` branch applies to every `fulfill` caller, not just suspension. Previously a stale or invalidated capture raised. Now it returns a partial result with `action_context_error`. Confirm that `handle_compose` surfaces `action_context_error` and does not present the result as success.
- **Uncaught exception types.** Corrupt stores or answers can raise `KeyError` or `TypeError` (for example `item['status']`, `data['source']`). The `except` catches `ValueError`, `OSError` and `sqlite3.Error` only. The test deletes a row but does not corrupt a file.
- **Suspension visibility in the report.** The saved report renders the original `context['answer']` status. Confirm that the HTML, PDF, JSON and CSV prominently carry the `source_disposition` and suspended assertions. Otherwise the report may say "answered" while the returned status is partial.
- **`program_results` still returned.** On the source-review path `save_program_workflow` is skipped, but results computed from a suspended source remain in `result`. Confirm they are suppressed or labeled.
- **Monitors and program links.**
  - A refresh replaces a previously `watching` monitor step with a blocked one in the active record. The old state survives only in `source_review_history`.
  - Check that monitor-check code does not need the active record.
  - A record with `program_links` would lose them from the active record on refresh. I cannot see `save_program_workflow`.
- **Test coverage gaps.**
  - The tests call `W.fulfill` directly, not `handle_compose`, which is the actual failing consumer.
  - There is no test for monitor steps or `program_results` under suspension.
  - There is no test for a stale or corrupt store, any race, or unsuspension after a refresh.

## Finite check before the ordinary original-question confirmatory run

One offline check, with no providers, no training and no broad reads:

1. **Drive the ordinary controller path.** Call `handle_compose` (or the ordinary HTTP handler) with the saved native 57-patient result, once with a suspended source fixture and once with current sources.
2. **Suspended case.** Assert all of the following:
   - `action_context` is returned.
   - Overall status is `partial`/`unverified`, and the captured status is preserved.
   - The `saved_report` step is `ready` and the report formats open through `read_format`.
   - Program and monitor steps are blocked.
   - There are 0 provider calls and the 15082 attempts are preserved.
3. **Current case.** The normal path is unchanged, including generation defaulting.
4. **Failure injection.** Include these three at minimum:
   - Make the source unavailable after `fulfill`'s load but before `materialize`'s load.
   - Corrupt the observation DB bytes.
   - Mutate the data between `materialize` and `create`.
5. **Fixes 1–3.** Each should show the reference preserved, steps blocked, and no `ready` stamp.

I'd rate the confirmatory run ready only after fixes 1–4 land and the final 109-test rerun is green. Fixes 5–6 can follow.
