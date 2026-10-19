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
