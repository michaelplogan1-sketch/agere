# Make reasoning reusable

## Project Wollstonecraft: a bottling-inspired engineering case study

**Research highlight — November 3, 2026.** One ordinary repeat of a synthetic healthcare claims question returned exactly the same 795 patient IDs, claim counts and integer amounts while reducing elapsed time from 128.05 to 6.73 seconds and recorded API-equivalent cost from $1.80923 to $0.00419. The repeat re-executed seven verified native actions and made zero new Claude calls. This is a useful observation to preserve and investigate: a completed investigation became a reusable procedure with explicit validity checks.

This brief records an observed engineering result and a proposed study. It is not a completed comparative research study or an independent clinical evaluation.

## The research connection

Ankit Sonthalia, Haritz Puerto, Alexander Rubinstein, Martin Gubri and Seong Joon Oh introduce BOTTLED in *Agent in a Bottle: Can LLM Agents Turn Their Capabilities Into Cheap, Scalable Artifacts?*, arXiv:2610.08775v1, October 6, 2026. Their introduction states:

> “An artifact can replace repeated, expensive LLM calls with cheaper operations.”

Source: [paper, Introduction, PDF page 1](https://arxiv.org/abs/2610.08775v1). The user-supplied PDF was checked against the public paper's title, authors, version and relevant text.

The paper lets an agent choose a reusable program, small model, retrieval index or hybrid for a large batch of related inputs. Its objective includes both construction effort and subsequent execution. This is the inspiration for our use of verified procedures. Our observed pair is narrower: exact-question execution reuse, with the question and source unchanged. It does not reproduce the paper's multi-instance benchmark or demonstrate transfer to unfamiliar tasks.

The study also reports that 48 of 60 bottling runs fell below the lower bound of their model's zero-shot confidence interval, and 31 fell below the stronger small-model distillation baseline. That supports a practical requirement: compare quality and complete cost before promoting a reusable artifact. [Paper, abstract and sections 3–4](https://arxiv.org/html/2610.08775v1).

## What happened in our stack

The ordinary question was:

> List patients with at least 3 provider claims in 2029 and their total allowed_cents.

The application connected to its existing synthetic healthcare warehouse through the read-only tool and semantic execution layer. Native database operations performed the population selection and arithmetic. Claude supplied the investigation logic on the qualification request; Jev and Perplexity supplied the bounded checks used by the workflow.

An older candidate was ineligible under the current policy. The normal request produced a new candidate, which the application validated by re-executing its actions with a fresh native executor. Selection followed completion of the full request and its downstream workflow contract. A subsequent ordinary HTTP request selected that candidate automatically.

| Measurement | Current-policy qualification | Ordinary repeat |
| --- | ---: | ---: |
| Elapsed time | 128.052538 s | 6.732681 s |
| New provider attempts | 13 | 3 |
| New Claude calls | 10 | 0 |
| New Jev calls | 2 | 2 |
| New Perplexity calls | 1 | 1 |
| Recorded API-equivalent cost | $1.809231572 | $0.004185682 |
| Patients returned | 795 | 795 |

The observed reductions are **94.7% in elapsed time** and **99.8% in API-equivalent cost**. Patient identifiers, counts and integer amounts matched exactly. The pair's 16 new provider attempts and $1.813417254 in known API-equivalent usage remain accounted for; the qualification run is not omitted from the record.

API-equivalent cost is the ledger's priced usage estimate. Claude used subscription transport. These figures are not invoice savings or total operating costs; local compute, subscription allocation, development and review are not fully priced.

## Why this can improve the architecture

The reusable artifact contains seven read-only actions, their inputs and expected native result fingerprints. The application checks identity covering the question and context, source and scope, definitions, model configuration, execution code and feedback revision. During reuse it executes the actions again and compares their results. A mismatch quarantines the candidate and returns control to the reasoning path. Final workflow checks remain in place.

The artifact therefore preserves useful investigation work between requests. Native computation continues to supply exact results, bounded model judgments continue to check the answer, and frontier reasoning remains available for work that lacks a valid reusable procedure. A stored response can be part of the artifact, but it is eligible only after the underlying execution checks pass.

This is **learning at the execution layer with fixed model weights**. Candidate generation, validation, selection, observed outcomes, adverse feedback and quarantine form the improvement loop. The measured pair demonstrates one selected procedure being reused; it does not show an indefinitely improving recursive system. The existing 21 reuse, feedback, quarantine and rollback checks provide engineering evidence for those controls, separately from the timing observation.

## Where the pattern could help other systems

| Candidate workload | Reusable artifact to test | Evidence needed before adoption |
| --- | --- | --- |
| Repeated claims and finance analysis | A parameterized query and typed aggregation procedure | Exact population, dates, units and totals; parameter-transfer checks |
| Repeated document extraction | A scoped extraction procedure or a small task model | Independent source-bound labels, missed qualifiers, abstention and review effort |
| Recurring operational reporting | A versioned sequence of native calculations | Fresh-source execution, schema-drift detection and faithful output |
| Large collections of similar judgments | A qualified small model or hybrid route | Matched simpler baselines, quality, escalation and full workload economics |

These are applications to test, not additional outcomes established by our 795-patient pair. The general design question is whether the expected repeated work justifies building and maintaining a reusable artifact.

## What a defensible follow-up study would measure

The next study should compare the ordinary stack with reuse disabled, the ordinary stack with guarded reuse enabled, a direct native procedure, and a conventional exact-result cache where appropriate. For an extraction task, add a qualified small-model baseline. Give routes the same source information, task contract and verification requirements; report cache and qualification state rather than presenting a warm repeat as a cold-run comparison.

Use a predeclared set of task families and parameter changes with approved independent evaluation data. Preserve all attempts, failures and incomplete answers. Separately test changed sources, dates, definitions, schema, model identity and adverse feedback. Repeated paired observations in randomized order should measure variability, successful coverage, incorrect results, missed qualifiers, latency, escalation and human review effort. Clinical claims require independent clinical adjudication.

The full economic model should include artifact construction, validation, repeated execution, fallback, maintenance, compute and review. If incremental build cost is B, ordinary cost per request is U and reuse cost is R, a simplified break-even count is B / (U − R), provided U > R and quality is comparable. The present pair does not isolate B or establish stable U and R, so it does not establish break-even or projected production savings.

The immediate build priority remains the existing G3 clinical routing/economics increment: preserve completed source-bound work while unresolved clinical conclusions remain unresolved. This research highlight does not replace that increment or authorize a new training campaign.

## Evidence and attribution

### Subsequent harder-question check — November 3

Mike requested a harder question after the visual was prepared. A new ordinary request combined two years of payer medical and pharmacy payments, current/nonduplicate paid claims, unreversed fills, patient joins, deltas, percentages and ranking. Separate Python and prewritten SQL calculations agreed on all 26,880 cells for 1,920 patients; the SQL query and fetch took 0.077 seconds, excluding query authoring, language interpretation, final review and product persistence.

**This harder case failed qualification.** The first run was stopped after 723.537 seconds when verification expanded to thousands of model calls and Perplexity returned throughput errors. Final accounting preserves 4,655 new attempts: 16 Claude, 2,324 Jev and 2,315 Perplexity. There were 603 HTTP 429 failures, one HTTP 503 failure and eight unresolved pending attempts. Known API-equivalent usage is $1.631045282; 612 attempts lack priced usage, so the complete cost is unknown.

The retained candidate SQL returned the full patient set but differed in ten cells across six patients. Its additional same-patient/drug/date exclusion matched eight paid records; none had an exact reversal-record link or matching claim ID. The existing native arithmetic qualifier also rejected the conditional multi-year query shape, leading to packet and field model review. No new candidate was validated or selected, and no repeat was dispatched. The owned app was restored on unchanged product code. This failure narrows what the earlier successful pair supports and identifies concrete routing and source-relation work for the next increment.

Evidence: `artifacts/agere-harder-bottling-2026-11-03/`, including the original protocol, interrupted run receipt, native baseline, retained candidate query/rows, source-link audit and offline diagnosis. The original visual remains explicitly scoped to its earlier successful pair; it is not evidence that this harder case passed.

### References

- **Primary research:** [Sonthalia et al., *Agent in a Bottle*, arXiv:2610.08775v1](https://arxiv.org/abs/2610.08775v1); [authors' code repository](https://github.com/aktsonthalia/bottled). Credit all five authors when describing the research inspiration. The authors have not reviewed or endorsed this implementation.
- **Supplied paper:** `/Users/mikel/Desktop/bottling.pdf`, 22 pages; abstract, introduction and methodology read, with pages 1 and 4 visually inspected. No paper prompts were treated as operating instructions.
- **Measured result:** `artifacts/agere-current-release-closure-2026-11-03/guarded-reuse-pair.json`, plus the `ordinary-current/` and `ordinary-reuse/` request, response and attempt records. Durable evidence copy: `/Users/mikel/clinical-insights-local/artifacts/wollstonecraft-current-release-e7a0d1c-2026-11-03/`.
- **Selection history:** `reuse-candidate-events.json`, events 97–101; candidate `d9f9ae8cd0bcdd7b32fd874203f578ee6db1e41751e364c2561cc45ebf297570`.
- **Implementation:** `clinical_core/agere/clinical/harness_improvement.py`, particularly `identity`, `propose`, `replay`, `finish` and `record_feedback`; product identity `e7a0d1c` in the measurement receipt.
- **Scope:** one same-question warm pair on synthetic data. No ablation isolating each source of the savings; no new model weights; no clinical accuracy, broader transfer or competitor comparison established by this observation.

Suggested public framing: **“Can a useful AI investigation become reusable execution? In one synthetic claims workflow, our stack re-executed a verified seven-step procedure, returned identical results for 795 patients, and reduced elapsed time by 94.7%. Inspired by BOTTLED, we are exploring how to make reasoning an investment that carries forward.”**
