# MVP completion audit — November 3, 2026

The modular stack is substantially implemented, but the original clinical workflow is unfinished and its current latency is unacceptable for the intended interactive experience. A completed synthetic MVP has not been demonstrated. This audit keeps all ten acceptance areas and the October 11 architecture; the next action remains in `AGERE_BUILD_CONTROL.json`.

Scope: owner review of current product `942ff1f`, existing release evidence, current source, completed clinical runs, and the completion/review code. No new provider run, training, clinical adjudication, security campaign or product edit was performed for this audit. The already-running continuation was allowed to finish and was inspected. Historical passing checks are identified as historical, not rerun or silently transferred.

## What is built

| Capability | Demonstrated behavior | Remaining distinction |
| --- | --- | --- |
| Warehouse/source layer | Received synthetic snapshot has 4,115 patients and 2,396,490 records across 237 streams; prior exhaustive adapter checks reconcile received rows, identity, dates and revisions. | Latest integrated release still needs explicit evidence applicability. This is not a demonstrated production EHR connection. |
| Native computation | SQL, governed definitions, typed plans and analytical operators handle cohorts, denominators, dates and amounts. A November 3 ordinary numeric question returned 795 exact rows, answered, in 100.144 seconds with 12 provider calls. | Exact calculation is established for these cases; broad arbitrary-question competence and interactive latency are not. |
| Narrative reading and provenance | Jev/Perplexity source reading, relation/time/subject bindings, complete source contexts, exact quotes, uncertainty and localized review are connected. Source statement cuts and date/record tuple defects were corrected. | Too much inference is dispatched for the full clinical question. The field relevance screen has a small scoped comparison, not full-workflow qualification. |
| Patient answer composition | Received names are available to the investigator; keyed prose can be attached beside native patient values. A targeted 57-patient profile question produced 114 reviewed text fields and saved/reopened successfully. | The original clinical workflow still does not produce the individual rationale and specific review steps. An available interface is not proof the investigator uses it correctly. |
| Population/program work | Prior two-program evidence retains all 4,115 members, reconciles 112 respiratory and 129 behavioral nominations, and persists 241 internal handoffs/follow-ups. | Nominations are not clinical eligibility or delivered care. Current affected-path compatibility remains open. |
| Finance, contracts and outcomes | Prior source audits reconcile 694,445 financial rows, 43,654 accounts and 60,472 accounting identities; versioned contracts retain attribution/entitlement unknowns. An ordinary financial workflow has demonstrated 22 native actions with zero Claude callbacks. | Opportunity is not realized savings; current release applicability and authentic all-in economics remain separate. |
| Discovery/prediction | Source-linked temporal analysis, time-safe targets, censoring and comparison infrastructure work. Prior discovery checks reconcile 20,432 appointments. | Selected prediction targets have no positive eligible labels. The system correctly declines a risk estimate; no useful trained predictor is established by that engineering pass. |
| Bottling and improvement | Execution traces, exact caches, source/policy guards, feedback quarantine, candidate evaluation and rollback exist. Financial reuse has executed in an ordinary question; a six-action numeric trace was selected. | Latest-code reuse/transfer needs targeted qualification. No clinically qualified full-question bottle or new clinical weights are selected. |
| Local learned models | Qwen adapter studies were completed with measured gains, errors and regressions. One typed round produced 185 correct, 33 wrong and 86 declines out of 304 development cases. | These are development results, not clinical accuracy. No experimental model is promoted; later rounds both gained and lost answers. |
| Application and operations | Saved decisions, internal work, follow-up, on-request deliverables, local startup/restore and recovery have executable evidence. | Latest code must be assessed as one release. Older nine-of-nine engineering checks are scoped, not a current finished-product claim. |

The current clinical path genuinely uses native execution, hosted small-model judgments and frontier reasoning. It is not Claude-only. Installed retrieval/model options are not all selected, and their presence is not evidence of value on this task.

## Findings

### P1: completion handling conflates unfinished work with uncertain verification

