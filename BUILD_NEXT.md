# Agere: the first learned clinical workflow

October 8, 2026. Local synthetic research only. This supplements
`AGERE_CONNECTED_STACK.md`; it does not replace its implementation limitations.

## The first deliverable

Make one complete question dependable: what do the supplied heart-failure
medication records establish, what remains unknown, and which review or
information-processing step follows? Start with treatment mentions, outside
fills, appropriate-hold evidence, access barriers and conflicting timelines.
Do not turn an absent claim into absent treatment. This first learned component
classifies evidence-review states; it does not establish eligibility, appropriate
prescribing, maximum tolerated dose, adherence or intervention benefit.

The connected source ledger, observation workflow, conditional reasoning,
packet selector and review UI already exist. The new work is the path from
attributed reviewed targets to reproducible model fitting.

## Added in this build

`clinical_core/reviewed_training.py` consumes the existing immutable review
export and checks it against a read-only snapshot of its review database. It
rejects altered exports, superseded/excluded reviews, development-world or
family-split violations, exact feature duplicates, unsupported record fields
and future-arrival evidence. Reviewer attribution is not credential verification.

The first comparator is character TF-IDF plus balanced logistic regression.
It fits on training families only, retains complete allowlisted text without
context truncation, and reports agreement on calibration families. Vocabulary
features are bounded. Targets, support IDs and reviewer information are not
model inputs. The classifier does not learn supporting spans or confidence
calibration. Output remains outside the production runtime.

This comparator is deliberately a measurable baseline for the proposed
BioClinical ModernBERT specialist and joint patient/observation model, not the
final Agere architecture. No candidate weights are downloaded. No provider
requests are sent. Unit-test training uses explicitly fictional temporary
fixtures; it does not create reviewed clinical targets.

## Run it

From `/Users/mikel/clinical-insights-local`:

```sh
.venv/bin/python -m clinical_core.reviewed_training \
  --dataset artifacts/agere-stack/reviewed-export \
  --database artifacts/agere-stack/development-reviews.sqlite3 \
  --check
```

The current export has no reviewed rows. Exit code 2 with `status: blocked` is
the expected result. Structural readiness requires training examples from at
least two states, a separate reviewed calibration partition and no calibration
state absent from training. These are execution prerequisites, not statistical
sample-size or clinical-validity thresholds.

Open `artifacts/agere-stack/training-review.html`, review the evidence and save
attributed review records. Import that real review file using the existing
workflow. Replace the path below with the actual downloaded review file:

```sh
.venv/bin/python -m clinical_core.learning \
  --database artifacts/agere-stack/development-reviews.sqlite3 \
  import-reviews /absolute/path/to/downloaded-reviews.jsonl

.venv/bin/python -m clinical_core.learning \
  --database artifacts/agere-stack/development-reviews.sqlite3 \
  export artifacts/agere-stack/reviewed-export-v2

.venv/bin/python -m clinical_core.reviewed_training \
  --dataset artifacts/agere-stack/reviewed-export-v2 \
  --database artifacts/agere-stack/development-reviews.sqlite3 \
  --output artifacts/agere-trained/reviewed-state-v1
```

Exports and model outputs require new directories. If a review is corrected,
export a new version before fitting. The model card records task IDs, file
identity, dependency versions, calibration agreement, local elapsed time and
model hash. Local operating cost is not inferred from zero API spend. The
serialized model is a trusted-local research artifact, not a file to load from
an untrusted source.

## Build sequence and acceptance evidence

1. **Evidence labels.** Review the existing development queue and expand it
   across clinical subjects, negation, time, exceptions, outside care and source
   failures. Keep training/calibration families separate. Add span and temporal
   relation labels for the encoder; the current state labels alone do not train
   every extraction task. The other chat owns changes to the synthetic generator.
2. **Clinical specialist.** Compare the baseline with a compact clinical encoder
   under the same reviewed inputs. Report consequential errors, unsupported
   findings, unresolved coverage and subgroup/source transfer. Choose numerical
   acceptance thresholds before inspecting final evaluation results.
3. **Joint patient/observation model.** Use observed-only inference features and
   separately stored synthetic supervision for clinical state versus recording
   mechanisms. Compare simple missingness-aware models and state-space models
   before a complex architecture. Preserve observationally indistinguishable
   explanations rather than forcing an individual answer.
4. **Selective model and document routing.** Compare Jev with Decider/Lev/d1 and
   frontier routes on matched evidence. Connect the selected OCR runtime to the
   existing document envelope. Measure acquisition, failed calls, inference,
   review burden and amortized local infrastructure; token counts alone are not
   a total cost comparison. License/hardware review precedes deployment.
5. **Broader ontology and policy operators.** Add terminology/unit normalization,
   identity resolution, effective-dated reviewed policy compilation and valid
   cohort denominators. No employer ontology or TextQL assets are available here.
6. **Discovery and intervention effects.** Add population/source drift monitoring
   before promoting patterns. Causal and constrained-allocation operators need
   longitudinal outcomes, patient goals and appropriate assumptions. Test tensors
   and algorithm search as research candidates, not mandatory dependencies.
7. **Execution agents, later.** Produce reviewable action contracts containing
   evidence, uncertainty, permissions, approvals and verifiable completion.
   Agent tool execution remains outside the current build.

## Sharing the proposed architecture

`artifacts/agere-share/agere-full-stack.png` covers the ten connected/proposed
layers, learning/economics and future execution. The companion
`agere-models-and-mathematics.png` explains model roles and research methods.
`artifacts/agere-share/expanded-image-prompts.txt` preserves the generation and
correction prompts. Both use the built-in image-generation tool.

These are proposed-architecture posters, not deployment receipts. Candidate
model names represent alternatives for specific tasks, not a running ensemble.
The potential Agere contribution is the joint composition of patient state,
source visibility, decisive evidence and information acquisition. The individual
methods are established prior art; uniqueness and comparative benefit are not
proven by the diagrams. Training, calibration and full-cost measurement shown
as planned expansions are distinct from the existing weak selector experiments.
