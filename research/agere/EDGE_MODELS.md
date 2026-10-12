# EdgeEvals review and Agere model shortlist

**October 9 implementation update:** Kev's trained pointer head now runs through
a pinned MLX wrapper, and d1-3B has run under the identical bounded evidence
contract. Both are installed research candidates; neither passed the frozen
automatic-acceptance gate. The native BF16 run is distinct from the external GGUF
rows below. See the [reconciliation](LOCAL_DECISION_RECONCILIATION_2026-10-09.md)
and [fresh paired results](../../artifacts/agere-local-decisions-v1/REPORT.md).
The earlier review's installation statements are historical.

Reviewed October 8, 2026. The site was read through the rendered browser after the web retrieval tool could not fetch it. Primary model cards and official documentation were checked separately. This review does not reproduce the benchmark or verify its hidden outputs. All our own patient experiments remain synthetic.

## What the board establishes

The [board](https://edgeevals.ai/system-one/?references=1) reports bounded decisions under supplied options. Its [methodology](https://edgeevals.ai/system-one/methodology/?references=1) describes 669 authored decisions covering triage, notes, criteria and coding. The labels await clinical/coder review. Severe errors can remove credit from an entire group. Ranking uses unadjusted pairwise tests; auto-accept coverage and robustness are separate metrics. Cases, individual answers and runtime details are withheld. Reported local costs omit hardware/power, and API speed excludes time spent waiting through retried service failures.

Our interpretation: suitable for finding candidates, insufficient to establish clinical accuracy or deployment economics. Correlated decisions and multiple comparisons warrant care in interpreting ranks. Confidence thresholds should be recalibrated for our tasks and sources. All model revisions/builds must be recorded locally before comparing to these rows.

## Selected results displayed by EdgeEvals

| Model / measured build | Board clinical-decision score (%) | Median decision time | Provider charge per 1,000 decisions |
|---|---:|---:|---:|
| pplx-decider v1.1 27B / API | 96.1 | 362 ms | $0.025 |
| GPT-6 Luna Decisions / API | 95.2 | 490 ms | $0.090 |
| Jev 1.13 / API | 93.9 | 345 ms | $0.044 |
| Clef 27B / API | 92.8 | 630 ms | $0.217 |
| d1 / API | 91.9 | 358 ms | $0.046 |
| lev / GGUF Q4_K_M | 84.6 | 583 ms | No per-call fee; operating costs excluded |
| JevK5 4B / GGUF Q5_K_M | 83.9 | 351 ms | No per-call fee; operating costs excluded |
| Mapika decider-4b / GGUF Q4_K_M | 83.1 | 284 ms | No per-call fee; operating costs excluded |
| Kev 4B / GGUF Q4_K_M | 83.0 | 311 ms | No per-call fee; operating costs excluded |
| d1-3B / BF16 local | 74.0 | 172 ms | No per-call fee; operating costs excluded |

These are external preview results, not Agere measurements or a current API price quotation. Hosted d1 and local d1-3B are distinct rows; their results are not interchangeable. The ranking has ties; table order is not proof that adjacent models differ. Download size is not required peak RAM. Costs are workload-specific and cannot be applied directly to long charts.

## Candidates and primary-source findings

**Kev 4B — first local adaptation candidate.** [Author model card](https://huggingface.co/jaredpalmer/kev-4b). Qwen3.5-4B-Base with LoRA and a pointer head that scores supplied options. Apache-2.0 is stated for adapter/head and base. The author distinguishes a served long context from a validated 8,192-token context and discloses ordering/date/calibration weaknesses. Its training design is useful for Agere's assertion/status tasks. Require a faithful pointer-head implementation and exact revision before using a quantized build. No local installation or compatibility test was performed here.

**Mapika Decider 4B — a useful contrasting readout.** [Author model card](https://huggingface.co/Mapika/decider-4b). Uses option-letter logits with per-type temperature and structured decision APIs. Apache-2.0 is stated. The documented runtime/version matters; older versions handle temperature differently. The card explicitly says the reviewed v2.1 MPS path was not checked. A generic JSON chat invocation would be the wrong comparison. Candidate for medication-state and rule-support questions, after local runtime validation.

**lev — strong local contender on this board.** [Author model card](https://huggingface.co/interfaze-ai/lev). Qwen3.5-4B plus LoRA, returning typed decisions without prose. Apache-2.0 is stated. The publisher explicitly separates structural output validity from correctness, and its main real-time path expects CUDA. The board's GGUF build needs its own provenance/parity check on this Mac. Candidate for bounded evidence interpretation; do not presume the board's local result describes the publisher's default runtime.

**Liquid d1-3B — architecture diversity.** [Official card](https://huggingface.co/LiquidAI/d1-3B). A typed multimodal model based on LFM2.5-VL-3B. It offers a distinct base architecture and direct decision output. The card lists the LFM license rather than Apache-2.0; deployment terms and custom runtime need review for the intended product. [Liquid's release post](https://huggingface.co/blog/LiquidAI/open-d1) also introduces experimental d1-omni-600M, based on an encoder with additional modalities. That smaller model is a later research candidate, not a proven clinical substitute or part of the displayed board comparison.

**JevK5 — secondary local candidate.** [Author model card](https://huggingface.co/alibiserikbay/JevK5). Independent from TypeSafe Jev, with Qwen3.5-4B, LoRA, option-letter readout and a specialized runtime. The card describes different versioned training and calibration behavior, several-pass handling for larger option sets, and out-of-scope weaknesses. Generic Hub `generate()` examples do not perform its intended decision readout. Avoid equating its name with TypeSafe's model. Audit the release's training provenance before adaptation.

**Perplexity pplx-decider v1.1 27B — higher-capacity comparator.** [Official card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b). Uses a separate readout and changed attention behavior; standard causal inference does not reproduce the evaluated setup. The page still refers to private-repository access and lists no Hugging Face inference provider, while EdgeEvals reports an API run. Those facts do not identify the endpoint used or prove we have equivalent access. Resolve access, revision and serving behavior before integration; do not start by downloading the full large checkpoint to this laptop.

**Cloudflare Clef / Clef-flash — joint question-head comparator.** [Official model card](https://huggingface.co/Cloudflare/clef), [Workers AI documentation](https://developers.cloudflare.com/workers-ai/models/clef/). Clef uses a joint schema head over a pretrained backbone and scores allowed answers without free-form generation. Its interface resembles the existing Jev contract. Candidate when multiple bounded questions share evidence or images; actual shared-input cost and runtime semantics need measurement. A compatible shape does not automatically make our provider adapter interchangeable.

**OpenAI Decisions — hosted comparator.** [Official API guide](https://developers.openai.com/api/docs/guides/decisions). At review, the dedicated beta endpoint supports GPT-6 Luna and typed questions. This is a separate integration from our existing Claude adapter, not permission or evidence of an already configured connection. Compare one bounded task contract across providers; evaluate refusals, evidence sufficiency and calibration instead of accepting every returned probability.

**Together Tev1 — training-recipe reference.** [Official repository](https://github.com/togethercomputer/tev1). It documents a LoRA training recipe using Qwen3.5-4B and constrained answer letters. The authors label the evidence developmental and distinguish model preferences from calibrated confidence. Code licensing does not settle all model/data terms. Useful as a reproducible adaptation pattern; no training job was launched and its advertised training cost is not an Agere budget estimate.

## What this changes for Agere

The ecosystem already contains many Jev-like systems. Agere's research contribution should focus on healthcare evidence semantics, observation noise, changing programs, clinical exceptions and decision-useful population analysis. A leaderboard winner is not automatically a better extractor, reranker, temporal model or causal analyst.

The board's weak nearest-label MiniLM result does not directly evaluate our trained domain head, and neither setup establishes clinical reading skill. Keep MiniLM in low-cost proposal/routing research. Similarly, poor performance of an entity model used as a decision maker does not establish poor entity extraction; use models on their intended tasks.

Use a small roster: one local decision candidate, existing Jev, one alternative hosted decision service, and the existing frontier comparator. Before adoption require exact runtime parity, matched evidence, source/family separation, fresh language, contradiction/unknown cases, citation verification, service-failure accounting and end-to-end cost. This is a model-selection plan, not another claim that test scenarios constitute training.
