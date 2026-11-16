# Work cost scenarios

Engineering checkpoint 2026-10-11T05:09:30.077102+00:00. Full acceptance and clinical validation remain false.

Buyers can now attach explicit USD hourly rates and one-time batch costs to saved resource or dependency scenarios. Exact rational-cent arithmetic compares occupied-task costs and keeping staff available, through the scenario horizon and whole-batch completion, against the original baseline. Fixed costs are counted once, averages retain their exact selected-task/patient denominators, and blocked batches have no complete-batch cost estimate.

## Actual executable path

Care capabilities → saved capacity comparison → saved resource or dependency scenario → Compare the cost of this work. Supply a cost question, assumption provenance, a USD rate and source for every pool, and an explicit list of fixed costs. A separate immutable result retains its exact original question, task versions, resource/dependency assumptions and baseline. Original scenarios and work are unchanged.

The dependency example uses hypothetical USD 120/hour behavioral and USD 60/hour respiratory rates plus USD 20 batch setup. In the central case, occupied cost through minute 30 changes from USD110 to USD80, while available-staff cost stays USD110. That difference reflects less work performed by that point, not savings. Through central batch completion, occupied cost is USD160 and available-staff cost is USD310 in both alternatives. Under high durations, the handoff extends batch completion 90→100 minutes and available-staff cost USD350→390. These supplied amounts are not payroll, payer payments, clinical benefit or ROI.

Rates parse as exact decimal strings; minute costs retain rational cents. Aggregate displays round half-up to cents without summing rounded rows. Whole-batch available-staff costing assumes every declared worker is retained from their availability until the overall batch end, including idle teams. Fixed cost is applied once in each alternative window/view. Per-task/patient costs are arithmetic scope averages, not allocations or charges. USD is the only supported currency in this version.

71 focused checks (16 new) passed before financial-context integration; the final 97-check combined suite also passes. It tests exact rounding/conservation, idle/occupied time, fixed costs, blocked batches, both parent types, invalid currency/units/missing rates, concurrency, stale work and immutable history. Actual browser save, API replay/invalid-input checks and four task-link targets pass. No captured browser errors. [Receipt](artifacts/agere-work-cost-v1/implementation-receipt.json) · [Screen](artifacts/agere-work-cost-v1/cost-comparison.png).

Received account context is now linked separately in [WORK_COST_CONTEXT_BUILD.md](WORK_COST_CONTEXT_BUILD.md). Measured costs, signed contract terms, program attribution, conditional payment and realized outcomes remain gaps.
