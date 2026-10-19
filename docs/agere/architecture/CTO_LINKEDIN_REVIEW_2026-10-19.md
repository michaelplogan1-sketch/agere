# CTO review of the LinkedIn stack post

October 19, 2026. Scope: final illustration, public copy, source-linked component roles, learning/bottling code and existing validated evidence. This is a communications and architecture critique, not a full code, statistical or clinical audit. Nothing has been published.

## Recommendation

Reasonable to share as an AI-assisted prototype and synthetic-data architecture experiment, using the revised copy. The design has intelligible roles: preserve meaning, select evidence, interpret bounded tasks, execute calculations, reuse work and escalate. Those roles give the author a concrete explanation for the architecture.

The central unresolved question is whether the added components improve enough real workflows to justify their integration, evaluation and operating cost. The current evidence does not answer that for the whole stack. A confident presentation should acknowledge that question rather than claim novelty or general superiority. Being transparent about AI-assisted development is compatible with having designed and directed the experiment.

## Questions an engineer could reasonably ask

| Likely critique | Assessment from current evidence | Defensible response |
|---|---|---|
| “Why so many models? Is this overengineered?” | Substantive. Component roles differ, but necessity has not been demonstrated by matched ablations. | “They serve different tasks, and each query uses selected paths. Simplifying the stack and measuring each layer's contribution remain important.” |
| “Why MiniLM, Granite and UltraBERT—and two decision providers?” | Some apparent duplication has an explanation: plan matching, passage retrieval, catalog/terminology matching. Jev and Perplexity genuinely overlap as alternative typed readers; they are not mandatory serial stages. | Explain the different objects being matched. Do not claim that more embeddings or more model votes automatically improve accuracy. |
| “Where are the fair baselines?” | Largest claim risk. The independently reconciled public demonstration has nine selected questions and an Opus baseline. It does not establish general accuracy superiority or performance against Haiku/Sonnet. | “This post explains an architecture experiment. Broader matched evaluation is still needed.” No public superiority numbers remain. |
| “Are you just shifting costs from API tokens to your laptop and engineering time?” | Substantive. Local inference, indexing, training, maintenance and evaluation consume resources. Three Opus analysts also increase fallback cost. | “The target is total cost per correct answer, including those costs. No measured overall saving is claimed here.” Bottling's training cost needs enough suitable future workload to amortize. |
| “Does parser-checked mean correct?” | No. Well-formed slots or executable SQL can still encode the wrong patient, date, concept or denominator. Multiple candidates can share an error. | Types and checks constrain failures; they are not truth oracles. Domain definitions, reference validation and end-to-end evaluation remain necessary. |
| “Is this actually self-training?” | Not autonomously end to end. Runtime captures accepted work/corrections; a separate loop prepares datasets/recommendations. Training and adapter activation are separate. | Use “controlled learning loop.” The current narrow Qwen adapter uses synthetic templates and Haiku paraphrases with parser checks. It still performs fresh local LLM inference. Broad SQL LoRA remains default-off. |
| “What does 29 engines prove?” | Inventory, not 29 independently qualified capabilities. Causal, survival, filtering and queueing methods have different prerequisites and assumptions. | Discuss a method's job and assumptions. Public post no longer uses engine count as a selling point; the image retains the documented inventory count. |
| “Does synthetic healthcare data establish clinical readiness?” | No. Synthetic data supports development and controlled checks, not real-world clinical qualification or robustness to every source. | Call it a synthetic-data prototype. Do not claim clinical validation or deployment readiness. |
| “What is new here?” | Routing, retrieval, reranking, compilation, caching, distillation and statistical engines are established techniques. Their composition is an engineering hypothesis, not proof of a unique invention. | “I explored how these methods fit together for disparate healthcare data.” Credit the model/tool/research teams. Avoid “first,” “best,” “most unique” and “unmatched.” |
| “Can you explain one question end to end?” | The poster is an overview, not a literal execution trace. Its density makes a concrete example important. | Explain one simple path: define population/date → obtain relevant records → compile or interpret missing slots → execute SQL → check scope and return source-linked results. Label a hypothetical walkthrough as illustrative. |

## Changes made before recommending sharing

- Preserved the user's approved illustration, with only the bottling and economics text corrected.
- Kept the full-stack story central; bottling is one mechanism among retrieval, computation, bounded decisions and reuse.
- Added Claude Code/Codex development attribution and “prototype”/synthetic-data context to the public copy.
- Made selective paths explicit. Kept Jev/Perplexity as alternatives and BioClinical features as research.
- Kept the broad SQL adapter default-off and new training/activation separate.
- Removed the public Opus-only price/score comparison; no replacement savings numbers were invented.
- Added an explicit sentence that broader accuracy and cross-model cost advantages still need validation.

## Language to avoid

“Most accurate,” “best token economics,” “zero-cost AI,” “hallucination-free,” “learns automatically from every answer,” “29 validated engines,” and “proves superiority over ChatGPT/other platforms.” None is established by the evidence reviewed here. Schema safety is different from factual correctness, including for typed decision models.

An AI-generated illustration is acceptable as a conceptual visual. Its machinery is metaphorical; it should not be used as deployment documentation. Tiny labels may require opening the full-resolution image. This is a readability tradeoff, not a technical defect; the user explicitly prefers keeping this design.

## Evidence and attribution checked

- Current public copy: `linkedin-post-public.txt`; current image: `linkedin-stack-final.png`.
- Implementation map: `FULL_STACK_EXPLAINED.md`; registry: `component-catalog-2026-10-19.json`.
- Evidence limitations: `docs/agere/study/PROOF_STATUS.md` and `artifacts/study/validated-proof-2026-10-19/evidence.json`.
- Learning/bottling: `clinical_core/agere/clinical/learning.py`, `clinical_core/agere/clinical/bottled.py`, `tools/learning_loop/loop.py`, `tools/bottling/narrow_data.py`, `tools/bottling/lora-narrow.yaml`.
- [TypeSafe's Jev introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev): typed decision interface; its vendor performance claims are not used as our own results.
- [Perplexity Decisions documentation](https://docs.perplexity.ai/docs/decisions/quickstart): bounded decision API.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): established routing/workflow patterns and the need to justify complexity.
- [Bottling research](https://arxiv.org/abs/2610.08775): task-specific artifacts and amortized cost; not a guarantee of retained quality or savings in our workload.
- [MLX LM](https://github.com/ml-explore/mlx-lm): local generation and LoRA fine-tuning.
- Anthropic's official [Haiku 5.5](https://www.anthropic.com/claude-haiku-5-5), [Sonnet 5.5](https://www.anthropic.com/claude-sonnet-5-5), and [Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview) pages confirm those public model names as of this review. Their prices and benchmark claims were not transferred to this experiment.
