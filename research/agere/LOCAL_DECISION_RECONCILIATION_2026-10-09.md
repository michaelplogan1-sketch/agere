# Agere: reconciling the local decision-model reviews

October 9, 2026. Personal, synthetic research project. This memo reconciles the
Claude review and the pasted Codex trading/quantitative-systems memo with the
existing model-selection records. Completed execution results are in
`../../artifacts/agere-local-decisions-v1/REPORT.md`: both models ran successfully,
neither passed the frozen acceptance gate, and neither replaced the default reader.

## The decision being implemented

Implement Kev 4B first and run Liquid d1-3B as its matched local challenger.
Neither replaces the whole stack or becomes a clinical authority by being
installed. This resolves implementation order, not a clinical-quality ranking.

| Record | Reconciliation |
|---|---|
| `CLAUDE_SLM_EVALUATION_2026-10-09.md` | Adopt its faithful Kev pointer-head requirement, revision/hash manifest, bounded evidence task, separate calibration families, matched d1 run, and error attribution. Treat its named primary model as the first candidate to implement. |
| `EDGE_MODELS.md` | Consistent with Kev as the first local adaptation candidate. Its external GGUF Kev row is a different build from this native BF16 MLX run. Hosted d1 and local d1-3B remain distinct. External ranks do not decide this experiment. |
| `FOUNDATION_DECISION.md` | Retain model-independent contracts and task-specific components. Rejecting an earlier Qwen3 chart-generation recipe does not reject Kev's Qwen3.5 backbone, trained adapter, or pointer readout. Its historical “not installed” descriptions are superseded by execution receipts. |
| `MODEL_SELECTION_REVIEW.md` | BioClinical remains an evidence-learning workstream; local bounded decisions are a separate role. Move d1 from “later” to the paired challenger now. MedGemma remains a document-reader candidate. Existing hosted adapters are unchanged by this work. |
| `LOCAL_MODEL_STATUS.md` | Execute the specified next experiment: supplied evidence, enumerated options, correct subject/person/time, explicit uncertainty, source selection, and a lexicon comparison on the reserved cohort. Do not recycle its old chart-to-JSON performance as evidence about Kev. |

The Claude review's encoder list needs a scope clarification: Granite and BGE
exist in earlier retrieval experiments; the current population question path uses
MiniLM for candidate routing. Listing an installed encoder does not show it is
the active retriever everywhere. BioClinical relation heads have prior training
receipts, but that does not make them a validated default clinical reader.

Kev's Apache-2.0 release is a practical distribution consideration. Liquid's
LFM-1.0 license needs deployment-specific review, including its commercial revenue
condition. These are licensing differences, not evidence of clinical accuracy.
No employer data or deployment is involved here.

## What was built

`clinical_core/agere/clinical/local_decisions.py` supplies two offline adapters
under one text/choice/probability contract. There is no generated chart JSON,
hosted fallback, or foundation-model training in this milestone.

Kev uses the pinned upstream MLX backbone and LoRA merge with an Agere MLX
pointer head loaded from the actual trained checkpoint. Its two affine projections
and scaled dot product run in fp32 on the CPU stream, while the backbone uses
Metal/BF16. The release temperature is retained. The adapter is merged in fp32
and rounded once to the backbone dtype. A native-head parity check and a
prefix-versus-full-row check run before the experiment. These checks do not
establish equivalence to a separate full fp32 PyTorch backbone run.

d1 uses its pinned official local Python implementation, BF16/MPS backbone and
native answer-slot scoring. That is intentionally its own native readout; using
a generic chat generator for either model would change the experiment.

| Artifact | Immutable revision |
|---|---|
| `jaredpalmer/kev-4b` | `6cfce5c2fa4b4bd64026336ab649c5ca78857d52` |
| Kev base `Qwen/Qwen3.5-4B-Base` | `1001bb4d826a52d1f399e183466143f4da7b741b` |
| Kev author implementation | `5e42a7a03f28134853dd3ff77461457e921e5ec1` |
| `LiquidAI/d1-3B` | `051bcc464b01b9f92942b364d9586b0ef5912432` |

