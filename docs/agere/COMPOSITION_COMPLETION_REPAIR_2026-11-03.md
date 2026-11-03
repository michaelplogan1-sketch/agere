# Composition repair and retained-evidence comparison

The original clinical workflow remains unaccepted. The repeated continuation was deliberately interrupted after preserving its completed responses; the build remains active. These changes are candidates; the last sealed delivery remains product `942ff1f`. No clinical qualification, model superiority or complete MVP acceptance is claimed.

## What the comparison showed

Both attempts used the original 57-patient question, the same 22 retained native actions, the same starting provider cache and the same final checks. They continued existing source readings; they were not fresh warehouse investigations or full Claude-only benchmarks.

| Starting investigator | Elapsed | New attempts | Known API-equivalent cost | Result |
| --- | ---: | ---: | ---: | --- |
| Haiku, then Sonnet | 543.816 s | 154: 13 Claude, 77 Jev, 64 Perplexity | $2.120321 | Partial. All 57 explanation cells were numbers because of generated SQL operator precedence. |
| Sonnet | 329.914 s | 7 Claude | $1.842899 | Partial. Draft explanations existed for all 57 patients, but compilation stopped before source review. |

The shorter run did not meet the same completed-output obligation, so it is not an accepted-answer performance improvement. Actual subscription allocation, local computation and human review are outside these API-equivalent subtotals. Every attempt and partial answer is retained; source, earlier answers and earlier attempt records remained unchanged.

## Reproduced defects and changes

Product `ba84e0c` distinguishes confidently incomplete answer coverage from uncertain source verification. Known unfinished composition can return for repair before patient support checks; a complete draft still needs source review. The actual continuation escalated to Sonnet after repeated compiler errors, but did not take the new confident-incomplete-coverage branch. Its failure does not prove that branch improved live performance.

Product `a747002` repairs two additional problems in the existing compiler and investigator state:

- The complete native patient result had 57 rows, while its model preview had zero because the first row exceeded the preview envelope. `query_sql(read_all_rows=true)` now explicitly supplies all selected rows and retains that exact reading across subsequent turns. Default previews and full native storage remain available. The investigator can select the fields needed for interpretation without discarding the original result.
- Row-prose validation stopped at the first invalid field. Later rows could therefore reveal the same error category and trigger a premature no-progress stop. Validation now reports all field errors together and commits no prose unless the entire batch compiles. Existing entity, quantity, source and final-review requirements remain enforced.

## Verification

142 targeted tests passed. After adding an early guard to avoid hashing default previews, the 49 affected reading/state checks passed again; those are overlapping tests, not 191 unique checks.

An offline replay of the actual drafts found 19 invalid fields in a draft whose previous review reported only the first. The final retained draft still has one invalid field and remains unaccepted. An actual native reading exposed all 57 rows and preserved them through a later investigation turn. The SQL view serialized 44 existing collection cells as JSON strings; exact decoded equality was checked, and SQL-result-to-model values were exact. No provider calls were made by this proof, and no source or prior answer was changed.

The first proof attempt incorrectly required list values and their SQL JSON-string representation to be byte-identical. Its failed log is retained. The corrected proof explicitly accounts for those 44 serialization differences; it does not normalize other changes.

Evidence is under `artifacts/agere-completion-routing-2026-11-03/`: `route-comparison.json`, `clinical-owner-review.json`, `actor-evidence-visibility.json`, `composition-boundary-proof.json`, the test logs, and each run's isolated receipt, response and provider ledger.

## Repeated verification and controlled interruption

The repaired continuation produced complete individual prose for all 57 patients. Twelve complete JSON drafts were retained, of which six passed the compiler. It still did not finish: every completed pair of coverage votes remained undecided. Jev's selected probabilities ranged from 0.63 to 0.70; the fallback did not settle them. All six compiled drafts retained the same native dependency identity. The prior review path nevertheless treated the undecided partial label as a missing-work finding, requested another rewrite, and excluded it from stable uncertainty handling.

