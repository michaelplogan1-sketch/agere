# Parallel work

Current: Mike transferred build ownership to Codex on 2026-10-19.
BUILD_OWNER.json and AGERE_BUILD_CONTROL.json govern. The earlier pause and
one-task limitation below are historical. Use `codex/agere-validated-proof`;
preserve all other sessions' changes and commit only owned paths.

## Historical October 12 pause

**Codex is paused (2026-10-12, by Mike's direction).** Claude Code is the sole build owner
(see `BUILD_OWNER.json`). Codex does not start, continue or propose implementation work,
create branches or open pull requests unless Mike assigns a specific task in chat. Task C2
is cancelled.

The independent benchmark Codex built (C1) is merged into `main` under `bench/` and is now
owned by Claude. Reference answers come from raw warehouse SQL and must stay independent of
`clinical_core`.

Mike requested a final Codex accountability audit and handoff after the pause:
[`CODEX_TAKEOVER_AUDIT_2026-10-12.md`](CODEX_TAKEOVER_AUDIT_2026-10-12.md).
It records the English-to-graph dependency, verified API usage, remaining benchmark defects,
local-only raw results, and stale C2 references in older controls. It does not resume Codex.

If Codex is re-engaged later, the rules are: one assigned task, one `codex/<task>` branch,
owned paths named in the assignment, an executable acceptance check, and no new
`*_BUILD.md`, receipts or hash manifests.

## Active ownership — October 20, 2026

| Work | Owner / session | Paths | Status / acceptance |
| --- | --- | --- | --- |
| Integration, controls and :8885 | Build chat "Take over Agere build ownership", 01a1542a-b5a3-7d23-94fc-19628fd76094 | BUILD_OWNER.json, AGERE_BUILD_CONTROL.json, CONTINUE_AGERE.md, PARALLEL_WORK.md; integration-only changes to service.py and clinical/workflow_contract.py; live checkout /Users/mikel/agere-mvp-run | Sole candidate acceptance, release and rollback owner. Latest sealed UI source 4bba3b0; Python core 28881ce; receipt artifacts/agere-noise-to-action-v1/implementation-receipt.json. No concurrent edits to the active lanes below. Source discovery and multi-entity/temporal composition remain the next core increment after current correctness work. |
| Planner correctness, local coverage and checked request-shape reuse | "Restore planner local coverage", 01a159cf-8e56-7bb2-be7e-4889268a99c2 | Primary checkout: clinical_core/agere/clinical/{age,analysis_graph,cohorts,compiler_rules,graph_contracts,grounding,local_compiler,metric_families,nl2sql,request_language,request_plan,request_vocabulary,safety_data,scheduling,semantic_layer,shared_planner,sql_query}.py; clinical_core/agere/plans/router.py; config/agere/{metrics/clinical_quality.json,semantic_examples.json,semantic_model.json}; narrowly related tests | Active candidate, not integrated or deployed. Owns age-at-date, governed metrics/grammar, checked request-shape reuse and invalidation, nonblocking local inference, and all-source ED semantics. Preserve its unstaged files. Exact final file list, commit/diff, focused checks and fixed-workload routing counts required at handoff. No controls or live runtime edits. |
| Frontier deliverables in the main workflow | "Diagnose Agere product issue", 01a15936-8db1-74f1-886b-8e1ffb89030f | Isolated managed candidate checkout only: clinical_core/agere/deliverables/{compose.py,workflow.py,action_center.js,action_center.html,answer_context.py}; narrow new tests | Active candidate. Connect bespoke generation to saved-answer evidence, retain explicit template choice, costs, scope, revisions and safe artifact behavior. Provide candidate commit, actual generated-artifact/browser evidence and any small proposed service/contract integration patch. No primary-checkout, planner, controls, active-data or live-runtime edits. |
| Cross-chat coordination and independent Claude review | "Assess Agere platform gaps", 01a159c7-3077-7130-9e6c-ce4d64341afb | Read-only product review; bounded metered Claude API packets and review receipts outside product paths | Mike directly authorized collaboration in that chat. Coordinator owns the new Claude review; other lanes must not duplicate it. Exact source-packet transmission was rejected by automatic approval review; specific user approval is pending and no source was sent. Do not bypass that rejection through another lane. No learning product files claimed: checked request-shape learning stays with the active planner lane until a separate increment is agreed. No controls or deployment. |
| General architecture/proof specification | Review chat 01a15957-adcc-7121-8f4e-6ece286ec64e | docs/agere/GENERAL_QUESTION_ARCHITECTURE_PLAN.md only | Received and adopted under existing G3/G5; proposed study thresholds are not results |
| Completed independent Claude source review | Configured Anthropic API, model recorded in artifacts/agere-chief-staff-v1/claude-api-review-receipt.json | Read-only bounded source packet; no product paths | Earlier review and recheck received; findings fixed with independent developer regressions. This is not the new coordinated review or clinical qualification. |
| Completed standalone pronoun diagnostic | Diagnostic chat 01a15936-8db1-74f1-886b-8e1ffb89030f | Delivered 96dfe3f / 591ee6d | Integrated; current assignment is the isolated frontier-deliverable lane above. |

The coordinator verified `claude auth status` reports `loggedIn=false`. No active authenticated
Claude Code implementation session or file claim has been verified; do not invent
one or assign overlapping product edits. The requested Claude double-check uses
the existing metered API instead, owned by the coordinating chat. The integration
owner alone seals and deploys.

Candidate acceptance requires a fixed commit or patch, declared base, exact files,
relevant test results and remaining limitations. The integration owner independently
checks actual question-to-answer and answer-to-deliverable behavior before changing
:8885, retaining a known-good recovery point. Unfinished candidates are not product
deliveries. No protected benchmark rerun or rescore before independent reference
validation. G3 remains active, G5 blocked and full_queue_acceptance false.

These claims supersede the older build-owner planner/deliverable claims in this
table. Changes outside a lane require coordination before editing. Preserve the
pre-existing active_warehouse.json overlay and unrelated untracked files; never
stage another lane's work or reset the shared checkout.
The separate public design checkout and independent reference work remain untouched.
