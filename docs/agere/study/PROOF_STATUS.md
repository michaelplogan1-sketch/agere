# Agere analytical evidence — October 19, 2026

The validated demonstration is promising, but it does **not yet establish general accuracy superiority**.

| Nine independently reconciled Gold 10 questions | Agere layered stack | Opus text-to-SQL |
|---|---:|---:|
| Questions passing the registered scorer | 9/9 | 7/9 |
| Incremental model API charges | $0.00 | $1.89 |
| Model API dollars per passing answer | $0.000 | $0.270 |

This is a reanalysis of the saved S1-lean A/B results on the already validated Gold 10 subset. No answers were
rerun or rescored to create this brief. The original question wording, native synthetic population, model settings,
pricing and cold-cache policies are retained in [the source run](../../../artifacts/study/S1-lean/run.json).
The baseline receives semantic documentation and one SQL execution repair. The scorer's patient-list pass threshold
is F1 >= 0.98; it is not an exact-match headline. Gold 10 references were independently reconciled with the generator
before the original run, under digest 05c5b54ba34728cd.

There are two discordant question pairs in Agere's favor and none in Opus's. Exact paired McNemar p =
**0.50**. Nine questions, selected around existing product workflows, do not support a broad
accuracy claim. The API cost advantage is observed on this set; local compute, engineering, validation and operating
cost are unmeasured and must not be described as zero. No percentage cost saving for a customer or general deployment
projection is established.

The [machine-readable calculation](../../../artifacts/study/validated-proof-2026-10-19/evidence.json) pins both
score files by hash and includes paired uncertainty calculations. Original [Agere](../../../artifacts/study/S1-lean/A/p1/score.json)
and [Opus](../../../artifacts/study/S1-lean/B/p1/score.json) files also contain unvalidated native rows; **only their
nine locked-set rows contribute above**. S1 D/E had credit failures and are excluded. Gold-50's 19/41 and 22/41
are not evidence of comparative accuracy while its reference audit remains open.

## Chosen proof route

Validate all Gold-50 references from delivered data and reconcile through the independent generator's scorer, then
rescore the saved A/B answers once on unchanged, validated questions. This avoids paying again for existing outputs
and preserves the unfavorable cases. Disclose every void question and do not silently replace wording. Any follow-up
run of the repaired stack must be explicitly post hoc and use validated references; it cannot be called held-out proof.

[EHRSQL](https://github.com/glee4810/EHRSQL) is a useful later independent hospital-query replication.
[BIRD Mini-Dev](https://github.com/bird-bench/mini_dev) is a useful cross-domain SQL check. Both need a new data/schema
integration and measure a different workload. Choosing either now would add integration and validation work without
establishing performance on Agere's native ACO workflow. This is an engineering judgment, not a measured cost comparison.

## What changed and what remains

The product now accepts complete analytical questions and definitions up to 12,000 characters across planning,
execution and conversation storage. Three saved Gold-50 abstentions reported the old 1,500-character rejection.
The fix is general and verified on independent tiny fixtures; their benchmark correctness has not been retested.

One authorized post-change Gold 10 regression (S4-input-contract-regression) passed
9/9 with $0.00
in model API charges and median latency 0.24 seconds.
This checks preservation of existing validated behavior; it is not a fresh held-out comparison or evidence
that the pending Gold-50 failures are corrected.

A bounded Opus review candidate can address analyst implementation failures or unsupported clarification requests.
It executes and checks SQL, preserves genuine missing-input declines, records costs, and requires a quoted definition
to override clarification. It remains **disabled by default**, with no model-quality improvement claimed.

The runner and scorer refuse pending reference sets before further study work. The
[source-audit packet](REFERENCE_AUDIT_2026-10-19.md) contains the requested breakdowns for all five pending disputes.
The independent generator owner must reconcile those findings and reseal corrected references. All 41 new questions
still require per-question independent validation; the five disputes are not the complete validation task.
