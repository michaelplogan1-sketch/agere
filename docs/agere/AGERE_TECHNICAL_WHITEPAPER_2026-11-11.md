# Agere

## Evidence-grounded analytics with selective model execution

**Project Wollstonecraft · Technical whitepaper · 11 November 2026**

**Edition:** local engineering prototype. **Implementation basis:** `e3d5445a22705bf92527d30aa59fadb606acb4d0`, branch `codex/integration-20261026`. All reported healthcare workflow evidence is synthetic. This document is prepared for technical review; it has not been published. Engineering delivery, model/clinical qualification, and customer deployment are separate claims.

## Abstract

Agere is a conversational analytics application designed to connect a user's question to reproducible computation, source-supported interpretation, and inspectable outputs. Its central design is a shared decision context: the original question, entity and population scope, time window, source revisions, analytical operations, unresolved dependencies, costs, and subsequent work remain linked throughout execution.

The application combines database operations and mathematical methods with selective retrieval, bounded specialist judgments, and frontier language models. Native plans and eligible verified reuse are attempted before optional model routing. An unresolved question can enter a single investigation that uses existing analytical tools, retrieves supporting records, asks for clarification, and escalates model capability when necessary. Assertions remain subject to source, entity, date, domain, quantity, and completeness checks. Saved progress permits recovery without reconstructing the entire analysis.

The current implementation demonstrates connected local engineering behavior. One ordinary warehouse-count request completed in 2.887 seconds with no model calls. A separate retained-report optimization reduced observed assembly time from 91.990 to 34.590 seconds while preserving output hashes. These are narrowly scoped observations, not evidence of overall clinical accuracy or lower cost per correct answer. Local student deployment, final review of a retained population analysis, current whole-release applicability, and customer deployment requirements remain unfinished. The contribution proposed here is an evidence-aware execution and learning architecture; its economic and clinical advantages remain hypotheses to establish.

## 1. Product purpose and scope

The intended product answers questions across healthcare data: clinical and medication context, access and care completion, population programs, longitudinal outcomes, provider and facility operations, financial performance, and payer or contract conditions. Population health, value-based care, and outcome-aligned payment remain central uses without defining the limits of the product.

A useful answer must explain its population and denominator, distinguish observed facts from interpretations, show its supporting records, and make missing prerequisites visible. A request for a financial opportunity must preserve the difference between an estimated opportunity and realized savings. A missing claim must not silently become evidence of missing treatment. A report of a past clinician role must not become a claim of current responsibility.

The intended buyers are healthcare organizations whose analysts and operational teams need this traceability across heterogeneous records. That is a product-positioning hypothesis, not evidence of market demand, pricing, procurement readiness, or customer adoption. The present evidence supports a local synthetic-data prototype.

The reusable architecture separates source adapters, semantic contracts, operators, model roles, and output/work interfaces. Another industry would need its own governed mappings, quantities, entities, rules, and qualification evidence. Reusing the architecture does not make healthcare definitions valid in a different industry or establish zero-effort portability.

## 2. How a question moves through the stack

The stack is conditional. A question does not pay for every component, and an available model is not automatically used.

```mermaid
flowchart TD
    Q[Question and conversational clarification] --> C[Shared decision context and scope]
    C --> N[Native plans and eligible verified reuse]
    N -->|supported answer| V[Source, scope, quantity and completeness checks]
    N -->|unresolved work| I[One investigation and selective routing]
    I <--> R[Scoped retrieval and reranking]
    I <--> M[Native SQL and mathematical operators]
    I <--> S[Qualified specialist judgments]
    I <--> F[Sonnet with selective Opus escalation]
    I --> V
    V -->|supported or explicitly unresolved| O[Answer, evidence, requested outputs and follow-up]
    V -->|located defect| I
    O --> L[Measured feedback and candidate reuse or learning]
    L -->|qualified version only| N
```

