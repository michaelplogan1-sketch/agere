# Shared workflow evaluation and first planner correction

Implemented October 11, 2026. The six-workflow scorecard is available at `http://localhost:8885/stack-evaluation`, from desktop and mobile workspace navigation. This release establishes an executable engineering evaluation and fixes two shared planner contract defects. It does not complete the clinical stack or qualify a model.

## What executes

The evaluator uses actual local application APIs for medication, respiratory, cardiometabolic, functional, appointment and payer questions. Exact requests, responses, errors, timing and implementation manifests are saved. It compares RRF with BGE on identical candidates, received-category inventory with selected evidence, existing statement-reader baselines, natural-language planning with explicit registered-program diagnostics, and service matching with available/unavailable/wrong-plan/unrelated controls. Failed traces are retained without automatic retry.

Two saved interpretations (cardiometabolic and appointment) now pass through the existing answer-origin matching API into internal handoffs. Both preserve the original question, patient and every selected finding. Tags are explicit evaluator selections, not inferred clinical indications. Benefits, appropriateness, preferences, consent and human review remain unconfirmed. Four separate assessment-origin diagnostic tasks are also retained. None contacted a patient or delivered care.

The planner correction supplies the patient/record predicate grammar that was removed from its request, restricts source selection to exact registered field-contract names, and carries the complete source-name enum once when fields are deferred. Runtime validation remains strict; no guessed field, reference or clinical-state binding is accepted. Cache identities change with the corrected code. Old failed requests remain untouched.

The actual catalog with a maximum-length 1,500-character question fits the existing request limit: selector 19,934 bytes, planning 19,108 bytes. These are an offline wire exercise, not a model-quality result. The functional question now executes a patient-restricted cohort, source review and one-candidate internal work proposal. Other questions expose further contract problems.

## Measured findings

| Stage | Result on the six declared development workflows |
|---|---|
| Candidate retrieval | Three minimal category-coverage gaps: medication orders/dispenses, cardiometabolic vitals/labs, and payer coverage/verifications |
| Reranking | Six identical-candidate comparisons; no additional loss of these minimal category anchors. Missing upstream categories remain missing. No clinical relevance claim. |
| Question context | Three lack declared category anchors; medication additionally omits six units and is blocked before interpretation |
| Source interpretation | Two execute, two reject ambiguous pass references, one exceeds the reconciliation request bound, one is blocked by context |
| Existing local readers | Twelve saved same-statement comparisons reopen with identity checks; their four roles and majority/abstain baselines remain separate from the full-question path |
| Natural-language planning | Baseline 0/6 execute; corrected contract 1/6 executes. Five still fail validation. |
| Service matching and handoff | Two answer-linked internal handoffs, two remaining isolated diagnostic handoffs; two lack an upstream diagnostic assessment. No automated indication or eligibility established. |
| Complete evaluated workflows | 0/6 meet all required stages on a connected path |

The remaining planner errors are explicit: missing assertion streams/concepts; selected requirement without a delivering node; report-result used as population membership; unmapped source-state concept/relation; and scoped unmet requirements not retained in the final answer. Do not weaken validators or turn these errors into successful unsupported answers. The first correction improves execution but is not a solved general planner.

The source interpretations remain machine proposals. Exact quotes and complete submitted-unit accounting do not establish clinical correctness, sufficient retrieval or correct cross-pass meaning. The cardiometabolic selection misses received labs/vitals even though its bounded interpretation executes. This is why the scorecard keeps coverage and execution separate.

## Evaluation boundaries

These are six previously inspected original-training development examples, not representative, independent or clinician-adjudicated evaluations. Expectations and fixture service tags are assistant-authored. The first correction uses these cases as development regressions; no frozen evaluation is tuned or refit. Targets never enter runtime evidence. Existing reader comparisons have different exact passages and sometimes different patients; patient overlap alone does not connect their paths. Their earlier scores and limitations remain in `READER_COMPARISON_BUILD.md`; no additional reader quality claim or promotion occurs here.

The connected-handoff extension was declared before those calls in `connected-work-predeclaration.json`; it tests every successful original-question interpretation without substituting failed cases. Baseline scope and planning overlapped on one serialized server, so their wall times include contention and cannot demonstrate a latency improvement.

## Verification and accounting

40 focused checks pass, including exact question/patient scope, negative patient predicates, identical-candidate requirements, matching rejection controls, disconnected-branch detection, saved-answer lineage, and safe report rendering. The real-catalog maximum request check passes. Live HTTP routes and browser navigation, stage details and saved-answer reopening pass. Browser testing found and fixed an overflowing fixed sidebar so the new link is usable at the existing viewport.

All 170 protected files, all 853 prior ledger rows, 382 prior work items/385 events, 13 prior capability tasks/37 events, 360 statement readings, all semantic reviews, 19 Documents research files and 33 historical source routes remain unchanged. New totals are 19 capability tasks/49 events: six synthetic tasks and twelve events. The original v1.23 warehouse remains active (1,614 patients; 1,238,686 records). No source migration, fitting, model promotion, clinical review or external dispatch occurred.

This run made 44 provider requests: 31 validated and 13 failed validation. Estimated API usage is $2.794128, including failed responses; no requests are unpriced in this delta. The estimate uses saved usage and configured rates, not an invoice. The shared ledger remains uncapped. Twelve saved reader inspections and the connected handoffs added no provider calls. Ledger totals: 870 validated / 27 failed. Exact failed requests are preserved and not resent.

## Next substantive work

1. Replace the selected operators' free-form parameter ambiguity with executable typed parameter and input/output contracts drawn from the same runtime registry. Correct the five measured planner failures through that shared boundary; preserve exact unmet requirements and patient scope. Do not repeatedly prompt-tune on these six examples or add unrelated models.
2. Correct source-reference qualification and size-safe reconciliation for new contexts while retaining immutable historical receipts and the 33 old routes. Keep unresolved references explicit and reuse validated source passes.
3. Improve question-driven source coverage before interpreting missingness, then verify answer-linked work with explicit service selection. Keep these development cases as regressions and declare fresh qualification cases before any quality claims.

The ten full-product acceptance areas in `AUTONOMOUS_STACK_QUEUE.md` remain intact and incomplete. This checkpoint is an engineering measurement and first correction, not a replacement for broad product scope.

Evidence: `artifacts/agere-workflow-evaluation-v1/scorecard.json`, `implementation-receipt.json`, `preservation.json`, `usage-accounting.json`, `http-verification.json`, `browser-verification.json`, `focused-tests.log`, `planner-wire-check-v3/verification.json`, immutable `baseline/` and `planner-v3/` traces; declaration `research/agere/workflow-evaluation-v1/plan.json`.
