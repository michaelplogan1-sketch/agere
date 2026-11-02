# Verifier cache identity — 2 November 2026

Product `f1a87d9f7d02ef3b09c0c37b8f10b8ec0b03de5a` fixes a reproduced source-to-result defect: two executions of the same financial program changed verifier identity solely because SQL execution time changed. The native financial proof now reuses the original validated judgment with zero new provider attempts. The current clinical answer, financial answer and patient PDF also pass their scoped app checks.

Only the reserved top-level `elapsed_s` metric on a genuine native `query_sql` result is excluded from its verifier digest. Complete raw tool receipts remain intact. Business rows, including a column named `elapsed_s`, source records, query text, claims, population, date, definitions, model and typed request contract still determine identity.

The existing support-packet path emits an exact legacy view alongside the stable view. System One validates that only the declared native receipt digests differ before trying the exact old request. A small index points the stable request to the original validated provider attempt. It records the target row digest and proof digest, preserves the original request identity and usage, and creates no fake billable success. Exact current responses take precedence. Actual failed or pending requests, explicit retry authorizations and changed targets cannot be bypassed. Read-only legacy lookup preserves the original judgment without writing the index.

| Workflow check | Measured result |
| --- | --- |
| Financial native execution | The full 22-action program executed twice independently. All 22 raw SQL timings differed; every non-timing result value and the verifier text were exact. |
| Exact retained provider reuse | Stable key `ab483da9…` reused original validated request `82e22acc…`; zero new provider attempts. |
| Financial values | All 12 rows preserve their previous values and independently reconcile to received source counts and integer cents. The answer remains answered. |
| Current clinical execution | All 26 native actions re-executed. All 57 patient-table rows, 66 packet judgments and 1,497 field-feedback entries remain exact. The answer remains partial/unverified. |
| Pending/failed attempts | Two unsuccessful Perplexity steps now explicitly report the prior-attempt block rather than the old cache-only harness's prepared state. No judgment or success was inferred. |
| Actual app and PDF | Current patient and financial contexts reopen without stale/invalidated flags. The 91-page patient PDF is byte-identical: `a68563893af394d63b00c501759abce9f25812dcca3b073dafdb0f69f5eb7a18`. |
| Accounting | All 27,274 original call rows remain exact: 26,996 validated, 272 failed, 6 pending. Three cache-index bindings point to actual validated attempts; no call rows were added. |

The financial proof took 22.274 seconds and the clinical native/review/delivery proof took 104.014 seconds. These are targeted confirmations, not ordinary cold-start clinical benchmarks. They do not establish a whole-clinical latency or cost reduction. New provider charges for this increment are $0; compute, development, prior attempts and actual total economics remain separate and unqualified.

Validation includes 193 connected tests (192 passed and one live test skipped), followed by two additional source/query/claim/model/transport/provider/option invalidation checks. The complete 14-test cache suite passes. This is 195 unique checks, with one live skip. The pinned document review, span repair and model-policy files remain byte-exact against `fa28466`. No thresholds, weights, clinical interpretations or clinical full-question bottles were promoted.

Two initial harness failures are retained. The first financial harness compared a pre-display answer with a displayed answer containing an additional currency limitation; the verifier correctly missed that changed input. The first clinical comparison rejected the two expected protected-attempt metadata transitions. Corrected proofs use the identical pre-display financial answer and require exactly those two unsuccessful clinical step transitions while keeping every clinical conclusion and other step exact. Product code did not change between either failed proof and its successful follow-up.

The owned candidate runs product `f1a87d9f7d02ef3b09c0c37b8f10b8ec0b03de5a` on localhost:8890; primary :8885 and main remain unchanged. Current clinical context is `ANS-6035426ea8ed83a4c266123f` with patient deliverable `DLV-7FEB1C46536E`. Current financial context is `ANS-b30f93ac3371cd65ff54a225` with deliverable `DLV-96F87DC45DBA`. The private draft PR remains unmerged and the scheduler remains paused.

G3 remains active. The prior full nine-of-nine engineering matrix and executable recovery drill remain pinned to `e400bb5`; this delivery supplies current scoped native/API/PDF evidence and does not restamp that matrix. All ten acceptance areas remain. Independent clinical/source-family/model/outcome qualification, acceptable measured whole-clinical performance and authentic total economics remain open. Customer deployment authority is separate. OWASP/HHS work remains deferred.

Next: address the retained clinical source-to-claim qualification gap through one existing typed assertion/state role and equivalent source-bound baselines. A supported unknown is acceptable; missing wiring is not. Keep the original question and 57-patient cohort, preserve each field obligation and source/date/entity binding, and require measured correct/unknown/error behavior before any default, learned-weight or whole-question reuse promotion. Do not repeat the full provider campaign, change thresholds to manufacture coverage or substitute a component catalog for a connected workflow result.

Evidence is sealed in `artifacts/agere-verifier-cache-identity-2026-11-02/delivery-receipt.json`; actual native results and failed attempts remain in that directory. The existing finite engineering/recovery baseline is documented in `docs/agere/CURRENT_ENGINEERING_RELEASE_2026-11-02.md`. The preceding role-input diagnosis is in `docs/agere/ANS-6035426ea8ed83a4c266123f_ROUTING_DIAGNOSIS_2026-11-02.md`.
