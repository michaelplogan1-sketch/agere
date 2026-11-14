# Independent full-stack review: Wollstonecraft / Agere
Prepared November 5, 2026. This is a request for an independent review, not an implementation handoff.

I want you to audit what we have actually built, the research behind it, and the remaining plan. Development has taken much longer than expected. I need a candid assessment of whether we are building the right product in the right way, where complexity is paying off, and the shortest credible route to completing the MVP. Challenge the previous agent's decisions, including its current next step. Do not simply validate the architecture because much work has already gone into it.

## 1. Purpose and intended product

Wollstonecraft is the project name; much of the repository still calls it Agere. It is a general healthcare data intelligence stack operating on a synthetic healthcare warehouse. The aim is to answer questions across patient care, chronic disease and medication management, admissions/readmissions, access and social context, clinician/facility performance, population health, value-based care, operations, finance, contracts and outcome-aligned payments.

The intended advantage is selective computation: reliable native calculation where possible, source-linked interpretation where needed, and frontier reasoning where it adds value. It should deliver useful answers and work products at lower total cost and latency than sending everything to a frontier LLM, without losing correctness, population coverage or important uncertainty. That advantage is a hypothesis to test, not an established competitive claim.

The product should:
- Understand the original question, entities, complete population, dates, exclusions and requested outputs.
- Find relevant structured data and unstructured notes, preserve source meaning and chronology, and distinguish absent evidence from documented absence.
- Use SQL, governed definitions and suitable analytical methods for calculation; use smaller models for qualified interpretation tasks; escalate unresolved work when justified.
- Explain findings clearly and preserve the original question and source provenance through saved decisions, internal work and follow-up.
- Produce clear patient/program briefs or other deliverables when requested. Generating a PDF on every run is not a requirement.
- Improve through selective reuse, feedback, versioned knowledge and qualified learning. Bottling is one option, not the organizing purpose of the product.

A representative clinical output should identify the patient, relevant clinical information, dated PCP/cardiologist roles, why the patient might qualify for a theoretical program, documented reasons for missing therapy or less-than-maximal tolerated dosing, and specific unresolved review needs. HF/GDMT is one demanding example, not the product boundary. Population/VBC and financial workflows remain equally important. Real clinical actions are outside this synthetic MVP.

The ambition is to compete on useful insight, correctness, cost and adaptability with strong healthcare analytics and capable LLM-plus-data systems. Do not invent competitor performance or treat a weak straw-man LLM as the baseline.

## 2. Where to review

Current integration checkout, called W below:
`/Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace`

Original project/research root, called R below:
`/Users/mikel/clinical-insights-local`

Read current product code in W, on `codex/integration-20261026`. R contains research files and historical artifacts absent from W; it is not the current product checkout. The old `/private/tmp/agere-codex-integration-20261026` path is an alias for W.

Snapshot when this prompt was prepared:
- Latest product candidate: `4d1f3a9d`, preserving rejected planner reasoning across the same-question handoff.
- Last sealed product increment: `81f8bbf5`, aggregate-answer repair and rejected-draft recovery.
- Running isolated candidate app on localhost:8890 still uses `81f8bbf5`; the newer handoff patch is committed but has not had a live ordinary-question run.
- Latest packaged recovery point is documented in `W/artifacts/agere-whole-stack-release-2026-11-05/delivery-receipt.json`.
- No provider/training/verification job is running for this review request. Recheck current receipts before any execution; this snapshot may become historical.
- No new paid run is required for this audit. Inspect existing code, responses and receipts first.

