# General analysis connected to simulated work

Agere's general staged-analysis path now carries the original request into a saved patient work queue, a generated HTML deliverable and a monitor. The local release is `8d1e2625ba5d5115eab132a18afab644989fd9cf`, with `9d271ecb5f6b73deeb66c50746c557610b7a16b9` retained for rollback. The active warehouse configuration is preserved.

This is a G3 workflow delivery. The synthetic MVP remains incomplete: G3 stays active, G5 remains blocked on independent qualification, and full ten-area acceptance is false. Tests and successful HTTP responses are not a product completion percentage.

## What changed

- Explicit patient tables can prepare simulated work while retaining their detailed answer columns. Merely returning patient identifiers in an aggregate does not authorize a patient queue.
- Conditional work uses a computed, question-bound boolean column. Only confirmed matches create work; unknown values remain unresolved. Saved input populations are enforced over patient-linked source relations.
- Work monitors read saved task progress, completion and exceptions without requiring a changed warehouse. Answer monitors reexecute the saved analytical definition against a new snapshot, retaining the original input or frozen result scope.
- The work view displays selection counts and simulated completion. Monitoring responses report zero new API calls and cost while preserving original generation receipts.
- The workflow validator understands “work with patients,” and the semantic review distinguishes saved work status from SQL result columns.

## Measured evidence

The final affected suite passes 120 tests, also passing from the frozen source copy. Coverage includes patient scope, conditional selection, unknowns, duplicate identities, immutable context, exact-definition follow-up, simulated completion and exceptions, cost accounting, and replay. A pre-existing unclosed-CSV ResourceWarning remains in the risk-adjustment test fixture.

Actual hosted requests ran through the complete API on an independent copy of the 4,115-patient synthetic warehouse:

| Request | Workflow evidence | Analytical qualification |
| --- | --- | --- |
| Repeated no-shows | Final candidate: 22 patient rows, 22 simulated tasks, generated brief and work monitor. Browser execution completed one five-step journey and the monitor showed 1/22 complete. Exact replay added no tasks or paid calls. | All rows/counts match direct SQL for the disclosed `outcome=no_show` definition. The predeclared latest-status reference returns 21; that strict miss is retained. |
| Allowed dollars by payer | Generated brief and answer monitor across 20 groups. A controlled $100 change on the isolated source changed exactly the relevant payer total, with no model call. | All 20 totals and per-patient averages match the predeclared SQL. |
| Repeat ED patients by PCP practice | Generated brief and answer monitor across nine groups. | Patient counts match. Seven cost totals fail the all-source reference because the interpretation selected payer claims and omitted provider claim amounts. This remains a real scope defect. |
| Original HFrEF/GDMT request | After workflow corrections: 17 patient rows, 17 simulated PharmD tasks, generated HTML deliverable and work monitor. The earlier partial attempt is retained. | Software connection demonstrated; clinical correctness is not qualified. The answer restricts HFrEF to its coded concept and maps absent qualifying fills to `not_on_therapy`. Its data-currency warning does not establish current medication status, clinical suitability or complete patient coverage. |

The activated service also produced 22 tasks and a generated brief. A completed simulation and a records-needed exception were both observed in its monitor and browser. The interactive brief's practice filter correctly showed seven rows. Exact replay took 0.139 seconds with zero new calls and no duplicate tasks.

New complete requests in this run took roughly 79–197 seconds. These observations expose a latency gap; they are not a controlled performance comparison. Exact costs, the activated-runtime check, replay and browser evidence are retained in `artifacts/agere-general-workflow-v1/implementation-receipt.json` and its referenced files. All failed attempts remain in the accounting. No protected benchmark was opened, rerun or rescored.

Generated-view validation also needs more work. The first isolated access brief passed structural checks but later threw a browser error on a nonexistent `answer.clinical_eligibility_established` path. The live brief rendered and filtered without a new runtime error, but its “PCP practices involved” count includes the unattributed group (nine groups versus eight known practices). These failures remain explicit; generation success is not semantic or runtime qualification of every generated view.

## Remaining MVP work

The next connected G3 increment is analytical source and temporal consistency: preserve the requested source coverage across related questions, distinguish no received evidence from a negative current-state fact, and carry unresolved judgments into conditional work. Verify provider/financial, access and clinical compositions with explicit source/window mutations, not only the original examples. The full charter remains in `AUTONOMOUS_STACK_QUEUE.md`; the exact next action is owned by `AGERE_BUILD_CONTROL.json`.

Monitoring is manually checked. Generated deliverables are saved evidence snapshots; the workspace monitor records subsequent observations. No real outreach, enrollment or care occurs. Independent analytical/model/clinical qualification and broader product acceptance remain open. Existing saved answers remain inspectable but may require re-asking after planner-policy changes before new work.
