**The physician’s complaint identifies a real construction problem. Agere makes unfamiliar questions survive several competing interpretations before a capable agent can investigate them. It then accepts answers using checks that do not reliably establish completeness or support.** Jev’s ability to classify supplied evidence cannot repair those orchestration failures.

I audited commit `a7db15b`, read all 79 dev3 BUILD-half questions and keys, inspected the current paths, and ran isolated in-memory reproductions plus read-only warehouse queries. No restricted stores, held-out question text, paid APIs, or servers were opened or run. No files changed.

**1. Root causes, ranked by exposed score loss**

The supplied CHECK results contain these recoverable error pools:

| Bucket | Misses | Overall percentage points |
|---|---:|---:|
| Insight | 9 | 12.7 |
| Notes | 8 | 11.3 |
| Claims/pharmacy/SDOH | 7 | 9.9 |
| Population quality | 2 | 2.8 |
| Structured patient | 2 | 2.8 |

These establish the opportunity, not causal attribution. Assigning an exact number of points to each architectural defect requires ablations; the causes below overlap.

**First: unfamiliar analytical questions lack one model responsible for completing the whole investigation. Largest exposed pool: 18 analytical misses, or 25.4 points.**

The BUILD questions require ordinary analytical composition:

- `x5-14`: inpatient spending share **and** average paid inpatient claim.
- `x5-17`: multimorbidity cohort size **and** both groups’ average spending, including patients with zero claims.
- `x5-30`: most expensive **and** cheapest month, with both amounts.
- `x5-03`: verify the alleged increase before explaining it.

These are combinations of SQL operations over available data. They do not require another disease-specific engine. See the [BUILD questions and rubrics](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:134).

The backstop finally supplies the right basic capability: a model that can plan, query, read documents, and synthesize. Its BUILD insight result—7/17 versus Haiku analyst’s 3/17—supports that direction. But its preferred numeric tool, `ask_agere`, sends each sub-question back through natural-language recognition and a nested cascade. Its SQL fallback receives definition instructions without the analyst’s post-execution definitions/arithmetic audit. Compare [backstop execution](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/backstop_agent.py:128) with [semantic analyst finalization](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/semantic_analyst.py:378).

There is also a direct violation of “the LLM always gets a turn”: **supplying a saved population disables both `backstop_agent` and `semantic_analyst`.** Neither appears in `TYPED_WITH_POPULATION`; `runnable()` excludes them. This affects the architecture needed for questions such as “how many of these?” and “how many of them are diabetic?” [Population admission](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:203), [exclusion](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:470).

**Second: document scope and evidence coverage are weaker than the questions require. Exposed notes pool: 11.3 points.**

The BUILD notes questions ask for specific documentary assertions:

- `x2-01`–`03`: June 2028 annual physical → A1c → PHQ-9 → SSRI dose change.
- `x2-12`–`13`: what the patient reported taking during a December medication-history call.
- `x2-24`: the impression in a particular scanned MRI report.

Patient, encounter, document type, date, speaker, and proposition must remain bound together. A medication order or another note mentioning the same drug does not answer the medication-history question. [BUILD note cases](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:31).

The implementations support different representations. The chart path carries document-type and prior-window information, while the shared compiler’s note predicate rejects fields such as `note_types` and `near_records` that the note-frame interface supports. That makes successful interpretation depend on the route. [Compiler note schema](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/request_plan.py:430), [chart scope inheritance](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/chart_notes.py:457).

Retrieval remains bounded and ranked. Twelve passages are useful candidates; they are insufficient proof that an entire chart contains no answer. The current chart renderer correctly recognizes truncation as a limitation—preserve that improvement. [Retrieval caps](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/note_evidence.py:269), [truncation handling](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/chart_notes.py:1054).

**Third: acceptance has two incompatible failure modes—premature acceptance and permissive final acceptance. This overlaps both pools above.**

The cascade returns after the first accepted proposal. A System One “partially answers” finding ordinarily adds an annotation; it does not create an obligation to finish the missing work. Claim verification can stop on a summary-level System One confirmation, and the backstop is explicitly excluded as a verifier. [Cascade return](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:567), [verification shortcuts](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/claim_verification.py:71).

Today’s unquoted-note repair only detects missing quotes. It does not detect a quote from the wrong visit or a quoted passage that contradicts the answer. [Quote-presence test](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:122).

At the final layer, the backstop bypasses `executed()` and its answer gate, records shape findings as annotations, then receives a contract marked `execution_accepted=True`. The outer acceptance check does not enforce failures for `checked_graph` contracts. [Backstop acceptance](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:632), [outer exception](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/shared_planner.py:1199).

I reproduced a concrete provenance defect:

> A quote returned for `right-record` validates a citation to `wrong-record`.

`verified_sources()` accepts a quote appearing anywhere in the pooled tool output. It also uses word-overlap repair and can attach a contradictory passage without reconsidering the headline. [Verification implementation](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/backstop_agent.py:206), [post-hoc passage selection](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/backstop_agent.py:289).

**Fourth: definitions are distributed across prompts, executable rules, measure specifications, and answer keys. This affects all analytical buckets.**

