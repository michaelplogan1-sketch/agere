# Agere: critical review of the stack and build method

October 26, 2026. Read-only engineering review; the only change made for this review is this report. No provider calls, product edits, deployments, or protected question-bank inspection. This is an assessment of local code and recorded evidence, not a clinical validation or a comparison against commercial products.

## Judgment

**The foundations are worth keeping. The current method of adding routes, repairing individual question families, and relaxing final acceptance is not the best way to finish Agere.** It has produced real capability, but development scores have outpaced demonstrated generalization. The next phase should consolidate execution and verification, rather than add another collection of parsers or another model rung.

Mike is right that an LLM with suitable evidence and tools should recover many questions the deterministic paths miss. The earlier stack often failed to give it that opportunity. The newer backstop substantially improves tool access and routing. However, reaching an LLM is not sufficient: it must receive the correct scope and definitions, execute the right computation, find the relevant documents, and have its final claims checked against those results. A stronger model cannot reliably infer an unstated denominator or repair an earlier answer it never sees.

There is no evidence yet that Agere meets 95% end-to-end correctness or beats all competitors. Nor does a question-answering score establish completion of the wider population-health/VBC workflow, clinical qualification, or outcome improvement. The repository itself still records G3 active and full acceptance false.

## What I actually reviewed

I inspected main at `d7f0edd`, the newer measurement worktree at `a562cd8`, their differences, cascade/backstop/acceptance code, evaluation and transport code, build controls, recent audits, and saved run metadata. At inspection, the only listener on the two specified product/measurement ports was :8888, running from the `cascade-v2` worktree. I found no :8885 listener. This establishes the server's working directory, not an immutable proof that its loaded Python code matches the worktree's latest commit.

The two trees differ across 61 files. That matters: conclusions about old main must not be presented as conclusions about the newer candidate. Main's recorded live-release information also does not match the currently observed listeners. A passing continuity checker verifies document consistency, not the actual release or clinical quality.

I ran 29 existing backstop/scorer tests in the newer worktree: all passed. I also ran small synthetic, offline reproductions described below. No model was invoked. Existing tests are useful, but the reproductions show important gaps in their coverage.

## What the measurements support

These figures are aggregates of saved result files, not fresh measurements. They span different candidates and scoring/key revisions; they are not a controlled before/after experiment.

| Saved run | Correct / total | Overall | Correct among answered* | p50 / p90 seconds | Recorded estimated cost** |
|---|---:|---:|---:|---:|---:|
| Original subscription dev3 check | 29/71 | 40.8% | 25/49 | 34.56 / 120.45 | $5.6465 |
| Backstop iteration 4, dev3 check | 43/71 | 60.6% | 38/61 | 26.61 / 96.58 | $3.2367 |
| Backstop, original dev | 94/100 | 94.0% | 92/98 | 0.93 / 34.04 | $0.1537 |
| Backstop, composition dev2 | 99/130 | 76.2% | 93/120 | 0.79 / 73.94 | $2.3638 |
| Backstop, held-out milestone | 23/50 | 46.0% | 18/43 | 43.12 / 135.67 | $3.7509 |
| Latest saved dev3 check v6 | 32/71 | 45.1% | 27/34 | 18.61 / 104.57 | $4.3211 |

*Uses each run's saved `answered` flag. Correct data-absence responses can contribute to overall correctness without appearing in this column. This is descriptive, not independently adjudicated clinical precision.

**Sums of available `usd` entries, not verified invoices. Respectively 2, 2, 1, 1, 4 and 31 rows lack cost entries. Subscription transport estimates and cache state further limit economic comparisons. Latency quantiles use all saved rows, including errors; p90 is the empirical lower-index percentile.

The latest v6 file has **31 rows with recorded errors**. Its 45.1% is not a clean estimate of reasoning capability. It is evidence that the measurement path was unreliable. The newer runner now stops after consecutive detected provider outages; that is a useful repair, but it does not retroactively qualify this run.

The saved subscription competitor check run is 23/71, with 20 errors. A comparison against that overall score would reward transport reliability as well as answer quality; comparing only its completed subset introduces selection bias. The older held-out competitor figure of 50% also becomes 36% under a stricter historical scoring treatment, according to the audit. **There is no current, same-protocol, same-scorer comparison that establishes superiority.**

Evidence files: `/tmp/e2e_sub_dev3_check.json`, `/tmp/e2e_sub_dev3_check_backstop4.json`, `/tmp/e2e_sub_dev_backstop.json`, `/tmp/e2e_sub_dev2_backstop.json`, `/tmp/e2e_sub_heldout_backstop.json`, `/tmp/e2e_sub_dev3_check_v6.json`, and `bench/results/dev_set/competitor-dev3-check-sub.json`. Only aggregate metadata was used here; held-out questions and answers were not inspected.

