# LinkedIn draft: insight from disparate data

Status: prepared for Mike to review and publish. Public copy has no project/company name or employer reference; model/provider names remain. Nothing has been posted or sent.

## Post

Data → Information → Intelligence → Wisdom

I built a data analytics stack and tested it on synthetic healthcare data to explore how disparate claims, notes, labs and enrollment records can become useful insight—with deliberate attention to token economics.

Each component has a specific job:

• Preserve meaning.
PDFKit + Apple Vision OCR extract document text. Canonical records, semantic SQL and explicit code mappings preserve patient, date and denominator definitions. UltraBERT-Embed-150M provides catalog/terminology matching when exact mappings fall short.

• Retrieve before reasoning.
BM25 finds exact terms; Granite Embedding English R2 finds related wording. Reciprocal-rank fusion combines rankings. BGE reranker v2-m3 prioritizes relevant passages; a learned schema reranker selects tables, fields and values. The purpose: better evidence and less irrelevant context.

• Interpret bounded evidence.
Jev 1.13.0 and Perplexity Decisions (pplx-decider-v1.1-27b) are alternatives for typed decisions about subject, assertion and time. They help distinguish a current patient fact from negation or family history. Rules and TF-IDF/logistic heads handle recurring cases; BioClinical-ModernBERT-base supports research task features.

• Reuse and compile.
all-MiniLM-L6-v2 supports plan matching and routing. A compiler and SQL families turn supported questions into executable plans. Qwen3-4B + narrow LoRA handles parser misses by proposing typed slots that code checks. Its separate broad-SQL adapter remains an experiment, default-off.

• Escalate selectively.
Perplexity can select complete recipes; Claude Haiku 5.5 fills bounded slots. Claude Opus 5.5 handles unfamiliar questions through analyst proposals, system-run SQL and bounded repair. Claude Sonnet 5.5 remains a configured alternate. Every question does not need every model.

• Calculate with appropriate methods.
The stack has 29 analytical engines: Kaplan–Meier/Cox for time-to-event, propensity methods for treatment comparisons, empirical Bayes for unstable estimates, and queueing methods for capacity questions, among others.

Classic computer science connects it all: types/contracts reject malformed plans; indexes narrow retrieval; memoization + content hashes reuse valid work; dependency invalidation rejects stale results; provenance traces answers; usage ledgers account for calls and retries.

In a saved nine-question pilot with independently reconciled references:
• Specialist-first stack: $0.00 model API charges; 9/9 scorer passes.
• Opus text-to-SQL on every question: $1.89; 7/9 passes.

Small synthetic set; not a general benchmark. Patient-list passes used F1 ≥ 0.98. Local compute/build costs were excluded, and not every pictured component ran.

The goal is to spend tokens where interpretation adds value—and measure total cost per correct answer.

#DataEngineering #HealthcareAI #TokenEconomics

## Optional credit line for the post

Credit to the teams at @TypeSafe AI, @Perplexity, @Anthropic and @Hugging Face whose models and tools support this exploration, alongside IBM Granite, Qwen and the research teams credited in the architecture.

Select each real company from LinkedIn’s @mention menu; these plain-text placeholders are not live mentions.

## Mention recommendations

Use the four above as the main-post mentions if including the credit line. This is an editorial recommendation, not a claim about LinkedIn ranking. Keep other credits in a follow-up comment or the image caption.

| Organization | Connection to this architecture | Verified page |
|---|---|---|
| TypeSafe AI | Jev typed evidence decisions | https://www.linkedin.com/company/typesafe-ai/ |
| Perplexity | Decisions API / typed decisions and recipe selection | https://www.linkedin.com/company/perplexity-ai |
| Anthropic | Claude analysis, bounded helper and composition roles | https://www.linkedin.com/company/anthropicresearch |
| Hugging Face | Open-model distribution and tooling ecosystem; not the creator of every hosted model | https://www.linkedin.com/company/huggingface/ |
| IBM | Granite embeddings; optional additional credit | https://www.linkedin.com/company/ibm/ |
| Alibaba Cloud / Qwen team | Qwen3-4B; optional additional credit | https://www.linkedin.com/company/alibaba-cloud-computing-company |

