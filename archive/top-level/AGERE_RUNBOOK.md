# Agere: the runnable local research stack

This build connects rich source ingestion, chronology/identity checks, OCR, clinical operators, trained action specialists, retrieval/reranking, conditional reasoning, mathematical analytics, policy rules and provider routing. It runs on synthetic data on this laptop. It is not a deployed clinical system or evidence of superiority to commercial products.

The user's clinician approval authorizes the discussed clinical definitions and synthetic development. Source-observation facts can enter the native decision workflow as `source_record`; they do not wait for an invented patient-level clinician label. Generator labels remain `unreviewed_synthetic`. Care absent, care outside the visible sources, and insufficient evidence stay distinct.

## Run the connected stack

From `/Users/mikel/clinical-insights-local`:

```bash
.venv/bin/python -m clinical_core.agere run \
  --release /Users/mikel/synthetic-patient-ecosystem/releases/ecosystem-v2.2 \
  --projection /Users/mikel/synthetic-patient-ecosystem/artifacts/current/agere-world \
  --document-extractions artifacts/agere-full/document-extractions.json \
  --output artifacts/agere-full/my-next-run
```

Use a new output directory. The shared content-addressed warehouse, patient/operator cache, encoder cache and provider budget persist across runs. A changed source, cutoff, clinical program, trained artifact or operator implementation invalidates the affected patient result. The legacy HF/A1c branch retains its own dependency DAG. Runtime never opens `truth/`.

The rich operators and cheap trained action head cover the whole roster. The expensive Granite/BGE/BioClinical pass defaults to 12 cases selected across predicted action groups, with an explicit limit up to 100 per run. That sampling is a cost choice; it is not a claim that every chart went through a neural reader. Current base model weights stay frozen; the trained components are task heads and the joint-state prototype.

The full run includes training and calibration families; it is an execution demonstration. The separate held-out audit is the only reported held-out model comparison.

```bash
.venv/bin/python -m clinical_core.agere serve --run artifacts/agere-full/warehouse-run-v4
.venv/bin/python -m clinical_core.agere query --run artifacts/agere-full/warehouse-run-v4 'Who needs kidney specialist review?'
```

The loopback service exposes `/health`, `/api/query?q=...`, and `/api/patient?id=...`. Its trained MiniLM question-domain proposal is informational; approved deterministic handlers return computed screening results. It does not pretend arbitrary language has been compiled into a clinically correct query. There are no agents taking external action.

## Models and selection

`config/agere/models.json` records each role. The default is the learned TF-IDF/logistic action head: it won the current synthetic comparison and is inexpensive. BioClinical-ModernBERT supplies a trained alternative, Granite supplies dense retrieval, BGE reranks passages, and the earlier joint model separates care/channel/documentation hypotheses. Qwen remains an earlier reader experiment, not the production default. MedGemma and Chandra have not demonstrated a reason to replace the current roles; they are candidates, not falsely advertised installed layers.

Jev and Claude were actually called through the new persistent gateway. Perplexity Decider's current typed API contract is implemented and tested offline; this environment has no Perplexity API key. Provider choices are replaceable. Source citations are validated against submitted IDs, but citation existence is not semantic entailment.

`config/agere/pricing.json` contains dated source-linked prices. `provider-budget.sqlite3` imports the original pilot's reservations and maintains the original $10 cap. Failed attempts retain their reservation. Validated requests can replay without paid inference; invalid responses are not cached as successes. The ledger only covers calls through this gateway, not account-wide activity or another chat's independently authorized work. Local machine time, energy, depreciation, storage and review labor are not priced without supplied rates.

## Train and audit

```bash
.venv/bin/python -m clinical_core.agere train \
  --training /Users/mikel/synthetic-patient-ecosystem/artifacts/current/training \
  --output artifacts/agere-full/new-specialists

.venv/bin/python -m clinical_core.agere.evaluation \
  --training /Users/mikel/synthetic-patient-ecosystem/artifacts/current/training \
  --models artifacts/agere-full/new-specialists \
  --output artifacts/agere-full/new-heldout-audit.json
```

Training uses all 7,247 train examples and 1,393 calibration examples; the 1,360 held-out examples are read only in the separate audit. Patient source families do not cross partitions. Training features exclude target/scenario/family fields and source identifiers; ID/name stripping is not a general deidentification product. The BioClinical head uses the latest matched note and document with all their token windows, whereas the cheap lexical head uses all allowed observable record fields. Therefore this measures these specific systems/input policies, not intrinsic model superiority. Tokenization and feature selection receipts make that distinction inspectable.

