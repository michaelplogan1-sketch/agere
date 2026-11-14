# G3 pre-implementation review

## 0. Basis and limits

- I read only the packet: the receipts, the audits and the code excerpts. I ran nothing and did not open the repository or the build-control files, so this review makes no product edits.
- "Code fact" below means visible in the excerpts. "Inferred" means arithmetic on the receipts. "Hypothesis" means it needs a test or measurement first.
- Files I could not see are `bounded_query`, `catalog`, `semantic_result`, the prose-binding validator, the final-answer gates, the cache keys, the `query_sql` tool schema, the clinical consumer and the feedback schema. Several recommendations below depend on them.

## 1. Verdict

The direction is sound, and the primary failure is item 1 plus item 3. Item 2 is real but is not shown to cause the repeated `four`/`12` failures. Item 4 is downstream presentation.

Three things in the packet are not yet covered by the proposal.

**A. Completeness alone does not fix the dose domain.**
- Registered keys and counts from `gdmt-dose-coverage.json`: carvedilol 1, lisinopril 3, losartan 7, benazepril 1, dapagliflozin 1, spironolactone 1, metoprolol_succinate 1, quinapril 2. That sums to 17 objects.
- Inferred: the three keys the preview omitted (spironolactone, metoprolol_succinate, quinapril) contribute 1+1+2 = 4. The other five keys therefore give 13 over complete rows.
- Counterexample to schedule: complete 89 rows plus the old five-key literal yields 13, not 17. The registry-derived domain is a separate must-fix, not a nice-to-have.
- Confirm by reading the actual planner literal. The identity of the five keys is inferred.

**B. The truncation was systematic, not random.**
- The 19 partial patient IDs are exactly the first 19 of the 29 complete IDs in sorted order.
- All 10 later patients (WHP-27E5… to WHP-DD25…) had zero rows in the preview.
- Their absence would have looked like "no dose evidence" rather than "not read".
- Hypothesis: the 40-row cut came from `max_chars=60000`, not `max_rows=2000`. Check which limit fired.

**C. Dependency detection cannot catch value transcription.**
- Authorizer-based or regex dependency tracking cannot see a literal copied from a preview into a later SQL `IN (...)` list. That is the actual laundering path here.
- Only two things close it: no truncated preview exists to copy from, and the domain comes from the registry instead of from observed keys.
- The omitted-keys list includes extraction artefacts (`actuat`, `hr`, `vitamin`). So "derive the domain from the keys present in the data" is wrong. The registry is the authority, and unregistered keys should be reported as unclassified, neither silently included nor dropped.

## 2. Item 1: store the whole executed SQL result

**Code facts**
- `query_sql` caps at 2000 rows and 60000 chars unless `complete` is true.
- `retain_result` builds `define_result` from `output_table(out)`, i.e. the already-truncated rows.
- A later query over the view runs through `bounded_query` on 40 rows and reports `truncated=False`. The flag lives on the producer's result and is not carried by the view.
- `compose._rows` and `materialize_table` test only the stored `truncated` flag, so derived results pass.
- `actor_result` already bounds what the model sees: a 12000-char threshold, a 3-row preview, and `computed_row_count_profile` over all rows.

**Must-fix**
1. In `_call`, always call `semantic.query` with `max_rows=None, max_chars=None`. Keep `complete` as an accepted, inert argument. Update its tool-schema text and the error strings that tell the model to "query with complete=true first", since that advice becomes wrong.
2. Verify that every model-facing serializer of a `query_sql` result goes through `actor_result`. If any path sends `out` raw, removing the char cap reproduces the 692 KB problem elsewhere. The test is a 5000-row result producing a bounded actor message.
3. Resource limits must fail loudly, never truncate silently.
   - A timeout must be an error, not a partial result. Confirm that `bounded_query` raises on timeout.
   - `define_result` embeds all rows as one SQL literal in `CREATE VIEW`, and again in `executed_query` per dependent query. Check `SQLITE_LIMIT_SQL_LENGTH` and `SQLITE_MAX_LENGTH` for the build in use; do not assume defaults. If the literal cannot be created, return an error. Never register a clipped view.
4. Version the `query_sql` result contract (a receipt field and a cache-key component).
   - Without this, a cached or replayed `(sql, complete=False)` call returns the old 40-row result and re-launders it after the code fix.
   - Legacy truncated results restored from receipts must be recognised as incomplete, via their stored `truncated` flag.