UltraBERT should be credited to DMIS Lab, not Hugging Face as its creator. Model card: https://huggingface.co/dmis-lab/UltraBERT-Embed-150M

## Source checks

- TypeSafe: https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Perplexity: https://docs.perplexity.ai/docs/decisions/quickstart
- IBM: https://huggingface.co/ibm-granite/granite-embedding-english-r2
- Qwen: https://huggingface.co/Qwen/Qwen3-4B
- MedGemma: https://deepmind.google/models/gemma/medgemma/
- Implementation roles/status: component-catalog-2026-10-19.json and source-linked FULL_STACK_EXPLAINED.md.

## Image

Current lead image: [illustrated stack cutaway](linkedin-stack-cutaway.png). It replaces the dense neon/card-grid overview with a light isometric machine drawing: shared data/meaning foundation, evidence selection, three selectable execution paths, computation and sourced outputs. Named tools/model roles appear on adjacent callouts. Classic CS mechanisms form the physical base and the valid-work reuse loop is visible. The headline remains Data → Information → Intelligence → Wisdom; observed pilot API economics remain at the bottom.

The cutaway is a conceptual architecture illustration, not a literal deployment/network topology or a claim that every displayed module runs on every question. Broad Qwen SQL remains default-off and BioClinical features remain research. All 29 engine names remain available in the [supporting detail page](linkedin-stack-02-methods.png); the hero groups them into four families.

[Exact generation prompt](linkedin-stack-cutaway-prompt.txt). Generated with the built-in image tool, without a paid external image service or CLI/API fallback. Visually inspected model labels, branch distinctions, status notes and pilot figures. Nothing has been published. Prior PNGs and the [earlier two-page PDF](../../../output/pdf/data-information-intelligence-wisdom-linkedin.pdf) are retained for reference; that PDF has not been updated with this new hero.

### Visual references reviewed

- [Vercel: Compute that takes any shape](https://vercel.com/blog/fluid-compute-takes-any-shape): inspected the expanded Fluid diagram. Clear layer grouping and separation of interface, execution and underlying components.
- [Stripe: Ledger](https://stripe.com/blog/ledger-stripe-system-for-tracking-and-validating-money-movement): inspected the expanded data-quality funnel. One dominant visual form and a restrained palette make the central relationship readable.
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): reviewed the routing description to inform distinct, selected branches.

The final illustration is original; no source artwork, logos or vendor performance numbers were copied. The interpretation and art direction are editorial choices, not claims that these companies recommend this particular architecture.

### Evidence behind the image's comparison

The validated subset of the saved S1-lean run gives the specialist-first stack 9/9 scorer passes and $0.00 incremental model API charges, versus 7/9 and $1.8886696 (displayed as $1.89) for Claude Opus 5.5 text-to-SQL on every question. API dollars per passing answer are $0.00 and $0.2698099428571429 (displayed as $0.27). The denominator for this last metric is passing answers, with all nine questions' API charges in the numerator.

Sources: artifacts/study/validated-proof-2026-10-19/evidence.json; artifacts/study/S1-lean/run.json; docs/agere/study/PROOF_STATUS.md. This uses existing validated results only, without rerunning or rescoring. The baseline received the same semantic documentation and one SQL execution repair. Patient-list pass threshold is F1 ≥ 0.98; passing is not a claim of exact-match accuracy. The set is small and workflow-selected, and does not establish broad superiority. Local compute, build, validation and operating costs were not measured. The saved run predates later stack changes; not every pictured component executed on these questions. These are API-dollar metrics, not a measured token-count reduction or total cost of ownership.
