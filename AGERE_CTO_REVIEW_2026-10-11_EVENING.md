# Agere engineering review — October 11 evening

Requested by the user during G6 recovery implementation. This is a focused review of the actual application, canonical delivery gates and implementation evidence, using the engineering code-review skill. It is not an exhaustive security audit, systematic literature review or independent clinical assessment.

**Decision: keep the modular architecture and full product charter; concentrate the remaining build on release blockers. The stack is a substantial connected synthetic prototype, not a finished or clinically qualified commercial system.** The current G6 recovery increment must finish before another feature. Do not convert new infrastructure checks into full acceptance.

## What is on track

- Original questions retain source versions, interpretation, buyer/payer context, owned tasks and follow-up. Individual and complete-population decisions use shared stores and interfaces. The original 1,614-person roster remains intact.
- Population/VBC work and received financial reconciliation have connected engineering evidence. Unknown attribution, benefits, clinical need, outcomes and causal effects remain visible.
- Retrieval, local classifiers/readers and hosted roles have bounded jobs. Unsupported labels abstain; negative baseline comparisons and unqualified candidates remain explicit. Adding a model has not been treated as qualification.
- The canonical control now separates accepted, active and blocked gates. All ten backlog areas are retained. Source/evaluation boundaries and the shared failed-request ledger are protected.
- The current recovery implementation copies the complete connected build, uses SQLite backup and integrity checks, refuses a running cooperating server, rejects incomplete/corrupt checkpoints and rebinds internal links only in the restored copy. It never rewinds the provider ledger or activates an external connection.

## Prioritized findings

| Priority | Finding and evidence | Required correction |
|---|---|---|
| P1 release blocker | G6 is currently same-machine data inspection recovery. `clinical_core/agere/local_release.py:78` records current code hashes and restore requires that same code, but does not archive an independently runnable code/runtime release. External model/research/source dependencies are checked at the manifest level and remain on this machine. | Preserve a recoverable code/config/runtime version and test an independent local release location. Verify controlled work resumption and rollback; do not label inspection-only recovery full operational portability. |
| P1 release blocker | G3 remains five of six individual workflows. The specific medication payload/destination approval remains pending. Actual changed-source interpretation and affected-result recomputation are not yet demonstrated. | Finish the permitted local changed-source workflow end to end. Keep the exact external-transfer restriction; do not bypass it through a different route. Return to the medication execution only with the required authorization. |
| P1 qualification blocker | G5 routing is four of five checks. The classifier contract is 16/24 reachable labels; existing supervision does not support the other eight. Historical full compute/reviewer economics and independent clinical/model qualification remain missing. `READER_CONTRACT_ROUTING_BUILD.md` records the limitations and retained failures. | Establish defensible source-family-disjoint supervision/evaluation and authentic review before model promotion. Unknown effort stays unknown. Do not spend more turns adding accounting fields or fabricate training examples to declare the gate complete. |
| P2 maintainability | Some identities still hash entire orchestration files: `reader_contract_routing.py:61`, `care_capabilities.py:25`. A review/UI/helper change can create new identities or stale otherwise unchanged decisions. The baseline question planner already uses an explicit behavior identity. | Change only a measured affected contract, with equivalence and stale-change tests, toward explicit behavior/model/source identities. Keep exact implementation hashes in receipts. Avoid a blanket rewrite or altering frozen experiments. |
| P2 performance | Saved individual reopening calls dependency validation and then reads the full patient received inventory again (`question_decision.py:290` and `:292`). The prior measured local-reader replay remains roughly 22 seconds; that is not an interactive production target. | Profile a named saved-question workflow and remove duplicate scans with a transaction-bound snapshot or existing invalidation cache. Compare latency and exact answers/work before activation. Do not suppress dependency checks merely for speed. |
| P1 production prerequisite | The service is a loopback synthetic workbench, not a governed customer deployment. Tenant isolation, authenticated clinical authority, production adapters and verified payer benefits have not been accepted. | Keep these as explicit production prerequisites. Do not claim that local HTTP checks, buyer declarations or imported clauses establish clinical execution or benefit entitlement. |

## Review dimensions

