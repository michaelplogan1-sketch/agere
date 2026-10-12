# From research to a trainable healthcare intelligence stack

Research and implementation record, October 8, 2026. All patient records, chart experiments and clinical examples here are synthetic. The cited research is real; its results do not establish performance of our platform. No employer ontology or TextQL installation was accessed. This is a scoped technical review, not an exhaustive review of physics or all Hugging Face models.

## What changed in response to the request

The earlier VBC research produced question specifications and evaluation scenarios. That was useful for defining what the system ought to answer, but it did not teach a model anything. This iteration exports training targets, fits local model weights, adds callable components and measures the consequences. Evaluation remains separate because a trained model can become cheaper while losing the evidence that matters.

Three artifacts now contain learned weights: a chart-passage relevance classifier, a lexical research-domain router, and a semantic research-domain router. The latter uses a frozen MiniLM encoder with a newly trained classifier head. Neither a frontier model, MedGemma, Jev, nor the MiniLM encoder itself was fine-tuned. The full analysis-plan targets are prepared for later supervised fine-tuning; the routers currently predict a domain and retrieve a template.

Start with [the implementation and training record](../../TRAINING_BUILD_REPORT.md), [source-by-source review notes](SOURCES.md), and [the mathematical design](MATHEMATICAL_DESIGN.md). These distinguish installed components from proposed experiments.

## The product opportunity

Our strongest research direction is a system that learns which evidence is necessary for a particular healthcare decision, reconciles contradictory observations, checks the resulting calculation, and spends more compute only when it could change the answer. A larger collection of models is useful only if its members have different, measurable jobs.

Consider a synthetic patient who seems to lack an HFrEF medication. A claim may be absent because it is late; a note may document an outside prescription; a copied medication list may be stale; a clinician may have recorded an appropriate hold. The useful output is an evidence-backed state with unresolved contradictions and a next information need. A plausible paragraph about a care gap is insufficient.

The proposed stack is:

1. An immutable source layer with patient identity, source provenance, units, event time, arrival time and version history.
2. Deterministic normalization and typed observations, preserving the original text and distinguishing missing, negative, unknown and contradicted values.
3. Cheap learned extraction and retrieval over changed records, with explicit evidence spans and an uncertainty channel.
4. A patient-state and observation-quality model that reconciles sources without treating documentation frequency as disease severity.
5. Versioned clinical and contract rules executed by code; models propose mappings and explanations.
6. Selective Jev, local model and frontier calls, chosen by demonstrated capability and expected value of additional information.
7. Population analyses that estimate actionable opportunity, uncertainty and operational constraints, followed by outcome measurement.

Only parts of this architecture exist today. The current local runtime already has source adapters, a reader/judge/discovery bridge, typed hosted adapters and a corpus. This iteration adds retrieval, proposal routing, cost accounting and a mathematical time-series primitive. The joint patient-state model, automatic program compiler and calibrated escalation policy remain to build.

## Tokenization and the economics that matter

