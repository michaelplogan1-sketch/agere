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
| Shared question/workflow, chief-of-staff UI, integration and :8885 | Codex build chat 01a1542a-b5a3-7d23-94fc-19628fd76094 | clinical_core/agere/clinical/{shared_planner,request_plan,request_language,workflow_contract,chief_staff,conversation}.py; deliverables/{answer_context,workflow,mock_views,action_center.*}; assets/{explore.*,experience.js}; ask_view.py; service.py; related tests and existing controls | Delivered in ea3deca; 110 focused checks plus connected browser acceptance. See AGERE_CHIEF_STAFF_2026-10-20.md |
| General architecture/proof specification | Review chat 01a15957-adcc-7121-8f4e-6ece286ec64e | docs/agere/GENERAL_QUESTION_ARCHITECTURE_PLAN.md only | Received and adopted under existing G3/G5; proposed study thresholds are not results |
| Independent Claude source review | Configured Anthropic API, model recorded in artifacts/agere-chief-staff-v1/claude-api-review-receipt.json when complete | Read-only bounded source packet; no product paths | Review and recheck received; findings fixed with independent developer regressions. No clinical qualification |
| Standalone pronoun diagnostic | Diagnostic chat 01a15936-8db1-74f1-886b-8e1ffb89030f | Delivered 96dfe3f / 591ee6d | Integrated; no concurrent runtime ownership |

The local Claude Code CLI authentication is expired. No active authenticated
Claude Code implementation session or file claim has been verified; do not invent
one or assign overlapping product edits. The requested Claude double-check uses
the existing metered API instead. The integration owner alone seals and deploys.
The separate public design checkout and independent reference work remain untouched.
