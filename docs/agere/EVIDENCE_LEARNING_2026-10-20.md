# Evidence completeness, dependency validation and checked learning

This G3 increment extends the shared planner, saved-answer workflow and existing learning store. It implements the four priorities from the DAIR research review. It does not change the full product charter, activate latent memory, or establish independent clinical/model qualification.

## Delivered behavior

| Capability | Behavior and integration | Limit |
|---|---|---|
| Evidence completeness | SQLite's compilation authorizer identifies the analysis steps actually used by the final query. A requirement credited to an unused CTE triggers repair; the relaxed path retains it as an explicit unresolved requirement. The answer carries a requirement-level evidence receipt, active/unused steps and received source dependencies. `COUNT(*)` dependencies are retained even when SQLite reports no specific column. | Compiled participation does not prove semantic entailment, correct joins or exhaustive source discovery. The existing source-coverage contracts and interpretation review remain necessary. |
| Dependency validation | Saved answers seal their derivation and observation dependencies. Reuse checks ancestry, planning definitions, feedback, source identity and SQLite WAL changes. Work transactions check again before commit. Deliverable generation and revision check the actual frozen answer/work bundle before publication; changed dependencies produce a rejected artifact with cost receipts. Historical answers remain inspectable. | Checks bracket work across independent stores; they are not one global atomic transaction. Existing source/policy invalidation remains conservative. |
| Prompt optimization | An offline reflective search appends candidate guidance to existing planner/review prompts, then runs the real compiler, executor and review path. Training and validation families are separated; an optional test split is touched only after selection. All candidates use identical call/output ceilings. Failed requests remain in denominators; usage and incomplete cost accounting are explicit. A candidate cannot trade away a previously passing development case for a higher average. | No automatic runtime promotion. Family labels and expected answers require author review. These checks do not establish independent clinical accuracy or superiority over another model. |
| Procedural memory | Immutable lesson revisions, source probes, use records and outcome events live in `learning.sqlite3`. A candidate needs an intact executed trajectory and successful read-only probes. Only the same declared question family, source, policy, fields and feedback can retrieve it. The planner receives lessons as suggestions to verify. Corrections and failed semantic reviews quarantine a revision; a replacement must be proposed and grounded again. Saved answers/cache entries retain lesson identity; generalized shapes cannot silently shed it. | Initial curation/revision is explicit through the development tool. The system does not autonomously rewrite lessons or train weights. Source probes verify declared preconditions, not the truth of arbitrary instructional prose. Recorded successful use is not causal evidence that a lesson improved the answer. |

The existing learning endpoint and interface now include lesson revision and quarantine counts. Provider outages are recorded as unverified outcomes, rather than evidence that a lesson is wrong. Up to three eligible lessons are supplied; each lesson's probe set has a five-second execution budget. Probe SQL uses the existing read-only governor and bounded results. Lessons also bind the exact originating input population: whole warehouse (`None`), empty population and different saved subsets remain distinct. Population-aware probes receive that same scope, and a replacement revision cannot broaden it. Integration review identified and regression-tested this additional constraint before activation.

## Verification

All tests use authored synthetic development fixtures; no protected benchmark, reserved answer, generator truth or real learning-loop corpus was opened or run.

- The first frozen tranche (`04fa8bd`, `c7c204d`, based on `e6fc5e0`) passed 72 checks from a clean checkout. The build owner integrated it separately and owns live activation/control records.
- Subsequent verification includes the real question → compiled analysis → saved answer → simulated work → brief → monitoring path, plus mutation tests for WAL-only changes, source changes during work writes, feedback/work changes during generation, stale revisions, tampered/legacy contexts and retired lessons.
- Optimizer tests exercise a scripted baseline failure, reflective repair, source execution, separated selection/test sets, fixed limits, preserved failures and candidate-only output. These are mechanics tests, not measured hosted-model gains.
- An actual hosted smoke run on three manually authored, disjoint families passed **3/3**: an ED-event threshold, an unfamiliar sensor source and distinct claim service/adjudication clocks. Seven Opus calls had a known API estimate of **$1.129003**. The baseline passed, so the optimizer retained it without generating or promoting gratuitous prompt changes. This small run is not representative accuracy evidence.
- Initial CLI setup attempts exposed missing output-directory creation while persisting transport errors. The directory creation was fixed; failed attempts and unknown accounting were retained separately. No production provider ledger was changed.

The final source hashes, exact regression command, counts, scripted demonstration and hosted receipts are in `artifacts/agere-evidence-learning-v1/implementation-receipt.json`. The dependency-only receipt is in `artifacts/agere-evidence-dependencies-v1/implementation-receipt.json` in the implementation checkout. Current live status and remaining acceptance gaps remain in the build owner's existing control/checkpoint files.

## Development tools

Validate an explicitly authored case file without making provider calls:

```sh
python -m tools.learning_loop.prompt_optimizer --stack DEVELOPMENT_STACK --cases authored-cases.json --out OUTPUT
```

Add `--execute` to run the existing metered hosted integration after configuring its existing `ANTHROPIC_API_KEY` environment. `--max-candidates` and `--max-calls` bound the experiment. The result is an inspection artifact (`optimization.json`, `candidate.json`), never a default-prompt change. Known protected benchmark paths are rejected. The tool does not scan the learning-loop corpus or import model-generated targets.

Cases are JSON objects with `id`, `family`, `split` (`train`, `validation`, optional `test`), `question`, `expected` (executed `value`, `rows` or `patient_ids`) and `provenance: "authored_development"`. Declared families and numeric variants cannot cross splits. Validation is used for selection; it must not be described as an untouched blind evaluation.

Inspect or curate a lesson:

```sh
python -m tools.learning_loop.procedures --stack DEVELOPMENT_STACK inspect
python -m tools.learning_loop.procedures --stack DEVELOPMENT_STACK propose --spec lesson.json
python -m tools.learning_loop.procedures --stack DEVELOPMENT_STACK ground --id LESSON_REVISION
```

A lesson specification contains `context_id`, a bounded `instruction`, `probes` with read-only `query`, `returns` and `expected`, and optional `required_fields` and `supersedes`. A probe must read warehouse evidence; a constant tautology cannot ground a lesson. Grounding makes the revision eligible only while its declared preconditions hold. A quarantined revision cannot simply be reactivated; create a traceable replacement.

## Remaining acceptance

G3 remains active and G5 independent qualification remains open. Broad retrieval sufficiency, current clinical state, tolerance/barrier interpretation, generated-view meaning and representative comparative model performance remain separate acceptance work. This increment removes specific silent-omission/stale-reuse failures and provides checked development learning mechanisms; it is not universal question coverage or a completed clinical product.
