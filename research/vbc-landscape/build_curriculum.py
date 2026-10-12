"""Original research-derived prompts and synthetic acceptance-case specifications.

These are proposed tasks, not implemented clinical capabilities or validated
training labels. The live config/questions.json is read but never modified.
"""
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent.parent

# Domain, related existing domain, evidence sources, reviewer, operators,
# outcome/process/balancing measures, then six question | data | failure cases.
DOMAINS=[
('VALUE','Patient value over a full care cycle','PAL', ['FOUND01','FOUND02','INT03'], 'Clinical outcomes lead and patient representatives',
 ['longitudinal outcome linking','resource costing','missing-response analysis'],
 ['Function and patient-defined goals','Patient-reported outcome capture','Treatment and survey burden'], '''
Which pathways look inexpensive in claims but consume excessive patient time, caregiver time or unreimbursed clinical work?|Patient/caregiver time diaries; staff resource use; claims; longitudinal outcomes|Do not equate charges or paid amounts with resource costs.
Which patients meet disease-control targets while their function or ability to live independently is worsening?|Repeated function and symptom reports; disease measures; care goals|A normal disease metric must not override deteriorating function.
Where do apparently excellent patient-reported outcomes reflect missing responses from the sickest patients?|Survey invitations and responses; baseline severity; language; follow-up status|Complete-case averages cannot be presented as the whole population's outcome.
Which separate disease programs impose conflicting instructions or excessive cumulative treatment burden on the same person?|Program enrollment; instructions; medication schedules; patient preferences|Individually valid disease rules can conflict at patient level.
Which recovery trajectories improve time at home without transferring unsustainable work to an unpaid caregiver?|Residence timeline; caregiver burden; patient preference; utilization|More days at home alone is not evidence of better care.
Which clinically equivalent pathway options better match an individual's priorities for function, symptoms and convenience?|Validated pathway options; outcomes; elicited preferences; resource costs|Do not infer preferences from age, spending or diagnosis.'''),
('CAUSAL','Whether an intervention actually helped','LEARN',['EVAL01','EVAL02','EVAL04','ENG04'],'Evaluation scientist and clinical sponsor',
 ['target-trial specification','difference-in-differences diagnostics','heterogeneous-effect estimation'],
 ['Patient outcome under a defensible comparison','Intervention reach and fidelity','Adverse effects and unequal access'],'''
Would the apparent success of our high-utilizer program remain if untreated patients selected at the same utilization peak were compared?|Eligibility-time utilization; comparison cohort; intervention dates; follow-up|Regression to the mean can produce impressive before-after improvement.
Which subgroup benefits are prespecified and replicated, and which emerged only after searching the data?|Analysis plans; subgroup definitions; effect intervals; replication cohort|A post hoc subgroup is a hypothesis, not an established targeting rule.
Which apparent intervention effects disappear when attribution is fixed at baseline rather than updated after patients move?|Attribution history; deaths; disenrollment; provider changes|Selective attrition can make performance look better.
Where do intervention and comparison groups have too little overlap to estimate who benefits reliably?|Baseline features; assignment mechanism; treatment exposure|Do not extrapolate a treatment effect to unsupported patients.
Could spillover to untreated clinics be contaminating our program comparison?|Shared staff; patient movement; referral networks; rollout timing|A comparison site exposed to the intervention is not an untreated control.
Which proposed program can be evaluated through a feasible randomized or phased rollout before widespread investment?|Operational capacity; equipoise; eligibility; outcome frequency; implementation schedule|A sophisticated observational estimator does not substitute for an available stronger design.'''),
('ECON','Financial truth and contract incentives','FIN',['CMS02','CMS04','PAY04','MED01'],'Finance, actuarial and clinical leadership',
 ['cash-flow reconciliation','price-volume-mix decomposition','claims-development analysis'],
 ['Net cost and patient benefit reported separately','Reconciled payment and expense ledger','Underuse and access deterioration'],'''
How much of each reported saving is benchmark-relative performance, estimated causal spending reduction, or net savings after program payments and operating costs?|Benchmark calculation; claims; comparison design; bonuses; delivery costs|Never label gross benchmark savings as net causal savings.
Which cost increases represent restored access to needed care rather than avoidable utilization?|Service category; access barriers; clinical indication; outcome trend|Lower spending caused by denied care must not score as success.
Which service redesign benefits the risk-bearing entity while reducing hospital fee-for-service revenue, and what internal transfer would sustain it?|Contract participation; marginal revenue/cost; delivery expense; clinical outcomes|Payer savings and provider margin are not interchangeable.
How sensitive is projected contract performance to claim runout, attribution changes and high-cost outliers?|Claims arrival history; member months; attribution versions; stop-loss clauses|Immature claims must not create a false profit signal.
Which financial gains are explained by risk-score growth without corresponding changes in independently observed clinical burden?|Coding history; audited evidence; clinical severity; risk-adjustment versions|Improved documentation is not proof of improved health.
Which savings estimates double-count a patient event across an episode bundle, an ACO and a disease program?|Event-level claims; program overlap; reconciliation rules|The same avoided event cannot be summed independently across programs.'''),
('ACCESS','Continuity and practical access','PCP',['ORG01','ORG05','INT01','INT03'],'Primary care and access operations',
 ['continuity measurement','queue and travel analysis','pathway conformance'],
 ['Function and avoidable acute use','Accessible completed care','Travel and clinician workload'],'''
Where is primary-care continuity breaking even though visit counts remain high?|Clinician identity across visits; patient preference; appointment history|Many visits with rotating clinicians do not establish continuity.
Which populations have a named PCP but no realistic route to a timely visit?|Empanelment; appointment offers; transportation; language; hours|Directory presence is not practical access.
Where does changing the timing or length of visits address a documented need better than adding another program?|Visit duration; patient goals; missed care; staffing|Extra visits should not be assumed beneficial without an identified mechanism.
Which rural services should be supported locally because travel burden and loss of access outweigh apparent unit-price savings elsewhere?|Travel times; local capability; outcomes; prices; patient preference|The lowest unit price can create a worse feasible care option.
Which primary-care investments appear effective only after allowing a realistic implementation and follow-up period?|Investment dates; staff readiness; patient cohorts; runout|Do not compare mature sites with newly enrolled sites without time alignment.
Where is an external provider filling a documented local capability gap that our network should explicitly recognize?|Referral reason; network capacity; specialty capability; external outcomes|Out-of-network care is not automatically leakage to eliminate.'''),
('RENAL','Kidney and heart-failure pathway completeness','CKD',['ORG06','MODEL33','MODEL92','MODEL101'],'Nephrology, cardiology and primary care',
 ['temporal evidence reconciliation','pathway state machine','competing-risk analysis'],
 ['Patient-centered kidney/HF outcomes','Completed and appropriate pathway transitions','Treatment adverse effects and burden'],'''
Which patients were referred for transplant but stalled before evaluation, and what barrier is actually documented?|Referral and evaluation status; contraindications; patient choice; center communication|A referral count is not completed access to transplant evaluation.
Which apparent renal program improvements depend on excluding patients who die or lose coverage before follow-up?|Cohort entry; death; coverage; outcome follow-up|Survivor-only metrics can overstate success.
Where do clinical kidney-care quality measures improve while the program still has a net financial loss?|Quality measures; claims; program payments; operating costs|Clinical improvement and financial performance require separate conclusions.
Which HF therapy gaps remain unresolved because the evidence needed for a safe titration decision is unavailable?|Medication use; recent relevant observations; clinician hold rationale; access history|Absence of maximum dose does not prove undertreatment.
Which combined cardiac and renal problems produce competing treatment considerations that a single-disease gap list misses?|Longitudinal cardiac/renal evidence; medication changes; symptoms; documented decisions|Flag for clinician reconciliation instead of independently applying conflicting rules.
Which patients match an ACCESS clinical topic but lack the program, timing or participant evidence required to recommend enrollment?|Versioned track rules; clinical evidence; coverage; participating organization; dates|Clinical similarity alone is not confirmed program eligibility.'''),
('SERIOUS','Serious illness, dementia and caregiver needs','PAL',['MODEL83','MODEL42','INT03'],'Palliative care, geriatrics and patient/caregiver representatives',
 ['goal/evidence extraction','caregiver relationship modeling','longitudinal symptom analysis'],
 ['Goal-concordant care and symptom relief','Goals and support needs assessed','Caregiver burden and unwanted treatment'],'''
Which intensive treatments are associated with persistent symptom burden or conflict with documented goals and warrant clinician review?|Patient goals; symptom trajectories; treatment response; clinical context|Never label a person's care futile from cost or prognosis alone.
Which caregiver's needs are being attributed to the patient, or lost when records describe a household rather than an individual?|Note subject; household relationships; caregiver reports; consent/context|Do not convert a caregiver diagnosis into a patient diagnosis.
Which dementia support needs change when the primary caregiver becomes ill or unavailable?|Caregiver availability; functional needs; support services; dated notes|A historical caregiver plan is not current support.
Where do payment rules obscure opportunities for supportive care alongside disease-directed treatment?|Current program rules; benefit eligibility; goals; treatment plan|Do not equate palliative care with hospice enrollment or stopping treatment.
Which apparent improvements in time at home coincide with worsening symptoms or unwanted avoidance of acute care?|Days at home; symptom reports; preferences; emergency access|Time at home cannot independently define success.
Which patients lack enough symptom, function or preference evidence to assess whether current care is helping them?|Patient-reported outcomes; function; goals; treatment history|Return evidence missing rather than a confident value judgment.'''),
('PHARM','Medication access and outcomes-based therapies','BEN',['MODEL89','MODEL107','PAY05','MED01'],'Pharmacy and benefits specialists',
 ['medication-event reconciliation','coverage versioning','longitudinal outcome linkage'],
 ['Appropriate sustained treatment and patient outcomes','Access barriers resolved','Adverse effects and financial toxicity'],'''
Which therapy interruptions followed a benefit transition rather than a patient's decision to stop treatment?|Enrollment dates; formulary versions; fills; denial and note evidence|Do not label nonadherence when coverage changed.
Which expensive therapies need outcome follow-up that extends beyond a patient's current payer enrollment?|Therapy date; agreement terms; outcomes; payer transitions|Missing follow-up is not evidence of treatment failure.
Where do authorization approvals fail to result in dispensing or administration?|Authorization; pharmacy and administration events; patient reports|Approval is a process milestone, not delivered therapy.
Which out-of-pocket burdens remain after nominal drug-price reductions or rebates?|Benefit design; actual paid amounts; assistance; patient bills|Manufacturer rebates do not directly establish the patient's payment.
Which medication-access interventions improve persistence without increasing adverse events or unacceptable burden?|Dispensing; actual use; symptoms; adverse events; preferences|Persistence alone is not an adequate clinical objective.
Which program-level coverage opportunities differ between Medicaid, Part D and temporary bridge arrangements?|Product-specific rules; enrollment; effective dates; state/plan participation|One program name cannot substitute for distinct benefit timelines.'''),
('BEHAV','Behavioral health and whole-person needs','BEN',['MODEL86','MED02','EVAL04','CMS05'],'Behavioral health and community-service partners',
 ['care cascade analysis','barrier extraction','matched outcome evaluation'],
 ['Symptoms, function and sustained engagement','Completed access to appropriate care','Stigma, burden and unmet urgent needs'],'''
Where has improved social-needs screening failed to produce treatment engagement or need resolution?|Screening; referrals; delivered services; patient reports|A referral or screen completion does not close a need.
Which behavioral-health patients receive frequent specialty contact but have unaddressed physical-health needs?|Specialty visits; primary-care evidence; clinical findings; preferences|Do not infer whole-person care from behavioral visit frequency.
Which patients appear disengaged because contact details, language or practical access are wrong?|Outreach attempts; verified contact; language; transport; work constraints|Failed contact is not refusal of care.
Where is referral demand exceeding the actual capacity of community organizations?|Referrals; waitlists; eligibility; hours; service fulfillment|A resource directory listing does not establish available service capacity.
Which apparent program benefits differ between navigation uptake, clinical utilization and patient-reported need resolution?|Assigned intervention; delivered navigation; claims; need follow-up|Do not invent a mechanism when outcome measures diverge.
Which combinations of physical and mental health needs are missed by programs organized around a single diagnosis?|Longitudinal diagnoses; symptoms; function; service enrollment|More diagnoses alone does not specify the best intervention.'''),
('FAMILY','Maternal, pediatric and household context','POL',['MODEL85','MODEL31','MODEL108'],'Maternal/pediatric clinicians and Medicaid program leads',
 ['relationship-aware linkage','life-course time windows','equity stratification'],
 ['Maternal/child outcomes and function','Continuity across life-stage transitions','Family burden and inappropriate attribution'],'''
Which postpartum needs disappear from view when pregnancy-related coverage or care teams change?|Pregnancy timeline; coverage; postpartum encounters; physical/mental needs|Adult annual measures may miss the postpartum transition.
Which maternal and infant outcomes require linked records but must retain separate patient identities?|Verified relationships; birth records; maternal/infant events|Shared household or surname must not merge people.
Which children lose effective support when transitioning from pediatric to adult services?|Age-specific program rules; service eligibility; care plan; handoff|Do not assume the adult version of a program has identical eligibility.
Which apparent pediatric utilization reductions coincide with loss of school or community support data?|Clinical utilization; data-feed coverage; school/community services|Missing cross-sector data can masquerade as improved health.
Which family-level barriers are documented but not represented in disease-only risk models?|Family reports; housing/transport; caregiving; individual clinical needs|Household context must not become an unsupported individual diagnosis.
Which local maternal or pediatric intervention could be evaluated without hiding differences in access across subgroups?|Outcome definitions; baseline risk; subgroup data; implementation exposure|Aggregate improvement must not mask worsening outcomes in a subgroup.'''),
('POSTACUTE','Recovery and post-acute comparisons','SNF',['MODEL91','MODEL109','QI02','MODEL29'],'Hospital and post-acute clinical teams',
 ['episode construction','case-mix adjustment','capacity-constrained comparison'],
 ['Recovery, safety and return to preferred setting','Complete transition information','Caregiver load and avoidable delay'],'''
How do conclusions about a post-acute pathway change when evaluated over the contract episode versus the patient's longer recovery?|Episode definitions; extended claims; function; patient goals|A short episode can move cost or harm outside the measurement window.
Which SNF comparisons confound long-stay residents with short-stay rehabilitation patients?|Residence status; admission purpose; baseline function; clinical severity|Do not rank fundamentally different populations together.
Which transition failures involve missing contingency plans rather than missing documents?|Handoff text; action items; pending tests; escalation plans|Document transmission does not prove clinically useful information transfer.
Which facility has the best supported fit for a patient's needs after accounting for uncertainty, capacity and preferences?|Comparable outcomes; sample size; staffing/capabilities; openings; preferences|A noisy league table must not override patient choice or unavailable beds.
Which apparent hospital improvements shift complications or expenses to home health, SNFs or families?|Cross-setting events; functional outcomes; costs; caregiver reports|Discharge earlier is not automatically better recovery.
Which TEAM and CJR-X evaluations would be wrong if they reused the same window or participant-exclusion logic?|Controlling program versions; episode anchors; participant lists; claims|Similar procedure names do not imply identical episode rules.'''),
('SAFETY','Quality improvement and measurement discipline','SEP',['QI01','QI02','QI03','QI04','ENG02'],'Quality and patient-safety teams',
 ['statistical process control','denominator audit','implementation-fidelity analysis'],
 ['Patient harm and clinical recovery','Reliable delivery of intended practice','Overtreatment and workflow burden'],'''
Is a rise in device-associated infections still unusual after accounting for device exposure and surveillance changes?|Device days; infection definition versions; case ascertainment; unit mix|Raw event counts can change because exposure or surveillance changed.
Where did sepsis recognition improve while unnecessary treatment or treatment-related harm increased?|Recognition/treatment timestamps; severity; adverse events; adjudication|A faster process measure needs balancing outcomes.
Which handoff omissions recur before harm, and which are merely common in all handoffs?|Handoff evidence; harms; comparable non-harm episodes|Frequent omissions among adverse events alone cannot establish predictive value.
Which improvements are sustained changes beyond ordinary variation rather than a favorable month?|Stable repeated measures; denominators; interventions; baseline period|Do not rank a clinic based on random variation.
Where is a quality intervention documented as complete but only some intended patients actually receive it?|Eligible cohort; delivery logs; completion evidence; staff/site dates|Documented adoption is not treatment fidelity.
Which intervention reduces one measured failure while creating a new bottleneck or staff burden elsewhere?|Process times; workload; patient outcomes; downstream queues|Local optimization can damage the whole pathway.'''),
('RULES','Policy updates and multi-payer reconciliation','POL',['CMS01','CMS03','PAY05','MODEL101','MODEL99','MODEL109'],'Policy/contract specialists with clinical owners',
 ['document version diff','typed rule compilation','temporal eligibility evaluation'],
 ['Correct and timely eligibility decisions','Reviewed rule updates and impacted-cohort replay','False enrollment and duplicate obligations'],'''
Which newly published policy is a proposal, final rule, operational clarification or actual effective change?|Document authority; publication/effective dates; amendment history|Publication time is not the date a rule applies.
Which beneficiaries qualify clinically but cannot enroll because the organization, state, payer or performance period is ineligible?|Clinical evidence; participant lists; geography; enrollment; rule version|Clinical eligibility and participation eligibility must both be satisfied.
Which changed clause requires recomputing a cohort, and which merely changes explanatory wording?|Old/new clauses; normalized rule representation; dependency map|Recompute affected decisions rather than every chart or no charts.
Which measure with the same display name has incompatible exclusions or lookback periods across contracts?|Complete measure specifications; contract/product identifiers; dates|Do not merge measures by name alone.
Which historical answers change under a corrected specification, and what did we reasonably know at the original decision time?|Original source snapshots; arrival times; correction effective date; executions|Retrospective truth and contemporaneously available evidence differ.
Which overlapping programs allow concurrent participation but require separate financial attribution or service responsibilities?|Overlap clauses; participant agreements; patient enrollment; episode events|Overlap does not automatically mean exclusion or additive savings.'''),
('EQUITY','Equity, geography and community capacity','DISC',['INT07','INT03','MED01','CMS05','ENG06'],'Community partners, access teams and clinical equity leads',
 ['stratified outcome analysis','capacity optimization','geospatial access analysis'],
 ['Need-adjusted benefit and access','Resources delivered to documented needs','Exclusion, travel and administrative burden'],'''
Which low-utilization communities have high unmet need that a spending-trained model systematically overlooks?|Independent need measures; access; symptoms; utilization; geography|Low spending can indicate unmet need rather than low clinical risk.
Which site performs poorly because it lacks reachable community resources rather than clinician effort?|Local capacity; eligibility; travel; referral fulfillment; case mix|Do not assign individual blame from an ecological shortage.
Would a proposed resource allocation still serve high-need groups when expected financial return is lower?|Need; intervention evidence; capacity; benefit distribution; financial effect|Financial ranking must not silently replace clinical objectives.
Which neighborhood-level signals improve discovery without stereotyping individual residents?|Area context; individual evidence; uncertainty; validation by subgroup|Area deprivation is not proof of an individual's financial need.
Where does a digital-only pathway exclude patients who need accessible, non-digital alternatives?|Digital access; language; disability; preference; completion outcomes|Portal inactivity is not lack of engagement or need.
Which community intervention appears transferable only when staffing, governance and funding conditions are similar?|Source setting; implementation resources; local capabilities; outcomes|A successful international or municipal association is not an immediately portable US effect.'''),
('DATAINT','Truth in messy and delayed data','DATA',['INT01','INT10','PAY05','ENG01'],'Data engineering and clinical informatics',
 ['bitemporal joins','entity resolution','source completeness and contradiction detection'],
 ['Correct reproducible patient/population conclusions','Timely traceable evidence availability','False merges and incorrect gap closure'],'''
Which clinical conclusions change when event time and arrival time are modeled separately?|Event timestamp; ingestion timestamp; corrections; historical query cutoff|Do not use later-arriving evidence in a historical prediction.
Which apparent cross-source contradictions reflect an intentional medication change rather than a data error?|Dated prescriptions; administrations; dispensing; discontinuation rationale|Do not choose the newest note solely by document timestamp.
Which missing observations are concentrated in an outside provider or payer feed rather than in patient care?|Source coverage; linkage confidence; expected feeds; observed services|No local record is not evidence the service never happened.
Which ontology mappings collapse clinically different concepts or incompatible units into the same label?|Raw values; terminology mappings; units; provenance; validation cases|A neat ontology can hide a harmful normalization error.
Which duplicate or copied assertions are artificially strengthening a patient conclusion or population pattern?|Text similarity; original source lineage; dates; independent corroboration|Repeated text is not repeated independent evidence.
Which research or policy ingestion succeeded technically but returned a bot page, empty document or obsolete redirect?|Response metadata; content validation; expected sections; version provenance|HTTP success is not successful evidence retrieval.'''),
('DISCOVERY','Finding overlooked and testable opportunities','DISC',['ENG02','ENG03','ENG04','EVAL01'],'Clinical discovery team and evaluation scientist',
 ['process mining','negative-control analysis','multiple-testing control'],
 ['Replicated clinically useful discoveries','Hypotheses tested with defensible comparisons','False discoveries and analyst burden'],'''
Which deviations from successful recovery pathways precede harm after accounting for how ill patients already were?|Time-stamped pathways; baseline severity; comparison episodes|A longer pathway may reflect severity rather than cause harm.
Which admission clusters persist after removing copied text, coding changes and missing-feed periods?|Deduplicated evidence; coding versions; feed availability; clinical events|Do not present a documentation cluster as a disease cluster.
Which candidate explanation fails a negative-control outcome or a placebo intervention date?|Candidate causal design; control outcomes; preperiod data|A plausible narrative must survive falsification tests.
Which apparent provider success survives shrinkage, uncertainty estimates and validation in a later cohort?|Provider sample sizes; risk factors; later outcomes|Extreme small-sample performance is not a reliable best-provider signal.
Which unasked question is worth pursuing because a feasible intervention and measurable patient benefit exist?|Evidence gaps; candidate actions; capacity; outcome observability|Novel correlations without a decision are not actionable insights.
What is the smallest additional data collection that could resolve a consequential uncertainty?|Current evidence; decision options; acquisition cost; expected information gain|Do not order more data merely because it is available.'''),
('COMPUTE','Accuracy per unit of total operating cost','LEARN',['ENG01','ENG03','ENG04','ENG06'],'ML evaluation, engineering and clinical reviewers',
 ['selective routing','dependency caching','cost-aware evaluation'],
 ['Correct supported decisions at stated coverage','Efficient retrieval and evidence reuse','Missed contraindications and reviewer workload'],'''
Which questions can be answered by an audited query or rule without any model invocation?|Question plan; ontology bindings; rule version; benchmark labels|A language model should not invent counts that a query can compute.
Which evidence fragments should be reranked for a question while retaining contradictory or exclusionary evidence?|Candidate spans; source types; timestamps; answer-changing labels|A relevance reranker that drops contraindications can improve fluency while harming correctness.
Which cached assertions remain valid after a corrected lab, new note or policy amendment?|Content hashes; assertion provenance; dependency graph; versions|Cache reuse must not survive a material source or rule change.
Which small-model outputs warrant frontier review because of consequence and uncertainty rather than document length alone?|Calibration by task; disagreement; consequence class; verified labels|A model's self-confidence is not calibrated accuracy.
Which model combination reduces total cost after including preprocessing, local compute, human review and maintenance?|Task costs; latency; energy/hardware allocation; review effort; accuracy|API token cost alone is not product cost.
Which training examples teach a reusable reasoning operation rather than memorizing a synthetic patient or benchmark world?|Patient-family IDs; templates; source versions; split lineage|Repeated draws of the same synthetic family must not cross train/test splits.''')]