**Dependency tracking: choose the SQLite authorizer on the executed SQL**
- Do not use a regex. The existing `used = [n for n in sources if re.search(...)]` runs on `args['sql']`, which is pre-`scope_sql` and pre-macro. It is case-sensitive.
- Counterexamples for a regex (names assume a view `agere_result_1`):

| SQL | Truth | Regex |
|---|---|---|
| `SELECT 'agere_result_1' FROM drug_fill` | no dependency | false positive |
| `SELECT * FROM AGERE_RESULT_1` | dependent | false negative |
| `WITH agere_result_1 AS (SELECT 1 x) SELECT * FROM agere_result_1` | CTE shadows the view | false positive |
| `-- agere_result_1` | comment | false positive |
| `SELECT * FROM "agere_result_1" a` | dependent | match |
| `WITH x AS (SELECT * FROM agere_result_1) SELECT * FROM x` | transitive | match |

- The case-insensitive miss also affects the existing portable-receipt CTE embedding: the receipt can be left with a dangling TEMP name.
- Install the same authorizer wrapper that `SemanticWarehouse.query` uses, but also on the `if not defined:` path. As written, that path has no authorizer.
- Record the view names read. Use the `source` argument (5th) and `arg1` for `database == 'temp'`. The existing code already relies on `source in views`.
- Hypothesis to test first: that `SELECT count(*) FROM <result view>` reports a read attributed to the view. That is exactly this trace's case. Also test `SELECT 1 FROM v LIMIT 1`, `EXISTS(subquery)`, quoted names, mixed case, and a name inside a string literal, a comment and a shadowing CTE.
- If the count(*) case is not reported, reconsider the mechanism before building on it.
- The authorizer fires at prepare time, so it is static and conservative. A read in an unexecuted branch counts as a dependency. That is acceptable for an acceptance-only rejection.

**Propagate metadata and reject only at acceptance (recommended)**
- In `retain_result`, keep `self.result_incomplete[name] = reasons`. A reason is the producer's own `truncated` flag, or an inherited entry from any read view that is itself incomplete.
- Put `incomplete_dependencies` and `read_views` on `out`. They persist in `native_calls` and in receipts.
- Add one helper, `incomplete_reason(result)`, and call it from the existing gates: `compose._rows`, `materialize_table`, the prose-binding validator and final composition.
- Do not reject at query time. Inspecting a bounded retrieval, such as a `search_documents` top-k, then querying it is legitimate exploration. Blocking there creates false failures. A supported partial or unknown is an acceptable outcome; an accepted claim built on a clipped input is not.
- Do not reuse `truncated=True` as the inherited flag. It would engage all gates for free, but it mislabels a complete derived aggregate as "row cut" in what the model sees.
- Treat as incomplete only results with an explicit `truncated` true, or a `table_rows` mismatch. Invent no further rules.
- Cost: a count over a bounded retrieval bound in prose, such as "N passages retrieved", would be rejected. That is rare and acceptable.
- Failed producers: errors register no view, so there is nothing to reference. The residual risk is transcription (point C above).
- Risk: if a `define_result` exception escapes after the paid call, the attempt must still be retained. `Tools.__call__` only catches `TypeError`; check where the attempt record is written.

## 3. Item 2: native feedback

**Code facts**
- `messages()` copies `feedback`, `reason` and `coverage`, plus `support.packets[].claim_feedback` for every non-supported packet, into the state. It also adds `current_review_native_digest`.
- `recovery_context` already stores full feedback in a digest-keyed, idempotent native view with a small reference.
- `retain_result` appends to `self.seen` and to `rows_by_record` for any row carrying `record_id`.

**Hypothesis**
- The 692,528 vs 60,841 byte growth is mostly the 371,695-char feedback string plus the duplicated `claim_feedback`. Measure bytes per top-level key before changing anything. `json.dumps` defaults to `ensure_ascii`, so non-ASCII clinical text expands.
- That the bloat caused the identical `four`/`12` failures is unproven. Those need SQL-computed scalars bound by placeholder. Do not claim the compaction fixes them.

