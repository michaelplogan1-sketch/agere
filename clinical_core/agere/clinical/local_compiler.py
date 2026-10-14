"""Local question compiler: English question -> executable graph, no model call.

Agere's own understanding comes first; a hosted LLM is the fallback. The
compiler reads the question against
  - the concept library (conditions, measurements, encounter types, aliases),
  - the received data dictionary (every stream, field and observed value),
  - a small grammar of comparisons, negation, demographics, payer, social-screen
    flags, grouping, statistics, adjustment and time windows.
Every word that carries meaning must be explained by something it recognised.
If anything substantive is left over, it declines and reports exactly which
words it could not interpret, so the fallback is explicit and measurable.
"""
import re
from collections import Counter, defaultdict

from . import concepts, source_profile

VERSION = 'local-question-compiler/1'
W = {'basis': 'all_observed', 'start': '', 'end': ''}

# ---- vocabulary -----------------------------------------------------------------
CONDITION_ALIASES = {
    'diabetes': ['diabetes', 'diabetics', 'diabetic', 'diabetes mellitus', 'dm', 'diabetic-complication', 'diabetic complication'],
    'type_2_diabetes': ['type 2 diabetes', 't2dm', 'type ii diabetes'],
    'type_1_diabetes': ['type 1 diabetes', 't1dm', 'type i diabetes'],
    'prediabetes': ['prediabetes', 'pre-diabetes', 'prediabetic'],
    'hypertension': ['hypertension', 'htn', 'high blood pressure', 'hypertensives', 'hypertensive'],
    'heart_failure': ['heart failure', 'chf', 'hf', 'congestive heart failure'],
    'heart_failure_reduced_ef': ['hfref', 'systolic heart failure', 'reduced ejection fraction'],
    'chronic_kidney_disease': ['chronic kidney disease', 'ckd', 'kidney disease', 'renal disease'],
    'copd': ['copd', 'chronic obstructive pulmonary disease', 'emphysema', 'chronic obstructive bronchitis', 'chronic bronchitis', 'chronic obstructive lung disease'],
    'asthma': ['asthma', 'asthmatics', 'asthmatic'],
    'atrial_fibrillation': ['atrial fibrillation', 'afib', 'a-fib'],
    'coronary_artery_disease': ['coronary artery disease', 'cad', 'ischemic heart disease'],
    'myocardial_infarction': ['myocardial infarction', 'heart attack', 'mi'],
    'stroke': ['stroke', 'cerebral infarction'],
    'obesity': ['obesity', 'obese'],
    'hyperlipidemia': ['hyperlipidemia', 'dyslipidemia', 'high cholesterol'],
    'depression': ['depression', 'depressive disorder', 'mdd'],
    'anxiety': ['anxiety'],
    'substance_use_disorder': ['substance use disorder', 'substance use', 'alcohol use disorder', 'opioid use disorder'],
    'dementia': ['dementia', 'alzheimer', "alzheimer's"],
    'cancer': ['cancer', 'malignancy', 'malignant neoplasm'],
    'sepsis': ['sepsis'],
    'anemia': ['anemia', 'anaemia'],
    'hypothyroidism': ['hypothyroidism'],
    'osteoporosis': ['osteoporosis'],
    'sleep_apnea': ['sleep apnea'],
    'hiv': ['hiv'],
}
MEASUREMENT_ALIASES = {
    'hba1c': ['hba1c', 'a1c', 'hemoglobin a1c', 'glycated hemoglobin'],
    'glucose': ['glucose', 'blood sugar'], 'ldl': ['ldl', 'ldl cholesterol'], 'hdl': ['hdl', 'hdl cholesterol'],
    'total_cholesterol': ['total cholesterol', 'cholesterol'], 'triglycerides': ['triglycerides', 'triglyceride'],
    'egfr': ['egfr', 'gfr', 'estimated gfr'], 'creatinine': ['creatinine'], 'potassium': ['potassium'],
    'sodium': ['sodium'], 'bun': ['bun', 'urea nitrogen'], 'hemoglobin': ['hemoglobin', 'hgb'],
    'platelets': ['platelets', 'platelet count'], 'wbc': ['wbc', 'white blood cell', 'white count'],
    'uacr': ['uacr', 'albumin/creatinine', 'urine albumin', 'albumin-to-creatinine ratio', 'albumin to creatinine ratio',
             'urine albumin-to-creatinine ratio', 'urine albumin to creatinine ratio', 'albumin/creatinine ratio', 'urine albumin/creatinine ratio'], 'nt_probnp': ['nt-probnp', 'ntprobnp', 'bnp'],
    'troponin': ['troponin'], 'tsh': ['tsh'], 'ferritin': ['ferritin'], 'phq9': ['phq-9', 'phq9'],
    'systolic_bp': ['systolic', 'systolic blood pressure', 'systolic bp'],
    'diastolic_bp': ['diastolic', 'diastolic blood pressure', 'diastolic bp'],
    'heart_rate': ['heart rate', 'pulse'], 'weight': ['weight'], 'spo2': ['spo2', 'oxygen saturation'],
}
MEASUREMENT_ALIAS_ORDER = [(c, [a]) for c, a in sorted(((c, a) for c, al in MEASUREMENT_ALIASES.items() for a in al), key=lambda ca: -len(ca[1]))]
MEASUREMENT_RANK = {c: i for i, c in enumerate(MEASUREMENT_ALIASES)}
ENCOUNTER_ALIASES = {
    'emergency_visit': ['emergency department visits', 'emergency department visit', 'emergency department',
                        'ed visits', 'ed visit', 'er visits', 'er visit', 'emergency visits', 'emergency visit', 'ed use', 'ed utilization'],
    'inpatient_admission': ['inpatient admissions', 'inpatient admission', 'hospitalizations', 'hospitalization',
                            'hospital admissions', 'admissions', 'inpatient stays'],
    'observation_stay': ['observation stays', 'observation stay'],
    'urgent_care_visit': ['urgent care visits', 'urgent care'],
    'primary_care_visit': ['primary care visits', 'primary care visit', 'pcp visits', 'office visits', 'wellness visits'],
    'specialist_visit': ['specialist visits', 'specialty visits'],
    'telehealth_contact': ['telehealth', 'telephone visits', 'virtual visits'],
}
SOCIAL_PHRASES = [  # regex -> (stream, field)
    (r'transportation (?:is |was )?(?:a |an )?(?:problem|issue|barrier|difficult\w*)', ('social_screens', 'transport_difficulty_reported')),
    (r'(?:trouble|difficulty|problems?) paying (?:their |his |her )?(?:medical )?bills', ('financial_screens', 'difficulty_paying_medical_bills')),
    (r'(?:trouble|difficulty) affording (?:care|food)', ('social_screens', 'difficulty_paying_for_care_reported')),
]
# The same need recorded by another instrument (plain-language questions match either).
NEED_INSTRUMENTS = {'transport_difficulty_reported': [('social_screens', 'transport', 'unreliable')],
                    'difficulty_paying_for_care_reported': [('financial_screens', 'difficulty_paying_medical_bills', True)],
                    'difficulty_paying_medical_bills': [('social_screens', 'difficulty_paying_for_care_reported', True)]}
SOCIAL_FLAGS = {  # phrase -> (stream, field)
    'food difficulty': ('social_screens', 'food_difficulty_reported'),
    'food insecurity': ('social_screens', 'food_difficulty_reported'),
    'transport difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation barrier': ('social_screens', 'transport_difficulty_reported'),
    'transportation barriers': ('social_screens', 'transport_difficulty_reported'),
    'transport problem': ('social_screens', 'transport_difficulty_reported'),
    'financial strain': ('social_screens', 'difficulty_paying_for_care_reported'),
    'trouble paying for care': ('social_screens', 'difficulty_paying_for_care_reported'),
    'transportation problems': ('social_screens', 'transport_difficulty_reported'),
    'difficulty paying for care': ('social_screens', 'difficulty_paying_for_care_reported'),
    'financial hardship': ('social_screens', 'difficulty_paying_for_care_reported'),
    'difficulty paying medical bills': ('financial_screens', 'difficulty_paying_medical_bills'),
}
PAYERS = {'medicare': 'medicare', 'medicaid': 'medicaid', 'commercial': 'commercial', 'commercially insured': 'commercial',
          'uninsured': 'uninsured'}
GROUPS = [(r'\b(?:by|for each|per|each) (?:assigned |attributed )?(?:pcp(?:_clinician_id| clinician)?|primary care (?:provider|physician|clinician)|physician|provider|clinician)s?\b', 'pcp'),
          (r'\b(?:by|for each|per|each) (?:attributed )?(?:practice|clinic)(?:_id)?s?\b', 'practice'),
          (r'\bby (?:hospital|facility|facilities|site)s?\b', 'facility'),
          (r'\bby (?:payer|payor|coverage(?: type)?|insurance(?: type)?|plan type)\b', 'coverage_type'),
          (r'\bby age(?: band| group)?s?\b', 'age_band'), (r'\bby (?:sex|gender)\b', 'sex'),
          (r'\b(?:by|per|each|for each|every) (?:calendar )?months?\b|\bmonthly\b|\bmonth[- ]by[- ]month\b', 'month'),
          (r'\b(?:by|per|each|for each|every) (?:calendar )?quarters?\b|\bquarterly\b', 'quarter'),
          (r'\b(?:by|per|each|for each|every) (?:calendar )?years?\b|\byearly\b|\bannually\b|\byear[- ]over[- ]year\b', 'year')]
TIME_GROUPS = ('month', 'quarter', 'year')
COMPARATORS = [(r'(?:at or above|at or over|at least|no less than|>=|≥)', 'ge'), (r'(?:at or below|at or under|at most|no more than|<=|≤)', 'le'),
               (r'(?:over|above|greater than|more than|higher than|exceed(?:ed|s|ing)?|>)', 'gt'),
               (r'(?:below|under|less than|lower than|<)', 'lt'), (r'(?:equal to|exactly|=)', 'eq')]
NUMBER = r'(-?\d+(?:\.\d+)?)'
# Demographic and coding words whose answerability depends on the release (v2.0 adds race/ethnicity, language,
# interpreter need and CPT/HCPCS): the refusal applies only when the source profile shows the field is not received.
RACE_WORDS = (r'\brace\b|\braces\b|\bethnicit(?:y|ies)\b|\bracial\b|\bhispanic\b|\blatin[oax]\b|\b(?:black|white|asian|native american|pacific islander|'
              r'african american)(?: (?:and|or|vs\.?|versus) (?:black|white|asian|hispanic|latin[oax]))? (?:patients|members|people|population|adults|women|men)\b|'
              r'\bhealth equity stratif\w+\b|\bequity stratif\w+\b')
LANGUAGE_WORDS = (r'\binterpreters?\b|\bpreferred languages?\b|\blanguage preferences?\b|\blimited english\b|\blep\b|\bnon[- ]english\b|'
                  r'\b(?:by|per|for each) (?:primary |spoken )?language\b|\blanguage other than english\b|'
                  r'\b(?:english|spanish|portuguese|chinese|mandarin|cantonese|vietnamese|korean|khmer|haitian(?: creole)?|creole|russian|arabic)[- ]speak\w*\b|'
                  r'\bspeak(?:s|ing)? (?:english|spanish|portuguese|chinese|mandarin|cantonese|vietnamese|korean|khmer|haitian(?: creole)?|creole|russian|arabic)\b')
# Category words name a subgroup (a filter); only group-by stratification is answered locally, so these go to the router.
DEMOGRAPHIC_CATEGORY = (r'\bhispanic\b(?! ethnicity)|\blatin[oax]\b|\b(?:black|white|asian|native american|pacific islander|african american)\b|'
                        r'\b(?:english|spanish|portuguese|chinese|mandarin|cantonese|vietnamese|korean|khmer|haitian|creole|russian|arabic)\b|'
                        r'\bnon[- ]english\b|\blimited english\b|\blep\b|\bother than english\b|\bneed(?:s|ing|ed)? an interpreter\b')
CPT_WORDS = r'\bcpt(?:/hcpcs)?\b|\bhcpcs\b'
DEMOGRAPHIC_GROUPS = ('race', 'ethnicity', 'race_ethnicity', 'language', 'interpreter')
GROUPS_DEMOGRAPHIC = [  # only offered when the source profile carries the field
    (r'\b(?:by|across|per|for each|stratified by|stratify(?:ing)? by|broken (?:down|out) by) race(?:/| and | or |, )ethnicity\b|'
     r'\b(?:by|across|stratified by) racial(?: and| or|/) ethnic (?:groups?|categor\w+)\b', 'race_ethnicity'),
    (r'\b(?:by|across|per|for each|stratified by|stratify(?:ing)? by|broken (?:down|out) by) (?:race|racial (?:groups?|categor\w+))\b', 'race'),
    (r'\b(?:by|across|per|for each|stratified by|stratify(?:ing)? by|broken (?:down|out) by) (?:ethnicity|ethnic (?:groups?|categor\w+)|hispanic ethnicity)\b', 'ethnicity'),
    (r'\b(?:by|across|per|for each|stratified by|broken (?:down|out) by) (?:preferred |primary |spoken )?language\b', 'language'),
    (r'\b(?:race|racial) (?:breakdown|mix|distribution|split|makeup|composition)\b', 'race'),
    (r'\bethnic(?:ity)? (?:breakdown|mix|distribution|split|makeup|composition)\b', 'ethnicity'),
    (r'\b(?:preferred )?language (?:breakdown|mix|distribution|split)\b', 'language'),
    (r'\b(?:by|across|stratified by|broken (?:down|out) by) (?:interpreter (?:need(?:ed)?|use|status)|whether (?:they|members|patients) need an interpreter)\b', 'interpreter')]


def _patient_fields(profile, *fields):
    """True when the release populates any of these patient fields (source profile, read-only)."""
    spec = ((profile or {}).get('patients') or {}).get('fields') or {}
    return any((spec.get(f) or {}).get('populated') for f in fields)


def _race_received(profile): return _patient_fields(profile, 'race', 'ethnicity')
def _language_received(profile): return _patient_fields(profile, 'preferred_language', 'interpreter_needed')


def _cpt_received(profile):
    for s in ('medical_claim_lines', 'medical_claims'):
        values = ((((profile or {}).get('streams') or {}).get(s) or {}).get('fields') or {}).get('service_code_system', {}).get('values') or {}
        if any(v in ('CPT', 'HCPCS') for v in values): return True
    return False


def _demographic_groups(profile):
    """Group-by phrasings the release can answer: race/ethnicity and language only when received."""
    ok = {'race': _race_received(profile), 'ethnicity': _race_received(profile), 'race_ethnicity': _race_received(profile),
          'language': _language_received(profile), 'interpreter': _language_received(profile)}
    return [(rx, g) for rx, g in GROUPS_DEMOGRAPHIC if ok[g]]


UNSUPPORTED = [
    (r'\bcausal(?:ly)?\b|\bcaus(?:ed|ally) (?:by|prevented)|\bprevented by\b|\bidentified causal effect\b|\battributable\b|'
     # "do they have lower AWV completion" compares groups; it is not a claim that something lowered something
     r'\b(?:did|does|do) [a-z -]{3,60} (?<!have )(?<!has )(?<!had )(?:reduce|lower|cut|prevent|improve|increase|decrease|drive down)\b|\bimpact of\b|\beffect of\b',
     ['identified causal design or randomised comparison', 'counterfactual outcome model', 'validated outcome attribution method']),
    (r'\b(?:externally )?validated,? calibrated\b|\bcalibrated probability\b',
     ['externally validated prediction model', 'calibration data for this population']),
    (r'\bcapitation\b|\bcontract(?:ed)? rate\b|\bmedicare advantage contract\b|\bmla\b|\bpremium revenue\b|\bpercent[- ]of[- ]premium\b|\bmedical margin\b',
     ['executed payer contract with capitation/premium rates', 'member-month eligibility under the contract', 'risk-adjusted revenue schedule']),
    # CMS's own figures (reports, assignment lists, its final settlement) are not received: before the settlement rule, which
    # would otherwise ask for contract terms that cannot reproduce CMS's numbers
    (r"\bcms(?:'s|') (?:[a-z-]+ ){0,2}(?:reports?|settlement|reconciliation|results?|figures?|numbers?|calculation)\b|"
     r"\bcms (?:quarterly |annual |program |settlement |benchmark |financial )?reports?\b|"
     r"\b(?:quarterly|annual) (?:assignment |benchmark |financial |program )?reports? (?:from|by|issued by) cms\b|\b(?:cms )?(?:quarterly )?assignment lists?\b",
     ['CMS ACO program reports, assignment lists and settlement results (not received; Agere computes PMPM, attribution and utilization '
      'from received claims and records, so a reconciliation to CMS figures needs the CMS report itself)']),
    (r'\bshared[- ]savings\b|\bpayer contract\b|\bsettlement\b|\bshared[- ]loss(?:es)?\b|\bloss threshold\b|\b(?:exceed|meet|clear|hit) (?:the |our )?msr\b|'
     r'\b(?:projected|expected|estimated) savings rate\b|\bsavings (?:we|they) (?:earned|earn)\b|\b(?:savings )?distribution (?:model|methodology|method)\b|'
     r'\bcontract performance\b',
     ['executed payer contract terms', 'attribution roster under the contract', 'benchmark/baseline expenditure',
      'quality score and sharing-rate schedule']),
    (r'\bobjectively\b|\bwearable\b|\bactivity tracker\b|\baccelerometer\b|\bminutes of (?:moderate|vigorous) exercise\b',
     ['device or objectively measured activity data (none is received)']),
    # 'survives shrinkage' is not mortality: only the vital-status senses of survive count
    (r"\b(?:died|deaths?|mortality|deceased|passed away|expired|vital status|survival|survivors?|survived|surviving|survive|"
     r"didn'?t make it|did not make it|still alive)\b",
     ['death or mortality records (none are received)']),
    (RACE_WORDS, ['race/ethnicity fields (none are received)']),
    (r'\braf(?: scores?)?\b|\b(?:cms-)?hccs? (?:scores?|codes?|coding|capture|gaps?|risk|categor\w+|weights?)\b|\brisk adjustment factors?\b|\brisk[- ]adjusted revenue\b',
     ['CMS-HCC risk adjustment factors (no HCC mapping or RAF scores are received; Agere has only an internal cost-risk score)']),
    (r'\bcahps\b|\bpatient experience scores?\b|\bsatisfaction scores?\b', ['patient experience survey results (none are received)']),
    (r'\bhousehold income\b|\bincome (?:level|bracket|distribution)s?\b|\bfpl\b|\bpoverty (?:level|line)\b|\bincome\b',
     ['household income / federal poverty level (not disclosed in received records)']),
    (LANGUAGE_WORDS, ['preferred language / interpreter need (not received)']),
    (r'\bsexual orientation\b|\bgender identity\b|\bsogi\b|\blgbtq?\+?\b', ['sexual orientation / gender identity (not received)']),
    (r'\bw?rvus?\b|\brelative value units?\b', ['RVU weights (no relative value unit table is received; Agere cannot weight services by RVU)']),
    (CPT_WORDS, ['CPT/HCPCS procedure codes (claims carry local service codes only)']),
    (r'\b(?:area |neighbou?rhood |community )?deprivation(?: index| indices| scores?| levels?)?\b|\badi\b|\bsocial vulnerability index\b|\bsvi\b',
     ['area-level deprivation index (ADI/SVI) linked to patient addresses (not received)']),
    (r'\bdrive[- ]?times?\b|\bdriving (?:times?|distances?)\b|\btravel (?:times?|distances?)\b',
     ['facility locations and drive-time/travel-distance data (not received)']),
    (r'\b(?:medicare )?(?:enrollment|entitlement|eligibility) (?:types?|categor(?:y|ies)|status(?:es)?|reasons?)\b|'
     r'\bdual[- ]?(?:eligib\w*|status)\b|\bduals\b|\baged/(?:non-?)?dual\b|\blow[- ]income subsidy\b|\blis status\b|\borec\b|'
     r'\boriginal reason for entitlement\b|\besrd (?:status|beneficiar\w+|enrollees?)\b',
     ['Medicare enrollment/entitlement status (ESRD, disabled, aged, dual/LIS) (not received; coverage records carry only '
      'commercial/medicaid/medicare/uninsured)']),
    (r'\bswallow(?:ed)?\b|\bactually (?:took|taken|swallowed)\b|\bverified independently\b',
     ['direct observation of medication ingestion (no received source records it)']),
]
SETTLEMENT_RULE = 'executed payer contract terms'
CAUSAL_RULE = 'identified causal design or randomised comparison'
# A number about this population (ours / how much / will we / $) makes it a data question even when phrased as "how does".
OUR_NUMBER = (r"\bhow (?:much|many)\b|\bwhat (?:is|are|was|were|will be|would be) (?:our|we)\b|\bours\b|\bour (?:projected|expected|estimated|final|current|actual)\b|"
              r"\$|\bif (?:the|our) benchmark\b|\bwill we\b|\bdo we (?:owe|earn|get|qualify)\b|\bare we (?:on track|likely|eligible|going)\b|"
              r"\bwhere do we stand\b|\bby (?:practice|pcp|payer|month|quarter)\b|\beach practice\b|\bprobabilit(?:y|ies)\b|\blikel(?:y|ihood)\b|"
              r"\b(?:this|next|last) (?:year|quarter|month)\b|\bproject(?:ed|ion)\b|\bwhat were\b|\b(?:19|20)\d\d\b|\bpy ?20\d\d\b|"
              r"\bour (?:aco|acos|contracts?|participation|performance|population|members|beneficiaries|patients)\b|"
              r"\bactual (?:spend|spending|costs?|pmpm|expenditures?|savings|results?)\b")


def _explanation_only(low):
    """Program mechanics asked as an explanation (how does X work / what is the difference / which expenditures are excluded),
    with no request for a number about this population."""
    asks = re.search(KNOWLEDGE_INTENT, low) or re.search(r'^which [a-z -]{2,40} (?:are|is) (?:excluded|included|counted|adjusted|capped)\b', low)
    return bool(asks and re.search(KNOWLEDGE_DOMAIN, low) and not re.search(OUR_NUMBER, low))


# A question about published research or how an external standard works ("what does the evidence say about ...",
# "how are work RVUs set", "what does the CDC SVI measure") is answered from the cited knowledge base even when the
# topic (mortality, RVUs, deprivation indices) is data this warehouse does not receive.
RESEARCH_QUESTION = (r"\bwhat (?:does|do) (?:the )?(?:evidence|research|literature|studies|trials?|data from studies) (?:say|show|suggest|find)\b|"
                     r"\bwhat (?:has|have) (?:research|studies|trials) (?:shown|found)\b|"
                     r"\bhow (?:are|is) [a-z0-9 /-]{2,60} (?:set|calculated|computed|defined|determined|derived|weighted)\b|"
                     r"\bwhat (?:does|do) (?:the )?[a-z0-9 /-]{2,60} (?:measure|capture|represent)\b")


def _research_only(low):
    return bool(re.search(RESEARCH_QUESTION, low) and not re.search(OUR_NUMBER, low))


def _knowledge_graph(question):
    from .vbc_knowledge import validate
    params = validate({'query': ' '.join(str(question).split())[:1500]})
    info = {'compiler': VERSION, 'recognised': ['explanation question about value-based care'], 'paraphrased': [],
            'route': 'local_compiler', 'unexplained': []}
    return {'interpretation': params['query'], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'knowledge', 'inputs': [], 'parameters': params,
                       'purpose': 'Cited explanation from the VBC knowledge base (public sources, extractive)'}]}, info
SETTLEMENT_ASK = ['program or contract terms: name the program and track (for example MSSP ENHANCED 2026, MSSP BASIC level E or ACO REACH) '
                  'so its cited rulebook terms apply, or state the contract terms (benchmark_pmpm and sharing_rate, plus MSR/MLR, '
                  'quality score and caps where they apply)',
                  'benchmark_pmpm (always contract-specific; a placeholder such as $X is not a value)']


# Settlement under the payer's ACTUAL contract also needs that contract's assigned-beneficiary list; our computed
# attribution is an estimate of it, not the roster the payer settles on.
ACTUAL_CONTRACT = r'\b(?:actual|executed|signed|real) (?:payer |aco |value-based )?contracts?\b'
ACTUAL_CONTRACT_ROSTER = ("attribution roster under the contract (the payer's assigned-beneficiary list; Agere's computed "
                          "attribution estimates it but is not the list the contract settles on)")


SETTLEMENT_ASK_INTENT = (r'\b(?:earn\w*|payments?|pay(?:s|out)? us|paid|owe\w*|savings|losses|settle\w*|bonus|receive|net|sensitiv\w*|'
                         r'distribut\w*|probability|msr|loss threshold|contract performance|projected)\b')


def _settlement_suppliable(low):
    """True when the settlement is computable once the user supplies a program/track or terms (no program named, or a
    named settlement program that only lacks its track or benchmark); False for reference material that has no terms."""
    named = _named_program(low)
    if named is None: return True
    from . import programs
    try: res = programs.resolve(named)
    except ValueError: return True  # e.g. a program option to choose: the user can supply it
    return bool(res['doc'].get('settlement') or res['doc'].get('choose'))


def _requires_input(q, asks, info):
    """The engine can answer once the user supplies these inputs: ask for them (the requires-input path), never guess."""
    info.update(route='local_requires_input', unexplained=[], requires_input=asks)
    return {'interpretation': f'Needs inputs not in the data: {q}', 'coverage': 'unsupported', 'nodes': [],
            'unmet_requirements': [f'Missing input: {m}' for m in asks]}, info


def _received_for(missing, profile):
    """A refusal whose input this release does receive no longer applies (the engines answer instead)."""
    head = missing[0]
    if head.startswith('CMS-HCC'): return _diagnoses_received(profile)  # ICD-10 on claims: risk_adjustment answers
    if head.startswith('race/ethnicity'): return _race_received(profile)
    if head.startswith('preferred language'): return _language_received(profile)
    if head.startswith('CPT/HCPCS'): return _cpt_received(profile)
    return False


def _refusal_text(missing, profile):
    """Name what is missing against what this release does carry."""
    head = missing[0]
    if head.startswith('RVU') and _cpt_received(profile):
        return ['RVU weights (no relative value unit table is received; this release carries CPT/HCPCS codes on claim lines, so service '
                'counts by code are answerable but RVU-weighted totals are not)']
    if head.startswith('area-level') and _patient_fields(profile, 'postal_code', 'county_fips'):
        return ['area-level deprivation index (ADI/SVI) (not received; patient ZIP code and county FIPS are received, but no index table '
                'to link them to)']
    return missing


def _refusal(q, low, profile, timed, terms, info):
    """UNSUPPORTED rules, data-aware: -> (graph, info) declining with the missing inputs named, or None.
    Settlement without terms asks for the program or terms (requires input) instead of a bare decline."""
    for pattern, missing in UNSUPPORTED:
        if timed and 'surviv' in pattern: continue
        if missing[0] == SETTLEMENT_RULE and terms: continue  # settlement is computable once terms are supplied
        if _received_for(missing, profile) or not re.search(pattern, low): continue
        if (missing[0] in (SETTLEMENT_RULE, CAUSAL_RULE) and _explanation_only(low)) or _research_only(low):
            # "how does the quality score scale our sharing rate?", "effect of the regional adjustment cap on our benchmark":
            # program mechanics asked as an explanation, answered from the cited knowledge base, never as a number
            return _knowledge_graph(q)
        if missing[0] == SETTLEMENT_RULE and re.search(SETTLEMENT_ASK_INTENT, low) and _settlement_suppliable(low):
            asks = _settlement_missing(low, SETTLEMENT_ASK)
            if re.search(ACTUAL_CONTRACT, low): asks = [*asks, ACTUAL_CONTRACT_ROSTER]
            return _requires_input(q, asks, info)
        if missing[0] == SETTLEMENT_RULE: missing = _settlement_missing(low, missing)  # reference material / no settlement asked
        info.update(route='local_unsupported', unexplained=[])
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': [f'Missing input: {m}' for m in _refusal_text(missing, profile)]}, info
    return None


FILLER = set("""a an the of in on at to for with and or any all each every this that these those is are was were be been
having has have had do does did which who whose what how many much number count list return give show tell me us our
patients patient people person persons members member distinct unique recorded observed documented received evidence
diagnosis diagnoses diagnosed result results value values record records explicitly explicit reported report reports
numeric lab labs test tests reading readings measurement measurements visit visits as of use set complete ids id
across either both regardless including include also only per there their they it its from than then one roster
original warehouse cohort population whole entire within during between date dates observation level levels percent
mmol mg dl ml min m2 1.73m2 g l units unit total among notification notifications whose where
it them so get got gets getting become became""".split())


OPERATOR_WORDS = set('''not no never without except excluding exclude over under above below more less least most fewer greater
higher lower exceed exceeded exceeds exceeding after before within since until between first last latest earliest same different
linked unlinked twice two three four five six per average mean median rate rates percent percentage share sum total max min
maximum minimum only neither nor nobody none missing lacks lack lacking january february march april june july august
september october november december jan feb mar apr jun jul aug sep sept oct nov dec monthly quarterly weekly
daily annually month months quarter quarters week weeks half q1 q2 q3 q4 died death deaths mortality deceased
denominator denominators numerator numerators avoidable preventable unnecessary inappropriate'''.split())
_VOCAB_CACHE = {}


def _vocabulary(profile):
    key = id(profile)
    if key not in _VOCAB_CACHE:
        words = set()
        for s, spec in profile['streams'].items():
            words.update(t for t in s.split('_') if len(t) > 3)
            for f, fs in spec['fields'].items():
                words.update(t for t in f.split('_') if len(t) > 3 and t not in ('date', 'time', 'record', 'patient', 'source', 'system', 'status'))
                for v in (fs.get('values') or {}):
                    if len(v) <= 40: words.update(t for t in re.split(r'[^a-z0-9]+', v.lower()) if len(t) > 3)
        for aliases in (*CONDITION_ALIASES.values(), *MEASUREMENT_ALIASES.values(), *ENCOUNTER_ALIASES.values()):
            for a in aliases: words.update(t for t in re.split(r'[^a-z0-9]+', a) if len(t) > 3)
        words -= FILLER | {'with', 'have', 'from', 'that', 'this', 'what', 'were', 'been', 'their', 'they', 'when', 'into', 'than',
                           'visit', 'visits', 'patient', 'patients', 'type', 'data', 'received', 'report', 'reports', 'reported'}
        _VOCAB_CACHE[key] = words
    return _VOCAB_CACHE[key]


CLARIFICATIONS = [  # (pattern, effect) - sentences that restate Agere's default semantics or the output shape
    (r'return (?:the |a |all )?(?:complete )?patient ids?(?: set)?', 'patient_set'),
    (r'return (?:a )?table[^.?]*', 'table'), (r'return (?:a )?numeric value', 'value'), (r'return (?:percent|cents)', None),
    (r'do not (?:restrict to the latest result|subtract refunds|infer net balances|net corrections|treat it as realized savings'
     r'|count missing fields as false|require a loinc code|advance)[^.?]*', None),
    (r'do not infer[^.?]*', None),
    (r'this (?:asks about|does not establish|is absence of)[^.?]*', None),
    (r'count each patient once[^.?]*', None), (r'include people with no social screen', None),
    (r'exclude prediabetes(?: and family history)?', None), (r'counting [a-z ]+ records as the denominator', None),
    (r'count (?:notification|order|discharge) records, not unique stays', None),
    (r'regardless of diagnosis', None), (r'ignoring case', None), (r'historical reports need not be contemporaneous', None),
]


MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
                                      'october', 'november', 'december'], 1)}
MONTH_RX = r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'


def _month_number(token):
    token = token.lower()
    return next(i for name, i in MONTHS.items() if name.startswith(token[:3]))


def _norm(q, as_of='2029-08-15'):
    q = q.strip()
    y, m, d = as_of.split('-')
    month = list(MONTHS)[int(m) - 1]
    snapshot = (rf'(?:{as_of}|{month[:3]}[a-z]* {int(d)}(?:st|nd|rd|th)?,? {y}|{month[:3]}[a-z]* {int(d)}(?:st|nd|rd|th)?)')
    lead = (r'(?:(?:use|using|with)? ?(?:the |what )?(?:received |the )?(?:evidence|data|record|warehouse|what we(?:\'d| had| have)?)?'
            r'(?: as)? ?(?:received|available|had received|\'d received)? ?(?:as of|through|thru|by|until|up to|to date|on)?)')
    q = re.sub(r'[,;(]?\s*' + lead + r'\s*' + snapshot + r'\)?\.?', ' ', q, flags=re.I)
    q = re.sub(r'\(\s*\)', ' ', q)
    q = re.sub(r'(?<![\d.])(\d{1,3})(?:,(\d{3}))+(?![\d])', lambda m: m.group(0).replace(',', ''), q)  # 1,000 -> 1000
    # "in our network" = the whole population (filler); "out of network" / leakage phrasing is kept.
    q = re.sub(r'(?<!out of )\b(?:in|across|throughout) (?:our|the) (?:whole |entire )?network\b', ' ', q, flags=re.I)
    return re.sub(r'\s+', ' ', q).strip()


# Plain analyst English -> the phrasing the grammar reads. Each rule preserves meaning exactly;
# anything a rule cannot restate faithfully is left alone so the compiler declines it instead.
PARAPHRASES = [
    # conversational framing: who is asking and why does not change the question
    (r'^(?:hi|hey|hello|ok|okay|quick question)[,!.]?\s+', ''),
    (r'^(?:[a-z/&\'-]+ ){0,3}here[.,:!]\s*', ''),                                    # "CMO here."
    (r"^i'?m (?:looking at|working on|thinking about|interested in|reviewing) [^.?!]*[.!]\s+(?=\S)", ''),     # topic sentence before the real question
    (r'^(?:for|from) (?:the |our |a )?(?:[a-z/&\'-]+ ){1,5}(?:proposal|scorecard|review|report|deck|meeting|board|committee)[:,]\s*', ''),
    (r'\b(?:please|pls|thanks|thank you|roughly|approximately|approx\.?|ballpark|quickly|just|actually|really)\b[,!]?', ' '),
    (r'\b(?:have|did|do) we (?:seen|see|had|have|logged|recorded|gotten|got)\b', ' '),
    (r'\b[a-z]+ outreach list\b', 'list'),
    (r'\b(?:for|in) (?:the |our |a )?(?:health )?equity (?:report|view|dashboard|analysis|review|scorecard|work)\b', ' '),
    (r'\b(?:did |does |do )?(?:their|the|each) (?:pcp\'?s? )?panels? (?:have|had|get|got)\b|\bfor (?:their|the) panels?\b', ' '),
    (r'\b(?:did|does|do) (?:each|every) ([a-z_ ]{3,20}?) (?:administer|give|send|order|perform|do|see|bill|submit|receive|write)\b', r'by \1'),
    (r'\b(?:who|that) (?:say|said|report|reported) (?:they )?(?:struggle|struggled|have trouble|had trouble|have difficulty|had difficulty) (?:to )?(?:pay(?:ing)? for|afford(?:ing)?) (?:medical )?care\b', 'with difficulty paying for care'),
    (r'\b(patients?|members?|people|adults|seniors) (?:reporting|who report(?:ed)?|that report(?:ed)?|who screened positive for|screening positive for)\b', r'\1 with'),
    (r'\bnames?/ids?\b|\bnames? (?:and|or|&) ids?\b|\bmrns?\b', ' list '),
    (r'\b(?:can|could|would) you (?:please )?(?:pull|get|give|send|run|show|find)(?: me| us)? (?:a |the )?list of\b', 'list'),
    (r'\b(?:can|could|would) you (?:please )?tabulate\b', 'count'),
    (r'\b(?:can|could|would) you (?:please )?(?:pull|get|give|send|run|show|tell|find|count)(?: me| us)?\b', ' '),
    (r"\bi(?:'d| would)? (?:want|need|like)(?: to (?:know|see))?\b|\bi'm (?:trying to (?:find|see|figure out)|wondering)\b", ' '),
    (r'\bhow big is (?:our |the )?([a-z0-9 -]+?) (?:population|cohort|panel|registry)\b', r'how many patients have \1'),
    (r"\bwhat(?:'s| is) the size of (?:our |the )?([a-z0-9 -]+?) (?:population|cohort|panel|registry)\b", r'how many patients have \1'),
    # abbreviations
    (r'\bpts\b', 'patients'), (r'\bdx\b', 'diagnosis'), (r'\bhx\b', 'history'),
    # default semantics restated: a condition counts however it is coded (ICD-10, SNOMED or label)
    (r'\b(?:anywhere )?on (?:their|the|his|her) problem lists?\b|\b(?:however|no matter how) (?:it was|they were|it is|it\'s) coded\b|'
     r'\b(?:of|in) any (?:kind|form|type|coding(?: system)?)\b|\bcoded (?:any ?way|anywhere)\b', ' '),
    (r'\b(?:any|every|all) ([a-z0-9 -]+?) (?:results?|readings?|values?) counts?\b', ' '),
    # grouping verbs
    (r'\b(?:how (?:do|does) (?:they|it|that|these|those) )?break(?:s|ing)? (?:it |them |this |that )?(?:down|out) by\b', ' by '),
    (r'\bbreak(?:s|ing)? (?:down|out)\b', ' '),
    (r'\bsplit (?:it |them )?(?:out )?by\b|\bbroken (?:down|out) by\b', ' by '),
    (r"\bby (?:the |their )?(?:patient'?s |member'?s )?(?:payer|insurance|coverage|plan)(?: type)?\b", 'by payer'),
    (r'\b(?:by|per|for each|each) (?:practice|clinic)s?\b', 'by practice'),
    (r'\b(by [a-z_ ]{2,30}?)\s*\((?:[a-z0-9 _-]+\s*(?:/|,|or|and)\s*)+[a-z0-9 _-]+\)', r'\1'),   # "by payer (medicare / medicaid)": categories, not filters
    (r'\b(?:in |during |for )?calendar (?:year )?(\d{4})\b', r' in \1'),
    # quantities
    (r'\b(?:at least one|one or more|1\+)\b', 'any'),
    (r'\b(ed|er|emergency department|urgent care|inpatient|primary care|admission|admit) (?:volume|volumes|counts?|utili[sz]ation)\b', r'\1 visits'),
    # time
    (r'\b(?:with|for|having) ((?:19|20)\d{2}) ([a-z]+) dates?\b', r'with \2 date in \1'),            # "claims with 2029 service dates"
    (r'\b(?:for|with|in) ((?:19|20)\d{2}) dates? of ([a-z]+)\b', r'with \2 date in \1'),             # "for 2028 dates of service"
    (r'(?<![\d/-])\b((?:19|20)\d{2}) (?!ytd\b|so far\b|to date\b|year)(?=(?:[a-z-]+ ){0,3}?(?:visits?|admissions?|stays?|claims?|claim rows|referrals?|reports?|payments?|fills?|appointments?|encounters?|orders?|discharges?|doses|records?|rows)\b)',
     r'in \1 '),                                                                                            # "2026 ED visits"
    (r'\bytd (\d{4})\b', r'\1 ytd'), (r'\b(\d{4}) (?:so far|ytd|year[- ]to[- ]date)(?: \((?:through|thru) [^)]*\))?', r'\1 ytd'),
]


# Words that only say who is asking or why ("Finance:", "Quality director question:", "Care gap check -").
FRAMING_WORDS = set('''for from the our a an my this cmo cfo ceo coo cno cmio cio vp svp chief medical officer officers director directors
manager managers lead leads head quality finance financial pharmacy nursing operations ops analytics analyst analysts data engineering
engineer team teams question questions check checks request requests ask quick care gap gaps transitions transition population health
value-based value based board committee scorecard proposal integration compliance revenue cycle here hi hey behavioral medicare advantage
aco mssp contract contracting payer strategy network utilization management um cm case program programs outreach'''.split())


def _strip_framing(low):
    m = re.match(r'^([a-z/&\'\- ]{2,80}?)\s*(?:[:\u2014\u2013]|\s-\s)\s+(?=\S)', low)
    if m and all(w in FRAMING_WORDS for w in re.split(r'[\s/&]+', m.group(1).strip()) if w):
        return low[m.end():]
    return low


def _paraphrase(q, as_of):
    """Apply PARAPHRASES; returns (text, rules applied)."""
    low, applied = _strip_framing(q.lower()), []
    if low != q.lower(): applied.append('framing')
    y = as_of[:4]
    us = f'{int(as_of[5:7])}/{int(as_of[8:10])}/{y}'  # the snapshot written as m/d/yyyy restates the default
    low = re.sub(r'\(?\s*(?:through |thru |as of )?' + re.escape(us) + r'\s*\)?', ' ', low)
    for pattern, repl in [(r'\bthis (?:calendar )?year\b(?: \(?\d{4} (?:so far|to date|ytd)\)?)?', f'{y} ytd'),
                          (r'(?<!\d{4} )\b(?:year[- ]to[- ]date|ytd)\b(?! \d)', f'{y} ytd')] + PARAPHRASES:
        new = re.sub(pattern, repl, low)
        if new != low: applied.append(pattern[:40]); low = new
    low = re.sub(r'\b(\d{4}) ytd (\d{4}) ytd\b', r'\1 ytd', low)
    low = re.sub(r'\b(\d{4}) \1 ytd\b', r'\1 ytd', low)
    return re.sub(r'\s+', ' ', low).strip(' ,'), applied


class Reader:
    """Tracks which characters of the question have been explained."""
    def __init__(self, text):
        self.text, self.low = text, text.lower()
        self.used = [False] * len(text)
        self.notes = []

    def take(self, pattern, note=None, flags=re.I, overlap=False):
        hits = []
        for m in re.finditer(pattern, self.low, flags):
            if m.end() == m.start() or (not overlap and any(self.used[m.start():m.end()])): continue
            for i in range(m.start(), m.end()): self.used[i] = True
            hits.append(m)
        if hits and note: self.notes.append(note)
        return hits

    def blocking(self, profile):
        """Leftover words that carry meaning Agere failed to use: numbers, operators, or data-dictionary/clinical terms.
        Conversational filler is ignored (and recorded)."""
        vocab = _vocabulary(profile)
        stems = {t[:5] for t in vocab if len(t) >= 5}
        left = self.leftover()
        # Inflections count: 'rejections' carries the meaning of the observed value 'rejected'.
        block = [w for w in left if re.search(r'\d', w) or '_' in w or w in OPERATOR_WORDS or w.rstrip('s') in vocab or w in vocab
                 or (len(w) >= 5 and w[:5] in stems)]
        self.ignored = [w for w in left if w not in block]
        return block

    def leftover(self):
        text = ''.join(c if not u else ' ' for c, u in zip(self.low, self.used))
        words = re.findall(r"[a-z][a-z0-9'_/-]*", text)
        # Unused numbers always carry meaning (a year, a threshold, a count): never drop them silently.
        numbers = [n for n in re.findall(r"(?<![a-z0-9.])\d+(?:[.,]\d+)?(?![a-z0-9])", text)]
        return [w.strip("-/'") for w in words if w.strip("-/'") not in FILLER and len(w.strip("-/'")) > 1] + numbers


def _value_pattern(value):
    parts = [t for t in re.split(r'[_\s-]+', value.lower()) if t]
    stem = lambda t: re.escape(t[:5]) + r'\w*' if len(t) > 5 else re.escape(t)
    return r'(?<![a-z0-9])' + r'[\s_-]+'.join(stem(t) for t in parts) + r'(?![a-z0-9])'


CODED_BOTH = r'\b(?:icd-?10 (?:and|or) snomed|snomed (?:and|or) icd-?10|coded (?:diagnos[ie]s|conditions?))\b'


def _alias_pattern(alias):
    return r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])'


def _negated(low, start):
    window = low[max(0, start - 40):start]
    return bool(re.search(r'\b(?:no|not|without|never|lacks?|lacking|but no|excluding|exclude|except)\b(?:\s+\S+){0,4}\s*$', window))


NATURAL_STREAM_EXCLUDE = {'conditions', 'labs', 'vitals', 'encounters', 'patients', 'coverage', 'notes', 'documents', 'social_screens', 'financial_screens'}


def _field_forms(fields):
    """Natural names for fields: 'payment channel', and a distinctive last word ('channel') when unique."""
    last = Counter(f.split('_')[-1] for f in fields)
    forms = {}
    for f, spec in fields.items():
        if not spec['populated'] or len(f) < 3: continue
        out = {f, f.replace('_', ' ')}
        tail = f.split('_')[-1]
        if last[tail] == 1 and len(tail) > 4 and tail not in ('cents', 'reported', 'date', 'time', 'status'): out.add(tail)
        forms[f] = sorted(out, key=len, reverse=True)
    return forms


def _stream_vocabulary(profile):
    """Natural names for each received stream: 'pharmacy_dispenses', 'pharmacy dispenses', 'appointment(s)'."""
    names = {}
    for s in profile['streams']:
        spaced = s.replace('_', ' ')
        forms = {s, spaced}
        if spaced.endswith('s'): forms.add(spaced[:-1])
        names[s] = sorted(forms, key=len, reverse=True)
    names.setdefault('adt', []).extend(['adt notification', 'adt'])
    for stream, words in STREAM_SYNONYMS.items():
        if stream in profile['streams']: names[stream] = sorted(set(names.get(stream, [])) | set(words), key=len, reverse=True)
    return names


FIELD_SYNONYMS = {'anatomic_site': ['body site', 'body part', 'anatomic site'], 'payment_channel': ['channel', 'payment method'],
                  'requested_specialty': ['specialty'], 'vaccine': ['vaccine type'], 'impression': ['impression', 'read', 'finding']}


# Everyday analyst nouns for received streams (used only when the stream exists in this release).
STREAM_SYNONYMS = {
    'medical_claims': ['medical claim rows', 'medical claim', 'medical claims', 'claim rows', 'professional claims', 'claims'],
    'pharmacy_dispenses': ['pharmacy fills', 'prescription fills', 'rx fills', 'fills', 'dispenses', 'dispensings', 'dispensing events'],
    'patient_payments': ['patient payments', 'payments received', 'payments'],
    'immunizations': ['vaccine doses', 'vaccinations', 'vaccines', 'vaccine', 'shots', 'doses', 'immunization doses'],
    'imaging_reports': ['x-ray reports', 'xray reports', 'radiology reports', 'imaging reports'],
    'imaging_orders': ['imaging orders', 'radiology orders', 'x-ray orders'],
    'laboratory_orders': ['lab orders', 'laboratory orders'],
    'referrals': ['specialty referrals', 'referrals', 'referral'],
    'appointments': ['appointments', 'appts', 'appointment'],
    'telephone_messages': ['phone messages', 'telephone messages'],
}


# Words that ask for an analytic method; a plain count or population list never answers them.
ANALYTIC_INTENT = (r"\b(?:why|what'?s behind|what is behind|driv(?:ing|ers?|es)|associated with|go(?:es)? along with|characteristics|"
                   r"contribut\w*|predicts?|explain|trend(?:s|ing)?|sliding|creeping|getting worse|worsening|declin\w+|trajector\w*|"
                   r"climb\w*|wrong way)\b")
PLAIN_OPS = {'cohort', 'aggregate', 'sql', 'union', 'intersect', 'difference'}


def compile(question, profile, as_of):
    """Return (graph, info). graph is None when the question needs the fallback planner.

    A plan made only of counting/listing operations is declined when the question asks for an analytic
    method (why, trend, ...): the operator router then picks the engine instead of a count answering it."""
    graph, info = _compile(question, profile, as_of)
    if graph is None:  # hand-written routes declined: accepted declarative rules (config/agere/compiler_rules) before the router
        from . import compiler_rules
        graph, info = compiler_rules.apply(question, profile, as_of, info)
    unread =_unread_demographics(question, profile, graph, info)
    if unread:  # the release has the field, but this plan does not use it: never answer as if the words were not there
        return None, {**info, 'route': 'fallback', 'reason': f'{unread} wording not interpreted locally'}
    if graph and graph.get('nodes') and {n['operation'] for n in graph['nodes']} <= PLAIN_OPS \
            and re.search(ANALYTIC_INTENT, question.lower()) \
            and not re.search(r'\b(?:monthly|weekly|quarterly|yearly|annual|by (?:month|week|quarter|year)|per (?:month|week|quarter|year)|over time)\b', question.lower()):
        return None, {**info, 'route': 'fallback', 'reason': 'analytic intent (why/trend) not expressible as a count'}
    return graph, info


def _unread_demographics(question, profile, graph, info):
    """When the release carries race/ethnicity, language or CPT the refusal no longer fires; the words must then be
    consumed by the plan (a demographic group_by, or the CPT lookup), else the plan would silently ignore them."""
    if not graph or not graph.get('nodes') or graph['nodes'][-1]['operation'] == 'knowledge': return None
    low = ' '.join(str(question).lower().split())
    groups = {str((n.get('parameters') or {}).get('group_by')) for n in graph['nodes']}
    rest = low
    for rx, _ in GROUPS_DEMOGRAPHIC: rest = re.sub(rx, ' ', rest)
    if (_race_received(profile) or _language_received(profile)) and re.search(DEMOGRAPHIC_CATEGORY, rest) \
            and re.search(RACE_WORDS + '|' + LANGUAGE_WORDS, low):
        return 'demographic subgroup'
    if _race_received(profile) and re.search(RACE_WORDS, low) and not groups & {'race', 'ethnicity', 'race_ethnicity'}:
        return 'race/ethnicity'
    if _language_received(profile) and re.search(LANGUAGE_WORDS, low) and not groups & {'language', 'interpreter'}:
        return 'language/interpreter'
    if _cpt_received(profile) and re.search(CPT_WORDS, low) and not info.get('cpt_lookup'):
        return 'CPT/HCPCS'
    return None


def _compile(question, profile, as_of):
    strategy = _contract_strategy_question(question, profile, as_of)  # should-we / which track / negotiate / option value; settlements stay vbc
    if strategy is not None: return strategy
    explained = _knowledge_question(question)  # explanation / strategy questions about VBC itself, never data questions
    if explained is not None:
        return explained
    q = _norm(question, as_of)
    low = q.lower()
    timed = survival_intent(low) and not re.search(DEATH_WORDS, low)  # "readmission-free survival" is not mortality
    if (plan := _target_trial_question(q, profile)) is not None: return plan  # drug A vs drug B (new-user active comparator); single starts stay program_effect
    if (plan := _line_of_therapy_question(q, profile)) is not None: return plan  # lines of therapy, switching, sequences, persistence from fills
    if (plan := _trial_feasibility_question(q, profile, as_of)) is not None: return plan  # trial eligibility funnel over stated criteria
    effect = _program_effect_question(q, profile)
    if effect is not None:
        return effect
    calibrated = _calibration_question(q)
    if calibrated is not None:
        return calibrated
    booked = _scheduling_question(q)  # overbooking / likely no-shows / reminders / access; plain no-show RATES stay literal metrics
    if booked is not None:
        return booked
    briefed = _briefing_question(q)  # 'what should I look at today?' -> the ranked proactive briefing
    if briefed is not None:
        return briefed
    refused = _refusal(q, low, profile, timed, _contract_terms(low), {'compiler': VERSION, 'recognised': [], 'unexplained': []})
    if refused is not None:  # checked on the user's own words, before any paraphrase
        return refused
    plan = _early_signal_question(q, profile, as_of)  # precursor signatures: before demand ("visits in the next 3 months"), outreach, pathway
    if plan is not None:
        return plan
    if demand_intent(low):  # volume forecasts: before outreach, whose capacity grammar would read "36 hours per week" as call capacity
        plan = _demand_question(q, profile, as_of)
        if plan is not None:
            return plan
    plan = _outreach_question(q, profile, as_of)  # before paraphrase: it rewrites "<x> outreach list" for plain patient lists
    if plan is not None:
        return plan
    plan = _loop_closure_question(q, profile, as_of)  # results / referrals / recommendations / orders without follow-up; time-to-event stays survival
    if plan is not None:
        return plan
    plan = _measure_question(q, profile, as_of)  # named library quality measures (HEDIS/Stars/eCQM/PQI approximations)
    if plan is not None:
        return plan
    plan = _risk_outcomes_question(q, profile, as_of)  # RSRR / O/E / ERR / funnel / HRRP; plain fair PCP comparison stays profile
    if plan is not None:
        return plan
    plan = _diagnostic_delay_question(q, profile, as_of)  # missed / delayed diagnosis look-back, undiagnosed review prompts; HCC suspects stay risk_adjustment
    if plan is not None:
        return plan
    plan = _network_question(q, profile, as_of)  # provider network (hubs, shared patients, leakage by specialty, steering, spread); journeys stay pathway
    if plan is not None:
        return plan
    plan = _pathway_question(q, profile, as_of)  # care paths / journeys / leakage, before paraphrase rewrites the wording
    if plan is not None:
        return plan
    q, paraphrased = _paraphrase(q, as_of)
    named =re.search(r'(?:^|[.;?] |, )(?:give(?: me)?|return|show|columns?(?: named)?|with columns?|output)\s*:?\s*([a-z][a-z_]*)\s*(?:,|and|&)\s*([a-z]+_[a-z_]+)\.?\s*$', q)
    columns = None
    table_with = re.search(r'\btable with ([a-z][a-z_]*) and ([a-z][a-z_]*)', q)
    if named and not table_with:
        columns = [named.group(1), named.group(2)]
        q = q[:named.start()].rstrip(' ,.;') + ('?' if q.rstrip().endswith('?') else '')
    elif table_with:  # literal-stream SQL aliases these itself; population tables rename their rows
        columns = [table_with.group(1), table_with.group(2)]
    r = Reader(q)
    r.profile = profile
    info = {'compiler': VERSION, 'recognised': r.notes, 'paraphrased': paraphrased, **({'columns': columns} if columns else {})}
    # 0. Requests the received data cannot support: answer unsupported locally, naming the missing inputs.
    terms = _contract_terms(r.low)
    refused = _refusal(q, r.low, profile, timed, terms, info)
    if refused is not None:
        return refused
    info['clarifications'] = []
    for pattern, effect in CLARIFICATIONS:
        for m in r.take(r'(?:^|(?<=[.;?] )|(?<=; )|(?<=, )|(?<=and ))' + pattern, None):
            info['clarifications'].append(m.group(0))
            if effect: info.setdefault('output', effect)
    r.take(r'\b(?:on file|on record|in (?:the |our )?(?:record|chart|warehouse)s?|on the roster|in the data)\b', None)
    hcc = _risk_adjustment_question(q, r, profile, as_of, info)  # explicit HCC/RAF wording only; a plain "risk score" stays vbc
    if hcc is not None:
        return hcc
    pid = r.take(r'\bp-[a-z0-9]+\b', 'patient id')
    if pid:  # single-patient questions with a measurement statistic
        return _patient_question(q, r, profile, [m.group(0).upper() for m in pid], info)
    watch = _monitor_question(q, r, profile, as_of, info)
    if watch is not None:
        return watch
    integrity = _claims_review_question(q, r, profile, as_of, info)  # after monitor: Benford/duplicate/control-chart wording stays there
    if integrity is not None:
        return integrity
    moved = _market_question(q, r, profile, as_of, info)  # after monitor: control-chart questions stay with monitor
    if moved is not None:
        return moved
    timed_plan = _survival_question(q, r, profile, as_of, info)
    if timed_plan is not None:
        return timed_plan
    fair = _profile_question(q, r, profile, as_of, info)
    if fair is not None:
        return fair
    coded = _cpt_question(q, r, profile, as_of, info)  # CPT/HCPCS service counts (releases that carry the codes)
    if coded is not None:
        return coded
    special = _record_constructs(q, r, profile, as_of, info)
    if special is not None:
        return special
    med = _medication_question(q, r, profile, as_of, info)
    if med is not None:
        return med
    ops = _ops_question(q, r, profile, as_of, info)
    if ops is not None:
        return ops
    vbc_plan = _vbc_question(q, r, profile, as_of, info, terms)
    if vbc_plan is not None:
        return vbc_plan
    literal = _literal_stream_question(q, r, profile, as_of, info)
    if literal is not None:
        return literal
    return _population_question(q, r, profile, as_of, info)


# ---- monitoring: statistical process control, feed health and claims review flags -------------
MONITOR_TRIGGER = (r"\bout of (?:statistical )?control\b|\banomal(?:y|ies|ous)\b|\bbenford(?:'s)?\b|\bcontrol charts?\b|\bspc\b|"
                   r"\bstatistical process control\b|\bwestern electric\b|\bnelson rules?\b|\bewma\b|\bbehaving (?:ab)?normally\b|"
                   r"\bbehaving (?:oddly|strangely|unusually)\b|\bduplicate (?:claims?|billing|bills)\b|\bbilling (?:intensity )?outliers?\b|"
                   r"\bunusual billing\b|\bfeeds? (?:gaps?|outages?|drop(?:out)?s?|health)\b|"
                   r"\b(?:not|stopped|aren'?t|isn'?t|no longer) sending (?:us )?(?:any |their )?(?:adt|lab|claims?|encounter)\b")
MONITOR_SERIES = [('ed_rate', r'\b(?:ed|er|emergency(?: department)?) (?:use|visits?|utili[sz]ation|rates?)\b'),
                  ('admit_rate', r'\b(?:inpatient )?(?:admissions?|admits?|hospitali[sz]ations?)(?: rates?)?\b'),
                  ('pmpm', r'\bpmpm\b|\bper member per month\b|\b(?:total |medical )?(?:cost|spend(?:ing)?)\b'),
                  ('denial_rate', r'\b(?:claims? )?denials?(?: rates?)?\b|\bdenial rates?\b'),
                  ('arrival_lag', r'\b(?:claims? )?(?:arrival |submission )?lag\b'),
                  ('lab_volume', r'\blab(?:oratory)? (?:volumes?|results?|counts?)\b')]
MONITOR_STREAMS = [(r'\blab(?:oratory)?s?(?: results?)? feeds?\b|\blabs?\b(?= (?:from|feed))', 'labs'),
                   (r'\bclaims? feeds?\b', 'medical_claims'), (r'\b(?:encounter|visit) feeds?\b', 'encounters'),
                   (r'\bsocial screens?(?:ing)? feeds?\b', 'social_screens'), (r'\badt (?:event )?(?:feeds?|notifications?|messages?)\b', 'adt'),
                   (r'\bappointments? feeds?\b', 'appointments'), (r'\bpharmacy feeds?\b', 'pharmacy_dispenses'),
                   (r'\bvitals? feeds?\b', 'vitals'), (r'\bnotes? feeds?\b', 'notes')]
MONITOR_GROUPS = [(r'\b(?:which|what|by|each|per|any|for each) (?:of (?:our|the) )?(?:practices?|clinics?)\b', 'practice'),
                  (r'\b(?:which|what|by|each|per|any|for each) (?:of (?:our|the) )?(?:payers?|payors?|plans?|insurers?)\b', 'payer'),
                  (r'\b(?:which|what|by|each|per|any|for each) (?:of (?:our|the) )?(?:facilit(?:y|ies)|hospitals?|sites?|service organi[sz]ations?)\b', 'facility'),
                  (r'\b(?:which|what|by|each|per|any|for each|from any) (?:of (?:our|the) )?(?:source systems?|sources?|systems?)\b', 'source_system')]


def _monitor_question(q, r, profile, as_of, info):
    low = r.low
    if not re.search(MONITOR_TRIGGER, low): return None
    params = {}
    claims_words = r'\b(?:claims?|billing|bills?|billed amounts?|allowed amounts?)\b'
    # A named feed first ('lab feed' must not be read as a generic 'feed'): received stream names, then common aliases.
    named = sorted((s for s in (profile or {}).get('streams', {}) if len(s) > 2), key=len, reverse=True)
    for stream, pattern in [(s, r'\b' + re.escape(s.replace('_', ' ')) + r' feeds?\b') for s in named] + \
                           [(s, p) for p, s in MONITOR_STREAMS]:
        if r.take(pattern, f'feed {stream}'):
            params['stream'] = stream
            break
    if r.take(r"\bbenford(?:'s)?(?: law)?(?: (?:check|test|analysis|conformity))?\b", 'monitor benford'):
        params['check'] = 'benford'
    elif r.take(r'\bduplicate (?:claims?|billing|bills)\b|\bduplicat(?:e|ed|es) (?:charges?|submissions?)\b', 'monitor duplicates'):
        params['check'] = 'duplicates'
    elif r.take(r'\bbilling (?:intensity )?outliers?\b|\bunusual billing\b', 'monitor peer outliers'):
        params['check'] = 'peer_outliers'
        r.take(r'\b(?:service )?organi[sz]ations?\b|\b(?:versus|vs\.?|compared (?:to|with)|relative to) (?:their |its )?peers?\b|\bpeers?\b', None)
    elif r.take(r'\bout of (?:statistical )?control\b|\bcontrol charts?\b|\bspc\b|\bstatistical process control\b|\bwestern electric\b|'
                r'\bnelson rules?\b', 'monitor control chart'):
        params['check'] = 'control_chart'
    elif r.take(r'\b(?:data )?feeds?\b(?=.*\b(?:behaving|anomal|gaps?|outages?|drop|health)\b)', 'monitor feed') or \
            re.search(r'\bfeeds? (?:gaps?|outages?|drop(?:out)?s?|health)\b', low) or \
            r.take(r"\b(?:are |is )?(?:not|stopped|aren'?t|isn'?t|no longer) sending(?: us)?(?: any| their)?\b", 'feed silence'):
        params['check'] = 'feed'
    elif params.get('stream'):
        params['check'] = 'feed'
    elif re.search(claims_words, low):
        params['check'] = 'claims'
    else:
        params['check'] = 'all'
    r.take(r"\banomal(?:y|ies|ous)\b|\bbehaving (?:ab)?normally\b|\bbehaving (?:oddly|strangely|unusually)\b|\bfeeds? (?:gaps?|outages?|"
           r"drop(?:out)?s?|health)\b|\bewma\b", 'monitor anomaly')
    series = []
    for s, pattern in MONITOR_SERIES:
        if r.take(pattern, f'series {s}'): series.append(s)
    if params.get('stream') == 'labs' and 'lab_volume' not in series: series.append('lab_volume')
    if series and params['check'] in ('all', 'claims'): params['check'] = 'control_chart'
    if series: params['series'] = series
    if re.search(r'\bewma\b|\bexponentially weighted\b', low): params['method'] = 'ewma'
    for pattern, g in MONITOR_GROUPS:
        if r.take(pattern, f'group by {g}'): params['group_by'] = g; break
    if params['check'] == 'benford' and 'group_by' not in params: params['group_by'] = 'payer'
    amount = r.take(r'\b(billed|allowed|charged?)(?: amounts?| charges?| dollars?)?\b', 'amount')
    if amount: params['amount'] = 'allowed' if amount[0].group(1) == 'allowed' else 'billed'
    period = _take_period(r, as_of)
    if period:
        params['period'] = {'start': period[0], 'end': period[1]}
    elif r.take(r'\b(?:this|the current) year\b|\byear[- ]to[- ]date\b|\bytd\b|\bso far this year\b', 'this year'):
        params['period'] = {'start': f'{as_of[:4]}-01-01', 'end': as_of}
    elif r.take(r'\blast year\b|\bthe prior year\b', 'last year'):
        y = int(as_of[:4]) - 1
        params['period'] = {'start': f'{y}-01-01', 'end': f'{y}-12-31'}
    r.take(claims_words, None, overlap=True)
    r.take(r"\b(?:run|do|check|test|look|scan|flag|show|find|are there|is there|any|our|the|on|in|of|for|from|by|which|what|are|is|"
           r"were|was|has|have|been|seen|signs?|anything|unusual|abnormal(?:ly)?|odd|strange|behaving|data|feeds?|check|test|analysis|"
           r"amounts?|charges?|statistically|currently|right now|lately|recently|use|rates?|volumes?|it|they|them|over time|monthly)\b",
           None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    info['route'] = 'local_compiler'
    from .monitor import validate
    params = validate(params)
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'monitor', 'inputs': [], 'parameters': params,
                       'purpose': f"Monitor: {params['check']}" + (f" {', '.join(params['series'])}" if params['series'] else '')
                                  + (f" by {params['group_by']}" if params['group_by'] != 'auto' else '')}]}, info


# ---- claims review: payment integrity and revenue leakage in dollars ---------------------------------
CLAIMS_UNDERPAID = r"\bunder[- ]?pa(?:id|ys?|yments?|ying)\b|\bshort[- ]?pa(?:id|ys?|yments?)\b|\bpaid (?:us )?less than (?:expected|similar|comparable|contract)"
CLAIMS_LEAK = (r"\bleak(?:s|ing|age)?\b|\blosing (?:money|revenue|dollars)\b|\b(?:revenue|money|dollars?) (?:lost|left on the table)\b|"
               r"\bwrite[- ]?offs?\b|\bwritten[- ]off\b")
CLAIMS_DENIALS = (r"\bdenial (?:patterns?|reasons?|pareto|codes?|root causes?|categor(?:y|ies)|drivers?)\b|\b(?:patterns?|reasons?|causes?) (?:of|for|behind) "
                  r"(?:(?:our|the|claims?) )*denials?\b|\bwhy (?:are|were|do|did) (?:our |the )?claims? (?:get )?denied\b|\boverturn(?:ed)?(?: rates?)?\b|"
                  r"\bcarcs?\b|\badjustment reason codes?\b|\bdenials? (?:by|per) (?:payer|reason)\b")
CLAIMS_CHURN = (r"\bresubmi(?:ssions?|tted|ts?)\b|\brework\b|\breversals?\b|\btake[- ]?backs?\b|\bclaw[- ]?backs?\b|\brecoup(?:ments?|ed)?\b|"
                r"\brepric(?:ed|ing)\b|\bduplicate or resubmitted\b")
CLAIMS_PEERS = (r"\b(?:bill(?:s|ed|ing)?|charg(?:e|es|ed|ing)) (?:unusually|abnormally|oddly|strangely|differently|much more|more)\b|"
                r"\bunusual(?:ly)? (?:high )?(?:billers?|billing providers?)\b|\b(?:billing|coding) (?:outliers?|anomal\w*)\b(?= .*\bproviders?\b)|"
                r"\b(?:over[- ]?bill\w*|upcod\w*|outlier billers?)\b")
CLAIMS_INTEGRITY = (r"\bsuspicious (?:billing|claims?|patterns?|charges?)\b|\bbilling patterns?\b|\bpayment integrity\b|\bclaims? (?:integrity )?review flags?\b|"
                    r"\bfraud\w*\b|\bwaste,? (?:and |or )?abuse\b|\bimproper payments?\b")
CLAIMS_TRIGGER = '|'.join((CLAIMS_UNDERPAID, CLAIMS_LEAK, CLAIMS_DENIALS, CLAIMS_CHURN, CLAIMS_PEERS, CLAIMS_INTEGRITY))
CLAIMS_FILLER = (r"\b(?:which|what|where|who|whose|why|how|is|are|was|were|do|does|did|have|has|had|been|be|we|us|our|the|a|an|any|anything|of|on|in|for|"
                 r"by|to|and|or|with|versus|vs|than|that|this|these|those|there|show|list|find|flag|give|tell|explain|identify|spot|look|check|run|see|"
                 r"me|it|they|them|their|its|most|top|biggest|largest|main|some|signs?|patterns?|claims?|billing|bills?|billed|money|dollars?|revenue|"
                 r"cash|payments?|paid|pay|payers?|plans?|insurers?|reasons?|reason codes?|denials?|denied|providers?|practices?|clinics?|organi[sz]ations?|"
                 r"facilit(?:y|ies)|peers?|similar|comparable|compared|relative|against|expected|unusual(?:ly)?|suspicious|abnormal(?:ly)?|odd|weird|"
                 r"strange|outliers?|high|higher|much|more|less|lower|getting|get|being|going|losing|lost|patterns?|leaking|trend|days?|"
                 r"resolution|recovered|recover|rates?|duplicates?|duplicated|exact|near|first[- ]pass|other|others|same|services?|setting|mix|"
                 r"where are we|could|should|can|would|happening|happen|problems?|issues?|amounts?|charges?|charged|by how much|recently|lately)\b")


LITERAL_MONEY_TOTAL = (r"\bhow much (?:did|do|does|have|has|was|were)\b|\bwhat (?:did|do|does) (?:\w+ ){0,3}pay\b|\btotal (?:net )?(?:paid|payments?|allowed|billed|"
                       r"payer paid|dollars|amount)\b|\bsum of\b|\bnet of (?:reversals|take[- ]?backs|recoupments|clawbacks)\b|\bnet (?:payer[- ]?)?paid\b")


def _claims_review_question(q, r, profile, as_of, info):
    low = r.low
    if not re.search(CLAIMS_TRIGGER, low): return None
    hit = lambda pattern: bool(re.search(pattern, low))
    under, leak, deny, churn = hit(CLAIMS_UNDERPAID), hit(CLAIMS_LEAK), hit(CLAIMS_DENIALS), hit(CLAIMS_CHURN)
    peers, integrity = hit(CLAIMS_PEERS), hit(CLAIMS_INTEGRITY)
    # Plain counts of received events ("how many reversals / overturned denials") stay literal: no estimation method needed.
    if re.search(r'^\s*(?:how many|count (?:of |the )?|number of|what (?:is|was) the (?:number|count|total) of)\b', low) and \
            not (under or leak or peers or integrity) and not re.search(r'\b(?:patterns?|reasons?|pareto|why)\b', low):
        return None
    # Plain money totals ("how much did payers pay, net of reversals") are literal sums: reversals there define the
    # amount, they do not ask for a review of rework or duplicates.
    if re.search(LITERAL_MONEY_TOTAL, low) and not (under or leak or peers or integrity or deny) and \
            not re.search(r'\b(?:patterns?|reasons?|pareto|why|flags?|review|at stake|duplicates?|suspicious)\b', low):
        return None
    if leak or (under and (deny or churn)) or (deny and churn):
        focus = 'leakage' if not (peers or integrity) else 'all'
    elif under: focus = 'underpayment'
    elif deny: focus = 'denials'
    elif churn: focus = 'duplicates'
    elif peers and not integrity: focus = 'providers'
    else: focus = 'all'
    params = {'focus': focus}
    for pattern in (CLAIMS_UNDERPAID, CLAIMS_LEAK, CLAIMS_DENIALS, CLAIMS_CHURN, CLAIMS_PEERS, CLAIMS_INTEGRITY):
        r.take(pattern, f'claims review {focus}')
    for pattern, by in ((r'\bby (?:payer|payor|plan|insurer)s?\b', 'payer'), (r'\bby (?:provider|practice|clinic|organi[sz]ation|facility)s?\b', 'provider'),
                        (r'\bby (?:service|service code|procedure)s?\b', 'service'), (r'\bby (?:setting|site type)s?\b', 'setting')):
        if r.take(pattern, f'by {by}'):
            params.setdefault('by', by)
    m = r.take(r'\b(?:payer|plan) ((?:wh-)?payer-\d+|[a-z]+-[a-z0-9_.-]*\d)\b|\b(wh-payer-\d+)\b', 'payer filter')
    if m: params['payer'] = (m[0].group(1) or m[0].group(2)).upper()
    m = r.take(r'\b(wh-(?:practice|lab|specialty|hospital|medcare|urgent|gi|diagnostic|[a-z]+)-\d+)\b', 'provider filter')
    if m: params['provider'] = m[0].group(1).upper()
    m = r.take(r'\$(\d+(?:\.\d+)?) (?:per|a|each|for each) (?:rework|touch|resubmission|correction|rejection)s?\b', 'rework cost')
    if m: params['rework_cost'] = float(m[0].group(1))
    m = r.take(r'\b(?:fdr|false discovery rate)(?: of| at)? (\d+(?:\.\d+)?)\s*(%|percent)?', 'fdr')
    if m: params['fdr'] = float(m[0].group(1)) / (100 if m[0].group(2) or float(m[0].group(1)) > 1 else 1)
    period = _take_period(r, as_of)
    if period:
        params['period'] = {'start': period[0], 'end': period[1]}
    elif r.take(r'\b(?:this|the current) year\b|\byear[- ]to[- ]date\b|\bytd\b|\bso far this year\b', 'this year'):
        params['period'] = {'start': f'{as_of[:4]}-01-01', 'end': as_of}
    elif r.take(r'\blast year\b|\bthe prior year\b', 'last year'):
        y = int(as_of[:4]) - 1
        params['period'] = {'start': f'{y}-01-01', 'end': f'{y}-12-31'}
    r.take(CLAIMS_FILLER, None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='claims review words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    from .claims_review import validate
    try:
        params = validate(params)
    except ValueError as exc:
        info.update(route='fallback', reason=f'claims_review parameters: {exc}')
        return None, info
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'claims_review', 'inputs': [], 'parameters': params,
                       'purpose': f"Claims review: {params['focus']}" + (f" by {params['by']}" if params['focus'] in ('denials', 'leakage', 'all') else '')}]}, info


# ---- CMS-HCC risk adjustment: RAF, coding gaps, recapture, suspects -------------------------
RA_RECAPTURE = (r"\bre-?captur(?:e|es|ed|ing)\b|\bcoded (?:last|in the prior|the previous) year\b|\bnot (?:yet )?(?:re)?(?:coded|captured) this year\b|"
                r"\bopen hcc gaps?\b|\bhcc gaps?\b")
RA_SUSPECT = (r"\bsuspect(?:ed|ing)? (?:conditions?|diagnos[ie]s|hccs?|list)\b|\bsuspects\b|\bsuspecting\b|\bdocumentation (?:gaps?|opportunit(?:y|ies)|review prompts?)\b|"
              r"\b(?:conditions?|diagnos[ie]s|evidence) (?:without|missing|lacking) (?:a |an |any )?(?:diagnosis |icd(?:-10)? )?codes?\b|"
              r"\bundocumented (?:conditions?|diagnos[ie]s)\b|\buncoded (?:conditions?|diagnos[ie]s)\b")
RA_CORE = (r"\bhccs?\b|\brafs?\b|\brisk adjustment factors?\b|\bcms[- ]?hcc\b|\bhierarchical condition categor(?:y|ies)\b|\bv(?:24|28)\b|"
           r"\bcoding (?:gaps?|completeness|opportunit(?:y|ies))\b|\brisk[- ]adjust(?:ed|ment) (?:revenue|payments?|dollars)\b|"
           r"\b(?:cms|hcc|medicare advantage|ma|aco reach) risk scores?\b|\brisk scores? (?:for|under|in) (?:medicare advantage|ma|aco reach)\b")
RA_TRIGGER = '|'.join((RA_CORE, RA_RECAPTURE, RA_SUSPECT))
RA_FILLER = (r"\b(?:which|what|who|whose|where|how|show|list|give|me|find|our|the|a|an|of|for|is|are|was|were|have|has|had|with|by|per|each|"
             r"patients?|members?|people|average|mean|median|typical|distribution|spread|score|scores|risk|adjustment|adjusted|coded|coding|code|codes|"
             r"diagnosis|diagnoses|conditions?|gaps?|open|last|this|year|yet|not|still|missing|need|needs|needing|review|documentation|evidence|"
             r"without|any|top|biggest|largest|most|dollars?|value|at stake|worth|how much|money|revenue|opportunit(?:y|ies)|rate|completeness|"
             r"across|panel|population|current|prior|previous|we|us|do|does|did|in|on|to|from|be|been|and|or|whole|overall|status|"
             r"cms|hcc|hccs|raf|rafs|payment|medicare advantage|medicare|ma|aco reach|reach|v24|v28|model|blended|normali[sz]ed|captured|capture)\b")


def _diagnoses_received(profile):
    """ICD-10 diagnosis codes arrive on medical claims (warehouse v2.0+); without them HCCs cannot be assigned."""
    fields = (((profile or {}).get('streams') or {}).get('medical_claims') or {}).get('fields') or {}
    return 'diagnosis_codes' in fields or 'principal_diagnosis' in fields


def _risk_adjustment_question(q, r, profile, as_of, info):
    low = r.low
    if not re.search(RA_TRIGGER, low) or not _diagnoses_received(profile): return None
    recap, suspect = bool(re.search(RA_RECAPTURE, low)), bool(re.search(RA_SUSPECT, low))
    params = {}
    gb = next((g for pattern, g in ((r'\bby (?:practice|clinic)s?\b|\bper (?:practice|clinic)\b|\beach (?:practice|clinic)\b', 'practice'),
                                    (r'\bby (?:pcp|primary care (?:provider|physician)|physician|provider|doctor|clinician)s?\b|\bper pcp\b', 'pcp'),
                                    (r'\bby (?:payer|payor|plan|insurer)s?\b', 'payer'), (r'\bby (?:coverage|insurance)(?: type)?\b', 'coverage_type'))
               if r.take(pattern, f'group by {g}')), None)
    if gb: params['group_by'] = gb
    if recap and suspect or re.search(r'\bcoding (?:gaps?|opportunit(?:y|ies))\b', low) and not (recap or suspect): focus = 'all'
    elif recap: focus = 'recapture'
    elif suspect: focus = 'suspects'
    elif gb or re.search(r'\bdistribution\b|\bcompleteness\b|\bspread\b', low): focus = 'population'
    else: focus = 'raf'
    params['focus'] = focus
    for pattern in (RA_RECAPTURE, RA_SUSPECT, RA_CORE):
        r.take(pattern, f'risk adjustment {focus}')
    m = r.take(r'\b(?:py|payment year) ?(20\d\d)\b|\bfor (20\d\d) payments?\b', 'payment year')
    if m: params['payment_year'] = int(m[0].group(1) or m[0].group(2))
    m = r.take(r'\$\s?([\d,]+(?:\.\d+)?) ?(?:pmpm|per member per month)\b|\bbase rate (?:of |is |at )?\$?\s?([\d,]+(?:\.\d+)?)(?: ?pmpm)?', 'base rate')
    if m: params['base_rate_pmpm'] = float((m[0].group(1) or m[0].group(2)).replace(',', ''))
    r.take(r'\bthis (?:calendar )?year\b(?: \(?\d{4} (?:ytd|year[- ]to[- ]date)\)?)?|\(?\b\d{4} (?:ytd|year[- ]to[- ]date)\)?|\bytd\b|'
           r'\bso far\b|\bto date\b', 'current year (recapture runs to the snapshot date)')
    m = r.take(r'\b(?:in|for|during) (20\d\d)\b', 'collection year')
    if m: params['year'] = int(m[0].group(1))
    if r.take(r'\b(?:all|every|whole|entire) (?:patients|population|members|roster)\b|\beveryone\b|\bcommercial\b|\ball coverage\b', 'coverage all'):
        params['coverage'] = 'all'
    m = r.take(r'\b(p-[a-z0-9]+|syn-[a-z0-9]+-[a-z0-9]+)\b', 'patient id')
    if m: params['patient'] = m[0].group(1).upper()
    m = r.take(r'\btop (\d{1,3})\b', 'top')
    if m: params['top'] = int(m[0].group(1))
    if re.search(r'\b(?:vs\.?|versus|compared (?:with|to)|than (?:those|patients|members)|those who (?:did|do|have|had) not)\b', low) and not gb:
        # "RAF of patients who had an AWV vs those who did not": a two-group comparison the RAF report does not split by
        info.update(route='fallback', reason='risk adjustment comparison between groups not interpreted locally')
        return None, info
    r.take(RA_FILLER, None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='risk adjustment words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    from .risk_adjustment import validate
    try:
        params = validate(params)
    except ValueError as exc:
        info.update(route='fallback', reason=f'risk_adjustment parameters: {exc}')
        return None, info
    if params['year'] is not None and params['year'] > int(as_of[:4]):
        info.update(route='fallback', reason='risk_adjustment collection year after the snapshot')
        return None, info
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'risk_adjustment', 'inputs': [], 'parameters': params,
                       'purpose': f"Risk adjustment: {params['focus']}" + (f" by {params['group_by']}" if params['group_by'] != 'none' else '')}]}, info


# ---- markets view: practice / PCP / payer tickers ------------------------------------------
MARKET_TRIGGER = (r"\bwhat(?:'s| has| have)? (?:moved|changed (?:the )?most)\b|\bmarkets? (?:view|board|map|summary)\b|\btickers?\b|"
                  r"\b(?:top |biggest |largest )?movers\b|\btrending (?:worse|better|up|down)\b|"
                  r"\bmost (?:surprising|unexpected) (?:changes?|moves?|shifts?)\b|\bbiggest (?:moves?|swings?)\b")
MARKET_UNITS = [(r'\b(?:pcps?|primary care (?:physicians?|providers?|doctors?|clinicians?)|doctors?|physicians?|providers?|clinicians?)\b', 'pcp'),
                (r'\b(?:payers?|payors?|health plans?|plans?|insurers?|coverage types?)\b', 'payer'),
                (r'\b(?:practices?|clinics?)\b', 'practice')]
MARKET_METRICS = [('ed_rate', r'\b(?:ed|er|emergency(?: department| room)?)(?: (?:use|visits?|utili[sz]ation|rates?))?\b'),
                  ('admit_rate', r'\b(?:inpatient )?(?:admissions?|admits?|hospitali[sz]ations?)(?: rates?)?\b'),
                  ('pmpm', r'\bpmpm\b|\bper member per month\b|\b(?:total |medical |allowed )?(?:costs?|spend(?:ing)?)\b'),
                  ('a1c_poor', r'\b(?:hb)?a1c(?: (?:poor[- ])?control)?\b|\b(?:diabetes|glycemic) control\b'),
                  ('bp_control', r'\b(?:bp|blood pressure)(?: control)?\b|\bhypertension control\b'),
                  ('denial_rate', r'\b(?:claims? )?denials?(?: rates?)?\b'),
                  ('no_show_rate', r'\bno[- ]shows?(?: rates?)?\b|\bmissed appointments?\b')]


def _market_question(q, r, profile, as_of, info):
    low = r.low
    if not re.search(MARKET_TRIGGER, low): return None
    params = {}
    if r.take(r'\b(?:worse|worsening|deteriorat\w*|declining|slipping)\b', 'direction worse'): params['direction'] = 'worse'
    elif r.take(r'\b(?:better|improv\w*)\b', 'direction better'): params['direction'] = 'better'
    r.take(MARKET_TRIGGER, 'market view', overlap=True)
    for pattern, unit in MARKET_UNITS:
        if r.take(pattern, f'unit {unit}'): params.setdefault('unit', unit)
    metrics = []
    if r.take(r'\bcare[- ]gaps?(?: closure)?\b|\bgap closure\b|\bquality (?:measures?|gaps?)\b', 'care-gap closure'):
        metrics += ['a1c_poor', 'bp_control']
    for m, pattern in MARKET_METRICS:
        if r.take(pattern, f'metric {m}') and m not in metrics: metrics.append(m)
    if metrics: params['metrics'] = metrics
    period = _take_period(r, as_of)
    if period: params['period'] = {'end': period[1]}
    r.take(r"\b(?:this|last|the latest|the last|past|previous) month\b|\b(?:give|show|tell|let)(?: me| us)?\b|\bnetwork(?:[- ]wide)?\b|"
           r"\b(?:what|which|who|where|are|is|was|were|has|have|been|the|a|an|our|my|of|for|on|and|in|across|by|per|each|all|every|most|"
           r"surprising|unexpected|changes?|moved?|moves?|moving|trending|trend|up|down|top|biggest|largest|view|board|rates?|see|today|"
           r"lately|recently|now|right|me|us|how|did|do|does|any|anything|month|monthly|panel|use|visits?|to|vs|versus|over|time|with)\b",
           None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    # Strict: a movers question with words the market view cannot place ("the zebra feed") is declined, not guessed.
    unclear = info['unexplained'] + [w for w in info['ignored_words'] if w not in ('among', 'please', 'between', 'whole', 'kind')]
    if unclear:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(unclear[:12]))
        return None, info
    info['route'] = 'local_compiler'
    from .market import validate
    params = validate(params)
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'market', 'inputs': [], 'parameters': params,
                       'purpose': f"Market view: {params['unit']} tickers" + (f" for {', '.join(params['metrics'])}" if params['metrics'] else '')
                                  + (f", moves {params['direction']}" if params['direction'] != 'any' else '')}]}, info


def _take_period(r, as_of, year_words=r'in|during|for'):
    """Time windows in analyst language -> (start, end) ISO dates, consuming the words. None when no period is named."""
    period = None
    for m in r.take(r'\b(?:from|between) (\d{4}) (?:through|thru|to|and|until|-|–) (?:ytd |year[- ]to[- ]date )?(\d{4})(?: ytd| year[- ]to[- ]date| to date| so far)?\b', 'year range'):
        period = (f'{m.group(1)}-01-01', min(f'{m.group(2)}-12-31', as_of))
    for m in r.take(r'\b(?:so far in|year[- ]to[- ]date in|ytd in) (\d{4})\b|\b(\d{4}) (?:year[- ]to[- ]date|ytd|to date|so far)\b|\bin (\d{4}) (?:to date|so far)\b', 'year to date'):
        y = next(g for g in m.groups() if g); period = (f'{y}-01-01', min(f'{y}-12-31', as_of))
    for m in r.take(r'\b(?:in|during) ' + MONTH_RX + r' (\d{4})\b', 'month'):
        mo, y = _month_number(m.group(1)), m.group(2)
        import calendar
        period = (f'{y}-{mo:02d}-01', f'{y}-{mo:02d}-{calendar.monthrange(int(y), mo)[1]:02d}')
    for m in r.take(r'\b(?:in |during |for |over )?(?:the )?(?:q([1-4])|(first|second|third|fourth|1st|2nd|3rd|4th) quarter(?: of)?) (\d{4})'
                    r'(?: \(?' + MONTH_RX + r'\s*(?:-|–|to|through)\s*' + MONTH_RX + r'\)?)?', 'quarter'):
        n = int(m.group(1)) if m.group(1) else ['first', 'second', 'third', 'fourth'].index({'1st': 'first', '2nd': 'second', '3rd': 'third', '4th': 'fourth'}.get(m.group(2), m.group(2))) + 1
        y = m.group(3); end = [f'{y}-03-31', f'{y}-06-30', f'{y}-09-30', f'{y}-12-31'][n - 1]
        period = (f'{y}-{3 * n - 2:02d}-01', min(end, as_of))
    for m in r.take(r'\b(?:in |during |for |over )?(?:the )?(?:(first|second) half(?: of)?|h([12])) (\d{4})\b', 'half year'):
        first = m.group(1) == 'first' or m.group(2) == '1'; y = m.group(3)
        period = (f'{y}-01-01', min(f'{y}-06-30', as_of)) if first else (f'{y}-07-01', min(f'{y}-12-31', as_of))
    for m in r.take(r'\b(?:since|from) (?:the (?:start|beginning) of )?(?:' + MONTH_RX + r'(?: (\d{1,2})(?:st|nd|rd|th)?)?,? )?(\d{4})'
                    r'(?: (?:through|thru|to|until) (?:today|now|date|the present))?\b(?! (?:to|through|thru|-|–) \d)', 'since'):
        mo = _month_number(m.group(1)) if m.group(1) else 1
        period = (f'{m.group(3)}-{mo:02d}-{int(m.group(2) or 1):02d}', as_of)
    for m in r.take(r'\b(?:' + year_words + r') (\d{4})\b', 'year'):
        y = m.group(1); period = period or (f'{y}-01-01', min(f'{y}-12-31', as_of))
    return period


# ---- population questions (concepts, aggregates, explanations) ------------------
def _population_question(q, r, profile, as_of, info):
    # Age as captured on the roster only when the question says so; otherwise the latest reported age.
    AGE_FIELD = 'age_reported' if re.search(r'\b(?:at (?:their |the )?profile capture|as captured|roster age|without advancing|originally reported)\b', q.lower()) else 'age_latest'
    low = r.low
    allp, excl = [], []
    # statistic / intent
    hotspot = r.take(r'\b(?:where (?:is|are)\b(?=.*\bconcentrated\b)|concentrated|concentrations?|unusually (?:high|low|common)|hot ?spots?|outliers?|clusters?|'
                     r'patterns? (?:of|in)|stands? out|which (?:groups?|segments?|populations?|subgroups?)(?= (?:have|has|show)))\b', 'discovery')
    projected = next(iter(r.take(r'\b(?:(?:are |is )?(?:likely|projected|expected|predicted|forecast|on track|at risk|going) (?:to )?|will )'
                                 r'(?:drop|fall|go|decline|slip|rise|climb|exceed|cross|reach|hit)\b', 'forecast')), None)
    forecast_target = None
    if projected:  # "drop below an eGFR of 30", "exceed an A1c of 9", "fall under 30 on eGFR"
        verb = projected.group(0)
        for concept, aliases in MEASUREMENT_ALIASES.items():
            for alias in sorted(aliases, key=len, reverse=True):
                m = re.compile(r'\s*(below|under|above|over|past)?\s*(?:an? |the |their )?' + re.escape(alias) +
                               r'(?: level| value| result)?\s*(?:of\s*)?' + NUMBER + r'(?![\d.])').match(low, projected.end())
                if m:
                    word = (m.group(1) or '') + ' ' + verb
                    op = 'lt' if re.search(r'below|under|drop|fall|declin|slip', word) else 'gt'
                    forecast_target = (concept, op, float(m.group(2)))
                    for i in range(m.start(), m.end()): r.used[i] = True
                    r.notes.append(f'forecast {concept} {op} {m.group(2)}')
                    break
            if forecast_target: break
    horizon = None
    for m in r.take(r'\b(?:in|within|over|during) (?:the )?(?:next |coming )?(\d+|a|one|two|three|six|twelve|eighteen) ?(days?|weeks?|months?|years?|yr)\b'
                    r'|\b(?:in|within|over|during) the (?:next|coming) (year|quarter|month|six months)\b|\bnext (year|quarter)\b', 'horizon'):
        words = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'six': 6, 'twelve': 12, 'eighteen': 18}
        if m.group(1):
            n = int(m.group(1)) if m.group(1).isdigit() else words[m.group(1)]
            unit = m.group(2)
        else:
            phrase = m.group(3) or m.group(4)
            n, unit = {'year': (1, 'year'), 'quarter': (3, 'month'), 'month': (1, 'month'), 'six months': (6, 'month')}[phrase]
        horizon = int(n * {'d': 1, 'w': 7, 'm': 30.44, 'y': 365.25}[unit[0]])
    trend = next(iter(r.take(r'\b(?:declin(?:e|es|ed|ing)(?: in)?|dropp(?:ed|ing)|fall(?:ing|en)|worsen(?:ing|ed)|deteriorat(?:ing|ed|ion)(?: in| of)?|'
                             r'ris(?:ing|en)|increas(?:ing|ed)|climb(?:ing|ed)|improv(?:ing|ed)|trend(?:ing)? (?:up|down)|trajector(?:y|ies)(?: of)?)\b', 'trend')), None)
    hot_segments = None
    if hotspot:
        named = {'practice': r'\b(?:practices?|clinics?)\b', 'pcp': r'\b(?:pcps?|physicians?|providers?|clinicians?)\b',
                 'coverage_type': r'\b(?:payers?|plans?|insurers?)\b'}
        hot_segments = [g for g, pattern in named.items() if r.take(pattern, f'segment {g}')] or None
        r.take(r'\b(?:find|show|identify|look for|detect|any)\b', None)
    why = r.take(r'\b(?:why|what drives|what is driving|drivers? of|factors? (?:associated with|behind|for)|what distinguishes|contribut\w+ to|explain)\b', 'why')
    share = r.take(r'(?:\b(?:what share|share|what percent(?:age)?|percentage|proportion|fraction)\b|\bwhat ?%|\bpercent of\b)(?: of)?', 'share')
    mean = r.take(r'\b(?:average|mean)\b', 'mean')
    median = r.take(r'\bmedian\b', 'median')
    rate = r.take(r'\b(?:rates?|per 1,?000|utili[sz]ation)\b', 'rate')
    count = r.take(r'\b(?:how many|number of|count)\b', 'count')
    which = r.take(r'\b(?:which|list|who are the|identify)\b', 'patient list') or info.get('output') == 'patient_set'
    # "latest 2029 A1c", "their last 2029 reading": the latest value within that year
    latest_year = next(iter(r.take(r'\b(?:latest|most recent|last|final) ((?:19|20)\d{2})\b', 'latest value in year')), None)
    latest = r.take(r'\b(?:latest|most recent|last)\b', 'latest value') or ([latest_year] if latest_year else [])
    adjust = r.take(r'\b(?:risk[- ]adjusted|case[- ]mix(?: adjusted)?|adjusted(?: for case mix)?|fair(?:ly)? compar\w+)\b', 'case-mix adjustment')
    readmit = r.take(r'\b(?:30[- ]day )?readmi(?:ssions?|tted|t)\b(?: within 30 days)?', 'readmission')
    r.take(r'\bwithin 30 days\b')
    highest = r.take(r'\b(?:highest|lowest|top|worst|best|most|fewest)\b', 'ranking')
    period = _take_period(r, as_of)
    if latest_year and not period:
        y = latest_year.group(1); period = (f'{y}-01-01', min(f'{y}-12-31', as_of))
    group = None
    for pattern, g in GROUPS + _demographic_groups(profile):
        if r.take(pattern, f'group by {g}'): group = g; break
    if not group and highest:
        for pattern, g in [(r'\b(?:pcps?|physicians?|providers?|clinicians?)\b', 'pcp'), (r'\b(?:practices?|clinics?)\b', 'practice'),
                           (r'\b(?:hospitals?|facilit(?:y|ies))\b', 'facility')]:
            if r.take(pattern, f'group by {g}'): group = g; break
    # demographics: an age needs explicit age context, never a bare number ("over 5.5")
    age_patterns = [
        (r'\bover (?:the age of |age )(\d{1,3})(?![\d.])', 'gt'), (r'\bunder (?:the age of |age )(\d{1,3})(?![\d.])', 'lt'),
        (r'\b(?:aged|age(?:d)? (?:of )?|ages )(at least|over|above|under|below|older than|younger than)?\s*(\d{1,3})(?![\d.])(?:\s*(?:years?(?: old)?|\+|or older|and older|or over|or younger|and under))?', None),
        (r'\b(\d{1,3})(?![\d.])\s*(?:\+|years? (?:old )?(?:or|and) (?:older|over)|or older|and older|and over|or over|and up)', 'ge'),
        (r'(?<=kids |dren |nors |tric )(?:under|below|younger than) (\d{1,2})(?![\d.])', 'lt'),
        (r'\b(?:patients?|members|people) (?:under|below|younger than) (\d{1,3})(?![\d.])', 'lt'),
        (r'\b(?:patients?|members|people) (?:over|above|older than) (\d{1,3})(?![\d.])', 'gt'),
        (r'\b(\d{1,3})(?![\d.])\s*(?:years? (?:old )?(?:or|and) (?:younger|under)|or younger|and under)', 'le'),
        (r'\b(older|younger) than (\d{1,3})(?![\d.])', None),
        (r'\breported age (at least|over|above|under|below) (\d{1,3})(?![\d.])', None)]
    for m in [m for m in re.finditer(r'\(?(\d{1,2}) ?(?:to|-|through|and) ?(\d{1,3})\)?', low)
              if re.search(r'\b(?:age[ds]?|aged|years? old|reproductive)\b', low[max(0, m.start() - 30):m.end() + 15])
              and not any(r.used[m.start():m.end()])]:
        for i in range(m.start(), m.end()): r.used[i] = True
        r.notes.append('age range')
        allp.append({'kind': 'patient', 'field': AGE_FIELD, 'op': 'ge', 'value': int(m.group(1))})
        allp.append({'kind': 'patient', 'field': AGE_FIELD, 'op': 'le', 'value': int(m.group(2))})
        r.take(r'\breproductive age\b|\bof reproductive age\b', None, overlap=True)
    if r.take(r'\b(?:kids|children|child|pediatric|paediatric|minors)\b', 'children') and not any(a.get('field') in ('age_reported', 'age_latest') for a in allp):
        allp.append({'kind': 'patient', 'field': AGE_FIELD, 'op': 'lt', 'value': 18})
    if r.take(r'\b(?:seniors|elderly|older adults|geriatric)\b', 'seniors') and not any(a.get('field') in ('age_reported', 'age_latest') for a in allp):
        allp.append({'kind': 'patient', 'field': AGE_FIELD, 'op': 'ge', 'value': 65})
    words_to_op = {'at least': 'ge', 'over': 'gt', 'above': 'gt', 'older than': 'gt', 'older': 'gt', 'under': 'lt', 'below': 'lt',
                   'younger than': 'lt', 'younger': 'lt'}
    for pattern, fixed in age_patterns:
        for m in r.take(pattern, 'age'):
            groups = [g for g in m.groups() if g]
            n = int(next(g for g in groups if g.isdigit()))
            word = next((g for g in groups if not g.isdigit()), None)
            op = fixed or words_to_op.get(word, 'ge' if re.search(r'older|\+|least', m.group(0)) else 'eq' if not word else 'ge')
            if re.search(r'or older|and older|\+|or over', m.group(0)): op = 'ge'
            if re.search(r'or younger|and under', m.group(0)): op = 'le'
            allp.append({'kind': 'patient', 'field': AGE_FIELD, 'op': op, 'value': n})
    if r.take(r'\b(?:female|women|woman)\b(?: reported sex)?', 'female'): allp.append({'kind': 'patient', 'field': 'sex_reported', 'op': 'eq', 'value': 'F'})
    if r.take(r'\b(?:male|men|man)\b(?: reported sex)?', 'male'): allp.append({'kind': 'patient', 'field': 'sex_reported', 'op': 'eq', 'value': 'M'})
    r.take(r'\breported (?:sex|age)\b|\bat (?:their )?profile capture\b|\bwithout advancing (?:that|the) age(?: to today)?\b')
    for phrase, payer in PAYERS.items():
        if r.take(_alias_pattern(phrase) + r'(?: coverage| members?| patients?| population| plan)?', f'payer {payer}'):
            allp.append({'kind': 'record', 'stream': 'coverage', 'conditions': [{'field': 'coverage_type', 'op': 'eq', 'value': payer}], 'window': W})
    # literal record predicates: a received stream named with one of its observed values
    for s_name, forms in sorted(_stream_vocabulary(profile).items(), key=lambda kv: -max(map(len, kv[1]))):
        if s_name in ('conditions', 'labs', 'vitals', 'encounters', 'coverage', 'social_screens', 'financial_screens'): continue
        fields = profile['streams'][s_name]['fields']
        for form in forms:
            for sm in re.finditer(_alias_pattern(form), low):
                window = low[max(0, sm.start() - 25):sm.end() + 45]
                hit = next(((f, v) for f, spec in fields.items() for v in sorted(spec.get('values') or {}, key=len, reverse=True)
                            if len(v) >= 3 and '_' in v and re.search(_alias_pattern(v), window)), None)
                if not hit: continue
                cond = [{'field': hit[0], 'op': 'eq', 'value': hit[1]}]
                r.take(_alias_pattern(form), f'stream {s_name}'); r.take(_alias_pattern(hit[1]), f'{hit[0]} = {hit[1]}')
                dm = re.search(r'with ([a-z_]+_date) in (\d{4})', window)
                if dm and dm.group(1) in fields:
                    cond.append({'field': dm.group(1), 'op': 'prefix', 'value': dm.group(2)})
                    r.take(r'with ' + dm.group(1) + r' in ' + dm.group(2), 'date year', overlap=True)
                    if period and period[0][:4] == dm.group(2): period = None  # the year belongs to this field, not a global window
                atom = {'kind': 'record', 'stream': s_name, 'conditions': cond, 'window': W}
                (excl if _negated(low, sm.start()) else allp).append(atom)
    # social-screen flags (true unless "as false"/"false")
    need_groups = []  # a social need asked in plain words matches either screening instrument

    def need_atoms(stream, field, value, start, literal):
        atom = {'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': value}], 'window': W}
        extra = [] if literal or value is not True else [
            {'kind': 'record', 'stream': s2, 'conditions': [{'field': f2, 'op': 'eq', 'value': v2}], 'window': W}
            for s2, f2, v2 in NEED_INSTRUMENTS.get(field, []) if s2 in profile['streams'] and f2 in profile['streams'][s2]['fields']]
        if _negated(low, start) or not extra: (excl if _negated(low, start) else allp).append(atom)
        else: need_groups.append([atom] + extra)

    for phrase, (stream, field) in SOCIAL_FLAGS.items():
        for m in r.take(_alias_pattern(phrase), f'{field}'):
            tail = low[m.end():m.end() + 25]
            value = not re.match(r'\s*(?:as |is |reported )?false', tail)
            literal = bool(re.match(r'\s*(?:as |is |reported )?(?:true|false)', tail)) or 'screen reporting' in low[max(0, m.start() - 30):m.start()]
            r.take(r'\b(?:as )?(?:true|false)\b')
            need_atoms(stream, field, value, m.start(), literal)
    for pattern, (stream, field) in SOCIAL_PHRASES:
        for m in r.take(pattern, field):
            need_atoms(stream, field, True, m.start(), False)
    for m in r.take(r'\b(?:missed|no[- ]showed?(?: for)?|skipped) (?:any |an |a )?(?:appointments?|appts?|visits?)\b|\bno[- ]shows?\b', 'missed appointment'):
        if 'appointments' in profile['streams'] and 'no_show' in (profile['streams']['appointments']['fields'].get('status', {}).get('values') or {}):
            atom = {'kind': 'record', 'stream': 'appointments', 'conditions': [{'field': 'status', 'op': 'eq', 'value': 'no_show'}], 'window': W}
            (excl if _negated(low, m.start()) else allp).append(atom)
        else:
            info.update(route='fallback', reason='no appointment no-show status is received'); return None, info
    r.take(r'\b(?:have|has|had) both\b|\bboth\b', None)
    r.take(r'\b(?:any |a |on a )?(?:social|financial) screens?\b(?: explicitly)?(?: reporting)?|\bscreened positive(?: for)?\b|\btold us\b', None, overlap=True)
    # literal labels: "lab named eGFR", "result named Hemoglobin A1c or Hemoglobin A1c/Hemoglobin.total"
    measured = []
    for m in r.take(r'\b(?:any )?(?:numeric )?(?:percent-unit )?(?:numeric )?(?:labs?|results?|tests?|a1c result) named ((?:(?! or | below| above| over| under| less| greater| at least| with|[?,]).)+)(?: or ((?:(?! below| above| over| under| less| greater| at least| with|[?,]).)+))?', 'literal lab names'):
        names = [n.strip().rstrip('.') for n in m.groups() if n]
        concept = next((concepts.concept_for_label(n) for n in names if concepts.concept_for_label(n)), None)
        if concept is None:
            info.update(route='fallback', reason=f'unrecognised lab label {names}'); return None, info
        tail = low[m.end():m.end() + 45]
        cmp = None
        for pattern, op in COMPARATORS:
            mm = re.match(r'\s*' + pattern + r'\s*' + NUMBER, tail)
            if mm:
                cmp = (op, float(mm.group(1)))
                for i in range(m.end(), m.end() + mm.end()): r.used[i] = True
                break
        allp.append({'kind': 'measurement', 'concept': concept, 'names': names, 'op': cmp[0] if cmp else 'gt',
                     'value': cmp[1] if cmp else -1e12, 'aggregate': 'latest' if latest else 'any', 'window': W})
    r.take(r'\bpercent-unit\b', None)
    # measurements with comparisons. Longest alias first across concepts: 'albumin-to-creatinine ratio' is uacr, not creatinine.
    for concept, aliases in MEASUREMENT_ALIAS_ORDER:
        for alias in aliases:
            for m in r.take(_alias_pattern(alias), f'measurement {concept}'):
                tail = low[m.end():m.end() + 60]
                cmp = None
                gap = r'\s*(?:(?:has|have|had|ever|any|a|an|the|results?|values?|levels?|readings?|of|is|was|were|on any draw|draws?)\s+)*'
                for pattern, op in COMPARATORS:
                    mm = re.match(gap + pattern + r'\s*' + NUMBER, tail)
                    if mm:
                        cmp = (op, float(mm.group(1)))
                        for i in range(m.end(), m.end() + mm.end()): r.used[i] = True
                        break
                if cmp is None:
                    mm = re.match(gap + NUMBER + r'\s*(?:[a-z%/.0-9]+\s*)?(or (?:higher|more|above|greater|over)|and (?:above|higher|over)|\+|'
                                  r'or (?:lower|less|below|under)|and (?:below|under))', tail)
                    if mm:
                        cmp = ('ge' if re.search(r'higher|more|above|greater|over|\+', mm.group(2)) else 'le', float(mm.group(1)))
                        for i in range(m.end(), m.end() + mm.end()): r.used[i] = True
                measured.append((concept, cmp, m.start()))
    measured.sort(key=lambda x: MEASUREMENT_RANK[x[0]])  # stable: keep the concept order atoms always had
    position = {}  # where each atom is mentioned: the last-mentioned subject of a share is its outcome
    for concept, cmp, start in measured:
        if cmp:
            atom = {'kind': 'measurement', 'concept': concept, 'op': cmp[0], 'value': cmp[1],
                    'aggregate': 'latest' if latest else 'any', 'window': W}
        else:
            atom = {'kind': 'concept', 'concept': concept, 'window': W}
        position[id(atom)] = start
        (excl if _negated(low, start) else allp).append(atom)
    r.take(r'\b(?:percent|%|mmol/l|mg/dl|ml/min/1\.73\s?m2|mmhg|pg/ml|ng/l)\b')
    # encounters (as population filters, or as the counted event)
    events, visit_atoms = [], []
    for concept, aliases in ENCOUNTER_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for m in r.take(_alias_pattern(alias), f'encounter {concept}'):
                before = low[max(0, m.start() - 40):m.start()]
                done = re.search(r'\bcompleted\s+(?:[a-z0-9-]+\s+)?$', before)
                if done:
                    r.take(r'\bcompleted(?=\s+(?:[a-z0-9-]+\s+)?' + re.escape(alias) + r')', 'completed', overlap=True)
                # Counted ("how many ED visits") vs a condition on patients ("patients who had any ED visit").
                counted = re.search(r'(?:how many|number of|count(?: of)?|total|tally of|volume of)\s+(?:[a-z0-9-]+\s+){0,3}$', before) \
                    and not re.search(r'\b(?:patients?|members?|people|persons?)\b', before.split('how many')[-1].split('number of')[-1])
                predicate = not counted and re.search(r'\b(?:had|have|has|having|with|any|an?|no|without|got|getting|made|making)\s+(?:[a-z0-9-]+\s+){0,2}$', before)
                if predicate:
                    atom = {'kind': 'concept', 'concept': concept, 'window': W, **({'status': 'completed'} if done else {})}
                    position[id(atom)] = m.start()
                    visit_atoms.append(atom)
                    (excl if _negated(low, m.start()) else allp).append(atom)
                else:
                    events.append((concept, m.start(), bool(done)))
    r.take(r'\b(?:including|incl\.?) (?:diabetic )?complications?\b', 'includes complications', overlap=True)
    # conditions
    conds = []
    for concept, aliases in sorted(CONDITION_ALIASES.items(), key=lambda kv: -max(map(len, kv[1]))):
        for alias in sorted(aliases, key=len, reverse=True):
            for m in r.take(_alias_pattern(alias), f'condition {concept}'):
                conds.append((concept, m.start(), m.end()))
    r.take(r'\b(?:including|incl\.?) (?:diabetic )?complications?\b|\bdiabetic[- ]complications?\b|\bfamily history\b|\bicd-?10\b|\bsnomed\b')
    anyp, or_gaps = [], []
    if len(need_groups) > 1:
        info.update(route='fallback', reason='more than one multi-instrument social need'); return None, info
    ordered = sorted(conds, key=lambda c: c[1])
    chains, chain = [], [ordered[0]] if ordered else []
    for prev, cur in zip(ordered, ordered[1:]):
        gap = low[prev[2]:cur[1]]
        joined = re.fullmatch(r'\s*(?:,\s*)?(?:or|and/or|,)\s*(?:an?\s+|any\s+)?', gap)
        if joined and _negated(low, prev[1]) and _negated(low, cur[1]):  # "no A or B": both excluded
            or_gaps.append((prev[2], cur[1])); chains.append(chain); chain = [cur]
        elif joined and not _negated(low, cur[1]):
            chain.append(cur); or_gaps.append((prev[2], cur[1]))
        else:
            chains.append(chain); chain = [cur]
    if chain: chains.append(chain)
    or_chains = [c for c in chains if len(c) > 1 and re.search(r'\bor\b', low[c[0][1]:c[-1][2]])]
    if len(or_chains) + len(need_groups) > 1:
        info.update(route='fallback', reason='more than one either/or group'); return None, info
    if need_groups: anyp.extend(need_groups[0])
    in_or = {c[1] for ch in or_chains for c in ch}
    for concept, start, _end in conds:
        atom = {'kind': 'concept', 'concept': concept, 'window': W}
        position[id(atom)] = start
        system = re.search(r'\b(snomed|icd-?10)\b(?:\s+[a-z-]+){0,2}\s*$', low[max(0, start - 30):start])
        if system:
            atom['system'] = 'snomed' if system.group(1) == 'snomed' else 'icd10'
            r.take(r'\b' + re.escape(system.group(1)) + r'\b', 'coding system', overlap=True)
        elif re.search(CODED_BOTH, low):  # "across ICD-10 and SNOMED": coded diagnoses only, not free-text mentions
            atom['system'] = 'coded'
        target = anyp if start in in_or else excl if _negated(low, start) else allp
        if atom not in target: target.append(atom)
    r.take(r'\beither\b', None)
    # Every remaining "or" must be one Agere actually combined as either/or, or part of a fixed phrase.
    for m in re.finditer(r'\bor\b', low):
        if any(a <= m.start() < b for a, b in or_gaps) or r.used[m.start()]: continue  # combined, or inside an explained phrase
        if re.match(r'or (?:higher|above|more|greater|lower|less|below|older|younger|over|under|equal|worse|better|not|fewer|after|before|later|earlier)\b', low[m.start():]) \
                or re.search(r'(?:one|1|two|2|three|3|any|dated|positive|either) $', low[max(0, m.start() - 12):m.start()]) \
                or re.search(r'(?:icd-?10|snomed|codes?|labels?|text|description|written|coded) $', low[max(0, m.start() - 14):m.start()]):
            continue
        info.update(route='fallback', reason='"or" joins terms that were not combined as either/or'); return None, info
    # "poor glycemic control (latest 2029 A1c above 9%)": a parenthetical whose words were all interpreted and that
    # carries a threshold defines the label right before it; the label words restate it and add no constraint.
    for pm in re.finditer(r'\(([^()]*\d[^()]*)\)', low):
        inner_words = list(re.finditer(r"[a-z0-9][a-z0-9'_.-]*", low[pm.start(1):pm.end(1)]))
        if not inner_words or any(not any(r.used[pm.start(1) + w.start():pm.start(1) + w.end()]) and w.group(0) not in FILLER
                                  for w in inner_words):
            continue
        label = []
        for w in reversed(list(re.finditer(r"[a-z][a-z'-]*", low[:pm.start()]))):
            if len(label) == 4 or any(r.used[w.start():w.end()]) or w.group(0) in (
                    'had', 'have', 'has', 'with', 'who', 'are', 'is', 'were', 'was', 'of', 'among', 'and', 'or', 'in', 'patients', 'people'):
                break
            if low[w.end():(label[-1].start() if label else pm.start())].strip(): break  # only whitespace between label words
            label.append(w)
        if label:
            for w in label:
                for i in range(w.start(), w.end()): r.used[i] = True
            r.notes.append('label defined in parentheses: ' + ' '.join(w.group(0) for w in reversed(label)))
    # Negation words are explained only by an exclusion built from them; otherwise they stay unexplained so the
    # compiler declines ("rows that have no result date" must never become the whole roster).
    if excl:
        r.take(r'\b(?:no|not|without|but no|lack|lacking|excluding|exclude|except)\b')
    r.take(r'\b(?:but|have|has|had|with|and|or)\b')
    info['unexplained'] = r.blocking(profile)
    # Counting rows/entries/line items is a record count, never a patient count from this cohort path.
    for w in getattr(r, 'ignored', []):
        if w in ('row', 'rows', 'entry', 'entries'):
            info['unexplained'].append(w)
    info['ignored_words'] = [w for w in getattr(r, 'ignored', []) if w not in info['unexplained']]
    if info['unexplained']:
        info['route'] = 'fallback'
        info['reason'] = 'words not interpreted locally: ' + ', '.join(info['unexplained'][:12])
        return None, info

    if projected:
        target = (forecast_target[0], (forecast_target[1], forecast_target[2])) if forecast_target else \
            next(((c, cmp) for c, cmp, _ in measured if cmp), None)
        if target is None:
            info.update(route='fallback', reason='forecast requested without a measurement threshold'); return None, info
        (c, (op, value)) = target
        if c not in __import__('clinical_core.agere.clinical.trajectory', fromlist=['NOISE']).NOISE:
            info.update(route='fallback', reason=f'no trajectory noise model for {c}'); return None, info
        base = [a for a in allp if not (a['kind'] in ('measurement', 'concept') and a.get('concept') == c)]
        info['route'] = 'local_compiler'
        return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': {'all': base[:20], 'any': anyp[:20], 'exclude': excl[:20]}},
             'purpose': 'Population named in the question'},
            {'id': 'forecast', 'operation': 'trajectory', 'inputs': ['population'],
             'parameters': {'concept': c, 'direction': 'down' if op in ('lt', 'le') else 'up', 'threshold': value, 'horizon_days': horizon or 365},
             'purpose': f'Patients projected to cross {c} {value}'}]}, info
    if trend:
        target = next((c for c, cmp, _ in measured), None)
        if target is None:
            info.update(route='fallback', reason='trend requested without a measurement'); return None, info
        word = trend.group(0)
        direction = ('down' if re.search(r'declin|dropp|fall|down', word) else 'up' if re.search(r'ris|increas|climb|up', word) else
                     'better' if 'improv' in word else 'worse')
        base = [a for a in allp if not (a['kind'] in ('measurement', 'concept') and a.get('concept') == target)]
        info['route'] = 'local_compiler'
        return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': {'all': base[:20], 'any': anyp[:20], 'exclude': excl[:20]}},
             'purpose': 'Population named in the question'},
            {'id': 'trend', 'operation': 'trajectory', 'inputs': ['population'], 'parameters': {'concept': target, 'direction': direction},
             'purpose': f'{direction} {target} trajectories'}]}, info
    if hotspot:
        segments = hot_segments or ([group] if group in ('practice', 'pcp', 'coverage_type') else None)
        for concept, start, _ in events:  # encounter types are outcomes here, not counted events
            atom = {'kind': 'concept', 'concept': concept, 'window': W}
            if atom not in allp: allp.append(atom)
        if not allp and not excl:
            info.update(route='fallback', reason='discovery requested without a recognised outcome'); return None, info
        info['route'] = 'local_compiler'
        params = {'outcome': {'all': allp[:20], 'any': anyp[:20], 'exclude': excl[:20]}, 'pairs': True, 'direction': 'high'}
        if segments: params['segments'] = segments
        return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': {'all': [], 'any': [], 'exclude': []}},
             'purpose': 'Whole population'},
            {'id': 'patterns', 'operation': 'discover', 'inputs': ['population'], 'parameters': params,
             'purpose': 'Segments where the outcome is concentrated'}]}, info

    if period:  # periods bound events, measurements, tests and records (incl. either/or atoms); chronic conditions keep full history
        timed = {'basis': 'source_event', 'start': period[0], 'end': period[1]}
        for atom in allp + anyp + excl:
            if atom['kind'] == 'record' and atom.get('stream') == 'coverage': continue  # payer membership describes who, not when
            if atom['kind'] in ('measurement', 'record') or (atom['kind'] == 'concept' and (
                    atom['concept'] in concepts.ENCOUNTERS or atom['concept'] in concepts.MEASUREMENTS)):  # "an A1c test in 2029"
                atom['window'] = dict(timed)

    contradictory = [a for a in allp if a in excl]
    if contradictory:
        info.update(route='fallback', reason=f'contradictory constraints: {contradictory[0].get("concept") or contradictory[0].get("field")}')
        return None, info

    def cohort(atoms_all, atoms_excl):
        return {'id': 'population', 'operation': 'cohort', 'inputs': [],
                'parameters': {'specification': {'all': atoms_all[:20], 'any': anyp[:20], 'exclude': atoms_excl[:20]}},
                'purpose': 'Population named in the question'}

    nodes, interpretation = [], q
    event_concept = events[0][0] if events else None
    event_completed = events[0][2] if events else False
    if why:
        outcome = ({'type': 'readmission'} if readmit else
                   {'type': 'concept', 'concept': event_concept} if event_concept else None)
        if outcome is None and measured and measured[-1][1]:
            c, (op, v), _ = measured[-1]
            outcome = {'type': 'measurement', 'concept': c, 'op': op, 'value': v, 'aggregate': 'latest' if latest else 'any'}
            allp = [a for a in allp if not (a['kind'] == 'measurement' and a['concept'] == c)]
        if outcome is None and conds:
            outcome = {'type': 'concept', 'concept': conds[-1][0]}
            allp = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == conds[-1][0])]
        if outcome is None:
            info.update(route='fallback', reason='why-question without a recognised outcome'); return None, info
        nodes = [cohort(allp, excl), {'id': 'why', 'operation': 'explain', 'inputs': ['population'],
                                      'parameters': {'outcome': outcome, 'window': {'start': '', 'end': ''}},
                                      'purpose': 'Factors distinguishing units with the outcome'}]
    elif readmit or rate or share or mean or median or group or (event_concept and count):
        base_all = [a for a in allp]
        if readmit:
            params = {'measure': 'readmissions', 'concept': '', 'statistic': 'rate' if (rate or share) else 'count'}
        elif mean or median:
            target = next((c for c, cmp, _ in measured if not cmp), None) or (measured[-1][0] if measured else None)
            if target is None:
                info.update(route='fallback', reason='average requested without a measurement'); return None, info
            base_all = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == target)]
            params = {'measure': 'value', 'concept': target, 'statistic': 'median' if median else 'mean',
                      'value_aggregate': 'latest' if latest or True else 'all'}
        elif event_concept and not share:
            base_all = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == event_concept)]
            params = {'measure': 'events', 'concept': event_concept,
                      'statistic': 'rate_per_1000' if rate and group not in TIME_GROUPS else 'count',
                      **({'status': 'completed'} if event_completed else {})}
        else:
            # share of the base population that has the last-named concept/measurement
            ranked = [a for a in allp if a['kind'] in ('concept', 'measurement') and id(a) in position]
            subject = max(ranked, key=lambda a: position[id(a)]) if share and ranked else (allp[-1] if allp else None)
            if subject is not None and subject in allp: allp = [a for a in allp if a is not subject] + [subject]
            if visit_atoms and visit_atoms[-1] in allp and not (share and ranked):  # "share of X who had a visit": the visit is the subject
                subject = visit_atoms[-1]
                allp = [a for a in allp if a is not subject] + [subject]
            elif event_concept and (share or count):
                subject = {'kind': 'concept', 'concept': event_concept, 'window': W}
                allp = allp + [subject]
            if group and not share and not visit_atoms:  # "how do they break out by payer": patients per group
                subject = {'kind': 'concept', 'concept': '', 'window': W}
                allp = allp + [subject]
            if subject is None or subject['kind'] not in ('concept', 'measurement'):
                info.update(route='fallback', reason='share requested without a subject'); return None, info
            base_all = allp[:-1]
            if subject['kind'] == 'measurement':
                info.update(route='fallback', reason='share of a measurement threshold needs a membership comparison'); return None, info
            params = {'measure': 'patients', 'concept': subject['concept'], 'statistic': 'share' if share else 'count',
                      **({'status': subject['status']} if subject.get('status') else {})}
        params.update(group_by=group or 'none', adjust='age_sex_conditions' if adjust and params['measure'] != 'value' else 'none',
                      window={'start': period[0], 'end': period[1]} if period else {'start': '', 'end': ''}, top=25 if highest else 200)
        params.setdefault('value_aggregate', 'all')
        if info.get('columns') and params['group_by'] != 'none':
            params['columns'] = info['columns']
            if params['measure'] == 'events' and re.search(r'count|visits$|_n$|number', info['columns'][1]): params['statistic'] = 'count'
        elif info.get('columns'):
            info.update(route='fallback', reason='named output columns but no grouping was recognised'); return None, info
        nodes = [cohort(base_all, excl), {'id': 'answer', 'operation': 'aggregate', 'inputs': ['population'],
                                          'parameters': params, 'purpose': 'Requested statistic'}]
    else:
        if not allp and not excl and not count:
            info.update(route='fallback', reason='no population or statistic recognised'); return None, info
        nodes = [cohort(allp, excl)]
    info['route'] = 'local_compiler'
    return {'interpretation': interpretation, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': nodes}, info


# ---- literal data-dictionary questions -> governed SQL --------------------------
def _literal_stream_question(q, r, profile, as_of, info):
    low = r.low
    clinical = [a for al in (*CONDITION_ALIASES.values(), *MEASUREMENT_ALIASES.values()) for a in al if len(a) > 3]
    if any(re.search(_alias_pattern(a), low) for a in clinical) and not re.search(r'\brecords?\b', low):
        return None
    vocab = _stream_vocabulary(profile)
    stream, span = None, None
    for s, forms in sorted(vocab.items(), key=lambda kv: -max(map(len, kv[1]))):
        for f in forms:
            m = re.search(_alias_pattern(f) + r'(?:\s+results?(?=\s+(?:records?|rows?|entries|entry)\b))?(?:\s+(?:records?|rows?|notifications?|entries|entry))?', low)
            named_field = any(re.search(_alias_pattern(x), low) for x in profile['streams'][s]['fields'] if '_' in x and len(x) > 6)
            subject = m and len(f) >= 5 and s not in NATURAL_STREAM_EXCLUDE and profile['streams'][s]['records'] >= 20 and (
                re.search(r'\b(?:how many|count|number of|total(?: up)?|sum of|list|which|each|on|of|for)\b(?:\s+[\w-]+){0,4}\s+' + _alias_pattern(f), low)
                or re.search(_alias_pattern(f) + r'\s+(?:with|that|which|whose|have|has|had|by|in|from|were|was|are|is|went|came|marked|scheduled|booked|split|lack)\b', low))
            if m and (re.search(r'records?|rows?|notifications?|entr', m.group(0)) or '_' in f or s == 'adt' or named_field or subject):
                stream, span = s, m
                break
        if stream: break
    if not stream: return None
    fields = profile['streams'][stream]['fields']
    for i in range(span.start(), span.end()): r.used[i] = True
    r.notes.append(f'stream {stream}')
    for token in stream.split('_'):  # words of the stream's own name are explained by it
        r.take(r'\b' + re.escape(token.rstrip('s')) + r's?\b', None, overlap=True)
    conds, sql_where, named_fields = [], [], []
    named_at = []  # (field, match) for every field named in the question
    group_field = None
    forms = _field_forms(fields)
    forms.setdefault('organization_id', []).extend(['organization', 'site', 'facility'])
    for f, words in FIELD_SYNONYMS.items():
        if f in forms: forms[f] = sorted(set(forms[f]) | set(words), key=len, reverse=True)
    if 'source_system' in forms and any('PRACTICE' in v for v in (fields['source_system'].get('values') or {})):
        forms['source_system'] = sorted(set(forms['source_system']) | {'practice', 'clinic'}, key=len, reverse=True)
    g_lead = r'\b(?:by|for each|per|split by|broken down by|break(?:s)? down by|went to each|to each|each)\s+(?:the |their |claim |patient\'s )?(?:recording |source |service )?'
    hit = None
    for f, fs in sorted(forms.items(), key=lambda kv: -max(map(len, kv[1]))):
        for form in fs:
            hit = next(iter(r.take(g_lead + _alias_pattern(form), f'group by {f}', overlap=True)), None)  # field words may share the stream's name
            if hit: group_field = f; break
        if group_field: break
    # A second dimension ("by site and impression", "each practice ... by vaccine") is grouped too, never dropped.
    group_fields = [group_field] if group_field else []
    if hit:
        for f, fs in sorted(forms.items(), key=lambda kv: -max(map(len, kv[1]))):
            if f == group_field: continue
            for form in fs:
                m = re.compile(r'\s*(?:,|\band\b|\bthen\b)\s*(?:by\s+)?(?:the\s+)?' + _alias_pattern(form)).match(low, hit.end())
                second = r.take(r'(?<=\S)\s*(?:,|\band\b|\bthen\b)\s*(?:by\s+)?(?:the\s+)?' + _alias_pattern(form), f'group by {f}') if m else \
                    r.take(g_lead + _alias_pattern(form), f'group by {f}')
                if second: group_fields.append(f); break
            if len(group_fields) == 2: break
    for m in r.take(r'\barrived in a later calendar year than (?:their )?source event dates?\b', 'arrival year after event year'):
        sql_where.append("event_date IS NOT NULL AND available_date IS NOT NULL AND substr(available_date,1,4) > substr(event_date,1,4)")
    for m in r.take(r'\barrived more than (\d+) (?:calendar )?days after (?:their )?source event dates?\b', 'arrival lag'):
        sql_where.append(f"event_date IS NOT NULL AND available_date IS NOT NULL AND julianday(available_date) - julianday(event_date) > {m.group(1)}")
    # field names mentioned (exact or natural). Longest FORM first across all fields, and a one-word tail form never
    # matches inside a snake_case name: place_of_service's tail 'service' must not consume 'service_date'.
    for form, f in sorted(((form, f) for f, fs in forms.items() for form in fs), key=lambda x: -len(x[0])):
        pattern = _alias_pattern(form) if ('_' in form or ' ' in form) else r'(?<![a-z0-9_])' + re.escape(form) + r'(?![a-z0-9_])'
        for m in r.take(pattern, f'field {f}'):
            named_fields.append(f)
            named_at.append((f, m))
    sum_m = r.take(r'\b(?:gross |raw |total )?sum(?: in usd)?(?: of)?\b', 'sum', overlap=True)
    # observed values mentioned (for low-cardinality fields)
    candidates = [(f, v) for f, spec in fields.items() for v in (spec.get('values') or {})
                  if len(v) >= 3 and not re.fullmatch(r'[\d\s:/.+-]+', v) and not f.endswith(('_date', '_time', 'date'))]
    exact = lambda v: re.search(_alias_pattern(v.lower()), low) is not None and v == v.lower()
    for f, v in sorted(candidates, key=lambda fv: (not exact(fv[1]), -len(fv[1]))):
        if True:
            for m in r.take(_alias_pattern(v.lower()) if exact(v) else _value_pattern(v), f'{f} = {v}'):
                neg = _negated(low, m.start())
                sql_where.append(f"json_extract(payload,'$.{f}') {'<>' if neg else '='} '{v}'")
                named_fields = [x for x in named_fields if x != f]
    # "contains X" on a named field
    for m in r.take(r'\bcontains? ([a-z0-9-]+)', 'contains'):
        target = named_fields.pop() if named_fields else None
        if not target: return None
        sql_where.append(f"lower(json_extract(payload,'$.{target}')) LIKE '%{m.group(1)}%'")
    r.take(r'\bignoring case\b|\bcase[- ]insensitive\b')
    # "lack(s|ing) a <field>" -> missing
    lack = next(iter(r.take(r'\blacks? (?:a |an |any )?([a-z_]+)', 'lacks field', overlap=True)), None)
    if lack and lack.group(1) in fields:
        sql_where.append(f"json_extract(payload,'$.{lack.group(1)}') IS NULL")
        named_fields = [x for x in named_fields if x != lack.group(1)]
    # "no/without/missing (a) <field>" -> the field is absent on the record
    for f, m in list(named_at):
        if f not in named_fields: continue
        neg = re.search(r'\b(?:no|without(?: an?| any)?|missing(?: an?| any)?|lacks? (?:a |an |any )?|lacking (?:a |an |any )?)\s*$', low[:m.start()])
        if neg:
            r.take(re.escape(neg.group(0).strip()) + r'(?=\s*' + re.escape(low[m.start():m.end()]) + ')', 'field absent', overlap=True)
            sql_where.append(f"json_extract(payload,'$.{f}') IS NULL")
            named_fields = [x for x in named_fields if x != f]
    date_field = next((f for f in named_fields if f.endswith('_date')), 'event_date')
    span_period = _take_period(r, as_of, year_words=r'(?!x)x')  # quarters, halves, ranges, since, YTD, months; plain years below
    if span_period:
        col = 'event_date' if date_field == 'event_date' else f"json_extract(payload,'$.{date_field}')"
        sql_where.append(f"{col} BETWEEN '{span_period[0]}' AND '{span_period[1]}'")
        named_fields = [x for x in named_fields if x != date_field]
    for m in r.take(r'\b(?:with \w+ )?(?:in|during) ' + MONTH_RX + r' (\d{4})\b', 'month window'):
        import calendar
        mo, y = _month_number(m.group(1)), m.group(2)
        col = 'event_date' if date_field == 'event_date' else f"json_extract(payload,'$.{date_field}')"
        sql_where.append(f"{col} BETWEEN '{y}-{mo:02d}-01' AND '{y}-{mo:02d}-{calendar.monthrange(int(y), mo)[1]:02d}'")
        named_fields = [x for x in named_fields if x != date_field]
    # year windows: "in 2028" on a named date field or the source event date
    date_field = next((f for f in named_fields if f.endswith('_date')), 'event_date')
    for m in r.take(r'\b(?:with )?(?:source[- ]event[- ]dated |source event dates? )?(?:in|during) (\d{4})\b|(?<=count )(\d{4})\b', 'year'):
        col = 'event_date' if date_field == 'event_date' else f"json_extract(payload,'$.{date_field}')"
        sql_where.append(f"substr({col},1,4) = '{m.group(1) or m.group(2)}'")
        named_fields = [x for x in named_fields if x != date_field]
    r.take(r'\bsource[- ]event[- ]dated\b|\bwhose\b|\bis\b|\bmarked\b|\bsay\b|\bwith\b|\bhave\b|\bhas\b|\bstatus\b|\btype\b|\bexcluding\b|\bnot unique stays\b|\bnotification records\b|\bcount\b')
    # statistic

    r.take(r'\breturn (?:a )?table\b|\breturn cents\b|\bexcluding missing organizations\b|\bmissing\b|\borganizations\b')
    patients = bool(re.search(r'\b(?:patients?|people)\b', low))
    # Two values of one field cannot both hold on a record: "or" means either; anything else is a sequence -> decline.
    same_field = defaultdict(list)
    for clause in sql_where:
        m = re.fullmatch(r"json_extract\(payload,'\$\.([a-z0-9_]+)'\) = '([^']*)'", clause)
        if m: same_field[m.group(1)].append((clause, m.group(2)))
    for field, items in same_field.items():
        if len(items) < 2: continue
        values = [v for _, v in items]
        if re.search(r'\bor\b', low):
            sql_where = [c for c in sql_where if c not in [x for x, _ in items]] + \
                        [f"json_extract(payload,'$.{field}') IN (" + ', '.join(f"'{v}'" for v in values) + ')']
        else:
            info.update(route='fallback', reason=f'{field} cannot be both {" and ".join(values)} on one record (a sequence across records)')
            return None, info
    where = ' AND '.join([f"stream = '{stream}'", *sql_where])
    amount = next((f for f in named_fields if re.search(r'cents|amount', f)), None)
    if not sum_m and re.search(r'\bwhat did [\w ]*\bpay\b|\btotal(?: up)?\b|\bhow much\b', low):
        sum_m = r.take(r'\bwhat did\b|\bpay\b|\btotal(?: up)?\b|\bhow much\b', 'sum', overlap=True)
    money = re.search(r'\bdollars?\b|\$|\busd\b|\bamounts?\b|\bgross\b|\brevenue\b|\bhow much\b', low)
    if money and not sum_m:
        sum_m = r.take(r'\bgross\b|\bamounts?\b|\brevenue\b', 'sum', overlap=True) or [money]
    if sum_m and not amount:
        prefs = ([r'payer_paid'] if re.search(r'\bpayers?\b', low) else []) + ([r'patient_paid|amount'] if re.search(r'\bpatients? (?:paid|pay)', low) else []) + \
                ([r'allowed'] if 'allowed' in low else []) + ([r'billed|charge'] if re.search(r'billed|charges?', low) else []) + [r'amount_cents', r'paid_cents', r'_cents']
        amount = next((f for pat in prefs for f in fields if f.endswith('_cents') and re.search(pat, f)), None)
        r.take(r'\bpayers?\b|\bpaid\b|\bpay\b|\ballowed\b|\bbilled\b|\bdollars?\b|\bcents\b|\banswer in\b', None, overlap=True)
    # "through which channel", "what modality": a named field asked as the answer's dimension is a grouping, never dropped
    if not group_field and not sum_m:
        asked = [f for f, m in named_at if f in named_fields and f != amount and not f.endswith(('_date', '_time', '_cents', '_id'))
                 and re.search(r'\b(?:which|what|whose|through which|via which|by which)\s+(?:\w+\s+){0,2}$', low[:m.start()])]
        if len(set(asked)) == 1:
            group_field = asked[0]
            group_fields = [group_field]
            r.notes.append(f'group by {group_field}')
            r.take(r'\b(?:through|via|by) which\b|\bwhich\b|\bwhat\b', None)
        elif len(set(asked)) > 1:
            info.update(route='fallback', reason='several fields asked as the answer dimension: ' + ', '.join(sorted(set(asked))))
            return None, info
    info['unexplained'] = [w for w in r.blocking(profile) if w not in {'usd', 'gross', 'raw', 'rows', 'net', 'refunds', 'subtract', 'infer',
                                                                'balances', 'corrections', 'realized', 'savings', 'treat', 'cents', 'table',
                                                                'organization_id', 'encounter_count', 'claim_record_count', 'clinician_id'}]
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    if info.get('columns') and not group_field:
        info.update(route='fallback', reason='named output columns but no grouping was recognised'); return None, info
    cols = re.search(r'table with ([a-z_]+) and ([a-z_]+)', low)
    key_name, count_name = (cols.group(1), cols.group(2)) if cols else tuple(info['columns']) if info.get('columns') else (group_field, 'count')
    if len(group_fields) == 2 and sum_m:
        info.update(route='fallback', reason='two-dimension sums are not compiled locally')
        return None, info
    if sum_m and not amount:
        info.update(route='fallback', reason='a money total was asked for but no amount field was identified'); return None, info
    if group_field and sum_m and amount:
        key_name = cols.group(1) if cols else info['columns'][0] if info.get('columns') else group_field
        dollars = bool(re.search(r'\busd\b|\bdollars?\b|\$', low)) and amount.endswith('_cents')
        total_name = cols.group(2) if cols else info['columns'][1] if info.get('columns') else ('total_usd' if dollars else 'total_cents')
        total = f"round(sum(json_extract(payload,'$.{amount}'))/100.0, 2)" if dollars else f"sum(json_extract(payload,'$.{amount}'))"
        sql = (f"SELECT json_extract(payload,'$.{group_field}') AS {key_name}, {total} AS {total_name} "
               f"FROM record WHERE {where} AND json_extract(payload,'$.{group_field}') IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        returns = 'table'
    elif len(group_fields) == 2:
        a, b = group_fields
        sql = (f"SELECT json_extract(payload,'$.{a}') AS {a}, json_extract(payload,'$.{b}') AS {b}, count(*) AS count FROM record WHERE {where} "
               f"AND json_extract(payload,'$.{a}') IS NOT NULL AND json_extract(payload,'$.{b}') IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2")
        returns = 'table'
    elif group_field:
        sql = (f"SELECT json_extract(payload,'$.{group_field}') AS {key_name}, count(*) AS {count_name} FROM record WHERE {where} "
               f"AND json_extract(payload,'$.{group_field}') IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        returns = 'table'
    elif sum_m and amount:
        sql = f"SELECT sum(json_extract(payload,'$.{amount}')) FROM record WHERE {where}"
        if re.search(r'\busd\b|\bdollars?\b|\$', low) and amount.endswith('_cents'): sql = f"SELECT sum(json_extract(payload,'$.{amount}'))/100.0 FROM record WHERE {where}"
        returns = 'value'
    elif patients:
        sql = f"SELECT count(DISTINCT patient_id) FROM record WHERE {where}"
        returns = 'patient_set' if (re.search(r'\b(?:which|list)\b', low) or info.get('output') == 'patient_set') else 'count'
        if returns == 'patient_set': sql = f"SELECT DISTINCT patient_id FROM record WHERE {where}"
    else:
        sql = f"SELECT count(*) FROM record WHERE {where}"
        returns = 'count'
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': returns},
                       'purpose': f'Literal {stream} question'}]}, info


NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
SQL_OPS = {'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<=', 'eq': '='}


# Governed operations metrics over the semantic entity tables (formula stated in the purpose).
OPS_METRICS = [
    (r'\bno[- ]?show rates?\b|\bno[- ]?show (?:percentage|share|%)|(?:rate|share|percent(?:age)?|%) of (?:appointments|appts) (?:that )?(?:were |ended (?:as|in) )?(?:a )?no[- ]?shows?',
     'appointment', 'scheduled_date', "round(100.0 * sum(ever_no_show) / nullif(sum(kept) + sum(ever_no_show), 0), 2)",
     'No-show rate = no-shows / (kept + no-shows), per appointment, by scheduled date'),
    (r'\bcancell?ation rates?\b|(?:rate|share|percent(?:age)?|%) of (?:appointments|appts) (?:that )?(?:were |got )?cancell?ed',
     'appointment', 'scheduled_date', "round(100.0 * sum(ever_cancelled) / count(*), 2)",
     'Cancellation rate = appointments ever cancelled / all appointments, by scheduled date'),
    (r'\b(?:first[- ]pass |initial )?denial rates?\b',
     'claim', 'service_date', "round(100.0 * sum(first_status = 'denied') / count(*), 2)",
     'First-pass denial rate = claims whose first adjudication was a denial / claims, by service date'),
    (r'\b(?:overturned denials|denials? (?:that were |later )?(?:overturned|paid)|denied,? (?:and |then |but )*(?:later |eventually |subsequently )?(?:got )?paid)\b',
     'claim', 'service_date', "sum(denied_then_paid)", 'Claims with a denial later followed by payment'),
    (r'\bnet (?:payer[- ]?)?paid\b|\bnet (?:payer )?payments?\b|\bpaid after reversals\b|\bpa(?:y|id),? net of (?:reversals|take[- ]?backs|recoupments)\b',
     'claim', 'service_date', "round(sum(net_payer_paid_cents) / 100.0, 2)", 'Net payer paid (USD) = paid minus reversed, per claim, by service date'),
    (r'\b(?:average|mean|avg) (?:inpatient )?(?:length of stay|los)\b|\balos\b',
     'stay', 'admit_date', "round(avg(los_days), 2)", 'Average length of stay (days) over inpatient stays with a received discharge time, by admit date'),
]
OPS_GROUPS = {'appointment': {'practice': 'organization_id', 'clinic': 'organization_id', 'clinician': 'clinician_id', 'provider': 'clinician_id'},
              'claim': {'payer': 'payer', 'insurer': 'payer', 'plan': 'payer'},
              'stay': {'hospital': 'organization_id', 'facility': 'organization_id'}}


DRUG_ALIASES = {
    'statin': r'statins?', 'beta_blocker': r'beta[- ]?blockers?', 'hf_evidence_based_beta_blocker': r'(?:evidence[- ]based|gdmt) (?:hf )?beta[- ]?blockers?',
    'ace_inhibitor': r'ace[- ]?(?:inhibitors?|i)s?', 'arb': r'arbs?|angiotensin receptor blockers?', 'arni': r'arnis?|entresto|sacubitril',
    'mra': r'mras?|mineralocorticoid (?:receptor )?antagonists?|spironolactone|eplerenone', 'sglt2_inhibitor': r'sglt-?2(?: inhibitors?|is?)?',
    'glp1_agonist': r'glp-?1(?: agonists?| ras?| receptor agonists?)?', 'metformin': r'metformin', 'insulin': r'insulin',
    'anticoagulant': r'anticoagulants?|blood thinners?|doacs?', 'opioid': r'opioids?|narcotics?', 'benzodiazepine': r'benzodiazepines?|benzos?',
    'inhaled_corticosteroid': r'inhaled (?:cortico)?steroids?|ics', 'loop_diuretic': r'loop diuretics?', 'antiplatelet': r'antiplatelets?',
}
# Medication evidence values that mean a fill happened (pharmacy dispense, pharmacy claim, payer pharmacy claim).
# One place in this file; read from the shared concepts definition when present (it MUST match the semantic layer).
FILL_EVIDENCE = getattr(concepts, 'FILL_EVIDENCE', ('dispensed', 'claim', 'payer_claim'))
FILL_EVIDENCE_SQL = getattr(concepts, 'FILL_EVIDENCE_SQL', '(' + ','.join(repr(e) for e in FILL_EVIDENCE) + ')')


def _picked_up_sql():
    """Predicate for a picked-up fill: the shared concepts.picked_up_sql (excludes prepared, returned and reversed
    fills) when available, else the fill-evidence filter."""
    shared = getattr(concepts, 'picked_up_sql', None)
    return shared() if shared else f'evidence IN {FILL_EVIDENCE_SQL}'
DRUG_GROUP_ALIASES = {'raas_inhibitor': r'(?:ace|acei)/arb(?:/arni)?|raas inhibitors?', 'diabetes_medication': r'diabetes (?:meds|medications?)'}


def _medication_question(q, r, profile, as_of, info):
    """Patients on / not on a drug class, optionally within a condition population and a period ($0, semantic medication table)."""
    from .drug_classes import GROUPS
    low = r.low
    found = None
    for cls, rx in sorted({**DRUG_ALIASES, **DRUG_GROUP_ALIASES}.items(), key=lambda kv: -len(kv[1])):
        m = re.search(r'\b(?:' + rx + r')\b', low)
        if m: found = (cls, m); break
    if not found or not re.search(r'\b(?:on|taking|take|filled|fill|dispensed|prescribed|ordered|using|receiving|got|picked up|pick up|picks up|picking up)\b', low): return None
    cls, m = found
    # This path has no encounter grammar: "diabetics not on insulin who had an ED visit in 2028" would
    # silently drop the visit and apply the year to dispensing. Decline so a verified planner handles it.
    if any(re.search(_alias_pattern(a), low) for aliases in ENCOUNTER_ALIASES.values() for a in aliases) or \
            re.search(r'\b(?:ed|er)\b', low):
        info.update(route='fallback', reason='medication filter combined with an encounter is not compiled locally')
        return None, info
    classes = GROUPS.get(cls, [cls])
    r.take(r'\b(?:' + ({**DRUG_ALIASES, **DRUG_GROUP_ALIASES}[cls]) + r')\b', f'drug class {cls}')
    evidence = FILL_EVIDENCE_SQL  # default: a picked-up fill
    if r.take(r'\b(?:prescribed|ordered|on an order)\b', 'evidence ordered'): evidence = "('ordered')"
    elif r.take(r'\b(?:on (?:their|the) med(?:ication)? lists?|listed)\b', 'evidence listed'): evidence = "('listed')"
    elif r.take(r'\b(?:any (?:source|evidence)|any record)\b', 'evidence any'): evidence = None
    r.take(r'\b(?:on|taking|take|filled|fill|dispensed|using|receiving|got|picked up|pick up|picks up|picking up|a|an|any|of|currently|active)\b', None, overlap=True)
    negated = bool(re.search(r'\b(?:not|no|without|never|lacking|missing)\b(?:\s+\w+){0,4}\s+(?:' + ({**DRUG_ALIASES, **DRUG_GROUP_ALIASES}[cls]) + r')', low))
    r.take(r'\b(?:not|no|without|never|lacking|missing|but)\b', None)
    period = _take_period(r, as_of)
    conds = []
    for concept, aliases in sorted(CONDITION_ALIASES.items(), key=lambda kv: -max(map(len, kv[1]))):
        for alias in sorted(aliases, key=len, reverse=True):
            if r.take(_alias_pattern(alias), f'condition {concept}'): conds.append(concept)
    which = r.take(r'\b(?:which|list|who|names?|ids?|pull)\b', 'patient list')
    share = r.take(r'(?:\bwhat (?:share|percent(?:age)?|proportion|fraction)\b|\bwhat ?%|\bpercent(?:age)? of\b)', 'share')
    r.take(r'\b(?:how many|number of|count|patients?|members?|people|have|has|had|with|and|our|the|are|is|were|was|been)\b', None, overlap=True)
    med = f"SELECT DISTINCT patient_id FROM medication WHERE drug_class IN ({', '.join(repr(c) for c in classes)})" + \
          (f" AND {_picked_up_sql()}" if evidence == FILL_EVIDENCE_SQL else f" AND evidence IN {evidence}" if evidence else '') + (f" AND event_date BETWEEN '{period[0]}' AND '{period[1]}'" if period else '')
    base = 'SELECT patient_id FROM patient' + ''.join(f" WHERE patient_id IN (SELECT patient_id FROM concept_patient WHERE concept = '{c}')" if i == 0 else
                                                      f" AND patient_id IN (SELECT patient_id FROM concept_patient WHERE concept = '{c}')" for i, c in enumerate(conds))
    cohort = f"{base} {'AND' if conds else 'WHERE'} patient_id {'NOT IN' if negated else 'IN'} ({med})"
    purpose = (f"Patients {'not ' if negated else ''}with {cls} ({', '.join(classes)}) evidence {evidence or 'any source'}"
               + (f" in {period[0]}..{period[1]}" if period else '') + (f" among {', '.join(conds)}" if conds else ''))
    if share:
        if not conds:
            info.update(route='fallback', reason='share of a medication needs a population'); return None, info
        sql = f"SELECT round(100.0 * (SELECT count(*) FROM ({cohort})) / (SELECT count(*) FROM ({base})), 2)"
        return _finish(q, r, info, sql, 'value', purpose + ' as % of the population')
    if which: return _finish(q, r, info, f"SELECT patient_id FROM ({cohort}) ORDER BY 1", 'patient_set', purpose)
    return _finish(q, r, info, f"SELECT count(*) FROM ({cohort})", 'count', purpose)


def _ops_question(q, r, profile, as_of, info):
    low = r.low
    hit = next(((rx, table, date_col, expr, purpose) for rx, table, date_col, expr, purpose in OPS_METRICS if re.search(rx, low)), None)
    if hit is None: return None
    rx, table, date_col, expr, purpose = hit
    r.take(rx, f'ops {table}')
    where = ["type = 'inpatient' AND los_days IS NOT NULL"] if table == 'stay' else []
    period = _take_period(r, as_of, year_words=r'in|during|for|with service dates? in|scheduled (?:for|in)|scheduled during')
    if period: where.append(f"{date_col} BETWEEN '{period[0]}' AND '{period[1]}'")
    group = None
    for word, col in OPS_GROUPS.get(table, {}).items():
        if r.take(r'\b(?:by|per|for each|each|split by|broken down by) (?:the )?' + word + r's?\b', f'group by {col}'): group = col; break
    r.take(r'\b(?:appointments?|appts?|claims?|medical claims?|inpatient|stays?|admissions?|scheduled|payers?|our|the|what|was|were|is|in days)\b', None, overlap=True)
    sql_where = (' WHERE ' + ' AND '.join(where)) if where else ''
    if group:
        sql = f"SELECT {group} AS {group.replace('_id', '')}, {expr} AS value FROM {table}{sql_where} GROUP BY 1 ORDER BY 1"
        return _finish(q, r, info, sql, 'table', purpose)
    returns = 'count' if expr.startswith('sum(denied') else 'value'
    return _finish(q, r, info, f"SELECT {expr} FROM {table}{sql_where}", returns, purpose)


CPT_CODE = r'(?<![\d$.,])(?:\d{4}[0-9ft]|[a-v]\d{4})(?![\d.,%])'


def _cpt_question(q, r, profile, as_of, info):
    """'How many patients had CPT 99214 or 99215 this year?', 'top 10 CPT codes by volume' -> governed SQL over claim_line
    (CPT/HCPCS on medical claim lines). Only on releases that carry the codes; RVU weighting is never implied."""
    low = r.low
    if not _cpt_received(profile) or not re.search(CPT_WORDS, low): return None
    codes = [c.upper() for c in re.findall(CPT_CODE, low)]
    ranked = re.search(r'\b(?:top|most (?:common|frequent|frequently billed|billed)|highest[- ]volume)\b|\bby (?:cpt |hcpcs )?code\b|\beach code\b|\bper code\b', low)
    if not codes and not ranked: return None  # "how many CPT codes ..." without a code or ranking: the other rungs read it
    info['cpt_lookup'] = True
    r.take(r'\b(?:cpt|hcpcs)(?:/hcpcs)?(?: (?:procedure |service |billing )?codes?)?\b|\bprocedure codes?\b|\bcodes?\b', 'CPT/HCPCS')
    if codes: r.take(CPT_CODE, 'codes ' + ', '.join(codes))
    code_expr = 'coalesce(cpt, hcpcs)'
    where = [f'{code_expr} IS NOT NULL']
    if codes: where.append(f"{code_expr} IN ({', '.join(repr(c) for c in codes)})")
    period = _take_period(r, as_of, year_words=r'in|during|for|with service dates? in')
    if period: where.append(f"service_date BETWEEN '{period[0]}' AND '{period[1]}'")
    elif r.take(r'\b(?:in |over |during )?the (?:last|past) (?:12 months|year)\b', 'last 12 months'):
        y, rest = int(as_of[:4]) - 1, as_of[4:]
        where.append(f"service_date > '{y}{rest}' AND service_date <= '{as_of}'")
    where_sql = ' WHERE ' + ' AND '.join(where)
    top = next(iter(r.take(r'\b(?:top|most (?:common|frequent|frequently billed|billed)|highest[- ]volume)(?: (\d{1,3}))?\b', 'top codes')), None)
    if top or r.take(r'\bby (?:cpt |hcpcs )?code\b|\beach code\b|\bper code\b', 'group by code'):
        n = int(top.group(1)) if top and top.group(1) else (len(codes) or 10)
        r.take(r'\bby (?:volume|count|frequency|lines?|claim lines?)\b|\bbilled\b|\bused\b', None)
        sql = (f'SELECT {code_expr} AS code, count(*) AS claim_lines, count(DISTINCT claim_id) AS claims, count(DISTINCT patient_id) AS patients '
               f'FROM claim_line{where_sql} GROUP BY 1 ORDER BY claim_lines DESC, code LIMIT {min(n, 200)}')
        returns, purpose = 'table', 'CPT/HCPCS codes by claim-line volume'
    else:
        unit = next(((m, rx) for m, rx in (('patients', r'\b(?:patients|members|people|beneficiaries)\b'), ('claims', r'\bclaims\b'),
                                            ('units', r'\bunits\b'), ('lines', r'\b(?:claim lines|lines|services|times|billed|instances)\b'))
                     if re.search(rx, low)), ('lines', None))
        if unit[1]: r.take(unit[1], f'count {unit[0]}')
        expr = {'patients': 'count(DISTINCT patient_id)', 'claims': 'count(DISTINCT claim_id)', 'units': 'coalesce(sum(units), 0)',
                'lines': 'count(*)'}[unit[0]]
        sql, returns, purpose = f'SELECT {expr} FROM claim_line{where_sql}', 'count', f'{unit[0]} with CPT/HCPCS ' + ', '.join(codes)
    r.take(r'\b(?:how many|number of|count|what|which|were|was|is|are|did|do|does|we|our|the|have|has|had|with|for|of|or|and|billed|'
           r'submitted|performed|done|had a|received|got|claim|claims|lines?|medical|on|in|a|an|any)\b', None, overlap=True)
    return _finish(q, r, info, sql, returns, purpose)


def _sql_graph(q, sql, returns, purpose, info):
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': returns},
                       'purpose': purpose}]}, info


def _comparison(low, start):
    for pattern, op in COMPARATORS:
        mm = re.match(r'\s*(?:results?|values?|records?|of|is|was)?\s*' + pattern + r'\s*' + NUMBER, low[start:start + 50])
        if mm: return op, float(mm.group(1)), start + mm.end()
    return None


def _finish(q, r, info, sql, returns, purpose):
    info['unexplained'] = r.blocking(r.profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    return _sql_graph(q, sql, returns, purpose, info)


def _record_constructs(q, r, profile, as_of, info):
    """Record-level analytics: event sequences, co-occurrence, linkage, repeated measures,
    arrival lag and strict latest values. Returns None when no construct applies."""
    low = r.low
    streams = _stream_vocabulary(profile)
    def stream_in(text):
        for s, forms in sorted(streams.items(), key=lambda kv: -max(map(len, kv[1]))):
            if any(re.search(_alias_pattern(f), text) for f in forms): return s
        return None
    def value_of(stream, text):
        for f, spec in profile['streams'][stream]['fields'].items():
            for v in sorted(spec.get('values') or {}, key=len, reverse=True):
                if len(v) >= 3 and re.search(_value_pattern(v), text): return f, v
        return None
    patients = bool(re.search(r'\bpatients?\b', low))
    count_expr = 'count(DISTINCT a.patient_id)' if patients else 'count(*)'

    # 1. sequences: "<stream> <B> N through M days after a <stream> <A> in a different ... encounter"
    m = re.search(r'(\w+) (\w+) (\d+) (?:through|to|-) (\d+) (?:calendar )?days after (?:an? )?(\w+) (\w+)', low)
    if m and stream_in(m.group(1)) and stream_in(m.group(5)):
        sa, sb = stream_in(m.group(5)), stream_in(m.group(1))
        va, vb = value_of(sa, m.group(6)), value_of(sb, m.group(2))
        if va and vb:
            r.take(re.escape(m.group(0)), 'event sequence')
            different = r.take(r'\bin a different[^.?]*encounter\b', 'different encounter')
            r.take(r'\buse source event dates?(?:, not arrival dates)?\b|\bhave an?\b|\bhas an?\b', None)
            sql = (f"SELECT {count_expr} FROM record a JOIN record b ON b.patient_id = a.patient_id "
                   f"WHERE a.stream = '{sb}' AND json_extract(a.payload,'$.{vb[0]}') = '{vb[1]}' "
                   f"AND b.stream = '{sa}' AND json_extract(b.payload,'$.{va[0]}') = '{va[1]}' "
                   f"AND a.event_date IS NOT NULL AND b.event_date IS NOT NULL "
                   f"AND julianday(a.event_date) - julianday(b.event_date) BETWEEN {m.group(3)} AND {m.group(4)}"
                   + (" AND json_extract(a.payload,'$.encounter_id') IS NOT NULL AND json_extract(b.payload,'$.encounter_id') IS NOT NULL"
                      " AND json_extract(a.payload,'$.encounter_id') <> json_extract(b.payload,'$.encounter_id')" if different else ''))
            return _finish(q, r, info, sql, 'count', f'{sb} {vb[1]} {m.group(3)}-{m.group(4)} days after {sa} {va[1]}')

    # 2. co-occurrence: "both <v1> and <v2> in <stream> for the (exact) same <field>"
    m = re.search(r'\bboth ([a-z_]+) and ([a-z_]+) in ([a-z_]+) for the (?:exact )?same ([a-z_]+)', low)
    if m and m.group(3) in profile['streams'] and m.group(4) in profile['streams'][m.group(3)]['fields']:
        st, key = m.group(3), m.group(4)
        field = next((f for f, spec in profile['streams'][st]['fields'].items()
                      if m.group(1) in (spec.get('values') or {}) and m.group(2) in (spec.get('values') or {})), None)
        if field:
            r.take(re.escape(m.group(0)), 'co-occurrence'); r.take(r'\bwhich\b', None)
            key_expr = f"lower(json_extract(payload,'$.{key}'))" if 'ignoring case' in low else f"json_extract(payload,'$.{key}')"
            sql = (f"SELECT DISTINCT patient_id FROM record WHERE stream = '{st}' AND {key_expr} IS NOT NULL "
                   f"GROUP BY patient_id, {key_expr} HAVING sum(json_extract(payload,'$.{field}') = '{m.group(1)}') > 0 "
                   f"AND sum(json_extract(payload,'$.{field}') = '{m.group(2)}') > 0")
            return _finish(q, r, info, sql, 'patient_set', 'co-occurring statuses for the same item')

    # 3. linkage: "<stream A> records lack any <stream B> record linked by <field>"
    m = re.search(r'([a-z_ ]+?) records? lacks? any ([a-z_]+) records? linked by ([a-z_]+)', low)
    if m and stream_in(m.group(1)) and m.group(2) in profile['streams']:
        sa, sb, field = stream_in(m.group(1)), m.group(2), m.group(3)
        r.take(re.escape(m.group(0)), 'linkage'); r.take(r'\bto th(?:at|e) [a-z ]+? record ids?\b|\bcount [a-z ]+ records\b', None)
        sql = (f"SELECT {'count(DISTINCT o.patient_id)' if patients and 'count' not in low else 'count(*)'} FROM record o WHERE o.stream = '{sa}' "
               f"AND NOT EXISTS (SELECT 1 FROM link l WHERE l.target_record_id = o.record_id AND l.stream = '{sb}' AND l.field = '{field}')")
        return _finish(q, r, info, sql, 'count', f'{sa} without linked {sb}')

    # 4. measurement-record questions ("HbA1c records", "at least two HbA1c results", latest strictly dated)
    for concept, aliases in MEASUREMENT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            am = re.search(_alias_pattern(alias) + r'\s+(records?|results?)', low)
            if not am: continue
            if not (re.search(r'\b(?:at least|more than) \w+ (?:recorded )?(?:numeric )?' + re.escape(alias), low)
                    or re.search(r'arrived more than \d+ (?:calendar )?days after|have no source event date|by the (?:recording |source )?organization', low)):
                continue
            where = [f"concept = '{concept}'", 'value_canonical IS NOT NULL']
            r.take(r'\b(?:numeric )?(?:percent-unit )?(?:numeric )?' + re.escape(alias) + r'\s+(?:records?|results?)', f'{concept} records')
            unit = concepts.MEASUREMENTS[concept]['unit']
            if re.search(r'percent-unit|in percent', low) and unit == '%': r.take(r'\bpercent-unit\b|\bin percent\b', None)
            cmp = _comparison(low, am.end())
            if cmp:
                where.append(f"value_canonical {SQL_OPS[cmp[0]]} {cmp[1]}")
                for i in range(am.end(), cmp[2]): r.used[i] = True
            repeat = re.search(r'\b(?:at least|more than) (\w+) (?:recorded )?(?:numeric )?' + re.escape(alias), low)
            lag = re.search(r'arrived more than (\d+) (?:calendar )?days after (?:their )?source event date', low)
            undated = re.search(r'\bhave no source event date\b', low)
            group = re.search(r'\bby the (?:recording |source )?organization\b', low)
            if repeat:
                n = NUMBER_WORDS.get(repeat.group(1)) or (int(repeat.group(1)) if repeat.group(1).isdigit() else None)
                if not n: return None
                r.take(r'\b(?:at least|more than) \w+ (?:recorded )?(?:numeric )?', 'repeated measure', overlap=True)
                sql = (f"SELECT count(*) FROM (SELECT patient_id FROM measurement WHERE {' AND '.join(where)} "
                       f"GROUP BY patient_id HAVING count(*) >= {n})")
                return _finish(q, r, info, sql, 'count', f'patients with >= {n} qualifying {concept} results')
            if lag:
                r.take(re.escape(lag.group(0)), 'arrival lag'); r.take(r'exclude records with unknown source event dates', None)
                where += ['event_date IS NOT NULL', 'available_date IS NOT NULL', f"julianday(available_date) - julianday(event_date) > {lag.group(1)}"]
                return _finish(q, r, info, f"SELECT count(*) FROM measurement WHERE {' AND '.join(where)}", 'count', 'late-arriving results')
            if undated:
                r.take(re.escape(undated.group(0)), 'undated')
                where.append('event_date IS NULL')
                return _finish(q, r, info, f"SELECT count(*) FROM measurement WHERE {' AND '.join(where)}", 'count', 'undated results')
            if group:
                r.take(re.escape(group.group(0)), 'by organization')
                cols = re.search(r'table with ([a-z_]+) and ([a-z_]+)', low)
                k, c = (cols.group(1), cols.group(2)) if cols else ('organization_id', 'patient_count')
                r.take(r'\bfor\b|\bcount distinct patients\b|\bexclude missing organizations\b', None)
                sql = (f"SELECT organization_id AS {k}, count(DISTINCT patient_id) AS {c} FROM measurement WHERE {' AND '.join(where)} "
                       f"AND organization_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
                return _finish(q, r, info, sql, 'table', f'{concept} patients by organization')
            return None
    m = re.search(r'\blatest (?:source[- ]event[- ]dated )?(' + '|'.join(re.escape(a) for al in MEASUREMENT_ALIASES.values() for a in al) + r')\b', low)
    if m and re.search(r'source[- ]event[- ]dated|exclude undated', low):
        concept = next(c for c, al in MEASUREMENT_ALIASES.items() if m.group(1) in al)
        cmp = _comparison(low, m.end())
        if not cmp: return None
        r.take(re.escape(low[m.start():cmp[2]]), 'strict latest value')
        r.take(r'exclude undated results|on the same date use the latest arrival, then largest record id|\bwhich\b', None)
        sql = (f"SELECT patient_id FROM (SELECT patient_id, value_canonical, row_number() OVER (PARTITION BY patient_id "
               f"ORDER BY event_date DESC, available_date DESC, record_id DESC) AS rn FROM measurement "
               f"WHERE concept = '{concept}' AND event_date IS NOT NULL AND value_canonical IS NOT NULL) "
               f"WHERE rn = 1 AND value_canonical {SQL_OPS[cmp[0]]} {cmp[1]}")
        return _finish(q, r, info, sql, 'patient_set', f'latest dated {concept}')
    return None


def _contract_terms(low):
    """Contract terms stated in the question: benchmark PMPM, sharing/loss rates, MSR/MLR, quality score, caps."""
    pct = lambda pattern: (lambda m: float(m.group(1)) / 100 if m else None)(re.search(pattern, low))
    num = lambda pattern: (lambda m: float(m.group(1).replace(',', '')) if m else None)(re.search(pattern, low))
    terms = {'benchmark_pmpm': num(r'benchmark (?:of |is |at )?\$?([\d,]+(?:\.\d+)?) ?(?:pmpm|per member per month)'),
             'sharing_rate': pct(r'(?:sharing|share) rate (?:of |is )?(\d+(?:\.\d+)?) ?%'),
             'msr': pct(r'(?:msr|minimum savings rate) (?:of |is )?(\d+(?:\.\d+)?) ?%'),
             'mlr': pct(r'(?:mlr|minimum loss rate) (?:of |is )?(\d+(?:\.\d+)?) ?%'),
             'loss_rate': pct(r'loss (?:sharing )?rate (?:of |is )?(\d+(?:\.\d+)?) ?%'),
             'quality_score': pct(r'quality score (?:of |is )?(\d+(?:\.\d+)?) ?%'),
             'savings_cap_pct': pct(r'(?:savings )?cap (?:of |is )?(\d+(?:\.\d+)?) ?%')}
    # Value-first phrasings: "$950 benchmark", "50% sharing", "2% MSR".
    after = {'benchmark_pmpm': num(r'\$([\d,]+(?:\.\d+)?) ?(?:pmpm )?benchmark'),
             'sharing_rate': pct(r'(\d+(?:\.\d+)?) ?% (?:shared savings |savings )?shar(?:ing|e)\b'),
             'msr': pct(r'(\d+(?:\.\d+)?) ?% (?:msr|minimum savings rate)'),
             'mlr': pct(r'(\d+(?:\.\d+)?) ?% (?:mlr|minimum loss rate)'),
             'quality_score': pct(r'(\d+(?:\.\d+)?) ?% quality score')}
    terms = {k: terms.get(k) if terms.get(k) is not None else after.get(k) for k in {*terms, *after}}
    terms = {k: v for k, v in terms.items() if v is not None}
    if re.search(r'two[- ]sided|downside', low): terms['two_sided'] = True
    if re.search(r'risk[- ]adjust', low): terms['risk_adjust'] = True
    program = _program_terms(low, terms)
    if program is not None: return program
    return terms if {'benchmark_pmpm', 'sharing_rate'} <= set(terms) else {}


PROGRAM_SETTLEMENT_INTENT = (r'\b(?:earn\w*|payments?|pay(?:s|out)? us|paid|owe\w*|savings|shared[- ]savings|losses|settle\w*|bonus|'
                             r'make|receive|get back|net)\b')
PROGRAM_BENCHMARK = r'\bbenchmark (?:of |is |at )?\$([\d,]+(?:\.\d+)?)\b|\$([\d,]+(?:\.\d+)?) ?(?:pmpm |per member per month )?benchmark\b'


def _named_program(low):
    from . import programs
    try: return programs.detect(low)
    except (OSError, ValueError): return None


def _program_kind(named):
    from . import programs
    return programs.load()[named['id']]['kind']


def _stated_benchmark(low, stated):
    stated = dict(stated)
    if 'benchmark_pmpm' not in stated:
        m = re.search(PROGRAM_BENCHMARK, low)
        if m: stated['benchmark_pmpm'] = float((m.group(1) or m.group(2)).replace(',', ''))
    return stated


def _program_terms(low, stated):
    """Terms of a program named in the question (MSSP BASIC A-E / ENHANCED, ACO REACH ...) merged under the
    question's own terms -> terms with cited provenance, or None when no settleable program is named or the
    benchmark (always contract-specific) is missing."""
    named = _named_program(low)
    if named is None: return None
    from . import programs
    try: terms = programs.contract_terms(named, _stated_benchmark(low, stated))
    except ValueError: return None
    return terms if not programs.missing_terms(terms) else None


def _settlement_missing(low, default):
    """Exactly the inputs a settlement question still lacks: with a named program only the contract-specific ones."""
    named = _named_program(low)
    if named is None: return default
    from . import programs
    try: res = programs.resolve(named)
    except ValueError as e: return [str(e)]
    if not res['doc'].get('settlement') and res['doc'].get('choose'):
        return [f"program track: {res['doc']['choose']}", *([] if re.search(PROGRAM_BENCHMARK, low) else ['benchmark_pmpm (contract-specific)'])]
    if not res['doc'].get('settlement'):
        return [f"executed contract terms ({res['label']} is {res['doc']['kind'].replace('_', ' ')} reference material, not settlement terms)",
                *[m for m in default[1:] if not (re.search(PROGRAM_BENCHMARK, low) and 'benchmark' in m)]]
    terms = programs.contract_terms(res, _stated_benchmark(low, {}))
    supplied = ', '.join(k for k in ('sharing_rate', 'msr', 'msr_schedule', 'mlr', 'loss_rate', 'savings_cap_pct', 'loss_cap_pct', 'corridors',
                                     'discount_pct', 'quality_withhold_pct') if k in terms)
    return [f'{k} (contract-specific; {res["label"]} {res["year_basis"].replace("_", " ")} {res["year"]} supplies {supplied})'
            for k in programs.missing_terms(terms)]


VBC_PATTERNS = [
    ('shared_savings', r'\bshared[- ]savings\b|\bsettlement\b'),
    ('cost', r'\bpmpm\b|\bper member per month\b|\bcost per member\b|\btotal cost of care\b|\b(?:medical |claims )?spend(?:ing)?\b|\ballowed (?:amount|cost)s?\b|'
             r'\bibnr(?: reserves?| estimates?)?\b|\b(?:claims? )?completion factors?\b|\bincurred but not reported\b|\bclaims? run-?out\b'),
    ('eligibility', r'\bmember[- ]months?\b|\benrolled members\b|\benrollment\b'),
    ('attribution', r'\battribut(?:ed|ion)\b'),
    ('risk', r'\brisk scores?\b|\brisk[- ]adjustment scores?\b|\bacuity\b'),
    ('quality', r'\bquality measures?\b|\ba1c (?:poor )?control\b|\bpoorly controlled diabetes\b|\b(?:bp|blood pressure) control\b|\bstatin (?:therapy|use)\b'),
    ('utilization', r'\bper 1,?000\b|\bper thousand\b|\butilization panel\b|\b7[- ]day (?:post[- ]discharge )?follow[- ]?up\b|'
                    r'\bfollow[- ]?up (?:(?:visit|appointment|contact)s? )?within 7 days\b'),
]
QUALITY_MEASURES = [('diabetes_a1c_poor_control', r'a1c (?:poor )?control|poorly controlled diabetes'),
                    ('bp_control', r'(?:bp|blood pressure) control'), ('statin_therapy_diabetes', r'statin')]


CHANGE_MEASURES = [('pmpm', r'\bpmpm\b|\bper member per month\b|\b(?:total |medical )?(?:cost|costs|spend|spending)\b'),
                   ('ed_rate', r'\b(?:ed|er|emergency(?: department)?) (?:visits?|use|utili[sz]ation)\b|\bed rate\b'),
                   ('admit_rate', r'\b(?:admissions?|hospitali[sz]ations?|admits?)\b')]


def _change_question(q, r, profile, as_of, info):
    low = r.low
    verb = re.search(r'\b(?:why|what explains|what drove|explain)\b.*\b(?:go(?:ne)? up|went up|go(?:ne)? down|went down|increase[ds]?|rise|rose|risen|'
                     r'grow|grew|change[ds]?|drop(?:ped)?|fall|fell|decrease[ds]?|decline[ds]?|jump(?:ed)?|spike[ds]?)\b', low)
    measure = next((m for m, pattern in CHANGE_MEASURES if re.search(pattern, low)), None)
    # "this year vs last year", "compared with the prior year": year to date against the same window a year earlier
    prior = re.search(r"(?:\b(\d{4}) ytd |\bthis year )?(?:(?:how (?:does|do|did) (?:it|that|they|this) )?compared? (?:with|to)|vs\.?|versus|relative to|against) "
                      r"(?:the )?(?:same (?:period|time) )?(?:last|prior|previous) year(?:'s)?(?: same period)?\b", low)
    if not (verb or prior) or not measure: return None
    r.take(r'\b(?:why|what explains|what drove|explain)\b', 'why'); r.take(dict(CHANGE_MEASURES)[measure], f'measure {measure}')
    if prior:
        r.take(re.escape(prior.group(0)), 'year to date vs the same window last year')
        y = int(prior.group(1) or as_of[:4]); md = as_of[5:10] if as_of[5:10] != '02-29' else '02-28'
        compare_prior = {'a': {'start': f'{y - 1}-01-01', 'end': f'{y - 1}-{md}'}, 'b': {'start': f'{y}-01-01', 'end': min(f'{y}-12-31', as_of)}}
    else:
        compare_prior = None
    r.take(r'\b(?:did|does|has|have|our|the|go(?:ne)? up|went up|go(?:ne)? down|went down|increase[ds]?|rise|rose|risen|grow|grew|change[ds]?|'
           r'drop(?:ped)?|fall|fell|decrease[ds]?|decline[ds]?|jump(?:ed)?|spike[ds]?|so much|this year|last year|year over year)\b', None, overlap=True)
    params = {'metric': 'change', 'measure': measure, 'group_by': 'none'}
    if compare_prior: params['compare'] = compare_prior
    years = re.search(r'\b(?:from|between) (\d{4}) (?:to|and) (\d{4})\b', low)
    if years:
        r.take(re.escape(years.group(0)), 'compare years')
        ya, yb = years.group(1), years.group(2)
        params['compare'] = {'a': {'start': f'{ya}-01-01', 'end': f'{ya}-12-31'}, 'b': {'start': f'{yb}-01-01', 'end': min(f'{yb}-12-31', as_of)}}
    quarters = re.search(r'\b(?:in )?q([1-4])(?: (\d{4}))? (?:compared (?:with|to)|vs\.?|versus|relative to|over) q([1-4])(?: (\d{4}))?\b|'
                         r'\b(?:from|between) q([1-4])(?: (\d{4}))? (?:to|and) q([1-4])(?: (\d{4}))?\b', low)
    if not years and quarters:  # "rise in Q3 compared with Q2": b = the later-named quarter's period, a = the comparison quarter
        g = quarters.groups()
        (qb, yb, qa, ya) = g[:4] if g[0] else (g[6], g[7], g[4], g[5])
        def bounds(n, y):
            n = int(n); return {'start': f'{y}-{3 * n - 2:02d}-01', 'end': min([f'{y}-03-31', f'{y}-06-30', f'{y}-09-30', f'{y}-12-31'][n - 1], as_of)}
        year = int(as_of[:4])
        if not yb and f'{year}-{3 * int(qb) - 2:02d}-01' > as_of: year -= 1  # a quarter that has not started yet means last year's
        yb = yb or str(year)
        ya = ya or (yb if int(qa) < int(qb) else str(int(yb) - 1))
        r.take(re.escape(quarters.group(0)), 'compare quarters')
        params['compare'] = {'a': bounds(qa, ya), 'b': bounds(qb, yb)}
    r.take(r'\bper 1,?000\b|\bper thousand\b|\brate\b', None)
    for pattern, g in GROUPS + _demographic_groups(profile):
        if g != 'facility' and r.take(pattern, f'group by {g}'): params['group_by'] = g; break
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12])); return None, info
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'vbc', 'inputs': [], 'parameters': params, 'purpose': f'Explain change in {measure}'}]}, info


def _vbc_question(q, r, profile, as_of, info, terms):
    low = r.low
    change = _change_question(q, r, profile, as_of, info)
    if change is not None: return change
    metric = next((m for m, pattern in VBC_PATTERNS if re.search(pattern, low)), None)
    named = _named_program(low)
    if named is not None and _program_kind(named) not in ('shared_savings', 'program_rules', 'typical_range'):
        named = None  # MA, episode and care-model reference entries do not turn a question into a settlement
    if named is not None and re.search(PROGRAM_SETTLEMENT_INTENT + r'|\bbenchmark\b', low):
        if not (terms or {}).get('program') and _explanation_only(low):  # program rules asked as an explanation
            return _knowledge_graph(q)
        if not (terms or {}).get('program'):  # program named but not settleable as asked: say exactly what is missing
            asks = _settlement_missing(low, ['executed payer contract terms'])
            if _settlement_suppliable(low): return _requires_input(q, asks, info)  # a track or benchmark the user can supply
            info.update(route='local_unsupported', unexplained=[])
            return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': [f'Missing input: {m}' for m in asks]}, info
        metric = 'shared_savings'
        r.take(re.escape(low[named['span'][0]:named['span'][1]]), f"program {terms['program']['label']} {terms['program']['year']}", overlap=True)
        r.take(PROGRAM_SETTLEMENT_INTENT + r'|' + PROGRAM_BENCHMARK + r'|\b(?:track|level|model|program|option|in|the|we|us|if|were|are|on|for|at|'
               r'participat\w*|how much|money)\b', 'program settlement wording', overlap=True)
    if metric is None or (metric == 'shared_savings' and not terms): return None
    if metric != 'shared_savings' and re.search(r'\bbenchmarks?\b', low):
        # "how does it compare to benchmark": the benchmark is contract-specific and not received; never drop the comparison
        if metric == 'cost' and not re.search(r'\$\s?\d|\bbenchmark (?:of |is |at )?\d', low):
            return _requires_input(q, ['benchmark_pmpm (contract-specific and not in the received data: state the benchmark PMPM with the program '
                                       'and track, e.g. MSSP ENHANCED 2026, or the sharing terms, and the comparison runs as a settlement)'], info)
        info.update(route='fallback', reason='benchmark comparison not interpreted locally')
        return None, info
    if metric != 'shared_savings' and re.search(r'\byear[- ]end\b|\bend of (?:the )?year\b|\bproject(?:ed|ions?|ing)\b|\bforecast\w*\b|\bnext (?:year|quarter)\b', low):
        # the vbc engine reports observed (completion-adjusted) values; a year-end projection is a different question
        info.update(route='fallback', reason='projection requested; the vbc engine reports observed, completion-adjusted values')
        return None, info
    for m, pattern in VBC_PATTERNS:
        if m == metric: r.take(pattern, f'vbc {metric}')
    followup_measure = metric == 'utilization' and re.search(r'follow[- ]?up', low) and re.search(r'discharg', low)
    if metric == 'utilization' and re.search(r'follow[- ]?up', low):  # 7-day post-discharge follow-up is a share of discharges
        r.take(r'\b(?:what )?(?:percent(?:age)?|share|proportion) of\b|\b(?:discharged|recently discharged) (?:members|patients)\b|'
               r'\b(?:after|following) (?:a |their |hospital )*discharges?\b|\bpost[- ]discharge\b|\bdischarges?\b|\bhad\b|\bgot\b', None)
    if metric == 'cost':
        r.take(r'\b(?:should we|do we|would we|to) (?:apply|use|book|hold)\b|\bto estimate\b|\bestimate\b|\bof claims\b|\bclaims\b', None)
        months = next(iter(r.take(r'\b(?:for |over |in )?(?:the )?(?:last|past|most recent|latest) (\d{1,2}) months\b', 'recent months')), None)
        if months:
            n = int(months.group(1)); y, m = int(as_of[:4]), int(as_of[5:7]) - n + 1
            while m < 1: y, m = y - 1, m + 12
            params_period = {'start': f'{y:04d}-{m:02d}-01', 'end': as_of}
        else:
            params_period = None
    else:
        params_period = None
    params = {'metric': metric, 'group_by': 'none'}
    if metric == 'utilization' and r.take(r'\b(?:use |using |with )?(?:the )?(?:whole |entire |full )?roster (?:as (?:the )?denominator|headcount)\b|'
                                          r'\bper 1,?000 (?:roster )?(?:patients|people) on the roster\b|\bheadcount denominator\b', 'denominator roster'):
        params['denominator'] = 'roster'
    for pattern, g in GROUPS + _demographic_groups(profile):
        if g in ('facility',) or (g in TIME_GROUPS and metric not in ('cost', 'utilization')): continue
        if r.take(pattern, f'group by {g}'): params['group_by'] = g; break
    if params_period: params['period'] = params_period
    period = _take_period(r, as_of)  # year ranges, YTD ("this year" paraphrases to "<year> ytd"), quarters, months
    if period: params['period'] = {'start': period[0], 'end': period[1]}
    year = None if period else next(iter(r.take(r'\b(?:in|during|for) (\d{4})\b', 'year')), None)
    if year: params['period'] = {'start': f'{year.group(1)}-01-01', 'end': min(f'{year.group(1)}-12-31', as_of)}
    r.take(r'\b(?:over |in )?the (?:last|past) (?:12 months|year)\b', 'last 12 months')
    if followup_measure and params.get('denominator') != 'roster':
        # post-discharge 7-day follow-up has a library measure (FUH-style, documented denominator/numerator): report it as such
        metric = 'quality'
        params = {'metric': 'quality', 'measure': 'fuh_7day_followup', **{k: v for k, v in params.items() if k != 'metric'}}
        if params['group_by'] in TIME_GROUPS:  # measures have no calendar-period grouping: the router decides
            info.update(route='fallback', reason='follow-up measure by calendar period not interpreted locally')
            return None, info
    elif metric == 'quality':
        for measure, pattern in QUALITY_MEASURES:
            if re.search(pattern, low): params['measure'] = measure; r.take(pattern, f'measure {measure}', overlap=True); break
    if metric == 'shared_savings':
        params['terms'] = terms
        r.take(r'benchmark (?:of |is |at )?\$?[\d,]+(?:\.\d+)? ?(?:pmpm|per member per month)|(?:sharing|share|loss(?: sharing)?) rate (?:of |is )?[\d.]+ ?%|'
               r'(?:msr|mlr|minimum (?:savings|loss) rate) (?:of |is )?[\d.]+ ?%|quality score (?:of |is )?[\d.]+ ?%|(?:savings )?cap (?:of |is )?[\d.]+ ?%|'
               r'[\d.]+ ?% (?:shared savings |savings )?shar(?:ing|e)\b|[\d.]+ ?% (?:msr|mlr|minimum (?:savings|loss) rate)|[\d.]+ ?% quality score|'
               r'\$[\d,]+(?:\.\d+)? ?(?:pmpm )?benchmark|'
               r'two[- ]sided|downside|risk[- ]adjust\w*|\bwith\b|\band\b|\ba\b|\bof\b|\bour\b|\bwhat\b|\bwould\b|\bbe\b|\bpayment\b|\bunder\b|\bterms?\b|\bcontract\b', 'contract terms', overlap=True)
    r.take(r'\b(?:what is|what are|what was|show|compare|calculate|compute|estimate|our|the|rate|rates|members?|visits?|ed|admissions?|readmissions?|'
           r'follow[- ]?up|allowed|paid|medical|pharmacy|cost|costs|by|each|per|month|projected|actual|panel|scores?|measures?|internal|we|us|do|have|has|average|mean|how many|total)\b', None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'vbc', 'inputs': [], 'parameters': params, 'purpose': f'VBC {metric}'}]}, info


# ---- measure library (config/agere/measures) ------------------------------------------
MEASURE_GAPS = (r'\b(?:open |outstanding )?(?:care[- ]gaps?|gaps? in care|gap lists?|open gaps?|gaps?)\b|\bnon[- ]?compliant\b|\bnot compliant\b|'
                r'\b(?:which|what|list(?: the)?|who are(?: the)?|show(?: me)?(?: the)?) (?:patients|members|people|discharges|admissions|ed visits)\b|'
                r'\bwho (?:still )?(?:needs?|is due|are due|is missing|are missing|has not|have not|hasn\'t|haven\'t)\b|\b(?:missing|overdue|due for)\b')
MEASURE_FRAMING = (r'\b(?:rate|rates|performance|compliance|compliant|score|scores|measure|measures|results?|hedis|stars?|star ratings?|mssp|app plus|'
                   r'ecqm|quality|what is|what was|what are|how (?:are|is|did|do) we|how many|show|give|list|for|on|in|with|of|patients|members|'
                   r'people|percent(?:age)?|share|doing|by|each|per|currently|current|we|us|do|did|have|has|at|are|is|who|which|a|an|our|the|'
                   r'gaps?|me|had|had a|been|get|got|received|look|looks?|this|that|what|how|year|period|network|overall|total|open|outstanding)\b')


def _plain_count_question(low):
    """'How many diabetics had ...' asks for a cohort count; a measure answers only when the question names a
    measure, a rate/percentage, compliance/performance or gaps. One word list, shared with the metric router."""
    from .metric_router import plain_count_question
    return plain_count_question(low)


def _measure_question(q, profile, as_of):
    from . import measures
    low = _strip_framing(q.lower())
    if _plain_count_question(low):
        return None
    try:
        mid, alias = measures.match(low)
    except (OSError, ValueError):
        return None
    adherence = re.search(r'\b(?:pdc|proportion of days covered|adherence (?:rates?|measures?|scores?))\b', low)
    if adherence and (mid is None or mid.startswith('pdc_')):  # "<class> ... PDC/adherence" in any order
        for cls_rx, pdc in ((r'\bstatins?\b|\bcholesterol\b', 'pdc_statins'), (r'\bras antagonists?\b|\brasa?\b|\bace(?:i| inhibitors?)?\b|\barbs?\b|\bhypertension\b', 'pdc_rasa'),
                            (r'\bdiabetes\b|\bdiabetic\b|\boral diabetes\b', 'pdc_diabetes')):
            if re.search(cls_rx, low): mid, alias = pdc, adherence.group(0); break
    if mid is None:
        sets = measures.measure_sets()
        hit = next(((k, a) for k, s in sets.items() for a in s.get('aliases', []) if re.search(_alias_pattern(a.lower()), low)), None)
        if hit is None: return None
        mid, alias = hit
    r = Reader(low); r.profile = profile
    info = {'compiler': VERSION, 'recognised': r.notes, 'paraphrased': [], 'route': 'local_compiler'}
    r.take(_alias_pattern(alias.lower()), f'measure {mid}')
    params = {'metric': 'quality', 'measure': mid, 'group_by': 'none'}
    if r.take(MEASURE_GAPS, 'care-gap list'): params['output'] = 'gaps'
    if mid.startswith('pdc_') and r.take(r'\b(?:pdc |adherence )?(?:below|under|less than|lower than|<) ?80 ?%?|\bnon[- ]?adherent\b|\bnot adherent\b', 'PDC below 80%', overlap=True):
        params['output'] = 'gaps'
    for pattern, g in GROUPS + _demographic_groups(profile) + [(r'\b(?:which|what) (?:practices|clinics)\b', 'practice'),
                                (r'\b(?:which|what) (?:pcps|providers|physicians|clinicians|doctors)\b|\bproviders\'', 'pcp')]:
        if g in ('facility',) + TIME_GROUPS: continue
        if r.take(pattern, f'group by {g}'):
            params['group_by'] = g
            if params.get('output') == 'gaps' and re.search(r'\bmost\b|\bhighest\b|\bfewest\b|\blowest\b', low): params.pop('output')  # ranking groups by gaps
            break
    # Words of the measure's own name and aliases ("kidney health evaluation ... eGFR plus uACR ... with diabetes") are explained by it.
    words = set()
    for text in [*(measures.library().get(mid) or {}).get('aliases', []), (measures.library().get(mid) or {}).get('name', ''),
                 (measures.library().get(mid) or {}).get('gap_label', '')]:
        words |= {w for w in re.findall(r'[a-z][a-z0-9-]+', text.lower()) if len(w) >= 3}
    if words: r.take(r'\b(?:' + '|'.join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r')s?\b', None, overlap=True)
    # A threshold that restates the measure's own definition ("poor control (>9%)") is explained by the measure; any other
    # number (a different age band, a different cut-off) stays unexplained so the question is not answered as asked.
    spec = measures.library().get(mid) or {}
    defined = {float(x) for x in re.findall(r'(?<![\d.])\d+(?:\.\d+)?', ' '.join([spec.get('name', ''), *[
        str((spec.get(k) or {}).get('summary', '')) for k in ('numerator', 'denominator')]]))}
    for m in re.finditer(r'\(?\s*(?:>=|<=|>|<|≥|≤|over |above |under |below )?\s*(?<![\d.,])(\d{1,3}(?:\.\d+)?)(?![\d.,])\s*%?\s*\)?', low):
        if float(m.group(1)) in defined: r.take(re.escape(m.group(0).strip()), 'measure definition restated', overlap=True)
    r.take(r'\bper 1,?000\b|\bper 100,?000\b|\bper thousand\b|\bmost\b|\bhighest\b|\blowest\b|\bfewest\b|\bworst\b|\bbest\b|\brank(?:ed)?\b|\bplus\b|\band\b|\bon\b|\bwith\b', None, overlap=True)
    period = _take_period(r, as_of)
    if not period and r.take(r'\bthis year\b|\bytd\b|\byear[- ]to[- ]date\b|\bso far\b', 'year to date'):
        period = (f'{as_of[:4]}-01-01', as_of)
    if not period and r.take(r'\blast year\b|\bprior year\b', 'last year'):
        y = int(as_of[:4]) - 1; period = (f'{y}-01-01', f'{y}-12-31')
    r.take(r'\b(?:over |in )?the (?:last|past) (?:12 months|year)\b|\b(?:rolling|trailing) (?:12 months|year)\b', 'last 12 months')
    if period: params['period'] = {'start': period[0], 'end': period[1]}
    r.take(MEASURE_FRAMING, None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason=f'measure {mid} named, but words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'vbc', 'inputs': [], 'parameters': params, 'purpose': f'Quality measure {mid}'}]}, info


# ---- outreach worklists ------------------------------------------------------------
# Intent needs an outreach/prioritisation verb AND a stated capacity; a plain "outreach list: everyone with X"
# (no capacity) stays a patient-set question.
OUTREACH_INTENT = (r'\boutreach\b|\bcare managers?\b|\bcare management\b|\bnurses?\b|\bcall(?:s|ed)?\b|\breach out\b|\bcontact\b|'
                   r'\bfollow[- ]?up\b|\bprioriti[sz]e\b|\bworklist\b|\bwork list\b')
OUTREACH_SLOTS = (r'\b(?:we have |with |for |capacity (?:of |for )?)?(\d{1,4}) (?:outreach |call |nurse |care[- ]manager )?(?:slots?|calls?|patients?|members?|people|contacts?)\b'
                  r'(?: (?:a|per|this|next) (?:week|month|day))?|'
                  r'\b(?:max(?:imum)?|top|up to|at most|no more than|limit(?: of)?|cap(?: of)?) (\d{1,4})\b(?! hours?)(?: (?:patients?|members?|people|slots?|calls?))?|'
                  r'\b(?:which|what|the) (\d{1,4}) (?:patients?|members?|people)\b')
OUTREACH_HOURS = r'\b(?:with |we have |for |capacity (?:of |for )?)?(\d{1,4}(?:\.\d+)?) (?:nurse |nursing |care[- ]manager |staff |rn )?hours?(?: of (?:nurse |nursing |care[- ]manager |staff )?(?:time|capacity))?(?: (?:a|per|this|next) (?:week|month))?\b'
OUTREACH_REQUIRE = [('care_gap', r'\b(?:with |who have |having )?(?:open |unclosed |outstanding )?(?:care |quality )?gaps?(?: in care| open)?\b'),
                    ('rising_risk', r'\b(?:with |who are )?(?:rising|increasing|emerging)[- ]risk\b'),
                    ('social_need', r'\b(?:with |who have )?(?:social needs?|sdoh|social determinants?(?: of health)?)\b'),
                    ('utilization', r'\b(?:with |who have )?(?:high |avoidable )(?:utili[sz]ation|ed use|admissions?)\b')]
OUTREACH_PHRASES = (r'\b(?:who|which|whom) should (?:our |the )?(?:care managers?|nurses?|team|we)?\s*(?:call|contact|reach out to|follow up with|see)\b|'
                    r'\b(?:build|create|make|generate|give|draft|pull|produce)(?: me| us)? (?:an? |the )?(?:prioriti[sz]ed )?(?:outreach |call |work ?)?(?:list|worklist|plan)\b|'
                    r'\bprioriti[sz]e\b|\bfor (?:nurse |care[- ]manager |rn )?(?:outreach|follow[- ]?up|calls?)\b|\b(?:nurse|care[- ]manager) follow[- ]?up\b|'
                    r'\b(?:give|get|yield|offer)s? (?:us )?the most (?:value|benefit|impact)(?: from (?:outreach|a call|calls))?\b|\bmost (?:value|valuable|benefit|impact)\b|'
                    r'\bfrom outreach\b|\boutreach(?: list)?\b|\bcare managers?\b|\bcall\b|\bcontact\b|\bworklist\b|\bthis (?:week|month)\b|\bnext (?:week|month)\b|'
                    r'\bpatients?\b|\bmembers?\b|\bpeople\b|\bour\b|\bwe\b|\bhave\b|\bshould\b|\bwho\b|\bwhich\b|\bfor\b|\bwith\b|\bof\b|\bthe\b|\ban?\b|\band\b|'
                    r'\bcapacity\b|\bslots?\b|\bhours?\b|\blist\b|\bpriority\b|\bby\b|\bgive\b|\bgives\b|\bvalue\b')


def _outreach_question(q, profile, as_of):
    low = _strip_framing(q.lower())
    if not re.search(OUTREACH_INTENT, low): return None
    slots, hours = re.search(OUTREACH_SLOTS, low), re.search(OUTREACH_HOURS, low)
    if not slots and not hours: return None
    r = Reader(low); r.profile = profile
    info = {'compiler': VERSION, 'recognised': r.notes, 'paraphrased': [], 'route': 'local_compiler'}
    params = {}
    for m in r.take(OUTREACH_HOURS, 'capacity hours'):
        params['capacity_hours'] = float(m.group(1)) if '.' in m.group(1) else int(m.group(1))
    for m in r.take(OUTREACH_SLOTS, 'capacity patients'):
        params['capacity_patients'] = int(next(g for g in m.groups() if g))
    for m in r.take(r'\b(?:at least|minimum(?: of)?|min(?:imum)?) (\d{1,4}) (?:patients? )?(?:per|from each|for each|in each) (?:practice|clinic)\b', 'practice floor'):
        params['min_per_practice'] = int(m.group(1))
    for m in r.take(r'\b(?:at most|no more than|max(?:imum)?(?: of)?|up to) (\d{1,4}) (?:patients? )?(?:per|from each|for each|in each|from any(?: one| single)?) (?:practice|clinic)\b', 'practice cap'):
        params['max_per_practice'] = int(m.group(1))
    if r.take(r'\b(?:by|per|for each|each|across) (?:practice|clinic)s?\b', 'group by practice'): params['group_by'] = 'practice'
    elif r.take(r'\b(?:by|per|for each|each) (?:pcp|primary care (?:provider|physician|clinician)|physician|provider|clinician)s?\b', 'group by pcp'):
        params['group_by'] = 'pcp'
    require = [cat for cat, rx in OUTREACH_REQUIRE if r.take(rx, f'require {cat}')]
    if require: params['require'] = require
    conditions = []
    for c, aliases in CONDITION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if r.take(_alias_pattern(alias), f'condition {c}'): conditions.append(c); break
    r.take(r'\b(?:heart failure|hf|diabet\w+|ckd|copd|hypertensive) (?:patients|members|population|panel)\b', None, overlap=True)
    r.take(OUTREACH_PHRASES, 'outreach', overlap=True)
    r.take(r'\b(?:this|next|a|per) (?:week|month)\b|\bweekly\b|\bnurse\b|\bnurses\b|\bteam\b|\bto\b|\bcan\b|\bget\b|\bis\b|\bare\b|\bmost\b|\bfrom\b', None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        info.update(route='fallback', reason='outreach words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    nodes = []
    if conditions:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [],
                      'parameters': {'specification': {'all': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20], 'any': [], 'exclude': []}},
                      'purpose': 'Population named in the question'})
    nodes.append({'id': 'outreach', 'operation': 'outreach', 'inputs': ['population'] if conditions else [], 'parameters': params,
                  'purpose': 'Prioritised outreach worklist within the stated capacity'})
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


def _patient_question(q, r, profile, pids, info):
    low = r.low
    stat = 'max' if r.take(r'\b(?:maximum|max|highest)\b', 'max') else 'min' if r.take(r'\b(?:minimum|min|lowest)\b', 'min') else \
           'latest' if r.take(r'\b(?:latest|most recent|last)\b', 'latest') else None
    concept = None
    for c, aliases in MEASUREMENT_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            if r.take(_alias_pattern(a), f'measurement {c}'): concept = c; break
        if concept: break
    r.take(r'\b(?:in percent|percent|including undated results|return a numeric value|what is the|for)\b')
    info['unexplained'] = r.blocking(profile)
    if info['unexplained'] or not concept or not stat:
        info.update(route='fallback', reason='patient question not fully interpreted: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    ids = ','.join(f"'{p}'" for p in pids)
    agg = {'max': 'max(value_canonical)', 'min': 'min(value_canonical)'}.get(stat)
    sql = (f"SELECT {agg} FROM measurement WHERE concept='{concept}' AND patient_id IN ({ids})" if agg else
           f"SELECT value_canonical FROM measurement WHERE concept='{concept}' AND patient_id IN ({ids}) AND value_canonical IS NOT NULL "
           f"ORDER BY event_date DESC, available_date DESC LIMIT 1")
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'value'},
                       'purpose': f'{stat} {concept} for {", ".join(pids)}'}]}, info


# ---- time-to-event questions -> survival ------------------------------------------
SURVIVAL_INTENT = (r'\b[a-z]+-free survival\b|\bkaplan[- ]meier\b|\bk-?m (?:curves?|estimates?|analysis)\b|\bsurvival (?:curves?|analys[ie]s|estimates?)\b|'
                   r'\btime[- ]to[- ](?:event|first|next)\b|\btime (?:to|until|till) (?:a |an |the )?(?:first |next )?\w|\bcumulative incidence\b|'
                   r'\bhazard ratios?\b|\bcox (?:model|regression|proportional)|\b(?:readmission|readmit|follow[- ]?up|ed visit|er visit|emergency visit) curves?\b|'
                   r'\bhow (?:long|many days|quickly|soon) (?:until|till|before|after|does it take|do(?:es)? it take|(?:do|does|did) (?:\w+ ){1,4}(?:take|wait) to)\b|'
                   r'\bdays (?:to|until|till) (?:a |an |the )?(?:first |next )?(?:follow|readmi|ed\b|er\b|emerg)')
DEATH_WORDS = r"\b(?:die|dies|dying|died|deaths?|mortality|deceased|passed away|expired|vital status|still alive|didn'?t make it|did not make it|overall survival)\b"
SURVIVAL_DISCHARGE = r'\b(?:(?:after|following|from|since) (?:a |the |their |each |hospital |inpatient )*)?(?:post[- ])?(?:hospital |inpatient )?discharge[sd]?\b|\bindex (?:stays?|admissions?)\b'
SURVIVAL_ONSET = r'\b(?:after|since|from|following) (?:(?:their|the|first|initial|a) )*(?:diagnosis|diagnoses|dx|onset|cohort entry|entry into the cohort|entry)\b'
SURVIVAL_GROUP_CONDITION = r'\b(?:by (?:whether (?:they|patients|members) (?:have|had) )?|(?:with|having) (?:vs\.?|versus|or) without )({})(?: status)?\b'
COX_WORDS = r'\bcox(?: (?:model|regression|proportional hazards?(?: model| regression)?))?\b|\bhazard ratios?\b'


def survival_intent(low):
    return bool(re.search(SURVIVAL_INTENT, low))


def _survival_question(q, r, profile, as_of, info):
    """Time-to-event questions: decide event, time zero, groups and population, or decline (fallback)."""
    low = r.low
    if not survival_intent(low): return None
    decline = lambda reason: (info.update(route='fallback', reason=reason), (None, info))[1]
    # event (exactly one)
    events = []
    if re.search(r'\breadmi(?:ssions?|tted|ts?)\b', low): events.append(('readmission', {'type': 'readmission'}, r'\b(?:30[- ]day |hospital )?readmi(?:ssions?|tted|ts?)\b'))
    if re.search(r'\bfollow[- ]?ups?\b', low):
        events.append(('followup', {'type': 'followup'}, r'\b(?:a |an |first )?(?:outpatient )?follow[- ]?ups?(?: (?:visits?|appointments?|contacts?|care))?\b'))
    found = None
    for concept, aliases in MEASUREMENT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for op_pattern, op in COMPARATORS:
                if op not in ('lt', 'le', 'gt', 'ge'): continue
                rx = (_alias_pattern(alias) + r'(?: levels?| values?| results?)?\s*(?:(?:to |that |is |are )?(?:drop|fall|go|decline|slip|rise|climb|cross|get|reach)\w*\s*)?'
                      r'(?:to )?' + op_pattern + r'\s*(?:an? |the )?' + NUMBER + r'(?![\d.])')
                m = re.search(rx, low)
                if m: found = ('measurement', {'type': 'measurement', 'concept': concept, 'op': op, 'value': float(m.group(1))}, re.escape(m.group(0))); break
            if found: break
        if found: break
    if found: events.append(found)
    for concept, aliases in ENCOUNTER_ALIASES.items():
        hit = next((a for a in sorted(aliases, key=len, reverse=True) if re.search(_alias_pattern(a), low)), None)
        if hit:
            events.append(('encounter', {'type': 'encounter', 'concept': concept}, r'\b(?:a |an |the |their )?(?:first |next )?' + _alias_pattern(hit)))
    if not events and (re.search(DEATH_WORDS, low) or re.search(r'\bsurviv', low)):  # survival of patients = mortality
        info.update(route='local_unsupported', unexplained=[])
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: death or mortality records (none are received)']}, info
    if not events:  # explicit survival vocabulary is ours to decline; "how many days ... until" may belong to another route
        strong = re.search(r'kaplan|\bk-?m\b|survival|cumulative incidence|hazard ratio|\bcox\b|time[- ]to[- ]event|\bcurves?\b', low)
        return decline('time-to-event question without a recognised event') if strong else None
    if len(events) > 1: return decline('time-to-event question names more than one event: ' + ', '.join(e[0] for e in events))
    kind, event, event_rx = events[0]
    r.take(SURVIVAL_INTENT, 'time to event')
    r.take(event_rx, f'event {kind}')
    # time zero
    index = None
    if r.take(SURVIVAL_DISCHARGE, 'index discharge') or kind in ('readmission', 'followup'):
        index = {'type': 'discharge'}
    onset = r.take(SURVIVAL_ONSET, 'index cohort entry')
    fixed = r.take(r'\b(?:after|since|from|starting(?: on| from)?) (\d{4}-\d{2}-\d{2}|\d{4})\b', 'index date')
    if sum(map(bool, (index, onset, fixed))) > 1: return decline('more than one time zero named')
    if fixed:
        d = fixed[0].group(1); index = {'type': 'date', 'date': d if len(d) == 10 else f'{d}-01-01'}
        if index['date'] > as_of: return decline('time zero after the snapshot')
    # horizon and time points
    params = {}
    for m in r.take(r'\b(\d{1,4})((?:\s*(?:/|,|and|or)\s*\d{1,4})*)[- ]days?\b(?! (?:after|before|of))', 'day marks'):
        days = [int(m.group(1))] + [int(x) for x in re.findall(r'\d+', m.group(2))]
        params['time_points'] = sorted(set(days)); params['horizon_days'] = max(days)
    for m in r.take(r'\b(?:within|over|in|during) (?:the )?(?:first |next )?(\d{1,4}|one|two|three|five) (days?|weeks?|months?|years?)\b', 'horizon'):
        n = int(m.group(1)) if m.group(1).isdigit() else {'one': 1, 'two': 2, 'three': 3, 'five': 5}[m.group(1)]
        params['horizon_days'] = int(round(n * {'d': 1, 'w': 7, 'm': 30.44, 'y': 365.25}[m.group(2)[0]]))
    if not 1 <= params.get('horizon_days', 1) <= 3650: return decline('horizon must be 1 day to ten years')
    if 'horizon_days' in params and ('time_points' not in params or max(params['time_points']) > params['horizon_days']):
        h = params['horizon_days']
        base = (7, 14, 30, 60, 90) if index and index['type'] == 'discharge' else (30, 90, 180, 365, 730)
        params['time_points'] = sorted({x for x in base if x < h} | {h})[-12:]
    # groups
    group, group_concept = 'none', None
    for concept, aliases in CONDITION_ALIASES.items():
        alt = '|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
        if r.take(SURVIVAL_GROUP_CONDITION.format(alt), f'group by {concept}'):
            group, group_concept = 'condition', concept; break
    if group == 'none':
        for pattern, g in GROUPS:
            if r.take(pattern, f'group by {g}'):
                if g in ('facility',) + TIME_GROUPS: return decline(f'survival curves are not split by {g}')
                group = g; break
    r.take(r'\b(?:compar\w+|vs\.?|versus|across|between)\b', None)
    # Cox model
    cox = bool(r.take(COX_WORDS, 'cox model'))
    covariates = []
    adj = next(iter(r.take(r'\b(?:adjust(?:ed|ing)?|control(?:led|ling)?) for ([a-z ,&-]+?)(?=[.?;]|$| by\b)', 'adjusted for')), None)
    if adj:
        cox = True
        for word in re.split(r',|\band\b|&', adj.group(1)):
            word = word.strip()
            if not word: continue
            if re.fullmatch(r'age(?: bands?| groups?)?', word): covariates.append('age_band')
            elif re.fullmatch(r'sex|gender', word): covariates.append('sex')
            elif re.fullmatch(r'payer|payor|insurance|coverage(?: type)?', word): covariates.append('coverage_type')
            else:
                c = next((c for c, al in CONDITION_ALIASES.items() if word in al), None)
                if c is None: return decline(f'unrecognised adjustment variable: {word}')
                covariates.append(c)
    # time zero window: "discharges in 2028"
    period = _take_period(r, as_of)
    if period and not (index and index['type'] == 'discharge'): return decline('a period restricts index discharges only')
    r.take(r'\b(?:curves?|plots?|estimates?|show(?: me)?|give(?: me)?|what(?:\'s| is| are)|draw|chart|get|gets|getting|see|seen|until|till|before|after|'
           r'take|takes|took|wait|first|next|time|times|days?|incidence|cumulative|survival|analysis|how|long|rates?|probability|risk|event)\b', None)
    # population: whatever is left must be a cohort the population grammar fully explains
    spec = {'all': [], 'any': [], 'exclude': []}
    if r.leftover():
        plan, info = _population_question(q, r, profile, as_of, info)
        if plan is None and info.get('reason') == 'no population or statistic recognised':
            plan = {'nodes': [{'operation': 'cohort', 'parameters': {'specification': spec}}]}  # whole population
            info.pop('route', None); info.pop('reason', None)
        if plan is None: return None, info
        if len(plan['nodes']) != 1 or plan['nodes'][0]['operation'] != 'cohort':
            return decline('time-to-event question also asks for another statistic')
        spec = plan['nodes'][0]['parameters']['specification']
    else:
        info['unexplained'], info['ignored_words'] = [], getattr(r, 'ignored', [])
    named = [a['concept'] for a in spec['all'] if a.get('kind') == 'concept' and a.get('concept') in concepts.CONDITIONS]
    if index is None or onset:
        if len(named) != 1:
            return decline('no time zero: name a discharge, a date, or exactly one condition whose first record is cohort entry')
        index = {'type': 'condition_first_record', 'concept': named[0]}
    if period: index['window'] = {'start': period[0], 'end': period[1]}
    params.update(index=index, event=event, group_by=group)
    if group_concept: params['group_concept'] = group_concept
    if cox: params['cox'] = True
    if covariates: params['covariates'] = covariates
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
        {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec}, 'purpose': 'Population named in the question'},
        {'id': 'survival', 'operation': 'survival', 'inputs': ['population'], 'parameters': params,
         'purpose': f'Time from {index["type"].replace("_", " ")} to {kind}' + (f' by {group_concept or group}' if group != 'none' else '')}]}, info

# ---- demand: volume, bed and staffing forecasts ---------------------------------------
DEMAND_VERB = (r"\bforecast(?:s|ed|ing)?\b|\bproject(?:ed|ion|ions|ing)?\b|\bpredict(?:ed|ion|ions|ing)?\b|\banticipat(?:e|ed|ing)\b|"
               r"\b(?:will|do|would|should) we (?:need|see|have|expect)\b|\bhow many [a-z /-]{1,40}? (?:will|do|would|should) we need\b|"
               r"\b(?:next|coming|upcoming) (?:\d{1,3} |one |two |three |four |six |twelve )?(?:weeks?|months?|quarters?|years?)(?:'s)?\b|\bplan for\b")
DEMAND_MEASURES = [  # (measure, pattern) - order matters: specific settings before generic words
    ('appointments', r"\b(?:primary care |clinic |pcp |outpatient )?appointments?(?: demand| volumes?| counts?)?\b"),
    ('ed_visits', r"\b(?:ed|er|emergency(?: department| room)?)(?: department)? (?:visits?|volumes?|arrivals?|encounters?|demand|census|use|utili[sz]ation)\b"),
    ('observation_stays', r"\bobservation (?:stays?|visits?|admissions?|volumes?)\b"),
    ('urgent_care_visits', r"\burgent care(?: visits?| volumes?| demand)?\b"),
    ('primary_care_visits', r"\bprimary care (?:visits?|volumes?|demand|encounters?)\b"),
    ('specialist_visits', r"\bspecialt?(?:y|ist) (?:visits?|volumes?|demand|encounters?)\b"),
    ('bed_days', r"\b(?:inpatient |hospital |staffed )?beds?(?:[- ]days?)?\b|\b(?:inpatient |hospital |daily )?census\b|\boccupancy\b"),
    ('discharges', r"\b(?:hospital |inpatient )?discharges?\b"),
    ('inpatient_admissions', r"\b(?:inpatient |hospital )?(?:admissions?|admits?|hospitali[sz]ations?)\b"),
]
DEMAND_WORDS = r'\b(?:visits?|volumes?|demand|encounters?|utili[sz]ation)\b'
STAFF_WORDS = r"\b(?:nurses?|nursing(?: staff)?|rns?|care managers?|case managers?|staff(?:ing)?|ftes?|headcount|clinicians? needed)\b"
NUMBER_WORDS_DEMAND = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'six': 6, 'twelve': 12}


def demand_intent(low):
    """A volume forecast: a forward-looking verb plus a volume, capacity or staffing noun, and not a per-patient projection."""
    if not re.search(DEMAND_VERB, low): return False
    if not (any(re.search(p, low) for _, p in DEMAND_MEASURES) or re.search(DEMAND_WORDS, low) or re.search(STAFF_WORDS, low)): return False
    if any(re.search(_alias_pattern(a), low) for aliases in MEASUREMENT_ALIASES.values() for a in aliases): return False  # lab trajectories
    if re.search(r'\b(?:which|who|whose|list)\b(?: [a-z]+){0,3} (?:patients|members|people)\b|\b(?:patients|members) (?:are |who are |that are )?'
                 r'(?:likely|projected|expected|predicted|at risk|going) to\b|\bp-[a-z0-9]+\b', low):
        return False
    return True


def _demand_question(q, profile, as_of):
    q, paraphrased = _paraphrase(q, as_of)
    r = Reader(q); r.profile = profile
    info = {'compiler': VERSION, 'recognised': r.notes, 'paraphrased': paraphrased}
    decline = lambda reason: (info.update(route='fallback', reason=reason), (None, info))[1]
    low = r.low
    params = {}
    # staffing ratios first (their nouns must not be read as measures)
    for m in r.take(r'\b(\d{1,4}(?:\.\d+)?) (?:staff |nurse |nursing |rn |care[- ]manager )?min(?:ute)?s? (?:of (?:staff |nurse |nursing )?time )?'
                    r'(?:per|for each|each|a|an) [a-z-]+\b', 'minutes per unit'):
        params['minutes_per_unit'] = float(m.group(1))
    for m in r.take(r'\b(\d{1,2}(?:\.\d+)?) (?:productive |worked |paid )?hours? (?:per|a|each) (?:fte|nurse|rn|staff member|care manager)?(?:[- ]?week)?'
                    r'(?: per week| a week)?\b', 'productive hours per fte-week'):
        params['fte_hours_per_week'] = float(m.group(1))
    if r.take(STAFF_WORDS, 'staffing'): params['staffing'] = True
    # occupancy target
    for m in r.take(r'\b(?:at |with |using )?(?:an? )?(?:target )?(\d{2,3}(?:\.\d+)?)\s*%\s*(?:target )?occupancy(?: target| rate)?\b|'
                    r'\b(?:target )?occupancy (?:target |rate |of |at |goal )*(\d{2,3}(?:\.\d+)?)\s*%', 'target occupancy'):
        v = float(m.group(1) or m.group(2))
        if not 30 <= v <= 100: return decline('occupancy target must be 30-100%')
        params['target_occupancy'] = v / 100
    # measure (exactly one)
    found = []
    for measure, pattern in DEMAND_MEASURES:
        if r.take(pattern, f'measure {measure}'): found.append(measure)
    if 'bed_days' in found and len(found) > 1:  # "beds needed for next quarter's admissions": beds are the answer, bed-days the series
        found = ['bed_days']
    if 'discharges' in found and 'inpatient_admissions' in found: return decline('volume forecast names both admissions and discharges')
    if len(found) != 1:
        return decline('volume forecast names more than one series: ' + ', '.join(found) if found else
                       'volume forecast without a recognised series (name ED visits, admissions, appointments, discharges or beds)')
    params['measure'] = found[0]
    # frequency and horizon
    freq = None
    if r.take(r'\b(?:weekly|per week|by week|each week|week by week|week-by-week)\b', 'weekly'): freq = 'week'
    if r.take(r'\b(?:monthly|per month|by month|each month|month by month|month-by-month)\b', 'monthly'):
        if freq: return decline('both weekly and monthly requested')
        freq = 'month'
    horizon = None
    for m in r.take(r"\b(?:for |over |in |during )?(?:the )?(?:next|coming|upcoming) (?:(\d{1,3}|one|two|three|four|six|twelve) )?(weeks?|months?|quarters?|years?)(?:'s)?\b|"
                    r"\b(\d{1,3})[- ](weeks?|months?)(?: ahead| out| horizon| forecast)?\b", 'horizon'):
        n = m.group(1) or m.group(3); unit = (m.group(2) or m.group(4))[0]
        n = int(n) if n and n.isdigit() else NUMBER_WORDS_DEMAND.get(n, 1)
        if unit == 'w':
            freq = freq or 'week'
            horizon = n if freq == 'week' else max(1, int(-(-n // 4.345)))
        else:
            months = n * {'m': 1, 'q': 3, 'y': 12}[unit]
            freq = freq or 'month'
            horizon = months if freq == 'month' else int(round(months * 4.345))
    freq = freq or 'month'
    if horizon is not None:
        if not 1 <= horizon <= (24 if freq == 'month' else 104): return decline(f'forecast horizon beyond {24 if freq == "month" else 104} {freq}s')
        params['horizon'] = horizon
    params['frequency'] = freq
    # groups
    for pattern, g in [(r'\b(?:by|per|for each|each|across) (?:practice|clinic)s?\b', 'practice'),
                       (r'\b(?:by|per|for each|each|across) (?:facilit(?:y|ies)|hospitals?|sites?|locations?)\b', 'facility'),
                       (r'\b(?:by|per|for each|each|across) (?:payer|payor|insurance|plan)s?\b', 'payer')]:
        if r.take(pattern, f'group by {g}'): params['group_by'] = g; break
    if re.search(r'\bby (?:pcp|provider|physician|clinician|age|sex|gender|condition|diagnosis)\b', r.low):
        return decline('volume forecasts are grouped by practice, facility or payer only')
    # wording that only restates a forecast
    r.take(DEMAND_VERB, 'forecast', overlap=True)
    r.take(r"\b(?:how many|what|what's|will|we|need|needed|needs|do|does|would|should|be|is|are|the|our|for|of|with|a|an|to|and|in|over|"
           r"uncertainty|(?:prediction |confidence )?intervals?|ranges?|bands?|error bars?|estimates?|estimated?|expected|outlook|plan|planning|"
           r"volumes?|demand|counts?|total|numbers?|levels?|capacity|required|requirements?|going|look|like|give|me|us|show|"
           r"'s|ahead|future|period|periods|please|roughly|about)\b", None, overlap=True)
    spec = {'all': [], 'any': [], 'exclude': []}
    if r.leftover():
        plan, info = _population_question(q, r, profile, as_of, info)
        if plan is None and info.get('reason') == 'no population or statistic recognised':
            plan = {'nodes': [{'operation': 'cohort', 'parameters': {'specification': spec}}]}
            info.pop('route', None); info.pop('reason', None)
        if plan is None: return None, info
        if len(plan['nodes']) != 1 or plan['nodes'][0]['operation'] != 'cohort':
            return decline('volume forecast also asks for another statistic')
        spec = plan['nodes'][0]['parameters']['specification']
    else:
        info['unexplained'], info['ignored_words'] = [], getattr(r, 'ignored', [])
    from .demand import validate
    params = validate(params)
    info['route'] = 'local_compiler'
    restricted = any(spec[k] for k in spec)
    nodes = ([{'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec}, 'purpose': 'Population named in the question'}]
             if restricted else [])
    nodes.append({'id': 'demand', 'operation': 'demand', 'inputs': ['population'] if restricted else [], 'parameters': params,
                  'purpose': f"Forecast {params['measure'].replace('_', ' ')} per {params['frequency']}, {params['horizon']} ahead"
                             + (f" by {params['group_by']}" if params['group_by'] != 'none' else '')})
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


# ---- fair provider/practice profiling (hierarchical Bayes + credibility) -------------------
PROFILE_STRONG = (r"\bfair(?:ly|er|est|ness)?\b|\b(?:adjust\w*|account\w*|controll?\w*|correct\w*) for (?:their |the |different |differences in )?panel sizes?\b|"
                  r"\bpanel[- ]size[- ](?:adjusted|fair)\b|\btruly (?:high|low|higher|lower|worse|better|different|above|below|an outlier|outliers?)\b|\bcredib\w*\b|"
                  r"\bshrunk(?:en)?\b|\bshrinkage\b|\bhierarchical\b|\bempirical[- ]bayes\b|\bbuhlmann\b|\bbühlmann\b|\bcaterpillar\b|\bstatistically (?:high|low|higher|lower)\b|"
                  r"\b(?:risk|case[- ]mix)[- ]adjusted\b")
PROFILE_WEAK = (r"\boutliers?\b|\brank(?:s|ed|ing)?\b|\bhow (?:do|does) (?:our |the |each |individual )?(?:pcps?|practices?|providers?|physicians?|clinicians?|doctors?|clinics?) compare\b|"
                r"\bcompare (?:our |the )?(?:pcps|practices|providers|physicians|clinicians|doctors|clinics)\b")
PROFILE_UNITS = [(r"\b(?:practices?|clinics?)\b", 'practice'),
                 (r"\b(?:pcps?|primary care (?:providers?|physicians?|doctors?|clinicians?)|physicians?|providers?|doctors?|clinicians?|dr\.?)\b", 'pcp')]
PROFILE_METRICS = [
    ('readmission_rate', r"\b(?:30[- ]day )?readmi(?:ssions?|ts?)(?: rates?)?\b"),
    ('ed_visits', r"\b(?:ed|er|emergency(?: department| room)?) (?:use|visits?|utili[sz]ation|rates?|visit rates?)\b|\bemergency visits?\b"),
    ('urgent_care_visits', r"\burgent[- ]care(?: visits?| use)?\b"),
    ('primary_care_visits', r"\b(?:primary[- ]care|office) visits?\b"),
    ('admissions', r"\b(?:inpatient |hospital )?(?:admissions?|admits?)(?: rates?)?\b|\bhospitali[sz]ations?(?: rates?)?\b"),
    ('a1c_poor_control', r"\b(?:hb)?a1c(?: poor)? control\b|\bpoor(?:ly)? controlled (?:diabetes|a1c)\b|\bpoor (?:a1c|glycemic|diabetes) control\b|\b(?:glycemic|diabetes) control\b"),
    ('bp_control', r"\b(?:bp|blood pressure|hypertension) control\b|\bcontrolled (?:bp|blood pressure)\b"),
    ('statin_therapy_diabetes', r"\bstatin (?:therapy|use|prescribing)\b"),
    ('pmpm', r"\bpmpm\b|\bper member per month\b|\btotal cost of care\b|\b(?:medical |total |allowed )?(?:costs?|spend(?:ing)?)\b"),
]
PROFILE_FILLER = (r"\b(?:which|what|who|whose|is|are|was|were|do|does|have|has|had|the|our|their|its|a|an|of|on|in|for|by|to|and|or|with|after|"
                  r"accounting|account|adjusting|adjusted|adjust|controlling|taking|into|consideration|truly|really|actually|high|higher|highest|low|lower|lowest|"
                  r"worse|worst|better|best|different|outliers?|outlier|rank|ranks|ranked|ranking|fair|fairly|compare|comparison|comparing|"
                  r"show|give|me|us|list|rate|rates|use|utilization|level|levels|panel|panels|size|sizes|patients?|members?|population|network|"
                  r"average|than|vs|versus|others|peers|each|every|all|between|across|statistically|significantly|meaningfully|really|"
                  r"weighted|weighting|weights?|once|using|you|we|when|estimates?|scores?|performance|perform|performing|stands?|out|there|any|how|much|many|be|it|this|that|those|these|"
                  r"attributed|assigned|per (?:attributed |assigned )?(?:patient|member)|per (?:patient|member) on (?:their|the) panel)\b")


def _profile_question(q, r, profile, as_of, info):
    low = r.low
    strong = re.search(PROFILE_STRONG, low)
    if not strong and not re.search(PROFILE_WEAK, low): return None
    if re.search(r"\bshared[- ]savings\b|\bsettlement\b|\bshapley\b|\ballocat\w*\b|\bsplit\b|\bbilling\b|\bclaims? (?:integrity|review)\b", low): return None
    units = [(m.start(), u) for pattern, u in PROFILE_UNITS for m in re.finditer(pattern, low)]
    if not units: return None
    metric = next((m for m, pattern in PROFILE_METRICS if re.search(pattern, low)), None)
    decline = lambda reason: (info.update(route='fallback', reason=reason), (None, info))[1]
    if metric is None:
        return decline('provider/practice profiling question without a recognised measure') if strong else None
    params = {'metric': metric}
    # focus unit: "Dr X", "practice WH-PRACTICE-3"
    m = re.search(r"\b(?:dr\.?|doctor) ([a-z0-9][a-z0-9_.-]*?)(?:'s)?(?=[\s,?.]|$)", low) or \
        re.search(r"\b(?:pcp|provider|clinician|practice|clinic) ([a-z0-9_.-]*\d[a-z0-9_.-]*?)(?:'s)?(?=[\s,?.]|$)", low)
    if m:
        params['focus'] = m.group(1)
        r.take(re.escape(m.group(0)), 'focus unit')
        params['unit'] = 'practice' if re.match(r"(?:practice|clinic)", m.group(0)) else 'pcp'
    else:
        params['unit'] = min(units)[1]
    for pattern, _ in PROFILE_UNITS: r.take(pattern, 'profiled unit')
    for name, pattern in PROFILE_METRICS:
        if name == metric: r.take(pattern, f'metric {metric}')
    for t in r.take(r"\b(?:above|over|exceed\w*|higher than|more than|worse than|beyond) (?:a |the |our )?(?:threshold|target|goal|benchmark|cutoff)?(?: of)? ?"
                    r"\$?(\d+(?:\.\d+)?)\s*(%|percent| per 1000| per thousand| pmpm)?", 'threshold'):
        params['threshold'] = float(t.group(1))
    r.take(PROFILE_STRONG, 'fair comparison', overlap=True)
    if r.take(r"\bunadjusted\b|\bwithout (?:any )?(?:case[- ]mix |risk )?adjust\w*\b|\braw\b|\bcrude\b", 'no adjustment'):
        params['adjust'] = 'none'
    if r.take(r"\b(?:chronic[- ]condition|illness|disease|comorbidit\w*) burden\b|\bcomorbidit\w*\b|\bchronic conditions?\b|\bhow sick\b|\bacuity\b", 'adjust conditions'):
        params['adjust'] = 'age_sex_conditions'
    elif r.take(r"\bcase[- ]mix(?:[- ]adjusted)?\b|\brisk[- ]adjusted\b|\bage(?: band)?,? (?:and |& )?(?:sex|gender)\b|\bdemographics?\b", 'adjust age sex',
                overlap=True):
        params.setdefault('adjust', 'age_sex')
    if r.take(r"\bcredib\w*\b|\bbuhlmann(?:[- ]straub)?\b|\bbühlmann(?:[- ]straub)?\b|\bactuarial\w*\b", 'credibility', overlap=True):
        params['method'] = 'credibility'
    elif r.take(r"\bbayes(?:ian)?\b|\bhierarchical\b|\bshrunk(?:en)?\b|\bshrinkage\b|\bmultilevel\b", 'bayes', overlap=True):
        params['method'] = 'bayes'
    period = _take_period(r, as_of)
    if period:
        params['period'] = {'start': period[0], 'end': period[1]}
    elif r.take(r"\b(?:over |in |during )?the (?:last|past) (?:12 months|year)\b|\blast year\b", 'last 12 months'):
        pass  # the operator default: the 12 months ending at the snapshot
    r.take(r"\bper 1000(?: member[- ]years?)?\b|\bper thousand\b|\bmember[- ]years?\b|\bvisits?\b|\bcase[- ]mix\b|\bmix\b|\bcase\b", None, overlap=True)
    r.take(PROFILE_FILLER, None, overlap=True)
    # population: whatever is left must be a cohort the population grammar fully explains
    spec = None
    if r.leftover():
        info['unexplained'] = r.blocking(profile)
        if info['unexplained']:
            plan, info2 = _population_question(q, r, profile, as_of, info)
            if plan is None or len(plan['nodes']) != 1 or plan['nodes'][0]['operation'] != 'cohort':
                return decline('profiling words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
            spec = plan['nodes'][0]['parameters']['specification']
    info['unexplained'], info['ignored_words'] = [], getattr(r, 'ignored', [])
    from .profile import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return decline(f'profile parameters: {exc}')
    info['route'] = 'local_compiler'
    nodes = []
    if spec is not None:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec}, 'purpose': 'Population named in the question'})
    nodes.append({'id': 'profile', 'operation': 'profile', 'inputs': ['population'] if spec is not None else [], 'parameters': params,
                  'purpose': f"Fair comparison of {params['unit']}s on {params['metric']}" + (f" (focus {params['focus']})" if params['focus'] else '')})
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


# ---- program effect ("did the program work?") -------------------------------------
EFFECT_QUESTION = (r'\b(?:effect|impact|influence)s? of\b|\bestimate (?:the |an? )?(?:effect|impact)\b|'
                   r'\b(?:did|does|do|has|have|is|was)\b[^?.;]{0,90}?\b(?:reduce[ds]?|lower(?:ed|s)?|cut|prevent(?:ed|s)?|improve[ds]?|'
                   r'increase[ds]?|decrease[ds]?|drive down|drove down|slow(?:ed|s)?|change[ds]?|affect(?:ed|s)?|work(?:ed|s)?|help(?:ed|s)?|bend)\b')
# Demands the data cannot meet even with a named program: identified causal effects and counterfactual counts. These fall
# through to the general UNSUPPORTED 'causal' refusal (which names the missing causal design and counterfactual model).
IDENTIFIED_CAUSAL = (r'\bcausal(?:ly)?\b|\bcause[ds]?\b|\battributable\b|\bidentified (?:causal )?effect\b|\bprevented by\b|'
                     r'\bhow many\b[^?.]{0,80}\bprevent(?:ed)?\b|\bcounterfactual\b')
RANDOMISED = r'\brandomi[sz](?:ed|ation)\b|\brct\b|\btrial\b'
INTERVENTION_WORDS = (r'\bprograms?\b|\binterventions?\b|\binitiatives?\b|\bpilots?\b|\bcampaigns?\b|\boutreach\b|\bjoin(?:ing|ed)? (?:the |an |our )?acos?\b|'
                      r'\blaunch(?:ed|ing)?\b|\brolled out\b|\bimplement(?:ed|ing|ation)\b|\bintroduc(?:ed|ing|tion)\b')
EFFECT_PROGRAMS = [  # program -> (pattern, received stream that identifies exposure)
    ('transitions_of_care', r'transitions?[- ]of[- ]care|\btoc\b|\btcm\b|\btransitional care management\b|'
                            r'post[- ]discharge (?:calls?|contacts?|follow[- ]?up calls?)|care transitions? (?:calls?|contacts?|program)',
     'transition_of_care_contacts'),
    ('care_management', r'\b(?:care|case) management\b|\bcare managers?\b', 'care_management_contacts'),
    ('community_services', r'\bcommunity (?:service|resource)s?\b|\bsocial service referrals?\b', 'community_service_outcomes'),
    ('care_coordination', r'\bcare coordination\b', 'care_coordination_contacts'),
]
EFFECT_OUTCOMES = [
    ('admissions', r'\b(?:re)?admissions?\b|\badmits?\b|\bhospitali[sz]ations?\b|\binpatient (?:stays?|admissions?)\b'),
    ('ed_visits', r'\b(?:ed|er|emergency(?: department| room)?) (?:visits?|use|utili[sz]ation)\b|\bemergency visits?\b'),
    ('pmpm', r'\bpmpm\b|\bper member per month\b|\btotal cost of care\b|\b(?:medical |total )?(?:costs?|spend(?:ing)?)\b'),
]
MEDICATION_STREAMS = ('pharmacy_dispenses', 'pharmacy_claims', 'medication_orders')


def _effect_unsupported(q, missing, info):
    return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
            'unmet_requirements': [f'Missing input: {m}' for m in missing]}, {**info, 'route': 'local_unsupported', 'unexplained': []}


# ---- calibration of Agere's own predictions -------------------------------------------
PREDICTION_WORDS = r'(?:risk scores?|risk models?|forecasts?|projections?|predictions?|predicted|outreach (?:values?|scores?))'
CALIBRATION_INTENT = (r'\b(?:well[- ]|poorly |mis)?calibrat\w*|\bhow (?:accurate|reliable|trustworthy|good|well)\b[^.?]{0,40}\b' + PREDICTION_WORDS +
                      r'|\b(?:accuracy|reliability|coverage|backtest\w*|brier|expected calibration error|ece)\b[^.?]{0,40}\b' + PREDICTION_WORDS +
                      r'|\b' + PREDICTION_WORDS + r'\b[^.?]{0,40}\b(?:accura\w+|reliab\w+|trust\w*|backtest\w*|coverage)\b'
                      r'|\b(?:can|should) we trust\b[^.?]{0,30}\b' + PREDICTION_WORDS)


BRIEFING_TRIGGER = (r"\b(?:(?:morning|daily|today'?s|this week'?s|weekly)\s+)?briefing\b|"
                    r"\bwhat (?:should|do) (?:i|we) (?:need to )?(?:look at|focus on|know|pay attention to|worry about|review|prioriti[sz]e)\b|"
                    r"\bwhat needs (?:my|our) attention\b|\banything (?:i|we) should (?:know|look at|worry about)\b|"
                    r"\bwhat stands out\b|\bwhere should (?:i|we) (?:start|focus)\b|\bwhat(?:'s| is) (?:important|notable|worth (?:a look|looking at))\b|"
                    r"\btoday'?s (?:top (?:\d{1,2} )?)?(?:findings|insights|priorities)\b")
BRIEFING_FILLER = (r"\b(?:what|whats|which|should|do|does|i|we|need|needs|to|my|our|me|us|the|a|an|is|are|anything|today|this|morning|week|"
                   r"right|now|first|please|give|show|tell|run|start|focus|look|at|on|in|data|across|population|network|panel|any|most|"
                   r"important|notable|worth|looking|stands?|out|know|about|worry|review|prioriti[sz]e|attention|pay|top|findings?|cards?|"
                   r"insights?|items?|things?|priorities|daily|weekly|where|for|of|and|with|ranked|ranking|briefing|s)\b|\d+")


# ---- explanation / strategy questions -> cited VBC knowledge base (vbc_knowledge.py) ---------------------------
# Routed only when the wording asks for an explanation AND names a VBC concept, program, model, company or framework AND
# carries no sign of a question about this population's data (our/we/how many/list/which patients/by practice/averages).
KNOWLEDGE_INTENT = (
    r"^(?:(?:can|could|would) you |please |so,? |ok,? |quick question:? )*(?:explain|describe|summari[sz]e|define|outline|walk me through|"
    r"tell me (?:about|how|what|why)|give (?:me )?(?:an? )?(?:overview|primer|summary|rundown)|help me understand|what do you know about)\b|"
    r"^(?:how|why) (?:does|do|did|is|are|was|were|would|should|can|could|will|has|have)\b|"
    r"^what(?:'s| is| are| was| were| does| do)\b|^who (?:is|are|was|were|owns|runs|founded)\b|^which (?:companies|firms|organi[sz]ations|models|programs)\b|"
    r"^(?:is|are|does|do|did) (?:there |the )?(?:evidence|research|studies|data show)\b|^(?:does|do|did) [a-z0-9' -]{2,60} (?:work|save money|reduce|improve)\b|"
    r"\b(?:difference|differences|distinction|compare|comparison|contrast)\b|\bvs\.?(?=\s)|\bversus\b|\bpros and cons\b|"
    r"\bwhat works\b|\bevidence (?:on|for|that|about)\b")
KNOWLEDGE_DOMAIN = (
    r"\bmssp\b|\bshared savings\b|\bacos?\b|\baccountable care\b|\breach\b|\bdirect contracting\b|\bnext ?gen(?:eration)?\b|\bpioneer\b|"
    r"\bmedicare advantage\b|\bma (?:plans?|enrollment|benchmarks?|payment|star)\b|\bpart [cd]\b|\bmedicaid (?:managed care|mcos?|vbp|value|expansion)\b|\bmanaged care\b|"
    r"\bcapitat\w*\b|\bglobal (?:risk|budget|cap)\w*\b|\bfull[- ]risk\b|\bpercent of premium\b|\btwo[- ]sided\b|\bupside[- ]only\b|\bdownside risk\b|\brisk corridors?\b|"
    r"\bstop[- ]loss\b|\bbenchmark(?:s|ing)?\b|\bratchet\b|\brebas\w*\b|\bminimum savings rate\b|\bmsr\b|\bsharing rate\b|\bquality withhold\b|"
    r"\battribut\w*\b|\bassignment\b|\bassigned\b|\balign(?:ed|ment)\b|"
    r"\bhccs?\b|\bcms[- ]hcc\b|\bv2[48]\b|\braf\b|\brisk adjust\w*\b|\brisk scores?\b|\bcoding intensity\b|\brecapture\b|\bradv\b|\bmeat criteria\b|\bnormali[sz]ation factor\b|"
    r"\bstars? (?:ratings?|program|bonus\w*)\b|\bhedis\b|\bncqa\b|\bcahps\b|\bapp (?:measures?|measure set)\b|\bquality (?:performance standard|measures?|bonus)\b|\becqms?\b|\bmips\b|\bqps?\b|\badvanced apms?\b|"
    r"\bcmmi\b|\binnovation center\b|\bcms innovation\b|\b2030 (?:goal|target)\b|\bcms models?\b|\bapms?\b|\balternative payment\b|\bhcp[- ]lan\b|\blan (?:framework|categor\w+)\b|"
    r"\bkidney care choices\b|\bprimary care first\b|\bcpc\+|\bmaking care primary\b|\bguide model\b|\bteam model\b|\bbpci\b|\bbundled payments?\b|\bcjr\b|\bahead model\b|\bmaryland (?:model|total cost)\b|"
    r"\benhancing oncology\b|\boncology care model\b|\bvbid\b|\blead model\b|"
    r"\bvalue[- ]based (?:care|payment|contracts?|arrangements?|programs?|models?)\b|\bvbc\b|\bfee[- ]for[- ]service\b|\bpmpm\b|\bmedical loss ratio\b|\bmlr\b|\bhealth equity benchmark\b|"
    r"\baledade\b|\bpearl health\b|\bwellvana\b|\bagilon\b|\bprivia\b|\boak street\b|\bchenmed\b|\bvillage ?md\b|\biora\b|\bone medical\b|\bevolent\b|\bsignify\b|"
    r"\boptum\b|\bcenterwell\b|\bhumana\b|\bcityblock\b|\blumeris\b|\bcaravan health\b|\benablement (?:compan\w+|models?|platforms?)\b|"
    r"\bporter\b|\bvalue agenda\b|\bintegrated practice units?\b|\bteisberg\b|\bnash\b|\bpopulation health\b|\btriple aim\b|\bquadruple aim\b|\bberwick\b|\btime[- ]driven activity\b|"
    r"\bhot[- ]?spott\w*\b|\bcamden\b|\bsuper[- ]?utili[sz]ers?\b|\bnaacos\b|\bmedpac\b|"
    r"\b(?:evidence|research|studies|trials?|rcts?|literature)\b.*\bcare (?:management|coordination)\b|\bcare (?:management|coordination)\b.*\b(?:evidence|research|studies|trials?|rcts?|literature)\b")
KNOWLEDGE_DATA = (
    r"\b(?:our|ours|my|we|we've|we're|us|your|yours|this population|these patients|this panel|the data|this data|the warehouse|received)\b|"
    r"\bhow many\b|\bcount\b|\bnumber of (?:patients|members|visits|claims|admissions)\b|\blist\b|\bshow(?: me)?\b|\bwhich (?:patients|members|practices|pcps|providers|clinics|physicians|doctors)\b|"
    r"\b(?:patients|members) (?:with|who|whose|that|in)\b|\btop \d+\b|\b(?:last|this|past|previous|next) (?:month|quarter|year|week)\b|\bby (?:practice|payer|pcp|provider|month|quarter|clinic|facility)\b|"
    r"\bper 1,?000\b|\b(?:average|mean|median)\b|\btrend(?:s|ing)?\b|\$\s?\d|\bdr\.? [a-z]|\bpatient [a-z0-9-]*\d|"
    r"\b(?:distribution|breakdown|total|highest|lowest)\b|\b(?:percentage|proportion) of (?:patients|members)\b|"
    r"\b(?:of|for|among) (?:the )?(?:attributed |assigned |aligned |enrolled )?(?:patients|members|population)\b|"
    # breakdowns and comparisons over this population's units are data questions, whatever the opening words
    r"\bfor each\b|\b(?:by|per) (?:[a-z]+ )?(?:type|types|agency|agencies|plans?|hospitals?|snfs?|county|counties|race|ethnicity|language|"
    r"specialty|specialties|categor(?:y|ies)|tins?|npis?|region|cohort)\b|\bwhich (?:agencies|hospitals|snfs|plans|specialists)\b|"
    r"\boutliers?\b|\bmismatch(?:es)?\b|\b(?:new|continuing) (?:and (?:new|continuing) )?(?:beneficiaries|members|patients)\b")


def _knowledge_question(question):
    low = ' '.join(str(question).lower().replace('’', "'").split())
    if not (re.search(KNOWLEDGE_INTENT, low) and re.search(KNOWLEDGE_DOMAIN, low)) or re.search(KNOWLEDGE_DATA, low):
        return None
    return _knowledge_graph(question)


def _contract_strategy_question(question, profile, as_of):
    """Risk-contract STRATEGY -> contract_strategy (options valuation, track selection, Nash negotiation). Strict: needs a
    strategy intent (should we switch / which track or offer is best / compare offers / negotiate / break even / what is the
    contract worth / how much downside can we take) AND contract wording. A single settlement ("what would we earn under
    MSSP ENHANCED with a $1,000 benchmark") never matches and stays vbc. Benchmark, offers and care-management spend come
    only from the question; when missing the answer names them (requires input) instead of assuming them."""
    low = ' '.join(str(question).lower().replace('’', "'").replace('−', '-').split())
    words = r"(?:tracks?|options?|offers?|contracts?|levels?|corridors?|arrangements?|deals?|proposals?)"
    intent = (r"\bshould (?:we|i|our (?:aco|group|practice|organi[sz]ation|network))\b (?:\w+ )?(?:move|switch|join|enter|take|accept|sign|choose|pick|go|stay|"
              r"stick|upgrade|elect|apply|renew|drop|leave|negotiate|ask|push|counter|step|graduate|transition|select|opt)\b|"
              rf"\bwhich (?:[\w-]+ ){{0,4}}{words}\b|\b(?:best|better|right|optimal|smart(?:er|est)|safer|safest|preferred) (?:[\w-]+ ){{0,2}}{words}\b|"
              r"\b(?:better|best|right|a good fit|optimal) (?:choice |fit |option |deal )?for (?:us|our)\b|"
              rf"\bcompare (?:[\w$%,./-]+ ){{0,8}}?{words}\b|\b(?:offers?|proposals?) (?:a|b|1|2|one|two)\b|"
              r"\bnegotiat\w*\b|\bbargain\w*\b|\bcounter[- ]?offer\b|\bbreak[- ]?even\b|"
              rf"\b(?:{words}|upside|downside|two[- ]sided(?: risk)?)\b[^.?;]{{0,40}}\bworth\b|\bworth\b[^.?;]{{0,25}}\b(?:{words}|upside|downside)\b|\boption value\b|"
              r"\bvalue of (?:the |our |this )?(?:upside|downside)\b|\bgreeks\b|"
              r"\bsensitiv\w+ (?:of|to) (?:our |the )?(?:payout|settlement|contract|shared savings|savings)\b|"
              r"\bhow much (?:downside|risk|loss(?:es)?)(?: risk)? (?:can|could|should|would) (?:we|our \w+) (?:take|bear|absorb|afford|tolerate|handle|accept)\b|\brisk capacity\b|\b(?:can|could) we (?:take|bear|absorb|afford|tolerate|handle)\b|"
              r"\b(?:move|switch|upgrade|step up|graduate|transition|go) (?:from|to) (?:mssp |the |aco )?(?:basic|enhanced|level [a-e]|reach|lead|two[- ]sided|downside)\b")
    context = (r"\bmssp\b|\bbasic\b|\benhanced\b|\breach\b|\blead (?:model|aco|global|professional)\b|\btracks?\b|\bcontracts?\b|\boffers?\b|"
               r"\bproposals?\b|\bsharing rate\b|\bshared[- ]savings\b|\bcorridors?\b|\bmsr\b|\bminimum savings rate\b|\bdownside\b|\bupside\b|"
               r"\btwo[- ]sided\b|\bone[- ]sided\b|\b(?:risk|commercial|payer|vbc|aco|value[- ]based) (?:contract|arrangement|deal)s?\b")
    versus = re.search(r"\bvs\.?(?=\s)|\bversus\b", low) and re.search(r"\b(?:we|us|our)\b|\$\s?\d|\bbenchmark\b", low) and \
        len(re.findall(r"\b(?:basic|enhanced|reach|lead|offer|level [a-e]|professional|global|upside[- ]only|two[- ]sided)\b", low)) >= 2
    ours = (r"\b(?:we|us|our|ours|we're|we've)\b|\$\s?\d|\d(?:\.\d+)? ?%|\bbenchmark\b|\b(?:offers?|proposals?) (?:a|b|1|2|one|two)\b|"
            r"\bthese (?:two |three )?(?:offers?|contracts?|proposals?|deals?|options|tracks)\b|\bthis (?:offer|contract|deal|proposal)\b")
    if not ((re.search(intent, low) or versus) and re.search(context, low) and re.search(ours, low)):
        return None  # no sign it is about OUR contracts: an explanation ("compare upside-only and two-sided arrangements") is knowledge
    info = {'compiler': VERSION, 'recognised': ['risk-contract strategy question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    money = lambda s, unit=None: float(s.replace(',', '')) * {'k': 1e3, 'thousand': 1e3, 'm': 1e6, 'million': 1e6}.get(unit or '', 1)
    pct = lambda pattern, text=low: (lambda m: float(next(g for g in m.groups() if g)) / 100 if m else None)(re.search(pattern, text))
    params = {}
    m = re.search(r"\bbenchmark (?:of |is |at |=|around |near )?\$\s?([\d,]+(?:\.\d+)?)|\$\s?([\d,]+(?:\.\d+)?) ?(?:pmpm |per member per month )?benchmark\b", low)
    if m: params['benchmark_pmpm'] = money(m.group(1) or m.group(2))
    m = re.search(r"\bcare[- ]management (?:spend(?:ing)?|costs?|budget|investment|program)?(?: of| is| at| =|=)?(?: us| our \w+)? ?\$\s?([\d,]+(?:\.\d+)?)|"
                  r"\$\s?([\d,]+(?:\.\d+)?) ?(?:pmpm |per member per month )?(?:of |on |for |in )?care[- ]management\b|"
                  r"\bspend(?:ing)? (?:of )?\$\s?([\d,]+(?:\.\d+)?) ?(?:pmpm|per member per month)", low)
    if m: params['care_management_pmpm'] = money(next(g for g in m.groups() if g))
    t = pct(r"([+-]?\d+(?:\.\d+)?) ?% (?:cost |medical |annual )?trend\b|\btrend (?:of |is |at )?([+-]?\d+(?:\.\d+)?) ?%")
    if t is not None: params['trend_pct'] = t
    m = re.search(r"\b(?:risk[- ]aversion|lambda|λ)(?: of| is| =|=| at)? ?(\d+(?:\.\d+)?)", low)
    if m: params['risk_aversion'] = float(m.group(1))
    c = pct(r"\b(\d{2}(?:\.\d+)?) ?% (?:confidence|level|var\b|cvar\b|value[- ]at[- ]risk)|\bconfidence (?:of |level (?:of )?)?(\d{2}(?:\.\d+)?) ?%")
    if c is not None: params['confidence'] = c
    m = re.search(r"\b(?:absorb|afford|tolerate|bear|handle|lose|losing|loss(?:es)? of|capacity (?:of|is)|risk capacity (?:of |is )?)"
                  r"(?: up to| at most| no more than| about)? \$\s?([\d,]+(?:\.\d+)?) ?(k|m|million|thousand)?\b", low)
    if m: params['risk_capacity'] = money(m.group(1), m.group(2))
    cp, rest = {}, low  # rest: the question without the counterparty's stated bounds (those are not our offer's terms)
    for key, pattern in (('sharing_rate_max', r"\b(?:payer|plan|insurer|they)\b[^.;]{0,40}?(?:max(?:imum)?(?: of)?|caps? (?:it )?at|won't go (?:above|over|past)|"
                                              r"will not go (?:above|over|past)|at most|up to|no more than|tops? out at|ceiling of) (\d+(?:\.\d+)?) ?% (?:shared savings |savings )?shar(?:ing|e)"),
                         ('sharing_rate_min', r"\b(?:payer|plan|insurer|they)\b[^.;]{0,40}?(?:offer(?:s|ed|ing)?|floor of|at least|minimum of) (\d+(?:\.\d+)?) ?% (?:shared savings |savings )?shar(?:ing|e)"),
                         ('msr_min', r"\b(?:payer|plan|insurer|they)\b[^.;]{0,40}?(?:insists? on|requires?|wants?|demands?|needs?)(?: at least| a| an)? (\d+(?:\.\d+)?) ?% (?:msr|minimum savings rate)")):
        m = re.search(pattern, rest)
        if m:
            cp[key] = float(m.group(1)) / 100
            rest = rest[:m.start()] + ' ; ' + rest[m.end():]
    m = re.search(r"\b(?:payer|plan|insurer)(?:'s)? (?:reservation|walk[- ]away)(?: value)?(?: of| is)? \$\s?([\d,]+(?:\.\d+)?) ?(k|m|million|thousand)?", low)
    if m: cp['reservation_value'] = money(m.group(1), m.group(2))

    def offer_terms(seg):
        t = {}
        for key, pattern in (('loss_cap_pct', r"\bloss cap (?:of |at )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:loss|downside) cap\b"),
                             ('savings_cap_pct', r"\b(?:savings |upside )?cap (?:of |at )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:savings |upside )?cap\b"),
                             ('loss_rate', r"\bloss(?: sharing)?(?: rate)? (?:of |at |is )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:shared )?loss(?:es)?(?: sharing| rate)?\b"),
                             ('mlr', r"\b(?:mlr|minimum loss rate) (?:of |at |is )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:mlr|minimum loss rate)\b"),
                             ('msr', r"\b(?:msr|minimum savings rate) (?:of |at |is )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:msr|minimum savings rate)\b"),
                             ('sharing_rate', r"\b(?:shared savings |savings )?shar(?:ing|e)(?: rate)? (?:of |at |is )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% (?:shared savings |savings )?shar(?:ing|e)\b"),
                             ('quality_score', r"\bquality score (?:of |is )?(\d+(?:\.\d+)?) ?%|(\d+(?:\.\d+)?) ?% quality score\b")):
            mm = re.search(pattern, seg)
            if mm:
                t[key] = float(next(g for g in mm.groups() if g)) / 100
                seg = seg[:mm.start()] + ' ' + seg[mm.end():]
        if re.search(r"\btwo[- ]sided\b|\bdownside\b|\bloss(?:es)? (?:sharing|rate)\b", seg) or 'loss_rate' in t: t['two_sided'] = True
        if re.search(r"\bupside[- ]only\b|\bone[- ]sided\b|\bno downside\b", seg): t['two_sided'] = False
        return t

    def named_programs(seg):
        out = []
        year = re.search(r"\b(?:py ?|performance year )?(20\d\d)\b", seg)
        year = int(year.group(1)) if year else None
        letters = re.findall(r"\bbasic(?: track)?(?: level)? ([a-e])\b(?!\s*(?:\$|\d))|\blevel ([a-e])\b", seg)
        letters = sorted({a or b for a, b in letters})
        if letters: out += [{'program': {'id': 'mssp_basic', 'variant': x.upper(), 'year': year}} for x in letters]
        elif re.search(r"\bbasic\b", seg): out.append({'program': {'id': 'mssp_basic', 'variant': None, 'year': year}})
        if re.search(r"\benhanced\b", seg): out.append({'program': {'id': 'mssp_enhanced', 'variant': None, 'year': year}})
        lead = re.search(r"\blead (?:model|aco|global|professional)\b|\blong[- ]term enhanced aco design\b", seg)
        if re.search(r"\breach\b", seg):
            pro, glo = re.search(r"\bprofessional\b", seg) and not lead, re.search(r"\bglobal\b", seg) and not lead
            for v in ([v for v, on in (('Professional', pro), ('Global', glo)) if on] if (pro or glo) and not re.search(r"\boptions\b|\bwhich\b", seg) else [None]):
                out.append({'program': {'id': 'aco_reach', 'variant': v, 'year': year}})
        if lead:
            v = 'Global' if re.search(r"\bglobal\b", seg) else 'Professional' if re.search(r"\bprofessional\b", seg) else None
            out.append({'program': {'id': 'lead', 'variant': v, 'year': year}})
        return out

    labels = list(re.finditer(r"\b(?:offer|proposal|deal|contract)\s+(?:#\s*)?([a-d1-4]|one|two|three)\b\s*[:(,-]?", rest))
    segments = [(m.group(0).strip(' :(,-').title(), rest[m.end():labels[i + 1].start() if i + 1 < len(labels) else len(rest)]) for i, m in enumerate(labels)]
    if len(segments) < 2:
        parts = re.split(r"\s(?:vs\.?|versus|compared (?:with|to))\s|;\s", rest)
        segments = [(None, s) for s in parts if offer_terms(s).get('sharing_rate') is not None]
        if len(segments) < 2: segments = []
    options = []
    for i, (label, seg) in enumerate(segments):
        t, progs = offer_terms(seg), named_programs(seg)
        if progs: options += [{**pr, **({'label': label} if label and len(progs) == 1 else {}), **({'terms': t} if t else {})} for pr in progs]
        elif t: options.append({'label': label or f'Offer {chr(65 + i)}', 'terms': t})
    whole = offer_terms(rest) if not options else {}
    if not options:
        options = named_programs(rest)
        numeric = {k: v for k, v in whole.items() if k != 'two_sided'}
        if len(options) == 1 and numeric: options[0]['terms'] = numeric  # stated terms override the named track's
        if options: whole = {}
    negotiate = re.search(r"\bnegotiat\w*\b|\bbargain\w*\b|\bcounter[- ]?offer\b|\bbreak[- ]?even\b|\bwhat (?:sharing rate|msr|share)\b", low)
    if negotiate:
        params['focus'] = 'negotiate'
        if re.search(r"\bbreak[- ]?even\b", low): params['target'] = 'break_even'
        if whole.get('sharing_rate') is not None: options = [{'label': 'Current offer', 'terms': whole}]
        elif whole.get('msr') is not None: cp.setdefault('msr_min', whole['msr']); cp.setdefault('msr_max', whole['msr'])  # MSR fixed as stated
    else:
        if not options and whole.get('sharing_rate') is not None: options = [{'label': 'Offer', 'terms': whole}]
        several = len(options) > 1 or any((o.get('program') or {}).get('id') in ('mssp_basic', 'aco_reach', 'lead')
                                          and (o.get('program') or {}).get('variant') is None for o in options)  # a family = all its options
        params['focus'] = ('capacity' if re.search(r"\bhow much (?:downside|risk|loss(?:es)?)\b|\brisk capacity\b|"
                                                  r"\b(?:can|could) we (?:take|bear|absorb|afford|tolerate|handle)\b|\bconfidence\b", low) else
                           'compare' if several else 'value')
    params['options'] = options
    if cp: params['counterparty'] = cp
    missing = []
    if 'benchmark_pmpm' not in params: missing.append('benchmark_pmpm (the contract-specific benchmark in $ PMPM; Agere never assumes one)')
    if params['focus'] != 'negotiate' and not options:
        missing.append('contract options: named tracks (MSSP BASIC A-E / ENHANCED, ACO REACH Professional / Global, LEAD) or offer terms (sharing rate, MSR, loss rate, caps)')
    if params.get('target') == 'break_even' and 'care_management_pmpm' not in params:
        missing.append('care_management_pmpm (expected care-management spend, $ PMPM)')
    if missing:
        info.update(route='local_unsupported', operation='contract_strategy')
        return {'interpretation': f'Needs inputs not in the data: {question}'[:2000], 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': [f'Missing input: {x}' for x in missing]}, info
    from .contract_strategy import validate
    try:
        validate(params)
    except ValueError as exc:
        if 'needs supplied' in str(exc):
            info.update(route='local_unsupported', operation='contract_strategy')
            return {'interpretation': f'Needs inputs not in the data: {question}'[:2000], 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': [f'Missing input: {exc}'[:1000]]}, info
        info.update(route='fallback', reason=f'contract_strategy parameters: {exc}')
        return None, info
    info['recognised'] += [f"{k}={params[k]}" for k in ('focus', 'benchmark_pmpm', 'care_management_pmpm', 'trend_pct', 'risk_aversion', 'confidence',
                                                        'risk_capacity') if k in params]
    names = ', '.join(o.get('label') or ' '.join(str(x) for x in (o['program']['id'], o['program'].get('variant') or 'all options') if x) for o in options)
    return {'interpretation': ' '.join(str(question).split())[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'contract_strategy', 'inputs': [], 'parameters': params,
                       'purpose': f"Contract strategy ({params['focus']}): {names or 'negotiated terms'}"[:1000]}]}, info


# ---- life-sciences / real-world evidence: target trials, lines of therapy, trial feasibility ----------------
RWE_MED_STREAMS = ('pharmacy_dispenses', 'pharmacy_claims', 'payer_pharmacy_claims')
RWE_DRUGS = {  # strategy -> regex over the question (class names, common abbreviations, ingredients)
    'sglt2_inhibitor': r'sglt-?2(?: inhibitors?|is?)?|gliflozins?|empagliflozin|dapagliflozin|canagliflozin|ertugliflozin',
    'glp1_agonist': r'glp-?1(?: (?:receptor )?agonists?| ras?)?|semaglutide|liraglutide|dulaglutide|exenatide|tirzepatide',
    'dpp4_inhibitor': r'dpp-?4(?: inhibitors?|is?)?|gliptins?|sitagliptin|linagliptin|saxagliptin|alogliptin',
    'sulfonylurea': r'sulfonylureas?|glipizide|glyburide|glimepiride', 'metformin': r'metformin', 'insulin': r'insulins?',
    'ace_inhibitor': r'ace[- ]?(?:inhibitors?|is?)\b|lisinopril|enalapril|ramipril', 'arb': r'arbs?|angiotensin receptor blockers?|losartan|valsartan|irbesartan',
    'arni': r'arnis?|entresto|sacubitril', 'mra': r'mras?|mineralocorticoid (?:receptor )?antagonists?|spironolactone|eplerenone|finerenone',
    'beta_blocker': r'beta[- ]?blockers?', 'calcium_channel_blocker': r'calcium channel blockers?|ccbs?|amlodipine',
    'thiazide_diuretic': r'thiazides?(?: diuretics?)?|chlorthalidone|hydrochlorothiazide', 'loop_diuretic': r'loop diuretics?|furosemide|torsemide',
    'statin': r'statins?|atorvastatin|rosuvastatin|simvastatin|pravastatin', 'anticoagulant': r'anticoagulants?|doacs?',
    'antiplatelet': r'antiplatelets?|clopidogrel|ticagrelor', 'ssri': r'ssris?|sertraline|fluoxetine|escitalopram|citalopram',
    'proton_pump_inhibitor': r'proton pump inhibitors?|ppis?|omeprazole|pantoprazole', 'opioid': r'opioids?',
}


def _target_trial_question(q, profile):
    """Drug A versus drug B comparative effectiveness -> target_trial (new-user active-comparator emulation). Needs two named
    strategies plus comparator wording and an outcome, or explicit target-trial wording (then missing pieces are asked for).
    A single drug or program start ('did starting X reduce Y') stays program_effect; time-to-event wording with no second
    strategy stays survival; 'what is target trial emulation' stays knowledge."""
    low = ' '.join(str(q).lower().replace('’', "'").split())
    explicit = re.search(r'\btarget[- ]trials?\b|\bemulat\w*\b|\bcomparative effectiveness\b|\bactive[- ]comparator\b|\bhead[- ]to[- ]head\b', low)
    if explicit and re.search(r"^(?:what|why|how|explain|describe)\b(?: is| are| does| do|'s)?\b", low) and not re.search(r'\b(?:our|we|us)\b', low):
        return None  # an explanation of the method itself is knowledge
    found = []
    intensity = re.search(r'\bstatin (?:dose )?intensit(?:y|ies)\b|\b(?:high|moderate|low|lower)[- ]intensity\b', low)
    if intensity and re.search(r'\bstatins?\b|\batorvastatin\b|\brosuvastatin\b', low):
        hi = re.search(r'\bhigh[- ]intensity\b', low)
        mod = re.search(r'\bmoderate[- ]intensity\b', low)
        lowi = re.search(r'\blow[- ]intensity\b', low)
        lower = re.search(r'\blower[- ]intensity\b|\blow[- ]to[- ]moderate\b|\bless intens\w*\b|\bnon[- ]high\b', low)
        if hi and (mod or lowi) and not lower and not (mod and lowi):
            second = 'moderate_intensity_statin' if mod else 'low_intensity_statin'
        else:
            second = 'lower_intensity_statin'
        found = [(0, 'moderate_intensity_statin'), (1, 'low_intensity_statin')] if not hi and mod and lowi else [(0, 'high_intensity_statin'), (1, second)]
    else:
        for arm, rx in RWE_DRUGS.items():
            m = re.search(r'(?<![a-z0-9-])(?:' + rx + r')(?![a-z0-9])', low)
            if m: found.append((m.start(), arm))
        found.sort()
        if 'statin' in [a for _, a in found] and len(found) > 2: found = [x for x in found if x[1] != 'statin']
    arms = [a for _, a in found][:2]
    comparator = re.search(r'\bvs\.?(?=\s)|\bversus\b|\bcompared (?:with|to)\b|\bcomparison\b|\bcompare[sd]?\b|\bthan\b|\brather than\b|\binstead of\b|'
                           r'\bstatin (?:dose )?intensit(?:y|ies)\b', low) or explicit or (re.search(r'\bor\b', low) and re.search(r'\b(?:better|worse|safer|more effective)\b', low))
    if (len(arms) < 2 and not explicit) or not arms: return None  # no named strategy at all: the router (or the user) must name them
    if len(arms) == 2 and not comparator: return None
    info = {'compiler': VERSION, 'recognised': ['target trial emulation question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    asks = []
    if len(arms) < 2:
        asks.append('two treatment strategies to compare: the treatment and its active comparator (drug classes such as SGLT2 inhibitor vs '
                    'DPP-4 inhibitor, or high- vs moderate-intensity statins); a single drug start with no comparator is a program-effect question')
    # outcome
    outcome, opos = None, None
    if re.search(DEATH_WORDS, low):
        info.update(route='local_unsupported', operation='target_trial')
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: death or mortality records (none are received)']}, info
    labs = {**MEASUREMENT_ALIASES, 'egfr': MEASUREMENT_ALIASES['egfr'] + ['kidney function', 'renal function'],
            'hba1c': MEASUREMENT_ALIASES['hba1c'] + ['glycemic control', 'glycaemic control'],
            'systolic_bp': MEASUREMENT_ALIASES['systolic_bp'] + ['blood pressure', 'bp control']}
    lab_hits = sorted((m.start(), c) for c, al in labs.items() for a in al for m in [re.search(_alias_pattern(a), low)] if m)
    event = None
    for kind, rx in (('admission', r'\b(?:re)?admi(?:ssions?|tted|ts?)\b|\bhospitali[sz](?:ations?|ed)\b|\binpatient (?:stays?|admissions?)\b'),
                     ('ed_visit', r'\b(?:ed|er|emergency(?: department| room)?) (?:visits?|use|utili[sz]ation)\b|\bemergency visits?\b')):
        m = re.search(rx, low)
        if m and (event is None or m.start() < event[0]): event = (m.start(), kind)
    incident = None
    for concept, aliases in CONDITION_ALIASES.items():
        for a in aliases:
            m = re.search(r'\b(?:incident|new(?:ly diagnosed)?|new[- ]onset|develop(?:ing|s|ed)?|onset of)\s+' + re.escape(a) + r'\b', low)
            if m: incident = (m.start(), concept)
    if event:
        outcome, opos = {'type': event[1]}, event[0]
    elif incident:
        outcome, opos = {'type': 'condition', 'concept': incident[1]}, incident[0]
    elif lab_hits:
        opos, lab = lab_hits[0]
        outcome = {'type': 'lab_change', 'lab': lab}
        pct = re.search(r'\b(\d{2})\s*%\s*(?:decline|drop|reduction|fall|decrease)\b', low)
        thr = re.search(r'\b(?:fall(?:ing|s)? below|drop(?:ping|s)? below|reach(?:ing|es)?|below|under|above|over|less than|greater than)\s+(\d+(?:\.\d+)?)\b', low[opos:])
        if pct:
            outcome = {'type': 'lab_decline_pct', 'lab': lab, 'pct': int(pct.group(1)) / 100}
        elif thr and re.search(r'\btime to\b|\brisk of\b|\breach|\bfall|\bdrop|\bprogress', low):
            word = thr.group(0).split()[0]
            outcome = {'type': 'lab_threshold', 'lab': lab, 'op': 'lt' if word in ('fall', 'falling', 'falls', 'drop', 'dropping', 'drops', 'below', 'under', 'less') else 'gt',
                       'value': float(thr.group(1))}
    if outcome is None:
        asks.append('an outcome: a lab change (eGFR, A1c, LDL, systolic blood pressure) or an event (admissions, ED visits, an incident condition)')
    effect = re.search(r'\beffect|\bimpact|\breduc\w*|\blower\w*|\bslow\w*|\bimprov\w*|\bbetter\b|\bworse\b|\bbenefit\w*|\bprotect\w*|\bassociated\b|'
                       r'\bless likely\b|\bmore likely\b|\bfewer\b|\bmore\b|\bgreater\b|\brisk\b|\bdeclin\w*|\boutcomes?\b|\bwork\w*\b|\bdifference\b|'
                       r'\bcontrol\b|\bstatin (?:dose )?intensit', low) or explicit
    if outcome and not effect and not re.search(r'\b(?:on|for|in terms of)\s+(?:\w+\s+){0,2}$', low[:opos]): return None  # a plain comparison of averages
    if not explicit and outcome is None: return None  # 'how many patients are on an ACE inhibitor vs an ARB' is a literal count
    if asks:
        info.update(route='local_requires_input', operation='target_trial', requires_input=asks)
        return {'interpretation': f'Needs inputs not in the data: {q}'[:2000], 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': [f'Missing input: {m}' for m in asks]}, info
    streams = (profile or {}).get('streams', {})
    if profile is not None and not any(s in streams for s in RWE_MED_STREAMS):
        info.update(route='local_unsupported', operation='target_trial')
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: received dispense or pharmacy claim records (treatment initiation is identified from fills)']}, info
    params = {'arms': arms, 'outcome': outcome}
    horizon = re.search(r'\b(?:within|over|at|after|in)\s+(?:the first\s+)?(\d{1,3}|one|two|three)\s+(day|month|year)s?\b', low)
    if horizon:
        n = {'one': 1, 'two': 2, 'three': 3}.get(horizon.group(1)) or int(horizon.group(1))
        days = n * {'day': 1, 'month': 30, 'year': 365}[horizon.group(2)]
        if 30 <= days <= 1825: outcome['horizon_days'] = days
    conds = []
    for concept, aliases in CONDITION_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            if re.search(r'\b(?:among|in|with|for|having)\s+(?:our\s+|the\s+|new(?:ly diagnosed)?\s+)?(?:patients\s+with\s+|adults\s+with\s+)?' + re.escape(a) + r'\b', low) \
                    or re.search(r'\b' + re.escape(a) + r'\s+(?:patients|adults|members)\b', low):
                if incident and incident[1] == concept: continue
                conds.append(concept); break
    if 'type_2_diabetes' in conds and 'diabetes' in conds: conds.remove('diabetes')
    if conds: params['population_conditions'] = conds[:6]
    if re.search(r'\bmatch(?:ed|ing)\b', low): params['adjustment'] = 'matching'
    elif re.search(r'\biptw\b|\binverse probability\b', low): params['adjustment'] = 'iptw'
    from .target_trial import validate
    try:
        validate(params)
    except ValueError as exc:
        info.update(route='fallback', reason=f'target_trial parameters: {exc}')
        return None, info
    info['recognised'] += [f"arms {arms[0]} vs {arms[1]}", f"outcome {outcome['type']}" + (f" {outcome.get('lab') or outcome.get('concept') or ''}".rstrip())]
    if conds: info['recognised'].append('population ' + ', '.join(conds))
    return {'interpretation': ' '.join(str(q).split())[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'target_trial', 'inputs': [], 'parameters': params,
                       'purpose': f"Target trial emulation: initiating {arms[0]} vs {arms[1]} (new users, active comparator)"[:1000]}]}, info


def _line_of_therapy_question(q, profile):
    """Treatment patterns -> line_of_therapy: lines of therapy, add-on vs switch, sequences, switching, persistence from fills.
    Needs pattern wording AND a therapy area or named drug classes. 'Time to X after Y' with no regimen wording stays survival;
    care-setting journeys stay pathway; 'did starting X reduce Y' stays program_effect."""
    low = ' '.join(str(q).lower().replace('’', "'").split())
    strong = (r'\blines? of (?:therapy|treatment)\b|\b(?:first|second|third|fourth|1st|2nd|3rd|later|subsequent)[- ,]+(?:(?:second|third|and)[- ,]+)*line\b|'
              r'\b(?:treatment|therapy|drug|medication|prescribing) (?:patterns?|sequenc\w*|pathways?|lines?|switch\w*|regimens?|escalation|intensification)\b|'
              r'\bregimens?\b|\bswitch(?:es|ed|ing)?\b[^.?]{0,60}\b(?:to|from|between|class)\b|\badd[- ]on\b|\bsankey\b|\bsequences?\b')
    weak = r'\bpersisten(?:ce|t)\b|\bdiscontinu\w+\b|\btime on (?:treatment|therapy)\b|\bstay on\b|\bstep(?:ped)?[- ]up\b|\bintensif\w+\b'
    if not re.search(strong, low): return None  # persistence / discontinuation of one drug start with no regimen wording is survival
    from .line_of_therapy import AREAS
    areas = [('rheumatoid_arthritis', r'\brheumatoid arthritis\b|\b(?:ra)\b(?= (?:patients|biologics?|therapy|treatment))|\bbiologic (?:dmards?|therap\w+)\b|\bdmards?\b|\bbiologics?\b'),
             ('heart_failure', r'\bheart failure\b|\bhfref\b|\bchf\b|\bgdmt\b|\bguideline[- ]directed medical therapy\b'),
             ('chronic_kidney_disease', r'\bchronic kidney disease\b|\bckd\b'),
             ('depression', r'\bdepression\b|\bdepressive\b|\bantidepressants?\b|\bmdd\b'),
             ('hypertension', r'\bhypertension\b|\bhtn\b|\bhigh blood pressure\b|\bantihypertensives?\b'),
             ('diabetes', r'\bdiabet\w*\b|\bt2dm\b|\bglucose[- ]lowering\b|\bantidiabetic\b')]
    area = next((a for a, rx in areas if re.search(rx, low)), None)
    named = [arm for arm, rx in RWE_DRUGS.items() if re.search(r'(?<![a-z0-9-])(?:' + rx + r')(?![a-z0-9])', low)]
    info = {'compiler': VERSION, 'recognised': ['line-of-therapy / treatment-pattern question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    if area is None and not named:
        if re.search(r'\bcancer\b|\boncolog\w*\b|\bchemo\w*\b|\btumou?r\b|\bmetastatic\b|\bimmunotherap\w*\b', low):
            info.update(route='local_unsupported', operation='line_of_therapy')
            return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': ['Missing input: antineoplastic regimens / administrations and oncology detail (stage, histology) are not received']}, info
        return None
    streams = (profile or {}).get('streams', {})
    if profile is not None and not any(s in streams for s in RWE_MED_STREAMS):
        info.update(route='local_unsupported', operation='line_of_therapy')
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: received dispense or pharmacy claim records (lines of therapy are derived from fills)']}, info
    params = {}
    if area: params['area'] = area
    comps = [c for c in named if not area or c in AREAS[area]['components']]
    if not area: params['components'] = comps
    elif len(comps) >= 2 and re.search(r'\bonly\b|\bbetween\b', low): params['components'] = comps
    params['focus'] = ('switching' if re.search(r'\bswitch\w*\b', low) else 'persistence' if re.search(weak, low)
                       else 'sequences' if re.search(r'\bsequenc\w*\b|\bsankey\b|\bpathways?\b', low) else 'lines' if re.search(r'\blines?\b', low) else 'all')
    gap = re.search(r'\b(\d{1,3})[- ]day (?:gap|grace)\b|\bgap (?:of |tolerance (?:of )?)?(\d{1,3}) days\b', low)
    if gap: params['gap_days'] = int(gap.group(1) or gap.group(2))
    if re.search(r'\badd[- ]ons? (?:stay|are|count as) (?:in )?the same line\b', low): params['add_on_new_line'] = False
    if re.search(r'\b(?:prevalent|existing|all) users\b|\bincluding prevalent\b', low): params['require_new_user'] = False
    from .line_of_therapy import validate
    try:
        validate(params)
    except ValueError as exc:
        info.update(route='fallback', reason=f'line_of_therapy parameters: {exc}')
        return None, info
    info['recognised'] += [f'area {area}' if area else 'components ' + ', '.join(comps), f"focus {params['focus']}"]
    return {'interpretation': ' '.join(str(q).split())[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'line_of_therapy', 'inputs': [], 'parameters': params,
                       'purpose': f"Lines of therapy ({area or ', '.join(comps)}): regimens, switches, persistence from fills"[:1000]}]}, info


def _trial_feasibility_question(q, profile, as_of):
    """Clinical-trial feasibility -> trial_feasibility: eligibility funnel over criteria STATED in the question (conditions, labs
    with thresholds and recency, medications, age, sex), sites, diversity, expected enrolment. Needs trial/study wording AND an
    eligibility / feasibility / enrolment word; a plain count without trial framing stays a literal lookup. Criteria the data cannot
    compute (EF, ECOG, stage, mutations) become 'not applied' criteria; no stated criteria -> requires input."""
    low = ' '.join(str(q).lower().replace('’', "'").replace('≥', '>=').replace('≤', '<=').split())
    if re.search(r'\btarget[- ]trials?\b|\bemulat\w*\b|\brandomi[sz]ed\b', low): return None
    if not (re.search(r'\btrials?\b|\bstud(?:y|ies)\b|\bprotocol\b', low) and
            re.search(r'\beligib\w*|\binclusion\b|\bexclusion\b|\bcriteri(?:a|on)\b|\bfeasib\w*|\benrol\w*|\brecruit\w*|\bscreen(?:ed|ing)? for\b|'
                      r'\bfunnel\b|\bdiversity\b|\bsites?\b|\binvestigators?\b|\bqualify\b|\bcandidates?\b|\battrition\b', low)):
        return None
    info = {'compiler': VERSION, 'recognised': ['trial feasibility question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    crit, taken = [], []

    def free(a, b): return all(b <= x or a >= y for x, y in taken)

    def negated(start):
        return bool(re.search(r'\b(?:no|not|without|never|excluding|exclude|exclusions?:?|free of|no history of|not on|not taking)\b(?:\s+\S+){0,3}\s*$',
                              low[max(0, start - 40):start]))

    def recency(end):
        m = re.match(r'[^,;()]{0,30}?\b(?:in|within|over) the (?:last|past|prior|previous) (\d{1,3}) (day|week|month|year)s?\b|[^,;()]{0,30}?\bwithin (\d{1,3}) (day|week|month|year)s?\b', low[end:])
        if not m: return None
        n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return min(3650, int(n) * {'day': 1, 'week': 7, 'month': 30, 'year': 365}[unit])
    # criteria the warehouse cannot compute (structured EF, performance status, staging, genomics, NYHA, pregnancy)
    for rx in (r'\b(?:lv)?ef\b[^,;()]*|\bejection fraction\b[^,;()]*', r'\becog\b[^,;()]*', r'\bstage (?:iv|iii|ii|i|[1-4])\b[^,;()]*',
               r'\b\w+ mutation\b|\bbiomarker\w*\b[^,;()]*', r'\bnyha\b[^,;()]*', r'\bpregnan\w*\b', r'\bbmi\b[^,;()]*'):
        for m in re.finditer(rx, low):
            if free(m.start(), m.end()):
                taken.append((m.start(), m.end()))
                crit.append({'type': 'unevaluated', 'kind': 'exclusion' if negated(m.start()) else 'inclusion', 'text': m.group(0).strip()[:200]})
    # age and sex
    m = re.search(r'\b(?:aged?|ages?)\s*(\d{1,3})\s*(?:-|–|to|and)\s*(\d{1,3})\b|\b(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\s*(?:years?|y/?o|yrs?)\b', low)
    if m:
        lo_, hi_ = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        crit.append({'type': 'age', 'min': int(lo_), 'max': int(hi_)}); taken.append((m.start(), m.end()))
    else:
        m = re.search(r'\b(?:aged?\s*)(\d{1,3})\s*(?:\+|or older|and older|years? (?:and|or) older)|\b(\d{1,3})\s*(?:\+ years|years? (?:and|or) older|years? or over)\b|'
                      r'\b(?:aged?|ages?) (?:over|above|older than) (\d{1,3})\b|\b(?:over|older than|above) (?:the )?age (?:of )?(\d{1,3})\b|'
                      r'\b(?:over|older than|above) (\d{1,3}) ?(?:years|yrs|y/?o)\b', low)
        if m and free(m.start(), m.end()):
            crit.append({'type': 'age', 'min': int(m.group(1) or m.group(2)) if (m.group(1) or m.group(2)) else int(m.group(3) or m.group(4) or m.group(5)) + 1})
            taken.append((m.start(), m.end()))
        elif re.search(r'\badults?\b', low): crit.append({'type': 'age', 'min': 18})
    if re.search(r'\bwomen\b|\bfemales?\b', low) and not re.search(r'\bmen\b|\bmales?\b', low): crit.append({'type': 'sex', 'value': 'F'})
    elif re.search(r'\bmen\b|\bmales?\b', low) and not re.search(r'\bwomen\b|\bfemales?\b', low): crit.append({'type': 'sex', 'value': 'M'})
    # labs with thresholds
    elevated = {'nt_probnp': ('gt', 125.0), 'uacr': ('gt', 30.0), 'hba1c': ('ge', 6.5), 'ldl': ('ge', 160.0), 'tsh': ('gt', 4.5), 'systolic_bp': ('ge', 140.0)}
    num = r'(\d+(?:\.\d+)?)\s*(?:%|mg/dl|ml/min(?:/1\.73 ?m2)?|pg/ml|mmhg|mg/g)?'
    lab_aliases = sorted(((a, c) for c, al in MEASUREMENT_ALIASES.items() for a in al), key=lambda x: -len(x[0]))
    for alias, concept in lab_aliases:
        for m in re.finditer(_alias_pattern(alias), low):
            if not free(m.start(), m.end()): continue
            tail = low[m.end():m.end() + 60]
            c = {'type': 'lab', 'concept': concept, 'kind': 'exclusion' if negated(m.start()) else 'inclusion'}
            spec = (re.match(r'\s*(?:of |is |level )?(?:between|from)\s+' + num + r'\s*(?:and|to|-)\s*' + num, tail) or
                    re.match(r'\s*(?:of |is )?' + num + r'\s*(?:-|–|to)\s*' + num, tail))
            if spec: c.update(low=float(spec.group(1)), high=float(spec.group(2)))
            else:
                spec = re.match(r'\s*(?:of |is |level |value )?(above|over|greater than|more than|higher than|>=|>|at least|exceeding|under|below|less than|lower than|<=|<|at most)\s+' + num, tail)
                if spec:
                    w = spec.group(1)
                    c.update(op={'>=': 'ge', 'at least': 'ge', '<=': 'le', 'at most': 'le'}.get(w, 'gt' if w in ('above', 'over', 'greater than', 'more than', 'higher than', '>', 'exceeding') else 'lt'),
                             value=float(spec.group(2)))
                else:
                    spec = re.match(r'\s*(?:of |is )?' + num + r'\s*(?:or|and) (more|higher|above|greater|over|less|lower|below|under)\b', tail)
                    if spec: c.update(op='ge' if spec.group(2) in ('more', 'higher', 'above', 'greater', 'over') else 'le', value=float(spec.group(1)))
                    elif re.search(r'\belevated\b|\bhigh\b|\braised\b', low[max(0, m.start() - 12):m.end() + 12]) and concept in elevated:
                        c.update(op=elevated[concept][0], value=elevated[concept][1], label=f'elevated {concept} ({OPERATOR_SIGNS[elevated[concept][0]]} '
                                                                                        f'{elevated[concept][1]:g}, Agere default threshold; state a value to override)')
                    else: continue
            end = m.end() + (spec.end() if spec else 0)
            r = recency(end)
            if r: c['recency_days'] = r
            crit.append(c); taken.append((m.start(), end))
    # medications ('on stable GDMT', 'not on an SGLT2 inhibitor')
    for m in re.finditer(r'\b(?:stable )?(?:gdmt|guideline[- ]directed medical therapy)\b', low):
        crit.append({'type': 'medication', 'classes': ['ace_inhibitor', 'arb', 'arni', 'hf_evidence_based_beta_blocker', 'mra', 'sglt2_inhibitor'],
                     'kind': 'exclusion' if negated(m.start()) else 'inclusion', 'min_fills': 2 if 'stable' in low[max(0, m.start() - 8):m.end()] else 1})
        taken.append((m.start(), m.end()))
    for arm, rx in RWE_DRUGS.items():
        m = re.search(r'(?<![a-z0-9-])(?:' + rx + r')(?![a-z0-9])', low)
        if m and free(m.start(), m.end()):
            c = {'type': 'medication', 'classes': [arm], 'kind': 'exclusion' if negated(m.start()) else 'inclusion',
                 'min_fills': 2 if re.search(r'\bstable\b', low[max(0, m.start() - 20):m.start()]) else 1}
            r = recency(m.end())
            if r: c['recency_days'] = r
            crit.append(c); taken.append((m.start(), m.end()))
    for concept, alias in sorted(((c, a) for c, al in ENCOUNTER_ALIASES.items() for a in al), key=lambda x: -len(x[1])):
        for m in re.finditer(_alias_pattern(alias), low):
            if not free(m.start(), m.end()): continue
            c = {'type': 'encounter', 'concept': concept, 'kind': 'exclusion' if negated(m.start()) else 'inclusion'}
            r = recency(m.end())
            if r: c['recency_days'] = r
            crit.append(c); taken.append((m.start(), m.end()))
    for m in re.finditer(r'\ban? (?:ed|er|emergency(?: department| room)?) visit\b|\ban? (?:hospital )?admission\b', low):
        if free(m.start(), m.end()):
            crit.append({'type': 'encounter', 'concept': 'emergency_visit' if 'admission' not in m.group(0) else 'inpatient_admission',
                         'kind': 'exclusion' if negated(m.start()) else 'inclusion'})
            taken.append((m.start(), m.end()))
    # conditions (longest aliases first; 'newly diagnosed' = documented in the last year)
    for concept, alias in sorted(((c, a) for c, al in CONDITION_ALIASES.items() for a in al), key=lambda x: -len(x[1])):
        for m in re.finditer(_alias_pattern(alias), low):
            if not free(m.start(), m.end()): continue
            if concept in ('diabetes',) and any(x['type'] == 'condition' and x['concept'] in ('type_2_diabetes', 'type_1_diabetes') for x in crit): continue
            c = {'type': 'condition', 'concept': concept, 'kind': 'exclusion' if negated(m.start()) else 'inclusion'}
            if re.search(r'\b(?:new(?:ly)?(?: diagnosed)?|recently diagnosed|incident)\s+$', low[max(0, m.start() - 22):m.start()]): c['recency_days'] = 365
            crit.append(c); taken.append((m.start(), m.end()))
    if re.search(r'\bckd stage (?:3|4|3-4|iii|iv)\b|\bstage (?:3|4|3-4) ckd\b', low):
        crit = [c for c in crit if not (c['type'] == 'unevaluated' and c['text'].startswith('stage'))]
    computable = [c for c in crit if c['type'] not in ('unevaluated', 'age', 'sex')]
    if not computable:
        info.update(route='local_requires_input', operation='trial_feasibility',
                    requires_input=['trial inclusion/exclusion criteria'])
        return {'interpretation': f'Needs inputs not in the data: {q}'[:2000], 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: trial inclusion/exclusion criteria (no structured trial protocol is received): state the '
                                       'conditions, labs with thresholds and recency, medications and age range'] +
                                      [f"Missing input: {c['text']} (not computable from received data)" for c in crit if c['type'] == 'unevaluated']}, info
    params = {'criteria': crit}
    t = re.search(r'\b(?:our|the|a|an)\s+([a-z0-9][a-z0-9 /-]{0,30}?)\s+(?:trial|study)\b', low)
    if t: params['trial'] = (t.group(1) + ' trial').strip()
    if re.search(RACE_WORDS, low) or re.search(r'\bdiversity\b', low): params['group_by'] = 'race_ethnicity'
    elif re.search(LANGUAGE_WORDS, low): params['group_by'] = 'language'
    elif re.search(r'\bsites?\b|\binvestigators?\b|\bpractices?\b|\bclinics?\b', low): params['group_by'] = 'practice' if not re.search(r'\binvestigators?\b', low) else 'pcp'
    cr = re.search(r'\b(\d{1,2}(?:\.\d+)?)\s*% (?:consent|enrol\w*|enroll\w*|screen[- ]pass|conversion)', low)
    if cr: params['consent_rate'] = float(cr.group(1)) / 100
    from .trial_feasibility import validate
    try:
        validate(params)
    except ValueError as exc:
        info.update(route='fallback', reason=f'trial_feasibility parameters: {exc}')
        return None, info
    info['recognised'] += [f"{len(crit)} criteria ({len([c for c in crit if c['type'] == 'unevaluated'])} not computable)"]
    return {'interpretation': ' '.join(str(q).split())[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'trial_feasibility', 'inputs': [], 'parameters': params,
                       'purpose': f"Trial feasibility funnel over {len(crit)} stated criteria"[:1000]}]}, info


OPERATOR_SIGNS = {'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}


def _briefing_question(q):
    """'What should I look at today?' / 'morning briefing' -> the ranked proactive briefing. Strict: a question that names
    anything the briefing does not ('what should I know about Mrs Smith's A1c') is left to the other routes."""
    low = q.lower()
    if not re.search(BRIEFING_TRIGGER, low): return None
    rest = re.sub(BRIEFING_FILLER, ' ', re.sub(BRIEFING_TRIGGER, ' ', low))
    if re.search(r'[a-z]', rest): return None
    params = {}
    m = re.search(r'\btop (\d{1,2})\b|\b(\d{1,2}) (?:findings?|cards?|insights?|items?|things?)\b', low)
    if m: params['top'] = max(1, min(25, int(m.group(1) or m.group(2))))
    from .briefing_rank import validate
    params = validate(params)
    info = {'compiler': VERSION, 'recognised': ['briefing question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'briefing', 'inputs': [], 'parameters': params,
                       'purpose': f"Ranked proactive briefing (top {params['top']}): learned ranking with the reason for each position"}]}, info


def _calibration_question(q):
    """'How well calibrated are our risk scores / forecasts?' -> calibration (time-split conformal backtest)."""
    low = q.lower()
    if not re.search(CALIBRATION_INTENT, low): return None
    # asking FOR a calibrated/validated prediction is a prediction request (left to the refusals), not an audit of ours
    if re.search(r'\b(?:calibrated|validated) (?:probabilit|risk|estimate|prediction|forecast)|\bvalidated risk model\b', low): return None
    info = {'compiler': VERSION, 'recognised': ['calibration question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    from .calibration import FORECASTS
    params = {}
    concept = next((c for c, aliases in sorted(MEASUREMENT_ALIASES.items(), key=lambda kv: -max(map(len, kv[1])))
                    if c in {f[0] for f in FORECASTS} and any(re.search(_alias_pattern(a), low) for a in aliases)), None)
    if concept:
        _, threshold, direction = next(f for f in FORECASTS if f[0] == concept)
        params.update(target='forecasts', concept=concept, threshold=threshold, direction=direction)
    elif re.search(r'\b(?:forecasts?|projections?|trajector\w*|labs?|vitals?)\b', low): params['target'] = 'forecasts'
    elif re.search(r'\brisk (?:scores?|models?)\b', low): params['target'] = 'risk_scores'
    elif re.search(r'\boutreach\b|\butilization\b', low): params['target'] = 'outreach_utilization'
    info['recognised'].append('target ' + params.get('target', 'all') + (f' ({concept})' if concept else ''))
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'calibration', 'inputs': [], 'parameters': params,
                       'purpose': 'Time-split conformal backtest of Agere predictions: coverage, Brier, ECE, reliability'}]}, info


# ---- scheduling: no-show prediction, overbooking, reminders, access ------------------------------
NO_SHOW = r'no[- ]?shows?'
SCHEDULING_INTENTS = [
    ('overbooking', r'\bover[- ]?book\w*|\bdouble[- ]?book\w*|\byield management\b|\b(?:extra|additional) (?:slots?|appointments?|bookings?)\b'),
    ('reminders', r'\breminders?\b[^.?]{0,40}\b' + NO_SHOW + r'|\bwho should (?:get|receive|we (?:call|text|remind))\b[^.?]{0,30}\breminders?\b'
                  r'|\breminder (?:calls?|texts?|messages?|outreach)\b'),
    ('no_show_risk', r'\b(?:likely|predicted|expected|probable|projected|at risk|risk of|going)\b[^.?]{0,25}\b' + NO_SHOW +
                     r'|\b' + NO_SHOW + r' (?:risk|probabilit\w*|model|predictions?|predictors?|scores?|forecasts?)\b'
                     r'|\b(?:likely|going|expected) to (?:not show|miss (?:their|the|an|her|his) (?:appointment|visit))\b|\bpredict\w*\b[^.?]{0,30}\b' + NO_SHOW),
    ('access', r'\bthird[- ]next[- ]available\b|\bdays to (?:the )?third\b|\b(?:appointment|clinic|scheduling|booking) (?:access|lead times?)\b'
               r"|\bwhat'?s our access\b|\bhow (?:good|long) is (?:our )?access\b"),
]


def _scheduling_question(q):
    """'How much should each clinic overbook?' / 'which appointments are likely no-shows next week?' -> scheduling.
    A historical no-show RATE ('what was our no-show rate in 2027') carries none of these intents and stays an ops metric."""
    low = q.lower()
    hits = [f for f, rx in SCHEDULING_INTENTS if re.search(rx, low)]
    if not hits: return None
    if re.search(r'\bconcentrat\w*\b|\bhot ?spots?\b|\bunusually (?:high|low|common|long)\b|\bunexpectedly\b', low):
        return None  # "where are long lead times unexpectedly concentrated" asks for hotspots (discover), not a schedule decision
    if re.match(r'(?:why (?:is|are|do|does)|what (?:is|are)|how (?:is|are|does|do)|explain)\b', low) and not re.search(OUR_NUMBER, low):
        return None  # an explanation of an access measure ("why is third next available preferred?") is knowledge, not a schedule
    params = {'focus': 'reminders' if set(hits) == {'no_show_risk', 'reminders'} else hits[0] if len(hits) == 1 else 'all'}
    m = re.search(r'\b(?:practice|clinic|site)\s+(?:#\s*)?(\d{1,3})\b|\b(wh-[a-z]+-\d+)\b', low)
    if m: params['practice'] = (m.group(1) or m.group(2)).upper()
    if re.search(r'\btomorrow\b', low): params['horizon_days'] = 1
    elif re.search(r'\b(?:next|this|coming) week\b', low): params['horizon_days'] = 7
    elif re.search(r'\b(?:next|coming) (?:two|2) weeks\b', low): params['horizon_days'] = 14
    elif re.search(r'\b(?:next|this|coming) month\b', low): params['horizon_days'] = 30
    else:
        d = re.search(r'\b(?:next|coming|within(?: the next)?) (\d{1,3}) days?\b', low)
        if d and 1 <= int(d.group(1)) <= 120: params['horizon_days'] = int(d.group(1))
    if re.search(r'\bsafe(?:ly)?\b|\bwithout (?:running over|overflow\w*)\b', low): params['max_overflow_probability'] = 0.1
    c = re.search(r'\b(\d{1,3})[- ](?:slots?|appointments?|patients?) (?:per|a|each|in (?:a|each)) (?:session|day|clinic|half[- ]day)\b', low)
    if c and 1 <= int(c.group(1)) <= 200: params['capacity'] = int(c.group(1))
    e = re.search(r'\breminders? (?:cut|reduce|prevent|avert)s? (?:no[- ]?shows? )?(?:by )?(\d{1,3})\s?%', low)
    if e and int(e.group(1)) <= 100: params['reminder_effect'] = int(e.group(1)) / 100
    k = re.search(r'\b(\d{1,4}) (?:reminder )?(?:calls?|reminders?)\b', low)
    if k: params['reminder_capacity'] = int(k.group(1))
    o = re.search(r'\b(?:overflow|overtime|over[- ]capacity) (?:costs?|penalty) (?:of |is |= )?\$?(\d+(?:\.\d+)?)', low)
    if o: params['overflow_cost'] = float(o.group(1))
    v = re.search(r'\b(?:visit|served visit|kept visit) (?:is )?worth \$?(\d+(?:\.\d+)?)|\bvalue (?:per|of a) (?:visit|appointment) (?:of |is )?\$?(\d+(?:\.\d+)?)', low)
    if v: params['value_per_visit'] = float(v.group(1) or v.group(2))
    info = {'compiler': VERSION, 'recognised': ['scheduling question', 'focus ' + params['focus']], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'scheduling', 'inputs': [], 'parameters': params,
                       'purpose': 'No-show model, session overbooking, reminder targeting and access from the appointment table'}]}, info


def _program_effect_question(q, profile):
    """'Did X reduce Y?' / 'impact of X on Y' -> program_effect when X is a program or medication start identified by received
    records and Y a recognised outcome. Declines (unsupported, naming the missing input) otherwise; returns None when the
    question is not effect-shaped or demands an identified causal effect (left to the general refusal)."""
    low = q.lower()
    if not re.search(EFFECT_QUESTION, low) or re.search(IDENTIFIED_CAUSAL, low): return None
    info = {'compiler': VERSION, 'recognised': ['program effect question'], 'paraphrased': []}
    if re.search(RANDOMISED, low):
        return _effect_unsupported(q, ['randomised assignment records (none are received; program receipt is observational, so a trial '
                                       'result cannot be reproduced or claimed)'], info)
    program = next(((name, stream) for name, rx, stream in EFFECT_PROGRAMS if re.search(rx, low)), None)
    drug = None
    if program is None:
        for cls, rx in sorted(DRUG_ALIASES.items(), key=lambda kv: -len(kv[1])):
            if re.search(r'\b(?:' + rx + r')\b', low): drug = cls; break
        if drug: program = ('medication_start', None)
    if program is None and not re.search(INTERVENTION_WORDS, low):
        # 'do they have lower AWV completion', 'which affects assignment', 'a 1% change in trend': comparisons or
        # sensitivities, not a program-effect question; the other routes (or the router) decide
        return None
    if program is None:
        return _effect_unsupported(q, ['a named program or exposure recorded in received data (care management contacts, transition-of-care '
                                       'contacts, community service outcomes, care coordination contacts, or a medication start)',
                                       'an identifiable assignment (first exposure) date per patient'], info)
    name, stream = program
    streams = profile.get('streams', {})
    if stream and stream not in streams:
        return _effect_unsupported(q, [f'received {stream} records ({name.replace("_", " ")} exposure is not in this warehouse)'], info)
    if not stream and not any(s in streams for s in MEDICATION_STREAMS):
        return _effect_unsupported(q, ['received dispense, pharmacy claim or medication order records'], info)
    info['recognised'].append(f'program {name}' + (f' ({drug})' if drug else ''))
    lab = None
    for concept, aliases in sorted(MEASUREMENT_ALIASES.items(), key=lambda kv: -max(map(len, kv[1]))):
        if any(re.search(_alias_pattern(a), low) for a in aliases): lab = concept; break
    outcome = 'lab' if lab else next((o for o, rx in EFFECT_OUTCOMES if re.search(rx, low)), None)
    if outcome is None:
        return _effect_unsupported(q, ['a recognised outcome (ED visits, admissions, PMPM/cost, or a lab measurement)'], info)
    info['recognised'].append(f'outcome {lab or outcome}')
    params = {'program': name, 'outcome': outcome}
    if drug: params['drug_class'] = drug
    if lab: params['lab'] = lab
    days = re.search(r'\b(\d{1,3})[- ]day\b', low)
    if days:
        params['post_days'] = min(max(int(days.group(1)), 14), 365)
        info['recognised'].append(f'post window {params["post_days"]} days')
    conds = []
    for concept, aliases in CONDITION_ALIASES.items():
        if any(re.search(r'\b(?:among|in|for|with)\b(?: our| the)? ' + re.escape(a) + r'\b', low) for a in aliases): conds.append(concept)
    if conds:
        params['population_conditions'] = conds[:5]
        info['recognised'].append('population ' + ', '.join(conds[:5]))
    info.update(route='local_compiler', unexplained=[])
    label = f'{drug} start' if drug else name.replace('_', ' ')
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'program_effect', 'inputs': [], 'parameters': params,
                       'purpose': f'Estimated effect of {label} on {lab or outcome} under stated assumptions (matched DiD, diagnostics)'}]}, info


# ---- care pathways: transitions, journeys, leakage ---------------------------------------
PATHWAY_INTENT = (r'\bcare (?:path(?:way)?s?|journeys?|flows?|sequences?|trajector(?:y|ies))\b|\bclinical pathways?\b|\bpatient (?:journeys?|flows?|paths?)\b|'
                  r'\bjourneys?\b|\bwhere (?:do|did|does) (?:\w+ ){0,4}(?:go|end up|head|turn up)\b|\bleak(?:s|ed|ing|age)?\b|\bout[- ]of[- ]network\b|'
                  r'\bwhat happens (?:\w+ ){0,4}after\b|\bflow of patients\b|\bflows? (?:through|after|from)\b|\bmove (?:through|between) (?:care|settings|the system)\b|'
                  r'\b(?:most )?(?:common|frequent|typical) (?:care )?(?:paths?|routes?|sequences?)\b|\bmarkov\b|\bsankey\b|\btransition (?:matrix|probabilit(?:y|ies))\b|'
                  r'\bends? up (?:being )?(?:admitted|hospitali[sz]ed|in the (?:ed|er|hospital))\b.*\bstarting (?:from|with|at)\b')
PATHWAY_STATES = [  # longest aliases first within each state
    ('emergency', ['emergency department visits', 'emergency department visit', 'emergency room visits', 'emergency room visit', 'emergency visits',
                   'emergency visit', 'emergency department', 'emergency room', 'ed visits', 'ed visit', 'er visits', 'er visit', 'ed', 'er']),
    ('discharge', ['hospital discharges', 'hospital discharge', 'inpatient discharge', 'discharges', 'discharge', 'being discharged', 'discharged']),
    ('observation', ['observation stays', 'observation stay', 'observation']),
    ('inpatient', ['inpatient admissions', 'inpatient admission', 'hospital admissions', 'hospital admission', 'hospitalizations', 'hospitalization',
                   'hospitalisations', 'hospitalisation', 'inpatient stays', 'inpatient stay', 'admissions', 'admission', 'inpatient']),
    ('urgent_care', ['urgent care visits', 'urgent care visit', 'urgent care']),
    ('primary_care', ['primary care visits', 'primary care visit', 'pcp visits', 'pcp visit', 'office visits', 'office visit', 'primary care', 'pcp']),
    ('specialty', ['specialist visits', 'specialist visit', 'specialty visits', 'specialty visit', 'specialty care', 'specialists', 'specialist']),
    ('telehealth', ['telehealth visits', 'telehealth visit', 'virtual visits', 'virtual visit', 'telehealth']),
    ('referral', ['referrals', 'referral']),
]
PATHWAY_START = (r'\b(?:after|following|post|from|starting (?:from|with|at)|beginning (?:from|with|at)|leaving)'
                 r'(?: (?:an?|the|their|each|every|a recent|recent|hospital))*[- ]({})\b')
PATHWAY_FOLLOWUP = r'(?:outpatient |clinic )?follow[- ]?ups?(?: (?:visits?|care|appointments?|contacts?))?\b'
PATHWAY_NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7}


def _pathway_alias(state):
    return '|'.join(re.escape(a) for a in dict(PATHWAY_STATES)[state])


def _pathway_question(q, profile, as_of):
    """Care-pathway questions -> pathway operator, or decline (fallback) when anything meaningful is left unexplained."""
    low = _strip_framing(q.lower())
    if not re.search(PATHWAY_INTENT, low): return None
    if re.search(CLAIMS_LEAK, low) and re.search(r'\b(?:money|revenue|dollars?|cash|claims?|denials?|underpa\w*|billing|payments?)\b', low):
        return None  # revenue leakage is a claims_review question, not patients leaving the network
    info = {'compiler': VERSION, 'recognised': [], 'paraphrased': [], 'route': 'local_compiler'}
    decline = lambda reason: (None, {**info, 'route': 'fallback', 'reason': reason})
    if re.search(r'\bcash flow\b|\bfund(?:s|ing)? flow\b|\bclaims? flow\b|\bby (?:month|quarter|year|week)\b|\bmonthly\b|\bquarterly\b', low):
        return decline('pathway wording with a time-series or financial meaning')
    r = Reader(low); r.profile = profile; info['recognised'] = r.notes
    params = {}
    starts = []
    any_state = '|'.join(re.escape(a) for _, al in PATHWAY_STATES for a in al)
    if re.search(PATHWAY_START.format(any_state) + r'(?: visits?)? (?:or|and|vs\.?|versus) (?:an? |the |after |following )*(?:' + any_state + r')\b', low):
        return decline('more than one starting point named')
    for state, _ in PATHWAY_STATES:
        if r.take(PATHWAY_START.format(_pathway_alias(state)), f'start {state}'): starts.append(state)
    any_condition = '|'.join(re.escape(a) for al in CONDITION_ALIASES.values() for a in sorted(al, key=len, reverse=True))
    new = r.take(r'\b(?:new(?:ly)?(?: diagnosed)?|incident|recently diagnosed)\b(?= (?:\w+ )?(?:' + any_condition + r'))', 'newly diagnosed')
    diagnosis = r.take(r'\b(?:after|since|from|following) (?:(?:their|the|first|initial|a) )*(?:diagnosis|dx)\b', 'start diagnosis')
    conditions = []
    for c, aliases in CONDITION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if r.take(_alias_pattern(alias), f'condition {c}'): conditions.append(c); break
    if new or diagnosis:
        if len(conditions) != 1: return decline('newly diagnosed needs exactly one named condition')
        starts.append('diagnosis'); params['index_concept'] = conditions[0]; params['anchor'] = 'first'
    if len(set(starts)) > 1: return decline('more than one starting point named: ' + ', '.join(sorted(set(starts))))
    if starts: params['start'] = starts[0]
    steps = r.take(r'\bwithin (\d{1,2}|two|three|four|five) (?:steps?|transitions?|moves?|hops?)\b', 'max steps')
    if steps:
        v = steps[0].group(1); params['max_steps'] = int(v) if v.isdigit() else PATHWAY_NUMBERS[v]
    days = None
    for m in r.take(r'\b(?:within|in|over|during|inside|by) (?:the )?(?:first |next |following )?(\d{1,3}|one|two|three|four|six|seven) (days?|weeks?|months?)\b', 'window'):
        n = int(m.group(1)) if m.group(1).isdigit() else PATHWAY_NUMBERS[m.group(1)]
        days = int(round(n * {'d': 1, 'w': 7, 'm': 30.44}[m.group(2)[0]]))
    for m in r.take(r'\b(\d{1,3})[- ]days?\b', 'window'):
        days = int(m.group(1))
    if days is not None and not 1 <= days <= 730: return decline('pathway window must be 1..730 days')
    targets = []
    if r.take(r'\b(?:return(?:s|ed|ing)?|come(?:s)? back|bounce(?:s|d)? back|go(?:es)? back|revisit(?:s)?)(?: visits?)? (?:to )?(?:the )?(?:ed|er|emergency(?: department| room)?)\b|'
              r'\b(?:ed|er) (?:returns?|revisits?|bounce[- ]?backs?)\b|\b(?:end|ends|ending) up (?:back )?in the (?:ed|er|emergency(?: department| room)?)\b', 'target emergency'):
        targets.append('emergency')
    if r.take(r'\b(?:(?:end|ends|ending) up |being |get |gets |getting |is |are )?(?:admitted|hospitali[sz]ed|readmitted)\b|\b(?:re)?admissions?\b|'
              r'\bhospitali[sz]ations?\b|\binpatient(?: stays?)?\b', 'target inpatient'):
        targets.append('inpatient')
    expected = None
    fu = r.take(r'\b(?:(?:get|gets|getting|got|have|has|had|receive|received|see|seen|with) )?(?:an? )?(?:(primary care|pcp|specialist|specialty|telehealth)(?: visit)? )?'
                + PATHWAY_FOLLOWUP, 'expected follow-up')
    if not fu:
        fu = r.take(r'\b(?:get|gets|getting|got|have|has|had|receive|received|see|seen) (?:an? )?(primary care|pcp|specialist|specialty|telehealth)(?: visits?)?\b', 'expected visit')
    if fu:
        to = {'primary care': ['primary_care'], 'pcp': ['primary_care'], 'specialist': ['specialty'], 'specialty': ['specialty'],
              'telehealth': ['telehealth']}.get(fu[0].group(1), ['primary_care', 'specialty', 'telehealth'])
        if 'start' not in params: return decline('an expected follow-up needs a starting point (after an ED visit, after discharge...)')
        expected = {'from': params['start'], 'to': to, 'within_days': days or 7}
    if targets: params['targets'] = targets
    leak = r.take(r'\bleak(?:s|ed|ing|age)?(?: out)?(?: of (?:the )?network)?\b|\bout[- ]of[- ]network\b|\boutside (?:the )?(?:network|organi[sz]ations?|providers?|system)\b', 'leakage')
    if r.take(r'\b(?:other|outside (?:their|the|of their)) (?:practices?|clinics?)\b|\boutside (?:their|of their) (?:own |attributed )?practice\b', 'leakage incl. other practices'):
        params['leakage'] = 'organization_or_practice'
    paths = r.take(r'\b(?:the )?(?:top (\d{1,2}) )?(?:most )?(?:common|frequent|typical|usual) (?:care )?(?:paths?|journeys?|routes?|sequences?|pathways?)\b', 'path variants')
    if paths and paths[0].group(1): params['top'] = min(50, int(paths[0].group(1)))
    if r.take(r'\bsecond[- ]order\b', 'second order'): params['order'] = 2
    if r.take(r'\bby (?:attributed )?(?:practice|clinic)s?\b|\b(?:compar(?:e|ed|ing)(?: across| between)?|across|between|which) (?:the |our )?(?:practices|clinics)\b', 'group by practice'):
        params['group_by'] = 'practice'
    elif r.take(r'\bby (?:payer|payor|coverage(?: type)?|insurance(?: type)?|plan type)\b|'
                r'\b(?:compar(?:e|ed|ing)(?: across| between)?|across|between|which) (?:the |our )?(?:payers|payors|insurers|plans)\b', 'group by payer'):
        params['group_by'] = 'coverage_type'
    period = _take_period(r, as_of)
    if period: params['period'] = {'start': period[0], 'end': period[1]}
    if expected:
        params['expected'] = expected
        params['window_days'] = max(30, expected['within_days'])
        params['horizons_days'] = sorted({expected['within_days'], params['window_days']})
    elif days:
        params['window_days'] = days; params['horizons_days'] = [days]
    r.take(PATHWAY_INTENT, 'pathway', overlap=True)
    r.take(r'\b(?:vs\.?|versus|or|compared (?:to|with)|how many|what (?:share|fraction|proportion|percent(?:age)?)|probability|chance|likelihood|likely|'
           r'share|percent(?:age)?|proportion|rate|odds|where|what|which|how|do|does|did|go|goes|went|next|then|after|afterwards|subsequently|'
           r'happen(?:s|ed)?|move|moves|through|care|path|paths|pathway|pathways|journey|journeys|typically|usually|common|most|our|patients?|members?|'
           r'people|a|an|the|their|they|them|from|starting|start|visit|visits|end|ends|up|step|steps|show|map|trace|describe|look|like|instead|'
           r'within|days?|weeks?|months?|network|out|of|in|any|those|who|ones?|cases?|is|are|that|for|among|with)\b', None, overlap=True)
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
    if info['unexplained']:
        return decline('pathway words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
    explicit = re.search(r'\bmarkov\b|\btransition (?:matrix|probabilit)|\bsankey\b|\bcare (?:path|journey|flow)|\bpatient (?:journey|flow|path)|\bclinical pathway', low)
    if not (params.get('start') or leak or paths or params.get('group_by') or targets or explicit):
        return decline('pathway question without a starting point, outcome, leakage or path request')
    from .pathway import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return decline(f'pathway parameters: {exc}')
    nodes = []
    if conditions:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [],
                      'parameters': {'specification': {'all': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20], 'any': [], 'exclude': []}},
                      'purpose': 'Population named in the question'})
    start = params['start'] or 'first event'
    nodes.append({'id': 'pathway', 'operation': 'pathway', 'inputs': ['population'] if conditions else [], 'parameters': params,
                  'purpose': f'Care pathway from {start.replace("_", " ")}' + (f" to {', '.join(params['targets'])}" if targets else '')
                             + (f" by {params['group_by']}" if params['group_by'] != 'none' else '')})
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


# ---- early warning: matched filtering for pre-event signatures -> early_signal ----------------------
_ACUTE_WORDS = r'(?:admissions?|hospitali\w+|ed visits?|er visits?|emergency(?: department)?(?: visits?)?|decompensation)'
EARLY_SIGNAL_INTENT = (r'\bheading (?:toward|towards|for)\b|\bon (?:the|a) (?:road|path|way) to (?:an? |the )?(?:\w+ ){0,2}' + _ACUTE_WORDS + r'|'
                       r'\bprecursors?\b|\bprodrom\w*|\bpre[- ]?(?:admission|hospitali[sz]ation|ed|event|decompensation) (?:signatures?|patterns?|signals?|templates?|shapes?)\b|'
                       r'\b(?:signatures?|patterns?|signals?|shapes?) (?:that )?(?:usually |typically |often |commonly |tends? to )?(?:precedes?|comes? before|leads? (?:up )?to|foreshadows?)\b|'
                       r'\b(?:signatures?|patterns?|signals?) (?:of|before|preceding|ahead of|leading (?:up )?to) (?:an? |the )?(?:\w+ ){0,3}' + _ACUTE_WORDS + r'\b|'
                       r'\bearly[- ]warning\b|\b(?:early )?warning signs?\b|\bearly signs?\b|\bdecompensat\w*|\bmatched[- ]filter\w*|\bleading indicators?\b|'
                       r'\babout to (?:be admitted|be hospitali[sz]ed|decompensate|end up in (?:the )?(?:ed|er|hospital))\b')


def _early_signal_question(q, profile, as_of):
    """'Which patients look like they're heading toward an admission?' -> early_signal (matched-filter precursor detection).
    Capacity-limited call lists stay with outreach; time-until questions stay with survival."""
    low = _strip_framing(q.lower())
    if not re.search(EARLY_SIGNAL_INTENT, low) or survival_intent(low) or re.search(DEATH_WORDS, low): return None
    if re.search(OUTREACH_INTENT, low) and (re.search(OUTREACH_SLOTS, low) or re.search(OUTREACH_HOURS, low)): return None
    info = {'compiler': VERSION, 'recognised': ['early-signal question'], 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    params = {}
    admit = re.search(r'\b(?:re)?admi(?:t|ssion|tted)\w*|\bhospitali[sz]\w*|\binpatient\b', low)
    ed = re.search(r'\b(?:ed|er)\b|\bemergency\b', low)
    acute = re.search(r'\bacute (?:care|visits?|events?|utili[sz]ation)\b', low)
    params['outcome'] = 'acute_care' if acute or (admit and ed) or not (admit or ed) else 'ed_visit' if ed else 'admission'
    m = re.search(r'\b(?:within|in|over|during)(?: the)? (?:next |coming |following )?(\d{1,3}|one|two|three|four|five|six) (days?|weeks?|months?)\b', low)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else PATHWAY_NUMBERS[m.group(1)]
        months = max(1, min(6, round(n * {'d': 1 / 30.44, 'w': 7 / 30.44, 'm': 1}[m.group(2)[0]])))
        params['horizon_months'] = months; info['recognised'].append(f'horizon {months} months')
    w = re.search(r'\b(\d{1,2})[- ]months? (?:pre[- ]?(?:event|admission)|before|look[- ]?back|window|signature|template)\b', low)
    if w and 3 <= int(w.group(1)) <= 12: params['window_months'] = int(w.group(1))
    f = re.search(r'\bfalse[- ]alarm (?:rate|probability) (?:of |at |below |under )?(\d+(?:\.\d+)?)(%)?', low)
    if f:
        v = float(f.group(1)) / (100 if f.group(2) or float(f.group(1)) > 1 else 1)
        if 0.001 <= v <= 0.5: params['false_alarm_rate'] = v
    conditions = []
    for c, aliases in CONDITION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(_alias_pattern(alias), low): conditions.append(c); break
    if 'heart_failure_reduced_ef' in conditions and 'heart_failure' in conditions: conditions.remove('heart_failure')
    info['recognised'].append(f"outcome {params['outcome']}" + (f" in {', '.join(conditions)}" if conditions else ''))
    nodes = []
    if conditions:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [],
                      'parameters': {'specification': {'all': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20], 'any': [], 'exclude': []}},
                      'purpose': 'Population named in the question'})
    nodes.append({'id': 'early_signal', 'operation': 'early_signal', 'inputs': ['population'] if conditions else [], 'parameters': params,
                  'purpose': 'Matched-filter search for the pre-event signature of ' + params['outcome'].replace('_', ' ')})
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


# ---- provider network: patient-sharing graph, hubs, cohesion, leakage by specialty, steering, spread (network.py) ----
NETWORK_HUB = (r"\b(?:referral|network|provider|physician|care) hubs?\b|\bhubs? (?:of|in) (?:our |the )?(?:referral |provider )?network\b|"
               r"\b(?:key|central|most (?:central|connected|influential)|best[- ]connected|influential) (?:referral )?(?:providers?|physicians?|doctors?|"
               r"clinicians?|specialists?|practices?|hubs?|nodes?|brokers?|organi[sz]ations?)\b|\bcentrality\b|\bbetweenness\b|\bpagerank\b|\bbrokers?\b")
NETWORK_SHARE = (r"\bshar(?:e|es|ed|ing) (?:the )?(?:most |many |more |any |a lot of )?(?:of (?:their|our|the same) )?(?:the same )?patients\b|"
                 r"\bshared[- ]patients?\b|\bpatient[- ]sharing\b|\bpatients? in common\b")
NETWORK_COHESION = r"\bcare[- ]teams? (?:cohesion|cohesiveness|density|fragmentation|networks?)\b|\bcare density\b|\bcohesi(?:on|ve|veness)\b"
NETWORK_COMMUNITY = (r"\b(?:communit(?:y|ies)|clusters?|cliques?) (?:of|among|in) (?:our )?(?:providers?|physicians?|doctors?|clinicians?|practices?|specialists?)\b|"
                     r"\b(?:provider|physician|referral|network) (?:communit(?:y|ies)|clusters?)\b")
NETWORK_GRAPH = (r"\b(?:provider|physician|doctor|clinician|referral) (?:networks?|graphs?)\b|\bnetwork (?:graph|analysis|map|structure|science)\b|"
                 r"\breferral (?:patterns?|concentration|flows?)\b|\bherfindahl\b|\bhhi\b|\bconcentrat\w+ (?:of )?(?:our )?referrals\b")
NETWORK_USE = (r"\b(?:which|what) (?:specialists?|hospitals?|providers?|facilities|organi[sz]ations?|outside groups?)(?: and (?:specialists?|hospitals?|providers?|facilities))? "
               r"(?:do|did|are) (?:our |the )?(?:[a-z0-9-]+ ){0,3}(?:patients|members|people) (?:actually |really )?(?:use|using|see|seeing|go to|going to|visit|rely on)\b")
NETWORK_LEAK = r"\bleak\w*\b|\bout[- ]of[- ]network\b|\boutside (?:of )?(?:our |the )?(?:preferred )?network\b"
NETWORK_LEAK_SCOPE = (r"\bby (?:specialty|specialties|service lines?|practice)\b|\b(?:which|what) (?:specialties|specialists|service lines)\b|\bspecialt(?:y|ies)\b|"
                      r"\bpreferred network\b|\btins?\b|\bspecialists?\b|\bservice lines?\b")
NETWORK_STEER = r"\bsteer(?:s|ed|ing)?\b|\bredirect(?:ing|ed)?\b|\brepatriat\w+\b|\brecover\w* (?:\w+ ){0,3}leak\w*\b|\brecaptur\w+ (?:\w+ ){0,3}leak\w*\b"
NETWORK_SPREAD = (r"\b(?:spread|spreads|spreading|diffus\w+|propagat\w+|cascade\w*|ripple)\b|\binfluence maximi[sz]\w+\b|\bseed (?:pcps?|practices?|providers?|physicians?)\b|"
                  r"\b(?:simulat\w+|model\w*) (?:the )?(?:[a-z-]+ ){0,4}adoption\b")
NETWORK_SPREAD_SCOPE = r"\bnetwork\b|\bpcps?\b|\bpractices?\b|\bphysicians?\b|\bproviders?\b|\bdoctors?\b|\bclinicians?\b|\bprotocols?\b|\bpractice[- ]patterns?\b|\badopt\w*\b"
NETWORK_SERVICE_LINES = [('cardiology', r'cardiolog\w*|cardiac (?:care|specialists?)|heart doctors?'), ('endocrinology', r'endocrinolog\w*|\bendo\b'),
                         ('nephrology', r'nephrolog\w*|kidney (?:doctors?|specialists?)'), ('pulmonology', r'pulmonolog\w*|lung (?:doctors?|specialists?)'),
                         ('orthopedics', r'orthop(?:a)?edic\w*|\bortho\b'), ('gastroenterology', r'gastroenterolog\w*|\bgi\b'),
                         ('neurology', r'neurolog\w*'), ('oncology', r'oncolog\w*|cancer (?:care|doctors?)'), ('dentistry', r'dent(?:al|istry|ists?)'),
                         ('optometry', r'optometr\w*|vision care|eye care'), ('audiology', r'audiolog\w*|hearing'), ('psychiatry', r'psychiatr\w*'),
                         ('physical therapy', r'physical therap\w*'), ('diagnostic radiology', r'radiolog\w*|imaging'),
                         ('emergency medicine', r'emergency medicine'), ('urgent care', r'urgent care')]


def _network_question(q, profile, as_of):
    """Provider-network questions -> network. Declines (None) for patient-journey wording ('after an ED visit', 'care paths'), revenue
    leakage (denials, underpayments) and anything without a provider/network signal, so pathway and claims_review keep theirs."""
    low = _strip_framing(q.lower())
    hits = {k: bool(re.search(rx, low)) for k, rx in (('hub', NETWORK_HUB), ('share', NETWORK_SHARE), ('cohesion', NETWORK_COHESION),
                                                      ('community', NETWORK_COMMUNITY), ('graph', NETWORK_GRAPH), ('use', NETWORK_USE),
                                                      ('steer', NETWORK_STEER))}
    hits['spread'] = bool(re.search(NETWORK_SPREAD, low) and re.search(NETWORK_SPREAD_SCOPE, low)
                          and not re.search(r'\bspread (?:of|in) (?:the )?(?:values?|costs?|scores?|results?|a1c|lab|distribution)\b|\bdistribution\b', low))
    journey = re.search(r'\b(?:after|following|post)[- ](?:an? |the |their |each |hospital |recent )*(?:ed|er|emergency|discharge\w*|admission|hospitali[sz]\w*|'
                        r'inpatient|urgent care|pcp|primary care|visit|stay)\b|\bjourneys?\b|\bcare (?:paths?|pathways?|sequences?)\b|\bmarkov\b|\bnext steps?\b', low)
    revenue = re.search(r'\bdenials?\b|\bunderpa\w*\b|\bwrite[- ]?offs?\b|\brework\b|\btake[- ]?backs?\b|\bleaking money\b|\brevenue leak\w*\b', low)
    specialist_lines = '|'.join(rx for name, rx in NETWORK_SERVICE_LINES if name not in ('emergency medicine', 'urgent care'))
    leak = bool(re.search(NETWORK_LEAK, low) and re.search(NETWORK_LEAK_SCOPE + '|' + specialist_lines, low) and not re.search(r'\bcompare\w*\b|\bpeers?\b', low))
    if revenue or not (any(hits.values()) or leak): return None
    if journey and not (hits['hub'] or hits['share'] or hits['cohesion'] or hits['community'] or hits['graph'] or hits['steer'] or hits['spread']):
        return None
    if hits['steer'] and not re.search(NETWORK_LEAK + r'|\bvolume\b|\bvisits?\b|\bin[- ]network\b|' + '|'.join(rx for _, rx in NETWORK_SERVICE_LINES), low):
        return None
    params, recognised = {}, ['provider network question']
    line = next((name for name, rx in NETWORK_SERVICE_LINES if re.search(rf'(?:{rx})', low)), '')
    focus = ('steering' if hits['steer'] else 'spread' if hits['spread'] else 'leakage' if leak else 'cohesion' if hits['cohesion'] else
             'communities' if hits['community'] else 'concentration' if re.search(r'\breferral (?:concentration|patterns?)\b|\bherfindahl\b|\bhhi\b|\bconcentrat', low) else
             'pcp_sharing' if hits['share'] and re.search(r'\bpcps?\b|\bprimary care\b|\bdoctors?\b|\bphysicians?\b|\bpractices?\b|\bproviders?\b', low) else
             'usage' if hits['use'] else 'hubs' if hits['hub'] else 'all')
    params['focus'] = focus
    if focus == 'steering':
        if not line: return None, {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'fallback',
                                   'reason': 'steering question without a recognised service line'}
        params['steer_specialty'] = line
        m = re.search(r'\bto (wh-[a-z0-9-]+)\b', low) or re.search(r'\bto (?:the )?((?:practice|clinic|group|hospital|specialty care|organi[sz]ation) [a-z0-9-]+)\b', low)
        if m: params['steer_to'] = m.group(1).upper() if m.group(1).startswith('wh-') else m.group(1)
        s = re.search(r'\b(\d{1,3}(?:\.\d+)?)\s?%', low)
        if s and 0 < float(s.group(1)) <= 100: params['steer_share'] = float(s.group(1)) / 100
        elif re.search(r'\bhalf\b', low): params['steer_share'] = 0.5
        elif re.search(r'\ball (?:of )?(?:the |our )?(?:leak\w*|it)\b', low): params['steer_share'] = 1.0
        if re.search(r'\bconsolidat\w+\b|\ball other\b|\bfrom other (?:in[- ]network )?(?:practices|groups|providers)\b', low): params['steer_from'] = 'all_other'
    elif line and focus in ('leakage', 'concentration'):
        params['specialty'] = line
    if focus == 'spread':
        if re.search(r'\bthreshold\b', low): params['spread_model'] = 'linear_threshold'
        m = re.search(r'\b(\d{1,2}) (?:seeds?|pcps?|practices?|champions?|early adopters?|physicians?|doctors?)\b', low)
        if m and 1 <= int(m.group(1)) <= 10: params['seeds'] = int(m.group(1))
        m = re.search(r'\b(\d{1,2}) (?:rounds?|steps?|generations?)\b', low)
        if m and 1 <= int(m.group(1)) <= 20: params['steps'] = int(m.group(1))
        m = re.search(r'\b(\d{1,2}(?:\.\d+)?)\s?% (?:transmission|chance|probability|adoption)\b', low)
        if m and 0 < float(m.group(1)) <= 100: params['transmission'] = float(m.group(1)) / 100
    if re.search(r'\bby (?:practice|clinic)s?\b|\b(?:each|per) (?:practice|clinic)\b|\bwhich practices\b', low): params['group_by'] = 'practice'
    if focus in ('hubs', 'communities', 'all') and re.search(r'\b(?:organi[sz]ations?|facilit(?:y|ies)|hospitals?|practices?|groups?|tins?)\b', low) \
            and not re.search(r'\b(?:pcps?|physicians?|doctors?|clinicians?|providers?|specialists?)\b', re.sub(r'\bprovider network\b', '', low)):
        params['level'] = 'organization'
    tins = re.findall(r'\b\d{2}-\d{7}\b', low)
    if tins: params.update(leakage='tins', network_tins=tins)
    elif re.search(r'\bnon[- ](?:aco|attributed) pcps?\b|\bother practices?\b', low): params['leakage'] = 'organization_or_practice'
    m = re.search(r'\b(?:at least|>=?|minimum of|min(?:imum)?) (\d{1,3}) (?:shared )?patients\b', low)
    if m and 1 <= int(m.group(1)) <= 100: params['min_shared'] = int(m.group(1))
    if re.search(r'\bdisparity filter\b|\bbackbone\b', low): params['filter'] = 'disparity'
    m = re.search(r'\btop (\d{1,3})\b', low)
    if m and 1 <= int(m.group(1)) <= 200: params['top'] = int(m.group(1))
    m = re.search(r'\b(?:in|for|during) (20\d\d)\b', low)
    if m and m.group(1) <= as_of[:4]:
        params['period'] = {'start': f'{m.group(1)}-01-01', 'end': min(f'{m.group(1)}-12-31', as_of[:10])}
    else:
        m = re.search(r'\b(?:last|past|previous) (\d{1,2}) (months?|years?)\b|\b(?:last|past) (year|12 months)\b', low)
        if m:
            days = 365 if m.group(3) else int(m.group(1)) * (30 if m.group(2).startswith('month') else 365)
            if 30 <= days <= 3650: params['window_days'] = days
    conditions = []
    for c, aliases in CONDITION_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(_alias_pattern(alias), low): conditions.append(c); break
    from .network import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return None, {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'fallback', 'reason': f'network parameters: {exc}'}
    recognised.append(f"focus {params['focus']}" + (f" ({line})" if line else '') + (f" in {', '.join(conditions)}" if conditions else ''))
    nodes = []
    if conditions:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [],
                      'parameters': {'specification': {'all': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20], 'any': [], 'exclude': []}},
                      'purpose': 'Population named in the question'})
    nodes.append({'id': 'answer', 'operation': 'network', 'inputs': ['population'] if conditions else [], 'parameters': params,
                  'purpose': f"Provider network: {params['focus']}" + (' (model)' if params['focus'] in ('steering', 'spread') else '')})
    info = {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}, info


# ---- quality & safety: loop closure, diagnostic delay look-back, risk-standardized outcomes -----------------------------
def _safety_conditions(low, skip=()):
    out = []
    for c, aliases in CONDITION_ALIASES.items():
        if c in skip: continue
        for alias in sorted(aliases, key=len, reverse=True):
            if re.search(_alias_pattern(alias), low): out.append(c); break
    return out


def _safety_plan(q, op, params, conditions, purpose, recognised, combine='all', coverage='supported_observation', unmet=()):
    nodes = []
    if conditions:
        nodes.append({'id': 'population', 'operation': 'cohort', 'inputs': [],
                      'parameters': {'specification': {'all': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20] if combine == 'all' else [],
                                                       'any': [{'kind': 'concept', 'concept': c, 'window': W} for c in conditions][:20] if combine == 'any' else [],
                                                       'exclude': []}},
                      'purpose': 'Population named in the question'})
    nodes.append({'id': 'answer', 'operation': op, 'inputs': ['population'] if conditions else [], 'parameters': params, 'purpose': purpose})
    info = {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'local_compiler', 'unexplained': []}
    return {'interpretation': q, 'coverage': coverage, 'unmet_requirements': list(unmet), 'nodes': nodes}, info


LOOP_ITEMS = (r"\b(?:lab(?:oratory)?|test|critical|abnormal|panic|imaging|screening|diagnostic)? ?(?:results?|values?|labs?)\b|\breferrals?\b|\bconsults?\b|"
              r"\b(?:lab|imaging|diagnostic|test|radiology) orders?\b|\bordered (?:labs?|tests?|imaging|studies)\b|\brecommendations?\b|\bincidental (?:findings?|nodules?)\b|"
              r"\bnodules?\b|\bmammogra\w+\b|\bbi-?rads\b|\bfit\b|\bfecal immunochemical\b|\bpositive (?:screens?|fit|stool)\b|\breports?\b|\bfollow[- ]?up imaging\b")
LOOP_FAIL = (r"\bno (?:documented |recorded )?(?:follow[- ]?up|acknowledg\w+|action|review|response|work[- ]?up)\b|\bwithout (?:a |any )?(?:documented |recorded )?"
             r"(?:follow[- ]?up|acknowledg\w+|action|review|response|work[- ]?up|diagnostic follow[- ]?up)\b|\bnot (?:been )?(?:followed[- ]up|acted (?:up)?on|acknowledged|"
             r"completed|resulted(?! in)|reviewed|addressed|closed|done|performed|collected)\b|\bnever (?:been )?(?:completed|resulted(?! in)|collected|acknowledged|"
             r"reviewed|closed|done|followed up|acted on|returned)\b|\bunacknowledged\b|\bunreviewed\b|\buncompleted\b|\bincomplete (?:referrals?|work[- ]?ups?)\b|"
             r"\bopen (?:loops?|referrals?|results?|orders?)\b|\b(?:clos(?:e|ed|ing)|clos(?:ure|ed)) (?:the |of )?loops?\b|\bloop[- ]closure\b|\bfell through the cracks\b|"
             r"\blost to follow[- ]?up\b|\boverdue (?:follow[- ]?ups?|referrals?|results?|work[- ]?ups?)\b|\bstill (?:open|pending|outstanding)\b|\boutstanding (?:referrals?|results?|orders?)\b|"
             r"\b(?:close|closure|completion|follow[- ]?up|acknowledg\w+) rates?\b|\bdropped (?:the )?ball\b|\bhad (?:it|them) done\b|\bhave not had (?:it|them) done\b|"
             r"\b(?:time|days?) (?:from|between) (?:an? )?(?:abnormal|critical|positive)? ?(?:results?|labs?) (?:to|and) (?:a )?(?:documented )?(?:clinician )?"
             r"(?:review|acknowledg\w+|action)\b|\bconsult (?:notes?|reports?|letters?) (?:returned|received|sent back)\b|\b(?:returned|received) (?:a )?consult (?:notes?|reports?)\b|"
             r"\bhow many (?:were|got|have been) (?:completed|done|performed)\b|\bnever had (?:a |an |the |any )?(?:\w+ ){0,2}(?:colonoscop\w*|follow[- ]?up|"
             r"work[- ]?up|diagnostic \w+|repeat \w+|imaging|visit)\b|\bwithout (?:a |an |any )?(?:colonoscop\w*|diagnostic (?:mammogra\w*|imaging|work[- ]?up))\b|"
             r"\bno (?:colonoscop\w*|diagnostic (?:mammogra\w*|imaging|work[- ]?up))\b|\bwere completed\b|\bdiagnostic follow[- ]?up within\b|\bcommunicated\b")
LOOP_LABS = [('potassium', r'potassium|\bk\+'), ('sodium', r'sodium'), ('glucose', r'glucose|blood sugar'), ('hba1c', r'\ba1c|hemoglobin a1c'),
             ('hemoglobin', r'hemoglobin(?! a1c)|\bhgb\b'), ('platelets', r'platelets?'), ('wbc', r'\bwbc\b|white (?:blood )?cell'),
             ('egfr', r'\begfr\b'), ('creatinine', r'creatinine'), ('uacr', r'\buacr\b|albumin'), ('ferritin', r'ferritin'), ('tsh', r'\btsh\b'),
             ('troponin', r'troponin'), ('ldl', r'\bldl\b')]


def _loop_closure_question(q, profile, as_of):
    """Results / referrals / recommendations / orders without follow-up -> loop_closure. Needs a loop item AND a failure-to-close or
    closure-rate phrase; time-to-event wording ('how long until', 'Kaplan-Meier') stays survival, HCC wording risk_adjustment."""
    low = _strip_framing(q.lower())
    generic = re.search(r'\bloop[- ]closure\b|\bopen loops?\b|\bclos(?:e|ing) (?:the )?loops?\b', low)
    if not (generic or (re.search(LOOP_ITEMS, low) and re.search(LOOP_FAIL, low))): return None
    if re.search(r'\bkaplan|\bhazard\b|\bhow (?:quickly|long)\b|\bhcc\b|\braf\b|\bsuspect|\bdenials?\b|\bclaims?\b|\bcontrol charts?\b', low): return None
    if re.search(r'\bcall (?:first|list)\b|\bshould (?:we |our |the )?(?:schedulers?|staff|nurses?|care managers?|team )?(?:call|contact|reach)\b|\bworklist\b|'
                 r'\bprioriti[sz]\w*\b|\bschedulers?\b|\boutreach\b', low): return None  # who-to-call lists are outreach
    if re.search(r'\bhow many\b', low) and re.search(r'\bbooked\b|\bappointments?\b|\bscheduled\b', low): return None  # booking-status counts are literal
    if re.search(r'\b\d+ minutes?\b|\bwithin (?:an? |one |\d+ )?hours?\b|\bread[- ]?back\b|\bcommunicated\b|\bcalled (?:to|in)\b', low) and re.search(r'\bcritical\b', low):
        info = {'compiler': VERSION, 'recognised': ['critical-result communication timing'], 'paraphrased': [], 'route': 'local_unsupported', 'unexplained': []}
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: critical-result call log (time called, recipient, read-back) - not received; loop_closure shows '
                                       'critical results without any acknowledgement or action evidence, in days']}, info
    if re.search(r'\bafter (?:an? |the |their )?(?:ed|er|emergency|hospital|inpatient)? ?(?:visits?|discharges?|stays?|admissions?)\b', low) and not re.search(
            r'\breferrals?\b|\bresults?\b|\brecommendations?\b|\borders?\b', low): return None  # post-discharge follow-up is pathway / measures
    params, recognised = {}, ['loop closure question']
    focus = ('critical' if re.search(r'\bcritical (?:\w+ ){0,2}(?:labs?|results?|values?)\b|\bpanic values?\b', low) else
             'screening' if re.search(r'\bmammogra\w+\b|\bbi-?rads\b|\bfit\b|\bfecal\b|\bpositive (?:screens?|stool)\b|\bcolonoscop\w+\b', low) else
             'referrals' if re.search(r'\breferrals?\b|\bconsults?\b|\breferred\b', low) else
             'recommendations' if re.search(r'\brecommend\w*\b|\bincidental\b|\bnodules?\b|\bfollow[- ]?up imaging\b', low) else
             'orders' if re.search(r'\borders?\b|\bordered\b|\bnever (?:collected|resulted)\b|\bnot (?:collected|resulted)\b', low) else
             'reports' if re.search(r'\breports?\b', low) and not re.search(r'\blabs?\b|\bresults?\b', low) else
             'results' if re.search(r'\bresults?\b|\blabs?\b|\bvalues?\b', low) else 'all')
    if re.search(r'\bopen loops?\b|\bloop[- ]closure\b|\bclos\w* (?:the |of )?loops?\b', low) and not re.search(r'\breferrals?\b|\bresults?\b|\blabs?\b|\borders?\b', low):
        focus = 'all'
    params['focus'] = focus
    streams = set((profile or {}).get('streams') or {})
    if focus == 'screening' and streams and not streams & {'cancer_screening_results', 'received_screening_reports'}:
        return None, {'compiler': VERSION, 'recognised': recognised + ['positive screens'], 'paraphrased': [], 'route': 'fallback',
                      'reason': 'missing input: cancer screening results (mammography BI-RADS, FIT results) are not received in this release'}
    lab = next((name for name, rx in LOOP_LABS if re.search(rx, low)), '')
    if lab and focus in ('results', 'critical'): params['concept'] = lab
    m = re.search(r'\b(?:after|within|in|over|more than|longer than|past|beyond|at least) (\d{1,3}) (days?|weeks?|months?)\b', low)
    if m:
        days = int(m.group(1)) * {'d': 1, 'w': 7, 'm': 30}[m.group(2)[0]]
        if 1 <= days <= 730:
            from .loop_closure import FOCUS_TYPES
            params['windows'] = {t: days for t in FOCUS_TYPES[focus]}
    if re.search(r'\bby (?:ordering |referring |responsible )?(?:clinicians?|providers?|physicians?|doctors?|owners?|pcps?)\b|\bwhich (?:clinicians?|providers?|physicians?|doctors?)\b|\bper (?:clinician|provider|physician)\b', low):
        params['group_by'] = 'owner'
    elif re.search(r'\bby (?:item )?type\b|\bby kind\b', low):
        params['group_by'] = 'item_type'
    if re.search(r'\bspecific (?:evidence|follow[- ]?up)\b|\bdocumented (?:acknowledg\w+|review)\b', low): params['evidence'] = 'specific'
    if re.search(r'\breference ranges?\b|\bflagged (?:high|low|abnormal)\b|\bout of range\b', low): params['abnormal_basis'] = 'reference_range'
    pm = re.search(r'\b(wh-practice-[a-z0-9-]+)\b', low)
    if pm: params['practice'] = pm.group(1).upper()
    cm = re.search(r'\b(wh-clin-[a-z0-9-]+)\b', low)
    if cm: params['owner'] = cm.group(1).upper()
    pr = re.search(r'\b(?:in|for|during) (20\d\d)\b', low)
    if pr and pr.group(1) <= as_of[:4]:
        params['period'] = {'start': f'{pr.group(1)}-01-01', 'end': min(f'{pr.group(1)}-12-31', as_of[:10])}
    else:
        lm = re.search(r'\b(?:last|past|previous) (\d{1,2}) (months?|years?)\b|\b(?:last|past|previous) (year|12 months)\b', low)
        if lm:
            days = 365 if lm.group(3) else int(lm.group(1)) * (30 if lm.group(2).startswith('month') else 365)
            if 30 <= days <= 3650: params['window_days'] = days
    conditions = _safety_conditions(low)
    from .loop_closure import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return None, {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'fallback', 'reason': f'loop_closure parameters: {exc}'}
    recognised.append(f"focus {params['focus']}" + (f" ({params['concept']})" if params['concept'] else ''))
    return _safety_plan(q, 'loop_closure', params, conditions, f"Open loops and close rates: {params['focus']}", recognised)


DELAY_TARGETS = [('colorectal_cancer', r'\b(?:colorectal|colon|bowel|rectal) cancers?\b|\bcrc\b'), ('ckd', r'\bckd\b|\bchronic kidney disease\b|\bkidney disease\b'),
                 ('heart_failure', r'\bheart failure\b|\bchf\b|\bhfref\b'), ('stroke', r'\bstrokes?\b|\btias?\b'),
                 ('myocardial_infarction', r'\bmyocardial infarctions?\b|\bheart attacks?\b|\bstemi\b|\bnstemi\b'), ('sepsis', r'\bsepsis\b|\bseptic\b'),
                 ('diabetes', r'\bdiabet\w*\b|\bt2dm\b'), ('hypertension', r'\bhypertension\b|\bhigh blood pressure\b|\bhtn\b')]
DELAY_WORDS = (r"\bmissed (?:or delayed )?diagnos\w*\b|\bdelay(?:ed|s)? (?:in )?diagnos\w*\b|\bdiagnostic (?:delays?|errors?|safety|look-?backs?)\b|"
               r"\blook-?backs?\b|\bspade\b|\bmissed (?:diagnostic )?opportunit\w+\b|\bmissed (?:an? )?(?:\w+ ){0,2}(?:cases?|diagnos\w*)\b|"
               r"\bbefore (?:their |the |a |being )?(?:\w+ ){0,3}diagnos\w*\b|\bprior to (?:their |the )?(?:\w+ ){0,3}diagnos\w*\b|"
               r"\b(?:recorded|documented|present|appeared|flagged) (?:more than |at least )?(?:\d+ (?:days?|weeks?|months?|years?) )?(?:earlier|before)\b|"
               r"\bhow long (?:before|until) (?:\w+ ){0,4}diagnos\w*\b|\bhow late\b|\bdiagnosed late\b|\blate diagnos\w*\b|"
               r"\bdiagnos\w* (?:of )?(?:\w+ ){0,3}(?:was |were |is |are |been |get |got )?delayed\b")
UNDIAGNOSED_WORDS = (r"\bundiagnosed\b|\bnot (?:yet )?diagnosed\b|\b(?:signals?|signs?|evidence|criteria) (?:of|for) (?:\w+ ){0,3}(?:but|without|with no)\b|"
                     r"\bmeets? (?:the )?(?:\w+ ){0,3}criteria\b|\bmeeting (?:the )?(?:\w+ ){0,3}criteria\b|\b(?:but|without|with no|and no) (?:a |any )?(?:\w+ ){0,3}diagnosis\b|\bnever diagnosed\b")


def _diagnostic_delay_question(q, profile, as_of):
    """Missed / delayed diagnosis questions -> diagnostic_delay (look-back, undiagnosed review prompts, ED look-forward). Coding and HCC
    'suspect' wording stays risk_adjustment; plain 'how many patients have X but no Y diagnosis' counts stay literal SQL."""
    low = _strip_framing(q.lower())
    if re.search(r'\bhccs?\b|\braf\b|\bsuspect\w*\b|\bcod(?:e|es|ed|ing)\b|\bdocumentation gaps?\b|\brecapture\b', low): return None
    target = next((t for t, rx in DELAY_TARGETS if re.search(rx, low)), None)
    ed_index = re.search(r'\b(?:discharged|released|sent home) from the (?:ed|er|emergency (?:department|room))\b|\btreat[- ]and[- ]release\b|\bed discharges?\b|'
                         r'\b(?:ed|er|emergency) (?:visits? )?(?:with|for) (?:\w+ ){0,3}(?:and|then|but) (?:were |was )?(?:sent home|discharged)\b', low)
    forward = bool(ed_index and re.search(r'\badmitted (?:for|with)\b|\badmissions? (?:for|with)\b|\breturned (?:with|for)\b', low))
    delay = re.search(DELAY_WORDS, low) or re.search(r'\b(?:new|incident|newly diagnosed|recent) (?:\w+ ){0,3}(?:diagnos\w*|cases?)\b.*\b(?:earlier|before|prior|preceding)\b', low)
    undiagnosed = re.search(UNDIAGNOSED_WORDS, low)
    if not (forward or delay or undiagnosed) or not target: return None
    if undiagnosed and not delay and not forward and re.match(r'^how many\b', low) and not re.search(r'\bundiagnosed\b|\bmissed\b|\bdelay', low):
        return None  # a count with a 'but no X diagnosis' clause is a literal lookup
    if undiagnosed and not delay and not forward and re.search(r'\b(?:under|below|above|over|less than|more than|greater than|at least)\s*\d|[<>]=?\s*\d|'
                                                               r'\blatest\b|\bmost recent\b|\bproblem lists?\b', low):
        return None  # the user's own lab predicate ('latest eGFR under 60', 'not on the problem list') is a literal lookup, not the criterion
    params, recognised = {'target': target}, ['diagnostic delay question']
    from .diagnostic_delay import CRITERION_TARGETS
    if forward:
        params['mode'] = 'look_forward'
    elif target not in CRITERION_TARGETS:
        params['mode'] = 'lookback'
    else:
        params['mode'] = 'all' if (delay and undiagnosed) else 'lookback' if delay else 'undiagnosed'
    m = re.search(r'\bwithin (\d{1,3}) (days?|weeks?|months?)\b', low)
    if m and forward:
        params['horizon_days'] = min(365, int(m.group(1)) * {'d': 1, 'w': 7, 'm': 30}[m.group(2)[0]])
    m = re.search(r'\b(?:look[- ]?back|window) of (\d{1,2}) (months?|years?)\b|\b(?:in|over) the (\d{1,2}) (months?|years?) before\b', low)
    if m:
        n, u = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        params['lookback_days'] = max(90, min(3650, int(n) * (30 if u.startswith('month') else 365)))
    if re.search(r'\bby (?:pcp|provider|physician|clinician|doctor)s?\b', low): params['group_by'] = 'pcp'
    if forward and re.search(r'\bwith (?:dizziness|vertigo|chest pain|fever|headache|syncope|fainting)\b', low): params['symptom_only'] = True
    from .diagnostic_delay import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return None, {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'fallback', 'reason': f'diagnostic_delay parameters: {exc}'}
    recognised.append(f"target {target}, mode {params['mode']}")
    target_concepts = {'ckd': 'chronic_kidney_disease', 'diabetes': 'diabetes', 'hypertension': 'hypertension', 'heart_failure': 'heart_failure', 'stroke': 'stroke',
                       'myocardial_infarction': 'myocardial_infarction', 'sepsis': 'sepsis'}
    skip = {target_concepts.get(target), 'type_2_diabetes', 'type_1_diabetes', 'heart_failure_reduced_ef'} if target in ('diabetes', 'heart_failure') else {target_concepts.get(target)}
    conditions = [c for c in _safety_conditions(low, skip) if not (target == 'colorectal_cancer' and c == 'cancer')]
    if params['mode'] in ('undiagnosed', 'all'): conditions = []  # the population IS the undiagnosed signal carriers
    return _safety_plan(q, 'diagnostic_delay', params, conditions, f"Diagnostic delay ({params['mode']}) for {target.replace('_', ' ')}: review prompts", recognised)


OUTCOME_STRONG = (r"\brisk[- ]standardi[sz]ed\b|\brsrr\b|\brsmr\b|\bsrr\b|\bexcess readmission ratios?\b|\berr\b|\bobserved[- ](?:to[- ])?expected\b|\bo/e\b|"
                  r"\bo:e\b|\bfunnel plots?\b|\bhrrp\b|\bhospital readmissions? reduction\b|\bstandardi[sz]ed\b")
OUTCOME_WORDS = (r"\breadmi(?:ssions?|ts?|tted)\b|\b(?:ed|er|emergency (?:department|room)) (?:revisits?|returns?|bounce[- ]?backs?)\b|\breturns? to the (?:ed|er)\b|"
                 r"\brevisits?\b|\blength of stay\b|\blos\b|\bmortality\b|\bdeaths?\b|\bed visits? after (?:hospital )?discharge\b|\bpost[- ]discharge ed\b|\bedac\b")


def _risk_outcomes_question(q, profile, as_of):
    """CMS-style risk-standardized outcomes (RSRR, O/E, ERR, funnel, HRRP) -> risk_outcomes. Plain fair comparison of PCPs / practices
    ('adjusting for case mix', 'truly high') stays profile unless the unit is a hospital or the CMS vocabulary is used."""
    low = _strip_framing(q.lower())
    if not re.search(OUTCOME_WORDS + r'|\brsrr\b|\brsmr\b|\bexcess readmission ratios?\b|\bhrrp\b', low): return None
    if re.search(r'\bplanned\b', low): return None  # planned vs unplanned needs a planned-readmission flag: literal lookup / unsupported
    strong = re.search(OUTCOME_STRONG, low)
    if re.match(r'^how many\b', low) and not re.search(r'\bratios?\b|\bo/e\b|\bstandardi[sz]ed\b|\brsrr\b|\bexpected\b', low): return None
    adjusted = re.search(r"\b(?:risk|case[- ]mix)[- ]adjusted\b|\badjust\w* for (?:case[- ]mix|risk|acuity|severity)\b|\bafter (?:case[- ]mix|risk) adjustment\b", low)
    hospital = re.search(r'\bhospitals?\b|\bfacilit(?:y|ies)\b|\bsites?\b', low)
    if not (strong or (adjusted and hospital)): return None
    if re.search(r'\bmoved?\b|\bmovers?\b|\btrend\w*\b|\bthis month\b|\btickers?\b|\bkaplan\b|\bhazard\b|\bhow (?:quickly|long)\b|\bwhy\b|\bdrivers?\b', low) and not strong:
        return None
    params, recognised = {}, ['risk-standardized outcome question']
    outcome = ('length_of_stay' if re.search(r'\blength of stay\b|\blos\b', low) else
               'mortality' if re.search(r'\bmortality\b|\bdeaths?\b|\brsmr\b', low) else
               'post_discharge_ed' if re.search(r'\bed visits? after (?:hospital )?discharge\b|\bpost[- ]discharge ed\b|\bedac\b', low) else
               'ed_revisit' if re.search(r'\b(?:ed|er|emergency (?:department|room)) (?:revisits?|returns?|bounce[- ]?backs?)\b|\breturns? to the (?:ed|er)\b|\brevisits?\b', low) else
               'readmission')
    params['outcome'] = outcome
    if re.search(r'\bpenalt\w*\b|\bpayment (?:reduction|adjustment)s?\b|\bat risk\b', low) and re.search(r'\bhrrp\b|\breadmi', low):
        info = {'compiler': VERSION, 'recognised': recognised + ['HRRP payment reduction'], 'paraphrased': [], 'route': 'local_unsupported', 'unexplained': []}
        return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                'unmet_requirements': ['Missing input: CMS HRRP inputs (base-operating DRG payments by condition, the peer-group median excess readmission '
                                       'ratio and dual-eligible share from CMS files) - not received; the excess readmission ratio itself is computable']}, info
    unit = ('pcp' if re.search(r'\b(?:by|each|per|every) (?:pcp|attending|physician|provider|doctor|clinician)s?\b|\b(?:pcps|attendings|physicians|providers|doctors|clinicians)\b', low) else
            'practice' if re.search(r'\bpractices?\b|\bclinics?\b', low) else 'hospital')
    params['unit'] = unit
    m = re.search(r'\b(\d{1,3})[- ]day\b', low) or re.search(r'\bwithin (\d{1,3}) days?\b', low)
    h = re.search(r'\b(\d{1,3})[- ]hours?\b', low)
    if m and outcome != 'length_of_stay': params['window_days'] = max(1, min(365, int(m.group(1))))
    elif h and outcome != 'length_of_stay': params['window_days'] = max(1, round(int(h.group(1)) / 24))
    if re.search(r'\bobservation\b', low): params['include_observation'] = True
    if re.search(r'\bage and sex\b|\bage/sex\b|\bdemographics? only\b', low): params['adjust'] = 'age_sex'
    elif re.search(r'\bunadjusted\b|\bcrude\b', low): params['adjust'] = 'none'
    fm = re.search(r'\b(wh-(?:hospital|practice|clin)-[a-z0-9-]+)\b', low)
    if fm: params['focus'] = fm.group(1).upper()
    pr = re.search(r'\b(?:in|for|during) (20\d\d)\b', low)
    if pr and pr.group(1) <= as_of[:4]:
        params['period'] = {'start': f'{pr.group(1)}-01-01', 'end': min(f'{pr.group(1)}-12-31', as_of[:10])}
    conditions = _safety_conditions(low)
    from .risk_outcomes import validate
    try:
        params = validate(params)
    except ValueError as exc:
        return None, {'compiler': VERSION, 'recognised': recognised, 'paraphrased': [], 'route': 'fallback', 'reason': f'risk_outcomes parameters: {exc}'}
    recognised.append(f"{outcome} by {unit}")
    unmet, coverage = [], 'supported_observation'
    if re.search(r'\bdrg\b|\bservice lines?\b|\bdiagnosis[- ]related group', low):
        claims = (((profile or {}).get('streams') or {}).get('medical_claims') or {}).get('fields') or {}
        if not ({'ms_drg', 'drg'} & set(claims)):
            return None, {'compiler': VERSION, 'recognised': recognised + ['DRG grouping'], 'paraphrased': [], 'route': 'fallback',
                          'reason': 'missing input: MS-DRG on inpatient claims or stays is not received in this release'}
        unmet, coverage = [f"Grouping by DRG family / service line needs a DRG or service line on each index stay (DRGs arrive on claims only); shown by {unit} instead"], 'partial'
    return _safety_plan(q, 'risk_outcomes', params, conditions, f"Risk-standardized {outcome.replace('_', ' ')} by {unit}", recognised,
                        combine='any', coverage=coverage, unmet=unmet)
