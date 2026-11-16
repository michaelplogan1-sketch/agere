# Agere build-method review — October 11, 2026

The product direction remains appropriate, but the implementation process is no longer the most effective way to finish it. The stack has useful working foundations and real integration receipts. Development has nevertheless drifted toward many narrow increments and compatibility layers while shared workflow qualification remains incomplete. This is an engineering assessment of the inspected repository and receipts, not a clinical review or a claim that every module was audited.

## Findings grounded in the current implementation

| Finding | Evidence | Consequence |
|---|---|---|
| The integration surface has grown faster than complete workflow acceptance | 219 Python modules in the clinical subtree, 50 JavaScript asset modules, 270 literal stack API paths in a 1,150-line service dispatcher | More interfaces and lifecycle variants must agree for a user to complete one question. Counts are complexity indicators, not proof that individual modules are unnecessary. |
| Reproducibility has become coupled to implementation immutability | The latest preservation manifest pins 170 implementation/artifact files; source-plan identities include raw module hashes; service bootstrap installs numerous runtime adapters | Preserving old results is necessary. Requiring each improvement to wrap frozen implementation code has made changes harder to understand and integrate. |
| Lifecycle logic is duplicated | `program_work_v1.py` and `program_work_current.py` share 458 identical lines out of 463 and 468 respectively; a registered-adapter engine also implements the lifecycle | Fixes and invariants have several paths. Consolidation should preserve historical replay, not erase prior engines or receipts. |
| Persistence is fragmented | 60 top-level SQLite files in the active stack directory, covering different kinds of runtime data and receipts | This is not a measured performance diagnosis, nor a reason to merge every cache. Mutable work state needs clear transaction ownership; immutable evidence and caches can remain separate. Cross-store consistency remains unfinished. |
| Quality evaluation is behind feature integration | Reader comparison covers 12 declared statement cases / 48 role executions. Its receipt explicitly excludes completed source-meaning, planning and service-matching evaluation | Passing software checks cannot establish that the full pipeline selects sufficient evidence, understands it, chooses an appropriate service and retains the original question. |
| Known retrieval failures remain consequential | Medication retrieval omitted both medication orders; payer retrieval omitted one coverage and 15 verification records. Explicit category selection can restore them; default candidate generation is unchanged | Another reader or reranker cannot recover evidence it never receives. Fix and measure source coverage before attributing failures to model choice. |
| There is measured latency to address | Previous profiling reports scoped FTS/BM25 taking 14.7–21.7 seconds on three development questions | Improve the demonstrated bottleneck while verifying scope/ranking preservation; do not begin speculative infrastructure work. |
| Current planning documents obscure the current priority | The former active queue contained 956 lines, including many superseded “next” steps and historical constraints | Retain the history separately, and make one concise current queue authoritative for continuation. |

Measurements are in `artifacts/agere-build-review-v1/audit.json`. The service, protected files and latest delivered capabilities are preserved. This review made no model calls and no new quality-validation claim.

## What to retain

Keep the original-cohort adapter and source boundaries; exact source identity and dates; explicit unknowns; existing local retrieval and specialist routes; shared cost accounting and caching; buyer service declarations; program/work state; and the existing domain, mathematical and operational capabilities that already execute.

Do not start a wholesale rewrite or a microservices project. A modular application is appropriate for the current local product. Migrate narrow interfaces under comparison tests while keeping historical results readable and old execution routes explicitly versioned where needed.

## Architectural correction

The current path should have a small set of shared, versioned contracts:

`Question and population → evidence selection → interpretation/state → evidence-supported options and buyer/payer matching → work and outcome follow-through`

1. **Evidence selection:** original question, population/date scope, selected and excluded records, exact typed references, source coverage and unresolved inputs. References are data fields, not identities inferred from generated prose.
2. **Interpretation/state:** claims with exact sources, subject/time/polarity, contradictions, unanswered questions and model/contract version. A cited source is not an endorsed clinical conclusion.
3. **Options/matching:** patient evidence requirements, guideline/rule version, buyer capability, dated payer/program facts, goals/permission requirements and reasons for an unknown or unavailable match.
4. **Work:** the original question and evidence dependencies, proposed versus assigned responsibility, revisions, acknowledgements and explicit completion evidence. Evidence follow-up must return to this same object.
5. **Execution trace:** stage-level input identity, route/model version, result, failure category, latency and cost. Use the trace for evaluation and debugging rather than separate one-off report formats.

