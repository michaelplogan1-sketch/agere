# LinkedIn draft: insight from disparate data

Status: prepared for Mike to review and publish. Public copy has no project/company name or employer reference; model/provider names remain. Nothing has been posted or sent.

## Post

I recently put together a data analytics stack and tested it on a synthetic healthcare dataset.

The experiment explored how to turn disparate sources—claims, clinical notes, lab results and enrollment files—into useful insight while keeping inference costs under control.

The architecture combines:

• Data normalization, shared definitions and source provenance.
• Embeddings and rerankers to find relevant evidence.
• Small models and bounded decision models for specific interpretation tasks.
• SQL and statistical methods for computation.
• Frontier models for questions that need additional reasoning.

Early results on a small, independently checked set of synthetic-data questions were encouraging. Some questions could be answered without any model API calls. Broader validation is still ahead.

One of the most interesting parts has been token economics.

Reusing valid query plans, limiting context to relevant evidence and handling suitable tasks locally can reduce the inference a workflow needs. Each component should earn its place through useful capability, better reliability or lower cost.

The measure I care about is total cost per correct answer—including model calls, local compute, retries and human review.

Sharing the architecture below for feedback: where would you simplify it, and which tradeoffs would you test first?

#DataEngineering #HealthcareAI #AIArchitecture #TokenEconomics

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

Use data-information-intelligence-wisdom-stack.png, the unbranded analytics-only edition. Its headline is Data → Information → Intelligence → Wisdom. Original panels 9 (actions/follow-through) and 11 (research candidates) are removed; classic computer science is renumbered 09. Model names in the retained stack and token economics remain. The edit prompt is data-information-intelligence-wisdom-prompt.txt. Generated through the built-in image tool; no external paid image service or API fallback. No platform publishing has been performed.
