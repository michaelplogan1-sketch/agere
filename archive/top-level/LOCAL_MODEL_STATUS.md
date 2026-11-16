# Local model integration

## October 9 — bounded Kev/d1 experiment completed

The next experiment specified below has now run with **Kev 4B's trained pointer
head on MLX** and **Liquid d1-3B's native BF16/MPS readout**. Both use immutable
checkpoint revisions and SHA-256 manifests. Read [LOCAL_DECISION_BUILD.md](LOCAL_DECISION_BUILD.md)
and the [complete paired report](artifacts/agere-local-decisions-v1/REPORT.md).

On the existing reserved 20 synthetic patients, fresh lexicon status agreement
was 44/47, Kev 42/47 and d1 39/47 before probability cutoffs and after source/answer
consistency checks. Both models completed 137 requests without runtime failures.
Neither met the frozen calibration acceptance rule; neither replaced the default
reader. No weights were trained and no hosted inference was called.

Retrieval attribution is limited: 5 of 67 bounded cases had exact positive
synthetic reference spans, all 5 were retrieved, and 62 were unscorable for that
attribution. Both models made one category error and selected correct support in
3 of those 5 cases. This is not evidence of perfect retrieval or broad clinical
performance. Calibration and reserved underlying source families do not overlap.
The old reserved cohort has prior developer exposure and is not a new blind test.

The earlier chart-to-JSON result below remains historical evidence about that
specific recipe. **It is not evidence against Kev** or other Qwen-derived decision
heads. Do not tune the next model on the now-observed reserved answers; create a
new versioned training/calibration/evaluation experiment.

## Historical build — October 8, 2026

The local MLX model is installed, connected to the shared reader interface, and
tested on synthetic charts. The rule-based reader remains the default.
No employer data, ontology, TextQL service, or hosted model inference is required.
No model training or healthcare fine-tuning was performed in this milestone.

## Evidence and decision

The frozen Qwen3-4B hybrid was checked once on 20 synthetic patient IDs excluded
from prompt development. Both cohorts come from the same demo world and may share
underlying library templates. This is a development check, not clinical validation.

| Result | Existing lexicon + rules | Qwen + rules |
|---|---:|---:|
| Exact patient/subject status matches | 44 / 47 | 26 / 47 |
| Patient/subject pairs sent to review | 0 | 20 |
| Worklist precision | 0.857 | 0.667 |
| Worklist recall | 1.0 | 0.167 |

Every number above is in `artifacts/holdout/comparison.json` and the corresponding
`*-evaluation.json` files. Review counts are patient/subject pairs, not people.
Exact matches count abstentions as non-matches. Precision and recall use the
inherited benchmark scorer and are specific to these synthetic data and heuristics.

The Qwen run took 105.7277666660957 seconds, used 20 local model calls, and reported
3.499145584 GB peak MLX memory. Seven chart extractions failed schema validation;
they were routed to review. These are runtime-memory measurements, not total
machine RAM or a dollar-cost estimate. The files retain all raw outputs and errors.

The experiment establishes that local GPU inference and the evaluation plumbing
work. It does **not** justify replacing the current reader or advertising superior
healthcare performance. Two earlier prompt variants and their unsuccessful results
are retained in `artifacts/pilot-v1/` and `artifacts/pilot-v2/`.

## What is implemented

- Isolated Python environment and pinned public quantized model files with hashes.
- Offline local model loading, no remote code or hosted fallback.
- Source-referenced evidence, strict schema validation, explicit review states.
- Content-addressed extraction cache and whole-run cache.
- Recorded raw responses, latency, tokens, memory and version identifiers.
- Label-independent cohort selection and separately loaded synthetic scoring.
- Sixteen local software tests and five existing engine tests passing.

## Next experiment

Replace the broad chart-to-JSON task with bounded classification of retrieved
evidence for a single subject. Enforce the output choices in decoding, then test
negation, the correct drug, the patient versus family, temporal changes, and source
entailment. Schema validity alone is insufficient. Measure retrieval misses
separately from interpretation errors.

Use new training/development examples for that work; do not tune against this
reserved evaluation cohort. Before claiming generalization, create a clinician-
reviewed evaluation set split by underlying source patient and new synthetic world,
with real-world validation later. Compare candidate small medical/general models
under the same task and runtime. Train an adapter only after the task and labels
are stable; compare its incremental value against the deterministic baseline.

Only then add selective routing: cheap rules for established cases, a bounded
classifier where it proves useful, and more capable reasoning or review for the
remaining ambiguity. Report automatic coverage, missed cases, errors, latency,
and total operating/review cost together. More model calls are not the objective.

Run instructions are in `README.md`; detailed results are in
`artifacts/holdout/REPORT.md` and `artifacts/pilot/REPORT.md`.