The owner interrupted only this experiment, at a boundary with no pending Claude generation. Its terminal receipt records 1,638.091 seconds and 392 new attempts: 17 validated Claude, 210 validated Jev, 164 validated Perplexity and one pending Perplexity. Known API-equivalent cost is $10.217555 plus the unpriced pending attempt. That pending outcome remains unknown; it has not been rewritten as free, failed or completed. All frozen code, source, original cache attempts, shared ledger rows, prior saved answers and prior exports remained exact. No complete response or accepted answer was produced by this interrupted run.

`composition-boundary-continuation/` retains the interruption decision, terminal receipt, every raw response and attempt, and a separate ledger snapshot. The build and quiet continuation automation remain active. No spending, time, patient or output cap was introduced.

Product `430c92c` makes coverage decision authority explicit in both directions. An undecided vote cannot reject a proposal or confirm complete coverage. Stable uncertainty can stop repeated rewriting without hiding a located source fault or known missing work. A source-verifier outage does not request another rewrite merely because coverage is also unavailable. Thresholds and source checks are unchanged.

The offline coverage proof replays the actual six vote pairs through the existing decision rule and compares the six actual compiled drafts' native dependencies. Its source-uncertainty fixture is explicitly authored; it does **not** reconstruct the interrupted run's complete clinical verdict. A separate cache-only reconstruction encountered unmatched source request identities. Those checks remain unavailable; no new provider calls or acceptance were inferred from cache misses.

## Localized prose repair candidate

The existing draft state and tool loop now accept a patch bound to the exact current draft digest. A patch replaces existing prose fields; it cannot remove patients, overwrite native columns, invent entity keys or change source bases. Every merged candidate passes the complete compiler and ordinary review. Stale, empty, malformed or repeated invalid patches remain rejected, and partial answers survive. Current repair counts distinguish actually changed fields from unchanged fields submitted again.

An offline replay applied the nine actual row-field changes between drafts 42303 and 42304. It removed eight compiler failures while preserving the other 105 prose fields, all 57 native rows, sources and bases. The patch was 4,012 bytes versus 36,312 bytes for the full target draft. Unrelated changes to top-level prose were deliberately excluded. This is an output-size and compiler proof, not a measured full-workflow speed or clinical-quality improvement.

The focused live probe has **not** qualified this repair path:

- The first attempt failed after 204.648 seconds because DNS was unavailable inside the restricted execution environment. A paired check resolved the provider hostname outside that environment. The original failed ledger entry was preserved and an explicit retry authorization recorded.
- The identical subscription request outside the sandbox returned in 33.055 seconds, with $1.163096 reported API-equivalent usage and 4,184 aggregate output tokens. Its input was roughly 594 KB and 280,315 reported prompt tokens including cache creation. Input selection remains inefficient.
- The model returned a small patch, but added unsupported `basis_extra` fields for new scalar bindings. The merge rejected it before mutation. Several original numerical details were also removed. No automatic schema relaxation, clinical acceptance or promotion follows from this output.

The final selected suite ran 204 tests: 203 passed and one optional live test was skipped. It covers exact-draft edits, native/entity/quantity guards, full review after merging, stale/no-op/repeated failures, uncertainty routing and existing source/state behavior. The first test run's missing import was corrected; no failing result was relabeled a pass.

## Targeted repair input and source-binding check

Candidate `49a07be` carries machine-readable compiler targets through the existing review and execution-state path. It identifies all eight failed fields in actual draft 42303. Only a receipt matching the current draft, keys, field values and bases permits the focused view; mixed or global failures retain the full state. Every native result remains queryable and all 57 patients remain in the final result. Explicit reading subsets are labeled as repair readings, never as a complete population.

The localized patch can append an executed, complete, same-entity basis row. It cannot replace prior bases, native values or entity keys. A binding repair must preserve the original sentence, quantities, units, dates and uncertainty; numeric spelling and equivalent formatting may change. Invalid patches retain these guards on later attempts. Other semantic changes still use the full answer contract and complete review.

On the same actual question, calls, candidate and compiler feedback, serialized message size decreased from **226,673 to 128,059 bytes (43.50%)**. This is the product execution-state comparison. The earlier 594 KB custom probe supplied additional redundant scalar data and is not a valid product baseline. The recorded old detail-dropping proposal remains rejected.

