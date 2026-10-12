# Current-cohort outcome learning and timing qualification

Built October 9, 2026 on the unchanged v1.18 original-cohort synthetic warehouse.
This adds executable target qualification, protected dataset construction and a
guarded local model-comparison path. **No model was fitted to this warehouse:**
all three current target definitions fail the declared data requirements.

## What is connected

Every existing `outcome → baseline` question now reports whether the received
observations can support an index-day prediction experiment. Each patient retains
the exact baseline and outcome receipts. The qualification distinguishes:

- No resolved index, missing baseline observations, incomplete calendar follow-up
  and an unresolved recorded target.
- Index information received after the declared index day. A later-arriving
  discharge/admission message cannot establish that an admission-time model knew
  the patient had entered the cohort. A timely original remains usable even when
  additional copies arrive later.
- A zero recorded label and a clinical negative. Source capture remains unknown;
  absence of a received endpoint never establishes absence of care.

Features and targets are exported separately, with exact join keys and hashes.
The existing original-source partition register excludes outer evaluation and
frozen Kev/d1 families before fitting or scoring. Remaining development rows are
grouped transitively by original source person, repeated narrative family,
shared source record and supplied household ID. Three deterministic development
subdivisions are made **inside the original training partition**. These are not
new independent clinical validation data. The public original-patient identity
register does not establish all household or semantic-template independence.

`build_observed_outcome_learning.py` defaults to the active warehouse. When a
qualified dataset exists, its fitter supports two local logistic comparisons:
with the baseline source-arrival-block features and without them. Each is compared
with the fitting-set prevalence baseline; vectorization/scaling use fitting data
only, probability calibration uses the calibration subdivision, and reported
scores use development-validation rows. The target and partition rules are fixed
in code. Minimum counts per recorded class are 10 fitting, 5 calibration and
5 development-validation observations. These are engineering requirements, not
clinical statistical guarantees. Models never promote themselves automatically.

The fitting branch was exercised on controlled engineering fixtures only. Those
results are not patient-data performance evidence. On the actual warehouse the
pipeline saves explicit `not_fitted` receipts and creates no predictor weights.

The actual `/stack` baseline result includes **Can these observations support
prediction?** and **Inspect protected outcome-model build**. The latter opens the
three source-bound qualification reports through `/api/stack/outcome-learning`.
Code, evidence, dataset and protected-register changes invalidate attachment
reuse. The earlier research forecasting artifact remains unattached.

## What the synthetic warehouse supports

All three questions evaluated all **1,614 patients**, without changing the
**1,049,339 received records** or the active release.

| Registered observed-record target | Baseline + calendar-mature label | Timing-eligible target rows | Eligible after development protections | Fit outcome |
|---|---:|---:|---:|---|
| Discharge after admission notification | 19 | 3 positive | 2 positive | Not fitted |
| Admission after discharge notification, source clock | 50 | 8 zero | 6 zero | Not fitted |
| Admission after discharge observation, explicit clinical clock | 50 | 9 zero | 7 zero | Not fitted |

The actual excluded late-index counts are **16, 43 and 42**, respectively. Both
discharge-index definitions have **1** incomplete follow-up window. No-index
patients are accounted for separately: **1,595, 1,563 and 1,563**. Exclusion reasons
can overlap and must not be summed into a population denominator.

The three post-protection development distributions are:

- Discharge-after-admission: fitting 2 positive; calibration and validation empty.
- Source-date admission-after-discharge: fitting 5 zero, calibration 1 zero;
  validation empty.
- Clinical-date admission-after-discharge: fitting 6 zero, calibration 1 zero;
  validation empty.

These numbers come from `artifacts/agere-observed-outcome-learning-v2/`:
`question-1.json` through `question-3.json`, each `dataset-*/manifest.json`, each
`learning-*/run.json`, and `verification.json`. No target/window was changed to
seek a more favorable class distribution. These are received-observation
definitions, not CMS readmission measures or adjudicated hospital outcomes.

## Verification and compute

`regression.log` records **68 passing focused checks**. They exercise late/unknown
index arrival, early copies, immature positives, separate no-index accounting,
transitive protected grouping, stable label-independent partitions, bad joins,
one-class refusal, both local fitting branches on fixtures, evidence changes and
dataset tampering. Counts are suite executions, not a cumulative unique-test total
across project milestones.

The independent full-cohort verifier checked **228, 2,469 and 2,519** included
pre-index source references for their exact patient and receipt/source-date
cutoffs. All joins and protected partition checks passed. Live API and rendered
browser flows passed with no new browser console errors. Screenshot capture had
previously failed; no pixel-layout verification is claimed.

Cold question execution took **38.13670516572893**, **34.13890304043889** and
**43.607516958378255** seconds in the recorded local run. These are individual
development measurements, not service-level promises. Existing input fingerprints
reused all 1,614 patients with zero raw source payloads rehashed; uncached baseline
assembly still reads the relevant patient sources. Hosted API calls and hosted
token charges for this milestone were **zero**. Local compute is not priced.

The incomplete `agere-observed-outcome-learning-v1` build is retained. It was
interrupted to correct an accounting issue that described no-index patients as
having incomplete follow-up. The v2 build and live reports contain the fix; v1
was not activated.

## Continue from here

The general target/dataset/comparison machinery is implemented; current-cohort
prediction qualification remains unmet. The next substantive addition is an
explicit **receipt-time decision landmark**: define what the system can know when
a late index arrives, then construct its subsequent endpoint window without
reusing outcomes that already happened as future labels. Keep clinical event time
separate from notification time. Predeclare that new task before inspecting its
outcome distribution; preserve this failed index-day experiment unchanged.

Broader clinical/payer program applicability and financial/provider/contract
accounting remain independent build work. Missing outcome classes do not justify
changing the other chat's generator, training on hidden truth, or transferring the
older expanded-cohort forecast into the active product.
