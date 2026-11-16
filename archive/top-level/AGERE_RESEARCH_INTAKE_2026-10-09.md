# Completed research → Agere capability backlog

Reviewed October 9, 2026 after **Research healthcare AI opportunities** completed
(chat `01a12078-934b-7af1-a5d9-4a21ff7db684`, final turn
`01a12092-3eba-7c81-a1bd-54b0d25b7fe8`). This is an implementation intake, not a
new independent literature review or evidence of commercial/clinical superiority.
All local patient runs remain synthetic.

The review read the completed core charter, strategy, model designs, academic and
competitive memos, component assessment, adapter notes, extension notes and source
register. The source register now lists 81 distinct external URLs, superseding
the earlier chat summary's 78. These are URLs, not independent studies. Research
files and the other chat were not modified or messaged.

## Priority carried into this build

The [core charter](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/CORE-PRODUCT.md)
puts population health, VBC, commercial measures and **outcome-aligned payments**
first. HFrEF-to-PharmD delivery is the flagship acceptance workflow, not the disease
boundary. Shared cohort/evidence/protocol/work/measurement primitives must support
other programs. Research additions should improve those jobs rather than postpone
them. No actual patient outreach or prescribing is authorized by this intake;
autonomous execution agents remain a separate later layer.

## What the research found versus what is now built

| Requirement / seam | Actual implementation status after this increment | Concrete remaining work |
|---|---|---|
| Complete populations | Existing cohort/evidence DAG uses complete membership and three-valued predicates; temporal sequence membership now composes with those sets | Accountable attribution, coverage intervals, clinical phenotype packages and additional terminology |
| Separate events and clocks | **Added:** `longitudinal.py` emits separate admission/discharge roles and preserves source, arrival, explicit clinical and literal narrative-reference dates | Full source-history replay for earlier cutoffs; broader event roles and correction relationships |
| Reconstruct episodes and documentary state | **Added:** source-bound narrative date spans, copied-section handling, repeat families, opposing documentary accounts, provisional source/encounter episode pairing and surrounding evidence | Clinical adjudication, cross-source episode resolution, clinical relation expansion, reported actions and updated patient state |
| Medication and tolerance interpretation | Existing `medication_reconciliation.py` distinguishes orders, history/claim supply, reversals, administrations and unresolved transactions; `workflows.py` exposes EF and class panels | Drug/formulation/dose/frequency normalization, actual-use reconciliation, alternative therapies, longitudinal tolerance/hold/contraindication evidence and reviewed optimization protocols |
| Owned program work | **Subsequently added:** persistent work, owner/due date, immutable assessments, staff-reported review/contact/consent/enrollment/monitoring/completion, reopening and idempotent refresh | Authenticated operations, verified care/outcomes and linkage to measure/contract accountability |
| Evidence prerequisites | **Subsequently added:** versioned internal contracts, source-state reconciliation, bounded dose notation and full-population `state_review` | Reviewed clinical protocols, medication terminology, applicability and current phenotype/use/exception/tolerance interpretation |
| Program/contract definitions | Existing `program_registry.py` installs immutable quoted candidate rules with bounded bindings | Attribution, numerator/denominator/exclusion contracts; source-to-rule review and activation; CMS/commercial/Medicaid change intake; signed OAP terms and settlement reconciliation |
| Outcome/causal/resource composition | Current graph accepts membership inputs; prior AIPW, allocation, shrinkage and information-value functions accept explicit tables/assumptions | Versioned typed outcome, estimand, resource and decision outputs with validated dependency types, ascertainment, censoring, capacities and effect assumptions |
| Model economics | Existing local extraction/retrieval/reranking, local mathematics, model routing, shared ledger and content-addressed results | Learn task-specific routing from reviewed consequential errors and total cost, including human review and acquisition |

The new event layer addresses the research's multi-event/date-binding finding in
the **new longitudinal path**. The older `clinical_events.py` timeline operator
still has its earlier single-date contract; it has not been silently rewritten.
Filtering the current snapshot by arrival date still does not recreate historical
records that were superseded or quarantined.