# Synthetic scenarios specify desired behavior, not clinical policy thresholds.
CASES=[
('CASE01','CAUSAL','Peak utilization','EVAL01','Synthetic intervention and comparison cohorts are selected after a utilization spike; both improve similarly.','Do not attribute the before-after drop to the program; request a defensible contrast.','claims; intervention; comparator'),
('CASE02','CAUSAL','Random sample versus randomization','ORG03','A report compares treated practices to a random national sample; treatment was not randomized.','Classify as observational, not a randomized trial.','report methods'),
('CASE03','ECON','Gross versus net','CMS02','A synthetic ledger contains benchmark savings, incentive payments and delivery expenses.','Report distinct ledger totals and labels; do not call gross savings net.','benchmark; payments; expenses'),
('CASE04','ECON','Missing mature claims','MED01','Recent apparent spending reduction coincides with delayed inpatient claims.','Flag incomplete runout; withhold a confirmed savings claim.','claims event and arrival dates'),
('CASE05','ECON','Coding-only improvement','CMS04','Risk-adjusted financial performance improves after coding changes while independent severity and outcomes are unchanged.','Separate documentation/payment effect from clinical improvement.','coding; severity; financial results'),
('CASE06','ECON','Necessary access costs more','MED01','Previously untreated patients obtain indicated services; costs rise while patient outcomes improve.','Do not classify the cost increase alone as failure or waste.','access; indications; outcomes; cost'),
('CASE07','POSTACUTE','Costs beyond episode window','MODEL91','Two synthetic recovery pathways have identical measured short-episode cost but different later complications.','Report the contract result separately from extended recovery outcomes.','short and extended event histories'),
('CASE08','POSTACUTE','Program window mismatch','MODEL109','A synthetic question attempts to reuse a TEAM episode definition for CJR-X.','Retrieve each controlling version and reject automatic reuse.','two program specifications'),
('CASE09','RULES','Program has not started','MODEL99','A patient matches an announced program topic before its performance period.','Do not mark currently enrollable based on diagnosis alone.','as-of date; program dates; participation'),
('CASE10','RULES','Announced plan ended early','MODEL82','An older page advertises a long model duration; a later authoritative notice ends it early.','Preserve history but use the controlling termination notice for current availability.','versioned notices'),
('CASE11','RULES','Proposal treated as final','MODEL105','A proposed rule changes eligibility but no controlling final change is supplied.','Label proposed and withhold applying it as current law.','authority and effective dates'),
('CASE12','RULES','Overlapping programs','MODEL91','A synthetic beneficiary is aligned to an ACO and has a qualifying episode under a concurrent model.','Evaluate explicit overlap rules; do not assume exclusion or sum savings.','participation; overlap clause; event'),
('CASE13','RENAL','Outside consult','ORG06','A synthetic local referral gap is contradicted by a dated outside specialist consultation.','Mark already addressed if evidence satisfies the task; preserve provenance.','local record; outside note'),
('CASE14','RENAL','Dose without tolerability context','MODEL101','A synthetic HF record lacks relevant observations and has ambiguous dose history.','Request clinician review and missing evidence rather than assert undertreatment.','medication; missing context'),
('CASE15','SERIOUS','Caregiver subject confusion','MODEL83',"A note says the caregiver is depressed while the patient's cognitive impairment is described separately.",'Keep assertions attached to the correct person.','household note'),
('CASE16','SERIOUS','High cost is not futility','MODEL42','A synthetic costly treatment improves the function the patient values.','Do not label care futile or recommend withdrawal on cost grounds.','goals; function; treatment response'),
('CASE17','VALUE','Missing survey respondents','INT03','Only healthier synthetic patients respond to the outcome survey.','Show response bias and missingness before ranking performance.','invitations; responses; severity'),
('CASE18','VALUE','Hidden caregiver burden','MODEL83','Days at home increase while the sole caregiver reports unsustainable strain.','Report both outcomes and identify a support review need.','residence; caregiver evidence'),
('CASE19','BEHAV','Referral is not resolution','MED02','Social screening and referrals rise but delivered treatment and need resolution do not.','Keep process, access and outcome measures separate.','screen; referral; fulfillment; follow-up'),
('CASE20','EQUITY','Low spending hides need','INT03','A synthetic subgroup has low spending because of transportation and coverage barriers.','Do not classify it as low need solely from spending.','independent need; access; claims'),
('CASE21','PHARM','Approved but never delivered','PAY05','A medication authorization is approved but dispensing fails during coverage transition.','Retain an unresolved access barrier; do not mark treatment delivered.','authorization; eligibility; fill'),
('CASE22','FAMILY','Maternal/infant identity','MODEL85','Linked synthetic maternal and infant records share a household and billing context.','Link the relationship without merging clinical identities.','verified relationship; events'),
('CASE23','SAFETY','Changed infection denominator','QI01','Infection event counts fall while device exposure falls proportionally.','Compute the exposure-adjusted measure; do not infer an improved infection rate from counts.','events; device exposure'),
('CASE24','SAFETY','Process gain with harm','QI03','A synthetic treatment-timing metric improves while treatment-related harm rises.','Surface the balancing outcome and reject unconditional success.','process; patient harm'),
('CASE25','DISCOVERY','Small-sample winner','ENG02','A small facility has the best observed recovery rate with substantial uncertainty.','Retain uncertainty and avoid a confident best-facility claim.','case mix; sample size; outcomes'),
('CASE26','DATAINT','Evidence arrived later','ENG01','A dated synthetic clinical event reaches the platform after the historical prediction cutoff.','Exclude it from contemporaneous features while retaining it for later truth assessment.','event and arrival timestamps'),
('CASE27','DATAINT','Copy-forward votes','ENG03','The same synthetic assertion is copied across multiple notes without independent corroboration.','Count one originating evidence chain, not multiple independent confirmations.','note lineage'),
('CASE28','DATAINT','HTTP success without content','CMS01','A public source returns a successful response containing a bot challenge or empty application shell.','Mark retrieval unusable and do not claim the document was reviewed.','response metadata and content'),
('CASE29','COMPUTE','Stale cached answer','ENG01','A synthetic lab correction changes evidence used in a cached answer.','Invalidate dependent assertions and answers; retain unrelated cached work.','hashes; dependencies; correction'),
('CASE30','COMPUTE','Reranker drops exclusion','ENG04','Highly similar positive evidence outranks a short decisive exclusion in a synthetic record.','Retain answer-changing exclusion evidence and avoid the false-positive action.','retrieval candidates; exclusion labels'),
('CASE31','COMPUTE','Synthetic family leakage','ENG05','Different IDs are drawn from the same synthetic source-patient family across worlds.','Keep the family and derivatives in the same split; do not treat copies as independent patients.','generation lineage'),
('CASE32','ACCESS','No feasible appointment','ENG06',"The highest-ranked local service lacks accessible appointments; an outside service meets the patient's needs.",'Present feasible patient-preferred options and explain the constraint.','availability; needs; travel; preferences')]

