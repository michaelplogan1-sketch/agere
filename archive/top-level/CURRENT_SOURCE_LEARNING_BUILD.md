# Current-source learning and question-linked acquisition

October 10, 2026. Implemented in the original-cohort v1.23 runtime. This is source
acquisition and qualification infrastructure, not a clinically validated reader.

The learning lab now uses the current received warehouse. An unresolved question
can send an explicitly marked phrase from a candidate passage into that lab, with
the exact original question, source, patient-family policy and context retained.
The existing statement readers and explicit source-review controls operate on the
saved task. Marking a phrase does not assign labels or resolve the question.

`question_learning.py` rejects protected and outer evaluation patients before
source acquisition, checks received text and Unicode offsets, and stores immutable
tasks. Changed source, question, policy, reader or implementation dependencies
invalidate reuse. Structured retrieval display fields cannot masquerade as received
narrative. The question-triggered lane is separate from the fixed acquisition
sample and makes no prevalence, relevance or retrieval-recall claim.

`distillation_qualification.py` checks a frozen development plan against the
existing student policy before inference or fitting. It reports sample/family
minimums and the best possible zero-error bound. Meeting these structural minimums
does not qualify a model or create supervision. The tool does not impose spending
limits or prohibit explicitly requested exploratory readings.

## Current data and execution

The active warehouse remains 1,614 synthetic original patients, 1,238,686 received
records and 158 source categories. Only the public original roster supplied the
new lineage preparation; no private generator files or hidden labels were read.
Existing original-person partitions and frozen protections were reused.

| Acquisition result | Count |
|---|---:|
| Eligible acquisition-training patients | 1,102 |
| Outer nontraining patients excluded | 460 |
| Frozen protected patients excluded | 52 |
| Received records scanned | 1,238,686 |
| Records excluded by nontraining/protected policy | 393,751 |
| Eligible received text fields | 198,952 |
| Eligible fields without existing annotations | 184,816 |
| Selected tasks / patients / original-person families | 192 / 145 / 145 |
| Selected source categories | 53 |
| Extraction audit / score-independent audit / priority tasks | 48 / 48 / 96 |

The full scan took 78.09 seconds on this local run; it is not a production latency
claim. Unannotated fields remain unassessed and are not confirmed extraction misses.

The fixed native development plan contains 144 exact marked targets in 107
connected components: **98 fitting, 23 calibration, 23 development-validation**,
all inside acquisition training families. Forty-eight unmarked fields remain
unassessed. The plan nominates the installed Kev teacher before acquisition;
nomination is not a quality comparison. No bulk teacher acquisition or fit ran.

The design fails existing qualification minimums: calibration needs at least 30
accepted rows, and even zero errors at full coverage need at least 35 observations
to meet the existing 0.10 empirical upper-error rule. Both assessment splits have
23. These are descriptive development gates, not clinical confidence guarantees.
Do not relax them or rearrange observed examples to obtain a passing result.

## Actual question-to-reader flow

The original medication reassessment question has a saved task for the exact
phrase “reassess symptoms and measurements at the next visit” in an older received
imported note. Browser creation replayed the task prepared in the HTTP check.

- Task: `28b2ab9b826c8bb2911e467604a7d99e5a61483dd291bde56b6297f550bba6b9`
- Candidate: `0ea4cba28330fd10794281cce5087356b33f4fa09919f3d936e13b067b69622b`
- Native reading: `af5343479aee4c429c94ef4f6b7c3243286bad0b182b7e1951206ae27fb421ba`

One local Kev call proposed `patient_affirmed / planned / care_plan`. It consumed
948 local input tokens, 5.28 inference seconds and 31.25 model-load seconds.
Reported peak MLX allocation was 9.53 GB and lifetime process peak RSS 4.60 GB;
these measure different things and are not interchangeable. Exact API replay was
cached. No paid calls or API charges occurred. No human review, reviewed training
label, clinical resolution, fit or model promotion was created. This individual
example is not an accuracy estimate and does not establish the later reassessment.

## Interfaces and verification

- POST `/api/stack/question-learning`: prepare exact candidate phrase task.
- GET `/api/stack/question-learning/preview?candidate_id=…`: family eligibility
  and supported received-text passages.
- GET `/api/stack/question-learning?id=…`: current task and saved reader proposals.
- GET `/api/stack/question-learning`: twelve most recent task statuses.
- GET `/api/stack/current-learning`: current acquisition, source reviews and
  attached model status.
- GET `/api/stack/learning-qualification`: fixed-design minimums and unknowns.
- CLI: `build_source_distillation.py qualify-design`.

119 focused tests pass, including 11 question-learning and five qualification
tests. The broader suite covers existing learning, semantic readers/students,
source distillation, original lineage, evidence candidates, timing and answers.
Rendered scripts pass Node syntax checks. Actual HTTP checks verified replay,
invalid/forged input rejection, unchanged questions, saved local reading reuse
and current qualification. Browser checks verified creation, navigation, reader
execution and displayed qualification, with no console errors. No review form
was submitted.

Receipts are in `artifacts/agere-current-source-learning-v1/`, including the
acquisition lineage/queue, native plan, design qualification, task, reading,
HTTP/browser evidence, screenshot, tests and implementation receipt.

## Preservation and remaining work

Twenty-seven protected prior engine/catalog files and the active configuration
match prior checksums. Five earlier paid answers still inspect as current; the
original pending account receipt remains current. Historical v1.18 acquisition
files still match their manifest. Their reader identity is stale and their models
remain historical shadow-only results; no old qualification was transferred to
v1.23. The 19 separate Documents research Markdown files match the reconciliation
index. No generator, frozen cohort, source research or employer environment edits.

Next, prepare a larger separately versioned learning design with sufficient
assessment groups before acquiring labels. Preserve the current fixed sample,
question task and old experiment artifacts; support multiple acquisition manifests
if necessary rather than silently replacing dependencies. Keep machine imitation
separate from reviewed supervision, and account for duplicate templates and
patient-family components across development work. Broader source extraction,
retrieval relevance, representative clinical validation, quality-based routing,
population/VBC reasoning, prediction/discovery and portable full-stack acceptance
remain incomplete. No new source-learning model is qualified.

The earlier payload-specific approval request for the separate 17-record Claude
account interpretation remains pending. This run did not retry or bypass it.
Spending is uncapped. Owned server: PID 87805, tool session 43474, port 8885.
