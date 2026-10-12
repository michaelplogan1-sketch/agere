# Agere: model decisions, research synthesis and first product build

**Current direction:** [Product direction and implementation priorities](AGERE_PRODUCT_DIRECTION.md)
supersedes the milestone ordering in this historical build record. HF/CKD are
examples within the broader interpretation/discovery/prediction/recommendation
product. Current implementation status is in `AGERE_BUILD_GAPS.md` and receipts.

October 8, 2026. Personal-laptop project; synthetic patients only. No employer ontology or TextQL assets are available here. This is a working research product, not a clinically validated deployment. No new model weights were trained or downloaded and no paid API requests were made for this build.

**Subsequent update:** the [foundation decision](research/agere/FOUNDATION_DECISION.md) broadens the shortlist beyond Qwen-derived models and records the implemented correction-to-training workflow. It supersedes the earlier Kev-versus-Mapika-only first comparison below. BioClinical ModernBERT is now the first compact evidence-adaptation candidate; MedGemma and Liquid d1 provide distinct-family comparisons. No winner is established.

## Product objective

Turn fragmented healthcare evidence into supported patient and population findings, explicit uncertainty and useful next steps, at the lowest measured total cost consistent with the required quality. Distinguish clinical opportunity, data-reconciliation opportunity, appropriate exceptions and unresolved evidence. Patient benefit and provider financial opportunity remain separate outcomes.

The core architecture is **sources → dated assertions + source coverage → patient-state reconciliation → versioned clinical/program operators → population findings → supported action or information request**. Cheap deterministic work should remain deterministic. Models are replaceable specialists behind typed evidence contracts.

## Model decisions

We selected roles and a narrow comparison roster. We have not selected a winning universal local model or trained a healthcare foundation model.

| Component | Decision | Actual status |
|---|---|---|
| Deterministic reader, rules, SQL | Keep as reproducible baseline and enforceable arithmetic | Working default; synthetic lexicon has known blind spots |
| MiniLM + trained classification head | Keep for experimental question routing | Installed; encoder frozen, head trained; proposal quality insufficient for automatic execution |
| TF-IDF/logistic passage selector | Keep as a cheap retrieval baseline | Trained; difficult-world evidence omissions prevent automatic filtering |
| Jev | Retain bounded decision-provider comparison | Typed adapter and small live pilot exist; failures and uncalibrated outputs remain visible |
| Claude | Retain frontier interpretation/teacher/planning comparator | Existing configured adapter; generated labels require review; no automatic clinical acceptance |
| Kev 4B versus Mapika Decider 4B | First local decision-model comparison, after runtime feasibility | Not installed; preserve intended decision head and attention/runtime semantics |
| Qwen3-4B MLX | Keep as an experimental generative-reader control | Installed; earlier small holdout underperformed baseline; not promoted |
| ModernBERT or another compact cross-encoder | Candidate for evidence reranking after reviewed relation labels | Not trained or integrated |
| MedGemma | Optional medical reader/teacher challenger | Not installed or selected as the universal brain |
| lev, Liquid d1, Clef, pplx-decider, OpenAI Decisions | Secondary candidates if an identified task warrants them | Research shortlist, not deployed ensemble |

