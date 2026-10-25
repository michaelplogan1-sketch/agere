# Agere measurements — October 24, 2026

**The >95% / 99% correctness target is unmet.** The latest development diagnostic is81.0% under the published scorer, which still over-credits some answers. The earlier held-out result remains30%; development results do not replace it. G3 is active, G5 and full ten-area acceptance are incomplete. All data are synthetic.

## Completed measurements

| Run | Published correctness | Recorded provider estimate |
| --- | ---: | ---: |
| Original live Agere dev, d4de3ef | 94/100 (94%) | $0.092350224 |
| Competitor held-out, unchanged Opus5.5 + semantic layer + eight SQL tools | 22/50 (44%) | $4.8693294 |
| Agere held-out, d4de3ef interpretation + evaluation correction06cd48a | 15/50 (30%) | $2.930939989 |
| Full dev3 BUILD diagnostic,6422bf3 → evaluation-only correctiona0832bc | 64/79 (81.0%); answered precision58/68 (85.3%) | $3.289170316 |
| Targeted five-note diagnosticc23b3a6 | 4/5 targets;5/6 including parent | $0.011944076 |
| Targeted five-note diagnostic6422bf3 | 4/5 targets;5/6 including parent | $0.011184148 |
| Source-scope check57a96f4 | Failed: fell back to “2 passages” | $0.004075512 |
| Source/evidence-context check1a7cef4 | Two separately dated supported outside reports; scoped source review below | $0.001080286 |

The original paid dev and both held-out comparisons completed in that order. Both held-out runs were asked once, with no completed-question repeats. No subsequent held-out or CHECK run was performed here. No current held-out question text or individual outcome was inspected for development. An earlier accidental legacy-oracle exposure was disclosed; that legacy material cannot be called unseen. The protected directory, private data and generator remain outside this work.

Original dev buckets: structured20/20, notes20/20, quality18/20, claims18/20, insight18/20. Its full ledger includes$0.023014716 missing question tags; the earlier$0.069335508 was only the attributed portion. Agere held-out:15correct,13wrong,22refused/error; answered precision45.8%. Buckets7/10,3/10,1/10,3/10,1/10. Competitor buckets9/10,1/10,4/10,6/10,2/10.

The Agere held-out run was interrupted by strict audit denials. Thirteen earlier attempts were retained,37 new questions were asked, zero were repeated, and one unretained/ungraded response was conservatively failed. Source identity and learning fingerprints match. Fifty audit receipts include one denied stack write and25 denied OCR temporary writes; one model-cache write was allowed. Interpretation, definitions, model policy and scorer stayed fixed while evaluation-only write guards were corrected. This is an interrupted comparison with disclosed corrections, not uninterrupted single-release qualification. A later dev fixture proved that denied OCR rebuilding can affect evidence availability; its effect on individual held-out items is unknown. The30% score stands and will not be rerun for development.

## Full79-question development run

| Bucket | Correct / questions | Wrong returned | Refused | Unretained failure |
| --- | ---: | ---: | ---: | ---: |
| Structured patient |14/14|0|0|0|
| Notes/documents |13/18|4|0|1|
| Population quality |12/18|5|1|0|
| Claims/pharmacy/social factors |10/12|1|1|0|
| Insight |15/17|0|2|0|
| Total |64/79|10|4|1|

BUILD and CHECK are separated by conversation roots. Only BUILD text/outcomes were inspected here. A concurrent owner's earlier population merge improved free BUILD16→54/79 while CHECK stayed13/71: no measured transfer. The current paid BUILD run is development evidence; six items had previously appeared in a targeted dev diagnostic. It is not unseen qualification.

The run retained11 answers before five attempted semantic-reading cache writes were denied. An evaluation-only private-cache repair retained18 more responses. The next response was discarded by an overly strict HTTP200 assertion, which also incorrectly rejected ordinary HTTP400 refusals. Its exact failure type is unknown; it counts as one failure and was not repeated. The corrected client retained the remaining49 results. No conversation chain crossed a recovery boundary. There were zero repeats of completed questions within the79-question run.

