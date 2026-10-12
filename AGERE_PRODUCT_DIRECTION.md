# Current scope clarification — October 12, 2026

Agere is a general healthcare and healthcare-adjacent intelligence product. Its
scope spans patients, episodes, populations, clinicians, hospitals, health systems,
ACOs, payers, services and contracts. The user's latest clarification supersedes
medication-first or single-disease flagship sequencing in the historical notes
below. Medication is one evidence domain and regression case, not the organizing
purpose of the stack or a prerequisite for every other capability.

Required question breadth includes:

- **Admissions, readmissions and deterioration:** reconstruct what happened and
  when, assess documented reasons and possible contributing factors, identify
  missing evidence and distinguish temporal association from an established cause.
- **Care gaps and intervention:** identify missing or incomplete care, reconcile
  conflicting evidence, and support suitable next actions with patient goals,
  clinical knowledge, buyer capacity and applicable payer requirements.
- **Healthcare-adjacent context:** investigate documented housing, food, transport,
  caregiver, behavioral, employment, financial and environmental circumstances
  that may affect health or access. Missing records do not establish absence or
  causation; social circumstances cannot be inferred from billing balances alone.
- **ACO and population priorities:** identify whom to focus on and why, connect
  quality, utilization, patient outcomes and cost to attribution and actual
  upside/downside-risk and shared-savings contract definitions. Distinguish risk,
  modifiable needs, feasible interventions and demonstrated benefit.
- **Physician and hospital performance:** compare quality, outcomes, access,
  utilization, care transitions and cost with explicit attribution, denominators,
  case mix, observation windows, sample size and uncertainty.
- **Unfamiliar combinations and discovery:** answer new questions joining these
  domains, examine patterns, and support explanations, predictions and actions
  when the available evidence and qualified analytical methods permit them.

These are representative question families, not a finite allowlist. Build reusable
source interpretation, longitudinal state, cohort/relationship composition,
analytical operators and work/follow-up contracts. A new question must not require
its own bespoke handler. Clinical, measure and contract definitions still need
versioned domain knowledge; a generic language interface alone is insufficient.
Each answer must retain the original question, population/entity and time scope,
source provenance, relevant uncertainty, missing inputs and supported next steps.
Analytical questions need not force a patient intervention or payer workflow.

“Any healthcare query” is the intended breadth of questions the system can engage
with, including a precise explanation of what it cannot answer and what evidence
or capability is missing. It does not imply every answer, causal inference or
prediction is already available or qualified. Finite local engineering receipts
remain evidence only for their tested behavior, not full-product acceptance.

**Sequencing correction:** the next human-directed G3 increment must trace
representative admission/readmission, care-gap/social-context, ACO-prioritization
and clinician/hospital-performance queries through the actual shared path. Use
that trace to identify and implement a concrete missing shared capability in the
same bounded increment, then verify its effect on the affected queries and an
unfamiliar composition. Do not stop at another catalog or planning report. Retain
medication and payer defects as regressions within the full ten-area backlog,
without automatically placing another medication-only increment first. Exact
sequence and next action remain owned by `AGERE_BUILD_CONTROL.json`.

This is a requirements and priority correction, not a delivered runtime capability
or model/clinical qualification. Automatic continuation remains paused; preserve
the user's human turn boundaries.

---

> October11 verified addition: [appointment follow-through, protected development comparison and uncertainty](APPOINTMENT_FOLLOWTHROUGH_BUILD.md). Actual source-state/work and model comparison are executable; both difference intervals cross zero, no predictor is promoted, and full clinical/product acceptance remains incomplete. Active v1.23 and prior experiments remain protected.

> October 10: registered respiratory/behavioral population work and exact local source-review questions are delivered in `DOMAIN_PROGRAMS_BUILD.md`. All 124 new items remain review-needed; clinical/benefit quality and full product acceptance remain incomplete.

> Saved machine answers now survive bulk work refresh, with transactional protection against stale saves: `WORK_CONTEXT_PRESERVATION_BUILD.md`. Selected-adapter registration, broader domain program composition, audited stale-answer retirement and full acceptance remain open.

> Respiratory and depression/anxiety source review now reaches bounded answers and buyer evidence handoffs: `DOMAIN_SOURCE_REVIEW_BUILD.md`. Label-only coverage, semantic quality, dedicated domain programs and full acceptance remain incomplete.

