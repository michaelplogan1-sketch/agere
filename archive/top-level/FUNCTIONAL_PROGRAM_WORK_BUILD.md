# Functional source review → population analysis → persistent work

October 10, 2026. Implemented and verified in the original-cohort v1.23 runtime. This is a source-review workflow, not validated clinical decision support.

The existing falls, mobility and support operator now composes with full-population cohort/set operations and persistent program work. The `functional_review` graph node supports source-review candidates, source availability and source absence. Candidate selection yields 256 matched and 1,358 unresolved patients across the full 1,614-person roster. Failure to nominate does not become a negative clinical finding. Source availability filters describe received data only.

The new **Function and support source review** program assesses the 308 patients with the selected received categories, retains 1,306 unavailable assessments and identifies 256 review candidates. All 256 have persistent local work items. Repeat saves preserve existing items. Source changes reject stale proposals/events; refreshing a changed assessment requires reassessment. Clinical need, preferences, service indication, coverage and execution authority remain separate and unresolved.

The Analysis library now offers both an executable population-to-work graph and exact-patient work preparation from the functional source receipt. The program workbench supports the new program and a direct link to its queue. The existing answer-to-program control includes it, allowing saved machine interpretations to accompany an assessment. Assessment-to-buyer matching uses the existing coverage, participation and readiness wrappers.

## Actual synthetic demonstration

- Population source receipt: `37421dee92cdae5cb293423bcdd9cc31bb36ba61f3652898b00b33b5013cf730` (unchanged).
- Patient `SYN-69283717-971`; functional assessment `5278761a50013cbde1d7e9f5d123415b9bfb49314c520ab340a002a1c9a31ee3` retains four received record references and saved answer `4937dbd744271141f3ed99482cef4fce506c59eb3295096319c6841e524dd9fe`.
- Persistent work item `5aa9d25af843a70adfaed327beb2355d6d014f36dc54136387f648f63d9c87c2` remains `review_needed`. Adding machine context produced an evidence-change event, not a human review; reassessment remains required.
- Fictional buyer `synthetic-functional-review`; plan `d91257239727cb63c2ff25cd06f8394c0aa760f87ea5dc9b75062ab196a144f3`; task `560ae2b3627f98ce001e7bbf3806512ec6d85756e70446ac6f6eaa08e5dfd3ac` is `handoff_prepared_for_review`. Only the local evidence-review option was queued. No home-health enrollment, referral, clinician review, consent, clinical action or external contact occurred.
- Browser verification used P-1003 for an exact-patient repeat save: 0 created, 1 unchanged, 0 changed assessments. The population graph visibly reports 256 matched and 1,358 unresolved.

## Verification and preservation

62 focused tests passed: 12 new functional-program checks plus the existing program-work, graph and functional-review suites. They cover full denominators, unknown membership, exact record provenance, idempotent save/assignment, stale-source rejection, reassessment, empty-scope behavior, source-bound selection, invalid filters, graph recomputation and historical compatibility. Both changed JavaScript surfaces passed syntax checks. The running HTTP and browser flows were exercised.

The first HTTP harness pass correctly received HTTP 400 because the harness copied an output-only coverage `authority` field into an input request. The fixture was corrected to the six supported input fields, and only the unfinished buyer portion resumed. The rejection, subsequent success and exact results are retained in `artifacts/agere-functional-work-v1/`. No failed provider call occurred.

The historical work engine is preserved **byte for byte** as `program_work_v1.py`; the original import path is a version dispatcher. Historical program definitions, proposal identity and assessment behavior remain unchanged. New functional work uses `program_work_current.py` and `functional_programs.py` with their own identities. Existing graph cache keys intentionally change with the new graph implementation. Prior graph results remain stored; exact historical graph-key reuse is not claimed.

67 earlier protected files, all six saved paid answers and the existing functional buyer task remain current. All 19 Markdown files in the separate Documents research corpus are unchanged; no new or missing files were found. The active warehouse configuration, generator and frozen training/evaluation artifacts were preserved.

There were **zero new model requests**, token-count calls or model charges. This implementation uses deterministic source evaluation, persistent state and an existing saved answer. The uncapped ledger remains at 810 validated / 10 failed historical calls and $6.162467524 in priced historical usage estimates; this is not a daily total or invoice.

## Remaining work

Broader respiratory, behavioral and other domain source review, recommendation composition, patient goals and exact benefit/participation verification remain incomplete. Need tags are still operator-selected. The earlier saved model wording around `homebound_assessed:false` remains unvalidated and unchanged. No general clinical accuracy, calibrated current-source predictor, causal benefit, model superiority or real care execution is established.

Work-event actors remain local self-reported identifiers, not authenticated clinician identities. The older operational work contract permits reported program milestones; these do not prove a service indication or care delivery. New functional work in this demonstration remains unreviewed and unassigned. A later bulk reassessment can replace attached machine context under the existing assessment-replacement semantics; the prior immutable assessment and event history are retained. A merge/preservation policy for optional machine attachments should be addressed before a production multi-operator deployment.

The frozen/current work-engine split preserves existing identities but leaves duplicated implementation to maintain. Further program extension should introduce a selected-adapter registry with per-program dependency identities, while preserving these saved functional assessments. Full product acceptance remains false; continue the substantive queue.
