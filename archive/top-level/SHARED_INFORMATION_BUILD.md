# Joint information value across review questions

Exact existing documentary review requests now support a joint information-value scenario across selected questions. Every request bundle is marginalized from one explicitly supplied joint observation model; request costs are counted once and correlated channel values are never added. No-information and single-request baselines remain visible, alongside the separate structural coverage objective.

The actual question planner, structural portfolio, source request inspection and new scenario editor are connected. The saved P-7193 example uses two unresolved medication questions and two distinct source-account requests. Their IDs remain distinct; the live source does not establish they are interchangeable. Controlled fixtures verify that one exact request linked to several questions is costed once.

Every scenario records exact source snapshot, population, acquisition-work proposal, portfolio and selected requirement receipts. It retains immutable assumptions and history; source, interpretation or implementation changes mark it stale. These are documentary acquisition proposals, not assigned buyer tasks. The original patient facts and work are unchanged.

The finite joint likelihood table includes an observation for every request. Subsets are obtained by summing joint probability masses, not multiplying marginal likelihoods. All subsets are evaluated through the existing finite expected-utility calculator. Validated likelihood rows are normalized within its numerical tolerance before marginalization; entered values remain unchanged. This reuses established [expected sample-information decision theory](https://ocw.mit.edu/courses/ids-333-risk-and-decision-analysis-fall-2021/resources/unit-9-value-of-info-video-3_transcript_pdf/), with no new mathematical or clinical-validity claim.

## Actual example and checks

Baseline utility is0.5. Under the explicitly hypothetical perfectly correlated model, either request alone has gross value0.3 and cost0.1, hence net0.2. The pair retains gross0.3 with cost0.2, hence net0.1. Its structural coverage count is2 versus1 for each request. This demonstrates that conditional coverage and modeled decision value answer different questions. It establishes neither patient probability nor measured savings.

**60 focused checks pass, including 16 new checks.** Thirty random joint models agree with an independent direct preposterior sum. Tests cover correlated redundancy, complementary observations, zero-probability branches, unavailable requests, scope guards, immutable replay, source/implementation invalidation and shared-request cost deduplication. The first run exposed a floating-point marginal row just above1; normalization within the validated tolerance resolved it. Actual HTTP preview/save/replay/rejection and browser entry/save/source drill-through pass; browser errors are zero. One label lookup in the harness was corrected to the observed question-field ID before entry.

All113 previous protected files and 19 separate Documents files are preserved. The manifest now protects114 files. Original work histories (380 work items,383 work events,10 historical capability tasks,31 capability events), earlier scenarios, comparisons and single-question information scenario are unchanged. Zero provider/token-count calls; uncapped ledger unchanged at812 validated/10 failed/$6.292875524 estimated historical API cost. Local compute/review labor remain unpriced.

## Boundaries

- All states, utilities, joint likelihoods, availability and costs are supplied assumptions; none are inferred or calibrated from the chart.
- States and choices describe one common objective for the selected questions. Separate question/channel values are never summed.
- Joint observations include every selected request. Omitted observation vectors are explicitly assigned zero probability; independence is never assumed.
- Marginalization assumes acquiring a subset does not change observation reliability, state or utility. Fixed additive costs, no bundling discounts or order effects.
- Validated likelihood rows are normalized within the existing calculator tolerance before marginalization; entered values remain recorded unchanged.
- Availability is a supplied deterministic feasibility assumption. Unavailable bundles remain visible but cannot be preferred.
- Structural coverage counts conditional possibilities only; modeled observations are not typed documentary facts and cannot resolve checks.
- Only exact request IDs are shared. Similar-looking requests are never merged. Linked checks do not multiply a request cost.
- This is a bounded simultaneous experiment, not a sequential acquisition policy, clinical priority, validated patient benefit or assigned buyer task.
- Source/interpretation/work-proposal or implementation changes require a new scenario; historical assumptions remain unchanged.

One to three requests, up to eight named states/choices/joint outcomes; no silent sampling. The initial local choices+preview took6.91seconds, including source dependency checks. Full product acceptance remains false. Receipt timestamp 2026-10-11T06:20:08.091850+00:00.

![Joint scenario and separate baselines](artifacts/agere-shared-information-v1/browser-scenario.png)
