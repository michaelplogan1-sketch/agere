# LinkedIn draft: insight from disparate data

Status: prepared for Mike to review and publish. Public copy has no project/company name or employer reference; model/provider names remain. Nothing has been posted or sent.

## Post

Data → Information → Intelligence → Wisdom

I wanted an analytics system that could answer a question—and preserve useful work for the next one.

So I built a stack over disparate synthetic healthcare data around two ideas: choose the right method for each question, and “bottle” repeatable tasks into reusable local capability.

Here’s why the pieces are there:

• Establish meaning. PDFKit + Apple Vision OCR extract documents. Canonical records, semantic SQL and code mappings align patients, dates and denominators. UltraBERT-Embed-150M helps match terminology when exact mappings fall short.

• Select evidence. BM25 + IBM Granite Embedding English R2 combine exact and semantic retrieval. Reciprocal-rank fusion, BGE reranker v2-m3 and a schema reranker prioritize relevant passages, tables and fields. Better context, less noise.

• Make bounded decisions. TypeSafe’s Jev and Perplexity Decisions are alternative readers for subject, assertion and time: who does a statement concern, is it negated, and is it current? Rules and TF-IDF/logistic heads handle recurring cases; BioClinical ModernBERT supports research features.

• Compile and reuse. MiniLM supports plan matching. A compiler, typed contracts and SQL families turn supported questions into executable plans. Numbers come from computation.

• Bottle recurring work. Qwen3-4B + narrow LoRA, trained and run locally with Apple MLX, translates unfamiliar phrasing into typed slots that the parser checks. The controlled learning loop captures accepted plans and corrections, prepares training candidates and identifies retraining opportunities. Training, evaluation and activation remain separate steps. The broad SQL adapter is still default-off.

• Escalate deliberately. Perplexity selects curated recipes; Claude Haiku handles bounded slots. Opus handles unfamiliar questions through analyst proposals and system-executed SQL; Sonnet is a configured alternate. Each question follows the path it needs.

• Use the right mathematics. The 29 analytical engines include Kaplan–Meier/Cox, propensity methods, empirical Bayes and queueing. Indexes, memoization, content hashes, dependency invalidation, provenance and usage ledgers make work reusable and auditable.

Token economics has two levers: the price of inference and how much inference a workflow needs.

In nine synthetic-data questions with independently reconciled references:
• This stack: $0.00 model API charges; 9/9 scorer passes.
• Opus text-to-SQL baseline: $1.89; 7/9 passes.

That comparison tested Opus, not Haiku or Sonnet. It’s a small experiment, not a general benchmark. Patient-list passes used F1 ≥ 0.98. Compute/build costs were excluded; not every pictured component ran. The learning loop’s additional savings have not been measured.

The goal: turn useful reasoning into reusable capability, and measure cost per correct answer.

#DataEngineering #HealthcareAI #TokenEconomics

## Optional credit line for the post

Built with contributions from TypeSafe/Jev, Perplexity, Anthropic, Qwen, IBM Granite, Apple MLX and the Hugging Face ecosystem, with UltraBERT from DMIS Lab and BGE from BAAI.

Concept credit: the “Agent in a Bottle” research by Ankit Sonthalia and colleagues informed the bottling approach: https://arxiv.org/abs/2610.08775. This is attribution, not a claim that its benchmark results transfer to this stack.

Select each real company from LinkedIn’s @mention menu; these plain-text placeholders are not live mentions.

## Mention recommendations

Name the actual components in the post and use the longer credit line in a comment if desired. Apple MLX is a relevant addition because the local Qwen adapter uses MLX LM for training and inference. These are implementation credits, not endorsements or partnerships. Do not add an organization solely for recognition.

| Organization | Connection to this architecture | Verified page |
|---|---|---|
| TypeSafe AI | Jev typed evidence decisions | https://www.linkedin.com/company/typesafe-ai/ |
| Perplexity | Decisions API / typed decisions and recipe selection | https://www.linkedin.com/company/perplexity-ai |
| Anthropic | Claude analysis, bounded helper and composition roles | https://www.linkedin.com/company/anthropicresearch |
| Hugging Face | Open-model distribution and tooling ecosystem; not the creator of every hosted model | https://www.linkedin.com/company/huggingface/ |
| IBM | Granite embeddings | https://www.linkedin.com/company/ibm/ |
| Alibaba Cloud / Qwen team | Qwen3-4B | https://www.linkedin.com/company/alibaba-cloud-computing-company |
| Apple | MLX local training/inference, PDFKit and Vision OCR | https://developer.apple.com/videos/play/wwdc2025/315/ |
| DMIS Lab | UltraBERT terminology/catalog embeddings | https://huggingface.co/dmis-lab/UltraBERT-Embed-150M |
| BAAI | BGE reranker | https://huggingface.co/BAAI/bge-reranker-v2-m3 |

UltraBERT should be credited to DMIS Lab, not Hugging Face as its creator. Model card: https://huggingface.co/dmis-lab/UltraBERT-Embed-150M

## Source checks

- TypeSafe: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Perplexity: https://docs.perplexity.ai/docs/decisions/quickstart
- IBM: https://huggingface.co/ibm-granite/granite-embedding-english-r2
- Qwen: https://huggingface.co/Qwen/Qwen3-4B
- Apple MLX: https://github.com/ml-explore/mlx and https://github.com/ml-explore/mlx-lm
- BGE: https://huggingface.co/BAAI/bge-reranker-v2-m3
- Bottling concept: https://arxiv.org/abs/2610.08775
- Implementation roles/status: component-catalog-2026-10-19.json and source-linked FULL_STACK_EXPLAINED.md.