## Ranked findings

### 1. Final-answer acceptance is weaker precisely where uncertainty is greatest — high priority

In the newer worktree, `tier_backstop_agent()` calls `contract_of()` directly. It records shape findings as limitations but bypasses `executed()` and the normal answer gate. The contract is marked execution-accepted. The outer planner does not enforce an acceptance failure for a `checked_graph` contract because it assumes the cascade already checked it.

This is a concrete control-flow problem, not an objection to flexible reasoning. “Heal rather than block” should mean repair the plan, retrieve more evidence, recompute, or escalate. It should not mean that a final model answer receives a verified-looking execution contract without the verification used elsewhere.

**Change:** require the same final contract everywhere. Keep hard invariants for patient/cohort scope, time, evidence binding, units and arithmetic. Permit an explicitly partial or unverified response when repair fails; do not conflate it with a checked answer.

References: newer worktree `clinical_core/agere/clinical/proposers.py:754`, `:1110`, `:1119`; `clinical_core/agere/clinical/shared_planner.py:1210–1222`.

### 2. A quote can be genuine while the answer contradicts it — high priority

The newer code fixes yesterday's pooled-output citation defect: returned rows are now bound to their own record. That deserves credit. A remaining defect is `snap()`, which accepts order-insensitive word overlap as quote repair.

Offline reproduction:

- Source: “Patient does take insulin daily.”
- Proposed quote and headline: “Patient does not take insulin daily.”
- Verification keeps the source's affirmative passage as a repaired quote, while the negative headline stays unchanged.

The source is real; the answer is still wrong. Quote presence is not entailment. Medication, experiencer, negation, date, certainty, and stopped-versus-current status must agree between claim and passage.

**Change:** allow formatting normalization, but require claim revalidation after substantive quote repair. Represent evidence bindings per claim. Qualify System One readers on these clinical distinctions using independent examples, rather than treating an uncalibrated confidence threshold as proof.

References: newer worktree `clinical_core/agere/clinical/backstop_agent.py:328–381`.

### 3. Record tools do not consistently enforce the request's scope — high priority before real data

`read_document(record_id)` can fetch an uncached record directly by ID without checking the request's patient/cohort or as-of/window constraints. Filtering list/search tools is insufficient when direct reads bypass those filters.

This is a scope-control gap in inspected code, not evidence of an actual patient-data disclosure; this environment is synthetic.

**Change:** enforce allowed patient/cohort and temporal scope in the tool boundary itself, including SQL and direct document reads. Intentional historical retrieval should be explicit and logged. The model should not be the authority deciding which records are permitted.

Reference: newer worktree `clinical_core/agere/clinical/backstop_agent.py:284–300`.

### 4. Routing still substitutes several judgments for one complete investigation — high priority

The newer candidate is meaningfully better than main: backstop priority, saved-cohort access, document judgments, long-document offsets, quoted-chart handling, and a System One second look address real defects. It is no longer accurate to say every early answer necessarily prevents backstop access.

But the system still combines many proposers, route reorderings, certified exceptions, budget/reserve mechanisms, and distinct acceptance behavior. A population answer reaches the backstop only when the second-look policy flags it; failed or unavailable second-look checks leave the first answer standing. The backstop itself is exempt from that second look.

**Change:** preserve fast paths that establish the entire request, then use one investigation controller for unresolved requirements. Have it operate on typed metrics, document evidence, and a shared result object. Prefer those tools over recursively asking another natural-language planner to interpret a subquestion. Keep experimental routes switchable, but remove them from the default path unless they demonstrate incremental benefit.

References: newer worktree `clinical_core/agere/clinical/proposers.py:591–742`; the October 25 audit acknowledges the remaining need for one investigation flow.

### 5. Evaluation changes prevent a trustworthy progress curve — high priority

Versioned keys and explicit owner decisions are the right response to ambiguous definitions. The recent alignment of event populations, observation stays, pharmacy transactions, and claim-payment basis is substantive work. These should not be described as unresolved old bugs after they have been fixed.

However, changing engines, prompts, keys, scorer, and transport across successive runs makes score changes difficult to attribute. Repeated consultation of the dev3 check half makes it a reused validation set, not an untouched final test. Its chain-preserving hash split also does not guarantee separation by question family or source pattern.

Scorer v2 correctly rejects my swapped-numerator/denominator example that v1 accepts. A remaining reproduction: an absence question expecting “no mortality linkage” is scored correct when the answer instead says a pharmacy field is absent. Specific supported absence is not yet reliably distinguished from a generic unsupported response.

