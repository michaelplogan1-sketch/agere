# Financial stocks, flows and source reconciliation — October 9, 2026

Agere can now reconcile received financial accounts across a complete population and compose that calculation with clinical review membership. This is a reusable `financial_accounts` graph operator with a separate adapter map; it is not a financial-hardship classifier or a contract-settlement model.

`financial_account_maps.json` maps account snapshots, patient payments/refunds, payer cash allocations and supporting claim/billing sources. The generic engine selects one unambiguous received snapshot per account, retains explicit same-patient links, and reports source timing or identity conflicts. Rows without direct account IDs can inherit an account only through an available, explicitly referenced submission/adjudication record of the correct type. Unknown bindings stay outside account totals.

Two exact integer-cent identities check the selected account statement:

- Patient responsibility − patient receipts + refunds − balance + patient credit = 0.
- Billed amount − contractual adjustment − self-pay discount − payer expected − patient responsibility − provider pending = 0.

Separate comparisons reconcile recorded patient receipts, refunds and payer cash against their received transaction reports through the selected snapshot's timestamp. Signed payer cash reversals remain signed; patient payment/refund amounts must be nonnegative integers. Units are explicit, with payer cash currency inherited only through its exact account binding. Unknown currency, noninteger amounts, unsupported status and ambiguous dates remain unresolved.

Account stocks are not summed across time. Adjudication and remittance amounts are not cash receipts. Missing or discrepant reports do not establish lost revenue, actual debt, hardship, collectability, attribution, treatment benefit or savings. Within-minute ordering and complete source capture remain unverified.

## Original-cohort results

Sources: `artifacts/agere-financial-accounts-v2/question.json`, `verification.json` and `http-verification.json`. The v1.18 warehouse and roster were not changed.

| Result | Synthetic received-source count |
|---|---:|
| Patients evaluated | 1,614 |
| Accounts evaluated | 26,837 |
| Reconciled received records | 17,031 |
| Unresolved snapshot | 5,148 |
| Other review required | 2,208 |
| No received account snapshot | 2,450 |
| Sources without unique account binding | 8,959 |
| Complete selected USD snapshots | 19,203 |
| Selected snapshots with missing currency/amounts | 36 |
| Patient-payment comparison differences | 17 |
| Payer-cash comparison differences | 12 |
| Refund comparison differences | 1 |

Difference counts are comparisons, not unique patients or proven losses. Account issue counts overlap. The source clock audit found **18,613 snapshot rows across 5,148 accounts** whose date differs from their timestamp's calendar date. Those exact fields are retained in the review receipt; an intended business-date or timezone explanation is not established. Agere does not silently choose one clock.

The clinical composition selects the **14** patients meeting the existing source-supported reduced-EF review criterion, then evaluates **496** received accounts: **300** reconcile, **66** have unresolved snapshots, **92** lack a received snapshot and **38** need other review. All **14** patient receipts were reused with no new financial patient computations. This is evidence composition, not confirmed HFrEF, treatment eligibility, financial assistance eligibility or program return on investment.

## Integration and verification

The local compiler, typed frontier catalog, graph execution and `/api/stack/financial-accounts?id=<receipt>` use the same calculation and immutable patient receipts. The workbench offers “Reconcile accounts and actual payment reports” and “Reduced-EF review population → financial sources”. Source inspection displays account-specific next evidence steps, exact date/timestamp conflicts, amount identities and linked received records. It does not create work items or send outreach.

Exact per-patient raw-record fingerprints provide reuse and affected-patient invalidation. The full cold run took **48.35136729106307 seconds**, loading **1,049,339** received source records. The repeat used the graph cache; the new selected-population composition reused financial receipts. Zero new paid API calls, model weights, source migrations, human reviews or external actions occurred.

**89 focused checks** pass, including clock/currency ambiguity, refunds, negative transactions, stock-versus-flow separation, linked account inheritance, stale evidence and full-population composition. The independent raw-record audit verified **382,958 explicit links**, **19,239 selected snapshot references**, **38,478 integer-identity calculations** and **37,467 cash source references**, with aggregate parity. Live HTTP and browser controls pass; exact patient financial receipts open and the checked browser error log is empty. Verification covers engineering/source fidelity, not clinical accuracy or economic outcomes.

`build_financial_account_stack.py`, `verify_financial_account_stack.py` and `verify_financial_http.py` reproduce the artifact and live-path checks. V1 remains preserved. V2 adds the exact conflicting snapshot fields and targeted next steps without changing the observed counts.

## Remaining work

The current model does not settle payer/VBC contracts, calculate program-attributable savings, adjust provider performance for case mix, infer benefit from an intervention or verify debt collectability. Versioned contract terms, denominators, attribution, payment periods and independently supported clinical/program outcomes are required. The next build should connect financial evidence and missing prerequisites to the existing immutable program/evidence-work path, with portable contract/input bindings and explicit abstention where terms or attribution are absent.
