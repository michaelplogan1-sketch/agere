# Request admission correction — October 20, 2026

The request-size and accounting correction is live at http://127.0.0.1:8885/ask on source `5092910a63d3b32bcc2047491cb373d778311734`, with rollback `176aabab590a938e954fd2e9a510546ee6c474dd`. This is a limited correction, not acceptance of the complete HFrEF workflow.

Mike authorized a task-scoped Codex integration window, preservation of concurrent changes, and a full-request test kept in Codex. The overlapping Claude session and its test servers were stopped through native controls. No question or diagnostic was transmitted to Claude or another external reviewer/provider during this work.

The reviewed correction removes exactly duplicated schema text, gives shared planning an explicit bounded request envelope, distinguishes an unsent size rejection from a paid call, and stops audit admission failures without another paid interpretation attempt. The generic request default remains unchanged. The complete original question and definitions remain intact.

Main integration commit: `301b65b`. Only the four reviewed files were committed. The unfinished general-analysis implementation remains unstaged in the primary checkout and was preserved byte-for-byte, including its shared-planner delta. The release is based directly on the previous live source and excludes the unfinished general-analysis and derived-discovery candidates. The warehouse configuration and histories were preserved.

Evidence:

- 70 checks passed on the merged primary checkout, including the existing general-analysis tests.
- 120 affected checks passed on the clean release.
- Independent Codex review verified the exact release base, four-file scope, and matching reviewed file hashes; no remaining code-review blocker for admission-only activation.
- Recorded-response replay of the reported failure passes the formerly rejected third-request admission: 122,604 bytes versus 129,918 before. This replay makes no new provider call and does not invent a completed interpretation.
- Six actual live grammar checks pass with zero paid calls, matching source identity, direct birth-date age calculation, all-source ED totals, grouping/exclusion retention, and restrictions on actions from summaries.

The exact complete HFrEF request was submitted through the real compose HTTP endpoint on a fresh copy of the same warehouse with hosted calls disabled and a remote-network guard. It returned HTTP 400 / `not_understood` after 20.186 seconds: “Local interpretation exceeded its time limit; no reduced question was executed.” It made zero paid calls and created no new answer workspace, agent work, or scoreboard.

**Complete workflow readiness: failed.** This local-only test does not establish hosted-route success. Medication/dose correctness, provider attribution, action targeting, the requested deliverable, and loop monitoring remain unverified. A timeout does not establish missing warehouse data. No clinical criteria or request-specific answer route was invented.

The release receipt is `artifacts/agere-request-admission-v1/implementation-receipt.json`. Restricted local diagnostics, the exact request, preservation snapshots, review, replay, and actual HTTP result are under `/Users/mikel/Documents/Codex/2026-10-20/agere-request-bound-investigation/`.

G3 remains active, G5 remains blocked, and full product acceptance remains false. Keep the overlapping implementation paused. Continue within Codex on the complete-request failure, preserving the full requested workflow and existing evidence/definition boundaries; do not call the workflow ready until the actual complete result passes.
