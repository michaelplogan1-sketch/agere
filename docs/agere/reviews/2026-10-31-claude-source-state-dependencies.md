# Review: suspension-to-current-answer gap

I did not execute anything, and I did not run the AGENTS.md start-of-turn checks (`check_agere_build_control.py`, gate intent). This is a review of the supplied excerpts only. Anything about code not shown is marked as a hypothesis. Before landing, the implementer must run the start-of-turn checks and the gate intent check, and update the control checkpoint.

## 1. Is the gap proven?

**Code proof (supplied excerpts):**
- `answer_dependencies.seal` records two things: a derivation fingerprint over `FIELDS`, and `{'source', wal_identity(path)}`. `wal_identity` stats only `semantic.sqlite3-wal`.
- `answer_dependencies.validate` additionally consults only procedural memory and `plan_fence.premise_issues`. Nothing in it reads `span_observation_events` or `typed_observation_events`.
- `answer_context.load` adds these currency checks: source identity, `capture_profiles` recomputation, policy identity, `correction_context`/`feedback_identity`, and the parent chain. None of them reads the observation store.
- `span_repair.quarantine_assertions` and `typed_document_review.quarantine_assertions` write only to `observation_store(path)` events. The excerpts show no write to the correction/feedback store that `feedback_identity` reads.
- `answer_context.create_work` calls `load(..., current=True)` at entry and again inside the transaction. If suspension is invisible to `load`, both gates pass.
- The ANS id includes `observation_dependencies`, which is the same WAL/source pair. A suspension therefore leaves the id, the seal and `answer.json` unchanged.

**Dependency set is derivable.** Saved contexts embed assertion IDs. `patient_evidence.projection` reads `(value.get('assertion') or {}).get('id')` from `investigation.document_reviews[].rows[].review_json` and from `documentary_state.assertions`. Dependent versus passive versus unrelated can therefore be decided from the saved context alone.

**Not proven by the excerpts:**
- Where `observation_store(path)` lives. Your statement that raw warehouse/WAL are unchanged is observed, not shown. If the store shared `semantic.sqlite3`, the WAL stat would flip for every answer, which is a different bug.
- Whether the 9 IDs sit in answer-bearing fields (table, claims) of the 57-patient partial context, or only in `investigation`. A read-only scan should settle this before the fix is sized.
- Whether `validate` has other callers that bypass `load`.

**Hazard in the naive fix.** In a corrected context, the raw `review_json` rows still contain the retired IDs, because `projection` retires them at presentation via `local_replacements`. A walk over raw rows would invalidate the corrected answer forever. The extractor must reuse the projection's retirement logic. Active IDs are the ones left in `findings` and in `documentary_state.assertions` after retirement.

## 2. Smallest correction

All edits stay outside `document_review.py`, `span_repair.py` and `model_policy.py`. `typed_document_review.py` is not in `span_repair.observation_key`'s file hashes, and its own `source_key` hashes no files.

1. **Extractor in `patient_evidence.py`.** Add something like `active_assertion_ids(context)` that returns the post-retirement IDs. Factor it out of `projection` so presentation and gating cannot diverge.
2. **Optional kwarg on `answer_dependencies.validate`.** Add `assertion_ids=None`. When it is given:
   - query the store for suspended IDs, mirroring existing semantics: any `quarantined` event, with span lookup via `json_extract(payload,'$.assertion_id')` and typed lookup via `typed_observation_bindings`;
   - chunk at 900 variables;
   - return `source_readings_current`, `suspended_assertions` and `coverage` (checked IDs versus IDs with no observation row).
   When it is omitted, behavior is unchanged.
3. **`answer_context.load`.** In the existing ancestor loop, pass each ancestor's extracted IDs. A narrowing child of an affected parent is then covered for free. Return `source_disposition` and `source_review_required`.
4. **Separate flags.**
   - Do not set `invalidated` or `stale`; their messages are wrong ("planning definitions", "data changed").
   - Raise a reason-specific `ValueError` for `current=True`.
   - Audit every consumer of `['invalidated']` and `dependency_validation`, including bottling and learning paths, so none treats this state as reusable.
5. **`create_work` split.**
   - For `program == 'evidence_review'`, pass a flag so both `load` calls accept `source_review_required`. Record `suspended_source_readings` (assertion and observation IDs) in `assessment['evidence_review']`. This keeps the existing `answer_review:evidence_review` lane and `clinical_execution_authorized: False`.
   - Every other program raises.
   - Pass the same flag on the in-transaction `load` so the existing rollback applies.