`clinical_core/agere/clinical/backstop_agent.py:1478` permits both `answers_fully` and `answers_partially` to enter `unresolved_verification`. `semantic_analyst.py:718–725` suppresses actor escalation on a repeated state in that category. A small offline probe reproduces the classification for incomplete coverage plus uncertain support.

The current continuation generated seven partial drafts and zero `row_prose` rows; five drafts required deterministic quantity-binding repairs. Both source-review rounds reported partial coverage, unknown overall support and 44 settled packets out of 60. The run used only Haiku for the actor and recorded no model escalations. Its own missing list explicitly says patient-by-patient interpretation and row prose were not completed. This confirms a completion-state weakness. It does not establish that changing to a larger model alone would solve the task, or that unresolved clinical facts should be guessed.

Required correction: distinguish requested work not yet performed, source facts that cannot be established, and uncertain machine verification in the existing assertion/state and review path. Preserve native values and genuine unknowns. Continue or route actual unfinished composition without restarting source acquisition; stop repeating the same uncertain evidence check. Test this distinction with retained reviews and ordinary authored state fixtures before provider execution.

### P1: economically poor decomposition remains a product blocker

The preceding original full clinical run took **3,459.554 seconds (57.66 minutes)** and returned partial. It made **6,966 new attempts: 3,357 Jev, 3,480 Perplexity and 129 Claude**. The two nonidentical document contracts expanded to 108,062 statement decisions in 1,725 typed read batches, plus relation and repair work. These are different units; statement decisions are not each a network request. Known API-equivalent cost was $6.144671538 plus one unpriced failed attempt; this is not an invoice or total cost.

The field screen reduced one 23-document test from 52 to 46 calls and 49.061 to 34.104 seconds while retaining the prior nine findings. That is useful scoped evidence, but it does not establish acceptable end-to-end latency. Small providers cannot compensate for excessive decomposition. The remaining work is dependency-driven dispatch and reuse, measured on the same requested information, with no patient/document sampling or weakening of evidence checks.

### P1: current whole-product acceptance lags implementation changes

The November 2 finite matrix has nine engineering passes spanning all ten areas and six connected API workflows. Its reviewed product is `aec778d`; 18 runtime files changed between that product and `942ff1f`. Historical evidence remains valuable, but current compatibility must be shown for affected behavior. The engineering matrix also deliberately permits properly represented unknown outcomes. It must not be used to imply the original clinical question was fully answered.

Required correction: assess one frozen release, reuse compatible evidence, rerun only affected paths, and record an explicit pass, defect or concrete dependency for every promise. Do not use counts of modules, tests or receipts as a completion percentage.

### P2: learning infrastructure and qualified learning gains are different

Exact computation reuse and candidate learning are implemented. Current clinical observations remain ineligible for promotion; the latest run records observation-only learning and no new adapter task. Existing Qwen experiments include errors and regressions. Starting another broad learning campaign would not fix the observed completion defect. Current execution reuse needs a scoped changed-input/rollback/transfer check; further model promotion needs independent suitable evidence.

### Delivery process correction

The October 11 and November 2 reviews already warned about component-level progress outpacing completed workflows. That warning remains applicable. Real defects were fixed, but the owner allowed repeated repairs and increasingly elaborate evidence handling to obscure the unchanged flagship outcome. Reporting incremental engineering as being close to a finished MVP was not justified by those results. The correction is to close the observed workflow defect and make one release decision; no architecture rewrite or additional model catalog follows from this audit.

Review dimensions: correctness has useful source/arithmetic guards but an open completion defect; performance fails the intended interaction model; maintainability has reusable modules but excessive delivery/version bookkeeping; customer security qualification is outside this review and remains deferred under Mike's instruction.

## Latest owned continuation, inspected during this audit

