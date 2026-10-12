# Agere: protected source distillation

October 9, 2026. All patients, source statements and measurements are synthetic.
The broad Agere stack is still unfinished.

Agere now has an executable path from its received-source learning queue to
versioned machine-supervision datasets, fitted local classifiers and optional
source-bound readings in the live application. A machine suggestion remains a
machine suggestion throughout. No clinician review was manufactured and neither
new classifier was promoted to automatic interpretation.

The active warehouse remains the original-cohort v1.18 connection: 1,614 patients,
1,049,339 received records and 113 source categories. No migration occurred.

## What was built

`clinical_core/agere/clinical/source_distillation.py` freezes targets and source
contexts from the existing protected queue, checks every source again on use,
runs both installed native readers with one model load per batch, journals
failures, and exports immutable teacher-imitation datasets. Resuming a completed
batch does not rerun models or silently retry failures.

Patient, identical-source, normalized whole-field and identical-input links form
connected components. A fixed hash assigns these components to training,
calibration and development-validation subdivisions **inside the acquisition
training partition**. Outer calibration/validation and frozen Kev/d1 families
remain excluded. The plan was saved before the new native readings. Near-duplicate
paraphrases and prior developer/model exposure are not ruled out; this is not an
independent clinical evaluation.

Kev was declared as the imitation teacher before the new readings because it is
an installed native implementation. This is not a finding that Kev is better.
d1 received the same serialized context and question choices. Its disagreements
are retained separately; there is no voting or consensus-label creation.

The existing student fitter now accepts the explicit `machine_teacher_snapshot`
supervision type. It fits character-feature logistic heads and frozen-MiniLM
logistic heads for statement, time and care relationship. No foundation weights
were updated. Dataset, source, teacher, family and code changes invalidate reuse.
The training script now defaults to the active original-cohort connection.

Both students are attached through the actual `Stack.refine_statement` path,
`/api/stack/semantic/read`, the statement workspace and the learning workspace.
Results expose exact target spans, source context, probabilities, compute usage
and review controls. They do not satisfy clinical prerequisites automatically.
`/api/stack/source-distillation` exposes acquisition and partition provenance.

A live-browser check also found and fixed a pre-existing integration regression:
learning/statement pages were importing workbench-only acquisition startup code.
Shared controls now live in `semantic_controls.py`, with page startup kept in
the owning page. All three pages were checked after the repair.

## Executed development results

Sources: `artifacts/agere-source-distillation-v1/acquisition-v2/plan.json`,
`verification.json`, `models/{lexical,minilm}/manifest.json` and
`paired-inputs.json` under the same artifact directory.

| Quantity | Recorded result |
|---|---:|
| Protected queue items / patient families | 96 / 77 |
| Marked source targets / patient families used | 72 / 54 |
| Connected components | 52 |
| Training / calibration / development-validation targets | 44 / 11 / 17 |
| Unmarked fields kept unassessed | 24 |
| Kev completed source readings | 72 |
| d1 completed source readings | 72 |
| Native inference failures after GPU access was available | 0 |
| Targets with at least one native-reader disagreement | 68 / 72 |
| Statement / time / relation disagreements | 45 / 41 / 57 |
| Lexical head exact agreement with Kev on development validation | 6 / 17 |
| MiniLM heads exact agreement with Kev on development validation | 7 / 17 |
| Validation targets accepted by either calibrated gate | 0 |
| New hosted API calls / new human-reviewed labels | 0 / 0 |

The original queue counts are in the connected queue's `run.json`. The 24
unmarked fields are audit candidates, not confirmed retrieval misses. Both native
models received exact target context; there are no adjudicated reference labels
here. Consequently retrieval recall and clinical interpretation error rates are
unmeasured. Native agreement would not establish truth either.

The new heads **did not qualify for automatic acceptance**. Calibration has 11
targets versus the existing minimum of 30 accepted targets, several classes are
absent from fitting, and teacher agreement itself is weak. Neither thresholds nor
partitions were relaxed. Their value at this milestone is a working, inspectable
training/serving path and a concrete failure receipt, not improved clinical
performance or a model winner.

The first sandboxed Kev launch failed before inference because Metal was
unavailable. Its log and original plan are retained. Native execution used the
existing approved local GPU access; no model was downloaded or hosted service
purchased. The subsequent plan version adds explicit native-import failure
accounting and preserves the same targets and partition rule.

## Compute and serving evidence

The recorded native inference totals were 63.316840338520706 seconds for Kev and
42.99261216446757 seconds for d1, excluding model loading. Their native input-token
counts were 66,124 and 67,949. These tokenizer counts are not interchangeable units
of compute, and local hardware/electricity costs are unpriced.

Classifier fitting took 0.07458783313632011 seconds for lexical heads and
2.7170634157955647 seconds for MiniLM heads, including encoder setup in the latter
measurement. Model feature/weight artifacts are 282,145 and 18,734 bytes; the
MiniLM number excludes the existing encoder weights. These are one-run local
measurements, not service-level latency promises.

Both students served an exact source from the development-validation subdivision
through the application API, replayed their cached readings, and left the source
review and prerequisite boundary intact. Neither generated hosted tokens.
`http-verification.json` records the active snapshot and responses.

There are 69 focused checks in `regression.log`, plus the actual source/model
verification and live API/DOM flows. Source changes, teacher-label changes,
protected families, transitive grouping, failed native imports, replay and
non-promotion are exercised. `browser-verification.json` retains the initial page
failure and its resolution. Screenshot tooling was already failing in the prior
milestone; no pixel-layout verification is claimed.

## Reproduce and continue

```sh
.venv/bin/python build_source_distillation.py status
.venv/bin/python verify_source_distillation.py
```

`build_source_distillation.py plan`, `acquire --provider kev|d1` and `freeze`
create fresh versioned acquisition/dataset artifacts. Then
`train_semantic_students.py --machine-dataset <dataset> --output <new-output>
--attach` fits and attaches both shadow readers. Existing artifacts are preserved.
Do not rerun these completed experiments just to repeat the measurements.

Next substantive integration is medication-item evidence in reusable
clinical/program prerequisites and review work. Broader learned interpretation
still needs better task/label coverage and qualified supervision; this acquisition
does not solve that requirement. Any next local-model qualification must use a new
development plan and honest reference labels, leaving the frozen reserved
experiments and outer evaluation families out of fitting. Outcome-safe prediction,
provider/contract accounting and full-stack acceptance also remain on the queue.
