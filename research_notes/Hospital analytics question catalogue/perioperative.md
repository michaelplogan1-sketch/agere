# Perioperative, OR and procedural-area questions and metrics (US hospitals, as of Nov 2026)

Scope note: I ran 19 tool calls. The Springer full text of the AACD glossary, AORN pages, the ACS NSQIP Operations Manual, and Advisory Board/Vizient paywalled reports could not be fetched (403/404/auth redirect). Items marked **[UNVERIFIED]** come from background knowledge of the OR-management literature and were not confirmed against a fetched source in this session. The report writer should treat them as candidate definitions only.

## OR operations: utilization, block, on-time starts, turnover, case duration accuracy, cancellations, add-ons, overtime, volume, throughput, PACU holds, ASC migration, robotics, plus cath lab / IR / endoscopy

### Takeaway
The definitional standard for OR time stamps and derived intervals is the AACD Procedural Times Glossary (first published 1998, revised 2018, Boggs/Tsai/Urman, J Med Syst 42:171). Turnover is defined there as prior patient out of room to next patient in room. Published benchmarks for utilization, first-case on-time start and turnover vary widely, and most come from vendors or consultants. Any benchmark used must state the definition it rests on (window for "on time", raw vs adjusted utilization, prime-time hours).

### Cited Findings
**Definitional standard (AACD glossary)**
- The AACD Glossary of Times (Procedural Times Glossary, PTG) standardizes terms for OR and procedural areas. It was first published in 1998 and revised in 2018. It has four categories: procedural times; procedural and scheduling definitions and time periods; utilization and efficiency indices; and patient categories. — [Boggs, Tsai, Urman, J Med Syst 2018](https://link.springer.com/article/10.1007/s10916-018-1022-6); [Mount Sinai scholars record](https://scholars.mssm.edu/en/publications/the-association-of-anesthesia-clinical-directors-aacd-glossary-of-2/)
- AACD item 2.22, Turnover Time: from prior patient out of room to succeeding patient in room, for sequentially scheduled cases. Item 2.19, "Start Time" = patient in room time. Room clean-up time = patient out of room to room clean-up finished. — [AACD glossary Table 1 (search snippet)](https://link.springer.com/article/10.1007/s10916-018-1022-6/tables/1); [South African procedural times glossary (adaptation)](https://sajaa.co.za/index.php/sajaa/article/download/1176/1167)
- Applied use: a total joint arthroplasty study computed TOT = PIR(patient 2) − POR(patient 1), citing AACD. — [PMC11531768](https://pmc.ncbi.nlm.nih.gov/articles/PMC11531768/)
- Turnaround time (surgery end to next surgery start, i.e. close-to-incision) is used as a distinct metric from turnover in some literature. — [Clinical Ophthalmology](https://www.tandfonline.com/doi/pdf/10.2147/OPTH.S277550)
- Inaccurate anesthesia-start documentation has a measurable cost within a health system, so time-stamp data quality is itself a metric. — [PubMed 33577169](https://pubmed.ncbi.nlm.nih.gov/33577169)
- A 2008 Anesthesiology study took time definitions from the AACD glossary where possible. It noted that OR efficiency has traditionally been measured with block-time utilization and turnover time. — [Anesthesiology 2008;109(1):25](https://asa2.silverchair.com/anesthesiology/article-pdf/109/1/25/655865/0000542-200807000-00007.pdf)

**Benchmarks (mostly vendor/consultant; definitions vary)**
- Block utilization goals: one source says 90%, another 75–85%. AORN Outpatient Surgery suggests reserving blocks only for surgeons likely to fill at least 75%. — [search summary of AORN/ORManager/LeanTaaS/Impact Advisors pages](https://aorn.org/outpatient-surgery/article/2013-October-how-do-you-measure-up); [Impact Advisors](https://impact-advisors.com/article/surgical-block-utilization). The page fetch failed (404), so this is snippet-level only.
- First-case on-time start targets are 80–85%+. A McKesson OR Benchmarks Collaborative figure puts the median at 64.3% and the 90th percentile at 88.3%. Some sources count "on time" as within ±5 min of scheduled. — [OR Manager benchmark PDF (2012)](https://www.ormanager.com/wp-content/uploads/2012/01/0112_ORM_5.Benchmark_r.pdf); [LeanTaaS](https://surgical-clinics.leantaas.com/5-of-the-most-important-operating-room-performance-metrics/). These are snippet-level and dated.
- Turnover: the common target is about 20 min, with a typical range of 25–30 min and a survey median of 28.5 min. AORN Outpatient Surgery cites 10 min as a goal for ASCs. — [OR Manager](https://www.ormanager.com/wp-content/uploads/2012/01/0112_ORM_5.Benchmark_r.pdf); [AORN Outpatient Surgery 2013](https://aorn.org/outpatient-surgery/article/2013-October-how-do-you-measure-up) (snippet-level)
- A 2018 Hospital IQ survey found 71% of respondents had block utilization below 81%, and 65% named increasing surgical capacity as their top 12-month objective. — [HIT Consultant 2018](https://hitconsultant.net/2018/03/21/hospital-iq-perioperative-survey)
- At OHSU, a vendor-linked case reported +2% block utilization YoY, +3% prime-time utilization, and 25 staffing-coordination hours saved per week. At MultiCare, prime time is split into allocated, open, and urgent/emergency time, and the executive's view is that over-allocating block blocks OR access. — [Becker's: turning OR bottlenecks into growth](https://www.beckershospitalreview.com/hospital-management-administration/turning-or-bottlenecks-into-growth-lessons-from-multicare-ohsu-and-duke-health/)

**Case duration accuracy, block release, PACU constraint**
- Dexter and Macario: monitor each service's average bias in scheduled case durations, and use the 95% lower confidence bound to reduce over-utilized OR time. The same group published on how and when to release allocated OR time. — [Can J Anesth 2005](https://springerlink.bibliotecabuap.elogim.com/doi/10.1007/BF03022054)
- Over-predicting case duration causes idle time. Under-predicting causes overtime cost. Cutting under-prediction and absolute error shortened patient waits without lengthening surgeon waits. — [Duke thesis](https://dukespace.lib.duke.edu/server/api/core/bitstreams/0cf99a50-9392-43fb-8ade-dfe8eaa848dc/content)
- PACU as a constraint: a 2022 Anesthesia & Analgesia ML study (ambulatory center, 13,447 procedures, median total perioperative time 165 min) predicted whether a case would end by the 5 pm block end and whether the patient would leave PACU by 7 pm (end of recovery staffing). Balanced bagging reached F1 0.78–0.82. Top predictors were scheduled room time, scheduled incision time, specialty, and patient weight. — [PMC9172889](https://pmc.ncbi.nlm.nih.gov/articles/PMC9172889); [OR Manager summary](https://www.ormanager.com/?p=85850)

**Inpatient → outpatient / ASC migration (CMS policy driver)**
- The CY2026 OPPS final rule begins a three-year phase-out of the Inpatient Only list, with full elimination by 1 Jan 2029. Phase 1 removes 285 (mostly musculoskeletal) services and adds a new Level 7 MSK APC. A two-midnight exemption allows billing either inpatient or outpatient. — [AHA](https://www.aha.org/news/headline/2025-11-21-cms-issues-cy-2026-opps-final-rule); [McDermott+](https://www.mcdermottplus.com/?p=159021); [HFMA summary PDF](https://www.hfma.org/wp-content/uploads/2025/12/510110_CY_2026_OPPS_ASC_Final_Rule_Summary.pdf)
- ASC Covered Procedures List CY2026: 560 additions per McDermott+ (276 from revised criteria, 271 IPO removals, 13 commenter-recommended). Other summaries say 289, because they exclude the IPO-derived codes. The additions include 168 orthopedic codes, among them revision THA/TKA (Becker's). — [McDermott+](https://www.mcdermottplus.com/?p=159014); [Becker's ASC](https://www.beckersasc.com/?p=108492). Count discrepancy noted.

**Procedural areas (cath/EP lab)**
- MedAxiom recommends a cath/EP lab room utilization goal of 80–85%. Below 75% suggests room to improve. — [CI Today 2025](https://citoday.com/articles/2025-july-aug/when-is-enough-enough-rethinking-staffing-and-utilization-in-todays-cath-and-ep-labs)
- In a Cleveland Clinic cath lab pre/post study, days at full utilization rose from 7.7% to 77.3%. Improved start times and turnaround added 5.1–5.6 lab-hours/day. — [Cleveland Clinic ConsultQD](https://consultqd.clevelandclinic.org/process-improvement-initiatives-drive-dramatic-gains-in-cath-lab-efficiency-productivity)
- Some certificate-of-need states define cath lab capacity as 2,000 weighted cases/year. Analysts recommend building actual turnover into capacity calculations. — [Cath Lab Digest: evaluating true cath lab capacity](https://hmpgloballearningnetwork.com/site/cathlab/articles/running-out-space-evaluating-true-cath-lab-capacity-utilization)

### Inferences
Proposed canonical inventory (definitions below are AACD-style; those not sourced above are **[UNVERIFIED]**).

| Measure | Definition | Data required | Typical benchmark | Decision driven |
|---|---|---|---|---|
| Raw block utilization | In-room minutes of a block owner's cases within their block ÷ allocated block minutes | Block schedule, PIR/POR, case→surgeon/service | 75–85% (vendor); 90% goal in some sources | Reallocate / shrink / grow blocks |
| Adjusted block utilization **[UNVERIFIED]** | Raw utilization + credited turnover between owner's cases (and sometimes released time credit) ÷ allocated | As above plus turnover attribution rule | Same as above; must state adjustment | Same; fairer surgeon comparison |
| Prime-time utilization | In-room (± turnover) minutes during defined prime hours (often 07:00–15:00 or 17:00 weekdays **[UNVERIFIED]**) ÷ staffed prime-time room minutes | Room calendar, staffed hours, PIR/POR | OHSU-type +3% gains reported | Room count, staffing, open-time pool |
| Block release lead time and released-time pickup | Days before surgery block was released; % released minutes later filled | Block release log with timestamps; bookings | No sourced benchmark found | Auto-release rules (e.g. 72h/7d **[UNVERIFIED]**) |
| First-case on-time start (FCOTS) | % of first cases with PIR ≤ scheduled start (+ grace, commonly 0 or 5 min) | Scheduled vs actual PIR, delay reason codes | 80–85% target; McKesson median 64.3%, P90 88.3% | Pre-op readiness, surgeon/anesthesia arrival accountability |
| Turnover time | Prior POR → next PIR, same room, sequential cases (AACD 2.22) | PIR/POR, room, sequence; often exclude >60–90 min outliers **[UNVERIFIED]** | ~20–30 min typical; ASC ~10 min | EVS/team staffing, parallel processing |
| Turnaround / surgeon idle (close → next incision) | Closure to next incision | Close and incision times | No consensus benchmark | Surgeon satisfaction, induction rooms |
| Case duration accuracy | (Actual PIR→POR − scheduled duration); bias and % within ±15 min/±10% **[UNVERIFIED thresholds]** | Booked duration, actual in-room time, CPT, surgeon | Dexter: monitor service bias with 95% LCB | Use historical surgeon×procedure medians; scheduling rules |
| Same-day / day-of-surgery cancellation rate | Cases cancelled on day of surgery ÷ scheduled cases, by reason (patient, medical, facility, surgeon) | Cancellation log with timestamp and reason code | No sourced benchmark in this session | Pre-anesthesia clinic, H&P, insurance auth |
| Add-on / unscheduled case share; to-follow wait | Cases booked after schedule freeze; time booked→PIR by urgency class | Booking timestamp, urgency class (e.g. NSQIP/ACS emergency class), PIR | — | Dedicated urgent room, open-time pool |
| Overtime / after-hours use | Minutes past staffed room end (POR − staffed end), and cases starting after hours | Staffed room schedule, PIR/POR, payroll OT | — | Staffing model, late-room rules, Dexter overtime-vs-underutilization cost trade-off |
| Volume by service / surgeon / CPT / site of care | Counts and trend; IP vs HOPD vs ASC share | Case log with CPT, patient class, location | — | Growth, ASC JV, recruitment |
| Pre-op → PACU throughput | Arrival→PIR; POR→PACU in; PACU Phase I/II LOS; PACU hold (case finished, PACU bed unavailable, patient held in OR) | ADT/periop status timestamps, PACU bed census | No sourced benchmark found | PACU staffing to 7 pm etc.; inpatient bed flow |
| Robotic utilization | Robot-days used ÷ robot-days available; robotic share by procedure; robotic minutes vs lap/open | Equipment assignment per case, approach field | No sourced benchmark found | Purchase/placement of robots, block assignment |
| Cath/EP/IR/endoscopy room utilization | Same as OR, by lab; cases per room-day; weighted cases | Lab scheduling system, procedure codes | 80–85% (MedAxiom); CON 2,000 weighted cases/yr | Lab count, hours, staffing |

- Benchmark caveat: almost every published utilization and FCOTS number is vendor or consultant data, and they come without a common definition. A product should store the definition parameters (grace window, adjustment rule, prime-time hours, outlier cap) with every computed value.
- The IPO phase-out (2026–2029) and ASC CPL expansion make "site-of-care migration potential" (cases eligible for HOPD/ASC still done inpatient) a high-value question for 2026–2027.

### Gaps
- Full AACD glossary text (all ~80 items: anesthesia ready, procedure/incision start, procedure finish, raw vs adjusted utilization, prime time) is paywalled. Only turnover, start time and clean-up definitions were confirmed.
- No current (2025–2026), non-vendor national benchmarks for block utilization, FCOTS, cancellation rate or PACU hold were found. The McKesson/Change Healthcare OR Benchmarks Collaborative figures are dated (circa 2012).
- No authoritative endoscopy (e.g. ASGE unit efficiency) or IR benchmark was retrieved. Door-to-balloon (ACC/AHA, NCDR) and endoscopy quality metrics (adenoma detection rate, cecal intubation; ASGE/ACG) are relevant but were not fetched. **[UNVERIFIED]**: ADR target ≥25% overall is a commonly cited ASGE/ACG figure (some later updates raise it).
- Robotic utilization benchmarks: none found.

## Surgical quality and safety: SSI (NHSN), NSQIP outcomes, VTE, unplanned returns, never events, Leapfrog volume, ERAS adherence, CMS surgical measures

### Takeaway
The externally defined quality measures hospitals compute from their own data are Leapfrog volume standards (11 procedures, hospital and surgeon thresholds, revised April 2026), CMS IQR/VBP/HAC measures (THA/TKA complication, NHSN SSI colon and hysterectomy, PSI-90), and CMS TEAM episode measures for five surgical episodes (live January 2026). NSQIP outcomes are 30-day, abstracted by trained Surgical Clinical Reviewers, with definitions in the year-specific Operations Manual.

### Cited Findings
- Leapfrog 2026 Complex Surgery (fact sheet revised 04/01/2026) lists annual hospital volume / surgeon volume for privileging: bariatric 50/20; esophageal resection for cancer 20/7; lung resection for cancer 40/15; pancreatic resection for cancer 20/10; rectal cancer surgery 16/6; carotid endarterectomy 20/10; open aortic procedures 10/7; mitral valve repair and replacement 40/20; Norwood 8/5; TKR 50/25; THR 50/25. To meet the standard, a hospital must meet the hospital volume and have a privileging process requiring the surgeon volume. Mitral also requires STS ACSD participation and a 3-star STS MV composite. Norwood also requires STS CHSD participation. — [Leapfrog 2026 Complex Surgery Fact Sheet](https://ratings.leapfroggroup.org/sites/default/files/2026-04/2026%20Complex%20Surgery%20Fact%20Sheet.pdf); [Leapfrog 2026 measure page](https://ratings.leapfroggroup.org/measure/hospital/2026/complex-adult-and-pediatric-surgery)
- The Leapfrog ASC standard (2025) covers TKR, THR and bariatric with the same thresholds and a surgeon privileging requirement. — [Leapfrog ASC Complex Adult Surgery 2025](https://ratings.leapfroggroup.org/measure/asc/2025/complex-adult-surgery)
- Older compliance data (2019 survey): fewer than 3% of hospitals met the esophageal resection standard and 8% met pancreatic. The thresholds have changed since. — [Becker's](https://www.beckershospitalreview.com/quality/leapfrog-most-hospitals-don-t-meet-surgical-volume-standards-for-safety)
- CMS THA/TKA Complications measure (IQR): changes finalized, including removal of the COVID-19 exclusion. For FY2033 CMS plans to add Medicare Advantage patients and shorten the performance period to 2 years. Hospital-specific reports were previewed May 22–June 20, 2026. — [Medisolv 2026 IPPS final rule](https://blog.medisolv.com/articles/2026-ipps-final-rule-what-your-hospital-should-know); [MHA Medicare Quality Program Reference Guide 2026–2028](https://www.mha.org/wp-content/uploads/2026/04/Medicare-Quality-Program-Reference-Guide-2026-2028-v1.pdf)
- THA/TKA PRO-PM (IQR): pre-op data due 30 Sep 2026, post-op due 30 Sep 2027. A separate Information Transfer PRO-PM is in OQR. — [Medisolv 2026 IQR requirements](https://blog.medisolv.com/articles/2026-hospital-iqr-requirements)
- NHSN SSI Colon and Abdominal Hysterectomy (HAI) feed the Hospital VBP Safety domain as a weighted average. One source lists them as removed from IQR after FY2025 (they remain under HAC/VBP). Current status needs checking against the latest rule. — [WHA Quality Measures Inventory](https://www.wha.org/getmedia/beff515f-b3c7-4c53-a9b0-6b15b3a85471/WHA-Quality-Measures-Inventory-Table_updated8-11-25_1.pdf); [MHA guide](https://www.mha.org/wp-content/uploads/2026/04/Medicare-Quality-Program-Reference-Guide-2026-2028-v1.pdf)
- CMS TEAM (Transforming Episode Accountability Model): mandatory for selected acute care hospitals, running 2026–2030. Its five episodes are CABG, major bowel procedure, LEJR, SHFFT and spinal fusion. An episode runs from the procedure (IP or OP) through 30 days post-discharge. Target price discount is 1.5% for CABG and bowel, 2.0% for LEJR, SHFFT and spine. Quality feeds a Composite Quality Score: hybrid hospital-wide readmission, PSI-90, THA/TKA PRO-PM, and hospital harm/failure-to-rescue measures from PY2. — [CMS TEAM overview](https://www.cms.gov/priorities/innovation/files/team-overview-updated.pdf); [CMS TEAM quality measures fact sheet](https://www.cms.gov/files/document/team-intro-qm-fs.pdf); [IMO Health](https://www.imohealth.com/resources/the-2026-cms-team-model-explained/) (vendor details vary)
- ACS NSQIP: 150+ variables covering preoperative risk factors, intraoperative variables and 30-day mortality/morbidity, abstracted by certified Surgical Clinical Reviewers. Definitions live in the year-specific Operations Manual (Chapter 4). — [NSQIP PUF User Guide 2023](https://facs.org/media/ekmnc2ge/nsqip_puf_userguide_2023.pdf); [2022](https://ww2.facs.org/media/1nrdyqmr/nsqip_puf_userguide_2022.pdf)
- NSQIP-based morbidity composites include cardiac arrest, MI, PE, sepsis/septic shock, SSI, unplanned intubation, DVT and others, plus 30-day mortality, return to OR and readmission. An NQF-endorsed colorectal measure resolves outcomes within 30 days of the NSQIP-listed CPT procedure. — [NQF OT2-002-09](https://www.qualityforum.org/Projects/n-r/Patient_Outcomes/OT2-002-09.aspx); [typeset summary of Annals 2021](https://typeset.io/pdf/risk-associated-with-complications-and-mortality-after-5fe6n31bnv.pdf)

### Inferences
| Measure | Definition / source | Data required | Benchmark source | Decision |
|---|---|---|---|---|
| SSI SIR by procedure (NHSN) | Observed ÷ predicted SSIs within 30 or 90 days by NHSN procedure category (colon, HYST are CMS-reported) **[90-day window for implants: UNVERIFIED here]** | NHSN procedure denominator (ICD-10-PCS/CPT), surveillance cases, risk factors (ASA, wound class, duration) | NHSN national baseline SIR = 1.0 | Bundle compliance (antibiotic timing, normothermia, glucose) |
| NSQIP 30-day mortality, morbidity, readmission, unplanned return to OR, by risk-adjusted O/E | ACS NSQIP Operations Manual | Abstracted NSQIP data or EHR proxies (reoperation CPT within 30 days, readmission ADT) | NSQIP SAR decile/odds ratio | Service-level QI targets |
| THA/TKA complication (CMS) | 90-day complication measure **[UNVERIFIED detail]** | Claims | CMS national rate | IQR/VBP, TEAM |
| PSI-90 components (PSI 03, 06, 08, 09, 10, 11, 12, 13, 14, 15) | AHRQ PSI software | Discharge ICD-10 coded data with POA flags | AHRQ benchmarks | HAC Reduction Program, TEAM CQS |
| Perioperative VTE (PSI-12) and prophylaxis adherence | PSI-12 plus internal order/admin compliance **[former SCIP-VTE measures retired: UNVERIFIED]** | Orders, MAR, Caprini/risk score | — | Order set enforcement |
| Never events / serious reportable events (wrong site, retained foreign object) | NQF SRE list **[UNVERIFIED citation]**; PSI-05 retained object | Incident reporting, coded data | Target 0 | RCA, timeout compliance |
| Leapfrog volume compliance | Hospital and surgeon counts vs thresholds above | Case log by Leapfrog CPT/ICD lists, surgeon ID across all sites | Leapfrog standard | Privileging, regionalization of low-volume procedures |
| ERAS adherence | % of protocol elements delivered per case (e.g. carbohydrate loading, multimodal analgesia, early mobilization, early feeding) **[UNVERIFIED element list]** | Order and nursing documentation | ≥70% adherence often cited as associated with better outcomes **[UNVERIFIED]** | Pathway enforcement; LOS reduction |
| TEAM episode spend and CQS | CMS TEAM specs | Claims-based episode spend (CMS reports), internal cost | Target price | Post-acute referral, SNF use, readmission prevention |

- For VBC alignment, TEAM is the 2026 perioperative linkage: OR efficiency, LOS, discharge destination, readmission and PSI-90 now affect a hospital's Medicare reconciliation for five surgical episodes.

### Gaps
- NSQIP operational definitions (SSI subtypes, return to OR) were not retrieved; the Operations Manual is restricted to participants.
- NHSN 2026 SSI protocol details (procedure categories, 30 vs 90-day surveillance) were not fetched.
- ASCQR surgical measures (e.g. patient burn, fall, wrong site, all-cause hospital transfer, facility 7-day visits after outpatient colonoscopy, orthopedic procedure outcomes) were not retrieved. **[UNVERIFIED]**.
- No published ERAS adherence benchmark was retrieved.
- Whether CMS 2026 rules changed SSI measures in IQR versus HAC/VBP is ambiguous across sources.

## Surgical finance: cost per case, supply/implant variation, contribution margin by procedure and surgeon, preference card waste

### Takeaway
Supply and implant variation by surgeon for the same procedure is large (often 2–4x), usually without outcome differences. Preference-card waste (items picked or opened but unused) is quantifiable from pick-list vs used-item data. Most quantified savings come from vendors or GPOs and should be marked as such.

### Cited Findings
- A study of preference cards found unused-item cost averaged $1,294.41 per case ($3.7M across the study's cases). All three optimization approaches tested reduced this. Colorectal saw the largest relative reduction (55.8%). — [HealthDay report](https://www.healthday.com/healthpro-news/public-health/data-driven-surgical-supply-lists-cut-waste-cost) (the underlying journal was not identified in the snippet)
- HealthTrust example: one surgeon's supplies cost $300 per case versus $600 for another surgeon doing the same procedure. When used-item cost falls below picked-item cost, the card is inaccurate (items are pulled but not used). When picked cost exceeds documented-used cost in the other direction, staff lacked needed supplies. — [HealthTrust "It's in the cards"](https://healthtrustpg.com/thesource/cqo/clinically-integrated-supply-chain/its-in-the-cards/); [HealthTrust 2026 education deck](https://education.healthtrustpg.com/wp-content/uploads/2026/08/M_215PM_Reducing-Waste_PreferenceCard.pdf)
- At Mercy, the highest-cost preference card was $1,600/case and the lowest $400/case for the same procedure, with no outcome differences. — [HealthTrust/Mercy, via search summary](https://healthtrustpg.com/thesource/cqo/clinically-integrated-supply-chain/its-in-the-cards/)
- A UCSF Caring Wisely proposal: some surgeons have significantly higher cost per case and carbon footprint than peers despite equivalent quality. — [UCSF Caring Wisely](https://open-proposals.ucsf.edu/chv/cw-projects-2024/proposal/19201)
- Vendor cases: lap chole −$90/case, $42k/yr on 450 cases (Medtronic/IHS, South Africa, so not US). ENT ASC: −22% of cases with unused items, $160k+ on two procedures (vendor). — [Medtronic](https://www.medtronic.com/za-en/transforming-healthcare/aligning-value/perspective/case-studies/ihs-surgeon-preference-cards.html); [PREFcards](https://prefcards-preview.pages.dev/resources/ent-surgery-center-preference-card-savings/). Unverified vendor claims.
- Vizient has published on preference card management savings. — [Vizient 2025](https://www.vizientinc.com/insights/all/2025/unlocking-savings-through-preference-card-management) (not fetched)
- Labor and supply costs are named as the main drivers of perioperative spending in 2025 commentary. — [Becker's](https://www.beckershospitalreview.com/quality/labor-supply-costs-and-or-efficiency-whats-driving-perioperative-spending/)

### Inferences
| Measure | Definition | Data required | Decision |
|---|---|---|---|
| Direct cost per case (by CPT/MS-DRG, surgeon, site) | Supplies + implants + OR minutes × cost/min + anesthesia + PACU + LOS costs | Cost accounting (TDABC or RVU-allocated), case log, supply-chain usage (item, qty, unit cost), implant log | Surgeon variation review; standardization |
| Supply/implant cost variation | Per-procedure distribution of supply+implant cost; surgeon median vs peer median; coefficient of variation | Item-level used supply, implant price, CPT | Vendor/implant standardization, price negotiation |
| Preference-card waste | (Picked − used) cost per case; % of card items unused in ≥X% of last N cases | Pick list (card version), used/charged items, returned items | Card cleanup cycles |
| Contribution margin by procedure / surgeon / payer | Net revenue − variable direct cost (per case and per OR hour) | Payer remits/expected reimbursement, cost accounting, OR minutes | Block allocation by margin per OR hour; service-line strategy; ASC shift |
| OR cost per minute and overtime cost | Fully loaded staffed OR minute cost; OT premium minutes | Payroll, staffed schedule | Room-hours staffing |
| Site-of-care margin comparison | IP vs HOPD vs ASC margin for the same CPT | Payer contracts, IPO/CPL eligibility | ASC migration decisions under 2026 OPPS |

- Margin per OR hour combines finance and operations. It is the most decision-relevant single figure for block allocation, but it is politically sensitive. **[Inference, not sourced]**

### Gaps
- No peer-reviewed US benchmark for OR cost per minute was retrieved. The figure of roughly $36–$37/min (Childers & Maggard, JAMA Surg 2018, California) is **[UNVERIFIED]** from background knowledge.
- The primary journal citation for the $1,294 unused-item study was not identified.
- Advisory Board/Vizient benchmark reports are paywalled and were not reviewed.

## Which questions perioperative directors and surgical chairs ask most often (per published sources)

### Takeaway
Published surveys and leader commentary consistently put staffing and labor cost, OR capacity and access (block allocation and utilization, prime-time access), supply cost and shortages, and ASC/outpatient migration at the top. No rigorous, current, non-vendor ranking of "most-asked questions" was found.

### Cited Findings
- A 2018 Hospital IQ survey: 65% named increasing surgical capacity as their top 12-month objective, and 71% had block utilization below 81%. — [HIT Consultant](https://hitconsultant.net/2018/03/21/hospital-iq-perioperative-survey)
- A LeanTaaS survey for 2024: 76% of OR leaders named staff recruitment and retention as their primary concern. — [LeanTaaS](https://leantaas.com/blog/new-survey-shows-perioperative-leaders-focus-on-staffing-as-nationwide-shortages-continue-into-2024/) (vendor)
- An OR Manager survey (Sep 2025): leaders are adapting to supply strain, staffing pressure and ASC growth, and reworking staffing models. — [OR Manager](https://www.ormanager.com/briefs/survey-perioperative-leaders-adapt-to-supply-strain-staffing-pressure-asc-growth)
- An OR Manager poll (Jul 2025): leaders are tackling labor costs and staffing flexibility. — [OR Manager](https://ormanager.com/july-poll-results-perioperative-leaders-are-tackling-labor-costs-staffing-flexibility)
- ASC leaders rank reliable anesthesia coverage as a top operational risk for 2026. — [Becker's ASC](https://www.beckersasc.com/asc-news/asc-leaders-anesthesia-coverage-non-negotiables-for-2026/)
- In Becker's, perioperative leaders describe what is "draining surgical time" and limiting OR performance: late starts, long turnovers, block hoarding and PACU/bed constraints (from headlines/snippets, not full text). — [Becker's: what's draining surgical time](https://www.beckershospitalreview.com/quality/whats-draining-surgical-time/); [8 leaders on constraints](https://www.beckershospitalreview.com/quality/8-leaders-on-the-constraints-limiting-or-performance/)

### Inferences
Candidate question catalogue, phrased as leaders ask them:
1. Which blocks are under-used and should be released or reallocated, and who is waiting for time? (block utilization, release pickup, open-time demand)
2. Why are first cases late, and whose delays are they? (FCOTS with reason codes)
3. How long are turnovers by room, service and time of day, and where are the outliers?
4. Are booked durations accurate by surgeon and procedure? Are we running over or leaving gaps?
5. How many cases were cancelled day-of, and why? Which were preventable?
6. How much overtime and after-hours time are we using, and what does it cost?
7. Where are the bottlenecks from pre-op to PACU? How many PACU holds, and how many minutes of OR time are lost to them?
8. Which inpatient cases could move to HOPD or ASC under the 2026 IPO/CPL changes, and what would that do to margin and capacity?
9. Are the robots used enough, and which surgeons and procedures should get robot time?
10. Are we meeting Leapfrog volume standards for every procedure and every privileged surgeon?
11. How do our SSI SIR, NSQIP O/E, return-to-OR and readmission compare with peers?
12. What are our TEAM episode spend and quality score for LEJR, SHFFT, spine, CABG and bowel, and where does variation come from (LOS, discharge to SNF)?
13. Which surgeons' cost per case is highest for the same procedure, and does it buy better outcomes?
14. How much do we waste on unused preference-card items?
15. What is our contribution margin per OR hour by service and surgeon?
16. For cath, EP, IR and endoscopy: room utilization, start times and turnaround; do we need another lab?

### Gaps
- No peer-reviewed study ranking the questions perioperative directors ask was found. The ranking above is inferred from surveys (several vendor-sponsored) and trade press.
- Advisory Board and Vizient reports were not accessed (paywalled).