Implementation: [longitudinal layer](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/longitudinal.py),
[graph execution](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/analysis_graph.py),
[planner](/Users/mikel/clinical-insights-local/clinical_core/agere/clinical/graph_planner.py).
Research comparison: [extension notes](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/stack-extension-notes.md),
[adapter contracts](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/adapter-notes.md).

## Build work brought forward

1. **Accountable population and clinical interpretation.** Extend versioned
   patient/coverage/attribution and phenotype contracts; reconcile EF and medication
   regimens across structured and narrative sources. Preserve possible, historical,
   conflicting and unobserved states. Completion: all accessible patients reconcile
   into explicit groups; below target dose is not mislabeled as below tolerated dose.
2. **Persistent program work.** Materialize review candidates into stable work
   items with evidence/rule/snapshot identities, assignment and an append-only
   disposition history. Re-runs update existing work rather than duplicate it.
   Completion: an HFrEF example and an access/affordability example progress through
   review/program states with independent evidence for completed care.
3. **Measurement and OAP.** Add shared metric/denominator/outcome/attribution
   contracts and exact payment inputs. Preserve claims maturity and unknown follow-up.
   Completion: patient-level evidence reconciles to clinical, operational, commercial
   and settlement totals; missing contract terms do not become invented formulas.
4. **Typed downstream results and replay.** Let registered outcome/design/resource
   results feed only compatible operators. Bind the historical source ledger to
   each prediction/measurement cutoff. Completion: a change in evidence or rule
   produces an auditable cohort/metric/work-item difference without future leakage.
5. **Advanced additions tied to those workflows.** Start with insight reversal
   and shared evidence acquisition, then supported effect estimation and capacity
   scenarios. Retain simple baselines and current negative predictor results.

This ordering incorporates the research charter without resetting the existing
retrieval, cohort, reader, mathematics or model stack. Longitudinal reconstruction,
persistent program-work machinery and internal evidence-prerequisite contracts are
now implemented to the scopes in their build records. Verified clinical state,
care/outcome/payment accountability and the remaining advanced additions stay open.

## Subsequent intake — scientific assertions and applicability

The research chat sent its new [System.com review](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/system-review.md)
on October 9; the memo and updated core charter were read. Add these shared
requirements without replacing the population-health/VBC/commercial/OAP core:

- Reviewed, versioned external assertions with exact passages, study identity and
  design, population/exclusions, intervention/comparator, outcome/horizon,
  estimate/units/uncertainty, adjustments, provenance and corrections/retractions.
  Separate association, causal estimate, guideline, label, local protocol and
  payer rule; reports of one study are not independent votes.
- Versioned terminology and supported/mismatched/unresolved patient applicability.
  Medication ingredient/formulation/route/strength/dose/frequency and order/fill/use
  distinctions are prerequisites for the HF flagship and reusable elsewhere.
- Reviewed protocols declare required context; retrieval returns supporting,
  contrary and missing evidence without using a top-k sample as the denominator.
- Knowledge changes identify affected rules, assessments, patients, work items and
  financial scenarios, with reviewed activation and auditable corrections.
- Evidence-to-assessment-to-program-to-verified-outcome-to-exact-contract traceability.

These additions are **not implemented** by reading the memo. New internal
prerequisite contracts and existing provenance/reassessment machinery provide
reusable infrastructure. System is an optional replaceable licensed source, not a
selected dependency. No purchase, data transfer, outreach or action-agent build is
authorized or performed by this intake. See `AGERE_STACK_RECONCILIATION.md`.

## Disposition of the nine advanced designs

Each has established prior art. The proposed integration and product benefit
remain hypotheses. The [model-design report](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/model-designs.md)
contains the source links, formulations and failure conditions.

