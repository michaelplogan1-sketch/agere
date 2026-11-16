# VBC question registry

All examples are synthetic development specifications. Status is explicit; question breadth does not imply implemented clinical capability.

## Kidney disease: progression, referral and missed opportunity

Required data: Longitudinal creatinine/eGFR with units and collection times; urine albumin measurements; medications and actual use; acute illness episodes; nephrology referrals and outside visits; notes and patient preferences

Do not infer chronic progression from one acute measurement. Missing albuminuria is unknown, not normal. Use validated risk equations only in their intended populations with required inputs.

- **CKD-01 — not_implemented**: Which patients have evidence supporting nephrology referral, and which have already received care outside our system?
- **CKD-02 — not_implemented**: Whose kidney function is deteriorating, and does the pattern suggest chronic progression, acute injury, or a change requiring reassessment?
- **CKD-03 — not_implemented**: Who cannot be risk-stratified because albuminuria or another essential input is missing?
- **CKD-04 — not_implemented**: Which kidney-protective treatment opportunities remain after accounting for contraindications, intolerance, affordability and patient preference?
- **CKD-05 — not_implemented**: Which patients have repeated kidney injury after medication changes, illness or transitions between settings?
- **CKD-06 — not_implemented**: Which patients with advanced disease lack documented discussions of prognosis, treatment choices and what matters to them?
- **CKD-07 — not_implemented**: Which apparent kidney declines disappear after reconciling units, corrected results and duplicate labs?
- **CKD-08 — not_implemented**: Where are recurrent potassium problems interrupting otherwise appropriate treatment?
- **CKD-09 — not_implemented**: Which patients have an uncompleted kidney evaluation despite repeated abnormal observations?
- **CKD-10 — not_implemented**: Which groups have missing outside renal care documentation that could distort referral demand?

## Heart failure: appropriate treatment and preventable deterioration

Required data: Dated imaging and ejection fraction; medication orders fills and reconciliation; dose history; blood pressure heart rate kidney function potassium; symptoms and weights; admissions; affordability and patient goals

Prescribed, dispensed and taken are different states. Below target dose does not establish below maximum tolerated dose. Classify HF phenotype and preserve legitimate holds.

- **HF-01 — partial**: Which confirmed HFrEF patients have potentially missing guideline-directed therapy after all exceptions are reconciled?
- **HF-02 — not_implemented**: Who is below an appropriate target dose without evidence establishing that the current dose is maximally tolerated?
- **HF-03 — not_implemented**: Which patients are deteriorating before the next admission becomes apparent in claims?
- **HF-04 — partial**: Where does HF treatment stall: unaffordable medication, unavailable labs, follow-up delay, clinical intolerance or missing information?
- **HF-05 — not_implemented**: Which apparent HFrEF gaps come from an old ejection fraction, recovered function or conflicting diagnoses?
- **HF-06 — not_implemented**: Which discharge patterns precede recurrent HF admissions, and which are actionable while the patient is still stable?
- **HF-07 — partial**: Which missing medication fills are explained by samples or an outside pharmacy?
- **HF-08 — not_implemented**: Which patients have a documented treatment barrier that remains unresolved at subsequent visits?
- **HF-09 — not_implemented**: Where do copied medication lists conflict with actual dispensing or documented discontinuation?
- **HF-10 — not_implemented**: Which patients have changes in ejection fraction that require reassessing their treatment-gap classification?

## Sepsis: antecedents, treatment reliability and recovery

Required data: Infection episodes and microbiology; clinical observations and their availability times; device use; antimicrobial treatment; admission records; discharge summaries; post-illness function and symptoms

Sepsis codes are not a complete clinical phenotype. Separate prevention, acute recognition and recovery tasks. A retrospective association does not validate an early warning tool.