The [Kev card](https://huggingface.co/jaredpalmer/kev-4b) describes a pointer-head decision model and an Apple Silicon serving path, but its stated Apple memory target exceeds this laptop's previously recorded memory. Feasibility and faithful quantization need validation before installation. [Mapika's card](https://huggingface.co/Mapika/decider-4b) also requires runtime/version scrutiny. A generic chat invocation is not an equivalent implementation of either decision interface. [MedGemma's card](https://huggingface.co/google/medgemma-1.5-4b-it) supports considering it as a task-specific challenger, not treating it as prevalidated for our population workflows.

Use one appropriate route per task; do not call the entire roster on every chart. Preserve an explicit unknown choice. Choose routes using measured errors, total cost, latency and coverage. Presently model confidence is not an automatic acceptance policy. Existing $10 hosted authorization is not a fresh budget for this build; the previous pilot ledger remains authoritative.

Detailed earlier measurements and versions: [training record](TRAINING_BUILD_REPORT.md), [local reader holdout](artifacts/holdout/REPORT.md), [Edge model review](research/agere/EDGE_MODELS.md), [hosted pilot](artifacts/live-provider-pilot/summary.json).

## Mathematical concepts reviewed

| Concept | Proposed job | State today |
|---|---|---|
| Joint latent-state and observation-process models; factor graphs | Separate patient state from missing feeds, recording intensity, copied text and source reliability | Research specification; this build supplies an initial evidence/coverage representation, not a fitted latent model |
| Bayesian Blocks / uncertainty-weighted change points | Identify changes in irregular measurement series with stated measurement uncertainty | Callable Gaussian segmentation primitive implemented; no automatic renal extraction or clinical warning model |
| Information bottleneck / decision-preserving selection | Keep answer-changing evidence, especially exclusions and contradictions, under a compute budget | Proposed learning objective; current relevance selector does not guarantee decision preservation |
| Expected value of information / active sensing | Choose between retrieving another record, running a specialist, escalation or clarification | New transparent information-needs queue; no learned utility or expected-benefit estimates yet |
| Neuro-symbolic proposal and verification | Let models propose mappings/plans; code verifies dates, joins, units and denominator rules | Temporal/provenance checks working; general plan/program compiler remains to build |
| Conformal uncertainty sets and selective prediction | Produce answer sets or abstain under validated assumptions | Research only; not a universal guarantee under source/site drift |
| Tensor-train / low-rank interaction models | Represent structured disease × therapy × time × source-quality interactions efficiently | Proposed classical research comparison; no quantum hardware or demonstrated quantum advantage |
| Causal inference and constrained optimization | Separate high risk from intervention benefit; respect capacity and patient preferences | Later research stage; associations are not intervention effects |
| Bounded algorithm search | Optimize retrieval/routing policies under a fixed quality constraint and compute budget | Proposed research harness; no autonomous self-certification |

The strongest Agere-specific hypothesis combines **patient state, observation quality, decision-relevant evidence and information acquisition**. These mathematical building blocks are established. We have not established novel theory or superiority. See [equations, assumptions and sources](research/model-economics/MATHEMATICAL_DESIGN.md), including [Bayesian Blocks](https://arxiv.org/abs/1207.5578) and [conformal prediction](https://arxiv.org/abs/2107.07511).

## Other fields reviewed

- **Astronomy/astrophysics:** uncertain observations, irregular sampling, change points, Lomb–Scargle periodicity and simulation-based inference. First use is distinguishing measurement patterns from sampling/source artifacts.
- **Weather forecasting:** GraphCast and ensemble-forecasting research suggest multiple scales and observation/state separation. Healthcare documentation incentives require different assumptions.
- **Reliability engineering/SRE:** stale-feed detection, service objectives and persistent data-quality failures. Operational error budgets do not define acceptable clinical harm.
- **Manufacturing statistics:** control charts, CUSUM/EWMA and investigating process changes with stable denominators.
- **Industrial process mining:** detect stalled or divergent observed pathways; missing events and confounding limit interpretation.
- **Aerospace prognostics:** NASA-style prospective evaluation and separating observation time from later outcomes.
- **Econometrics/causal inference:** who might benefit from an intervention, beyond who is likely to be hospitalized.
- **Operations research:** constrained allocation, scheduling and capacity-aware recommendations.
- **Quantum-inspired classical machine learning:** tensor networks as a falsifiable comparison against simpler models.
- **Software engineering and agent systems:** composable tools, reusable corrections and incremental determinism from Every; cache stable work and avoid repeated inference.

Actuarial claims-development methods were identified as a lead, but have not received a complete methods review or implementation. This research does not constitute exhaustive coverage of these disciplines. Source links and review scopes are in the [VBC review](research/vbc-landscape/RESEARCH_REPORT.md) and [technical review](research/model-economics/RESEARCH_REPORT.md).

## Implemented in this build

1. **An append-only assertion store.** SQLite retains content-addressed assertions with source hashes, exact note offsets, antecedent context when available, dates, reader revision and review status. Changed assertions create new records. Automatic correction/supersession resolution is still absent.
2. **Separate care evidence and observability.** Per-concept source declarations carry scope, covered period, when the declaration became known and its provenance. Feed presence does not imply completeness. Declarations are supplied metadata, not independently verified truth.
3. **Arrival-aware input filtering where dates exist.** Optional `available_date` is allowlisted and checked before baseline inference. Later arrivals cannot affect an earlier run. Missing arrival dates remain explicit unknowns; this is not a complete historical reconstruction.
4. **Evidence reconciliation.** Preserve exceptions and barriers that the inherited judge can hide behind a paid-fill rule. A reversal does not imply financial hardship, a fill does not imply ingestion, and a documented hold is not automatically appropriate. Potential time conflicts route to reconciliation.
5. **Information-needs output.** Identify unresolved source coverage and the next fact to clarify. No fabricated outside records, automatic retrieval or unsupported probabilities.
6. **Incremental assessment reuse.** Changed patient evidence, applicable subjects, dates, source coverage, reader or policy implementation invalidate that patient's assessment cache. The baseline engine still reruns for a changed world; this is not a full dependency graph for all computation.
7. **A local product view and read-only API.** Search/filter assessments, open exact evidence and inspect uncertainty. `/agere` serves a configured saved run. Updated clinical question handlers use this assessment contract on newly generated runs. Previously saved corpus answers require regeneration.

Main code: `clinical_core/observation.py`, `runtime.py`, `inputs.py`, `questions.py`, `service.py`, `product_view.py`. Reconciliation currently uses the inherited lexicon even if the benchmark uses the optional MLX reader. It is an explicit baseline that a trained reader can later replace.

## Demonstration and validation

The generated [400-patient synthetic view](artifacts/agere/index.html) and [hard16_llm view](artifacts/agere/hard16.html) are self-contained and make no external requests. The browser's URL policy blocked the local-file preview, so no visual browser inspection is claimed. File generation and payload-escaping tests passed.

| Assessment state | Demo sample | hard16_llm |
|---|---:|---:|
| Insufficient evidence | 272 | 1,421 |
| Care record observed | 321 | 2,169 |
| Reconcile timeline | 16 | 131 |
| Reported outside care | 24 | 38 |
| Documented barrier review | 18 | 91 |
| Documented exception review | 9 | 54 |
| Transaction review | 1 | 10 |

Counts are **patient–subject pairs**, sourced from `agere.states` in [sample output](artifacts/agere/sample-run.json) and [hard-world output](artifacts/agere/hard16-run.json). The hard world contains 4,836 synthetic patients and 3,914 applicable assessments under inherited eligibility heuristics. These are software outputs, not accuracy scores, validated care gaps or proof of patient benefit. The new states have not been independently clinically adjudicated. Separate `*-evaluation.json` outputs continue to score the inherited benchmark only; those scores do not validate the new assessment states.

`python -m unittest discover -q` passes 49 checks, including late-arrival exclusion, copied stale evidence, outside-care distinctions, transaction semantics, scoped coverage, per-patient reuse, preserved versions, API integration and report escaping. No paid calls or new trained weights in this build.

## Next implementation milestones

1. **Reviewer corrections → usable training data.** Create adjudicated assertion/relation labels for current/historical, patient/relative, support/exception/conflict, outside completion and missingness. Preserve family/world separation. Identical visible inputs with different hidden realities must target uncertainty, not an impossible hidden-state guess. Add correction/supersession semantics to the assertion store.
2. **Train the first better evidence specialist.** Adapt a compact encoder/reranker, then compare one feasible native local decision model with Jev and the configured frontier model. Use shared evidence contracts. Promote only improvements demonstrated on separate data; evaluation supports training rather than replacing it.
3. **Finish the HF evidence slice.** Separate prescription, dispense, reported use, holds, dose, tolerance, barriers and time. Extend the synthetic source generator where these facts are absent. No current claim of identifying maximum tolerated dose.
4. **Add kidney trajectories and source-quality modeling.** Normalize units and assays, preserve irregular sampling and known arrival times, then connect the existing mathematical primitive. Train the joint observation-state prototype alongside simpler statistical baselines.
5. **Add composable question and policy operators.** Start with cohort selection, evidence retrieval, rule applicability and missing-input reporting. Compile reviewed CMS/commercial/Medicaid changes into effective-dated rules; avoid relearning changing policies in model weights.
6. **Add cost-aware selection and population discovery.** Learn when additional information or compute changes a decision; measure data processing, inference, hardware and review costs. Add causal and capacity-aware analysis only when its required data and assumptions are available.

The immediate next training target is evidence understanding, not a general healthcare chatbot. The long-term research target is a model that learns what can be concluded, what remains unobserved and which additional observation would be most useful.

## Run

```sh
cd /Users/mikel/clinical-insights-local
.venv/bin/python -m clinical_core --output artifacts/agere/sample-run.json --database artifacts/agere/evidence.sqlite3 --report artifacts/agere/index.html --evaluate
.venv/bin/python -m clinical_core.service --agere-run artifacts/agere/sample-run.json
```

The API binds to loopback at `http://127.0.0.1:8876/agere`. The report is a standalone local HTML artifact; the service does not host its HTML. Run `--help` for a full synthetic world, optional coverage declarations and other outputs.
