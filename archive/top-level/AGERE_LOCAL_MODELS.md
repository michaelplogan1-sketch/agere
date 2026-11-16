# Agere: local models and mathematical components running together

Historical October 8 milestone, with the October 26 opt-in component-learning integration described in `docs/agere/COMPONENT_LEARNING_INTEGRATION_2026-10-26.md`. For current status, read
`AGERE_STACK_RECONCILIATION.md`. Later receipts supersede this document's statements
that task heads, OCR and tensor experiments had not yet been implemented. Those
earlier experiments are not all connected to the current warehouse question path.

Built October 8, 2026. All patient data and supervision in this build are synthetic.
This extends `AGERE_CONNECTED_STACK.md`; its earlier statements that all new
encoders remained uninstalled are superseded by this record. Clinical reviewed
labels and autonomous execution remain separate, unfinished work.

## Installed and executed

| Component | Actual role | What it does not establish |
|---|---|---|
| Granite Embedding English R2 | Dense representations of fully windowed note text; cosine retrieval combined with lexical BM25 using reciprocal-rank fusion | Retrieval relevance is not clinical entailment or complete evidence |
| BGE reranker v2-m3 | Scores bounded query/passage candidates; preserves selected protected exception passages ahead of ordinary candidates | Logits are not clinical probabilities; reranking cannot recover passages missed upstream |
| BioClinical ModernBERT-base | Frozen contextual representations for retrieved clinical text, saved for downstream learning | No new Agere clinical task head has been trained; representations are not diagnoses or decisions |
| Agere finite joint observation prototype | Learned distribution over simulator therapy, outside-channel and documentation states given observed evidence features | An experimental generative classifier, not a validated clinical latent-state model or new mathematical theorem |
| CUSUM and EWMA | Monitor monthly volumes of visible notes, pharmacy and lab records | Alerts do not distinguish cause or establish worsening care |
| Existing conditional kernel, source ledger and dependency cache | Preserve premises, source lineage, time and unknown states while reusing valid work | Extracted proposals do not become accepted clinical facts because a model agrees |
| UltraBERT-Embed-150M (second pinned encoder, `local_encoders.PinnedSentenceEncoder`) | Two existing default jobs: dense re-ranking of the raw received catalog in `answer_guard` (probe + the SQL writer's raw-catalog slice; lexical first stage, RRF k=60 fusion, catalog embedded once per source digest) and a thresholded fallback for lab/condition labels that code and alias matching leave unresolved (`concepts.match_label`) | Default stack encoder, router, semantic cache and dense index remain unchanged. The October 26 candidate additionally supports this pinned encoder in explicitly qualified routing/shape heads; no default release. Cosine is not a mapping probability, so below-threshold proposals are reported, never bound |

The installed model manifests pin repository revisions, file sizes and SHA-256
digests. Inference verifies model bytes, uses local files only, disables remote
code, and uses CPU in the measured environment. The one exception is
UltraBERT-Embed-150M, whose architecture ships as remote code: its manifest is
versioned in the repository (`config/agere/encoders/ultrabert-embed-150m.json`)
and pins the weights, both remote-code modules, the tokenizer and the
sentence-transformers configs; `trust_remote_code` is used only after every hash
verifies, the imported module files are re-hashed after load, and any mismatch
refuses the model (callers fall back to their lexical path). Integrated-path
measurements: `tools/second_encoder/measure_integrated.py`. Brand-name drugs are
resolved by the RxNorm-named table `config/agere/drug_brands.json`, not by any
encoder. The BioClinical masked-language
prediction head is intentionally discarded when loading its pretrained encoder;
no randomly initialized clinical classifier is presented as a working reader.

Sources: [BioClinical model card](https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base),
[IBM retrieval model card](https://huggingface.co/ibm-granite/granite-embedding-english-r2),
[BGE reranker card](https://huggingface.co/BAAI/bge-reranker-v2-m3).
Author-declared licenses are recorded in the manifests; downloading a checkpoint
does not resolve the requirements for a clinical commercial deployment.

## Executable path

```text
Observed synthetic source feeds
  → versioned ledger and event/arrival snapshot
  → inherited evidence reader and source-coverage reconciliation
  → conditional symbolic analysis (unvalidated premises stay unknown)
  → finite joint patient/recording hypotheses for supported subjects
  → bounded neural branch: complete windows → BM25 + Granite → BGE
      → exact source passages + frozen BioClinical representations
  → patient review report + lineage + compute receipt

Cross-cutting: model/content identities, embedding and reranker caches,
              observed-feed CUSUM/EWMA, separate training/review provenance.
```

All source text within the selected patients' visible notes is windowed; none is
silently truncated. Only a bounded subset of baseline assessments takes the
neural branch. Packet selection still omits other passages and makes no
completeness guarantee. Overflow of protected evidence is explicit. Source
quotes retain patient ID, note ID, offsets, source hash and ledger provenance.
Known future arrivals are excluded; unknown arrivals remain labeled as unknown.

The joint prototype consumes binary observed evidence and source-presence
features. Repeated copied assertions do not multiply identical feature votes.
The model's conditional-independence assumption remains imperfect: different
features can still be correlated. Probabilities are not clinically calibrated.

## What was trained, and what the comparison found

The finite joint model was fitted to **3,030** development examples from **229**
source families. Calibration contains **768** examples from **53** separate
families. Reserved test families never enter fitting or calibration. Labels
come from the development simulator's medication status, outside-channel marker
and low-documentation flag. They are not invented human reviews or targets copied
from the baseline reader.

Training truth is loaded only in `train_joint_observation.py`, after observed
inference has run. The inference module and `clinical_core.neural_stack` do not
load truth files. The target `therapy_absent` includes appropriate holds and must
not be interpreted as a care gap. `no_outside_marker` describes the simulator's
target, not a guarantee of complete local capture. The training cohort is
limited to supported HF medication assessments with a defined simulator target.

The joint prototype's calibration joint-state accuracy is **0.86328125**; the
logistic comparator using the same observed features scores **0.8763020833333334**.
The more elaborate generative model therefore has not demonstrated superiority.
The report also records marginal accuracy, Brier score, log loss and limitations.
These are simulator-agreement measurements, not clinical effectiveness results.

Receipts: `artifacts/agere-joint-v1/training-report.json` and
`artifacts/agere-joint-v1/reproducibility.json`. The latter verifies exact
reconstruction from saved supervision and disjoint source-family partitions.

## Run locally

From `/Users/mikel/clinical-insights-local` after installing the checkpoints:

```sh
HF_HUB_OFFLINE=1 .venv/bin/python -m clinical_core.neural_stack \
  --world /Users/mikel/insight-engine/data/worlds/hard16_llm \
  --namespace hard16_llm \
  --max-cases 12 \
  --database artifacts/agere-neural/verified-runtime.sqlite3 \
  --vector-cache artifacts/agere-neural/verified-encoder-cache.sqlite3 \
  --output artifacts/agere-neural/next-run
```

Choose a new output directory each time. `--sample` accepts the existing synthetic
sample contract instead of a world. The current CLI supports at most 100 neural
cases per explicit batch. It is not a production connector for arbitrary PHI or
a billion-record distributed service.

To reproduce the research fitting independently, choose a new model directory:

```sh
.venv/bin/python train_joint_observation.py --output artifacts/agere-joint-v2
```

The separate reviewed-label baseline trainer and validation workflow are described
in `BUILD_NEXT.md`. It still blocks fitting on the current empty reviewed export.
Simulator supervision has not been relabeled as clinician-reviewed supervision.

## Measurements and remaining limitations

The hardest-world run processes **4,836** synthetic patients through the baseline,
yielding **3,914** assessments. **2,172** supported assessments receive joint
research hypotheses. The neural pilot covers **12** cases from **11** patients.
The source review verifies **96** selected quotes and **12** representation
matrices. All **3,914** conditional clinical workflows remain unresolved.

The execution JSON separates baseline, encoder and reranker wall times, input
token counts, cache hits and actual computations. Counts use each local model's
tokenizer. Paid inference calls are zero; local hardware, electricity, storage
and human review are not priced. This is not a claim of zero total operating cost.
Cold/warm receipts belong to their exact code and model versions. A lexical
floating-point ordering issue found in the first replay was fixed before the
verified v2 replay; the earlier v1 artifacts are retained as development history.

Source-volume monitoring uses the first six complete observed months as an
engineering reference. Seasonality, changing enrollment, reporting maturity and
autocorrelation are not adjusted. A flag asks for source investigation; it is not
a validated process-control false-alarm rate or a care-quality finding.

## Still to earn inclusion

Clinically reviewed encoder heads and probability calibration; broader ontology,
identity and unit normalization; actual OCR; matched Jev/Decider/frontier API
comparisons; learned information acquisition; general payer-rule compilation;
causal benefit and capacity allocation; tensor-network and algorithm-search
experiments; governed external data connectors. Execution agents remain future
scope as requested. No model was auto-promoted into clinical action.

The shareable architecture posters describe this research destination. Their
candidate labels reflect the pre-installation architecture review. This build
record and its execution receipts are the current authority for what runs.
