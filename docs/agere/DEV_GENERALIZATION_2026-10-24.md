# Dev generalization repair, October 24

Release a7f2c9b is pushed to main and the synthetic service on :8885 restarted; /health reports ready. The target of >95% remains unmet. No current held-out evaluation was started. A later inspection incident is recorded below; current held-out bank and generator remain unopened.

Repairs preserve polarity, conjunctions, ratio denominators, rolling claim dates, event-specific note reasons and safe plan-cache revision. Completed discovery fields are cached even if another field times out. The chart adapter retains negative boolean results as derived rows so the answer guard can inspect them. Explicit reported historical event counts retain the dated note scope; stated zero counts remain zero.

## Measurements

All free measurements use frozen6424730 versus19262be, cold evaluation stacks, no Claude or local model tiers.

| Set | Before | After |
|---|---:|---:|
| Original dev |83/100|84/100|
| Dev paraphrases |214/295|217/295|
| Composition dev |15/130|26/130|

The paid100-question dev run used frozen6424730 and the real conversation handler, with Haiku first and normal escalation available. Under the shipped scorer it scored87 correct,7 wrong,6 refusals:87% accuracy and92.6% precision among nonrefusals. Recorded provider usage estimate: $0.192139175;45 Haiku calls plus hosted System One calls, no Sonnet. The older frozen scorer gave86%; raw responses were rescored without new API calls. This is not a measurement of the later shipped routing/notes fixes.

| Bucket | Correct |
|---|---:|
| Structured patient |19/20|
| Notes |18/20|
| Population quality |18/20|
| Claims/pharmacy/social factors |15/20|
| Insight |17/20|

Additional diagnostics:6 Haiku calls $0.02617635; interrupted mutable-version dev run $0.043482654, not a full accuracy measurement; source-note probe $0.00023557. Usage estimates are not invoice totals. Failed calls have no reported token usage.

## Release evidence

`tools/runtests_diff.py`: combined candidate113 modules/9 failing tests versus current-main f351e7a111 modules/10 failing tests; no new failure names. Reported-count increment4 modules/0 versus2 modules/0. Existing failures were retained, not weakened. Focused reported-count/notes/boolean tests35 passed. Live readiness verified after restart.

Raw receipts, failures and responses: `/tmp/agere_generalization/`. Paid failed-dev chains are being rerun on the shipped release with a separate ledger; report them as targeted diagnostics, never full accuracy. Remaining gaps include conversational antecedents, multi-part claims calculations and broad composition coverage. Dev-key semantic disagreements remain wrong in scoring; no keys were changed. G3 remains active, G5 incomplete and full acceptance false.

## Follow-up delivery and evaluation hygiene

fd5d95b preserves a transformed summary’s exact original input membership when its engine contract omits the population field, including an explicit empty input. Conflicting contract/transform inputs are rejected. Candidate and main each passed25 regression modules with0 failing tests;9 focused summary tests passed. Pushed and local service restarted. This repairs provenance preservation; it does not establish that every conversational reference resolves correctly.

A subsequent inspection of the first100 lines of `tools/dev_oracle.py` accidentally displayed a legacy block labelled HELDOUT. The inspection stopped and the user was informed. Those displayed legacy questions must not be claimed unseen or used for tuning. No subsequent oracle inspection or tuning was performed; the current dev-set held-out question bank, reserved evaluation directory, private data and generator remain unopened. All reported measurements above preceded this exposure.

The isolated committed composition candidate c26073f scored77/130 correct,21 wrong,32 refusals against26/130,8 wrong,96 refusals before; no previously correct answer regressed. It was not landed. Its regression diff found an unconsumed historical wording regression, repaired in the newer isolated candidate. Direct source-SQL audit confirms dev c3-31’s key counts556 visit rows rather than201 distinct patients; Agere’s201 is retained as a scored failure pending independent key adjudication. No key changed.

## Combined release ce310b5

Pushed to main and restarted :8885; readiness reports ready/synthetic. Combines the committed composition and shared acceptance work with independently reproduced repairs. Broad frozen09b239f regression:105 candidate modules/7 failures versus102 current-main modules/9 failures, no new failure names. Final attribution/acceptance delta:17 modules/1 existing failure on both sides, no new failure names. Existing failures remain visible.64 focused final-scope tests passed.

The acceptance candidate initially rejected15 previously correct grouped answers. It was not landed in that state. After fixing grouped shape, sex_reported and bounded multi-year intervals, all15 passed. A further parenthetical inclusion case was caught by the full130-question run and corrected: an inclusive population definition does not request separate groups. An explicitly undecided chart-note answer must proceed to another layer.

Live product smoke: public dev b4-20 (pharmacy rejection share plus top code) and b4-27 (SVI group ED rates) each match their key through the API, both with cost receipts of0 new calls/$0. One-question note diagnostic b2-04 matched under the shipped scorer at recorded estimate$0.000564436. Its fresh reader settled the evidence; this stochastic replay alone does not prove the new escalation gate caused the recovery.

