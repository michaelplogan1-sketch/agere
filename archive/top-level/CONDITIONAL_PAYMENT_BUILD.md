# Conditional payment scenarios for saved work costs

Engineering checkpoint 2026-10-11T05:27:36.072161+00:00. Full product acceptance and clinical validation remain false.

Saved work-cost questions now accept versioned supplied USD payment terms, an exact payment-unit definition and performance period, and explicitly selected hypothetical payable patients, tasks or one batch. The fixed original task population, cost window/basis and duration cases remain bound. Exact rational-cent calculations expose hypothetical receipts, receipts minus modeled cost, required rates rounded upward to whole cents, and sensitivity across the finite selected unit scope. No eligibility is inferred from task completion or received accounts. Seven contract/attribution/outcome prerequisites remain unresolved in the two-patient demonstration.

## Executable path

Care capabilities → saved capacity/resource/dependency scenario → saved work cost question → Compare conditional payments for this work. The buyer supplies a question, term identifier/version/source, USD rate, unit definition, explicit performance dates, cost basis/window, period alignment rationale and the exact units assumed payable. No units are preselected. A saved same-buyer term identifier/version cannot be overwritten with changed terms; edits require a new version. The new version does not silently supersede or delete the old one.

Actual local synthetic demonstration: terms version 1 assumes USD180 once per selected patient and both patients payable. Central available-staff cost is USD310; hypothetical receipts are USD360; conditional difference is USD50. Version 2, saved through the browser, assumes USD200 but only P-7193 payable: the same cost is USD310, receipts are USD200 and difference is USD−110. Its fixed-work sensitivity spans zero/one/two assumed units, with differences USD−310/−110/+90. These numbers are engineered assumptions, not a CMS rate, signed contract, eligible patient classification or observed program result. The original four tasks and their two patients remain fixed.

The exact payment rate required at a supplied nonzero unit count is retained as rational cents; the minimum payable whole-cent rate rounds upward. Zero units never divide by zero or receive a required-rate claim. Zero payment with positive cost cannot cover that cost; zero cost has a zero count threshold. Blocked whole-batch work receives no complete-batch balance. Low/central/high are inherited duration assumptions, not confidence intervals. Sensitivity changes payable count within the same fixed work, not the amount or cost of work delivered.

Versioned development evidence requirements are reused from the existing accountability contract: effective agreement, performance period, measure/payment rule, and per-patient attribution and outcome links. All seven remain unresolved. The supplied period and alignment text are assumptions. Received financial accounts remain separately inspectable from the cost question; no account is designated program revenue or netted against costs.

## Verification

125 focused checks pass, including 19 new conditional-payment checks and nine separate payment-comparison checks. Arithmetic checks cover 60 rate/count/cost combinations, exact thresholds, zero and fractional cases. Integration checks cover typed money/date/scope validation, immutable terms, concurrent replay and conflicting version writes, blocked work, stale parent/code, integrity and mid-save dependency changes. The actual HTTP path saves/replays version 1 and rejects four invalid/authority/version requests; the browser enters and saves version 2 and displays its sensitivity and unresolved prerequisites. Source accounts and all work remain preserved. Python and changed JavaScript syntax pass; no captured browser errors.

[Receipt](artifacts/agere-conditional-payment-v1/implementation-receipt.json) · [HTTP evidence](artifacts/agere-conditional-payment-v1/http-verification.json) · [Screen](artifacts/agere-conditional-payment-v1/payment-sensitivity.png).

## Limits

This version supports flat, once-per-selected-unit USD assumptions only. Patient-months, actual payer clauses, case mix, risk adjustment, withholding, settlement, realized revenue, clinical benefit and verified period alignment are not implemented. Positive arithmetic does not imply economic or clinical value. No service, task state, staffing, eligibility or external action changes. See the compatible saved-scenario comparison in [PAYMENT_COMPARISON_BUILD.md](PAYMENT_COMPARISON_BUILD.md).