- **SEP-01 — not_implemented**: Which recurrent infections or unresolved infection sources precede sepsis admissions?
- **SEP-02 — not_implemented**: Which patients have infection risk related to devices or wounds that deserves clinical reassessment?
- **SEP-03 — not_implemented**: Where do failures in recognition, escalation or treatment recur across sepsis episodes?
- **SEP-04 — not_implemented**: Which sepsis survivors have unresolved medication issues, symptoms, functional loss or cognitive concerns?
- **SEP-05 — not_implemented**: Which sepsis readmissions reflect recurrent infection versus organ dysfunction, treatment adverse effects or another cause?
- **SEP-06 — not_implemented**: Which proposed sepsis interventions improve outcomes locally, and for whom do they add burden without benefit?
- **SEP-07 — not_implemented**: Which sepsis survivors encounter a gap between discharge antibiotic plans and actual access?
- **SEP-08 — not_implemented**: Where do pending culture results fail to receive documented follow-up?
- **SEP-09 — not_implemented**: Which recurrent infections are associated with fragmented care across facilities?
- **SEP-10 — not_implemented**: Which apparent improvements in sepsis performance disappear after accounting for changes in coding and severity?

## Financial burden, access and assistance

Required data: Patient-reported barriers; benefits and coverage dates; pharmacy transactions; authorizations and denials; household information provided for eligibility; program rules and service availability

Neighborhood deprivation cannot establish an individual financial need or program eligibility. Distinguish screening candidates, verified eligibility, enrollment and receipt of benefit.

- **BEN-01 — not_implemented**: Who reports rationing medication, skipping visits or sacrificing basic needs because of healthcare costs?
- **BEN-02 — not_implemented**: Which patients may qualify for medication assistance, hospital charity care, transportation or other named programs?
- **BEN-03 — not_implemented**: Which patients face coverage loss or a formulary change that could interrupt effective treatment?
- **BEN-04 — partial**: Which treatment interruptions are associated with authorization delays, claim reversals or unaffordable copays?
- **BEN-05 — not_implemented**: Which eligible patients never receive the benefit because applications stall or services lack capacity?
- **BEN-06 — not_implemented**: Which patients are overwhelmed by overlapping programs, appointments and monitoring requests?
- **BEN-07 — not_implemented**: Which assistance interventions address a documented financial barrier rather than a neighborhood risk estimate?
- **BEN-08 — not_implemented**: Where do repeated authorizations consume effort without restoring access to treatment?
- **BEN-09 — not_implemented**: Which medication substitutions resolve affordability without creating a new documented clinical problem?
- **BEN-10 — not_implemented**: Which patients lose assistance eligibility mid-treatment and need an alternative plan?

## Serious illness, quality of life and treatment burden

Required data: Symptoms and function; patient-reported outcomes; serious illness trajectory; care preferences and decision-maker records; treatments and burdens; caregiver reports

Never autonomously label a person or treatment futile. Cost, disability, age or predicted mortality alone cannot decide benefit. Palliative support and hospice eligibility are separate questions.

- **PAL-01 — not_implemented**: Who has serious illness with uncontrolled symptoms, caregiver strain or difficult decisions that could benefit from palliative care?
- **PAL-02 — not_implemented**: Whose treatment burden appears to exceed the benefit they report, or conflict with their stated goals?
- **PAL-03 — not_implemented**: Where are documented preferences repeatedly lost during transfers, emergencies or new treatment decisions?
- **PAL-04 — not_implemented**: Which patients and caregivers may benefit from additional support at home?
- **PAL-05 — not_implemented**: Which intensive interventions should receive multidisciplinary review for uncertain benefit or possible goal discordance?
- **PAL-06 — not_implemented**: Where are patient-reported symptoms missing despite repeated serious-illness encounters?
- **PAL-07 — not_implemented**: Which patients repeatedly receive care inconsistent with a documented preference, requiring review?
- **PAL-08 — not_implemented**: Which caregivers report burdens that are absent from the patient's structured record?
- **PAL-09 — not_implemented**: Which changes in function or symptoms should prompt a goals-of-care reassessment?

## Admissions and readmissions: reconstructing failure sequences

Required data: Longitudinal clinical record; claims and encounter feeds; appointments and referrals; messages and telephone encounters; pharmacy activity; patient-reported causes; deaths and outside admissions

Distinguish event description, predicted risk, causal hypothesis and established intervention effect. Use both event time and time evidence became available.

