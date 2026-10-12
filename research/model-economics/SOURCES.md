# Source-by-source research record

October 8, 2026. These are source pages, not a claim of fully reading every paper, website or model repository. Scope is explicit. Vendor reports are identified as such; transfer to our synthetic healthcare setting is our hypothesis. Abstract-only items require deeper study before implementing their methods. Original summaries are not a license to train on the source text.

| ID / source | Actual scope | Proposed use or finding |
|---|---|---|
| TOK01 [Tokenizer architecture](https://huggingface.co/blog/tokenizers) | Pipeline and v5 architecture sections (reviewed_sections) | Tokenizer choice is coupled to model representations; preserve clinical semantics when changing serialization. |
| TOK02 [KV cache strategies](https://huggingface.co/docs/transformers/kv_cache) | Cache strategy and offloading sections (reviewed_sections) | Memory savings can trade off against throughput; target hardware matters. |
| TOK03 [FlashAttention](https://arxiv.org/abs/2205.14135) | Abstract (abstract_only) | Exact attention with reduced memory traffic; no measured gain on our laptop. |
| TOK04 [PagedAttention](https://arxiv.org/abs/2309.06180) | Abstract (abstract_only) | KV memory paging improves serving utilization; not an automatic API-price discount. |
| TOK05 [Lost in the Middle](https://arxiv.org/abs/2307.03172) | Abstract (abstract_only) | Evidence position is an evaluation dimension; results concern the paper’s model setting. |
| TOK06 [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) | Pricing table, tokenizer caveat, cache and batch sections (reviewed_sections) | Use provider-specific tokens and disjoint cost categories; prices are time-sensitive. |
| TOK07 [Jev model documentation](https://docs.typesafe.ai/models) | Model aliases, pricing and limits (reviewed_sections) | Pin model revision and count actual input; typed labels still need semantic validation. |
| HF01 [SetFit distillation](https://huggingface.co/docs/setfit/how_to/knowledge_distillation) | Teacher/student tutorial (reviewed_sections) | Distill task supervision with careful teacher-label quality controls. |
| HF02 [SetFit overview](https://huggingface.co/docs/setfit/main/index) | Overview (reviewed_sections) | Small sentence models can support supervised task adaptation without generative prompts. |
| HF03 [ModernBERT](https://huggingface.co/blog/modernbert) | Architecture, context and usage sections (reviewed_sections) | Candidate long-context encoder; compare accuracy and measured hardware cost. |
| HF04 [Matryoshka embeddings](https://sbert.net/examples/sentence_transformer/training/matryoshka/README.html) | Method and usage sections (reviewed_sections) | Nested vectors can reduce retrieval storage; truncation alone does not save encoding. |
| HF05 [PEFT LoRA](https://huggingface.co/docs/peft/v0.21.0/package_reference/lora) | Introduction, low-rank update, usage and QLoRA sections (reviewed_sections) | Adapt limited parameters while retaining the pretrained base computation. |
| HF06 [MedGemma 1.5 4B](https://huggingface.co/google/medgemma-1.5-4b-it) | Model card use and limitations (reviewed_sections) | Medical starting point requiring adaptation and evaluation; not installed here. |
| HF07 [MiniLM L6 v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Model card, usage, local README/configuration (reviewed_sections) | Frozen sentence encoder now used with a locally trained domain head. |
| JEV01 [Jev introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev) | Official architecture and task framing (reviewed_sections) | Specialization is useful; proprietary details and company claims are not independently reproduced. |
| DM01 [AlphaGeometry](https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/) | Architecture, synthetic training and reasoning sections (reviewed_sections) | Learned proposals plus symbolic verification; uncertain clinical premises remain uncertain. |
| DM02 [AlphaEvolve introduction](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) | Generation, evaluation and algorithm search sections (reviewed_sections) | Search executable candidates using separate evaluators. |
| DM03 [AlphaEvolve technical report](https://arxiv.org/abs/2506.13131) | Abstract (abstract_only) | Automated algorithm discovery is an experimental pattern, not a healthcare result. |
| DM04 [AlphaEvolve impact report](https://deepmind.google/blog/alphaevolve-impact/) | Applications and algorithm-improvement sections (reviewed_sections) | Cross-domain transfers are company-reported examples, not our measured gains. |
| DM05 [GraphCast](https://deepmind.google/blog/graphcast-ai-model-for-faster-and-more-accurate-global-weather-forecasting/) | Architecture, reanalysis training and forecasting sections (reviewed_sections) | Multiscale graph structure inspires typed population/patient/source representations. |
| DM06 [GraphCast research abstract](https://deepmind.google/research/publications/22598/) | Abstract (abstract_only) | Forecasting success does not imply healthcare transfer. |
| DM07 [GenCast early abstract](https://deepmind.google/research/publications/gencast-learning-skillful-ensemble-forecasting-of-medium-range-weather/) | Abstract contains unfinished results placeholder (incomplete_source) | Do not rely on this version for quantitative performance claims. |
| AST01 [Bayesian Blocks](https://arxiv.org/abs/1207.5578) | Abstract (abstract_only) | Adaptive segmentation for observational series; clinical assumptions must be supplied separately. |
| AST02 [Astropy Bayesian Blocks](https://docs.astropy.org/en/stable/api/astropy.stats.bayesian_blocks.html) | Measurement fitness, prior parameters and examples (reviewed_sections) | Choose observation model and penalty explicitly. |
| AST03 [Astropy PointMeasures source](https://docs.astropy.org/en/stable/_modules/astropy/stats/bayesian_blocks.html) | PointMeasures validation and Gaussian fitness (reviewed_sections) | Reviewed Gaussian fitness formula; our implementation written independently. |
| AST04 [Lomb–Scargle guide](https://docs.astropy.org/en/stable/timeseries/lombscargle.html) | Irregular observations and false-alarm sections (reviewed_sections) | Periodic signals can reflect sampling; noise-conditional significance is not disease probability. |
| AST05 [Lomb–Scargle API](https://docs.astropy.org/en/stable/api/astropy.timeseries.LombScargle.html) | Parameters and false-alarm methods (reviewed_sections) | Measurement uncertainties and null assumptions matter. |
| AST06 [GWOSC tutorials](https://gwosc.org/tutorials/) | Tutorial directory only (directory_only) | Matched filtering is a lead; this pass did not complete its technical tutorial review. |
| MATH01 [Simulation-based inference](https://arxiv.org/abs/1911.01429) | Abstract (abstract_only) | Known simulators support controlled latent-state recovery experiments. |
| MATH02 [Information bottleneck](https://arxiv.org/html/physics/0004057) | Introduction and sections 2–3, including objective and self-consistent equations (reviewed_sections) | Compression needs a defined relevance target; present selector is not an IB optimizer. |
| MATH03 [Conformal prediction introduction](https://arxiv.org/abs/2107.07511) | Abstract (abstract_only) | Uncertainty sets are a research candidate; exchangeability and shift require explicit treatment. |
| Q01 [Quantum-inspired tensor networks](https://arxiv.org/html/1605.05775v2) | Sections I–V: feature map, labels, MPS and sweeping objective (reviewed_sections) | Classical low-rank tensor interactions are testable against ordinary models. |
| Q02 [Power of data in quantum ML](https://www.nature.com/articles/s41467-021-22539-9) | Abstract, setup, bounds and discussion (reviewed_sections) | Strong classical comparisons are necessary before asserting quantum advantage. |
| Q03 [Barren plateaus](https://arxiv.org/abs/1803.11173) | Abstract (abstract_only) | Some parameterized quantum circuit classes face severe gradient-training difficulties. |
| SITE01 [Frontier Labs](https://frontierlabs.co.uk/) | Homepage opened; prior engineering review carries earlier notes (lead_only) | This pass did not verify a new technical method from the homepage. |

## Deliberate limits

No comprehensive patent/prior-art review was performed. No quantum experiment or healthcare foundation-model pretraining was performed. No paper metric is represented as a result of our code. Model-card and vendor performance claims require independent task-specific checks. The GenCast draft, GWOSC directory and Frontier Labs homepage do not support an implemented-method or performance claim.

The machine-readable companion is [source_catalog.json](source_catalog.json).
