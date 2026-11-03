# Composition repair and retained-evidence comparison

The original clinical workflow is still under verification. These changes are candidates; the last sealed delivery remains product `942ff1f`. No clinical qualification, model superiority or complete MVP acceptance is claimed.

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

One repaired continuation is running from the retained evidence at `composition-boundary-continuation/run-receipt.json`. Its investigator starts on Sonnet for this probe; no default model route or learned clinical configuration has been promoted. The canonical next action and process ownership are in `AGERE_BUILD_CONTROL.json`. All ten acceptance areas remain in scope.