Keep exact historical model/prompt/code references in execution manifests. New releases can change implementation under a new explicit version without rewriting or reclassifying old outputs. This is a target for incremental migration; it is not delivered by this review. Do not edit the 170 pinned files merely because this review recommends a better future versioning boundary.

## Revised critical path

### 1. Finish the shared workflow evaluation in the real application

Use the six already approved domains: medication, respiratory, cardiometabolic, functional support, appointments and payer evidence. Drive existing APIs and the actual question/state/work path. Include missing evidence, conflicting timing, unavailable services and explicit unknowns. Keep the existing protected cases and family boundaries; the inspected examples remain development probes, not independent validation.

Save one trace/scorecard separating retrieval misses, reranker losses, reader errors, synthesis/reference errors, planning failures and service-matching failures. Distinguish software assertions, assistant-authored expected behavior and independently adjudicated clinical labels. Compare simple baselines and appropriate current candidate roles on identical stage inputs. Do not call a majority baseline's success on twelve selected statements a model qualification result.

Deliverable: executable runner and case manifests, actual workflow traces, stage-specific failure table, latency/cost, and explicit unsupported stages. The broad evaluation may initially expose failures; retain them. Do not postpone it until another chain of small features is finished.

### 2. Repair the highest-impact failures through one shared path

Use the evaluation to determine whether the next correction is source coverage, interpretation, task context, payer/service matching or lifecycle handling. Carry combined source-plan questions into that shared path rather than adding another autonomous queue. Verify an unchanged workflow as well as the affected one. A new helper module, UI control or receipt alone does not finish this increment.

Deliverable: a user can ask a representative question, inspect sufficient declared evidence and uncertainty, see the available buyer services and unresolved prerequisites, create/reopen the appropriate internal review work, and attach follow-up evidence without losing the question or source identities. No clinical entitlement or care delivery is inferred.

### 3. Consolidate only the interfaces that impede the measured workflow

Replace duplicated lifecycle and runtime patching in the path just exercised with explicit versioned adapters. Define mutable transaction ownership and retain immutable evidence history. Repair the measured retrieval latency where ranking/source equivalence can be demonstrated. Do not mass-migrate all stores or refactor unrelated modules to make the inventory look smaller.

Deliverable: fewer execution variants in the critical path, preserved historical replay and the same or better acceptance results. Then widen the same shared contracts across the remaining product queue.

## Model and research policy

The goal is a qualified role, not a list of installed models. For each role record the required task, input/output contract, deterministic/simple baseline, candidate, quality evidence, latency/cost and promotion/abstention decision. Add or train an SLM, classifier, reranker or other model when a measured failure justifies it. Use the authorized hosted specialist/frontier route when it performs the needed task better; “zero paid calls” is not a success metric.

Mathematical methods from finance, HFT, astrophysics and control remain candidate tools for concrete healthcare or operational questions. Promote them through the same baseline and assumptions checks. Literature coverage is ongoing; no exhaustive research, universal model ranking or clinical qualification is established.

## Completion and reporting

Track two separate outcomes: **the finite synthetic product release works as specified**, and **its clinical/model quality is independently validated**. Engineering can complete workflows and expose genuine unknowns; it cannot manufacture clinical labels, live plan verification, customer deployments or real care outcomes. A blocked external dependency is reported, not silently treated as completed engineering or used to stop unrelated work.

The broader product charter remains intact: broad interpretation/population health, recommendations, payer/VBC, discovery/prediction, learning/routing, portable intake and production readiness. Record acceptance against that charter in a single release matrix. Future updates should lead with complete workflows gained, measured quality/failure changes and unresolved acceptance rows, with detailed receipts linked underneath.

## Work interrupted for this review

Two combined-question adapters were drafted this turn but were not tested or delivered. They are preserved under `artifacts/agere-build-review-v1/drafts/`. Their temporary service/registry hooks were removed; the service file exactly matches the last verified release. Do not enable these drafts by copying them back before the shared contract/evaluation decision. No server restart, new work item, human review or provider call occurred in this review.

The standing authorization to continue building remains active. The next substantive run should execute the shared workflow evaluation above, not resume the prior helper-by-helper sequence or spend a run merely rechecking this architecture note.