> Functional/support source review now composes into persistent program work and a saved-answer buyer handoff: `FUNCTIONAL_PROGRAM_WORK_BUILD.md`. Clinical validation, broader domain recommendations and full product acceptance remain incomplete.

> Open-vocabulary question-linked machine phrase acquisition is connected: `SOURCE_SPAN_PROPOSALS_BUILD.md`. The daily/whole-stack update is in `AGERE_DAILY_STACK_UPDATE_2026-10-10.md`. Broad unmarked-field acquisition, semantic quality and full product acceptance remain incomplete.

> Complete-window reading and honest acquisition-versus-fitted coverage are delivered: `WINDOWED_SOURCE_READER_BUILD.md`. Source coverage succeeds; calibration remains insufficient. The fitted set covers 13 categories / 11 concepts, not the full 76-category acquisition. Next broaden exact question-linked source acquisition and continue the full product direction.

> Additional source-learning cohorts, preserved source-family partitions, native supervision acquisition and visible shadow-model disagreements are delivered: `LEARNING_COHORTS_BUILD.md`. Lexical calibration fails; fixed-length MiniLM rejects long input. Clinical qualification and automatic acceptance remain unavailable. Continue complete-window representation and the full product direction below.

> Current-source protected acquisition, question-linked exact source tasks and pre-inference design qualification are delivered: `CURRENT_SOURCE_LEARNING_BUILD.md`. The current fixed sample fails assessment minimums; no model promotion, clinical validation or human review is claimed. Continue broader source learning and the full warehouse product direction.

> Source timing, explicit dated evidence selection and immutable follow-up answers attached to original questions are connected: `EVIDENCE_TIMING_BUILD.md` and `EVIDENCE_ANSWERS_BUILD.md`. Engineering checks pass; temporal sufficiency, semantic/clinical quality, model qualification and full product acceptance remain incomplete. Resume broad source-family-safe acquisition/learning and the full population/VBC/prediction direction below.

> Bounded unresolved-question candidate acquisition and exact received-account follow-through are now connected: `NEED_CANDIDATES_BUILD.md` and `NEED_ACCOUNTS_BUILD.md`. Local engineering verification passes; retrieval quality and clinical meaning remain unvalidated. The full 17-record account interpretation awaits payload-specific approval after automatic review rejection; independent development continues.

> Stable cross-pass references, direct default synthesis and return of interpreted follow-up evidence to exact internal requests are delivered: `STABLE_REFERENCES_BUILD.md`, `DEFAULT_STABLE_SYNTHESIS_BUILD.md`, `FOLLOWUP_ANSWERS_BUILD.md`. Current machine attachments preserve source/review dependencies and leave clinical requirements unresolved. Broad interpretation, learning, VBC/financial reasoning, prediction/discovery and portable acceptance remain incomplete.

> Scoped local packet execution and source-bound follow-through are delivered: `INTERNAL_EXECUTION_BUILD.md` and `PACKET_FOLLOWUP_BUILD.md`. Local credential possession is authenticated; clinician identity, clinical authority, care delivery and outcome benefit remain unverified. Unresolved requirements can now lead to exact patient evidence and bounded interpretation. Continue the full warehouse product direction below.

> Combined internal work status and exact local refresh are delivered: `ACTION_WORK_STATE_BUILD.md`. A current evidence packet remains distinct from clinical or benefit approval. Continue authenticated review/execution contracts and the full interpretation/prediction/economical-routing backlog.

> Exact buyer/provider participation declarations now travel through service matching and internal handoffs: `PARTICIPATION_EVIDENCE_BUILD.md`. Declared scope and imported clause mapping do not establish actual participation, benefits or clinical execution.

> Received patient coverage now feeds exact internal handoffs: `COVERAGE_CONTEXT_BUILD.md`. Current membership, benefits and provider participation remain unverified; the next implementation is dated participation/contract scope.

> Source-bound readiness is now connected: see `ACTION_READINESS_BUILD.md`. Documentary decisions and imported clause dependencies do not establish clinical eligibility, exact plan applicability or external execution authority.

## Buyer-aware action requirement — user clarification October 10, 2026