Token count is model-specific. Tokenization includes normalization, segmentation and token IDs; swapping a tokenizer under an existing model does not preserve its learned representations. A new healthcare vocabulary would require an adaptation and evaluation project. Preserve numbers, units, negation, relationships and temporal qualifiers before trying clever abbreviations. See [Hugging Face's tokenizer architecture](https://huggingface.co/blog/tokenizers).

The new local audit compares pretty and compact JSON containing the same synthetic evidence and verifies a semantic round trip. The exact counts and tokenizer hash are in [tokenization_audit.json](../../artifacts/research-stack-demo/tokenization_audit.json). This measures formatting overhead only. It is not evidence that a compressed clinical summary preserves a decision.

Provider prices need to be counted in the provider's tokens. At review time, the [Claude pricing table](https://platform.claude.com/docs/en/about-claude/pricing) lists Opus 5.5 input/output at $4/$20 per million tokens, five-minute cache writes at $5 and cache hits at $0.20. It also warns that newer tokenization can increase token counts for the same text. Prices, eligibility, residency and cache duration must be versioned. [Jev's model documentation](https://docs.typesafe.ai/models) lists Jev 1.13 input at $0.042 per million, with free output tokens. Cheap typed inference still needs clinical validation.

For disjoint token categories, our cost module implements:

`cost = (uncached_input × input_rate + cache_writes × write_rate + cache_hits × hit_rate + output × output_rate) / 1,000,000 + local_compute_cost + review_cost`.

The saved [scenario](../../artifacts/research-stack-demo/cost_results.json) uses explicitly assumed provider-token volumes. It is neither an invoice nor a conversion of local Qwen token counts into Claude charges. The formula excludes any unprovided tool charges, tax or operating expense.

The appropriate product metric is **total cost per verified useful answer**, accompanied by coverage, error severity, latency and abstention. Optimizing dollars per request alone rewards incomplete answers. Human review, failed requests, reprocessing, idle servers, annotation and data integration belong in the economics.

### The cache hierarchy we should build

| Layer | Reuse unit | Invalidation requirement | Status |
|---|---|---|---|
| Source normalization | Exact source content and parser version | Source correction, parser or terminology update | Existing source hashes; finer reuse proposed |
| Embeddings | Normalized passage, encoder/tokenizer revision | Text, normalization, encoder change | Proposed |
| Extracted evidence | Passage plus context, task, reader version | Context, schema, model or task change | Proposed |
| Patient state | Evidence dependencies and as-of time | New/corrected evidence, arrivals, time-sensitive rules | Proposed |
| Cohort/rule results | Typed inputs, rule version, contract, time | Any dependency changes | Existing whole-run versioning; incremental DAG proposed |
| Hosted response/prefix | Exact request or supported shared prefix | Model, prompt, schema, source, tenant or TTL | Existing response-cache option; provider-prefix integration proposed |

Semantic similarity alone must never reuse one patient's answer for another. Separate tenants, authorization scopes and patients. A copied sentence can share an embedding while its patient-specific clinical interpretation remains separate. Reuse stable ontology and schema prefixes where supported; keep volatile evidence later in the prompt. Cache expiry can make an apparently attractive write cost uneconomic.

For identical eligible prompts within the cache lifetime, break-even requires `n ≥ (write_rate − hit_rate) / (input_rate − hit_rate)`, rounded up. The module handles cases where hits never repay an expensive write. More important than this algebra is avoiding unnecessary inference: recompute the affected dependencies when a new lab arrives, rather than rereading every chart and recalculating every measure.

### Serving, compression and model adaptation

| Method | What transfers | Important limit |
|---|---|---|
| [FlashAttention](https://arxiv.org/abs/2205.14135) | Reduce memory traffic for exact attention | Hardware/kernel compatibility matters; not a claim that full attention has linear arithmetic cost |
| [PagedAttention](https://arxiv.org/abs/2309.06180) | Reduce fragmented KV memory and improve batching | Primarily a serving problem; not an automatic reduction in hosted per-token prices |
| [KV cache strategies](https://huggingface.co/docs/transformers/kv_cache) | Trade memory against latency and throughput | Offloading can slow generation; measure on this laptop separately |
| [Matryoshka embeddings](https://sbert.net/examples/sentence_transformer/training/matryoshka/README.html) | Smaller retrieval vectors and coarse-to-fine search | Truncating an embedding does not itself avoid the encoder forward pass |
| [SetFit distillation](https://huggingface.co/docs/setfit/how_to/knowledge_distillation) | Transfer expensive teacher judgments into a small classifier | Teacher errors transfer too; disagreement and unknown labels must survive |
| [LoRA](https://huggingface.co/docs/peft/v0.21.0/package_reference/lora) | Train a limited parameter update instead of all base weights | Does not eliminate base-model inference or the need for correct supervision |

The highest-priority next adaptation is an encoder trained on clinical evidence relationships: current versus historical, patient versus relative, supported versus contradicted, and relevant exception versus unrelated text. A teacher ensemble can propose labels; adjudication and evidence checks determine which become training targets. Do not train on an LLM's confidence as if it were truth.

## Hugging Face: which models have a job here?

The downloaded [MiniLM model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) is a compact general sentence encoder. Its fixed revision and file hashes are recorded locally. It now supports the trained question router; its short context makes it inappropriate for blindly encoding entire charts. Inputs exceeding the configured context are rejected by the router.

[ModernBERT](https://huggingface.co/blog/modernbert) is a candidate for longer text extraction and reranking. It should compete against a smaller encoder on the same evidence task and target hardware. Bigger context alone does not establish clinical understanding. [MedGemma's model card](https://huggingface.co/google/medgemma-1.5-4b-it) describes medical capabilities and adaptation limits: it is a candidate reader/teacher, not a prevalidated answer engine for this product. It was not downloaded or trained in this iteration.

Evaluate encoders, cross-encoders, typed classifiers, temporal models and frontier models on their assigned tasks. A cross-encoder can examine a question and candidate evidence jointly after cheap retrieval. It must see exceptions and conflicting passages, not only the top positive match. The [Lost in the Middle study](https://arxiv.org/abs/2307.03172) motivates checking evidence placement; its older model results do not establish how every current model behaves.

Jev's [official introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev) supports a useful design principle: specialize inference around constrained tasks. We do not have its proprietary training recipe, and typed output does not guarantee that the selected clinical label is correct. Our new local heads are conventional supervised models, not a recreation of Jev.

## Google DeepMind: borrow the mechanism, then prove the transfer

[AlphaGeometry](https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/) combines learned proposals with symbolic deduction. For us, a model could propose a cohort predicate, measure mapping or temporal relation; code could verify schema, units, dates and denominator logic. A proof checks consequences of premises. It does not make an uncertain chart premise true.

[AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) and its [technical report](https://arxiv.org/abs/2506.13131) suggest a bounded search over executable algorithms with independent evaluators. Candidate targets include retrieval policies, dependency invalidation and query plans. Generate candidates in a sandbox, prohibit test-set inspection, and compare cost under a fixed quality floor. The [later impact report](https://deepmind.google/blog/alphaevolve-impact/) illustrates transfer across domains; its reported gains are not measurements of our stack.

[GraphCast](https://deepmind.google/blog/graphcast-ai-model-for-faster-and-more-accurate-global-weather-forecasting/) suggests learning at multiple scales and separating observations from latent state. Healthcare adds selection effects, incentives and changing documentation that weather grids do not share. A graph should connect observations, patients, episodes, clinicians and contracts with typed edges; message passing must not leak future information or turn association into a causal intervention.

The older [GenCast abstract](https://deepmind.google/research/publications/gencast-learning-skillful-ensemble-forecasting-of-medium-range-weather/) contains an unfinished results placeholder. It is logged as an incomplete source for performance claims. Ensemble forecasting remains a research lead; no claim of healthcare benefit depends on that draft.

## Astrophysics: infer the signal and the observation process

Astronomy offers concrete tools for irregular, noisy data. [Bayesian Blocks](https://arxiv.org/abs/1207.5578) and the [Astropy implementation documentation](https://docs.astropy.org/en/stable/api/astropy.stats.bayesian_blocks.html) motivate our new Gaussian change-point primitive. It weights measurements by supplied uncertainty and penalizes unnecessary segments. It currently operates on supplied scalar measurements; it does not extract a creatinine series, select a clinical threshold, or issue a patient alert.

Next, test whether a measurement pattern remains after accounting for assay, source, visit intensity and care setting. A sharp jump may be a new lab interface or a unit error. Hospitalized patients are measured more frequently; sampling times can carry information. Treating that process as random can produce misleading risk signals.

[Lomb–Scargle methods](https://docs.astropy.org/en/stable/timeseries/lombscargle.html) can examine periodic structure in irregular observations. The first application should be detecting scheduling, refill or feed artifacts. A periodic signal may reflect the collection process. False-alarm probabilities are conditional on the noise model and sampling assumptions, not a posterior probability of disease.

[Simulation-based inference](https://arxiv.org/abs/1911.01429) offers a way to test recovery of latent causes from a specified generator. Our synthetic worlds can vary missingness, claims delay, duplication and source disagreement. Success would prove recovery under those generator assumptions, not that real clinical causes have been identified.

## Quantum concepts: a narrow, falsifiable opportunity

[Tensor-network classifiers](https://arxiv.org/html/1605.05775v2) approximate high-order interactions with smaller linked tensors. A classical tensor-train model could be compared with boosted trees and low-rank linear models for sparse combinations of disease, medication, time and source quality. Feature ordering and rank constrain what it can express. This is a proposed experiment, not an installed quantum component.

The [power-of-data study](https://www.nature.com/articles/s41467-021-22539-9) shows why strong classical comparisons matter when evaluating quantum advantage. [Barren plateaus](https://arxiv.org/abs/1803.11173) identify training difficulties for classes of parameterized quantum circuits. Neither supports paying for quantum hardware for this platform now. There is no demonstrated quantum or tensor-network advantage on our data.

## The research hypothesis worth pursuing

Learn **decision-preserving evidence selection with adaptive information acquisition**. Its loss should penalize missed treatment exceptions, wrong temporal relationships, unsupported assertions and wasted compute. When sources disagree, the system should identify which additional datum could change the decision: an outside medication fill, a repeat measurement, an updated contract rule or a clinician clarification.

This draws on the [information bottleneck](https://arxiv.org/html/physics/0004057): compress with respect to a defined target, rather than maximizing textual brevity. Our present selector is a weakly supervised heuristic, not an information-bottleneck optimizer. It lost relevant evidence on the difficult world; that prevents promotion to an automatic clinical filter.

The potentially distinctive contribution is how evidence, observation quality, rule verification, information acquisition and cost are trained together for VBC. The individual techniques already exist. Novelty, defensibility and superiority require a prior-art search and comparative results. We have neither proven a new invention nor beaten commercial products.

## What should be trained next

1. Replace single-teacher relevance with reviewed evidence labels that distinguish positive, negative, exception, conflict, historical and unknown states. Preserve patient-family partitions and create genuinely fresh language/source evaluations.
2. Train the encoder/reranker on those relationships. Train on the development split, tune on calibration only, and preserve full-chart fallback until omission risk is acceptable.
3. Add typed longitudinal lab extraction with unit and source checks; then fit the observation model and time-series method using known synthetic mechanisms.
4. Train the question planner on verified executable plans and failed-plan corrections. Expand beyond single-label domains: many VBC questions require several operators and domains together.
5. Learn escalation only after measuring error and cost for each model/task combination. Consider [conformal uncertainty sets](https://arxiv.org/abs/2107.07511), with explicit exchangeability and drift checks; scores are not clinical confidence by default.
6. Train action prioritization only when there is an outcome and intervention label worth learning. Hospital/SNF comparisons require case mix, selection and uncertainty; observational correlations alone do not identify who would benefit from a different placement.

The engineering priority is reliable evidence reuse and specialized supervision. The exploratory priority is the joint model of patient state, data-observation quality and the value of one more question. Both aim to turn dirty data into useful insight at a measured cost.
