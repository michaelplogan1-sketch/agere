# Independent review response and first corrections

The intended general warehouse MVP is not complete. The supplied review correctly identifies that isolated handlers, tool availability and a synthetic action demonstration do not establish a general question-answering capability. The HF shortcut was an inappropriate response to a whole-warehouse requirement. Its unconditional answered status was wrong.

The supplied review and appendix are preserved in docs/agere/reviews/. The historical economic totals and all clinical row findings have not been independently re-audited in this turn. No paid comparison, benchmark run or rescore occurred.

## Delivered behavior

- The incomplete HF review returns partial with explicit implementation gaps. Positive/full-treatment wording is not claimed by that tool. Historical v1 HF answers remain inspectable but cannot create or advance work or generate new artifacts.
- The deterministic and learned cohort-family routes share a scope rejection check. A positive medication predicate cannot be interpreted by an all-negative gap family. Unsupported composition remains not_understood, not missing data.
- Actual API checking caught a second defect after the first code fix: Qwen rewrote the mixed statin/ACEi/ARB request into a missing-statin request and returned 447 patients. This failed intermediate response is preserved in live-api.json and invalidated for new work. The shared learned-route guard now rejects it before slot inference. This is evidence of the acceptance-policy problem, not proof all routes preserve all constraints.
- The age_latest grounding check now checks its real roster/demographic dependencies; birth date is a fallback when no reported age exists. The actual age-filtered ED query executes instead of falsely reporting that age_latest is absent. Its temporal/clinical definition is unchanged, and this is not an independent clinical validation of the numerical answer.
- Failed turns remain in the audit history without becoming the next prior answer. Supported list/count, PCP/practice/payer grouping and recorded-death exclusion operate on exact saved membership, retain evidence/lineage and preserve partial status. Unsupported edits to a saved cohort refuse instead of rewriting it into a new population. This is a finite transformation vocabulary, not arbitrary conversational understanding.
- New workspace identities exclude question punctuation, elapsed time, cache state and cost; they retain answer content, executed plan, source and answer policy version. Old workspace IDs and work remain preserved. This does not prove arbitrary paraphrases equivalent.
- Review transitions are enforced; blocked/completed/cancelled reviews cannot advance a demo. Duplicate creation with a different owner returns an explicit conflict and rolls back the whole selection. Missing items no longer masquerade as stale revisions. RPM/custom chart-review receipts use appropriate team wording.
- Failed/unpriced answer usage is NULL rather than invented zero. Historical costs remain unreconciled; telemetry labels its known-cost scope. Runtime metadata now reflects configured/available Qwen slot filling and rewrite roles.

## Evidence and deployment

98 focused tests pass, including 12 new independent regressions. One existing test was changed because silently discarding a different owner is now explicitly rejected. Existing empty-slice VBC warnings remain. Node syntax and git diff checks pass.

Actual no-hosted-call API checks: original request partial; positive GDMT request not_understood; mixed polarity not_understood after the shared guard; age query answered; 671-patient diabetes cohort grouped by PCP without expanding after a failed turn; absent conversation context refused. Old HF workspace still contains its three tasks but is invalidated for mutation. Browser inspection confirmed the invalidation notice. Initial failure and final corrections are retained in artifacts/agere-review-corrections-v1/live-api.json.

The :8885 service was down at inspection. Restored from committed source 8eb7306a3eaa40308a452b62c2049536c7efd720, with hosted models disabled, at /Users/mikel/agere-mvp-run. Current PID 31270, exec session 28121; health ready as of 2026-10-20T01:15:30.455215+00:00. This is a timestamped observation, not a claim that the process will remain alive after the host/app exits. The preexisting v2.8 config overlay and data/model symlinks were preserved. The active config remains uncommitted and portability remains an open issue. Recovery reference: b28989f; that historical version has known review defects and is not an accuracy-qualified rollback.

## Remaining work and decision

G3 remains active; G5 remains blocked. No full-product, clinical, model or economics gate is promoted. General constraint accountability, a unified planner, source reconciliation, arbitrary follow-ups, complete prospective per-question cost lineage and fair independent evaluation remain incomplete. The existing multi-Opus live architecture was not validated or replaced in this correction; the restored service cannot make hosted calls.

The next increment must consolidate Stack.compose/economical_planner/together around the existing typed analysis graph, with one original request contract for population, period, predicates and exclusions, grouping, measure, comparison and required evidence. Every route, including cache and learned rewrites, must retain that contract or explicitly decline. Demonstrate new compositions across utilization/readmission, financial/denial and population/provider domains, using independent fixtures and constraint mutation checks. No additional HF-specific handler or model catalog is an acceptable substitute. Reuse existing semantic sources, operators and saved decision/work contexts. The complete ten-area charter remains in the canonical backlog.

Clinical repair must distinguish payer-attributed practice from named PCP, billed specialist encounter from ongoing responsibility, target-dose comparison from maximum tolerated dose, and absent records from absent care. Reading additional sources is necessary; it does not by itself justify those stronger conclusions. The prior 32-row output has not been repaired into a clinically qualified worklist.

Do not use the historic 49/50 or zero-dollar comparison as a supported public performance claim. No new paid comparison is authorized by this repair. Sealed independently validated questions, equal data/tools, cold/warm separation and cost per correct answer remain required before comparative claims.