Cost reconciliation: first phase incremental$0.032441278 plus the later dedicated ledger$3.256729038 equals$3.289170316. Total1330 recorded provider calls:88Haiku,37Sonnet,679Jev,524Perplexity and2preset-fast. All returned priced responses were priced; pending reservations are zero. Thirty-seven failed calls lack usage, including provider rate-limit failures, so this remains a usage estimate. Final source fingerprints match the initial source and no learning/bank files changed. Five denied cache writes and the one missing audit receipt remain disclosed. No clinical definitions or answer keys were changed to improve scores.

**The published scorer is not strict adjudication.** Two of the64 credits lack affirmative data-absence proof; one is a generic `not_understood` answer that the scorer credits because its type is `unsupported`. The supplemental primary-value audit covers only14/79 items:13match,1mismatch,65unverified. Other weaknesses include incidental table dates and citation IDs without entailment. These limitations are retained rather than silently regrading the completed comparison.

A read-only BUILD audit explains one population discrepancy: the reference counts87patients with two stay rows, while the compiled answer counts72with two distinct admission days. Fifteen patients differ;13 have same-day/same-facility zero-day plus longer-stay records. This supports a duplicate-episode concern, not automatic reference correction. The remaining records and clinical unit need adjudication. Source evidence is retained; the score remains wrong.

## Landed repairs and source-fidelity evidence

The current product release is **b3bc46d**, pushed to main and restarted on the original synthetic :8885 service. Health is ready. The preceding live definition overlay and normal shared ledger are preserved; evaluation flags are unset. Two clean normal requests (no benchmark headers) cite exactly the two qualified chest reports. The repeat took4.8seconds with zero provider calls and$0 recorded provider cost. Receipt: `artifacts/agere-imaging-scope-b3bc46d/implementation-receipt.json`.

Earlier semantic repairs preserve exact latest-only BP comparisons, distinct drug-class counts, mortality/date uncertainty, positive social-screen filters, latest-admission ties and typed primary values. The next increment preserves a checked scalar after final spec execution: a real dev response changed from `1 rows · days_used` with no primary scalar to `0 days used`, while keeping its partial status and every uncertainty limitation. This was replayed with zero provider calls; attribution and interpretation remain separately unverified.

The source increment binds “CXR report” to document kind, recognizes formal report headings when imported titles are generic, excludes ED/discharge narratives, keeps conflicting dated reports separate, and gives the answer checker the cited impression text. It also versions saved plans against note interpretation and acceptance modules. No checker threshold was reduced.

Final regression:40candidate/38main modules, one identical existing `test_core.setUpClass` failure and zero new failures. Ninety focused tests pass with one skip. Matched free paraphrases:243/295(82.4%) versus241/295; two recoveries, zero previously correct answers lost, provider cost$0. Earlier independent scalar/cache increment:45/43modules, two identical existing failures, zero new failures;241/295 on both. These checks do not mean the entire suite is green.

Service checks first exposed mixed ED summaries and then an unqualified generic radiograph in an outside-CXR answer. The benchmark client used evaluation headers: these checks exercised live code with learning writes disabled, not normal learning. This mislabelling is corrected; raw responses are preserved.

The source-kind/context increment passed90focused tests(1skip),40candidate/38main modules with one identical existing setup failure and zero new failures. It improved free paraphrases241→243/295, with two recoveries and zero losses. A frozen check cost$0.001080286,27SystemOne calls and noClaude, with source/bank state unchanged and no strict write attempts. It returned two supported outside reports, but a later service check also included a nonspecific XRAY. The additional body/modality qualification now selects exactly two chest-supported reports and leaves all three available for a broad X-ray request. That increment passed79focused tests(1skip),41candidate/40main modules with one identical existing failure and zero new failures;243/295 on both, no losses.

Final clean normal-mode requests use only `Content-Type`; both return evaluation=None, exactly the two qualified sources and separate findings. The first took10.9seconds, the repeat4.8seconds; both recorded zero provider usage. The earlier evaluated b3bc46d service check had two directly tagged SystemOne calls costing$0.00009361. Same-window Haiku activity is not assigned to this question. Source quotes preserve both a possible infiltrate and a negative cardiopulmonary finding; they are not collapsed into one clinical result.

