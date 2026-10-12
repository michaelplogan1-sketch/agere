# Economical question planning — October 9, 2026

The question workbench now defaults to local compilation and economical routing.
Claude is an escalation route. This implements the user's requirement to reserve
frontier inference for work that cheaper components cannot adequately perform.
All patient execution and measurements below use the synthetic warehouse.

## Executable path

1. Exact authored observation plans compile locally, including the medication
   identity graph and the existing compositional examples.
2. Exact question plans are reused only under matching source, snapshot, operator,
   capability, routing-policy and compact-encoder identities. A changed exclusion,
   patient, time or other qualifier cannot inherit the unrestricted question's plan.
   This is plan caching, not semantic answer caching; execution still resolves its
   own source/dependency identities.
3. For a bounded descriptive question, the installed MiniLM encoder ranks complete
   plan descriptions. Perplexity's typed decision API selects among the top three
   and an explicit escalation option. Only the question and plan descriptions are
   sent for this routing decision, not patient records.
4. Constraints outside the registered coverage, unknown terms, low scores,
   uncertainty or a failed specialist attempt escalate to Claude. The cutoff of
   0.95 is an engineering gate, not a calibrated probability of semantic accuracy.
5. Registered local operators execute the resulting validated graph. Paid clinical
   reviews remain explicit graph operations; economical planning is not permission
   to omit needed evidence review or clinical requirements.

`Stack.compose()` defaults to `planning='auto'`; explicit `planning='frontier'`
remains for controlled comparisons. The older `ask(live=True)` UI path also uses
economical composition. Hosted-disabled servers can still compile exact local
plans and replay eligible cached plans. Failed/unpriced attempts remain visible
instead of being counted as free. Cached calls are not charged again.

Qwen3-4B is not active in this question path. MiniLM is a compact retrieval encoder,
not a generative clinical planner. Perplexity is a hosted decision model, not a newly
trained local SLM. Earlier local context/action heads remain separate bounded
components. This increment did not train a general local question compiler.

## Measured behavior

Source: `artifacts/agere-population-stack-v3/examples/economical-planner-v3/execution.json`.

The development routing set has 18 authored questions. Three use the cheap route;
15 select escalation. No accepted route dropped a requirement in these checks.
The 15 escalation decisions were routing probes, not 15 completed Claude answers.
This small authored set is not an independent clinical benchmark or broad coverage
estimate. The first source-coverage paraphrase correctly abstained with competing
source-completeness and feed-volume interpretations; an initial runner assertion
incorrectly expected automatic acceptance and was repaired. That escalation is
preserved in the report. The eventual paired example was selected from a supported
renal wording, so it cannot establish typical savings over arbitrary questions.

For “Display observed kidney laboratory trajectories across the whole population,”
the cheap plan and explicit Claude control produced the same kidney-operator
summary and returned patient preview over a 10,000-patient selected population.

| First recorded planning request for this question | Input tokens | Output tokens | Estimated API cost |
|---|---:|---:|---:|
| Perplexity typed route selection | 284 | 1 | $0.00000568 |
| Explicit Claude planning control | 6,227 | 383 | $0.032568 |
| Exact replay: new paid requests | 0 | 0 | $0 |

These are recorded **planning API estimates**, not invoice-verified costs, total
system costs or proof of equivalent overall clinical quality. Local candidate
encoding, indexing, execution, hardware, review and development are excluded.
Provider receipts retain the original usage even when a later run reuses them.
No model is described as free merely because it runs locally.

## Remaining work

The cheap route has deliberately narrow coverage. Broader question understanding
requires task training and supported operators, not looser string matching.
The next local-model step is to collect reviewed plan/correction examples across
population, temporal, evidence, provider and payer questions; train a compact
selector or constrained compiler; and compare correct supported answers, dropped
qualifiers, abstentions and total cost on frozen held-out question families.
Unreviewed machine plans are stored with `human_reviewed=false`; saving a model
response does not make it an approved training label.

Use the cheapest route that meets the task's quality requirements. Distillation,
dependency reuse, incremental reprocessing and compact structured evidence should
reduce repeated work. Frontier synthesis can then consume verified calculations
and cited evidence for difficult reasoning or a requested deliverable.

## Files and checks

- `clinical_core/agere/clinical/economical_planner.py`: local plans, compact
  candidates, typed routing, qualifier gates, exact cache and cost accounting.
- `stack_runtime.py`, `graph_workbench.py`, `stack_workbench.py`: default runtime
  route, actual role disclosure and visible route/cost receipts.
- `run_economical_planner.py`: live synthetic routing/control/replay execution.
- `test_economical_planner.py`: no-hosted local execution, qualifier preservation,
  score contract, unknown entities, capability/model cache invalidation and failed
  request accounting. The combined focused suite passed 115 checks.
- `MEDICATION_IDENTITY_BUILD.md`: the accompanying deterministic terminology layer.
- `research/agere/MODEL_ROLE_DECISION_2026-10-09.md`: model selection evidence.

