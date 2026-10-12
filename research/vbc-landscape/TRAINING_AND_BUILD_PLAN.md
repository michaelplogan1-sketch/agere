# Turning the research into a better system

This plan extends the local synthetic prototype and its existing 140-question registry. The research adds 96 formulations and 32 developmental scenario specifications. It does not add 96 implemented handlers. The combined snapshot has 236 question rows, including intentional refinements of earlier questions.

## Separate knowledge, skills and evidence of effectiveness

**Changing knowledge belongs in versioned retrieval and rules.** CMS, state Medicaid and commercial-plan documents should be stored with authority, publication date, effective period, jurisdiction, product, participation requirements and supersession links. Retrieve the exact controlling clause and compile explicit logic into a typed representation. Tests and expert review precede activation. A rule update should trigger impact analysis, not retraining of every model.

**Reusable skills are candidates for training.** Examples include finding an assertion in a bloated note, identifying its subject, preserving negation, resolving time, identifying an appropriate hold, compiling a question into an analysis plan, recognizing an unsupported causal claim, and stating why data are insufficient. Train a small model only after a baseline demonstrates enough repeated errors or cost to justify it.

**Clinical intervention effects are evidence to appraise.** A trial result becomes an evidence record with its population, comparator, outcomes, design and limitations. It does not become a universal patient label. A frontier model's explanation is not a gold-standard treatment-effect estimate.

**Financial arithmetic belongs in deterministic code.** Store contracts as executable, versioned clauses; reconcile benchmark, actual spending, bonuses, expenses, quality gates and overlap. A model may extract a candidate clause or explain an audited calculation, but should not invent the financial result.

## Proposed evidence record

Each research claim should carry:

```text
claim_id, source_id, source_version, publication_date, reviewed_scope
population, setting, intervention, comparator, outcome, follow_up
study_design, estimate, uncertainty, adjustment, missingness
funding_or_interests, implementation_components, transportability_limits
author_claim vs reviewer_inference, source_span_or_table_locator
review_status, clinical_reviewer, permitted_reuse
```

The current `evidence_catalog.json` is a source-level starting point with scope and limitations. It is not yet a fully extracted, independently adjudicated claim database. That more detailed representation is the next research-ingestion implementation target.

## Proposed patient assertion record

```text
patient_id, source_patient_family, source_record_id, source_type
subject, concept, value, unit, polarity, certainty, status
event_time, available_time, valid_from, valid_to, correction_of
exact_evidence_span, source_hash, independent_evidence_chain
reader_version, ontology_version, confidence_calibration_status
contradictions, completeness, reviewer_correction
```

This implements the temporal evidence-store direction already set out in `ENGINEERING_RESEARCH.md`. Keep source facts separate from derived clinical judgments. Evidence that arrives later must not leak into historical evaluations. Copy-forward duplicates retain lineage; they do not become independent corroboration.

## Curriculum by component

| Component | Supervision to develop | Baseline before training | Acceptance question |
|---|---|---|---|
| Narrative reader | Span, subject, negation, time, actual use, appropriate hold and contradiction labels | Existing reader and hosted shadow reader | Did it preserve answer-changing evidence and cite an exact source? |
| Retrieval/reranker | Relevant, contradictory, excluding and stale evidence labels | Lexical + embedding retrieval | Does improving retrieval preserve exclusions and patient/time boundaries? |
| Question compiler | Natural question → typed cohort, measures, time, sources and operators | Templates and structured frontier output | Does the executed plan match the requested question and available data? |
| Policy extractor | Clause → draft rule with authority, effective dates and unknowns | Structured extraction plus deterministic validation | Does it distinguish proposed, finalized, effective and superseded provisions? |
| Evidence appraiser | Design, comparator, uncertainty, causal limits and unsupported claim labels | Structured prompting plus source retrieval | Does it distinguish an RCT from a randomly sampled observational comparator? |
| Router | Task-specific error/cost outcomes and consequence labels | Deterministic routing | Does escalation improve quality enough to justify its total cost? |
| Explanation layer | Supported finding, unknowns, action rationale and citations | Templates | Can each consequential sentence be traced to evidence or marked as a hypothesis? |

Jev, MedGemma or other specialist/small models are candidate components, not required winners. Compare them on the same task contract. Do not invoke every model on every patient. The research does not establish which available model or ensemble will win.

## Extend synthetic worlds deliberately

Keep the existing synthetic chart library for the clinical patterns it actually represents. Do not silently stretch an adult chronic-disease corpus into a valid pediatric, maternal or payer-contract benchmark.

