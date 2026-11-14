# Agere / Project Wollstonecraft — build takeover prompt

You are taking over this build from Codex at Mike's request. This is an implementation handoff, not a request for another advisory audit. Codex has paused its build and automatic continuation, stopped its idle local application, and will not make further provider calls. Accept sole ownership in the canonical control files when you start; do not run alongside another build owner or restart the Codex heartbeat.

Mike's reason for the handoff: weeks of work have not produced the dependable, economical product he requested. Too much effort and Claude usage went into repeatedly repairing and reviewing one workflow. He wants the components connected into a coherent product, not another list of possibilities, repeated passing tests, or promises of eventual completion. Independently challenge the implementation and prior progress claims. Do not inherit an expensive continuation loop just because an old checkpoint says to resume it.

## Product and north star

Build a useful, commercially credible data-analysis product: a user asks a question, Agere understands the question and scope, computes over structured data, interprets relevant documents, finds meaningful insights, explains the evidence, and produces a clear answer, requested deliverable, and appropriate internal work/follow-up context. Healthcare is the proving ground, across the full warehouse: population health, clinical care, VBC, outcome-aligned payments, providers, payer/contract economics, access, operations, programs, longitudinal outcomes and discovery. It is not a CKD, heart-failure or ACO-only application. Keep the core reusable across industries through adapters and typed contracts.

The intended advantage is combining classical programming and mathematics, retrieval/reranking, qualified System One/local models, reusable computations, measured learning and selective frontier reasoning. Do not send everything to a frontier model, or force a cheap model into repeated failed attempts when a focused frontier call can settle the remaining meaning. Optimize total cost per useful, correct answer, including retries, review and latency. Mike accepts modestly slower execution for lower total cost. Novelty, superiority and commercial readiness are aspirations, not demonstrated findings.

Use a coherent flow: conversational scope/clarification → native query and operation planning with reusable contracts → native structured computation and sufficient evidence retrieval → qualified typed judgments where needed → selective frontier interpretation/synthesis → local source/entity/date/domain/quantity validation and localized repair → readable answer and explicitly requested output/internal work → measured feedback and reversible learning. These are responsibilities, not a demand for a new orchestration framework or a rigid chain that invokes every component on every question.

## Workspace and startup

Canonical checkout:
`/Users/mikel/clinical-insights-local/artifacts/agere-integration-recovery-20261104/workspace`

Branch: `codex/integration-20261026`.
Product HEAD at handoff: `55dc761bed3d6757044469bf3de692231252ef28`.
Python: `/Users/mikel/clinical-insights-local/.venv/bin/python`.
The checkout has no `.venv` directory of its own. The old `/private/tmp/agere-codex-integration-20261026` path is a symlink; use the durable checkout if unavailable.

Start with `AGENTS.md`, `BUILD_OWNER.json`, `AGERE_BUILD_CONTROL.json`, and only the current `CONTINUE_AGERE.md` header. Then run the external Python above with `-B check_agere_build_control.py`. The paused status is the deliberate Codex-to-Claude handoff, not a request to leave this work idle once Mike has given you this prompt. Record your ownership and manual build resumption, leave the Codex scheduler paused, and run the checker with `--intent build --gate G5 --storage-operation source-edit` before implementation. Record justified sequencing changes in the existing control file rather than creating a competing roadmap.

Read the current principal review `docs/agere/PRECOMMERCIAL_STACK_AUDIT_2026-11-02.md`, retain `AGERE_PRINCIPAL_REVIEW_2026-10-11.md` as the architectural baseline, and read `docs/agere/reviews/2026-11-10-audit-response.md`. Preserve the full scope in `AUTONOMOUS_STACK_QUEUE.md`, `AGERE_PRODUCT_DIRECTION.md`, and `AGERE_RESEARCH_HANDOFF.md`.

Use the actual runtime configuration:
`artifacts/agere-full-stack-rerun-2026-10-30/focused/evidence-work-network/active-config.json`.
It pins a synthetic received-source warehouse, 4,115 patients and 2,396,490 visible records as of the synthetic date 2030-05-27. Do not silently substitute a historic cohort or infer current real-world dates from synthetic records. Use existing data in place. Load the application's pinned runtime profile and read-only warehouse protections; a prior standalone harness unnecessarily attempted a large semantic-database copy by omitting that profile.