Agere must accept each buyer's actual service capabilities and understand patient-specific CMS, Medicare Advantage, commercial, Medicaid and other arrangements. Match supported needs and patient goals to available services, accountable clinicians/agents/teams, geographic and modality constraints, effective benefits, participation and capacity. Examples are not an allowlist. Keep clinical benefit, financial effects, benefit eligibility and execution authority distinct. The first connected implementation is `BUYER_CAPABILITIES_BUILD.md`; its research and remaining gaps govern the continuation queue. Internal evidence handoffs are executable; live clinical or external execution is not yet enabled or authorized by this requirements clarification.

---

## Current spending policy — uncapped, explicitly authorized October 10, 2026

The user stated that there should be **no caps on spend at this time**. The assistant-selected $50 ceiling is removed from the shared Agere ledger; no replacement ceiling is permitted without a new user instruction. Usage, reservations, costs, caching and duplicate/failed-request protections remain tracked. Existing authorized integrations and relevant bounded requests remain the scope. Do not stop useful authorized API work for reservation headroom or require settlement before continuing. Historical $0.33/$50 budget notes below are superseded. See `UNCAPPED_SPENDING_BUILD.md` and `artifacts/agere-uncapped-spending-v1/implementation-receipt.json`.

126 focused checks and actual API/browser checks pass. Three paid saved answers retain exact identities and replay with no new calls; an uncached scope is dispatchable. New paid calls for the policy change: zero. Current owned server PID **77662**, tool session **44988**, port 8885. Next product implementation remains typed evidence-need dispositions and reviewed source dependencies. The requested whole-stack report has already been delivered; do not repeat it as a substitute for implementation.

---

# Agere — product direction and implementation priorities

## Latest scope and research continuity — October 10, 2026

The user reaffirmed that Agere must handle all healthcare data in the connected warehouse, with structured and unstructured interpretation, missingness, unfamiliar questions, patterns, explanations, supported predictions and actionable recommendations. Earlier disease examples are regression cases, not architectural limits or exclusive priorities. This whole-warehouse scope supersedes narrower flagship framing below while retaining population health, VBC, commercial measures and contract requirements.

The competitive objective is to outperform Arcadia, Innovaccer, TextQL, Databricks-based healthcare solutions and capable frontier-model systems connected to clinical data on useful correctness, coverage, actionability and total economics. This is an engineering objective, not an established comparative result. Read [the research handoff](AGERE_RESEARCH_HANDOFF.md), including the separate Documents research corpus, and reconcile material research suggestions with actual implementation and remaining gaps. Preserve unexplored ideas explicitly; do not silently drop them during handoff. The handoff documents research continuity, not new runtime capabilities.

## Standing routing requirement — October 10, 2026

The user explicitly clarified: “if an API call is the best option it needs to be done.” Optimize for useful, evidence-supported answers and appropriate task quality, then minimize the total cost of achieving that quality. Use an existing authorized hosted specialist or frontier API when it is the best supported option; do not withhold it merely to report zero paid calls or favor an inadequate local model. Routine calls within existing integrations and authorized ledger limits do not need another per-call permission request. Retain bounded relevant inputs, dependency-aware caching, spending receipts and existing budget safeguards. Do not make gratuitous calls or invent missing source evidence. Higher price or frontier status alone does not establish higher quality.

This is a standing build and routing requirement, not a claim that calibrated quality-based routing or end-to-end clinical qualification is already delivered. Previous zero-call receipts remain historical execution facts, not the product's success criterion.

Updated October 9, 2026 following the user's scope clarification. This is the
current product direction. It supersedes narrower milestone ordering in earlier
plans; it does not supersede implementation receipts or claim new runtime features.

## Objective

### Product destination — portable intelligence across customer data environments

Agere is intended to become a commercial product that connects to customer
ontologies and healthcare warehouses. The synthetic ecosystem being developed in
the other chat is its first development and testing environment. Build and test
the complete stack there first, then support customer onboarding and deployment.
Access to the user's employer environment is not a current dependency; it is not
necessary to build the portable product.

Portability is an architecture requirement now, even though customer connectors
and production deployment come later:

- Keep ecosystem-specific schemas, table names, codes, identifiers and paths in
  versioned adapters and mapping configuration, separate from clinical reasoning.
- Map customer ontology concepts and relationships into shared evidence, event,
  state, outcome and program contracts while retaining original meaning and source
  provenance. Customers should be able to reuse their existing ontology work.
- Describe each source's available fields, units, clocks, identity links and
  coverage explicitly. Surface missing mappings or inputs rather than assuming
  that every warehouse has the synthetic ecosystem's sources.
