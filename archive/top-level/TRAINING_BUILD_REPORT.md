# Actual training and stack additions

October 8, 2026. Synthetic research only. This iteration used local compute and public research/model downloads; it made no paid Jev/frontier inference calls. It does not change the prior hosted budget or imply zero electricity, hardware or labor cost.

## What was trained

| Artifact | Supervision | What learned |
|---|---|---|
| `artifacts/learned-evidence/selector.joblib` | Synthetic development-chart passages labeled by the inherited reader | Character TF-IDF/logistic relevance model |
| `artifacts/research-training/planner.joblib` | Original VBC research questions and analyst-authored domain labels | Lexical domain classifier |
| `artifacts/research-training-semantic/planner.joblib` | Same research curriculum, frozen MiniLM embeddings | Semantic domain classifier head |

The evidence model fitted 16,122 examples after deduplication and negative sampling, drawn from 1,156 training source-patient families. Development worlds were `dev` and `hard_dev`; calibration used different source families. The raw training pool had 389,072 rows but only 18,168 distinct text/label pairs before sampling. Repeated synthetic templates are not independent clinical experiences. Source identifiers are excluded from model features.

The 96 research questions now have full analysis-plan supervision in [planner_training.jsonl](artifacts/research-training/planner_training.jsonl): required data, operators, outcomes, balancing measures, reviewer and failure modes. Those full targets are exported for future SFT. The fitted routers learn domain labels and retrieve a representative domain template; they do not generate or verify complete patient-specific plans. Research labels await expert review.

MiniLM was downloaded at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; its manifest records file checksums. Loading is local-only with remote code disabled. The encoder was not fine-tuned. MedGemma, Jev, the existing Qwen model and frontier model weights were not trained in this iteration.

## What the measurements actually show

| Model/check | Result | Interpretation |
|---|---:|---|
| Evidence selector, `hard_dev` reserved families | 99.21% weak-teacher relevance recall | Familiar synthetic language, not clinical sensitivity |
| Evidence selector, `hard16_llm` reserved families | 71.41% weak-teacher relevance recall | Large language-shift weakness; not ready to discard evidence automatically |
| Packets for 40 `hard16_llm` synthetic charts | 100,435 raw-note tokens → 30,886 packet tokens | 69.25% local-token reduction; provider overhead excluded |
| Same packet sample | 10 of 11 teacher evidence spans retained | Compression missed relevant evidence; quality-preserving savings are NOT established |
| Lexical question router, six-fold developmental CV | 16.67% top-1; 34.38% top-3 | Weak baseline |
| Frozen MiniLM + trained head, same CV | 37.50% top-1; 65.63% top-3 | Better suggestions, insufficient for automatic execution |

Numbers come directly from [the evidence training report](artifacts/learned-evidence/training_report.json), [lexical card](artifacts/research-training/planner_card.json) and [semantic card](artifacts/research-training-semantic/planner_card.json). Percentage displays are rounded. Precision, average precision, sample sizes, token scope and text overlap are retained in the files.

Family partitions prevent the same synthetic source patient entering train and reserved-family evaluation, but development and evaluation language overlap substantially. Historical engine development was not established as blind to evaluation phrasing. These are developmental measurements, not an external benchmark or proof of generalization. The routing curriculum is small, related and single-label even when a question spans several domains. No clinical accuracy, vendor superiority or patient-outcome claim follows.

## Installed components

- `clinical_core.evidence_selection`: learned passage ranking; exact source offsets and hashes; antecedent preservation; source-date cutoff; protected-span budget overflow that refuses silent truncation; local tokenizer accounting. A helper creates the existing typed provider task without calling a provider.
- `clinical_core.question_planner`: checksummed lexical/semantic models return three proposed analysis families with source references and review requirements. Long semantic inputs are rejected rather than silently truncated.
- `clinical_core.signal_math`: independent-Gaussian, uncertainty-weighted change-point segmentation. It returns measurement patterns, not diagnoses. It is not yet connected to automatic clinical lab-series extraction.
- `clinical_core.token_economics`: explicit input/output/cache cost categories, local/review costs and cache break-even calculations. Rates are caller-supplied.
- `clinical_core.experimental`: opt-in local commands connecting these additions to the existing synthetic world loader and reader. Existing clinical results and the website do not silently switch to the experimental models.

The models are intentionally not promoted into automatic clinical judgment: their measured limitations prohibit that. Callable experimental integration is complete; default clinical deployment is not.

## Run it locally

```sh
cd /Users/mikel/clinical-insights-local
.venv/bin/python -m clinical_core.experimental plan 'Which heart failure patients lack effective therapy because of cost?'
.venv/bin/python -m clinical_core.experimental changes artifacts/research-stack-demo/series.json
.venv/bin/python -m clinical_core.experimental cost artifacts/research-stack-demo/cost_scenario.json
.venv/bin/python -m clinical_core.experimental packet --world /Users/mikel/insight-engine/data/worlds/hard16_llm --patient SYNTHETIC_PATIENT_ID --subject 'heart failure therapy and appropriate holds'
```

Replace the placeholder with the `synthetic_patient_id` saved in [example_packet.json](artifacts/research-stack-demo/example_packet.json). The packet's missing evidence remains recoverable from original notes. `prepare_hosted_task` validates schema, not clinical adequacy; no automatic cascade uses these compressed packets.

Reproduce training with `train_evidence_selector.py`, then `train_research_planner.py`, then `train_semantic_planner.py` in the project venv. The latter requires the already downloaded local MiniLM directory. [requirements-training.lock](requirements-training.lock) records the installed environment; it is a version snapshot, not a cross-platform reproducibility guarantee. Data/model hashes accompany the artifacts.

Validation: `python -m unittest discover -q` passed 37 checks. New checks cover exact evidence references, future exclusion, protected overflow, provider-task construction, input validation, uncertainty-sensitive segmentation, cost accounting and curriculum structure. These are software checks, not clinical validation.

## The next model improvement

The first priority is reviewed extraction and contradiction labels, followed by encoder/reranker adaptation. Weak labels from the old reader are a bootstrap, not a path to surpassing its blind spots. The first planner improvement is multi-domain operator prediction trained on executable, reviewed plans. Neither problem is solved by adding more loosely labeled synthetic text.

See [the extensive technical review](research/model-economics/RESEARCH_REPORT.md) and [mathematical design](research/model-economics/MATHEMATICAL_DESIGN.md) for the evidence, compute economics, source limits and research roadmap.