def write_json(name,obj):
    (ROOT/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')

def build():
    baseline=json.loads((PROJECT/'config/questions.json').read_text())
    existing={g['id']:[q['id'] for q in g['questions']] for g in baseline['groups']}
    groups=[];flat=[]
    for domain,title,related,sources,owner,operators,measures,body in DOMAINS:
        questions=[]
        for n,line in enumerate(body.strip().splitlines(),1):
            question,data,failure=line.split('|')
            q=dict(id=f'R-{domain}-{n:02d}',question=question,source_refs=sources,
                source_role='Research inspiration, not a validated clinical or payment rule',
                related_existing_question_ids=existing[related],
                relationship_to_existing='Additional formulation/refinement; not assumed a unique new capability',
                required_data=[s.strip() for s in data.split(';')],operators=operators,review_owner=owner,
                outcome_measure=measures[0],process_measure=measures[1],balancing_measure=measures[2],
                failure_to_catch=failure,
                answer_contract=['Versioned cohort and time window','Source-linked evidence and contradictions','Supported finding separated from causal hypothesis','Patient benefit separated from financial effect','Feasible response, owner, uncertainty and outcome evaluation'],
                execution=dict(status='proposed_not_implemented',handler=None),
                training_status='Author-drafted task specification; requires expert adjudication',
                synthetic_case_ids=[c[0] for c in CASES if c[1]==domain])
            questions.append(q);flat.append(q)
        groups.append(dict(id='R-'+domain,title=title,questions=questions))
    bank=dict(title='Research supplement to the CareSignal question registry',as_of='2026-10-08',
        status='Research curriculum only. No live API capability added.',groups=groups)
    write_json('question_supplement.json',bank)
    with (ROOT/'question_supplement.csv').open('w',newline='') as f:
        keys=['id','question','required_data','operators','review_owner','outcome_measure','process_measure','balancing_measure','failure_to_catch','source_refs']
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for q in flat:w.writerow({k:'; '.join(q[k]) if isinstance(q[k],list) else q[k] for k in keys})
    merged=dict(baseline)
    merged.update(title='Combined research snapshot; not the executable runtime registry',version='research-snapshot-1',
        groups=baseline['groups']+groups,
        provenance=dict(baseline_path=str(PROJECT/'config/questions.json'),baseline_sha256=hashlib.sha256((PROJECT/'config/questions.json').read_bytes()).hexdigest(),
            note='Original execution statuses preserved. Research rows are unimplemented and may refine existing questions.'))
    write_json('combined_question_snapshot.json',merged)
    cases=[]
    for sid,domain,title,source,scenario,expected,inputs in CASES:
        cases.append(dict(id=sid,title=title,domain=domain,source_refs=[source],
            synthetic=True,scenario=scenario,expected_behavior=expected,required_inputs=inputs.split(';'),
            proposed_question_ids=[q['id'] for q in flat if q['id'].startswith('R-'+domain+'-')],
            status='Scenario specification; not a generated patient cohort or executed clinical benchmark',
            label_provenance='Original analyst-authored behavioral expectation inspired by cited research',
            expert_review='Pending',grading='Expert-adjudicated evidence/decision rubric; deterministic checks only where specified',
            leakage_controls=['Split by source-patient family','Hold out policy versions and sites','Do not train on the final evaluation labels']))
    write_json('synthetic_case_specs.json',cases)
    lines=['# Research question supplement','',f'{len(flat)} additional formulations in {len(groups)} domains. Some refine existing questions; none is newly implemented by this research.','']
    for g in groups:
        lines.extend([f"## {g['title']}",''])
        for q in g['questions']:
            lines.extend([f"**{q['id']} — {q['question']}**",'',f"Data: {'; '.join(q['required_data'])}.",
                f"Failure to catch: {q['failure_to_catch']}",f"Owner: {q['review_owner']}. Sources: {', '.join(q['source_refs'])}.",''])
    (ROOT/'QUESTIONS.md').write_text('\n'.join(lines))
    print(json.dumps(dict(existing=sum(len(g['questions']) for g in baseline['groups']),supplement=len(flat),combined=sum(len(g['questions']) for g in merged['groups']),scenarios=len(cases))))

if __name__=='__main__':build()
