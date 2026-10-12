# Jev, local specialists and a frontier API

Design proposal — October 8, 2026. This document does not enable hosted inference.
Current local runs and benchmarks use synthetic data. No employer systems are dependencies.

## Recommendation

Build a routed hybrid: code for explicit rules and arithmetic; specialist judgments
for interpreting evidence; a frontier model for unfamiliar planning and difficult
cases; a versioned evidence store for reuse. Promote components only when matched
evaluations show an improvement in correctness, coverage and total cost.

The recent Qwen experiment tested one model/prompt/reader configuration. It does
not establish that all local models fail, that Jev will succeed, or that an ensemble
automatically improves care. The existing lexicon stays the default during comparison.

## Existing assets and concrete changes

`/Users/mikel/insight-engine/ie/llm/jev.py` already calls the TypeSafe System One API,
retrieves deduplicated subject-specific excerpts and asks bounded questions. Reuse
its question bank and retrieval ideas, not its complete integration unchanged:

- Replace the moving model alias with a versioned ID. The live documentation currently
  identifies `jev-1.13.0`; record the actual response model as well.
- Preserve full answer distributions, usage, timestamps and evidence IDs. The old
  adapter reduces responses to a chosen label and confidence.
- Select and validate supporting source spans. The old judge path can attach lexicon
  evidence or a generic `model: ...` string; that is insufficient provenance.
- Distinguish absent documentation, contradictory evidence, service failure and an
  actual clinical reason. Never convert 'nothing documented' into 'never offered'.
- Make retrieval limits and missing candidates observable. No silent tail truncation.
- Batch independent questions about the same evidence; use another call only when
  an answer is needed to construct the next request.

The new local runtime intentionally rejects `network_required` readers. Introduce
an explicit hosted-provider execution policy rather than removing that guard.
Offline remains a supported mode. The gateway owns allowed providers, model IDs,
timeouts, bounded retries, request/token budgets and accounting. A provider error
must yield a review/failure state, not an empty clinical answer.

## Component responsibilities

| Component | Job | Evaluation requirement |
|---|---|---|
| Source adapters and terminology | Resolve identity, dates, units and coded concepts; preserve original records and conflicts | Correct joins, units, event/arrival times and provenance |
| Rules and SQL | Known eligibility, medication transactions, lab trends, measure arithmetic and denominators | Versioned rules and executable boundary tests |
| Retrieval | Find relevant passages using terminology, lexical search and optionally embeddings | Candidate recall, including unusual phrasing and temporal context |
| Jev | Bounded assertions, reason classification, source selection and claim-support checks | Task-specific discrimination, calibration and acceptance coverage |
| Local medical/general model | A challenger for extraction or bounded classification | Same evidence/tasks as Jev; add only where it improves results |
| Frontier API | Novel question planning, unresolved evidence and policy-document interpretation; explain verified results | Evidence support, tool correctness, cost and abstention |
| Statistical models | Later: outcome prediction, risk-adjusted comparisons and intervention-effect analysis | Outcome labels, temporal validation, confounding analysis and uncertainty |

An embedding model retrieves candidates; it does not establish clinical truth.
A second reader is not automatically an independent verifier. Agreement between
models is a signal to evaluate, not ground truth.

## Shared decision contract

Each judgment should return a task/concept and version, subject/patient key, selected
label or explicit unknown/conflict state, evidence IDs and exact source offsets,
event time, assertion/experiencer/temporality, model and question versions, raw
probabilities when supplied, acceptance/review status, usage and elapsed time.
Keep provider confidence separate from calibrated task acceptance and clinical risk.

Allow multiple independent reasons to coexist. Use separate yes/no questions where
several barriers may apply; use a choice only for mutually exclusive states.
Code resolves precedence using time, source semantics and the applicable rule.

## Frontier API

Start with a Claude adapter because the original engine already uses Anthropic;
keep the interface provider-neutral. Use schema-constrained outputs and controlled
tool calls. Candidate tools are `get_patient_evidence`, `run_cohort_query`,
`compare_segments` and `get_program_version`; these names describe proposed functions,
not services already implemented. Code validates arguments and executes read-only
queries. Return evidence and metric IDs with the explanation and verify references.

The frontier model can plan a new population question upfront. Its chart-reading
role is selectively invoked for unresolved cases after retrieval has supplied the
needed context. Missing source data stays unknown; reasoning cannot recover it.
Send relevant excerpts and computed facts, with audited escalation to broader context
when justified. Keep API credentials in the local backend environment, never the UI.
Live comparisons require configured provider credentials and an explicit budget.

For changing CMS or payer programs: retrieve official/current source documents,
draft a structured rule specification with citations and effective dates, review
and test its logic, then publish a versioned executable rule. Models can propose the
translation; deployment does not happen automatically from a generated summary.

## Routing and costs

1. Reuse unchanged validated evidence and deterministic computations.
2. Apply a specialist only to unresolved semantic judgments.
3. Escalate when task-calibrated acceptance fails, evidence conflicts, retrieval is
   incomplete, or the question falls outside a supported task.
4. Recheck the frontier answer against source and executable rules; retain review
   when unresolved. Do not default to majority voting.
5. Sample accepted outputs for audit to detect confident errors and routing blind spots.

Cache by source hash, task/ontology/rule version, question version and model revision.
Reprocess affected evidence rather than whole populations when a source or rule changes.
Measure retrieval, all model calls, retries, verification, hardware and review cost
per correctly resolved task at the required coverage. A cascade can cost more than a
single frontier call when too many cases escalate; retain the direct frontier arm.

## Implementation sequence

1. Add a shared decision contract, evidence selection and a tested Jev provider adapter.
2. Add the hosted gateway and Claude adapter with structured output and cost limits.
3. Compare existing rules, rules + Jev, a matched frontier reader, and the routed
   combination. Separate reader-only comparisons from end-to-end tool-enabled systems.
   Report cohort/worklist errors, reason errors, retrieval misses, evidence support,
   review coverage, latency and total measured cost.
4. Fit routing thresholds on a calibration split. Evaluate on new synthetic worlds
   and source-patient-family splits; prior pilot cohorts are development evidence now.
5. Introduce MedGemma or another local specialist only as a measured challenger.
   If training is justified, use clinician-reviewed labels and independently held-out
   evaluation. Frontier suggestions can accelerate labeling but are not automatic truth.

TypeSafe documents that Jev is not customer fine-tuned or LoRA-adapted. Customize its
state and question criteria. Any privately trained local classifier is a separate model.

## Sources checked

- [TypeSafe API](https://docs.typesafe.ai/api): typed state/questions/answers and usage.
- [TypeSafe models](https://docs.typesafe.ai/models): pinned versions and customization limits.
- [TypeSafe confidence](https://docs.typesafe.ai/confidence): distribution-derived confidence.
- [TypeSafe choice](https://docs.typesafe.ai/primitives/choice): defined options and probabilities.
- [TypeSafe extraction cascade](https://docs.typesafe.ai/cookbooks/sde_cascade): a relevant architecture example, not healthcare validation.
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs): schema-constrained API responses.
- [Claude tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works): application-controlled tool execution.
- [Google MedGemma](https://developers.google.com/health-ai-developer-foundations/medgemma): candidate medical model family.
