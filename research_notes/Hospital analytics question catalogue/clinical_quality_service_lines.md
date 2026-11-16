# Clinical quality, patient safety and service-line analytics questions US hospitals ask of their own data

Scope: internal quality and service-line analytics, not the CMS program lists, which another researcher covers. Research date 2026-11-16. Sources were reached through web search snippets plus a few fetch attempts; the heart.org recognition page returned HTTP 403. Tags used throughout:
- **[V]**: confirmed in a cited source in this session.
- **[U]**: unverified, from practitioner or background knowledge, with no source retrieved this session. Check before use in product logic.

Data-need shorthand: DX = ICD-10-CM diagnosis with POA flag; PX = ICD-10-PCS/CPT procedure; LAB-T = lab result with collection and result timestamps; VIT-T = timestamped vitals; MAR = medication administration record with administration times; ORD = orders with timestamps; NOTE = clinical notes (NLP or abstraction); ABS = registry chart abstraction; ADT = admission/discharge/transfer events and unit census; DISP = discharge disposition; FU = post-discharge encounters or claims.

---

## Q1. Condition and service-line questions (sepsis, HF, AMI, stroke, pneumonia, COPD, CABG, joint replacement, maternity, pediatrics, behavioral health)

### Takeaway
Service-line teams mostly track process timing (time zero to lactate, cultures, antibiotics and fluids; door-to-device and door-to-needle; GDMT at discharge) plus outcome tails (mortality, readmission, LOS). The definitions come from registry programmes: SEP-1 abstraction, AHA GWTG, ACC NCDR, Joint Commission PC/HBIPS and CDC SMM. Nearly all timing measures need timestamped labs, MAR and orders, or registry abstraction. Claims codes alone cannot compute them.

### Cited Findings

