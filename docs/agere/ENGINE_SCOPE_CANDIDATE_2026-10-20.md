# Queueing scope integration candidate

G3 / B2. Candidate source: `e62bba6a52bd73b50f84bb4f6d052ba43d17dbad`, based on owner checkpoint `a2c6335`. This is an isolated candidate for owner review, not an activated release.

The shared request compiler previously rejected queueing analyses because the engine contract permitted zero population inputs. Direct execution also discarded the selected cohort and snapshot. In an ordinary independent fixture, a request for patient A through January 31 used three arrivals and an 80-minute mean service duration instead of one arrival and 30 minutes. An explicitly empty cohort also used those three arrivals.

Queueing now accepts the shared cohort, distinguishes empty from omitted membership, intersects organization and inclusive arrival dates, and excludes service completions after the snapshot. The event-window denominator includes days with no arrivals. Results expose their scope, and selected-cohort capacity explicitly assumes a dedicated queue. Explicit scenario inputs remain labeled as assumptions without verified patient linkage. Existing Erlang, Halfin–Whitt, and SIPP equations are preserved. A single observed duration can supply a mean, while its variability remains untestable.

Nested ED forecasting receives the same cohort and snapshot. Mismatched event definitions, organizations, or explicit event windows cannot silently replace that input. Such requests retain a labeled historical estimate with partial status and a missing-forecast explanation. The existing graph cache now also includes the nested forecast model identity.

Verification on the committed candidate:

- 143 tests passed in 22.466 seconds, covering queueing mathematics, scope/admission, shared request execution, graph/cache behavior, and existing general analysis/workflow regressions.
- Nine read-only checks against the received synthetic warehouse matched independent SQL counts across ED, outpatient, kept appointments, admissions, date windows, and empty membership. Warehouse size and modification time were unchanged.
- Authored shared contracts verified saved-cohort execution, different cohorts, replay, empty results, partial forecasts, and forecast-model cache invalidation. These establish execution behavior, not fresh language interpretation.
- Zero paid provider calls; no protected evaluation, generator, research, design, active runtime, or primary control changes.

Evidence: `artifacts/agere-engine-scope-v1/implementation-receipt.json`, `before-after.json`, `final-tests.txt`, `test-run.json`, and `received-source.json` in the candidate workspace. Initial failed checks and fixture repairs remain in that directory. The initial schema test omitted catalog context; it was corrected without weakening the product checks.

Next: the sole build owner reviews the complete diff, integrates once with rollback, and verifies the existing `/ask` path. G3 remains active. The original clinical compound request, broad language correctness, generated-output reliability, and independent model/clinical qualification remain open. This increment neither completes the full charter nor qualifies operational staffing.
