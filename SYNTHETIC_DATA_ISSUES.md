# Synthetic data issues log

Issues found in the synthetic release `artifacts/agere-healthcare-v123` while building and measuring Agere.
These belong to the **generator** (`/Users/mikel/synthetic-patient-ecosystem`, not edited from this repo).
Each entry separates likely defects from deliberate realism (traps that real warehouses have and Agere must handle).

Owner request (2026-10-13): flag anything that is a mistake in how the synthetic dataset is created.

## Likely generator defects (fix in the generator)

| # | Issue | Evidence | Impact on analytics | Suggested fix |
|---|---|---|---|---|
| D1 | **Missing discharge times on most inpatient stays** | 72 of 100 `encounters` type `inpatient` have no `end_time`; observation stays all have one | Length of stay, discharge-based follow-up windows and readmission gaps are unknowable for 72% of stays (Agere now leaves LOS empty rather than treating it as 0 days) | Always emit `end_time` on the completed version of inpatient encounters |
| D2 | **23-month hole in ED and inpatient activity** | Zero ED/inpatient encounters from 2026-11 through 2028-09, while primary care continues | Any year-over-year utilization, PMPM or readmission trend across 2027 is an artifact; the briefing flags it as a feed gap | Generate acute utilization continuously across the whole timeline |
| D3 | **Social screening and intake stop for 21 months** | `social_screens` none 2026-10..2028-06; `care_intake` none 2026-11..2028-07 | Screening rates and social-need trends over time are distorted | Continue screening cadence through 2027-2028 |
| D4 | **ED spike in one month** | 92 ED visits in 2026-09 vs 3-4 in the neighbouring months | Monthly ED series and change-point detection dominated by one month | Smooth acute-event generation over time (seasonality ok, single-month burst not) |
| D5 | **No appointment cancellations after 2026** | Cancellations: 61 in 2026, 0 in 2027-2029; reschedules only 13 total | Cancellation and access metrics are meaningless after 2026 | Keep cancellation/reschedule processes active in all years |
| D6 | **Every screened uninsured patient reports cost difficulty** | Uninsured: 108 screened, 108 positive (100%) vs 5-7% for insured | Financial-strain analyses by payer are degenerate | Draw cost-difficulty from a probability by payer, not a deterministic rule |
| D7 | **Problem-list conditions carry no dates** | All 6,929 `conditions` rows have no event date (onset present on all but 13) | Incident-condition and "new diagnosis in period" questions need onset parsing; partly realistic | Emit recorded/diagnosis dates, or confirm intentional |

## Deliberate realism (keep: Agere handles these)

| Trap | Evidence | How Agere handles it |
|---|---|---|
| Encounters arrive as in_progress + completed versions | 34,827 encounter IDs on more than one record | `visit` table: one row per encounter |
| Claims adjudication sequences (denied -> paid, paid -> reversed -> paid) | 959 claims denied then paid | `claim` table: first/final status, net paid |
| Appointment status versions | booked row + status rows per appointment | `appointment` table: final status |
| Lab name/unit variants, undated lab rows | 3 A1c names, eGFR unit spellings, 16,543 undated labs | concept library + canonical units; undated rows excluded from "latest" logic |
| Two social-needs instruments | `transport_difficulty_reported` vs `transport = unreliable` | `social_need` table; compiler matches either |
| Later reported ages | 1,446 demographic updates | `patient.age_latest`; cohorts use latest reported age |
| Legacy claim rows without claim_id | 1,315 rows | excluded from claim-level metrics, stated in the definition |
| ADT registrations alongside encounters | ED registrations in `adt` | one-per-day ED counting (HEDIS EDU) |

## Absent by design (Agere answers "unsupported" and names the gap)

Death/vital status, race/ethnicity, preferred language, income/FPL (fields present but undisclosed), CMS-HCC/RAF,
CPT/RVU (claims use local codes), patient-experience surveys.
If any of these should exist for demos (e.g. race/ethnicity for equity analyses, death for mortality), they need generating.

## Note on the public site (2026-10-13)
The site's "Did health change? Or did the records start arriving?" section is built on the hospital-summary gap and
abrupt start, which overlaps defect D2. Re-anchor that story once the generator is fixed.