- **Correctness: credible local engineering, incomplete release.** Exact identities, conservative unknowns and original/population reopen checks are useful. The remaining six-domain/change-reassessment and operational-resumption gaps are material.
- **Security: bounded for the current local research scope; production unassessed.** Loopback checks, recovery route restrictions and unchanged credential/ledger handling are positive. This is not a penetration test or multi-tenant authorization review.
- **Performance: needs a targeted acceptance pass.** Existing caches and deterministic operators are appropriate. Repeated validation and large offline checkpoints have costs; provider dollars alone do not measure total operating cost.
- **Maintainability: elevated integration debt.** Reusing the modular application remains preferable to a rewrite, but repeated compatibility wrappers, broad file-hash identities and many stores make changes harder to reason about. Delivery should now require connected behavior, not another component receipt.

## Delivery discipline to retain

`AGERE_BUILD_CONTROL.json` remains the only sequence and exact next action. This review does not create another backlog. Finish G6's bounded operational acceptance, then address the retained G3 and G5 blockers. No new model family, mathematical catalog, helper workflow or architecture rewrite unless a named failed acceptance check requires it. Every increment needs a user-visible behavior or measured failure reduction, evidence and a remaining gap. After two increments without either, reassess rather than expand scope.

Engineering completion, model/clinical qualification and external deployment remain three different outcomes. The target is a reliably evidenced release, not a claim of perfection. I previously allowed too many narrow increments to substitute for release closure; the corrective action is to close these shared workflow and operational gates, while preserving the full population-health/VBC/OAP charter.

Final recovery evidence and the computed gate assessment will be appended after the in-progress verification completes.

## Final evidence and release assessment

The G6 data-recovery milestone is complete within its declared inspection-only scope. See `LOCAL_RECOVERY_BUILD.md` and `artifacts/agere-local-recovery-v1/implementation-receipt.json` for 46 focused tests, the 1,427-file/66-database checkpoint, actual API/browser/restored-restart checks and preservation evidence. Exact individual/population decisions, owned work, source reading and dispatch history were recovered. Zero new provider calls; no clinical review or model promotion.

The live scorecard at `artifacts/agere-local-recovery-v1/scorecard.json` reports:

| Current-release requirement | Actual status |
|---|---|
| Six individual question-to-follow-up workflows | 5/6; medication execution incomplete |
| Complete population program | Passed local engineering |
| Changed-evidence reassessment | 0/4 accepted checks; local preparation exists but full recomputation is unassessed |
| Source and adapter conformance | 4/5; writable activation/recovery remains blocked |
| Longitudinal prediction path | Passed engineering, including supported inability to estimate; no superior predictor claimed |
| Discovery/explanation path | Passed scoped engineering; no general causal validity claimed |
| Outcome/financial reconciliation | Passed documentary engineering with explicit unknown attribution/contract effects |
| Economical role routing | 4/5; complete cost/review accounting remains blocked |
| Portable local operations | 4/6; independent runnable packaging and operational resumption remain blocked |
| Independent model, clinical and production qualification | Unassessed/separate prerequisites |

**Four of nine engineering requirements pass. This is a requirements count, not “44% of the product is built.”** The recovered data and new partial checks are real progress without completing their parent release requirements. Restored individual/population reopen took about 1.58/0.32 seconds in one development run; that is useful inspection evidence, not a general performance guarantee or a controlled optimization comparison. The earlier roughly22-second native-reader replay remains a different, unresolved interaction cost.

The next permitted implementation remains G6: versioned runnable local packaging and controlled resumption/rollback, preserving source/model qualification boundaries. In particular, `learning_cohorts.py:182` binds a cohort to its original workspace; the restore preserved the saved reading but rejected a claim of current cohort qualification in a different directory. Resolve portability through an explicit installation/compatibility contract, not by rewriting frozen plans or disabling their checks. After G6, return to the retained changed-evidence/medication and qualification gaps. No new feature/model catalog is justified by this review.

The original8885 workbench remains running. The temporary8887 server and its browser tab were closed. The user's build authorization remains active; this review does not narrow the ten-area charter or create a competing plan.
