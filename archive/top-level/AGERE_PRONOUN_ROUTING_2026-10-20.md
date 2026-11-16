# Standalone question routing repair — October 20, 2026

The reported MSK ACCESS request was rejected before planning because the
conversation boundary treated every occurrence of “them” as a reference to an
earlier answer. Complete questions with a named subject and a linked clause now
reach the planner unchanged. No program name or eligibility rule was added to the
router. Explicit saved-answer selection still binds the exact saved membership;
unresolved external references still cannot become warehouse-wide queries.

The repair is active on localhost:8885 from source commit `96dfe3f`.
Recovery source is `f0eb75b`. The existing warehouse, configuration overlay,
saved analyses, and work were preserved.

## Evidence

- 80 focused tests passed across conversation, shared planning, earlier CTO
  regressions, and answer UI. Existing VBC empty-slice warnings remain.
- The exact screenshot question passes unchanged through conversation dispatch,
  with and without a conversation, with explicit warehouse scope, and against an
  explicitly selected saved answer.
- Live health passed. A diabetes result and its saved “Count them” follow-up both
  contained 230 patients. An explicit warehouse count returned 4,115. “Count them”
  with warehouse scope was rejected without executing a population query.
- Replaying the exact screenshot request returned HTTP 200 through the shared
  planner, with `follow_up: false` and the complete original question preserved.
  The request took 13.86 seconds and two hosted calls, estimated at $0.210538.
  This was a user-requested product diagnostic, not a protected benchmark.

Evidence: `artifacts/agere-pronoun-routing-v1/`.

## Remaining workflow gap

The planner returned `not_understood` for the combined eligibility, agent
creation, and ongoing monitoring request. It reported unavailable eligibility
definitions in its supplied catalog and unsupported action/monitoring intents in
the read-only query language. These reports do not establish absence from the
entire warehouse. No partial all-patient query, enrollment, or agent execution
was performed.

This repair closes the demonstrated routing failure. It is a bounded
conversation-language improvement, not complete semantic coreference resolution
or full MSK workflow delivery. G3 remains active, G5 remains blocked, and all ten
acceptance areas remain unchanged. The next shared composition work remains in
`AGERE_BUILD_CONTROL.json`.
