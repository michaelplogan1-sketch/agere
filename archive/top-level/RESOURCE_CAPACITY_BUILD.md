# Service resources and task readiness connected to buyer work

Agere can now extend a saved capacity question with separately declared teams, service compatibility, staff availability and per-task ready times. The original task scope, task versions, duration assumptions, source dependencies and question remain intact. A new explicit question and assumption provenance produce a separate immutable comparison; no tasks, clinical priorities or service slots are changed.

## Executable behavior

The buyer workbench lets users add resource pools, select which declared services each pool can perform, enter worker counts and availability, and supply readiness for every selected task. Zero staff and missing compatible pools stay visible as blocked work. Time before a task is ready is distinguished from time spent waiting for a worker.

A deterministic non-preemptive dispatcher takes ready tasks in original creation order, skipping tasks with no available compatible worker. It chooses the available worker from the pool with the fewest declared services, then stable pool/worker identity. This is a transparent heuristic; no optimality or approximation guarantee is claimed for these resource constraints. Results show completion, in-progress/ready-waiting/not-ready/blocked counts, remaining task-minutes, pool utilization and exact schedules.

Low/central/high use the original supplied durations, not confidence intervals. A one-worker-per-active-pool baseline retains service restrictions and availability; a separate unrestricted single-worker reference assumes every service skill and is explicitly potentially infeasible. When any task is blocked, whole-batch completion is unavailable, even if all schedulable tasks finish.

## Actual saved example

The existing synthetic-domain-source-review question has four tasks for two patients. The supplied scenario has two respiratory reviewers from minute zero and one behavioral reviewer from minute 15, with behavioral tasks ready at minutes 10 and 60. These are engineering assumptions, not observed staff credentials, capacity or clinical readiness.

At the original 30-minute horizon, the central scenario has two completed tasks, one in progress, one not ready and 25 task-minutes remaining. Schedulable mean queue wait is 1.25 minutes, with the whole batch complete at minute 80. Both references complete one task by minute 30. Low/high whole-batch times are 70/90 minutes. A second scenario with zero behavioral staff explicitly blocks two tasks and gives no whole-batch completion time. The browser independently saved the normal configuration; three explicit scenario records remain current.

## Verification and preserved state

- 53 focused checks pass, including 18 new tests. These cover resource matching, release times, horizon boundaries, zero capacity, invalid inputs, immutable/concurrent/idempotent saves, staleness and races. 243 small single-pool combinations match the preceding frozen FIFO model; 81 constrained combinations verify conservation, non-overlap and service compatibility.
- Actual HTTP exercises save/inspect/history, both working and blocked scenarios, invalid requests and unchanged original comparison/task histories. Browser entry/save and saved-history rendering pass with no browser error logs.
- All 89 preceding protected identities, 380 work items, 383 work events, 10 capability tasks, 31 capability events and 19 external research files remain unchanged. Protect 90 identities with `artifacts/agere-resource-capacity-v1/protected-for-continuation.json`.
- No provider/token-count calls, patient contact, task execution, clinical review, source migration, generator change or external publication. Accounting remains unchanged and uncapped.

Evidence: `artifacts/agere-resource-capacity-v1/implementation-receipt.json`, `http-verification.json`, `preservation.json`, `focused-tests.txt`, `browser-resource-capacity.txt` and `resource-capacity.png`.

## Remaining limits

Worker/service compatibility and readiness are supplied claims. The simulation uses one worker from one pool per task and excludes simultaneous resources, dependencies, breaks, appointments, arrivals, setup, empirical duration distributions, urgent clinical priority and causal benefit. Optimistic upstream rechecking detects freshness changes; separate stores are not one atomic transaction. Neither this calculation nor source completeness establishes clinical validity.

Next return to reference evidence and explicit patient applicability through real review questions, while retaining the full clinical/benefit, prediction/discovery, mathematical and portable product queue. Three exact outbound interpretation approvals remain pending separately; no attempts were made here. Full acceptance remains false. Server 5224, session 12670, port 8885.
