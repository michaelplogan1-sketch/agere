# Queueing scope integrated into Ask — October 20, 2026

Activated `8e83d0d945486e6caa9a4921ee2b8bd395e8fc5e` at http://127.0.0.1:8885/ask, Mike's identified product surface. Rollback is `7b9f8fead524550459a07d783cabd32ec86e1925`. Existing design, app integration, configuration and original warehouse are preserved. This is a bounded G3 engineering delivery; full MVP and clinical qualification remain incomplete.

Queueing now admits the shared planner's selected population and preserves it through execution, including an explicit empty set. It applies the snapshot and inclusive event window, excludes service completions after the snapshot, and includes zero-arrival dates in rate denominators. Subset estimates disclose the dedicated-queue assumption. Optional automatic forecasts must match the population, ED source and event clock. A mismatch produces an explicit partial historical answer rather than presenting the historical estimate as the requested forecast. Nested demand-model identity participates in cache dependencies. Existing Erlang/SIPP formulas are retained.

Integrated lane source `e62bba6` and documentation `44c0bcf` as `8d3218e`/`81ee139`. Owner commit `8e83d0d` fixes four actual-browser findings: positive arrivals rounded to zero, peak rate/load rounded to zero, scope hidden in method details, and a misleading empty-cohort follow-up option for analytical summaries. Overview now shows selected patient count, inclusive dates, snapshot and observed arrivals. Summary-only follow-up is disabled consistently with the existing backend guard. **Following up on the original analytical input cohort after a summary remains a separate B2 gap; this increment does not implement it.**

Evidence:

- All **226 tests passed** in primary source (47.029 seconds) and the frozen release (48.353 seconds). Explicit modules cover queueing, graph contracts/execution, shared planning, general-analysis workflow, Ask UI, answer scope/dependencies and healthcare learning. No broad discovery or protected evaluation was run. Existing NumPy empty-array and an imported risk-factor file ResourceWarning remain in the logs.
- Nine read-only received-source checks match independent SQL for ED, clinic, kept appointments and stays, including date and empty scopes. Original warehouse size and mtime are unchanged.
- Owner-authored shared contracts on an isolated copy of the received warehouse produce nine 2029 ED arrivals for three selected patients and six after changing to two; empty scope stays insufficient, replay matches, and mismatched forecasts remain partial. These are execution fixtures, not automatic language-interpretation qualification. Four cases plus replay took 5.3904 seconds, not a representative end-to-end latency benchmark.
- Actual candidate browser shows three selected patients, 2029-01-01 through 2029-12-31 inclusive, snapshot 2030-05-27, nine observed arrivals and 0.172 arrivals/week. Forecast mismatch is visibly partial and historical. Before/after screenshots and DOM are preserved. The existing chart axis-label contrast is still a presentation limitation.
- Live Ask check `Count patients` returned **4,115** in **0.5 seconds**, current context `ANS-9f08eafe1dcae77d6b541290`. Live source hashes, updated page controls, learning API, preserved config and original warehouse stat checks passed. PID96834, session85653, log `/private/tmp/agere-live-queueing.log`. Candidate8918 stopped.

The first owner-authored fixture omitted the population/window phrase from its analysis quote. The compiler correctly rejected it; the initial failure is retained. Completing the authored quote fixed the fixture without relaxing a product guard. Initial and final test/verification evidence are retained separately. No authored answer or test correction was copied into the active warehouse.

Zero paid provider calls and zero recorded API cost for this increment. Existing app credentials/integration are unchanged. Credits are not a prerequisite. Local deterministic/source checks do not establish language, clinical, staffing-policy or real-world operational suitability. Source-scoped memory and feedback capture remain distinct from automatic weight training or external source refresh. Prior lane evaluation-hygiene disclosures remain unchanged.

Remaining blockers:

- **B1 — Clinical compound and current state** (open): Original EF/current-therapy/dose/barrier question and conditional work are not demonstrated complete.
- **B2 — Unfamiliar and multi-domain composition** (open): Broad natural-language correctness and temporal composition are unproven. Queueing cohort/date admission, execution and forecast mismatch defects are repaired; post-summary follow-up using the original analytical input cohort remains unimplemented.
- **B3 — Generated views and response time** (open): Generated output meaning and representative end-to-end latency have unresolved failures. Queueing overview now exposes scope and preserves small positive rates; chart axis-label contrast remains a presentation limitation.
- **B4 — Independent model and clinical qualification** (unverified): Local tests do not qualify model interpretation, clinical correctness or real-world use.

All ten acceptance areas remain in scope; G3 active, G5 incomplete, full acceptance false. Synthetic only; no real care, outreach, payment, external publication, new service, protected evaluation or generator/research/design edits. Next action remains the original clinical/current-state compound and broad scope semantics through the shared path.

Receipt: `artifacts/agere-queueing-integration-v1/implementation-receipt.json`.