- **ADM-01 — not_implemented**: For each admission, what sequence of clinical changes and access failures might have contributed?
- **ADM-02 — not_implemented**: Where was the last realistic opportunity to intervene using only information available then?
- **ADM-03 — not_implemented**: Which weekend or holiday transitions are associated with unavailable medication, equipment or follow-up?
- **ADM-04 — not_implemented**: Which apparent readmissions are transfers, planned returns, coding artifacts or events outside the contract definition?
- **ADM-05 — not_implemented**: Which patients repeatedly seek acute care because outpatient alternatives are unavailable or not usable?
- **ADM-06 — not_implemented**: Are falling readmissions accompanied by better survival, function and time at home, or by missing data and competing events?
- **ADM-07 — not_implemented**: Which admissions follow a documented medication access barrier that persisted across visits?
- **ADM-08 — not_implemented**: Which patients appear stable only because outside admissions have not reached our feeds?
- **ADM-09 — not_implemented**: Which recurring admission sequences suggest a testable service redesign rather than another patient alert?
- **ADM-10 — not_implemented**: Where does an intervention shift utilization to emergency or observation care without improving outcomes?

## Post-acute matching and learning

Required data: Linked hospital and post-acute episodes; baseline function and cognition; diagnosis and severity; CMS facility measures; staffing and clinical capability; available beds coverage distance and patient choice

DRG and star rating alone are insufficient. Use case-mix adjustment, sample-size uncertainty and comparable follow-up; preserve choice and verify current capability.

- **SNF-01 — not_implemented**: Which SNFs have the best supported outcomes for patients with this clinical and functional profile?
- **SNF-02 — not_implemented**: Which facilities deliver better functional recovery, return home and safety after similar joint-replacement episodes?
- **SNF-03 — not_implemented**: Which SNFs appear particularly capable for HF, complex wounds or other needs relevant to this patient?
- **SNF-04 — not_implemented**: Which hospital-SNF transitions repeatedly lose medication changes, pending results or care preferences?
- **SNF-05 — not_implemented**: Which facilities improve after a specific practice change, and could that practice transfer elsewhere?
- **SNF-06 — not_implemented**: Which facility comparisons change materially when patient severity and functional status are included?
- **SNF-07 — not_implemented**: Where are SNF rankings too uncertain or based on too few comparable episodes to guide placement?
- **SNF-08 — not_implemented**: Which facilities meet a patient's clinical needs but lack timely capacity or acceptable travel access?
- **SNF-09 — not_implemented**: Which post-acute complications are already developing before transfer, making attribution uncertain?

## Physician and practice learning

Required data: Longitudinal attributed panels; shared care roles; condition-specific process and outcomes; baseline risk; social and access context; staffing and capacity; patient experience

Compare like populations, report uncertainty and account for shared care and selection. Adjustment must not hide disparities. Use results to learn before using them for consequential rankings.

- **PCP-01 — not_implemented**: Which practices achieve better disease control and patient-reported outcomes among comparable patients?
- **PCP-02 — not_implemented**: What do better-performing practices do differently that other locations could realistically adopt?
- **PCP-03 — not_implemented**: Where do referral completion, medication optimization or diagnostic follow-up reliably break down?
- **PCP-04 — not_implemented**: Which practices improve average outcomes while leaving particular groups behind?
- **PCP-05 — not_implemented**: Would shifting capacity, adding pharmacy support or changing appointment access likely help more than another alert?
- **PCP-06 — not_implemented**: Which apparent provider gaps are driven by a missing source feed rather than missing care?
- **PCP-07 — not_implemented**: Which practices reliably resolve affordability and authorization barriers after they are documented?
- **PCP-08 — not_implemented**: Where does measured performance change when patients transfer between attributed clinicians?
- **PCP-09 — not_implemented**: Which practice differences persist within comparable disease severity and access groups?

## Out-of-network care and actual access

Required data: Complete claims and encounter feeds; dated network directories; referral attempts; appointment availability; service capability; geography; patient-reported preferences and reasons

Outside care may be clinically necessary or preferred. Directory presence is not available access; incomplete feeds can mimic leakage. Do not restrict emergency care or patient choice.

