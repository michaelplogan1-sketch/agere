# Agere independent technical review

**Verdict: Claude identified important symptoms, but the proposed architectural reversal is wrong.** Agere needs one reliable interpretation and execution contract. Replacing the cascade with whole-roster Jev judging would scale several existing semantic errors.

Reviewed main at `8c159cd` and the changing `claude/s1-fanout` worktree. Fan-out references below correspond to the inspected draft, including `system_one_fanout.py` hash `0e106399…`. No files changed; no benchmark, server, local model, or paid API was run. Protected question text was not inspected.

## 1. Is Claude’s diagnosis right?

**Right:** the current System One interfaces are too restrictive; evidence gathering is inefficient; component expansion has outpaced demonstrated workflows; measurement loses essential diagnostics.

**Wrong or incomplete:**

- **MGB is also planner-first.** Its LLM plans typed questions from a document manifest; CS1 reads documents; code aggregates; the LLM writes citations. The useful distinction is *bounded planning versus repeated record reading*. “Judge-first” misdescribes the reference architecture. Its 690-fold token reduction concerns trial screening and excludes CS1 computation and verification/judge calls. [Paper, sections 0.6, 0.7 and 0.14](https://arxiv.org/html/2610.27099v1)
- **The grammar is not the final barrier.** A grammar failure explicitly invokes the proposer cascade. The returned error prepends that initial grammar failure, so its wording does not identify the actual downstream bottleneck. [shared_planner.py:1001](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/shared_planner.py:1001)
- **The atomic path does not normally attempt 4,100 patients.** It defaults to 25, with a hard maximum of 200. The “30 minutes over the whole panel” explanation extrapolates beyond its implemented execution contract. Serial evidence collection is real; the claimed timings and “always times out” were not independently established here. [atomic_questions.py:81](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:81)
- **The checks do more than refuse. Some fail open, and rejected or conflicting answers can still be returned.** This directly contradicts “never ship wrong answers.”
- **Zero direct System One winners does not mean zero System One contribution.** It also routes and confirms compiler answers. Measure its incremental coverage, error reduction and avoided LLM cost through ablation.

The historical aggregate score confirms **5 correct answers among 16 answered: 31.25% precision, with 11 wrong answers**. The reported 24 correct includes 19 correct abstentions. That is the clearest existing product result—not the component count. [Aggregate score](</Users/mikel/clinical-insights-local/bench/results/truth/healthcare-warehouse-v2.8/agere-defined-free-tier-dd2b2df-20261023T064022Z/score.txt>)

## 2. Why System One answers almost nothing, ranked

These are ranked structural findings, not measured causal attribution across the sealed questions.

| Rank | Reason | Code evidence |
|---|---|---|
| **1** | **It cannot express ordinary questions.** The metric chooser excludes metrics containing population or numeric slots. The engine chooser fills limited enums and integers, but supplies no general population predicate. Plain counts and lists become `literal_lookup`, which returns no engine plan. | [s1_governed.py:144](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/s1_governed.py:144), [operator_router.py:269](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/operator_router.py:269) |
| **2** | **Routing uses a conflicting prompt contract.** The chooser supplies `evidence=[]`, while the shared provider instruction says to use dated source evidence and choose unknown when evidence is absent. This is a plausible contributor requiring development-response inspection—not evidence that Jev cannot route. | [s1_governed.py:82](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/s1_governed.py:82), [providers.py:108](/Users/mikel/clinical-insights-local/clinical_core/providers.py:108) |
| **3** | **General decomposition is effectively unavailable on the cheap atomic path.** Hosted decomposition defaults off. Templates or local Qwen must produce the plan. The atomic schema also lacks an explicit Boolean expression combining criteria; aggregation selects patients matching **any** selected atom. | [atomic_questions.py:676](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:676), [atomic_questions.py:597](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:597) |
| **4** | **The record-reading executor is not a population executor.** It takes the first sorted patients, gathers evidence serially for every patient/atom, then starts the decision pool. Default concurrency is four. Faster inference cannot repair that preparation path. | [atomic_questions.py:440](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:440), [atomic_questions.py:481](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:481) |
| **5** | **Saved-cohort questions disable the System One metric and operator routers entirely.** They are absent from the population-capable allowlist, so ordinary follow-ups cannot use them. | [proposers.py:120](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/proposers.py:120), [proposers.py:333](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/proposers.py:333) |
| **6** | **The development loop cannot reliably explain failure.** HTTP errors discard Agere’s structured body; benchmark rows discard most successful-response diagnostics too. Paid attempts, rejection causes and learning effects become difficult to reconcile. | [adapters.py:25](/Users/mikel/clinical-insights-local/bench/adapters.py:25), [truth_run.py:156](/Users/mikel/clinical-insights-local/bench/truth_run.py:156) |

Generating thousands of metrics expands a menu; it does not supply compositional meaning. The candidate generates mostly single-table categorical counts, shares and breakdowns, chooses grain and clocks heuristically, and validates SQL with `EXPLAIN`. Those are useful discovery aids, not qualified clinical definitions. [generated_metrics.py:308](/Users/mikel/clinical-insights-local/.claude/worktrees/overnight/clinical_core/agere/clinical/generated_metrics.py:308), [generated_metrics.py:533](/Users/mikel/clinical-insights-local/.claude/worktrees/overnight/clinical_core/agere/clinical/generated_metrics.py:533)

**Correctness defects must precede coverage work:**

- `system_one.decide()` returns a best label with `decided_by=None` when neither provider clears its threshold. The atomic caller does not require acceptance before using that label. [system_one.py:151](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/system_one.py:151), [atomic_questions.py:417](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:417)
- Atomic aggregation does not propagate unresolved patients into count uncertainty. An isolated, invented-input probe of the actual answer functions returned **`answered`, count 0** when the sole patient was unknown. [atomic_questions.py:603](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/atomic_questions.py:603)
- Both System One families can “confirm” an answer without a minimum positive confidence threshold. They inspect a truncated plan/result summary, not independently verified source facts. Check exceptions generally annotate and continue. [system_one_cross_check.py:30](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/system_one_cross_check.py:30), [answer_checks.py:1061](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/answer_checks.py:1061)
- Provider-outage fallback chooses among executed-but-rejected candidates. Unresolved disagreement between independent proposers retains the first answer with low confidence. A warning does not make its number correct. [proposers.py:498](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/proposers.py:498), [claim_verification.py:163](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/claim_verification.py:163)

## 3. Architecture I would build

**Keep the modular application. Replace competing interpretation paths with one typed contract and two execution backends: SQL and evidence judgment.**

| Order | Component | Responsibility |
|---|---|---|
| **1** | Shared decision context | Original question, exact saved population, chart/person grain, event window, received-data cutoff, source and definition versions. Empty population remains empty. |
| **2** | Interpretation | Verified contract reuse and deterministic parsing first. System One selects bounded operators, concepts and enums; code binds literal values. An unfamiliar composition gets one small LLM planning call. |
| **3** | Compiler | Produce one explicit expression tree: population, numerator, denominator, AND/OR/NOT, temporal relations, grouping and requested outputs. Reject unresolved meaning before execution. |
| **4** | Hybrid executor | SQL computes structured facts, joins, dates, arithmetic and denominators. System One evaluates only predicates requiring interpretation of documentary evidence. |
| **5** | Reduction and answer | Code combines typed judgments, preserves unknowns/conflicts, computes results and attaches original evidence. Templates render routine answers. Optional LLM prose sees only the checked result and citations. |

**How data reaches System One:** batch-read versioned source rows and document manifests; select evidence using the compiled criterion and its time scope; create bounded document chunks with stable record IDs and spans. Send each chunk plus an atomic question and explicit options. Cache by criterion, evidence, model and policy versions. Reduce judgments across documents and patients using explicit code.

A patient summary can help find evidence. It cannot certify that omitted evidence does not exist.

**Initial LLM ceilings to test:** ≤4,000 input tokens and ≤1,000 output tokens for planning; at most one bounded repair. Optional narrative: ≤2,000 input and ≤300 output. No LLM reads the whole population. “A few hundred tokens” should be a measured outcome for simple questions, not a universal requirement.

**Correctness:** guarantee engineering invariants within declared contracts—scope, arithmetic, clocks, provenance and uncertainty propagation. Model interpretation cannot have an absolute correctness guarantee. If `K` patients meet a criterion and `U` remain unresolved, return a bounded count or an explicitly incomplete result; do not present `K` as the exact total. Missing coverage, failed mandatory checks or unresolved contradictions prevent a definitive answer.

**Keep / merge / remove from the default path:**

- **Keep:** source adapters, semantic relations, governed analytical engines, request compiler, durable decision context, provenance, accounting and dependency invalidation.
- **Merge:** `request_language` and `local_compiler` outputs into the same contract; metric/operator/atomic routing into one planner interface; duplicate checks into one acceptance policy.
- **Retire from default execution after comparison:** overlapping Qwen rewrite/normalizer/free-SQL/bottled routes, repeated whole-plan LLM proposers, three-analyst voting and the legacy ladder. Preserve experiments and recovery points.
- **Keep generated metrics as candidates**, not automatically governed definitions.

## 4. Fan-out review: useful infrastructure, unsafe default

The draft improves concurrency, caching and handling of below-threshold decisions. **I would not activate it as the primary answer path.**

| Problem | Specific finding and required change |
|---|---|
| **Admission is broken** | The criterion lives in `question` and `description`; `executable_text()` explicitly excludes both. The fan-out pre-check therefore cannot see ordinary concepts and thresholds. Emit a typed criterion contract; do not bypass the check. [literal_check.py:50](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/literal_check.py:50), [proposers.py:751](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/proposers.py:751) |
| **Population can broaden** | `scoped or store.all_ids()` turns an empty cohort into everyone. Preflight can use a supplied subset, but the emitted graph still declares all patients. Bind the exact population into both execution and replay. [system_one_fanout.py:375](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:375), [system_one_fanout.py:568](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:568) |
| **Shares have the wrong denominator** | “What share of patients with diabetes have …?” divides matches by the entire input roster. Population and numerator must be separate expressions. [system_one_fanout.py:524](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:524) |
| **Lossy sheets are declared complete** | The prompt says every diagnosis, fill, lab, visit and need is listed. The sheet builder deliberately drops negative screens, medication/barrier lines and labs; historical detail is compressed. Absence cannot support “no.” Preserve completeness metadata and fetch omitted evidence when required. [patient_sheets.py:299](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/patient_sheets.py:299), [system_one_fanout.py:170](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:170) |
| **Prefilter “proof” is lexical** | `positive_part("patients with diabetes or no CKD")` becomes `"patients with diabetes or"`. Word coverage can then certify a diabetes filter that wrongly excludes the other OR branch. Derive necessary conditions from the expression tree. [system_one_fanout.py:105](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:105) |
| **The SQL guard tolerates wrong answers** | It accepts differences up to three patients or 10%; an isolated probe confirms **0 versus 3 passes**. Equal counts can also hide completely different patient sets. Its reference can come from a previously rejected candidate. Compare exact memberships against an independently qualified oracle. [system_one_fanout.py:471](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:471), [proposers.py:759](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/proposers.py:759) |
| **Citations stop at summaries** | Output invents record references such as `patient#D1` and dates them at the snapshot. Retain mappings to actual records, fields, dates and supporting spans. [system_one_fanout.py:560](/Users/mikel/clinical-insights-local/.claude/worktrees/agent-ab6bfbfa411cf1538/clinical_core/agere/clinical/system_one_fanout.py:560) |

Using the supplied economics, 4,100 single judgments cost approximately **$0.107**, with an ideal throughput floor of **7.8 seconds**. Eight criteria become approximately **$0.85 and 62 seconds**, before additional documents, fallback calls, retries or preparation. These are arithmetic projections, not measured fan-out performance.

Three to five development questions and a 3,000-call cap cannot establish full-roster correctness. Whole-roster fan-out belongs on genuine documentary criteria; direct SQL should answer structured questions first.

## 5. Next 1–2 weeks and trustworthy measurement

These are proposed acceptance milestones, not implementation claims.

| Period | Delivery | Measurable acceptance |
|---|---|---|
| **Days 1–2** | Repair acceptance semantics and telemetry | No definitive answers from rejected candidates, unresolved provider labels or failed mandatory checks. Every outcome retains stage diagnostics, uncertainty and complete/explicitly-unknown cost. |
| **Days 3–4** | One structured interpretation path | At least 50 independently authored development questions across clinical, access, utilization, financial and VBC domains match independent SQL—including exact patient sets, denominators and clocks. |
| **Days 5–7** | One qualified System One evidence path | Independently labeled development/calibration/validation records cover negation, temporal changes, contradictions and missing evidence. Measure accepted-answer precision, abstention, retrieval omissions and citation support separately. |
| **Days 8–14** | Broad composition and connected workflow | Fresh validation questions exercise mixed SQL/document predicates, provider/payer breakdowns and saved-cohort follow-ups. Target ≥70% answerable-question coverage, zero observed incorrect definitive answers, routine p95 ≤5 seconds and declared evidence-workload p95 ≤30 seconds. Failed targets keep the candidate unpromoted. |

The measurement loop needs these changes first:

1. **Isolate evaluation from learning.** The benchmark calls the normal compose endpoint. Success hooks append training traffic; failure hooks append development gaps. No evaluation exclusion is visible in that path. This permits contamination without reading answer keys. Treat existing traffic-derived artifacts as suspect pending a provenance audit; this review did not determine its extent. [adapters.py:79](/Users/mikel/clinical-insights-local/bench/adapters.py:79), [learning_hooks.py:222](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/learning_hooks.py:222), [learning_hooks.py:270](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/learning_hooks.py:270)
2. **Use new development material only.** Independently author questions and source-grounded oracles from permitted synthetic data. Split by patient/source family and question composition. Do not turn sealed failures or stored sealed prompts into development examples.
3. **Freeze the actual contestant.** Pin served code, warehouse, definitions, model revisions, caches and learning state. Use disposable state per variant. Reject resume under a different manifest; current resume reuses rows by question ID without that check. [truth_run.py:233](/Users/mikel/clinical-insights-local/bench/truth_run.py:233)
4. **Compare against simple baselines.** SQL alone; SQL plus one bounded planner; then add System One documentary predicates. Require a measured improvement before adding another tier.
5. **Report separate metrics:** precision on definitive answers, coverage on answerable questions, supported abstention, exact membership, unknown counts, latency, cold/warm cost and human review burden. Include confidence intervals.
6. **Account for every attempt and background call.** The inspected historical runs have 139–140 unknown-cost rows out of 147; their total cost is explicitly unknown. Tiny known spend does not establish token economics. [Cost summary](</Users/mikel/clinical-insights-local/bench/results/truth/healthcare-warehouse-v2.8/agere-defined-free-tier-dd2b2df-20261023T064022Z/cost.json>)

The sealed bank should remain outside this development loop.

## 6. What Mike must hear plainly

**“Every component must be on the question path” is an engineering liability.** Require evidence that each default component improves a user outcome. Wiring and passing component tests do not establish usefulness.

**“Never wrong” requires withholding definitive claims when support is insufficient.** Neither confidence scores, two-model agreement nor citations guarantee truth. Even 99.9% per-patient accuracy implies about four errors per 4,100 judgments on average.

**The broad product is still population health, VBC, financial/provider analysis and accountable work.** A fast patient-sheet classifier does not replace those analytical contracts.

**Two weeks can establish a trustworthy foundation—not commercial superiority.** Judge progress by correct completed workflows and cost per correct answer. The immediate objective is to make one shared path dependable, then expand its measured coverage.