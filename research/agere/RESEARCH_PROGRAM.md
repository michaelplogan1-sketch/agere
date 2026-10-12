# Agere research program: beyond model selection

October 8, 2026. Restores the full scope of the user's research request. Selecting pretrained models is an enabling workstream, not the product research objective. All Agere patient examples remain synthetic.

## The central research question

Can a system learn patient state, the process that records it, decision-relevant evidence and selective information acquisition together, producing better supported population findings at lower total cost?

The clinical encoder supplies representations. A decision provider supplies bounded interpretations. Neither replaces the mathematical work below. We have implemented several supporting primitives, but have not trained the joint Agere model or established a novel architecture's superiority.

## Research tracks and actual status

| Origin / technique | Agere research use | Status |
|---|---|---|
| Astronomy: inverse problems, uncertainty-weighted segmentation, simulation-based inference | Distinguish changing physiology from assay, source and sampling changes; recover latent states under controlled simulator assumptions | Gaussian Bayesian Blocks primitive implemented; joint observation/state model not fitted |
| Weather forecasting and sensor fusion: latent states and multiple observation channels | Reconcile claims, labs, pharmacy and text while maintaining source-specific uncertainty | Dated assertions and source coverage implemented; learned fusion remains proposed |
| Information theory: bottleneck and sufficient representations | Select the least expensive evidence that preserves a decision, including exceptions and contradictions | Weak relevance selector exists; bounded logical fact certificates exist; learned decision-preserving text selection does not |
| Control/decision theory: active sensing and value of information | Choose which missing record or computation could change the answer | Prior-free resolution sets and assumption-based information-value calculator implemented; learned acquisition policy absent |
| Software build systems and formal reasoning: dependency graphs and executable constraints | Recompute only affected results; verify conclusions under explicit rules and premises | New reasoning-layer DAG and bounded Boolean kernel implemented; general clinical/payer compiler absent |
| Manufacturing and reliability: CUSUM/EWMA, change detection and process conformance | Detect source outages or genuine site/pathway deterioration rather than promote measurement artifacts | Reviewed candidates, no fitted population monitoring component |
| Econometrics and operations research: causal effects and constrained allocation | Distinguish risk from potential intervention benefit; account for capacity and patient preference in recommendations | Research stage; observational associations are not causal evidence |
| Quantum-inspired classical ML: tensor-train interactions | Efficiently represent disease, therapy, time and source-quality interactions | Method sections reviewed; no tensor model trained or advantage demonstrated |
| Automated algorithm search | Search retrieval, partitioning or routing procedures under independent quality constraints | Research pattern reviewed; no autonomous algorithm-discovery run |
| Actuarial claims development | Separate reporting maturity from changing utilization/cost | Lead only; substantial methods review still needed |

Scope and primary references: [technical source register](../model-economics/SOURCES.md), [equations](../model-economics/MATHEMATICAL_DESIGN.md), [VBC and adjacent fields](../vbc-landscape/RESEARCH_REPORT.md), [implemented conditional layer](CONDITIONAL_REASONING_BUILD.md). Several prior sources were abstracts or directories only. These do not constitute an exhaustive review of physics or all relevant prior art.

## First Agere-specific prototype: patient state and recording state

Start with a small interpretable joint model before a deep graph architecture. Give it two distinct latent variables: patient/care state and source-observation state. Observed values, missingness, collection times, arrival times and lineage are inputs. A proposed target is:

`p(patient_state, source_state | observations_available_by_cutoff, coverage, event_times, lineage)`.

The task is not to fill every blank. It is to distinguish explanations where evidence permits it and retain an unresolved set where it does not. Clinical state is not identifiable from arbitrary missing records without assumptions, useful anchors or further observations.

Example: apparent medication discontinuation might be a real change, a pharmacy-channel outage, outside supply or a stale copied list. Source-level patterns across patients can support an outage hypothesis, but an outage does not prove an individual's treatment status. A latent model must preserve this distinction.