- Make models, analytical operators, clinical knowledge, routing and caching
  reusable across adapters. Dataset-specific mappings must not require a new
  reasoning engine for each customer.
- Exercise portability with independently mapped synthetic schema/source variants
  before customer deployment. Keep tenant separation, access control, governed
  connectors and operational packaging in the later commercialization backlog.

Build the first adapter against versioned synthetic ecosystem releases and extend
it as those releases evolve. Preserve the other chat's generator and artifacts.
Use received warehouse records for interpretation; hidden synthetic truth belongs
in separate evaluation, not runtime evidence. These are design and delivery
requirements, not a claim that portable connectors are already implemented.

### Latest user priority — population health, VBC, commercial metrics and OAP first

The user clarified on October 9 that Agere's core must first compete with
Innovaccer and Arcadia as a population-health and value-based-care operating and
analytics product, including commercial metrics and **outcome-aligned payments
(OAP)**. Advanced research capabilities follow that core or improve a specific
core workflow without delaying it. This supersedes research-first sequencing.

The flagship acceptance request is to find the complete in-scope heart-failure
population with reduced EF, reconcile medications and doses, identify supported
GDMT initiation/titration opportunities, distinguish documented maximum tolerated
dose and other exceptions from unknowns, and operate outreach/enrollment into a
PharmD program through monitoring, titration follow-up and reassessment. Connect
clinical outcomes, program performance, commercial metrics and actual contract
payment definitions. Below guideline target dose does not establish that the
current dose is below the patient's maximum tolerated dose.

Build shared cohort/evidence/protocol/workflow/measurement components so this
generalizes to other population programs. HFrEF is the flagship acceptance case,
not the product's sole disease or a reason to hard-code question strings.
Outreach and program execution are required product capabilities; this scope
clarification does not authorize contacting actual patients from a research chat.

Current core requirements and revised milestone order:
[core product charter](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/CORE-PRODUCT.md).
This is a requirements update, not a runtime release. The capability sections
below remain architectural context; their order does not override this priority.

Build a healthcare data intelligence system that interprets fragmented evidence,
answers unfamiliar questions, discovers consequential patterns, investigates why
events happen, forecasts what may happen, identifies what is missing, and proposes
supported actions. The scope includes clinical care, population health, value-based
care, patient goals, access, operations, providers, payer programs and contracts.

HF and CKD are examples, not the product boundary or the exclusive next milestone.
The existing question catalog is a curriculum and a record of user needs, not an
allowlist of questions the finished product may understand. New questions should
compose shared capabilities and domain knowledge rather than require a dedicated
hard-coded handler for every wording. Explicit clinical and payer definitions are
still necessary; a general planner cannot invent them.

The ambition is leadership in useful, correct healthcare interpretation and action
recommendations at low total cost. No claim that named vendors or frontier models
cannot match the system is established. Model count, unfamiliar equations and
architecture diagrams are not evidence of superiority.

## Shared intelligence capabilities

| Capability | Product behavior | Reusable implementation |
|---|---|---|
| Interpret | Understand what the warehouse and documents actually contain, which concepts mean the same thing, and which relationships are uncertain | Source/schema catalog, terminology and unit normalization, identity and relationship contracts, exact narrative spans and provenance |
| Reconstruct | Represent evolving patient, episode, provider, access and source states | Typed events, event/record/arrival time, correction and copy lineage, conflicting assertions, source coverage and unresolved states |
| Answer | Execute unfamiliar combinations of cohorts, time windows, outcomes and comparisons | Typed analytical plans, a composable operator graph, verified input bindings, exact denominators and explicit unmet requirements |
| Discover | Find meaningful patterns without requiring the user to name the exact pattern | Cohort and sequence discovery, change detection, duplicate/source-artifact controls, multiple-testing controls and replication |
| Explain | Reconstruct contributing sequences and compare plausible explanations | Process and temporal graphs, alternative hypotheses, source-state checks, and causal designs where identification assumptions can be supported |
| Predict | Estimate future outcomes and utilization with uncertainty | Time-safe feature/label construction, censoring and competing-event handling, model fitting, calibration, drift detection and versioned forecasts |
| Recommend | Match supported options to patients and populations | Intervention evidence, clinical/program rules, patient goals, benefit and harm assumptions, capacity constraints, feasibility and separate financial effects |
| Learn economically | Improve reusable skills and spend computation where it helps | Reviewed corrections, versioned task training, task-specific routing, dependency-aware invalidation and measured total costs |

