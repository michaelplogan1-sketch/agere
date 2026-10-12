# Broad question planning: shared contract repairs

Delivered 2026-10-12 in G3. Five development questions now execute through the
existing question interface, with explicit partial answers and missing evidence.
This is an engineering increment, not broad clinical qualification or a completed
general healthcare reasoning system. G3 remains active; G5 remains unqualified.

## What changed

The admission question previously stopped because its selected planning request
was 20,074 bytes, above the unchanged 20,000-byte guard. Planner dictionaries now
load only for operations that consume them. Redundant audit metadata stays in the
receipt instead of the model prompt. With the original selector scope held fixed,
the request is 19,964 bytes. Required source names, fields, question segments and
runtime restrictions remain. This is a small measured reduction, not a claim of
large token savings or guaranteed capacity for every future question.

The care/context question failed when a graph schema version was supplied as a
clinical evidence contract. Optional context contracts now enumerate actual
runtime IDs and the empty open-question choice. The performance question failed
when the planner tried to leave required measure definitions unbound. Measure
contracts now expose exact package roles; missing relevant denominator/numerator
definitions leave that requirement unmet while supported observations proceed.
Existing validators still reject invalid input.

A later cross-domain development question exposed a financial receipt cutoff
containing the words "snapshot as_of" instead of a date. Its shared planning
contract now requires a literal date while retaining historical cutoffs and the
runtime check against the snapshot date. Its failed response is preserved. Because
we repaired this case, its final result is not independent generalization evidence.

These are shared operator/loading changes in graph_contracts.py and planner_scope.py,
not question-specific handlers or clinical inference rules. Existing workbench,
runtime execution and clinical validation code were not changed.

## Observed workflow results

| Development question | Before | Final behavior |
| --- | --- | --- |
| Admission/readmission explanation, P-1006 | No executable plan; input too large | Cohort, assertions, sequence, longitudinal evidence and context execute. No admission anchors in this selected evidence; cause remains unanswered. |
| Care gaps and social context, P-3002 | Invalid context contract | Context, dated financial barrier state, functional review, assertions and evidence requests execute. No clinical intervention is established. |
| ACO priorities, whole observed population | Already executed partially | Full 1,614-person denominator retained with review nominations/worklists; attribution, contract terms, causal benefit and savings remain unknown. |
| Physician/hospital performance | Invalid required measure component | Utilization/source observations and accountability review execute; unrelated measure packages are not substituted. No rankings or risk-adjusted performance claim. |
| Financial barriers, appointment follow-through and payment accounts, P-3002 | First attempt failed date validation | Dated source state, received financial accounts, assertions and context execute; appointment relationships and deterioration causation remain unsupported. |

The original four questions improved from one executable result to four. The
fifth also executes after its repair. All five remain explicitly partial. Patient
questions preserve their exact patient; population questions evaluate all 1,614
people. The original question, source digest and receipt cutoff remain bound.
Clinical execution is false in every result. Existing work and follow-up records
are unchanged; the ACO path composes review worklists, not executed care.

## Evidence and cost

- 32 focused contract/catalog/scope tests pass. Actual final graphs pass runtime
  validation after restarting the normal runtime on port 8885.
- Final replay of the original four questions required zero new paid API calls
  and retained the candidate graphs. The repaired fifth used one new plan call.
- Actual browser submission shows the P-3002 question, the one-person cohort,
  20 included context units with no declared omissions, financial source state,
  unresolved functional review and evidence-acquisition/portfolio results.
  Source-context inspection was also exercised for the admission case; its
  omitted evidence kept synthesis disabled.
- Final replay times were approximately 0.95s, 6.34s, 80.99s, 5.81s and 30.00s.
  Earlier candidate ACO and performance executions took 180.89s and 138.29s.
  Warm replay and cold execution are not equivalent performance baselines; the
  80.99s ACO replay still exposes substantial local work.
- 18 distinct returned product-provider requests are accounted for, including
  three failed validations and their selection calls: configured API estimate
  **$1.282504**, not invoice verified. This excludes the Codex/Astra engineering
  conversation, local hardware and human review. It is not the total cost of
  building the product or evidence of best token economics.
- 170 protected files and 148 prior implementation receipts are unchanged.
  The original cohort and active v1.23 warehouse remain; no migration occurred.
  Failed request keys remain failed in the shared provider ledger.

Raw traces, failed outcomes, usage, request-byte comparison, browser evidence and
preservation results are in artifacts/agere-broad-question-planning-v1/.
Verification is reproducible from saved captures without more model calls using
the checks in seal_broad_question_verification.py (it writes new files exclusively).
The existing G6 recovery release remains the preceding recovery artifact; this
increment does not claim a newly packaged portable release.

## Remaining gap and next action

Open questions can still miss capabilities already implemented elsewhere. The
cross-domain answer declares generic appointment follow-through unsupported,
while the product has a dedicated appointment review endpoint and work adapter.
The next bounded G3 increment should connect that existing documentary review to
the common graph/planner path, preserving original question, patient/population
scope, dates and evidence, then verify its composition with another domain and
the applicable work/follow-up path. Do not introduce prompt-specific answers.

Broader source interpretation, clinically qualified temporal states, relevant
measure/attribution definitions, independent model evaluation and latency remain
open. Provider comparisons, causal admission explanations, intervention benefit
and ACO savings are not established here. The full ten-area backlog, population
health, VBC/OAP and medication/payer regressions remain in scope. No claim of
novelty, competitor superiority or an optimal stack follows from these results.

The runtime and existing MVP entry are reachable locally. Stop at this human
turn boundary; heartbeat stays paused. AGERE_BUILD_CONTROL.json owns the next action.