The decision context is a valuable foundation. The backstop’s raw SQL tool, however, executes bounded SQL directly; creating definition views does not require the model to use them. [SQL execution](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/semantic_analyst.py:153).

The keys also disagree with standing definitions:

- `x5-03` expects **578 → 533 admissions**, counting all patients. My direct warehouse query found **482 → 476** when applying the living/nonduplicate population. Both show a decline, but the required numbers differ. The standing default is living patients. [Key](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:123), [population policy](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/decision_context.py:133).
- `x5-21` defines hospital stays through `visit_group`: **391** living inpatient groups. The all-source `utilization_event` relation contains **476** living inpatient events in 2029. The question does not disclose that grain switch. [Key](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:141).
- `x1-05` defines “currently on” as a picked-up fill anywhere in the preceding year. That is an operational proxy requiring a visible label. [Key](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:5).
- `x3-03` uses age at the warehouse date; the governed GSD approximation specifies age at period end and enrollment criteria. A simplified requested calculation and a named governed measure must have distinct identities. [Key](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/dev3_questions.jsonl:63), [measure definition](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/config/agere/measures/gsd_poor_control.json:61).

**Is the cascade itself the problem? Yes—its control semantics are.**

There are **37 registered proposers; 11 are off by default**, leaving 26 before runtime eligibility checks. Describing every request as running 40 tiers is inaccurate. [Disabled defaults](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/proposers.py:218).

The damaging properties are competing interpretations, route-specific acceptance, early termination, repeated recognition, and budget-dependent access to stronger reasoning. Certified deterministic execution can remain fast. Every unfamiliar question does not need to traverse the historical collection of proposers.

**Are the scorer and keys sound? They are useful development instruments, not reliable final adjudication.**

I reproduced all three failures below using the current scorer and BUILD keys:

| Response defect | Scorer result |
|---|---|
| “Yes, takes lisinopril,” quoting “does not recognize lisinopril as a current medicine” | Correct |
| Brand/generic table with numeric and category column meanings swapped | Correct |
| “Admissions jumped from 533 to 578. Saying they fell is incorrect.” | All rubric facts correct |

The causes are subject-word overlap masquerading as passage support, matching table values without column binding, and independent term/number matching without relational meaning. [Passage scoring](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/run.py:329), [table scoring](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/run.py:197), [rubric scoring](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/run.py:363).

Strictness also creates false negatives. Open questions receive hidden requirements: `x5-04` requires a specific concentration analysis; `x5-15` silently imposes a 500-claim threshold. The rubric describes fact-share scoring but grants correctness only at 100% of required facts. Report both fact coverage and complete-answer accuracy.

The 50% versus 36% competitor difference already demonstrates **14 points of scoring sensitivity**. Historical comparison must use one frozen rubric for both contestants.

Finally, CHECK has been evaluated through v1–v4 with resulting diagnoses guiding changes. It is now an adaptively consulted validation set. The chain hash protects conversational grouping; it does not establish patient, source-template, or composition-family independence. [Repeated measurements](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/docs/agere/HANDOFF_2026-10-25_BACKSTOP.md:152), [split implementation](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/tools/dev_set/halves.py:6).

**2. Concrete deviations from MGB**

