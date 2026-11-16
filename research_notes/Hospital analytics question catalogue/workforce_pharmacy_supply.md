# Workforce, nursing operations, pharmacy operations and supply-chain questions US hospitals ask of their own data

Scope: US hospitals and health systems, current as of November 2026. Research ran about 17 search and fetch calls. Several primary documents could not be opened: the NSI PDF only came back as binary with no local PDF text tool, Becker's returned 403, and AHA and MGMA tables are behind membership. Many figures below therefore come from secondary coverage, and each one is marked. Anything tagged **[UNVERIFIED]** is background knowledge from before this session that was not confirmed against a source here. It is listed so the report writer can see what still needs checking, and it should not be cited as fact.

Data-source codes used in the inventory tables:
- **TK/PAY**: timekeeping and payroll (worked and paid hours, job code, cost centre, pay codes for overtime, sick and orientation)
- **SCHED**: scheduling and staffing system (planned shifts, float and agency assignments, grids)
- **CENSUS**: ADT census by unit and hour (midnight census, admissions, discharges, transfers, observation hours)
- **ACU**: patient acuity or classification
- **AGY**: agency and travel invoices (vendor-management system)
- **HR**: HRIS (hires, terminations with reason, positions, requisitions)
- **INC**: incident or safety reporting
- **eMAR/BCMA**: medication administration records, including scans and overrides
- **DISP**: pharmacy dispensing (ADC/automated dispensing cabinet transactions, carousel, IV workflow)
- **INV**: inventory (on-hand, par levels, expiry)
- **PUR**: purchasing and AP (POs, invoices, GPO contract prices, wholesaler data)
- **GL**: general ledger
- **EHR-AUD**: EHR audit logs (documentation time)
- **SCHED-AMB**: ambulatory scheduling templates and slots
- **ITEM**: item master and UDI implant logs

---

## Nursing and workforce metrics

### Takeaway
Nursing and workforce analytics centre on four linked questions. Do we have enough of the right staff on each unit and shift (HPPD, grid or ratio compliance, skill mix)? What are we paying to fill the gaps (overtime, agency, premium pay)? Are we losing people (turnover, especially first-year, and vacancy)? Are outcomes or regulatory exposure affected (nurse-sensitive indicators, state staffing laws, workplace violence)? NSI's 2026 report puts RN turnover at about 17.6% and RN vacancy at about 8.6%. Only two states have comprehensive hospital ratio laws (California and Oregon), with ICU-only laws in Massachusetts and New York and committee laws in eight states.

### Cited Findings

