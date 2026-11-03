# Composition repair and retained-evidence comparison

The original clinical workflow remains unaccepted. The repeated continuation was deliberately interrupted after preserving its completed responses; the build remains active. The described repairs are now selected on the owned candidate as `e7a0d1c`; its backend was started at `4c5832b`, followed only by verified presentation-asset changes. No clinical qualification, model superiority or complete MVP acceptance is claimed.

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

The subsequent measured correction is described below. Earlier receipts retain their exact historical results.


## Complete-packet fallback and saved original answer

The compression hypothesis did not hold: layout metadata accounted for only5,403bytes of a316,712byte parent. Exact dictionary pruning saved about14KB and still could not fit. Neither experiment was promoted. Inspection instead found that the oversized route forced Jev alone, excluding the configured fallback. [Current Perplexity documentation](https://docs.perplexity.ai/docs/decisions/quickstart) permits under256Kinputtokens and32MiB requests. It also states that the configured v1.1name now routes to v1.2 while responses echo the requested alias. These observations do not establish independent model qualification; the alias caveat is recorded in both live receipts.

`4c5832b` preserves the task's configured fallback for complete large packets, unchanged source text, existing thresholds, provider limit handling and failed-key protections. The largest unresolved packet cleared through Perplexity in9.432seconds/$0.00352194; the other nine took51.757seconds/$0.02647622. These are two targeted review measurements, not end-to-end question latency. Exactly10newPerplexityattempts validated, no newJev orClaude calls; oldJev results were reused. Five additional packets cleared the0.8bar. Five remained below it (0.77005–0.79955); no threshold was lowered.

A normal final review with gateway dispatch disabled reassembled all exact receipts in25.764seconds, with0newattempts. All50previously supported packet verdicts and native calls stayed exact. The result is55supported and6unresolved scheduled packets, including the separate below-threshold rationale field; coverage remains undecided. No source fault or concrete omission was established by those uncertain votes.

The original question now has saved current context `ANS-39a99a79fc18fc9a250d80d8` on owned8890. The API preserves all57patients,114proposed explanation/review fields and all93older saved answers; current policy is valid and older-policy answers remain inspectable without gaining new action authority. The103.501second save/HTTP proof includes preservation reads and two API inspections; it is not a one-question or one-page latency measurement. No inference or export occurred.

Browser verification then exposed IDs-only cohort labels and expanded JSON-string receipts ahead of the explanation. `e7a0d1c` uses the existing source-bound profile and declared interpreted-column contracts: names beside IDs, proposed interpretations first, native evidence retained below, complete structured records expandable. Partial/unverified status stays visible. The real browser showed the57patient cohort and Patricia Moore's explanation, next review and preserved records. No disease-specific handler or new clinical assertion was added.62selected backend checks and JavaScript syntax verification pass.

All live checks are terminal. This increment adds$0.02999816knownAPI-equivalent usage; actual compute/review costs and preceding attempts remain outside that subtotal. All source and original attempt records remain preserved. No new model weights or clinical final-answer bottle was promoted.

Next: use the existing release scorecard to assess current applicability and guarded reuse; preserve the six unresolved checks and undecided coverage as explicit dependencies. The change inventory identifies20backend modules and2presentation assets since the prior finite engineering decision. Do not rerun supported checks or rewrite the entire answer merely to obtain a stronger verifier vote. FullMVP/independent clinical/model qualification remain false; alltencharterareas remain in scope.

## Current release decision and actual execution reuse — 2026-11-03T19:43:36.988190+00:00

The finite local engineering decision is current at `e7a0d1c`: **nine requirements pass across all ten acceptance areas**. All six original saved workflows reopen through the normal current HTTP service with exact answers, decisions, work and follow-up. Source interpretation was not repeated. This is a functional applicability decision; it does not close the clinical performance or independent qualification gaps.

The latest preceding finite release was `a32a2c5`, which already contained an executed installation/restore proof. The inventory compared 22 changed files against `aec778d`; 15 differ from that newer finite release. The current decision explicitly maps the changes to sealed workflow evidence. Installation/recovery mechanisms are unchanged; no new current installation drill is claimed.

Two ordinary HTTP requests for the same provider-claims question returned all **795 exact patients, claim counts and integer amounts**. The first correctly declined the older execution candidate after code/policy changes and qualified a current seven-action candidate. The second selected that candidate automatically, reexecuted each native action, retained final verification and used **zero Claude callbacks**.

| Current numeric observation | Qualification request | Ordinary repeat |
| --- | ---: | ---: |
| Wall time | 128.053 seconds | 6.733 seconds |
| New Claude subscription calls | 10 | 0 |
| New Jev / Perplexity calls | 2 / 1 | 2 / 1 |
| Known API-equivalent cost | $1.809232 | $0.004186 |
| Exact patient rows | 795 | 795 |

This one same-question pair observed a 94.7% wall-time reduction and 99.8% API-equivalent reduction. It is exact execution reuse, with warm caches, not a cold comparison, changed-question transfer, clinical accuracy gain or all-in cost claim. The 21 selected reuse/feedback/rollback tests pass. No new weights or unverified clinical answer were promoted. All 16 new attempts and earlier attempts remain accounted for.

The original 57-patient clinical context now has six internal evidence-review items attached to its six unresolved packets. Idempotent creation and persisted blocked-review state passed; the other items remained exact. The app rejected PharmD program work from the incomplete answer. A saved-cohort follow-up counted all57 while retaining partial status. All96 preceding saved answers remained byte exact; no provider call or export was needed for this follow-up check.

Verification setup failures are retained. The older temporary six-case runtime lacked its semantic file; an isolated sealed checkpoint supplied the reference runtime. Its declared internal links needed relocation, and a standalone diagnostic omitted service registration for reference contexts. The corrected check used the normal service, where that registration already exists; all six passed in223.942seconds. This did not require a product patch. Thirty-one absent evidence files were restored from sealed copies only after exact hash checks; the cause of their prior absence is unknown.

The checkpoint semantic file has a different raw hash and timestamp. An exhaustive byte comparison found differences only at offsets27,43,95—the [SQLite header revision fields](https://www.sqlite.org/fileformat.html). SQL schema rows and all source/schema payload bytes match; full raw hashes and all differences are retained. This is offline evidence applicability, not a relaxation of runtime identity guards.

The clinical answer remains **partial/unverified:55of61 source packets supported, six unresolved, coverage undecided**. Engineering progress has not established clinical accuracy, superiority, useful prediction-model qualification or customer deployment readiness. The remaining engineering focus is repeated clinical planning/source work and a fair simpler-route cost/latency comparison. The canonical control file contains the exact next action.

Evidence: `artifacts/agere-current-release-closure-2026-11-03/delivery-receipt.json`.

## Research highlight requested by Mike — November 3

Preserve the ordinary seven-action numeric execution reuse pair as an important candidate case study. Mike supplied `/Users/mikel/Desktop/bottling.pdf`; its title, authors, version and relevant text match [Sonthalia et al., *Agent in a Bottle*, arXiv:2610.08775v1](https://arxiv.org/abs/2610.08775v1). The [study brief](WOLLSTONECRAFT_BOTTLING_STUDY_BRIEF_2026-11-03.md) links the paper's amortization idea to the measured procedure, preserves the one-pair scope and all-attempt accounting, and specifies the comparison needed for stronger claims. A requested visual quotes the paper and labels our measurements separately. The image and its provenance are at `artifacts/wollstonecraft-bottling-study-2026-11-03/`; [generation record](WOLLSTONECRAFT_BOTTLING_VISUAL_PROMPT_2026-11-03.md).

This is documentation and research communication, with no additional product result, provider evaluation, weight change or clinical promotion. The last sealed delivery, G3 next action and all ten acceptance areas remain unchanged. Future study work is a proposal, not a newly dispatched campaign.

## User-requested harder bottling test — failed qualification

Mike then requested a harder question. One ordinary 8890 request combined payer medical and pharmacy feeds over 2028–2029, with paid/nonduplicate/reversal handling, patient joins, year totals, change, percentages and ranking. Independent Python and prewritten SQL agree on 1,920 rows and 26,880 cells; 484 prior-year totals are zero. No plan, expected result or selected patient set was injected into the application.

The run was stopped after 723.537 seconds following model-verification expansion and repeated provider throughput failures. All 41,923 preceding attempt rows and the source/product identities remained exact. There are 4,655 new attempts: 16 Claude validated; 2,319 Jev validated and five pending; 1,708 Perplexity validated, 604 failed and three pending. The failures are 603 HTTP 429 and one HTTP 503. Known API-equivalent usage is $1.631045282 with 612 unpriced attempts; actual total cost is unknown. This is a failed/interrupted first run, with no qualified repeat or new bottle selection. The app was restored with unchanged code at owned8890PID47807/session12518; GET /ask returned200. Primary8885 was not modified.

Two observed failures require shared-path work. First, `native_numeric_aggregate_shape` rejects this conditional multi-year/ratio SQL, leaving numeric cells to expensive packet/field model checks. Second, the investigator added a same-patient/NDC/date exclusion without an exact claim relationship. The source audit found eight matched paid records and zero exact reversal links or equal claim IDs. Reexecuting the retained candidate query offline preserves the same 1,920-patient set but yields ten cell disagreements across six patients against the source-contract baseline. No independently verified final answer was produced. Candidate validation and selection events after the prior sealed pair remain zero.

Evidence: `artifacts/agere-harder-bottling-2026-11-03/run-protocol.json`, `qualification/run-receipt.json`, `native-baseline-receipt.json`, `reversal-link-audit.json`, `offline-diagnosis.json`, `learning-state-check.json` and `interruption-decision.json`. Partial SQL/results and every provider attempt are retained. Two initial offline diagnostic snippets had indentation/syntax errors; the corrected `diagnose.py` and successful receipt are retained. These diagnostic failures are separate from the product defects and provider attempts.

Next: repair and qualify the shared numerical verification and relation-binding path offline against this retained case, preserving global semantic review, unknowns and rejection of integer-encoded clinical classifications. Diagnose the provider rate-error expansion in the same workflow. Run only the affected ordinary confirmation after those checks pass and provider throughput is available. Preserve the original57-patient clinical work,55/61supported packets,six unresolved dependencies, all ten areas and the prior clinical routing next action. No product code was changed during this test or its frozen interval, and no new release is claimed.


## Alternative financial bottling check requested by Mike — November 3

Mike clarified that bottling should reuse reasoning already performed by an LLM. A separate completed task fits: “For patients outside the registered HFrEF cohort, show the number of patients and received claim adjudication rows, total net payer-paid claim dollars by service year, and payer cash transactions and dollars separately by payment year. Keep the two financial lifecycles distinct.” Its original request also asked for a saved review; this offline check created no new end-user deliverable.

The historical LLM-derived program has22native operations corresponding to15actorcallbacks. On current source/code it reexecuted in8.546seconds; setup, reexecution and independent Python reconciliation took9.890seconds. All22semantic receipts and all12financial result rows matched. Fresh source grouping independently checked cents, row counts and per-year patients. There were0LLMcalls,0newproviderattempts and all46578ledgerrows remained byte-equivalent in the row digest. All learning events, source and product code remained unchanged.

This is a concrete executable artifact reuse example, not merely a cached answer. It is not current ordinary routing acceptance: the current identity guard finds no eligible selected configuration. No guard was bypassed and no candidate was promoted. The earlier November2ordinary reuse took31.779seconds, with0actorcalls and2Jevcalls; that historical observation does not qualify the current release or establish a matched cost reduction. Current ordinary requalification remains required before automatic activation.

Evidence: `artifacts/agere-financial-bottle-current-2026-11-03/receipt.json`, `action-checks.json`, `independent-reconciliation.json`, `native-calls.json` and `check.py`. The harder question failure and its repair priority remain unchanged. No weights were trained; this bottling form compiles prior model work into executable database operations.


## Rich clinical replay experiment requested for a post — November 3

The user requested a more compelling bottling use case. The original57patient HFrEF medication review provides one: cohort, medication pillars/doses, source-bound note findings, dated care-team evidence and patient-specific review prose. The existing explicit owned-recovery mechanism reexecuted28operations in34.855seconds; the process including checks took37.023seconds, with1.564seconds of subsequent reconciliation. No model/provider calls occurred, and all46578shared ledger rows, learning events, source and product code remained unchanged. All57final patient rows reproduce exactly.

Two strict-check failures are retained. The initial28-action extraction omitted the original recovery boundary, whose operational feedback occupied result23. After22matching actions, later result references shifted and reconstruction stopped. Restoring the authentic feedback through existing execution_state.recovery_context reexecuted all28actions; every row matched, but four legacy SQL receipts lacked current source_contracts. The reconciliation requires every other compared field, query, input and cell to match and verifies the added contracts against the same source digest/date. Nothing treats operational feedback as patient evidence, bypasses a production guard, or changes a source result.

The result is explicit component replay with retained source observations and interpretations, not current automatic ordinary activation, a fresh clinical investigation, a matched whole-request speedup or new model weights. The final clinical answer remains partial/unverified. Its prior source-review state includes54supported and6unresolved non-summary packets plus the supported global summary; the earlier55/61count includes that summary. No source-support judgment was rerun or upgraded.

Evidence: `artifacts/agere-clinical-bottling-usecase-2026-11-03/qualification.json`, both failed attempt receipts, current-action-reconciliation.json, reconstructed-answer.json and the saved replay/reconciliation programs. LinkedIn copy and a visual use34.9s as native replay execution only, with explicit same-snapshot scope and six unresolved checks preserved. No comparative percentage, clinical superiority or full-MVP claim is made. No post was published. Harder payer-analysis repair and current clinical ordinary-routing qualification remain open.

Mike subsequently requested retaining the before/after cost/time presentation. The draft keeps the prior795patient paired benchmark separate from this57patient clinical replay. A historical329.914s/$1.842899warm clinical continuation was inspected but its outputs and measured stages differ, so it is not used as a matched baseline. The fuller3459.554sclinical run likewise cannot be compared directly with native replay. A matched clinical first/reuse benchmark remains open. Durable post/visual/evidence archive: `/Users/mikel/clinical-insights-local/artifacts/wollstonecraft-clinical-bottling-2026-11-03`. Built-in image generation; exact prompt preserved.