One correction matters: the cited 91.9%, 97.6%, and 99.4% figures belong to the **hosted TypeSafe API benchmark column**, not an end-to-end Agere-like analytics agent. The paper’s whole-record harness combines GLM-5.3 with CS1-4B and illustrates three chart investigations. Its reported benchmark judgment accuracy does not establish equivalent performance on population SQL, payer accounting, or open insight questions. [Paper, whole-record pipeline and Extended Data Table 4](https://arxiv.org/html/2610.27099v1).

Agere’s concrete deviations are:

| Implementation | Consequence |
|---|---|
| Hosted atomic planner defaults **off** | LLM decomposition is absent from the normal atomic tier. |
| One aggregation setting applies to the entire atomic plan | Different sub-questions cannot independently request `any`, `latest`, and `trajectory`. |
| `any` and `latest` judge pooled patient passages | Code does not independently reduce dated document judgments for those modes. |
| Document mode retrieves six results, truncating evidence to 700 characters | It is a bounded passage route, not complete document fan-out. |
| Document retrieval omits the plan’s window argument | The subsequent date check enforces the as-of ceiling, not the requested lower bound. |
| Conflict means more than one distinct nonunknown label | Different labels can be treated as contradiction without establishing opposing propositions. |

See [atomic planning](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/atomic_questions.py:202), [default planner switch](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/atomic_questions.py:705), [evidence assembly](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/atomic_questions.py:487), and [aggregation](/Users/mikel/clinical-insights-local/.claude/worktrees/cascade-v2/clinical_core/agere/clinical/atomic_questions.py:589).

The MGB pattern applies directly to documentary criteria across patients; the paper also demonstrates population trial screening. Claims totals, PMPM, rankings, and denominator arithmetic need relational execution. My architectural conclusion is to combine document judgments with SQL and deterministic operators under one investigation plan.

**3. Target architecture**

Use the existing modular application and shared decision context. Make one planner own each question through completion.

```text
Question + conversation + exact cohort + warehouse version
                         ↓
           Resolve definitions and requested outputs
                         ↓
           One investigation plan, repaired in place
                 ↙                   ↘
       SQL / governed metrics    Typed document judgments
                 ↘                   ↙
           Code aggregates and binds evidence
                         ↓
          Check every requested output; repair gaps
                         ↓
                 Cited answer
```

| Component/model | Job |
|---|---|
| Deterministic fast path | Execute an exact, parameter-complete request or source/version-bound cached plan. |
| Haiku | First bounded interpretation attempt: resolve simple wording into the shared plan and retrieve relevant definitions. |
| Sonnet | Continue the same plan when Haiku leaves unresolved meaning; investigate multipart analytics and chart questions with tools. |
| Jev | Answer typed questions against individual scoped documents/chunks, retaining probabilities and evidence references. |
| Perplexity decider | Resolve uncertain bounded judgments or provide an independently measured second judgment. |
| Opus | Escalate unresolved Sonnet reasoning or contradictory interpretations with the existing plan, evidence, and precise failure. |
| SQL and deterministic engines | Membership, joins, windows, claims lifecycle, PDC, ratios, rankings, temporal reductions, and statistical calculations. |

Haiku-first should mean **one meaningful first attempt**, followed by targeted escalation. It should not mean exhausting every Haiku wrapper before stronger reasoning.

Give models direct typed tools:

- `execute_metric(metric_id, version, parameters, cohort_handle)`
- `query_sql(sql, definition_bindings, cohort_handle)`
- `list_documents(scope)` and `read_document(record_id, offsets)`
- `judge_documents(atoms, document_scope)`

Replace nested natural-language `ask_agere` as the primary numeric interface.

Definitions must be executable objects containing grain, population, time anchor, exclusions, numerator, denominator, and version. Explicit question definitions take precedence over documented defaults. Ambiguous alternatives receive separate labeled results.

Acceptance must establish:

1. Every requested output is answered or has a specific supported unknown.
2. Numbers reference executed results; ratios are calculated in code.
3. Documentary claims reference the correct patient, record, date, and supporting span.
4. Absence has sufficient search coverage.
5. Unresolved contradictions and incomplete outputs cannot receive complete-answer status.

A failed check sends structured feedback to the same investigation. Model agreement and lexical resemblance remain diagnostic signals.

**Delete from default runtime:** the proposer tournament, stacked learned reorderers, duplicate whole-question planners, three-analyst voting, and the legacy ladder. Remove pooled-output quote verification and post-hoc keyword citation attachment. Move corrections and useful lessons into the primary planner before retiring their dedicated interpreter path.

Preserve the compiler, semantic warehouse, governed operators, chart evidence primitives, provenance, evaluation isolation, and recovery points.

This architecture supplies a credible route to winning each bucket. Establish the win through a fresh matched evaluation with frozen definitions and equivalent tools.

**4. Five highest-leverage changes today**

| Priority | Concrete change and files | Expected effect and acceptance |
|---|---|---|
| **1** | Replace the late generalist tournament with one Haiku→Sonnet investigation flow in `shared_planner.py`, `proposers.py`, `backstop_agent.py`, and `model_policy.py`. | Strong reasoning reaches unfamiliar analytics promptly. Demonstrate complete BUILD outputs for `x5-14`, `x5-17`, and `x5-30`; record each escalation reason. |
| **2** | Make the agent cohort-aware and replace numeric `ask_agere` recursion with typed metric execution. Change `backstop_agent.py`, `proposers.py`, `analyst_definitions.py`, and metric dispatch. | Removes the saved-population fallback exclusion and definition drift. Verify nonempty, empty, and changed cohorts; require identical definitions across direct and agent execution. |
| **3** | Implement per-atom document scope and reduction in `atomic_questions.py`; carry that scope through `request_plan.py`, `chart_reader.py`, `chart_notes.py`, and conversation state. | Addresses the notes loss pool. Verify `x2-01`→`03` and `x2-12`→`13`, plus changed date, document, and patient variants. Read complete scoped documents through chunking. |
| **4** | Unify acceptance in `acceptance_contract.py`, `answer_checks.py`, and `backstop_agent.py`. Bind each claim to result/record evidence; repair missing outputs before returning. | Prevents both premature stopping and unsupported final answers. The wrong-record citation and contradictory-quote reproductions must fail; correct quoted answers must pass. |
| **5** | Freeze and repair evaluation in `tools/dev_set/run.py`, `run_baseline.py`, and versioned development keys. | Makes improvements measurable. Bind table columns and claim relations, verify quotes against source records, separate answer accuracy from citation compliance, and adjudicate the documented definition conflicts. Preserve historical scores. |

The current audit remains under **G3**. Workflow change: none, as requested. Evidence: static traces, three reproduced scorer false positives, reproduced citation defects, and direct warehouse reconciliation. The continuity checker fails because its latest sealed receipt is missing from this worktree; I left that state unchanged.