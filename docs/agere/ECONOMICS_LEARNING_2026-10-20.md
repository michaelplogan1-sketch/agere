# Shared savings, downside risk and verified rule learning

User-directed G3 increment. This is an isolated candidate based on `69caa1549391164aa21d64766d3ab596886bcf51`; the build owner integrates and activates it. G3 remains active, G5 blocked, full acceptance false, and the full product charter remains intact.

Agere now rejects a two-sided contract without an explicit loss share and a revenue-capped contract without participant revenue. It validates finite rates and expenditures in both the settlement and risk-simulation engines. Settlement output explains gross savings/losses, thresholds, net payment and assumptions. An omitted program year is disclosed as the latest stored year assumed for a scenario; it cannot authorize a reusable rule lesson. This does not establish CMS payment entitlement, actual quality qualification or causal intervention savings.

The existing conversation route automatically captures questions and unsuccessful outcomes. The learning summary now groups healthcare economics questions and recurring gaps. In **Flag as wrong**, an explicit program, track and year can expose an optional cited rule selector. The server saves feedback first, invalidates related learned plans, and checks the selected parameter/value against the exact current program document before appending a rule lesson. Free-text explanations and expected answers remain unverified feedback. No model weights are trained.

Later questions receive active lessons through the shared planner's existing correction context. Lessons require the same program, variant, year and source digest. Changed or unavailable program documents suspend reuse. Later feedback for that program/year also suspends the earlier lesson until rechecked. This is deliberately conservative: the current implementation retains the latest checked correction for a program/year, rather than resolving conflicting comments automatically. Existing immutable correction history is preserved. Source edits invalidate both planner and analytical caches and are detected without restarting the process.

## Evidence

`artifacts/agere-economics-learning-v1/implementation-receipt.json` records file hashes, tests, before/after failures and accounting. Reproduce from this checkout with the existing Python environment:

```sh
/Users/mikel/clinical-insights-local/.venv/bin/python -m unittest test_economics_learning test_vbc.VBC test_programs test_contract_strategy test_shared_planner test_general_analysis -q
/Users/mikel/clinical-insights-local/.venv/bin/python verify_economics_learning.py
```

The first command covers 107 affected checks, including 17 authored economics-learning checks. The HTTP verifier uses a temporary empty fixture and replaces only Stack construction with a fixture adapter; it exercises real API routes, correction persistence, failed-question capture, scope isolation, rejected claims, suspension/rechecking and served page JavaScript syntax. It is not a production-language or live-browser acceptance test. The owner must verify the integrated shared-planner and actual UI after reconciling concurrent changes.

Four baseline failures reproduced on `69caa15` are now rejected: missing downside rate returned `-$2,500`; a missing participant-revenue cap input returned `-$4,000`; a 150% savings share returned `$15,000`; and NaN expenditures returned zero. Independent fixtures check a $1m benchmark, first-dollar sharing after a 2% hurdle, a $50,000 benchmark loss cap, an $8,000 revenue loss cap, and Enhanced quality-dependent loss sharing with the 40% floor. Negative simulated spending is floored at zero and disclosed; this modeling assumption has not received actuarial qualification.

The regulatory interpretation was checked against [42 CFR 425.610](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-425/subpart-G/section-425.610) and [42 CFR 425.605](https://www.ecfr.gov/current/title-42/chapter-IV/subchapter-B/part-425/subpart-G/section-425.605). Existing program documents and their retrieval dates remain unchanged. “Verified rule” means an exact match to the stored cited specification, not a new independent verification of every source clause. Public CMS changes still require a reviewed rulebook update; no automatic web ingestion or recurring background retraining was introduced.

## Integration boundary

The integration owner retains control of the running app, warehouse and checkpoint. Preserve newer procedural-memory, learning, shared-planner, general-analysis and service changes when applying this delta. No original warehouse, generator, separate research corpus, protected benchmark or held-out answer key was opened or changed. The pre-existing program/contract unit tests use their public phrasing fixtures. Hosted model/API calls for this candidate: **0**, spend **$0**.

Next owner check: ask a shared-savings/downside question through the integrated shared planner, inspect the supplied assumptions and source citations, flag a cited rule for the explicit year, re-ask with different wording, and verify that stale or cross-year lessons do not influence the answer. Keep broader model, actuarial, clinical and payment qualification gaps open.
