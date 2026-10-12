# Agere local SLM selection: external evaluation

October 9, 2026. Written by Claude from EDGE_MODELS.md, FOUNDATION_DECISION.md, MODEL_SELECTION_REVIEW.md, LOCAL_MODEL_STATUS.md, AGERE_LOCAL_MODELS.md and the model cards cited below. No weights were run.

## Decision

Primary local bounded-decision SLM: **Kev 4B** (jaredpalmer/kev-4b).
Challenger: **Liquid d1-3B** (LiquidAI/d1-3B).
Document/image reader only: **MedGemma 1.5 4B**.
Hosted comparators, unchanged: Jev 1.13, Perplexity Decider v1.1.
Encoders, unchanged: Granite Embedding English R2, BGE reranker v2-m3, BioClinical ModernBERT-base.

## Reasons

1. The open SLM role in the stack is scoring supplied options over retrieved evidence (status, held vs taken, patient vs relative, current vs historical, unknown). Kev is Qwen3.5-4B-Base + LoRA + a pointer head over supplied options; the task shape matches the evidence contract including an explicit unknown option.
2. Kev states a validated 8,192-token context, matching the Granite R2 and BioClinical 8,192-token windows already in the pipeline.
3. Kev is Apache-2.0 for base, adapter and head. d1-3B is under the LFM license; MedGemma under HAI-DEF terms. For a commercial client deployment the license gap outweighs the board spread between Kev 83.0, lev 84.6, Mapika 83.1 and JevK5 83.9.
4. The installed Qwen3-4B result (26/47 exact vs 44/47 lexicon; worklist recall 0.167) was open chart-to-JSON generation. It is not evidence about constrained option scoring and must not be cited against Kev.
5. d1-3B is the challenger because it is a non-Qwen backbone (LFM2.5-VL-3B), documents an MPS path, and reads images (faxed outside-care, SNF and HH documents). License and native runtime need review before it touches non-synthetic data.
6. MedGemma stays a reader, not a decider: the MedGemma 1.5 report, Table 4, gives EHRNoteQA 80.4 for MedGemma 1.5 4B vs 90.6 for Qwen3 VL 4B.

## Conditions before a Kev result counts

- Reimplement the pointer head faithfully in MLX at a pinned revision with SHA-256 manifests, as done for the installed encoders. A GGUF chat loader does not reproduce the readout.
- Run only the bounded-classification experiment defined in LOCAL_MODEL_STATUS.md "Next experiment": constrained decoding over enumerated options, scored against the lexicon baseline on the reserved cohort, with retrieval misses reported separately from interpretation errors.
- Report missed exceptions and false care-gap proposals separately from exact-match accuracy; record latency, peak MLX memory and failed calls per run.
- Calibrate the acceptance threshold on the calibration partition; keep reserved test families out.
- Run d1-3B under the identical contract before any winner is named.

## Sources

- https://huggingface.co/jaredpalmer/kev-4b
- https://huggingface.co/LiquidAI/d1-3B
- https://huggingface.co/google/medgemma-1.5-4b-it
- https://arxiv.org/html/2604.05081v1
- https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base
- https://huggingface.co/ibm-granite/granite-embedding-english-r2
- https://huggingface.co/BAAI/bge-reranker-v2-m3
