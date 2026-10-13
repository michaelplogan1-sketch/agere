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
| D8 | **Glycemic control is implausibly good** | Only 2 patients ever have an A1c > 9% (max 11.8) among ~230 diabetics; 28 have a latest A1c >= 8 | Poor-control (HEDIS GSD >9%) analyses and outreach demos have almost no population | Draw A1c from a realistic diabetic distribution (US: roughly 15-20% > 9%) |
| D9 | **Near-absent modern HF therapy (GDMT)** | Among ~300 heart-failure patients: SGLT2 inhibitors 4 patients ever (none dispensed in 2029), MRA 4, ARNI 13 dispensed; GLP-1 agonists 2 patients overall | GDMT-gap and cardiometabolic outreach analyses (the site's HFrEF story) have almost nothing to find; unrealistic for a 2026-2029 timeline | Prescribe SGLT2i/MRA/ARNI/GLP-1 at realistic, varied uptake (with documented contraindications and gaps) |
| D10 | **Care management enrolls nearly all Medicare 65+ patients** | e.g. 58 exposed vs 22 unexposed in the female 65-74 stratum; 160 of 231 exposed patients have no comparable unexposed match | Program evaluation (did care management work?) cannot find comparison groups; real programs target a subset by risk | Enroll a realistic, risk-targeted fraction with documented eligibility/assignment so comparisons are possible |
| D11 | **No new medication starts after the medication feed begins** | Every statin/SGLT2 first dispense falls inside the first months of the feed (2026-09); no incident users later | "Effect of starting X" analyses and new-start adherence measures have no population | Generate new starts (and discontinuations) throughout 2027-2029 |
| D12 | **No real provider/practice variation** | Hierarchical model finds all 80 PCPs statistically indistinguishable on ED use (between-PCP variance ~0, median shrinkage 97%); all 8 practices indistinguishable on A1c control | Fair-comparison, hotspot and "which practice is the outlier" analyses have nothing real to find | Add PCP and practice random effects (utilization, quality, cost, prescribing) with a few genuine outliers |
| D13 | **Coverage never changes** | One coverage record per patient (Aug 2026), never refreshed; no enrollment churn | Member months, attribution changes and churn analyses are trivial; Medicare cohort small (287) with low cost vs real Medicare | Generate enrollment periods, payer switches, disenrollment and realistic Medicare cost levels |
| D14 | **No incident diagnoses** | Every first diabetes record is dated 2026 (feed start); same pattern as D11 for medications | "New diagnosis" pathways, time-from-diagnosis survival and incidence questions have no population | Generate new diagnoses throughout 2027-2029 |
| D15 | **Acute volume too low to analyse** | ~1 ED visit per practice-month; 169 stays in total; 30-day return to acute care 1 of 49 | Demand forecasts, practice tickers and readmission analytics are dominated by noise | Scale acute utilization to realistic rates (e.g. ~400-600 ED visits / 1,000 / year for this mix) |
| D16 | **No seasonality** | Acute and visit series show no winter respiratory peak | Seasonal forecasting cannot be exercised or validated | Add seasonal effects (respiratory winter peaks, summer dips in elective care) |
| D17 | **Implausible clinical values** | 36 legacy echo EF values out of physiologic range; a negative cholesterol | Fine as a planted data-quality trap if intended; otherwise contaminates clinical cohorts | Confirm intentional, or bound values |
| D18 | **Almost no out-of-network leakage** | 10 outside-organisation transitions out of 10,354; zero PCP visits at a non-attributed practice | Leakage / network-integrity analyses (a core VBC question) have nothing to find | Generate realistic leakage (outside ED, specialists, PCP switching) |
| D19 | **Lab vs diagnosis disagreement is extreme** | 474 patients with HbA1c >= 6.5% and no diabetes diagnosis vs 230 diagnosed | Under-coding exists in real data but not at 2:1; distorts quality and risk analyses | Reduce to realistic under-diagnosis (~20-25% of lab-defined) |
| D20 | **No outreach outcomes** | Worklists cannot be linked to contact attempts and results | Outreach bandit and engagement calibration cannot learn | Emit outreach attempts with outcomes (reached, engaged, declined) and later utilization |
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
Requested 2026-10-13 for the stack roadmap: death records (mortality/survival), CMS-HCC diagnoses with RAF (risk adjustment and upcoding detection), race/ethnicity and language (equity analyses).

## Note on the public site (2026-10-13)
The site's "Did health change? Or did the records start arriving?" section is built on the hospital-summary gap and
abrupt start, which overlaps defect D2. Re-anchor that story once the generator is fixed.
