# Claude review: native verification feedback and service context

Exact subscription review of6c83a8a, followed by owner corrections and evidence.

# Review of commit 6c83a8a: native source-review feedback

Nothing was executed; this is from the diff, the supplied files and the proof output only.

## Introduced faults and gaps

1. **Repair prose is hidden for every packet-bearing review, not only oversized ones.** In native mode `feedback` is removed from `current_review_feedback`, so the actor no longer sees the reviewer's instruction text. It sees only `status`, `reason`, `coverage`, counts and a pointer. Nothing forces a query before the actor finalizes, and the scripted test makes the query by construction. A real actor can now finish `partial` or `confirmed` without reading the repair text, and that text may carry scope constraints. Also, `reason` is not guaranteed to hold the actionable content. There is no size threshold, so small reviews lose their inline guidance too. Suggested direction: keep a bounded exact prefix, or inline whenever the size is under budget. Hiding the text only above a budget would still preserve the 5.3 MB fix.

2. **Non-native callers change behavior despite the stated claim.** The non-native `fields` tuple now includes `patient_id`, which was not in the old `('id','label','reason','claim_feedback')`. `VERSION` also moves to `execution-state/5` for all callers, which may affect cache keys, digests or fixtures that compare the version. The test only checks the compiler-repair case, which has no packets, so it does not detect this.

3. **The real query path is untested.**
   - `NativeTools.__call__` runs raw sqlite with a stubbed `_authorize`. It never goes through `SemanticWarehouse.query` with its authorizer, function allowlist, `max_rows`/`max_chars=60000`, or incomplete-dependency handling.
   - The instruction tells the actor to use `json_each`. Whether the real authorizer permits table-valued functions is not shown. Recovery feedback only needed `json_extract`.
   - The row is one cell of up to about 5 MB. Selecting the cell itself would hit the character cap. Only extracted paths are practical, and the instruction does not say so.
   - The "152 regressions" and the scripted loop therefore do not demonstrate that the 1466 entries are queryable through the production tool. The proof says "queryable", but it covers representation only, as its own scope note states.

4. **The error paths abort the loop.** `review_reference` and `messages` raise `ValueError` for a changed view or a digest mismatch, and `review_reference` is called inside the actor-message build. If these are not caught upstream, the whole request is lost along with its partial answer. Also, `json.dumps(..., allow_nan=False)` on the full feedback can raise on a NaN, where the old subset projection might not have.

5. **Memory and CPU cost per turn.**
   - Every distinct review digest adds a view of about 5 MB, plus the `self.seen` copy and the `executed_results` copy.
   - `digest(feedback)` and `json.dumps(feedback)` run on every actor turn while the feedback is unchanged, and `digest` runs twice.
   - Growth is unbounded across repair rounds.

6. **Oversize remains on the non-packet path.** Feedback without `support.packets` still inlines a large `feedback` string. This is probably out of scope, but "fixes Prompt is too long" holds only for packet-bearing reviews.

## Hypotheses to verify

- **Reviewer feedback may become evidence.** `retain_result` appends the feedback JSON to `self.seen` and registers it in `executed_results`. If `seen`, `executed_results` or `rows_by_record` feed quote or ID grounding checks, `table_from_tool` or `compose_table`, then reviewer-generated quotes and record IDs could ground a final answer. The instruction text says "not patient evidence", but nothing enforces it in code. The same pattern exists for recovery feedback, so this is probably not newly introduced; check whether any consumer filters on `kind`.
- **View numbering and replay.** `agere_result_{len(self.seen)+1}` means the feedback view consumes a sequence number that is not a recorded tool call. A replay or recovery that re-executes recorded calls could assign different view names than the SQL recorded against them. `progress_evidence` already normalizes names, which suggests the issue is known. Check owned-run recovery.
- **Non-JSON values.** Rows use `json.dumps(default=str)` while `digest` may serialize differently. Non-JSON values would be stringified, so "exact" holds only for JSON-native receipts.
- **Wrapped executors.** If the executor in some caller is wrapped and loses `.semantic` or `.retain_result`, the code silently falls back to the 5 MB inline path, with no warning.

## Not found

- No change to clinical thresholds or truth-by-vote.
- Unresolved packet IDs, labels, patient IDs and entry counts stay visible.
- The original feedback is not mutated, and digest binding and idempotent reuse are checked.

The "supported" packets are hidden from the visible list. Their claim entries stay queryable, but the counts do not say whether any of them are non-supported.


## Owner assessment and follow-through

The review executed no tests and did not adjudicate clinical facts. Its production-query coverage gap and instruction-visibility concern were valid. The hardened candidate retains every prose instruction and references only parseable embedded JSON when the reference is shorter. Exact character offsets allow full reconstruction; the actual2.602MB guidance roundtrips byte-for-byte through production SQL. There is no prefix, word limit or input/output ceiling. Small prose stays verbatim.

The actual66packet/1466claim receipt is now queried through real Tools/SemanticWarehouse authorization in1.087s with0providers. All27090shared attempt rows remain unchanged. The same actual projected state is5318303bytes inline versus79655bytes with native feedback; system/request scaffolding is excluded identically. This is representation/query evidence, not a completed live repair or model quality result.155connected regressions pass, including scripted continuation and actual production authorization.

The citation-grounding hypothesis was addressed with an executable regression: operational feedback has its own digest-named namespace, does not consume clinical result numbers, and does not populate the record-quote index. Its unverified-authority dependency propagates through existing native SQL; derived quotations cannot ground citations or final tables. The model can inspect feedback through the existing inspection tool. No source threshold or clinical authority is relaxed.

The non-native pending-dependency projection is restored exactly. The execution-state version increase intentionally identifies the changed actor protocol; the three frozen source reader/model-policy cache producer files remain identical to fa28466. Error handling already catches representation/provider errors and retains the last partial draft, as the original ordinary failure demonstrated; the review's lost-answer concern is not an observed result. Strict JSON-native feedback remains required.

Digest/serialization work and retained review memory remain proportional to actual feedback and are not claimed free or bounded by an assistant-selected cap. The measured correction covers packet-bearing source feedback; arbitrary oversized unstructured instructions remain outside this proof. No new source campaign, model weights, full-question bottle, primary promotion or complete-MVP/clinical-quality claim is made. The hardened follow-through was owner-tested; it was not independently rereviewed.


## Actual owned-recovery follow-through

The first live continuation returned no candidate after39.072s/2newattempts. The initial actor request still failed PromptTooLong: the saved server answer carried5.570MB of verifier-derived limitation strings into recovery's last draft. The earlier source-feedback projection was not the failing body in this distinct caller. The original failed receipt and attempts remain unchanged.

The same exact-prose/embedded-JSON reference contract now projects those limitations at their original native JSON paths. The actual18.964MB retained feedback projects to15.182KB before the native tool catalog; every limitation and all4embedded JSON segments roundtrip exactly through production SQL in0.670s with0providers. Unfinished recovery feedback also propagates the same unverified authority, so it cannot become accepted source evidence.156connected regressions pass. This corrects the measured recovery representation; live completion remains separate. No prefix, word cap, source-row removal or clinical threshold change.
