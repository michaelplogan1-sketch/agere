# Saved-question execution lineage — October 11, 2026

G5 remains active; finite local release engineering remains **4/9**, G3 blocked
**5/6**. Saved questions can now inspect prospectively observed request attempts
alongside the existing route accounting, buyer options and owned work.

The existing work SQLite store holds append-only execution events. A scoped
execution binds exact input/output answer, context and decision IDs; patient IDs
or similar question text never substitute for that binding. Provider reservation
intent is persisted before reservation, and reservation, completion, blocked retry
and ledger-cache events retain exact request keys. An interrupted execution stays
unfinished. Events contain hashes and status metadata, not copied prompts,
responses or credentials. Context-local scopes prevent cross-thread attribution.

Normal question composition, source synthesis, claim reconciliation, grouped-answer
composition and individual/population decision preparation are instrumented. Calls
outside these registered HTTP paths, old operations and asynchronous work outside
the execution context are not covered. The ledger cannot establish whether a
reserved pending request reached its provider, nor capture request body sizes.
Existing body limits and cache/failure rules remain unchanged.

The saved-decision view joins those events to current ledger usage without retrying
requests. Saved output receipt keys are explicitly historical reuse, not new
reservations. Existing detailed cost snapshots remain available; unrecorded
historical, local-compute and human-review costs remain unknown. No default route,
model identity, classifier threshold or clinical authority is promoted.

Verification in `artifacts/agere-request-lineage-v1/`:

- 25 focused checks cover append-only durability, pending/failure/no-retry,
  concurrent/nested scopes, actual bounded-send cache behavior, token-check
  exclusion, and specialist-failure/frontier-escalation/exact-cache behavior.
  Provider failure/escalation used isolated transport fixtures, not live new calls.
- Actual normal-server HTTP replay passes for individual baseline, buyer candidate
  and population decision preparation on saved evidence. The candidate reuses ten
  saved planner receipt keys; no new request is reserved. Original work is unchanged.
- Actual local question compilation and disabled-hosted unsupported-question
  fallback pass. The latter retains its failed operation event.
- All three saved histories reopen after normal server restart. Browser verification
  shows the two same-answer planning executions beside the original candidate's
  existing three internal work items and earlier cost audit.
- All 170 protected files and 124 prior sealed receipts checked retain their bytes;
  the full provider calls ledger is unchanged. No provider call, fit or task mutation
  was made during this increment. Two current decision versions were saved by normal
  preparation because the earlier versions predate received-inventory binding.

Initial sandbox socket access and a restart-script import-path failure were
corrected and retained as diagnostic logs. The final authorized loopback checks
pass. Slow individual source validation remains a known limitation; this delivery
makes no latency guarantee.

The research-chat BOTTLED application is preserved in
`research/agere/BOTTLED_INTAKE_2026-10-11.md` and the canonical decision log. It calls
for a measured repeated task, equivalent existing-route/code/distillation baselines,
protected family/time splits, qualified offline artifacts and honest total costs.
The build owner has not yet independently reread its paper or numeric claims.

Next G5 work is the comparative routing/role experiment, not another accounting
layer: select a concrete repeated question-to-plan or narrow extraction failure,
verify the research source, and compare executable candidate behavior against the
existing economical route on appropriate development/held-out evidence. Retain
negative results, unsupported labels, review requirements and unknown costs.
The blocked medication transfer, changed-source reinterpretation, G6 adapter and
operations work, independent clinical qualification and full ten-area scope remain.
