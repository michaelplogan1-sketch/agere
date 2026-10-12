> Later delivery on October 10: the evidence-to-work path now includes dated coverage/provider participation, documentary prerequisites, scoped local packet execution, exact source follow-ups, stable reference reconciliation and saved interpretation attachments. See [latest follow-through build](FOLLOWUP_ANSWERS_BUILD.md) and [default synthesis build](DEFAULT_STABLE_SYNTHESIS_BUILD.md). These engineering capabilities do not establish clinical quality, real benefits, care delivery or outcomes. The inventory below retains its earlier report snapshot.

> Subsequent delivery on October 10: buyer service configuration, 21 public pathway discovery entries, source-linked matching and assigned internal evidence handoffs are now connected. See [buyer capability build](BUYER_CAPABILITIES_BUILD.md) for evidence and limits. The inventory below records the earlier report snapshot.

# Agere stack status — October 10, 2026

Agere is a connected local prototype for synthetic healthcare data. It can move from a supported question to a complete selected population, inspect source evidence, compose calculations and rules, generate bounded source-cited interpretations, and prepare persistent review work. It is not yet the complete clinical/VBC operating product described in the product direction. Clinical accuracy, useful current-cohort prediction, autonomous treatment recommendations, signed-contract payment accountability and customer production readiness remain unestablished.

This report answers the user's request now. The latest delivered implementation is `RECORD_EVIDENCE_BUILD.md`; no later implementation milestone is claimed. It reconciles current runtime status with historical receipts. Old 10,000-patient results and older warehouse/model attachments are not current deployment evidence.

The live read-only status captures are in [the status evidence directory](/Users/mikel/clinical-insights-local/artifacts/agere-stack-status-2026-10-10). No inference, model fitting, clinical review or product-code change was performed to prepare this report.

## Active foundation