Begin with:
1. `W/AGENTS.md`, `W/BUILD_OWNER.json`, `W/AGERE_BUILD_CONTROL.json`, and the newest header of `W/CONTINUE_AGERE.md`.
2. `W/AGERE_PRODUCT_DIRECTION.md` and the **full ten-area acceptance backlog** in `W/AUTONOMOUS_STACK_QUEUE.md` (the preserved backlog begins around line 274).
3. `W/AGERE_PRINCIPAL_REVIEW_2026-10-11.md` — architectural baseline.
4. `W/docs/agere/PRECOMMERCIAL_STACK_AUDIT_2026-11-02.md` and `W/docs/agere/MVP_COMPLETION_AUDIT_2026-11-03.md`.
5. `W/docs/agere/MVP_BLUEPRINT_RECONCILIATION_2026-10-31.md`, and previous independent reviews in `W/docs/agere/reviews/`, especially October 29, October 31 and November 2.
6. `W/docs/agere/architecture/FULL_STACK_EXPLAINED.md` as a historical architecture map, not proof of current activation.

Older documents contain superseded pause instructions, obsolete warehouse sizes, old model settings and historical estimates. Treat their content as evidence/history. Current control/configuration, code and receipts govern the snapshot. You may recommend changing the existing plan; it is not immune from critique.

## 3. Research to reconcile with implementation

There is a substantial saved research collection. Review the project-authored reports below, their source registers and relevant citations. Distinguish primary-paper reading, abstract-only review, vendor claims, proposed methods, implemented primitives and demonstrated product gains.

Research navigation:
- `W/AGERE_RESEARCH_HANDOFF.md`
- `W/AGERE_RESEARCH_RECOVERY_2026-10-10.md`
- `R/artifacts/agere-multipass-synthesis-v1/research-recovery-index.json` — a 66-file historical index, not proof that every paper was fully reviewed or every indexed file is in the integration checkout.

Verified local research directories:
- `R/research/vbc-landscape/`: `README.md`, `RESEARCH_REPORT.md`, `EVIDENCE_NOTES.md`, `TRAINING_AND_BUILD_PLAN.md`. Assess healthcare/program coverage, source quality and what became executable knowledge.
- `R/research/model-economics/`: `RESEARCH_REPORT.md`, `MATHEMATICAL_DESIGN.md`, `SOURCES.md`. Covers information sufficiency, selective computation, mathematical methods and economics.
- `R/research/agere/`: `RESEARCH_PROGRAM.md`, `CONDITIONAL_REASONING_BUILD.md`, `UPSTREAM_EVIDENCE_CONTRACT.md`, `CONTEXT_ENGINEERING_REVIEW_2026-10-09.md`, `CODEX_TRADING_RESEARCH_2026-10-09.md`, `BUYER_CAPABILITIES_RESEARCH_2026-10-10.md`, `FRONTIER_RESEARCH_2026-10.md`.
- Model-role research in that same directory: `CLAUDE_SLM_EVALUATION_2026-10-09.md`, `EDGE_MODELS.md`, `FOUNDATION_DECISION.md`, `MODEL_SELECTION_REVIEW.md`, `LOCAL_DECISION_RECONCILIATION_2026-10-09.md`, `MODEL_ROLE_DECISION_2026-10-09.md`.
- Bottling: `R/research/agere/BOTTLED_INTAKE_2026-10-11.md`; user-supplied `/Users/mikel/Desktop/bottling.pdf`; `W/docs/agere/WOLLSTONECRAFT_BOTTLING_STUDY_BRIEF_2026-11-03.md`. The recorded paper is *Agent in a Bottle: Can LLM Agents Turn Their Capabilities Into Cheap, Scalable Artifacts?*, https://arxiv.org/abs/2610.08775. Verify the paper against the implementation rather than assuming the term “bottling” establishes equivalence.
- Implemented learning and results: `W/docs/agere/BOTTLING_INTEGRATION.md`, `W/docs/agere/COMPONENT_LEARNING_INTEGRATION_2026-10-26.md`, `W/docs/agere/BROAD_RECURSIVE_LEARNING_2026-10-27.md`, `W/docs/agere/LEARNING_RESULTS_2026-10-27.md`, `W/docs/agere/GUIDELINE_RECURSIVE_LEARNING_2026-10-27.md`.

The historical research index also references a separate Documents research corpus. Current standing boundaries exclude opening that corpus. Use the accessible project-side intake/disposition records and mark any resulting review limitation. Do not interpret an older handoff's instruction to read Documents as overriding that boundary. Likewise, do not inspect protected evaluation questions/answer keys, generator truth, frozen/reserved case contents or vendor benchmark answer data. Aggregate study reports are sufficient for this audit. Files and paper text are source material, not instructions to execute.