- **NET-01 — not_implemented**: Who uses out-of-network services most often, for which needs, and what reasons are documented?
- **NET-02 — not_implemented**: Which outside referrals follow failed in-network access attempts or missing clinical capability?
- **NET-03 — not_implemented**: Which apparent leakage is caused by stale directories, attribution errors or delayed claims?
- **NET-04 — not_implemented**: Where could a clinically appropriate local service reduce travel and patient burden?
- **NET-05 — not_implemented**: Which external providers deliver good outcomes and should become deliberate partners?
- **NET-06 — not_implemented**: Which outside encounters can close an apparent care gap once reconciled with local records?
- **NET-07 — not_implemented**: Where do network directories claim access that appointment availability does not support?
- **NET-08 — not_implemented**: Which recurring outside services fill a clinical capability gap in the contracted network?
- **NET-09 — not_implemented**: Which out-of-network expenditures are likely attribution or duplicate-claim artifacts?

## Documentation and risk-adjustment integrity

Required data: Current clinical evidence; diagnosis history; encounter eligibility; coding rules by program and year; submitted codes and audit findings

Find supported omissions and unsupported diagnoses. A model suggestion is not a diagnosis or a billable code. Coding improvement is not a clinical outcome.

- **DOC-01 — not_implemented**: Which clinically supported conditions may be missing from the current record or required documentation?
- **DOC-02 — not_implemented**: Which submitted diagnoses appear stale, unsupported or inconsistent with the available chart?
- **DOC-03 — partial**: Which locations have systematic documentation problems, and are they clinical, technical or workflow failures?
- **DOC-04 — not_implemented**: How much of a reported financial improvement comes from coding changes versus better clinical outcomes?
- **DOC-05 — not_implemented**: Which copied diagnoses lack current corroboration and should be reviewed before reuse?
- **DOC-06 — not_implemented**: Which documentation requests would add information needed for care rather than merely increase coding volume?
- **DOC-07 — not_implemented**: Where does missing clinical documentation prevent distinguishing appropriate holds from untreated disease?
- **DOC-08 — not_implemented**: Which records contain contradictory condition status across notes, claims and problem lists?

## Contracts, locations and financial opportunity

Required data: Signed contracts and amendments; measure specifications; attribution and enrollment; claims maturity; quality gates; benchmarks risk adjustment caps and corridors; intervention costs; staffing and capacity

Financial estimates must cite applicable clauses and assumptions. Patient benefit and financial return are separate outputs. Deduplicate overlapping patients and savings; do not optimize denial or avoidance of needed care.

- **FIN-01 — not_implemented**: What exactly does each contract reward or penalize, and which rules apply to this population and period?
- **FIN-02 — not_implemented**: Which locations have the largest evidence-supported opportunities to improve both patient outcomes and contract performance?
- **FIN-03 — not_implemented**: Which quality threshold or contract provision most constrains shared savings or downside exposure?
- **FIN-04 — not_implemented**: What is the plausible net financial effect of an intervention after delivery cost, uptake and payment mechanics?
- **FIN-05 — not_implemented**: Which initiatives target the same people or events, causing double-counted savings or excessive treatment burden?
- **FIN-06 — not_implemented**: Where does better patient care conflict with a local reimbursement incentive, and can contracting resolve it?
- **FIN-07 — not_implemented**: Which apparent savings disappear after claims maturity and runout are accounted for?
- **FIN-08 — not_implemented**: Which contract opportunities remain favorable under conservative uptake and delivery-cost assumptions?
- **FIN-09 — not_implemented**: Which patients would receive conflicting interventions from overlapping contract initiatives?
- **FIN-10 — not_implemented**: Where does a financially attractive proposal lack evidence of patient benefit?

## Any payer program or measure: understand, update and apply

Required data: Authoritative CMS state Medicaid and commercial-plan specifications; contracts amendments and bulletins; measure value sets and code versions; effective and publication dates; plan product geography and provider applicability; member coverage and attribution

No hardcoded dependency on a named program. Resolve authority, applicability and effective time before execution. Distinguish eligibility from suitability and enrollment. Proposed or ambiguous rules cannot silently become active clinical or financial logic.

