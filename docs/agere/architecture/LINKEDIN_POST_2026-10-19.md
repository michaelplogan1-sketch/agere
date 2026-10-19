# LinkedIn draft: insight from disparate data

Status: prepared for Mike to review and publish. Public copy has no project/company name or employer reference; model/provider names remain. Nothing has been posted or sent.

## Post

I went a little mad scientist on a practical question: can we get more reliable insight from messy healthcare data while improving token economics?

The experiment combines UltraBERT, Qwen, Jev and Perplexity with compilers, SQL, retrieval, statistics and some very old computer science.

With Claude Code/Codex, Python/SQL and Hugging Face tooling, I built a prototype tested on synthetic healthcare data. Each question uses the relevant path through the stack.

The ingredients:

• Preserve meaning. Native parsers, PDFKit + Apple Vision OCR, semantic SQL and code mappings align claims, notes, labs and enrollment. UltraBERT-Embed-150M helps match terminology and catalog entries when exact mappings fail. Patient, date and denominator definitions matter.

• Find the useful evidence. BM25 + IBM Granite embeddings cover exact terms and related wording. Reciprocal-rank fusion combines rankings; BGE and schema rerankers prioritize relevant passages, tables and fields. Less irrelevant context.

• Interpret what the evidence says. TypeSafe’s Jev and Perplexity Decisions are alternative typed readers for subject, assertion and time. Rules and TF-IDF/logistic heads handle recurring distinctions; BioClinical ModernBERT supports research features. A family-history mention should not become a current patient diagnosis.

• Compile what can be computed. MiniLM helps match plans. A compiler, typed contracts, SQL families and Learn-then-Test gates support checked execution and selective escalation. Code produces the numbers.

• Specialize recurring inference. Qwen3-4B + narrow LoRA + Apple MLX “bottles” a slot-filling task into a local LLM. It still runs inference on new inputs; the aim is cheaper repeated work with parser-checked outputs. The separate broad-SQL adapter remains default-off.

• Use broader models where needed. Perplexity selects curated recipes; Claude Haiku fills bounded slots; Opus proposes analyses for unfamiliar questions; Sonnet is a configured alternate. The system executes and checks proposed SQL.

• Bring the mathematics. The analytical engines include Kalman filters, survival analysis, causal methods, empirical Bayes, false-discovery control, graph methods and queueing. Different questions require different assumptions and calculations.

Underneath: compiler design, type systems, indexes, memoization, content hashes, dependency invalidation, provenance and cost ledgers.

A controlled learning loop captures accepted work and corrections, prepares training candidates and flags gaps. New model training and activation are separate, checked steps.

Token economics combines appropriate model selection with focused context, bounded tasks, reusable computation and specialized inference.

The goal is better total cost per correct answer—including compute, training and evaluation. Broader accuracy and cross-model cost advantages still need validation.

Data → Information → Intelligence → Wisdom.

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

Current lead image: [corrected stack cutaway](linkedin-stack-final.png). It replaces the dense neon/card-grid overview with a light isometric machine drawing: shared data/meaning foundation, evidence selection, three selectable execution paths, computation and sourced outputs. Named tools/model roles appear on adjacent callouts. Classic CS mechanisms form the physical base and the valid-work reuse loop is visible. The headline remains Data → Information → Intelligence → Wisdom. Mike explicitly rejected an Opus-only public comparison, so the footer now explains four economics mechanisms—retrieve, compute, reuse and bottle—and names lower total cost per correct answer as the design objective. It contains no dollar amounts, pass scores or cross-model savings claim. The local Qwen callout includes Apple MLX, and the return loop distinguishes reuse from separate training/evaluation.

The cutaway is a conceptual architecture illustration, not a literal deployment/network topology or a claim that every displayed module runs on every question. Broad Qwen SQL remains default-off and BioClinical features remain research. All 29 engine names remain available in the [supporting detail page](linkedin-stack-02-methods.png); the hero groups them into four families.

[Exact correction prompt](linkedin-stack-final-prompt.txt). Input: [previous economics cutaway](linkedin-stack-economics.png). Generated with the built-in image tool, without a paid external image service or CLI/API fallback. Visually inspected model labels, branch distinctions, status notes, controlled-learning labels and the mechanism-based economics footer. Nothing has been published. Prior PNGs and the [earlier two-page PDF](../../../output/pdf/data-information-intelligence-wisdom-linkedin.pdf) are retained for reference; that PDF has not been updated with this new hero.

### Visual references reviewed

- [Vercel: Compute that takes any shape](https://vercel.com/blog/fluid-compute-takes-any-shape): inspected the expanded Fluid diagram. Clear layer grouping and separation of interface, execution and underlying components.
- [Stripe: Ledger](https://stripe.com/blog/ledger-stripe-system-for-tracking-and-validating-money-movement): inspected the expanded data-quality funnel. One dominant visual form and a restrained palette make the central relationship readable.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): reviewed the routing description to inform distinct, selected branches.

The final illustration is original; no source artwork, logos or vendor performance numbers were copied. The interpretation and art direction are editorial choices, not claims that these companies recommend this particular architecture.

### Internal evidence retained; comparison removed from public materials

The historical Opus-only comparison is retained here for traceability, not in the current public post or lead image. Mike rejected that comparison as insufficient against the reasonable cheaper-model objection.

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

The final public version makes no numerical accuracy/cost comparison. Fair comparative evaluation would need the same validated questions and evidence, comparable execution/repair opportunities, and complete cost accounting across relevant cheaper baselines. No new paid runs were authorized or performed for this revision.


### Final correction and CTO communications review

Mike approved the existing illustration concept and asked only to correct bottling. The current lead preserves the composition and changes the Qwen annotation to show new inputs → local LLM → checked slots, with cheaper recurring inference labeled a goal. The footer says “Specialize LLM inference.” Bottling remains one equal-weight component; it is not caching and does not imply eliminating LLM inference. The separate learning/retraining controls remain visible.

The rewritten hook emphasizes the combination of specialized AI, classical CS and mathematics. Public copy explicitly identifies an AI-assisted Python/SQL prototype tested on synthetic data, says questions use relevant paths, and acknowledges that broader accuracy and cross-model economics need validation. It does not claim that all methods were run on every question or that component count establishes performance.

The requested CTO critique is saved in [CTO LinkedIn review](CTO_LINKEDIN_REVIEW_2026-10-19.md). Review of communication, source-linked implementation roles and existing evidence only; no new benchmark or paid evaluation was performed. This is not a full code, statistical or clinical audit.