| Item | Verified current state |
|---|---|
| Data | Original-cohort synthetic healthcare warehouse v1.23 |
| Population | 1,614 synthetic patients |
| Received records | 1,238,686 across 158 source categories |
| Warehouse cutoff | 2029-08-15, a date within the synthetic dataset |
| Search index | 2,334,396 passages; all transported source fields have lexical indexing |
| Dense search | 107,251 distinct eligible narrative texts, using MiniLM; structured-only passages are not all densely embedded |
| Baseline annotation bindings | 34,621 exact source spans; this is not a count of correct or fully understood statements |
| Execution catalog | 33 registered graph-operation types; registration is not proof of complete domain coverage or an available model for every operation |
| Runtime | Local Python/SQLite application at [Agere](http://localhost:8885/stack) |
| Data boundary | Received source data only in runtime evidence; original generator, hidden truth, frozen experiments and separate research corpus preserved |

The source inventory spans encounters, notes, labs, medications, pharmacy, referrals, hospital/procedure/pathology records, home health, community services, coverage, claims, billing, payments, goals and other sources. Transporting all 158 categories does not mean Agere understands every field or can answer every clinical question about them.

Sources: [active configuration](/Users/mikel/clinical-insights-local/config/agere/active_warehouse.json), [v1.23 build](/Users/mikel/clinical-insights-local/WAREHOUSE_V123_BUILD.md), [live metadata capture](/Users/mikel/clinical-insights-local/artifacts/agere-stack-status-2026-10-10/meta.json).

## Capability inventory

“Connected” means the code is integrated into the local question, evidence or review path. It does not mean independently validated clinical performance. Historical research implementations are identified separately.

| Layer | What is implemented | What remains |
|---|---|---|
| Source intake and identity | Versioned adapters, pinned snapshots, exact patient crosswalks, raw-source hashes, linked-record checks, immutable provenance and explicit unresolved links. | Customer connectors, broader ontology mapping, independently adjudicated identity/episode resolution and governed continuous ingestion. |
| Time and longitudinal records | Distinct source, arrival, clinical-event and literal-reference clocks; multiple event roles; observed sequences; provisional episodes; corrections, repeated text and opposing accounts. | Faithful historical replay of superseded records, stronger cross-source episode reconciliation and general current-state interpretation. |
| Search and inspection | FTS5/BM25, MiniLM dense retrieval, reciprocal-rank fusion, BGE reranking, source atlas, exact text/field inspection and connected evidence graphs. | Measured retrieval recall and task sufficiency across the full question curriculum; retrieval rankings are not population denominators. |
| Question planning and populations | Local compiler and exact plan cache, bounded model-assisted composition, typed graphs, cohort/time predicates, unions/intersections/differences, comparisons and full matched/nonmatched/unknown accounting. | More unfamiliar question families and reusable clinical/provider/payer semantics. A planner cannot supply an absent operator or definition. |
| Narrative interpretation | Baseline assertion/subject/time/relation annotations, exact spans, statement refinement, source-role handling, copy/history controls, bounded specialist review and source-linked proposals. | Reliable broad meaning extraction, scope/negation/subject/date accuracy, independent reviewed targets, calibrated acceptance and current-source qualifications. |
| Cross-source answers | Bounded Claude synthesis, whole-unit source passes, reconciliation groups, exact-citation validation, immutable saved answers, resumable partial progress and replay. | Whether selected evidence is sufficient, whether conclusions follow clinically, cross-group reasoning coverage and stable source-pass identifiers inside reconciliation prompts. |
| Evidence needs and missingness | Supported/contradicted/unresolved logic; separate absent source, absent field, untransported field and unresolved meaning; conditional evidence impact; acquisition proposals; exact question-to-source follow-up. | Complete need-resolution lifecycle with explicit addressed/unresolved/invalid-binding dispositions, reviewer provenance and recomputation of affected conclusions. No learned probabilities distinguish missing care from missing records. |
| Shared source state | Reusable assertions, reviewed-source reconciliation, documentary state by relationship and clock, exact dependency checks and selected-patient reuse. | General patient/care state, robust current use/completion/exception interpretation and evidence-supported state uncertainty. Source review remains distinct from clinical truth. |
| Medication interpretation | Pinned limited RxNorm mapping, name/formulation checks, supported dose notation, order/fill/administration/use/hold distinctions, item-level pharmacy/request/copy/review chains. | Comprehensive ingredient/product/route/strength/frequency coverage; confirmed current regimen, indication, contraindications, intolerance, maximum tolerated dose and complete optimization logic. |
| Clinical protocols and program applicability | Versioned review definitions, typed evidence prerequisites, literal/narrative inputs, documentary requirements, protocol-to-program adapters and before/after rule impact. | Broad reviewed clinical definitions, patient applicability, confirmed preferences/consent and clinical/program/payer eligibility kept distinct. Existing HF/CKD examples are review workflows. |
| Program operations | Persistent local queues, stable patient/program identity, owner/due date, immutable assessments, append-only events, idempotent changes, reassessment/reopening and reported lifecycle states. | Authenticated staff, real communication/scheduling/EHR integrations, verified consent/completion, operational capacity and outcome follow-through. External execution is intentionally deferred; no patient contact is authorized here. |
| Specifications and measures | Quoted immutable document/rule packages, effective periods, mapped/unbound inputs, mapping review history, version-impact calculations, denominator/numerator/exclusion/attribution composition and uncertainty bounds. | Automatic trustworthy document-to-rule intake, reviewed real CMS/commercial/Medicaid specifications, episode/period measures, genuine attribution and certified reporting. Current demonstrations use labeled engineering specifications. |
| Financial accountability | Exact-cent account reconciliation, payment/refund/cash links, snapshot-clock conflicts, observed claims development, financial review requests and financial context attached to program assessments. | Signed payer terms, benchmarks, risk adjustment, complete attribution, contract-specific OAP formulas, program costs, attributable savings and settlement reconciliation. Accounting identity checks do not establish ROI. |
| Outcomes and prediction infrastructure | Versioned observed outcomes, index/follow-up windows, baseline export, receipt-time decision landmarks, leakage checks, protected partitions, guarded fitting/comparison code and failure receipts. | Useful qualified current-source models, endpoint variation, reference quality, calibration and external evaluation. The live v1.23 outcome-learning endpoint has no attached reports; no current validated predictor is claimed. |
| Descriptive analytics and discovery | Renal trends, event/sequence comparisons, source-change detection, observed claims runout, exploratory statistical comparisons and explicit assumptions. | General replicated subgroup/pathway discovery, case-mix-aware provider comparisons, valid causal studies and useful predictions/recommendations tied to outcomes. |
| Cost and reliability | Bounded inputs, native token checks, exact request/result caches, dependency-aware invalidation, per-patient fingerprints, embedding reuse, partial-call preservation, local execution and shared budget ledger. | Quality-trained routing, broader inexpensive model coverage, total cost including hardware/review, continuous refresh, workload scheduling, scale and latency qualification. |
| User interface | Main question workspace, evidence atlas, analysis library, connected evidence drawer, saved answers, program queues, measures/specifications, interpretation and learning surfaces. Recent answer/evidence flows were exercised on desktop and mobile. | Consistent product polish across all older pages, consolidated supported-capability reporting, authenticated role workflows and deployment-grade accessibility/usability evaluation. |

Key implementation sources: [record evidence](/Users/mikel/clinical-insights-local/RECORD_EVIDENCE_BUILD.md), [multi-pass synthesis](/Users/mikel/clinical-insights-local/MULTIPASS_SYNTHESIS_BUILD.md), [workspace](/Users/mikel/clinical-insights-local/WORKSPACE_EVIDENCE_BUILD.md), [requirements](/Users/mikel/clinical-insights-local/EVIDENCE_REQUIREMENTS_BUILD.md), [medication accounts](/Users/mikel/clinical-insights-local/MEDICATION_ACCOUNTS_BUILD.md), [program work](/Users/mikel/clinical-insights-local/PROGRAM_WORK_BUILD.md), [quoted specifications](/Users/mikel/clinical-insights-local/PROGRAM_DOCUMENTS_BUILD.md), [measures](/Users/mikel/clinical-insights-local/MEASURE_COMPOSITION_BUILD.md), [financial accounts](/Users/mikel/clinical-insights-local/FINANCIAL_ACCOUNTS_BUILD.md). Counts in historical source reports retain their original snapshot and are not substituted for live counts above.

## Models: active roles versus preserved experiments

| Component | Current status |
|---|---|
| Local code and baseline reader | Execute calculations, source/clock checks, supported planning, rules and bounded annotations. Many tasks need no hosted call. |
| MiniLM and BGE | Current local retrieval/reranking; MiniLM also proposes bounded plan candidates. Neither proves clinical meaning. |
| Perplexity Decisions | Connected hosted typed route-selection and optional statement-review adapter. Current configured ID: `pplx-decider-v1.1-27b`. |
| Jev | Connected optional typed review adapter, configured as `jev-1.13.0`. Not automatically called alongside every other model. |
| Claude | Current hosted planning escalation and bounded multi-source synthesis, configured as `claude-opus-5-5`. Source and spending guards still apply. |
| Kev 4B and Liquid d1-3B | Native local bounded-reader implementations and preserved paired experiments exist. Neither passed the frozen calibration gate; neither is the default clinically accepted reader. Reserved families remain protected. |
| Lexical and frozen-MiniLM learned heads | Training/serving code and prior fitted artifacts exist. Authored-development and machine-teacher experiments failed their selective acceptance gates. Live v1.23 reports both semantic students and source distillation **not attached**. |
| BioClinical ModernBERT and Granite | Installed/executed in earlier experiments; frozen encoder/task-head and retrieval work is preserved. Granite is not the active warehouse embedding engine. Earlier execution is not current clinical qualification. |
| Qwen3-4B chat reader | Historical narrow experiment with poor results; inactive in the current question path. This is separate from Kev's checkpoint and task. |
| Outcome models, joint-observation and tensor-train models | Earlier research artifacts exist with negative or insufficient comparisons. They are not current validated clinical models. |
| MedGemma, Chandra, Interfaze/Lev, Mapika, Clef, JevK5 and other shortlist candidates | Research options; not assumed installed, qualified or needed merely because shortlisted. Native PDFKit/Vision extraction was implemented earlier, but a general validated document-understanding pipeline remains incomplete. |

The current semantic-review audit reports **zero saved current-reader refinement results, zero recorded review revisions and zero eligible reviewed readings**. This does not erase the separate baseline annotation layer or saved synthesis answers. It means the active workspace does not yet have the reviewed semantic supervision needed for a qualified learning loop.

Earlier source-distillation heads matched the unreviewed teacher on 6/17 and 7/17 development-validation examples. That is weak teacher imitation, not clinical accuracy. Earlier outcome-learning tasks were refused because available timing-safe targets lacked adequate class variation. Those failures are preserved; migration did not promote or reattach them automatically.

Sources: [live model and learning captures](/Users/mikel/clinical-insights-local/artifacts/agere-stack-status-2026-10-10), [distillation report](/Users/mikel/clinical-insights-local/SOURCE_DISTILLATION_BUILD.md), [local paired models](/Users/mikel/clinical-insights-local/LOCAL_DECISION_BUILD.md), [outcome qualification](/Users/mikel/clinical-insights-local/OBSERVED_OUTCOME_LEARNING_BUILD.md), [decision landmarks](/Users/mikel/clinical-insights-local/RECEIPT_LANDMARK_BUILD.md).

## Mathematics and research still to integrate

Some researched methods already support bounded descriptive jobs: Theil–Sen trajectories, Fisher comparisons, Benjamini–Hochberg adjustment, beta-binomial intervals, Poisson source-change segmentation, CUSUM/EWMA and observed claims runout. Their inputs and limitations matter; source-volume changes do not identify a clinical cause.

Other methods have callable prototypes or prior experiments: AIPW causal estimation, provider shrinkage, constrained allocation, value of information under supplied assumptions, process/network functions, joint patient/observation modeling, learned compression and low-rank tensor-train classification. They lack some combination of current warehouse inputs, justified assumptions, integration or evidence of incremental benefit. The joint observation experiment did not beat its simpler comparator; reduced context size has not demonstrated preservation of every decisive fact.

The preserved research agenda still includes:

- A calibrated joint model of patient state and the process that makes evidence observable; informative missingness and source fault isolation.
- Better irregular-time models, including Neural CDE/GRU-D candidates, graph fusion and independently tested change/regime detection.
- Structured scientific evidence and applicability: study identity, population/intervention/comparator/outcome, effect uncertainty, retractions and exact source/version propagation.
- Causal study design with explicit assignment, time zero, ascertainment, overlap and censoring; robust decisions under competing explanations.
- Provider/site/household effects, spillovers, congestion, capacity and patient-goal-concordant alternatives.
- Acquisition policies that learn useful evidence value from real outcomes and measured costs.
- Replicated subgroup discovery, multiple-testing/search registries, learned deferral and stronger question-planning skills.
- Claims maturity, mixed source/rule/attribution change decomposition, contract incentive responses and outcome-aligned payments.
- Fine-grained incremental query maintenance, insight reversal, feed recovery, backpressure and scheduling.
- Portable adapters and selected use of tools such as MEDS/ACES, DoWhy/EconML, OR-Tools, MAPIE and process-mining libraries when a defined task justifies them.

The 66-file research recovery index, the VBC source/question curriculum and the separate Documents corpus are preserved. That is continuity of saved research, not proof that every paper was independently validated or every proposal implemented. The detailed disposition table is in [research recovery](/Users/mikel/clinical-insights-local/AGERE_RESEARCH_RECOVERY_2026-10-10.md).

## What remains to reach the intended product

1. **Finish the evidence-to-state loop.** Preserve exact need/source/unit/field/time dependencies; record explicit resolution or continued uncertainty; carry stable identities across model passes; invalidate affected findings and work when sources or reviews change.
2. **Broaden and qualify clinical interpretation.** Expand terminology, phenotype, actual medication use, treatment intent, exceptions, tolerance, monitoring, completion and goals. Evaluate retrieval misses separately from interpretation errors using protected, independently reviewed targets. Keep the whole warehouse as the boundary.
3. **Complete real population-health workflows.** Exercise the full chain from accountable population through supported opportunities, owned review, reported enrollment/monitoring and outcome evidence. HFrEF/PharmD is one acceptance workflow; CKD, diabetes, hypertension, prevention, transitions, access and other programs must reuse the same machinery. Current HF review is not yet complete GDMT optimization.
4. **Install genuine knowledge and commercial definitions.** Add reviewed clinical/guideline and payer packages, attribution, period/episode measures, exclusions, patient applicability, program cost and signed commercial/OAP terms. Connect changes to affected patients and financial results.
5. **Make prediction, discovery and recommendations useful.** Qualify current outcomes, fit only when data support it, compare simple baselines, calibrate uncertainty and test external validity. Add supported causal/provider analyses, feasible recommendations and measurable acquisition value.
6. **Turn the learning loop into demonstrated improvement.** Acquire reviewed corrections, retain protected source families, train reusable local skills, evaluate consequential errors/abstention/cost and release only qualified versions. Reattaching a prior failed model would not complete this work.
7. **Package and qualify the product.** Portable customer ontology/warehouse interfaces, independently mapped schema variants, authentication/authorization, tenant separation, secure operations, reproducible deployment, continuous ingestion, observability, recovery and realistic load tests. Then broad end-to-end acceptance and fair comparison with competent alternatives.

The core product goal remains population health, VBC and outcome-aligned payments across the warehouse. Advanced research is valuable where it improves those jobs. No completion percentage, delivery date or superiority claim is supported by the current receipts.

## Evidence and current constraint

The latest implementation passed 73 focused unittest executions plus API and desktop/mobile verification. These are engineering checks, not a cumulative unique-test count or clinical accuracy score. The latest five-record synthesis produced nine findings and 19 structurally checked exact citations in three calls, costing an estimated $0.186144; cached replay made no new call.

**Spending correction after the user's October 10 instruction:** the active ledger now has **no monetary cap**. The prior assistant-selected $50 ceiling has been removed, with usage and request history preserved. The earlier $0.33 was reservation headroom, not actual funds remaining. No settlement is required to resume useful authorized API work. Approximately $3.38 of priced estimates remains recorded for the ledger's tracked requests; this is not an invoice or total project cost. Caching, duplicate/failed-call protection and source/request bounds remain. See [the uncapped-spending receipt](/Users/mikel/clinical-insights-local/UNCAPPED_SPENDING_BUILD.md).

Full queue acceptance remains false. No clinician review, clinical benefit, real patient outreach, qualified current predictor, completed contract settlement or production deployment is claimed.