## Image

Current lead image: [stack cutaway with bottling and controlled learning](linkedin-stack-bottling.png). It replaces the dense neon/card-grid overview with a light isometric machine drawing: shared data/meaning foundation, evidence selection, three selectable execution paths, computation and sourced outputs. Named tools/model roles appear on adjacent callouts. Classic CS mechanisms form the physical base and the valid-work reuse loop is visible. The headline remains Data → Information → Intelligence → Wisdom; observed experiment API economics remain at the bottom. The comparison names the tested Opus text-to-SQL baseline and explicitly says Haiku/Sonnet were not compared. “Saved pilot” is removed. The local Qwen callout includes Apple MLX, and the return loop distinguishes reuse from separate training/evaluation.

The cutaway is a conceptual architecture illustration, not a literal deployment/network topology or a claim that every displayed module runs on every question. Broad Qwen SQL remains default-off and BioClinical features remain research. All 29 engine names remain available in the [supporting detail page](linkedin-stack-02-methods.png); the hero groups them into four families.

[Exact edit prompt](linkedin-stack-bottling-prompt.txt). Input: [previous cutaway](linkedin-stack-cutaway.png). Generated with the built-in image tool, without a paid external image service or CLI/API fallback. Visually inspected model labels, branch distinctions, status notes, controlled-learning labels and experiment figures. Nothing has been published. Prior PNGs and the [earlier two-page PDF](../../../output/pdf/data-information-intelligence-wisdom-linkedin.pdf) are retained for reference; that PDF has not been updated with this new hero.

### Visual references reviewed

- [Vercel: Compute that takes any shape](https://vercel.com/blog/fluid-compute-takes-any-shape): inspected the expanded Fluid diagram. Clear layer grouping and separation of interface, execution and underlying components.
- [Stripe: Ledger](https://stripe.com/blog/ledger-stripe-system-for-tracking-and-validating-money-movement): inspected the expanded data-quality funnel. One dominant visual form and a restrained palette make the central relationship readable.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): reviewed the routing description to inform distinct, selected branches.

The final illustration is original; no source artwork, logos or vendor performance numbers were copied. The interpretation and art direction are editorial choices, not claims that these companies recommend this particular architecture.

### Evidence behind the image's comparison

The validated subset of the saved S1-lean run gives the specialist-first stack 9/9 scorer passes and $0.00 incremental model API charges, versus 7/9 and $1.8886696 (displayed as $1.89) for Claude Opus 5.5 text-to-SQL on every question. API dollars per passing answer are $0.00 and $0.2698099428571429 (displayed as $0.27). The denominator for this last metric is passing answers, with all nine questions' API charges in the numerator.

Sources: artifacts/study/validated-proof-2026-10-19/evidence.json; artifacts/study/S1-lean/run.json; docs/agere/study/PROOF_STATUS.md. This uses existing validated results only, without rerunning or rescoring. The baseline received the same semantic documentation and one SQL execution repair. Patient-list pass threshold is F1 ≥ 0.98; passing is not a claim of exact-match accuracy. The set is small and workflow-selected, and does not establish broad superiority. Local compute, build, validation and operating costs were not measured. The saved run predates later stack changes; not every pictured component executed on these questions. These are API-dollar metrics, not a measured token-count reduction or total cost of ownership.


### Learning and bottling: implementation boundaries

The public hook is “turn useful reasoning into reusable capability.” This is an architectural objective, not a measured continuous-improvement curve. Do not call the current stack fully autonomous or continuously self-training.

- `clinical_core/agere/clinical/learning.py`: captures accepted hosted question/plan pairs after answer checks, records outcomes and corrections, quarantines warning-bearing or disputed entries, and reuses a matching normalized question only in the matching data/semantic context. A correction disables reuse. Operational acceptance is not independent ground truth.
- `tools/learning_loop/loop.py`: an explicitly invoked tool builds gap reports, regression cases, routing data, eligible bottling examples and retraining recommendations. Grammar changes are proposals for review. It does not launch training or activate adapters; `retrain_decision()` emits suggested next steps.
- `clinical_core/agere/clinical/bottled.py`: Qwen3-4B narrow slot adapter is enabled by default when available, with agreement/parser checks and decline/fallthrough behavior. The separate broad SQL adapter remains default-off. Historical documentation saying all bottling is off predates the narrow adapter activation.
- `tools/bottling/narrow_data.py` and `tools/bottling/lora-narrow.yaml`: the installed narrow adapter was trained on synthetic slot templates and Haiku paraphrases checked against parser constraints, using MLX LoRA. Do not imply that it was automatically trained from every user query, or that the broad-SQL training export directly trains this narrow adapter.
- Benchmark exclusions protect training candidate preparation. No new benchmark, adapter training or model activation was performed for this communication revision. No additional savings from the learning loop have been established by the cited nine-question experiment.

Cheaper model selection is a fair comparator. The available validated cost result compares only the tested Opus baseline; it cannot establish a win over Haiku, Sonnet or all LLM architectures. The architecture hypothesis concerns reducing repeated inference through matching, compilation, evidence selection and qualified reuse.