| Design | Existing reusable part | Missing addition and placement |
|---|---|---|
| Care versus observation response | Joint-observation prototype, source diagnostics, new documentary accounts | Intervention/assignment and independent outcome ascertainment; later causal observation-policy model |
| Decisions robust to competing explanations | Conditional kernel and AIPW prototype | Justified effect/sensitivity sets, separate uncertainty types and feasible action frontier |
| Congestion and spillover-aware network decisions | Supplied-benefit allocator and network summaries | Actual queue/capacity/task-duration data, exposure graph and response simulation |
| Population evidence acquisition | Single-acquisition information-value calculator, provenance | Shared source dependencies and acquisition outcomes; first count affected decisions without invented utility/probability |
| Question-to-study design | Typed planner and unmet requirements | Estimand/time-zero/outcome/censoring/assignment contracts and feasibility checks |
| Why metrics changed | Source/rule versions and claims triangles | Historical denominator/attribution/rule replay with order-dependence and residuals shown |
| Goal-concordant feasible care | Some goals/preference annotations and bounded allocation | Confirmed preferences, burden/task durations, caregiver and travel constraints, supported alternatives |
| What would overturn an insight | Conditional fact kernel, exact sources, result cache | Authenticate support IDs, typed plausible source/rule corrections and bounded re-execution with decision diffs |
| Incentive-response simulation | Contract arithmetic prototypes | Grounded behavioral scenarios and separate clinical, coding, access, workload and payment outcomes |

The two longer-horizon bets—preserving future care options and learning minimum
local evidence for transfer—remain research items, not reasons to delay the core.

## Model and library decisions from this review

Retain the working Jev/Perplexity typed reviewers, frontier planner, compact reader,
local retrieval/reranking and fitted research baselines. This review does not
establish a replacement-model winner or install an unmeasured ensemble.

- MEDS/ACES: candidate loss-aware event/task interchange adapters, after preserving
  clock semantics and provenance; not a replacement for the evidence ledger.
- DoWhy plus one EconML comparator: candidates when causal contracts and inputs exist.
- OR-Tools: candidate when real program capacity/assignment exceeds the existing solver.
- MAPIE: candidate after an appropriate calibration population and endpoint exist.
- MEDS-Torch or a FEMR-derived training environment: choose one research challenger
  after longitudinal labels and historical replay are adequate.
- MedGemma: extraction challenger, not an automatically better planner.
- Stanford released CLMBR/MOTOR weights, PM4Py and framework version compatibility
  need the specific terms/revisions checked before reuse. No new packages or weights
  were installed from the research report in this increment.

These dispositions follow the [component assessment](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/open-source.md).
Upstream versions and terms should be freshly verified and pinned at implementation.
The [academic review](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/academic-research.md)
and [competitor dossier](/Users/mikel/Documents/Healthcare-Intelligence-Research-2026-10-09/competitors.md)
do not constitute executed comparisons with actual vendor products.

## Evidence from the implemented increment

`artifacts/agere-longitudinal-v3/run.json` records 10,000 synthetic patients,
313,586 source-record observations and 103,163 source-bound narrative annotations.
The new source-clock example computes 144 matching patients and 144 provisional
admission/discharge pairs, with 1,706 surrounding observations and 122 repeated
text families. No opposing same-concept/date/clock documentary accounts were found
in this selected example; that is not proof the warehouse has no contradictions.

The explicit-clinical-clock example preserves 144 undated admission anchors and
4,743 observations that cannot be placed on that clock. It returns no dated context
because the anchors have no explicit clinical date. Independent raw ADT scans agree
with sequence counts. One actual frontier request produced the same cohort →
sequence → longitudinal graph; all population execution was local. Cached local
replay made no paid calls.

Receipts: [local execution](/Users/mikel/clinical-insights-local/artifacts/agere-population-stack-v3/examples/longitudinal-execution.json),
[frontier execution](/Users/mikel/clinical-insights-local/artifacts/agere-population-stack-v3/examples/longitudinal-execution-live.json).
These checks establish implementation behavior, not verified clinical causes,
program effectiveness, prediction superiority or clinical outcomes.
