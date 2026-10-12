# Agere foundation decision: do not commit to Qwen

**October 9 implementation update:** the model-independent strategy now includes
an installed Kev MLX pointer-head adapter and a matched native d1-3B adapter.
Their new bounded-classification run is separate from the historical Qwen3
chart-to-JSON recipe below. Neither candidate passed the frozen acceptance gate;
the default reader remains unchanged. See the
[reconciliation](LOCAL_DECISION_RECONCILIATION_2026-10-09.md) and
[paired run](../../artifacts/agere-local-decisions-v1/REPORT.md).
Historical “not installed” cells below no longer describe the latest build.

October 8, 2026. Follow-up to the evidence-layer build. Synthetic development only.

**Subsequent research update:** [model selection and next build](MODEL_SELECTION_REVIEW.md) sets implementation priority after deeper paper and provider review. BioClinical ModernBERT remains the first training foundation; Perplexity is the first hosted decision challenger. MedGemma is a document/reader challenger, not an assumed superior text reader. The earlier build record below remains historical.

## Decision

Agere will not have a mandatory Qwen foundation. Keep model-independent evidence contracts, reviewed labels and deterministic data/rule operations. Select different foundations for different tasks, and measure the complete pipeline before promotion.

Our installed Qwen3-4B instruction-reader recipe achieved 26/47 exact status matches versus 44/47 for the lexicon on a small synthetic holdout, with 20 pairs sent to review. [Local report](../../artifacts/holdout/REPORT.md). This argues against deploying that recipe. It does not establish that every Qwen model or a healthcare-adapted Qwen decision head will perform poorly.

The earlier Kev-versus-Mapika shortlist lacked backbone diversity: both use Qwen3.5-4B-Base. They remain useful comparisons of adaptation/readout, not independent architecture families. [Kev card](https://huggingface.co/jaredpalmer/kev-4b), [Mapika card](https://huggingface.co/Mapika/decider-4b).

## Revised comparison roster

| Job | Candidate | Reason to consider it | Current status |
|---|---|---|---|
| Evidence classification / reranking | BioClinical ModernBERT-base | Compact clinical encoder; potential foundation for trained evidence relations | First adaptation candidate, not installed or fine-tuned |
| Medical chart interpretation | MedGemma 1.5 4B | Gemma-based medical text and document understanding | Cross-family reader/teacher candidate; gated access/terms and runtime remain to resolve |
| Bounded decisions | Liquid d1-3B | LFM-based typed decision model with a distinct backbone | Cross-family decision candidate; custom runtime and license need inspection before use |
| Bounded decisions | One of Kev / Mapika | Keep a Qwen-derived specialist as a control; choose a faithful feasible runtime | Not installed; no clinical winner selected |
| Local plan proposals | IBM Granite family | Additional non-Qwen instruction/tool-use candidate if local planning becomes a bottleneck | Secondary, not a new dependency |
| Lightweight routing | Existing MiniLM + trained head | Already available and cheap to compare | Experimental proposal-only baseline |
| Hosted comparison / difficult interpretation | Existing Jev and Claude adapters | Shared task contract and prior integration work | No new API calls in this update |

[BioClinical ModernBERT's card](https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base) describes a 150M-parameter base encoder and MIT-licensed weights. It is a masked-language model: a useful classifier or reranker requires downstream training, not just loading it and calling it a clinical expert. The [paper abstract](https://arxiv.org/abs/2506.10896) provides further context; this follow-up reviewed the card and abstract, not the entire paper.

[MedGemma's card](https://huggingface.co/google/medgemma-1.5-4b-it) includes medical-document and EHR understanding and expects task adaptation. Its medical pretraining does not establish VBC measure correctness, long-chart evidence recall or cheap deployment. [Liquid's card](https://huggingface.co/LiquidAI/d1-3B) describes typed decisions without generated prose and an LFM base. Input processing still costs compute; its published latency is not our laptop measurement. [IBM's Granite 4.1 release](https://research.ibm.com/blog/granite-4-1-ai-foundation-models) supports considering an additional instruction/tool-use family, not a clinical-accuracy claim.

## Selection criterion

Compare on matched observations and information cutoffs, preserving exact source references. Prioritize missed exceptions, wrong subject/time, false care gaps, unsupported answers and appropriate uncertainty. Record coverage, latency, peak memory, input/output tokens where relevant, failed calls and human review. Minimize total cost at the required quality; do not combine clinical harm and dollars with arbitrary weights.

Historical benchmark truth is separate from what a chart supports. Identical visible inputs can correspond to different unseen realities; the correct learned behavior may be uncertainty. Changing CMS/commercial/Medicaid rules belong in retrieved, versioned logic, not mandatory retraining of every foundation.

## Work completed in this update

- Implemented `clinical_core.learning`: development-only review preparation, immutable review revisions, explicit supersession, source-reference validation, model-independent train/calibration exports and dataset version preservation.
- Prepared 60 synthetic review tasks from `hard_dev`: 29 training-partition and 31 calibration-partition tasks. These are deliberately selected across baseline states, not a representative accuracy sample. Reserved test families and evaluation worlds are excluded; patient clones retain family partitions.
- Model input contains raw allowlisted observations and dates, not hidden truth, saved answer labels or baseline proposals. Proposals remain separately visible to reviewers.
- Added an offline [review screen](../../artifacts/agere-learning/review.html). Reviewers choose a label, identify supporting records, explain their reasoning and download JSONL for explicit import. Nothing is uploaded or automatically submitted.
- The initial [export manifest](../../artifacts/agere-learning/export-v1/manifest.json) has 60 pending reviews and zero training/calibration labels. No clinician review is fabricated. No new weights were trained, model files downloaded or paid model calls made.
- All 59 software checks pass. The review page's JavaScript passes syntax checking. Browser interaction/visual validation was not performed; the existing local-file browser restriction remains.

Review identity is caller-attributed, not authenticated; clinical credentials and correctness are not established by the software. Exact reference checks do not prove entailment. Exact duplicate/conflicting inputs are blocked from exports, but near-duplicate language still needs review. This is a local synthetic workflow, not a production labeling service.

The new labels concern evidence reconciliation. They do not yet cover all dose, tolerance, entity-span, renal-trajectory or policy-compilation skills. Reviewed correction history updates training targets; it does not silently overwrite source assertions or activate new clinical decisions.

## Use the workflow

The first queue is `artifacts/agere-learning/queue.jsonl`. The browser view keeps saved reviews only in tab memory until downloaded. Provide reviewer attribution, supporting records and a rationale; exclude ambiguous examples when appropriate. No examples are pre-marked reviewed.

```sh
cd /Users/mikel/clinical-insights-local
.venv/bin/python -m clinical_core.review_view artifacts/agere-learning/queue.jsonl artifacts/agere-learning/review.html
# After an actual reviewer has downloaded a review file:
.venv/bin/python -m clinical_core.learning import-reviews /absolute/path/agere-reviews.jsonl
.venv/bin/python -m clinical_core.learning export artifacts/agere-learning/export-v2
```

`--database PATH` precedes the subcommand when selecting another review database. Export directories must be empty. For a correction to an imported review, supply `previous_revision` equal to the current review revision returned on import. Earlier revisions and exports remain intact.

The immediate model-training recommendation is a compact clinical encoder with reviewed task heads, measured against the simple baseline. MedGemma and a non-Qwen decision model should challenge the Qwen-derived specialist on complementary tasks. None is selected as the universal winner.