**Bind the request.** The conversation layer carries the original question, clarifications, entity/population membership and dates into the shared context. Explicit scope must survive planning and execution. When a saved cohort conflicts with an explicit warehouse-wide request, the planner must resolve the conflict rather than silently broaden the cohort.

**Try native execution and eligible reuse.** Counts, filters, joins, aggregations, and compatible saved plans can execute without generative reasoning. The current cascade delays optional question classification until the native/reuse prefix declines. Corrections and explicit continuations retain their priority. A reused program still needs compatible inputs, source versions and policy; a cached answer is not automatically current.

**Investigate what remains.** The shared investigator has access to existing SQL, analytical operators, document search, specialist judgment and clarification. Prior rejected proposals and their reasons travel into that investigation as execution history, not as evidence. Sonnet is the configured entry model for frontier investigation, with selective Opus escalation in the same investigation. Configuration does not prove current provider availability or an optimal routing policy.

**Check and persist.** The output carries native results, citations, qualifications, unresolved premises, cost accounting and a saved context. Localized repair targets a located defect. An interrupted provider call can preserve a new unfinished context and a recovery link while keeping the original snapshot intact. A partial answer is not promoted to a complete answer by saving it.

**Produce requested outputs and follow-through.** Conversation, evidence inspection, requested reports, work ownership and subsequent questions use the same context. Supported internal follow-through is distinct from real-world outreach, payment execution or care delivery.

Current execution connections are implemented in the existing [cascade](../../clinical_core/agere/clinical/proposers.py), [investigator](../../clinical_core/agere/clinical/backstop_agent.py), [analytical tool boundary](../../clinical_core/agere/clinical/analysis_tools.py), [request language](../../clinical_core/agere/clinical/request_language.py), and [service](../../clinical_core/agere/service.py). Their scoped delivery is recorded in [E1](../../artifacts/agere-stack-integration-2026-11-11/delivery-receipt.json).

## 3. Evidence is a runtime contract

A source-supported assertion needs more than a citation label. Its identity includes the source record and revision, entity, exact field or text span, relevant event and availability times, semantic domain, value and unit, and interpretation status. Derived results add their executed operation and dependencies.

Four boundaries are especially important:

1. **Meaning:** a prescribed drug, a reported medication list, a dispensing claim and observed administration are different observations. Payment, adjudication, balance and patient hardship are also different concepts.
2. **Time:** event time describes when something happened; availability time describes when it became knowable. A later correction cannot silently enter an earlier prospective analysis.
3. **Scope and completeness:** a model preview is not the full result relation. An aggregate over clipped input must retain incomplete ancestry. Missing or unread sources must remain visible.
4. **Interpretation:** supported, contradicted, uncertain, unassessed and missing evidence are different states. Model agreement is not independent clinical adjudication.

The investigator's connected search uses the existing hybrid retriever and configured reranker alongside retained lexical matches. Returned spans must match the readable source, correct entity and allowed dates. Retrieval failure is explicit; pagination does not repeatedly rerank the same request. Search ranking helps allocate attention but cannot prove that an unreturned fact is absent. The bulk document reader has separate configuration, so enabling search reranking does not mean that every document-reading operation uses it. [E1](../../artifacts/agere-stack-integration-2026-11-11/delivery-receipt.json)

## 4. Division of work: code, mathematics and models

| Role | Work assigned | Current boundary |
|---|---|---|
| Native database and classical code | Scope, joins, counts, arithmetic, source projection, dependencies and reproducible output construction | Connected to the ordinary question path; semantic premises still require support |
| Analytical operators | Descriptive and longitudinal analysis, financial reconciliation, operational and statistical methods through declared contracts | Existing engines are exposed through the investigator; a registered operator is not proof of validated performance on every input |
| Retrieval and reranking | Find and prioritize relevant source passages while preserving source identity | Hybrid search connected; retrieval quality and completeness require their own evidence |
| System One specialists | Small, typed judgments for appropriate, qualified roles | Interfaces and gates exist; current-contract local student deployment is incomplete |
| Frontier LLMs | Interpret unfamiliar requests, compose tools, resolve difficult reasoning and synthesize supported findings | Sonnet/Opus subscription transport configured; availability depends on the provider |
| Execution reuse and learning | Reuse compatible verified work; capture corrections and compare candidate improvements | Connected with acceptance/invalidation boundaries; trained generative bottles remain off |

