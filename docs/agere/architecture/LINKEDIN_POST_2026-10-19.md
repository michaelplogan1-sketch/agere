# LinkedIn draft: insight from disparate data

Status: prepared for Mike to review and publish. Public copy has no project/company name or employer reference; model/provider names remain. Nothing has been posted or sent.

## Post

I’m an ER doc who went down a mad-scientist engineering rabbit hole because of token economics.

I wanted to test which healthcare questions need an LLM, and which could be answered more accurately and cheaply with SQL, statistics or a small specialized model.

With Claude Code and Codex, I built a prototype on synthetic healthcare data. The idea: triage the question, then use the right tools.

Here’s what went into it:

• SQL + a shared data layer: keep patients, dates and definitions straight, then calculate.

• UltraBERT: match unfamiliar terminology to the data.

• BM25 + IBM Granite + BGE reranking: find relevant evidence and put the useful pieces first.

• Jev / Perplexity Decisions: alternative tools for focused interpretation, like current diagnosis versus family history.

• MiniLM + a compiler: recognize supported questions and reuse executable plans.

• Qwen + LoRA + Apple MLX: “bottle” a narrow task into a small local LLM that handles new inputs, with code checking its output.

• Claude Haiku / Opus / Sonnet: focused helper tasks, unfamiliar analysis and alternative model choices.

• Statistics: survival analysis, Kalman filters, causal methods, Bayes and queueing. Different questions need different math.

• Classic computer science: types, indexes, caching, hashes and provenance. Make work reusable, checkable and traceable.

A learning loop captures accepted work and corrections for reuse and future training. Training and model changes are separate, evaluated steps. BioClinical ModernBERT is also on the research side.

The next step is the part I’m most interested in: giving a frontier model the computed results and evidence to build something useful—an interactive command center, a PharmD program workspace or a remote-monitoring workflow. Something a team can act on, with clear ownership and review.

That’s where I want to spend frontier-model tokens: turning the analysis into a useful deliverable.

The economics question is total cost per useful, correct answer—including model calls, local compute, training and review.

Still a synthetic-data prototype. Broader accuracy and cost comparisons, and the full path from answer to action, remain work in progress.

Data → Information → Intelligence → Wisdom

#HealthcareAI #DataEngineering #TokenEconomics

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


### Voice revision

At Mike’s request, the public copy now uses his ER-doctor perspective, a simple triage analogy and short ingredient/reason bullets. The approved image is unchanged. Detailed model versions, research branches and mathematical inventory remain in the diagram and technical notes; the post uses plain language and preserves prototype, economics and learning limits.


### Final motivation and deliverable-role revision

The public post now states token economics and testing non-LLM methods as the motivation, keeps named ingredients and their jobs, and identifies frontier composition of evidence-bound interactive deliverables as the intended next step. It explicitly says the complete answer-to-action path remains work in progress. The observed main /ask failure is not presented as a working program.

Mike briefly requested an Agere wordmark, then requested a return to the previous image. The approved `linkedin-stack-final.png` was never overwritten and remains the final unbranded image. The unused wordmark version was generated with the built-in image tool at `/Users/mikel/.codex/generated_images/01a1542a-b5a3-7d23-94fc-19628fd76094/exec-ba2e2ecd-6fd3-4992-baf9-0ac273b62607.png`; it is not the selected public asset. No external paid image service or CLI/API fallback was used. Nothing has been published.