**Properties this gives:**
- Unrelated answers and patients have an empty intersection, so nothing changes for them.
- The original `answer.json`, dependency receipt and readings are untouched. Nothing is restamped.
- Clearing the block requires a new ask. The new context has a new ANS id and its own recheck-retired set.
- The supplied code shows no "released" event kind. Do not invent one; mirror the existing "any quarantined event" semantics.

**Fail closed.** `plan_fence` handling uses `except Exception: premises = []`. Do not copy that. A store error, an `ObservationIntegrityError` or a missing table must yield `disposition: unavailable`. Work is blocked and inspection is preserved. It must never be reported as "no suspension" or as a clinical unknown.

## 3. Reaching verification and presentation

**Immediate increment (presentation):**
- `projection` reads `context.get('source_disposition')`, which `load` attached.
- Suspended IDs are added to the existing `retired` and `prior_findings` path. That moves them to `prior_findings`/`audit_only_assertion_ids` and re-runs `reconcile` over the remaining assertions. They stay inspectable and are not active.
- The saved answer's own numbers are not recomputed, so the banner must say "relies on a suspended source reading; re-ask or open source review". It must not say the answer is corrected.
- Verification needs no model change for the immediate increment. Fresh runs already block disputed readings via `cached_source_judge` (`_disputed_reading`) and `span_repair` ("Disputed source observation … remains unresolved").

**Later:**
- Fidelity checking of a suspended reading can reuse `evidence_projection.source_dependency_packets`. Its `observation()` already verifies id, quote/offset, binding and axes, and its prompt disclaims clinical certification.
- Run it only inside source-review work, with accounting.
- Mark the packet `source_disposition: suspended`. A "supported" result means the reading matches its source. It must not add to `field_uses`, must not count toward `confirmed` in `review_final`, and must not lift the suspension. Per `span_repair`, only a change of source, question, model or policy does that.
- Keep this separate from the 9-assertion diagnostic recheck.

## 4. Regression (finite)

**Fixture.** Use real store functions that write receipts, because `cached_observation` rejects empty `source_request_hashes`. The earlier fixture failed exactly there. The fixture has:
- A: an answer with active assertion X;
- U: an unrelated answer with its own assertions;
- C: a corrected context whose recheck retires X;
- one passive-reading case, where an ID appears in `investigation` but is retired.

**Steps:**
1. Baseline: A, U and C pass `load(current=True)`. Snapshot the `answer.json` hash and the dependency receipt.
2. Call `quarantine_assertions([X])` and assert the returned count is exactly 1. A zero count means the fixture binding is broken, so the test fails rather than passing vacuously.
3. On the pre-change code, A still loads as current. That is the red test, and it confirms the gap empirically.
4. After the fix:
   - `load(A)` shows `derivation_current` and `observation_current` true, `source_readings_current` false, `stale` false, `invalidated` false, and suspended set `{X}`.
   - `load(A, current=True)` raises the distinct message.
   - `create_work(A, <any non-evidence program>)` raises, with `work_items`, `work_events` and `work_assessments` row counts unchanged.
   - `create_work(A, 'evidence_review')` succeeds with X recorded and no clinical authorization.
   - `answer.json` and the receipt are byte-identical to the baseline.
5. Controls: U works and its receipt is identical; C works.
6. Race rollback: suspend X between the first and second `load` inside `create_work`. Expect a `ValueError` and no committed rows.
7. Service error: make the store unreadable or corrupt a binding. Expect `unavailable`, work refused, inspection intact, and a message that is neither "suspended" nor "unknown".
8. Zero provider dispatch: patch the sender to fail if called, and assert the ledger is unchanged. Roll back by reverting the commit, and the red test returns.

## What could hide missing evidence

- **Coverage.** IDs with no observation row are neither suspended nor verified. Report them as `unchecked` rather than treating them as clean.
- **Diagnostic recheck.** The 9-assertion no-positives result had local sender failures. It is not evidence of absence or of zero cost. `span_repair` folds `AttributeError` into a generic "Unresolved source block"; that file is cache-bound, so fix it caller-side rather than in `span_repair.py`.
- **Re-ask.** A blocked reading must stay unresolved or partial with a disputed-source reason. It must not become "no finding".
- **Scope of block.** Blocking at answer level is conservative: all non-review work on an affected answer is refused. Per-patient narrowing is later work.
- **Existing items.** Work created before the suspension is not retroactively annotated, because creation is idempotent and skips existing items.
- **Cost.** Per-load cost is local only (extraction plus chunked queries) and unmeasured.

**Report format:** gate G3 / suspended source reading now blocks program work and routes to source review / the regression above (not yet run) / remaining gap: fidelity packets, per-patient narrowing, caller audit.