**Proportional change**
1. Generalise the `recovery_context` mechanism into one function. Store feedback as native result views of a distinct `kind` (for example `review_feedback`), with digest-keyed reuse.
2. Use one row per entry (all 516), with the exact raw entry text and digest, plus index columns taken only from existing structured keys: patient, field, assertion or record identity, and located vs unknown from the existing label. Do not parse prose by regex. If a key is absent, the column is NULL.
3. Add the same for pending `claim_feedback`, one row per packet id.
4. Keep these in the message verbatim:
   - the exact current compiler instruction (the numeric-prose text, which is small);
   - the status, reason and coverage fields;
   - counts: 6 located faults vs 510 unknown, the 4 families, and the 13 unresolved packets;
   - the six located faults, if measured small, otherwise ids plus a query pointer;
   - the view reference and a one-line instruction to query before repair.
5. Find where the compiler instruction currently lives. If it is embedded in the 371k string rather than a separate field, split it structurally at its source. Do not carve it out of a string.
6. Unknown entries stay unknown. No model vote promotes or demotes anything.

**Guard (must-fix if implemented)**
- Feedback views must not enter `self.seen` or `rows_by_record` as observed source evidence.
  - The draft's unverified values live in feedback (the old `four`/`12`).
  - If `seen` backs any value-grounding check, retaining feedback there could launder them. I have not seen how `seen` is consumed, so verify.
  - `recovery_context` already routes through `retain_result`. That existing case needs the same check.
  - Skip `seen`/`rows_by_record` registration by `kind`, or name the identity columns something other than `record_id`.

**Progress identity**
- In `progress_evidence`, `names[view] = 'native_' + ident` is applied only for views produced by calls in `calls`. Feedback views are retained outside `calls`, so a query reading `agere_result_K` would keep a raw, numbering-dependent name in the digest.
- Retaining feedback shifts `len(self.seen)` and therefore all later view numbers.
- Minimum fix:
  - Exclude calls whose recorded `read_views` are all feedback kinds from `observed`. This reuses the item 1 dependency capture.
  - Map any such name to a digest-based stable token.
  - Feedback and `current_review_native_digest` never feed progress.
- When `wanted` (the closure from the candidate) is non-empty, feedback reads are already outside it. The fix matters mainly for the unbound-scalar path, where all calls are observed.
- Tests:
  - an identical failing draft plus extra feedback reads gives the same progress set;
  - renumbering views gives the same set;
  - re-running the changed dose query gives a new identity, which is real progress.
- Defer a general "same evidence, differently worded SQL" identity (hashing result content). The trace does not support it.

## 4. Item 3: resolve the actual native result locally

Order of work:
1. Compute the dependency closure over the retained calls from the stored `executed_query` and inputs, including `review_documents` `patient_query_sql`, `field_patient_queries` and `candidate_result_view`. Re-execute only the dose call and its dependents.
2. If a document-review scope was derived from the 19-patient partial set, only the delta patients (10, per the receipt) need review. If scope did not depend on it, no document work is needed. Establish this from the closure, not by assumption.
3. Re-run the dose SQL unbounded. The offline replay already shows 89 rows and 29 patients.
4. Derive the GDMT domain from the registered classes via the existing `drug_classes` mechanism or the `{{ }}` Catalog expansion that `query_sql` already supports. If neither exposes it to SQL, build the key list at plan time from the registry. Record the registry version or digest in the receipt. Do not add a disease-specific handler.
5. Retain the unaffected fill groups, the pillar cells whose dose binding is unchanged, and the source reports.
6. Prose: `four` and `12` must be SQL-computed from the new domain and bound by placeholder. Their correct values may differ after recomputation. Do not keep or patch to 12 or four.
7. If a new template is needed, use one bounded repair turn under the compacted message. Do not rerun the actor. Do not pre-judge that the numeric-binding failure is cured.
8. For the located source faults (4 suspended assertions in 4 families), use the existing `review_documents(candidate_result_view=…, record_ids=…)` on the named records only. The 40 supported and 13 unresolved stay as they are unless the same record or contract changed. Re-verification keys must be assertion, source-record digest and contract.
9. A final partial or `source_not_established` outcome remains acceptable if the located faults are not resolved.
10. Check byte-identity of `document_review.py`, `span_repair.py` and `model_policy.py` before and after. Nothing here requires touching them.

## 5. Item 4: report presentation

**Executed facts from the PDF audit**
- 679 groups: 604 `therapy_status`, 73 `therapy_reason`, 2 `named_clinician_role`.
- Exact-excerpt dedupe gives 486 excerpts.
- Quotation words: 28,496. Raw question text: 19,692.
- By subtraction, about 24.6k words are other content. This assumes the categories are disjoint, which is unmeasured.