Build additional synthetic modules:

1. **Patient goals and outcomes:** symptoms, function, goal changes, nonresponse and caregiver burden.
2. **Access and delivery:** appointment availability, capability, transportation, referrals, waitlists and actual fulfillment.
3. **Benefits and contracts:** fictional plan versions, enrollment changes, claims runout, risk corridors, program payments and overlapping attribution.
4. **Policy lifecycle:** fictional proposals, finalized rules, amendments, effective dates and withdrawal notices. Later map to reviewed real specifications.
5. **Intervention evaluation:** known assignment mechanisms, confounding, regression to the mean, attrition and treatment-effect heterogeneity.
6. **Maternal/pediatric and household relationships:** separate persons, verified relationships, life-stage transitions and family supports.

Hidden truth should be generated independently of the inference text. Generate decisions, causal structure and data-availability times before writing prose. Include favorable and unfavorable treatment effects, inaccessible services, appropriate non-treatment, already-addressed needs, contradictory records and source failures.

The current `synthetic_case_specs.json` supplies developmental requirements for these modules. It contains no completed generated cohort and no evidence that a model passes them.

## Split design and review

- Preserve source-patient family identity across repeated draws, worlds, rewritten notes and derived examples. Split at that family level.
- Hold out sites, policy versions, intervention mechanisms and phrase families as separate generalization tests.
- Treat the current published scenarios and example labels as development material. Create a fresh sealed evaluation set after design decisions; do not advertise these familiar cases as unseen.
- Teacher-generated labels are provisional. Use independent clinical review for clinical judgments, policy/contract review for rules and evaluation expertise for causal claims. Adjudicate disagreements and retain alternatives/uncertainty.
- Keep public research text out of raw-text fine-tuning by default. Original annotations and synthetic examples are the initial curriculum; document-specific reuse permissions need checking before redistributing or training on full source text.

## Evaluation that matches the product ambition

Measure at a stated coverage level rather than rewarding confident answers to everything:

- Correct patient/subject, negation, time, values and evidence spans.
- Retrieval of decisive positive, exclusionary and contradictory evidence.
- Cohort membership and outreach precision after already-treated patients and appropriate holds are accounted for.
- Policy version correctness and missing-input abstention.
- Reproducible financial arithmetic and correct gross/net/causal labels.
- Unsupported causal claims, false discovery rate and replication across worlds.
- Calibration and error concentration by subgroup, source and missingness pattern.
- Total dollars and reviewer effort per correct, supported, decision-useful answer; cold and warm latency; cache correctness after corrections.

Compare deterministic/structured, reader-assisted, small-model, frontier-only and routed-stack approaches on the same evidence, truth and information cutoffs. The simulated Arcadia-class measure platform and TextQL-class semantic layer remain class reimplementations, not the vendors' products. Include data engineering, extraction and review costs, not just API tokens. Local inference is not free simply because it avoids an API invoice.

Synthetic benchmarks can establish computational behavior under specified conditions. They cannot prove real clinical outcomes or causal superiority. A later prospective deployment needs clinical review, realistic source variation and an appropriate outcome evaluation.

## Build sequence

**First: make evidence and rule changes auditable.** Implement the assertion record, versioned rule registry and dependency map. Start with the existing kidney/HF synthetic slice, plus policy-timing and outside-care cases. Exit when every consequential judgment points to dated source evidence and changes correctly after a correction.

**Second: implement the question-to-operator contract.** Extend only questions supported by available data. Add policy lifecycle, deterministic financial distinctions and population measure denominators. Unsupported questions should return the exact missing inputs. Exit when the same question and snapshot produce the same audited computation.

**Third: evaluate retrieval and selective model routing.** Add hybrid retrieval and reranking that preserves exclusions. Compare reader options, cache assertions rather than whole narratives, and escalate difficult/high-consequence cases. Exit when a route improves the measured quality/cost tradeoff without hiding consequential errors.

**Fourth: add discovery and intervention learning.** Add process mining, constrained feasibility and statistically disciplined hypothesis testing after trustworthy data and executable questions exist. Report estimated effects only when the design supports them. Exit when discoveries survive negative controls and held-out validation and have an identifiable decision owner.

**Fifth: validate beyond synthetic data.** Secure appropriately governed data and partner evaluation. Expand clinical scope only after task-specific review. There is no basis yet for claims that the product improves real patient outcomes or exceeds any named vendor.

No paid model calls or fine-tuning were performed for this research. The earlier synthetic API comparison budget was not used by this project.