Completed targeted paid diagnostic on the earlier shipped release:19 questions including6 parent turns,10 correct/6 wrong/3 unresolved. Of13 previously failing questions,4 recovered. Recorded estimate$1.436317484; Haiku first, then Sonnet escalation. This is not full-set accuracy.

Frozen09b239f full free composition run:91/130 correct,19 wrong,20 refusals. The later inclusion and practice-scope corrections are not part of that full score. The earlier composition-only candidate scored92/130. Attribution follows existing PCP-then-payer fallback for unqualified practice questions; explicit primary-care practice questions use the recorded PCP practice. A new full100-question paid dev run on frozen09b239f remains in progress, with separate ledger `final-09b239f-dev-provider-budget.sqlite3`; report its actual version and spend at completion. No further held-out evaluation has been run. Target>95% remains unmet.


## Context recovery and isolated evaluation (0c65f5a)

01d1f59 preserves explicitly named conversation cohorts and a single inherited claims period, without borrowing dates from unrelated clinical turns. f529135 binds shorthand numeric measure references to the prior governed measure only after exact source population and source-identity checks; bare thresholds, explicit new labs, partial populations and ambiguous multi-measure antecedents are not guessed. Focused context tests and regression comparisons passed before both deliveries. Original high-cost claimant and A1c follow-up dev chains recovered at zero provider spend.

Frozen09b239f paid dev completed91/100 (20/19/18/17/17),7 wrong/2 refused, estimated$1.217704837 recorded usage;33Haiku calls before14Sonnet calls plus System One. Free paraphrases229/295(77.6%),10wrong/56refused. These precede the later context fixes.

The livef529135 diagnostic scored93/100 (20/19/18/18/18),6wrong/1refusal, with estimated$0.052285666 in recorded attributed provider usage:15Haiku/144Jev/73Perplexity, noSonnet;4failed calls lack usage. A mistaken page-URL harness attempt returned404 throughout, made no analysis calls/$0 and is excluded. Its artifacts are preserved separately. The93% run was not fully frozen:696program-work DML authorizations and changed program-work bytes were detected; the shared stack WAL also changed (later tracing confirmed result-cache writes; attribution solely to concurrent writers was not established). Do not present it as the final controlled comparison.

0c65f5a sends evaluation work proposals/assessments to a private SQLite backup keyed by frozen-run identity, retaining prior operational state and read-after-write follow-ups without changing live work.26focused evaluation tests pass; tools/runtests_diff.py compared59modules against mainac9bf5a,9same existing failures and0new failures. Committed/pushed after that comparison and :8885 restarted on a detached release worktree. The controlled comparison additionally uses APFS-cloned stack and derived inputs so concurrent sessions cannot change its data or learned stores. Configuration validation and live evaluation smoke passed with0write attempts/$0.

A new100dev run is in progress on this frozen release; competitorheldoutonce thenAgereheldoutonce remain pending. No new held-out texts, individual outcomes, private data, reserved evaluation directory or generator were opened. All development continues using dev-only material. Target>95%/99% remains unmet.


## Strict-audit repair and preflight (4c84e34)

The initial0c65f5a strict live diagnostic was stopped after the first basic chart request fell through to unnecessary paid escalation. Source-level tracing identified graph/cohort result-cache writes and economical-planner saved-plan writes that evaluation had missed. This invalid diagnostic recorded estimated$1.062034214 across38responses; it is excluded from accuracy. No held-out run began.

4c84e34 suppresses those writes during evaluation while returning the computed answer; normal requests still persist caches.28focused tests pass. The100-question no-provider strict preflight scored92/100,6wrong/2refused, at$0 with100evaluation receipts and0store-write attempts. Free paraphrase run on0c65f5a:229/295,9wrong/57refused,$0.

Regression evidence:145modules on candidate and main; each initially reported21failures, with one exchanged UI failure. Reproduction found cross-worktree races on identical temporary JavaScript filenames. The two UI modules each passed on both versions with isolated TMPDIRs; all other20failure names were identical. A separate aligned52-module cache/evaluation comparison had18same existing failures and0new failure names (baseline economical-planner module evaluated separately because the new test changed regex module selection). No assertions were weakened.

Pushed4c84e34 and restarted :8885 on the detached release against the isolated source/learned-state copy. Controlled100dev is in progress with strict audit and separate ledger; next competitorheldoutonce thenAgereheldoutonce remain pending. Additional dev-only chart paraphrase fixes are isolated candidates, not changes to this runtime.


## Shipped d4de3ef: final dev result and held-out milestone