The first focused tool-loop run completed in **67.020 seconds**, using four Claude subscription calls and two native SQL queries, with **$1.096555 known API-equivalent usage**. All 57 native rows, 106 unchanged prose fields, original sources, code, warehouse identity and shared attempt records remained exact. Complete requests, responses, pre-review answers, native calls and review receipts are preserved under `focused-prose-live-v2/`.

That run passed syntactic binding checks but **failed the owner source-binding audit**: it copied the draft numbers into SQL constants and then added literal patient IDs. This does not establish source support. Its original compiler-pass receipt is retained without being relabeled a clinical pass. Candidate `ee4f9b7` rejects the demonstrated source-free SQL path in the existing entity-prose compiler and clarifies the existing prompt. Exact offline replay rejects all eight copied bindings. This narrow guard is not a claim of complete SQL column-lineage verification; source meaning still requires the existing review.

The selected suite now runs **183 tests: 182 passed and one optional live skip**. Two preparation failures (an incorrect harness import and an incorrect shared-ledger path) made zero provider calls and are retained.

The corrected `ee4f9b7` retest completed in **69.643 seconds**, with four Claude subscription calls, two native queries and **$1.1505657 API-equivalent usage**. It extracted measurements from retained documentary observations and medication rows, and computed the remaining-pillar count from native status columns. The first extraction had substring-offset errors; the preservation guard rejected it and the second query corrected them. All eight fields then compiled with eight explicit same-patient basis additions. The 57 native patient rows and 106 untouched prose fields remained exact. The final input comparison is **226,673 versus 128,174 bytes (43.45% less)** after adding source-binding guidance. This is a targeted repair measurement, not an accepted whole-clinical latency benchmark.

`focused-prose-live-v3/` retains the entire successful repair trace. The subsequent normal final review in `focused-draft-source-review/` completed in **81.100 seconds**, with **63 Jev and 53 Perplexity validated attempts**, **$0.267981572 API-equivalent usage**, and zero new Claude calls. All original cache attempts, shared attempts, code and warehouse identity remained exact. Full final-review inputs, source contexts, packet text and attempts are preserved.

All dependencies were checked. Of the 61 scheduled dependency packets (58 logical packets, with one split into source dependencies), **50 were supported by the configured verifier and 11 remain unresolved**. These are packet counts, not independent clinical accuracy scores or patient counts. Coverage was undecided (Jev's partial vote 0.64 with no deciding provider). The final review correctly returned `unresolved_verification`; it did not establish a concrete missing output or source fault. No additional frontier rewrite was launched.

Offline diagnosis identifies the remaining routing failure precisely: ten unresolved packets still exceed the partition envelope after source separation because their non-source parent remains large. Their full original packets range from 247,963 to 376,099 bytes. The existing partitioner preserves all content but cannot construct a smaller parent, so those packets take the Jev-only long-context path and remain undecided. One other packet partitions successfully; its remaining rationale field is below the configured 0.8 acceptance threshold (0.761976), which is retained without threshold relaxation. `unresolved-partition-diagnosis.json` and `parent-size-diagnosis.json` retain the evidence. For the inspected parent, native `evidence` is the largest remaining field; exact dependency selection or deduplication must be justified before changing transport.

All live jobs are terminal. No current candidate is activated on the owned server. The two focused runs and final review together produced 124 new validated attempts and **$2.515102272 known API-equivalent usage**; actual subscription allocation and local costs remain outside that subtotal. Earlier failed and interrupted attempts remain separately accounted, not discarded.

Next: inspect and repair the ten oversized non-source parent dependencies in the existing projection/partition path, using exact retained packets and complete reconstruction checks before any changed provider check. Preserve the 50 supported packets and the below-threshold field as distinct observations; do not rerun passing checks, truncate sources, weaken thresholds or rewrite the whole answer. Original-question product completion, guarded reuse and current ten-area release applicability remain open. `AGERE_BUILD_CONTROL.json` owns the exact next action and process state; all ten acceptance areas remain in scope.
