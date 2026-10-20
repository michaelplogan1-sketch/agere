# Agere: build status, routing and market readiness

Assessment date: October 20, 2026. Source reviewed at `d19a81e`; deployed application at `f0eb75b`. This is a user-requested assessment, not a new delivery roadmap or an implementation receipt. `AGERE_BUILD_CONTROL.json` remains the delivery authority: G3 active, G5 blocked, full acceptance false.

**Verdict: a connected synthetic prototype exists; a qualified, independently deployable customer product is not finished.** The data foundation, shared planner, SQL and analytical engines, saved populations, interface and internal synthetic work are real implementation. New-language interpretation, clinical and temporal meaning, independent accuracy/economics qualification, and customer deployment remain incomplete. Earlier component acceptance does not establish these broader claims.

## What actually runs

The active path is service → conversation context → `Stack.compose` → shared planner → typed request compiler → governed SQL, plus registered analytical engines when requested.

- A fully consumed grammar or exact-source plan cache can avoid new hosted calls.
- An unfamiliar question uses the configured Claude interpreter when hosted execution is enabled. It proposes a typed request; code compiles that request into SQL. A separate prompt to the same model reviews its interpretation. That is an additional check, not independent clinical validation.
- SQL executes the relational calculation or selects the population consumed by a statistical engine. SQL and LLM participation are therefore overlapping categories.
- Qwen is an unqualified fallback when hosted execution is disabled. Registered readers, classifiers, rerankers and bottling machinery should not be represented as active or qualified on every request.
- Frontier deliverables and action agents remain synthetic demonstrations, as requested. Saved work does not establish clinical eligibility or perform real enrollment.

Evidence: `clinical_core/agere/clinical/stack_runtime.py:106`, `shared_planner.py:186`, `shared_planner.py:263`, `shared_planner.py:322`, `deliverables/answer_context.py:219`.

## Measured routing, with its limits

Read-only audit: `artifacts/agere-cto-market-review-v1/routing-audit.json`; reproducible script alongside it. Captured at 12:17:29 UTC on October 20. No product questions or benchmarks were executed for this audit.

| Observation | Count / estimate |
|---|---:|
| Historical attempts across old and new versions | 1,444 |
| Historical distinct question hashes | 710 |
| Attempts since first shared-planner log entry | 27 |
| Shared-planner requests marked answered | 24, covering 12 distinct questions |
| Other recent attempts | 1 saved-cohort operation; 2 errors |
| Shared-planner answers with zero new paid API calls | 22 |
| Shared-planner answers with paid interpretation | 2 |
| API estimates for those two paid requests | $0.306712 and $0.221843 |
| Recorded elapsed times for those two requests | 13.748 s and 13.558 s |

The shared execution code requires SQL for every successful shared-planner answer, so these 24 successful requests traversed a SQL execution path; some also used analytical engines. This conclusion combines route logs with inspected code, not a dedicated SQL-execution field in telemetry.

The 22 zero-new-call requests include repeated questions and previously paid planning. They do not establish a 91.7% savings rate. “Answered” is a runtime status, not externally verified correctness. The older 347 `hosted_verified_sql` records are not the only historical SQL users; route-name division would give a misleading SQL percentage.

The answer log currently lacks per-request build version, cache-hit classification and linked execution-stage receipts. Instrument those before reporting a representative mix. Measure cost per independently correct, useful answer, with failed attempts and cold versus repeat requests separated. Include model, warehouse and local compute cost; API spend alone is incomplete.

## Concrete current failure

The latest logged standalone request asked to find patients qualifying for an MSK program, help **them** enroll, and monitor outcomes. It failed in 0.001 seconds with “No saved patient cohort is available for this follow-up.”

`conversation.py:577` treats any occurrence of `them`, `those`, `these` or `they` as a reference to earlier conversation. `conversation.py:611` then rejects the new request. A pronoun referring to patients introduced in the same sentence is not necessarily a follow-up. With a prior context, the same heuristic can incorrectly restrict a new request to that old population. The next correction must address context resolution generally, including explicit warehouse scope and saved-context edits, not add an MSK-specific handler.

Further, repairing this entry-point defect alone does not complete the requested enrollment and monitoring workflow. Those intents need a shared representation and explicit synthetic execution state.

## Competitive implication

These are current first-party capability descriptions, not independent vendor performance tests.