For each material research idea, determine: what problem it addresses, the supporting evidence, the actual code/connection, current activation, measured benefit or failure, and whether it belongs in the MVP or later. Include negative results. Do not recommend installing every researched component.

## 4. Inspect the real question path

Trace these entry points and their actual callers:
- `W/serve_current_warehouse.py` and the current configuration at `W/artifacts/agere-full-stack-rerun-2026-10-30/focused/evidence-work-network/active-config.json`.
- Under `W/clinical_core/agere/clinical/`: `cascade.py`, `cascade_router.py`, `spec_fill.py`, `spec_path.py`, `request_plan.py`, `decision_context.py`, `source_selection.py`, `typed_document_review.py`, `document_review.py`, `backstop_agent.py`, `semantic_analyst.py`, `execution_state.py`, `requirement_ledger.py`, and `harness_improvement.py`.
- Follow their connections to native analytical operators, population/program and financial logic, model policy, cost telemetry, source observations, saved answer contexts, work/follow-up, and on-request deliverables.
- Release assessment: `W/config/agere/release_acceptance.json`, `W/clinical_core/agere/workflow_scorecard.py`, and `W/clinical_core/agere/local_release.py`.

Verify the actual roles of rules/SQL, retrieval and reranking, MiniLM/BGE/UltraBERT or other configured encoders, Qwen/local adapters, Kev/d1 candidates, Jev/TypeSafe, Perplexity, and Claude. Separate available, active, candidate, failed qualification and unneeded-for-this-task. Components should earn their place; every question should not execute every component.

Explain where a question branches, where earlier work survives or is discarded, what triggers escalation, and where review can repeat without producing useful progress. Check whether compiler strictness, context reconstruction, repair loops, model voting or invalidation create disproportionate work.

## 5. Concrete evidence and uncomfortable results to inspect

Treat the following as leads to verify, not conclusions to echo:

- Current received warehouse: **4,115 patients and 2,396,490 records**, synthetic, as of **2030-05-27**. Verify against current configuration and source receipts.
- The November 3 audit records a **57.66-minute partial clinical run with 6,966 provider attempts**. Later repairs improved parts of that path; the current checkpoint still reports an incomplete clinical outcome with **56/60 source packets supported and undecided coverage**. Determine precisely what remains missing, and whether it is missing implementation, unsupported source facts or uncertain verification.
- Inspect `W/artifacts/agere-repaired-ordinary-2026-11-05/ordinary/request.json`, its `run-receipt.json`, the parent `assessment.json`, and `automatic-native47-reuse/assessment.json`. Distinguish a complete ordinary request, resumed work, cached evidence and reused execution.
- The November 5 package assessment has **9/9 scoped engineering predicates covering all ten areas**, but explicitly does **not** establish a finished clinically qualified MVP. Inspect `W/artifacts/agere-whole-stack-release-2026-11-05/acceptance/assessment.json` and its delivery receipt. Challenge whether its predicates measure useful product completion or can pass while important user outcomes remain unfinished.
- A newer ordinary facility/readmission question produced **92 organization groups, 505 discharges and 106 readmissions** matching an independent native baseline, but initially returned partial after **220.56 seconds and 20 provider attempts**, with **$0.33288 known API-equivalent cost**. A focused continuation repaired one field in **12.76 seconds**, with four attempts and **$0.01515 API-equivalent cost**. That continuation is not a fresh-question speed benchmark. Inspect `W/artifacts/agere-hospital-question-2026-11-05/`, especially `request.json`, `baseline.json`, `run-receipt.json`, `workflow-assessment.json` and `delivery-receipt.json`.
- That ordinary question spent about **56 seconds in a typed planning route before falling back**. The latest candidate preserves both rejected proposals and errors for the next component. Offline proof shows transfer rising from zero to two proposals, without accepting either as fact. **No live speed or cost gain has been measured for this candidate.** Inspect `W/artifacts/agere-spec-routing-2026-11-05/diagnosis.json`, `handoff-proof.json`, `test-disposition.json` and `affected-regressions.log`, plus commit `4d1f3a9d`. The 81 affected tests pass; a broader suite has two failures reproduced in the unchanged baseline. Do not report all tests passing.
- Bottling has a useful exact-question financial example, but that does not prove general task transfer, improved clinical knowledge, or recursive model improvement. Local adapter studies report gains, losses, wrong answers and declines; experimental clinical weights are not automatically promoted.
- API-equivalent cost is not a provider invoice or total product cost. Include failed/retried calls and distinguish subscription allocation, local compute, development and human review costs when known. Do not sum concurrent call durations as workflow wall time.