Missingness is part of every capability. Unrecorded care, delayed data, unavailable
sources, unrecognized concepts and genuinely absent events are different possible
explanations. Some cannot be distinguished from the observed data. The output must
identify the unresolved alternatives and what evidence would discriminate them.

## Central research hypothesis

The strongest proposed integration couples patient/care state with the process
that generates observations, then connects that uncertainty to prediction and
action selection. A feed interruption and a change in care can create similar
records. Population-wide source patterns can inform the distinction without
proving an individual patient's unobserved treatment state.

Build a common interface for competing state explanations, their source evidence,
identifiability limits and the consequences of obtaining additional information.
Connect the existing joint-observation and information-value prototypes through
this interface only with their actual assumptions and evidentiary status exposed.
Learned probabilities require appropriate training and calibration; unresolved
alternatives must not receive invented confidence numbers.

Relevant mathematics already researched has specific jobs: state-space and
missingness models for observation/state separation; change points for irregular
trajectories; shrinkage for sparse provider comparisons; false-discovery controls
for large searches; causal estimators for supported intervention questions; value
of information for acquisition; constrained optimization for feasible responses;
and low-rank models as economical interaction-model candidates. These are
established methods. An original combination or improvement remains a research
hypothesis until comparative work demonstrates it.

## Engineering sequence

### A. Shared data understanding and composable execution

Extend the existing observed-source index and operator registry with machine-readable
concept, relationship, event, outcome, attribution and required-input contracts.
Separate schema/record availability from adequate clinical observation. Extend
plans from a list of independent operations to explicit dependencies, with bounded
execution and verified joins, temporal cutoffs and denominator scope.

Deliverable: the workbench shows a question's interpretation, required data,
executable dependency graph and remaining unknowns across the existing clinical,
utilization, provider, access, policy and financial domains. Previously unseen
combinations of supported primitives can execute without adding question strings.
Do not equate a registry entry with a working warehouse adapter.

### B. General longitudinal state and observation reconciliation

Add typed multi-event records, source-bound free-text dates, episode links,
corrections, reported actions, barriers and source-quality hypotheses. Admission
and discharge are distinct events, not conflicting values for one generic date.
Retain narrative evidence and uncertainty when a date or clinical state cannot be
resolved. Extend condition, medication and access states through reusable contracts.

Deliverable: questions can traverse events and source changes across admissions,
medications, referrals, access barriers and patient goals, with inclusion/exclusion
reasons. Disease-specific definitions attach here as versioned knowledge.

### C. Population discovery, investigation and forecasting

Connect existing mathematical components to documented warehouse inputs. Add
general outcome/cohort comparison and pathway analysis before treating detected
patterns as causes. Build prospective features and outcome labels, fit suitable
predictors, and keep forecasts distinct from descriptive extrapolation.

Deliverable: the same interface can discover a supported population pattern,
investigate competing explanations, and produce a forecast when adequate historical
outcomes exist. An unavailable outcome or attribution definition produces a concrete
data/implementation requirement, not a fabricated estimate. Predictions and causal
estimates must carry their population, horizon, assumptions and uncertainty.

### D. Evidence-based options, programs and operational decisions

Connect intervention evidence, patient preferences, versioned CMS/commercial/Medicaid
rules, measures, contract arithmetic and capacity inputs to findings. Implement
update intake, clause-to-rule proposals, review records, impact analysis and version
activation. Changing policy content belongs in versioned knowledge; it does not
require retraining every model.

Deliverable: each recommendation explains who it applies to, why, alternatives,
missing prerequisites, evidence strength, anticipated benefit/harms and capacity.
Financial projections remain separate from clinical benefit. The latest core
scope includes outreach and program-execution capabilities. Their implementation
must track authorization, ownership and completed actions; this requirements
update neither enables live execution agents nor authorizes actual patient contact.

### E. Continuous learning and total-cost optimization

