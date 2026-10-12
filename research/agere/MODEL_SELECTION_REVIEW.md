# Agere model selection and next build

**October 9 implementation update:** the Claude review's Kev-first local
implementation is complete, and Liquid d1-3B has already run as its paired
challenger. Kev, d1 and the lexicon were scored on identical evidence packets;
full-chart lexicon was retained separately. Neither model passed the frozen
acceptance gate, and neither was promoted. This adds a local decision role without
replacing BioClinical's learning role, retrieval components or hosted comparators.
See [reconciliation](LOCAL_DECISION_RECONCILIATION_2026-10-09.md),
[build](../../LOCAL_DECISION_BUILD.md) and
[results](../../artifacts/agere-local-decisions-v1/REPORT.md).
Installation/training statuses in the October 8 review below are historical.

This is the pretrained-model workstream of the broader [Agere mathematical and cross-industry research program](RESEARCH_PROGRAM.md). It does not replace the joint patient/observation model, decision-preserving selection or information-acquisition research.

Reviewed October 8, 2026. This is a research-based engineering selection, not a claim that untested candidates outperform the current pipeline. All Agere patient experiments remain synthetic. No weights were downloaded or trained, and no paid inference was run for this review.

## Decision

Build the next proprietary learning component on **BioClinical ModernBERT-base**, initially for evidence relations and medication-state interpretation. Add **Perplexity Decider v1.1** as the first hosted decision challenger to the existing Jev adapter. Preserve the baseline and select winners by task after matched observations, reviewed targets and full-cost measurement. Do not replace the entire stack with a single new foundation.

Use **Granite Embedding English R2** as the first candidate for improving dense retrieval alongside lexical retrieval. Compare **BGE reranker v2-m3** with **Qwen3-Reranker-0.6B** when retrieval becomes the next measured bottleneck. Keep **MedGemma 1.5 4B** for a medical-document/reader comparison, rather than assuming its medical branding makes it our best text model. Keep **Liquid d1-3B** as a later local decision challenger. Retain the existing Claude adapter for difficult synthesis and a quality reference; do not send routine classification through it by default.

These choices set implementation priority. They do not activate new production routes, approve automatic clinical decisions or imply installation.

## Evidence that changes the recommendation

