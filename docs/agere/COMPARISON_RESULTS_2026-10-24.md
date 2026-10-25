# Agere comparison — 2026-10-24

Status: paid comparisons complete; dev-only generalization work continues. The >95% / 99% correctness target is not established. All measurements use synthetic data.

| Measurement | Published-score correctness | Recorded estimated provider spend |
| --- | --- | --- |
| Live Agere dev, d4de3ef | 94/100 (94%) | $0.092350224 |
| Competitor held-out, unchanged Opus 5.5 + semantic layer + eight SQL tools | 22/50 (44%) | $4.8693294 |
| Agere held-out, d4de3ef interpretation + evaluation-only correction 06cd48a | 15/50 (30%) | $2.930939989 |
| Free dev paraphrases, a0b614b | 237/295 (80.3%), versus 229/295 | $0 |

The dev score is the existing published scorer's output, not strict independent adjudication. A subsequent dev-only check found that date scoring can accept an expected date appearing in a result table even when the primary answer differs. No scorer, reference answer or definition was changed during the held-out comparison. This limitation applies to what the published scores can establish. No individual current held-out questions or outcomes have been inspected for development.

Dev bucket results were structured 20/20, notes 20/20, clinical quality 18/20, claims 18/20 and insight 18/20. The dev spend includes $0.023014716 of calls missing question tags; the previously reported $0.069335508 was only the attributed portion. Whole-ledger windows include those charges. Amounts are usage-based estimates, not invoices; failed calls can lack reported usage.

The held-out trial was interrupted by strict audit denials of optional cache writes. Source and learning state remained protected. Evaluation-only guards were tested on synthetic fixtures and landed after a regression comparison with zero new failure names. Original attempts were retained: one unretained/ungraded response counts as a failure and is never repeated. The remaining unasked items continue under the same data, definitions, interpretation, model policy and published scorer. This is an interrupted comparison with a disclosed evaluation correction, not uninterrupted single-release qualification.

The current bank, reserved evaluation directory and generator have not been opened by this assistant. An earlier accidental inspection exposed legacy oracle material labelled held-out; that was disclosed and those legacy items cannot be claimed unseen. The current comparison remains opaque except for aggregates.

Subsequent dev-only fixes are pushed as 3d3f6ff: request cost/audit context in parallel note reading; claim-record lists versus monetary totals; patient context and discharge chronology in short date follow-ups. The combined regression compared 213 candidate versus 211 main modules, with 37 identical existing failures and zero new failures. The final chart comparison compared 16 versus 14 modules, with one identical existing failure and zero new failures. The initial chart failures did not reproduce in 101 aligned tests; the repeat comparison is retained. These checks do not mean the full suite is green.

The original discharge paraphrase now returns the expected date in its actual primary answer at $0. A complete subsequent free paraphrase attempt crashed in a native local-model import before producing a score; it is excluded, preserved and cost $0. Complete matched candidate/main paraphrase verification remains pending. A source tie between latest-admission records still requires explicit ambiguity handling. No clinical definition or key was changed to improve the score.

G3 remains active; G5 and full product acceptance remain incomplete. Greater than 95% correctness, clinical qualification and product-wide superiority are not established by these measurements.


Final Agere held-out aggregates: 15 correct, 13 wrong, 22 refused/error; published precision among answered 45.8%. Buckets: structured 7/10, notes 3/10, clinical quality 1/10, claims 3/10, insight 1/10. The trial retained 13 earlier attempts, asked only 37 new items, repeated zero, and counted one ungraded response failed. Final source identity and learning-file fingerprints match; 50 audit receipts record one denied stack write and 25 denied optional OCR temporary writes. These are attempted writes, not permission to mutate source. One model-cache write was allowed. Provider reservations pending before and after: zero. Haiku preceded every question-path Sonnet escalation recorded by the ledger. No current held-out text or individual outcomes were inspected.

Total recorded estimated spend for this continuation's dedicated runs, including invalid diagnostics and both final held-out comparisons, is $12.015487791, excluding the pre-existing $0.204123845 analyst calibration. No charges from invalid runs were hidden. This total does not represent all account-wide activity by other sessions.