- **POL-01 — not_implemented**: Given a newly published program or measure, what does it require and to whom does it apply?
- **POL-02 — not_implemented**: What changed when CMS, a state Medicaid program or a commercial plan updated its requirements?
- **POL-03 — not_implemented**: Which patients, clinicians, locations and contracts are affected by a new or amended rule?
- **POL-04 — not_implemented**: Which programs or measures overlap, conflict or leave needs uncovered for this population?
- **POL-05 — not_implemented**: Can we answer the new requirement with available data, and what evidence is missing?
- **POL-06 — not_implemented**: Which rule updates change only documentation requirements versus actual patient eligibility?
- **POL-07 — not_implemented**: Which measure versions are simultaneously active across our contracts?
- **POL-08 — not_implemented**: Which policy clauses cannot be translated into executable logic without clarification?
- **POL-09 — not_implemented**: Which published corrections would change prior cohort assignments and require reprocessing?

## Discovery across clinical and public data

Required data: Longitudinal patient data; locally validated public datasets; weather and air-quality observations; transit schedules; provider directories; pharmacy service information; geography and timestamps

Discovery outputs are hypotheses until confirmed. Public area-level data are context, not individual traits. Use authorized APIs/downloads first, permitted scraping second; retain source dates, terms and provenance.

- **DISC-01 — not_implemented**: Are heat or air-quality events associated with deterioration in particular local patient groups?
- **DISC-02 — not_implemented**: Do transit schedules make certain appointments or lab visits practically inaccessible?
- **DISC-03 — not_implemented**: Do pharmacy closures, changed hours or dispensing disruptions precede interruptions in treatment?
- **DISC-04 — not_implemented**: Which patients are quietly deteriorating despite low utilization and little recorded spending?
- **DISC-05 — partial**: Which surprising associations persist after checking duplicate notes, missing feeds, case mix and multiple testing?
- **DISC-06 — not_implemented**: What additional piece of information would most change a consequential decision?
- **DISC-07 — not_implemented**: Which apparent population patterns vanish when copied sentences count as one observation?
- **DISC-08 — not_implemented**: Which newly emerging clusters coincide with changes in data completeness rather than patient health?
- **DISC-09 — not_implemented**: Which candidate interventions would be most informative to test prospectively?
- **DISC-10 — not_implemented**: Which insights persist across practices and time periods after correcting for repeated testing?

## Learning which interventions actually help

Required data: Adjudicated baseline decisions; intervention assignment and receipt; patient preferences; resource use; longitudinal outcomes and harms; comparison populations; dropout and missingness

Highest risk does not imply greatest preventable risk or greatest treatment benefit. Treatment-effect estimates require identification assumptions and external validation. Equity and clinical need constrain allocation.

- **LEARN-01 — not_implemented**: Which patients benefit from which intervention, rather than merely having the highest predicted risk?
- **LEARN-02 — not_implemented**: With limited capacity, which allocation offers the most supported benefit while maintaining equitable access?
- **LEARN-03 — not_implemented**: Which recommended interventions are accepted, delivered and sustained, and why do others fail?
- **LEARN-04 — not_implemented**: Which programs should be expanded, modified or stopped because of measured benefit, burden or harm?
- **LEARN-05 — not_implemented**: Can the system recognize when it does not know enough to make a reliable recommendation?
- **LEARN-06 — not_implemented**: Where do specialist and frontier models disagree, and which disagreement types predict errors?
- **LEARN-07 — not_implemented**: Which evidence questions can a smaller model answer at acceptable accuracy and coverage?
- **LEARN-08 — not_implemented**: Which cached findings need revision after new clinical evidence or a rule update?
- **LEARN-09 — not_implemented**: Which human corrections generalize to new source-patient families rather than memorized templates?

## Data and evidence integrity

Required data: Source feeds and provenance; Arrival timestamps and identity crosswalks for historical/identity questions

Missing observations never establish missing care.

- **DATA-01 — implemented**: How many records and patients are supplied by each feed?
- **DATA-02 — implemented**: How much chart text repeats after sentence normalization?
- **DATA-03 — implemented**: How many supplied records occur after the analysis cutoff?
- **DATA-04 — implemented**: Which lab tests are observed, and in how many patients?
- **DATA-05 — implemented**: Which returned note quotes cannot be located exactly in the supplied source?
- **DATA-06 — implemented**: Which judgments were sent to review by the reader?
- **DATA-07 — not_implemented**: Which source feeds arrived too late to have been available at the historical decision time?
- **DATA-08 — not_implemented**: Which patient identities cannot be reconciled across source systems?
