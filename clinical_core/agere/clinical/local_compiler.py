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
from collections import Counter

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
    'uacr': ['uacr', 'albumin/creatinine', 'urine albumin'], 'nt_probnp': ['nt-probnp', 'ntprobnp', 'bnp'],
    'troponin': ['troponin'], 'tsh': ['tsh'], 'ferritin': ['ferritin'], 'phq9': ['phq-9', 'phq9'],
    'systolic_bp': ['systolic', 'systolic blood pressure', 'systolic bp'],
    'diastolic_bp': ['diastolic', 'diastolic blood pressure', 'diastolic bp'],
    'heart_rate': ['heart rate', 'pulse'], 'weight': ['weight'], 'spo2': ['spo2', 'oxygen saturation'],
}
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
SOCIAL_FLAGS = {  # phrase -> (stream, field)
    'food difficulty': ('social_screens', 'food_difficulty_reported'),
    'food insecurity': ('social_screens', 'food_difficulty_reported'),
    'transport difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation barrier': ('social_screens', 'transport_difficulty_reported'),
    'transportation barriers': ('social_screens', 'transport_difficulty_reported'),
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
UNSUPPORTED = [
    (r'\bcausal(?:ly)?\b|\bcaus(?:ed|ally) (?:by|prevented)|\bprevented by\b|\bidentified causal effect\b|\battributable\b|'
     r'\b(?:did|does|do) [a-z -]{3,60} (?:reduce|lower|cut|prevent|improve|increase|decrease|drive down)\b|\bimpact of\b|\beffect of\b',
     ['identified causal design or randomised comparison', 'counterfactual outcome model', 'validated outcome attribution method']),
    (r'\b(?:externally )?validated,? calibrated\b|\bcalibrated probability\b',
     ['externally validated prediction model', 'calibration data for this population']),
    (r'\bshared[- ]savings\b|\bpayer contract\b|\bsettlement\b',
     ['executed payer contract terms', 'attribution roster under the contract', 'benchmark/baseline expenditure',
      'quality score and sharing-rate schedule']),
    (r'\bcapitation\b|\bcontract(?:ed)? rate\b|\bmedicare advantage contract\b|\bmla\b|\bpremium revenue\b',
     ['executed payer contract with capitation/premium rates', 'member-month eligibility under the contract', 'risk-adjusted revenue schedule']),
    (r'\bobjectively\b|\bwearable\b|\bactivity tracker\b|\baccelerometer\b|\bminutes of (?:moderate|vigorous) exercise\b',
     ['device or objectively measured activity data (none is received)']),
    (r'\b(?:died|deaths?|mortality|deceased|passed away)\b', ['death or mortality records (none are received)']),
    (r'\brace\b|\bethnicit(?:y|ies)\b', ['race/ethnicity fields (none are received)']),
    (r'\bcahps\b|\bpatient experience scores?\b|\bsatisfaction scores?\b', ['patient experience survey results (none are received)']),
    (r'\bhousehold income\b|\bincome (?:level|bracket|distribution)s?\b|\bfpl\b|\bpoverty (?:level|line)\b|\bincome\b',
     ['household income / federal poverty level (not disclosed in received records)']),
    (r'\binterpreter\b|\bpreferred language\b|\blanguage preference\b|\blimited english\b|\blep\b',
     ['preferred language / interpreter need (not received)']),
    (r'\bsexual orientation\b|\bgender identity\b|\bsogi\b|\blgbtq?\+?\b', ['sexual orientation / gender identity (not received)']),
    (r'\bw?rvus?\b|\brelative value units?\b|\bcpt\b', ['CPT procedure codes and RVU weights (claims carry local codes only)']),
    (r'\bswallow(?:ed)?\b|\bactually (?:took|taken|swallowed)\b|\bverified independently\b',
     ['direct observation of medication ingestion (no received source records it)']),
]
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
denominator denominators numerator numerators'''.split())
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


def compile(question, profile, as_of):
    """Return (graph, info). graph is None when the question needs the fallback planner."""
    q = _norm(question, as_of)
    low = q.lower()
    for pattern, missing in UNSUPPORTED:  # checked on the user's own words, before any paraphrase
        if re.search(pattern, low) and not (_contract_terms(low) and 'shared' in pattern):
            return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': [f'Missing input: {m}' for m in missing]}, \
                   {'compiler': VERSION, 'recognised': [], 'route': 'local_unsupported', 'unexplained': []}
    q, paraphrased = _paraphrase(q, as_of)
    named = re.search(r'(?:^|[.;?] |, )(?:give(?: me)?|return|show|columns?(?: named)?|with columns?|output)\s*:?\s*([a-z][a-z_]*)\s*(?:,|and|&)\s*([a-z]+_[a-z_]+)\.?\s*$', q)
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
    for pattern, missing in UNSUPPORTED:
        if terms and 'shared' in pattern: continue  # settlement is computable once terms are supplied
        if re.search(pattern, r.low):
            info.update(route='local_unsupported', unexplained=[])
            return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': [f'Missing input: {m}' for m in missing]}, info
    info['clarifications'] = []
    for pattern, effect in CLARIFICATIONS:
        for m in r.take(r'(?:^|(?<=[.;?] )|(?<=; )|(?<=, )|(?<=and ))' + pattern, None):
            info['clarifications'].append(m.group(0))
            if effect: info.setdefault('output', effect)
    r.take(r'\b(?:on file|on record|in (?:the |our )?(?:record|chart|warehouse)s?|on the roster|in the data)\b', None)
    pid = r.take(r'\bp-[a-z0-9]+\b', 'patient id')
    if pid:  # single-patient questions with a measurement statistic
        return _patient_question(q, r, profile, [m.group(0).upper() for m in pid], info)
    special = _record_constructs(q, r, profile, as_of, info)
    if special is not None:
        return special
    vbc_plan = _vbc_question(q, r, profile, as_of, info, terms)
    if vbc_plan is not None:
        return vbc_plan
    literal = _literal_stream_question(q, r, profile, as_of, info)
    if literal is not None:
        return literal
    return _population_question(q, r, profile, as_of, info)


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
    latest = r.take(r'\b(?:latest|most recent|last)\b', 'latest value')
    adjust = r.take(r'\b(?:risk[- ]adjusted|case[- ]mix(?: adjusted)?|adjusted(?: for case mix)?|fair(?:ly)? compar\w+)\b', 'case-mix adjustment')
    readmit = r.take(r'\b(?:30[- ]day )?readmi(?:ssions?|tted|t)\b(?: within 30 days)?', 'readmission')
    r.take(r'\bwithin 30 days\b')
    highest = r.take(r'\b(?:highest|lowest|top|worst|best|most|fewest)\b', 'ranking')
    period = _take_period(r, as_of)
    group = None
    for pattern, g in GROUPS:
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
        allp.append({'kind': 'patient', 'field': 'age_reported', 'op': 'ge', 'value': int(m.group(1))})
        allp.append({'kind': 'patient', 'field': 'age_reported', 'op': 'le', 'value': int(m.group(2))})
        r.take(r'\breproductive age\b|\bof reproductive age\b', None, overlap=True)
    if r.take(r'\b(?:kids|children|child|pediatric|paediatric|minors)\b', 'children') and not any(a.get('field') == 'age_reported' for a in allp):
        allp.append({'kind': 'patient', 'field': 'age_reported', 'op': 'lt', 'value': 18})
    if r.take(r'\b(?:seniors|elderly|older adults|geriatric)\b', 'seniors') and not any(a.get('field') == 'age_reported' for a in allp):
        allp.append({'kind': 'patient', 'field': 'age_reported', 'op': 'ge', 'value': 65})
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
            allp.append({'kind': 'patient', 'field': 'age_reported', 'op': op, 'value': n})
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
    for phrase, (stream, field) in SOCIAL_FLAGS.items():
        for m in r.take(_alias_pattern(phrase), f'{field}'):
            tail = low[m.end():m.end() + 25]
            value = not re.match(r'\s*(?:as |is |reported )?false', tail)
            r.take(r'\b(?:as )?(?:true|false)\b')
            atom = {'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': value}], 'window': W}
            (excl if _negated(low, m.start()) else allp).append(atom)
    for pattern, (stream, field) in SOCIAL_PHRASES:
        for m in r.take(pattern, field):
            atom = {'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': True}], 'window': W}
            (excl if _negated(low, m.start()) else allp).append(atom)
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
    # measurements with comparisons
    for concept, aliases in MEASUREMENT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
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
    for concept, cmp, start in measured:
        if cmp:
            atom = {'kind': 'measurement', 'concept': concept, 'op': cmp[0], 'value': cmp[1],
                    'aggregate': 'latest' if latest else 'any', 'window': W}
        else:
            atom = {'kind': 'concept', 'concept': concept, 'window': W}
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
    if len(or_chains) > 1:
        info.update(route='fallback', reason='more than one either/or group of conditions'); return None, info
    in_or = {c[1] for ch in or_chains for c in ch}
    for concept, start, _end in conds:
        atom = {'kind': 'concept', 'concept': concept, 'window': W}
        system = re.search(r'\b(snomed|icd-?10)\b(?:\s+[a-z-]+){0,2}\s*$', low[max(0, start - 30):start])
        if system:
            atom['system'] = 'snomed' if system.group(1) == 'snomed' else 'icd10'
            r.take(r'\b' + re.escape(system.group(1)) + r'\b', 'coding system', overlap=True)
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
    r.take(r'\b(?:no|not|without|but no|lack|lacking|excluding|exclude|except|but|have|has|had|with|and|or)\b')
    info['unexplained'] = r.blocking(profile)
    info['ignored_words'] = getattr(r, 'ignored', [])
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

    if period:  # periods bound events, measurements and records; chronic conditions keep full history
        timed = {'basis': 'source_event', 'start': period[0], 'end': period[1]}
        for atom in allp + excl:
            if atom['kind'] == 'record' and atom.get('stream') == 'coverage': continue  # payer membership describes who, not when
            if atom['kind'] in ('measurement', 'record') or (atom['kind'] == 'concept' and atom['concept'] in concepts.ENCOUNTERS):
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
            subject = allp[-1] if allp else None
            if visit_atoms and visit_atoms[-1] in allp:  # "share of X who had a visit": the visit is the subject
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
            m = re.search(_alias_pattern(f) + r'(?:\s+(?:records?|rows?|notifications?|entries|entry))?', low)
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
    # field names mentioned (exact or natural)
    for f, fs in sorted(forms.items(), key=lambda kv: -max(map(len, kv[1]))):
        for form in fs:
            for m in r.take(_alias_pattern(form), f'field {f}'): named_fields.append(f)
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
    terms = {k: v for k, v in terms.items() if v is not None}
    if re.search(r'two[- ]sided|downside', low): terms['two_sided'] = True
    if re.search(r'risk[- ]adjust', low): terms['risk_adjust'] = True
    return terms if {'benchmark_pmpm', 'sharing_rate'} <= set(terms) else {}


VBC_PATTERNS = [
    ('shared_savings', r'\bshared[- ]savings\b|\bsettlement\b'),
    ('cost', r'\bpmpm\b|\bper member per month\b|\bcost per member\b|\btotal cost of care\b|\b(?:medical |claims )?spend(?:ing)?\b|\ballowed (?:amount|cost)s?\b'),
    ('eligibility', r'\bmember[- ]months?\b|\benrolled members\b|\benrollment\b'),
    ('attribution', r'\battribut(?:ed|ion)\b'),
    ('risk', r'\brisk scores?\b|\brisk[- ]adjustment scores?\b|\bacuity\b'),
    ('quality', r'\bquality measures?\b|\ba1c (?:poor )?control\b|\bpoorly controlled diabetes\b|\b(?:bp|blood pressure) control\b|\bstatin (?:therapy|use)\b'),
    ('utilization', r'\bper 1,?000\b|\bper thousand\b|\butilization panel\b|\b7[- ]day follow[- ]?up\b|\bfollow[- ]?up within 7 days\b'),
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
    if not verb or not measure: return None
    r.take(r'\b(?:why|what explains|what drove|explain)\b', 'why'); r.take(dict(CHANGE_MEASURES)[measure], f'measure {measure}')
    r.take(r'\b(?:did|does|has|have|our|the|go(?:ne)? up|went up|go(?:ne)? down|went down|increase[ds]?|rise|rose|risen|grow|grew|change[ds]?|'
           r'drop(?:ped)?|fall|fell|decrease[ds]?|decline[ds]?|jump(?:ed)?|spike[ds]?|so much|this year|last year|year over year)\b', None, overlap=True)
    params = {'metric': 'change', 'measure': measure, 'group_by': 'none'}
    years = re.search(r'\b(?:from|between) (\d{4}) (?:to|and) (\d{4})\b', low)
    if years:
        r.take(re.escape(years.group(0)), 'compare years')
        ya, yb = years.group(1), years.group(2)
        params['compare'] = {'a': {'start': f'{ya}-01-01', 'end': f'{ya}-12-31'}, 'b': {'start': f'{yb}-01-01', 'end': min(f'{yb}-12-31', as_of)}}
    for pattern, g in GROUPS:
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
    if metric is None or (metric == 'shared_savings' and not terms): return None
    for m, pattern in VBC_PATTERNS:
        if m == metric: r.take(pattern, f'vbc {metric}')
    params = {'metric': metric, 'group_by': 'none'}
    if metric == 'utilization' and r.take(r'\b(?:use |using |with )?(?:the )?(?:whole |entire |full )?roster (?:as (?:the )?denominator|headcount)\b|'
                                          r'\bper 1,?000 (?:roster )?(?:patients|people) on the roster\b|\bheadcount denominator\b', 'denominator roster'):
        params['denominator'] = 'roster'
    for pattern, g in GROUPS:
        if g in ('facility',): continue
        if r.take(pattern, f'group by {g}'): params['group_by'] = g; break
    year = next(iter(r.take(r'\b(?:in|during|for) (\d{4})\b', 'year')), None)
    if year: params['period'] = {'start': f'{year.group(1)}-01-01', 'end': min(f'{year.group(1)}-12-31', as_of)}
    r.take(r'\b(?:over |in )?the (?:last|past) (?:12 months|year)\b', 'last 12 months')
    if metric == 'quality':
        for measure, pattern in QUALITY_MEASURES:
            if re.search(pattern, low): params['measure'] = measure; r.take(pattern, f'measure {measure}', overlap=True); break
    if metric == 'shared_savings':
        params['terms'] = terms
        r.take(r'benchmark (?:of |is |at )?\$?[\d,]+(?:\.\d+)? ?(?:pmpm|per member per month)|(?:sharing|share|loss(?: sharing)?) rate (?:of |is )?[\d.]+ ?%|'
               r'(?:msr|mlr|minimum (?:savings|loss) rate) (?:of |is )?[\d.]+ ?%|quality score (?:of |is )?[\d.]+ ?%|(?:savings )?cap (?:of |is )?[\d.]+ ?%|'
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