**On the narrower correction**
- The measured redundancy is mostly the per-account question text (about 27% of words) plus exact repeated excerpts. Near-duplicate semantic merging is not needed.
- Consuming `therapy_status` and `therapy_reason` by their declared relation names is proportionate. It is two entries in an existing consumer, driven by the contract's relation value, never by parsing prompt text. Unknown relation names continue to fall through to the generic account list and are never dropped.
- `therapy_reason`: present it as "recorded therapy limitation (non-use or dose, as documented)". Do not place it in a missing-pillar reason column. Keep polarity and uncertainty visible, so a negated statement is not read as a limitation.
- Do not join `therapy_reason` to a dose object by drug-name text. Link only through an explicit entity and date binding in the contract.
- Do not present it as an individual maximum tolerated dose or as current clinical applicability.
- Replace the per-account raw prompt with the role label plus the field ID.
  - Show each field contract once (glossary or appendix), keeping the native text unchanged.
  - Within a request, `document_field_contracts` rejects changed meanings for a field ID. So one contract per ID is a code fact, and repeating its text per account is provably lossless.
  - Verify with a count: distinct `(field_id, question text)` pairs equal distinct field IDs.

**Dedupe constraints (do not violate)**
- Merge only when the full presented tuple is identical: patient, relation or field, entity, state or polarity, date, record id, excerpt digest.
- Counterexample 1: one sentence like "lisinopril stopped; carvedilol continued" can back two accounts with different entities and states. Excerpt-level dedupe must keep both bindings under one quote.
- Counterexample 2: an identical copy-forward text in two records or dates is two sources. List both record ids and dates; do not collapse them.
- Do not infer contradictions from repetition or from its absence.

**Measurements before changing the report**
1. Group composition: (relation, field ID) counts, and the 679 → 486 reduction broken down by whether the dropped groups had identical bindings or differed.
2. Words by section. Question text is counted per group versus per field ID.
3. Distinct bindings sharing an excerpt.
4. Whether the 17 dose objects and any new therapy accounts change the page count. Measure after the completeness fix, since the dose section grows.

## 6. Test order

1. Replay: 40 → 89 rows and 19 → 29 patients. The registry yields 17 objects and 13 patients with the exact per-key counts above. The old five-key literal over complete rows yields 13, not 17.
2. Generic non-health cases: more than 2000 rows, and a result with more than 60000 chars. The stored view count equals the true count. `complete=true` and `complete=false` produce identical stored results. The actor preview stays bounded. A financial or event table, not drug-specific.
3. Authorizer matrix (section 2), including count(*) over a view, case, quoting, shadowing CTE, string literal and comment.
4. Laundering: a legacy view with `truncated=True`, then a query over it, then `table_from_tool`, `compose_table` and a prose binding. All are rejected and name the incomplete dependency. A `search_documents` top-k view behaves the same way.
5. A cached `complete=False` receipt is not served as current.
6. Feedback: all 516 entries round-trip with exact text and digest. The 6/510 counts match the existing labels. The message size falls. Feedback is absent from `seen` and `rows_by_record`. Progress identity is invariant to view renumbering and to feedback reads.
7. Then the targeted native checks, app, report and save-reopen, and the exact, changed-window and non-HF reuse runs.

## 7. Must-fix, should-do, optional

**Must-fix**
- Unbounded SQL storage with the serializer check and loud failure on timeout or literal-size limits.
- Result-contract versioning so cached or legacy truncated results cannot be re-served as current.
- Registry-derived GDMT domain (point A).
- Local re-resolution of the dose chain.
- Fixing the `complete=true` error advice.

**Should-do in this increment**
- Authorizer-based dependency recording with acceptance-time rejection through one helper. It still matters for non-SQL and legacy views once SQL truncation is gone.
- Compacted native feedback with the `seen`/`rows_by_record` guard.
- The `therapy_status`/`therapy_reason` consumption with the contract glossary, after the measurements.

**Optional or defer**
- Content-based progress identity.
- Replacing the regex closure in `progress_evidence` with recorded reads.
- Any dedupe beyond identical full tuples.

## 8. Does the plan miss or overcomplicate?

- It does not miss the primary failure, provided the registry-derived domain is added. Without it, completeness alone leaves 13 vs 17.
- It would overcomplicate the work if it added query-time blocking, a general incompleteness lattice, semantic clustering, or a generalised evidence-identity scheme. None of these is needed by the trace.
- The feedback work is justified by cost and context, not shown to cause the repeated binding failures. State it that way in the control checkpoint.