The original automated source audit expected all three retrieved radiographs to be cited. Its `all_matching_reports_cited=false` is preserved. The third candidate merely says XRAY / mild degenerative changes / no fracture, without establishing chest specificity. Retrieval candidates are not confirmed facts. The supplemental review records two supported chest-report answers and the unasserted nonspecific candidate; it is not clinical adjudication or a complete-warehouse coverage claim. No evaluation answer key was changed.

The experimental passage gate remains isolated. It reduced a free score241→238 by withholding three previously credited “2 passages” answers. It was excluded from the live release while stronger source-bound answering was repaired.

## Spend and remaining work

Cost attribution was reconciled separately from account activity:

| Category | Recorded estimate |
| --- | ---: |
| Dedicated continuation ledgers, including invalid runs and completed comparisons | $15.332942129 |
| Directly question-tagged recent service checks | $0.001526914 |
| Earlier reported normal-smoke estimates, still provisional | $0.012716224 |
| Subtotal including those older provisional estimates | **$15.347185267** |
| Deduplicated same-window activity with unassigned ownership, excluded above | $0.032960800 |

Earlier totals included same-window Haiku calls without causal attribution. Those are real recorded activity, but none is linked to these executions in the request journal, so they remain unassigned. The raw telemetry and earlier reconciliation are preserved. The benchmark-header correction also means earlier “normal” service checks must not be used as proof of normal learning. The two clean normal-mode checks above now establish that behavior. These are usage estimates, not invoices or account-wide totals; failed calls can lack usage. The pre-existing analyst calibration($0.204123845) and other sessions' activity remain outside the owner subtotal.

The60-question analyst calibration achieved26/60 correct(43.3%),46.4% answered precision. Quality13/20 cost$0.066366145; claims11/20 cost$0.06188009; insight2/20 cost$0.07587761, versus free65%,65%,70%. The analyst remains a fallback after free refusal, not an override of accepted free answers.

Next: repair the observed shared-result identity collision in devx2-18, where a generated classification key overwrites a known patient identifier with “unknown”; then address premature acceptance of partial/unknown results while preserving useful fallback evidence. Keep definition/episode disputes separate from scoring changes. Haiku remains before Sonnet; no unilateral spending ceiling, held-out/CHECK tuning or rerun has been introduced. Greater than95% correctness, clinical qualification and superiority over commercial stacks remain unestablished.

Receipts and aggregate-only comparisons: `/tmp/agere_generalization/`. Current product/receipt state is maintained in `BUILD_OWNER.json` and `AGERE_BUILD_CONTROL.json`.


Atomicidentity03904dd is pushed/live after20/19module matched regressionzero failures on finalrerun. Saved BUILDpatientidentity repaired; answer remainsunknown, no accuracygainclaimed. Finaltrue-normal sourcecheck exact2reports/$0/evalNone. Initialartifactsetupsmoke26SystemOnecalls known$0.001017174 plus1failedwithoutusage, accounted separately and preserved; originalsharedartifacts restored before finalverification. Updated owner subtotal including older provisionalestimates$15.348202441, unassignedwindowactivity unchanged$0.032960800. Partial-continuation candidate is notlive; devparaphrase comparisoninprogress.


Partial-continuation probe: baseline03904dd1/2correct($.673830245,34calls,11Haiku10Sonnet) versuscandidate326a7ee1/2($.593646393,36calls,8Haiku14Sonnet,1failedwithoutusage). No source/bankchanges orstrictwriteattempts. Exploratoryfreeparaphrases242/295onboth,0lost/0recovered,$0; candidatepolicycacheidentitylistchanged duringrun,so thisisnotsealedreleasequalification. No measuredaccuracygain; candidatekeptinactive. Owner recorded subtotalincludingolderprovisional$16.615679079, plusunassignedwindowactivity$0.032960800 separately. Nextreassessedfailure: sourcequalifier pertheMay2027callbackfalselydemandsagroupedtable.