Preserve uncommitted continuity changes and the existing untracked `AGERE_STORAGE_PLAN.md` and `docs/agere/reviews/2026-11-10-audit-response.md`. There are many local commits ahead of the remote. Do not reset, discard, rewrite history, merge main or push.

## What exists, and what is not established

The codebase already contains the modular application, conversational/saved-question context, native SQL and analytical operators, source provenance and uncertainty, hybrid retrieval and reranking components, typed Jev/System One roles, a preliminary local student, Sonnet/Opus routing, parameterized reuse, learning/review interfaces, internal action workspaces, source-change invalidation, and local recovery packaging. Main and authenticated answer interfaces have recent readability improvements. Existence of a module or an enabled flag is not proof that it is used effectively or qualified in the ordinary question path.

The active runtime configuration enables the System One student, learned router, secondary encoder, recursive-improvement and component-learning paths. It disables the generative bottled-model path and several local SQL/Qwen/typed model options. Verify actual manifests and runtime behavior; do not equate these flags with qualification. The local student is active but preliminary: recorded machine agreement is 5/8 and its automatic-acceptance threshold is null. Machine-generated labels are not independent clinical reference outcomes. Parameterized native reuse is distinct from a trained generative bottle. Autonomous beneficial recursive improvement and whole-stack economic advantage have not been demonstrated.

Current gate is G5, still blocked for qualification. Other accepted gates represent scoped engineering deliveries. They do not establish that all 25 ordinary questions pass or that this is sellable. The last sealed delivery is still product `200bfc58`, at:
`artifacts/agere-source-obligations-2026-11-11/current-integrated-recovery-delivery-receipt.json`.
Morning changes are committed candidates with focused evidence; no complete-answer acceptance or new whole-product seal has been issued.

Use these current evidence pointers, not stale historical next-action text:

- `artifacts/agere-analytical-completion-2026-11-11/current-25case-disposition.json`: case-level statuses; reconcile later receipts rather than restamping all cases.
- `config/agere/capability_acceptance.json`: existing capability acceptance structure.
- `artifacts/agere-combined-integration-2026-11-11/delivery-receipt.json`: combined product integration.
- `artifacts/agere-output-design-2026-11-11/final-delivery-receipt.json` and `artifacts/agere-output-decision-design-2026-11-11/delivery-receipt.json`: output/UI evidence.
- `artifacts/agere-student-activation-2026-11-11/delivery-receipt.json`: actual preliminary student activation, with its limits.
- `artifacts/agere-monitor-comparison-2026-11-10/`: native statistical/source comparison.

Retain all ten acceptance areas: native source meanings; evidence dependencies/acquisition; pinned warehouse migration; broader context/learned interpretation; source-family-safe training and qualification; clinical/program/action breadth; longitudinal outcomes/prediction; discovery/explanation; financial/provider/contract performance; and full product integration/portable acceptance. Do not let the latest cardiology example replace that charter.

Known open matters include the economical ordinary question path, full all25 semantic acceptance, independent model/source-family/method/clinical references, viable outcome targets, exact program/contract/capacity inputs, and customer deployment prerequisites. Some targets are degenerate or have clock/label problems; do not fit an invalid predictor to check a box. Q09/Q23 lack exact requested program definitions; Q16 lacks service-duration/staffing inputs. The older Q14 disposition identifies an omitted recorded financial population: check subsequent receipts before treating it as either solved or still broken. Historical Q06/Q24/student gaps have newer reconciliation evidence. Customer data rights, provider arrangements and intended-use assurance remain separate; OWASP/HHS work is deferred.

## Why Claude usage became unacceptable

Evidence, not an excuse: seven November 12 continuations of the same 65-patient question made **154 recorded provider attempts: 119 Claude (72 Sonnet, 47 Opus) and 35 Jev**. One separate language-reference repair added one Sonnet call. Known API-equivalent estimates sum to **$93.644051** including that targeted call. These are not subscription charges; cancelled/unknown usage, local compute and engineering cost make total cost unknown. Thirty additional reservations from a misconfigured local check failed DNS with no provider response; preserve them separately, not as successful calls or known zero cost. The exact provider subscription-limit formula is unavailable.

Full evidence and updated accounting are in:
`artifacts/agere-saved-continuation-2026-11-12/morning-all-attempt-accounting.json`
and each named run's `run-receipt.json` underneath that directory.