Training requires observed-only inputs plus separately stored simulator supervision. Use factual event labels, reviewed evidence labels and known synthetic source failures for their respective targets. If two hidden realities produce indistinguishable observed inputs, their supervised target should express uncertainty or a distribution justified by the simulation, not a forced individual truth. Do not expose oracle fields or future arrivals at inference.

Compare with the existing rules, logistic/boosted-tree models with missingness indicators and a conventional state-space model. A complex joint model earns inclusion only if it improves held-out source/time/family transfer, calibration and decision usefulness at acceptable cost.

## Second prototype: evidence selection trained for consequences

Use encoder representations to choose a source-grounded evidence subset. Optimize a proposed task loss that distinguishes wrong subject/time, missed exceptions, unsupported conclusions and unresolved cases, with a separately measured computation budget. Choose tradeoffs on development data; do not silently price clinical harm in arbitrary dollar units.

The existing logical kernel can say which accepted facts suffice under a bounded rule. Use that to supervise which facts matter, then connect facts back to exact source passages. This does not make a model-selected passage logically sufficient: extraction correctness and premise validity remain separate checks.

A learning example should distinguish a redundant positive mention from an answer-changing exception. Keep raw evidence recoverable and test counterfactual changes, retrieval omissions and shifted note styles. Selection must never turn an omitted exception into a confident negative fact.

## Third prototype: learn where to spend effort

The existing information-value calculator supplies the decision-theoretic structure. Its priors, utilities, likelihoods and costs are currently declared assumptions. The learning project is to estimate useful observation reliability and task improvement from recorded outcomes, with uncertainty and source availability explicit.

Candidate operations include retrieving an authorized outside record, expanding a passage window, running a specialized classifier, applying code or escalating for review. Missing external data can remain missing regardless of how many models reread the local chart. Choosing no further computation is a valid result when none has demonstrated value.

Track full cost, including retrieval, inference, failed attempts, review, updates and amortized local infrastructure. Distinguish reduction in logical possibilities from expected clinical benefit; they are different targets.

## Prior art added in this follow-up

Missingness-aware healthcare modeling already exists. **GRU-D** uses masks/time intervals and learned decay for incomplete time series. It is a relevant baseline, not a new Agere invention. This follow-up reviewed its abstract/search-visible method summary; implementation should follow a full methods review. [Original paper](https://arxiv.org/abs/1606.01865)

**Neural controlled differential equations** provide a continuous-time model for irregular, partially observed sequences. The online-prediction follow-up highlights that interpolation can accidentally use future information. This follow-up reviewed abstracts and the author's repository description; neither model was run. Arrival-aware, prospective interpolation would be required for Agere. [Original paper](https://arxiv.org/abs/2005.08926), [online-prediction paper](https://arxiv.org/abs/2106.11028), [author code](https://github.com/patrick-kidger/NeuralCDE)

Quantum-inspired tensor-network classifiers are also established prior art. Their role here is a bounded-rank classical comparison, not evidence that quantum terminology improves clinical learning. [Original method](https://arxiv.org/html/1605.05775v2)

CUSUM is an established industrial process-monitoring technique. Any healthcare application needs appropriate denominators, case mix and source-state checks. [NIST method](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc323.htm)

## Execution order and ownership boundaries

Build the evidence specialist and joint state/observation prototype as complementary workstreams. The other chat owns synthetic-world improvements; this stack work defines consumable contracts without editing that generator. Reviewed labels and versioned data remain common inputs.

Then train decision-preserving selection and information acquisition. Add population process monitoring and intervention-effect estimation when required longitudinal, outcome and capacity data exist. Keep tensor interactions and bounded algorithm search as explicit experiments with simple comparators rather than default dependencies.

The planned output is a reusable learned component plus source-grounded patient/population examples, not merely another scenario catalog. Evaluation exists to test the trained component and prevent self-deception. Novelty requires a more complete prior-art search; superiority requires comparative results. We can pursue original work without claiming that established equations have never been used before.
