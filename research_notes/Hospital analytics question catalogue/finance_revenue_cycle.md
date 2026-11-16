# Finance, revenue-cycle and cost questions US hospitals ask of their own data

Research date: 2026-11-16. Scope: US hospitals and health systems. Primary sources read in full this session: the HFMA MAP Keys 2023 Edition definitions PDF (all 29 keys), the HFMA MAP Award winner statistical data PDF (2016–2018), and the Kaufman Hall National Hospital Flash Report, April 2026 Metrics PDF (metric list and margin index charts). Other figures come from secondary trade press summarising rating agency, Kaufman Hall, Kodiak and survey reports; these are marked "secondary". Anything marked **[unverified]** is standard practitioner knowledge that I did not confirm against a source this session.

Data-source shorthand used below: **PFS** = patient financial/accounting system; **ATB** = aged trial balance; **GL** = general ledger; **IS** = income statement; **BS** = balance sheet; **835** = electronic remittance advice (with CARC/RARC denial reason codes); **837** = claim file; **CPT** = claims processing/scrubber tool; **CA** = cost accounting/decision support; **Sched** = scheduling system; **Encoder** = coding/DRG grouper.

---

## Revenue cycle: HFMA MAP Keys, denials, prior authorization, underpayments, price transparency

### Takeaway
HFMA's MAP Keys are the de facto standard definitions: 29 KPIs in five groups (Patient Access, Pre-Billing, Claims, Account Resolution, Financial Management), each with a fixed numerator, denominator, inclusions/exclusions and preferred data source (GL preferred). Key nuance for any analytics product: the MAP Keys contain *remittance denial rate* (AR-5) and *denial write-offs* (AR-6) but **not** an "initial denial rate". That metric comes from HFMA's separate Claim Integrity Task Force. Current market benchmarks are mostly vendor-derived (Kodiak, Ensemble) and show initial denials around 11–12% in 2025, with Medicare Advantage more than double traditional Medicare.

### Cited Findings

