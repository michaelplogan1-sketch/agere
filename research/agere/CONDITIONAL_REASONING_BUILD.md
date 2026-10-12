# Agere conditional reasoning: implementation record

October 8, 2026. All patient inputs and examples are synthetic. This build adds executable stack components, not newly trained weights or a clinically validated decision system.

## What changed

Agere can now ask which conclusions survive unresolved facts, which facts are sufficient for a conclusion, and which additional observations could settle a question. A separate dependency cache preserves computations and their inputs. An optional information-value calculator compares observations under explicit probability, utility and cost assumptions.

The default reader remains the existing lexicon/rules implementation. Jev, frontier readers and local model experiments remain separate optional components. This build neither changes their acceptance thresholds nor promotes their output to clinical truth.

| Component | Implementation | Working capability |
|---|---|---|
| Conditional decision kernel | `clinical_core/decision_kernel.py` | Enumerates admissible Boolean states under a versioned rule, preserves unknowns, finds a minimum-cardinality supporting fact set and inclusion-minimal sets of questions that resolve the outcome |
| Dependency cache | `clinical_core/dependency_cache.py` | Content-addressed source/operator nodes, explicit dependencies and implementation revisions, scoped reuse, preserved history and affected-result lookup |
| Evidence adapter | `clinical_core/reasoning.py` | Connects saved Agere assessments to the kernel; inherited extracted assertions remain proposals |
| Information value | `clinical_core/information_value.py` | Bayesian expected value of a single additional observation, with caller-supplied distributions, utilities, availability and costs |
| Local API | `clinical_core/service.py` | Read-only `/reasoning` endpoint for a configured saved synthetic run |
| Interactive lab | `build_reasoning_lab.py` | Offline demonstration using outputs precomputed by the actual Python kernel |

## Mathematical meaning

Let `S` be the finite states allowed by a declared policy and constraints, and `K` the accepted dated facts. The possible outcomes are:

`O(K) = { P(s) : s in S and s agrees with K }`.

A conclusion is determined only when this set has one member. No frequency of enumerated states is interpreted as a patient probability. If the set is empty, the supplied inputs are inconsistent with the declared constraints.

A supporting certificate is a smallest subset `C` of `K` for which `O(C) = O(K)` and the outcome is determined. This establishes logical sufficiency under this rule and state space; it does not validate the rule, facts or clinical premise. It is fact-level sufficiency, not a guarantee that a compressed clinical note preserves every important passage.

A resolution set is an inclusion-minimal set of unknown facts whose complete answers determine the outcome for every admissible assignment. Joint sets matter: sometimes neither fact alone settles a question. If explicit acquisition costs and availability are supplied, the kernel identifies a least-cost available sufficient set. This is a static set, not an optimal adaptive sequence of questions.

The optional information-value calculator implements:

`EVSI(j) = sum_y p(y) max_a sum_z p(z|y) U(a,z) - max_a sum_z p(z) U(a,z)`.

It subtracts the declared acquisition cost on the same utility scale. Probabilities and utilities are assumptions supplied by the caller, not learned patient estimates. Independent evaluations of correlated channels must not be added together. The calculator does not fetch records or recommend treatment.

These are established logical and decision-theoretic methods. The Agere research hypothesis is that combining dated healthcare evidence, missingness, bounded decisions and selective computation can improve useful accuracy per total cost. No mathematical novelty or superiority has been demonstrated.

## Actual runs

| Run | Assessments | Operator cache hits | Operators executed | External model calls | Source |
|---|---:|---:|---:|---:|---|
| Sample, initial pass | 661 | 0 | 1,322 | 0 | `artifacts/agere-reasoning/sample.json` |
| Hardest world, after sample | 3,914 | 1,322 | 6,506 | 0 | `artifacts/agere-reasoning/hard16.json` |
| Hardest world, repeated | 3,914 | 7,828 | 0 | 0 | `artifacts/agere-reasoning/hard16-warm.json` |

Each assessment uses a proposal operator and a decision-analysis operator. These are cache operation counts, not avoided API tokens, financial savings or clinical benefits. Source hashing, SQLite access and output serialization still execute on a warm run.

All assessments remain unresolved in this new conditional layer. The current adapter does not establish reviewed eligibility, current treatment, an appropriate exception or an access barrier merely from inherited extraction labels. The original evidence assessments, benchmark outputs and insight results remain separate and unchanged.

The engineering fixture in `artifacts/agere-reasoning/dependency-audit.json` starts with two independent case computations. Changing a fact in one case raises total computations to three and reuses the other case once. Prior nodes remain accessible. This demonstrates dependency reuse, not a clinical accuracy result.

## Try the working component

Open [the interactive reasoning lab](../../artifacts/agere-reasoning/lab.html). Its 81 precomputed combinations use explicitly fictional assumptions. Select unknown, assume no or assume yes for each fact to see possible outcomes and supporting facts. These controls do not submit patient reviews or create training labels.

```sh
.venv/bin/python -m clinical_core.reasoning \
  --run artifacts/agere/hard16-run.json \
  --database artifacts/agere-reasoning/verified-dependencies.sqlite3 \
  --output artifacts/agere-reasoning/hard16-warm.json

.venv/bin/python -m clinical_core.experimental information-value \
  artifacts/agere-reasoning/information-assumptions.json

.venv/bin/python -m clinical_core.service \
  --reasoning-run artifacts/agere-reasoning/hard16-warm.json
```

The direct Python `analyze_case` API and CLI `--case` accept an explicit policy, dated observations, cutoff and optional acquisition assumptions. See [the upstream evidence contract](UPSTREAM_EVIDENCE_CONTRACT.md).

## Verification and boundaries

The full local suite passed 79 tests. New coverage includes unknowns, temporal exclusions, contradictory constraints, sufficient certificates, joint resolution sets, monotonic knowledge, policy changes, patient-scope isolation, cache tampering, failed computations, saved API responses and information-value assumptions. The lab's generated JavaScript passed `node --check`. Browser rendering was not verified because the browser tool rejected local-file preview; no alternate preview route was attempted.

The kernel is deliberately bounded to eight Boolean facts and 128 expression nodes. It is not a general clinical ontology reasoner or a CMS/commercial-contract compiler. Source identifiers and review status are caller-supplied; this local layer does not authenticate them. Cache scope labels are not production access control. Dependency recomputation occurs when the resolver is called with updated inputs; there is no background scheduler. The legacy engine still has its own run cache.

No generator, original world feed, employer system, paid API or model weights were changed. The separate model-independent review queue still needs actual reviewed labels before its exports can support new supervised training.

## Next integration

Use richer dated synthetic evidence to exercise eligibility, current treatment, source coverage and exceptions without leaking hidden truth into inference. Then connect reviewed evidence targets to a compact encoder/decision specialist and measure the entire pipeline against the existing reader and frontier-only comparison. The observation-process model, causal intervention estimation, conformal calibration and tensor interaction experiments remain research candidates, not implemented capabilities of this layer.