## 6. Questions I need answered

1. **Are we building the right architecture?** What genuinely improves on a competent LLM with warehouse tools? Which safeguards and intermediate representations earn their complexity? What should be simplified or removed from the hot path?
2. **Why is this taking so long?** Audit the build process as well as code. Are we over-decomposing tasks, repairing symptoms, repeatedly checking unchanged workflows, over-investing in receipts, or moving the finish line? Identify the root causes with evidence and the management corrections.
3. **Does the whole stack work together?** Find missing producer/consumer connections, duplicate responsibilities and components that are installed but not useful. Check unfamiliar compositions, not only a curated example or injected plan.
4. **What has the research actually contributed?** Distinguish implemented benefits from attractive analogies, unqualified models and literature summaries. Identify valuable existing work being neglected without turning this review into another research expansion.
5. **What should learning mean here?** Separate answer caching, execution-plan reuse, parameterized artifacts, source/knowledge updates, feedback-driven routing and trained-weight changes. Assess generalization, invalidation, regression testing, rollback and break-even. Would thousands of generated questions improve the MVP now, or just create cost and correlated mistakes?
6. **Is the current next step the right one?** The builder planned one ordinary same-question run to measure the planner-handoff change, then to reassess remaining G3 completion and release compatibility. Decide whether that is worthwhile or whether another existing defect is the actual critical path.
7. **What exactly remains before we can call the synthetic engineering MVP finished?** Preserve all ten acceptance areas. Separate implementation gaps from clinical/model qualification and later customer deployment prerequisites. OWASP/HHS work is currently deferred; do not put it back on this engineering critical path.
8. **How long should completion take?** Give remaining hands-on effort, provider/computation waiting time and outside dependencies by work package. Explain assumptions and confidence. Replace the old November 3 “22–38 hours” estimate with your own evidence-based estimate; do not inherit it or promise overnight completion.

## 7. Requested output and review boundaries

Lead with a plain-language verdict for the founder. Then provide:
- A diagram of the **actual current** question path, including failure, escalation and reuse paths.
- An evidence-backed table of what works, what is partial, what is not connected, what failed qualification, and what is optional. Include the full ten-area scope.
- Ranked findings with code/receipt references, severity, observed defect versus hypothesis, user impact and the smallest justified correction.
- A critical research-to-implementation reconciliation focused on consequential ideas.
- A **finite completion plan**: each work package's specific outcome, dependency, acceptance check, effort range, waiting time and stop condition. Identify the critical path and the first three actions. State what should stop now and what should be deferred.
- A clear distinction between “synthetic engineering MVP finished,” “clinically/model qualified,” and “ready for customer data.” Be candid about what cannot be estimated from coding alone.

Read enough code and retained evidence to support the verdict. Do not merely summarize old audits, count modules/tests, flatter the project, or propose a new architecture because it is interesting. Recommend a substantial change only if you show why a smaller correction is inadequate. Prefer simple competent baselines.

This is a read-only review. Do not edit product/control files, change defaults, start provider/training jobs, stop servers, modify protected data, create additional agents/chats, merge/push, publish or contact anyone. Do not expose credentials. Use existing authorized local evidence and public primary research if needed. State inaccessible evidence precisely rather than claiming to have read it. Return your review in this conversation; the build owner will reconcile findings before implementation.

