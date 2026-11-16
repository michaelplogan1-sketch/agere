# Appointment follow-through and protected comparison

Verified 2026-10-11T09:43:43.633896+00:00. Original-cohort v1.23 remains active. Full product acceptance and clinical validation are not met.

The Intelligence workspace now answers **“What follow-through is explicitly reported after the first received booking?”** for the complete population or an explicit patient. Saved reviews retain exact source identities and histories; users can inspect states, reopen receipts and prepare persistent internal review work. P-3002's booked/no_show history produced one unassigned local work item. HTTP and browser replay created no duplicate and recorded no patient contact or human review.

The endpoint [predeclaration](artifacts/agere-appointment-followthrough-v1/PREDECLARATION.md) was saved before population profiling and fitting. It selects one earliest received booking, fixes a next-day decision and receipt-day feature cutoff, and requires a mature scheduled-date +14-day receipt window. Explicit fulfilled/no_show reports have a scheduled-date through +7-day event window. Cancellation is separate. Missing, censored, contradictory, rescheduled or unmapped reports do not become negative labels. Exact same-source identity is required; no nearest-date matching or selection of a later favorable booking.

## Observed population and qualification

- 1,614 patients and 46,726 received appointment records: 1,415 explicit fulfilled, 182 explicit no_show, nine bookings not received before their scheduled date, eight ambiguous earliest bookings.
- 1,399 observations have usable prior evidence and target timing; 957 remain after original training-family and reserved-family protections. Exclusion reasons overlap. None of the 4,340 included baseline sources overlaps the index or target records, or falls after the feature cutoff.
- Development components and rows: train 599 fulfilled/76 no_show; calibration 117/18; development validation 130/17. Every partition passes the frozen row gates and added distinct-component gates. No outer or reserved evaluation was fitted or scored.
- Two frozen small logistic candidates reuse the existing 180-day evidence profile and calibration method, with and without source-arrival blocks. All five previous tasks remain unchanged and not fitted. No new model is promoted or used for patient scoring.

## Comparison and uncertainty

On 147 held-out development patients (130 fulfilled/17 no_show):

| Saved comparison | Brier | Log loss | AUROC |
|---|---:|---:|---:|
| Constant training prevalence | 0.102282 | 0.358206 | 0.500000 |
| Calibrated logistic | 0.101569 | 0.356064 | 0.559050 |
| Calibrated logistic without arrival blocks | 0.101568 | 0.356062 | 0.559502 |

These scores are close. A separate post-fit descriptive analysis uses 2,000 paired resamples of whole protected development components (seed 1729) with both candidate models fixed. Model-minus-constant Brier differences are about −0.00071; approximate 95% percentile intervals are **−0.00544 to 0.00337** and **−0.00543 to 0.00336**. Both include zero. They neither establish equivalence nor clear improvement. No statistical, clinical or commercial superiority is claimed.

The empirical-resampling foundation was checked in [Efron 1979, section 2](https://sites.stat.washington.edu/courses/stat527/s13/readings/ann_stat1979.pdf). Grouped pairing and these windows are engineering choices. Intervals assume independent/exchangeable protected components and condition on the fitted models; they omit training/calibration uncertainty, selection effects, unknown cross-group dependence and external validity. They are pointwise approximate intervals, not preregistered tests or clinical confidence statements. The runtime displays aggregate comparisons and limitations, not private ancestry or split assignments.

## Verification and preservation

**68 focused checks, 40 new.** Actual HTTP and browser population execution, state filtering, exact source inspection, work preparation/save/replay and persisted reload pass. The source audit independently verifies 46,726 hashes,186,904 literal fields, 1,597 explicit binary targets, 4,340 baseline clock checks and three Brier calculations. JavaScript syntax and browser console checks pass. One browser wait timed out during asynchronous reload; the next observed state showed successful completion, with no console errors.

All prior 381 work items / 384 events / 12 capability tasks / 35 events are intact; totals now 382 / 385 / 12 / 35. The broad preservation audit confirms historical clinical/source/reference/operational paths, all 837 ledger rows, 33 pinned source contracts and 19 separate Documents files. The five existing prediction reports are current. Protect 156 files, including the new fixed datasets, model weights, fitting reports, predeclaration and uncertainty receipt, using `artifacts/agere-appointment-followthrough-v1/protected-for-continuation.json`. The active warehouse remains 1,614 patients / 1,238,686 records; no generator or external corpus was edited.

No provider or token-count calls; new API cost $0. Spending remains uncapped. Four earlier exact outbound approvals remain pending and were not retried. No patient contact, external publication, enrollment, appointment booking or care delivery occurred.

## Remaining work

Clinical interpretation, reviewed guideline/payer applicability, useful predictive performance, uncertainty beyond this fixed development sample, temporal/external validation, calibrated operational inputs, production care-system adapters and full acceptance remain unfinished. This new endpoint is a received-status prediction experiment, not clinical-event risk or causal intervention benefit. The next work is:

Connect the new appointment source-review work to buyer care-navigation and follow-through responsibilities. Inspect existing capability matching, patient-context, coverage, participation and component-proposal contracts first; carry exact appointment histories and unresolved questions into service proposals, with cancellation/unknown/conflicting evidence distinct. Do not infer the cause of a no-show, current patient need, consent, benefit entitlement or clinical priority from the recorded status or research model. Exercise an actual matching and a mismatched/unavailable buyer-service path, plus an internal evidence handoff, without contacting patients or scheduling care. Then continue broader clinical/payer applicability and production adapter acceptance. Preserve the new fixed appointment dataset, both models and uncertainty report; no tuning on this development comparison or outer evaluations, no unchanged single-class refits. The four exact outbound approvals, 33 pinned routes, original v1.23, frozen Kev/d1 and separate Documents corpus remain protected. Full acceptance remains false.

[Machine-readable receipt](artifacts/agere-appointment-followthrough-v1/implementation-receipt.json) · [Source audit](artifacts/agere-appointment-followthrough-v1/source-audit.json) · [Live review](http://localhost:8885/stack?appointment=58b3cc22a169043c5c9693c893c7984f5d843a2c4726ec0a4968ef3aaff633d9)
