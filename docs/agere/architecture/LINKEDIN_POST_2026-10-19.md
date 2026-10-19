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

Use the two-page portrait carousel in [the uploadable PDF](../../../output/pdf/data-information-intelligence-wisdom-linkedin.pdf), or the individual PNGs:

1. [Overview](linkedin-stack-01-overview.png): named tools/models, their jobs and a WHY line for each of six stages; local/frontier branches; the saved nine-question API-economics comparison.
2. [Methods and foundations](linkedin-stack-02-methods.png): all 29 analytical engines, mathematical methods, ten classic computer science mechanisms and their purposes.

The only top headline remains Data → Information → Intelligence → Wisdom. The pages use midnight petrol, cyan, lime and amber with a clear technical flow. No project branding, employer reference, action-execution panel or research-candidate panel. BioClinical's research status and the broad-Qwen experiment's default-off status remain explicit; Jev and Perplexity are alternatives, not obligatory consecutive steps. Named roles were checked against component-catalog-2026-10-19.json and FULL_STACK_EXPLAINED.md. Model choices shown are local implementation configuration, not claims that these are each provider's newest models.

Prompts: [overview prompt](linkedin-stack-01-overview-prompt.txt), [methods prompt](linkedin-stack-02-methods-prompt.txt). Both generated using the built-in image tool, with no external paid image service/API fallback. The PDF embeds the images without changing their contents; both rendered pages were visually inspected. Nothing has been published.

### Evidence behind the image's comparison

The validated subset of the saved S1-lean run gives the specialist-first stack 9/9 scorer passes and $0.00 incremental model API charges, versus 7/9 and $1.8886696 (displayed as $1.89) for Claude Opus 5.5 text-to-SQL on every question. API dollars per passing answer are $0.00 and $0.2698099428571429 (displayed as $0.27). The denominator for this last metric is passing answers, with all nine questions' API charges in the numerator.

Sources: artifacts/study/validated-proof-2026-10-19/evidence.json; artifacts/study/S1-lean/run.json; docs/agere/study/PROOF_STATUS.md. This uses existing validated results only, without rerunning or rescoring. The baseline received the same semantic documentation and one SQL execution repair. Patient-list pass threshold is F1 ≥ 0.98; passing is not a claim of exact-match accuracy. The set is small and workflow-selected, and does not establish broad superiority. Local compute, build, validation and operating costs were not measured. The saved run predates later stack changes; not every pictured component executed on these questions. These are API-dollar metrics, not a measured token-count reduction or total cost of ownership.
