"""Synthetic paraphrase templates for the learned router (training material, never a benchmark).

Written from the capability_registry.PRODUCT descriptions, not from bench/acceptance; templates that happened to
reproduce an acceptance question verbatim were removed (train.py also drops anything with token Jaccard >= 0.5 to a bench question). Each template carries
the engine its author intended; the deterministic local compiler relabels any question it compiles, so the
intended label is used only where the compiler declines (exactly the questions that reach the router).
"""
import random

FILL = {
    'cond': ['diabetes', 'heart failure', 'CKD', 'COPD', 'hypertension', 'asthma', 'atrial fibrillation', 'depression',
             'dementia', 'CHF', 'chronic kidney disease', 'type 2 diabetes', 'obesity', 'coronary artery disease'],
    'conds': ['diabetics', 'heart failure patients', 'CKD patients', 'COPD patients', 'hypertensive patients', 'asthmatics',
              'our afib patients', 'patients with depression', 'members with dementia', 'CHF members', 'obese patients'],
    'lab': ['A1c', 'eGFR', 'LDL', 'systolic blood pressure', 'potassium', 'creatinine', 'BNP', 'weight', 'hemoglobin', 'blood pressure'],
    'event': ['readmission', 'an ED visit', 'a hospital admission', 'an ER return', 'an urgent care visit', 'an inpatient stay',
              'an observation stay', 'the next admission'],
    'events': ['readmissions', 'ED visits', 'hospital admissions', 'ER visits', 'admissions', 'inpatient stays', 'avoidable ED use'],
    'group': ['practice', 'PCP', 'payer', 'age band', 'sex', 'clinic', 'insurance type', 'provider'],
    'unit': ['practices', 'PCPs', 'clinics', 'primary care doctors', 'physicians', 'payers'],
    'metric': ['ED visits', 'admissions', 'PMPM', 'readmissions', 'A1c control', 'blood pressure control', 'total cost of care'],
    'n': ['20', '25', '40', '50', '75', '100', '150', '200'],
    'days': ['7', '14', '30', '60', '90', '180'],
    'program': ['care management', 'transitions of care', 'our community health worker program', 'remote patient monitoring',
                'care coordination', 'the diabetes education program', 'home visits', 'the CHF clinic', 'pharmacist outreach'],
    'drug': ['an SGLT2 inhibitor', 'a statin', 'a GLP-1', 'metformin', 'an ACE inhibitor', 'a beta blocker', 'insulin'],
    'grain': ['week', 'month'],
    'horizon': ['next quarter', 'the next 8 weeks', 'next month', 'the next 6 months', 'this winter', 'the next 12 weeks'],
    'need': ['food insecurity', 'transportation barriers', 'housing instability', 'financial strain'],
    'feed': ['lab', 'claims', 'ADT', 'pharmacy', 'eligibility', 'EHR'],
    'when': ['tomorrow', 'next week', 'Monday', 'this Friday', 'the next two weeks'],
    'bench': ['950', '1,020', '1,100', '880', '1,240', '760', '1,005', '1,150'],
    'spend': ['4', '6.50', '9', '12', '3.25'],
    'level': ['A', 'B', 'C', 'D', 'E'],
    'arm': ['SGLT2 inhibitors', 'GLP-1 agonists', 'DPP-4 inhibitors', 'metformin', 'sulfonylureas', 'ACE inhibitors', 'ARBs', 'thiazides',
            'calcium channel blockers', 'empagliflozin', 'semaglutide', 'sitagliptin', 'insulin', 'MRAs'],
    'tlab': ['eGFR', 'A1c', 'LDL', 'systolic blood pressure', 'kidney function', 'HbA1c'],
    'area': ['type 2 diabetes', 'heart failure', 'hypertension', 'depression', 'CKD', 'rheumatoid arthritis', 'diabetes', 'HFrEF GDMT'],
    'tcond': ['heart failure', 'type 2 diabetes', 'CKD', 'COPD', 'hypertension', 'atrial fibrillation', 'obesity', 'asthma'],
}

