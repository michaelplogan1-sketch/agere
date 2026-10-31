# Review: source-suspension dependency correction

I executed nothing and did not run the AGENTS.md start-of-turn or gate-intent checks. Everything below is from reading the supplied code and diff. Items marked **(hypothesis)** are unexecuted. The fixture correction is accepted: my earlier claim that `source_request_hashes` were empty was wrong, and zero-call default behavior is not shown by my review.

## Defects

**1. Procedural-lesson reuse ignores the ancestor chain (concrete, fix required).**
- `procedural_memory._source_readings_current` calls `source_readings()` only on the lesson's own context.
- `propose` uses `A.load`, which walks parents, so proposal is covered.
- `ground` and `select` are not. If a lesson was grounded from a derived context and its parent is later suspended, the lesson stays selectable.
- This is the same class that `test_derived_saved_context_keeps_parent_source_dependency` covers for work, but nothing covers it for lessons.
- Smallest fix, with no `A.load` recursion:

```python
def _source_readings_current(path, lesson):
    from ..deliverables import answer_context as A
    from .answer_dependencies import seal, source_readings
    key = lesson.get('context_id')
    if not isinstance(key, str) or not re.fullmatch(r'ANS-[0-9a-f]{24}', key):
        return False
    try:
        context = json.loads((A.root(path) / key / 'answer.json').read_text())
        if not (context.get('dependencies', {}).get('derivation') == lesson.get('trajectory')
                == seal(path, context)['derivation']):
            return False
        seen = {key}
        for _ in range(32):
            if not source_readings(path, context)['source_readings_current']:
                return False
            parent = (context.get('cohort_transform') or {}).get('parent_context_id')
            if not parent:
                return True
            if parent in seen or not re.fullmatch(r'ANS-[0-9a-f]{24}', parent):
                return False
            seen.add(parent)
            context = json.loads((A.root(path) / parent / 'answer.json').read_text())
        return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
```

  Add one test: a lesson grounded from a child context, then the parent suspended, then `select` returns `[]`.

**2. Spurious in-transaction failure (low, trivial fix).**
- `create_work` and `update_work` compare the whole `source_disposition` dict before and after.
- That dict includes `checked_assertion_ids` and `unchecked_assertion_ids`.
- A concurrent unrelated cache write that adds an observation row for a dependent assertion changes those lists. The commit is then rejected with "status changed" although nothing was suspended.
- Compare only `(status, suspended_assertions)`.
- The suspension case is already caught by the non-review path's `load`. The review path needs this comparison, so keep it there.

**3. Empty findings may read as absence (hypothesis, highest-value check).**
- `projection` leaves `findings['field'] == []` and moves the suspended findings to `prior_findings`. The test asserts exactly that.
- `review_cards` adds a `suspended_source_reading` verification and a notice that is carefully worded.
- I did not see the field-level renderers in the dashboard, PDF and cards. If any of them print an empty list as "none documented" or "no reason found", this becomes the clinical-absence error you asked about.
- Render one suspended patient through the card, dashboard and PDF paths. Assert that no field shows a negative or empty-as-none statement and that each shows an unresolved or suspended marker.
- If one does, key the field renderer on `source_reading_suspended`. Do not change `projection`'s output shape.

**4. Lesson deselection can invalidate unrelated answers (hypothesis).**
- When `select` drops a suspended lesson, `procedural_memory.identity` changes.
- `validate` then sets `derivation=False` for any answer that used that lesson, with the message "procedural lesson … no longer applicable".
- `load` turns that into `invalidated=True`. This applies to answers whose own source readings are fine, and it also applies to a store outage, because `_source_readings_current` is False when the store is unavailable.
- This conflates suspension and outage with planning invalidation, the separation we wanted.
- It may be acceptable because such an answer did rely on an untrusted lesson. It is untested and the message is misleading.
- Add one test with an answer that used a lesson. Then either accept the behavior and add a specific issue string, or leave it as a recorded known gap.

