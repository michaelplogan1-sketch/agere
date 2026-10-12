# Context engineering: comparison with Agere

October 9, 2026. Review only; no model or product code changed.

Source: David JH Wu, Jonathan H Chen and Ethan Goh, “Context is key: context
engineering as the next frontier of medical AI,” BMJ Digital Health 2026;2:e000032,
doi:10.1136/bmjdh-2026-000032. User-supplied file:
`/Users/mikel/Desktop/context engineering medical ai.pdf`.
All three pages were extracted and visually inspected, including Figure 1,
publication statements and references. The cited studies were not independently
reproduced or reviewed in this task. The source is an editorial, not a trial of
Agere or a validated implementation specification.

## Assessment

Agere's architecture substantially follows this approach. The implementation is
partial. The paper organizes context engineering into retrieval, processing and
management: selecting relevant evidence, structuring it, and maintaining context
across an interaction. It also advocates tools and deterministic systems, and
warns about lost evidence, summary errors, outdated context and clinician review
burden. Those principles fit the existing product direction and next build.

## Mapping to actual implementation

| Paper concept | Actual Agere implementation inspected | Boundary |
|---|---|---|
| Retrieve relevant sources | `warehouse_retrieval.py`: FTS5/BM25 and MiniLM retrieval, reciprocal-rank fusion, BGE reranking, source diversity and cohort/time filtering before ranking | Relevance ranking is not evidence sufficiency; returned passages explicitly carry no completeness guarantee |
| Structure the context | Source-bound text annotations, bounded choice/support contracts, terminology identity, structured fields and documentary-state reconciliation | Broad arbitrary-note meaning, actual current use, clinically appropriate exceptions and tolerated dose remain incomplete |
| Preserve temporal context | `longitudinal.py`, `clinical_events.py` and `clinical_state.py`: distinct source/arrival/clinical clocks, literal narrative dates, repeats, conflicts and provisional episodes | Relative-date interpretation, clinical supersession and faithful historical reconstruction are incomplete |
| Maintain and refresh memory | Source/model/code/version-bound retrieval and result caches, derived ledgers and persistent program work | Cached results and documentary state are not a complete dynamic clinical memory system; goals/preferences are explicitly unsupported in clinical-state bindings |
| Combine models and tools | `stack_runtime.py` and `economical_planner.py`: registered deterministic operators, local planning/replay, bounded hosted specialist routing and frontier escalation | Routing coverage is bounded; installed Kev/d1 adapters remain research candidates following their failed automatic-acceptance gate |
| Preserve accountability | Exact spans, evidence/rule identities, unknown/review states and model proposals separated from clinical authority | Valid citations do not prove entailment; comprehensive clinician review and clinical outcome validation remain unfinished |

The paper mentions MCP as one example of connecting models to tools. Agere's typed
local operator graph follows the tool-use principle; this is not a claim that
Agere currently exposes a clinical MCP server or needs MCP to implement context
engineering.

## Concrete gaps exposed by the comparison

The retrieval code constructs candidates with `protected=False`, reranks for
relevance and then applies source diversity and a bounded result count. It does
not have a general clinical guarantee that exceptions, negative evidence and
contradictory accounts survive selection. The existing state and longitudinal
components preserve some of those distinctions after extraction, but that does
not recover evidence that retrieval or extraction omitted.

The current `clinical_state.patient_state` explicitly leaves current therapy use,
appropriate exceptions and tolerance in `unknown_semantics`; it also marks goals,
current affordability confirmation and payer eligibility as unsupported bindings.
These declarations distinguish missing software from missing observations. They
show why the next shared interpretation milestone is necessary.

There are separate bounded context builders for retrieval, specialist calls,
longitudinal views and local experiments. There is not yet a single reusable
task-specific context contract joining all relevant patient/episode state, policy
version, exceptions, contradictions, preferences, unknowns and budget requirements.

## Implications for the next build

Continue the shared interpretation work in `NEXT_STACK_BUILD.md`, making the
context builder an explicit interface. For each task it should assemble:

- The question, patient/cohort scope, cutoff and required evidence.
- Relevant structured findings and exact narrative support.
- Critical exceptions, contrary evidence and clinically meaningful history.
- Applicable versioned knowledge and tool outputs, distinguished from source facts.
- Missing or stale prerequisites, conflicts and any omitted context.
- A model-specific input budget and a retained route to the original source.

Require selection/compression to preserve decisive information, not merely reduce
tokens. Avoid suppressing a contradiction just because one source ranks higher.
Do not turn a retrieved excerpt's absence into a negative clinical finding.
Summaries must retain provenance and uncertainty and be refreshable after source
changes. Generated outputs can be cached as proposals; they must not become
independent corroborating evidence on the next request.

For population questions, calculate complete defined cohort membership with the
warehouse operators before using bounded evidence for explanation or review.
The paper's per-interaction context window does not replace population denominator
accounting, outcome definitions, forecasting or program/payment measurement.

Context engineering complements model adaptation: it controls the evidence a
model receives; training affects how the model interprets it. A better context
does not automatically resolve the local classifiers' observed interpretation
errors, and a stronger model cannot recover unseen evidence reliably.

## What this paper does not establish

It does not select Kev, d1, Jev or Perplexity; provide a specific ontology or
compression algorithm; demonstrate clinical/token savings for Agere; validate a
new mathematical model; or establish uniqueness versus other vendors. Its warnings
about context length are reasons to measure task-specific evidence preservation,
not a universal claim that shorter context always performs better.

The intended Agere scope adds complete-population analytics, missingness reasoning,
discovery, forecasting, versioned payer/clinical rules and action accountability.
Those extensions are product requirements with mixed implementation status, not
proof of superiority. All current Agere patient experiments remain synthetic.
