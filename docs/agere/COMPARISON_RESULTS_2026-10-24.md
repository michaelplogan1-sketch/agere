# Agere measurements — October 24, 2026

The >95% / 99% correctness target remains unmet. G3 is active; G5, clinical qualification and full ten-area product acceptance remain incomplete. All measurements use synthetic data.

## Completed paid comparisons

These runs were completed in the planned order. Both held-out comparisons ran once; they will not be repeated for development.

| Run | Published correctness | Recorded provider estimate |
| --- | ---: | ---: |
| Live Agere dev, d4de3ef | 94/100 (94%) | $0.092350224 |
| Competitor held-out, unchanged Opus 5.5 + semantic layer + eight SQL tools | 22/50 (44%) | $4.8693294 |
| Agere held-out, d4de3ef interpretation + evaluation correction 06cd48a | 15/50 (30%) | $2.930939989 |

Dev buckets: structured20/20, notes20/20, quality18/20, claims18/20, insight18/20. The full dev ledger includes $0.023014716 missing question tags; $0.069335508 previously reported was only the attributed portion.

Agere held-out:15 correct,13 wrong,22 refused/error; precision among answered45.8%. Buckets: structured7/10, notes3/10, quality1/10, claims3/10, insight1/10. Competitor buckets:9/10,1/10,4/10,6/10,2/10 respectively.

The Agere held-out trial was interrupted by strict audit denials. It retained13 prior attempts and asked37 new items, repeated zero, and conservatively counted one unretained/ungraded response failed. Source identity and learning fingerprints match. Fifty receipts include one denied stack write and25 denied OCR temporary writes; one model-cache write was allowed. Pending reservations:zero. Interpretation, definitions, model policy and scorer remained fixed while evaluation-only write guards were corrected. This was an interrupted comparison with disclosed corrections, not uninterrupted single-release qualification.

A later dev-only fixture reproduced that denied OCR temporary rebuilding can affect evidence availability. The new private evaluation cache fixes that defect. Its effect on the earlier held-out score cannot be inferred; the30% result stands and will not be rerun.

Published scoring can over-credit an expected date appearing in a table when the primary answer differs. A supplemental dev-only primary-value audit now exposes such discrepancies; it is limited in coverage and does not replace whole-question adjudication. No reference answer or clinical definition was changed to improve scores.

No current held-out text or individual outcomes were inspected for development. An earlier accidental inspection exposed legacy oracle text labelled held-out; it was disclosed, stopped, and those legacy items cannot be claimed unseen. The reserved directory, current held-out bank, private data and generator remain unopened.

## Subsequent dev-only evidence

| Measurement | Result | Provider spend |
| --- | --- | ---: |
| Matched free paraphrases, 3d3f6ff vs preceding main | 238/295 vs237; one recovery, zero losses | $0 |
| Matched free paraphrases, 530edf9 vs preceding main | 239/295 on both; zero losses | $0 |
| Independent dev3 BUILD baseline, 3d3f6ff | 16/79;8 wrong,55 refused | $0 |
| Semantic repairs plus source-absence work, b911822 | 24/79;5 wrong,50 refused; structured14/14 | $0 |
| Concurrent owner's population merge, e7364f5 | BUILD54/79; CHECK13/71 unchanged | $0 |
| Targeted paid note diagnostic, c23b3a6 | 4/5 targets;5/6 with parent context | $0.011944076 |
| Targeted paid note diagnostic, 6422bf3 | 4/5 targets;5/6 with parent context | $0.011184148 |

BUILD and CHECK are separated by conversation roots. Only BUILD text/outcomes were inspected here. CHECK text remains closed; the concurrent owner's aggregate is reported as evidence of **no measured transfer**, not generalization success. A narrow note diagnostic is not whole-stack accuracy.

Landed repairs preserve exact BP bounds, latest-only comparison scope, distinct drug-class counts, mortality polarity and conflicting dates, explicit social-screen positivity, ambiguous latest-admission ties, typed primary values and private OCR evaluation scratch. Candidate c23b3a6 compared31 relevant modules against26 current-main modules:zero failures on either side and zero new failures. Earlier broader suites retain known failures; this does not mean the entire suite is green.

Normal :8885 now runs pushed c23b3a6, verified ready/synthetic on the original active warehouse with the preceding live definition overlay, evaluation flags unset and normal shared ledger preserved. The routing and semantic-reading cache follow-up remain isolated candidates. Receipt: `artifacts/agere-semantic-review-c23b3a6/implementation-receipt.json`.

## Spend accounting

Completed dedicated continuation runs including invalid diagnostics and original comparisons: $12.015487791. Subsequent reported normal-service smokes: $0.006018, $0.000195 and reconciled question-tagged $0.006503224; one failed live call lacks usage. The isolated note diagnostic adds $0.011944076. The next isolated note run adds $0.011184148 (three failed calls lack usage). Known recorded subtotal before the in-progress full BUILD run: **$12.051332239**, with these attribution and missing-usage limits. This excludes the pre-existing60-question analyst calibration ($0.204123845) and other sessions' activity.

The calibration achieved26/60 correct,43.3% accuracy and46.4% answered precision. Quality13/20 cost$0.066366145; claims11/20 cost$0.06188009; insight2/20 cost$0.07587761. Free-path baselines were65%,65%,70%. The analyst remains a fallback after free refusal rather than replacing accepted free answers.

Costs are recorded usage estimates, not invoices or account-wide totals. Failed calls can lack usage. Haiku remains first; no unilateral spend ceiling was introduced. Current raw dev diagnostics and aggregate-only comparison receipts are retained under `/tmp/agere_generalization/`; protected raw comparison files remain closed.

## Current development measurement

A complete79-question dev3 BUILD paid run is in progress on immutable6422bf3 with strict frozen evaluation and the same source/definition overlay. The six previously diagnosed dev items repeat within this consistent full-set measurement; this is disclosed rather than presented as fresh qualification. Its start ledger is $0.011184148; both the incremental run cost and full ledger total will be reported. No CHECK or held-out rerun is planned. A matched295-paraphrase free comparison againstc23b3a6 is also in progress. Normal deployment waits for verification.

The routing candidate free paraphrase result is238/295 versus241/295. All three lost published credits had baseline headlines “2 passages” and now refuse; they require reader-level healing, not restoration of unverified passage-only acceptance. The full BUILD diagnostic stopped after11/79 retained correct answers on five denied semantic-reading cache writes, at incremental recorded$0.032441278. The evaluation-only private-cache repair resumes only the68unasked questions on a new ledger. No source write was authorized.
