# Capacity scenarios connected to internal work

Agere now answers a concrete operational question from the buyer workbench: **how do staffing and assumed task duration affect this selected batch of internal work?** The user selects current tasks, enters a question, supplies low/central/high service durations and their provenance, and chooses hypothetical pooled workers and a horizon. The saved answer keeps exact task versions, patient/service identities, source freshness, the full unselected task list and the question.

The result compares first-in-first-out execution with one worker versus the supplied worker count. It reports completion time for the batch, mean/max task wait, completed/in-progress/waiting tasks at the horizon, remaining workload, utilization under the scenario and a task-by-task schedule. Zero workers produces no invented completion time. Low/central/high are separate supplied scenarios, not confidence intervals.

The implementation uses conventional list scheduling with a min-heap of worker availability. A lower bound on batch completion is the larger of total workload divided by workers and the longest task, under these identical-worker assumptions. This follows [MIT's parallel-machine scheduling notes](https://courses.csail.mit.edu/6.854/18/Scribe/s20-ApproxNP/s20-ApproxNP.html). The finite-resource FIFO interpretation is also described in the [SimPy shared-resource documentation](https://simpy.readthedocs.io/en/stable/simpy_intro/shared_resources.html); no new dependency was installed. No new mathematics or optimal clinical allocation is claimed.

## Delivered example and verification

The actual synthetic-domain-source-review buyer has four selected task records for two patients. The engineering demonstration supplies behavioral durations of 10/20/30 minutes and respiratory durations of 15/30/45 minutes. With two interchangeable workers and a 30-minute horizon:

| Central-duration scenario | One worker | Two workers |
|---|---:|---:|
| Whole batch complete | 100 min | 50 min |
| Mean internal task wait | 40 min | 12.5 min |
| Complete by minute 30 | 1 task | 2 tasks |

These differences are arithmetic under supplied assumptions, not measured staffing improvement, empirical care delay, clinical benefit or proof that the same staff can perform both services. Four tasks do not mean four patients; distinct task scopes are retained explicitly.

- 35 focused checks pass, including 18 new tests. These cover zero workers, exact horizon boundaries, partial work, 243 small duration/staff combinations, workload conservation, non-overlap and theoretical completion bounds.
- Work integration checks cover missing assumptions, foreign/held/stale tasks, current source dependencies, task/service changes, idempotency, concurrent saves, immutable history, tampering and a queue-change race.
- Actual HTTP runs preview → saved comparison → inspect/history, verifies idempotency and invalid requests, and confirms that every existing program/task/event table remains unchanged.
- Browser entry and save reproduce the same saved scenario, with zero browser error logs and the assumptions shown beside the answer. Saved result: `artifacts/agere-work-capacity-v1/scenario.json`; screenshot: `artifacts/agere-work-capacity-v1/capacity-scenario.png`.
- The 88 preceding protected identities and all 19 separate research files remain unchanged. Protect the new capacity implementation as well: 89-file `protected-for-continuation.json`. Final task histories preserve 380 work items, 383 work events, 10 total capability tasks and 31 capability events.

## Honest limits and next work

This is a finite batch scenario. All selected tasks are assumed ready at minute zero, with identical interchangeable workers, uninterrupted processing, and no arrivals, breaks, travel, setup, external equipment, abandonment or urgent clinical priorities. A prepared packet may have had earlier internal activity; explicitly selecting it models proposed additional work, not an inferred outstanding clinical obligation. Existing service-slot declarations are not converted into worker counts or rates. Task links lead back to the existing queue; the calculation changes no task and reserves no slot.

Saved comparisons become historical when the buyer portfolio, task versions, source dependencies or calculation change. Snapshot rechecking guards concurrent saves; the scenario store and upstream task stores are separate, so this is optimistic freshness verification, not an atomic cross-store transaction. Any subsequent change is detected on inspection.

Next extend explicitly supplied operational assumptions to service-specific resources and ready times, with pooling feasibility and a simple baseline exposed. Empirically calibrated arrivals/durations, verified staff capabilities, appointments, spillovers, clinically justified priorities and causal benefit remain absent. Broader clinical/benefit interpretation, model qualification and full product acceptance remain incomplete.

The ledger is unchanged and uncapped; this capability made zero provider/token-count calls. The exact diagnostic, helper-support and financial interpretation approvals remain pending separately after automatic-review rejection. No clinician review, external care, patient contact, source migration, generator change or external publication occurred. Runtime server: 4828, session 20767, port 8885.
