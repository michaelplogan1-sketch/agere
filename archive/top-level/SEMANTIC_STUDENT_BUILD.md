# Agere: local source-skill training connected to the warehouse

October 9, 2026. All records, curriculum examples and results are synthetic.
This completes a bounded training/serving increment, not the proposed whole stack.

## Implemented

`semantic_student.py` fits two economical readers under the existing three-axis
source task: proposition, source-relative time and relation to care. One uses
character TF-IDF and balanced logistic heads. The other uses the installed,
SHA-256-verified MiniLM encoder and balanced logistic heads. MiniLM's foundation
weights remain frozen. These are newly trained task heads, not a new foundation
model or replacements for Jev, Perplexity, Kev, d1 or the default reader.

The existing reviewed-source snapshot path now exposes all three verified
partitions to the training orchestrator. It rechecks the rows against current
source reviews even if someone updates a file checksum. Training sees only train
labels. Temperature and the selective threshold use calibration labels only.
Validation labels are used for the final report. Tests change validation labels
and verify that the weights, temperatures and threshold do not change.

Model storage uses JSON and numerical NPZ with `allow_pickle=False`. Manifests
pin code, reader contract, files, input provenance and the frozen encoder. The
attached manifest digest is checked before replay or inference. Reviewed-snapshot
models also recheck the source review snapshot when used; changed/revoked reviews
require rebuilding. Output remains a source-bound proposal with the same explicit
review controls and exact-result cache as the other statement readers.

The development classifier consumes the marked text and concept, not patient IDs,
split names, labels, review metadata or the packet's date metadata. It requires a
complete received text field. The lexical bound and encoder token limit refuse
oversize inputs without truncating. Date reconciliation, broader ontology
understanding and long-context modeling are not learned by these heads.

`/semantics` now has **Inspect trained local readers**, and statement-reader
controls include both local students. `Stack.refine_statement` and the existing
POST route serve them; GET `/api/stack/semantic-students` exposes their model cards.
The new heads have no automatic clinical or population-state acceptance.

## Actual fitting and results

The source-review workflow currently contains **zero human review revisions**.
No user reviews were invented. To execute the complete fitting path immediately,
`semantic_curriculum.py` provides **558 authored development examples**, with
**186 train, 186 calibration and 186 validation** examples. Each partition has
31 wording families with six entity substitutions. All task labels are represented.
These are related, author-written paraphrase families, not independently sampled
clinical cases. Exact family/input separation does not make them external validation.

Results from `artifacts/agere-semantic-students-v1/run.json` and each model's
`manifest.json` (percentages rounded from stored proportions):

| Reader | All three axes exact | Statement accuracy | Time accuracy | Relation accuracy | Validation rows selected |
|---|---:|---:|---:|---:|---:|
| Lexical classifier | 32.3% | 69.9% | 64.5% | 67.7% | 0 |
| Frozen MiniLM + learned heads | 37.6% | 68.3% | 71.5% | 73.7% | 0 |

Neither achieved the declared calibration selection criteria. Both release
receipts say `shadow_only`; no winner was selected. The reports preserve per-axis
confusions, temperatures, missing training classes, calibration rules and all
validation predictions. Low accuracy is retained, not repaired by tuning on the
reported validation set. A future iteration needs new evaluation families.

The recorded fit runs took 0.0895 seconds for the lexical model and 3.0754 seconds
for MiniLM including encoder loading/feature computation. These are single local
runs, not a latency benchmark. New head/feature artifacts occupy 105,269 and
35,812 bytes respectively; the latter **excludes the shared MiniLM encoder**.
The encoder processed 558 inputs / 16,650 local tokenizer tokens in this run.
Zero hosted API calls were made; local computation is not cost-free.

## Warehouse integration and observed failure

Both models were run on the two existing synthetic source passages, creating four
additional unreviewed readings. There are now **12 saved current-contract readings**
and **zero human review revisions**. Exact replay succeeded for all four. These
two selected passages are an integration demonstration, not a labeled accuracy
sample or the frozen Kev/d1 comparison.

On the dose-increase refusal passage, both proposed `dose_change`. On the passage
that begins “metoprolol succinate held,” followed by uncertain use and a previous
dispense, the lexical head proposed `reported_use` and MiniLM `dispense_record`.
That is an observed failure to retain the target's hold relation. No source review
or clinical state was created from those proposals. This points to context/scope
and relation supervision needs; it does not prove that a particular foundation
model cannot learn the task.

Cold-call and replay timings, source offsets, labels and model provenance are in
`warehouse-integration.json`. All four exact replays took under 0.004 seconds on
this run. Initial model load materially affects cold MiniLM latency; no estimate
of deployment throughput or total operating cost follows from these samples.

Interpretation is measured with the target supplied. These scores do not measure
retrieval recall. The reserved Kev/d1 experiment, its lexicon baseline and the
chart-to-JSON holdout were not used for fitting or this comparison.

## Run it

```sh
.venv/bin/python train_semantic_students.py --authored-development --output artifacts/NEW_OUTPUT --attach

# After explicit reviews and verified lineage produce a ready snapshot:
.venv/bin/python prepare_semantic_learning.py --stack artifacts/agere-population-stack-v3 --family-policy FAMILY_POLICY.json --output artifacts/NEW_REVIEWED_DATASET
.venv/bin/python train_semantic_students.py --reviewed-dataset artifacts/NEW_REVIEWED_DATASET --output artifacts/NEW_TRAINED_OUTPUT --attach
```

Fresh output directories are required. Attaching means optional shadow serving;
it does not promote a model, alter source truth or execute care. Reviewed snapshots
with missing partitions or insufficient training classes fail before fitting.

143 focused checks passed across student training, reviewed learning, source readers,
source-state/interpretation composition, assertions, chart passes and analysis graphs.
The live browser verified the model report, reader choices and exact MiniLM replay.
JavaScript syntax checks and implementation hashes are recorded with the artifacts.

## Next build

Build a larger source-grounded supervision workflow on fresh synthetic patient and
source families. Use specialist proposals for labeling assistance, retaining their
machine provenance, and explicit correction for reviewed examples. Target
multi-event scope, negation, temporal ambiguity, unseen terminology, long passages
and missing-source alternatives. Keep retrieval misses separate from supplied-
context interpretation errors. Fit narrow skills and measure cost per supported
result before enabling an automatic route. The small authored corpus above is a
development smoke corpus, not the training program for the full clinical product.

Continue connecting cross-source clinical state, program/outcome contracts and
broad question composition. Changing clinical guidance, payer terms and program
measures remain versioned knowledge and executable rules outside these weights.