The current calibration threshold search uses row-level binomial uncertainty and a shared calibration set for temperature, threshold and empirical candidate sets. Correlated variants, multiple-threshold selection and template reuse mean it is not a guaranteed clinical error bound or formal conformal coverage. The runtime requires a singleton empirical candidate set for the local-proposal route. A proposal never becomes proof of a treatment gap or an executed action.

## Documents and source graph

```bash
.venv/bin/python -m clinical_core.agere ocr path/to/synthetic.pdf --output artifacts/extraction.json
```

Native PDFKit extraction and actual Apple Vision OCR are implemented in `native/ExtractDocument.swift`. Pages with fewer than 80 native-text characters use OCR; a page number alone is insufficient. Extracted text, page number, bounding boxes, source hash and OCR scores persist. Vision may require running outside an app sandbox. Mixed image/text pages with substantial native text still need explicit OCR-quality investigation. Existing document text is never replaced by hidden clean-reference text.

The tested PDF contains 18 pages; 5 required OCR. Four document bindings were excluded by the current source snapshot's correction/identity/availability rules, leaving 14 applied extracts. The graph exposes source-to-patient, concept and correction links; it is a local ontology, not Lumeris's unavailable employer ontology.

## Mathematical and cross-industry layers

`clinical_core/agere/knowledge.py` maps the prior research to executable modules: astronomy-inspired Gaussian change points; sensor-fusion joint observation state; information value; manufacturing CUSUM/EWMA; incremental build caching; actuarial runout; process mining; group-cross-fitted AIPW; observed/expected facility comparisons; Bayesian shrinkage; multiple-testing adjustment; MILP allocation; classical tensor trains; and cost-constrained routing.

Run a method on an explicit JSON input object:

```bash
.venv/bin/python -m clinical_core.agere analyze causal scenario.json
.venv/bin/python -m clinical_core.agere analyze facilities outcomes.json
.venv/bin/python -m clinical_core.agere analyze allocation candidates.json
.venv/bin/python -m clinical_core.agere analyze contract contract-and-sites.json
```

Other operators: `shrinkage`, `fdr`, `network`, `coding`, `information-value`, `change-points`. JSON keys match the named Python function arguments. `mathematical-experiments.json` records separate algorithm checks with explicit artificial assumptions, not clinical results. AIPW requires measured confounders and overlap; facility adjustment does not establish best-provider causality; allocation requires supplied benefit/cost values. Tensor-train is classical and was not promoted after its weak minority-class results. These are established methods; their combination is an engineering/research design, not a proven new mathematical invention.

## Programs, measures, and the original questions

`Program` evaluates effective-dated declarative predicates with true/false/unknown behavior. `compile_document` requires exact source-clause spans for every referenced input. Exact quotation is checked; semantic equivalence of a translated rule still needs verification. A rule update does not require retraining base models. The included kidney screen is one referral-review criterion, not a full implementation of KDIGO. Financial screening is not payer eligibility certification.

`question-inventory.json` traces all 236 authored questions to available components and required data. Existing research is indexed by hash and provenance for retrieval/task design; publications are not silently relabeled clinical ground truth or blindly used as weight-training text.

This warehouse cannot supply several requested conclusions: it has no creatinine results, complete sepsis severity/treatment/outcome episodes, signed commercial contract measures, comprehensive network classification, or adequate attributed SNF/PCP outcome cohorts. Their general computation operators are implemented and callable, but the run reports missing input tables. Patient goals and preferences inform review; there is no automatic label of futile care.

## Verification

Run `.venv/bin/python -m unittest discover -p 'test_*.py'`. Tests exercise temporal corrections, identity quarantine, source text boundaries, fill/reversal/rebill distinctions, units, unknown predicates, contract arithmetic, causal overlap, allocation constraints, family/target feature boundaries, persistent budgets, and source-observation acceptance without fabricated human review.

The next substantive work is adversarial/template/site-held-out evaluation and richer outcome/contract data, followed by model improvement where errors justify it. Distributed ingestion, real EHR/payer connectors, enterprise identity, access controls and prospective clinical benefit validation are not claimed complete by this local build.

## Received narrative integration (2026-10-08)

The corrected native narrative run is `artifacts/agere-full/warehouse-narrative-v2`. See `artifacts/agere-narrative-review/README.md` for source-field mappings, exact-citation verification, review proposals and remaining semantic limits. It adds a complete-length index of mapped received text, contextual mention handling and `/api/evidence` without retraining existing model heads. Run `python -m clinical_core.agere search --run artifacts/agere-full/warehouse-narrative-v2 'your question'` for local source passages. Search results are evidence leads, not exhaustive cohorts or clinical answers. The original historical report is unchanged.
