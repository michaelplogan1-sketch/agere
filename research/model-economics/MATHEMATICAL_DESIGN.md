# Mathematical design: evidence, uncertainty and compute

October 8, 2026. Established methods are attributed below. Their proposed healthcare composition is a research hypothesis, not a novel theorem or a validated clinical algorithm. All patient examples and experiments are synthetic.

## 1. Model the patient separately from how the patient is observed

Let `z_t` be latent patient state, `o_(s,t)` an observation from source `s`, `r_(s,t)` its observation indicator, and `c_t` recorded context. A possible generative factorization is:

`p(z,o,r | c) = p(z_0) × product_t p(z_t | z_(t−1),c_t) × product_(s,t) p(r_(s,t) | z_t,c_t,s) × p(o_(s,t) | z_t,c_t,s)^(r_(s,t))`.

This is a proposed model specification. It exposes assumptions that a text-only answer often hides: source reliability, changing state, and informative missingness. It does not identify these distributions from sparse records without additional assumptions or supervision. Copy-forward observations are dependent and require a shared provenance or duplication factor rather than independent multiplication of confidence.

Potential implementation: a constrained state-space or factor-graph model with explicit source quality and observation-process features. Compare with simpler imputation-plus-tree models. Do not infer absence of therapy from absent claims, or interpret unidentifiable missingness parameters as known facts. Separate event time from arrival time so retrospective knowledge cannot enter a prospective prediction.