**Change:** freeze definitions, scorer, data, configuration, cache policy and candidate SHA before measurement. Label old check sets as validation. Use a fresh independent final set, with an adjudicator separated from implementation. Re-score saved responses only where available and disclose incomplete rescoring; never silently combine old and new verdicts.

References: newer worktree `tools/dev_set/run.py:178–196`, `:540`, `:735`; `tools/dev_set/halves.py`; `docs/agere/DEFINITIONS_2026-10-25_CODEX.md`.

### 6. Release discipline is relative rather than sufficient — medium/high priority

“No new failures” is useful but does not mean the suite passes. The recent definition release reports 61 failing tests against 69 in its baseline. `runtests_diff.py` compares failure names in selected top-level modules; it does not establish workflow correctness, clinical qualification, or absence of worsening behavior inside an existing failing test.

The control documents, main checkout, candidate worktree, and observed server do not present one obvious current release. This makes independent review and incident recovery unnecessarily difficult.

**Change:** one integrator, an immutable candidate, a release manifest exposed by the server, and unique run directories. Preserve relative regression checks, add a small green mandatory workflow suite, and track known failures explicitly. Each release should identify source/config/definition/scorer versions, actual loaded revision, rollback, and evidence limitations.

### 7. Subscription testing is useful, but not a production economics benchmark — medium priority

The transport flattens messages and tool definitions into CLI prompts, reconstructs tool calls from text, and applies its own timeout/retry behavior. It is a practical experimental adapter, not identical to native Messages API execution. It can change failures, latency, token use, and model behavior.

**Change:** retain it for affordable exploration. Validate final quality and economics on a stable, matched transport. Report cold/warm runs, failed calls, retries, missing usage, and cost per independently correct grounded answer. Do not treat subscription use as evidence that production inference is free.

Reference: `clinical_core/agere/subscription_transport.py:117–182` in the newer worktree.

## The architecture I recommend

Keep the modular application, warehouse adapters, source identity, governed metrics, decision context, deterministic math, and evidence tools. Avoid a wholesale rewrite.

Use one request object carrying original wording, resolved subject, cohort, visit/window, required outputs, definition versions and unresolved parts. Route it through:

1. A certified deterministic path when it proves the complete request is supported.
2. One evidence-capable investigation controller for everything unresolved, with economical model selection and stronger-model escalation based on observed failure.
3. A shared verifier that binds every material claim to executed data or a cited passage, checks completeness and scope, and sends failures back for repair.
4. A final result that distinguishes verified facts, supported absence, unresolved parts and provider failure.

System One fits passage relevance, typed assertions, contradiction screening and closed-list choices. The LLM fits decomposition, tool selection, synthesis and explanation. SQL and governed engines fit computation. No component should be asked to substitute for all the others. Move a stronger model earlier where a matched experiment shows a benefit; do not make every hard question exhaust a long queue first.

## How I would build the next increment

First, freeze the current candidate and repair the acceptance/provenance/scope failures above with adversarial synthetic tests. Second, deliver one complete population/insight workflow and one longitudinal notes workflow through the shared controller, including ambiguous definitions, follow-ups, updates and multi-part output. Third, measure that fixed candidate once against a simple evidence-capable baseline under identical conditions. Report overall correctness, answer precision, coverage, supported-absence accuracy, per-bucket results, p50/p90 and complete cost accounting.

Optimize routing and caching after that evidence identifies the expensive steps. Save validated executable plans with scope, definition and data-version constraints; do not promote a model's answer into deterministic memory merely because it returned successfully.

For the wider product, retain the existing population/VBC, work assignment and follow-up backlog. Qualify complete question-to-decision-to-work workflows separately from answering benchmarks. A strong analytical assistant is an important component of that product, not its completion certificate.

## Assessment by review dimension

| Dimension | Assessment | Reason |
|---|---|---|
| Correctness | High risk; not qualified | Final-route acceptance exception and reproduced claim/evidence contradiction. |
| Security/scope | Needs boundary hardening | Direct record reads lack consistent request-scope enforcement; not a full security audit. |
| Performance/economics | Promising fast paths; unqualified tails | Subsecond familiar medians coexist with roughly 97–136 second p90s in relevant saved runs; incomplete cost records. |
| Maintainability | Consolidation needed | Multiple interpreters, routing policies, acceptance paths and release narratives. |

The important progress is real: executable definitions, stronger evidence tools, explicit accounting, reproducible defects, and increasingly honest evaluation. The best next move is to make those foundations authoritative across every answer path. More components alone will not establish the accuracy target.
