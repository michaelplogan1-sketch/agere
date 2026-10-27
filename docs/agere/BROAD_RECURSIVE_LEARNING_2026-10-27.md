# Broader offline SLM and recursive learning

The offline learning increment now has a substantially broader authored analytics
curriculum, independent source calculations and a fit-only corrective training
loop. General output-shape and rate checks were repaired for the affected
questions. Academic reading is connected to the existing cited retriever as an
explicit candidate library. G3 and the full ten-area product acceptance remain
open. No default model or runtime was promoted.

## The measured gap and the changes

The previous trained candidate used 520 fitting tasks but only two verification
families; all its verification gains came from one family. More phrasings of those
same structures could not establish broader usefulness. This increment instead
authors 104 structures across 13 domains, separates entire structures before model
outputs are inspected, and changes the underlying received rows for verification.

The domains are patients, condition assertions, measurements, medications,
utilization, appointments, providers, claims, pharmacy, social context, services,
VBC and patient outcomes/resource costs. Eleven condition names cover HF,
diabetes, COPD, asthma, obesity, CAD, hypertension, hyperlipidemia, cancer,
prediabetes and depression. These are analytical exercises, not independently
physician-authored clinical qualification.

| Allocation | Distinct structures | Distinct questions per learner |
| --- | --- | --- |
| Fitting | 65 | 5,200 |
| Calibration | 13 | 39 |
| Verification | 26 | 208 |
| Total | 104 | 5,447 |

The typed-plan and bottled-SQL learners use the same questions with different
code targets. They do not constitute 10,894 independent questions. Duplicated
question wording was discarded. Three explicitly authored received-source
fixtures contain 48, 31 and 57 patients, with repeated measurements and claims,
adjudication history, reversals, canceled appointments, unknown results,
documented reasons, care teams, device/service states, member-months, patient
outcomes and care-activity costs. They do not use the separate generator, hidden
truth, protected question files or historical operational learning stores.

Each reference calculation is independently reconstructed in Python from actual
fixture rows. The SQL target and the compiled native output must agree with that
calculation on all three fixtures. A deliberately wrong SQL reference paired
with a matching wrong target fails this check. All 5,447 targets pass for both
learner forms. That is target verification, not model accuracy.

The shared answer-shape check had interpreted rate denominators, per-patient dose
modifiers, arithmetic multiplication and distinct-record wording as grouping
requests. The correction preserves genuine group/list requests while accepting
these scalar asks. Reversal-field naming now satisfies the existing reversal
literal check, and member-month rate wording still requires executable ratio
arithmetic. Other failures encountered while authoring were ambiguous exercise
wording or incomplete reference predicates; those were corrected as dataset
problems and are not all counted as product defects.

## Actual local training and the recursive loop

The running study uses the pinned local Qwen3-4B base through MLX. Each role has a
declared first pass of 1,300 iterations at batch four, four LoRA layers, rank eight
and learning rate 5e-5. Sequence lengths are derived from complete examples.
The script runs typed planning and then bottled SQL, with up to two predeclared
rounds per role. These allocations are experiment settings, not runtime patient
or spend caps. No provider, external process, download or telemetry is allowed
inside training/qualification.

The first approved fitting example per fitting family probes residual failures.
The corrective pass selects up to eight existing approved fitting examples from
each failing family and two rehearsal examples from each other fitting family.
It does not generate new truth or choose corrections from calibration or
verification failures. Those rows retain their identities and contents.

Warm starts identify an immutable prior candidate, verify its model/base/code
hashes and matching LoRA configuration, and leave runtime pointers untouched.
Each correction is compared against both the original baseline and the parent
candidate. Selection needs additional useful gains with no observed regressions
or accepted wrong results on any source variant. Failed candidates, fit probes,
selection audits and qualification receipts are retained. Existing unfinished
weights cannot be silently overwritten. Completed jobs deduplicate across prior
revisions.

**At this checkpoint training is still running.** There is no completed broader
model comparison or demonstrated recursive accuracy improvement to report yet.
Training loss is not answer accuracy. Results will be written to
`typed-recursive-result.json` and `bottled-recursive-result.json` in the evidence
directory when the sequential study completes. The running script is
`train_study.py`; its declared protocol and log are preserved alongside it.

## Academic reading

[The academic reading report](ACADEMIC_READING_2026-10-27.md) identifies Mike's
reference recording and 28 comparable lecture records. Eighteen additional cards
cover Harvard value-based-care concepts, open text, government references and a
selected ACC slide review. All 46 resources retrieve through the existing cited
knowledge engine in an explicit candidate directory, with their source links.
The operational clinical-rule catalog returns zero rules for this directory.
Metadata, partial reading and clinical adjudication remain distinct.

The local SQL/plan training is supervised by approved source calculations, not
by copying these lectures into weights. Lecture intake can supply attributed
educational concepts, applicability requirements and proposed questions; dated
clinical evidence and appropriate review must support any executable treatment
rule. No claim that the model has learned 28 full lectures is established.

## Verification and preservation

Evidence root: `artifacts/agere-healthcare-learning-2026-10-27/`.
Frozen source fingerprint:
`4e6246a0ff8905c9141b54729e143407707165b190f0c27b74adaaecef5bc293`.

- Final guarded regression run: 520 checks, 516 passed and four optional/data
  skips. Zero provider sends, blocked send attempts or protected-corpus reads.
- Three existing acceptance-file readers remain excluded. Public policy tests
  are retained unchanged; the new analytics tests have a separate filename.
- The three reading-library checks also passed after the final ACC card was
  added, including actual retrieval of all 46 cards and refusal to treat metadata
  as clinical approval or a full-content review. These checks overlap the main
  suite and are not added to its distinct-test total.
- Three-source native target verification and actual local training are separate
  receipts. Scripted model judgments in the regression suite remain fixtures.
- Both running training implementation contracts still match their frozen
  protocol. Other source changes must not invalidate an in-flight comparison.
- The accepted assisted Heartline result, old offline evidence and interrupted
  attempt1 ledger remain preserved. The original dirty source/configuration is
  unchanged. Default runtime and weights remain unpromoted.

Earlier bank versions, smoke runs, failed attempts and the test-selection naming
correction remain in the evidence history. They are not extra independent
questions or additional passing evidence.

## Remaining work and economics

Inspect the completed broader comparisons by domain and family, including
declines, accepted wrong results, regressions, parent comparisons and all local
compute time. No cost per clinically correct automatic answer is available yet.
Provider inference for this offline increment is zero; local computation and
review still cost resources. Public-source research requests are separate from
provider inference and are not represented as zero network activity.

The ordinary complete-note/dose/final-answer/deliverable workflow still awaits
Claude subscription availability and browser/physician acceptance. The prepared
resume command in this evidence root supersedes prior fingerprints. It retains
the 181 validated and five failed attempt1 ledger entries, recomputes changed
requests and keeps local SLMs off for the controlled first continuation.

The previously observed subscription reset is October 28 at 06:00
America/Chicago. No provider availability check, retry, heartbeat or external
Anthropic API fallback was made. A future subscription limit must stop the run.
The sole next action remains in `AGERE_BUILD_CONTROL.json`; this report does not
replace its sequencing or narrow the product charter.