The astrophysics connection is inference under noisy, incomplete observation; [simulation-based inference](https://arxiv.org/abs/1911.01429) offers an experimental framework for known generators. Clinical physiology and documentation require their own assumptions.

## 2. Detect a measurement change with stated uncertainty

For a block B of observations with independent known Gaussian standard deviations:

`W_B = sum_(i in B) 1/sigma_i²`

`S_B = sum_(i in B) x_i/sigma_i²`

`fitness(B) = S_B² / (2 W_B)`.

The dynamic program maximizes `sum_B fitness(B) − lambda × number_of_blocks`. The block estimate is `S_B/W_B` and its standard error is `sqrt(1/W_B)`. This is the Gaussian measurement fitness described in [Scargle's Bayesian Blocks work](https://arxiv.org/abs/1207.5578) and [Astropy's PointMeasures implementation](https://docs.astropy.org/en/stable/_modules/astropy/stats/bayesian_blocks.html).

Implemented in `clinical_core/signal_math.py`. It uses bounded quadratic computation, rejects invalid times/errors and returns no diagnosis. The default penalty is an engineering setting, not a calibrated clinical false-alert rate. Observation times determine order and reported boundaries; the implemented fitness does not model the timing process. Real assay errors are not supplied by our current feed. Assuming them for patient care would need justification.

## 3. Compress for the question's decision, not for textual similarity

The established [information-bottleneck objective](https://arxiv.org/html/physics/0004057) is `I(X;Z) − beta I(Z;Y)`: reduce the representation's information about the input while preserving information about a specified target. It requires a meaningful target and a suitable estimate of its relationship to the input.

For this product, an implementable surrogate research objective could be:

`L = task_loss + alpha × missed_exception_loss + beta × temporal_or_subject_error + gamma × unsupported_claim_loss + eta × measured_compute_cost`.

These terms must be learned/evaluated against reviewed labels, not a second LLM's unsupported preference. A synthetic oracle can supervise controlled mechanisms; it cannot certify real-world medical correctness. Costs need normalization and a chosen tradeoff, not arbitrary sums of dollars and clinical harm.

The present selector only learns weak relevance. Its greedy packet utility includes relevance, query overlap, source diversity and token cost, with protected lexical spans. It does NOT optimize the objective above and provides no decision-preservation guarantee. Compression remains experimental after missing evidence in the difficult-world sample.

A stronger future implementation will retain minimal sufficient evidence *for each typed question*, with contradiction and exception channels. It must abstain when there is no basis to claim sufficiency. A full chart remains recoverable; a bounded packet is a view, not the patient record.

Implementation update: `clinical_core/decision_kernel.py` now finds a minimum-cardinality sufficient set of accepted Boolean facts for a bounded declared program. This is conditional logical sufficiency at the fact layer, not trained text compression or proof that the facts are clinically correct. See [the build record](../agere/CONDITIONAL_REASONING_BUILD.md).

## 4. Buy more information only when it could improve a decision

For possible additional observation `Y_j`, current evidence `D`, actions `a` and utility `U`, define:

`EVI(j) = E_(Y_j|D)[max_a E[U(a,z)|D,Y_j]] − max_a E[U(a,z)|D]`.

The proposed policy would acquire an observation when its estimated value exceeds acquisition, compute, delay and review costs under a stated utility scale. The finite calculator is now implemented in `clinical_core/information_value.py`, using explicitly supplied priors, observation likelihoods, utilities and costs. It does not estimate these from patient records or execute acquisitions. This is standard Bayesian decision theory, not a new equation or an assertion that patient benefit can be priced without governance.

For a synthetic medication-gap question, the next useful item may be the outside prescription record rather than a larger LLM. For a contract question, it may be the effective-date appendix. Initially implement a transparent rule-based information-need queue. Learn a cost-aware policy only when observed correctness and downstream outcomes support it.

The conditional kernel also provides prior-free information needs and inclusion-minimal joint resolution sets. Optional declared acquisition costs select a static sufficient set; this does not solve the adaptive acquisition-policy problem. Neither possibility counts nor fictional example priors are calibrated patient probabilities.

## 5. Use symbolic verification where the premises permit it

Borrow the proposal-plus-check pattern from [AlphaGeometry](https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/). Given a proposed program `P`, typed inputs `D`, schema `S` and rule version `V`, require checks of units, joins, cohort denominator, temporal windows, exclusions and provenance before evaluating `P(D)`.

Examples of enforceable invariants: no future-arrival evidence in a prospective run; no duplicate patient contribution where the contract counts people; no measure numerator without denominator membership; no unrecognized unit conversion; no unsupported citation offset. A syntactically valid program with the wrong clinical premise can still be wrong. Human review and semantic validation are distinct from execution correctness.

Implemented subset: the new bounded Boolean kernel checks the program schema, effective interval, accepted-fact arrival/validity and declared logical constraints. Its synthetic workflow is not a medical guideline. Unit-aware arithmetic, denominator validation and general clinical/payer rule compilation remain future work.

## 6. Treat tensor networks as a classical baseline candidate

A tensor-train weight decomposition represents a large interaction tensor as a product of smaller cores. At bounded rank `r` and local feature size `k`, storage can scale approximately as `O(d k r²)` for `d` input dimensions. Representing arbitrary interactions may require ranks that destroy that advantage. See [Stoudenmire and Schwab](https://arxiv.org/html/1605.05775v2).

Potential task: model interactions among structured disease, medication, timing and source-quality features. Compare with logistic regression, boosted trees and conventional low-rank models. Rank, ordering and missingness encoding must be selected using training/calibration only. This proposal needs no quantum processor and has no demonstrated advantage on our corpus.

## 7. Optimize the pipeline under a quality constraint

A candidate policy `pi` determines model, context, retrieval depth and escalation. The engineering problem is:

`min_pi E[cost(pi,D)]`

`subject to verified_quality(pi) ≥ Q_min, severe_error_rate(pi) ≤ E_max, and latency(pi) ≤ L_max`.

The limits are product decisions requiring explicit definitions. Neither our current weak-label recall nor six-fold routing accuracy is a clinical-quality certificate. [AlphaEvolve](https://arxiv.org/abs/2506.13131) motivates searching executable candidate policies with independent evaluators; we have not run AlphaEvolve or built that search here.

Before automatic acceptance, use independent calibration and drift monitoring. [Conformal prediction](https://arxiv.org/abs/2107.07511) is a candidate for uncertainty sets under its assumptions; it does not make every patient's answer correct or preserve coverage under arbitrary site/time shift.

Implemented engineering step: `clinical_core/dependency_cache.py` reuses the new reasoning layer's unchanged source/operator dependencies while retaining prior versions. This avoids repeated operations in measured synthetic runs, but does not establish a lower total cost per clinically correct result. The legacy baseline pipeline has not been migrated to this cache.

## A falsifiable development sequence

First train better evidence labels and a reranker. Then add normalized longitudinal observations and an observation-process model. Then learn when to fetch or escalate. Only after those steps evaluate tensor interaction models or automated algorithm search. At each stage compare with a simpler model, record total cost, and preserve failure cases. A complicated method that adds cost without verified benefit does not enter the deployed stack.