SHA-256 inventories are `models/kev-4b/agere-model.json` and
`models/d1-3b/agere-model.json`. Loading checks bytes, hashes, runtime versions and
the complete model-directory inventory. Checkpoint-owned Python was inspected
before use. The local d1 loader permits its verified custom code, with Hub access
disabled. Kev's audited imported modules are separately hashed.

The installed runtime is recorded rather than inferred from an upstream example:
MLX 0.32.3, mlx-lm 0.32.0, torch 2.14.1, transformers 5.19.0. These are newer than
the cloned Kev project's declared mlx-lm/torch upper bounds. The bounded native
checks and run must therefore be read as results of this exact local runtime,
not a claim that every upstream supported configuration is identical.

## Identical evidence, separate sources of error

`bounded_evidence.py` selects at most eight deduplicated, subject-anchored
sentences with their preceding sentence, dates and exact source offsets. Both
models receive identical serialized evidence and semantic options; native
tokenization and prompt delimiters differ. Questions ask for a documented event
and its supporting excerpt. Missing care is never inferred by the classifier from
a missing excerpt. Contradictory status/support outputs abstain.

The original reserved cohort supplies 20 synthetic patients and 67 applicable
subject/access cases. Applicability still comes from the legacy observed-data
kernel; this is not an evaluation of complete disease discovery. Calibration uses
50 cases from 32 other underlying source families, plus 20 authored synthetic
controls. Family overlap is zero. The controls include negation, relatives,
resolved history, wrong-item denials, hypothetical events and ambiguity.

The reserved cohort was previously used in another experiment and its earlier
report was available to the developer. It is a reused reserved benchmark, not a
new independently blind clinical test. No candidate prompt or threshold is tuned
to its results in this experiment. The frozen plan and file hashes record the
protocol before candidate inference.

The synthetic support oracle lives only in preparation/scoring. Exact matches to
planted generator phrases supply supporting spans independently of the lexicon's
predicted labels. A reference span present in the full chart but absent from the
packet is a retrieval miss. A wrong category when that span is present is an
interpretation error. Unmatched prose and undocumented hidden states are
**unscorable for this attribution**, not negative labels or retrieval failures.
This partial oracle is not clinician adjudication and cannot establish semantic
recall on arbitrary free text.

The downstream benchmark separately compares patient/subject statuses and
outreach lists to simulator truth. It retains the inherited kernel's
“never offered” heuristic for comparability, explicitly as research-only scoring.
It does not authorize care-gap recommendations from absent documentation.

## What the second, Codex report adds

The attachment is titled “Trading systems, financial quants, chart analysis, and
think tanks.” Its received text is preserved in
`CODEX_TRADING_RESEARCH_2026-10-09.md`; the linked technical memos were not included
in the attachment. It is not a second SLM evaluation. Its useful transfers are:

- **Faithful replay and a complete trial record:** the frozen inputs, model/code
  hashes, raw predictions, failed attempts, calibration cutoff and cached replay
  are implemented here. Negative results are retained.
- **Separate delivery and interpretation failures:** this comparison distinguishes
  retrieval failure, unavailable reference evidence, and model interpretation.
  This is only part of the proposed source-completeness work.
- **Source recovery and execution lifecycle contracts:** source sequence gaps,
  reconnect recovery, late acknowledgements and cancellation races remain separate
  engineering requirements. This SLM work does not implement them.
- **Trajectory research and robust scenarios:** prespecified patterns, simple
  comparators, observation-process controls and forward-time validation remain
  research proposals. No financial chart indicator or trading package was installed
  or declared a clinical predictor.

No claims are made that this experiment validates causal outcomes, autonomous
care execution, a novel mathematical model, or commercial superiority.

## Primary implementation references

- [Kev model card](https://huggingface.co/jaredpalmer/kev-4b)
- [Kev source](https://github.com/jaredpalmer/kev)
- [Liquid d1-3B model card and native implementation](https://huggingface.co/LiquidAI/d1-3B)

Local source snapshots and metadata are under `local-decision-sources/`.