**Medical specialization does not guarantee the strongest text reader.** Google's MedGemma 1.5 report, Table 4, reports EHRNoteQA accuracy of 80.4 for MedGemma 1.5 4B and 90.6 for Qwen3 VL 4B. On EHRQA, Table 3 instead favors MedGemma 1.5 4B (89.6 versus 87.3). Prompts and decoding settings also matter. These are the paper's results, not ours; the Qwen variant is different from the installed Qwen3-4B experiment. The report gives MedGemma a stronger position in specialized medical vision and shows document-extraction improvements. The inference for Agere is to assign document and text roles separately, not discard Qwen as a family. [Technical report](https://arxiv.org/html/2604.05081v1)

**A clinical encoder is a training foundation, not a ready-made expert.** BioClinical ModernBERT-base has 150M parameters and supports 8,192-token inputs, with MIT-licensed weights. It is a masked-language encoder requiring task adaptation. Its authors report that MIMIC supplies over 95% of the clinical pretraining corpus; English-only training and incomplete i2b2 evaluation are stated limitations. Its paper evaluates classification and entity tasks, not our complete VBC workload. We should train source-grounded tasks and explicitly test outpatient/payer distribution shifts. [Model card](https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base), [paper, methods and limitations](https://arxiv.org/html/2506.10896v1), [author training repository](https://github.com/lindvalllab/BioClinical-ModernBERT)

**Perplexity provides a plausible adaptation path, with serving caveats.** Its published decision checkpoint is Qwen3.8-27B-derived with a separate readout, modified full-attention behavior and saved calibration temperature. Apache-2.0 weights support investigating adaptation, but the documented native runtime needs roughly 49 GiB of weights plus working memory on CUDA. The card retains private-access wording despite public metadata. Start with the documented API and resolve artifact access/parity before attempting local training or conversion. An ordinary causal chat loader is not equivalent. [Model card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b), [API](https://docs.perplexity.ai/docs/decisions/quickstart)

## Role-by-role comparison

| Role | Preferred first candidate | Alternatives reviewed | Why this order / limitation |
|---|---|---|---|
| Clinical evidence relations and status | BioClinical ModernBERT-base | Existing MiniLM head; general ModernBERT control | Compact trainable foundation; needs reviewed task labels and source-specific temporal context |
| Dense passage retrieval | Granite Embedding English R2 | EmbeddingGemma-300m; existing MiniLM | Retrieval-trained rather than raw masked-language pooling; English initial scope |
| Passage reranking | BGE reranker v2-m3 | Qwen3-Reranker-0.6B; MedCPT; Jina v3.5 | Start with established cross-encoder and a different-family challenger; retain excluded/contradicting evidence |
| Hosted bounded decisions | Perplexity Decider v1.1 challenger | Existing Jev 1.13 | Cheap published input rate and weights; actual clinical superiority remains unmeasured |
| Local bounded decisions | Liquid d1-3B later | Earlier Kev/Mapika shortlist | Distinct base architecture; custom runtime, terms and quality need verification |
| Medical document interpretation | MedGemma 1.5 4B challenger | General reader and frontier comparator | Medical-document/vision rationale stronger than a universal text-reader claim |
| Difficult synthesis and plan proposals | Existing Claude adapter initially | Current Haiku, Sonnet and Gemini Flash offerings | Avoid a new dependency until a frontier cost/quality comparison is justified |
| Arithmetic, policy logic, uncertainty, reuse | Existing code and statistical components | LLM-only comparison | Keep explicit dates, units, rule versions, provenance and missing facts outside opaque generated answers |

### Retrieval and reranking findings

**Granite English R2:** the author card describes a 149M-parameter retrieval-trained bi-encoder with 768-dimensional vectors, 8,192-token context and Apache-2.0 weights. IBM documents data provenance and retrieval-oriented adaptation. This is our first dense-retrieval candidate, not a proven healthcare winner. Pin the revision and handle overlength documents explicitly rather than accepting example-code truncation. [IBM card](https://huggingface.co/ibm-granite/granite-embedding-english-r2)

**EmbeddingGemma:** Google's 300M embedding model supports multilingual retrieval and selectable embedding dimensions, with a 2,048-token input limit and Gemma terms/access conditions. It is a useful alternative if multilingual requirements or storage constraints dominate. The initial English clinical scope does not require adding it alongside Granite immediately. [Google card](https://huggingface.co/google/embeddinggemma-300m)

**BGE and Qwen rerankers:** BGE v2-m3 is an XLM-RoBERTa-based cross-encoder with Apache-2.0 weights. Qwen3-Reranker-0.6B is a separate purpose-trained ranking model with Apache-2.0 weights and its own scoring protocol. Neither's generic relevance score guarantees retaining a clinically decisive exception. Compare them on query/passage pairs with reviewed supporting, contradicting, exception and irrelevant labels. Do not infer reranker failure from our unrelated Qwen chat-reader result. [BGE card](https://huggingface.co/BAAI/bge-reranker-v2-m3), [Qwen card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)

**MedCPT:** NCBI's cross-encoder is aimed at biomedical literature relevance and its example uses a 512-token pair limit. It is a useful literature-retrieval control, not the first choice for longitudinal patient evidence. [NCBI card](https://huggingface.co/ncbi/MedCPT-Cross-Encoder)

**Jina:** v3 points new projects to v3.5. The successor uses listwise reranking and is published under CC-BY-NC-4.0. Keep it as a research comparator; do not make these published weights the assumed commercial distribution dependency without resolving commercial terms. This is a deployment screening fact, not a legal determination. [Author card](https://huggingface.co/jinaai/jina-reranker-v3.5)

### Local and frontier findings

Liquid d1-3B is based on LFM2.5-VL-3B, returns typed decisions and documents an MPS path with Apple hardware measurements. The page's short-input timings differ substantially from its longer-input timings. Those are author measurements, not this laptop's performance. The LFM license and native implementation need explicit review before adoption. Do not substitute its NVIDIA-specific W8A8 checkpoint on Apple hardware. [d1 card](https://huggingface.co/LiquidAI/d1-3B), [quantized variant](https://huggingface.co/LiquidAI/d1-3B-w8a8)

Google describes MedGemma as a developer model needing task validation/adaptation, with medical document and EHR capabilities. It is governed by HAI-DEF terms. There is no need to incur the local runtime work until reviewed document tasks can test its contribution. [Google overview](https://developers.google.com/health-ai-developer-foundations/medgemma), [model card](https://developers.google.com/health-ai-developer-foundations/medgemma/model-card)

The current Anthropic catalog includes Opus, Sonnet and Haiku offerings with materially different prices. Agere's existing gateway pins `claude-opus-5-5`; that is an integration fact, not evidence it is the cheapest adequate synthesizer. Google's current catalog also offers Gemini Flash variants. Keep them on the frontier comparison list; do not confuse API availability or provider benchmarks with our workload quality. No new frontier model has been integrated in this review. [Anthropic catalog](https://platform.claude.com/docs/en/models/overview), [Google catalog](https://ai.google.dev/gemini-api/docs/models)

## Economics and promotion rules

At review, Perplexity lists $0.02 per million input tokens, no output charge and no request fee for v1.1. Jev lists $0.042 per million input tokens and free output; its documentation describes ingesting shared state once for multiple questions. Compare actual usage receipts at multiple questions per chart. This review does not establish Perplexity's shared-state billing equivalence or a measured patient-level savings factor. [Perplexity price](https://docs.perplexity.ai/docs/getting-started/pricing), [Jev model and batching documentation](https://docs.typesafe.ai/models)

The selection objective is total cost per correct supported task at a stated coverage, not price per token alone. Record initial indexing, incremental updates, retrieval, inference, cache overhead, failed attempts, human review, latency and local memory. Record tokenizer counts separately by model. Do not call every specialist on every case or duplicate billing savings already accounted for by the dependency cache.

Promotion requires matched visible evidence and cutoff, held-out patient families, reviewed labels, preserved unknown states, exact evidence references and explicit model/runtime revision. Report missed exceptions and false care-gap proposals separately from average accuracy. Calibrate the acceptance threshold on the calibration partition, and keep final evaluation separate from training/model selection. Model agreement is not independent confirmation. A source ID being valid does not establish that its text entails a claim.

## Training is the next product work

The current review database contains 60 tasks and zero submitted reviews. The existing export manifest has no reviewed training or calibration rows. Do not describe this as a ready clinical training set. The existing selector was trained on weak synthetic targets; these are distinct from reviewed labels.

1. **Prepare the specialist boundary.** Implement a revision-pinned BioClinical encoder wrapper and downstream heads with source offsets, explicit chunk coverage and bounded input lengths. No random initialization may be presented as a clinical prediction. First fit a simple head as the baseline; compare partial/full encoder adaptation only as supervised data supports it.
2. **Turn richer synthetic records into training examples.** Use the other world's arrival times, medication events and source coverage through the upstream contract. Distinguish observable evidence labels from hidden simulator truth. Use controlled counterfactuals: held versus taken, patient versus relative, current versus historical, present versus negated, and record missing versus care explicitly not completed. Keep rules and clinically appropriate exceptions tied to reviewed definitions.
3. **Review and learn.** Use machine suggestions to prioritize disagreement and rare exceptions, preserving their unreviewed status. Train on accepted development labels; reserve separate calibration and evaluation families. Grow the set according to error slices and learning curves rather than claiming a fixed small count is sufficient.
4. **Add the Perplexity comparison adapter.** Reuse the evidence contract with an explicit unknown option, validate all answer distributions/references, require the returned model to match, log actual billed tokens and leave automatic acceptance disabled. First verify offline fixtures. Any live comparison must honor the remaining previously authorized budget, not assume the old cap is fresh.
5. **Integrate the winning evidence skill.** Start with medication state and the evidence behind apparent HFrEF gaps. Feed proposals into dated reconciliation and the conditional kernel. The bounded synthetic workflow is not yet a GDMT guideline engine. Show what is observed, unresolved and worth obtaining next before adding renal trajectory and financial-barrier skills.
6. **Improve retrieval where errors justify it.** Add lexical-plus-dense retrieval and reranking with recall protection for exception/contradiction channels. Reuse embeddings and assertions only when their source/model revisions match. Measure whether fewer tokens preserve the decision, not just whether the packet is shorter.

Software integration and training-data preparation can proceed while reviews are pending. Actual reviewed-label training cannot. This is not a reason to replace training work with more benchmark scenarios.

## Novel research track, kept separate

The promising Agere contribution is a shared representation with separate heads for evidence status, source observability and decision relevance, connected to the conditional kernel. Train on observed facts; make uncertainty the target when hidden realities are observationally indistinguishable. Later learn which authorized observation or computation is worth its cost. This is a falsifiable architectural hypothesis using established mathematics, not a claim of a new theorem or unique model already achieved.

Do not train a new general foundation from scratch, combine model weights indiscriminately, or add quantum-inspired components before simpler methods have been fairly tested. Model-family diversity is a comparison tool, not an objective that justifies extra production cost.

## Local feasibility and scope of this review

The environment reports arm64 and Python 3.12.13. Existing packages include torch, transformers, MLX and sentence-transformers. A read-only system-memory probe was denied by the sandbox, so memory capacity and peak candidate runtime requirements were not verified. Installed packages do not prove a candidate's MPS/MLX parity. No runtime claim relies on hardware inferred from vendor examples.

This review read primary model cards, the BioClinical methods/evaluation/limitations, the MedGemma EHR/document results and limitations, author repository documentation, and current provider catalogs/prices. It is a targeted comparative review, not an exhaustive survey of every Hugging Face checkpoint or a reproduction of external results. [Prior model review](EDGE_MODELS.md), [prior training decision](FOUNDATION_DECISION.md), [working reasoning layer](CONDITIONAL_REASONING_BUILD.md).