The ordinary tool boundary exposes existing operations for trajectories, survival, calibration, demand and capacity, program effects, financial and contract analysis, network questions, and source/knowledge inspection. Whether an operation can answer a particular request depends on its required inputs and assumptions. A causal operator cannot create an identifiable effect from observational records that lack its required design. A prediction path can correctly report that usable targets are unavailable rather than fabricate a risk estimate.

The research also includes implemented mathematical primitives whose usefulness is conditional. For example, [the change-point primitive](../../clinical_core/signal_math.py) uses a Gaussian block fitness when measurement uncertainties are supplied. For a block of observations, with weights `w_i = 1/σ_i²`, it uses `W = Σw_i`, `S = Σw_i x_i`, and fitness `S²/(2W)`, with a segmentation penalty. This follows established Bayesian Blocks work; it does not turn a statistical change into a diagnosis or supply missing assay error estimates. [Scargle et al.](https://arxiv.org/abs/1207.5578)

[The information-value calculator](../../clinical_core/information_value.py) accepts explicit priors, observation likelihoods, utilities and costs. Its decision-theoretic objective is the expected improvement in the best available decision after acquiring additional information. It does not learn reliable clinical priors from sparse records or autonomously acquire care. Proposed joint patient-state/observation models, tensor interactions and automatic algorithm search remain research candidates, not claimed deployed capabilities.

## 5. Routing economics and escalation

The goal is low total cost for an acceptable answer. A cheap call that causes multiple repairs can cost more than a stronger model that completes the same work once. Conversely, asking a frontier model to perform a deterministic database count introduces avoidable generation.

A useful accounting definition is:

`C_answer = Σ C_attempt + C_local_compute + C_review + C_recovery`

The sum includes failed and interrupted attempts. API-equivalent token estimates, subscription allocation, actual invoices and unpriced work are different quantities. An unknown term makes the total unknown; zero new provider calls does not mean zero total computational cost.

The design objective is to choose among eligible routes using expected total cost and answer quality, under the workflow's requirements. The current implementation is a guarded cascade, not a demonstrated global optimizer. Existing scope and acceptance checks determine whether a candidate can finish; optional routing operates after native/reuse opportunities. Frontier escalation should preserve useful work and failure reasons rather than restart analysis blindly. A provider limit is a dependency, not a reason to repeat the request.

Selective model execution has established precedent. FrugalGPT studies prompt adaptation, approximation and cascades; RouteLLM studies learned selection between stronger and weaker models. These motivate Agere's economic design but their benchmark savings do not transfer to this healthcare stack without measurement. [FrugalGPT](https://arxiv.org/abs/2305.05176), [RouteLLM](https://arxiv.org/abs/2406.18665)

## 6. Bottling and measured improvement

Agere distinguishes **execution reuse** from **training a smaller model**. Reusing a parameterized verified computation can avoid repeated planning while still running current data through the relevant guards. Distilling a reusable decision into model weights requires compatible examples, a current input contract, calibration, comparison and an explicit promotion decision. Neither means copying a prior answer into a new population or date range.

The intended improvement cycle is: preserve the attempt and source context; identify a reusable capability or located failure; create a candidate; compare it with the incumbent on appropriate separated evidence; promote only within its demonstrated role; retain a recovery version. Source changes and observed regressions can invalidate reuse. Unverified outputs remain candidates, not training truth merely because a model generated them.

This distinction matters in the current build. The configured student directory contains no deployed weights. Older student manifests do not establish compatibility with the current input contract and were not promoted. The student feature flag therefore must not be described as active qualified local inference. Trained generative bottles are also disabled. Guarded execution reuse and feedback paths are connected, but autonomous recursive clinical improvement has not been demonstrated. [E1](../../artifacts/agere-stack-integration-2026-11-11/delivery-receipt.json)

Information-bottleneck research motivates preserving task-relevant information while reducing representation size; it does not itself validate Agere's passage selection. Conformal methods are relevant to calibrated uncertainty under their assumptions; they are not a blanket guarantee under arbitrary site or time shift. These are research foundations, not borrowed clinical guarantees. [Tishby et al.](https://arxiv.org/abs/physics/0004057), [Angelopoulos and Bates](https://arxiv.org/abs/2107.07511)

## 7. Research contribution and limits of novelty

Retrieval-augmented generation already combines learned language generation with external information. Cascades, model routing, database optimizers, dependency caches and typed verification also predate this project. Agere does not claim to have invented those methods. [Lewis et al.](https://arxiv.org/abs/2005.11401)

The proposed contribution is their coordinated use around one decision context: jointly preserve what the data mean, what is observable, which computations support the answer, where uncertainty remains, what additional evidence could resolve it, and what the entire attempt history costs. Healthcare makes these boundaries consequential, while the interfaces are intended to be reusable beyond it.

The product thesis is falsifiable: compared on equivalent inputs and acceptance criteria, this composition should reduce avoidable model work and repeated analysis while preserving or improving useful answer quality. Current receipts demonstrate some mechanisms; they do not establish overall superiority, patentable novelty, product-market fit, clinical benefit, or an industry-leading analytics stack. Adding every researched model would not establish any of those claims.

The user-supplied DecisionRAG reading remains a retrieval candidate in the research handoff. It is not implemented here, and no cost or accuracy advantage from that paper is attributed to this release. Optional research does not expand the current finish line.

## 8. Measured engineering observations

These results retain their original scope. No new broad acceptance campaign was run to prepare this document.

| Observation | Recorded result | What it establishes and what it does not |
|---|---|---|
| Ordinary question: “How many patients are in the connected warehouse?” | Initially HTTP 400; after scope-binding repair, HTTP 200, 2.887 seconds, zero paid/model calls | Demonstrates one ordinary native question. The returned 3,882 is the disclosed living/non-duplicate default, not the raw 4,115-row roster. One timing observation is not a latency benchmark. [E2](../../artifacts/agere-stack-integration-2026-11-11/ordinary-question-after-receipt.json) |
| Assembly of the same retained 124-row partial report | 91.990 → 34.590 seconds; extra evidence bundle 60,903,777 → 66,116 bytes; same HTML/JSON/CSV hashes and answer fingerprint | Demonstrates less duplicated serialization while keeping sealed investigation references. It does not validate the underlying clinical interpretation or total workflow economics. [E3](../../artifacts/agere-complete-roster-2026-11-10/report-reference-delivery-receipt.json) |
| Interrupted-investigation recovery | New partial context reopens; original unchanged; unknown total preserved; UI recovery link present; no automatic retry | Authored local HTTP/API and shipped JavaScript failure scenario, with one synthetic attempt and zero actual provider calls. It establishes recovery behavior, not live-provider reliability. [E4](../../artifacts/agere-resume-persistence-2026-11-11/workflow-proof.json) |
| Historical aggregate release evidence | Six individual engineering workflows recorded as passed; aggregate release acceptance remains blocked | Evidence is assembled from multiple dates and implementation scopes. It cannot establish all 25 cases or current whole-product acceptance at this whitepaper's commit. [E5](../../artifacts/agere-complete-roster-2026-11-10/current-release-final.json) |

The retained population investigation remains partial/unverified. Its final source and coverage review is unfinished. The last Claude failure records a weekly subscription limit with “resets 5am” in America/Chicago but no confirmed reset date; inspecting its retained raw receipt did not resolve that date. No quota probe was made for this whitepaper. Current transport configuration is not evidence that provider capacity has returned.

No matched cost-per-correct-answer result, independent clinical accuracy estimate or calibrated superiority claim is reported. Historical partial attempts remain part of the record and must be included when the corresponding economic comparison is completed.

## 9. The finite completion boundary

The existing ten-area acceptance scope remains intact. This table states the required outcomes, not a replacement roadmap or a completion score.

| Area | Required completion outcome |
|---|---|
| 1. Native source meaning | Reusable evidence preserves fields, domain, entity, clocks, quantities and uncertainty |
| 2. Evidence dependencies | Missing or changed evidence produces specific review needs and affected-result invalidation |
| 3. Warehouse migration | Current source manifest, original cohort, received-row coverage and rollback remain explicit |
| 4. Context and interpretation | Source-linked local/specialist/frontier routes distinguish retrieval misses, interpretation failures and unassessed content |
| 5. Model qualification | Current-contract training/calibration/evaluation provenance supports a role-specific promotion or documented failure |
| 6. Clinical/program breadth | Versioned reusable criteria, payer prerequisites, preferences and owned internal work remain distinct |
| 7. Longitudinal outcomes | Time-safe targets and honest prediction uncertainty; no forced fit when targets are unusable |
| 8. Discovery and explanation | Applicable statistical methods produce inspectable findings with assumptions and meaningful baselines |
| 9. Financial/provider/contract performance | Actual amounts, lifecycle definitions and denominators reconcile; opportunities remain separate from realized outcomes |
| 10. Integrated product | Current ordinary workflows, all 25 fixed cases, unfamiliar supported composition, follow-up and portable local operation have applicable evidence |

The immediate engineering work is to complete the unresolved ordinary analytical path while preserving existing native results; the retained population investigation should resume only its outstanding review when the provider dependency is resolved. Current role deployment must either obtain compatible qualification evidence or remain explicitly unavailable. Existing compatible receipts should be reused; only affected or missing behaviors require fresh execution. The current release then needs one coherent acceptance decision and a demonstrated setup/restore path at that release identity.

Independent model and clinical qualification requires suitable independent evidence. Customer deployment additionally requires actual customer identity/access boundaries, approved connections and provider arrangements, operational support and an intended-use-specific assessment. These are explicit dependencies, not outcomes supplied by this document. OWASP/HHS work remains deferred under the current scope. Missing external qualification does not prohibit finishing independent engineering, and successful engineering does not establish clinical authority.

## 10. Evidence and reproducibility notes

The authoritative architecture and scope are the [October 11 baseline](../../AGERE_PRINCIPAL_REVIEW_2026-10-11.md), [current principal review](PRECOMMERCIAL_STACK_AUDIT_2026-11-02.md), [product direction](../../AGERE_PRODUCT_DIRECTION.md), [ten-area backlog](../../AUTONOMOUS_STACK_QUEUE.md), and [research handoff](../../AGERE_RESEARCH_HANDOFF.md). Older research plans are historical proposals when they conflict with current implementation receipts.

The application configuration used for the current local runtime is [the active warehouse profile](../../artifacts/agere-full-stack-rerun-2026-10-30/focused/evidence-work-network/active-config.json). The [runtime receipt](../../artifacts/agere-stack-integration-2026-11-11/runtime-receipt.json) records subscription-enabled startup without a new provider question; it does not prove continued process liveness at the time a reader opens this paper. The native count observation was made before that configuration was restored.

The accompanying evidence manifest records file hashes and each observation's scope. Receipts are local development evidence, not externally adjudicated results. Source code, retained attempts and synthetic results remain private. Preparing this whitepaper did not publish them, run a training campaign, access protected answers, use generator truth, or execute real care.
