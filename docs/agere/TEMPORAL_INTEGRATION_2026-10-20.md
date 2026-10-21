# Temporal semantics integrated locally — October 20, 2026

Activated `93409e8c75a3a33d2901df9ebbe1b7b78a25b3ea` on the existing connected local website at http://127.0.0.1:8885/site/ . Rollback `da83c23e0435cf74bb8b8991a464674107db19d6`. This is a bounded G3 engineering increment; G3 remains active, G5 remains incomplete, and all ten product areas remain in scope.

Integrated the isolated temporal candidate (`0835f13`, `cf6fd98`, policy clarification `85d65e0`) into the current website release. Current-dose evidence now preserves stopped/held status, undated evidence, same-date contradictions, formulation uncertainty and source IDs. Labels cannot replace regimen statements; strengths without quantities and ambiguous instructions do not become definite daily doses. Reference ranges are distinct from patient-specific targets. Appointment metadata distinguishes final received status from ever-no-show history, and raw catalog relation names work through the local grammar.

Owner review additionally reproduced incorrect magnitude parsing: `.5 mg` became `5`, `25 mg/5 mL` became `25`, `1,025 mg` became `25`, and negative/fractional ranges could be accepted. The final parser preserves explicit fractional dose magnitude and rejects these concentration/ambiguous-number/range forms. Before/after evidence and regression are retained.

All **204** relevant tests passed on both primary and independently frozen source. Fourteen actual-source grammar queries matched separate SQL, reopened/replayed saved answers, and made zero model calls. These cover patients, appointment final/history states and dates, claims, admissions, social observations and extracted barriers. An explicitly owner-authored contract reproduces the declared **21-patient** final-status access population and creates exactly that simulated work population, a local-template brief and manual work monitor with duplicate-free replay. This is an execution/workflow check, not a fresh language-interpretation result. No authored contract was installed as a question-specific production handler.

The activated website accepted a fresh `Count patients` submission and returned **4,115**, saved as `ANS-8357cdbf22b38c1ae5686aa6`. Provider ledger confirms zero calls. Release hashes, configuration SHA and source warehouse size/mtime match the preserved baseline. Service PID 94246, exec session 43831, log `/private/tmp/agere-live-temporal.log`. Existing app provider integration and credentials were not changed. Older contexts remain subject to existing policy-version invalidation.

Mike's verified clarification removes paid-provider credit restoration as a prerequisite for building. **No paid API calls were made for this owner integration**. The candidate lane's earlier paid failures and incomplete cost accounting remain in its original sealed receipt; they are not erased or counted as this increment's spend. No model, prompt optimizer or clinical role is declared qualified.

Remaining limits:

- Fresh unfamiliar-language correctness, the original clinical compound (EF coverage, current use, dose/barriers and conditional work), general temporal composition and generated-view meaning/latency remain incomplete.
- The 21-patient access result is an owner-authored contract execution check. It does not establish that a language model will choose that interpretation for the original question.
- Dose values are reconciled received documentation and reference arithmetic, not confirmed ingestion, individual dose suitability, maximum tolerated treatment or clinical adjudication.
- G3 remains active, G5 qualification remains incomplete and full ten-area acceptance is false. Hosted design preview remains separate from the connected local website.
- No paid calls were made for this increment. Prior lane hosted failures/costs remain historical evidence; further provider qualification is unverified and does not block local build work.

Next: G3: integrate source/correction learning and graph-cache dependency commits f5ad976 + 5edac69; verify local correction, conflict, source-change, API and connected-site behavior without paid calls. Then resolve fresh unfamiliar-language and original clinical compound completeness using local evidence and appropriate baselines. Live-model and independent clinical qualification remain unverified, not a credit-dependent build prerequisite.

Receipt: `artifacts/agere-temporal-integration-v1/implementation-receipt.json`. Separate generator/research/design and protected evaluations were not used or modified.