| Product | Verified offering | Recommendation for Agere |
|---|---|---|
| [System](https://www.system.com/) | A quantitative graph of scientific findings and relationships, with healthcare and research applications. | Focus on joining an organization's observations to decisions and accountable work; external evidence graphs may also be complementary. |
| [Databricks Genie](https://docs.databricks.com/aws/en/genie/) | Natural-language questions, dashboards, applications and domain agents grounded in governed organizational data. | Build a healthcare application that can work with a customer's existing warehouse; avoid making replacement of its storage platform a prerequisite. |
| [Innovaccer Gravity](https://innovaccer.com/gravity) | Healthcare data integration, analytics, agents and workflows; model choice can vary by use case, cost and performance. | Prove faster onboarding, usable answers and workflow value for an identified buyer. Do not assume competitors call an expensive LLM for every operation. |
| [Arcadia Ascent](https://arcadia.io/ascent/platform/) | Integrated clinical and claims data, analytics, predictive models, registries and care/financial workflows. | Demonstrate a better question-to-work experience and measured economics rather than claiming data-to-action is unique. |
| [OpenAI's Epic plugin](https://help.openai.com/en/articles/20001490-using-the-epic-plugin-with-chatgpt-and-codex) | Authorized chart access in approved organizational workspaces; currently read-only, without chart updates, orders or patient messaging. | Mike's premise is correct. Test Agere's cross-source population calculations, durable work and monitoring against an appropriately equipped alternative with equivalent evidence access. |

The proposed market promise is: **turn fragmented healthcare data into a verifiable answer, an interactive workspace, and accountable next steps—with measurable cost per useful answer.** This remains a product hypothesis to validate.

## Next release gates within the existing scope

1. **Reliable shared interpretation.** Handle new combinations across claims, utilization, access, providers, contracts and clinical observations. Preserve dates, negation, denominators, requested outputs and context. Distinguish unavailable evidence from interpretation failure. Accept no silent constraint loss in the release cases.
2. **Complete the synthetic question-to-work loop.** The original request should lead to inspectable calculations, appropriate interactive outputs, simulated work with owners/status, and a monitoring definition. Preserve population and evidence versions throughout. Keep simulated action visibly labeled. Analytic-only questions should not force care actions.
3. **Independent qualification.** Validate references before running or rescoring protected benchmarks. Use unseen questions and systematic changes in dates, thresholds, negation and scope. Compare against capable LLM-plus-SQL/tool baselines, including lower-cost models, with the same data and semantics. Report correctness, abstention, latency and total cost separately for first and repeated requests.
4. **Customer installation.** Demonstrate onboarding a second independently prepared schema without new question-specific code. Establish application identity, role permissions and tenant isolation, managed secrets, durable jobs, timeouts/cancellation, auditability, recovery and monitoring. Existing connector classes and local recovery tests are useful beginnings, not proof of a customer deployment.
5. **One design-partner pilot.** A proposed first buyer is an ACO or multispecialty group's population-health/operations team with an existing warehouse. Exercise several domains, rather than one disease. Agree on useful-answer time, staff effort and completed workflow measures before the pilot. A narrower first buyer does not narrow the whole-warehouse technical requirement.

The immediate engineering priority is general context and compound-intent handling, followed by independent acceptance of the connected workflow. Additional models should enter the default path only when evidence shows they improve a named failure or its cost.

## Codex and Claude review process

Keep one implementation owner. Give the reviewer a fixed commit, requirements, exact changed code and reproducible evidence. The reviewer should propose independent counterexamples and report defects without simultaneously editing the implementation checkout. Codex fixes confirmed defects; Claude checks the resulting commit; executable acceptance and domain-reviewed reference answers decide release readiness. Model agreement alone does not establish correctness.

Artifacts in `artifacts/agere-cto-market-review-v1/` retain the review prompt, read-only audit and model-review evidence. The Claude Code attempt encountered expired OAuth. A bounded source-packet review then completed through the application's existing metered Claude connection, using `claude-opus-5-5`. The model saw selected source excerpts, not the whole repository, and ran no tests.

The first API attempt exhausted its response limit during reasoning without returning visible findings. One explicit retry returned the complete review. Receipts retain both attempts; combined recorded API estimate is **$0.692324**, not an invoice. This review expense is separate from the question-routing sample.

### Findings reconciled by Codex

1. **Confirmed: new requests can be mistaken for saved-cohort edits.** Both source inspection and the existing live failure support the generic pronoun/context problem.
2. **Confirmed: compound intent is not represented in the shared request.** Its contract covers relational queries and analytical operations, not separate action and monitoring clauses. Existing synthetic agent simulation does exist in `deliverables/synthetic_actions.py`; the problem is connecting the original compound request to it. Do not interpret the review as saying no synthetic agents exist.
3. **Reproduced: literal coverage can pass despite a dropped condition.** An isolated, deliberately incomplete plan for “Count patients with ckd” compiled to an all-patient count when its source quote contained the whole question. This demonstrates a weakness in the structural checker; it does not show that the hosted semantic reviewer also accepted this plan.
4. **Reproduced: mixed AND/OR wording is accepted with an ambiguous scope.** In a two-patient in-memory fixture, “Find patients over 65 with diabetes or ckd” includes a 30-year-old with CKD. An explicitly grouped version does not. The release behavior should resolve ambiguity rather than silently assume user intent.
5. **Source-confirmed maintenance gaps:** an accepted request-policy value is rejected by the active stack, and the plan-cache fingerprint list omits vocabulary modules used by the grammar. These warrant focused regressions, not a claim that a stale production answer has already been observed.

Probe script and exact results: `artifacts/agere-cto-market-review-v1/reproduce_review_findings.py` and `confirmed-review-probes.json`. These use a tiny isolated fixture; they are not protected benchmark questions, population accuracy estimates or clinical validation.

Corrections to the review's limited view: `Stack.shared_request_planning = True` is defined at `stack_runtime.py:15`. Snowflake, BigQuery and Databricks client factories and scoped credential plumbing exist later in `onboarding/connectors.py`, outside the supplied excerpt. Customer deployment is still unqualified, but “no connectors” would be inaccurate. The service also has scoped API authorization in other paths; localhost guards alone do not establish complete application identity or isolation.

No product code changed during this assessment. No real care was executed, no protected benchmark was run or rescored, and no current release gate was marked complete.