Latest user clarification: compact/local models and code should handle each task
they can perform adequately, with Claude or another frontier provider reserved
for questions or deliverables needing frontier capability. Model-family loyalty
and sending every task to a frontier model both conflict with that objective.
October 9 implementation: default economical planning now exists, with exact local
plans/cache, MiniLM candidates, Perplexity typed routing and Claude escalation.
Its narrow current coverage is documented in `ECONOMICAL_ROUTING_BUILD.md`;
expanding reliable cheap coverage remains active implementation work.

Connect corrections to task-specific training and release. Train reusable skills
such as relation/time extraction, question planning, evidence selection and routing.
Do not train changing payer policy into a supposedly permanent answer. Preserve
unreviewed teacher labels as machine supervision, not clinician approval.

Deliverable: versioned model releases improve identified work, affected results
recompute after data/rule/model changes, and the workbench reports provider usage,
local computation, cache behavior and review burden. Economics is part of every
milestone, not a final optimization exercise. Avoid an all-models-on-every-chart
ensemble; route work according to demonstrated task needs.

## Build and evidence discipline

Each milestone must add an executable capability in the current warehouse/question
path. Verification accompanies implementation; repeated unchanged comparisons,
more question text, research catalogs and diagrams do not count as feature delivery.
The existing synthetic warehouse and another chat's generator remain separate.
Missing fields and missing engineering are reported separately.

For eventual superiority claims, compare against a competent frontier model with
appropriate tools and retrieval, strong structured/semantic approaches, and simpler
specialized models using equivalent information and cutoffs. Compare useful-answer
coverage, consequential errors, discovery validity, forecast calibration, action
usefulness, latency and total cost. Do not obtain cheap answers by quietly excluding
hard questions, omitting failed requests, or treating correct uncertainty as failure.

Class reimplementations must be called an Arcadia-class measure platform or
TextQL-class semantic layer, not measurements of the vendors' actual products.
No measured claim against Innovaccer, OpenAI connected to Epic or other commercial
systems is available here. All current patient data is synthetic; real clinical
outcome superiority requires later appropriately governed outcome evidence.

## Current implementation boundary

Financial reconciliation and program accountability are now connected (`FINANCIAL_ACCOUNTS_BUILD.md`, `PROGRAM_ACCOUNTABILITY_BUILD.md`). They preserve source-linked internal review requests and missing contract prerequisites in program assessments. Actual payer contracts, verified attribution and payment entitlement remain unresolved. These layers add no clinical authority or model qualification.

The authoritative active runtime is the original-cohort v1.18 warehouse in `config/agere/active_warehouse.json`: 1,614 synthetic patients and 1,049,339 received records. Medication item-level reconciliation and exact incremental input fingerprints are connected to the existing question/state/acquisition path. See `MEDICATION_ACCOUNTS_BUILD.md` and `INPUT_FINGERPRINTS_BUILD.md`. No older forecast or local-student qualification automatically transfers to this snapshot. Two current-source classifier variants are attached only as optional shadow readers; neither passed automatic acceptance. See `SOURCE_DISTILLATION_BUILD.md`. No current-source forecast is attached. Protocol supporting documentary context is also delivered (`PROTOCOL_SOURCE_CONTEXT_BUILD.md`); the outcome-learning build now provides timing qualification and protected development comparisons (`OBSERVED_OUTCOME_LEARNING_BUILD.md`). All three current-source tasks declined fitting because qualifying targets lack both observed classes; no clinical predictor is established.

The following describes the historical population-stack-v3 milestone, not the current source/model qualification:

The historical runtime was population stack v3, with the attached
longitudinal v3 evidence ledger, persistent program work and clinical-state v2
documentary prerequisites. It runs complete structured/text/temporal population
composition, bounded registered operators, local fitted research predictors,
source-change mathematics, Jev/Perplexity reviews, economical routing with frontier
escalation and a pinned offline medication-identity subset. Internal
evidence requirements are not reviewed clinical protocols or eligibility decisions.
General discovery, clinical causal explanation, verified care execution and recommendation
composition remain incomplete. Earlier prototypes are useful starting points,
not automatically integrated or validated features. Read `AGERE_BUILD_GAPS.md`,
`LONGITUDINAL_BUILD.md`, `AGERE_RESEARCH_INTAKE_2026-10-09.md` and the artifact
implementation receipts and `AGERE_STACK_RECONCILIATION.md` for actual status.
The latest research adds reviewed scientific assertions, patient applicability,
protocol context requirements and external knowledge-change impact to the backlog.
Research priorities do not themselves
change runtime behavior or add model weights.