Pushed main and restarted :8885 on detached d4de3ef with frozen synthetic data, pinned active-definition overlay, process/request evaluation and strict audit. Final live100dev:94correct/6wrong/0unresolved, with2correct supported-absence answers (98answered). Structured20/20; notes20/20; quality18/20; claims18/20; insight18/20. Recorded attributed provider estimate$0.069335508:235Jev,118Perplexity,19Haiku, noSonnet;4failed calls lack reported usage. Every returned priced response was priced. This remains an estimate, not an invoice. The one-point note improvement is a repeated-reading result, not proof that a specific parser change caused it.

All100evaluation receipts show0store-write attempts. Source fingerprints are unchanged; no bank files changed. Fingerprinting ignores newly created empty WAL sidecars, which hold no data. HTTP success and failure tests also preserved every row in all four operational/learning stores at$0. The complete request envelope now includes dispatch-journal start and finish within evaluation scratch. Tracing the prior4c84 run established its physical differences were empty WAL creation and dispatch-journal events, not changed clinical source or learned plans; the new run removes that exception.

Chart paraphrase semantics preserve numeric words, plural lab names, evidential “going by”, sourced scalar labels and monetary intent; fronted yes/no questions retain a boolean reading. Frozen a0b614b free paraphrases:237/295(80.3%) versus229/295,9wrong/49refused;8recoveries and0previously correct answers lost. Structured paraphrases59/60. Both comparative runs use the same committed definitions; the live active overlay is separately pinned in its manifest.

The corrected regression runner records nonzero process exits instead of silently treating native crashes as passes.65candidate modules versus63main modules have14exactly identical failure names, including existing native crashes in test_metric_first and test_request_lineage;0new failures.36focused tests pass. The earlier narrower chart comparison had22candidate/21main modules with0failures. Assertions were not weakened.

A client attempt before service readiness made no model calls/$0, was excluded and preserved separately. The harness now requires ready health and aborts on transport/endpoint or audit failures.

Competitor held-out run started once after final live dev, on the same frozen semantic database with unchanged Opus5.5/8-tool documented protocol. Its outputs remain opaque; only aggregates will be inspected. Agere held-out will run once afterward. No held-out question text or individual outcomes have been inspected for development. The six remaining scored dev failures retain unresolved population/attribution/window/measure-definition questions; no keys or standing definitions were changed to increase the score. >95%/99% remains unmet. G3 stays active, G5 incomplete and full acceptance false.

Implementation evidence: artifacts/agere-generalization-d4de3ef/implementation-receipt.json (implementation verified, comparison measurements in progress).


## Full-cost reconciliation and interrupted held-out recovery

Final dev's full dedicated ledger total is$0.092350224; the$0.069335508 previously reported is the question-attributed portion. Another$0.023014716of SystemOnecalls lacked question tags. Whole-ledger windows now capture all trial charges; a separate synthetic worker-context fix is tested but not applied during this comparison. The earlier implementation receipt retains both figures when finalized.

Competitorheldout completed22/50(44%), estimated$4.8693294. No question text or individual outcome was inspected. Agere's firstphase attempted9uniqueitems before an optionalcachewrite audit interruption;8originalscorer results were preserved, and the one unretained/ungraded response is conservatively counted failed and is not repeated. Its9attempts cost$0.149194188 including all ledger-window charges. Source and learning files remained unchanged because strict write protection denied the attempted write.

Evaluation-only cache guards06cd48a were reproduced on synthetic dev fixtures and landed after22candidate/21main regression modules showed8identical existing failures/0new. They only suppress optional analytics/retrieval cache persistence; question interpretation, data, definitions, SQL calculations, model policy and published scorer remain unchanged. Resumption attempted4additional uniqueitems; their full responses were retained and scored internally without another APIcall. All13attempts were preserved, and the prefix ended at a conversation boundary.

The final continuation sends only the remaining37unaskeditems. Strict write protection stays on. Denied optionalcache attempts are recorded as diagnostics while actual returned answers are scored; they do not pause the entire trial. Source/bank fingerprints are checked again at completion. No held-out item is repeated. The final comparison must disclose the evaluation-only correction, the single conservative failure and the full ledger-window cost. It must not be described as an uninterrupted single-release qualification.


## Subsequent dev-only phrasing and accounting fixes (3d3f6ff)

Parallel note readers now copy the request context per submitted job, preserving question cost tags and evaluation audit identity without changing model choices, prompts or concurrency. Explicit “show paid claims” requests retain claim records; explicit totals still return sums. A short dated fragment such as “date of the most recent discharge?” retains the established patient and inherited admission-year scope. Explicit latest-discharge requests sort by discharge date; discharge of the latest admission retains admission order. Synthetic chronology checks distinguish the two requests. The original dev paraphrase now returns the expected discharge date in its actual headline at$0, rather than merely matching the published scorer.