**Nursing hours per patient day (NHPPD)**
- NDNQI and NQF numerator: productive hours worked by RNs, LPN/LVNs and UAP assigned to the unit who spend more than 50% of their shift on direct patient care. Productive hours are hours actually worked, not budgeted or scheduled, so vacation, sick time, orientation, education and committee time are excluded. — [CHA 2018 Data Collection Guide for Nursing Sensitive Measures](https://cha.com/wp-content/uploads/2018/01/1_2018-Data-Collection-Guide-for-Nursing-Sensitive-Measures-.pdf); [PMC3291170](https://pmc.ncbi.nlm.nih.gov/articles/PMC3291170)
- Denominator, per the CDC NHSN Nurse Staffing Indicator protocol: patient days are patients on the unit per day over the calendar month, usually counted at midnight. NHPPD patient days must match the Device-associated Total Patient Days reported. — [CDC NHSN Nurse Staffing Indicator Protocol](https://www.cdc.gov/nhsn/psc/nshi/Nurse-Staffing-Indicator-Protocol508c.pdf)
- NHPPD is a structural measure that is algebraically equivalent to a ratio. Specialty units use other denominators, for example nursing care hours per surgical case in perioperative areas. — [AMN Healthcare: new nurse staffing measures for specialty areas](https://www.amnhealthcare.com/latest-healthcare-news/new-nurse-staffing-measures-developed-for-specialty-areas/)
- Data required: TK/PAY (productive hours by job code and unit), HR (job-code roles), CENSUS (midnight census, or hourly census for short-stay units). Skill mix (RN hours ÷ total nursing hours) is reported to NDNQI alongside NHPPD. — same CHA guide
- Benchmarks: NDNQI publishes unit-type percentiles to members only. No public benchmark was found (see Gaps).

**Turnover, vacancy, cost, time to fill (NSI 2026 National Health Care Retention & RN Staffing Report, 527 hospitals in 40 states; figures via secondary coverage)**
- RN turnover in 2025 was 17.6%, up 1.2 points year over year. — [Becker's: cost of nurse turnover 2026](https://www.beckershospitalreview.com/workforce/the-cost-of-nurse-turnover-in-10-points-2026/) (snippet only, page returned 403); [nurse.org](https://nurse.org/news/how-much-does-nurse-turnover-cost/)
- RN vacancy is 8.6%, down from 9.6%. NSI changed its vacancy methodology this year, so treat the year-over-year comparison as directional. 33.1% of hospitals reported RN vacancy of 10% or more. — [Becker's current state of nursing 2026](https://www.beckershospitalreview.com/quality/nursing/the-current-state-of-nursing-2026/) (snippet)
- Average cost of turnover for one bedside RN is $60,090. — Becker's / nurse.org, as above
- Hospitals added 377,650 RNs, a 2.9% add rate, down from 5.6% the year before. — [NSI report PDF](https://www.nsinursingsolutions.com/Documents/Library/NSI_National_Health_Care_Retention_Report.pdf) (figures via secondary snippets, the PDF text itself was not readable)
- RN turnover by specialty: behavioral health 22.5%, emergency 20.7%, telemetry 19.5%, step-down 19%. — secondary coverage of NSI ([Stealth Agents](https://stealthagents.com/research/healthcare-staffing-shortage-statistics-2026); Becker's)
- Recruiting an experienced RN takes about 78 days (NSI RN Recruitment Difficulty Index). — secondary coverage of NSI
- Data required: HR (headcount, terminations with voluntary or involuntary reason, hire date for first-year turnover, open requisitions and time to fill), GL for cost-of-turnover models.

**State staffing laws (regulatory reporting questions)**
- Comprehensive ratio laws: California (enacted 2004, fully phased in January 1, 2008; for example 1:5 for adult medical/surgical and 1:2 for ICU) and Oregon (in effect 2024, medical/surgical 1:5 tightening to 1:4 on June 1, 2026, with a possible two-year variance for rural hospitals). — [AFT / Aiken, *Policies to Achieve Hospital Nurse Staffing Adequacy*, Fall 2025](https://www.aft.org/hc/fall2025/aiken)
- ICU-only ratio laws: Massachusetts (2014, 1:1 or 1:2 depending on acuity) and New York (Safe Staffing for Hospital Care Act 2021, ICU rule 2023, 1:2). — same source
- Staffing committee laws in eight states: CT, IL, NV, NY, OH, OR, TX, WA. Committees are typically at least 50% direct-care nurses. 11 states require hospital staffing plans. — same source
- Mandatory public reporting of staffing in IL, NJ, NY, RI and VT. Voluntary reporting in MA, MN and WA. — same source
- Oregon Health Authority can levy civil monetary penalties for violations on or after June 1, 2025. — secondary summary in search results, **[partially verified]**
- Source conflict: some 2026 guides count four "ratio states" (CA, MA, OR, NY) because they include the ICU-only laws. Strictly, only California has a long-standing comprehensive enforceable ratio law. — [nursinglicensure.org](https://nursinglicensure.org/articles/nurse-staffing-ratios/) vs [rn411 blog](https://rn411-com-nursing-jobs-and-information.jobboardly.com/blog/which-states-actually-mandate-safe-staffing-ratios)
- Pending bills in 2025–2026, status not confirmed: Massachusetts S.1768, Hawaii HB 1244 (carried over) and SB 2763 (deviation reporting), Wisconsin Nurse Staffing & Patient Protection Act (February 2026), New Mexico HB 172 (2026), federal H.R. 3415 (May 2025). — [Hawaii SB2763](https://data.capitol.hawaii.gov/sessions/session2026/bills/SB2763_.pdf); [nurse.org Wisconsin](https://nurse.org/news/wisconsin-nurse-staffing-patient-protection-act); [NM HB0172](https://www.nmlegis.gov/Sessions/26%20Regular/bills/house/HB0172.pdf)
- California's psychiatric ratio enforcement led to a new state inspection unit. — [Becker's](https://www.beckershospitalreview.com/quality/nursing/californias-psych-nurse-ratio-fallout-prompts-a-new-state-inspection-unit/) (headline only)
- Data required: SCHED and CENSUS at shift or hour level by unit and patient assignment, ACU, and documented deviations from the plan.

**Nurse-sensitive outcomes linked to staffing**
- Each additional patient per nurse is associated with about 7% higher odds of in-hospital mortality and failure to rescue (Aiken 2002, 2014). — [AFT / Aiken 2025](https://www.aft.org/hc/fall2025/aiken)
- California patients received up to 3 more RN hours per day than patients in other states (1997–2016), with lower mortality and failure to rescue and less nurse burnout. — same source
- The Massachusetts ICU law showed no change in staffing or outcomes in a six-centre evaluation; Aiken disputes the study design. Staffing committees alone, and mandatory public reporting alone, show no significant effect on staffing or outcomes. — same source
- NDNQI nurse-sensitive indicators include falls with injury, HAPI (hospital-acquired pressure injury), CAUTI, CLABSI, restraint use and RN satisfaction, each joined to staffing at unit-month level. — [CHA NDNQI guide](https://cha.com/wp-content/uploads/2018/01/1_2018-Data-Collection-Guide-for-Nursing-Sensitive-Measures-.pdf) **[indicator list from background; confirm against current NDNQI guidelines]**

**Workplace violence**
- 27% of 2,090 nurses surveyed were physically assaulted in 2025 and 52% had verbal threats. Rates were 59% in telemetry and 58% in progressive care. — [nurse.org 2026 State of Nursing survey](https://nurse.org/articles/nurses-workplace-violence-2026)
- In National Nurses United's survey of 1,267 RNs, 84.8% had experienced at least one type of workplace violence in the past year, and 25.5% had considered leaving because of it. — [NNU press release](https://www.nationalnursesunited.org/press/nnu-report-finds-a-majority-of-nurses-experienced-workplace-violence-in-the-past-year) (advocacy source)
- More than half of emergency nurses report being assaulted or facing aggression within the past month (ENA survey). — [workerscompensation.com](https://www.workerscompensation.com/daily-headlines/nurses-look-to-cope-with-rising-attacks-in-workplace/) (secondary)
- The BLS-based multiplier for violent injury risk among hospital workers versus all workers is variously reported as 5x or 7x. Sources conflict. — [getsafeandsound blog](https://getsafeandsound.com/blog/healthcare-workplace-violence-statistics/) (vendor)
- Hospitals lost $18.27B to workplace violence in 2023. — vendor and blog source, **[UNVERIFIED primary]**
- Data required: INC (event type, severity, unit, time), HR and workers' comp (lost days), CENSUS for rates per 10,000 patient days or per 100 FTE.

**Leader priorities (AONL)**
- AONL Nursing Leadership Insight Study 2025 (survey January 2025): staff recruitment and retention was the top challenge at 49%, down from 69% in 2023. Staffing came second at 37%. Staff emotional health and well-being is in the top three. Workplace violence, financial constraints and technology integration persist. New 2025 priorities include technology adoption and onboarding. — [AONL Insight Study 2025 PDF](https://www.aonl.org/system/files/media/file/2025/03/AONL-Nursing-Leadership-Insight-Study-2025.pdf)
- AONL sample-size figures conflict across its own pages: 1,338, more than 3,100, and n = 2,992 for the challenge question. — [AONL survey page](https://www.aonl.org/resources/nursing-leadership-survey); [AONL Voice of the President, May 2025](https://www.aonl.org/news/voice/may-2025/Voice-of-the-President)

**AHA 2026 Health Care Workforce Scan (December 2025)**
- Themes: labor cost and financial pressure; rebuilding care teams and team-based models instead of waiting for supply to recover; AI paired with redesigned workflows to cut administrative burden; culture and engagement to retain early-career and Gen Z staff; upskilling for digital, virtual and AI roles. — [AHA Workforce Scan](https://www.aha.org/aha-talent-scan); [PDF](https://www.aha.org/system/files/media/file/2025/12/2026-Health-Care-Workforce-Scan.pdf); [AONL news](https://www.aonl.org/news/AHA-releases-2026-Workforce-Scan)

**Labor cost trend**
- Kaufman Hall Flash Report coverage for 2026: labor expense per adjusted discharge is up about 2% from 2023 levels and labor has "leveled off". Through January 2026, labor expense per calendar day was up 5% year over year. — [Becker's: 43% of hospitals in the red](https://www.beckershospitalreview.com/finance/43-of-hospitals-are-in-the-red-6-financial-notes/) (snippet); [Kaufman Hall Jan 2026](https://www.kaufmanhall.com/insights/research-report/national-hospital-flash-report-january-2026-data)

### Inventory: nursing and workforce metric definitions

Items marked [UNVERIFIED] are standard industry definitions from background knowledge, not confirmed this session.

| Metric | Definition / source | Data | Benchmark (source) | Decision it drives |
|---|---|---|---|---|
| NHPPD (total, RN) | Productive direct-care hours ÷ patient days (NDNQI/NQF; CDC NHSN) | TK/PAY, HR job codes, CENSUS | NDNQI unit-type percentiles (member-only) | Budgeted HPPD targets, position control |
| Skill mix (% RN) | RN productive hours ÷ total nursing productive hours (NDNQI) | TK/PAY | NDNQI percentiles (member-only) | Team model redesign (LPN/UAP mix) |
| Grid or ratio compliance | % of shifts or hours where actual staff meet grid or legal ratio for the census at that time (state law: CA Title 22, OR HB 2697 [bill number UNVERIFIED]) | SCHED, CENSUS hourly, ACU | Legal floor = 100% compliance in CA, OR | Regulatory exposure, staffing committee reports, call-ins |
| Worked hours per unit of service (WHPUOS) / productivity index | Worked hours ÷ UOS (patient days, visits, cases) vs target; productivity % = target ÷ actual [UNVERIFIED standard practice] | TK/PAY, CENSUS/volume, GL | Internal or vendor benchmarks (Premier, Vizient) [UNVERIFIED] | Flex staffing, daily labor huddles |
| Overtime % | OT hours ÷ total worked or paid hours [UNVERIFIED definition] | TK/PAY pay codes | No public benchmark found | Schedule fixes, burnout risk |
| Agency/travel utilization and cost | Contract FTEs or hours ÷ total; contract $ ÷ total labor $ [UNVERIFIED definition] | AGY, VMS, GL | NSI reports national agency usage (figure not retrieved) | Conversion to core staff, internal agency, rate caps |
| RN vacancy rate | Open budgeted RN positions ÷ total budgeted RN positions (NSI; methodology changed in 2026) | HR positions and requisitions | 8.6% (NSI 2026 via Becker's) | Recruitment spend, contract labor bridge |
| RN turnover | RN separations ÷ average RN headcount (NSI) | HR | 17.6% (NSI 2026 via Becker's/nurse.org); behavioral health 22.5%, ED 20.7% | Retention programs, unit leadership focus |
| First-year RN turnover | Separations of RNs with tenure under 1 year ÷ new hires (NSI tracks this) | HR hire and term dates | Not retrieved this session (see Gaps) | Residency and onboarding investment |
| Cost of turnover | NSI: $60,090 per bedside RN | HR + GL | $60,090 (NSI 2026 via Becker's) | ROI of retention spend |
| Time to fill | Days from requisition to acceptance (NSI index) | HR/ATS | about 78 days experienced RN (NSI via secondary) | Pipeline planning |
| Sick time / unplanned absence | Unplanned PTO hours ÷ scheduled hours [UNVERIFIED definition] | TK/PAY | None found | Call-out coverage, float pool size |
| Float pool utilization | Float hours ÷ total worked hours; % of float requests filled [UNVERIFIED] | SCHED, TK | None found | Size the float pool versus agency |
| Nurse-sensitive outcomes vs staffing | Falls with injury, HAPI, CAUTI, CLABSI per 1,000 patient days, joined to NHPPD (NDNQI) | INC, infection prevention, TK, CENSUS | Each extra patient per RN about 7% higher mortality (Aiken via AFT) | Staffing investment case |
| Workplace violence rate | Incidents per 10,000 patient days or per 100 FTE [UNVERIFIED normaliser] | INC, HR | 27% of nurses assaulted in 2025 (nurse.org survey) | Security staffing, de-escalation training, Joint Commission WPV program |

### Inferences
- The practical join that makes these questions answerable is unit × hour (or shift) × job code: TK/PAY hours, SCHED assignments, CENSUS hourly occupancy and ACU. Ratio-law compliance (CA, OR) cannot be answered from monthly HPPD; it needs shift-level assignment and census data.
- NSI is the de facto public benchmark for turnover and vacancy. NDNQI is the de facto benchmark for HPPD and nurse-sensitive outcomes but is member-only. A product can compute definitions locally but should mark external benchmark comparisons as requiring a licence.
- Since labor cost per adjusted discharge has flattened (Kaufman Hall), 2026 leader questions likely shift from "how much agency" toward retention, first-year turnover, team-model redesign and productivity. The AHA scan and AONL themes support this. This is an inference, not a published ranking.

### Gaps
- The NSI 2026 PDF was downloaded but not text-readable here: no pdftotext or pypdf, and Becker's returned 403. First-year RN turnover, hospital-wide staff turnover, travel or agency usage %, and "each 1% change in turnover costs $X" were **not retrieved**. They are known NSI report fields; values must be confirmed from the PDF.
- No public NDNQI HPPD percentiles or benchmarks were found.
- No authoritative public overtime %, sick-time or float-pool benchmarks were found.
- No current consolidated state staffing law tracker was found; AFT Fall 2025 is the best source here. Confirm 2026 bill outcomes for MA, WI, NM and HI.
- The Joint Commission workplace violence standards (effective January 2022 for hospitals, background knowledge) were not confirmed from Joint Commission text: **[UNVERIFIED]**.

---

## Physician and provider operations

### Takeaway
Provider operations questions centre on wRVU productivity against MGMA percentiles, clinic access (third next available appointment, TNAA), panel size and EHR documentation burden. MGMA's 2026 data shows compensation rising while wRVU production falls. Public benchmark values for TNAA and panel size were not found this session.

### Cited Findings
- MGMA 2026 DataDive (2025 data, 23 common specialties): median total compensation rose while productivity fell, so pay and production are diverging. — [Physicians Practice, June 2026](https://www.physicianspractice.com/view/physician-pay-productivity-split-mgma-report); [MGMA 2026 Provider Compensation](https://www.mgma.com/2026-provider-compensation)
- In physician-owned practices, top-quartile primary care physicians produced more than 7,585 wRVUs and bottom-quartile physicians fewer than 4,165 (MGMA 2026 data). — [Medical Economics, September 2026](https://www.medicaleconomics.com/view/make-more-money-by-making-your-doctors-more-productive-mgma-expert-says)
- Family medicine median wRVU is about 4,715. — [FastRVU](https://fastrvu.com/articles/mgma-family-medicine-median-wrvu) (third-party, **[verify against MGMA]**)
- The 2026 Medicare Physician Fee Schedule introduced a wRVU change (an efficiency adjustment, details truncated), which affects year-over-year wRVU comparison. — [MGMA 2026 report PDF](https://pages.mgma.com/rs/144-AMJ-639/images/2606-N36956D-2026%20Provider%20Compensation%20and%20Productivity%20Data%20Report%20MW%202.pdf?version=0) **[partially verified]**
- MGMA lists foundational practice KPIs, including encounters per provider. — [MGMA foundational benchmarks](https://mgma.com/practice-resources/financial-management/foundational-benchmarks-and-kpis-for-medical-pract); [MGMA encounters per provider](https://www.mgma.com/articles/optimizing-encounters-per-provider-provider-production)

| Metric | Definition / source | Data | Benchmark | Decision |
|---|---|---|---|---|
| wRVUs per clinical FTE | Sum of CMS work RVUs on billed CPT codes ÷ clinical FTE (MGMA) | Professional billing, provider FTE | PCP top quartile >7,585, bottom <4,165 (MGMA via Medical Economics) | Compensation plans, staffing, coding review |
| Compensation per wRVU | Total compensation ÷ wRVU (MGMA) | Payroll + billing | MGMA (licensed) | Fair market value, plan design |
| Third next available appointment | Days to the third open slot for a new or return visit type (IHI measure) [UNVERIFIED: not sourced this session] | SCHED-AMB slot inventory | Not found | Template changes, access recovery |
| Panel size | Unique patients attributed to a PCP over 12–18 months [UNVERIFIED] | Billing, attribution | Not found | Hiring, team-based care, VBC attribution |
| Documentation time / pajama time | EHR active minutes per visit or after-hours minutes (Epic Signal, Cerner Lights On) [UNVERIFIED] | EHR-AUD | Not found | Ambient AI scribe ROI, burnout |
| Encounters per provider per day | Billed encounters ÷ clinical days (MGMA) | Billing, scheduling | MGMA (licensed) | Template capacity |

### Inferences
- wRVU questions must account for the 2026 fee schedule change, or year-over-year productivity "declines" may be partly an artefact of the change. This is inferred from the MGMA note.
- Panel size and TNAA tie directly to VBC and population health (attribution, access as a quality lever), which matches the product's VBC centre.

### Gaps
- No sourced TNAA benchmark or definition was found this session (IHI is the likely source).
- No sourced panel-size or documentation-time benchmarks were found (likely sources: AMA, Epic Signal studies, peer-reviewed *Annals of Family Medicine*).

---

## Pharmacy operations

### Takeaway
Pharmacy operations questions cover drug spend (per adjusted discharge, trend), shortages and substitutions, inventory turns and waste, 340B compliance and savings, medication administration timeliness and BCMA scanning compliance, controlled-substance diversion surveillance, formulary adherence and IV room throughput. Shortages remain elevated at 225 active (ASHP, Q3 2026) against a peak of 323 in Q1 2024. Drug expense per adjusted discharge is rising faster than inflation (about 6% year to date in 2026 per Kaufman Hall coverage). Leapfrog's BCMA standard sets the 95% scanning benchmark.

### Cited Findings
- ASHP active shortages: 225 in Q3 2026 (chemotherapy shortages rising), 227 in Q2 2026, 223 in Q1 2026, 216 at year-end 2025. All-time high was 323 in Q1 2024. — [ASHP Drug Shortages Statistics](https://www.ashp.org/Drug-Shortages/Shortage-Resources/Drug-Shortages-Statistics); [Becker's chemo shortage timeline](https://www.beckershospitalreview.com/pharmacy/the-2026-chemo-shortage-a-timeline/); [AJMC July 2026](https://www.ajmc.com/view/active-us-drug-shortages-rise-for-third-straight-quarter)
- ASHP identified 89 new shortages in 2025, the lowest annual total since 2006. — [Becker's: new drug shortages hit 20-year low](https://www.beckershospitalreview.com/pharmacy/new-drug-shortages-hit-20-year-low-ashp-report)
- 41% of shortages last more than two years. — [Becker's](https://www.beckershospitalreview.com/pharmacy/41-of-drug-shortages-span-more-than-2-years) (headline)
- Largest shortage categories are CNS drugs, then antimicrobials, then chemotherapy and hormones. — [AJMC](https://www.ajmc.com/view/active-us-drug-shortages-rise-for-third-straight-quarter)
- FDA and USP counts differ by method (for example, USP had 75 active at year-end 2025) and should not be mixed with ASHP counts. — [IntuitionLabs summary](https://intuitionlabs.ai/articles/drug-shortages-united-states-2026) (secondary)
- ASHP National Survey (2024 data, 250 hospitals, AJHP June 2025): managing shortages takes about 1.5 FTE in large hospitals. More than 80% of directors report shortages of experienced technicians and about 60% report shortages of clinical specialists. Pharmacists are taking on more 340B compliance oversight. — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/hospitals-increasingly-tap-pharmacists-clinical-care-struggle-fill-rolls-survey); [AJMC](https://www.ajmc.com/view/pharmacists-expand-frontline-role-in-us-hospitals-survey-finds)
- ASHP survey of 298 pharmacy leaders (late 2025): 60% think a manufacturer-imposed 340B rebate model is somewhat or very likely and expect margin erosion. — [Managed Healthcare Executive, December 2025](https://www.managedhealthcareexecutive.com/view/pharmacy-leaders-in-ashp-survey-worry-about-340b-program-changes)
- HRSA: 340B covered entities bought "$81.4" in covered outpatient drugs in 2024. The unit is missing in the snippet and is presumably billions. — same source **[verify unit with HRSA]**
- Drug expense per adjusted discharge is up about 6% year to date in 2026 versus 2025, with supply expense up 5% and non-labor up 6% (Kaufman Hall via Becker's). One bed-size subgroup showed drug expense up 21.9% year over year. — [Becker's](https://www.beckershospitalreview.com/finance/43-of-hospitals-are-in-the-red-6-financial-notes/); [Kaufman Hall April 2026](https://www.kaufmanhall.com/insights/research-report/national-hospital-flash-report-april-2026-data)
- Leapfrog BCMA standard: scan both patient and medication barcodes in 95% of bedside administrations on BCMA-equipped units, plus EHR linkage and decision support. Failing the 95% scanning criterion is the most common reason hospitals miss the standard. Only about 30% of 1,859 hospitals fully met it, and a later report found fewer than 35%. These are 2017–2019 data. — [Leapfrog-Castlight BCMA report](https://leapfroggroup.org/sites/default/files/Files/Leapfrog-Castlight_BCMA_Final.pdf); [2019 Leapfrog Medication Safety Report](https://www.leapfroggroup.org/sites/default/files/Files/2019%20Leapfrog%20Medication%20Safety%20Report.pdf); [HC Innovation Group](https://www.hcinnovationgroup.com/clinical-it/news/13030082/room-for-improvement-in-bar-code-administration-leapfrog-report-finds)
- In one Baptist Health example, monthly reporting and education raised medication scan compliance from 82% to 92% and patient scan compliance from 83% to 93%. — [Baptist Health Scholarly Commons](https://scholarlycommons.baptisthealth.net/se-all-publications/3288)

| Metric | Definition / source | Data | Benchmark | Decision |
|---|---|---|---|---|
| Drug expense per adjusted discharge | Pharmaceutical expense ÷ (inpatient discharges × gross revenue ÷ inpatient gross revenue), often CMI-adjusted (Kaufman Hall) | GL, PUR, census, revenue | About +6% YTD 2026 (Kaufman Hall via Becker's) | Formulary, biosimilar conversion, site of care |
| Active shortages affecting us / substitutions | Count of shortage NDCs or items on order failure, therapeutic substitutions made (ASHP list as the external feed) | PUR (wholesaler fill rate), INV, DISP, eMAR | National 225 active (ASHP Q3 2026) | Conservation, alternate sourcing, P&T substitution protocols |
| Inventory turns | Annual drug COGS ÷ average inventory value [UNVERIFIED definition; common target 10–12+, UNVERIFIED] | INV, PUR | Not sourced | Par optimisation, working capital |
| Waste / expired drug $ | Expired or returned or wasted dose $ ÷ purchases [UNVERIFIED] | INV, DISP waste transactions, returns | Not sourced | Par levels, dose rounding, multi-dose vial policy |
| 340B savings and compliance | Savings = WAC − 340B price on eligible dispenses; compliance = audit findings, duplicate discounts, diversion to ineligible patients (HRSA) [definition UNVERIFIED] | PUR (340B and GPO accounts), DISP, ADT and encounter eligibility, contract pharmacy claims | HRSA program total $81.4[B?] (2024) | Contract pharmacy strategy, rebate-model risk, audit readiness |
| Medication administration timeliness | % of scheduled doses given within the window (e.g. ±60 min for non-time-critical per CMS/ISMP) [UNVERIFIED] | eMAR | Not sourced | Workflow, staffing, first-dose turnaround |
| BCMA scanning compliance | % of administrations with both patient and medication scanned (Leapfrog) | eMAR/BCMA scan logs | Leapfrog 95% | Unit feedback, hardware fixes, override review |
| Controlled-substance diversion analytics | ADC withdrawals vs administration vs waste reconciliation; outlier users vs peers by unit and shift [UNVERIFIED: ASHP diversion guidelines exist but were not retrieved] | DISP (ADC), eMAR, waste witness, TK (who was on shift) | Not sourced | Investigations, DEA reporting |
| Formulary adherence | % of orders or doses on formulary, non-formulary request rate [UNVERIFIED] | CPOE orders, DISP | Not sourced | P&T decisions |
| IV room throughput | Doses compounded per hour and turnaround time; first-dose TAT (USP 797/800 context) [UNVERIFIED] | IV workflow system, DISP | Not sourced | Batching, staffing, outsourcing (503B) |

### Inferences
- Pharmacy questions are the most cross-source: shortages need PUR fill rates plus DISP and eMAR substitution evidence, diversion needs DISP + eMAR + TK, and 340B needs PUR + ADT eligibility. A shared decision context binding population and date scope is essential.
- 340B is under policy change (rebate-model risk), so "what is our 340B savings at risk?" is likely a high-frequency 2026 executive question. This is inferred from ASHP survey concern.

### Gaps
- No public benchmarks were found for inventory turns, waste, administration timeliness, formulary adherence or IV throughput.
- ASHP controlled-substance diversion guidelines were not retrieved; the peer-reviewed diversion analytics literature was not searched (tool budget).
- No current (2024–2026) Leapfrog BCMA compliance rates were found; the data above is 2017–2019.
- The unit of the HRSA 340B $81.4 purchase figure needs confirming.

---

## Supply chain

### Takeaway
The standard supply cost question is supply expense per (CMI-) adjusted discharge, rising about 5% year to date in 2026 (Kaufman Hall coverage). AHRMM's CQO framework explicitly says the raw per-adjusted-discharge metric alone is not meaningful and pairs it with quality and outcome measures for internal trending. Benchmarks for stockouts, standardization, implant price variation and recall response were not found in public sources this session.

### Cited Findings
- AHRMM CQO cost measure: supply expense per CMI-adjusted discharge, paired with HCAHPS (quality) and a Truven aggregated outcome score. — [AHRMM CQO metrics](https://www.ahrmm.org/resource-repository-ahrmm/cqo-metrics-100114-1?page=5)
- AHRMM-HFMA definition of supply expense: net cost of all tangible expensed items, including freight, standard distribution cost and sales and use tax, minus rebates. Labor is excluded. — same source
- AHRMM says traditional supply cost per adjusted discharge is "no longer viable" as a standalone indicator, and the CQO metrics are meant for internal directional trending rather than industry benchmarking. — same source
- Supply expense per adjusted discharge is up 5% year to date in 2026 (Kaufman Hall via Becker's), up 11.2% in one bed-size subgroup, and running "well above inflation" through June 2026. — [Becker's](https://www.beckershospitalreview.com/finance/43-of-hospitals-are-in-the-red-6-financial-notes/); [Kaufman Hall](https://www.kaufmanhall.com/insights/research-report/national-hospital-flash-report-april-2026-data)
- The AHA 2026 workforce scan cites supply chain volatility as a budget pressure. — [AHA Workforce Scan](https://www.aha.org/aha-talent-scan)

| Metric | Definition / source | Data | Benchmark | Decision |
|---|---|---|---|---|
| Supply expense per CMI-adjusted discharge | AHRMM-HFMA supply expense ÷ (adjusted discharges × CMI) (AHRMM CQO) | GL, PUR, census, CMI | +5% YTD 2026 trend (Kaufman Hall); internal trending only per AHRMM | Value analysis priorities |
| Supply cost per case by procedure and surgeon | Supply $ per case for the same DRG or CPT by physician [UNVERIFIED standard practice] | OR case logs, ITEM/UDI, PUR | Not sourced | Physician preference item (PPI) conversations, standardization |
| Implant and device price variation | Price paid vs contract or peer price for the same SKU or category [UNVERIFIED] | PUR, ITEM, GPO contract data | Not sourced | Renegotiation, vendor consolidation |
| Product standardization / compliance | % spend on contract or preferred SKU [UNVERIFIED] | PUR, ITEM | Not sourced | Formulary-style supply standardization |
| Stockouts / backorders | Count or % of requested lines not filled at par; fill rate [UNVERIFIED] | INV, PUR | Not sourced | Par levels, alternative sourcing |
| Recall response | Time to identify and quarantine recalled lots; patients implanted with recalled UDI [UNVERIFIED] | ITEM/UDI, OR logs, INV | Not sourced | Patient notification, vendor management |

### Inferences
- Supply chain questions that matter clinically (implant recalls, PPI variation by surgeon) need UDI capture in OR logs, which is often incomplete. This should be treated as a supported-unknown data-quality condition.
- Since AHRMM rejects standalone cost per adjusted discharge, a product answering "is our supply spend high?" should pair cost with an outcome or quality measure.

### Gaps
- AHRMM PPI resources and price-variation benchmarks were not found.
- No public stockout or fill-rate benchmarks, and no recall response-time standards, were found.
- Vizient and Premier supply benchmarks are proprietary.

---

## Which questions operations leaders ask most often (published sources)

### Takeaway
No published source ranks "questions asked of data" directly. Published priority surveys point to recruitment and retention (AONL: 49%, the top challenge), staffing (37%), staff well-being, workplace violence, financial constraints and labor and non-labor cost (AHA scan, Kaufman Hall), drug shortages (ASHP) and 340B (ASHP leader survey). These are the most frequently raised operational concerns.

### Cited Findings
- AONL 2025: recruitment and retention 49% (69% in 2023), staffing 37%, staff emotional health and well-being in the top three, followed by workplace violence, financial constraints, technology integration and onboarding. — [AONL Insight Study 2025](https://www.aonl.org/system/files/media/file/2025/03/AONL-Nursing-Leadership-Insight-Study-2025.pdf)
- The AHA 2026 workforce scan foregrounds labor cost, team redesign, AI-enabled workflow, retention of early-career staff and upskilling. — [AHA](https://www.aha.org/aha-talent-scan)
- Kaufman Hall 2026: cost pressure has shifted from labor to non-labor (supply +5%, drug +6% per adjusted discharge year to date). — [Becker's](https://www.beckershospitalreview.com/finance/43-of-hospitals-are-in-the-red-6-financial-notes/)
- ASHP: shortages cost about 1.5 FTE of effort in large hospitals, technician shortages are reported by more than 80%, and 60% expect 340B rebate-model disruption. — [Fierce Healthcare](https://www.fiercehealthcare.com/providers/hospitals-increasingly-tap-pharmacists-clinical-care-struggle-fill-rolls-survey); [MHE](https://www.managedhealthcareexecutive.com/view/pharmacy-leaders-in-ashp-survey-worry-about-340b-program-changes)
- MGMA 2026: pay rising while productivity falls drives wRVU and productivity questions. — [Physicians Practice](https://www.physicianspractice.com/view/physician-pay-productivity-split-mgma-report)

### Inferences
- Likely most-frequent data questions, inferred and not ranked by any source:
  1. Where and why is turnover (especially first-year) and vacancy highest, by unit and role?
  2. What are we spending on overtime and agency, and where can it convert to core staff?
  3. Are units staffed to grid or ratio by shift, as required in CA and OR and for committee and plan states?
  4. Is labor productivity (worked hours per unit of service) on target?
  5. Drug and supply spend per adjusted discharge trend and drivers.
  6. Shortage exposure and substitutions.
  7. 340B savings at risk.
  8. BCMA compliance and diversion outliers.
  9. Workplace violence rates.
  10. Provider wRVU productivity and access.

### Gaps
- No peer-reviewed or survey source was found that ranks the specific analytics questions or dashboards operations leaders query most. This would need sources such as CHIME, HIMSS or vendor (Epic, Premier, Vizient) usage studies, which were not searched within the tool budget.