- Returned at 2026-11-03T16:43:17.920976+00:00 after **787.679 seconds (13.13 minutes)**; process is terminal and source is unfrozen.
- New saved context: `ANS-3b16659c143580baecaf0b51`; **57 patients, 57 names, partial/unverified, no individual prose columns**.
- **157 new attempts: 34 Claude Haiku, 68 Jev, 55 Perplexity**. Every returned attempt is priced for API-equivalent accounting: **$0.786483564**. Actual subscription allocation, local compute and human review remain outside that subtotal.
- Exact owned recovery re-executed 22 prior native actions and reused source readings. This was a continuation with retained evidence, not a matched fresh-planning benchmark. Its duration cannot be presented as a 58-to-13-minute product speedup.
- All old attempts, original saved answers, source, code and prior exports remained unchanged. The 1,596 common clinical cells match semantically; 44 no-dose evidence cells use JSON strings rather than arrays, retained as an explicit serialization difference.
- No new clinical bottle, weights, clinical qualification or release acceptance. No repeated provider run was launched by the audit.

Evidence: `artifacts/agere-clinical-interpretation-completion-2026-11-03/continuation/run-receipt.json`, `response.json`, `new-attempts.json`, and `owner-audit-result.json`. The original partial answer and every draft/attempt are preserved.

## Remaining effort estimate

These are provisional engineering-effort ranges based on the observed open work, not measured runtimes or guaranteed dates. They assume existing sources and provider availability, no rewrite, no new broad training, and no newly discovered major defect.

| Work package, in delivery order | Completion evidence | Estimated effort |
| --- | --- | --- |
| Clinical completion/state repair and patient-specific synthesis | Original 57-patient request produces individualized source-dated rationale and review steps; facts unavailable in the source are explicit, while work that can be performed is actually done. | 6–10 hours |
| Routing and practical cost/latency | Retained evidence is reused; only necessary changed dependencies dispatch; matched workflow timing and all attempts reported. No omission-based speed claim. | 4–8 hours |
| Guarded bottling/reuse closure | Current compatible execution reuse works; changed source/parameters invalidate correctly; failed candidates and rollback remain intact. | 3–5 hours |
| Whole-product acceptance across all ten areas | Clinical, population/VBC, finance, discovery, missingness and follow-up behaviors assessed on the release, including an unfamiliar supported composition. | 6–10 hours |
| Final local release and usability check | Current app saves/reopens the actual answers and work, produces deliverables when requested, and has verified installation/recovery applicability. | 3–5 hours |
| **Total remaining local synthetic engineering** | Subject to the stated assumptions; not commercial or clinical qualification. | **22–38 hours, roughly 3–5 focused working days** |

Independent clinical/model qualification, useful prediction labels, and customer deployment prerequisites do not have a credible completion date from coding effort alone. They require evidence/resources beyond the current synthetic engineering run. They remain explicit dependencies, not silently dropped scope. Advanced research and additional model experiments remain in the charter but are not prerequisites for repairing the current workflow; the existing product direction already puts population/VBC delivery first.

The estimate should be revised after the clinical completion defect is repaired and measured. No further promise of overnight completion is supported by this audit.

## Mike's architecture challenge during the audit

Mike challenged whether the layered stack is justified when a simpler Jev, Perplexity or Claude route might complete the task. The owner agrees that the observed execution fails the intended selective-computation design. The current evidence does not establish which simpler route would win; no comparative superiority is inferred.

For the observed unfinished-composition problem, the changed path must be compared with the simplest suitable route on identical retained patient evidence, requested output and source obligations. Native computations should be exact checks, coherent evidence bundles should receive only necessary interpretation, and final synthesis should complete the requested patient-level work. Additional checks must demonstrate an error they catch or another measured benefit. Compare task completion, unsupported claims, entity/time/quantity integrity, abstention, latency and all-attempt cost. Machine checks do not become independent clinical labels. Preserve the full population, scope and sources, and avoid repeating the original expensive acquisition campaign for this comparison.

This is a delivery criterion for the existing next increment, not another model catalog or architecture rewrite. A simpler route that meets the same requirements at lower cost should be preferred. Existing components remain available for their justified roles; they need not all execute for every question.
