# ED, Capacity and Patient-Flow Questions and Metrics (US hospitals, as of Nov 2026)

Research scope note: this pass used about 15 search/fetch calls. Several primary sources (eCQI measure page CMS1244, Chartis PDF, PMC, Becker's) returned 403 errors or binary content, so some definitions below are taken from search-result summaries of those pages and are marked as such. Items marked **[UNVERIFIED – background knowledge]** come from prior knowledge of standard specifications and were not re-confirmed against a primary source in this session. The report writer should treat them as candidates to verify.

## 1. ED throughput measures (definitions, sources, timestamps, benchmarks, decisions)

### Takeaway
The biggest recent change is CMS's Emergency Care Access & Timeliness (ECAT) eCQM, adopted in the CY 2026 OPPS final rule. It is a composite visit-level "failure" measure with four thresholds: arrival to treatment space over 1 hour, boarding over 4 hours, discharged-patient ED LOS over 8 hours, and leaving before evaluation. Voluntary reporting starts in CY 2027 and mandatory reporting in CY 2028 (CY 2030 payment). It replaces the chart-abstracted OP-18 (median arrival-to-departure for discharged patients) and OP-22 (Left Without Being Seen). ED-2 (admit-decision-to-departure boarding) was publicly reported from 2013 until it was removed in 2022. The EDBA time marks (arrival, practitioner contact, decision to admit/transfer, ED disposition) remain the operational lingua franca.

### Cited Findings

**ECAT eCQM (CMS1244, hospital OQR; CMS1264 for Rural Emergency Hospitals)**
- ECAT is an OQR eCQM meant to assess "emergency department access, patient flow, and timeliness of care." Voluntary reporting begins Jan 1, 2027 (CY 2027 reporting period). Mandatory reporting begins Jan 1, 2028 (CY 2028 reporting period, CY 2030 payment determination). It replaces "Median Time from ED Arrival to ED Departure for Discharged Patients" and "Left Without Being Seen," which stop being required from Jan 1, 2028 — [eCQI Resource Center: Upcoming ECAT eCQM](https://ecqi.healthit.gov/upcoming-emergency-care-access-and-timeliness-ecat-ecqm-hospital-oqr-program)
- The four failure thresholds per visit are: (1) more than 1 hour from arrival to treatment-room placement; (2) admitted patients boarding more than 4 hours after the admission decision; (3) ED LOS over 8 hours for discharged patients; (4) leaving before clinician evaluation. A visit fails if it meets any one of them (from search-result summaries of these pages) — [Chartis insight](https://www.chartis.com/insights/what-new-cms-ecat-measure-and-how-should-health-systems-prepare); [Health Affairs Forefront 2026](https://www.healthaffairs.org/do/10.1377/forefront.20260204.653186/full/); [Yale CORE ECCQ specifications](https://mmshub.cms.gov/sites/default/files/Yale-CORE-ECCQ-Measure-Specifications.pdf)
- In the precursor ECCQ specification, patients admitted to ED observation are included, but their boarding and ED LOS outcomes are not counted in the score (search-result summary) — [Yale CORE ECCQ specifications](https://mmshub.cms.gov/sites/default/files/Yale-CORE-ECCQ-Measure-Specifications.pdf)
- It is episode-based: an episode is an ED visit ending in the measurement period. The measure was designed to target ED boarding directly — [Medisolv: 2026 OPPS proposed rule](https://blog.medisolv.com/articles/2026-opps-proposed-rule-the-major-changes-to-outpatient-quality-reporting); [eCQI CMS1244 v1.2 QDM](https://ecqi.healthit.gov/sites/default/files/ecqm/measures/CMS1244-v1.2.000-QDM.html)
- Sources conflict on timing. Coronis and the eCQI page say CY 2027 optional. An OMB supporting statement cites the "CY 2029 payment determination." The eCQI page (voluntary 2027, mandatory 2028/CY 2030 payment) is the most authoritative of these — [Coronis Health](https://www.coronishealth.com/blog/2026-opps-final-rule-quality-programs); [OMB ICR](https://omb.report/icr/202608-0938-017/doc/171873600.pdf)
- For Rural Emergency Hospitals, ECAT is optional from CY 2027, as an alternative to the existing median-time measure (search summary of rule overview) — [NRHA CY 2026 OPPS final rule summary](https://www.ruralhealth.us/NationalRuralHealth/media/Documents/Advocacy/2025/CY-2026-OPPS-final-rule-summary-11-24-25.pdf); [AHA](https://www.aha.org/news/headline/2025-11-21-cms-issues-cy-2026-opps-final-rule)
- Failing OQR requirements costs a 2.0 percentage-point reduction in the OPPS annual update. CMS plans Care Compare public reporting and Star Rating inclusion (search summary) — [Coronis Health](https://www.coronishealth.com/blog/2026-opps-final-rule-quality-programs)
- ACEP issued a statement on the CY 2026 OPPS final rule and the new boarding measure — [ACEP press release 11/21/25](https://www.emergencyphysicians.org/press-releases/2025/11-21-25-acep-statement-on-cms-2026-opps-final-rule-and-new-emergency-department-boarding-measure)
- The FY 2027 IPPS proposed rule includes an RFI on an emergency access and timeliness eCQM for inpatient programs (search summary) — [QualityReportingCenter FY2027 IPPS proposed rule overview](https://qualityreportingcenter.com/globalassets/iqr2026events/052126iqr/fy2027_ipps-proposed-rule-overview-for-hospital-quality-programs-_vfinal_508.pdf)

**OP-18 (Median Time from ED Arrival to ED Departure for Discharged ED Patients)**
- OP-18 is the median minutes from ED arrival to ED departure for patients discharged from the ED. Patients who died in the ED are excluded. Data elements include arrival time, discharge code, E/M code, and ED departure date/time. Public reporting includes OP-18b (reporting measure), OP-18c (psychiatric/mental health patients; Data.cms.gov only) and OP-18d (transfer patients) (search summaries of the 2026 REH public reporting preview guides) — [QualityReportingCenter Oct 2026 public reporting guide](https://mp.qualityreportingcenter.com/globalassets/2026/08/iqr/pr_october-2026-rural-emergency-hospital-quality-reporting-public-reporting-preview-quick-reference-guide_vfinal508.pdf); [Joint Commission OP-18 sample size](https://jointcommission-ddsp.atlassian.net/wiki/spaces/DCS/pages/417267716/Chart-Abstracted+Sample+Size+Requirements+OP-18)
- 2026 quarterly submission deadlines listed by Telligen are Feb 2, Apr 30, Jul 31 and Nov 2 — [Telligen MBQIP deadlines](https://www.telligen.com/wp-content/uploads/2026/05/MonthlyMBQIPDataSubmissionDeadlines_202605_508.pdf)

**ED-1 / ED-2 (inpatient, retired)**
- In 2013 CMS required reporting of ED boarding time (ED-2) on Hospital Compare. That requirement was eliminated in 2022 during COVID-era reporting changes — [ACEP Now, Augustine, May 7, 2026](https://www.acepnow.com/article/to-optimize-patient-flow-we-need-accurate-standardized-data/)
- **[UNVERIFIED – background knowledge]** ED-1 was the median arrival-to-departure time for admitted patients. ED-2 was the median decision-to-admit to ED departure time for admitted patients, with "b" strata excluding psychiatric/mental health patients. Both moved to eCQMs (CMS55 / CMS111) before being retired. ED-1 was removed earlier than ED-2.
- **[UNVERIFIED – background knowledge]** OP-20 (door to diagnostic evaluation by a qualified medical professional) and OP-21 (median time to pain management for long-bone fracture) were removed from OQR in earlier rule cycles. OP-23 is the percentage of ED stroke patients who receive head CT/MRI interpretation within 45 minutes of arrival, for patients arriving within 2 hours of last known well. OP-22 is the percentage of patients who left without being seen, reported annually as an aggregate count.

**EDBA definitions (ACEP/EDBA consensus)**
- EDBA timestamps:
  - Arrival time: first recorded request for services.
  - Practitioner contact time: first contact by a physician or APP to start the medical screening exam.
  - Decision-to-admit time: decision to place the patient in inpatient or observation status.
  - Decision-to-transfer time.
  - ED disposition time: physical departure from the ED treatment area.
- EDBA intervals: door to practitioner (arrival to contact), door to decision, boarding time (decision to disposition), ED LOS (arrival to disposition). Medians are used for all time parameters. The first summit was held Feb 23, 2006, with 18 organizations. The definitions "have been stretched recently" — [ACEP Now, Augustine, May 7, 2026](https://www.acepnow.com/article/to-optimize-patient-flow-we-need-accurate-standardized-data/)
- In a March 31, 2026 comment letter on ECAT specifications, the EDBA recommends that boarding start at the earliest electronic evidence of admission intent (admission order **or** bed request) and be reported in total minutes (search summary of the letter) — [EDBA Recommendations for Acumen, 3/31/2026](https://oncprojectracking.healthit.gov/support/secure/attachment/102286/EDBA%20Recommendations%20for%20Acumen%203.30.26.docx)
- LWBS is defined as leaving after registration but before screening by a qualified medical professional in triage (if present) or before provider contact (AAAEM/ACEP benchmarking survey definition, via search summary) — [Springer supplementary (BMC Emerg Med 2019)](https://www.springermedizin.de/resource/supplementary/12873_2019_285_MOESM1_ESM.pdf/17415492)
- **[UNVERIFIED – background knowledge]** The EDBA family of walkaway measures also includes LBTC (left before treatment complete, the umbrella term), LWBS, LAMA (left against medical advice) and elopement. Its fourth summit addressed intake, boarding, diversion and observation definitions (Yiadom et al., Acad Emerg Med 2020, consistent with the search hit [zendy 10.1111/acem.13978](https://zendy.io/title/10.1111/acem.13978)).
- Door-to-provider time can be gamed, for example by provider-in-triage contact that does not change care — [Becker's, The quiet gaps in ED performance data](https://www.beckershospitalreview.com/care-coordination/the-quiet-gaps-in-ed-performance-data-and-what-execs-can-do-about-them/)

**Benchmarks**
- EDBA-based analysis: each 10 minutes of boarding was associated with roughly a 0.1% increase in LWBS and a 3-minute increase in discharged-patient LOS (search summary) — [ACEP: longer boarding times predict patient processing](https://www.acep.org/acep-news/acep-in-the-news-redirects/acep22-researh-forum-coverage/longer-boarding-times-predict-patient-processing-in-ed)
- Median ED LOS for all patients was 184 min in 2023, down from 205 min in 2022 (Becker's citing EDBA, not split by disposition; search summary only, page 403) — [Becker's, ED length of stay falls in 2023](https://www.beckershospitalreview.com/care-coordination/ed-length-of-stay-falls-in-2023-3-takeaways.html)
- A 2020 EDBA 50th-percentile admitted-patient LOS of 401 min appears in an ENA public comment (secondary, dated) — [ENA crowding/boarding public comment, Apr 2025](https://ena.org/sites/default/files/2025-04/Crowding%20Boarding%20and%20Patient%20Throughput%20Public%20Comment.pdf)
- EDBA has described a recent LBTC rate as the highest ever seen (no dated figure retrieved) — [Becker's, Record number of patients leaving EDs](https://www.beckershospitalreview.com/care-coordination/record-number-of-patients-leaving-eds-without-complete-care/)
- EDBA survey size conflicts across sources: 1,934 EDs/41M visits; 1,148 full-service EDs/49M; 1,840 EDs/58.5M (including 390 freestanding and 50 pediatric) — [Becker's quiet gaps](https://www.beckershospitalreview.com/care-coordination/the-quiet-gaps-in-ed-performance-data-and-what-execs-can-do-about-them/); [Maryland HSCRC ED Best Practice WG](https://hscrc.maryland.gov/Documents/Jan%2010%20ED%20Best%20Practice%20WG%20Meeting%20-%20Final%20%28combined%29xx.pdf)
- About 68% of hospital admissions come through the ED, and about 19% of ED visits result in admission — [ACEP Now, Augustine 2026](https://www.acepnow.com/article/to-optimize-patient-flow-we-need-accurate-standardized-data/)
- A national operations database analysis of factors associated with ED LOS — [WestJEM](https://westjem.com/articles/operations-factors-associated-with-emergency-department-length-of-stay-analysis-of-a-national-operations-database.html)

**Ambulance offload (APOT) / diversion**
- California HSC §1797.120 directs EMSA to set a standard method for calculating and reporting ambulance patient offload time — [CA AB 2961 chaptered](https://legiscan.com/CA/text/AB2961/id/1820757/California-2017-AB2961-Chaptered.html); [HSC 1797.120](https://california.public.law/codes/health_and_safety_code_section_1797.120)
- California AB 40 draft regulations use NEMSIS eTimes.11 (arrival at destination/ambulance bay) to eTimes.12 (transfer of patient care). The APOT standard cannot exceed 30 minutes at the 90th percentile, as a local-agency maximum. The regulations have been emergency re-adoptions in 2025–2026, and final status is unconfirmed — [EMSA AB 40 APOT reg text](https://emsa.ca.gov/wp-content/uploads/sites/71/2025/06/AB-40-APOT-Regulations-Reg-Text.pdf); [EMSA re-adopt notice Feb 2026](https://emsa.ca.gov/wp-content/uploads/sites/71/2026/03/Notice-and-FOE-_-Implementation-of-AB-40_-2.20.26-Re-Adopt.pdf)
- Local standards are stricter: San Francisco's Nov 2026 public-comment draft sets 20 minutes for 90% of transports, and Stanislaus County uses 20 minutes. San Diego uses the eOther.19 signature timestamp — [SF EMSA 4000.1 draft](https://media.api.sf.gov/documents/EMSA-4000.1-Ambulance-Patient-Offload-Time-Standard-Public-Comment-November-2026.pdf); [Stanislaus](https://oes.stancounty.gov/home/showpublisheddocument/2484/639065666793730000); [San Diego County](https://www.sandiegocounty.gov/content/sdc/ems/transfer_of_care)
- Texas tracks "EMS wall time" for state reporting — [Texas DSHS GETAC EMS Wall Time 2024](https://Dshs.texas.gov/sites/default/files/emstraumasystems/GETAC/PDF/EMS-Wall-Time-2024.pdf)

**Psychiatric boarding**
- OP-18c separately reports psychiatric/mental health discharged patients — [QualityReportingCenter Oct 2026 guide](https://mp.qualityreportingcenter.com/globalassets/2026/08/iqr/pr_october-2026-rural-emergency-hospital-quality-reporting-public-reporting-preview-quick-reference-guide_vfinal508.pdf)
- Joint Commission boarding standards and leadership collaboration with the behavioral health community — [OMW Law](https://omwlaw.com/news-events/joint-commission-standards-for-boarding-and-leadership-collaboration-with-behavioral-health-community); [American Nurse, psychiatric ED boarding](https://www.myamericannurse.com/problem-of-psychiatric-patient-ed-boarding/)

### Inferences
- Visit-level threshold flags (>1 h to room, >4 h boarding, >8 h discharged LOS, LWBS) will now sit beside medians. Analytics should compute both per visit, with ESI, age and mental-health stratifiers, and keep observation patients separate.
- Boarding start is contested: admit order versus bed request versus decision documented. A warehouse should store all candidate timestamps and report which one anchors the interval.
- The minimum ED timestamp set is: arrival/registration, triage, room placement, practitioner contact, decision to admit/transfer (order), bed request, bed assigned, bed ready, ED departure, plus disposition code, ESI, mode of arrival (EMS arrival and transfer-of-care times), and a behavioral-health flag.

### Gaps
- The exact ECAT CMS1244 numerator logic, stratifications (age, behavioral health), exclusions and value sets could not be retrieved (eCQI page returned 403; Chartis PDF did not decode).
- Current 2024/2025 EDBA benchmark values (median LOS discharged/admitted, LWBS/LBTC %, door-to-provider) were not obtained from a primary source.
- I found no CMS pediatric readiness ED measure in 2026 OPPS/IPPS rulemaking. The National Pediatric Readiness Project score (HRSA EMSC/AAP/ACEP/ENA) was not researched this pass.
- ESI-stratified LOS benchmarks, ED visits per hour by arrival (arrival curves), 72-hour return-with-admission definitions (a CMS/EDBA quality signal), and diversion hours definitions were not verified.

## 2. Inpatient flow measures

### Takeaway
Inpatient flow measures (occupancy, discharge timing, LOS vs GMLOS, observation, transfers, bed turnaround) are mostly internal operational metrics without a single federal specification. The Joint Commission's LD.04.03.11 requires leaders to measure and manage flow, and it recommends that boarding not exceed 4 hours. Evidence on the most popular lever, discharge before noon (DBN), is mixed. DBN goals raise early-discharge percentages but do not consistently reduce ED boarding.

### Cited Findings
- Joint Commission LD.04.03.11, the "patient flow standard," covers how leaders use data to identify, mitigate and manage flow issues. The revisions were published in 2012, and the boarding elements took effect Jan 1, 2014. An EP 6 note says boarding "not exceed 4 hours" in the interest of safety. Hospitals are not scored on the 4-hour figure, but surveyors ask leaders what conditions cause boarding beyond 4 hours. Goals should reflect acuity and best practice. Current E-dition wording and EP numbering were not confirmed for 2026 — [Becker's: Joint Commission clarifies patient flow standard](https://www.beckershospitalreview.com/capacity-management/joint-commission-clarifies-qpatient-flow-standardq.html); [MDedge: Joint Commission beefs up patient flow rules](https://www.mdedge.com/content/joint-commission-beefs-patient-flow-rules); [Joint Commission R3 Report issue 4](https://jointcommission.org/-/media/tjc/documents/%20standards/r3-reports/r3_report_issue_4.pdf)
- **[UNVERIFIED – background knowledge]** The LD.04.03.11 elements of performance include measuring and setting goals for: inpatient bed availability, efficiency of areas where patients receive care, safety of areas where care is provided, support services (lab, imaging, EVS, transport), behavioral health patient flow, and boarding (ED and other areas). Results go to leaders, who act on them.
- Discharge before noon: a 2024 pre/post study found DBN initiatives raised DBN percentage but did not reduce ED boarding time or 30-day readmissions. A multi-year academic program raised DBN from 9.5% to 26.8%, with risk-adjusted LOS falling from 1.16 to 1.09. A 2021 study of 189,781 patients at seven Canadian hospitals found no association between morning discharge and ED or hospital LOS, though it did not report boarding (search summaries) — [ACEP Now: hospital-wide strategies for reducing discharge delays and boarding](https://acepnow.com/article/hospital-wide-strategies-for-reducing-inpatient-discharge-delays-and-boarding/4); [UTHSCSA: increasing and sustaining discharges by noon](https://scholars.uthscsa.edu/en/publications/increasing-and-sustaining-discharges-by-noon-a-multi-year-process/); [MDedge: Discharge before noon, benefits and costs](https://mdedge.com/content/discharge-noon-toward-better-understanding-benefits-and-costs)
- Interhospital transfer: in 24,352 transfers to Brigham and Women's, an acceptance-to-arrival delay over 48 h was not significantly associated with ICU transfer within 48 h or 30-day mortality. Nighttime arrival was associated with higher odds of both — [J Hosp Med via MDedge: Interhospital transfer processes and outcomes](https://www.mdedge.com/content/interhospital-transfer-transfer-processes-and-patient-outcomes/page/0/1)
- Johns Hopkins uses a clinical nurse expediter role to move patients forward (discharge facilitation) — [Hopkins Medicine news, June 2025](https://www.hopkinsmedicine.org/news/articles/2025/06/making-room-for-care-clinical-nurse-expediter-helps-patients-move-forward)
- **[UNVERIFIED – background knowledge]** Standard definitions not re-verified this session:
  - Occupancy is midnight census (or hourly census) divided by staffed beds.
  - GMLOS is the geometric mean LOS published per MS-DRG in the IPPS Table 5. LOS index (O/E) is observed LOS divided by GMLOS or a risk-adjusted expected LOS (vendor models such as Vizient).
  - The two-midnight rule (42 CFR 412.3) presumes inpatient admission is appropriate when the admitting practitioner expects medically necessary care spanning two or more midnights. Since CY 2024 it also applies to Medicare Advantage plans (CMS-4201-F).
  - Observation hours run from the observation order to the discharge or inpatient order. Observation-to-inpatient conversion rate is the percentage of observation stays converted to inpatient.
  - Avoidable or excess days are days beyond the expected LOS or days without medical necessity (InterQual/MCG criteria, care-management "delay codes").
  - Bed turnaround is discharge (patient out) to bed clean/ready; EVS response is clean request to clean complete.
  - Discharge order to departure: hospitals commonly target a 2-hour window.

### Inferences
- These metrics need ADT event-level data (admit, transfer, discharge with unit and bed, level of care), order timestamps (discharge order, admit order, observation order), EVS bed-status events (dirty, cleaning, clean), transfer-center logs (request, accept/decline reason, arrival), and care-management avoidable-day/delay codes. Hourly census by unit is derivable from ADT.
- Since DBN evidence is weak, a question catalogue should pair "DBN %" with the outcomes it is supposed to move (boarding hours, ED LOS for admitted patients, PACU holds), not report it alone.

### Gaps
- No primary source this pass for the GMLOS/O:E, two-midnight, observation conversion or bed-turnaround benchmarks.
- No published benchmark for transfer-center acceptance or decline rates, ICU step-down delay, or post-acute placement delay days was found.
- Current LD.04.03.11 text in the 2026 Joint Commission manual was not verified.

## 3. Capacity planning and forecasting questions command centers ask

### Takeaway
Command centers (Johns Hopkins since October 2016 is the reference case) ask real-time and near-term questions: current and predicted census by unit, pending admissions and discharges, bed requests waiting, transfer requests, OR/PACU holds, and surge triggers. Reported results come from operational self-reports, not controlled studies.

### Cited Findings
- Johns Hopkins opened its Capacity Command Center in Oct 2016 for bed management, transfers and surge. By Mar 1, 2021 it had managed the transfer of 659 COVID-19 patients within the system — [Clinician.com: COVID-19 put pioneering capacity command center to the test](https://www.clinician.com/articles/148137-covid-19-pandemic-put-pioneering-capacity-command-center-to-the-test)
- Reported Hopkins results (HealthLeaders, June 2018): OR holds down 80%, ED boarding down 20%, transfers accepted from other hospitals up 60%. Baselines and periods are not given; these are operational/vendor-associated claims, not peer reviewed — [HealthLeaders: 4 insights to build a better command center](https://www.healthleadersmedia.com/innovation/4-insights-build-better-command-center)
- Johns Hopkins Malone Center modeling with the command center: a capacity dashboard and simulation suggested about a 40% reduction in additional beds needed in a single hospital, more when operated as a network, with strategic transfers to balance load (modeling, not an observed result) — [JHU Malone Center: Wait watchers](https://malonecenter.jhu.edu/wait-watchers-a-new-data-driven-interface-shows-potential-in-slimming-down-hospital-waits)
- JHHS presentation on capacity and ED wait time (Maryland Hospital Association, 2024) — [Peter Hill, JHHS capacity and ED wait time](https://mhaonline.org/wp-content/uploads/2024/05/peter-hill_jhhs_capacity-and-ed-wait-time.pdf)
- Industry description: command centers monitor delays and reduce transfer declines by streamlining acceptance pathways (non-study) — [ECG Management Consultants](https://ecgmc.com/insights/blog/1522/why-are-more-hospitals-building-command-centers)

### Inferences
- The typical command-center question set is:
  - "What will census be at 7am / noon / midnight by unit?"
  - "How many beds will open today and when?"
  - "Which patients are medically ready but waiting (post-acute, transport, consult)?"
  - "How many transfer requests did we decline for capacity, and from where?"
  - "Do we need to open surge beds or call in staff for the next shift?"
  - "Which ED boarders have been waiting more than 4 hours?"
- These map to predicted census (arrivals forecast plus LOS-based discharge prediction), expected-discharge lists, and staffing-to-demand ratios (nurse hours per patient day versus forecast census).

### Gaps
- No peer-reviewed comparative study of command-center questions or forecasting accuracy was retrieved. Hopkins figures are self-reported.
- Staffing-to-demand metrics (HPPD, nurse-to-patient ratio vs forecast) and surge-trigger definitions were not researched with primary sources.

## 4. Evidence on which questions are asked most often

### Takeaway
There is no published, ranked inventory of "most-asked" flow questions. The best proxy is what is standardized and mandated. That means EDBA's core intervals (door-to-provider, LOS by disposition, boarding, LBTC), CMS's reported measures (OP-18, OP-22, now ECAT), and Joint Commission LD.04.03.11 boarding and flow goals. Professional commentary puts boarding at the center, as the driver of LWBS and discharged-patient LOS.

### Cited Findings
- Boarding is the measure ECAT was designed to target, and the boarding increment predicts LWBS and discharged LOS — [Medisolv](https://blog.medisolv.com/articles/2026-opps-proposed-rule-the-major-changes-to-outpatient-quality-reporting); [ACEP research forum coverage](https://www.acep.org/acep-news/acep-in-the-news-redirects/acep22-researh-forum-coverage/longer-boarding-times-predict-patient-processing-in-ed)
- Health Affairs pieces discuss federal and state approaches to boarding and argue for moving beyond time-based metrics — [Health Affairs: Hospital boarding in the ED](https://www.healthaffairs.org/content/forefront/hospital-boarding-ed-federal-state-and-other-approaches); [Health Affairs: Moving beyond time-based metrics](https://www.healthaffairs.org/content/forefront/moving-beyond-time-based-metrics-emergency-department-boarding)
- Boarding and handoff practice vary widely across hospitals (University of Michigan IHPI) — [IHPI: Waiting for a bed, but not for care](https://ihpi.umich.edu/news-events/news/waiting-bed-not-care-emergency-department-boarding-study-finds-wide-variation)
- Executive-facing commentary flags data-quality gaps: inconsistent timestamps and gameable door-to-provider — [Becker's quiet gaps](https://www.beckershospitalreview.com/care-coordination/the-quiet-gaps-in-ed-performance-data-and-what-execs-can-do-about-them/)
- Boarding myths review (Kelen, 2024) — [Alabama ACEP: Kelen boarding myths](https://alacep.org/wp-content/uploads/2024/07/6-Kelen_Boarding-Myths_web.pdf)

### Inferences
- Likely priority ordering for a catalogue (inferred from mandates and commentary, not from a frequency study):
  1. Boarding hours and count over 4 h
  2. ED LOS by disposition and ESI
  3. LWBS/LBTC %
  4. Door-to-provider and arrival-to-room (now an ECAT threshold)
  5. Occupancy/census and predicted census
  6. Discharge timing (DBN, order-to-departure)
  7. LOS O/E and avoidable days
  8. Transfer acceptance/declines
  9. APOT (where state-mandated, such as California)
  10. Observation hours and conversion
  11. Psychiatric boarding

### Gaps
- No survey or log study ranking actual question frequency among hospital ops, ED, nursing or command-center users was found. Advisory Board and IHI patient-flow resources were not retrieved this pass (IHI "Achieving Hospital-wide Patient Flow" white paper is a known resource, unverified here).