**5. Dependency can be subtracted by retirement (hypothesis).**
- `active_assertion_ids` returns `active | (answer_ids - retired)`.
- If a recheck retires X in `investigation` while X remains in non-`prior_` `answer` or `patient_rows`, X stops being a dependency.
- The saved answer's numbers are not recomputed, so they may still derive from X.
- Decide whether recheck retirement should discharge answer-table dependencies. If not, drop `- retired` for `answer_ids`.
- Add a test with X in both places.

**6. Persisted work fingerprints (hypothesis).**
- `work()` now adds `source_review_required` and `source_disposition_status` to every item, including current ones.
- Any persisted `fingerprint(work(...))` made before this change, such as saved-report or bundle receipts, will mismatch. It will surface as "Review work changed during generation".
- Grep consumers of `work(`, `A.work` and `review_work` receipts.
- If any are persisted, attach the flags outside the fingerprinted payload.

**7. Callers that read flags instead of `load(current=True)` (hypothesis; open).**
- `workflow.refresh` needed a manual guard because it uses plain `load` plus `invalidated`. That suggests other callers exist.
- Run `grep -rn "\['invalidated'\]\|get('invalidated')\|\['stale'\]\|get('stale')\|retained_inspection"`.
- Confirm that each mutating or spending caller is covered. Check the action-simulate endpoint, saved report and monitor creation, and bottling and learning paths.
- Confirm that no code path falls back to `retained_inspection=True` after catching a broad `ValueError`. That would turn a suspension block into a generated deliverable.

## Lower-priority notes

- **Unchecked coverage fails open by design.** An assertion with no bound observation row is "unchecked", not "suspended", and `source_readings_current` stays True. Suspension cannot reach unbound assertions, and `quarantine_assertions` cannot either. Report the checked and unchecked counts when you read the 57-patient context. Make sure no notice or label reads `source_readings_current: True` as "verified".
- **One suspension can reach several assertions.** Typed events are looked up per observation, so an observation bound to several assertions suspends all of them. That matches the `_disputed_reading` semantics but is untested.
- **A corrupt span row blocks every answer with a span assertion.** `json_extract` raises on malformed JSON anywhere in the table. That is fail-closed, which is correct, but it has wide blast radius. Do not "fix" it with `json_valid`, because that would hide corrupt rows and fail open. The unindexed scan and the repeated loads (ancestors × 2–3 loads) are unmeasured, so time them on the real context.
- **Non-evidence work cannot be closed under suspension.** `update_work` blocks every transition, including closing or withdrawing. Check whether `STATES` has a terminal state. Allowing only a transition to that state would be a product choice, not a safety defect.
- **Read-only claim.** `observation_path` calls `operational_path`. I did not see whether that creates directories. Confirm it has no side effects.
- **Duplicated integrity logic.** `assertion_disposition` duplicates the span digest and label rules outside `span_repair` (cache-bound). Rows written under older `CACHE_VERSION` values would read as `unavailable`. That is safe but could block old answers.
- **`BudgetLedger.reserve` guard is narrower than "zero calls".** It proves no ledger reservation. Also patch the sender, or state that limit.
- **Missing tests.** The `update_work` race, more than 900 IDs in a chunk, shared observation across assertions, and the JS `canChangeWork` logic are untested.

## Verdict

The finite scoped candidate is acceptable after fix 1 plus its test, and trivial fix 2. Checks 3 and 7, and tests for items 4 and 5, are acceptance conditions on the evidence, not new design. Items 6 and the lower-priority notes are recorded gaps if they pass.

The commit window across independent stores remains. This is authored synthetic evidence with a scripted judge. It is not model or clinical qualification, not MVP acceptance, and no native real-provider run has occurred. The 57-patient read-only test should report `status`, suspended, checked and unchecked counts, wall time, and confirm the old receipt is unchanged.

**Report:** G3 / suspended source readings block program work, lesson reuse and generation, and route to source review / red test now passes and 77 checks pass (your reported results, not re-run by me) / remaining gap: lesson ancestor chain, absence-rendering check, caller grep, real-context read-only run.