T = {
    'explain': [
        'What drives {events} among {conds}?', 'Which factors are linked to {events} for patients with {cond}?',
        'why do some {conds} end up with {events}', 'What characteristics go along with uncontrolled {cond}?',
        'What is associated with high {lab} in {conds}?', 'Help me understand what separates {conds} who have {events} from those who do not',
        'Key drivers of {events} in our population', 'what contributes to poor {lab} control',
        'Explain why {events} are higher for some members', 'What is behind the {events} among patients with {cond}?',
        'Which patient traits predict having {events}?', 'what sets apart {conds} with frequent {events}',
        'Contributing factors for {cond} patients being hospitalized', 'which variables are most associated with {events}',
        'Why are some {conds} not controlled?', 'What explains elevated {lab} among {conds}?',
    ],
    'discover': [
        'Where are {events} concentrated?', 'Find pockets of the population with unusually high {events}',
        'Which groups have more {cond} than expected?', 'Are there hotspots of {events} by {group} or {group}?',
        'Show me segments where {cond} is overrepresented', 'Which combinations of {group} and {group} stand out for {events}?',
        'any unexpected clusters of uncontrolled {cond}', 'Where do {conds} cluster across practices and payers?',
        'Surface subgroups with surprisingly high {events}', 'Look for concentrations of {need} among {conds}',
        'Which subpopulations are outliers for {events}?', 'find hidden hotspots for {events} nobody has looked at',
        'where in the network is {cond} clustering', 'What segments have abnormal rates of {events}?',
    ],
    'trajectory': [
        'Which {conds} have a worsening {lab} trend?', 'Who is likely to cross {lab} thresholds soon?',
        'Which patients have declining {lab}?', 'Find patients whose {lab} is trending the wrong way',
        'whose {lab} is getting worse over time', 'Which patients will probably hit a dangerous {lab} in the next few months?',
        'Flag {conds} with a sharp change in {lab}', 'Show patients with a change point in {lab}',
        'which members have rising {lab} trajectories', 'Forecast each patient\'s {lab} and show who is heading out of range',
        'Patients whose kidney function is falling fastest', 'Who among {conds} has steadily climbing {lab}?',
    ],
    'survival': [
        'How long until {event} after discharge for {conds}?', 'Time to {event} after a hospital stay, by {group}',
        'Kaplan-Meier curve of time to {event} for {conds}', 'median days to {event} after discharge',
        'How fast do {conds} have {event} after diagnosis?', 'Compare time to {event} between {conds} and everyone else',
        'Hazard ratios for {event} by {group}', 'What share of {conds} have {event} within {days} days of discharge, as a curve',
        'time-to-event analysis for {event} after an ED visit', 'how quickly after diagnosis do {conds} reach {lab} below 30',
        'Days from discharge until first follow-up visit by {group}', 'Survival curve for {event}-free time after discharge',
        'Cox model of time to {event} with age and sex', 'How soon after leaving the hospital do patients come back?',
    ],
    'vbc': [
        'What is our PMPM for {conds}?', 'Calculate member months and PMPM by {group}', 'Shared savings settlement estimate for this year',
        'What is the risk of missing our shared savings target?', 'Utilization per 1,000 for {events}', 'Attributed members by practice',
        'How did total cost of care change and what drove it?', 'Quality measure performance for A1c control in the contract',
        'Fair split of shared savings across practices', 'What is our value at risk on the shared savings contract?',
        'Decompose the change in PMPM year over year', 'admits per 1000 and PMPM for the attributed population',
        'what does the shared-savings math look like', 'Average risk score of attributed members by payer',
        # one settlement under one stated contract stays vbc (strategy across contracts is contract_strategy)
        'Settlement payment under MSSP BASIC level {level} with a ${bench} benchmark', 'What do we receive in the ENHANCED track at a ${bench} PMPM benchmark?',
        'shared savings owed to us under ACO REACH Professional with a ${bench} benchmark',
    ],
    'program_effect': [
        'Did {program} reduce {events}?', 'Did enrollment in {program} lower PMPM?', 'Effect of {program} on {events}',
        'Did starting {drug} change {lab}?', 'Does {program} work?', 'Impact of {program} on hospital use',
        'Did patients in {program} have fewer {events} afterwards?', 'measure the effect of starting {drug} on {events}',
        'Has {program} paid off in lower costs?', 'Did {program} improve {lab} for {conds}?',
        'What happened to {events} after people joined {program}?', 'Is {program} making a difference for {conds}?',
    ],
    'outreach': [
        'Who should our care managers call this week?', 'Build a prioritized list of {n} patients for outreach',
        'We have {n} call slots, who should we contact?', 'Which {conds} should nurses reach out to first?',
        'Give me an outreach worklist for {conds} with care gaps', 'Prioritize {n} members for care management outreach',
        'Who are the top patients to contact about rising risk?', 'make a call list ranked by expected benefit',
        'Our team has {n} hours, who should they call?', 'Which patients with {need} should we reach out to?',
        'rank patients for nurse follow-up calls', 'who should the care team prioritise for outreach today',
    ],
    'calibration': [
        'Backtest the trajectory forecasts',
        'Brier score and calibration of the readmission risk model', 'Are our predictions reliable?',
        'Show the reliability table for our risk predictions', 'how trustworthy are the outreach risk estimates',
        'Check coverage of the prediction intervals', 'Is the risk model overconfident?', 'expected calibration error of our forecasts',
    ],
    'monitor': [
        'Is anything out of control this month?', 'Control chart of {events} rate',
        'Did any practice\'s {events} rate spike beyond normal variation?', 
        'Has the denial rate shifted?', 'Any anomalies in {feed} data volume?', 'Run a Benford check on claims amounts',
        'SPC chart for PMPM', 'Are there duplicate claims or peer outliers?', 'Is the {feed} feed down?',
        'any special-cause variation in {events}', 'did lab volume drop unexpectedly',
    ],
    'market': [
        'Show the markets view of practices', 'Which PCPs moved the most on {metric}?',
        'Ticker view for each practice', 'Which payers changed the most versus baseline?', 'What changed most in {metric} versus last month?',
        'biggest movers by practice this month', 'network-wide versus single-practice moves in {metric}',
        'Rank practice moves by surprise and impact', 'which clinics are trending up on {metric} relative to the network',
    ],
    'demand': [
        'Forecast {events} per {grain} for {horizon}', 'Project admissions for {horizon}',
        'Predict appointment volume per {grain}', 'Staffing needed for expected ED volume {horizon}',
        'What will ED demand look like {horizon}?', 'Expected discharges per {grain} with intervals',
        'how many bed-days should we plan for {horizon}', 'forecast weekly ED arrivals', 'anticipated admission volume by {grain}',
    ],
    'profile': [
        'Which {unit} are truly high on {metric} after case mix?', 'Fair comparison of {unit} on {metric}',
        'Rank {unit} on {metric} adjusting for panel size', 'Which PCPs are outliers on {metric} once you account for their patients?',
        'Compare practices fairly on readmissions', 'Shrunk rates of {metric} by PCP', 'Which doctors really have high {metric}?',
        'risk-adjusted ranking of {unit} for {metric}', 'are any PCPs genuinely worse on {metric}',
    ],
    'pathway': [
        'Where do patients go after an ED visit?', 'Most common paths after discharge', 'What happens next after a PCP visit?',
        'Patient journeys after hospital discharge', 'Follow-up within {days} days versus ED return after discharge',
        'How much leakage to outside organizations after discharge?', 'Chance of admission within three steps after an ED visit',
        'care pathway after an ED visit for {conds}', 'what sequence of care do {conds} follow after a hospitalization',
        'map the patient flow after discharge', 'next steps after an urgent care visit',
    ],
    'network': [
        'Map our provider network', 'which doctors act as brokers between our practices', 'most influential physicians in the referral graph',
        'Which outside groups pull the most of our members?', 'how much of our cardiology volume goes out of network',
        'which hospitals and specialty groups do {conds} end up using', 'Do our primary care doctors see each other\'s patients?',
        'care density for {conds} by practice', 'how fragmented are care teams for {conds}', 'clusters of physicians who work together',
        'how concentrated are each PCP\'s specialist referrals', 'repatriate out-of-network {group} visits: what would we gain?',
        'what if we moved half of the leaked orthopedic visits to our own group', 'who should champion a new protocol so it reaches the most PCPs',
        'simulate adoption of a care guideline across the physician network', 'betweenness and PageRank of our clinicians',
        'pairs of primary care physicians with overlapping panels', 'redirect {group} referrals to our preferred specialists: visits and dollars regained',
        'diffusion model of a new prescribing habit among PCPs', 'patient-sharing ties between our doctors',
        'which PCPs are connected through common specialists',
    ],
    'briefing': [
        'Morning briefing', 'Give me my daily briefing',
        'anything I should know about this morning', 'what are the most important things this week',
        'top findings I should review today', 'what deserves my attention right now',
    ],
    'scheduling': [
        'How much should we overbook each clinic?', 'Which appointments {when} are likely no-shows?',
        'Who should get a reminder call before their appointment {when}?', 'How many extra slots can each practice book?',
        'Overbooking recommendation per session', 'Predict no-shows for {when}', 'which upcoming visits are at risk of being missed',
        'booking lead time and access by practice', 'safe overbooking level for each clinic session',
    ],
    'early_signal': [
        'Which patients show the pattern that usually precedes an admission?', 'Who is heading toward decompensation?',
        'Find {conds} with a pre-admission signature', 'Early warning: who looks like they are on the road to an ED visit?',
        'Which patients look like past patients right before they were hospitalized?', 'pre-event signature matching for {conds}',
        'who is showing early warning signs of an admission', 'patients whose recent pattern resembles the run-up to an ED visit',
    ],
    'claims_review': [
        'Which providers bill unusually compared with peers?', 'Where are we leaking revenue?', 'Which claims were underpaid?',
        'Denial patterns by payer and reason', 'Suspicious billing patterns', 'How much money are we losing to denial write-offs?',
        'Payment integrity review flags ranked by dollars', 'Which claims were paid less than comparable claims?',
        'how much do takebacks and rework cost us', 'root causes of denials in dollars',
    ],
    'knowledge': [
        'How does the Medicare Shared Savings Program decide which beneficiaries belong to an ACO?', 'What is a High Needs ACO in REACH?',
        'Explain prospective versus retrospective assignment', 'What is the minimum savings rate?', 'How do MA plans pay primary care groups under capitation?',
        'What changed between CMS-HCC V24 and V28?', 'Why does coding intensity matter in Medicare Advantage?', 'What is a quality withhold?',
        'How does Pearl Health work with independent practices?', 'Who acquired Signify Health and why?', 'How is Privia different from Aledade?',
        'What are integrated practice units?', 'What did the Next Generation ACO evaluation find?', 'Is there evidence that ACOs save money?',
        'Describe the Kidney Care Choices model', 'What is the ratchet problem in ACO benchmarks?', 'what does two-sided risk mean',
        'Explain the HEDIS hybrid method', 'What is the Triple Aim?', 'Give me an overview of Medicaid managed care auto-assignment',
    ],
    # answerable once ICD-10 arrives on claims (warehouse v2.0); on a build without it the compiler answers not_answerable
    'risk_adjustment': [
        'Average HCC RAF score', 'RAF scores by PCP', 'Which HCCs have not been recaptured this year?', 'Open HCC recapture gaps',
        'Suspect conditions for {conds} without a diagnosis code', 'What is our coding completeness?', 'CMS-HCC risk score by practice',
        'documentation gaps worth reviewing', 'V24 versus V28 risk scores', 'coding gaps in dollars at our Medicare Advantage base rate',
        'patients whose labs suggest an HCC that was never coded', 'HCC suspects to review for documentation', 'uncoded suspected conditions by PCP',
        'which {conds} have evidence of a condition not yet coded this year', 'suspect list for {cond} coding review',
    ],
    # risk-contract strategy (options valuation, track selection, negotiation); one settlement amount stays vbc
    'contract_strategy': [
        'Should our ACO switch to the ENHANCED track if the benchmark is ${bench} PMPM?', 'Is BASIC level {level} or ENHANCED the smarter track for us with a ${bench} benchmark',
        'Which ACO REACH option should we pick with a ${bench} PMPM benchmark?', 'How much is two-sided risk in ENHANCED worth to us at a ${bench} benchmark?',
        'What is the option value of our upside under MSSP BASIC level {level} with a ${bench} PMPM benchmark?', 'Value of the downside we take on in ENHANCED with a ${bench} benchmark',
        'What MSR should we push for with a ${bench} PMPM benchmark?', 'What would a fair sharing rate be in negotiation at a ${bench} benchmark?',
        'Break-even sharing rate if care management costs us ${spend} PMPM and the benchmark is ${bench}',
        'How much loss can we absorb at 90% confidence under BASIC level {level} with a ${bench} benchmark?',
        'compare offer A: 50% sharing upside only; offer B: 70% sharing two-sided, with a ${bench} PMPM benchmark', 'which ACO REACH risk corridor is right for us at a ${bench} benchmark',
        'Should we step up from level {level} to two-sided risk given a ${bench} benchmark?', 'sensitivity of our payout to the benchmark under REACH Professional with a ${bench} benchmark',
    ],
    # comparative effectiveness of two drug strategies (target trial emulation); one drug start with no comparator stays program_effect
    'target_trial': [
        'Do {arm} slow {tlab} decline compared with {arm}?', '{arm} versus {arm}: which works better on {tlab}?',
        'Comparative effectiveness of {arm} vs {arm} for {events}', 'Emulate a target trial of {arm} compared with {arm} on {tlab}',
        'new users of {arm} or {arm}: who has fewer {events}?', 'Is starting {arm} rather than {arm} associated with lower {tlab}?',
        'head-to-head {arm} vs {arm} on the risk of {event}', 'active comparator new-user analysis of {arm} and {arm} on {tlab}',
        'high-intensity versus moderate-intensity statins and LDL', 'does statin intensity change LDL in new users',
        'are patients started on {arm} less likely to have {events} than those started on {arm}?',
        'effect of {arm} compared to {arm} on {tlab} among {conds}',
    ],
    # treatment patterns from fills
    'line_of_therapy': [
        'Lines of therapy for {area}', 'What are the first- and second-line regimens in {area}?', 'Treatment patterns for {area} patients',
        'How often do patients switch from {arm} to another class in {area}?', 'Most common treatment sequences in {area}',
        'Persistence on first-line {area} therapy', 'Switching matrix between drug classes for {area}', 'Sankey of {area} treatment sequences',
        'Time on treatment for each line of {area} therapy', 'Do {area} regimens get add-on drugs or switches?',
        'discontinuation of first-line {area} medications', 'what regimen do {area} patients move to after first line',
    ],
    # trial eligibility funnels over stated criteria
    'trial_feasibility': [
        'How many patients are eligible for a {tcond} trial with {lab} above {n}?', 'Eligibility funnel for our {tcond} study requiring {lab} below {n}',
        'Trial feasibility: adults with {tcond} and {lab} between {n} and 200', 'Which criteria remove the most patients for a {tcond} trial with {lab} over {n}?',
        'Which sites see the most eligible patients for a {tcond} trial?', 'Expected enrollment per month for a {tcond} study of adults on {arm}',
        'Diversity by race and ethnicity of patients eligible for our {tcond} trial with {lab} above {n}',
        'how many {conds} qualify for the study if we exclude patients on {arm}', 'Recruitment funnel for a {tcond} trial: age 40 to 75, {lab} under {n}',
        'screening counts for a {tcond} trial requiring {lab} of at least {n} in the last 6 months',
    ],
    'loop_closure': [
        'abnormal {lab} results nobody followed up on', 'which {lab} results are still waiting on a clinician response',
        'Open loops for {conds}', 'unacknowledged critical values in the last {days} days', 'referrals that never got completed for {conds}',
        'Which specialist consults never came back?', 'lab orders that were never resulted at each {group}', 'pending results older than {days} days with no action',
        'incidental findings with no follow-up imaging', 'positive stool tests without a colonoscopy', 'BI-RADS 0 mammograms lacking a diagnostic work-up',
        'how well does each {group} close the loop on abnormal results', 'Which recommendations from radiology reports were ignored?',
        'results that slipped through the cracks for {conds}', 'track closure of referrals by ordering doctor', 'what share of abnormal tests got a documented response',
    ],
    'diagnostic_delay': [
        'How late are we diagnosing {cond}?', 'missed chances to diagnose {cond} at earlier visits', 'look back before new {cond} diagnoses for warning signs',
        'patients with labs pointing to {cond} who were never diagnosed', 'possible undiagnosed {cond} in our panel', 'diagnostic safety: delayed {cond} detection by {group}',
        'How many visits happened between the first abnormal {lab} and the {cond} diagnosis?', 'time from first qualifying {lab} to a {cond} diagnosis',
        'SPADE analysis for missed stroke after ED discharge', 'ED patients sent home who came back with a heart attack within {days} days',
        'signs of colon cancer that were there before the diagnosis', 'diagnostic safety review: {cond} criteria met but the diagnosis was missed',
        'were there red flags before our new colorectal cancer cases', 'diagnostic delay distribution for {cond}',
    ],
    'risk_outcomes': [
        'risk standardized {events} by hospital', 'RSRR by {group}', 'observed versus expected readmissions for each hospital',
        'O/E ratio for {events} across our facilities', 'excess readmission ratio for {conds}', 'HRRP style readmission ratio by hospital',
        'funnel chart of {events} by {group}', 'Which hospitals are statistically worse than our network on 30-day readmissions after risk adjustment?',
        'case-mix adjusted ED revisit rate per hospital', 'risk-adjusted length of stay by facility', 'standardized readmission ratio with confidence intervals',
        'expected vs observed ED returns within {days} days by hospital', 'CMS-style readmission measure for our hospitals',
        'which facility has the highest risk-standardized readmission rate', 'ED bounce-back rates by hospital after case-mix adjustment',
        'facility comparison of emergency returns adjusted for risk', 'hospital-level risk-adjusted revisits within 72 hours',
    ],
    'not_answerable': [
        'How many patients died last year?', 'Mortality rate for {conds}', 'Breakdown of patients by race and ethnicity',
        'Total RVUs per physician', 'Which CPT codes are billed most?', 'patient satisfaction survey results',
        'HCAHPS scores by practice', 'How many patients are still alive?', 'death rate after discharge', 'What is each patient\'s income?',
        'Show the CAHPS survey results', 'how many deceased members', 'wRVU productivity by provider',
    ],
}


def generate(per_template=6, seed=7):
    """-> rows {question, intent, template}. Fillers are sampled; duplicates dropped."""
    rng = random.Random(seed)
    rows, seen = [], set()
    for label, templates in T.items():
        for t_index, template in enumerate(templates):
            for _ in range(per_template * 3):
                if sum(r['template'] == f'{label}:{t_index}' for r in rows) >= per_template: break
                values = {}
                text = template
                for key, options in FILL.items():
                    while '{' + key + '}' in text:
                        text = text.replace('{' + key + '}', rng.choice(options), 1)
                variants = [text, text.lower(), text.rstrip('?'), 'Can you tell me: ' + text[0].lower() + text[1:]]
                text = rng.choice(variants)
                if text.casefold() in seen: continue
                seen.add(text.casefold())
                rows.append({'question': text, 'intent': label, 'template': f'{label}:{t_index}'})
    return rows
