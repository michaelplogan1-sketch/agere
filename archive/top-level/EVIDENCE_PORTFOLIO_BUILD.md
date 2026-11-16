# Evidence-review bundle comparison

October 10, 2026 · original-cohort v1.23 · synthetic development capability

Agere now compares combinations of evidence-review requests through the question planner and Analysis library. The result opens the existing exact requests, documentary contracts, literal source accounts and unresolved fields. It does not create clinical actions or claim that acquiring a source will resolve a patient question.

This implements a bounded part of the shared-information-investment direction in the separate research corpus's `model-designs.md`, section 4: show which checks a bundle could support before attempting expected-value calculations. The external corpus is unchanged. This is standard finite combinatorial analysis, not a novel mathematical result.

## What executes

- An `evidence_portfolio` graph node evaluates one explicit selected population and one to eight existing documentary contracts. An exact local question command preserves patient, contract and slot parameters; qualifiers are never silently stripped.
- Existing evidence acquisition generates source-bound requests and conditional cases. The comparison uses those immutable identities and stores its own immutable, replayable receipt. Source or interpretation changes invalidate current access.
- For each request bundle S, coverage is the number of distinct patient-contract checks with at least one conditional case whose entire request set lies in S. A request used by multiple cases/checks occupies one slot, and a check is counted once. These are separate hypothetical possibilities, not necessarily jointly achievable outcomes.
- Exact subset-union calculation examines every combination in a bounded universe of at most 16 requests and 128 unresolved checks. It reports the best coverage for each allowed cardinality, prefers fewer requests on ties, and compares a deterministic greedy marginal-coverage baseline. It assumes equal request effort solely for the declared scenario.
- Excess scope, incomplete upstream cases and unbound request sets yield an explicit incomplete result and no optimum. Nothing is silently sampled. A zero-slot or empty-population input remains zero/empty.
- The existing Analysis library can select several documentary checks, prepare its evidence-review queue, compare bundles, and open the selected requests. The comparison can also run through the main question planner.

## Actual warehouse observations

For P-1001, the three selected medication contracts produce six requests and two unresolved documentary checks. With three hypothetical slots, the exact comparison selects two literal-field requests covering one check; the greedy baseline's three requests cover none. This illustrates request complementarity under this structural objective. It is not clinical superiority, an outcome improvement or a staffing recommendation. The source viewer preserves the missing `reported_intent` fields and their distinct medication-account identities.

For P-7193, the same contracts produce seven requests and three unresolved checks. Two requests cover two checks; the greedy baseline also covers two. Shared-request deduplication across checks is exercised in controlled mathematical tests; these live examples do not establish that the current contract catalog has interchangeable acquisition requests.

## Validation and limitations

65 unique focused checks pass, including 14 new checks and independent brute-force comparisons on 80 generated hypergraphs. Tests cover complementary requests, duplicate counting, input order, zero/empty scope, exceeded bounds, incomplete cases, stale source/logic, immutable receipts and exact question scope. Actual HTTP and browser checks pass through the main question path and multi-contract Analysis library, including opening the selected source account. Final evidence is in `artifacts/agere-evidence-portfolio-v1/implementation-receipt.json`.

The initial cold composed query took about 60 seconds. Source validation repeatedly hashes/reconstructs existing source state; this is an observed performance limitation, not time spent in the small combinatorial calculation. No performance improvement is claimed here.

No probability distribution, expected information value, causal effect, calibrated clinical benefit, real duration, real team capacity or patient preference utility is available to this method. It does not merge similar-looking source requests, resolve conflicting identities, retire answers, prioritize clinical care or dispatch work. Buyer/team assignment remains in the existing separate workflow. The method currently covers registered documentary contracts; imported quoted program packages and answer-derived review questions require explicit adapters.

All 75 previously protected files and the separate 19-file research corpus are unchanged; eight saved answers and six buyer tasks still verify. The 256 functional work items and 257 events retain their exact digests. The new receipt-bound method makes 76 protected files for continuation. Provider usage is unchanged; no new inference or token-count calls are required. Spending remains uncapped. The rest of the full product queue is unfinished.
