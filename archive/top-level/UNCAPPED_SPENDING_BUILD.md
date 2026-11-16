# User-authorized uncapped API spending — October 10, 2026

The user explicitly stated: “this should have no caps on spend at this time.” The active Agere provider ledger now has **no monetary spending cap**. No larger substitute ceiling was introduced. Existing authorized integrations remain the scope; this is not a purchase of new services.

The prior $50 ceiling was assistant-selected. Its own saved authorization entry records an October 8 user instruction to continue past $10, followed by an assistant-chosen $50 operating guardrail. The new instruction supersedes that ceiling. The earlier $0.33 figure described conservative reservation headroom, not actual account funds or an invoice balance.

`BudgetLedger.remove_spending_cap()` records the exact user authorization and persists SQL NULL for the cap. Public receipts use `spending_limit: uncapped`, `authorized_cap_usd: null` and `remaining_reservation_usd: null`. They serialize as strict JSON. Existing connections reload policy when checking a request. Ordinary ledger reopening cannot silently reintroduce a finite cap.

All 691 existing call records, imported accounting holds, usage/cost estimates and failed/pending statuses are retained unchanged. No settlement or release of reservations was necessary. Duplicate successful requests replay; pending/failed requests still cannot silently retry. Accounting reservations remain finite, nonnegative values. Context/source boundaries, request-size checks, live-route controls and bounded task execution are unchanged; these are not monetary ceilings.

The preserved synthesis engines have numeric preflight checks that are part of their saved-answer code identities. The public synthesis dispatcher now supplies an internal numeric compatibility view for uncapped policy. That internal view does not enter API or persisted ledger receipts. This keeps the exact original synthesis implementations, inputs, saved identities and paid answers usable without weakening source checks or paying to recreate answers. The historical verification entry points now use the shared dispatcher.

The source-plan API treats uncapped policy as eligible for authorized dispatch, and the workspace displays **No spending cap. Usage is tracked.** It no longer displays a misleading remaining balance or calls the estimate a spending ceiling in uncapped mode. The single existing continuation heartbeat retains the user's instruction and will not impose a new assistant-selected cap.

Verification:

- 126 focused unittest executions pass, including uncapped persistence, strict JSON, stale connections, history retention, duplicate/failed-call protection, finite-policy regressions and both synthesis contracts. These are overlapping suite executions, not a clinical-quality metric.
- JavaScript syntax passes. Actual API checks preserve and replay all three existing saved answers with zero new inference calls.
- An unsynthesized 26-unit scope, four source passes and up to eight inference calls is now dispatchable; no new inference was needed to test eligibility.
- Actual browser flow shows the no-cap policy and an enabled interpretation button; no console errors. Screenshot: `artifacts/agere-uncapped-spending-v1/uncapped-plan.jpg`.
- The entire usage ledger is unchanged apart from the authorization entry/cap. No new paid calls, human reviews, work events, source migrations or model changes.

Runtime: loopback port 8885, owned server PID **77662**, tool session **44988**. The next substantive product task remains typed evidence-need dispositions and reviewed source dependencies. Whole-stack status was already delivered in `AGERE_STACK_STATUS_2026-10-10.md`; its spending paragraph is corrected to this current policy. Full product acceptance remains incomplete.