**Sepsis (SEP-1 and internal sepsis dashboards)**
- **Severe sepsis definition.** At least 2 SIRS criteria plus suspected infection, plus either organ dysfunction or lactate ≥2 mmol/L. **Septic shock** is SBP <90 after fluids or lactate ≥4. Data: VIT-T, LAB-T, NOTE (suspected infection), ORD. [V] — [PMC9924005 Table 1](https://pmc.ncbi.nlm.nih.gov/articles/PMC9924005/table/tbl0001)
- **Time zero.** The moment all qualifying criteria are first present. Every bundle deadline runs from it. [V] — [Munson Healthcare sepsis algorithm](https://www.munsonhealthcare.org/media/file/Physician%20Services/Practice%20Pro/Sepsis%20Algorithm.pdf)
- **3-hour bundle.** Initial lactate, blood cultures before antibiotics, broad-spectrum antibiotics, and 30 mL/kg crystalloid for septic shock or initial hypotension. [V] — [PMC8189682 Table 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC8189682/table/T3)
- **6-hour bundle.** Repeat lactate if the initial value was elevated; vasopressors for persistent hypotension after fluids; a volume status or perfusion reassessment exam, which needs NOTE abstraction. [V] — [PMC9924005](https://pmc.ncbi.nlm.nih.gov/articles/PMC9924005/)
- **Compliance drivers.** IDSA called the criteria "complex and heterogeneous", a source of abstraction variability, and asked for a more precise shock time zero. It proposed dropping lactate and moving to antibiotics within 1 h of septic-shock time zero. [V] — [PMC7024755](https://pmc.ncbi.nlm.nih.gov/articles/PMC7024755)
- **Contested components.** The 3-h antibiotic window for all patients, the blanket 30 mL/kg fluid requirement and the repeat lactate are disputed. Teams therefore track antibiotics within 1 h for shock and within 3 h for sepsis as separate questions. [V] — [PMC10802116](https://pmc.ncbi.nlm.nih.gov/articles/PMC10802116)
- **Compliance and outcomes.** SEP-1 compliance is linked to survival benefit and cost savings, which is the internal ROI argument. [V] — [endsepsis.org compliance study PDF](https://endsepsis.org/wp-content/uploads/2019/02/Survival_Benefit_and_Cost_Savings_From_Compliance.96736-1.pdf)

**Heart failure (AHA GWTG-HF)**
- **2025 recognition measures (2024 data).** These include an SGLT2 inhibitor at discharge for HFrEF (AHAHF93), together with beta blocker, MRA and ACEi/ARB/ARNi at discharge. [V] — [AHA 2025 HF recognition PDF](https://heart.org/-/media/Files/Professional/Quality-Improvement/Get-With-the-Guidelines/GWTG-Award-Criteria/2025-Recognition-Documents/HFRecognition2025112023.pdf?sc_lang=en)
- **HFpEF/HFmrEF SGLT2i at discharge (AHAHF94).** Required for Joint Commission Advanced Certification in Heart Failure from 2025-01-01. [V] — [Joint Commission, Sept 2024](https://www.jointcommission.org/resources/news-and-multimedia/newsletters/newsletters/joint-commission-online/sept-18-2024/performance-measure-changes-for-advanced-certification-in-heart-failure-for-2025)
- **2026 rural HF criteria.** Beta blocker, MRA, ARNi, SGLT2i and LV-function measures, with a composite goal of at least 60%. [V] — [AHA 2026 Rural HF criteria](https://www.heart.org/-/media/Files/Professional/Quality-Improvement/Recognition-Criteria/2026/2026-Rural-HF-Recognition-Criteria-61125.pdf?sc_lang=en)
- **ACC/AHA/HFSA performance measures.** GDMT at discharge for HFrEF, and SGLT2i for HFrEF and HFpEF within 12 months, including at discharge. [V] — [Cardiovascular Business](https://cardiovascularbusiness.com/topics/clinical/heart-failure/cardiology-groups-introduce-new-performance-quality-measures-heart-failure)
- **Data.** Discharge medication list or reconciliation, LVEF from an echo report (NOTE or structured), documented contraindications (NOTE/ABS), DX for HF type.

**AMI (ACC NCDR Chest Pain–MI Registry)**
- **2026 Performance Achievement Award composites.** STEMI first medical contact (FMC) to device ≤90 min, door-to-needle ≤30 min for lytics, and door-in-door-out for transfers, plus NSTEMI and discharge-medication measures. The 2027 award uses 2025–2026 data. [V] — [ACC Chest Pain–MI award page](https://cvquality.acc.org/NCDR-Home/registries/hospital-registries/action-registry/action-registry-performance-achievement-awards)
- **Door-to-balloon target.** ≤90 min. After the 2006 D2B initiative, more than 75% of patients in participating hospitals met it by 2008. [V] — [ACC, D2B 10 years later](https://acc.org/latest-in-cardiology/articles/2016/11/29/10/30/door-to-balloon-10-years-later?w_nav=LC)
- **Mortality by D2B time (NRMI 1999–2002, historical).** 3.0% at ≤90 min, 4.2% at 91–120, 5.7% at 121–150 and 7.4% at >150 min. [V] — [ACC, D2B 10 years later](https://acc.org/latest-in-cardiology/articles/2016/11/29/10/30/door-to-balloon-10-years-later?w_nav=LC)
- **Data.** EMS FMC time (often from the EMS ePCR, outside the EHR), ED arrival (ADT), first ECG time, cath-lab device time (procedure log), transfer times, discharge medications. Registry abstraction is typical.

**Stroke (AHA GWTG-Stroke / Target: Stroke Phase III)**
- **Honor Roll tiers.** DTN ≤60 min in at least 75% of patients (Honor Roll) or at least 85% (Elite). Elite Plus needs ≤45 min in at least 75% and ≤30 min in at least 50%. Advanced Therapy needs door-to-device ≤90 min for direct arrivals and ≤60 min for transfers in at least 50% of patients. Eligibility requires GWTG-Stroke Gold, Silver or Bronze status and at least 6 consecutive applicable patients per quarter. [V] — [AHA Target: Stroke Honor Roll](https://www.heart.org/en/professional/quality-improvement/target-stroke/target-stroke-honor-roll)
- **Conflict on launch goals.** The 2019 Virginia slides list Phase III goals as 60 min in at least 85% and 30 min in at least 50%. A PMC study gives 60/45/30 min in at least 85/75/50%. The current AHA page should be treated as authoritative. [V] — [VDH 2019](https://www.vdh.virginia.gov/content/uploads/sites/133/2023/07/Target-Stroke-Phase-III_VSSTF_April-19-2019.pdf); [PMC10851100](https://pmc.ncbi.nlm.nih.gov/articles/PMC10851100)
- **Data.** Last-known-well time (NOTE/ABS), arrival, CT time, thrombolytic MAR time, groin puncture and reperfusion times, NIHSS.

**Maternity and perinatal (Joint Commission PC, CDC SMM)**
- **Chart-abstracted measures.** PC-01 Elective Delivery, PC-02 Cesarean Birth (NTSV), PC-05 Exclusive Breast Milk Feeding and PC-06 Unexpected Complications in Term Newborns. eCQM versions are ePC-01/02/05/06. [V] — [Joint Commission perinatal measures](https://jointcommission.org/measurement/measures/perinatal-care)
- **PC-07 Severe Obstetric Complications.** eCQM-only (CMS1028). It counts delivery hospitalisations with severe complications, excluding transfusion-only cases. [V] — [eCQI CMS1028v4 (2026)](https://ecqi.healthit.gov/ecqm/hosp-inpt/2026/cms1028v4)
- **PC-08 Timely Treatment of Severe Hypertension.** Introduced June 2026; optional reporting for 2027. [V, Joint Commission wiki page, confirm in the official manual] — [TJC 2026 ORYX requirements](https://jointcommission-ddsp.atlassian.net/wiki/spaces/DCS/pages/1030619137)
- **Conflict on 2026 ORYX requirements.** One page specifies 3 required perinatal eCQMs plus 2 hospital-harm eCQMs. An older page lists PC-01/02/05/06 as required for hospitals with ≥300 births. [V] — [TJC ORYX 2026](https://jointcommission-ddsp.atlassian.net/wiki/spaces/DCS/pages/1030619137)
- **CDC SMM.** 21 indicators built from ICD-10 DX/PX codes on delivery hospitalisations, valid from October 2015 (the original ICD-9 list had 25). [V] — [CDC SMM ICD page](https://www.cdc.gov/maternal-infant-health/php/severe-maternal-morbidity/icd.html)
- **SMM and transfusion.** Transfusion is the only SMM indicator in about half of cases, so rates are reported with and without transfusion. Transfusion coding has about 65% sensitivity, and CMQCC tells coders to apply PCS transfusion codes to every maternity case. [V] — [PMC7523732](https://pmc.ncbi.nlm.nih.gov/articles/PMC7523732); [medRxiv 2023](https://medrxiv.org/content/10.1101/2023.10.17.23297168.full.pdf); [CMQCC Appendix EE](https://cmqcc.org/system/files/Appendix%20EE%20Alert%20on%20Blood%20Transfusion%20Procedure%20Coding.pdf)
- **ICD-10 transition.** Of 20 SMM indicators, 10 rose and 3 fell significantly across the 2015 transition, so code-set breaks matter for trending. [V] — [PMC5546569](https://pmc.ncbi.nlm.nih.gov/articles/PMC5546569/table/pone.0182343.t001)

**Behavioral health (HBIPS)**
- **HBIPS-2 and HBIPS-3.** Hours of physical restraint (HBIPS-2) and seclusion (HBIPS-3) per 1,000 inpatient hours, stratified into children 1–12, adolescents 13–17, adults 18–64 and older adults 65+. Measure steward: Joint Commission. [V] — [NQF 0640 worksheet](https://www.qualityforum.org/Projects/a-b/Behavioral_Health_and_Substance_Use/Measure_Worksheet_-_0640.aspx); [TJC manual MIF0117](https://manual.jointcommission.org/releases/TJC2024A/MIF0117.html)
- **Formula.** (numerator hours × 1000) / (inpatient days minus leave days, × 24). Worked example: 4.22 h over 337,584 h gives 0.01. [V] — [QRC HBIPS-2/3 FY2025](https://dev01.qualityreportingcenter.com/globalassets/2023/11/iqr/ipffy2025/fy2025/hbips--2-3.pdf)
- **Variation.** 67% of hospitals report seclusion ≤0.09 and restraint ≤0.15 hours per 1,000. About 10% report rates 5–10 times higher. No regulatory benchmark exists, and some hospitals put hours in the days field, a data-quality check worth automating. [V] — [Psychiatric Services 2020](https://ps.psychiatryonline.org/doi/10.1176/appi.ps.202000011)
- **Data.** Restraint and seclusion order start/stop times (ORD), flowsheet monitoring, ADT leave days.

### Inferences
**Practitioner inventory for areas not sourced this session [U].** Teams typically also track the following. Verify before encoding.
- **Sepsis.** Sepsis mortality observed/expected (O/E), POA vs hospital-onset sepsis, sepsis readmission within 30 days, time to antibiotics as a distribution (median and 90th percentile) rather than pass/fail, and best-practice-alert firing and response rates.
- **HF.** Follow-up visit scheduled or completed within 7 days. GWTG-HF historically had a "follow-up visit within 7 days" quality measure [U]. Also LOS and O/E LOS, 30-day all-cause readmission and ED revisit, IV-to-oral diuretic transition, and daily weights recorded.
- **Pneumonia and COPD.** Antibiotic choice concordant with IDSA/ATS guidance, blood cultures in ICU pneumonia, steroid duration in COPD, NIV use, and readmission/mortality O/E.
- **CABG.** The STS Adult Cardiac Surgery Database composite (absence of mortality, absence of major morbidity, IMA use, perioperative medications) and STS-PROM O/E. Not sourced this session [U].
- **Joint replacement.** AJRR registry, PROMs (HOOS-JR, KOOS-JR), 90-day complications, LOS, discharge to home vs SNF, tranexamic-acid use, and implant-level revision.
- **Maternity.** NTSV cesarean by provider, AIM hemorrhage and hypertension bundle compliance (quantitative blood loss, treatment within 60 min), and early elective delivery under 39 weeks without indication.
- **Pediatrics.** Pediatric early warning score (PEWS) escalations, Solutions for Patient Safety (SPS) harm measures, unplanned extubation, pediatric asthma care process, and peripheral IV infiltration.
- **Behavioral health.** Readmission to psychiatric unit within 30 days, suicide risk screening (Joint Commission NPSG 15.01.01), ED boarding hours for psychiatric patients, and post-discharge continuing care plan.

**Why teams track these.** Registry recognition (GWTG, NCDR) drives marketing and payer contracts. SEP-1 and PC measures are publicly reported. Timing distributions point to the operational bottleneck, such as ED triage, pharmacy verification or cath-lab activation.

### Gaps
- The AHA GWTG-HF recognition page returned 403, so the full current list of achievement and quality measures, including the 7-day follow-up measure, was not confirmed.
- No source retrieved for the STS CABG composite, AJRR measures, pneumonia/COPD internal measures or pediatric SPS measures.
- No current CMS SEP-1 specification manual version was retrieved. Bundle details reflect the 2015/2018 versions as described in the literature.

---

## Q2. Patient safety (falls, HAPI, HAIs, VTE, deterioration, failure to rescue, restraint, O/E mortality, complications, hospital harm eCQMs)

### Takeaway
Safety analytics combine three definitional families that are not interchangeable:
- **Code-based AHRQ PSIs** (POA-dependent, so CDI-sensitive).
- **Surveillance definitions.** NHSN HAIs reported as SIR against the 2015 baseline, and unit-level NDNQI rates.
- **EHR-derived hospital harm eCQMs** built from labs, MAR and flowsheets.

Teams also report vendor O/E indices (Vizient, Premier) that depend on proprietary risk models.

### Cited Findings
**AHRQ PSI v2025**
- **Release and PSI 02.** v2025 software shipped in August 2025 and retired PSI 02 (death in low-mortality DRGs) in favour of PSI 04 and failure-to-rescue measures. [V] — [AHRQ PSI v2025 ChangeLog](https://qualityindicators.ahrq.gov/Downloads/Modules/PSI/V2025/ChangeLog_PSI_v2025.pdf)
- **PSI 04.** "Death Rate among Surgical Inpatients with Serious Treatable Complications", stratified by DVT/PE, pneumonia, sepsis, shock/cardiac arrest and GI bleed. It is not part of PSI 90. [V for title and strata] — [AHRQ v2025 benchmark tables](https://qualityindicators.ahrq.gov/Downloads/Modules/PSI/V2025/Version_2025_Benchmark_Tables_PSI.pdf); [CASRAI](https://casrai.org/guides/ahrq-patient-safety-indicators-explained)
- **CMS replacement of PSI 04 [U].** A blog says CMS replaced PSI 04 with a 30-day risk-standardised Failure-to-Rescue measure in the FY2025 IPPS rule. Not confirmed against the Federal Register. — [Droubre Digest](https://droubredigest.beehiiv.com/p/failure-to-rescue)
- **PSI 90 components.** PSI 03 (pressure ulcer), 06 (iatrogenic pneumothorax), 08 (in-hospital fall with hip fracture) and 09 (perioperative haemorrhage/haematoma) are confirmed components. PSI 10–15 membership was not confirmed this session; background knowledge suggests 10, 11, 12, 13, 14 and 15 are included [U]. [V for 03/06/08/09] — [AHRQ PSI v2025 composite](https://qualityindicators.ahrq.gov/Downloads/Modules/PSI/V2025/PSI_Composite_Measures.pdf); [PSI 03 tech spec](https://qualityindicators.ahrq.gov/Downloads/Modules/PSI/V2025/TechSpecs/PSI_03_Pressure_Ulcer_Rate.pdf)
- **Data.** DX with POA, PX with dates, admission type, DISP, MS-DRG. Weights are in the Empirical Methods 2025 document. — [AHRQ Empirical Methods 2025](https://qualityindicators.ahrq.gov/Downloads/Resources/Publications/2025/Empirical_Methods_2025.pdf)

**Hospital harm eCQMs (CY2026)**
- **Self-selection pool.** Acute Kidney Injury (CMS832v3, risk-adjusted), Opioid-Related Adverse Events (CMS819v4), Pressure Injury (CMS826v3), Postoperative Respiratory Failure (CMS1218v2, risk-adjusted) and Falls with Injury (CMS1017v2). [V] — [QRC CY2026 available eCQMs](https://qualityreportingcenter.com/globalassets/2026/05/iqr/ecqm_cy-2026-available-ecqms_jan2026_vfinal_508.pdf)
- **Required for CY2026.** Severe Hypoglycemia and Severe Hyperglycemia, plus Safe Use of Opioids, Cesarean Birth and Severe Obstetric Complications. [V] — [QRC CY2026](https://qualityreportingcenter.com/globalassets/2026/05/iqr/ecqm_cy-2026-available-ecqms_jan2026_vfinal_508.pdf)
- **Future mandatory dates (vendor, unconfirmed).** ORAE in 2027; AKI and Pressure Injury in 2028. — [Medisolv June 2026 deck](https://go.medisolv.com/hubfs/2026%20Webinars/The%20Next%20Era%20of%20Patient%20Safety%20-%20Evolving%20Hospital%20Harm%20eCQMs%206.30.26/nexterapatientsafetymeasuresmedisolv1782830682911.pdf?hsLang=en)
- **Data.** These are the clearest EHR-derived safety signals and are also used internally on monthly dashboards:
  - Hypoglycemia: glucose results with times (LAB-T) plus insulin and hypoglycemic MAR.
  - ORAE: naloxone MAR after opioid MAR.
  - AKI: serial creatinine (LAB-T).
  - Pressure injury: stage 2+ or deep tissue injury documented after 72 h (flowsheet/DX).
  - Falls with injury: event plus injury DX or imaging.
  - Postoperative respiratory failure: ventilation or reintubation timing (PX/flowsheet).

**HAIs (NHSN)**
- **SIR.** Observed HAIs divided by predicted HAIs from the national 2015 baseline, risk-adjusted. A value above 1.0 means more infections than predicted. SIRs exist for CLABSI, MBI-LCBI, CAUTI, SSI, CDI (LabID), MRSA bacteremia (LabID) and VAE. LabID events use negative binomial models. [V] — [NHSN SIR guide](https://webarchive.library.unt.edu/web/20201221064851/https://www.cdc.gov/nhsn/pdfs/ps-analysis-resources/nhsn-sir-guide.pdf)
- **National trend.** 2024 vs 2023: CLABSI down 9%, CAUTI down 10%, hospital-onset MRSA bacteremia down 7%. [V] — [CDC HAI Progress Report](https://www.cdc.gov/healthcare-associated-infections/php/data/progress-report.html?)
- **Data.** Positive cultures with collection date and specimen (LAB-T), device days (central line and urinary catheter days from flowsheets or LDA), patient days by unit (ADT), and admission date for hospital-onset attribution (day 4 or later) [U for the day-4 rule].

**Falls, HAPI, restraint (NDNQI)**
- **Falls with injury.** Tracked separately from the total fall rate to link nursing assessment and intervention to harm. [V, secondary] — [NUMC NDNQI summary](https://numc.edu/?p=8020)
- **HAPI.** Stage 2+, unstageable and deep tissue injury categories. [V, secondary] — [open-exam-prep NDNQI guide](https://open-exam-prep.com/study-guides/ancc-ne-bc/quality-safety-part1/nursing-sensitive-quality-indicators-ndnqi)
- **Restraint prevalence.** Uses the CMS restraint definition (42 CFR 482.13). [V, secondary] — [CASRAI](https://www.casrai.org/guides/nurse-sensitive-indicators)
- **NDNQI vs CMS.** Unit-level NDNQI rates are not directly comparable to the CMS hospital-level Falls with Injury eCQM. [V] — [CASRAI](https://www.casrai.org/guides/nurse-sensitive-indicators)
- **Post-pandemic trend.** Fall and HAPI rates rose during COVID-19 and stayed elevated, and they vary more by unit type than CAUTI or CLABSI do. [V] — [OR Manager brief, Nov 2024](https://ormanager.com/briefs/study-links-pandemic-stress-to-persistent-increases-in-nursing-sensitive-quality-indicators)

**Mortality index (O/E)**
- **Vizient mortality index.** Observed deaths divided by expected deaths from Vizient risk models; one analysis used the 2023 AMC risk model. The aggregate index fell from 0.97 (2019) to 0.65 (2025). Another AHA report gives a 2019 baseline of 1.0, so model version and cohort must be pinned before trending. [V] — [AHA Aug 2025 analysis](https://www.aha.org/system/files/media/file/2025/08/New-Analysis-Connects-Better-Outcomes-for-Hospitalized-Surgical-Patients-to-Improvements-in-Key-Safety-Indicators.pdf); [AHA Sept 2024](https://www.aha.org/system/files/media/file/2024/09/New-Analysis-Shows-Hospitals-Performance-on-Key-Patient-Safety-Measures-Surpassing-Pre-pandemic-Levels.pdf); [Vizient essential hospitals](https://www.vizientinc.com/insights/reports/data-on-the-edge/advancing-outcomes-and-quality-of-care-data-insights-from-essential-hospitals)
- **Premier.** Premier QualityAdvisor publishes AHRQ PSI FAQs and uses the modified PSI 90. [V] — [Premier AHRQ PSI FAQ July 2025](https://qualityadvisor.premierinc.com/QualityAdvisorInformation/help/WebHelp/FAQs/ahrq_psi_faqs_july2025.pdf)

**Adverse event detection (IHI Global Trigger Tool)**
- **Rates.** Adverse events per 1,000 patient days, per 100 admissions, and percent of admissions with an adverse event. [V] — [IHI GTT](https://www.ihi.org/resources/pages/tools/ihiglobaltriggertoolformeasuringaes.aspx)
- **Medication triggers.** Naloxone (M9), vitamin K for high INR, and diphenhydramine. One German version uses an INR threshold above 4. [V] — [IHI ADE trigger tool](https://stg.ihi.org/sites/default/files/2023-09/IHITriggerToolforMeasuringAdverseDrugEvents.pdf); [PMC8713708](https://pmc.ncbi.nlm.nih.gov/articles/PMC8713708/)
- **Detection yield.** Triggers find about 10 times more ADEs than voluntary reporting. One site's median was 6 ADEs per 1,000 patient days, and patients with an ADE had LOS of 22.3 vs 8.0 days. Single-site result, not a benchmark. [V] — [PMC8713708](https://pmc.ncbi.nlm.nih.gov/articles/PMC8713708/)

### Inferences
**Common internal safety questions [U].**
- **VTE.** Prophylaxis ordered or administered within 24 h of admission or surgery, matched to risk score; hospital-acquired VTE (non-POA DX plus imaging).
- **Deterioration.** Rapid response calls per 1,000 patient days, codes outside the ICU per 1,000 patient days, unplanned ICU transfers within 24–48 h of admission, early warning score (MEWS/NEWS/eCART/Epic Deterioration Index) alert-to-action time, and ICU readmission within 48 h.
- **Failure to rescue.** Death after a complication, computed from VIT-T plus DX.
- **Code-based vs EHR signals.** PSIs measure coding as much as care. Mature teams reconcile PSI flags against EHR harm signals before acting, which is a natural fit for a "versioned evidence" design.
- **Why teams track these.** Leapfrog Safety Grade, US News, Vizient Q&A rankings, CMS HAC Reduction Program penalties, and internal zero-harm programmes.

### Gaps
- PSI 90 v2025 component list beyond 03/06/08/09 is not confirmed.
- No primary NDNQI specification document was retrieved; NDNQI is a subscription programme.
- Vizient and Premier risk-model covariates are proprietary and not public.
- The CMS Failure-to-Rescue measure's status is unconfirmed.

---

## Q3. Nursing-sensitive, antimicrobial stewardship, medication safety, lab/imaging utilisation, end-of-life

### Takeaway
Stewardship has the most standardised internal metric: NHSN AU DOT per 1,000 days present and the SAAR. Medication safety relies on trigger-based surveillance using MAR and labs. Utilisation and end-of-life metrics are mostly locally defined, with literature support but no single steward.

### Cited Findings
**Antimicrobial stewardship (NHSN AU)**
- **DOT per 1,000 days present.** Each agent counts once per calendar day it is given (any dose). Days present counts patients on the unit at any point in a day. Data: MAR with route and day, ADT. [V] — [CDC AUR protocol (2019 archive)](https://webharvest.gov/congress115th/20190112170020/https://www.cdc.gov/nhsn/PDFs/pscManual/11pscAURcurrent.pdf); [CASRAI SAAR guide](https://www.casrai.org/guides/standardized-antimicrobial-administration-ratio-saar)
- **SAAR.** Observed over predicted antimicrobial days. 22 agent categories across adult, pediatric and neonatal locations, up to 47 SAARs per facility. Raw rates should not be compared across facilities. [V] — [CDC SAAR training 2020](https://www.cdc.gov:443/nhsn/pdfs/training/2020/saar-508.pdf); [CASRAI](https://www.casrai.org/guides/standardized-antimicrobial-administration-ratio-saar)
- **Tennessee example.** Statewide AU rose from 593 to 621 DOT per 1,000 days present between 2017 and 2023. [V] — [Cambridge ICHE 2025](https://www.cambridge.org/core/product/47DB5B6CECCE0A57EECEBB6DA2CA8886/core-reader)
- **AR Option and use-to-resistance.** Hospitals benchmark use against resistance with NHSN metrics. [V] — [Cambridge ICHE comparative study](https://www-cambridge-org.ezproxyberklee.flo.org/core/services/aop-cambridge-core/content/view/19143CDE07BF92F35820AB10F39144EC/S0899823X24002101a.pdf/benchmarking-antimicrobial-use-to-antimicrobial-resistance-a-comparative-study-of-two-hospitals-using-current-national-healthcare-safety-network-nhsn-metrics.pdf)

**Medication safety**
- **IHI ADE triggers.** Naloxone, vitamin K or high INR, and diphenhydramine; rates per 1,000 patient days (see Q2). [V] — [IHI ADE trigger tool](https://stg.ihi.org/sites/default/files/2023-09/IHITriggerToolforMeasuringAdverseDrugEvents.pdf)
- **CMS harm eCQMs.** ORAE, severe hypoglycemia and hyperglycemia, plus Safe Use of Opioids (concurrent prescribing) (see Q2). [V] — [QRC CY2026](https://qualityreportingcenter.com/globalassets/2026/05/iqr/ecqm_cy-2026-available-ecqms_jan2026_vfinal_508.pdf)

**End-of-life and palliative care**
- **Palliative Care Quality Network (PCQN).** National collaborative with prospective data on 135,197 patients; one trend study covered 88 hospitals. [V] — [PMC6902777](https://pmc.ncbi.nlm.nih.gov/articles/PMC6902777); [Pantilat, NRC](https://nrchealth.com/wp-content/uploads/2017/08/BRP_2014_08_V25N4_Palliative-Care-Quality-Network_Pantilat.pdf)
- **Metric framework.** Hospital palliative care metrics fall into outcome, process and transition measures. [V] — [PMC5017551](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5017551/)
- **Specialty consult associations.** Consultation is associated with lower in-hospital death (IRR 0.57) and more hospice referral (IRR 1.45). Each day of consult delay in the ICU raised the likelihood of in-hospital death by 8%. In one ICU, 23% of decedents had a palliative referral. These are observational results. [V] — [PMC9303815](https://pmc.ncbi.nlm.nih.gov/articles/PMC9303815); [PMC7364302](https://pmc.ncbi.nlm.nih.gov/articles/PMC7364302)
- **Early palliative care.** More than 90 days before death: 6% vs 56% had ICU use in the last month, and median hospice stay was 138 vs 8 days. [V] — [PMC10092792](https://pmc.ncbi.nlm.nih.gov/articles/PMC10092792)
- **Data.** Consult order and note times, code-status orders, DISP (hospice, expired), ICU ADT in the final admission, goals-of-care notes (NOTE).

### Inferences
**Nursing-sensitive indicators [U, background knowledge].** NDNQI also covers:
- Nursing hours per patient day and skill mix
- RN education and certification, RN turnover
- Peripheral IV infiltration (pediatric)
- Pain assessment and reassessment cycle
- Pediatric pressure injury
- RN survey practice environment scales

**Medication safety [U].**
- High-alert drug events: anticoagulant bleeding with heparin anti-Xa or aPTT out of range, INR >5 on warfarin; insulin hypoglycemia per 100 patient days on insulin.
- Opioid metrics: morphine milligram equivalents (MME) per patient day, naloxone rescue per 1,000 opioid patient days, opioid prescribing at discharge in MME, co-prescribing with benzodiazepines.
- Barcode medication administration (BCMA) scan compliance and override rates; ADC (automated dispensing cabinet) overrides.
- Anticoagulation-stewardship measures (DOAC dosing vs renal function).

**Stewardship [U].** IV-to-PO conversion, antibiotic time-outs at 48–72 h, days of therapy (DOT) per indication, and duration for community-acquired pneumonia and UTI.

**Lab and imaging utilisation [U].**
- Daily repeat labs on stable patients (Choosing Wisely, "Society of Hospital Medicine" recommendations): CBC/BMP per patient day.
- Duplicate test rate within 24–72 h.
- Phlebotomy blood volume, with transfusion threshold compliance (Hb <7).
- Imaging appropriateness: CT-PE yield, AUC via clinical decision support (the Medicare imaging AUC programme was paused or rescinded [U]).
- Telemetry utilisation outside indications.
- Blood culture contamination rate (target <3% [U]).

### Gaps
- The current (2025/2026) NHSN AUR protocol text was not retrieved; the definitions cited come from a 2019 archive and 2020 training.
- No authoritative source retrieved for lab and imaging utilisation benchmarks or high-alert medication event definitions.
- PCQN metric definitions themselves were not retrieved.

---

## Q4. Clinical documentation integrity (CMI, SOI/ROM, query rates, HCC/APR-DRG capture)

### Takeaway
CDI programmes track throughput KPIs (review rate, query rate, response rate, agreement rate) and impact KPIs (CMI, APR-DRG SOI/ROM capture, which move expected mortality and therefore O/E indices). Teams increasingly also track clinical-validation retention and risk-adjustment accuracy. Denominators are not standardised, so cross-organisation benchmarks are weak.

### Cited Findings
- **Core KPIs.** CMI, review rate, query rate, response rate and agreement rate. Benchmarks should be revisited each year. [V] — [AHIMA CDI KPI practice brief](https://journal.ahima.org/AMP_EDN/383/Clinical-Documentation-Integrity-Key-Performance-Indicators-Practice-Brief-2492.amp.html)
- **Agreement-rate survey.** Among organisations that track agreement, 48.76% report 91–100%, 39.75% report 81–90% and 6.36% report 71–80%. [V] — [ACDIS query metrics report](https://acdis.org/sites/acdis/files/cdi-week/JF_Publication_Industry-Report-CDI-Week_QueryMetrics_final.pdf)
- **Query-rate reference points.** A former 15% standard and a 35% DRG-impact query rate have both been cited; the basis is unclear. [V that these figures are cited; basis unverified] — [ACDIS query metrics](https://acdis.org/sites/acdis/files/cdi-week/JF_Publication_Industry-Report-CDI-Week_QueryMetrics_final.pdf); [ICD10monitor, The Query Conundrum](https://icd10monitor.medlearn.com/the-query-conundrum)
- **Denominator choice.** Query rates fall when total admissions is the denominator and are more stable per CDI review. Agreement definitions vary, especially for clinical-validation queries. [V] — [ICD10monitor](https://icd10monitor.medlearn.com/the-query-conundrum)
- **Newer focus.** Clinical-validation retention (diagnoses retained vs removed) and risk-adjustment accuracy, not only DRG movement. [V] — [RACmonitor, Evolving CDI metrics 2025](https://racmonitor.medlearn.com/?p=153177)
- **PSI link.** Rising PSI 90 rates need facility CDI and coding attention, because POA coding drives PSI numerators. [V] — [ICD10monitor PSI-90](https://icd10monitor.medlearn.com/increasing-ahrq-psi-90-rates-requires-facility-attention)
- **Data.** Query log (CDI worklist), DX/PX before and after query, MS-DRG and APR-DRG with SOI/ROM (3M grouper), payer, denial outcomes.

### Inferences
**Common CDI analytics [U].**
- CMI by service line and physician, Medicare vs all-payer.
- Complication or comorbidity (CC) and major complication or comorbidity (MCC) capture rate.
- Expected mortality and expected LOS movement attributable to documentation.
- PSI and HAC flag review ("PSI validation") before submission.
- HCC capture on inpatient stays for MA/ACO risk.
- Denial and overturn rate for clinical-validation denials (sepsis, malnutrition, encephalopathy, acute respiratory failure).
- Second-level DRG validation.

**Use in internal analysis [Inference].** Because Vizient/Premier O/E indices depend on coded SOI/ROM, internal analysts routinely split "mortality improvement" into a documentation component and a care component. The product should keep coded-severity evidence versioned separately from clinical evidence.

### Gaps
- No public numeric benchmarks were found for CMI, SOI/ROM distributions or expected-mortality capture.
- APR-DRG methodology (3M) is proprietary, and no public documentation was retrieved this session.