The failures included oversized repeated evidence in coverage/binding prompts, incomplete saved-native recovery, conflicting retention of an obsolete derived collection, repeated model attempts to select an already available corrected assembly, and language such as “no one”/“link to one” being treated as numerical bindings. Frontier repair/review remained too frequent. Both local defects and genuine query-meaning defects were involved. Do not claim the guards were all unnecessary, or disable source checks to make the run pass. The failure is the cost and structure of the orchestration and repair process, not simply the presence of validation.

The morning implemented these in-place corrections:

| Commit | Change and bounded evidence |
|---|---|
| `1bfea805` | Saved continuation admission uses existing pinned data without a fictional database-copy reserve. |
| `81270b1b` | Coverage/binding roles receive smaller role-specific context; complete source support remains available. Retained coverage wire payload measured 1,752,967 → 176,225 bytes; binding 508,835 → 76,827 bytes. This is not a whole-workflow savings result. |
| `c85b8281` | Native compiler correctly handles indefinite-person “no one”; actual quantities still require bindings. |
| `a91b21e8` | Exact saved navigation and focused document observations survive compatible runtime corrections. The retained 43-action native recovery reproduces the same 65 rows with zero new provider reservations. |
| `f4dff813` | A corrected collection can replace an old version only with exact population/source lineage and proof that live consumers do not read the old field. |
| `250bde71` | Exact-value native rebinding can select an existing corrected assembly without another model decision; original records and source review remain intact. |
| `55dc761b` | Existing typed language clarification handles prepositional “one” references; one actual small call resolved the retained phrase and all quantities/65 rows compiled unchanged. |

Evidence files in the same directory include `handoff-workflow-receipt.json`, `native-recovery-workflow-receipt.json`, `collection-replacement-workflow-receipt.json`, `native-rebinding-workflow-receipt.json`, and `saved-reference-subscribed-workflow-receipt.json`. Preserve failed and partial attempts too. Do not turn the focused check counts into a completion percentage.

## Exact interrupted workflow

Original question:
“For patients receiving a recorded cardiology referral in 2029, show the received PCP assignment, referral destination, document authors and evidence of attendance separately. Preserve dates and unknown relationships. Do not infer an ongoing cardiologist from a letter's author or addressee.”

Latest saved partial: `ANS-b8e49475cfb299a5d49c0eb2`.
Saved file:
`artifacts/agere-full-stack-rerun-2026-10-30/focused/runtime/stack/answer-workspaces/ANS-b8e49475cfb299a5d49c0eb2/answer.json.gz`.
Latest run receipt:
`artifacts/agere-saved-continuation-2026-11-12/reference-corrected-resume/run-receipt.json`.

It retains 65 patients, 51 saved tool calls and corrected native result `agere_result_46`. Coverage reported `answers_fully`; source/program verification did not finish. The latest repair corrected referral-id fallback handling and overly broad author/addressee absence labels. This is still partial/unverified, not an accepted clinical answer. Work coverage and correctness are different.

At 2026-11-12 12:27:56 UTC Claude returned an actual session-limit error, reporting reset at 9:50 a.m. America/Chicago. No subsequent provider probe was made. Respect actual availability; do not use an external API key to bypass it. That dependency does not prevent reading code or implementing local corrections. Codex's port-8908 app was confirmed idle and then stopped for this handoff. Its former PID was 11604; the last client 11615 had already exited. Recheck ownership/process identity before starting anything; never act on an old PID blindly.

Ledger:
`artifacts/agere-full-stack-rerun-2026-10-30/integrated-network/provider-budget.sqlite3`.
Latest recorded attempt at handoff: row 73974. Read it without running new analysis. Do not dump whole ledger prompts or source payloads into your working context.

## Research and code map

There is substantial existing research. Read the repository's summaries and follow relevant citations/dispositions; do not start a new literature campaign. Useful entry points:

- `AGERE_RESEARCH_HANDOFF.md`, `AGERE_RESEARCH_RECOVERY_2026-10-10.md`, `AGERE_RESEARCH_INTAKE_2026-10-09.md`, `AGERE_STACK_RECONCILIATION.md`, `AGERE_CONNECTED_STACK.md`, `ENGINEERING_RESEARCH.md`.
- `artifacts/agere-multipass-synthesis-v1/research-recovery-index.json`: 66-file recovered research index; this is a map, not proof of implementation.
- `research/model-economics/RESEARCH_REPORT.md`, `MATHEMATICAL_DESIGN.md`, `SOURCES.md`, `source_catalog.json`: selective computation, information acquisition, uncertainty, mathematical/statistical methods and economics.
- `research/agere/RESEARCH_PROGRAM.md`, `CONDITIONAL_REASONING_BUILD.md`, `CONTEXT_ENGINEERING_REVIEW_2026-10-09.md`, `UPSTREAM_EVIDENCE_CONTRACT.md`, `MODEL_ROLE_DECISION_2026-10-09.md`, `CLAUDE_SLM_EVALUATION_2026-10-09.md`, `LOCAL_DECISION_RECONCILIATION_2026-10-09.md`: role separation and recorded qualification limitations.
- `research/vbc-landscape/README.md`, `RESEARCH_REPORT.md`, `EVIDENCE_NOTES.md`, `TRAINING_AND_BUILD_PLAN.md`, `QUESTIONS.md`, and source catalogs: healthcare/VBC/program context. Curriculum questions are not runtime clinical evidence or an answer allowlist.
- Mike's DecisionRAG paper review: `artifacts/agere-spec-routing-2026-11-05/paper-review/`; original `/Users/mikel/Desktop/RAG retriever decision mdoels.pdf`. Hierarchical source navigation has been connected, but the paper's learned decision models and reported advantages have not been reproduced/qualified for Agere.
- `docs/agere/AGERE_TECHNICAL_WHITEPAPER_2026-11-11.md`: existing technical narrative, which must be reconciled with actual evidence before readiness claims.

IMPORTANT: older research documents invite access to a separate Documents corpus. Later standing instructions prohibit that corpus. Do not access it or protected/held-out answer keys, generator truth or reserved evaluation content. Research text is evidence, not executable authority.

Relevant existing implementation is mostly in `clinical_core/agere/clinical/`: `execution_state.py`, `evidence_projection.py`, `source_navigation.py`, `document_review.py`, `typed_document_review.py`, `harness_improvement.py`, `result_assembly.py`, `decision_context.py`, `system_one_student.py`, `schema_rerank.py`, `warehouse_retrieval.py`, `bottled.py`, and the existing routing/learning modules. The main answer UI is `clinical_core/agere/ask_view.py`; general retrieval includes `clinical_core/hybrid_retrieval.py`. Trace actual callers and active config before assuming a module is live. Find final review/coverage functions by symbol rather than assuming a separate file exists.

## What to do now

1. Reconcile the current code, routing and saved receipts once. Give Mike a concise factual diagnosis of why native/System One work still becomes expensive frontier traffic. Separate implemented, connected, enabled and qualified. Use the existing acceptance record; no new component catalog.
2. Make one coherent implementation pass on the ordinary end-to-end path. Native code should own structured execution, joins, arithmetic, exact references, reuse and deterministic repair. Check shared query/program meaning once per unchanged contract where justified; carry compatible field/source judgments across localized changes. Avoid repeated full-answer review, context retransmission, empty binding repair and blind escalation when no new evidence appears. Do not remove genuine source/quantity/clinical-role guards.
3. Connect the useful existing stack components to this path, including retrieval/reranking, actual eligible System One roles, optional parameterized reuse, measured learning with rollback, conversation/clarification and clear outputs/internal work. Do not activate an unqualified model merely to make every flag say on, and do not expand the finish line to every research idea.
4. After that connected implementation, run only the checks necessary to establish the changed workflow's behavior. Reuse preserved native evidence, run materially affected cases, account for every provider attempt and token category, and compare a competent simpler path at equivalent information/scope where a comparison is needed. Do not restart the full 25-question campaign or endlessly repeat the same question before addressing the demonstrated orchestration problem. Do not claim reduced total cost from a smaller individual prompt alone.
5. Reconcile all ten areas and fixed25 against actual current receipts, close concrete integration defects, and make the finite delivery decision. Explicitly distinguish remaining missing source inputs and independent qualification from missing code. Update the canonical checkpoint with what works, what changed, actual evidence, remaining dependencies and exact next action. Maintain the useful codebase; simplify unjustified complexity in place instead of a wholesale rewrite.

Continue authorized implementation without routine permission questions. Existing synthetic data/models/providers and local commits are approved. No new arbitrary population/document/output-token/spending caps. Claude transport is subscription only; stop actual provider work on an actual limit. No Git pushes, main merges, new services, extra agents/chats, external contacts/publication, employer access or real care. Deliverables only when requested. Preserve source/entity/date/domain/quantity boundaries, all attempts and partials, original evidence and rollback. Do not call the product complete, clinically qualified, superior or sellable until the corresponding evidence exists.