Combined note/accounting + claims comparison:213candidate/211main modules,37identical existing failure names,0new. Final chart comparison:16candidate/14main modules,1same existing spec-fill tie failure,0new. An earlier chart attempt had5candidate failures; all101affected tests passed on candidate and main on reproduction with matching model files/settings. The repeat retained the1shared failure. These are regression comparisons, not an all-green test-suite claim. The three fixes were committed and pushed to main3d3f6ff after these checks. Runtime remains pinned during the unfinished held-out trial.

Dev validation also exposed a published date-scorer weakness: the expected date anywhere in serialized output can over-credit an incorrect primary answer when a table contains multiple dates. No scoring code, reference key, definition or held-out comparison was changed during the trial. Published94/100dev and paraphrase scores must not be described as strict headline/value-adjudicated accuracy. A dev latest-admission source tie (same admission date, different discharge dates) remains a specific ambiguity-handling requirement; arbitrary record-id order is not a clinical resolution. Subsequent complete free paraphrase checks use the same frozen source on candidate and main, with no provider keys.


Matched free paraphrase checks completed with correct local model/calibration links and Metal access:238/295on3d3f6ff versus237/295on619e02b,9wrong/48refused,$0provider spend,one recovered discharge follow-up and0previously correct lost. Invalid missing-artifact/sandbox-GPU attempts were preserved and excluded; no result was fabricated from their partial logs. Full paid aggregates and cost reconciliation are in COMPARISON_RESULTS_2026-10-24.md. Runtime3d3f6ff is normal/ready on original source. New independentdev3 BUILD-half BP chain reproduces1/2correct(1refusal),$0. A shared typed explicit-comparison candidate is isolated, not landed; CHECK-half texts and current held-out individual outcomes remain unopened.


Deliveredaa88491: explicitmeasurementcomparisons persist throughplanning and execution; no defaultclinicalgoal inferred. IndependentstructuredBUILD9/14to10/14,$0,nocorrectlost;18/17module regression1samefailure/0new,30focusedtests. Firstfullnewdeadlinefailure didnotreproduce inisolationon either version orfullrepeat. Liveoriginalsource ready/synthetic;BP147/71vs<140/90correctNo,6SystemOne/0Claude,$0.000195reportedestimate. Newcandidate530edf9counted-fields/mortality73focusedtests pass; intermediateindependentBUILD12/14(2newrecoveries,noloss,$0)butfinalregression/behaviorchecks pending. CHECKhalf closed, heldoutnotrepeated. FullfreeBUILDbaseline16/79(20.3%),8wrong55refused,$0,on3d3f6ff; notpaid/unseenqualification. MaincurrentAAincludes concurrentnotesfixes; attributewhole-workflowchangesaccordingly.


## Current verified delivery and open routing candidate

The current normal release is c23b3a6, pushed and verified ready/synthetic on :8885. It retains the original active warehouse, preceding live definition overlay and normal shared ledger; evaluation flags are unset. Regression31candidate/26main modules:0failures on either side. Receipt: `artifacts/agere-semantic-review-c23b3a6/implementation-receipt.json`.

Reviewed semantic changes preserve typed primary values, explicit comparison bounds only for supported latest-value scope, social-screen positive filters, ambiguous latest-admission ties and private OCR evaluation caching. Frozen free dev3 BUILD on the preceding combined semantic/source-absence candidate scored24/79, with structured14/14. Subsequent concurrent population work reportedBUILD54/79 but CHECK13/71 unchanged; this is no transfer. CHECK text remains closed.

Targeted c23 paid note run:4/5 targets,5/6 including parent, recorded$0.011944076. Isolated routing candidate6422bf3:4/5,5/6 including parent,$0.011184148,2Haiku/0Sonnet,3failed calls withoutusage. No source/learning differences or strict write attempts. A retrieval miss is not proof of source-wide absence, and no answer key was changed.

Matched free routing paraphrases:238/295 versus241/295. Three lost credits were baseline “2 passages” outputs; the candidate now leaves those clinical fact questions unresolved. Healing is unfinished and the routing candidate is not deployed. Seventeen focused tests(1skip),7candidate/base regressionmodules0failures.

The full paid BUILD diagnostic stopped after11/79 retained correct responses on five denied `semantic-readings.sqlite3` cache writes. Incremental recordedspend$0.032441278; full ledger$0.043625426 includes the preceding$0.011184148 diagnostic. Source protection held. The evaluation-only private-cache correction a0832bc has15candidate/14base modules0failures, combined21candidate/20current-main modules0failures. Only68unasked BUILD items continue on a new ledger; no chain crosses the phase boundary and no completed question repeats. These results cannot qualify unseen correctness.

The coherent current comparison and spend report is `COMPARISON_RESULTS_2026-10-24.md`; earlier sections above retain historical evidence. G3 active, G5 incomplete, all ten areas retained. >95%/99% unmet.