**MAP Keys structure and data-source rule**
- There are 29 MAP Keys in 5 groups, and one set applies to hospitals, ambulatory, physician, post-acute and IDS. The GL is the preferred source because it is auditable, and the same source must be used every month unless systems change. — [HFMA MAP Keys 2023 Edition](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

**MAP Key inventory (definitions verbatim in substance from the 2023 Edition)** — all rows from [HFMA MAP Keys 2023 Edition](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

| Code | Metric | Numerator / Denominator (source) | Key inclusions / exclusions | Decision it drives |
|---|---|---|---|---|
| PA-1 | % patient schedule occupied | Slots occupied / slots available (Sched) | Overbooks count, so it can exceed 100%. No-shows/cancels count as occupied unless the slot was reused. Excludes non-patient blocks | Capacity/template management |
| PA-2 | Pre-registration rate | Encounters pre-registered / scheduled encounters (PFS) | Pre-registered means at minimum all demographic and insurance fields are complete. Excludes walk-ins, ED, unscheduled | Front-end staffing, denial prevention |
| PA-3 | Insurance verification rate | Verified encounters / all registered encounters (PFS) | Denominator includes ALL encounters, ED and walk-ins included | Eligibility denial prevention |
| PA-4 | Service authorization rate, IP/OBS | IP/OBS encounters authorized before claim release / IP/OBS encounters requiring auth (PFS) | Retro-auth counts if obtained before claim release. Denominator = authorized + denied for no auth | Prior-auth workflow, auth denials |
| PA-5 | Service authorization rate, OP | Same as PA-4 for outpatient | Referral approval is treated the same as auth. Series accounts count where a new auth is needed | Same |
| PA-6 | Conversion of uninsured to third-party funding | Uninsured converted to third-party coverage / uninsured discharges+encounters (A/R) | Medicaid, COBRA, WC, TPL, SSI etc. Charity conversions don't count. Pending Medicaid applications excluded | Eligibility/Medicaid-enrolment vendor value |
| PA-7 | POS cash collections | Patient POS payments / total self-pay cash collected (A/R or GL) | POS = current-encounter cash before, at or ≤7 days after discharge, plus prior balances collected at a new service. Excludes refunds and routine payment plans | Front-end collection policy |
| PB-1 | Days in DNFB | Gross $ discharged not final billed / avg daily gross patient service revenue, single month (unbilled A/R, IS) | Month-end snapshot. Excludes in-house, FBNS, late-charge bills, rebills | Coding/HIM backlog, cash flow |
| PB-2 | Days in FBNS | Gross $ initial 837 claims held in scrubber / avg daily GPSR (CPT, IS) | Initial claims only. Includes payer front-end rejections (not denials) | Edit/scrubber workload |
| PB-3 | Days in DNSP | PB-1 + PB-2 | — | End-to-end billing lag |
| PB-4 | Total charge lag days | Σ(posting date − service date) per charge code / count of charge codes (PFS) | A count of days, not dollars | Charge-capture workflow by department |
| CL-1 | Clean claim rate | Claims passing edits with no manual intervention on first scrub / claims accepted into the scrubber (CPT) | Primary, secondary and tertiary 837s. Excludes direct-to-payer submissions and print/hard-copy warnings | Front-end and coding data quality |
| CL-2 | Late charges % of total charges | Gross charges posted >3 days after service date / total gross charges (PFS) | Absolute value of late debits plus credits, not netted | Charge capture, rebills |
| AR-1 / AR-2 | Aged A/R % of billed A/R (total / by payer group) | Billed A/R in 0-30, 31-60, 61-90, 91-120, >120 day buckets / total billed A/R (ATB) | Aged from discharge (IP) or DOS. Excludes credit balances, DNFB, in-house | Follow-up prioritisation by payer |
| AR-3 / AR-4 | Aged A/R % of total A/R (total / by payer) | Same but adds an "unbilled" bucket (in-house + DNFB) | — | Same |
| AR-5 | Remittance denial rate | Claims denied / claims remitted (835 or paper) | "Actionable" denials only. Includes initial AND appeal denials and zero/partial pays with a denial code. Excludes patient-responsibility, RAC recoupments, duplicates, shadow claims. Claim-level, not line-level | Denial prevention by payer/reason |
| AR-6 | Denial write-offs % NPSR | Net $ written off as denials (net of recoveries) / avg monthly NPSR, 3-month (PFS, IS) | Includes lost or unappealed RAC denials. Excludes non-covered services and patient responsibility | Final revenue leakage from denials |
| AR-7 | Bad debt | Provision for bad debt on the IS / gross patient service revenue | IS provision, not A/R write-offs | Collection and financial counselling effectiveness |
| AR-8 | Charity care | Charity care on the IS (may be a footnote) / GPSR | Excludes community benefit | Financial assistance policy administration |
| AR-9 | Net days in credit balance | $ in account-level credit balances (absolute) / avg daily NPSR, 3-month | Excludes pre-service deposits, in-house, unapplied cash | Refund compliance, posting accuracy |
| FM-1 | Net days in A/R | Net patient A/R (BS) / avg daily NPSR, 3-month (IS) | Includes Medicare DSH and IME on a DRG basis and patient-specific settlements. Excludes lump-sum settlements (Medicaid DSH, DGME, cost report), 340B revenue not booked as patient A/R, and capitation/premium revenue | Overall cash conversion |
| FM-2 | Cash collection % NPSR | Patient service cash collected net of refunds (GL) / avg monthly NPSR, 3-month | Includes bad-debt recoveries and Medicare DSH/IME. Excludes capitation, DGME, Medicaid DSH, non-patient cash | Fiscal integrity, revenue-to-cash yield |
| FM-3 | Uninsured discounts | Uninsured discounts before charity/bad debt / GPSR | Excludes prompt-pay discounts and post-insurance self-pay discounts | Self-pay pricing policy |
| FM-4 | Uncompensated care | (AR-7 + AR-8 + FM-3) / GPSR | — | Mission and payer-mix exposure |
| FM-5 | Case mix index | Σ MS-DRG relative weights / IP discharges in month (Encoder) | Medicare MS-DRG weights applied to all payers. Updated until all cases are coded | CDI/coding, acuity-adjusted cost and payment |
| FM-6 | Cost to collect | Total revenue cycle cost / total patient service cash collected | — | Rev-cycle operating budget, outsourcing |
| FM-7 | Cost to collect by functional area | Same, split by patient access, billing, collections etc. | — | Same at function level |

- Under ASC 606 the MAP Keys define NPSR as GPSR minus contractual allowances, minus charity, minus the provision for doubtful accounts. The worked example shows the implicit price concession reducing revenue directly ($10,300 NPSR rather than $11,000 less a $700 provision). — [HFMA MAP Keys 2023 Edition](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

**MAP Key benchmarks (HFMA MAP Award winners, the only HFMA-published percentiles retrieved; these are high performers and are old)**
- 2018 MAP Award winners (16 hospitals, CAHs, health systems and IDS), at the 90th / 50th / 10th percentile:
  - Net days in A/R: 34.6 / 39.7 / 55.3
  - DNSP days: 2.2 / 5.1 / 7.5
  - Aged A/R ≥90 days: 12.2% / 21.8% / 30.9%
  - POS cash as % of self-pay cash: 55.7% / 27.1% / 6.6%
  - Cash collection % NPSR: 102.2% / 99.3% / 96.8%
  - Bad debt: 0.2% / 0.6% / 2.2%
  - Charity: 4.8% / 1.2% / 0.1%
  - Cost to collect: 1.2% / 2.7% / 4.7%
  
  2018 physician-practice winners averaged 22.2 A/R days, 13.4% of A/R over 90 days, 2.4% denials and a 2.7-day charge lag. — [HFMA MAP Award statistical data 2016–2018](https://www.hfma.org/wp-content/uploads/2022/10/32307.pdf)
- 2017 winners (17 organisations) had a median of 40.6 A/R days, 5.1 DNSP days, 19.8% aged >90 and 2.8% cost to collect. 2016 winners (10) had a median of 36.6 A/R days and 1.6% cost to collect. — [HFMA MAP Award statistical data](https://www.hfma.org/wp-content/uploads/2022/10/32307.pdf)
- HFMA's MAP Award page says winners are selected using MAP Key analysis plus narrative and other materials. I found no fixed public cut-offs. — [HFMA MAP Award](https://www.hfma.org/tools/map-initiative/map-award.html)
- Secondary and vendor benchmarks are not authoritative:
  - Clean claim rate targets are cited at ≥90–95%, with 98% described as "strong". — [TechTarget RevCycle](https://www.techtarget.com/revcyclemanagement/feature/Breaking-Down-the-Top-5-Healthcare-Revenue-Cycle-KPIs)
  - Days in A/R targets are cited at 30–40, with best-in-class ≤25. — [TechTarget RevCycle](https://www.techtarget.com/revcyclemanagement/feature/Breaking-Down-the-Top-5-Healthcare-Revenue-Cycle-KPIs)
  - Cost to collect is cited at 2–4% of net patient revenue, with targets labelled illustrative. — [ASP RCM board playbook](https://asprcmsolutions.com/whitepapers/cohort-collections-waterfall-rcm-kpis/)

**Initial denial rate (outside MAP Keys) and denials benchmarks**
- HFMA's Claim Integrity Task Force defines the initial denial rate two ways: by volume (initial denial claims / total claims submitted) and by dollars (using gross charges). Only the first denial on a claim counts. Its companion metric is "denials overturned" (appeal success). — [HFMA Claim Integrity Task Force](https://www.hfma.org/guidance/standardizing-denial-metrics-revenue-cycle-benchmarking-process-improvement/)
- AR-5 (remittance denial rate) and AR-6 (denial write-offs % NPSR) are distinct measures that are both casually called "the denial rate". — [TechTarget: clean claims vs initial denials](https://www.techtarget.com/revcyclemanagement/news/366601124/Difference-Between-Clean-Claims-Initial-Claim-Denials-Key-Hospital-KPI)
- Kodiak Solutions 2025 benchmark figures (secondary, via trade press):
  - Initial denial rate rose to 11.65% in 2025 through November, from 11.41% in 2024. — [Fierce Healthcare](https://www.fiercehealthcare.com/finance/despite-better-cash-flow-providers-missed-out-more-revenue-2025-due-increased-payer-denials); [TechTarget](https://www.techtarget.com/revcyclemanagement/news/366641065/Hospitals-lost-over-48B-from-claims-denials-uncollected-bills)
  - Median final denial rate rose from 2.5% to 2.7%, and net revenue leakage was up 25%: $48.4B across analysed hospitals. — same sources
  - Clinical denials rose from 2.4% to 2.6%, and the clinical-denial overturn rate fell from 42.7% to 42.1%. — same sources
  - Kodiak's 2024 rate appears as 11.81% in one release and 11.41% in another. — conflict noted by the search summary; unresolved
- Medicare Advantage initial denials were 10.1% versus 4.2% for traditional Medicare, and MA has more than double the takebacks. — [HealthLeaders (Kodiak)](https://www.healthleadersmedia.com/node/99873) (secondary)
- Ensemble reported an 8.0% initial denial rate and a 2.8% denial write-off rate, against "industry benchmarks" of 11.8% and 3–5%. — [BusinessWire](https://www.businesswire.com/news/home/20250521892947/en) (vendor self-report)

**Prior authorization and payer behaviour**
- HFMA/Guidehouse 2026 revenue cycle report:
  - 88% of respondents named payer challenges the top revenue-cycle stressor.
  - About 81% reported increased denials and 74% reported more prior-auth delays.
  - 73% cited unclear denial reasons or underpayments, and 69% cited excessive information requests.
  
  The figures come from text extraction of the PDF and should be verified against the original. — [Guidehouse/HFMA 2026 RCM report](https://guidehouse.com/-/media/new-library/industries/health/documents/2026/health-revenue-cycle-management-report2026327.pdf); [Healthcare Finance News](https://www.healthcarefinancenews.com/news/payer-denials-and-prior-authorization-delays-are-top-rcm-concerns)
- 71% of Black Book respondents (not an HFMA survey) ranked prior authorization a top-three bottleneck to revenue realisation. — [Newswire / Black Book](https://www.newswire.com/news/black-book-survey-finds-hfma-ac26-attendees-arriving-with-narrower-rcm-22792715)
- The MAP Keys measure auth through PA-4/PA-5 (authorized ÷ requiring auth, where the denominator includes encounters denied for no auth). — [HFMA MAP Keys 2023](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

**Price transparency compliance**
- The PatientRightsAdvocate 8th report (September 2026; review window April 29–May 3, 2026; 2,000 hospitals) found 49.4% fully compliant, up from 21.1% in the prior report. Only 18% posted enough dollar pricing for easy comparison. 28 hospitals have been fined cumulatively, and PRA's classifications are not federal enforcement findings. — [PatientRightsAdvocate](https://www.patientrightsadvocate.org/eighth-annual-hospital-price-transparency-compliance-report-september-2026); [Becker's](https://www.beckershospitalreview.com/finance/hospital-price-transparency-in-2026-4-things-to-know/)
- Only one CMS fine was issued in the first half of 2026. — [Becker's](https://www.beckershospitalreview.com/finance/hospital-price-transparency-in-2026-4-things-to-know/)

### Inferences
- An analytics product should store denial reason (CARC/RARC group plus code), payer, first-denial flag, appeal outcome and write-off linkage at **claim** level, because the MAP Keys require claim-level counting for AR-5. That supports AR-5, AR-6, the task-force initial denial rate and overturn rate from the same data without mixing them up.
- Denominator traps that a question-answering system must catch:
  - DNFB, FBNS and DNSP use **single-month average daily gross** revenue.
  - FM-1, FM-2, AR-6 and AR-9 use the **3-month average NPSR**.
  - Bad debt, charity and uncompensated care use **gross** patient service revenue.
- MAP Award winner percentiles (2016–2018) describe high performers. They are not current national medians. Current national revenue-cycle medians are sold through HFMA's MAP App or vendors and were not publicly retrievable.
- Underpayment analysis (expected reimbursement from a contract model versus actual 835 paid amount) is standard practice **[unverified — no source retrieved]**. It needs the 837, 835, contract terms/fee schedules and a contract-modelling engine. It is distinct from denials: the Guidehouse survey bundles "underpayments" with unclear denial reasons.

### Gaps
- No current public national median for clean claim rate, POS collections, DNFB days or cost to collect. HFMA MAP App data is gated, and the HFMA-hosted 2026 Savista benchmark report (102 respondents, July 28, 2026) is form-gated and showed no numbers. — [HFMA/Savista page](https://www.hfma.org/revenue-cycle/2026-revenue-cycle-benchmark-report-where-leading-providers-are-adapting-and-where-theyre-struggling/)
- No AHA primary denial figure was retrieved. The roughly $43B AHA "cost of caring" figure seen in a blog is unverified.
- No published underpayment-rate benchmark (percentage of expected reimbursement recovered) was found.
- Denial distribution by reason category (eligibility, auth, medical necessity, coding, timely filing) with percentages was not retrieved from a primary source.

---

## Margin and cost: operating margin, cost per case, CMI, contribution margin, supply/labor, LOS, payer mix, 340B, uncompensated care

### Takeaway
The national margin picture in 2026 is thin and diverging. Rating-agency FY2025 medians put the operating margin at about 1.2–1.5%. The Kaufman Hall hospital median CYTD index fell in early 2026 and depends heavily on the treatment of corporate allocations. Kaufman Hall's Flash Report is the best public template for the cost question set: expense per adjusted discharge split into labor, supply, drugs and purchased services, plus FTEs per adjusted occupied bed, NPSR per adjusted discharge, and bad debt and charity as a share of gross revenue. Service-line and DRG contribution margin, 340B savings and cost per case have no public national benchmark. They are internal cost-accounting questions.

### Cited Findings

**Kaufman Hall National Hospital Flash Report: metric set and method**
- The Flash Report uses actual and budget data over the last three years from more than 1,300 hospitals via Strata Decision Technology. Kaufman Hall describes the sample as representative by geography and bed size. In 2026 it updated the adjustment for corporate allocations and intercompany transfers. — [KH NHFR April 2026 Metrics](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf)
- Metrics tracked, each shown month-over-month, year-over-year, YTD vs prior YTD and YTD vs YTD 2023, nationally, by region and by bed size — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf):
  - **Profitability:** Operating Margin Index and Operating EBIDA Margin Index (national median, with and without allocations).
  - **Revenue:** net and gross operating revenue per calendar day; IP and OP revenue per calendar day; IP/OP adjustment factor; NPSR per adjusted discharge; NPSR per adjusted patient day; bad debt and charity per calendar day; bad debt and charity as % of gross operating revenue.
  - **Expense:** total, labor, non-labor, supply, drugs and purchased-service expense, each per calendar day and per adjusted discharge; FTEs per adjusted occupied bed (AOB).
  - **Volume:** discharges, adjusted discharges, equivalent patient days and adjusted patient days per calendar day; observation days as % of patient days; ALOS; ED visits per calendar day; OR minutes per calendar day.
  - A non-operating section is also included.
- April 2026 CYTD Operating Margin Index (hospital median, read from extracted chart labels; verify against the rendered PDF) — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf):

  | Series | Jan 2026 | Feb 2026 | Mar 2026 | Apr 2026 |
  |---|---|---|---|---|
  | Median including allocations | 1.9% | 2.1% | 2.3% | 2.5% |
  | Median without allocations | 6.4% | 6.6% | 7.4% | 8.3% |

  For comparison, May–December 2025 including allocations was about 3.3–3.8%.
- Other reports of the same index (secondary):
  - A March 2026 adjusted YTD operating margin of 1.7%, up from 1.3% in February. — [Kaufman Hall news release, May 18 2026](https://www.kaufmanhall.com/news/hospital-finances-pace-below-2025-despite-slight-improvements-margins-and-expenses)
  - A mid-2026 CYTD index of 2.5%. — [Advisory Board](https://www.advisory.com/daily-briefing/2026/08/27/hospital-performance-ab-ec)
  - A CYTD index of 2.9%, with April's 3.6% as the year's high, described as "without allocations". — [TechTarget](https://www.techtarget.com/revcyclemanagement/news/366645808/Care-continues-shift-to-outpatient-as-hospital-margins-dip)
  
  **These conflict.** The likely cause is different allocation treatment and different report months. Always record which series is cited.
- August 2026 report (published October 13): about 43% of hospitals operating at a loss, versus about 33% pre-pandemic. Hospitals with 500+ beds saw operating margins fall 16.8% year over year in August. — [Becker's](https://www.beckershospitalreview.com/finance/why-43-of-hospitals-are-losing-money/)
- Non-labor expense per adjusted discharge rose 6% YTD versus 2025, with supplies up 5% and drugs up 6% (secondary, month unclear). — [Becker's 100 benchmarks 2026](https://www.beckershospitalreview.com/finance/100-hospital-financial-benchmarks-2026/)
- April 2026 key takeaways — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf):
  - Expense growth is outpacing inflation, with drug and labor expense the main contributors.
  - Payer mix continues to erode, with year-over-year climbs in bad debt and charity care.

**Rating-agency medians (not-for-profit; FY2025 data)**
- Fitch 2026 medians (FY2025, 222 providers), secondary:
  - Operating margin 1.5% (FY2024: 1.1%); 67% of providers had a positive margin.
  - Days cash on hand about 212 (FY2024: 215.1).
  - Cash-to-debt 188.0%; debt-to-capitalization 28.9%.
  - Below-investment-grade operating margin −2.8%.
  - Fitch called 2025 a possible "operational peak" before Medicaid cuts in 2027.
  
  Sources: [Fierce Healthcare](https://www.fiercehealthcare.com/providers/fitch-2025-likely-brief-operational-peak-nonprofit-hospitals-ahead-obbba-changes); [Healthcare Dive](https://www.healthcaredive.com/news/nonprofit-hospital-recovery-may-have-peaked-fitch-ratings-warns/827743/); [Becker's](https://www.beckershospitalreview.com/finance/hospital-liquidity-robust-but-long-term-future-clouded-fitch/)
- Fitch children's hospitals FY2025: operating margin 3.3% and days cash 401.7. — [Chief Healthcare Executive](https://www.chiefhealthcareexecutive.com/view/children-s-hospitals-see-improved-earnings-but-brace-for-medicaid-cuts)
- Moody's final FY2025 medians (201 NFP and public hospitals), secondary:
  - Operating margin 1.2% (FY2024: 1.3%). A Chartis summary of the preliminary medians cited 1.0%.
  - Operating revenue growth 8.9%; expense growth 8.5% (FY2024: 7.3%).
  - EBIDA margin 5.9% (FY2024: 6.2%).
  - Days cash 188 (FY2024: 193).
  - Preliminary operating cash flow margin 5.8% (FY2024: 6.3%).
  
  Sources: [Becker's](https://www.beckershospitalreview.com/finance/nonprofit-hospital-profitability-softened-in-2025-10-notes-from-moodys/); [Chartis](https://www.chartis.com/insights/capital-spending-rises-health-systems-need-more-durable-operating-model)
- S&P preliminary FY2025 medians (238 audits, May 7 2026; finals on about 400 entities due Q3 2026; not retrieved), secondary:
  - Operating EBIDA margin 6.2%; EBIDA margin 8.9%; unrestricted days cash 208.2.
  - Speculative-grade operating EBIDA 3.8%.
  
  Source: [Becker's 100 benchmarks 2026](https://www.beckershospitalreview.com/finance/100-hospital-financial-benchmarks-2026/)
- Rating-agency universes skew toward stronger, rated systems. — [search summary of Fitch coverage](https://www.fiercehealthcare.com/providers/fitch-2025-likely-brief-operational-peak-nonprofit-hospitals-ahead-obbba-changes)

**Case mix index**
- MAP Key FM-5 = Σ MS-DRG relative weights / inpatient discharges, with Medicare weights applied to all payers. It is a trending indicator of acuity and CDI/coding. — [HFMA MAP Keys 2023](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

**Uncompensated care: two competing definitions**
- MAP Key FM-4 (charges basis) = (bad debt + charity + uninsured discounts) / GPSR. — [HFMA MAP Keys 2023](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)
- CMS cost report Worksheet S-10 (cost basis):
  - Line 1 is the cost-to-charge ratio (Worksheet C Pt I line 202 col 3 ÷ col 8).
  - Line 23 is charity care cost; line 29 is non-Medicare and non-reimbursable Medicare bad debt cost; line 30 is total uncompensated care cost (line 23 + line 29).
  - S-10 data drive Medicare DSH uncompensated-care payments.
  - Medicaid and Medicare shortfalls are **not** included.
  
  Sources: [Noridian S-10 guidance](https://med.noridianmedicare.com/documents/10538/3599098/Worksheet+S-10+032024.pdf); [Noridian S-10 audits](https://med.noridianmedicare.com/web/jea/audit-reimbursement/worksheet-s-10-audits)
- AHA has argued that S-10 is inaccurate or inconsistent and should use a broader definition of uncompensated care. — [RevCycleIntelligence](https://revcycleintelligence.com/news/aha-uncompensated-care-costs-worksheet-inaccurate-inconsistent)
- Bad debt and charity rose year over year in 2026 (Flash Report). — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf); [Kaufman Hall May 2026 release](https://www.kaufmanhall.com/news/hospital-finances-pace-below-2025-despite-slight-improvements-margins-and-expenses)

**Cost report as a data source**
- HCRIS (Medicare cost report, CMS-2552-10) is a public, hospital-level source for costs, charges, CCRs and S-10 uncompensated care. HFMA publishes an introductory series. — [HFMA cost report series](https://www.hfma.org/wp-content/uploads/2023/05/crseries1hospitalcostreportsintroduction-nne.pdf); [NBER HCRIS manual ch. 40](https://m.nber.org/hcris/2552-10/manual/pr2_40.pdf)

### Inferences
**Standard internal definitions [unverified — standard practitioner definitions, not retrieved this session]:**
- **Adjusted discharges** = IP discharges × (gross total patient revenue / gross IP revenue). Kaufman Hall's "IP/OP adjustment factor" is this multiplier.
- **Cost per case / per adjusted discharge** = total operating expense (or direct + indirect cost from cost accounting) / cases (or adjusted discharges). Compare it CMI-adjusted.
- **Contribution margin** = net revenue − variable direct cost, by service line, MS-DRG, attending physician or payer. It requires a cost-accounting system (activity-based or RVU/RCC allocation), GL, payroll (labor by job code), supply/item master and charge detail.
- **LOS cost impact** = (actual ALOS − expected or GMLOS) × variable cost per day × cases. It needs DRG-level GMLOS, encounter-level daily cost and payer method, because extra days add cost but little revenue under DRG payment.
- **Payer mix** = share of gross charges, NPSR or cases by financial class. Shifts (Medicaid disenrollment, MA growth, uninsured) are the main revenue driver named by Kaufman Hall.
- **340B savings** = (acquisition cost at non-340B GPO/WAC price − 340B ceiling price) × units for eligible patients, plus contract-pharmacy net. It needs pharmacy purchasing, split-billing/TPA, eligibility (provider-based clinic, referral) and dispensing data.

**Other inferences:**
- The no-allocation vs with-allocation gap in the Kaufman Hall index (about 6 percentage points in April 2026) shows that "operating margin" at hospital level depends on whether system overhead and physician subsidies are allocated. An answer engine must report which convention it used.
- Rating-agency medians (Fitch about 1.5%, Moody's 1.2%, S&P operating EBIDA 6.2%) are system/obligor-level and skew to rated entities. The Kaufman Hall index is hospital-level and median. They should not be mixed as one "benchmark".

### Gaps
- No public national median for cost per adjusted discharge in dollars, labor cost per adjusted discharge, supply cost per case or CMI. Kaufman Hall publishes percentage changes only. Absolute values could be derived from HCRIS (not done here).
- No authoritative published benchmark for contribution margin by service line or DRG. These are proprietary (Strata, Vizient, Premier).
- 340B: no source on savings measurement methods or benchmarks retrieved (HRSA, AHA, 340B Health, peer-reviewed papers not searched due to budget).
- Peer-reviewed literature on cost accounting methods (e.g., time-driven ABC, Kaplan & Porter) was not searched this session.
- The final S&P FY2025 medians and the Moody's primary report were not retrieved.

---

## Volume and strategy: adjusted admissions/patient days, outpatient visits, market share, leakage, referrals, new-patient access

### Takeaway
Kaufman Hall tracks the standard volume set in 2026 and reports a continuing shift to outpatient, with softer inpatient and outpatient volumes in some months. That set is discharges, adjusted discharges, adjusted and equivalent patient days, observation share, ALOS, ED visits and OR minutes, all per calendar day. Market share, outmigration/leakage and referral-pattern questions depend on external all-payer claims, state discharge data or commercial referral data. None of the sources retrieved gives a standard public definition or benchmark for them.

### Cited Findings
- The Flash Report volume metrics are listed above. ALOS reduction "salvaged" March 2026 margins, and hospitals are working to reduce LOS and shift care to outpatient settings. — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf); [Kaufman Hall May 2026 release](https://www.kaufmanhall.com/news/hospital-finances-pace-below-2025-despite-slight-improvements-margins-and-expenses); [Fierce Healthcare](https://www.fiercehealthcare.com/providers/reduced-average-length-stay-salvaged-hospitals-operating-margins-march-report?rand=57452)
- July 2026 operating margins "dove" on soft outpatient volumes and charity care pressures. — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/hospital-operating-margins-dive-july-amid-soft-outpatient-volumes-charity-care-pressures)
- Care continues to shift to outpatient as margins dip. — [TechTarget](https://www.techtarget.com/revcyclemanagement/news/366645808/Care-continues-shift-to-outpatient-as-hospital-margins-dip)
- Kaufman Hall's Physician Flash Report (Q1 2026, more than 200,000 providers via Strata) is relevant to new-patient access and referral capacity. It showed provider work RVUs per FTE up 3% year over year, with APPs at 40% of the workforce. — [Kaufman Hall May 2026 release](https://www.kaufmanhall.com/news/hospital-finances-pace-below-2025-despite-slight-improvements-margins-and-expenses)
- The MAP Key access measures that apply here are PA-1 (schedule occupied) and PA-2 (pre-registration). — [HFMA MAP Keys 2023](https://www.hfma.org/wp-content/uploads/2023/10/2023-Edition-MAP-Keys.pdf)

### Inferences
**Standard definitions [unverified — no source retrieved]:**
- **Market share** = system discharges (or cases by service line) / all discharges for residents of a defined service area, usually from state all-payer discharge databases or CMS MedPAR.
- **Outmigration/leakage** = share of services for attributed or service-area patients (or patients of employed or aligned physicians) delivered outside the system. It requires referral orders linked to claims, or payer/ACO claims.
- **New-patient access** = third-next-available appointment, new-patient lag days, or the new-patient share of visits.

**Other inferences:**
- For an analytics product, the in-house data (ADT/encounters, scheduling, orders/referrals) supports volume, ALOS, observation share and internal referral-capture questions. True market share and leakage need external claims, so they are a separate data-dependency class.

### Gaps
- No public, sourced benchmark was found for market share, leakage rate (commonly quoted ranges in vendor marketing were not verified) or third-next-available appointment.
- Volume benchmarks in absolute terms are not public. Kaufman Hall gives only percentage changes.

---

## Which finance questions CFOs and revenue-cycle leaders ask most (published sources)

### Takeaway
Published 2026 surveys and Flash Report commentary agree on the top themes:
1. Payer behaviour (denials, prior auth, underpayments, MA audits and takebacks), named as the top revenue-cycle stressor by 88% in HFMA/Guidehouse.
2. Expense growth, especially labor and drugs, outpacing revenue.
3. Payer-mix erosion and rising bad debt and charity ahead of 2027 Medicaid cuts.
4. Thin or negative margins: about 43% of hospitals were losing money in August 2026.
5. Staffing shortages in revenue cycle, and a related interest in automation, AI and outsourcing.

### Cited Findings
- HFMA/Guidehouse 2026 report:
  - 88% named payer challenges the top stressor.
  - About 81% reported more denials and 74% more prior-auth delays.
  - 73% cited unclear denial reasons or underpayments.
  - 78% use automation or AI for manual revenue-cycle processes.
  
  The figures come from text extraction; verify against the original. — [Guidehouse/HFMA 2026](https://guidehouse.com/-/media/new-library/industries/health/documents/2026/health-revenue-cycle-management-report2026327.pdf); [Guidehouse RCM trends](https://guidehouse.com/insights/healthcare/2026/rev-cycle-trends-report)
- HFMA and Eliciting Insights surveyed more than 130 health system CFOs on pain points and 2026 budget and labor predictions. A ranked list was not retrieved, and one HFMA summary says most CFOs are *not* looking to technology or AI as the solution. That conflicts with the Guidehouse 78% AI-use figure, and the two measure different things (intent versus current use). — [HFMA](https://www.hfma.org/?p=118575); [HFMA Fast Finance on Medicaid cuts](https://www.hfma.org/fast-finance/health-systems-preparing-for-medicaid-cuts-2026/)
- HFMA/Savista 2026 survey (102 finance and revenue-cycle professionals, July 28 2026):
  - Denials and appeals are the industry's top concern.
  - Expanding audit activity is increasing exposure.
  - Staffing shortages are disrupting operations.
  - Responses focus on automation, AI and outsourcing.
  
  Source: [HFMA/Savista](https://www.hfma.org/revenue-cycle/2026-revenue-cycle-benchmark-report-where-leading-providers-are-adapting-and-where-theyre-struggling/)
- Revenue-cycle leaders' main worries are increased claims denials and staff shortages. — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/increased-claims-denials-staff-shortages-worry-health-system-rev-cycle-heads-survey-finds)
- Patient financial experience is a top focus for revenue-cycle leaders in HFMA coverage. — [HFMA](https://www.hfma.org/operations-management/rcm-recalibration-patient-financial-experience-captures-leaders-top-focus/)
- Lower reimbursement rates and denials lie behind razor-thin margins. — [TechTarget](https://www.techtarget.com/revcyclemanagement/news/366600199/Lower-reimbursement-rates-denials-behind-razor-thin-margins)
- Kaufman Hall April 2026 takeaways: performance is under pressure, expense growth is outpacing inflation (drugs and labor), and payer mix is eroding with rising bad debt and charity. — [KH NHFR April 2026](https://www.kaufmanhall.com/sites/default/files/2026-06/KH-NHFR_Report-April-2026-Metrics.pdf)
- Fitch warns that 2025 may be the "operational peak" before OBBBA Medicaid reductions bite in 2027. — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/fitch-2025-likely-brief-operational-peak-nonprofit-hospitals-ahead-obbba-changes)

### Inferences
These are the question templates the sources imply CFO and revenue-cycle users will ask most. Data requirements are in brackets.
1. "What is our initial and final denial rate by payer, reason code and service line, and what share is preventable (eligibility, auth, coding, medical necessity)?" [837 + 835 CARC/RARC + PFS + auth log]
2. "Which payers are underpaying against contract, and by how much?" [837 + 835 + contract model]
3. "Why did margin move this month? Volume, rate, payer mix or cost (labor/supply/drug per adjusted discharge)?" [GL + IS + payroll + supply + ADT/encounter + CMI]
4. "What will the Medicaid and coverage changes do to payer mix, bad debt and charity?" [PFS financial class + IS provisions + S-10]
5. "Where are DNFB, FBNS and A/R >90 stuck, and what is the cash impact?" [unbilled A/R + scrubber + ATB]
6. "Which service lines, DRGs and physicians have negative contribution margin, and is excess LOS driving cost?" [cost accounting + encoder + ADT]
7. "How do we compare to Kaufman Hall, Moody's, Fitch or S&P medians?" [external benchmark tables; record the allocation convention and data year]

These are inferred priorities, not a published ranked list.

### Gaps
- I found no published *ranked* list of the most-asked CFO analytics questions. The HFMA/Eliciting Insights CFO survey results were not accessible.
- The full Kaufman Hall CFO-oriented "State of Healthcare Performance Improvement" or similar surveys were not searched (budget).
- Several items above rest on secondary press summaries of gated reports: Kodiak, Moody's, S&P, Fitch, and the Guidehouse percentages extracted from a jumbled PDF. Verify against the primaries before quoting externally.
